"""#321 live check — cancel a real prompt before its turn starts, then prompt again.

Not a unit test: the harness is built the way ``aelix -p --provider openrouter
--model anthropic/claude-haiku-4.5`` builds its model — ``register_providers()``
then ``resolve_model(model, provider)``, the catalog entry, no injected
``stream_fn`` — so the provider closure is the production one and the second
prompt is a real OpenRouter request. With no stored credential the CLI's auth
callback answers "no opinion" and the adapter reads ``OPENROUTER_API_KEY`` from
the environment, which is the path taken here. The session is a real
``JsonlSessionStorage`` file, as print mode always attaches one.

A hook parks inside the window between ``prompt()``'s phase claim and ``_run``;
the ``prompt()`` task is cancelled there, exactly as an embedder's
``task.cancel()`` / ``asyncio.wait_for`` timeout would; the hook is removed;
then a second prompt goes to the model.

Run from a scratch directory (never the checkout), with the tree under test's
interpreter::

    cd /tmp/321-work/live-cwd && set -a && . /tmp/wt-321/.env && set +a
    uv run --project <tree> python /tmp/wt-321/.omc/specs/321-live.py [HOOK]

``HOOK`` is ``queue_update`` (default: a message is queued with ``next_turn()``
first, so the parked emit is the drain's — #311's window) or
``before_agent_start`` / ``input``.

``rerun`` instead drives probe ARM 15's shape through the production provider:
the first two provider calls are answered with an injected retryable error
(``429 rate limit exceeded``) — the only thing this driver fakes, since a real
rate limit cannot be ordered — so prompt #1 and prompt #2 (which gets in during
#1's backoff) both sleep in the REAL ``_handle_retryable_error`` backoff, base
delay cut to 1.5 s. #1's backoff ends first and its re-run goes to OpenRouter;
while that request is in flight, #2 is cancelled in its own backoff. The turn
standing is #1's re-run's, so the harness must stay busy, #1 must get its real
answer, and the next prompt must get one too.

``firstrun`` / ``firstrun-cancel`` drive probe ARM 21r / 21c's shape through the
production provider (added in the cross-review round): the first provider call
gets the injected 429, so #1 sleeps in the REAL backoff (base delay cut to 1.5
s); #2 gets in and parks in ``before_agent_start``; #1's re-run goes to
OpenRouter and #1 returns with its real answer while #2 is still parked. Then
#2's hook returns and its FIRST ``_run``'s ``session.build_context()`` — the
real JSONL-backed session's, wrapped for that one call — raises an injected
error (``firstrun``) or parks and #2 is cancelled there (``firstrun-cancel``).
Nothing else is in flight, so the harness must go idle and the next prompt must
reach the model.
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

QUEUED = "Also reply with exactly: CHARLIE"
CANCELLED = "Reply with exactly: ALPHA"
SECOND = "Reply with exactly: BRAVO"


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
    return f"phase={h.phase!r} idle_event={h._idle_event.is_set()}"


def _reply(messages: Any) -> str:
    return "".join(
        c.text
        for m in messages
        if getattr(m, "role", "") == "assistant"
        for c in getattr(m, "content", []) or []
        if getattr(c, "type", "") == "text"
    ).strip()


async def rerun(h: AgentHarness, path: str) -> None:
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 1500  # type: ignore[attr-defined]
    h._state.auto_retry_enabled = True
    calls = {"n": 0}
    rerun_started = asyncio.Event()
    real_make = h._make_stream_fn

    def make(get_turn_state: Any) -> Any:
        real = real_make(get_turn_state)

        async def fn(model: Any, context: Any, options: Any = None) -> Any:
            calls["n"] += 1
            if calls["n"] <= 2:
                text = "429 rate limit exceeded (injected by 321-live.py)"
                failed = AssistantMessage(content=[], stop_reason="error", error_message=text)
                yield AssistantStartEvent(partial=AssistantMessage(content=[]))
                yield AssistantErrorEvent(reason="error", error=failed, error_message=text)
                return
            if calls["n"] == 3:
                rerun_started.set()
            async for event in real(model, context, options):
                yield event

        return fn

    h._make_stream_fn = make  # type: ignore[method-assign]
    starts = [asyncio.Event(), asyncio.Event()]

    def watch(event: Any) -> None:
        if type(event).__name__ == "AutoRetryStartEvent":
            next(e for e in starts if not e.is_set()).set()

    h.subscribe(watch)
    first = asyncio.ensure_future(h.prompt(CANCELLED))
    await asyncio.wait_for(starts[0].wait(), 30)
    second = asyncio.ensure_future(h.prompt(SECOND))
    await asyncio.wait_for(starts[1].wait(), 30)
    print("#1 and #2 in backoff :", _state(h))
    await asyncio.wait_for(rerun_started.wait(), 30)
    print("#1's re-run at the provider:", _state(h), f"(#2 still in its backoff: {not second.done()})")
    second.cancel()
    try:
        await asyncio.wait_for(second, 30)
        ended = "returned"
    except asyncio.CancelledError:
        ended = "CancelledError"
    print("#2 cancelled         :", ended, _state(h))
    third = asyncio.ensure_future(h.prompt("Reply with exactly: CHARLIE"))
    done, _ = await asyncio.wait({third}, timeout=0.5)
    if third in done:
        exc = third.exception()
        print("#3 prompt() now      :", f"{type(exc).__name__}: {exc}" if exc else "returned")
    else:
        print("#3 prompt() now      : ACCEPTED (in flight on top of #1's re-run) — cancelling it")
        third.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(third, 30)
    try:
        messages = await asyncio.wait_for(first, 120)
        print("#1 returned          : model replied", repr(_reply(messages)))
    except BaseException as exc:  # noqa: BLE001
        print("#1 ended             :", f"{type(exc).__name__}: {exc}")
    print("after #1             :", _state(h))
    try:
        messages = await asyncio.wait_for(h.prompt("Reply with exactly: DELTA"), 120)
        print("next prompt          : model replied", repr(_reply(messages)))
    except BaseException as exc:  # noqa: BLE001
        print("next prompt          :", f"{type(exc).__name__}: {exc}")
    print("after next prompt    :", _state(h))
    print("user lines on disk   :", _user_lines(path))


async def firstrun(h: AgentHarness, path: str, session: Session, fault: str) -> None:
    core_mod._AUTO_RETRY_BASE_DELAY_MS = 1500  # type: ignore[attr-defined]
    h._state.auto_retry_enabled = True
    calls = {"n": 0}
    real_make = h._make_stream_fn

    def make(get_turn_state: Any) -> Any:
        real = real_make(get_turn_state)

        async def fn(model: Any, context: Any, options: Any = None) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                text = "429 rate limit exceeded (injected by 321-live.py)"
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
    in_hook = asyncio.Event()
    release_hook = asyncio.Event()

    async def park_second(event: Any, *_args: Any) -> None:
        if getattr(event, "prompt", None) == SECOND:
            in_hook.set()
            await release_hook.wait()

    h.hooks.on("before_agent_start", park_second)  # type: ignore[call-overload]
    real_build_context = session.build_context
    armed = {"on": False}
    reached = asyncio.Event()

    async def build_context() -> Any:
        if armed["on"]:
            armed["on"] = False
            reached.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable (injected by 321-live.py)")
            await asyncio.Event().wait()
        return await real_build_context()

    session.build_context = build_context  # type: ignore[method-assign]
    first = asyncio.ensure_future(h.prompt(CANCELLED))
    await asyncio.wait_for(in_backoff.wait(), 30)
    second = asyncio.ensure_future(h.prompt(SECOND))
    await asyncio.wait_for(in_hook.wait(), 30)
    print("#1 in backoff, #2 in before_agent_start:", _state(h))
    try:
        messages = await asyncio.wait_for(first, 120)
        print("#1 returned          : model replied", repr(_reply(messages)), _state(h))
    except BaseException as exc:  # noqa: BLE001
        print("#1 ended             :", f"{type(exc).__name__}: {exc}")
    armed["on"] = True
    release_hook.set()
    if fault == "cancel":
        await asyncio.wait_for(reached.wait(), 30)
        second.cancel()
    try:
        await asyncio.wait_for(second, 30)
        print("#2 ended             : returned")
    except BaseException as exc:  # noqa: BLE001
        print("#2 ended             :", f"{type(exc).__name__}: {exc}")
    print("after #2             :", _state(h))
    try:
        await asyncio.wait_for(h.wait_for_idle(), 10)
        print("wait_for_idle()      : returned")
    except TimeoutError:
        print("wait_for_idle()      : TIMEOUT after 10 s")
    try:
        messages = await asyncio.wait_for(h.prompt("Reply with exactly: DELTA"), 120)
        print("next prompt          : model replied", repr(_reply(messages)))
    except BaseException as exc:  # noqa: BLE001
        print("next prompt          :", f"{type(exc).__name__}: {exc}")
    print("after next prompt    :", _state(h))
    print("user lines on disk   :", _user_lines(path))


async def main(hook: str) -> int:
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers, resolve_model

    print("core.py              :", core_mod.__file__)
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2
    register_providers()
    model = resolve_model("anthropic/claude-haiku-4.5", "openrouter")
    print("model                :", model.provider, model.id, model.api)

    with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmp:
        path = f"{tmp}/live.jsonl"
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), path, cwd=tmp, session_id="live-321"
        )
        session = Session(storage)
        h = AgentHarness(AgentHarnessOptions(model=model, session=session))
        if hook in ("rerun", "firstrun", "firstrun-cancel"):
            if hook == "rerun":
                await rerun(h, path)
            else:
                await firstrun(h, path, session, "cancel" if hook.endswith("cancel") else "raise")
            try:
                await asyncio.wait_for(h.dispose(), 10)
                print("dispose()            : returned")
            except TimeoutError:
                print("dispose()            : TIMEOUT after 10 s")
            return 0
        if hook == "queue_update":
            await h.next_turn(QUEUED)

        entered = asyncio.Event()

        async def park(*_args: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        unsubscribe = h.hooks.on(hook, park)  # type: ignore[call-overload]
        first = asyncio.ensure_future(h.prompt(CANCELLED))
        await asyncio.wait_for(entered.wait(), 30)
        print(f"parked in {hook:<11}:", _state(h))
        first.cancel()
        try:
            await asyncio.wait_for(first, 30)
            ended = "returned"
        except asyncio.CancelledError:
            ended = "CancelledError"
        print("first prompt ended   :", ended)
        unsubscribe()

        print("before second prompt :", _state(h))
        queued = [
            c.text for m in h._next_turn_queue for c in m.content if hasattr(c, "text")
        ]
        if hook == "queue_update":
            print("next_turn queue      :", queued)
        try:
            messages = await asyncio.wait_for(h.prompt(SECOND), 120)
            reply = "".join(
                c.text
                for m in messages
                if getattr(m, "role", "") == "assistant"
                for c in getattr(m, "content", []) or []
                if getattr(c, "type", "") == "text"
            )
            stop = next(
                (
                    getattr(m, "stop_reason", None)
                    for m in reversed(messages)
                    if getattr(m, "role", "") == "assistant"
                ),
                None,
            )
            print("second prompt        : returned, stop_reason", repr(stop))
            print("model replied        :", repr(reply.strip()))
        except BaseException as exc:  # noqa: BLE001
            print("second prompt        :", f"{type(exc).__name__}: {exc}")
        print("after second prompt  :", _state(h))
        print("user lines on disk   :", _user_lines(path))
        try:
            await asyncio.wait_for(h.dispose(), 10)
            print("dispose()            : returned")
        except TimeoutError:
            print("dispose()            : TIMEOUT after 10 s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "queue_update")))
