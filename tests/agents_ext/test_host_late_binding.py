"""#304 — the host reads the LIVE harness, not the last hook's context.

``/agents run`` is a slash command. It fires no hook, so the
``ExtensionContext`` the delegation extension keeps
(``AgentsExtension._ctx``) is in one of three states when a human types it:

* ``None`` — it is the first thing typed in a fresh TUI;
* STALE — it belongs to the session ``/new`` / ``/resume`` / ``/fork`` /
  ``/reload`` just tore down, and every attribute raises
  ``ExtensionError("stale")``;
* LIVE but carrying a SNAPSHOT — ``_make_context_kwargs`` passes
  ``"model": self._state.model`` by value, so ``/model <id>`` followed by
  ``/agents run`` (neither fires a hook) leaves the context naming the model
  the parent was on BEFORE the switch.

#199 (ADR-0243 A.3a) fixed exactly this for the SESSION getter and left its
neighbours on the old path. #304 is the bill: the reporter's child ran on its
own default rather than the parent's ``--model``, and that default was
reasoning-mandatory, so the delegation died on a provider 400.

WHAT IS PINNED HERE. The first half drives the REAL ``/agents run`` door
(``_SubagentRuntimeImpl.spawn``) through the REAL ``PrintChannel`` with only the
argv builder replaced, so the assertion is on the child's actual command line —
the thing ADR-0197 §(l) makes the security boundary. The second half is the
audit the issue asked for: every ``_ctx``-derived value on ``SubagentHost``,
measured in all three states.

The standalone probes these were lifted from are
``.omc/specs/304-probe-parent-model-inherit.py`` and
``.omc/specs/304-probe-ctx-audit.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import PrintChannel, narrow_tools
from aelix_agents.runtime import _frozen_tools
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli.entry import _live_model_of
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    _default_actions,
    _ExtensionRuntime,
)
from aelix_coding_agent.subagent_contract import ResolvedProfile

PARENT_MODEL = Model(
    id="anthropic/claude-haiku-4.5", provider="openrouter", api="openai-completions"
)
"""What the parent is running NOW — ``--model`` on argv, or the last ``/model``."""

DEAD_MODEL = Model(
    id="dead/previous-session", provider="openrouter", api="openai-completions"
)
"""What a stale context, or a live context built before ``/model``, still names."""

PARENT_TOOLS = ["read", "bash"]
DEAD_TOOLS = ["read", "bash", "write", "edit"]
"""Deliberately WIDER than :data:`PARENT_TOOLS`: a dead harness's grant must not
be what a child inherits, and neither must "no grant at all"."""

NO_TOOLS: list[str] = []
"""A parent whose LIVE grant is genuinely empty — ``aelix --no-tools``
(``cli/entry.py::_resolve_active_tools`` returns ``[]`` for it), or an extension
that called ``set_active_tools([])``.

A third value, not a spelling of the other two, and the one this file's
``active_tools`` rows would otherwise never exercise."""


# === scaffolding =============================================================


def _resolved(tmp_path: Path) -> ResolvedProfile:
    """A profile that declares NO model — the case that inherits the parent's."""

    path = tmp_path / "agent" / "agents" / "scout.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: scout\ndescription: scout agent\n---\n\nYou are the scout.\n",
        encoding="utf-8",
    )
    profile = AgentProfile(
        name="scout",
        description="scout agent",
        body="You are the scout.",
        file_path=str(path),
        scope="user",
    )
    return ResolvedProfile(
        name="scout", profile=profile, source_path=str(path), scope="user"
    )


def _runtime_with_tools(tools: list[str]) -> _ExtensionRuntime:
    """An ``_ExtensionRuntime`` bound the way ``AgentHarness`` binds one.

    ``ExtensionAPI.get_active_tools`` reaches ``runtime.actions``; without
    :meth:`bind_core` every action is a throwing stub, which is the state an
    embedder that never built a harness is in.
    """

    runtime = _ExtensionRuntime()
    actions = _default_actions()
    actions.get_active_tools = lambda: list(tools)
    runtime.bind_core(actions)
    return runtime


