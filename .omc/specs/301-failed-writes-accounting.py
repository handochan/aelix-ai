#!/usr/bin/env python3
"""#301 cross-review: what does ``SavePointHookEvent.failed_writes`` actually prove?

The first wording of that field said zero means every pending mutation is
committed and a positive count means the session on disk is missing that many
records. Codex's cross-review produced two counterexamples; both are measured
here, on a real :class:`JsonlSessionStorage` file under ``tmp/`` — the word
"disk" in this file's output means bytes in a file, read back with ``open()``.

    ARM 1  UNDERCOUNT. Refuse one write, then cancel the flush while it awaits
           the next. The local ``failed`` counter dies with the raise, the
           close-out's second flush drains the requeued tail cleanly, and the
           only ``save_point`` says ``failed_writes=0`` although a record was
           refused and a second one is unaccounted for.
    ARM 2  OVERCOUNT. A disk that fills between a record's last byte and its
           newline. ``_append_line`` re-arms the healing newline and re-raises,
           the next append opens a fresh line, and the "lost" record is on disk
           and survives the reload — while ``failed_writes`` counts it as gone.

Run from a worktree root::

    uv run --no-sync python .omc/specs/301-failed-writes-accounting.py

Measured 2026-09-21::

    === ARM 1 — a refusal, then a cancellation: the count dies with the raise ===
    save_points       : [(True, 0)]
    queue after abort : []
    lines on disk     : ['session', 'message', 'message', 'message', 'session_info', 'leaf']
    reloaded entries  : ['message', 'message', 'message', 'session_info', 'leaf']
    in-memory entries : ['message', 'message', 'message', 'session_info', 'leaf']

    === ARM 2 — a record whose newline did not fit: counted lost, present on disk ===
    save_points       : [(True, 1)]
    lines on disk     : ['session', 'message', 'message', 'message', 'custom', 'session_info', 'leaf']
    reloaded entries  : ['message', 'message', 'message', 'custom', 'session_info', 'leaf']
    in-memory entries : ['message', 'message', 'message', 'session_info', 'leaf']

ARM 1's WARNING records, which are what the repair adds::

    session write lost: PendingCustomWrite could not be persisted (SessionError(
      'Failed to append session entry …: [Errno 28] No space left on device'));
      1 of 4 writes in this flush have failed so far, the rest are still being
      attempted
    session write flush cancelled after 1 of 4 writes had already failed; that
      count reaches no save_point — these WARNING records are the whole report

ARM 1: the ``custom`` write was refused with ``ENOSPC`` and the ``label`` write
behind it was cancelled mid-append, yet the turn's only ``save_point`` reads
``failed_writes=0`` — the count lived in a local that the ``raise`` discarded,
and the close-out's second flush counts only the requeued tail it attempts
itself. ARM 2: ``failed_writes=1`` for a record that is on disk and comes back
on reload; note that the store's own in-memory entry list does NOT have it,
because ``append_entry`` raised before extending it, so live and reloaded state
disagree about that record either way.

What changed because of this: the wording on ``SavePointHookEvent`` and in
ADR-0245 §4 now says the field counts refusals rather than missing records, and
the cancellation path logs a WARNING naming the count it is about to lose. The
payload was deliberately NOT widened — ADR-0245 §4 argues why a
failed/uncertain split would be two fields with one meaning.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingCustomWrite,
    PendingLabelWrite,
    PendingLeafWrite,
    PendingSessionInfoWrite,
)
from aelix_agent_core.harness.hooks import SavePointHookEvent
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return fn


class _FaultyFs(LocalFileSystem):
    """A real local filesystem with one scripted fault per marker.

    ``refuse`` raises before any byte is written; ``truncate`` writes the line
    minus its final newline and then raises ENOSPC — the disk that fills
    between a record's last byte and its separator; ``block`` never returns.
    """

    def __init__(self) -> None:
        super().__init__()
        self.refuse: str | None = None
        self.truncate: str | None = None
        self.block: str | None = None
        self.entered = asyncio.Event()

    async def append_file(self, path: str, content: str) -> None:
        if self.refuse is not None and self.refuse in content:
            raise OSError(errno.ENOSPC, "No space left on device")
        if self.truncate is not None and self.truncate in content:
            await super().append_file(path, content.rstrip("\n"))
            raise OSError(errno.ENOSPC, "No space left on device")
        if self.block is not None and self.block in content:
            self.entered.set()
            await asyncio.sleep(3600)
        await super().append_file(path, content)


async def _seed(fs: LocalFileSystem, root: Path) -> tuple[AgentHarness, Session, str, str]:
    file_path = str(root / "s.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(root), session_id="probe"
    )
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    return h, session, seed_id, file_path


def _lines_on_disk(file_path: str) -> list[str]:
    """Entry types as the BYTES in the file have them, nothing in memory."""

    out: list[str] = []
    for raw in Path(file_path).read_text(encoding="utf-8").split("\n"):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw)["type"])
        except Exception:
            out.append("<unparseable>")
    return out


async def _reloaded(fs: LocalFileSystem, file_path: str) -> list[str]:
    """Entry types a fresh reader gets after re-opening the same file."""

    reopened = await JsonlSessionStorage.open(fs, file_path)
    return [e.type for e in await reopened.get_entries()]


async def arm1() -> None:
    print("=== ARM 1 — a refusal, then a cancellation: the count dies with the raise ===")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fs = _FaultyFs()
        fs.refuse = "refuse-me"  # the custom entry
        fs.block = "block-me"  # the label entry
        h, session, seed_id, file_path = await _seed(fs, root)

        save_points: list[SavePointHookEvent] = []

        async def on_save_point(event: Any, _ctx: Any) -> Any:
            save_points.append(event)
            return None

        async def in_turn(event: Any, _ctx: Any) -> Any:
            if not h._pending_session_writes:
                h._pending_session_writes.extend(
                    [
                        PendingCustomWrite(custom_type="refuse-me"),  # raises
                        PendingLabelWrite(target_id=seed_id, label="block-me"),
                        PendingSessionInfoWrite(name="tail-1"),
                        PendingLeafWrite(target_id=seed_id),
                    ]
                )
            return None

        h.hooks.on("save_point", on_save_point)  # type: ignore[arg-type]
        h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

        task = asyncio.ensure_future(h.prompt("hello"))
        await asyncio.wait_for(fs.entered.wait(), timeout=10)
        await h.abort()
        await asyncio.wait_for(task, timeout=10)

        print("save_points       :", [(e.had_pending_mutations, e.failed_writes) for e in save_points])
        print("queue after abort :", [type(e).__name__ for e in h._pending_session_writes])
        print("lines on disk     :", _lines_on_disk(file_path))
        print("reloaded entries  :", await _reloaded(LocalFileSystem(), file_path))
        print("in-memory entries :", [e.type for e in await session.get_entries()])
        print()


async def arm2() -> None:
    print("=== ARM 2 — a record whose newline did not fit: counted lost, present on disk ===")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fs = _FaultyFs()
        fs.truncate = "truncate-me"  # the custom entry: object lands, newline does not
        h, session, seed_id, file_path = await _seed(fs, root)

        save_points: list[SavePointHookEvent] = []

        async def on_save_point(event: Any, _ctx: Any) -> Any:
            save_points.append(event)
            return None

        async def in_turn(event: Any, _ctx: Any) -> Any:
            if not h._pending_session_writes:
                h._pending_session_writes.extend(
                    [
                        PendingCustomWrite(custom_type="truncate-me"),
                        PendingSessionInfoWrite(name="tail-1"),
                        PendingLeafWrite(target_id=seed_id),
                    ]
                )
            return None

        h.hooks.on("save_point", on_save_point)  # type: ignore[arg-type]
        h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

        await h.prompt("hello")

        print("save_points       :", [(e.had_pending_mutations, e.failed_writes) for e in save_points])
        print("lines on disk     :", _lines_on_disk(file_path))
        print("reloaded entries  :", await _reloaded(LocalFileSystem(), file_path))
        print("in-memory entries :", [e.type for e in await session.get_entries()])
        print()


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(message)s")
    await arm1()
    await arm2()


if __name__ == "__main__":
    asyncio.run(main())
