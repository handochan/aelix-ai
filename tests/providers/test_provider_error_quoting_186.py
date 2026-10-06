"""#186 review round 2 — a MODEL request's error carries the other end's text too.

A proxy that refuses the CONNECT answers with a status line of its own, and
httpcore puts that reason phrase into ``httpx.ProxyError`` before any response
exists. Every adapter then handed the exception to
:func:`~aelix_ai.providers._error_hints.describe_provider_error`, which passed it
on whole. Measured on 157c7b73 with a real ``aelix -p`` through a local proxy
refusing with a 55,000-character reason that opens with ``ESC[2J``, an OSC 52
clipboard write and ``ESC[?1049h``: 55,053-55,075 bytes on stderr with all three
intact for openai-codex, anthropic, openrouter (openai-completions) and google.

These rows drive each adapter through a REAL local CONNECT refuser (an asyncio
server on port 0; httpx, httpcore and h11 parse its bytes as they would a
proxy's), so the exception chain is the one production builds. They assert on
the event a library caller receives - not on a screen - because ``-p`` stderr
and a library caller get no render-side sanitising at all.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from aelix_ai.oauth._helpers import SERVER_TEXT_MAX_CHARS, quote_server_text
from aelix_ai.providers._error_hints import describe_provider_error
from aelix_ai.streaming import AssistantErrorEvent, Context, Model, SimpleStreamOptions
from aelix_ai.utils.terminal_text import contains_steering_chars

#: What the proxy says. h11 admits ESC in a reason phrase (``[^\x00\s]``).
_STEER = b"\x1b[2J\x1b[H\x1b]52;c;cHduZWQ=\x07\x1b[?1049h"
_HOSTILE_REASON = b"Bad Gateway " + _STEER + b"R" * 4000

#: Two quotations (the SDK's message and the recovered cause) plus fixed text.
_LIMIT = 2 * (SERVER_TEXT_MAX_CHARS + 1) + 64


@asynccontextmanager
async def _connect_refuser(reason: bytes = _HOSTILE_REASON) -> AsyncIterator[str]:
    """A local proxy that refuses every CONNECT with ``502 <reason>``."""

    connects: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            connects.append(head.split(b"\r\n", 1)[0])
            writer.write(b"HTTP/1.1 502 " + reason + b"\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()
    assert connects and all(c.startswith(b"CONNECT ") for c in connects), connects


def _assert_contained(event: Any) -> str:
    assert isinstance(event, AssistantErrorEvent), type(event)
    message = event.error_message or ""
    assert event.error.error_message == message
    assert "502 Bad Gateway" in message, message[:200]
    assert not contains_steering_chars(message), f"steering survived: {message[:160]!r}"
    assert len(message) <= _LIMIT, f"unbounded: {len(message)} chars"
    return message


async def _events(stream: AsyncIterator[Any]) -> list[Any]:
    return [event async for event in stream]


def _fake_codex_token() -> str:
    def segment(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claim = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-186"}}
    return f"{segment({'alg': 'none'})}.{segment({'exp': 9999999999, **claim})}.sig"


# === the boundary itself =====================================================


def _proxy_error(text: str) -> httpx.ProxyError:
    import httpcore

    try:
        try:
            raise httpcore.ProxyError(text)
        except httpcore.ProxyError as inner:
            raise httpx.ProxyError(text) from inner
    except httpx.ProxyError as exc:
        return exc


def test_describe_provider_error_quotes_the_message_and_its_cause() -> None:
    import openai

    text = "502 " + _HOSTILE_REASON.decode("ascii")
    assert contains_steering_chars(text), "positive control: the reason steers"
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    try:
        try:
            raise _proxy_error(text)
        except httpx.ProxyError as inner:
            raise openai.APIConnectionError(request=request) from inner
    except openai.APIConnectionError as exc:
        described = describe_provider_error(exc)
    assert described.startswith("Connection error. — 502 Bad Gateway [2J")
    assert not contains_steering_chars(described)
    assert len(described) <= len("Connection error. — ") + SERVER_TEXT_MAX_CHARS + 1


def test_describe_provider_error_bounds_a_message_with_nothing_to_strip() -> None:
    """The bound holds without a single control character (verify V24's form:
    rewriting only text that steers)."""

    described = describe_provider_error(RuntimeError("502 Bad Gateway " + "R" * 55_000))
    assert len(described) == SERVER_TEXT_MAX_CHARS + 1
    assert described.endswith("…")


def test_describe_provider_error_keeps_the_tls_remedy_lines() -> None:
    """The remedy is aelix's own text: quoting the exception must not flatten it."""

    import ssl

    verify = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed (_ssl.c:1000)"
    )
    try:
        try:
            raise verify
        except ssl.SSLError as inner:
            raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as exc:
        described = describe_provider_error(exc)
    assert "\n\n" in described
    assert "export SSL_CERT_FILE=" in described


