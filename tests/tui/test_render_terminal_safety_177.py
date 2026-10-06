"""#177 — what ``tui/render.py`` writes cannot steer the terminal.

Every row asserts on the BYTE STREAM a ``rich`` ``Console(force_terminal=True)``
produces, never on ``Text.plain``: ``.plain`` hands back the string that went in
and proves nothing about the emulator, and ``rich`` itself strips only BEL, BS,
VT, FF and CR — ESC, the one-byte C1 CSI (0x9B) and the BiDi overrides pass it.

MEASURED on aab1f210 before the fix (``.omc/probes/177-live/impl/``): every
hostile-input row below leaked — a model-authored ``read`` path carrying an
ST-terminated OSC 52 (a clipboard write) and ``ESC [ 2 J`` reached the output
bytes intact, and so did a tool result, a reasoning block, an error line and
every replayed copy of them.

The verdict helper removes ``rich``'s OWN styling (``ESC [ <digits;> m``) and
then requires that nothing steering is left. That is stricter than looking for
the payload: a leak of any other sequence fails it too.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.types import (
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantErrorEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
)
from aelix_ai.tools import ToolResult
from aelix_coding_agent.tui import render
from aelix_coding_agent.tui.render import EventRenderer
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

OSC52 = "\x1b]52;c;cHduZWQ=\x1b\\"  # ST-terminated: rich passes it whole
CLEAR = "\x1b[2J"
TITLE = "\x1b]0;PWNED\x07"
C1_CSI = "\x9b2J"
RLO = "‮"
HOSTILE = f"x\r{TITLE}{OSC52}{CLEAR}{C1_CSI}{RLO}\nNEXT"

_RICH_SGR = re.compile(r"\x1b\[[0-9;]*m")
_STEERING = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\u2028\u2029"
    "\u200e\u200f\u061c\u200b\ufeff]"
)


def _bytes(renderable: Any, width: int = 120) -> str:
    buf = io.StringIO()
    Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        height=40,
        legacy_windows=False,
    ).print(renderable)
    return buf.getvalue()


def _assert_inert(out: str, where: str) -> None:
    assert "\x1b]52" not in out, f"{where}: OSC 52 reached the terminal: {out!r}"
    assert CLEAR not in out, f"{where}: ESC[2J reached the terminal: {out!r}"
    rest = _RICH_SGR.sub("", out)
    found = sorted({repr(m.group()) for m in _STEERING.finditer(rest)})
    assert not found, f"{where}: steering characters reached the terminal: {found} in {out!r}"


def _renderer(width: int = 100) -> tuple[EventRenderer, list[Any], list[str]]:
    commits: list[Any] = []
    tails: list[str] = []
    return EventRenderer(commit=commits.append, set_tail=tails.append, width=width), commits, tails


def _update(event: Any) -> MessageUpdateEvent:
    return MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=event)


# === tool headers ===========================================================


def test_the_issue_s_own_measurement_is_one_inert_row() -> None:
    """The exact call #177 was filed with returned its input unchanged."""

    header = render._tool_header("read", {"path": "x\r\x1b]0;PWNED\x07\nNEXT"})
    assert not any(ch in header for ch in "\x1b\r\n\x07"), repr(header)
    # Each steering character became a SPACE — the words stay apart, and the
    # sequence that was there is visible as its inert literal.
    assert header == "x  ]0;PWNED  NEXT", repr(header)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read", {"path": HOSTILE}),
        ("read", {"path": HOSTILE, "offset": 3, "limit": 4}),
        ("read", {"path": HOSTILE, "offset": "abc"}),
        ("write", {"path": HOSTILE}),
        ("edit", {"path": HOSTILE}),
        ("bash", {"command": HOSTILE}),
        ("grep", {HOSTILE: 1}),  # a KEY: values are repr-ed, keys were not
        ("grep", {"pattern": HOSTILE}),
    ],
    ids=["read", "read-range", "read-bad-range", "write", "edit", "bash", "args-key", "args-value"],
)
def test_a_hostile_tool_argument_reaches_the_header_bytes_inert(
    tool: str, args: dict[str, Any]
) -> None:
    summary = render._tool_header(tool, args)
    assert not _STEERING.search(summary) and "\x1b" not in summary, repr(summary)
    _assert_inert(_bytes(render.render_tool_call_line(tool, summary)), f"{tool} header")


def test_the_shared_header_boundary_strips_what_it_is_handed() -> None:
    """``render_tool_call_line`` is what live AND replay draw through, so it is
    safe on its own, whatever the caller did first — a hostile summary and a
    hostile tool NAME (a call to a tool that does not exist still draws one)."""

    _assert_inert(_bytes(render.render_tool_call_line("read", HOSTILE)), "summary")
    line = render.render_tool_call_line(HOSTILE * 20, "")
    _assert_inert(_bytes(line), "tool name")
    # The name is capped like the summary is: a flood is one row, not a page.
    assert cell_len(line.plain) <= 2 + render._HEADER_MAX_CELLS, cell_len(line.plain)


def test_the_live_tool_start_header_is_inert() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(
        ToolExecutionStartEvent(tool_call_id="c1", tool_name=HOSTILE, args={"path": HOSTILE})
    )
    assert commits
    for c in commits:
        _assert_inert(_bytes(c), "live tool start")


def test_a_flooding_path_is_capped_from_the_front_and_keeps_its_file_name() -> None:
    """A model-authored 55 KB "path" was ~700 header rows with no escape in it."""

    # The name has no ``.py`` on purpose: ``scripts/check_citations.py`` reads
    # ``<name>.py:<line>`` in any tracked file as a citation, and the header this
    # builds would be one it cannot resolve.
    flood = "a/" * 27_500 + "target_file.rs"
    header = render._tool_header("read", {"path": flood, "offset": 1, "limit": 9})
    assert header.endswith("target_file.rs:1-10"), header[-40:]
    assert header.startswith("…"), header[:10]
    assert cell_len(header) <= render._HEADER_PATH_MAX_CELLS + len(":1-10"), cell_len(header)


def test_a_real_path_is_untouched() -> None:
    path = "packages/aelix-coding-agent/src/aelix_coding_agent/tui/한글_파일.py"
    assert render._tool_header("read", {"path": path}) == path
    assert render._tool_header("read", {"path": path, "offset": 10, "limit": 5}) == f"{path}:10-15"
    assert render._tool_header("bash", {"command": "ls -la\ngit status"}) == "ls -la git status"


# === tool results ===========================================================


def _end(r: EventRenderer, tool: str, result: Any, *, is_error: bool = False) -> None:
    r.on_agent_event(
        ToolExecutionEndEvent(tool_call_id="c1", tool_name=tool, result=result, is_error=is_error)
    )


_MANY = "\n".join(f"line {i} {HOSTILE}" for i in range(30))


