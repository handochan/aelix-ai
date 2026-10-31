"""#627 — a lone surrogate or a NUL in what the model sends ``agent`` is refused
before anything is spent, and a child's in a chain's ``{previous}`` is replaced.

Both are legal in a ``str`` and in a tool call's JSON (``"\\ud83d"``, half an
emoji; ``"\\u0000"``), and neither can be handed to a child. Before #627 the run
path took them like this (measured on ``e19ab55e`` through the real hook and
``execute()``; recorded in ``.omc/specs/627-progress-2026-10-31.md``, the
probes kept locally in ``.omc/specs/634-627-live/sweep/``, not in the repo):

* a lone surrogate in ``task`` or any ``tasks[i]`` raised ``UnicodeEncodeError``
  out of the ``tool_call`` hook (``chain.check_task_size`` encodes strictly), and
  the kernel ended the whole turn on it;
* a NUL passed every check, was approved and admitted, charged the budget,
  wrote four records and a child file, and failed in ``create_subprocess_exec``
  with ``embedded null byte`` — the only thing the model was told;
* a step's ``{previous}`` carrying either (a CHILD wrote it) did the same one
  step later: a lone surrogate raised out of ``execute()`` and lost step 1's
  result, a NUL spent step 2's budget and records for nothing;
* ``/agents run`` and an RPC client's ``agents/run`` (``runtime.spawn``) wrote
  the records and the file, or opened the dialog first, and then failed;
* a profile file whose ``model:`` held either one parsed, and the delegation
  failed at launch after the budget, the records and the child's file.

The model's strings are refused by the parser with a sentence that names the
argument and what to send instead (the resume refusals #610 shipped, spelled the
same); the child's ``{previous}`` is made sendable (U+FFFD), because no one in
the loop can send it again; a profile holding either does not parse; the
runtime's own doors refuse a task, a resume's instruction, a ``cwd`` and a
profile an extension built, as a belt.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.chain import PREVIOUS_FENCE_CLOSE, PREVIOUS_FENCE_OPEN, render_step
from aelix_agents.print_channel import RunningChild, SpawnPlan, resolve_child_cwd, resume_cwd
from aelix_agents.runtime import MAX_DELEGATIONS_PER_PROMPT
from aelix_agents.tool import AgentCallError, parse_agent_call
from aelix_coding_agent.agents.discovery import discover_profiles
from aelix_coding_agent.agents.profile import parse_profile, unlaunchable_profile
from aelix_coding_agent.agents.sendable import sendable, unsendable_refusal
from aelix_coding_agent.subagent_contract import SubagentResult

from tests.agents_ext.test_child_session_records import (
    _Channel,
    _grant,
    _parent,
    _profile,
    _records,
    _resolved,
    _runtime,
)
from tests.agents_ext.test_tool_and_security import (
    _Bench,
    _bench,
    _call,
    _RecordingChannel,
    _text,
    _write_profile,
)

HALF = "\ud83d"
"""Half of U+1F642, as ``json.loads('"\\ud83d"')`` returns it."""

POSIX = sys.platform != "win32"
"""Where ``os.fsencode`` refuses ``HALF``. On Windows it uses surrogatepass and
takes every lone surrogate, so a string the OS is handed as it stands — a
``cwd``, an argv element — is not refused for one there (the text rule still
refuses it in what the model sends)."""


def _odd_dir(tail: str = "") -> str:
    """A directory name the OS takes back but the text rule would refuse: on
    POSIX one that is not UTF-8 (it decodes to U+DCE9, surrogateescape); on
    Windows one holding an unpaired surrogate, which NTFS allows."""

    if POSIX:
        return os.fsdecode(b"/home/u/caf\xe9/proj" + tail.encode())
    return "C:\\Users\\u\\caf\ud83d\\proj" + tail.replace("/", "\\")


BAD = {"nul": "\x00", "lone surrogate": HALF}

_HOLDS = {
    "nul": "holds a NUL character (U+0000), which cannot be sent to the agent. Resend {it} without it.",
    "lone surrogate": (
        "holds half of a character (a lone surrogate), which cannot be sent to the agent. "
        "Resend {it} as plain text."
    ),
}

NORMAL = [
    "검토해 주세요 — 中文も日本語も",
    "🙂 👍🏽 👨‍👩‍👧 🇰🇷",
    "e\u0301 a\u0308 \u0915\u094d\u0937",
    "مرحبا بالعالم",
    json.loads('"\\ud83d\\ude42 is one character"'),
]
"""CJK, emoji (a skin tone, a ZWJ family, a flag), combining marks, a
right-to-left script — and a surrogate PAIR as JSON spells it, which decodes to
one character and is not a lone surrogate."""


def _refusal(kind: str, where: str, it: str) -> str:
    return f"{where} " + _HOLDS[kind].format(it=it)


def _shapes(bad: str) -> dict[str, tuple[dict[str, Any], str, str]]:
    """Every string ``agent`` takes, carrying ``bad``: (args, where, what to resend)."""

    return {
        "task": ({"profile": "w", "task": f"keep{bad}going"}, "'task'", "the task"),
        "parallel tasks[2]": (
            {"profile": "w", "mode": "parallel", "tasks": ["one", f"two{bad}"]},
            "tasks[2]",
            "tasks[2]",
        ),
        "chain tasks[2]": (
            {"profile": "w", "mode": "chain", "tasks": ["one", f"use {{previous}}{bad}"]},
            "tasks[2]",
            "tasks[2]",
        ),
        "chain tasks[1]": (
            {"profile": "w", "mode": "chain", "tasks": [f"one{bad}", "two"]},
            "tasks[1]",
            "tasks[1]",
        ),
        "profile": ({"profile": f"w{bad}", "task": "go"}, "'profile'", "the profile name"),
        "cwd": ({"profile": "w", "task": "go", "cwd": f"sub{bad}"}, "'cwd'", "the directory"),
        "resume id": (
            {"action": "resume", "profile": "w", "id": f"x{bad}", "task": "go on"},
            "'id'",
            "the id",
        ),
        "resume profile": (
            {"action": "resume", "profile": f"w{bad}", "id": "x", "task": "go on"},
            "'profile'",
            "the profile name",
        ),
        "resume task": (
            {"action": "resume", "profile": "w", "id": "x", "task": f"go{bad}on"},
            "'task'",
            "the instruction",
        ),
    }


# === The parser: every string the model sends ================================


@pytest.mark.parametrize("kind", sorted(BAD))
@pytest.mark.parametrize("shape", sorted(_shapes("")))
def test_every_string_the_model_sends_is_refused_by_the_parser(kind: str, shape: str) -> None:
    """Refused with a sentence that names the argument, the reason and what to
    send instead — the resume's refusals, which #610 shipped, unchanged."""

    args, where, it = _shapes(BAD[kind])[shape]
    with pytest.raises(AgentCallError) as caught:
        parse_agent_call(args)
    assert str(caught.value) == _refusal(kind, where, it)
    # The refusal never echoes the character it refuses.
    assert BAD[kind] not in str(caught.value)


