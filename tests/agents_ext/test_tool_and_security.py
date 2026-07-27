"""The ``agent`` tool and its security floor — ADR-0197 §(d)/(f)/(g)/(i)/(l).

Driven against the REAL extension objects — :class:`_ExtensionRuntime`,
:class:`ExtensionAPI`, :class:`ExtensionContext` — and not against fakes of
them, for one reason that matters: ``ctx.has_ui`` is
``runtime.ui is not HEADLESS_UI_CONTEXT`` (``extensions/api.py:1082-1083``), a
TIME-VARYING value (finding OC-7). A hand-rolled context with a boolean
attribute cannot express "the UI was bound after this extension loaded", which
is the state every interactive session is actually in.

What IS faked is the process boundary: :class:`_RecordingChannel` stands in for
:class:`PrintChannel` so these tests can assert on the :class:`SpawnPlan` —
the posture, the cwd, the live tool grant — without spawning anything. The
process layer itself is pinned by ``test_print_channel_spawn.py``; the two
tests here that need a real envelope drive a real (stub) child.

THE ONE SHAPE TO INTERNALISE BEFORE READING ON: consent is taken in the
extension's ``tool_call`` HOOK and spent in the tool's ``execute()``, linked by
``tool_call_id``. :func:`_call` therefore runs BOTH halves, and renders a
blocked hook the way the kernel does (``loop.py:518-531``) — as a model-readable
error result — so a refusal from either half is observed identically.
"""

from __future__ import annotations

import dataclasses
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import (
    BeforeAgentStartHookEvent,
    ToolCallHookEvent,
)
from aelix_agent_core.types import AgentTool
from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import (
    AGENT_TOOL_NAME,
    PrintChannel,
    RunningChild,
    SpawnPlan,
    build_child_argv,
    narrow_tools,
)
from aelix_agents.tool import (
    MAX_ROSTER,
    ROSTER_DESCRIPTION_CHARS,
    ROSTER_MAX_BYTES,
    build_roster,
    create_agent_tool,
    render_subagent_result,
)
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.agents.discovery import ProfileError
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    _ExtensionRuntime,
)
from aelix_coding_agent.subagent_contract import (
    DEPTH_ENV_VAR,
    ResolvedProfile,
    SubagentResult,
)

# === Scaffolding ==============================================================


class _FakeUI:
    """The two ``ExtensionUIContext`` methods this feature touches.

    Binding one of these via ``runtime.bind_ui`` is what makes ``ctx.has_ui``
    True, so the consent gate's live read sees a real transition rather than a
    flag someone set.
    """

    def __init__(self, *answers: object) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, list[str]]] = []
        self.status: dict[str, str | None] = {}

    async def select(
        self, title: str, options: list[str], opts: object = None
    ) -> object:
        self.calls.append((title, list(options)))
        return self._answers.pop(0) if self._answers else None

    def set_status(self, key: str, text: str | None) -> None:
        self.status[key] = text


class _RecordingChannel:
    """A :class:`PrintChannel` that records the plan instead of spawning."""

    def __init__(self, result: SubagentResult | None = None) -> None:
        self.plans: list[SpawnPlan] = []
        self._result = result

    async def run(
        self,
        plan: SpawnPlan,
        *,
        child: RunningChild | None = None,
        on_stream: Any = None,
    ) -> SubagentResult:
        self.plans.append(plan)
        if child is not None:
            child.state = "done"
        if self._result is not None:
            return self._result
        return SubagentResult(
            id=plan.id,
            profile=plan.resolved.name,
            ok=True,
            status="ok",
            summary="the child says hello",
            permission_mode=plan.permission_mode.value,
        )


@dataclass
class _Bench:
    ext: AgentsExtension
    api: ExtensionAPI
    extension: Extension
    runtime: _ExtensionRuntime
    ctx: ExtensionContext
    ui: _FakeUI
    channel: _RecordingChannel
    cwd: Path
    agent_dir: Path

    @property
    def tool(self) -> AgentTool:
        """Read FRESH every time — ``before_agent_start`` replaces the object."""

        return self.extension.tools[AGENT_TOOL_NAME]

    async def hook(self, event: ToolCallHookEvent) -> Any:
        handler = self.extension.handlers["tool_call"][0]
        return await handler(event, self.ctx)