@pytest.mark.parametrize(
    ("tool", "result", "is_error"),
    [
        ("bash", ToolResult(content=[TextContent(text=HOSTILE)]), False),
        ("bash", ToolResult(content=[TextContent(text=HOSTILE)]), True),
        ("bash", ToolResult(content=[TextContent(text=_MANY)]), False),
        (
            "bash",
            ToolResult(content=[TextContent(text=f"@@ -1,2 +1,2 @@\n-{HOSTILE}\n+{HOSTILE}")]),
            False,
        ),
        (
            "edit",
            ToolResult(
                content=[TextContent(text=f"Edited {HOSTILE}")],
                details=SimpleNamespace(diff=f"+1 {HOSTILE}\n-2 {HOSTILE}"),
            ),
            False,
        ),
    ],
    ids=["card", "error-card", "truncated-card", "unified-diff", "edit-message-and-diff"],
)
def test_a_hostile_tool_result_reaches_the_card_bytes_inert(
    tool: str, result: Any, is_error: bool
) -> None:
    r, commits, _t = _renderer()
    _end(r, tool, result, is_error=is_error)
    assert commits
    for c in commits:
        _assert_inert(_bytes(c), f"{tool} result")


def test_expand_prints_the_stored_body_inert() -> None:
    """``/expand N`` prints the stored body in a Panel with no renderer of its own
    in between, so storing the raw text would reopen every card this closes."""

    from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command

    r, _commits, _t = _renderer()
    _end(r, "bash", ToolResult(content=[TextContent(text=_MANY)]))
    assert r.get_expanded(1) is not None
    committed: list[object] = []
    ctx = CommandContext(
        chrome=object(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/work",
        commands=list(BUILTIN_COMMANDS),
        expand_lookup=r.get_expanded,
    )
    command = match_command("/expand", ctx.commands)
    assert command is not None and command.handler is not None
    asyncio.run(command.handler(ctx, "1"))  # type: ignore[arg-type]
    assert committed
    out = _bytes(committed[0])
    _assert_inert(out, "/expand")
    assert "line 29" in out  # still the FULL body


def test_truncate_lines_measures_after_it_strips() -> None:
    """Pure helper, called by the card and the diff: kept lines are safe, the
    hidden count is the same, and the cell cap counts what is drawn."""

    kept, hidden = render._truncate_lines(
        f"a{TITLE}b\n{OSC52}c{CLEAR}\nthird", max_lines=2, max_line_width=76
    )
    assert kept == ["ab", "c"], kept
    assert hidden == 1
    wide, _h = render._truncate_lines(
        "\x1b[31m" + "x" * 70 + "\x1b[0m", max_lines=1, max_line_width=76
    )
    assert wide == ["x" * 70], wide  # 70 cells fit; the escapes are not counted against it


def test_a_long_hostile_edit_diff_is_stored_inert_for_expand() -> None:
    """The edit tool's diff quotes the FILE. A diff past the 40-line cap is
    stored for ``/expand`` whole — so it has to be safe before it is stored,
    not only where the card cuts it."""

    diff = "\n".join(f"+{i} {HOSTILE}" for i in range(60))
    r, commits, _t = _renderer()
    _end(
        r,
        "edit",
        ToolResult(content=[TextContent(text="Edited f.py")], details=SimpleNamespace(diff=diff)),
    )
    for c in commits:
        _assert_inert(_bytes(c), "edit diff card")
    stored = r.get_expanded(1)
    assert stored is not None and "+59" in stored
    _assert_inert(_bytes(Text(stored)), "stored edit diff")


def test_a_stray_osc_introducer_cannot_swallow_the_lines_after_it() -> None:
    """An OSC body may not cross a newline. With pi's lazy ``[\\s\\S]*?`` an
    ``ESC ]`` on one line and a BEL three lines later delete everything between —
    lines the user then never sees, and a ``+N more`` count that is wrong."""

    kept, hidden = render._truncate_lines(
        "a\x1b]0;x\nsecond\nthird\x07d", max_lines=5, max_line_width=76
    )
    assert hidden == 0
    assert kept[1:] == ["second", "thirdd"], kept
    assert render._safe_tool_output("a\x1b]0;x\nsecond\nthird\x07d").count("\n") == 2


def test_coloured_tool_output_loses_its_codes_not_its_words() -> None:
    """Colour is ordinary in a tool result (pytest, ``ls --color``). Stripping only
    the ESC would leave ``[31mFAILED[0m`` on every line; the whole sequence goes
    (pi's ``getTextOutput`` rule) and the word stays."""

    r, commits, _t = _renderer()
    _end(
        r,
        "bash",
        ToolResult(content=[TextContent(text="\x1b[31mFAILED\x1b[0m test_x\n\x1b[1;32mok\x1b[m")]),
    )
    out = _bytes(commits[0])
    _assert_inert(out, "coloured output")
    # The tool's SGR LOOKS like rich's own, so the inert check alone cannot see
    # it. The card must be byte-identical to the card of the uncoloured text.
    r2, plain_commits, _t2 = _renderer()
    _end(r2, "bash", ToolResult(content=[TextContent(text="FAILED test_x\nok")]))
    assert out == _bytes(plain_commits[0]), (out, _bytes(plain_commits[0]))
    assert "[31m" not in _RICH_SGR.sub("", out)


def test_a_coloured_git_diff_is_now_recognised_as_a_diff() -> None:
    """Its hunk header began with ``ESC [ 36 m`` until the strip ran first, so
    ``_looks_like_diff`` (anchored at ``^@@``) missed it and it drew as a dim card."""

    coloured = "\x1b[36m@@ -1,2 +1,2 @@\x1b[m\n\x1b[31m-old\x1b[m\n\x1b[32m+new\x1b[m"
    r, commits, _t = _renderer()
    _end(r, "bash", ToolResult(content=[TextContent(text=coloured)]))
    styles = [str(row.style) for row in commits[0].renderables]
    assert styles == ["cyan", "red", "green"], styles


def test_legitimate_tool_output_still_renders() -> None:
    """Wide glyphs, a ZWJ emoji, tab indentation and line structure survive to
    the bytes. (U+200D is deliberately not a steering character.)"""

    body = "한글 출력 👩‍💻\n\tindented\nlast"
    r, commits, _t = _renderer()
    _end(r, "bash", ToolResult(content=[TextContent(text=body)]))
    out = _bytes(commits[0])
    _assert_inert(out, "legit output")
    plain = _RICH_SGR.sub("", out)
    assert "│ 한글 출력 👩‍💻" in plain, plain
    # rich expanded the tab to its stop (column 8, past the 2-cell gutter):
    # indentation survives as spaces and no raw TAB reaches the terminal.
    assert re.search(r"^│ {3,}indented$", plain, re.MULTILINE), plain
    assert "\t" not in out
    assert plain.count("│ ") == 3, plain


# === error lines ============================================================


def test_a_hostile_stream_error_is_inert() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(_update(AssistantErrorEvent(reason="error", error_message=HOSTILE)))
    assert commits
    _assert_inert(_bytes(commits[-1]), "stream error")


def test_a_hostile_message_error_is_inert_and_dedup_still_sees_the_raw_text() -> None:
    """The ✖ line is sanitised for the glass; ``take_reported_error`` keeps the raw
    string because the shell compares it byte for byte with ``str(exc)`` (#189)."""

    r, commits, _t = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(
        MessageEndEvent(message=AssistantMessage(stop_reason="error", error_message=HOSTILE))
    )
    assert commits
    _assert_inert(_bytes(commits[-1]), "message_end error")
    assert r.take_reported_error() == HOSTILE


def test_a_body_sized_error_is_bounded() -> None:
    """A provider's whole response body (#186's 55 KB page) is 8 lines, not a page."""

    r, commits, _t = _renderer()
    r.on_agent_event(
        MessageEndEvent(
            message=AssistantMessage(stop_reason="error", error_message="<p>x</p>\n" * 5000)
        )
    )
    plain = commits[-1].plain
    assert plain.count("\n") <= 9, plain.count("\n")


# === reasoning and answer ===================================================


def test_hostile_reasoning_is_inert_live_and_committed() -> None:
    r, commits, tails = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(ThinkingDeltaEvent(delta=HOSTILE, content_index=0)))
    assert tails and tails[-1]
    for tail in tails:
        _assert_inert(tail, "reasoning tail")
    r.on_agent_event(_update(ThinkingEndEvent(content_index=0, content="")))
    assert commits
    for c in commits:
        _assert_inert(_bytes(c), "reasoning block")


