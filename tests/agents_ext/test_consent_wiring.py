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

ADR-0199 (P3) makes every one of those a BATCH property as well, and the second
half of this file is that: ONE grant and ONE identity re-check for a call that
starts up to eight children, an ``args`` dict whose ``mode`` and ``tasks`` a
later handler may rewrite without changing which topology runs, a per-prompt
budget that refuses the whole CALL rather than half of it, and the group whose
open/close pair is what gives all three S10 surfaces their grouping without a
correlation field in product-core (§3.6).

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
from aelix_agents.panel import PANEL_WIDGET_KEY
from aelix_agents.progress import group_status_key, status_key
from aelix_agents.runtime import MAX_DELEGATIONS_PER_PROMPT
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.builtin.permission_mode import PermissionMode

from tests.agents_ext.test_tool_and_security import (
    AGENT_TOOL_NAME,
    _Bench,
    _bench,
    _call,
    _RecordingChannel,
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

    ASSERTED ON THE TAIL, and that is the contract rather than a weakening. The
    ``Directory:`` row elides its MIDDLE at ``DIALOG_FIELD_CHARS`` and
    guarantees the tail (``consent.py:386-399``: "WHERE inside is the question
    this row exists to answer, and it is answered by the TAIL"). Asserting the
    whole absolute path made this test pass or fail on how long
    ``/tmp/pytest-of-<user>/pytest-<N>/...`` happened to be on the day — it goes
    red once the counter reaches three digits, which is a property of the
    machine and not of the code.
    """

    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))
    (bench.cwd / "sub").mkdir()

    await _call(bench, {"profile": "scout", "task": "go", "cwd": "sub"})

    contained = str((bench.cwd / "sub").resolve())
    title = bench.ui.calls[0][0]
    shown = f"{bench.cwd.name}/sub"
    # The parent component proves the RESOLUTION happened: the model asked for
    # the relative ``"sub"`` and the human is shown where that landed.
    assert contained.endswith(shown)
    assert shown in title
    # Nothing was dropped SILENTLY: either the whole path is there, or the
    # elision marker says out loud that it is not.
    assert contained in title or "…" in title


# === P3: the batch travels the same wire ======================================
#
# Everything below is ADR-0199. The single-task tests above are the control
# group: not one of them changes, which is the claim that the fan-out door was
# BUILT ON the P2 gate rather than beside it.


class _CancellingChannel(_RecordingChannel):
    """A channel whose children die of an EXTERNAL cancellation.

    ``batch._member`` converts every ``BaseException`` into an envelope except
    ``CancelledError``, which it re-raises (§3.5.1) — so this is the one shape
    that can make ``run_batch`` raise out of ``_execute`` at all, and therefore
    the only way to exercise the failure path of the group's ``finally``.
    """

    async def run(self, plan: Any, *, child: Any = None, on_stream: Any = None) -> Any:
        self.plans.append(plan)
        raise asyncio.CancelledError


def _tall_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``consent._terminal_rows`` so the §3.7 height budget is deterministic.

    ``shutil.get_terminal_size`` reads ``$LINES`` before it asks the OS, so this
    is the supported spelling rather than a patch of the function. 40 rows is a
    normal terminal, where the fit check admits eight members with room to spare
    — the height REFUSAL is ``test_batch_consent.py``'s subject, and a test of
    the wiring that flaked on the height of the CI terminal would be measuring
    the wrong thing.
    """

    monkeypatch.setenv("LINES", "40")
    monkeypatch.setenv("COLUMNS", "120")


def _record_ui(bench: _Bench) -> tuple[
    list[tuple[str, str | None]], list[tuple[str, list[str] | None]]
]:
    """Record the ORDER of statusline and widget writes, not just the last one.

    ``_FakeUI`` keeps a ``dict`` of the current row text, which cannot answer
    "was this key written twice" or "was it cleared afterwards" — the two
    questions the group's open/close pair is about. ``set_widget`` is added
    here rather than to the shared bench because ``test_tool_and_security``
    owns that file exclusively (§5.9).
    """

    writes: list[tuple[str, str | None]] = []
    widgets: list[tuple[str, list[str] | None]] = []
    original = bench.ui.set_status

    def _set_status(key: str, text: str | None) -> None:
        writes.append((key, text))
        original(key, text)

    def _set_widget(key: str, content: list[str] | None, options: Any = None) -> None:
        widgets.append((key, content))

    bench.ui.set_status = _set_status  # type: ignore[method-assign]
    bench.ui.set_widget = _set_widget  # type: ignore[attr-defined]
    return writes, widgets


# === one call, one grant ======================================================


async def test_a_batch_takes_exactly_one_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DECISION S4, on the wire: N children, ONE dialog, ONE human answer.

    The count is the assertion that matters. A hook that took a grant per task
    would still spawn four children and still return a clean aggregate — and it
    would have asked the user four times for one tool call, which is both the
    modal-collision hazard ``tool.py``'s docstring enumerates and the "train them
    to click through" failure the pre-filter exists to avoid.

    Every task is on screen in that one dialog, because a grant covering text the
    human never saw is not consent (S4, §3.7's content assertion).
    """

    _tall_terminal(monkeypatch)
    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))

    result = await _call(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": ["count the files", "read the README", "list the tests"],
        },
    )

    assert result.is_error is False
    assert len(bench.ui.calls) == 1
    title = bench.ui.calls[0][0]
    for task in ("count the files", "read the README", "list the tests"):
        assert task in title
    assert [plan.task for plan in bench.channel.plans] == [
        "count the files",
        "read the README",
        "list the tests",
    ]
    # ONE grant, spent on every member: one mode, one profile, one source path.
    assert {plan.permission_mode for plan in bench.channel.plans} == {
        PermissionMode.PLAN
    }
    assert {plan.resolved.name for plan in bench.channel.plans} == {"scout"}


async def test_declining_a_batch_starts_no_member_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One "no" refuses the CALL, never a prefix of it.

    The refusal is taken in the hook, before ``PendingSpawn`` exists, so there is
    nothing for ``execute()`` to find and nothing partially started to unwind.
    """

    _tall_terminal(monkeypatch)
    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(CANCEL_OPTION,))

    result = await _call(
        bench, {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]}
    )

    assert result.is_error is True
    assert "declined" in _text(result)
    assert bench.channel.plans == []
    assert bench.ext._pending == {}


async def test_a_batch_refused_for_height_reads_as_split_it_not_as_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SpawnGrant.reason`` must survive the hook, and it must WIN over ``_DECLINED``.

    ``_DECLINED`` says "do not retry it", which is true of a human answer and
    false of a dialog that was never shown (``consent.py:256-270``). A model told
    "the user declined" when the user was never asked stops delegating for the
    rest of the prompt; a model told the terminal is too short splits the call
    and gets its work done.
    """

    monkeypatch.setenv("LINES", "24")
    monkeypatch.setenv("COLUMNS", "80")
    _ask_profile(tmp_path)
    bench = _bench(tmp_path, has_ui=True, answers=(_BASELINE_PLAN,))

    result = await _call(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": [f"task {index}" for index in range(8)],
        },
    )

    assert result.is_error is True
    text = _text(result)
    assert "declined" not in text
    assert "agent" in text and "8" in text
    # No dialog was opened at all: there was nothing that could be drawn in full.
    assert bench.ui.calls == []
    assert bench.channel.plans == []
    assert bench.ext._pending == {}


async def test_a_read_only_batch_runs_with_no_dialog_at_all(tmp_path: Path) -> None:
    """THE COMMON CASE, and it costs nothing — no dialog, so no height either.

    A ``default`` parent clamps every child to ``plan``; a profile that declared
    nothing cannot be widened; so ``consent_is_required`` is False and an
    eight-way read-only fan-out never renders a modal. Asserted with a LIVE UI,
    because with no UI nothing would prompt anyway and the test could not tell
    the fast path from the dialog path.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    result = await _call(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": [f"task {index}" for index in range(8)],
        },
    )

    assert result.is_error is False
    assert bench.ui.calls == []
    assert len(bench.channel.plans) == 8


