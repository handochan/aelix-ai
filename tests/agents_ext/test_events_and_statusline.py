"""Progress fan-out: ``api.events`` + the guarded statusline row — ADR-0197 §(k).

Two consumers, one tap. The event bus is what a P4 dashboard, a Web UI or a
third-party extension subscribes to; the statusline is the one height-1 row the
user actually sees (``tui/chrome.py:1097-1108`` — multi-row panels are
``set_widget``, which is P4).

THREE MEASURED HAZARDS ARE PINNED HERE, and each is invisible in production:

* ``EventBus.emit`` (``extensions/api.py:321-327``) swallows every handler
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
    disclosure_status_key,
    format_status_row,
    status_key,
)
from aelix_agents.stream import _StreamState
from aelix_coding_agent.builtin.permission_mode import PermissionMode
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
    (``extensions/api.py:321-327``), so a broken handler is silent. The
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
    """The two ``ExtensionUIContext`` methods the bridge can reach.

    ``set_widget`` is here rather than in the P2 shape because ADR-0199's S10
    surface 3 writes a batch panel through the same guard; the batch tests in
    ``test_batch_surfaces.py`` import this class, so a UI fake missing the method
    would make a widget write indistinguishable from a suppressed
    ``AttributeError``.
    """

    def __init__(self) -> None:
        self.writes: list[tuple[str, str | None]] = []
        self.widgets: list[tuple[str, list[str] | None]] = []

    def set_status(self, key: str, text: str | None) -> None:
        self.writes.append((key, text))

    def set_widget(
        self, key: str, content: list[str] | None, options: object = None
    ) -> None:
        self.widgets.append((key, content))


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
        _progress(
            model="claude-opus-4-8",
            current_tool="edit",
            elapsed_ms=12_300,
            tokens=1500,
            cost=0.0031,
        )
    )
    assert "\n" not in text
    assert "scout" in text
    # The child's run model, right after ``agent {profile}`` and before the tool.
    assert "claude-opus-4-8" in text
    assert text.startswith("agent scout · claude-opus-4-8 · edit")
    assert "edit" in text
    assert "1.5k tok" in text


def test_a_model_less_child_row_has_no_stray_model_separator() -> None:
    """``model`` is ``None`` until the child's first ``message_end`` resolves the
    run model (a profile that declared no ``model:`` may never resolve one). The
    term is OMITTED then — never rendered as ``· None ·`` and never a dangling
    separator — so the row stays byte-identical to what P2 shipped."""

    text = format_status_row(_progress(current_tool="edit", elapsed_ms=1_000))
    assert text == "agent scout · edit · 1s"
    assert "None" not in text


def test_a_hostile_model_string_cannot_break_the_status_row() -> None:
    """``model`` is child-authored (read off the child's own ``message_end``), so
    a newline or an ESC in it must not add a chrome row or smuggle an SGR onto the
    shared statusline. Same flatten the panel gives every child string."""

    hostile = "gpt\n\x1b[31m FAKE\n" + "x" * 5000
    text = format_status_row(_progress(model=hostile, elapsed_ms=1_000))
    assert "\n" not in text
    assert "\x1b" not in text
    assert "\x9b" not in text  # C1: the one-byte CSI
    assert len(text) <= len("agent scout · ") + 40 + len(" · 1s")


# === the model on the LIVE row, BEFORE the child has reported one =============
#
# THE MEASURED GAP (live QA, main @ 9ed0dde). ``format_status_row`` renders the
# model when ``SubagentProgress.model`` is set and omits it when it is not — both
# correct — but that field was fed ONLY from ``_StreamState.model``, which
# ``stream.py:561-563`` assigns from the child's first ``message_end``. A short
# delegation returns before one arrives, so the field was ``None`` for the whole
# VISIBLE LIFE of the row and the live statusline read ``agent explorer · 1s``
# with no model at all — the feature shipped and was invisible in practice.
#
# The parent already knows what it asked for: the same value it puts on the
# child's argv (``resolver.child_model_id``). Seeding the row with it makes the
# term present from the first snapshot, and the child's own report still wins the
# moment it exists.


class _SilentChannel:
    """A child that publishes progress and finishes WITHOUT a ``message_end``.

    Exactly the QA-reproduced shape. It drives ``on_stream`` the way
    ``PrintChannel._pump_stdout`` does but never assigns ``state.model``, which
    is what the real reducer does for every snapshot before the child's first
    assistant message — and for EVERY snapshot of a child that dies, is aborted,
    or answers from a tool call alone.
    """

    def __init__(self) -> None:
        self.plans: list[Any] = []

    async def run(
        self, plan: Any, *, child: Any = None, on_stream: Any = None
    ) -> SubagentResult:
        self.plans.append(plan)
        state: _StreamState = child.stream if child is not None else _StreamState()
        if child is not None:
            child.state = "running"
        for tool in (None, "read"):
            state.current_tool = tool
            if on_stream is not None:
                on_stream(state)
        if child is not None:
            child.state = "done"
        return SubagentResult(
            id=plan.id, profile=plan.resolved.name, ok=True, status="ok", summary="done"
        )


class _ReportingChannel:
    """…and one whose ``message_end`` lands MID-RUN, so both halves are pinned.

    Its snapshots come in two shapes, in order: ones from before the child has
    said anything (the seed's whole window, including the publish ``_run`` makes
    before the channel is even called) and ones carrying the model the child
    really ran. That is the real cadence, and it is the only way to assert "the
    seed fills the gap AND the report wins" in one run rather than in two that
    cannot see each other.
    """

    def __init__(self, model: str) -> None:
        self.plans: list[Any] = []
        self._model = model

    async def run(
        self, plan: Any, *, child: Any = None, on_stream: Any = None
    ) -> SubagentResult:
        self.plans.append(plan)
        state: _StreamState = child.stream if child is not None else _StreamState()
        if child is not None:
            child.state = "running"
        state.current_tool = "read"
        if on_stream is not None:
            on_stream(state)
        state.model = self._model
        state.current_tool = None
        if on_stream is not None:
            on_stream(state)
        if child is not None:
            child.state = "done"
        return SubagentResult(
            id=plan.id, profile=plan.resolved.name, ok=True, status="ok", summary="done"
        )


class _ParentModel:
    """``ExtensionContext.model`` reads structurally through the host seam."""

    def __init__(self, id: str, provider: str = "anthropic") -> None:  # noqa: A002
        self.id = id
        self.provider = provider


def _seeded_runtime(
    tmp_path: Path, channel: Any, *, parent_model: Any = None
) -> tuple[Any, list[SubagentProgress]]:
    from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl

    seen: list[SubagentProgress] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), model=lambda: parent_model),
        channel=channel,
    )
    return runtime, seen


async def test_a_child_that_never_reports_still_names_the_model_on_the_live_row(
    tmp_path: Path,
) -> None:
    """The QA-reproduced gap. No ``message_end``, so no reported model — and the
    row must still say which model the parent asked for, because it DID ask.

    Asserted over EVERY snapshot, not just the last: the row is drawn from all of
    them and the complaint was about what is on screen while the child runs.
    """

    from tests.agents_ext.test_print_channel_spawn import _profile, _resolved

    channel = _SilentChannel()
    runtime, seen = _seeded_runtime(
        tmp_path, channel, parent_model=_ParentModel("claude-sonnet-4-5")
    )
    result = await runtime.spawn(
        _resolved(_profile(model=None, provider=None)), "go", on_event=seen.append
    )

    assert result.ok is True
    assert seen, "the runtime published no snapshot at all"
    assert {p.model for p in seen} == {"claude-sonnet-4-5"}
    assert format_status_row(seen[0]).startswith("agent scout · claude-sonnet-4-5")


async def test_a_profiles_own_model_is_the_one_the_live_row_names(
    tmp_path: Path,
) -> None:
    """A profile that declares ``model:`` is not inheriting anything, and the row
    must name what the CHILD will be launched with — never the parent's."""

    from tests.agents_ext.test_print_channel_spawn import _profile, _resolved

    runtime, seen = _seeded_runtime(
        tmp_path, _SilentChannel(), parent_model=_ParentModel("claude-sonnet-4-5")
    )
    await runtime.spawn(
        _resolved(_profile(model="claude-opus-4-8")), "go", on_event=seen.append
    )

    assert {p.model for p in seen} == {"claude-opus-4-8"}


async def test_the_reported_model_wins_over_the_one_we_asked_for(
    tmp_path: Path,
) -> None:
    """PRECEDENCE, and it only points one way. The seed is what we ASKED for; the
    child's ``message_end`` is what it ACTUALLY ran, and a silent substitution —
    a persisted default at a different price — is the exact thing the model term
    exists to make visible. So the reported value must win every time it exists.
    """

    from tests.agents_ext.test_print_channel_spawn import _profile, _resolved

    runtime, seen = _seeded_runtime(
        tmp_path,
        _ReportingChannel("deepseek/deepseek-v4-flash"),
        parent_model=_ParentModel("claude-sonnet-4-5"),
    )
    await runtime.spawn(
        _resolved(_profile(model=None, provider=None)), "go", on_event=seen.append
    )

    models = [p.model for p in seen]
    # Before the report: the seed, so the row is never blank. After it: the
    # child's own word, and it never reverts.
    assert models[0] == "claude-sonnet-4-5"
    assert models[-1] == "deepseek/deepseek-v4-flash"
    assert "deepseek/deepseek-v4-flash" in models
    assert None not in models
    assert models.index("deepseek/deepseek-v4-flash") == models.count(
        "claude-sonnet-4-5"
    )


async def test_no_model_anywhere_publishes_no_model(tmp_path: Path) -> None:
    """A parent with no resolved model and a profile that declares none leaves the
    child to its own cascade, whose outcome is not knowable here. ``None``, so
    every renderer omits the term — never a guess, never ``unknown``."""

    from tests.agents_ext.test_print_channel_spawn import _profile, _resolved

    runtime, seen = _seeded_runtime(tmp_path, _SilentChannel(), parent_model=None)
    await runtime.spawn(
        _resolved(_profile(model=None, provider=None)), "go", on_event=seen.append
    )

    assert {p.model for p in seen} == {None}
    assert "None" not in format_status_row(seen[0])


async def test_a_host_whose_model_getter_raises_still_spawns(tmp_path: Path) -> None:
    """The seed is a DISPLAY concern and is read off a live, host-supplied getter
    (``/model`` rebinds it mid-session). A host that hands us something unreadable
    must cost the child its model TERM, not its spawn — the same posture
    ``parent_model_flags`` takes one layer down."""

    from tests.agents_ext.test_print_channel_spawn import _profile, _resolved

    def _boom() -> Any:
        raise RuntimeError("stale runtime")

    from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl

    seen: list[SubagentProgress] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), model=_boom),
        channel=_SilentChannel(),
    )
    result = await runtime.spawn(
        _resolved(_profile(model=None, provider=None)), "go", on_event=seen.append
    )

    assert result.ok is True
    assert {p.model for p in seen} == {None}