def _context(
    runtime: _ExtensionRuntime,
    cwd: Path,
    *,
    model: Model | None,
    tools: list[str],
    project_trusted: bool = True,
) -> ExtensionContext:
    return ExtensionContext(
        runtime,
        cwd=str(cwd),
        model=model,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: list(tools),
        get_system_prompt=lambda: "system",
        is_project_trusted=lambda: project_trusted,
    )


def _extension(
    tmp_path: Path,
    ctx_state: str,
    *,
    wire_model: bool = True,
    live_tools: list[str] = PARENT_TOOLS,
) -> AgentsExtension:
    """An ``AgentsExtension`` wired the way ``cli/entry.py`` wires one.

    ``wire_model=False`` reproduces the branch-base shape — the ``model=``
    getter absent, everything else identical — so a test can state what the
    fallback still does.

    ``live_tools`` is the CURRENT harness's grant, reached both through the live
    ``ExtensionAPI`` and through a live/snapshot ``_ctx``. The STALE state keeps
    :data:`DEAD_TOOLS` whatever this is: the dead harness's grant is the wrong
    answer by construction, and staying wider than the live one is what makes
    "did we read the dead harness?" observable.
    """

    cwd = tmp_path / "project"
    cwd.mkdir(parents=True, exist_ok=True)

    runtime = _runtime_with_tools(live_tools)
    api = ExtensionAPI(Extension(name="aelix-agents"), runtime)

    # ``cli/entry.py``'s ``session_host`` holder, carrying an
    # ``AgentSessionRuntime`` stand-in. ``harness`` is re-pointed by the runtime
    # on every session swap and ``current_model`` is what ``/model`` writes.
    class _Harness:
        current_model = PARENT_MODEL

    class _Runtime:
        harness = _Harness()
        session = None

    session_host: dict[str, Any] = {"runtime": _Runtime()}

    wiring: dict[str, Any] = {}
    if wire_model:
        # THE REAL entry.py FUNCTION, not a restatement of it.
        wiring["model"] = lambda: _live_model_of(session_host)

    ext = AgentsExtension(
        posture=PermissionPosture(mode=PermissionMode.DEFAULT),
        agent_dir=str(tmp_path / "agent"),
        cwd=str(cwd),
        project_trusted=True,
        no_context_files=lambda: False,
        session=lambda: None,
        **wiring,
    )
    ext(api)

    if ctx_state == "live":
        ext._ctx = _context(runtime, cwd, model=PARENT_MODEL, tools=live_tools)
    elif ctx_state == "snapshot":
        # A live context built BEFORE ``/model`` moved the harness on.
        ext._ctx = _context(runtime, cwd, model=DEAD_MODEL, tools=live_tools)
    elif ctx_state == "stale":
        dead = _runtime_with_tools(DEAD_TOOLS)
        ext._ctx = _context(dead, cwd, model=DEAD_MODEL, tools=DEAD_TOOLS)
        dead.invalidate("stale")
    elif ctx_state != "none":  # pragma: no cover — a typo in a parametrisation
        raise AssertionError(f"unknown ctx state {ctx_state!r}")
    return ext


async def _agents_run(ext: AgentsExtension, tmp_path: Path) -> list[str]:
    """``/agents run scout run it`` — returns the child's argv.

    The channel is a REAL :class:`PrintChannel`, constructed exactly as
    ``_SubagentRuntimeImpl.__post_init__`` constructs the default one
    (``parent_model=self.host.model``), so the late read this issue is about
    happens where it happens in production. Only ``argv_builder`` is replaced,
    and it still returns a real command the channel really spawns — the pump,
    the reaper and the envelope all run.
    """

    impl = ext.runtime
    assert impl is not None
    seen: list[list[str]] = []

    def _spy(*_args: Any, **kwargs: Any) -> list[str]:
        model = kwargs["parent_model"]
        seen.append(
            ["--model", getattr(model, "id", None) or "<no --model on the argv>"]
        )
        return [sys.executable, "-c", ""]

    impl.channel = PrintChannel(parent_model=impl.host.model, argv_builder=_spy)
    await impl.spawn(_resolved(tmp_path), "run it")
    assert seen, "the channel never built an argv"
    return seen[0]


