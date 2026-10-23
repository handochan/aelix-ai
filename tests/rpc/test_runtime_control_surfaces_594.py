"""#594 — runtime-control surfaces: MCP selection, permission policy, consent, settings, UI.

APIs this issue introduces are imported inside the cases that need them, so one missing
name fails that case only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.types import AgentTool
from aelix_ai.settings.settings_manager import SettingsManager
from aelix_ai.settings.storage import InMemorySettingsStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.api import Extension, RegisteredCommand
from aelix_coding_agent.extensions.ext_ui import ExtensionUIDialogOptions
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.control_mcp import contributions
from aelix_coding_agent.rpc.control_ui import RpcCommandUI
from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime, run_rpc_mode
from aelix_coding_agent.subagent_contract import (
    CONTRACT_VERSION,
    ProjectScopeRefused,
    ResolvedProfile,
    SubagentResult,
)
from aelix_coding_agent.tools import create_all_tools

from tests.builtin.gate_tools import builtin_event
from tests.rpc.test_runtime_control_lifecycle_594 import Rig, Wire, _control

MODEL = Model(id="fixture", provider="fixture")
MCP_FIXTURE = """from mcp.server.fastmcp import FastMCP
server = FastMCP("fixture")
@server.tool()
def echo(value: str) -> str:
    return value
