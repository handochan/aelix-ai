"""The consent WIRING — hook → grant dict → ``execute()`` — ADR-0197 §(i).

``test_spawn_consent.py`` pins the DECISION (which options are offered, what
each answer means, and that there is no session memo for anything to be held
in). This file pins the TRANSPORT, which is where a decision can be lost,
forged or bypassed:

* the grant travels in a private dict keyed by ``tool_call_id``, never through
  ``event.args`` — even though ``harness/core.py:3723-3725`` explicitly permits
  a handler to mutate them, because that would put an unvalidated key into the
  transcript;
* :meth:`dict.pop` takes a ``None`` DEFAULT, and ``None`` FAILS CLOSED. A call
  that reached ``execute()`` without going through the hook skipped the only
  gate there is;
* what the human saw is what runs. ``event.args`` is the same mutable dict the
  tool receives, so the approved profile, task and directory are carried in the
  grant record rather than re-read at execution time.

The scaffolding is imported from ``test_tool_and_security`` so both files drive
one bench: a real :class:`_ExtensionRuntime` / :class:`ExtensionContext` pair
whose ``has_ui`` is the genuine ``runtime.ui is not HEADLESS_UI_CONTEXT``
(finding OC-7), with the process boundary — and only the process boundary —
replaced by a recording channel.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import SessionShutdownHookEvent, ToolCallHookEvent
from aelix_agents.consent import CANCEL_OPTION, build_options
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.builtin.permission_mode import PermissionMode

from tests.agents_ext.test_tool_and_security import (
    AGENT_TOOL_NAME,
    _Bench,
    _bench,
    _call,
    _text,
    _write_profile,
)

_WIDEN = build_options(PermissionMode.PLAN, may_widen=True)[1]
_BASELINE_PLAN = build_options(PermissionMode.PLAN, may_widen=True)[0]


def _ask_profile(tmp_path: Path, name: str = "scout") -> None:
    """A profile whose author asked for a human decision (OC-8).

    ``approval_mode: ask`` is the one value that opens the dialog regardless of
    the clamp, which is what makes the DEFAULT-parent case — the common one —
    reachable in a test without pretending the user pressed shift+tab.
    """

    _write_profile(
        tmp_path / "agent" / "agents" / f"{name}.md", name, extra="approval_mode: ask"
    )


async def _hook(bench: _Bench, args: dict[str, Any], *, tool_call_id: str) -> Any:
    event = ToolCallHookEvent(
        tool_call_id=tool_call_id, tool_name=AGENT_TOOL_NAME, args=dict(args)
    )
    return await bench.hook(event)


# === the grant reaches execute() ==============================================


async def test_the_baseline_answer_grants_the_clamp(tmp_path: Path) -> None:
    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is False
    assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN


async def test_the_widening_answer_reaches_the_child(tmp_path: Path) -> None:
    """The ONE path that raises authority above the clamp, end to end.

    The dialog may lift a spawn to at most ``auto-accept-edits``, and that
    decision has to survive all the way into ``SpawnPlan.permission_mode`` —
    which is the posture the child is LAUNCHED with, and therefore the only
    thing that actually bounds it (``builtin/permission.py``'s headless floor
    cannot, finding B4).
    """

    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_WIDEN,))

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is False
    assert bench.channel.plans[0].permission_mode is PermissionMode.AUTO_ACCEPT


async def test_a_read_only_project_spawn_runs_without_a_dialog(
    tmp_path: Path,
) -> None:
    """THE POLICY CHANGE, on the door that can actually reach project scope.

    REWRITTEN FROM ``test_the_widening_answer_is_never_offered_at_project_scope``
    (owner decision, 2026-07-27). That test asserted *"a project profile with
    approval_mode: ask must prompt"* and then checked the widening option was
    absent from the rendered list — i.e. it pinned the two-option modal that had
    no real answer. Under a DEFAULT parent this spawn clamps to ``plan`` and a
    project profile can never be widened, so there is nothing to decide and no
    dialog is shown.

    The B4 property is asserted where it now lives — on ``SpawnPlan``, the
    posture the child is LAUNCHED with. "Never offered" has become "never
    reachable", which is strictly stronger than the option-list check it
    replaces: a modal that is not shown cannot be answered wrongly.

    The human gate this does NOT remove: ``/agents run`` reaches a project
    identity only through ``tui/commands.py``'s
    ``_confirm_project_agent_for_run`` → ``resolve_profile(allow_project=True)``
    sequence, and the model door hardcodes ``allow_project=False`` at both of
    its sites. See ``tests/tui/test_agents_run_command.py`` for that gate.
    """

    _write_profile(
        tmp_path / "project" / ".aelix" / "agents" / "helper.md",
        "helper",
        extra="approval_mode: ask",
    )
    bench = _bench(tmp_path, project_trusted=True, has_ui=True, answers=(CANCEL_OPTION,))
    runtime = bench.ext.runtime
    assert runtime is not None

    resolved = runtime.resolve_profile("helper", allow_project=True)
    # Prime the extension with a live context the way any hook would.
    await _hook(bench, {"profile": "nope", "task": "t"}, tool_call_id="prime")
    result = await runtime.spawn(resolved, "do a thing")

    assert bench.ui.calls == []
    assert result.status != "declined"
    plan = bench.channel.plans[0]
    assert plan.permission_mode is PermissionMode.PLAN
    assert plan.resolved.scope == "project"


async def test_the_widening_answer_is_never_offered_at_project_scope(
    tmp_path: Path,
) -> None:
    """Belt to the model door's braces, on the parent posture that still asks.

    The model door refuses a project-scoped identity outright (finding B5), so
    this is asserted on the runtime's own door — a project profile reaching the
    dialog by ANY route still gets no widening option.

    A LOOSE PARENT is now what makes the dialog fire here: the clamp is
    ``auto-accept-edits``, which grants write authority, so the human is asked
    whether to run a child that can write. What must be absent is the option
    that would raise it further, and — because a project profile is already at
    the ceiling — the whole widening rung.
    """

    _write_profile(
        tmp_path / "project" / ".aelix" / "agents" / "helper.md",
        "helper",
        extra="approval_mode: ask",
    )
    bench = _bench(
        tmp_path,
        project_trusted=True,
        has_ui=True,
        posture=PermissionMode.AUTO_ACCEPT,
        answers=(CANCEL_OPTION,),
    )
    runtime = bench.ext.runtime
    assert runtime is not None

    resolved = runtime.resolve_profile("helper", allow_project=True)
    # Prime the extension with a live context the way any hook would.
    await _hook(bench, {"profile": "nope", "task": "t"}, tool_call_id="prime")
    result = await runtime.spawn(resolved, "do a thing")

    assert bench.ui.calls, "a write-capable project spawn must still prompt"
    options = bench.ui.calls[-1][1]
    assert _WIDEN not in options
    assert len(options) == 2
    # Cancel was the answer, so nothing started.
    assert result.status == "declined"
    assert bench.channel.plans == []


@pytest.mark.parametrize("answer", [CANCEL_OPTION, None, "something else entirely"])
async def test_a_declined_dialog_blocks_and_starts_nothing(
    tmp_path: Path, answer: object
) -> None:
    """Esc, Cancel and a nonsense answer are all the same decision: no.

    A ``None`` answer is Esc (``tui/context.py:255-262``). Anything that is not
    a rendered option is treated identically, because the alternative is
    inferring consent from a string nobody was shown.

    THIS TEST USED TO ASSERT THE OPPOSITE for the nonsense case (P2 review,
    MEDIUM #1). It accepted a SPAWN at the clamp —
    ``assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN`` —
    which contradicted both its own name and the docstring above it, and pinned
    a consent gate that failed OPEN. Measured through this same wiring before
    the fix, with a parent at ``yolo``: ``is_error=False``, one child spawned,
    ``child_mode=yolo``. ``consent.py`` now ALLOW-LISTS the rendered options, so
    all three answers below take one identical path.
    """

    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(answer,))

    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is True
    assert "declined" in _text(result)
    assert bench.channel.plans == []


async def test_a_raising_dialog_blocks_rather_than_allows(tmp_path: Path) -> None:
    """DENY ON ERROR is the whole point of the ``_ask`` wrapper.

    ``select`` can raise ``NotImplementedError`` — the headless binding, which
    is genuinely reachable if the UI is re-bound between the ``has_ui`` check
    and the call. A refusal that became an ALLOW because a dialog threw would be
    the single worst failure this feature could have.
    """

    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True)

    async def _boom(title: str, options: list[str], opts: object = None) -> str:
        raise NotImplementedError("headless binding")

    bench.ui.select = _boom  # type: ignore[method-assign, assignment]
    result = await _call(bench, {"profile": "scout", "task": "go"})
    assert result.is_error is True
    assert bench.channel.plans == []


async def test_a_read_only_model_delegation_runs_without_a_dialog(
    tmp_path: Path,
) -> None:
    """The MODEL door's read-only fast path, with a LIVE UI.

    ``AgentsExtension._grant_for`` skips ``request_spawn_consent`` when the clamp
    grants no write authority and the profile declared no need for it — the
    out-of-the-box case for a ``default`` parent (a profile with no
    ``approval_mode:`` line at all), and the case
    ``MAX_DELEGATIONS_PER_PROMPT`` exists to bound. The condition has been
    respelled twice and the test is unchanged through both, which is the point:
    ``want is PermissionMode.PLAN`` → ``not grants_write_authority(want) and
    approval_mode != "ask"`` → ``not consent_is_required(resolved, want, ...)``,
    the predicate ``consent.py``'s own early-out now calls.

    Driven with ``has_ui=True`` deliberately: with no UI nothing prompts anyway,
    so a headless run could not tell the fast path from the dialog path.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    result = await _call(bench, {"profile": "scout", "task": "go"})

    assert result.is_error is False
    assert bench.ui.calls == []
    assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN


async def test_a_declaring_profile_prompts_on_the_model_door_too(
    tmp_path: Path,
) -> None:
    """THE SECOND SITE, discriminated against the test above it.

    Same door, same ``default`` parent, same read-only clamp, same live UI. The
    only difference is ``approval_mode: auto`` in the profile — a declaration
    that it needs write authority — and that is enough to open the dialog, offer
    the bounded widening, and let one human answer lift the child to
    ``auto-accept-edits``.

    THIS IS A BEHAVIOUR CHANGE ON THIS DOOR, and it is the intended one. The old
    pre-filter (``not grants_write_authority(want) and approval_mode != "ask"``)
    swallowed exactly this cell: a profile could declare ``auto``, get clamped to
    ``plan`` by finding B4's rule, and never be mentioned to the human at all.
    The model door and ``request_spawn_consent`` disagreed about it — one asked,
    one did not — which is what ``consent_is_required`` now makes impossible.
    """

    _write_profile(
        tmp_path / "agent" / "agents" / "writer.md", "writer", extra="approval_mode: auto"
    )
    bench = _bench(tmp_path, has_ui=True, answers=(_WIDEN,))

    result = await _call(bench, {"profile": "writer", "task": "go"})

    assert result.is_error is False
    assert len(bench.ui.calls) == 1
    assert _WIDEN in bench.ui.calls[0][1]
    assert bench.channel.plans[0].permission_mode is PermissionMode.AUTO_ACCEPT


async def test_a_deny_profile_is_never_offered_a_widening_on_the_model_door(
    tmp_path: Path,
) -> None:
    """``deny`` on the door where the MODEL chose the profile.

    The complement of the two tests above, and the one that must never regress:
    a profile whose author wrote ``approval_mode: deny`` gets no dialog and no
    option to raise it, even under a ``yolo`` parent where every other profile
    inherits real authority. The child is launched at ``plan``.
    """

    _write_profile(
        tmp_path / "agent" / "agents" / "reader.md", "reader", extra="approval_mode: deny"
    )
    bench = _bench(tmp_path, posture=PermissionMode.YOLO, has_ui=True)

    result = await _call(bench, {"profile": "reader", "task": "go"})

    assert result.is_error is False
    assert bench.ui.calls == []
    assert bench.channel.plans[0].permission_mode is PermissionMode.PLAN


# === headless ================================================================


async def test_headless_grants_the_clamp_and_never_blocks(tmp_path: Path) -> None:
    """FINDING OC-8 — no UI means a silent DOWNGRADE, never a refusal.

    A write-capable want under a loose parent still spawns headlessly; it just
    spawns at the clamp, with nobody asked and nothing widened. This is what
    keeps every existing ``-p`` / ``--mode json`` / RPC user working.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, posture=PermissionMode.AUTO)

    blocked = await _hook(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-h")
    assert blocked is None
    assert bench.ui.calls == []

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "task": "go"}, ToolExecutionContext(tool_call_id="tc-h")
    )
    assert result.is_error is False
    assert bench.channel.plans[0].permission_mode is PermissionMode.AUTO_ACCEPT


# === the anti-bypass invariant ================================================


async def test_execute_without_a_grant_is_refused_and_starts_nothing(
    tmp_path: Path,
) -> None:
    """THE FAIL-CLOSED PIN. ``pop(tool_call_id, None)`` and ``None`` means no.

    Calling ``execute()`` directly is exactly what a caller that skipped the
    hook looks like. It must be a refusal, not a spawn — and the refusal must be
    a returned ``is_error`` result, because raising here would abort the
    parent's whole turn.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, posture=PermissionMode.YOLO, has_ui=True)

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "task": "go"},
        ToolExecutionContext(tool_call_id="never-armed"),
    )
    assert result.is_error is True
    assert "not approved" in _text(result)
    assert bench.channel.plans == []
    assert bench.ui.calls == []


