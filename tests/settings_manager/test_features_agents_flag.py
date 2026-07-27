"""``[features] agents`` — the agent-delegation kill switch (ADR-0197, P2).

Aelix-original. Five properties, each of which fails SILENTLY if the wiring is
incomplete, which is why each one is pinned here rather than left to review:

1. The default is ``False`` (owner-confirmed; spec §8 keeps delegation off
   through Phase 3 and flips it at Phase 4).
2. It round-trips to the GLOBAL ``settings.json`` as ``{"features": {"agents":
   true}}`` — the on-disk shape other tools read.
3. A PROJECT ``.aelix/settings.json`` cannot turn it on. This is the security
   test: project settings are loaded ungated, so a merged read would let any
   cloned repo grant itself the right to spawn a second agent.
4. The key survives the JSON boundary. ``_json_dict_to_settings``
   (``settings_manager.py:107``) drops an unregistered key with no error, so
   omitting any one of the five ``types.py`` tables produces a flag that reads
   ``False`` forever with a perfectly valid-looking settings file on disk.
5. The ``/settings`` row is LIVE — present in ``build_settings_rows`` AND in
   both ``_BOOL_GETTERS`` and ``_BOOL_SETTERS``. Issue #84 shipped 11 rows that
   existed but did nothing; a key registered in the settings tree but absent
   from the picker is the same class of defect one layer up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import (
    FeaturesSettings,
    FileSettingsStorage,
    Settings,
    SettingsManager,
)


def _make_manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    storage = FileSettingsStorage(
        settings_dirs["project_dir"], settings_dirs["agent_dir"]
    )
    return SettingsManager.from_storage(storage)


@pytest.fixture
def manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    return _make_manager(settings_dirs)


# === 1. the default ===


def test_default_is_false(manager: SettingsManager) -> None:
    """OC-9: delegation is OFF out of the box in P2."""

    assert manager.get_features_agents() is False


def test_default_is_false_when_features_block_exists_but_key_does_not(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    # A half-written ``features`` object must not read as enabled — the getter
    # checks the field, not just the container.
    write_settings(settings_dirs["global_path"], {"features": {}})
    manager = _make_manager(settings_dirs)
    assert manager.get_features_agents() is False


# === 2. the round trip ===


async def test_roundtrip_through_disk(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_features_agents(True)
    assert manager.get_features_agents() is True
    # ``_save`` only ENQUEUES (settings_manager.py:689) — without the flush the
    # file is still empty and this test would pass vacuously on the in-memory
    # value alone.
    await manager.flush()

    on_disk = read_settings(settings_dirs["global_path"])
    assert on_disk["features"] == {"agents": True}
    # GLOBAL scope only — the setter must never write the project file.
    assert read_settings(settings_dirs["project_path"]) == {}

    # A fresh manager over the same storage reads it back: proves the value
    # survives the write -> parse -> hydrate path, not just the live object.
    reloaded = _make_manager(settings_dirs)
    assert reloaded.get_features_agents() is True
    assert isinstance(reloaded.get_settings().features, FeaturesSettings)


async def test_disable_roundtrips_too(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    # Turning it back OFF must persist ``false``, not delete the key: a missing
    # key and an explicit ``false`` read the same today, but only the explicit
    # form survives a future default flip at Phase 4 (spec §8).
    manager.set_features_agents(True)
    await manager.flush()
    manager.set_features_agents(False)
    await manager.flush()

    assert read_settings(settings_dirs["global_path"])["features"] == {
        "agents": False
    }
    assert _make_manager(settings_dirs).get_features_agents() is False


async def test_set_preserves_sibling_global_keys(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    # The nested per-key merge (``_persist_scoped_settings``) must not clobber
    # unrelated settings written by another surface.
    write_settings(settings_dirs["global_path"], {"theme": "dark"})
    manager = _make_manager(settings_dirs)
    manager.set_features_agents(True)
    await manager.flush()

    on_disk = read_settings(settings_dirs["global_path"])
    assert on_disk["theme"] == "dark"
    assert on_disk["features"] == {"agents": True}


# === 3. the security test ===


def test_project_settings_cannot_enable(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """SECURITY (ADR-0197 §2b): a repo cannot grant itself delegation.

    ``.aelix/settings.json`` is loaded WITHOUT any trust gate, so a getter that
    read the merged view would let a cloned repository switch on the one
    capability that spawns a whole second agent with real authority. The getter
    reads ``_global_settings`` directly, exactly like
    ``get_default_project_trust`` (issue #5).
    """

    write_settings(
        settings_dirs["project_path"], {"features": {"agents": True}}
    )
    manager = _make_manager(settings_dirs)  # global empty, project = enabled

    assert manager.get_features_agents() is False
    # ...and the project value WAS loaded — proving the getter deliberately
    # bypasses the merged read rather than the file simply being ignored.
    project_features = manager.get_project_settings().features
    assert project_features is not None
    assert project_features.agents is True
    # The merged view really does say True; only the getter's scope choice
    # stands between a repo and delegation.
    merged_features = manager.get_settings().features
    assert merged_features is not None
    assert merged_features.agents is True


def test_project_settings_cannot_disable_a_global_enable(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    # The mirror direction, for completeness: global-only means global-only.
    write_settings(settings_dirs["global_path"], {"features": {"agents": True}})
    write_settings(
        settings_dirs["project_path"], {"features": {"agents": False}}
    )
    manager = _make_manager(settings_dirs)
    assert manager.get_features_agents() is True


# === 4. the 5-table registration guard ===


def test_unknown_key_is_not_dropped(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    """``features`` must be registered in every ``types.py`` boundary table.

    ``_json_dict_to_settings`` (``settings_manager.py:107``) skips any JSON key
    missing from ``SETTINGS_JSON_TO_PY``, and ``_json_dict_to_nested`` skips any
    nested key missing from ``NESTED_JSON_TO_PY`` — both silently. Without
    ``SETTINGS_NESTED_CLASSES`` the value would hydrate as a raw ``dict`` and
    the getter's attribute read would raise. This asserts the hydrated TYPE, not
    just the truthiness, so a partial registration cannot pass.
    """

    write_settings(settings_dirs["global_path"], {"features": {"agents": True}})
    manager = _make_manager(settings_dirs)

    features = manager.get_global_settings().features
    assert isinstance(features, FeaturesSettings), (
        f"features hydrated as {type(features)!r} — check SETTINGS_NESTED_CLASSES"
    )
    assert features.agents is True
    assert manager.get_features_agents() is True


def test_features_survives_a_full_json_round_trip() -> None:
    # The pure boundary, with no storage in the way: py -> json -> py.
    from aelix_ai.settings.settings_manager import (
        _json_dict_to_settings,
        _settings_to_json_dict,
    )

    raw = _settings_to_json_dict(Settings(features=FeaturesSettings(agents=True)))
    assert raw == {"features": {"agents": True}}
    # And the JSON text an editor would produce parses back to the dataclass.
    back = _json_dict_to_settings(json.loads(json.dumps(raw)))
    assert back.features == FeaturesSettings(agents=True)


# === 5. the /settings row is LIVE (issue #84 guard) ===


def test_settings_row_wired(manager: SettingsManager) -> None:
    """The row must exist AND be dispatchable, or it is an inert #84 row."""

    from aelix_coding_agent.tui.settings_rows import (
        _BOOL_GETTERS,
        _BOOL_SETTERS,
        build_settings_rows,
    )

    rows = build_settings_rows(manager)
    matches = [r for r in rows if r.key == "features_agents"]
    assert len(matches) == 1, "no /settings row for features_agents"
    row = matches[0]
    assert row.kind == "bool"
    assert row.label
    assert row.help
    # Both halves of the dispatch: a missing getter raises KeyError on render,
    # a missing setter raises it one keystroke later — and ``apply_setting``
    # swallows both into a red line, so neither surfaces as a test failure
    # anywhere else.
    assert _BOOL_GETTERS["features_agents"] == "get_features_agents"
    assert _BOOL_SETTERS["features_agents"] == "set_features_agents"
    assert hasattr(manager, "get_features_agents")
    assert hasattr(manager, "set_features_agents")
    # The row renders the real value through the real getter.
    assert row.read(manager) == "off"


async def test_settings_row_toggle_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """Driving the row the way ``shell.py::_open_settings`` drives it."""

    from aelix_coding_agent.tui.settings_rows import (
        apply_setting,
        build_settings_rows,
    )

    row = next(
        r for r in build_settings_rows(manager) if r.key == "features_agents"
    )
    result = apply_setting(row, manager)

    assert result.kind == "ok", result.message
    # ``live is None`` is the point, not an oversight: the flag is consumed once
    # when the harness is built (``cli/entry.py::_build_harness_options``), so
    # the shell must NOT be told to mirror it onto the live session.
    assert result.live is None
    assert manager.get_features_agents() is True

    await manager.flush()
    assert read_settings(settings_dirs["global_path"])["features"] == {
        "agents": True
    }

    # And the row now reads back the new value (the re-render path).
    row = next(
        r for r in build_settings_rows(manager) if r.key == "features_agents"
    )
    assert row.read(manager) == "on"
