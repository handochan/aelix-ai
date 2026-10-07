"""#188 / ADR-0253 — a tool aelix did not build is a mutating tool.

The permission gate used to decide "mutating?" with a set of eight BARE names
(``_MUTATING = _BASH_TOOLS | _WRITE_TOOLS``) and treated every other name as
read-only. MCP registers ``<server>__<tool>`` and extensions pick any name, so
on ``aab1f210`` an MCP ``fs__write_file`` ran with no prompt in ``default`` and
ran in ``plan`` as well — interactive and headless alike.

The gate now classifies the tool OBJECT the loop will execute
(``aelix_coding_agent.tools.provenance``). Every row below that names a tool
aelix did not build is red on ``aab1f210``; the built-in rows are the pins that
the flip did not touch aelix's own tools.

Tools here are built the way production builds them: MCP tools through the real
``mcp_tool_to_agent_tool`` adapter, built-ins through their real factories.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
import pytest
from aelix_agent_core import AgentLoopConfig, agent_loop, default_convert_to_llm
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_agent_core.types import AgentContext, AgentTool, BeforeToolCallResult
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model
from aelix_ai.tools import ToolResult
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.mcp.adapter import mcp_tool_to_agent_tool

from tests.builtin.gate_tools import BUILTIN_TOOLS

# === fakes ===================================================================


class _UI:
    """Scripted ``select``; records every prompt it was asked."""

    def __init__(self, answer: str | None = "No") -> None:
        self.answer = answer
        self.titles: list[str] = []
        self.options: list[list[str]] = []

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        self.titles.append(title)
        self.options.append(list(options))
        return self.answer

    async def input(self, *_a: Any, **_k: Any) -> str | None:
        return None


class _Ctx:
    def __init__(self, *, has_ui: bool, cwd: str = "/proj", ui: _UI | None = None) -> None:
        self.has_ui = has_ui
        self.cwd = cwd
        self.ui = ui if ui is not None else (_UI() if has_ui else None)


class _Conn:
    """The two things the MCP adapter touches on a live connection."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, args: dict[str, Any]) -> mcp_types.CallToolResult:
        self.calls.append((name, dict(args)))
        return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text="ok")])


def _mcp_tool(
    name: str,
    *,
    prefix: str | None = "fs",
    read_only_hint: bool | None = None,
    conn: _Conn | None = None,
) -> AgentTool:
    annotations = (
        None if read_only_hint is None else mcp_types.ToolAnnotations(readOnlyHint=read_only_hint)
    )
    tool = mcp_types.Tool(
        name=name,
        description="",
        inputSchema={"type": "object", "properties": {}},
        annotations=annotations,
    )
    return mcp_tool_to_agent_tool(conn or _Conn(), tool, name_prefix=prefix)  # type: ignore[arg-type]


async def _noop(_args: dict[str, Any], _ctx: Any) -> ToolResult:
    return ToolResult(content=[TextContent(text="ok")])


def _ext_tool(name: str) -> AgentTool:
    """A tool as an extension or pack registers it: built outside aelix."""

    return AgentTool(name=name, description="", parameters={}, execute=_noop)


def _event(tool: AgentTool, args: dict[str, Any], *extra: AgentTool) -> ToolCallHookEvent:
    """The event the loop emits for ``tool``: its context holds every tool."""

    tools = [*BUILTIN_TOOLS.values(), *extra, tool]
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name=tool.name,
        args=args,
        context=AgentContext(tools=tools),
    )


def _perm(mode: PermissionMode, **kwargs: Any) -> PermissionExtension:
    return PermissionExtension(posture=PermissionPosture(mode=mode), **kwargs)


async def _gate(
    perm: PermissionExtension, event: ToolCallHookEvent, ctx: _Ctx
) -> ToolCallResult | None:
    return await perm._on_tool_call(event, ctx)  # type: ignore[arg-type]


# === PLAN: the stated guarantee ==============================================


