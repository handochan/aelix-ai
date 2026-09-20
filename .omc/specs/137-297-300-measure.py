"""Pre-fix reproduction probe for #137 / #297 / #300 (read-only; writes to a tmpdir).

Run from the worktree:
    cd /tmp/wt-137 && uv run --no-sync python <this file>
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from aelix_agent_core.session.context import build_session_context
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
from aelix_agent_core.session.repo_utils import ForkOptions
from aelix_agent_core.session.session import Session
from aelix_ai.messages import TextContent, UserMessage


def _user(text: str):
    return UserMessage(content=[TextContent(text=text)])


async def defect_137(root: Path) -> None:
    print("=== #137 two writers on one file ===")
    repo = JsonlSessionRepo(sessions_root=str(root))
    s = await repo.create(JsonlSessionCreateOptions(cwd="/w/137"))
    meta = await s.get_metadata()

    # Two independent storage objects over the SAME file = two processes.
    a = Session(await JsonlSessionStorage.open(repo._fs, meta.path))
    b = Session(await JsonlSessionStorage.open(repo._fs, meta.path))

    await a.append_message(_user("A-turn-1"))
    await b.append_message(_user("B-turn-1"))
    await a.append_message(_user("A-turn-2"))

    lines = Path(meta.path).read_text(encoding="utf-8").splitlines()
    parsed = 0
    for line in lines:
        if line.strip():
            json.loads(line)
            parsed += 1
    reopened = await JsonlSessionStorage.open(repo._fs, meta.path)
    branch = await reopened.get_path_to_root(await reopened.get_leaf_id())
    ctx = build_session_context(branch)
    texts = [
        getattr(part, "text", None)
        for m in ctx.messages
        for part in (m.content if isinstance(m.content, list) else [])
        if getattr(part, "text", None) is not None
    ]
    all_texts = [
        getattr(part, "text", None)
        for e in await reopened.get_entries()
        for part in (getattr(getattr(e, "message", None), "content", None) or [])
        if getattr(part, "text", None) is not None
    ]
    print(f"  every entry on disk          = {all_texts}")
    print(f"  lines on disk (incl. header) = {len(lines)}, all parse = {parsed == len(lines)}")
    print(f"  replay after reopen          = {texts}")
    print(f"  entries actually stored      = {len(await reopened.get_entries())}")


async def defect_297(root: Path) -> None:
    print("=== #297 header over 512 bytes ===")
    repo = JsonlSessionRepo(sessions_root=str(root))
    deep = "/" + "/".join(f"deep-directory-segment-{i:02d}" for i in range(6))
    src = await repo.create(JsonlSessionCreateOptions(cwd=deep))
    await src.append_message(_user("hello"))
    src_meta = await src.get_metadata()
    forked = await repo.fork(
        src_meta, ForkOptions(cwd=deep, parent_session_path=src_meta.path)
    )
    fmeta = await forked.get_metadata()
    src_header = Path(src_meta.path).read_text(encoding="utf-8").split("\n", 1)[0]
    fork_header = Path(fmeta.path).read_text(encoding="utf-8").split("\n", 1)[0]
    print(f"  cwd length          = {len(deep)}")
    print(f"  origin header bytes = {len(src_header.encode())}")
    print(f"  fork   header bytes = {len(fork_header.encode())}")
    print(f"  _is_valid_session_file(origin) = {JsonlSessionRepo._is_valid_session_file(Path(src_meta.path))}")
    print(f"  _is_valid_session_file(fork)   = {JsonlSessionRepo._is_valid_session_file(Path(fmeta.path))}")
    found = await repo.find_most_recent(deep)
    print(f"  find_most_recent picks         = {'fork' if found and found.path == fmeta.path else ('origin' if found and found.path == src_meta.path else found)}")


async def defect_300(root: Path) -> None:
    print("=== #300 fork before the first user message ===")
    repo = JsonlSessionRepo(sessions_root=str(root))
    s = await repo.create(JsonlSessionCreateOptions(cwd="/w/300"))
    first = await s.append_message(_user("first user message"))
    await s.append_message(_user("second user message"))
    storage = s.get_storage()
    entries = await storage.get_entries()
    first_entry = await storage.get_entry(first.id if hasattr(first, "id") else entries[0].id)
    target = first_entry if first_entry is not None else entries[0]
    print(f"  first entry type/role = {target.type}/{getattr(getattr(target, 'message', None), 'role', None)}")
    print(f"  its parent_id         = {target.parent_id!r}  <- the None that means 'whole session'")
    meta = await s.get_metadata()
    forked = await repo.fork(
        meta,
        ForkOptions(cwd=meta.cwd, entry_id=target.parent_id, position="at", parent_session_path=meta.path),
    )
    fstorage = forked.get_storage()
    print(f"  entries in the 'empty' fork = {len(await fstorage.get_entries())} (expected 0)")


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        await defect_137(root / "a")
        await defect_297(root / "b")
        await defect_300(root / "c")


asyncio.run(main())
