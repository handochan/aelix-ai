"""Sprint 6h₈ (Phase 5a-iv, ADR-0092, §D) — ``JsonlSessionRepo.find_most_recent``.

Pi parity: ``findMostRecentSession`` (``core/session-manager.ts:480-493``)
+ ``isValidSessionFile`` (``:464-478``) at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Covers: mtime DESC sort, cwd filter via encoded directory layout,
invalid-header skip, empty case, and nonexistent cwd → ``None``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)


async def test_empty_cwd_returns_none(tmp_path: Path) -> None:
    """A cwd with no sessions directory returns :data:`None`."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    result = await repo.find_most_recent("/nonexistent/cwd/path")
    assert result is None


async def test_cwd_with_sessions_root_but_no_dir_returns_none(
    tmp_path: Path,
) -> None:
    """A sessions root exists but the cwd-encoded dir does not."""

    # Create the root so absolute_path resolves; the cwd-encoded
    # subdirectory is absent.
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    result = await repo.find_most_recent("/some/cwd/with/no/sessions")
    assert result is None


async def test_single_session_returned(tmp_path: Path) -> None:
    """A directory with one valid session returns its metadata."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    expected_meta = await session.get_metadata()

    found = await repo.find_most_recent("/r1")
    assert found is not None
    assert found.id == expected_meta.id
    assert found.path == expected_meta.path


async def test_most_recent_session_returned_by_mtime(tmp_path: Path) -> None:
    """When two sessions exist, the one with newer mtime is returned."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    a = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    await asyncio.sleep(0.01)
    b = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))

    a_meta = await a.get_metadata()
    b_meta = await b.get_metadata()

    # Force a's mtime older than b's via os.utime (immune to fs jitter).
    os.utime(a_meta.path, (1000, 1000))
    os.utime(b_meta.path, (2000, 2000))

    found = await repo.find_most_recent("/r1")
    assert found is not None
    assert found.id == b_meta.id


async def test_mtime_overrides_created_at_order(tmp_path: Path) -> None:
    """mtime sort wins over header ``created_at`` (divergence from ``list``)."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    a = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    await asyncio.sleep(0.01)
    b = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))

    a_meta = await a.get_metadata()
    b_meta = await b.get_metadata()

    # ``b`` was created later (newer ``created_at``) but pin ``a``'s
    # mtime newer so ``find_most_recent`` picks ``a``.
    os.utime(a_meta.path, (5000, 5000))
    os.utime(b_meta.path, (1000, 1000))

    found = await repo.find_most_recent("/r1")
    assert found is not None
    assert found.id == a_meta.id


async def test_cwd_filter_excludes_other_cwd_sessions(tmp_path: Path) -> None:
    """Sessions in a different cwd are not returned."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    other = await repo.create(JsonlSessionCreateOptions(cwd="/r2"))
    other_meta = await other.get_metadata()

    found = await repo.find_most_recent("/r1")
    assert found is not None
    # The /r2 session must not appear under /r1.
    assert found.id != other_meta.id


async def test_invalid_header_files_skipped(tmp_path: Path) -> None:
    """Files with an invalid first-line header are skipped."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    # Build a valid session first so the directory exists.
    valid = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    valid_meta = await valid.get_metadata()

    # Drop an invalid .jsonl alongside.
    bad_path = Path(valid_meta.path).parent / "bogus.jsonl"
    bad_path.write_text("this is not JSON")
    # Force the bad file's mtime newer so a naïve sort would pick it.
    os.utime(bad_path, (9999, 9999))
    os.utime(valid_meta.path, (1000, 1000))

    found = await repo.find_most_recent("/r1")
    assert found is not None
    assert found.id == valid_meta.id


async def test_missing_id_in_header_skipped(tmp_path: Path) -> None:
    """Header with missing/empty ``id`` is filtered out."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    valid = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    valid_meta = await valid.get_metadata()

    # Drop a "valid JSON" but type-wrong .jsonl alongside.
    bad_path = Path(valid_meta.path).parent / "bogus.jsonl"
    bad_path.write_text(json.dumps({"type": "session", "id": ""}) + "\n")
    os.utime(bad_path, (9999, 9999))
    os.utime(valid_meta.path, (1000, 1000))

    found = await repo.find_most_recent("/r1")
    assert found is not None
    assert found.id == valid_meta.id


async def test_non_jsonl_files_ignored(tmp_path: Path) -> None:
    """Files without .jsonl extension are ignored."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    valid = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    valid_meta = await valid.get_metadata()

    # Drop a non-.jsonl file alongside.
    extra = Path(valid_meta.path).parent / "notes.txt"
    extra.write_text("ignored")

    found = await repo.find_most_recent("/r1")
    assert found is not None
    assert found.id == valid_meta.id


def test_is_valid_session_file_helper_static(tmp_path: Path) -> None:
    """``_is_valid_session_file`` is callable as a static method."""

    p = tmp_path / "x.jsonl"
    p.write_text(
        json.dumps({"type": "session", "id": "abc"}) + "\n"
        + json.dumps({"type": "message"}) + "\n"
    )
    assert JsonlSessionRepo._is_valid_session_file(p) is True

    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json")
    assert JsonlSessionRepo._is_valid_session_file(bad) is False

    missing = tmp_path / "missing.jsonl"
    assert JsonlSessionRepo._is_valid_session_file(missing) is False


# === W5 MAJOR-1 fold-in regression =========================================


async def test_falls_through_to_older_when_newer_metadata_parse_fails(
    tmp_path: Path,
) -> None:
    """When the most-recent candidate's full metadata parse fails,
    ``find_most_recent`` falls through to the older valid candidate.

    Sprint 6h₈ W5 MAJOR-1 fold-in regression. Previously the loader
    invoked ``load_jsonl_session_metadata`` only on the first sort-
    descending candidate and silently returned :data:`None` on
    ``SessionError`` — losing access to all prior valid sessions. The
    fix iterates candidates in mtime-DESC order until one parses
    successfully.

    Test scenario: write one valid session (older mtime), then drop a
    sniff-passes-but-load-fails file alongside (newer mtime). The
    sniff (`_is_valid_session_file`) reads only the first 512 bytes
    and checks ``type == "session"`` + ``id`` non-empty, so a minimal
    ``{"type": "session", "id": "..."}`` header passes — but the full
    ``load_jsonl_session_metadata`` requires more fields and raises
    ``SessionError``. The loader must skip this candidate and return
    the older valid session.
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    valid_session = await repo.create(
        JsonlSessionCreateOptions(cwd="/regression/cwd")
    )
    valid_meta = await valid_session.get_metadata()

    # Drop a sniff-passes-but-load-fails JSONL alongside.
    bogus_path = Path(valid_meta.path).parent / "z_bogus.jsonl"
    bogus_path.write_text(
        json.dumps({"type": "session", "id": "header-only"}) + "\n",
        encoding="utf-8",
    )

    # Force the bogus file to be the newest by mtime.
    os.utime(bogus_path, (99999, 99999))
    os.utime(valid_meta.path, (1000, 1000))

    # Sanity: the sniff sees the bogus file as valid.
    assert JsonlSessionRepo._is_valid_session_file(bogus_path) is True

    # Act + assert: the loader must skip the bogus file and return the
    # older valid metadata, NOT ``None``.
    found = await repo.find_most_recent("/regression/cwd")
    assert found is not None
    assert found.id == valid_meta.id
