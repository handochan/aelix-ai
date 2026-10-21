"""#506 - a compaction's and a branch summary's requests ask at the session's
thinking level, set no output cap of their own, and refuse a cut answer.

Before #506 every summarization request - the history summary, a split turn's
prefix summary and a ``/tree`` branch summary - left ``reasoning`` unset, so it
went out on the adapter's reasoning-off path. OpenAI Responses and OpenRouter
send that as ``reasoning: {effort: "none"}``, which ``o4-mini``, ``o3-mini`` and
``gpt-6-astra`` refuse with a 400, while the session's turns ran at its level:
the conversation worked and every compaction failed. Pi passes the session's
level (``createSummarizationOptions``, ``compaction.ts:595-609`` @ fdce48eb4, pi
``da6a81d39``). Doing only that leaves the fixed caps (``0.8`` / ``0.5 x 16384``
and the branch summary's ``2048``) for the reasoning to spend as well, so they
are dropped (pi #11036), and an answer that stopped at its limit is refused
rather than written over the conversation (pi ``getSummarizationFailure``).

The rows pin each of the three requests on each of the three changes, the level
read from the harness at the call, the fitting to the model, ``off`` and a
non-reasoning model unchanged, the refusal not retried (#409's rule), and the
real OpenAI Responses adapter's payload: ``effort`` the session's, no
``max_output_tokens``, and its ``incomplete`` status refused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    NavigateTreeOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.session.branch_summarization import generate_branch_summary
from aelix_agent_core.session.compaction import (
    SUMMARIZATION_SYSTEM_PROMPT,
    CompactionPreparation,
    compact,
)
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai import clear_providers, register_provider
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import AssistantDoneEvent, AssistantErrorEvent, Model, TextDeltaEvent

_API = "summary-reasoning-506"
#: A reasoning row with no ``thinking_level_map``: it lists off..high, not xhigh/max.
_REASONING = Model(api=_API, id="o4-like", provider="p", reasoning=True, context_window=200_000)
_PLAIN = Model(api=_API, id="plain", provider="p", reasoning=False, context_window=200_000)
_SUMMARY = "## Goal\nthe whole summary"
_CUT = "## Goal\nthe sum"
_HISTORY_CUT = "Summarization failed: generation hit the token cap and the summary is incomplete"
_PREFIX_CUT = "Turn prefix summarization failed: generation hit the token cap and the summary is incomplete"
_BRANCH_CUT = "Branch summarization failed: generation hit the token cap and the summary is incomplete"
_502 = "Error code: 502 - Bad Gateway"


@pytest.fixture(autouse=True)
def _providers() -> Any:
    clear_providers()
    yield
    clear_providers()


def _kind(context: Any) -> str:
    text = "".join(
        getattr(block, "text", "") or ""
        for message in context.messages
        for block in (getattr(message, "content", None) or [])
    )
    if "PREFIX of a turn" in text:
        return "prefix"
    if "conversation branch" in text:
        return "branch"
    return "history"


class _Provider:
    """A registered provider that records each request's options.

    ``answers`` maps a request kind (``history`` / ``prefix`` / ``branch``) to a
    list of steps taken in order: ``ok`` answers the whole summary;
    ``length-reason`` stops at the limit as the built-in adapters do (done
    ``reason`` and ``stop_reason`` both ``"length"``); ``length-event`` sets only
    the event's ``reason``, ``length-message`` only the message's; ``502``
    answers an error event. A kind with no step left answers ``ok``.
    """

    def __init__(self, **answers: list[str]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.requests: list[tuple[str, Any]] = []
        register_provider(_API, self._stream)

    def options(self, kind: str) -> list[Any]:
        return [o for k, o in self.requests if k == kind]

    async def _stream(self, _model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        assert context.system_prompt == SUMMARIZATION_SYSTEM_PROMPT
        kind = _kind(context)
        self.requests.append((kind, options))
        steps = self.answers.get(kind) or []
        step = steps.pop(0) if steps else "ok"
        if step == "502":
            message = AssistantMessage(content=[], stop_reason="error", error_message=_502)
            yield AssistantErrorEvent(reason="error", error=message, error_message=_502)
            return
        if step == "ok":
            yield TextDeltaEvent(delta=_SUMMARY)
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[TextContent(text=_SUMMARY)], stop_reason="stop"),
            )
            return
        reason, stop = {
            "length-reason": ("length", "length"),
            "length-event": ("length", "stop"),
            "length-message": ("stop", "length"),
        }[step]
        yield TextDeltaEvent(delta=_CUT)
        yield AssistantDoneEvent(
            reason=reason,  # type: ignore[arg-type]
            message=AssistantMessage(content=[TextContent(text=_CUT)], stop_reason=stop),
        )


def _auth(_model: Any) -> dict[str, Any]:
    return {"apiKey": "fake-key", "headers": {}}


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)], stop_reason="stop")


_HISTORY = CompactionPreparation(
    first_kept_entry_id="k", messages_to_summarize=[_user("history")], tokens_before=10
)
_SPLIT = CompactionPreparation(
    first_kept_entry_id="k",
    messages_to_summarize=[_user("history")],
    turn_prefix_messages=[_assistant("prefix")],
    is_split_turn=True,
    tokens_before=10,
)


async def _branch_entries() -> list[Any]:
    session = Session(MemorySessionStorage())
    await session.append_message(_user("explore a side idea"))
    await session.append_message(_assistant("side idea done"))
    return await session.get_entries()


async def _summarize(site: str, model: Model, level: str | None) -> Any:
    """Run one site's requests through the module entry points."""

    if site == "history":
        return await compact(model, _auth, _HISTORY, thinking_level=level)
    if site == "split":
        return await compact(model, _auth, _SPLIT, thinking_level=level)
    return await generate_branch_summary(model, _auth, await _branch_entries(), thinking_level=level)


