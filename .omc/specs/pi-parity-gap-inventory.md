# Pi-Parity Gap Inventory — aelix vs pi (latest 0.79.6)

Generated 2026-06-17 from two exhaustive sweeps:
- **Layer A — parity debt vs the pin** (`734e08e` ≈ coding-agent 0.74.1): 71 gaps across 9 surfaces.
- **Layer B — since-pin delta** (pin → HEAD ≈ 0.79.6, +449 commits): pi features added after the pin that aelix lacks.

> Honest note: prior targeted audits (launch-readiness, Phase A/B, aelix-tui) were NOT a full functional diff and **missed most of this**, including the thinking no-op. This is the corrected, exhaustive picture.

Severity: **P0** = HIGH parity-breaking · **P1** = MEDIUM · **P2** = LOW/cosmetic. `[A]`/`[B]` = layer.

---

## P0 — HIGH parity-breaking (fix first)

1. **✅ CLOSED (ADR-0135) — [A] Thinking/reasoning NEVER reaches the provider** — was: `AgentLoopConfig` has no `reasoning` field (types.py:267-301); `loop.py:244-249` builds `SimpleStreamOptions` without it; `core.py:3510 reasoning=options.reasoning` is always `None`. `/thinking`, Shift+Tab, `--thinking` (parsed, never read), `:level` shorthand all mutate + persist `thinking_level_change` but the model is never asked to reason. **The entire thinking stack was cosmetic.** *(VERIFIED both sweeps)* — **Fixed in 3 layers (NOT "one wire-up": pi also needs the OpenAI thinkingLevelMap lookup + the full Anthropic adaptive/budget request port). Layer 1 state→options ("off"→None); Layer 2 OpenAI build_params thinkingLevelMap (deepseek+openrouter); Layer 3 Anthropic adaptive-effort/budget-tokens + interleaved beta. Gate 3158 green.**
2. **✅ CLOSED (ADR-0136) — [A] Builtin tool SCHEMAS diverge from pi (all 7)** — was: snake_case params (`old_text`/`new_text` vs `oldText`/`newText`; `ignore_case` vs `ignoreCase`), zero per-field `description` strings, terse top-level descriptions, no `promptSnippet`/`promptGuidelines`. A model aligned to pi's schema emits camelCase → "old_text not found"; case-insensitive grep silently runs case-sensitive. Broad model-behavior gap hidden behind tests that called aelix's own snake_case. — **Fixed the WIRE schema (`{name, description, parameters}` the provider sees) for all 7 tools: camelCase-only param names + per-field descriptions + pi's `number` numeric types + pi-styled top-level descriptions. Option (A) — descriptions state aelix's ACTUAL behavior (no false claims); two behavior-coupled clauses dropped (read offset "(1-indexed)", edit "original file") pending P0 #3. Gate 3186 green.** **STILL OWED (tracked):** (a) **P0 #3 below** — when behavior lands, upgrade the 5 adapted descriptions to verbatim-pi (bash 2000/50KB+temp-file; read 50KB byte cap+1-indexed offset [line count already 2000=matches]; grep/find .gitignore; find relative paths; grep 500-char line cap [aelix currently 250]); (b) **`promptSnippet`/`promptGuidelines`** (option 가) deferred to a system-prompt-parity sprint — add `prompt_snippet`/`prompt_guidelines` to base `Tool` (aelix-ai, non-protected) + a dynamic tool-guidelines assembler feeding `cli/agent_context.build_system_prompt` (which currently hardcodes the equivalent).
3. **[A] Tool behavior divergences — ✅ WAVE 1 + WAVE 2 DONE (ADR-0137/0138), only HEAVY remains.** Research split this into 43 CORE / 5 HEAVY / 9 NA. **✅ Wave 1 (ADR-0137):** `bash` (2000/50KB caps + temp-file + notices + status text), `write` (UTF-16 count + raw path), `ls` (casefold sort + 50KB cap + notices + off-by-one + stat-skip + `(empty directory)` + nullish limit), `grep` (500-char + 50KB cap + notices + effectiveLimit + relativized paths + `--hidden` + `path:N: text` formatBlock), `find` (relativized POSIX + fd flags + notices + effectiveLimit), shared `_truncate.py` (constants + `truncated_by`/`last_line_partial` + whole-line UTF-8-safe truncate) + `_path_utils.py` (`expand_path` + `relativize_to_posix`); bash/grep/find/ls descriptions upgraded to verbatim-pi-where-truthful. **✅ Wave 2 (ADR-0138):** `read` (1-indexed offset+oob error, byte cap via truncate_head + 4-branch notices, NO cat -n numbering, `split("\n")`, magic-byte mime, no server-default limit, verbatim-pi description) + `edit` (new `_edit_diff.py`: original-content right-to-left matching, fuzzy fallback, overlap, prepareArguments, success-msg-not-diff, pi line-numbered diff in details, verbatim error strings, no-change=error) + `tui/render.py` edit-card reads diff from details. **⬜ HEAVY (only thing left for P0 #3):** `ensureTool` rg/fd auto-download (→ guaranteed `.gitignore`+hidden, then add `.gitignore` back to grep/find descriptions; aelix's Python fallback divergence is why it's omitted now), read **image resize to 2000x2000 + dimension note + non-vision note** (needs Pillow + `model` field on `ToolExecutionContext`), read compact-card TUI, bash `commandPrefix`/`spawnHook`/`shellPath`.
4. **[A] models.json custom-model loader UNIMPLEMENTED** — `model_registry.py:109-115` raises `NotImplementedError`; `entry.py:311` calls `create()` with no path. No schema validation / stripJsonComments / custom-model merge / provider baseUrl·headers·compat overrides. Blocks ALL user-defined providers/models. (Settings was rated "full parity" earlier — the registry layer was the miss.)
5. **[A] CLI flags parsed but INERT** — `--export`, `--fork`, `--session`, `--session-dir`, `--models`, `--api-key` are parsed but never consumed; `--fork`/`--session` only appear in `_validate_continue_flag` → a fresh session is silently created instead of forking/opening. `--api-key` does nothing.
6. **[A] Compaction fidelity** — `compaction.py:251-282` rewrites a message list, not pi's entry-level `findCutPoint`/`findValidCutPoints` (no turn-boundary snap, divergent `firstKeptEntryId`); split-turn (turn-prefix) summarization absent; file-op extraction (`<read-files>`/`<modified-files>`) missing (same in branch summaries); no `max_tokens` cap (no `SimpleStreamOptions.max_tokens` field).
7. **[A] Extensions-api breaks** — `getFlag()` returns the DEFAULT, ignoring CLI overrides (api.py:1336-1342; whole point broken); `ExtensionCommandContext` fork/navigateTree/newSession/switchSession signatures diverge + `new_session`/`switch_session` RAISE; no `ReplacedSessionContext` plumbing; `assert_active()` skipped on 8 registration methods; `register_tool` never refreshes tools.
8. **[A] RPC gaps** — `prompt` handler DROPS images + streamingBehavior (rpc_mode.py:309) while steer/follow_up decode them; extension-UI sub-protocol is a total no-op both directions (`ui.select/confirm/input/editor/notify/setStatus` over RPC hangs forever) despite the command set being a perfect 29/29.
9. **[A] /model opens no selector UI** — `/model` only prints/switches by id; pi opens a search/group/Ctrl+P selector. A generic select primitive exists (context.py:137) but is unused here.
10. **[B] Project Trust subsystem ENTIRELY ABSENT** *(pi 0.79.0–0.79.2, since-pin)* — ask-before-loading project-local settings/resources/instructions/packages, `--approve`/`--no-approve`, `defaultProjectTrust`, `project_trust` event, `ctx.isProjectTrusted()`. A whole new security subsystem; aligns with the queued "entry.py extension-loading security" follow-up.

---

## P1 — MEDIUM

**Agent/harness/session**
- [A] `harness.skill()` + `harness.prompt_from_template()` turn-drivers MISSING — formatters exist but have zero callers (dead code); skills/templates can't be invoked by name.
- [A] `prepare_next_turn` not wired (core.py:3570-3583) — mid-run set_model/set_thinking doesn't take effect until next prompt().
- [A] `message_end` replacement reducer missing — ADR-0018 deprecated it on a **layer mix-up** (pi's coding-agent runner, the layer aelix mirrors, DOES implement+consume it). Revert.
- [A] `buildSessionContext` doesn't resolve model from assistant messages (documented ADR-0022, real narrowing).

**Tools**
- [A] No cooperative abort inside `execute()` for read/edit/write/grep/find/ls (only bash forwards signal) — Esc can't cancel long ops.
- [A] bash error reporting drops exit-code/timeout/abort reason; missing commandPrefix/shellPath/spawnHook + getShellEnv curation.

**Extensions / settings / model / oauth**
- [A] `getApiKeyAndHeaders`/`getProviderAuthStatus`/`registerProvider` partial (no config-value indirection, no per-model headers, no authHeader→Bearer, no models.json sources, no catalog replacement).
- [A] GitHub Copilot login doesn't enable models post-auth (github_copilot.py:351-416) — gated Claude/Grok stay unusable; OAuth ignores cancellation.
- [A] `getCommands` argument-completion + sourceInfo missing; `getAllTools` omits parameters schema + sourceInfo.

**RPC**
- [A] RPC bash hardcodes `cancelled=false` + abort decoupled from the ad-hoc exec.

**CLI**
- [A] `@file` + initial messages + `--verbose` not passed to interactive mode (run_tui starts empty regardless).
- [A] Non-interactive + no resolvable model doesn't exit with auth guidance (no `!session.model` guard; no `auth-guidance.ts` Python equivalent).
- [A] Package-manager subcommands (install/remove/update/list/config) absent — become plain message positionals.

**TUI**
- [A] No user keybindings (`keybindings.json` loader); model hotkeys unbound (Ctrl+P/Shift+Ctrl+P cycle, Ctrl+L select, Ctrl+O tool-expand, Shift+Tab thinking cycle, Ctrl+Z suspend).
- [A] Footer omits ~7 segments (token up/down + cache R/W + $cost, `(sub)` OAuth, thinking suffix, `(provider)`, session-name, `(auto)` compaction, context% color thresholds).
- [A] `/settings` exposes 4 of pi's ~18 entries; `/scoped-models`, `/login`, `/logout` + selectors absent.

**Since-pin (B) — net-new pi features (MEDIUM)**
- [B] `openai_completions.py:733` gates OpenRouter routing on `'openrouter.ai' in base_url` — pi decoupled this (#5347). **Interacts with ADR-0114's Ambient-provider ignore-list** (routing block may not reach a proxied baseUrl).
- [B] `--exclude-tools`/`-xt` (subtract flag); `--session-id` (stable ID for automation); `--name`/`-n` (startup session name) — all absent.
- [B] Prompt-template default positional args `${N:-default}` (prompt_templates.py:276 supports `$N` only).
- [B] `ctx.mode` + `ctx.getSystemPromptOptions()`; extension autocomplete `triggerCharacters` (only `/`,`@` hardcoded).
- [B] `StreamOptions.env` / `auth.json` per-key env overrides; global `httpProxy` setting.
- [B] Model catalog stale — `models_generated.json` predates Opus 4.8 / Fable 5 / MiniMax-M3 (regen, not a code port). *(aelix can't even resolve the model this audit ran on.)*

---

## P2 — LOW + cosmetic (selected)
- [A] hook payload field drops (turn_end.toolResults+turnIndex, turn_start.turnIndex, tool_execution_end.result, message_update.assistantMessageEvent, before_agent_start.*); `model_select` source enum missing `'cycle'`.
- [A] `/share`, `/changelog`, `/debug` missing; double-escape picker unimplemented; `/hotkeys` table hard-coded; RPC signal exit codes (129/143) not mirrored.
- [A] ~15 cosmetic wording/format diffs (write "Wrote N bytes", edit diff-as-content, unknown-flag wording, getProviderDisplayName 5 vs ~30, easter eggs).
- [B] cache-hit-rate footer marker; auto first-run theme detection (OSC 11); `areExperimentalFeaturesEnabled` + `xp` marker; OSC 8 clickable file paths; `InputEvent.streamingBehavior` extension field; `compat.forceAdaptiveThinking`/`allowEmptySignature`; headless Codex device-code login.

---

## Likely-latent bugs to verify in the Python port (since-pin crosscutting)
- **LIKELY-LATENT**: stale `tool_execution_update` after tool settlement (loop.py:474-485, pi #5573); sibling tool preflight keeps running after abort (loop.py ~677/580-590, pi #4276).
- Session disposal must abort in-flight agent/compaction/branch-summary/retry/bash (verify /resume hot-swap + auto-retry + compaction cancellation).
- bash: drain stdout/stderr after child exits while descendants write (#5755).
- OpenAI-compatible: tolerate null message content before tool_calls (#5819); context-overflow regexes `(N)` (#5677) / "maximum allowed input length" (#4943); HTTP-status prefix on errorMessage so retry classifier matches 5xx/429 but NOT quota 429s.
- Anthropic 1h cache-write priced 2× input (#5738); OpenAI cache key clamp 64 chars; ANTHROPIC_AUTH_TOKEN env ignored for api-key requests (#4342).
- `--model` slash-prefix ambiguity vs unauthenticated built-in provider (#5643).
- Tail-truncation edge: oversized single line ending in trailing newline (#4715).

## Behavior changes worth adopting (since-pin)
- Compaction summarizer prompt: neutral "AI assistant" wording for non-coding agents (#5401, one line; matches aelix dual-audience).
- XML-tag boundaries for system prompt + AGENTS.md ingestion (vs Markdown headings).
- Event renames `model_select→model_update`, `thinking_level_select→thinking_level_update` (pi 0.77.0 BREAKING — align before porting any HEAD-era extension).
- Collapsed read-tool cards (single line until expanded); `/new` from ephemeral stays ephemeral; `/reload` re-applies steeringMode/followUpMode live (#5377).

---

## Intentional divergences (14 — documented, NOT action items)
entry-id uuid4 vs uuidv7 (ADR-0022); eager compaction/branch message rendering (ADR-0022); WidgetPlacement/flag-type/literal snake_case vs pi camelCase (ADR-0100); AuthStorage/SettingsManager async-first (documented); AgentHarnessStreamOptions untyped dict + TODO; getter shape divergences; phase 'retry' omitted + 'aborted' added; RPC immediate-ack (ADR-0058); find `>` vs `>=`; bash `$SHELL`-first; PI_OFFLINE without PI_SKIP_VERSION_CHECK; `--no-builtin-tools` parsed-unconsumed (Phase A descope).

## Aelix-additive superset (10 — keep, pi lacks these)
abort() cancels in-flight tool tasks; HookBus own-events on `on()`; afterToolCall `terminate`; **PermissionExtension 4-option gate**; **GuardrailExtension 7 deny rules**; `add_cleanup()` LIFO; subprocess_hooks lane; JsonlSessionRepo helpers; auto-retry events + countdown UI (ADR-0130); extra slash commands + arrow-key select primitive.

## Coverage gaps (still NOT fully verified)
`_apply_stream_options_patch` delete-on-None semantics; memory_storage/JSONL maxLines; hook signal/AbortSignal parity; SessionBeforeForkResult.skip_conversation_restore; runtime fork/switch + streamingBehavior honoring; `bind_core` action impls (surface defs only); selector internal layouts; exportFromFile/forkFrom/continueRecent/list_models internals; 428KB catalog content parity; estimate_tokens drift magnitude; image-resize/mime/output-accumulator/edit-diff byte-level. Phase B (providers) + Phase A items excluded by design.

---

## Recommended fix order (front-load the highest leverage)
1. **Wire `reasoning` end-to-end** — one change lights up the entire thinking stack (P0 #1).
2. **Builtin tool schema parity** — camelCase (or accept both) + per-field descriptions + bash 2000/50KB (P0 #2).
3. **Tool behavior parity** — read/edit/grep/find/ls semantics (P0 #3).
4. **models.json loader** + auth indirection (P0 #4).
5. **CLI flag consumption** + no-model guard + interactive @file/initial-msg (P0 #5, P1).
6. **Compaction fidelity** — entry-level cut-point + split-turn + file-ops + max_tokens (P0 #6).
7. **Extensions-api** — getFlag→flag_values, assert_active, ReplacedSessionContext, command-context sigs, message_end reducer (P0 #7).
8. **RPC** — forward images+streamingBehavior; extension-UI bridge; bash cancellation (P0 #8).
9. **TUI** — keybindings.json + model hotkeys + /model selector + footer + /settings + /scoped-models·/login·/logout (P0 #9, P1).
10. **Project Trust subsystem** (P0 #10) + since-pin low-effort wins + likely-latent bugfix sweep + catalog regen.

**Pin advance: NO bulk bump — cherry-pick.** 449 commits are ~68 catalog-metadata + ~125 bugfixes (mostly N/A under aelix's prompt-toolkit/Rich/term-image substitution); only the items above transfer.