def test_describe_provider_error_falls_back_to_the_type_name() -> None:
    assert describe_provider_error(RuntimeError("\x1b\x07 \n")) == "RuntimeError"


# === every built-in adapter, through a real refusing proxy ===================


async def test_codex_model_request_through_a_refusing_proxy() -> None:
    from aelix_ai.providers.openai_codex_responses import (
        DEFAULT_CODEX_BASE_URL,
        OPENAI_CODEX_RESPONSES_API,
        stream_openai_codex_responses,
    )
    from aelix_ai.providers.openai_responses import OpenAIResponsesOptions

    model = Model(
        api=OPENAI_CODEX_RESPONSES_API,
        id="gpt-5.2",
        provider="openai-codex",
        base_url=DEFAULT_CODEX_BASE_URL,
    )
    async with _connect_refuser() as proxy, httpx.AsyncClient(proxy=proxy) as client:
        opts = OpenAIResponsesOptions(api_key=_fake_codex_token(), client=client)
        events = await _events(stream_openai_codex_responses(model, Context(), opts))
    message = _assert_contained(events[-1])
    assert message.startswith("502 Bad Gateway [2J")


async def test_anthropic_model_request_through_a_refusing_proxy() -> None:
    import anthropic
    from aelix_ai.providers.anthropic import ANTHROPIC_API, stream_anthropic

    model = Model(
        api=ANTHROPIC_API,
        id="claude-sonnet-4-5",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        max_tokens=1024,
    )
    async with _connect_refuser() as proxy, httpx.AsyncClient(proxy=proxy) as http:
        client = anthropic.AsyncAnthropic(api_key="fake-key-186", http_client=http, max_retries=0)
        opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
        events = await _events(stream_anthropic(model, Context(), opts))
    message = _assert_contained(events[-1])
    assert message.startswith("Connection error. — 502 Bad Gateway")


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
async def test_openai_model_request_through_a_refusing_proxy(api: str) -> None:
    import openai
    from aelix_ai.providers.openai_completions import stream_openai_completions
    from aelix_ai.providers.openai_responses import stream_openai_responses

    stream = stream_openai_completions if api == "openai-completions" else stream_openai_responses
    model = Model(
        api=api,
        id="openai/gpt-4o-mini",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        max_tokens=1024,
    )
    async with _connect_refuser() as proxy, httpx.AsyncClient(proxy=proxy) as http:
        client = openai.AsyncOpenAI(
            api_key="fake-key-186", base_url=model.base_url, http_client=http, max_retries=0
        )
        opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
        events = await _events(stream(model, Context(), opts))
    message = _assert_contained(events[-1])
    assert message.startswith("Connection error. — 502 Bad Gateway")


