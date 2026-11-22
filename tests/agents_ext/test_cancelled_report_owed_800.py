"""#800 (ADR-0307 D1-D2): a cancelled delegation's report is its call's result, and is owed until placed.

The ``agent`` tool raises ``ToolCancelled`` with the report, so the loop writes it as
the cancelled call's result; the extension records its execution receipt
(``AgentsExtension._owed``) and checks it at every
``before_agent_start`` against the session as COMMITTED: retired when the call's own
result is there and is not ``loop.CUT_TEXT`` (whatever a hook made of it), or when a
copy it injected is there; injected again as a user message otherwise.

Each test drives the real ``agent`` tool through a real ``AgentHarness`` on a JSONL
session (the shapes of ``.omc/probes/612/design-r5/l1/l1_owed_probe.py``): a parked
fake child, a scripted parent model whose requests are recorded, an Esc
(``abort(withdraw_unanswered=True)``), and, where named, a second abort that lands in
a hook or the append. The file is re-read from disk after the last prompt.

Contract r5 §12.3, L1. Fail on 6dd682cd: test 2 (a redacting hook: the base also
sends the raw report as a user message), test 3 (an annotating hook: the base sends
two), test 4 (the copy's append fails: the base's file ends with no copy), and the
record of another session (the base's queue sends the report into whatever session
comes next; r5 listed this one as a guard, and its own probe row says the base sends
``user``). The guards pass on both: a second cancel (K1) in a ``tool_result`` or a
pre-commit ``message_end`` hook still gives one user copy at the next prompt and
none injected at the one after, a failed prompt keeps it, a session-less harness
gets one copy, a raising renderer is the bare cancel. The second cancel parked in
the report's own append has no base run (there the result is the cut text, which
that gate never parks).

The "fix round 1" section is Codex's review C-5 to C-9 and verify round 1's F2: each test
there names how it fails on the previous head ``deb7cdd7``, or is a guard that kills a mutant
of the round's lines (ADR-0307, How it is held).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aelix_agent_core import loop as core_loop
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions, TranscriptOutcome
from aelix_agent_core.harness.hooks import (
    BeforeAgentStartHookEvent,
    BeforeAgentStartResult,
    MessageEndEventResult,
    ToolExecutionEndHookEvent,
    ToolResultPatch,
)
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.session.fs import NotAttempted
from aelix_agent_core.session.transcript import MessageAppend
from aelix_agent_core.types import AgentEvent, AgentMessage
from aelix_agents import extension as agents_extension
from aelix_agents.aggregate import CANCEL_REPORT_HEAD
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import Model

from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_stopped_child_reports_604 import (
    _message_text,
    _Parking,
    _scripted,
    _tool_call,
)
from tests.agents_ext.test_tool_and_security import _bench, _write_profile
from tests.harness._cancelled_turn import MODEL, Provider, finished_tool

CUT = core_loop._CUT_TEXT  # the alias exists on the base too; ``CUT_TEXT`` is #800's name
REDACTED = "[redacted]"
# ASCII words of ``CANCEL_REPORT_HEAD``: a JSONL line escapes its non-ASCII characters.
_HEAD_WORDS = "stopped when the turn was cancelled"
ANNOTATED = "[annotated] "
NOTICE = "[aelix system report — not user input] The `agent` call was cancelled."
MASKING_MARKER = "original delegation detail 839"


def _owed(run: _Run) -> dict[Any, Any]:
    """The extension's owed records; none on the base, which has no such table."""

    return getattr(run.bench.ext, "_owed", {})


def _kinds(messages: list[Any]) -> list[str]:
    """The role of each message that carries the report, marked when a hook rewrote it."""

    out = []
    for message in messages:
        text = _message_text(message)
        if text == REDACTED:
            out.append(f"{message.role}(redacted)")
        elif text.startswith(ANNOTATED):
            out.append(f"{message.role}(annotated)")
        elif text == NOTICE or CANCEL_REPORT_HEAD in text:
            out.append(message.role)
    return out


def _user_and_tool_rows(messages: list[Any]) -> list[tuple[str, str]]:
    return [(m.role, _message_text(m)) for m in messages if m.role in {"user", "toolResult"}]


def _refuse_rows(role: str, marker: str = "") -> Any:
    original = LocalFileSystem.append_file

    async def append(fs: LocalFileSystem, path: str, line: str) -> None:
        if f'"role":"{role}"' in line.replace(" ", "") and marker in line:
            raise NotAttempted("simulated append refusal")
        await original(fs, path, line)

    return patch.object(LocalFileSystem, "append_file", append)


@dataclass
class _Run:
    harness: AgentHarness
    bench: Any
    seen: list[list[Any]]
    path: Path | None
    session: Any
    gated: asyncio.Event
    released: asyncio.Event = field(default_factory=asyncio.Event)

    def request(self, n: int) -> list[str]:
        """What carries the report in the n-th model request (1 is the delegating one)."""

        return _kinds(self.seen[n - 1])

    async def file(self) -> list[str]:
        return _kinds(await self.reopen())

    async def reopen(self) -> list[Any]:
        assert self.path is not None
        reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(self.path)))
        return list((await reopened.build_context()).messages)


async def _cancelled(
    tmp_path: Path,
    *,
    rewrite: str | None = None,
    second: str | None = None,
    sessionless: bool = False,
    later_handlers: list[Any] | None = None,
    first: AssistantMessage | None = None,
    prior: list[Any] | None = None,
    replies: list[AssistantMessage] | None = None,
    before_abort: Any = None,
    before_prompt: Any = None,
    abort_with: Any = None,
) -> _Run:
    """Delegate, Esc while the child runs, and (``second``) abort again where it parks.

    ``rewrite``: a ``tool_result`` hook that redacts or annotates every result.
    ``second``: ``"tool_result"`` (the second abort in a ``tool_result`` hook) or
    ``"message_end"`` (in the result's ``message_end`` before the commit) or
    ``"append"`` (in the report's JSONL append, before its bytes).
    ``first``: the delegating answer (default: one ``agent`` call, ``call-1``).
    ``prior``: messages written to the session before the prompt.
    ``replies``: the answers to the requests after the first (default: "noted").
    ``before_abort``: called with the run once the child runs, before the Esc.
    """

    channel = _Parking()
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path, channel=channel)  # type: ignore[arg-type]
    session, _repo, path = await _parent(tmp_path)
    for message in prior or []:
        await session.append_message(message)
    bench.ext.session = lambda: None if sessionless else session
    seen: list[list[Any]] = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            session=None if sessionless else session,
            extensions=[bench.extension],
            runtime=bench.runtime,
            stream_fn=_scripted(first or _tool_call({"profile": "scout", "task": "look"}), seen)
            if replies is None
            else _replying([first or _tool_call({"profile": "scout", "task": "look"}), *replies], seen),
        )
    )
    run = _Run(harness, bench, seen, None if sessionless else path, session, asyncio.Event())

    async def park(*_args: Any) -> None:
        run.gated.set()
        await run.released.wait()

    if rewrite is not None:

        async def rewriting(event: Any, _ctx: Any) -> ToolResultPatch:
            text = {"redact": REDACTED, "cut": CUT}.get(
                rewrite, ANNOTATED + _message_text(event)
            )
            return ToolResultPatch(content=[TextContent(text=text)])

        harness.hooks.on("tool_result", rewriting)
    if second == "tool_result":

        async def parked_result(_event: Any, _ctx: Any) -> None:
            await park()

        harness.hooks.on("tool_result", parked_result)
    if second == "message_end":

        async def parked_commit(event: Any, _ctx: Any) -> None:
            if not run.gated.is_set() and event.message.role == "toolResult":
                await park()

        harness.hooks.on("message_end", parked_commit)
    for handler in later_handlers or []:
        harness.hooks.on("before_agent_start", handler)
    append = LocalFileSystem.append_file

    async def gated_append(fs: Any, target: str, text: str) -> None:
        if second == "append" and '"toolResult"' in text.replace(" ", "") and _HEAD_WORDS in text:
            await park()
        await append(fs, target, text)

    if before_prompt is not None:
        await before_prompt(run)
    with patch.object(LocalFileSystem, "append_file", gated_append):
        turn = asyncio.ensure_future(harness.prompt("delegate"))
        await asyncio.wait_for(channel.arrived.wait(), 10)
        if before_abort is not None:
            before_abort(run)
        if abort_with is None:
            await harness.abort(withdraw_unanswered=True)
        else:
            await abort_with(run)
        if second is not None:
            await asyncio.wait_for(run.gated.wait(), 10)
            await harness.abort()
        await asyncio.wait_for(turn, 10)
    run.released.set()
    return run


