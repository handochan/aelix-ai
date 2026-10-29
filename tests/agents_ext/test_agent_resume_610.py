"""#610 — a delegated agent is resumed on its own session, inside the limits it ran with.

WHAT IS PINNED HERE: the runtime half of ``agent(action="resume")`` (ADR-0277).
The limits a delegation records (``ChildLimits``) and the argv it is launched
from; the receipt's ``limits`` / ``resumes`` and the ATTACHED resume run; the
reader that turns a ``Session:`` id back into a child file
(``find_resume_target``) and every refusal it can give; the listing; the
intersection a resume runs under; the runtime door (``resume_granted``) and the
checks it repeats; the RPC channel's refusal; and the exception safety of
deciding the limits before admission.

AT WHAT LEVEL. The REAL runtime against a REAL parent session on disk
(``JsonlSessionStorage`` in a bucket under ``tmp_path``) and the channel
stand-in of ``test_child_session_records`` — no process, except where the argv
``PrintChannel`` really builds is the point, and there a stdlib stub stands in
for the child. A stand-in "child" leaves no transcript, so a test writes the one
thing a resume requires of it — its task, as a user message — through the
session store (:func:`_received_task`).

The real child — a dangling tool call reaching a built-in adapter's request as
"No result provided" — is ``test_agent_resume_real_child_610.py`` (POSIX). The
tool's parser, the hook, the dialog and the rendered results are pinned with
the tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.session.session_lock import SessionWriterLock
from aelix_agents import runtime as runtime_module
from aelix_agents.chain import MAX_TASK_BYTES
from aelix_agents.child_session import (
    CHILD_SESSION_TYPE,
    LISTED_AGENTS,
    LISTING_BYTES,
    RESUMABLE_HEAD,
    RESUMABLE_NONE,
    RESUME_BEFORE_LIMITS,
    RESUME_CONVERSATION_CHANGED,
    RESUME_CWD_GONE,
    RESUME_FILE_GONE,
    RESUME_FIRST_RUN_MISSING,
    RESUME_FORKED,
    RESUME_IN_USE,
    RESUME_NEEDS_ID,
    RESUME_NEVER_STARTED,
    RESUME_NO_SESSION,
    RESUME_NOT_YOURS,
    RESUME_PROFILE_CHANGED,
    RESUME_ROUTE_MOVED,
    RESUME_RUNNING,
    RESUME_UNKNOWN_ID,
    ChildLimits,
    LimitsError,
    ResumeRefused,
    ResumeTarget,
    find_resume_target,
)
from aelix_agents.posture import posture_rank, resume_clamp
from aelix_agents.print_channel import (
    AGENT_TOOL_NAME,
    PrintChannel,
    SpawnPlan,
    build_child_argv,
    limited_profile,
    resume_limits,
)
from aelix_agents.rpc_channel import RpcChannel
from aelix_agents.runtime import (
    MAX_DELEGATIONS_PER_PROMPT,
    MAX_LIVE_CHILDREN,
    RESUME_PREAMBLE,
    RESUME_PREAMBLE_RESERVE,
    SpawnRecordMeta,
    SubagentHost,
    _SubagentRuntimeImpl,
    resume_task,
)
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.resolver import (
    ModelPin,
    child_model_flags,
    child_model_pin,
    model_pin_flags,
)
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.model_registry import ProviderConfigInput
from aelix_coding_agent.tools import ALL_TOOL_NAMES

from tests.agents_ext.test_child_session_records import (
    _Channel,
    _grant,
    _parent,
    _profile,
    _records,
    _resolved,
    _shape,
)
from tests.model_registry.test_provider_ownership_365 import registry as _registry
from tests.model_registry.test_provider_ownership_365 import row as _row

POSIX = sys.platform != "win32"
ALL_BUILTINS = sorted(ALL_TOOL_NAMES)
_SCOUT_SOURCE = os.path.abspath("/home/u/.aelix/agent/agents/scout.md")
"""``_profile()``'s file as ``fresh_limits`` records it: absolute (#610 F15)."""


# === Scaffolding ==============================================================


@dataclass
class _Live:
    """The parent as the runtime reads it — every value a test may move between
    the delegation and the resume, read through the host's live getters."""

    cwd: Path
    tools: list[str] | None = None
    context_files: bool = True
    model: Any = None
    thinking: str | None = None
    registry: Any = None
    session: Any = None


def _runtime(live: _Live, channel: Any) -> _SubagentRuntimeImpl:
    host = SubagentHost(
        cwd=lambda: str(live.cwd),
        session=lambda: live.session,
        active_tools=lambda: live.tools,
        context_files=lambda: live.context_files,
        model=lambda: live.model,
        thinking=lambda: live.thinking,
        model_registry=lambda: live.registry,
    )
    return _SubagentRuntimeImpl(host=host, channel=channel)


def _meta(tool_call_id: str) -> SpawnRecordMeta:
    return SpawnRecordMeta(tool_call_id=tool_call_id, mode="single", index=0)


async def _received_task(path: str | Path, text: str = "Task: look at the thing") -> None:
    """What a real child's first turn leaves in its file: its task, as a user
    message. A resume refuses a child file without one (#610 D6, critique m5)."""

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    await Session(storage).append_message(UserMessage(content=[TextContent(text=text)]))


@dataclass
class _Scene:
    """One parent, one first-run delegation of ``scout``, its child started."""

    live: _Live
    channel: _Channel
    runtime: _SubagentRuntimeImpl
    parent_path: Path
    child_id: str
    child_path: Path
    first_key: str
    session: Any = field(repr=False, default=None)


async def _delegated(
    tmp_path: Path,
    *,
    mode: PermissionMode = PermissionMode.PLAN,
    profile: Any = None,
    started: bool = True,
    cwd: str | None = None,
    live: _Live | None = None,
    task: str = "look at the thing",
) -> _Scene:
    session, _repo, parent_path = await _parent(tmp_path)
    live = live if live is not None else _Live(cwd=tmp_path / "p")
    live.session = session
    channel = _Channel()
    runtime = _runtime(live, channel)
    first = await runtime.spawn_granted(
        _grant(mode), _resolved(profile), task, cwd=cwd, record=_meta("tc-1")
    )
    assert first.ok and first.session_id is not None, first.summary
    child_path = Path(channel.plans[0].session_path or "")
    if started:
        await _received_task(child_path, f"Task: {task}")
    return _Scene(
        live=live,
        channel=channel,
        runtime=runtime,
        parent_path=parent_path,
        child_id=first.session_id,
        child_path=child_path,
        first_key=first.id,
        session=session,
    )


async def _target(scene: _Scene) -> ResumeTarget:
    found = await scene.runtime.find_resume_target(scene.child_id)
    assert isinstance(found, ResumeTarget), found
    return found


async def _resume(
    scene: _Scene,
    instruction: str = "keep going",
    *,
    mode: PermissionMode = PermissionMode.PLAN,
    resolved: Any = None,
    tool_call_id: str = "tc-2",
) -> Any:
    target = await _target(scene)
    return await scene.runtime.resume_granted(
        _grant(mode),
        resolved if resolved is not None else _resolved(),
        target,
        instruction,
        record=_meta(tool_call_id),
    )


def _argv(plan: SpawnPlan) -> list[str]:
    """The argv ``PrintChannel`` builds for ``plan``: from its limits alone."""

    assert plan.limits is not None
    return build_child_argv(
        limited_profile(plan.resolved.profile, plan.limits),
        prompt_path="/tmp/prompt.md",
        task=plan.task,
        permission_mode=plan.permission_mode,
        child_cwd=plan.cwd,
        parent_cwd=plan.parent_cwd,
        session_path=plan.session_path,
        model_pin=plan.limits.model,
    )[3:]


def _rewrite_line(path: Path, index: int, edit: Any) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[index] = json.dumps(edit(json.loads(lines[index])))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _first_run_record(scene: _Scene) -> dict[str, Any]:
    """The first run's LAST ``aelix.child_session`` line (its settle)."""

    return [
        data
        for kind, data in await _records(scene.session)
        if kind == CHILD_SESSION_TYPE and data["key"] == scene.first_key
    ][-1]


async def _forge(scene: _Scene, **changes: Any) -> None:
    """Append a later line for the first run's key — what a hand-edited parent
    file APPENDS. The reader takes what the first run was from its START line
    (#610 F2), so such a line can only agree with it or be damage."""

    data = {**await _first_run_record(scene), **changes}
    await scene.session.append_custom_entry(CHILD_SESSION_TYPE, data)


async def _rewrite_first_run(scene: _Scene, edit: Any) -> None:
    """EVERY line of the first run's key, rewritten by ``edit`` in the parent's
    file, and the parent reopened from it — what a hand-edited parent file
    says when its start says it too. The scene's parent is the reopened one."""

    lines = scene.parent_path.read_text(encoding="utf-8").splitlines()
    for at, line in enumerate(lines):
        entry = json.loads(line)
        data = entry.get("data")
        if (
            entry.get("customType") == CHILD_SESSION_TYPE
            and isinstance(data, dict)
            and data.get("key") == scene.first_key
        ):
            lines[at] = json.dumps({**entry, "data": edit(data)})
    scene.parent_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reopened = Session(
        await JsonlSessionStorage.open(LocalFileSystem(), str(scene.parent_path))
    )
    scene.session = reopened
    scene.live.session = reopened