def test_a_thinking_end_that_carries_its_own_copy_is_inert() -> None:
    """``thinking_end`` can bring the block's full text with it, and that copy is
    what commits — benign deltas say nothing about it."""

    r, commits, _t = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(ThinkingDeltaEvent(delta="benign", content_index=0)))
    r.on_agent_event(_update(ThinkingEndEvent(content_index=0, content=HOSTILE)))
    assert commits
    for c in commits:
        _assert_inert(_bytes(c), "thinking_end content")


def test_a_hostile_answer_is_inert_live_and_committed() -> None:
    r, commits, tails = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(TextDeltaEvent(delta=f"hello {HOSTILE}\n\nmore")))
    assert tails and tails[-1]
    for tail in tails:
        _assert_inert(tail, "answer tail")
    r.on_agent_event(_update(TextEndEvent(content="")))
    assert commits
    for c in commits:
        _assert_inert(_bytes(c), "answer commit")


def test_a_text_end_that_carries_its_own_copy_is_inert() -> None:
    r, commits, _t = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(TextDeltaEvent(delta="benign")))
    r.on_agent_event(_update(TextEndEvent(content=f"benign {HOSTILE}")))
    assert commits
    for c in commits:
        _assert_inert(_bytes(c), "text_end content")


def test_a_sequence_split_across_deltas_strips_exactly_like_the_whole_text() -> None:
    """Per-delta stripping must equal stripping the finished text, or the stream's
    committed prefix disagrees with its final frame. Character-wise stripping
    commutes with concatenation; a sequence-aware one would not."""

    deltas = ["before \x1b", "[2J mid \x1b]52;c;", "cHduZWQ=\x1b", "\\ after"]
    joined = "".join(deltas)
    assert "".join(render._safe_prose(d) for d in deltas) == render._safe_prose(joined)


def test_streamed_deltas_commit_what_the_joined_text_commits() -> None:
    """Through the RENDERER, not the helper: a sequence split across deltas must
    end up as the same committed bytes as the same text in one delta."""

    deltas = ["before \x1b", "[2J mid \x1b]52;c;", "cHduZWQ=\x1b", "\\ after"]

    def run(parts: list[str]) -> list[str]:
        r, commits, _t = _renderer()
        r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
        for part in parts:
            r.on_agent_event(_update(TextDeltaEvent(delta=part)))
        r.on_agent_event(_update(TextEndEvent(content="")))
        return [_bytes(c) for c in commits]

    split, whole = run(deltas), run(["".join(deltas)])
    assert split == whole, (split, whole)
    for out in split:
        _assert_inert(out, "split deltas")


# === replay =================================================================


def test_a_hostile_session_replays_inert() -> None:
    """``/resume`` and the startup paint redraw ALL of it from a session file —
    which can arrive with a repository rather than be written by this user."""

    r, commits, _t = _renderer()
    r.replay(
        [
            UserMessage(content=[TextContent(text=f"pasted {HOSTILE}")]),
            AssistantMessage(
                content=[
                    ThinkingContent(thinking=HOSTILE),
                    TextContent(text=f"answer {HOSTILE}"),
                    ToolCallContent(tool_call_id="t1", tool_name=HOSTILE, input={"path": HOSTILE}),
                    ToolCallContent(tool_call_id="t2", tool_name="read", input={"path": HOSTILE}),
                ],
                stop_reason="error",
                error_message=HOSTILE,
            ),
            ToolResultMessage(tool_call_id="t2", content=[TextContent(text=HOSTILE)]),
            SimpleNamespace(role="custom", display=True, custom_type=HOSTILE, content=HOSTILE),
        ]
    )
    assert len(commits) >= 7, commits
    for i, c in enumerate(commits):
        _assert_inert(_bytes(c), f"replay commit {i}")


def test_replay_still_drops_the_echoed_failure_body() -> None:
    """#194's exact-match rule compares the RAW body, before any strip — a hostile
    failure still replays as ONE line, not two."""

    r, commits, _t = _renderer()
    r.replay(
        [
            AssistantMessage(
                content=[TextContent(text=f"[error] {HOSTILE}")],
                stop_reason="error",
                error_message=HOSTILE,
            )
        ]
    )
    assert len(commits) == 1, [type(c).__name__ for c in commits]
    _assert_inert(_bytes(commits[0]), "replayed failure")


def test_the_user_echo_drops_bidi_overrides() -> None:
    """The echo already mapped C0/C1 to spaces; BiDi, U+2028 and zero-width were
    outside that map — and the echo replays from the session file."""

    out = _bytes(render.render_user_message(f"trusted={RLO}eslaf ok​!", width=60))
    _assert_inert(out, "user echo")
    assert "​" not in out


# === extension surfaces =====================================================


def test_default_custom_message_rendering_is_inert() -> None:
    r, commits, _t = _renderer()
    r.replay(
        [
            SimpleNamespace(
                role="custom",
                display=True,
                custom_type=HOSTILE,
                content=[{"type": "text", "text": HOSTILE}],
            )
        ]
    )
    assert commits
    _assert_inert(_bytes(commits[0]), "custom message")


