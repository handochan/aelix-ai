"""#510 (ADR-0283; owner decision 6; Codex F07) — a parent's ``--exclude-tools`` reaches its children.

The patterns travel as they were typed:

    AgentsExtension.exclude_tools  (the parent's ``Args``)
      → SubagentHost.exclude_tools
        → SpawnPlan.parent_exclude_tools
          → narrow_tools(profile, grant, exclude) → AgentProfile.exclude_tools
            → ChildLimits.exclude_tools (print channel; recorded, resumed)
              → resolver.profile_to_flags → ``--exclude-tools`` on the argv

BOTH channels are driven through their own ``run`` (the rpc channel never
reads ``plan.limits``, which is what F07 found), every argv assertion is a
``parse_args`` round trip, and the child's REGISTRY — built by the child's own
launch path from that argv — is checked to lack the tool, not only its
execution.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agents.child_session import ChildLimits, LimitsError
from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import (
    PrintChannel,
    RunningChild,
    SpawnPlan,
    build_child_argv,
    fresh_limits,
    limited_profile,
    narrow_tools,
    resume_limits,
)
from aelix_agents.rpc_channel import RpcChannel, build_rpc_child_argv
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import profile_to_flags
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import Args, parse_args
from aelix_coding_agent.subagent_contract import ResolvedProfile

# ``build_child_argv``/``build_rpc_child_argv`` prefix the command line with
# ``[sys.executable, "-P", "-m", "aelix_coding_agent"]``; ``parse_args`` is
# handed ``sys.argv[1:]``, i.e. everything after those four.
_LAUNCH_PREFIX = 4
_EXCLUDE = ("bash", "github__*")


def _profile(**kwargs: Any) -> AgentProfile:
    base: dict[str, Any] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
        "tools": ("read", "bash", "grep"),
    }
    base.update(kwargs)
    return AgentProfile(**base)


def _resolved(profile: AgentProfile | None = None) -> ResolvedProfile:
    p = profile if profile is not None else _profile()
    return ResolvedProfile(name=p.name, profile=p, source_path=p.file_path, scope=p.scope)


def _plan(tmp_path: Path, **kwargs: Any) -> SpawnPlan:
    base: dict[str, Any] = {
        "id": "sub-test",
        "resolved": _resolved(),
        "task": "do the thing",
        "cwd": str(tmp_path),
        "parent_cwd": str(tmp_path),
        "permission_mode": PermissionMode.PLAN,
    }
    base.update(kwargs)
    return SpawnPlan(**base)


_PRELUDE = textwrap.dedent(
    """
    import json, sys

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    def turn(text):
        emit({"id": "stub-session", "created_at": "now"})
        emit({"type": "agent_start"})
        emit({"type": "turn_start"})
        emit({"type": "message_end", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": None, "provider": "stub", "model": "stub-1",
        }})
        emit({"type": "agent_end"})
    """
)
_PRINT_STUB = _PRELUDE + 'turn("done")\n'
_RPC_STUB = _PRELUDE + textwrap.dedent(
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        cmd = json.loads(line)
        rid, kind = cmd.get("id"), cmd.get("type")
        emit({"type": "response", "command": kind, "success": True, "id": rid, "data": {}})
        if kind == "prompt":
            turn("done")
    """
)


def _capture(real: Any, script: str, seen: list[list[str]], *tail: str) -> Any:
    def _build(*args: Any, **kwargs: Any) -> list[str]:
        seen.append(real(*args, **kwargs))
        return [sys.executable, "-c", script, *tail]

    return _build


def _child(argv: list[str]) -> Args:
    return parse_args(argv[_LAUNCH_PREFIX:])


# --- the narrowing and the flag -------------------------------------------------


def test_narrow_tools_puts_the_parents_patterns_on_the_child_profile() -> None:
    narrowed = narrow_tools(_profile(), ["read", "grep"], _EXCLUDE)
    assert narrowed.profile.exclude_tools == _EXCLUDE
    assert narrowed.profile.tools == ("grep", "read")  # bash: not in the parent's grant
    flags = profile_to_flags(narrowed.profile, prompt_path="/p.md")
    assert flags[flags.index("--exclude-tools") + 1] == "bash,github__*"
    assert "--exclude-tools" not in profile_to_flags(_profile(), prompt_path="/p.md")