def test_a_text_holding_both_is_named_for_its_lone_surrogate() -> None:
    """One sentence, not two: the order the resume parse always had."""

    with pytest.raises(AgentCallError, match="lone surrogate"):
        parse_agent_call({"profile": "w", "task": f"a\x00b{HALF}"})


@pytest.mark.parametrize("kind", sorted(BAD))
@pytest.mark.parametrize("key", ["mode", "action"])
def test_mode_and_action_are_refused_by_their_own_value_check(kind: str, key: str) -> None:
    """Already refused before #627, and left so: the value check names the
    values that are allowed, and quotes the bad one escaped."""

    with pytest.raises(AgentCallError, match=r"is not an? (mode|action)\. Use one of") as caught:
        parse_agent_call({"profile": "w", "task": "go", key: f"single{BAD[kind]}"})
    assert BAD[kind] not in str(caught.value)


@pytest.mark.parametrize("text", NORMAL)
def test_normal_unicode_still_passes_everywhere(text: str) -> None:
    assert parse_agent_call({"profile": "w", "task": text}).tasks == (text,)
    assert parse_agent_call({"profile": "w", "mode": "parallel", "tasks": [text, text]}).tasks == (
        text,
        text,
    )
    assert parse_agent_call({"profile": text, "task": "go"}).profile == text
    assert parse_agent_call({"profile": "w", "task": "go", "cwd": text}).cwd == text
    resume = parse_agent_call({"action": "resume", "profile": "w", "id": text, "task": text})
    assert (resume.id, resume.tasks) == (text, (text,))


