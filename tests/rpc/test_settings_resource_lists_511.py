"""#511 (ADR-0279 D4; Codex C18) — RPC ``settings/get|set`` and the resource lists.

``settings/set skill_paths`` called ``set_skill_paths``: the in-memory snapshot,
written later by the queue, and for a global file that did not load, skipped —
after ``drain_errors()`` had emptied the queue of the one error that would have
said so — while the call returned the new value. It now goes through
``SettingsManager.edit_list``; ``settings/get`` reads the same global list, and
a malformed value reads as ``[]`` rather than as characters.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.rpc.control import RpcControlPlane


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _plane(tmp_path: Path, global_text: str | None) -> tuple[RpcControlPlane, Path]:
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir()
    work.mkdir()
    if global_text is not None:
        (agent / "settings.json").write_text(global_text)
    manager = SettingsManager.create(work, agent, project_trusted=True)
    plane = RpcControlPlane(
        SimpleNamespace(harness=SimpleNamespace(phase="idle")), settings_manager=manager
    )
    return plane, agent / "settings.json"


async def test_set_skill_paths_edits_the_list_on_disk(tmp_path: Path) -> None:
    plane, path = _plane(tmp_path, json.dumps({"theme": "dark", "skills": ["-old"]}))
    # Another session's save after this one loaded is not overwritten by a snapshot.
    path.write_text(json.dumps({"theme": "light", "skills": ["-old"]}))
    result = await plane.handle("settings/set", {"key": "skill_paths", "value": ["~/s", "-pdf"]})
    assert result["value"] == ["~/s", "-pdf"]
    assert json.loads(path.read_text()) == {"theme": "light", "skills": ["~/s", "-pdf"]}


async def test_a_global_file_that_did_not_load_refuses_every_write(tmp_path: Path) -> None:
    plane, path = _plane(tmp_path, "{ broken")
    with pytest.raises(ValueError, match="not saved"):
        await plane.handle("settings/set", {"key": "skill_paths", "value": ["-pdf"]})
    with pytest.raises(ValueError, match="did not load"):
        await plane.handle("settings/set", {"key": "retry_enabled", "value": False})
    assert path.read_text() == "{ broken"


async def test_get_reads_the_global_list_and_never_splits_a_string(tmp_path: Path) -> None:
    plane, path = _plane(tmp_path, json.dumps({"skills": "pdf", "prompts": ["-a", 3]}))
    settings = (await plane.handle("settings/get", {}))["settings"]
    assert settings["skill_paths"] == []
    assert settings["prompt_template_paths"] == ["-a"]