# === the anti-bypass invariant, at batch size =================================


async def test_a_batch_that_skipped_the_hook_starts_nothing(tmp_path: Path) -> None:
    """``pop(tool_call_id, None)`` FAILS CLOSED for eight children as for one.

    Calling ``execute()`` directly with a fully-formed ``mode="parallel"``
    argument dict is exactly what a caller that skipped the gate looks like, and
    it is the shape that would be worth the most to an attacker: one unguarded
    call, eight children.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, posture=PermissionMode.YOLO, has_ui=True)

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
        ToolExecutionContext(tool_call_id="never-armed"),
    )

    assert result.is_error is True
    assert "not approved" in _text(result)
    assert bench.channel.plans == []
    assert bench.ui.calls == []


async def test_a_profile_swapped_between_hook_and_execute_refuses_the_whole_batch(
    tmp_path: Path,
) -> None:
    """ONE identity re-check, and it covers all N — because a batch is ONE profile.

    The window is real: the hook and ``execute()`` are separated by the kernel's
    parallel execute phase and the profile search path is a directory the model's
    own tools can write. Deleting the file is the honest way to exercise it —
    ``resolve_profile`` raises, ``still_the_same`` is False, and ANY failure to
    re-establish the identity is a refusal.

    The assertion that earns the test is ``plans == []``: a re-check placed after
    the fan-out, or per member, would let members 1..k start before member k+1
    noticed.
    """

    profile = _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    await _hook(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
        tool_call_id="tc-swap-batch",
    )
    assert bench.ext._pending["tc-swap-batch"].call.mode == "parallel"
    profile.unlink()

    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
        ToolExecutionContext(tool_call_id="tc-swap-batch"),
    )

    assert result.is_error is True
    assert "do not match" in _text(result)
    assert bench.channel.plans == []


@pytest.mark.parametrize(
    ("approved", "mutation"),
    [
        pytest.param(
            {"profile": "scout", "mode": "parallel", "tasks": ["keep a", "keep b"]},
            {"mode": "single", "task": "rm -rf everything", "tasks": ["evil"]},
            id="batch-downgraded-to-single",
        ),
        pytest.param(
            {"profile": "scout", "task": "keep a"},
            {"mode": "parallel", "tasks": [f"evil {i}" for i in range(8)]},
            id="single-upgraded-to-batch",
        ),
    ],
)
async def test_a_later_handler_cannot_change_the_topology_that_runs(
    tmp_path: Path, approved: dict[str, Any], mutation: dict[str, Any]
) -> None:
    """THE ``args``-BY-REFERENCE HOLE, closed in both directions (§5, WP-6).

    ``harness/core.py:3722-3726`` states verbatim that the kernel passes
    ``ctx.args`` by reference with no defensive copy, precisely so a later
    ``tool_call`` handler may mutate the dict and have the mutation reach
    ``tool.execute``. ``AgentsExtension`` is APPENDED to the extension list
    (``entry.py:860-873``), so a handler registered after it is not hypothetical.

    An ``_execute`` that read ``args.get("mode", "single")`` would let that
    handler pick a different execution TOPOLOGY from the one the human approved —
    downgrading a consented batch to one child of its choosing, or upgrading a
    consented single call into an eight-way fan-out of tasks nobody saw. Both
    directions are asserted, because the two are different defects: one drops
    work the model believes it delegated, the other starts work no human ever
    approved.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    _write_profile(tmp_path / "agent" / "agents" / "other.md", "other")
    bench = _bench(tmp_path, has_ui=True)

    args: dict[str, Any] = dict(approved)
    event = ToolCallHookEvent(
        tool_call_id="tc-mutate", tool_name=AGENT_TOOL_NAME, args=args
    )
    assert await bench.hook(event) is None

    # Whatever ran between the hook and execute() rewrote the call — the SAME
    # dict object the tool is about to receive.
    args.update(mutation)
    args["profile"] = "other"

    execute = bench.tool.execute
    assert execute is not None
    await execute(args, ToolExecutionContext(tool_call_id="tc-mutate"))

    expected = approved.get("tasks") or [approved["task"]]
    assert [plan.task for plan in bench.channel.plans] == expected
    assert {plan.resolved.name for plan in bench.channel.plans} == {"scout"}