def test_a_component_keeps_its_colour_and_loses_its_steering() -> None:
    """A component's lines are ANSI BY CONTRACT: SGR is read as style. What
    ``from_ansi`` leaves behind (C1 CSI, BiDi) becomes a space in place, so the
    red span still covers exactly the word that was red."""

    component = SimpleNamespace(render=lambda _w: [f"\x1b[31mred\x1b[0m {C1_CSI}{RLO} tail"])
    text = render.component_to_text(component, 80)
    _assert_inert(_bytes(text), "component")
    assert [(s.start, s.end) for s in text.spans] == [(0, 3)], text.spans
    assert text.plain[0:3] == "red", repr(text.plain)
    assert "\x1b[31mred" in _bytes(text), "the SGR colour must still reach the terminal"
    assert text.plain.endswith(" tail"), repr(text.plain)


def test_render_text_is_not_trusted_by_plain_alone() -> None:
    """The instrument's own control: ``Text.plain`` of a raw Text still carries
    the payload, and the byte stream of a raw Text leaks — so the assertions
    above are measuring the renderer, not rich."""

    raw = Text(HOSTILE)
    assert "\x1b]52" in raw.plain
    with pytest.raises(AssertionError):
        _assert_inert(_bytes(raw), "control")


# === review round 1 =========================================================
#
# Each row below was RED, or let a named wrong implementation through, on
# 33b6c43f (``.omc/probes/177-live/fix2/``).

#: Every one-byte C1 introducer a terminal acts on, not only CSI. DCS opens a
#: device-control string, OSC an operating-system command (the clipboard), ST
#: ends either. A strip table holding only 0x9B passed every row above.
C1_TABLE = {
    "DCS-0x90": "\x90q#1;2;3\x9c",
    "CSI-0x9b": "\x9b2J",
    "ST-0x9c": "\x9c",
    "OSC-0x9d": "\x9d52;c;cHduZWQ=\x9c",
    # NEL moves the cursor to the next line in a terminal that acts on C1, so
    # a "header row" holding one draws on two (review round 2).
    "NEL-0x85": "\x85",
}


def _c1_surface(surface: str, seq: str) -> list[str]:
    """The console bytes ``surface`` draws for a string carrying ``seq``."""

    payload = f"a {seq} b"
    r, commits, tails = _renderer()
    if surface == "header":
        return [
            _bytes(
                render.render_tool_call_line("read", render._tool_header("read", {"path": payload}))
            )
        ]
    if surface == "tool-name":
        return [_bytes(render.render_tool_call_line(payload, ""))]
    if surface == "tool-output":
        _end(r, "bash", ToolResult(content=[TextContent(text=payload)]))
    elif surface == "answer":
        r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
        r.on_agent_event(_update(TextDeltaEvent(delta=payload)))
        r.on_agent_event(_update(TextEndEvent(content="")))
    elif surface == "reasoning":
        r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
        r.on_agent_event(_update(ThinkingDeltaEvent(delta=payload, content_index=0)))
        r.on_agent_event(_update(ThinkingEndEvent(content_index=0, content="")))
    elif surface == "error":
        r.on_agent_event(_update(AssistantErrorEvent(reason="error", error_message=payload)))
    elif surface == "replay-result":
        r.replay([ToolResultMessage(tool_call_id="t", content=[TextContent(text=payload)])])
    elif surface == "custom":
        r.replay([SimpleNamespace(role="custom", display=True, custom_type="c", content=payload)])
    elif surface == "component":
        return [_bytes(render.component_to_text(SimpleNamespace(render=lambda _w: [payload]), 80))]
    elif surface == "echo":
        return [_bytes(render.render_user_message(payload, width=60))]
    return [*tails, *(_bytes(c) for c in commits)]


@pytest.mark.parametrize("seq", list(C1_TABLE.values()), ids=list(C1_TABLE))
@pytest.mark.parametrize(
    "surface",
    [
        "header",
        "tool-name",
        "tool-output",
        "answer",
        "reasoning",
        "error",
        "replay-result",
        "custom",
        "component",
        "echo",
    ],
)
def test_every_one_byte_c1_introducer_is_inert(surface: str, seq: str) -> None:
    outs = _c1_surface(surface, seq)
    assert outs and any(out.strip() for out in outs), outs
    for out in outs:
        assert seq[0] not in out, f"{surface}: raw {seq[0]!r} reached the terminal: {out!r}"
        _assert_inert(out, surface)


def test_a_one_byte_csi_goes_whole_from_tool_output() -> None:
    """The sequence pass knows 0x9B as well as ``ESC [``: the whole CSI goes,
    not only its introducer — no ``2J`` is left on the line."""

    assert render._safe_tool_output("a\x9b2Jb") == "ab"
    r, commits, _t = _renderer()
    _end(r, "bash", ToolResult(content=[TextContent(text="before \x9b2J after")]))
    out = _RICH_SGR.sub("", _bytes(commits[0]))
    assert "before  after" in out and "2J" not in out, out


# --- OSC 8 targets in a component ---------------------------------------------


def _link_line(url: str) -> str:
    return f"\x1b]8;;{url}\x1b\\CLICK\x1b]8;;\x1b\\ \x1b[31mred\x1b[0m"


@pytest.mark.parametrize(
    "url",
    [
        "https://e.x/\x07\x1b]52;c;cHduZWQ=\x07\x1b[2J",
        "https://e.x/\x9d52;c;cHduZWQ=\x9c",
        f"https://e.x/{RLO}txt.exe",
        "https://e.x/‏a",
        "javascript:alert(1)",
        "x-handler://run",
        "https://e.x/a\tb",
    ],
    ids=["bel-osc52", "c1-osc", "rlo", "rlm", "javascript", "custom-scheme", "tab"],
)
def test_an_unclean_component_link_is_dropped_and_its_text_kept(url: str) -> None:
    """``Text.from_ansi`` reads an OSC 8 target into ``Style.link`` and rich
    writes it back VERBATIM: a BEL in it closed the OSC 8 early and the OSC 52
    after it was live (33b6c43f). The link goes; the text and colour stay."""

    component = SimpleNamespace(render=lambda _w: [_link_line(url)])
    outs = [_bytes(render.component_to_text(component, 80))]
    # The same conversion on replay, through the custom-message hook.
    r, commits, _t = _renderer()
    r.render_custom_message = lambda _msg: render.component_to_text(component, 80)
    r.replay([SimpleNamespace(role="custom", display=True, custom_type="c", content="m")])
    outs += [_bytes(c) for c in commits]
    assert len(outs) == 2
    for out in outs:
        _assert_inert(out, "component link")
        assert "\x1b]8;" not in out, f"an unclean link was written back: {out!r}"
        assert "CLICK" in out and "\x1b[31mred" in out, out


@pytest.mark.parametrize(
    "url", ["https://example.com/docs?q=1#a", "HTTPS://EXAMPLE.COM/Docs"], ids=["lower", "upper"]
)
def test_a_clean_component_link_is_kept(url: str) -> None:
    """A scheme is case-insensitive (RFC 3986 3.1): ``HTTPS:`` is kept too."""

    out = _bytes(render.component_to_text(SimpleNamespace(render=lambda _w: [_link_line(url)]), 80))
    assert re.search(r"\x1b\]8;id=[^;]*;" + re.escape(url) + r"\x1b\\CLICK", out), repr(out)
    assert "\x1b[31mred" in out


