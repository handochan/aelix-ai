"""#769 — a background agent's result file: written 0600 through a temporary file, read back only as written.

The owner's choice of 2026-11-12 (ADR-0295's note): a background agent's whole
result goes to ``<children dir>/<run key>.result``. These pin the file itself;
``test_agent_retention_769.py`` pins the surfaces that read it.

Fails on ``5390b254``: ``aelix_agents.result_file`` does not exist.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from aelix_agents.result_file import ResultFile, read_result, result_path, write_result

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and links")
KEY = "sub-0123456789ab"
TEXT = "the whole result, 가나다 🙂\n" * 100


def test_it_is_written_whole_and_read_back(tmp_path: Path) -> None:
    written = write_result(str(tmp_path), KEY, TEXT)
    assert written is not None and written.key == KEY and written.size == len(TEXT.encode("utf-8"))
    assert read_result(str(tmp_path), written) == TEXT
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{KEY}.result"], "no temporary file left"
    assert ResultFile.from_record(written.record()) == written


@POSIX
def test_it_is_this_users_alone(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        written = write_result(str(tmp_path), KEY, TEXT)
    finally:
        os.umask(old)
    assert written is not None
    assert stat.S_IMODE(os.stat(tmp_path / f"{KEY}.result").st_mode) == 0o600


def test_a_write_that_fails_leaves_nothing_and_says_none(tmp_path: Path) -> None:
    assert write_result(str(tmp_path / "missing"), KEY, TEXT) is None
    assert write_result(str(tmp_path), "../escape", TEXT) is None
    assert list(tmp_path.iterdir()) == []


def test_a_file_that_is_gone_or_changed_is_said_so(tmp_path: Path) -> None:
    written = write_result(str(tmp_path), KEY, TEXT)
    assert written is not None
    path = tmp_path / f"{KEY}.result"
    data = path.read_bytes()
    path.write_bytes(data[:-1] + b"!")  # same size, other bytes
    assert read_result(str(tmp_path), written) == "its result file is not the one aelix wrote"
    path.write_bytes(data + b"more")
    assert read_result(str(tmp_path), written) == "its result file is not the one aelix wrote"
    path.unlink()
    assert read_result(str(tmp_path), written) == "its result file no longer exists"


@POSIX
def test_a_link_in_its_place_is_never_followed(tmp_path: Path) -> None:
    written = write_result(str(tmp_path), KEY, TEXT)
    assert written is not None
    path = tmp_path / f"{KEY}.result"
    elsewhere = tmp_path / "elsewhere"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    assert read_result(str(tmp_path), written) == "its result file cannot be opened"
    path.unlink()
    os.link(elsewhere, path)
    assert read_result(str(tmp_path), written) == "its result file is not the one aelix wrote"


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"v": 2, "key": KEY, "bytes": 1, "sha256": "x"},
        {"v": 1, "key": "../../etc/passwd", "bytes": 1, "sha256": "x"},
        {"v": 1, "key": KEY, "bytes": True, "sha256": "x"},
        {"v": 1, "key": KEY, "bytes": -1, "sha256": "x"},
        {"v": 1, "key": KEY, "bytes": 1},
    ],
)
def test_a_record_of_another_shape_names_no_file(record: object) -> None:
    assert ResultFile.from_record(record) is None


def test_only_a_spawn_key_names_a_file(tmp_path: Path) -> None:
    assert result_path(str(tmp_path), KEY) == str(tmp_path / f"{KEY}.result")
    assert result_path(str(tmp_path), "sub-../x") is None


def test_a_replace_that_fails_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object) -> None:
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", refuse)
    assert write_result(str(tmp_path), KEY, TEXT) is None
    assert list(tmp_path.iterdir()) == []


def test_a_text_that_cannot_be_encoded_writes_nothing_and_says_none(tmp_path: Path) -> None:
    """Verify-r2 F4: a lone surrogate. Fails on ``c06128c8``: ``UnicodeEncodeError`` escaped."""

    assert write_result(str(tmp_path), KEY, "half a pair: \ud800") is None
    assert list(tmp_path.iterdir()) == []


@POSIX
def test_a_fifo_in_its_place_is_refused_at_once(tmp_path: Path) -> None:
    """Verify-r2 F8 and mutant R12: the open does not wait for a writer, and what it opened is
    refused for not being a regular file — even with the size and sha256 an empty result has.

    Fails on ``c06128c8``: the open blocked until a writer came.
    """

    import threading

    written = write_result(str(tmp_path), KEY, "")
    assert written is not None and written.size == 0
    path = tmp_path / f"{KEY}.result"
    path.unlink()
    os.mkfifo(path)
    box: list[object] = []
    reader = threading.Thread(target=lambda: box.append(read_result(str(tmp_path), written)), daemon=True)
    reader.start()
    reader.join(2.0)
    try:
        assert not reader.is_alive(), "the open blocked"
        assert box == ["its result file is not the one aelix wrote"]
    finally:
        if reader.is_alive():
            os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
            reader.join(2.0)


def test_a_result_file_with_no_children_directory_to_find_it_in_is_said() -> None:
    """Verify-r2 mutant C04: a payload that records a file, read where the parent's session file
    is not in a sessions bucket, says so rather than calling the result not kept.
    A guard on ``c06128c8`` (it passes there)."""

    from aelix_agents.jobs_client import KIND, DelegationJobs, DelegationKindAdapter
    from aelix_coding_agent.jobs.contract import JobSnapshot

    snap = JobSnapshot(
        job_id="j", kind=KIND, label="agent", title="t", state="done", owner="s", created=0.0,
        elapsed=1.0, last_activity_ago=None, quiet=False, result_waiting=False,
        notice_state="delivered", payload={"result": {"v": 1, "key": KEY, "bytes": 1, "sha256": "x"}},
    )
    page = DelegationKindAdapter(DelegationJobs(), session=lambda: None).output(snap, offset=0, limit=1024)
    assert page is not None and page.text == (
        "Its result cannot be read: this conversation's session file is not where its agents' files are."
    )
