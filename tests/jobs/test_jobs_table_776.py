"""#776 (#611 S3b) — the ``/jobs`` table: ``/jobs list``, ``job(action="list")`` and non-TUI hosts (ADR-0299).

Measured before (pty, gate forced on, scripted model): ACTIVITY repeated the
title (``7s ago · agent slow · slow`` at 120x40) because the table showed a
kind's row 0, and at 80 columns the fixed columns left TITLE 5 cells (``slow:``,
``sleep``) — ``.omc/probes/611-s3-design/live/r2/snaps.txt``, ``live/r3/snaps.txt``.

Now ACTIVITY shows row 1 when the kind has one; before TITLE gets fewer than 20
cells AGE goes, and then ACTIVITY narrows; every cut is by terminal cells.

Fails on ``7e2feb50``: ACTIVITY holds row 0, TITLE gets 5 cells at 80, and a
line with a Hangul title is cut by characters, so it is wider than the width.
Guards: the header at 100 columns and more, and ``job(action="list")`` keeps the
table whatever the TUI hooks.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs import commands
from aelix_coding_agent.jobs.tool import create_job_tool
from rich.cells import cell_len

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")

_BASE_HEADER = f"  {'ID':<10}  {'KIND':<10} {'STATE':<12} {'AGE':<7} {'ACTIVITY':<26} TITLE"


class _Rows(_sleeper.SleeperAdapter):
    """The sleeper, with a row 1 of its own."""

    def render_rows(self, snapshot: Any, width: int) -> list[str]:
        return [f"sleeper {snapshot.title}", "phase sleeping"]


async def _service(tmp_path: Path, adapter: Any = None) -> Any:
    service = fast_service()
    if adapter is None:
        _sleeper.register(service)
    else:
        service.register_kind(adapter, _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    return service


async def _started(service: Any, title: str) -> str:
    job_id = await service.start(_sleeper.KIND, title=title, params={"seconds": 30})
    await wait_for(lambda: service.snapshot(job_id).state == "running")
    return str(job_id)


def _title_cells(header: str, row: str) -> int:
    at = header.index("TITLE")
    return cell_len(row[at:])


async def test_at_80_columns_title_keeps_20_cells_and_age_goes(tmp_path: Path) -> None:
    service = await _service(tmp_path)
    try:
        await _started(service, "count the .py files under src/ and say how many")
        header, row = commands.list_rows(service, width=80)
        assert "AGE" not in header
        assert _title_cells(header, row) >= commands.TITLE_MIN == 20, row
        assert cell_len(row) <= 80
        wide_header, _wide = commands.list_rows(service, width=120)
        assert "AGE" in wide_header
    finally:
        await service.shutdown()


async def test_activity_is_row_one_not_the_title_again(tmp_path: Path) -> None:
    service = await _service(tmp_path, _Rows(bound=4))
    try:
        await _started(service, "slow")
        _header, row = commands.list_rows(service, width=160)
        assert "phase sleeping" in row
        assert row.count("slow") == 1, "the title once, in TITLE"
    finally:
        await service.shutdown()


class _HangulActivity(_sleeper.SleeperAdapter):
    """Row 1 in Hangul, longer than ACTIVITY, for a title starting with ``k``."""

    def render_rows(self, snapshot: Any, width: int) -> list[str]:
        busy = "한국어 활동이 아주 길게 이어지는 줄입니다" if snapshot.title.startswith("k") else "phase sleeping"
        return [f"sleeper {snapshot.title}", busy]


@pytest.mark.parametrize("width", [80, 160])
async def test_title_starts_in_one_column_whatever_activity_holds(tmp_path: Path, width: int) -> None:
    """ACTIVITY is padded and cut by cells: a Hangul row 1 neither pushes TITLE right nor runs on."""

    service = await _service(tmp_path, _HangulActivity(bound=4))
    try:
        await _started(service, "ascii job")
        await _started(service, "korean job")
        header, *rows = commands.list_rows(service, width=width)
        column = cell_len(header[: header.index("TITLE")])
        for row, title in zip(rows, ("ascii job", "korean job"), strict=True):
            assert cell_len(row[: row.index(title)]) == column, (header, row)
        assert "한국어 활동" in rows[1] and "…" in rows[1] and "phase sleep" in rows[0]
    finally:
        await service.shutdown()


@pytest.mark.parametrize("width", [80, 100, 120, 160])
async def test_a_hangul_title_is_cut_by_cells(tmp_path: Path, width: int) -> None:
    service = await _service(tmp_path)
    try:
        await _started(service, "한국어 제목이 아주 길게 이어지는 작업의 이름입니다 " * 3)
        rows = commands.list_rows(service, width=width)
        assert all(cell_len(row) <= width for row in rows), [(cell_len(r), r) for r in rows]
        assert rows[1].endswith("…")
    finally:
        await service.shutdown()


@pytest.mark.parametrize("width", [100, 120, 160])
async def test_guard_the_header_from_100_columns_is_as_before(tmp_path: Path, width: int) -> None:
    service = await _service(tmp_path)
    try:
        await _started(service, "t")
        assert commands.list_rows(service, width=width)[0] == _BASE_HEADER
    finally:
        await service.shutdown()


async def test_guard_job_list_is_the_table_whatever_the_tui_hooks(tmp_path: Path) -> None:
    service = await _service(tmp_path)

    async def panel() -> str | None:
        raise AssertionError("the job tool opened the panel")

    service.panel_hook = panel
    try:
        await _started(service, "t")
        result = await create_job_tool(service).execute({"action": "list"})
        text = result.content[0].text  # type: ignore[union-attr]
        assert text.splitlines()[0] == _BASE_HEADER
    finally:
        await service.shutdown()


#: Strings ``jobs/cells.py`` must read as ``rich.cells.cell_len`` does (#776 fix round 1): CJK, fullwidth,
#: combining marks, U+FE0F emoji and keycaps (Codex M3, verify F1), zero width joiner families, skin
#: tones, controls, a lone surrogate, a decomposed Hangul syllable, zero-width and other format characters.
RICH_CORPUS = [
    "abc", "한국어 제목", "Ａｂ", "e\u0301t\u0301", "☺️", "❤️", "✔️", "⚠️", "♥️", "1️⃣", "#️⃣", "1\u20e3",
    "echo ❤️ ✔️ ⚠️ ☺️ ✌️ ☀️ done", "👨\u200d👩\u200d👧", "👩🏽\u200d💻", "🏳️\u200d🌈", "👨\u200d❤️\u200d👨",
    "a\u200db", "a\tb\x1b[31mc", "x\x7fy\x85z", "\ud83d", "a\ud83db", "🙂 ok", "→ path", "☰☷",
    "\u1100\u1161\u11a8", "x\u200by", "\u00ad", "\u0600x", "ก\u0e31", "❤️\ufe0f", "\U0001fae9 new emoji",
    "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f", "\u1100\ud7b0\ud7cb",
]

#: ``rich.cells.cell_len`` of each :data:`RICH_CORPUS` string under the ``rich`` versions the ``[tui]`` extra
#: also allows (``rich>=13.7,<15``) but the lock does not install, recorded with
#: ``uv run --isolated --no-project --with rich==X`` (#776 fix round 2,
#: ``.omc/probes/775-776/776-fix-r2/richver-out.txt``). 13.9.4, 14.0.0 and 14.1.0 read as 14.2.0 does.
RICH_RECORDED = {
    "13.7.1": [3, 11, 4, 2, 1, 1, 1, 1, 1, 1, 1, 1, 21, 6, 4, 3, 5, 2, 7, 3, 1, 3, 5, 6, 2, 2, 2, 0, 1, 1, 1, 11, 2, 2],
    "14.2.0": [3, 11, 4, 2, 1, 1, 1, 1, 1, 1, 1, 1, 21, 6, 4, 3, 5, 2, 7, 3, 1, 3, 5, 6, 2, 2, 2, 0, 1, 1, 1, 11, 2, 2],
}

#: Where ``cells.py`` counts more than ``rich`` 14.3.4: each side of a zero width joiner on its own (as ``rich``
#: before 14.3 does), and a control character as one (as ``rich`` 14.3.0 does).
_COUNTED_MORE = ("\u200d", "\t", "\x1b", "\x7f", "\x85")


@pytest.mark.parametrize("text", RICH_CORPUS)
def test_cells_reads_the_widths_rich_reads(text: str) -> None:
    from aelix_coding_agent.jobs import cells

    if any(mark in text for mark in _COUNTED_MORE):
        assert cells.cells(text) > cell_len(text)
    else:
        assert cells.cells(text) == cell_len(text)
    for width in range(1, cell_len(text) + 2):
        cut = cells.cut(text, width)
        assert cell_len(cut) <= width and (cut == text or cut.endswith("…")), (width, cut)


@pytest.mark.parametrize("version", sorted(RICH_RECORDED))
def test_cells_never_counts_fewer_than_an_older_rich_the_extra_allows(version: str) -> None:
    """Verify round 2 F2: on ``b27037db`` a zero width joiner joined the next character, as ``rich`` 14.3
    reads it, so ``👨‍👩‍👧`` was 2 cells where ``rich`` 13.7.1–14.2.0 reads 6 and a table row ran past the width."""

    from aelix_coding_agent.jobs import cells

    under = [(t, cells.cells(t), w) for t, w in zip(RICH_CORPUS, RICH_RECORDED[version], strict=True) if cells.cells(t) < w]
    assert under == []


def test_where_cells_and_rich_differ_cells_counts_more() -> None:
    """The one known difference: U+FE0F after a narrow character with no emoji form. And over
    random strings of the hard characters, ``cells`` is never under ``rich`` and every cut fits."""

    import random

    from aelix_coding_agent.jobs import cells

    assert (cells.cells("a\ufe0f"), cell_len("a\ufe0f")) == (2, 1)
    pool = [*"ab한国Ａ1#☺❤🏳🌈👨👩💻☰→ …", "\u0301", "\ufe0f", "\u200d", "\u20e3", "🏽", "\t", "\x1b", "\ud83d", "\u1161", "\u200b", "\u00ad"]
    rng = random.Random(776)
    for _ in range(2000):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(1, 12)))
        assert cells.cells(text) >= cell_len(text), repr(text)
        assert "".join(cells.clusters(text)) == text
        for width in (1, 2, 3, 5, 8):
            assert cell_len(cells.cut(text, width)) <= width, (repr(text), width)


def test_a_cut_reads_no_further_than_it_needs() -> None:
    """Verify round 2 N4: on ``b27037db`` a cut split the whole text into pieces and measured each prefix
    again (657 ms for 1 MB). Now it stops once the text is past the width; counted, not timed."""

    from aelix_coding_agent.jobs import cells

    seen = {"n": 0}
    real = cells.char_cells

    def counted(ch: str) -> int:
        seen["n"] += 1
        return real(ch)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cells, "char_cells", counted)
        shown = cells.cut("é" * 1_000_000, 40)
    assert shown == "é" * 39 + "…" and seen["n"] < 200, seen


def test_a_cut_never_splits_a_character_from_its_marks_or_a_joined_sequence() -> None:
    from aelix_coding_agent.jobs import cells

    assert cells.clusters("e\u0301x❤️y") == ["e\u0301", "x", "❤️", "y"]
    assert cells.clusters("👨\u200d👩\u200d👧x") == ["👨\u200d👩\u200d👧", "x"]
    family = "👨\u200d👩\u200d👧"  # six cells here: each side of a joiner on its own (fix round 2)
    assert [cells.cut(family * 3, w) for w in (4, 8, 13)] == ["…", family + "…", family * 2 + "…"]


async def test_a_kind_no_longer_registered_reads_as_its_title(tmp_path: Path) -> None:
    """Verify N5: the service's generic row is the title — the surfaces show the label themselves."""

    import dataclasses

    service = await _service(tmp_path)
    try:
        job_id = await _started(service, "nap")
        snap = dataclasses.replace(service.snapshot(job_id), kind="test:gone")
        assert service.render_rows(snap, 80) == ["nap"]
    finally:
        await service.shutdown()


