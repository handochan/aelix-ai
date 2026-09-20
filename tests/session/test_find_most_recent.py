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
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_repo import _HEADER_LINE_MAX_BYTES
from aelix_ai.messages import TextContent, UserMessage


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
    sniff (`_is_valid_session_file`) reads the first line and checks
    only ``type == "session"`` + ``id`` non-empty, so a minimal
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


# === #297: a header longer than 512 bytes is still a header ================


async def test_fork_under_a_deep_cwd_is_found(tmp_path: Path) -> None:
    """A fork whose header runs past 512 bytes is the file ``--continue`` picks.

    #297, reproduced live before this test existed: under a 143-character cwd a
    fork's header measured 557 bytes. A fork's header carries ``cwd`` AND
    ``parentSession``, and ``parentSession`` embeds the encoded cwd a second
    time, so it is roughly twice the origin's. The old ``f.read(512)`` sniff
    truncated that line, ``json.loads`` raised, and the fork was dropped as "not
    a session" — so ``find_most_recent`` fell through to the **origin** and the
    next ``--continue`` resumed the file the user had just forked away from.

    Red on ``47dfacd``: ``_is_valid_session_file(fork)`` was ``False`` and
    ``find_most_recent`` returned the origin.
    """

    deep = "/" + "/".join(f"deep-directory-segment-{i:02d}" for i in range(6))
    assert len(deep) > 120

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=deep))
    await source.append_message(UserMessage(content=[TextContent(text="u1")]))
    source_meta = await source.get_metadata()

    forked = await repo.fork(
        source_meta,
        ForkOptions(cwd=deep, parent_session_path=source_meta.path),
    )
    fork_meta = await forked.get_metadata()

    source_header = Path(source_meta.path).read_bytes().split(b"\n", 1)[0]
    fork_header = Path(fork_meta.path).read_bytes().split(b"\n", 1)[0]
    # The premise of the defect: the origin fits in 512 bytes, the fork does not.
    assert len(source_header) < 512
    assert len(fork_header) > 512

    # The fork is the newest file, so it is what `--continue` must resolve to.
    os.utime(source_meta.path, (1000, 1000))
    os.utime(fork_meta.path, (99999, 99999))

    assert JsonlSessionRepo._is_valid_session_file(Path(fork_meta.path)) is True
    found = await repo.find_most_recent(deep)
    assert found is not None
    assert found.path == fork_meta.path
    assert found.id == fork_meta.id


def test_header_between_512_bytes_and_the_cap_sniffs(tmp_path: Path) -> None:
    """The sniff's only bound is the newline, up to the 64 KiB cap.

    The unit-level half of #297: 512 was a cliff, not a limit. Red on
    ``47dfacd`` — ``f.read(512)`` returned a truncated object.
    """

    p = tmp_path / "long-header.jsonl"
    header = {"type": "session", "id": "long", "cwd": "/" + "x" * 4096}
    p.write_text(json.dumps(header) + "\n" + json.dumps({"type": "message"}) + "\n")
    assert len(json.dumps(header).encode()) > 512
    assert JsonlSessionRepo._is_valid_session_file(p) is True


def test_header_line_that_runs_past_the_cap_is_refused(tmp_path: Path) -> None:
    """A first line that reaches the cap without a newline is not a header.

    Built so the guard is load-bearing: the JSON object closes on the cap's
    LAST byte and the real line continues past it. Without the explicit
    "capped and unterminated" check the sniff would parse that prefix, answer
    ``True``, and hand ``load_jsonl_session_metadata`` — which has no cap — a
    line it cannot parse.
    """

    head = '{"type": "session", "id": "cap", "pad": "'
    tail = '"}'
    line = head + "A" * (_HEADER_LINE_MAX_BYTES - len(head) - len(tail)) + tail
    assert len(line.encode()) == _HEADER_LINE_MAX_BYTES
    assert json.loads(line)["type"] == "session"

    p = tmp_path / "over-cap.jsonl"
    p.write_bytes(line.encode() + b"BBBBBBBBBB" + b"\n")
    assert JsonlSessionRepo._is_valid_session_file(p) is False


def test_header_line_ending_exactly_on_the_cap_still_sniffs(
    tmp_path: Path,
) -> None:
    """The cap counts the newline: a complete line that ends on it is valid."""

    head = '{"type": "session", "id": "cap", "pad": "'
    tail = '"}'
    line = head + "A" * (_HEADER_LINE_MAX_BYTES - 1 - len(head) - len(tail)) + tail
    assert len((line + "\n").encode()) == _HEADER_LINE_MAX_BYTES

    p = tmp_path / "at-cap.jsonl"
    p.write_bytes(line.encode() + b"\n")
    assert JsonlSessionRepo._is_valid_session_file(p) is True


def test_unterminated_header_still_sniffs(tmp_path: Path) -> None:
    """ADR-0208: a complete but unterminated final line is still a session.

    ``readline`` returns it without a trailing ``\\n``; only a line that ALSO
    reached the cap is refused.
    """

    p = tmp_path / "no-newline.jsonl"
    p.write_bytes(json.dumps({"type": "session", "id": "abc"}).encode())
    assert JsonlSessionRepo._is_valid_session_file(p) is True


def test_crlf_header_still_sniffs(tmp_path: Path) -> None:
    """A CRLF header from pre-ADR-0242 Windows Aelix still parses.

    ``readline`` stops at ``\\n`` and leaves the ``\\r``, which is whitespace to
    ``json.loads`` — the same reason this worked before #297. Pinned because the
    #297 rewrite touches exactly this line-splitting code.
    """

    p = tmp_path / "crlf.jsonl"
    p.write_bytes(
        json.dumps({"type": "session", "id": "crlf"}).encode()
        + b"\r\n"
        + json.dumps({"type": "message"}).encode()
        + b"\r\n"
    )
    assert JsonlSessionRepo._is_valid_session_file(p) is True
