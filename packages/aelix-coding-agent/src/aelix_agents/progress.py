"""Delegation progress — the event bus and the guarded statusline row (§(k)).

One object, :class:`SubagentProgressBridge`, sits on
:attr:`~aelix_agents.runtime.SubagentHost.on_progress` and fans every snapshot
the child stream produces out to two places:

* ``api.events`` — the three contract channels ``subagent_start`` /
  ``subagent_tool`` / ``subagent_end``, payload
  :class:`~aelix_coding_agent.subagent_contract.SubagentProgress`. This is the
  channel a P4 dashboard, a Web UI or a third-party extension subscribes to.
* the TUI statusline — ONE height-1 row per live child
  (``tui/chrome.py:1097-1108`` renders ``set_status`` into a single line), or,
  while a BATCH group is open, ONE aggregate row for the whole batch plus a
  ``set_widget`` panel (ADR-0199 decision S10; the layouts live in
  :mod:`aelix_agents.panel`).

THE GROUP IS OPENED WITH A COUNT, NEVER WITH IDS (ADR-0199 §3.6). ``spawn_id``
is minted inside ``runtime._run`` (``runtime.py:799``) — after ``spawn_granted``
has already been entered, and for members 5-8 of an 8-task batch not until
wave 2 — so nothing can hand this object a list of ids when the batch starts.
Membership arrives instead through :meth:`SubagentProgressBridge.adopt`, which
the executor's per-member ``on_event`` closure calls with the index it was
created with. That is exact rather than heuristic because ``runtime._publish``
fans each snapshot out as ``for tap in (on_event, self.host.on_progress)``
(``runtime.py:948-952``) with no ``await`` between them: the per-spawn callback
ALWAYS runs before this session-wide tap for the same snapshot, so there is no
window in which a member's id reaches :meth:`__call__` unadopted. An id that
never gets adopted is not an error — it correctly falls back to its own
per-child row, which is what a concurrent ``/agents run`` child is.

THE EVENT-BUS HALF IS UNCHANGED BY GROUPING. ``SubagentProgress``
(``subagent_contract.py:175-203``) gains no ``batch_id`` field in P3 (§3.6:
the product-core delta is zero), so a subscriber sees N interleaved starts with
no grouping. That residual is named in ADR-0199; it is not something this file
papers over with an ``aelix_agents``-private channel.

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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.extensions.headless_ui import HEADLESS_UI_CONTEXT
from aelix_coding_agent.subagent_contract import (
    SUBAGENT_END,
    SUBAGENT_START,
    SUBAGENT_TOOL,
)

from aelix_agents.panel import (
    PANEL_MIN_CHILDREN,
    PANEL_ROW_MAX_CHARS,
    PANEL_WIDGET_KEY,
    _flatten,
    format_aggregate_status,
    format_panel,
)

if TYPE_CHECKING:
    from aelix_coding_agent.subagent_contract import SubagentProgress

STATUS_KEY_PREFIX = "subagent:"
"""One key per child, so two concurrent delegations own two rows rather than
overwriting each other. ``stop`` / teardown clear by the same key."""

GROUP_KEY_INFIX = "group:"
"""The aggregate row's key is ``subagent:group:<call id>`` — under
:data:`STATUS_KEY_PREFIX` on purpose, so every key this bridge can ever write
is recognisable as ours by one prefix test, and so :meth:`clear` needs no second
bookkeeping path."""

_TERMINAL_STATES = frozenset({"done", "error", "stopped"})
"""``SubagentState`` values after which no further snapshot arrives. The
registry row is dropped by ``runtime._run``'s ``finally`` immediately after the
final publish, so this is the last chance to clear the statusline."""


def status_key(spawn_id: str) -> str:
    return f"{STATUS_KEY_PREFIX}{spawn_id}"


def group_status_key(group_key: str) -> str:
    """The aggregate row's key for one ``agent`` call's group.

    Namespaced by the caller's key (the parent tool call id) rather than a bare
    constant, so a host that somehow drives two calls at once writes two rows
    instead of two calls fighting over one.
    """

    return f"{STATUS_KEY_PREFIX}{GROUP_KEY_INFIX}{group_key}"


@dataclass(slots=True)
class _Group:
    """One open batch: its key, its member table and whether it renders.

    ``snapshots`` is indexed by SUBMITTED position and starts all-``None``; a
    ``None`` entry is a member still parked on the batch semaphore, which is what
    :data:`~aelix_agents.panel.format_aggregate_status` renders as ``queued``.

    ``active`` is ``False`` for a group of one. S10 is explicit that a single
    delegation keeps P2's behaviour EXACTLY — its own per-child row, no
    aggregate, no panel — and an inactive group still exists so that
    :meth:`SubagentProgressBridge.end_group` is symmetric for every call the
    extension opens, batch or not.

    ``tasks`` is index-aligned with ``snapshots`` and is what the panel shows a
    member DOING. It has to be carried here rather than read off a snapshot
    because it is the one distinguishing fact a snapshot does not have: one
    ``agent()`` call is one profile and one model across N tasks (S3), so every
    other per-member field is identical, and ADR-0199 §3.6 rules out adding a
    field to ``SubagentProgress``. The submitted INDEX is bound at member
    creation, which is exactly what makes an index-aligned tuple sufficient.
    """

    key: str
    snapshots: list[SubagentProgress | None] = field(default_factory=list)
    active: bool = False
    tasks: tuple[str, ...] = ()


_STATUS_MODEL_MAX_CHARS = 40
"""Bound on the model term in :func:`format_status_row`.

