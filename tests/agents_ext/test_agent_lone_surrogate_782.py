"""#782 — a background agent whose answer holds a lone surrogate ends its job like any other.

A lone surrogate (``"\\ud83d"``, half an emoji) is a legal ``str`` that a child's
JSON stream and its session file carry. A FOREGROUND agent keeps it in the
tool's result and nothing raises there; the openai-completions adapter drops it
on the wire (``providers/_sanitize_unicode.py``), so that model reads the text
without it (measured for this issue on ``52bc607a``; the Anthropic adapter
raises on it before sending, a separate pre-existing defect). A BACKGROUND agent's
text is cut and paged by UTF-8 bytes and written to its result file, which no
encoder can do with it: its job's drive task died in ``jobs/notice.cut_brief``
with no outcome fact and no notice, and ``agent(action="result")`` raised in
``jobs_client.page``. The delegation client now replaces each lone surrogate
with U+FFFD where the child's text enters it (``jobs_client._utf8_safe``): its
result and brief, its progress, and an answer read back after a restart. A tool
name holding one made the envelope's tool-trail byte count raise
(``envelope.render_tool_trail``), for a background job (``failed``, "client error:
UnicodeEncodeError") and a foreground call alike; the count now passes it
through, as the rest of the envelope's byte counts do.

The REAL ``AgentsExtension``, job service and ``PrintChannel`` over a stub child
(``test_agent_background_611``'s rig), and for a restart the real kind adapter
over a parent and child file (``test_child_recovery_611``'s).

On ``52bc607a`` (measured with ``_utf8_safe`` stubbed as the identity, as this
file cannot import it there): the first two cases wait for an outcome and a
notice that never come, the drive task having died in ``cut_brief``; the
restart-recovered case gets a notice, but the generic fallback one, because the
kind's ``render_notice`` raised ``UnicodeEncodeError`` and the service fell back
(its ``RECOVERED_CAVEAT`` and answer are missing); the settled-run restart case
raises in ``page``; the helper guard fails as the identity. On ``639dcdf6``
(#782 fix round 1) the two progress cases fail: the job ends ``failed`` and the
foreground call raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.envelope import MAX_TRAIL_BYTES, render_tool_trail
from aelix_agents.jobs_client import RECOVERED_CAVEAT, _utf8_safe, result_text
from aelix_agents.stream import _StreamState, reduce_event
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE

from tests.agents_ext.test_agent_background_611 import (
    REAL_CHILD,
    _rig,
    _script,
    _settled,
    _spawned_id,
    _text,
    jobs_on,  # noqa: F401 — the fixture
)
from tests.agents_ext.test_agent_retention_769 import _let_go, _result_path, _results
from tests.agents_ext.test_child_recovery_611 import (
    _gone_process_fact,
    _restart,
    _settle,
    _spawned,
    _start_fact,
)
from tests.jobs.conftest import wait_for

pytestmark = pytest.mark.usefixtures("jobs_on")

HALF = "\ud83d"
SHORT = f"before {HALF} after"
# ~50 KB, a lone surrogate on every line: several pages on every surface.
LONG = "".join(f"line {i:05d} holds half {HALF} of an emoji 🙂 and 줄\n" for i in range(1200))


def _outcomes(entries: list[Any], job_id: str) -> list[dict[str, Any]]:
    return [
        e.data
        for e in entries
        if e.type == "custom"
        and e.custom_type == JOB_FACT_TYPE
        and e.data.get("fact") == "outcome"
        and e.data.get("job_id") == job_id
    ]


@REAL_CHILD
async def test_the_job_ends_with_its_outcome_and_exactly_one_notice(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, script=_script(SHORT))
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
        snap = await _settled(rig)
        assert snap.state == "done" and snap.notice_state == "pending"
        assert snap.brief == "before \ufffd after"
        (outcome,) = _outcomes(list(await rig.session.get_entries()), snap.job_id)
        assert outcome["status"] == "done" and outcome["brief"] == snap.brief
        (notice,) = rig.harness.undelivered_notices()
        assert "before \ufffd after" in notice.body and HALF not in notice.body
        assert HALF not in notice.brief
    finally:
        await rig.service.shutdown()


@REAL_CHILD
async def test_every_result_surface_reads_the_same_text_and_its_file_byte_for_byte_after_a_let_go(
    tmp_path: Path,
) -> None:
    """``agent(action="result")``, ``job(action="result")`` and ``/jobs result`` page the same text,
    each byte once; the file holds exactly that text, and after the job service lets the job go the
    three read it from the file, page for page, as before."""

    rig = await _rig(tmp_path, script=_script(LONG))
    try:
        agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
        snap = await _settled(rig)
        await rig.harness.prompt_notices()
        await wait_for(lambda: rig.service.snapshot(snap.job_id).notice_state == "delivered")  # type: ignore[union-attr]
        held = rig.service.snapshot(snap.job_id).payload["text"]  # type: ignore[union-attr]
        # The envelope keeps the head of a long answer; every half it kept is U+FFFD.
        assert HALF not in held and held.count("\ufffd") == held.count("holds half") > 1000

        before = await _results(rig, agent_id, snap.job_id)
        assert all(len(pages) >= 2 for pages in before.values())
        assert {surface: "".join(pages) for surface, pages in before.items()} == dict.fromkeys(before, held)
        path = _result_path(rig, snap.job_id)
        assert path.read_bytes() == held.encode("utf-8"), "the file holds the text byte for byte"

        await _let_go(rig, snap.job_id)
        assert await _results(rig, agent_id, snap.job_id) == before
    finally:
        await rig.service.shutdown()


async def test_a_restart_that_recovers_the_answer_from_the_childs_file_gives_it_once(tmp_path: Path) -> None:
    """With no settle of its own, a restart reads the answer from the child's file (``recovered``):
    its notice and ``result`` carry it with U+FFFD in the half's place."""

    session, _receipt = await _spawned(tmp_path, SHORT)
    await _start_fact(session)
    await _gone_process_fact(session)
    service, jobs, harness = await _restart(session)
    try:
        snap = service.snapshot("a1b2c3d4e5f60718")
        assert snap is not None and snap.state == "recovered"
        (notice,) = harness.undelivered_notices()
        assert RECOVERED_CAVEAT in notice.body and "before \ufffd after" in notice.body
        text, failed = await result_text(service, jobs, snap.job_id, offset=0, session=session)
        assert not failed and text == f"{RECOVERED_CAVEAT}\n\nbefore \ufffd after"
    finally:
        await service.shutdown()