# === the defect ==============================================================


@pytest.mark.parametrize(
    "ctx_state",
    [
        pytest.param("none", id="no hook has fired (first command in a fresh TUI)"),
        pytest.param("stale", id="stale context (right after /new, /resume, /fork)"),
        pytest.param("snapshot", id="live context, pre-/model snapshot"),
        pytest.param("live", id="live context (the one case that always worked)"),
    ],
)
async def test_agents_run_puts_the_parents_model_on_the_childs_argv(
    tmp_path: Path, ctx_state: str
) -> None:
    """The issue, in all four states. Three of them used to be wrong.

    Measured on the branch base (``.omc/specs/304-probe-parent-model-inherit.py``):
    ``none`` and ``stale`` emitted no ``--model`` at all, and ``snapshot``
    emitted ``dead/previous-session``. Only ``live`` was right, and a test that
    covered only ``live`` — or only ``none`` — would not have closed this.
    """

    argv = await _agents_run(_extension(tmp_path, ctx_state), tmp_path)
    assert argv == ["--model", PARENT_MODEL.id]


async def test_the_getter_beats_a_live_context_that_disagrees(tmp_path: Path) -> None:
    """PRECEDENCE, pinned on its own: the getter wins, ``_ctx`` is the fallback.

    The same rule ``_host_session`` follows (#199). It matters because
    ``ExtensionContext.model`` is a value snapshot, so "there is a live context"
    is not the same as "the context is right" — the ``snapshot`` state above is
    a live context naming the wrong model.
    """

    ext = _extension(tmp_path, "snapshot")
    assert ext._ctx is not None, "the fallback source is present and disagrees"
    assert ext._ctx.model is DEAD_MODEL
    assert ext._host_model() is PARENT_MODEL


async def test_an_unwired_extension_still_falls_back_to_a_live_context(
    tmp_path: Path,
) -> None:
    """``None`` is the unwired default — an embedder that wires no getter keeps
    the behaviour it had before #304, which is the hook context or nothing."""

    assert _extension(tmp_path, "live", wire_model=False)._host_model() is PARENT_MODEL
    assert _extension(tmp_path, "none", wire_model=False)._host_model() is None


async def test_a_getter_that_raises_costs_the_child_nothing_but_the_fallback(
    tmp_path: Path,
) -> None:
    """A broken getter means "not yet", never a failed spawn — the same
    containment ``_host_session`` applies to its own."""

    def _boom() -> Model:
        raise RuntimeError("the runtime host is mid-swap")

    ext = _extension(tmp_path, "live")
    ext.model = _boom
    assert ext._host_model() is PARENT_MODEL  # from ``_ctx``, and no raise
    ext._ctx = None
    assert ext._host_model() is None


def test_live_model_of_follows_the_holder(tmp_path: Path) -> None:
    """``cli/entry.py::_live_model_of`` — ``None`` before the runtime exists,
    and the CURRENT harness's model once it does.

    The harness object is replaced on every ``/new`` / ``/resume`` / ``/fork`` /
    ``/reload``; ``AgentSessionRuntime.harness`` reads through to whichever one
    the host holds now, which is why the getter takes the holder rather than a
    harness.
    """

    class _Harness:
        def __init__(self, model: Model | None) -> None:
            self.current_model = model

    class _Runtime:
        def __init__(self, model: Model | None) -> None:
            self.harness = _Harness(model)

    host: dict[str, Any] = {}
    assert _live_model_of(host) is None, "no runtime yet — startup, or an embedder"

    host["runtime"] = _Runtime(PARENT_MODEL)
    assert _live_model_of(host) is PARENT_MODEL

    # ``/new``: the runtime swaps the harness, and the getter follows.
    host["runtime"].harness = _Harness(DEAD_MODEL)
    assert _live_model_of(host) is DEAD_MODEL

    host["runtime"].harness = _Harness(None)
    assert _live_model_of(host) is None, "a harness with no model is no evidence"