def test_a_text_exactly_as_wide_as_the_width_is_not_cut() -> None:
    from aelix_coding_agent.jobs import cells

    assert [cells.cut(t, w) for t, w in (("abc", 3), ("한국", 4), ("❤️x", 3))] == ["abc", "한국", "❤️x"]
    assert cells.pad("❤️", 4) == "❤️  "


@pytest.mark.parametrize("width", [80, 120])
async def test_a_vs16_title_row_does_not_wrap_when_the_tui_prints_it(tmp_path: Path, width: int) -> None:
    """Verify F1 (``p3_table_wrap.py``): the table printed as the TUI prints it — a ``rich`` ``Text`` at the
    terminal's width — is one line per row."""

    from rich.console import Console
    from rich.text import Text

    service = await _service(tmp_path)
    try:
        await _started(service, "echo " + "❤️ ✔️ ⚠️ ☺️ ✌️ ☀️ " * 6 + "done")
        rows = commands.list_rows(service, width=width)
        out = io.StringIO()
        Console(file=out, width=width, color_system=None, force_terminal=False).print(Text("\n".join(rows)))
        assert len(out.getvalue().rstrip("\n").split("\n")) == len(rows)
        assert all(cell_len(row) <= width for row in rows)
    finally:
        await service.shutdown()


async def test_at_60_columns_activity_keeps_12_cells(tmp_path: Path) -> None:
    """Verify F7 K05: ACTIVITY narrows to no fewer than 12 cells."""

    service = await _service(tmp_path)
    try:
        await _started(service, "t")
        header = commands.list_rows(service, width=60)[0]
        assert cell_len(header[header.index("ACTIVITY") : header.index("TITLE")]) >= 13
    finally:
        await service.shutdown()


def test_a_cut_to_no_width_is_empty() -> None:
    """Verify F7 X07."""

    from aelix_coding_agent.jobs import cells

    assert (cells.cut("abc", 0), cells.pad("abc", 0)) == ("", "")


def test_a_mark_unicode_16_added_counts_one() -> None:
    """``rich`` before 14.3 gives U+0897 (and Unicode 16's other new marks) a cell; Python 3.14 reads it as
    a mark, which counted none here (#776 fix round 2: 36 such code points, measured)."""

    from aelix_coding_agent.jobs import cells

    assert cells.cells("a\u0897") >= 2