async def test_a_grant_is_spent_exactly_once(tmp_path: Path) -> None:
    """A replayed ``tool_call_id`` finds nothing — one approval, one spawn."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    await _hook(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-once")
    execute = bench.tool.execute
    assert execute is not None
    first = await execute(
        {"profile": "scout", "task": "go"}, ToolExecutionContext(tool_call_id="tc-once")
    )
    second = await execute(
        {"profile": "scout", "task": "go"}, ToolExecutionContext(tool_call_id="tc-once")
    )
    assert first.is_error is False
    assert second.is_error is True
    assert len(bench.channel.plans) == 1


async def test_the_grant_never_rides_in_the_tool_arguments(tmp_path: Path) -> None:
    """``event.args`` is the transcript. Nothing this gate decides belongs there."""

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    args = {"profile": "scout", "task": "go"}
    event = ToolCallHookEvent(
        tool_call_id="tc-args", tool_name=AGENT_TOOL_NAME, args=args
    )
    await bench.hook(event)
    assert event.args == {"profile": "scout", "task": "go"}


async def test_what_the_human_approved_is_what_runs(tmp_path: Path) -> None:
    """A LATER handler may mutate ``args``; the approved values must survive.

    ``harness/core.py:3723-3725`` explicitly permits it and the dict the tool
    receives is the same object. The dialog showed a profile, a task and a
    directory; re-reading them at execution time would let anything that ran in
    between substitute different ones.
    """

    _ask_profile(tmp_path)
    _write_profile(tmp_path / "agent" / "agents" / "other.md", "other")
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))

    args: dict[str, Any] = {"profile": "scout", "task": "read the README"}
    event = ToolCallHookEvent(
        tool_call_id="tc-swap", tool_name=AGENT_TOOL_NAME, args=args
    )
    assert await bench.hook(event) is None

    # Whatever ran between the hook and execute() rewrote the call.
    args["profile"] = "other"
    args["task"] = "rm -rf everything"

    execute = bench.tool.execute
    assert execute is not None
    await execute(args, ToolExecutionContext(tool_call_id="tc-swap"))

    plan = bench.channel.plans[0]
    assert plan.resolved.name == "scout"
    assert plan.task == "read the README"
    assert "read the README" in bench.ui.calls[0][0]


async def test_a_mismatched_grant_is_refused(tmp_path: Path) -> None:
    """Defence in depth: the grant names the identity it approved.

    If a grant and a resolved profile were ever paired by anything other than
    the hook that produced both, one human decision could authorise a different
    file. Cheap to check, and the check is the difference between a bug and a
    vulnerability.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)

    await _hook(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-mismatch")
    pending = bench.ext._pending["tc-mismatch"]
    import dataclasses

    bench.ext._pending["tc-mismatch"] = dataclasses.replace(
        pending, grant=dataclasses.replace(pending.grant, source_path="/elsewhere.md")
    )

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "task": "go"},
        ToolExecutionContext(tool_call_id="tc-mismatch"),
    )
    assert result.is_error is True
    assert bench.channel.plans == []


