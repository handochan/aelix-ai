"""#779 (R9, the lead's call) — ``job`` and ``job_stop`` count as built-in tools, and RPC/ACP has neither.

With the jobs gate on (shipped since #779), ``JobsExtension`` registered both tools
on every host, and they survived ``--no-builtin-tools``
(``.omc/probes/611-s3-design/gateon/on-cli-ab.txt``, the flip measured with a
scratch plugin). R9: they come from aelix itself, so the run's tool policy counts
them as built-in (``tools.BUILTIN_TOOL_NAMES``, ``cli/entry.py`` ``_tool_policy``):
``--no-builtin-tools`` turns them off, ``--tools`` filters them like any other
name, and ``--no-tools`` turns them off with everything else. On an RPC host —
and so under ACP, which drives aelix over RPC (ADR-0275) — they are not
registered at all until #612, while the job service and ``/jobs`` still are.

The matrix, each row driven through the REAL ``_async_main``:

* ``-p``: no flag (all on), ``--no-builtin-tools``, ``--tools`` with and
  without ``job``, ``--no-tools``; ``--mode json`` and the TUI, no flag (all on, fix-r1);
  then a rebuild (``AgentSessionRuntime.reload``)
  of the ``--no-builtin-tools`` and ``--tools`` runs, whose policy must hold on
  the new harness, whose ``JobsExtension`` registered both tools again;
* RPC, through the real ``run_rpc_mode``: ``new_session`` (a rebuild) between two
  reads; and the ``aelix_control`` envelope ACP uses (ADR-0275):
  ``resources/list``, ``resources/reload`` (a rebuild), ``resources/list``.

Every row runs with the gate on, set by this file (``_jobs_on``), not read from
the shipped value: R9 is what the gate-on build does, and a rollback of the gate
is not a change of R9 (fix-r1, verify r1 F6). Fails on ``bf507d59`` with the gate
on: ``BUILTIN_TOOL_NAMES`` did not exist, and with only that shimmed the
``--no-builtin-tools`` rows had both tools active and the RPC rows had both
registered (verify r1, ``.omc/probes/779/verify-r1/bite/``).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.tools import ALL_TOOL_NAMES, BUILTIN_TOOL_NAMES

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env and wire are fixtures
    _drive_real_rpc,
    _write_models,
    env,
    wire,
)
from tests.env_sandbox import sandbox_home

JOB_TOOLS = {"job", "job_stop"}
_EXT_TOOL = "ext_probe"


class _StopAfterBuild(Exception):
    """Raised by the runtime spy once the first harness exists."""


class _FakePipedStdin:
    """Non-tty stdin → ``_async_main`` resolves to print mode."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture(autouse=True)
