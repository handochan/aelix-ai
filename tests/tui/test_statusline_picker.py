"""WP-2 (ADR-0160) — run_statusline_picker DI-flow tests (no prompt-toolkit)."""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.footer_segments import build_footer_registry
from aelix_coding_agent.tui.statusline_picker import run_statusline_picker
from aelix_coding_agent.tui.statusline_store import StatuslineConfig
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


@asynccontextmanager
async def _segments() -> AsyncGenerator[list]:
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=120)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        yield build_footer_registry(ctx)


class _Commits:
    def __init__(self) -> None:
        self.items: list[object] = []

    def __call__(self, renderable: object) -> None:
        self.items.append(renderable)

    def text(self) -> str:
        from rich.text import Text

        return "\n".join(str(i.plain) if isinstance(i, Text) else str(i) for i in self.items)


async def test_confirm_persists_selection_in_registry_order() -> None:
    async with _segments() as segments:
        saved: dict[str, StatuslineConfig] = {}

        def load() -> StatuslineConfig:
            return StatuslineConfig(enabled=["model", "git-branch"])

        def save(cfg: StatuslineConfig) -> None:
            saved["cfg"] = cfg

        async def fake_multiselect(title, options, *, selected, extra_toggles, preview):
            # user adds current-dir; preview must not raise
            assert callable(preview)
            preview(set(selected) | {"current-dir"}, {"use_theme_colors": True})
            chosen = set(selected) | {"current-dir"}
            return chosen, {"use_theme_colors": False}

        commits = _Commits()
        refreshed = {"n": 0}
        await run_statusline_picker(
            segments=segments,
            load=load,
            save=save,
            multiselect=fake_multiselect,
            commit=commits,
            refresh_footer=lambda: refreshed.__setitem__("n", refreshed["n"] + 1),
        )
        cfg = saved["cfg"]
        # persisted in REGISTRY order (current-dir comes before model/git-branch)
        assert cfg.enabled == ["current-dir", "model", "git-branch"]
        assert cfg.use_theme_colors is False
        assert refreshed["n"] == 1
        assert "status line →" in commits.text()


async def test_cancel_does_not_save() -> None:
    async with _segments() as segments:
        saved: dict[str, StatuslineConfig] = {}

        async def fake_multiselect(*a, **k):
            return None  # Esc

        commits = _Commits()
        refreshed = {"n": 0}
        await run_statusline_picker(
            segments=segments,
            load=lambda: StatuslineConfig(enabled=["model"]),
            save=lambda cfg: saved.__setitem__("cfg", cfg),
            multiselect=fake_multiselect,
            commit=commits,
            refresh_footer=lambda: refreshed.__setitem__("n", 1),
        )
        assert "cfg" not in saved
        assert refreshed["n"] == 0


async def test_empty_segments_degrades() -> None:
    commits = _Commits()
    await run_statusline_picker(
        segments=[],
        load=lambda: StatuslineConfig(),
        save=lambda cfg: None,
        multiselect=None,  # never called
        commit=commits,
    )
    assert "no segments" in commits.text().lower()


async def test_save_failure_is_committed_not_raised() -> None:
    async with _segments() as segments:
        def boom(cfg: StatuslineConfig) -> None:
            raise OSError("disk full")

        async def fake_multiselect(*a, **k):
            return {"model"}, {"use_theme_colors": True}

        commits = _Commits()
        await run_statusline_picker(
            segments=segments,
            load=lambda: StatuslineConfig(enabled=["model"]),
            save=boom,
            multiselect=fake_multiselect,
            commit=commits,
        )
        assert "save failed" in commits.text().lower()


async def test_load_failure_is_committed_not_raised() -> None:
    async with _segments() as segments:
        def boom() -> StatuslineConfig:
            raise OSError("boom")

        commits = _Commits()
        await run_statusline_picker(
            segments=segments,
            load=boom,
            save=lambda cfg: None,
            multiselect=None,
            commit=commits,
        )
        assert "load failed" in commits.text().lower()
