# Providers and Models

Status: Accepted

How to give `aelix` a provider key, pick a model, and switch between them. For
adding **custom** providers/models (custom base URLs, header packs, per-model
overrides) see [models-json.md](models-json.md).

## Selecting a model

Model ids use the `<provider>/<model>` form:

```bash
aelix --model openai/gpt-4o-mini "..."
aelix --provider anthropic --model claude-sonnet-4-6 "..."
```

Discover what is available:

```bash
aelix --list-models            # every model in the catalog
aelix --list-models claude     # filter by substring
```

Inside the interactive TUI, `/model` opens a picker to switch the active model
mid-session. The picker lists every provider you have a key for, a project
`.env` key included — choosing one there is your own pick. `/model <id>` and
`/model <provider>/<id>` follow the same two rules as the launch below. While
you hold a credential of your own anywhere (an `--api-key` counts, and so does
a key for a provider that lists no models), a project `.env` key never decides
where the string goes: a provider only the `.env` authenticates is left out of
the match — even when the session is already on it — and where only such a
provider could serve the string, `/model` refuses and says so rather than
switch (export the key or `/login` to use it). A prefix that names a provider
you defined (in `models.json`, including a re-pointed built-in — a re-pointed
`openrouter` too — or by an extension) stays inside it, and its key may come
from the `.env`: the prefix chose the destination, the file only authenticates
it — as with `--model`. One difference from `--model`: the built-in
`openrouter/` prefix is not an exemption in `/model`, so with the OpenRouter
key only in a project `.env` and a key of your own elsewhere,
`/model openrouter/auto` is refused, while `--model openrouter/auto` runs on
the `.env` key.

