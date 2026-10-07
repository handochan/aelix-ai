"""A delegated child inherits the parent's EFFECTIVE thinking level — #354.

MEASURED ON THE PRE-FIX TREE (``402a8013``), against a local mock of a
reasoning-mandatory endpoint (``.omc/probes/354-live/impl/``): a parent on
``--thinking high`` sent ``"reasoning": {"effort": "high"}`` and its child, on the
SAME model, sent ``{"effort": "none"}`` and got ``400 Reasoning is mandatory for
this endpoint and cannot be disabled``. The same in a TUI parent whose level
came from ``defaultThinkingLevel``. The owner's default model
(``openrouter/z-ai/glm-5.3-flash``) is such an endpoint, so every delegation
died in one turn. #304 had forwarded the parent's MODEL through a late-bound
getter; nothing forwarded the level.

The value travels one chain, and every hop is pinned below::

    cli/entry.py _live_thinking_of(session_host)   (runtime.harness.state)
      → AgentsExtension.thinking → _host_thinking   (live ExtensionAPI fallback)
        → SubagentHost.thinking
          → runtime._parent_thinking → SpawnPlan.parent_thinking
            → inherit_thinking(profile)            (profile ``thinking:`` wins;
                                                     only with the parent's model)
              → resolver.profile_to_flags → the child's ``--thinking``

The level travels WITH the model (review round 2, pi's direction): a profile that
names its own ``model`` or ``provider`` and no ``thinking:`` gets no inherited
level — pi's subagent example forwards ``--thinking`` only when the agent file
names no model (``inheritsDispatchConfig = !agent.model``).

Every argv assertion is a ``parse_args`` ROUND TRIP, for the reason
``test_child_context_files_inherit.py`` gives: an unrecognised ``--key`` is
swallowed into ``unknown_flags`` with no diagnostic, so a substring match would
stay green over a token the child never honours.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import (
    PrintChannel,
    RunningChild,
    SpawnPlan,
    build_child_argv,
    inherit_thinking,
)
from aelix_agents.rpc_channel import RpcChannel, build_rpc_child_argv
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import inherits_parent_model
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli import entry as entry_module
from aelix_coding_agent.cli.args import VALID_THINKING_LEVELS, Args, parse_args
from aelix_coding_agent.cli.entry import _live_thinking_of
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _default_actions,
    _ExtensionRuntime,
)
from aelix_coding_agent.subagent_contract import ResolvedProfile

_LAUNCH_PREFIX = 3
"""``[sys.executable, "-m", "aelix_coding_agent"]`` — ``parse_args`` sees the rest."""


# === Fixtures =================================================================


def _profile(**kwargs: Any) -> AgentProfile:
    base: dict[str, Any] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
        "tools": ("read",),
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
        emit({"type": "response", "command": kind, "success": True,
              "id": rid, "data": {}})
        if kind == "prompt":
            turn("done")
    """
)


def _capture(real: Any, script: str, seen: list[list[str]], *tail: str) -> Any:
    """Record the argv the REAL builder makes from what the channel passed, then
    run a stub so the channel finishes in milliseconds."""

    def _build(*args: Any, **kwargs: Any) -> list[str]:
        seen.append(real(*args, **kwargs))
        return [sys.executable, "-c", script, *tail]

    return _build


def _child(argv: list[str]) -> Args:
    return parse_args(argv[_LAUNCH_PREFIX:])


_BUILDER_KWARGS: dict[str, Any] = {
    "prompt_path": "/tmp/p.md",
    "task": "t",
    "permission_mode": PermissionMode.PLAN,
    "child_cwd": "/tmp",
    "parent_cwd": "/tmp",
}


# === The precedence itself ====================================================


def test_a_profile_with_no_thinking_takes_the_parents_level() -> None:
    profile = _profile()
    assert profile.thinking is None, "the bundled profiles declare none"

    inherited = inherit_thinking(profile, "high")

    assert inherited.thinking == "high"
    # One field, nothing else: a fill, not a rebuild.
    assert (inherited.name, inherited.tools, inherited.body) == (
        profile.name,
        profile.tools,
        profile.body,
    )


