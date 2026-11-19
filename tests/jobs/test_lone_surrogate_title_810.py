"""#810 — a job whose title holds a lone surrogate ends with its outcome and its notice.

``jobs/notice.py`` cut the title, the brief and the body by UTF-8 bytes, and a lone
surrogate (``"\\udcff"``, a legal ``str``) cannot be encoded: ``frame`` and
``cut_brief`` raised ``UnicodeEncodeError`` in ``JobService._frame_notice``, the
job's drive task died, and the job read ``done``/``stopped`` with no ``outcome``
fact and no notice; ``-p`` ended without giving the model the result. Every cut
in ``notice.py`` now replaces a lone surrogate with U+FFFD first (``utf8_safe``,
as ``jobs_client._utf8_safe`` does for a child's text since #782).

Fails on ``1c2a4a7c``: the unit row raises ``UnicodeEncodeError``; the service
rows read ``notice_state == "none"`` with no ``outcome`` fact; the bash adapter's
``render_notice`` raises; the ``-p`` row sends the model two requests, not three.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aelix_coding_agent.jobs import notice as nt
from aelix_coding_agent.jobs.contract import ClientOutcome
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE
from aelix_coding_agent.tools.bash_background import BashKindAdapter

from tests.jobs._sleeper import KIND
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for
from tests.jobs.test_attention_123 import _Held, _running
from tests.jobs.test_gated_cli_611 import ModelServer, Scratch, all_text, call, say

HALF = "half \ud800 x"


def test_no_cut_in_the_notice_module_raises_on_a_lone_surrogate() -> None:
    framed = nt.frame(
        job_id="j\udcff",
        kind="k\ud800",
        label="sleeper\udfff",
        title=HALF,
        status="done",
        elapsed=1.0,
        body="answer \udcff" * 4000,
        next_action="next \ud800",
    )
    assert "\ufffd" in framed and not any("\ud800" <= ch <= "\udfff" for ch in framed)
    framed.encode("utf-8")
    assert len(framed.encode("utf-8")) <= nt.NOTICE_MAX_BYTES - nt.PLACEMENT_LINE_RESERVE
    assert nt.cut_brief(f'Bash "{HALF}" done') == 'Bash "half \ufffd x" done'
    assert nt.cut_placement_line("line \udcff") == "line \ufffd"
    key = nt.extension_notice_key("ext\ud800", "id\udcff")
    assert key == "ext~:657874eda080:6964edb3bf", "identity apart from display (fix round 1, L9)"
    key.encode("ascii")
    assert nt.escape_body("a\ud800b") == "a\ufffdb"


def test_a_surrogate_notice_key_keeps_as_many_bytes_per_part_as_a_valid_key() -> None:
    """Fix round 2 (lane b7f verify round 2: N1, and the D1 and D4 mutants).

    Each part of an ``ext~:`` key is the hex of its first 128 bytes, as many as a
    valid key keeps, so two ids differing at byte 100 are two notices; they differ
    only after byte 128, they are one, as two valid ids are. A surrogate only in
    the extension's name writes the key apart too. Fails on ``a3c8b9a6``: the ids
    differing at byte 100 gave one key (it kept 64 bytes a part). The surrogates are
    built with ``chr`` so no lone surrogate sits in this module's source text.
    """

    high, low = chr(0xD800), chr(0xDCFF)
    stem = low + "x" * 96  # 3 bytes, then 96: byte 100 is the next
    assert nt.extension_notice_key("e", stem + "a") != nt.extension_notice_key("e", stem + "b")
    valid = "x" * 99
    assert nt.extension_notice_key("e", valid + "a") != nt.extension_notice_key("e", valid + "b")
    long = low + "x" * 125  # 128 bytes: anything after it is past the bound
    assert nt.extension_notice_key("e", long + "a") == nt.extension_notice_key("e", long + "b")
    assert nt.extension_notice_key("e", "x" * 128 + "a") == nt.extension_notice_key("e", "x" * 128 + "b")
    key = nt.extension_notice_key(high * 1000, low * 1000)
    assert key.startswith("ext~:") and len(key) == len("ext~:") + 2 * 256 + 1, len(key)
    named = nt.extension_notice_key("ext" + high, "id")
    assert named == "ext~:657874eda080:6964" and named != nt.extension_notice_key("ext\ufffd", "id")


def test_the_bash_adapter_renders_a_command_with_a_lone_surrogate() -> None:
    adapter = BashKindAdapter()
    payload = {"command": "echo m810half \udcff", "bytes": 12, "tail": "m810half \ufffd\n", "exit_code": 0}
    body = adapter.render_notice(ClientOutcome("done", "exit 0", payload))
    framed = nt.frame(
        job_id="j", kind=adapter.name, label=adapter.label, title="t", status="done", elapsed=1.0, body=body.body
    )
    assert "$ echo m810half \ufffd" in framed


@pytest.mark.parametrize("how", ["stop", "itself"])
async def test_a_job_titled_with_a_lone_surrogate_records_its_outcome_and_notice(
    tmp_path: Path, how: str
) -> None:
    service, harness, _model, _first, _first_runner = await _running(tmp_path)
    job_id = await service.start(KIND, title=HALF)
    runner = _Held.instances[-1]
    if how == "stop":
        runner.request_stop("test")
    else:
        runner.release.set()
    await wait_for(lambda: service.snapshot(job_id).notice_state != "none", timeout=5.0)
    snap = service.snapshot(job_id)
    assert (snap.state, snap.notice_state) == ("stopped" if how == "stop" else "done", "pending")
    entries = await harness.session.get_entries()
    facts = [e.data for e in entries if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]
    assert [f["fact"] for f in facts if f["job_id"] == job_id].count("outcome") == 1
    [notice] = [n for n in harness.undelivered_notices() if n.notice_id == nt.final_notice_id(job_id)]
    assert 'Background sleeper "half \ufffd x"' in notice.body
    assert notice.brief.startswith('sleeper "half \ufffd x" ')
    _first_runner.release.set()
    await service.shutdown()


async def test_a_running_job_titled_with_a_lone_surrogate_posts_its_attention_notice(
    tmp_path: Path,
) -> None:
    from aelix_coding_agent.jobs.contract import NoticeBody

    service, harness, _model, _first, first_runner = await _running(tmp_path)
    job_id = await service.start(KIND, title=HALF)
    runner = _Held.instances[-1]
    await wait_for(lambda: runner.ctx is not None)
    assert runner.ctx.attention(NoticeBody("last call: grep \udcff"), brief="looks stuck \ud800")
    [notice] = [n for n in harness.undelivered_notices() if n.notice_id == nt.attention_notice_id(job_id)]
    assert "half \ufffd x" in notice.body and "grep \ufffd" in notice.body
    assert notice.brief == 'sleeper "half \ufffd x" still running: looks stuck \ufffd'
    runner.release.set()
    first_runner.release.set()
    await service.shutdown()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX spawn passes only U+DC80-U+DCFF (fsencode surrogateescape) and the command is sh",
)
def test_print_mode_gives_the_model_the_result_of_a_command_with_a_lone_surrogate(
    tmp_path: Path,
) -> None:
    def parent(_body: dict, index: int) -> dict:
        if index == 0:
            return call("bash", {"command": "sleep 1; echo m810half \udcff", "run_in_background": True})
        return say(f"parent turn {index}")

    scratch = Scratch(tmp_path)
    with ModelServer(parent) as server:
        scratch.point_at(server)
        done = scratch.run("-p", "go", launcher=None)
    assert done.returncode == 0, done.stderr[-3000:]
    assert len(server.parent) == 3, (len(server.parent), done.stderr[-3000:])
    third = all_text(server.parent[2])
    assert "<background-job-result" in third and "m810half" in third, third[-2000:]
    assert "Task exception was never retrieved" not in done.stderr
    assert "render_notice failed" not in done.stderr


async def test_two_extension_notices_a_surrogate_tells_apart_stay_two(tmp_path: Path) -> None:
    """Codex review of #810, L9: the U+FFFD in display text made ``"id\\udcff"`` and
    ``"id\\ufffd"`` one key, so the second notice was refused as a duplicate. Fails
    on ``1099de2b``: the second ``post_extension_notice`` returns ``False``."""

    service = fast_service()
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    try:
        assert service.post_extension_notice("example", "id\udcff", "one")
        assert service.post_extension_notice("example", "id\ufffd", "two")
        assert not service.post_extension_notice("example", "id\udcff", "again"), "still one id per notice"
        keys = [n.notice_id for n in harness.undelivered_notices()]
        assert keys == ["ext~:6578616d706c65:6964edb3bf", "ext:example:id\ufffd"], keys
        for notice in harness.undelivered_notices():
            notice.body.encode("utf-8")
            assert "id\ufffd" in notice.brief
    finally:
        await service.shutdown()
