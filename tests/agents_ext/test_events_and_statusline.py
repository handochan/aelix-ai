"""Progress fan-out: ``api.events`` + the guarded statusline row — ADR-0197 §(k).

Two consumers, one tap. The event bus is what a P4 dashboard, a Web UI or a
third-party extension subscribes to; the statusline is the one height-1 row the
user actually sees (``tui/chrome.py:1036-1047`` — multi-row panels are
``set_widget``, which is P4).

THREE MEASURED HAZARDS ARE PINNED HERE, and each is invisible in production:

* ``EventBus.emit`` (``extensions/api.py:280-286``) swallows every handler
  exception under ``contextlib.suppress(Exception)`` with NO logging, and
  DISCARDS handler return values — so an ``async def`` subscriber's body never
  runs. A broken subscriber produces zero rows and zero diagnostics, which is
  exactly why :func:`test_broken_subscriber_does_not_break_spawn` has to exist.
* ``runtime.ui`` is TIME-VARYING (finding OC-7): re-pointed on every harness
  rebuild and reverted to :data:`HEADLESS_UI_CONTEXT` on TUI exit. A captured
  binding writes into a dead UI after the first ``/new``.
* in print / json / rpc mode ``bind_ui`` is never called and EVERY ``ui.*``
  method raises :exc:`NotImplementedError`. A statusline update must never be
  able to fail a delegation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aelix_agents.progress import (
    STATUS_KEY_PREFIX,
    SubagentProgressBridge,
    format_status_row,
    status_key,
)
from aelix_agents.stream import _StreamState
from aelix_coding_agent.subagent_contract import (
    SUBAGENT_END,
    SUBAGENT_START,
    SUBAGENT_TOOL,
    SubagentProgress,
    SubagentResult,
)

from tests.agents_ext.test_tool_and_security import (
    _Bench,
    _bench,
    _call,
    _write_profile,
)

_CHANNELS = (SUBAGENT_START, SUBAGENT_TOOL, SUBAGENT_END)


class _EmittingChannel:
    """A channel that streams one tool execution before finishing.

    It drives ``on_stream`` the way ``PrintChannel._pump_stdout`` does — one
    call per reduced line, with the mutable :class:`_StreamState` the reducer
    folds into — so the bridge sees the real publish cadence rather than a
    hand-built sequence of events.
    """

    def __init__(self, tools: tuple[str, ...] = ("read", "read", "grep")) -> None:
        self.plans: list[Any] = []
        self._tools = tools

    async def run(
        self, plan: Any, *, child: Any = None, on_stream: Any = None
    ) -> SubagentResult:
        self.plans.append(plan)
        state: _StreamState = child.stream if child is not None else _StreamState()
        if child is not None:
            child.state = "running"
        for index, tool in enumerate(self._tools):
            # ``turn_start`` / ``agent_end`` clear ``current_tool`` in the real
            # reducer, so consecutive uses of the same tool arrive as two
            # distinct ``None -> name`` transitions.
            state.current_tool = None
            if on_stream is not None:
                on_stream(state)
            state.current_tool = tool
            state.tokens = 100 * (index + 1)
            if on_stream is not None:
                on_stream(state)
        state.current_tool = None
        if child is not None:
            child.state = "done"
        return SubagentResult(
            id=plan.id,
            profile=plan.resolved.name,
            ok=True,
            status="ok",
            summary="done",
            permission_mode=plan.permission_mode.value,
        )


def _subscribe(bench: _Bench) -> list[tuple[str, SubagentProgress]]:
    seen: list[tuple[str, SubagentProgress]] = []
    for channel in _CHANNELS:
        bench.api.events.on(
            channel, lambda payload, c=channel: seen.append((c, payload))
        )
    return seen


async def _delegate(bench: _Bench, tmp_path: Path) -> None:
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is False


# === the event bus ============================================================


async def test_progress_events_emitted_in_order(tmp_path: Path) -> None:
    """START first, END last, one TOOL per tool execution — and nothing else.

    The runtime publishes a snapshot after EVERY reduced stdout line, which for
    a chatty child is hundreds per turn. Only the edges are events; the rest is
    a statusline refresh.
    """

    bench = _bench(tmp_path, channel=_EmittingChannel())  # type: ignore[arg-type]
    seen = _subscribe(bench)
    await _delegate(bench, tmp_path)

    channels = [channel for channel, _ in seen]
    assert channels[0] == SUBAGENT_START
    assert channels[-1] == SUBAGENT_END
    assert channels.count(SUBAGENT_START) == 1
    assert channels.count(SUBAGENT_END) == 1
    assert channels.count(SUBAGENT_TOOL) == 3
    assert [p.current_tool for c, p in seen if c == SUBAGENT_TOOL] == [
        "read",
        "read",
        "grep",
    ]


async def test_payload_is_the_contract_dataclass(tmp_path: Path) -> None:
    """Subscribers bind against ``subagent_contract``, never against our types.

    The payload is the product-core dataclass precisely so a consumer can live
    in another package (or another repo) without importing ``aelix_agents``.
    """

    bench = _bench(tmp_path, channel=_EmittingChannel())  # type: ignore[arg-type]
    seen = _subscribe(bench)
    await _delegate(bench, tmp_path)

    for _, payload in seen:
        assert isinstance(payload, SubagentProgress)
        assert payload.id.startswith("sub-")
        assert payload.profile == "scout"
    assert seen[0][1].state == "starting"
    assert seen[-1][1].state == "done"


async def test_broken_subscriber_does_not_break_spawn(tmp_path: Path) -> None:
    """The failure this documents is INVISIBLE, which is why it needs a test.

    ``EventBus.emit`` swallows subscriber exceptions with no logging
    (``extensions/api.py:277-278``), so a broken handler is silent. The
    guarantee that matters is the other direction: it must not be able to take
    a delegation down with it, and the healthy subscribers must still be served.
    """

    bench = _bench(tmp_path, channel=_EmittingChannel())  # type: ignore[arg-type]

    def _boom(payload: object) -> None:
        raise RuntimeError("subscriber is broken")

    bench.api.events.on(SUBAGENT_START, _boom)
    bench.api.events.on(SUBAGENT_END, _boom)
    seen = _subscribe(bench)

    await _delegate(bench, tmp_path)
    assert [c for c, _ in seen][0] == SUBAGENT_START
    assert [c for c, _ in seen][-1] == SUBAGENT_END


# === the statusline ===========================================================


async def test_headless_never_calls_set_status(tmp_path: Path) -> None:
    """In print / json / rpc mode every ``ui.*`` call raises.

    ``bind_ui`` is never called there, so the guard is
    ``runtime.ui is not HEADLESS_UI_CONTEXT`` — the same identity test
    ``ExtensionContext.has_ui`` itself performs.
    """

    bench = _bench(tmp_path, channel=_EmittingChannel())  # type: ignore[arg-type]
    await _delegate(bench, tmp_path)
    assert bench.ui.status == {}


async def test_statusline_row_set_and_cleared(tmp_path: Path) -> None:
    """One key per child, and it is CLEARED when the child finishes.

    A segment outliving the delegation that owns it is a lie the user cannot
    dismiss, and ``set_status`` has no "clear all" verb.
    """

    bench = _bench(
        tmp_path, has_ui=True, channel=_EmittingChannel()  # type: ignore[arg-type]
    )
    await _delegate(bench, tmp_path)

    keys = list(bench.ui.status)
    assert len(keys) == 1
    assert keys[0].startswith(STATUS_KEY_PREFIX)
    assert bench.ui.status[keys[0]] is None


async def test_the_ui_binding_is_read_live_not_cached(tmp_path: Path) -> None:
    """FINDING OC-7 on the statusline half.

    The extension is constructed while the UI is still headless — which is the
    real order of events, because ``harness.bootstrap()`` runs before
    ``tui/shell.py`` binds anything. A bridge that resolved ``ui`` once at
    construction would write into the headless singleton forever.
    """

    bench = _bench(tmp_path, channel=_EmittingChannel())  # type: ignore[arg-type]
    assert bench.ui.status == {}

    bench.runtime.bind_ui(bench.ui)  # type: ignore[arg-type]
    await _delegate(bench, tmp_path)
    assert bench.ui.status


async def test_a_raising_statusline_does_not_fail_the_delegation(
    tmp_path: Path,
) -> None:
    """The binding can flip between the guard and the call. It must not matter."""

    bench = _bench(
        tmp_path, has_ui=True, channel=_EmittingChannel()  # type: ignore[arg-type]
    )

    def _boom(key: str, text: str | None) -> None:
        raise NotImplementedError("ExtensionUIContext.set_status is not bound")

    bench.ui.set_status = _boom  # type: ignore[method-assign, assignment]
    await _delegate(bench, tmp_path)


# === the bridge in isolation ==================================================


class _Bus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, SubagentProgress]] = []

    def emit(self, channel: str, data: SubagentProgress) -> None:
        self.emitted.append((channel, data))


class _Runtime:
    def __init__(self, ui: Any) -> None:
        self.ui = ui


class _Api:
    def __init__(self, ui: Any) -> None:
        self.events = _Bus()
        self.runtime = _Runtime(ui)


class _Ui:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str | None]] = []

    def set_status(self, key: str, text: str | None) -> None:
        self.writes.append((key, text))


def _progress(**kwargs: Any) -> SubagentProgress:
    base: dict[str, Any] = {"id": "sub-1", "profile": "scout", "state": "running"}
    base.update(kwargs)
    return SubagentProgress(**base)


def test_an_unchanged_row_is_not_rewritten() -> None:
    """Hundreds of publishes per turn must not become hundreds of UI writes."""

    ui = _Ui()
    bridge = SubagentProgressBridge(_Api(ui))
    for _ in range(5):
        bridge(_progress(current_tool="read", elapsed_ms=1000))
    # One write for the first (``starting``-edge) publish, one for the rest.
    assert len(ui.writes) <= 2


def test_a_child_born_terminal_still_reports_a_start() -> None:
    """A ``stop`` that lands before the process exists makes the FIRST snapshot
    terminal (``print_channel.abort_child``). A subscriber that saw an end
    without a start would have to guess."""

    ui = _Ui()
    api = _Api(ui)
    bridge = SubagentProgressBridge(api)
    bridge(_progress(state="stopped"))
    assert [c for c, _ in api.events.emitted] == [SUBAGENT_START, SUBAGENT_END]
    assert ui.writes == [(status_key("sub-1"), None)]


def test_clear_removes_every_row_it_owns() -> None:
    """The ``session_shutdown`` / teardown half — no orphaned segments."""

    ui = _Ui()
    bridge = SubagentProgressBridge(_Api(ui))
    bridge(_progress(id="sub-a", current_tool="read"))
    bridge(_progress(id="sub-b", current_tool="grep"))
    ui.writes.clear()

    bridge.clear()
    assert sorted(ui.writes) == [
        (status_key("sub-a"), None),
        (status_key("sub-b"), None),
    ]


def test_the_row_text_is_one_line_and_names_the_profile() -> None:
    """``set_status`` renders into a single height-1 row shared with every other
    segment, so anything that wraps is anything that disappears."""

    text = format_status_row(
        _progress(current_tool="edit", elapsed_ms=12_300, tokens=1500, cost=0.0031)
    )
    assert "\n" not in text
    assert "scout" in text
    assert "edit" in text
    assert "1.5k tok" in text
