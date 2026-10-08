"""Drive contributed settings through the real modal/key/persistence pipeline."""

import asyncio
import json
import time

import pytest
from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _launch,
)


async def _wait(predicate, *, what="condition"):
    # Presence and clean shutdown are anti-hang bounds, not timing verdicts.
    deadline = time.monotonic() + 20
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {what}")
        await asyncio.sleep(0.01)


async def _esc_until_settings_closed(chrome, pipe):
    def idle():
        getters = getattr(chrome._input_queue, "_getters", None)
        assert getters is not None
        return any(not future.done() for future in getters)

    for _ in range(6):
        await _wait(lambda: chrome.is_modal_open() or idle(), what="settings driver")
        if not chrome.is_modal_open():
            return
        modal = chrome._modal
        pipe.send_text("\x1b")
        await _wait(lambda prior=modal: chrome._modal is not prior, what="modal close")
    raise AssertionError("Settings did not return to the input loop")


async def _quit_within(task):
    return await asyncio.wait_for(asyncio.shield(task), timeout=20)


async def test_settings_toggle_persists_and_a_fresh_tui_can_turn_it_off(tmp_path):
    path = tmp_path / "global-setting.json"

    def read():
        return json.loads(path.read_text(encoding="utf-8"))["enabled"] if path.exists() else False

    async def write(value):
        path.write_text(json.dumps({"enabled": value}), encoding="utf-8")

    for desired in (True, False):
        extension = Extension(name="example")
        api = ExtensionAPI(extension, _ExtensionRuntime())
        api.register_setting("enabled", label="Memory", get_value=read, set_value=write)
        harness = FakeHarness()
        harness.extension_runner = ExtensionRunner(extensions=[extension])
        async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
            task = _launch(runtime, chrome, settings_manager=SettingsManager.in_memory({}))
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("/settings\n")
            await _wait(lambda: chrome.is_modal_open())
            menu = chrome._modal
            pipe.send_text("Memory\n")
            await _wait(
                lambda expected=desired: read() is expected,
                what="the setting owner to persist the toggle",
            )
            await _wait(lambda prior=menu: chrome._modal is not None and chrome._modal is not prior)
            await _esc_until_settings_closed(chrome, pipe)
            pipe.send_text("/quit\n")
            assert await _quit_within(task) == 0
        assert read() is desired


@pytest.mark.parametrize("initial", [True, False])
async def test_settings_preserves_displayed_intent_after_an_external_change(initial):
    state = {"enabled": initial}
    writes = []

    async def write(value):
        writes.append(value)
        state["enabled"] = value

    extension = Extension(name="example")
    api = ExtensionAPI(extension, _ExtensionRuntime())
    api.register_setting(
        "enabled", label="Memory", get_value=lambda: state["enabled"], set_value=write
    )
    harness = FakeHarness()
    harness.extension_runner = ExtensionRunner(extensions=[extension])
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome, settings_manager=SettingsManager.in_memory({}))
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/settings\n")
        await _wait(lambda: chrome.is_modal_open())
        # Another process changes the global owner after this menu is displayed.
        state["enabled"] = not initial
        pipe.send_text("Memory\n")
        await _wait(lambda: len(writes) == 1)
        await _esc_until_settings_closed(chrome, pipe)
        pipe.send_text("/quit\n")
        assert await _quit_within(task) == 0
    # Selecting the displayed ON row means OFF, even if someone already set OFF.
    assert writes == [not initial] and state["enabled"] is not initial