def test_half_of_a_json_pair_is_refused_and_the_pair_is_not() -> None:
    pair = json.loads('{"profile": "w", "task": "\\ud83d\\ude42"}')
    assert parse_agent_call(pair).tasks == ("🙂",)
    half = json.loads('{"profile": "w", "task": "\\ud83d"}')
    with pytest.raises(AgentCallError, match="lone surrogate"):
        parse_agent_call(half)


# === The hook: refused before the dialog, the budget, the records, the file ===


async def _bench_with_session(
    tmp_path: Path, *, ask: bool = True, channel: _RecordingChannel | None = None
) -> tuple[_Bench, Any]:
    """A bench over a real parent session. With ``ask``, a valid call WOULD
    open the dialog (``approval_mode: ask`` with a UI) — and be declined, as
    no answer is queued."""

    _write_profile(
        tmp_path / "agent" / "agents" / "w.md", "w", extra="approval_mode: ask" if ask else ""
    )
    bench = _bench(tmp_path, has_ui=True, channel=channel)
    (bench.cwd / "sub").mkdir()
    session, _repo, _path = await _parent(tmp_path, cwd=bench.cwd)
    bench.ext.session = lambda: session
    return bench, session


def _child_files(tmp_path: Path) -> list[Path]:
    return [path for path in (tmp_path / "s").rglob("*.jsonl") if path.name != "p.jsonl"]


@pytest.mark.parametrize("kind", sorted(BAD))
@pytest.mark.parametrize("shape", ["task", "parallel tasks[2]", "chain tasks[2]", "cwd", "profile"])
async def test_the_hook_refuses_before_anything_is_spent(
    tmp_path: Path, kind: str, shape: str
) -> None:
    """An ordinary error result — so the kernel goes on with the turn — and no
    dialog, no budget, no record, no child file, no process."""

    bench, session = await _bench_with_session(tmp_path)
    args, where, it = _shapes(BAD[kind])[shape]

    result = await _call(bench, args)

    assert result.is_error is True
    assert _text(result) == _refusal(kind, where, it)
    assert bench.ui.calls == [], "no dialog"
    assert bench.ext.runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert await _records(session) == []
    assert _child_files(tmp_path) == []
    assert bench.channel.plans == []
    assert bench.ext._pending == {}


async def test_the_same_call_with_normal_unicode_reaches_the_dialog(tmp_path: Path) -> None:
    """The control for the test above: its bench would have asked."""

    bench, _session = await _bench_with_session(tmp_path)
    await _call(bench, {"profile": "w", "task": NORMAL[1]})
    assert len(bench.ui.calls) == 1


# === A chain's {previous}: a child wrote it, so it is made sendable ===========


class _Summaries(_RecordingChannel):
    """Step 1 returns ``first`` as its summary and trail; every later step ok."""

    def __init__(self, first: str) -> None:
        super().__init__()
        self._first = first

    async def run(
        self, plan: SpawnPlan, *, child: RunningChild | None = None, on_stream: Any = None
    ) -> SubagentResult:
        self.plans.append(plan)
        if child is not None:
            child.state = "done"
        first = len(self.plans) == 1
        return SubagentResult(
            id=plan.id,
            profile=plan.resolved.name,
            ok=True,
            status="ok",
            summary=self._first if first else "step two done",
            tool_trail=f"- read {self._first}" if first else None,
            permission_mode=plan.permission_mode.value,
        )


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_a_childs_previous_is_made_sendable_and_the_chain_runs(
    tmp_path: Path, kind: str
) -> None:
    """Refusing would stop the chain on text neither the model nor the user can
    send again; U+FFFD keeps the chain and shows where a character was."""

    bench, _session = await _bench_with_session(
        tmp_path, ask=False, channel=_Summaries(f"found{BAD[kind]}it")
    )

    result = await _call(
        bench, {"profile": "w", "mode": "chain", "tasks": ["one", "use {previous}"]}
    )

    assert result.is_error is False, _text(result)
    step_two = bench.channel.plans[1].task
    assert BAD[kind] not in step_two
    assert "found\ufffdit" in step_two
    assert step_two.count("\ufffd") == 2, "the summary and the trail"
    step_two.encode("utf-8")  # an argv element can carry it
    assert bench.ext.runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT - 2


