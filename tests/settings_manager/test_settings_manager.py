"""SettingsManager core test suite — Sprint 6h₇b · §F.3 · Commit 2.

Ports the 18 Pi main test cases from
``coding-agent/test/core/settings-manager.test.ts`` (SHA 734e08e)
verbatim, snake_case-converted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import (
    FileSettingsStorage,
    InMemorySettingsStorage,
    PackageSourceObject,
    Settings,
    SettingsManager,
)


def _make_manager(
    settings_dirs: dict[str, Path],
) -> SettingsManager:
    storage = FileSettingsStorage(
        settings_dirs["project_dir"], settings_dirs["agent_dir"]
    )
    return SettingsManager.from_storage(storage)


# === Pi `preserves externally added settings` describe block (3 tests) ===


async def test_preserve_enabled_models_when_changing_thinking_level(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:28-57`."""

    write_settings(
        settings_dirs["global_path"],
        {"theme": "dark", "defaultModel": "claude-sonnet"},
    )
    manager = _make_manager(settings_dirs)

    # User externally edits settings.json
    current = read_settings(settings_dirs["global_path"])
    current["enabledModels"] = ["claude-opus-4-5", "gpt-5.2-codex"]
    write_settings(settings_dirs["global_path"], current)

    manager.set_default_thinking_level("high")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["enabledModels"] == ["claude-opus-4-5", "gpt-5.2-codex"]
    assert saved["defaultThinkingLevel"] == "high"
    assert saved["theme"] == "dark"
    assert saved["defaultModel"] == "claude-sonnet"


