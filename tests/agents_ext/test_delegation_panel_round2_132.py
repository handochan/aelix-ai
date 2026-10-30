"""#132 review round 2 — the delegation panel's defects found by the delta
review of round 1 and by the lead's own look at the live grids, each pinned
by a test that fails on ``6d4c13b6`` (ADR-0278; the findings and their probes
are listed in ``.omc/specs/132-progress-2026-10-30.md``, "Review round 2").
A test that passes there is a GUARD — it pins a behaviour round 1 shipped
that no test held, and says so.

G1 a label that fits beside the numbers as they are now is not cut for the
   reserve; one that is cut keeps its cut; narrow labels are told apart first;
G2 a header shows a lower term that fits after a higher one was dropped, and
   no term comes and goes with the clock, the idle time or the state word;
G3 the Esc frame's fixed phrases are shown whole or not at all;
G4 a call the old 32-call window turned away still shows while it runs, and
   an open call leaves the window when the run ends;
G5 a cut costs a logarithmic number of whole-string measures, and finds the
   cut the one-character-at-a-time loop found.

The last section is the finish pass: six boundaries the round-2 delta
review's mutants found unpinned (M9, M11, M20, M22, M26, M33; listed in
``.omc/specs/132-progress-2026-10-30.md``, "Round-2 delta review and
finish"). Each test pins
the behaviour as it is, on inputs chosen away from the review's residuals
(ADR-0278 Limits), so fixing one of those does not break the pin.
"""

from __future__ import annotations

import random
import re
from typing import Any

import pytest
from aelix_agents import panel as panel_module
from aelix_agents import stream as stream_module
from aelix_agents.panel import format_panel, panel_height
from aelix_agents.stream import ACTIVITY_MAX, _StreamState, activity_of, reduce_event
from aelix_coding_agent.subagent_contract import SubagentUsage
from prompt_toolkit.utils import get_cwidth
from rich.cells import cell_len

from tests.agents_ext.test_delegation_panel_132 import (
    _call,
    _end,
    _screen,
    _snap,
    _start,
    _stopped,
)

# === G1: the reserve never cuts a label that fits ===========================

_LIVE_TASKS = (
    "[TAG:A] Read notes.txt and wait six seconds, then report.",
    "[TAG:B] Wait ten seconds, echo bee, then report.",
    "[TAG:C] Write c-out.txt, wait fourteen seconds, then report.",
)


def _labels(tasks: tuple[str, ...], width: int, elapsed_ms: int, tokens: int) -> list[str]:
    """Each two-row member's first row with its numbers taken off."""

    snaps = [_snap(k, elapsed_ms=elapsed_ms, tokens=tokens) for k in range(len(tasks))]
    rows = _screen(
        format_panel(snaps, tasks=tasks, width=width, height=1 + 2 * len(tasks), profile="worker")
    )
    return [re.sub(r" {2,}\S+( · \S+)*$", "", row) for row in rows[1::2]]


def test_a_label_that_fits_beside_the_numbers_is_not_cut_for_the_reserve() -> None:
    """The live grid (fix-r1 ``parallel-80x30/grid-025.txt``): at 80 columns
    and ``6s · 15`` the reserved column cut task C with nine blank cells
    beside it."""

    rows = _labels(_LIVE_TASKS, 80, 6_000, 15)
    assert rows == [f"│ ● {k + 1}  {task}" for k, task in enumerate(_LIVE_TASKS)], rows


def test_a_label_that_fits_is_cut_once_when_the_numbers_grow_into_it() -> None:
    """Whole while it fits beside the numbers as they are; once they grow
    into it (``1m00s · 0.1k``), cut at the reserve — and kept there."""

    frames = [
        _labels(_LIVE_TASKS, 80, ms, tok)
        for ms, tok in ((9_000, 15), (10_000, 15), (59_000, 99), (60_000, 100), (61_000, 9_900))
    ]
    c = [frame[2] for frame in frames]
    assert c[:3] == ["│ ● 3  " + _LIVE_TASKS[2]] * 3, c
    assert c[3] == c[4] and c[3] != c[0] and "…" in c[3], c
    # A and B fit in the reserved room too: never cut.
    assert all(frame[:2] == frames[0][:2] for frame in frames), frames


