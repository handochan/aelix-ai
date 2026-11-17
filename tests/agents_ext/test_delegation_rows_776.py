"""#776 (#611 S3b) — a background agent's rows, as the ``/jobs`` panel and table show them (ADR-0299).

Design proposal of the issue (not an owner decision): row 0 ``<profile>: <task>``
(the job's title); row 1 ``running <tool>`` or ``thinking`` with ``· <n> tok``
while it runs; row 2 its last text on one line; row 3 ``id <agent id>``. Each
row is cut to the width in terminal cells, not in characters.

Fails on ``7e2feb50``: row 0 was ``agent <profile> · <title>`` (the label and the
profile again before a title that already names the profile), row 1 ``  running
<tool>`` only, no ``thinking``, tokens or last text, and rows were cut by ``str``
length, so a row of Hangul took twice the width it was given.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from aelix_agents.jobs_client import AgentJob, DelegationJobs, DelegationKindAdapter
from aelix_coding_agent.jobs.contract import JobSnapshot
from rich.cells import cell_len


def _snap(state: str = "running", progress: dict[str, Any] | None = None, title: str = "scout: count files") -> JobSnapshot:
    return JobSnapshot(
        job_id="j1", kind="agent", label="agent", title=title, state=state, owner="s",  # type: ignore[arg-type]
        created=0.0, elapsed=3.0, last_activity_ago=1.0, quiet=False, result_waiting=False,
        notice_state="none", progress=progress,
    )


def _adapter(agent_id: str | None = "sub-1234") -> DelegationKindAdapter:
    jobs = DelegationJobs()
    jobs.info["j1"] = AgentJob(job_id="j1", profile="scout", agent_id=agent_id)
    return DelegationKindAdapter(jobs, session=lambda: None)


def test_a_running_agent_has_its_four_rows() -> None:
    progress = {"tool": "bash", "tokens": 12345, "last_text": "Found 412 files;\n\x1b[31mnow counting"}
    rows = _adapter().render_rows(_snap(progress=progress), 120)
    assert rows[:2] == ["scout: count files", "running bash · 12.3k tok"]
    assert rows[2].startswith("Found 412 files; ") and rows[2].endswith("now counting"), "one line"
    assert "\x1b" not in rows[2], "the child's own escape is not sent to the terminal"
    assert rows[3] == "id sub-1234"


@pytest.mark.parametrize(
    ("progress", "row"),
    [
        ({}, "thinking"),
        ({"tokens": 0}, "thinking"),
        ({"tokens": 812}, "thinking · 812 tok"),
        ({"tool": "read", "tokens": 1000}, "running read · 1.0k tok"),
    ],
)
def test_row_one_says_what_it_does(progress: dict[str, Any], row: str) -> None:
    assert _adapter().render_rows(_snap(progress=progress), 120)[1] == row


def test_an_ended_agent_has_no_doing_row_and_no_id_before_it_has_one() -> None:
    rows = _adapter(agent_id=None).render_rows(_snap(state="done", progress={"tool": "bash", "last_text": "all done"}), 120)
    assert rows == ["scout: count files", "all done"]


def test_the_rows_are_cut_by_cells() -> None:
    title = "scout: " + "파일을 세어 주세요 " * 8
    progress = {"last_text": "가나다라마바사아자차카타파하" * 3}
    rows = _adapter().render_rows(_snap(progress=progress, title=title), 30)
    assert all(cell_len(row) <= 30 for row in rows), [(cell_len(r), r) for r in rows]
    assert rows[0].endswith("…") and rows[2].endswith("…")
    assert cell_len(rows[2]) >= 29, "cut at the width, not at half of it"


def test_after_a_restart_the_id_comes_from_the_records() -> None:
    """No entry in this process (a restored job): the agent id its outcome recorded."""

    snap = _snap(state="done")
    restored = dataclasses.replace(snap, payload={"agent_id": "sub-9"})
    rows = DelegationKindAdapter(DelegationJobs(), session=lambda: None).render_rows(restored, 120)
    assert rows == ["scout: count files", "id sub-9"]


def test_a_vs16_task_is_cut_within_the_width_and_a_user_stop_names_jobs() -> None:
    """#776 fix round 1: Codex M3 (79 cells at width 40 on ``3f11a358``), verify N2."""

    from aelix_agents import jobs_client

    rows = _adapter().render_rows(_snap(title="scout: " + "☺️" * 100, progress={"last_text": "♥️" * 80}), 40)
    assert all(cell_len(row) <= 40 for row in rows), [(cell_len(r), r) for r in rows]
    assert jobs_client._STOPPED_BY["user"] == "stopped by the user (/jobs)"
