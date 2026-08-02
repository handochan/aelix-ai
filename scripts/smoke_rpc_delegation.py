"""RpcChannel live smoke — the first run of this channel against a REAL model.

    cd /workspaces/aelix-rpc && \
    PYTHONPATH=packages/aelix-coding-agent/src:packages/aelix-agent-core/src:packages/aelix-ai/src \
    /workspaces/aelix-ai/.venv/bin/python scripts/smoke_rpc_delegation.py --step all --live

NO SECRET IS EVER PRINTED. Credentials are read by ``AuthStorage`` out of
``~/.aelix/agent/auth.json`` and never leave it; the only things this script
prints are argv, timings, envelope fields and event counts. (``--api-key`` is
deliberately not offered: a key on an argv is a key in ``ps`` and in the shell
history.) Reading auth from the agent dir rather than a ``.env`` also makes the
run independent of cwd — ``load_dotenv`` is cwd-relative and this worktree has
no ``.env``.

STEPS THAT SPEND TOKENS REQUIRE ``--live``. Without it they are skipped and
reported ``SKIPPED (needs --live)``, so the default invocation is free:

    select   S0  free    which channel, and the exact argv each one builds
    boot     S1  free    a real rpc child, spawned, never prompted
    success  T1  LIVE    a clean delegation, end to end
    timeout  T2  LIVE    the deadline lands AFTER the child has spoken
    cancel   T3  LIVE    the parent's task is cancelled mid-delegation

WHY IT DRIVES ``_SubagentRuntimeImpl`` AND NOT ``RpcChannel.run``. Calling the
channel directly bypasses ``runtime._publish`` → ``host.on_progress`` →
``SubagentProgressBridge``, and the ``subagent_start`` / ``subagent_end`` pair
COUNT is the only way to see the regression Step 1 shipped and then fixed: a
progress tap left live through teardown published a terminal snapshot per
drained line and produced 2 325 pairs for ONE cancelled delegation. Every step
below therefore asserts that count, and T3 is where it actually bites.

WHAT THIS IS FOR. All ~25 of ``RpcChannel``'s tests drive scripted stub
children. Three paths have never met a real model — clean success, timeout, and
cancellation — and those are exactly the three Step 1 touched. Set
``AELIX_SUBAGENT_CHANNEL=rpc`` (see :mod:`aelix_agents.channel_select`) to make
an interactive aelix use this channel; this script sets it for itself.

NOT COLLECTED BY PYTEST. ``[tool.pytest.ini_options] testpaths = ["tests"]``,
and nothing here is named ``test_*``. It IS inside ``[tool.pyright] include``,
so it must type-check clean.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aelix_agents.channel_select import CHANNEL_ENV_VAR, select_channel
from aelix_agents.print_channel import build_child_argv
from aelix_agents.progress import SubagentProgressBridge
from aelix_agents.reaper import descendant_pids
from aelix_agents.rpc_channel import build_rpc_child_argv
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.discovery import resolve_profile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.config import get_agent_dir
from aelix_coding_agent.subagent_contract import ResolvedProfile, SubagentResult

STEPS = ("select", "boot", "success", "timeout", "cancel")
LIVE_STEPS = frozenset({"success", "timeout", "cancel"})

PROFILE = "helper"
"""``model: inherit`` / ``provider: inherit`` normalise to ``None``, so no
``--model`` reaches the child argv and the child inherits the same settings
default the parent has. ONE place to control what this costs."""

_ACK_TASK = "Reply with exactly the word ACK and nothing else."
_LONG_TASK = (
    "Count from 1 to 200, one number per line, and write one short sentence "
    "about each number as you go."
)


# ── reporting ────────────────────────────────────────────────────────


class Report:
    """``STEP | VERDICT | evidence``, legible without reading this file."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, step: str, verdict: str, evidence: str) -> None:
        self.rows.append((step, verdict, evidence))
        print(f"{step:<9}| {verdict:<8}| {evidence}", flush=True)

    def note(self, text: str) -> None:
        print(f"         |         | {text}", flush=True)

    @property
    def ok(self) -> bool:
        return all(v != "FAIL" for _, v, _ in self.rows)


