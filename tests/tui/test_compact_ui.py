"""Compaction UX: the live "Compacting context…" indicator + the optional
``hide_compaction_summary`` display gate.

Covers the two feature tracks:

1. :func:`aelix_coding_agent.tui.shell._drive_compaction_indicator` — drives the
   working-row indicator from the harness ``compaction_start`` /
   ``compaction_end`` subscriber events so a manual ``/compact`` (which dispatches
   outside the ``set_running`` turn wrapper) shows a live spinner instead of a
   frozen prompt. Save/restore keeps an in-flight turn's row intact.
2. ``hide_compaction_summary`` — a persisted DISPLAY gate: ``/compact`` shows a
   terse line instead of the full summary panel, and transcript replay collapses
   the persisted compaction-summary message to a one-line marker. The summary
   ALWAYS stays in the LLM context — this gates DISPLAY only.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

from aelix_agent_core.session.context import create_compaction_summary_message
from aelix_ai.settings.settings_manager import SettingsManager
from aelix_ai.settings.storage import InMemorySettingsStorage
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command
from aelix_coding_agent.tui.render import EventRenderer
from aelix_coding_agent.tui.shell import _drive_compaction_indicator
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


def _plain(renderable: object) -> str:
    console = Console(file=io.StringIO(), width=200, force_terminal=False)
    console.print(renderable)
    return console.file.getvalue()  # type: ignore[attr-defined]


# ============================================================================
# Feature 1 — the "Compacting context…" working-row indicator
# ============================================================================


def _headless_chrome() -> AelixChrome:
    console = Console(file=io.StringIO(), force_terminal=True, width=80)
    return AelixChrome(console=console)


def test_compaction_indicator_manual_cycle() -> None:
    """A manual /compact (idle chrome) shows the indicator on start and clears
    it on end — the row is invisible before and after."""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _headless_chrome()
        state: dict[str, Any] = {"active": False, "prev_msg": None, "prev_visible": False}

        assert chrome.get_working_visible() is False
        _drive_compaction_indicator(chrome, state, "compaction_start")
        assert chrome.get_working_visible() is True
        assert chrome.get_working_message() == "Compacting context…"

        _drive_compaction_indicator(chrome, state, "compaction_end")
        assert chrome.get_working_visible() is False
        assert chrome.get_working_message() is None
        assert state["active"] is False


def test_compaction_indicator_restores_prior_working_row() -> None:
    """When a working row is already up (e.g. an extension indicator / mid-turn),
    the matched end restores the exact prior message + visibility."""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _headless_chrome()
        chrome.set_working_message("Custom indicator")
        chrome.set_working_visible(True)
        state: dict[str, Any] = {"active": False, "prev_msg": None, "prev_visible": False}

        _drive_compaction_indicator(chrome, state, "compaction_start")
        assert chrome.get_working_message() == "Compacting context…"

        _drive_compaction_indicator(chrome, state, "compaction_end")
        # Restored, not blanked.
        assert chrome.get_working_message() == "Custom indicator"
        assert chrome.get_working_visible() is True


def test_compaction_indicator_double_start_is_idempotent() -> None:
    """A second start (defensive) must not overwrite the captured prior state;
    the single matched end still restores the ORIGINAL prior row."""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _headless_chrome()
        chrome.set_working_message("Original")
        chrome.set_working_visible(True)
        state: dict[str, Any] = {"active": False, "prev_msg": None, "prev_visible": False}

        _drive_compaction_indicator(chrome, state, "compaction_start")
        _drive_compaction_indicator(chrome, state, "compaction_start")
        assert state["prev_msg"] == "Original"  # captured once, not clobbered

        _drive_compaction_indicator(chrome, state, "compaction_end")
        assert chrome.get_working_message() == "Original"
        assert chrome.get_working_visible() is True


def test_compaction_indicator_turn_start_heals_stranded() -> None:
    """SELF-HEAL: a compaction cancelled via BaseException (Ctrl+C) never emits
    compaction_end, stranding active=True. The next turn_start must restore the
    prior working row and clear the active flag (else "Compacting context…"
    sticks on screen). Locks the "turn_start" literal + the restore path."""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _headless_chrome()
        state: dict[str, Any] = {"active": False, "prev_msg": None, "prev_visible": False}

        # Compaction started but end never arrived (BaseException-cancelled).
        _drive_compaction_indicator(chrome, state, "compaction_start")
        assert state["active"] is True
        assert chrome.get_working_visible() is True

        # The next turn self-heals the stranded row.
        _drive_compaction_indicator(chrome, state, "turn_start")
        assert state["active"] is False
        assert chrome.get_working_visible() is False
        assert chrome.get_working_message() is None


def test_compaction_indicator_turn_start_noop_when_inactive() -> None:
    """turn_start fires on EVERY turn; with no active indicator it must NOT touch
    the working row (guards against clobbering a normal turn's 'Working…')."""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _headless_chrome()
        chrome.set_working_message("Working on a real turn")
        chrome.set_working_visible(True)
        state: dict[str, Any] = {"active": False, "prev_msg": None, "prev_visible": False}

        _drive_compaction_indicator(chrome, state, "turn_start")
        assert chrome.get_working_message() == "Working on a real turn"
        assert chrome.get_working_visible() is True
        assert state["active"] is False


def test_compaction_indicator_end_without_start_is_noop() -> None:
    """An unmatched end (no active indicator) must not touch the working row."""

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _headless_chrome()
        chrome.set_working_message("Untouched")
        chrome.set_working_visible(True)
        state: dict[str, Any] = {"active": False, "prev_msg": None, "prev_visible": False}

        _drive_compaction_indicator(chrome, state, "compaction_end")
        assert chrome.get_working_message() == "Untouched"
        assert chrome.get_working_visible() is True


# ============================================================================
# Feature 2 — hide_compaction_summary setting (persistence)
# ============================================================================


def test_hide_compaction_summary_default_false() -> None:
    manager = SettingsManager.in_memory()
    assert manager.get_hide_compaction_summary() is False


async def test_hide_compaction_summary_roundtrip() -> None:
    # async so the setter's ``_save()`` (which schedules an async write via
    # ``asyncio.ensure_future``) has a live event loop — a sync test can hit a
    # closed/absent loop left by a prior test (asyncio_mode=auto).
    manager = SettingsManager.in_memory()
    manager.set_hide_compaction_summary(True)
    await manager.flush()
    assert manager.get_hide_compaction_summary() is True
    manager.set_hide_compaction_summary(False)
    await manager.flush()
    assert manager.get_hide_compaction_summary() is False


def test_hide_compaction_summary_reads_camel_json() -> None:
    """The on-disk key is pi-shaped camelCase ``hideCompactionSummary``."""

    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _: '{"hideCompactionSummary": true}')
    manager = SettingsManager.from_storage(storage)
    assert manager.get_hide_compaction_summary() is True


