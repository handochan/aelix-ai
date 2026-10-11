"""#395: a select-only embedding cannot approve built-in file changes.

Real factory objects decide provenance. The SDK rows use the real extension
loader, bound public UI, harness hook bridge and loop, with a scripted model.
Affirmative runner controls prove the file operation itself can execute.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_agent_core.types import AgentContext, AgentTool
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, ToolResultMessage
from aelix_ai.streaming import AssistantEndEvent, AssistantMessageEvent, AssistantStartEvent, Model
from aelix_ai.tools import ToolResult
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.loader import load_extensions
from aelix_coding_agent.tools import create_all_tools
from aelix_coding_agent.tools.edit import create_edit_tool
from aelix_coding_agent.tools.write import create_write_tool
from aelix_coding_agent.tui.approval_dialog import ApprovalDecision, ApprovalRequest


class _SelectOnlyUI:
    """Only the public select/input signatures, deliberately no preview API."""

    def __init__(self, answer: str = "Yes") -> None:
        self.answer = answer
        self.select_calls: list[tuple[str, list[str]]] = []
        self.input_calls = 0

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        self.select_calls.append((title, list(options)))
        if self.answer == "redirect":
            return options[2]
        return self.answer

    async def input(self, title: str, placeholder: str | None = None, opts: Any = None) -> str:
        self.input_calls += 1
        return "This cannot supply a file preview."


class _Ctx:
    def __init__(self, cwd: Path, ui: _SelectOnlyUI | None) -> None:
        self.cwd = str(cwd)
        self.ui = ui
        self.has_ui = ui is not None


class _RecordingFiles:
    """Real local file operations, recorded at the factory's supported seam."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read_file(self, path: str) -> bytes:
        self.calls.append("read_file")
        return Path(path).read_bytes()

    async def write_file(self, path: str, data: bytes) -> None:
        self.calls.append("write_file")
        Path(path).write_bytes(data)

    async def mkdir(self, path: str) -> None:
        self.calls.append("mkdir")
        Path(path).mkdir(parents=True, exist_ok=True)

    async def access(self, path: str) -> bool:
        self.calls.append("access")
        return os.access(path, os.R_OK | os.W_OK)


def _event(cwd: Path, name: str, args: dict[str, Any]) -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="preview-395",
        tool_name=name,
        args=args,
        context=AgentContext(tools=list(create_all_tools(str(cwd)).values())),
    )


_LONG = "\n".join(f"line-{i}" for i in range(160)) + "\nDECISIVE_TAIL_395"
_FILE_CHANGES = [
    pytest.param("write", {"content": "short"}, id="short-write"),
    pytest.param("write", {"content": ""}, id="empty-write"),
    pytest.param("write", {"content": _LONG}, id="long-write"),
    pytest.param("write", {"content": "x" * 1800 + "DECISIVE_TAIL_395"}, id="wide-write"),
    pytest.param("edit", {"oldText": "original", "newText": "short"}, id="top-level-edit"),
    pytest.param("edit", {"edits": [{"oldText": "original", "newText": _LONG}]}, id="long-edit"),
    pytest.param(
        "edit",
        {"edits": json.dumps([{"oldText": "original", "newText": _LONG}])},
        id="json-edit-list",
    ),
    pytest.param(
        "edit",
        {
            "edits": [{"oldText": "original", "newText": "first"}],
            "oldText": "second",
            "newText": _LONG,
        },
        id="list-and-top-level-edit",
    ),
    pytest.param("edit", {"edits": "malformed"}, id="unparsed-edit"),
]


