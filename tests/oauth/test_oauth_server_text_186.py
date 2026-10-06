"""#186 — what an OAuth server may put into an aelix error message.

Every string the OTHER end of an OAuth exchange chose — a non-2xx body, a 2xx
body that is not JSON, a JSON ``error``/``error_description``, the keys of a
token response, the HTTP reason phrase — used to be interpolated raw into a
``RuntimeError``. Measured on aab1f210 with a real ``aelix -p`` run against a
local token endpoint: the Codex refresh error reached stderr with ``ESC[2J``,
an OSC 52 clipboard write and ``ESC[?1049h`` intact, and through
``rich.Console(force_terminal=True)`` onto an 80x24 emulator the prior
transcript line did not survive (0 non-blank rows).

The rule these tests pin, per site: the message keeps the status and the
opening of the server's text, carries no steering character, and quotes at
most ``SERVER_TEXT_MAX_CHARS`` of it. Each "is it gone?" assertion has a
positive control on the INPUT, and the end-to-end case asserts on the byte
stream ``rich`` writes, not on ``Text.plain`` (which only proves the
sanitiser ran).

The transport is ``httpx.MockTransport`` — real ``httpx.Response`` objects, so
``reason_phrase`` goes through httpx's own ASCII decode, which keeps ESC.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from aelix_ai.oauth import _high_level, anthropic, github_copilot, openai_codex
from aelix_ai.oauth._helpers import (
    SERVER_TEXT_MAX_CHARS,
    describe_token_response_keys,
    quote_server_text,
    quoting_transport_errors,
)
from aelix_ai.oauth.auth_storage import OAuthRefreshError
from aelix_ai.oauth.types import OAuthCredentials
from aelix_ai.utils.terminal_text import contains_steering_chars

_REAL_ASYNC_CLIENT = httpx.AsyncClient

#: The families that did the damage, at the front where a ``[:N]`` cut keeps them.
_STEER = "\x1b[2J\x1b[H\x1b]52;c;cHduZWQ=\x07\x1b[?1049h\x9b2J trusted=‮eslaf‬"

#: The #184 Unicorn page at its measured SCALE: 164 lines, ~55 KB, one ~45 KB line.
_UNICORN = (
    _STEER
    + "\n<!DOCTYPE html>\n<title>Unicorn! &middot; GitHub</title>\n"
    + "".join(f"<p>line {i}</p>\n" for i in range(160))
    + "data:image/png;base64,"
    + "A" * 45_736
    + "\n"
)

#: h11 admits ESC in a reason phrase (``[^\x00\s]``) and httpx decodes it as ASCII.
_HOSTILE_REASON = b"Bad Gateway \x1b[2J\x1b]52;c;cHduZWQ=\x07\x1b[?1049h" + b"R" * 4000

#: Generous for the fixed prefix each site adds around one quotation.
_PREFIX_ALLOWANCE = 300


def _response(
    status: int,
    text: str = "",
    *,
    reason: bytes | None = None,
    content_type: str = "text/html",
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        extensions = {"reason_phrase": reason} if reason is not None else {}
        return httpx.Response(
            status,
            content=text.encode("utf-8"),
            headers={"content-type": content_type},
            extensions=extensions,
        )

    return handler


def _serve(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    """Route every ``httpx.AsyncClient`` the OAuth modules build to ``handler``."""

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return patch.object(httpx, "AsyncClient", side_effect=factory)


def _assert_contained(message: str, *, quoted: int = 1) -> None:
    """No steering character, and at most ``quoted`` bounded quotations."""

    assert contains_steering_chars(_UNICORN), "positive control: the payload steers"
    assert not contains_steering_chars(message), f"a steering character survived: {message[:160]!r}"
    limit = quoted * (SERVER_TEXT_MAX_CHARS + 1) + _PREFIX_ALLOWANCE
    assert len(message) <= limit, f"unbounded: {len(message)} chars > {limit}"


async def _raised(coro: Any) -> str:
    with pytest.raises(RuntimeError) as ei:
        await coro
    return str(ei.value)


# === the helper ==============================================================


def test_quote_server_text_strips_then_bounds() -> None:
    quoted = quote_server_text(_UNICORN)
    assert not contains_steering_chars(quoted)
    assert len(quoted) == SERVER_TEXT_MAX_CHARS + 1, len(quoted)  # + the marker
    assert quoted.endswith("…")
    # Strip THEN cut: the budget is spent on visible text, so the page's opening
    # survives instead of being crowded out by invisible bytes.
    assert "<title>Unicorn! &middot; GitHub</title>" in quoted


def test_quote_server_text_empty_when_nothing_visible() -> None:
    assert quote_server_text("\x1b\x07\n \t") == ""


#: A whole one-line OAuth JSON error: what the budget exists to keep.
_DIAGNOSTIC = '{"error":"invalid_grant","error_description":"fake authorization code expired"}'


@pytest.mark.parametrize(
    "padding",
    [
        pytest.param(" " * SERVER_TEXT_MAX_CHARS, id="spaces"),
        pytest.param("\n\t " * SERVER_TEXT_MAX_CHARS, id="whitespace-lines"),
        # Deleted bytes in front of the budget: a quote that cut (or pre-cut,
        # to save work on a huge body) before deleting would keep nothing.
        pytest.param("\x1b" * (2 * SERVER_TEXT_MAX_CHARS), id="esc-x1024"),
        pytest.param("\x1b \x07 " * SERVER_TEXT_MAX_CHARS, id="esc-and-spaces"),
    ],
)
def test_quote_server_text_spends_the_budget_on_visible_text(padding: str) -> None:
    """Delete, trim, THEN cut (review, #186): leading invisible text must not
    eat the budget and leave only the marker."""

    assert len(padding) >= SERVER_TEXT_MAX_CHARS, "positive control: padding alone fills the budget"
    assert quote_server_text(padding + _DIAGNOSTIC) == _DIAGNOSTIC
    assert quote_server_text(padding + _DIAGNOSTIC + padding) == _DIAGNOSTIC


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("codex-exchange", "OpenAI Codex token exchange failed (400): "),
        ("codex-refresh", "OpenAI Codex token refresh failed (400): "),
        ("anthropic-refresh", "status=400; "),
        ("copilot-start", "400 Bad Request from "),
    ],
)
async def test_a_padded_json_error_keeps_its_diagnostic_at_every_site(
    site: str, expected: str
) -> None:
    calls = {
        "codex-exchange": lambda: openai_codex._exchange_authorization_code("c", "v"),
        "codex-refresh": lambda: openai_codex.refresh_openai_codex_token("r"),
        "anthropic-refresh": lambda: anthropic.refresh_anthropic_token("r"),
        "copilot-start": lambda: github_copilot._start_device_flow("github.com"),
    }
    body = "\x1b" * (2 * SERVER_TEXT_MAX_CHARS) + " " * SERVER_TEXT_MAX_CHARS + _DIAGNOSTIC
    with _serve(_response(400, body, reason=b"Bad Request", content_type="application/json")):
        message = await _raised(calls[site]())
    assert expected in message
    assert "fake authorization code expired" in message, message
    assert not contains_steering_chars(message)


def test_describe_token_response_keys_never_quotes_a_value() -> None:
    secret = "fake-access-SECRET-186"
    described = describe_token_response_keys({"access_token": secret, "junk\x1b[2J": "x" * 50_000})
    assert secret not in described
    assert "access_token" in described
    assert not contains_steering_chars(described)
    assert len(described) <= SERVER_TEXT_MAX_CHARS + 32
    assert "list" in describe_token_response_keys([1, 2])


# === OpenAI Codex ============================================================


@pytest.mark.parametrize("op", ["exchange", "refresh"])
async def test_codex_non_2xx_body_is_quoted(op: str) -> None:
    call = (
        (lambda: openai_codex._exchange_authorization_code("code", "verifier"))
        if op == "exchange"
        else (lambda: openai_codex.refresh_openai_codex_token("fake-refresh"))
    )
    with _serve(_response(502, _UNICORN)):
        message = await _raised(call())
    assert message.startswith(f"OpenAI Codex token {op} failed (502): ")
    assert "<title>Unicorn! &middot; GitHub</title>" in message
    _assert_contained(message)


def _codex_call(op: str) -> Any:
    if op == "exchange":
        return openai_codex._exchange_authorization_code("code", "verifier")
    return openai_codex.refresh_openai_codex_token("fake-refresh")


@pytest.mark.parametrize("op", ["exchange", "refresh"])
async def test_codex_empty_body_falls_back_to_a_quoted_reason_phrase(op: str) -> None:
    with _serve(_response(502, "", reason=_HOSTILE_REASON)):
        message = await _raised(_codex_call(op))
    assert message.startswith(f"OpenAI Codex token {op} failed (502): Bad Gateway")
    _assert_contained(message)


@pytest.mark.parametrize("op", ["exchange", "refresh"])
async def test_codex_body_of_only_controls_falls_back_to_the_reason_phrase(op: str) -> None:
    # Only invisible bytes: an escape SEQUENCE would leave its inert literal.
    with _serve(_response(503, "\x1b\x07\n\t \x9b\u202e", reason=b"Service Unavailable")):
        message = await _raised(_codex_call(op))
    assert message == f"OpenAI Codex token {op} failed (503): Service Unavailable"


@pytest.mark.parametrize("op", ["exchange", "refresh"])
async def test_codex_missing_fields_names_keys_not_tokens(op: str) -> None:
    secret = "fake-access-SECRET-186"
    body = json.dumps({"access_token": secret, "junk": "\x1b[2J" + "J" * 50_000})
    call = (
        (lambda: openai_codex._exchange_authorization_code("code", "verifier"))
        if op == "exchange"
        else (lambda: openai_codex.refresh_openai_codex_token("fake-refresh"))
    )
    with _serve(_response(200, body, content_type="application/json")):
        message = await _raised(call())
    assert secret not in message, "a live token reached the error message"
    assert message == (
        f"OpenAI Codex token {op} response missing fields: received keys: access_token, junk"
    )


# === Anthropic ===============================================================


async def test_anthropic_post_json_body_is_quoted() -> None:
    url = "https://example.invalid/v1/oauth/token"
    with _serve(_response(502, _UNICORN)):
        message = await _raised(anthropic._post_json(url, {"x": 1}))
    assert message.startswith(f"HTTP request failed. status=502; url={url}; body=")
    assert "<title>Unicorn! &middot; GitHub</title>" in message
    _assert_contained(message)


@pytest.mark.parametrize("status", [502, 200])
@pytest.mark.parametrize("op", ["exchange", "refresh"])
async def test_anthropic_token_paths_quote_the_body(op: str, status: int) -> None:
    """502 goes through ``_post_json``; a 200 that is not JSON (a captive
    portal, a proxy login page) through the ``invalid JSON`` branch."""

    call = (
        (lambda: anthropic._exchange_authorization_code("c", "s", "v", "http://localhost/cb"))
        if op == "exchange"
        else (lambda: anthropic.refresh_anthropic_token("fake-refresh"))
    )
    with _serve(_response(status, _UNICORN)):
        message = await _raised(call())
    if status == 200:
        assert "returned invalid JSON" in message
    else:
        assert "status=502" in message
    _assert_contained(message)


# === GitHub Copilot ==========================================================


async def test_copilot_reason_phrase_is_quoted_with_an_empty_body() -> None:
    with _serve(_response(502, "", reason=_HOSTILE_REASON)):
        message = await _raised(github_copilot.refresh_github_copilot_token("fake-gh"))
    assert message.startswith("502 Bad Gateway")
    assert "server said:" not in message  # nothing visible was said
    _assert_contained(message)


async def test_copilot_reason_of_only_controls_falls_back_to_the_status_table() -> None:
    with _serve(_response(502, "", reason=b"\x1b\x07\x1b")):
        message = await _raised(github_copilot.refresh_github_copilot_token("fake-gh"))
    assert message.startswith("502 Bad Gateway from https://api.github.com/")


async def test_copilot_reason_phrase_and_body_are_both_quoted() -> None:
    with _serve(_response(502, _UNICORN, reason=_HOSTILE_REASON)):
        message = await _raised(github_copilot._start_device_flow("github.com"))
    assert "; server said: [2J" in message
    assert "<title>Unicorn! &middot; GitHub</title>" in message
    _assert_contained(message, quoted=2)


async def test_copilot_device_flow_json_error_is_quoted() -> None:
    """A device-flow protocol error arrives as HTTP 200 + JSON, so the
    status-path quoting in ``_http_error`` never sees it."""

    body = json.dumps(
        {"error": "access_denied\x1b[2J", "error_description": _UNICORN},
    )
    with _serve(_response(200, body, content_type="application/json")):
        message = await _raised(
            github_copilot._poll_for_github_access_token("github.com", "dc", 1, 30)
        )
    assert message.startswith("Device flow failed: access_denied[2J: ")
    _assert_contained(message, quoted=2)


# === the refresh choke point, for every provider =============================


class _ExtensionProvider:
    """An extension-registered OAuth provider whose refresh quotes its server raw."""

    id = "ext-186"
    name = "Extension 186"

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        raise RuntimeError(f"upstream said (502): {_UNICORN}")

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access


async def test_refresh_choke_point_neuters_any_provider_and_keeps_the_status() -> None:
    expired = OAuthCredentials(refresh="r", access="a", expires=int(time.time() * 1000) - 1)
    with patch.object(_high_level, "get_oauth_provider", return_value=_ExtensionProvider()):
        message = await _raised(
            _high_level.get_oauth_api_key_from_credentials("ext-186", {"ext-186": expired})
        )
    assert message.startswith("Failed to refresh OAuth token for ext-186: upstream said (502):")
    assert not contains_steering_chars(message)
    assert len(message) <= 300 + 64


async def test_refresh_choke_point_keeps_word_boundaries() -> None:
    """SPACE, not delete, at the choke point: its input is a whole message."""

    class _MultiLine(_ExtensionProvider):
        async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
            raise RuntimeError("token endpoint said no\nsign in again")

    expired = OAuthCredentials(refresh="r", access="a", expires=int(time.time() * 1000) - 1)
    with patch.object(_high_level, "get_oauth_provider", return_value=_MultiLine()):
        message = await _raised(
            _high_level.get_oauth_api_key_from_credentials("ext-186", {"ext-186": expired})
        )
    assert message.endswith("token endpoint said no sign in again")


async def test_codex_refresh_error_on_the_request_path_leaves_the_screen_alone() -> None:
    """End to end, on the bytes ``rich`` writes: the turn error a user sees
    when the Codex refresh meets a hostile 502."""

    from rich.console import Console
    from rich.text import Text

    expired = OAuthCredentials(refresh="r", access="a", expires=int(time.time() * 1000) - 1)
    with (
        _serve(_response(502, _UNICORN, reason=_HOSTILE_REASON)),
        pytest.raises(RuntimeError) as ei,
    ):
        await _high_level.get_oauth_api_key_from_credentials(
            "openai-codex", {"openai-codex": expired}
        )
    shown = str(OAuthRefreshError("openai-codex", ei.value))

    buf = io.StringIO()
    Console(file=buf, force_terminal=True, width=80, color_system="truecolor").print(
        Text(f"✖ {shown}", style="bold red")
    )
    out = buf.getvalue()
    for sequence in ("\x1b[2J", "\x1b]52", "\x1b[?1049h", "\x1b[H", "\x9b", "‮"):
        assert sequence in _STEER, f"positive control: {sequence!r} is in the payload"
        assert sequence not in out, f"{sequence!r} reached the terminal"
    assert "OpenAI Codex token refresh failed (502)" in shown


# === the login flow's custom-provider model fetch ============================


async def test_custom_provider_model_fetch_failure_is_neutered() -> None:
    from aelix_coding_agent.tui import login_wizard

    async def boom(*_a: Any, **_k: Any) -> list[str]:
        request = httpx.Request("GET", "https://gw.example.invalid/v1/models")
        response = httpx.Response(
            502, request=request, extensions={"reason_phrase": _HOSTILE_REASON}
        )
        response.raise_for_status()
        return []

    committed: list[Any] = []

    class _Text:
        def __init__(self, text: str, style: str = "") -> None:
            self.plain = text

    with patch.object(login_wizard, "_fetch_openai_model_ids", side_effect=boom):
        ok = await login_wizard._register_custom_models(
            provider_id="gw",
            base_url="https://gw.example.invalid/v1",
            api="openai-completions",
            api_key="fake-key",
            model_registry=object(),
            multiselect=None,
            commit=committed.append,
            Text=_Text,
            settings_manager=None,
        )
    assert ok is False
    (line,) = [c.plain for c in committed]
    assert line.startswith("Could not fetch models from https://gw.example.invalid/v1/models (")
    assert not contains_steering_chars(line, keep_newline=True)
    assert len(line) <= 8 * 201 + 200


# === a transport error: the proxy's CONNECT refusal ==========================
#
# A proxy that refuses the CONNECT answers with a status line of its own, and
# httpcore puts that reason phrase into ``ProxyError`` before any response
# exists to quote. Measured in review with httpcore's real proxy code reading a
# recorder's bytes: 55,044 characters with ESC[2J, OSC 52 and ?1049h intact,
# from every OAuth entry point, and twice that from the Codex and Anthropic
# login wrappers, which interpolate the error and its cause. The handler raises
# the same chain httpx builds (``httpx.ProxyError`` from ``httpcore.ProxyError``).

_PROXY_REFUSAL = "502 " + _HOSTILE_REASON.decode("ascii")


def _refuse_connect(request: httpx.Request) -> httpx.Response:
    import httpcore

    raise httpx.ProxyError(_PROXY_REFUSAL, request=request) from httpcore.ProxyError(_PROXY_REFUSAL)


def _chain(exc: BaseException) -> list[BaseException]:
    links: list[BaseException] = []
    link: BaseException | None = exc
    while link is not None and link not in links:
        links.append(link)
        link = link.__cause__ or link.__context__
    return links


async def _fake_callback_server(expected_state: str, **_kwargs: Any) -> Any:
    import asyncio

    from aelix_ai.oauth._callback_server import CallbackServerInfo

    future = asyncio.get_running_loop().create_future()
    future.set_result(("fake-code", expected_state))
    return CallbackServerInfo(
        "http://localhost/fake-cb", lambda: future, lambda: None, lambda: None
    )


def _login(module: Any) -> Any:
    from aelix_ai.oauth.types import OAuthLoginCallbacks

    callbacks = OAuthLoginCallbacks(on_auth=lambda info: None, on_prompt=lambda prompt: "")
    login = {
        openai_codex: lambda: openai_codex.login_openai_codex(callbacks),
        anthropic: lambda: anthropic.login_anthropic(callbacks),
        github_copilot: lambda: github_copilot.login_github_copilot(callbacks),
    }[module]
    return login()


_CONNECT_SITES: dict[str, tuple[Callable[[], Any], type[BaseException], int]] = {
    # name: (call, the type the caller sees, how many quotations the message holds)
    "codex-exchange": (
        lambda: openai_codex._exchange_authorization_code("c", "v"),
        httpx.ProxyError,
        1,
    ),
    "codex-refresh": (lambda: openai_codex.refresh_openai_codex_token("r"), httpx.ProxyError, 1),
    "codex-login": (lambda: _login(openai_codex), RuntimeError, 2),
    "anthropic-post_json": (
        lambda: anthropic._post_json("https://example.invalid/token", {}),
        httpx.ProxyError,
        1,
    ),
    "anthropic-exchange": (
        lambda: anthropic._exchange_authorization_code("c", "s", "v", "http://localhost/cb"),
        RuntimeError,
        2,
    ),
    "anthropic-refresh": (lambda: anthropic.refresh_anthropic_token("r"), RuntimeError, 2),
    "anthropic-login": (lambda: _login(anthropic), RuntimeError, 2),
    "copilot-start": (lambda: github_copilot._start_device_flow("github.com"), httpx.ProxyError, 1),
    "copilot-poll": (
        lambda: github_copilot._poll_for_github_access_token("github.com", "dc", 1, 30),
        httpx.ProxyError,
        1,
    ),
    "copilot-refresh": (
        lambda: github_copilot.refresh_github_copilot_token("fake-gh"),
        httpx.ProxyError,
        1,
    ),
    "copilot-enable-all": (
        lambda: github_copilot.enable_all_github_copilot_models("fake-token"),
        httpx.ProxyError,
        1,
    ),
    "copilot-login": (lambda: _login(github_copilot), httpx.ProxyError, 1),
}


@pytest.mark.parametrize("site", sorted(_CONNECT_SITES))
async def test_a_proxy_connect_refusal_is_quoted_at_every_oauth_entry_point(site: str) -> None:
    call, expected_type, quotations = _CONNECT_SITES[site]
    with (
        _serve(_refuse_connect),
        patch.object(openai_codex, "start_callback_server", side_effect=_fake_callback_server),
        patch.object(anthropic, "start_callback_server", side_effect=_fake_callback_server),
        pytest.raises(Exception) as ei,
    ):
        await call()
    exc = ei.value
    # The type survives: callers tell a transport failure from an answer by it
    # (Copilot's reachability check after /login, #99).
    assert type(exc) is expected_type, type(exc)
    message = str(exc)
    assert "502 Bad Gateway" in message
    _assert_contained(message, quoted=quotations)
    for link in _chain(exc):
        assert not contains_steering_chars(str(link)), f"{type(link).__name__} kept a steering char"


async def test_quoting_leaves_an_ordinary_transport_error_alone() -> None:
    """A TLS failure is rewritten nowhere: the hint reads its chain, and the
    OpenSSL error at the bottom is what names the reason and the host."""

    import ssl

    from aelix_ai.oauth._helpers import quoting_transport_errors
    from aelix_ai.providers._error_hints import describe_provider_error

    verify = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed (_ssl.c:1000)"
    )
    verify.verify_code = 19
    try:
        try:
            raise verify
        except ssl.SSLError as inner:
            raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as exc:
        error = exc
    before = [link.args for link in _chain(error)]
    described = describe_provider_error(error)

    with pytest.raises(httpx.ConnectError) as ei, quoting_transport_errors():
        raise error
    assert ei.value is error
    assert [link.args for link in _chain(error)] == before
    assert describe_provider_error(error) == described


# === the Codex MODEL request: the same body, after sign-in ===================


async def test_codex_model_request_error_body_is_quoted() -> None:
    """Measured with the TUI's render sanitising (#177) in place: the TUI was
    contained, ``-p`` stderr carried ESC[2J and OSC 52 and ``--mode rpc``
    stdout carried C1 CSI and BiDi. So the quotation is at the raise site."""

    import base64

    from aelix_ai.models import Model
    from aelix_ai.providers.openai_codex_responses import (
        DEFAULT_CODEX_BASE_URL,
        OPENAI_CODEX_RESPONSES_API,
        stream_openai_codex_responses,
    )
    from aelix_ai.providers.openai_responses import OpenAIResponsesOptions
    from aelix_ai.streaming import AssistantErrorEvent, Context

    def segment(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claim = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-186"}}
    token = f"{segment({'alg': 'none'})}.{segment({'exp': 9999999999, **claim})}.sig"
    model = Model(
        api=OPENAI_CODEX_RESPONSES_API,
        id="gpt-5.2",
        provider="openai-codex",
        base_url=DEFAULT_CODEX_BASE_URL,
    )
    client = _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_response(400, _UNICORN)))
    try:
        opts = OpenAIResponsesOptions(api_key=token, client=client)
        events = [event async for event in stream_openai_codex_responses(model, Context(), opts)]
    finally:
        await client.aclose()
    error = events[-1]
    assert isinstance(error, AssistantErrorEvent)
    message = error.error_message or ""
    assert message.startswith("OpenAI Codex request failed (400): ")
    assert "<title>Unicorn! &middot; GitHub</title>" in message
    _assert_contained(message)


# === review round 2 ==========================================================
#
# Measured on 157c7b73 (round 1's commit) through a REAL local proxy: the chain
# walk compared the quote with ``text.strip()``, so a reason phrase padded with
# 2,048 tabs or spaces counted as unchanged and went out unbounded (2,063
# characters from the Codex token calls, 4,254 from the Anthropic wrappers);
# ``httpx.RemoteProtocolError`` (h11 quoting a garbled status line) was covered
# only by accident of the catch; and the walk followed ``__context__`` into an
# exception the caller was handling and rewrote it. The proxy below is an
# asyncio server on port 0 - httpx, httpcore and h11 parse its bytes exactly
# as they would a real proxy's, so the chains are the ones production builds.


@asynccontextmanager
async def _proxy(answer: bytes) -> AsyncIterator[str]:
    """A local proxy that answers every CONNECT with ``answer`` and hangs up."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(answer)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()


def _through(proxy: str) -> Any:
    """Route every ``httpx.AsyncClient`` the OAuth modules build through ``proxy``."""

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["proxy"] = proxy
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return patch.object(httpx, "AsyncClient", side_effect=factory)


def _raw_links(exc: BaseException) -> list[BaseException]:
    """Every linked exception, a suppressed context included (what a tool that
    walks the chain can print)."""

    links: list[BaseException] = []
    link: BaseException | None = exc
    while link is not None and all(link is not seen for seen in links):
        links.append(link)
        link = link.__cause__ if link.__cause__ is not None else link.__context__
    return links


_DIAG = "fix3-diag"

#: The four OAuth calls whose own error is the transport error (one quotation),
#: plus the Anthropic wrapper, which quotes it and its cause (two).
_ROUND2_SITES: dict[str, tuple[Callable[[], Any], int]] = {
    "codex-exchange": (lambda: openai_codex._exchange_authorization_code("c", "v"), 1),
    "codex-refresh": (lambda: openai_codex.refresh_openai_codex_token("r"), 1),
    "anthropic-refresh": (lambda: anthropic.refresh_anthropic_token("r"), 2),
    "copilot-refresh": (lambda: github_copilot.refresh_github_copilot_token("fake-gh"), 1),
    "copilot-start": (lambda: github_copilot._start_device_flow("github.com"), 1),
}


@pytest.mark.parametrize("site", sorted(_ROUND2_SITES))
@pytest.mark.parametrize(
    "reason",
    [
        pytest.param(b"Bad Gateway " + _DIAG.encode() + b" " * 2000, id="2000-trailing-spaces"),
        pytest.param(b"Bad Gateway " + _DIAG.encode() + b"\t" * 2048, id="2048-trailing-tabs"),
        pytest.param(b"Bad Gateway" + b" " * 2000 + _DIAG.encode(), id="2000-inner-spaces"),
        pytest.param(b"Bad Gateway " + _DIAG.encode() + b" " + b"R" * 4000, id="4000-plain"),
    ],
)
async def test_a_padded_or_long_connect_refusal_is_bounded(site: str, reason: bytes) -> None:
    """Padding-only differences are a change (Codex round 2, P2), and so is a
    long reason with nothing to strip (verify's V24: rewrite only what steers)."""

    call, quotations = _ROUND2_SITES[site]
    assert len(reason) > SERVER_TEXT_MAX_CHARS, "positive control: the reason alone overflows"
    async with _proxy(b"HTTP/1.1 502 " + reason + b"\r\nContent-Length: 0\r\n\r\n") as proxy:
        with _through(proxy), pytest.raises(Exception) as ei:
            await call()
    message = str(ei.value)
    assert f"502 Bad Gateway {_DIAG}" in message, message[:200]
    _assert_contained(message, quoted=quotations)
    for link in _raw_links(ei.value):
        if isinstance(link, httpx.HTTPError) or type(link).__module__.startswith(
            ("httpcore", "h11")
        ):
            assert len(str(link)) <= SERVER_TEXT_MAX_CHARS + 1, (type(link), len(str(link)))


@pytest.mark.parametrize("site", sorted(_ROUND2_SITES))
async def test_a_garbled_status_line_is_quoted_down_to_h11(site: str) -> None:
    """``httpx.RemoteProtocolError``, not ``ProxyError`` (Codex mutant A: catch
    only ProxyError). Its h11 cause sits behind httpcore's ``raise exc from
    None`` - a suppressed ``__context__`` - so a walk that followed
    ``__cause__`` alone, or stopped at the suppression, left the status line
    raw there (verify's V26)."""

    call, quotations = _ROUND2_SITES[site]
    # The Anthropic wrapper's pi-shaped ``details=`` repeats every link of the
    # chain, and this one has three (httpx, httpcore, h11): three quotations.
    quotations = 3 if quotations == 2 else quotations
    garbage = b"NOT_HTTP " + _HOSTILE_REASON
    async with _proxy(garbage + b"\r\n\r\n") as proxy:
        with _through(proxy), pytest.raises(Exception) as ei:
            await call()
    links = _raw_links(ei.value)
    names = [f"{type(link).__module__}.{type(link).__name__}" for link in links]
    assert any(name.startswith("h11.") for name in names), names
    assert any(isinstance(link, httpx.RemoteProtocolError) for link in links), names
    _assert_contained(str(ei.value), quoted=quotations)
    for link in links:
        assert not contains_steering_chars(str(link)), f"{type(link).__name__} kept a steering char"
        if not isinstance(link, RuntimeError) or isinstance(link, httpx.HTTPError):
            assert len(str(link)) <= SERVER_TEXT_MAX_CHARS + 1, (type(link), len(str(link)))


async def test_another_transport_error_is_quoted_too() -> None:
    """A second non-proxy ``httpx.HTTPError`` (Codex mutant A)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(_PROXY_REFUSAL, request=request)

    with _serve(handler), pytest.raises(httpx.ReadError) as ei:
        await openai_codex.refresh_openai_codex_token("r")
    assert str(ei.value).startswith("502 Bad Gateway [2J")
    _assert_contained(str(ei.value))


@pytest.mark.parametrize("count", [4096, 60_000])
def test_deleted_bytes_in_front_never_cost_the_diagnostic(count: int) -> None:
    """Codex mutant B: ``text[:4096]`` before the helper keeps nothing of a
    diagnostic behind 4,096 ESC bytes. 60,000 is past the measured 55 KB page."""

    assert quote_server_text("\x1b" * count + _DIAGNOSTIC) == _DIAGNOSTIC


async def test_deleted_bytes_in_front_keep_the_diagnostic_at_a_site() -> None:
    body = "\x1b" * 4096 + _DIAGNOSTIC
    with _serve(_response(400, body, reason=b"Bad Request", content_type="application/json")):
        message = await _raised(openai_codex.refresh_openai_codex_token("r"))
    assert message == f"OpenAI Codex token refresh failed (400): {_DIAGNOSTIC}"


@pytest.mark.parametrize(
    "padding",
    [
        pytest.param(" " * 2000, id="2000-spaces"),
        pytest.param("  　" * 700, id="unicode-blank"),
        pytest.param("\x1b \t" * 1500, id="esc-space-tab"),
    ],
)
def test_blank_space_inside_the_text_does_not_spend_the_budget(padding: str) -> None:
    quoted = quote_server_text("Bad Gateway" + padding + _DIAGNOSTIC)
    assert quoted == f"Bad Gateway {_DIAGNOSTIC}"


# --- the chain walk stays inside the request's own chain (verify NB) ---------

_REMEDY = "TLS verification failed for auth.openai.com.\n  1. export SSL_CERT_FILE=/path/ca.pem\n  2. retry"


async def test_an_exception_the_caller_is_handling_is_left_alone() -> None:
    """The OAuth call is made inside the caller's ``except`` block, so the
    caller's exception is the ``__context__`` at the bottom of the request's
    chain. Measured on 157c7b73: rewritten in place, newlines deleted."""

    outer = RuntimeError(_REMEDY)
    async with _proxy(b"HTTP/1.1 502 " + _HOSTILE_REASON + b"\r\n\r\n") as proxy:
        with _through(proxy):
            try:
                raise outer
            except RuntimeError:
                with pytest.raises(httpx.ProxyError) as ei:
                    await openai_codex.refresh_openai_codex_token("r")
    assert any(link is outer for link in _raw_links(ei.value)), (
        "positive control: it is in the chain"
    )
    assert outer.args == (_REMEDY,)
    _assert_contained(str(ei.value))


async def test_a_transport_error_the_caller_is_handling_is_left_alone() -> None:
    """The stop is the caller's exception itself, not only its type: a caller
    retrying after an httpx error keeps that error's text as it was."""

    first = httpx.ConnectError("first attempt failed\n  [Errno 61] Connection refused   ")
    async with _proxy(b"HTTP/1.1 502 " + _HOSTILE_REASON + b"\r\n\r\n") as proxy:
        with _through(proxy):
            try:
                raise first
            except httpx.ConnectError:
                with pytest.raises(httpx.ProxyError) as ei:
                    await openai_codex.refresh_openai_codex_token("r")
    assert any(link is first for link in _raw_links(ei.value)), (
        "positive control: it is in the chain"
    )
    assert first.args == ("first attempt failed\n  [Errno 61] Connection refused   ",)


def test_the_walk_stops_at_a_link_that_is_not_a_transport_error() -> None:
    """A local error the request raised over is aelix's text, not the server's."""

    local = ValueError("local\nmulti-line detail   ")
    with pytest.raises(httpx.ConnectError) as ei, quoting_transport_errors():
        try:
            raise local
        except ValueError:
            raise httpx.ConnectError(_PROXY_REFUSAL) from None
    assert ei.value.__context__ is local
    assert local.args == ("local\nmulti-line detail   ",)
    assert str(ei.value).startswith("502 Bad Gateway [2J")
    _assert_contained(str(ei.value))


def test_a_transport_link_reached_only_by_context_is_quoted() -> None:
    """A transport error raised while the request was handling another (a
    failing cleanup, httpcore's ``except: ...; raise exc``) is part of the
    request's own failure: ``__context__`` is followed inside the chain."""

    import httpcore

    with pytest.raises(httpx.ReadError) as ei, quoting_transport_errors():
        try:
            raise httpcore.RemoteProtocolError(_PROXY_REFUSAL)
        except httpcore.RemoteProtocolError:
            raise httpx.ReadError("Server disconnected") from None
    inner = ei.value.__context__
    assert isinstance(inner, httpcore.RemoteProtocolError)
    assert not contains_steering_chars(str(inner))
    assert len(str(inner)) <= SERVER_TEXT_MAX_CHARS + 1
    assert ei.value.args == ("Server disconnected",)


def test_a_wrapped_ordinary_error_keeps_its_args_down_the_chain() -> None:
    """httpcore wraps the OpenSSL error as ``ConnectError(exc)`` - its args hold
    the exception, not text. A walk that rewrote every link, changed or not,
    replaced that with a string (verify's V19, alive again once the comparison
    moved to the original text)."""

    import ssl

    import httpcore

    verify = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed (_ssl.c:1000)"
    )
    try:
        try:
            try:
                raise verify
            except ssl.SSLError as inner:
                raise httpcore.ConnectError(inner) from inner
        except httpcore.ConnectError as middle:
            raise httpx.ConnectError(str(middle)) from middle
    except httpx.ConnectError as exc:
        error = exc
    before = [link.args for link in _chain(error)]
    assert before[1] == (verify,), "positive control: httpcore holds the exception itself"

    with pytest.raises(httpx.ConnectError), quoting_transport_errors():
        raise error
    assert [link.args for link in _chain(error)] == before


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("\x1b" * 4096 + _DIAGNOSTIC, _DIAGNOSTIC, id="esc-x4096"),
        pytest.param(_UNICORN, "<title>Unicorn! &middot; GitHub</title>", id="unicorn"),
    ],
)
def test_the_codex_model_error_is_bounded_where_it_is_raised(body: str, expected: str) -> None:
    """The adapter's error event quotes again (``describe_provider_error``), so
    the raise site's own quotation is pinned on the exception itself: it never
    holds the page, whatever later reads it."""

    from aelix_ai.providers.openai_codex_responses import _CodexHTTPError

    message = str(_CodexHTTPError(400, body))
    assert message.startswith("OpenAI Codex request failed (400): ")
    assert expected in message
    _assert_contained(message)