def _replying(answers: list[AssistantMessage], seen: list[list[Any]]) -> Any:
    """A scripted model that answers request n with ``answers[n - 1]``, then "noted"."""

    from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent

    async def stream(_model: Any, context: Any, _options: Any) -> Any:
        seen.append(list(context.messages))
        n = len(seen)
        reply = (
            answers[n - 1]
            if n <= len(answers)
            else AssistantMessage(content=[TextContent(text="noted")], stop_reason="end_turn")
        )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=reply)

    return stream


# === tests that fail on the base ==================================================


async def test_a_redacting_hook_leaves_only_the_redacted_result(tmp_path: Path) -> None:
    """Contract r5 §12.3 test 2. On 6dd682cd the next request also carries the raw
    report as a user message, after the hook redacted the call's (cut) result."""

    run = await _cancelled(tmp_path, rewrite="redact")
    await run.harness.prompt("next")
    await run.harness.prompt("again")

    assert run.request(2) == ["toolResult(redacted)"]
    assert run.request(3) == ["toolResult(redacted)"]
    assert await run.file() == ["toolResult(redacted)"]


@pytest.mark.parametrize(
    "mode", ["cut", "raises", "tool_result", "message_end", "nameless", "message_end_raises"]
)
async def test_fallback_uses_only_the_final_mask_or_a_cancellation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """#839: exact cut, failed hook, and failed appends after either hook's mask."""

    render = agents_extension.render_cancel_report
    monkeypatch.setattr(
        agents_extension, "render_cancel_report", lambda *a, **kw: f"{render(*a, **kw)}\n{MASKING_MARKER}"
    )
    observed: list[str] = []

    def setup(run: _Run) -> None:
        def observe(event: AgentEvent) -> None:
            if event.type == "tool_execution_end":
                observed.append(_message_text(event.result))

        async def fail(_event: Any, _ctx: Any) -> None:
            raise RuntimeError("redaction failed")

        async def redact_message(event: Any, _ctx: Any) -> MessageEndEventResult | None:
            if event.message.role == "toolResult":
                return MessageEndEventResult(
                    message=ToolResultMessage(
                        tool_call_id=event.message.tool_call_id,
                        tool_name="" if mode == "nameless" else event.message.tool_name,
                        content=[TextContent(text=REDACTED)],
                        is_error=True,
                    ),
                )
            return None

        run.harness.subscribe(observe)
        if mode == "raises":
            run.harness.hooks.on("tool_result", fail)
        if mode in ("message_end", "nameless", "message_end_raises"):
            run.harness.hooks.on("message_end", redact_message)
        if mode == "message_end_raises":
            async def fail_after_mask(event: Any, _ctx: Any) -> None:
                if event.message.role == "toolResult":
                    assert _message_text(event.message) == REDACTED
                    raise RuntimeError("second message-end handler failed")

            run.harness.hooks.on("message_end", fail_after_mask)

    append = LocalFileSystem.append_file
    refused: list[str] = []

    async def refuse_mask(fs: Any, target: str, text: str) -> None:
        if mode != "nameless" and REDACTED in text and '"toolResult"' in text:
            refused.append(text)
            raise NotAttempted("failed append after masking")
        await append(fs, target, text)

    rewrite = {"cut": "cut", "tool_result": "redact"}.get(mode)
    with patch.object(LocalFileSystem, "append_file", refuse_mask):
        run = await _cancelled(tmp_path, rewrite=rewrite, before_abort=setup)
    assert len(run.seen) == 1, "cancellation started no extra turn"
    expected = REDACTED if mode in ("tool_result", "message_end", "nameless") else NOTICE
    carrier = "toolResult" if mode == "nameless" else "user"
    copy_refusals: list[str] = []

    async def refuse_copy(fs: Any, target: str, text: str) -> None:
        if '"role":"user"' in text.replace(" ", "") and (REDACTED in text or MASKING_MARKER in text):
            copy_refusals.append(text)
            raise NotAttempted("failed first copy append")
        await append(fs, target, text)

    if mode == "tool_result":
        with patch.object(LocalFileSystem, "append_file", refuse_copy):
            await run.harness.prompt("next")
        assert len(copy_refusals) == 1
        await run.harness.prompt("retry")
    else:
        await run.harness.prompt("next")
    await run.harness.prompt("again")
    assert run.path is not None
    assert MASKING_MARKER not in run.path.read_text(encoding="utf-8")
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(run.path)))
    for messages in (*run.seen[1:], (await reopened.build_context()).messages):
        texts = [_message_text(message) for message in messages]
        assert all(MASKING_MARKER not in text and CANCEL_REPORT_HEAD not in text for text in texts)
        assert texts.count(expected) == 1, texts
        assert sum(
            message.role == carrier and _message_text(message) == expected for message in messages
        ) == 1
        assert texts.count(NOTICE) == (1 if expected == NOTICE else 0)
    assert len(refused) == (1 if mode in ("tool_result", "message_end") else 0)
    if mode in ("message_end", "nameless", "message_end_raises"):
        assert len(observed) == 1 and MASKING_MARKER in observed[0]
    else:
        assert observed == [REDACTED if mode == "tool_result" else CUT]


async def test_an_annotating_hook_leaves_one_result_and_no_copy(tmp_path: Path) -> None:
    """Contract r5 §12.3 test 3: identity, not text — the annotated result retires
    the record. On 6dd682cd the request carries the annotated cut text and the
    report as a user message."""

    run = await _cancelled(tmp_path, rewrite="annotate")
    await run.harness.prompt("next")

    assert run.request(2) == ["toolResult(annotated)"]
    assert await run.file() == ["toolResult(annotated)"]
    assert _owed(run) == {}


async def test_a_copy_whose_append_fails_is_injected_again_and_the_file_keeps_one(
    tmp_path: Path,
) -> None:
    """Contract r5 §12.3 test 4. A second abort in the ``tool_result`` hook keeps the
    report out of the call's result (K1), so the next prompt injects the copy; its
    JSONL append fails (``core.py`` logs and goes on). The prompt after injects it
    again, and the one after that reads it from the file and injects nothing. On
    6dd682cd the queued report is drained once and lost: the file holds no copy."""

    run = await _cancelled(tmp_path, second="tool_result")
    append = LocalFileSystem.append_file
    refusals: list[str] = []

    async def refused(fs: Any, target: str, text: str) -> None:
        if '"role":"user"' in text.replace(" ", "") and "The `agent` call" in text:
            refusals.append(text)
            raise NotAttempted("a simulated append failure")
        await append(fs, target, text)

    with patch.object(LocalFileSystem, "append_file", refused):
        await run.harness.prompt("p1")
    assert len(refusals) == 1, "the copy's append was refused"
    await run.harness.prompt("p2")
    await run.harness.prompt("p3")

    assert run.request(2) == ["user"]
    assert run.request(3) == ["user"], "the copy that never reached the file is injected again"
    assert run.request(4) == ["user"], "read from the file; none injected beside it"
    assert await run.file() == ["user"]
    assert _owed(run) == {}


# === fix round 1 (Codex review C-5 to C-9, verify F2): fail on deb7cdd7 ==========


def _redact_copies(run: _Run) -> None:
    """A ``message_end`` hook that rewrites every report copy (after the extension's)."""

    async def rewrite(event: Any, _ctx: Any) -> MessageEndEventResult | None:
        message = event.message
        if message.role == "user" and _message_text(message) == NOTICE:
            return MessageEndEventResult(message=UserMessage(content=[TextContent(text=REDACTED)]))
        return None

    run.harness.hooks.on("message_end", rewrite)


