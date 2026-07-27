"""ADR-0197 §(a)/§(b)/§(d) — when the bundled delegation extension loads.

Three independent gates decide whether ``aelix_agents`` is prepended onto a
harness build, and each one fails SILENTLY in the dangerous direction if it is
mis-wired:

* the **flag** (``--no-agents`` > ``--agents`` > global ``[features] agents``,
  default False) — a broken precedence turns delegation on for users who never
  asked for it;
* the **depth guard** (``subagent_depth() < MAX_SUBAGENT_DEPTH``) — without it a
  delegated child loads the extension and P2 is a fork bomb;
* the **order** (Guardrail, then Permission, then us) — ``entry.py``'s prepend
  comment documents Guardrail-first as a security invariant, and our ``tool_call``
  handler carrying the spawn-time consent gate has to run LAST so a guardrail
  hard-deny and a permission denial both win over it (first-block-wins,
  ``harness/hooks.py::_reducer_tool_call``).

The prepend list is observed by spying on ``discover_and_load_extensions``
rather than by inspecting the loaded ``Extension`` objects: the list literal IS
the assertion target (order and membership), and the loader flattens it into
opaque wrappers.

The last test is a REAL child interpreter. ``tests/conftest.py``'s autouse
download guard is an in-process ``monkeypatch.setattr`` and does not reach a
child (finding I10), so that test sets ``HOME`` / ``XDG_CONFIG_HOME`` /
``AELIX_CODING_AGENT_DIR`` under ``tmp_path`` and ``PI_OFFLINE=1`` explicitly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_ai.settings import FileSettingsStorage, SettingsManager
from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.cli import entry
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.subagent_contract import DEPTH_ENV_VAR

REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIRS = (
    REPO_ROOT / "packages" / "aelix-coding-agent" / "src",
    REPO_ROOT / "packages" / "aelix-agent-core" / "src",
    REPO_ROOT / "packages" / "aelix-ai" / "src",
)


class _Sentinel:
    """Stands in for ``AgentsExtension`` wherever only PLACEMENT is under test.

    A real extension would bind the subagent seam and register a tool — both
    irrelevant to "is it in the prepend list, and where", and both extra ways for
    an unrelated regression to redden these tests.
    """

    def __call__(self, aelix: Any) -> None:  # pragma: no cover - never invoked
        raise AssertionError("the sentinel must never be loaded")


async def _prepend_for(
    monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> list[Any]:
    """Run ``_build_harness_options`` and return the ``prepend=`` list it built."""

    captured: dict[str, list[Any]] = {}
    real = entry.discover_and_load_extensions

    async def _spy(*args: Any, **kw: Any) -> Any:
        captured["prepend"] = list(kw.get("prepend") or [])
        # The sentinel is not a loadable factory, so drop the prepend before
        # delegating: this spy observes the LIST, it does not exercise loading.
        kw["prepend"] = []
        return await real(*args, **kw)

    monkeypatch.setattr(entry, "discover_and_load_extensions", _spy)
    await entry._build_harness_options(
        Args(no_extensions=True), Session(MemorySessionStorage()), **kwargs
    )
    return captured["prepend"]


def _manager(tmp_path: Path, *, enabled: bool | None) -> SettingsManager:
    """A real :class:`SettingsManager` over a scratch global settings dir.

    Real rather than a stub so these tests break if ``get_features_agents``
    is renamed or its default flips — the precedence helper is only as good as
    the getter it calls.
    """

    agent_dir = tmp_path / "agent"
    project_dir = tmp_path / "project"
    agent_dir.mkdir(exist_ok=True)
    project_dir.mkdir(exist_ok=True)
    manager = SettingsManager.from_storage(
        FileSettingsStorage(project_dir, agent_dir)
    )
    if enabled is not None:
        manager.set_features_agents(enabled)
    return manager


# === the flag: --no-agents > --agents > [features] agents (default False) ====


def test_default_is_off_with_no_flag_and_no_settings() -> None:
    """The P2 default. Delegation is opt-in through Phase 3 (spec §8)."""

    assert entry._agents_delegation_enabled(Args(), None) is False


def test_not_prepended_when_flag_off(tmp_path: Path) -> None:
    assert entry._agents_delegation_enabled(Args(), _manager(tmp_path, enabled=None)) is False
    assert (
        entry._agents_delegation_enabled(Args(), _manager(tmp_path, enabled=False))
        is False
    )


def test_prepended_when_flag_on(tmp_path: Path) -> None:
    manager = _manager(tmp_path, enabled=True)
    assert entry._agents_delegation_enabled(Args(), manager) is True


def test_no_agents_flag_overrides_settings_on(tmp_path: Path) -> None:
    """A run-scoped kill switch must beat a persisted enable."""

    manager = _manager(tmp_path, enabled=True)
    parsed = Args(agents_override=False)
    assert entry._agents_delegation_enabled(parsed, manager) is False


def test_agents_flag_overrides_settings_off(tmp_path: Path) -> None:
    manager = _manager(tmp_path, enabled=False)
    parsed = Args(agents_override=True)
    assert entry._agents_delegation_enabled(parsed, manager) is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--agents", "--no-agents"],
        ["--no-agents", "--agents"],
        ["--no-agents", "--agents", "--agents"],
        ["--agents", "--no-agents", "--agents"],
    ],
)
def test_no_agents_is_sticky_against_a_later_agents(argv: list[str]) -> None:
    """MEDIUM #11 — the documented precedence is now the implemented one.

    ``_agents_delegation_enabled``'s docstring and ``args.py``'s comment both
    say ``--no-agents`` > ``--agents``, but the parse loop assigned in both
    branches, so the real rule was last-flag-wins. Measured before the fix, with
    the global setting ON::

        ['--agents', '--no-agents']  -> False
        ['--no-agents', '--agents']  -> True   <- doc says False

    A wrapper script or shell alias pinning ``--no-agents`` could therefore be
    re-opened by a later ``--agents`` with no diagnostic. Off wins now, wherever
    the two appear and however many times.
    """

    from aelix_coding_agent.cli.args import parse_args

    parsed = parse_args(argv)
    assert parsed.agents_override is False
    assert entry._agents_delegation_enabled(parsed, None) is False


def test_a_lone_agents_flag_still_turns_delegation_on() -> None:
    """The other half: stickiness must not make ``--agents`` inert."""

    from aelix_coding_agent.cli.args import parse_args

    parsed = parse_args(["--agents"])
    assert parsed.agents_override is True
    assert entry._agents_delegation_enabled(parsed, None) is True


def test_override_wins_even_with_no_settings_manager() -> None:
    """``None`` manager is not a veto — the flag is still authoritative.

    Embedders and tests reach ``_async_main`` without a manager; the tri-state
    must not collapse to "off" for them when the user typed ``--agents``.
    """

    assert entry._agents_delegation_enabled(Args(agents_override=True), None) is True
    assert entry._agents_delegation_enabled(Args(agents_override=False), None) is False


def test_project_settings_cannot_enable_delegation(tmp_path: Path) -> None:
    """The security half, asserted through the helper the CLI actually calls.

    ``tests/settings_manager`` pins the getter's global-only scope; this pins
    that ``cli/entry.py`` reads THAT getter and not the merged view — a one-word
    slip there would re-open the hole one layer up.
    """

    agent_dir = tmp_path / "agent"
    project_dir = tmp_path / "project"
    agent_dir.mkdir()
    (project_dir / ".aelix").mkdir(parents=True)
    (project_dir / ".aelix" / "settings.json").write_text(
        json.dumps({"features": {"agents": True}}), encoding="utf-8"
    )
    manager = SettingsManager.from_storage(
        FileSettingsStorage(project_dir, agent_dir)
    )

    assert entry._agents_delegation_enabled(Args(), manager) is False


# === the prepend list ========================================================


async def test_agents_ext_none_leaves_the_prepend_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegation off must change NOTHING about a P1 harness build."""

    prepend = await _prepend_for(monkeypatch, agents_ext=None)

    assert len(prepend) == 2
    assert isinstance(prepend[0], GuardrailExtension)
    assert isinstance(prepend[1], PermissionExtension)


