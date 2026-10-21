"""#391 - ``-p`` and ``--mode json`` exit 1 with the overflow error when its recovery gives up.

A request the provider refused as too long starts the overflow recovery: drop the failed
answer, compact, re-run. When the compaction could not run - nothing to compact (the
session's first turn), a summary that failed, an extension's cancel - the answer stayed
dropped and step 8 (``print_mode._final_failure``) judged the user's message: text mode
printed nothing and both modes exited 0. Now the harness puts the answer back, so text mode
writes the provider's error to stderr and both modes exit 1, as for any failed answer
(ADR-0268). Text mode also keeps #546's ``Context overflow recovery failed: …`` line when
the compaction itself failed; json mode's stderr stays clean.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
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
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.modes.print_mode import run_print_mode

_OVERFLOW = "prompt is too long: 250000 tokens > 200000 maximum"
_ANSWER = "the answer is 42"
_502 = "Error code: 502 - Bad Gateway"


def _turns(*steps: str) -> Any:
    """A stream_fn whose turns follow ``steps`` (``overflow`` or ``ok``), the last repeating."""

    plan = list(steps)

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        step = plan.pop(0) if len(plan) > 1 else plan[0]
        if step == "overflow":
            message = AssistantMessage(content=[], stop_reason="error", error_message=_OVERFLOW)
            yield AssistantErrorEvent(reason="error", error=message, error_message=_OVERFLOW)
            return
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=_ANSWER)], stop_reason="end_turn", usage={"total_tokens": 10}
            )
        )

    return fn


async def _runtime(
    summarizer: Any, *, compactable: bool, turns: tuple[str, ...] = ("overflow",)
) -> AgentSessionRuntime:
    session = Session(MemorySessionStorage())
    if compactable:
        for _ in range(6):
            await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock", context_window=20_000),
            stream_fn=_turns(*turns),
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


def _json_events(stdout: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


async def test_text_mode_nothing_to_compact_exits_1_with_the_overflow_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The issue's case: before #391, exit 0 with nothing on stdout or stderr."""

    runtime = await _runtime(_succeeds, compactable=False)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.out, out.err) == (1, "", f"{_OVERFLOW}\n")


async def test_json_mode_nothing_to_compact_exits_1_with_a_clean_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = await _runtime(_succeeds, compactable=False)
    code = await run_print_mode(runtime, mode="json", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.err) == (1, "")  # before #391: exit 0
    events = _json_events(out.out)
    ends = [(e["reason"], e["error_message"]) for e in events if e.get("type") == "compaction_end"]
    assert ends == [("overflow", "Nothing to compact")]
    turn_ends = [e for e in events if e.get("type") == "turn_end"]
    assert turn_ends[-1]["message"]["error_message"] == _OVERFLOW


async def test_text_mode_failed_compaction_writes_both_lines_and_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#546's line as it happens, then step 8's verdict on the failed answer."""

    runtime = await _runtime(_fails, compactable=True)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert code == 1  # before #391: 0
    assert out.out == ""
    assert out.err == f"Context overflow recovery failed: {_502}\n{_OVERFLOW}\n"


async def test_json_mode_failed_compaction_exits_1_with_a_clean_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = await _runtime(_fails, compactable=True)
    code = await run_print_mode(runtime, mode="json", messages=[], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.err) == (1, "")


@pytest.mark.parametrize("mode", ["text", "json"])
async def test_a_cancelled_compaction_exits_1_with_the_overflow_error(
    capsys: pytest.CaptureFixture[str], mode: Any
) -> None:
    runtime = await _runtime(_succeeds, compactable=True)

    async def cancel(_event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True)

    runtime.harness.hooks.on("session_before_compact", cancel)
    code = await run_print_mode(runtime, mode=mode, messages=[], initial_message="go")
    out = capsys.readouterr()
    assert code == 1
    assert out.err == (f"{_OVERFLOW}\n" if mode == "text" else "")


@pytest.mark.parametrize("mode", ["text", "json"])
async def test_a_recovered_turn_still_prints_its_answer_and_exits_0(
    capsys: pytest.CaptureFixture[str], mode: Any
) -> None:
    runtime = await _runtime(_succeeds, compactable=True, turns=("overflow", "ok"))
    code = await run_print_mode(runtime, mode=mode, messages=[], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.err) == (0, "")
    if mode == "text":
        assert out.out == f"{_ANSWER}\n"


async def test_a_later_prompt_that_answers_decides_the_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With several prompts the last one decides (ADR-0268): the restored answer of the
    first does not fail a run whose second prompt answered."""

    runtime = await _runtime(_succeeds, compactable=False, turns=("overflow", "ok"))
    code = await run_print_mode(runtime, mode="text", messages=["again"], initial_message="go")
    out = capsys.readouterr()
    assert (code, out.out, out.err) == (0, f"{_ANSWER}\n", "")
