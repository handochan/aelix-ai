"""Shared fixtures for SettingsManager tests — Sprint 6h₇b §F.1."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def settings_dirs(tmp_path: Path) -> dict[str, Path]:
    """Pi parity: agent + project (cwd) directory layout.

    Returns a dict with::

        {
          "agent_dir": <tmp>/agent,
          "project_dir": <tmp>/project,
          "global_path": <tmp>/agent/settings.json,
          "project_path": <tmp>/project/.aelix/settings.json,
        }

    Directories are created up-front for the agent dir + project dir;
    the project-scope ``.aelix`` subdir is intentionally NOT created so
    write-side tests can assert lazy creation.
    """

    agent_dir = tmp_path / "agent"
    project_dir = tmp_path / "project"
    agent_dir.mkdir()
    project_dir.mkdir()
    return {
        "agent_dir": agent_dir,
        "project_dir": project_dir,
        "global_path": agent_dir / "settings.json",
        "project_path": project_dir / ".aelix" / "settings.json",
    }


@pytest.fixture
def write_settings() -> Any:
    """Helper to write a Pi-shape JSON dict to a settings.json path."""

    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return _write


@pytest.fixture
def read_settings() -> Any:
    """Helper to read a settings.json path back as a Pi-shape JSON dict."""

    def _read(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    return _read


@pytest.fixture(autouse=True)
def _clear_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Ensure ``PI_CLEAR_ON_SHRINK`` / ``PI_HARDWARE_CURSOR`` are unset
    by default so tests start from the Pi-default fallback path."""

    monkeypatch.delenv("PI_CLEAR_ON_SHRINK", raising=False)
    monkeypatch.delenv("PI_HARDWARE_CURSOR", raising=False)
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)
    yield