async def test_preserve_custom_settings_when_changing_theme(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:59-85`."""

    write_settings(
        settings_dirs["global_path"], {"defaultModel": "claude-sonnet"}
    )
    manager = _make_manager(settings_dirs)

    current = read_settings(settings_dirs["global_path"])
    current["shellPath"] = "/bin/zsh"
    current["extensions"] = ["/path/to/extension.ts"]
    write_settings(settings_dirs["global_path"], current)

    manager.set_theme("light")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["shellPath"] == "/bin/zsh"
    assert saved["extensions"] == ["/path/to/extension.ts"]
    assert saved["theme"] == "light"


async def test_in_memory_change_overrides_file_for_same_key(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:87-110`."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)

    current = read_settings(settings_dirs["global_path"])
    current["defaultThinkingLevel"] = "low"
    write_settings(settings_dirs["global_path"], current)

    manager.set_default_thinking_level("high")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["defaultThinkingLevel"] == "high"


# === Pi `packages migration` describe block (2 tests) ===


def test_local_extensions_kept_in_extensions_array(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:114-127`."""

    write_settings(
        settings_dirs["global_path"],
        {"extensions": ["/local/ext.ts", "./relative/ext.ts"]},
    )
    manager = _make_manager(settings_dirs)

    assert manager.get_packages() == []
    assert manager.get_extension_paths() == [
        "/local/ext.ts",
        "./relative/ext.ts",
    ]


def test_packages_with_filtering_objects(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:129-155`."""

    write_settings(
        settings_dirs["global_path"],
        {
            "packages": [
                "npm:simple-pkg",
                {
                    "source": "npm:shitty-extensions",
                    "extensions": ["extensions/oracle.ts"],
                    "skills": [],
                },
            ]
        },
    )
    manager = _make_manager(settings_dirs)

    packages = manager.get_packages()
    assert len(packages) == 2
    assert packages[0] == "npm:simple-pkg"
    assert isinstance(packages[1], PackageSourceObject)
    assert packages[1].source == "npm:shitty-extensions"
    assert packages[1].extensions == ["extensions/oracle.ts"]
    assert packages[1].skills == []


# === Pi `reload` describe block (2 tests) ===


async def test_reload_picks_up_disk_changes(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:159-185`."""

    write_settings(
        settings_dirs["global_path"],
        {"theme": "dark", "extensions": ["/before.ts"]},
    )
    manager = _make_manager(settings_dirs)

    write_settings(
        settings_dirs["global_path"],
        {
            "theme": "light",
            "extensions": ["/after.ts"],
            "defaultModel": "claude-sonnet",
        },
    )

    await manager.reload()

    assert manager.get_theme() == "light"
    assert manager.get_extension_paths() == ["/after.ts"]
    assert manager.get_default_model() == "claude-sonnet"


async def test_reload_keeps_previous_when_file_invalid(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:187-198`."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)

    settings_dirs["global_path"].write_text("{ invalid json", encoding="utf-8")
    await manager.reload()

    assert manager.get_theme() == "dark"


# === Pi `error tracking` describe block (1 test) ===


def test_drain_errors_collects_and_clears_load_errors(
    settings_dirs: dict[str, Path],
) -> None:
    """Pi parity: `settings-manager.test.ts:201-213`."""

    global_path = settings_dirs["global_path"]
    project_path = settings_dirs["project_path"]
    global_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text("{ invalid global json", encoding="utf-8")
    project_path.write_text("{ invalid project json", encoding="utf-8")

    manager = _make_manager(settings_dirs)
    errors = manager.drain_errors()

    assert len(errors) == 2
    assert sorted(e.scope for e in errors) == ["global", "project"]
    assert manager.drain_errors() == []


# === Pi `project settings directory creation` describe block (2 tests) ===


def test_pi_folder_not_created_on_read_only(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:217-233`."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    # Project .aelix dir was not created by the fixture.
    assert not (settings_dirs["project_dir"] / ".aelix").exists()

    manager = _make_manager(settings_dirs)

    assert not (settings_dirs["project_dir"] / ".aelix").exists()
    assert manager.get_theme() == "dark"


async def test_pi_folder_created_on_project_write(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:235-257`."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    assert not (settings_dirs["project_dir"] / ".aelix").exists()

    manager = _make_manager(settings_dirs)
    assert not (settings_dirs["project_dir"] / ".aelix").exists()

    manager.set_project_packages([PackageSourceObject(source="npm:test-pkg")])
    await manager.flush()

    assert (settings_dirs["project_dir"] / ".aelix").exists()
    assert settings_dirs["project_path"].exists()


# === Pi `shellCommandPrefix` describe block (3 tests) ===


def test_load_shell_command_prefix(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:261-268`."""

    write_settings(
        settings_dirs["global_path"],
        {"shellCommandPrefix": "shopt -s expand_aliases"},
    )
    manager = _make_manager(settings_dirs)
    assert manager.get_shell_command_prefix() == "shopt -s expand_aliases"


def test_shell_command_prefix_unset_returns_none(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:270-277`."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)
    assert manager.get_shell_command_prefix() is None


async def test_shell_command_prefix_preserved_on_unrelated_save(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:279-290`."""

    write_settings(
        settings_dirs["global_path"],
        {"shellCommandPrefix": "shopt -s expand_aliases"},
    )
    manager = _make_manager(settings_dirs)
    manager.set_theme("light")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["shellCommandPrefix"] == "shopt -s expand_aliases"
    assert saved["theme"] == "light"


# === Pi `getSessionDir` describe block (4 tests) ===


def test_session_dir_unset_returns_none(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:294-298`."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)
    assert manager.get_session_dir() is None


def test_session_dir_global(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:300-304`."""

    write_settings(
        settings_dirs["global_path"], {"sessionDir": "/tmp/sessions"}
    )
    manager = _make_manager(settings_dirs)
    assert manager.get_session_dir() == "/tmp/sessions"


def test_session_dir_project_overrides_global(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:306-311`."""

    write_settings(
        settings_dirs["global_path"], {"sessionDir": "/global/sessions"}
    )
    write_settings(
        settings_dirs["project_path"], {"sessionDir": "./sessions"}
    )
    manager = _make_manager(settings_dirs)
    assert manager.get_session_dir() == "./sessions"


def test_session_dir_expands_tilde(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """Pi parity: `settings-manager.test.ts:313-317`."""

    write_settings(settings_dirs["global_path"], {"sessionDir": "~/sessions"})
    manager = _make_manager(settings_dirs)
    expected = str(Path.home() / "sessions")
    assert manager.get_session_dir() == expected


# === Additional core tests not covered above ===


async def test_in_memory_factory_no_io(tmp_path: Path) -> None:
    """In-memory factory does not touch disk."""

    manager = SettingsManager.in_memory()
    assert manager.get_default_thinking_level() is None
    # The factory's storage is :class:`InMemorySettingsStorage` — no disk IO.
    manager.set_theme("dark")
    await manager.flush()
    # No files should exist anywhere under tmp_path
    assert not any(tmp_path.glob("**/*.json"))


def test_in_memory_factory_seeds_initial_settings() -> None:
    """In-memory factory accepts initial settings as dict (Pi parity)."""

    manager = SettingsManager.in_memory({"theme": "dark"})
    assert manager.get_theme() == "dark"


def test_in_memory_factory_with_dataclass_settings() -> None:
    """In-memory factory accepts a :class:`Settings` dataclass."""

    s = Settings(theme="light", default_model="claude-sonnet")
    manager = SettingsManager.in_memory(s)
    assert manager.get_theme() == "light"
    assert manager.get_default_model() == "claude-sonnet"


def test_get_settings_returns_deep_copy() -> None:
    """``get_settings`` returns a deep copy — caller mutations don't leak."""

    manager = SettingsManager.in_memory({"theme": "dark"})
    s = manager.get_settings()
    s.theme = "light"  # caller mutation
    # Manager's view remains unchanged.
    assert manager.get_theme() == "dark"


def test_get_global_settings_returns_deep_copy() -> None:
    """``get_global_settings`` returns a deep copy."""

    manager = SettingsManager.in_memory({"theme": "dark"})
    s = manager.get_global_settings()
    s.theme = "light"
    assert manager.get_theme() == "dark"


async def test_save_persists_global(
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """Global setters persist via the write queue."""

    manager = _make_manager(settings_dirs)
    manager.set_theme("dark")
    manager.set_default_model("claude-sonnet")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["theme"] == "dark"
    assert saved["defaultModel"] == "claude-sonnet"


async def test_save_persists_project(
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """Project setters persist to the project-scope settings file."""

    manager = _make_manager(settings_dirs)
    manager.set_project_extension_paths(["/path/ext.ts"])
    await manager.flush()

    saved = read_settings(settings_dirs["project_path"])
    assert saved["extensions"] == ["/path/ext.ts"]


def test_deep_merge_project_wins_on_conflict() -> None:
    """deep_merge_settings — project value wins."""

    from aelix_ai.settings import deep_merge_settings

    g = Settings(theme="dark", default_model="claude-sonnet")
    p = Settings(theme="light")
    merged = deep_merge_settings(g, p)
    assert merged.theme == "light"
    assert merged.default_model == "claude-sonnet"


def test_deep_merge_nested_per_field() -> None:
    """deep_merge_settings — nested fields merge per-key."""

    from aelix_ai.settings import CompactionSettings, deep_merge_settings

    g = Settings(
        compaction=CompactionSettings(
            enabled=True, reserve_tokens=16384, keep_recent_tokens=20000
        )
    )
    p = Settings(compaction=CompactionSettings(reserve_tokens=8192))
    merged = deep_merge_settings(g, p)
    assert merged.compaction is not None
    assert merged.compaction.enabled is True
    assert merged.compaction.reserve_tokens == 8192
    assert merged.compaction.keep_recent_tokens == 20000


def test_apply_overrides_after_construction() -> None:
    """apply_overrides extends the merged view post-construction."""

    manager = SettingsManager.in_memory({"theme": "dark"})
    manager.apply_overrides(Settings(theme="light"))
    assert manager.get_theme() == "light"


async def test_reload_clears_modification_tracking(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """reload() clears modification tracking before remerge (P-429)."""

    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)
    manager.set_theme("light")  # marks "theme" as modified
    await manager.reload()
    # After reload, _modified_fields should be empty.
    assert manager._modified_fields == set()
    assert manager._modified_nested_fields == {}


def test_pi_factories_construct_via_storage() -> None:
    """from_storage accepts an arbitrary :class:`SettingsStorage`."""

    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _: json.dumps({"theme": "dark"}))
    manager = SettingsManager.from_storage(storage)
    assert manager.get_theme() == "dark"


def test_invalid_json_at_construction_records_error(
    settings_dirs: dict[str, Path],
) -> None:
    """Invalid JSON at construction surfaces via drain_errors."""

    settings_dirs["global_path"].parent.mkdir(parents=True, exist_ok=True)
    settings_dirs["global_path"].write_text(
        "{ invalid json", encoding="utf-8"
    )
    manager = _make_manager(settings_dirs)
    errors = manager.drain_errors()
    assert len(errors) >= 1
    assert any(e.scope == "global" for e in errors)


@pytest.mark.asyncio
async def test_flush_returns_when_no_pending() -> None:
    """flush() is a no-op when no writes are pending."""

    manager = SettingsManager.in_memory()
    await manager.flush()  # should not raise
    await manager.flush()  # idempotent


# === W5 MAJOR-3 fold-in regression =========================================


def test_aelix_settings_path_override_honored_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AELIX_SETTINGS_PATH overrides the FULL path (filename included).

    Sprint 6h₇b W5 MAJOR-3 fold-in regression. Previously
    `default_settings_path().parent` discarded the override's filename
    and `FileSettingsStorage` always derived `agent_dir / "settings.json"`,
    so an override like `/tmp/custom.json` silently became
    `/tmp/settings.json`. The fix routes the full override through
    `FileSettingsStorage(global_path=...)`.
    """

    override = tmp_path / "custom-settings.json"
    override.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(override))

    manager = SettingsManager.create(cwd=tmp_path)

    assert manager.get_theme() == "dark"
    storage = manager._storage  # white-box: verifies the wiring
    assert isinstance(storage, FileSettingsStorage)
    assert storage.global_path == override
