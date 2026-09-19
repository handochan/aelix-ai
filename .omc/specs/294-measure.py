"""Measure the three #294 defects on the current checkout (read-only on the repo).

Run from the repo root (or a worktree): ``uv run --no-sync python .omc/specs/294-measure.py``.
The same file runs on ``main`` and on the #294 branch: it feature-detects
``LocalFileSystem.rename_file`` (added by #294) and injects the fork failure where
each side can actually fail --

* ``main`` builds a fork as header + one ``append_file`` per entry, so [2] fails the
  3rd ``append_file``;
* the branch publishes the fork as one staged temp file renamed into place, so [2]
  fails the ``rename_file`` and, separately, a ``write_file`` that writes HALF the
  content before raising.

[2] ends with a no-injection control fork on both sides. Everything lives in a
TemporaryDirectory; nothing under the repo is touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from aelix_agent_core.session import (
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalFileSystem,
)
from aelix_ai.messages import TextContent, UserMessage

logging.basicConfig(level=logging.ERROR)

#: ``True`` on the #294 branch: forks and creates are published atomically.
HAS_PUBLISH = hasattr(LocalFileSystem, "rename_file")


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


async def unknown_type_prunes_descendants(root: Path) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(root / "s1"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(root)))
    a = await session.append_message(_user("turn-1"))
    storage = session.get_storage()
    path = (await storage.get_metadata()).path
    # A future writer inserts a record of a NEW type as the leaf, then the
    # conversation continues on top of it.
    lines = Path(path).read_text().splitlines()
    new_type = {"type": "subagent_run", "id": "zz000001", "parentId": a,
                "timestamp": "2026-09-19T00:00:00.000Z", "run": 1}
    child = json.loads(lines[-1])
    child.update(id="zz000002", parentId="zz000001")
    child["message"]["content"][0]["text"] = "turn-2 (child of the new type)"
    grandchild = dict(child, id="zz000003", parentId="zz000002")
    Path(path).write_text("\n".join(lines + [json.dumps(new_type), json.dumps(child),
                                             json.dumps(grandchild)]) + "\n")
    reopened = await JsonlSessionStorage.open(fs, path)
    ids = [e.id for e in await reopened.get_entries()]
    print("[1] unknown type: lines on disk =", len(lines) + 3,
          "| entries loaded =", ids,
          "| skipped =", reopened.recovery.skipped_lines if reopened.recovery else None,
          "| orphaned =", reopened.recovery.orphaned_entries if reopened.recovery else None)


class _FailAfter(LocalFileSystem):
    """``main``: the fork's Nth ``append_file`` fails (ENOSPC)."""

    def __init__(self, fail_on_append: int) -> None:
        super().__init__()
        self.appends = 0
        self.fail_on_append = fail_on_append

    async def append_file(self, path: str, content: str) -> None:
        self.appends += 1
        if self.appends == self.fail_on_append:
            raise OSError(28, "No space left on device (injected)")
        await super().append_file(path, content)


class _FailRename(LocalFileSystem):
    """Branch: the staged file is complete, but publishing it fails."""

    async def rename_file(self, source: str, destination: str) -> None:
        raise OSError(28, "No space left on device (injected at rename_file)")


class _HalfWrite(LocalFileSystem):
    """Branch: staging writes half the bytes, then the disk fills."""

    async def write_file(self, path: str, content: str) -> None:
        await super().write_file(path, content[: len(content) // 2])
        raise OSError(28, "No space left on device (injected mid write_file)")


async def _fork_with(fs: LocalFileSystem, root: Path, meta, cwd: Path) -> None:  # type: ignore[no-untyped-def]
    frepo = JsonlSessionRepo(fs=fs, sessions_root=str(root / "s2"))
    try:
        await frepo.fork(meta, ForkOptions(cwd=str(cwd)))
    except Exception as exc:  # noqa: BLE001
        # The branch's message names the whole temp path; keep its basename.
        message = re.sub(r"/\S+/([^/\s]+\.jsonl)", r"…/\1", str(exc))
        print("[2] fork raised:", type(exc).__name__, message)


async def _report_recent(repo: JsonlSessionRepo, fs: LocalFileSystem, cwd: Path) -> None:
    recent = await repo.find_most_recent(str(cwd))
    if recent is None:
        session_dir = await repo._get_session_dir(str(cwd))
        left = sorted(os.listdir(session_dir)) if os.path.isdir(session_dir) else []
        print("[2] find_most_recent(dst) = None | left in the session dir:", left)
        return
    got = await JsonlSessionStorage.open(fs, recent.path)
    print("[2] find_most_recent(dst) picks", Path(recent.path).name,
          "with", len(await got.get_entries()), "of 5 entries")


async def crash_mid_fork(root: Path) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(root / "s2"))
    src = await repo.create(JsonlSessionCreateOptions(cwd=str(root / "src")))
    for i in range(5):
        await src.append_message(_user(f"turn-{i}"))
    meta = await src.get_metadata()
    if HAS_PUBLISH:
        injections: list[LocalFileSystem] = [_FailRename(), _HalfWrite()]
    else:
        injections = [_FailAfter(fail_on_append=3)]
    for failing in injections:
        await _fork_with(failing, root, meta, root / "dst")
        await _report_recent(repo, fs, root / "dst")
    # Control: the same fork with nothing injected, read back from disk.
    control = await repo.fork(meta, ForkOptions(cwd=str(root / "dst-control")))
    control_path = (await control.get_metadata()).path
    reread = await JsonlSessionStorage.open(fs, control_path)
    print("[2] fork complete:", len(await reread.get_entries()), "of 5")


class _TornAppend(LocalFileSystem):
    """Write half of the line, then fail — what ENOSPC mid-write leaves."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    async def append_file(self, path: str, content: str) -> None:
        if self.armed:
            self.armed = False
            await super().append_file(path, content[: len(content) // 2])
            raise OSError(28, "No space left on device (injected)")
        await super().append_file(path, content)


async def failed_append_fuses_next(root: Path) -> None:
    fs = _TornAppend()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(root / "s3"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(root)))
    await session.append_message(_user("turn-1"))
    fs.armed = True
    try:
        await session.append_message(_user("turn-2 (torn)"))
    except Exception as exc:  # noqa: BLE001
        print("[3] torn append raised:", type(exc).__name__)
    await session.append_message(_user("turn-3"))
    await session.append_message(_user("turn-4"))
    path = (await session.get_metadata()).path
    live = [e.message.content[0].text for e in await session.get_entries()]
    reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
    after = [e.message.content[0].text for e in await reopened.get_entries()]
    print("[3] in memory before reload:", live)
    print("[3] after reload           :", after,
          "| orphaned =", reopened.recovery.orphaned_entries if reopened.recovery else None)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        await unknown_type_prunes_descendants(root)
        await crash_mid_fork(root)
        await failed_append_fuses_next(root)


asyncio.run(main())
