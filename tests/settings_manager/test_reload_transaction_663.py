"""#663 (ADR-0289) — a ``SettingsManager.reload`` that raises leaves the manager as it was.

``reload`` assigned the global file's settings, cleared the modification sets
and only then merged; a merge that raised (#658's deeply nested value, whose
``copy.deepcopy`` hits the recursion limit) left the NEW global settings next to
the OLD merged view, and every later rebuild read that mix. The failure is
forced here with a monkeypatch — the recursion limit is platform-specific, and
this must hold whatever #658 does about it.

``snapshot`` / ``restore`` is what ``AgentSessionRuntime`` uses to put the
settings a session was running with back after a rebuild failed.
"""

from __future__ import annotations

import json
from typing import Any

import aelix_ai.settings.settings_manager as sm_mod
import pytest
from aelix_ai.settings import SettingsManager
from aelix_ai.settings.storage import InMemorySettingsStorage


def _manager(global_doc: dict[str, Any]) -> tuple[SettingsManager, InMemorySettingsStorage]:
    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _cur: json.dumps(global_doc))
    return SettingsManager.from_storage(storage), storage


async def test_a_reload_whose_merge_raises_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, storage = _manager({"theme": "before", "defaultModel": "m-before"})
    manager._mark_modified("theme")
    global_before = manager._global_settings
    merged_before = manager.get_settings()
    storage.with_lock("global", lambda _cur: json.dumps({"theme": "after"}))

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(sm_mod, "deep_merge_settings", _raise)
    with pytest.raises(RecursionError):
        await manager.reload()
    monkeypatch.undo()

    assert manager._global_settings is global_before
    assert manager.get_theme() == "before"
    assert manager.get_settings() == merged_before
    assert manager._modified_fields == {"theme"}
    assert manager.drain_errors() == []
    # And the next reload, once the merge works, applies the file.
    await manager.reload()
    assert manager.get_theme() == "after"


async def test_a_reload_that_works_still_records_a_file_that_did_not_load() -> None:
    manager, storage = _manager({"theme": "before"})
    storage.with_lock("global", lambda _cur: "{not json")
    await manager.reload()
    assert manager.get_theme() == "before"
    assert manager.load_error("global") is not None
    assert [e.scope for e in manager.drain_errors()] == ["global"]


async def test_restore_puts_back_what_snapshot_took() -> None:
    manager, storage = _manager({"theme": "before", "skills": ["./a"]})
    manager._mark_modified("theme")
    snap = manager.snapshot()
    storage.with_lock("global", lambda _cur: json.dumps({"theme": "after", "skills": ["./b"]}))
    await manager.reload()
    assert manager.get_theme() == "after"
    assert manager._modified_fields == set()

    manager.restore(snap)
    assert manager.get_theme() == "before"
    assert manager.get_global_settings().skills == ["./a"]
    assert manager._modified_fields == {"theme"}
    # Nothing was written: the file still holds what the reload read.
    assert json.loads(storage.read("global") or "{}")["theme"] == "after"


async def test_a_snapshot_is_a_copy_a_setter_cannot_reach() -> None:
    """Codex R07 / verify V5: a setter changes the global scope in place.

    The snapshot held that object, so a write after it changed the snapshot too:
    after ``restore`` the global scope said the write and the merged view did
    not, and the next unrelated setter flipped the theme.
    """

    manager, _storage = _manager({"theme": "before"})
    snap = manager.snapshot()
    manager.set_theme("written-between")
    assert snap.global_settings.theme == "before"

    manager.restore(snap)
    assert manager.get_global_settings().theme == manager.get_theme()
    manager.set_quiet_startup(True)
    assert manager.get_theme() == manager.get_global_settings().theme


async def test_restore_keeps_the_writes_made_after_the_snapshot() -> None:
    """Codex R08 — the order: the snapshot, then every write made since, again."""

    manager, storage = _manager(
        {"theme": "before", "defaultModel": "m-before", "retry": {"enabled": True, "maxRetries": 3}}
    )
    snap = manager.snapshot()
    storage.with_lock(
        "global",
        lambda _cur: json.dumps(
            {"theme": "from-file", "defaultModel": "m-file", "retry": {"enabled": True, "maxRetries": 9}}
        ),
    )
    await manager.reload()
    manager.set_theme("user-write")
    manager.set_retry_enabled(False)
    await manager.flush()

    manager.restore(snap)
    # The writes stand, in memory as on disk...
    assert manager.get_theme() == "user-write"
    on_disk = json.loads(storage.read("global") or "{}")
    assert on_disk["theme"] == "user-write" and on_disk["retry"]["enabled"] is False
    retry = manager.get_global_settings().retry
    assert retry is not None and retry.enabled is False
    # ...and what only the re-read brought in is the snapshot's again, a nested
    # sibling of a written key included.
    assert manager.get_default_model() == "m-before"
    assert retry.max_retries == 3


async def test_restore_keeps_a_trust_decision_made_after_the_snapshot() -> None:
    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _cur: json.dumps({"theme": "global"}))
    storage.with_lock("project", lambda _cur: json.dumps({"theme": "project"}))
    manager = SettingsManager.from_storage(storage, project_trusted=False)
    snap = manager.snapshot()
    assert manager.get_theme() == "global"
    manager.set_project_trusted(True)

    manager.restore(snap)
    assert manager.is_project_trusted() is True
    assert manager.get_theme() == "project"
