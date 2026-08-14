"""Issue #165 — a resumed session must PAINT, not only load.

`aelix --resume <id>` seeded `harness.state.messages` (so /context, /cost and
/stats read correctly) and rendered nothing, because `EventRenderer.replay` had
exactly two call sites and both were in-session swaps. Proven live with a pty
A/B before the fix: `/context` reported "Messages 3.9K tokens" while the screen
held only the banner; the same session through the in-session `/resume` command
replayed in full.

These assert on the PAINTED GRID via `_pyte.render_shell_to_screen`, which drives
the real `run_tui`. That is deliberate: the defect was never in `replay` itself
— it was that nothing called it — so a test that spies on `replay` would pass on
a build where the transcript still never reaches the terminal.

(A first attempt asserted on renderables captured from `chrome.print_above_many`
under the sibling file's `_wait` poll loop. The spy fires and the batch is
correct — measured — but not while that loop is running, so the test timed out
on a working build. The pyte harness has no such interaction.)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aelix_coding_agent.tui.chrome import AelixChrome

# Space-free markers on purpose. ``print_above`` commits inside ``in_terminal()``,
# which erases and repaints the chrome around every write, and ``pyte.Screen``
# has no scrollback — so it models that as overwriting in place and a phrase can
# land split across a row boundary at an arbitrary column. Rows are reassembled
# with no separator (see ``_flat``), which reconstructs a split TOKEN exactly but
# would silently eat the space inside a split PHRASE.
# ``Ctrl+C`` is the banner's only space-free token; ``rstrip`` keeps a row's
# INTERIOR spaces, so "/help for commands" survives reassembly only when it did
# not straddle a row boundary — not something to assert on.
_BANNER_MARK = "Ctrl+C"
_USER_MARK = "OMEGA_USER_QUESTION"
_BOLD_MARK = "OMEGA_BOLD_HEADING"
_ASSISTANT_MD = f"**{_BOLD_MARK}** first.\n\n- OMEGA_ITEM_ONE\n- OMEGA_ITEM_TWO\n"


class _SessionWithEntries:
    """A session whose display tier holds real ``MessageEntry`` rows.

    ``get_branch`` returns session ENTRIES, not messages: `_display_messages`
    feeds the result straight to `build_display_messages`, which iterates it. A
    messages-shaped return makes `run_tui` RAISE — and that raise is visible
    rather than swallowed on purpose, because a silently-failing startup replay
    is indistinguishable from the #165 symptom it is meant to fix.
    """

    def __init__(self, entries: list[Any]) -> None:
        self._entries = entries

    async def get_branch(self, from_id: str | None = None) -> list[Any]:
        return list(self._entries)


def _entries() -> list[Any]:
    from aelix_agent_core.session.entries import MessageEntry
    from aelix_ai.messages import AssistantMessage, TextContent, UserMessage

    return [
        MessageEntry(
            id="u1",
            parent_id=None,
            timestamp="2026-08-14T00:00:00.000Z",
            message=UserMessage(content=[TextContent(text=_USER_MARK)]),
        ),
        MessageEntry(
            id="a1",
            parent_id="u1",
            timestamp="2026-08-14T00:00:01.000Z",
            message=AssistantMessage(content=[TextContent(text=_ASSISTANT_MD)]),
        ),
    ]


async def _render_startup(entries: list[Any], cols: int = 100) -> str:
    from _pyte import render_shell_to_screen  # sibling helper
    from test_run_tui_smoke import FakeHarness, FakeRuntime  # sibling module

    runtime = FakeRuntime(FakeHarness())
    runtime.session = _SessionWithEntries(entries)  # type: ignore[attr-defined]

    async def drive(_chrome: AelixChrome) -> None:
        # Nothing to drive: the startup paint is what is under test. Give the
        # output pump a beat to flush its batch.
        await asyncio.sleep(0.2)

    display = await render_shell_to_screen(
        runtime=runtime, rows=24, cols=cols, drive=drive, include_history=True
    )
    return _flat(display)


def _flat(display: list[str]) -> str:
    """Reassemble the grid so a token split across a row boundary survives."""

    return "".join(row.rstrip() for row in display)


async def test_startup_paints_the_resumed_transcript() -> None:
    """FAILS TODAY: the grid holds the banner and nothing else."""

    screen = await _render_startup(_entries())
    assert _USER_MARK in screen, "the resumed transcript never reached the terminal"
    assert "Resumed" in screen, "the marker line never painted"


async def test_startup_keeps_the_banner_above_the_transcript() -> None:
    """No ``clear()`` at startup — the one thing not copied from the swap path.

    `_replay_after_swap` clears first because it repaints over a used screen. At
    startup a clear would erase the banner printed one line earlier plus
    anything emitted before the TUI came up (the --resume picker, the #98
    unrunnable-model warning). Ordering is banner → transcript → marker.
    """

    screen = await _render_startup(_entries())
    assert _BANNER_MARK in screen, "the banner was erased"
    assert screen.index(_BANNER_MARK) < screen.index(_USER_MARK)
    assert screen.index(_USER_MARK) < screen.index("Resumed")


async def test_startup_renders_the_assistant_body_as_markdown() -> None:
    """#164 rides along: what a resuming user sees must not be raw source."""

    screen = await _render_startup(_entries())
    assert f"**{_BOLD_MARK}**" not in screen  # not the literal source
    assert _BOLD_MARK in screen  # ...but the text itself is there, styled
    assert "OMEGA_ITEM_TWO" in screen