def test_a_cut_label_keeps_its_cut_while_a_neighbour_is_whole() -> None:
    """A label that is cut whatever the numbers do is cut at the reserve —
    also while another label of the batch is whole only at the numbers' width
    now — so it does not re-cut at 9 s -> 10 s -> 1m00s."""

    tasks = (
        "Port the retry backoff in the provider layer to every adapter we ship, then report",
        _LIVE_TASKS[2],  # 60 cells: whole beside ``9s · 15`` (61) and ``59s · 99`` (60), not at the reserve (55)
    )
    frames = [
        _labels(tasks, 77, ms, tok) for ms, tok in ((9_000, 15), (10_000, 15), (59_000, 99))
    ]
    assert {frame[0] for frame in frames} == {frames[0][0]}, frames
    assert "…" in frames[0][0]
    assert all(frame[1] == "│ ● 2  " + tasks[1] for frame in frames), frames


@pytest.mark.parametrize("width", [40, 42, 45])
def test_narrow_labels_are_told_apart_before_their_cut_is_kept_still(width: int) -> None:
    """GUARD (round 1 shipped it, no test held it — the delta review's mutant
    "always reserve" survived). Where reserving the numbers would leave the
    labels fewer cells than a middle cut needs (24), they are laid out against
    the numbers as they are, and the end that tells them apart stays — at
    40-45 columns, 24-29 cells now against 18-23 at the reserve."""

    tasks = (
        "port the retry backoff to google",
        "port the retry backoff to openai",
        "port the retry backoff to mistral",
    )
    rows = _labels(tasks, width, 9_000, 15)
    for row, word in zip(rows, ("google", "openai", "mistral"), strict=True):
        assert "…" in row and word in row, (width, rows)


# === G2: a greedy header whose choices do not move ===========================


_MODEL = "anthropic/claude-haiku-4.5"


def _single_header(width: int, *, state: str = "running", elapsed_ms: int = 6_000,
                   idle_ms: int | None = 1_000, tokens: int = 15, cost: float = 0.0) -> str:
    activity = (_call("bash", "sleep 12", "running" if state == "running" else "ok"),)
    snap = _snap(model=_MODEL, elapsed_ms=elapsed_ms, tokens=tokens, cost=cost, activity=activity)
    return _screen(format_panel([snap], width=width, idle_ms=[idle_ms]))[0]


def test_a_header_shows_a_lower_term_that_fits_after_a_higher_one_was_dropped() -> None:
    """The live grid (fix-r1 ``single-long-60x30/grid-025.txt``): at 60
    columns the long model id did not fit, and the tokens went with it —
    ``● agent worker · running`` and 32 blank cells before ``6s``."""

    header = _single_header(60, idle_ms=None)
    assert _MODEL not in header
    assert re.fullmatch(r"│ ● agent worker · running {2,}6s · 15 tok", header), header


def test_the_batch_header_fills_the_same_way_and_keeps_its_counts() -> None:
    snaps = [
        _snap(k, model=_MODEL, elapsed_ms=10_000, tokens=1_500, state=("running", "done", "done")[k])
        for k in range(3)
    ]
    header = _screen(format_panel(snaps, tasks=("a", "b", "c"), width=60, profile="worker"))[0]
    assert _MODEL not in header
    assert re.fullmatch(
        r"│ 3 agents · worker · 1 running · 2 done {2,}10s · 1\.5k tok", header
    ), header


@pytest.mark.parametrize("model", [_MODEL, "o3"])
def test_the_elapsed_time_is_still_the_last_term_to_go(model: str) -> None:
    """Greedy, but not past the clock: a header that cannot show the elapsed
    time shows no other optional term — ``o3`` is short enough to fit where
    the time does not, so it is the model that would slip in without it."""

    for width in range(14, 141):
        snap = _snap(model=model, elapsed_ms=79_000, tokens=1_500, cost=0.0093,
                     activity=(_call("bash", "sleep 12", "running"),))
        header = _screen(format_panel([snap], width=width, idle_ms=[7_000]))[0]
        if any(t in header for t in ("agent worker", f" · {model} · ", " tok", "$0.0093", "idle ")):
            assert re.search(r" 1m26s( · |$)", header), (width, header)
    for width in range(14, 141):
        snaps = [_snap(k, model="o3", elapsed_ms=10_000, tokens=1_500, state=("running", "done")[k])
                 for k in range(2)]
        header = _screen(format_panel(snaps, tasks=("a", "b"), width=width, profile="w"))[0]
        if any(t in header for t in (" · w · ", " · o3 · ", " tok")):
            assert re.search(r" 10s( · |$)", header), (width, header)