def test_fresh_limits_carry_the_patterns_and_a_resume_keeps_both_runs(tmp_path: Path) -> None:
    cwd = str(tmp_path.resolve())
    computed = fresh_limits(
        _resolved(),
        granted_mode=PermissionMode.PLAN,
        cwd=cwd,
        parent_tools=None,
        parent_context_files=True,
        parent_thinking=None,
        parent_model=None,
        model_registry=None,
        parent_exclude_tools=_EXCLUDE,
    )
    assert computed.limits.exclude_tools == _EXCLUDE
    argv = build_child_argv(
        limited_profile(_profile(), computed.limits),
        prompt_path="/p.md",
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd=cwd,
        parent_cwd=cwd,
        model_pin=computed.limits.model,
    )
    assert _child(argv).exclude_tools == list(_EXCLUDE)
    now = fresh_limits(
        _resolved(),
        granted_mode=PermissionMode.PLAN,
        cwd=cwd,
        parent_tools=None,
        parent_context_files=True,
        parent_thinking=None,
        parent_model=None,
        model_registry=None,
        parent_exclude_tools=("ls",),
    ).limits
    resumed = resume_limits(computed.limits, now, parent_cwd=cwd).limits
    assert resumed.exclude_tools == ("bash", "github__*", "ls")


# --- the record (ADR-0277): optional, validated -----------------------------------


def _limits(cwd: Path, **kwargs: Any) -> ChildLimits:
    # ``tmp_path``, never ``"/tmp"``: the record requires an absolute cwd, and a
    # POSIX path is not absolute on Windows.
    base: dict[str, Any] = {
        "granted_mode": PermissionMode.PLAN,
        "tools": ("read",),
        "context_files": True,
        "thinking": None,
        "model": fresh_limits(
            _resolved(),
            granted_mode=PermissionMode.PLAN,
            cwd=str(cwd),
            parent_tools=None,
            parent_context_files=True,
            parent_thinking=None,
            parent_model=None,
            model_registry=None,
        ).limits.model,
        "cwd": str(cwd),
        "profile_source": None,
        "profile_scope": "user",
    }
    base.update(kwargs)
    return ChildLimits(**base)


def test_the_record_round_trips_and_an_old_record_reads_as_no_exclusions(tmp_path: Path) -> None:
    with_them = _limits(tmp_path, exclude_tools=_EXCLUDE)
    record = with_them.to_record()
    assert record["exclude_tools"] == list(_EXCLUDE)
    assert ChildLimits.from_record(record) == with_them
    old = _limits(tmp_path).to_record()
    assert "exclude_tools" not in old  # what an earlier aelix wrote and reads
    assert ChildLimits.from_record(old).exclude_tools == ()


@pytest.mark.parametrize(
    "forged",
    [["-rf"], ["a b"], ["a,b"], [""], ["x\x1b"], "bash", [1], ["bash", "bash"], ["x" * 300]],
)
def test_a_forged_exclusion_is_refused(forged: Any, tmp_path: Path) -> None:
    record = _limits(tmp_path).to_record()
    record["exclude_tools"] = forged
    with pytest.raises(LimitsError):
        ChildLimits.from_record(record)


# --- both channels, through their own run --------------------------------------------


async def test_the_print_channel_carries_the_patterns_onto_the_argv(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    channel = PrintChannel(argv_builder=_capture(build_child_argv, _PRINT_STUB, seen, "Task: t"))
    await channel.run(
        _plan(tmp_path, parent_exclude_tools=_EXCLUDE),
        child=RunningChild(id="sub-test", profile="scout"),
    )
    assert len(seen) == 1 and _child(seen[0]).exclude_tools == list(_EXCLUDE)


async def test_the_rpc_channel_carries_the_patterns_onto_the_argv(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    channel = RpcChannel(argv_builder=_capture(build_rpc_child_argv, _RPC_STUB, seen))
    await channel.run(
        _plan(tmp_path, parent_exclude_tools=_EXCLUDE),
        child=RunningChild(id="sub-test", profile="scout"),
    )
    assert len(seen) == 1 and _child(seen[0]).exclude_tools == list(_EXCLUDE)


async def test_the_host_reaches_the_argv_through_the_runtimes_own_channel(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), exclude_tools=lambda: _EXCLUDE)
    )
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: t"
    )
    await runtime.spawn(_resolved(), "go")
    assert len(seen) == 1 and _child(seen[0]).exclude_tools == list(_EXCLUDE)