def test_the_profiles_own_thinking_wins_over_the_parents() -> None:
    """``thinking: low`` is an explicit statement about THIS child. Identity, not
    equality: no rewrite happened at all."""

    pinned = _profile(thinking="low")
    assert inherit_thinking(pinned, "high") is pinned


def test_no_evidence_leaves_the_profile_untouched() -> None:
    profile = _profile()
    assert inherit_thinking(profile, None) is profile


_OWN_ROUTE = [
    pytest.param({"model": "z-ai/glm-5.3-flash", "provider": "openrouter"}, id="model+provider"),
    pytest.param({"model": "z-ai/glm-5.3-flash"}, id="model only"),
    pytest.param({"provider": "openrouter"}, id="provider only"),
]


@pytest.mark.parametrize("route", _OWN_ROUTE)
def test_a_profile_that_names_its_own_model_or_provider_inherits_no_level(
    route: dict[str, str],
) -> None:
    """The level travels WITH the model (review round 2, pi's direction). pi's
    subagent example forwards ``--thinking ctx.thinkingLevel`` only when the
    agent file names no model. A level is a statement about the parent's model;
    a profile that names its own route and wants a level says ``thinking:``.
    Identity: no rewrite at all, so the child's argv is the pre-#354 one."""

    own = _profile(**route)
    assert not inherits_parent_model(own)
    assert inherit_thinking(own, "high") is own


@pytest.mark.parametrize("route", _OWN_ROUTE)
def test_an_own_route_profile_keeps_its_own_thinking(route: dict[str, str]) -> None:
    pinned = _profile(thinking="low", **route)
    assert inherit_thinking(pinned, "high") is pinned


def test_the_thinking_gate_is_the_model_gate() -> None:
    """``inherit_thinking`` reads the SAME predicate ``child_model_flags`` does,
    so the two cannot drift: a profile inherits the level exactly when it
    inherits the model."""

    assert inherits_parent_model(_profile())
    assert inherit_thinking(_profile(), "high").thinking == "high"


def test_a_parent_at_off_hands_its_child_off() -> None:
    """``off`` is a level, not "unset". A parent is at ``off`` when it set it
    (``--thinking off``, ``/thinking off``) or when nothing set any level: a
    fresh headless parent with no ``--thinking``, no ``--agent`` ``thinking:``,
    no resumed level and no rpc ``set_thinking_level`` (#286 keeps
    ``defaultThinkingLevel`` out of ``-p``). Its child then gets ``--thinking
    off``, whose wire is identical to the pre-#354 child's. pi forwards ``off``
    too."""

    assert inherit_thinking(_profile(), "off").thinking == "off"


_EVERY_LEVEL = ("off", "minimal", "low", "medium", "high", "xhigh")
"""Written out, not read off ``VALID_THINKING_LEVELS``: a validation that
silently drops a level (``minimal`` treated as unset, Codex review round 1)
would also shrink a parametrisation derived from the constant it edited."""


def test_every_level_list_is_the_clis() -> None:
    assert set(_EVERY_LEVEL) == set(VALID_THINKING_LEVELS)


@pytest.mark.parametrize("own", _EVERY_LEVEL)
def test_every_profile_level_wins_over_the_parents(own: str) -> None:
    """The top rung for every level, ``off`` included: a profile that says
    ``thinking: "off"`` under a ``high`` parent runs its child at ``off``. The
    parent side of "off is a level, not unset" is pinned by
    ``test_a_parent_at_off_hands_its_child_off``; this is the profile side
    (review round 3 mutant: ``profile.thinking not in (None, "off")``)."""

    pinned = _profile(thinking=own)
    assert inherit_thinking(pinned, "high") is pinned


# === The fill reaches a real argv, on both transports =========================


