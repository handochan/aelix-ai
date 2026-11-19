"""#123 (ADR-0306 §3.1): the human's surfaces — ``/jobs steer``, ``/jobs wrap``, the panel's ``m`` and ``w w``.

``/jobs steer [--after] <id> <text>`` and ``/jobs wrap <id>`` go through
``JobService.control`` to the kind's adapter; the panel's ``m`` closes with
``/jobs steer <id> `` for the input box and ``w w`` wraps the selected job, armed
as ``s s`` is, and on a job whose kind takes neither both leave the kind's
refusal on the hint row (V7); the key row names them, the fullest row that fits
(F11), and a note is fitted to its row (V5); a job's rows and the
delegation panel show ``wrapping up`` and ``stuck: repeat``. ``/jobs`` is an
extension command, which the TUI runs mid-turn (``tui/shell.py``, ADR-0292),
so ``steer`` and ``wrap`` are allowed mid-turn by construction; the structural
guard below pins that no built-in table claims the name.

Fails on ``a1606f58``: ``/jobs steer`` is read as a job id, the panel has no
``m`` or ``w`` key, ``KindCapabilities`` has no ``controls``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agents.jobs_client import _doing_row, describe_delivery
from aelix_agents.panel import _intervention_term
from aelix_coding_agent.jobs import activation, commands
from aelix_coding_agent.jobs.commands import FOOTER, JobsCommand
from aelix_coding_agent.jobs.contract import JobSnapshot, KindCapabilities
from aelix_coding_agent.subagent_contract import SubagentProgress
from aelix_coding_agent.tools import bash_background
from aelix_coding_agent.tui.commands import _MID_TURN_REFUSED, MID_TURN_RUNS
from aelix_coding_agent.tui.jobs_panel import WAITING, JobsPanel, Prefill
from aelix_coding_agent.tui.jobs_ui import JobsUi
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for
from tests.tui.test_jobs_panel_611 import _ANSI, _Adapter, _plain, _rig, _Runner, _Stub


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setattr(activation, "_switched_off", False)


class _Controllable(_Adapter):
    """The panel test's kind, declaring ``steer`` and ``wrap_up`` and recording what it is handed."""

    name = "test:controllable"
    capabilities = KindCapabilities(default_bound=32, notify=True, reads_persisted=True, controls=("steer", "wrap_up"))
    taken: list[tuple[str, str, Mapping[str, Any]]] = []  # noqa: RUF012 — one per test (cleared below)

    async def control(self, job_id: str, verb: str, payload: Mapping[str, Any]) -> str:
        _Controllable.taken.append((job_id, verb, dict(payload)))
        return f"took {verb}"


async def _controllable(tmp_path: Path) -> tuple[Any, Any]:
    _Controllable.taken.clear()
    _Runner.gates.clear()
    service = fast_service()
    service.register_kind(_Controllable(), _Runner)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_Controllable.name, title="a", params={"said": "a"})
    await wait_for(lambda: service.snapshot(job_id).state == "running")  # type: ignore[union-attr]
    return service, job_id


# === /jobs steer and /jobs wrap ======================================================


async def test_jobs_steer_and_wrap_reach_the_kinds_control_adapter(tmp_path: Path) -> None:
    service, job_id = await _controllable(tmp_path)
    try:
        command = JobsCommand(service)
        short = job_id[:6]
        assert await command(f"steer {short} look at the tests too") == "took steer"
        assert await command(f"steer --after {short} then summarise") == "took steer"
        assert await command(f"wrap {short}") == "took wrap_up"
        assert _Controllable.taken == [
            (job_id, "steer", {"text": "look at the tests too", "mode": "steer", "origin": "user"}),
            (job_id, "steer", {"text": "then summarise", "mode": "follow_up", "origin": "user"}),
            (job_id, "wrap_up", {"origin": "user"}),
        ]
        assert (await command("steer")).startswith("usage: /jobs steer")
        assert (await command(f"steer {short}")).startswith("usage: /jobs steer")
        assert (await command("wrap")).startswith("usage: /jobs wrap")
        assert (await command(f"wrap {short} now")).startswith("usage: /jobs wrap <id>")
        assert "no job" in await command("steer zzzzzz hi")
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


