"""rg / fd binary auto-download — Pi parity ``utils/tools-manager.ts``.

P0 #3 HEAVY (ADR-0139). Port of Pi
``packages/coding-agent/src/utils/tools-manager.ts`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Ensures ``ripgrep`` (``rg``) and ``fd`` are available — preferring a
system-PATH copy, else a previously-downloaded copy under
:func:`aelix_coding_agent.cli.config.get_bin_dir`, else downloading the
platform/arch-matched GitHub release archive, extracting the binary and
``chmod``-ing it executable. With rg/fd guaranteed, ``grep``/``find`` honor
``.gitignore`` (pi parity).

Aelix-additive divergences (documented):

- Archive extraction uses Python stdlib (:mod:`tarfile` with the
  ``filter="data"`` safe extractor / :mod:`zipfile`) instead of shelling out
  to ``tar`` / ``unzip`` / ``powershell`` (more portable, no system-tar
  dependency, path-traversal-safe).
- Network download uses :mod:`urllib.request` instead of ``fetch``.
- Android/Termux detection is best-effort (Python cannot read Node's
  ``os.platform() === "android"`` directly).
- Offline mode reads ``PI_OFFLINE`` (the env name Aelix keeps — see
  ``cli/entry.py``).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform as _platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_NETWORK_TIMEOUT_S = 10
_DOWNLOAD_TIMEOUT_S = 120

# Pi parity: ``APP_NAME`` used in the GitHub API User-Agent header.
_USER_AGENT = "aelix-coding-agent"


@dataclass(frozen=True)
class _ToolConfig:
    """Pi parity ``ToolConfig`` (``tools-manager.ts:20-27``)."""

    name: str
    repo: str
    binary_name: str
    tag_prefix: str
    get_asset_name: Callable[[str, str, str], str | None]
    system_binary_names: tuple[str, ...] = ()


def _fd_asset_name(version: str, plat: str, arch: str) -> str | None:
    """Pi parity ``TOOLS.fd.getAssetName`` (``tools-manager.ts:36-47``)."""

    arch_str = "aarch64" if arch == "arm64" else "x86_64"
    if plat == "darwin":
        return f"fd-v{version}-{arch_str}-apple-darwin.tar.gz"
    if plat == "linux":
        return f"fd-v{version}-{arch_str}-unknown-linux-gnu.tar.gz"
    if plat == "win32":
        return f"fd-v{version}-{arch_str}-pc-windows-msvc.zip"
    return None


def _rg_asset_name(version: str, plat: str, arch: str) -> str | None:
    """Pi parity ``TOOLS.rg.getAssetName`` (``tools-manager.ts:55-68``)."""

    arch_str = "aarch64" if arch == "arm64" else "x86_64"
    if plat == "darwin":
        return f"ripgrep-{version}-{arch_str}-apple-darwin.tar.gz"
    if plat == "linux":
        if arch == "arm64":
            return f"ripgrep-{version}-aarch64-unknown-linux-gnu.tar.gz"
        return f"ripgrep-{version}-x86_64-unknown-linux-musl.tar.gz"
    if plat == "win32":
        return f"ripgrep-{version}-{arch_str}-pc-windows-msvc.zip"
    return None


# Pi parity ``TOOLS`` (``tools-manager.ts:29-71``).
_TOOLS: dict[str, _ToolConfig] = {
    "fd": _ToolConfig(
        name="fd",
        repo="sharkdp/fd",
        binary_name="fd",
        system_binary_names=("fd", "fdfind"),
        tag_prefix="v",
        get_asset_name=_fd_asset_name,
    ),
    "rg": _ToolConfig(
        name="ripgrep",
        repo="BurntSushi/ripgrep",
        binary_name="rg",
        tag_prefix="",
        get_asset_name=_rg_asset_name,
    ),
}

# Pi parity ``TERMUX_PACKAGES`` (``tools-manager.ts:319-322``).
_TERMUX_PACKAGES: dict[str, str] = {"fd": "fd", "rg": "ripgrep"}


def _node_platform() -> str:
    """Map :func:`platform.system` onto Node's ``os.platform()`` values."""

    system = _platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "windows":
        return "win32"
    if system == "linux":
        return "linux"
    return system


