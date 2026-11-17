"""#776 (#611 S3b) — bare ``/jobs`` opens the background-jobs panel (ADR-0299).

Owner decisions (2026-11-12): OQ-S3-1 "B: /jobs로 여는 패널 (권장)"; OQ-S3-2
"↑↓·Enter·r·s s·Esc/q (권장)". The ``s s`` arm is contract r2 §4.2.

The panel is ``tui/jobs_panel.py`` over the generic ``tui/list_panel.py``. The
jobs are a real :class:`~aelix_coding_agent.jobs.service.JobService` running an
in-process kind of this file (no child process). Keys go through a running
chrome's real key processor (pipe input) wherever the case is about a key; the
Esc case runs 25 times in one process (the TUI key race, memory 2026-11-05).

Fails on ``7e2feb50``: there is no ``tui/jobs_panel.py`` and bare ``/jobs``
prints the table.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from _polling import quit_within, wait_until  # sibling helper (pytest prepend import mode)
from aelix_coding_agent.jobs import activation, commands
from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.jobs.contract import (
    ClientOutcome,
    HelperCleanup,
    JobSnapshot,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    RelationSpec,
)
from aelix_coding_agent.tui import jobs_panel
from aelix_coding_agent.tui.jobs_panel import WAITING, JobsPanel
from aelix_coding_agent.tui.jobs_ui import JobsUi
from aelix_coding_agent.tui.list_panel import ListPanel, PanelLayout, PanelRow
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.cells import cell_len

from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

KIND = "test:panel"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setattr(activation, "_switched_off", False)


class _Adapter:
    """Row 0 is the title; row 1 what the runner last said it does (when it said)."""

    name = KIND
    version = 1
    label = "panel"
    settings_key = "panel"
    capabilities = KindCapabilities(default_bound=32, notify=True, reads_persisted=True)

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        fact = client.get("fact", {})
        said = next(
            (str(r.get("said", "")) for r in client.get("records", []) if r.get("custom_type") == "test.panel"),
            "",
        )
        return ClientOutcome(status=fact.get("status", "done"), brief=str(fact.get("brief", "")), payload={"said": said})

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody:
        return NoticeBody(f"it said {outcome.payload.get('said', '')}")

    async def finalize_at_placement(self, outcome: ClientOutcome, branch: list[Any]) -> str | None:
        return None

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        doing = (snapshot.progress or {}).get("doing")
        return [snapshot.title, *([str(doing)] if doing else [])]

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        text = str(job.payload.get("said", ""))
        return OutputSlice(text[offset : offset + limit], offset, len(text), offset + limit < len(text))

    async def recover(self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]) -> Any:
        return None

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec:
        return RelationSpec()

    def restored_note(self, facts: Mapping[str, Any]) -> str | None:
        return None


class _Runner:
    """Runs until the test ends it (:func:`_end`) or a stop is asked; records what it said."""

    gates: dict[str, asyncio.Event] = {}  # noqa: RUF012 — one per test (cleared by _rig)
    stops: list[str] = []  # noqa: RUF012

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.said = str(params.get("said", ""))
        self.doing = params.get("doing")
        self.gate = asyncio.Event()
        _Runner.gates[self.said] = self.gate
        self.stopped = False

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        if self.doing:
            ctx.progress({"doing": self.doing})
        await self.gate.wait()
        status = "stopped" if self.stopped else "done"
        return ClientOutcome(status, f"said {len(self.said)} characters", {"said": self.said})

    def request_stop(self, reason: str) -> None:
        _Runner.stops.append(self.said)
        self.stopped = True
        self.gate.set()

    async def settle(self) -> None:
        pass

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return [("test.panel", {"said": self.said})]


class _Rig:
    def __init__(self, service: Any, harness: Any) -> None:
        self.service = service
        self.harness = harness

    async def start(self, said: str, *, title: str | None = None, doing: str | None = None, notify: bool = False) -> str:
        params = {"said": said, "doing": doing}
        job_id = await self.service.start(KIND, title=title or said, params=params, notify=notify)
        await wait_for(lambda: self.service.snapshot(job_id).state == "running")
        return str(job_id)

    async def end(self, said: str) -> None:
        _Runner.gates[said].set()

    def state(self, job_id: str) -> str:
        snap = self.service.snapshot(job_id)
        return "gone" if snap is None else snap.state


@contextlib.asynccontextmanager
async def _rig(tmp_path: Path, **kwargs: Any) -> AsyncIterator[_Rig]:
    _Runner.gates.clear()
    _Runner.stops.clear()
    service = fast_service(**kwargs)
    service.register_kind(_Adapter(), _Runner)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    try:
        yield _Rig(service, harness)
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


class _Stub:
    """A chrome for the cases that draw and press without a terminal: the panel is on top."""

    def __init__(self) -> None:
        self.covered = False

    def invalidate(self) -> None:
        pass

    def modal_on_top(self, _content: Any) -> bool:
        return not self.covered


def _plain(lines: list[str]) -> list[str]:
    return [_ANSI.sub("", line) for line in lines]


def _opened(panel: JobsPanel) -> JobsPanel:
    """As ``run`` leaves it once mounted: rows read, a window to be on top."""

    panel.list.window = Window(FormattedTextControl("", focusable=True))
    panel.list.refresh()
    return panel


# --- layout ----------------------------------------------------------------------


@pytest.mark.parametrize("width", [120, 80])
async def test_the_rows_are_laid_out_in_cells(tmp_path: Path, width: int) -> None:
    """Each row in ``width`` cells; row 0 and the age line up whatever the titles' scripts."""

    async with _rig(tmp_path) as rig:
        await rig.start("a", title="count the .py files under src/", doing="reading src")
        await rig.start("b", title="한국어 제목이 아주 길게 이어지는 작업의 이름입니다 그리고 더 길게")
        await rig.start("c", title="sleep 300 && echo m611s3marker")
        panel = _opened(JobsPanel(rig.service, _Stub(), rows=40))
        lines = _plain(panel.list.lines(width))
    assert all(cell_len(line) <= width for line in lines), [(cell_len(x), x) for x in lines]
    rows = lines[1:4]
    starts = {cell_len(row[: row.index(head)]) for row, head in zip(rows, ("count", "한국어", "sleep"), strict=True)}
    assert len(starts) == 1, rows  # row 0 starts in one column
    assert "…" in rows[1], "the Hangul title is cut, by cells"
    if width >= 100:
        assert all(" running " in row for row in rows), "the state column is shown"
        assert rows[0].rstrip().endswith("reading src"), "the activity is row 1"
    else:
        assert not any(" running " in row for row in rows), "at 80 row 0 would get under 20 cells: the state column goes first"