async def test_a_rewritten_copy_is_placed_once(tmp_path: Path) -> None:
    """Codex C-5. After a second cancel (K1) the copy goes out once, and a ``message_end``
    hook redacts it. The copy is placed by its session entry, not its text, so three
    prompts carry exactly one (redacted) copy and the record is retired. On deb7cdd7
    the check compared texts: one, two, then three redacted copies accumulated in the
    requests and the file."""

    run = await _cancelled(tmp_path, second="tool_result")
    _redact_copies(run)
    for number in range(1, 4):
        await run.harness.prompt(f"prompt {number}")
        assert run.request(number + 1) == ["user(redacted)"], f"prompt {number}"
    assert await run.file() == ["user(redacted)"]
    assert _owed(run) == {}


async def test_a_session_less_harness_after_one_abort_carries_the_report_once(
    tmp_path: Path,
) -> None:
    """Codex C-6, first shape. A harness with no session has nothing to write the
    call's result to or check it against: its report goes on the next-turn queue, as
    before #800 (ADR-0307 K1), and the call's result is the cut text. The next request
    carries the report once. On deb7cdd7 it carried it twice: the call's result and a
    user copy."""

    run = await _cancelled(tmp_path, sessionless=True)
    await run.harness.prompt("next")

    assert run.request(2) == ["user"]
    texts = [_message_text(m) for m in run.seen[1] if getattr(m, "role", "") == "toolResult"]
    assert texts == [CUT]
    assert _owed(run) == {}


async def test_a_session_less_harness_keeps_the_report_across_a_failed_prompt(
    tmp_path: Path,
) -> None:
    """Codex C-6, second shape. A second abort, then a prompt that a later
    ``before_agent_start`` handler fails: the queue restores what it drained, and the
    next prompt carries the report once. On deb7cdd7 the record was retired when the
    failed prompt injected it, and the next prompt carried nothing."""

    async def refuse(event: Any, _ctx: Any) -> None:
        if event.prompt == "fail here":
            raise RuntimeError("failed before the model")

    run = await _cancelled(
        tmp_path, second="tool_result", sessionless=True, later_handlers=[refuse]
    )
    with pytest.raises(Exception, match="failed before the model"):
        await run.harness.prompt("fail here")
    await run.harness.prompt("next")

    assert run.request(2) == ["user"]


@pytest.mark.parametrize("earlier", ["other-tool", "agent"])
async def test_an_earlier_result_under_the_same_call_id_is_not_the_carrier(
    tmp_path: Path, earlier: str
) -> None:
    """Codex C-7. An earlier call returned under ``call-1`` (another tool, Codex's
    shape, or an earlier delegation); the delegation reuses the id, and a second cancel
    (K1) keeps its report out of its result. The carrier is the first ``agent`` result
    for the id after where the call began, so the earlier one does not count and the
    next prompt carries the copy. On deb7cdd7 the earlier result was taken as proof
    of delivery and the report was lost."""

    prior = [
        UserMessage(content=[TextContent(text="prior turn")]),
        AssistantMessage(
            content=[ToolCallContent(tool_call_id="call-1", tool_name=earlier, input={})],
            stop_reason="tool_use",
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name=earlier,
            content=[TextContent(text="prior result")],
        ),
    ]
    run = await _cancelled(tmp_path, second="tool_result", prior=prior)
    await run.harness.prompt("next")

    assert run.request(2) == ["user"]
    assert await run.file() == ["user"]


async def test_an_earlier_unrelated_result_does_not_carry_the_report(tmp_path: Path) -> None:
    """Verify F2's probe P2: a tool result under another id is already on the branch,
    and a second cancel (K1) keeps the delegation's report out of its result. The next
    prompt carries the copy."""

    prior = [
        UserMessage(content=[TextContent(text="prior turn")]),
        AssistantMessage(
            content=[ToolCallContent(tool_call_id="call-0", tool_name="other-tool", input={})],
            stop_reason="tool_use",
        ),
        ToolResultMessage(
            tool_call_id="call-0",
            tool_name="other-tool",
            content=[TextContent(text="prior result")],
        ),
    ]
    run = await _cancelled(tmp_path, second="tool_result", prior=prior)
    await run.harness.prompt("next")

    assert run.request(2) == ["user"]


@pytest.mark.parametrize("reused", ["call-9", "call-1"])
async def test_a_later_calls_result_does_not_carry_the_report(tmp_path: Path, reused: str) -> None:
    """Verify F2 (mutant E8: the check without the call's id). A second cancel in the
    result's ``message_end`` leaves the delegation with no result; the next prompt's
    copy fails to reach the file, and that prompt's answer calls another tool, whose
    result is the first one after the delegation began, under another id or the
    delegation's own (``call-1``, reused). It is not the delegation's (not an
    ``agent`` result), so the prompt after carries the copy again and the file ends
    with one."""

    other = AssistantMessage(
        content=[ToolCallContent(tool_call_id=reused, tool_name="other-tool", input={})],
        stop_reason="tool_use",
    )
    run = await _cancelled(
        tmp_path, second="message_end", replies=[other], first=None
    )
    append = LocalFileSystem.append_file

    async def refused(fs: Any, target: str, text: str) -> None:
        if '"role":"user"' in text.replace(" ", "") and "The `agent` call" in text:
            raise NotAttempted("a simulated append failure")
        await append(fs, target, text)

    with patch.object(LocalFileSystem, "append_file", refused):
        await run.harness.prompt("p1")
    results = [
        m.tool_call_id
        for m in (await run.session.build_context()).messages
        if m.role == "toolResult"
    ]
    assert results == [reused], results
    await run.harness.prompt("p2")

    assert run.request(4) == ["user"], "the copy that never reached the file"
    assert await run.file() == ["user"]


@pytest.mark.parametrize(
    ("second", "later_id"),
    [("message_end", "call-9"), ("message_end", "call-1"), ("tool_result", "call-1")],
)
async def test_a_later_delegations_result_does_not_carry_the_report(
    tmp_path: Path, second: str, later_id: str
) -> None:
    """Verify F2 (mutant E8) and the first-result rule: after a second cancel (K1),
    a later delegation's result is written to the session before the next prompt —
    under another id while the cancelled call has no result at all, or under the
    reused id after the cancelled call's cut result. Neither is the cancelled call's
    carrier, so the next prompt carries the copy."""

    run = await _cancelled(tmp_path, second=second)
    await run.session.append_message(UserMessage(content=[TextContent(text="later turn")]))
    await run.session.append_message(
        AssistantMessage(
            content=[ToolCallContent(tool_call_id=later_id, tool_name="agent", input={})],
            stop_reason="tool_use",
        )
    )
    await run.session.append_message(
        ToolResultMessage(
            tool_call_id=later_id, tool_name="agent", content=[TextContent(text="an envelope")]
        )
    )
    await run.harness.prompt("next")

    assert run.request(2) == ["user"]


async def test_a_reused_id_does_not_replace_the_owed_mask(tmp_path: Path) -> None:
    """#839 report verification R1: a real later agent call reuses a still-owed ID."""
    later = _tool_call({"profile": "unknown-profile", "task": "later"})
    with _refuse_rows("toolResult"):
        run = await _cancelled(tmp_path, rewrite="redact", replies=[later])
    reached, release = asyncio.Event(), asyncio.Event()

    async def after_later_result(event: Any) -> None:
        if event.type == "message_end" and event.message.role == "toolResult":
            reached.set()
            await release.wait()

    off = run.harness.subscribe(after_later_result)
    with _refuse_rows("user", REDACTED):
        turn = asyncio.create_task(run.harness.prompt("next"))
        try:
            await asyncio.wait_for(reached.wait(), 10)
            await run.harness.abort()
        finally:
            off()
            release.set()
            await asyncio.wait_for(turn, 10)

    await run.harness.prompt("retry")
    await run.harness.prompt("again")
    for messages, extra in ((run.seen[-2], []), (run.seen[-1], ["again"]), (await run.reopen(), ["again"])):
        rows = _user_and_tool_rows(messages)
        assert [text for role, text in rows if role == "user"] == [
            "delegate", "next", REDACTED, "retry", *extra,
        ]
        (later_text,) = [text for role, text in rows if role == "toolResult"]
        assert later_text.startswith("Delegation refused: unknown agent profile")


