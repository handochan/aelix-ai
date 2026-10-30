"""Delegation progress — the event bus, the delegation panel and the guarded
statusline row (§(k); ADR-0278).

One object, :class:`SubagentProgressBridge`, sits on
:attr:`~aelix_agents.runtime.SubagentHost.on_progress` and fans every snapshot
the child stream produces out to two places:

* ``api.events`` — the three contract channels ``subagent_start`` /
  ``subagent_tool`` / ``subagent_end``, payload
  :class:`~aelix_coding_agent.subagent_contract.SubagentProgress`. This is the
  channel a P4 dashboard, a Web UI or a third-party extension subscribes to.
* the TUI — for a child the model's ``agent`` tool started, the delegation
  panel (a ``set_widget`` block above the input, one per call, for one child or
  eight; ADR-0278, layouts in :mod:`aelix_agents.panel`) and NO statusline row
  (D2); for a child nobody grouped — a ``/agents run`` child — ONE height-1
  statusline row (``tui/chrome.py:1097-1108`` renders ``set_status`` into a
  single line), exactly as before.

THE PANEL IS NOT DRAWN FROM THE PUBLISH PATH (ADR-0278 D6). Every snapshot is
recorded and stamped with its arrival time, then ONE redraw is scheduled no
sooner than :data:`RENDER_MIN_INTERVAL_S` after the previous one — N chatty
children cost at most four panel writes a second — and a :data:`TICK_INTERVAL_S`
tick redraws while any member is live, so elapsed and idle times move while a
child is silent. The scheduler and the clock are injectable; with no running
loop (a test, a synchronous caller) the bridge draws at once, as it always did.

THE GROUP IS OPENED WITH A COUNT, NEVER WITH IDS (ADR-0199 §3.6). ``spawn_id``
is minted inside ``runtime._run`` (``runtime.py:1386``) — after ``spawn_granted``
has already been entered, and for members 5-8 of an 8-task batch not until
wave 2 — so nothing can hand this object a list of ids when the batch starts.
Membership arrives instead through :meth:`SubagentProgressBridge.adopt`, which
the executor's per-member ``on_event`` closure calls with the index it was
created with. That is exact rather than heuristic because ``runtime._publish``
fans each snapshot out as ``for tap in (on_event, self.host.on_progress)``
(``runtime.py:1874-1878``) with no ``await`` between them: the per-spawn callback
ALWAYS runs before this session-wide tap for the same snapshot, so there is no
window in which a member's id reaches :meth:`__call__` unadopted. An id that
never gets adopted is not an error — it correctly falls back to its own
per-child row, which is what a concurrent ``/agents run`` child is.

THE EVENT-BUS HALF IS UNCHANGED BY GROUPING. ``SubagentProgress``
(``subagent_contract.py:299-327``) gains no ``batch_id`` field in P3 (§3.6:
the product-core delta is zero), so a subscriber sees N interleaved starts with
no grouping. That residual is named in ADR-0199; it is not something this file
papers over with an ``aelix_agents``-private channel.

THREE MEASURED CAVEATS, all of which shape this file.

1. ``EventBus.emit`` (``extensions/api.py:331-337``) calls handlers
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

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
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
    format_panel,
    panel_height,
)

if TYPE_CHECKING:
    from aelix_coding_agent.subagent_contract import SubagentProgress, SubagentResult

logger = logging.getLogger(__name__)

RENDER_MIN_INTERVAL_S = 0.25
"""The panel is redrawn at most this often (ADR-0278 D6): however many members
publish, four panel writes a second. 0.25 s is two and a half of the chrome's
own 0.1 s refresh ticks, so every frame written is a frame shown."""

TICK_INTERVAL_S = 1.0
"""While any member is live the panel is redrawn this often with no snapshot
behind it, so ``elapsed`` and ``idle`` advance while a child is silent — the
baseline showed a ``2s`` that stood still for nine seconds (D6)."""

_FALLBACK_WIDTH = 80
"""Columns when there is no chrome to measure — headless writes nothing anyway,
so this only shapes a UI that cannot report its size."""

Scheduler = Callable[[float, Callable[[], None]], Any]
"""``(delay_s, callback) -> handle`` — ``loop.call_later``'s shape. ``None`` back
means "no loop to schedule on": the bridge then draws at once."""


def _loop_call_later(delay: float, callback: Callable[[], None]) -> Any:
    """The default :data:`Scheduler`: the running loop's ``call_later``, or
    ``None`` outside one (a synchronous caller, a unit test)."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.call_later(delay, callback)