async def _hook_once(bench: Any) -> None:
    """Fire one ``tool_call`` hook so the extension holds a live context."""

    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_agents.tool import AGENT_TOOL_NAME

    await bench.hook(
        ToolCallHookEvent(
            tool_call_id="prime",
            tool_name=AGENT_TOOL_NAME,
            args={"profile": "nope", "task": "t"},
        )
    )

# === #196: the disclosure row, driven end to end ==============================
#
# THESE EXIST BECAUSE A SABOTAGE ROUND FOUND THE GAP. Deleting the disclosure
# from `AgentsExtension._grant_for` — the MODEL door, the one where the model
# chose the profile, the tasks and the directory — left 1548 tests green. So did
# never emitting it at all. Both holes had the same shape: everything asserted
# the `SpawnGrant.disclosure` FIELD and nothing asserted that a user would ever
# see it.
#
# The first implementation was worse than untested, it was inert: it rode
# `ctx.on_partial`, and `tui/render.py` no-ops `tool_execution_update`. Measured
# with a positive control — the same `EventRenderer` commits a line for
# `tool_execution_start` and zero for `tool_execution_update`. So these tests
# drive a REAL delegation and read the statusline the product actually writes.


async def test_a_yolo_delegation_writes_a_disclosure_row_and_clears_it(
    tmp_path: Path,
) -> None:
    """The MODEL door, end to end, through the real extension.

    Two assertions and they fail differently: a missing key means the user was
    never told, and a key still holding text means a row outlived the child that
    owned it — which claims a yolo child is running when none is.
    """

    bench = _bench(
        tmp_path,
        posture=PermissionMode.YOLO,
        has_ui=True,
        channel=_EmittingChannel(),  # type: ignore[arg-type]
    )
    await _delegate(bench, tmp_path)

    disclosure = [k for k in bench.ui.status if "disclose:" in k]
    assert len(disclosure) == 1, (
        f"expected exactly one disclosure row, got {sorted(bench.ui.status)}"
    )
    assert bench.ui.status[disclosure[0]] is None, "the row outlived the child"

    # It was WRITTEN before it was cleared — a key that only ever held None
    # would satisfy the assertion above while telling the user nothing.
    assert any(
        text and "yolo" in text and "scout" in text
        for text in bench.ui.writes.get(disclosure[0], [])
    ), bench.ui.writes.get(disclosure[0])