@pytest.mark.parametrize("module_name", ["google_generative_ai", "google_vertex"])
async def test_google_model_request_through_a_refusing_proxy(module_name: str) -> None:
    """The google SDK is not handed an httpx client here, so the row raises
    the ProxyError a real refusing proxy produced, from the SDK seam."""

    import importlib

    module = importlib.import_module(f"aelix_ai.providers.{module_name}")
    async with _connect_refuser() as proxy, httpx.AsyncClient(proxy=proxy) as http:
        with pytest.raises(httpx.ProxyError) as ei:
            await http.get("https://generativelanguage.googleapis.com/")
    error = ei.value
    assert contains_steering_chars(str(error)), "positive control: the real error steers"

    async def refuse(*_a: Any, **_k: Any) -> Any:
        raise error

    api = (
        module.GOOGLE_GENERATIVE_AI_API
        if module_name == "google_generative_ai"
        else module.GOOGLE_VERTEX_API
    )
    model = Model(api=api, id="gemini-2.5-flash", provider="google", base_url="")
    stream = (
        module.stream_google
        if module_name == "google_generative_ai"
        else module.stream_google_vertex
    )
    opts = SimpleStreamOptions(api_key="fake-key-186", client=object())
    with (
        patch.object(module, "open_generate_content_stream", side_effect=refuse),
        patch.dict(
            "os.environ", {"GOOGLE_CLOUD_PROJECT": "p-186", "GOOGLE_CLOUD_LOCATION": "us-central1"}
        ),
    ):
        events = await _events(stream(model, Context(), opts))
    _assert_contained(events[-1])


async def test_the_event_leaves_a_terminal_alone() -> None:
    """End to end on the bytes ``rich`` writes, as ``-p`` prints the error."""

    from aelix_ai.providers.openai_codex_responses import (
        DEFAULT_CODEX_BASE_URL,
        OPENAI_CODEX_RESPONSES_API,
        stream_openai_codex_responses,
    )
    from aelix_ai.providers.openai_responses import OpenAIResponsesOptions
    from rich.console import Console
    from rich.text import Text

    model = Model(
        api=OPENAI_CODEX_RESPONSES_API,
        id="gpt-5.2",
        provider="openai-codex",
        base_url=DEFAULT_CODEX_BASE_URL,
    )
    async with _connect_refuser() as proxy, httpx.AsyncClient(proxy=proxy) as client:
        opts = OpenAIResponsesOptions(api_key=_fake_codex_token(), client=client)
        events = await _events(stream_openai_codex_responses(model, Context(), opts))
    buf = io.StringIO()
    Console(file=buf, force_terminal=True, width=80).print(Text(events[-1].error_message or ""))
    out = buf.getvalue()
    for sequence in ("\x1b[2J", "\x1b]52", "\x1b[?1049h", "\x1b[H"):
        assert sequence.encode() in _STEER, f"positive control: {sequence!r}"
        assert sequence not in out, f"{sequence!r} reached the terminal"


# === the other server strings on the model path ==============================


async def test_openrouter_raw_metadata_is_quoted() -> None:
    """OpenRouter's ``error.metadata.raw`` is the upstream's own text, appended
    on a line of its own after the described error."""

    from aelix_ai.providers.openai_completions import stream_openai_completions

    raw = "upstream said " + _STEER.decode("ascii") + "R" * 4000

    class _OpenRouterError(Exception):
        error = {"message": "Provider returned error", "metadata": {"raw": raw}}

    async def create(**_kwargs: Any) -> Any:
        raise _OpenRouterError("400 Provider returned error")

    from types import SimpleNamespace

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )
    )

    model = Model(
        api="openai-completions",
        id="openai/gpt-4o-mini",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        max_tokens=1024,
    )
    opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
    events = await _events(stream_openai_completions(model, Context(), opts))
    message = events[-1].error_message or ""
    head, _, tail = message.partition("\n")
    assert head == "400 Provider returned error"
    assert contains_steering_chars(raw), "positive control: the raw text steers"
    assert tail == quote_server_text(raw), tail[:120]
    assert not contains_steering_chars(tail)