@pytest.mark.parametrize("rewrite", [None, "redact"])
async def test_a_finalized_report_survives_an_abort_in_agent_end(
    tmp_path: Path, rewrite: str | None
) -> None:
    """#839 report verification R1: the result has reduced, its append was refused."""
    observed: list[str] = []

    def interrupt_closeout(run: _Run) -> None:
        async def listener(event: Any) -> None:
            if event.type == "agent_end" and not observed:
                observed.extend(_message_text(m) for m in event.messages if m.role == "toolResult")
                run.gated.set()
                await run.released.wait()

        run.harness.subscribe(listener)

    async def abort_twice(run: _Run) -> None:
        await run.harness.abort(withdraw_unanswered=True)
        await asyncio.wait_for(run.gated.wait(), 10)
        await run.harness.abort()

    with _refuse_rows("toolResult"):
        run = await _cancelled(
            tmp_path, rewrite=rewrite, before_abort=interrupt_closeout, abort_with=abort_twice
        )
    (expected,) = observed
    if rewrite:
        assert expected == REDACTED
    else:
        assert _HEAD_WORDS in expected
    await run.harness.prompt("next")
    await run.harness.prompt("again")
    assert _user_and_tool_rows(run.seen[1]) == [
        ("user", "delegate"), ("user", expected), ("user", "next")
    ]
    for messages in (run.seen[-1], await run.reopen()):
        assert _user_and_tool_rows(messages) == [
            ("user", "delegate"), ("user", expected), ("user", "next"), ("user", "again")
        ]


async def test_a_cancel_right_after_the_copy_is_written_still_places_it(tmp_path: Path) -> None:
    """The session assigns the copy's entry at its commit. A listener parks on its
    ``message_end`` (after the write) and the prompt is aborted there: the copy is on
    the branch, so the prompt after carries that one and injects none."""

    run = await _cancelled(tmp_path, second="tool_result")
    parked, release = asyncio.Event(), asyncio.Event()

    async def listener(event: Any) -> None:
        message = getattr(event, "message", None)
        if (
            getattr(event, "type", "") == "message_end"
            and getattr(message, "role", "") == "user"
            and _message_text(message) == NOTICE
            and not parked.is_set()
        ):
            parked.set()
            await release.wait()

    run.harness.subscribe(listener)
    p1 = asyncio.ensure_future(run.harness.prompt("p1"))
    try:
        await asyncio.wait_for(parked.wait(), 10)
        await run.harness.abort()
    finally:
        release.set()
        await asyncio.wait_for(p1, 10)
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"], "p2 carries the copy p1 wrote, and only that one"
    assert await run.file() == ["user"]
    assert _owed(run) == {}


async def test_a_committed_copy_survives_two_aborts_without_duplication(tmp_path: Path) -> None:
    """The copy receipt survives an interrupted write and interrupted close-out."""
    with _refuse_rows("toolResult"):
        run = await _cancelled(tmp_path, rewrite="redact")
    append = LocalFileSystem.append_file
    written, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def park_after_bytes(fs: LocalFileSystem, path: str, line: str) -> None:
        await append(fs, path, line)
        if '"role":"user"' in line.replace(" ", "") and REDACTED in line:
            written.set()
            await release.wait()

    async def park_before_closeout_hooks(event: Any) -> None:
        if event.type == "agent_end":
            closing.set()
            await release.wait()

    off = run.harness.subscribe(park_before_closeout_hooks)
    with patch.object(LocalFileSystem, "append_file", park_after_bytes):
        turn = asyncio.create_task(run.harness.prompt("interrupted"))
        try:
            await asyncio.wait_for(written.wait(), 10)
            await run.harness.abort()
            await asyncio.wait_for(closing.wait(), 10)
            await run.harness.abort()
        finally:
            off()
            release.set()
            await asyncio.wait_for(turn, 10)
    assert len(run.seen) == 1
    await run.harness.prompt("next")
    expected = [
        ("user", "delegate"), ("user", REDACTED), ("user", "interrupted"), ("user", "next")
    ]
    assert _user_and_tool_rows(run.seen[-1]) == _user_and_tool_rows(await run.reopen()) == expected


async def test_a_failed_read_of_the_session_never_duplicates_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C-8, his shape. One abort; the next prompt's first read of the session
    fails, and the prompt is sent again. Each request carries the report once, as the
    call's result. On deb7cdd7 the check read nothing, injected the copy, and the
    request read the committed result too: ``['toolResult', 'user']``."""

    run = await _cancelled(tmp_path)
    original = Session.build_context
    failed = False

    async def once(session: Any) -> Any:
        nonlocal failed
        if session is run.session and not failed:
            failed = True
            raise OSError("one read failed")
        return await original(session)

    monkeypatch.setattr(Session, "build_context", once)
    try:
        await run.harness.prompt("next")
    except Exception:  # noqa: BLE001 — the failed read failed this prompt
        await run.harness.prompt("retry")
    assert failed
    assert [run.request(n) for n in range(2, len(run.seen) + 1)] == [["toolResult"]]
    assert await run.file() == ["toolResult"]


async def test_a_branch_the_check_cannot_read_fails_the_prompt_and_keeps_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C-8, fail closed: the session cannot be read from the Esc on, so the run's
    end cannot retire the record; the next prompt's check raises before anything is
    sent, in its own words, and the record stays. Once the session can be read, the
    report is found as the call's result and no copy goes out. On deb7cdd7 the check
    swallowed the error and the prompt failed later, on the request's own read."""

    original = Session.get_branch
    broken: list[Any] = []

    async def unreadable(session: Any, from_id: str | None = None) -> Any:
        if session in broken:
            raise OSError("the disk is gone")
        return await original(session, from_id)

    monkeypatch.setattr(Session, "get_branch", unreadable)
    run = await _cancelled(tmp_path, before_abort=lambda r: broken.append(r.session))
    assert [record.call_id for record in _owed(run).values()] == ["call-1"]
    with pytest.raises(Exception, match="cancelled delegation's report"):
        await run.harness.prompt("next")
    assert len(run.seen) == 1, "the failed prompt made a request"
    assert [record.call_id for record in _owed(run).values()] == ["call-1"]
    broken.clear()
    await run.harness.prompt("retry")

    assert run.request(2) == ["toolResult"]
    assert _owed(run) == {}


@pytest.mark.parametrize("ui", [False, True])
async def test_a_report_whose_write_fails_is_told_and_reaches_the_next_prompt(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    ui: bool,
) -> None:
    """Codex C-9, and verify round 2 F3. One abort, and the report's own JSONL append
    fails before its bytes: the file has no report until the next prompt, so the run's
    end tells it, naming the call, and the next prompt carries the report once as a
    user copy, which the file then holds. Told at DEBUG, and as one stderr line only
    with no UI (``ui``: one is bound before the Esc), never as a WARNING, which with no
    handler reaches ``logging.lastResort`` and painted over the live TUI. On deb7cdd7
    the failure was a DEBUG line only; on 9acd9ea0 it was a WARNING and no stderr line."""

    append = LocalFileSystem.append_file

    async def refused(fs: Any, target: str, text: str) -> None:
        if '"toolResult"' in text.replace(" ", "") and _HEAD_WORDS in text:
            raise NotAttempted("a simulated append failure")
        await append(fs, target, text)

    def bind_ui(run: _Run) -> None:
        if ui:
            run.bench.runtime.bind_ui(run.bench.ui)

    with caplog.at_level(logging.DEBUG, logger=agents_extension.logger.name):
        with patch.object(LocalFileSystem, "append_file", refused):
            run = await _cancelled(tmp_path, before_abort=bind_ui)
        assert await run.file() == []
    told = [r.getMessage() for r in caplog.records if "call-1" in r.getMessage()]
    assert [t for t in told if "next prompt" in t] != [], told
    assert {r.levelno for r in caplog.records if "call-1" in r.getMessage()} == {logging.DEBUG}
    stderr = [line for line in capsys.readouterr().err.splitlines() if line]
    assert stderr == ([] if ui else [t for t in told if "next prompt" in t])
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=agents_extension.logger.name):
        await run.harness.prompt("next")
    assert [r for r in caplog.records if "call-1" in r.getMessage()] == [], "placed: not told"
    assert capsys.readouterr().err == ""

    assert run.request(2) == ["user"]
    assert await run.file() == ["user"]