async def test_a_default_posture_delegation_writes_no_disclosure_row(
    tmp_path: Path,
) -> None:
    """The control. Every other posture opens a dialog instead, and a row next
    to a dialog is two copies of one decision."""

    bench = _bench(
        tmp_path, has_ui=True, channel=_EmittingChannel()  # type: ignore[arg-type]
    )
    await _delegate(bench, tmp_path)

    assert [k for k in bench.ui.status if "disclose:" in k] == []


async def test_a_headless_yolo_delegation_writes_no_row(tmp_path: Path) -> None:
    """No UI, nobody to tell — and every ``ui.*`` raises there anyway."""

    bench = _bench(
        tmp_path,
        posture=PermissionMode.YOLO,
        channel=_EmittingChannel(),  # type: ignore[arg-type]
    )
    await _delegate(bench, tmp_path)

    assert [k for k in bench.ui.status if "disclose:" in k] == []


def test_the_disclosure_key_cannot_collide_with_a_childs_own_row() -> None:
    """Both are written into the same height-1 status line, and ``set_status``
    is keyed: two writers sharing a key would silently replace each other."""

    from aelix_agents.progress import status_key

    assert disclosure_status_key("abc") != status_key("abc")
    assert disclosure_status_key("abc").startswith(STATUS_KEY_PREFIX)


