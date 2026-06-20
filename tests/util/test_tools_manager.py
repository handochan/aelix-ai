"""P0 #3 HEAVY (ADR-0139) — ``tools_manager.ensure_tool`` rg/fd download tests.

Network I/O is mocked: ``_get_latest_version`` is stubbed and ``_download_file``
writes a locally-built archive, so the real extraction / binary-discovery /
``chmod`` / move logic is exercised end-to-end with zero network access.

Pi citation: ``utils/tools-manager.ts:1-369`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from aelix_coding_agent.util import tools_manager as tm


def _make_targz(dest: Path, arcname: str, content: bytes) -> None:
    with tarfile.open(dest, "w:gz") as tf:
        info = tarfile.TarInfo(name=arcname)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))


def _make_zip(dest: Path, arcname: str, content: bytes) -> None:
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(arcname, content)


# --- asset-name platform/arch matrix (Pi parity getAssetName) ---------------


def test_rg_asset_name_matrix():
    assert (
        tm._rg_asset_name("14.1.0", "linux", "x64")
        == "ripgrep-14.1.0-x86_64-unknown-linux-musl.tar.gz"
    )
    assert (
        tm._rg_asset_name("14.1.0", "linux", "arm64")
        == "ripgrep-14.1.0-aarch64-unknown-linux-gnu.tar.gz"
    )
    assert (
        tm._rg_asset_name("14.1.0", "darwin", "arm64")
        == "ripgrep-14.1.0-aarch64-apple-darwin.tar.gz"
    )
    assert (
        tm._rg_asset_name("14.1.0", "win32", "x64")
        == "ripgrep-14.1.0-x86_64-pc-windows-msvc.zip"
    )
    assert tm._rg_asset_name("14.1.0", "sunos", "x64") is None


def test_fd_asset_name_matrix():
    assert (
        tm._fd_asset_name("10.2.0", "linux", "x64")
        == "fd-v10.2.0-x86_64-unknown-linux-gnu.tar.gz"
    )
    assert (
        tm._fd_asset_name("10.2.0", "darwin", "arm64")
        == "fd-v10.2.0-aarch64-apple-darwin.tar.gz"
    )
    assert (
        tm._fd_asset_name("10.2.0", "win32", "arm64")
        == "fd-v10.2.0-aarch64-pc-windows-msvc.zip"
    )
    assert tm._fd_asset_name("10.2.0", "plan9", "x64") is None


# --- offline detection ------------------------------------------------------


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True),
    ("0", False), ("", False), ("nope", False),
])
def test_is_offline(monkeypatch, val, expected):
    if val == "":
        monkeypatch.delenv("PI_OFFLINE", raising=False)
    else:
        monkeypatch.setenv("PI_OFFLINE", val)
    assert tm._is_offline() is expected


# --- get_tool_path ----------------------------------------------------------


def test_get_tool_path_unknown_tool():
    assert tm.get_tool_path("notatool") is None


def test_get_tool_path_local_bin_dir(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "rg").write_text("binary")
    monkeypatch.setattr(tm, "_bin_dir", lambda: str(bin_dir))
    monkeypatch.setattr(tm, "_node_platform", lambda: "linux")
    assert tm.get_tool_path("rg") == str(bin_dir / "rg")


def test_get_tool_path_system_binary(tmp_path, monkeypatch):
    # Empty bin dir; "fd" resolves via the fdfind system alias.
    monkeypatch.setattr(tm, "_bin_dir", lambda: str(tmp_path))
    monkeypatch.setattr(tm, "_node_platform", lambda: "linux")
    monkeypatch.setattr(tm, "_command_exists", lambda c: c == "fdfind")
    assert tm.get_tool_path("fd") == "fdfind"


def test_get_tool_path_none(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "_bin_dir", lambda: str(tmp_path))
    monkeypatch.setattr(tm, "_node_platform", lambda: "linux")
    monkeypatch.setattr(tm, "_command_exists", lambda c: False)
    assert tm.get_tool_path("rg") is None


# --- ensure_tool short-circuits ---------------------------------------------


async def test_ensure_tool_returns_existing(monkeypatch):
    monkeypatch.setattr(tm, "get_tool_path", lambda t: "/usr/bin/rg")
    assert await tm.ensure_tool("rg") == "/usr/bin/rg"


async def test_ensure_tool_unknown(monkeypatch):
    monkeypatch.setattr(tm, "get_tool_path", lambda t: None)
    assert await tm.ensure_tool("nope") is None


async def test_ensure_tool_offline_no_download(monkeypatch):
    monkeypatch.setattr(tm, "get_tool_path", lambda t: None)
    monkeypatch.setenv("PI_OFFLINE", "1")

    def _boom(_tool):
        raise AssertionError("download must not run when offline")

    monkeypatch.setattr(tm, "_download_tool", _boom)
    assert await tm.ensure_tool("rg") is None


async def test_ensure_tool_android_no_download(monkeypatch):
    monkeypatch.setattr(tm, "get_tool_path", lambda t: None)
    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.setattr(tm, "_is_android", lambda: True)

    def _boom(_tool):
        raise AssertionError("download must not run on android")

    monkeypatch.setattr(tm, "_download_tool", _boom)
    assert await tm.ensure_tool("fd") is None


async def test_ensure_tool_download_failure_returns_none(monkeypatch):
    monkeypatch.setattr(tm, "get_tool_path", lambda t: None)
    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.setattr(tm, "_is_android", lambda: False)

    def _fail(_tool):
        raise RuntimeError("network down")

    monkeypatch.setattr(tm, "_download_tool", _fail)
    assert await tm.ensure_tool("rg") is None


# --- end-to-end download (mocked network) -----------------------------------


def _setup_download(monkeypatch, tmp_path, *, asset_layout: str, archive: str):
    """Wire a deterministic linux/x64 rg download to a locally-built archive."""

    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(tm, "_bin_dir", lambda: str(bin_dir))
    monkeypatch.setattr(tm, "_node_platform", lambda: "linux")
    monkeypatch.setattr(tm, "_node_arch", lambda: "x64")
    monkeypatch.setattr(tm, "_command_exists", lambda c: False)
    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.setattr(tm, "_is_android", lambda: False)
    monkeypatch.setattr(tm, "_get_latest_version", lambda repo: "14.1.0")

    content = b"#!/bin/sh\necho fake-rg\n"

    def _fake_download(url: str, dest: Path) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if archive == "tar":
            _make_targz(Path(dest), asset_layout, content)
        else:
            _make_zip(Path(dest), asset_layout, content)

    monkeypatch.setattr(tm, "_download_file", _fake_download)
    return bin_dir, content


async def test_ensure_tool_download_nested_targz(monkeypatch, tmp_path):
    # Real ripgrep layout: binary nested under the versioned dir.
    layout = "ripgrep-14.1.0-x86_64-unknown-linux-musl/rg"
    bin_dir, content = _setup_download(
        monkeypatch, tmp_path, asset_layout=layout, archive="tar"
    )
    path = await tm.ensure_tool("rg")
    assert path == str(bin_dir / "rg")
    assert Path(path).read_bytes() == content
    # chmod 755 (Pi parity make-executable).
    assert os.stat(path).st_mode & stat.S_IXUSR


async def test_ensure_tool_download_recursive_discovery(monkeypatch, tmp_path):
    # Binary buried in an unexpected subdir → recursive search finds it.
    layout = "weird/deeper/rg"
    bin_dir, content = _setup_download(
        monkeypatch, tmp_path, asset_layout=layout, archive="tar"
    )
    path = await tm.ensure_tool("rg")
    assert path == str(bin_dir / "rg")
    assert Path(path).read_bytes() == content


async def test_ensure_tool_download_binary_missing_returns_none(monkeypatch, tmp_path):
    # Archive contains no rg binary → download fails → ensure_tool returns None.
    layout = "ripgrep-14.1.0-x86_64-unknown-linux-musl/NOTRG"
    _setup_download(monkeypatch, tmp_path, asset_layout=layout, archive="tar")
    assert await tm.ensure_tool("rg") is None


# --- extraction safety ------------------------------------------------------


def test_extract_zip_rejects_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", b"pwned")
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()
    with pytest.raises(RuntimeError, match="Unsafe archive member"):
        tm._extract_archive(archive, extract_dir, "evil.zip")


def test_extract_unsupported_format(tmp_path):
    archive = tmp_path / "thing.rar"
    archive.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="Unsupported archive format"):
        tm._extract_archive(archive, tmp_path, "thing.rar")


def test_extract_zip_rejects_sibling_prefix(tmp_path):
    """ADR-0139 review (MINOR security): a member that resolves to a SIBLING
    directory sharing the extract-dir's name prefix must be rejected. The old
    ``str.startswith`` check accepted it; ``is_relative_to`` rejects it."""

    extract_dir = tmp_path / "extract_abc"
    extract_dir.mkdir()
    archive = tmp_path / "sib.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../extract_abc_evil/payload", b"pwned")
    with pytest.raises(RuntimeError, match="Unsafe archive member"):
        tm._extract_archive(archive, extract_dir, "sib.zip")
    # The sibling dir must NOT have been created.
    assert not (tmp_path / "extract_abc_evil").exists()


def test_extract_zip_accepts_legit_member(tmp_path):
    """Regression guard: a normal nested member still extracts after the
    containment-check tightening."""

    extract_dir = tmp_path / "out"
    extract_dir.mkdir()
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ripgrep-1.0/rg", b"binary")
    tm._extract_archive(archive, extract_dir, "ok.zip")
    assert (extract_dir / "ripgrep-1.0" / "rg").read_bytes() == b"binary"
