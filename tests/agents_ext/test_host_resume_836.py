"""#836 (ADR-0318) — the runtime's host door, ``resume_for_host``, behind RPC ``children/resume``.

WHAT IS PINNED HERE. The door resumes the target the model's
``agent(action="resume")`` finds (``find_resume_target``), refuses one a person
started with the host's own sentence, runs the runtime's checks again before a
dialog (each a ``ValueError`` with the sentence the host reads, nothing launched,
no dialog asked), takes consent as the user-typed ``spawn`` does — on the bound
UI, the first run's grant still the ceiling, the instruction labelled the
host's — and resumes through the model door's re-checks unbudgeted and with no
tool call on its record.

AT WHAT LEVEL. The real runtime and a real parent session on disk, with
``test_agent_resume_610``'s channel stand-in, as #610's runtime tests do. The
profile is re-resolved through ``resolve_profile``, replaced here by the
stand-in profile the first run used (the stand-in has no file on disk to
discover); ``test_a_profile_that_cannot_be_found_is_refused`` keeps the real
failure.

Fails on ``0436193c``: ``_SubagentRuntimeImpl`` has no ``resume_for_host``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session.session_lock import SessionWriterLock
from aelix_agents.child_session import (
    RESUME_CWD_GONE,
    RESUME_IN_USE,
    RESUME_NOT_YOURS,
    RESUME_PROFILE_CHANGED,
    RESUME_RUNNING,
    RESUME_UNKNOWN_ID,
)
from aelix_agents.consent import (
    CANCEL_OPTION,
    RESUME_HOST_INSTRUCTION_LABEL,
    RESUME_INSTRUCTION_LABEL,
)
from aelix_agents.runtime import (
    HOST_RESUME_NOT_YOURS,
    HOST_RESUME_TASK_MAX_BYTES,
    resume_task,
)
from aelix_agents.tool import RESUME_TASK_MAX_BYTES
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import HostResume

from tests.agents_ext.test_agent_resume_610 import (
    _Channel,
    _delegated,
    _Live,
    _received_task,
    _resolved,
    _runtime,
    _Scene,
    _target,
)
from tests.agents_ext.test_child_session_records import _parent, _records
from tests.env_sandbox import sandbox_home


class _UI:
    """A dialog that records what it was shown and answers ``answer`` (an index, or ``None`` for Esc)."""

    def __init__(self, answer: int | None) -> None:
        self.answer = answer
        self.titles: list[str] = []
        self.options: list[list[str]] = []

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        self.titles.append(title)
        self.options.append(list(options))
        return None if self.answer is None else options[self.answer]


def _host(scene: _Scene, *, posture: PermissionMode, ui: _UI | None = None) -> None:
    ctx = type("Ctx", (), {"has_ui": ui is not None, "ui": ui})()
    scene.runtime.host = dataclasses.replace(
        scene.runtime.host, posture=lambda: posture, consent_context=lambda: ctx
    )
    scene.runtime.resolve_profile = lambda name, *, allow_project=False: _resolved()  # type: ignore[method-assign]


async def _starts(scene: _Scene) -> list[dict[str, Any]]:
    return [data for _type, data in await _records(scene.session) if data.get("phase") == "start"]


def test_the_bundled_runtime_is_a_host_resume_and_the_bounds_agree() -> None:
    assert issubclass(type(_runtime(_Live(cwd=Path("/")), _Channel())), HostResume)
    assert HOST_RESUME_TASK_MAX_BYTES == RESUME_TASK_MAX_BYTES, (
        "the agent parser's bound for a resume"
    )


async def test_a_host_resumes_an_agent_the_model_delegated(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    budget = scene.runtime.remaining_delegation_budget()
    result = await scene.runtime.resume_for_host(scene.child_id, "keep going")
    assert result.ok, result.summary
    assert len(scene.channel.plans) == 2
    plan = scene.channel.plans[1]
    assert plan.session_path == str(scene.child_path), "on the agent's own session"
    assert plan.task == resume_task("keep going")
    assert scene.runtime.remaining_delegation_budget() == budget, "a person asked: unbudgeted"
    first, resumed = await _starts(scene)
    assert first["tool_call_id"] == "tc-1"
    assert (resumed["resumes"], resumed["tool_call_id"]) == (scene.first_key, None)
    # The model can still resume it afterwards: the lookup reads the FIRST run.
    assert (await _target(scene)).first_key == scene.first_key


async def test_a_resume_the_host_made_runs_at_the_first_grant_at_most(tmp_path: Path) -> None:
    """ADR-0277 D10's ceiling holds on this door too: a yolo host cannot widen a ``plan`` agent."""

    scene = await _delegated(tmp_path, mode=PermissionMode.PLAN)
    _host(scene, posture=PermissionMode.YOLO)
    assert (await scene.runtime.resume_for_host(scene.child_id, "go")).ok
    assert scene.channel.plans[1].permission_mode is PermissionMode.PLAN