# === The receipt: limits and resumes on every run (D8, D12, D15) ==============


async def test_every_start_and_settle_records_the_limits_the_argv_was_built_from(
    tmp_path: Path,
) -> None:
    scene = await _delegated(tmp_path)
    records = await _records(scene.session)
    start, settle = records[0][1], records[2][1]
    plan = scene.channel.plans[0]

    assert plan.limits is not None
    assert start["limits"] == settle["limits"] == plan.limits.to_record()
    assert start["resumes"] is None and settle["resumes"] is None
    assert start["limits"] == {
        "granted_mode": "plan",
        "tools": ALL_BUILTINS,
        "context_files": True,
        "thinking": None,
        "model": None,
        "cwd": str((tmp_path / "p").resolve()),
        # Absolute on every platform (#610 F15) — ``/home/u/…`` is not, on a
        # Windows Python ≥ 3.13, which wants a drive.
        "profile_source": _SCOUT_SOURCE,
        "profile_scope": "user",
    }
    # A JSON value, read back exactly (ADR-0242 rule 1.7: lists, not tuples).
    assert ChildLimits.from_record(json.loads(json.dumps(start["limits"]))) == plan.limits
    # The argv carries what the record says, and no other tool list.
    argv = _argv(plan)
    assert argv[argv.index("--tools") + 1] == ",".join(ALL_BUILTINS)


async def test_a_resume_is_a_new_key_naming_the_first_and_writes_nothing_into_the_child(
    tmp_path: Path,
) -> None:
    """D12/D15. The first run's records stay exactly as they were; the resume
    gets its own four under a NEW key with ``resumes`` = the first; its start
    names the same child file; and the parent writes nothing into that file —
    the child holds it (pi #8476/#8502)."""

    scene = await _delegated(tmp_path)
    before_records = await _records(scene.session)
    before_bytes = scene.child_path.read_bytes()

    result = await _resume(scene, "now write the summary")

    assert result.ok is True
    assert scene.child_path.read_bytes() == before_bytes
    records = await _records(scene.session)
    assert records[:4] == before_records
    assert _shape(records[4:]) == ["start", "pending", "settle", "final"]
    start = records[4][1]
    assert start["key"] == result.id and start["key"] != scene.first_key
    assert start["resumes"] == scene.first_key
    assert start["child"] == before_records[0][1]["child"]
    assert start["task_preview"] == "now write the summary", "the instruction, not the preamble"
    assert (start["tool_call_id"], start["mode"], start["index"]) == ("tc-2", "single", 0)
    assert records[6][1]["resumes"] == scene.first_key, "the settle carries it too"

    plan = scene.channel.plans[1]
    assert plan.session_path == str(scene.child_path)
    assert plan.resumes == scene.first_key
    assert plan.task == resume_task("now write the summary")
    assert result.session_id == scene.child_id


def test_the_resume_task_is_the_preamble_then_the_instruction() -> None:
    """D13: one sentence for every resume, and the parser's reserve covers it."""

    task = resume_task("finish the parser")
    assert task == f"{RESUME_PREAMBLE}\n\nNext instruction:\nfinish the parser"
    assert "may or may not have run" in RESUME_PREAMBLE
    widest = "x" * (MAX_TASK_BYTES - RESUME_PREAMBLE_RESERVE)
    assert len(resume_task(widest).encode("utf-8")) <= MAX_TASK_BYTES


# === The intersection (D9, D10) ===============================================


@pytest.mark.parametrize("ceiling", list(PermissionMode))
@pytest.mark.parametrize("live", list(PermissionMode))
def test_resume_clamp_is_the_rank_minimum(ceiling: PermissionMode, live: PermissionMode) -> None:
    clamped = resume_clamp(ceiling, live)
    assert posture_rank(clamped) == min(posture_rank(ceiling), posture_rank(live))


def _limits(**kwargs: Any) -> ChildLimits:
    base: dict[str, Any] = {
        "granted_mode": PermissionMode.AUTO_ACCEPT,
        "tools": ("bash", "read", "write"),
        "context_files": True,
        "thinking": "high",
        "model": ModelPin(model="m-first", provider="p-first"),
        "cwd": "/",
        "profile_source": "/home/u/.aelix/agent/agents/scout.md",
        "profile_scope": "user",
    }
    base.update(kwargs)
    return ChildLimits(**base)


def test_resume_limits_takes_the_narrower_side_of_every_authority(tmp_path: Path) -> None:
    stored = _limits(cwd=str(tmp_path))
    narrowed_since = _limits(
        granted_mode=PermissionMode.PLAN,
        tools=("ls", "read", "write"),
        context_files=False,
        thinking="low",
        model=ModelPin(model="m-later", provider="p-later"),
        cwd=str(tmp_path),
    )
    out = resume_limits(stored, narrowed_since, parent_cwd=str(tmp_path))
    assert out.limits.granted_mode is PermissionMode.PLAN
    assert out.limits.tools == ("read", "write"), "the recorded order, the live set"
    assert out.limits.context_files is False
    assert out.dropped == ("bash",), "what the resumed child had and has no more"
    # Not authorities: pinned to the first run, never re-derived (Codex #26718).
    assert out.limits.thinking == "high"
    assert out.limits.model == ModelPin(model="m-first", provider="p-first")

    widened_since = _limits(
        granted_mode=PermissionMode.YOLO, tools=tuple(ALL_BUILTINS), cwd=str(tmp_path)
    )
    narrow_first = _limits(
        granted_mode=PermissionMode.PLAN, tools=("read",), context_files=False, cwd=str(tmp_path)
    )
    out = resume_limits(narrow_first, widened_since, parent_cwd=str(tmp_path))
    assert out.limits.granted_mode is PermissionMode.PLAN
    assert out.limits.tools == ("read",)
    assert out.limits.context_files is False
    assert out.dropped == ()


async def test_a_parent_that_narrowed_resumes_the_child_narrower(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    scene.live.tools = ["read", "ls", "my_extension_tool"]
    scene.live.context_files = False

    await _resume(scene)

    plan = scene.channel.plans[1]
    assert plan.limits is not None
    assert plan.limits.tools == ("ls", "read")
    assert plan.limits.context_files is False
    assert plan.dropped_tools == ("bash", "edit", "find", "grep", "write")
    argv = _argv(plan)
    assert argv[argv.index("--tools") + 1] == "ls,read"
    assert "--no-context-files" in argv


async def test_a_parent_that_widened_does_not_widen_the_resumed_child(tmp_path: Path) -> None:
    """B1's belt: even a grant that arrives WIDER than the first run's — a hook
    that skipped the ceiling — runs at the first run's posture, because
    ``resume_granted`` floors it with the recorded grant."""

    live = _Live(cwd=tmp_path / "p", tools=["read"])
    scene = await _delegated(tmp_path, live=live)
    scene.live.tools = None  # every built-in now
    scene.live.context_files = True

    result = await _resume(scene, mode=PermissionMode.YOLO)

    plan = scene.channel.plans[1]
    assert plan.limits is not None
    assert plan.permission_mode is PermissionMode.PLAN
    assert plan.limits.granted_mode is PermissionMode.PLAN
    assert plan.limits.tools == ("read",)
    argv = _argv(plan)
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv[argv.index("--tools") + 1] == "read"
    records = await _records(scene.session)
    assert records[4][1]["permission_mode"] == "plan"
    assert result.permission_mode == "plan"


@pytest.mark.parametrize(
    ("asked", "runs"),
    [
        (PermissionMode.AUTO_ACCEPT, PermissionMode.AUTO_ACCEPT),
        (PermissionMode.YOLO, PermissionMode.AUTO_ACCEPT),
        (PermissionMode.PLAN, PermissionMode.PLAN),
    ],
)
async def test_a_first_run_a_human_widened_resumes_up_to_that_grant_and_no_further(
    tmp_path: Path, asked: PermissionMode, runs: PermissionMode
) -> None:
    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    await _resume(scene, mode=asked)
    plan = scene.channel.plans[1]
    assert plan.permission_mode is runs
    assert (await _records(scene.session))[4][1]["limits"]["granted_mode"] == runs.value


async def test_the_resumed_child_keeps_its_model_and_thinking_after_the_parent_moved(
    tmp_path: Path,
) -> None:
    """D9: a ``/model`` and a ``/thinking`` after the delegation do not reach the
    resumed child — the argv carries the first run's pin (critique: Codex
    #26718 / #36433 switched a resumed agent's model)."""

    live = _Live(
        cwd=tmp_path / "p", model=Model(id="m-first", provider="p-first"), thinking="high"
    )
    scene = await _delegated(tmp_path, live=live)
    scene.live.model = Model(id="m-later", provider="p-later")
    scene.live.thinking = "low"

    await _resume(scene)

    plan = scene.channel.plans[1]
    assert plan.limits is not None
    assert plan.limits.model == ModelPin(model="m-first", provider="p-first")
    assert plan.limits.thinking == "high"
    parsed = parse_args(_argv(plan))
    assert (parsed.model, parsed.provider, parsed.thinking) == ("m-first", "p-first", "high")
    assert (await _records(scene.session))[4][1]["requested_model"] == "m-first"


# === What a record can put on an argv (D8, critique B2) =======================


_GOOD = {
    "granted_mode": "plan",
    "tools": ["read"],
    "context_files": True,
    "thinking": None,
    "model": {"model": "m1", "provider": "own", "route": "a" * 64},
    # ``abspath``: ``/repo`` is not absolute on a Windows Python ≥ 3.13 (it
    # wants a drive), and every check after ``cwd`` would read the wrong field.
    "cwd": os.path.abspath("/repo"),
    "profile_source": _SCOUT_SOURCE,
    "profile_scope": "user",
}


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"granted_mode": "root"}, "granted_mode"),
        ({"granted_mode": 3}, "granted_mode"),
        ({"tools": ["read", "-e"]}, "tools"),
        ({"tools": [AGENT_TOOL_NAME]}, "tools"),
        ({"tools": ["read", "read"]}, "tools"),
        ({"tools": "read"}, "tools"),
        ({"context_files": "yes"}, "context_files"),
        ({"thinking": "--session /tmp/x.jsonl"}, "thinking"),
        ({"thinking": "ultra"}, "thinking"),
        ({"model": {"model": "-e", "provider": "own", "route": None}}, "model.model"),
        ({"model": {"model": "m 1", "provider": "own", "route": None}}, "model.model"),
        ({"model": {"model": "m\x1b[8m", "provider": "own", "route": None}}, "model.model"),
        ({"model": {"model": "m1", "provider": "--api-key", "route": None}}, "model.provider"),
        ({"model": {"model": "m1", "provider": "own", "route": "zz" * 32}}, "model.route"),
        ({"model": {"model": "m1", "provider": None, "route": "a" * 64}}, "model.route"),
        ({"model": {"model": "m1", "provider": "own", "route": None, "x": 1}}, "model"),
        ({"model": ["--model", "m1"]}, "model"),
        ({"cwd": "relative/dir"}, "cwd"),
        ({"cwd": "/repo/\x1b[8m"}, "cwd"),
        ({"cwd": ""}, "cwd"),
        ({"profile_source": "/x\x00y"}, "profile_source"),
        ({"profile_source": "agents/scout.md"}, "profile_source"),  # F15: relative
        ({"profile_scope": ""}, "profile_scope"),
        ({"extensions": ["/tmp/evil.py"]}, "does not know"),
        ({"argv": ["-e", "/tmp/evil.py"]}, "does not know"),
    ],
)
def test_a_forged_limits_field_is_refused(change: dict[str, Any], reason: str) -> None:
    with pytest.raises(LimitsError) as caught:
        ChildLimits.from_record({**_GOOD, **change})
    assert reason in caught.value.reason
    for value in change.values():
        # The reason names the field, never the forged value (it reaches the model).
        if isinstance(value, str) and len(value) > 3:
            assert value not in caught.value.reason