# === §3.5.2.1 the per-prompt budget refuses the CALL ==========================


async def test_a_batch_over_the_remaining_budget_is_blocked_at_the_hook(
    tmp_path: Path,
) -> None:
    """S6 meets S7: the budget refuses the whole CALL, never half of it.

    The budget is charged per CHILD, inside ``runtime._run`` — i.e. AFTER a
    dialog would already have shown the human all N. Without the hook-time check
    a second eight-task call in one prompt would start four children and return
    four budget-exhausted envelopes for the rest: a partial-success envelope set
    arriving after a human said yes to eight, which is the exact shape S5 made
    unreachable for the live-child cap.

    Three assertions, and each one is a different failure it excludes: no plan
    (nothing ran), no dialog (nobody was asked), no ``PendingSpawn`` (nothing is
    left armed for a later ``execute()`` to spend).
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    first = await _call(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": [f"first {index}" for index in range(8)],
        },
        tool_call_id="tc-budget-1",
    )
    assert first.is_error is False
    assert len(bench.channel.plans) == 8

    blocked = await _hook(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": [f"second {index}" for index in range(8)],
        },
        tool_call_id="tc-budget-2",
    )

    assert blocked is not None and blocked.block is True
    remaining = MAX_DELEGATIONS_PER_PROMPT - 8
    # BOTH numbers, because the model's only useful next action is a smaller
    # call and it cannot compute the size from "budget exhausted".
    assert "8" in blocked.reason and str(remaining) in blocked.reason
    assert len(bench.channel.plans) == 8
    assert bench.ui.calls == []
    assert "tc-budget-2" not in bench.ext._pending


async def test_a_batch_that_exactly_fits_the_remaining_budget_is_allowed(
    tmp_path: Path,
) -> None:
    """The boundary, in the direction that must NOT be refused.

    ``>`` and not ``>=``: a call for exactly the remaining budget is legal, and a
    check off by one here would refuse work the per-child charge would have
    admitted — silently making the advertised ceiling smaller than it says.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    await _call(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": [f"first {index}" for index in range(8)],
        },
        tool_call_id="tc-fit-1",
    )
    result = await _call(
        bench,
        {
            "profile": "scout",
            "mode": "parallel",
            "tasks": [
                f"second {index}"
                for index in range(MAX_DELEGATIONS_PER_PROMPT - 8)
            ],
        },
        tool_call_id="tc-fit-2",
    )

    assert result.is_error is False
    assert len(bench.channel.plans) == MAX_DELEGATIONS_PER_PROMPT