def _write_profile(path: Path, name: str, *, extra: str = "", body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = f"{extra}\n" if extra else ""
    path.write_text(
        f"---\nname: {name}\ndescription: {name} agent\n{suffix}---\n\n"
        f"{body or 'You are the agent.'}\n",
        encoding="utf-8",
    )
    return path


def _bench(
    tmp_path: Path,
    *,
    posture: PermissionMode = PermissionMode.DEFAULT,
    tools: tuple[str, ...] = ("read", "write", "bash"),
    project_trusted: bool = False,
    has_ui: bool = False,
    answers: tuple[object, ...] = (),
    channel: _RecordingChannel | None = None,
) -> _Bench:
    agent_dir = tmp_path / "agent"
    (agent_dir / "agents").mkdir(parents=True, exist_ok=True)
    cwd = tmp_path / "project"
    cwd.mkdir(parents=True, exist_ok=True)

    runtime = _ExtensionRuntime()
    ui = _FakeUI(*answers)
    if has_ui:
        runtime.bind_ui(ui)  # type: ignore[arg-type]
    extension = Extension(name="aelix-agents")
    api = ExtensionAPI(extension, runtime)

    ext = AgentsExtension(
        posture=PermissionPosture(mode=posture),
        agent_dir=str(agent_dir),
        # The PRE-HOOK fallbacks. ``cli/entry.py`` knows both at construction
        # time and a live ``ExtensionContext`` overrides them the moment the
        # first hook fires — but ``/agents run`` can be the very first thing a
        # user types, before any hook has run at all.
        cwd=str(cwd),
        project_trusted=project_trusted,
    )
    ext(api)
    recording = channel if channel is not None else _RecordingChannel()
    ext.runtime.channel = recording  # type: ignore[union-attr, assignment]

    ctx = ExtensionContext(
        runtime,
        cwd=str(cwd),
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: list(tools),
        get_system_prompt=lambda: "system",
        is_project_trusted=lambda: project_trusted,
    )
    return _Bench(
        ext=ext,
        api=api,
        extension=extension,
        runtime=runtime,
        ctx=ctx,
        ui=ui,
        channel=recording,
        cwd=cwd,
        agent_dir=agent_dir,
    )


async def _call(
    bench: _Bench, args: dict[str, Any], *, tool_call_id: str = "tc-1"
) -> ToolResult:
    """Run the hook and then ``execute()``, exactly as the kernel would.

    A blocked hook is rendered as an ``is_error`` tool result because that is
    what the kernel does with one (``loop.py:518-531``): the model sees a
    refusal it can read either way, so the tests assert on the OUTCOME rather
    than on which half produced it.
    """

    event = ToolCallHookEvent(
        tool_call_id=tool_call_id, tool_name=AGENT_TOOL_NAME, args=dict(args)
    )
    blocked = await bench.hook(event)
    if blocked is not None and blocked.block:
        return ToolResult(
            content=[TextContent(text=blocked.reason or "")], is_error=True
        )
    execute = bench.tool.execute
    assert execute is not None
    return await execute(
        dict(args), ToolExecutionContext(tool_call_id=tool_call_id)
    )


def _text(result: ToolResult) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


def _profile(**kwargs: Any) -> AgentProfile:
    base: dict[str, Any] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
    }
    base.update(kwargs)
    return AgentProfile(**base)


# === §(l) tool narrowing ======================================================


def test_tools_intersected_with_parent_grant() -> None:
    """STRUCTURAL, not advisory — the child is LAUNCHED with the result.

    A profile naming ``bash`` under a parent whose grant excludes it gets no
    ``bash``, no matter what the profile says and no matter what its model asks
    for.
    """

    narrowed = narrow_tools(_profile(tools=("read", "bash")), ["read", "write"])
    assert narrowed.profile.tools == ("read",)
    assert "bash" in narrowed.dropped