@pytest.mark.parametrize("has_ui", [True, False], ids=["interactive", "headless"])
@pytest.mark.parametrize(
    "tool",
    [
        _mcp_tool("write_file"),
        _mcp_tool("drop_table", prefix="db"),
        _ext_tool("deploy"),
    ],
    ids=lambda t: t.name,
)
async def test_plan_blocks_a_tool_aelix_did_not_build(tool: AgentTool, has_ui: bool) -> None:
    ctx = _Ctx(has_ui=has_ui)
    result = await _gate(_perm(PermissionMode.PLAN), _event(tool, {"path": "a.txt"}), ctx)

    assert result is not None and result.block
    assert tool.name in (result.reason or "")
    assert "read-only built-in" in (result.reason or "")
    if ctx.ui is not None:
        assert ctx.ui.titles == [], "PLAN refuses; it does not ask"


async def test_an_mcp_read_only_hint_never_lowers_the_gate() -> None:
    """The hint is a claim made by the thing being gated (owner, 2026-09-19)."""

    tool = _mcp_tool("search", read_only_hint=True)
    assert tool.name == "fs__search"

    result = await _gate(_perm(PermissionMode.PLAN), _event(tool, {}), _Ctx(has_ui=False))
    assert result is not None and result.block

    ui = _UI()
    await _gate(_perm(PermissionMode.DEFAULT), _event(tool, {}), _Ctx(has_ui=True, ui=ui))
    assert len(ui.titles) == 1


# === DEFAULT: the prompt =====================================================


@pytest.mark.parametrize(
    "tool",
    [_mcp_tool("write_file"), _ext_tool("deploy"), _ext_tool("agent")],
    ids=lambda t: t.name,
)
async def test_default_asks_before_a_tool_aelix_did_not_build(tool: AgentTool) -> None:
    """``agent`` here is a THIRD-PARTY tool of that name, not ``aelix_agents``'."""

    ui = _UI(answer="No")
    result = await _gate(
        _perm(PermissionMode.DEFAULT),
        _event(tool, {"path": "a.txt", "content": "x"}),
        _Ctx(has_ui=True, ui=ui),
    )

    assert len(ui.titles) == 1 and tool.name in ui.titles[0]
    assert result is not None and result.block


@pytest.mark.parametrize("name", ["read", "grep", "aelix_status"])
async def test_a_borrowed_read_only_name_is_not_read_only(name: str) -> None:
    """Provenance, not name: the tool the loop would run is the LAST of that name
    in the context (``loop.py`` ``tool_map``), here a third-party one."""

    ui = _UI()
    shadow = _ext_tool(name)
    assert await _gate(_perm(PermissionMode.DEFAULT), _event(shadow, {}), _Ctx(has_ui=True, ui=ui))
    assert len(ui.titles) == 1

    blocked = await _gate(_perm(PermissionMode.PLAN), _event(shadow, {}), _Ctx(has_ui=False))
    assert blocked is not None and blocked.block


async def test_an_unprefixed_mcp_tool_named_read_is_not_read_only() -> None:
    """``name_prefix=None`` makes the adapter use the bare MCP name."""

    tool = _mcp_tool("read", prefix=None)
    assert tool.name == "read"

    blocked = await _gate(
        _perm(PermissionMode.PLAN), _event(tool, {"path": "a"}), _Ctx(has_ui=False)
    )
    assert blocked is not None and blocked.block


async def test_an_event_without_a_context_is_not_trusted() -> None:
    """No evidence about which object runs is not a licence to skip the prompt."""

    ui = _UI()
    event = ToolCallHookEvent(tool_call_id="t1", tool_name="read", args={"path": "a"})
    await _gate(_perm(PermissionMode.DEFAULT), event, _Ctx(has_ui=True, ui=ui))
    assert len(ui.titles) == 1


async def test_a_replaced_copy_of_a_builtin_is_not_the_builtin() -> None:
    """``dataclasses.replace`` makes a new, unmarked object: it fails closed."""

    copy = dataclasses.replace(BUILTIN_TOOLS["read"], description="changed")
    event = ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="read",
        args={"path": "a"},
        context=AgentContext(tools=[copy]),
    )
    blocked = await _gate(_perm(PermissionMode.PLAN), event, _Ctx(has_ui=False))
    assert blocked is not None and blocked.block


