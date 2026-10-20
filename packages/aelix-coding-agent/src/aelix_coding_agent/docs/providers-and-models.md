# Providers and Models

Status: Accepted

How to pick a model and switch between them. Giving `aelix` a provider key, and
the table of each provider's key environment variable, are in
[credentials.md](credentials.md). For adding **custom** providers/models
(custom base URLs, header packs, per-model overrides) see
[models-json.md](models-json.md).

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
of your own. RPC's `cycle_model` (`aelix --mode rpc`, or a program that runs
RPC mode with a model registry) rotates only through models the rule leaves;
`set_model` names its provider, so a `.env` key may authenticate it, as with
`--provider`.

### A resumed session keeps its model

Opening a session that already has a conversation — `--continue`, `--resume`,
`--session`, `--fork`, and inside a session `/resume`, `/fork`, `/clone`,
`/import` and `/reload` (RPC: `switch_session`, `fork`, `clone`) — puts you back
on the model it last ran on: whichever the session recorded last, a `/model`
pick or the model that wrote an answer. A pick is recorded when you make it, so
`/model` then quitting comes back on that model. A new session records the model
it starts on as well, as pi does — at launch, on `/new`, and on a `/fork` before
the first message — when it is created, before the extensions' `session_start`
handlers run, so a session whose answers all failed before reaching a model (an
extension hook that raised, or a turn a handler started) still comes back on it,
also when the key was added after launch. A launch whose route is decided only
after `session_start` (an id sent to OpenRouter as written), and a `/new` or
`/fork` in such a session, records once that is decided — after any message
written by then, and not at all when one of them already names its model; a
held model records nothing, and reopening a session that has a
conversation adds no record. Your settings `defaultModel`
does not override the session; it is for new sessions (`/new` starts on the
launch model). `--model` / `--provider`, an agent profile that names a `model:`
or `provider:`, or `--api-key` (typed for the launch model) win over the
session, as in pi. Switching identity inside the session does not move you off
it: `/agents use` of a profile none of whose `model:` / `provider:` applies (it
names neither, or your `--model` / `--provider` override each one it names), or
`/agents use --none`, keeps the model you are on — the session's, a `/model`
pick, or the launch model — and records no model change, so the next reopen
comes back on the same model. A `provider:` that applies is a route of its own:
a profile whose `model:` your `--model` overrides but whose `provider:` applies
switches to your `--model` on that provider. Only such a use that replaces a
profile whose own `model:` or `provider:` applied (`--agent` at launch, or an
earlier `/agents use`) re-derives the model from your launch flags or settings
default; with neither there is nothing to re-derive, and that profile's model
stays.
A thinking level the profile or `--thinking` names is still applied, and that
change is recorded.

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

