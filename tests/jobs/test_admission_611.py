"""Admission: configurable per-kind bounds, an optional total, hard maxima (#722, owner OQ6).

Fails on main ``a385c64a``: no ``jobs.admission`` and no
``get_background_jobs_settings``. Naive: a fixed module constant (round 4's
single global ceiling) ignores the user's setting; a slot released when the
notice is sent (or when the outcome is written) leaks when that write fails;
reading a project file lets a cloned repo raise its own limits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.jobs.admission import (
    HARD_MAX_PER_KIND,
    HARD_MAX_TOTAL,
    Admission,
    AdmissionRefused,
)

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for


def _admission(settings: dict[str, Any] | None, warnings: list[str] | None = None) -> Admission:
    return Admission(lambda: settings, warn=(warnings.append if warnings is not None else None))


def test_kind_only_when_total_off() -> None:
    admission = _admission({"limits": {"a": 2, "b": 2}})
    held = [
        admission.reserve("a", 4),
        admission.reserve("a", 4),
        admission.reserve("b", 4),
        admission.reserve("b", 4),
    ]
    with pytest.raises(AdmissionRefused, match=r"backgroundJobs\.limits\.a"):
        admission.reserve("a", 4)
    assert admission.total() == 4 and len(held) == 4


def test_total_cap_reserved_atomically_when_set() -> None:
    admission = _admission({"limits": {"a": 4, "b": 4}, "maxTotal": 3})
    admission.reserve("a", 4)
    admission.reserve("a", 4)
    admission.reserve("b", 4)
    with pytest.raises(AdmissionRefused, match="maxTotal"):
        admission.reserve("b", 4)
    with pytest.raises(AdmissionRefused):
        admission.reserve("a", 4, slots=2)
    assert admission.total() == 3


def test_batch_sized_to_free_slots_never_refuses_after_consent() -> None:
    """``reserve_up_to`` takes what is free before consent, so nothing after it is refused."""

    admission = _admission({"limits": {"a": 4}})
    admission.reserve("a", 4)
    batch = admission.reserve_up_to("a", 4, wanted=5)
    assert batch.slots == 3
    with pytest.raises(AdmissionRefused, match="do not retry"):
        admission.reserve_up_to("a", 4, wanted=1)
    batch.release()
    batch.release()
    assert admission.count("a") == 1


def test_invalid_values_fall_back_with_one_warning() -> None:
    warnings: list[str] = []
    admission = _admission(
        {
            "limits": {"a": 0, "b": HARD_MAX_PER_KIND + 1, "c": "4", "d": True},
            "maxTotal": HARD_MAX_TOTAL + 1,
        },
        warnings,
    )
    for _ in range(3):
        assert admission.limit_for("a", 4) == 4
        assert admission.limit_for("b", 2) == 2
        assert admission.limit_for("c", 4) == 4
        assert admission.limit_for("d", 4) == 4
        assert admission.total_limit() is None
    assert len(warnings) == 5
    assert admission.limit_for("e", 99) == HARD_MAX_PER_KIND, "a kind's default is clamped too"


def test_lowering_a_limit_refuses_new_only() -> None:
    settings: dict[str, Any] = {"limits": {"a": 3}}
    admission = _admission(settings)
    held = [admission.reserve("a", 4) for _ in range(3)]
    settings["limits"]["a"] = 1
    with pytest.raises(AdmissionRefused):
        admission.reserve("a", 4)
    held[0].release()
    held[1].release()
    with pytest.raises(AdmissionRefused):
        admission.reserve("a", 4)
    held[2].release()
    admission.reserve("a", 4)


def test_project_scope_ignored(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".aelix").mkdir(parents=True)
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"backgroundJobs": {"limits": {"sleeper": 2}}}))
    (project / ".aelix" / "settings.json").write_text(
        json.dumps({"backgroundJobs": {"limits": {"sleeper": 16}, "maxTotal": 32}})
    )
    manager = SettingsManager.create(cwd=str(project), agent_dir=str(home))
    manager.set_project_trusted(True)
    assert manager.get_background_jobs_settings() == {"limits": {"sleeper": 2}}


@pytest.mark.usefixtures("jobs_active")
async def test_slot_released_on_cleanup_even_when_record_append_fails(tmp_path: Path) -> None:
    from aelix_agent_core.session.storage import SessionError

    service = fast_service()
    _sleeper.register(service, bound=1)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.2})

    async def refuses(*_a: Any, **_k: Any) -> str:
        raise SessionError("storage", "disk full")

    session.place_custom_entry = refuses  # type: ignore[method-assign]
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    assert service.admission.count(_sleeper.KIND) == 0, "released at confirmed cleanup"
    assert service.unsettled(), "its outcome is still owed, so -p waits"
    await service.shutdown()


# === #722 fix round 1 =========================================================


async def test_a_start_cancelled_before_its_job_exists_gives_its_slot_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex #15: the reservation is the call's until the job's finalizer owns it.

    Fails on ``a80e81fa``: a cancel while the ``start`` fact was being written
    left ``slots=1`` with no job to give it back (``free=0``).
    """

    import asyncio

    from aelix_coding_agent.jobs import service as service_module

    service = fast_service()
    _sleeper.register(service, bound=1)
    await service.bind(make_harness(await make_session(tmp_path)))

    async def never_lands(_session: Any, _fact: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(service_module, "append_fact", never_lands)
    start = asyncio.ensure_future(service.start(_sleeper.KIND, title="cancelled"))
    await asyncio.sleep(0.05)
    assert service.admission.count(_sleeper.KIND) == 1
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    assert service.admission.count(_sleeper.KIND) == 0
    assert service.admission.free(_sleeper.KIND, 1, settings_key=_sleeper.SETTINGS_KEY) == 1
    await service.shutdown()


async def test_a_reservation_taken_before_consent_is_handed_to_the_start(tmp_path: Path) -> None:
    """Codex #16: ``reserve`` before consent, then ``start(reservation=…)`` takes its
    slot out of it; a competing caller in between is refused, the consented one is not.

    Fails on ``a80e81fa``: ``start`` reserved another slot, so with a bound of one
    the consented start was refused by the caller's own reservation.
    """

    from aelix_coding_agent.jobs.service import JobStartRefused

    service = fast_service()
    _sleeper.register(service, bound=2)
    await service.bind(make_harness(await make_session(tmp_path)))
    batch = service.reserve(_sleeper.KIND, wanted=5)
    assert batch.slots == 2
    with pytest.raises(JobStartRefused):
        await service.start(_sleeper.KIND, title="competing caller")
    first = await service.start(
        _sleeper.KIND, title="consented 1", params={"seconds": 30}, reservation=batch
    )
    second = await service.start(
        _sleeper.KIND, title="consented 2", params={"seconds": 30}, reservation=batch
    )
    assert batch.released and batch.slots == 0, "spent"
    assert service.admission.count(_sleeper.KIND) == 2
    with pytest.raises(JobStartRefused, match="spent"):
        await service.start(_sleeper.KIND, title="third", reservation=batch)
    for job_id in (first, second):
        await service.stop(job_id)
    assert service.admission.count(_sleeper.KIND) == 0
    await service.shutdown()


async def test_a_kind_registered_again_under_another_settings_key_keeps_its_count(tmp_path: Path) -> None:
    """Codex #17: counted by the kind, not by the key that bounds it.

    Fails on ``a80e81fa``: re-registering with a new settings key admitted 16
    more jobs of the same kind while the first generation's 16 still ran.
    """

    from aelix_coding_agent.jobs.service import JobStartRefused

    service = fast_service()
    _sleeper.register(service, bound=2)
    await service.bind(make_harness(await make_session(tmp_path)))
    ids = [
        await service.start(_sleeper.KIND, title=f"gen1-{n}", params={"seconds": 30})
        for n in range(2)
    ]
    renamed = _sleeper.SleeperAdapter(bound=2)
    renamed.settings_key = "renamed-key"
    service.register_kind(renamed, _sleeper.Sleeper)
    with pytest.raises(JobStartRefused, match="backgroundJobs.limits.renamed-key"):
        await service.start(_sleeper.KIND, title="gen2")
    assert service.admission.count(_sleeper.KIND) == 2
    for job_id in ids:
        await service.stop(job_id)
    await service.shutdown()


def test_a_kinds_noun_counts_what_runs_with_its_plural() -> None:
    """#611 S1c (verify S3): a kind that names what it runs is counted by that noun.

    The service stays kind-agnostic — the noun is ``KindCapabilities.noun``,
    passed through — and a kind with none keeps the generic sentence. Fails on
    ``b387b4b5``: ``reserve`` takes no ``noun``.
    """

    noun = ("delegated agent", "delegated agents")
    admission = _admission({"limits": {"a": 1, "b": 2}})
    admission.reserve("a", 4)
    with pytest.raises(AdmissionRefused) as one:
        admission.reserve_up_to("a", 4, wanted=1, noun=noun)
    assert str(one.value).startswith("1 delegated agent already runs; ")
    admission.reserve("b", 4)
    admission.reserve("b", 4)
    with pytest.raises(AdmissionRefused) as two:
        admission.reserve("b", 4, noun=noun)
    assert str(two.value).startswith("2 delegated agents already run; ")
    with pytest.raises(AdmissionRefused) as generic:
        admission.reserve("b", 4)
    assert str(generic.value).startswith("2 background jobs of this kind already run; ")


def test_the_total_limit_names_no_kind_and_calls_nothing_a_background_job() -> None:
    """Verify-r2 N5: ``backgroundJobs.maxTotal`` counts every kind together, and a
    kind may count what runs in the foreground (delegation counts every live
    agent), so its refusal names no kind's noun and no "background jobs".

    Fails on ``fa742f77``: "3 background jobs already run; …".
    """

    noun = ("delegated agent", "delegated agents")
    admission = _admission({"limits": {"a": 4, "b": 4}, "maxTotal": 3})
    admission.reserve("a", 4)
    admission.reserve("b", 4)
    admission.reserve("b", 4)
    with pytest.raises(AdmissionRefused) as refused:
        admission.reserve("a", 4, noun=noun)
    assert str(refused.value) == (
        "3 run across every kind, and settings backgroundJobs.maxTotal allows 3. "
        "Stop one or wait; do not retry."
    )


async def test_a_start_with_no_reservation_counts_by_the_kinds_noun(tmp_path: Path) -> None:
    """Verify-r2 N03: ``JobService.start`` reserving its own slot passes the kind's
    noun, as ``JobService.reserve`` does.

    A guard on ``fa742f77``: kills verify-r2's N03 (that reserve without
    ``noun``), under which it reads "1 background jobs of this kind already run".
    """

    from dataclasses import replace

    from aelix_coding_agent.jobs.service import JobStartRefused

    service = fast_service()
    adapter = _sleeper.SleeperAdapter(bound=1)
    adapter.capabilities = replace(adapter.capabilities, noun=("sleeper", "sleepers"))
    service.register_kind(adapter, _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="one", params={"seconds": 30})
    with pytest.raises(JobStartRefused) as refused:
        await service.start(_sleeper.KIND, title="two")
    assert str(refused.value).startswith("1 sleeper already runs; ")
    await service.stop(job_id)
    await service.shutdown()
