"""Real public setting registration, runner aggregation and settings dispatch."""

import json
from pathlib import Path

import pytest
from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionError,
    _ExtensionRuntime,
)
from aelix_coding_agent.tui.extension_settings import (
    apply_extension_setting,
    extension_settings_rows,
)
from aelix_coding_agent.tui.settings_rows import build_settings_rows


def registered(*, name="example", label="Memory", read=None, write=None):
    value = {"enabled": False}
    runtime = _ExtensionRuntime()
    extension = Extension(name=name)
    api = ExtensionAPI(extension, runtime)
    api.register_setting(
        "enabled",
        label=label,
        get_value=read or (lambda: value["enabled"]),
        set_value=write or (lambda new: value.update(enabled=new)),
        description="Global user setting.",
    )
    return extension, runtime, value


async def test_registration_is_lazy_and_sync_toggle_changes_the_actual_owner():
    calls = []
    extension, runtime, value = registered(read=lambda: calls.append("get") or False)
    runner = ExtensionRunner(extensions=[extension])
    sm = SettingsManager.in_memory({})
    rows = extension_settings_rows(runner.get_settings(), build_settings_rows(sm))
    assert calls == [] and len(rows) == 1
    assert rows[0].key == "extension:example:enabled"
    extension, runtime, value = registered()
    rows = extension_settings_rows(ExtensionRunner(extensions=[extension]).get_settings(), [])
    result = await apply_extension_setting(rows[0])
    assert result.kind == "ok" and "on" in result.message
    assert value["enabled"] and rows[0].read(sm) == "on"


async def test_async_setter_persists_to_disk_and_new_getter_reads_it(tmp_path: Path):
    path = tmp_path / "setting.json"

    def read():
        return json.loads(path.read_text(encoding="utf-8"))["enabled"] if path.exists() else False

    async def write(value):
        path.write_text(json.dumps({"enabled": value}), encoding="utf-8")

    extension, runtime, _ = registered(read=read, write=write)
    row = extension_settings_rows(ExtensionRunner(extensions=[extension]).get_settings(), [])[0]
    assert (await apply_extension_setting(row)).kind == "ok"
    fresh, _, _ = registered(read=read, write=write)
    fresh_row = extension_settings_rows(ExtensionRunner(extensions=[fresh]).get_settings(), [])[0]
    assert fresh_row.read(SettingsManager.in_memory({})) == "on"
    assert (await apply_extension_setting(fresh_row)).kind == "ok"
    assert read() is False


async def test_removed_and_reloaded_settings_do_not_keep_stale_callbacks():
    old, runtime, values = registered()
    runner = ExtensionRunner(extensions=[old])
    stale = extension_settings_rows(runner.get_settings(), [])[0]
    runtime.invalidate()
    new, _, _ = registered(name="new-owner")
    runner.extensions = [new]
    assert set(runner.get_settings()) == {"extension:new-owner:enabled"}
    result = await apply_extension_setting(stale)
    assert result.kind == "error" and values["enabled"] is False
    runner.extensions = []
    assert runner.get_settings() == {}


async def test_invalidation_before_await_never_enters_async_owner():
    persisted = []

    async def write(value):
        persisted.append(value)

    extension, runtime, _ = registered(write=write)
    pending = extension.settings["enabled"].set_value(True)
    runtime.invalidate()
    with pytest.raises(ExtensionError):
        await pending
    assert persisted == []


async def test_failed_setter_is_isolated_and_does_not_expose_error_text():
    def fail(value):
        raise RuntimeError("private token and path must not reach settings UI")

    extension, _, _ = registered(write=fail)
    row = extension_settings_rows(ExtensionRunner(extensions=[extension]).get_settings(), [])[0]
    result = await apply_extension_setting(row)
    assert result.kind == "error" and "private" not in result.message
    assert row.read(SettingsManager.in_memory({})) == "off"


async def test_non_persisted_value_is_not_reported_as_success():
    extension, _, _ = registered(write=lambda value: None)
    row = extension_settings_rows(ExtensionRunner(extensions=[extension]).get_settings(), [])[0]
    result = await apply_extension_setting(row)
    assert result.kind == "error" and "did not change" in result.message


async def test_unavailable_displayed_state_cannot_enable_the_owner():
    extension, _, values = registered()
    row = extension_settings_rows(ExtensionRunner(extensions=[extension]).get_settings(), [])[0]
    result = await apply_extension_setting(row, displayed_value="unavailable")
    assert result.kind == "error" and values["enabled"] is False


def test_colliding_labels_are_qualified_and_builtin_rows_stay_unchanged():
    first, _, _ = registered(name="first", label="Theme")
    second, _, _ = registered(name="second", label="Theme")
    sm = SettingsManager.in_memory({})
    builtin = build_settings_rows(sm)
    rows = extension_settings_rows(
        ExtensionRunner(extensions=[first, second]).get_settings(), builtin
    )
    assert len({r.label for r in rows}) == 2
    assert all(r.label != "Theme" for r in rows)
    assert len(build_settings_rows(sm)) == len(builtin)


async def test_generated_label_collisions_still_select_the_correct_owner():
    first, _, first_value = registered(name="a")
    second, _, second_value = registered(name="b")
    third, _, third_value = registered(name="c", label="Memory (a:enabled)")
    fourth, _, _ = registered(name="d", label="Theme")
    sm = SettingsManager.in_memory({})
    builtin = build_settings_rows(sm)
    rows = extension_settings_rows(
        ExtensionRunner(extensions=[first, second, third, fourth]).get_settings(), builtin
    )
    labels = [r.label for r in builtin + rows]
    assert len(labels) == len(set(labels))
    selected = next(r.label for r in rows if r.key == "extension:c:enabled")
    chosen = rows[[r.label for r in rows].index(selected)]
    assert (await apply_extension_setting(chosen)).kind == "ok"
    assert third_value["enabled"] and not first_value["enabled"] and not second_value["enabled"]


def test_invalid_getter_and_control_metadata_are_rejected():
    extension, _, _ = registered(read=lambda: "true")
    row = extension_settings_rows(ExtensionRunner(extensions=[extension]).get_settings(), [])[0]
    assert row.read(SettingsManager.in_memory({})) == "unavailable"
    with pytest.raises(ValueError):
        registered(label="Memory\x1b[2J")
