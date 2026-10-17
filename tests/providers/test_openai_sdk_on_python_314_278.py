"""#278 / #262 — aelix supports Python 3.14, so the openai SDK it pins must too.

THE DEFECT (#262). Python 3.14 made ``typing.Union[...]`` a slotted object.
openai's ``construct_type`` validates a streamed event through pydantic FIRST;
only when that fails does it fall back to ``_build_discriminated_union_meta``,
which on every openai up to and including 2.7.1 caches its result with
``union.__discriminator__ = details``. On 3.14 that line is::

    AttributeError: 'typing.Union' object has no attribute '__discriminator__'
                    and no __dict__ for setting new attributes

openai 2.7.2 caches in a ``WeakKeyDictionary`` instead (diffed: the only change
to ``_models.py`` between 2.7.1 and 2.7.2). So ``aelix-ai`` pins
``openai>=2.7.2,<3`` (ADR-0265).

IT IS LATENT, which is why the rows below feed the SDK events that FAIL
validation. A server whose every event carries all the fields openai's models
require never reaches the write: measured on 529b997e under 3.14.5 with the old
pin (openai 1.109.1), a real ``aelix -p`` turn with a tool round trip against a
local fake Responses server PASSED when the server sent strict events and died
with the message above when it sent the compat shape many OpenAI-compatible
servers send (no ``sequence_number``, no ``logprobs``, a ``response`` without
``created_at``). The same probe on 3.13 passed both ways.

WHICH ROWS GO RED WHERE, with the pin reverted to ``openai>=1.66,<2.0`` and the
lock regenerated (1.109.1):

* ``test_the_declared_openai_floor_is_the_python_314_fix`` — every interpreter;
  it reads the pin, not the environment.
* ``test_the_installed_openai_is_inside_the_declared_range`` — every
  interpreter whose environment still has the old SDK (a stale ``--frozen``
  sync of an old lock).
* ``test_the_fallback_writes_nothing_onto_the_union`` — every interpreter: on
  3.11-3.13 the old SDK's write SUCCEEDS silently, and this row is the one that
  sees it there.
* ``test_construct_type_survives_an_event_that_fails_validation`` and
  ``test_a_compat_responses_turn_completes_through_the_real_sdk`` — 3.14 only,
  with #262's exact message.
"""

from __future__ import annotations

import json
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any, get_args

import httpx
import openai
import pydantic
import pytest
from aelix_ai.messages import TextContent, ToolCallContent, UserMessage
from aelix_ai.providers import openai_completions as completions_adapter
from aelix_ai.providers import openai_responses as responses_adapter
from aelix_ai.streaming import AssistantDoneEvent, AssistantErrorEvent, Context, Model
from aelix_ai.tools import Tool
from openai._models import construct_type
from openai.types.responses import ResponseStreamEvent, ResponseTextDeltaEvent
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
AELIX_AI_PYPROJECT = REPO_ROOT / "packages" / "aelix-ai" / "pyproject.toml"

#: The first openai that does not assign ``__discriminator__`` onto a union.
FIRST_FIXED = Version("2.7.2")
#: The last one that does (measured: same failure as 1.109.1 on 3.14.5).
LAST_BROKEN = Version("2.7.1")

#: A text delta missing ``item_id``/``output_index``/``content_index``/
#: ``sequence_number``/``logprobs``: every variant of the union rejects it.
_INVALID_TEXT_DELTA: dict[str, Any] = {"type": "response.output_text.delta", "delta": "hi"}


def _openai_requirement() -> Requirement:
    project = tomllib.loads(AELIX_AI_PYPROJECT.read_text(encoding="utf-8"))["project"]
    found = [Requirement(dep) for dep in project["dependencies"] if Requirement(dep).name == "openai"]
    assert len(found) == 1, found
    return found[0]


def _union() -> Any:
    """The bare ``Union[...]`` inside ``Annotated[Union[...], PropertyInfo(...)]``
    — the object ``construct_type`` hands to the fallback."""

    return get_args(ResponseStreamEvent)[0]


# === the pin ==================================================================


def test_the_declared_openai_floor_is_the_python_314_fix() -> None:
    spec = _openai_requirement().specifier
    assert FIRST_FIXED in spec, spec
    assert LAST_BROKEN not in spec, f"{spec} admits openai {LAST_BROKEN}, which dies on 3.14 (#262)"
    assert Version("1.109.1") not in spec, spec
    # The ceiling (ADR-0265): openai 3.0 moved to HTTPX2 and stopped installing
    # httpx/certifi, which this repo's TLS handling and tests are written for.
    assert Version("3.0.0") not in spec, spec


def test_the_installed_openai_is_inside_the_declared_range() -> None:
    installed = Version(metadata.version("openai"))
    assert installed in _openai_requirement().specifier, (
        f"openai {installed} is installed but aelix-ai declares {_openai_requirement()}; "
        "the environment is stale (re-run `uv sync --all-packages`)."
    )
    assert installed == Version(openai.__version__)