def test_a_component_s_colour_stays_on_its_word_after_a_c1_byte() -> None:
    """The C1 byte BEFORE the coloured word becomes a space in place. Deleting it
    instead shifts ``.plain`` under the spans and the red lands on ``ed ``."""

    text = render.component_to_text(
        SimpleNamespace(render=lambda _w: [f"{C1_CSI}\x1b[31mred\x1b[0m tail"]), 80
    )
    out = _bytes(text)
    _assert_inert(out, "component")
    assert "\x1b[31mred\x1b[0m tail" in out, repr(out)


# --- descriptor views ----------------------------------------------------------


def _descriptor_outs(view: str, body: str, **payload: Any) -> list[str]:
    from aelix_agent_core.contracts import DescriptorEnvelope, ToolRendererDescPayload
    from aelix_coding_agent.tui.descriptors import DescriptorRenderer

    envelope = DescriptorEnvelope(
        kind="tool-renderer-desc",
        namespace="x",
        id="d",
        payload=ToolRendererDescPayload(tool_name="probe", view=view, **{"title": "T", **payload}),
    )
    outs: list[str] = []
    for mode in ("live", "replay"):
        r, commits, _t = _renderer()
        r.get_tool_renderer_desc = lambda _name: envelope
        r.descriptor_renderer = DescriptorRenderer  # type: ignore[assignment]
        if mode == "live":
            _end(r, "probe", ToolResult(content=[TextContent(text=body)]))
        else:
            r.replay(
                [
                    ToolResultMessage(
                        tool_call_id="t", tool_name="probe", content=[TextContent(text=body)]
                    )
                ]
            )
        assert len(commits) == 1, (mode, commits)
        outs.append(_bytes(commits[0]))
    return outs


#: JSON for ``ESC ] 52 … ESC \ ESC [ 2 J`` + RLO: inert in the BODY (which was
#: already made safe), live again once the descriptor renderer decodes it.
_JSON_HOSTILE = "\\u001b]52;c;cHduZWQ=\\u001b\\\\SEEN\\u001b[2J\\u202e"


@pytest.mark.parametrize(
    ("view", "body", "payload", "expect"),
    [
        ("text", f'{{"message": "{_JSON_HOSTILE}"}}', {"text_path": "message"}, "SEEN"),
        (
            "table",
            f'[{{"message": "{_JSON_HOSTILE}"}}]',
            {"columns": [{"header": "message", "key": "message"}]},
            "SEEN",
        ),
        ("form", f'[{{"lab{_JSON_HOSTILE}": "{_JSON_HOSTILE}"}}]', {}, "labSEEN"),
        ("grid", f'["{_JSON_HOSTILE}", "plain"]', {}, "SEEN"),
    ],
    ids=["text-path", "table", "form-key-and-value", "grid-of-strings"],
)
def test_a_descriptor_view_draws_decoded_values_inert(
    view: str, body: str, payload: dict[str, Any], expect: str
) -> None:
    for out in _descriptor_outs(view, body, **payload):
        _assert_inert(out, f"descriptor {view}")
        assert expect in out, out
        # The TOOL-OUTPUT shape, in every view: the sequences go whole. The
        # header-row shape leaves their inert literals (`` ]52;…`` and ``[2J``)
        # and the prose shape leaves them without the ESC; review round 3 found
        # that only the form row told the shapes apart.
        plain = _RICH_SGR.sub("", out)
        assert "]52" not in plain and "[2J" not in plain, plain


def test_the_descriptor_view_gets_the_body_as_it_arrived_and_draws_it_inert() -> None:
    """The view DECODES the body, so it is handed the body as the tool wrote
    it (rstripped, as before #177) and makes safe what it draws. No JSON: the
    ``text`` view draws that body itself, through the tool-output shape. The
    renderer the view is handed to records what it was given. Review round 3:
    handing it the cleaned body lost a key that held a literal U+200B
    (``test_a_descriptor_column_is_looked_up_by_the_key_it_was_given``)."""

    from aelix_coding_agent.tui.descriptors import DescriptorRenderer

    for out in _descriptor_outs("text", f"plain {HOSTILE}"):
        _assert_inert(out, "descriptor text body")
        assert "plain" in out

    handed: list[str] = []

    class _Recording:
        @staticmethod
        def project_tool_result(envelope: Any, result_text: str) -> Any:
            handed.append(result_text)
            return DescriptorRenderer.project_tool_result(envelope, result_text)

        build_tool_renderable = staticmethod(DescriptorRenderer.build_tool_renderable)

    envelope = SimpleNamespace(payload=SimpleNamespace(view="text", title="T", text_path=None))
    r, commits, _t = _renderer()
    r.get_tool_renderer_desc = lambda _name: envelope
    r.descriptor_renderer = _Recording  # type: ignore[assignment]
    _end(r, "probe", ToolResult(content=[TextContent(text=f"plain {HOSTILE}  \n")]))
    assert len(commits) == 1 and handed == [f"plain {HOSTILE}"], (commits, handed)
    _assert_inert(_bytes(commits[0]), "descriptor text body")


def test_a_grid_of_objects_still_draws_their_repr() -> None:
    """The grid draws a dict row as its ``repr``, which escapes the control
    characters itself; that row is left exactly as it was."""

    for out in _descriptor_outs("grid", f'[{{"message": "{_JSON_HOSTILE}"}}]'):
        _assert_inert(out, "descriptor grid")
        assert "'\\x1b]52;c;cHduZWQ=\\x1b\\\\SEEN\\x1b[2J\\u202e'" in out, out


# --- BiDi after Markdown decoding, and the marks -----------------------------


_FORMAT_MARKS = {"LRM": "‎", "RLM": "‏", "ALM": "؜"}


#: Every code point ``stream.markdown_lines`` removes after Markdown decoded it,
#: written out here rather than read from ``stream._FORMAT_CONTROLS``: a row
#: built from the table would agree with any table. Review round 2: with only
#: U+202E and U+2067 in the rows, a table that stopped one short of U+2069 (the
#: end of a ``range``) passed every test, and ``&#8297;`` drew a raw PDI.
_DECODED_CONTROLS = [
    *(0x202A, 0x202B, 0x202C, 0x202D, 0x202E),  # LRE RLE PDF LRO RLO
    *(0x2066, 0x2067, 0x2068, 0x2069),  # LRI RLI FSI PDI
    *(0x200E, 0x200F, 0x061C),  # LRM RLM ALM
    *(0x2028, 0x2029, 0x200B, 0xFEFF),  # LS PS ZWSP BOM
]
_ENTITIES = [f"&#{cp};" for cp in _DECODED_CONTROLS] + [f"&#x{cp:X};" for cp in _DECODED_CONTROLS]


