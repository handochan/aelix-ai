"""``respectGitignore`` — the ``@``-menu ignore switch (#238, ADR-0193 amendment).

Aelix-original; pi has no equivalent setting. Owner decision B (2026-09-06): a
``/settings`` toggle, **default ON**, that asks ``fd`` for ``--no-ignore`` when
it is off. Four properties, each of which fails silently if the wiring is
incomplete, shaped on ``test_features_agents_flag.py``:

1. The default is ``True`` when the key is absent — the narrow menu #231 gave
   everyone who has ever run ``find`` stays the default.
2. It round-trips to the GLOBAL ``settings.json`` as ``{"respectGitignore":
   false}`` and reloads.
3. It is read from the GLOBAL cell only, never the merged view. NOT the
   self-elevation argument of ``features_agents`` — nothing is granted here, the
   menu inserts a path string — but HONESTY of the ``/settings`` row: under
   write-global/read-merged a project ``.aelix/settings.json`` carrying the key
   makes the toggle a visible no-op. Measured on ``check_for_updates``, which
   has exactly that shape: with a project override of ``False``,
   ``set_check_for_updates(True)`` leaves the getter ``False``, so ``/settings``
   prints "→ on" and redraws "off" — the #84 class.
4. The getter is a plain attribute load. It is read on the completer WORKER
   thread once per keystroke, so a body that goes through
   ``get_global_settings()`` / ``get_settings()`` would ``deepcopy`` the whole
   44-field settings tree per keypress (measured 8.67 µs against 0.018 µs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import FileSettingsStorage, SettingsManager


def _make_manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    storage = FileSettingsStorage(settings_dirs["project_dir"], settings_dirs["agent_dir"])
    return SettingsManager.from_storage(storage)


@pytest.fixture
def manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    return _make_manager(settings_dirs)


# === 1. the default ===


def test_default_is_true_when_absent(manager: SettingsManager) -> None:
    assert manager.get_respect_gitignore() is True


def test_an_explicit_false_reads_false(settings_dirs: dict[str, Path], write_settings: Any) -> None:
    write_settings(settings_dirs["global_path"], {"respectGitignore": False})
    assert _make_manager(settings_dirs).get_respect_gitignore() is False


# === 2. the round trip ===


async def test_roundtrip_through_disk(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_respect_gitignore(False)
    assert manager.get_respect_gitignore() is False
    # ``_save`` only ENQUEUES — without the flush the file is still empty and
    # this case would pass vacuously on the in-memory value alone.
    await manager.flush()

    on_disk = read_settings(settings_dirs["global_path"])
    assert on_disk["respectGitignore"] is False
    # GLOBAL scope only — the setter must never write the project file.
    assert read_settings(settings_dirs["project_path"]) == {}

    assert _make_manager(settings_dirs).get_respect_gitignore() is False


async def test_set_preserves_sibling_global_keys(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)
    manager.set_respect_gitignore(False)
    await manager.flush()

    on_disk = read_settings(settings_dirs["global_path"])
    assert on_disk["theme"] == "dark"
    assert on_disk["respectGitignore"] is False


# === 3. global scope only — the row must not be able to lie ===


def test_a_project_override_does_not_move_the_getter(
    settings_dirs: dict[str, Path], write_settings: Any
) -> None:
    write_settings(settings_dirs["project_path"], {"respectGitignore": False})
    manager = _make_manager(settings_dirs)

    assert manager.get_respect_gitignore() is True
    # ...and the project value WAS loaded, so this is the getter's scope choice
    # rather than the file being ignored — the merged view really does say False.
    assert manager.get_project_settings().respect_gitignore is False
    assert manager.get_settings().respect_gitignore is False


def test_a_set_after_a_project_override_is_visible_immediately(
    settings_dirs: dict[str, Path], write_settings: Any
) -> None:
    """The failure mode this scope choice exists to prevent (#84 class).

    Under write-global/read-merged the toggle would persist and then read back
    unchanged, so ``/settings`` prints "→ off" and redraws "on" on the very next
    frame. Measured on ``check_for_updates``, which is wired that way today.
    """

    write_settings(settings_dirs["project_path"], {"respectGitignore": True})
    manager = _make_manager(settings_dirs)
    manager.set_respect_gitignore(False)
    assert manager.get_respect_gitignore() is False


# === 4. the getter's cost — one attribute load, per keystroke ===


def test_the_getter_does_not_deep_copy_the_settings_tree(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⑩b — the read lands on the completer worker thread once per keystroke.

    ``get_global_settings()`` and ``get_settings()`` both ``deepcopy`` the whole
    settings tree (8.67 µs against 0.018 µs for the attribute load). A getter
    body that goes through either is correct and silently ~480× the cost, at
    keystroke frequency, inside the same call that fuzzy-scores ~12 600 paths.
    Nothing else in the suite can see that, so it is nailed shut here.
    """

    write_settings(settings_dirs["global_path"], {"respectGitignore": False})
    manager = _make_manager(settings_dirs)
    calls: list[str] = []

    def _forbidden_global(_self: SettingsManager) -> None:
        calls.append("get_global_settings")
        raise AssertionError("the getter deep-copied the settings tree")

    def _forbidden_merged(_self: SettingsManager) -> None:
        calls.append("get_settings")
        raise AssertionError("the getter deep-copied the settings tree")

    monkeypatch.setattr(SettingsManager, "get_global_settings", _forbidden_global)
    monkeypatch.setattr(SettingsManager, "get_settings", _forbidden_merged)

    assert manager.get_respect_gitignore() is False
    assert calls == []
