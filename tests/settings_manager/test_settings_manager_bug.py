"""External-edit preservation regression tests — Sprint 6h₇b · §F.4 · Commit 2.

Ports the 4 Pi regression tests from
``coding-agent/test/core/settings-manager-bug.test.ts`` (SHA 734e08e).

These tests cover the bug fix where external file changes to arrays
were being overwritten by stale in-memory state. The fix tracks which
fields were explicitly modified during the session, and only those
fields override file values during ``save()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aelix_ai.settings import (
    FileSettingsStorage,
    SettingsManager,
)


def _make_manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    storage = FileSettingsStorage(
        settings_dirs["project_dir"], settings_dirs["agent_dir"]
    )
    return SettingsManager.from_storage(storage)


async def test_preserve_file_packages_array_when_changing_unrelated(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager-bug.test.ts:37-72`.

    File externally changes ``packages: []``; UI changes ``theme``.
    Expected: ``packages`` stays ``[]`` (file wins), ``theme`` updates.
    """

    write_settings(
        settings_dirs["global_path"],
        {"theme": "dark", "packages": ["npm:pi-mcp-adapter"]},
    )
    manager = _make_manager(settings_dirs)
    assert manager.get_packages() == ["npm:pi-mcp-adapter"]

    current = read_settings(settings_dirs["global_path"])
    current["packages"] = []
    write_settings(settings_dirs["global_path"], current)
    assert read_settings(settings_dirs["global_path"])["packages"] == []

    manager.set_theme("light")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["packages"] == []
    assert saved["theme"] == "light"


async def test_preserve_file_extensions_array_when_changing_unrelated(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager-bug.test.ts:74-100`."""

    write_settings(
        settings_dirs["global_path"],
        {"theme": "dark", "extensions": ["/old/extension.ts"]},
    )
    manager = _make_manager(settings_dirs)

    current = read_settings(settings_dirs["global_path"])
    current["extensions"] = ["/new/extension.ts"]
    write_settings(settings_dirs["global_path"], current)

    manager.set_default_thinking_level("high")
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["extensions"] == ["/new/extension.ts"]


async def test_preserve_external_project_changes_to_unrelated_field(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager-bug.test.ts:102-124`."""

    write_settings(
        settings_dirs["project_path"],
        {
            "extensions": ["./old-extension.ts"],
            "prompts": ["./old-prompt.md"],
        },
    )
    manager = _make_manager(settings_dirs)

    current = read_settings(settings_dirs["project_path"])
    current["prompts"] = ["./new-prompt.md"]
    write_settings(settings_dirs["project_path"], current)

    manager.set_project_extension_paths(["./updated-extension.ts"])
    await manager.flush()

    saved = read_settings(settings_dirs["project_path"])
    assert saved["prompts"] == ["./new-prompt.md"]
    assert saved["extensions"] == ["./updated-extension.ts"]


async def test_in_memory_project_change_overrides_external_for_same_key(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Pi parity: `settings-manager-bug.test.ts:126-146`."""

    write_settings(
        settings_dirs["project_path"],
        {"extensions": ["./initial-extension.ts"]},
    )
    manager = _make_manager(settings_dirs)

    current = read_settings(settings_dirs["project_path"])
    current["extensions"] = ["./external-extension.ts"]
    write_settings(settings_dirs["project_path"], current)

    manager.set_project_extension_paths(["./in-memory-extension.ts"])
    await manager.flush()

    saved = read_settings(settings_dirs["project_path"])
    assert saved["extensions"] == ["./in-memory-extension.ts"]