async def test_anthropic_auth_error_text_is_quoted() -> None:
    """A 401/403 leaves the adapter as ``_AuthError`` - the SDK's message, which
    carries the body a gateway or proxy chose."""

    import anthropic
    from aelix_ai.providers.anthropic import ANTHROPIC_API, _AuthError, stream_anthropic

    body = "\x1b[2J<html>" + "B" * 50_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=body, headers={"content-type": "text/html"})

    model = Model(
        api=ANTHROPIC_API,
        id="claude-sonnet-4-5",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        max_tokens=1024,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = anthropic.AsyncAnthropic(api_key="fake-key-186", http_client=http, max_retries=0)
        opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
        with pytest.raises(_AuthError) as ei:
            await _events(stream_anthropic(model, Context(), opts))
    message = str(ei.value)
    assert message.startswith("[2J<html>BBBB"), message[:40]
    assert not contains_steering_chars(message)
    assert len(message) <= SERVER_TEXT_MAX_CHARS + 1


# === review round 3: the cut must not decide what the error is ===============
#
# Codex round 3 (P2): the string this boundary returns is also what the
# overflow patterns and the harness's auto-retry regex read. Measured on
# cdeeb166 through the real OpenAI SDK and a MockTransport: a valid HTTP 400
# whose JSON carries a 592-character diagnostic and then
# ``"code": "context_length_exceeded"`` described in 513 characters without the
# code, ``is_context_overflow`` False and no compaction or re-run (aab1f210: 725
# characters, overflow, one compaction, re-run). A proxy's 502 page whose
# "502 Bad Gateway" sits at character 644 lost its retry the same way. The
# display text stays bounded; the classifiers read what they read before.

_OVERFLOW_BODY = {
    "error": {
        "message": "Request cannot be accommodated. " + "Detail. " * 70,
        "type": "invalid_request_error",
        "param": "messages",
        "code": "context_length_exceeded",
    }
}

_PROXY_PAGE = (
    "<html><head><style>"
    + "p{margin:0} " * 50
    + "</style></head><body><h1>502 Bad Gateway</h1></body></html>"
)


async def _completions_error(handler: Any) -> Any:
    """The assistant message the real OpenAI SDK + adapter build for ``handler``."""

    import openai
    from aelix_ai.providers.openai_completions import stream_openai_completions

    model = Model(
        api="openai-completions",
        id="local-model",
        provider="openai",
        base_url="https://local.invalid/v1",
        max_tokens=1024,
        context_window=4096,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = openai.AsyncOpenAI(
            api_key="fake-key-186", base_url=model.base_url, http_client=http, max_retries=0
        )
        opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
        events = await _events(stream_openai_completions(model, Context(), opts))
    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error_message is events[-1].error.error_message
    return events[-1].error


def _harness(*, retry: bool) -> Any:
    from types import SimpleNamespace

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.session import MemorySessionStorage, Session

    h = AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage())))
    h._state.model = SimpleNamespace(context_window=4096, provider="openai", id="local-model")  # type: ignore[assignment]
    h._state.auto_compaction_enabled = not retry
    h._state.auto_retry_enabled = retry
    return h


async def _prompt_through(h: Any, failure: Any) -> tuple[list[list[Any]], list[Any]]:
    """Drive ``prompt()`` with ``failure`` as the first turn's answer."""

    from aelix_ai.messages import AssistantMessage, TextContent

    runs: list[list[Any]] = []

    async def fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        runs.append(list(prompts))
        if len(runs) == 1:
            h._state.messages.extend(prompts)
            h._state.messages.append(failure)
        else:
            h._state.messages.append(
                AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
            )
        return list(h._state.messages)

    compactions: list[tuple[str, bool]] = []

    async def fake_compact(
        custom_instructions: Any = None,
        *,
        reason: str = "manual",
        will_retry: bool = False,
        _claim: object = None,
    ) -> Any:
        from types import SimpleNamespace

        compactions.append((reason, will_retry))
        return SimpleNamespace(summary="", first_kept_entry_id="", tokens_before=0)

    h._run = fake_run
    h.compact = fake_compact
    await h.prompt("a big request")
    return runs, compactions


