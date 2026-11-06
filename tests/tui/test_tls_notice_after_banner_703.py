"""#703 fix round 1 - the TUI says a relaxation made before it listened, once, under the banner.

An extension's ``session_start`` runs before ``run_tui`` registers its listener
(``cli/entry.py`` builds the runtime first), so a request it sent that failed on
Python 3.13's strict check alone was relaxed and answered with nobody hearing of
it: the dim line was never shown. The listener registration now hands such a
relaxation over (``_tls_strict.add_relaxation_listener``), and the TUI holds the
line until its banner is up, so it reads under it rather than above it. One made
while the TUI runs is committed at once, as before.
"""

from __future__ import annotations

from aelix_ai.providers import _tls_strict
from aelix_ai.providers._tls_strict import Relaxation, reset_strict_relaxation

from tests.tui.test_run_tui_smoke import (
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
    _wait,
)

_RELAXATION = Relaxation(
    host="proxy.corp.example", verify_code=85, verify_message="Missing Authority Key Identifier"
)


async def test_a_relaxation_made_before_the_tui_listened_is_said_once_under_the_banner() -> None:
    reset_strict_relaxation()
    # What maybe_relax_strict_for_session leaves when no listener was registered.
    _tls_strict._relaxation = _RELAXATION
    _tls_strict._unannounced = True
    line = _RELAXATION.notice()
    try:
        async with _harness_chrome() as (runtime, chrome, pipe):
            commits = _spy_commits(chrome)
            task = _launch(runtime, chrome)
            await _wait(lambda: any(line in c for c in commits), what="the notice line")
            pipe.send_text("/quit\n")
            await _quit_within(task)
    finally:
        reset_strict_relaxation()

    assert commits.count(line) == 1
    banner = next(i for i, c in enumerate(commits) if "/help for commands" in c)
    assert commits.index(line) > banner


async def test_a_relaxation_made_while_the_tui_runs_is_said_at_once() -> None:
    reset_strict_relaxation()
    line = _RELAXATION.notice()
    try:
        async with _harness_chrome() as (runtime, chrome, pipe):
            commits = _spy_commits(chrome)
            task = _launch(runtime, chrome)
            await _wait(lambda: any("/help for commands" in c for c in commits), what="the banner")
            await _wait(lambda: chrome.app.is_running)
            _tls_strict._relaxation = _RELAXATION
            _tls_strict._announce(_RELAXATION)
            await _wait(lambda: any(line in c for c in commits), what="the notice line")
            pipe.send_text("/quit\n")
            await _quit_within(task)
    finally:
        reset_strict_relaxation()

    assert commits.count(line) == 1
