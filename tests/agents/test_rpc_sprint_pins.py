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
import warnings

import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.env_sandbox import child_env


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
    """The soft-kill grace + the final reap, and nothing open-ended.

    Both halves are needed and neither is obvious from the code: a child that
    survives the soft signal costs ``SHUTDOWN_SIGTERM_TIMEOUT_MS`` before the
    escalation, and the reap after the hard kill is bounded at 5 s so an
    unkillable child cannot hold the caller forever. A delegation channel calls
    this on every teardown path, so an unbounded leg here is an unbounded leg
    there.

    RUNS ON WINDOWS AGAIN (#202 closed #207 (2)). It was skipped there because
    ``Popen.terminate()`` is ``TerminateProcess(handle, 1)``, uncatchable, so a
    ``SIG_IGN`` stub died on the first attempt and no grace elapsed (measured on
    windows-latest: 0.047 s against a 1.000 s grace). ``stop()`` now sends
    ``CTRL_BREAK_EVENT``, which arrives as ``SIGBREAK`` and IS catchable, so the
    stub below installs a real HANDLER for whichever of the two names the
    platform has. The ``signal seen`` breadcrumb is what separates "delivered
    and survived" from "delivered nothing and the timeout expired" — without it
    ``elapsed >= grace`` passes either way.
    """

    stub = (
        "import signal, sys, time\n"
        'def seen(*_):\n'
        '    sys.stderr.write("signal seen\\n"); sys.stderr.flush()\n'
        'for name in ("SIGTERM", "SIGBREAK"):\n'
        "    s = getattr(signal, name, None)\n"
        "    if s is not None: signal.signal(s, seen)\n"
        'sys.stderr.write("armed\\n"); sys.stderr.flush()\n'
        # 0.05 s chunks, not 0.5 s: on Windows the handler runs when the main
        # thread next reaches a bytecode boundary, so this is the breadcrumb's
        # latency bound and it has to fit inside the grace.
        "while True: time.sleep(0.05)\n"
    )
    client = RpcClient(RpcClientOptions(argv=[sys.executable, "-c", stub]))
    await client.start()
    for _ in range(200):
        if "armed" in client.get_stderr():
            break
        await asyncio.sleep(0.05)
    assert "armed" in client.get_stderr(), "the stub never armed its handler"

    tree = client._tree
    assert tree is not None
    # Count the escalation instead of inferring it from the clock. Measured
    # (review MUT-1): delete ``tree.hard_kill()`` from ``stop()`` and the two
    # timing assertions below still hold — the grace is paid in full, and
    # ``stop()``'s trailing ``transport.close()`` reaches ``Popen.kill()`` and
    # ends the root anyway. The mutated run came within 4 ms of the 6 s bound
    # instead of breaking it, which is how close "still green" was. An INSTANCE
    # attribute, so the class stays clean for every other client in this run;
    # it delegates, so the child dies the way production kills it.
    hard_kills = 0
    real_hard_kill = tree.hard_kill

    def _counting_hard_kill() -> None:
        nonlocal hard_kills
        hard_kills += 1
        real_hard_kill()

    tree.hard_kill = _counting_hard_kill  # type: ignore[method-assign]

    started = time.monotonic()
    await client.stop()
    elapsed = time.monotonic() - started

    worst_case = RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0 + 5.0
    assert elapsed <= worst_case, f"stop() took {elapsed:.2f}s, over {worst_case:.2f}s"
    # And it really did have to escalate — otherwise this pins nothing.
    assert elapsed >= RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0
    assert hard_kills == 1, (
        f"hard_kill ran {hard_kills} times, not once — the bound this test "
        "measures was not the soft grace plus a real escalation"
    )
    assert "signal seen" in client.get_stderr(), (
        "the grace elapsed but the child never saw the soft signal; that is a "
        f"missing delivery, not a survived one. stderr: {client.get_stderr()!r}"
    )


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

    THE ``stop()`` HALF IS THE ONLY TEST OF THE CHILD-SIDE SIGNAL HANDLERS
    (``rpc_mode.install_signal_handlers``). Every other shutdown test drives a
    stub that installs its own. A real child answering under the grace with
    status 0 is the POSIX ``SIGTERM`` handler on ubuntu/macOS and, on
    windows-latest, the ``SIGBREAK`` handler #202 added next to it — nothing
    else in the suite exercises either.
    """

    from pathlib import Path

    home = Path(str(tmp_path)) / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    (home / "agent").mkdir(parents=True, exist_ok=True)
    env = child_env(
        home,
        XDG_CONFIG_HOME=str(home / ".config"),
        AELIX_CODING_AGENT_DIR=str(home / "agent"),
        PI_OFFLINE="1",
    )
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
    proc = client.process
    assert proc is not None
    try:
        state = await client.get_state()
        # A real child with no model configured still starts and answers; the
        # session id is the proof that a real harness is behind the wire.
        assert state.session_id
    finally:
        started = time.monotonic()
        await client.stop()
        elapsed = time.monotonic() - started

    # ITS OWN BOUND, not the product constant. What ``stop()`` is timed over
    # here is the whole cooperative shutdown: the soft signal, the child's
    # Python-level handler, ``loop.call_soon_threadsafe``, ``run_rpc_mode``'s
    # return, the full runtime dispose, ``_await_exit``'s 50 ms polling
    # quantum, ``_teardown_tasks()`` and ``transport.close()``. On
    # windows-latest that chain additionally pays CTRL_BREAK delivery on a
    # separate thread and a handler that runs at the main thread's next
    # bytecode boundary — the leg with the most to do and the one nobody has
    # measured (review win-leg/F4). 3.0 s says "far below the escalation", not
    # "a millisecond budget"; a 1.05 s orderly shutdown on a busy runner used
    # to report the opposite of what ``returncode == 0`` proves. The escalation
    # it must stay clear of is ``SHUTDOWN_SIGTERM_TIMEOUT_MS`` + the 5 s reap.
    assert elapsed < 3.0, (
        f"the real child took {elapsed:.3f}s to go — far enough over the "
        "cooperative path to suspect it did not answer the soft signal"
    )
    # Status 0 is the child's own orderly return out of ``run_rpc_mode``, and
    # this is the real delivery proof: a hard kill cannot produce it on either
    # platform.
    assert proc.returncode == 0, (
        f"the real child exited {proc.returncode} after a {elapsed:.3f}s stop(); "
        "the shutdown path did not run its teardown. The child's stderr tail is "
        "the only diagnostic a remote leg can hand back, so it rides along: "
        f"{client.get_stderr()[-2000:]!r}"
    )


class _PatientClient(RpcClient):
    """A grace long enough that only a child that NEVER answers gets hard-killed.

    The conformance test above times the whole cooperative shutdown against a
    3 s bound and needs ``returncode == 0``; when the windows leg answered
    ``exited 1`` (``TerminateJobObject``'s code) that single number could not say
    whether the console event was undeliverable, the child's handler never ran
    under the proactor loop, or its orderly teardown merely outran the 1 s
    Pi-parity grace on a slow runner. This client gives the child 20 s so the
    three are told apart: a hard kill here means the soft stage does not work at
    all; a clean exit says how long the teardown really takes.
    """

    SHUTDOWN_SIGTERM_TIMEOUT_MS = 20_000


async def test_the_real_rpc_child_exits_cleanly_given_a_generous_grace(
    tmp_path: object,
) -> None:
    """Diagnostic twin of the conformance test: same child, 20 s grace.

    Passes wherever the soft signal reaches the child and its handler runs; the
    measured shutdown time is emitted as a warning so it shows in a ``-q`` CI
    log without a failing assertion. If THIS fails on a leg, the cooperative
    stage does not exist there and ADR-0238's claim narrows; if only the 3 s
    conformance bound fails, the teardown is slow, not absent.
    """

    from pathlib import Path

    home = Path(str(tmp_path)) / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    (home / "agent").mkdir(parents=True, exist_ok=True)
    env = child_env(
        home,
        XDG_CONFIG_HOME=str(home / ".config"),
        AELIX_CODING_AGENT_DIR=str(home / "agent"),
        PI_OFFLINE="1",
    )
    client = _PatientClient(
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
            send_timeout_ms=180_000,
        )
    )
    await client.start()
    proc = client.process
    assert proc is not None
    try:
        state = await client.get_state()
        assert state.session_id
    finally:
        started = time.monotonic()
        await client.stop()
        elapsed = time.monotonic() - started

    warnings.warn(
        f"real rpc child orderly shutdown on {sys.platform}: {elapsed:.3f}s, "
        f"returncode={proc.returncode}",
        stacklevel=1,
    )
    assert proc.returncode == 0, (
        f"the real child exited {proc.returncode} after {elapsed:.3f}s against a "
        "20 s grace — the soft signal never produced an orderly exit. stderr tail: "
        f"{client.get_stderr()[-2000:]!r}"
    )
