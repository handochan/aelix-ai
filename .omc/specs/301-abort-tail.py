#!/usr/bin/env python3
"""#301 follow-up: what happens to the queue when an abort cancels the flush?

``flush_pending_session_writes`` detaches the queue before its first await. If
a ``CancelledError`` leaves the loop, the items it had not reached yet are on
no queue and on no disk — #301 again, wearing an abort for a costume. The
``finally`` safety net cannot help: the queue it re-flushes is already ``[]``.

Before deciding whether to hand that tail back, the question is whether
ANYTHING would flush it again. Four arms:

    ARM 1  the realistic Ctrl-C — ``abort()`` while the ``turn_end`` flush sits
           in a blocking append. What is on disk when ``prompt()`` returns?
    ARM 2  does a LATER turn's ``turn_end`` flush drain a non-empty queue?
    ARM 3  does ``dispose()`` flush it?
    ARM 4  does the harness accept another ``prompt()`` after an ``abort()``?

Every arm runs against a real :class:`JsonlSessionStorage` file in a temporary
directory, and the ``on disk`` lines below are that file's bytes, re-read and
parsed with ``json.loads`` — not a storage object's in-memory entry list. The
first version of this probe subclassed ``MemorySessionStorage`` and printed its
``get_entries()`` under that label, which made ADR-0245's disk evidence a claim
about a Python list; a cross-review pass caught it and this is the repair. The
blocking and refusing faults now sit at the ``FileSystem`` seam, under the real
store, so the healing-newline machinery of ADR-0242 is in the measured path.

Run from a worktree root::

    uv run --no-sync python .omc/specs/301-abort-tail.py

Measured 2026-09-21. ARM 1 BEFORE the fix — the pre-#301 ``core.py`` overlaid on
this tree via ``PYTHONPATH`` (``git show HEAD~1:…/harness/core.py``), so only
the drain differs::

    queue after abort : []
    on disk           : ['session', 'message', 'message', 'message']

three records — a label, a rename and a leaf move — dropped with no WARNING and
no ``failed_writes``. ARM 1 AFTER::

    queue after abort : []
    on disk           : ['session', 'message', 'message', 'message', 'label', 'session_info', 'leaf']

ARM 2 writes the seeded queue (``['label', 'session_info']`` land), ARM 3 does
NOT (the queue still holds its item and the file holds only its header), ARM 4
returns True. So: yes, something flushes it again, inside the same ``prompt()``.

This docstring used to name that call as "the same ``prompt()``'s ``finally``
net". That was wrong — read off the ordering of the code rather than measured.
Wrapping ``flush_pending_session_writes`` and reading
``sys._getframe(1).f_lineno`` on the ARM 1 path gives::

    ENTER  flush from core.py:4613  queue=['PendingCustomWrite', 'PendingLabelWrite', 'PendingSessionInfoWrite', 'PendingLeafWrite']
      RAISED CancelledError from the call at :4613; queue now=['PendingLabelWrite', 'PendingSessionInfoWrite', 'PendingLeafWrite']
    ENTER  flush from core.py:4613  queue=['PendingLabelWrite', 'PendingSessionInfoWrite', 'PendingLeafWrite']
      RETURN failed=0; on disk now=['session', 'message', 'message', 'message', 'label', 'session_info', 'leaf']
    ENTER  flush from core.py:4786  queue=[]
      RETURN failed=0; on disk now=['session', 'message', 'message', 'message', 'label', 'session_info', 'leaf']

``prompt()`` does catch the turn task's ``CancelledError``
(``core.py:4661-4662``), but what writes the tail is the **turn_end
projection's** flush at ``core.py:4613``, re-run because the abort close-out
emits a synthetic ``TurnEndEvent`` (``core.py:4700-4705``). The ``finally`` net
(``core.py:4786``) runs uncancelled to an already-empty queue. It is the
backstop, and measurably a working one: suppress that ``TurnEndEvent`` and the
net delivers the tail itself, and a cancellation that is not an abort (the
``raise`` at ``core.py:4713``) reaches it with the tail still queued. Failing
both, the next turn. ``dispose()`` is not a backstop.
"""

from __future__ import annotations

import asyncio
import json
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