@pytest.mark.parametrize("builder", [build_child_argv, build_rpc_child_argv], ids=["print", "rpc"])
def test_the_inherited_level_is_a_thinking_flag_a_real_child_parses(builder: Any) -> None:
    inherited = builder(inherit_thinking(_profile(), "high"), **_BUILDER_KWARGS)
    untouched = builder(inherit_thinking(_profile(), None), **_BUILDER_KWARGS)

    assert _child(inherited).thinking == "high"
    assert "thinking" in _child(inherited).provided
    # The negative control: a builder that always emitted a level would pass
    # the first assertion too.
    assert _child(untouched).thinking is None


@pytest.mark.parametrize("builder", [build_child_argv, build_rpc_child_argv], ids=["print", "rpc"])
def test_the_flag_lands_once_and_carries_the_profiles_level(builder: Any) -> None:
    """Why the inherit is a profile fill and not a second append: a parent at
    ``high`` delegating to a ``thinking: low`` profile has two candidate levels,
    and two emission sites would put two ``--thinking`` on the argv, the LAST
    of which wins in ``parse_args`` — i.e. whichever site happened to run
    second."""

    argv = builder(inherit_thinking(_profile(thinking="low"), "high"), **_BUILDER_KWARGS)

    assert argv.count("--thinking") == 1
    assert _child(argv).thinking == "low"


# === The channels carry the plan's answer ====================================


async def test_the_print_channel_puts_the_plans_level_on_the_childs_argv(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    channel = PrintChannel(
        argv_builder=_capture(build_child_argv, _PRINT_STUB, seen, "Task: do the thing")
    )

    await channel.run(
        _plan(tmp_path, parent_thinking="high"), child=RunningChild(id="s", profile="scout")
    )
    await channel.run(_plan(tmp_path), child=RunningChild(id="t", profile="scout"))

    assert [_child(a).thinking for a in seen] == ["high", None]


async def test_the_rpc_channel_puts_the_plans_level_on_the_childs_argv(tmp_path: Path) -> None:
    """The second transport through its own ``run`` — it builds its argv at a
    different line from a different builder."""

    seen: list[list[str]] = []
    channel = RpcChannel(argv_builder=_capture(build_rpc_child_argv, _RPC_STUB, seen))

    await channel.run(
        _plan(tmp_path, parent_thinking="medium"), child=RunningChild(id="s", profile="scout")
    )
    await channel.run(_plan(tmp_path), child=RunningChild(id="t", profile="scout"))

    assert [_child(a).thinking for a in seen] == ["medium", None]


async def test_a_profile_level_beats_the_plans_on_the_channel_too(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    channel = PrintChannel(
        argv_builder=_capture(build_child_argv, _PRINT_STUB, seen, "Task: do the thing")
    )

    await channel.run(
        _plan(tmp_path, resolved=_resolved(_profile(thinking="low")), parent_thinking="high"),
        child=RunningChild(id="s", profile="scout"),
    )

    assert [_child(a).thinking for a in seen] == ["low"]


# === The runtime reads the host once per spawn ===============================


def _runtime(tmp_path: Path, thinking: Any, seen: list[list[str]]) -> _SubagentRuntimeImpl:
    """The default channel, built by ``__post_init__``; only its argv builder is
    patched, so the host → plan hop is under test too."""

    host = SubagentHost(cwd=lambda: str(tmp_path), thinking=thinking)
    runtime = _SubagentRuntimeImpl(host=host)
    assert runtime.channel is not None, "__post_init__ must build the channel"
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
    )
    return runtime


async def test_the_hosts_level_reaches_the_child_and_is_read_per_spawn(tmp_path: Path) -> None:
    """``/thinking`` moves the level between two delegations; a captured value
    would hand the second child the first one's."""

    seen: list[list[str]] = []
    answers = ["high", "low"]
    runtime = _runtime(tmp_path, lambda: answers[len(seen)], seen)

    await runtime.spawn(_resolved(), "go")
    await runtime.spawn(_resolved(), "again")

    assert [_child(a).thinking for a in seen] == ["high", "low"]


@pytest.mark.parametrize("level", _EVERY_LEVEL)
async def test_every_valid_level_reaches_the_child(tmp_path: Path, level: str) -> None:
    """Through the runtime's own validation (``_parent_thinking``) and the
    default channel: each level the child's ``--thinking`` accepts, ``minimal``
    included, lands on its argv. Codex round 1: a whitelist without
    ``minimal`` passed every test the commit touched, and a ``--thinking
    minimal`` parent's child then sent no reasoning and died with the 400."""

    seen: list[list[str]] = []
    runtime = _runtime(tmp_path, lambda: level, seen)

    await runtime.spawn(_resolved(), "go")

    assert len(seen) == 1
    assert seen[0].count("--thinking") == 1
    assert _child(seen[0]).thinking == level


def _routed_runtime(tmp_path: Path, seen: list[list[str]], *, level: str) -> _SubagentRuntimeImpl:
    host = SubagentHost(
        cwd=lambda: str(tmp_path),
        model=lambda: Model(id="parent-model", provider="parentprov"),
        thinking=lambda: level,
    )
    runtime = _SubagentRuntimeImpl(host=host)
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
    )
    return runtime


