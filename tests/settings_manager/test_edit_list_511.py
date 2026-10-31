"""#511 (ADR-0279 D4) — ``SettingsManager.edit_list``, the write the resource panels use.

Measured on ``e19ab55e`` before this: ``set_skill_paths`` + ``flush`` wrote the
in-memory snapshot (a hand edit made after load was overwritten) and, for a
scope whose file did not load, wrote nothing while ``drain_errors()`` stayed
empty and ``get_skill_paths()`` returned the new value. ``edit_list`` reads the
list on disk under the lock and raises instead of skipping.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from aelix_ai.settings import SettingsFileError, SettingsManager


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _manager(dirs: dict[str, Path], *, trusted: bool = True) -> SettingsManager:
    return SettingsManager.create(dirs["project_dir"], dirs["agent_dir"], project_trusted=trusted)


async def test_a_hand_edit_made_after_load_is_kept(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    path.write_text(json.dumps({"theme": "dark", "skills": ["!old-*"]}))
    manager = _manager(settings_dirs)
    # Another session (or the user) edits the file after this one loaded it.
    path.write_text(json.dumps({"theme": "light", "skills": ["!old-*", "-deploy"]}))

    result = await manager.edit_list("global", "skills", lambda e: [*e, "-pdf-tools"])

    assert result == ["!old-*", "-deploy", "-pdf-tools"]
    on_disk = json.loads(path.read_text())
    assert on_disk == {"theme": "light", "skills": ["!old-*", "-deploy", "-pdf-tools"]}
    assert path.read_text().startswith('{\n  "theme"')  # indent=2
    # The in-memory scope took exactly what was written.
    assert manager.get_global_settings().skills == ["!old-*", "-deploy", "-pdf-tools"]


async def test_an_unchanged_list_writes_nothing(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    original = '{"skills":["-a"],   "theme":"dark"}'
    path.write_text(original)
    manager = _manager(settings_dirs)
    await manager.edit_list("global", "skills", lambda e: list(e))
    assert path.read_text() == original


async def test_an_empty_result_removes_the_key(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    path.write_text(json.dumps({"theme": "dark", "skills": ["-a"]}))
    manager = _manager(settings_dirs)
    assert await manager.edit_list("global", "skills", lambda _e: []) == []
    assert json.loads(path.read_text()) == {"theme": "dark"}
    assert manager.get_global_settings().skills is None


async def test_the_new_file_path(settings_dirs: dict[str, Path]) -> None:
    manager = _manager(settings_dirs)
    await manager.edit_list("project", "skills", lambda e: [*e, "-x"])
    assert json.loads(settings_dirs["project_path"].read_text()) == {"skills": ["-x"]}
    assert manager.get_project_settings().skills == ["-x"]


async def test_a_file_that_did_not_load_raises_and_is_not_written(
    settings_dirs: dict[str, Path],
) -> None:
    path = settings_dirs["global_path"]
    path.write_text("{ not json")
    manager = _manager(settings_dirs)
    assert manager.load_error("global") is not None
    with pytest.raises(SettingsFileError, match="did not load"):
        await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert path.read_text() == "{ not json"


async def test_a_file_broken_after_load_raises(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    path.write_text("{}")
    manager = _manager(settings_dirs)
    path.write_text("{ broken")
    with pytest.raises(SettingsFileError, match="not valid JSON"):
        await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert path.read_text() == "{ broken"


async def test_a_malformed_value_under_the_key_raises(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    path.write_text(json.dumps({"skills": "pdf"}))
    manager = _manager(settings_dirs)
    with pytest.raises(SettingsFileError, match="not a list of strings"):
        await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert json.loads(path.read_text()) == {"skills": "pdf"}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions")
async def test_a_read_only_file_raises(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    path.write_text("{}")
    manager = _manager(settings_dirs)
    path.chmod(0o444)
    try:
        with pytest.raises(OSError):
            await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    finally:
        path.chmod(0o644)
    assert path.read_text() == "{}"


async def test_an_untrusted_project_refuses(settings_dirs: dict[str, Path]) -> None:
    manager = _manager(settings_dirs, trusted=False)
    with pytest.raises(RuntimeError, match="not trusted"):
        await manager.edit_list("project", "skills", lambda e: [*e, "-x"])
    assert not settings_dirs["project_path"].exists()
    assert manager.read_list("project", "skills") is None


async def test_a_pending_setter_change_to_the_key_does_not_overwrite_the_edit(
    settings_dirs: dict[str, Path],
) -> None:
    path = settings_dirs["global_path"]
    path.write_text("{}")
    manager = _manager(settings_dirs)
    manager.set_skill_paths(["stale"])  # queued, not yet written
    await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    manager.set_theme("dark")  # a later write of ANOTHER key
    await manager.flush()
    assert json.loads(path.read_text()) == {"skills": ["stale", "-x"], "theme": "dark"}


def test_read_list_reads_the_disk_now(settings_dirs: dict[str, Path]) -> None:
    path = settings_dirs["global_path"]
    path.write_text(json.dumps({"skills": ["-a"]}))
    manager = _manager(settings_dirs)
    path.write_text(json.dumps({"skills": ["-b"], "prompts": 3}))
    assert manager.read_list("global", "skills") == ["-b"]
    assert manager.read_list("global", "prompts") == 3
    assert manager.read_list("project", "skills") is None
    path.write_text("[]")
    with pytest.raises(SettingsFileError, match="JSON object"):
        manager.read_list("global", "skills")


def test_scope_path(settings_dirs: dict[str, Path]) -> None:
    manager = _manager(settings_dirs)
    assert manager.scope_path("global") == settings_dirs["global_path"]
    assert manager.scope_path("project") == settings_dirs["project_path"]
    assert SettingsManager.in_memory().scope_path("global") is None


async def test_unrelated_keys_are_written_back_as_read_not_migrated(
    settings_dirs: dict[str, Path],
) -> None:
    """C19: the edit touches its key; a legacy ``queueMode`` stays a ``queueMode``."""

    path = settings_dirs["global_path"]
    original = {"queueMode": "all", "websockets": True, "custom": {"x": [1, 2]}, "skills": ["-a"]}
    path.write_text(json.dumps(original))
    manager = _manager(settings_dirs)
    await manager.edit_list("global", "skills", lambda e: [*e, "-b"])
    assert json.loads(path.read_text()) == {**original, "skills": ["-a", "-b"]}


async def test_a_wellformed_legacy_skills_object_reads_as_its_directories(
    settings_dirs: dict[str, Path],
) -> None:
    path = settings_dirs["global_path"]
    path.write_text(
        json.dumps({"skills": {"customDirectories": ["/opt/s"], "enableSkillCommands": False}})
    )
    manager = _manager(settings_dirs)
    assert manager.read_list("global", "skills") == ["/opt/s"]
    await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert json.loads(path.read_text()) == {
        "skills": ["/opt/s", "-x"],
        "enableSkillCommands": False,
    }


async def test_an_unchanged_edit_leaves_a_legacy_skills_object_byte_identical(
    settings_dirs: dict[str, Path],
) -> None:
    """Round 2 (R2: V3): a save that changes nothing does not migrate the object."""

    path = settings_dirs["global_path"]
    raw = '{"skills": {"customDirectories": ["/opt/s"], "enableSkillCommands": false}}'
    path.write_text(raw)
    before = os.stat(path)
    manager = _manager(settings_dirs)
    assert await manager.edit_list("global", "skills", lambda e: list(e)) == ["/opt/s"]
    assert path.read_text() == raw
    after = os.stat(path)
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


async def test_a_file_with_a_utf8_bom_loads_and_edits_as_in_pi(
    settings_dirs: dict[str, Path],
) -> None:
    """Round 2 (R6): pi parses ``stripBom(content)``; aelix failed with "Unexpected UTF-8 BOM"."""

    path = settings_dirs["global_path"]
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"theme": "dark", "skills": ["-a"]}).encode())
    manager = _manager(settings_dirs)
    assert manager.load_error("global") is None
    assert manager.get_global_settings().theme == "dark"
    assert manager.read_list("global", "skills") == ["-a"]
    await manager.edit_list("global", "skills", lambda e: [*e, "-b"])
    assert json.loads(path.read_text("utf-8")) == {"theme": "dark", "skills": ["-a", "-b"]}


async def test_a_malformed_legacy_skills_object_is_refused_not_dropped(
    settings_dirs: dict[str, Path],
) -> None:
    """The migration would delete it without a word (``migrate_settings`` step 3)."""

    path = settings_dirs["global_path"]
    raw = json.dumps({"skills": {"customDirectories": "not-a-list"}})
    path.write_text(raw)
    manager = _manager(settings_dirs)
    with pytest.raises(SettingsFileError, match="old object form"):
        await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert path.read_text() == raw


def test_the_list_getters_never_split_a_string_into_characters(
    settings_dirs: dict[str, Path],
) -> None:
    """C18: ``list("pdf")`` was ``["p", "d", "f"]`` for every reader of these getters."""

    settings_dirs["global_path"].write_text(
        json.dumps({"skills": "pdf", "prompts": ["ok", 3], "extensions": {"a": 1}})
    )
    manager = _manager(settings_dirs)
    assert manager.get_skill_paths() == []
    assert manager.get_prompt_template_paths() == ["ok"]
    assert manager.get_extension_paths() == []


async def test_a_queued_setter_never_writes_over_a_file_broken_since_load(
    settings_dirs: dict[str, Path],
) -> None:
    """Codex X1: the drained setter used to read the broken file as ``{}`` and overwrite it."""

    path = settings_dirs["global_path"]
    path.write_text(json.dumps({"skills": ["-keep"], "theme": "dark"}))
    manager = _manager(settings_dirs)
    manager.set_theme("light")  # queued, not yet written
    path.write_text("{ broken by hand")
    with pytest.raises(SettingsFileError, match="not valid JSON"):
        await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert path.read_text() == "{ broken by hand"
    errors = manager.drain_errors()
    assert errors and "not valid JSON" in str(errors[0].error)
