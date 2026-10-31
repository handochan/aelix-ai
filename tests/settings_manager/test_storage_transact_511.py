"""#511 (ADR-0279 D4; Codex C1, C2) — the settings transaction holds across processes.

``with_lock`` locks the settings file's inode, only when it exists, and
``os.replace`` gives the file a new inode on every write: a waiter can hold the
old inode while another writer locks the new one, and for a missing file ``fn``
ran before any lock. ``transact`` takes a stable sidecar lock first. These tests
run real processes against one file, existing and missing, and check that no
edit is lost; then the strict read and the exclusive temp write.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_ai.settings import storage as storage_mod
from aelix_ai.settings.storage import FileSettingsStorage

_WORKERS = 6
_EDITS = 8

_WORKER = textwrap.dedent(
    """
    import asyncio, sys, time
    from pathlib import Path
    from aelix_ai.settings import SettingsManager

    agent, project, go, tag, edits = sys.argv[1:6]
    while not Path(go).exists():
        time.sleep(0.005)

    async def main() -> None:
        for i in range(int(edits)):
            manager = SettingsManager.create(project, agent, project_trusted=True)
            await manager.edit_list("global", "skills", lambda e, i=i: [*e, f"-{tag}-{i}"])

    asyncio.run(main())
    """
)


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _run_workers(agent: Path, project: Path, tmp_path: Path) -> list[str]:
    go = tmp_path / "go"
    env = dict(os.environ)
    env.pop("AELIX_SETTINGS_PATH", None)
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _WORKER,
                str(agent),
                str(project),
                str(go),
                f"w{n}",
                str(_EDITS),
            ],
            env=env,
        )
        for n in range(_WORKERS)
    ]
    time.sleep(0.3)  # every worker is parked on the start file
    go.write_text("go")
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    return json.loads((agent / "settings.json").read_text())["skills"]


def test_concurrent_edits_of_an_existing_file_lose_nothing(tmp_path: Path) -> None:
    agent, project = tmp_path / "agent", tmp_path / "project"
    agent.mkdir()
    project.mkdir()
    (agent / "settings.json").write_text(json.dumps({"theme": "dark"}))
    entries = _run_workers(agent, project, tmp_path)
    assert sorted(entries) == sorted(f"-w{n}-{i}" for n in range(_WORKERS) for i in range(_EDITS))
    assert json.loads((agent / "settings.json").read_text())["theme"] == "dark"


def test_concurrent_edits_that_create_the_file_lose_nothing(tmp_path: Path) -> None:
    agent, project = tmp_path / "agent", tmp_path / "project"
    agent.mkdir()
    project.mkdir()
    entries = _run_workers(agent, project, tmp_path)
    assert len(entries) == _WORKERS * _EDITS


def test_the_sidecar_lock_lives_beside_the_global_file_for_both_scopes(tmp_path: Path) -> None:
    storage = FileSettingsStorage(tmp_path / "project", tmp_path / "agent")
    project_lock = storage._sidecar_path(storage.project_path)
    assert project_lock.parent == tmp_path / "agent" / "locks"
    assert storage._sidecar_path(storage.global_path) != project_lock
    storage.transact("project", lambda _c: None)
    assert not (tmp_path / "project" / ".aelix").exists()  # a no-op created nothing there


async def test_an_unreadable_file_raises_instead_of_reading_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    path = agent / "settings.json"
    path.write_text(json.dumps({"skills": ["-keep"], "theme": "dark"}))
    manager = SettingsManager.create(tmp_path, agent, project_trusted=True)
    real = Path.read_text

    def unreadable(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            raise PermissionError("probe: unreadable")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(PermissionError):
        await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    monkeypatch.setattr(Path, "read_text", real)
    assert json.loads(path.read_text()) == {"skills": ["-keep"], "theme": "dark"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
async def test_a_planted_temp_path_is_never_written_through(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    (agent / "settings.json.tmp").symlink_to(victim)  # the old predictable temp name
    manager = SettingsManager.create(tmp_path, agent, project_trusted=True)
    await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
    assert victim.read_text() == "untouched"
    assert json.loads((agent / "settings.json").read_text()) == {"skills": ["-x"]}
    leftovers = [
        p.name for p in agent.iterdir() if p.name.endswith(".tmp") and p.name != "settings.json.tmp"
    ]
    assert leftovers == []


async def test_a_short_write_still_writes_the_whole_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    real_write = os.write

    def dribble(fd: int, data: bytes | memoryview) -> int:
        return real_write(fd, bytes(data[:3]))

    monkeypatch.setattr(storage_mod.os, "write", dribble)
    manager = SettingsManager.create(tmp_path, agent, project_trusted=True)
    entries = [f"-skill-number-{i}" for i in range(20)]
    await manager.edit_list("global", "skills", lambda _e: list(entries))
    assert json.loads((agent / "settings.json").read_text()) == {"skills": entries}


async def test_setters_write_through_the_same_transaction(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    manager = SettingsManager.create(tmp_path, agent, project_trusted=True)
    manager.set_theme("dark")
    await manager.flush()
    assert (agent / "locks").is_dir()  # the setter took the sidecar lock too
    assert json.loads((agent / "settings.json").read_text()) == {"theme": "dark"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
async def test_a_symlinked_settings_file_keeps_its_link_and_its_lock(tmp_path: Path) -> None:
    """Codex X4: replacing the link changed the sidecar's identity after the first save."""

    agent = tmp_path / "agent"
    agent.mkdir()
    real = tmp_path / "dotfiles" / "aelix-settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"theme": "dark"}))
    (agent / "settings.json").symlink_to(real)
    storage = FileSettingsStorage(tmp_path, agent)
    lock_before = storage._sidecar_path(storage.target("global"))
    manager = SettingsManager.create(tmp_path, agent, project_trusted=True)
    await manager.edit_list("global", "skills", lambda e: [*e, "-a"])
    await manager.edit_list("global", "skills", lambda e: [*e, "-b"])
    assert (agent / "settings.json").is_symlink()
    assert json.loads(real.read_text()) == {"theme": "dark", "skills": ["-a", "-b"]}
    assert storage._sidecar_path(storage.target("global")) == lock_before