async def test_the_plan_carries_the_parents_live_grant(tmp_path: Path) -> None:
    """The wiring half: the grant is read from ``ctx`` at spawn time, not cached.

    ``get_active_tools`` is rebuilt by every ``register_tool``
    (``core.py:847-906`` materialises the ``None`` sentinel into a real list),
    so a captured copy would be the set that existed before this extension
    loaded its own tool. ``SpawnPlan.parent_tools`` is where that live grant
    lands, and ``PrintChannel`` intersects the child's request with it.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, tools=("read", "grep"))

    await _call(bench, {"profile": "scout", "task": "go"})
    assert bench.channel.plans[0].parent_tools == ("read", "grep")


def test_empty_intersection_emits_no_tools() -> None:
    """``tools=()`` must survive as ``()`` all the way to ``--no-tools``.

    ``--tools ''`` parses to ``[]``, which ``_resolve_active_tools`` reads as
    falsy → ``None`` → EVERY tool active. The exact inversion of what an empty
    grant asked for, so the empty tuple may never be collapsed.
    """

    narrowed = narrow_tools(_profile(tools=("bash",)), ["read"])
    assert narrowed.profile.tools == ()
    argv = build_child_argv(
        narrowed.profile,
        prompt_path="/tmp/p.md",
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd="/tmp",
        parent_cwd="/tmp",
    )
    assert "--no-tools" in argv
    assert "--tools" not in argv


def test_agent_tool_never_in_child_grant() -> None:
    """The second, independent anti-nesting layer (the first is the depth env var)."""

    narrowed = narrow_tools(
        _profile(tools=("read", AGENT_TOOL_NAME)), ["read", AGENT_TOOL_NAME]
    )
    assert AGENT_TOOL_NAME not in (narrowed.profile.tools or ())
    assert AGENT_TOOL_NAME in narrowed.dropped


async def test_dropped_tools_recorded(tmp_path: Path) -> None:
    """The narrowing rides back to the parent's MODEL on a real envelope.

    Run against a real (stub) child rather than the recording channel, because
    ``dropped_tools`` is computed inside ``PrintChannel.run`` and folded into
    the envelope there — a fake channel would be asserting on itself.
    """

    script = textwrap.dedent(
        """
        import json, sys
        sys.stdout.write(json.dumps({"type": "agent_end"}) + "\\n")
        """
    )
    channel = PrintChannel(argv_builder=lambda *a, **k: [sys.executable, "-c", script])
    plan = SpawnPlan(
        id="sub-1",
        resolved=ResolvedProfile(
            name="scout",
            profile=_profile(tools=("read", "bash")),
            source_path="/u/scout.md",
            scope="user",
        ),
        task="t",
        cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        permission_mode=PermissionMode.PLAN,
        parent_tools=("read",),
    )
    result = await channel.run(plan)
    assert result.dropped_tools == ("bash",)
    assert "bash" in _text(render_subagent_result(result))


# === §(d) the depth guard =====================================================


async def test_depth_guard_in_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The THIRD layer, and it RETURNS rather than raising.

    Build-time suppression (``entry.py``) and the seam's own refusal
    (``bind_subagents``) are the first two. This one exists because neither can
    cover a process whose depth changed after the harness was built, and a raise
    here would abort the parent's whole turn instead of telling its model no.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    event = ToolCallHookEvent(
        tool_call_id="tc-depth",
        tool_name=AGENT_TOOL_NAME,
        args={"profile": "scout", "task": "go"},
    )
    assert await bench.hook(event) is None  # armed at depth 0

    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "task": "go"},
        ToolExecutionContext(tool_call_id="tc-depth"),
    )
    assert result.is_error is True
    assert "max depth" in _text(result)
    assert bench.channel.plans == []


async def test_a_seam_takeover_stands_this_extension_down(tmp_path: Path) -> None:
    """MEDIUM #13 — a displaced runtime must stop spawning.

    ``bind_subagents(..., replace=True)`` is the one legal way for a second
    orchestration engine to take the seam over, and it returns the displaced
    runtime to nobody, calls no ``stop_all`` on it and offers no ``on_replaced``.
    Without a live check this extension keeps its ``agent`` tool registered and
    its ``tool_call`` hook armed, so the MODEL's delegations keep spawning
    through the displaced registry while ``/agents run`` drives the new one —
    two live child registries, which is exactly the split-brain
    ``api.py``'s double-bind refusal exists to prevent, arriving through the
    spelling that refusal permits.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    # Sanity: it works while we hold the seam.
    assert (await _call(bench, {"profile": "scout", "task": "go"})).is_error is False
    assert len(bench.channel.plans) == 1

    class _Successor:
        contract_version = 1

        def resolve_profile(
            self, name_or_path: str, *, allow_project: bool = False
        ) -> Any:  # pragma: no cover
            raise NotImplementedError

        async def spawn(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise NotImplementedError

        def list(self) -> list[Any]:  # pragma: no cover
            return []

        def status(self, id: str) -> Any:  # noqa: A002 — Protocol spelling
            raise KeyError(id)  # pragma: no cover

        async def stop(self, id: str) -> None:  # noqa: A002 — Protocol spelling
            return None  # pragma: no cover

        async def stop_all(self) -> None:  # pragma: no cover
            return None

    bench.runtime.bind_subagents(_Successor(), replace=True)  # type: ignore[arg-type]

    result = await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-2")
    assert result.is_error is True
    assert len(bench.channel.plans) == 1, "no second child through the old registry"


async def test_a_profile_swapped_between_hook_and_execute_is_refused(
    tmp_path: Path,
) -> None:
    """MEDIUM #10 — the identity guard is a LIVE gate, not decoration.

    It used to compare ``pending.grant.source_path`` against
    ``pending.resolved.source_path``. ``PendingSpawn`` is constructed at one
    site from the same ``resolved`` the grant was taken against, and every
    ``SpawnGrant`` constructor sets ``source_path=resolved.source_path``, so the
    two were equal by construction on every path and the branch could not fire —
    while sitting directly above the spawn and reading like an anti-substitution
    defence.

    The gate now re-resolves the approved NAME and compares. The window is real:
    the ``tool_call`` hook runs in the kernel's SEQUENTIAL prep phase
    (``loop.py:510-531``) and ``execute()`` under ``asyncio.gather``
    (``loop.py:877``), and the profile search path is a directory the model's own
    tools can write. Here the user-scope file the human approved is replaced by a
    PROJECT-scoped one of the same name, which additionally WINS the collision
    (``agents/service.py:99-100``) — the exact B5 shape, arriving one step later.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, project_trusted=True)

    event = ToolCallHookEvent(
        tool_call_id="tc-swap",
        tool_name=AGENT_TOOL_NAME,
        args={"profile": "scout", "task": "go"},
    )
    assert await bench.hook(event) is None  # approved against the user profile

    # Between the hook and execute(), a project-scoped 'scout' appears.
    _write_profile(
        tmp_path / "project" / ".aelix" / "agents" / "scout.md",
        "scout",
        body="You are the ATTACKER's scout.",
    )

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "task": "go"},
        ToolExecutionContext(tool_call_id="tc-swap"),
    )
    assert result.is_error is True
    assert "do not match" in _text(result)
    assert bench.channel.plans == []


async def test_the_unswapped_identity_still_spawns(tmp_path: Path) -> None:
    """The other half of MEDIUM #10 — the gate must not refuse the normal case.

    A re-resolution that could not reproduce the approved path would fail every
    delegation closed, which is safe and useless. This is the pin that it does
    not.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is False
    assert len(bench.channel.plans) == 1


async def test_a_profile_deleted_between_hook_and_execute_is_refused(
    tmp_path: Path,
) -> None:
    """FAIL CLOSED: an identity that can no longer be resolved never runs."""

    path = _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    event = ToolCallHookEvent(
        tool_call_id="tc-gone",
        tool_name=AGENT_TOOL_NAME,
        args={"profile": "scout", "task": "go"},
    )
    assert await bench.hook(event) is None
    path.unlink()

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "task": "go"},
        ToolExecutionContext(tool_call_id="tc-gone"),
    )
    assert result.is_error is True
    assert bench.channel.plans == []


