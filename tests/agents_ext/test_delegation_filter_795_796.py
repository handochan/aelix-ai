"""#795, #796 (ADR-0304 D5-D8, D17) — every door aelix opens to the model, filtered.

An off profile, one the identity's ``agents:`` leaves out, and every profile
under ``agents: []`` are refused at the model's door — a run, a batch, a
chain, a background spawn, a resume, and the pre-spawn re-check — before a
dialog, a budget charge, a slot, a record or a process, with ADR-0304 D7's
exact texts. The roster leaves them out. The human doors (``/agents run``, RPC
``agents/run``, ``/agents use``, ``--agent``) and an extension's
``resolve_profile`` + ``spawn`` are not filtered (owner); an extension's
model-facing tool that uses ``resolve_for_model`` is.

AT WHAT LEVEL: ``test_tool_and_security``'s bench — the REAL extension hook and
``execute()`` over a real ``_ExtensionRuntime``/``ExtensionContext``, the
process boundary replaced by a recording channel — with the extension's two
new getters (``agent_lists``, ``active_profile``) set the way ``cli/entry.py``
sets them.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent, ToolCallHookEvent
from aelix_agent_core.types import AgentTool
from aelix_agents import runtime as runtime_module
from aelix_agents.child_session import (
    RESUMABLE_HEAD,
    RESUME_NEEDS_ID,
    RESUME_PROFILE_CHANGED,
    RESUME_RUNNING,
    RESUME_UNKNOWN_ID,
)
from aelix_agents.extension import _IDENTITY_MISMATCH
from aelix_agents.print_channel import AGENT_TOOL_NAME
from aelix_agents.runtime import MAX_DELEGATIONS_PER_PROMPT, DelegationRefused
from aelix_agents.tool import build_description, build_roster
from aelix_ai.messages import TextContent
from aelix_ai.settings import SettingsManager
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.agents import discovery
from aelix_coding_agent.agents.discovery import ProfileError, discover_profiles
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.core.agent_selection import AgentDirs, AgentKind
from aelix_coding_agent.core.resource_filter import ScopeList
from aelix_coding_agent.core.resource_selection import ResourceSelection, SettingsLists
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI
from aelix_coding_agent.subagent_contract import (
    DelegationNotAllowed,
    ModelDelegation,
    SubagentRuntime,
)

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is a fixture
    call,
    finish,
    parked_call,
    reap_children,
    row_of,
    settled,
    tui_rig,
    within,
)
from tests.agents_ext.test_agent_background_611 import (  # noqa: F401 — jobs_on is a fixture
    _rig,
    _spawned_id,
    jobs_on,
)
from tests.agents_ext.test_agent_resume_tool_610 import _delegated, _resume, _Scene
from tests.agents_ext.test_child_session_records import _records
from tests.agents_ext.test_detach_611 import MOVED, _agent_id, _resumable
from tests.agents_ext.test_tool_and_security import (
    _Bench,
    _bench,
    _call,
    _RecordingChannel,
    _text,
    _write_profile,
)
from tests.jobs.conftest import wait_for

_BUNDLED = ("explorer", "general-purpose")


def _lists(bench: Any, *global_entries: str, project: tuple[str, ...] = ()) -> SettingsLists:
    lists = SettingsLists(
        ScopeList("global", tuple(global_entries), str(bench.agent_dir)),
        ScopeList("project", tuple(project), str(bench.cwd / ".aelix")),
    )
    bench.ext.agent_lists = lambda: lists
    return lists


def _identity(bench: Any, name: str, agents: tuple[str, ...] | None) -> AgentProfile:
    profile = AgentProfile(
        name=name,
        description=f"{name} agent",
        body="b",
        file_path=f"/agents/{name}.md",
        scope="user",
        agents=agents,
    )
    bench.ext.active_profile = lambda: profile
    return profile


def _scout(tmp_path: Path, *, trusted: bool = False, has_ui: bool = False) -> _Bench:
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    _write_profile(tmp_path / "agent" / "agents" / "reviewer.md", "reviewer")
    return _bench(tmp_path, project_trusted=trusted, has_ui=has_ui)


_SHAPES: dict[str, dict[str, Any]] = {
    "single": {"task": "go"},
    "parallel": {"mode": "parallel", "tasks": ["a", "b", "c"]},
    "chain": {"mode": "chain", "tasks": ["a", "b"]},
}

_RUN_TEXTS = {
    "off": (
        "Delegation refused: agent profile 'scout' is turned off in the settings, and only the "
        "user can turn it on (with /agents); available: explorer, general-purpose, reviewer"
    ),
    "not_listed": (
        "Delegation refused: agent profile 'scout' is not one this session's profile "
        "'orchestrator' may delegate to; available: reviewer"
    ),
    "none_listed": (
        "Delegation refused: this session runs as agent profile 'orchestrator', whose agents: "
        "list is empty, so it delegates to no profile"
    ),
    "unknown": (
        "Delegation refused: unknown agent profile 'nope'; available: explorer, "
        "general-purpose, reviewer"
    ),
}


def _arrange(bench: _Bench, verdict: str) -> str:
    """Set the bench up so the call's profile meets *verdict*; returns the profile it calls."""

    if verdict == "off":
        _lists(bench, "-scout")
    elif verdict == "not_listed":
        _identity(bench, "orchestrator", ("reviewer",))
    elif verdict == "none_listed":
        _identity(bench, "orchestrator", ())
    elif verdict == "unknown":
        _lists(bench, "-scout")
        return "nope"
    return "scout"