def _jobs_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate on, and not switched off at start, whatever the shipped value."""

    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setattr(activation, "_switched_off", False)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A sandboxed home, agent dir and cwd, and an ``-e`` extension registering one tool."""

    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    (tmp_path / "probe_ext.py").write_text(
        textwrap.dedent(
            f"""
            from aelix_agent_core.types import AgentTool

            def setup(aelix):
                aelix.register_tool(AgentTool(name={_EXT_TOOL!r}, description="probe tool"))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    cwd = tmp_path / "proj"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return tmp_path


async def _built(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, *flags: str, mode: tuple[str, ...] = ("--print",)
) -> Any:
    """The REAL ``AgentSessionRuntime`` of a ``-p`` (or ``mode``) launch with ``flags``, stopped before any turn."""

    captured: dict[str, Any] = {}
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        captured["runtime"] = await real_create(harness, factory, **kwargs)
        raise _StopAfterBuild

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    argv = ["--no-session", *mode, "-e", str(scratch / "probe_ext.py")]
    argv += ["--provider", "anthropic", "--model", "probe-model", *flags]
    with pytest.raises(_StopAfterBuild):
        await entry_mod._async_main(argv)
    return captured["runtime"]


def _registered(harness: Any) -> set[str]:
    return {tool.name for tool in harness.state.tools}


def _active(harness: Any) -> set[str]:
    active = harness.state.active_tool_names
    return _registered(harness) if active is None else set(active)


async def _dispose(runtime: Any) -> None:
    await runtime.dispose()
    if activation._service is not None:  # noqa: SLF001 — the service the launch installed
        await activation._service.shutdown()  # noqa: SLF001


def test_the_built_in_set_is_pis_list_and_the_two_job_tools() -> None:
    from aelix_coding_agent.jobs.tool import JOB_STOP_TOOL_NAME, JOB_TOOL_NAME

    assert BUILTIN_TOOL_NAMES - ALL_TOOL_NAMES == {JOB_TOOL_NAME, JOB_STOP_TOOL_NAME} == JOB_TOOLS
    assert ALL_TOOL_NAMES < BUILTIN_TOOL_NAMES


@pytest.mark.parametrize(
    ("flags", "on", "off"),
    [
        pytest.param((), JOB_TOOLS | {"read", _EXT_TOOL}, set(), id="no-flag"),
        pytest.param(("--no-builtin-tools",), {_EXT_TOOL}, JOB_TOOLS | set(ALL_TOOL_NAMES), id="no-builtin-tools"),
        pytest.param(("--tools", f"read,{_EXT_TOOL}"), {"read", _EXT_TOOL}, JOB_TOOLS, id="tools-without-job"),
        pytest.param(("--tools", "read,job"), {"read", "job"}, {"job_stop", _EXT_TOOL}, id="tools-with-job"),
        pytest.param(("--no-tools",), set(), JOB_TOOLS | {"read", _EXT_TOOL}, id="no-tools"),
    ],
)
async def test_a_print_run_registers_both_and_the_tool_flags_treat_them_as_built_in(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...], on: set[str], off: set[str]
) -> None:
    runtime = await _built(scratch, monkeypatch, *flags)
    try:
        harness = runtime.harness
        assert _registered(harness) >= JOB_TOOLS, "registered: a flag locks them, it does not remove them"
        assert on <= _active(harness), sorted(_active(harness))
        assert not off & _active(harness), sorted(_active(harness))
        if "--no-builtin-tools" in flags:
            lock = harness.tool_policy.lock("job")
            assert lock is not None and lock.value is False and "built-in" in lock.reason
    finally:
        await _dispose(runtime)


async def test_a_json_run_registers_and_activates_both(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--mode json`` is a host that gets them, as ``-p`` is (verify r1 F2: the host list names it).

    Fails with the host condition narrowed to drop json (verify r1's mutant M5,
    ``tools=app_mode not in ("rpc", "json")``): neither tool is registered.
    """

    runtime = await _built(scratch, monkeypatch, mode=("--mode", "json"))
    try:
        harness = runtime.harness
        assert _registered(harness) >= JOB_TOOLS
        assert _active(harness) >= JOB_TOOLS
    finally:
        await _dispose(runtime)


class _FakeTTYStdin:
    """A TTY stdin with no ``--print``: ``_async_main`` resolves to the TUI."""

    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover — never read on a TTY
        return ""


async def test_the_tui_host_registers_and_activates_both(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The TUI is a host that gets them (fix-r1, F10: the hosts are named, so a new mode gets none).

    ``run_tui`` is a stand-in that reads the tools of the harness the router hands it.
    """

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    seen: dict[str, set[str]] = {}

    async def _stub_run_tui(runtime: Any, *, cwd: str, **_rest: Any) -> int:
        seen["registered"] = _registered(runtime.harness)
        seen["active"] = _active(runtime.harness)
        return 0

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    argv = ["--no-session", "-e", str(scratch / "probe_ext.py"), "--provider", "anthropic", "--model", "probe-model"]
    assert await entry_mod._async_main(argv) == 0
    assert seen["registered"] >= JOB_TOOLS and seen["active"] >= JOB_TOOLS


@pytest.mark.parametrize(
    ("flags", "on", "off"),
    [
        pytest.param(("--no-builtin-tools",), {_EXT_TOOL}, JOB_TOOLS, id="no-builtin-tools"),
        pytest.param(("--tools", "read,job"), {"read", "job"}, {"job_stop"}, id="tools-with-job"),
    ],
)
async def test_a_rebuild_registers_them_again_under_the_same_policy(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...], on: set[str], off: set[str]
) -> None:
    runtime = await _built(scratch, monkeypatch, *flags)
    try:
        before = runtime.harness
        await runtime.reload()
        rebuilt = runtime.harness
        assert rebuilt is not before
        assert _registered(rebuilt) >= JOB_TOOLS
        assert on <= _active(rebuilt), sorted(_active(rebuilt))
        assert not off & _active(rebuilt), sorted(_active(rebuilt))
    finally:
        await _dispose(runtime)


def _control(request_id: str, method: str) -> dict[str, Any]:
    return {"id": request_id, "type": "aelix_control", "method": method, "params": {}}


async def test_rpc_and_acp_register_neither_across_rebuilds_and_keep_the_jobs_command(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPC's own ``new_session`` and ACP's envelope (``resources/list``, ``resources/reload``)."""

    _write_models(env, "rec")
    responses = _drive_real_rpc(
        monkeypatch,
        [
            _control("l1", "resources/list"),
            {"type": "new_session", "id": "n1"},
            _control("l2", "resources/list"),
            _control("r1", "resources/reload"),
            _control("l3", "resources/list"),
        ],
    )
    code = await entry_mod._async_main(["--model", "rec/m1", "--mode", "rpc"])
    assert code == 0
    assert [r["success"] for r in responses] == [True] * 5, responses
    for listed in (responses[0], responses[2], responses[4]):
        data = listed["data"]
        tools = {t["name"] for t in data["tools"]}
        assert "bash" in tools, sorted(tools)
        assert not JOB_TOOLS & tools, sorted(tools)
        assert not JOB_TOOLS & set(data["activeTools"])
        assert "jobs" in {c["name"] for c in data["commands"]}, "the service and /jobs are built on RPC"