async def test_the_hook_refuses_at_depth_before_any_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child must not even be asked whether to delegate."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True, posture=PermissionMode.YOLO)
    monkeypatch.setenv(DEPTH_ENV_VAR, "1")

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is True
    assert bench.ui.calls == []
    assert bench.channel.plans == []


# === MEDIUM #2 — the delegation budget ========================================


async def test_one_prompt_cannot_start_unbounded_delegations(tmp_path: Path) -> None:
    """MEDIUM #2 — nothing bounded how many children one injected turn started.

    ``_grant_for`` returns ``consented=True`` with NO dialog whenever the clamp
    is ``PLAN`` and ``approval_mode != "ask"`` — the out-of-the-box case for a
    ``default`` parent with a default profile, i.e. the common one. Measured
    against the shipped runtime before the cap::

        DEFAULT parent, default profile:
          dialogs shown to the human: 0
          child processes started   : 200

    Each is a full ``-m aelix_coding_agent`` process holding the parent's API
    keys with up to ``DEFAULT_TIMEOUT_MS`` (10 min) of wall clock, and
    ``"agent"`` is deliberately absent from ``builtin/permission.py``'s
    ``_MUTATING``, so the parent's own permission ladder never saw them either. A
    prompt-injected README saying "call agent() 200 times" therefore cost real
    money and 200 real processes with no gate at all.
    """

    from aelix_agents.runtime import MAX_DELEGATIONS_PER_PROMPT

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    results = [
        await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id=f"tc-{i}")
        for i in range(MAX_DELEGATIONS_PER_PROMPT + 8)
    ]

    assert bench.ui.calls == [], "a read-only child is still not worth a dialog"
    assert len(bench.channel.plans) == MAX_DELEGATIONS_PER_PROMPT
    refused = [r for r in results if r.is_error]
    assert len(refused) == 8
    # The refusal has to be READABLE by the model, and has to tell it what to do
    # instead — otherwise it retries the same call for the rest of the turn.
    assert "maximum number" in _text(refused[0])
    assert "untrusted" in _text(refused[0])


async def test_the_next_prompt_gets_a_fresh_budget(tmp_path: Path) -> None:
    """A CEILING per prompt, not a quota per session (MEDIUM #2).

    A long legitimate session may delegate many times; a single injected turn may
    not. ``before_agent_start`` fires once per USER prompt — not once per model
    turn — so an injected instruction cannot refresh its own budget by taking
    another turn.
    """

    from aelix_agents.runtime import MAX_DELEGATIONS_PER_PROMPT

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    for i in range(MAX_DELEGATIONS_PER_PROMPT):
        await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id=f"a-{i}")
    exhausted = await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id="a-x")
    assert exhausted.is_error is True

    await bench.ext._on_before_agent_start(
        BeforeAgentStartHookEvent(), bench.ctx
    )

    fresh = await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id="b-0")
    assert fresh.is_error is False
    assert len(bench.channel.plans) == MAX_DELEGATIONS_PER_PROMPT + 1