STATUS_KEY_PREFIX = "subagent:"
"""One key per child, so two concurrent delegations own two rows rather than
overwriting each other. ``stop`` / teardown clear by the same key."""

GROUP_KEY_INFIX = "group:"
"""The aggregate row's key was ``subagent:group:<call id>``. ADR-0278 D2 retired
the row — the bridge writes nothing under this key any more — and the name stays
public so a caller (and a test) can still say which key that was."""

_RETIRED_MAX = 64
"""How many spawn ids of ended groups the bridge remembers (eight full
batches), so a member's snapshot that arrives after its group ended writes
nothing; the oldest is forgotten first."""

_TERMINAL_STATES = frozenset({"done", "error", "stopped"})
"""``SubagentState`` values after which no further snapshot arrives. The
registry row is dropped by ``runtime._run``'s ``finally`` immediately after the
final publish, so this is the last chance to clear the statusline."""


def status_key(spawn_id: str) -> str:
    return f"{STATUS_KEY_PREFIX}{spawn_id}"


#: The disclosure row's infix. Distinct from the retired aggregate row's
#: (:data:`GROUP_KEY_INFIX`) so the two keys could never collide; since ADR-0278
#: D2 the disclosure row is the only row a model delegation writes, and
#: ``clear()`` withdraws it with every other row this bridge wrote.
DISCLOSURE_KEY_INFIX = "disclose:"


def disclosure_status_key(key: str) -> str:
    """The row that says a child is starting at a posture nobody was asked about.

    Keyed by the CALL (the parent tool-call id, or a ``/agents run`` id) rather
    than by spawn id, for the same reason :func:`group_status_key` is: one
    ``agent`` call is one decision and therefore one row, whether it starts one
    child or eight (#196, ADR-0231).
    """

    return f"{STATUS_KEY_PREFIX}{DISCLOSURE_KEY_INFIX}{key}"


def group_status_key(group_key: str) -> str:
    """The key the retired aggregate row of one ``agent`` call's group used
    (ADR-0278 D2: nothing is written under it any more)."""

    return f"{STATUS_KEY_PREFIX}{GROUP_KEY_INFIX}{group_key}"


@dataclass(slots=True)
class _Group:
    """One ``agent`` call's panel: its key, its member table and its frame state.

    ``snapshots`` is indexed by SUBMITTED position and starts all-``None``; a
    ``None`` entry is a member still parked on the batch semaphore or a chain
    step not reached yet, which the panel draws as ``○`` waiting.

    ``active`` is ``False`` only for a group of none
    (:data:`~aelix_agents.panel.PANEL_MIN_CHILDREN` is 1 since ADR-0278 D1).

    ``tasks`` is index-aligned with ``snapshots`` and is each member's label.
    It has to be carried here rather than read off a snapshot because a
    snapshot does not carry its task: one ``agent()`` call is one profile and
    one model across N tasks (S3), and the submitted INDEX is bound at member
    creation, which is exactly what makes an index-aligned tuple sufficient.
    """

    key: str
    snapshots: list[SubagentProgress | None] = field(default_factory=list)
    active: bool = False
    tasks: tuple[str, ...] = ()
    mode: str = "parallel"
    """``single`` / ``parallel`` / ``chain`` — the header says ``chain of N``
    and a chain's waiting step says which step it waits for."""
    resumed: bool = False
    """A resume (#610): the one-child header says ``(resumed)``."""
    profile: str = ""
    """The profile the call runs, known before any member publishes."""
    height: int = 0
    """Rows every frame of this group has, fixed at :meth:`begin_group` (D5)."""
    arrived: list[float | None] = field(default_factory=list)
    """When each member's latest snapshot arrived (the injected clock), so the
    panel can say ``idle 7s`` — the runtime publishes after every stdout line,
    so the time since the last snapshot IS the time since the child wrote."""
    last_render: float | None = None
    render_handle: Any = None
    tick_handle: Any = None
    cancelled: bool = False
    """Set by :meth:`SubagentProgressBridge.linger`: the call was cancelled and
    this group's last frame stays on screen until the next prompt (D7)."""
    results: dict[int, Any] = field(default_factory=dict)
    """The envelopes the Esc frame reads changed files from, by member index."""
    width: int | None = None
    """The width the last frame was drawn at, so a lingering Esc frame is
    redrawn when — and only when — the terminal's width changes."""