@pytest.mark.parametrize("entity", _ENTITIES)
def test_an_entity_encoded_bidi_control_is_inert_after_markdown_decodes_it(entity: str) -> None:
    """The source holds only ASCII; rich's Markdown DECODES it into the control
    after every strip that ran on the source (33b6c43f: a raw U+202E in the
    live tail, the committed block and the replay). One row per code point, in
    its decimal and its hex spelling."""

    body = f"before {entity} after"
    r, commits, tails = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(TextDeltaEvent(delta=body)))
    r.on_agent_event(_update(TextEndEvent(content=body)))
    r2, replayed, _t = _renderer()
    r2.replay([AssistantMessage(content=[TextContent(text=body)])])
    outs = [*filter(None, tails), *(_bytes(c) for c in commits), *(_bytes(c) for c in replayed)]
    assert tails and commits and replayed
    for out in outs:
        _assert_inert(out, f"answer {entity}")
        assert "before" in out and "after" in out


@pytest.mark.parametrize("mark", list(_FORMAT_MARKS.values()), ids=list(_FORMAT_MARKS))
def test_a_raw_bidi_mark_is_inert_in_answer_reasoning_and_custom_text(mark: str) -> None:
    payload = f"total {mark}= 1"
    r, commits, tails = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(ThinkingDeltaEvent(delta=payload, content_index=0)))
    r.on_agent_event(_update(ThinkingEndEvent(content_index=0, content=payload)))
    r.on_agent_event(_update(TextDeltaEvent(delta=payload)))
    r.on_agent_event(_update(TextEndEvent(content=payload)))
    r2, replayed, _t = _renderer()
    r2.replay(
        [
            AssistantMessage(
                content=[ThinkingContent(thinking=payload), TextContent(text=payload)]
            ),
            SimpleNamespace(role="custom", display=True, custom_type="c", content=payload),
        ]
    )
    outs = [*tails, *(_bytes(c) for c in commits), *(_bytes(c) for c in replayed)]
    assert len(replayed) == 3, replayed
    for out in outs:
        _assert_inert(out, "bidi mark")
        assert mark not in out


def test_legitimate_right_to_left_and_composed_text_survives_prose() -> None:
    body = "مرحبا بالعالم שלום 한글 👩‍💻 café"
    r, commits, _t = _renderer()
    r.replay([AssistantMessage(content=[ThinkingContent(thinking=body), TextContent(text=body)])])
    for c in commits:
        assert body in _RICH_SGR.sub("", _bytes(c)), _bytes(c)


# --- the tab is layout in prose ----------------------------------------------


_FENCED_TAB = "```make\nall:\n\techo hi\n```\n"


def _indent_of(out: str, word: str) -> int:
    plain = _RICH_SGR.sub("", out)
    line = next(line for line in plain.splitlines() if word in line)
    return len(line) - len(line.lstrip(" "))


def test_a_tab_indented_fenced_block_keeps_its_indentation_in_every_prose_body() -> None:
    """A Makefile recipe is tab-indented, and a tab that vanished would put
    ``echo hi`` back at the column of ``all:``. Answer (live and replay),
    reasoning and a custom message body all keep it."""

    r, commits, _t = _renderer()
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(TextDeltaEvent(delta=_FENCED_TAB)))
    r.on_agent_event(_update(TextEndEvent(content="")))
    live = "".join(_bytes(c) for c in commits)
    r2, replayed, _t2 = _renderer()
    r2.replay([AssistantMessage(content=[TextContent(text=_FENCED_TAB)])])
    replay = "".join(_bytes(c) for c in replayed)
    for out in (live, replay):
        assert _indent_of(out, "echo hi") >= _indent_of(out, "all:") + 3, out

    r3, thought, _t3 = _renderer()
    r3.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r3.on_agent_event(_update(ThinkingDeltaEvent(delta="all:\n\techo hi", content_index=0)))
    r3.on_agent_event(_update(ThinkingEndEvent(content_index=0, content="")))
    r4, custom, _t4 = _renderer()
    r4.replay(
        [SimpleNamespace(role="custom", display=True, custom_type="c", content="all:\n\techo hi")]
    )
    for out in ("".join(_bytes(c) for c in thought), "".join(_bytes(c) for c in custom)):
        assert _indent_of(out, "echo hi") >= _indent_of(out, "all:") + 3, out


# --- /expand of collapsed reasoning --------------------------------------------


def test_collapsed_reasoning_is_stored_inert_for_expand() -> None:
    """With ``hide_thinking`` the block is one placeholder line and the WHOLE text
    goes to the ``/expand`` store, which prints it as it is."""

    r, commits, _t = _renderer()
    r.hide_thinking = True
    r.on_agent_event(MessageStartEvent(message=AssistantMessage()))
    r.on_agent_event(_update(ThinkingEndEvent(content_index=0, content=f"plan {HOSTILE}")))
    assert commits and "/expand 1" in commits[0].plain
    stored = r.get_expanded(1)
    assert stored is not None and "plan" in stored
    _assert_inert(_bytes(Text(stored)), "collapsed reasoning")


# --- caps and bounds -------------------------------------------------------------


def test_an_argument_summary_is_capped_after_it_is_stripped() -> None:
    """A control character is zero cells until it becomes a space: capping first
    lets 200 of them through and the row comes out 200 cells wide."""

    header = render._tool_header("grep", {"\x1b" * 200 + "k": 1})
    assert cell_len(header) <= render._HEADER_MAX_CELLS, cell_len(header)


def test_a_wide_flooding_path_is_capped_in_cells() -> None:
    flood = "가/" * 30_000 + "파일.rs"
    header = render._tool_header("read", {"path": flood})
    assert header.endswith("파일.rs") and header.startswith("…"), header[-20:]
    assert cell_len(header) <= render._HEADER_PATH_MAX_CELLS, cell_len(header)


@pytest.mark.parametrize("where", ["stream-error", "message-error", "replay"])
def test_an_error_line_is_bounded_on_every_path(where: str) -> None:
    """Eight kept lines of 200 characters and an ellipsis each, plus the
    omitted-lines note — on the stream ``error`` event too, not only on
    ``message_end``."""

    body = "\n".join("y" * 500 for _ in range(40))
    r, commits, _t = _renderer()
    if where == "stream-error":
        r.on_agent_event(_update(AssistantErrorEvent(reason="error", error_message=body)))
    elif where == "message-error":
        r.on_agent_event(
            MessageEndEvent(message=AssistantMessage(stop_reason="error", error_message=body))
        )
    else:
        r.replay([AssistantMessage(content=[], stop_reason="error", error_message=body)])
    lines = commits[-1].plain.split("\n")
    # Exactly what the CHANGELOG says: the first 8 lines, each cut to 200
    # characters followed by an ellipsis, then one note line for the rest.
    assert lines[0] == "✖ " + "y" * 200 + "…", lines[0]
    assert lines[1:8] == ["y" * 200 + "…"] * 7, [len(line) for line in lines]
    assert lines[8:] == ["… (32 more lines omitted)"], lines[8:]


