"""#822 — a refused wrap-up does not say "with this text as its next instruction".

A wrap-up carries no text of the caller's. Its refusal for a row out of the
registry (the door) and for a line written after the wire closed (a late
``refused``) used the steer's resume hint, so the model's ``agent(action="wrap_up")``
answer and the human's ``/jobs wrap`` / panel ``w w`` sentence read "use
action="resume" with this text as its next instruction". Both now stop at
``action="resume"``; a steer's and a follow-up's refusals keep the hint.

Fails on ``1c2a4a7c``: every wrap-up row below ends in "with this text as its
next instruction".

Fix round 1 (Codex M6): ``/jobs wrap`` on an agent that has ENDED never reached the
kind; the service said "<id> already ended: done". The kind now words it
(``JobEndedControlAdapter``). Fails on ``1099de2b``: that sentence. Fix round 2
(V2-F1): the panel's ``w w`` and ``m`` on such a row said "<id> has ended" without
asking the service; they now ask its ``control``. Fails on ``a3c8b9a6``: that note.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_agents.command_wire import CommandWire
from aelix_agents.extension import _send_text
from aelix_agents.jobs_client import describe_delivery
from aelix_agents.runtime import Delivery, _SubagentRuntimeImpl

from tests.agents_ext._detach_rig import reap_children  # noqa: F401 — the fixture
from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_intervention_doors_123 import _Stdin

WAY = 'the agent has finished or was stopped; to go on, use action="resume"'
WITH_TEXT = f"{WAY} with this text as its next instruction"


async def _late(verb: str) -> Delivery:
    """A request written after the child's wire closed, as the door reports it."""

    wire = CommandWire("r")
    wire.attach(_Stdin())
    wire.close()
    message = {"message": "b", "final": True} if verb == "wrap_up" else {"message": "b"}
    reply = await wire.request("steer", message)
    return Delivery.of(reply, origin="model", verb=verb)


async def _door(verb: str) -> Delivery:
    """The runtime's own door for an id no longer in its registry (a finished or stopped agent)."""

    runtime = SimpleNamespace(_children={})  # all the doors read before they refuse
    if verb == "wrap_up":
        return await _SubagentRuntimeImpl.wrap_up(runtime, "gone", origin="model")  # type: ignore[arg-type]
    return await _SubagentRuntimeImpl.steer(runtime, "gone", "NOTE", mode=verb, origin="model")  # type: ignore[arg-type]


@pytest.mark.parametrize("how", ["door", "late"])
async def test_a_refused_wrap_up_names_resume_and_no_text(how: str) -> None:
    delivery = await (_door("wrap_up") if how == "door" else _late("wrap_up"))
    assert delivery.outcome == "refused"
    assert _send_text(delivery, agent_id="abc123", verb="wrap_up") == (f"The wrap-up was refused: {WAY}.", True)
    assert describe_delivery(delivery, what="the wrap-up") == f"Not taken: {WAY}."


@pytest.mark.parametrize("verb", ["steer", "follow_up"])
@pytest.mark.parametrize("how", ["door", "late"])
async def test_a_refused_note_keeps_the_text_to_resume_with(how: str, verb: str) -> None:
    delivery = await (_door(verb) if how == "door" else _late(verb))
    assert _send_text(delivery, agent_id="abc123", verb=verb) == (f"The note was refused: {WITH_TEXT}.", True)
    assert describe_delivery(delivery, what="the note") == f"Not taken: {WITH_TEXT}."


# === fix round 1 (Codex review of #822, M6) ==========================================


@pytest.mark.usefixtures("jobs_on", "reap_children")
@pytest.mark.skipif(sys.platform == "win32", reason="a stub child's pipes, as test_detach_611")
async def test_jobs_wrap_and_steer_on_a_finished_agent_say_what_the_model_is_told(tmp_path: Path) -> None:
    """``/jobs wrap`` (and ``service.control``, which the panel's ``w w`` asks since
    fix round 2) on an agent that has ended answered "<id> already ended: done", the service's
    words, before the kind was asked. Now the kind words it, as its door does, in
    this process and after a restart. Fails on ``1099de2b``: "… already ended: done"."""

    from aelix_coding_agent.jobs.commands import JobsCommand

    from tests.agents_ext._detach_rig import finish, settled
    from tests.agents_ext.test_child_recovery_611 import _restart
    from tests.agents_ext.test_detach_611 import _moved, _resumable

    rig, gate, _text, _row = await _moved(tmp_path, channel=_resumable(tmp_path / "gate"))
    try:
        gate.write_text("go")
        snap = await settled(rig)
        short = snap.job_id[:6]
        jobs = JobsCommand(rig.service)
        assert await jobs(f"wrap {short}") == f"Not taken: {WAY}."
        assert await jobs(f"steer {short} go on") == f"Not taken: {WITH_TEXT}."
        assert await rig.service.control(snap.job_id, "wrap_up", {"origin": "user"}) == f"Not taken: {WAY}."
        new, _jobs, _harness = await _restart(rig.session)
        try:
            assert await new.control(snap.job_id, "wrap_up", {"origin": "user"}) == f"Not taken: {WAY}."
        finally:
            await new.shutdown()
    finally:
        await finish(rig)
    from aelix_agents.jobs_client import DelegationKindAdapter
    from aelix_coding_agent.jobs.contract import JobEndedControlAdapter

    assert issubclass(DelegationKindAdapter, JobEndedControlAdapter)


