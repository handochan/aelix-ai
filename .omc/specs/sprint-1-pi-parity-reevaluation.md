# Aelix · Strategic Re-Evaluation Against Pi Parity (1st Principle)

Reviewer: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Verdict: **Aelix as currently shipped is a Pi-parity skeleton, not a Pi-parity superset.** 1st-principle realignment requires (a) demoting ADR-0011/0013/0014 to Phase 1.2 임시 결정, (b) re-opening ~10 of 16 scope cuts as scheduled work, and (c) migrating to a uv-workspaces monorepo at the Phase 1.3 boundary.

---

## 1. Executive Summary

The first-principle re-affirmation ("pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표") inverts the design weight of nine of fourteen ADRs. Most of those ADRs were not wrong on architecture, but they were written *under the assumption that Phase 1.2 needed to be small*; with parity restored to the top of the constraint stack, several of them now read as "we cut Pi features to ship faster", not "we intentionally diverged from Pi for a documented Aelix reason". The three structural recommendations:

1. **Demote three "binding contract" ADRs** (ADR-0011 hook catalogue v1, ADR-0013 message_end observational, ADR-0014 hook error policy) to **Phase 1.2 임시 결정**, supersede them with Pi-parity successors that pin the full Pi event set, the role-preserving `message_end` replacement reducer, and Pi's `"continue"` default error policy.
2. **Migrate to a uv-workspaces monorepo** at the Phase 1.3 boundary (`packages/aelix-ai/`, `packages/aelix-agent-core/`, `packages/aelix-coding-agent/`, later `packages/aelix-tui/`, `packages/aelix-web-ui/`, `packages/aelix-rpc/`).
3. **Re-scope the phase plan** so each phase exits with **Pi parity for one Pi package**, not "an Aelix slice that omits ten things".

The current code is solid Phase 1.1+1.2 work, but it is **not** "pi agent 완전 동일하게 완벽하게 구현". To get there, the user must accept that the previous "stop after Phase 1.2 with 16 events and silent-block guardrails" framing was a scope cut that needs to be re-opened.

---

## 2. ADR Re-Assessment Table

| ADR | Origin | Conflicts? | Recommended action |
|---|---|---|---|
| 0001 Use Aelix name | Aelix-specific | No | **Keep** |
| 0002 Small runtime kernel | Aelix-specific | Partial — "small" was misread as "fewer hooks"; Pi's `pi-agent-core` is ALSO small but includes tool execution + full hook bus | **Reword** to "Pi's `pi-agent-core` boundary" |
| 0003 Use pi agent as primary reference | Pi-parity | No | **Keep + strengthen**: "Any Aelix deviation must be explicit ADR; absent ADR, parity is binding." |
| 0004 Policy/Guardrail built-in extensions | Aelix divergence (Pi has neither) | Intentional | **Keep**, tighten scope: additions, not replacements |
| 0005 Multi-source marketplace | Aelix addition | No (Pi has no marketplace) | **Keep** |
| 0006 Standalone platform | Aelix positioning | No | **Keep** |
| 0007 In-process extension | Pi-parity | No | **Keep + strengthen with Pi citation** |
| 0008 Loop in core, orchestration in extensions | Pi-parity | No | **Keep** |
| 0009 Python-first SDK; multi-language deferred | Pi-parity-partial — Pi has RPC mode in 1.0 | **Yes** — RPC indefinitely deferred | **Demote** to "Phase 1.2 SDK choice", schedule RPC mode in Phase 4 (ADR-0020) |
| 0010 Source-specific trust | Pi-parity | No | **Keep** |
| **0011** Hook event catalogue v1 (16 events) | Pi-parity-narrowed (Pi has ~30) | **Yes — direct violation** | **Supersede** with ADR-0017 "Full Hook Event Catalogue v2" |
| **0013** message_end observational | Pi-parity-narrowed (Pi has replacement reducer) | **Yes — direct violation** | **Supersede** with ADR-0018 "message_end replacement reducer" |
| **0014** Hook error mutation throw / lifecycle swallow | Pi-parity-narrowed (Pi `"continue"` default) | **Yes — partial direct violation** | **Demote**, draft ADR-0019 "Hook Error Policy v2 — Pi continue default + per-handler errorMode override" |
| 0012 deferred Extension discovery | Pi-parity-partial | Indefinite deferral | **Schedule Phase 3** |
| 0015 deferred ExtensionContext UI | Pi-parity-narrowed | OK for Phase 1-4, required Phase 5 | **Schedule Phase 5** |
| 0016 deferred Phase machine expansion | Pi-parity-narrowed | Yes — compaction/branch_summary required | **Schedule Phase 2** |

