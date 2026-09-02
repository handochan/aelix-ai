# TUI v2 Overhaul — Grounded Roadmap

> Source: 6-agent code recon + synthesis (2026-06-21). All claims are file:line / ADR verified.
> Scope: the user's 7-area TUI overhaul agenda. This is the durable record of state/gap/effort/deps.

## Headline

Overwhelmingly a **"rails exist, train missing"** situation: the harness already exposes the
backing APIs for almost every item (AuthStorage, McpClientManager, PermissionExtension,
SettingsManager, ModelRegistry.get_available, get_session_stats, harness introspection). The gap
is almost entirely **pure-TUI-consumer UI**, NOT protected-core work. Exceptions: WP-0's persistent
permission-rule store, and the (external) subagent-lifecycle subsystem.

**Parity split (~50 sub-items):** ~35-40% pi-parity catch-up · ~50-55% Claude/Qwen additive
divergence · ~5-10% aelix-additive/done. **Most foundational infra exists to serve the ADDITIVE
half** — so endorsing the divergence is the gating decision.

## Per-area state

1. **Differential rendering** — *DO NOT ADOPT.* pi diffs because it owns the whole screen as one
   re-rendered buffer; aelix writes history to immutable Rich scrollback + lets prompt-toolkit
   already diff the live region (`_output_screen_diff`, 3.0.52). Porting = full-screen rewrite vs
   ADR-0088/0133 for zero visible gain (flicker already fixed by ADR-0132). **Only lift CSI 2026
   synchronized-output (~5 lines @ chrome.py:440-458).**
2. **Statusline/chrome** — single-line 6-segment footer exists (context.py:505-530). Missing:
   multi-line layout (chrome is fixed height=1 rows), spinner elapsed/token count (chrome.py:400-416
   shows static "Working…"), input prompt prefix/placeholder (prompt='» ' stored chrome.py:91/95 but
   never rendered), subagent activity line (agent events are EMPTY, types.py:159-224), `/statusline`
   config + segment registry + preview + persistence.
3. **Slash commands** — 21 work (good framework). MISSING/unregistered: /context /login /logout /mcp
   /extension /permissions /hooks /stats /btw /skills /init. /model + /thinking are plain-text not
   pickers (select() + get_available() are ready). /settings exposes 4 of pi's ~18. Autocomplete is
   stock CompletionsMenu — no "→" marker, no "(N/total)" counter. **Memory correction: /fork·/clone =
   ADR-0129, not 0131.**
4. **Modes / permissions / approval-UI** — NO shift+tab, NO mode set (grep PermissionMode/plan/yolo =
   zero). BUT a real per-tool gate exists: PermissionExtension (ADR-0120, 4-option dialog, ephemeral
   session wildcards) + GuardrailExtension (hard-deny) + Project Trust (load-time, distinct). It's
   invisible/un-toggleable. "mode" is OVERLOADED (steering/queue-drain). **"Weird screen" root cause:
   the approval prompt reuses the generic filterable select() → shows a nonsensical "Type to search"
   hint on a yes/no, truncates command to 120 chars, no diff preview.** entry.py:384 builds
   PermissionExtension anonymously (no held ref to mutate posture).
5. **Startup header** — logo exists (ADR-0148) but panel shows only model+cwd+/help. version,
   base-url, [Context]/[Tools]/[Skills]/[Hooks]/[Extensions] ALL introspectable off harness; only the
   AGENTS.md-loaded flag needs plumbing. **Small effort.** (Skills shows 0 until ResourceLoader lands.)
6. **Transcript / user-echo** — user turn is the WEAKEST element: `Text("» {text}", bold)` mono, no
   color/box/blank-line, buried among cyan tool headers / colored diffs / dim-italic thinking. Two
   call sites only (shell.py:1298 live, render.py:465 replay). pi uses a full-width bg bubble (#343541,
   pad 1/1, no chevron). steer/follow-up echo is inconsistent (shell.py:752). **Small effort.**

## Work packages

- **WP-0 Permission Engine (FOUNDATION, large):** PermissionMode posture object + held PermissionExtension
  ref + posture-aware gate + persistent rule store + tests. Blocks WP-4 and /permissions.
- **WP-1 Descriptor-Dialog Framework (FOUNDATION, large):** custom completions menu (→/counter),
  multi-checkbox picker (extend select()), purpose-built approval dialog (fix "weird screen"),
  tabbed-manager shell on the 8 Tier-2 primitives. Shared by /statusline /permissions /mcp /extension
  /stats /model + autocomplete.
