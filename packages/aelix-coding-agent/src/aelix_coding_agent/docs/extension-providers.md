# Writing a Provider Extension

Status: Accepted

An extension can bring its own model provider. `register_provider` declares
it, `register_login_provider` gives it a `/login` method, and
`register_api_adapter` adds a wire protocol for an endpoint the built-in
adapters cannot talk to. A provider that needs only a base URL, headers or a
key does not need an extension: [models-json.md](models-json.md) covers it.
Writing and loading an extension in general is in
[extension-authoring.md](extension-authoring.md).

## Custom providers with their own `/login` method

A private provider can expose its own `/login` flow, such as pasting an
endpoint-issued token. Register its runtime and login method under the same id:

- `register_provider(id, ProviderConfigInput(...))` declares its wire protocol
  (`api`), `base_url` and `models` (shown in `/model` with a credential). Register
  in `setup()` so `--model <id>/<model>` can select it at launch. Later
  registrations reach the registry immediately but cannot serve an unclaimed
  pending launch; see [Launch and rebuilds](#launch-and-rebuilds).
- `register_login_provider(LoginProvider(id, name, authenticate))` adds `name`
  to the `/login` method list. When the user picks it, your async `authenticate`
  handler runs a custom flow through a `LoginContext` (the same masked
  `select` / `prompt` / `confirm` / `notify` dialogs the built-in methods use) and
  returns the credential string. The wizard persists it under `id` — you never
  touch the auth store yourself.

```python
from aelix_coding_agent.login_registry import LoginContext, LoginProvider
from aelix_coding_agent.model_registry import ProviderConfigInput
from aelix_ai.streaming import Model

async def _authenticate(ctx: LoginContext) -> str | None:
    token = await ctx.prompt(                                # masked
        "Paste the access token from your endpoint's console", password=True)
    if not token or not token.strip():
        return None                                          # None = cancel
    ctx.notify("Self-hosted endpoint: access token stored", kind="info")
    return token.strip()                                     # becomes the credential

def setup(aelix):
    aelix.register_provider("selfhosted", ProviderConfigInput(
        name="Self-hosted endpoint",
        models={"selfhosted-large": Model(
            id="selfhosted-large", provider="selfhosted",
            api="openai-completions", base_url="https://llm.internal.example/v1")},
    ))
    aelix.register_login_provider(LoginProvider(
        id="selfhosted", name="Self-hosted endpoint", authenticate=_authenticate))
```

Notes and limits:

- `models=None` augments existing builtin/models.json models. An explicit map
  replaces every model for that exact provider name, including `{}` (zero
  models). This applies to built-in names and coexisting models.json base URLs
  too. Model.provider is copied and stamped with the registration name. Each
  re-registration replaces the whole previous extension configuration; caller
  mutation does not change a queued or active registration. Refresh/reset keep
  it, unregister restores lower layers, and a failed reload keeps the prior one.
- Launch, `/model`, pickers and registry consumers see the same replacement
  models. An unlisted explicitly named id may still backfill from those models
  when all agree on one API and nonempty host. Empty/split/hostless scopes refuse;
  this is not a strict id allowlist. A stale selected route or Model.headers
  projection refuses before credentials are resolved: select the current model
  again after re-registration. Name, cost and context metadata alone do not
  invalidate it. Nonowned Model headers and explicit request-option headers
  retain their existing behavior.
- Changing the registration, route or Model headers in `before_provider_request`
  or `before_provider_payload` cancels that pending request before the SDK sends.
  The same auth snapshot is checked after those hooks; credentials are not
  refreshed or resolved twice. Metadata-only model changes remain valid. After
  a registration or OAuth projection changes the route/headers, select the current
  model again; Aelix does not silently replace your selection.
  A closing refusal never restarts that request, even if its displayed text looks
  like a retryable provider status. A later user turn may use a fresh snapshot;
  ordinary provider/OAuth retries retain their existing behavior.
  A `message_end` replacement may change the displayed response but cannot
  reopen a fatal request through tool calls, steering or follow-ups in the same
  user prompt. Unconsumed queued input remains pending for a later user prompt;
  steering keeps its configured one-at-a-time/all behavior.
- Keys use runtime → stored-owning → extension key (or JSON key if absent) →
  provider environment. JSON provider headers merge with extension headers,
  whose collisions win; `auth_header=None` inherits JSON and `False` clears it.
  JSON per-model headers on replaced rows are removed. A delegated child must
  reproduce the explicit registration and route, even if it uses a builtin
  name: enable inheritance or supply the same owner through profile `extensions`.
  Missing/wrong owners refuse through hooks and reloads as well as startup.
  See [ADR-0262](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0262-explicit-provider-registrations-own-model-routes.md).
- The credential your handler returns is stored under the provider `id`, so the
  same id must be used for both calls for turns to authenticate.
- `api` is normally a built-in adapter id (`openai-completions`,
  `anthropic-messages`, `google-generative-ai`, …). For an endpoint those can't
  express, register your own with `register_api_adapter` (below). A model on an
  unregistered `api` is hidden from `/model`.
- The `/login` picker is interactive-only (a TTY). In `--print` / `--json` /
  `--mode rpc` there is no wizard, so a custom login flow does not run there.
- The login registry is process-global: an extension removed on `/reload` should
  call `unregister_login_provider(id)` in its teardown.

### Launch and rebuilds

`setup()` registrations bind before model selection on the first build and every
`/new`, `/fork`, `/resume` and `/reload`. A named registered provider keeps its
route regardless of keys; it never becomes an OpenRouter id (ADR-0249).

A provider arriving after launch lookup cannot claim that launch (#367), even
if its id matches or settings `defaultProvider` names it. This includes
registrations during `session_start`, in tasks started by `setup()` or a handler,
and in hooks of turns those handlers trigger. Aelix waits out those turns and
checks again after `session_start`. `set_model` there cannot release the decision:

- An unclaimed launch exits 1 in print/JSON before any request, asking for a
  `setup()` registration. Interactive and RPC start held (`api='unknown'`).
  Interactive `/model` can then select the now-registered provider; CLI RPC has
  no model registry for `set_model`.
- During `session_start`, an unclaimed launch cannot start a request, whatever
  model a handler selects. A triggered turn emits the normal refused-turn events,
  including `agent_end`, with its error and no send. Awaiters are woken; its
  prompt stays in the conversation and goes out with the next prompt that runs,
  as for other failed turns.
- A launch served by an already registered builtin, JSON or setup provider stays
  there; handlers' turns may run there. Later rebuilds re-derive launch inputs
  and hold wherever they land on the late provider. This also covers the
  post-login pick and `/agents use` of a profile none of whose `model:` /
  `provider:` applies (or `--none`) when it replaces a profile whose own `model:`
  or `provider:` applied (any other such use keeps the current model and
  re-derives nothing).
  `/model <name>/<id>` or a profile explicitly naming it can select it afterwards.

`/model` records its choice. A rebuild restoring the session's runnable model
(#376: conversation present, no launch model override) keeps that choice without
a hold. `/new` and rebuilds that do not restore re-derive launch inputs and hold
again. Pi retains the session model instead; Aelix checks these rebuilt inputs.

`--api-key` attaches only after the decision. The hold also checks `model_select`:
a handler replacing its placeholder is put back on it at launch, rebuild and
`/agents use`. Turns triggered while applying it are refused, with their prompts
retained. A task acting after the decision is a later extension action, outside
that hold; one acting while the hold is being applied is held.

A failed `/reload` retains the previous provider's route/key until unregister,
as in Pi. Delegated children load no extensions by default. Enable
`inherit_extensions: true` or profile `extensions` to reproduce a selected
explicit owner; its missing/wrong route refuses even under a builtin name.
The parent passes `--provider <name> --model <id>` rather than letting the child
infer OpenRouter. The ownership notes above govern subsequent hook/reload checks.

### When config isn't enough — a custom wire adapter

For a model in the URL, custom body fields or private-CA `httpx` client,
register a **StreamFn** `(Model, Context, SimpleStreamOptions) -> AsyncIterator[event]`
with `register_api_adapter(api, stream_fn)`. A matching `Model.api` routes to it.
Delegate to the built-in adapter with `replace(opts, client=...)` to reuse SSE
parsing and event mapping. Build that client with `create_async_client`: a bare
`AsyncOpenAI` (openai 2.34+) merges `OPENAI_CUSTOM_HEADERS` over its key and
refuses an empty one (#278). It waits for `Retry-After` up to 60 s unless built
with another `max_retry_delay_ms`; `**sdk_retry_options(opts)` (same module)
passes `opts`' own. `stream_simple` refuses a call with no key, so a keyless
endpoint also passes `api_key=opts.api_key or "unused"` to `replace` and sends
`Authorization: Bearer unused`; to send none, delegate to
`stream_openai_completions` and add `"Authorization": openai.Omit()` to
`params["extra_headers"]` in the payload hook when `opts.api_key` is empty:

```python
import contextlib
import os
import ssl
from dataclasses import replace
import httpx
from aelix_ai.providers._openai_client import create_async_client
from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_PROVIDER
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import ProviderConfigInput

# The operator supplies the signing CA bundle; unset uses system trust.
# A missing path raises FileNotFoundError at context construction.
_CA_BUNDLE_ENV = "SELFHOSTED_CA_BUNDLE"

async def selfhosted_stream(model, context, opts):
    ca_bundle = os.environ.get(_CA_BUNDLE_ENV)            # custom TLS; unset = verify=True
    verify = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
    client = create_async_client(
        http_client=httpx.AsyncClient(verify=verify),
        base_url=getattr(model, "base_url", "") or None,  # model baked into the URL
        api_key=opts.api_key or "unused",
    )
    def payload(params, _m):
        # a non-OpenAI body field no config key can name (see extra_body, below)
        params["extra_body"] = {**(params.get("extra_body") or {}), "queue": "interactive"}
        params["model"] = ""               # the model is in the URL, not the body
        return params
    try:
        async for ev in OPENAI_COMPLETIONS_PROVIDER.stream_simple(
            model, context, replace(opts, client=client, on_payload=payload)
        ):
            yield ev
    finally:
        with contextlib.suppress(Exception):   # you built it, you close it
            await client.close()               # AsyncOpenAI: close(), not aclose()

def setup(aelix):
    aelix.register_api_adapter("selfhosted-openai", selfhosted_stream)
    aelix.register_provider("selfhosted", ProviderConfigInput(models={"gpt5mini": Model(
        id="gpt5mini", api="selfhosted-openai",
        base_url="https://llm.internal.example/v1/gpt5mini")}))
```

**Close clients you build in `finally`.** The adapter closes only clients it
creates; closing an injected client would break its owner's next turn. Otherwise
each request strands its client/pool until cyclic collection (#174). The method
is SDK-specific: OpenAI/Anthropic async clients use `await client.close()` (no
`aclose`); httpx uses `await client.aclose()`; Google uses
`await client.aio.aclose()` (its synchronous `close()` leaves the async pool open).
Closing the example's OpenAI client also closes its injected httpx client.
Suppress cleanup exceptions so generator cancellation remains a clean abort.

`register_api_adapter` replays across `/reload`, which resets the api registry.
Custom body keys must be OpenAI-valid or go in `extra_body`; the SDK rejects
unknown top-level kwargs. Your adapter owns networking and TLS. Pass
`verify=ssl.create_default_context(cafile=...)` the CA bundle signing your
endpoint, with its path read from an environment variable. A path is validated
at construction and raises `FileNotFoundError` if absent, so an unset variable
should use system trust (`verify=True`). The context replaces httpx 0.28's
deprecated `verify="/path/ca.pem"`. `verify=False` disables certificate checking
and accepts any certificate; it does not trust an internal CA. This adds a trust
anchor, not a pin: Aelix's startup truststore also loads platform default roots.

**A text block may arrive whole.** An adapter that yields its own events, rather
than a built-in adapter's, can send a text block as `text_start` and then a
`text_end` carrying the text, with no `text_delta` in between: the TUI draws it
when that `text_end` arrives, below any reasoning that came before it, exactly as
it draws a streamed one. Give every event of a block its `content_index` — the
block's position in the message's `content`, as the built-in adapters do: the TUI
tells one reasoning block from the next by it. An answer's text does not depend
on it, so a `text_delta` your adapter rebuilds from its text alone (index 0) is
still drawn once.

A complete worked example ships at
`aelix_coding_agent/examples/selfhosted/selfhosted.py`. Load it like any extension —
point `--extension` at the file, drop it in a project-local `.aelix/extensions/`,
or install it as a package (see [Loading an extension](extension-authoring.md#loading-an-extension)).