async def test_the_live_child_cap_applies_to_both_doors(tmp_path: Path) -> None:
    """MEDIUM #2's resource half — the registry may not grow without bound.

    Enforced in ``_run`` rather than on each door so every path into the registry
    passes it, including the user-typed ``/agents run`` one. Driven here by
    pre-loading the registry, because P2's own shape (a sequential tool and an
    awaited command handler) means a real session cannot hold more than one live
    child anyway — which is exactly why the cap has to be asserted directly.
    """

    from aelix_agents.print_channel import RunningChild
    from aelix_agents.runtime import MAX_LIVE_CHILDREN

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    runtime = bench.ext.runtime
    assert runtime is not None

    for i in range(MAX_LIVE_CHILDREN):
        runtime._children[f"live-{i}"] = RunningChild(id=f"live-{i}", profile="scout")

    # The MODEL door.
    blocked = await _call(bench, {"profile": "scout", "task": "go"})
    assert blocked.is_error is True
    assert "live delegated agents" in _text(blocked)
    assert bench.channel.plans == []

    # And the USER-TYPED door, which takes its own consent and never sees the
    # per-prompt budget.
    resolved = runtime.resolve_profile("scout")
    result = await runtime.spawn(resolved, "go")
    assert result.ok is False
    assert "live delegated agents" in (result.error or "")
    assert bench.channel.plans == []


async def test_a_human_typed_run_is_not_rate_limited_per_prompt(tmp_path: Path) -> None:
    """The budget is scoped to the MODEL door only (MEDIUM #2).

    ``/agents run`` is a human typing a command, one delegation per keystroke
    burst, and rate-limiting a human who is already the gate would be theatre.
    """

    from aelix_agents.runtime import MAX_DELEGATIONS_PER_PROMPT

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    runtime = bench.ext.runtime
    assert runtime is not None
    resolved = runtime.resolve_profile("scout")

    for _ in range(MAX_DELEGATIONS_PER_PROMPT + 5):
        assert (await runtime.spawn(resolved, "go")).ok is True
    assert len(bench.channel.plans) == MAX_DELEGATIONS_PER_PROMPT + 5


# === P2 scope refusals ========================================================


async def test_background_true_is_rejected_in_p2(tmp_path: Path) -> None:
    """A background child has no owner to reap it and no channel to report on.

    Rejected LOUDLY rather than ignored: silently dropping ``background: true``
    would let a model believe it had started something it had not, and then
    proceed as if the work were under way.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    result = await _call(
        bench, {"profile": "scout", "task": "go", "background": True}
    )
    assert result.is_error is True
    assert "ackground" in _text(result)
    assert bench.channel.plans == []


@pytest.mark.parametrize("mode", ["parallel", "chain"])
async def test_parallel_and_chain_modes_rejected(tmp_path: Path, mode: str) -> None:
    """P2 ships single-mode delegation only; topology is P3/P4 extension policy."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    result = await _call(bench, {"profile": "scout", "task": "go", "mode": mode})
    assert result.is_error is True
    assert bench.channel.plans == []

    # And the runtime refuses independently, so the tool's message is a better
    # error rather than the only guard.
    runtime = bench.ext.runtime
    assert runtime is not None
    with pytest.raises(ValueError, match="P3"):
        await runtime.spawn(
            ResolvedProfile(
                name="scout",
                profile=_profile(),
                source_path="/u/scout.md",
                scope="user",
            ),
            "go",
            mode=mode,  # type: ignore[arg-type]
        )


