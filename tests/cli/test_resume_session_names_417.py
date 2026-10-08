"""Saved names survive both resume picker paths and process restarts (#417)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_coding_agent.cli.entry import _resume_choice_label
from aelix_coding_agent.cli.session_labels import session_choice_label
from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command
from aelix_coding_agent.tui.shell import _format_session_choice


def metadata(tmp_path: Path, *records):
    path = tmp_path / "named.jsonl"
    header = {
        "type": "session",
        "version": 3,
        "id": "named",
        "cwd": str(tmp_path),
        "timestamp": "2026-10-08T10:00:00Z",
    }
    entries = [
        {
            "id": f"entry-{index}",
            "parentId": None,
            "timestamp": "2026-10-08T10:00:00Z",
            **record,
        }
        for index, record in enumerate(records)
    ]
    path.write_bytes(("\n".join(json.dumps(r) for r in [header, *entries]) + "\n").encode())
    return SimpleNamespace(path=str(path), id="named", created_at="2026-10-08T10:00:00Z")


def user(text):
    return {
        "type": "message",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def name(text):
    return {"type": "session_info", "name": text}


def test_both_resume_label_entrypoints_use_latest_name(tmp_path):
    meta = metadata(tmp_path, user("original prompt"), name("old title"), name("Renamed session"))
    assert "Renamed session" in _resume_choice_label(meta, 1e9)
    assert "Renamed session" in _format_session_choice(meta, 1e9)
    assert "original prompt" not in session_choice_label(meta, 1e9)


def test_name_before_long_later_messages_is_still_visible(tmp_path):
    meta = metadata(tmp_path, user("opening"), name("keep this name"), user("x" * 300000))
    assert "keep this name" in session_choice_label(meta, 1e9)


def test_bad_records_and_message_mentions_cannot_override_name(tmp_path):
    meta = metadata(tmp_path, name("good name"), name(17), user('"session_info" name evil'))
    with open(meta.path, "ab") as handle:
        handle.write(b'{"type":"session_info","name":"torn')
    assert "good name" in session_choice_label(meta, 1e9)


def test_names_use_existing_terminal_sanitizing(tmp_path):
    meta = metadata(tmp_path, name(" \x1b[31m한글\x9b31m\n이름 "))
    label = session_choice_label(meta, 1e9)
    assert "한글" in label and "이름" in label
    assert "\x1b" not in label and "\x9b" not in label and "\n" not in label


def test_deeply_nested_malformed_name_cannot_hide_a_saved_title(tmp_path):
    meta = metadata(tmp_path, user("opening"), name("good saved title"))
    with open(meta.path, "ab") as handle:
        handle.write(b'{"type":"session_info","name":' + b"[" * 20000 + b"]" * 20000 + b"}\n")
    assert "good saved title" in session_choice_label(meta, 1e9)


def test_blank_latest_name_clears_title_and_unnamed_falls_back(tmp_path):
    meta = metadata(tmp_path, user("opening task"), name("old name"), name("   "))
    assert "opening task" in session_choice_label(meta, 1e9)
    assert "old name" not in session_choice_label(meta, 1e9)
    empty = metadata(tmp_path, name(""))
    assert "(no messages)" in session_choice_label(empty, 1e9)


async def test_rename_alias_uses_existing_persistent_name_handler(tmp_path):
    saved = []

    class Session:
        async def append_session_name(self, value):
            saved.append(value)

    command = match_command("/rename Renamed task", BUILTIN_COMMANDS)
    assert command is not None and command.name == "name"
    ctx = CommandContext(
        chrome=SimpleNamespace(),
        harness=SimpleNamespace(session=Session()),
        commit=lambda value: None,
        cwd=str(tmp_path),
    )
    await command.handler(ctx, "Renamed task")
    assert saved == ["Renamed task"]


async def test_real_session_names_survive_reopen_and_fresh_repo_listing(tmp_path):
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionListOptions,
        JsonlSessionRepo,
    )
    from aelix_ai.messages import TextContent, UserMessage

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd="/project"))
    await session.append_message(UserMessage(content=[TextContent(text="opening task")]))
    await session.append_session_name("first title")
    await session.append_session_name("latest title")
    fresh_repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    listed = await fresh_repo.list(JsonlSessionListOptions(cwd="/project"))
    assert len(listed) == 1
    reopened = await fresh_repo.open(listed[0])
    assert await reopened.get_session_name() == "latest title"
    assert "latest title" in _resume_choice_label(listed[0], 1e9)
    assert "latest title" in _format_session_choice(listed[0], 1e9)


async def persisted_named_session(tmp_path):
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionListOptions,
        JsonlSessionRepo,
    )
    from aelix_ai.messages import TextContent, UserMessage

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd="/project"))
    await session.append_message(UserMessage(content=[TextContent(text="opening task")]))
    await session.append_session_name("retained title")
    meta = (await repo.list(JsonlSessionListOptions(cwd="/project")))[0]
    return repo, meta


def append_raw(meta, record):
    with open(meta.path, "ab") as handle:
        handle.write(json.dumps(record).encode() + b"\n")


def candidate_name(value: object = "invalid title") -> dict[str, object]:
    return {
        "type": "session_info",
        "id": "candidate-name",
        "parentId": None,
        "timestamp": "2026-10-08T10:00:00Z",
        "name": value,
    }


@pytest.mark.parametrize("field", ["id", "timestamp"])
@pytest.mark.parametrize("value", [None, "", 17, []])
async def test_invalid_common_name_fields_match_reopened_session(tmp_path, field, value):
    repo, meta = await persisted_named_session(tmp_path)
    record = candidate_name()
    record[field] = value
    append_raw(meta, record)
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() == "retained title"
    assert "retained title" in _resume_choice_label(meta, 1e9)
    assert "retained title" in _format_session_choice(meta, 1e9)


@pytest.mark.parametrize("field", ["id", "timestamp"])
async def test_missing_common_name_field_cannot_clear_a_title(tmp_path, field):
    repo, meta = await persisted_named_session(tmp_path)
    record = candidate_name(None)
    del record[field]
    append_raw(meta, record)
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() == "retained title"
    assert "retained title" in _resume_choice_label(meta, 1e9)
    assert "retained title" in _format_session_choice(meta, 1e9)


@pytest.mark.parametrize("value", [17, [], {}])
async def test_invalid_name_parent_matches_reopened_session(tmp_path, value):
    repo, meta = await persisted_named_session(tmp_path)
    record = candidate_name()
    record["parentId"] = value
    append_raw(meta, record)
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() == "retained title"
    assert "retained title" in _resume_choice_label(meta, 1e9)
    assert "retained title" in _format_session_choice(meta, 1e9)


@pytest.mark.parametrize("value", [17, [], {}, False])
async def test_invalid_name_type_is_skipped_by_reopen_and_both_pickers(tmp_path, value):
    repo, meta = await persisted_named_session(tmp_path)
    append_raw(meta, candidate_name(value))
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() == "retained title"
    assert "retained title" in _resume_choice_label(meta, 1e9)
    assert "retained title" in _format_session_choice(meta, 1e9)


@pytest.mark.parametrize("value", [None, "", "   ", "missing"])
async def test_valid_name_clear_matches_reopened_session(tmp_path, value):
    repo, meta = await persisted_named_session(tmp_path)
    record = candidate_name(value)
    if value == "missing":
        del record["name"]
    append_raw(meta, record)
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() is None
    assert "opening task" in _resume_choice_label(meta, 1e9)
    assert "opening task" in _format_session_choice(meta, 1e9)


async def test_orphan_name_after_corrupt_ancestor_outside_tail_is_not_a_title(tmp_path):
    repo, meta = await persisted_named_session(tmp_path)
    # The name at the tail is structurally valid. Only full-file recovery can
    # know its ancestor was dropped, including the transitive descendant.
    append_raw(meta, {"type": "message", "id": "broken-parent"})
    append_raw(
        meta,
        {
            "type": "message",
            "id": "padding",
            "parentId": None,
            "timestamp": "2026-10-08T10:00:00Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "x" * 300000}],
            },
        },
    )
    record = candidate_name("orphan title")
    record["parentId"] = "broken-parent"
    append_raw(meta, record)
    child = candidate_name("orphan child title")
    child["id"] = "orphan-child"
    child["parentId"] = record["id"]
    append_raw(meta, child)
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() == "retained title"
    assert "retained title" in _resume_choice_label(meta, 1e9)
    assert "retained title" in _format_session_choice(meta, 1e9)


async def test_json_escaped_name_record_type_matches_reopened_session(tmp_path):
    repo, meta = await persisted_named_session(tmp_path)
    record = candidate_name("escaped type title")
    raw = json.dumps(record).replace("session_info", "session\\u005finfo")
    with open(meta.path, "ab") as handle:
        handle.write(raw.encode() + b"\n")
    reopened = await repo.open(meta)
    assert await reopened.get_session_name() == "escaped type title"
    assert "escaped type title" in _resume_choice_label(meta, 1e9)
    assert "escaped type title" in _format_session_choice(meta, 1e9)


async def test_picker_inspection_does_not_log_recovery_or_rewrite_bytes(tmp_path, caplog):
    _, meta = await persisted_named_session(tmp_path)
    append_raw(meta, {"type": "session_info", "name": "malformed title"})
    original = Path(meta.path).read_bytes()
    assert "retained title" in _resume_choice_label(meta, 1e9)
    assert "retained title" in _format_session_choice(meta, 1e9)
    assert not caplog.records
    assert Path(meta.path).read_bytes() == original