async def test_a_report_that_lands_as_the_result_is_not_warned_about(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """One abort whose report is the call's result in the file: the run's end logs
    nothing about it and writes nothing to stderr."""

    with caplog.at_level(logging.DEBUG, logger=agents_extension.logger.name):
        run = await _cancelled(tmp_path)
    assert await run.file() == ["toolResult"]
    assert [r for r in caplog.records if "cancelled delegation" in r.getMessage()] == []
    assert "cancelled delegation" not in capsys.readouterr().err


# === fix round 2 (verify round 2 F1, F3, F4): fail on 9acd9ea0 unless a guard =====


@pytest.mark.parametrize("to", ["root", "root-masked", "second"])
async def test_a_copy_after_the_leaf_moved_off_the_carrier_is_placed_once(
    tmp_path: Path, to: str
) -> None:
    """Verify round 2 F1. One Esc, so the report is the call's result; then the leaf is
    moved off that result with ``navigate_tree`` to a user message (pi's tree edit),
    and three prompts follow. The first carries one copy and the others carry that
    copy from the file. ``root``: the first user message, whose parent is no entry,
    so the copy is the branch's first entry; on 9acd9ea0 the second prompt carried a
    second copy, ``['user', 'user']``, and the file kept both. ``second`` (a guard): the
    second user message, so the copy follows an entry."""

    prior = (
        [
            UserMessage(content=[TextContent(text="earlier")]),
            AssistantMessage(content=[TextContent(text="earlier answer")], stop_reason="end_turn"),
        ]
        if to == "second"
        else None
    )
    run = await _cancelled(tmp_path, prior=prior, rewrite="redact" if to == "root-masked" else None)
    users = [
        entry.id
        for entry in await run.session.get_branch()
        if getattr(entry, "type", "") == "message" and entry.message.role == "user"
    ]
    await run.harness.navigate_tree(users[-1] if to == "second" else users[0])
    assert (await run.session.get_leaf_id() is None) is (to != "second")
    carrier = "user(redacted)" if to == "root-masked" else "user"
    for number in range(1, 4):
        await run.harness.prompt(f"rephrased {number}")
        assert run.request(number + 1) == [carrier], f"prompt {number}"
    assert await run.file() == [carrier]
    assert _owed(run) == {}


async def test_a_committed_mask_is_the_only_carrier_with_unreadable_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#839 report verification R1: leaf lookup fails at execute and tool end."""

    async def unreadable_leaf(_session: Any) -> Any:
        raise OSError("the leaf cannot be read")

    def with_unreadable_leaf(method: Any) -> Any:
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            with patch.object(Session, "get_leaf_id", unreadable_leaf):
                return await method(*args, **kwargs)

        return wrapped

    ext = agents_extension.AgentsExtension
    for name in ("_execute", "_on_tool_execution_end"):
        monkeypatch.setattr(ext, name, with_unreadable_leaf(getattr(ext, name)))
    run = await _cancelled(tmp_path, rewrite="redact")
    await run.harness.prompt("next")
    expected = [("user", "delegate"), ("toolResult", REDACTED), ("user", "next")]
    assert _user_and_tool_rows(run.seen[1]) == _user_and_tool_rows(await run.reopen()) == expected
    assert _owed(run) == {}


class _LegacyWriterSession(Session):
    def __init__(self, storage: Any) -> None:
        super().__init__(storage)
        self.writes: list[tuple[str, str]] = []

    async def append_message(self, message: AgentMessage) -> str:
        self.writes.append((message.role, _message_text(message)))
        return await super().append_message(message)


class _LegacyReceiptSession(_LegacyWriterSession):
    async def append_message_receipt(self, message: AgentMessage) -> MessageAppend:
        self.writes.append((message.role, _message_text(message)))
        return await super().append_message_receipt(replace(message))


@pytest.mark.parametrize("transcript", [False, True], ids=["classic", "transcript"])
@pytest.mark.parametrize("at_native_append", [False, True], ids=["override-return", "native-append"])
@pytest.mark.parametrize("writer_text", [REDACTED, "host copy"], ids=["copy", "rewrite"])
async def test_interrupted_legacy_writer_delivers_the_stored_result_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transcript: bool,
    at_native_append: bool,
    writer_text: str,
) -> None:
    """A base-signature writer can forward a copy and be cancelled after its bytes land."""
    landed = asyncio.Event()
    release = asyncio.Event()
    parent = _parent

    def copy_message(message: AgentMessage) -> AgentMessage:
        if isinstance(message, ToolResultMessage) and writer_text != REDACTED:
            return replace(message, content=[TextContent(text=writer_text)], timestamp=0.0)
        return replace(message)

    async def hold() -> None:
        landed.set()
        await release.wait()

    class LegacySession(Session):
        async def append_message(self, message: AgentMessage) -> str:
            entry_id = await super().append_message(copy_message(message))
            if not at_native_append and message.role == "toolResult":
                await hold()
            return entry_id

        async def append_message_receipt(self, message: AgentMessage) -> MessageAppend:
            receipt = await super().append_message_receipt(copy_message(message))
            if not at_native_append and message.role == "toolResult":
                await hold()
            return receipt

    async def legacy_parent(path: Path) -> tuple[LegacySession, Any, Path]:
        session, repo, file = await parent(path)
        return LegacySession(session.get_storage()), repo, file

    async def listen(run: _Run) -> None:
        if transcript:
            run.harness.subscribe_transcript(lambda *_: None)

    async def abort_twice(run: _Run) -> None:
        await run.harness.abort(withdraw_unanswered=True)
        try:
            await asyncio.wait_for(landed.wait(), 5)
            await run.harness.abort()
        finally:
            release.set()

    if at_native_append:
        append_file = LocalFileSystem.append_file

        async def hold_after_append(fs: LocalFileSystem, path: str, text: str) -> None:
            await append_file(fs, path, text)
            if '"role":"toolResult"' in text.replace(" ", ""):
                await hold()

        monkeypatch.setattr(LocalFileSystem, "append_file", hold_after_append)
    monkeypatch.setattr(f"{__name__}._parent", legacy_parent)
    run = await _cancelled(tmp_path, rewrite="redact", before_prompt=listen, abort_with=abort_twice)
    expected = [("user", "delegate"), ("toolResult", writer_text)]
    for prompt in ("next", "again"):
        await run.harness.prompt(prompt)
        expected.append(("user", prompt))
        assert _user_and_tool_rows(run.seen[-1]) == _user_and_tool_rows(await run.reopen()) == expected


async def test_an_ordinary_extension_keeps_its_original_hook_shapes() -> None:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=MODEL,
            stream_fn=Provider([("call-a", "one")]),
            tools=[finished_tool("one", "first")],
        )
    )
    order: list[str] = []
    hook_payloads: list[dict[str, Any]] = []
    injected = BeforeAgentStartResult(None, "updated")
    assert asdict(injected) == {"messages": None, "system_prompt": "updated"}

    async def before_start(_event: Any, _ctx: Any) -> BeforeAgentStartResult:
        order.append("before")
        return injected

    async def tool_end(event: ToolExecutionEndHookEvent, _ctx: Any) -> None:
        order.append("tool end")
        hook_payloads.append(asdict(event))

    async def message_end(event: Any, _ctx: Any) -> None:
        if isinstance(event.message, ToolResultMessage):
            order.append("result message end")

    harness.hooks.on("before_agent_start", before_start)
    harness.hooks.on("tool_execution_end", tool_end)
    harness.hooks.on("message_end", message_end)
    reduced = await harness.hooks.emit(BeforeAgentStartHookEvent(prompt="start"))
    assert type(reduced) is BeforeAgentStartResult
    assert reduced == injected
    order.clear()
    await harness.prompt("start")
    assert order == ["before", "tool end", "result message end"]
    assert hook_payloads == [
        {
            "type": "tool_execution_end",
            "tool_call_id": "call-a",
            "tool_name": "one",
            "is_error": False,
        }
    ]