def _check(report: Report, step: str, claims: list[tuple[bool, str]]) -> None:
    """One row per step: PASS with the evidence, or FAIL naming every breach."""

    broken = [why for good, why in claims if not good]
    if broken:
        report.add(step, "FAIL", "; ".join(broken))
    else:
        report.add(step, "PASS", "; ".join(why for _, why in claims))


# ── the driver ───────────────────────────────────────────────────────


class _Ui:
    """``SubagentProgressBridge`` writes statusline rows here. Nothing reads
    them; what this smoke measures is the EVENT side."""

    def set_status(self, key: str, text: str | None) -> None:
        del key, text


class _Api:
    """The ``ExtensionAPI`` surface ``SubagentProgressBridge`` actually reaches
    for. Hand-written rather than a mock because ``_ui()`` compares
    ``api.runtime.ui`` by IDENTITY against ``HEADLESS_UI_CONTEXT``."""

    def __init__(self) -> None:
        self.channels: list[str] = []
        api = self

        class _Events:
            def emit(self, channel: str, _payload: Any) -> None:
                api.channels.append(channel)

        class _Runtime:
            ui = _Ui()

        self.events = _Events()
        self.runtime = _Runtime()

    def starts(self) -> int:
        return self.channels.count("subagent_start")

    def ends(self) -> int:
        return self.channels.count("subagent_end")


async def _model_registry() -> Any:
    """The real registry, from the real agent dir. Same two lines ``entry.py``
    uses. It is what wakes ``apply_cost_fallback`` — the path that has never run
    in production, so every openrouter delegation has always reported cost 0."""

    from aelix_ai.oauth import AuthStorage
    from aelix_coding_agent.model_registry import ModelRegistry

    storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage)


def _resolved(cwd: str, *, provider: str | None, model: str | None) -> ResolvedProfile:
    profile = resolve_profile(
        PROFILE, cwd=cwd, project_trusted=False, agent_dir=get_agent_dir()
    )
    if provider or model:
        profile = dataclasses.replace(
            profile,
            provider=provider or profile.provider,
            model=model or profile.model,
        )
    return ResolvedProfile(
        name=profile.name,
        profile=profile,
        source_path=profile.file_path,
        scope=profile.scope,
    )


async def _driver(cwd: str, registry: Any) -> tuple[_SubagentRuntimeImpl, _Api]:
    """A runtime wired exactly as ``AgentsExtension`` wires one, minus the
    harness: the same channel selector, the same progress bridge, and
    ``consent_context`` answering ``None`` so consent takes its headless silent
    grant (no dialog, no hang) and the child is clamped to ``plan``."""

    api = _Api()
    bridge = SubagentProgressBridge(api)
    channel = select_channel(model_registry=lambda: registry)
    host = SubagentHost(
        cwd=lambda: cwd,
        posture=lambda: PermissionMode.DEFAULT,
        consent_context=lambda: None,
        project_trusted=lambda: False,
        agent_dir=lambda: get_agent_dir(),
        model_registry=lambda: registry,
        on_progress=bridge,
    )
    return _SubagentRuntimeImpl(host=host, channel=channel), api


def _envelope_line(result: SubagentResult) -> str:
    u = result.usage
    return (
        f"ok={result.ok} status={result.status!r} exit={result.exit_code} "
        f"stop_reason={result.stop_reason!r} mode={result.permission_mode!r} "
        f"turns={u.turns} tokens={u.tokens} cost={u.cost} "
        f"elapsed={result.elapsed_ms}ms"
    )