async def test_the_level_goes_where_the_model_goes(tmp_path: Path) -> None:
    """End to end through the runtime with a parent MODEL as well as a level:
    the child that gets the parent's ``--model`` gets its ``--thinking``; the
    child of a profile naming its own model gets neither."""

    seen: list[list[str]] = []
    runtime = _routed_runtime(tmp_path, seen, level="high")

    await runtime.spawn(_resolved(), "inherits")
    await runtime.spawn(_resolved(_profile(model="own-model", provider="ownprov")), "own route")
    await runtime.spawn(_resolved(_profile(model="own-model")), "own model")
    await runtime.spawn(
        _resolved(_profile(model="own-model", provider="ownprov", thinking="low")), "own level"
    )

    got = [(_child(a).model, _child(a).thinking) for a in seen]
    assert got == [
        ("parent-model", "high"),
        ("own-model", None),
        ("own-model", None),
        ("own-model", "low"),
    ]


async def test_an_unwired_host_keeps_the_pre_354_argv(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: str(tmp_path)))
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
    )

    await runtime.spawn(_resolved(), "go")

    assert len(seen) == 1
    assert "--thinking" not in seen[0]


def _boom() -> str:
    raise RuntimeError("the runtime host is mid-swap")


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(_boom, id="getter raises"),
        pytest.param(lambda: "max", id="a level the child CLI would drop"),
        pytest.param(lambda: "", id="empty"),
        pytest.param(lambda: 3, id="not a string"),
        pytest.param(lambda: None, id="None"),
    ],
)
async def test_no_evidence_costs_the_child_its_inheritance_not_its_spawn(
    tmp_path: Path, answer: Any
) -> None:
    """Guarded, unlike ``context_files``: an unreadable level is "no evidence".
    The child still spawns, with the argv it had before #354."""

    seen: list[list[str]] = []
    runtime = _runtime(tmp_path, answer, seen)

    await runtime.spawn(_resolved(), "go")

    assert len(seen) == 1, "the spawn happened"
    assert "--thinking" not in seen[0]
    assert _child(seen[0]).diagnostics == [], "nothing the child would warn about"


# === The extension asks the live harness =====================================


class _Harness:
    def __init__(self, level: str) -> None:
        class _State:
            thinking_level = level

        self.state = _State()
        self.current_model = None


class _Runtime:
    def __init__(self, level: str) -> None:
        self.harness = _Harness(level)
        self.session = None


def _api(level: str | None) -> ExtensionAPI:
    """A live ``ExtensionAPI``; ``None`` leaves the runtime unbound, so
    ``get_thinking_level`` raises the way it does before a harness exists."""

    runtime = _ExtensionRuntime()
    if level is not None:
        actions = _default_actions()
        actions.get_thinking_level = lambda: level
        runtime.bind_core(actions)
    return ExtensionAPI(Extension(name="aelix-agents"), runtime)