@pytest.mark.parametrize("missing", sorted(_GOOD))
def test_a_limits_object_missing_a_field_is_refused(missing: str) -> None:
    record = {k: v for k, v in _GOOD.items() if k != missing}
    with pytest.raises(LimitsError, match=missing):
        ChildLimits.from_record(record)


def test_a_good_limits_record_round_trips() -> None:
    limits = ChildLimits.from_record(_GOOD)
    assert limits.to_record() == _GOOD
    assert limits.model == ModelPin(model="m1", provider="own", route="a" * 64)
    assert ChildLimits.from_record({**_GOOD, "model": None}).model == ModelPin()


@pytest.mark.parametrize(
    ("profile", "parent"),
    [
        ({}, None),
        ({}, Model(id="claude-haiku-4-5", provider="anthropic")),
        ({}, Model(id="claude-haiku-4-5")),
        ({}, Model()),
        ({"model": "z-ai/glm-5.3-flash"}, Model(id="ignored", provider="anthropic")),
        ({"provider": "openrouter"}, None),
        ({"model": "glm", "provider": "openrouter"}, None),
    ],
)
def test_a_model_pin_emits_exactly_the_flags_the_decision_made(
    profile: dict[str, str], parent: Any
) -> None:
    """``model_pin_flags`` is the one emission site; a pin read off
    ``child_model_flags`` gives the child the argv it always got."""

    agent = _profile(**profile)
    assert model_pin_flags(child_model_pin(agent, parent)) == child_model_flags(agent, parent)


async def test_a_registration_route_pin_round_trips_too(tmp_path: Path) -> None:
    registry = await _registry(tmp_path)
    registry.register_provider("own", ProviderConfigInput(api_key="x", models={"m1": _row("own")}))
    parent = registry.find("own", "m1")
    agent = _profile()

    pin = child_model_pin(agent, parent, registry)
    assert pin.route is not None and len(pin.route) == 64
    assert model_pin_flags(pin) == child_model_flags(agent, parent, registry)


async def test_a_resumed_childs_argv_carries_no_recorded_string_as_a_flag(
    tmp_path: Path,
) -> None:
    """Whatever a record holds, the argv a resume launches is the one the
    builder makes from typed values: one ``--session`` (the derived file), no
    extension, no flag the parser does not know."""

    scene = await _delegated(tmp_path)
    await _resume(scene)
    argv = _argv(scene.channel.plans[1])
    parsed = parse_args(argv)
    assert parsed.unknown_flags == {}
    assert parsed.extensions == []
    assert parsed.session == str(scene.child_path)
    assert argv.count("--session") == 1


