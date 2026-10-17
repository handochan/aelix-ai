"""#414 - a model error with status ``520`` or ``524`` is retried, as pi retries it.

The harness's model-error pattern (``harness/core.py`` ``_RETRYABLE_ERROR_PATTERN``,
ADR-0128) had ``429``/``500``/``502``-``504`` and not ``520``/``524``; pi's has
all seven (``RETRYABLE_PROVIDER_ERROR_PATTERN``, ``packages/ai/src/utils/
retry.ts`` @ 3daa20280), and #379 had already given the OAuth refresh retry pi's
seven. Measured on 529b997e with a real ``aelix --mode json`` against a local
server (``.omc/probes/414-live/impl/``): anthropic and an openai-completions
provider answering ``520`` (or ``524``) then ``200`` ended the turn on the error;
now each retries once and answers.

The statuses are matched as pi matches them: anywhere in the text, so digits that
merely contain a status - ``max_tokens: 25000`` (its ``500``), ``messages.520``,
a request id - make the error retryable too, as on 529b997e for the five statuses
it had (review round 2 dropped round 1's whole-number rule, which diverged from pi
and still retried ``messages.520``, ``1,520 tokens`` or ``req-520-abc``). The rows
under "as in pi" pin that, and a follow-up issue covers it.

Hermetic: no socket (the adapter row uses ``httpx.MockTransport``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import (
    _RETRYABLE_ERROR_PATTERN,
    AgentHarness,
    AgentHarnessOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import Model

#: The texts the providers' SDKs and adapters actually produce for a status.
_RETRIED = [
    # Anthropic / OpenAI SDKs (``APIStatusError``): "Error code: <status> - <body>".
    "Error code: 520 - {'type': 'error', 'error': {'type': 'api_error', 'message': 'x'}}",
    "Error code: 524 - {'type': 'error', 'error': {'type': 'api_error', 'message': 'x'}}",
    # google-genai: "<status> <reason>. <body>".
    "520 None. {'error': {'message': 'x'}}",
    "524 None. {'error': {'message': 'x'}}",
    "HTTP 520",
    "Codex request failed (524): x",
    "status=520",
    # A status between non-Latin words still stands alone.
    "上游返回520错误",
    # The five that were there before stay retried.
    "Error code: 429 - x",
    "Error code: 500 - x",
    "Error code: 502 - x",
    "Error code: 503 - x",
    "Error code: 504 - x",
]

#: Texts whose digits merely CONTAIN a status: pi retries each (its statuses match
#: anywhere), and so does aelix - a follow-up issue, not a fix here.
_DIGITS_INSIDE = [
    "request req_8f524e1c was rejected",  # a request id holding 524
    "request req_011CT520ab was rejected",  # ... and 520
    "request req-520-abc was rejected",
    "rejected after 15240 ms",  # a duration
    "rejected after 0.524 s",
    "prompt is 15200 tokens long",  # a token count
    "prompt is 1,520 tokens long",
    # Anthropic's real 400 for too many output tokens: 25000 holds 500.
    "max_tokens: 25000 > 8192, which is the maximum allowed number of output tokens",
    # Anthropic's 400 for a long session's message index.
    "messages.520: `tool_use` ids were found without `tool_result` blocks immediately after",
    "messages[524].content: text content blocks must be non-empty",
    "message msg_01HQ5200XYZ not found",
    "invalid value 1429 for field n",
    "batch 5029 rejected",
    "HTTP502",
    "error_524",
    "toolu_01QW520XK",
    "520abc",
]

#: Texts with none of the seven statuses' digits and none of the pattern's words.
_NOT_RETRIED = [
    "Error code: 400 - invalid_request_error",
    "Error code: 401 - authentication_error",
    "Error code: 404 - not_found_error",
    "Error code: 501 - not implemented",
    "Error code: 505 - x",
    "Error code: 521 - x",
    "Error code: 522 - x",
    "Error code: 523 - x",
]


@pytest.mark.parametrize("text", _RETRIED)
def test_a_status_is_retried(text: str) -> None:
    assert _RETRYABLE_ERROR_PATTERN.search(text) is not None


@pytest.mark.parametrize("text", _DIGITS_INSIDE)
def test_digits_that_contain_a_status_are_retried_as_in_pi(text: str) -> None:
    """pi's ``RETRYABLE_PROVIDER_ERROR_PATTERN`` has each status as a bare alternative
    (``"429"``, ..., ``"524"``) in a case-insensitive regex with no boundaries: a status
    counts wherever its digits appear."""

    assert _RETRYABLE_ERROR_PATTERN.search(text) is not None


@pytest.mark.parametrize("text", _NOT_RETRIED)
def test_other_statuses_are_not_retried(text: str) -> None:
    assert _RETRYABLE_ERROR_PATTERN.search(text) is None


def _err(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text="")], stop_reason="error", error_message=text)


def _harness(settings: dict[str, Any] | None = None) -> AgentHarness:
    harness = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()),
            settings_manager=SettingsManager.in_memory(settings) if settings is not None else None,
        )
    )
    harness._state.auto_compaction_enabled = False
    return harness


@pytest.mark.parametrize("text", [_RETRIED[0], _RETRIED[1]])
def test_the_harness_classifies_520_and_524_as_retryable(text: str) -> None:
    assert _harness()._is_retryable_error(_err(text)) is True


def test_the_harness_retries_a_400_that_names_25000_as_pi_does() -> None:
    assert _harness()._is_retryable_error(_err(f"Error code: 400 - {_DIGITS_INSIDE[7]}")) is True


class _Scripted:
    """Every attempt of a real ``prompt`` answers from ``plan`` (``_run`` replaced)."""

    def __init__(self, harness: AgentHarness, plan: list[str]) -> None:
        self.events: list[Any] = []
        harness.subscribe(lambda ev: self.events.append(ev) or None)
        self.attempts = 0

        async def run(prompts: Any, *, system_prompt: Any = None, pending_inputs: Any = None) -> list[Any]:
            self.attempts += 1
            harness._state.messages.extend(prompts)
            answer = plan.pop(0) if plan else "ok"
            if answer == "ok":
                harness._state.messages.append(
                    AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
                )
            else:
                harness._state.messages.append(_err(answer))
            return list(harness._state.messages)

        harness._run = run  # type: ignore[method-assign]

    def retries(self) -> tuple[list[tuple[int, int]], list[tuple[bool, int]]]:
        starts = [(e.attempt, e.max_attempts) for e in self.events if isinstance(e, AutoRetryStartEvent)]
        ends = [(e.success, e.attempt) for e in self.events if isinstance(e, AutoRetryEndEvent)]
        return starts, ends


@pytest.mark.parametrize("status", ["520", "524"])
async def test_a_520_or_524_turn_is_retried_by_the_settings_policy(status: str) -> None:
    """#408's settings-driven retry carries the new statuses: ``maxRetries`` and
    ``baseDelayMs`` from ``settings.json`` govern them like a ``502``."""

    harness = _harness({"retry": {"maxRetries": 2, "baseDelayMs": 1}})
    rig = _Scripted(harness, [f"Error code: {status} - x", f"Error code: {status} - x", "ok"])

    await harness.prompt("hi")

    assert rig.attempts == 3
    assert rig.retries() == ([(1, 2), (2, 2)], [(True, 2)])


async def test_retry_enabled_false_still_stops_a_520() -> None:
    harness = _harness({"retry": {"enabled": False}})
    rig = _Scripted(harness, ["Error code: 520 - x", "ok"])

    await harness.prompt("hi")

    assert rig.attempts == 1
    assert rig.retries() == ([], [])


async def test_a_400_naming_25000_is_retried_as_in_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request that cannot succeed, sent again until the budget runs out - as on 529b997e
    and in pi (its ``25000`` holds ``500``). Pinned so a change to it is a decision (the
    follow-up issue), not an accident."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    harness = _harness()
    text = f"Error code: 400 - {_DIGITS_INSIDE[7]}"
    rig = _Scripted(harness, [text, text, text, text])

    await harness.prompt("hi")

    assert rig.attempts == 4
    assert rig.retries() == ([(1, 3), (2, 3), (3, 3)], [(False, 3)])


async def test_anthropics_own_520_is_retried_to_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real anthropic adapter (its SDK's own retries off) answered 520, then 200."""

    from aelix_ai.providers import anthropic as anthropic_mod
    from anthropic import AsyncAnthropic

    from tests.cli.test_refresh_retry_379 import _anthropic_sse

    answers = ["520", "200"]

    def handler(request: httpx.Request) -> httpx.Response:
        answer = answers.pop(0)
        if answer != "200":
            return httpx.Response(
                int(answer), json={"type": "error", "error": {"type": "api_error", "message": "upstream"}}
            )
        return httpx.Response(
            200, content=_anthropic_sse("claude-haiku-4-5"), headers={"content-type": "text/event-stream"}
        )

    def client(**kw: Any) -> Any:
        return AsyncAnthropic(
            api_key="sk-ant-fake-414",
            base_url=kw.get("base_url"),
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(anthropic_mod, "create_async_client", client)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)

    async def stream(model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        async for event in anthropic_mod.stream_anthropic(model, context, options):
            yield event

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(
                api="anthropic-messages",
                id="claude-haiku-4-5",
                provider="anthropic",
                base_url="https://anthropic.invalid",
            ),
            session=Session(MemorySessionStorage()),
            stream_fn=stream,
            get_api_key=lambda _provider: "sk-ant-fake-414",
        )
    )
    harness._state.auto_compaction_enabled = False
    events: list[Any] = []
    harness.subscribe(events.append)

    await harness.prompt("hi")

    assert answers == []
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [(e.success, e.attempt) for e in events if isinstance(e, AutoRetryEndEvent)]
    assert [s.attempt for s in starts] == [1]
    assert "520" in starts[0].error_message
    assert ends == [(True, 1)]
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "end_turn"
    await harness.dispose()
