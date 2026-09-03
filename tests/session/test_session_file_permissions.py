"""Track S2 — session files are owner-only.

A session JSONL stores every prompt and every tool result verbatim, so an
agent that runs ``env`` writes an API key into it. These files used to be
created at the umask default (0644, or worse on a permissive umask) while
``auth.json`` next door was already 0600.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
)
from aelix_agent_core.session import fs as fs_mod
from aelix_agent_core.session.entries import MessageEntry
from aelix_agent_core.session.fs import SESSION_DIR_MODE, SESSION_FILE_MODE
from aelix_ai.messages import TextContent, UserMessage


def _mode(path: str | Path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


async def test_write_file_creates_owner_only_file_and_dir(
    tmp_path: Path,
) -> None:
    fs = LocalFileSystem()
    target = tmp_path / "sessions" / "s.jsonl"

    await fs.write_file(str(target), "{}\n")

    assert _mode(target) == SESSION_FILE_MODE
    assert _mode(target.parent) == SESSION_DIR_MODE


async def test_append_file_creates_owner_only_file(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    target = tmp_path / "sessions" / "s.jsonl"

    await fs.append_file(str(target), "{}\n")

    assert _mode(target) == SESSION_FILE_MODE
    assert _mode(target.parent) == SESSION_DIR_MODE


async def test_create_dir_is_owner_only(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    target = tmp_path / "sessions"

    await fs.create_dir(str(target))

    assert _mode(target) == SESSION_DIR_MODE


async def test_preexisting_0644_file_is_tightened_on_append(
    tmp_path: Path,
) -> None:
    """Sessions written before this change migrate on their next write."""

    fs = LocalFileSystem()
    target = tmp_path / "old.jsonl"
    target.write_text("first\n", encoding="utf-8")
    os.chmod(target, 0o644)
    assert _mode(target) == 0o644

    await fs.append_file(str(target), "second\n")

    assert _mode(target) == SESSION_FILE_MODE
    # Tightening must not cost data.
    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


async def test_preexisting_loose_dir_is_tightened(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    session_dir = tmp_path / "loose"
    session_dir.mkdir(mode=0o755)
    os.chmod(session_dir, 0o755)

    await fs.append_file(str(session_dir / "s.jsonl"), "{}\n")

    assert _mode(session_dir) == SESSION_DIR_MODE


async def test_copy_file_does_not_inherit_loose_source_mode(
    tmp_path: Path,
) -> None:
    """``import_from_jsonl`` copies a caller-supplied file into the store.

    A copy that carried the source's mode across would leave a
    world-readable import world-readable inside ``~/.aelix/sessions``.
    """

    fs = LocalFileSystem()
    source = tmp_path / "incoming.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    os.chmod(source, 0o644)
    destination = tmp_path / "sessions" / "imported.jsonl"

    await fs.copy_file(str(source), str(destination))

    assert _mode(destination) == SESSION_FILE_MODE


async def test_copy_file_is_owner_only_before_content_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No window where imported content sits in a readable file.

    Creating the destination via ``copy2`` and tightening it afterwards
    left the imported prompts and tool results group/world-readable for
    the duration of the copy. Observe the mode at the moment the content
    write begins — it must already be 0600.
    """

    from aelix_agent_core.session import fs as fs_module

    fs = LocalFileSystem()
    source = tmp_path / "incoming.jsonl"
    source.write_text("AWS_SECRET=hunter2\n", encoding="utf-8")
    os.chmod(source, 0o644)
    destination = tmp_path / "store" / "imported.jsonl"

    observed: list[str] = []
    real_copyfile = fs_module.shutil.copyfile

    def _spy(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        # "absent" means content is about to be written into a file this
        # process never created at 0600 — the copy2 path, where the mode
        # is whatever umask and copystat decide.
        observed.append(
            oct(_mode(dst)) if Path(dst).exists() else "absent"
        )
        return real_copyfile(src, dst, *args, **kwargs)

    monkeypatch.setattr(fs_module.shutil, "copyfile", _spy)
    await fs.copy_file(str(source), str(destination))

    assert observed == [oct(SESSION_FILE_MODE)]
    assert _mode(destination) == SESSION_FILE_MODE


async def test_copy_file_preserves_mtime(tmp_path: Path) -> None:
    """``find_most_recent`` sorts by mtime, so the import must keep it.

    Guards the ``copy2`` -> ``copyfile`` swap: ``copyfile`` carries no
    metadata, so mtime is now restored explicitly.
    """

    fs = LocalFileSystem()
    source = tmp_path / "incoming.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    os.utime(source, (1_700_000_000, 1_700_000_000))
    destination = tmp_path / "store" / "imported.jsonl"

    await fs.copy_file(str(source), str(destination))

    assert destination.stat().st_mtime == pytest.approx(1_700_000_000)
    assert destination.read_text(encoding="utf-8") == "{}\n"


async def test_copy_file_tightens_preexisting_loose_destination(
    tmp_path: Path,
) -> None:
    """Re-importing over a destination left at 0644 by an older build."""

    fs = LocalFileSystem()
    source = tmp_path / "incoming.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    destination = tmp_path / "imported.jsonl"
    destination.write_text("stale\n", encoding="utf-8")
    os.chmod(destination, 0o644)

    await fs.copy_file(str(source), str(destination))

    assert _mode(destination) == SESSION_FILE_MODE
    assert destination.read_text(encoding="utf-8") == "{}\n"


async def test_real_session_lands_owner_only_end_to_end(tmp_path: Path) -> None:
    """The property that actually matters, through the public repo API."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    metadata = await session.get_metadata()

    await session.get_storage().append_entry(
        MessageEntry(
            id="aaa1",
            parent_id=None,
            timestamp="2026-08-07T00:00:01.000Z",
            message=UserMessage(
                content=[TextContent(text="AWS_SECRET=hunter2")]
            ),
        )
    )

    assert _mode(metadata.path) == SESSION_FILE_MODE
    assert _mode(Path(metadata.path).parent) == SESSION_DIR_MODE


# --- Windows: the tighten has no mode bits to tighten (#103 P0-b) -------------
#
# No ``skipif``: this pins an AttributeError, and the attribute is missing
# because of what ``sys.platform`` says, so deleting ``os.fchmod`` and saying
# "win32" reproduces it exactly on POSIX. A case that only runs on the runner we
# do not have is not a regression guard — same reasoning as
# ``tests/cli/test_stdio_encoding_win32.py``.


def _simulate_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the module see Windows: no ``os.fchmod``, ``sys.platform`` win32."""

    monkeypatch.setattr(fs_mod.sys, "platform", "win32", raising=True)
    monkeypatch.delattr(fs_mod.os, "fchmod", raising=True)


async def test_write_file_on_windows_does_not_die_on_missing_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suppress(OSError)` never caught this — AttributeError is not an OSError.

    Before the guard, every session write raised on Windows: 200 of the 433
    failures in the first ``windows-latest`` CI run came through here.

    The target must already be loose. On POSIX a fresh ``os.open(..., 0o600)``
    lands at 0600, ``st_mode & 0o077`` is 0, and ``fchmod`` is never reached —
    the case would pass with the guard removed and pin nothing. Windows has no
    such luck: it ignores the mode argument to ``os.open`` entirely, so real
    session files there are always loose by this test and always reached the
    missing attribute. Seeding 0644 is what makes POSIX ask the question
    Windows cannot avoid.
    """

    _simulate_win32(monkeypatch)
    fs = LocalFileSystem()
    target = tmp_path / "sessions" / "s.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("stale\n", encoding="utf-8")
    os.chmod(target, 0o644)

    await fs.write_file(str(target), '{"role":"user"}\n')

    assert target.read_text(encoding="utf-8") == '{"role":"user"}\n'


async def test_append_file_on_windows_does_not_die_on_missing_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _simulate_win32(monkeypatch)
    fs = LocalFileSystem()
    target = tmp_path / "sessions" / "s.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("first\n", encoding="utf-8")

    await fs.append_file(str(target), "second\n")

    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


async def test_the_guard_is_platform_scoped_not_a_blanket_disable(
    tmp_path: Path,
) -> None:
    """The POSIX tightening must survive the Windows fix.

    A guard written as a bare ``suppress(AttributeError)`` or an unconditional
    early return would pass both cases above while quietly retiring Track S2 on
    the platform that has mode bits. This is the case that would catch that.
    """

    target = tmp_path / "sessions" / "s.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    os.chmod(target, 0o644)

    await LocalFileSystem().append_file(str(target), "{}\n")

    assert _mode(target) == SESSION_FILE_MODE
