"""Headless hosts use native services, with no prompt fallback or credential export."""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.prompt_templates import PromptTemplate
from aelix_agent_core.harness.skills import Skill
from aelix_ai.models import Model
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.api import Extension, RegisteredCommand
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.control_mcp import RpcMcpController, contributions
from aelix_coding_agent.rpc.control_ui import RpcCommandUI
from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime, run_rpc_mode


def make_harness(extensions=None):
    return AgentHarness(AgentHarnessOptions(model=Model(id="fixture", provider="fixture"), extensions=extensions or []))


async def test_capability_version_is_explicit_and_permission_changes_reach_native_gate():
    harness = make_harness()
    posture = PermissionPosture()
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), permission_posture=posture)
    try:
        assert (await plane.handle("capabilities", {}))["version"] == 1
        await plane.handle("permissions/set", {"mode": "plan"})
        assert posture.get() is PermissionMode.PLAN
        with pytest.raises(ValueError):
            await plane.handle("permissions/set", {"mode": "made-up"})
        assert posture.get() is PermissionMode.PLAN
    finally:
        await harness.dispose()


async def test_unknown_command_is_not_a_model_prompt():
    harness = make_harness()
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result = await plane.handle("commands/run", {"text": "/does-not-exist"})
        assert result["outcome"] == "not_a_command"
        assert harness.state.messages == []
    finally:
        await harness.dispose()


async def test_extension_commands_use_the_shared_dispatcher_and_raw_arguments():
    extension = Extension(name="fixture")
    extension.commands["echo"] = RegisteredCommand(name="echo", handler=lambda args, ctx: "received:" + args,
                                                   description="Echo", source="fixture")
    harness = make_harness([extension])
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result = await plane.handle("commands/run", {"text": "/echo  two spaces"})
        assert result["outcome"] == "handled"
        assert result["messages"] == [{"type": "text", "text": "received: two spaces"}]
    finally:
        await harness.dispose()


async def test_a_throwing_extension_is_an_error_and_never_falls_through():
    def fail(args, ctx):
        raise ValueError("fixture failure")
    extension = Extension(name="fixture")
    extension.commands["fail"] = RegisteredCommand(name="fail", handler=fail, description="Fail", source="fixture")
    harness = make_harness([extension])
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result = await plane.handle("commands/run", {"text": "/fail"})
        assert result["outcome"] == "error"
        assert harness.state.messages == []
    finally:
        await harness.dispose()


async def test_prompt_templates_and_user_selected_skills_are_prepared_by_native_formatters():
    harness = make_harness()
    harness.set_prompt_templates([PromptTemplate(name="greet", description="Greet", content="Hello $1")])
    harness.set_skills([Skill(name="test", description="Test", content="Use this skill", file_path="/fixture/SKILL.md",
                             disable_model_invocation=True)])
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        prompt = await plane.handle("commands/run", {"text": "/greet world"})
        assert prompt["outcome"] == "prompt" and "Hello world" in prompt["prompt"]
        skill = await plane.handle("commands/run", {"text": "/skill:test extra"})
        assert skill["outcome"] == "prompt" and "Use this skill" in skill["prompt"]
        assert "extra" in skill["prompt"]
    finally:
        await harness.dispose()


async def test_settings_are_explicitly_typed_and_credentials_cannot_be_written():
    plane = RpcControlPlane(SimpleNamespace(harness=SimpleNamespace(phase="idle")), settings_manager=SimpleNamespace())
    for params in [{"key": "api_key", "value": "secret"}, {"key": "retry_enabled", "value": "true"},
                   {"key": "enabled_models", "value": [1]}, {"key": [], "value": True}]:
        with pytest.raises(ValueError):
            await plane.handle("settings/set", params)


async def test_runtime_mutations_refuse_a_live_turn():
    posture = PermissionPosture()
    plane = RpcControlPlane(SimpleNamespace(harness=SimpleNamespace(phase="turn")), permission_posture=posture)
    with pytest.raises(ValueError, match="idle"):
        await plane.handle("permissions/set", {"mode": "yolo"})
    assert posture.get() is PermissionMode.DEFAULT


async def test_dialog_correlation_and_shutdown_cancellation():
    events = []
    ui = RpcCommandUI(events.append)
    with pytest.raises(RuntimeError, match="unavailable"):
        await ui.confirm("Title", "Message")
    ui.enabled = True
    selection = asyncio.create_task(ui.select("Pick", ["one", "two"]))
    await asyncio.sleep(0)
    ui.respond({"id": events[-1]["id"], "value": "not-an-option"})
    assert await selection is None
    confirmation = asyncio.create_task(ui.confirm("Title", "Message"))
    await asyncio.sleep(0)
    ui.close()
    assert await confirmation is False
    assert ui.pending == {}


async def test_command_cancellation_does_not_wait_for_the_control_lock():
    entered = asyncio.Event()
    async def wait(args, ctx):
        entered.set()
        await asyncio.Event().wait()
    extension = Extension(name="fixture")
    extension.commands["wait"] = RegisteredCommand(name="wait", handler=wait, description="Wait", source="fixture")
    harness = make_harness([extension])
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), permission_posture=PermissionPosture())
    try:
        command = asyncio.create_task(plane.handle("commands/run", {"text": "/wait"}))
        await entered.wait()
        verdict = await asyncio.wait_for(plane.handle("permissions/evaluate", {"args": {"command": "pwd"}}), 1)
        assert verdict["decision"] in {"allow", "ask", "deny"}
        assert await asyncio.wait_for(plane.handle("commands/cancel", {}), 1) == {
            "cancelled": True, "method": "commands/run"}
        with pytest.raises(asyncio.CancelledError):
            await command
    finally:
        await harness.dispose()