async def test_a_transcript_listener_preserves_a_legacy_memory_writer() -> None:
    session = _LegacyWriterSession(MemorySessionStorage())
    harness = AgentHarness(AgentHarnessOptions(model=MODEL, stream_fn=Provider([]), session=session))
    statuses: list[str] = []

    def observe(_event: Any, _stream_id: str | None, outcome: TranscriptOutcome | None) -> None:
        if outcome is not None:
            statuses.append(outcome.status)

    harness.subscribe_transcript(observe)
    await harness.prompt("hello")
    assert [m.role for m in (await session.build_context()).messages] == ["user", "assistant"]
    assert statuses == ["storageOnly", "storageOnly"]


@pytest.mark.parametrize("transcript", [False, True], ids=["classic", "transcript"])
@pytest.mark.parametrize("refuse_result", [False, True])
async def test_legacy_jsonl_writer_stores_one_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transcript: bool, refuse_result: bool
) -> None:
    parent = _parent

    async def legacy_parent(path: Path) -> tuple[_LegacyReceiptSession, Any, Path]:
        session, repo, file = await parent(path)
        return _LegacyReceiptSession(session.get_storage()), repo, file

    async def listen(run: _Run) -> None:
        if transcript:
            run.harness.subscribe_transcript(lambda *_: None)

    monkeypatch.setattr(f"{__name__}._parent", legacy_parent)
    with _refuse_rows("toolResult") if refuse_result else contextlib.nullcontext():
        run = await _cancelled(tmp_path, rewrite="redact", before_prompt=listen)

    carrier = ("user" if refuse_result else "toolResult", REDACTED)
    expected = [("user", "delegate"), carrier]
    for prompt in ("next", "again"):
        await run.harness.prompt(prompt)
        expected.append(("user", prompt))
        assert _user_and_tool_rows(run.seen[-1]) == _user_and_tool_rows(await run.reopen()) == expected
    assert run.session.writes.count(carrier) == 1


@pytest.mark.parametrize("field_name", ["tool_call_id", "tool_name"])
@pytest.mark.parametrize("refuse_result", [False, True])
async def test_a_result_with_the_wrong_call_binding_needs_a_generic_notice(
    tmp_path: Path, field_name: str, refuse_result: bool
) -> None:
    async def change_binding(event: Any, _ctx: Any) -> MessageEndEventResult | None:
        if event.message.role != "toolResult":
            return None
        return MessageEndEventResult(
            message=replace(
                event.message, content=[TextContent(text="unbound")], **{field_name: "different"}
            )
        )

    def register(run: _Run) -> None:
        run.harness.hooks.on("message_end", change_binding)

    with _refuse_rows("toolResult") if refuse_result else contextlib.nullcontext():
        run = await _cancelled(tmp_path, before_abort=register)
    await run.harness.prompt("next")

    expected = [("user", "delegate")]
    if not refuse_result:
        expected.append(("toolResult", "unbound"))
    expected.extend([("user", NOTICE), ("user", "next")])
    assert _user_and_tool_rows(run.seen[-1]) == _user_and_tool_rows(await run.reopen()) == expected


async def test_two_owed_reports_with_the_same_call_id_survive_in_order(tmp_path: Path) -> None:
    later = _tool_call({"profile": "scout", "task": "second"})
    with _refuse_rows("toolResult"):
        run = await _cancelled(tmp_path, rewrite="redact", replies=[later])
    channel = run.bench.channel
    channel.arrived.clear()
    second_mask = "second mask"

    async def rewrite(_event: Any, _ctx: Any) -> ToolResultPatch:
        return ToolResultPatch(content=[TextContent(text=second_mask)])

    run.harness.hooks.on("tool_result", rewrite)
    with _refuse_rows("user", REDACTED), _refuse_rows("toolResult"):
        turn = asyncio.create_task(run.harness.prompt("second delegation"))
        try:
            await asyncio.wait_for(channel.arrived.wait(), 10)
            await run.harness.abort(withdraw_unanswered=True)
        finally:
            await asyncio.wait_for(turn, 10)
    await run.harness.prompt("retry")

    for messages in (run.seen[-1], await run.reopen()):
        assert [row for row in _user_and_tool_rows(messages) if row[1] in {REDACTED, second_mask}] == [
            ("user", REDACTED), ("user", second_mask),
        ]


# === guards: pass on the base and after ===========================================


@pytest.mark.parametrize("second", ["tool_result", "message_end"])
async def test_a_second_cancel_gives_one_user_copy_and_none_after(
    tmp_path: Path, second: str
) -> None:
    """K1: a second abort in the call's ``tool_result`` hook or in its result's
    ``message_end`` before the commit keeps the report out of the call's result.
    The next prompt carries a content-free notice as a user message, and the
    one after carries that notice from the file and injects no second one."""

    run = await _cancelled(tmp_path, second=second)
    await run.harness.prompt("p1")
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"]
    assert run.request(3) == ["user"]
    assert await run.file() == ["user"]
    assert [_message_text(message) for message in run.seen[1] if message.role == "user"].count(NOTICE) == 1


async def test_a_second_cancel_in_the_reports_append_gives_one_user_copy(tmp_path: Path) -> None:
    """K1, the third place: the second abort parks the report's append before its
    bytes. Not run on 6dd682cd: there the call's result is the cut text, which this
    gate never parks (the probe's ``n/a`` row)."""

    run = await _cancelled(tmp_path, second="append")
    await run.harness.prompt("p1")
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"]
    assert run.request(3) == ["user"]
    assert await run.file() == ["user"]


async def test_a_prompt_a_later_handler_fails_keeps_the_copy_for_the_next(tmp_path: Path) -> None:
    """A ``before_agent_start`` handler after the extension's raises: that prompt
    makes no request and its injected messages are not restored. The next prompt
    still carries one copy (K1 path, so the copy is what carries it)."""

    async def refuse(event: Any, _ctx: Any) -> None:
        if event.prompt == "failed":
            raise RuntimeError("a later handler failed")

    run = await _cancelled(tmp_path, second="tool_result", later_handlers=[refuse])
    with contextlib.suppress(Exception):
        await run.harness.prompt("failed")
    assert len(run.seen) == 1, "the failed prompt made no request"
    await run.harness.prompt("p1")
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"]
    assert run.request(3) == ["user"]
    assert await run.file() == ["user"]


async def test_a_session_less_harness_gets_one_copy(tmp_path: Path) -> None:
    """Rule 1: no session recorded — the copy goes out once and the record is
    retired, as the base's queue sent it."""

    run = await _cancelled(tmp_path, second="tool_result", sessionless=True)
    await run.harness.prompt("p1")
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"]
    assert run.request(3) == ["user"], "live state keeps the one copy"
    assert _owed(run) == {}


async def test_a_record_of_another_session_injects_nothing(tmp_path: Path) -> None:
    """Rule 2: the live session is not the one the call ran in — retired, nothing
    sent (the report is in that session's file). On 6dd682cd the queue sends its
    copy into whatever session comes next: the next request carries ``user``."""

    run = await _cancelled(tmp_path)
    other, _repo, _path = await _parent(tmp_path / "other")
    run.bench.ext.session = lambda: other
    await run.harness.prompt("p1")

    assert "user" not in run.request(2)
    assert _owed(run) == {}