async def test_cwd_outside_parent_rejected(tmp_path: Path) -> None:
    """AN ERROR, never a silent fallback — ``cwd`` is MODEL-chosen.

    Falling back to the parent's cwd would run the task somewhere the model did
    not ask for and nobody was told about. It composes with, and does not
    replace, ``child_trust_argv``: this bounds WHERE, that bounds WHAT.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    bench = _bench(tmp_path)

    result = await _call(
        bench, {"profile": "scout", "task": "go", "cwd": str(outside)}
    )
    assert result.is_error is True
    assert "outside" in _text(result)
    assert bench.channel.plans == []


async def test_cwd_inside_parent_is_contained_and_absolute(tmp_path: Path) -> None:
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    (bench.cwd / "sub").mkdir()

    result = await _call(bench, {"profile": "scout", "task": "go", "cwd": "sub"})
    assert result.is_error is False
    assert bench.channel.plans[0].cwd == str((bench.cwd / "sub").resolve())


# === §(g) the child's project-trust flags =====================================


def test_spawn_argv_follows_the_two_clause_rule(tmp_path: Path) -> None:
    """``--no-approve`` is CONDITIONAL (finding OC-4), and the argv proves it.

    Clause 1 — same cwd and the trust gate has nothing to gate → emit NOTHING,
    which is what preserves the shipped ``inherit_skills: true`` default
    (``has_trust_requiring_project_resources`` deliberately omits
    ``.aelix/skills/``).

    Clause 2 — any other case, above all a DIFFERENT cwd, which is always
    model-chosen → ``--no-approve``. That is the one measured escalation: a
    vendored ``.aelix/extensions/*.py`` the parent never loaded, reached through
    ``ProjectTrustStore``'s nearest-ancestor walk.
    """

    root = tmp_path / "repo"
    (root / "vendor" / "sdk").mkdir(parents=True)

    def argv_for(child_cwd: Path) -> list[str]:
        return build_child_argv(
            _profile(tools=("read",)),
            prompt_path="/tmp/p.md",
            task="t",
            permission_mode=PermissionMode.PLAN,
            child_cwd=str(child_cwd),
            parent_cwd=str(root),
        )

    assert "--no-approve" not in argv_for(root)
    assert "--no-approve" in argv_for(root / "vendor" / "sdk")

    # A project extension in the parent's own cwd re-arms clause 2.
    (root / ".aelix" / "extensions").mkdir(parents=True)
    (root / ".aelix" / "extensions" / "x.py").write_text("", encoding="utf-8")
    assert "--no-approve" in argv_for(root)


def test_the_child_argv_always_disables_delegation() -> None:
    """Belt-and-braces with the depth env var: the flag beats the settings gate."""

    argv = build_child_argv(
        _profile(tools=("read",)),
        prompt_path="/tmp/p.md",
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd="/tmp",
        parent_cwd="/tmp",
    )
    assert "--no-agents" in argv


# === §(f) identity =============================================================


async def test_agent_tool_refuses_project_scoped_profile_without_confirmation(
    tmp_path: Path,
) -> None:
    """FINDING B5 — the model chose this string, so a repo file must not answer it.

    Directory trust is a yes-once decision ancestors inherit
    (``project_trust.py:60-61``); it is NOT consent to a project-local IDENTITY,
    which additionally WINS a name collision against the user's own. No prompt
    is offered, because a prompt here would be a prompt the MODEL summoned.
    """

    _write_profile(tmp_path / "project" / ".aelix" / "agents" / "helper.md", "helper")
    bench = _bench(tmp_path, project_trusted=True, has_ui=True)

    result = await _call(bench, {"profile": "helper", "task": "go"})
    assert result.is_error is True
    assert "project-local" in _text(result)
    assert bench.ui.calls == []
    assert bench.channel.plans == []


async def test_the_identity_gate_is_a_parameter_not_a_mode(tmp_path: Path) -> None:
    """The DELIBERATE ASYMMETRY: the user-typed door may consent, the model's may not.

    RENAMED (P2 review, MEDIUM #15). It was
    ``test_agents_run_can_consent_with_approve`` and exercised neither
    ``/agents run`` nor ``--approve``: it calls ``runtime.resolve_profile``
    directly and never touches ``parsed.project_trust_override``. Worse, the
    ``--approve`` shortcut the plan specified (§2(f), §5.5:
    ``allow_project = ctx.parsed.project_trust_override is True``) was
    deliberately NOT implemented — the shipped ``/agents run`` starts
    ``allow_project`` at False unconditionally and raises it only via
    ``_confirm_project_agent_for_run``, which is STRICTER and is the behaviour
    we want. A green test named for a launch-flag door that does not exist is
    how an auditor concludes the path is covered.

    So the name now states what it proves: the gate is a PARAMETER on
    ``resolve_profile`` that a caller must pass explicitly, not a mode the
    process can be started in. The model-driven door (the tool above) cannot
    reach it at all; ``/agents run``'s per-identity confirmation is pinned in
    ``tests/tui/test_agents_run_command.py``.
    """

    _write_profile(tmp_path / "project" / ".aelix" / "agents" / "helper.md", "helper")
    bench = _bench(tmp_path, project_trusted=True)
    runtime = bench.ext.runtime
    assert runtime is not None

    with pytest.raises(ProfileError, match="project-local"):
        runtime.resolve_profile("helper", allow_project=False)

    resolved = runtime.resolve_profile("helper", allow_project=True)
    assert resolved.name == "helper"
    assert resolved.scope == "project"


async def test_a_path_shaped_profile_name_is_refused(tmp_path: Path) -> None:
    """Delegation resolves profiles by NAME only, and that is a security choice.

    Accepting a path would let a model-chosen string select an identity file
    from anywhere on the filesystem — outside the repo, hence outside both the
    project-scope refusal and the trust gate. The path-taking door is
    ``--agent-file``, where a human typed it.
    """

    bench = _bench(tmp_path)
    result = await _call(
        bench, {"profile": "../../etc/evil.md", "task": "go"}
    )
    assert result.is_error is True
    assert bench.channel.plans == []


async def test_unknown_profile_lists_roster(tmp_path: Path) -> None:
    """The refusal has to be actionable, or the model just retries the same name."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    _write_profile(tmp_path / "agent" / "agents" / "auditor.md", "auditor")
    bench = _bench(tmp_path)

    result = await _call(bench, {"profile": "nope", "task": "go"})
    assert result.is_error is True
    text = _text(result)
    assert "scout" in text
    assert "auditor" in text


# === OC-8 ``approval_mode: ask`` ==============================================


async def test_approval_mode_ask_opens_the_consent_dialog(tmp_path: Path) -> None:
    """FINDING OC-8 — ``ask`` PROMPTS; it never refuses the spawn.

    This replaces the drafted ``test_approval_mode_ask_refuses_to_spawn`` and
    retires the "validated, not read" deferral at ``agents/profile.py:218-220``.
    Note that the clamp under a DEFAULT parent is already ``plan``, so this is
    also the case that proves the dialog is opened by the profile's REQUEST and
    not only by a write-capable clamp.
    """

    _write_profile(
        tmp_path / "agent" / "agents" / "scout.md", "scout", extra="approval_mode: ask"
    )
    bench = _bench(tmp_path, has_ui=True, answers=("Run read-only (plan)",))

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert len(bench.ui.calls) == 1
    assert result.is_error is False
    assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN


async def test_approval_mode_ask_is_silent_and_read_only_when_headless(
    tmp_path: Path,
) -> None:
    """Headless DOWNGRADES, never refuses — no new failure path for ``-p`` users."""

    _write_profile(
        tmp_path / "agent" / "agents" / "scout.md", "scout", extra="approval_mode: ask"
    )
    bench = _bench(tmp_path, posture=PermissionMode.YOLO)

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is False
    assert bench.ui.calls == []
    assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN


async def test_a_read_only_delegation_does_not_prompt(tmp_path: Path) -> None:
    """The common case: DEFAULT parent + ``inherit`` → PLAN → no dialog.

    Asking a human to approve a READER would train them to click through the
    dialog that matters. The gate fires for the write-capable cells only.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert bench.ui.calls == []
    assert result.is_error is False
    assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN


# === the roster ===============================================================


async def test_roster_injected_and_capped(tmp_path: Path) -> None:
    """The roster is prompt text on EVERY request, so both budgets are real.

    A deliberate divergence from pi, which exports ``formatAgentList`` and never
    imports it — pi's parent model has to guess profile names.
    """

    for index in range(MAX_ROSTER + 6):
        _write_profile(
            tmp_path / "agent" / "agents" / f"a{index:02d}.md", f"a{index:02d}"
        )
    bench = _bench(tmp_path)

    handler = bench.extension.handlers["before_agent_start"][0]
    await handler(BeforeAgentStartHookEvent(), bench.ctx)

    description = bench.tool.description
    roster_lines = [
        line for line in description.splitlines() if line.startswith("- a")
    ]
    assert 0 < len(roster_lines) <= MAX_ROSTER
    assert len(description.encode("utf-8")) < ROSTER_MAX_BYTES + 2048
    assert "a00" in description


def test_a_long_profile_description_is_truncated() -> None:
    roster = build_roster([_profile(description="x" * 5000)])
    assert len(roster) < ROSTER_DESCRIPTION_CHARS + 100
    assert roster.endswith("…")


def test_a_roster_of_maximal_descriptions_stays_under_the_byte_cap() -> None:
    profiles = [
        _profile(name=f"agent{i:02d}", description="y" * 400) for i in range(MAX_ROSTER)
    ]
    assert len(build_roster(profiles).encode("utf-8")) <= ROSTER_MAX_BYTES


async def test_no_profiles_still_produces_a_usable_description(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    assert "No agent profiles" in bench.tool.description


# === the tool object ==========================================================


def test_execution_mode_survives_dataclasses_replace() -> None:
    """SECURITY, not performance — and the roster refresh must not drop it.

    The kernel downgrades the WHOLE BATCH to sequential when any tool in it
    declares ``execution_mode="sequential"`` (``types.py:47-56``,
    ``loop.py:683-693``), which is what stops two ``agent`` calls in one
    assistant message racing two modals onto ``tui/chrome.py:518``'s single
    ``_modal`` slot. Because the description is re-stamped with
    :func:`dataclasses.replace` on every ``before_agent_start``, losing the
    field there would silently reopen the hazard.
    """

    async def _noop(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[])

    tool = create_agent_tool(description="d", execute=_noop)
    assert tool.execution_mode == "sequential"
    replaced = dataclasses.replace(tool, description="d2")
    assert replaced.execution_mode == "sequential"
    assert replaced.execute is tool.execute
    assert replaced.name == AGENT_TOOL_NAME


async def test_the_registered_tool_is_sequential(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    assert bench.tool.execution_mode == "sequential"


async def test_the_roster_refresh_keeps_the_tool_callable(tmp_path: Path) -> None:
    """A re-registration that dropped ``execute`` would brick delegation."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    handler = bench.extension.handlers["before_agent_start"][0]
    await handler(BeforeAgentStartHookEvent(), bench.ctx)

    assert "scout" in bench.tool.description
    assert bench.tool.execute is not None
    assert bench.tool.execution_mode == "sequential"
    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is False


# === the seam and the teardown ================================================


def test_the_extension_stands_down_at_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FINDING I4 — product-core will not HOLD a runtime inside a child.

    Build-time suppression in ``cli/entry.py`` is the primary gate, but a
    user-scope tier-1 extension still loads in a child with
    ``inherit_extensions: true``, so the seam refuses the bind itself. This
    pins the extension's own response to that refusal: register NOTHING. A tool
    that cannot spawn is worse than no tool, because the model would keep
    calling it and reading a failure it cannot act on.
    """

    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    runtime = _ExtensionRuntime()
    extension = Extension(name="aelix-agents")
    AgentsExtension()(ExtensionAPI(extension, runtime))

    assert extension.tools == {}
    assert extension.handlers == {}
    assert extension.cleanups == []
    assert runtime.subagents is None


def test_a_foreign_runtime_is_not_displaced(tmp_path: Path) -> None:
    """FINDING B7 — extension LOAD ORDER is not a contract.

    A silent swap would leave the ``agent`` tool and ``/agents run`` driving two
    different child registries. A second orchestration engine has to opt in with
    ``replace=True``, which this extension deliberately does not do for anything
    that is not already its own runtime.
    """

    class _Foreign:
        """A CONFORMING second runtime — all seven Protocol members.

        This used to read ``class _Foreign: contract_version = 1`` and the seam
        took it, which is precisely what P2 review MEDIUM #7 was about: a
        self-declared ``int`` was the seam's ONLY conformance check, so a stub
        standing in for ``aelix-team`` did not have to look like a runtime at
        all. ``bind_subagents`` now also runs the ``runtime_checkable``
        ``isinstance``, and the refusal is pinned by
        ``tests/agents/test_subagent_contract.py::
        test_bind_subagents_refuses_a_partial_implementation``.
        """

        contract_version = 1

        def resolve_profile(
            self, name_or_path: str, *, allow_project: bool = False
        ) -> Any:  # pragma: no cover — the BIND is the subject, not the call
            raise NotImplementedError

        async def spawn(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            raise NotImplementedError

        def list(self) -> list[Any]:  # pragma: no cover
            return []

        def status(self, id: str) -> Any:  # noqa: A002 — Protocol spelling
            raise KeyError(id)  # pragma: no cover

        async def stop(self, id: str) -> None:  # noqa: A002 — Protocol spelling
            return None  # pragma: no cover

        async def stop_all(self) -> None:  # pragma: no cover
            return None

    runtime = _ExtensionRuntime()
    foreign = _Foreign()
    runtime.bind_subagents(foreign)  # type: ignore[arg-type]

    extension = Extension(name="aelix-agents")
    ext = AgentsExtension()
    ext(ExtensionAPI(extension, runtime))

    assert runtime.subagents is foreign
    assert ext.degraded is True
    assert extension.tools == {}


async def test_teardown_stops_children_and_releases_the_seam(tmp_path: Path) -> None:
    """ADR-0197 forbids any task outliving session teardown.

    The reaper is a DETACHED task (finding B1), so ``stop_all`` has to JOIN it
    rather than merely signal it — and the unbind must be identity-scoped, or
    whichever extension tears down first nulls the slot for a runtime it does
    not own.
    """

    bench = _bench(tmp_path)
    assert bench.runtime.subagents is bench.ext.runtime

    cleanup = bench.extension.cleanups[0]
    await cleanup()

    assert bench.runtime.subagents is None


async def test_a_stale_cleanup_does_not_unbind_the_current_runtime(
    tmp_path: Path,
) -> None:
    """One extension instance is threaded through EVERY harness rebuild.

    So an older bus's cleanup can run while ``self._runtime`` already points at
    the next harness's runtime. The cleanup closes over the runtime it was
    registered with, which is what keeps ``unbind_subagents`` honest.
    """

    bench = _bench(tmp_path)
    stale_cleanup = bench.extension.cleanups[0]

    # A rebuild: same extension instance, fresh runtime + api.
    fresh_runtime = _ExtensionRuntime()
    fresh_extension = Extension(name="aelix-agents")
    bench.ext(ExtensionAPI(fresh_extension, fresh_runtime))
    current = bench.ext.runtime

    await stale_cleanup()

    assert fresh_runtime.subagents is current