async def test_result_after_a_restart_reads_a_settled_runs_answer_from_the_childs_file(tmp_path: Path) -> None:
    """A run that settled and wrote no result file: ``result`` reads its last answer from the child's
    file after a restart, and pages it."""

    session, receipt = await _spawned(tmp_path, SHORT)
    await _start_fact(session)
    await _settle(session, receipt, "ok")
    service, jobs, _harness = await _restart(session)
    try:
        text, failed = await result_text(service, jobs, "a1b2c3d4e5f60718", offset=0, session=session)
        assert not failed and text.endswith("\n\nbefore \ufffd after") and "after a restart" in text
    finally:
        await service.shutdown()


_HALF_TOOL = '"tool_name": "read\\ud83d"'


@REAL_CHILD
async def test_a_tool_name_and_a_progress_line_with_a_half_are_whole_and_the_job_ends_done(
    tmp_path: Path,
) -> None:
    """The progress the job keeps reads U+FFFD for the half, its row too, and the job still ends
    ``done`` with one notice (Codex C3: ``failed`` with "client error: UnicodeEncodeError")."""

    gate = tmp_path / "gate"
    script = _script(SHORT, gate=gate, partial=f"thinking {HALF}").replace('"tool_name": "read"', _HALF_TOOL)
    rig = await _rig(tmp_path, script=script)
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
        job_id = rig.jobs()[0].job_id

        def progressed() -> bool:
            snap = rig.service.snapshot(job_id)
            progress = (snap.progress or {}) if snap is not None else {}
            return progress.get("tool") is not None and progress.get("last_text") is not None

        await wait_for(progressed)
        running = rig.service.snapshot(job_id)
        assert running is not None and running.progress is not None
        assert running.progress["tool"] == "read\ufffd"
        assert running.progress.get("last_text") == "thinking \ufffd", running.progress
        assert "  running read\ufffd" in rig.service.render_rows(running, 120)
        gate.write_text("go", encoding="utf-8")
        snap = await _settled(rig)
        assert snap.state == "done" and snap.brief == "before \ufffd after"
        assert len(_outcomes(list(await rig.session.get_entries()), job_id)) == 1
        assert len(rig.harness.undelivered_notices()) == 1
    finally:
        await rig.service.shutdown()