_SITES = {"history": ("history",), "split": ("history", "prefix"), "branch": ("branch",)}


# === 1. the session's level, fitted to the model ==============================


@pytest.mark.parametrize("site", list(_SITES))
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
async def test_every_summary_request_asks_at_the_sessions_level(site: str, level: str) -> None:
    provider = _Provider()
    await _summarize(site, _REASONING, level)
    assert sorted(k for k, _ in provider.requests) == sorted(_SITES[site])
    assert [o.reasoning for _, o in provider.requests] == [level] * len(_SITES[site])


@pytest.mark.parametrize("site", list(_SITES))
@pytest.mark.parametrize(
    ("model", "level", "sent"),
    [
        # no xhigh / max on the row: the nearest level below, as the adapters clamp a turn's
        (_REASONING, "xhigh", "high"),
        (_REASONING, "max", "high"),
        # a level the row lists as unsupported: the next one up
        (Model(api=_API, id="m", provider="p", reasoning=True, thinking_level_map={"minimal": None}), "minimal", "low"),
        # a row that names max: an old xhigh goes up to it (ADR-0266)
        (Model(api=_API, id="m", provider="p", reasoning=True, thinking_level_map={"max": "max"}), "xhigh", "max"),
    ],
    ids=["xhigh-on-high-row", "max-on-high-row", "minimal-unsupported", "xhigh-on-max-row"],
)
async def test_the_level_is_fitted_to_the_model(site: str, model: Model, level: str, sent: str) -> None:
    provider = _Provider()
    await _summarize(site, model, level)
    assert {o.reasoning for _, o in provider.requests} == {sent}


@pytest.mark.parametrize("site", list(_SITES))
@pytest.mark.parametrize(
    ("model", "level"),
    [
        (_REASONING, None),
        (_REASONING, "off"),
        (_PLAIN, "high"),
        (_PLAIN, "off"),
        # a row that cannot switch reasoning off: ``off`` is still not fitted up to
        # ``minimal`` here - the turn sends reasoning off at ``off`` too (#507)
        (Model(api=_API, id="m", provider="p", reasoning=True, thinking_level_map={"off": None}), "off"),
    ],
    ids=["no-level", "off", "non-reasoning-at-high", "non-reasoning-off", "off-on-a-no-off-row"],
)
async def test_off_and_a_non_reasoning_model_still_ask_with_reasoning_off(
    site: str, model: Model, level: str | None
) -> None:
    """pi: ``if (model.reasoning && thinkingLevel && thinkingLevel !== "off")``."""

    provider = _Provider()
    await _summarize(site, model, level)
    assert provider.requests
    assert {o.reasoning for _, o in provider.requests} == {None}


# === 2. no output cap of its own ===============================================