async def test_authentication_mutations_return_status_without_returning_credentials(tmp_path):
    from aelix_ai.oauth import AuthStorage
    storage = AuthStorage(tmp_path / "auth.json")
    await storage.load()
    harness = make_harness()
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), auth_storage=storage)
    try:
        result = await plane.handle("auth/set_api_key", {"provider": "fixture", "key": "synthetic-secret"})
        assert "synthetic-secret" not in json.dumps(result)
        assert await storage.get_api_key("fixture") == "synthetic-secret"
        await plane.handle("auth/logout", {"provider": "fixture"})
        assert await storage.get_api_key("fixture") is None
        with pytest.raises(ValueError):
            await plane.handle("auth/set_api_key", {"provider": "fixture", "key": "bad\nheader"})
    finally:
        await harness.dispose()


async def test_agent_profiles_use_the_native_service_without_mutating_extensions():
    harness = make_harness()
    async def use(name, *, harness):
        return "selected:" + str(name)
    profile = SimpleNamespace(name="reviewer", description="Review", scope="user", file_path="/user/reviewer.md")
    service = SimpleNamespace(active=None, list=lambda: SimpleNamespace(profiles=[profile], diagnostics=[]), use=use)
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), agent_service=service)
    try:
        result = await plane.handle("agents/list", {})
        assert result["profiles"][0]["name"] == "reviewer"
        assert await plane.handle("agents/use", {"name": "reviewer"}) == {"message": "selected:reviewer"}
        with pytest.raises(ValueError, match="disabled"):
            await plane.handle("agents/run", {"name": "reviewer", "task": "review"})
    finally:
        await harness.dispose()


async def test_agents_show_still_carries_the_ignored_timeout_ms_key():
    """#604 (ADR-0276 D2) — hosts keep the key they already read.

    ``agents/show`` returns ``asdict`` of the parsed profile. ``timeout_ms`` no
    longer does anything, but the field is KEPT (deprecated, never read) so this
    payload does not lose a key a host may be displaying or round-tripping.
    """

    from aelix_coding_agent.agents.profile import parse_profile

    parsed = parse_profile(
        "---\nname: reviewer\ndescription: Review\ntimeout_ms: 60000\n---\nbody\n",
        file_path="/user/reviewer.md",
        scope="user",
    )
    assert parsed.profile is not None
    harness = make_harness()
    service = SimpleNamespace(active=None, show=lambda name: parsed.profile)
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), agent_service=service)
    try:
        shown = await plane.handle("agents/show", {"name": "reviewer"})
        assert shown["profile"]["timeout_ms"] == 60000
    finally:
        await harness.dispose()


async def test_native_rpc_roster_and_additive_control_coexist():
    harness = make_harness()
    source = asyncio.StreamReader()
    for payload in [{"type": "get_state", "id": "classic"},
                    {"type": "aelix_control", "id": "control", "method": "capabilities", "params": {}}]:
        source.feed_data((json.dumps(payload) + "\n").encode())
    source.feed_eof()
    output = []
    try:
        await run_rpc_mode(harness, stdin=source, stdout_write=output.append, install_signal_handlers=False)
        responses = {value["id"]: value for line in output if (value := json.loads(line)).get("type") == "response"}
        assert responses["classic"]["success"] is True
        assert responses["control"]["data"]["version"] == 1
    finally:
        await harness.dispose()


@pytest.mark.parametrize("servers", [None, [{"name": "a", "command": "python", "env": {}}],
    [{"name": "a", "command": "python"}, {"name": "a", "command": "python"}],
    [{"name": "a", "type": "http", "url": "file:///tmp/test"}],
    [{"name": "a", "type": "http", "url": "https://user:password@example.test"}],
    [{"name": "a", "type": "http", "url": "https://example.test", "headers": [{"name": "x", "value": "a\nb"}]}]])
def test_invalid_mcp_configuration_is_rejected_before_connecting(servers):
    with pytest.raises(ValueError):
        contributions(servers)


def test_mcp_credentials_are_not_serialized_in_metadata():
    value = contributions([{"name": "a", "type": "http", "url": "https://example.test",
                            "headers": [{"name": "Authorization", "value": "secret"}]}])[0]
    assert value.headers == {"Authorization": "secret"}
    assert "secret" not in json.dumps(value.model_dump())


async def test_real_stdio_mcp_has_owned_cleanup_and_can_rebind(tmp_path):
    script = tmp_path / "mcp_fixture.py"
    script.write_text('''from mcp.server.fastmcp import FastMCP
server=FastMCP("fixture")
@server.tool()
def echo(value: str) -> str: return value
server.run()
''')
    controller = RpcMcpController()
    harness, replacement = make_harness(), make_harness()
    try:
        status = await controller.configure([{"name": "fixture", "command": sys.executable, "args": [str(script)]}],
                                            lambda: harness)
        assert status["servers"][0]["connected"] is True
        result = await controller.manager.connections["acp_fixture"].call_tool("echo", {"value": "MCP_OK"})
        assert result.content[0].text == "MCP_OK"
        await controller.attach(replacement)
        assert any(tool.name == "acp_fixture__echo" for tool in replacement.state.tools)
        await controller.configure([], lambda: replacement)
        assert not any(tool.name == "acp_fixture__echo" for tool in replacement.state.tools)
    finally:
        await controller.close()
        await harness.dispose()
        await replacement.dispose()