def _extension(tmp_path: Path, *, getter: Any, api_level: str | None) -> AgentsExtension:
    cwd = tmp_path / "project"
    cwd.mkdir(parents=True, exist_ok=True)
    ext = AgentsExtension(
        posture=PermissionPosture(mode=PermissionMode.DEFAULT),
        agent_dir=str(tmp_path / "agent"),
        cwd=str(cwd),
        project_trusted=True,
        no_context_files=lambda: False,
        session=lambda: None,
        thinking=getter,
    )
    ext(_api(api_level))
    return ext


def test_the_wired_getter_beats_the_live_api(tmp_path: Path) -> None:
    ext = _extension(tmp_path, getter=lambda: "high", api_level="low")
    assert ext._host_thinking() == "high"
    assert ext._host().thinking() == "high", "and it is what the host hands the runtime"


@pytest.mark.parametrize(
    "getter",
    [
        pytest.param(None, id="unwired"),
        pytest.param(lambda: None, id="runtime not built yet"),
        pytest.param(_boom, id="getter raises"),
    ],
)
def test_the_live_api_answers_when_the_getter_cannot(tmp_path: Path, getter: Any) -> None:
    assert _extension(tmp_path, getter=getter, api_level="medium")._host_thinking() == "medium"


def test_no_getter_and_no_bound_harness_is_no_evidence(tmp_path: Path) -> None:
    assert _extension(tmp_path, getter=None, api_level=None)._host_thinking() is None


async def test_agents_run_follows_the_parents_level_through_the_real_entry_getter(
    tmp_path: Path,
) -> None:
    """End to end from ``cli/entry.py``'s holder: the REAL ``_live_thinking_of``,
    the extension's own host, the runtime it built, the default channel. Then
    ``/thinking low`` — a direct write to the live harness's state that fires no
    hook — and the next delegation follows it."""

    session_host: dict[str, Any] = {"runtime": _Runtime("high")}
    ext = _extension(tmp_path, getter=lambda: _live_thinking_of(session_host), api_level="off")
    impl = ext._runtime
    assert impl is not None
    seen: list[list[str]] = []
    assert impl.channel is not None
    impl.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
    )

    await impl.spawn(_resolved(), "go")
    session_host["runtime"].harness.state.thinking_level = "low"
    await impl.spawn(_resolved(), "again")
    session_host["runtime"].harness = _Harness("xhigh")  # ``/new`` swaps the harness
    await impl.spawn(_resolved(), "and again")

    assert [_child(a).thinking for a in seen] == ["high", "low", "xhigh"]


def test_live_thinking_of_follows_the_holder() -> None:
    host: dict[str, Any] = {}
    assert _live_thinking_of(host) is None, "no runtime yet — startup, or an embedder"

    host["runtime"] = _Runtime("high")
    assert _live_thinking_of(host) == "high"

    host["runtime"].harness = _Harness("off")
    assert _live_thinking_of(host) == "off"


def test_entry_wires_the_extensions_thinking_getter_to_the_live_holder() -> None:
    """``cli/entry.py`` passes ``thinking=lambda: _live_thinking_of(session_host)``
    to the ``AgentsExtension`` it builds — read off the source, because that
    construction sits inside ``main``'s startup and has no seam of its own.
    Without the keyword the extension would still answer from the live
    ``ExtensionAPI``; this pins the ADR-0243 order (the runtime host's harness
    first) rather than leaving it to the fallback."""

    tree = ast.parse(Path(entry_module.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentsExtension"
    ]
    assert len(calls) == 1, "one construction site"
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "thinking" in kw, "the getter is not wired"
    lam = kw["thinking"]
    assert isinstance(lam, ast.Lambda)
    body = lam.body
    assert isinstance(body, ast.Call)
    assert isinstance(body.func, ast.Name) and body.func.id == "_live_thinking_of"
    assert [a.id for a in body.args if isinstance(a, ast.Name)] == ["session_host"]
