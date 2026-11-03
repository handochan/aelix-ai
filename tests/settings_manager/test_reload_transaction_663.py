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
