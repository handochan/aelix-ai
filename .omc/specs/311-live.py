"""#311 live check — a real model, a real session file, a throwing observer.

Not a unit test: this drives the production path end to end. An
``AgentHarness`` with no injected ``stream_fn`` builds the real provider
closure, so both turns below are actual OpenRouter requests, and the session
is a real ``JsonlSessionStorage`` writing real JSONL lines.

A message is queued for the next turn, an observer then starts raising on
``queue_update``, and the first ``prompt()`` is expected to fail. What is
being checked is what the user has afterwards: the queued sentence must still
be queued, must reach the real model on the next prompt, and must be in the
session file on disk.

    set -a && . .env && set +a
    uv run --no-sync python .omc/specs/311-live.py

Recorded run on the branch (2026-09-22, openrouter / anthropic/claude-haiku-4.5)::

    first prompt raised  : AgentHarnessError('queue_update hook handler raised: observer blew up')
    queue after the raise: ['Also reply with exactly: BRAVO']
    second prompt raised : None
    model replied        : 'seed\\n\\nBRAVO\\nECHO'
    user lines on disk   : ['seed', 'Also reply with exactly: BRAVO',
                            'Reply with exactly: ECHO']
    reloaded user lines  : ['seed', 'Also reply with exactly: BRAVO',
                            'Reply with exactly: ECHO']

And the same file run against the base, by overlaying ``main``'s ``core.py``
on this tree so only the drain differs::

    git show main:packages/aelix-agent-core/src/aelix_agent_core/harness/core.py \\
        > /tmp/base/src/aelix_agent_core/harness/core.py
    PYTHONPATH=/tmp/base/src uv run --no-sync python .omc/specs/311-live.py

    queue after the raise: []
    model replied        : 'ECHO'
    user lines on disk   : ['seed', 'Reply with exactly: ECHO']
    reloaded user lines  : ['seed', 'Reply with exactly: ECHO']

The sentence is in none of the three. The model answered the second prompt
and never saw the first, and nothing on disk records that it was ever asked.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.messages import TextContent, UserMessage

QUEUED = "Also reply with exactly: BRAVO"
PROMPTED = "Reply with exactly: ECHO"


def _user_lines(path: str) -> list[str]:
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            msg = rec.get("message") or {}
            if rec.get("type") == "message" and msg.get("role") == "user":
                for c in msg.get("content") or []:
                    if c.get("type") == "text":
                        out.append(c["text"])
    return out


async def main() -> int:
    from aelix_ai.models import get_models
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/live.jsonl"
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), path, cwd=tmp, session_id="live-311"
        )
        session = Session(storage)
        await session.append_message(UserMessage(content=[TextContent(text="seed")]))

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

        # Queue while idle, THEN install the observer — ``next_turn()`` emits
        # ``queue_update`` itself, so a handler registered first would refuse
        # the enqueue rather than the drain.
        await h.next_turn(QUEUED)

        calls = {"n": 0}

        def boom_once(event: Any, _ctx: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("observer blew up")

        h.hooks.on("queue_update", boom_once)  # type: ignore[arg-type]

        raised: BaseException | None = None
        try:
            await h.prompt(PROMPTED)
        except BaseException as exc:  # noqa: BLE001
            raised = exc
        print(f"first prompt raised  : {raised!r}")
        print(
            "queue after the raise: "
            f"{[c.text for m in h._next_turn_queue for c in m.content if hasattr(c, 'text')]}"
        )

        raised2: BaseException | None = None
        messages: list[Any] = []
        try:
            messages = await h.prompt(PROMPTED)
        except BaseException as exc:  # noqa: BLE001
            raised2 = exc
        # ``prompt()`` returns the turn's new messages, user ones included —
        # the drained message is among them, which is itself the point.
        text = "".join(
            c.text
            for m in messages
            if getattr(m, "role", "") == "assistant"
            for c in getattr(m, "content", []) or []
            if getattr(c, "type", "") == "text"
        )
        print(f"second prompt raised : {raised2!r}")
        print(f"model replied        : {text.strip()!r}")
        print(f"user lines on disk   : {_user_lines(path)}")
        reloaded = await JsonlSessionStorage.open(LocalFileSystem(), path)
        entries = await reloaded.get_entries()
        print(
            "reloaded user lines  : "
            f"{[c.text for e in entries if e.type == 'message' and e.message.role == 'user' for c in e.message.content if hasattr(c, 'text')]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