@pytest.mark.parametrize("site", list(_SITES))
@pytest.mark.parametrize("level", [None, "high"])
@pytest.mark.parametrize("model_max", [0, 1000, 100_000])
async def test_no_summary_request_sets_its_own_output_cap(
    site: str, level: str | None, model_max: int
) -> None:
    """Before #506: 13107 / 8192 / 2048, clamped by ``model.max_tokens``. Now the
    adapter's cap for the model applies (cut to the window, #563), as pi #11036."""

    provider = _Provider()
    model = Model(api=_API, id="m", provider="p", reasoning=True, max_tokens=model_max)
    await _summarize(site, model, level)
    assert provider.requests
    assert {o.max_tokens for _, o in provider.requests} == {None}


# === 3. a cut answer is refused ================================================


@pytest.mark.parametrize("cut", ["length-reason", "length-event", "length-message"])
@pytest.mark.parametrize(
    ("site", "kind", "code", "text"),
    [
        ("history", "history", "compaction", _HISTORY_CUT),
        ("split", "history", "compaction", _HISTORY_CUT),
        ("split", "prefix", "compaction", _PREFIX_CUT),
        ("branch", "branch", "invalid_state", _BRANCH_CUT),
    ],
    ids=["history", "split-history", "split-prefix", "branch"],
)
async def test_a_summary_that_stopped_at_its_limit_is_refused(
    cut: str, site: str, kind: str, code: str, text: str
) -> None:
    provider = _Provider(**{kind: [cut]})
    with pytest.raises(AgentHarnessError) as caught:
        await _summarize(site, _REASONING, "high")
    assert caught.value.code == code
    assert str(caught.value) == text
    assert len(provider.options(kind)) == 1  # sent once: nothing retries it here


@pytest.mark.parametrize("site", list(_SITES))
async def test_a_whole_answer_is_still_the_summary(site: str) -> None:
    _Provider()
    result = await _summarize(site, _REASONING, "high")
    summary = result if isinstance(result, str) else result.summary
    assert summary.count(_SUMMARY) == len(_SITES[site])
    assert _CUT + "\n" not in summary


# === through the harness: the level read at the call; #409 unchanged ============


async def _compactable() -> Session:
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(_user("x" * 30_000))
    return session


def _rig(session: Session, level: str | None) -> tuple[AgentHarness, list[Any]]:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=_REASONING,
            session=session,
            thinking_level=level,
            get_api_key_and_headers=_auth,
            settings_manager=SettingsManager.in_memory({"retry": {"baseDelayMs": 1}}),
        )
    )
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    return harness, events


async def test_the_harness_passes_the_level_it_holds_when_it_compacts() -> None:
    provider = _Provider()
    harness, _ = _rig(await _compactable(), "high")
    await harness.compact()
    await harness.set_thinking_level("low")
    for _ in range(6):
        await harness._session.append_message(_user("y" * 30_000))  # type: ignore[union-attr]
    await harness.compact()
    assert [o.reasoning for _, o in provider.requests] == ["high", "low"]
    assert {o.max_tokens for _, o in provider.requests} == {None}


async def test_the_harness_passes_the_level_to_a_branch_summary() -> None:
    provider = _Provider()
    session = Session(MemorySessionStorage())
    root = await session.append_message(_user("start"))
    await session.append_message(_assistant("ok"))
    await session.append_message(_user("explore a side idea"))
    await session.append_message(_assistant("side idea done"))
    harness, _ = _rig(session, "medium")
    result = await harness.navigate_tree(root, NavigateTreeOptions(summarize=True))
    assert result.summary_entry is not None
    assert [(k, o.reasoning, o.max_tokens) for k, o in provider.requests] == [("branch", "medium", None)]


async def test_a_cut_compaction_is_not_retried_and_writes_nothing() -> None:
    provider = _Provider(history=["length-reason"])
    session = await _compactable()
    harness, events = _rig(session, "high")
    with pytest.raises(AgentHarnessError) as caught:
        await harness.compact()
    assert (caught.value.code, str(caught.value)) == ("compaction", _HISTORY_CUT)
    assert len(provider.requests) == 1
    assert not [e for e in events if isinstance(e, (AutoRetryStartEvent, AutoRetryEndEvent))]
    assert not [e for e in await session.get_entries() if e.type == "compaction"]
    ends = [e for e in events if e.type == "compaction_end"]
    assert [(e.result, e.error_message) for e in ends] == [(None, _HISTORY_CUT)]


