"""#123 (ADR-0306), the child side: a delegated child serves a command inbox on its stdin.

``--command-inbox=1`` (hidden) makes ``_read_piped_stdin`` read nothing, on every
platform, and makes print mode pump JSONL commands from stdin beside its prompt
(``modes/print_inbox.py``): ``steer``, ``follow_up`` and a tighten-only
``set_permission_mode``, each answered on stdout in the rpc ``response`` shape;
a malformed line is answered by id or dropped and never fatal; EOF aborts the
turn once. The header, ``aelix_print_done`` and exit 1 on a failed answer are
unchanged with the flag (guards).

Fails on ``a1606f58``: ``parse_args`` has no ``command_inbox`` (the flag lands in
``unknown_flags``), ``_read_piped_stdin`` and ``run_print_mode`` take no
``inbox``, ``modes/print_inbox`` does not exist, and a real child launched with
the flag waits ``AELIX_STDIN_TIMEOUT`` on its pipe, prints the "no data on piped
stdin" note and never reads a line written after it (contract r2 §14.1).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core import AgentTool
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agents.envelope import build_result
from aelix_agents.stream import _StreamState, reduce_line
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.streaming import AssistantEndEvent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli.args import (
    COMMAND_INBOX_FLAG,
    COMMAND_INBOX_VERSION,
    parse_args,
    print_help,
)
from aelix_coding_agent.cli.entry import _command_inbox, _read_piped_stdin
from aelix_coding_agent.modes import print_mode
from aelix_coding_agent.modes.print_inbox import RESPONSE_TYPE, CommandInbox
from aelix_coding_agent.rpc import rpc_mode
from aelix_coding_agent.rpc.rpc_types import RpcErrorResponse, RpcSuccessResponse

from tests.env_sandbox import child_env
from tests.event_waits import wait_until

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="a real child's pipes are POSIX here")


# === the flag ======================================================================


def test_the_flag_parses_to_a_version_and_stays_out_of_help() -> None:
    parsed = parse_args(["--mode", "json", "-p", f"{COMMAND_INBOX_FLAG}={COMMAND_INBOX_VERSION}", "Task: x"])
    assert parsed.command_inbox == COMMAND_INBOX_VERSION
    assert parsed.unknown_flags == {}, "not a pass-through extension flag"
    assert parsed.messages == ["Task: x"] and parsed.diagnostics == []
    assert parse_args(["-p", "x"]).command_inbox is None
    out = io.StringIO()
    print_help(out)
    assert COMMAND_INBOX_FLAG not in out.getvalue(), "hidden: a parent's request, never a user's"


@pytest.mark.parametrize("spelling", [f"{COMMAND_INBOX_FLAG}=2", COMMAND_INBOX_FLAG, f"{COMMAND_INBOX_FLAG}="])
def test_an_unknown_inbox_version_is_an_error_diagnostic(spelling: str) -> None:
    """Refused at launch: ``_async_main`` prints every error diagnostic and returns 1."""

    parsed = parse_args([spelling, "-p", "x"])
    assert parsed.command_inbox is None
    assert [d["type"] for d in parsed.diagnostics] == ["error"]
    assert f"serves {COMMAND_INBOX_VERSION}" in parsed.diagnostics[0]["message"]
    assert parsed.messages == ["x"], "the rest of the line is parsed as before"


def test_tokens_after_a_bare_double_dash_are_left_alone() -> None:
    parsed = parse_args([f"{COMMAND_INBOX_FLAG}=1", "--", f"{COMMAND_INBOX_FLAG}=9"])
    assert parsed.command_inbox == "1"
    assert parsed.messages == [f"{COMMAND_INBOX_FLAG}=9"]


# === stdin is not a prompt =========================================================


class _NeverRead:
    """A non-tty stdin that fails the test if anything reads or selects on it."""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise AssertionError("the inbox must not be selected on")

    def read(self, *_a: Any) -> str:
        raise AssertionError("the inbox must not be read as a prompt")

    @property
    def buffer(self) -> Any:
        raise AssertionError("the inbox must not be read as a prompt")


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
async def test_read_piped_stdin_with_the_inbox_reads_nothing_on_every_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "stdin", _NeverRead())
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("AELIX_STDIN_TIMEOUT", "0")  # "wait forever" is not consulted
    started = time.monotonic()
    assert await asyncio.wait_for(_read_piped_stdin(required=False, inbox=True), 5) is None
    assert time.monotonic() - started < 1.0


async def test_the_reader_is_opened_only_for_a_launch_that_carried_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[int] = []
    reader = asyncio.StreamReader()

    async def fake_open(*, platform: str | None = None) -> tuple[Any, Any]:
        opened.append(1)
        return reader, None

    monkeypatch.setattr(rpc_mode, "_open_stdin_reader", fake_open)
    posture = PermissionPosture()
    assert await _command_inbox(parse_args(["-p", "x"]), posture) is None
    assert opened == []
    inbox = await _command_inbox(parse_args([f"{COMMAND_INBOX_FLAG}=1", "-p", "x"]), posture)
    assert isinstance(inbox, CommandInbox)
    assert inbox.reader is reader and inbox.posture is posture and inbox.pump_thread is None
    assert opened == [1]


# === print mode serves the inbox in-process =======================================


def _shape(messages: Any) -> list[str]:
    out = []
    for m in messages:
        texts = [getattr(c, "text", None) or getattr(c, "tool_name", None) for c in m.content]
        out.append(f"{m.role}:{'|'.join(str(t) for t in texts)}")
    return out


class _Provider:
    """Request 1 → one ``wait`` tool call; every later request → a text answer."""

    def __init__(self, *, fail_last: bool = False) -> None:
        self.seen: list[list[str]] = []
        self.tools: list[int] = []
        self.fail_last = fail_last

    async def __call__(self, _model: Any, context: Any, _options: Any) -> Any:
        self.seen.append(_shape(context.messages))
        self.tools.append(len(context.tools))
        if len(self.seen) == 1:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[ToolCallContent(tool_call_id="c1", tool_name="wait", input={})],
                    stop_reason="tool_use",
                )
            )
            return
        if self.fail_last:
            yield AssistantEndEvent(
                message=AssistantMessage(content=[], stop_reason="error", error_message="the provider said no")
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text=f"answer {len(self.seen)}")]))


class _Run:
    """One ``run_print_mode(mode="json", inbox=...)`` over a scripted harness, its stdout captured."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        fail_last: bool = False,
        mode: str = "yolo",
        inbox: CommandInbox | None = None,
    ) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.provider = _Provider(fail_last=fail_last)
        self.lines: list[dict[str, Any]] = []
        # ``inbox``: one the product opened (the win32 pump test); else a fed reader.
        self.inbox = inbox or CommandInbox(
            reader=asyncio.StreamReader(), posture=PermissionPosture(mode=PermissionMode(mode))
        )
        self.reader = self.inbox.reader
        self.posture = self.inbox.posture
        monkeypatch.setattr(print_mode, "_write_raw_stdout", lambda text: self.lines.append(json.loads(text)))
        monkeypatch.setenv("AELIX_JOBS_PROTOCOL", "1")  # an owned run writes aelix_print_done

        async def wait_tool(_args: Any, _ctx: Any) -> ToolResult:
            self.started.set()
            await self.release.wait()
            return ToolResult(content=[TextContent(text="waited")])

        self.harness = AgentHarness(
            AgentHarnessOptions(
                stream_fn=self.provider,
                session=Session(MemorySessionStorage()),
                tools=[AgentTool(name="wait", execute=wait_tool)],
            )
        )
        self.harness.state.auto_retry_enabled = False
        self.harness.state.auto_compaction_enabled = False
        self.runtime = rpc_mode._make_passthrough_runtime(self.harness, None)
        self.task: asyncio.Task[int] | None = None

    def start(self) -> None:
        self.task = asyncio.ensure_future(
            print_mode.run_print_mode(self.runtime, mode="json", messages=[], initial_message="TASK", inbox=self.inbox)
        )

    def feed(self, *payloads: Any) -> None:
        for payload in payloads:
            line = payload if isinstance(payload, str) else json.dumps(payload)
            self.reader.feed_data(line.encode() + b"\n")

    async def finish(self) -> int:
        assert self.task is not None
        try:
            return await asyncio.wait_for(self.task, 10)
        finally:
            if not self.reader.at_eof():
                self.reader.feed_eof()
            if not self.harness.disposed:
                await self.harness.dispose()

    @property
    def types(self) -> list[Any]:
        return [line.get("type") for line in self.lines]

    @property
    def responses(self) -> list[dict[str, Any]]:
        return [line for line in self.lines if line.get("type") == RESPONSE_TYPE]