@pytest.mark.parametrize("shape", sorted(_SHAPES))
@pytest.mark.parametrize("verdict", sorted(_RUN_TEXTS))
async def test_every_run_shape_is_refused_at_the_hook_with_the_texts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, verdict: str
) -> None:
    bench = _scout(tmp_path, has_ui=True)
    profile = _arrange(bench, verdict)
    asked: list[object] = []

    async def _no_dialog(*args: object, **kwargs: object) -> object:
        asked.append(args)
        raise AssertionError("no consent dialog for a refused call")

    monkeypatch.setattr("aelix_agents.extension.request_spawn_consent_batch", _no_dialog)
    runtime = bench.ext.runtime
    assert runtime is not None
    before = runtime.remaining_delegation_budget()
    result = await _call(bench, {"profile": profile, **_SHAPES[shape]})
    assert result.is_error is True
    assert _text(result) == _RUN_TEXTS[verdict]
    assert asked == []
    assert runtime.remaining_delegation_budget() == before == MAX_DELEGATIONS_PER_PROMPT
    assert bench.channel.plans == []
    assert bench.ext._pending == {}
    assert runtime._children == {}


async def test_the_same_shapes_with_the_profile_on_and_listed_run(tmp_path: Path) -> None:
    for shape, args in _SHAPES.items():
        bench = _scout(tmp_path / shape)
        _lists(bench, "-reviewer")
        _identity(bench, "orchestrator", ("scout",))
        result = await _call(bench, {"profile": "scout", **args})
        assert result.is_error is False, (shape, _text(result))
        assert len(bench.channel.plans) == len(args.get("tasks", ["x"]))


async def test_a_project_profile_meets_finding_b5_as_before(tmp_path: Path) -> None:
    bench = _scout(tmp_path, trusted=True)
    _write_profile(bench.cwd / ".aelix" / "agents" / "local.md", "local")
    _lists(bench, "-local")  # off AND project: the project refusal comes first
    result = await _call(bench, {"profile": "local", "task": "go"})
    assert result.is_error is True
    assert "is project-local and needs a per-identity confirmation" in _text(result)
    assert "/agents run local" in _text(result)


_SPAWN_TEXTS = {
    "off": (
        "Delegation refused: agent profile 'scout' is turned off in the settings, and only the "
        "user can turn it on (with /agents); available: explorer, general-purpose"
    ),
    "not_listed": (
        "Delegation refused: agent profile 'scout' is not one this session's profile "
        "'orchestrator' may delegate to; available: explorer"
    ),
    "none_listed": _RUN_TEXTS["none_listed"],
    "unknown": (
        "Delegation refused: unknown agent profile 'nope'; available: general-purpose, scout"
    ),
}


@pytest.mark.usefixtures("jobs_on")
@pytest.mark.parametrize("verdict", sorted(_SPAWN_TEXTS))
async def test_a_background_spawn_is_refused_at_the_hook(tmp_path: Path, verdict: str) -> None:
    """Every verdict, not only ``off`` (verifier F5, probe P3): no job, no record."""

    rig = await _rig(tmp_path)
    profile = "scout"
    if verdict == "off":
        _rig_lists(rig, "-scout")
    elif verdict in ("not_listed", "none_listed"):
        _identity(rig, "orchestrator", ("explorer",) if verdict == "not_listed" else ())
    else:
        _rig_lists(rig, "-explorer")
        profile = "nope"
    result = await rig.call({"action": "spawn", "profile": profile, "task": "t"})
    assert result.is_error is True
    assert _text(result) == _SPAWN_TEXTS[verdict]
    assert rig.jobs() == []
    assert rig.ext._pending == {}
    # ``_register_kind``'s ``dataclasses.replace`` kept the live getters.
    assert rig.ext.runtime is not None
    host = rig.ext.runtime.host
    if rig.ext.agent_lists is not None:
        assert host.agent_lists() is rig.ext.agent_lists()
    if rig.ext.active_profile is not None:
        assert host.active_profile() is rig.ext.active_profile()


# === The roster (ADR-0304 D7) =============================================


async def _stamped(bench: _Bench, source: str = "interactive") -> str:
    handler = bench.extension.handlers["before_agent_start"][0]
    await handler(BeforeAgentStartHookEvent(source=source), bench.ctx)  # type: ignore[arg-type]
    return bench.tool.description


async def test_the_roster_leaves_out_off_and_unlisted_profiles(tmp_path: Path) -> None:
    bench = _scout(tmp_path, trusted=True)
    _write_profile(bench.cwd / ".aelix" / "agents" / "local.md", "local")
    _lists(bench, "-scout")
    _identity(bench, "orchestrator", ("scout", "local", "explorer"))
    description = await _stamped(bench)
    roster = description.split("Available profiles:\n", 1)[1]
    names = [line.split(" ", 2)[1] for line in roster.splitlines()]
    assert names == ["explorer", "local"]
    assert "- local (project): " in roster


async def test_nothing_filtered_reads_byte_identical_to_today(tmp_path: Path) -> None:
    bench = _scout(tmp_path)
    _lists(bench)
    description = await _stamped(bench)
    found = discover_profiles(bench.cwd, project_trusted=False, agent_dir=str(bench.agent_dir))
    assert description == build_description(
        build_roster(sorted(found.profiles, key=lambda p: p.name))
    )