# === serialisation ============================================================


async def test_two_concurrent_hook_invocations_serialize(tmp_path: Path) -> None:
    """Two modals must never be open at once.

    ``tui/chrome.py:518``'s ``_modal`` is a SINGLE slot: ``mount_modal``
    overwrites unconditionally, the first Future is orphaned, and the turn hangs
    until Ctrl+C. Three independent defences cover it — the kernel's own
    sequential prep phase, the tool's ``execution_mode="sequential"``, and
    ``consent.py``'s process-wide lock. This exercises the third, because it is
    the only one a direct caller cannot skip.
    """

    _ask_profile(tmp_path)
    bench = _bench(
        tmp_path, has_ui=True, answers=(_BASELINE_PLAN, _BASELINE_PLAN)
    )

    inside = 0
    overlapped = False
    original = bench.ui.select

    async def _slow(title: str, options: list[str], opts: object = None) -> object:
        nonlocal inside, overlapped
        inside += 1
        if inside > 1:
            overlapped = True
        try:
            await asyncio.sleep(0.02)
            return await original(title, options, opts)
        finally:
            inside -= 1

    bench.ui.select = _slow  # type: ignore[method-assign, assignment]
    await asyncio.gather(
        _hook(bench, {"profile": "scout", "task": "a"}, tool_call_id="tc-a"),
        _hook(bench, {"profile": "scout", "task": "b"}, tool_call_id="tc-b"),
    )
    assert overlapped is False
    assert len(bench.ui.calls) == 2
    assert set(bench.ext._pending) == {"tc-a", "tc-b"}