async def test_a_forged_record_is_refused_before_any_process(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    forged = {**(await _first_run_record(scene))["limits"]}
    forged["model"] = {"model": "-e", "provider": "/tmp/evil.py", "route": None}
    await _rewrite_first_run(scene, lambda data: {**data, "limits": forged})

    found = await scene.runtime.find_resume_target(scene.child_id)

    assert isinstance(found, str)
    assert found.startswith("Resume refused: that agent's session record is damaged")
    assert "model.model" in found
    assert "evil" not in found, "a forged value never reaches the model"
    assert len(scene.channel.plans) == 1, "no resume ran"


async def test_the_recorded_path_is_never_a_way_to_the_child_file(tmp_path: Path) -> None:
    """D6: the file is DERIVED from where the parent is now. A record whose
    ``path`` names a perfectly good copy elsewhere does not make a missing child
    resumable, and a ``rel`` under another directory is refused outright."""

    scene = await _delegated(tmp_path)
    decoy = tmp_path / "decoy.jsonl"
    decoy.write_bytes(scene.child_path.read_bytes())
    child = {**(await _first_run_record(scene))["child"], "path": str(decoy)}
    await _rewrite_first_run(scene, lambda data: {**data, "child": child})
    scene.child_path.unlink()
    assert await scene.runtime.find_resume_target(scene.child_id) == RESUME_FILE_GONE

    elsewhere = {**child, "rel": f"../../elsewhere/{scene.first_key}.jsonl"}
    await _rewrite_first_run(scene, lambda data: {**data, "child": elsewhere})
    assert await scene.runtime.find_resume_target(scene.child_id) == RESUME_FORKED


# === Finding the target, and every refusal on the way (D5-D7, D11) ============


async def test_a_delegated_and_started_child_is_found(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    assert target.session_id == scene.child_id
    assert target.first_key == scene.first_key
    assert target.path == str(scene.child_path)
    assert target.rel == f"p/{scene.first_key}.jsonl"
    assert target.profile == "scout"
    assert target.task_preview == "look at the thing"
    assert target.last_status == "finished"
    assert target.limits == scene.channel.plans[0].limits
    assert target.parent_session_id == (await scene.session.get_metadata()).id


async def test_a_missing_or_unknown_id_is_refused_with_the_listing(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    asks = ((None, RESUME_NEEDS_ID), ("", RESUME_NEEDS_ID), ("nope", RESUME_UNKNOWN_ID))
    for asked, head in asks:
        found = await scene.runtime.find_resume_target(asked)
        assert isinstance(found, str)
        assert found.startswith(head)
        assert RESUMABLE_HEAD in found and scene.child_id in found
    # The spawn key is not an id a model was shown, and does not work as one.
    by_key = await scene.runtime.find_resume_target(scene.first_key)
    assert isinstance(by_key, str) and by_key.startswith(RESUME_UNKNOWN_ID)


async def test_an_agents_run_child_is_not_the_models_to_resume(tmp_path: Path) -> None:
    """D5 / critique m4: ``/agents run`` records ``tool_call_id: null``. Its task
    was typed by a person, so it is neither resumable nor listed."""

    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    channel = _Channel()
    runtime = _runtime(live, channel)
    typed = await runtime.spawn(_resolved(), "a task a human typed")
    assert typed.session_id is not None
    await _received_task(channel.plans[0].session_path or "")

    assert await runtime.find_resume_target(typed.session_id) == RESUME_NOT_YOURS
    assert await runtime.resumable_agents() == RESUMABLE_NONE


async def test_a_record_from_before_610_is_refused(tmp_path: Path) -> None:
    """Every line of a run from before #610 lacks ``limits`` and ``resumes``."""

    scene = await _delegated(tmp_path)
    await _rewrite_first_run(
        scene, lambda data: {k: v for k, v in data.items() if k not in ("limits", "resumes")}
    )
    assert await scene.runtime.find_resume_target(scene.child_id) == RESUME_BEFORE_LIMITS


async def test_runs_that_do_not_chain_to_one_first_run_are_refused(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    first = await _first_run_record(scene)
    # A resume record whose first run is on no branch this parent has.
    orphan = {**first, "key": "sub-0000000000aa", "resumes": "sub-ffffffffffff"}
    other_child = {**first["child"], "session_id": "11111111-2222-3333-4444-555555555555"}
    await scene.session.append_custom_entry(CHILD_SESSION_TYPE, {**orphan, "child": other_child})
    assert (
        await scene.runtime.find_resume_target(other_child["session_id"])
        == RESUME_FIRST_RUN_MISSING
    )
    # A resume of THIS child that names another first run.
    await scene.session.append_custom_entry(
        CHILD_SESSION_TYPE, {**orphan, "key": "sub-0000000000bb"}
    )
    found = await scene.runtime.find_resume_target(scene.child_id)
    assert isinstance(found, str) and "do not chain" in found


async def test_a_forked_conversation_does_not_resume_the_originals_child(tmp_path: Path) -> None:
    """D6. A fork copies the parent's records (``repo_utils.get_entries_to_fork``)
    but not its children: the child stays under the ORIGINAL parent's stem and
    its origin names the original. Both checks refuse with the fork sentence."""

    scene = await _delegated(tmp_path)
    original_id = (await scene.session.get_metadata()).id
    fork_path = scene.parent_path.with_name("q.jsonl")
    fork = Session(
        await JsonlSessionStorage.create(
            LocalFileSystem(),
            str(fork_path),
            cwd=str(tmp_path / "p"),
            session_id="fork-of-" + original_id[:8],
            entries=list(await scene.session.get_branch()),
        )
    )
    assert await find_resume_target(fork, scene.child_id) == RESUME_FORKED

    # Even with the child copied beside a fork and the fork's records — its
    # START included, which is what the reader reads (F2) — pointing there, the
    # child's origin names another parent session.
    copy_path = scene.parent_path.with_name("r.jsonl")
    copied = copy_path.with_name("r") / scene.child_path.name
    copied.parent.mkdir()
    copied.write_bytes(scene.child_path.read_bytes())

    def _pointing_at_r(entry: Any) -> Any:
        data = getattr(entry, "data", None)
        if getattr(entry, "custom_type", None) != CHILD_SESSION_TYPE or not (
            isinstance(data, dict) and data.get("key") == scene.first_key
        ):
            return entry
        return replace(entry, data={**data, "child": {**data["child"], "rel": f"r/{copied.name}"}})

    copy = Session(
        await JsonlSessionStorage.create(
            LocalFileSystem(),
            str(copy_path),
            cwd=str(tmp_path / "p"),
            session_id="copy-of-" + original_id[:8],
            entries=[_pointing_at_r(entry) for entry in await scene.session.get_branch()],
        )
    )
    assert await find_resume_target(copy, scene.child_id) == RESUME_FORKED


async def test_a_parent_with_no_session_file_has_nothing_to_resume(tmp_path: Path) -> None:
    in_memory = Session(MemorySessionStorage())
    assert await find_resume_target(in_memory, "any") == RESUME_NO_SESSION
    assert await find_resume_target(None, "any") == RESUME_NO_SESSION

    outside = tmp_path / "loose" / "p.jsonl"  # not inside a bucket
    outside.parent.mkdir()
    loose = Session(
        await JsonlSessionStorage.create(
            LocalFileSystem(), str(outside), cwd=str(tmp_path), session_id="loose-1"
        )
    )
    assert await find_resume_target(loose, "any") == RESUME_NO_SESSION


async def test_a_child_that_never_started_is_refused(tmp_path: Path) -> None:
    """Critique m5: header and origin only — its task was never delivered, so
    "continue" would be the first thing it ever heard."""

    scene = await _delegated(tmp_path, started=False)
    assert await scene.runtime.find_resume_target(scene.child_id) == RESUME_NEVER_STARTED


def _damage_header(path: Path) -> None:
    _rewrite_line(path, 0, lambda header: {**header, "id": "someone-else"})


def _damage_origin(path: Path) -> None:
    _rewrite_line(
        path, 1, lambda origin: {**origin, "data": {**origin["data"], "key": "sub-000000000000"}}
    )


def _tear_header(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(['{"type": "sess', *lines[1:]]) + "\n", encoding="utf-8")


def _make_directory(path: Path) -> None:
    path.unlink()
    path.mkdir()


@pytest.mark.parametrize(
    ("damage", "detail"),
    [
        (_damage_header, "session header"),
        (_damage_origin, "origin record"),
        (_tear_header, "session header"),
        (_make_directory, "not a regular file"),
    ],
    ids=["header-id", "origin-key", "torn-header", "directory"],
)
async def test_a_damaged_child_file_is_refused(
    tmp_path: Path, damage: Any, detail: str
) -> None:
    scene = await _delegated(tmp_path)
    damage(scene.child_path)
    found = await scene.runtime.find_resume_target(scene.child_id)
    assert isinstance(found, str)
    assert found.startswith("Resume refused: that agent's session record is damaged")
    assert detail in found


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on windows")
async def test_a_symlinked_child_file_is_refused(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    real = tmp_path / "real.jsonl"
    real.write_bytes(scene.child_path.read_bytes())
    scene.child_path.unlink()
    scene.child_path.symlink_to(real)
    found = await scene.runtime.find_resume_target(scene.child_id)
    assert isinstance(found, str) and "symbolic link" in found


# === The checks resume_granted repeats, and what a refusal costs (D11, D12) ===


async def _nothing_spent(scene: _Scene, result: Any, reason: str, records_before: Any) -> None:
    assert result.ok is False
    assert result.summary == reason
    assert len(scene.channel.plans) == 1, "the channel was never called"
    assert scene.runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT - 1
    assert await _records(scene.session) == records_before, "no run was recorded"
    assert scene.runtime._children == {}


async def test_a_child_file_gone_since_the_hook_is_refused_and_costs_nothing(
    tmp_path: Path,
) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    scene.child_path.unlink()

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, RESUME_FILE_GONE, before)


async def test_a_running_child_is_not_resumed_twice(tmp_path: Path) -> None:
    """D12: a live registry row that runs the child session refuses a second
    resume — reachable in one process once delegation runs in the background
    (#611); driven here with two resumes of one target in flight."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    scene.channel._park = True
    scene.channel.arrived.clear()  # set by the first run
    first = asyncio.ensure_future(
        scene.runtime.resume_granted(_grant(), _resolved(), target, "one", record=_meta("tc-2"))
    )
    await scene.channel.arrived.wait()
    assert scene.runtime.resume_refusal(target) == RESUME_RUNNING
    second = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "two", record=_meta("tc-3")
    )
    assert (second.ok, second.summary) == (False, RESUME_RUNNING)

    scene.channel.release.set()
    assert (await first).ok is True
    assert scene.runtime.resume_refusal(target) is None, "the row went with its run"
    assert len(scene.channel.plans) == 2


async def test_a_session_another_process_holds_is_refused(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    holder = SessionWriterLock(str(scene.child_path))
    assert holder.try_acquire()
    try:
        assert scene.runtime.resume_refusal(target) == RESUME_IN_USE
    finally:
        holder.release()
    assert scene.runtime.resume_refusal(target) is None, "the probe let go of it"
    assert holder.try_acquire(), "and so did the check"
    holder.release()


@pytest.mark.skipif(not POSIX, reason="a symlinked sidecar is the POSIX ELOOP shape")
async def test_a_lock_that_cannot_be_checked_says_why_not_that_it_is_in_use(
    tmp_path: Path,
) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    Path(f"{scene.child_path}.lock").symlink_to(tmp_path / "elsewhere")
    refusal = scene.runtime.resume_refusal(target)
    assert refusal is not None and refusal != RESUME_IN_USE
    assert refusal.startswith("Resume refused: aelix could not check")
    assert str(tmp_path) not in refusal, "no path reaches the model"


async def test_a_directory_gone_or_outside_the_parent_is_refused(tmp_path: Path) -> None:
    (tmp_path / "p" / "sub").mkdir(parents=True)
    scene = await _delegated(tmp_path, cwd="sub")
    target = await _target(scene)
    assert target.limits.cwd == str((tmp_path / "p" / "sub").resolve())

    elsewhere = tmp_path / "q"
    elsewhere.mkdir()
    scene.live.cwd = elsewhere
    assert scene.runtime.resume_refusal(target) == RESUME_CWD_GONE

    scene.live.cwd = tmp_path / "p"
    (tmp_path / "p" / "sub").rmdir()
    before = await _records(scene.session)
    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "go", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, RESUME_CWD_GONE, before)


@pytest.mark.parametrize(
    "profile",
    [
        _profile(file_path="/home/u/.aelix/agent/agents/other/scout.md"),
        _profile(scope="project"),
        _profile(name="spy"),
    ],
    ids=["another-file", "project-scope", "another-name"],
)
async def test_a_profile_that_is_not_the_first_runs_is_refused(
    tmp_path: Path, profile: Any
) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    refusal = scene.runtime.resume_refusal(target, resolved=_resolved(profile))
    assert refusal == RESUME_PROFILE_CHANGED
    result = await scene.runtime.resume_granted(
        _grant(), _resolved(profile), target, "go", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, RESUME_PROFILE_CHANGED, before)


async def test_a_moved_registration_route_is_refused_before_anything_is_spent(
    tmp_path: Path,
) -> None:
    """Critique m6. The child would refuse its first turn with "requires its
    original explicit provider registration and route" — after the consent and
    the budget. The parent asks its own registry the same question first."""

    registry = await _registry(tmp_path)
    registry.register_provider("own", ProviderConfigInput(api_key="x", models={"m1": _row("own")}))
    live = _Live(cwd=tmp_path / "p", model=registry.find("own", "m1"), registry=registry)
    scene = await _delegated(tmp_path, live=live)
    target = await _target(scene)
    assert target.limits.model.route is not None
    assert scene.runtime.resume_refusal(target) is None

    registry.register_provider(
        "own",
        ProviderConfigInput(
            api_key="x", models={"m1": _row("own", base="http://127.0.0.1:9/v1")}
        ),
    )
    before = await _records(scene.session)
    assert scene.runtime.resume_refusal(target) == RESUME_ROUTE_MOVED
    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "go", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, RESUME_ROUTE_MOVED, before)


async def test_a_conversation_switched_before_the_spawn_is_refused(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    other, _repo, _path = await _parent(tmp_path, name="o.jsonl")
    scene.live.session = other

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "go", record=_meta("tc-2")
    )
    assert (result.ok, result.summary) == (False, RESUME_CONVERSATION_CHANGED)
    assert len(scene.channel.plans) == 1
    assert await _records(other) == []


async def test_a_grant_that_is_not_consented_is_refused(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    target = await _target(scene)
    refused = replace(_grant(), consented=False)
    result = await scene.runtime.resume_granted(refused, _resolved(), target, "go")
    assert result.ok is False and result.status == "declined"
    assert len(scene.channel.plans) == 1


# === Exception safety: limits are decided before admission (critique M4) ======


async def test_limits_that_cannot_be_computed_leak_no_registry_row(tmp_path: Path) -> None:
    """A raise between ``_run``'s registry insert and its ``try`` leaked the row
    for the session's life; four of them and ``MAX_LIVE_CHILDREN`` refused every
    delegation. Deciding the limits ABOVE the admission block means a raising
    registry costs that one delegation and nothing else."""

    session, _repo, _path = await _parent(tmp_path)
    broken = {"on": True}

    def _registry_getter() -> Any:
        if broken["on"]:
            raise RuntimeError("the registry is being reloaded")
        return None

    channel = _Channel()
    host = SubagentHost(
        cwd=lambda: str(tmp_path / "p"),
        session=lambda: session,
        model_registry=_registry_getter,
    )
    runtime = _SubagentRuntimeImpl(host=host, channel=channel)
    for _ in range(MAX_LIVE_CHILDREN + 1):
        with pytest.raises(RuntimeError, match="being reloaded"):
            await runtime.spawn_granted(_grant(), _resolved(), "go", record=_meta("tc-1"))
    assert runtime._children == {}
    assert runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert await _records(session) == []
    assert channel.plans == []

    broken["on"] = False
    assert (await runtime.spawn_granted(_grant(), _resolved(), "go")).ok is True


async def test_a_resume_refused_inside_run_charges_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run``'s own refusal — the intersection raising ``ResumeRefused`` — is
    above the admission block, so it costs what the checks before ``_run`` cost:
    nothing."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)

    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ResumeRefused(RESUME_CWD_GONE)

    monkeypatch.setattr(runtime_module, "resume_limits", _refuse)
    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "go", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, RESUME_CWD_GONE, before)


# === Channels (D8, critique M4) ===============================================


async def test_the_rpc_channel_refuses_a_resume_plan(tmp_path: Path) -> None:
    built: list[Any] = []

    def _never(*args: Any, **kwargs: Any) -> list[str]:
        built.append((args, kwargs))
        return [sys.executable, "-c", "pass"]

    plan = SpawnPlan(
        id="sub-rpc",
        resolved=_resolved(),
        task=resume_task("go"),
        cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        permission_mode=PermissionMode.PLAN,
        limits=_limits(cwd=str(tmp_path)),
        resumes="sub-0123456789ab",
    )
    result = await RpcChannel(argv_builder=_never).run(plan)
    assert result.ok is False
    assert "cannot resume" in f"{result.summary} {result.error}"
    assert built == [], "no process was made"


async def test_the_print_channel_refuses_a_resume_plan_without_limits(tmp_path: Path) -> None:
    built: list[Any] = []

    def _never(*args: Any, **kwargs: Any) -> list[str]:
        built.append(kwargs)
        return [sys.executable, "-c", "pass"]

    plan = SpawnPlan(
        id="sub-print",
        resolved=_resolved(),
        task=resume_task("go"),
        cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        permission_mode=PermissionMode.PLAN,
        resumes="sub-0123456789ab",
    )
    result = await PrintChannel(argv_builder=_never).run(plan)
    assert result.ok is False and "no recorded limits" in f"{result.summary} {result.error}"
    assert built == []


_OK_CHILD = (
    "import json, sys\n"
    "for event in ({'type': 'agent_start'}, {'type': 'message_end', 'message': "
    "{'role': 'assistant', 'content': [{'type': 'text', 'text': 'done'}], "
    "'stop_reason': 'end_turn'}}, {'type': 'agent_end'}):\n"
    "    sys.stdout.write(json.dumps(event) + '\\n')\n"
)


async def test_the_print_channel_launches_a_resume_from_its_limits(tmp_path: Path) -> None:
    """The real ``PrintChannel.run`` (a stdlib child stands in for aelix): the
    argv it hands its builder carries the plan's limits — the pinned model and
    the intersected tools — and not the parent's model as it is now."""

    seen: list[dict[str, Any]] = []

    def _capture(*args: Any, **kwargs: Any) -> list[str]:
        seen.append({"argv": build_child_argv(*args, **kwargs), **kwargs})
        return [sys.executable, "-c", _OK_CHILD]

    live = _Live(cwd=tmp_path / "p", model=Model(id="m-first", provider="p-first"))
    scene = await _delegated(tmp_path, live=live)
    scene.runtime.channel = PrintChannel(
        grace=1.0, argv_builder=_capture, parent_model=lambda: scene.live.model
    )
    scene.live.model = Model(id="m-later", provider="p-later")
    scene.live.tools = ["read"]

    result = await _resume(scene)

    assert result.ok is True, result.summary
    (call,) = seen
    assert call["model_pin"] == ModelPin(model="m-first", provider="p-first")
    assert "parent_model" not in call, "the live parent model is not read at launch"
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "m-first"
    assert argv[argv.index("--tools") + 1] == "read"
    assert argv[argv.index("--session") + 1] == str(scene.child_path)
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert any(token.startswith("Task: " + RESUME_PREAMBLE) for token in argv)


# === The listing (D7) =========================================================


async def test_the_listing_is_newest_first_bounded_and_counts_the_rest(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    runtime = _runtime(live, _Channel())
    ids = []
    for n in range(LISTED_AGENTS + 2):
        runtime.reset_delegation_budget()
        result = await runtime.spawn_granted(
            _grant(), _resolved(), f"task number {n}", record=_meta(f"tc-{n}")
        )
        ids.append(result.session_id)

    listing = await runtime.resumable_agents()
    lines = listing.splitlines()
    assert lines[0] == RESUMABLE_HEAD
    shown = lines[1:-1]
    assert len(shown) == LISTED_AGENTS
    assert [line.split()[1] for line in shown] == list(reversed(ids))[:LISTED_AGENTS]
    assert lines[-1] == "- … and 2 more not shown"
    assert shown[0].endswith('(scout, finished): "task number 11"')
    assert len(listing.encode("utf-8")) <= LISTING_BYTES


async def test_the_listing_stays_inside_its_byte_budget(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    runtime = _runtime(live, _Channel())
    for n in range(LISTED_AGENTS):
        runtime.reset_delegation_budget()
        await runtime.spawn_granted(_grant(), _resolved(), "가" * 200, record=_meta(f"tc-{n}"))

    listing = await runtime.resumable_agents()
    assert len(listing.encode("utf-8")) <= LISTING_BYTES
    lines = listing.splitlines()
    shown = len(lines) - 2
    assert shown < LISTED_AGENTS, "three-byte previews cannot all fit"
    assert lines[-1] == f"- … and {LISTED_AGENTS - shown} more not shown"


async def test_the_listing_cleans_what_a_record_says(tmp_path: Path) -> None:
    """Records are untrusted (critique B2/m4): one line per agent, no escape
    sequence, the preview cut to 80 characters and quoted."""

    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    runtime = _runtime(live, _Channel())
    hostile = 'line one\nline two \x1b[2J\x1b[31m"quoted"\x1b[0m ' + "y" * 200
    await runtime.spawn_granted(
        _grant(),
        _resolved(_profile(name="evil\nprofile\x1b[8m")),
        hostile,
        record=_meta("tc-1"),
    )

    listing = await runtime.resumable_agents()
    lines = listing.splitlines()
    assert len(lines) == 2, listing
    entry = lines[1]
    assert "\x1b" not in entry
    preview = json.loads(entry.split(": ", 1)[1])
    assert preview.startswith('line one line two "quoted" yyy')
    assert len(preview) == 80 and preview.endswith("…")
    assert "(evil profile" in entry


async def test_the_listing_says_how_each_agents_last_run_ended(tmp_path: Path) -> None:
    from aelix_coding_agent.subagent_contract import SubagentOutcome, SubagentResult

    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    outcomes: dict[SubagentOutcome, str] = {
        "ok": "finished",
        "error": "failed",
        "aborted": "stopped",
    }
    for status in outcomes:
        channel = _Channel(
            result=SubagentResult(
                id="x", profile="scout", ok=status == "ok", status=status, summary=status
            )
        )
        runtime = _runtime(live, channel)
        await runtime.spawn_granted(_grant(), _resolved(), status, record=_meta(f"tc-{status}"))
    parked = _Channel(park=True)
    runtime = _runtime(live, parked)
    in_flight = asyncio.ensure_future(
        runtime.spawn_granted(_grant(), _resolved(), "running", record=_meta("tc-run"))
    )
    await parked.arrived.wait()

    listing = await runtime.resumable_agents()
    said = {line.rsplit(": ", 1)[1]: line for line in listing.splitlines()[1:]}
    for status, word in outcomes.items():
        assert f", {word}): " in said[json.dumps(status)]
    assert ", unknown): " in said['"running"'], "a start alone is no outcome"

    parked.release.set()
    await in_flight


async def test_the_listing_orders_and_words_each_agent_by_its_latest_run(
    tmp_path: Path,
) -> None:
    """D7 lists agents newest first "with how each last ended": both by the
    LATEST run. A stopped agent resumed to the end reads "finished" and moves
    above one delegated after it (final verify m1: neither ``first`` in place
    of ``latest`` was caught)."""

    from aelix_coding_agent.subagent_contract import SubagentResult

    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    stopped = _Channel(
        result=SubagentResult(id="x", profile="scout", ok=False, status="aborted", summary="cut")
    )
    older = await _runtime(live, stopped).spawn_granted(
        _grant(), _resolved(), "older task", record=_meta("tc-older")
    )
    await _received_task(stopped.plans[0].session_path or "", "Task: older task")
    newer = await _runtime(live, _Channel()).spawn_granted(
        _grant(), _resolved(), "newer task", record=_meta("tc-newer")
    )
    runtime = _runtime(live, _Channel())

    def rows(listing: str) -> list[tuple[str, str]]:
        return [
            (line.split()[1], line.split(", ", 1)[1].split(")", 1)[0])
            for line in listing.splitlines()[1:]
        ]

    assert rows(await runtime.resumable_agents()) == [
        (newer.session_id, "finished"),
        (older.session_id, "stopped"),
    ]

    target = await runtime.find_resume_target(older.session_id)
    assert isinstance(target, ResumeTarget), target
    resumed = await runtime.resume_granted(
        _grant(), _resolved(), target, "finish it", record=_meta("tc-resume")
    )
    assert resumed.ok, resumed.summary

    assert rows(await runtime.resumable_agents()) == [
        (older.session_id, "finished"),
        (newer.session_id, "finished"),
    ]


# === Cost (D15) ===============================================================


async def test_a_resume_adds_to_the_delegations_cost_and_counts_as_a_run(
    tmp_path: Path,
) -> None:
    """A new key per run: the kernel's fold is last-line-wins PER KEY, so the
    resume's final adds to the first run's instead of replacing it."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    scene = await _delegated(tmp_path)  # 100/20/5/1, $0.25
    await _resume(scene)  # the same again

    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(scene.parent_path)))
    options = AgentHarnessOptions(model=None, session=reopened)  # type: ignore[arg-type]
    harness = AgentHarness(options)
    try:
        stats = await harness.get_session_stats()
    finally:
        await harness.dispose()
    tool = stats.tool_usage
    assert (tool.runs, tool.pending) == (2, 0)
    assert (tool.tokens.input, tool.tokens.output) == (200, 40)
    assert tool.cost == pytest.approx(0.5)
    assert stats.cost == pytest.approx(0.5) and stats.cost_known is True


# === The attach (D12) =========================================================


async def test_the_attach_counts_its_opening_records_like_open(tmp_path: Path) -> None:
    from aelix_agents.child_session import SpawnReceipt

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = scene.child_path.read_bytes()
    receipt = SpawnReceipt(
        key="sub-00000000beef",
        tool_call_id="tc-9",
        index=0,
        mode="single",
        profile="scout",
        task_preview="go",
        requested_model=None,
        permission_mode="plan",
        limits=target.limits.to_record(),
        resumes=target.first_key,
    )
    await receipt.attach(scene.session, child=target.child_file)

    assert receipt.opening_attempted == 2
    assert receipt.child == target.child_file
    assert scene.child_path.read_bytes() == before
    start = (await _records(scene.session))[-2][1]
    assert (start["key"], start["resumes"]) == ("sub-00000000beef", target.first_key)
    assert start["child"] == target.child_file.as_record()
    assert os.path.isabs(start["child"]["path"])


# === Review round 1 (#610 F2-F6, F8, F14, F15) =================================


async def test_a_later_line_that_widens_the_first_runs_limits_is_damage(tmp_path: Path) -> None:
    """F2 (Codex 1). The ceiling is the first run's START. A later line of the
    same key — the shape of a settle, a copy of the receipt with one value
    changed — that names a wider grant does not raise it: the run's lines
    disagree, and that is damage. Nothing runs."""

    scene = await _delegated(tmp_path)  # plan
    first = await _first_run_record(scene)
    await _forge(scene, limits={**first["limits"], "granted_mode": "yolo"})

    found = await scene.runtime.find_resume_target(scene.child_id)

    assert found == (
        "Resume refused: that agent's session record is damaged (its records "
        "disagree about the limits it ran with). Delegate the task anew."
    )
    assert await scene.runtime.resumable_agents() == RESUMABLE_NONE, "not listed either"
    assert len(scene.channel.plans) == 1, "no resume ran"


async def test_the_ceiling_is_the_first_run_not_the_latest_resume(tmp_path: Path) -> None:
    """F2 (verify M1, the ``data = latest.data`` mutant). First run at
    ``auto-accept-edits`` with bash, read and write; a resume under a parent
    narrowed to ``plan`` and ``read``; then a resume under a wide parent runs
    at the FIRST run's limits again — a parent that was briefly narrow does not
    narrow the agent for good — and the dialog's earlier task is the FIRST
    task, not the narrowed resume's instruction."""

    from aelix_agents.consent import RESUME_EARLIER_TASK_LABEL, build_resume_consent_title

    live = _Live(cwd=tmp_path / "p", tools=["bash", "read", "write"])
    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT, live=live)
    assert scene.channel.plans[0].limits is not None
    assert scene.channel.plans[0].limits.tools == ("bash", "read", "write")

    scene.live.tools = ["read"]
    narrowed = await _resume(scene, "narrow step", mode=PermissionMode.PLAN)
    assert narrowed.ok is True
    assert scene.channel.plans[1].permission_mode is PermissionMode.PLAN
    assert scene.channel.plans[1].limits is not None
    assert scene.channel.plans[1].limits.tools == ("read",)

    scene.live.tools = None
    target = await _target(scene)
    assert target.limits == scene.channel.plans[0].limits
    assert target.task_preview == "look at the thing"
    title = build_resume_consent_title(_resolved(), target, "wide step", PermissionMode.PLAN)
    rows = title.splitlines()
    assert rows[rows.index(RESUME_EARLIER_TASK_LABEL) + 1] == "look at the thing"

    await _resume(scene, "wide step", mode=PermissionMode.AUTO_ACCEPT, tool_call_id="tc-3")

    wide = scene.channel.plans[2]
    assert wide.permission_mode is PermissionMode.AUTO_ACCEPT
    assert wide.limits is not None and wide.limits.tools == ("bash", "read", "write")


async def test_a_file_swapped_for_another_session_after_the_hook_is_refused(
    tmp_path: Path,
) -> None:
    """F3 (Codex 3). Between the hook and the launch, the child's file is
    replaced by a VALID session of someone else's — another header id, another
    parent, a message the agent never saw. The launch runs the hook's lookup
    again and refuses; nothing is spent and that conversation is never opened."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    lines = scene.child_path.read_text(encoding="utf-8").splitlines()
    header, origin = json.loads(lines[0]), json.loads(lines[1])
    header["id"] = "11111111-2222-3333-4444-555555555555"
    origin["data"]["parent"]["session_id"] = "someone-else"
    scene.child_path.write_text(
        "\n".join([json.dumps(header), json.dumps(origin), *lines[2:]]) + "\n", encoding="utf-8"
    )
    await _received_task(scene.child_path, "FOREIGN_PARENT_SECRET")

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )

    await _nothing_spent(
        scene,
        result,
        "Resume refused: that agent's session record is damaged (its session header "
        "does not match). Delegate the task anew.",
        before,
    )


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on windows")
async def test_a_directory_swapped_for_a_link_after_the_hook_is_refused(tmp_path: Path) -> None:
    """F3 (Codex 3). The children's directory renamed out of the parent's
    bucket and a symlink left under its name: the launch's lookup sees the link
    and refuses, so nothing is appended outside the bucket."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    directory = scene.child_path.parent
    moved = tmp_path / "outside-the-bucket"
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    outside = (moved / scene.child_path.name).read_bytes()

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )

    await _nothing_spent(
        scene,
        result,
        "Resume refused: that agent's session record is damaged (its session directory "
        "is a symbolic link). Delegate the task anew.",
        before,
    )
    assert (moved / scene.child_path.name).read_bytes() == outside


async def test_a_branch_moved_off_the_first_run_after_the_hook_is_refused(
    tmp_path: Path,
) -> None:
    """F3 (Codex 3). ``move_to`` takes the first run's receipt off the current
    branch after the target was found. The launch's lookup no longer finds the
    agent on this branch and refuses, with the listing; nothing is written."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    await scene.session.move_to(None)
    before = await _records(scene.session)

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )

    await _nothing_spent(scene, result, f"{RESUME_UNKNOWN_ID}\n\n{RESUMABLE_NONE}", before)


async def test_a_target_that_changed_under_its_id_is_refused(tmp_path: Path) -> None:
    """F3: the launch's lookup finds the same id but not the approved target —
    here, other recorded limits than the dialog was bounded by. Refused as
    damaged; nothing is spent."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    other = replace(target, limits=replace(target.limits, tools=("read",)))

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), other, "keep going", record=_meta("tc-2")
    )

    await _nothing_spent(
        scene,
        result,
        "Resume refused: that agent's session record is damaged (its record changed "
        "before the resume could start). Delegate the task anew.",
        before,
    )


@pytest.mark.skipif(not POSIX, reason="hard-link counts are measured on POSIX here")
async def test_a_child_file_with_a_second_name_is_refused(tmp_path: Path) -> None:
    """F4 (Codex 4). The child's file moved out of the bucket and hard-linked
    back under its own name: two paths to one session are two writer locks
    (``SessionWriterLock`` locks by name), so the alias is refused."""

    scene = await _delegated(tmp_path)
    outside = tmp_path / "outside-child.jsonl"
    scene.child_path.rename(outside)
    os.link(outside, scene.child_path)
    assert scene.child_path.stat().st_nlink == 2

    found = await scene.runtime.find_resume_target(scene.child_id)

    assert found == (
        "Resume refused: that agent's session record is damaged (its session file has "
        "another name (a hard link)). Delegate the task anew."
    )


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on windows")
async def test_a_directory_replaced_by_a_link_to_a_sibling_is_refused(tmp_path: Path) -> None:
    """F5 (Codex 5). The first run ran in ``p/work``. ``work`` is renamed and a
    symlink to its sibling ``p/other`` left under the old name: still inside
    the parent, but not the directory the agent ran in. Refused at the hook's
    check and at the launch; nothing is spent."""

    (tmp_path / "p" / "work").mkdir(parents=True)
    (tmp_path / "p" / "other").mkdir()
    scene = await _delegated(tmp_path, cwd="work")
    target = await _target(scene)
    assert target.limits.cwd == str((tmp_path / "p" / "work").resolve())
    before = await _records(scene.session)

    (tmp_path / "p" / "work").rename(tmp_path / "p" / "original-work")
    (tmp_path / "p" / "work").symlink_to(tmp_path / "p" / "other", target_is_directory=True)

    assert scene.runtime.resume_refusal(target) == RESUME_CWD_GONE
    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, RESUME_CWD_GONE, before)


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on windows")
async def test_the_recorded_cwd_is_the_real_one_so_a_linked_parent_still_resumes(
    tmp_path: Path,
) -> None:
    """F5: what the first run records is the canonical directory (every door
    hands ``_run`` what ``resolve_child_cwd`` returned), so a parent whose own
    cwd goes through a symlink records the real path — and the identity check
    does not refuse it."""

    (tmp_path / "p").mkdir()
    linked = tmp_path / "linked-p"
    linked.symlink_to(tmp_path / "p", target_is_directory=True)
    scene = await _delegated(tmp_path, live=_Live(cwd=linked))
    target = await _target(scene)

    assert target.limits.cwd == os.path.realpath(target.limits.cwd)
    assert target.limits.cwd == str((tmp_path / "p").resolve())
    assert scene.runtime.resume_refusal(target) is None
    assert (await _resume(scene)).ok is True


def _rewrite_header(path: Path, edit: Any) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = edit(lines[0])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _header_field(**changes: Any) -> Any:
    def edit(line: str) -> str:
        header = {**json.loads(line), **changes}
        return json.dumps({k: v for k, v in header.items() if v is not _DROP})

    return edit


_DROP = object()


@pytest.mark.parametrize(
    "edit",
    [
        _header_field(version=999),
        _header_field(version="3"),
        _header_field(timestamp=_DROP),
        _header_field(timestamp=""),
        _header_field(cwd=_DROP),
        _header_field(parentSession=7),
        _header_field(type="sessions"),
        # Valid JSON (``\r`` is whitespace to it), but the loader reads text with
        # universal newlines, so its first line ends at the ``\r``.
        lambda line: line.replace(", ", ",\r", 1),
    ],
    ids=[
        "version-999",
        "version-string",
        "no-timestamp",
        "empty-timestamp",
        "no-cwd",
        "parent-session-not-a-string",
        "not-a-session",
        "carriage-return",
    ],
)
async def test_a_header_the_childs_loader_refuses_is_refused_before_anything_is_spent(
    tmp_path: Path, edit: Any
) -> None:
    """F6 (Codex 8). The parent reads the header by the session loader's own
    parser, so what the child would refuse to open — a version it does not
    know, a header with no timestamp — is refused before the budget, the
    records and the process. Each damaged file is first shown to make the
    loader itself refuse."""

    from aelix_agent_core.session.storage import SessionError

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    _rewrite_header(scene.child_path, edit)
    with pytest.raises(SessionError):
        await JsonlSessionStorage.open(LocalFileSystem(), str(scene.child_path))

    damaged = (
        "Resume refused: that agent's session record is damaged (its session header "
        "is not valid). Delegate the task anew."
    )
    assert await scene.runtime.find_resume_target(scene.child_id) == damaged
    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )
    await _nothing_spent(scene, result, damaged, before)