async def test_a_kind_without_controls_is_refused_with_its_hint(tmp_path: Path) -> None:
    """V7 (verify VF12): the refusal says "a message" and "a wrap-up", never the verb ``steer`` or ``wrap_up``.

    This kind declares no hint, so the sentence ends there. Fails on
    ``8d7ee999``: "this kind of job takes no steer" and "… takes no wrap_up".
    """

    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        command = JobsCommand(rig.service)
        assert await command(f"steer {a[:6]} hi") == "this kind of job does not take a message"
        assert await command(f"wrap {a[:6]}") == "this kind of job does not take a wrap-up"


_NO_MESSAGE = "this kind of job does not take a message; /jobs stop <id> stops it (panel: s s)"
_NO_WRAP_UP = "this kind of job does not take a wrap-up; /jobs stop <id> stops it (panel: s s)"


async def _background_command(tmp_path: Path) -> tuple[Any, Any]:
    """A running job of the real ``bash`` kind — its adapter and its capabilities; the panel test's runner is its shell."""

    _Runner.gates.clear()
    service = fast_service()
    service.register_kind(bash_background.BashKindAdapter(), _Runner)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(bash_background.KIND, title="sleep 30", params={"said": "a"})
    await wait_for(lambda: service.snapshot(job_id).state == "running")  # type: ignore[union-attr]
    return service, job_id


async def test_jobs_steer_and_wrap_on_a_background_command_say_what_stops_it(tmp_path: Path) -> None:
    """V7: a background command takes neither, and its hint names what works. Fails on ``8d7ee999`` (no hint, the verbs)."""

    service, job_id = await _background_command(tmp_path)
    try:
        command = JobsCommand(service)
        assert await command(f"steer {job_id[:6]} hi") == _NO_MESSAGE
        assert await command(f"wrap {job_id[:6]}") == _NO_WRAP_UP
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


def test_the_footer_and_the_mid_turn_tables() -> None:
    assert "/jobs steer <id> <text>" in FOOTER and "/jobs wrap <id>" in FOOTER
    # ``/jobs`` is an extension command: no built-in table names it, so the TUI's
    # mid-turn decision takes the extension path, which runs it at once (ADR-0292).
    assert "jobs" not in MID_TURN_RUNS and "jobs" not in _MID_TURN_REFUSED


# === the panel keys ==================================================================


def _opened(panel: JobsPanel) -> JobsPanel:
    panel.list.window = Window(FormattedTextControl("", focusable=True))
    panel.list.refresh()
    return panel


async def test_m_closes_the_panel_with_the_steer_line_for_the_input(tmp_path: Path) -> None:
    service, job_id = await _controllable(tmp_path)
    try:
        panel = JobsPanel(service, _Stub())
        panel.list._result = asyncio.get_running_loop().create_future()
        _opened(panel).list.press("m")
        closed = panel.list._result.result()
        assert isinstance(closed, Prefill) and str(closed) == f"/jobs steer {job_id[:6]} "
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


@pytest.mark.parametrize("width", [80, 120])
async def test_m_on_a_background_command_is_the_refusal_as_a_note_and_not_a_prefill(tmp_path: Path, width: int) -> None:
    """V7 (verify VF12): ``m`` prefilled ``/jobs steer <id> `` for a job whose kind takes no steer.

    The panel stays open and its hint row is the sentence ``/jobs steer`` would
    answer, whole at 80 columns (79 cells). Fails on ``8d7ee999``: the panel
    closed with the prefill.
    """

    service, _job_id = await _background_command(tmp_path)
    try:
        panel = JobsPanel(service, _Stub())
        panel.list._result = asyncio.get_running_loop().create_future()
        _opened(panel).list.press("m")
        closed = panel.list._result.result() if panel.list._result.done() else None
        assert closed is None, f"m closed the panel with {str(closed)!r} for a job that takes no message"
        assert panel.note == _NO_MESSAGE
        assert _painted_rows(panel, width)[-1] == _NO_MESSAGE
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


