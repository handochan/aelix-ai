"""#407: child-authored result text through Rich to the terminal byte stream.

Plain output isolates attacker controls; terminal output must keep Aelix's own
SGR while rejecting the child's. Each case also checks visible text so an empty
render cannot pass. The same file is run against the unchanged base as a
counterfactual before the fix (failure reports are captured, never printed raw).
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.utils.terminal_text import contains_steering_chars
from aelix_coding_agent.subagent_contract import SubagentResult
from aelix_coding_agent.tui.commands import (
    CommandContext,
    _agents_handler,
    _render_subagent_result,
    _sanitize_child_field,
    sanitize_for_terminal,
)
from rich.console import Console

_FIELDS = (
    "summary",
    "error",
    "profile",
    "declined_profile",
    "status",
    "permission_mode",
    "model",
    "provider",
    "dropped_tools",
)
_ATTACKS = {
    "osc52-bel": "\x1b]52;c;aGk=\x07",
    "osc52-st": "\x1b]52;c;aGk=\x1b\\",
    "osc8": "\x1b]8;;https://evil.test\x1b\\",
    "dcs": "\x1bPFAKE\x1b\\",
    "erase": "\x1b[2J",
    "sgr": "\x1b[48;5;123m",
    "c1-csi": "\x9b2J",
    "c1-osc": "\x9d52;c;aGk=\x9c",
    "c1-dcs": "\x90FAKE\x9c",
    "bidi": "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069",
    "bidi-marks": "\u200e\u200f\u061c",
    "controls": "".join(
        chr(i) for i in (*range(0x20), 0x7F, *range(0x80, 0xA0)) if i not in (0x09, 0x0A)
    ),
    "separators-zero-width": "\u2028\u2029\u200b\ufeff",
}
_OWN_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _result(field: str, text: str) -> SubagentResult:
    result = SubagentResult(
        id="sub-1",
        profile="scout",
        ok=True,
        status="ok",
        summary="Completed safely.",
        permission_mode="plan",
        model="model",
        provider="provider",
    )
    updates: dict[str, Any] = {field: text}
    if field == "declined_profile":
        updates = {"profile": text, "status": "declined", "ok": False}
    elif field == "dropped_tools":
        updates = {field: (text, "write")}
    return replace(result, **updates)


def _capture(result: SubagentResult, *, tty: bool) -> str:
    stream = io.StringIO()
    console = Console(
        file=stream,
        width=300,
        force_terminal=tty,
        no_color=not tty,
        color_system="standard" if tty else None,
    )
    for renderable in _render_subagent_result(result):
        console.print(renderable)
    return stream.getvalue()


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("attack", _ATTACKS)
@pytest.mark.parametrize("tty", [False, True], ids=["plain", "terminal"])
def test_child_text_cannot_steer_the_terminal(field: str, attack: str, tty: bool) -> None:
    payload = "VISIBLE" + _ATTACKS[attack]
    if attack == "bidi-marks":
        assert all(mark in payload for mark in ("\u200e", "\u200f", "\u061c"))
    else:
        assert contains_steering_chars(payload), "positive control: payload is hostile"

    raw = _capture(_result(field, payload), tty=tty)

    assert "VISIBLE" in raw, "positive control: the affected field was rendered"
    # This unique child SGR is never part of the renderer's own styling. The
    # plain case separately rejects every ESC, including any other injected SGR.
    assert "\x1b[48;5;123m" not in raw, "child SGR reached the terminal"
    content = _OWN_SGR.sub("", raw) if tty else raw
    assert not contains_steering_chars(content, keep_newline=True), (
        "child control or BiDi reached the terminal"
    )
    assert all(mark not in raw for mark in ("\u200e", "\u200f", "\u061c")), (
        "child BiDi mark reached the terminal"
    )


@pytest.mark.parametrize("field", _FIELDS)
def test_child_rich_markup_is_literal(field: str) -> None:
    raw = _capture(_result(field, "[reverse]VISIBLE[/reverse]"), tty=False)
    assert "[reverse]VISIBLE[/reverse]" in raw


def test_multiline_multilingual_output_and_own_styles_survive() -> None:
    summary = "파일 設定 é 👩‍💻 مرحبا שלום\n\n    indented code\n\tindented tab\nlast line"
    result = replace(
        _result("summary", summary),
        error="첫 번째 오류\n    remedy: retry",
        truncated=True,
    )
    plain = _capture(result, tty=False)
    for text in (
        "파일 設定 é 👩‍💻 مرحبا שלום",
        "    indented code",
        "        indented tab",
        "last line",
        "첫 번째 오류",
        "    remedy: retry",
        "output truncated",
    ):
        assert text in plain
    lines = plain.splitlines()
    first = next(i for i, line in enumerate(lines) if "파일 設定" in line)
    code = next(i for i, line in enumerate(lines) if "indented code" in line)
    assert code == first + 2, "the author's blank line survives"
    tty = _capture(result, tty=True)
    assert "\x1b[32m" in tty, "Aelix's green panel survives"
    assert "\x1b[31m" in tty, "Aelix's red error survives"
    assert "\x1b[1;36m" in tty, "Aelix's bold cyan grid labels survive"
    assert result.summary == summary, "sanitising display never rewrites the transport"


def test_sanitised_empty_summary_falls_back_and_grid_contracts_survive() -> None:
    raw = _capture(_result("summary", "\x1b\u202e"), tty=False)
    assert "(no output)" in raw
    assert _sanitize_child_field("model\n\tprovider") == "model provider"
    assert _sanitize_child_field("\u202e\x1b\x9b") == ""
    assert _sanitize_child_field("x" * 100) == "x" * 39 + "…"
    assert sanitize_for_terminal("path\u202e\x1b[2J\nname") == "path[2Jname"


def test_fields_that_are_not_displayed_stay_out_of_scrollback() -> None:
    hidden = "HIDDEN\x1b]52;c;aGk=\x07\u202e"
    result = replace(
        _result("summary", "VISIBLE"),
        id=hidden,
        stop_reason=hidden,
        tool_trail=hidden,
        details=hidden * 30,
    )
    raw = _capture(result, tty=False)
    assert "VISIBLE" in raw and "Full output:" in raw
    assert "HIDDEN" not in raw
    assert result.details == hidden * 30


@pytest.mark.parametrize("failure", ["resolve", "confirmed_resolve", "spawn"])
async def test_run_exception_text_cannot_steer_the_terminal(failure: str) -> None:
    # Resolve and spawn failures do not produce SubagentResult. They still cross
    # the same /agents run display boundary, including the second resolution
    # after an explicit project-profile confirmation.
    from aelix_coding_agent.subagent_contract import ProjectScopeRefused

    hostile = "VISIBLE\x1b]52;c;aGk=\x07\u202e\n    remedy"

    class Runtime:
        def resolve_profile(self, name: str, *, allow_project: bool = False) -> object:
            if failure == "resolve":
                raise ValueError(hostile)
            if failure == "confirmed_resolve":
                if not allow_project:
                    raise ProjectScopeRefused("project profile requires confirmation")
                raise ValueError(hostile)
            return object()

        async def spawn(self, resolved: object, task: str) -> SubagentResult:
            raise ValueError(hostile)

    async def select(*args: Any, **kwargs: Any) -> str:
        from aelix_coding_agent.cli.entry import PROJECT_AGENT_CONFIRM_OPTIONS

        return PROJECT_AGENT_CONFIRM_OPTIONS[0]

    committed: list[Any] = []
    ctx = CommandContext(
        chrome=SimpleNamespace(),  # type: ignore[arg-type]
        harness=SimpleNamespace(  # type: ignore[arg-type]
            runtime=SimpleNamespace(subagents=Runtime(), ui=SimpleNamespace(select=select))
        ),
        commit=committed.append,
        cwd="/repo",
    )
    await _agents_handler(ctx, "run scout task")
    stream = io.StringIO()
    console = Console(file=stream, width=300, no_color=True)
    for renderable in committed:
        console.print(renderable)
    raw = stream.getvalue()
    assert "VISIBLE" in raw and "agents run failed:" in raw
    assert "\n    remedy" in raw
    assert not contains_steering_chars(raw, keep_newline=True)