def _node_arch() -> str:
    """Map :func:`platform.machine` onto Node's ``os.arch()`` ``arm64``/``x64``."""

    machine = _platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x64"


def _is_android() -> bool:
    """Best-effort Android/Termux detection (Pi ``platform() === "android"``)."""

    if sys.platform == "android":  # Python 3.13+
        return True
    return hasattr(sys, "getandroidapilevel")


def _is_offline() -> bool:
    """Pi parity ``isOfflineModeEnabled`` (``tools-manager.ts:14-18``).

    Reads ``PI_OFFLINE`` (the env name Aelix keeps); truthy when ``1`` /
    ``true`` / ``yes`` (case-insensitive).
    """

    value = os.environ.get("PI_OFFLINE")
    if not value:
        return False
    return value == "1" or value.lower() in ("true", "yes")


def _bin_dir() -> str:
    # Lazy import — ``cli.config`` is behind a heavy ``cli/__init__`` that
    # imports ``tools.bash``; importing it at module load would cycle.
    from aelix_coding_agent.cli.config import get_bin_dir

    return get_bin_dir()


def _command_exists(cmd: str) -> bool:
    """Pi parity ``commandExists`` (``tools-manager.ts:74-82``).

    True when ``cmd --version`` can be spawned (the binary resolves on PATH);
    the exit status is irrelevant — only an ENOENT-equivalent (spawn failure)
    makes this ``False``.
    """

    try:
        subprocess.run(  # noqa: S603
            [cmd, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    return True


def get_tool_path(tool: str) -> str | None:
    """Pi parity ``getToolPath`` (``tools-manager.ts:85-104``).

    Returns the local bin-dir binary if present, else the first
    system-PATH binary name that resolves, else ``None``.
    """

    config = _TOOLS.get(tool)
    if config is None:
        return None

    ext = ".exe" if _node_platform() == "win32" else ""
    local_path = Path(_bin_dir()) / (config.binary_name + ext)
    if local_path.exists():
        return str(local_path)

    system_names = config.system_binary_names or (config.binary_name,)
    for name in system_names:
        if _command_exists(name):
            return name
    return None


def _get_latest_version(repo: str) -> str:
    """Pi parity ``getLatestVersion`` (``tools-manager.ts:107-119``).

    Fetches the latest release ``tag_name`` from the GitHub API and strips a
    leading ``v``.
    """

    req = urllib.request.Request(  # noqa: S310 — fixed https GitHub API host
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=_NETWORK_TIMEOUT_S) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"GitHub API error: {resp.status}")
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data["tag_name"])
    return tag[1:] if tag.startswith("v") else tag


def _download_file(url: str, dest: Path) -> None:
    """Pi parity ``downloadFile`` (``tools-manager.ts:122-137``)."""

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"Failed to download: {resp.status}")
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)


def _find_binary_recursively(root: Path, binary_file_name: str) -> Path | None:
    """Pi parity ``findBinaryRecursively`` (``tools-manager.ts:139-159``)."""

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.name == binary_file_name:
                return entry
            if entry.is_dir():
                stack.append(entry)
    return None


def _extract_archive(archive_path: Path, extract_dir: Path, asset_name: str) -> None:
    """Pi parity ``extractTarGzArchive`` / ``extractZipArchive``.

    Aelix uses Python stdlib with path-traversal-safe extraction
    (:data:`tarfile.data_filter`) instead of shelling out to ``tar``/``unzip``.
    """

    if asset_name.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            # ``filter="data"`` (Python 3.12+) blocks absolute paths, ``..``
            # traversal, device files, and unsafe permission/ownership bits.
            tf.extractall(extract_dir, filter="data")  # noqa: S202 — filtered
    elif asset_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            dest_root = extract_dir.resolve()
            for member in zf.namelist():
                # Reject absolute paths and parent-dir traversal using a true
                # path-boundary check (``is_relative_to``) — a string ``startswith``
                # would accept a sibling dir sharing the prefix.
                resolved = (extract_dir / member).resolve()
                if not resolved.is_relative_to(dest_root):
                    raise RuntimeError(f"Unsafe archive member: {member}")
            zf.extractall(extract_dir)  # noqa: S202 — members validated above
    else:
        raise RuntimeError(f"Unsupported archive format: {asset_name}")