_HOLDER = textwrap.dedent(
    """
    import fcntl, os, sys, time
    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    open(sys.argv[2], "w").write("held")
    time.sleep(float(sys.argv[3]))
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="the holder uses fcntl")
async def test_a_held_lock_times_out_without_blocking_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V6: POSIX had no deadline, and the wait ran on the event loop (a 3.8 s gap measured)."""

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text("{}")
    storage = FileSettingsStorage(tmp_path, agent)
    lock = storage._sidecar_path(storage.target("global"))
    lock.parent.mkdir(parents=True, exist_ok=True)
    held = tmp_path / "held"
    # Held far longer than the deadline (killed below), so a slow runner can
    # never see the lock come free and the edit succeed.
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(lock), str(held), "60"])
    try:
        for _ in range(1000):
            if held.exists():
                break
            time.sleep(0.01)
        assert held.exists()
        monkeypatch.setattr(storage_mod, "_LOCK_TIMEOUT_S", 1.0)
        manager = SettingsManager.create(tmp_path, agent, project_trusted=True)
        gaps: list[float] = []

        async def ticker() -> None:
            last = time.monotonic()
            while True:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        tick = asyncio.ensure_future(ticker())
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="held the lock"):
            await manager.edit_list("global", "skills", lambda e: [*e, "-x"])
        waited = time.monotonic() - started
        tick.cancel()
        assert waited > 0.9
        # Round 2 (R6): relative bounds, not a fixed 0.3 s. A wait ON the loop
        # gives one gap as long as the whole wait and almost no ticks; a loaded
        # runner stretches single gaps, but never to half of the wait.
        assert len(gaps) >= 10, f"only {len(gaps)} ticks in {waited:.2f} s"
        assert max(gaps) < waited / 2, f"the event loop stalled for {max(gaps):.2f} s"
        assert (agent / "settings.json").read_text() == "{}"
    finally:
        holder.kill()
        holder.wait(timeout=10)


# --- round 2 (R3, R5) ---------------------------------------------------------


class _GatedStorage(InMemorySettingsStorage):
    """``transact`` that waits on *release* — before the builder, or after it."""

    def __init__(self, *, after_build: bool = False) -> None:
        super().__init__()
        self.after_build = after_build
        self.entered = threading.Event()
        self.release = threading.Event()
        self.done = threading.Event()

    def transact(self, scope: Any, fn: Any) -> None:
        try:
            if not self.after_build:
                self.entered.set()
                self.release.wait(10)
                return super().transact(scope, fn)

            def _built(current: str | None) -> str | None:
                payload = fn(current)
                self.entered.set()
                self.release.wait(10)
                return payload

            return super().transact(scope, _built)
        finally:
            self.done.set()


async def _until(event: threading.Event) -> None:
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the worker never got there")