# === the audit the issue asked for ===========================================
#
# Every OTHER ``_ctx``-derived value on ``SubagentHost``, in the same three
# states. ``cwd``, ``posture``, ``context_files``, ``model_registry`` and
# ``session`` are not in this list: the first reads a value that cannot move
# within a process and already falls back, and the rest were never
# ``_ctx``-derived (measured — ``.omc/specs/304-probe-ctx-audit.py``).


async def test_a_stale_context_does_not_widen_the_childs_tool_grant(
    tmp_path: Path,
) -> None:
    """THE SAME DEFECT, AND IT FAILED OPEN.

    ``SubagentHost.active_tools`` returning ``None`` means "no narrowing, every
    tool". On the branch base a stale ``_ctx`` raised, the ``except`` returned
    ``None``, and a parent launched with ``--tools read,bash`` who typed
    ``/new`` spawned its next child with the FULL set. The live
    ``ExtensionAPI`` — replaced by ``_invoke_factory`` on every harness rebuild,
    unlike ``_ctx`` — answers with the current harness's grant instead.
    """

    for state in ("none", "stale", "live"):
        ext = _extension(tmp_path, state)
        assert ext._host_active_tools() == PARENT_TOOLS, state
    # And specifically NOT the dead harness's wider grant.
    assert DEAD_TOOLS != PARENT_TOOLS


async def test_an_empty_live_grant_hands_the_child_no_tools_not_every_tool(
    tmp_path: Path,
) -> None:
    """``[]`` IS A GRANT AND WHAT IT GRANTS IS NOTHING — never fold it onto ``None``.

    The test above says the child gets the parent's grant; this one says that is
    still true when the grant is empty, because that is the single case where
    the obvious tidy-up reverses the fix. ``return list(...) or None``, or any
    ``if not tools: return None``, reads as harmless and is not: ``None`` on
    :attr:`SubagentHost.active_tools` means "the parent holds every built-in"
    (``print_channel.py:256-258``), so it would hand a parent running
    ``--no-tools`` a child running EVERY tool. That is the fail-open hole #304
    closed, re-opened from the other end.

    THIS IS A TIGHTENING AGAINST THE BRANCH BASE, and a deliberate one. There
    the ``_ctx=None`` and ``_ctx=STALE`` rows returned ``None`` unconditionally,
    so a ``--no-tools`` parent's first ``/agents run`` — or its next one after
    ``/new`` — spawned a fully-armed child. A LIVE ``_ctx`` already answered
    ``[]`` in exactly that state, so what changes is that the three states now
    agree on the value the live one always had.

    The second half of the chain, with the real production functions rather
    than a restatement of them: ``runtime._frozen_tools`` keeps ``[]`` as ``()``
    rather than collapsing it to ``None`` (it only maps ``None`` → ``None``),
    and ``narrow_tools`` intersects an INHERITING profile's request with it to
    ``()``. ``()`` is the value ``profile_to_argv`` renders as ``--no-tools``,
    pinned at ``test_child_argv_contract.py::
    test_an_empty_intersection_emits_no_tools_never_an_empty_string``.
    """

    inheriting = _resolved(tmp_path).profile
    assert inheriting.tools is None, "the profile asks for the ambient set"

    for state in ("none", "stale", "live"):
        grant = _extension(tmp_path, state, live_tools=NO_TOOLS)._host_active_tools()
        assert grant == [], state
        assert narrow_tools(inheriting, _frozen_tools(grant)).profile.tools == (), state