async def test_past_24_profiles_the_unknown_text_is_todays(tmp_path: Path) -> None:
    for index in range(30):
        _write_profile(tmp_path / "agent" / "agents" / f"p{index:02d}.md", f"p{index:02d}")
    bench = _bench(tmp_path)
    _lists(bench)
    result = await _call(bench, {"profile": "nope", "task": "go"})
    found = discover_profiles(bench.cwd, project_trusted=False, agent_dir=str(bench.agent_dir))
    with pytest.raises(ProfileError) as today:
        discovery.resolve_profile(
            "nope", cwd=bench.cwd, project_trusted=False, agent_dir=str(bench.agent_dir)
        )
    assert _text(result) == f"Delegation refused: {today.value}"
    assert len(found.profiles) == 32


@pytest.mark.parametrize(
    ("arrange", "sentence"),
    [
        (
            lambda b: _identity(b, "orchestrator", ()),
            "No agent profile is available: this session runs as agent profile 'orchestrator', "
            "whose agents: list is empty, so it delegates to no profile.",
        ),
        (
            lambda b: (_lists(b, "-scout"), _identity(b, "orchestrator", ("scout", "ghost"))),
            "No agent profile is available: this session runs as agent profile 'orchestrator', "
            "which may delegate only to scout, ghost, and each of those that is found is turned "
            "off. Only the user can turn one on (with /agents).",
        ),
        (
            lambda b: _identity(b, "orchestrator", ("ghost",)),
            "No agent profile is available: this session runs as agent profile 'orchestrator', "
            "which may delegate only to ghost, and none of those is found.",
        ),
        (
            lambda b: _lists(b, "!*"),
            "No agent profile is available: every agent profile found is turned off in the "
            "settings. Only the user can turn one on (with /agents).",
        ),
    ],
)
async def test_each_empty_roster_sentence(tmp_path: Path, arrange: Any, sentence: str) -> None:
    bench = _scout(tmp_path)
    arrange(bench)
    description = await _stamped(bench)
    assert description.endswith(f"\n\n{sentence}")
    assert "Available profiles:" not in description


async def test_with_nothing_discovered_the_sentence_is_todays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "bundled"
    empty.mkdir()
    monkeypatch.setattr(discovery, "builtin_agents_dir", lambda: empty)
    bench = _bench(tmp_path)
    _identity(bench, "orchestrator", ())
    assert await _stamped(bench) == build_description("")


async def test_with_nothing_discovered_the_door_says_todays_no_profiles_found_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifier V35/V51: with no profile found at all — a policy in force or not — the
    model door's ``unknown`` text is today's, naming the user agents folder."""

    empty = tmp_path / "bundled"
    empty.mkdir()
    monkeypatch.setattr(discovery, "builtin_agents_dir", lambda: empty)
    bench = _bench(tmp_path)
    with pytest.raises(ProfileError) as today:
        discovery.resolve_profile(
            "x", cwd=bench.cwd, project_trusted=False, agent_dir=str(bench.agent_dir)
        )
    for arrange in (lambda: None, lambda: _lists(bench, "-reviewer")):
        arrange()
        result = await _call(bench, {"profile": "x", "task": "go"})
        assert _text(result) == f"Delegation refused: {today.value}"
        assert _text(result) == (
            f"Delegation refused: unknown agent profile 'x'; no profiles found in "
            f"{bench.agent_dir / 'agents'}"
        )


async def test_a_path_shaped_name_is_refused_by_the_model_door_itself(tmp_path: Path) -> None:
    """Verifier V30: ``resolve_for_model`` refuses a path before it takes a view — the
    hook's text is the path refusal, not ``unknown``."""

    bench = _scout(tmp_path)
    _lists(bench, "-reviewer")
    result = await _call(bench, {"profile": "../agents/scout", "task": "go"})
    assert _text(result) == (
        "Delegation refused: agent profile '../agents/scout' looks like a path; delegation "
        "resolves profiles by NAME only. Use --agent-file at launch for a path you typed "
        "yourself."
    )
    runtime = bench.ext.runtime
    assert runtime is not None
    with pytest.raises(ProfileError, match="looks like a path"):
        runtime.resolve_for_model("agents/scout")


async def test_with_nothing_available_the_refusals_say_so(tmp_path: Path) -> None:
    """Verifier V52 (run) and V58 (resume): ``<available>`` with nothing in it."""

    bench = _scout(tmp_path)
    _identity(bench, "orchestrator", ("ghost",))
    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert _text(result) == (
        "Delegation refused: agent profile 'scout' is not one this session's profile "
        "'orchestrator' may delegate to; no agent profile is available to delegate to"
    )
    scene = await _delegated(tmp_path / "resume")
    _identity(scene.bench, "orchestrator", ("ghost",))
    resumed = await _resume(scene)
    assert _text(resumed) == (
        "Resume refused: the agent profile 'scout' that agent ran as is not one this "
        "session's profile 'orchestrator' may delegate to. No agent profile is available to "
        "delegate to."
    )