class _BlockingOnceFs(LocalFileSystem):
    """A real local filesystem whose first marked append never returns.

    The fault sits here, under ``JsonlSessionStorage``, rather than in a
    storage subclass: a disk that stops is a write that does not come back,
    and putting it at this seam leaves the store's own lock, encoding and
    healing-newline handling in the measured path.
    """

    def __init__(self, marker: str | None = None) -> None:
        super().__init__()
        self.marker = marker
        self.entered = asyncio.Event()

    async def append_file(self, path: str, content: str) -> None:
        if self.marker is not None and self.marker in content:
            self.marker = None
            self.entered.set()
            await asyncio.sleep(3600)
        await super().append_file(path, content)


def _on_disk(file_path: str) -> list[str]:
    """Entry types as the BYTES in the file have them — nothing in memory."""

    out: list[str] = []
    for raw in Path(file_path).read_text(encoding="utf-8").split("\n"):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw)["type"])
        except Exception:
            out.append("<unparseable>")
    return out


async def _seed(
    fs: LocalFileSystem, root: Path
) -> tuple[AgentHarness, Session, str, str]:
    file_path = str(root / "s.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(root), session_id="abort-tail"
    )
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    return h, session, seed_id, file_path


async def arm1() -> None:
    print("=== ARM 1 — abort() during a slow turn_end flush ===")
    with tempfile.TemporaryDirectory() as tmp:
        fs = _BlockingOnceFs(marker="slow")
        h, _session, seed_id, file_path = await _seed(fs, Path(tmp))

        async def in_turn(event: Any, _ctx: Any) -> Any:
            if not h._pending_session_writes:
                h._pending_session_writes.extend(
                    [
                        PendingCustomWrite(custom_type="slow"),  # blocks
                        PendingLabelWrite(target_id=seed_id, label="tail-1"),
                        PendingSessionInfoWrite(name="tail-2"),
                        PendingLeafWrite(target_id=seed_id),
                    ]
                )
            return None

        h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

        task = asyncio.ensure_future(h.prompt("hello"))
        await asyncio.wait_for(fs.entered.wait(), timeout=10)
        await h.abort()
        await asyncio.wait_for(task, timeout=10)

        print(
            "queue after abort :",
            [type(e).__name__ for e in h._pending_session_writes],
        )
        print("on disk           :", _on_disk(file_path))
        print("phase after abort :", h._phase)
        print()


async def arm2() -> None:
    print("=== ARM 2 — does a LATER turn flush what sits on the queue? ===")
    with tempfile.TemporaryDirectory() as tmp:
        h, _session, seed_id, file_path = await _seed(LocalFileSystem(), Path(tmp))
        h._pending_session_writes.extend(
            [
                PendingLabelWrite(target_id=seed_id, label="tail-1"),
                PendingSessionInfoWrite(name="tail-2"),
            ]
        )
        print(
            "queue before turn :",
            [type(e).__name__ for e in h._pending_session_writes],
        )
        await h.prompt("a later turn")
        print(
            "queue after turn  :",
            [type(e).__name__ for e in h._pending_session_writes],
        )
        print("on disk           :", _on_disk(file_path))
        print()


async def arm3() -> None:
    print("=== ARM 3 — does dispose() flush it? ===")
    with tempfile.TemporaryDirectory() as tmp:
        h, _session, seed_id, file_path = await _seed(LocalFileSystem(), Path(tmp))
        h._pending_session_writes.append(
            PendingLabelWrite(target_id=seed_id, label="tail-1")
        )
        await h.dispose()
        print(
            "queue after dispose:",
            [type(e).__name__ for e in h._pending_session_writes],
        )
        print("on disk            :", _on_disk(file_path))
        print()


async def arm4() -> None:
    print("=== ARM 4 — is the harness usable for another turn after abort()? ===")
    with tempfile.TemporaryDirectory() as tmp:
        h, _session, _seed_id, _file_path = await _seed(LocalFileSystem(), Path(tmp))
        await h.abort()
        messages = await h.prompt("after the abort")
        print("prompt() after abort returned:", bool(messages))
        print()


async def main() -> None:
    await arm1()
    await arm2()
    await arm3()
    await arm4()


if __name__ == "__main__":
    asyncio.run(main())