async def test_a_stale_context_does_not_revoke_project_trust(tmp_path: Path) -> None:
    """``/agents run`` right after ``/new`` in a TRUSTED project kept its
    ``.aelix/agents`` tier.

    ``ctx.is_project_trusted()`` raises on a stale context and the ``except``
    read that as "untrusted", so the project profiles vanished from the roster
    and ``resolve_profile`` refused them until the next turn. A stale context is
    no context; the value ``cli/entry.py``'s trust gate passed in stands.
    """

    for state in ("none", "stale", "live"):
        assert _extension(tmp_path, state)._host_project_trusted() is True, state


async def test_a_live_context_that_denies_trust_is_still_obeyed(
    tmp_path: Path,
) -> None:
    """The fallback is for a context that cannot answer, not one that says no."""

    ext = _extension(tmp_path, "none")
    runtime = _runtime_with_tools(PARENT_TOOLS)
    ext._ctx = _context(
        runtime,
        tmp_path / "project",
        model=PARENT_MODEL,
        tools=PARENT_TOOLS,
        project_trusted=False,
    )
    assert ext.project_trusted is True, "the wired fallback would have said yes"
    assert ext._host_project_trusted() is False


async def test_has_ui_errs_toward_false_instead_of_raising(tmp_path: Path) -> None:
    """``getattr(ctx, "has_ui", False)`` catches ``AttributeError`` and nothing
    else, so on a stale context this RAISED rather than answering ``False`` as
    its own docstring promised.

    ``batch._member`` catches it two frames up with a blanket
    ``except BaseException``, which turned the documented read-only clamp into
    ``delegation failed to start: ExtensionError`` for that member.
    """

    for state in ("none", "stale"):
        assert _extension(tmp_path, state)._host_has_ui() is False, state


async def test_has_ui_is_true_when_the_live_context_has_a_ui_bound(
    tmp_path: Path,
) -> None:
    """THE POSITIVE ROW — the only state of the three that may answer ``True``.

    Its two negatives above are both reached by ``getattr``'s default, which
    answers ``False`` for a getter that is merely broken just as quietly as for
    one that is correctly saying "no UI". So the negatives alone cannot tell the
    two apart, and ``has_ui`` is the ONE input deciding what
    ``approval_mode: "ask"`` clamps to (``posture.py:201-204``): stuck on
    ``False`` it costs a human the dialog they were owed and pins their batch to
    ``PLAN``, with nothing red anywhere to say so.

    ``ExtensionContext.has_ui`` is ``runtime.ui is not HEADLESS_UI_CONTEXT``
    (``extensions/api.py:1224-1225``) — an IDENTITY check against the headless
    singleton, so any concrete binding is the real signal and a stand-in is a
    faithful one. ``tui/shell.py`` installs the real ``AelixTUIContext`` at
    run-start and re-binds it onto the new runtime after every ``/new``,
    ``/resume``, ``/fork`` and ``/reload`` (``shell.py:2632-2634``) — which is
    why this is time-varying and why it is a getter.
    """

    ext = _extension(tmp_path, "live")
    assert ext._host_has_ui() is False, "a live context is headless until bound"

    api = ext._api
    assert api is not None
    ui: Any = object()  # not HEADLESS_UI_CONTEXT — that is the whole predicate
    api.runtime.bind_ui(ui)
    assert ext._host_has_ui() is True


async def test_the_consent_context_is_deliberately_left_on_the_old_path(
    tmp_path: Path,
) -> None:
    """The one ``_ctx``-derived value #304 does NOT late-bind, pinned so the
    omission reads as a decision rather than an oversight.

    It decides whether a HUMAN IS ASKED. #199 settled that a stale context is no
    context here, which takes the clamp, never prompts and never widens; giving
    it the live UI handle would turn that documented pre-hook state into a
    dialog, and that is a change to an authority surface with its own ADR to
    write. See ``AgentsExtension._host_consent_context``.
    """

    assert _extension(tmp_path, "stale")._host_consent_context() is None
    assert _extension(tmp_path, "none")._host_consent_context() is None
    assert _extension(tmp_path, "live")._host_consent_context() is not None