async def test_the_agents_run_door_also_writes_a_disclosure_row(
    tmp_path: Path,
) -> None:
    """THE OTHER DOOR, and a sabotage found it unguarded.

    ``/agents run`` lives in ``runtime`` and never sees ``AgentsExtension``, so
    it reaches the row through ``SubagentHost.on_disclosure``. Deleting that one
    wiring line left 1552 tests green — every one of them was watching the model
    door.

    A human typed this one, so they already know the profile and the task; what
    the removed modal uniquely told them is the POSTURE, which is what the row
    carries.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(
        tmp_path,
        posture=PermissionMode.YOLO,
        has_ui=True,
        channel=_EmittingChannel(),  # type: ignore[arg-type]
    )
    runtime = bench.ext.runtime
    assert runtime is not None
    # PRIME THE LIVE CONTEXT, exactly as a real session does. ``host.consent_context``
    # reads ``AgentsExtension._ctx``, which only a hook sets — without this the
    # door sees no UI, consents headlessly and correctly writes nothing, and this
    # test would pass for the wrong reason forever.
    await _hook_once(bench)
    resolved = runtime.resolve_profile("scout", allow_project=False)
    result = await runtime.spawn(resolved, "go")

    assert result.status != "declined"
    assert bench.ui.calls == [], "a YOLO parent was prompted on the typed door"

    disclosure = [k for k in bench.ui.status if "disclose:" in k]
    assert len(disclosure) == 1, (
        f"/agents run wrote no disclosure row: {sorted(bench.ui.status)}"
    )
    written = [t for t in bench.ui.writes[disclosure[0]] if t]
    assert written and "yolo" in written[0] and "scout" in written[0], written
    assert bench.ui.status[disclosure[0]] is None, "the row outlived the child"


async def test_the_grant_itself_carries_no_disclosure_headlessly(
    tmp_path: Path,
) -> None:
    """The INNER layer of the headless guard, broken on its own.

    ``test_a_headless_yolo_delegation_writes_no_row`` cannot see this: the
    bridge's own ``_ui()`` guard returns ``None`` headlessly, so deleting the
    ``has_ui`` term from ``_grant_for`` composes a misleading string onto the
    grant and the row still never appears. Green, and one layer thinner than it
    looks — measured, that sabotage left 365 tests passing.

    The grant is what a host reads, so an empty string there is the claim.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(
        tmp_path,
        posture=PermissionMode.YOLO,
        channel=_EmittingChannel(),  # type: ignore[arg-type]
    )
    grant = await bench.ext._grant_for(  # noqa: SLF001 — the seam under test
        bench.ctx,
        bench.ext.runtime.resolve_profile("scout", allow_project=False),  # type: ignore[union-attr]
        ("go",),
        cwd=str(tmp_path / "project"),
        mode="single",
    )
    assert grant.consented is True
    assert grant.disclosure == "", (
        "a headless grant carries a disclosure string nothing can render"
    )
