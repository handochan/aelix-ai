"""Delegation progress — the event bus and the guarded statusline row (§(k)).

One object, :class:`SubagentProgressBridge`, sits on
:attr:`~aelix_agents.runtime.SubagentHost.on_progress` and fans every snapshot
the child stream produces out to two places:

* ``api.events`` — the three contract channels ``subagent_start`` /
  ``subagent_tool`` / ``subagent_end``, payload
  :class:`~aelix_coding_agent.subagent_contract.SubagentProgress`. This is the
  channel a P4 dashboard, a Web UI or a third-party extension subscribes to.
* the TUI statusline — ONE height-1 row per live child
  (``tui/chrome.py:1036-1047`` renders ``set_status`` into a single line;
  multi-row panels are ``set_widget``, which is P4).

THREE MEASURED CAVEATS, all of which shape this file.

1. ``EventBus.emit`` (``extensions/api.py:280-286``) calls handlers
   SYNCHRONOUSLY, DISCARDS the return value — so an ``async def`` subscriber's
   body never runs — and swallows every handler exception under
   ``contextlib.suppress(Exception)`` with NO logging. A broken subscriber
   therefore produces zero rows and zero diagnostics, which is why
   ``test_broken_subscriber_does_not_break_spawn`` exists: the failure is
   invisible by design, so the guarantee has to be pinned by a test rather than
   observed in the field.

2. ``runtime.ui`` IS TIME-VARYING (finding OC-7) and must be read LIVE on every
   call. ``tui/shell.py`` re-points the binding on every harness rebuild
   (``/new`` / ``/fork`` / ``/resume``) and reverts it to
   :data:`HEADLESS_UI_CONTEXT` on TUI exit. A bridge that captured ``ctx.ui``
   once would write into a dead UI after the first ``/new``.

3. In print / json / rpc mode ``bind_ui`` is never called at all and EVERY
   ``ui.*`` method raises :exc:`NotImplementedError` (``headless_ui.py``). The
   ``is not HEADLESS_UI_CONTEXT`` guard is what keeps a delegation from dying of
   a statusline update — and the ``suppress`` beneath it is what keeps it alive
   if the binding flips between the guard and the call.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.extensions.headless_ui import HEADLESS_UI_CONTEXT
from aelix_coding_agent.subagent_contract import (
    SUBAGENT_END,
    SUBAGENT_START,
    SUBAGENT_TOOL,
)

if TYPE_CHECKING:
    from aelix_coding_agent.subagent_contract import SubagentProgress

STATUS_KEY_PREFIX = "subagent:"
"""One key per child, so two concurrent delegations own two rows rather than
overwriting each other. ``stop`` / teardown clear by the same key."""

_TERMINAL_STATES = frozenset({"done", "error", "stopped"})
"""``SubagentState`` values after which no further snapshot arrives. The
registry row is dropped by ``runtime._run``'s ``finally`` immediately after the
final publish, so this is the last chance to clear the statusline."""


def status_key(spawn_id: str) -> str:
    return f"{STATUS_KEY_PREFIX}{spawn_id}"


def _format_tokens(tokens: int) -> str:
    if tokens < 1000:
        return f"{tokens} tok"
    return f"{tokens / 1000:.1f}k tok"


def format_status_row(progress: SubagentProgress) -> str:
    """The one-line statusline text for a live child.

    Deliberately terse. ``set_status`` renders into a single height-1 row shared
    with every other status segment, so anything that wraps is anything that
    disappears.
    """

    parts = [f"agent {progress.profile}"]
    if progress.current_tool:
        parts.append(progress.current_tool)
    elif progress.state == "starting":
        parts.append("starting")
    parts.append(f"{progress.elapsed_ms / 1000:.0f}s")
    if progress.tokens:
        parts.append(_format_tokens(progress.tokens))
    if progress.cost:
        parts.append(f"${progress.cost:.4f}")
    return " · ".join(parts)


def channels_for(
    progress: SubagentProgress, *, first: bool, previous_tool: str | None
) -> tuple[str, ...]:
    """Which contract channels this snapshot belongs on — possibly none.

    The runtime publishes a snapshot after EVERY reduced stdout line, which for
    a chatty child is hundreds per turn. Only three kinds of them are events:

    * the first snapshot for an id → ``subagent_start``;
    * a terminal state → ``subagent_end``;
    * a NEW ``current_tool`` → ``subagent_tool``.

    Everything else is a statusline refresh and nothing more. ``current_tool``
    is cleared to ``None`` by ``turn_start`` and ``agent_end`` (``stream.py``),
    so two consecutive uses of the SAME tool are two distinct ``None → name``
    transitions and each is reported.

    A tuple rather than one channel because a spawn can be born terminal — a
    ``stop`` that lands between the registry row being published and the
    process existing (``print_channel.abort_child``) makes the first snapshot a
    ``stopped`` one, and a subscriber that saw an end without a start would have
    to guess.
    """

    if first:
        return (
            (SUBAGENT_START, SUBAGENT_END)
            if progress.state in _TERMINAL_STATES
            else (SUBAGENT_START,)
        )
    if progress.state in _TERMINAL_STATES:
        return (SUBAGENT_END,)
    if progress.current_tool and progress.current_tool != previous_tool:
        return (SUBAGENT_TOOL,)
    return ()


class SubagentProgressBridge:
    """The ``host.on_progress`` tap. Constructed once per extension instance.

    :param api: the :class:`~aelix_coding_agent.extensions.api.ExtensionAPI`
        handed to the extension factory. Only ``api.events`` and ``api.runtime``
        are used, and ``api.runtime.ui`` is re-read on every call (caveat 2).
    """

    __slots__ = ("_api", "_rows", "_tools")

    def __init__(self, api: Any) -> None:
        self._api = api
        self._rows: dict[str, str] = {}
        """id → the text currently on screen, so an unchanged row is not
        re-written hundreds of times per turn."""
        self._tools: dict[str, str | None] = {}
        """id → last ``current_tool``, the ``subagent_tool`` edge detector."""

    def __call__(self, progress: SubagentProgress) -> None:
        """Publish one snapshot. NEVER raises — a delegation must not fail here.

        The caller (``runtime._publish``) already wraps this in
        ``contextlib.suppress(Exception)``; the containment is repeated here so
        that a failure in the bus half cannot skip the statusline half, and vice
        versa.
        """

        first = progress.id not in self._tools
        previous_tool = self._tools.get(progress.id)
        self._tools[progress.id] = progress.current_tool

        for channel in channels_for(
            progress, first=first, previous_tool=previous_tool
        ):
            with contextlib.suppress(Exception):
                self._api.events.emit(channel, progress)

        if progress.state in _TERMINAL_STATES:
            self._clear_row(progress.id)
            self._tools.pop(progress.id, None)
            return
        self._set_row(progress.id, format_status_row(progress))

    def clear(self) -> None:
        """Drop every row we own — the ``session_shutdown`` / teardown half.

        A statusline segment outliving the delegation that owns it is a lie the
        user cannot dismiss, and ``set_status`` has no "clear all" verb.
        """

        for spawn_id in list(self._rows):
            self._clear_row(spawn_id)
        self._tools.clear()

    # ── the UI half ───────────────────────────────────────────────

    def _ui(self) -> Any | None:
        """The LIVE UI binding, or ``None`` when there is nothing to write to.

        Read through ``api.runtime`` rather than a captured ``ctx.ui`` for the
        reason in caveat 2, and compared by IDENTITY against the headless
        singleton — the same test ``ExtensionContext.has_ui`` itself performs
        (``extensions/api.py:1082-1083``).
        """

        try:
            ui = self._api.runtime.ui
        except Exception:  # noqa: BLE001 — a stale runtime is not our problem
            return None
        return None if ui is HEADLESS_UI_CONTEXT else ui

    def _set_row(self, spawn_id: str, text: str) -> None:
        if self._rows.get(spawn_id) == text:
            return
        ui = self._ui()
        if ui is None:
            return
        with contextlib.suppress(Exception):
            ui.set_status(status_key(spawn_id), text)
            self._rows[spawn_id] = text

    def _clear_row(self, spawn_id: str) -> None:
        self._rows.pop(spawn_id, None)
        ui = self._ui()
        if ui is None:
            return
        with contextlib.suppress(Exception):
            ui.set_status(status_key(spawn_id), None)


__all__ = [
    "STATUS_KEY_PREFIX",
    "SubagentProgressBridge",
    "channels_for",
    "format_status_row",
    "status_key",
]
