# 0239. The level was never written down, so every resume started from `off`

Status: Accepted (2026-09-08) — amended 2026-10-08 (#376): decision 7 superseded, the model is restored too
Date: 2026-09-08
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
  session written before this amendment.
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
  Unchanged here (a follow-up).
- `/agents use` of a profile whose model applies records it (decision 8). If the
  same `use` then fails after the switch (its thinking-level setter refused by a
  hook), the rollback restores the live model by assignment and the record keeps
  the profile's model, so a reopen tries that one.
- The decision 7 paragraph above ("The model is deliberately NOT restored here")
  is superseded by this amendment. Its ordering note is kept: the model is
  resolved in the harness build, before `_seed_startup_state` and
  `_finish_session_replacement` clamp the level against it.
