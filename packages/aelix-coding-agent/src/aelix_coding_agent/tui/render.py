"""Sprint 6h₁₀a (ADR-0104) / 6h₁₀b (ADR-0105) — harness-event → output renderer.

:class:`EventRenderer` is the TUI's :meth:`AgentHarness.subscribe` sink. It
mirrors the rpc/print frontends but renders to the TUI instead of JSONL/stdout.

Sprint 6h₁₀b rework (ADR-0105): output no longer goes to a Rich ``Live`` (the
live region is owned by the prompt-toolkit chrome). Instead the renderer emits
through two synchronous sinks the chrome shell wires up:

- ``commit(renderable)`` — a finished Rich renderable → scrollback (queued, then
  flushed above the chrome via ``in_terminal``).
- ``set_tail(ansi)`` — the in-progress streamed-text window → the chrome stream
  widget (``StreamRenderer`` owns the window/throttle logic).

Dispatch is ``match`` on the ``type`` Literal; unknown types are no-ops
(forward-compatible). Terminal failures surface on ``message_end`` via
``stop_reason`` (loop.py path; the streaming ``error`` event is never re-emitted
as a ``MessageUpdateEvent``). Out-of-band prints finalize any open text stream
first so they never interleave with the live tail.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import AssistantMessage
from rich.cells import cell_len, set_cell_size
from rich.console import Group
from rich.padding import Padding
from rich.text import Text

from .stream import StreamRenderer, markdown_lines, plain_lines

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
    from aelix_agent_core.types import AgentEvent
    from aelix_ai.streaming import AssistantMessageEvent

    from .descriptors import DescriptorRenderer


def _result_text(result: Any) -> str:
    """Extract a printable string from a ``ToolResult`` (or any payload)."""

    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            parts.append(text if isinstance(text, str) else str(block))
        return "\n".join(parts)
    return str(content)


def _block_type(block: Any) -> Any:
    """Type discriminator tolerant of both dataclass blocks and raw dicts.

    ``TextContent``/``ImageContent`` objects expose ``.type`` as an attribute,
    but a ``custom_message`` entry's ``content`` is stored VERBATIM off the
    JSONL wire as ``[{"type": "text", "text": ...}]`` plain dicts
    (``entry_from_json``, no re-materialisation) — where ``type`` is a KEY,
    not an attribute (issue #62 review). Read both.
    """

    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_text(block: Any) -> str:
    if isinstance(block, dict):
        return block.get("text", "") or ""
    return getattr(block, "text", "") or ""


def _join_text(content: Any) -> str:
    """Join the ``TextContent`` blocks of a message body into one string.

    Non-text blocks (images) are skipped. Blocks may be dataclass content
    objects OR raw wire dicts (custom messages — see :func:`_block_type`).
    Defensive: a non-iterable / odd payload yields ``""`` rather than raising
    during transcript replay.
    """

    if not isinstance(content, (list, tuple)):
        return ""
    return "\n".join(_block_text(b) for b in content if _block_type(b) == "text")


def component_to_text(component: Any, width: int) -> Text:
    """Snapshot a pi-tui ``Component`` (``render(width) -> list[str]`` of raw
    ANSI lines) into a Rich ``Text`` for the scrollback (issue #62, ADR-0183).

    The extension custom-message renderer returns a ``Component``; the shell
    closure converts it here so the (multi-line, ANSI) result is committed
    identically to a live-streamed block. Extracted module-level so the
    conversion is unit-testable (review MEDIUM: it was previously buried in a
    ``run_tui`` closure and never asserted).
    """

    lines = component.render(width)
    return Text.from_ansi("\n".join(str(line) for line in lines))


def _compact_args(args: dict[str, Any]) -> str:
    """One-line, length-capped argument summary for a tool header."""

    if not args:
        return ""
    items = ", ".join(f"{k}={v!r}" for k, v in args.items())
    items = items.replace("\n", " ")
    return _cap_cells(items, _HEADER_MAX_CELLS)


#: Tool-header summary cap, in terminal CELLS. Deliberately NOT derived from the
#: live width: the header is committed as a bare Rich ``Text`` to the adaptive
#: scrollback console, which soft-wraps it, so this is a density choice rather
#: than an overflow guard.
_HEADER_MAX_CELLS = 80

#: Trailing reasoning lines held in the live tail while a thinking block
#: streams. Matches ``StreamRenderer``'s window so reasoning and answer text
#: occupy the same amount of screen while they are in flight.
_THINKING_LIVE_WINDOW = 12

#: Floor on how often the reasoning tail repaints, in seconds. Same 10 FPS floor
#: ``StreamRenderer`` settled on: above ~16 Hz the eye reads widget growth as
#: thrash rather than as a smooth update, and every push repaints a chrome
#: widget.
_THINKING_TAIL_MIN_DELAY = 0.1

#: Ceiling the adaptive throttle can back off to, in seconds. Same 2.0 as
#: ``StreamRenderer``'s ``max_delay``: past that the window stops reading as live.
_THINKING_TAIL_MAX_DELAY = 2.0

#: Rows of headroom above :data:`_THINKING_LIVE_WINDOW` when sizing the slice of
#: the accumulator that can still reach the window. The budget is counted in
#: CHARACTERS against a width counted in CELLS; one cell per character (ASCII) is
#: the case that yields the fewest rows per character, so ``width × (window +
#: slack)`` characters is ``window + slack`` rows at worst and more for any run of
#: wide glyphs. The slack keeps that worst case clear of the window exactly.
_THINKING_TAIL_LINE_SLACK = 2


def _cap_cells(text: str, limit: int) -> str:
    """Cap *text* at *limit* terminal CELLS, appending ``…`` when it is cut.

    Cells, not codepoints. These two headers measured with ``len()`` until issue
    #166: a Hangul or emoji summary passing an 80-CODEPOINT check is up to 160
    cells, so it silently ate two or three scrollback rows where an ASCII one
    took a single row. The adaptive console soft-wrapped it, which is why the
    unit mismatch was invisible — and why leaving it in place while the width
    became dynamic would have made the CJK case drift further, not less.
    ``_truncate_lines`` below has always measured cells; this makes the file
    agree with itself.
    """

    return text if cell_len(text) <= limit else set_cell_size(text, limit - 1) + "…"


def _truncate_lines(
    text: str, max_lines: int = 12, max_line_width: int = 76
) -> tuple[list[str], int]:
    """Keep the first ``max_lines`` lines, each hard-capped at ``max_line_width``.

    PURE. Returns ``(kept_lines, hidden_count)`` where ``hidden_count`` is the
    number of trimmed trailing lines. Width is measured in **terminal cells**
    (CJK / wide chars count as 2) via ``rich.cells`` so a line of Hangul doesn't
    overflow; a too-wide line is cut to ``max_line_width - 1`` cells plus ``…``.
    The default leaves room for the 2-cell ``│ `` card gutter within an 80-col
    chrome.
    """

    lines = text.split("\n")
    kept = lines[:max_lines]
    hidden = len(lines) - len(kept)
    capped: list[str] = [
        line if cell_len(line) <= max_line_width else set_cell_size(line, max_line_width - 1) + "…"
        for line in kept
    ]
    return capped, hidden


def _tool_header(tool_name: str, args: dict[str, Any]) -> str:
    """Tool-aware one-line argument summary for the ``●`` start header.

    ``read``/``write``/``edit`` show the ``path`` (read appends an
    ``offset:limit`` line range when present); ``bash`` shows the ``command``;
    every other tool falls back to :func:`_compact_args`.
    """

    if tool_name in ("read", "write", "edit"):
        path = args.get("path")
        if isinstance(path, str) and path:
            if tool_name == "read":
                offset = args.get("offset")
                limit = args.get("limit")
                if offset or limit:
                    # Args come from unvalidated model tool-call JSON — a
                    # non-numeric offset/limit must degrade to the bare path,
                    # not raise inside the (unguarded) start-header render.
                    try:
                        start = int(offset) if offset else 0
                        if limit:
                            return f"{path}:{start}-{start + int(limit)}"
                        return f"{path}:{start}-"
                    except (TypeError, ValueError):
                        return path
            return path
    elif tool_name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command:
            one_line = command.replace("\n", " ")
            return _cap_cells(one_line, _HEADER_MAX_CELLS)
    return _compact_args(args)


def _bash_exit_code(result: Any) -> int | None:
    """Extract a bash exit code from a ``ToolResult`` payload, else ``None``."""

    details = getattr(result, "details", None)
    code = getattr(details, "exit_code", None)
    return code if isinstance(code, int) else None


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


def _looks_like_diff(text: str) -> bool:
    """True for difflib-style unified-diff output.

    Requires a real ``@@ -n,n +n,n @@`` hunk header (not bare ``@@``) so
    ordinary output — markdown with a ``---`` rule, lines starting with
    ``+``/``-`` — is not mis-coloured as a diff.
    """
    return _HUNK_HEADER_RE.search(text) is not None


def _render_diff(
    text: str,
    *,
    max_lines: int = 40,
    expand_id: int | None = None,
    max_line_width: int = 76,
) -> Group:
    """Colourise a unified diff: +green / -red / @@cyan / ---|+++ bold.

    ``expand_id`` (when the diff is truncated) is appended to the elision footer
    as a ``/expand N`` hint so the user can recover the full diff (ADR-0121).
    """
    kept, hidden = _truncate_lines(text, max_lines=max_lines, max_line_width=max_line_width)
    rows: list[Text] = []
    for line in kept:
        if line.startswith(("+++", "---")):
            style = "bold"
        elif line.startswith("@@"):
            style = "cyan"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        else:
            style = "dim"
        rows.append(Text(line, style=style))
    if hidden > 0:
        suffix = f" · /expand {expand_id}" if expand_id is not None else ""
        hint = f"… (+{hidden} more lines{suffix})"
        rows.append(Text(hint, style="dim"))
    return Group(*rows)


# Sprint 6h₂₅ (ADR-0153, TUI v2 quick-wins WP-6 trivial tier) — shared user-echo
# vocabulary. Human input was the weakest visual element (monochrome ``» text``
# with no separation, buried among colored tool cards / diffs / thinking). The
# trivial-tier lift was ONE helper every human-input site shares: a leading blank
# line for separation + a bold-cyan echo that stands out.
#
# #48 lands the medium tier that comment deferred — the full-width background
# bar. Padding + blank lines were still only NEGATIVE space, so a human turn read
# as "a gap" rather than as its own object, and a wrapped one lost even that.
_USER_MESSAGE_LABELS: dict[str, str] = {
    "prompt": "» ",
    "steer": "Steering: ",
    "follow_up": "Follow-up: ",
}

#: The user-echo bar. AN EXPLICIT FOREGROUND **AND** BACKGROUND, deliberately.
#:
#: The rest of this module names a colour and lets the terminal supply the
#: ground (``bold cyan``, ``dim``, …), which is only safe while the ground is
#: whatever the user already reads text on. A bar owns its ground, so BOTH
#: halves have to be stated.
#:
#: Cyan because that is already aelix's colour — the tool-card marker and header
#: are cyan, so the bar reads as part of the same product rather than as a
#: foreign stripe. It stays the terminal's OWN cyan (ANSI 46, not a 256/truecolor
#: shade), so it tracks the user's theme and survives the colour-system degrade
#: Rich does on a poorer terminal. Only the text is pinned, to black.
#:
#: ``reverse`` was the other candidate and was REJECTED on measurement. Rich
#: emits it as ``1;7;36``: foreground cyan plus SGR 7, and the terminal then
#: swaps, so the TEXT becomes the terminal's own background colour. On a dark
#: theme that is dark-on-cyan and fine; on a light one it is near-white on cyan,
#: which is the low-contrast case. It delegates half the pair to the user's
#: theme, and it is the half that can go wrong. "Cannot clash" was true of hue
#: and not of contrast.
#:
#: It is NOT routed through ``tui/themes.py``. That registry is the
#: EXTENSION-facing surface (``context.py`` hands it to widget factories); no
#: renderer in this module has ever consulted it, and its roles are
#: foreground-only. Wiring the transcript renderer to it is real work and is what
#: #58 (auto light/dark) is actually about — doing it here, for one line, would
#: leave the other twenty hardcoded styles behind and claim a theme integration
#: that does not exist.
#:
#: MEASURED through the commit path (``chrome.print_above`` →
#: ``chrome._console.print``): the SGR survives, pyte reads the cells back, and
#: with ``Padding`` every wrapped row is covered edge to edge. Piped output and
#: ``NO_COLOR`` drop it entirely — the text and its ``» `` marker still carry the
#: turn there, which is why the marker was not replaced by the bar.
_USER_ECHO_STYLE = "bold black on cyan"

# Sprint 6h₃₂ — the tool-call header marker. A bold filled ``●`` (replacing the
# thin ``⚙`` gear) reads at a glance against the dim result card below it; the
# tool NAME is bolded so it stands out from its argument summary. Shared by the
# live render path and transcript replay via :func:`render_tool_call_line`.
_TOOL_MARKER = "●"


def render_user_message(text: str, kind: str = "prompt") -> Group:
    """Build the canonical echo for a human turn (Sprint 6h₂₅, ADR-0153).

    Every site that echoes the user's own input — the live prompt, the replayed
    transcript, and steer / follow-up injections — routes through this helper so
    they share ONE visual language. The turn is a full-width bar
    (:data:`_USER_ECHO_STYLE`), still fenced by a blank line above and below.

    #48 — WHY A BAR AND NOT MORE PADDING. ADR-0153 gave the echo a leading blank
    and bold cyan; Sprint 6h₃₂ added the trailing blank because one was "too
    subtle when the turn landed mid-stream". Both rounds bought separation with
    NEGATIVE space, and negative space is what a human turn was already made of —
    it read as a gap between coloured tool cards rather than as an object. The
    bar gives it its own ground, which is the one property none of the
    surrounding renderers use (tool cards colour their marker, diffs their
    gutter, reasoning is dim italic — all on the terminal's ground).

    ``Padding`` rather than a padded :class:`Text`: MEASURED, a manually
    right-padded ``Text`` covers only the FIRST row once the line wraps, leaving
    a ragged bar; ``Padding`` with a style fills every wrapped row edge to edge.
    The ``(0, 1)`` inset keeps the glyphs off the terminal's left column.

    ``kind`` selects the leading marker: ``"prompt"`` keeps the ``» `` chevron;
    ``"steer"`` / ``"follow_up"`` use a distinct ``Steering: `` / ``Follow-up: ``
    label inside the SAME bar. An unknown kind degrades to the prompt chevron.
    """

    label = _USER_MESSAGE_LABELS.get(kind, _USER_MESSAGE_LABELS["prompt"])
    return Group(
        Text(""),
        Padding(Text(f"{label}{text}"), (0, 1), style=_USER_ECHO_STYLE),
        Text(""),
    )


def render_tool_call_line(tool_name: str, summary: str) -> Text:
    """Build the styled one-line header for a tool call (Sprint 6h₃₂).

    A bold ``●`` marker (more visible than the prior thin ``⚙`` gear) + the tool
    NAME in bold, then the argument ``summary`` in the default card weight inside
    parentheses. The whole line keeps the card's cyan hue; only the marker and
    name are bolded so the name reads first and the args stay secondary. Shared
    by the live (:meth:`EventRenderer._render_tool_start`) and replayed
    (:meth:`EventRenderer.replay`) paths so a resumed transcript is pixel-identical
    to a freshly-streamed turn.
    """

    line = Text()
    line.append(f"{_TOOL_MARKER} ", style="bold cyan")
    line.append(tool_name, style="bold cyan")
    if summary:
        line.append(f"({summary})", style="cyan")
    return line


class EventRenderer:
    """Renders the harness :data:`AgentEvent` stream via commit/tail sinks.

    :param commit: sync sink for a finished Rich renderable (→ scrollback).
    :param set_tail: sync sink for the in-progress streamed-text window (→ chrome).
    :param width: render width for streamed text.
    """

    def __init__(
        self,
        *,
        commit: Callable[[object], None],
        set_tail: Callable[[str], None],
        width: int | Callable[[], int] = 80,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._commit = commit
        self._set_tail = set_tail
        self._time = time_fn
        # Issue #166 — a CALLABLE re-measures the terminal; an int keeps the
        # historical fixed behaviour for the many tests that pass one. The
        # resolution point is ``_new_stream``, which already runs once per
        # assistant message, so a resize is picked up from the next message on.
        # Committed scrollback is never reflowed: those bytes belong to the host
        # terminal (the app is ``full_screen=False``), which is the same
        # guarantee every other terminal program offers.
        self._width_of: Callable[[], int] = width if callable(width) else (lambda: width)
        self._text_stream: StreamRenderer | None = None
        self._text_accum: str = ""
        self._thinking_accum: str = ""
        # Issue #170 — reasoning STREAMS to the live tail as it arrives instead
        # of appearing all at once when the block closes. Every shipping adapter
        # already emits ``thinking_delta`` in real time (anthropic, google,
        # openai-responses, openai-completions); the renderer was the only thing
        # holding them, so on a slow reasoning model the user watched a spinner
        # with nothing behind it.
        #
        # Tail ONLY: the block is still committed to scrollback exactly once,
        # when it closes. Reasoning is ephemeral scaffolding — you want to watch
        # it happen and then have one tidy block in history, not a progressive
        # dribble of committed lines (which would also make every existing
        # commit assertion in the suite a lie).
        #
        # Keyed on ``content_index``, which is what distinguishes one thinking
        # block from the next on the wire and which this renderer previously
        # never read. The old ``_thinking_flushed`` boolean conflated "already
        # printed THIS block" with "already printed ANY block", so a second
        # block in the same message was silently dropped (#169).
        self._thinking_index: int | None = None
        self._thinking_done: set[int] = set()
        self._thinking_tail_when: float | None = None
        # "A live window is currently painted for this block." The RELEASE half
        # of the tail. Derived state is not good enough here: the obvious
        # ``was_streaming and not self.hide_thinking`` re-reads a flag the user
        # can flip MID-BLOCK (Ctrl+T, /settings), so the clear was skipped for
        # exactly the tail that was painted under the old value and the
        # reasoning the user just asked to hide stayed welded above the prompt.
        self._thinking_tail_shown: bool = False
        # Adaptive throttle, mirroring ``StreamRenderer``'s governor
        # (stream.py:155-158). ``_THINKING_TAIL_MIN_DELAY`` is a FLOOR, not the
        # interval: if a push ever gets expensive the gap widens with it, so the
        # renderer can never spend an unbounded share of the turn inside its own
        # repaint. Reset per block, like ``_thinking_tail_when``.
        self._thinking_min_delay: float = _THINKING_TAIL_MIN_DELAY
        # Issue #133 item 2 — set when ``_render_message_error`` has already
        # printed a terminal-outcome line for the message that just ended, so
        # ``_render_turn_abort`` does not print a SECOND line for the same abort.
        # Scoped to the message_end -> turn_end adjacency it guards: cleared on
        # message_start AND consumed by ``_render_turn_abort``. See that method
        # for why it is a bool rather than the message object.
        self._outcome_reported: bool = False
        # /expand support (ADR-0121) — full, untruncated tool-result bodies kept
        # by sequential id so ``/expand N`` can recover the text a truncated card
        # elided. Only TRUNCATED cards get an id (that's when /expand is useful);
        # the id is surfaced on the truncation footer (``… /expand N``). Bounded
        # so a long session can't grow this without limit (oldest dropped first).
        self._expand_store: dict[int, str] = {}
        self._expand_seq: int = 0
        self._expand_max: int = 100
        # Issue #66 (TUI polish) — configurable cap on the NORMAL tool-card output
        # body. Default 12 (unchanged behaviour); run_tui seeds this from the
        # persisted ``get_tool_card_max_lines()`` setting (clamped [3, 40]). Governs
        # ONLY the normal-output card path in _render_tool_end — the separate
        # 40-line diff/error cap is a distinct literal and stays 40.
        self.tool_card_max_lines: int = 12
        # ADR-0115 still holds and is why the COMMIT happens where it does:
        # ``thinking_end`` arrives at end-of-stream, after the answer already
        # streamed, so a block is committed BEFORE the text/tool that follows it
        # rather than when its end event lands — otherwise reasoning prints
        # after the answer it preceded. What changed for #170 is only that the
        # block is also shown live while it accumulates; the idempotency the old
        # ``_thinking_flushed`` flag provided now lives in ``_thinking_done``,
        # keyed per block.
        # Thinking-block visibility (Sprint 6h₁₅, ADR-0123). Default VISIBLE to
        # match pi's ``hideThinkingBlock`` default (False) — issue #50 reconcile:
        # the run_tui startup seed overwrites this from the persisted setting, so
        # this hardcoded default only governs headless / no-settings contexts and
        # must agree with the settings default (visible). When HIDDEN (persisted
        # setting or live Ctrl+T), pi shows an italic "Thinking…" placeholder to
        # keep reasoning-heavy models from flooding the transcript. Aelix
        # divergence from pi: pi's Ctrl+T rebuilds the whole chat to retroactively
        # toggle PAST blocks; inline scrollback can't, so the toggle affects
        # subsequent renders — but a collapsed block is routed through the /expand
        # store so its full reasoning stays recoverable (``💭 Thinking… (/expand N)``).
        #
        # A PROPERTY rather than a plain attribute (see the setter): since #170
        # reasoning is painted live, so turning the setting ON has to take the
        # already-painted window down. Doing that in the setter covers all three
        # writers — the startup seed, /settings live-apply and Ctrl+T
        # (shell.py:602, 1051, 2151) — without asking each to remember.
        self._hide_thinking: bool = False
        self._hidden_thinking_label: str = "Thinking…"
        # Aelix-original DISPLAY gate: when True, the persisted compaction-summary
        # message (a ``UserMessage`` carrying ``COMPACTION_SUMMARY_PREFIX``) is
        # collapsed to a one-line marker on transcript replay. Seeded by run_tui
        # from ``SettingsManager.get_hide_compaction_summary()`` (live-applied via
        # /settings). The summary stays in the LLM context — this gates DISPLAY.
        self.hide_compaction_summary: bool = False
        # §B — live tool-result interception. Late-bound by run_tui to read the
        # descriptor registry by reference (returns a matching tool-renderer-desc
        # envelope for a tool_name, or None). ``descriptor_renderer`` builds the
        # custom view. Both unset → default Text-dump rendering (unchanged).
        self.get_tool_renderer_desc: Callable[[str], DescriptorEnvelope | None] | None = None
        self.descriptor_renderer: DescriptorRenderer | None = None
        # Issue #62 (ADR-0183) — extension custom-message rendering hook.
        # Late-bound by run_tui: given a DISPLAY-tier custom message (rich
        # ``CustomMessage`` with custom_type/content/details), returns a Rich
        # renderable from the extension's registered MessageRenderer, or None
        # to fall through to the default rendering (the get_tool_renderer_desc
        # idiom). Unset (headless/tests) → default rendering.
        self.render_custom_message: Callable[[Any], object | None] | None = None

    @property
    def hide_thinking(self) -> bool:
        """Whether reasoning is collapsed to a ``💭 Thinking…`` placeholder."""

        return self._hide_thinking

    @hide_thinking.setter
    def hide_thinking(self, value: bool) -> None:
        turning_on = value and not self._hide_thinking
        self._hide_thinking = value
        if turning_on:
            # Ctrl+T is most naturally pressed WHILE reasoning is scrolling by —
            # that is the moment it becomes noise. Honour it immediately instead
            # of at block close: the window comes down now, and the block still
            # commits as the collapsed placeholder when it ends.
            self._clear_thinking_tail()

    def on_agent_event(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._reset_message_state()
        elif event.type == "message_update":
            self._on_stream_event(event.assistant_message_event)
        elif event.type == "message_end":
            # Terminal failures arrive here as a MessageEndEvent whose message
            # carries stop_reason ∈ {"error","aborted"} (loop.py:385-410).
            self._finalize_text()
            self._close_thinking()
            self._render_message_error(event.message)
        elif event.type == "turn_end":
            # ``_finalize_text`` FIRST so the partial streamed answer is committed
            # to scrollback before the abort notice — the notice has to read as
            # terminating the text above it, not as a header over nothing.
            self._finalize_text()
            # Same rule for reasoning, and the reason this branch is load-bearing:
            # abort emits ONLY turn_end (no message_end, and the cancelled adapter
            # never sends thinking_end), so without this the last painted frame of
            # reasoning stayed pinned above the prompt for the rest of the session.
            self._close_thinking()
            # ``getattr``, not ``event.message``: this branch never touched the
            # message before, and the renderer is a plain broadcast subscriber
            # that is fed bare ``SimpleNamespace(type="turn_end")`` stubs (e.g.
            # tests/tui/test_run_tui_smoke.py's refresh-generation tests). Reading
            # the attribute unconditionally turned those into an AttributeError
            # inside the subscriber — caught in review by the full suite.
            self._render_turn_abort(getattr(event, "message", None))
        elif event.type == "tool_execution_start":
            self._render_tool_start(event.tool_name, event.args)
        elif event.type == "tool_execution_end":
            self._render_tool_end(event.tool_name, event.result, event.is_error)
        # tool_execution_update / turn_start / agent_* / unknown → no-op.

    def finalize(self) -> None:
        """Close any open streamed-text OR reasoning window (e.g. turn error)."""

        self._finalize_text()
        self._close_thinking()

    # === streaming-layer dispatch ==========================================

    def _on_stream_event(self, sev: AssistantMessageEvent) -> None:
        if sev.type == "text_delta":
            if self._text_stream is None:
                # Render buffered reasoning ABOVE the answer it preceded.
                self._flush_thinking(self._thinking_accum)
                self._text_stream = self._new_stream()
            self._text_accum += sev.delta
            self._text_stream.update(self._text_accum)
        elif sev.type == "text_end":
            if sev.content:
                self._text_accum = sev.content
            self._finalize_text()
        elif sev.type == "thinking_delta":
            index = getattr(sev, "content_index", 0)
            if self._thinking_index is not None and index != self._thinking_index:
                # A new block started without an explicit end for the previous
                # one — close that one out before opening this.
                self._flush_thinking(self._thinking_accum, index=self._thinking_index)
            if index in self._thinking_done:
                # The wire can REOPEN an index this renderer already committed:
                # ``openai_completions`` allocates one thinking index per message
                # (openai_completions.py:1222-1223) and reasoning that resumes after
                # answer text replays on it, so the block was flushed by the
                # text_delta arm above and then continued. Retiring the index
                # permanently dropped that continuation on the floor.
                self._thinking_done.discard(index)
            self._thinking_index = index
            self._thinking_accum += sev.delta
            self._push_thinking_tail()
        elif sev.type == "thinking_end":
            self._flush_thinking(
                sev.content or self._thinking_accum,
                index=getattr(sev, "content_index", self._thinking_index),
            )
        elif sev.type in ("done", "end"):
            self._finalize_text()
        elif sev.type == "error":
            self._finalize_text()
            self._close_thinking()
            message = sev.error_message or f"request {sev.reason}"
            self._commit(Text(f"✖ {message}", style="bold red"))

    # === helpers ===========================================================

    def _card_line_width(self) -> int:
        """Cells a tool-card / diff line may occupy at the current width.

        Four cells are reserved: two for the ``│ `` gutter each row is built with
        below, and two of slack so a cell-width measurement never lands exactly
        on the terminal edge.

        The floor is the HISTORICAL 76, and that is the important part. Cards and
        the approval dialog overflow differently, so the same rule does not apply
        to both:

        * the dialog is a prompt-toolkit float with ``wrap_lines=False`` — a row
          wider than the screen is CLIPPED, losing content silently, so its width
          must never exceed the terminal;
        * a card row is committed as a bare Rich ``Text`` to the ADAPTIVE
          scrollback console, which WRAPS it. Exceeding the terminal costs a
          second screen row; it loses nothing.

        Deriving this cap from the terminal in both directions therefore made
        narrow terminals strictly worse than before. Measured on a 60-column
        terminal with a 63-cell tool-output line: at the old fixed 76 the line
        survived and the console wrapped it, while ``60 - 4 = 56`` cut it to
        ``...T…`` — output DELETED that main displayed. Only widen.
        """

        return max(76, self._width_of() - 4)

    def _new_stream(self) -> StreamRenderer:
        return StreamRenderer(
            commit=lambda ansi: self._commit(Text.from_ansi(ansi)),
            set_tail=self._set_tail,
            width=self._width_of(),
        )

    def _reset_message_state(self) -> None:
        self._finalize_text()
        # Belt and braces: every terminal path already closes the reasoning
        # window, but a message that starts with one still painted would inherit
        # the previous turn's frame, and that is the one failure mode a user
        # cannot tell from a hang.
        self._clear_thinking_tail()
        self._text_accum = ""
        self._thinking_accum = ""
        self._thinking_index = None
        # Per MESSAGE, not per session: ``content_index`` restarts at 0 in every
        # message (anthropic.py:848, _google_shared.py:965,
        # _openai_responses_shared.py:725, openai_completions.py:1248), so
        # keeping the set would retire index 0 for the whole session. The delta
        # arm un-retires a reused index on its own, so what this line actually
        # covers is the block that arrives as ``thinking_end`` ALONE, with no
        # deltas to trigger that: without it the first turn shows its reasoning
        # and every turn after it shows none.
        self._thinking_done = set()
        self._thinking_tail_when = None
        self._thinking_min_delay = _THINKING_TAIL_MIN_DELAY
        # A new message has not been reported on yet. This is the ONLY caller of
        # _reset_message_state (on_agent_event's ``message_start`` branch), so the
        # flag can never survive into a message it did not come from.
        self._outcome_reported = False

    def _finalize_text(self) -> None:
        if self._text_stream is not None:
            self._text_stream.update(self._text_accum, final=True)
            self._text_stream = None
            self._text_accum = ""

    def _render_message_error(self, message: object) -> None:
        if isinstance(message, AssistantMessage) and message.stop_reason in (
            "error",
            "aborted",
        ):
            detail = message.error_message or f"request {message.stop_reason}"
            self._commit(Text(f"✖ {detail}", style="bold red"))
            self._outcome_reported = True

    def _render_turn_abort(self, message: object) -> None:
        """Issue #133 item 2 — a user interrupt must leave a trace.

        pi prints "Operation aborted" from its ``message_end`` handler
        (interactive-mode.ts:2752-2757) because pi's abort is signal-based: the
        stream returns a message with stopReason "aborted" and ``message_end``
        fires normally on the way out (agent-loop.ts:195-199). Aelix aborts by
        CANCELLING the turn task, and the close-out (harness/core.py:4541-4557)
        deliberately emits ONLY ``turn_end`` + ``agent_end`` — no
        ``message_start``/``message_end`` pair, so that abort stays off the
        session write path. ``_render_message_error`` is message_end-only and
        therefore never ran: the Working row simply vanished and nothing at all
        reached scrollback.

        ``stop_reason == "aborted"`` on ``turn_end`` is produced by that close-out
        and, today, by nothing else — the harness never threads ``signal`` into
        ``agent_loop`` (core.py:4497-4503, loop.py:107), so no adapter can raise
        it. Normal turns carry "end_turn"/"tool_use"; provider failures carry
        "error" and already print via ``_render_message_error``. Cancelling the
        retry COUNTDOWN goes through ``abort_retry`` and prints its own line;
        the TUI routes an interrupt during the retry REQUEST to
        ``harness.abort()`` instead, though that abort does not currently take
        effect (see the retry note below).

        The dedupe guard is not decoration. ``AssistantErrorEvent`` is typed
        ``reason: Literal["aborted", "error"]``, so an adapter that starts
        honouring a signal WOULD emit message_end(aborted) immediately followed
        by turn_end(aborted) for that same message — and both sites would print.

        The guard is a BOOL that is cleared at both ends of the only window it
        has to cover. An earlier version keyed on the message OBJECT and claimed
        that "cannot go stale"; that was wrong in both directions, and both
        failures are measured, not theoretical:

        * Identity goes stale on REPLACEMENT. loop.py:401-410 identity-swaps the
          message when a message_end handler returns a replacement, so
          ``_render_message_error`` sees the ORIGINAL while turn_end carries the
          REPLACEMENT — ``is`` misses and the notice prints twice.
        * A bool cleared ONLY at message_start goes stale on the RETRY path.
          Measured by calling ``AgentHarness.abort()`` on a real harness while an
          auto-retry attempt was in flight, the emitted sequence is
          message_end("error") -> turn_end("error") -> auto_retry_start ->
          turn_start -> turn_end("aborted") with NO message_start in between, so
          a flag left set by the failed attempt would SUPPRESS the notice.
          Scope, honestly stated: this was reached through the programmatic
          ``abort()`` API. From the TUI keyboard it is currently NOT reachable,
          because interrupting an in-flight auto-retry does not abort at all
          today (Ctrl+C x2 and Esc all measured as no-ops against a hanging
          retry, on this build AND on the parent commit — a separate pre-existing
          gap, not something this change introduces or fixes). So this half of
          the guard is defensive for the keyboard path and load-bearing for the
          programmatic one.

        Hence: cleared on message_start, and CONSUMED here. The window it guards
        is exactly the adjacent message_end -> turn_end pair, which is the only
        shape that can double-print.

        Yellow, not the bold red every genuine failure uses: the whole point of
        the line is that the user can tell "I stopped it" from "it crashed".
        """

        # Read-and-clear BEFORE the stop_reason check: the turn is over either
        # way, so a flag set by this turn must never outlive it.
        already_reported = self._outcome_reported
        self._outcome_reported = False
        if getattr(message, "stop_reason", None) != "aborted":
            return
        if already_reported:
            return
        self._commit(Text("✖ Operation aborted", style="yellow"))

    def _thinking_tail_source(self, width: int) -> str:
        """The suffix of the accumulator that can still reach the visible window.

        Rendering the WHOLE accumulator to keep 12 lines is quadratic: cost per
        frame grows with the block while the frames keep coming at the throttle
        floor, so a 150KB chain of thought spent tens of seconds inside
        ``plain_lines`` — on the prompt-toolkit loop, since the harness calls the
        subscriber synchronously (harness/core.py:2008). At 50KB, 98% of the
        rendered lines were discarded unread.

        Rich wraps each newline-separated line independently, so a slice that
        begins at a line boundary renders byte-identically to the full text from
        that boundary on. Hence: take a suffix generous enough to fill the window
        at this width even if every character lands on its own row, then
        resynchronise to the first boundary inside its first half — which leaves
        at least the other half, still more than the window needs. Only a slice
        with no boundary at all in that half (one unbroken mega-paragraph) can
        differ from the full render, and only in where the earliest visible row
        happens to break.
        """

        text = self._thinking_accum
        keep = max(1, width) * (_THINKING_LIVE_WINDOW + _THINKING_TAIL_LINE_SLACK)
        if len(text) <= keep * 2:
            return text
        window = text[-keep * 2 :]
        boundary = window.find("\n")
        if 0 <= boundary < keep:
            window = window[boundary + 1 :]
        return window

    def _push_thinking_tail(self) -> None:
        """Show the reasoning so far in the live tail (issue #170).

        Throttled, and skipped entirely when ``hide_thinking`` is on — the point
        of that setting is to NOT show reasoning, so streaming it and then
        collapsing it would be the loudest possible way to honour it.
        """

        if self.hide_thinking or not self._thinking_accum.strip():
            return
        if self._text_stream is not None:
            # The answer owns the live window once it starts streaming, and both
            # write the same last-writer-wins sink (shell.py:3075). A provider
            # that resumes reasoning after answer text — openai-completions
            # replays it on the same content_index — would otherwise flip the
            # window between the answer being typed and a reasoning fragment.
            # The reasoning is not lost: it still commits when the block closes.
            return
        now = self._time()
        # ``None`` = nothing shown for this block yet. The FIRST push is never
        # throttled: it is the "reasoning has started" moment, the single most
        # useful frame, and on a fresh block the user is otherwise looking at an
        # empty spinner. (Initialising this to 0.0 instead hid the first frame
        # whenever the clock started near zero — real ``monotonic()`` is large
        # enough to mask it, an injected test clock is not.)
        if self._thinking_tail_when is not None and (
            now - self._thinking_tail_when < self._thinking_min_delay
        ):
            return
        self._thinking_tail_when = now
        width = self._width_of()
        started = self._time()
        lines = plain_lines(
            self._thinking_tail_source(width), width, style="dim italic"
        )
        render_time = self._time() - started
        # The governor ``StreamRenderer`` has had since 6h₂₄ (stream.py:155-158):
        # hold the gap at ten times the render it just paid for. With the slice
        # above this should never leave the floor — which is the point. It is the
        # backstop that keeps ANY future renderer that is not O(1) in block size
        # from eating the turn, rather than a second copy of the same fix.
        self._thinking_min_delay = min(
            max(render_time * 10, _THINKING_TAIL_MIN_DELAY), _THINKING_TAIL_MAX_DELAY
        )
        self._set_tail("".join(lines[-_THINKING_LIVE_WINDOW:]))
        self._thinking_tail_shown = True

    def _clear_thinking_tail(self) -> None:
        """Take the live reasoning window down, if one is up.

        Gated on "a tail was painted", never on the CURRENT ``hide_thinking``
        value: the tail was written under whatever the flag was when the deltas
        arrived, and the user can flip it mid-block.
        """

        if not self._thinking_tail_shown:
            return
        self._thinking_tail_shown = False
        self._set_tail("")

    def _close_thinking(self) -> None:
        """End-of-turn close-out: commit whatever reasoning streamed, drop the tail.

        Called from every path that ends a turn without a ``thinking_end`` —
        abort, provider error, message error. Reasoning that was on screen lands
        in scrollback, matching what ``_finalize_text`` already does one line
        earlier for a partial ANSWER; the alternative (blink it out of existence
        the moment the user hits Esc) would make the two halves of the same turn
        disagree about whether partial output survives.
        """

        if self._thinking_index is None and not self._thinking_tail_shown:
            return
        self._flush_thinking(self._thinking_accum)

    def _flush_thinking(self, content: str, *, index: int | None = None) -> None:
        """Close out a thinking block: clear its tail, commit it once.

        ``index`` is the block's ``content_index``. Committing is idempotent PER
        BLOCK rather than per message: the adapter emits ``thinking_end`` at
        end-of-stream, after the answer has already streamed, so the same block
        arrives twice and must not print twice — but a genuinely different block
        (think → tool → think) must still print. The previous boolean latch
        could not tell those apart and dropped the second one (#169).
        """

        self._finalize_text()
        text = content.strip()
        self._thinking_accum = ""
        if index is None:
            index = self._thinking_index
        self._thinking_index = None
        self._thinking_tail_when = None  # the next block starts unthrottled
        self._thinking_min_delay = _THINKING_TAIL_MIN_DELAY
        self._clear_thinking_tail()  # the committed block replaces the live window
        if index is not None and index in self._thinking_done:
            # The late ``thinking_end`` for a block already printed.
            return
        if index is not None:
            self._thinking_done.add(index)
        if not text:
            return
        if self.hide_thinking:
            # Collapsed: a one-line placeholder; stash the full reasoning in the
            # /expand store so it stays recoverable (``/expand N``).
            n = self._store_expandable(text)
            self._commit(
                Text(f"💭 {self._hidden_thinking_label} (/expand {n})", style="dim italic")
            )
        else:
            self._commit(Text(text, style="dim italic"))

    def _render_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        # Reasoning that preceded a tool call renders above its card too.
        self._flush_thinking(self._thinking_accum)
        self._finalize_text()
        summary = _tool_header(tool_name, args)
        self._commit(render_tool_call_line(tool_name, summary))

    def _render_tool_end(self, tool_name: str, result: Any, is_error: bool) -> None:
        self._finalize_text()
        text = _result_text(result).rstrip()
        # §B — a stored tool-renderer-desc for this tool_name renders a custom view
        # (table/grid/form/text) instead of the default Text dump. The default
        # rendering is unchanged whenever no descriptor matches (or the lookup /
        # build raises — a faulty renderer must not swallow tool output). A
        # matched descriptor keeps full precedence: no truncation is applied.
        if self._render_with_descriptor(tool_name, text):
            return
        exit_code = _bash_exit_code(result) if tool_name == "bash" else None
        if not text:
            return
        # §C′ (ADR-0138) — the edit tool now returns a SUCCESS MESSAGE as content
        # and its diff in ``details`` (Pi parity). Surface the colorized diff from
        # details: pi's diff is a line-numbered +/- format (NOT a ``@@`` unified
        # diff), so _looks_like_diff would miss it, but _render_diff colours by
        # the +/- prefix and renders it correctly.
        if not is_error and tool_name == "edit":
            diff_text = getattr(getattr(result, "details", None), "diff", "")
            if isinstance(diff_text, str) and diff_text.strip():
                cap = self._card_line_width()
                _, diff_hidden = _truncate_lines(diff_text, max_lines=40, max_line_width=cap)
                expand_id = self._store_expandable(diff_text) if diff_hidden > 0 else None
                diff_group = _render_diff(diff_text, expand_id=expand_id, max_line_width=cap)
                self._commit(Group(Text(text, style="green"), *diff_group.renderables))
                return
        # §C (ADR-0116) — diff-shaped tool output (edit/write difflib, or a
        # bash `git diff`) renders with +/- colour instead of flat dim text.
        # Errors keep the red card below (a failed edit isn't a diff to review).
        if not is_error and _looks_like_diff(text):
            cap = self._card_line_width()
            _, diff_hidden = _truncate_lines(text, max_lines=40, max_line_width=cap)
            expand_id = self._store_expandable(text) if diff_hidden > 0 else None
            diff_group = _render_diff(text, expand_id=expand_id, max_line_width=cap)
            if exit_code is not None and exit_code != 0:
                # Preserve the bash exit footer for diff-shaped output that
                # still reports a non-zero exit (e.g. `git diff --exit-code`).
                self._commit(Group(*diff_group.renderables, Text(f"exit {exit_code}", style="red")))
            else:
                self._commit(diff_group)
            return
        # §A — truncated, styled card under the ● header: a dim left-gutter block
        # (red when is_error), with a "+N more lines" footer when truncated and an
        # "exit N" footer for non-zero/failed bash. One committed renderable.
        # Error output is head-truncated too, but a Python traceback's diagnostic
        # tail (the exception type/message) lives at the bottom — so give errors a
        # higher cap to keep that visible (full detail still via a future /expand).
        kept, hidden = _truncate_lines(
            text,
            max_lines=40 if is_error else self.tool_card_max_lines,
            max_line_width=self._card_line_width(),
        )
        body_style = "red" if is_error else "dim"
        rows: list[Text] = [Text(f"│ {line}", style=body_style) for line in kept]
        if hidden > 0:
            expand_id = self._store_expandable(text)
            rows.append(Text(f"│ … (+{hidden} more lines · /expand {expand_id})", style="dim"))
        if exit_code is not None and exit_code != 0:
            rows.append(Text(f"│ exit {exit_code}", style="red"))
        self._commit(Group(*rows))

    def _store_expandable(self, full_text: str) -> int:
        """Retain ``full_text`` under a fresh id; return the id (for ``/expand``).

        Bounded to ``_expand_max`` entries — the oldest id is dropped when the
        store is full so a long session can't grow it without limit.
        """

        self._expand_seq += 1
        n = self._expand_seq
        self._expand_store[n] = full_text
        if len(self._expand_store) > self._expand_max:
            oldest = min(self._expand_store)
            del self._expand_store[oldest]
        return n

    def get_expanded(self, n: int) -> str | None:
        """Return the full, untruncated body stored for ``/expand N`` (or None)."""

        return self._expand_store.get(n)

    def reset_expand_store(self) -> None:
        """Drop all ``/expand`` ids + thinking flush state (W-review 6h₁₅ MEDIUM).

        The :class:`EventRenderer` is long-lived and reused across a session
        swap (``/new`` / ``/resume``), but ``/expand`` ids are scoped to the
        visible transcript — without this, after a swap ``/expand N`` would still
        return the PREVIOUS session's body (a cross-session leak, now widened by
        collapsed thinking landing in the same store). Called from ``run_tui``'s
        rebind seam so it fires on every swap.
        """

        self._expand_store.clear()
        self._expand_seq = 0
        self._thinking_done = set()

    def replay(self, messages: list[Any]) -> None:
        """Re-render a loaded session's static messages into scrollback.

        pi ``renderCurrentSessionState`` parity (Sprint 6h₁₄b, ADR-0122): used by
        ``/resume`` after a session hot-swap, and (issue #165) by ``run_tui``'s
        startup paint. Reuses the live helpers (``_tool_header``,
        ``_render_tool_end``, ``markdown_lines``); truncated tool-result cards
        are stored too, so ``/expand`` works on them.

        It does NOT look identical to a freshly-streamed transcript, and this
        docstring said it did until issue #164. What still differs, and why each
        is left open rather than forgotten:

        * a **toolResult** carries no ``details`` on the wire
          (``messages.py``'s ``ToolResultMessage`` has none), so an edit's diff,
          a bash ``exit N`` footer and the green edit-success style cannot be
          recovered here — closing them needs a kernel-side field, not a render
          change;
        * ``tool_name`` is empty on a persisted toolResult, so a descriptor's
          custom card is silently skipped;
        * headers and cards do not interleave — replay walks one assistant
          message's ``●`` headers, then the toolResult cards;
        * a ``UserMessage`` records no steer/follow-up kind, so every echo is
          ``» ``; an image-only user turn renders nothing at all;
        (One entry used to sit here and no longer does: TWO thinking blocks in
        one message replayed BOTH while the live path showed only the first.
        That was the live side losing content, and #170 fixed it there — the
        idempotency latch is now keyed per ``content_index``, so the two agree
        by both being right rather than by replay being matched down.)

        Static (no streaming) — never opens a text-stream window. Each message:
        user → ``» {text}``; assistant → thinking (dim italic) + text + ``●``
        tool-call headers + a terminal-error line; toolResult → the result card.
        """

        self._finalize_text()  # belt-and-braces: no open stream during replay
        # Resolved ONCE for the whole replay: ``_width_of`` does a live ioctl
        # per call (issue #166), so reading it per block would lay block 3 out
        # at a different measure than block 1 if the terminal were resized
        # mid-repaint.
        replay_width = self._width_of()
        for msg in messages:
            role = getattr(msg, "role", None)
            if role == "user":
                text = _join_text(getattr(msg, "content", []))
                if text.strip():
                    # Aelix-original: collapse the compaction-summary message to a
                    # one-line marker when the display gate is on (the summary
                    # stays in context; only its transcript render is suppressed).
                    from aelix_agent_core.session.context import (
                        COMPACTION_SUMMARY_PREFIX,
                    )

                    if self.hide_compaction_summary and text.startswith(COMPACTION_SUMMARY_PREFIX):
                        self._commit(
                            Text(
                                "⋯ (context compacted — summary hidden)",
                                style="dim",
                            )
                        )
                    else:
                        # Sprint 6h₂₅ (ADR-0153) — shared user-echo vocabulary so a
                        # replayed transcript echoes input identically to a live turn.
                        self._commit(render_user_message(text))
            elif role == "assistant":
                for block in getattr(msg, "content", []) or []:
                    btype = getattr(block, "type", None)
                    if btype == "thinking":
                        thinking = (getattr(block, "thinking", "") or "").strip()
                        if thinking:
                            # Issue #164 — the live path gates reasoning on
                            # ``hide_thinking`` (``_flush_thinking``); replay
                            # dumped it unconditionally, so a user with "Show
                            # thinking" OFF got every past turn's reasoning back
                            # on every resume. The two arms are inlined rather
                            # than delegated: ``_flush_thinking`` also drives the
                            # LIVE tail and its per-block idempotency set, and a
                            # persisted block carries no ``content_index`` to key
                            # that on — so replay renders the block directly
                            # instead of borrowing streaming machinery it has no
                            # inputs for.
                            if self.hide_thinking:
                                hidden_id = self._store_expandable(thinking)
                                self._commit(
                                    Text(
                                        f"💭 {self._hidden_thinking_label} "
                                        f"(/expand {hidden_id})",
                                        style="dim italic",
                                    )
                                )
                            else:
                                self._commit(Text(thinking, style="dim italic"))
                    elif btype == "text":
                        body = getattr(block, "text", "") or ""
                        # Issue #164 — render Markdown the way the live path
                        # does. Committing ``Text(body)`` put the raw SOURCE on
                        # screen: literal ``**bold**``, literal backticks, no
                        # bullets, no syntax highlighting.
                        #
                        # ``Text.from_ansi`` and not a bare ``Markdown``
                        # renderable: that is what the live commit sink builds
                        # (``_new_stream``), so replay commits the same TYPE.
                        # The guard is on the RENDERED lines, not on
                        # ``body.strip()`` — an HTML comment or a link-reference
                        # definition is non-blank and renders to nothing, and
                        # the live path commits nothing for it.
                        rendered = markdown_lines(body, replay_width)
                        if rendered:
                            self._commit(Text.from_ansi("".join(rendered)))
                    elif btype == "toolCall":
                        name = getattr(block, "tool_name", "") or ""
                        summary = _tool_header(name, getattr(block, "input", {}) or {})
                        self._commit(render_tool_call_line(name, summary))
                stop = getattr(msg, "stop_reason", None)
                if stop in ("error", "aborted"):
                    detail = getattr(msg, "error_message", None) or f"request {stop}"
                    self._commit(Text(f"✖ {detail}", style="bold red"))
            elif role == "toolResult":
                # _render_tool_end reads result.content / .is_error and applies
                # the same truncation + /expand-store as a live tool card.
                self._render_tool_end(
                    getattr(msg, "tool_name", "") or "",
                    msg,
                    bool(getattr(msg, "is_error", False)),
                )
            elif role == "custom":
                # Issue #62 (ADR-0183) — DISPLAY-tier custom message (rich
                # ``CustomMessage`` from build_display_messages). The display
                # gate fires BEFORE any renderer lookup (pi
                # interactive-mode.ts:3109-3116): display=False stays in the
                # LLM context but never renders.
                if getattr(msg, "display", False):
                    self._render_custom(msg)

    def _render_custom(self, msg: Any) -> None:
        """Render one custom message: extension hook, else default.

        Pi ``CustomMessageComponent.rebuild()`` parity
        (``custom-message.ts:58-97``): a hook failure or ``None`` falls
        through SILENTLY to the default rendering — a bold ``[custom_type]``
        label + the plain content text (pi draws a themed box + markdown;
        Aelix scrollback is plain-text-first, divergence noted in ADR-0183).
        """

        hook = self.render_custom_message
        if hook is not None:
            try:
                renderable = hook(msg)
                if renderable is not None:
                    self._commit(renderable)
                    return
            except Exception:  # noqa: BLE001 — pi swallows renderer errors and
                # falls back (custom-message.ts:68-70). Aelix keeps the silent
                # FALLBACK (a bad renderer must not break replay) but, per the
                # "every skip/failure logs" convention (ADR-0181), records it at
                # DEBUG so a plugin dev can diagnose — no per-message warning spam.
                logger.debug(
                    "custom-message renderer failed for %r; using default rendering",
                    getattr(msg, "custom_type", None),
                    exc_info=True,
                )
        content = getattr(msg, "content", None)
        text = content if isinstance(content, str) else _join_text(content or [])
        label = Text(f"[{getattr(msg, 'custom_type', '') or 'custom'}]", style="bold magenta")
        if text.strip():
            self._commit(Group(label, Text(text)))
        else:
            self._commit(label)

    def _render_with_descriptor(self, tool_name: str, text: str) -> bool:
        lookup = self.get_tool_renderer_desc
        renderer = self.descriptor_renderer
        if lookup is None or renderer is None or not tool_name:
            return False
        try:
            envelope = lookup(tool_name)
            if envelope is None:
                return False
            rows = renderer.project_tool_result(envelope, text)
            self._commit(renderer.build_tool_renderable(envelope, rows))
        except Exception:  # noqa: BLE001 — fall back to default on any failure
            return False
        return True


__all__ = [
    "EventRenderer",
    "component_to_text",
    "render_tool_call_line",
    "render_user_message",
    "_tool_header",
    "_truncate_lines",
]