async def test_a_steer_fed_mid_tool_is_in_the_next_request_after_the_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed({"type": "steer", "id": "s1", "message": "STEER NOTE"})
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0

    assert run.provider.seen[1] == ["user:TASK", "assistant:wait", "toolResult:waited", "user:STEER NOTE"]
    assert run.responses == [
        {"type": RESPONSE_TYPE, "command": "steer", "id": "s1", "success": True, "data": {"phase": "streaming"}}
    ]
    # The rpc ``response`` shape, field for field, so a parent's correlator reads it.
    assert set(run.responses[0]) == set(RpcSuccessResponse.__dataclass_fields__)
    # Unchanged with the inbox: the header first, ``aelix_print_done`` last (guards).
    assert run.lines[0].get("type") is None and "id" in run.lines[0]
    assert run.types[-1] == "aelix_print_done"


async def test_a_final_steer_on_the_inbox_makes_the_next_call_tool_less_and_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``{"type": "steer", "final": true}`` reaches the kernel's latch (ADR-0306 §4.2)."""

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed({"type": "steer", "id": "f1", "message": "WRAP"})
    run.feed({"type": "steer", "id": "f2", "message": "WRAP NOW", "final": True})
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    # The default steering mode is one-at-a-time: WRAP at the first boundary (with
    # tools), WRAP NOW at the next — and the call after the final has no tools.
    assert run.provider.tools == [1, 1, 0], "the call after the final has no tools"
    assert run.provider.seen[1][-1] == "user:WRAP" and run.provider.seen[2][-1] == "user:WRAP NOW"
    assert len(run.provider.seen) == 3