async def test_a_retried_request_asks_at_the_level_again() -> None:
    """#409: a 502 is sent again; the second request carries the level too."""

    provider = _Provider(history=["502"])
    harness, events = _rig(await _compactable(), "high")
    await harness.compact()
    assert [o.reasoning for _, o in provider.requests] == ["high", "high"]
    assert [type(e).__name__ for e in events if isinstance(e, (AutoRetryStartEvent, AutoRetryEndEvent))] == [
        "AutoRetryStartEvent",
        "AutoRetryEndEvent",
    ]


# === the real OpenAI Responses adapter: what goes on the wire ====================


class _Raw:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def parse(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            for event in self._events:
                yield event

        return gen()


class _ResponsesClient:
    """Stands in for ``AsyncOpenAI``: records ``responses.create``'s params and
    answers ``completed`` or ``incomplete`` (OpenAI's ``max_output_tokens`` stop)."""

    def __init__(self, sent: list[dict[str, Any]], status: str) -> None:
        self.sent = sent
        self.status = status
        self.responses = self
        self.with_raw_response = self

    def create(self, **params: Any) -> _Raw:
        self.sent.append(params)
        text = _SUMMARY if self.status == "completed" else _CUT
        item = {"type": "message", "id": "m1", "content": [{"type": "output_text", "text": text}]}
        return _Raw(
            [
                {"type": "response.created", "response": {"id": "r1"}},
                {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "m1"}},
                {"type": "response.output_text.delta", "output_index": 0, "delta": text},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed" if self.status == "completed" else "response.incomplete",
                    "response": {
                        "status": self.status,
                        "incomplete_details": None if self.status == "completed" else {"reason": "max_output_tokens"},
                        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    },
                },
            ]
        )


_RESPONSES = Model(
    api="openai-responses",
    id="o4-mini",
    provider="openai",
    base_url="http://127.0.0.1:9/v1",
    reasoning=True,
    context_window=200_000,
    max_tokens=100_000,
)


def _wire(monkeypatch: pytest.MonkeyPatch, status: str) -> list[dict[str, Any]]:
    import aelix_ai.providers.openai_responses as responses

    sent: list[dict[str, Any]] = []
    responses.register_all()
    monkeypatch.setattr(responses, "create_async_client", lambda **_kw: _ResponsesClient(sent, status))
    return sent


@pytest.mark.parametrize("site", list(_SITES))
async def test_the_responses_payload_carries_the_level_and_no_cap(
    monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """The 400 of the issue: before #506 each payload here was ``{"effort":
    "none"}`` with ``max_output_tokens`` 13107 / 8192 / 2048."""

    sent = _wire(monkeypatch, "completed")
    await _summarize(site, _RESPONSES, "high")
    assert len(sent) == len(_SITES[site])
    for params in sent:
        assert params["reasoning"]["effort"] == "high"
        assert "max_output_tokens" not in params


@pytest.mark.parametrize("site", list(_SITES))
async def test_the_responses_payload_at_off_is_unchanged(monkeypatch: pytest.MonkeyPatch, site: str) -> None:
    """``off`` keeps the adapter's reasoning-off path (``effort: "none"``); a row
    that cannot take it is the catalog's to describe (#507)."""

    sent = _wire(monkeypatch, "completed")
    await _summarize(site, _RESPONSES, "off")
    assert [p["reasoning"]["effort"] for p in sent] == ["none"] * len(_SITES[site])


@pytest.mark.parametrize(
    ("site", "text"),
    [("history", _HISTORY_CUT), ("split", _HISTORY_CUT), ("branch", _BRANCH_CUT)],
)
async def test_an_incomplete_responses_answer_is_refused(
    monkeypatch: pytest.MonkeyPatch, site: str, text: str
) -> None:
    _wire(monkeypatch, "incomplete")
    with pytest.raises(AgentHarnessError) as caught:
        await _summarize(site, _RESPONSES, "high")
    # a split turn's two requests run together: whichever is refused first is reported
    allowed = {text, _PREFIX_CUT} if site == "split" else {text}
    assert str(caught.value) in allowed
