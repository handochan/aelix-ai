"""#795, #796 (ADR-0304 D12) — what RPC says and does about the ``agents`` lists.

* ``agents/list``: each profile's ``enabled`` (the APPLIED lists' answer) and
  ``decidedBy``; a host's own service with no ``delegation`` reads enabled;
* ``agents/show``: the profile's ``agents`` key;
* ``settings/get`` / ``settings/set``: ``agent_profiles``, the GLOBAL file's
  ``agents`` list, through ``edit_list``; a global file that did not load is
  refused; the session's view does not move until a reload re-reads the lists;
* ``agents/run`` is not filtered (owner O2): an off profile is spawned;
* ``commands/run`` does not dispatch built-ins, so ``/agents list`` there is
  ``not_a_command`` — RPC builds no ``CommandContext`` to wire.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.agents.service import AgentProfileService
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.core.resource_filter import ScopeList
from aelix_coding_agent.core.resource_selection import SettingsLists
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime

from tests.agents_ext.test_tool_and_security import _bench, _write_profile
from tests.rpc.test_runtime_control_surfaces_594 import _harness


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _service(tmp_path: Path, lists: dict[str, SettingsLists]) -> AgentProfileService:
    agent = tmp_path / "agent"
    _write_profile(agent / "agents" / "reviewer.md", "reviewer", extra="agents: [explorer]")
    (tmp_path / "work").mkdir(exist_ok=True)
    return AgentProfileService(
        cwd=str(tmp_path / "work"),
        project_trusted=False,
        parsed=Args(),
        baseline=Args(),
        skills_holder={},
        agent_dir=str(agent),
        agent_lists=lambda: lists["result"],
    )


async def test_agents_list_carries_enabled_and_decided_by(tmp_path: Path) -> None:
    lists = {"result": SettingsLists(ScopeList("global", ("-reviewer",), str(tmp_path)), None)}
    harness = _harness()
    plane = RpcControlPlane(
        _make_passthrough_runtime(harness, None), agent_service=_service(tmp_path, lists)
    )
    try:
        result = await plane.handle("agents/list", {})
        rows = {p["name"]: (p["enabled"], p["decidedBy"]) for p in result["profiles"]}
        assert rows == {
            "explorer": (True, None),
            "general-purpose": (True, None),
            "reviewer": (False, "global"),
        }
        json.dumps(result)
        shown = await plane.handle("agents/show", {"name": "reviewer"})
        assert shown["profile"]["agents"] == ["explorer"] or shown["profile"]["agents"] == (
            "explorer",
        )
    finally:
        await harness.dispose()


async def test_a_service_with_no_delegation_reads_enabled(tmp_path: Path) -> None:
    profile = SimpleNamespace(name="reviewer", description="R", scope="user", file_path="/r.md")
    service = SimpleNamespace(
        active=None, list=lambda: SimpleNamespace(profiles=[profile], diagnostics=[])
    )
    harness = _harness()
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), agent_service=service)
    try:
        result = await plane.handle("agents/list", {})
        assert result["profiles"][0]["enabled"] is True
        assert result["profiles"][0]["decidedBy"] is None
    finally:
        await harness.dispose()


async def test_settings_get_and_set_carry_the_global_list(tmp_path: Path) -> None:
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir()
    work.mkdir()
    manager = SettingsManager.create(work, agent, project_trusted=False)
    lists = {"result": SettingsLists(ScopeList("global", (), str(agent)), None)}
    harness = _harness()
    plane = RpcControlPlane(
        _make_passthrough_runtime(harness, None),
        settings_manager=manager,
        agent_service=_service(tmp_path, lists),
    )
    try:
        got = await plane.handle("settings/get", {})
        assert got["settings"]["agent_profiles"] == []
        schema = next(s for s in got["schema"] if s["key"] == "agent_profiles")
        assert (schema["type"], schema["requiresReload"]) == ("string_array", True)
        written = await plane.handle(
            "settings/set", {"key": "agent_profiles", "value": ["-reviewer"]}
        )
        assert written == {"key": "agent_profiles", "value": ["-reviewer"], "requiresReload": True}
        on_disk = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
        assert on_disk["agents"] == ["-reviewer"]
        # Saved is not applied: the session's view moves when a reload re-reads.
        listed = await plane.handle("agents/list", {})
        assert {p["name"]: p["enabled"] for p in listed["profiles"]}["reviewer"] is True
    finally:
        await harness.dispose()


async def test_settings_set_refuses_a_global_file_that_did_not_load(tmp_path: Path) -> None:
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir()
    work.mkdir()
    (agent / "settings.json").write_text("{broken", encoding="utf-8")
    manager = SettingsManager.create(work, agent, project_trusted=False)
    harness = _harness()
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), settings_manager=manager)
    try:
        with pytest.raises(ValueError, match="was not saved"):
            await plane.handle("settings/set", {"key": "agent_profiles", "value": ["-x"]})
        assert (agent / "settings.json").read_text(encoding="utf-8") == "{broken"
    finally:
        await harness.dispose()


async def test_agents_run_is_not_filtered(tmp_path: Path) -> None:
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    lists = SettingsLists(ScopeList("global", ("-scout",), str(tmp_path)), None)
    bench.ext.agent_lists = lambda: lists
    harness = _harness()
    harness.runtime._subagents = bench.ext.runtime  # type: ignore[assignment]
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result = await plane.handle("agents/run", {"name": "scout", "task": "go"})
        assert result["result"]["ok"] is True
        assert [plan.resolved.name for plan in bench.channel.plans] == ["scout"]
    finally:
        await harness.dispose()


async def test_commands_run_does_not_dispatch_the_agents_builtin() -> None:
    harness = _harness()
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result: dict[str, Any] = await plane.handle("commands/run", {"text": "/agents list"})
        assert result["outcome"] == "not_a_command"
    finally:
        await harness.dispose()