@pytest.mark.parametrize("width", [80, 120])
async def test_w_w_on_a_background_command_says_what_stops_it(tmp_path: Path, width: int) -> None:
    """V7 (verify VF12): ``w`` ``w`` on a background command answered "this kind of job takes no wrap_up" and no more.

    The note is the refusal in a human's words with the kind's hint, compared
    whole on the painted row. Fails on ``8d7ee999`` with the old sentence.
    """

    service, job_id = await _background_command(tmp_path)
    try:
        panel = _opened(JobsPanel(service, _Stub()))
        panel.list.press("w")
        assert _plain(panel.list.lines(width))[-1] == f"press w again to wrap up {job_id[:6]}"
        panel.list.press("w")
        await wait_for(lambda: panel.note != f"wrapping up {job_id[:6]}")
        assert panel.note == _NO_WRAP_UP
        assert _painted_rows(panel, width)[-1] == _NO_WRAP_UP
        assert service.snapshot(job_id).state == "running", "a refused wrap-up stops nothing"  # type: ignore[union-attr]
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


async def test_w_w_wraps_the_selected_job_and_s_after_w_arms_afresh(tmp_path: Path) -> None:
    service, job_id = await _controllable(tmp_path)
    try:
        panel = _opened(JobsPanel(service, _Stub()))
        panel.list.press("w")
        assert panel.arm is not None and panel.arm.verb == "wrap"
        assert _plain(panel.list.lines(100))[-1] == f"press w again to wrap up {job_id[:6]}"
        panel.list.press("s")
        assert panel.arm is not None and panel.arm.verb == "stop", "the other letter arms afresh"
        assert _Controllable.taken == []
        panel.list.press("w")
        assert panel.arm is not None and panel.arm.verb == "wrap"
        panel.list.press("w")
        await wait_for(lambda: _Controllable.taken == [(job_id, "wrap_up", {"origin": "user"})])
        assert panel.arm is None
        await wait_for(lambda: panel.note == "took wrap_up")
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


_QUEUED = describe_delivery(SimpleNamespace(outcome="queued", reason=None, phase="tool"), what="the wrap-up")
_HANGUL = "보내지 못했습니다: " + "에이전트가 이미 끝났습니다 " * 6


class _Wordy(_Controllable):
    """A kind whose answer is longer than a narrow panel: the real wrap-up sentence, or a wide-cell one."""

    name = "test:wordy"
    answer = _QUEUED

    async def control(self, job_id: str, verb: str, payload: Mapping[str, Any]) -> str:
        return _Wordy.answer


def _painted_rows(panel: JobsPanel, width: int) -> list[str]:
    """The panel's rows as a ``width``-column terminal would be sent them, escapes removed (never cropped here)."""

    console = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
    for line in panel.list.lines(width):
        console.print(Text.from_ansi(line), no_wrap=True, crop=False, overflow="ignore", highlight=False)
    return [row.rstrip() for row in _ANSI.sub("", console.file.getvalue()).splitlines()]  # type: ignore[union-attr]


@pytest.mark.parametrize("answer", [_QUEUED, _HANGUL], ids=["the-wrap-up-sentence", "wide-cells"])
@pytest.mark.parametrize("waiting", [False, True], ids=["alone", "result-waiting"])
@pytest.mark.parametrize("width", [80, 120])
async def test_a_note_is_fitted_to_its_row_and_never_sliced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, waiting: bool, answer: str
) -> None:
    """V5 (verify VF9): ``answer[:80]`` cut the wrap-up's answer mid-word — "…it is already runnin".

    The note is fitted where the row is drawn: in the cells the panel has, less
    the waiting text's when a result waits, whole or ending in ``…``. Measured
    in cells on a StringIO console. Fails on ``8d7ee999`` for the wrap-up
    sentence (82 cells) at both widths, sliced to 80 characters with no ``…``,
    and for the wide-cell answer while a result waits (the waiting text cut off).
    """

    assert cell_len(_QUEUED) == 82 and cell_len(_HANGUL) > 120, "the two answers: 82 cells, and wider than any panel here"
    monkeypatch.setattr(_Wordy, "answer", answer)
    _Runner.gates.clear()
    service = fast_service()
    service.register_kind(_Wordy(), _Runner)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_Wordy.name, title="a", params={"said": "a"})
    await wait_for(lambda: service.snapshot(job_id).state == "running")  # type: ignore[union-attr]
    try:
        panel = _opened(JobsPanel(service, _Stub()))
        panel.list.press("w")
        panel.list.press("w")
        await wait_for(lambda: _plain(panel.list.lines(200))[-1].startswith(answer[:6]))
        if waiting:
            monkeypatch.setattr(service, "extension_waiting", lambda: 1)
        row = _painted_rows(panel, width)[-1]
        assert cell_len(row) <= width, row
        tail = f"   {WAITING}" if waiting else ""
        assert row.endswith(tail), row
        note = row[: len(row) - len(tail)]
        room = width - cell_len(tail)
        if cell_len(answer) <= room:
            assert note == answer
        else:
            assert note.endswith("…") and answer.startswith(note[:-1]), note
            assert room - 2 <= cell_len(note) <= room, "cut at the room the row has (a wide cell may leave one free)"
    finally:
        for gate in list(_Runner.gates.values()):
            gate.set()
        await service.shutdown()


