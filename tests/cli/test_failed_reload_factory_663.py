"""#663 (ADR-0289) — the product factory under a ``/reload`` that fails past the teardown.

``_async_main`` builds the real factory (Guardrail and Permission prepended, the
skill and prompt catalogs in holders); ``run_tui`` is stubbed so the test drives
``runtime.reload()`` itself, as ``tests/cli/test_extension_launch_25.py`` does.

* A rebuild that raises after the reload rescanned the catalogs leaves them as
  the running session has them, and the build that puts the session back
  (``ReloadSeed.restore``) does not rescan: the restored harness has the skills
  the session ran with, not the ones the failed build read.
* That harness runs every tool call through Guardrail and Permission again: an
  ``rm -rf`` is refused and, with a UI bound, ``default`` mode asks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import aelix_coding_agent.cli.entry as entry_mod
import pytest
from aelix_agent_core.runtime import RebuildFailedError
from aelix_agent_core.types import AgentContext, BeforeToolCallContext
from aelix_ai.messages import AssistantMessage, ToolCallContent

from tests.cli.test_extension_launch_25 import _Manager, _TTYStdin
from tests.env_sandbox import sandbox_home


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agent = tmp_path / "agent"
    (agent / "extensions").mkdir(parents=True)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.setattr(entry_mod, "McpClientManager", _Manager)
    return agent


def _skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: the {name} skill\n---\nbody\n", encoding="utf-8"
    )


async def _call(harness: Any, command: str) -> Any:
    # The built-in tool the factory registered: the gate keys on its provenance.
    assert any(t.name == "bash" for t in harness.state.tools)
    call = ToolCallContent(tool_call_id="t1", tool_name="bash", input={"command": command})
    return await harness._before_tool_call_bridge(
        BeforeToolCallContext(
            assistant_message=AssistantMessage(content=[call], stop_reason="tool_use"),
            tool_call=call,
            args={"command": command},
            context=AgentContext(tools=list(harness.state.tools)),
        )
    )


def _mine(harness: Any) -> list[str]:
    """The test's skills among the harness's (aelix bundles skills of its own)."""

    return sorted(s.name for s in harness.skills if s.name in ("first", "second"))


class _UI:
    """A bound UI that answers tool approvals (``provides_tool_approval``)."""

    provides_tool_approval = True


async def test_a_failed_reload_restores_the_catalogs_and_the_gates(
    agent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = tmp_path / "skills"
    _skill(skills, "first")
    settings = agent / "settings.json"
    settings.write_text(json.dumps({"skills": [str(skills)]}))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "stdin", _TTYStdin())
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kw: Any) -> int:
        seen["launch"] = _mine(runtime.harness)
        # The reload reads a second skill, then its build raises; the build
        # after it (on the previous settings) works.
        _skill(skills, "second")
        real = entry_mod.apply_post_registration_tool_policy
        calls = {"n": 0}

        async def _raise_once(*a: Any, **k: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(13, "Permission denied", "zlocked/inner")
            await real(*a, **k)

        monkeypatch.setattr(entry_mod, "apply_post_registration_tool_policy", _raise_once)
        try:
            await runtime.reload()
        except RebuildFailedError as exc:
            seen["state"] = exc.state
        seen["restored"] = _mine(runtime.harness)
        harness = runtime.harness
        seen["guard"] = await _call(harness, "rm -rf ./no-such-dir-663 && echo GUARD > guard.txt")

        asked: list[Any] = []

        async def _deny(request: Any) -> str:
            asked.append(request)
            return "no"

        permission = kw["permission_ext"]
        permission.approval_runner = _deny
        harness.runtime.bind_ui(_UI())
        seen["after"] = await _call(harness, "echo AFTER > after.txt")
        seen["asked"] = len(asked)
        # Once the cause is gone, /reload reads the second skill.
        monkeypatch.setattr(entry_mod, "apply_post_registration_tool_policy", real)
        await runtime.reload()
        seen["recovered"] = _mine(runtime.harness)
        return 0

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session"])
    assert code == 0
    assert seen["launch"] == ["first"]
    assert seen["state"] == "kept"
    assert seen["restored"] == ["first"]
    assert seen["guard"] is not None and seen["guard"].block
    assert "[guardrail]" in (seen["guard"].reason or "")
    assert seen["after"] is not None and seen["after"].block
    assert seen["asked"] == 1
    assert seen["recovered"] == ["first", "second"]
    assert not (work / "guard.txt").exists() and not (work / "after.txt").exists()


async def test_a_kept_restore_runs_the_model_the_session_was_on(
    agent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex R06: a ``set_model`` choice came back as the launch ``--model``.

    The restore is seeded with the exact model (``ReloadSeed.model``, ``restore``)
    and the factory keeps it, so "kept" is the session as it ran.
    """

    from aelix_ai.models import get_model

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "stdin", _TTYStdin())
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **_kw: Any) -> int:
        chosen = get_model("anthropic", "claude-haiku-4-5")
        assert chosen is not None
        await runtime.harness.set_model(chosen)
        real = entry_mod.apply_post_registration_tool_policy
        calls = {"n": 0}

        async def _raise_once(*a: Any, **k: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first build fails")
            await real(*a, **k)

        monkeypatch.setattr(entry_mod, "apply_post_registration_tool_policy", _raise_once)
        try:
            await runtime.reload()
        except RebuildFailedError as exc:
            seen["state"] = exc.state
        seen["model"] = (runtime.harness.current_model.provider, runtime.harness.current_model.id)
        seen["held"] = runtime.harness.turns_held
        return 0

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(
        ["--no-session", "--provider", "anthropic", "--model", "claude-sonnet-4-5"]
    )
    assert code == 0
    assert seen["state"] == "kept"
    assert seen["model"] == ("anthropic", "claude-haiku-4-5")
    assert seen["held"] is None