# === session scope ============================================================


async def test_session_shutdown_clears_every_grant(tmp_path: Path) -> None:
    """A grant may not outlive the session that produced it.

    ``session_shutdown`` fires on ``/new`` / ``/resume`` / ``/fork`` as well as
    ``quit``, and the extension instance is threaded across all of them by held
    reference — so without this a decision made in one session would still be
    sitting in the dict in the next.
    """

    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))

    await _hook(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-live")
    assert bench.ext._pending

    handler = bench.extension.handlers["session_shutdown"][0]
    await handler(SessionShutdownHookEvent(reason="new"), bench.ctx)

    assert bench.ext._pending == {}
    # There is nothing ELSE to clear: P2 keeps no session memo, so a grant
    # cannot outlive the single ``execute()`` it was taken for (P2 review,
    # HIGH #2/#5; ADR-0197:613-616).
    assert not hasattr(bench.ext, "memo")


async def test_every_model_driven_delegation_asks_again(tmp_path: Path) -> None:
    """NO STANDING GRANT, end to end through the tool (P2 review, HIGH #2/#5).

    This is the door that matters: the MODEL chose the profile, the task and the
    directory. The removed session rung was wired ONLY here — ``/agents run``
    passed no memo — so the model-chosen delegation asked less often than the
    user-typed one, inverted relative to risk (finding OC-1). Measured on the
    removed implementation with a parent at ``auto-accept-edits``: one dialog,
    four children, three of them in model-chosen directories.

    Three delegations, three dialogs, and the third can still say no.
    """

    _ask_profile(tmp_path)
    bench = _bench(
        tmp_path,
        has_ui=True,
        answers=(_BASELINE_PLAN, _BASELINE_PLAN, CANCEL_OPTION),
    )

    first = await _call(bench, {"profile": "scout", "task": "a"}, tool_call_id="tc-1")
    second = await _call(bench, {"profile": "scout", "task": "b"}, tool_call_id="tc-2")
    third = await _call(bench, {"profile": "scout", "task": "c"}, tool_call_id="tc-3")

    assert first.is_error is False
    assert second.is_error is False
    assert third.is_error is True
    assert len(bench.ui.calls) == 3
    # Every dialog carried its OWN task text — the thing a source-path-keyed
    # memo could never distinguish.
    assert "a" in bench.ui.calls[0][0]
    assert "b" in bench.ui.calls[1][0]
    assert "c" in bench.ui.calls[2][0]


async def test_a_loose_parent_still_asks_on_every_delegation(tmp_path: Path) -> None:
    """The shift+tab scenario finding OC-1 names, pinned on the live wiring.

    A user who pressed shift+tab once for their OWN convenience must not thereby
    hand every future model-chosen delegation an unattended ``auto-accept-edits``
    child. The posture object is SHARED with ``PermissionExtension`` by held
    reference, so setting it here is the real mechanism and not a stand-in.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, posture=PermissionMode.AUTO_ACCEPT, has_ui=True)
    bench.ui._answers.extend(  # type: ignore[attr-defined]
        ["Run with the inherited posture (auto-accept-edits)"] * 2
    )

    await _call(bench, {"profile": "scout", "task": "a"}, tool_call_id="tc-1")
    await _call(bench, {"profile": "scout", "task": "b"}, tool_call_id="tc-2")

    assert len(bench.ui.calls) == 2
    assert bench.channel.plans[0].permission_mode is PermissionMode.AUTO_ACCEPT
    assert bench.channel.plans[1].permission_mode is PermissionMode.AUTO_ACCEPT


async def test_the_dialog_shows_the_contained_cwd_not_the_requested_one(
    tmp_path: Path,
) -> None:
    """Containment happens BEFORE the human is asked.

    The directory on screen has to be the one the child will actually get, and
    an out-of-tree request has to be refused without ever rendering a dialog —
    otherwise the modal would be teaching the user to approve a path that was
    about to be silently changed.
    """

    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))
    (bench.cwd / "sub").mkdir()

    await _call(bench, {"profile": "scout", "task": "go", "cwd": "sub"})
    assert str((bench.cwd / "sub").resolve()) in bench.ui.calls[0][0]