def test_the_listing_predicate_takes_its_view_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifier V34: ``_listed_predicate``'s docstring says the view is taken once, on
    the first call — a listing of many rows costs one discovery."""

    bench = _scout(tmp_path)
    _lists(bench, "-scout")
    runtime = bench.ext.runtime
    assert runtime is not None
    views: list[int] = []
    real = type(runtime).delegation

    def _counted(self: Any) -> Any:
        views.append(1)
        return real(self)

    monkeypatch.setattr(type(runtime), "delegation", _counted)
    listed = runtime._listed_predicate()
    assert listed is not None
    assert views == []
    assert [listed(name) for name in ("scout", "reviewer", "scout", "ghost")] == [
        False,
        True,
        False,
        True,
    ]
    assert views == [1]


def test_the_identity_list_is_cut_at_24_names() -> None:
    from aelix_agents.tool import empty_roster_text

    names = tuple(f"n{index}" for index in range(30))
    text = empty_roster_text("listed_none_found", "o", names)
    assert text is not None
    assert "n23, … (+6 more), and none of those is found." in text
    assert "n24" not in text


async def test_a_notice_turn_restamps_the_roster(tmp_path: Path) -> None:
    bench = _scout(tmp_path)
    _lists(bench)
    first = await _stamped(bench)
    assert "- scout (user)" in first
    _lists(bench, "-scout")
    assert "- scout (user)" not in await _stamped(bench, "notice")


# === No peek, one discovery (Codex 6, Codex 3) ============================


async def test_the_model_door_does_not_peek_at_an_untrusted_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _scout(tmp_path, trusted=False)
    _write_profile(bench.cwd / ".aelix" / "agents" / "local.md", "local")
    _lists(bench)
    scanned: list[Path] = []
    real = discovery._scan_dir

    def _spy(directory: Path, *args: Any, **kwargs: Any) -> Any:
        scanned.append(Path(directory))
        return real(directory, *args, **kwargs)

    monkeypatch.setattr(discovery, "_scan_dir", _spy)
    result = await _call(bench, {"profile": "local", "task": "go"})
    assert _text(result) == (
        "Delegation refused: unknown agent profile 'local'; available: explorer, "
        "general-purpose, reviewer, scout"
    )
    assert "--approve" not in _text(result)
    assert not [d for d in scanned if ".aelix" in d.parts]


async def test_one_call_takes_one_discovery_and_returns_the_profile_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _scout(tmp_path)
    runtime = bench.ext.runtime
    assert runtime is not None
    a = discover_profiles(bench.cwd, project_trusted=False, agent_dir=str(bench.agent_dir))
    other = tmp_path / "elsewhere" / "scout.md"
    _write_profile(other, "scout")
    b = discovery.ProfileDiscoveryResult(
        [
            p
            if p.name != "scout"
            else discovery.load_profile_file(str(other), cwd=bench.cwd).profile
            for p in a.profiles
        ],  # type: ignore[misc]
        a.diagnostics,
    )
    answers = [a, b]
    calls: list[int] = []

    def _discover(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return answers[len(calls) - 1]

    monkeypatch.setattr(runtime_module, "discover_profiles", _discover)
    resolved = runtime.resolve_for_model("scout")
    assert len(calls) == 1
    assert resolved.source_path == str(tmp_path / "agent" / "agents" / "scout.md")


# === The pre-spawn re-check (Codex 10) =====================================


async def _approved(bench: _Bench, tool_call_id: str, args: dict[str, Any]) -> None:
    event = ToolCallHookEvent(tool_call_id=tool_call_id, tool_name=AGENT_TOOL_NAME, args=dict(args))
    assert await bench.hook(event) is None


async def _execute(bench: _Bench, tool_call_id: str, args: dict[str, Any]) -> ToolResult:
    execute = bench.tool.execute
    assert execute is not None
    return await execute(dict(args), ToolExecutionContext(tool_call_id=tool_call_id))


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
async def test_a_path_turned_off_between_hook_and_execute_is_refused_in_the_hooks_words(
    tmp_path: Path,
) -> None:
    real_a = _write_profile(tmp_path / "store" / "a" / "scout.md", "scout")
    real_b = _write_profile(tmp_path / "store" / "b" / "scout.md", "scout")
    link = tmp_path / "agent" / "agents" / "scout.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(real_a)
    bench = _bench(tmp_path)
    _lists(bench, f"-{real_b}")
    runtime = bench.ext.runtime
    assert runtime is not None
    args = {"profile": "scout", "task": "go"}
    await _approved(bench, "tc-1", args)
    link.unlink()
    link.symlink_to(real_b)
    result = await _execute(bench, "tc-1", args)
    assert _text(result) == (
        "Delegation refused: agent profile 'scout' is turned off in the settings, and only the "
        "user can turn it on (with /agents); available: explorer, general-purpose"
    )
    assert bench.channel.plans == []
    assert runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert bench.ext._pending == {}


async def test_a_profile_file_moved_under_an_off_path_between_hook_and_execute_is_refused(
    tmp_path: Path,
) -> None:
    """The case above with no symlink, so it runs on Windows too (verify r2 F2): the
    approved file is moved (``os.replace``) onto a path a ``-<path>`` entry turns off —
    D7's other filesystem change within one prompt. The re-check refuses in the hook's
    words; the gate checks only the refusal's shape, so this is what catches a refusal
    that can never fire (ADR-0304 Limits)."""

    approved = _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    moved = approved.with_name("parked.md")
    bench = _bench(tmp_path)
    _lists(bench, f"-{moved}")
    runtime = bench.ext.runtime
    assert runtime is not None
    args = {"profile": "scout", "task": "go"}
    await _approved(bench, "tc-1", args)
    os.replace(approved, moved)
    result = await _execute(bench, "tc-1", args)
    assert _text(result) == (
        "Delegation refused: agent profile 'scout' is turned off in the settings, and only the "
        "user can turn it on (with /agents); available: explorer, general-purpose"
    )
    assert bench.channel.plans == []
    assert runtime.remaining_delegation_budget() == MAX_DELEGATIONS_PER_PROMPT
    assert bench.ext._pending == {}


async def test_a_different_file_winning_the_name_is_still_a_mismatch(tmp_path: Path) -> None:
    bench = _scout(tmp_path, trusted=True)
    _lists(bench)
    args = {"profile": "scout", "task": "go"}
    await _approved(bench, "tc-1", args)
    _write_profile(bench.cwd / ".aelix" / "agents" / "scout.md", "scout")
    result = await _execute(bench, "tc-1", args)
    assert _text(result) == _IDENTITY_MISMATCH
    assert bench.channel.plans == []


async def test_an_edit_of_the_approved_file_at_the_same_path_runs_the_approved_object(
    tmp_path: Path,
) -> None:
    bench = _scout(tmp_path)
    _lists(bench)
    args = {"profile": "scout", "task": "go"}
    await _approved(bench, "tc-1", args)
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout", body="EDITED")
    result = await _execute(bench, "tc-1", args)
    assert result.is_error is False
    (plan,) = bench.channel.plans
    assert "EDITED" not in plan.resolved.profile.body


# === Continuation (Codex 8, ADR-0304 D8; Codex 3 r1) =======================


class _Turn:
    """The live harness as the panel's ``AgentKind`` reads it: busy while the call runs.

    The kernel holds a turn busy until its tool calls return; this test holds
    it busy from the hook to the call's end, which is the same window.
    """

    def __init__(self) -> None:
        self.is_idle = True
        self._idle = asyncio.Event()
        self._idle.set()
        self.state = type("State", (), {"resources": {}})()
        self.events: list[dict[str, Any]] = []

    def busy(self) -> None:
        self.is_idle = False
        self._idle.clear()

    def idle(self) -> None:
        self.is_idle = True
        self._idle.set()

    async def wait_for_idle(self) -> None:
        await self._idle.wait()

    async def set_resources(self, resources: dict[str, Any]) -> None:
        self.events.append(dict(resources))


class _HeldFirst(_RecordingChannel):
    """Records every plan; the FIRST one's child stays alive until :attr:`release` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.started: list[str] = []

    async def run(self, plan: Any, *, child: Any = None, on_stream: Any = None) -> Any:
        self.started.append(plan.task)
        if len(self.started) == 1:
            self.entered.set()
            await self.release.wait()
        return await super().run(plan, child=child, on_stream=on_stream)