# === §3.6 the group: one open, one close, per call ============================


async def test_a_batch_writes_one_grouped_status_row_and_no_per_child_rows(
    tmp_path: Path,
) -> None:
    """THE END-TO-END PROOF ``test_batch_surfaces.py`` says it cannot give (§S10).

    That file drives ``begin_group`` / ``adopt`` / ``__call__`` by hand over
    synthetic snapshots. If the extension's per-call closure never called
    ``adopt`` — the one thing only this layer can get wrong — the bridge would
    write four ``subagent:<id>`` rows, ``chrome._render_status`` would
    ``"  ".join`` them into a height-1 row, and every assertion in that file
    would still pass.

    So: four real members, through the real runtime, and the only key the UI is
    ever asked to write is the group's.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)
    writes, widgets = _record_ui(bench)

    result = await _call(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
        tool_call_id="tc-group",
    )

    assert result.is_error is False
    keys = {key for key, _ in writes}
    assert keys == {group_status_key("tc-group")}
    for plan in bench.channel.plans:
        assert status_key(plan.id) not in keys
    assert {key for key, _ in widgets} == {PANEL_WIDGET_KEY}


async def test_the_group_is_opened_once_and_closed_once_per_call(
    tmp_path: Path,
) -> None:
    """Symmetry, asserted on the bridge's own table rather than on a row.

    An aggregate row or a widget panel that outlives its delegation is a lie the
    user cannot dismiss, and ``set_status`` has no "clear all" verb — so the
    close has to happen on the call's own path. The final write for the group key
    is ``None`` (the row cleared) and the final widget write is ``None`` (the
    panel blanked).
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)
    writes, widgets = _record_ui(bench)

    await _call(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
        tool_call_id="tc-close",
    )

    bridge = bench.ext._progress
    assert bridge is not None
    assert bridge._groups == {}
    assert bridge._members == {}
    assert writes[-1] == (group_status_key("tc-close"), None)
    assert widgets[-1] == (PANEL_WIDGET_KEY, None)


