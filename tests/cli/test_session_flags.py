"""P0 #5 (ADR-0141) — CLI session/export flag consumption tests.

Covers the flags that were parsed-but-inert before this sprint:
``--session-dir``, ``--session``, ``--fork``, ``--export`` (now wired), and
the honest deferred diagnostics for ``--api-key`` / ``--models``.

Pi citation: ``packages/coding-agent/src/main.ts`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` (``resolveSessionPath`` /
``SessionManager.open`` / ``forkFrom`` / ``exportFromFile``).
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
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.storage import JsonlSessionMetadata, SessionError
from aelix_ai.messages import TextContent, UserMessage
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import (
    _async_main,
    _build_session,
    _resolve_session_metadata,
    _run_export,
)


async def _make_session(
    root: Path, cwd: str, *, text: str = "hello world"
) -> tuple[LocalFileSystem, JsonlSessionRepo, JsonlSessionMetadata]:
    """Create a persisted session with one user message under ``root``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(root))
    session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    await session.append_message(UserMessage(content=[TextContent(text=text)]))
    meta = await session.get_metadata()
    return fs, repo, meta


class _FakePipedStdin:
    """Non-tty stdin so ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


# === _resolve_session_metadata ================================================


async def test_resolve_by_path(tmp_path: Path) -> None:
    fs, repo, meta = await _make_session(tmp_path / "r", str(tmp_path))
    resolved = await _resolve_session_metadata(repo, fs, meta.path, str(tmp_path))
    assert resolved is not None
    assert resolved.id == meta.id


async def test_resolve_by_id_prefix(tmp_path: Path) -> None:
    fs, repo, meta = await _make_session(tmp_path / "r", str(tmp_path))
    resolved = await _resolve_session_metadata(
        repo, fs, meta.id[:8], str(tmp_path)
    )
    assert resolved is not None
    assert resolved.id == meta.id


async def test_resolve_id_not_found(tmp_path: Path) -> None:
    fs, repo, _ = await _make_session(tmp_path / "r", str(tmp_path))
    resolved = await _resolve_session_metadata(
        repo, fs, "zzz-no-such-id", str(tmp_path)
    )
    assert resolved is None


# === _build_session ===========================================================


async def test_build_session_no_session_is_in_memory(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "r"))
    s = await _build_session(Args(no_session=True), repo, fs, str(tmp_path))
    assert isinstance(s._storage, MemorySessionStorage)


async def test_build_session_opens_by_path(tmp_path: Path) -> None:
    fs, repo, meta = await _make_session(
        tmp_path / "r", str(tmp_path), text="LOADED-CONTENT"
    )
    # Open with a cwd DIFFERENT from the seed cwd so the cwd_override seam
    # (entry.py ``repo.open(meta, cwd_override=cwd)``) has teeth.
    override = str(tmp_path / "elsewhere")
    s = await _build_session(Args(session=meta.path), repo, fs, override)
    sm = await s.get_metadata()
    assert sm.id == meta.id
    assert sm.cwd == override
    # The seeded message actually loaded (not just a same-id empty session).
    ctx = await s.build_context()
    assert any(
        getattr(c, "text", "") == "LOADED-CONTENT"
        for m in ctx.messages
        for c in getattr(m, "content", [])
    )


async def test_build_session_opens_by_id(tmp_path: Path) -> None:
    fs, repo, meta = await _make_session(tmp_path / "r", str(tmp_path))
    s = await _build_session(Args(session=meta.id), repo, fs, str(tmp_path))
    sm = await s.get_metadata()
    assert sm.id == meta.id


async def test_build_session_fork_creates_new_with_lineage(
    tmp_path: Path,
) -> None:
    fs, repo, meta = await _make_session(tmp_path / "r", str(tmp_path))
    s = await _build_session(Args(fork=meta.path), repo, fs, str(tmp_path))
    sm = await s.get_metadata()
    assert sm.id != meta.id
    assert sm.parent_session_path == meta.path


async def test_build_session_not_found_raises(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "r"))
    with pytest.raises(SessionError):
        await _build_session(
            Args(session="no-such-id"), repo, fs, str(tmp_path)
        )


async def test_build_session_default_create(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "r"))
    s = await _build_session(Args(), repo, fs, str(tmp_path))
    sm = await s.get_metadata()
    # Persisted under the configured root.
    assert str(tmp_path / "r") in sm.path


# === _run_export ==============================================================


async def test_run_export_renders_messages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fs, repo, meta = await _make_session(
        tmp_path / "r", str(tmp_path), text="EXPORTME-UNIQUE"
    )
    out = tmp_path / "out.html"
    code = await _run_export(
        Args(export=meta.path, messages=[str(out)]), repo, fs
    )
    assert code == 0
    assert out.exists()
    content = out.read_text()
    assert "<html" in content.lower()
    assert "EXPORTME-UNIQUE" in content
    assert str(out.resolve()) in capsys.readouterr().out


async def test_run_export_default_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs, repo, meta = await _make_session(tmp_path / "r", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    code = await _run_export(Args(export=meta.path, messages=[]), repo, fs)
    assert code == 0
    # Default name ``aelix-session-<basename>.html`` in cwd.
    produced = list(tmp_path.glob("aelix-session-*.html"))
    assert len(produced) == 1


# === --export end-to-end (flag routing) =======================================


async def test_export_flag_e2e(tmp_path: Path) -> None:
    fs, repo, meta = await _make_session(
        tmp_path / "r", str(tmp_path), text="E2E-EXPORT"
    )
    out = tmp_path / "e2e.html"
    code = await _async_main(["--export", meta.path, str(out)])
    assert code == 0
    assert out.exists()
    assert "E2E-EXPORT" in out.read_text()


async def test_export_bad_path_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await _async_main(["--export", str(tmp_path / "missing.jsonl")])
    captured = capsys.readouterr()
    assert code == 1
    assert "Error" in captured.err


# === --session-dir / deferred-flag diagnostics (e2e) ==========================


async def test_session_dir_roots_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--session-dir`` roots the repo; a session file lands under it
    (persisted before the harness runs, regardless of exit code)."""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "sroot"
    code = await _async_main(["--session-dir", str(root), "--print"])
    assert code in (0, 1)
    assert any(root.rglob("*.jsonl"))