async def test_an_overflow_code_past_the_cut_still_compacts_and_retries() -> None:
    from aelix_ai.utils.overflow import is_context_overflow

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=_OVERFLOW_BODY)

    message = await _completions_error(handler)
    display = message.error_message
    # The display text is what cdeeb166 showed: bounded, and the code cut off.
    assert display == quote_server_text(f"Error code: 400 - {_OVERFLOW_BODY}")
    assert len(display) == SERVER_TEXT_MAX_CHARS + 1
    assert "context_length_exceeded" not in display, "positive control: the cut drops the code"
    assert is_context_overflow(message, 4096)

    h = _harness(retry=False)
    runs, compactions = await _prompt_through(h, message)
    # (The success afterwards may also trip the threshold check; not this row's.)
    assert [c for c in compactions if c[0] == "overflow"] == [("overflow", True)]
    assert len(runs) == 2 and runs[1] == []


async def test_a_5xx_past_the_cut_is_still_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_agent_core.types import AutoRetryStartEvent

    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text=_PROXY_PAGE, headers={"content-type": "text/html"})

    message = await _completions_error(handler)
    assert _PROXY_PAGE.index("502") > SERVER_TEXT_MAX_CHARS, "positive control: past the cut"
    assert message.error_message == quote_server_text(_PROXY_PAGE)
    assert "502" not in message.error_message

    h = _harness(retry=True)
    starts: list[Any] = []
    h.subscribe(
        lambda event: starts.append(event) if isinstance(event, AutoRetryStartEvent) else None
    )
    runs, _ = await _prompt_through(h, message)
    assert len(runs) == 2 and runs[1] == []
    assert len(starts) == 1


def test_the_classifiers_read_what_they_read_before_not_the_display() -> None:
    """A line break becomes a space in the display (below), which would make
    "context\\nlength exceeded" match the overflow pattern ``context[_ ]length``
    that the raw text never matched: the class comes from the unquoted text."""

    from aelix_ai.messages import AssistantMessage
    from aelix_ai.utils.overflow import is_context_overflow

    described = describe_provider_error(RuntimeError("context\nlength exceeded"))
    assert described == "context length exceeded"
    message = AssistantMessage(stop_reason="error", error_message=described)
    assert not is_context_overflow(message, 4096)
    assert is_context_overflow(
        AssistantMessage(stop_reason="error", error_message="context length exceeded"), 4096
    ), "positive control: the display text alone would match"


def test_the_carried_text_is_the_description_built_before_quoting() -> None:
    """Base, recovered cause and TLS remedy, on the unquoted text - the string
    aab1f210's describe_provider_error returned."""

    import ssl

    from aelix_ai.utils.overflow import classifier_text_of

    verify = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed (_ssl.c:1000)"
    )
    try:
        try:
            raise verify
        except ssl.SSLError as inner:
            raise httpx.ConnectError("x" * 600 + " upstream connect error") from inner
    except httpx.ConnectError as exc:
        described = describe_provider_error(exc)
    carried = classifier_text_of(described)
    head, _, remedy = carried.partition("\n\n")
    assert head == "x" * 600 + f" upstream connect error — {verify}"
    assert remedy and described.endswith(remedy)
    assert "upstream connect error" not in described.partition("\n\n")[0]
    # Plain text where nothing was lost: no carrier at all.
    plain = describe_provider_error(RuntimeError("rate limit"))
    assert type(plain) is str and plain == "rate limit"


def test_the_carrier_never_reaches_json_or_a_copy_of_the_message() -> None:
    import copy
    import dataclasses

    from aelix_ai.messages import AssistantMessage
    from aelix_ai.utils.overflow import classifier_text_of

    described = describe_provider_error(RuntimeError("y" * 600 + " overloaded"))
    message = AssistantMessage(stop_reason="error", error_message=described)
    assert json.loads(json.dumps(dataclasses.asdict(message)))["error_message"] == described
    assert "overloaded" not in json.dumps(dataclasses.asdict(message))
    # The loop stamps a timestamp with dataclasses.replace; the carrier survives.
    stamped = dataclasses.replace(message, timestamp=1.0)
    assert classifier_text_of(stamped.error_message or "").endswith(" overloaded")
    assert classifier_text_of(copy.deepcopy(described)).endswith(" overloaded")