def _saved_through_the_panel(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch, turn: _Turn
) -> tuple[ResourceSelection, AgentKind, dict[str, Any]]:
    """The REAL settings file, ``ResourceSelection`` and ``AgentKind`` the panel saves with,
    holding the lists the extension reads (``cli/entry.py``'s holder)."""

    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)
    manager = SettingsManager.create(bench.cwd, bench.agent_dir, project_trusted=False)
    selection = ResourceSelection(
        manager,
        global_base=str(bench.agent_dir),
        project_base=str(bench.cwd / ".aelix"),
        project_trusted=False,
    )
    holder: dict[str, Any] = {"result": selection.read_lists("agents")[0]}
    bench.ext.agent_lists = lambda: holder["result"]
    runtime = bench.ext.runtime
    assert runtime is not None
    kind = AgentKind(
        holder=holder,
        delegation=runtime.delegation,
        harness=lambda: turn,
        read_lists=lambda: selection.read_lists("agents"),
        cwd=str(bench.cwd),
        project_trusted=False,
        dirs=AgentDirs(
            project=str(bench.cwd / ".aelix" / "agents"),
            user=str(bench.agent_dir / "agents"),
            bundled=str(discovery.builtin_agents_dir()),
        ),
    )
    return selection, kind, holder


@pytest.mark.parametrize(
    ("mode", "tasks"),
    [("parallel", ["a", "b", "c"]), ("chain", ["a", "b {previous}"])],
)
async def test_an_approved_call_runs_its_queued_members_after_a_save_turns_the_profile_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, tasks: list[str]
) -> None:
    """Contract §5.2: member 1 held, the later ones queued (one permit; a chain's link
    waits for the one before), a panel save of ``-scout`` while the turn runs is
    deferred, and after the release every member runs the APPROVED object; then the
    apply lands and the model's next call is refused (ADR-0304 D8)."""

    from aelix_agents import batch as batch_module

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    channel = _HeldFirst()
    bench = _bench(tmp_path, channel=channel)
    turn = _Turn()
    selection, kind, holder = _saved_through_the_panel(bench, monkeypatch, turn)
    monkeypatch.setitem(batch_module._SEM_BY_LOOP, asyncio.get_running_loop(), asyncio.Semaphore(1))
    args = {"profile": "scout", "mode": mode, "tasks": tasks}
    turn.busy()
    await _approved(bench, "tc-1", args)
    approved = bench.ext._pending["tc-1"].resolved
    run = asyncio.ensure_future(_execute(bench, "tc-1", args))
    await asyncio.wait_for(channel.entered.wait(), 10)
    for _ in range(20):  # let every member that could start, start
        await asyncio.sleep(0)
    assert channel.started == ["a"], "the later members must be queued behind member 1"

    saved = await selection.save(kind, "global", {"scout": False})
    assert saved.ok, saved.error
    landed: list[Any] = []
    applied = await selection.apply(kind, on_deferred=landed.append)
    assert applied.deferred and not applied.applied
    assert applied.turned_off == ("scout",)
    assert holder["result"].global_list.entries == ()  # not applied while the turn runs
    assert json.loads((bench.agent_dir / "settings.json").read_text(encoding="utf-8")) == {
        "agents": ["-scout"]
    }

    channel.release.set()
    result = await asyncio.wait_for(run, 10)
    assert result.is_error is False, _text(result)
    assert [p.task.split()[0] for p in channel.plans] == [t.split()[0] for t in tasks]
    assert all(p.resolved.source_path == approved.source_path for p in channel.plans)
    assert all(p.resolved.profile is approved.profile for p in channel.plans)
    assert landed == []  # still deferred: the turn has not ended

    turn.idle()
    await asyncio.wait_for(_until(lambda: bool(landed)), 10)
    assert landed[0].applied and landed[0].turned_off == ("scout",)
    assert holder["result"].global_list.entries == ("-scout",)
    assert "scout" not in await _stamped(bench)
    fresh = await _call(bench, {"profile": "scout", "task": "again"}, tool_call_id="tc-2")
    assert _text(fresh).startswith("Delegation refused: agent profile 'scout' is turned off")
    assert len(channel.plans) == len(tasks)


