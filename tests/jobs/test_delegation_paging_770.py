"""#770 — a background agent's result pages by bytes on every surface that reads it (ADR-0297 §4).

``job(action="result")`` and ``/jobs result`` pass a page's ``continue_at`` back
as the next ``offset``. Delegation's pager (``jobs_client.page``) reads its
result by UTF-8 bytes, so ``continue_at`` has to be a byte offset too.

Run with the REAL delegation adapter (``DelegationKindAdapter.output``) over a
job whose runner returns a result at once, so what is pinned is the adapter's
paging, not a child's.

Fails on ``363accf2``: ``page`` set no ``next_offset``, so ``continue_at`` was
``offset + len(text)`` — characters — and each page after the first began
inside bytes the page before had read; a limit smaller than the first
character read nothing and hinted the same offset again.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import jobs_client
from aelix_agents.jobs_client import KIND, DelegationJobs, DelegationKindAdapter
from aelix_coding_agent.jobs import commands
from aelix_coding_agent.jobs.contract import ClientOutcome, HelperCleanup
from aelix_coding_agent.jobs.tool import create_job_tool

from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")

# ASCII, three-byte Hangul and four-byte emoji, so a page boundary falls inside
# characters of every width; about 70 KB, more than four 16 KiB pages.
TEXT = "".join(f"줄 {i:04d}: 가나다라 🙂 mixed text\n" for i in range(2000))


class _Answered:
    """A runner whose agent answered ``TEXT`` at once: no process, nothing to settle."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        return ClientOutcome("done", "answered", {"text": TEXT, "agent_id": "sub-aaaaaaaaaaaa"})

    def request_stop(self, reason: str) -> None:
        pass

    async def settle(self) -> None:
        pass

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return []


async def _ended_agent(tmp_path: Path) -> tuple[Any, str]:
    service = fast_service()
    service.register_kind(DelegationKindAdapter(DelegationJobs(), session=lambda: None), _Answered)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(KIND, title="scout: count files")
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    return service, job_id


_HINT = re.compile(r"\n… more: (?:job\(action=\"result\", id=\"[0-9a-f]+\", offset=|/jobs result \S+ )(\d+)\)?$")


def _split(text: str) -> tuple[str, int | None]:
    found = _HINT.search(text)
    if found is None:
        return text, None
    return text[: found.start()], int(found.group(1))


def _read_whole(read: Any) -> tuple[list[str], list[int]]:
    """Every page from offset 0 to the last, following each hint; and the offsets asked."""

    pages: list[str] = []
    asked: list[int] = []
    offset: int | None = 0
    while offset is not None:
        assert len(asked) < 50, f"no progress: {asked[-5:]}"
        asked.append(offset)
        body, offset = _split(read(offset))
        pages.append(body)
    return pages, asked


def _assert_each_byte_once(pages: list[str], asked: list[int]) -> None:
    assert "".join(pages) == TEXT
    raw = TEXT.encode("utf-8")
    assert sum(len(p.encode("utf-8")) for p in pages) == len(raw), "every byte once"
    for at, page_text in zip(asked, pages, strict=True):
        assert raw[at : at + len(page_text.encode("utf-8"))] == page_text.encode("utf-8")
    assert len(pages) >= 4


async def test_the_job_tool_reads_a_multibyte_result_to_its_end_each_byte_once(tmp_path: Path) -> None:
    """``job(action="result")``'s hint is the byte after the page (#770).

    Fails on ``363accf2``: the second page starts at the first page's character
    count, inside bytes it already read, and the pages joined are longer than
    the result.
    """

    service, job_id = await _ended_agent(tmp_path)
    tool = create_job_tool(service)
    results: list[Any] = []

    async def collect() -> None:
        offset: int | None = 0
        while offset is not None:
            assert len(results) < 50
            result = await tool.execute({"action": "result", "id": job_id, "offset": offset})
            assert not result.is_error
            results.append((offset, result.content[0].text))  # type: ignore[union-attr]
            offset = _split(results[-1][1])[1]

    await collect()
    asked = [at for at, _text in results]
    pages = [_split(text)[0] for _at, text in results]
    _assert_each_byte_once(pages, asked)
    await service.shutdown()


async def test_jobs_result_and_agent_result_read_the_same_pages(tmp_path: Path) -> None:
    """``/jobs result`` (8 KiB pages) and ``agent(action="result")`` (16 KiB) read each byte once too."""

    service, job_id = await _ended_agent(tmp_path)
    snap = service.snapshot(job_id)
    assert snap is not None
    pages, asked = _read_whole(lambda at: commands.result_text(service, snap, at))
    _assert_each_byte_once(pages, asked)

    jobs = DelegationJobs()
    agent_pages: list[str] = []
    agent_asked: list[int] = []
    offset: int | None = 0
    while offset is not None:
        agent_asked.append(offset)
        text, failed = await jobs_client.result_text(service, jobs, job_id, offset=offset, session=None)
        assert not failed
        body, _sep, hint = text.partition("\n\n… more: ")
        agent_pages.append(body)
        offset = int(hint.rsplit("offset=", 1)[1].rstrip(")")) if hint else None
    _assert_each_byte_once(agent_pages, agent_asked)
    await service.shutdown()


@pytest.mark.parametrize(
    ("text", "offset", "limit", "expected"),
    [
        ("가나다", 0, 1, ("가", 0, 3, True)),  # a limit under the first character's three bytes
        ("가나다", 3, 2, ("나", 3, 6, True)),
        ("🙂x", 0, 3, ("🙂", 0, 4, True)),
        ("가나다", 1, 3, ("나", 3, 6, True)),  # an offset inside a character starts at the next
        ("가나다", 6, 3, ("다", 6, 9, False)),  # the last page: no more
        ("가나다", 9, 3, ("", 9, 9, False)),  # at the end
        ("ab", 0, 16 * 1024, ("ab", 0, 2, False)),
        ("", 0, 4, ("", 0, 0, False)),
    ],
)
def test_page_takes_whole_characters_and_names_the_next_byte(
    text: str, offset: int, limit: int, expected: tuple[str, int, int, bool]
) -> None:
    """ADR-0297 §4's rules on delegation's pager: progress, no split character, ``more`` false at the end.

    Fails on ``363accf2``: ``next_offset`` is ``None``, and a limit under the
    first character reads ``""`` with ``more`` true.
    """

    page = jobs_client.page(text, offset=offset, limit=limit)
    assert (page.text, page.offset, page.next_offset, page.more) == expected
    assert page.continue_at == expected[2]