@pytest.mark.parametrize("name,change", _FILE_CHANGES)
@pytest.mark.parametrize("answer", ["Yes", "Yes, for this session", "No, provide reason"])
async def test_prompted_builtin_change_never_reaches_select(
    tmp_path: Path, name: str, change: dict[str, Any], answer: str
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("original\n", encoding="utf-8")
    args = {"path": str(target), **copy.deepcopy(change)}
    before = copy.deepcopy(args)
    ui = _SelectOnlyUI(answer)
    perm = PermissionExtension()

    result = await perm._on_tool_call(_event(tmp_path, name, args), _Ctx(tmp_path, ui))  # type: ignore[arg-type]

    assert isinstance(result, ToolCallResult) and result.block
    assert "file-change preview approval runner" in (result.reason or "")
    assert name in (result.reason or "")
    assert ui.select_calls == [] and ui.input_calls == 0
    assert perm._session_allows == set()
    assert args == before
    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("answer", ["Yes", "Yes, for this session", "redirect"])
@pytest.mark.parametrize("path_key", ["path", "file_path"])
async def test_missing_preview_refuses_before_redirect_or_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, path_key: str
) -> None:
    import aelix_coding_agent.builtin.permission as permission

    agent_dir = tmp_path / "agent"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    target = agent_dir / "extensions" / "untrusted.py"
    args = {path_key: str(target), "content": _LONG}
    event = _event(project, "write", args)
    before = copy.deepcopy(args)
    ui = _SelectOnlyUI(answer)
    perm = PermissionExtension()

    def forbidden(*_a: Any, **_k: Any) -> Any:
        pytest.fail("a missing preview must deny before redirect resolution or file IO")

    with monkeypatch.context() as guard:
        guard.setattr(permission, "_extension_redirect", forbidden)
        guard.setattr(Path, "read_text", forbidden)
        guard.setattr(Path, "write_text", forbidden)
        result = await perm._on_tool_call(event, _Ctx(project, ui))  # type: ignore[arg-type]

    assert result is not None and result.block
    assert "file-change preview approval runner" in (result.reason or "")
    assert ui.select_calls == [] and ui.input_calls == 0
    assert perm._session_allows == set() and args == before
    assert not target.exists()
    assert not (project / ".aelix" / "extensions" / "untrusted.py").exists()


async def _sdk_call(
    cwd: Path, perm: PermissionExtension, ui: _SelectOnlyUI, name: str, args: dict[str, Any]
) -> tuple[ToolResultMessage, list[ToolResultMessage], list[str]]:
    loaded = await load_extensions([perm])
    loaded.runtime.bind_ui(ui)  # type: ignore[arg-type]
    model_receipts: list[ToolResultMessage] = []
    calls = 0

    async def stream_fn(
        _model: Any, context: Any, _options: Any
    ) -> AsyncIterator[AssistantMessageEvent]:
        nonlocal calls
        calls += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if calls == 1:
            message = AssistantMessage(
                content=[ToolCallContent(tool_call_id="preview-395", tool_name=name, input=args)],
                stop_reason="tool_use",
            )
        else:
            model_receipts.extend(m for m in context.messages if isinstance(m, ToolResultMessage))
            message = AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        yield AssistantEndEvent(message=message)

    tools = create_all_tools(str(cwd))
    files = _RecordingFiles()
    factory = create_write_tool if name == "write" else create_edit_tool
    tools[name] = factory(str(cwd), {"operations": files})
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="scripted-395", provider="test"),
            extensions=loaded.extensions,
            runtime=loaded.runtime,
            cwd=str(cwd),
            tools=list(tools.values()),
            stream_fn=stream_fn,
        )
    )
    try:
        messages = await harness.prompt("Apply the requested file change.")
        results = [m for m in messages if isinstance(m, ToolResultMessage)]
        assert len(results) == 1 and calls == 2
        return results[0], model_receipts, files.calls
    finally:
        await harness.dispose()


@pytest.mark.parametrize("name", ["write", "edit"])
@pytest.mark.parametrize("long", [False, True], ids=["short", "decisive-long-tail"])
@pytest.mark.parametrize("answer", ["Yes", "Yes, for this session"])
async def test_sdk_bound_select_only_host_blocks_real_file_execution(
    tmp_path: Path, name: str, long: bool, answer: str
) -> None:
    target = tmp_path / "target.txt"
    if name == "edit":
        target.write_text("original\n", encoding="utf-8")
    replacement = _LONG if long else "replacement"
    args = {"path": str(target)}
    args.update(
        {"content": replacement}
        if name == "write"
        else {"edits": [{"oldText": "original", "newText": replacement}]}
    )
    before = copy.deepcopy(args)
    perm = PermissionExtension()
    ui = _SelectOnlyUI(answer)

    result, receipts, executions = await _sdk_call(tmp_path, perm, ui, name, args)

    assert result.is_error
    assert "file-change preview approval runner" in result.content[0].text
    assert receipts == [result], "the refusal reaches the next model request"
    assert ui.select_calls == [] and ui.input_calls == 0
    assert executions == [] and perm._session_allows == set() and args == before
    if name == "write":
        assert not target.exists()
    else:
        assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("name", ["write", "edit"])