# ============================================================================
# Feature 2 — /compact handler DISPLAY gate
# ============================================================================


class _CompactHarness:
    async def compact(self, _custom_instructions: str | None = None) -> object:
        return SimpleNamespace(tokens_before=1234, summary="SECRET_SUMMARY_BODY")


class _SM:
    def __init__(self, hide: bool) -> None:
        self._hide = hide

    def get_hide_compaction_summary(self) -> bool:
        return self._hide


def _run_compact(ctx: CommandContext) -> None:
    import asyncio
    from collections.abc import Coroutine
    from typing import cast

    command = match_command("/compact", ctx.commands)
    assert command is not None and command.handler is not None
    asyncio.run(cast(Coroutine[Any, Any, None], command.handler(ctx, "")))


def _compact_ctx(committed: list[object], *, hide: bool) -> CommandContext:
    return CommandContext(
        chrome=SimpleNamespace(),  # type: ignore[arg-type] — handler never touches chrome
        harness=_CompactHarness(),  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/work",
        commands=list(BUILTIN_COMMANDS),
        settings_manager=_SM(hide),  # type: ignore[arg-type]
    )


def test_compact_handler_hides_summary_when_enabled() -> None:
    committed: list[object] = []
    _run_compact(_compact_ctx(committed, hide=True))
    text = "".join(_plain(c) for c in committed)
    assert "Compacted context" in text
    assert "SECRET_SUMMARY_BODY" not in text  # summary body suppressed


def test_compact_handler_shows_summary_when_disabled() -> None:
    committed: list[object] = []
    _run_compact(_compact_ctx(committed, hide=False))
    text = "".join(_plain(c) for c in committed)
    assert "Compacted context" in text
    assert "SECRET_SUMMARY_BODY" in text  # full summary shown (prior behavior)


# ============================================================================
# Feature 2 — transcript replay DISPLAY gate
# ============================================================================


def _replay_renderer(hide: bool) -> tuple[EventRenderer, list[Any]]:
    commits: list[Any] = []
    tails: list[str] = []
    r = EventRenderer(commit=commits.append, set_tail=tails.append, width=80)
    r.hide_compaction_summary = hide
    return r, commits


def test_replay_collapses_compaction_summary_when_hidden() -> None:
    r, commits = _replay_renderer(hide=True)
    msg = create_compaction_summary_message(
        "MY_SECRET_SUMMARY", 100, "2026-01-01T00:00:00Z"
    )
    r.replay([msg])
    text = "".join(_plain(c) for c in commits)
    assert "MY_SECRET_SUMMARY" not in text
    assert "summary hidden" in text


def test_replay_shows_compaction_summary_when_visible() -> None:
    r, commits = _replay_renderer(hide=False)
    msg = create_compaction_summary_message(
        "MY_SECRET_SUMMARY", 100, "2026-01-01T00:00:00Z"
    )
    r.replay([msg])
    text = "".join(_plain(c) for c in commits)
    assert "MY_SECRET_SUMMARY" in text


def test_replay_normal_user_message_never_collapsed() -> None:
    """The gate keys on the compaction-summary prefix only — an ordinary user
    message renders in full even when the gate is on."""

    from aelix_ai.messages import TextContent, UserMessage

    r, commits = _replay_renderer(hide=True)
    r.replay([UserMessage(content=[TextContent(text="just a normal question")])])
    text = "".join(_plain(c) for c in commits)
    assert "just a normal question" in text