async def test_trust_revoked_while_the_lock_is_awaited_refuses_the_write() -> None:
    """R3: the check runs again after the lock wait, inside the transaction."""

    storage = _GatedStorage()
    storage.with_lock("project", lambda _c: '{"skills": ["-a"]}')
    manager = SettingsManager.from_storage(storage, project_trusted=True)
    edit = asyncio.ensure_future(manager.edit_list("project", "skills", lambda e: [*e, "-b"]))
    await _until(storage.entered)
    manager.set_project_trusted(False)
    storage.release.set()
    with pytest.raises(RuntimeError, match="not trusted"):
        await edit
    assert storage.project_content == '{"skills": ["-a"]}'


async def test_an_edit_cancelled_while_it_waits_for_the_lock_writes_nothing() -> None:
    """R3: the verifier measured disk ``['-a', '-late']`` and memory ``['-a']``."""

    storage = _GatedStorage()
    storage.with_lock("global", lambda _c: '{"skills": ["-a"]}')
    manager = SettingsManager.from_storage(storage)
    edit = asyncio.ensure_future(manager.edit_list("global", "skills", lambda e: [*e, "-late"]))
    await _until(storage.entered)
    edit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await edit
    storage.release.set()
    await _until(storage.done)
    await asyncio.sleep(0.05)
    assert storage.global_content == '{"skills": ["-a"]}'
    assert manager.get_global_settings().skills == ["-a"]


async def test_an_edit_cancelled_after_its_list_was_built_leaves_memory_matching_disk() -> None:
    storage = _GatedStorage(after_build=True)
    storage.with_lock("global", lambda _c: '{"skills": ["-a"]}')
    manager = SettingsManager.from_storage(storage)
    edit = asyncio.ensure_future(manager.edit_list("global", "skills", lambda e: [*e, "-late"]))
    await _until(storage.entered)
    edit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await edit
    storage.release.set()
    await _until(storage.done)
    for _ in range(50):
        if manager.get_global_settings().skills == ["-a", "-late"]:
            break
        await asyncio.sleep(0.01)
    assert json.loads(storage.global_content or "{}") == {"skills": ["-a", "-late"]}
    assert manager.get_global_settings().skills == ["-a", "-late"]


async def test_a_cancelled_setter_write_is_not_recorded_as_a_settings_error() -> None:
    """R3: ``_write_task`` caught ``BaseException``, so a cancel became an error."""

    storage = _GatedStorage()
    manager = SettingsManager.from_storage(storage)
    manager.set_theme("dark")
    await _until(storage.entered)
    pending = list(manager._pending_writes)  # noqa: SLF001
    assert pending
    for task in pending:
        task.cancel()
    storage.release.set()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await _until(storage.done)
    assert manager.drain_errors() == []


def _replace_failing(times: int, calls: list[int]) -> Any:
    real = os.replace

    def fake(src: Any, dst: Any) -> None:
        calls.append(1)
        if len(calls) <= times:
            raise PermissionError(13, "The process cannot access the file", str(dst))
        real(src, dst)

    return fake


def test_a_replace_refused_while_a_reader_holds_the_file_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5: on Windows a lock-free reader can make ``os.replace`` fail for a moment."""

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text("{}")
    storage = FileSettingsStorage(tmp_path, agent)
    calls: list[int] = []
    monkeypatch.setattr(storage_mod, "_REPLACE_RETRY", True)
    monkeypatch.setattr(storage_mod.os, "replace", _replace_failing(3, calls))
    storage.transact("global", lambda _c: '{"skills": ["-a"]}')
    assert len(calls) == 4
    assert json.loads((agent / "settings.json").read_text()) == {"skills": ["-a"]}
    assert [p.name for p in agent.iterdir() if p.name.endswith(".tmp")] == []


def test_a_replace_that_stays_refused_raises_inside_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text("{}")
    storage = FileSettingsStorage(tmp_path, agent)
    calls: list[int] = []
    monkeypatch.setattr(storage_mod, "_REPLACE_RETRY", True)
    monkeypatch.setattr(storage_mod, "_REPLACE_RETRY_S", 0.2)
    monkeypatch.setattr(storage_mod.os, "replace", _replace_failing(10**6, calls))
    started = time.monotonic()
    with pytest.raises(PermissionError):
        storage.transact("global", lambda _c: '{"skills": ["-a"]}')
    assert time.monotonic() - started < 2.0
    assert len(calls) > 2
    assert (agent / "settings.json").read_text() == "{}"
    assert [p.name for p in agent.iterdir() if p.name.endswith(".tmp")] == []
    # Off Windows a refused replace is a real permission problem: one try.
    calls.clear()
    monkeypatch.setattr(storage_mod, "_REPLACE_RETRY", False)
    with pytest.raises(PermissionError):
        storage.transact("global", lambda _c: '{"skills": ["-a"]}')
    assert len(calls) == 1