# --- the user's own !cmd, live -----------------------------------------------


async def test_live_bang_output_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    """``!cmd`` output is bytes a process wrote. Its replay (the
    ``bash_execution`` custom message) was stripped; the live line was
    committed by ``tui/shell.py`` as ``Text(output)``, raw."""

    from aelix_coding_agent.tui import shell as tui_shell

    from tests.tui.test_run_tui_smoke import _harness_chrome, _launch, _quit_within, _wait

    async def _fake_bash(
        harness: Any, command: str, *, exclude_from_context: bool, cwd: str
    ) -> str:
        return f"\x1b[31mFAILED\x1b[0m {HOSTILE}\n\tindented\n"

    monkeypatch.setattr(tui_shell, "handle_user_bash", _fake_bash)
    captured: list[object] = []
    async with _harness_chrome() as (runtime, chrome, pipe):
        orig_many = chrome.print_above_many

        def _spy_many(renderables: object, *a: object, **k: object) -> object:
            captured.extend(list(renderables))  # type: ignore[call-overload]
            return orig_many(renderables, *a, **k)  # type: ignore[arg-type]

        chrome.print_above_many = _spy_many  # type: ignore[method-assign]
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("!cat evil\n")
        await _wait(lambda: any("FAILED" in getattr(c, "plain", "") for c in captured))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    lines = [c for c in captured if "FAILED" in getattr(c, "plain", "")]
    assert len(lines) == 1
    out = _bytes(lines[0])
    _assert_inert(out, "live !cmd output")
    plain = _RICH_SGR.sub("", out)
    assert "FAILED" in plain and "[31m" not in plain, plain
    assert re.search(r"^ {3,}indented", plain, re.MULTILINE), plain


# === review round 2 =========================================================
#
# Each row below was RED, or let a named wrong implementation through, on
# 70651cd7 (``.omc/probes/177-live/fix3/``).

_TITLE = f"T{OSC52}{RLO}TITLE"


@pytest.mark.parametrize(
    ("view", "body", "payload", "expect"),
    [
        ("text", "plain body", {"title": _TITLE}, "TITLE"),
        (
            "table",
            '[{"k": "v"}]',
            {"title": _TITLE, "columns": [{"key": "k", "header": "K"}]},
            "TITLE",
        ),
        ("form", '[{"k": "v"}]', {"title": _TITLE}, "TITLE"),
        ("grid", '["v"]', {"title": _TITLE}, "TITLE"),
        ("grid", "[]", {"title": _TITLE}, "TITLE"),
        (
            "table",
            '[{"k": "v"}]',
            {"columns": [{"key": "k", "header": f"H{OSC52}{CLEAR}HEAD"}]},
            "HEAD",
        ),
        ("table", '[{"k": "v"}]', {"columns": [{"key": f"k{OSC52}KEY"}]}, "KEY"),
    ],
    ids=[
        "text-title",
        "table-title",
        "form-title",
        "grid-title",
        "empty-grid-title",
        "column-header",
        "header-less-column-key",
    ],
)
def test_a_descriptor_s_own_title_and_headers_are_inert(
    view: str, body: str, payload: dict[str, Any], expect: str
) -> None:
    """The EXTENSION writes the title and the column headers, and they never
    passed through the renderer's strip: an OSC 52 in either reached the bytes
    on 70651cd7, live and on replay, in every view. They are one header row
    each, so a newline in a title would not open a second row either."""

    for out in _descriptor_outs(view, body, **payload):
        _assert_inert(out, f"descriptor {view} metadata")
        # rich draws a grid's title one character per row; read it whole.
        assert expect in re.sub(r"\s", "", _RICH_SGR.sub("", out)), out


def test_a_descriptor_column_header_stays_on_one_row() -> None:
    """A header row, not tool output: a newline the extension put in a column
    header is a space, as on every other header row."""

    columns = [{"key": "k", "header": "first\nsecond"}]
    for out in _descriptor_outs("table", '[{"k": "v"}]', columns=columns):
        assert "first second" in _RICH_SGR.sub("", out), out


def test_a_descriptor_table_row_that_is_a_string_is_inert() -> None:
    body = f'["{_JSON_HOSTILE}"]'
    for out in _descriptor_outs("table", body, columns=[{"key": "k", "header": "K"}]):
        _assert_inert(out, "descriptor table string row")
        assert "SEEN" in out, out


#: A JSON body spells a zero-width space either way: escaped (``\\u200b``,
#: ``json.dumps``'s default) or as the character itself (``ensure_ascii=False``).
_KEY_SPELLINGS = pytest.mark.parametrize("ensure_ascii", [True, False], ids=["escaped", "literal"])


@_KEY_SPELLINGS
def test_a_descriptor_column_is_looked_up_by_the_key_it_was_given(ensure_ascii: bool) -> None:
    """A JSON key may hold a zero-width space; the column names that exact key
    and shows a safe header. Cleaning the row's keys before the lookup (the
    first form of this change) blanked the cell, live and on replay. Review
    round 3: cleaning the BODY before it was decoded did the same to the
    literal spelling (d61473c9), while the escaped one passed."""

    body = json.dumps([{"part\u200bno": 42}], ensure_ascii=ensure_ascii)
    columns = [{"key": "part\u200bno", "header": "Part number"}]
    for out in _descriptor_outs("table", body, columns=columns):
        _assert_inert(out, "descriptor table lookup")
        plain = _RICH_SGR.sub("", out)
        assert "Part number" in plain and "42" in plain, plain


@_KEY_SPELLINGS
def test_two_form_fields_that_clean_alike_are_both_drawn(ensure_ascii: bool) -> None:
    """``FIELD<ZWSP>`` and ``FIELD`` are two fields. Rebuilding the row with
    cleaned keys made them one dict key, and FIRST was gone; so did cleaning a
    body that spelt the space literally, before ``json.loads`` (d61473c9)."""

    body = json.dumps([{"FIELD\u200b": "FIRST", "FIELD": "SECOND"}], ensure_ascii=ensure_ascii)
    for out in _descriptor_outs("form", body):
        _assert_inert(out, "descriptor form fields")
        plain = _RICH_SGR.sub("", out)
        assert "FIRST" in plain and "SECOND" in plain, plain


@pytest.mark.parametrize("mark", list(_FORMAT_MARKS.values()), ids=list(_FORMAT_MARKS))
def test_a_component_drops_the_bidi_marks_as_spaces(mark: str) -> None:
    """A component is extension prose, so it loses the marks prose loses — as a
    space, in place, so the colour stays on its word (70651cd7: each mark
    reached the bytes, live and on replay)."""

    component = SimpleNamespace(render=lambda _w: [f"{mark}\x1b[31mred\x1b[0m a{mark}b"])
    live = render.component_to_text(component, 80)
    r, commits, _t = _renderer()
    r.render_custom_message = lambda _msg: render.component_to_text(component, 80)
    r.replay([SimpleNamespace(role="custom", display=True, custom_type="c", content="m")])
    assert len(commits) == 1
    for out in (_bytes(live), _bytes(commits[0])):
        _assert_inert(out, "component mark")
        assert " \x1b[31mred\x1b[0m a b" in out, repr(out)