async def test_a_cancelled_batch_still_closes_its_group(tmp_path: Path) -> None:
    """THE FAILURE PATH, which is the one a ``finally`` exists for.

    ``run_batch`` re-raises ``CancelledError`` (§3.5.1) — it is the one exception
    a member does not turn into an envelope — so a turn aborted mid-batch unwinds
    straight through ``_execute``. Without the ``finally`` the group would stay
    open, its row would stay on the statusline, and the next batch under the same
    ``tool_call_id`` would be the only thing that could ever clear it.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True, channel=_CancellingChannel())
    writes, _ = _record_ui(bench)

    await _hook(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
        tool_call_id="tc-cancel",
    )
    execute = bench.tool.execute
    assert execute is not None
    with pytest.raises(asyncio.CancelledError):
        await execute(
            {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]},
            ToolExecutionContext(tool_call_id="tc-cancel"),
        )

    bridge = bench.ext._progress
    assert bridge is not None
    assert bridge._groups == {}
    assert writes[-1] == (group_status_key("tc-cancel"), None)


async def test_a_single_delegation_keeps_its_own_per_child_row(
    tmp_path: Path,
) -> None:
    """S10's floor: at N == 1 every surface stays byte-identical to P2.

    NO GROUP IS OPENED AT ALL below ``PANEL_MIN_CHILDREN``, and that is stricter
    than "the group is inactive". An inactive group renders nothing, but
    ``end_group`` clears the aggregate row unconditionally
    (``progress.py:341``) — one ``set_status(subagent:group:<id>, None)`` for a
    row that was never written, which is a UI write P2 never made. The shipped
    ``test_events_and_statusline.py::test_statusline_row_set_and_cleared``
    counts the keys the UI is asked for and would go red on it.

    So: exactly one key, the child's own, and no widget.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)
    writes, widgets = _record_ui(bench)

    await _call(bench, {"profile": "scout", "task": "go"}, tool_call_id="tc-solo")

    plan = bench.channel.plans[0]
    assert {key for key, _ in writes} == {status_key(plan.id)}
    assert widgets == []
    bridge = bench.ext._progress
    assert bridge is not None
    assert bridge._groups == {}


async def test_the_tool_card_carries_every_member_in_submitted_order(
    tmp_path: Path,
) -> None:
    """Surface 2, end to end — the INDEX is what makes it possible.

    The index is bound at member creation by the executor and arrives with every
    snapshot, which is why no ``batch_id`` field was added to
    ``SubagentProgress`` (§3.6). ``spawn_id`` is minted inside ``runtime._run``
    and for a later wave does not exist for minutes, so a card that tried to
    infer position from the id could not render a queued member at all.
    """

    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, has_ui=True)

    frames: list[str] = []

    def _on_partial(partial: ToolResult) -> None:
        frames.append(_text(partial))

    await _hook(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c"]},
        tool_call_id="tc-card",
    )
    execute = bench.tool.execute
    assert execute is not None
    result = await execute(
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c"]},
        ToolExecutionContext(tool_call_id="tc-card", on_partial=_on_partial),
    )

    assert frames, "a batch must publish a tool card"
    assert all(frame.count("\n") == 2 for frame in frames), (
        "every frame renders all three members, queued ones included — a card "
        "that shows 2 of 3 rows reads as 'one task was dropped'"
    )
    assert frames[-1].startswith("[1/3] ")
    # And the aggregate the model reads keeps the same submitted order — never
    # completion order (§3.4), because the model addressed the tasks by position.
    assert result.is_error is False
    text = _text(result)
    assert text.index("[1/3 ") < text.index("[2/3 ") < text.index("[3/3 ")


async def test_the_model_door_dialog_names_the_child_s_model(tmp_path: Path) -> None:
    """THE DOOR THE MODEL CHOSE THE PROFILE ON, so the one where being told what
    will answer matters most.

    ``AgentsExtension._grant_for`` resolves the child's model the same way the
    spawner resolves its argv (``resolver.child_model_id``) and hands it to the
    dialog. Driven end to end through the ``tool_call`` hook: the profile on disk
    declares ``model:``, and that string is on screen before the human answers.
    """

    _write_profile(
        tmp_path / "agent" / "agents" / "writer.md",
        "writer",
        extra="approval_mode: auto\nmodel: claude-opus-4-8",
    )
    bench = _bench(tmp_path, has_ui=True, answers=(CANCEL_OPTION,))

    await _call(bench, {"profile": "writer", "task": "go"})

    assert len(bench.ui.calls) == 1
    assert "Model:      claude-opus-4-8" in bench.ui.calls[0][0].splitlines()


async def test_an_unresolvable_model_still_opens_the_dialog(tmp_path: Path) -> None:
    """A profile that names no model, under a parent whose own model is unknown,
    leaves the child to its own cascade — nothing to name. The dialog must still
    open, one row shorter, rather than failing on a display concern."""

    _write_profile(
        tmp_path / "agent" / "agents" / "writer.md", "writer", extra="approval_mode: auto"
    )
    bench = _bench(tmp_path, has_ui=True, answers=(CANCEL_OPTION,))

    await _call(bench, {"profile": "writer", "task": "go"})

    assert len(bench.ui.calls) == 1
    assert "Model:" not in bench.ui.calls[0][0]