def test_the_header_rule_is_the_session_loaders_own() -> None:
    """F6: one rule, not two that agree today — the reader calls the loader's
    parser itself, and its first line is the one the loader takes."""

    from aelix_agent_core.session import jsonl_storage
    from aelix_agents import child_session

    assert child_session._parse_header_line is jsonl_storage._parse_header_line
    head = b'\n\r\n{"type": "session"}\r{"x": 1}\r\n\n{"y": 2}\n{"cut'
    assert child_session._head_lines(head, complete=False) == [
        '{"type": "session"}',
        '{"x": 1}',
        '{"y": 2}',
    ]


async def test_a_channel_that_cannot_resume_is_refused_before_admission(tmp_path: Path) -> None:
    """F8 (Codex 10). ``RpcChannel`` cannot launch a resume within the recorded
    limits and says so (``resumes_agents = False``); a channel that does not
    say is treated the same. The runtime refuses before ``_run``: no budget, no
    record. The channel's own refusal stays as the belt under it."""

    from aelix_agents.child_session import RESUME_UNSUPPORTED_CHANNEL

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    before = await _records(scene.session)
    assert RpcChannel.resumes_agents is False and PrintChannel.resumes_agents is True

    class _Unsaid:
        async def run(self, plan: SpawnPlan, **_kwargs: Any) -> Any:
            raise AssertionError("a resume reached a channel that cannot launch one")

    for channel in (RpcChannel(), _Unsaid()):
        scene.runtime.channel = channel
        assert scene.runtime.resume_refusal(target) == RESUME_UNSUPPORTED_CHANNEL
        result = await scene.runtime.resume_granted(
            _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
        )
        await _nothing_spent(scene, result, RESUME_UNSUPPORTED_CHANNEL, before)