@pytest.mark.parametrize(("terminal", "height"), [(40, 9), (60, 9), (24, 5), (39, 5)])
async def test_the_height_is_fixed_while_it_is_open(tmp_path: Path, terminal: int, height: int) -> None:
    """3 job rows, 3 detail rows and the key row from 40 lines; 3 job rows and the key row below."""

    async with _rig(tmp_path) as rig:
        panel = _opened(JobsPanel(rig.service, _Stub(), rows=terminal))
        assert panel.list.model.layout.height == height
        seen = {len(panel.list.lines(100))}
        for said in "abcde":
            await rig.start(said)
            panel.list.refresh()
            seen.add(len(panel.list.lines(100)))
        await rig.end("a")
        await wait_for(lambda: rig.state(rig.service.snapshots()[0].job_id) == "done")
        panel.list.refresh()
        seen.add(len(panel.list.lines(100)))
    assert seen == {height}


async def test_k_of_n_shows_and_the_list_scrolls_with_the_selection(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        ids = [await rig.start(said) for said in "abcde"]
        panel = _opened(JobsPanel(rig.service, _Stub(), rows=24))
        assert "1/5" in _plain(panel.list.lines(100))[0]
        for _ in range(4):
            panel.list.press("down")
        lines = _plain(panel.list.lines(100))
        assert "5/5" in lines[0]
        assert [row[4:10] for row in lines[1:4]] == [i[:6] for i in ids[2:]], "the last three, scrolled"
        assert lines[3].startswith("▸"), "the selection is in view"
        panel.list.press("up")
        assert "4/5" in _plain(panel.list.lines(100))[0]
        panel.list.press("down")
        panel.list.press("down")  # held at the end
        assert "5/5" in _plain(panel.list.lines(100))[0]
        one = _opened(JobsPanel(rig.service, _Stub(), rows=24))
        one.list.model.layout = PanelLayout(rows=5)
        assert "/5" not in _plain(one.list.lines(100))[0], "no k/N when every job fits"


# --- the s s arm ------------------------------------------------------------------


async def test_s_s_stops_the_selected_job(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        b = await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("down")
        panel.list.press("s")
        assert panel.arm is not None and panel.arm.job_id == b
        assert "press s again to stop" in _plain(panel.list.lines(100))[-1]
        assert _Runner.stops == [], "one s stops nothing"
        panel.list.press("s")
        assert _Runner.stops == ["b"]
        await wait_for(lambda: rig.state(b) == "stopped")
        assert rig.state(a) == "running" and panel.arm is None


async def test_a_second_s_after_the_armed_job_left_stops_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract r2 §4.2: A leaves, B takes its row; the second ``s`` must not stop B."""

    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        b = await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("s")
        assert panel.arm is not None and panel.arm.job_id == a
        real = rig.service.snapshots
        monkeypatch.setattr(rig.service, "snapshots", lambda *a_, **k: [s for s in real(*a_, **k) if s.job_id != a])
        panel.list.refresh()  # the 1 s redraw
        assert panel.list.model.selected == b, "the selection moved to the row now at A's place"
        assert panel.arm is None
        panel.list.press("s")
        assert _Runner.stops == [] and rig.state(b) == "running" and rig.state(a) == "running"
        assert panel.arm is not None and panel.arm.job_id == b, "it armed again, on B"


async def test_a_second_s_before_any_redraw_after_the_job_left_stops_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same, with no redraw between: the second press reads the jobs itself."""

    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("s")
        real = rig.service.snapshots
        monkeypatch.setattr(rig.service, "snapshots", lambda *a_, **k: [s for s in real(*a_, **k) if s.job_id != a])
        panel.list.press("s")
        assert _Runner.stops == []


@pytest.mark.parametrize(
    "between",
    ["nothing", "almost", "move", "cover", "covered-at-redraw", "reload", "timeout", "exactly-3s", "ended"],
)
async def test_the_arm_goes_on_move_cover_reload_timeout_and_end(tmp_path: Path, between: str) -> None:
    """``nothing`` and ``almost`` (2.999 s) are the positive controls: the same two presses stop the
    job. Closing: :func:`test_closing_or_a_dialog_over_it_drops_the_arm_in_the_tui`."""

    now = [100.0]
    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        await rig.start("b")
        chrome = _Stub()
        panel = _opened(JobsPanel(rig.service, chrome, clock=lambda: now[0]))
        panel.list.press("s")
        assert panel.arm is not None
        if between == "move":
            panel.list.press("down")
            panel.list.press("up")
        elif between == "cover":
            chrome.covered = True
            panel.on_modal_change()  # a dialog mounted over it
            chrome.covered = False
            panel.on_modal_change()  # and went
        elif between == "covered-at-redraw":
            chrome.covered = True
            panel.list.refresh()  # a redraw while a dialog is over it, no modal-change call
            chrome.covered = False
        elif between == "reload":
            await rig.service.bind(make_harness(rig.harness.session), reason="reload")
        elif between == "timeout":
            now[0] += 3.1  # contract r2 §4.2: 3 s
        elif between == "exactly-3s":
            now[0] += 3.0  # Codex L4: the arm holds BELOW 3 s, not at it
        elif between == "almost":
            now[0] += 2.999
        elif between == "ended":
            await rig.end("a")
            await wait_for(lambda: rig.state(a) == "done")
        panel.list.refresh()  # the 1 s redraw
        assert (panel.arm is not None) is (between in ("nothing", "almost")), between
        panel.list.press("s")
        if between in ("nothing", "almost"):
            assert _Runner.stops == ["a"]
        else:
            assert _Runner.stops == [], between
            assert rig.state(a) == ("done" if between == "ended" else "running")


async def test_a_session_change_stops_nothing(tmp_path: Path) -> None:
    """The arm's owner is the session it was armed in; after a change it is not the service's."""

    async with _rig(tmp_path) as rig:
        await rig.start("a")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("s")
        other = make_harness(await make_session(tmp_path, "t.jsonl", session_id="t"))
        await rig.service.bind(other, reason="new")
        panel.list.press("s")
        assert _Runner.stops == []


async def test_s_on_an_ended_job_arms_nothing(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        await rig.end("a")
        await wait_for(lambda: rig.state(a) == "done")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("s")
        assert panel.arm is None and f"{a[:6]} has ended" in _plain(panel.list.lines(100))[-1]


# --- Enter, r --------------------------------------------------------------------------


async def test_enter_writes_the_detail_above_and_keeps_the_panel(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        a = await rig.start("a", doing="reading files")
        said: list[str] = []
        panel = _opened(JobsPanel(rig.service, _Stub(), emit=said.append))
        panel.list._result = asyncio.get_running_loop().create_future()
        panel.list.press("enter")
        snap = rig.service.snapshot(a)
        assert said == ["\n".join(commands.detail(rig.service, snap))]
        assert not panel.list._result.done(), "still open"


async def test_r_closes_the_panel_with_the_result_page(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        a = await rig.start("the answer is 42")
        await rig.end("the answer is 42")
        await wait_for(lambda: rig.state(a) == "done")
        panel = JobsPanel(rig.service, _Stub())
        loop = asyncio.get_running_loop()
        panel.list._result = loop.create_future()
        _opened(panel).list.press("r")
        assert panel.list._result.result() == commands.result_text(rig.service, rig.service.snapshot(a))
        assert panel.list._result.result() == "the answer is 42"


async def test_bare_jobs_opens_the_hook_list_prints_the_table(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        command = JobsCommand(rig.service)
        assert await command("") == "No background jobs in this session.", "nothing to open"
        await rig.start("a")
        table = await command("list")
        assert table.splitlines()[0].lstrip().startswith("ID") and commands.FOOTER in table
        bare = await command("")
        assert bare.splitlines()[0] == table.splitlines()[0] and commands.FOOTER in bare, "no host panel: the table"
        opened: list[bool] = []

        async def panel() -> str | None:
            opened.append(True)
            return "paged"

        rig.service.panel_hook = panel
        assert await command("") == "paged" and opened == [True]
        listed = await command("list")
        assert listed.splitlines()[0] == table.splitlines()[0] and opened == [True], "/jobs list is the table"

        async def closed() -> str | None:
            return None

        rig.service.panel_hook = closed
        assert await command("") == "", "a plain close prints nothing"


# --- the TUI: keys, wake, Ctrl+B ------------------------------------------------------


@contextlib.asynccontextmanager
async def _tui(rig: _Rig) -> AsyncIterator[tuple[Any, Any, JobsUi]]:
    from tests.tui.test_management_modal_keys_671 import _live

    class _Host:
        stopped = None
        in_transition = False

        def __init__(self, harness: Any) -> None:
            self.harness = harness

    async with _live(rows=40, cols=120) as live:
        ui = JobsUi(rig.service, chrome=live.chrome, runtime_host=_Host(rig.harness))
        live.chrome.on_modal_change = ui.on_modal_change
        try:
            yield live, live.chrome, ui
        finally:
            ui.close()


async def _open(live: Any, ui: JobsUi) -> asyncio.Future[Any]:
    task = asyncio.ensure_future(ui.open_panel())
    await live.until(lambda: ui.panel is not None and ui.panel.list.on_top(), "the panel on top")
    return task


async def test_esc_and_q_close_it_and_never_end_the_turn_25_times(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig, _tui(rig) as (live, chrome, ui):
        await rig.start("a")
        interrupts: list[int] = []
        chrome.on_interrupt = lambda: interrupts.append(1)
        chrome.set_running(True)
        for n in range(25):
            task = await _open(live, ui)
            live.pipe.send_text("\x1b" if n % 2 == 0 else "q")
            await wait_until(task.done, what=f"the panel to close on press {n}", detail=live.dump)
            assert task.result() is None and not chrome.is_modal_open()
        assert interrupts == [], "the turn was never interrupted"
        assert rig.state(rig.service.snapshots()[0].job_id) == "running"
        chrome.set_running(False)


async def test_keys_reach_the_panel_and_s_s_stops_through_the_real_key_processor(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig, _tui(rig) as (live, _chrome, ui):
        await rig.start("a")
        b = await rig.start("b")
        task = await _open(live, ui)
        await live.until(lambda: live.shows("Background jobs · 2"), "the title row")
        live.pipe.send_text("\x1b[B")  # down
        await live.until(lambda: ui.panel.list.model.selected == b, "the selection on b")  # type: ignore[union-attr]
        live.pipe.send_text("s")
        await live.until(lambda: live.shows("press s again to stop"), "the arm's key row")
        live.pipe.send_text("s")
        await wait_until(lambda: rig.state(b) == "stopped", what="b stopped", detail=live.dump)
        assert _Runner.stops == ["b"]
        live.pipe.send_text("q")
        await wait_until(task.done, what="the close")


async def test_the_wake_waits_while_it_is_open_and_goes_when_it_closes(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig, _tui(rig) as (live, chrome, ui):
        a = await rig.start("a", notify=True)
        ui.parked = True
        task = await _open(live, ui)
        await rig.end("a")
        await wait_for(lambda: rig.harness.pending_notices == 1 and rig.state(a) == "done")
        ui.maybe_wake()
        assert ui.gates() == "G2: a dialog is open" and not ui.queued
        ui.panel.list.refresh()  # type: ignore[union-attr]
        await live.until(lambda: live.shows(WAITING), "the key row saying the result waits")
        live.pipe.send_text("\x1b")
        await wait_until(task.done, what="the close")
        assert ui.queued, "closing ran the waker"


async def test_ctrl_b_under_the_open_panel_asks_nothing(tmp_path: Path) -> None:
    """S3a's key with the panel open does nothing; closed, the same key asks (positive control)."""

    from tests.tui.test_detach_key_611 import _Host, _press, _running, _wire

    async with _rig(tmp_path) as rig, _running() as (chrome, pipe):
        await rig.start("a")
        host = _Host()
        _wire(chrome, host)
        panel = JobsPanel(rig.service, chrome)
        task = asyncio.ensure_future(panel.run())
        await wait_until(panel.list.on_top, what="the panel on top")
        await _press(chrome, pipe)
        assert host.requests == 0 and chrome.buffer.cursor_position == 4
        await _press(chrome, pipe, "q")
        await wait_until(task.done, what="the close")
        await _press(chrome, pipe)
        assert host.requests == 1


async def test_closing_or_a_dialog_over_it_drops_the_arm_in_the_tui(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig, _tui(rig) as (live, chrome, ui):
        await rig.start("a")
        task = await _open(live, ui)
        live.pipe.send_text("s")
        await live.until(lambda: live.shows("press s again to stop"), "armed")
        dialog = Window(FormattedTextControl("a dialog", focusable=True), height=1)
        chrome.mount_modal(dialog, focus=dialog)  # a turn's dialog over the panel
        chrome.focus(dialog)
        assert ui.panel is not None and ui.panel.arm is None, "covered: the arm went"
        chrome.unmount_modal(dialog)
        await live.until(lambda: ui.panel is not None and ui.panel.list.on_top(), "the panel back on top")
        live.pipe.send_text("s")
        await live.until(lambda: ui.panel is not None and ui.panel.arm is not None, "armed again, not stopped")
        live.pipe.send_text("\x1b")
        await wait_until(task.done, what="the close")
        task = await _open(live, ui)
        assert ui.panel.arm is None, "a new panel starts with no arm"
        live.pipe.send_text("s")
        await live.until(lambda: ui.panel is not None and ui.panel.arm is not None, "armed")
        live.pipe.send_text("q")
        await wait_until(task.done, what="the close")
        assert _Runner.stops == [], "no second s ever met a live arm"


async def test_it_reads_the_jobs_again_every_second(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig, _tui(rig) as (live, _chrome, ui):
        await rig.start("a")
        task = await _open(live, ui)
        assert ui.panel is not None and ui.panel.list.interval == 1.0
        await live.until(lambda: live.shows("Background jobs · 1"), "one job")
        await rig.start("b", title="a second job")
        await live.until(lambda: live.shows("a second job"), "the 1 s redraw, no key pressed")
        loop = asyncio.get_running_loop()
        ticks: list[float] = []
        real = ui.panel.list.refresh  # type: ignore[union-attr]

        def counted() -> None:
            ticks.append(loop.time())
            real()

        ui.panel.list.refresh = counted  # type: ignore[union-attr, method-assign]
        await wait_until(lambda: len(ticks) >= 3, what="three redraws")
        gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
        assert all(0.9 <= gap < 5.0 for gap in gaps), gaps
        live.pipe.send_text("q")
        await wait_until(task.done, what="the close")


def test_the_jobs_ui_hooks_the_panel_and_unhooks_it() -> None:
    from aelix_coding_agent.jobs.service import JobService

    class _Host:
        harness = make_harness(None)

    service = JobService()
    ui = JobsUi(service, chrome=_Stub(), runtime_host=_Host())
    assert service.panel_hook == ui.open_panel
    ui.close()
    assert service.panel_hook is None


# --- #769: an ended job let go ------------------------------------------------------


async def test_an_ended_job_the_service_let_go_stays_listed_and_reads_back(tmp_path: Path) -> None:
    async with _rig(tmp_path, settled_kept=0) as rig:
        a = await rig.start("kept in records")
        b = await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        await rig.end("kept in records")
        await wait_for(lambda: a in rig.service._settled)
        panel.list.refresh()
        assert [row.key for row in panel.list.model.rows] == [a, b]
        assert panel.list.model.selected == a
        row = panel.list.model.current()
        assert row is not None and row.mark == "✓" and "done" in row.columns
        assert row.columns[-1] == "said 15 characters", "an ended job's activity is its brief"
        panel.list._result = asyncio.get_running_loop().create_future()
        panel.list.press("r")
        assert panel.list._result.result() == "kept in records", "read back from its records"


def test_the_widget_names_no_job() -> None:
    """#795 reuses ``tui/list_panel.py`` as it is: nothing in it is about jobs."""

    import inspect

    from aelix_coding_agent.tui import list_panel

    source = inspect.getsource(list_panel).lower()
    assert "job" not in source.replace("/jobs", "").replace("jobs_panel", "")
    assert jobs_panel.COLUMNS[2].width is None, "row 0 is the column that takes what is left"


async def test_run_tui_hooks_the_panel_and_forwards_modal_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real ``run_tui``: its JobsUi is the panel's host, hears the chrome's modal changes and
    writes Enter's detail as plain text; quitting takes the hook back."""

    from aelix_coding_agent.jobs.service import JobService
    from aelix_coding_agent.tui import shell as tui_shell
    from aelix_coding_agent.tui.chrome import AelixChrome
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime

    built: list[AelixChrome] = []
    real = tui_shell.AelixChrome
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

        def chrome(**kwargs: Any) -> AelixChrome:
            kwargs.pop("history_path", None)
            made = real(**kwargs, pt_input=pipe, pt_output=DummyOutput())
            built.append(made)
            return made

        monkeypatch.setattr(tui_shell, "AelixChrome", chrome)
        service = JobService()
        harness = FakeHarness()
        harness.pending_notices = 0  # type: ignore[attr-defined]
        task = asyncio.ensure_future(
            tui_shell.run_tui(
                FakeRuntime(harness),  # type: ignore[arg-type]
                cwd=str(tmp_path),
                install_signal_handlers=False,
                jobs=service,
            )
        )
        await wait_until(lambda: bool(built) and built[0].app.is_running, what="run_tui's chrome")
        (made,) = built
        hook = service.panel_hook
        assert hook is not None
        ui = hook.__self__  # type: ignore[attr-defined]
        assert isinstance(ui, JobsUi)
        assert made.on_modal_change == ui.on_modal_change
        assert ui.emit is not None
        pipe.send_text("/quit\n")
        await quit_within(task)
    assert service.panel_hook is None, "closed with the TUI"


# --- pure: a row's parts, the widget's selection ------------------------------------


def _snap(**fields: Any) -> JobSnapshot:
    base: dict[str, Any] = {
        "job_id": "j123456789", "kind": KIND, "label": "panel", "title": "t", "state": "running",
        "owner": "s", "created": 0.0, "elapsed": 5.0, "last_activity_ago": 7.0, "quiet": False,
        "result_waiting": False, "notice_state": "none",
    }
    return JobSnapshot(**{**base, **fields})


@pytest.mark.parametrize(
    ("fields", "rows", "shown"),
    [
        ({}, ["t", "reading"], "reading"),
        ({}, ["t"], "7s ago"),
        ({"quiet": True, "last_activity_ago": 660.0}, ["t"], "quiet 11m"),
        ({"quiet": True, "last_activity_ago": 660.0}, ["t", "reading"], "quiet 11m · reading"),
        ({"state": "unresolved", "unresolved_reason": "pid 7 alive"}, ["t", "x"], "pid 7 alive"),
        ({"state": "unresolved"}, ["t"], "not confirmed stopped"),
        ({"state": "done", "result_waiting": True, "brief": "exit 0"}, ["t", "x"], "result waiting"),
        ({"state": "done", "brief": "exit 0"}, ["t", "x"], "exit 0"),
        ({"state": "failed"}, ["t", "x"], "x"),
    ],
)
def test_the_activity_by_state(fields: dict[str, Any], rows: list[str], shown: str) -> None:
    assert jobs_panel.activity(_snap(**fields), rows) == shown


def test_the_mark_and_the_detail() -> None:
    assert jobs_panel.mark(_snap(state="done", result_waiting=True)) == "◆"
    assert [jobs_panel.mark(_snap(state=s)) for s in ("running", "stopping", "done", "failed", "stopped")] == [
        "●", "○", "✓", "✗", "■",
    ]
    unresolved = _snap(state="unresolved", unresolved_reason="r", notice_state="pending")
    assert jobs_panel.detail(unresolved, ["t", "a", "b"]) == ("why: r", "a", "b", "◆ its result waits for the model")
    assert jobs_panel.detail(_snap(notice_state="delivered"), ["t"]) == ("its result went to the model",)
    assert jobs_panel.detail(_snap(), ["t"]) == ("id j123456789",)


def test_a_vanished_selection_goes_to_the_row_now_at_its_place_and_the_host_hears_it() -> None:
    from aelix_coding_agent.tui.list_panel import ListPanel, ListPanelModel, PanelRow

    model = ListPanelModel(PanelLayout(rows=3))
    rows = [PanelRow(k, (k,)) for k in "abcd"]
    assert model.update(rows) is False
    model.move(1)
    assert model.selected == "b"
    assert model.update([r for r in rows if r.key != "b"]) is True
    assert model.selected == "c", "the row now at b's place, not the first"
    assert model.update(rows) is False, "c is still there"

    shown = list(rows)
    lost: list[str] = []
    panel = ListPanel(_Stub(), layout=PanelLayout(rows=3), rows=lambda: shown, title=lambda: "t", on_lost=lambda: lost.append("x"))  # type: ignore[arg-type]
    panel.refresh()
    panel.press("down")
    shown.remove(rows[1])
    panel.refresh()
    assert lost == ["x"] and panel.model.selected == "c"


async def test_the_panel_paints_no_background_and_marks_the_selection_in_bold_cyan(tmp_path: Path) -> None:
    """Read from the terminal's cells (pyte), not the text: no cell of the panel's 9 rows has a
    background colour; the selected row is bold cyan; the others keep the default colour."""

    import pyte

    async with _rig(tmp_path) as rig, _tui(rig) as (live, _chrome, ui):
        await rig.start("a")
        await rig.start("b")
        task = await _open(live, ui)
        await live.until(lambda: live.shows("Background jobs · 2"), "the title row")
        screen = pyte.Screen(120, 40)
        pyte.Stream(screen).feed(live._capture.getvalue())
        top = next(y for y, row in enumerate(screen.display) if "Background jobs · 2" in row)
        panel = range(top, top + 9)
        assert all(screen.buffer[y][x].bg == "default" for y in panel for x in range(120))
        selected = screen.display[top + 1]
        assert selected.startswith("▸")
        assert screen.buffer[top + 1][4].fg == "cyan" and screen.buffer[top + 1][4].bold
        assert screen.buffer[top + 2][4].fg == "default" and not screen.buffer[top + 2][4].bold
        live.pipe.send_text("q")
        await wait_until(task.done, what="the close")


# --- fix round 1 (Codex H1/M2/M3/L4, verify F4-F7) --------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no SIGTERM handler on win32")
async def test_sigterm_with_the_panel_open_at_idle_ends_run_tui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex H1 (``probe_shutdown.py``): on ``3f11a358`` the input loop stayed inside ``/jobs``'s panel,
    so ``run_tui`` never reached its teardown, the ticker ran and the runtime was not disposed."""

    import signal
    from types import SimpleNamespace

    from aelix_coding_agent.tui import shell as tui_shell
    from aelix_coding_agent.tui.chrome import AelixChrome
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime

    async with _rig(tmp_path) as rig:
        await rig.start("a")
        harness = FakeHarness()
        harness.pending_notices = 0  # type: ignore[attr-defined]
        jobs = SimpleNamespace(invocation_name="jobs", command=SimpleNamespace(handler=JobsCommand(rig.service), description="Jobs"))
        harness.extension_runner = SimpleNamespace(  # type: ignore[attr-defined]
            get_command=lambda n: jobs if n == "jobs" else None, get_registered_commands=lambda: [jobs]
        )
        harness.make_command_context = lambda **_kw: SimpleNamespace()  # type: ignore[attr-defined]
        runtime = FakeRuntime(harness)
        built: list[AelixChrome] = []
        handlers: dict[int, Any] = {}
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb, *a: handlers.__setitem__(sig, cb))
        monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: handlers.pop(sig, None) is not None)
        real = tui_shell.AelixChrome
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):

            def chrome(**kwargs: Any) -> AelixChrome:
                kwargs.pop("history_path", None)
                made = real(**kwargs, pt_input=pipe, pt_output=DummyOutput())
                built.append(made)
                return made

            monkeypatch.setattr(tui_shell, "AelixChrome", chrome)
            task = asyncio.ensure_future(tui_shell.run_tui(runtime, cwd=str(tmp_path), jobs=rig.service))  # type: ignore[arg-type]
            await wait_until(lambda: bool(built) and built[0].app.is_running, what="run_tui's chrome")
            pipe.send_text("/jobs\r")
            await wait_until(built[0].is_modal_open, what="the panel")
            assert _tickers() == 1
            handlers[signal.SIGTERM]()
            await quit_within(task)
        assert _tickers() == 0 and runtime.disposed == 1


def _tickers() -> int:
    return sum(
        1 for t in asyncio.all_tasks() if getattr(t.get_coro(), "__qualname__", "") == "ListPanel._tick" and not t.done()
    )


async def test_the_app_ending_closes_the_panel_and_its_ticker(tmp_path: Path) -> None:
    """The way a SIGTERM ends a panel opened mid-turn too: ``chrome.exit()`` ends the app's run, and
    the panel, which no key can reach after that, closes (on ``3f11a358`` it stayed open)."""

    async with _rig(tmp_path) as rig, _tui(rig) as (live, chrome, ui):
        await rig.start("a")
        task = await _open(live, ui)
        assert _tickers() == 1
        chrome.exit()
        await wait_until(task.done, what="the panel to close with the app")
        assert task.result() is None and _tickers() == 0 and not chrome.is_modal_open()


@pytest.mark.parametrize("where", ["title", "hint", "rows"])
async def test_a_host_that_cannot_draw_closes_the_panel_and_a_failed_read_keeps_it(tmp_path: Path, where: str) -> None:
    """Codex's ``lifecycle`` probe raised in the title as a frame was drawn: the exception left the
    control and the panel stayed open with its ticker (``3f11a358``). Verify's raising ``rows()`` is
    the 1 s read: the last rows stay and the panel stays open until a key closes it."""

    from tests.tui.test_management_modal_keys_671 import _live

    async with _live(rows=40, cols=120) as live:
        broken = {"on": False}

        def maybe(text: str) -> str:
            if broken["on"]:
                raise ValueError("cannot draw")
            return text

        def rows() -> list[PanelRow]:
            maybe("")
            return [PanelRow("a", ("A",))]

        panel = ListPanel(
            live.chrome,
            layout=PanelLayout(rows=3),
            rows=rows if where == "rows" else (lambda: [PanelRow("a", ("A",))]),
            title=(lambda: maybe("T")) if where == "title" else (lambda: "T"),
            hint=(lambda _w: maybe("H")) if where == "hint" else (lambda _w: "H"),
        )
        task = asyncio.ensure_future(panel.run())
        await wait_until(panel.on_top, what="the panel")
        broken["on"] = True
        if where == "rows":
            panel.refresh()
            live.chrome.invalidate()
            await asyncio.sleep(0.05)
            assert not task.done() and [r.key for r in panel.model.rows] == ["a"], "the last rows stay"
            panel.press("escape")
        else:
            live.chrome.app._redraw()  # a frame drawn now, as Codex's probe drew it
        await wait_until(task.done, what="the panel to close")
        assert _tickers() == 0 and live.chrome.app.is_running


async def test_ctrl_c_and_ctrl_j_under_the_panel_mid_turn(tmp_path: Path) -> None:
    """Codex M2 (``keys-arm``: ``interrupts=1 buffer='DRAFT' panel_open=True`` on ``3f11a358``), verify
    F4/F5: Ctrl+C closes the panel as every modal's does and never reaches the turn; Ctrl+J is Enter."""

    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import Keys

    async with _rig(tmp_path) as rig, _tui(rig) as (live, chrome, ui):
        await rig.start("a")
        said: list[str] = []
        ui.emit = said.append
        interrupts: list[int] = []
        chrome.on_interrupt = lambda: interrupts.append(1)
        chrome.set_running(True)
        chrome.buffer.text = "DRAFT"
        task = await _open(live, ui)
        chrome.app.key_processor.feed(KeyPress(Keys.ControlJ))
        chrome.app.key_processor.process_keys()
        assert len(said) == 1 and said[0].startswith('panel "a"'), said
        chrome.app.key_processor.feed(KeyPress(Keys.ControlC))
        chrome.app.key_processor.process_keys()
        await wait_until(task.done, what="Ctrl+C to close the panel")
        assert (interrupts, chrome.buffer.text, chrome.is_modal_open()) == ([], "DRAFT", False)
        chrome.set_running(False)


async def test_enter_in_the_real_tui_writes_through_the_jobs_ui(tmp_path: Path) -> None:
    """Verify F7 U08: ``JobsUi.open_panel`` hands its ``emit`` to the panel (``\\r`` through the pipe)."""

    async with _rig(tmp_path) as rig, _tui(rig) as (live, _chrome, ui):
        a = await rig.start("a")
        said: list[str] = []
        ui.emit = said.append
        task = await _open(live, ui)
        live.pipe.send_text("\r")
        await wait_until(lambda: bool(said), what="Enter's detail")
        assert said == ["\n".join(commands.detail(rig.service, rig.service.snapshot(a)))]
        live.pipe.send_text("q")
        await wait_until(task.done, what="the close")


async def test_the_arm_redraws_when_it_runs_out(tmp_path: Path) -> None:
    """Verify N1: no key and no 1 s read — the key row stops saying "press s again" at 3 s."""

    async with _rig(tmp_path) as rig:
        await rig.start("a")
        panel = _opened(JobsPanel(rig.service, _Stub(), interval=60.0))
        panel.list.press("s")
        assert "press s again" in _plain(panel.list.lines(100))[-1]
        await wait_until(lambda: panel.arm is None, what="the arm to run out")
        assert "press s again" not in _plain(panel.list.lines(100))[-1]


@pytest.mark.parametrize("width", [80, 100, 120])
async def test_after_s_s_the_key_row_still_says_results_wait(tmp_path: Path, width: int) -> None:
    """Verify F6 (``p5_age_and_hint.py``): the ``stopping <id>`` note took the waiting text off the key
    row at 80 and 100 on ``3f11a358`` — the L7b flow, ``s s`` then Esc."""

    async with _rig(tmp_path) as rig:
        done = await rig.start("done first", notify=True)
        await rig.end("done first")
        await wait_for(lambda: rig.service.snapshot(done).result_waiting)
        await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub(), rows=40))
        panel.list.press("down")
        panel.list.press("s")
        panel.list.press("s")
        key_row = _plain(panel.list.lines(width))[-1]
        assert key_row.startswith("stopping ") and key_row.endswith(WAITING), key_row
        assert cell_len(key_row) <= width


async def test_a_waiting_extension_notice_says_so_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify F7 J30."""

    async with _rig(tmp_path) as rig:
        await rig.start("a")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        assert WAITING not in _plain(panel.list.lines(100))[-1]
        monkeypatch.setattr(rig.service, "extension_waiting", lambda: 1)
        assert _plain(panel.list.lines(100))[-1].endswith(WAITING)


async def test_a_control_character_in_a_title_never_reaches_the_terminal(tmp_path: Path) -> None:
    """Verify F7 L14: the columns go through ``flat()``."""

    async with _rig(tmp_path) as rig:
        await rig.start("a", title="evil\x1b]0;pwned\x07\x1b[2Jname")
        lines = _opened(JobsPanel(rig.service, _Stub())).list.lines(120)
    raw = _ANSI.sub("", "\n".join(lines))
    assert "\x07" not in raw and "\x1b" not in raw


def test_the_widget_slices_the_detail_and_fits_its_title_and_key_rows() -> None:
    """Verify F7 L22, L18, L23."""

    from aelix_coding_agent.tui.list_panel import ListPanel

    row = PanelRow("a", ("A",), detail=tuple(f"detail {n}" for n in range(6)))
    panel = ListPanel(
        _Stub(), layout=PanelLayout(rows=3, detail=3), rows=lambda: [row],  # type: ignore[arg-type]
        title=lambda: "A title far longer than thirty cells", right=lambda: "and a corner text too",
        hint=lambda _w: "a key row that is also far longer than thirty cells",
    )
    panel.refresh()
    lines = _plain(panel.lines(30))
    assert len(lines) == 9 and all(cell_len(line) <= 30 for line in lines), lines
    assert "detail 2" in lines[7] and not any("detail 3" in line for line in lines)


def test_the_selection_holds_at_the_top_scrolls_only_as_needed_and_the_top_is_clamped() -> None:
    """Verify F7 L06, L08, L09."""

    from aelix_coding_agent.tui.list_panel import ListPanelModel

    model = ListPanelModel(PanelLayout(rows=3))
    rows = [PanelRow(k, (k,)) for k in "abcde"]
    model.update(rows)
    assert model.move(-1) is False and model.selected == "a", "↑ at the top does not wrap"
    model.move(1)
    model.move(1)
    assert (model.selected, model.top) == ("c", 0), "no scroll while the selection is in view"
    model.move(1)
    model.move(1)
    assert (model.selected, model.top) == ("e", 2)
    model.update(rows[2:])
    assert model.top == 0, "clamped once the rows shrink"


async def test_a_panel_opened_on_an_ended_app_closes_at_once() -> None:
    """The app's run future already done when the panel is built: it closes then (no key could reach it)."""

    from types import SimpleNamespace

    ended = asyncio.get_running_loop().create_future()
    ended.set_result(None)
    chrome = _Stub()
    chrome.app = SimpleNamespace(future=ended)  # type: ignore[attr-defined]
    panel = ListPanel(chrome, layout=PanelLayout(rows=3), rows=lambda: [], title=lambda: "T")  # type: ignore[arg-type]
    panel._result = asyncio.get_running_loop().create_future()
    panel._close_with_the_app()
    assert panel._result.done() and panel._result.result() is None


async def test_moving_after_a_stop_clears_its_note(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        await rig.start("a")
        await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("s")
        panel.list.press("s")
        assert _plain(panel.list.lines(100))[-1].startswith("stopping ")
        panel.list.press("down")
        assert _plain(panel.list.lines(100))[-1].startswith("↑↓ select")


async def test_verify_round_one_survivors_pinned(tmp_path: Path) -> None:
    """Verify F7's survivors J26 (Enter drops the arm), J25 (a stop is read back at once), J35 (no
    ``⚙`` in the corner), L15 (a row of many columns fits a narrow width), L27 (a second close is
    harmless)."""

    async with _rig(tmp_path) as rig:
        await rig.start("a")
        b = await rig.start("b")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("s")
        panel.list.press("enter")
        panel.list.press("s")
        assert _Runner.stops == [], "Enter dropped the arm; the second s armed again"
        assert not _plain(panel.list.lines(100))[0].count("⚙")
        panel.list.press("down")
        panel.list.press("s")
        panel.list.press("s")
        row = panel.list.model.current()
        assert row is not None and row.key == b and row.columns[3] in ("stopping", "stopped")
        assert all(cell_len(line) <= 24 for line in _plain(panel.list.lines(24)))
        panel.list._result = asyncio.get_running_loop().create_future()
        panel.list.close()
        panel.list.close("again")
        assert panel.list._result.result() is None