_FULL = "↑↓ select · enter detail · r result · m note · s s stop · w w wrap · esc close"
_SHORT = "↑↓ select · enter · r · m note · s s stop · w w wrap · esc"
_BARE = "↑↓ · enter · r · m · s s · w w · esc"


@pytest.mark.parametrize(
    ("width", "waiting", "row"),
    [
        # no result waits: the row is the keys alone — 78, 58 and 36 cells
        (120, False, _FULL),
        (100, False, _FULL),
        (80, False, _FULL),
        (78, False, _FULL),
        (77, False, _SHORT),
        (58, False, _SHORT),
        (57, False, _BARE),
        # a result waits: the waiting text follows — 118, 98 and 76 cells
        (120, True, _FULL),
        (118, True, _FULL),
        (117, True, _SHORT),
        (100, True, _SHORT),
        (98, True, _SHORT),
        (97, True, _BARE),
        (80, True, _BARE),
    ],
)
async def test_the_key_row_keeps_every_label_where_it_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, waiting: bool, row: str
) -> None:
    """F11: at ``8d7ee999`` the wide row had lost ``detail``, ``result`` and ``close`` to make room for two keys.

    The fullest row that fits the panel's width is shown, the waiting text
    counted in, measured in cells (``rich.cells.cell_len``). Fails on
    ``8d7ee999`` wherever the full row fits, and at 77–99 columns where the
    row with ``select``/``note``/``stop``/``wrap`` fits and the bare one was shown.
    """

    async with _rig(tmp_path) as rig:
        await rig.start("a")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        if waiting:
            monkeypatch.setattr(rig.service, "extension_waiting", lambda: 1)
        shown = _plain(panel.list.lines(width))[-1]
        assert shown == (f"{row}   {WAITING}" if waiting else row)
        assert cell_len(shown) <= width


async def test_m_on_an_ended_job_is_a_note_and_not_a_close(tmp_path: Path) -> None:
    async with _rig(tmp_path) as rig:
        await rig.start("a")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        panel.list.press("m")  # an ended job is not messaged: a note, not a close
        await rig.end("a")
        await wait_for(lambda: rig.state(next(iter(rig.service.snapshots())).job_id) == "done")
        panel.list.refresh()
        panel.list._result = asyncio.get_running_loop().create_future()
        panel.list.press("m")
        assert panel.note.endswith("has ended")  # until the service answers (one loop turn)
        # Then what ``/jobs steer`` says, and ``w`` what ``/jobs wrap`` says: a kind with
        # no ended words keeps the service's (lane b7f fix round 2, V2-F1). Fails on
        # ``a3c8b9a6``: the note stays "<id> has ended".
        short = next(iter(rig.service.snapshots())).job_id[:6]
        steer_said = await JobsCommand(rig.service)(f"steer {short} x")
        await wait_for(lambda: panel.note == steer_said, timeout=2.0)
        panel.list.press("w")
        wrap_said = await JobsCommand(rig.service)(f"wrap {short}")
        await wait_for(lambda: panel.note == wrap_said, timeout=2.0)
        assert (steer_said, wrap_said) == (f"{short} already ended: done", f"{short} already ended: done")
        assert panel.arm is None and not panel.list._result.done()