def test_the_extension_reads_the_parents_patterns_live() -> None:
    parsed = parse_args(["-xt", "bash", "-p", "x"])
    host = AgentsExtension(exclude_tools=lambda: tuple(parsed.exclude_tools))._host()
    assert host.exclude_tools() == ("bash",)
    parsed.exclude_tools.append("ls")
    assert host.exclude_tools() == ("bash", "ls")
    assert AgentsExtension()._host().exclude_tools() == ()


# --- the child's registry -------------------------------------------------------------


async def test_the_childs_own_launch_leaves_the_tool_out_of_its_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Built by the child's launch path from the argv the channel emits."""

    from aelix_coding_agent.cli.entry import _build_harness_options

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.chdir(tmp_path)
    narrowed = narrow_tools(_profile(tools=None), None, ("bash",))
    argv = build_rpc_child_argv(
        narrowed.profile,
        prompt_path=str(tmp_path / "p.md"),
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
    )
    parsed = _child(argv)
    options = await _build_harness_options(
        parsed, Session(MemorySessionStorage()), project_trusted=False
    )
    harness = AgentHarness(options)
    assert "bash" not in {t.name for t in harness.state.tools}
    assert "bash" in {t.name for t, _r in harness.excluded_tools()}
    # A child-side extension cannot bring it back either.
    harness.runtime.actions.set_active_tools(["bash", "read"])
    assert harness._action_get_active_tools() == ["read"]


# --- the bound (Codex F03): refused before admission, never truncated -------------------


def _patterns(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{i}" for i in range(count))


def _grant() -> Any:
    from aelix_agents.consent import SpawnGrant

    p = _profile()
    return SpawnGrant(
        profile=p.name,
        source_path=p.file_path,
        scope=p.scope,
        mode=PermissionMode.PLAN,
        widened=False,
        consented=True,
    )


async def test_a_fresh_spawn_over_the_records_bound_is_refused_before_admission(
    tmp_path: Path,
) -> None:
    """257 distinct patterns the CLI accepts: written, the record would not read back."""

    seen: list[list[str]] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), exclude_tools=lambda: _patterns("t", 257))
    )
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: t"
    )
    result = await runtime.spawn(_resolved(), "go")
    assert not result.ok and result.status == "error" and result.error is not None
    assert "257 --exclude-tools entries" in result.error and "at most 256" in result.error
    assert seen == [] and runtime._children == {}  # no process, no row
    # 256 is the bound itself: started, and every pattern reaches the argv.
    ok = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), exclude_tools=lambda: _patterns("t", 256))
    )
    assert ok.channel is not None
    ok.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: t"
    )
    await ok.spawn(_resolved(), "go")
    assert len(seen) == 1 and len(_child(seen[0]).exclude_tools) == 256


async def test_a_resume_whose_union_passes_the_bound_is_refused(tmp_path: Path) -> None:
    """Two individually valid, disjoint 129-entry sets: the union of 258 is refused."""

    from aelix_agents.child_session import ResumeTarget, exclude_tools_refusal
    from aelix_agents.runtime import _Resume

    cwd = str(tmp_path.resolve())
    stored = _limits(tmp_path.resolve(), exclude_tools=_patterns("a", 129), tools=("read",))
    assert ChildLimits.from_record(stored.to_record()) == stored
    target = ResumeTarget(
        session_id="0" * 32,
        first_key="sub-0123456789ab",
        path=str(tmp_path / "s.jsonl"),
        rel="s.jsonl",
        limits=stored,
        profile="scout",
        task_preview="t",
        last_status="stopped",
        parent_session_id="parent-1",
    )
    seen: list[list[str]] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: cwd, exclude_tools=lambda: _patterns("b", 129))
    )
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: t"
    )
    result = await runtime._run(
        _grant(),
        _resolved(),
        "t",
        child_cwd=cwd,
        on_event=None,
        charge_budget=False,
        resume=_Resume(target=target, instruction="go on"),
    )
    assert not result.ok and result.status == "error" and result.error is not None
    assert "258 --exclude-tools entries" in result.error
    assert seen == [] and runtime._children == {}
    assert exclude_tools_refusal(_patterns("a", 129)) is None


@pytest.mark.parametrize("bad", ["-rf", "a b", "a,b", "x\x1b", "x" * 300])
def test_an_entry_the_record_would_refuse_is_refused_with_its_reason(bad: str) -> None:
    from aelix_agents.child_session import exclude_tools_refusal

    reason = exclude_tools_refusal(("bash", bad))
    assert reason is not None and "cannot be passed to a delegated agent" in reason
