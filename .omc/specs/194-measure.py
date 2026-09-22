"""#194 — a failed turn is written into the session twice, so ``/resume`` shows
it twice. The same file runs on the branch base and on the branch.

    uv run python .omc/specs/194-measure.py

Drives a REAL ``AgentHarness`` over a REAL JSONL session on the one failure
path that still escapes the agent loop after #189's shell fix: a provider
stream that ends with no terminal event, which ``loop.py`` turns into
``RuntimeError("The model stream ended without a result: ...")``. #189's own
case is now refused by ``tui/shell.py``'s runnability gate and never reaches
the loop, so it cannot be used to measure this.

Five numbers, because five different questions were open:

  ARM 1  LIVE vs REPLAY emissions for the same failed turn. The defect.
  ARM 2  Is the ``content`` copy load-bearing — does the MODEL read it on the
         next turn, live and after a reload? (If not, the fix would be a
         one-line deletion at the writer. It is.)
  ARM 3  Would a writer-only fix help the sessions already on disk? Replays
         the exact on-disk shape with no writer involved.
  ARM 4  The boundary the first draft of the fix got wrong, found in review.
         ``str(exc)`` is ``""`` for any exception raised with no message, so
         the writer itself emits ``error_message=""`` beside the body
         ``"[error] "``. A truth test (``if err:``) reads that as "no error
         message", puts the echo back on screen, and the ``✖`` line degrades to
         ``✖ request error`` — so the two spellings do not even match any more.
         The shipped guard is ``is not None``.
  ARM 5  The export road, which the ADR first over-claimed as fixed.
         ``export_to_html`` renders the persisted ``content`` verbatim and has
         no notion of ``error_message`` or ``stop_reason`` at all, so the
         ``[error] `` marker is still user-visible THERE. Not fixed here; this
         number is what scopes the ADR's claim to the replay/live roads.

On the branch base ``fad2e28``::

    ARM 1  LIVE 1 / REPLAY 2
    ARM 2  live-next-turn 1 / resumed 1   (the model does read it)
    ARM 3  REPLAY 2
    ARM 4  LIVE 1 / REPLAY 2
    ARM 5  '[error] ' 1 / '✖' 0

On the branch::

    ARM 1  LIVE 1 / REPLAY 1
    ARM 2  live-next-turn 1 / resumed 1   (unchanged — the writer is untouched)
    ARM 3  REPLAY 1
    ARM 4  LIVE 1 / REPLAY 1
    ARM 5  '[error] ' 1 / '✖' 0           (UNCHANGED — the exporter is untouched)

Restore the first draft's ``if err:`` and only ARM 4 moves: LIVE 1 / REPLAY 2.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.session.context import build_display_messages, build_session_context
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.tui.render import EventRenderer

SEEN_CONTEXTS: list[list[Any]] = []


async def truncated_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    """Start a message and then just stop — no done/error/end event."""

    yield AssistantStartEvent()


async def recording_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    """Record what the turn was about to send the provider, then succeed."""

    SEEN_CONTEXTS.append(list(context.messages))
    yield AssistantStartEvent()
    yield AssistantDoneEvent(
        message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="done")
    )


def plain(renderable: Any) -> str:
    rows = getattr(renderable, "renderables", None)
    if rows is not None:
        return "\n".join(plain(r) for r in rows)
    inner = getattr(renderable, "renderable", None)
    if inner is not None:
        return plain(inner)
    text = getattr(renderable, "plain", None)
    return text if isinstance(text, str) else str(renderable)


def flat(text: str) -> str:
    """Collapse whitespace.

    THE RULER MATTERS HERE. ``markdown_lines`` wraps the echoed block to the
    replay width, so a plain ``sentence in commit`` finds nothing and reports
    ONE emission on the broken build, with two on screen. Measured — the first
    draft of the test for this issue passed against the unfixed renderer.
    """

    return " ".join(text.split())


def renderer() -> tuple[EventRenderer, list[str]]:
    commits: list[str] = []
    return (
        EventRenderer(
            commit=lambda r: commits.append(plain(r)),
            set_tail=lambda _s: None,
            width=80,
        ),
        commits,
    )


def mentions(commits: list[str], needle: str) -> list[str]:
    want = flat(needle)
    return [flat(c) for c in commits if want in flat(c)]


def message_text(message: object) -> str:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return ""
    return "".join(getattr(b, "text", "") or "" for b in content)


async def arm_1_live_vs_replay(tmp: str) -> str:
    print("=== ARM 1 — LIVE vs REPLAY for one failed turn ===")
    path = str(Path(tmp) / "arm1.jsonl")
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), path, cwd=tmp, session_id="m194-1"
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=Session(storage),
            stream_fn=truncated_stream,
        )
    )
    live_renderer, live_commits = renderer()
    harness.subscribe(live_renderer.on_agent_event)

    detail = ""
    try:
        await harness.prompt("hello there")
    except Exception as exc:  # noqa: BLE001 — the loop is expected to raise
        detail = str(exc)
    print(f"harness.prompt raised: {detail or '(nothing — the arm is broken)'}")

    live = mentions(live_commits, detail)
    print(f"LIVE turn emissions:  {len(live)}")
    for line in live:
        print(f"   | {line}")

    raw = Path(path).read_text(encoding="utf-8").splitlines()
    print(f"session file: 1 header + {len(raw) - 1} entries")
    for line in raw[1:]:
        msg = json.loads(line).get("message") or {}
        if msg.get("role") != "assistant":
            continue
        print(f"   stop_reason   = {msg.get('stop_reason')!r}")
        print(f"   error_message = {msg.get('error_message')!r}")
        print(f"   content       = {msg.get('content')!r}")

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
    messages = list(build_display_messages(await Session(reopened).get_branch()))
    replay_renderer, replay_commits = renderer()
    replay_renderer.replay(messages)
    replayed = mentions(replay_commits, detail)
    print(f"REPLAY emissions:     {len(replayed)}")
    for line in replayed:
        print(f"   | {line}")
    print()
    return detail


async def arm_2_does_the_model_see_it(tmp: str) -> None:
    print("=== ARM 2 — does the MODEL read the content copy? ===")
    path = str(Path(tmp) / "arm2.jsonl")
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), path, cwd=tmp, session_id="m194-2"
    )
    holder: dict[str, Any] = {"fn": truncated_stream}

    async def dispatch(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        async for event in holder["fn"](model, context, options):
            yield event

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=Session(storage),
            stream_fn=dispatch,
        )
    )
    with contextlib.suppress(Exception):  # the loop is expected to raise
        await harness.prompt("first")
    holder["fn"] = recording_stream
    await harness.prompt("second")

    sent = SEEN_CONTEXTS[-1]
    live_hits = [m for m in sent if "[error] " in message_text(m)]
    print(f"live next turn — messages the provider was sent: {len(sent)}")
    print(f"  carrying the '[error] ' copy: {len(live_hits)}")
    for m in live_hits:
        print(f"   | {message_text(m)[:90]}")

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
    resumed = build_session_context(await Session(reopened).get_branch()).messages
    resumed_hits = [m for m in resumed if "[error] " in message_text(m)]
    print(f"after a reload — messages a resumed turn would send: {len(resumed)}")
    print(f"  carrying the '[error] ' copy: {len(resumed_hits)}")
    print()


def arm_3_the_shape_already_on_disk() -> None:
    print("=== ARM 3 — the shape every released session already holds ===")
    error = "the wheels came off"
    persisted = AssistantMessage(
        content=[TextContent(text=f"[error] {error}")],
        stop_reason="error",
        error_message=error,
    )
    replay_renderer, commits = renderer()
    replay_renderer.replay([persisted])
    hits = mentions(commits, error)
    print("no writer is involved — this message is read, never written")
    print(f"REPLAY emissions:     {len(hits)}")
    for line in hits:
        print(f"   | {line}")
    print()


async def bare_timeout_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    """Raise an exception carrying NO message, so ``str(exc)`` is ``""``.

    A bare ``TimeoutError()`` is the cheapest real instance; ``asyncio``'s own
    timeouts and any third-party ``raise SomeSentinel()`` have the same shape.
    Nothing here is hand-written — the harness synthesises the failure message
    from this exception exactly as it does for every other one.
    """

    yield AssistantStartEvent()
    raise TimeoutError


async def arm_4_an_exception_with_no_message(tmp: str) -> None:
    print("=== ARM 4 — the failure whose str(exc) is '' ===")
    path = str(Path(tmp) / "arm4.jsonl")
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), path, cwd=tmp, session_id="m194-4"
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=Session(storage),
            stream_fn=bare_timeout_stream,
        )
    )
    live_renderer, live_commits = renderer()
    harness.subscribe(live_renderer.on_agent_event)

    try:
        await harness.prompt("hello there")
    except BaseException as exc:  # noqa: BLE001 — the loop is expected to raise
        print(f"escaped the loop: {type(exc).__name__}  str(exc)={str(exc)!r}")

    # No error sentence to search for — that is the whole point of this arm —
    # so count every non-empty commit the ASSISTANT turn produced. The user
    # echo is dropped by the prefix test.
    def assistant_only(commits: list[str]) -> list[str]:
        return [flat(c) for c in commits if flat(c) and not flat(c).startswith("»")]

    live = assistant_only(live_commits)
    print(f"LIVE turn emissions:  {len(live)}")
    for line in live:
        print(f"   | {line}")

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
    messages = list(build_display_messages(await Session(reopened).get_branch()))
    for message in messages:
        if getattr(message, "stop_reason", None):
            print(
                f"   persisted: stop_reason={message.stop_reason!r} "
                f"error_message={getattr(message, 'error_message', None)!r} "
                f"content={message_text(message)!r}"
            )
    replay_renderer, replay_commits = renderer()
    replay_renderer.replay(messages)
    replayed = assistant_only(replay_commits)
    print(f"REPLAY emissions:     {len(replayed)}")
    for line in replayed:
        print(f"   | {line}")
    print()


async def arm_5_the_export_road(tmp: str) -> None:
    print("=== ARM 5 — export_to_html, NOT fixed here ===")
    path = str(Path(tmp) / "arm5.jsonl")
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), path, cwd=tmp, session_id="m194-5"
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=Session(storage),
            stream_fn=truncated_stream,
        )
    )
    with contextlib.suppress(Exception):  # the loop is expected to raise
        await harness.prompt("hello there")

    out = str(Path(tmp) / "arm5.html")
    html = Path(harness.export_to_html(out)).read_text(encoding="utf-8")
    print(f"'[error] ' occurrences: {html.count('[error] ')}")
    print(f"'✖' occurrences:        {html.count('✖')}")
    for line in html.splitlines():
        if "[error]" in line:
            print(f"   | {line.strip()[:110]}")
    print(
        "the exporter has no notion of error_message/stop_reason — "
        "`grep -rn 'error_message\\|stop_reason' .../_export_html/` is empty"
    )
    print()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        await arm_1_live_vs_replay(tmp)
        await arm_2_does_the_model_see_it(tmp)
    arm_3_the_shape_already_on_disk()
    with tempfile.TemporaryDirectory() as tmp:
        await arm_4_an_exception_with_no_message(tmp)
        await arm_5_the_export_road(tmp)


if __name__ == "__main__":
    asyncio.run(main())