async def _until(done: Any) -> None:
    while not done():
        await asyncio.sleep(0.01)


# === Already running: a background job, Ctrl+B (ADR-0304 D8) ================
# Adopted from the round-1 verifier's probes P1, P2 and P4.

_OFF_SCOUT = "Delegation refused: agent profile 'scout' is turned off in the settings"
_RESUME_OFF_SCOUT = "Resume refused: the agent profile 'scout' that agent ran as is turned off"


def _rig_lists(rig: Any, *entries: str) -> None:
    lists = SettingsLists(ScopeList("global", tuple(entries), str(rig.agent_dir)), None)
    rig.ext.agent_lists = lambda: lists


@pytest.mark.usefixtures("jobs_on", "reap_children")
async def test_a_background_job_whose_profile_is_turned_off_runs_to_its_end(
    tmp_path: Path,
) -> None:
    """It finishes, its notice arrives, status and result work; a resume while it
    still runs and one after it ended read the resume ``off`` text (not
    ``RESUME_RUNNING``: ADR-0304 Limits), and a fresh spawn is refused."""

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, channel=_resumable(gate))
    spawned = await call(rig, {"action": "spawn", "profile": "scout", "task": "t"})
    agent_id = _spawned_id(spawned)
    await wait_for(lambda: (rig.jobs()[0].progress or {}).get("last_text") == "halfway")
    _rig_lists(rig, "-scout")
    status = await call(rig, {"action": "status", "id": agent_id})
    assert status.is_error is False and agent_id in _text(status)
    resume = {"action": "resume", "profile": "scout", "id": agent_id, "task": "again"}
    running = _text(await call(rig, resume))
    assert running.startswith(_RESUME_OFF_SCOUT) and RESUME_RUNNING not in running
    gate.write_text("go", encoding="utf-8")
    snap = await settled(rig)
    assert snap.state == "done"
    assert len(rig.harness.undelivered_notices()) == 1
    result = await call(rig, {"action": "result", "id": agent_id})
    assert "the moved answer" in _text(result)
    assert _text(await call(rig, resume)).startswith(_RESUME_OFF_SCOUT)
    fresh = await call(rig, {"action": "spawn", "profile": "scout", "task": "t"})
    assert fresh.is_error and _text(fresh).startswith(_OFF_SCOUT)
    assert len(rig.jobs()) == 1
    await finish(rig)


@pytest.mark.usefixtures("jobs_on", "reap_children")
async def test_ctrl_b_moves_a_run_whose_profile_was_turned_off_after_it_started(
    tmp_path: Path,
) -> None:
    """Ctrl+B resolves nothing (ADR-0304 D6, D8): the run moves and ends; a later
    resume and a fresh run are starts, and are refused."""

    gate = tmp_path / "gate"
    rig = await tui_rig(tmp_path, channel=_resumable(gate))
    turn = await parked_call(rig)
    row = row_of(rig)
    await wait_for(lambda: row.stream.last_text == "halfway")
    _rig_lists(rig, "-scout")  # turned off while the run is in the foreground
    assert rig.jobs_ui.request_detach(), "Ctrl+B must still move the run"
    text = _text(await within(turn))
    assert MOVED in text
    agent_id = _agent_id(text)
    gate.write_text("go", encoding="utf-8")
    snap = await settled(rig)
    assert snap.state == "done"
    result = await call(rig, {"action": "result", "id": agent_id})
    assert "the moved answer" in _text(result)
    resume = {"action": "resume", "profile": "scout", "id": agent_id, "task": "again"}
    assert _text(await call(rig, resume)).startswith(_RESUME_OFF_SCOUT)
    fresh = await call(rig, {"profile": "scout", "task": "again"})
    assert _text(fresh).startswith(_OFF_SCOUT)
    await finish(rig)


