# 0239. The level was never written down, so every resume started from `off`

Status: Accepted (2026-09-08) — amended 2026-10-08 (#376): decision 7 superseded, the model is restored too — amended 2026-10-14 (#413): a new session records the model it starts on — amended 2026-10-14 (#412): a route-less `/agents use` (no `model:`/`provider:` of the profile applies, or `--none`) keeps the restored model and writes no model record — amended 2026-10-14 (#286, narrowed 2026-10-15): every mode applies `defaultThinkingLevel`, clamped to the model; what a launch records is unchanged but where a `session_start` handler sets the level or the model; decision 5 retired — amended 2026-10-22 (#415): RPC `set_model` and `cycle_model` record the pick, fire `model_select` and fit the level to the model
Date: 2026-09-08
Superseded by: [ADR-0263](0263-configured-request-auth-before-new-prompt-records.md)
(2026-10-12, #416: decision 5's header/token/ADC limit and decision 6's
credential deferral for definite source absence; historical #376 evidence below).
Supersedes/relates: ADR-0196 (its D6.3 thinking-level ladder, **amended** here
with a third rung), ADR-0135 (the thinking level as harness *state* — untouched;
this ADR is about the *file*), ADR-0235 (Pi is a reference, not a target, so the
three divergences below owe no argument).
Issue: #198.
Design spec: `.omc/specs/198-design-2026-09-08.md`.

`/thinking high`, `/quit`, `aelix --continue` — and you are back at `off`. Two
independent faults produced that, and either one alone was enough to reproduce
it.

## What was actually broken, measured on `main` 0985fcf

**Half 1 — an idle change was never written down.**
`AgentHarness.set_thinking_level` mutated `_state.thinking_level` and queued a
`PendingThinkingLevelChangeWrite` **only when `self._phase == "turn"`**; the
flush dispatcher was the only caller of `Session.append_thinking_level_change`
in the repo. Measured with a harness at phase `idle`:

```
state.thinking_level: high
entry types after idle set_thinking_level: []
build_context.thinking_level: off
```

Every caller a user actually reaches is idle: the `/thinking` picker, the
`/settings` row, the startup settings seed, and `/agents use`. So the session
file held **zero** record of the level. Only `/settings` appeared to survive a
relaunch, and only because the startup seed re-applied the *global*
`defaultThinkingLevel` — not because anything about that session was remembered.

**Half 2 — a session that did carry the entry was ignored on resume.**
`build_session_context` folds `thinking_level_change` last-wins into
`SessionContext.thinking_level`, and neither seam that builds a harness from a
resumed session read it. Startup (`--resume`/`--continue`/`--session`/`--fork`)
seeded `state.messages` only (#122's `_seed_startup_messages`); in-session
`/resume`, `/fork` and `/new` rebuilt `_state.messages` only. Measured: a session
carrying `thinking_level_change("high")` reported
`session context -> high 1 msgs` while the harness built over it reported
`harness after build -> off`.

The in-session half is the worse one: it rebuilds through the harness factory, so
`/resume` discarded the level set *in that very process*. Pi has no such half —
its `AgentSession` survives `switchSession` (`agent-session-runtime.ts:256` at
`pi@da840b6`), so `agent.state.thinkingLevel` carries over for free.

## Decision

**The session transcript is the record of the level.**

1. **`set_thinking_level` appends when it is not in a turn**, and only when the
   level actually changes (Pi's `isChanging`). The in-turn pending-write deferral
   is untouched, so a mid-turn change still lands after the turn's messages.
2. **The append runs after the `thinking_level_select` emit**, so a level an
   extension refuses leaves no entry. `/agents use` rolls a refused level back by
   writing `AgentState` directly, which cannot undo a session append.
3. **Both resume seams restore it** through one pure helper,
   `resolve_resumed_thinking_level(path_entries, model, *, fallback)`. Ladder:
   `--thinking`/profile > the session's entry > (unchanged) the settings-default
   seed. Both seams pass the live model, so a level the resumed model cannot do
   is **clamped**, never dropped: `xhigh` on a `high`-max model resumes at
   `high`. That ladder holds **at launch**. At the in-session seam it inverts by
   construction: `/resume` rebuilds the harness through a factory that closes
   over `parsed`, so the new harness starts at `--thinking`'s level and the
   restore then overwrites it with the target session's own. Kept, because
   `/resume <id>` is a request for *that session*, not a re-run of the command
   line; documented in `docs/guides/providers-and-models.md`.
4. **An explicit `off` restores as `off`.** An entry is a decision, not an
   absence. `build_session_context` cannot express this — its initial value *is*
   `"off"` — which is why the helper exists alongside it and returns `None` for
   "nothing recorded".
5. **The startup settings seed is told, not left to guess.** `run_tui` takes a
   derived `thinking_level_restored: bool`. ADR-0196 D6.3's guard sniffs
   `state.thinking_level` for "unset", and a restored explicit `off` is
   indistinguishable from unset by value. Without the signal, decision 1 turns
   that seed into a *writer* and overwrites a recorded `off` in the file on first
   launch — data that survives today. This amends D6.3; it does not reverse its
   "`parsed` is not threaded into `run_tui`" clause, since a derived boolean is
   not `parsed`.
6. **Both seams carry the live/flag level forward** when the target session has
   no entry of its own — in-session this is Pi's implicit behaviour, since Pi
   never rebuilds — **and record it** when it is not the kernel default `off`.
   Without the append, `/new` at `medium` produces a session whose file says
   `off` and the headline bug reproduces the next time that session is opened.
   The startup seam does the same for `cli_level`: `--thinking` is the *only*
   level source that would otherwise never be written down, so
   `aelix --thinking high` then `--continue` came back at `off` while the weaker
   `defaultThinkingLevel` — which reaches the harness through
   `set_thinking_level`, i.e. through decision 1's append — survived. A session
   that already recorded a level is not overwritten by the flag; the flag wins
   for that process only.
7. **The model is deliberately NOT restored here.** Same bug class, bigger
   radius: `set_model` has the identical turn-phase gate, and the restore belongs
   in `_build_harness_options`' provider ladder rather than a post-build
   assignment — Pi additionally requires configured auth and emits "could not
   restore model", and the #98 unrunnable-startup gate judges the model after the
   build. **Ordering note for that follow-up:** restore the model *before*
   clamping the level, or the clamp validates against the wrong model.

Rejected: *restoring inside `_build_harness_options`* — one call site for both
seams, but it cannot see the pre-swap live level (6), it also fires on `/reload`,
and it moves a kernel-shaped concern into the repo's most tangled precedence
ladder. *Calling `harness.set_thinking_level(level)` to restore* — it would fire
`thinking_level_select` at extensions for something the user did not do; both
seams assign state directly, exactly as they already do for `state.messages`.
*Gating the hook emit on "changed" too (full Pi parity)* — out-of-scope churn;
`tests/pi_parity/test_setter_emit_sites_match_pi.py` and
`tests/test_harness_setters.py` pin the unconditional emit. Only the **write** is
change-gated.

## Divergences from Pi (ADR-0235: recorded, not argued)

- **Layering.** Pi appends from its product layer
  (`coding-agent/src/core/agent-session.ts:1793-1815`); its harness
  (`agent/src/harness/agent-harness.ts:576`) only declares the setter. Aelix
  appends in the harness, where the session handle already lives.
- **Raw vs clamped.** Pi appends the *clamped* level. Aelix appends the raw one
  — this setter does not clamp today, and making it clamp is a second behaviour
  change. The restore clamps instead.
- **No `messages.length > 0` conjunct.** Pi honours a session's level only when
  the session also has messages (`sdk.ts:191,232`). Aelix restores on the entry's
  presence alone: a level set before the first prompt is still a decision.

## Consequences

- The session file grows one `thinking_level_change` per level change; per `/new`
  or `/resume` into a session with no level of its own; one the first time a
  session with no level of its own is launched under `--thinking`; and one the
  first time a session with no level of its own is opened while
  `defaultThinkingLevel` is set.
  That last one is bounded to a single entry, after which **that session
  out-ranks a later change to the global default** — accepted as "the level this
  session ran at".
- **A cancelled `/agents use` can still leave one entry.** Its `except
  BaseException` rollback writes `AgentState` directly. Decision 2 removes the
  reachable case (a refusing extension hook); a `CancelledError` landing between
  the emit and the append still leaves an entry for a rolled-back level.
  Last-wins recovers it on any later change, and a compensating append in the
  rollback would race the other way. Recorded, not guarded.
- **`ExtensionAPI.setThinkingLevel` can now fail asynchronously.** Decision 1
  puts file I/O on the idle path, and the extension binding fires the setter as
  a pinned fire-and-forget task. `_pin_task` therefore retrieves and `debug`-logs
  the task's exception instead of leaving asyncio to report
  `Task exception was never retrieved` at GC time with no caller in the
  traceback. All pinned extension-action tasks get that, not only this one.
- **`/reload` is a third rebuild seam and is untouched.**
  `AgentSessionRuntime.reload` rebuilds through `_teardown_current` + `_apply`,
  not `_finish_session_replacement`, so it still drops the level. Cheap to fix
  now that the helper exists; out of scope for #198.
- The write fires for any non-`turn` phase, including `compaction` and
  `branch_summary`. A level change cannot realistically be issued from there and
  the entry would be harmless, so it is not guarded.
- Restoration is not TUI-only: the boolean is computed upstream of the mode
  dispatch, so `--print`, `--mode json` and RPC resume at the recorded level too.

## Amendment (2026-10-08, #376) — the model is restored too

Decision 7 left the model to a follow-up. #376 is that follow-up, and it was
bigger than "not restored": a session reopened without model flags was not
merely on the wrong model, it could be on **no** model.

**Measured on `main` `8f7d98aa`** (fake keys, `models.json` pointing anthropic at
a local recorder; driver and outputs in `.omc/probes/376-live/impl/`):

- `aelix --mode rpc --continue` with no settings default: `get_state` model
  `''/''`, `api='unknown'`; a prompt failed `No provider registered for
  api='unknown'` and its message was still written (`messageCount` 4 → 5); RPC
  `compact` failed the same way. This is the issue as filed (TUI `/compact`,
  `navigate_tree(summarize=True)` too).
- With settings `defaultModel=claude-sonnet-4-5`, a session that ran on
  `claude-haiku-4-5` resumed on sonnet in every mode: RPC, `-p`, `--mode json`,
  the TUI (its `/compact` was sent as sonnet), the in-session `/resume`, and RPC
  `switch_session`.
- The cause had three parts. `core/model_resolver.py::restore_model_from_session`
  had no caller. The model a session ran on was written down nowhere it was read:
  aelix never writes a `model_change` for a session's launch model (pi does, for
  every new session, `core/sdk.ts:456` at `pi@1cedd3272`), so a session that
  only ever ran on its `--model` held **zero** `model_change` entries — the
  only record was the `provider`/`model` every adapter writes on each response,
  which nothing read. And the one `model_change` writer the harness has, an
  in-turn `set_model`, wrote `model.api` (`anthropic-messages`) into the
  `provider` field; an idle one (every `/model` pick) wrote nothing.

### Decision

1. **The session's model is restored when its record can run and the user named
   no model.** pi's restore (`core/sdk.ts:217-251`): the branch's selection is
   taken when `--model` resolved nothing; otherwise `findInitialModel`. Here
   "named" is `--model`/`--provider` on the command line or an agent profile's
   `model:`/`provider:` (`--agent`, or the live `/agents use` pick — ADR-0196's
   profile > settings), and `--api-key`: it is typed for the launch model, and pi
   refuses it without `--model` (`main.ts:827-834`), so there it never meets a
   restore. A settings `defaultModel` and `OPENROUTER_DEFAULT_MODEL`
   are defaults, and lose to the session as pi's settings default does. "Has
   history" is pi's `messages.length > 0` (`sdk.ts:210-211`), so `/new` keeps the
   launch inputs.
2. **The record is pi's `getBranchSelection`** (`core/virtual-models.ts:128-145`):
   walking back from the leaf, the first `model_change` or the first assistant
   message naming its provider and model. One kernel helper,
   `session.context.resolve_resumed_model`. aelix has no virtual models, so pi's
   one exception does not apply. An assistant message without both names (a turn
   refused before any request) is skipped, so a failed turn does not erase the
   record before it.
3. **It is restored where decision 7 said, in `_build_harness_options`' resolve**,
   after the extensions' `setup()` registrations are bound and before the
   thinking-level seed clamps against the model (the ordering note). One site
   covers every way a harness is built on a session: the startup build
   (`--continue`, `--resume`, `--session`, `--fork`), `/resume`, `/fork`,
   `/import`, RPC `switch_session` and `/reload`. `/reload` is a deliberate
   inclusion, and a divergence: pi's reload keeps the **live** model; aelix's
   rebuild restores the session's **record** when decision 1 lets it (it used to
   re-derive the launch model). Since every pick is recorded (decision 8) the
   two agree whenever the rebuild restores and the live model was picked or has
   answered; where the live model was never recorded — a model set on a
   read-only session, under the CLI's turn gate, or a placeholder — `/reload`
   goes back to the record. And where decision 1 does not restore, `/reload`
   (like every other rebuild) re-derives the launch inputs as before #376 and
   drops a recorded `/model` pick, where pi keeps the live model: when the user
   named a model at launch (`--model`, `--provider`, `--api-key`, a profile's
   `model:`/`provider:` — measured in review round 2: `--continue --provider
   anthropic --model claude-haiku-4-5`, `/model claude-sonnet-4-5`, `/reload`,
   and the next request went out as haiku), when the session has no
   conversation yet, and when the record cannot run (decision 5's fallback).
4. **What "can run" means** (`runtime_bootstrap.restore_session_route`). The
   record is an exact provider and id, so none of the launch's inference runs
   (no prefix reading, no swap, no guard 2, no `defaultProvider`). A model the
   registry knows (the catalogue, `models.json`, an extension's `setup()`) comes
   back when its provider holds a credential from any source, a project `.env`
   included. That is pi's `hasConfiguredAuth`, and it is what step E does for
   `--provider`: the session's record named the route, so the `.env` key only
   authenticates it (ADR-0250). An id **no catalogue lists** comes back as a
   custom id in two cases only. On a provider the user defined (`models.json`, an
   extension) it needs a credential from any source, a `.env` included — what
   `--provider <it> --model <id>` needs at launch. On any other provider it needs
   the user's **own** credential (not a `.env`) — the condition under which the
   launch sends an uncatalogued id to OpenRouter (guard 2). So a session file
   cannot reach a route the same string typed at launch could not. pi restores
   no custom id at all. Nothing `is_runnable` refuses is restored.
5. **When the record cannot run, the fallback is what the launch would have
   chosen without the session** (`parsed`: the flags' absence, the settings
   default), with pi's line: `Could not restore model <provider>/<id>. Using
   <provider>/<id>`. The `. Using …` half is printed only when a turn can run on
   the fallback: it has an adapter (`is_runnable`) and its provider a credential
   (the restore's own `hasConfiguredAuth` question) — pi's `findInitialModel`
   falls back only to a model with auth. The `Using` half names the model the
   run is on when the line is said, after the build's `session_start` handlers
   ran (review round 3): computed before them, a handler that moved the model
   there left the line naming a model no longer active. The runtime carries it
   (`AgentSessionRuntime.model_fallback_message`, the pi field, set on every
   build through the new `set_model_fallback_message` — at launch after
   `session_start` and the late-route decision, on a rebuild in the
   after-`session_start` seam). The
   TUI commits it under the banner at startup and after `/resume`, `/import`,
   `/fork`, `/clone` and `/reload`, as pi's `showWarning`; `-p`, `--mode json`
   and RPC print it on stderr at startup, and RPC again after `switch_session`,
   `fork` and `clone`. pi prints nothing outside interactive mode.
   **Known limit** (review round 4): "a credential" in decisions 4 and 5 is
   `ModelRegistry.has_configured_auth`, which does not count request-level auth
   — an auth header in `models.json` or a registration's `headers`,
   `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_AUTH_TOKEN`, google-vertex ADC (pi's
   `hasConfiguredAuth` counts ADC). A session whose only auth is one of those is
   not restored: it falls back to the launch inputs and says so (`Could not
   restore model …`, without a `Using` half when the fallback has the same kind
   of auth). That is safe — the run says which model it is on — and a
   follow-up issue unifies the predicate with pi's.
6. **A prompt on a model that certainly cannot run writes nothing, in every
   mode.** pi's `prompt` runs the extension input handlers
   (`_runInputHandlers`, `agent-session.ts:1993`) and then validates the model
   before recording anything (`:2032-2050`): a model at all, then
   `hasConfiguredAuth || checkAuth` for its provider. aelix asks the first half
   at the same point, once, in the harness: `AgentHarness.prompt` calls the
   question bound with `set_prompt_check` after the `input` hook and before the
   user message is built, and raises `AgentHarnessError("invalid_state",
   reason)` on an answer. Every prompt path goes through it — RPC `prompt`, the
   TUI, `-p`, `--mode json`, and an extension's `send_message(...,
   trigger_turn=True)` — so an input an extension handles itself
   (`InputHandled`) is answered without a model, and a model an `input` handler
   switched to is the one judged (review round 3: round 2 asked before the hook).
   The CLI's harness factory binds `core.runnable_models.turn_refusal` on every
   build; an embedder that binds nothing is asked nothing. It refuses **only** a
   model that certainly cannot run for a reason that is not a credential: the
   empty placeholder (no provider and no adapter — the unresolved launch's
   `''/''`, the issue as filed, answered "No model selected."), no adapter for
   its `api` whatever its base URL (the late-provider hold's `api 'unknown'`
   among them, and a catalog model such as `mistral/mistral-large-latest`,
   `api` `mistral-conversations` on `https://api.mistral.ai`), or no base URL
   (declared empty, or a `{NAME}` placeholder in it left unset — cloudflare's
   account and gateway ids). A missing provider only words the refusal; it is
   not a reason on its own. These are `is_runnable`'s arms less its one
   auth-configuration arm, google-vertex's `_vertex_config_missing`
   (`GOOGLE_CLOUD_API_KEY`, or a project and a location, read from the
   environment only); `is_runnable` itself — the TUI's #189 gate, `-p`'s #98
   gate, decision 5's restore predicate — is unchanged. Both fail open with no
   adapter registered at all: an embedder that binds the check before it
   registers its adapters is refused nothing. RPC's `prompt` answers
   once that verdict is in, as pi's does (`rpc-mode.ts:394-412`: success from
   `preflightResult`, which aelix's `prompt(..., on_accept=)` ports; the error
   when the prompt threw first): `success: false` before acceptance, and still
   before the turn's first event when accepted. The TUI prints the text and
   runs no turn (its #189 gate, which predates #376, refuses an unrunnable
   model before the hook with its own advice; the check catches the one an
   `input` handler switched to); `-p` and `--mode json` print it and exit 1
   (their #98 launch gate refuses an unrunnable launch model before any
   prompt, as on `main`).

   **No credential is asked** (owner decision 2026-10-08, review round 4).
   Round 3 also refused a model whose provider `has_configured_auth` did not
   count, and that refused request-level auth the adapters accept and that ran
   on `main` in the TUI and RPC: an auth header in `models.json` or a
   registration's `headers`, `ANTHROPIC_CUSTOM_HEADERS` on anthropic (ADR-0254
   §2.2), `ANTHROPIC_AUTH_TOKEN`, google-vertex ADC. Review round 5 took the
   Vertex arm out too: through `is_runnable` round 4 still refused, over RPC
   (the one mode with no such gate on `main`), a keyless Vertex prompt (`main`
   writes it and the adapter fails with `Vertex AI requires a project ID`) and
   a Vertex key from `--api-key`, `auth.json` or a `models.json` `apiKey` with
   no `GOOGLE_CLOUD_*` set (`main` sends it). No key, header, token or Google
   Cloud setting is asked, in any mode. A keyless prompt is
   handled exactly as on `main`: in the TUI and RPC it is accepted, written,
   and fails with the adapter's own `No API key for provider: <provider>` at
   request time; `-p` and `--mode json` keep `main`'s launch gate
   (`cli/entry.py`, `has_configured_auth` before any prompt — so there an
   `InputHandled` input on a keyless model is refused before its handler, as on
   `main`). pi's refusal of a keyless prompt before it records anything is
   **not** ported here; it needs a credential predicate that counts what the
   adapters accept, and is a follow-up issue.

   **The no-base-URL arm stays for cloudflare** (decided by the main loop in
   review round 6, not by the owner). A `cloudflare-workers-ai` model with a
   key but no `CLOUDFLARE_ACCOUNT_ID` (and `cloudflare-ai-gateway` without its
   account and gateway ids) is refused before anything is written, with the
   variable(s) to set: its base URL still carries the unexpanded `{NAME}`.
   `main` accepted and wrote that prompt and sent the request to
   `api.cloudflare.com` (`gateway.ai.cloudflare.com`) with the literal
   placeholder in its path, which cannot succeed (verify r5,
   `.omc/probes/376-live/r5verify/live-base.out` row `cf-workers-no-account`,
   `live2-base.out`). It is a question about where the request goes, not
   about its credential.
7. **`set_model` records the provider**, as pi's `appendModelChange(model.provider,
   model.id)`.
8. **An idle `set_model` records a `model_change` too** (review round 2), as #198
   did for the thinking level: pi's `setModel` appends one on every call
   (`agent-session.ts:2485`), and every pick a user makes (`/model`, its picker,
   `/agents use`, an extension's `setModel` between turns) runs idle. Without it
   `/model X` then a quit reopened on the model the last answer named — a
   regression from `8f7d98aa`, where the picker's persisted settings default
   brought X back. Not gated on a change (the live model may be a launch model
   the session never recorded). Never recorded, idle or in a turn: a model whose
   resolution named no protocol (`api` `unknown`: the late-provider hold's
   placeholder, ADR-0250 §2.11, or an unresolved route), one with no provider or
   id, and anything set while the CLI holds turns (a pending launch's or held
   rebuild's `session_start`, a hold being applied — a model a handler sets there
   is put back to the placeholder). On a read-only session (ADR-0244) the switch
   happens and nothing is recorded.

### Divergences from Pi (ADR-0235: recorded, not argued)

- **No `model_change` for the launch model.** pi writes one per new session;
  aelix reads the responses instead (decision 2), which also covers every
  session written before this amendment. *Retired 2026-10-14 by the #413
  amendment below: a new session now records it; the responses still cover
  older sessions.*
- **An uncatalogued id can come back** (decision 4); pi's `getModel` knows none.
- **The fallback is aelix's launch, not `findInitialModel`.** aelix's launch
  never auto-picks the first available model when no default is set; a resume
  that cannot restore does not start doing so. With nothing runnable the first
  half of pi's line stands alone, and the existing gates say what they say (pi
  replaces the line with `No models available`).
- **The line is printed outside the TUI too.**
- **The idle `model_change` is appended after the `model_select` emit** (pi
  appends before it), and only while the harness still holds the model: a
  handler that refuses the pick or answers it with its own `set_model` leaves
  only the model the harness ends on in the record.
- **`/reload` restores the record, pi's keeps the live model** (decision 3) —
  and where decision 1 does not restore, it re-derives the launch inputs.
- **A `trigger_turn` is asked too** (decision 6). pi's `sendMessage` with
  `triggerTurn` calls `agent.prompt` directly, past `prompt`'s validation; here
  it goes through `AgentHarness.prompt`, so a turn on a model with no adapter
  is refused and nothing is written, where pi's runs, fails and records the
  failure. A handler that awaits that turn's `agent_end` is not woken (no turn
  started). While `session_start` handlers still run after a launch that
  resolved no model, ADR-0250 §2.11's hold answers such a turn first and its
  message is written with the hold's error, as on `main`.
- **The prompt check asks no credential** (decision 6), nor Vertex's Google
  Cloud setup. pi's `prompt` refuses a model without auth before recording
  anything; aelix's keyless prompt is
  written and fails at request time in the TUI and RPC, and `-p` / `--mode
  json` refuse it at their launch gate before the input handlers, as on
  `main`. A follow-up issue.
- **The restore's credential question misses request-level auth** (decision
  5's known limit): a header, an auth token or Vertex ADC alone does not
  restore; pi's `hasConfiguredAuth` counts ADC.

### Consequences

- **ADR-0250 §2.11's rebuild hold applies to the launch inputs only.** A rebuild
  that restores its session's model did not re-resolve them, so the factory does
  not hold it, nor does the after-`session_start` seam. A `/model` pick of a late
  provider that has since answered is therefore restored on `/reload` or a
  `/resume` back to that session, as in pi, where it used to be held again. A
  launch or rebuild that falls back still goes through the hold exactly as before.
- `/agents use` of a profile that names no model still re-resolves the launch
  inputs (ADR-0196), so in a restored session it moves off the restored model.
  Unchanged here (a follow-up). *Resolved 2026-10-14 by #412 (the amendment
  below): such a use keeps the live model and writes no `model_change`.*
- `/agents use` of a profile whose model applies records it (decision 8). If the
  same `use` then fails after the switch (its thinking-level setter refused by a
  hook), the rollback restores the live model by assignment and the record keeps
  the profile's model, so a reopen tries that one.
- The decision 7 paragraph above ("The model is deliberately NOT restored here")
  is superseded by this amendment. Its ordering note is kept: the model is
  resolved in the harness build, before `_seed_startup_state` and
  `_finish_session_replacement` clamp the level against it.

## Amendment (2026-10-14, #413) — a new session records the model it starts on

The #376 divergence "no `model_change` for the launch model" left a hole that
amendment's decision 2 named: a session whose every answer carried no
provenance had no record at all. An assistant message has no `provider`/`model` when the turn
failed before any request — the harness's turn-failure message, e.g. an
extension's `context` hook that raised.

**Measured on `main` `7d163232`** (fake keys, `models.json` pointing anthropic
at a local mock; driver and outputs in `.omc/probes/413-live/impl/`):

- `-p hi`, `--mode json hi`, `--mode rpc` (`get_state`, quit) and RPC
  `new_session`, each on `--provider anthropic --model claude-haiku-4-5`: the
  session file held the header and the messages, never a `model_change`.
- The issue's case: `-p hi -e <extension whose context hook raises>` wrote a
  user message and an assistant failure with no `provider`/`model`; `aelix
  --mode rpc --continue` with settings `defaultModel=claude-sonnet-4-5` and no
  flags then reported `get_state` `anthropic/claude-sonnet-4-5` — the default,
  not the model the session started and failed on.

### Decision

1. **A session created with no conversation records the model it starts on as a
   `model_change`, when it is created** — as pi's `createAgentSession` does for
   every session without messages (`core/sdk.ts:449-459` at `pi@3daa20280`):
   before `bindExtensions` emits `session_start`, and on `newSession` before its
   `setup` runs (pi's `createRuntime` precedes `options.setup`,
   `agent-session-runtime.ts:226-262`). One CLI helper,
   `cli.entry._record_session_start_model`, called at launch before
   `create_agent_session_runtime` emits `session_start`, and for every rebuild
   from the CLI harness factory, inside the runtime's `_apply` — before `setup`,
   before the carried thinking level, before `session_start`: `/new`, `/fork`,
   `/resume`, `/import`, `/reload`; RPC `new_session`, `fork`,
   `switch_session`; an extension's `ctx.new_session` / `fork` /
   `switch_session`. "No conversation" is pi's `hasExistingSession` —
   `build_session_context(get_branch()).messages` is empty at creation — so a
   resume, `--continue`, a fork or `/clone` that keeps messages writes nothing,
   whatever model the run is on: its answers name theirs, and a `--model` for one
   run is not a pick to remember before a prompt runs on it. A `/fork` before the
   first user message (the empty branch) and `/new` record. Messages written
   after creation — a `session_start` handler's triggered turn, awaited or not,
   and `new_session`'s `setup` seed — land after the record and cannot suppress
   it.
2. **Where the model is known only after `session_start`, the record follows the
   late-route decision** (ADR-0250 §2.11): #367's pending launch route (guard 2's
   OpenRouter-as-written, an unresolved placeholder) and a rebuild — a `/new`,
   fork, `switch_session`, `/import` or `/reload` in a session launched on such
   a route —
   that resolved to one or that the factory holds for a late provider. A provider
   that `session_start` registers may still hold such a session: recorded at
   creation, a rebuild whose own `session_start` registers the provider its
   inputs name would have recorded the OpenRouter route it never runs on (verify
   round 1, B2). A pending launch waits on the hold's placeholder under the turn
   gate; a HELD rebuild gets the placeholder and the gate too, but a pending
   rebuild that is not held gets neither (the factory calls `hold_turns` only
   when it holds), so its `session_start` turn runs on the guard-2 route. Whether
   it is a new session is the flag read when it was created empty, not the
   messages at record time, so **in this case the record follows messages
   written after creation** — `setup`'s seed, a handler's `trigger_turn` (on a
   pending launch the one the turn gate refused and `_finish_refused_turns`
   wrote). Two rules keep the reopen where pi's lands, as far as those messages
   go: when any one of them names its model (an assistant message with
   `provider` and `model`, such as the answer a handoff-style `setup` seeds —
   also when a turn that failed before the request follows it), no record is
   written — pi's record precedes that answer and its reopen lands on the
   answer, which ours, written after it, would outrank (review rounds 3-4); when
   none does (a turn that failed before the request), the record is written and
   the reopen lands on it (`resolve_resumed_model` reads the latest). A
   `model_change` entry that `setup` writes is not one of those messages: see the
   known corners below. A launch the decision refuses
   (print/json) or holds, and a rebuild left on the placeholder, record nothing:
   the placeholder is never recorded.
3. **The latest record decides a second line.** A record equal to the latest one
   on the branch (`resolve_resumed_model`) is not written, so reopening an empty
   session on the same model adds nothing. Reopened on another model it records
   that one — no prompt ran on the first, and #376's decision 1 lets the launch
   inputs decide an empty session's model — and back on an earlier model it
   records it again (the latest, not any, record is compared). A
   `session_start` handler's `set_model` records itself after the start record
   (#376's decision 8), pi's order: the file then reads the launch model, then
   the handler's. On a pending launch (decision 2) the handler runs under the
   turn gate, so `AgentHarness._records_model` does not record its pick; the
   deferred start record then names the handler's model, and the file holds that
   one record — the reopen lands on the same model.
4. **A credential is not asked.** pi records at creation whatever the auth state;
   so does aelix: a resolved model is recorded with no key configured yet, so a
   session launched before `/login` whose turn is written after `/login` and
   fails before the request still reopens on it. A prompt refused for want of a
   key (ADR-0263) writes nothing, so a session holding only that refusal is
   still empty: its reopen follows the launch inputs (#376's decision 1) and
   records the model it reopens on. Only a model no prompt could ever be sent to
   is skipped:
   `AgentHarness`'s placeholder rule (`api` `unknown` or empty, no provider or
   id) and turns still held. ADR-0263 (#416) is about prompt records — a prompt
   with no configured request auth is refused before its user message is
   written — and is unchanged.
5. **A storage failure on the record is a warning.** `read_only`
   (`ReadOnlySessionStorage`, ADR-0244) is swallowed silently, as
   `AgentHarness.set_model` swallows it. A failed append (`SessionError` code
   `storage`: EACCES, ENOSPC, a read-only mount) prints `Warning: could not
   record the model this session starts on …` on stderr and the session goes on:
   before #413 such a file opened and its prompts ran, writing nothing and
   saying nothing (`-p hi` and an RPC prompt on a 0444 file at `7d163232`: rc 0,
   file unchanged, stderr empty; only `--thinking` failed on it, at launch with a
   `SessionError` traceback, then as now — `.omc/probes/413-live/r3/rokit/`), and
   a rebuild is already live on the new session by then (RPC `switch_session`
   answers success). Any other code (a leaf missing from the tree, an entry that
   cannot be serialized) is broken session state and propagates, as from every
   other append. In the TUI the launch's warning is written before the chrome
   paints and can be erased by it (the #98 note); the record is then missing,
   as the line said.
6. **When the file exists does not change.** aelix publishes a new session's
   file at launch (header only) where pi defers it to the first message; the
   record lands in a file that was already there. No session file is created to
   hold the record.
7. **Appended, never inserted.** A delegated child's file starts with
   `aelix.child_origin` (ADR-0243 §2); the child's launch appends its record
   after it, so the origin stays first and §8's warning still finds it.
8. **A session with no conversation is not cloned.** `/clone` and RPC `clone`
   refused a fresh session before #413 because it had no leaf; the start record
   gives it one. They now ask for a conversation — a user or assistant message
   anywhere in the file, pi's `SessionManager._hasConversation`
   (`session-manager.ts:1166-1170`) — as well as a leaf, and refuse without one
   in the words `7d163232` used: `Nothing to clone (session is empty).` and
   `Cannot clone session: no current entry selected`, on disk and under
   `--no-session`. pi's interactive guard is a leaf check
   (`interactive-mode.ts:5554-5559`), but its runtime then refuses a persisted
   session without a conversation — `SessionManager._persist` writes no file
   before one (`session-manager.ts:1172-1186`), and `fork` throws `This session
   has not been saved yet. Send a message before cloning or forking it.`
   (`agent-session-runtime.ts:312-313`); only its in-memory (`--no-session`)
   path clones such a session. Review round 2 said aelix's clone of a fresh
   session "matches pi's leaf guard"; that read only the interactive guard and
   was wrong (review round 4, cross F1). Measured
   (`.omc/probes/413-live/r4/`): at `7d163232` a fresh session was refused on
   both surfaces, on disk and under `--no-session`; at the round-3 commit it was
   cloned on disk and, under `--no-session`, reached an older in-memory fork bug
   (`'SessionMetadata' object has no attribute 'cwd'`), now refused again. One
   change from `7d163232`: a session with no conversation but other entries
   (only a name, a `/model` pick, `!cmd` output or a `custom_message` entry an
   extension wrote - a leaf, but no user or assistant message) is now refused
   where `7d163232` cloned it on disk (and hit that fork bug under
   `--no-session`), as pi refuses it on disk.

### Divergences from Pi (ADR-0235: recorded, not argued)

- **The thinking level is not recorded beside it.** pi appends
  `thinking_level_change` for every new session too (`sdk.ts:458`). Here `off`
  is the kernel's unset sentinel, and writing it would turn every fresh
  session's level into a decision that outranks `defaultThinkingLevel` on
  resume (decisions 4-6 of this ADR); the levels already recorded (a `--thinking`
  flag, a carried level on `/new`) are unchanged. Left to #286.
- **A pending launch, and a pending or held rebuild, records after
  `session_start`** (decision 2), so after the messages written by then, and not
  at all when one of them names its model. pi has no late route: its model is
  resolved before any handler runs.
- **An empty session reopened on another model records that one**; pi never
  reopens an empty session (it was never written to disk).
- **A failed append is a warning.** pi writes the file lazily, so its record
  cannot fail at creation.
- **CLI only.** The record lives in the CLI (`cli/entry.py`), where the launch
  route and the late-route rule are; `aelix-server`'s WS factory and an embedder
  calling `create_agent_session_runtime` directly write none, as before.

### Known corners (left to a follow-up issue)

- **A deferred record outranks a `model_change` that a pending rebuild's
  `setup` writes** when no later answer names its model (verify N1, review
  round 3): a guard-2 launch, then an extension's `ctx.new_session` whose
  `setup` writes `model_change` opus and a turn whose answer carries no
  provenance leaves `[model_change opus, user, assistant, model_change
  guard-2]`, which reopens on the guard-2 route; after a resolved launch, and in
  pi (whose record precedes `setup`), the same flow reopens on opus. Decision 2's
  check reads messages only.
- **A pending rebuild whose `setup` raises never records** (cross F6, review
  round 3): the exception propagates, but the runtime is already live on the new
  session, unheld and on the guard-2 route; the deferred record waits for an
  after-`session_start` callback that never runs, so after one turn that failed
  before the request the reopen lands on the settings default — the #413
  defect, in this corner. pi records in `createRuntime` before `options.setup`
  (`agent-session-runtime.ts:246-257`). Not a regression: before #413 nothing
  was recorded at all.

### Consequences

- A beta.2 reader opens these sessions unchanged (`model_change` was always a
  known entry type): measured with a `v0.1.0-beta.2` worktree's kernel and CLI
  on files this change wrote (`.omc/probes/413-live/impl/compat*.out`).
- Sessions written before #413 keep restoring from their responses (#376's
  decision 2); nothing is migrated.
- A launch with no key now leaves a file holding the record, where it left the
  header only.
- The session viewer (#296) is not built; when it is, a session's first entry
  (after a child's origin) names the model it started on.
- `harness/core.py`'s `set_model` docstring still says aelix writes no
  `model_change` for a launch model; it is owned by another change in this batch
  and is corrected in a follow-up.
- Review round 2 (2026-10-14) moved the record from after `session_start` to
  creation (decisions 1-2), stopped asking a credential (4), turned a storage
  failure into a warning (5) and recorded the `/clone` change (8); measured in
  `.omc/probes/413-live/r2/`.
- Review round 3 (2026-10-14) left the deferred record out when a message
  written since names its model (decision 2) and corrected decision 2's account
  of a pending rebuild, decision 3's pending launch, decision 4's no-key claim
  and decision 5's read-only claim; measured in `.omc/probes/413-live/r3/`.
- Review round 4 (2026-10-14) replaced decision 8 (a fresh session's clone is
  refused again, as at `7d163232`; the "matches pi" claim was wrong),
  clarified decision 2's wording (round 3 already read every message written
  since creation; round 4 added the row that pins it) and named `/import`
  among the pending rebuilds, and recorded the known corners above;
  measured in `.omc/probes/413-live/r4/`.

## Amendment (2026-10-14, #412) — `/agents use` keeps the restored model

The #376 amendment left one way out of a restored model open (its Consequences):
`/agents use` of a profile that names no `model:` or `provider:` (or `--none`)
re-resolved the launch inputs, and in a
session reopened on its record that moved it onto the settings model. Measured on `main`
`529b997e` in the real TUI (`.omc/probes/412-live/impl/`): a session that ran on
`claude-haiku-4-5`, reopened with `--continue` under a settings pair
`anthropic`/`claude-sonnet-4-5`, came back on haiku; `/agents use plain` moved it to
sonnet, the next prompt was sent as sonnet, and — #376's decision 8 records every idle
`set_model` — the file gained `model_change` sonnet, so the next reopen came back on
sonnet too.

### Decision

The owner decided 2026-10-14 (option A) that a route-less use — no `model:`/`provider:`
of the profile applies, or `--none` — keeps the live model; the decision is ADR-0196's
(its D8, amended the same day), and this section records what it means for the
session's model record:

1. **A route-less `/agents use` writes no model record.** It resolves no model and calls
   no `set_model` — a profile none of whose `model:`/`provider:` applies (none named, or
   a typed CLI flag beat each one named), or `--none` — so a restored session stays on
   its record and reopens on it again, and a fresh session keeps #413's one start record
   instead of gaining a copy per use (#376's decision 8, not gated on a change, used to
   append one). It can still write a `thinking_level_change`: the use re-applies a level
   `--thinking` or the profile's `thinking:` names, and `set_thinking_level` records it
   when that moves the level (ADR-0196's thinking corner, below).
2. **Only dropping a profile's route moves the model.** When the profile being replaced
   (the launch's `--agent` or an earlier use) applied its own `model:`/`provider:`, the use
   re-resolves the launch inputs and records where it lands, as before. Launch inputs that
   name no model and no provider (no `--model`/`--provider`, no settings default) leave
   nothing to re-resolve: the dropped profile's model stays and nothing is recorded, as
   before (since review round 3 its status notes a model whose prompts will be refused,
   as a kept model's does).
3. **Rebuilds are unchanged.** `/resume`, `/fork`, `/import` and `/reload` restore the
   record as decision 1 of #376 says (an active profile that names no `model:` or
   `provider:` counts as naming nothing there either; one whose route a typed flag beat
   comes with that flag, which already turns the restore off); `/new` follows the launch
   inputs.

### Known corners (left to follow-up issues)

- In a restored session, a profile whose `model:` or `provider:` applied and then
  `--none` (or another route-less use) lands on the launch inputs — the settings model —
  not on the record the session was restored on: the restore state lives in
  `cli/entry.py`, which the service cannot read.
- The thinking level is not kept: a route-less use re-applies a level `--thinking` (or
  the profile's `thinking:`) names, reverting a `/thinking` pick, and records that as a
  `thinking_level_change` (ADR-0196's amendment, known corners).

## Amendment (2026-10-14, #286; narrowed 2026-10-15) — every mode applies `defaultThinkingLevel`; what a launch records is unchanged but around a `session_start` handler

Decision 3's ladder — `--thinking`/profile > the session's entry > the
settings-default seed — held in the TUI only. The seed lived in `run_tui`
(`tui/shell.py`), after the harness build, so `-p`, `--mode json` and
`--mode rpc` never applied `defaultThinkingLevel`, and neither did a delegated
child, which is a `--mode json -p` launch (ADR-0243). And it skipped, rather
than clamped, a level the model does not list.

**Measured on `529b997e`** (a local OpenRouter-format recorder with a fake key;
drivers and outputs in `.omc/probes/286-live/impl/`): with
`defaultThinkingLevel=high`, `-p hi`, `--mode json hi` and an RPC prompt each
sent `"reasoning": {"effort": "none"}` and RPC `get_state` said `off`, while the
TUI sent `high` (`base-1.out`, `base-tui.out`); a child whose profile names its
own model and no `thinking:` sent `none` (`base-2.out`, `D[ownmodel]`); a TUI
with `defaultThinkingLevel=xhigh` on a model that tops out at `high` sent `none`
(`tui-xhigh.out`).

### Decision

1. **One ladder, before the mode dispatch, for every mode** — in `cli/entry.py`
   beside `_seed_startup_state`: `--thinking` (and a profile's `thinking:`) >
   the session's recorded level (decision 3, an explicit `off` included, with
   or without a conversation) > `defaultThinkingLevel` > `off`. pi's
   `createAgentSession` resolves the same order for every mode
   (`core/sdk.ts:253-278` at `pi@3daa20280`), less a per-model override aelix
   does not have, and with `medium` where aelix's bottom rung is `off`
   (`core/defaults.ts:3`). It runs before `session_start`, as pi's does, so a
   `session_start` handler sees the level the run starts at and can change it
   — and what the handler does is judged against that level, not against
   `off`, which it met while the default was applied only after it (in
   `run_tui`) or not at all (decision 4's exceptions).
   The seed left `run_tui`, and decision 5's `thinking_level_restored` signal
   left with it: `_seed_startup_state`'s return now feeds
   `_seed_default_thinking_level` directly. The level is assigned, not routed
   through `set_thinking_level` — the restore's rule (no
   `thinking_level_select` for a choice nobody made), and how pi sets its
   initial level — so the TUI launch no longer fires the
   `thinking_level_select` that `run_tui`'s seed fired for the default. A value
   that is not a level (`settings.json` is not validated on load) seeds
   nothing.
2. **Every rung but `--thinking` is clamped to the model**, as pi clamps
   whatever its ladder resolved (`sdk.ts:273-278`) and as decision 3's restore
   does: `xhigh` on a `high`-max model starts at `high`, `high` on a model that
   cannot reason at `off`, and `off` — the default's, or the bottom rung's with
   no default set — on one that cannot switch reasoning off
   (`thinkingLevelMap.off = null`) at its lowest level. The TUI's seed skipped
   instead, which left those launches at `off`. `--thinking` is not clamped at
   launch (the adapters clamp it at request build).
3. **A pending launch is judged after the late-route decision** (#367, ADR-0250
   §2.11). While `session_start` runs, its harness sits on `Model(id,
   provider)` (`api 'unknown'`, which supports only `off`), and a provider
   registered there may still hold the route. The session's level, the
   default or the bottom rung's `off` is applied before `session_start` as
   written, and clamped after the decision against the model the run is on —
   the route, or a model a `session_start` handler moved to — unless a handler
   set another level meanwhile. On the hold's placeholder nothing is clamped:
   the adapters clamp at request build, and `/model`, which does not touch the
   level, leaves the hold at the user's default instead of `off`. (The restore
   had clamped a pending launch's recorded level against an unresolved
   placeholder, so a recorded `high` ran as `off`; it waits for the decision
   too now.)
4. **What a launch records is unchanged** (owner decision 2026-10-15, review
   round 3), **except where a `session_start` handler sets the level or the
   model** (the last item). Otherwise a launch writes the
   `thinking_level_change` entries it wrote on `529b997e`, and no others:
   - `-p`, `--mode json` and `--mode rpc` record nothing for the default (they
     never applied it). A session they start at the default takes whatever the
     default is at its next launch.
   - The TUI records the default where `run_tui`'s seed did, and under its
     conditions: `_record_tui_default_level`, called right before `run_tui`
     (after `session_start` and the late-route decision), appends it when no
     flag or restored level applied, the model the run is on supports the
     default as written (`get_supported_thinking_levels`, the seed's test: a
     default the launch clamped, the bottom rung, a held placeholder and a run
     with no model are not recorded), the default is not `off` (the seed's
     `set_thinking_level("off")` over the unset `off` was no change), and the
     live level still is the default (the seed, too, stood down over a level
     other than `off`). A read-only session or a storage failure writes nothing
     and says nothing, as the seed's `contextlib.suppress(Exception)` did.
   - `--thinking` keeps decision 6's carry (`_seed_startup_state`): recorded
     into a session with no level of its own, unless it is `off`. So a
     `--continue` after a launch with `--thinking off` takes the default, in
     every mode now as in the TUI before.
   - In-session rebuilds keep decision 6's carry: `/new`, `/fork`, `/resume`
     and RPC `new_session` carry the live level into a session with no level of
     its own and record it unless it is `off`. The live level now starts at the
     default in every mode, clamped, so such a session records it — a clamped
     one too (a follow-up).
   - **The exceptions: a `session_start` handler.** The seed ran after
     `session_start` in the TUI (and not at all in the other modes); it now
     runs before it (decision 1, pi's order). With no `--thinking` and no
     restored level, a handler therefore meets the launch's start level, not
     `off`, and the TUI's record comes after what it did. (With either, a
     handler meets the level it met on `529b997e`, where the restore and the
     flag were applied before `session_start` too — except a level restored
     on a pending launch, which the restore had clamped against the
     placeholder, decision 3.)
     - Its `set_thinking_level` records when it changes the live level (#198),
       as before, but the live level is the start level now. A level other
       than the start level and `off` is recorded as before. `off` over a start
       level other than `off` is now recorded, and the run stays at `off`;
       on `529b997e` `-p`, json and RPC recorded nothing, and the TUI's seed
       then set the default over it, ran at it and recorded it whenever the
       model the run was on supported it. A start level other than `off`, set
       again, is now no change and records nothing, where `529b997e` recorded
       it (a change from `off`); the TUI's record still writes it when it is
       the default as written, but not a default the launch clamped, nor the
       lowest level a model that cannot switch reasoning off starts at with no
       default.
     - Its `set_model`, on a launch that is not pending, keeps the level the
       launch fitted to its own model (decision 2): `set_model` keeps the live
       level (the divergence "`/model` does not re-apply the default" below).
       `run_tui`'s seed judged the default against the handler's model, so
       where the launch's model could not take the default as written and the
       handler's can, the TUI ran at the default and recorded it; it now runs
       at the level fitted to the launch's model and records nothing — a
       default of `high`, a launch on a model that cannot reason and a handler
       moving to one that can run at `off`, one of `xhigh` on a `high`-max
       launch model and a handler moving to an `xhigh` model at `high`. The
       other modes recorded nothing before either; they now carry the
       launch-fitted level onto the handler's model. A pending launch is not
       affected (decision 3 settles it after the handler). pi re-applies the
       default on every model switch (`_getThinkingLevelForModelSwitch`), a
       handler's included; the `/model` follow-up covers this path too.

     Measured (`.omc/probes/286-live/r4/n.{base,fix}.out`, the real CLI and
     the real TUI in a pty, a local recorder, a fake key; `529b997e` →
     this change; files as entries, `M` a model, `T` a level): default
     `high`, handler `set_thinking_level("off")`: `-p` and RPC `[M, u, a]`
     → `[M, T:off, u, a]` (both sent `none`), the TUI `[M, T:high, u, a]`
     sent `high` → `[M, T:off, u, a]` sent `none`; handler
     `set_thinking_level("high")`: `-p` and RPC `[M, T:high, u, a]` →
     `[M, u, a]`, the TUI `[M, T:high, u, a]` in both; default `xhigh` on a
     `high`-max model, handler `set_thinking_level("high")`: `[M, T:high, u,
     a]` → `[M, u, a]` in every mode; handler `set_thinking_level("low")`:
     `[M, T:low, u, a]` in both; default `high`, launch on a model that
     cannot reason, handler `set_model` to one that can: TUI `[M, M,
     T:high, u, a]` sent `high` → `[M, M, u, a]` sent `none`; default
     `xhigh` on a `high`-max launch model, handler `set_model` to an `xhigh`
     model: TUI `[M, M, T:xhigh, u, a]` sent `xhigh` → `[M, M, u, a]` sent
     `high`, `-p` and RPC sent `none` → `high`, files `[M, M, u, a]` in both.

   Rounds 1-2 of #286 recorded the start level at creation as pi does
   (`sdk.ts:448-459`): every launch's (round 1), then only a level somebody
   chose (round 2, with pi's `hasExistingSession` conjunct on the restore).
   Both froze levels nobody chose over every later default, or lost a level a
   user chose before quitting (review rounds 1-2, `.omc/probes/286-live/r2*`).
   Measured after the narrowing (`.omc/probes/286-live/r3/rows.*.out`,
   `tui.*.out`; the real CLI and the real TUI in a pty, a local recorder, a fake
   key; rows without a `session_start` handler): what every launch row writes
   equals `529b997e`'s, row for row (the
   one file that differs is the session an RPC `new_session` made, which now
   carries the level the launch applied) — a TUI default of `high` records
   `thinking_level_change(high)`, one of `xhigh` on a `high`-max model (now
   run at `high`) or `high` on a model that cannot reason records nothing;
   `-p`, json and RPC at a default of `high`
   send `high` and record nothing; a default of `high`, then `low`, then
   `--continue -p` sends `low` (`3d1bc5fa`: `high`); an RPC
   `set_thinking_level low` with no prompt, then `--continue -p`, sends `low`
   (`3d1bc5fa`: `none`).

### What this retires above

- Decision 5 (the signal to `run_tui`): the seed is not there any more.
- Consequences' "Restoration is not TUI-only" now holds for the default too.

The divergence "No `messages.length > 0` conjunct" and the #413 divergence "The
thinking level is not recorded beside it" stand.

### Divergences from pi (ADR-0235: recorded, not argued)

- **No creation-time record.** pi appends the level it resolved for every new
  session right after the start model, and for a session with messages and no
  level of its own (`sdk.ts:448-459`); aelix records only what it recorded
  before (decision 4). Without that record, pi's `hasExistingSession` conjunct
  on the restore is not adopted either. A follow-up: record a chosen start
  level without freezing one nobody chose.
- **`/model` does not re-apply the default.** pi's `setModel` re-applies it on
  every model switch (`agent-session.ts:2699-2711`,
  `_getThinkingLevelForModelSwitch`); aelix's `set_model` keeps the live level,
  so a run that started on a model that cannot reason stays at `off` after
  `/model` to one that can (the adapters clamp it at request build), and so
  does one whose `session_start` handler switches the model (decision 4's
  exception). A follow-up, in `harness/core.py`.
- **The bottom rung is `off`, not `medium`, and there is no per-model
  override.** pi reads `getModelThinkingLevel(provider, id)` before the
  default and falls back to `DEFAULT_THINKING_LEVEL = "medium"`
  (`core/defaults.ts:3`).
- **`--thinking` is not clamped at launch** (the adapters clamp it at request
  build); pi clamps every rung.
- **A pending launch is judged after `session_start`** (decision 3 above); pi
  has no late route. A launch with no model at all (no models, no keys) is
  pending too and keeps the default as written (the TUI footer reads
  `high (off)`), where pi starts at `off`.
- **A delegated child reads the global default only** (ADR-0243's #286
  amendment): an own-model child is started with `--no-approve` whenever the
  project has a `.aelix/settings.json`, so a `defaultThinkingLevel` there does
  not reach it, while the parent in a trusted project runs at it. A follow-up.

### Known limits (follow-ups)

- No creation-time record, and `/model` does not re-apply the default (the
  divergences above) — nor does a `session_start` handler's `set_model`, so a
  TUI launch whose model cannot take the default as written, moved by its
  handler to one that can, runs at the level fitted to the launch's model and
  records nothing, where `run_tui`'s seed ran at the default and recorded it
  (decision 4's exception).
- A level the launch clamped is carried by `/new`, `/fork`, `/resume`, RPC
  `new_session` and `switch_session` into the next session and recorded there
  (review round 2, cross F1: a default of `xhigh` run at `high` on a
  `high`-max model leaves `high` in a session made by `/new`, whose next launch
  on an `xhigh` model then runs at `high`).
- `/reload` still drops the level to `--thinking`'s or `off` (this ADR's
  consequence "`/reload` is a third rebuild seam and is untouched"): measured
  in the TUI after this change exactly as before it (`fix-tui.out`,
  `base-tui.out`: `/reload`, then a prompt sent `none`). pi keeps the live
  level across a reload.
- `/agents use` of a profile with `thinking:` still skips a level the model
  does not list (`agents/service.py`), the rule decision 2 replaced for the
  default, and its comment still names the TUI seed that is gone.
- `aelix-server`'s WebSocket RPC builds a bare harness and reads no settings,
  so it applies neither a default model nor a default level.
- A project's `defaultThinkingLevel` does not reach an own-model delegated
  child (the divergence above).

### Review rounds 2 and 3

Round 1 recorded every launch's start level, `off` included, and restored a
session's level whether or not it had a conversation. Round 2 recorded only a
level somebody chose and skipped the session rung without a conversation, as
pi; its review found the record still freezing levels nobody chose (a bottom
rung or clamped default written after a level the reopen ignored) and the
conjunct losing a level chosen before quitting (`.omc/probes/286-live/r2verify/`,
`r2cross-claude/`). Round 3 (owner decision 2026-10-15, "narrow the range and
finish") dropped the record and the conjunct; decisions 1-3 are what rounds 1-2
measured, decision 4 is `529b997e`'s recording. The rows are in
`tests/cli/test_default_thinking_every_mode_286.py`. Round 3's verify
(`.omc/probes/286-live/r3verify/`) found that recording differs where a
`session_start` handler sets the level or the model, which the seed's move
before `session_start` brings; the finish after it states those exceptions
(decision 4) and the `thinking_level_select` the TUI launch no longer fires
(decision 1), measured in `.omc/probes/286-live/r4/`, and changes no
behaviour.

## Amendment (2026-10-22, #415) — RPC `set_model` and `cycle_model` record the pick and fit the level

Decision 8 of the #376 amendment records every idle `set_model`, and its list of
picks a user makes left out the two RPC model commands. They never reached it:
both handlers called `AgentHarness.set_current_model`, a bare state write.

**Measured on `7d9588d1`** (main `27fa21c5` plus three TUI commits that do not
touch these paths): the real `aelix --mode rpc` with a fake key, `models.json`
pointing a `rec` provider at a local recorder (`m1` reasons up to `xhigh`, `m2` up
to `high`, `m3` not at all); driver and outputs in `.omc/probes/b4-b-live/415/`.

- A prompt, then `set_model rec/m2`, then close: `--continue` answered `get_state`
  with `rec/m1` and sent its next request to `m1`. The file held no
  `model_change` for `m2`. `cycle_model` did the same.
- Neither command fired `model_select`. An extension logging both hooks saw only
  the `thinking_level_select` of `cycle_model`'s level fit.
- `--thinking xhigh`, then `set_model rec/m3` (a model that cannot reason):
  `get_state` still said `xhigh`, and nothing was recorded. The request itself
  was already right, because every adapter clamps when it builds the request (`m2`
  went out with `reasoning_effort: high`).
- `cycle_model` fitted the level and recorded the fit, but recorded no model.
  `xhigh` on `m1`, a rotation to `m2` (`high`) and on to `m3` (`off`), then
  close: `--continue` came back on `m1` at `off`, the level fitted for `m3`.

### Decision

1. **Both commands switch through `AgentHarness.set_model`**, as pi's RPC calls
   `setModel` and `cycleModel` (`rpc-mode.ts:476`, `:481` →
   `agent-session.ts:2507-2527`, `:2600-2630` at `pi@fdce48eb4`). The pick is
   recorded under decision 8's rules: at once when idle, queued during a turn,
   never a placeholder, nothing on a read-only session. `model_select` fires.
2. **Then the level is fitted to the model the harness ended on**
   (`rpc_mode._fit_thinking_level_to_live_model`, shared by both commands):
   `clamp_thinking_level` of the live model and the live level, with `off` when
   no level is set. It is written through `set_thinking_level` only when the fit
   changes the level, as pi's setter records and fires only when `isChanging`
   (`:2648`). The model is the live one. A `model_select` handler that answers the
   pick with its own `set_model` leaves the harness on that model, and the level
   fits that model. In pi the handler's `setModel` fits the level again, so pi
   ends in the same state. The file reads `model_change` then
   `thinking_level_change`, pi's order.
3. **A `model_select` handler that raises fails the command** with its error
   (`model_select hook handler raised: …`). The model stays in force, as after a
   refused `/model` (the extension guide: a raise there does not undo the
   switch). The level is not fitted. Between turns neither the model nor a level
   is recorded then, so a session closed before another prompt reopens where it
   was. A prompt that follows runs on the refused model, and its answer records
   that model, so a reopen then comes back on it (review round 2, measured with
   the real `aelix --mode rpc`: `.omc/probes/b4-b-live/r2/p415_rpc.refused.out`).
   A fit at this point would record a level for a model the session never
   recorded. During a turn, `set_model` has already queued the model's record before the hook runs
   (the in-turn write comes before the emit, unlike the idle append). A pick
   refused there is therefore still recorded, as it already was for `/model` and
   an extension's `set_model` (`.omc/probes/b4-b-live/415/inturn_refusal.out`;
   a follow-up).
4. **The answers.** `set_model` returns the model it was asked for, as pi's does.
   `cycle_model`'s `thinkingLevel` is the live level after the fit, which is
   what pi's `this.thinkingLevel` returns.

### Divergences from Pi (ADR-0235: recorded, not argued)

- **The fit starts from the live level.** pi's `_getThinkingLevelForModelSwitch`
  (`:2699-2711`) checks a per-model level setting first, then
  `defaultThinkingLevel`, and only then the live level. aelix has no per-model
  setting, and its default is for a session that has chosen no level (the #286
  amendment). `cycle_model` has always started from the live level (P-182), and
  `/model` keeps it. A switch away and back therefore restores nothing: `xhigh`
  on `m1`, then `m3`, then `m1` again ends at `off`. pi does the same when no
  default is set.
- **`model_select` fires before the level is fitted** (pi fits first), so a
  handler still sees the old level. It also fires when the pick is the live model
  again, which pi's `_emitModelSelect` skips (`:2492`). Both behaviours belong to
  `AgentHarness.set_model`, which `/model` uses too.
- **A rotation's `model_select` says `source: "set"`.** The event's literal has
  no `"cycle"` (`harness/hooks.py`).
- **`cycle_model` still ignores `enabledModels`** (`isScoped: false`, ADR-0162).
  This amendment does not change that.

### Not changed

- `/model`, its picker, `/agents use` and an extension's `setModel` still do not
  fit the level (#255).
- `AgentHarness.set_current_model` is kept for embedders and tests. No product
  code calls it now, but its docstring still names the RPC handlers. A
  follow-up fixes the docstring.
- A session with no conversation still follows the launch inputs (decision 1 of
  the #376 amendment). The pick is written to its file, but a reopen ignores it.
- `aelix-server`'s WebSocket RPC passes no model registry, so both commands
  still answer `requires a ModelRegistry` there.

### Consequences

- The rows are in `tests/rpc/test_rpc_model_commands_record_415.py`. All eleven
  fail on `7d9588d1`, and ten sabotages of the fix each turn at least one red
  (`.omc/probes/b4-b-live/415/sabotage.out`). Review round 2 adds a twelfth: a
  `cycle_model` whose rotation a `model_select` handler moves on answers the
  live level (`xhigh` on m1, to m2 and on to m3: `off`), so an answer of the
  level fitted to the model asked for, read before the switch (`high`), is red
  (`.omc/probes/b4-b-live/r2/415-mutants.out`).