The same post-input check now refuses a known adapter with no configured
authentication source before a new user message or turn event is recorded
(#416). TUI, RPC, `-p` and json share it; `InputHandled` still needs no auth.
The check uses the actual model and the request's header merge: accepted
provider/model/registration auth headers, Anthropic's own `ANTHROPIC_AUTH_TOKEN`
and `ANTHROPIC_CUSTOM_HEADERS`, and Vertex keys or ADC configuration count.
Header-only and ADC sessions restore on that actual candidate too.

Configured does not mean valid. Stored entries and credential commands are
resolved at request time, preserving OAuth retry and stored-provider ownership.
An OAuth registration alone is login capability; a fallback resolver alone is
route selection capability. Neither supplies a request credential. Unknown
custom stream requirements are deferred. Vertex with project and location
defers ambient ADC discovery to the SDK, including metadata/WIF; if actual ADC
is absent or invalid, that accepted prompt can still be recorded before the SDK
fails. The check never refreshes, executes a credential command or discovers
ADC. The session's thinking level is restored against its selected model.

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

A `--model` (with or without `--provider`) that does not resolve — not found,
ambiguous, or naming a provider nobody registered (`--provider nope --model
x`) — stops `-p`, `--mode json` and `--mode rpc` with that error on stderr and
exit 1 before any request, as in pi. pi exits in the interactive mode too;
aelix starts it with the error as a Warning and waits for `/model`. A saved
`defaultProvider`/`defaultModel` (pair or alone) or an agent profile's
`model:` that does not resolve stops `-p` and `--mode json` the same way, but
`--mode rpc` starts on it and refuses each prompt until `set_model` or
`switch_session` moves the session (pi falls back from a saved default to an
available model) — unless `--provider` is typed beside the profile: then RPC
exits 1 too.

An extension's explicit `models` map replaces the named provider's builtin and
models.json rows in every selection surface, including `/model`; `{}` offers
none and `None` leaves the model list intact. Explicitly named unknown ids may
backfill from the owner's models when their API and nonempty host agree. A
previously selected Model on an old route or with old Model headers is refused
before credentials are resolved; select the current model again. Delegated
children must load the same explicit owner and route, including builtin names.
Request/payload hooks that change the registration or selected route/headers
also cancel that pending request before the SDK sends. Metadata changes alone
do not cancel it. The same closing check covers compaction and branch summaries.
See [extension-providers.md](extension-providers.md#custom-providers-with-their-own-login-method).
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
of a profile none of whose `model:` / `provider:` applies (it names neither, or your
flags override each one it names; or `--none`) when it replaces a profile whose own
`model:` or `provider:` applied — an earlier `/agents use`, or `--agent` at launch,
also a launch that went elsewhere, such as `--agent` with `provider: openrouter` and
`--model <name>/<id>` (any other such use keeps the model you are on, and on the
held placeholder says why again) — and the model picked for you after `/login`,
which picks as it would with no saved default and never that provider.
For a refused launch nothing is sent in the meantime, whatever keys you hold:
while the extensions' `session_start` handlers run, a launch model no registered
provider has claimed is not runnable yet, and no turn starts at all — not even
one a handler triggers after setting a model of its own with `set_model` — so a
handler's `trigger_turn` then ends as a refused turn (nothing sent; its message
stays in the conversation and goes out with the next prompt that is sent), a
`model_select` handler cannot move the session off the hold — nor send a turn
while aelix puts the session on hold, at launch, on a rebuild or at `/agents use`
(that turn is refused too, the same way) —
and an `--api-key` is attached only after that decision — typed with a refused
model it is not used at all, and no provider holds it. The same holds while a
rebuild's `session_start` handlers run in a session that is held. Once the session is running the provider is there,
so `/model <name>/<id>` switches to it like any other, and so does `/agents use`
of a profile whose own `model:` or `provider:` names it (`provider:` alone names
the route too). A `/model` choice is recorded in the session, so a rebuild that
restores the session's model (`/reload`, `/fork`, `/clone`, a `/resume` back to
it) comes back on it and is not held; `/new`, and a rebuild that does not
restore (as above), re-derive the launch model and hold again (pi keeps the
session's model). A profile's choice lasts until an `/agents use` of a profile
none of whose `model:` / `provider:` applies, or `--none`. `/model` also saves
it as your default, so the next launch without `--model` reads it from
`settings.json` and is refused the same way. The RPC mode
starts held too, with the same warning ending `send set_model to select a
model.`: RPC's `set_model` leaves the hold as `/model` does
(`get_available_models` lists what it can pick; `cycle_model` leaves it only
with two or more models available, as in pi). Register providers
in the extension's `setup()` and they work at launch. A provider the handler
registers and then unregisters is simply unknown.

The same rules apply to an agent profile's `model:`, to a hand-written
`defaultModel` in `settings.json`, and to delegated children — which are told
`--provider` too wherever they could not reach the parent's route alone (a
provider you defined, which a child without your extensions does not have). A
route only your `--api-key` would decide is left to the child, which never
receives the key.

## Providing an API key

Where a key can come from (an environment variable, `--api-key`, `models.json`
or `/login`), each provider's environment variable, and what happens when no
key is found are in [credentials.md](credentials.md).

## Automatic retry