# === the SDK's fallback, driven directly ======================================


def test_the_payload_really_fails_validation() -> None:
    """Rig control: if this event validated, the rows below would never reach
    the fallback and would pass on a broken SDK too."""

    with pytest.raises(pydantic.ValidationError):
        pydantic.TypeAdapter(_union()).validate_python(_INVALID_TEXT_DELTA)


def test_construct_type_survives_an_event_that_fails_validation() -> None:
    """Old pin on 3.14: AttributeError, #262's message."""

    out = construct_type(type_=ResponseStreamEvent, value=dict(_INVALID_TEXT_DELTA))
    assert isinstance(out, ResponseTextDeltaEvent), type(out)
    assert out.delta == "hi"


def test_the_fallback_writes_nothing_onto_the_union() -> None:
    """The write IS the defect. On 3.11-3.13 it succeeds silently, which is why
    no leg below 3.14 ever saw #262 — and why this row is the one that goes red
    there when the pin regresses."""

    construct_type(type_=ResponseStreamEvent, value=dict(_INVALID_TEXT_DELTA))
    assert getattr(_union(), "__discriminator__", None) is None


# === through aelix's adapters and the real SDK ================================


def _sse(payload: dict[str, Any]) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"


#: What a compat server streams for one tool call: no sequence_number, no
#: status on the item, a response object without created_at/model/tools.
_COMPAT_TOOL_CALL = "".join(
    _sse(e)
    for e in (
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read", "arguments": ""},
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "output_index": 0,
            "delta": '{"path": "a.txt"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "read",
                "arguments": '{"path": "a.txt"}',
            },
        },
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed", "output": [], "usage": {"input_tokens": 3, "output_tokens": 2}},
        },
    )
)

_TOOL = Tool(
    name="read",
    description="Read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def _client(body: str, requests: list[httpx.Request]) -> openai.AsyncOpenAI:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    return openai.AsyncOpenAI(
        api_key="sk-fake-278",
        base_url="http://fake.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_a_compat_responses_turn_completes_through_the_real_sdk() -> None:
    """The adapter path #262 died on: openai-responses, the real SDK's SSE
    parsing and ``construct_type``, a stream that fails validation."""

    requests: list[httpx.Request] = []
    model = Model(api="openai-responses", id="m", provider="compat", base_url="http://fake.invalid/v1")
    ctx = Context(messages=[UserMessage(content=[TextContent(text="read a.txt")])], tools=[_TOOL])
    opts = responses_adapter.OpenAIResponsesOptions(api_key="sk-fake-278", client=_client(_COMPAT_TOOL_CALL, requests))
    events = [e async for e in responses_adapter.stream_openai_responses(model, ctx, opts)]

    assert len(requests) == 1 and requests[0].url.path == "/v1/responses"
    last = events[-1]
    assert not isinstance(last, AssistantErrorEvent), last.error.error_message
    assert isinstance(last, AssistantDoneEvent)
    assert last.reason == "toolUse"
    calls = [c for c in last.message.content if isinstance(c, ToolCallContent)]
    assert [(c.tool_name, c.input) for c in calls] == [("read", {"path": "a.txt"})]
    assert last.message.response_id == "resp_1"


async def test_a_completions_turn_completes_through_the_real_sdk() -> None:
    """Control for the other OpenAI path: a chat-completions chunk is a model,
    not a discriminated union at the top, so it never reached the write — it
    passes with either pin and must keep passing with the new one."""

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return "data: " + json.dumps(
            {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        ) + "\n\n"

    body = "".join(
        [
            chunk({"role": "assistant", "content": None,
                   "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                   "function": {"name": "read", "arguments": '{"path": "a.txt"}'}}]}),
            chunk({}, "tool_calls"),
            "data: [DONE]\n\n",
        ]
    )
    requests: list[httpx.Request] = []
    model = Model(api="openai-completions", id="m", provider="compat", base_url="http://fake.invalid/v1")
    ctx = Context(messages=[UserMessage(content=[TextContent(text="read a.txt")])], tools=[_TOOL])
    opts = completions_adapter.OpenAICompletionsOptions(api_key="sk-fake-278", client=_client(body, requests))
    events = [e async for e in completions_adapter.stream_openai_completions(model, ctx, opts)]

    assert len(requests) == 1 and requests[0].url.path == "/v1/chat/completions"
    last = events[-1]
    assert isinstance(last, AssistantDoneEvent), getattr(getattr(last, "error", None), "error_message", last)
    assert last.reason == "toolUse"
    calls = [c for c in last.message.content if isinstance(c, ToolCallContent)]
    assert [(c.tool_name, c.input) for c in calls] == [("read", {"path": "a.txt"})]