async def test_a_renderer_that_raises_is_the_bare_cancel(tmp_path: Path) -> None:
    """The report is rendered inside the containment: a renderer that fails gives
    the call the loop's default cut text, records nothing and queues nothing."""

    def broken(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("the renderer failed")

    with patch.object(agents_extension, "render_cancel_report", broken):
        run = await _cancelled(tmp_path)
    await run.harness.prompt("p1")

    texts = [_message_text(m) for m in run.seen[1] if getattr(m, "role", "") == "toolResult"]
    assert texts == [CUT]
    assert run.harness._next_turn_queue == []
    assert _owed(run) == {}


def test_the_cut_sentence_is_public_and_the_private_name_is_its_alias() -> None:
    assert core_loop._CUT_TEXT is core_loop.CUT_TEXT
    assert "CUT_TEXT" in core_loop.__all__


async def _open_receipt_wire(run: _Run, *, factory=None, repo=None, write=None):
    from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime, run_rpc_mode

    from tests.rpc.control_v1.rigs import hold_session_writer, request
    from tests.rpc.test_runtime_control_lifecycle_594 import Wire

    runtime = _make_passthrough_runtime(run.harness, factory, repo=repo)
    hold_session_writer(runtime)
    wire = Wire()
    task = asyncio.create_task(
        run_rpc_mode(
            run.harness, runtime_host=runtime, stdin=wire.reader,
            stdout_write=wire.write if write is None else lambda data: write(wire, data),
            install_signal_handlers=False,
        )
    )
    ready = await request(wire, "ready", "capabilities", {})
    assert ready["success"], ready
    return runtime, wire, task


async def _directed_receipt(wire, receipt, request_id):
    from tests.rpc.control_v1.rigs import request

    answer = await request(wire, request_id, "transcript/list", {
        "leafId": receipt["entryId"], "firstSequence": receipt["sequence"], "limit": 1,
    })
    assert answer["success"], answer
    assert answer["data"]["nativeSessionId"] == receipt["nativeSessionId"]
    row, = answer["data"]["entries"]
    for coordinate in ("entryId", "parentEntryId", "sequence"):
        assert row[coordinate] == receipt[coordinate]
    return row


@pytest.mark.parametrize("second", [None, "message_end", "append"])
async def test_report_carriers_have_distinct_live_keys_and_directed_readback(
    tmp_path: Path, second: str | None,
) -> None:
    wire = None
    rpc = None

    async def observe(run):
        nonlocal wire, rpc
        _, wire, rpc = await _open_receipt_wire(run)

    try:
        run = await _cancelled(tmp_path, second=second, before_prompt=observe)
        assert wire is not None
        execution = next(
            frame for frame in wire.records if frame["type"] == "tool_execution_end"
            and frame.get("tool_name") == "agent"
        )
        assert "transcript" in execution, execution
        result_stream = execution["transcript"]["streamId"]
        carrier_events = [
            frame for frame in wire.records
            if frame.get("transcript", {}).get("streamId") == result_stream
        ]
        assert carrier_events[-2 if second is None else -1]["type"] == "message_start"
        if second is None:
            receipt = carrier_events[-1]["transcript"]
            assert receipt["status"] == "stored" and receipt["sequence"] == 3
            carrier = await _directed_receipt(wire, receipt, "carrier")
            assert carrier["message"]["role"] == "toolResult"
            assert CANCEL_REPORT_HEAD in carrier["message"]["content"][0]["text"]
        else:
            assert all(frame["type"] != "message_end" for frame in carrier_events)

        await run.harness.prompt("after the cancel")
        user_ends = [
            frame for frame in wire.records
            if frame["type"] == "message_end" and frame["message"]["role"] == "user"
        ]
        assert len(user_ends) == (2 if second is None else 3)
        streams = [frame["transcript"]["streamId"] for frame in user_ends]
        assert len(set([result_stream, *streams])) == 1 + len(streams)
        for index, frame in enumerate(user_ends):
            receipt = frame["transcript"]
            assert receipt["status"] == "stored"
            row = await _directed_receipt(wire, receipt, f"user-{index}")
            assert row["message"]["role"] == "user"
        if second is not None:
            copy_text = user_ends[-2]["message"]["content"][0]["text"]
            if second == "message_end":
                assert copy_text == NOTICE
            else:
                assert CANCEL_REPORT_HEAD in copy_text
            assert user_ends[-1]["transcript"]["parentEntryId"] == user_ends[-2]["transcript"]["entryId"]
    finally:
        if wire is not None and rpc is not None:
            wire.send(eof=True)
            await asyncio.wait_for(rpc, 10)


@pytest.mark.parametrize("boundary", ["eof", "new_session", "broken"])
async def test_outgoing_report_receipt_is_delivered_during_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    import json

    from aelix_agent_core.runtime import agent_session_runtime
    from aelix_agent_core.session import JsonlSessionRepo

    from tests.rpc.control_v1.rigs import request

    monkeypatch.setattr(agent_session_runtime, "SWAP_SETTLE_SECONDS", 0)
    wire = None
    rpc = None
    settling = asyncio.Event()
    disposed = asyncio.Event()
    release = asyncio.Event()
    broken = False
    attempts: list[str] = []

    def write(output, data: bytes) -> None:
        if broken:
            attempts.append(json.loads(data)["type"])
            raise BrokenPipeError("fixture consumer closed")
        output.write(data)

    async def factory(session, **_):
        return AgentHarness(AgentHarnessOptions(session=session, cwd=str(tmp_path)))

    async def observe(run):
        nonlocal wire, rpc
        _, wire, rpc = await _open_receipt_wire(
            run, factory=factory, repo=JsonlSessionRepo(sessions_root=str(tmp_path / "sessions")),
            write=write,
        )

        async def hold_report(event, _ctx):
            if event.message.role == "toolResult":
                settling.set()
                await release.wait()

        run.harness.hooks.on("message_end", hold_report)
        original_dispose = run.harness.dispose

        async def mark_dispose():
            disposed.set()
            await release.wait()
            await original_dispose()

        monkeypatch.setattr(run.harness, "dispose", mark_dispose)

    async def abort_at_boundary(run):
        nonlocal broken
        assert wire is not None
        if boundary == "broken":
            broken = True
            wire.send({"type": "get_state", "id": "wake"})
        elif boundary == "eof":
            wire.send(eof=True)
        else:
            wire.send({"type": "new_session", "id": "swap"})

        try:
            await asyncio.wait_for(settling.wait(), 10)
            await asyncio.wait_for(disposed.wait(), 10)
            if boundary == "new_session":
                fenced = await request(wire, "fenced", "transcript/list", {})
                assert not fenced["success"]
        finally:
            release.set()

    try:
        run = await _cancelled(tmp_path, before_prompt=observe, abort_with=abort_at_boundary)
        assert wire is not None and rpc is not None and run.path is not None
        if boundary == "new_session":
            reply = await asyncio.wait_for(wire.response("swap"), 10)
            assert reply["success"], reply
            wire.send(eof=True)
        await asyncio.wait_for(rpc, 10)
        if boundary == "broken":
            assert attempts == ["response"]
            execution = next(frame for frame in wire.records if frame["type"] == "tool_execution_start")
            assert "transcript" in execution, execution
            return
        terminals = [
            frame for frame in wire.records if frame["type"] == "message_end"
            and frame["message"]["role"] == "toolResult"
        ]
        receipts = [frame.get("transcript") for frame in terminals]
        assert len(receipts) == 1 and receipts[0] is not None, terminals
        (terminal,) = terminals
        (receipt,) = receipts
        assert receipt["status"] == "stored"
        if boundary == "new_session":
            assert wire.records.index(terminal) < wire.records.index(reply)
        reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(run.path))
        snapshot = await reopened.transcript_snapshot()
        matches = snapshot.messages(receipt["entryId"])
        assert matches[-1].id == receipt["entryId"] and len(matches) == receipt["sequence"]
    finally:
        release.set()
        if rpc is not None and not rpc.done():
            rpc.cancel()
            await asyncio.gather(rpc, return_exceptions=True)


