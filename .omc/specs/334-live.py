"""#334 live check — a second input during prompt #1's REAL retry backoff.

Committed as ``.omc/specs/334-live.py`` (the #334 lane's driver; the design lane
wrote it as ``tail-live.py``). The contract it checks on a fixed tree: #2 is
refused ``busy``; #1's re-run answers only its own message; the JSONL holds #1's
line only; the NEXT prompt, once #1 has returned, gets a real answer; and
``dispose()`` returns.

Adapted from ``.omc/specs/321-live.py`` (its ``rerun`` mode). The harness is
built the way ``aelix -p --provider openrouter --model anthropic/claude-haiku-4.5``
builds its model — ``register_providers()`` then ``resolve_model(...)``, the
catalog entry, no injected ``stream_fn`` — with a real ``JsonlSessionStorage``
file. The ONE thing faked: the first provider call is answered with an injected
retryable error (``429 rate limit exceeded``), because a real rate limit cannot
be ordered. #1 then sleeps in the REAL ``_handle_retryable_error`` backoff (base
delay cut to 1.5 s) and its re-run goes to OpenRouter.

Modes:
  prompt  during #1's backoff: the phase, ``is_idle``, a ``wait_for_idle()``
          (bounded 0.2 s), then a second ``prompt()`` — is it refused (busy) or
          accepted? What does #1's re-run answer (does it see #2's message)?
  steer   during #1's backoff: ``steer()`` a second instruction — the input
          route the TUI takes for a line typed during the countdown (Enter while
          the chrome is running). #1's re-run should carry it.

Run from a scratch directory (never the checkout), with the tree under test's
interpreter::

    cd <scratch dir> && set -a && . <tree>/.env && set +a
    uv run --no-sync --project <tree> python .omc/specs/334-live.py prompt|steer
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from typing import Any

import aelix_agent_core.harness.core as core_mod
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.messages import AssistantMessage
from aelix_ai.streaming import AssistantErrorEvent, AssistantStartEvent

FIRST = "Reply with exactly: ALPHA"
SECOND = "Reply with exactly: BRAVO"
THIRD = "Now reply with exactly: CHARLIE"


def _user_lines(path: str) -> list[str]:
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            msg = rec.get("message") or {}
            if rec.get("type") == "message" and msg.get("role") == "user":
                out.extend(c["text"] for c in msg.get("content") or [] if c.get("type") == "text")
    return out


def _state(h: AgentHarness) -> str:
    return f"phase={h.phase!r} idle_event={h._idle_event.is_set()} is_idle={h.is_idle}"


def _reply(messages: Any) -> str:
    return "".join(
        c.text
        for m in messages
        if getattr(m, "role", "") == "assistant"
        for c in getattr(m, "content", []) or []
        if getattr(c, "type", "") == "text"
    ).strip()


async def run(h: AgentHarness, path: str, mode: str) -> None:
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 1500  # type: ignore[attr-defined]
    h._state.auto_retry_enabled = True
    calls = {"n": 0}
    contexts: list[list[str]] = []
    real_make = h._make_stream_fn

    def make(get_turn_state: Any) -> Any:
        real = real_make(get_turn_state)

        async def fn(model: Any, context: Any, options: Any = None) -> Any:
            calls["n"] += 1
            contexts.append(
                [
                    c.text
                    for m in context.messages
                    if getattr(m, "role", "") == "user"
                    for c in getattr(m, "content", []) or []
                    if hasattr(c, "text")
                ]
            )
            if calls["n"] == 1:
                text = "429 rate limit exceeded (injected by 334-live.py)"
                failed = AssistantMessage(content=[], stop_reason="error", error_message=text)
                yield AssistantStartEvent(partial=AssistantMessage(content=[]))
                yield AssistantErrorEvent(reason="error", error=failed, error_message=text)
                return
            async for event in real(model, context, options):
                yield event

        return fn

    h._make_stream_fn = make  # type: ignore[method-assign]
    in_backoff = asyncio.Event()

    def watch(event: Any) -> None:
        if type(event).__name__ == "AutoRetryStartEvent":
            in_backoff.set()

    h.subscribe(watch)
    first = asyncio.ensure_future(h.prompt(FIRST))
    await asyncio.wait_for(in_backoff.wait(), 30)
    print("#1 in its real backoff:", _state(h))
    waiter = asyncio.ensure_future(h.wait_for_idle())
    done, _ = await asyncio.wait({waiter}, timeout=0.2)
    print("wait_for_idle()       :", "returned during #1's backoff" if waiter in done else "parked (0.2 s)")
    second: asyncio.Future[Any] | None = None
    if mode == "prompt":
        second = asyncio.ensure_future(h.prompt(SECOND))
        done, _ = await asyncio.wait({second}, timeout=0.05)
        if second in done and second.exception() is not None:
            exc = second.exception()
            print("#2 prompt()           :", f"{type(exc).__name__}: {exc}")
            second = None
        else:
            print("#2 prompt()           : ACCEPTED (in flight)")
    else:
        await h.steer(SECOND.replace("Reply", "Also reply"))
        print("steer()               : queued", h.pending_message_count, "message(s)")
    try:
        messages = await asyncio.wait_for(first, 120)
        print("#1 returned           : model replied", repr(_reply(messages)))
    except BaseException as exc:  # noqa: BLE001
        print("#1 ended              :", f"{type(exc).__name__}: {exc}")
    if second is not None:
        try:
            messages = await asyncio.wait_for(second, 120)
            print("#2 returned           : model replied", repr(_reply(messages)))
        except BaseException as exc:  # noqa: BLE001
            print("#2 ended              :", f"{type(exc).__name__}: {exc}")
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(waiter, 10)
    print("after                 :", _state(h))
    print("provider calls        :", calls["n"], "user text per call:", contexts)
    print("user lines on disk    :", _user_lines(path))
    try:
        messages = await asyncio.wait_for(h.prompt(THIRD), 120)
        print("next prompt()         : model replied", repr(_reply(messages)))
    except BaseException as exc:  # noqa: BLE001
        print("next prompt()         :", f"{type(exc).__name__}: {exc}")
    print("provider calls now    :", calls["n"], "| last call's user text:", contexts[-1])


async def main(mode: str) -> int:
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers, resolve_model

    print("core.py               :", core_mod.__file__)
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2
    register_providers()
    model = resolve_model("anthropic/claude-haiku-4.5", "openrouter")
    print("model                 :", model.provider, model.id, model.api)
    with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
        path = f"{tmp}/live.jsonl"
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), path, cwd=tmp, session_id="live-334"
        )
        h = AgentHarness(AgentHarnessOptions(model=model, session=Session(storage)))
        await run(h, path, mode)
        try:
            await asyncio.wait_for(h.dispose(), 10)
            print("dispose()             : returned")
        except TimeoutError:
            print("dispose()             : TIMEOUT after 10 s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "prompt")))