# === Resume (ADR-0304 D7) =================================================


def _scene_lists(scene: _Scene, *entries: str) -> None:
    _lists(scene.bench, *entries)


@pytest.mark.parametrize(
    ("arrange", "text"),
    [
        (
            lambda s: _lists(s.bench, "-scout"),
            "Resume refused: the agent profile 'scout' that agent ran as is turned off in the "
            "settings, and only the user can turn it on (with /agents). Available: explorer, "
            "general-purpose.",
        ),
        (
            lambda s: _identity(s.bench, "orchestrator", ("explorer",)),
            "Resume refused: the agent profile 'scout' that agent ran as is not one this "
            "session's profile 'orchestrator' may delegate to. Available: explorer.",
        ),
        (
            lambda s: _identity(s.bench, "orchestrator", ()),
            "Resume refused: this session runs as agent profile 'orchestrator', whose agents: "
            "list is empty, so it resumes no agent.",
        ),
    ],
)
async def test_a_resume_is_refused_at_the_hook_with_its_texts(
    tmp_path: Path, arrange: Any, text: str
) -> None:
    scene = await _delegated(tmp_path)
    arrange(scene)
    before = await _records(scene.session)
    result = await _resume(scene)
    assert result.is_error is True
    assert _text(result) == text
    assert len(scene.plans) == 1
    assert await _records(scene.session) == before
    assert scene.bench.ext._pending == {}


