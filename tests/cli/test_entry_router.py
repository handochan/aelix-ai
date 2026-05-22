"""Sprint 6h₆ (Phase 5a-i + 5a-ii, ADR-0089) — ``cli/entry.py`` tests.

Covers:
  - :func:`resolve_app_mode` decision table (Pi parity, main.ts:96-113).
  - :func:`to_print_output_mode` mapping.
  - ``--rpc`` + ``@file`` guard.
  - ``--version`` short-circuit.
  - ``--help`` short-circuit.
  - ``--list-models`` deferred error path.
  - Interactive mode raises :class:`NotImplementedError` (Phase 5b
    carry-forward).
  - Piped stdin → print mode promotion.
  - ``python -m aelix_coding_agent --version`` end-to-end smoke.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import (
    _async_main,
    resolve_app_mode,
    to_print_output_mode,
)

# === resolve_app_mode decision table (Pi main.ts:96-113) ====================


def test_resolve_rpc_explicit() -> None:
    args = Args(mode="rpc")
    assert resolve_app_mode(args, stdin_is_tty=True) == "rpc"


def test_resolve_rpc_overrides_print_flag() -> None:
    args = Args(mode="rpc", print_mode=True)
    assert resolve_app_mode(args, stdin_is_tty=False) == "rpc"


def test_resolve_json_explicit() -> None:
    args = Args(mode="json")
    assert resolve_app_mode(args, stdin_is_tty=True) == "json"


def test_resolve_json_overrides_print_flag() -> None:
    args = Args(mode="json", print_mode=True)
    assert resolve_app_mode(args, stdin_is_tty=False) == "json"


def test_resolve_print_flag() -> None:
    args = Args(print_mode=True)
    assert resolve_app_mode(args, stdin_is_tty=True) == "print"


def test_resolve_piped_stdin_promotes_to_print() -> None:
    args = Args()
    assert resolve_app_mode(args, stdin_is_tty=False) == "print"


def test_resolve_default_interactive() -> None:
    args = Args()
    assert resolve_app_mode(args, stdin_is_tty=True) == "interactive"


# === to_print_output_mode ====================================================


def test_to_print_output_mode_json() -> None:
    assert to_print_output_mode("json") == "json"


def test_to_print_output_mode_print_is_text() -> None:
    assert to_print_output_mode("print") == "text"


def test_to_print_output_mode_other_falls_back_text() -> None:
    # Defensive: any non-json mode maps to text.
    assert to_print_output_mode("rpc") == "text"
    assert to_print_output_mode("interactive") == "text"


# === --version short-circuit =================================================


async def test_version_prints_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--version"])
    captured = capsys.readouterr()
    assert code == 0
    # VERSION is non-empty (test_config asserts this).
    assert captured.out.strip()


# === --help short-circuit ====================================================


async def test_help_prints_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "aelix" in captured.out.lower()
    assert "--help" in captured.out


# === --list-models deferred ===================================================


async def test_list_models_deferred_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--list-models"])
    captured = capsys.readouterr()
    assert code == 1
    assert "SettingsManager" in captured.err or "deferred" in captured.err.lower()


# === Diagnostic-error short-circuit ==========================================


async def test_diagnostic_error_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A parse-time error diagnostic (e.g., --mode bogus) → exit 1."""

    code = await _async_main(["--mode", "bogus"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err
    assert "--mode" in captured.err


# === --rpc + @file guard =====================================================


async def test_rpc_plus_file_arg_returns_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    f = tmp_path / "foo.txt"
    f.write_text("hi")
    code = await _async_main(["--mode", "rpc", f"@{f}"])
    captured = capsys.readouterr()
    assert code == 1
    assert "rpc" in captured.err.lower()
    assert "@file" in captured.err or "file" in captured.err.lower()


# === Interactive mode → NotImplementedError ==================================


async def test_interactive_mode_raises_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TTY stdin invocation with no --print flag picks "interactive";
    Sprint 6h₆ raises :class:`NotImplementedError` (Phase 5b deferred).
    """

    class _FakeTTYStdin:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:  # pragma: no cover — never read on TTY
            return ""

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())

    with pytest.raises(NotImplementedError):
        await _async_main([])


# === Piped stdin promotes to print mode ======================================


async def test_piped_stdin_promotes_to_print(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-TTY stdin promotes to print mode — but with no message
    and no initial content, the print path is a no-op (exit 0)."""

    class _FakePipedStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return ""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())

    # No initial message + no residual messages: print mode loop exits
    # cleanly without calling the harness.
    code = await _async_main(["--no-session"])
    # Should not raise NotImplementedError.
    assert code in (0, 1)


# === End-to-end subprocess smoke tests =======================================


def test_module_dash_m_version() -> None:
    """Smoke: ``python -m aelix_coding_agent --version`` exits 0 with
    a non-empty version string on stdout.
    """

    result = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_module_dash_m_help() -> None:
    """Smoke: ``python -m aelix_coding_agent --help`` exits 0 and emits
    the help banner with ``aelix`` in it.
    """

    result = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "aelix" in result.stdout.lower()
    assert "--help" in result.stdout


# === Bare diagnostic guard =================================================


def test_args_module_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: Args + parse_args wiring through entry sees diagnostics."""

    from aelix_coding_agent.cli.args import parse_args

    parsed = parse_args(["--mode", "wat"])
    assert any(d["type"] == "error" for d in parsed.diagnostics)


# Silence unused-import warning if Any is not consumed above.
_UNUSED: Any = (io,)