### New ADR candidates (to draft)
- ADR-0015 Monorepo Layout (uv workspaces, mirrors Pi `packages/*`)
- ADR-0017 Full Hook Event Catalogue v2 (supersedes 0011)
- ADR-0018 message_end replacement reducer (supersedes 0013)
- ADR-0019 Hook Error Policy v2 — Pi `"continue"` default (supersedes 0014)
- ADR-0020 RPC Mode for Multi-Language Clients (concretizes 0009)
- ADR-0021 Parallel-Mode Tool Execution + Per-Tool Override
- ADR-0022 Session Manager + JSONL Persistence
- ADR-0023 Compaction + Branch Summary
- ADR-0024 Queue Default `"one-at-a-time"`

---

## 3. S-1 ~ S-16 Re-Classification

Key: (A) already-Pi-parity · (B) Phase-deferred — schedule · (C) re-open as 1.2 follow-up · (D) justified divergence.

| # | Scope cut | Aelix today | Pi state | Class | Phase |
|---|---|---|---|---|---|
| S-1 | `before_provider_request/payload` events | Absent | `agent-harness.ts:250-291,358-389` | B | Phase 2.1 |
| S-2 | `after_provider_response` event | Absent | `agent-harness.ts:376-380` | B | Phase 2.1 |
| S-3 | `message_end` observational | Pinned by ADR-0013 | Replacement reducer | C+B | ADR supersede now; impl Phase 2.1 |
| S-4 | Hook error mutation throws | Pinned by ADR-0014 | `"continue"` default | C | ADR supersede now; full impl Phase 2.1 |
| S-5 | `session_before_compact` no emit | Stub | Emitted in `harness.compact()` | B | Phase 2.2 |
| S-6 | `settled` event | Emitted (simpler payload) | Pi emits via `emitOwn` | A | Already aligned |
| S-7 | No `compact()` method | Absent | `agent-harness.ts:681-735` | B | Phase 2.2 |
| S-8 | No `navigateTree()` | Absent | `agent-harness.ts:737-835` | B | Phase 2.2 |
| S-9 | No `nextTurn()/appendMessage()` | Absent | `agent-harness.ts:664-679` | B | Phase 2.1 |
| S-10 | No setters (`setModel`/etc 8개) | `_action_set_active_tools` only | Pi has 8 setters | B+C | Phase 2.1; setActiveTools should be public now |
| S-11 | Parallel tool execution | Sequential-only | Per-tool override | B | Phase 2.1 |
| S-12 | ExtensionAPI 3 actions | 3 (vs Pi ~30) | Full extensible API | B | Phase 3 |
| S-13 | ExtensionContext 5 props | 5 (vs Pi 14) | Full ctx (incl UI) | B | Phase 3 non-UI; Phase 5 UI |
| S-14 | No JSONL session persistence | In-memory only | `Session` interface + JSONL | B | Phase 2.1/2.2 |
| S-15 | CLI demo-only | Echo demo | TUI/RPC/print + install/remove/login | B | Phase 4 RPC; Phase 5 TUI/install |
| S-16 | Queue default `"all"` | Aelix `"all"` | Pi `"one-at-a-time"` | **C** — undocumented divergence | **Flip now** |

**Summary:** 2 A, 9 B, 5 C.

---

## 4. Monorepo Recommendation

### Pi shape
- `packages/ai`, `packages/agent`, `packages/coding-agent`, `packages/tui`, `packages/web-ui`
- Each is a separately-published npm package
- npm workspaces

### Proposed Aelix mapping (uv workspaces)
```
aelix-mono/
├── pyproject.toml                 # [tool.uv.workspace] members = ["packages/*"]
└── packages/
    ├── aelix-ai/                  # ↔ pi packages/ai
    ├── aelix-agent-core/          # ↔ pi packages/agent
    ├── aelix-coding-agent/        # ↔ pi packages/coding-agent
    ├── aelix-tui/                 # ↔ pi packages/tui (Phase 5)
    ├── aelix-web-ui/              # ↔ pi packages/web-ui (Phase 6)
    └── aelix-rpc/                 # ↔ Pi --mode rpc (Phase 4)
```

### Migration cost
- ~2,700 LOC production + ~1,100 LOC tests redistribute
- Import-path rewrites (mechanical)
- Single `uv sync` workspace bootstrap
- uv 0.5+ workspace mature enough