async def test_api_key_without_provider_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 #7 (ITEM 6): ``--api-key`` with no resolvable provider is a Pi-shape
    error (``main.ts:574-582``) + exit 1, and never echoes the key value."""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Clear any ambient OPENROUTER_API_KEY so resolve_model can't infer a
    # provider from env (which would otherwise pass the requires-model gate).
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secret = "sk-LEAKCANARY-9z7q"
    code = await _async_main(["--api-key", secret, "--print"])
    err = capsys.readouterr().err
    assert code == 1
    assert "--api-key requires a model" in err
    # SECURITY: the error must NOT echo the key value.
    assert secret not in err


async def test_models_emits_deferred_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    await _async_main(["--models", "claude-*,gpt-*", "--print"])
    assert "--models" in capsys.readouterr().err


async def test_session_not_found_e2e_exits_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code = await _async_main(["--session", "zzz-no-such-id", "--print"])
    assert code == 1
    err = capsys.readouterr().err
    assert "No session matching --session" in err
    assert "'zzz-no-such-id'" in err  # repr of the bad id


# === Review hardening (ADR-0141 adversarial review) ===========================


async def test_resolve_by_id_prefix_global_cross_cwd(tmp_path: Path) -> None:
    """Id resolution falls through to the GLOBAL (2nd) list scan when the
    session lives under a different cwd than the one being resolved from."""

    cwd_a = str(tmp_path / "a")
    cwd_b = str(tmp_path / "b")
    fs, repo, meta = await _make_session(tmp_path / "r", cwd_a)
    # cwd_b-local list is empty (sessions partition by encoded cwd); only the
    # global fallback can find the cwd_a session.
    resolved = await _resolve_session_metadata(repo, fs, meta.id[:8], cwd_b)
    assert resolved is not None
    assert resolved.id == meta.id


async def test_bare_filename_collision_resolves_as_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A separator-free, non-``.jsonl`` token that ALSO exists as a cwd file
    is still classified as a session-id (Pi-faithful: no existence check),
    so an unrelated cwd file is never mis-loaded as a session."""

    fs, repo, _ = await _make_session(tmp_path / "r", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notasession").write_text("garbage\n")
    # Goes to id resolution (no match) → None — NOT a path-load SessionError.
    resolved = await _resolve_session_metadata(
        repo, fs, "notasession", str(tmp_path)
    )
    assert resolved is None


async def test_session_dir_flag_wins_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli.config import ENV_SESSION_DIR

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env_root = tmp_path / "env_root"
    flag_root = tmp_path / "flag_root"
    monkeypatch.setenv(ENV_SESSION_DIR, str(env_root))
    code = await _async_main(["--session-dir", str(flag_root), "--print"])
    assert code in (0, 1)
    assert any(flag_root.rglob("*.jsonl"))
    assert not any(env_root.rglob("*.jsonl"))


async def test_session_dir_env_only_roots_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli.config import ENV_SESSION_DIR

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env_root = tmp_path / "env_root"
    monkeypatch.setenv(ENV_SESSION_DIR, str(env_root))
    code = await _async_main(["--print"])
    assert code in (0, 1)
    assert any(env_root.rglob("*.jsonl"))


async def test_continue_reopens_most_recent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--continue`` re-opens the most-recent session (no NEW file)."""

    root = tmp_path / "sroot"
    cwd = str(tmp_path)
    monkeypatch.chdir(cwd)
    await _make_session(root, cwd, text="RESUME-ME")
    before = set(root.rglob("*.jsonl"))
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    code = await _async_main(["--continue", "--session-dir", str(root)])
    assert code in (0, 1)
    # The existing session was re-opened — no new session file created.
    assert set(root.rglob("*.jsonl")) == before
