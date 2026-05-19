"""Sprint 6e · Phase 4.5 — ``aelix auth`` CLI subcommand tests.

Uses ``subprocess.run([sys.executable, "-m", "aelix", "auth", ...])`` so
the actual argparse + entry-point wire-up is exercised. Each invocation
points ``AELIX_AUTH_PATH`` at a tmp file so the developer's real
``~/.config/aelix/agent/auth.json`` is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(
    args: list[str], auth_path: Path, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AELIX_AUTH_PATH"] = str(auth_path)
    return subprocess.run(
        [sys.executable, "-m", "aelix", *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=30,
    )


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    return tmp_path / "auth.json"


# === aelix auth list ===


def test_auth_list_empty(auth_path: Path) -> None:
    result = _run_cli(["auth", "list"], auth_path)
    assert result.returncode == 0, result.stderr
    assert "no credentials stored" in result.stdout.lower()


def test_auth_list_with_entries(auth_path: Path) -> None:
    auth_path.write_text(
        json.dumps(
            {
                "openai": {"type": "api_key", "key": "sk-x"},
                "anthropic": {"type": "api_key", "key": "sk-y"},
            }
        )
    )
    result = _run_cli(["auth", "list"], auth_path)
    assert result.returncode == 0, result.stderr
    assert "openai" in result.stdout
    assert "anthropic" in result.stdout


# === aelix auth status ===


def test_auth_status_no_provider_lists_all(auth_path: Path) -> None:
    """No provider arg → lists all built-in OAuth providers + stored entries."""

    result = _run_cli(["auth", "status"], auth_path)
    assert result.returncode == 0, result.stderr
    # Should mention all 3 built-in OAuth providers.
    for pid in ("anthropic", "github-copilot", "openai-codex"):
        assert pid in result.stdout


def test_auth_status_single_provider_stored(auth_path: Path) -> None:
    auth_path.write_text(
        json.dumps({"anthropic": {"type": "api_key", "key": "sk-x"}})
    )
    result = _run_cli(["auth", "status", "anthropic"], auth_path)
    assert result.returncode == 0, result.stderr
    assert "anthropic" in result.stdout
    assert "stored" in result.stdout
    assert "configured" in result.stdout


def test_auth_status_single_provider_unknown_exits_2(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 6e W6 (P-152): explicit unknown provider → exit 2.

    Prior behavior silently reported ``not configured`` for any string,
    including typos. Now ``aelix auth status <unknown>`` validates
    against the OAuth registry ∪ stored entries and exits 2 with a
    ``Unknown provider: <id>`` diagnostic on stderr.
    """

    result = _run_cli(
        ["auth", "status", "totally-fictional-provider"], auth_path
    )
    assert result.returncode == 2
    assert "Unknown provider" in result.stderr
    assert "totally-fictional-provider" in result.stderr


def test_auth_status_known_oauth_provider_unconfigured(auth_path: Path) -> None:
    """A KNOWN OAuth provider without creds reports ``not configured``."""

    result = _run_cli(["auth", "status", "anthropic"], auth_path)
    assert result.returncode == 0, result.stderr
    assert "anthropic" in result.stdout
    assert "not configured" in result.stdout


# === aelix auth logout ===


def test_auth_logout_removes_entry(auth_path: Path) -> None:
    auth_path.write_text(
        json.dumps(
            {
                "anthropic": {"type": "api_key", "key": "sk-x"},
                "openai": {"type": "api_key", "key": "sk-y"},
            }
        )
    )
    result = _run_cli(["auth", "logout", "anthropic"], auth_path)
    assert result.returncode == 0, result.stderr
    assert "logged out" in result.stdout.lower()
    # Verify file content: anthropic gone, openai still present.
    on_disk = json.loads(auth_path.read_text())
    assert "anthropic" not in on_disk
    assert on_disk.get("openai", {}).get("key") == "sk-y"


# === Back-compat: top-level interactive/rpc still works ===


def test_auth_status_stored_non_oauth_provider_is_valid(auth_path: Path) -> None:
    """Sprint 6e W6 (P-152): a stored api_key for a non-OAuth provider id
    IS valid (Pi-parity ``known = registry ∪ stored``)."""

    auth_path.write_text(
        json.dumps({"my-custom-llm": {"type": "api_key", "key": "sk-x"}})
    )
    result = _run_cli(["auth", "status", "my-custom-llm"], auth_path)
    assert result.returncode == 0, result.stderr
    assert "my-custom-llm" in result.stdout
    assert "configured" in result.stdout


def test_auth_login_runtime_error_exits_1(auth_path: Path) -> None:
    """Sprint 6e W6 (n1): unknown provider → RuntimeError → exit 1 with
    a stderr diagnostic."""

    result = _run_cli(["auth", "login", "totally-fictional-provider"], auth_path)
    assert result.returncode == 1
    assert "Login failed" in result.stderr
    assert "Unknown OAuth provider" in result.stderr or "totally-fictional" in result.stderr


def test_no_subcommand_runs_interactive_demo(auth_path: Path) -> None:
    """Sprint 6d back-compat: bare ``aelix`` (no subcommand) runs the
    interactive demo. The mock stream prints the echoed message.
    """

    result = _run_cli([], auth_path)
    assert result.returncode == 0, result.stderr
    # The interactive mock prints the assistant's echoed text.
    assert "Aelix runtime is online" in result.stdout
