"""WP-2 (ADR-0160) — StatuslineStore round-trip + degrade tests."""

from __future__ import annotations

import json
from pathlib import Path

from aelix_coding_agent.tui.statusline_store import StatuslineConfig, StatuslineStore

_DEFAULTS = ["permission-mode", "model", "git-branch"]


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    store = StatuslineStore(tmp_path / "statusline.json", default_enabled=_DEFAULTS)
    cfg = store.load()
    assert cfg.enabled == _DEFAULTS
    assert cfg.use_theme_colors is True


def test_corrupt_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    assert store.load().enabled == _DEFAULTS


def test_non_dict_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    assert store.load().enabled == _DEFAULTS


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    store.save(StatuslineConfig(enabled=["model", "cost"], use_theme_colors=False))
    assert path.is_file()
    cfg = store.load()
    assert cfg.enabled == ["model", "cost"]
    assert cfg.use_theme_colors is False


def test_save_is_atomic_no_tmp_left(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    store.save(StatuslineConfig(enabled=["model"]))
    # No leftover .tmp.* files (atomic temp+replace cleaned up).
    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_save_creates_agent_dir(tmp_path: Path) -> None:
    # Nested non-existent dir is created on demand.
    path = tmp_path / "nested" / "deeper" / "statusline.json"
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    store.save(StatuslineConfig(enabled=["model"]))
    assert path.is_file()


def test_saved_payload_has_version_and_sorted_keys(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    store.save(StatuslineConfig(enabled=["model"], use_theme_colors=True))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["enabled"] == ["model"]
    assert raw["use_theme_colors"] is True
    assert list(raw.keys()) == sorted(raw.keys())


def test_bad_enabled_type_falls_back_per_field(tmp_path: Path) -> None:
    path = tmp_path / "statusline.json"
    path.write_text(
        json.dumps({"enabled": "not-a-list", "use_theme_colors": False}),
        encoding="utf-8",
    )
    store = StatuslineStore(path, default_enabled=_DEFAULTS)
    cfg = store.load()
    assert cfg.enabled == _DEFAULTS  # bad type → defaults
    assert cfg.use_theme_colors is False  # valid key preserved


def test_default_path_is_agent_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path))
    store = StatuslineStore(default_enabled=_DEFAULTS)
    assert store.path == tmp_path / "statusline.json"