The model picked for you after `/login` (when the current one cannot run)
follows the same rule: your saved `defaultProvider`/`defaultModel` (global
settings only — a project's `.aelix/settings.json` is never read there, #369)
that only a project `.env` key authenticates is skipped while you hold a key
of your own. A program
that runs RPC mode with a model registry gets the same from `cycle_model`,
which rotates only through models the rule leaves; `set_model` names its
provider, so a `.env` key may authenticate it, as with `--provider`.
(`aelix --mode rpc` itself wires no registry, and answers both with "requires
a ModelRegistry".)

### A resumed session keeps its model

Opening a session that already has a conversation — `--continue`, `--resume`,
`--session`, `--fork`, and inside a session `/resume`, `/fork`, `/clone`,
`/import` and `/reload` (RPC: `switch_session`, `fork`, `clone`) — puts you back
on the model it last ran on: whichever the session recorded last, a `/model`
pick or the model that wrote an answer. A pick is recorded when you make it, so
`/model` then quitting comes back on that model. Your settings `defaultModel`
does not override the session; it is for new sessions (`/new` starts on the
launch model). `--model` / `--provider`, an agent profile that names a `model:`
or `provider:`, or `--api-key` (typed for the launch model) win over the
session, as in pi.

The model comes back only if it can still run: it must be one this build or
your `models.json` / an extension knows, with a key for its provider (from any
source, a project `.env` included: the session already chose the route). A
model id nothing lists comes back as a custom id on a provider you defined in
`models.json` or an extension, when that provider has any key — as
`--provider <it> --model <id>` would at launch. On any other provider it comes
back only on a key of your own for that provider, never on a project `.env`'s:
for one the launch sent to OpenRouter as written that is the rule the launch
itself follows, and for a built-in provider whose catalogue does not list the
id (say `anthropic/claude-www-unlisted`) it is stricter than the launch, where
`--provider anthropic --model claude-www-unlisted` is accepted on a `.env` key.
When the model cannot come back, aelix falls back to the model it would have
picked without the session and says so:

```
Warning: Could not restore model anthropic/claude-haiku-4-5. Using openrouter/anthropic/claude-sonnet-4.5
```

It is shown under the banner in the TUI and after each in-session command
above, and printed on stderr in `-p`, `--mode json` and RPC (at startup and
after `switch_session`, `fork` and `clone`). The `Using …` half names the model
the run is on when the line is said — after the extensions' `session_start`
handlers ran, so a handler that selects a model there is the one named — and
appears only when that model can run: it has an adapter and its provider a key.

A prompt on a model that certainly cannot run, for a reason that is not a
credential, is refused before anything is written to the session, in every
mode: no model at all (the empty placeholder of a launch that resolved
none — no provider and no adapter — answered `No model selected.`), a model
this build has no adapter for, whatever its base URL (`mistral/...` models,
for one), or a model with no base URL (none declared, or a `{NAME}`
placeholder in it left unset — a `cloudflare-workers-ai` model without
`CLOUDFLARE_ACCOUNT_ID` even with a key set, whose request would go out with
the placeholder in its path). It is asked once, after the extensions' `input`
handlers ran and before the prompt is written (pi's place), so an input an
extension handles itself is still answered, a model an `input` handler
switches to is the one judged, and a turn an extension triggers
(`send_message(..., trigger_turn=True)`) is refused the same way (pi does not
ask there; while `session_start` handlers still run after a launch that
resolved no model, the late-provider hold answers that turn first, as before,
and its message is written with the hold's error). `-p` / `--mode json` stop with exit 1, the TUI says it and waits, and an
RPC `prompt` is answered `success: false`. (The TUI still answers a model with
no adapter before the `input` handlers, with its own advice, as it did before.)

No credential or auth setting is asked there — no key, header, token or
Google Cloud setting. A prompt whose provider has no key runs as it always
did: the TUI and RPC send it and it fails with `No API key for provider:
<provider>`, and `-p` / `--mode json` refuse it at startup (before any `input`
handler) with `No API key found for <provider>.` That keeps every kind of auth
the providers accept working — an auth header in `models.json` or an
extension's provider `headers`, `ANTHROPIC_CUSTOM_HEADERS`,
`ANTHROPIC_AUTH_TOKEN`, Vertex Application Default Credentials, a Vertex key
from `--api-key`, `auth.json` or `models.json` — and an RPC prompt on Vertex
with no Google Cloud setup is written and fails with the adapter's own error
(`Vertex AI requires a project ID`), as before. (The TUI and `-p` still refuse
such a Vertex model before the `input` handlers, as they did before.) pi
refuses a keyless prompt before writing it; aelix does not yet.

Known limit: "a key" in the restore rule above means a key aelix's registry
counts (an API key from `auth.json`, `models.json`, the environment, a project
`.env`, `--api-key`, an extension's registration). A session whose provider is
authenticated only by a header, `ANTHROPIC_AUTH_TOKEN` or Vertex Application
Default Credentials is not restored: it falls back to the launch model and says
`Could not restore model …` (without the `Using` half when the fallback is
authenticated the same way). Pass `--model` to reopen it on that model.
The session's thinking level is restored against the model the session came
back on (below).

### How a `--model` string becomes a provider

Aelix follows pi's order (`resolveCliModel`), with exact ids only and two guards
of its own ([ADR-0250](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)):

1. **`--provider` names the provider.** The id is looked up inside it; a repeated
   prefix is tolerated (`--provider anthropic --model anthropic/claude-haiku-4-5`
   is `claude-haiku-4-5`), and an id the provider does not list is sent as a
   custom id, with a one-line warning. **`--provider` requires `--model`**, as in
   pi: `aelix --provider openai` alone — or an agent profile with `provider:` and
   no `model:` — stops with `Error: --provider requires --model (for example:
   --provider openai --model <id>)` and exit 1 in every mode, the interactive one
   and `--mode rpc` included, before any MCP server starts or the session's
   extensions load. Without `--agent`/`--agent-file` it also comes before the
   project-trust question, so nothing is asked and no extension's `setup()`
   runs. With a profile it comes after the profile is read, and the profile is
   read under the trust answer: in a directory with `.aelix/` resources (a
   `.aelix/settings.json` alone counts) and no
   `--approve`/`--no-approve`, the trust question (interactive) and its load of
   your user, global and `-e` extensions' `setup()` come first. Your settings
   `defaultModel` does not count as the `--model`, and `--provider ""` is no
   provider. The one exception is `--provider openrouter` with
   `OPENROUTER_DEFAULT_MODEL` in your shell: it runs that model with an
   OpenRouter key from anywhere — one an extension registers included — and
   without one fails as it did before (below).
2. **A prefix that names a provider** — any provider aelix knows: the catalog,
   your `models.json`, an extension's `register_provider` — picks it,
   case-insensitively (`RetryProbe/held-model` works). A provider **you defined**
   wins over a built-in spelled the same up to case, and never lets the string go
   anywhere else: `ollama/qwen3.6:35b-a3b`, `mygateway/some-model` and
   `extprov/m1` reach that endpoint, and an id it does not list is backfilled from
   its own models (when they all speak one protocol) or refused naming the
   provider. Two of your providers that differ only in case (`OpenAI`, `OPENAI`)
   and a prefix that spells neither are refused naming both.
3. **Otherwise the whole string is matched as an id** across every provider.
   One match wins (`x-ai/grok-4.3` is OpenRouter's, `held-model` is your
   `models.json` provider's). Several matches: the settings `defaultProvider`
   decides if it is one of them, else the **one** matching provider you hold a
   credential for; otherwise it is an error that lists them —
   `Model "gpt-4o-mini" is ambiguous across providers: azure-openai-responses/gpt-4o-mini,
   cloudflare-ai-gateway/gpt-4o-mini, openai/gpt-4o-mini. … Use --provider or
   provider/model.` A bare id nothing lists is sent to the `defaultProvider` as a
   custom id. In both cases `defaultProvider` is the merged setting — a trusted
   project's `.aelix/settings.json` can set it, over yours (an untrusted one is
   not read, [project-trust.md](project-trust.md)) — so while you hold a credential
   of your own it counts only when it names a provider your own credential
   authenticates, or a provider you defined (`models.json`, an extension, a
   re-pointed built-in); otherwise it is ignored and the error says so
   (`Settings defaultProvider "anthropic" was not used: …`).
4. **Inside the provider a prefix named, the id must match exactly** (no fuzzy
   matching). When it does, but you hold no credential for that provider and
   exactly one provider you do hold a credential for lists the whole string as
   its id, that one is used: with only `OPENROUTER_API_KEY`,
   `openai/gpt-4o-mini` goes to OpenRouter; with an OpenAI key as well, to
   OpenAI.
5. **When it does not match**, the whole string is tried as an id elsewhere — a
   provider you hold a credential for first; if you hold one for the provider
   the prefix named, the id stays there as a custom id.

Then the two guards:

- **A key from a project `.env` never chooses where a prompt goes.** It still
  authenticates the route your flags, your `models.json` and your own keys
  chose — but the "provider you hold a credential for" questions above count
  only credentials you exported in your shell, stored with `/login` (in your
  agent dir's `auth.json`) or wrote into your `models.json`. So a cloned repo's
  `OPENROUTER_API_KEY` cannot pull `anthropic/claude-haiku-4-5` off your own
  Anthropic key, and its `OPENAI_API_KEY` cannot pull `openai/gpt-4o-mini` off
  OpenRouter. Delegated agents and an `aelix` the bash tool starts inherit the
  same judgement (aelix passes them the list of names the `.env` supplied, in
  `AELIX_DOTENV_ADMITTED`). Nor does a `.env` value choose a **host**: a
  `{NAME}` placeholder in a base URL is filled from a project `.env` only for
  Cloudflare's two ids; for anything else (a `{TENANT_KEY}` in your own
  `models.json` template) export the variable — until then the model is not
  runnable and the refusal says so. An embedder that installs an
  `AuthStorage.set_fallback_resolver` counts as holding a credential of its
  own (the providers a resolver answers for cannot be listed), and a resolver
  answer that carries a `.env` value (equal to it ignoring case and
  surrounding spaces, or - both 8 characters or longer - with it inside) does
  not count as one - a resolver must not derive its answer from a variable a
  project `.env` supplied (`aelix_ai.dotenv_record.dotenv_supplied`).
- **New OpenRouter ids.** This build's catalog lacks many of OpenRouter's live
  ids (pi refreshes its catalog from pi.dev; aelix does not). So a
  `<vendor>/<model>` string **that OpenRouter's part of this build's catalog does
  not list** goes to OpenRouter as written, with a one-line note, **only when you
  hold an OpenRouter key of your own** (exported, `/login`, or `models.json`) —
  when its prefix is one no provider has (`newlab/model-x`), or one of the
  prefixes OpenRouter shares with a provider (`anthropic/…`, `openai/…`, …) and
  you hold no key of your own for that provider. That includes an id the
  vendor's own catalog lists: with only `OPENROUTER_API_KEY`, `openai/o1-pro`
  goes to OpenRouter, because OpenRouter is the only provider you can reach it
  through. It never happens under a provider you defined, nor under a catalogued
  provider OpenRouter has no ids for (`xai/…`, `openai-codex/…`, `groq/…`), nor
  for a bare id, nor **with `--api-key`**: those stay pi's errors.

The explicit routes to OpenRouter are `openrouter/<id>` (the prefix is stripped,
as in pi: `openrouter/newlab/model-x` sends `newlab/model-x`) and
`--provider openrouter --model <id>`; both work with a key from anywhere,
a project `.env` included. `OPENROUTER_DEFAULT_MODEL`, read from your **shell**
only, is the id used when no `--model` is given, as
`--provider openrouter --model <it>` (so `aelix --provider openrouter` alone runs
with it, where pi, which has no such variable, refuses) — "shell only" unless you hatch it yourself:
with `AELIX_DOTENV_ALLOW=OPENROUTER_DEFAULT_MODEL` in your shell, a project
`.env`'s value is admitted and picks that model. `OPENROUTER_BASE_URL` (your shell, or a
`.env` name you listed in `AELIX_DOTENV_ALLOW`) applies to every model whose provider
is `openrouter`, wherever it is picked: `--model` / `--provider` at launch, `/model`
and its picker (also when `/scoped-models` narrows it), a session you continue with
`-c`, `/agents use`, a delegated agent, and an embedder's rpc `set_model` /
`cycle_model`. It wins over `providers.openrouter.baseUrl` in `models.json` and over a
`baseUrl` on one of that provider's `models` entries, and it changes nothing but the
host. It moves the provider named exactly `openrouter` and no other: a provider you
defined under another name keeps its own `baseUrl` and key, even when it serves the
same ids or points at `https://openrouter.ai/api/v1`, and so does one you spelled
`OpenRouter`. One model does not get it: one an extension builds itself with
`aelix_ai.models.get_model()` (to pass to `set_model`, say) is the catalog entry and
keeps `https://openrouter.ai/api/v1`; the copies `ctx.model_registry` hands an
extension carry the variable. (pi has no such variable. In pi's `models.json`,
`providers.openrouter.baseUrl` re-points OpenRouter, and a `baseUrl` on one of that
provider's `models` entries wins over it for that model.)

```bash
aelix --model openrouter/openai/gpt-4o-mini "..."              # OpenRouter, whatever keys you hold
aelix --provider openrouter --model openai/gpt-4o-mini "..."   # the same
aelix --provider anthropic  --model claude-haiku-4-5 "..."     # the vendor
```

(`--provider anthropic` is the vendor unless you defined a provider of that name
in some case — then it is yours. The same goes for `--provider openrouter`: if
you named one of your providers `OpenRouter` in any case, that flag selects
yours, so rename it if you also want to force OpenRouter.)

A provider an extension registers while a session is starting — from the end of the
build through its `session_start` handlers and the turns they trigger, which aelix waits
out, up to aelix's check after `session_start`: in a `session_start` handler, in a
handler of a turn one triggers, such as its `input` or `before_agent_start` handler
(that turn itself is refused, below; it may run after the handler returned), or in a
task a handler or `setup()` started that registers then —
exists only after the launch model was chosen, so at launch its name is unknown, and a launch model
that no provider registered at launch could take — `--model <name>/<id>`,
`--provider <name> --model <id>`, a bare id only it lists, a
`defaultProvider`/`defaultModel` pair in `settings.json`, an agent profile's
`model:`, or a settings `defaultProvider` alone — is refused, as in pi
(`--provider <name>` alone never gets that far: it is the usage error above). A launch model a
provider registered at launch does serve (a built-in, one in `models.json`, one
registered in `setup()`) stays on that provider, as pi's launch model does, even
if the new provider serves the same id or your settings `defaultProvider` names
it — and a turn a `session_start` handler triggers runs there too. The refusal: `-p` and
`--mode json` stop with an error before any request ("… names provider '<name>',
which an extension registered while a session was starting (for example in a
session_start handler) … Register '<name>' in
the extension's setup() (its factory) to use it at launch."), and the interactive
mode starts with that warning and sends nothing until you pick a model with
`/model`. What decides is where your launch inputs land once the handler has run,
not what the handler did: a `set_model` in it does not make the launch pass. And
whatever re-derives the model from those inputs without you naming one holds the
session again wherever it lands on that provider — also when the session started on
a registered provider, since those builds re-derive the model from the launch
inputs where pi keeps the session's: `/new`, and a rebuild that does not restore
the session's own model (one of a session with no conversation yet, of a run
whose model you named at launch, or whose recorded model cannot run — see "A
resumed session keeps its model"; a rebuild that restores it is not held), also
when a `session_start` handler of the rebuild sets a model; `/agents use`
of a profile that names no model or provider of its own (or `--none`, or one whose
`model:` your `--model` overrides) — also after a launch that went elsewhere, such
as `--agent` with `provider: openrouter` and `--model <name>/<id>` — and the model
picked for you after `/login`, which picks as it would with no saved default and
never that provider.
For a refused launch nothing is sent in the meantime, whatever keys you hold:
while the extensions' `session_start` handlers run, a launch model no registered
provider has claimed is not runnable yet, and no turn starts at all — not even
one a handler triggers after setting a model of its own with `set_model` — so a
handler's `trigger_turn` then ends as a refused turn (nothing sent; its message
stays in the conversation and goes out with the next prompt that is sent), a
`model_select` handler cannot move the session off the hold — nor send a turn
while aelix puts the session on hold, at launch, on a rebuild or at `/agents use`
(that turn is refused too, and nothing is sent; its message stays in the
conversation and goes out with the next prompt that is sent) —
and an `--api-key` is attached only after that decision — typed with a refused
model it is not used at all, and no provider holds it. The same holds while a
rebuild's `session_start` handlers run in a session that is held. Once the session is running the provider is there,
so `/model <name>/<id>` switches to it like any other, and so does `/agents use`
of a profile whose own `model:` or `provider:` names it (`provider:` alone names
the route too). A `/model` choice is recorded in the session, so a rebuild that
restores the session's model (`/reload`, `/fork`, `/clone`, a `/resume` back to
it) comes back on it and is not held; `/new`, and a rebuild that does not
restore (as above), re-derive the launch model and hold again (pi keeps the
session's model). A profile's choice lasts until an `/agents use` that names no
route. `/model` also saves it as your default, so the next launch without
`--model` reads it from `settings.json` and is refused the same way. The RPC mode
starts held too but cannot pick another model (`aelix --mode rpc` has no model
registry for `set_model`): restart it with another `--model`. Register providers
in the extension's `setup()` and they work at launch. A provider the handler
registers and then unregisters is simply unknown.

The same rules apply to an agent profile's `model:`, to a hand-written
`defaultModel` in `settings.json`, and to delegated children — which are told
`--provider` too wherever they could not reach the parent's route alone (a
provider you defined, which a child without your extensions does not have). A
route only your `--api-key` would decide is left to the child, which never
receives the key.

## Providing an API key

A credential can come from four places. Pick whichever fits your setup:

1. **Environment variable** (simplest) — set the provider's variable before
   running `aelix` (see the table below).
2. **`--api-key <key>`** — an inline key for a single run, attached to the
   provider the run actually resolved (after extensions have loaded), and it
   outranks every other source for that provider in that invocation. So
   `--model ollama/<id> --api-key K` sends `K` to your ollama endpoint. With
   `--api-key`, a prefix that names a provider keeps the string there — the id
   it lists, else a custom id — whatever else you hold:
   `--model openai/gpt-4o-mini --api-key K` sends `K` to **OpenAI** even with an
   OpenRouter key exported (rules 4 and 5 and the OpenRouter guard below are
   skipped; pi would send it to OpenRouter with `K` as the bearer). The price of
   that choice: if `K` is an **OpenRouter** key, `--model openai/gpt-4o-mini
   --api-key K` sends it to `api.openai.com` as the bearer, where pi would have
   put it on OpenRouter — write `openrouter/openai/gpt-4o-mini` or
   `--provider openrouter` for an OpenRouter key. A prefix no provider has (`newlab/model-x`) is
   refused as not found, as in pi — write `openrouter/newlab/model-x` or
   `--provider openrouter --model newlab/model-x` to send it to OpenRouter with
   that key (an id OpenRouter's part of the catalog lists, such as
   `x-ai/grok-4.3`, goes there with it, as in pi). A string that does not resolve
   (ambiguous, not found) gets no key. It is not forwarded to delegated children.
3. **`models.json`** — an `apiKey` field on a provider, which itself may point
   at an environment variable or a `!command` (see
   [models-json.md](models-json.md)).
4. **`/login` inside the interactive TUI** — the OAuth path (GitHub Copilot and
   other subscription logins). It stores the result in `auth.json`, so it
   persists across runs and is the one option a delegated child process can
   also pick up.

> There is no `aelix auth login` **command-line** subcommand — the shipped
> `aelix` console script has no `auth` verb, so OAuth login goes through `/login`
> in the TUI.

A `/login` subscription's access token expires, and aelix refreshes it before
the request that needs it. If that refresh fails because the token endpoint
answered `429`, `500`, `502`, `503`, `504`, `520` or `524` (the statuses pi
retries), or could not be reached (a dropped connection, a timeout, a proxy
that refused the connection), the turn is retried the way a provider's `502`
is: up to three retries, 2 s, 4 s and 8 s apart, shown as `Retrying…` in the
TUI and as `auto_retry_start` / `auto_retry_end` in `--mode json` and rpc, and
a delegated agent's run retries the same way. The error then reads `OAuth
refresh failed for <provider>: <cause>`, with no `/login` hint. While your
stored login exists, each retry refreshes again, and no request is sent until
one succeeds — never on an environment variable or a `models.json` key instead
of the login. If another aelix logs you out meanwhile, what the retry does
depends on when the logout lands:

- **During the wait between retries:** the retry sends nothing and ends with
  `get_api_key_and_headers failed: The auth.json entry for <provider> is an
  OAuth login that gave no key. …` (`✖ Retry failed: …` in the TUI,
  `auto_retry_end` with `success: false` in `--mode json` and rpc). That
  message still tells you to remove the entry, which the logout already did.
  The next turn uses the next key in the
  [order models-json.md lists](models-json.md#which-key-a-request-carries):
  the provider's `models.json` `apiKey`, else its environment variable.
- **While the failing refresh request is still under way:** the retry itself
  uses that next key, as a new turn would. With no such key it sends nothing
  and ends with the provider's own error (`No API key for provider:
  anthropic`). An OpenAI Codex request needs a ChatGPT login token, so it
  sends nothing either and ends with `No OAuth token for openai-codex — run
  /login …` (or, with a `models.json` key that is not such a token,
  `openai-codex access token is missing the chatgpt_account_id claim …`).

A refresh the
endpoint refused (`400`, `401`, `403`, such as `invalid_grant` for a revoked
login), and any other status such as `501`, is not retried: the turn fails at
once with `get_api_key_and_headers failed: OAuth refresh failed for
<provider>: … Run /login to sign in to <provider> again.` The one exception
is a `2xx` whose body is cut short or times out: it is retried as a dropped
connection (a `2xx` whose encoding is broken fails at once). A non-`2xx`
answer is decided by its status once it arrives, whatever then happens to its body (a
broken encoding, a body cut short, a read that times out). A refusal after a
retry ends that retry with the same message (`✖ Retry failed: …` in the TUI,
`auto_retry_end` with `success: false` in `--mode json` and rpc). This covers
OpenAI Codex, Anthropic and GitHub Copilot logins (pi retries the same
refreshes;
[ADR-0251](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0251-own-api-key-before-environment-and-composed-launch-model.md)
§4, §12). For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.

When an OAuth sign-in or a token refresh fails on an HTTP error answer, the
error keeps the status code and quotes what the provider's server answered: at
most 512 characters per quoted string, with terminal control characters and
line breaks removed, runs of blank space shortened to one, and leading or
trailing blank space not counted. A 200 answer that is not JSON (a captive
portal) carries no status: Anthropic's error says it returned invalid JSON and
quotes the body, OpenAI Codex's is the JSON parser's own message. The bound is
per quoted string, not per message: a message that quotes two strings - a
Copilot device-flow error and its description, or a proxy's connection error
and its cause - carries about 1.2 KB, and the Codex and Anthropic sign-in
errors and Anthropic's refresh error, which repeat every link of a connection
error, up to about 1.8 KB; none of them repeats an exception your own code was
handling when it called them. A
proxy's or captive portal's error page, or a proxy that refuses the connection
with a status line of its own, can no longer clear your screen, switch it to
the alternate screen or write your clipboard. The same holds for a model
request: when a proxy refuses it or answers with an error page, the turn's
error quotes the proxy's words the same way for every built-in provider, except
that a line break becomes a space (an extension's own provider builds its own
message). A model request's error shows the HTTP status only where the
provider's SDK puts it in its message. The automatic retry and the
context-overflow compaction still read the whole error, so a
`context_length_exceeded` code or a `502` past the 512th character is acted on
as before. A token response that is
missing a field is described by its keys, never by the tokens it did carry.
The full body is not kept; if you need it, reproduce the request with `curl`.

## Provider environment variables

Set the variable for the provider you use:

| Provider        | Environment variable                          |
| --------------- | --------------------------------------------- |
| `anthropic`     | `ANTHROPIC_API_KEY` (or `ANTHROPIC_OAUTH_TOKEN`) |
| `openai`        | `OPENAI_API_KEY`                              |
| `openrouter`    | `OPENROUTER_API_KEY`                          |
| `google`        | `GEMINI_API_KEY`                              |
| `deepseek`      | `DEEPSEEK_API_KEY`                            |
| `groq`          | `GROQ_API_KEY`                                |
| `cerebras`      | `CEREBRAS_API_KEY`                            |
| `xai`           | `XAI_API_KEY`                                 |
| `together`      | `TOGETHER_API_KEY`                            |
| `fireworks`     | `FIREWORKS_API_KEY`                           |
| `github-copilot`| `COPILOT_GITHUB_TOKEN`                        |
| `huggingface`   | `HF_TOKEN`                                    |
| `vercel-ai-gateway` | `AI_GATEWAY_API_KEY`                      |

This is the most common subset. Other supported providers include `zai`
(`ZAI_API_KEY`), `minimax` (`MINIMAX_API_KEY`), `moonshotai`
(`MOONSHOT_API_KEY`), `cloudflare-workers-ai` (`CLOUDFLARE_API_KEY`), and the
Xiaomi token-plan providers. The authoritative map lives in
`packages/aelix-ai/src/aelix_ai/providers/_env_api_keys.py`.

## Adapter coverage — what this build can actually run

The bundled model catalog spans nine wire protocols; this build ships adapters
for six of them. A provider is usable only if its protocol has an adapter.

| Wire protocol (`api`)     | Adapter | Status | Notes |
| ------------------------- | ------- | ------ | ----- |
| `openai-completions`      | ✅ | verified | Also carries `openrouter`, `cloudflare-workers-ai`, `groq`, `deepseek`, `xai`, … |
| `anthropic-messages`      | ✅ | verified | Also carries `minimax` and the Xiaomi token-plan providers |
| `openai-responses`        | ✅ | 🧪 experimental | |
| `openai-codex-responses`  | ✅ | 🧪 experimental | ChatGPT Plus/Pro OAuth |
| `google-generative-ai`    | ✅ | 🧪 experimental | Hidden until `GEMINI_API_KEY` resolves |
| `google-vertex`           | ✅ | 🧪 experimental | Hidden until GCP auth resolves |
| `bedrock-converse-stream` | ❌ | **not supported** | `amazon-bedrock` |
| `azure-openai-responses`  | ❌ | **not supported** | `azure-openai-responses` |
| `mistral-conversations`   | ❌ | **not supported** | `mistral` |

The three unsupported protocols have no adapter in this build, so
`amazon-bedrock`, `azure-openai-responses` and `mistral` cannot run. Rather than
let them fail at the first turn, Aelix hides them: they do not appear in
`--list-models` or the `/model` picker. `MISTRAL_API_KEY` and
`AZURE_OPENAI_API_KEY` are still listed in `_env_api_keys.py`, but there is
nothing for them to authenticate. Mistral and Azure models reached **through** a
supported gateway — `openrouter`, say — work normally, because those ride
`openai-completions`.

`github-copilot` is the one provider that spans three protocols
(`openai-completions`, `anthropic-messages`, `openai-responses`) and routes
per-model, which is why its models behave differently from one another.

To reproduce this table, enumerate the registered protocols after
`runtime_bootstrap.register_providers()` and compare against the `api` field in
`packages/aelix-ai/src/aelix_ai/models_generated.json`.

For `anthropic`, an `ANTHROPIC_OAUTH_TOKEN` takes precedence over a static
`ANTHROPIC_API_KEY` when both are set.
With neither set, the TUI and `--mode rpc` send an exported
`ANTHROPIC_AUTH_TOKEN` as `Authorization: Bearer` to the `anthropic` provider,
as pi does (`-p` and `--mode json` do not count it as a key yet and stop with
"No API key found").

Each variable belongs to its provider only. `ANTHROPIC_API_KEY`,
`ANTHROPIC_OAUTH_TOKEN` and `ANTHROPIC_AUTH_TOKEN` go to `anthropic` and to no
other provider on the Anthropic API — not `fireworks`, `minimax`,
`vercel-ai-gateway` or a `models.json` gateway with `"api":
"anthropic-messages"`. A request to a provider with no key of its own stops
before anything is sent, with `No API key for provider: <provider>`
([#374](https://github.com/handochan/aelix-ai/issues/374)). The Anthropic SDK's
own credential sources (`ANTHROPIC_PROFILE`, workload identity federation) are
not used, and the headers it reads from `ANTHROPIC_CUSTOM_HEADERS` are sent to
the provider named `anthropic` only (a `models.json` override of it included).
For that provider an auth header in the variable (`X-Api-Key`, `Authorization`
or `cf-aig-authorization`, any letter case, with a non-blank value) is enough on
its own, so a proxy you authenticate that way works in the TUI and
`--mode rpc`; `-p` and `--mode json` do not read the variable and still ask for
a key ("No API key found"), as before.

## When no key is found

If you run `aelix` without a usable key (and without selecting a model), it
prints guidance pointing you at the relevant `<PROVIDER>_API_KEY` variable and
the `--model` flag. In the non-interactive modes (`--print` / `--mode json`)
this is a hard error with a non-zero exit code, so scripts fail fast instead of
hanging.

## Reasoning / thinking level

Models that support extended reasoning accept a thinking level:

```bash
aelix --thinking medium --model anthropic/claude-sonnet-4-6 "..."
```

Valid levels: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`. Inside the TUI,
`/thinking` opens a picker over the levels the current model supports
(`/settings` → **Thinking level** cycles them instead).

Not every model calls those levels by those names, and not every model supports
all of them. Where the two differ, the picker rows, the confirmation line and the
🧠 statusline segment show both: `xhigh (max)` on a model whose catalog entry maps
`xhigh` to `max`, `xhigh (high)` on one that has no `xhigh` and is clamped down to
`high`, `low (high)` on one that supports neither `low` nor `medium` and is
clamped up. **The value in parentheses is the tier the request carries**; a level
shown as one bare word is one the model calls by the same name. `off` is always
bare — it is folded into "no reasoning requested" before a provider adapter sees
it, so there is no tier to name.

One place that reading is currently too generous: on the thirteen non-reasoning
Google entries (Gemini 1.5 / 2.0, Gemma, and the Vertex-hosted Llamas) the Google
adapter turns that disabled level back into a thinking request, so a `high (off)`
there understates what actually goes out. That is an adapter defect, tracked as
[#256](https://github.com/handochan/aelix-ai/issues/256).

The level is recorded in the session and restored when you come back to it —
`--continue`, `--resume`, `--session`, `--fork` and the in-session `/resume`.
It is clamped to whatever the resumed model can do, so a session left at `xhigh`
opens at `high` on a model that tops out there rather than falling back to `off`.
An explicit `off` is a decision and is restored as `off`. `--thinking` (and an
agent profile's `thinking:`) outranks the session at launch, and is itself
recorded into a session that has not chosen a level yet — so the next
`--continue` comes back at it. The session in turn outranks the
`defaultThinkingLevel` setting. One exception to the ladder: after an in-session
`/resume` the target session's own recorded level wins, launch flag included —
you asked for that session, not for a re-run of the command line.

What `xhigh` means depends on how the model thinks. On a model that takes a
Claude-style token budget it sends a larger budget than `high` — 32768 against
16384 — except where the model's output cap is tighter than the budget, in
which case the budget shrinks to leave 1024 tokens for the answer and the two
levels can come out equal (`openai/gpt-5.2-chat` and `openai/gpt-5.3-chat`,
output cap 16384, are the shipped examples). Equal is as close as they get: a
higher level never sends a *smaller* budget than a lower one, whatever you
override `maxTokens` to. On an adaptive model — Opus 4.6 and everything after
it (4.7, 4.8, Opus 5, Sonnet 5, Fable 5), plus any row the catalog marks
`compat.forceAdaptiveThinking` — it selects the model's own top effort rather
than a token count. On Gemini 2.x there is nothing above `high`: that level is
already the API's `thinkingBudget` ceiling.

A model that does not list `xhigh` gets `high` instead — `--thinking` accepts
the level on any model, and each provider clamps it to what the model offers.

Override the `maxTokens` of a Claude-style row that thinks with a *budget*
below 2048 and thinking is **off** for it, whatever level you pick: the API
wants a thinking budget of at least 1024 that is still smaller than the
request, and below 2048 no such number also leaves the answer its 1024 tokens.
An adaptive row is not affected — it carries no budget for the cap to squeeze,
so the override bounds the answer and nothing else.

Two adaptive rows cannot be asked to stop thinking at all: `claude-fable-5`
and `claude-fable-5-1` reject the "thinking off" request outright (measured
2026-09-09), so `off` there buys the least thinking they offer and asks the API
not to show it. `anthropic/claude-sonnet-5` behaves the same way because its
catalog row says it has no `off` level — which is also why the picker does not
offer you one.

## Offline

`--offline` (equivalent to `PI_OFFLINE=1` or `AELIX_OFFLINE=1`) skips the
network operations aelix starts on its own:

- the `rg` / `fd` binary auto-download. Measured with `PI_OFFLINE=1` and neither
  binary on `PATH`: `ensure_tool("rg")` printed
  `ripgrep not found. Offline mode enabled, skipping download.` and returned
  nothing, instead of fetching a release archive from GitHub.
- the extension catalog fetch and index-less pip installs. Note the spelling
  here: subcommands are routed before the flag parser runs, so
  `aelix --offline extension …` does **not** reach them. Use the subcommand's
  own flag (`aelix extension discover --refresh --offline`) or export
  `PI_OFFLINE=1` / `AELIX_OFFLINE=1`. With it on, a catalog on a network transport is skipped with a
  per-source notice, and a bare `aelix extension install <pkg>` with no
  `--index-url` is refused rather than silently reaching PyPI.
- the once-a-day update check.

The two variable names are the same switch, and every one of the operations
above reads them the same way: `1`, `true` or `yes` (any case) turns offline
mode on; `0`, `false`, `no`, `off` or an empty value leaves it off; any other
value turns it **on**, so a misspelt value fails closed. `aelix` exports
`PI_OFFLINE=1` when either is on — before it dispatches any subcommand, and for
`aelix extension …`'s own `--offline` too — so a delegated agent, a `bash`
command, an extension's subprocess and the installer `aelix extension install`
starts all see it. Until #288 the `rg`/`fd` download read `PI_OFFLINE` alone:
with only `AELIX_OFFLINE=1` set, the first `find` still went to
`api.github.com`.

A project's `.env` cannot switch it: both names are refused there unless you
name them yourself in `AELIX_DOTENV_ALLOW`, and a `.env` never overrides a value
you exported.

It does **not** touch provider/LLM calls — those are the request you made, not
something aelix decided to do. A turn still goes to the network under
`--offline`. The same goes for the MCP servers you configured, remote `http`
and `sse` ones included: they still connect under `--offline`, because you
named them.

This section used to say `--offline` was "currently a no-op reserved for forward
compatibility". That was wrong on both halves.