async def test_prepend_order_guardrail_permission_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guardrail, permission, THEN us — appended, never inserted."""

    sentinel = _Sentinel()
    prepend = await _prepend_for(monkeypatch, agents_ext=sentinel)

    assert len(prepend) == 3
    assert isinstance(prepend[0], GuardrailExtension)
    assert isinstance(prepend[1], PermissionExtension)
    assert prepend[2] is sentinel


async def test_the_held_permission_ext_is_still_the_one_prepended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding a third entry must not have broken the hold-the-ref invariant.

    A fresh per-rebuild ``PermissionExtension`` silently resets the posture to
    DEFAULT and loses the session-approve set (ADR-0157) — the regression this
    hunk is most likely to have introduced by rewriting the list literal.
    """

    held = PermissionExtension()
    prepend = await _prepend_for(
        monkeypatch, permission_ext=held, agents_ext=_Sentinel()
    )

    assert prepend[1] is held


# === the depth guard =========================================================


async def test_depth_gate_suppresses_in_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At ``MAX_SUBAGENT_DEPTH`` the extension is not even loaded (§(d) layer 1)."""

    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    prepend = await _prepend_for(monkeypatch, agents_ext=_Sentinel())

    assert len(prepend) == 2
    assert all(not isinstance(e, _Sentinel) for e in prepend)


async def test_depth_zero_still_prepends(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discriminating control for the test above."""

    monkeypatch.setenv(DEPTH_ENV_VAR, "0")
    prepend = await _prepend_for(monkeypatch, agents_ext=_Sentinel())

    assert isinstance(prepend[2], _Sentinel)


