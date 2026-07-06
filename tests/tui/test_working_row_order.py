"""#66 item 5 — the "Working…" row renders ABOVE the input prompt (was below).

Asserts on the ACTUAL painted terminal grid (pyte), so it validates the real
chrome body order rather than the internal HSplit child list.
"""

from __future__ import annotations

from _pyte import assert_row_contains, render_chrome_to_screen  # sibling helper
from aelix_coding_agent.tui.chrome import AelixChrome


async def test_working_row_renders_above_input() -> None:
    def build(chrome: AelixChrome) -> None:
        chrome.set_working_visible(True)
        chrome.set_working_message("Crunching")
        chrome.set_running(True)

    display = await render_chrome_to_screen(build_state=build)
    working_row = assert_row_contains(display, "Crunching")
    prompt_row = assert_row_contains(display, "❯")
    assert working_row < prompt_row, (
        f"working row ({working_row}) must be above the input prompt "
        f"({prompt_row})"
    )


async def test_no_working_row_when_idle() -> None:
    # gate_visible keeps the working row at 0 rows when neither visible nor
    # running — the ❯ prompt still renders, and "Working" never appears.
    def build(chrome: AelixChrome) -> None:
        return None

    display = await render_chrome_to_screen(build_state=build)
    assert_row_contains(display, "❯")  # input still present
    assert not any("Working" in row for row in display)