@REAL_CHILD
async def test_a_foreground_call_whose_child_names_a_tool_with_a_half_returns(tmp_path: Path) -> None:
    """The envelope's tool-trail count no longer raises on it (on ``639dcdf6`` the call raised
    ``UnicodeEncodeError``). The foreground result keeps the child's text as it was."""

    rig = await _rig(tmp_path, script=_script("the answer").replace('"tool_name": "read"', _HALF_TOOL))
    try:
        result = await rig.call({"profile": "scout", "task": "t"})
        assert not result.is_error and "the answer" in _text(result)
    finally:
        await rig.service.shutdown()


@REAL_CHILD
async def test_progress_with_no_tool_or_no_line_yet_reads_none(tmp_path: Path) -> None:
    """A progress update whose tool or last line is ``None`` is kept with ``None``, not dropped:
    before the child says anything its tool shows with no line, and once it has ended the job's
    last progress has no tool (verify-r2 F2, mutants J3 and J4). Passes on ``8f07404f``."""

    gate = tmp_path / "gate"
    rig = await _rig(tmp_path, script=_script(SHORT, gate=gate).replace('"tool_name": "read"', _HALF_TOOL))
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
        job_id = rig.jobs()[0].job_id

        def tool_shown() -> bool:
            snap = rig.service.snapshot(job_id)
            return snap is not None and (snap.progress or {}).get("tool") is not None

        await wait_for(tool_shown)
        running = rig.service.snapshot(job_id)
        assert running is not None and running.progress is not None
        assert running.progress["tool"] == "read\ufffd" and running.progress["last_text"] is None
        gate.write_text("go", encoding="utf-8")
        snap = await _settled(rig)
        assert snap.progress is not None and snap.progress["tool"] is None, snap.progress
    finally:
        await rig.service.shutdown()


def test_the_trail_is_bounded_by_utf8_bytes_with_a_half_counted_as_three() -> None:
    """The trail's budget is bytes (verify-r2 F4, mutant V2): 100 calls of three-byte characters,
    a half in each name, fit :data:`MAX_TRAIL_BYTES` by characters and not by bytes, so the trail
    keeps the lines that fit in bytes and says how many more there were. Passes on ``8f07404f``."""

    state = _StreamState()
    for i in range(100):
        start = {"type": "tool_execution_start", "tool_call_id": f"c{i}", "tool_name": f"읽{HALF}", "args": {"path": "가" * 30}}
        reduce_event(state, start)
        reduce_event(state, {"type": "tool_execution_end", "tool_call_id": f"c{i}", "is_error": False})
    line = f"읽{HALF}({'가' * 30}) ok"
    assert 100 * (len(line) + 1) <= MAX_TRAIL_BYTES < 100 * (len(line.encode("utf-8", "surrogatepass")) + 1)

    trail = render_tool_trail(state)
    assert trail is not None
    *kept, more = trail.split("\n")
    assert kept and set(kept) == {line}
    assert len(kept) == MAX_TRAIL_BYTES // (len(line.encode("utf-8", "surrogatepass")) + 1)
    assert more == f"… {100 - len(kept)} more"


def test_only_lone_surrogates_are_replaced() -> None:
    """Guard on the helper: a pair JSON joins, every other character, NUL and controls stay as they are."""

    kept = "a\x00b\x1b[31m 🙂 줄 \ufffd \U0010ffff"
    assert _utf8_safe(kept) == kept
    assert _utf8_safe(f"{HALF}x\udc00{HALF}\ude42") == "\ufffdx\ufffd\ufffd\ufffd"
    assert json.loads('"\\ud83d\\ude42"') == "🙂", "the stream's JSON joins a real pair before this runs"