def test_no_header_term_comes_and_goes_as_the_tokens_grow() -> None:
    """The tokens are judged at ``12.3k tok`` from the start — before the
    first reply too — so neither they nor a term after them come and go as a
    child's context grows."""

    for width in range(20, 141):
        others, shown = set(), set()
        for tokens in (0, 15, 99, 100, 1_500, 9_999, 12_345):
            header = _single_header(width, elapsed_ms=9_000, idle_ms=7_000, tokens=tokens, cost=0.0093)
            others.add(tuple(t for t in ("agent worker", _MODEL, "idle ", "$0.0093") if t in header))
            if tokens:
                shown.add(" tok" in header)
        assert len(others) == 1 and len(shown) == 1, (width, others, shown)


@pytest.mark.parametrize("phase", ["thinking", "starting"])
def test_no_header_term_comes_and_goes_with_the_state_word(phase: str) -> None:
    """The slack for the widest state word is pinned (the delta review's
    mutant ``slack=0`` survived and flipped the model and the tokens)."""

    for width in range(30, 121):
        a = _single_header(width, state="running", elapsed_ms=72_000, tokens=1_500)
        if phase == "thinking":
            b = _single_header(width, state="thinking", elapsed_ms=72_000, tokens=1_500)
        else:
            snap = _snap(model=_MODEL, state="starting", elapsed_ms=72_000, tokens=1_500)
            b = _screen(format_panel([snap], width=width, idle_ms=[1_000]))[0]
        for term in (_MODEL, " tok", "agent worker"):
            assert (term in a) == (term in b), (width, term, a, b)


def test_no_header_term_comes_and_goes_with_the_clock() -> None:
    """At every width, which of the profile, model, tokens and cost a header
    shows is one answer for every elapsed time under ten minutes and every
    idle time — the idle term itself is the only one that comes and goes."""

    for width in range(20, 141):
        seen = set()
        for elapsed_ms in (0, 9_000, 10_000, 59_000, 60_000, 61_000, 299_000):
            for idle_ms in (None, 1_000, 7_000, 9_000, 10_000, 59_000, 60_000, 299_000):
                header = _single_header(
                    width, elapsed_ms=elapsed_ms, idle_ms=idle_ms, tokens=1_500, cost=0.0093
                )
                seen.add(tuple(t for t in ("agent worker", _MODEL, " tok", "$0.0093") if t in header))
        assert len(seen) == 1, (width, seen)


def test_the_batch_header_s_terms_do_not_move_with_the_clock() -> None:
    # From 31, where ``3 agents · 2 running · 1 done`` fits: narrower, the
    # counts alone are wider than the row, and that is the one place they are
    # cut (ADR-0278 D4).
    for width in range(31, 141):
        seen = set()
        for elapsed_ms in (0, 9_000, 10_000, 59_000, 60_000, 299_000):
            snaps = [
                _snap(k, model=_MODEL, elapsed_ms=elapsed_ms, tokens=1_500, cost=0.01,
                      state=("running", "done", "running")[k])
                for k in range(3)
            ]
            header = _screen(format_panel(snaps, tasks=("a", "b", "c"), width=width, profile="worker"))[0]
            assert "1 done" in header and "2 running" in header, (width, header)
            seen.add(tuple(t for t in (" worker", _MODEL, " tok", "$0.0300") if t in header))
        assert len(seen) == 1, (width, seen)


# === G3: the Esc frame's fixed phrases, whole or not at all ===================


_PHRASES = (
    "stopped — the turn was cancelled",
    "ask to continue them",
    "no session to resume",
    "Ask to continue it and the model can resume it.",
    "It has no session, so it cannot be resumed.",
)


def _esc_frames(width: int, *, session: bool) -> tuple[list[str], list[str]]:
    sid: dict[str, Any] = {"session_id": "s-1" if session else None, "usage": SubagentUsage(turns=2)}
    live = [_snap(k, elapsed_ms=4_000, activity=(_call("bash", f"sleep {6 + 4 * k}", "running"),))
            for k in range(3)]
    batch = _screen(format_panel(live, tasks=("a", "b", "c"), width=width, cancelled=True,
                                 results={k: _stopped(k, **sid) for k in range(3)}))
    one = _screen(format_panel(live[:1], width=width, cancelled=True, results={0: _stopped(**sid)}))
    return batch, one


