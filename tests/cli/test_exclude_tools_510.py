"""#510 (ADR-0283) — ``--exclude-tools``, the tool locks and ``defaultTools`` at a real launch.

Driven through the REAL ``_async_main`` (the factory is a closure only it
builds), stopped at the first harness build as ``test_no_builtin_tools.py``
does, with one ``-e`` extension tool and one fake MCP tool. Pins:

* an excluded tool is not in the registry, the prompt or the wire, and a call
  to it fails as an unknown tool — built-in, extension and MCP alike;
* ``--tools bash -xt bash`` gives no ``bash`` (the exclusion wins);
* M1 — ``--no-tools`` then ``/reload`` turned tools back on; it no longer does;
* M2 — ``--tools read`` then ``/reload`` unioned every extension tool in; under
  owner decision 1 it no longer does;
* a saved ``-bash`` keeps ``bash`` registered and off, and survives ``/reload``;
* an extension cannot turn on what a flag or the setting keeps off.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, ToolResultMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.cli import entry as entry_mod

_EXT_TOOL = "ext_probe"
_MCP_TOOL = "probe__ping"
_BUILT = 42


class _StopAfterBuild(Exception):
    pass


class _FakePipedStdin:
    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    mcp_config = tmp_path / "mcp.json"
    mcp_config.write_text('{"mcpServers": {"probe": {"command": "echo"}}}')
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(mcp_config))

    class _FakeMcpManager:
        def __init__(self, contribs: Any, **_kwargs: Any) -> None:
            self.contribs = contribs

        async def connect_all(self) -> list[str]:
            return []

        async def collect_agent_tools(self) -> list[AgentTool]:
            return [AgentTool(name=_MCP_TOOL, description="fake mcp tool")]

        async def disconnect_all(self) -> None:
            return None

    monkeypatch.setattr(entry_mod, "McpClientManager", _FakeMcpManager)
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _extension(tmp_path: Path, *, widen: bool = False) -> Path:
    """An ``-e`` extension with one tool; *widen* makes it ask for every tool at session_start."""

    ext = tmp_path / "probe_ext.py"
    body = f"""
        from aelix_agent_core.types import AgentTool

        def setup(aelix):
            aelix.register_tool(AgentTool(name={_EXT_TOOL!r}, description="probe tool"))
    """
    if widen:
        body += """
            async def _widen(event, ctx):
                aelix.set_active_tools([t.name for t in aelix.get_all_tools()] + ["bash"])
            aelix.on("session_start", _widen)
        """
    ext.write_text(textwrap.dedent(body).strip() + "\n")
    return ext


async def _launch(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.chdir(cwd)
    real = entry_mod.create_agent_session_runtime
    captured: dict[str, Any] = {}

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        captured["runtime"] = await real(harness, factory, **kwargs)
        captured["harness"] = harness
        raise _StopAfterBuild

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    try:
        code = await entry_mod._async_main(argv)
    except _StopAfterBuild:
        code = _BUILT
    captured["code"] = code
    return captured


def _argv(ext: Path, *extra: str) -> list[str]:
    return [
        "--no-session",
        "--print",
        "-e",
        str(ext),
        "--provider",
        "anthropic",
        "--model",
        "probe-model",
        *extra,
    ]


def _registry(h: Any) -> set[str]:
    return {t.name for t in h.state.tools}


def _active(h: Any) -> set[str]:
    return set(h._action_get_active_tools())


def _listed(prompt: str) -> list[str]:
    match = re.search(r"Available tools:\n(.*?)\n\n", prompt, re.S)
    assert match is not None
    body = match.group(1)
    return [] if body.strip() == "(none)" else [ln.split(":", 1)[0][2:] for ln in body.splitlines()]


@pytest.mark.parametrize(
    ("pattern", "gone"),
    [("bash", "bash"), (_EXT_TOOL, _EXT_TOOL), ("probe__*", _MCP_TOOL)],
    ids=["built-in", "extension", "mcp-pattern"],
)
async def test_an_excluded_tool_is_not_registered_listed_or_callable(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str, gone: str
) -> None:
    run = await _launch(_argv(_extension(tmp_path), "-xt", pattern), _env, monkeypatch)
    assert run["code"] == _BUILT
    h = run["harness"]
    assert gone not in _registry(h)
    assert gone in {t.name for t, _r in h.excluded_tools()}
    assert gone not in _listed(h.state.system_prompt)
    assert {"read", _EXT_TOOL, _MCP_TOOL} - {gone} <= _active(h)


async def test_the_wire_the_prompt_and_the_callable_set_agree_and_a_call_fails_cleanly(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A placeholder key passes the launch's credential gate; the stream below is
    # injected, so nothing is sent anywhere.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    run = await _launch(_argv(_extension(tmp_path), "-xt", "bash"), _env, monkeypatch)
    h = run["harness"]
    seen: list[dict[str, Any]] = []

    async def _stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(
            {"tools": [t.name for t in context.tools or []], "prompt": context.system_prompt}
        )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if len(seen) == 1:
            message = AssistantMessage(
                content=[
                    ToolCallContent(tool_call_id="c1", tool_name="bash", input={"command": "ls"})
                ],
                stop_reason="tool_use",
            )
        else:
            message = AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        yield AssistantEndEvent(message=message)

    h._options = dataclasses.replace(h._options, stream_fn=_stream)
    await h.prompt("run ls")
    wire = seen[0]["tools"]
    assert "bash" not in wire
    assert set(wire) == _active(h)
    listed = _listed(seen[0]["prompt"])
    assert "bash" not in listed and set(listed) <= set(wire)
    results = [m for m in h.state.messages if isinstance(m, ToolResultMessage)]
    assert results and results[0].is_error


async def test_an_exclusion_beats_tools(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _launch(
        _argv(_extension(tmp_path), "--tools", "bash,read", "-xt", "bash"), _env, monkeypatch
    )
    assert run["code"] == _BUILT
    assert _active(run["harness"]) == {"read"}


async def test_m1_no_tools_stays_off_through_reload(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _launch(_argv(_extension(tmp_path), "--no-tools"), _env, monkeypatch)
    assert _active(run["harness"]) == set()
    runtime = run["runtime"]
    await runtime.reload()
    assert _active(runtime.harness) == set()
    assert _listed(runtime.harness.state.system_prompt) == []


async def test_m2_tools_is_a_lock_through_reload(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _launch(_argv(_extension(tmp_path), "--tools", "read"), _env, monkeypatch)
    assert _active(run["harness"]) == {"read"}
    runtime = run["runtime"]
    await runtime.reload()
    assert _active(runtime.harness) == {"read"}


async def test_a_saved_off_tool_is_registered_inactive_and_survives_reload(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps({"defaultTools": ["-bash", f"-{_EXT_TOOL}"]})
    )
    run = await _launch(_argv(_extension(tmp_path)), _env, monkeypatch)
    h = run["harness"]
    assert {"bash", _EXT_TOOL} <= _registry(h)
    assert not {"bash", _EXT_TOOL} & _active(h)
    assert "bash" not in _listed(h.state.system_prompt)
    runtime = run["runtime"]
    await runtime.reload()
    assert not {"bash", _EXT_TOOL} & _active(runtime.harness)
    # The setting changed on disk and /reload re-reads it: bash comes back.
    (tmp_path / "settings.json").write_text(json.dumps({"defaultTools": [f"-{_EXT_TOOL}"]}))
    await runtime.reload()
    assert "bash" in _active(runtime.harness) and _EXT_TOOL not in _active(runtime.harness)


@pytest.mark.parametrize(
    ("flags", "settings", "off"),
    [
        (["-xt", "bash"], None, "bash"),
        (["--tools", f"read,{_EXT_TOOL}"], None, "bash"),
        (["--no-builtin-tools"], None, "bash"),
        ([], {"defaultTools": ["-bash"]}, "bash"),
    ],
    ids=["excluded", "tools", "no-builtin", "saved-off"],
)
async def test_an_extension_cannot_widen_past_a_flag_or_the_setting(
    _env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    settings: dict[str, Any] | None,
    off: str,
) -> None:
    if settings is not None:
        (tmp_path / "settings.json").write_text(json.dumps(settings))
    run = await _launch(_argv(_extension(tmp_path, widen=True), *flags), _env, monkeypatch)
    h = run["harness"]
    h.runtime.actions.set_active_tools([t.name for t in h.state.tools])
    assert off not in _active(h)


# --- a whole launch, the mode entry stubbed -----------------------------------------


async def _rpc_session(
    argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch, steps: Any
) -> dict[str, Any]:
    """Launch ``--mode rpc`` for real and run *steps* where the RPC loop would start.

    Unlike :func:`_launch` the launch runs to the mode dispatch, so the runtime
    host is wired (``/agents use``, the delegation host's live reads) as a
    session has it. *steps* gets the harness, the runtime host and the services.
    """

    from aelix_coding_agent import modes

    monkeypatch.chdir(cwd)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    seen: dict[str, Any] = {}

    async def _rpc(
        harness: Any,
        *,
        model_registry: Any,
        runtime_host: Any,
        harness_factory: Any,
        **services: Any,
    ) -> None:
        seen["result"] = await steps(harness, runtime_host, services)

    monkeypatch.setattr(modes, "run_rpc_mode", _rpc)
    code = await entry_mod._async_main(["--mode", "rpc", *argv])
    assert code == 0
    return seen


async def test_a_child_is_started_without_what_the_parents_settings_keep_off(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner decision B: the saved-off set reaches the child as ``-xt``, live, and its registry.

    A child reads its own settings — an untrusted one never the project's — so
    an extension tool turned off only in the parent's files would come back in
    it. The host's read follows the parent's policy: a tool turned back on is
    no longer forwarded.
    """

    import aelix_agents
    from aelix_agents.print_channel import build_child_argv, narrow_tools
    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.cli.args import parse_args
    from aelix_coding_agent.rpc.control import RpcControlPlane
    from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime

    (tmp_path / "settings.json").write_text(json.dumps({"defaultTools": [f"-{_EXT_TOOL}", "-ls"]}))
    made: list[Any] = []
    real = aelix_agents.AgentsExtension

    def _spy(**kwargs: Any) -> Any:
        made.append(real(**kwargs))
        return made[-1]

    monkeypatch.setattr(aelix_agents, "AgentsExtension", _spy)
    ext = _extension(tmp_path)

    async def _steps(harness: Any, _runtime: Any, _services: Any) -> dict[str, Any]:
        host = made[0]._host()
        out = {"saved": host.exclude_tools()}
        plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
        await plane.handle(
            "tools/set_active", {"names": [*harness._action_get_active_tools(), "ls"]}
        )
        out["after_rpc_on"] = host.exclude_tools()
        return out

    seen = await _rpc_session(
        [
            "--no-session",
            "--agents",
            "-e",
            str(ext),
            "-xt",
            "bash",
            "--provider",
            "anthropic",
            "--model",
            "probe-model",
        ],
        _env,
        monkeypatch,
        _steps,
    )
    assert seen["result"]["saved"] == ("bash", _EXT_TOOL, "ls")
    assert seen["result"]["after_rpc_on"] == ("bash", _EXT_TOOL)  # ls is on again: not forwarded
    # The child, launched from the argv a spawn builds, lacks the extension tool.
    profile = AgentProfile(
        name="scout", description="d", body="b", file_path="/p/scout.md", scope="user"
    )
    argv = build_child_argv(
        narrow_tools(profile, None, seen["result"]["after_rpc_on"]).profile,
        prompt_path=str(tmp_path / "p.md"),
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(_env),
        parent_cwd=str(_env),
    )
    forwarded = parse_args(argv[4:]).exclude_tools  # after python -P -m aelix_coding_agent
    assert forwarded == ["bash", _EXT_TOOL]
    # The child's own settings do not say it (an untrusted child never reads the
    # project's file): only the forwarded flag keeps the tool out.
    (tmp_path / "settings.json").write_text("{}")
    child = await _launch(_argv(ext, "-xt", ",".join(forwarded)), _env, monkeypatch)
    assert _EXT_TOOL not in _registry(child["harness"])
    assert _EXT_TOOL in {t.name for t, _r in child["harness"].excluded_tools()}
    assert "ls" in _active(child["harness"])


async def test_agents_use_installs_the_profiles_tools_as_a_lock_and_none_lifts_it(
    _env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V3: ``/agents use lean`` (``tools: read, grep``) is a lock, not a selection.

    An extension cannot widen it, a client cannot turn ``ls`` on, a failed
    ``/agents use bad`` rolls back to the lean lock, and ``--none`` lifts it.
    """

    from aelix_coding_agent.rpc.control import RpcControlPlane
    from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime

    agents = tmp_path / "agent" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "lean.md").write_text(
        "---\nname: lean\ndescription: Lean.\ntools: read, grep\n---\nYou are lean.\n"
    )
    (agents / "bad.md").write_text(
        "---\nname: bad\ndescription: Bad.\ntools: read, nonexistent_tool\n---\nBad.\n"
    )
    ext = _extension(tmp_path)

    async def _steps(harness: Any, _runtime: Any, services: Any) -> dict[str, Any]:
        service = services["agent_service"]
        plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
        out: dict[str, Any] = {}
        await service.use("lean", harness=harness)
        out["lean"] = harness._action_get_active_tools()
        harness.runtime.actions.set_active_tools([t.name for t in harness.state.tools])
        out["widened"] = harness._action_get_active_tools()
        with pytest.raises(
            ValueError, match="ls is locked off for this run: not named by the agent profile"
        ):
            await plane.handle("tools/set_active", {"names": ["read", "ls"]})
        with pytest.raises(Exception, match="nonexistent_tool"):
            await service.use("bad", harness=harness)
        out["after_bad"] = harness._action_get_active_tools()
        with pytest.raises(ValueError, match="not named by the agent profile"):
            await plane.handle("tools/set_active", {"names": ["read", "ls"]})
        await service.use(None, harness=harness)
        out["none"] = harness._action_get_active_tools()
        return out

    seen = await _rpc_session(
        ["--no-session", "-e", str(ext), "--provider", "anthropic", "--model", "probe-model"],
        _env,
        monkeypatch,
        _steps,
    )
    result = seen["result"]
    assert result["lean"] == ["read", "grep"]
    assert result["widened"] == ["read", "grep"]
    assert result["after_bad"] == ["read", "grep"]
    assert {"read", "grep", "ls", "bash", _EXT_TOOL} <= set(result["none"])


@pytest.mark.parametrize(
    ("flags", "settings"),
    [(["-xt", "bash"], None), ([], {"defaultTools": ["-bash"]})],
    ids=["excluded", "saved-off"],
)
async def test_the_first_builds_prompt_is_composed_without_what_the_policy_keeps_off(
    _env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    settings: dict[str, Any] | None,
) -> None:
    """Verify V9: the options the FIRST build composes, before the post-registration rebuild.

    The factory rebuilds the prompt once extensions register, which hides this
    list from every later check; a prompt composed from the unfiltered tools
    is what any path that keeps the first prompt would send.
    """

    if settings is not None:
        (tmp_path / "settings.json").write_text(json.dumps(settings))
    composed: list[list[str]] = []
    real = entry_mod._resolve_system_prompt

    def _spy(*args: Any, **kwargs: Any) -> Any:
        composed.append([t.name for t in kwargs.get("tools") or []])
        return real(*args, **kwargs)

    monkeypatch.setattr(entry_mod, "_resolve_system_prompt", _spy)
    run = await _launch(_argv(_extension(tmp_path), *flags), _env, monkeypatch)
    assert run["code"] == _BUILT
    assert composed and "read" in composed[0]
    assert "bash" not in composed[0]