def test_render_step_makes_previous_and_trail_sendable_before_the_fence() -> None:
    forged = f"x{PREVIOUS_FENCE_CLOSE[:5]}\x00{PREVIOUS_FENCE_CLOSE[5:]}y{HALF}"
    rendered = render_step("go: {previous}", forged, trail=f"- read a\x00{HALF}")
    assert "\x00" not in rendered and HALF not in rendered
    rendered.encode("utf-8")
    # The fence still holds round a forged tag the NUL split: one of each.
    assert rendered.count(PREVIOUS_FENCE_CLOSE) == 1
    assert rendered.count(PREVIOUS_FENCE_OPEN) == 1
    # Step 1 has no previous, and the model's own task is never rewritten here.
    assert render_step("one", None) == "one"


@pytest.mark.parametrize("text", NORMAL)
def test_sendable_leaves_normal_unicode_alone(text: str) -> None:
    assert sendable(text) == text
    assert unsendable_refusal(text, where="'task'", resend="the task") is None


# === The runtime's own doors ==================================================


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_agents_run_and_rpc_are_refused_before_the_dialog_and_the_records(
    tmp_path: Path, kind: str
) -> None:
    """``runtime.spawn`` is ``/agents run``'s door and RPC ``agents/run``'s, and
    the ``SubagentRuntime`` Protocol's: refused before its own consent."""

    bench, session = await _bench_with_session(tmp_path)
    bench.ext._ctx = bench.ctx
    runtime = bench.ext.runtime
    result = await runtime.spawn(runtime.resolve_profile("w"), f"keep{BAD[kind]}going")

    assert (result.ok, result.status) == (False, "error")
    assert result.error == _refusal(kind, "The task", "the task")
    assert bench.ui.calls == []
    assert await _records(session) == []
    assert _child_files(tmp_path) == []
    assert bench.channel.plans == []


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_the_model_door_refuses_a_task_that_skipped_the_parser(
    tmp_path: Path, kind: str
) -> None:
    """The belt on ``spawn_granted``: nothing charged, recorded or launched."""

    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)

    result = await runtime.spawn_granted(_grant(), _resolved(), f"keep{BAD[kind]}going")

    assert result.error == _refusal(kind, "The task", "the task")
    assert runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert await _records(session) == []
    assert channel.plans == []


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_a_resume_that_skipped_the_parser_is_refused_before_its_target_is_read(
    tmp_path: Path, kind: str
) -> None:
    """The same belt on ``resume_granted``, before the target is looked up or
    its lock probed: the target here is not even a target."""

    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)

    result = await runtime.resume_granted(
        _grant(),
        _resolved(),
        object(),
        f"go{BAD[kind]}on",  # type: ignore[arg-type]
    )

    assert result.error == _refusal(kind, "The instruction", "the instruction")
    assert runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert await _records(session) == []
    assert channel.plans == []


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_an_extensions_cwd_gets_the_same_sentence(tmp_path: Path, kind: str) -> None:
    """``spawn(cwd=…)`` is an extension's door; it used to answer with the
    codec's or the OS's message (``resolve_child_cwd``). Held to the OS's rule:
    on Windows a lone surrogate encodes, so the directory check answers."""

    bench, session = await _bench_with_session(tmp_path)
    bench.ext._ctx = bench.ctx
    runtime = bench.ext.runtime
    result = await runtime.spawn(runtime.resolve_profile("w"), "go", cwd=f"sub{BAD[kind]}")

    if kind == "nul" or POSIX:
        assert result.error == _refusal(kind, "'cwd'", "the directory")
    else:
        assert result.error is not None and "is not a directory" in result.error
    assert bench.ui.calls == []
    assert await _records(session) == []