The statusline is one segment on a shared height-1 row and the model is
child-authored (read off the child's own ``message_end``), so the term is
sanitised and length-capped here rather than trusted to fit. ``chrome._render_status``
strips newlines from this row but bounds neither its width nor its ESC content
(``chrome.py:1097-1108``), so — unlike ``current_tool``, which P2 left to that far end —
this field is defended at the source, the posture the panel already takes."""


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
    if progress.model:
        parts.append(_flatten(progress.model, limit=_STATUS_MODEL_MAX_CHARS))
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

    __slots__ = (
        "_api",
        "_groups",
        "_members",
        "_rows",
        "_tools",
        "_widget_lines",
        "_widget_owner",
    )

    def __init__(self, api: Any) -> None:
        self._api = api
        self._rows: dict[str, str] = {}
        """statusline KEY → the text currently on screen, so an unchanged row is
        not re-written hundreds of times per turn. Keyed by the rendered key
        rather than by spawn id because there are now two kinds of row — a
        per-child one and a group aggregate — and one dedup table must cover
        both."""
        self._tools: dict[str, str | None] = {}
        """id → last ``current_tool``, the ``subagent_tool`` edge detector."""
        self._groups: dict[str, _Group] = {}
        """group key → its open :class:`_Group`."""
        self._members: dict[str, tuple[str, int]] = {}
        """spawn id → (group key, submitted index), filled by :meth:`adopt`."""
        self._widget_owner: str | None = None
        """Which group key last wrote :data:`~aelix_agents.panel.PANEL_WIDGET_KEY`.
        Tracked so ``end_group`` can only blank a panel it actually owns."""
        self._widget_lines: tuple[str, ...] | None = None
        """The panel currently on screen. ``chrome.set_widget`` calls
        ``invalidate()`` unconditionally (``chrome.py:1457-1463``), so an
        unchanged panel re-written on every reduced stdout line would be a
        full repaint per line."""

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

        member = self._member_of(progress.id)
        if member is not None:
            group, index = member
            # The GROUPED path. The member's snapshot is recorded even when it is
            # terminal — "3 done" is exactly what the aggregate has to be able to
            # say — and no per-child row is ever written, which is the whole
            # point of surface 1 (S10: four 45-char segments do not fit an
            # 80-column height-1 row).
            group.snapshots[index] = progress
            if progress.state in _TERMINAL_STATES:
                self._tools.pop(progress.id, None)
            self._render_group(group)
            return

        if progress.state in _TERMINAL_STATES:
            self._clear_row(status_key(progress.id))
            self._tools.pop(progress.id, None)
            return
        self._set_row(status_key(progress.id), format_status_row(progress))

    # ── the batch group (S10) ─────────────────────────────────────

    def begin_group(
        self, key: str, *, expected: int, tasks: Sequence[str] = ()
    ) -> None:
        """Open a group of ``expected`` members under ``key``.

        A COUNT, not ids — §3.6, restated in this module's docstring: the ids do
        not exist yet and for the members still queued behind the batch
        semaphore they will not exist for minutes. The count is what lets the
        aggregate say ``5 queued`` instead of pretending the batch is a
        three-member one that keeps growing.

        ``tasks`` is the submitted task text, index-aligned with the members, and
        it is the only per-member fact that differs across a batch — the profile
        and the model are one apiece by construction. Defaulted to empty so every
        existing caller and test is unaffected; the panel falls back to what it
        rendered before when a group has none.

        A LABEL, NOT A PROMPT, in ``chain`` mode: ``chain`` substitutes
        ``{previous}`` per step at RUN time (``chain.py``), so ``tasks[k]`` is
        the un-substituted template. That is the right thing to show — it is what
        the model wrote and what the index means — but it is not what the child
        received, and the panel must not imply otherwise.

        Re-opening a live key ends the old group first. That can only happen if
        a caller leaked one (the extension opens and closes in the same
        ``try``/``finally``), and leaving the stale row on screen would be the
        worse failure — it is a lie the user cannot dismiss.
        """

        if key in self._groups:
            self.end_group(key)
        self._groups[key] = _Group(
            key=key,
            snapshots=[None] * max(expected, 0),
            # S10: the panel and the aggregate exist only at N >= 2. At N == 1
            # every surface stays byte-identical to P2.
            active=expected >= PANEL_MIN_CHILDREN,
            tasks=tuple(tasks),
        )

    def adopt(self, spawn_id: str, key: str, *, index: int) -> None:
        """Bind a member's freshly minted spawn id to (group, submitted index).

        Called from the executor's per-member ``on_event`` closure, which runs
        BEFORE this bridge sees the same snapshot (``runtime.py:948-952``), so by
        the time :meth:`__call__` is reached the membership is already known.
        Idempotent: the closure calls it on every snapshot, not only the first,
        because "the first" is not a fact the closure can cheaply know.

        Unknown key, or an index outside the group, is IGNORED rather than
        raised: this runs on the delegation's own callback path, and the
        fallback — the member keeps its own per-child row — is a working UI.
        """

        group = self._groups.get(key)
        if group is None or not group.active:
            return
        if index < 0 or index >= len(group.snapshots):
            return
        self._members[spawn_id] = (key, index)

    def end_group(self, key: str) -> None:
        """Close a group: drop its members, its aggregate row and its panel.

        SAFE FOR A GROUP THAT NEVER RENDERED. The aggregate row is cleared only
        if this bridge actually wrote one, because ``_clear_row`` issues its
        ``ui.set_status(key, None)`` UNCONDITIONALLY — the ``_rows`` pop is a
        dedup-cache eviction, not a guard. Without this test, closing a group
        that never wrote a row emits a clear for a key the UI has never seen.
        Three shapes reach here that way, and the first is the one that matters:

        * an INACTIVE group (``expected < PANEL_MIN_CHILDREN`` — a group of
          one), whose whole contract under S10 is that every surface stays
          BYTE-IDENTICAL to P2. P2 opened no group and therefore made no such
          write, so the stray clear broke exactly the invariant the inactive
          group exists to preserve;
        * an active group closed before any member published — a batch that
          died on the consent gate or on a cancelled turn;
        * an active group whose aggregate text never came out non-empty
          (``_render_group`` skips the write in that case).

        The guard is deliberately confined to this method rather than pushed
        into ``_clear_row``: the un-grouped terminal path in :meth:`__call__`
        clears a born-terminal child's row that was likewise never written, and
        THAT write is pinned by ``test_a_child_born_terminal_still_reports_a_start``
        as the P2-shipped behaviour. One symmetric-looking change there would be
        a behaviour change; this one is not.
        """

        group = self._groups.pop(key, None)
        if group is None:
            return
        for spawn_id in [
            spawn_id
            for spawn_id, (member_key, _) in self._members.items()
            if member_key == key
        ]:
            del self._members[spawn_id]
        row_key = group_status_key(key)
        if row_key in self._rows:
            self._clear_row(row_key)
        if self._widget_owner == key:
            self._set_widget(key, None)

    def _member_of(self, spawn_id: str) -> tuple[_Group, int] | None:
        """The open, ACTIVE group this id belongs to and its index, or ``None``.

        ``None`` covers three real cases and they all want the same handling:
        an un-adopted id (a concurrent ``/agents run`` child — §3.6's second
        residual), a group of one, and a snapshot arriving after ``end_group``.
        Each falls back to today's per-child row, byte-identical.
        """

        member = self._members.get(spawn_id)
        if member is None:
            return None
        group = self._groups.get(member[0])
        if group is None or not group.active:
            return None
        return group, member[1]

    def _render_group(self, group: _Group) -> None:
        text = format_aggregate_status(group.snapshots)
        if text:
            # An empty aggregate means "nothing publishable yet"; writing it
            # would still cost the two-space join at ``chrome.py:1108``.
            self._set_row(group_status_key(group.key), text)
        self._set_widget(
            group.key,
            format_panel(group.snapshots, tasks=group.tasks, width=self._panel_width()),
        )

    def _panel_width(self) -> int:
        """The columns the panel is actually painting into.

        ``panel.py`` is pure and cannot see a terminal — it is the module whose
        docstring promises no UI handle — so the width is measured HERE, at the one
        place that already holds a live UI binding, and passed in. It only ever
        narrows: ``format_panel`` caps whatever arrives at ``PANEL_ROW_MAX_CHARS``,
        so a wide screen is unchanged and a narrow one stops being told it has 78
        columns.

        DEGRADES TO THE OLD CONSTANT, never raises and never returns nothing. A
        headless run, a UI without a chrome, a chrome whose output cannot be sized
        before it runs — each yields the 78 the panel used before this existed,
        which is the behaviour ADR-0199 shipped.
        """

        from aelix_coding_agent.tui.width import terminal_columns

        chrome = getattr(self._ui(), "chrome", None)
        if chrome is None:
            return PANEL_ROW_MAX_CHARS
        try:
            return terminal_columns(chrome, max_width=PANEL_ROW_MAX_CHARS)
        except Exception:  # noqa: BLE001 — an unsizeable output is not a render error
            return PANEL_ROW_MAX_CHARS

    def clear(self) -> None:
        """Drop every row and panel we own — the ``session_shutdown`` /
        teardown half.

        A statusline segment outliving the delegation that owns it is a lie the
        user cannot dismiss, and ``set_status`` has no "clear all" verb. Groups
        are ended first so the widget goes with them.
        """

        for key in list(self._groups):
            self.end_group(key)
        for key in list(self._rows):
            self._clear_row(key)
        self._tools.clear()

    # ── the UI half ───────────────────────────────────────────────

    def _ui(self) -> Any | None:
        """The LIVE UI binding, or ``None`` when there is nothing to write to.

        Read through ``api.runtime`` rather than a captured ``ctx.ui`` for the
        reason in caveat 2, and compared by IDENTITY against the headless
        singleton — the same test ``ExtensionContext.has_ui`` itself performs
        (``extensions/api.py:1175-1176``).
        """

        try:
            ui = self._api.runtime.ui
        except Exception:  # noqa: BLE001 — a stale runtime is not our problem
            return None
        return None if ui is HEADLESS_UI_CONTEXT else ui

    def _set_row(self, key: str, text: str) -> None:
        if self._rows.get(key) == text:
            return
        ui = self._ui()
        if ui is None:
            return
        with contextlib.suppress(Exception):
            ui.set_status(key, text)
            self._rows[key] = text

    def _clear_row(self, key: str) -> None:
        self._rows.pop(key, None)
        ui = self._ui()
        if ui is None:
            return
        with contextlib.suppress(Exception):
            ui.set_status(key, None)

    def _set_widget(self, group_key: str, lines: list[str] | None) -> None:
        """Write (or blank) the batch panel, through the SAME ``_ui()`` guard.

        Mandatory, not tidy: ``headless_ui.set_widget`` raises
        ``NotImplementedError`` (``headless_ui.py:126-145``) like every other
        ``ui.*`` method there, and the ``suppress`` beneath the guard covers a
        binding that flips between the guard and the call (caveat 3). A
        statusline — or panel — update must never be able to fail a delegation.

        ``set_widget(key, content)`` with no options places the widget
        ``above_editor`` (``tui/context.py:1063-1078`` → ``chrome.set_widget(...,
        above=True)``), which is the placement S10 asked for; passing
        ``ExtensionWidgetOptions`` for the default would only add an import of a
        product-core type this file does not otherwise need.
        """

        payload = None if not lines else tuple(lines)
        if payload == self._widget_lines:
            return
        ui = self._ui()
        if ui is None:
            return
        with contextlib.suppress(Exception):
            ui.set_widget(PANEL_WIDGET_KEY, None if payload is None else list(payload))
            self._widget_lines = payload
            self._widget_owner = None if payload is None else group_key


__all__ = [
    "GROUP_KEY_INFIX",
    "STATUS_KEY_PREFIX",
    "SubagentProgressBridge",
    "channels_for",
    "format_status_row",
    "group_status_key",
    "status_key",
]
