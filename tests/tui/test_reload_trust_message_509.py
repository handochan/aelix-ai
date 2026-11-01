"""#509 verify V2 — the trust ``/reload`` saves names ``.agents/skills`` too.

A folder trusted without asking (nothing to gate at launch) is saved as
trusted once ``/reload`` finds resources the predicate counts (#112, ADR-0216).
Since #509 that includes a ``.agents/skills`` here or in a parent, and the
message said "it gained .aelix resources" when only that had arrived.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _quit_within,
    _spy_commits,
    _wait,
)


class _Registry:
    def clear_config_value_cache(self) -> None:
        pass

    def get_available(self) -> list[Any]:
        return []


async def test_the_reload_trust_save_names_both_families() -> None:
    saves: list[bool] = []

    def save() -> bool:
        saves.append(True)
        return True

    async with _harness_chrome(harness=FakeHarness()) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                save_implicit_trust_after_reload=save,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: any("Saved project trust" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)

    assert saves == [True]
    [line] = [c for c in commits if "Saved project trust" in c]
    assert ".aelix" in line and ".agents/skills" in line, line