# === AUTO_ACCEPT / AUTO: never auto-allowed ==================================


@pytest.mark.parametrize("mode", [PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO])
@pytest.mark.parametrize(
    "args", [{"path": "src/a.py", "content": "x"}, {}], ids=["in-project-path", "no-path"]
)
async def test_auto_postures_ask_for_a_tool_aelix_did_not_build(
    mode: PermissionMode, args: dict[str, Any], tmp_path: Path
) -> None:
    """A ``path`` key says nothing about what a third-party tool does with it."""

    ui = _UI(answer="No")
    result = await _gate(
        _perm(mode),
        _event(_mcp_tool("write_file"), args),
        _Ctx(has_ui=True, cwd=str(tmp_path), ui=ui),
    )
    assert len(ui.titles) == 1
    assert result is not None and result.block


async def test_auto_does_not_classify_a_tool_that_is_not_aelix_bash() -> None:
    """``shell`` is in the guardrail's bash-family name set, and on ``aab1f210``
    that sent an extension's ``shell(command="ls")`` through the tree-sitter
    classifier, which said ALLOW. The verdict describes aelix's bash only."""

    ui = _UI(answer="No")
    await _gate(
        _perm(PermissionMode.AUTO),
        _event(_ext_tool("shell"), {"command": "ls"}),
        _Ctx(has_ui=True, ui=ui),
    )
    assert len(ui.titles) == 1


# === headless: the verdict aelix's own bash gets =============================


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO],
)
async def test_a_delegated_child_blocks_a_tool_aelix_did_not_build(mode: PermissionMode) -> None:
    """``headless_default="block"`` is the child floor (ADR-0197 §(e)); an
    unknown tool reaches it exactly where aelix's bash does when it is not
    auto-allowed."""

    perm = _perm(mode, headless_default="block")
    result = await _gate(perm, _event(_mcp_tool("write_file"), {"path": "a"}), _Ctx(has_ui=False))
    assert result is not None and result.block


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO, PermissionMode.YOLO],
)
async def test_print_mode_gives_it_what_print_mode_gives_bash(mode: PermissionMode) -> None:
    """Unchanged by #188 on purpose: ``-p`` / json / rpc keep ``headless_default
    = "allow"`` for every mutating tool (SECURITY.md). Pinned so the parity with
    the built-in bash is a checked property, not a sentence."""

    perm = _perm(mode)
    unknown = await _gate(perm, _event(_mcp_tool("write_file"), {"path": "a"}), _Ctx(has_ui=False))
    bash = await _gate(
        perm,
        _event(BUILTIN_TOOLS["bash"], {"command": "make install"}),
        _Ctx(has_ui=False),
    )
    assert unknown is None
    assert bash is None


# === session grants: exact, and namespaced by provenance ======================


async def test_a_session_grant_for_one_unknown_tool_does_not_glob() -> None:
    """A tool's name is chosen by its author. ``srv__*`` granted for the session
    must not approve ``srv__other``."""

    perm = _perm(PermissionMode.DEFAULT)
    globby = _mcp_tool("*", prefix="srv")
    other = _mcp_tool("other", prefix="srv")

    yes = _UI(answer="Yes, for this session")
    assert await _gate(perm, _event(globby, {}), _Ctx(has_ui=True, ui=yes)) is None
    assert perm._session_allows == {"tool:srv__*"}

    ui = _UI(answer="No")
    await _gate(perm, _event(other, {}, globby), _Ctx(has_ui=True, ui=ui))
    assert len(ui.titles) == 1

    # ...and the same tool again IS covered by its own grant.
    again = _UI()
    assert await _gate(perm, _event(globby, {}), _Ctx(has_ui=True, ui=again)) is None
    assert again.titles == []