def test_a_directory_whose_name_the_text_rule_refuses_is_still_a_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2, F1: round 1 held ``resolve_child_cwd`` to the TEXT rule,
    and ``resume_cwd`` brings a RECORDED cwd back through it — so on Linux every
    resume under a directory whose name is not UTF-8 was refused ("no longer
    exists"), and so was an extension's spawn there. Simulated, because APFS
    refuses such a name: ``Path.resolve`` and ``is_dir`` answer for paths under
    it as the OS would (measured on Linux by the round-1 check)."""

    odd = _odd_dir()
    assert os.fsdecode(os.fsencode(odd)) == odd, "the OS takes the name back"
    real_resolve, real_is_dir = Path.resolve, Path.is_dir

    def resolve(self: Path, strict: bool = False) -> Path:
        return self if str(self).startswith(odd) else real_resolve(self, strict)

    def is_dir(self: Path, **kwargs: Any) -> bool:
        return True if str(self).startswith(odd) else real_is_dir(self, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "is_dir", is_dir)

    recorded = resolve_child_cwd("sub", odd)
    assert recorded == str(Path(odd) / "sub")
    assert resume_cwd(recorded, odd) == recorded
    assert resume_cwd(resolve_child_cwd(None, odd), odd) == str(Path(odd))
    assert resolve_child_cwd(recorded, odd) == recorded  # an extension's absolute cwd
    with pytest.raises(ValueError, match="holds a NUL character"):
        resolve_child_cwd("sub\x00", odd)


# === A profile's own text ======================================================

_YAML_ESCAPE = {"nul": "\\0", "lone surrogate": "\\ud83d"}
"""How a profile file spells either character: YAML's double-quoted escapes."""

_PROFILE_FIELDS = {
    "model": 'model: "x{bad}y"',
    "provider": 'provider: "p{bad}"',
    "description": 'description: "reads {bad} things"',
    "tools": 'tools:\n  - read\n  - "gr{bad}ep"',
    "skills": 'skills:\n  - "s{bad}"',
    "thinking": 'thinking: "hi{bad}gh"',
}


def _profile_file(field: str, kind: str) -> str:
    line = _PROFILE_FIELDS[field].format(bad=_YAML_ESCAPE[kind])
    if field == "description":
        return f"---\nname: w\n{line}\n---\n\nYou are the agent.\n"
    return f"---\nname: w\ndescription: w agent\n{line}\n---\n\nYou are the agent.\n"


@pytest.mark.parametrize("kind", sorted(BAD))
@pytest.mark.parametrize("field", sorted(_PROFILE_FIELDS))
def test_a_profile_field_holding_either_does_not_parse(field: str, kind: str) -> None:
    """Every known key, read as written. Before #627 a ``model:`` holding one
    parsed, and the delegation failed at launch after the budget, four
    records and the child's file."""

    result = parse_profile(_profile_file(field, kind), file_path="/u/agents/w.md", scope="user")

    assert result.profile is None
    holds = "a NUL character (U+0000)" if kind == "nul" else "half of a character"
    assert any(
        d.type == "error" and d.message.startswith(f"{field} holds {holds}")
        for d in result.diagnostics
    ), result.diagnostics


@pytest.mark.parametrize("kind", sorted(BAD))
def test_a_profile_body_holding_either_does_not_parse(kind: str) -> None:
    content = f"---\nname: w\ndescription: w agent\n---\n\nYou are{BAD[kind]} the agent.\n"
    result = parse_profile(content, file_path="/u/agents/w.md", scope="user")

    assert result.profile is None
    assert any(d.message.startswith("body holds ") for d in result.diagnostics)


def test_an_unknown_key_is_still_only_a_warning() -> None:
    """Ignored keys are not passed on, so their values are not judged."""

    content = '---\nname: w\ndescription: w agent\nlater: "x\\0y"\n---\n\nBody.\n'
    result = parse_profile(content, file_path="/u/agents/w.md", scope="user")

    assert result.profile is not None
    assert [d.code for d in result.diagnostics] == ["unknown_field"]


@pytest.mark.parametrize("text", NORMAL)
def test_a_profile_in_normal_unicode_still_parses(text: str) -> None:
    content = f'---\nname: w\ndescription: "{text}"\nmodel: "{text}"\n---\n\n{text}\n'
    result = parse_profile(content, file_path="/u/agents/w.md", scope="user")

    assert result.profile is not None, result.diagnostics


@pytest.mark.parametrize("kind", sorted(BAD))
async def test_a_profile_file_holding_either_is_refused_before_anything_is_spent(
    tmp_path: Path, kind: str
) -> None:
    """The verifier's probe, as a test: ``model: "x\\0y"`` reached the launch.

    A profile that does not parse is not found by name — discovery's rule for
    every broken profile — so the model reads that; the reason is in the
    discovery diagnostics ``/agents list`` shows."""

    bench, session = await _bench_with_session(tmp_path, ask=False)
    (tmp_path / "agent" / "agents" / "w.md").write_text(_profile_file("model", kind))

    result = await _call(bench, {"profile": "w", "task": "go"})

    assert result.is_error is True
    assert _text(result).startswith("Delegation refused: unknown agent profile 'w'")
    listed = discover_profiles(bench.cwd, project_trusted=False, agent_dir=str(tmp_path / "agent"))
    assert any(d.message.startswith("model holds") for d in listed.diagnostics)
    assert bench.ext.runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert await _records(session) == []
    assert _child_files(tmp_path) == []
    assert bench.channel.plans == []


_BUILT = [
    ("model", "nul"),
    ("provider", "nul"),
    ("thinking", "nul"),
    ("tools", "nul"),
    ("skills", "nul"),
    ("extensions", "nul"),
    ("body", "lone surrogate"),
    *([("model", "lone surrogate"), ("tools", "lone surrogate")] if POSIX else []),
]
"""What breaks a launch: a NUL on the command line, a lone surrogate there that
``os.fsencode`` refuses (POSIX), and a lone surrogate in the body, which the
prompt file is written without (UTF-8)."""


def _built_value(field: str, kind: str) -> Any:
    if field in ("tools", "skills", "extensions"):
        return (f"read{BAD[kind]}",)
    return f"x{BAD[kind]}y"


@pytest.mark.parametrize(("field", "kind"), _BUILT)
async def test_a_profile_an_extension_built_is_refused_at_both_doors(
    tmp_path: Path, kind: str, field: str
) -> None:
    """Never parsed, so held at the runtime: before ``spawn``'s dialog and
    ``spawn_granted``'s budget — and so before the prompt file is written."""

    value = _built_value(field, kind)
    profile = _profile(**{field: value})
    resolved = _resolved(profile)

    bench, session = await _bench_with_session(tmp_path)
    bench.ext._ctx = bench.ctx
    spawned = await bench.ext.runtime.spawn(resolved, "go")
    assert spawned.status == "error"
    assert bench.ui.calls == []
    assert await _records(session) == []

    granted_session, _repo, _path = await _parent(tmp_path / "granted")
    channel = _Channel()
    runtime = _runtime(tmp_path / "granted", channel, session=granted_session)
    granted = await runtime.spawn_granted(_grant(), resolved, "go")
    assert granted.status == "error"
    assert runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert await _records(granted_session) == []
    assert channel.plans == []
    for result in (spawned, granted):
        assert BAD[kind] not in (result.error or "")
        assert "Fix the profile." in (result.error or "")


@pytest.mark.parametrize(
    ("field", "kind"),
    [("name", "nul"), ("name", "lone surrogate"), ("description", "nul"), ("body", "nul")],
)
async def test_what_does_not_break_a_launch_is_not_refused(
    tmp_path: Path, field: str, kind: str
) -> None:
    """Review round 2, F2: only what breaks the launch is refused. The name
    reaches no command line (the prompt file's name is made safe from it, the
    records escape it), the description none at all, and a NUL in the body is
    written to the prompt file as it is."""

    profile = _profile(**{field: f"x{BAD[kind]}y"})
    assert unlaunchable_profile(profile) is None
    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)

    result = await runtime.spawn_granted(_grant(), _resolved(profile), "go")

    assert result.ok is True, result.error
    assert len(channel.plans) == 1