When a model request fails on a cause that may pass - the provider is
overloaded or rate-limits you (`429`), a `500`, `502`, `503`, `504`, `520` or
`524` (the statuses pi retries), a dropped or refused connection, a timeout -
aelix waits and sends the turn again (`520` and `524` since #414). As in pi,
a status counts wherever its digits appear in the error text, so an error that
only contains them - `max_tokens: 25000` (its `500`), a message index such as
`messages.520`, a request id - is retried too, until the retries run out. The TUI
shows `Retrying (N/M) in Xs… Esc to cancel`; `--mode json` and rpc emit
`auto_retry_start` (`attempt`, `max_attempts`, `delay_ms`, `error_message`) and
`auto_retry_end` (`success`, `attempt`, `final_error`). The keys are snake_case;
pi's same events spell them `maxAttempts`, `delayMs`, `errorMessage` and
`finalError`. A context overflow is not retried this way: it is compacted
instead.

The `retry` block of `settings.json` (`~/.aelix/agent/settings.json`; a trusted
project's `.aelix/settings.json` overrides it key by key) sets how, with pi's
keys and defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `retry.enabled` | `true` | `false` ends the turn on the first failure. |
| `retry.maxRetries` | `3` | Retries after the first attempt; `0` sends once. |
| `retry.baseDelayMs` | `2000` | The first wait in milliseconds; each later one doubles. |
| `retry.maxAgentDelayMs` | `60000` | The longest single wait. |

```json
{ "retry": { "maxRetries": 5, "baseDelayMs": 1000, "maxAgentDelayMs": 10000 } }
```

waits 1 s, 2 s, 4 s, 8 s and 10 s. The values are read each time a retry is
decided, so a `/reload` after editing the file applies them to the next retry.
The TUI, `-p`, `--mode json` and `--mode rpc` use them. A delegated agent's
run uses the global file's values: the child is started with `--no-approve`
whenever the project has a `.aelix/settings.json`, so a trusted project's
`retry` block does not reach it.

A value of the wrong type is ignored. In the global file, a string such as
`"5"`, `null`, a list or a `retry` that is not an object leaves the default in
force. In a trusted project's file, `null` is skipped when the two files are
merged, so the global value applies (pi skips only a missing key, and its
`?? 3` then gives the default); any other wrong-typed project value replaces the
global one and the default applies, and a project `"retry": false` replaces
the whole global block. A negative number counts as `0`. Each wait is rounded to
whole milliseconds, half to even: `"baseDelayMs": 1.5` waits 2, 3 and 6 ms. An
`abort`, an `abort_retry` or an extension's `ctx.abort()` ends the wait at once,
however long it is, and the turn is not sent again; with a wait of `0`, one made
while `auto_retry_start` is delivered to a subscriber still cancels the retry.
The TUI's countdown rounds up to whole seconds, as pi's does: a 500 ms wait
shows `in 1s`.

rpc `set_auto_retry` sets `retry.enabled` in the global `settings.json`, as pi
does, so it outlasts the session: `set_auto_retry false` sent for one rpc
session also turns the retry off for later TUI, `-p` and rpc runs, until
something sets it back. `get_state`'s `autoRetryEnabled` reports the value in
force. A project `.aelix/settings.json` that sets `retry.enabled` itself still
wins over it, in pi as here. The harness's own toggle is set as well, so
`autoRetryEnabled` and the next turn follow `set_auto_retry` even when a
trusted project's `"retry": false` hides the global block. A global `retry`
that is not an object (`"retry": false`) is replaced by `{"enabled": ...}`.
The file is written in the background after the answer. A write that fails
is not reported: rpc still answers `success: true`, as pi does, and the
setting holds for that session only - the next run reads the old value. A
global `settings.json` that aelix cannot open read-write when it starts is
reported once, at startup, as `Warning: settings (global): ...`; that file is
then neither read nor written, so every retry key takes its default. Only an error raised before the write is
queued answers `success: false`, and it leaves the retry as it was.

Until this release none of these keys was read: every retry ran three times at
2 s, 4 s and 8 s, and `"enabled": false` did not stop it (#408). The
`aelix-server` WebSocket bridge has no settings file and keeps those defaults.
`retry.provider.*` (the provider SDK's own retries and timeout) is still not
read.

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
`--continue` comes back at it (`--thinking off` is not recorded). The session in
turn outranks the `defaultThinkingLevel` setting. One exception to the ladder: after an in-session
`/resume` the target session's own recorded level wins, launch flag included —
you asked for that session, not for a re-run of the command line.

`defaultThinkingLevel` applies in every mode, a delegated agent included (it
reads only your global `settings.json`), when neither `--thinking` nor the
session gives a level; unset, the level is `off`. It is fitted to the model:
`xhigh` runs at `high` on a model that tops out there, a model that cannot
reason runs at `off`, and `off` runs at the lowest level on one that cannot
switch reasoning off. Only the TUI records it, and only as set (not `off`, not
fitted), so a `-p`, `--mode json` or `--mode rpc` session takes the default of
its next launch. A `session_start` extension handler sees the level: one it sets
is recorded only if it changes it, and a model it switches to keeps it, as
`/model` does. `/new` keeps the level and records it unless `off`; `/reload`
goes back to `--thinking`'s level or `off`.

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
- the extension catalog fetch and index-less pip installs. Use
  `aelix --offline extension …`, the subcommand's own flag
  (`aelix extension discover --refresh --offline`), or export
  `PI_OFFLINE=1` / `AELIX_OFFLINE=1`. With it on, a catalog on a network transport is skipped with a
  per-source notice, and a bare `aelix extension install <pkg>` with no
  `--index-url` is refused rather than silently reaching PyPI.
- the once-a-day update check.

Only `--offline` may precede the root commands `extension`, `docs`, and `status`.
Other launch options before a command, such as `-e policy.py status` or
`--provider anthropic --model example status`, fail with exit 2 before a
session starts. `aelix --offline docs` and `aelix --offline status` work;
`docs --offline` and `status --offline` remain usage errors. To send a command
word to the agent intentionally, use `aelix -p status` or `aelix -- status`.
An option's literal value and an already started ordinary prompt keep their
agent meaning; an unknown long option immediately followed by a command word
is rejected as ambiguous. `--custom=status` keeps its explicit extension value.
Bare `--` ends agent option parsing: subsequent tokens are messages or `@files`.

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