async def test_a_builtin_write_grant_does_not_cover_a_third_party_write_tool() -> None:
    """On ``aab1f210`` an extension ``write_file`` shared the ``write:`` key
    namespace with aelix's own write, so a ``write:src/*`` grant covered it."""

    perm = _perm(PermissionMode.DEFAULT)
    yes = _UI(answer="Yes, for this session")
    await _gate(
        perm, _event(BUILTIN_TOOLS["write"], {"path": "src/a.py"}), _Ctx(has_ui=True, ui=yes)
    )
    assert perm._session_allows == {"write:src/*"}

    ui = _UI(answer="No")
    await _gate(
        perm, _event(_ext_tool("write_file"), {"path": "src/b.py"}), _Ctx(has_ui=True, ui=ui)
    )
    assert len(ui.titles) == 1


@pytest.mark.parametrize(
    ("name", "args", "own", "own_args", "wildcard"),
    [
        (
            "write_file",
            {"path": "src/a.txt", "content": "x"},
            "write",
            {"path": "src/evil.py", "content": "y"},
            "write:src/*",
        ),
        (
            "shell",
            {"command": "git status --short"},
            "bash",
            {"command": "git status --porcelain"},
            "bash:git status *",
        ),
    ],
)
async def test_a_session_grant_for_an_unknown_tool_never_covers_aelix_own_tool(
    name: str,
    args: dict[str, Any],
    own: str,
    own_args: dict[str, Any],
    wildcard: str,
) -> None:
    """The other direction of the row above (#188 round 1, verify B1).

    On ``aab1f210`` "Yes, for this session" for an extension tool named
    ``write_file`` stored ``write:src/*`` — the key of aelix's OWN write — so
    aelix's write to ``src/evil.py`` then ran with no prompt; an extension
    ``shell`` granted aelix's ``bash`` the same way. The grant is now the exact
    ``tool:<name>``, and it never covers aelix's tool.
    """

    perm = _perm(PermissionMode.DEFAULT)
    yes = _UI(answer="Yes, for this session")
    assert await _gate(perm, _event(_ext_tool(name), args), _Ctx(has_ui=True, ui=yes)) is None
    assert perm._session_allows == {f"tool:{name}"}
    assert wildcard not in perm._session_allows

    ui = _UI(answer="No")
    result = await _gate(perm, _event(BUILTIN_TOOLS[own], own_args), _Ctx(has_ui=True, ui=ui))
    assert len(ui.titles) == 1, f"aelix's own {own} must still ask"
    assert result is not None and result.block