def test_a_path_is_held_to_what_the_launch_does_with_it() -> None:
    """A directory whose name the text rule refuses but the OS takes back
    (:func:`_odd_dir`) is a path the child can open; a NUL never is."""

    assert unlaunchable_profile(_profile(skills=(_odd_dir("/skill"),))) is None
    refusal = unlaunchable_profile(_profile(skills=("/tmp/a\x00b",)))
    assert refusal is not None and "no command line can carry" in refusal
    assert (unlaunchable_profile(_profile(model=f"m{HALF}")) is not None) is POSIX


def test_a_surrogate_pair_a_profile_file_spells_is_one_character() -> None:
    """Review round 2, F3: PyYAML leaves ``"\\ud83d\\ude42"`` — what
    ``json.dumps("smile 🙂")`` writes — as two halves. Joined after the load, so
    the profile parses with the one character; a reversed pair and a lone half
    are still refused."""

    def parse(escaped: str) -> Any:
        content = (
            f'---\nname: w\ndescription: "smile {escaped}"\ntools: ["{escaped}"]\n---\n\nBody.\n'
        )
        return parse_profile(content, file_path="/u/agents/w.md", scope="user")

    joined = parse("\\ud83d\\ude42")
    assert joined.profile is not None, joined.diagnostics
    assert joined.profile.description == "smile \U0001f642"
    assert joined.profile.tools == ("\U0001f642",)
    for escaped in ("\\ude42\\ud83d", "\\ud83d", "\\ude42"):
        refused = parse(escaped)
        assert refused.profile is None
        assert any(d.message.startswith("description holds half") for d in refused.diagnostics)
