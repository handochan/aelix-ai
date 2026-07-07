"""Issue #28 — startup ``--resume`` / ``-r`` wiring tests.

Before this change ``--resume`` printed a Phase-5b line and then
``raise NotImplementedError`` (an ~18-line traceback, exit 1). Now it is a real
session source:

- ``--resume <id>`` resolves the id/prefix (reusing the ``--session`` resolver)
  and opens it; a miss is a clean ``not_found`` diagnostic.
- ``--resume`` (no id) is an interactive picker over the cwd's sessions; a
  picker needs a TTY, so in print/json/rpc it is a clean argument error, NOT a
  traceback and NOT a silent most-recent open (that is ``--continue``).
- ``--resume`` is mutually exclusive with the other session-source flags.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aelix_agent_core.session.fs import LocalFileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.storage import JsonlSessionMetadata, SessionError
from aelix_ai.messages import TextContent, UserMessage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.args import Args, parse_args
from aelix_coding_agent.cli.entry import (
    _async_main,
    _prompt_resume_choice,
    _resume_choice_label,
    _resume_session_startup,
    _validate_resume_flag,
)

# === arg parsing ============================================================


def test_parse_resume_bare_is_picker() -> None:
    for flag in ("--resume", "-r"):
        p = parse_args([flag])
        assert p.resume is True and p.resume_id is None


def test_parse_resume_with_id() -> None:
    for flag in ("--resume", "-r"):
        p = parse_args([flag, "abc123"])
        assert p.resume is True and p.resume_id == "abc123"


def test_parse_resume_does_not_eat_a_flag() -> None:
    p = parse_args(["--resume", "-p", "hi"])
    assert p.resume is True and p.resume_id is None and p.print_mode is True


def test_parse_resume_does_not_eat_a_file_positional() -> None:
    p = parse_args(["-r", "@notes.txt"])
    assert p.resume is True and p.resume_id is None


# === _validate_resume_flag ==================================================


def test_validate_resume_conflicts() -> None:
    assert "--no-session" in (_validate_resume_flag(Args(resume=True, no_session=True)) or "")
    assert "--session" in (_validate_resume_flag(Args(resume=True, session="/x.jsonl")) or "")
    assert "--fork" in (_validate_resume_flag(Args(resume=True, fork="id")) or "")
    assert "--continue" in (
        _validate_resume_flag(Args(resume=True, continue_session=True)) or ""
    )


def test_validate_resume_alone_ok() -> None:
    assert _validate_resume_flag(Args(resume=True)) is None
    assert _validate_resume_flag(Args(resume=True, resume_id="abc")) is None


def test_validate_resume_unset_returns_none() -> None:
    assert _validate_resume_flag(Args()) is None


# === _resume_choice_label ===================================================


def _meta(id_: str, created: str = "2026-07-06T10:11:12", cwd: str = "/w") -> JsonlSessionMetadata:
    return JsonlSessionMetadata(id=id_, created_at=created, cwd=cwd, path=f"{cwd}/{id_}.jsonl")


def test_resume_choice_label_created_and_id() -> None:
    assert _resume_choice_label(_meta("abcdef1234")) == "2026-07-06 10:11 · abcdef12"


def test_resume_choice_label_id_only() -> None:
    assert _resume_choice_label(_meta("abcdef12", created="")) == "abcdef12"


def test_resume_choice_label_empty_is_session() -> None:
    assert _resume_choice_label(_meta("", created="")) == "session"


# === _prompt_resume_choice (monkeypatch the stdin read) =====================


async def _prompt_with(monkeypatch: pytest.MonkeyPatch, reply: object, sessions: list):
    def _fake_read() -> str:
        if isinstance(reply, BaseException):
            raise reply
        return str(reply)

    monkeypatch.setattr(entry_mod, "_read_resume_line", _fake_read)
    return await _prompt_resume_choice(sessions)


async def test_prompt_valid_number_returns_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = [_meta("aaa"), _meta("bbb"), _meta("ccc")]
    assert await _prompt_with(monkeypatch, "2", sessions) is sessions[1]


async def test_prompt_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _prompt_with(monkeypatch, "", [_meta("aaa")]) is None


async def test_prompt_non_number_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _prompt_with(monkeypatch, "nope", [_meta("aaa")]) is None


async def test_prompt_out_of_range_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _prompt_with(monkeypatch, "9", [_meta("aaa")]) is None


async def test_prompt_eof_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _prompt_with(monkeypatch, EOFError(), [_meta("aaa")]) is None


# === _resume_session_startup (real repo) ====================================


async def _seed(root: Path, cwd: str, *, text: str = "hello") -> tuple[
    LocalFileSystem, JsonlSessionRepo, JsonlSessionMetadata
]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(root))
    session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    await session.append_message(UserMessage(content=[TextContent(text=text)]))
    return fs, repo, await session.get_metadata()


async def test_startup_resume_by_id_opens_session(tmp_path: Path) -> None:
    cwd = str(tmp_path / "proj")
    fs, repo, meta = await _seed(tmp_path / "sroot", cwd)
    parsed = Args(resume=True, resume_id=meta.id)
    session = await _resume_session_startup(parsed, repo, fs, cwd)
    assert (await session.get_metadata()).id == meta.id


async def test_startup_resume_by_id_prefix_opens_session(tmp_path: Path) -> None:
    cwd = str(tmp_path / "proj")
    fs, repo, meta = await _seed(tmp_path / "sroot", cwd)
    parsed = Args(resume=True, resume_id=meta.id[:6])  # prefix match
    session = await _resume_session_startup(parsed, repo, fs, cwd)
    assert (await session.get_metadata()).id == meta.id


async def test_startup_resume_by_id_miss_raises(tmp_path: Path) -> None:
    cwd = str(tmp_path / "proj")
    fs, repo, _ = await _seed(tmp_path / "sroot", cwd)
    parsed = Args(resume=True, resume_id="no-such-id")
    with pytest.raises(SessionError) as ei:
        await _resume_session_startup(parsed, repo, fs, cwd)
    assert ei.value.code == "not_found"


async def test_startup_resume_picker_selects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = str(tmp_path / "proj")
    fs, repo, meta = await _seed(tmp_path / "sroot", cwd)
    monkeypatch.setattr(entry_mod, "_read_resume_line", lambda: "1")
    parsed = Args(resume=True)  # no id → picker
    session = await _resume_session_startup(parsed, repo, fs, cwd)
    assert (await session.get_metadata()).id == meta.id


async def test_startup_resume_picker_cancel_starts_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = str(tmp_path / "proj")
    fs, repo, meta = await _seed(tmp_path / "sroot", cwd)
    monkeypatch.setattr(entry_mod, "_read_resume_line", lambda: "")  # cancel
    parsed = Args(resume=True)
    session = await _resume_session_startup(parsed, repo, fs, cwd)
    assert (await session.get_metadata()).id != meta.id  # a brand-new session


async def test_startup_resume_empty_root_starts_fresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "empty"))
    cwd = str(tmp_path / "proj")
    parsed = Args(resume=True)
    session = await _resume_session_startup(parsed, repo, fs, cwd)
    assert (await session.get_metadata()).id  # a fresh session exists
    assert "No previous sessions" in capsys.readouterr().err


# === end-to-end via _async_main =============================================


async def test_resume_no_id_non_interactive_exits_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: ``--resume`` (no id) in print mode must be a clean exit-1
    error, NOT the old ``NotImplementedError`` traceback."""

    code = await _async_main(["--resume"])  # pytest stdin is non-tty → print mode
    captured = capsys.readouterr()
    assert code == 1
    assert "interactive terminal" in captured.err
    assert "NotImplementedError" not in captured.err
    assert "Traceback" not in captured.err


async def test_resume_plus_no_session_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = await _async_main(["--resume", "--no-session"])
    captured = capsys.readouterr()
    assert code == 1
    assert "--no-session" in captured.err


async def test_resume_plus_fork_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = await _async_main(["--resume", "--fork", "some-id"])
    captured = capsys.readouterr()
    assert code == 1
    assert "--fork" in captured.err


async def test_resume_by_id_miss_exits_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--resume <bad-id>`` in print mode → clean ``not_found`` diagnostic."""

    class _FakePipedStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return ""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path))
    code = await _async_main(["--resume", "definitely-no-such-session"])
    captured = capsys.readouterr()
    assert code == 1
    assert "No session matching" in captured.err
    assert "Traceback" not in captured.err