async def test_the_dialog_shows_a_third_party_tool_raw() -> None:
    """``other`` (raw arguments), never the built-in write's synthesized diff."""

    from aelix_coding_agent.tui.approval_dialog import ApprovalDecision, ApprovalRequest

    seen: list[ApprovalRequest] = []

    async def _runner(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return ApprovalDecision.NO

    perm = _perm(PermissionMode.DEFAULT, approval_runner=_runner)
    await _gate(
        perm, _event(_ext_tool("write_file"), {"path": "a", "content": "x"}), _Ctx(has_ui=True)
    )
    await _gate(
        perm, _event(BUILTIN_TOOLS["write"], {"path": "a", "content": "x"}), _Ctx(has_ui=True)
    )

    assert [r.kind for r in seen] == ["other", "write"]


# === what the prompt SHOWS for a tool aelix did not build ======================

#: Six harmless-looking keys first, the decisive ones seventh and eighth. On
#: ``4a5d208d`` the dialog printed ``label0``..``label5`` and nothing else, and
#: answering Yes wrote ``hidden.txt`` (#188 round 1, verify B2 / Codex 2A).
_HIDDEN = {
    **{f"label{i}": "safe-looking" for i in range(6)},
    "path": "hidden.txt",
    "content": "destructive-marker-188",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _rendered(request: Any) -> str:
    from aelix_coding_agent.tui.approval_dialog import build_approval_view

    return "\n".join(_ANSI.sub("", line) for line in build_approval_view(request))


async def test_the_dialog_is_handed_and_shows_every_argument() -> None:
    """The request carries the arguments the tool will run with, all of them,
    and the rendered body shows every one — the seventh included."""

    from aelix_coding_agent.tui.approval_dialog import ApprovalDecision, ApprovalRequest

    seen: list[ApprovalRequest] = []

    async def _runner(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return ApprovalDecision.NO

    perm = _perm(PermissionMode.DEFAULT, approval_runner=_runner)
    await _gate(perm, _event(_mcp_tool("write_file"), dict(_HIDDEN)), _Ctx(has_ui=True))

    assert len(seen) == 1
    assert seen[0].args == _HIDDEN
    body = _rendered(seen[0])
    assert "8 arguments:" in body
    for key, value in _HIDDEN.items():
        assert f"{key}={value!r}" in body, f"{key} is not on the approval prompt"


def test_one_long_value_is_shown_whole_and_the_next_key_after_it() -> None:
    """#389 review round 2 removed ADR-0253 §8's 200-character cut: the rest of
    a long value was approved unseen whenever the cut body fit the screen. The
    whole value is drawn and the hold (``_BodyViewport``) makes it cost paging,
    so ``path`` after a 5000-character value is still on the body, last."""

    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest

    body = _rendered(ApprovalRequest(tool_name="t", args={"a": "x" * 5000, "path": "evil.txt"}))

    assert "path='evil.txt'" in body
    assert "more chars" not in body
    assert "a='" + "x" * 5000 + "'" in re.sub(r"[│\s]", "", body)


def test_an_argument_name_cannot_draw_a_row_of_its_own() -> None:
    """A key is chosen by whoever sent the call. One with a newline in it is
    shown escaped, so it cannot fake a harmless-looking extra row."""

    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest

    body = _rendered(
        ApprovalRequest(tool_name="t", args={"x\nnote": "routine", "path": "evil.txt"})
    )

    assert "'x\\nnote'='routine'" in body
    assert "path='evil.txt'" in body


def _decisive_last(n: int) -> dict[str, Any]:
    """``n`` arguments: ``n - 1`` harmless-looking fillers, then ``path``.

    #188 review round 2 (verify B2): every row above used exactly eight
    arguments, so a renderer that silently stopped at eight passed them all.
    """

    return {**{f"note{i:02d}": "routine" for i in range(n - 1)}, "path": "decisive-188.txt"}


@pytest.mark.parametrize("n", [9, 20, 60])
def test_no_row_cap_the_last_of_many_arguments_is_rendered(n: int) -> None:
    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest

    body = _rendered(ApprovalRequest(tool_name="fs__write_file", args=_decisive_last(n)))

    assert f"{n} arguments:" in body
    assert "path='decisive-188.txt'" in body
    assert body.count("='routine'") == n - 1


@pytest.mark.parametrize("n", [9, 20, 60])
async def test_no_row_cap_in_the_generic_fallback_title(n: int) -> None:
    ui = _UI(answer="No")
    args = _decisive_last(n)
    await _gate(
        _perm(PermissionMode.DEFAULT),
        _event(_mcp_tool("write_file"), dict(args)),
        _Ctx(has_ui=True, ui=ui),
    )

    assert len(ui.titles) == 1
    title = ui.titles[0]
    assert title.startswith(f"Allow fs__write_file? {n} arguments: ")
    assert title.endswith("path='decisive-188.txt'")
    assert title.count("='routine'") == n - 1


async def test_the_generic_fallback_title_carries_a_long_value_whole() -> None:
    """#389 review round 2: the same rows as the dialog, so no value cut here
    either. The title is sent whole; aelix's own ``select`` still cuts a long
    title row at the screen edge, which is issue #399."""

    ui = _UI(answer="No")
    value = "safe " * 100 + "DECISIVE_TAIL"
    await _gate(
        _perm(PermissionMode.DEFAULT),
        _event(_mcp_tool("write_file"), {"content": value, "path": "p.txt"}),
        _Ctx(has_ui=True, ui=ui),
    )
    assert f"content={value!r}" in ui.titles[0]


def _body_rows(request: Any) -> list[str]:
    """The text between the Panel's side borders, one entry per body line."""

    rows = []
    for line in _rendered(request).splitlines():
        if line.startswith("│") and line.endswith("│"):
            rows.append(line[1:-1].strip())
    return rows


def test_each_argument_is_a_row_of_its_own() -> None:
    """The layout the docs promise: the count line, then one line per argument."""

    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest

    args = {"alpha": 1, "beta": "two", "path": "p.txt", "content": "c"}
    rows = _body_rows(ApprovalRequest(tool_name="t", args=args))

    assert rows == [
        "Tool: t",
        "4 arguments:",
        "alpha=1",
        "beta='two'",
        "path='p.txt'",
        "content='c'",
    ]


def test_an_argument_name_is_shown_whole() -> None:
    """The 60-character key cut went with the value cut (#389 review round 2)."""

    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest, argument_rows

    key = "k" * 100
    assert argument_rows({key: 1}) == ["k" * 100 + "=1"]
    body = _rendered(ApprovalRequest(tool_name="t", args={key: 1, "path": "evil.txt"}))
    # The row wraps inside the Panel at width 80; compare without layout.
    assert "k" * 100 + "=1" in re.sub(r"[│\s]", "", body)
    assert "path='evil.txt'" in body


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (_ext_tool("shell"), {"command": "printf hello-review-188"}),
        (_ext_tool("write_file"), {"path": "legitimate.txt", "content": "hello"}),
        (_mcp_tool("write_file"), _HIDDEN),
    ],
    ids=["ext-shell", "ext-write_file", "mcp-eight-args"],
)
async def test_the_generic_fallback_shows_every_argument(
    tool: AgentTool, args: dict[str, Any]
) -> None:
    """A host with a UI and no approval dialog asks through ``ctx.ui.select``,
    whose title is all the user sees. On ``4a5d208d`` it was a bare
    "Allow shell?" (Codex 2B); ``aab1f210`` showed the command or path, by
    name. It now shows every argument, as the dialog does."""

    ui = _UI(answer="No")
    await _gate(_perm(PermissionMode.DEFAULT), _event(tool, dict(args)), _Ctx(has_ui=True, ui=ui))

    assert len(ui.titles) == 1
    title = ui.titles[0]
    assert title.startswith(f"Allow {tool.name}? {len(args)} argument")
    for key, value in args.items():
        assert f"{key}={value!r}" in title


async def test_the_generic_fallback_keeps_aelix_own_one_line_summary() -> None:
    """aelix's own bash and write keep the command / path title (a pin)."""

    for name, args, title in [
        ("bash", {"command": "git status"}, "Allow bash? git status"),
        ("write", {"path": "src/a.py", "content": "x"}, "Allow write? src/a.py"),
    ]:
        ui = _UI(answer="No")
        await _gate(
            _perm(PermissionMode.DEFAULT),
            _event(BUILTIN_TOOLS[name], args),
            _Ctx(has_ui=True, ui=ui),
        )
        assert ui.titles == [title]


async def test_the_generic_fallback_title_carries_the_whole_command_and_path() -> None:
    """#389: the title stopped at 120 characters, so a host with a UI and no
    approval dialog was asked about the start of a command whose end is what
    it does."""

    command = "true " + " ".join(f"arg{i:03d}" for i in range(400)) + " ; echo TAIL_MARKER_389"
    path = "d/" * 80 + "TAIL_389.txt"
    for name, args, title in [
        ("bash", {"command": command}, f"Allow bash? {command}"),
        ("write", {"path": path, "content": "x"}, f"Allow write? {path}"),
    ]:
        ui = _UI(answer="No")
        await _gate(
            _perm(PermissionMode.DEFAULT),
            _event(BUILTIN_TOOLS[name], args),
            _Ctx(has_ui=True, ui=ui),
        )
        assert ui.titles == [title]


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_every_posture_description_says_what_it_does_with_mcp_and_extension_tools(
    mode: PermissionMode,
) -> None:
    """The shift+tab toast is how a user learns what a posture does. Each one
    names MCP and extension tools, because each one treats them in its own way
    (prompt / prompt / block / run / prompt)."""

    from aelix_coding_agent.builtin.permission_mode import MODE_META

    description = MODE_META[mode].description
    assert "MCP" in description and "extension tool" in description, description


async def test_the_extension_tier_redirect_is_offered_for_aelix_write_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#161's "write it to the other tier instead" rewrites ``args["path"]``. It
    is offered for aelix's own ``write``; a third-party tool that is merely
    called ``write`` gets the standard rows, because what its ``path`` means is
    unknown."""

    agent_dir = tmp_path / "agent"
    project = tmp_path / "proj"
    (agent_dir / "extensions").mkdir(parents=True)
    (project / ".aelix" / "extensions").mkdir(parents=True)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    target = str(agent_dir / "extensions" / "x.py")

    builtin_ui = _UI(answer="No")
    await _gate(
        _perm(PermissionMode.DEFAULT),
        _event(BUILTIN_TOOLS["write"], {"path": target, "content": "x"}),
        _Ctx(has_ui=True, cwd=str(project), ui=builtin_ui),
    )
    shadow_ui = _UI(answer="No")
    await _gate(
        _perm(PermissionMode.DEFAULT),
        _event(_ext_tool("write"), {"path": target, "content": "x"}),
        _Ctx(has_ui=True, cwd=str(project), ui=shadow_ui),
    )

    assert len(builtin_ui.options[0]) == 5, "the built-in write is offered the redirect"
    assert shadow_ui.options == [["Yes", "Yes, for this session", "No", "No, provide reason"]]


# === the built-ins: pins, green before and after =============================


@pytest.mark.parametrize("mode", list(PermissionMode))
@pytest.mark.parametrize("name", ["read", "grep", "find", "ls"])
async def test_aelix_read_only_tools_stay_silent_in_every_mode(
    name: str, mode: PermissionMode
) -> None:
    for has_ui in (True, False):
        ctx = _Ctx(has_ui=has_ui)
        assert (
            await _gate(_perm(mode, headless_default="block"), _event(BUILTIN_TOOLS[name], {}), ctx)
            is None
        )
        if ctx.ui is not None:
            assert ctx.ui.titles == []


async def test_the_bundled_status_tool_stays_silent_in_plan() -> None:
    from aelix_status.extension import create_status_tool

    status = create_status_tool(_noop)
    assert await _gate(_perm(PermissionMode.PLAN), _event(status, {}), _Ctx(has_ui=False)) is None


# === provenance itself ========================================================


def test_the_factories_mark_what_they_build() -> None:
    from aelix_agents.tool import create_agent_tool, with_description
    from aelix_coding_agent.tools.provenance import builtin_provenance
    from aelix_status.extension import create_status_tool

    assert {n: builtin_provenance(t) for n, t in BUILTIN_TOOLS.items()} == {
        "read": "read_only",
        "grep": "read_only",
        "find": "read_only",
        "ls": "read_only",
        "bash": "bash",
        "edit": "write",
        "write": "write",
    }
    assert builtin_provenance(create_status_tool(_noop)) == "read_only"
    agent = create_agent_tool(description="a", execute=_noop)
    assert builtin_provenance(agent) == "delegation"
    # The roster re-stamp replaces the object every turn; it must carry the mark.
    assert builtin_provenance(with_description(agent, "b")) == "delegation"
    # ...and never invent one.
    assert builtin_provenance(with_description(_ext_tool("agent"), "b")) is None
    assert builtin_provenance(_mcp_tool("write_file")) is None


# === through the real loop ====================================================


async def test_plan_stops_an_mcp_write_through_the_real_loop() -> None:
    """The loop's own prep path: the gate sees ``BeforeToolCallContext.context``,
    the same context the loop builds ``tool_map`` from, and the MCP connection
    is never called."""

    conn = _Conn()
    mcp_write = _mcp_tool("write_file", conn=conn)
    perm = _perm(PermissionMode.PLAN)

    async def before(ctx: Any) -> BeforeToolCallResult | None:
        # What ``AgentHarness._before_tool_call_bridge`` does with the context.
        event = ToolCallHookEvent(
            tool_call_id=ctx.tool_call.tool_call_id,
            tool_name=ctx.tool_call.tool_name,
            args=ctx.args,
            context=ctx.context,
        )
        result = await _gate(perm, event, _Ctx(has_ui=False))
        if result is not None and result.block:
            return BeforeToolCallResult(block=True, reason=result.reason)
        return None

    turns = iter(
        [
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="c1",
                        tool_name="fs__write_file",
                        input={"path": "a.txt", "content": "x"},
                    )
                ],
                stop_reason="tool_use",
            ),
            AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn"),
        ]
    )

    async def stream_fn(_model: Any, _context: Any, _options: Any):  # noqa: ANN202
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=next(turns))

    async def emit(_event: Any) -> None:
        return None

    messages = await agent_loop(
        [UserMessage(content=[TextContent(text="write it")])],
        AgentContext(tools=[*BUILTIN_TOOLS.values(), mcp_write]),
        AgentLoopConfig(
            model=Model(id="m", provider="p"),
            convert_to_llm=default_convert_to_llm,
            before_tool_call=before,
        ),
        emit=emit,
        stream_fn=stream_fn,
    )

    assert conn.calls == []
    results = [m for m in messages if getattr(m, "role", None) == "toolResult"]
    assert len(results) == 1 and results[0].is_error
    assert "fs__write_file" in results[0].content[0].text


async def test_the_real_harness_hands_the_gate_the_turn_context(tmp_path: Path) -> None:
    """The emulation above is not the bridge. Through the real ``AgentHarness``
    and extension loader at PLAN: aelix's own ``read`` runs and the MCP write
    is refused. If the harness stopped passing ``context`` into the
    ``tool_call`` event, every tool would be unknown and PLAN would refuse the
    read too (Codex round 1 measured exactly that with the real CLI, and no
    test noticed)."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.messages import ToolResultMessage
    from aelix_coding_agent.extensions.loader import load_extensions
    from aelix_coding_agent.tools import create_all_tools

    (tmp_path / "notes.txt").write_text("READ_MARKER_188\n", encoding="utf-8")
    conn = _Conn()
    loaded = await load_extensions([_perm(PermissionMode.PLAN)])

    def _call(call_id: str, name: str, args: dict[str, Any]) -> AssistantMessage:
        return AssistantMessage(
            content=[ToolCallContent(tool_call_id=call_id, tool_name=name, input=args)],
            stop_reason="tool_use",
        )

    turns = iter(
        [
            _call("c1", "read", {"path": str(tmp_path / "notes.txt")}),
            _call("c2", "fs__write_file", {"path": "a.txt", "content": "x"}),
            AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn"),
        ]
    )

    async def stream_fn(_model: Any, _context: Any, _options: Any):  # noqa: ANN202
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=next(turns))

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            extensions=loaded.extensions,
            runtime=loaded.runtime,
            tools=[*create_all_tools(str(tmp_path)).values(), _mcp_tool("write_file", conn=conn)],
            stream_fn=stream_fn,
        )
    )
    messages = await harness.prompt("look, then write")

    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolResultMessage)}
    assert not results["c1"].is_error, results["c1"].content
    assert "READ_MARKER_188" in results["c1"].content[0].text
    assert results["c2"].is_error and "fs__write_file" in results["c2"].content[0].text
    assert conn.calls == []


def test_a_mark_answers_only_for_the_object_it_was_made_for() -> None:
    """The registry is keyed by ``id()``, and CPython reuses an id once the
    object is gone. Simulated directly: an entry filed under B's id that still
    refers to A must not make B a built-in."""

    import gc

    from aelix_coding_agent.tools import provenance

    a = provenance.mark_builtin(_ext_tool("a"), "read_only")
    b = _ext_tool("b")
    provenance._MARKS[id(b)] = provenance._MARKS[id(a)]
    try:
        assert provenance.builtin_provenance(b) is None
        assert provenance.builtin_provenance(a) == "read_only"
    finally:
        provenance._MARKS.pop(id(b), None)

    # ...and a collected tool's entry goes with it.
    key = id(a)
    del a
    gc.collect()
    assert key not in provenance._MARKS