# ── host writers and readers the receipt cannot identify (#839, verify round 4) ──────────
#
# The next four tests pin the configurations round 4 of the independent verify found
# delivering a cancelled report twice. Each passes on the base before #839 (one carrier)
# and must keep passing: a host written for the base sees what it saw.


@pytest.mark.parametrize("transcript", [False, True], ids=["classic", "transcript"])
async def test_a_forwarding_writer_over_the_same_storage_delivers_its_copy_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transcript: bool
) -> None:
    """A host wraps the repository's ``Session`` instead of calling ``super()`` (round 4 F1).

    Both objects address the same storage; the host stores ``host copy`` in place
    of the hook's mask and is aborted after the write, before its override
    returns. The write is identified by the storage it reaches and the call it is
    the result of, not by the ``Session`` object the harness called.
    """

    landed = asyncio.Event()
    release = asyncio.Event()
    parent = _parent

    class HostSession(Session):
        def __init__(self, inner: Session) -> None:
            super().__init__(inner.get_storage())
            self.inner = inner

        async def forward(self, writer: Any, message: AgentMessage) -> Any:
            if message.role != "toolResult":
                return await writer(message)
            stored = await writer(replace(message, content=[TextContent(text="host copy")]))
            landed.set()
            await release.wait()
            return stored

        async def append_message(self, message: AgentMessage) -> str:
            return await self.forward(self.inner.append_message, message)

        async def append_message_receipt(self, message: AgentMessage) -> MessageAppend:
            return await self.forward(self.inner.append_message_receipt, message)

    async def use_host(path: Path) -> tuple[HostSession, Any, Path]:
        session, repo, file = await parent(path)
        return HostSession(session), repo, file

    async def listen(run: _Run) -> None:
        if transcript:
            run.harness.subscribe_transcript(lambda *_: None)

    async def abort_twice(run: _Run) -> None:
        await run.harness.abort(withdraw_unanswered=True)
        try:
            await asyncio.wait_for(landed.wait(), 5)
            await run.harness.abort()
        finally:
            release.set()

    monkeypatch.setattr(f"{__name__}._parent", use_host)
    run = await _cancelled(tmp_path, rewrite="redact", before_prompt=listen, abort_with=abort_twice)
    await run.harness.prompt("next")
    expected = [("user", "delegate"), ("toolResult", "host copy"), ("user", "next")]
    assert _user_and_tool_rows(run.seen[-1]) == _user_and_tool_rows(await run.reopen()) == expected


@pytest.mark.parametrize("transcript", [False, True], ids=["classic", "transcript"])
async def test_a_restamped_fallback_copy_is_delivered_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transcript: bool
) -> None:
    """The host writes the user-role fallback with its own timestamp (round 4 F2).

    The result's write is refused, so the next prompt carries the report as a
    user message; the host's writer restamps it and is aborted before returning.
    The second prompt must not carry it again: within the harness's write of
    that message, the entry the session builds for a message of the same role
    is this write's, whatever the host changed on it.
    """

    landed = asyncio.Event()
    release = asyncio.Event()
    parent = _parent

    class HostSession(Session):
        async def forward(self, writer: Any, message: AgentMessage) -> Any:
            if message.role != "user" or CANCEL_REPORT_HEAD not in _message_text(message):
                return await writer(message)
            entry = await writer(replace(message, timestamp=1.0))
            landed.set()
            await release.wait()
            return entry

        async def append_message(self, message: AgentMessage) -> str:
            return await self.forward(super().append_message, message)

        async def append_message_receipt(self, message: AgentMessage) -> MessageAppend:
            return await self.forward(super().append_message_receipt, message)

    async def use_host(path: Path) -> tuple[HostSession, Any, Path]:
        session, repo, file = await parent(path)
        return HostSession(session.get_storage()), repo, file

    async def listen(run: _Run) -> None:
        if transcript:
            run.harness.subscribe_transcript(lambda *_: None)

    monkeypatch.setattr(f"{__name__}._parent", use_host)
    with _refuse_rows("toolResult"):
        run = await _cancelled(tmp_path, before_prompt=listen)
    turn = asyncio.create_task(run.harness.prompt("p1"))
    try:
        await asyncio.wait_for(landed.wait(), 5)
        await run.harness.abort()
    finally:
        release.set()
        await asyncio.wait_for(turn, 5)
    await run.harness.prompt("p2")
    assert _kinds(run.seen[-1]) == _kinds(await run.reopen()) == ["user"]


async def test_a_hosts_read_view_of_attribute_records_sees_the_placed_result(
    tmp_path: Path,
) -> None:
    """The extension's ``session=`` callback hands back a read view (round 4 F3).

    Its ``get_branch()`` presents the branch's messages as attribute records
    with the native ids and values, not as the message classes. The committed
    masked result is recognised by its attributes, as before receipts, so no
    second, user-role mask goes out.
    """

    from types import SimpleNamespace

    async def install(run: _Run) -> None:
        class Reader:
            def __getattr__(self, name: str) -> Any:
                if name.startswith("_"):
                    raise AttributeError(name)
                return getattr(run.session, name)

            async def get_branch(self) -> list[Any]:
                entries = await run.session.get_branch()
                return [
                    SimpleNamespace(id=e.id, message=SimpleNamespace(**vars(e.message)))
                    if hasattr(e, "message")
                    else e
                    for e in entries
                ]

        view = Reader()
        run.bench.ext.session = lambda: view

    run = await _cancelled(tmp_path, rewrite="redact", before_prompt=install)
    await run.harness.prompt("next")
    assert _kinds(run.seen[-1]) == _kinds(await run.reopen()) == ["toolResult(redacted)"]


@pytest.mark.parametrize("bypassed", ["toolResult", "user"], ids=["result", "copy"])
async def test_a_writer_past_the_bundled_session_keeps_the_base_delivery_when_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bypassed: str
) -> None:
    """The narrowed configuration (ADR-0307, #839 note): a writer the receipt cannot see.

    The host's writer builds the entry itself and appends it to the storage,
    past ``Session``'s own builder, then is aborted before it returns. No entry
    was claimed for the receipt, so the write is judged as the base judged every
    write: the result by the first result for the call after its origin, the
    copy by the leaf moved past its ``message_start``. One carrier either way.
    """

    from aelix_agent_core.session.entries import MessageEntry
    from aelix_agent_core.session.session import _iso_now

    landed = asyncio.Event()
    release = asyncio.Event()
    parent = _parent

    class HostSession(Session):
        async def append_message(self, message: AgentMessage) -> str:
            if message.role != bypassed or (
                bypassed == "user" and CANCEL_REPORT_HEAD not in _message_text(message)
            ):
                return await super().append_message(message)
            entry = await self.get_storage().append_to_leaf(
                lambda entry_id, parent_id: MessageEntry(
                    id=entry_id, parent_id=parent_id, timestamp=_iso_now(), message=message
                )
            )
            landed.set()
            await release.wait()
            return entry.id

    async def use_host(path: Path) -> tuple[HostSession, Any, Path]:
        session, repo, file = await parent(path)
        return HostSession(session.get_storage()), repo, file

    monkeypatch.setattr(f"{__name__}._parent", use_host)
    if bypassed == "toolResult":

        async def abort_twice(run: _Run) -> None:
            await run.harness.abort(withdraw_unanswered=True)
            try:
                await asyncio.wait_for(landed.wait(), 5)
                await run.harness.abort()
            finally:
                release.set()

        run = await _cancelled(tmp_path, rewrite="redact", abort_with=abort_twice)
        await run.harness.prompt("next")
        expected = ["toolResult(redacted)"]
    else:
        with _refuse_rows("toolResult"):
            run = await _cancelled(tmp_path)
        turn = asyncio.create_task(run.harness.prompt("p1"))
        try:
            await asyncio.wait_for(landed.wait(), 5)
            await run.harness.abort()
        finally:
            release.set()
            await asyncio.wait_for(turn, 5)
        await run.harness.prompt("p2")
        expected = ["user"]
    assert _kinds(run.seen[-1]) == _kinds(await run.reopen()) == expected
