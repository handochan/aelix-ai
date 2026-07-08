"""Sprint 6c · Phase 4.3 — Auth storage tests."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from aelix_ai.oauth.auth_storage import AuthStorage, default_auth_path
from aelix_ai.oauth.types import OAuthCredentials


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    return tmp_path / "subdir" / "auth.json"


@pytest.fixture(autouse=True)
def _clear_auth_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_AUTH_PATH", raising=False)


# === Defaults ===


def test_default_auth_path_uses_aelix_root() -> None:
    """Aelix default: ``~/.config/aelix/agent/auth.json``."""

    p = default_auth_path()
    assert p.name == "auth.json"
    assert "aelix" in str(p)


def test_default_auth_path_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AELIX_AUTH_PATH", str(tmp_path / "custom.json"))
    p = default_auth_path()
    assert p == tmp_path / "custom.json"


# === Load / save round-trip ===


async def test_load_missing_file_yields_empty(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    assert await storage.get_api_key("anthropic") is None
    assert await storage.get_oauth("anthropic") is None


async def test_save_creates_dir_and_file(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    assert auth_path.exists()
    assert auth_path.parent.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
async def test_parent_dir_mode_0700(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    parent_mode = stat.S_IMODE(auth_path.parent.stat().st_mode)
    assert parent_mode == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
async def test_file_mode_0600(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    file_mode = stat.S_IMODE(auth_path.stat().st_mode)
    assert file_mode == 0o600


async def test_api_key_round_trip(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    assert await storage.get_api_key("anthropic") == "sk-test"

    # New instance reading same path.
    storage2 = AuthStorage(auth_path)
    assert await storage2.get_api_key("anthropic") == "sk-test"


async def test_oauth_round_trip(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    creds = OAuthCredentials(
        refresh="rt-1",
        access="at-1",
        expires=int(time.time() * 1000) + 3_600_000,
        extra={"scope": "openid"},
    )
    await storage.set_oauth("anthropic", creds)

    storage2 = AuthStorage(auth_path)
    rebuilt = await storage2.get_oauth("anthropic")
    assert rebuilt is not None
    assert rebuilt.refresh == "rt-1"
    assert rebuilt.access == "at-1"
    assert rebuilt.expires == creds.expires
    assert rebuilt.extra == {"scope": "openid"}


async def test_file_schema_matches_pi(auth_path: Path) -> None:
    """Pi parity: stored as ``{type: 'oauth', refresh, access, expires, ...}``."""

    storage = AuthStorage(auth_path)
    creds = OAuthCredentials(refresh="r", access="a", expires=12345)
    await storage.set_oauth("anthropic", creds)

    raw = json.loads(auth_path.read_text())
    assert raw["anthropic"] == {
        "type": "oauth",
        "refresh": "r",
        "access": "a",
        "expires": 12345,
    }


async def test_api_key_file_schema_matches_pi(auth_path: Path) -> None:
    """Pi parity: api_key shape ``{type: 'api_key', key: ...}``."""

    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    raw = json.loads(auth_path.read_text())
    assert raw["anthropic"] == {"type": "api_key", "key": "sk-test"}


async def test_remove_drops_entry(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    await storage.set_api_key("openai", "sk-openai")
    await storage.remove("anthropic")

    assert await storage.get_api_key("anthropic") is None
    assert await storage.get_api_key("openai") == "sk-openai"


async def test_logout_clears_runtime_override(auth_path: Path) -> None:
    """``logout`` clears the in-memory runtime override too — not just the
    persisted entry. ``has_configured_auth`` checks the runtime override FIRST,
    so leaving it behind kept a logged-out provider authorized (its models never
    left ``/model``). Regression for the "/logout didn't remove" report."""

    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-stored")
    storage.set_runtime_api_key("anthropic", "sk-runtime")

    await storage.logout("anthropic")

    assert not storage.has("anthropic")  # persisted entry gone
    assert "anthropic" not in storage._runtime_overrides  # runtime override cleared


async def test_remove_keeps_runtime_override(auth_path: Path) -> None:
    """``remove`` is the low-level persisted-drop primitive ``logout`` builds
    on; it stays pure (persisted entry only) so clearing the runtime override is
    a ``logout``-only concern."""

    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-stored")
    storage.set_runtime_api_key("openai", "sk-runtime")

    await storage.remove("openai")

    assert not storage.has("openai")  # persisted entry gone
    assert "openai" in storage._runtime_overrides  # runtime override untouched


async def test_get_api_key_with_oauth_entry_returns_none(auth_path: Path) -> None:
    """``get_api_key`` only returns api_key entries (Pi parity discriminator)."""

    storage = AuthStorage(auth_path)
    creds = OAuthCredentials(refresh="r", access="a", expires=int(time.time() * 1000) + 100_000)
    await storage.set_oauth("anthropic", creds)
    assert await storage.get_api_key("anthropic") is None


async def test_get_oauth_with_api_key_entry_returns_none(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-test")
    assert await storage.get_oauth("anthropic") is None


# === get_oauth_api_key auto-refresh ===


async def test_get_oauth_api_key_fresh_token_no_refresh(auth_path: Path) -> None:
    """Fresh token (not expired) → return access verbatim, no refresh call."""

    storage = AuthStorage(auth_path)
    creds = OAuthCredentials(
        refresh="rt-fresh",
        access="at-fresh",
        expires=int(time.time() * 1000) + 3_600_000,
    )
    await storage.set_oauth("anthropic", creds)

    with patch(
        "aelix_ai.oauth._high_level.get_oauth_provider"
    ) as mock_get:
        # We should NOT reach the refresh path.
        key = await storage.get_oauth_api_key("anthropic")
        assert key == "at-fresh"
        mock_get.assert_not_called()


async def test_get_oauth_api_key_expired_triggers_refresh(auth_path: Path) -> None:
    """Expired token → refresh + persist + return new access."""

    storage = AuthStorage(auth_path)
    expired_creds = OAuthCredentials(
        refresh="rt-old",
        access="at-old",
        expires=int(time.time() * 1000) - 10_000,  # already expired
    )
    await storage.set_oauth("anthropic", expired_creds)

    fresh_creds = OAuthCredentials(
        refresh="rt-new",
        access="at-new",
        expires=int(time.time() * 1000) + 3_600_000,
    )

    class _FakeProvider:
        id = "anthropic"

        async def refresh_token(self, creds: OAuthCredentials) -> OAuthCredentials:
            return fresh_creds

        def get_api_key(self, creds: OAuthCredentials) -> str:
            return creds.access

    with patch(
        "aelix_ai.oauth._high_level.get_oauth_provider",
        return_value=_FakeProvider(),
    ):
        key = await storage.get_oauth_api_key("anthropic")
    assert key == "at-new"

    # Persisted on disk.
    raw = json.loads(auth_path.read_text())
    assert raw["anthropic"]["refresh"] == "rt-new"
    assert raw["anthropic"]["access"] == "at-new"


async def test_get_oauth_api_key_no_credentials(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    assert await storage.get_oauth_api_key("anthropic") is None


# === Concurrency ===


async def test_in_process_lock_serializes_writes(auth_path: Path) -> None:
    """Concurrent ``set_api_key`` calls produce a coherent final state."""

    storage = AuthStorage(auth_path)

    async def writer(i: int) -> None:
        await storage.set_api_key(f"provider-{i}", f"sk-{i}")

    await asyncio.gather(*(writer(i) for i in range(10)))

    for i in range(10):
        assert await storage.get_api_key(f"provider-{i}") == f"sk-{i}"


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl POSIX-only")
async def test_cross_process_flock_acquired(auth_path: Path, tmp_path: Path) -> None:
    """The cross-process flock is acquired without raising on POSIX."""

    storage = AuthStorage(auth_path)
    # First write creates the file; second write acquires + releases lock.
    await storage.set_api_key("anthropic", "sk-1")
    await storage.set_api_key("anthropic", "sk-2")
    assert await storage.get_api_key("anthropic") == "sk-2"


async def test_corrupted_json_resets_to_empty(auth_path: Path) -> None:
    """Corrupted ``auth.json`` is treated as empty (logged warning)."""

    auth_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    auth_path.write_text("not valid json {{{")
    auth_path.chmod(0o600)

    storage = AuthStorage(auth_path)
    await storage.load()
    assert await storage.get_api_key("anthropic") is None


# === W4 M1 — atomic write ===


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX rename only")
async def test_auth_storage_atomic_write_survives_simulated_crash(
    auth_path: Path,
) -> None:
    """W4 M1: writing via ``<path>.tmp`` + fsync + rename means a crash
    during fsync leaves the ORIGINAL ``auth.json`` intact (never half-
    written). Simulate by patching ``os.fsync`` to raise after the
    first successful write.
    """

    import os as _os

    # First write succeeds normally.
    storage = AuthStorage(auth_path)
    await storage.set_api_key("anthropic", "sk-good-1")
    original_contents = auth_path.read_text()
    assert "sk-good-1" in original_contents

    # Second write — patch os.fsync to raise so the .tmp file never
    # gets renamed into place.
    storage2 = AuthStorage(auth_path)
    real_fsync = _os.fsync

    def _crash_fsync(fd: int) -> None:
        real_fsync(fd)
        raise OSError("simulated crash")

    with patch(
        "aelix_ai.oauth.auth_storage.os.fsync", side_effect=_crash_fsync
    ), pytest.raises(OSError, match="simulated crash"):
        await storage2.set_api_key("anthropic", "sk-bad-2")

    # auth.json must STILL contain sk-good-1, never sk-bad-2 — no
    # partial write reached the canonical path.
    after = auth_path.read_text()
    assert after == original_contents
    assert "sk-good-1" in after
    assert "sk-bad-2" not in after


# === W4 m9 — XDG_CONFIG_HOME compliance ===


def test_default_auth_path_honors_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """W4 m9: ``$XDG_CONFIG_HOME`` overrides ``~/.config``."""

    monkeypatch.delenv("AELIX_AUTH_PATH", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = default_auth_path()
    assert p == tmp_path / "xdg" / "aelix" / "agent" / "auth.json"


def test_default_auth_path_falls_back_when_xdg_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W4 m9: with ``XDG_CONFIG_HOME`` unset, falls back to ``~/.config``."""

    monkeypatch.delenv("AELIX_AUTH_PATH", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    p = default_auth_path()
    assert ".config" in str(p)
    assert "aelix" in str(p)
    assert p.name == "auth.json"