def _pgrep() -> list[str]:
    """Every live ``python -m aelix_coding_agent``. The P2/P3 hygiene command."""

    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["/usr/bin/pgrep", "-af", "python.*-m aelix_coding_agent"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ── S0 — select ──────────────────────────────────────────────────────


def step_select(report: Report, cwd: str, *, provider: str | None, model: str | None) -> None:
    """WHICH CHANNEL, AND WHAT ARGV. Everything after this is worthless if the
    wrong one ran, and the child's argv is the ONLY external discriminator
    between the two — the envelope and the progress events are identical."""

    from aelix_agents.channel_select import ChannelSelectionError
    from aelix_agents.print_channel import PrintChannel
    from aelix_agents.rpc_channel import RpcChannel

    unset = select_channel(env={})
    chosen = select_channel(env={CHANNEL_ENV_VAR: "rpc"})

    refused = ""
    try:
        select_channel(env={CHANNEL_ENV_VAR: "rpk"})
    except ChannelSelectionError as exc:
        refused = str(exc)

    resolved = _resolved(cwd, provider=provider, model=model)
    kwargs: dict[str, Any] = {
        "prompt_path": "/tmp/aelix-subagent-smoke/prompt.md",
        "task": "TASK-GOES-HERE",
        "permission_mode": PermissionMode.PLAN,
        "child_cwd": cwd,
        "parent_cwd": cwd,
    }
    print_argv = build_child_argv(resolved.profile, **kwargs)
    rpc_argv = build_rpc_child_argv(resolved.profile, **kwargs)

    report.note(f"print argv: {print_argv}")
    report.note(f"rpc   argv: {rpc_argv}")
    report.note(f"refusal   : {refused}")

    _check(
        report,
        "select",
        [
            (isinstance(unset, PrintChannel), "unset -> PrintChannel"),
            (isinstance(chosen, RpcChannel), "rpc -> RpcChannel"),
            (bool(refused) and "rpk" in refused, "'rpk' refuses with the value named"),
            ("--mode" in print_argv and "json" in print_argv, "print argv: --mode json"),
            (
                any(a.startswith("Task:") for a in print_argv),
                # NOT "ends with": ``profile_to_argv`` appends the positional and
                # then ``build_child_argv`` appends --permission-mode, the trust
                # argv and --no-agents after it. What matters is that the print
                # child gets its task on the ARGV and the rpc child does not.
                "print argv carries the Task: positional",
            ),
            ("--mode" in rpc_argv and "rpc" in rpc_argv, "rpc argv: --mode rpc"),
            ("--no-session" in rpc_argv, "rpc argv: --no-session"),
            ("--no-agents" in rpc_argv, "rpc argv: --no-agents"),
            (
                rpc_argv[rpc_argv.index("--permission-mode") + 1] == "plan",
                "rpc argv: --permission-mode plan",
            ),
            (
                not any(a.startswith("Task:") for a in rpc_argv),
                "rpc argv carries NO task positional (it goes over the wire)",
            ),
        ],
    )


# ── S1 — boot ────────────────────────────────────────────────────────


async def step_boot(report: Report, cwd: str, registry: Any, *, provider: str | None, model: str | None) -> None:
    """A REAL rpc child, spawned and torn down, that never contacts the model.

    ``timeout_ms=1`` is the only STRUCTURALLY free control. ``timeout_ms=1000``
    is not: ``prompt_and_wait`` writes the command into the child's stdin
    immediately after ``start()`` returns (~100 ms), and the child reads it out
    of the pipe buffer when its loop opens — so it contacts the model even if
    the parent has already timed out. With ``timeout_ms=1``, ``_run_turn``'s
    ``remaining_ms = timeout_ms - elapsed`` is ``<= 0`` on its first line and the
    prompt is never sent at all.

    This is also the re-measurement of ``_run_turn``'s docstring, which claims a
    cold child costs "~25 s to reach its read loop, ~18.6 s of it import time".
    """

    runtime, api = await _driver(cwd, registry)
    resolved = _resolved(cwd, provider=provider, model=model)

    started = time.monotonic()
    task = asyncio.ensure_future(runtime.spawn(resolved, _ACK_TASK, timeout_ms=1))

    # SELECTION OBSERVED, NOT TRUSTED. The child lives for its boot plus its
    # stdin-EOF teardown; polling here catches its real command line off /proc.
    seen: list[str] = []
    while not task.done():
        seen = [line for line in _pgrep() if "--mode rpc" in line]
        if seen:
            break
        await asyncio.sleep(0.02)

    result = await task
    elapsed_ms = int((time.monotonic() - started) * 1000)

    report.note(f"envelope  : {_envelope_line(result)}")
    report.note(f"spawn->envelope: {elapsed_ms} ms  (docstring claims ~25 000 ms cold)")
    report.note(f"pgrep     : {seen[0] if seen else '(never observed)'}")

    _check(
        report,
        "boot",
        [
            (result.status == "timeout", f"status={result.status!r} (expected timeout)"),
            (result.exit_code == 0, f"exit_code={result.exit_code} (expected 0)"),
            (
                "RpcServerExited" not in (result.error or ""),
                "no RpcServerExited — the child exited on stdin EOF, not a crash",
            ),
            (bool(seen), "a live child's argv carried --mode rpc"),
            (api.starts() == 1, f"{api.starts()} subagent_start (expected 1)"),
            (api.ends() == 1, f"{api.ends()} subagent_end (expected 1)"),
        ],
    )


# ── T1 — success ─────────────────────────────────────────────────────


async def step_success(
    report: Report, cwd: str, registry: Any, *, timeout_ms: int, provider: str | None, model: str | None
) -> None:
    """A clean delegation, end to end. SPENDS TOKENS.

    ``turns == 1`` together with ``ok is True`` is the live assertion for the
    second regression Step 1 shipped: the accumulator folding one post-terminator
    ``message_end`` flipped a finished, exit-0 delegation to
    ``ok=False summary='shutdown hook noise' turns=2 tokens=1``.
    """

    runtime, api = await _driver(cwd, registry)
    resolved = _resolved(cwd, provider=provider, model=model)

    started = time.monotonic()
    result = await runtime.spawn(resolved, _ACK_TASK, timeout_ms=timeout_ms)
    wall_ms = int((time.monotonic() - started) * 1000)

    report.note(f"envelope  : {_envelope_line(result)}")
    report.note(f"summary   : {result.summary[:200]!r}")
    report.note(f"wall      : {wall_ms} ms")

    _check(
        report,
        "success",
        [
            (result.ok is True, f"ok={result.ok}"),
            (result.status == "ok", f"status={result.status!r}"),
            (result.exit_code == 0, f"exit_code={result.exit_code}"),
            (
                result.stop_reason not in ("error", "aborted"),
                f"stop_reason={result.stop_reason!r}",
            ),
            (result.error is None, f"error={result.error!r}"),
            (
                result.permission_mode == "plan",
                f"permission_mode={result.permission_mode!r}",
            ),
            (
                bool(result.summary) and result.summary != "(no output)",
                "summary is the child's real answer",
            ),
            (result.usage.tokens > 0, f"tokens={result.usage.tokens}"),
            (result.usage.turns == 1, f"turns={result.usage.turns} (expected 1)"),
            (api.starts() == 1, f"{api.starts()} subagent_start (expected 1)"),
            (api.ends() == 1, f"{api.ends()} subagent_end (expected 1)"),
        ],
    )
    report.note(
        "cost is EVIDENCE, not an assertion: a non-zero usage.cost is the first "
        "live sign apply_cost_fallback is awake, but a provider that reports its "
        "own cost, or a model missing from the registry, both legitimately give 0"
    )


# ── T2 — timeout ─────────────────────────────────────────────────────


async def step_timeout(
    report: Report, cwd: str, registry: Any, *, timeout_ms: int, provider: str | None, model: str | None
) -> None:
    """The deadline lands AFTER the child has spoken. SPENDS TOKENS.

    THE WHOLE POINT IS THE PARTIAL. Before Step 1's fix ① the parent
    unsubscribed its reducer before ``_shutdown`` drained, so a timed-out
    delegation reported ``summary='(no output)'`` with zero tokens and zero
    turns while the child's real partial answer sat unread in the pipe. This is
    the live counterpart of that measurement.

    The budget is derived from S1's measured boot, not hardcoded: an 8 s budget
    on a ~1.2 s boot leaves the child ~7 s of real generation, which is enough
    for the model to have started answering and nowhere near enough to finish.
    """

    runtime, api = await _driver(cwd, registry)
    resolved = _resolved(cwd, provider=provider, model=model)

    started = time.monotonic()
    result = await runtime.spawn(resolved, _LONG_TASK, timeout_ms=timeout_ms)
    wall_ms = int((time.monotonic() - started) * 1000)

    report.note(f"envelope  : {_envelope_line(result)}")
    report.note(f"summary   : {result.summary[:200]!r}")
    report.note(f"wall      : {wall_ms} ms (budget {timeout_ms} ms)")

    spoke = bool(result.summary) and result.summary != "(no output)"
    _check(
        report,
        "timeout",
        [
            (result.status == "timeout", f"status={result.status!r}"),
            (spoke, "summary is the child's real partial, not '(no output)'"),
            (
                result.usage.tokens > 0,
                f"tokens={result.usage.tokens} — a non-zero usage block survived teardown",
            ),
            (api.starts() == 1, f"{api.starts()} subagent_start (expected 1)"),
            (api.ends() == 1, f"{api.ends()} subagent_end (expected 1)"),
        ],
    )
    if not spoke:
        report.note(
            "NOT NECESSARILY FIX ① REGRESSING: if the budget fired before the "
            "model emitted its first message_end there was nothing to recover. "
            "Re-run with a larger --timeout-ms before recording a finding."
        )


# ── T3 — cancel ──────────────────────────────────────────────────────


async def step_cancel(
    report: Report, cwd: str, registry: Any, *, after_s: float, provider: str | None, model: str | None
) -> None:
    """The parent's task is cancelled mid-delegation. SPENDS TOKENS.

    THE ONLY LIVE VIEW OF STEP 1 REGRESSION ①. ``_eager_abort`` makes the row
    terminal BEFORE the teardown drain, so a progress tap left live through it
    published a terminal snapshot per drained line and ``SubagentProgressBridge``
    emitted a fresh pair for each: measured at 2 325 pairs for one cancelled
    delegation. One delegation is one ``subagent_start`` and one
    ``subagent_end``, always.

    ``CancelledError`` IS A ``BaseException`` — caught explicitly, never by
    ``except Exception``, which is the landmine this repo has tripped before.
    """

    runtime, api = await _driver(cwd, registry)
    resolved = _resolved(cwd, provider=provider, model=model)

    task = asyncio.ensure_future(
        runtime.spawn(resolved, _LONG_TASK, timeout_ms=120_000)
    )
    await asyncio.sleep(after_s)
    pids = [
        pid
        for line in _pgrep()
        if "--mode rpc" in line
        for pid in [int(line.split(None, 1)[0])]
    ]
    grandchildren = [p for pid in pids for p in descendant_pids(pid)]
    task.cancel()

    propagated = False
    try:
        await task
    except asyncio.CancelledError:
        propagated = True

    # The reaper is detached and shielded, so give it its moment before judging.
    await asyncio.sleep(1.0)
    survivors = [line for line in _pgrep() if "--mode rpc" in line]
    orphans = [p for p in grandchildren if _alive(p)]

    report.note(f"child pids: {pids} (+{len(grandchildren)} descendants)")
    report.note(f"survivors : {survivors or '(none)'}")

    _check(
        report,
        "cancel",
        [
            (propagated, "CancelledError propagated to the caller"),
            (bool(pids), "a child was actually running when the cancel landed"),
            (not survivors, f"{len(survivors)} child(ren) survived the cancel"),
            (not orphans, f"{len(orphans)} orphaned grandchild(ren)"),
            (api.starts() == 1, f"{api.starts()} subagent_start (expected 1)"),
            (api.ends() == 1, f"{api.ends()} subagent_end (expected 1)"),
        ],
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ── post-run hygiene ─────────────────────────────────────────────────


def step_hygiene(report: Report, sessions_before: set[str], parent_pid: int) -> None:
    """The four named checks. Run last, whatever the steps did."""

    survivors = _pgrep()
    sessions_dir = Path.home() / ".aelix" / "sessions"
    sessions_now = (
        {p.name for p in sessions_dir.iterdir()} if sessions_dir.is_dir() else set()
    )
    new_sessions = sorted(sessions_now - sessions_before)
    tmp = Path(os.environ.get("TMPDIR", "/tmp"))
    leaked = sorted(
        str(p) for p in tmp.glob(f"aelix-subagent-{parent_pid}-*") if p.is_dir()
    )

    _check(
        report,
        "hygiene",
        [
            (not survivors, f"{len(survivors)} orphan aelix process(es): {survivors}"),
            (
                not new_sessions,
                # --no-session is supplied by the ONESHOT argv prefix and not by
                # the rpc one, so rpc_channel appends it itself. This asserts
                # that line is alive: without it every delegation would leave a
                # session the user never started in their /resume picker.
                f"new session file(s) — rpc's own --no-session: {new_sessions}",
            ),
            (not leaked, f"leaked prompt dir(s): {leaked}"),
        ],
    )


# ── entry point ──────────────────────────────────────────────────────


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=(*STEPS, "all"), default="all")
    parser.add_argument(
        "--live",
        action="store_true",
        help="permit the steps that contact a real model and spend tokens",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="override the settings default (github-copilot is refused, below)",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=60_000,
        help="T1's whole-delegation budget",
    )
    parser.add_argument(
        "--partial-timeout-ms",
        type=int,
        default=8_000,
        help="T2's budget — must land AFTER the child has spoken, so derive it "
        "from S1's measured boot rather than trusting this default",
    )
    parser.add_argument("--cancel-after", type=float, default=3.0)
    parser.add_argument("--cwd", default=os.getcwd())
    args = parser.parse_args()

    if args.provider == "github-copilot":
        # Its OAuth refresh plus this box's recorded history of host/endpoint
        # routing defects would make any failure ambiguous between "rpc broke"
        # and "Copilot routing broke", which is the one thing a first smoke
        # cannot afford.
        print("Error: --provider github-copilot is refused for this smoke.")
        return 2

    # The script sets it for ITSELF, so the run cannot be silently downgraded by
    # an ambient value — see channel_select's refusal note for why a downgrade
    # is invisible at every other surface.
    os.environ[CHANNEL_ENV_VAR] = "rpc"

    wanted = list(STEPS) if args.step == "all" else [args.step]
    report = Report()
    sessions_dir = Path.home() / ".aelix" / "sessions"
    sessions_before = (
        {p.name for p in sessions_dir.iterdir()} if sessions_dir.is_dir() else set()
    )

    registry = await _model_registry()
    cwd = str(Path(args.cwd).resolve())
    common = {"provider": args.provider, "model": args.model}

    for step in wanted:
        if step in LIVE_STEPS and not args.live:
            report.add(step, "SKIPPED", "needs --live (this step spends tokens)")
            continue
        try:
            if step == "select":
                step_select(report, cwd, **common)
            elif step == "boot":
                await step_boot(report, cwd, registry, **common)
            elif step == "success":
                await step_success(
                    report, cwd, registry, timeout_ms=args.timeout_ms, **common
                )
            elif step == "timeout":
                await step_timeout(
                    report, cwd, registry, timeout_ms=args.partial_timeout_ms, **common
                )
            elif step == "cancel":
                await step_cancel(
                    report, cwd, registry, after_s=args.cancel_after, **common
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a step that blew up is a RESULT
            report.add(step, "FAIL", f"{type(exc).__name__}: {exc}")

    step_hygiene(report, sessions_before, os.getpid())
    print(f"\n{'PASS' if report.ok else 'FAIL'} — {len(report.rows)} step(s)")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
