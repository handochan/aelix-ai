"""#301 probe — one failed pending session write throws away every write behind it.

Run from the worktree root:

    uv run --no-sync python .omc/specs/301-measure.py

``AgentHarness.flush_pending_session_writes`` detaches the whole queue
(``self._pending_session_writes = []``) and then calls ``session.append_*``
per item.  This probe queues eight writes, makes the THIRD one raise, and
reports what reached the session, what is still queued, and whether the
caller can even tell that anything was lost.

Two arms:

* ``flush``        — call ``flush_pending_session_writes`` directly.
* ``turn_end``     — drive a real ``prompt()`` so the production call site
                     (the ``turn_end`` projection, ``core.py:4593``, and the
                     ``finally`` safety net, ``core.py:4766``) is the one that
                     flushes.

Exit code 0 means "the defect reproduces" on a base build; after the fix the
probe reports LOSS: 0 and exits 0 as well — read the printed report, not just
the status.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingCustomMessageWrite,
    PendingCustomWrite,
    PendingLabelWrite,
    PendingLeafWrite,
    PendingMessageWrite,
    PendingModelChangeWrite,
    PendingSessionInfoWrite,
    PendingThinkingLevelChangeWrite,
)
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


class BoomStorage(MemorySessionStorage):
    """Memory storage whose Nth ``append`` raises, like a full disk would.

    ADR-0242 makes this survivable one layer down: the JSONL store re-arms the
    healing newline on any exception out of a write, so the NEXT append still
    lands.  The question this probe asks is whether the harness layer above it
    ever makes that next append.
    """

    def __init__(self, fail_type: str) -> None:
        super().__init__()
        self._fail_type = fail_type
        self.failures = 0

    async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
        if entry.type == self._fail_type:
            self.failures += 1
            raise OSError(28, "No space left on device")
        return await super().append_entry(entry)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


def _queue(harness: AgentHarness, seed_id: str) -> list[str]:
    """Queue all eight variants; return the label of each, in order."""

    writes = [
        PendingMessageWrite(message=UserMessage(content=[TextContent(text="m1")])),
        PendingModelChangeWrite(provider="anthropic", model_id="claude-x"),
        # #3 is the one that fails.
        PendingThinkingLevelChangeWrite(thinking_level="high"),
        PendingCustomWrite(custom_type="ct", data={"x": 1}),
        PendingCustomMessageWrite(
            custom_type="cm", content="text", display=True, details=None
        ),
        PendingLabelWrite(target_id=seed_id, label="checkpoint"),
        PendingSessionInfoWrite(name="my session"),
        PendingLeafWrite(target_id=seed_id),
    ]
    harness._pending_session_writes.extend(writes)
    return [type(w).__name__ for w in writes]


async def arm_direct() -> None:
    print("=" * 72)
    print("ARM 1 — flush_pending_session_writes() called directly")
    print("=" * 72)

    # The 2nd of the 8 queued writes is the one that fails.
    storage = BoomStorage(fail_type="model_change")
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    queued = _queue(h, seed_id)

    print(f"queued           : {len(queued)} writes -> {queued}")
    print("failing append   : the PendingModelChangeWrite (#2 of 8)")

    raised: BaseException | None = None
    reported: Any = "<flush returns None on the base build>"
    try:
        reported = await h.flush_pending_session_writes()
    except BaseException as exc:  # noqa: BLE001
        raised = exc

    entries = await session.get_entries()
    landed = [e.type for e in entries]
    still_queued = [type(w).__name__ for w in h._pending_session_writes]

    print(f"flush raised     : {raised!r}")
    print(f"flush reported   : {reported!r} failure(s)")
    print(f"landed in session: {landed}")
    print(f"still queued     : {still_queued}")
    delivered = len(landed) - 1  # minus the seed
    lost = len(queued) - delivered - len(still_queued)
    collateral = lost - storage.failures
    print(f"LOSS             : {lost} of {len(queued)} — {storage.failures} the "
          f"session actually refused, {collateral} COLLATERAL")
    print(
        "  (collateral = innocent writes thrown away because an earlier one "
        "failed;\n   that number is the defect, and it must be 0)"
    )
    print()


async def arm_turn_end() -> None:
    print("=" * 72)
    print("ARM 2 — the production call site: a real prompt() turn_end flush")
    print("=" * 72)

    storage = BoomStorage(fail_type="model_change")
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))

    save_points: list[Any] = []
    h.hooks.on("save_point", lambda e, _c: save_points.append(e))  # type: ignore[arg-type]

    # Queue during the turn so the real turn_end drain is what flushes.
    async def queue_mid_turn(event: Any, _ctx: Any) -> Any:
        if not h._pending_session_writes:
            _queue(h, seed_id)
        return None

    h.hooks.on("before_agent_start", queue_mid_turn)  # type: ignore[arg-type]

    raised: BaseException | None = None
    try:
        await h.prompt("hello")
    except BaseException as exc:  # noqa: BLE001
        raised = exc

    entries = await session.get_entries()
    landed = [e.type for e in entries]
    still_queued = [type(w).__name__ for w in h._pending_session_writes]

    print(f"prompt() raised  : {raised!r}")
    print(f"landed in session: {landed}")
    print(f"still queued     : {still_queued}")
    print(f"save_point emits : {len(save_points)} -> "
          f"{[ (e.had_pending_mutations, getattr(e, 'failed_writes', '<no field>')) for e in save_points ]}")
    print(
        "  (save_point is the only signal a caller gets here. On the base "
        "build it reads\n   (had_pending_mutations=False, <no field>) — a "
        "clean turn — while prompt() dies\n   with the storage error and six "
        "records are gone. That is the silence.)"
    )
    print()


async def arm_poison() -> None:
    """Why "put the failed item back" is not the answer.

    ADR-0242 makes an entry the JSONL store cannot serialize raise
    ``invalid_entry`` BEFORE a byte is written.  That refusal is a property of
    the entry, not of the disk, so it is identical on every retry.  A queue
    that puts such an item back keeps it at the head forever and every later
    label, leaf move and model-change record in the session is stuck behind
    it — a permanent outage where #301 is a one-time loss.
    """

    print("=" * 72)
    print("ARM 3 — is a failed write retryable? (the put-back question)")
    print("=" * 72)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fs = LocalFileSystem()
        path = f"{tmp}/poison.jsonl"
        storage = await JsonlSessionStorage.create(
            fs, path, cwd=tmp, session_id="poison"
        )
        session = Session(storage)

        # An extension calling ``append_custom_entry`` with a live object is
        # all it takes — this is a reachable public path, not a synthetic one.
        attempts = []
        for _ in range(3):
            try:
                await session.append_custom_entry("ct", {"obj": object()})
            except BaseException as exc:  # noqa: BLE001
                attempts.append(f"{type(exc).__name__}/{getattr(exc, 'code', '?')}")
            else:
                attempts.append("OK")
        print(f"same write, 3 attempts : {attempts}")
        print(
            "  -> deterministic refusal: a put-back retry can never clear "
            "this head, so\n     the whole queue behind it would stop "
            "forever."
        )
        # And the store is still healthy for the NEXT, different write -
        # which is exactly the contract #294/ADR-0242 established and the one
        # "keep going" is built on.
        ok_id = await session.append_custom_entry("ct", {"x": 1})
        entries = await session.get_entries()
        print(f"next (different) write : appended {ok_id!r}, entries={[e.type for e in entries]}")
        print(
            "  -> the storage layer promises the NEXT append lands. "
            "Keep-going is the\n     harness-layer expression of that promise; "
            "put-back is not."
        )
    print()


async def main() -> None:
    # Show whether the harness says ANYTHING at the default level.  Nothing
    # below WARNING reaches an operator who has not turned on debug logging.
    logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(name)s: %(message)s")
    await arm_direct()
    await arm_turn_end()
    await arm_poison()


if __name__ == "__main__":
    asyncio.run(main())
