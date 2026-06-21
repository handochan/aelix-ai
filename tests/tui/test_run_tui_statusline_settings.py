"""WP-2 (ADR-0160) — run_tui threads SettingsManager + statusline_action wiring.

Builds on the FakeRuntime/FakeHarness used by test_run_tui_smoke.py to assert the
new ImplFoundation seams: the CommandContext carries the threaded SettingsManager,
the /statusline command routes through the wired statusline_action, and the
SettingsManager.create factory has no FS side effects when no settings files exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime


async def _wait(predicate, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@asynccontextmanager
async def _harness_chrome() -> AsyncGenerator[tuple[FakeRuntime, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        runtime = FakeRuntime(FakeHarness())
        chrome = AelixChrome()
        yield runtime, chrome, pipe


async def test_run_tui_threads_settings_manager_and_statusline(tmp_path) -> None:
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({"theme": "dark"})

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=sm,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        # /statusline routes to the wired action → multiselect modal opens.
        pipe.send_text("/statusline\n")
        await _wait(lambda: chrome.is_modal_open())
        pipe.send_text("\x1b")  # Esc the picker (no write)
        await _wait(lambda: not chrome.is_modal_open())
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0


async def test_run_tui_statusline_unavailable_without_action() -> None:
    # No statusline_action wired in a bare run_tui still degrades gracefully: the
    # command exists but the handler commits "unavailable" when no host wired it.
    # (run_tui always wires it, so we test the handler path via CommandContext.)
    from aelix_coding_agent.tui.commands import (
        BUILTIN_COMMANDS,
        CommandContext,
        match_command,
    )

    committed: list[object] = []

    class _Chrome:
        pass

    ctx = CommandContext(
        chrome=_Chrome(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        commit=committed.append,
        cwd=".",
        commands=list(BUILTIN_COMMANDS),
        statusline_action=None,
    )
    cmd = match_command("/statusline", ctx.commands)
    assert cmd is not None and cmd.handler is not None
    await cmd.handler(ctx, "")
    from rich.text import Text

    assert committed and isinstance(committed[0], Text)
    assert "unavailable" in committed[0].plain.lower()


async def test_run_tui_statusline_action_invoked_when_wired() -> None:
    from aelix_coding_agent.tui.commands import (
        BUILTIN_COMMANDS,
        CommandContext,
        match_command,
    )

    calls = {"n": 0}

    async def _action() -> None:
        calls["n"] += 1

    ctx = CommandContext(
        chrome=object(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        commit=lambda r: None,
        cwd=".",
        commands=list(BUILTIN_COMMANDS),
        statusline_action=_action,
    )
    cmd = match_command("/statusline", ctx.commands)
    assert cmd is not None and cmd.handler is not None
    await cmd.handler(ctx, "")
    assert calls["n"] == 1


async def test_settings_opens_modal_through_run_tui(tmp_path) -> None:
    # End-to-end through the real run_tui /settings driver: the expanded menu
    # opens (the modal mounts) and Esc closes it without crashing the REPL. The
    # per-row toggle/cycle/clamp + dual-write + persist logic is covered
    # deterministically by tests/tui/test_settings_rows.py (driving the modal's
    # type-to-filter + accept by raw pipe bytes is inherently timing-flaky).
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({})

    async with _harness_chrome() as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=sm,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await _wait(lambda: chrome.is_modal_open())  # expanded menu mounted
        pipe.send_text("\x1b")  # Esc closes the settings menu
        await _wait(lambda: not chrome.is_modal_open())
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0


async def test_settings_unavailable_without_settings_manager(tmp_path) -> None:
    # No SettingsManager threaded → /settings degrades with a committed message
    # (the modal never opens). run_tui always threads it, so this is the honest
    # belt-and-braces path.
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                # no settings_manager
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        # No modal opens on the degrade path; the REPL survives + quits.
        await asyncio.sleep(0.1)
        assert not chrome.is_modal_open()
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0


async def test_scoped_models_command_routes_through_run_tui(tmp_path) -> None:
    # /scoped-models routes to the wired action. With no model registry threaded,
    # run_scoped_models degrades with a committed "no model registry" line (the
    # modal never opens) — assert the command path is wired + degrades, not crashes.
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({})
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=sm,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/scoped-models\n")
        # No registry → degrade path; just confirm the REPL survives + quits.
        await asyncio.sleep(0.1)
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0


def test_settings_manager_create_no_fs_side_effects(tmp_path) -> None:
    # Constructing SettingsManager over an empty agent dir must NOT create files
    # (read-only on construction; load errors captured, never written).
    from aelix_ai.settings import SettingsManager

    agent_dir = tmp_path / "agent"
    sm = SettingsManager.create(cwd=str(tmp_path), agent_dir=agent_dir)
    # No settings file written by construction.
    assert not (agent_dir / "settings.json").exists()
    # Reads the (empty) merged settings without raising.
    assert sm.get_settings() is not None
    # No load errors over a clean empty dir.
    assert sm.drain_errors() == []