async def test_a_first_key_not_shaped_like_a_spawn_key_is_refused_before_any_path(
    tmp_path: Path,
) -> None:
    """F14 (verify m1, the ``if False:`` mutant). The first run's key names its
    file, so a key of another shape — ``..`` in it — is refused before it is
    joined onto a path, even with a well-formed child file waiting where the
    traversal would land."""

    scene = await _delegated(tmp_path)
    evil = "../evil"
    landing = scene.child_path.parent.parent / "evil.jsonl"
    lines = scene.child_path.read_text(encoding="utf-8").splitlines()
    origin = json.loads(lines[1])
    origin["data"]["key"] = evil
    landing.write_text("\n".join([lines[0], json.dumps(origin), *lines[2:]]) + "\n", "utf-8")

    def _rekey(data: dict[str, Any]) -> dict[str, Any]:
        return {**data, "key": evil, "child": {**data["child"], "rel": f"p/{evil}.jsonl"}}

    await _rewrite_first_run(scene, _rekey)

    assert await scene.runtime.find_resume_target(scene.child_id) == (
        "Resume refused: that agent's session record is damaged (its record's key is "
        "not a delegation's). Delegate the task anew."
    )


async def test_a_resumes_registry_row_names_its_child_from_the_insert(tmp_path: Path) -> None:
    """F14 (verify m2, the ``session_id=None`` mutant). The row a resume inserts
    names the child session at once — before the receipt attaches the file —
    so a second resume of it is refused from the first progress frame on."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    seen: list[str | None] = []

    def _first_frame(_progress: Any) -> None:
        if not seen:
            seen.append(scene.runtime.resume_refusal(target))

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", on_event=_first_frame, record=_meta("tc-2")
    )

    assert result.ok is True
    assert seen == [RESUME_RUNNING]


async def test_a_relative_profile_path_is_recorded_absolute_and_still_resumes(
    tmp_path: Path,
) -> None:
    """F15. A relative ``AELIX_CODING_AGENT_DIR`` gives a relative
    ``source_path``. The record holds it absolute — the reader refuses a
    relative one — and the resume compares the same spelling, so the agent is
    still its profile's."""

    relative = _profile(file_path=os.path.join("agents", "scout.md"))
    scene = await _delegated(tmp_path, profile=relative)
    target = await _target(scene)
    assert target.limits.profile_source == os.path.abspath(os.path.join("agents", "scout.md"))

    result = await _resume(scene, resolved=_resolved(relative))

    assert result.ok is True, result.summary