_STATUS_MODEL_MAX_CHARS = 40
"""Bound on the model term in :func:`format_status_row`.

The statusline is one segment on a shared height-1 row and the model is
child-authored (read off the child's own ``message_end``), so the term is
sanitised and length-capped here rather than trusted to fit. ``chrome._render_status``
strips newlines from this row but bounds neither its width nor its ESC content
(``chrome.py:1097-1108``), so this field is defended at the source, the posture
the panel already takes. ``current_tool`` and ``profile`` were left to that far
end until #178 and are now defended the same way — see
:func:`format_status_row`."""


def _format_tokens(tokens: int) -> str:
    if tokens < 1000:
        return f"{tokens} tok"
    return f"{tokens / 1000:.1f}k tok"


def format_status_row(progress: SubagentProgress) -> str:
    """The one-line statusline text for a live child.

    Deliberately terse. ``set_status`` renders into a single height-1 row shared
    with every other status segment, so anything that wraps is anything that
    disappears.

    EVERY FREE-TEXT TERM IS FLATTENED (#178). ``current_tool`` is the child's own
    stdout, and ``profile`` is whatever the runtime that published the snapshot
    put there — aelix's own runtime publishes the profile's validated ``name:``
    field, another ``SubagentRuntime`` anything; both were appended raw while
    ``model`` beside them was flattened. ``chrome._render_status`` replaces ``\\n`` and
    nothing else, then hands the row to prompt_toolkit's ``ANSI`` parser, which
    obeys SGR and passes anything between ``\\x01`` and ``\\x02`` to the terminal
    as a ``ZeroWidthEscape``. MEASURED on ``8f7d98aa`` through a real
    ``Vt100_Output``: a ``current_tool`` of
    ``read\\x1b[31mFAKE\\x01\\x1b]52;c;…\\x07\\x02`` painted the row red and
    wrote ``\\x1b]52;c;…`` — a clipboard write — to the terminal byte stream.
    ``profile`` and ``current_tool`` now go through :func:`~aelix_agents.panel._flatten`
    at :data:`~aelix_agents.panel.PANEL_ROW_MAX_CHARS`, the widget's own per-field
    bound, so a name that is clean as :func:`~aelix_agents.panel._child_line`
    defines it renders exactly as it did. Otherwise the steering characters are
    deleted (an escape sequence is defanged, not removed: its ESC goes and the
    rest stays as inert text), every run of whitespace — a no-break or
    ideographic space included — becomes one ASCII space, leading and trailing
    whitespace goes (also where a deleted control left it), and a field past the
    bound (78 cells, or 312 code points of zero-width marks) is cut with ``…``.
    ``current_tool`` is tested after flattening: one made only of controls and
    whitespace falls back to ``starting`` like an absent one. A
    non-``str`` value is ``str()``-ed first: ``profile=None`` prints
    ``agent None`` as P2's f-string did, and a non-``str`` tool, which P2's
    ``" · ".join`` raised ``TypeError`` on, now prints too.
    """

    parts = [f"agent {_flatten(str(progress.profile), limit=PANEL_ROW_MAX_CHARS)}"]
    if progress.model:
        parts.append(_flatten(progress.model, limit=_STATUS_MODEL_MAX_CHARS))
    tool = (
        _flatten(str(progress.current_tool), limit=PANEL_ROW_MAX_CHARS)
        if progress.current_tool
        else ""
    )
    if tool:
        parts.append(tool)
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
    :param clock: monotonic seconds — arrival stamps and the redraw throttle.
    :param call_later: the :data:`Scheduler` redraws are put on. Both are
        injectable so the throttle and the tick are pinned against a fake clock
        and a fake loop rather than a ``sleep``.
    """

    __slots__ = (
        "_api",
        "_call_later",
        "_groups",
        "_members",
        "_now",
        "_retired",
        "_rows",
        "_tools",
        "_widget_lines",
        "_widget_owner",
    )

    def __init__(
        self,
        api: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        call_later: Scheduler = _loop_call_later,
    ) -> None:
        self._api = api
        self._now = clock
        self._call_later = call_later
        self._rows: dict[str, str] = {}
        """statusline KEY → the text currently on screen, so an unchanged row is
        not re-written hundreds of times per turn. Keyed by the rendered key,
        so a per-child row and the disclosure row share one dedup table."""
        self._tools: dict[str, str | None] = {}
        """id → last ``current_tool``, the ``subagent_tool`` edge detector."""
        self._groups: dict[str, _Group] = {}
        """group key → its open (or lingering) :class:`_Group`."""
        self._members: dict[str, tuple[str, int]] = {}
        """spawn id → (group key, submitted index), filled by :meth:`adopt`."""
        self._retired: dict[str, None] = {}
        """Spawn ids of members whose group has ended, oldest first, at most
        :data:`_RETIRED_MAX`. A snapshot from one of them — a child still on its
        way out after ``dismiss`` or ``end_group`` — writes NOTHING (#132 review
        round 1): it used to fall through to the ungrouped path and put a
        ``subagent:<id>`` statusline row on screen for a call that was over."""
        self._widget_owner: str | None = None
        """Which group key last wrote :data:`~aelix_agents.panel.PANEL_WIDGET_KEY`.
        Tracked so ``end_group`` can only blank a panel it actually owns."""
        self._widget_lines: tuple[str, ...] | None = None
        """The panel currently on screen. ``chrome.set_widget`` calls
        ``invalidate()`` unconditionally (``chrome.py:1457-1463``), so an
        unchanged panel re-written on every redraw would be a full repaint for
        nothing."""

    def __call__(self, progress: SubagentProgress) -> None:
        """Publish one snapshot. NEVER raises — a delegation must not fail here.

        The caller (``runtime._publish``) already wraps this in
        ``contextlib.suppress(Exception)``; the containment is repeated here so
        that a failure in the bus half cannot skip the UI half, and vice versa.
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
            # The GROUPED path (ADR-0278 D2): the snapshot is recorded — a
            # terminal one too, "done" is what the panel has to be able to say
            # — and NO statusline row is written, per child or for the group.
            # Never drawn from here (D6): a redraw is scheduled instead.
            group.snapshots[index] = progress
            if progress.state in _TERMINAL_STATES:
                self._tools.pop(progress.id, None)
            if group.cancelled:
                # The Esc frame is a record of the moment of the cancel (D7); a
                # member still on its way out does not redraw it.
                return
            with contextlib.suppress(Exception):
                group.arrived[index] = self._now()
            self._schedule(group)
            return

        if progress.id in self._retired:
            # A member of a group that has ended: no panel to record it in, and
            # no statusline row either (D2 holds after the group too).
            if progress.state in _TERMINAL_STATES:
                self._retired.pop(progress.id, None)
                self._tools.pop(progress.id, None)
            return
        if progress.state in _TERMINAL_STATES:
            self._clear_row(status_key(progress.id))
            self._tools.pop(progress.id, None)
            return
        self._set_row(status_key(progress.id), format_status_row(progress))

    # ── the call's group: one panel per ``agent`` call (ADR-0278) ──

    def begin_group(
        self,
        key: str,
        *,
        expected: int,
        tasks: Sequence[str] = (),
        mode: str = "parallel",
        resumed: bool = False,
        profile: str = "",
    ) -> None:
        """Open a group of ``expected`` members under ``key``.

        A COUNT, not ids — §3.6, restated in this module's docstring: the ids do
        not exist yet and for the members still queued behind the batch
        semaphore they will not exist for minutes. The count is what lets the
        panel say ``2 queued`` instead of pretending the batch is a
        three-member one that keeps growing.

        EVERY ``agent`` CALL OPENS ONE, one child included (ADR-0278 D1), and
        the panel's height is fixed here for the life of the group (D5): from
        the member count and the terminal's rows NOW. A child's first tool,
        first reply or end then never moves the input and status rows; a
        resize changes the width only.

        ``tasks`` is the submitted task text, index-aligned with the members —
        each member's label. A LABEL, NOT A PROMPT, in ``chain`` mode: ``chain``
        substitutes ``{previous}`` per step at RUN time (``chain.py``), so
        ``tasks[k]`` is the un-substituted template, and the panel shows the
        placeholder as ``‹previous›`` rather than imply the child saw it.
        ``mode``, ``resumed`` and ``profile`` shape the header.

        A frame left on screen by a cancelled call (:meth:`linger`) goes first:
        a new group is a new call. Re-opening a live key ends the old group too —
        that can only happen if a caller leaked one, and a stale panel is a lie
        the user cannot dismiss. Nothing is drawn until a member publishes.
        """

        self.dismiss()
        if key in self._groups:
            self.end_group(key)
        count = max(expected, 0)
        self._groups[key] = _Group(
            key=key,
            snapshots=[None] * count,
            active=count >= PANEL_MIN_CHILDREN,
            tasks=tuple(tasks),
            mode=mode,
            resumed=resumed,
            profile=profile,
            height=panel_height(count, terminal_rows=self._terminal_rows()),
            arrived=[None] * count,
        )

    def adopt(self, spawn_id: str, key: str, *, index: int) -> None:
        """Bind a member's freshly minted spawn id to (group, submitted index).

        Called from the executor's per-member ``on_event`` closure, which runs
        BEFORE this bridge sees the same snapshot (``runtime.py:1874-1878``), so by
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
        """Close a group: cancel its redraws, drop its members, blank its panel.

        Every scheduled redraw and tick of the group is cancelled HERE, so
        nothing the loop runs later can draw a panel for a call that is over.
        The panel is blanked only if this group owns it, so a group closing
        late cannot blank another call's. Writes nothing for a group that never
        drew — a call refused before its first child, or cancelled before one
        published.
        """

        group = self._groups.pop(key, None)
        if group is None:
            return
        self._cancel_handles(group)
        for spawn_id in [
            spawn_id
            for spawn_id, (member_key, _) in self._members.items()
            if member_key == key
        ]:
            del self._members[spawn_id]
            self._retire(spawn_id)
        if self._widget_owner == key:
            self._set_widget(key, None)

    def _retire(self, spawn_id: str) -> None:
        """Remember an ended group's member, so its late snapshots write
        nothing; bounded at :data:`_RETIRED_MAX`, oldest forgotten first."""

        self._retired.pop(spawn_id, None)
        self._retired[spawn_id] = None
        while len(self._retired) > _RETIRED_MAX:
            del self._retired[next(iter(self._retired))]

    def linger(self, key: str, results: Mapping[int, SubagentResult]) -> bool:
        """Leave a cancelled call's record on screen (ADR-0278 D7).

        Called from the extension's ``except CancelledError`` with each started
        member's envelope — the same ``CallEnvelopes.snapshot()`` the model's
        report is built from — and draws the Esc frame AT ONCE (the cancel path
        is not the publish path, and nothing after it is certain to run): every
        member still live reads as stopped, its cut call as ``no result``, its
        changed files beside it. The frame keeps the group's height (D5) and
        stays until the next prompt (:meth:`dismiss`), a new group, or
        :meth:`clear`.

        Returns whether the frame stays. ``False`` — the group was ended
        instead — when there is nothing to keep: no such group (a session
        teardown's :meth:`clear` already took it), no member that ever
        published, or THE ESC FRAME ITSELF COULD NOT BE WRITTEN (#132 review
        round 1). The last is not "the group owns the widget": MEASURED with the
        real ``RpcCommandUI``, a frame that failed to encode was suppressed, the
        group still owned the PREVIOUS frame, and ``linger`` said True while
        the client showed ``running``. Ending the group blanks that stale frame.
        NEVER raises.

        While the frame stays, a watch redraws it when the terminal's width
        changes (:meth:`_watch`) — never its height (D5).
        """

        try:
            group = self._groups.get(key)
            if group is None or not group.active or all(
                snapshot is None for snapshot in group.snapshots
            ):
                self.end_group(key)
                return False
            self._cancel_handles(group)
            group.cancelled = True
            group.results = dict(results)
            if not self._render(group):
                self.end_group(key)
                return False
            self._arm_watch(group)
            return True
        except Exception:  # noqa: BLE001 — a frame is never worth the cancel
            logger.debug("could not leave the cancelled call's panel", exc_info=True)
            with contextlib.suppress(Exception):
                self.end_group(key)
            return False

    def dismiss(self) -> None:
        """Take down a frame :meth:`linger` left — the next prompt has started
        (``before_agent_start``) or a new call opened its own group (D7)."""

        for key, group in list(self._groups.items()):
            if group.cancelled:
                self.end_group(key)

    def _member_of(self, spawn_id: str) -> tuple[_Group, int] | None:
        """The open, ACTIVE group this id belongs to and its index, or ``None``.

        ``None`` covers an un-adopted id (a concurrent ``/agents run`` child —
        §3.6's second residual), which falls back to the per-child row,
        byte-identical to what it always was, and a snapshot arriving after
        ``end_group`` — which :meth:`__call__` drops instead, by the id
        :meth:`end_group` retired (#132 review round 1).
        """

        member = self._members.get(spawn_id)
        if member is None:
            return None
        group = self._groups.get(member[0])
        if group is None or not group.active:
            return None
        return group, member[1]

    # ── redraws: throttled, ticking, never from the publish path (D6) ──

    def _later(self, delay: float, callback: Callable[[], None]) -> Any:
        try:
            return self._call_later(delay, callback)
        except Exception:  # noqa: BLE001 — no loop to schedule on: draw now
            return None

    def _schedule(self, group: _Group) -> None:
        """Ask for ONE redraw of ``group``, no sooner than
        :data:`RENDER_MIN_INTERVAL_S` after its last one. A redraw already
        pending absorbs this request — it will read the newest snapshots."""

        if group.render_handle is not None:
            return
        if self._ui() is None:
            # Nothing to draw into — ``-p``, ``--mode json``, an RPC prompt. No
            # frame is formatted and no tick armed (#132 review round 1: the
            # bridge formatted a panel on every throttle and tick and wrote it
            # nowhere). A UI that binds later is drawn on the next snapshot.
            return
        delay = 0.0
        if group.last_render is not None:
            with contextlib.suppress(Exception):
                delay = max(0.0, group.last_render + RENDER_MIN_INTERVAL_S - self._now())
        key = group.key
        handle = self._later(delay, lambda: self._scheduled_render(key))
        if handle is None:
            self._render(group)
        else:
            group.render_handle = handle

    def _scheduled_render(self, key: str) -> None:
        group = self._groups.get(key)
        if group is None:
            return
        group.render_handle = None
        self._render(group)

    def _tick(self, key: str) -> None:
        group = self._groups.get(key)
        if group is None:
            return
        group.tick_handle = None
        self._schedule(group)

    def _arm_tick(self, group: _Group) -> None:
        """While a member is live, redraw every :data:`TICK_INTERVAL_S` even
        with no snapshot, so a silent child's clock still moves (D6)."""

        if group.cancelled or group.tick_handle is not None:
            return
        if not any(
            snapshot is not None and snapshot.state not in _TERMINAL_STATES
            for snapshot in group.snapshots
        ):
            return
        key = group.key
        group.tick_handle = self._later(TICK_INTERVAL_S, lambda: self._tick(key))

    def _arm_watch(self, group: _Group) -> None:
        """While an Esc frame lingers, look at the terminal's width every
        :data:`TICK_INTERVAL_S` (#132 review round 1). The frame was drawn at
        one width and stays for as long as the user takes to type the next
        prompt; after a resize the chrome clipped it with no ``…``. Its handle
        is the group's tick handle, so ``dismiss``, ``end_group`` and ``clear``
        cancel it like any other."""

        if not group.cancelled or group.tick_handle is not None:
            return
        key = group.key
        group.tick_handle = self._later(TICK_INTERVAL_S, lambda: self._watch(key))

    def _watch(self, key: str) -> None:
        """Redraw a lingering frame at the new width — and only when the width
        changed, so an unchanged frame is not formatted again. Same height."""

        group = self._groups.get(key)
        if group is None or not group.cancelled:
            return
        group.tick_handle = None
        with contextlib.suppress(Exception):
            if self._panel_width() != group.width:
                self._render(group)
        self._arm_watch(group)

    @staticmethod
    def _cancel_handles(group: _Group) -> None:
        for handle in (group.render_handle, group.tick_handle):
            if handle is not None:
                with contextlib.suppress(Exception):
                    handle.cancel()
        group.render_handle = None
        group.tick_handle = None

    def _render(self, group: _Group) -> bool:
        """Draw ``group``'s panel now; whether that frame is on screen. NEVER
        raises: it runs as a loop callback, where an exception would be printed
        over the TUI, and in the cancel path, where it must not replace the
        cancel. With no UI to write to it formats nothing (#132 review round 1)."""

        if self._ui() is None:
            return False
        try:
            now = self._now()
            idle = [
                None if arrived is None else int(max(0.0, now - arrived) * 1000)
                for arrived in group.arrived
            ]
            width = self._panel_width()
            lines = format_panel(
                group.snapshots,
                tasks=group.tasks,
                width=width,
                height=group.height,
                mode=group.mode,
                resumed=group.resumed,
                profile=group.profile,
                idle_ms=idle,
                cancelled=group.cancelled,
                results=group.results,
            )
            group.last_render = now
            group.width = width
            shown = self._set_widget(group.key, lines)
            self._arm_tick(group)
            return shown
        except Exception:  # noqa: BLE001 — a frame is never worth a delegation
            logger.debug("could not draw the delegation panel", exc_info=True)
            return False

    def _panel_width(self) -> int:
        """The columns the panel is painting into, re-read on every frame.

        ``panel.py`` is pure and cannot see a terminal — it is the module whose
        docstring promises no UI handle — so the width is measured HERE, at the
        one place that already holds a live UI binding, and passed in. It is the
        transcript's own measure (``tui.width.terminal_columns``, capped at 120),
        so the panel and the tool cards above it are the same width.

        DEGRADES, never raises: a headless run, a UI without a chrome, a chrome
        whose output cannot be sized yet — each yields 80 columns.
        """

        from aelix_coding_agent.tui.width import terminal_columns

        chrome = getattr(self._ui(), "chrome", None)
        if chrome is None:
            return _FALLBACK_WIDTH
        try:
            return terminal_columns(chrome)
        except Exception:  # noqa: BLE001 — an unsizeable output is not a render error
            return _FALLBACK_WIDTH

    def _terminal_rows(self) -> int | None:
        """The terminal's rows when a group opens (D5), or ``None`` when they
        cannot be read — the panel then assumes 24."""

        try:
            chrome = getattr(self._ui(), "chrome", None)
            if chrome is None:
                return None
            rows = int(chrome.app.output.get_size().rows)
        except Exception:  # noqa: BLE001 — no size is "assume the default"
            return None
        return rows if rows > 0 else None

    def announce(self, key: str, text: str | None) -> None:
        """Write (``text``) or withdraw (``None``) one call's disclosure row.

        WHY A STATUS ROW AND NOT THE TRANSCRIPT. Measured with a positive
        control: feeding ``tool_execution_update`` — what ``ctx.on_partial``
        produces — through the real ``EventRenderer`` yields ZERO commits,
        because ``tui/render.py`` no-ops that event; the same harness commits a
        line for ``tool_execution_start``. Nothing in the extension band can
        reach the transcript before a child runs: ``ExtensionUIContext`` has no
        commit verb, and adding one is a product-core contract change. So the
        pre-spawn surface is this row, and the PERMANENT record is the tool
        card's own footer, which already names the posture
        (``tool._usage_line`` renders ``[agent scout · … · yolo · …]``).

        Goes through the same ``_set_row`` / ``_clear_row`` as every other row
        here, which means the same live-``ui`` guard, the same headless no-op
        and the same ``suppress`` — a delegation must not die of a statusline
        write (caveat 3).
        """

        row = disclosure_status_key(key)
        if text is None:
            self._clear_row(row)
        else:
            self._set_row(row, text)

    def clear(self) -> None:
        """Drop every row and panel we own — the ``session_shutdown`` /
        teardown half.

        A statusline segment outliving the delegation that owns it is a lie the
        user cannot dismiss, and ``set_status`` has no "clear all" verb. Groups
        are ended first — a lingering Esc frame (D7) and every scheduled redraw
        and tick with them — so the widget goes too.
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
        (``extensions/api.py:1238-1239``).
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

    def _set_widget(self, group_key: str, lines: list[str] | None) -> bool:
        """Write (or blank) the delegation panel, through the SAME ``_ui()`` guard.

        Mandatory, not tidy: ``headless_ui.set_widget`` raises
        ``NotImplementedError`` (``headless_ui.py:126-145``) like every other
        ``ui.*`` method there, and the ``suppress`` beneath the guard covers a
        binding that flips between the guard and the call (caveat 3). A
        statusline — or panel — update must never be able to fail a delegation.

        ``set_widget(key, content)`` with no options places the widget
        ``above_editor`` (``tui/context.py:1775-1790`` → ``chrome.set_widget(...,
        above=True)``), which is the placement S10 asked for and ADR-0278 keeps; passing
        ``ExtensionWidgetOptions`` for the default would only add an import of a
        product-core type this file does not otherwise need.

        Returns whether ``lines`` is what the widget now shows — written now,
        or already there (the dedup) — so :meth:`linger` can tell a frame that
        reached the screen from one a UI refused (#132 review round 1).
        """

        payload = None if not lines else tuple(lines)
        if payload == self._widget_lines:
            return True
        ui = self._ui()
        if ui is None:
            return False
        try:
            ui.set_widget(PANEL_WIDGET_KEY, None if payload is None else list(payload))
        except Exception:  # noqa: BLE001 — a panel write never fails a delegation
            logger.debug("could not write the delegation panel", exc_info=True)
            return False
        self._widget_lines = payload
        self._widget_owner = None if payload is None else group_key
        return True


__all__ = [
    "GROUP_KEY_INFIX",
    "STATUS_KEY_PREFIX",
    "SubagentProgressBridge",
    "channels_for",
    "format_status_row",
    "group_status_key",
    "status_key",
]