def test_the_esc_header_drops_its_hint_whole() -> None:
    """The live grid (fix-r1 ``esc-resize-80x30/grid-esc-resize-60+4s.txt``):
    at 60 columns the batch header read ``… · ask to cont…``."""

    batch, _ = _esc_frames(60, session=True)
    assert batch[0] == "│ 3 agents · stopped — the turn was cancelled", batch[0]
    batch, _ = _esc_frames(80, session=True)
    assert batch[0] == "│ 3 agents · stopped — the turn was cancelled · ask to continue them"


@pytest.mark.parametrize("session", [True, False])
def test_no_fixed_phrase_of_the_esc_frame_is_cut_mid_word(session: bool) -> None:
    for width in range(20, 121):
        batch, one = _esc_frames(width, session=session)
        for row in (batch[0], one[0], one[4]):
            assert "…" not in row, (width, row)
            for phrase in _PHRASES:
                # Whole, or not begun: the first three words of a phrase on
                # the row mean all of it is.
                if " ".join(phrase.split(" ")[:3]) in row:
                    assert phrase in row, (width, phrase, row)
        assert batch[0].startswith("│ 3 agents · stopped"), (width, batch[0])
        assert one[4] in ("│", "│   " + _PHRASES[3 if session else 4]), (width, one[4])


def test_the_esc_header_judges_its_numbers_at_their_width() -> None:
    """GUARD: the Esc frame does not tick, so the greedy fill judges its
    numbers as they are — a reserve would only cost room (a first draft of
    G2 reserved them and ``2 turns`` went at 66 columns, where round 1 and
    the room both had it)."""

    _, one = _esc_frames(66, session=True)
    assert one[0].endswith("— the turn was cancelled   2 turns · 4s"), one[0]
    for width in range(20, 121):
        _, one = _esc_frames(width, session=True)
        head = one[0]
        if head.endswith(" 4s") and "2 turns" not in head:
            # the turns did not fit beside what the header kept
            assert cell_len(head) + len("2 turns · ") > width, (width, head)


def test_a_cut_call_s_no_result_is_whole_or_absent() -> None:
    snaps = [_snap(k, elapsed_ms=4_000, activity=(_call("bash", "sleep 1000 && echo " + "x" * 40, "running"),))
             for k in range(5)]
    for width in range(20, 121):
        rows = _screen(format_panel(snaps, tasks=["t"] * 5, width=width, height=6, cancelled=True,
                                    results={k: _stopped(k) for k in range(5)}))
        for row in rows[1:]:
            if " · no" in row:
                assert " · no result" in row, (width, row)


def test_the_files_a_stopped_member_changed_keep_their_room() -> None:
    """Dropping ``· no result`` whole must not take room from what follows it
    (a first draft of G3 shrank ``_now``'s own room, and the live Esc frame at
    60 columns read ``· changed: …`` where round 1 named ``c-out.txt``)."""

    snaps = [
        _snap(k, elapsed_ms=4_000, activity=((_call("write", "c-out.txt"),) if k == 2 else ())
              + (_call("bash", f"sleep {(6, 10, 14)[k]}", "running"),))
        for k in range(3)
    ]
    results = {k: _stopped(k, session_id="s", changed_files=("c-out.txt",) if k == 2 else ())
               for k in range(3)}
    for width in range(50, 121):
        rows = _screen(format_panel(snaps, tasks=("a", "b", "c"), width=width, height=7,
                                    cancelled=True, results=results))
        if "changed:" in rows[6]:
            assert not rows[6].endswith("changed: …"), (width, rows[6])
    rows = _screen(format_panel(snaps, tasks=("a", "b", "c"), width=60, height=7,
                                cancelled=True, results=results))
    assert rows[6] == "│      ✓ ■ $ sleep 14 · no result · changed: c-out.txt", rows[6]