async def test_a_follow_up_lands_once_the_answer_has_no_tool_calls_left(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed({"type": "follow_up", "id": "f1", "message": "AFTERWARDS"})
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    assert run.provider.seen[1] == ["user:TASK", "assistant:wait", "toolResult:waited"], "not before the answer"
    assert run.provider.seen[2][-1] == "user:AFTERWARDS"
    assert run.responses[0]["command"] == "follow_up" and run.responses[0]["success"] is True


async def test_a_malformed_line_is_answered_by_id_or_dropped_and_never_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed(
        "not json at all",
        "[1, 2, 3]",
        {"type": "bogus", "id": "b1"},
        {"type": "bogus"},
        {"type": "steer", "id": "s0", "message": ""},
        {"type": "steer", "id": "s2", "message": "KEPT"},
    )
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    answered = {r["id"]: r for r in run.responses}
    assert set(answered) == {"b1", "s0", "s2"}, "the two with no id were dropped"
    assert answered["b1"]["success"] is False and "bogus" in answered["b1"]["error"]
    assert set(answered["b1"]) == set(RpcErrorResponse.__dataclass_fields__)
    assert answered["s0"]["success"] is False
    assert answered["s2"]["success"] is True
    assert run.provider.seen[1][-1] == "user:KEPT"


@pytest.mark.parametrize("final", ["false", "true", 1, 0, None, [True]])
async def test_a_final_that_is_not_a_json_boolean_is_refused_and_nothing_is_queued(
    monkeypatch: pytest.MonkeyPatch, final: Any
) -> None:
    """F8: ``"final": "false"`` ended the run (``bool("false")``); now the steer is refused whole.

    Fails on ``8d7ee999``: the line was answered ``success: true`` and its text
    reached the model — as a final (no tools on the next call) for the truthy
    values, as a plain note for the falsy ones.
    """

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed({"type": "steer", "id": "bad", "message": "NOT A FINAL", "final": final})
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    (answer,) = run.responses
    assert (answer["id"], answer["success"]) == ("bad", False)
    assert answer["error"] == "'final' must be a JSON boolean"
    assert run.provider.tools == [1, 1], "nothing was queued: the next call has its tools"
    assert not any("NOT A FINAL" in entry for call in run.provider.seen for entry in call)


async def test_a_command_with_no_string_id_is_dropped_and_nothing_is_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """F8: the parent always sends a string id, so a command without one is not the parent's: dropped.

    Fails on ``8d7ee999``: each was run and answered ``"id": null`` — the notes
    reached the model, the null-id final ended the run, the mode was set.
    """

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed(
        {"type": "steer", "message": "NO ID NOTE"},
        {"type": "steer", "id": 7, "message": "NUMERIC ID"},
        {"type": "steer", "id": None, "message": "NULL ID", "final": True},
        {"type": "follow_up", "id": ["x"], "message": "LIST ID"},
        {"type": "set_permission_mode", "id": 1, "mode": "plan"},
        {"type": "steer", "id": "ok", "message": "KEPT"},
    )
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    assert [(r["id"], r["success"]) for r in run.responses] == [("ok", True)], "one answer: the one with a string id"
    assert run.provider.seen[1][-1] == "user:KEPT" and len(run.provider.seen) == 2
    assert run.provider.tools == [1, 1]
    said = [entry for call in run.provider.seen for entry in call]
    assert not any(text in entry for entry in said for text in ("NO ID NOTE", "NUMERIC ID", "NULL ID", "LIST ID"))
    assert run.posture.mode == PermissionMode("yolo"), "the id-less set_permission_mode was not applied"


async def test_bytes_that_are_not_utf8_drop_that_line_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """F7: a line that is not UTF-8 is one more malformed line, not the end of stdin.

    Fails on ``8d7ee999``: the rpc reader decodes the stream strictly, the pump
    took its ``UnicodeDecodeError`` for an EOF and aborted the turn — exit 1, the
    tool result "the user stopped the turn", the steer after it never answered.
    """

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.reader.feed_data(b"\xff\n")
    # Bad bytes inside an otherwise whole command: the line is dropped, not repaired.
    run.reader.feed_data(b'{"type": "steer", "id": "bad", "message": "BRO\xffKEN"}\n')
    run.feed({"type": "steer", "id": "s1", "message": "AFTER THE BAD BYTES"})
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    assert run.responses == [
        {"type": RESPONSE_TYPE, "command": "steer", "id": "s1", "success": True, "data": {"phase": "streaming"}}
    ]
    assert run.provider.seen[1] == ["user:TASK", "assistant:wait", "toolResult:waited", "user:AFTER THE BAD BYTES"]
    assert run.inbox.ended is False, "no EOF was seen"


async def test_a_character_cut_by_a_read_and_a_crlf_line_are_still_one_line_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard for F7's per-line decode: the framing is still the rpc reader's.

    A multi-byte character split across two reads is one character (the split is
    on LF bytes, never inside a sequence), a CRLF line loses its CR, and a line
    cut by a read is one line.
    """

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    whole = json.dumps({"type": "steer", "id": "k1", "message": "한글 né"}, ensure_ascii=False).encode()
    cut = whole.index("한".encode()) + 1  # inside the three bytes of the first character
    run.reader.feed_data(whole[:cut])
    await asyncio.sleep(0.02)
    run.reader.feed_data(whole[cut:] + b"\r\n")
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    assert [(r["id"], r["success"]) for r in run.responses] == [("k1", True)]
    assert run.provider.seen[1][-1] == "user:한글 né"


async def test_eof_on_the_inbox_aborts_the_turn_once_and_the_run_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """The death detector: the parent's end of the pipe gone, the child stops its turn."""

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.reader.feed_eof()  # the tool is never released: only the abort can end it
    assert await run.finish() == 1
    assert len(run.provider.seen) == 1
    assert run.types[-1] == "aelix_print_done"
    assert any("stopped the turn" in text for text in _shape(run.harness.state.messages))


class _PipeStdin:
    """``sys.stdin`` for the win32 arm: a descriptor and nothing else (#202 — the
    pump reads a descriptor with ``os.read``, never a stream object)."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


async def test_the_win32_thread_pump_serves_the_inbox_and_its_eof_aborts_the_turn_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard (fix round 1, V4): the inbox as ``windows-latest`` opens it, in
    process, on every platform.

    On win32 ``_command_inbox`` gets ``rpc_mode._thread_pumped_stdin_reader(fd)``,
    an ``os.read`` daemon thread feeding the ``StreamReader`` (``connect_read_pipe``
    does not exist there, #107). The win32 arm is chosen here by patching
    ``sys.platform`` for the one call that opens the inbox, and fd 0 is the read
    end of an ``os.pipe()``: the bytes cross a real descriptor and a real thread on
    Linux, macOS and Windows alike. A steer written while the pipe stays open is
    answered and reaches the harness; closing the write end is the EOF that aborts
    the turn, once, and ends the thread. Bytes, never text (CRLF on win32).
    """

    read_fd, write_fd = os.pipe()
    write_open = True
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "platform", "win32")
            patch.setattr(sys, "stdin", _PipeStdin(read_fd))
            inbox = await _command_inbox(
                parse_args([f"{COMMAND_INBOX_FLAG}=1", "-p", "x"]), PermissionPosture(mode=PermissionMode("yolo"))
            )
        assert isinstance(inbox, CommandInbox)
        thread = inbox.pump_thread
        assert thread is not None and thread.daemon and thread.is_alive()

        run = _Run(monkeypatch, inbox=inbox)
        steers: list[tuple[str, dict[str, Any]]] = []
        aborts: list[int] = []
        real_steer, real_abort = run.harness.steer, run.harness.abort

        async def steer(text: str, **kwargs: Any) -> Any:
            steers.append((text, kwargs))
            return await real_steer(text, **kwargs)

        async def abort(*args: Any, **kwargs: Any) -> Any:
            aborts.append(1)
            return await real_abort(*args, **kwargs)

        monkeypatch.setattr(run.harness, "steer", steer)
        monkeypatch.setattr(run.harness, "abort", abort)
        run.start()
        await asyncio.wait_for(run.started.wait(), 5)

        os.write(write_fd, b'{"type": "steer", "id": "s1", "message": "STEER NOTE"}\n')
        await wait_until(lambda: run.responses, timeout=10, what="the reply to a steer written to the pipe")
        assert run.responses == [
            {"type": RESPONSE_TYPE, "command": "steer", "id": "s1", "success": True, "data": {"phase": "streaming"}}
        ]
        assert steers == [("STEER NOTE", {"final": False})]
        # The pipe is still open: no EOF, no abort, the thread parked in its read.
        assert aborts == [] and thread.is_alive()

        os.close(write_fd)  # the parent's end gone; the tool is never released
        write_open = False
        assert await run.finish() == 1
        assert aborts == [1]
        assert len(run.provider.seen) == 1  # the steer was queued, and the abort left no call to read it
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        if write_open:
            os.close(write_fd)
        os.close(read_fd)


