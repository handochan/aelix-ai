"""§E.8 — Pi-parity cross-runtime session parse test (Sprint 4a / ADR-0022).

Reads a vendored Pi v3 ``.jsonl`` fixture and asserts that Aelix
``JsonlSessionStorage.open`` decodes it without loss. This is the durable
guard against Aelix drifting from Pi's on-disk wire format.

The fixture lives at ``tests/pi_parity/fixtures/pi_session_v3_734e08e.jsonl``
and is SHA-anchored to Pi pin ``734e08e`` (ADR-0034).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    SessionTreeEntry,
)

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "pi_session_v3_734e08e.jsonl"
)


async def test_pi_v3_fixture_loads_without_loss(tmp_path: Path) -> None:
    """Open the vendored Pi v3 JSONL and assert all 6 entries decode."""

    target = tmp_path / "pi.jsonl"
    shutil.copy(_FIXTURE, target)
    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(target))
    entries = await storage.get_entries()
    assert len(entries) == 6
    types = [e.type for e in entries]
    assert types == [
        "message",
        "thinking_level_change",
        "model_change",
        "message",
        "label",
        "session_info",
    ]
    # Leaf-after-entry tracking matches Pi `leafIdAfterEntry`: the last
    # entry is a session_info — its id becomes the leaf.
    assert await storage.get_leaf_id() == "ffffffff"


async def test_pi_v3_fixture_entry_fields_round_trip(tmp_path: Path) -> None:
    """Decode each entry type and verify field-level parity."""

    target = tmp_path / "pi.jsonl"
    shutil.copy(_FIXTURE, target)
    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(target))
    entries: list[SessionTreeEntry] = await storage.get_entries()

    by_type: dict[str, list[SessionTreeEntry]] = {}
    for entry in entries:
        by_type.setdefault(entry.type, []).append(entry)

    user_msg = by_type["message"][0]
    assert user_msg.parent_id is None
    assert user_msg.id == "aaaaaaaa"
    assistant_msg = by_type["message"][1]
    assert assistant_msg.parent_id == "cccccccc"
    assert assistant_msg.id == "dddddddd"

    tlc = by_type["thinking_level_change"][0]
    assert tlc.thinking_level == "high"  # type: ignore[union-attr]
    assert tlc.parent_id == "aaaaaaaa"

    mc = by_type["model_change"][0]
    assert mc.provider == "anthropic"  # type: ignore[union-attr]
    assert mc.model_id == "claude-opus-4-7"  # type: ignore[union-attr]

    label = by_type["label"][0]
    assert label.target_id == "dddddddd"  # type: ignore[union-attr]
    assert label.label == "checkpoint"  # type: ignore[union-attr]

    info = by_type["session_info"][0]
    assert info.name == "Pi cross-runtime fixture"  # type: ignore[union-attr]


async def test_aelix_round_trip_preserves_camelcase(tmp_path: Path) -> None:
    """Append a new entry via Aelix and verify the wire form stays
    camelCase so a Pi reader can still parse the file."""

    from aelix_agent_core.session.entries import LabelEntry

    target = tmp_path / "pi.jsonl"
    shutil.copy(_FIXTURE, target)
    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(target))
    new_label = LabelEntry(
        id="newlabel",
        parent_id="ffffffff",
        timestamp="2026-05-17T00:00:07.000Z",
        target_id="dddddddd",
        label="aelix-added",
    )
    await storage.append_entry(new_label)

    raw = Path(target).read_text(encoding="utf-8")
    last_line = raw.splitlines()[-1]
    persisted = json.loads(last_line)
    # camelCase keys; no snake_case leaks.
    assert "parentId" in persisted
    assert "targetId" in persisted
    assert "parent_id" not in persisted
    assert "target_id" not in persisted