async def test_w_on_an_ended_row_whose_job_left_the_session_keeps_has_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``w`` on an ended row asks the service's ``control`` (lane b7f fix round 2,
    V2-F1); one that answers ``JobNotFound`` (the session changed under the panel)
    leaves the note "<id> has ended" and says nothing else."""

    from aelix_coding_agent.jobs.service import JobNotFound

    async with _rig(tmp_path) as rig:
        a = await rig.start("a")
        await rig.end("a")
        await wait_for(lambda: rig.state(a) == "done")
        panel = _opened(JobsPanel(rig.service, _Stub()))
        asked: list[str] = []

        async def gone(job_id: str, verb: str, payload: Any) -> str:
            asked.append(verb)
            raise JobNotFound(job_id)

        monkeypatch.setattr(rig.service, "control", gone)
        panel.list.press("w")
        await wait_for(lambda: asked == ["wrap_up"], timeout=2.0)
        await asyncio.sleep(0)
        assert (panel.note, panel.arm) == (f"{a[:6]} has ended", None)


async def test_the_jobs_ui_puts_the_prefill_in_the_input_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``open_panel``: a ``Prefill`` goes to the chrome's input box and nothing to the transcript;
    what ``r`` paged still comes back as before."""

    from aelix_coding_agent.tui import jobs_ui as jobs_ui_module

    class _Chrome(_Stub):
        def __init__(self) -> None:
            super().__init__()
            self.text: str | None = None

        def set_editor_text(self, text: str) -> None:
            self.text = text

    closes: list[Any] = [Prefill("/jobs steer abcdef "), "the paged result"]

    class _FakePanel:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def run(self) -> Any:
            return closes.pop(0)

    monkeypatch.setattr(jobs_ui_module, "JobsPanel", _FakePanel)
    async with _rig(tmp_path) as rig:
        chrome = _Chrome()
        ui = JobsUi(rig.service, chrome=chrome, runtime_host=type("H", (), {"harness": rig.harness})(), commit=lambda _t: None)
        assert await ui.open_panel() is None, "nothing for the transcript"
        assert chrome.text == "/jobs steer abcdef "
        assert await ui.open_panel() == "the paged result"
        assert ui.panel is None


# === the rows ======================================================================


def test_a_jobs_row_and_the_delegation_panel_show_wrapping_up_and_stuck() -> None:
    snap = JobSnapshot(
        job_id="j", kind="agent", label="agent", title="t", state="running", owner="o",
        created=0.0, elapsed=1.0, last_activity_ago=0.0, quiet=False, result_waiting=False, notice_state="none",
    )
    assert _doing_row(snap, {"tool": "read", "tokens": 1200}) == "running read · 1.2k tok"
    assert _doing_row(snap, {"tool": None, "stuck": "repeat"}) == "thinking · stuck: repeat"
    assert _doing_row(snap, {"tool": "grep", "wrapping_up": True, "stuck": "error loop"}) == (
        "wrapping up · running grep · stuck: error loop"
    )
    progress = SubagentProgress(id="a", profile="scout", state="running", stuck="repeat", wrapping_up=True)
    assert _intervention_term(progress) == "wrapping up · stuck: repeat"
    assert _intervention_term(SubagentProgress(id="a", profile="scout", state="running")) == ""
    assert _intervention_term(None) == ""


def test_the_delegation_panel_paints_the_mark_on_a_live_member() -> None:
    """On a StringIO console, as the TUI spacing pitfalls ask: the mark is in the painted cells."""

    from aelix_agents.panel import format_panel

    progress = SubagentProgress(id="a", profile="scout", state="running", stuck="repeat", wrapping_up=True, tokens=10)
    rows = format_panel([progress], width=100, tasks=("count files",), mode="single", profile="scout")
    two = SubagentProgress(id="b", profile="scout", state="running", stuck="error loop")
    rows += format_panel([progress, two], width=100, tasks=("a", "b"), mode="parallel", profile="scout")
    console = Console(file=StringIO(), width=100, force_terminal=True, color_system="truecolor")
    for row in rows:
        console.print(row, markup=False, highlight=False)
    painted = _ANSI.sub("", console.file.getvalue())  # type: ignore[union-attr]
    assert "wrapping up" in painted and "stuck: repeat" in painted and "stuck: error loop" in painted, painted
    assert commands.short_id("abcdefgh") == "abcdef…"