- **WP-2 Settings Persistence + Segment Model (FOUNDATION, medium):** construct the existing-but-DEAD
  SettingsManager in TUI runtime; add statusLine field; refactor footer composer → named-segment
  registry shared by footer + statusline + header.
- **WP-3 Footer/Spinner/Input quick wins (medium):** spinner elapsed+tokens+"esc to cancel"; input
  '❯' prefix + placeholder; missing pi footer segments; context% color thresholds. (soft-dep WP-2)
- **WP-4 Permission Modes + shift+tab + Approval UI (large):** s-tab cycling, mode BADGE (disambiguated
  from steering), wire approval dialog, plan mode, /permissions tabs. ('auto mode' classifier = defer.)
  Deps WP-0+WP-1+WP-2.
- **WP-5 Enriched Startup Header (SMALL, self-contained):** version, base-url,
  [Context]/[Tools]/[Skills]/[Hooks]/[Extensions], tri-part hint. No deps. **Recommended first.**
- **WP-6 Transcript/User-Echo lift (SMALL):** blank-line + color + _render_user() helper (trivial);
  pi bg-bubble (medium). No deps. **Quick win.**
- **WP-7 Slash-Command catch-up (very-large):** /thinking→select, /model rich picker (#9), /hooks
  viewer, /mcp status, /scoped-models, /skills /init /btw, /settings expansion. Deps WP-1+WP-2.
- **WP-8 Heavyweight subsystems (very-large):** /context aggregator, /login wizard, /stats time-series,
  /extension manager, subagent activity line (BLOCKED on subagent-lifecycle subsystem), multi-line
  statusline. Last. **✅ SHIPPED 2026-06-22 (ADR-0165, `7d2d5c1`, merged+pushed)** — /login·/logout
  (API-key + OAuth), /stats, /extension, /context enrichment, multi-line statusline all live (full
  suite 3971 pass; tmux smoke 7/7). **DEFERRED items (not abandoned — resume after each dependency
  lands) are enumerated in ADR-0165 §"Deferred / not implemented" (D1–D8, updated by ADR-0166):**
  D1 subagent activity line (needs subagent-lifecycle events — they're empty today), ~~D2 "auto mode"~~
  **CORRECTED — already implemented (ADR-0158: AUTO posture + tree-sitter classifier + 🤖 badge +
  shift+tab)**, D3 /stats cross-session heatmap/trend/projects (needs persisted per-turn time-series),
  D4 /stats latency cards (low-effort TUI-side follow-up), D5 /extension Discover/Sources + runtime
  enable-disable (needs a registry + toggle API), D6 /context EXACT per-category split (needs a
  protected-core ContextUsage change; estimate ships today), **D7 /login custom-provider model wiring
  — RESOLVED for OpenAI-compatible (ADR-0166): fetch /models → select → models.json → /model;
  Anthropic/Gemini still manual**, **D8 (NEW) OpenAI Responses API adapter (`openai-responses`) —
  protected-core gap; Copilot/OpenAI gpt-5.x are now hidden/guarded with a clear message (ADR-0166)
  until the adapter ships in aelix_ai.providers.**
- **WP-9 Differential rendering — REJECTED (record only):** only CSI 2026 wrap.

## Recommended sequencing

1. **Phase 1 (no deps, ship now):** WP-5 header + WP-6 trivial user-echo + WP-3 spinner/input + WP-9
   CSI-2026. Momentum, zero foundations.
2. **Phase 2 (foundations, overlap):** WP-0 (start first — gates headline feature), WP-1, WP-2.
3. **Phase 3:** WP-4 (shift+tab modes + approval fix) + WP-3/WP-6 segment/bubble folds.
4. **Phase 4:** WP-7 pickers/viewers.
5. **Phase 5:** WP-8 heavyweights, one sprint each.

## Open decisions (user)

1. **THE BIG ONE — parity vs divergence.** ~half the agenda is Claude/Qwen additive (permission mode
   set, /context, /stats, /login wizard UX, enriched header, multi-line statusline, /statusline, /init,
   /btw, /skills, subagent line). Stay pi-faithful OR endorse a deliberate fork. Most foundational
   infra (WP-0/1/2) is only justified if additive is approved.
2. **Permission scope:** default+auto-accept+yolo are cheap posture flips; plan needs a plan/exit flow;
   'auto mode' needs a net-new classifier (defer?). Does YOLO bypass Guardrail hard-denies?
3. **Naming:** "mode" is taken by steering — permission mode needs a distinct name + badge.
4. **User-echo treatment:** pi full-width bg bubble (no chevron) vs lighter colored-chevron + blank-line.
5. **Statusline ambition:** single enriched line vs full multi-line block + "N local agents" (needs new
   chrome rows + the blocked subagent subsystem).
6. **Subagent activity line:** invest in the subagent-lifecycle subsystem, or drop the line from scope?

## Cross-cutting corrections surfaced

- `/fork`·`/clone` shipped under **ADR-0129** (sprint 6h21), not ADR-0131 (Ctrl+G editor).
- `--api-key` IS wired (entry.py:656-687 / ADR-0150) — earlier "no harness AuthStorage / deferred" note is stale.
- SettingsManager exists (aelix_ai/settings/) but is **never constructed** in the coding-agent.

---

# Appendix: Target UI mockups (user-provided, 2026-06-21)

These are the concrete UX targets the user specified. Each is tagged with the work package that
owns it. Several are Claude-Code/Qwen-Code-inspired (additive); a few are pi-parity. Reproduced
verbatim as the visual spec — implementations should match layout/wording, not pixel-art.

## A. Statusline / chat-area chrome — WP-3 (spinner/input ✅ shipped) · WP-2 (multi-line) · WP-8 (subagent line)

```
  ⠸ Thinking (10s · ↑ 356 tokens · esc to cancel)
──────────────────────────────────────────────────────────────────────────────
❯ Type your message or @path/to/file
──────────────────────────────────────────────────────────────────────────────
  [OpenRouter] openai/gpt-oss-120b:free | main | Context 83.5% left |   16.5% used
  /Users/handochan/dev/aelix-ai | Context 16.5% used
  auto mode (classifier-evaluated) (shift + tab to cycle) · 1 local agent

    main
    ○ Explore: Inspect recent repository changes (Shell git -C /Users/handoc… ▶ 28s · 34k tokens
```
Notes: the spinner `(10s · ↑ N tokens · esc to cancel)` shipped in ADR-0153 EXCEPT the `↑ N tokens`
clause (deferred — no real incremental-usage source). The `❯` input prefix + placeholder shipped.
The multi-line statusline block (provider|model|branch|context, cwd line, `auto mode … N local agents`)
is WP-2/WP-8. The per-subagent activity line (`○ Explore: … ▶ 28s · 34k tokens`) is WP-8, BLOCKED on the
subagent-lifecycle subsystem (agent events are empty payloads today).

## B. `/statusline` — configure-statusline picker — WP-2 (claude-additive)

```
> /statusline
  ╭──────────────────────────────────────────────────────────────────────╮
  │ Configure Status Line                                                 │
  │ Select which items to display in the status line.                     │
  │ Type to search                                                        │
  │ >                                                                     │
  │ › [x] Use theme colors      Apply colors from the active /theme       │
  │       ───────────────────────                                        │
  │   [x] model-with-reasoning  Current model name with reasoning level   │
  │   [ ] model-only            Current model name without reasoning level│
  │   [x] git-branch            Current Git branch when available         │
  │   [x] context-remaining     Percentage of context window remaining    │
  │   [ ] total-input-tokens    Total input tokens used in session        │
  │   [ ] total-output-tokens   Total output tokens used in session       │
  │   [x] current-dir           Current working directory                 │
  │   [ ] project-name          Project name when available               │
  │ Preview                                                              │
  │ [OpenRouter] openai/gpt-oss-120b:free | main | Context 82.8% left | … │
  │ Use up/down to navigate, space to select, enter to confirm, esc cancel│
  ╰──────────────────────────────────────────────────────────────────────╯
```
Needs: multi-CHECKBOX picker (current `select()` is single-choice) + named-segment registry + live Preview
+ SettingsManager persistence.

## C. Slash-command autocomplete dropdown — WP-1 (custom menu) · WP-7

```
❯ /logout
──────────────────────────────────────────────────────────────────────────────
  trust          Save project trust decision for future sessions
  login          Configure provider authentication
→ logout         Remove provider authentication
  new            Start a new session
  compact        Manually compact the session context
  (17/22)
```
Needs: replace stock prompt-toolkit `CompletionsMenu` with a custom control adding the `→` selected-row
marker + the `(17/22)` match counter (per-command descriptions already render).

## D. `/model` — rich model picker — WP-7 (pi-parity) — IN PROGRESS (this sprint)

```
/model
  ╭───────────────────────────────────────────────────────────────────────╮
  │ Select Model                                                           │
  │   1. [openai] [OpenRouter] z-ai/glm-4.5-air:free                       │
  │      (z-ai/glm-4.5-air:free)                                           │
  │ › 2. [openai] [OpenRouter] openai/gpt-oss-120b:free                    │
  │      (openai/gpt-oss-120b:free)                                        │
  │   3. [openai] qwen3.6-hermes:latest                                    │
  │   4. [openai] qwen3.6:35b-a3b                                          │
  │ ───────────────────────────────────────────────────────────────────── │
  │ Modality:       text-only                                              │
  │ Context Window: 131,072 tokens                                         │
  │ Base URL:       https://openrouter.ai/api/v1                           │
  │ API Key:        OPENROUTER_API_KEY                                     │
  │ Enter to select, ↑↓ to navigate, Esc to close                          │
  ╰───────────────────────────────────────────────────────────────────────╯
```
Needs: numbered list with `[provider]` tags + a DETAIL FOOTER (modality / context-window / base-url /
api-key-env) that updates as the highlight moves. Reuses `ModelRegistry.get_available()` + `select()`
(extended with an optional per-item detail panel).

## E. `/context` — context-usage panel — WP-8 (claude-additive)

```
  ╭─────────────────────────────────────────────────────────────────────╮
  │  Context Usage                                                       │
  │  Model:                           Context window: 131.1k tokens      │
  │  openai/gpt-oss-120b:free                                            │
  │  ██████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒            │
  │  █ Used                              22.5k tokens (17.2%)            │
  │  ░ Free                              75.5k tokens (57.6%)            │
  │  ▒ Autocompact buffer               33.0k tokens (25.2%)            │
  │  Usage by category                                                   │
  │  █ System prompt                       6.0k tokens (4.6%)            │
  │  █ Built-in tools                     10.8k tokens (8.3%)            │
  │  █ Memory files                         210 tokens (0.2%)            │
  │  █ Skills                               472 tokens (0.4%)            │
  │  █ Messages                             965 tokens (0.7%)            │
  │  Compaction thresholds                                               │
  │    Effective window     111.1k │ Warn 78.6k │ Auto 98.1k │ Hard 108.1k│
  │    Current tier                                      safe            │
  │  Run /context detail for per-item breakdown.                         │
  ╰─────────────────────────────────────────────────────────────────────╯
```
Needs: new usage-by-category aggregator + autocompact-buffer surfacing (reserve math exists in core.py).

## F. `/login` (auth) — provider connect wizard — WP-8 (mixed: QWEN-style UX)

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │ Connect a Provider                                                    │
  │   Using OAuth (Subscription Plan)                                     │
  │   Using Subscription Plan                                             │
  │ › Using API   Choose a built-in provider and connect with an API key  │
  │   Custom Provider   Manually connect a local server / proxy / unsupported│
  │ Terms of Service and Privacy Notice: <url>                            │
  └──────────────────────────────────────────────────────────────────────┘

  Custom Provider · Step 1/6 · Protocol
   › OpenAI-compatible      Standard OpenAI API format (most common)
     Anthropic-compatible   Anthropic Messages API format
     Gemini-compatible      Google Gemini API format
   Enter to select, ↑↓ to navigate, Esc to go back

  Custom Provider · Step 2/6 · Base URL
   > https://api.openai.com/v1
   Enter to submit, Esc to go back

  Custom Provider · Step 3/6 · API Key
   > sk-...
   Enter to submit, Esc to go back
```
Needs: AuthStorage.login wired into a select/input/confirm multi-step flow. Also `/logout`.

## G. `/btw` — WP-7 (claude-additive)

`/btw` — Ask a quick side question without affecting the main conversation (ephemeral turn, not persisted to the branch).

## H. `/extension` — manager — WP-8 (claude-additive)

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Installed    Discover    Sources   (Tab / ←→ to switch)              │
  │ No plugins or MCP servers installed.                                  │
  │ Use the Discover tab to find and install plugins.                     │
  │ ↑↓ navigate · Space enable/disable · f favorite · Enter details · Esc │
  └──────────────────────────────────────────────────────────────────────┘
```

## I. `/hooks` — read-only viewer — WP-7 (claude-additive)

```
  ╭──────────────────────────────────────────────────────────────────────╮
  │  Hooks · 0 hooks configured                                           │
  │  This menu is read-only. To add/modify hooks, edit settings.json.     │
  │   1. PreToolUse        Before tool execution                         │
  │   2. PostToolUse       After tool execution                          │
  │   3. PostToolUseFailure  After tool execution fails                  │
  │   …  (SessionStart / Stop / SubagentStart / PreCompact / SessionEnd …)│
  │  Enter to select · Esc to cancel                                      │
  ╰──────────────────────────────────────────────────────────────────────╯
```

## J. `/init` — WP-7 (claude-additive)

`/init` — scaffold a project AGENTS.md/memory file (bootstrap; nothing generates one today).

## K. `/mcp` — MCP server manager — WP-7 (claude-additive)

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │ Manage MCP servers · 0 servers                                       │
  │ No MCP servers configured. Add MCP servers to your settings.         │
  │ Esc to close                                                         │
  └──────────────────────────────────────────────────────────────────────┘
```

## L. `/permissions` — tabbed manager — WP-4 (claude-additive)

```
  Permissions:  Allow    Ask    Deny    Workspace   (←/→ or tab to cycle)
  Qwen Code won't ask before using allowed tools.
  ╭──────────────────────────────────╮
  │ > Search…                        │
  ╰──────────────────────────────────╯
  › 1. Add a new rule…
  ↑↓ navigate · Enter select · Type to search · Esc cancel
```
Needs: a persistent rule store + the WP-0 permission engine.

## M. `/stats` (usage) — 3-tab dashboard — WP-8 (qwen-additive)

```
  Session   Activity   Efficiency      (tab to switch)
  [Session] Tool Calls 8 (✓7 ✗1) · Success 87.5% · +7/-0 · Wall 54m · API 2m · Tokens in/out/cached
  [Activity] Sessions·Duration·Tokens · Activity heatmap · Token trend (braille chart) · Projects table
  [Efficiency] Cache hit / Tool success / Avg latency cards · Tool leaderboard · per-model reqs/in/out/cache/latency
```
Needs: per-turn time-series instrumentation (not retained today) for heatmap/trend.

## N. `/thinking` — WP-7 (pi-parity)

Make `/thinking` a SELECT over the model's supported reasoning levels (currently a typed arg).

## O. `/settings` — pi has ~23 entries — WP-7 (pi-parity)

```
  Auto-resize images      true
  Block images            false
  Skill commands          true
  Show hardware cursor    false
  Editor padding          0
→ Autocomplete max items  5
  Clear on shrink         false
  Terminal progress       false
  Steering mode           one-at-a-time
  Follow-up mode          one-at-a-time
  (7/23)
  Max visible items in autocomplete dropdown (3-20)
  Type to search · Enter/Space to change · Esc to cancel
```
Aelix exposes 4 today; pi exposes ~23. Surface SettingsManager fields.

## P. Other commands

`/scope-models` (per-scope model defaults) — WP-7 pi-parity. `/fork` `/clone` — ✅ shipped (ADR-0129).
`/skills` — WP-7, list/run skills.

## Q. shift+tab mode cycling — WP-4 (claude-additive)

Mode set cycled by shift+tab: **default / plan mode / auto-accept edits / auto mode (classifier-evaluated) /
YOLO mode**. Requires the WP-0 permission engine + posture object. NOTE: "mode" currently means steering
(queue-drain) — must be disambiguated (distinct name + footer badge).

## R. Startup header — WP-5 (✅ shipped, ADR-0153)

```
  █████╗ ███████╗██╗     ██╗██╗  ██╗
 ██╔══██╗██╔════╝██║     ██║╚██╗██╔╝
 ███████║█████╗  ██║     ██║ ╚███╔╝
 ██╔══██║██╔══╝  ██║     ██║ ██╔██╗
 ██║  ██║███████╗███████╗██║██╔╝ ██╗
 ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚═╝  ╚═╝
------------- Aelix AI Agent --------------
cwd / base url / model / version
[Context]  AGENTS.md
[Tools]    [Skills]    [Hooks]    [Extensions]
  /help for commands  •  Ctrl+C interrupt
```
Shipped (with `[Extensions]` showing active runtime extensions). `Ctrl+C×2 exit` intentionally NOT advertised
(no such binding exists).

## S. Permission-approval prompt ("weird screen") — WP-4

Today the approval reuses the generic filter picker → shows a nonsensical "Type to search" hint on a yes/no,
truncates the command to 120 chars, no diff preview. Replace with a purpose-built approval dialog (command
syntax highlight, edit diff preview, 4 clear options, no filter hint).

## T. User-chat REPL distinction — WP-6 (✅ trivial tier shipped, ADR-0153)

Shipped: blank-line + bold-cyan echo via `render_user_message()`. Deferred (WP-6 medium): pi's full-width
background bubble (bg `#343541`, padding 1/1, no chevron).
