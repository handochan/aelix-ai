"""``aelix.job`` facts and their fold (#722, Codex r3 #7, #8, r1 #6).

Fails on main ``a385c64a``: no ``jobs.records``. Naive "last line wins per
job" (round 3's rule) lets a later, conflicting ``outcome`` — a second process
reconciling the same file — overwrite the first; and a prefix of the file that
ends between two facts must fold to a job, never to an error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.records import (
    JOB_FACT_TYPE,
    RecordWriteFailed,
    append_fact,
    fold,
    make_fact,
)

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen


class _Entry:
    type = "custom"
    custom_type = JOB_FACT_TYPE

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


def _facts() -> list[dict[str, Any]]:
    return [
        make_fact(
            "start",
            "j",
            kind="k",
            kind_version=1,
            owner="s",
            mode="background",
            notify=True,
            title="t",
            created=1.0,
            settings_key="k",
        ),
        make_fact("process", "j", seq=1, evidence={"pid": 10, "started": 1.0, "host": "h"}),
        make_fact("process", "j", seq=2, evidence={"pid": 11, "started": 2.0, "host": "h"}),
        make_fact("cleanup", "j", helpers="confirmed"),
        make_fact("outcome", "j", status="done", brief="ok", elapsed_ms=5, client_records="ok"),
    ]


@pytest.mark.parametrize("prefix", range(6))
def test_fold_prefixes(prefix: int) -> None:
    jobs = fold([_Entry(f) for f in _facts()[:prefix]])
    if prefix == 0:
        assert jobs == {}
        return
    job = jobs["j"]
    assert job.kind == "k"
    assert len(job.processes) == max(0, min(prefix, 3) - 1)
    assert (job.outcome is not None) == (prefix == 5)


def test_a_fact_without_a_start_is_ignored() -> None:
    assert (
        fold(
            [
                _Entry(
                    make_fact(
                        "outcome", "x", status="done", brief="", elapsed_ms=0, client_records="ok"
                    )
                )
            ]
        )
        == {}
    )


def test_process_facts_accumulate() -> None:
    facts = _facts()
    duplicate = dict(facts[1], fid="other")
    job = fold([_Entry(f) for f in [*facts[:3], duplicate]])["j"]
    assert [p["evidence"]["pid"] for p in job.process_list()] == [10, 11]


def test_unknown_version_ignored() -> None:
    facts = _facts()
    future = dict(facts[4], v=2, status="failed")
    job = fold([_Entry(f) for f in [*facts[:4], future]])["j"]
    assert job.outcome is None
    unreadable = fold([_Entry(dict(facts[0], v=9))])
    assert unreadable["j"].unreadable


def test_final_never_overwritten() -> None:
    facts = _facts()
    later = make_fact(
        "outcome",
        "j",
        status="lost",
        brief="a second reconciler",
        elapsed_ms=0,
        client_records="missing",
    )
    job = fold([_Entry(f) for f in [*facts, later]])["j"]
    assert job.outcome is not None and job.outcome["status"] == "done"
    assert job.conflicts == 1


async def test_confirmed_client_settle_wins_over_child_file_and_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed client record of a finished run decides before OS evidence is read."""

    from aelix_coding_agent.jobs import activation, evidence

    monkeypatch.setattr(activation, "ACTIVE", True)
    session = await make_session(tmp_path)
    await append_fact(
        session,
        make_fact(
            "start",
            "j",
            kind=_sleeper.KIND,
            kind_version=1,
            owner="s",
            mode="background",
            notify=True,
            title="t",
            created=1.0,
            settings_key="sleeper",
            supervisor={"pid": 1, "started": 1.0, "host": evidence.this_host()},
        ),
    )
    await session.append_custom_entry("test.sleeper", {"job_id": "j", "status": "done"})

    def must_not_classify(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("evidence read although the client record settled it")

    monkeypatch.setattr(evidence, "classify_restart", must_not_classify)
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await reopen(tmp_path))
    await service.bind(harness)
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "done"
    assert harness.pending_notices == 1
    await service.shutdown()


