"""#800 (ADR-0307 D1-D2): a cancelled delegation's report is its call's result, and is owed until placed.

The ``agent`` tool raises ``ToolCancelled`` with the report, so the loop writes it as
the cancelled call's result; the extension also records it under the call's
``toolCallId`` (``AgentsExtension._owed``) and checks it at every
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aelix_agent_core import loop as core_loop
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import MessageEndEventResult, ToolResultPatch
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.session.fs import NotAttempted
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

CUT = core_loop._CUT_TEXT  # the alias exists on the base too; ``CUT_TEXT`` is #800's name
REDACTED = "[redacted]"
# ASCII words of ``CANCEL_REPORT_HEAD``: a JSONL line escapes its non-ASCII characters.
_HEAD_WORDS = "stopped when the turn was cancelled"
ANNOTATED = "[annotated] "


def _owed(run: _Run) -> dict[str, Any]:
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
        elif CANCEL_REPORT_HEAD in text:
            out.append(message.role)
    return out


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
        assert self.path is not None
        reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(self.path)))
        return _kinds(list((await reopened.build_context()).messages))


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
            text = REDACTED if rewrite == "redact" else ANNOTATED + _message_text(event)
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

    with patch.object(LocalFileSystem, "append_file", gated_append):
        turn = asyncio.ensure_future(harness.prompt("delegate"))
        await asyncio.wait_for(channel.arrived.wait(), 10)
        if before_abort is not None:
            before_abort(run)
        await harness.abort(withdraw_unanswered=True)
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
        if '"role":"user"' in text.replace(" ", "") and _HEAD_WORDS in text:
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
        if message.role == "user" and CANCEL_REPORT_HEAD in _message_text(message):
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
        if '"role":"user"' in text.replace(" ", "") and _HEAD_WORDS in text:
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
    ("second", "later_id"), [("message_end", "call-9"), ("tool_result", "call-1")]
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


async def test_a_cancel_right_after_the_copy_is_written_still_places_it(tmp_path: Path) -> None:
    """The copy's commit is read at the next ``message_start`` or, when a cancel ends
    the prompt first, at its ``agent_end``. A listener parks on the copy's
    ``message_end`` (after the write) and the prompt is aborted there: the copy is on
    the branch, so the prompt after carries that one and injects none."""

    run = await _cancelled(tmp_path, second="tool_result")
    parked, release = asyncio.Event(), asyncio.Event()

    async def listener(event: Any) -> None:
        message = getattr(event, "message", None)
        if (
            getattr(event, "type", "") == "message_end"
            and getattr(message, "role", "") == "user"
            and CANCEL_REPORT_HEAD in _message_text(message)
            and not parked.is_set()
        ):
            parked.set()
            await release.wait()

    run.harness.subscribe(listener)
    p1 = asyncio.ensure_future(run.harness.prompt("p1"))
    await asyncio.wait_for(parked.wait(), 10)
    await run.harness.abort()
    release.set()
    await asyncio.wait_for(p1, 10)
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"], "p2 carries the copy p1 wrote, and only that one"
    assert await run.file() == ["user"]
    assert _owed(run) == {}


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
    assert list(_owed(run)) == ["call-1"]
    with pytest.raises(Exception, match="cancelled delegation's report"):
        await run.harness.prompt("next")
    assert len(run.seen) == 1, "the failed prompt made a request"
    assert list(_owed(run)) == ["call-1"]
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


@pytest.mark.parametrize("to", ["root", "second"])
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
    run = await _cancelled(tmp_path, prior=prior)
    users = [
        entry.id
        for entry in await run.session.get_branch()
        if getattr(entry, "type", "") == "message" and entry.message.role == "user"
    ]
    await run.harness.navigate_tree(users[0] if to == "root" else users[-1])
    assert (await run.session.get_leaf_id() is None) is (to == "root")
    for number in range(1, 4):
        await run.harness.prompt(f"rephrased {number}")
        assert run.request(number + 1) == ["user"], f"prompt {number}"
    assert await run.file() == ["user"]
    assert _owed(run) == {}


async def test_a_call_whose_origin_could_not_be_read_finds_its_result_anywhere(
    tmp_path: Path,
) -> None:
    """Verify round 2 F4 (mutant V-E22: an unread origin skips the carrier check). The
    session's leaf cannot be read when the ``agent`` call begins, so the record has no
    origin and the whole branch is searched for the call's result: one Esc, and the
    next request carries the report once, as that result, with no copy. A guard: it
    passes on 9acd9ea0 and fails under the mutant with ``['toolResult', 'user']``."""

    real_origin = agents_extension.AgentsExtension._call_origin

    async def unreadable_leaf(_session: Any) -> Any:
        raise OSError("the leaf cannot be read")

    async def origin_unread(self: Any) -> Any:
        with patch.object(Session, "get_leaf_id", unreadable_leaf):
            return await real_origin(self)

    with patch.object(agents_extension.AgentsExtension, "_call_origin", origin_unread):
        run = await _cancelled(tmp_path)
    record = _owed(run)["call-1"]
    assert record.session is run.session and record.origin is None
    await run.harness.prompt("next")

    assert run.request(2) == ["toolResult"]
    assert _owed(run) == {}


# === guards: pass on the base and after ===========================================


@pytest.mark.parametrize("second", ["tool_result", "message_end"])
async def test_a_second_cancel_gives_one_user_copy_and_none_after(
    tmp_path: Path, second: str
) -> None:
    """K1: a second abort in the call's ``tool_result`` hook or in its result's
    ``message_end`` before the commit keeps the report out of the call's result.
    The next prompt carries it as a user message, as the base's queue did, and the
    one after carries that copy from the file and injects no second one."""

    run = await _cancelled(tmp_path, second=second)
    await run.harness.prompt("p1")
    await run.harness.prompt("p2")

    assert run.request(2) == ["user"]
    assert run.request(3) == ["user"]
    assert await run.file() == ["user"]


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