async def test_set_permission_mode_tightens_echoes_and_refuses_a_widen(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(monkeypatch, mode="auto-accept-edits")
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.feed(
        {"type": "set_permission_mode", "id": "t1", "mode": "plan"},
        {"type": "set_permission_mode", "id": "t2", "mode": "yolo"},
        {"type": "set_permission_mode", "id": "t3", "mode": "nope"},
        {"type": "set_permission_mode", "id": "t4", "mode": "plan"},
    )
    await asyncio.sleep(0.05)
    run.release.set()
    assert await run.finish() == 0
    answered = {r["id"]: r for r in run.responses}
    assert answered["t1"] == {
        "type": RESPONSE_TYPE, "command": "set_permission_mode", "id": "t1", "success": True, "data": {"mode": "plan"},
    }
    assert answered["t2"]["success"] is False and "only tightens" in answered["t2"]["error"]
    assert answered["t3"]["success"] is False
    assert answered["t4"]["success"] is True, "the same mode again is at-or-below: idempotent"
    assert run.posture.get() is PermissionMode.PLAN


async def test_a_line_written_after_the_prompt_returned_is_never_read_and_gets_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print mode ends its pump before its dispose, so a line that arrives once the
    prompt returned is not read and not answered: no refusal comes from the child.

    The parent's request stays open until the process exits and is then failed
    ``unknown`` with the way on, ``action="resume"`` (fix round 1 F3;
    ``tests/agents_ext/test_intervention_doors_123.py``,
    ``test_a_request_open_when_the_child_ends_is_unknown_and_names_resume``).
    Contract r2 §3.2's row said "refused by the child"; this is what the code does.
    """

    run = _Run(monkeypatch)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.release.set()
    assert run.task is not None
    assert await asyncio.wait_for(run.task, 10) == 0
    run.feed({"type": "steer", "id": "late", "message": "LATE"})
    await asyncio.sleep(0.1)
    assert run.responses == []
    assert len(run.provider.seen) == 2
    run.reader.feed_eof()


async def test_a_failed_last_answer_still_exits_1_with_the_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(monkeypatch, fail_last=True)
    run.start()
    await asyncio.wait_for(run.started.wait(), 5)
    run.release.set()
    assert await run.finish() == 1
    assert run.types[-1] == "aelix_print_done"


async def test_without_an_inbox_print_mode_is_what_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(monkeypatch)
    run.task = asyncio.ensure_future(
        print_mode.run_print_mode(run.runtime, mode="json", messages=[], initial_message="TASK")
    )
    await asyncio.wait_for(run.started.wait(), 5)
    run.release.set()
    assert await run.finish() == 0
    assert run.responses == [] and run.types[-1] == "aelix_print_done"


# === a real child ===================================================================


class _ModelServer:
    """A chat-completions endpoint: request 1 → ``bash sleep``, every later request → a text answer.

    With ``script`` the n-th request (from 1) is answered with ``script(n)``'s chunks
    instead (:func:`_tool_turn`, :func:`_text_turn`), or, where it returns a
    ``(status, body)`` tuple, with that HTTP error and no stream.
    """

    def __init__(self, sleep: float = 0, *, script: Any = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.sleep = sleep
        self.script = script
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802 — http.server's spelling
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length))
                server.requests.append(body)
                turn = server.turn(len(server.requests))
                if isinstance(turn, tuple):
                    status, payload = turn
                    raw = json.dumps(payload).encode()
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for chunk in turn:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._http.server_address[1]}/v1"

    def __enter__(self) -> _ModelServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._http.shutdown()
        self._http.server_close()

    def turn(self, n: int) -> Any:
        if self.script is not None:
            return self.script(n)
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if n == 1:
            call = {
                "index": 0,
                "id": "call_sleep_1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": f"sleep {self.sleep}"})},
            }
            delta: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": [call]}
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": f"PONG {n}"}
            finish = "stop"
        return [_chunk(delta), _chunk({}, finish=finish, usage=usage)]


def _chunk(delta: dict[str, Any], *, finish: str | None = None, usage: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "mockm",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _tool_turn(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> list[dict[str, Any]]:
    call = {"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    return [
        _chunk({"role": "assistant", "content": None, "tool_calls": [call]}),
        _chunk({}, finish="tool_calls", usage=_USAGE),
    ]


def _text_turn(text: str) -> list[dict[str, Any]]:
    return [_chunk({"role": "assistant", "content": text}), _chunk({}, finish="stop", usage=_USAGE)]


def _texts(body: dict[str, Any]) -> list[str]:
    out = []
    for m in body.get("messages", []):
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        out.append(f"{m.get('role')}:{content or ''}")
    return out


def _child_setup(tmp_path: Path, server: _ModelServer) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    agent_dir = home / "agent"
    cwd = tmp_path / "repo"
    for directory in (home / ".config", agent_dir, cwd):
        directory.mkdir(parents=True, exist_ok=True)
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "mockp": {
                        "baseUrl": server.url,
                        "apiKey": "MOCK_KEY",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "mockm",
                                "reasoning": False,
                                "input": ["text"],
                                "cost": {"input": 1.0, "output": 2.0, "cacheRead": 0, "cacheWrite": 0},
                                "contextWindow": 100000,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    env = child_env(
        home,
        XDG_CONFIG_HOME=str(home / ".config"),
        AELIX_CODING_AGENT_DIR=str(agent_dir),
        PI_OFFLINE="1",
        MOCK_KEY="not-a-real-key",
        # What ``build_child_env`` sets for every delegated child: without the
        # flag the base waited this long on the pipe and printed its note.
        AELIX_STDIN_TIMEOUT="1",
        PYTHONUNBUFFERED="1",
    )
    return cwd, env


@posix_only
async def test_a_real_child_with_the_flag_reads_a_steer_mid_tool_and_never_waits_on_stdin(
    tmp_path: Path,
) -> None:
    """Contract r2 §14.1 against the product: an open, silent pipe costs no wait and
    no stderr note, and a line written while the child's ``bash`` runs is in its
    next request after the tool result."""

    with _ModelServer(sleep=3) as server:
        cwd, env = _child_setup(tmp_path, server)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-P", "-m", "aelix_coding_agent",
            "--mode", "json", "-p", f"{COMMAND_INBOX_FLAG}={COMMAND_INBOX_VERSION}",
            "--model", "mockm", "--provider", "mockp", "--no-session", "--no-extensions",
            "--permission-mode", "yolo", "--no-agents", "Task: run the check then say PONG",
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        # NOT ``communicate()``: it closes the child's stdin at once, and EOF on the
        # inbox is the death detector (the second test below). The pipe stays open
        # until the child has exited, as the parent's close-out keeps it.
        reads = asyncio.gather(proc.stdout.read(), proc.stderr.read())
        try:
            started = time.monotonic()
            while len(server.requests) < 1:
                assert proc.returncode is None, "the child ended before its first request"
                assert time.monotonic() - started < 60, "no first model request"
                await asyncio.sleep(0.05)
            proc.stdin.write(json.dumps({"type": "steer", "id": "s1", "message": "STEER NOTE"}).encode() + b"\n")
            await proc.stdin.drain()
            await asyncio.wait_for(proc.wait(), 90)
            out, err = await asyncio.wait_for(reads, 10)
        finally:
            if proc.returncode is None:
                proc.kill()
            proc.stdin.close()
    stderr = err.decode("utf-8", errors="replace")
    assert "no data on piped stdin" not in stderr, stderr
    assert proc.returncode == 0, stderr
    lines = [json.loads(line) for line in out.decode("utf-8", errors="replace").splitlines() if line.strip()]
    responses = [line for line in lines if line.get("type") == RESPONSE_TYPE]
    assert responses == [
        {"type": RESPONSE_TYPE, "command": "steer", "id": "s1", "success": True, "data": {"phase": "streaming"}}
    ]
    assert len(server.requests) == 2, [_texts(r) for r in server.requests]
    second = _texts(server.requests[1])
    assert second[-1] == "user:STEER NOTE"
    assert [t.split(":")[0] for t in second if not t.startswith("system")] == ["user", "assistant", "tool", "user"]
    assert "STEER NOTE" not in _texts(server.requests[0])[-1], "never prepended to the task"


@posix_only
async def test_a_real_child_whose_first_attempt_retries_ends_by_itself_with_both_attempts_folded(
    tmp_path: Path,
) -> None:
    """A guard (contract r2 §12, fix round 1 V4): the first model request is
    answered with a retryable error, the harness retries, the second answers.

    The child's stdout then holds two runs (``agent_end`` of the failed attempt,
    ``auto_retry_start``, a second ``agent_start`` … ``agent_end``), and the first
    ``agent_end`` is not the end: the child goes on with its inbox open and silent,
    exits 0 by itself, and the parent's reducer folds both attempts into the
    second one's answer. Nothing closes the pipe before the exit.
    """

    def script(n: int) -> Any:
        if n == 1:
            return 400, {"error": {"message": "Overloaded, please retry", "type": "overloaded_error"}}
        return _text_turn(f"RETRIED OK {n}")

    with _ModelServer(script=script) as server:
        cwd, env = _child_setup(tmp_path, server)
        settings = Path(env["AELIX_CODING_AGENT_DIR"]) / "settings.json"
        settings.write_bytes(json.dumps({"retry": {"maxRetries": 2, "baseDelayMs": 1}}).encode())
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-P", "-m", "aelix_coding_agent",
            "--mode", "json", "-p", f"{COMMAND_INBOX_FLAG}={COMMAND_INBOX_VERSION}",
            "--model", "mockm", "--provider", "mockp", "--no-session", "--no-extensions",
            "--permission-mode", "yolo", "--no-agents", "Task: say it",
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        reads = asyncio.gather(proc.stdout.read(), proc.stderr.read())
        try:
            # The pipe is held open and nothing is written: only the child ends this.
            await asyncio.wait_for(proc.wait(), 90)
            out, err = await asyncio.wait_for(reads, 10)
        finally:
            if proc.returncode is None:
                proc.kill()
            proc.stdin.close()
    stderr = err.decode("utf-8", errors="replace")
    assert proc.returncode == 0, stderr
    assert len(server.requests) == 2, [_texts(r) for r in server.requests]
    text = out.decode("utf-8", errors="replace")
    types = [json.loads(line).get("type") for line in text.splitlines() if line.strip()]
    kept = [t for t in types if t in ("agent_start", "agent_end", "auto_retry_start", "auto_retry_end")]
    assert kept == ["agent_start", "agent_end", "auto_retry_start", "agent_start", "auto_retry_end", "agent_end"]

    state = _StreamState()
    for line in text.splitlines():
        reduce_line(state, line)
    result = build_result(id="c1", profile="p", state=state, outcome="ok", exit_code=proc.returncode)
    assert (result.ok, result.status, result.summary) == (True, "ok", "RETRIED OK 2")
    assert (result.error, state.dropped_lines) == (None, 0)


@posix_only
async def test_a_real_child_refuses_an_inbox_version_it_does_not_serve(tmp_path: Path) -> None:
    with _ModelServer(sleep=0) as server:
        cwd, env = _child_setup(tmp_path, server)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-P", "-m", "aelix_coding_agent",
            "--mode", "json", "-p", f"{COMMAND_INBOX_FLAG}=2",
            "--model", "mockm", "--provider", "mockp", "--no-session", "--no-extensions", "Task: x",
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), 60)
        finally:
            if proc.returncode is None:
                proc.kill()
        assert server.requests == [], "nothing was asked of the model"
    assert proc.returncode == 1
    assert "version this aelix does not serve" in err.decode("utf-8", errors="replace")
    assert os.linesep is not None  # the stderr line is what the parent's fallback chain reads