### Recommendation: **Phase 1.3 boundary** (after Phase 1.2 commit)

Alternative: defer to Phase 3 (acceptable but increases accumulated cost).

---

## 5. Phase Re-Design (Pi-Parity Anchored)

Each phase = Pi parity for one Pi package.

### Phase 1 — `aelix-ai` Pi parity (CURRENT, ~80% done)
- Full `Message` / `AssistantMessage` / `UserMessage` / `ToolResultMessage` / content blocks ✓
- `Model`, `Cost`, `Context`, `SimpleStreamOptions`, `AssistantMessageEvent` union ✓
- `Tool`, `ToolResult`, `ToolExecutionContext`, `validate_tool_arguments` (real validator Phase 2) ✓
- `stream_simple` real implementation — MISSING (currently stub)

### Phase 2 — `aelix-agent-core` Pi parity (4-6 weeks)
**2.1 Loop + Harness completeness**
- Parallel-mode tool execution + per-tool override (S-11)
- `before_provider_request/payload/after_provider_response` events (S-1, S-2)
- `model_select`, `thinking_level_select`, `queue_update`, `save_point`, `abort` events
- All 8 harness setters (S-10): `setModel`, `setThinkingLevel`, `setActiveTools`, `setSteeringMode`, `setFollowUpMode`, `setResources`, `setStreamOptions`, `setTools`
- `nextTurn()`, `appendMessage()` (S-9)
- `pendingSessionWrites` queue + `flushPendingSessionWrites`
- Full 28 events in `HookEventName` Literal (ADR-0017)
- Queue defaults flip to `"one-at-a-time"` (S-16)
- Hook error policy v2: `"continue"` default + per-handler `error_mode` opt-in (ADR-0019)

**2.2 Session + Compaction + Branch Summary**
- `Session` interface + JSONL append-only storage (S-14)
- `appendModelChange`, `appendThinkingLevelChange`, `appendCompaction`, `appendCustomEntry`, `appendLabel`, `moveTo`
- `compact()` + `session_before_compact` + `session_compact` events (S-5, S-7)
- `navigateTree()` + `session_before_tree` + `session_tree` (S-8)
- Phase machine: `idle | turn | compaction | branch_summary` (ADR-0016 successor)
- `message_end` replacement reducer with role preservation (S-3, ADR-0018)

### Phase 3 — `aelix-coding-agent` Pi parity (UI excluded, 6-7 weeks)
**3.1 Session manager + ExtensionAPI surface**
- `ReadonlySessionManager`, `SessionManager`
- `ModelRegistry`
- Full Pi `ExtensionAPI` minus UI (S-12)
- Full Pi `ExtensionContext` minus UI (S-13)
- Auto-discovery (ADR-0012 successor)

**3.2 Built-in coding tools**
- `bash`, `read`, `edit`, `write`, `grep`, `find`, `ls`
- `input` event with transform/handled actions
- `user_bash` event
- `resources_discover` event

### Phase 4 — Real Providers + RPC Mode (3-4 weeks)
- `aelix_ai.providers.anthropic/openai/openrouter`
- OAuth flow
- `aelix-rpc` package — Pi `--mode rpc` parity (ADR-0020)

### Phase 5 — TUI / Web UI / CLI subcommands (5-6 weeks)
- `aelix-tui` (`textual` likely)
- `aelix-web-ui` (FastAPI + frontend)
- Full `ExtensionUIContext` (ADR-0015 successor)
- `aelix install/remove/login/logout/--print/mode rpc` CLI

### Phase 6 — Marketplace polish + multi-language clients (open)
- Multi-source index per ADR-0005
- Cross-source trust verdicts
- Sample multi-language clients (Java, Go, Rust) via Phase 4 RPC

---

## 6. Wrong-Information / Wrong-Assumption Findings

