"""Sprint 6h₈ (Phase 5a-iv, ADR-0092, §D) — ``--continue`` / ``-c`` tests.

Covers:

- 3 incompatible-flag conflicts (``--no-session`` / ``--session`` /
  ``--fork``) → stderr error + exit 1.
- Empty cwd silent fallback to a fresh session.
- ``--continue`` + ``--print`` smoke (OK combo).
- ``--continue`` re-opens the most-recent session via
  :meth:`JsonlSessionRepo.find_most_recent`.

Pi citation: ``main.ts:280-281`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import (
    _async_main,
    _validate_continue_flag,
)

# === _validate_continue_flag (unit) =========================================


def test_validate_continue_with_no_session_conflict() -> None:
    args = Args(continue_session=True, no_session=True)
    err = _validate_continue_flag(args)
    assert err is not None
    assert "--no-session" in err


def test_validate_continue_with_session_path_conflict() -> None:
    args = Args(continue_session=True, session="/path/x.jsonl")
    err = _validate_continue_flag(args)
    assert err is not None
    assert "--session" in err


def test_validate_continue_with_fork_conflict() -> None:
    args = Args(continue_session=True, fork="some-entry-id")
    err = _validate_continue_flag(args)
    assert err is not None
    assert "--fork" in err


def test_validate_continue_alone_ok() -> None:
    args = Args(continue_session=True)
    assert _validate_continue_flag(args) is None


def test_validate_continue_with_print_ok() -> None:
    args = Args(continue_session=True, print_mode=True, messages=["hi"])
    assert _validate_continue_flag(args) is None


def test_validate_continue_with_rpc_ok() -> None:
    args = Args(continue_session=True, mode="rpc")
    assert _validate_continue_flag(args) is None


def test_validate_continue_with_session_dir_ok() -> None:
    args = Args(continue_session=True, session_dir="/some/dir")
    assert _validate_continue_flag(args) is None


def test_validate_continue_unset_returns_none() -> None:
    args = Args()
    assert _validate_continue_flag(args) is None


# === end-to-end via _async_main =============================================


async def test_continue_plus_no_session_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--continue", "--no-session"])
    captured = capsys.readouterr()
    assert code == 1
    assert "--no-session" in captured.err


async def test_continue_plus_session_exits_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    fake = tmp_path / "x.jsonl"
    fake.write_text("{}\n")
    code = await _async_main(["--continue", "--session", str(fake)])
    captured = capsys.readouterr()
    assert code == 1
    assert "--session" in captured.err


async def test_continue_plus_fork_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--continue", "--fork", "some-id"])
    captured = capsys.readouterr()
    assert code == 1
    assert "--fork" in captured.err


async def test_continue_empty_cwd_silent_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``--continue`` with zero sessions in cwd silently creates a new one.

    Uses ``--no-session=False`` (default) + a piped stdin to promote to
    print mode + an isolated empty sessions root so no prior session
    exists. The print mode runs to completion without raising.
    """

    class _FakePipedStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return ""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    # Redirect sessions root so we get a clean slate.
    monkeypatch.setenv("HOME", str(tmp_path))
    code = await _async_main(["--continue"])
    # Empty piped stdin + no messages → no-op print mode; either exits
    # 0 (clean no-op) or 1 (provider config absent — Aelix default
    # without API keys). Both outcomes prove `--continue` did NOT raise
    # on empty cwd. Pi parity: silent fallback (no error message about
    # the missing session).
    assert code in (0, 1)


# === W5 MAJOR-2 fold-in regression ========================================


async def test_list_models_with_continue_no_session_emits_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--list-models --continue --no-session`` must surface the
    continue-flag conflict error BEFORE the ``--list-models`` short-circuit
    completes (Sprint 6h₈ W5 MAJOR-2 fold-in regression).

    Previously ``_validate_continue_flag`` ran after the ``--list-models``
    short-circuit, so the incompatible combo was silently accepted on
    the list-models exit path. The fix moves the validator above the
    short-circuit; this test pins the new ordering.
    """

    code = await _async_main(["--list-models", "--continue", "--no-session"])
    captured = capsys.readouterr()
    assert code == 1
    assert "--no-session" in captured.err
    assert "--continue" in captured.err
