"""Invariants the RPC sprint depends on that nothing else asserts.

Each one was found by measurement to be UNGUARDED — mutate the source and the
whole suite stays green. They are grouped here rather than scattered because
what they have in common is the failure mode, not the subsystem: a claim the
codebase makes in prose, relies on elsewhere, and never checks.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time

import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions
from aelix_coding_agent.subagent_contract import ResolvedProfile


def _resolved() -> ResolvedProfile:
    profile = AgentProfile(
        name="scout",
        description="Reads things.",
        body="You are a scout.",
        file_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
        tools=("read",),
    )
    return ResolvedProfile(
        name=profile.name,
        profile=profile,
        source_path=profile.file_path,
        scope=profile.scope,
    )


def _grant() -> SpawnGrant:
    return SpawnGrant(
        consented=True,
        mode=PermissionMode.PLAN,
        profile="scout",
        source_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
        widened=False,
    )


class _NeverRuns:
    """A channel that fails loudly if the topology guard ever lets it through."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, plan: object, **_kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("the topology guard admitted a non-single spawn")


def _runtime(channel: object | None = None) -> _SubagentRuntimeImpl:
    return _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: "/tmp"),
        channel=channel or _NeverRuns(),  # type: ignore[arg-type]
    )


# === ONE CALL IS ONE CHILD ==================================================
#
# MEASURED UNGUARDED: relaxing ``_reject_unsupported`` to admit
# ``("single", "parallel", "chain")`` left the whole ``tests/agents/`` suite
# green. And the sprint's own recon file still tells an implementer to relax it
# ("Relax _reject_unsupported, which currently raises for any mode != 'single'
# ... This is where P3 items 1 and 2 land") — advice P3 deliberately did NOT
# take. So the most likely reader of that note would break the invariant with
# zero test signal, guided by a document.


@pytest.mark.parametrize("mode", ["parallel", "chain"])
async def test_a_non_single_topology_is_refused_at_both_doors(mode: str) -> None:
    """Parallel and chain are composed ABOVE this layer, one child per member.

    P3 shipped both topologies and this still raises, deliberately: the batch
    executor calls the door once per member with ``mode="single"``. A door that
    accepted a topology would have to fan out itself, which is exactly the
    "one call, many children" shape the registry, the live cap and the reaper
    are not built for.
    """

    runtime = _runtime()
    with pytest.raises(ValueError, match="not a per-spawn topology"):
        await runtime.spawn_granted(
            _grant(), _resolved(), "task", mode=mode  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="not a per-spawn topology"):
        await runtime.spawn(_resolved(), "task", mode=mode)  # type: ignore[arg-type]


async def test_a_background_spawn_is_refused_at_both_doors() -> None:
    """Foreground only. A background child has nobody to report to.

    The runtime deregisters its row when the delegation returns, so a child
    that outlives the call is invisible to ``list`` / ``status`` / ``stop`` /
    ``stop_all`` and to session teardown.
    """

    runtime = _runtime()
    with pytest.raises(ValueError):
        await runtime.spawn_granted(_grant(), _resolved(), "task", background=True)
    with pytest.raises(ValueError):
        await runtime.spawn(_resolved(), "task", background=True)


def test_the_guard_is_still_wired_into_both_doors() -> None:
    """The parametrised tests above prove the behaviour; this pins the WIRING.

    A refactor that inlined the check into one door and forgot the other would
    keep every test above green through whichever door it kept.
    """

    from aelix_agents import runtime as rt

    source = inspect.getsource(rt)
    assert source.count("_reject_unsupported(mode, background=background)") == 2


# === The rpc client's worst-case teardown ===================================


async def test_stop_is_bounded_by_the_documented_worst_case() -> None:
    """SIGTERM grace + the final reap, and nothing open-ended.

    Both halves are needed and neither is obvious from the code: a child that
    ignores SIGTERM costs ``SHUTDOWN_SIGTERM_TIMEOUT_MS`` before the escalation,
    and the post-SIGKILL reap is bounded at 5 s so an unkillable child cannot
    hold the caller forever. A delegation channel calls this on every teardown
    path, so an unbounded leg here is an unbounded leg there.
    """

    stub = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        'sys.stderr.write("armed\\n"); sys.stderr.flush()\n'
        "while True: time.sleep(0.5)\n"
    )
    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", stub]))
    await client.start()
    for _ in range(200):
        if "armed" in client.get_stderr():
            break
        await asyncio.sleep(0.05)
    assert "armed" in client.get_stderr(), "the stub never armed its handler"

    started = time.monotonic()
    await client.stop()
    elapsed = time.monotonic() - started

    worst_case = RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0 + 5.0
    assert elapsed <= worst_case, f"stop() took {elapsed:.2f}s, over {worst_case:.2f}s"
    # And it really did have to escalate — otherwise this pins nothing.
    assert elapsed >= RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0


# === The rpc wire, against the REAL child ===================================


async def test_the_real_rpc_child_answers_the_client_it_ships_with(
    tmp_path: object,
) -> None:
    """Conformance: the shipped client, the shipped server, no stub in between.

    Every other rpc client test overrides the argv, and they have to — the
    default points at the umbrella package's mock-echo demo rather than the CLI.
    That is precisely why this test exists: it is the only one that would notice
    if the real ``--mode rpc`` child and the real ``RpcClient`` stopped agreeing
    about the wire.

    ``get_state`` is the probe because it has no side effects and echoes its
    request id, so a pass proves framing, dispatch AND correlation.
    """

    import os
    from pathlib import Path

    home = Path(str(tmp_path)) / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    (home / "agent").mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "AELIX_CODING_AGENT_DIR": str(home / "agent"),
        "PI_OFFLINE": "1",
    }
    client = RpcClient(
        RpcClientOptions(
            argv=[
                sys.executable,
                "-m",
                "aelix_coding_agent",
                "--mode",
                "rpc",
                "--no-session",
                "--no-extensions",
                "--no-agents",
            ],
            cwd=str(tmp_path),
            env_base=env,
            # A cold child is ~25 s, of which ~18.6 s is import time. The 30 s
            # pi default would make this a coin flip on a loaded box.
            send_timeout_ms=180_000,
        )
    )
    await client.start()
    try:
        state = await client.get_state()
        # A real child with no model configured still starts and answers; the
        # session id is the proof that a real harness is behind the wire.
        assert state.session_id
    finally:
        await client.stop()
