"""#546 - ``-p`` prints the answer of a turn whose threshold compaction failed.

Before #546 the compaction's error was raised out of ``harness.prompt``: text mode
wrote it to stderr instead of the answer and both modes exited 1. Now the turn is
judged as any other (step 8): text mode prints the answer and writes the
compaction's ``Auto-compaction failed: …`` line to stderr (an aelix choice; pi's
text mode writes nothing); ``--mode json`` exits 0 with the event on its stdout and
nothing on its stderr (ADR-0268 §3). A cancel, an abort and "Nothing to compact"
leave no line.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.harness.hooks import SessionBeforeCompactResult
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    CompactResult,
    JsonlSessionRepo,
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
from aelix_coding_agent.modes.print_mode import _compaction_failure, run_print_mode

_ANSWER = "the answer is 42"
_502 = "Error code: 502 - Bad Gateway"


def _big_answer() -> Any:
    """A turn whose answer reports 19500 tokens: past a 20000 window's threshold."""

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=_ANSWER)],
                stop_reason="end_turn",
                usage={"total_tokens": 19_500},
            )
        )

    return fn


async def _runtime(summarizer: Any, *, compactable: bool = True) -> AgentSessionRuntime:
    session = Session(MemorySessionStorage())
    if compactable:
        for _ in range(6):
            await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock", context_window=20_000),
            stream_fn=_big_answer(),
            session=session,
            _summarizer_override=summarizer,
        )
    )

    async def _noop(_s: Any) -> AgentHarness:
        return harness

    return AgentSessionRuntime(
        harness, _noop, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem()
    )


async def _fails(*_args: Any) -> CompactResult:
    raise AgentHarnessError("compaction", _502)


async def _succeeds(_model: Any, preparation: Any, *_args: Any) -> CompactResult:
    return CompactResult(
        summary="## Goal\nok",
        first_kept_entry_id=preparation.first_kept_entry_id,
        tokens_before=preparation.tokens_before,
        details={},
    )


async def test_text_mode_prints_the_answer_and_one_stderr_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = await _runtime(_fails)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert code == 0  # before #546: 1
    assert out.out == f"{_ANSWER}\n"  # before #546: nothing
    assert out.err == f"Auto-compaction failed: {_502}\n"  # before #546: the bare 502


async def test_json_mode_exits_0_with_the_failure_on_its_event_and_a_clean_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = await _runtime(_fails)
    code = await run_print_mode(runtime, mode="json", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert code == 0  # before #546: 1
    assert out.err == ""  # before #546: the bare 502
    ends = [
        e for e in (json.loads(line) for line in out.out.splitlines()) if e.get("type") == "compaction_end"
    ]
    assert [(e["reason"], e["aborted"], e["error_message"]) for e in ends] == [
        ("threshold", False, f"Auto-compaction failed: {_502}")
    ]


async def test_text_mode_writes_no_line_for_a_cancelled_compaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = await _runtime(_succeeds)

    async def cancel(_event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True)

    runtime.harness.hooks.on("session_before_compact", cancel)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.out, out.err) == (0, f"{_ANSWER}\n", "")


@pytest.mark.parametrize("compactable", [True, False], ids=["compacted", "nothing-to-compact"])
async def test_text_mode_writes_no_line_when_no_compaction_failed(
    capsys: pytest.CaptureFixture[str], compactable: bool
) -> None:
    runtime = await _runtime(_succeeds, compactable=compactable)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.out, out.err) == (0, f"{_ANSWER}\n", "")


def _end(**fields: Any) -> Any:
    base: dict[str, Any] = {
        "type": "compaction_end",
        "reason": "threshold",
        "aborted": False,
        "result": None,
        "error_message": "Auto-compaction failed: x",
    }
    return SimpleNamespace(**{**base, **fields})


@pytest.mark.parametrize(
    ("event", "line"),
    [
        (_end(), "Auto-compaction failed: x"),
        (_end(reason="overflow", error_message="Context overflow recovery failed: x"),
         "Context overflow recovery failed: x"),
        (_end(reason="manual", error_message="x"), None),
        (_end(aborted=True, error_message=None), None),
        (_end(aborted=True), None),
        (_end(error_message="Nothing to compact"), None),
        (_end(error_message=None, result=object()), None),
        (_end(error_message=""), None),
        (_end(type="compaction_start"), None),
        (SimpleNamespace(type="turn_end"), None),
    ],
    ids=[
        "threshold", "overflow", "manual", "aborted", "aborted-with-text", "nothing-to-compact",
        "success", "empty", "start", "other-event",
    ],
)
def test_compaction_failure_picks_only_an_automatic_compactions_failure(
    event: Any, line: str | None
) -> None:
    assert _compaction_failure(event) == line
