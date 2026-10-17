"""#278 — moving ``openai`` from 1.109.1 to 2.x changed nothing on aelix's wire.

Each scenario below drives ``stream_openai_completions`` or
``stream_openai_responses`` through the REAL openai SDK - the client the
adapter builds for itself (``_openai_client.create_async_client``), unless the
scenario hands it one - with every ``httpx.AsyncClient`` answering from
``httpx.MockTransport`` with a recorded body. What went on the wire (method,
path, JSON body, every header but the SDK's own version stamps:
``user-agent``, ``x-stainless-*`` and ``content-length``, which follows the
body) and every ``AssistantMessageEvent`` the adapter yielded - or its error
text - is compared with ``fixtures/openai_sdk_wire_278.json``.

That file was RECORDED on the base build, 529b997e with openai 1.109.1 under
CPython 3.13.13, by running this module as a script::

    <python of a 529b997e environment> tests/providers/test_openai_sdk_wire_278.py \\
        --record tests/providers/fixtures/openai_sdk_wire_278.json

so every leg that runs these rows - 3.11 through 3.14, Ubuntu and Windows -
proves its SDK reproduces the 1.109.1 requests, events and error messages
byte for byte. Measured before the first fixture existed
(``.omc/probes/278-live``): the first 13 scenarios gave identical dumps under
openai 1.109.1 (3.11, 3.13), 2.7.2 (3.13, 3.14) and 2.54.0 (3.11, 3.13, 3.14).
Under 1.109.1 on 3.14 the three compat Responses scenarios end in #262's
AttributeError instead.

Review round 2 re-recorded the file with the adapters' own client (the first
recording handed every scenario a client, so the headers the adapter builds
never reached it) and added what that blind spot hid:

* ``*-env-exported``: ``OPENAI_CUSTOM_HEADERS`` (with an ``Authorization``),
  ``OPENAI_ADMIN_KEY`` and ``OPENAI_API_KEY`` exported. 1.109.1 read none of
  them; 2.34 and later merge ``OPENAI_CUSTOM_HEADERS`` over the provider's key
  unless the client drops it. ``OPENAI_ORG_ID`` / ``OPENAI_PROJECT_ID`` are
  exported too: 1.x sent them, and still does (ADR-0254 §3).
* ``comp-keyless-*``: a call with no key, which openai 2.34 refuses unless the
  adapter settles it (``_keyless_client_auth``): no auth header, each spelling
  of one, a payload hook's ``extra_headers`` ``Authorization``, and a
  caller-supplied client with a key of its own. ``resp-keyless-*``: the
  Responses adapter's own keyless settling, unchanged by #278.

Review round 3 added two ``comp-keyless-*`` scenarios with the hook's header
spelled ``authorization``, alone and over an ``authorization`` of the
request's own: on dd8b86aa the second sent the request's header AND the hook's
(openai 1.109.1 sent the hook's alone). The keyless cases whose wire changed on
purpose - two different spellings of ``Authorization`` meeting, where 1.x sent
both lines and aelix now sends one - are pinned in
``test_anthropic_no_env_credential_374.py``.

The narrow finish after review round 3 added two more, recorded the same way:
a hook's ``authorization`` on a request WITH a key, and on a caller-supplied
client. 1.109.1 sent the SDK's ``Authorization: Bearer <key>`` and the hook's
line both; so does this build, because the collapse above is for the keyless
request on the adapter's own client only (a variant that collapsed these too
passed every row before them).
The SDK's retry WAIT is not a wire shape; ``test_openai_sdk_retry_wait_278.py``
holds it.

Regenerate the fixture only from a base environment, and say so in the commit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Iterator
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

#: Headers that carry the SDK's own version (``user-agent``, ``x-stainless-*``),
#: expected to change with it, and ``content-length``, which follows the body
#: (compared parsed). Every other header line is recorded.
_DROPPED_HEADERS = ("user-agent", "content-length")


def _recorded_headers(headers: httpx.Headers) -> list[list[str]]:
    return sorted(
        [name.lower(), value]
        for name, value in headers.multi_items()
        if name.lower() not in _DROPPED_HEADERS and not name.lower().startswith("x-stainless-")
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
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": body,
                "headers": _recorded_headers(request.headers),
            }
        )
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


#: name -> (api, model, context kwargs, recorder factory, extra options). Extra
#: options go to the adapter's options; ``_ENV`` / ``_CLIENT_KEY`` below add an
#: environment or a caller-supplied client to a scenario.
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
    # --- review round 2 ---
    "comp-env-exported": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"headers": {"x-team": "t1"}},
    ),
    "resp-env-exported": (
        "r",
        _RM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _responses_body(strict=True, tool=False, reasoning=False)),
        {"headers": {"x-team": "t1"}},
    ),
    "comp-keyless": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None},
    ),
    "comp-keyless-env-exported": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None},
    ),
    "comp-keyless-header-Authorization": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None, "headers": {"Authorization": "Bearer gw-header-fake"}},
    ),
    "comp-keyless-header-authorization": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None, "headers": {"authorization": "Bearer gw-header-fake"}},
    ),
    "comp-keyless-header-cf-aig-authorization": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None, "headers": {"cf-aig-authorization": "Bearer gw-header-fake"}},
    ),
    "comp-keyless-hook-extra-headers": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None, "on_payload": lambda params, _model: _with_hook_authorization(params)},
    ),
    "comp-keyless-caller-client": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None},
    ),
    # --- review round 3: the hook's header in lower case ---
    "comp-keyless-hook-extra-headers-lowercase": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"api_key": None, "on_payload": lambda params, _model: _with_hook_authorization(params, "authorization")},
    ),
    "comp-keyless-header-authorization-hook-authorization": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {
            "api_key": None,
            "headers": {"authorization": "Bearer gw-header-fake"},
            "on_payload": lambda params, _model: _with_hook_authorization(params, "authorization"),
        },
    ),
    # --- narrow finish (after review round 3): the collapse is for the
    # keyless request on the adapter's own client only. With a key, or on a
    # client the caller hands in, a hook's lowercase ``authorization`` rides
    # beside the SDK's ``Authorization`` - two lines, as on 1.109.1.
    "comp-keyed-hook-extra-headers-lowercase": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {"on_payload": lambda params, _model: _with_hook_authorization(params, "authorization")},
    ),
    "comp-caller-client-hook-extra-headers-lowercase": (
        "c",
        _CM,
        {"tools": False, "history": False},
        lambda: _Recorder(200, _COMPLETIONS_TEXT),
        {
            "api_key": None,
            "on_payload": lambda params, _model: _with_hook_authorization(params, "authorization"),
        },
    ),
    # The Responses adapter settles a keyless call itself ("unused" beside an
    # auth header, else ``No API key``); these hold that it still sends what
    # 1.x sent - including the two lines a lowercase ``authorization`` gets
    # and the placeholder bearer beside ``cf-aig-authorization``, both older
    # than #278 (ADR-0265, "What this does NOT close").
    **{
        f"resp-keyless-header-{name}": (
            "r",
            _RM,
            {"tools": False, "history": False},
            lambda: _Recorder(200, _responses_body(strict=True, tool=False, reasoning=False)),
            {"api_key": None, "headers": {name: "Bearer gw-header-fake"}},
        )
        for name in ("Authorization", "authorization", "cf-aig-authorization")
    },
}

#: The environment a scenario runs in. Every other ``OPENAI_*`` variable is
#: unset while a scenario runs.
_EXPORTED = {
    "OPENAI_CUSTOM_HEADERS": "Authorization: Bearer sk-env-custom-fake\nX-Env-Custom: env-custom-fake",
    "OPENAI_ADMIN_KEY": "sk-env-admin-fake",
    "OPENAI_API_KEY": "sk-env-openai-fake",
    "OPENAI_ORG_ID": "org-env-fake",
    "OPENAI_PROJECT_ID": "proj-env-fake",
}
_ENV: dict[str, dict[str, str]] = {
    "comp-env-exported": _EXPORTED,
    "resp-env-exported": _EXPORTED,
    "comp-keyless-env-exported": _EXPORTED,
    "comp-keyless": {"OPENAI_API_KEY": "sk-env-openai-fake"},
}

#: Scenarios that hand the adapter a client of their own, built with this key.
_CLIENT_KEY: dict[str, str] = {
    "comp-keyless-caller-client": "sk-caller-client-fake",
    "comp-caller-client-hook-extra-headers-lowercase": "sk-caller-client-fake",
}


def _with_hook_authorization(params: dict[str, Any], name: str = "Authorization") -> dict[str, Any]:
    """A ``before_provider_payload`` hook that authenticates the request itself."""

    return {**params, "extra_headers": {name: "Bearer sk-hook-fake"}}


@contextlib.contextmanager
def _rig(recorder: _Recorder, env: dict[str, str]) -> Iterator[None]:
    """Every ``httpx.AsyncClient`` answers from ``recorder``; ``OPENAI_*`` is ``env``."""

    saved = {name: value for name, value in os.environ.items() if name.startswith("OPENAI_")}
    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(recorder.handler)
        real_init(self, *args, **kwargs)

    for name in saved:
        del os.environ[name]
    os.environ.update(env)
    httpx.AsyncClient.__init__ = _init  # type: ignore[method-assign]
    try:
        yield
    finally:
        httpx.AsyncClient.__init__ = real_init  # type: ignore[method-assign]
        for name in [name for name in os.environ if name.startswith("OPENAI_")]:
            del os.environ[name]
        os.environ.update(saved)


async def _run(name: str) -> dict[str, Any]:
    api, model, ctx_kw, make_recorder, extra = _SCENARIOS[name]
    recorder = make_recorder()
    extra = {"api_key": "sk-fake", **extra}
    with _rig(recorder, _ENV.get(name, {})):
        if name in _CLIENT_KEY:
            extra["client"] = openai.AsyncOpenAI(
                api_key=_CLIENT_KEY[name], base_url=model.base_url, max_retries=0
            )
        if api == "c":
            stream = completions_adapter.stream_openai_completions
            opts: Any = completions_adapter.OpenAICompletionsOptions(max_retries=0, **extra)
        else:
            stream = responses_adapter.stream_openai_responses
            opts = responses_adapter.OpenAIResponsesOptions(max_retries=0, **extra)
        events = [_summary(event) async for event in stream(model, _context(**ctx_kw), opts)]
        if "client" in extra:
            await extra["client"].close()
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