async def test_a_malformed_depth_is_treated_as_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage in the env var must not silently DISABLE delegation.

    ``subagent_depth`` maps anything unparseable to 0 ("I am the root"), which is
    the restrictive direction for every downstream consumer *except* this one —
    so pin the direction rather than assume it.
    """

    monkeypatch.setenv(DEPTH_ENV_VAR, "not-a-number")
    prepend = await _prepend_for(monkeypatch, agents_ext=_Sentinel())

    assert isinstance(prepend[2], _Sentinel)


# === the real child ==========================================================

_CHILD_PROBE = """
import asyncio, json, sys
from aelix_agent_core.harness.core import AgentHarness
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options
from aelix_agents import AgentsExtension


async def main() -> None:
    opts = await _build_harness_options(
        Args(no_extensions=True, no_context_files=True),
        Session(MemorySessionStorage()),
        agents_ext=AgentsExtension(),
    )
    harness = AgentHarness(opts)
    print(json.dumps(sorted(t.name for t in harness.state.tools)))


asyncio.run(main())
"""


def _run_probe(tmp_path: Path, depth: str) -> list[str]:
    home = tmp_path / f"home-{depth}"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    agent_dir = home / "agent"
    agent_dir.mkdir(exist_ok=True)
    work = tmp_path / f"work-{depth}"
    work.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": ":".join(str(p) for p in _SRC_DIRS),
        # I10 — the autouse download guard is in-process only, so the child gets
        # its hermeticity from an explicit env instead.
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "AELIX_CODING_AGENT_DIR": str(agent_dir),
        "PI_OFFLINE": "1",
        DEPTH_ENV_VAR: depth,
    }
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", textwrap.dedent(_CHILD_PROBE)],
        capture_output=True,
        text=True,
        cwd=str(work),
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_child_process_has_no_agent_tool(tmp_path: Path) -> None:
    """A REAL interpreter at depth 1 builds a harness with no ``agent`` tool.

    The end-to-end version of the depth guard: the env var is genuinely
    inherited (not monkeypatched), the extension is genuinely constructed, and
    the assertion is on the harness's live tool registry rather than on a list
    literal. Both of ``entry.py``'s suppression and ``bind_subagents``'s seam
    refusal (finding I4) are in force here, and the union is what must hold.
    """

    assert "agent" not in _run_probe(tmp_path, "1")


def test_root_process_does_have_an_agent_tool(tmp_path: Path) -> None:
    """The discriminating control — without it the test above proves nothing."""

    assert "agent" in _run_probe(tmp_path, "0")