async def test_what_the_first_run_was_is_read_off_its_start(tmp_path: Path) -> None:
    """F2: a later line of the first run's key that agrees about the limits but
    names another profile, task and file is not news — the profile the call
    must name, the earlier task the dialog shows and the file are the
    START's."""

    scene = await _delegated(tmp_path)
    first = await _first_run_record(scene)
    await _forge(
        scene,
        profile="someone-else",
        task_preview="a task the model never wrote",
        child={**first["child"], "rel": f"elsewhere/{scene.first_key}.jsonl"},
    )

    target = await _target(scene)

    assert (target.profile, target.task_preview) == ("scout", "look at the thing")
    assert target.rel == f"p/{scene.first_key}.jsonl"


async def test_a_first_run_whose_start_is_not_on_the_branch_is_refused(tmp_path: Path) -> None:
    """F2: the limits are read off the first run's start, so a first run this
    branch holds only the settle of is a delegation whose record is not here."""

    scene = await _delegated(tmp_path)

    def _no_start(data: dict[str, Any]) -> dict[str, Any]:
        return {**data, "phase": "begun"} if data.get("phase") == "start" else data

    await _rewrite_first_run(scene, _no_start)

    assert await scene.runtime.find_resume_target(scene.child_id) == RESUME_FIRST_RUN_MISSING


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on windows")
async def test_a_parent_opened_through_a_linked_directory_still_finds_its_child(
    tmp_path: Path,
) -> None:
    """F3, G1: the child's path is derived from the parent's file AS ITS
    SESSION NAMES IT, as allocation derived it, so a parent reopened through a
    symlinked sessions directory (``~/.aelix`` on another volume, say) finds its
    child under that spelling — and the "reached through a link" check, which
    looks only below the parent's directory, does not refuse it."""

    scene = await _delegated(tmp_path)
    linked_root = tmp_path / "linked-sessions"
    linked_root.symlink_to(tmp_path / "s", target_is_directory=True)
    through_link = linked_root / scene.parent_path.relative_to(tmp_path / "s")
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(through_link)))
    scene.live.session = reopened

    target = await _target(scene)

    assert target.path == str(through_link.parent / "p" / scene.child_path.name)
    assert os.path.realpath(target.path) == str(scene.child_path)