def test_a_stopped_call_is_cut_where_round_1_cut_it() -> None:
    """GUARD: dropping ``· no result`` whole changes nothing where it fits —
    the call is cut at the same cell as before (a first draft measured its
    room before the ``■`` and gave it two more)."""

    long = "sleep 1000 && echo the slow step has finished at last and then some more words to cut"
    snaps = [_snap(k, elapsed_ms=4_000, activity=(_call("bash", long, "running"),)) for k in range(5)]
    results = {k: _stopped(k) for k in range(5)}
    one_row = _screen(format_panel(snaps, tasks=[f"task {k}" for k in range(5)], width=80, height=6,
                                   cancelled=True, results=results))
    assert one_row[1] == "│ ■ 1 task 0  ■ $ sleep 1000 && echo the slow step has fin… · no result       4s"
    two_row = _screen(format_panel(snaps[:2], tasks=("a", "b"), width=60, cancelled=True,
                                   results={0: results[0], 1: results[1]}))
    assert two_row[2] == "│      ■ $ sleep 1000 && echo the slow step h… · no result", two_row[2]


# === G4: the window =========================================================


def test_a_call_the_old_window_turned_away_still_shows_while_it_runs() -> None:
    """The delta review's ``stream_probe.py`` case 7: past 32 parallel calls
    the newest was never added, and once the others ended the panel said
    ``thinking`` while it ran."""

    for calls in (33, 40, 100):
        state = _StreamState()
        reduce_event(state, {"type": "agent_start"})
        reduce_event(state, {"type": "turn_start"})
        for k in range(calls):
            _start(state, str(k), "grep" if k == calls - 1 else "read", path=f"f{k}")
        for k in range(calls - 1):
            _end(state, str(k))
        shown = activity_of(state, live=True)
        assert ("grep", f"f{calls - 1}", "running") in [(a.name, a.args, a.status) for a in shown]
        header = _screen(format_panel([_snap(activity=shown)], width=60))[0]
        assert " · running" in header, (calls, header)


def test_an_open_call_leaves_the_window_when_the_run_ends() -> None:
    """GUARD: the run-end half of the round-1 test (the delta review's mutant
    "no trim at ``agent_end``" survived — only a new turn was tested)."""

    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    _start(state, "0", "grep", path="large-tree")
    for k in range(1, 5):
        _start(state, str(k), "read", path=f"file{k}.txt")
    for k in range(1, 5):
        _end(state, str(k))
    assert len(state.recent) == ACTIVITY_MAX + 1
    reduce_event(state, {"type": "agent_end", "messages": []})
    assert len(state.recent) == ACTIVITY_MAX
    assert [(a.args, a.status) for a in activity_of(state, live=True)] == [
        (f"file{k}.txt", "ok") for k in range(1, 5)
    ]


def test_the_window_is_still_bounded() -> None:
    state = _StreamState()
    reduce_event(state, {"type": "agent_start"})
    reduce_event(state, {"type": "turn_start"})
    for k in range(stream_module._RECENT_MAX * 3):
        _start(state, f"c{k}", "read", path=f"f{k}")
    assert len(state.recent) == stream_module._RECENT_MAX == 256


# === G5: bounded work, the same cut ==========================================


def _linear_cut(text: str, cells: int) -> str:
    """``_cut_to_cells`` as round 1 shipped it: accumulate, then drop one
    character at a time while the whole is too wide."""

    measure = panel_module._cells
    if cells <= 0:
        return ""
    if measure(text) <= cells:
        return text
    out: list[str] = []
    used = 0
    for char in text:
        width = measure(char)
        if used + width > cells:
            break
        out.append(char)
        used += width
    while out and measure("".join(out)) > cells:
        out.pop()
    return "".join(out)


_SHAPES = (
    [chr(c) for c in range(32, 127)],
    [chr(c) for c in range(0x4E00, 0x4E00 + 60)] + [chr(c) for c in range(0xAC00, 0xAC00 + 60)] + list("ab ,"),
    ["❤️", "❤", "️", "︎", "‍", "👩", "💻", "👩‍💻", "🇦", "🇰", "🏽", "👍🏽",
     "́", "é", "😀", "☺", "a", " ", "한"],
)


@pytest.mark.parametrize("alphabet", _SHAPES, ids=["ascii", "cjk", "emoji"])
def test_the_cut_is_the_one_the_linear_loop_found(alphabet: list[str]) -> None:
    rng = random.Random(132)
    for _ in range(1_500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 90)))
        for cells in (0, 1, 2, 5, 13, 24, 40):
            got = panel_module._cut_to_cells(text, cells)
            assert got == _linear_cut(text, cells), (text, cells)
            assert cell_len(got) <= cells and get_cwidth(got) <= max(cells, 0)


