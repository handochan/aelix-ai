"""#278 — moving ``openai`` from 1.109.1 to 2.x changed nothing on aelix's wire.

Each scenario below drives ``stream_openai_completions`` or
``stream_openai_responses`` through the REAL ``openai.AsyncOpenAI``, whose
``http_client`` answers from ``httpx.MockTransport`` with a recorded body. What
the SDK put on the wire (method, path, JSON body, the headers aelix controls)
and every ``AssistantMessageEvent`` the adapter yielded - or its error text -
is compared with ``fixtures/openai_sdk_wire_278.json``.

That file was RECORDED on the base build, 529b997e with openai 1.109.1 under
CPython 3.13.13, by running this module as a script::

    <python of a 529b997e environment> tests/providers/test_openai_sdk_wire_278.py \\
        --record tests/providers/fixtures/openai_sdk_wire_278.json

so every leg that runs these rows - 3.11 through 3.14, Ubuntu and Windows -
proves its SDK reproduces the 1.109.1 requests, events and error messages
byte for byte. Measured before the fixture existed (``.omc/probes/278-live``):
the same 13 scenarios gave identical dumps under openai 1.109.1 (3.11, 3.13),
2.7.2 (3.13, 3.14) and 2.54.0 (3.11, 3.13, 3.14). Under 1.109.1 on 3.14 the
three compat Responses scenarios end in #262's AttributeError instead.

The compat Responses bodies omit fields openai's models require
(``sequence_number``, ``logprobs``, ``created_at`` ...), so they exercise the
SDK's construct-without-validation fallback; the strict ones carry every field
and exercise the validated path. A keyless call is deliberately absent: its
behaviour changed on purpose (``test_anthropic_no_env_credential_374.py``).

Regenerate the fixture only from a base environment, and say so in the commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers import openai_completions as completions_adapter
from aelix_ai.providers import openai_responses as responses_adapter
from aelix_ai.streaming import Context, Model
from aelix_ai.tools import Tool

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "openai_sdk_wire_278.json"

_TOOL = Tool(
    name="read",
    description="Read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)

#: The request headers aelix decides. ``user-agent`` and ``x-stainless-*`` carry
#: the SDK's own version and are expected to change with it.
_KEPT_HEADERS = (
    "authorization",
    "content-type",
    "accept",
    "x-team",
    "session_id",
    "x-client-request-id",
    "x-session-affinity",
    "openai-organization",
    "openai-project",
)


# === recorded bodies ===========================================================


def _sse(payload: dict[str, Any], event: str | None = None) -> str:
    return (f"event: {event}\n" if event else "") + "data: " + json.dumps(payload) + "\n\n"


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    return _sse(
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


_COMPLETIONS_TEXT = "".join(
    [
        _chunk({"role": "assistant", "content": "Hel"}),
        _chunk({"content": "lo"}),
        _chunk({}, "stop"),
        _sse(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "m",
                "choices": [],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 2,
                    "total_tokens": 9,
                    "prompt_tokens_details": {"cached_tokens": 3},
                },
            }
        ),
        "data: [DONE]\n\n",
    ]
)

_COMPLETIONS_TOOL = "".join(
    [
        _chunk({"role": "assistant", "content": None, "reasoning_content": "think"}),
        _chunk(
            {
                "tool_calls": [
                    {"index": 0, "id": "call_1", "type": "function", "function": {"name": "read", "arguments": ""}}
                ]
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"path":'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "a.txt"}'}}]}),
        _chunk({}, "tool_calls"),
        "data: [DONE]\n\n",
    ]
)


def _responses_body(*, strict: bool, tool: bool, reasoning: bool) -> str:
    seq = [0]

    def ev(payload: dict[str, Any]) -> str:
        if strict:
            payload = {**payload, "sequence_number": seq[0]}
            seq[0] += 1
        return _sse(payload, payload["type"])

    base: dict[str, Any] = {"id": "resp_1", "status": "in_progress", "output": []}
    if strict:
        base.update(
            {
                "object": "response",
                "created_at": 1,
                "model": "m",
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
            }
        )
    out = [ev({"type": "response.created", "response": base})]
    items: list[dict[str, Any]] = []
    idx = 0
    if reasoning:
        r_item = {"type": "reasoning", "id": "rs_1", "summary": []}
        out.append(ev({"type": "response.output_item.added", "output_index": idx, "item": r_item}))
        out.append(
            ev(
                {
                    "type": "response.reasoning_summary_text.delta",
                    "item_id": "rs_1",
                    "output_index": idx,
                    "summary_index": 0,
                    "delta": "plan",
                }
            )
        )
        r_done = {**r_item, "summary": [{"type": "summary_text", "text": "plan"}]}
        out.append(ev({"type": "response.output_item.done", "output_index": idx, "item": r_done}))
        items.append(r_done)
        idx += 1
    if tool:
        f_item: dict[str, Any] = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read",
            "arguments": "",
        }
        if strict:
            f_item["status"] = "in_progress"
        out.append(ev({"type": "response.output_item.added", "output_index": idx, "item": f_item}))
        for piece in ('{"path":', ' "a.txt"}'):
            out.append(
                ev(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc_1",
                        "output_index": idx,
                        "delta": piece,
                    }
                )
            )
        out.append(
            ev(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_1",
                    "output_index": idx,
                    "arguments": '{"path": "a.txt"}',
                }
            )
        )
        f_done = {**f_item, "arguments": '{"path": "a.txt"}'}
        if strict:
            f_done["status"] = "completed"
        out.append(ev({"type": "response.output_item.done", "output_index": idx, "item": f_done}))
        items.append(f_done)
    else:
        m_item: dict[str, Any] = {"type": "message", "id": "msg_1", "role": "assistant", "content": []}
        if strict:
            m_item["status"] = "in_progress"
        out.append(ev({"type": "response.output_item.added", "output_index": idx, "item": m_item}))
        extra = {"logprobs": []} if strict else {}
        for piece in ("Hel", "lo"):
            out.append(
                ev(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_1",
                        "output_index": idx,
                        "content_index": 0,
                        "delta": piece,
                        **extra,
                    }
                )
            )
        part = {"type": "output_text", "text": "Hello", **({"annotations": []} if strict else {})}
        m_done = {**m_item, "content": [part]}
        if strict:
            m_done["status"] = "completed"
        out.append(ev({"type": "response.output_item.done", "output_index": idx, "item": m_done}))
        items.append(m_done)
    done = {
        **base,
        "status": "completed",
        "output": items,
        "usage": {
            "input_tokens": 7,
            "output_tokens": 2,
            "total_tokens": 9,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
    }
    out.append(ev({"type": "response.completed", "response": done}))
    return "".join(out)


# === the rig ====================================================================


class _Recorder:
    def __init__(
        self,
        status: int,
        body: str,
        content_type: str = "text/event-stream",
        *,
        raise_exc: Exception | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.content_type = content_type
        self.raise_exc = raise_exc
        self.headers = headers or {}
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        raw = request.content.decode() if request.content else ""
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = raw
        kept: dict[str, str] = {}
        for name in _KEPT_HEADERS:
            if name in request.headers:
                value = request.headers[name]
                kept[name] = "Bearer <key>" if name == "authorization" and value == "Bearer sk-fake" else value
        self.requests.append({"method": request.method, "path": request.url.path, "body": body, "headers": kept})
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(
            self.status, headers={"content-type": self.content_type, **self.headers}, content=self.body.encode()
        )


def _summary(event: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": type(event).__name__}
    for name in ("delta", "content_index", "reason", "content", "tool_call_id", "tool_name"):
        if hasattr(event, name):
            value = getattr(event, name)
            out[name] = asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
    tool_call = getattr(event, "tool_call", None)
    if tool_call is not None:
        out["tool_call"] = asdict(tool_call)
    message = getattr(event, "message", None) or getattr(event, "error", None)
    if message is not None and out["type"] in ("AssistantDoneEvent", "AssistantErrorEvent"):
        as_dict = asdict(message)
        as_dict.pop("timestamp", None)
        out["message"] = as_dict
    return out


def _context(*, tools: bool, history: bool) -> Context:
    messages: list[Any] = [UserMessage(content=[TextContent(text="hi")])]
    if history:
        messages += [
            AssistantMessage(
                content=[ToolCallContent(tool_call_id="call_0", tool_name="read", input={"path": "x"})],
                stop_reason="toolUse",
                api="openai-completions",
                provider="p",
                model="m",
            ),
            ToolResultMessage(tool_call_id="call_0", tool_name="read", content=[TextContent(text="file body")]),
        ]
    return Context(system_prompt="be terse", messages=messages, tools=[_TOOL] if tools else [])


_CM = Model(api="openai-completions", id="m", provider="p", base_url="http://fake.invalid/v1")
_CM_OPENAI = Model(
    api="openai-completions", id="gpt-4o-mini", provider="openai", base_url="https://api.openai.com/v1"
)
_RM = Model(api="openai-responses", id="m", provider="p", base_url="http://fake.invalid/v1")
_RM_OPENAI = Model(
    api="openai-responses", id="gpt-5", provider="openai", base_url="https://api.openai.com/v1", reasoning=True
)


def _error_body(message: str, **extra: str) -> str:
    return json.dumps({"error": {"message": message, "type": "invalid_request_error", **extra}})


#: name -> (api, model, context kwargs, recorder factory, extra options)
_SCENARIOS: dict[str, tuple[str, Model, dict[str, bool], Any, dict[str, Any]]] = {
    "comp-text": ("c", _CM, {"tools": False, "history": False}, lambda: _Recorder(200, _COMPLETIONS_TEXT), {}),
    "comp-tool": ("c", _CM, {"tools": True, "history": True}, lambda: _Recorder(200, _COMPLETIONS_TOOL), {}),
    "comp-openai-cache-long": (
        "c",
        _CM_OPENAI,
        {"tools": True, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"cache_retention": "long", "session_id": "sess-1", "headers": {"x-team": "t1"}},
    ),
    "comp-http-400": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(400, _error_body("bad thing", code="bad"), "application/json"),
        {},
    ),
    "comp-http-429": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(
            429,
            json.dumps({"error": {"message": "slow down", "type": "rate_limit"}}),
            "application/json",
            headers={"retry-after": "1"},
        ),
        {},
    ),
    "comp-connect-error": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, "", raise_exc=httpx.ConnectError("refused")),
        {},
    ),
    "resp-compat-text": (
        "r",
        _RM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _responses_body(strict=False, tool=False, reasoning=False)),
        {},
    ),
    "resp-compat-tool": (
        "r",
        _RM,
        {"tools": True, "history": True},
        lambda: _Recorder(200, _responses_body(strict=False, tool=True, reasoning=False)),
        {},
    ),
    "resp-strict-text": (
        "r",
        _RM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _responses_body(strict=True, tool=False, reasoning=False)),
        {},
    ),
    "resp-strict-tool-reasoning": (
        "r",
        _RM_OPENAI,
        {"tools": True, "history": True},
        lambda: _Recorder(200, _responses_body(strict=True, tool=True, reasoning=True)),
        {"cache_retention": "long", "session_id": "sess-2", "reasoning_effort": "high"},
    ),
    "resp-compat-reasoning-text": (
        "r",
        _RM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _responses_body(strict=False, tool=False, reasoning=True)),
        {},
    ),
    "resp-http-400": (
        "r",
        _RM,
        {"tools": False, "history": False},
        lambda: _Recorder(400, _error_body("bad thing"), "application/json"),
        {},
    ),
    "resp-connect-error": (
        "r",
        _RM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, "", raise_exc=httpx.ConnectError("refused")),
        {},
    ),
}


async def _run(name: str) -> dict[str, Any]:
    api, model, ctx_kw, make_recorder, extra = _SCENARIOS[name]
    recorder = make_recorder()
    client = openai.AsyncOpenAI(
        api_key="sk-fake",
        base_url=model.base_url,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)),
    )
    if api == "c":
        stream = completions_adapter.stream_openai_completions
        opts: Any = completions_adapter.OpenAICompletionsOptions(
            api_key="sk-fake", client=client, max_retries=0, **extra
        )
    else:
        stream = responses_adapter.stream_openai_responses
        opts = responses_adapter.OpenAIResponsesOptions(api_key="sk-fake", client=client, max_retries=0, **extra)
    events = [_summary(event) async for event in stream(model, _context(**ctx_kw), opts)]
    await client.close()
    # A JSON round trip so a fresh run compares like for like with the file
    # (tuples become lists, keys become strings).
    return json.loads(json.dumps({"requests": recorder.requests, "events": events}, default=str))


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_was_recorded_on_the_old_sdk(recorded: dict[str, Any]) -> None:
    """The comparison is only worth something against the build being
    replaced: 1.109.1 is the openai every release up to 0.1.0b2 pins."""

    assert recorded["meta"]["openai"] == "1.109.1", recorded["meta"]
    assert sorted(recorded["scenarios"]) == sorted(_SCENARIOS)


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
async def test_the_wire_and_the_events_are_what_openai_1_109_produced(
    recorded: dict[str, Any], name: str
) -> None:
    got = await _run(name)
    want = recorded["scenarios"][name]
    assert got["requests"] == want["requests"], name
    assert got["events"] == want["events"], name


def _record(path: Path) -> None:
    async def all_scenarios() -> dict[str, Any]:
        return {name: await _run(name) for name in sorted(_SCENARIOS)}

    data = {
        "meta": {"openai": openai.__version__, "python": sys.version.split()[0]},
        "scenarios": asyncio.run(all_scenarios()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"recorded {len(data['scenarios'])} scenarios on openai {openai.__version__} -> {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    _record(parser.parse_args().record)