async def test_an_unknown_outcome_is_settled_by_reading_the_file_not_written_twice(
    tmp_path: Path,
) -> None:
    from aelix_agent_core.session.storage import SessionError

    session = await make_session(tmp_path)
    real = session.place_custom_entry
    calls: list[int] = []

    async def lands_then_unknown(entry_id: str, custom_type: str, data: Any = None, **kwargs: Any) -> Any:
        calls.append(1)
        placed = await real(entry_id, custom_type, data, **kwargs)
        if len(calls) == 1:
            raise SessionError("outcome_unknown", "it may have landed")
        return placed

    session.place_custom_entry = lands_then_unknown  # type: ignore[method-assign]
    await append_fact(
        session,
        make_fact(
            "start",
            "j",
            kind="k",
            kind_version=1,
            owner="s",
            mode="background",
            notify=True,
            title="t",
            created=1.0,
            settings_key="k",
        ),
    )
    assert calls == [1, 1], "placed again under the same id: adopted, not written twice"
    assert len(fold(await session.get_entries())) == 1
    starts = [e for e in await session.get_entries() if getattr(e, "type", "") == "custom"]
    assert len(starts) == 1

    async def refuses(*_a: Any, **_k: Any) -> Any:
        raise SessionError("storage", "nothing written")

    session.place_custom_entry = refuses  # type: ignore[method-assign]
    with pytest.raises(RecordWriteFailed):
        await append_fact(session, make_fact("cleanup", "j", helpers="none"))


# === #722 fix round 1 =========================================================


def _entry(data: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(type="custom", custom_type="aelix.job", data=data)


def test_facts_before_the_start_and_invalid_outcomes_are_ignored() -> None:
    """Codex #14: an ``outcome`` (or ``process``) that comes before its ``start`` in
    the file is no part of the run; an outcome with an unknown status does not win.

    Fails on ``a80e81fa``: the pre-start outcome survived (``done``), and
    ``status="not-a-status"`` won over the valid ``done`` after it.
    """

    start = make_fact(
        "start", "j", kind="k", kind_version=1, owner="s", mode="background",
        notify=True, title="t", created=1.0, settings_key="k",
    )
    folded = fold(
        [
            _entry(make_fact("outcome", "j", status="done", brief="before")),
            _entry(make_fact("process", "j", seq=1, evidence={"pid": 1, "started": 1.0})),
            _entry(start),
            _entry(make_fact("outcome", "j", status="not-a-status", brief="bad")),
            _entry(make_fact("process", "j", seq="x", evidence={})),
            _entry(make_fact("cleanup", "j", helpers="whatever")),
            _entry(make_fact("outcome", "j", status="stopped", brief="the real one")),
        ]
    )["j"]
    assert folded.outcome is not None and folded.outcome["brief"] == "the real one"
    assert folded.processes == {} and folded.cleanup is None
    assert folded.conflicts == 1

    bad_start = dict(start, kind_version="2")
    assert fold([_entry(bad_start)])["j"].unreadable


async def test_a_retried_record_lands_once_after_a_failed_settle(tmp_path: Path) -> None:
    """Codex #13: the first write lands and raises, and settling it fails (the
    file cannot be read); the outbox writes the same fact again later.

    Fails on ``a80e81fa``: the retry appended a second line with the same ``fid``.
    """

    from aelix_agent_core.session import JsonlSessionStorage, Session
    from aelix_coding_agent.jobs.records import record_entry_id

    from tests.session.test_append_resolution_611 import _enospc, _PlanFs, _whole

    fs = _PlanFs()
    session = Session(
        await JsonlSessionStorage.create(fs, str(tmp_path / "s.jsonl"), cwd="/repo", session_id="s")
    )
    fact = make_fact("outcome", "j", status="done")
    fs.arm(_whole, _enospc())
    fs.fail_reads = True
    with pytest.raises(RecordWriteFailed):
        await append_fact(session, fact)
    fs.fail_reads = False
    await append_fact(session, fact)  # the outbox, later
    copies = [e for e in await session.get_entries() if getattr(e, "data", {}).get("fid") == fact["fid"]]
    assert [e.id for e in copies] == [record_entry_id(fact["fid"])]


async def test_a_record_id_held_by_another_payload_is_a_conflict_not_an_adoption(tmp_path: Path) -> None:
    """``Session.place_custom_entry`` adopts only the record it was asked to place:
    the same id with the same type and data writes nothing; the same id with other
    data is ``PlacementConflict`` and the stored line is left as it was. Adopting
    any payload under the id (verify R06) would take another record for this one."""

    from aelix_agent_core.session.entries import CustomEntry
    from aelix_agent_core.session.storage import PlacementConflict

    session = await make_session(tmp_path)
    first = await session.place_custom_entry("jr-1", JOB_FACT_TYPE, {"fid": "a", "v": 1})
    again = await session.place_custom_entry("jr-1", JOB_FACT_TYPE, {"fid": "a", "v": 1})
    assert not first.adopted and again.adopted
    with pytest.raises(PlacementConflict):
        await session.place_custom_entry("jr-1", JOB_FACT_TYPE, {"fid": "b", "v": 1})
    with pytest.raises(PlacementConflict):
        await session.place_custom_entry("jr-1", "another.type", {"fid": "a", "v": 1})
    stored = [e for e in await session.get_entries() if isinstance(e, CustomEntry)]
    assert [(e.id, e.data) for e in stored] == [("jr-1", {"fid": "a", "v": 1})]