@pytest.mark.parametrize(
    "decision",
    [
        ApprovalDecision.YES,
        ApprovalDecision.YES_SESSION,
        ApprovalDecision.NO,
        ApprovalDecision.CANCEL,
        "raise",
    ],
)
async def test_sdk_wired_runner_keeps_real_verdict_and_execution(
    tmp_path: Path, name: str, decision: ApprovalDecision | str
) -> None:
    target = tmp_path / "target.txt"
    if name == "edit":
        target.write_text("original\n", encoding="utf-8")
    args = {"path": str(target)}
    args.update(
        {"content": _LONG}
        if name == "write"
        else {"edits": [{"oldText": "original", "newText": _LONG}]}
    )
    seen: list[ApprovalRequest] = []

    async def runner(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(copy.deepcopy(request))
        if decision == "raise":
            raise RuntimeError("preview host detached")
        assert isinstance(decision, ApprovalDecision)
        return decision

    perm = PermissionExtension(approval_runner=runner)
    ui = _SelectOnlyUI("Yes")
    result, receipts, executions = await _sdk_call(tmp_path, perm, ui, name, args)
    allowed = decision in (ApprovalDecision.YES, ApprovalDecision.YES_SESSION)

    assert len(seen) == 1 and seen[0].kind == name and seen[0].args == args
    assert ui.select_calls == [] and ui.input_calls == 0
    assert result.is_error is not allowed and receipts == [result]
    assert ("write_file" in executions) is allowed
    if not allowed:
        assert executions == []
    expected_grants = (
        {f"write:{tmp_path.as_posix()}/*"} if decision == ApprovalDecision.YES_SESSION else set()
    )
    assert perm._session_allows == expected_grants
    if allowed:
        assert target.read_text(encoding="utf-8") == _LONG + ("\n" if name == "edit" else "")
    elif name == "write":
        assert not target.exists()
    else:
        assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("answer", ["Yes", "Yes, for this session"])
async def test_sdk_select_only_host_cannot_overwrite_existing_file(
    tmp_path: Path, answer: str
) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original\n", encoding="utf-8")
    args = {"path": str(target), "content": _LONG}
    perm = PermissionExtension()
    ui = _SelectOnlyUI(answer)
    result, receipts, operations = await _sdk_call(tmp_path, perm, ui, "write", args)
    assert result.is_error and receipts == [result]
    assert "file-change preview approval runner" in result.content[0].text
    assert operations == [] and ui.select_calls == [] and perm._session_allows == set()
    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize("answer", ["Yes", "Yes, for this session", "redirect"])
async def test_sdk_select_only_host_cannot_execute_or_redirect_extension_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    target = agent_dir / "extensions" / "x.py"
    args = {"path": str(target), "content": _LONG}
    before = copy.deepcopy(args)
    perm = PermissionExtension()
    ui = _SelectOnlyUI(answer)
    result, receipts, operations = await _sdk_call(project, perm, ui, "write", args)
    assert result.is_error and receipts == [result]
    assert "file-change preview approval runner" in result.content[0].text
    assert operations == [] and ui.select_calls == [] and perm._session_allows == set()
    assert args == before and not target.exists()
    assert not (project / ".aelix" / "extensions" / "x.py").exists()


@pytest.mark.parametrize("name", ["write", "edit"])
@pytest.mark.parametrize("decision", [ApprovalDecision.YES, ApprovalDecision.YES_SESSION])
async def test_sdk_runner_once_and_session_authority_keep_their_scope(
    tmp_path: Path, name: str, decision: ApprovalDecision
) -> None:
    seen: list[ApprovalRequest] = []

    async def runner(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return decision

    perm = PermissionExtension(approval_runner=runner)
    ui = _SelectOnlyUI("No")
    for index in range(2):
        target = tmp_path / f"file-{index}.txt"
        if name == "edit":
            target.write_text("original\n", encoding="utf-8")
        change = (
            {"content": _LONG}
            if name == "write"
            else {"edits": [{"oldText": "original", "newText": _LONG}]}
        )
        result, receipts, operations = await _sdk_call(
            tmp_path, perm, ui, name, {"path": str(target), **change}
        )
        assert not result.is_error and receipts == [result] and "write_file" in operations
        assert target.read_text(encoding="utf-8") == _LONG + ("\n" if name == "edit" else "")
    assert len(seen) == (1 if decision == ApprovalDecision.YES_SESSION else 2)
    assert ui.select_calls == []


@pytest.mark.parametrize("name", ["write", "edit"])
async def test_same_name_third_party_tool_keeps_whole_argument_fallback(
    tmp_path: Path, name: str
) -> None:
    async def execute(_args: dict[str, Any], _ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(name=name, description="third party", parameters={}, execute=execute)
    args = {"path": "untrusted.py", "content": _LONG, "last": "DECISIVE_LAST_ARG_395"}
    event = ToolCallHookEvent(
        tool_call_id="other-395", tool_name=name, args=args, context=AgentContext(tools=[tool])
    )
    ui = _SelectOnlyUI("Yes, for this session")
    perm = PermissionExtension()

    assert await perm._on_tool_call(event, _Ctx(tmp_path, ui)) is None  # type: ignore[arg-type]
    assert len(ui.select_calls) == 1
    title, options = ui.select_calls[0]
    for key, value in args.items():
        assert f"{key}={value!r}" in title
    assert options == ["Yes", "Yes, for this session", "No", "No, provide reason"]
    assert perm._session_allows == {f"tool:{name}"}


@pytest.mark.parametrize("mode", [PermissionMode.AUTO, PermissionMode.AUTO_ACCEPT])
@pytest.mark.parametrize(
    "sensitive", [False, True], ids=["outside-project", "sensitive-in-project"]
)
async def test_auto_prompted_change_still_requires_a_preview_runner(
    tmp_path: Path, mode: PermissionMode, sensitive: bool
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / ".bashrc" if sensitive else tmp_path / "outside.txt"
    ui = _SelectOnlyUI("Yes")
    perm = PermissionExtension(posture=PermissionPosture(mode=mode))
    result = await perm._on_tool_call(
        _event(project, "write", {"path": str(target), "content": _LONG}), _Ctx(project, ui)
    )  # type: ignore[arg-type]
    assert result is not None and result.block
    assert "file-change preview approval runner" in (result.reason or "")
    assert ui.select_calls == [] and not target.exists()


@pytest.mark.parametrize(
    "mode", [PermissionMode.AUTO, PermissionMode.AUTO_ACCEPT, PermissionMode.YOLO]
)
async def test_authorized_posture_still_allows_builtin_change_without_preview(
    tmp_path: Path, mode: PermissionMode
) -> None:
    perm = PermissionExtension(posture=PermissionPosture(mode=mode))
    ui = _SelectOnlyUI("No")
    assert (
        await perm._on_tool_call(
            _event(tmp_path, "write", {"path": str(tmp_path / "a.txt"), "content": _LONG}),
            _Ctx(tmp_path, ui),
        )
        is None
    )  # type: ignore[arg-type]
    assert ui.select_calls == []


async def test_existing_session_authority_still_precedes_preview_requirement(
    tmp_path: Path,
) -> None:
    perm = PermissionExtension()
    perm._session_allows.add("write:src/*")
    ui = _SelectOnlyUI("No")
    assert (
        await perm._on_tool_call(
            _event(tmp_path, "write", {"path": "src/a.txt", "content": _LONG}), _Ctx(tmp_path, ui)
        )
        is None
    )  # type: ignore[arg-type]
    assert ui.select_calls == [] and perm._session_allows == {"write:src/*"}


@pytest.mark.parametrize("child", [False, True], ids=["parent", "child"])
@pytest.mark.parametrize("mode", [PermissionMode.DEFAULT, PermissionMode.PLAN])
async def test_headless_and_plan_floors_are_unchanged(
    tmp_path: Path, child: bool, mode: PermissionMode
) -> None:
    perm = PermissionExtension(
        posture=PermissionPosture(mode=mode), headless_default="block" if child else "allow"
    )
    result = await perm._on_tool_call(
        _event(tmp_path, "write", {"path": "a.txt", "content": _LONG}), _Ctx(tmp_path, None)
    )  # type: ignore[arg-type]
    if child or mode == PermissionMode.PLAN:
        assert result is not None and result.block
        assert "file-change preview" not in (result.reason or "")
    else:
        assert result is None
