"""#301 live check — a real model turn, a real session file on disk, one
refused pending write.

Not a unit test: this drives the production path end to end. An
``AgentHarness`` with no injected ``stream_fn`` builds the real provider
closure, so the turn below is an actual OpenRouter request; the session is a
real ``JsonlSessionStorage`` writing real JSONL lines; only one entry type is
refused, to stand in for a disk that has filled up mid-turn.

    set -a && . .env && set +a
    uv run --no-sync python .omc/specs/301-live.py

Recorded run on the branch (2026-09-20, openrouter / anthropic/claude-haiku-4.5):

    LOG WARNING aelix_agent_core.harness.core: session write lost:
      PendingModelChangeWrite could not be persisted (OSError(28, ...));
      1 of 5 writes in this flush have failed so far, the rest are still
      being attempted                          [+ full traceback]
    prompt() raised   : None
    model replied     : 'Reply with exactly: okok'
    storage refusals  : 1
    save_point        : had_pending=True failed_writes=1
    lines on disk     : ['session', 'message', 'message', 'message',
                         'custom', 'label', 'session_info', 'leaf']
    reloaded entries  : ['message', 'message', 'message', 'custom',
                         'label', 'session_info', 'leaf']

The four writes queued behind the refused one are on disk and survive a
reload. On the branch base all four were gone and ``prompt()`` raised the
``OSError`` instead of returning the model's reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingCustomWrite,
    PendingLabelWrite,
    PendingLeafWrite,
    PendingModelChangeWrite,
    PendingSessionInfoWrite,
)
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.messages import TextContent, UserMessage


class RefusingJsonl(JsonlSessionStorage):
    """The real JSONL store, with ``model_change`` entries refused."""

    refusals = 0

    async def append_entry(self, entry: Any) -> Any:
        if entry.type == "model_change":
            type(self).refusals += 1
            raise OSError(28, "No space left on device (injected)")
        return await super().append_entry(entry)


async def main() -> int:
    logging.basicConfig(
        level=logging.WARNING, format="LOG %(levelname)s %(name)s: %(message)s"
    )
    from aelix_ai.models import get_models
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/live.jsonl"
        storage = await RefusingJsonl.create(
            LocalFileSystem(), path, cwd=tmp, session_id="live-301"
        )
        session = Session(storage)
        seed_id = await session.append_message(
            UserMessage(content=[TextContent(text="seed")])
        )

        async def auth(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {"apiKey": key, "headers": {}}

        h = AgentHarness(
            AgentHarnessOptions(
                model=next(
                    m
                    for m in get_models("openrouter")
                    if m.id == "anthropic/claude-haiku-4.5"
                ),
                session=session,
                get_api_key_and_headers=auth,
            )
        )

        save_points: list[Any] = []
        h.hooks.on("save_point", lambda e, _c: save_points.append(e))  # type: ignore[arg-type]

        async def queue_mid_turn(event: Any, _ctx: Any) -> Any:
            if not h._pending_session_writes:
                h._pending_session_writes.extend(
                    [
                        PendingModelChangeWrite(
                            provider="openrouter", model_id="anthropic/claude-haiku-4.5"
                        ),
                        PendingCustomWrite(custom_type="aelix.probe", data={"n": 301}),
                        PendingLabelWrite(target_id=seed_id, label="live-301"),
                        PendingSessionInfoWrite(name="live 301"),
                        PendingLeafWrite(target_id=seed_id),
                    ]
                )
            return None

        h.hooks.on("before_agent_start", queue_mid_turn)  # type: ignore[arg-type]

        raised: BaseException | None = None
        try:
            messages = await h.prompt("Reply with exactly: ok")
        except BaseException as exc:  # noqa: BLE001
            raised, messages = exc, []

        text = ""
        for m in messages:
            for c in getattr(m, "content", []) or []:
                if getattr(c, "type", "") == "text":
                    text += c.text

        with open(path, encoding="utf-8") as fh:
            on_disk = [json.loads(line)["type"] for line in fh]
        print(f"prompt() raised   : {raised!r}")
        print(f"model replied     : {text.strip()!r}")
        print(f"storage refusals  : {RefusingJsonl.refusals}")
        print(
            "save_point        : "
            f"had_pending={save_points[0].had_pending_mutations if save_points else None} "
            f"failed_writes={save_points[0].failed_writes if save_points else None}"
        )
        print(f"lines on disk     : {on_disk}")
        reloaded = await JsonlSessionStorage.open(LocalFileSystem(), path)
        print(f"reloaded entries  : {[e.type for e in await reloaded.get_entries()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