def _download_tool(tool: str) -> str:
    """Pi parity ``downloadTool`` (``tools-manager.ts:241-316``). Blocking."""

    config = _TOOLS.get(tool)
    if config is None:
        raise RuntimeError(f"Unknown tool: {tool}")

    plat = _node_platform()
    arch = _node_arch()

    version = _get_latest_version(config.repo)
    # Pi parity special-case (``tools-manager.ts:250-252``).
    if tool == "fd" and plat == "darwin" and arch == "x64":
        version = "10.3.0"

    asset_name = config.get_asset_name(version, plat, arch)
    if not asset_name:
        raise RuntimeError(f"Unsupported platform: {plat}/{arch}")

    bin_dir = Path(_bin_dir())
    bin_dir.mkdir(parents=True, exist_ok=True)

    download_url = (
        f"https://github.com/{config.repo}/releases/download/"
        f"{config.tag_prefix}{version}/{asset_name}"
    )
    archive_path = bin_dir / asset_name
    binary_ext = ".exe" if plat == "win32" else ""
    binary_path = bin_dir / (config.binary_name + binary_ext)

    _download_file(download_url, archive_path)

    # Pi parity: unique temp extract dir (fd + rg may download concurrently,
    # and two parallel first-time greps could race the same tool). pid + a
    # random token mirror Pi's ``${pid}_${Date.now()}_${random}`` suffix.
    extract_dir = (
        bin_dir / f"extract_tmp_{config.binary_name}_{os.getpid()}_{uuid.uuid4().hex}"
    )
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        _extract_archive(archive_path, extract_dir, asset_name)

        binary_file_name = config.binary_name + binary_ext
        nested = extract_dir / asset_name
        for suffix in (".tar.gz", ".zip"):
            if str(nested).endswith(suffix):
                nested = Path(str(nested)[: -len(suffix)])
                break
        candidates = [nested / binary_file_name, extract_dir / binary_file_name]
        extracted = next((c for c in candidates if c.exists()), None)
        if extracted is None:
            extracted = _find_binary_recursively(extract_dir, binary_file_name)

        if extracted is None:
            raise RuntimeError(
                f"Binary not found in archive: expected {binary_file_name} "
                f"under {extract_dir}"
            )

        # Pi parity ``renameSync`` — replace any stale binary.
        if binary_path.exists():
            binary_path.unlink()
        shutil.move(str(extracted), str(binary_path))

        if plat != "win32":
            binary_path.chmod(
                stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
            )
    finally:
        archive_path.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)

    return str(binary_path)


async def ensure_tool(tool: str, silent: bool = True) -> str | None:
    """Pi parity ``ensureTool`` (``tools-manager.ts:326-369``).

    Returns the path to ``tool`` (system, cached, or freshly downloaded), or
    ``None`` when it cannot be made available (unknown tool, offline mode,
    Android/Termux, or a download failure). The blocking download runs in a
    worker thread so the event loop stays responsive.
    """

    existing = get_tool_path(tool)
    if existing:
        return existing

    config = _TOOLS.get(tool)
    if config is None:
        return None

    if _is_offline():
        if not silent:
            print(f"{config.name} not found. Offline mode enabled, skipping download.")
        return None

    if _is_android():
        if not silent:
            pkg = _TERMUX_PACKAGES.get(tool, tool)
            print(f"{config.name} not found. Install with: pkg install {pkg}")
        return None

    if not silent:
        print(f"{config.name} not found. Downloading...")

    try:
        path = await asyncio.to_thread(_download_tool, tool)
    except Exception as exc:  # noqa: BLE001 — download is best-effort
        if not silent:
            print(f"Failed to download {config.name}: {exc}")
        return None
    if not silent:
        print(f"{config.name} installed to {path}")
    return path


__all__ = ["ensure_tool", "get_tool_path"]
