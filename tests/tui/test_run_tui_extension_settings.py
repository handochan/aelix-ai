"""Drive contributed settings through the real modal/key/persistence pipeline."""

import json

from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    _esc_until_settings_closed,
    _harness_chrome,
    _launch,
    _quit_within,
    _wait,
)


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
