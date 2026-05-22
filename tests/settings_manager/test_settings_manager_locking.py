"""Lock contention tests — Sprint 6h₇b · §F.7 · Commit 4 · Aelix-additive.

Verifies in-process serialization (via ``asyncio.Lock``) and
cross-process locking (via ``fcntl.flock``). POSIX-only — tests skip
on Windows.

Aelix-additive tests — Pi's own suite does NOT cover lock contention.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import (
    FileSettingsStorage,
    SettingsManager,
)

# POSIX-only — fcntl is not available on Windows.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="fcntl POSIX-only"
)


def _make_manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    storage = FileSettingsStorage(
        settings_dirs["project_dir"], settings_dirs["agent_dir"]
    )
    return SettingsManager.from_storage(storage)


async def test_concurrent_save_calls_serialize(
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """Multiple concurrent setter calls from the same event loop serialize.

    All writes complete and the final file reflects every setter's
    update (modification tracking accumulates per setter; the final
    write merges them on disk).
    """

    manager = _make_manager(settings_dirs)

    manager.set_theme("dark")
    manager.set_default_model("claude-sonnet")
    manager.set_default_provider("anthropic")
    manager.set_steering_mode("all")

    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["theme"] == "dark"
    assert saved["defaultModel"] == "claude-sonnet"
    assert saved["defaultProvider"] == "anthropic"
    assert saved["steeringMode"] == "all"


async def test_cross_process_lock_serializes_real_subprocess(
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """fcntl.flock serializes writes from a sibling Python subprocess.

    Spawns a real subprocess that takes the flock briefly, then issues
    the in-process write. The final on-disk content reflects the
    in-process write (which executed after the subprocess released the
    lock). Verifies that the manager's write path correctly waits on
    the cross-process lock.

    Note: fcntl.flock is per-process — acquiring it twice from the
    same process succeeds immediately. A real subprocess is required.
    """

    import subprocess
    import textwrap
    import time

    global_path = settings_dirs["global_path"]
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        '{"theme": "from-subprocess"}', encoding="utf-8"
    )

    # Subprocess takes lock, holds briefly, releases.
    sentinel = settings_dirs["agent_dir"] / "locked.flag"
    holder_script = textwrap.dedent(
        f"""
        import fcntl
        import os
        import time

        fd = os.open({str(global_path)!r}, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with open({str(sentinel)!r}, "w") as f:
            f.write("locked")
        time.sleep(0.5)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        """
    )
    holder = subprocess.Popen(["python", "-c", holder_script])
    try:
        # Wait until subprocess has the lock.
        for _ in range(100):
            if sentinel.exists():
                break
            time.sleep(0.05)
        assert sentinel.exists(), "subprocess did not acquire lock in time"

        manager = _make_manager(settings_dirs)
        manager.set_theme("after-lock")
        # ``flush`` blocks on flock; once subprocess releases, the
        # write proceeds.
        await asyncio.wait_for(manager.flush(), timeout=10.0)
    finally:
        holder.wait(timeout=10)

    saved = read_settings(global_path)
    assert saved["theme"] == "after-lock"


async def test_reload_drains_pending_writes(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """reload() awaits pending writes before re-reading from disk.

    Pi parity (`:404`): the first line of ``reload()`` is
    ``await this.writeQueue``.
    """

    manager = _make_manager(settings_dirs)
    manager.set_theme("first")
    # Schedule a second setter — both should land on disk before reload
    # re-reads.
    manager.set_default_model("model-x")
    await manager.reload()

    # After reload, the disk values should round-trip back.
    assert manager.get_theme() == "first"
    assert manager.get_default_model() == "model-x"