server.run()
"""


def _tool(name: str) -> AgentTool:
    return AgentTool(name=name, description=name, parameters={"type": "object", "properties": {}})


def _harness(*tools: AgentTool, extensions: list[Extension] | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(model=MODEL, tools=list(tools), extensions=extensions or [])
    )


def _server(tmp_path: Path, name: str = "fixture") -> dict[str, Any]:
    script = tmp_path / f"mcp_{name}.py"
    script.write_text(MCP_FIXTURE)
    return {"name": name, "command": sys.executable, "args": [str(script)]}


# === F2: client MCP tools and an explicit tool selection ========================


async def test_removing_a_selected_client_mcp_tool_succeeds(tmp_path: Path) -> None:
    harness = _harness(_tool("alpha"), _tool("beta"))
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        await plane.handle("mcp/configure", {"servers": [_server(tmp_path)]})
        await plane.handle("tools/set_active", {"names": ["alpha", "acp_fixture__echo"]})
        await plane.handle("mcp/configure", {"servers": []})
        assert harness.state.active_tool_names == ["alpha"]
        assert [tool.name for tool in harness.state.tools] == ["alpha", "beta"]
    finally:
        await plane.mcp.close()
        await harness.dispose()


async def test_new_client_mcp_tools_never_widen_an_explicit_selection(tmp_path: Path) -> None:
    harness = _harness(_tool("alpha"), _tool("beta"))
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        await plane.handle("tools/set_active", {"names": ["alpha"]})
        await plane.handle("mcp/configure", {"servers": [_server(tmp_path)]})
        assert harness.state.active_tool_names == ["alpha"]
        assert "acp_fixture__echo" in [tool.name for tool in harness.state.tools]
        # Replacing the server keeps a same-named tool the user selected.
        await plane.handle("tools/set_active", {"names": ["alpha", "acp_fixture__echo"]})
        await plane.handle("mcp/configure", {"servers": [_server(tmp_path)]})
        assert harness.state.active_tool_names == ["alpha", "acp_fixture__echo"]
    finally:
        await plane.mcp.close()
        await harness.dispose()


async def test_reload_keeps_a_selected_client_mcp_tool_active(tmp_path: Path) -> None:
    rig = await Rig.create(tmp_path)
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            rig.runtime.harness,
            runtime_host=rig.runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    try:
        wire.send(_control("c", "mcp/configure", servers=[_server(tmp_path)]))
        assert (await wire.response("c"))["success"] is True
        wire.send(_control("s", "tools/set_active", names=["alpha", "acp_fixture__echo"]))
        assert (await wire.response("s"))["success"] is True
        wire.send(_control("r", "resources/reload"))
        assert (await wire.response("r"))["success"] is True
        wire.send(_control("l", "resources/list"))
        listed = await wire.response("l")
        assert listed["data"]["activeTools"] == ["alpha", "acp_fixture__echo"]
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 30)
        await rig.runtime.harness.dispose()


# === F3: a command dialog UI is not a tool-approval UI =========================


async def test_command_dialogs_leave_the_headless_permission_verdict_alone() -> None:
    gate = PermissionExtension(posture=PermissionPosture())
    seen: dict[str, Any] = {}

    async def probe(args: str, ctx: Any) -> None:
        seen["has_ui"] = ctx.has_ui
        seen["verdict"] = await gate._on_tool_call(
            builtin_event("write", {"path": "notes.txt", "content": "x"}), ctx
        )
        ctx.ui.set_widget("panel", ["line one"])
        ctx.ui.set_title("title")
        ctx.ui.set_working_message("busy")
        seen["custom"] = await ctx.ui.custom(lambda *parts: None)
        seen["editor"] = ctx.ui.get_editor_text()

    extension = Extension(name="fixture")
    extension.commands["probe"] = RegisteredCommand(
        name="probe", handler=probe, description="probe", source="fixture"
    )
    harness = _harness(extensions=[extension])
    events: list[dict[str, Any]] = []
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), emit=events.append)
    try:
        await plane.handle("ui/enable", {"enabled": True})
        result = await plane.handle("commands/run", {"text": "/probe"})
        assert result["outcome"] == "handled", result
        assert seen == {"has_ui": True, "verdict": None, "custom": None, "editor": ""}
        assert [event.get("method") for event in events] == ["setWidget", "setTitle"]
    finally:
        await harness.dispose()


# === F7: permission evaluation follows provenance and posture ==================


async def test_permission_evaluation_uses_tool_provenance_and_posture(tmp_path: Path) -> None:
    tools = [*create_all_tools(str(tmp_path)).values(), _tool("fs__run")]
    harness = _harness(*tools)
    posture = PermissionPosture(mode=PermissionMode.PLAN)
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), permission_posture=posture)
    try:
        bash = await plane.handle(
            "permissions/evaluate", {"tool": "bash", "args": {"command": "ls"}}
        )
        assert bash["decision"] == "deny"
        read = await plane.handle("permissions/evaluate", {"tool": "read", "args": {"path": "a"}})
        assert read["decision"] == "allow"
        posture.set(PermissionMode.AUTO)
        mcp = await plane.handle(
            "permissions/evaluate", {"tool": "fs__run", "args": {"command": "ls"}}
        )
        assert mcp["decision"] == "ask"
        # Without ``tool`` the args are aelix's own bash (the shipped adapter's call).
        legacy = await plane.handle("permissions/evaluate", {"args": {"command": "ls"}})
        assert legacy["decision"] == PermissionExtension._auto_classify_bash({"command": "ls"})
        dangerous = await plane.handle(
            "permissions/evaluate", {"args": {"command": "rm -rf /tmp/x"}}
        )
        assert dangerous["decision"] == "deny"
    finally:
        await harness.dispose()


# === F8: project agent profiles need native consent ============================


class _Subagents:
    contract_version = CONTRACT_VERSION

    def __init__(self, *, project: bool) -> None:
        self.project = project
        self.resolved: list[bool] = []
        self.spawned: list[str] = []

    def resolve_profile(self, name: str, *, allow_project: bool = False) -> ResolvedProfile:
        self.resolved.append(allow_project)
        scope = "project" if self.project else "user"
        if self.project and not allow_project:
            raise ProjectScopeRefused(f"agent profile {name!r} is project-local")
        profile = AgentProfile(
            name=name,
            description="d",
            body="b",
            file_path=f"/repo/.aelix/agents/{name}.md",
            scope=scope,
        )
        return ResolvedProfile(
            name=name, profile=profile, source_path=profile.file_path, scope=scope
        )

    async def spawn(self, resolved: ResolvedProfile, task: str, **_: Any) -> SubagentResult:
        self.spawned.append(task)
        return SubagentResult(id="s1", profile=resolved.name, ok=True, status="ok", summary="done")


def _delegating(
    subagents: _Subagents, **services: Any
) -> tuple[AgentHarness, RpcControlPlane, list]:
    harness = _harness()
    # Test seam: skip bind's runtime_checkable probe; only these two members are used.
    harness.runtime._subagents = subagents  # type: ignore[assignment]
    events: list[dict[str, Any]] = []
    plane = RpcControlPlane(
        _make_passthrough_runtime(harness, None), emit=events.append, **services
    )
    return harness, plane, events


async def test_a_client_boolean_is_not_project_profile_consent() -> None:
    subagents = _Subagents(project=True)
    harness, plane, _ = _delegating(subagents)
    try:
        with pytest.raises(ValueError, match="allowProject"):
            await plane.handle("agents/run", {"name": "rev", "task": "t", "allowProject": True})
        with pytest.raises(ValueError, match="consent"):
            await plane.handle("agents/run", {"name": "rev", "task": "t"})
        assert subagents.spawned == []
    finally:
        await harness.dispose()


async def test_project_profile_runs_after_the_rpc_confirmation() -> None:
    subagents = _Subagents(project=True)
    harness, plane, events = _delegating(subagents)

    def answer(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("method") == "select":
            asyncio.get_running_loop().call_soon(
                plane.ui.respond, {"id": event["id"], "value": event["options"][0]}
            )

    plane.ui.emit = answer
    try:
        await plane.handle("ui/enable", {"enabled": True})
        result = await plane.handle("agents/run", {"name": "rev", "task": "t"})
        assert result["result"]["ok"] is True
        assert subagents.resolved == [False, True]
        assert subagents.spawned == ["t"]
        assert "/repo/.aelix/agents/rev.md" in next(
            e["title"] for e in events if e.get("method") == "select"
        )
    finally:
        await harness.dispose()


async def test_project_profile_runs_under_launch_approval() -> None:
    subagents = _Subagents(project=True)

    async def approve(profile: AgentProfile) -> bool:
        return True

    service = SimpleNamespace(
        active=None,
        confirm_project=approve,
        show=lambda name: subagents.resolve_profile(name, allow_project=True).profile,
    )
    harness, plane, _ = _delegating(subagents, agent_service=service)
    try:
        result = await plane.handle("agents/run", {"name": "rev", "task": "t"})
        assert result["result"]["ok"] is True
        assert subagents.spawned == ["t"]
    finally:
        await harness.dispose()


# === F9: capabilities name only what this host can serve ======================


async def test_capabilities_follow_the_injected_services() -> None:
    harness = _harness()
    bare = RpcControlPlane(_make_passthrough_runtime(harness, None))
    full = RpcControlPlane(
        _make_passthrough_runtime(harness, None),
        permission_posture=PermissionPosture(),
        settings_manager=SettingsManager.in_memory(),
        auth_storage=SimpleNamespace(),
        agent_service=SimpleNamespace(active=None),
    )
    try:
        bare_methods = set((await bare.handle("capabilities", {}))["methods"])
        full_methods = set((await full.handle("capabilities", {}))["methods"])
        hosted = {
            "settings/get",
            "settings/set",
            "permissions/get",
            "permissions/set",
            "permissions/evaluate",
            "auth/status",
            "auth/login",
            "agents/list",
            "agents/use",
        }
        assert not bare_methods & hosted
        assert hosted <= full_methods
        assert "agents/run" not in full_methods  # delegation is not bound on this harness
        assert {
            "commands/run",
            "commands/cancel",
            "mcp/configure",
            "sessions/branch",
        } <= bare_methods
    finally:
        await harness.dispose()


# === F10: dialogs and errors end the same way every time =======================


async def test_dialog_timeout_signal_and_shutdown_resolve_as_cancelled() -> None:
    ui = RpcCommandUI(lambda event: None)
    ui.enabled = True
    timed = ExtensionUIDialogOptions(timeout=0.01)
    assert await asyncio.wait_for(ui.select("t", ["a"], timed), 5) is None
    signal = asyncio.Event()
    waiting = asyncio.create_task(ui.confirm("t", "m", ExtensionUIDialogOptions(signal=signal)))
    await asyncio.sleep(0)
    signal.set()
    assert await asyncio.wait_for(waiting, 5) is False
    ui.close()
    assert await asyncio.wait_for(ui.input("t"), 5) is None
    assert ui.pending == {}


async def test_mcp_connect_timeout_names_itself(tmp_path: Path) -> None:
    from aelix_coding_agent.rpc.control_mcp import RpcMcpController

    silent = tmp_path / "silent.py"
    silent.write_text("import sys\nsys.stdin.read()\n")
    harness = _harness(_tool("alpha"))
    controller = RpcMcpController(connect_timeout=0.5)
    try:
        with pytest.raises(ValueError, match="did not finish connecting"):
            await controller.configure(
                [{"name": "silent", "command": sys.executable, "args": [str(silent)]}],
                lambda: harness,
            )
        assert [tool.name for tool in harness.state.tools] == ["alpha"]
    finally:
        await controller.close()
        await harness.dispose()


async def test_control_errors_are_never_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError

    monkeypatch.setattr(RpcControlPlane, "_dispatch", fail)
    harness = _harness()
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            harness, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False
        )
    )
    try:
        wire.send(_control("x", "mcp/status"))
        response = await asyncio.wait_for(wire.response("x"), 5)
        assert response["success"] is False and response["error"]
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await harness.dispose()


async def test_an_unserializable_result_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def odd(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"value": object()}

    monkeypatch.setattr(RpcControlPlane, "_dispatch", odd)
    harness = _harness()
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(
            harness, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False
        )
    )
    try:
        wire.send(_control("x", "mcp/status"))
        response = await asyncio.wait_for(wire.response("x"), 5)
        assert response["success"] is False and response["error"]
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await harness.dispose()


# === F11: settings are saved before success is reported ========================


async def test_settings_are_flushed_and_write_errors_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemorySettingsStorage()
    settings = SettingsManager.from_storage(storage)
    plane = RpcControlPlane(
        SimpleNamespace(harness=SimpleNamespace(phase="idle")), settings_manager=settings
    )
    await plane.handle("settings/set", {"key": "retry_enabled", "value": False})
    assert SettingsManager.from_storage(storage).get_retry_enabled() is False

    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_persist_scoped_settings", broken)
    with pytest.raises(ValueError, match="not saved"):
        await plane.handle("settings/set", {"key": "retry_enabled", "value": True})


# === F12: MCP names stay inside provider tool-name limits ======================


def test_mcp_server_names_map_to_provider_safe_keys() -> None:
    # ACP names are human-readable: mapped, not refused.
    mapped = contributions(
        [{"name": "github.com", "command": "python"}, {"name": "a" * 40, "command": "python"}]
    )
    assert [server.name for server in mapped] == ["acp_github_com", "acp_" + "a" * 32]
    with pytest.raises(ValueError, match="unique key"):
        contributions(
            [{"name": "my.server", "command": "p"}, {"name": "my server", "command": "p"}]
        )
    with pytest.raises(ValueError, match="unique key"):
        contributions([{"name": "...", "command": "python"}])
    with pytest.raises(ValueError, match="printable"):
        contributions([{"name": "bad\nname", "command": "python"}])


def test_tools_with_unsafe_qualified_names_are_skipped_with_diagnostics() -> None:
    from aelix_coding_agent.rpc.control_mcp import provider_safe_tools

    kept, skipped = provider_safe_tools(
        [_tool("acp_x__ok"), _tool("acp_x__has.dot"), _tool("acp_x__" + "y" * 60)]
    )
    assert [tool.name for tool in kept] == ["acp_x__ok"]
    assert [entry["tool"] for entry in skipped] == ["acp_x__has.dot", "acp_x__" + "y" * 60]
    assert json.dumps(skipped)
