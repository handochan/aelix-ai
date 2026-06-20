# 0143. P0 #7 Wave 1 — Extension-API Hardening + `--api-key` Auth + Command-Context Lifecycle

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #7 (extensions-api)**. A recon pass (`.omc/specs/sprint-p0-7-extensions-api-spec.md`)
mapped the six work-items against verbatim pi source (`coding-agent/src/core/extensions/{loader,runner,types}.ts`,
`agent-session.ts`, `agent-session-runtime.ts`) and corrected several imprecise gap-inventory
premises. The recon recommended a 3-wave split by risk/protection. **This ADR is Wave 1** — the four
low-risk, **unprotected** items (no `aelix-agent-core` change). Wave 2 (item 3, `register_tool`
refresh — protected harness binding) and Wave 3 (item 5, `message_end` replacement reducer — heavily
protected, supersedes ADR-0013/0018) follow.

## Decision (Wave 1)

### Item 1 — `get_flag` → `flag_values` precedence (`extensions/api.py`)

Pi `getFlag` (`loader.ts:262-267`) reads `runtime.flagValues.get(name)` (returning `undefined` if the
extension never registered the flag) and does **not** fall back to the static default at read time;
the default is seeded into `flagValues` at **registration** (`loader.ts:251-253`). Two-part fix:
`register_flag` now seeds `self._runtime.flag_values[name] = default` when `default is not None and
name not in flag_values` (the `not in` mirrors pi's `!has` so a CLI override is never clobbered);
`get_flag` reads from `flag_values`, so a `set_flag_value` override now wins. (The flag_values
machinery existed since ADR-0093 but `get_flag` was never wired to it — finishing an incomplete port.)

### Item 2 — `assert_active()` on the registration/flag methods (`extensions/api.py`)

The gap-inventory's "8 registration methods" framing was inverted: pi guards **every** `ExtensionApi`
method with `runtime.assertActive()` (the lazy-context-getter guarding is a *separate* mechanism aelix
already mirrors via `ExtensionContext.__getattribute__`). Aelix's register*/`get_flag` methods guarded
**zero**. Added `self._runtime.assert_active()` as the first statement of the 7 pi-verified methods
(`on`, `register_tool`, `register_flag`, `get_flag`, `register_command`, `register_shortcut`,
`register_message_renderer`) + `add_cleanup` (aelix-additive, documented). A no-op during normal load
(the loader never invalidates mid-load); it only rejects use of a stale post-replacement API.
(`register_provider`/`unregister_provider` left as a tracked LOW — not in the recon's verbatim-verified
set; not adding an unverified guard.)

### Item 4 — `ExtensionCommandContext.new_session`/`switch_session` (`extensions/command_context.py`)

Both previously RAISED a "deferred" error. They now delegate to a bound `AgentSessionRuntime` (Pi
`runner.ts:636-668` `createCommandContext` overlays delegating to `newSessionHandler`/`switchSessionHandler`/
`forkHandler`); the runtime's existing signatures already accept pi's option shapes
(`new_session{parent_session,setup,with_session}`, `switch_session{with_session}`,
`fork{position,with_session}`) — **no protected change needed**. `fork` was realigned from
`fork(source, options)` to pi's `fork(entry_id, options)` (delegates to `runtime.fork` when bound,
keeps `repo.fork` as the unattached fallback). `with_session` flows through to the runtime, which
builds the `ReplacedSessionContext` handle via `_finish_session_replacement →
create_replaced_session_context`. A new `_opt(options, key, default)` helper accepts dict-or-attribute
options. Unbound → a clear `invalid_state` error (not the old "deferred" text). No live callers exist
outside tests, so the `fork` signature realignment is safe.

### Item 6 — `--api-key` harness-auth wiring (`cli/entry.py`)

The harness already consumes `AgentHarnessOptions.get_api_key_and_headers` (`core.py:3447`);
`AuthStorage.set_runtime_api_key` and `ModelRegistry.get_api_key_and_headers` already exist — only the
CLI agent-run path never populated them. Now `AuthStorage` + `ModelRegistry` are built once on the
agent-run path; when `--api-key` is supplied, pi's "requires a model" rule (`main.ts:574-582`,
verbatim error string) is enforced, the key becomes a runtime override
(`set_runtime_api_key(model.provider, key)`), and a `_make_auth_callback` adapter
(`ResolvedRequestAuth` → harness `{"apiKey","headers"}` dict; returns `None` when neither is set so the
adapter's `get_env_api_key` fallback still resolves) is threaded onto the options. The inert
"--api-key ignored" warning is removed.

**Design (i) — zero env regression:** `get_api_key_and_headers` is set **only** when `--api-key` is
present, so env-only runs (`OPENROUTER_API_KEY` etc.) keep using the adapter's direct env resolution.

## Known limitation (tracked, not a regression)

`--api-key --model <provider>/<pattern>` (no separate `--provider`) is rejected because aelix's stubbed
`resolve_model` does not yet split the `provider/pattern` shorthand (returns `provider=""`), so the
"requires a model" guard is stricter than pi. Documented inline at the guard; resolves when the
SettingsManager / `resolveModelFromCli` port lands. The OpenRouter-from-env path populates `provider`,
so the common case works.

## Verification

- Implemented + 4-lens adversarial review (pi-fidelity / regression / scope / test-adequacy) + fix as a
  dynamic Workflow (8 agents). 21 findings, 3 confirmed non-LOW (1 HIGH + 2 MEDIUM) — all fixed
  (test-only + the guard comment; 0 false positives; new tests mutation-verified).
- Full gate: **3434 passed, 1 skipped** (+28 tests); the only 3 failures
  (`tests/cli/test_append_system_prompt.py`) are the pre-existing AGENTS.md cwd-coupling baseline,
  unrelated. ruff clean.
- Diff confined to `extensions/api.py`, `extensions/command_context.py`, `cli/entry.py`, and tests —
  **no protected `aelix-agent-core` change** (verified).
