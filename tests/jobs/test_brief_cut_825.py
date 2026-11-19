"""#825 — a cut brief ends at a word and in ``…``, within its bound.

``aelix_agents.jobs_client._brief`` cut a background agent's summary at 160
characters and ``jobs/notice.cut_brief`` cut the ``◆ background result`` line at
256 UTF-8 bytes, both mid-word and with no mark: the #123 live check showed
"… The second step remains undone per your instructions. Stoppin". Both now end
in ``…`` inside their bound, at the last whitespace before the cut, and never
split a character; only one unbroken token is cut hard.

Fails on ``1c2a4a7c``: ``_brief`` returns the 160 characters ending "Stoppin",
``cut_brief`` returns 256 bytes with no ``…``, and the transcript line and the
``agent(status)`` row show the same.

Fix round 1 (Codex L12, verify F2): the cut looked back only 24 characters, and
for an ASCII space only, so "a"×130 + " " + "b"×50 kept 28 "b"s and a tab or an
em space was no boundary. Fails on ``1099de2b``: the ``far-space``, ``tab``,
``em-space`` and ``backoff-25`` rows.

Fix round 2 (lane b7f verify round 2, N2; the lead's call): ending at any
whitespace however far back dropped a long token whole ("Failed: /very/long/path…"
read "Failed:…"). A cut now ends at the last whitespace only when what it keeps
measures at least two thirds of the bound (107 of 160 characters, 171 of 256
bytes); otherwise the token is cut hard. Fails on ``a3c8b9a6``: the
``long-token``, ``two-thirds-miss`` and ``cjk-after-words`` rows of ``_brief``, and
the ``long-url``, ``bytes-miss`` and ``cjk-bytes-miss`` rows of ``cut_brief``, and
``nine-of-fifteen`` in the edges.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agents.jobs_client import _brief, _status_line
from aelix_coding_agent.jobs import notice as nt
from aelix_coding_agent.jobs.contract import ClientOutcome, JobSnapshot
from aelix_coding_agent.subagent_contract import SubagentResult

from tests.jobs._sleeper import KIND, SleeperAdapter
from tests.jobs.conftest import RecordingModel, fast_service, make_harness, make_session, wait_for
from tests.jobs.test_attention_123 import _Held

#: The #123 live check's real summary (``openai/gpt-4.1-mini``), as #825 quotes it, and its tail.
LIVE = (
    "I ran step 1 (sleep 15; date +%s) and step 3 (sleep 10; echo third) as requested, "
    "skipping step 2. The second step remains undone per your instructions. Stopping here "
    "as you asked."
)
LIVE_BRIEF = (
    "I ran step 1 (sleep 15; date +%s) and step 3 (sleep 10; echo third) as requested, "
    "skipping step 2. The second step remains undone per your instructions.…"
)

_TITLE = "scout: Do these steps strictly one at a time"
#: What 256 bytes leave of the doubled summary behind ``sleeper "<title>" stopped: ``, cut at a word.
_CUT_TAIL = f"{LIVE} I ran…"
#: verify round 2's N2 line: a failed agent's notice brief whose only long part is a URL.
_URL_LINE = 'agent "scout: t" failed: See https://example.com/' + "a" * 300


def _result(summary: str) -> SubagentResult:
    return SubagentResult(id="a", profile="scout", ok=False, status="aborted", summary=summary)


def test_a_summary_over_160_characters_ends_at_a_word_and_in_an_ellipsis() -> None:
    assert len(LIVE) > 160
    assert _brief(_result(LIVE)) == LIVE_BRIEF
    assert len(LIVE_BRIEF) <= 160


@pytest.mark.parametrize(
    ("summary", "brief"),
    [
        ("x" * 160, "x" * 160),  # at the bound: not cut, no mark
        ("x" * 161, "x" * 159 + "…"),  # one token: every character the bound allows
        ("한" * 200, "한" * 159 + "…"),
        ("word " * 31 + "abcdefgh", ("word " * 31).rstrip() + "…"),  # the cut lands on a space
        ("a" * 150 + " " + "b" * 30, "a" * 150 + "…"),  # back off to the space before the cut word
        ("a" * 130 + " " + "b" * 50, "a" * 130 + "…"),  # however far back the space is (Codex L12)
        ("a" * 150 + " " + "b" * 8 + " " + "c" * 10, "a" * 150 + " " + "b" * 8 + "…"),  # cut before a space
        ("a" * 135 + " " + "b" * 40, "a" * 135 + "…"),
        ("a" * 134 + " " + "b" * 40, "a" * 134 + "…"),  # 25 back: the old limit's first miss
        ("a" * 130 + "\u2003" + "b" * 50, "a" * 130 + "…"),  # any Unicode whitespace
        # 59 characters before the CJK run: under two thirds of 160, so the run is cut hard (N2).
        ("한글 " * 20 + "한" * 120, "한글 " * 20 + "한" * 99 + "…"),
        (" " + "x" * 200, "x" * 159 + "…"),  # the summary is stripped first: one token, cut hard
        # Fix round 2 (N2): the whitespace ends it only when that keeps 107 of the 160 characters.
        ("Failed: " + "/very/long/path" * 20, ("Failed: " + "/very/long/path" * 20)[:159] + "…"),
        ("a" * 107 + " " + "b" * 100, "a" * 107 + "…"),  # 107 kept: two thirds, ends at the word
        ("a" * 106 + " " + "b" * 100, "a" * 106 + " " + "b" * 52 + "…"),  # 106: the word is cut
        ("한" * 107 + " " + "b" * 100, "한" * 107 + "…"),  # characters, not bytes
    ],
    ids=[
        "at-bound", "one-token", "cjk", "on-space", "back-off", "far-space",
        "word-ends-at-cut", "backoff-24", "backoff-25", "em-space", "cjk-after-words", "leading-blank",
        "long-token", "two-thirds-hit", "two-thirds-miss", "cjk-two-thirds",
    ],
)
def test_the_brief_cut_shapes(summary: str, brief: str) -> None:
    assert _brief(_result(summary)) == brief
    assert len(brief) <= 160


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("x" * 256, "x" * 256),
        ("x" * 257, "x" * 253 + "…"),
        ("한" * 85 + "x", "한" * 85 + "x"),  # 256 bytes exactly
        ("한" * 86, "한" * 84 + "…"),  # 258 bytes: 252 + 3, no half character
        ("word " * 60, ("word " * 50).rstrip() + "…"),
        ("one\ntwo", "one two"),
        ("a" * 220 + "\t" + "🐍" * 42, "a" * 220 + "…"),  # Codex L12: a tab is a boundary
        ("a" * 220 + "\u2003" + "🐍" * 42, "a" * 220 + "…"),  # and so is an em space
        # Fix round 2 (N2): the whitespace ends it only when that keeps 171 of the 256 bytes.
        (_URL_LINE, _URL_LINE.encode()[:253].decode() + "…"),
        ("a" * 171 + " " + "b" * 100, "a" * 171 + "…"),
        ("a" * 170 + " " + "b" * 100, "a" * 170 + " " + "b" * 82 + "…"),
        ("한" * 57 + " " + "b" * 100, "한" * 57 + "…"),  # 57 characters are 171 bytes: bytes, not characters
        ("한" * 56 + "x" * 2 + " " + "b" * 100, "한" * 56 + "xx " + "b" * 82 + "…"),  # 170 bytes: cut
    ],
    ids=[
        "at-bound", "one-over", "cjk-at-bound", "cjk-over", "words", "newline", "tab", "em-space",
        "long-url", "bytes-hit", "bytes-miss", "cjk-bytes-hit", "cjk-bytes-miss",
    ],
)
def test_cut_brief_marks_a_cut_inside_256_bytes(text: str, expect: str) -> None:
    got = nt.cut_brief(text)
    assert got == expect
    assert len(got.encode("utf-8")) <= 256


@pytest.mark.parametrize(
    ("text", "limit", "expect"),
    [
        (" abcdefghij", 8, " abcd…"),  # the only space is the first character: nothing is dropped
        ("ab  cdefgh", 7, "ab…"),  # backing off leaves no trailing blank before the mark
        ("ab \ufffd", 6, "ab \ufffd"),  # a replaced surrogate is three bytes, as it is written
        # The cut lands inside "klmnop": 10 of 15 bytes kept is exactly two thirds, so it ends at the word.
        ("abcdefghij klmnop", 15, "abcdefghij…"),
        ("abcdefghi jklmnop", 15, "abcdefghi jk…"),  # 9 of 15: under two thirds, the word is cut
        ("abcdefghij  klmnop", 15, "abcdefghij…"),  # two blanks before the cut word: neither is kept
    ],
    ids=[
        "leading-space", "trailing-blank", "surrogate-bytes", "ten-of-fifteen", "nine-of-fifteen", "two-blanks",
    ],
)
def test_cut_marked_edges(text: str, limit: int, expect: str) -> None:
    assert nt.cut_marked(text.replace("\ufffd", "\udcff"), limit) == expect


class _LongBrief(_Held):
    """``_Held`` whose outcome carries a brief no kind cut: the live summary twice."""

    async def run(self, ctx: Any) -> ClientOutcome:
        outcome = await super().run(ctx)
        return ClientOutcome(outcome.status, f"{LIVE} {LIVE}", outcome.payload)


async def test_the_background_result_line_ends_in_an_ellipsis(tmp_path: Path) -> None:
    from aelix_coding_agent.tui.jobs_ui import JobsUi

    from tests.tui.test_notice_never_touches_dialogs_611 import _Chrome, _Host

    harness = make_harness(await make_session(tmp_path), RecordingModel())
    service = fast_service()
    service.register_kind(SleeperAdapter(), _LongBrief)
    await service.bind(harness)
    lines: list[str] = []
    ui = JobsUi(service, chrome=_Chrome(), runtime_host=_Host(harness), commit=lines.append)
    job_id = await service.start(KIND, title="scout: Do these steps strictly one at a time")
    runner = _Held.instances[-1]
    runner.request_stop("test")
    await wait_for(lambda: service.snapshot(job_id).notice_state == "pending")
    await harness.prompt_notices()
    assert lines == [f'◆ background result: sleeper "{_TITLE}" stopped: {_CUT_TAIL}'], lines
    assert lines[0].endswith("…")
    assert len(lines[0].removeprefix("◆ background result: ").encode("utf-8")) <= 256
    ui.close()
    await service.shutdown()


def test_the_agent_status_row_shows_the_marked_brief() -> None:
    snap = JobSnapshot(
        job_id="0123456789abcdef",
        kind="agent",
        label="agent",
        title="scout: count",
        state="stopped",
        owner="s",
        created=0.0,
        elapsed=15.6,
        last_activity_ago=None,
        quiet=False,
        result_waiting=True,
        notice_state="pending",
        brief=_brief(_result(LIVE)),
    )
    row = _status_line(SimpleNamespace(info={}), snap)  # type: ignore[arg-type]
    assert row.endswith(f"{LIVE_BRIEF} — result waiting"), row