def test_replayed_collapsed_reasoning_is_stored_inert_for_expand() -> None:
    """The replay half of the ``/expand`` store: with ``hide_thinking`` a
    replayed block is one placeholder line and its text goes to the store."""

    r, commits, _t = _renderer()
    r.hide_thinking = True
    r.replay([AssistantMessage(content=[ThinkingContent(thinking=f"plan {HOSTILE}")])])
    assert len(commits) == 1 and "/expand 1" in commits[0].plain, commits
    stored = r.get_expanded(1)
    assert stored is not None and "plan" in stored
    _assert_inert(_bytes(Text(stored)), "replayed collapsed reasoning")


def test_a_replayed_bang_command_takes_its_live_line_s_shape() -> None:
    """The ``!cmd`` record replays in the tool-output shape its live line took:
    the colour codes go whole. As prose it came back as ``[31mFAILED``."""

    from aelix_coding_agent.cli.repl import BASH_EXECUTION_TYPE, bash_execution_to_text

    assert render._BASH_EXECUTION_TYPE == BASH_EXECUTION_TYPE
    output = f"\x1b[31mFAILED\x1b[0m {HOSTILE}\n\tindented\n"
    r, _c, _t = _renderer()
    live = _bytes(r.user_bash_output(output))
    r2, commits, _t2 = _renderer()
    r2.replay(
        [
            SimpleNamespace(
                role="custom",
                display=True,
                custom_type=BASH_EXECUTION_TYPE,
                content=bash_execution_to_text("cat evil", output),
            )
        ]
    )
    assert len(commits) == 1
    replay = _bytes(commits[0])
    for out in (live, replay):
        _assert_inert(out, "!cmd")
    plain = _RICH_SGR.sub("", replay)
    assert "[31m" not in plain, plain
    for line in _RICH_SGR.sub("", live).strip().splitlines():
        assert line.rstrip() in plain, (line, plain)


@pytest.mark.parametrize("entity", ["&#8232;", "&#x2029;"])
def test_a_decoded_line_separator_does_not_count_as_a_line(entity: str) -> None:
    """``markdown_lines`` drops the controls BEFORE it splits: ``splitlines``
    breaks on U+2028/2029, so dropping them per line afterwards counts one
    paragraph as two lines in the live tail."""

    from aelix_coding_agent.tui.stream import markdown_lines

    lines = markdown_lines(f"before {entity} after", 80)
    assert len(lines) == 1, lines


# === review round 3 =========================================================
#
# Each row below was RED on d61473c9, or let a wrong implementation that the
# round-3 reviews named pass (``.omc/probes/177-live/fix4/``).


def test_a_descriptor_title_stays_on_one_row() -> None:
    """The title is a header row too. Round 2's docstring said a title could
    not show the difference; it can, once the table is wider than the title
    (round 3's verification, ``title_shape``): in the tool-output shape a
    ``first`` + newline + ``second`` title drew on two rows."""

    columns = [{"key": "k", "header": "a-wide-column-header-that-is-long"}]
    title = "first\nsecond"
    for out in _descriptor_outs("table", '[{"k": "v"}]', title=title, columns=columns):
        assert "first second" in _RICH_SGR.sub("", out), out


@_KEY_SPELLINGS
@pytest.mark.parametrize(
    ("view", "payload", "body", "expect", "absent"),
    [
        (
            "table",
            {"rows_path": "a\u200bb.rows", "columns": [{"key": "k", "header": "K"}]},
            {"a\u200bb": {"rows": [{"k": "R1"}]}},
            "R1",
            "rows",
        ),
        ("text", {"text_path": "m\u200bsg"}, {"m\u200bsg": "BODYTEXT"}, "BODYTEXT", "sg"),
    ],
    ids=["rows-path", "text-path"],
)
def test_a_descriptor_path_is_looked_up_by_the_key_it_was_given(
    ensure_ascii: bool, view: str, payload: dict[str, Any], body: Any, expect: str, absent: str
) -> None:
    """``rows_path`` and ``text_path`` name keys too. With the body cleaned
    before it was decoded, a literal U+200B in the key missed, and the view
    fell back to drawing the whole body (d61473c9, live and on replay)."""

    for out in _descriptor_outs(view, json.dumps(body, ensure_ascii=ensure_ascii), **payload):
        _assert_inert(out, f"descriptor {view} path")
        plain = _RICH_SGR.sub("", out)
        assert expect in plain and absent not in plain, plain


@pytest.mark.parametrize("cp", _DECODED_CONTROLS, ids=[f"U+{cp:04X}" for cp in _DECODED_CONTROLS])
def test_a_component_link_whose_target_holds_a_format_control_is_dropped(cp: int) -> None:
    """Every code point of ``stream._FORMAT_CONTROLS``, written out. The shared
    helper KEEPS U+200E, U+200F and U+061C, so the guard's own table is what
    drops them; a guard that added only LRM and RLM to ``contains_steering_chars``
    passed every row while it wrote U+061C back in the target (Codex, round 3)."""

    url = f"https://e.x/a{chr(cp)}b"
    component = SimpleNamespace(render=lambda _w: [_link_line(url)])
    out = _bytes(render.component_to_text(component, 80))
    assert "\x1b]8;" not in out and chr(cp) not in out, repr(out)
    assert "CLICK" in out and "\x1b[31mred" in out, repr(out)


@pytest.mark.parametrize("mark", list(_FORMAT_MARKS.values()), ids=list(_FORMAT_MARKS))
def test_a_bang_command_keeps_the_marks_live_and_on_replay(mark: str) -> None:
    """Tool output keeps U+200E, U+200F and U+061C (right-to-left text a file
    holds), and so does the output of the user's own ``!cmd``: live and on
    replay alike, so the resumed line reads as the live one did."""

    from aelix_coding_agent.cli.repl import BASH_EXECUTION_TYPE, bash_execution_to_text

    output = f"a{mark}b\n"
    r, _c, _t = _renderer()
    live = _RICH_SGR.sub("", _bytes(r.user_bash_output(output)))
    r2, commits, _t2 = _renderer()
    r2.replay(
        [
            SimpleNamespace(
                role="custom",
                display=True,
                custom_type=BASH_EXECUTION_TYPE,
                content=bash_execution_to_text("cat f", output),
            )
        ]
    )
    assert len(commits) == 1
    replay = _RICH_SGR.sub("", _bytes(commits[0]))
    assert live.count(mark) == replay.count(mark) == 1, (live, replay)
    assert f"a{mark}b" in replay, replay