# === review round 3 ==========================================================
#
# Codex round 3 (P2): a library caller retrying inside ``except
# httpx.ProxyError`` - the proxy's hostile reason phrase in hand - called the
# Anthropic refresh, whose pi-shaped ``details=`` followed ``__context__`` past
# the request's own chain into the caller's exception and copied it raw:
# 4,278 characters with ``ESC[2J`` and OSC 52 on cdeeb166 (the exchange 4,337,
# the Anthropic sign-in 4,349, the Codex sign-in 4,262). The transport walk
# leaves the caller's exception alone by design; the formatter now stops there
# too, and still does not touch it.


@asynccontextmanager
async def _proxy_hostile_then(answer: bytes) -> AsyncIterator[str]:
    """The first CONNECT is refused with the hostile reason, every later one with ``answer``."""

    seen: list[int] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            seen.append(1)
            reason = b"Previous " + _HOSTILE_REASON if len(seen) == 1 else answer
            writer.write(b"HTTP/1.1 502 " + reason + b"\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()
    assert len(seen) == 2, seen


_HANDLED_SITES: dict[str, tuple[Callable[[], Any], type[BaseException]]] = {
    "anthropic-refresh": (lambda: anthropic.refresh_anthropic_token("r"), RuntimeError),
    "anthropic-exchange": (
        lambda: anthropic._exchange_authorization_code("c", "s", "v", "http://localhost/cb"),
        RuntimeError,
    ),
    "anthropic-login": (lambda: _login(anthropic), RuntimeError),
    "codex-login": (lambda: _login(openai_codex), RuntimeError),
    "codex-refresh": (lambda: openai_codex.refresh_openai_codex_token("r"), httpx.ProxyError),
}


@pytest.mark.parametrize("site", sorted(_HANDLED_SITES))
async def test_details_leave_out_the_exception_the_caller_is_handling(site: str) -> None:
    call, expected_type = _HANDLED_SITES[site]
    async with (
        _proxy_hostile_then(b"Current") as proxy,
        _REAL_ASYNC_CLIENT(proxy=proxy) as first,
    ):
        try:
            await first.get("https://example.invalid/first")
        except httpx.ProxyError as handled:
            before = handled.args
            assert contains_steering_chars(str(handled)), "positive control: it steers"
            with (
                _through(proxy),
                patch.object(
                    openai_codex, "start_callback_server", side_effect=_fake_callback_server
                ),
                patch.object(anthropic, "start_callback_server", side_effect=_fake_callback_server),
                pytest.raises(Exception) as ei,
            ):
                await call()
            # The caller's exception is in the chain, and untouched.
            assert any(link is handled for link in _raw_links(ei.value))
            assert handled.args == before
    assert type(ei.value) is expected_type, type(ei.value)
    message = str(ei.value)
    assert "502 Current" in message, message[:300]
    assert "Previous" not in message
    _assert_contained(message, quoted=2)


def test_details_without_a_handled_exception_keep_every_link() -> None:
    """The stop is the caller's exception, not a depth: pi's shape, ``; cause=``
    per link, outermost first."""

    from aelix_ai.oauth._helpers import format_error_details

    try:
        try:
            try:
                raise ValueError("inner")
            except ValueError as inner:
                raise KeyError("middle") from inner
        except KeyError:
            raise RuntimeError("outer")  # noqa: B904 - an implicit context on purpose
    except RuntimeError as exc:
        error = exc
    expected = "RuntimeError: outer; cause=KeyError: 'middle'; cause=ValueError: inner"
    assert format_error_details(error, handled=None) == expected
    assert anthropic._format_error_details(error, handled=None) == expected
    assert openai_codex._format_error_details(error, handled=None) == expected
    middle = error.__context__
    assert format_error_details(error, handled=middle) == "RuntimeError: outer"


@pytest.mark.parametrize("count", [65_536, 131_072])
def test_deleted_bytes_past_64_kib_never_cost_the_diagnostic(count: int) -> None:
    """Codex round 3: a 64 KiB raw pre-cut before deleting (a plausible
    performance guard) kept nothing of a diagnostic behind 65,536 ESC bytes and
    passed every earlier row."""

    assert quote_server_text("\x1b" * count + _DIAGNOSTIC) == _DIAGNOSTIC


async def test_deleted_bytes_past_64_kib_keep_the_diagnostic_at_a_site() -> None:
    body = "\x1b" * 65_536 + _DIAGNOSTIC
    with _serve(_response(400, body, reason=b"Bad Request", content_type="application/json")):
        message = await _raised(openai_codex.refresh_openai_codex_token("r"))
    assert message == f"OpenAI Codex token refresh failed (400): {_DIAGNOSTIC}"