async def test_an_agents_run_child_is_refused_with_the_hosts_sentence(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    live = _Live(cwd=tmp_path / "p", session=session)
    channel = _Channel()
    runtime = _runtime(live, channel)
    typed = await runtime.spawn(_resolved(), "a task a human typed")
    assert typed.session_id is not None
    await _received_task(channel.plans[0].session_path or "")
    assert await runtime.find_resume_target(typed.session_id) == RESUME_NOT_YOURS, (
        "the model's, unchanged"
    )
    with pytest.raises(ValueError) as refused:
        await runtime.resume_for_host(typed.session_id, "go on")
    assert str(refused.value) == HOST_RESUME_NOT_YOURS
    assert "agents/run" in HOST_RESUME_NOT_YOURS and "by you" not in HOST_RESUME_NOT_YOURS
    assert len(channel.plans) == 1


def _hold_lock(scene: _Scene) -> Any:
    holder = SessionWriterLock(str(scene.child_path))
    assert holder.try_acquire()
    return holder.release


def _gone_cwd(scene: _Scene) -> None:
    scene.live.cwd = scene.live.cwd.parent / "elsewhere"
    scene.live.cwd.mkdir(exist_ok=True)


async def _running(scene: _Scene) -> Any:
    scene.channel._park = True
    scene.channel.arrived.clear()
    target = await _target(scene)
    first = asyncio.ensure_future(
        scene.runtime.resume_granted(_grant_plan(), _resolved(), target, "one", charge_budget=False)
    )
    await scene.channel.arrived.wait()

    async def finish() -> None:
        scene.channel.release.set()
        await first

    return finish


def _grant_plan() -> Any:
    from tests.agents_ext.test_child_session_records import _grant

    return _grant()


@pytest.mark.parametrize(
    ("case", "head"),
    [
        ("unknown id", RESUME_UNKNOWN_ID),
        ("its writer lock is held", RESUME_IN_USE),
        ("its directory is gone", RESUME_CWD_GONE),
        ("it runs here", RESUME_RUNNING),
        ("a NUL in the instruction", "The instruction holds a NUL"),
        ("one byte over the bound", f"The instruction is {HOST_RESUME_TASK_MAX_BYTES + 1} bytes"),
    ],
)
async def test_each_check_refuses_before_a_dialog_or_a_process(
    tmp_path: Path, case: str, head: str
) -> None:
    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    ui = _UI(answer=0)
    _host(scene, posture=PermissionMode.AUTO_ACCEPT, ui=ui)
    child_id, task, undo = scene.child_id, "go on", None
    if case == "unknown id":
        child_id = "nope"
    elif case == "its writer lock is held":
        undo = _hold_lock(scene)
    elif case == "its directory is gone":
        _gone_cwd(scene)
    elif case == "it runs here":
        undo = await _running(scene)
    elif case == "a NUL in the instruction":
        task = "a\x00b"
    else:
        task = "x" * (HOST_RESUME_TASK_MAX_BYTES + 1)
    plans = len(scene.channel.plans)
    try:
        with pytest.raises(ValueError) as refused:
            await scene.runtime.resume_for_host(child_id, task)
    finally:
        if undo is not None:
            result = undo()
            if asyncio.iscoroutine(result):
                await result
    assert str(refused.value).startswith(head), refused.value
    assert ui.titles == [], "refused before a dialog"
    assert len(scene.channel.plans) == plans, "nothing launched for the refused request"


async def test_a_profile_that_cannot_be_found_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    scene = await _delegated(tmp_path)
    # The runtime's own resolve_profile: the stand-in's file is not on disk.
    with pytest.raises(ValueError) as refused:
        await scene.runtime.resume_for_host(scene.child_id, "go on")
    assert str(refused.value) == RESUME_PROFILE_CHANGED
    assert len(scene.channel.plans) == 1


async def test_an_instruction_at_the_bound_is_resumed(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    task = "x" * HOST_RESUME_TASK_MAX_BYTES
    assert (await scene.runtime.resume_for_host(scene.child_id, task)).ok
    assert scene.channel.plans[1].task == resume_task(task)


async def test_consent_names_the_host_as_the_instructions_writer_and_a_decline_runs_nothing(
    tmp_path: Path,
) -> None:
    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    ui = _UI(answer=None)  # Esc
    _host(scene, posture=PermissionMode.AUTO_ACCEPT, ui=ui)
    declined = await scene.runtime.resume_for_host(scene.child_id, "write the file now")
    assert (declined.ok, declined.status) == (False, "declined")
    assert declined.summary == "Delegation declined by the user; no agent was started."
    assert declined.permission_mode == "auto-accept-edits", "the mode a yes would have granted"
    assert len(scene.channel.plans) == 1, "a decline launches nothing"
    (title,) = ui.titles
    assert title.startswith("Resume agent 'scout'?")
    assert RESUME_HOST_INSTRUCTION_LABEL in title and RESUME_INSTRUCTION_LABEL not in title
    assert "write the file now" in title and f"Session:    {scene.child_id}" in title
    assert CANCEL_OPTION in ui.options[0]

    ui.answer = 0  # the first option: the clamp, auto-accept-edits
    resumed = await scene.runtime.resume_for_host(scene.child_id, "write the file now")
    assert resumed.ok and scene.channel.plans[1].permission_mode is PermissionMode.AUTO_ACCEPT


async def test_with_no_ui_the_clamp_is_consented_as_for_agents_run(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    _host(scene, posture=PermissionMode.AUTO_ACCEPT)
    assert (await scene.runtime.resume_for_host(scene.child_id, "go")).ok
    assert scene.channel.plans[1].permission_mode is PermissionMode.AUTO_ACCEPT


async def test_the_slot_is_reserved_before_the_dialog_and_given_back(tmp_path: Path) -> None:
    """While background jobs are on, as ``spawn``: a refused reservation is the answer, a taken one is released.

    The dialog itself sees the slot taken (fix round 1, Codex C7): the
    reservation is made before the consent is asked, not after. Moving the
    consent ``await`` above ``reserve(1)`` fails this test.
    """

    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    taken: list[str] = []
    seen: list[list[str]] = []

    class _Watching(_UI):
        async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
            seen.append(list(taken))
            return await super().select(title, options, opts)

    _host(scene, posture=PermissionMode.AUTO_ACCEPT, ui=_Watching(answer=0))

    class _Slot:
        def release(self) -> None:
            taken.append("released")

    def reserve(count: int) -> _Slot:
        taken.append(f"reserved {count}")
        return _Slot()

    scene.runtime.host = dataclasses.replace(scene.runtime.host, reserve=reserve)
    assert (await scene.runtime.resume_for_host(scene.child_id, "go")).ok
    assert seen == [["reserved 1"]], "the dialog opened with the slot already reserved"
    assert taken == ["reserved 1", "released"]

    def refuse(count: int) -> Any:
        raise RuntimeError("every agent slot is taken")

    scene.runtime.host = dataclasses.replace(scene.runtime.host, reserve=refuse)
    with pytest.raises(ValueError, match="^every agent slot is taken$"):
        await scene.runtime.resume_for_host(scene.child_id, "go")
    assert len(scene.channel.plans) == 2


async def test_a_person_asking_reopens_a_runtime_whose_stop_all_ended(tmp_path: Path) -> None:
    """As ``/agents run`` (``spawn``): the door ``stop_all`` shut is reopened for a person's request."""

    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    await scene.runtime.stop_all()
    assert (await scene.runtime.resume_for_host(scene.child_id, "go on")).ok


# === fix round 1 =====================================================================


async def test_the_hosts_dialogs_are_asked_when_the_hook_context_has_no_ui(tmp_path: Path) -> None:
    """Codex C1, at the runtime: ``ui`` is asked even where the extension's hook context has none."""

    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    _host(
        scene, posture=PermissionMode.AUTO_ACCEPT
    )  # a hook context with no UI, as before a prompt
    ui = _UI(answer=None)
    declined = await scene.runtime.resume_for_host(scene.child_id, "go on", ui=ui)
    assert (declined.status, len(ui.titles), len(scene.channel.plans)) == ("declined", 1, 1)
    assert RESUME_HOST_INSTRUCTION_LABEL in ui.titles[0]
    ui.answer = 0
    assert (await scene.runtime.resume_for_host(scene.child_id, "go on", ui=ui)).ok
    assert len(ui.titles) == 2 and len(scene.channel.plans) == 2


@pytest.mark.parametrize("how", ["removed", "another file"])
async def test_a_profile_that_moved_while_the_dialog_was_open_is_refused_after_it(
    tmp_path: Path, how: str
) -> None:
    """Codex C2: the model door's anti-substitution check, after the dialog. Fails without fix round 1: it launched."""

    from aelix_agents.consent import IDENTITY_MISMATCH

    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    runtime = scene.runtime
    moved: list[bool] = []

    def resolve(name: str, *, allow_project: bool = False) -> Any:
        """The stand-in profile, until the dialog has opened; then gone, or another file."""

        if not moved:
            return _resolved()
        if how == "removed":
            raise FileNotFoundError(name)
        return dataclasses.replace(_resolved(), source_path="/elsewhere/scout.md")

    class _Moving(_UI):
        async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
            moved.append(True)
            return await super().select(title, options, opts)

    ui = _Moving(answer=0)
    _host(scene, posture=PermissionMode.AUTO_ACCEPT, ui=ui)
    runtime.resolve_profile = resolve  # type: ignore[method-assign]
    result = await runtime.resume_for_host(scene.child_id, "go on")
    assert len(ui.titles) == 1, "the dialog was answered yes"
    assert (result.ok, result.status, result.summary) == (False, "error", IDENTITY_MISMATCH)
    assert len(scene.channel.plans) == 1, "nothing was started"
    assert len(await _starts(scene)) == 1, "and nothing recorded"


async def test_the_hosts_listing_names_an_agent_the_agents_lists_turn_off(tmp_path: Path) -> None:
    """Verify V-F2: the host door is not filtered (ADR-0318 D4), so its listing does not say such an agent cannot be resumed.

    Fails without fix round 1: the host's listing left the agent out and said "they
    cannot be resumed" while the same door resumed it.
    """

    from aelix_coding_agent.core.resource_filter import ScopeList
    from aelix_coding_agent.core.resource_selection import SettingsLists

    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    scene.runtime.host = dataclasses.replace(
        scene.runtime.host,
        agent_lists=lambda: SettingsLists(ScopeList("global", ("-scout",), "/g"), None),
    )

    class _Off:
        def verdict(self, name: str) -> str:
            return "off" if name == "scout" else "available"

    scene.runtime.delegation = lambda: _Off()  # type: ignore[method-assign]
    model_listing = await scene.runtime.find_resume_target("nope")
    assert isinstance(model_listing, str) and "they cannot be resumed" in model_listing
    assert scene.child_id not in model_listing, "the model's listing is unchanged"
    with pytest.raises(ValueError) as refused:
        await scene.runtime.resume_for_host("nope", "go on")
    assert scene.child_id in str(refused.value) and "cannot be resumed" not in str(refused.value)
    assert (await scene.runtime.resume_for_host(scene.child_id, "go on")).ok, "and resumes it"


async def test_a_default_parent_with_child_approvals_on_resumes_an_agent_that_asks_it(
    tmp_path: Path,
) -> None:
    """Verify V-F5 (mutant M35): ADR-0308 D3 on the host's door — the agent runs ``default`` and asks its parent."""

    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    _host(scene, posture=PermissionMode.DEFAULT)
    scene.runtime.host = dataclasses.replace(scene.runtime.host, asks_parent=lambda: True)
    assert (await scene.runtime.resume_for_host(scene.child_id, "go on")).ok
    plan = scene.channel.plans[1]
    assert (plan.permission_mode, plan.asks_parent) == (PermissionMode.DEFAULT, True)


async def test_a_resume_nobody_was_asked_about_is_announced_while_it_runs(tmp_path: Path) -> None:
    """Verify V-F6 (mutant M40): the disclosure a yolo parent's resume carries is shown, then taken down."""

    scene = await _delegated(tmp_path, mode=PermissionMode.YOLO)
    ui = _UI(answer=0)
    _host(scene, posture=PermissionMode.YOLO, ui=ui)
    shown: list[tuple[str, str | None]] = []
    scene.runtime.host = dataclasses.replace(
        scene.runtime.host, on_disclosure=lambda key, text: shown.append((key, text))
    )
    assert (await scene.runtime.resume_for_host(scene.child_id, "go on")).ok
    assert ui.titles == [], "a yolo parent is told, not asked"
    (key, text), (key_after, gone) = shown
    assert key == key_after and gone is None
    assert text and "resuming scout" in text
