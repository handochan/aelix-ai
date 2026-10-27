"""``checkModelCatalog`` — the automatic model catalog check switch (#609, ADR-0264 §10).

Default on (pi's triggers, the owner's choice of 2026-10-19). Global scope only,
as ``respectGitignore``: a project ``.aelix/settings.json`` must not decide what
the global model store fetches, and the ``/settings`` row must show what it
changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aelix_ai.settings import FileSettingsStorage, SettingsManager


def _make_manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    storage = FileSettingsStorage(settings_dirs["project_dir"], settings_dirs["agent_dir"])
    return SettingsManager.from_storage(storage)


def test_default_is_on() -> None:
    assert SettingsManager.in_memory({}).get_check_model_catalog() is True


def test_an_explicit_false_reads_false(settings_dirs: dict[str, Path], write_settings: Any) -> None:
    write_settings(settings_dirs["global_path"], {"checkModelCatalog": False})
    assert _make_manager(settings_dirs).get_check_model_catalog() is False


def test_a_project_file_neither_turns_it_off_nor_on(
    settings_dirs: dict[str, Path], write_settings: Any
) -> None:
    write_settings(settings_dirs["project_path"], {"checkModelCatalog": False})
    assert _make_manager(settings_dirs).get_check_model_catalog() is True

    write_settings(settings_dirs["global_path"], {"checkModelCatalog": False})
    write_settings(settings_dirs["project_path"], {"checkModelCatalog": True})
    assert _make_manager(settings_dirs).get_check_model_catalog() is False


async def test_a_set_is_global_and_visible_at_once(settings_dirs: dict[str, Path]) -> None:
    import json

    manager = _make_manager(settings_dirs)
    manager.set_check_model_catalog(False)
    await manager.flush()

    assert manager.get_check_model_catalog() is False
    assert json.loads(Path(settings_dirs["global_path"]).read_text())["checkModelCatalog"] is False
