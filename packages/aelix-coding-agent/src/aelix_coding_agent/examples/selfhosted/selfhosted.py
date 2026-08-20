"""Self-hosted endpoint — a worked custom-provider extension (#77).

Demonstrates the full "bring your own provider + login screen + custom wire
protocol" pattern a team uses to add an OpenAI-compatible inference endpoint it
RUNS ITSELF, on its own network, when that endpoint deviates from vanilla
OpenAI in three ways an OpenAI-compatible config can't express:

  1. the MODEL is baked into the URL (``base_url = https://host/v1/<model>``),
     so the request body must not repeat it,
  2. the endpoint's scheduler wants an extra body field (``queue``) that no
     config key can name,
  3. TLS against a PRIVATE CA bundle, which no config key can point httpx at
     (``SELFHOSTED_CA_BUNDLE=/path/ca.pem``; unset means ordinary system trust).

The key move: a small CUSTOM StreamFn builds its own ``openai.AsyncOpenAI`` (with
its own http client — private CA bundle + per-model ``base_url``) and DELEGATES to
the built-in openai-completions provider via ``replace(opts, client=...)`` — reusing
all of aelix's SSE parsing / event mapping / param assembly. Three pieces:

- ``register_api_adapter(api, stream_fn)`` — the custom wire adapter (this file).
- ``register_provider(name, ProviderConfigInput(models=...))`` — the Models that
  route to that api (so they appear in ``/model``).
- ``register_login_provider(...)`` — the access-token ``/login`` method.

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
import os
import ssl
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_coding_agent.login_registry import LoginContext, LoginProvider
from aelix_coding_agent.model_registry import ProviderConfigInput

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PROVIDER_ID = "selfhosted"
_SELFHOSTED_API = "selfhosted-openai"  # our custom wire-protocol id (not "openai-completions")
_MODEL_ID = "gpt5mini"
# The model rides in the URL; the built-in openai adapter appends "/chat/completions".
_BASE_URL = f"https://llm.internal.example/v1/{_MODEL_ID}"
# (3) Where to find the CA bundle that signs the endpoint's certificate. Read
# from the environment, not hardcoded: httpx validates the path AT CONSTRUCTION
# and raises FileNotFoundError for a missing file, so a baked-in path would
# break every user (and every test) who has not created that exact file.
_CA_BUNDLE_ENV = "SELFHOSTED_CA_BUNDLE"
# (2) The scheduling queue this endpoint expects in the body. Interactive turns
# are meant to jump ahead of batch jobs; there is no aelix config key for it.
_QUEUE = "interactive"


async def _selfhosted_stream(model: Model, context: Any, opts: Any) -> AsyncIterator[Any]:
    """Custom wire adapter: private-CA client + model-in-URL + an extra body field.

    Delegates the actual streaming to the built-in openai-completions provider by
    injecting a custom ``AsyncOpenAI`` (so all SSE/event logic is reused), and
    closes that client in a ``finally`` because it built it (#174 — see the
    module docstring).
    """

    import httpx
    from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_PROVIDER
    from openai import AsyncOpenAI

    # The access token is what the /login flow stored as the credential, so it
    # arrives as opts.api_key. A real deployment might mint a short-lived token
    # or read a separate service key — adjust to your endpoint.
    access_token = opts.api_key or ""

    # (3) TLS against the endpoint's own CA. llm.internal.example is served from
    # a private CA that is not in the system trust store, and no aelix config
    # key can hand httpx a bundle path — that is what makes this custom client
    # necessary. Unset, this is verify=True, i.e. ordinary system trust.
    # NOT verify=False: that would accept ANY certificate, from anyone, which is
    # not the right answer even on a network you run yourself — the fix for a
    # private CA is to TRUST that CA, not to stop checking.
    #
    # An SSLContext, not the bundle path: MEASURED on httpx 0.28.1, passing the
    # path as a string still works but emits `DeprecationWarning: verify=<str>
    # is deprecated`, naming this exact replacement. Both spellings validate the
    # file EAGERLY (a missing path raises FileNotFoundError right here, not at
    # the first request), which is why the path is read from the environment
    # rather than baked in.
    #
    # Note this ADDS a trust anchor, it does not pin one: aelix injects
    # truststore at startup, whose handshake path also calls
    # set_default_verify_paths(), so the public roots stay trusted too.
    ca_bundle = os.environ.get(_CA_BUNDLE_ENV)
    verify = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True

    client = AsyncOpenAI(
        http_client=httpx.AsyncClient(verify=verify),
        base_url=getattr(model, "base_url", "") or None,  # (1) model is in the URL
        api_key=access_token or "unused",
    )

    def _payload(params: dict[str, Any], _model: Model) -> dict[str, Any]:
        # (2) The extra field, through ``extra_body`` — the openai SDK validates
        # its keyword arguments and a non-OpenAI one raises before anything
        # reaches the wire (MEASURED on openai 1.109.1: a top-level ``queue``
        # gives "AsyncCompletions.create() got an unexpected keyword argument
        # 'queue'", the turn ends in an error event and the server sees zero
        # requests). ``extra_body`` is merged verbatim into the JSON body, so
        # the field arrives; existing entries are kept, not overwritten.
        params["extra_body"] = {**(params.get("extra_body") or {}), "queue": _QUEUE}
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
    """Custom ``/login`` flow: an endpoint-issued access token → the credential."""

    token = await ctx.prompt("Paste the access token from your endpoint's console", password=True)
    if not token or not token.strip():
        return None
    ctx.notify("Self-hosted endpoint: access token stored", kind="info")
    return token.strip()


def setup(aelix: ExtensionAPI) -> None:
    """Register the custom adapter + provider models + the access-token login."""

    # 1. The custom wire adapter (survives /reload via bind_api_adapters replay).
    aelix.register_api_adapter(_SELFHOSTED_API, _selfhosted_stream)

    # 2. The provider + its models, routed to the custom api id so /model lists them.
    aelix.register_provider(
        _PROVIDER_ID,
        ProviderConfigInput(
            name="Self-hosted endpoint",
            models={
                _MODEL_ID: Model(
                    id=_MODEL_ID,
                    name="Self-hosted gpt5mini",
                    provider=_PROVIDER_ID,
                    api=_SELFHOSTED_API,
                    base_url=_BASE_URL,
                ),
            },
        ),
    )

    # 3. The access-token /login method (#77).
    aelix.register_login_provider(
        LoginProvider(id=_PROVIDER_ID, name="Self-hosted endpoint", authenticate=_authenticate)
    )


__all__ = ["setup"]