- **F-1.** `AgentOptions.steering_mode = "all"` (`agent/agent.py:43-44`) — Pi default `"one-at-a-time"`. Undocumented divergence. **Fix:** flip default.
- **F-2.** ADR-0011 framed as "binding contract" too early — pin is on 16 events not Pi's 30. **Fix:** restate as "registry mechanism binding; catalogue mutable in line with Pi parity"; ADR-0017 lands full set.
- **F-3.** ADR-0014 "mutation throws" doesn't survive Pi-parity test. Pi `"continue"` deliberate. **Fix:** ADR-0019, Aelix default `"continue"`, `errorMode="throw"` opt-in.
- **F-4.** ADR-0013 overstates `message_end` replacement complexity — ~20 LOC fix when scheduled. **Fix:** ADR-0018 Phase 2.1.
- **F-5.** ADR-0009 indefinitely defers multi-language clients. Pi ships RPC in 1.0. **Fix:** ADR-0020 schedules Phase 4.
- **F-6.** `AgentHarnessOptions` missing `session/env/resources/thinkingLevel/activeToolNames/getApiKeyAndHeaders/streamOptions` (`harness/core.py:103-126`). **Fix:** add placeholder fields with `None` defaults as Phase 2 lands them.
- **F-7.** Aelix conflates loop `AgentEvent` (10) with harness `AgentHarnessEvent` (30). **Fix:** distinguish in code; encode in ADR-0017.
- **F-8.** `ExtensionContext.model` is `Model | None`; Pi uses `Model<any> | undefined` with API generic. Acceptable Python gap; document.
- **F-9.** `_action_set_active_tools` is destructive (`harness/core.py:342-344`) — drops tools. Pi separates `tools` (all) from `activeToolNames` (subset). **Fix:** Phase 2.1 — add `active_tool_names` to `AgentState`.
- **F-10.** `_emit_before_agent_start` doesn't rebuild between turns (`harness/core.py:458-473`). Pi's `createTurnState` rebuilds per turn. **Fix:** Phase 2.1.
- **F-11.** `convert_to_llm` type duplicated. **Fix:** define `ConvertToLlmFn` alias once.
- **F-12.** Lazy `__getattr__` re-export brittle. **Fix:** resolves with monorepo split.

---

## 7. Action Plan

### 7.1 Before Phase 1.2 commit
1. **Decide** monorepo timing (now vs Phase 1.3 vs Phase 3)
2. **Demote** ADR-0011/0013/0014 status (`Status: Accepted (Phase 1.2 임시 결정)` + `Superseded by: ADR-NNNN`)
3. **Draft** ADR-0017~0024 as `Status: Draft` (one paragraph each minimum)
4. **Flip** queue default to `"one-at-a-time"` (F-1)
5. **Update** decisions/README.md Index

### 7.2 Phase 1.3 (next sprint)
1. Monorepo migration sprint (1-2 days mechanical) + ADR-0015
2. Phase 1.2 Pi-parity follow-ups: F-9 (non-destructive set_active_tools), F-11 (type alias), F-12 (static re-export)

### 7.3 Phase 2.1 (4-6 weeks)
S-1, S-2, S-3 reducer, S-4 error v2, S-9, S-10, S-11. ADR-0017, 0018, 0019, 0021.

### 7.4 Phase 2.2 (2-3 weeks)
S-5, S-7, S-8, S-14. ADR-0022, 0023.

### 7.5 Phase 3 (4-5 weeks)
S-12, S-13 (non-UI). ADR-0012 successor.

### 7.6 Phase 4 (3-4 weeks)
Real providers + RPC mode (S-15 partial). ADR-0020.

### 7.7 Phase 5 (5-6 weeks)
TUI/Web UI/CLI subcommands. ADR-0015 successor.

### 7.8 Phase 6 (open-ended)
Marketplace + multi-language clients + customer-site polish.

### 7.9 New test plan deltas (Pi-parity-pinned)
- `test_hook_event_name_literal_matches_pi_event_set`
- `test_parallel_tool_execution_respects_per_tool_override`
- `test_message_end_replacement_preserves_role`
- `test_hook_error_continue_default`
- `test_compact_emits_session_before_compact`
- `test_navigate_tree_summary_short_circuits_on_cancel`
- `test_pi_extension_api_method_count`
- `test_rpc_mode_handles_pi_protocol_v1_messages`

---

## 8. Open Questions to Resolve with User

1. **Monorepo timing:** migrate now vs Phase 1.3 boundary vs defer to Phase 3?
2. **Queue default flip:** Flip `steering_mode`/`follow_up_mode` defaults to `"one-at-a-time"` immediately?
3. **Hook error policy v2 scope:** `"continue"` default for ALL hooks (Pi parity) or keep mutation as `"throw"` for safety?
4. **Aelix-specific divergences to preserve:** built-in `PolicyExtension`/`GuardrailExtension` (ADR-0004) — confirm stay as documented divergences?
5. **Phase boundary semantics:** strict "Pi parity superset" (no documented omissions allowed) vs softer "Pi parity superset minus N documented items" per phase?

---

End of report.