def test_a_cut_takes_a_logarithmic_number_of_measures(monkeypatch: pytest.MonkeyPatch) -> None:
    """The delta review's finding 5: the shrink measured the whole prefix once
    per character dropped — 8 members with 64 KiB ``❤️`` tasks took 430 ms a
    frame. A ``_flatten`` cut of 2 048 code points of them now measures a
    handful of times."""

    calls = 0
    measure = panel_module._cells

    def counting(text: str) -> int:
        nonlocal calls
        calls += 1
        return measure(text)

    monkeypatch.setattr(panel_module, "_cells", counting)
    out = panel_module._cut_to_cells("❤️" * 1_024, 511)
    assert measure(out) <= 511 and out == "❤️" * 255 + "❤", repr(out[-4:])
    assert calls <= 16, calls


def test_one_character_s_cached_width_is_its_measure() -> None:
    rng = random.Random(7)
    chars = [chr(c) for c in range(0x20, 0x3000)] + [chr(rng.randint(0x1F000, 0x1FAFF)) for _ in range(800)]
    for char in [*chars, "️", "︎", "‍", "́"]:
        assert panel_module._char_cells(char) == panel_module._cells(char), hex(ord(char))


def test_the_prompt_toolkit_measure_counts_a_long_string_the_same() -> None:
    for text in ("❤️" * 300, "🇦" * 70 + "TAIL", "한글 " * 40, "é" * 90, "abc" * 30):
        assert panel_module._ptk_cells(text) == get_cwidth(text), repr(text[:8])


def test_a_cached_label_draws_the_frame_a_fresh_one_does() -> None:
    snaps = [_snap(k, elapsed_ms=9_000, tokens=15) for k in range(3)]
    tasks = ("❤️" * 400, "한" * 300, "Read notes.txt and wait six seconds, then report.")
    panel_module._flat_label.cache_clear()
    cold = format_panel(snaps, tasks=tasks, width=80, height=7)
    warm = format_panel(snaps, tasks=tasks, width=80, height=7)
    assert cold == warm
    assert panel_module._flat_label.cache_info().hits >= 3


def test_a_frame_of_eight_long_emoji_tasks_is_drawn_in_bounded_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole frame, counted in whole-string measures rather than timed
    (a timing test is a flake): round 1 took tens of thousands here."""

    calls = 0
    measure = panel_module._cells

    def counting(text: str) -> int:
        nonlocal calls
        calls += 1
        return measure(text)

    monkeypatch.setattr(panel_module, "_cells", counting)
    cache = getattr(panel_module, "_flat_label", None)
    if cache is not None:
        cache.cache_clear()  # the first frame of a group, the worst one
    child = "❤️" * 256
    snaps = [
        _snap(k, elapsed_ms=1_000, tokens=10, last_text=child,
              activity=tuple(_call("bash", child, s) for s in ("ok", "failed", "no_result", "running")))
        for k in range(8)
    ]
    format_panel(snaps, tasks=("❤️" * 32_768,) * 8, width=120,
                 height=panel_height(8, terminal_rows=40), idle_ms=[7_000] * 8)
    assert calls < 2_500, calls


# === Finish: the boundaries the round-2 delta review's mutants found unpinned =


_SLOW = "sleep 1000 && echo the slow step has finished at last, and every word after this is cut"


def _stopped_call_rows(widths: range) -> list[tuple[int, str]]:
    """The two-row Esc frame's first call row, ``■ $ sleep 1000 && …``."""

    snaps = [_snap(k, elapsed_ms=4_000, activity=(_call("bash", _SLOW, "running"),)) for k in range(2)]
    results = {k: _stopped(k) for k in range(2)}
    return [
        (width, _screen(format_panel(snaps, tasks=("a", "b"), width=width, cancelled=True,
                                     results=results))[2])
        for width in widths
    ]


def test_a_stopped_call_left_fewer_than_twelve_cells_drops_its_no_result() -> None:
    """M9 (``call < 0`` for ``call < _NUMBERS_FLOOR``) survived: no test held
    the 12-cell floor ``· no result`` leaves the call. At 26-32 columns the
    call keeps under 12 cells beside it however the ``■`` is counted (the
    delta review's finding 3: it is counted twice, so 33-34 drop it too —
    those widths are left out here), so it goes; from 36 it stays."""

    for width, row in _stopped_call_rows(range(26, 33)):
        assert "no result" not in row and "■ $ sleep" in row, (width, row)
    for width, row in _stopped_call_rows(range(36, 81)):
        assert row.endswith("… · no result"), (width, row)