async def test_a_resume_of_an_unknown_profile_stays_profile_changed(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    (tmp_path / "agent" / "agents" / "scout.md").unlink()
    result = await _resume(scene)
    assert _text(result) == RESUME_PROFILE_CHANGED


async def test_a_resume_with_the_profile_on_still_runs(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    _lists(scene.bench, "-reviewer")
    result = await _resume(scene)
    assert result.is_error is False, _text(result)
    assert len(scene.plans) == 2


async def test_a_resume_turned_off_between_hook_and_execute_says_the_resume_text(
    tmp_path: Path,
) -> None:
    scene = await _delegated(tmp_path)
    args = {"action": "resume", "profile": "scout", "id": scene.child_id, "task": "more"}
    await _approved(scene.bench, "tc-r", args)
    _lists(scene.bench, "-scout")
    result = await _execute(scene.bench, "tc-r", args)
    assert _text(result).startswith(
        "Resume refused: the agent profile 'scout' that agent ran as is turned off"
    )
    assert len(scene.plans) == 1


@pytest.mark.parametrize(
    "arrange",
    [
        lambda s: _lists(s.bench, "-scout"),
        lambda s: _identity(s.bench, "orchestrator", ("general-purpose",)),
        lambda s: _identity(s.bench, "orchestrator", ()),
    ],
)
async def test_the_resume_listing_leaves_out_what_may_not_be_resumed(
    tmp_path: Path, arrange: Any
) -> None:
    scene = await _delegated(tmp_path)
    arrange(scene)
    runtime = scene.bench.ext.runtime
    assert runtime is not None
    for asked, head in ((None, RESUME_NEEDS_ID), ("nope", RESUME_UNKNOWN_ID)):
        found = await runtime.find_resume_target(asked)
        assert isinstance(found, str) and found.startswith(head)
        assert scene.child_id not in found
        assert found.endswith(
            "- 1 more ran as an agent profile this session may not delegate to now; they "
            "cannot be resumed."
        )


async def test_with_nothing_filtered_the_listing_is_todays_and_takes_no_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_agents.child_session import find_resume_target as plain

    scene = await _delegated(tmp_path)
    _lists(scene.bench)
    runtime = scene.bench.ext.runtime
    assert runtime is not None
    views: list[int] = []
    real = type(runtime).delegation

    def _counted(self: Any) -> Any:
        views.append(1)
        return real(self)

    monkeypatch.setattr(type(runtime), "delegation", _counted)
    found = await runtime.find_resume_target(None)
    assert found == await plain(scene.session, None)
    assert RESUMABLE_HEAD in found and scene.child_id in found
    assert views == []


async def test_an_agent_of_a_profile_now_gone_stays_listed_under_a_policy(tmp_path: Path) -> None:
    """Only the policy verdicts leave an agent out: an unknown (deleted) profile's
    agent is listed as before, and its resume is refused as before."""

    scene = await _delegated(tmp_path)
    _lists(scene.bench, "-reviewer")
    (tmp_path / "agent" / "agents" / "scout.md").unlink()
    runtime = scene.bench.ext.runtime
    assert runtime is not None
    found = await runtime.find_resume_target(None)
    assert isinstance(found, str)
    assert scene.child_id in found
    assert "may not delegate to now" not in found


async def test_a_found_id_builds_no_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = await _delegated(tmp_path)
    _lists(scene.bench, "-reviewer")
    runtime = scene.bench.ext.runtime
    assert runtime is not None
    views: list[int] = []
    real = type(runtime).delegation

    def _counted(self: Any) -> Any:
        views.append(1)
        return real(self)

    monkeypatch.setattr(type(runtime), "delegation", _counted)
    found = await runtime.find_resume_target(scene.child_id)
    assert not isinstance(found, str)
    assert views == []


def test_the_listing_keeps_a_resumable_agent_among_ten_hidden_ones() -> None:
    from aelix_agents import child_session

    def _run(profile: str, position: int) -> Any:
        identity = {"profile": profile, "task_preview": "t", "limits": {}, "tool_call_id": "tc"}
        return child_session._Run(
            key=f"k{position}", data=identity, position=position, start=identity, limits_agree=True
        )

    groups: dict[str, list[Any]] = {}
    for index in range(10):
        sid = f"0b8a2a8e-0000-4000-8000-{index:012d}"
        groups[sid] = [_run("scout", index)]
    keep = "0b8a2a8e-0000-4000-8000-999999999999"
    groups[keep] = [_run("explorer", 0)]
    real_lineage = child_session._lineage
    real_model = child_session._by_the_model
    try:
        child_session._lineage = lambda runs: (runs[0], runs[0])  # type: ignore[assignment]
        child_session._by_the_model = lambda identity: True  # type: ignore[assignment]
        text = child_session._listing(groups, lambda profile: profile != "scout")
    finally:
        child_session._lineage = real_lineage  # type: ignore[assignment]
        child_session._by_the_model = real_model  # type: ignore[assignment]
    assert keep in text
    assert text.endswith(
        "- 10 more ran as an agent profile this session may not delegate to now; they cannot "
        "be resumed."
    )
    assert len(text.encode("utf-8")) <= child_session.LISTING_BYTES


# === The extension door (ADR-0304 D17, owner) ==============================


def _model_tool(api: ExtensionAPI) -> AgentTool:
    """ADR-0304 D17's shape, on the public API only: the filtered door, then ``spawn``."""

    async def execute(args: dict[str, Any], ctx: Any) -> ToolResult:
        runtime = api.runtime.subagents
        if not isinstance(runtime, ModelDelegation):
            return ToolResult(content=[TextContent(text="cannot check")], is_error=True)
        try:
            resolved = runtime.resolve_for_model(args["profile"])
        except Exception as exc:  # noqa: BLE001 — every exception is a refusal
            return ToolResult(
                content=[TextContent(text=f"Delegation refused: {exc}")], is_error=True
            )
        result = await cast("Any", runtime).spawn(resolved, args.get("task", "go"))
        return ToolResult(
            content=[TextContent(text=f"{result.profile}: {result.summary}")],
            is_error=not result.ok,
        )

    tool = AgentTool(
        name="delegate_probe",
        description="Delegates one task.",
        parameters={"type": "object", "properties": {"profile": {"type": "string"}}},
        execute=execute,
    )
    api.register_tool(tool)
    return tool


async def test_a_model_facing_extension_tool_on_the_filtered_door(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 4 r1: the tool reaches the PUBLIC ``spawn`` and the recording channel —
    zero calls for a refusal, exactly one for an accepted profile."""

    bench = _scout(tmp_path)
    _lists(bench, "-reviewer")
    extension = Extension(name="probe")
    api = ExtensionAPI(extension, bench.runtime)
    runtime = bench.runtime.subagents
    assert runtime is not None
    spawns: list[str] = []
    real_spawn = runtime.spawn

    async def _counted(resolved: Any, task: str, **kwargs: Any) -> Any:
        spawns.append(resolved.name)
        return await real_spawn(resolved, task, **kwargs)

    monkeypatch.setattr(runtime, "spawn", _counted)
    tool = _model_tool(api)
    assert tool.execute is not None
    refused = await tool.execute({"profile": "reviewer"}, None)
    assert refused.is_error is True
    assert _text(refused) == (
        "Delegation refused: agent profile 'reviewer' is turned off in the settings, and only "
        "the user can turn it on (with /agents); available: explorer, general-purpose, scout"
    )
    assert spawns == [] and bench.channel.plans == []
    started = await tool.execute({"profile": "explorer", "task": "look"}, None)
    assert _text(started) == "explorer: the child says hello" and started.is_error is False
    assert spawns == ["explorer"]
    (plan,) = bench.channel.plans
    assert (plan.resolved.name, plan.task) == ("explorer", "look")
    # The public human door stays open (owner): resolve_profile still returns.
    assert runtime.resolve_profile("reviewer").name == "reviewer"
    assert isinstance(runtime, ModelDelegation) and isinstance(runtime, SubagentRuntime)
    with pytest.raises(DelegationNotAllowed):
        runtime.resolve_for_model("reviewer")  # type: ignore[attr-defined]
    with pytest.raises(ProfileError):
        runtime.resolve_for_model("reviewer")  # type: ignore[attr-defined]
    assert issubclass(DelegationRefused, (ProfileError, DelegationNotAllowed))


def test_a_runtime_with_only_the_seven_members_still_binds() -> None:
    from aelix_coding_agent.extensions.api import _ExtensionRuntime

    class _Seven:
        contract_version = 1

        def resolve_profile(self, name_or_path: str, *, allow_project: bool = False) -> Any: ...

        async def spawn(self, resolved: Any, task: str, **kwargs: Any) -> Any: ...

        def list(self) -> list[Any]:
            return []

        def status(self, id: str) -> Any: ...

        async def stop(self, id: str) -> None: ...

        async def stop_all(self) -> None: ...

    runtime = _ExtensionRuntime()
    seven = _Seven()
    members = ("contract_version", "resolve_profile", "spawn", "list", "status", "stop", "stop_all")
    assert all(hasattr(seven, name) for name in members)
    runtime.bind_subagents(seven)  # type: ignore[arg-type]
    assert runtime.subagents is seven
    assert not isinstance(seven, ModelDelegation)