@pytest.mark.usefixtures("jobs_on", "reap_children")
@pytest.mark.skipif(sys.platform == "win32", reason="a stub child's pipes, as test_detach_611")
async def test_the_panels_w_w_and_m_on_a_finished_agent_say_what_jobs_wrap_and_steer_say(tmp_path: Path) -> None:
    """The ``/jobs`` panel's ``w`` ``w`` and ``m`` on an agent that has ended answer
    through the service's ``control``, so the kind words them as ``/jobs wrap`` and
    ``/jobs steer`` do. Fails on ``a3c8b9a6``: the panel answered "<id> has ended"
    itself (``_armed_key`` and ``on_message`` stopped at the ended row) and never
    asked the kind (lane b7f verify round 2, V2-F1)."""

    import asyncio

    from aelix_coding_agent.jobs.commands import JobsCommand
    from aelix_coding_agent.tui.jobs_panel import JobsPanel
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    from tests.agents_ext._detach_rig import finish, settled
    from tests.agents_ext.test_detach_611 import _moved, _resumable
    from tests.jobs.conftest import wait_for

    class _Chrome:
        def invalidate(self) -> None:
            pass

        def modal_on_top(self, _content: object) -> bool:
            return True

    rig, gate, _text, _row = await _moved(tmp_path, channel=_resumable(tmp_path / "gate"))
    try:
        gate.write_text("go")
        snap = await settled(rig)
        short = snap.job_id[:6]
        jobs = JobsCommand(rig.service)
        wrap_said = await jobs(f"wrap {short}")
        steer_said = await jobs(f"steer {short} go on")
        assert (wrap_said, steer_said) == (f"Not taken: {WAY}.", f"Not taken: {WITH_TEXT}.")

        panel = JobsPanel(rig.service, _Chrome())
        panel.list.window = Window(FormattedTextControl("", focusable=True))
        panel.list.refresh()
        panel.list._result = asyncio.get_running_loop().create_future()
        panel.list.press("w")
        panel.list.press("w")
        with contextlib.suppress(AssertionError):  # a3c8b9a6 never changes it: the assert below says what it shows
            await wait_for(lambda: panel.note != f"{short} has ended", timeout=2.0)
        assert (panel.note, panel.arm) == (wrap_said, None)
        panel.note = ""
        panel.list.press("m")
        with contextlib.suppress(AssertionError):
            await wait_for(lambda: panel.note not in ("", f"{short} has ended"), timeout=2.0)
        assert panel.note == steer_said
        assert not panel.list._result.done()  # a note, not a close with "/jobs steer <id> "
    finally:
        await finish(rig)


async def test_a_kind_without_ended_words_keeps_the_services(tmp_path: Path) -> None:
    """The sleeper declares no controls: the service's own sentence, unchanged, even
    when the kind has ``ended_control`` (it is asked only for a verb it declares)."""

    from tests.jobs import _sleeper
    from tests.jobs.conftest import fast_service, make_harness, make_session

    class _Worded(_sleeper.SleeperAdapter):
        def ended_control(self, job: object, verb: str) -> str:
            return "the kind's words"

    service = fast_service()
    service.register_kind(_Worded(), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="t", params={"seconds": 5})
    await service.stop(job_id)
    assert await service.control(job_id, "wrap_up", {}) in (
        f"{job_id[:6]} already ended: stopped",
        f"{job_id[:6]} has ended: nothing runs to take the wrap-up",
    )
    await service.shutdown()
    await _sleeper.reap_all()


async def test_a_kind_whose_ended_words_raise_leaves_the_services(tmp_path: Path) -> None:
    """V2-F3 C3: a kind that declares the verb and whose ``ended_control`` raises
    answers nothing, and the service's own sentence stands; no traceback reaches
    the surface. The same kind answering a sentence is the positive control: it is
    asked. Passes on ``a3c8b9a6``; it kills the mutant that re-raises in
    ``JobService._ended_control``."""

    import dataclasses

    from tests.jobs import _sleeper
    from tests.jobs.conftest import fast_service, make_harness, make_session

    class _Controlled(_sleeper.SleeperAdapter):
        def __init__(self, words: str | None) -> None:
            super().__init__()
            self.capabilities = dataclasses.replace(self.capabilities, controls=("wrap_up",))
            self.words = words

        async def control(self, job_id: str, verb: str, payload: object) -> str:
            return "taken"

        def ended_control(self, job: object, verb: str) -> str:
            if self.words is None:
                raise RuntimeError("boom")
            return self.words

    for words in ("the kind's words", None):
        service = fast_service()
        service.register_kind(_Controlled(words), _sleeper.Sleeper)
        await service.bind(make_harness(await make_session(tmp_path, f"{words is None}.jsonl")))
        job_id = await service.start(_sleeper.KIND, title="t", params={"seconds": 5})
        await service.stop(job_id)
        said = await service.control(job_id, "wrap_up", {})
        await service.shutdown()
        await _sleeper.reap_all()
        if words is not None:
            assert said == words, "the positive control: the kind is asked"
        else:
            assert said in (
                f"{job_id[:6]} already ended: stopped",
                f"{job_id[:6]} has ended: nothing runs to take the wrap-up",
            ), said