async def test_cold_start_paints_only_the_banner() -> None:
    """A NO-OP GUARD: passes today AND after, deliberately.

    Its value is that it fails if the startup replay ever loses its
    empty-history guard and starts emitting a marker (or a clear) on a normal
    launch — the same guard `_seed_startup_messages` uses.
    """

    screen = await _render_startup([])
    assert _BANNER_MARK in screen  # the launch itself is healthy
    assert "Resumed" not in screen


async def test_startup_survives_a_runtime_with_no_session_member() -> None:
    """``getattr``, not ``runtime_host.session``.

    Most run_tui smokes drive a runtime with no ``session`` member at all, and
    one asserts exactly that; a bare attribute read would red-line the file.
    """

    from _pyte import render_shell_to_screen
    from test_run_tui_smoke import FakeHarness, FakeRuntime

    runtime = FakeRuntime(FakeHarness())
    assert not hasattr(runtime, "session")

    async def drive(_chrome: AelixChrome) -> None:
        await asyncio.sleep(0.1)

    display = await render_shell_to_screen(
        runtime=runtime, rows=24, cols=80, drive=drive, include_history=True
    )
    assert _BANNER_MARK in _flat(display)


# === the startup repaint must not freeze the launch =========================


async def test_chunked_replay_paints_the_same_content_as_one_call() -> None:
    """Chunking is a scheduling change; the CONTENT must not move.

    Not asserted as exact grid equality, and the difference is worth recording
    rather than hiding: each chunk becomes its own ``print_above_many`` batch,
    and every batch carries an ``in_terminal()`` erase/repaint, so the grid's
    whitespace genuinely differs at extreme chunk sizes (measured: 2701 chars
    whole vs 2446 at one message per chunk, with every content marker present in
    both). At the shipped chunk size the two are byte-identical for this
    fixture — the divergence only appears when the batching does.
    """

    import aelix_coding_agent.tui.shell as shell_mod

    marks = (_USER_MARK, _BOLD_MARK, "OMEGA_ITEM_TWO", "Resumed")
    original = shell_mod._STARTUP_REPLAY_CHUNK
    try:
        shell_mod._STARTUP_REPLAY_CHUNK = 10**9  # a single call
        whole = await _render_startup(_entries())
        shell_mod._STARTUP_REPLAY_CHUNK = 1  # one message per call
        finest = await _render_startup(_entries())
        shell_mod._STARTUP_REPLAY_CHUNK = original
        shipped = await _render_startup(_entries())
    finally:
        shell_mod._STARTUP_REPLAY_CHUNK = original

    for mark in marks:
        assert mark in whole, mark
        assert mark in finest, mark
        assert mark in shipped, mark
    # Order is preserved regardless of how the repaint was split.
    for screen in (whole, finest, shipped):
        assert screen.index(_USER_MARK) < screen.index("Resumed")


async def test_the_pyte_history_guard_fires_instead_of_evicting() -> None:
    """A saturated scrollback must raise, not silently drop the oldest rows.

    pyte's history is a ``deque(maxlen=...)`` that evicts from the LEFT without
    a signal, so a test asserting on the TOP of a long transcript would read an
    eviction as "the transcript never painted" and blame the code under test.
    """

    import pytest

    with pytest.raises(AssertionError, match="scrollback saturated"):
        await _render_startup_with_history_cap(_entries(), history_lines=8)


async def _render_startup_with_history_cap(
    entries: list[Any], *, history_lines: int
) -> str:
    from _pyte import render_shell_to_screen
    from test_run_tui_smoke import FakeHarness, FakeRuntime

    runtime = FakeRuntime(FakeHarness())
    runtime.session = _SessionWithEntries(entries)  # type: ignore[attr-defined]

    async def drive(_chrome: AelixChrome) -> None:
        await asyncio.sleep(0.2)

    display = await render_shell_to_screen(
        runtime=runtime,
        rows=24,
        cols=100,
        drive=drive,
        include_history=True,
        history_lines=history_lines,
    )
    return _flat(display)