@pytest.mark.parametrize(
    ("pad", "overflow"),
    [
        # Past what the 512-character display keeps after the 35-character
        # prefix, inside what the old 500-character cut kept: classified.
        pytest.param(435, True, id="inside-the-old-cut"),
        # Past the old cut too: not classified before #186, and not now.
        pytest.param(470, False, id="past-the-old-cut"),
    ],
)
async def test_a_codex_code_classifies_as_it_did_before(pad: int, overflow: bool) -> None:
    """``_CodexHTTPError`` is bounded where it is raised; its message as it was
    before (body stripped, cut at 500) is what the classifiers read - the same
    answer as before #186 either way."""

    from aelix_ai.providers.openai_codex_responses import (
        DEFAULT_CODEX_BASE_URL,
        OPENAI_CODEX_RESPONSES_API,
        stream_openai_codex_responses,
    )
    from aelix_ai.providers.openai_responses import OpenAIResponsesOptions
    from aelix_ai.utils.overflow import is_context_overflow

    body = json.dumps({"error": {"message": "z" * pad, "code": "context_length_exceeded"}})
    at = body.index("context_length_exceeded")
    assert at > 512 - 35 - 23, "positive control: the display cannot hold the code"
    assert (at + 23 <= 500) is overflow, "positive control: the old cut"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=body, headers={"content-type": "application/json"})

    model = Model(
        api=OPENAI_CODEX_RESPONSES_API,
        id="gpt-5.2",
        provider="openai-codex",
        base_url=DEFAULT_CODEX_BASE_URL,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        opts = OpenAIResponsesOptions(api_key=_fake_codex_token(), client=client)
        events = await _events(stream_openai_codex_responses(model, Context(), opts))
    error = events[-1].error
    assert "context_length_exceeded" not in (error.error_message or ""), "positive control"
    assert is_context_overflow(error, 4096) is overflow


def test_a_codex_error_classifies_on_its_unquoted_text() -> None:
    """The quoted body holds more than the old cut did, and a line break in it
    reads as a space: "context\nlength exceeded" matches no overflow pattern,
    "context length exceeded" does. ``_CodexHTTPError`` carries its unquoted
    message, and ``describe_provider_error`` classifies on that - as before."""

    from aelix_ai.messages import AssistantMessage
    from aelix_ai.providers.openai_codex_responses import _CodexHTTPError
    from aelix_ai.utils.overflow import classifier_text_of, is_context_overflow

    error = _CodexHTTPError(400, "context\nlength exceeded")
    assert str(error) == "OpenAI Codex request failed (400): context length exceeded"
    described = describe_provider_error(error)
    assert described == str(error)
    assert (
        classifier_text_of(described)
        == "OpenAI Codex request failed (400): context\nlength exceeded"
    )
    assert not is_context_overflow(
        AssistantMessage(stop_reason="error", error_message=described), 4096
    )


@pytest.mark.parametrize("module_name", ["google_generative_ai", "google_vertex"])
async def test_google_classifies_on_its_own_message_as_before(module_name: str) -> None:
    """The two Google adapters used ``str(exc)`` before #186; they now display
    the described error but classify on that text, so a recovered cause does
    not change their retry decision."""

    import importlib

    from aelix_agent_core.harness.core import _RETRYABLE_ERROR_PATTERN
    from aelix_ai.utils.overflow import classifier_text_of

    module = importlib.import_module(f"aelix_ai.providers.{module_name}")
    try:
        try:
            raise OSError("Connection refused")
        except OSError as inner:
            raise RuntimeError("w" * 600 + " overloaded") from inner
    except RuntimeError as exc:
        error = exc

    async def refuse(*_a: Any, **_k: Any) -> Any:
        raise error

    api = (
        module.GOOGLE_GENERATIVE_AI_API
        if module_name == "google_generative_ai"
        else module.GOOGLE_VERTEX_API
    )
    model = Model(api=api, id="gemini-2.5-flash", provider="google", base_url="")
    stream = (
        module.stream_google
        if module_name == "google_generative_ai"
        else module.stream_google_vertex
    )
    opts = SimpleStreamOptions(api_key="fake-key-186", client=object())
    with (
        patch.object(module, "open_generate_content_stream", side_effect=refuse),
        patch.dict(
            "os.environ", {"GOOGLE_CLOUD_PROJECT": "p-186", "GOOGLE_CLOUD_LOCATION": "us-central1"}
        ),
    ):
        events = await _events(stream(model, Context(), opts))
    shown = events[-1].error_message or ""
    assert shown.endswith("— Connection refused"), shown[-60:]
    assert "overloaded" not in shown
    assert classifier_text_of(shown) == str(error)
    assert _RETRYABLE_ERROR_PATTERN.search(classifier_text_of(shown))


async def test_openrouter_raw_metadata_past_the_cut_still_classifies() -> None:
    from types import SimpleNamespace

    from aelix_ai.providers.openai_completions import stream_openai_completions
    from aelix_ai.utils.overflow import is_context_overflow

    raw = "v" * 600 + " maximum context length is 8192 tokens"

    class _OpenRouterError(Exception):
        error = {"message": "Provider returned error", "metadata": {"raw": raw}}

    async def create(**_kwargs: Any) -> Any:
        raise _OpenRouterError("400 Provider returned error")

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )
    )
    model = Model(
        api="openai-completions",
        id="openai/gpt-4o-mini",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        max_tokens=1024,
    )
    opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
    events = await _events(stream_openai_completions(model, Context(), opts))
    error = events[-1].error
    assert "maximum context length" not in (error.error_message or ""), "positive control"
    assert is_context_overflow(error, 8192)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("upstream failed\nretry later\r\nbye", "upstream failed retry later bye"),
        ("a b\x0bc\x85d", "a b c d"),
    ],
)
async def test_a_multi_line_model_error_keeps_its_word_boundaries(raw: str, shown: str) -> None:
    """Codex round 3 (P3): the line breaks were deleted and the words ran
    together ("upstream failedretry laterbye", measured on cdeeb166)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=raw, headers={"content-type": "text/plain"})

    message = await _completions_error(handler)
    assert message.error_message == shown


async def test_an_anthropic_auth_error_keeps_its_word_boundaries() -> None:
    """The 401/403 text the Anthropic adapter raises as ``_AuthError`` is a model
    request's error too: a line break reads as a space."""

    import anthropic
    from aelix_ai.providers.anthropic import ANTHROPIC_API, _AuthError, stream_anthropic

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, text="Denied by\ncorporate gateway", headers={"content-type": "text/html"}
        )

    model = Model(
        api=ANTHROPIC_API,
        id="claude-sonnet-4-5",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        max_tokens=1024,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = anthropic.AsyncAnthropic(api_key="fake-key-186", http_client=http, max_retries=0)
        opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
        with pytest.raises(_AuthError) as ei:
            await _events(stream_anthropic(model, Context(), opts))
    # No status in it: the SDK's message for a plain-text body is the body.
    assert str(ei.value) == "Denied by corporate gateway"


async def test_openrouter_raw_metadata_keeps_its_word_boundaries() -> None:
    from types import SimpleNamespace

    from aelix_ai.providers.openai_completions import stream_openai_completions

    class _OpenRouterError(Exception):
        error = {"message": "Provider returned error", "metadata": {"raw": "upstream\nsaid no"}}

    async def create(**_kwargs: Any) -> Any:
        raise _OpenRouterError("400 Provider returned error")

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )
    )
    model = Model(
        api="openai-completions",
        id="openai/gpt-4o-mini",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        max_tokens=1024,
    )
    opts = SimpleStreamOptions(api_key="fake-key-186", client=client)
    events = await _events(stream_openai_completions(model, Context(), opts))
    assert events[-1].error_message == "400 Provider returned error\nupstream said no"
