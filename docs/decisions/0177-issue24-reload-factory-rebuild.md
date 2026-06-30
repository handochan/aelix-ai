# ADR-0177 — #24 harness hot-reload round-trip via the P-302 factory rebuild

- **Status:** Accepted — **LIVE**. Production `/reload` routes through the factory rebuild by default (`_reload_rebuild_enabled()` default-ON after the adversarial review; `AELIX_RELOAD_REBUILD=0` is the kill-switch). The dormant build → go-live flip both landed.
- **Date:** 2026-06-30
- **Sprint:** Moat chain — keystone. `#24` is the named BLOCKER for `#53` (self-extensible epic): "agent writes `.aelix/extensions/foo.py` → `/reload` → `/foo` + its tool work, no process restart".
- **Pi pin:** `earendil-works/pi@734e08e`. Ported from `agent-session.ts:reload` (`:2382-2413`) + `_buildRuntime` (`:2329-2381`).
- **Relates:** ADR-0093/0087 (P-380 reload primitives — the 2 steps this completes), ADR-0091 (SettingsManager), ADR-0174 (#44 settings_manager seam — makes `reload()` reachable), P-302 (harness-rebuild pattern), ADR-0100 (ExtensionUIContext / `_rebind`). GitHub `#24`; unblocks `#53` / `#21`.

## Context

`AgentHarness.reload()` implemented pi steps 1–5 + 8–9 but stubbed steps 6 (`_resourceLoader.reload` — re-discover extensions from disk) and 7 (`_buildRuntime` — rebuild runtime + tool registry + restore flag values), and **dropped** the captured `_previous_flag_values`. So `/reload` never picked up a newly-written extension. A 5-agent recon (post-#4, pi-cross-checked) established the seam: the harness **cannot** rebuild itself — it holds no `_create_harness` reference (its `runtime` property is the `_ExtensionRuntime` bridge, not the `AgentSessionRuntime` that owns the factory).

## Decision

**Owner-approved route: factory rebuild, NOT an in-place `_buildRuntime` port.** Add a NEW protected-core method `AgentSessionRuntime.reload()` (sibling of `new_session`/`fork`) that re-runs the factory over the **same** `Session`. `_apply` fuses pi's step 6 + 7 (`_create_harness` → `_build_harness_options` → `discover_and_load_extensions` re-scans disk → fresh `_ExtensionRuntime` + HookBus + tool registry) into one await, so steps 6/7 collapse with **zero** new port.

`reload()` chain, pi `:2382-2413` order, reusing the existing `_teardown_current`/`_apply` primitives:
1. `wait_for_idle` (no mid-turn swap).
2. snapshot `previous_flag_values = harness.extension_runner.get_flag_values()` (shallow copy) **before** teardown.
3. `settings_manager.reload()` (guarded not-None) + `reset_api_providers()` on the OLD harness so the rebuild resolves against reloaded settings.
4. `_teardown_current("reload")` — emits `session_shutdown(reload)`, invalidates the OLD runner (`PI_STALENESS_MESSAGE`), disposes the OLD harness.
5. `_apply(session)` — factory re-discovers + rebuilds (the **same** Session, no `repo.create`/`fork`).
6. **flag round-trip** (the load-bearing new line): overwrite the freshly re-seeded `register_flag` defaults with the snapshot via `new_runner.set_flag_value(...)` — **after** `_apply`, **before** `session_start`.
7. `_rebind_session(harness, "reload")` — swap subscribers / UI / command context onto the new harness.
8. emit `session_start(reload)` on the NEW runner (gated on `has_handlers`).
9. `reload_resources()` (= pi `extendResourcesFromExtensions("reload")`).

**Rebind contract widened (additive, atomic):** `set_rebind_session`'s callback `(AgentHarness)` → `(AgentHarness, str)`; the single `_finish_session_replacement` call-site now passes its `reason`. The TUI `_rebind` skips its session-swap-only `reset_expand_store()` + `tracker.reset()` when `reason == "reload"` so a reload preserves the on-screen transcript + `/stats` lifetime (a session swap still resets them). Headless RPC/print rebinds gain a defaulted `reason` (bodies unchanged). For `/new`/`/fork`/`/resume`, behaviour is byte-identical.

**Dormant build.** The whole rebuild path ships behind `_reload_rebuild_enabled()` (env `AELIX_RELOAD_REBUILD`, **default-OFF**): production `/reload` keeps the cheap `reload_resources()` refresh (zero behaviour change) until the toggle flips. The machinery is fully exercised by tests calling `runtime.reload()` directly. `AgentHarness.reload()` stays AS-IS as the in-place service-reset fallback for runtime-less callers (the minimal REPL).

## Known divergences / v1 scope (deliberate, recorded)

- **New harness OBJECT per reload** vs pi's in-place `this` mutation (the P-302 architecture). The Session, message history, and lineage are preserved (same `Session` object); only the harness/runtime/HookBus are rebuilt.
- **Stale-message text:** a ctx captured before reload raises `ExtensionError(code="stale")` but with the message `"AgentHarness has been disposed"` (dispose's `invalidate()` runs last, last-write-wins over `PI_STALENESS_MESSAGE`) rather than pi's reload-specific wording. The *contract* (loud failure, `code == "stale"`) holds; the text is arguably more accurate since the old harness is genuinely disposed.
- **No `reset_api_providers()` on reload** (adversarial-review HIGH fix). aelix's `reset_api_providers()` is **clear-only** (it empties the process-global `_PROVIDERS` streaming-dispatch table with no re-register), and the factory rebuild never re-runs `register_providers()` — so calling it would brick all model access until a process restart. `_PROVIDERS` is a stateless `api→adapter` dispatch table, unchanged across a reload (and `/new`/`/fork`/`/resume` don't touch it either); credential/setting changes flow through `settings_manager.reload()` + the rebuilt harness's per-stream `get_api_key_and_headers`. (The in-place `AgentHarness.reload()` still calls the clear-only reset — a latent no-op-or-brick that is moot since it has no production caller.)
- **`model_registry.reset` not run on reload** — the live `ModelRegistry` is a `run_tui` closure, unreachable from `AgentSessionRuntime`. Re-declared providers survive via `bind_model_registry` replay in the factory; a provider from a *removed* extension lingers. v1 scope (threading the registry into the runtime is a follow-up).
- **Flag round-trip lands AFTER `_apply`** — the restore loop runs after the factory re-ran each extension's `setup()`, so an extension whose `setup()` *reads its own flag* observes the registered DEFAULT (not the user's prior value) DURING reload; the post-reload end-state is correct (the restore overwrites the default before any handler runs). Threading `previous_flag_values` into the factory so the fresh runtime is seeded before `setup()` (pi's `_buildRuntime({flagValues})`) is a follow-up; the practical impact is narrow (flags are typically read in handlers, which run after restore).
- **`active_tool_names` round-trip + `includeAllExtensionTools:true` union deferred** — the default (no `--tools`) path already activates all tools incl. extension tools, so the moat works; only the explicit `--tools`+extension combo is affected.
- **Minimal REPL** keeps `reload_resources()` (no runtime → no factory route).

## Adversarial review + fixes applied

6-lens review (pi-parity, lifecycle/teardown, flag/staleness, security/trust, blast-radius, completeness) + a verification synthesis that adversarially re-confirmed each finding against the code. Verdict: **CHANGES-NEEDED** — 1 HIGH + 1 MED applied, plus 2 test-gap fixes:

- **HIGH (provider brick) — FIXED.** `reload()` called `reset_api_providers()` (clear-only) and the factory never re-registered, so a rebuild `/reload` would have emptied `_PROVIDERS` and bricked all model access until restart (the 6 reload tests passed only because they inject a mock `stream_fn` that bypasses `_PROVIDERS` — false-green). Removed the `reset_api_providers()` call; added `test_reload_does_not_clear_streaming_providers` (registers a real provider, reloads, asserts it survives).
- **MED (ordering) — FIXED.** `settings_manager.reload()` ran BEFORE the `session_shutdown(reload)` emit, inverting pi (`:2385`→`:2386`); a shutdown handler would observe post-reload settings. Moved settings reload to AFTER `_teardown_current`, before `_apply`.
- **LOW (flag-before-setup) — documented** as a known divergence (above).
- **Test gaps — FIXED.** Added `test_reload_picks_up_newly_written_extension_tool` (the moat for TOOLS, not just commands). The dormant ON-path routing + emit-reason coverage gaps were judged low-value by the synthesis (the branch + `wait_for_idle` are verifiably present).
- Rejected by the synthesis: 2 duplicates + 2 low-value coverage nits.

## Verification

`python3 -m pytest tests/ -q` → **4527 passed / 0 failed / 1 skipped** (+21); `ruff check` clean; authoritative whole-project `.venv/bin/pyright` → only the pre-existing intentional `scripts/pyright_spike.py` narrowing-test errors (**0 new**; shell.py single-file "errors" are pre-existing false-positives that vanish in the whole-project run). Tests: the **moat regression** (write `.aelix/extensions/foo.py` after startup → `runtime.reload()` → its **command AND tool** are live, no restart), flag round-trip (user-toggled flag survives), removed-ext (command disappears — HookBus rebuilt), old-runtime invalidation (`code=="stale"`), rebind fired with `reason="reload"`, same-Session reuse, the provider-survival guard, and 13 dormant-toggle cases. The widened-callback regression (2 runtime test stubs) is resolved.

## Go-live + follow-ups

- **Go-live — DONE.** `_reload_rebuild_enabled()` flipped to default-ON (owner-approved post-review); production TUI `/reload` now routes through `AgentSessionRuntime.reload()`. `AELIX_RELOAD_REBUILD=0` is the kill-switch. This delivers the observable **#53 Track A** imperative-hot-reload flagship (write extension → `/reload` → live, no restart); Project-Trust is re-applied via the `no_project_local` closure (reload never silently upgrades/downgrades trust — confirmed by the security-trust review lens).
- **Remaining v1 follow-ups:** re-point `ctx.reload()` to the runtime path; thread `ModelRegistry` into `AgentSessionRuntime` for removed-ext provider reset; `active_tool_names`/`includeAllExtensionTools` round-trip + seed `previous_flag_values` into the factory (so `setup()` reads restored values); give the minimal REPL a runtime. **#53** epic remainder = `#21` (declarative `contributes.*`/activation — aelix-original, deferred per ADR-0174) + `#19` (install) + `#32` (marketplace).
