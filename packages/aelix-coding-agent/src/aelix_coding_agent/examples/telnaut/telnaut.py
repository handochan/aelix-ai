"""Telnaut — a worked corporate custom-provider extension (#77).

Demonstrates the full "bring your own provider + login screen + custom wire
protocol" pattern an in-house team would use to add a private provider that
deviates from vanilla OpenAI in three ways an OpenAI-compatible config can't
express:

  1. the MODEL is baked into the URL (``base_url = https://host/v1/<model>``),
  2. an employee number (사번) rides in the standard OpenAI ``user`` field,
  3. TLS verification is disabled (``httpx.AsyncClient(verify=False)``, self-signed
     internal CA).

The key move: a small CUSTOM StreamFn builds its own ``openai.AsyncOpenAI`` (with
the ``verify=False`` http client + per-model ``base_url``) and DELEGATES to the
built-in openai-completions provider via ``replace(opts, client=...)`` — reusing
all of aelix's SSE parsing / event mapping / param assembly. Three pieces:

- ``register_api_adapter(api, stream_fn)`` — the custom wire adapter (this file).
- ``register_provider(name, ProviderConfigInput(models=...))`` — the Models that
  route to that api (so they appear in ``/model``).
- ``register_login_provider(...)`` — the employee-number ``/login`` method.

AND ONE RULE THIS EXAMPLE EXISTS TO TEACH: A CLIENT YOU BUILD IS A CLIENT YOU
CLOSE (#174). ``stream_simple`` is called once per request, so a stream_fn that
builds a client per call builds one per turn. The built-in adapter does
``client = opts.client or create_async_client(...)`` and only closes the ones it
created — hand it yours through ``replace(opts, client=...)`` and it will
deliberately leave it alone, because closing an injected client would break the
caller's next turn. That makes the ``finally`` below yours, not the built-in's.
Skip it and each turn strands a client and its connection pool until the cyclic
garbage collector happens to run.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_coding_agent.login_registry import LoginContext, LoginProvider
from aelix_coding_agent.model_registry import ProviderConfigInput

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PROVIDER_ID = "telnaut"
_TELNAUT_API = "telnaut-openai"  # our custom wire-protocol id (not "openai-completions")
_MODEL_ID = "gpt5mini"
# The model rides in the URL; the built-in openai adapter appends "/chat/completions".
_BASE_URL = f"https://llm.telnaut.internal/v1/{_MODEL_ID}"


async def _telnaut_stream(model: Model, context: Any, opts: Any) -> AsyncIterator[Any]:
    """Custom wire adapter: verify=False client + model-in-URL + 사번 as ``user``.

    Delegates the actual streaming to the built-in openai-completions provider by
    injecting a custom ``AsyncOpenAI`` (so all SSE/event logic is reused), and
    closes that client in a ``finally`` because it built it (#174 — see the
    module docstring).
    """

    import httpx
    from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_PROVIDER
    from openai import AsyncOpenAI

    # The employee number (사번) is what the /login flow stored as the credential,
    # so it arrives as opts.api_key. A real deployment might split "empno:token"
    # or read a separate service key — adjust to your endpoint.
    employee_no = opts.api_key or ""

    client = AsyncOpenAI(
        # (3) TLS verification off. This is NOT a shortcut around a cert error:
        # llm.telnaut.internal is served from a private CA that is not in the
        # system trust store, and no aelix config key can express that. It also
        # means this stream_fn accepts ANY certificate, so it must stay scoped
        # to the internal host in _BASE_URL — never point it at the internet.
        # (If your CA *can* be handed to httpx, prefer `verify="/path/ca.pem"`.)
        http_client=httpx.AsyncClient(verify=False),
        base_url=getattr(model, "base_url", "") or None,  # (1) model is in the URL
        api_key=employee_no or "unused",
    )

    def _payload(params: dict[str, Any], _model: Model) -> dict[str, Any]:
        if employee_no:
            params["user"] = employee_no  # (2) 사번 → standard OpenAI ``user`` field
        params["model"] = ""  # the model is in the URL, not the body
        return params

    try:
        stream = OPENAI_COMPLETIONS_PROVIDER.stream_simple(  # type: ignore[attr-defined]
            model, context, replace(opts, client=client, on_payload=_payload)
        )
        async for event in stream:
            yield event
    finally:
        # #174 — we built this client, so we close it, on every exit. That
        # includes the abort path: a stream_fn is an async generator, so an
        # abandoned turn unwinds it through GeneratorExit right here. (Measured
        # both ways against a local server, 8 turns inside gc.disable(): with
        # this block 0 clients and 0 server-side connections survive; without
        # it, 8 and 8 on the completed path, 8 clients on the aborted one.)
        #
        # ``close()``, not ``aclose()``: MEASURED on openai 1.109.1 —
        # ``AsyncOpenAI`` has no ``aclose`` at all and its ``close`` IS a
        # coroutine, so ``aclose()`` here raises AttributeError and closes
        # nothing. The one await also closes the httpx client above (measured:
        # ``is_closed`` False -> True), so it does not need its own teardown.
        # Other SDKs differ — ``httpx.AsyncClient`` wants ``aclose()`` and
        # ``google.genai.Client`` wants ``client.aio.aclose()`` (its plain
        # ``close()`` leaves the async pool open). Check yours; don't copy this
        # line blind.
        #
        # Suppressed because this runs while a cancellation is unwinding, and a
        # raise from cleanup would replace the user's clean abort with a bogus
        # provider error in the transcript.
        with contextlib.suppress(Exception):
            await client.close()


async def _authenticate(ctx: LoginContext) -> str | None:
    """Custom ``/login`` flow: employee number → the stored credential."""

    employee_no = await ctx.prompt("사번을 입력하세요 (employee number)")
    if not employee_no or not employee_no.strip():
        return None
    ctx.notify(f"Telnaut: signed in as employee {employee_no.strip()}", kind="info")
    return employee_no.strip()


def setup(aelix: ExtensionAPI) -> None:
    """Register the custom adapter + provider models + the employee-number login."""

    # 1. The custom wire adapter (survives /reload via bind_api_adapters replay).
    aelix.register_api_adapter(_TELNAUT_API, _telnaut_stream)

    # 2. The provider + its models, routed to the custom api id so /model lists them.
    aelix.register_provider(
        _PROVIDER_ID,
        ProviderConfigInput(
            name="Telnaut (사내)",
            models={
                _MODEL_ID: Model(
                    id=_MODEL_ID,
                    name="Telnaut gpt5mini",
                    provider=_PROVIDER_ID,
                    api=_TELNAUT_API,
                    base_url=_BASE_URL,
                ),
            },
        ),
    )

    # 3. The employee-number /login method (#77).
    aelix.register_login_provider(
        LoginProvider(id=_PROVIDER_ID, name="Telnaut (사내)", authenticate=_authenticate)
    )


__all__ = ["setup"]