def test_the_room_no_result_leaves_goes_to_the_call() -> None:
    """M20 (``· no result`` dropped, its room not given back) survived. With
    it dropped, the call runs to the right edge — within the two cells
    finding 3's double-counted ``■`` costs, which a fix would only narrow."""

    for width, row in _stopped_call_rows(range(20, 81)):
        assert width - 2 <= cell_len(row) <= width, (width, cell_len(row), row)


@pytest.mark.parametrize(
    ("session", "width"),
    [(True, 51), (False, 47)],
    ids=["resume-hint", "no-session-hint"],
)
def test_the_one_child_esc_hint_is_shown_when_it_fits_exactly(session: bool, width: int) -> None:
    """M11 (``<`` for ``<=``) survived: no test drew the last row at the
    width it fills exactly — 51 columns for ``Ask to continue it and the
    model can resume it.``, 47 for the no-session hint."""

    hint = "Ask to continue it and the model can resume it." if session else (
        "It has no session, so it cannot be resumed."
    )
    live = _snap(elapsed_ms=4_000, activity=(_call("bash", "sleep 6", "running"),))
    result = {0: _stopped(session_id="s-1" if session else None, usage=SubagentUsage(turns=2))}
    rows = _screen(format_panel([live], width=width, cancelled=True, results=result))
    assert rows[4] == "│   " + hint and cell_len(rows[4]) == width, rows[4]
    rows = _screen(format_panel([live], width=width - 1, cancelled=True, results=result))
    assert rows[4] == "│", rows[4]


def test_the_one_child_esc_header_keeps_its_phrase_before_the_turns() -> None:
    """M22 (the phrase ranked below the turns) survived: at 55-64 columns
    the header has room for ``— the turn was cancelled`` or ``2 turns``, not
    both, and it is the phrase that stays (ADR-0278 D7)."""

    live = _snap(elapsed_ms=4_000, activity=(_call("bash", "sleep 6", "running"),))
    result = {0: _stopped(session_id="s-1", usage=SubagentUsage(turns=2))}
    for width in (57, 60, 62):
        head = _screen(format_panel([live], width=width, cancelled=True, results=result))[0]
        assert re.fullmatch(r"│ ■ agent worker · stopped — the turn was cancelled {2,}4s", head), (
            width,
            head,
        )


def test_a_label_one_cell_too_wide_for_the_gap_is_cut_in_the_middle() -> None:
    """M26 (``_label_room`` leaving a 1-cell gap) survived: a label one cell
    too wide to sit whole two cells from the numbers was laid out whole, and
    the row cut its END to make the gap. At 80 columns and ``9s · 15`` a
    label is whole up to 64 cells; from 65 it is cut in the middle, at the
    reserve, and keeps ``the end.``."""

    snaps = [_snap(k, elapsed_ms=9_000, tokens=15) for k in range(2)]
    for cells in range(58, 72):
        label = "Start " + "m" * (cells - len("Start  the end.")) + " the end."
        row = _screen(format_panel(snaps, tasks=(label, "short"), width=80))[1]
        assert re.fullmatch(r"│ ● 1  Start m+(…m+)? the end\. {2,}9s · 15", row), (cells, row)
        assert ("…" in row) == (cells > 64), (cells, row)


def test_no_batch_header_term_comes_and_goes_as_the_tokens_grow() -> None:
    """M33 (the batch header's tokens judged without their reserve) survived:
    the growing-tokens test held the one-child header only. A batch's tokens
    are judged at ``12.3k tok`` too — before the first reply as well — so
    neither they nor the cost come and go as the context grows."""

    for width in range(31, 141):
        others, shown = set(), set()
        for tokens in (0, 15, 99, 100, 1_500, 9_999, 12_345):
            snaps = [
                _snap(k, model=_MODEL, elapsed_ms=9_000, tokens=tokens, cost=0.01,
                      state=("running", "done", "running")[k])
                for k in range(3)
            ]
            header = _screen(
                format_panel(snaps, tasks=("a", "b", "c"), width=width, profile="worker")
            )[0]
            others.add(tuple(t for t in (" worker", _MODEL, "$0.0300") if t in header))
            if tokens:
                shown.add(" tok" in header)
        assert len(others) == 1 and len(shown) == 1, (width, others, shown)