def test_a_child_path_with_a_parent_step_is_not_its_file(tmp_path: Path) -> None:
    """F3: whatever leads to a child file other than the derivation itself — a
    ``..`` (should a key's shape check ever be skipped), a Windows junction
    ``lstat`` does not call a link — is refused by the ``realpath`` comparison,
    before the file is read."""

    from aelix_agents import child_session

    (tmp_path / "bucket" / "p").mkdir(parents=True)
    landing = tmp_path / "bucket" / "evil.jsonl"
    landing.write_text('{"type": "session"}\n', encoding="utf-8")
    sideways = os.path.join(str(tmp_path / "bucket" / "p"), "..", "evil.jsonl")

    refusal = child_session._check_child_file(
        sideways, str(tmp_path / "bucket" / "p"), child_id="x", first_key="k", parent_id="p"
    )

    assert refusal == (
        "Resume refused: that agent's session record is damaged (its session file is "
        "reached through a link). Delegate the task anew."
    )


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on windows")
def test_a_link_lstat_does_not_report_in_the_childrens_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G1: the ``realpath`` comparison is built from the PARENT's directory and
    the two names below it, so a link in the children's directory that
    ``lstat`` does not report — a Windows junction, which ``lstat`` does not
    mark ``S_IFLNK`` — is refused too. Simulated with a symlink and an
    ``lstat`` that follows it, for this module only."""

    from aelix_agents import child_session

    bucket = tmp_path / "--bucket--"
    (bucket / "elsewhere").mkdir(parents=True)
    name = "sub-0123456789ab.jsonl"
    (bucket / "elsewhere" / name).write_text('{"type": "session"}\n', encoding="utf-8")
    (bucket / "p").symlink_to(bucket / "elsewhere", target_is_directory=True)

    class _JunctionBlind:
        """``os`` as ``child_session`` sees it, with an ``lstat`` that follows."""

        def __getattr__(self, attribute: str) -> Any:
            return getattr(os, attribute)

        @staticmethod
        def lstat(path: Any) -> os.stat_result:
            return os.stat(path)

    monkeypatch.setattr(child_session, "os", _JunctionBlind())

    refusal = child_session._check_child_file(
        str(bucket / "p" / name),
        str(bucket / "p"),
        child_id="x",
        first_key="sub-0123456789ab",
        parent_id="p",
    )

    assert refusal == (
        "Resume refused: that agent's session record is damaged (its session file is "
        "reached through a link). Delegate the task anew."
    )


# === Review round 2 (#610 G2) ===================================================

_OTHER_CHILD = "0b8a2a8e-0000-4000-8000-0000000000ff"

_LATER_LINE = {
    "another-child-session": lambda first: {"child": {**first["child"], "session_id": _OTHER_CHILD}},
    "a-resume-of-another-run": lambda first: {"resumes": "sub-0123456789ab"},
    "another-profile": lambda first: {"profile": "someone-else"},
    "another-task": lambda first: {"task_preview": "a task the model never wrote"},
}
"""What a later line of the first run's key says instead of its start (G2)."""


@pytest.mark.parametrize("later", sorted(_LATER_LINE))
async def test_a_later_line_moves_neither_the_target_nor_the_listing(
    tmp_path: Path, later: str
) -> None:
    """G2 (verify r2 m2). Which child session a run ran in, whether it is a
    first run, and the profile and task the listing shows are its START's
    (F2), like its limits. A later line of the first run's key that says
    otherwise — and agrees about the limits, so it is not damage — moves
    neither the target the lookup finds nor the listing a refusal carries."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    listing = await scene.runtime.resumable_agents()
    assert listing == (
        f'{RESUMABLE_HEAD}\n- {scene.child_id} (scout, finished): "look at the thing"'
    )

    await _forge(scene, **_LATER_LINE[later](await _first_run_record(scene)))

    assert await _target(scene) == target
    assert await scene.runtime.resumable_agents() == listing


@pytest.mark.parametrize(
    ("field", "value"),
    [("profile", "someone-else"), ("task_preview", "a task the dialog never showed")],
)
async def test_a_record_that_changed_what_the_dialog_showed_is_refused_at_the_launch(
    tmp_path: Path, field: str, value: str
) -> None:
    """G2 (verify r2 m2), F3. After the target was found, every line of the
    first run's record — its start included — is rewritten to name another
    profile or another earlier task: the same file and the same limits, but not
    what the human was shown. The launch's lookup finds a target that is not
    the approved one and refuses; nothing is spent."""

    scene = await _delegated(tmp_path)
    target = await _target(scene)
    await _rewrite_first_run(scene, lambda data: {**data, field: value})
    before = await _records(scene.session)

    result = await scene.runtime.resume_granted(
        _grant(), _resolved(), target, "keep going", record=_meta("tc-2")
    )

    await _nothing_spent(
        scene,
        result,
        "Resume refused: that agent's session record is damaged (its record changed "
        "before the resume could start). Delegate the task anew.",
        before,
    )
