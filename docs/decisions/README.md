# Architecture Decision Records

이 폴더는 Aelix 설계의 중요한 결정을 ADR(Architecture Decision Record) 형식으로
보관합니다. ADR 형식과 상태 규칙은 `../00-conventions.md`를 따릅니다.

## Status 값

- `Draft`: 논의 중인 결정
- `Accepted`: 현재 설계 기준
- `Accepted (Phase 1.2 임시 결정)`: Phase 1.2 scope 하에 결정되었으나 Pi parity 관점에서 후속 ADR로 대체 예정
- `Deprecated`: 폐기되었지만 역사적 맥락 때문에 보존
- `Superseded`: 다른 ADR로 대체됨 (`Superseded by:` 명시)

## Index

| #    | Title                                                                                                                                                  | Status                              | 한 줄 결정                                                                                                                                          |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0001 | [Use Aelix as the Product Name](0001-use-aelix-as-product-name.md)                                                                                     | Accepted                            | 프로젝트와 플랫폼 이름은 `Aelix`를 사용한다.                                                                                                        |
| 0002 | [Start with a Small Runtime Kernel](0002-start-with-small-runtime-kernel.md)                                                                           | Accepted                            | Aelix runtime core는 Pi `pi-agent-core` 경계(loop + hook bus + extension runtime + session manager)와 동일. Multi-agent orchestration은 core 밖.    |
| 0003 | [Use pi agent as the Primary Reference](0003-use-pi-agent-as-primary-reference.md)                                                                     | Accepted                            | pi agent를 primary reference로 두고 Python으로 충실히 재구현. ADR 없는 divergence는 버그.                                                          |
| 0004 | [Policy and Guardrail as Built-in Extensions](0004-policy-and-guardrail-as-builtin-extensions.md)                                                      | Accepted                            | runtime core는 정책을 강제하지 않고, built-in extension이 lifecycle hook으로 강제한다.                                                              |
| 0005 | [Marketplace Supports Multi-Source Indexes](0005-marketplace-multi-source-indexes.md)                                                                  | Accepted                            | marketplace는 npm / git / 사내 custom index를 1st-class source로 지원하고, 단일 manifest 형식을 강제하지 않는다.                                    |
| 0006 | [Aelix is a Standalone Platform](0006-aelix-is-a-standalone-platform.md)                                                                               | Accepted                            | Aelix는 standalone runtime/platform이며 DeepSight는 use case 중 하나로만 다룬다.                                                                    |
| 0007 | [In-Process Extension Execution by Default](0007-in-process-extension-execution.md)                                                                    | Accepted                            | extension은 in-process로 실행하며, isolation은 runtime protocol에 두지 않는다. 강한 격리는 OS/sandbox extension/index trust로 흡수한다.              |
| 0008 | [Agent Loop in Core, Orchestration in Extensions](0008-agent-loop-in-core-orchestration-in-extensions.md)                                              | Accepted                            | 단일 agent loop는 kernel 책임, multi-agent / planner / subagent orchestration은 extension layer 책임.                                              |
| 0009 | [Python-First SDK; Multi-Language Clients via Future RPC ADR](0009-python-first-sdk.md)                                                                | Accepted (Phase 1.2 SDK choice)     | 1차 SDK는 Python만. 다언어 외부 클라이언트는 Phase 4 ADR-0020 RPC mode로 결정.                                                                     |
| 0010 | [Trust Model Stays Source-Specific; No Unified Schema in 1st Cut](0010-trust-model-stays-source-specific.md)                                           | Accepted                            | 1차에는 통합 trust verdict schema를 정의하지 않는다. ADR-0005의 source-책임 모델 유지.                                                              |
| 0011 | [Hook Event Catalogue v1](0011-hook-event-catalogue-v1.md)                                                                                             | Accepted (Phase 1.2 임시 결정)      | Phase 1.2가 ship하는 16개 hook event를 binding contract으로 등록. Phase 2.1에 ADR-0017로 대체.                                                      |
| 0012 | Extension Discovery Model                                                                                                                              | Deferred                            | Phase 3에서 결정. ADR-0028로 부분 해소 예정. (`~/.aelix/extensions`, `entry_points` 우선순위 결정됨.)                                              |
| 0013 | [message_end Reducer Policy — Observational in Phase 1.2](0013-message-end-observational-in-1-2.md)                                                    | Accepted (Phase 1.2 임시 결정 — Sprint 3b 재확정: message_end는 영구 observational)      | Phase 1.2의 `message_end`는 observational only. Sprint 3b P-3 verdict로 영구 확정(ADR-0018 Deprecated, Pi has no replacement reducer @ SHA 734e08e). |
| 0014 | [Hook Error Policy — Mutation hooks throw; Lifecycle observational hooks swallow+log](0014-hook-error-policy.md)                                       | Superseded by ADR-0019              | (Phase 1.2 임시 결정) — Sprint 3a에서 ADR-0019 v3로 대체됨.                                                                                          |
| 0015 | [Monorepo Layout — uv Workspaces](0015-monorepo-layout-uv-workspaces.md)                                                                               | Accepted (Phase 1.3 shipped)        | Phase 1.3 경계에서 uv workspaces 마이그레이션. Pi `packages/*` 구조와 1:1 매핑.                                                                    |
| 0017 | [Full Hook Event Catalogue v2](0017-full-hook-event-catalogue-v2.md)                                                                                   | Accepted (Sprint 3a / Phase 2.1.1 shipped) | Pi-verified 28 hook events (10 loop + 18 harness own). 7 wishlist events dropped per W1 Finding P-1. ADR-0011 대체.                                |
| 0018 | [message_end Replacement Reducer (Pi parity)](0018-message-end-replacement-reducer.md)                                                                  | Deprecated (Sprint 3b — superseded by P-3 verdict)                   | **Deprecated** — Pi has no message_end reducer at SHA 734e08e. Sprint 3b §0 verdict: keep `message_end` observational, no replacement reducer.       |
| 0019 | [Hook Error Policy v2 — Pi `"throw"` Default + Aelix `"continue"` Opt-in](0019-hook-error-policy-v2-pi-continue-default.md) | Accepted (Sprint 3a / Phase 2.1.1 shipped — v3 reframe) | Pi-parity `"throw"` default (matches Pi `normalizeHookError`) + Aelix additive `error_mode="continue"` opt-in. v3 reframe per W1 Finding P-2. ADR-0014 대체. |
| 0020 | [RPC Mode for Multi-Language Clients](0020-rpc-mode-multi-language-clients.md)                                                                          | Accepted (Sprint 6d / Phase 4.4 / W6 shipped) | `aelix --mode rpc` JSONL stdin/stdout protocol. Sprint 6d shipped 9 supported + 20 deferred (ADR-0058). Sub-sprints 6e/6f close the remaining surface.                                                     |
| 0021 | [Parallel-Mode Tool Execution + Per-Tool Override](0021-parallel-tool-execution.md)                                                                     | Accepted (Sprint 3c / Phase 2.1.3 shipped) | default parallel 실행 (`asyncio.gather` per P-7 reversal) + per-tool `execution_mode="sequential"` override.                                       |
| 0022 | [Session Manager + JSONL Persistence](0022-session-manager-jsonl-persistence.md)                                                                        | Accepted (Sprint 4a / Phase 2.2.1 shipped)                   | `Session` concrete class (17+1 methods) + `SessionStorage` Protocol + `JsonlSessionRepo`. 8-variant `PendingSessionWrite`. `message_end` wired. P-11 reversal (`PendingActiveToolsChangeWrite` deleted). |
| 0023 | [Compaction + Branch Summary](0023-compaction-branch-summary.md)                                                                                        | Accepted (Sprint 4b / Phase 2.2.2 shipped)                   | `compact()` + `navigate_tree()` + Phase machine `idle\|turn\|compaction\|branch_summary` + 4 session_* emit sites. ADR-0016 대체. P-14/P-15/P-16 verified.                                             |
| 0024 | [Queue Default `"one-at-a-time"` (Pi parity)](0024-queue-default-one-at-a-time.md)                                                                      | Accepted (Phase 1.2 follow-up fix)  | `steering_mode` / `follow_up_mode` default를 `"all"` → `"one-at-a-time"`으로 즉시 flip.                                                            |
| 0025 | [F-10 Minimal Turn-State Snapshot Rationale](0025-f10-minimal-turn-state-snapshot.md)                                                                   | Accepted (Phase 1.3 shipped)        | `_TurnState` 2-field minimal snapshot은 의도적. 나머지 7 fields는 owning ADR (0017/0022) land 시 확장.                                             |
| 0026 | [Workspace-Root Pytest Layout](0026-workspace-root-pytest-layout.md)                                                                                    | Accepted (Sprint 2 shipped)         | workspace-root 공유 `tests/` 유지. cross-package fixture 중복 방지. Pi additive divergence.                                                        |
| 0027 | [asyncio.TaskGroup for Parallel Tool Execution](0027-asyncio-taskgroup-parallel-tools.md)                                                               | Accepted (Sprint 3c / Phase 2.1.3 shipped — DECISION REVERSED to asyncio.gather) | P-7 reversal: `asyncio.gather(*coros, return_exceptions=False)` — Pi never cancels siblings on tool error, TaskGroup auto-cancel would be Pi divergence. ADR-0021 구현 binding. |
| 0028 | [Extension Auto-Discovery — Directory Scan (Pi Parity) + entry_points (Aelix-Additive)](0028-extension-auto-discovery-entry-points.md) | **Accepted (Sprint 5a / Phase 3.1.1 shipped — P-21 reversal)** | **Directory scan PRIMARY (Pi parity), entry_points ADDITIVE.** Sprint 2 Draft 명세를 P-21 검증으로 반전. ADR-0012 partial supersede. |
| 0029 | [Pi-Parity Acceptance Test Harness](0029-pi-parity-acceptance-test-harness.md)                                                                          | Draft (Phase 2.1+ ongoing)          | `tests/pi_parity/` 별도 lane. vendored Pi fixture + message-level equivalence assert. "믿는다" → "증명한다".                                       |
| 0030 | [Hook Event Exhaustiveness via assert_never](0030-hook-event-exhaustiveness-assert-never.md)                                                             | Accepted (Sprint 3a / Phase 2.1.1 shipped) | `match`+`assert_never` 패턴 코드-land. `_to_hook_event`에 적용. 새 event 미처리 시 pyright build fail.                                            |
| 0031 | [Build Backend Choice — Hatchling](0031-build-backend-hatchling.md)                                                                                     | Accepted (Sprint 2 shipped)         | 모든 packages/* hatchling 사용. declarative `src/` layout 지원. uv workspace first-class.                                                          |
| 0032 | [Sprint Workflow — Review + Pi Parity + Suggestions](0032-sprint-workflow-review-parity-suggestions.md)                                                 | Accepted (Sprint 2 onwards)         | W4 code-review + W5 Pi parity audit mandatory gate. bypass는 새 ADR 필요.                                                                          |
| 0033 | ExtensionContext UI surface                                                                                                                             | Deferred (Phase 5)                  | `ExtensionUIContext` 전체 표면. Phase 5 TUI/Web UI 구현 시 결정. (구 spec의 "ADR-0015" 항목 → 번호 충돌로 재배정.)                                 |
| 0034 | [Pi Reference Version Pin](0034-pi-reference-version-pin.md)                                                                                            | Accepted (Sprint 2.5 shipped)       | Pi reference를 commit SHA로 sprint별 pinning. 현재 pin: `734e08edf82ff315bc3d96472a6ebfa69a1d8016`.                                                |
| 0035 | [Error Code Taxonomy](0035-error-code-taxonomy.md)                                                                                                       | Draft (Phase 1.4 shipped — Aelix subset; full taxonomy Phase 2.1+) | Pi 10-code 표준 문서화. Aelix 5 codes 유지 + 5 placeholder는 owning ADR land 시 widen.                                                              |
| 0036 | [Loop AgentEvent vs Harness HookEvent Distinction (F-7)](0036-loop-event-vs-harness-event-distinction.md)                                              | Accepted (Sprint 3a — code-land: AgentEventName + AgentHarnessEventName aliases) | Loop `AgentEventName` (10) ≠ Harness `AgentHarnessEventName` (18). 두 union 별도 유지 + 통합 `HookEventName` union.                                |
| 0037 | [Streaming Event Union (Pi Parity)](0037-streaming-event-union-pi-parity.md)                                                                            | Draft (Phase 1.4 shell; adapter coverage Phase 4)                  | Pi 12-event union을 Phase 4 adapter PR에서 land. Phase 1.4는 design 문서만.                                                                          |
| 0038 | [stream_simple Dispatch Shell — Phase 1 Boundary](0038-stream-simple-dispatch-shell-phase-1-boundary.md)                                                | Accepted (Sprint 2.5 / Phase 1.4 shipped — body lands Phase 4)     | Phase 1 exit는 dispatch shell + registry + typed error에서 완료. Adapter는 Phase 4.                                                                |
| 0039 | [Phase 2.1 Strict Superset Closure](0039-phase-2-1-strict-superset-closure.md)                                                                          | Accepted (Sprint 3d / Phase 2.1.4 shipped)                          | Phase 2.1 strict superset closure — P-1..P-9 roster + E.5 closure pin + deferred allowlist (forward-compat clause).                                  |
| 0040 | [Phase 2.2 Strict Superset Closure](0040-phase-2-2-strict-superset-closure.md)                                                                          | Accepted (Sprint 4b shipped)                                        | Phase 2.2 strict Pi-parity superset closure (P-11~P-20 roster + closure pin).                                                                        |
| 0041 | [Phase 3.1 Extension API Full Surface Closure](0041-phase-3-1-extension-api-full-surface-closure.md)                                                    | Accepted (Sprint 5a / Phase 3.1.1 shipped)                          | Extension auto-discovery (Pi-primary), ExtensionAPI 48-method surface, ExtensionContext 14 fields, 3 new events registered (input/user_bash/resources_discover). P-21~P-28 roster + 4-week time-bound deferral. |
| 0042 | [Built-in Coding Tools + 3 Event Emit Sites + Minimal CLI Loop](0042-built-in-coding-tools.md)                                                          | Accepted (Sprint 5b / Phase 3.2 shipped)                            | 7 Pi-parity built-in coding tools (bash/read/edit/write/grep/find/ls) + 3 event emit sites active + minimal CLI loop + ExtensionCommandContext 4-of-6 lands. P-32~P-36. |
| 0043 | [Tool-Typed ToolCallEvent Variants](0043-tool-typed-tool-call-event-variants.md)                                                                        | Accepted (Sprint 5b / Phase 3.2 shipped)                            | 8 typed `ToolCallEvent` + 8 symmetric `ToolResultEvent` subclasses on existing base classes, `make_tool_call_event` factory, `is_tool_call_event_type` narrow helper. P-31. |
| 0044 | [Phase 3 Strict Superset Closure](0044-phase-3-strict-superset-closure.md)                                                                              | Accepted (Sprint 5b / Phase 3.2 shipped)                            | Phase 3 closure — P-21~P-36 roster + closure pin (`tests/pi_parity/test_phase_3_2_strict_superset.py`) + deferred allowlist Phase-4-only. |
| 0045 | [Provider Adapter Interface](0045-provider-adapter-interface.md)                                                                                        | Accepted (Sprint 6a / Phase 4.1 shipped)                            | `Provider` Protocol + `register_provider_object` / `unregister_providers_by_source` (Pi parity) + `SimpleStreamOptions` extensions + `ProviderResponse`. Anthropic adapter ships under `aelix_ai.providers.anthropic`. |
| 0046 | [Phase 4 Strict Superset Closure](0046-phase-4-strict-superset-closure.md)                                                                              | Accepted (Sprint 6a / Phase 4.1 shipped)                            | Phase 4 closure — P-37~P-43 roster + closure pin (`tests/pi_parity/test_phase_4_strict_superset.py`) + DEFERRED_ALLOWLIST = `{}` (empty). |
| 0047 | [OpenAI Completions Adapter (+ Compat Detection)](0047-openai-completions-adapter.md)                                                                   | Accepted (Sprint 6b / Phase 4.2 shipped)                            | OpenAI Completions adapter (2nd of 9 KnownApi adapters); OpenRouter served via same adapter; 17-field compat dataclass; sync-factory `stream_simple` + eager auth. |
| 0048 | [Pi Shared Utilities Ported](0048-pi-shared-utilities.md)                                                                                                | Accepted (Sprint 6b / Phase 4.2 shipped)                            | `_transform_messages` + `_sanitize_unicode` + `_streaming_json` + `_env_api_keys` shared cross-provider infra; Anthropic retrofit deferred to Sprint 6d (P-50-followup). |
| 0049 | [Message Dataclass — Provenance + Thinking + Image Split + Tool Name](0049-message-dataclass-provenance-and-thinking.md)                                | Accepted (Sprint 6b / Phase 4.2 / W6 shipped)                       | Additive `ThinkingContent`, `AssistantMessage.api/provider/model`, `ImageContent.mime_type/data`, `ToolResultMessage.tool_name`. Anthropic-side population deferred to Sprint 6d. |
| 0050 | [Phase 4.2 Strict Superset Closure](0050-phase-4-2-strict-superset-closure.md)                                                                          | Accepted (Sprint 6b / Phase 4.2 / W6 shipped)                       | Phase 4.2 closure — P-47~P-82 + C-1 + M-1..M-6 roster + closure pin (`tests/pi_parity/test_phase_4_2_strict_superset.py`) + `PHASE_4_2_DEFERRED_APIS` (7) + `COMPAT_DEFERRED_ALLOWLIST` (4+2). |
| 0051 | [OAuth Client Framework](0051-oauth-client-framework.md)                                                                                                | Accepted (Sprint 6c / Phase 4.3 / W6 shipped)                       | OAuth `Protocol` + types + PKCE + callback server + registry + high-level `get_oauth_api_key_from_credentials`. Separate registry from API providers (ADR-0045). |
| 0052 | [Anthropic OAuth Flow](0052-anthropic-oauth-flow.md)                                                                                                    | Accepted (Sprint 6c / Phase 4.3 / W6 shipped)                       | Anthropic OAuth flow port (PKCE + local callback + token exchange/refresh + constants) + Bearer header injection (P-94) + late-manual-input fallback (P-93) + CALLBACK_HOST wiring (P-98). |
| 0053 | [AuthStorage and Secrets](0053-auth-storage-and-secrets.md)                                                                                              | Accepted (Sprint 6c / Phase 4.3 / W6 shipped — Anthropic only)      | `AuthStorage` JSON layer (atomic tmp+fsync+rename write + 0o700/0o600 perms + asyncio.Lock + fcntl.flock POSIX cross-process). Copilot/Codex + layered cascade deferred to Sprint 6e. |
| 0054 | [RPC Mode Deferred to Sprint 6d](0054-rpc-mode-deferred-to-sprint-6d.md)                                                                                 | Accepted (Sprint 6c / Phase 4.3 / W6 — formal carry-forward)        | Formal carry-forward record for `rpc-mode` / `rpc-client` / `rpc-types` / `jsonl` (~1,310 Pi LOC). `_PHASE_4_DEFERRED_FEATURES["rpc-mode"]` owns. |
| 0055 | [Phase 4.3 Strict Superset Closure](0055-phase-4-3-strict-superset-closure.md)                                                                          | Accepted (Sprint 6c / Phase 4.3 / W6 shipped)                       | Phase 4.3 closure — P-83~P-104 + W4 M1..M6 + W4 m1..m9 roster + closure pin (`tests/pi_parity/test_phase_4_3_strict_superset.py`) + `_OAUTH_DEFERRED_PROVIDERS` (2) + `_PHASE_4_DEFERRED_FEATURES` (2). |
| 0056 | [RPC JSONL Protocol](0056-rpc-jsonl-protocol.md)                                                                                                        | Accepted (Sprint 6d / Phase 4.4 / W6 shipped)                       | LF-only framing + UTF-8 incremental decode + CR strip + tail-on-end. Pi `jsonl.ts` (58 LOC) parity. `ensure_ascii=False` preserves U+2028/U+2029 inside string payloads. |
| 0057 | [RPC Types and Envelope](0057-rpc-types-and-envelope.md)                                                                                                | Accepted (Sprint 6d / Phase 4.4 / W6 shipped)                       | 29 RpcCommand variants + uniform 24 success + 1 error envelope + 12-field RpcSessionState + 9-method RpcExtensionUIRequest + 3-shape RpcExtensionUIResponse (TYPES only). |
| 0058 | [Phase 4.4 Strict Superset Closure](0058-phase-4-4-strict-superset-closure.md)                                                                          | Accepted (Sprint 6d / Phase 4.4 / W6 shipped)                       | Phase 4.4 closure — P-105~P-129 + W4 M1..M5 + W4 m1..m10 roster + closure pin (`tests/pi_parity/test_phase_4_4_strict_superset.py`) + `DEFERRED_COMMANDS` (20) covering 29 - 9 = 20 deferred Pi RpcCommand variants. |
| 0059 | [GitHub Copilot OAuth (Device-Code Flow)](0059-copilot-oauth-device-flow.md)                                                                          | Accepted (Sprint 6e / Phase 4.5 / W6 shipped)                       | Pi parity port of Copilot device-code grant + enterprise domain (`enterpriseUrl` camelCase) + `proxy-ep` → `Model.base_url` injection via `modify_models` Protocol callback (Sprint 6c P-102 closed). |
| 0060 | [OpenAI Codex OAuth (PKCE Callback Flow)](0060-codex-oauth-pkce-flow.md)                                                                                | Accepted (Sprint 6e / Phase 4.5 / W6 shipped)                       | Pi parity port of Codex PKCE callback at port 1455 path `/auth/callback` + JWT `accountId` extraction at claim `https://api.openai.com/auth` + `originator=pi` + RFC 7519 §3 base64url decode (Pi `atob` bug correction). |
| 0061 | [AuthStorage Layered Cascade](0061-auth-storage-layered-cascade.md)                                                                                      | Accepted (Sprint 6e / Phase 4.5 / W6 shipped)                       | Pi parity port of 12 `AuthStorage` cascade methods (`set_runtime_api_key` / `set_fallback_resolver` / `has_auth` / `get_auth_status` / `list` / `has` / `get_all` / `drain_errors` / `login` / `logout` / `get_api_key_cascade` / `remove_runtime_api_key`) + `AuthStatus` (frozen) + `AuthSource` 6-value Literal + `FallbackResolver` + `resolveConfigValue` helper. P-141 / P-142. |
| 0062 | [`aelix auth` CLI Subcommand](0062-aelix-auth-cli-subcommand.md)                                                                                          | Accepted (Sprint 6e / Phase 4.5 / W6 shipped)                       | `aelix auth login/logout/status/list` subparser preserving Sprint 6d `--mode {interactive,rpc}` back-compat. P-152 unknown-provider exit 2 + n1 RuntimeError exit 1. |
| 0063 | [Phase 4.5 Strict Superset Closure](0063-phase-4-5-strict-superset-closure.md)                                                                          | Accepted (Sprint 6e / Phase 4.5 / W6 shipped)                       | Phase 4.5 closure — P-130~P-162 + W4 M1 + W4 m1..n3 roster + closure pin (`tests/pi_parity/test_phase_4_5_strict_superset.py`) + `_OAUTH_DEFERRED_PROVIDERS` drained to `{}` (3/3 Pi providers live) + 12 cascade methods present. |
| 0064 | [Model Cost + Thinking + Headers Fields](0064-model-cost-and-thinking-fields.md)                                                                        | Accepted (Sprint 6f / Phase 4.6 / W6 shipped)                       | `ModelCost` (frozen per-million rate) + `UsageCost` (mutable resolved) + `Usage` + `Cost = ModelCost` back-compat alias + Pi `Model.thinking_level_map` / `max_tokens` / `context_window` / `headers` (P-178). |
| 0065 | [ModelRegistry Runtime](0065-model-registry-runtime.md)                                                                                                  | Accepted (Sprint 6f / Phase 4.6 / W6 shipped)                       | Pi parity port of 14-method `ModelRegistry` + 2 factory constructors (`create` / `in_memory`) + `ResolvedRequestAuth` discriminated union (P-180 bool) + `ProviderConfigInput` + P-175/P-176/P-184 wire fixes + P-187 `set_current_model` writes `_state.model` directly. |
| 0066 | [Phase 4.6 Strict Superset Closure](0066-phase-4-6-strict-superset-closure.md)                                                                          | Accepted (Sprint 6f / Phase 4.6 / W6 shipped)                       | Phase 4.6 closure — P-163~P-187 roster + closure pin (`tests/pi_parity/test_phase_4_6_strict_superset.py`) + ModelRegistry 14 methods present + 7 Pi helpers exposed + 13-model seed catalog + `DEFERRED_COMMANDS` 20 → 17 (set_model/cycle_model/get_available_models live). |
| 0067 | [Model Resolver Port + Full Pi Catalog Data Transfer](0067-model-resolver-and-catalog.md)                                                              | Accepted (Sprint 6g₁ / Phase 4.7 / W6 shipped)                      | Pi parity port of `model-resolver.ts` (637 LOC, 7 functions + 3 helpers) + `defaultModelPerProvider` (32 rows) + full 942-model JSON catalog data transfer + `KnownProvider` Pi semantic order (P-208) + `Model.compat` passthrough (`_openai_compat.get_compat` merge confirmed wired, P-210) + `_glob_match_pi_minimatch` (P-207) + `RestoreModelResult` typed dataclass (P-206). |
| 0068 | [Phase 4.7 Strict Superset Closure](0068-phase-4-7-strict-superset-closure.md)                                                                          | Accepted (Sprint 6g₁ / Phase 4.7 / W6 shipped)                      | Phase 4.7 closure — P-197~P-215 roster (W0 P-197..P-204 + W4/W5 P-205..P-215) + closure pin (`tests/pi_parity/test_phase_4_7_strict_superset.py`, 32 tests) + `KnownProvider` Pi semantic order + `DEFAULT_THINKING_LEVEL == "medium"` (P-205) + `Model.compat` field + `RestoreModelResult` shape + glob `/`-boundary. Sprint 6g₂/6g₃/6h carry-forward enumerated. |
| 0069 | [Prompt-Templates + Skills + `get_commands` RPC](0069-prompt-templates-and-skills.md)                                                                  | Accepted (Sprint 6h₁ / Phase 4.8 / W6 shipped)                      | Pi parity port of `harness/prompt-templates.ts` (~380 LOC) + `harness/skills.ts` (~540 LOC) + `_handle_get_commands` (Pi `rpc-mode.ts:622-653` aggregates 3 sources) + `ResolvedCommand` Pi `{name}:{N}` disambiguation (P-224 BLOCKING) + Pi-shape `{path, source, scope, origin}` `sourceInfo` wire (P-225 BLOCKING) + `ResolvedCommand` forwards source_info (P-229 BLOCKING) + `PromptTemplate` empty defaults (P-226 MAJOR) + shared `_frontmatter` parser (W4 m4) + YAML error surface (P-233) + case-insensitive `.md` strip (P-234). |
| 0070 | [Phase 4.8 Strict Superset Closure](0070-phase-4-8-strict-superset-closure.md)                                                                          | Accepted (Sprint 6h₁ / Phase 4.8 / W6 shipped)                      | Phase 4.8 closure — P-216~P-244 roster (W0 P-216..P-223 + W4/W5 P-224..P-244) + closure pin (`tests/pi_parity/test_phase_4_8_strict_superset.py`) + 13 supported / 16 deferred RPC split + 22+ W6 regression tests. Sprint 6h₂ / 6h₃ carry-forward enumerated. |
| 0071 | [9 RPC Commands + Harness Setters](0071-9-rpc-commands-and-harness-setters.md)                                                                          | Accepted (Sprint 6h₂ / Phase 4.9 / W6 shipped)                      | Pi parity port of 9 RPC handlers + 5 harness setters + 4 AgentState fields + 1 `_MessageQueue.set_mode` helper + 2 public properties + cycle algorithm with `supportsThinking()` guard (P-254 BLOCKING) + strict `_decode_images` (P-262 BLOCKING) + keyword-only `images` (P-263 MAJOR) + `auto_retry_enabled` wire surface (P-264 BLOCKING) + `_MessageQueue.set_mode` validation (P-265 BLOCKING) + line citation corrections (P-258 BLOCKING). |
| 0072 | [Phase 4.9 Strict Superset Closure](0072-phase-4-9-strict-superset-closure.md)                                                                          | Accepted (Sprint 6h₂ / Phase 4.9 / W6 shipped)                      | Phase 4.9 closure — P-245~P-267 roster (W0 P-245..P-253 + W4/W5 P-254..P-267) + closure pin (`tests/pi_parity/test_phase_4_9_strict_superset.py`, 28 tests) + 22 supported / 7 deferred RPC split + 11 W6 regression pins. Sprint 6h₃ carry-forward enumerated (5 session-tree + 2 session-inspection commands + Pi `SettingsManager` port + retry loop + queue_update wire shape + steer expanders). |
| 0073 | [Session Stats + HTML Export Wire Port](0073-session-stats-and-html-export.md)                                                                          | Accepted (Sprint 6h₃ / Phase 4.10 / W6 shipped)                     | Pi parity port of `SessionStats` (`agent-session.ts:212-223`) + `getSessionStats` aggregator (`agent-session.ts:2901-2945`) + minimal HTML emitter (`coding-agent/src/core/export-html/`) + 2 RPC handlers (`get_session_stats` / `export_html`). Pi-shape `contextUsage {tokens, contextWindow, percent}` (P-275 BLOCKING) + `totalMessages = len(messages)` (P-276 BLOCKING) + Pi error parity on `export_to_html` (P-279 MAJOR) + Pi-shape default `outputPath = aelix-session-<basename>.html` (P-281 MAJOR) + aggregator dict-shape fallback via `_read` (P-283) + W4 HIGH `hasattr` dead branch drop (P-292) + line citation corrections (P-277/P-278/P-286). |
| 0074 | [Phase 4.10 Strict Superset Closure](0074-phase-4-10-strict-superset-closure.md)                                                                        | Accepted (Sprint 6h₃ / Phase 4.10 / W6 shipped)                     | Phase 4.10 closure — P-268~P-292 roster (W0 P-268..P-274 + W4/W5 P-275..P-292) + closure pin (`tests/pi_parity/test_phase_4_10_strict_superset.py`) + 24 supported / 5 deferred RPC split. Sprint 6h₄ carry-forward enumerated (5 session-tree commands + `AgentSessionRuntime` port + `SessionManager.getLeafId` + `rebindSession` seam + `_get_context_usage_safe` real impl + live `session_id` read + Pi-source-grep verification + Pi HTML visual fidelity to Sprint 6h₅). |
| 0075 | [Session Navigation (read-only) RPC Commands](0075-session-navigation-read-only.md)                                                                    | Accepted (Sprint 6h₄a / Phase 4.11 / W6 shipped)                    | Pi parity port of 2 read-only session-navigation RPC commands — `get_fork_messages` (Pi `rpc-mode.ts:591-594` → `agent-session.ts:2867-2900` `getUserMessagesForForking`) + `get_last_assistant_text` (Pi `rpc-mode.ts:596-599` → `agent-session.ts:3063-3070` `getLastAssistantText`). `ForkPointInfo` frozen dataclass for Pi inline `{entryId, text}` shape (P-295) + async harness method per Aelix `Session.get_entries()` async (P-294) + list-only `_extract_user_message_text` with defensive string branch (P-296) + aborted-empty filter `stop_reason == "aborted" AND len(content) == 0` (P-297) + Pi key-omission parity `{"text": text} if text is not None else {}` (P-298 SYNTHESIS) + P-293 line-citation drift captured (ADR-0074 `:563-566`/`:568-571` → verified `:591-594`/`:596-599`). W4+W5 CLEAN verdict — zero BLOCKING/MAJOR/MINOR, only INFO observations mapping to documented divergences. |
| 0076 | [Phase 4.11 Strict Superset Closure](0076-phase-4-11-strict-superset-closure.md)                                                                        | Accepted (Sprint 6h₄a / Phase 4.11 / W6 shipped)                    | Phase 4.11 closure — P-293~P-301 roster (W0 P-293..P-298 + W5 INFO P-299..P-301 mapping to P-294/P-295/P-296 documented divergences) + closure pin (`tests/pi_parity/test_phase_4_11_strict_superset.py`) + 26 supported / 3 deferred RPC split. Sprint 6h₄b carry-forward enumerated (3 session-tree commands `switch_session`/`fork`/`clone` + `AgentSessionRuntime` full port + `SessionManager.getLeafId()` + `rebindSession()` seam P-126 multi-sprint carry-forward). First sprint since 6a with W4+W5 0-finding CLEAN verdict. Sprint 6h₄b foundation-update note appended: DEFERRED ownership rebrands ADR-0076 → ADR-0078 per spec §D.5; runtime infrastructure now in place for 6h₄c wiring. |
| 0077 | [`AgentSessionRuntime` Pi Port + `rebindSession` Seam (FOUNDATION ONLY)](0077-agent-session-runtime-port.md)                                              | Accepted (Sprint 6h₄b / Phase 4.12 / W6 shipped)                    | Pi parity port of `AgentSessionRuntime` (`packages/coding-agent/src/core/agent-session-runtime.ts:67-374`) + `rebindSession` closure (`rpc-mode.ts:310-349`) as the FOUNDATION-ONLY layer. **NO new RPC commands wired** — counts stay 26 / 3 / 29. Harness-rebuild pattern (P-302 BINDING — `HarnessFactory: Callable[[Session], Awaitable[AgentHarness]]` preserves `_state.session_id` + action bindings + merged tools + cached session-name invariants) + `rebind_session` closure subset (P-303 — re-subscribe only; `bindExtensions` / `commandContextActions` waveform deferred to 6h₄c) + `run_rpc_mode` `runtime_host` shim (P-309 — backward-compat for 26 already-wired handlers; `_make_passthrough_runtime` raises `RuntimeError` on accidental replace per W4 LOW-3) + P-306 invariant test + P-307 / P-308 / P-313 / P-314 / P-315 / P-316 carry-forward to 6h₄c. 4 stubbed replace APIs (`switch_session` / `new_session` / `fork` / `import_from_jsonl`) raise `NotImplementedError("Sprint 6h₄c — ADR-0078")`. |
| 0078 | [Phase 4.12 Strict Superset Closure + Sprint 6h₄c Wiring Carry-Forward](0078-phase-4-12-strict-superset-closure.md)                                       | Accepted (Sprint 6h₄b / Phase 4.12 / W6 shipped)                    | Phase 4.12 closure — P-302~P-310 + P-318 roster + closure pin (`tests/pi_parity/test_phase_4_12_strict_superset.py`) + 26 supported / 3 deferred RPC split UNCHANGED (foundation sprint). DEFERRED owner rebrand ADR-0076 → ADR-0078 applied per spec §D.5; cascade pin allowlists in 4.4 / 4.9 / 4.10 / 4.11 extended with ADR-0078 prefix. NEW back-compat regression suite `tests/rpc/test_rpc_mode_runtime_shim.py` (7 P-309 / P-311 tests). Sprint 6h₄c carry-forward enumerated (3 session-tree handler wires + 4 stub fills + P-307 / P-308 / P-313 / P-314 / P-315 lift). |
| 0079 | [Session-tree handlers wired (`switch_session` / `fork` / `clone`) + runtime body fills](0079-session-tree-handlers-wired.md)                            | Accepted (Sprint 6h₄c / Phase 4.13 / W6 shipped — **PHASE 4 RPC CLOSURE**) | Pi parity wiring of 3 session-tree RPC handlers + 3 of 4 runtime replace body fills via `JsonlSessionRepo.open` / `create` / `fork`. `AgentSessionRuntime.__init__` extended with required keyword-only `repo` + `fs` (P-324 BINDING) + 3 stubbed bodies filled (P-325) — `import_from_jsonl` stays stubbed per ADR-0080. NEW `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class + `_bind_runtime_host` adapter (P-326) + `selectedText → text` wire rename with key-omission (P-327) + `clone` leaf_id pre-capture (P-328) + P-329 deliberate convergence (Aelix handlers MUST NOT call rebind manually) + Sprint 6d `_handle_new_session` stub REPLACED — ADR-0058 `parent_session` carry-forward CLOSES (P-330) + `_apply_for_test` test seam REMOVED with 6h₄b tests migrated to real public API (P-331) + 4 W4 MINOR cleanups applied. |
| 0080 | [Phase 4.13 Strict Superset Closure + Phase 4 RPC Roster CLOSED](0080-phase-4-13-strict-superset-closure.md)                                              | Accepted (Sprint 6h₄c / Phase 4.13 / W6 shipped — **PHASE 4 RPC ROSTER CLOSED**) | Phase 4.13 closure — P-323~P-331 roster + closure pin (`tests/pi_parity/test_phase_4_13_strict_superset.py`) + **29 supported / 0 deferred / 29 total** = full Pi parity for `RpcCommand` discriminator union at SHA `734e08e`. `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` full set equality + `DEFERRED_COMMANDS == {}` literal empty + 5 strict invariants beyond count parity (4-entry `RUNTIME_HOST` arity, fork text-omission, clone text-drop, leaf_id pre-capture ordering, exactly-once rebind, `_apply_for_test` removed). **LAST Phase 4 RPC sprint** — Sprint 6h₅+ carry-forward roster covers runtime / extension polish (P-307 / P-308 / P-314 / P-315 + cwd validation + cross-cwd import + `sessionStartEvent` + TUI `/import` body). P-313 `HarnessFactory` 4-field refresh CONFIRMED DROPPED (harness-rebuild encapsulates services + diagnostics + model_fallback_message via factory closure). |

### Sprint 6h₄c sub-table (Phase 4.13 closure — **PHASE 4 RPC ROSTER CLOSED**)

Counts move **26 supported / 3 deferred / 29 total → 29 supported / 0 deferred / 29 total**
= full Pi parity for the 29-variant `RpcCommand` discriminator union at
SHA `734e08e`. `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` (full set
equality) and `DEFERRED_COMMANDS == {}` (literal empty). **This is the
LAST RPC sprint for Phase 4** — remaining Pi parity gaps are runtime /
extension polish (no RPC dispatch impact).

| Item | Status | Owner ADR |
|---|---|---|
| `AgentSessionRuntime.__init__` constructor extended with required keyword-only `repo: JsonlSessionRepo` + `fs: FileSystem` (P-324 BINDING — rejecting Optional shape is deliberate; compile-time enforcement) | shipped | 0079 |
| `AgentSessionRuntime.switch_session` real body — `repo.open(load_jsonl_session_metadata(fs, path))` → `_finish_session_replacement` (P-325) | shipped | 0079 |
| `AgentSessionRuntime.new_session` real body — `repo.create(JsonlSessionCreateOptions(cwd, parent_session_path))` → `_finish_session_replacement` (P-325 / P-330 — replaces Sprint 6d rejection stub) | shipped | 0079 |
| `AgentSessionRuntime.fork` real body — `repo.fork(source, ForkOptions(cwd, entry_id=target_leaf_id, position="at", parent_session_path))` — Aelix persisted-only (drops Pi in-memory branch `:303-319`) (P-325) | shipped | 0079 |
| `AgentSessionRuntime.import_from_jsonl` STAYS STUBBED — no Pi `RpcCommand` discriminator maps to it at SHA `734e08e` (Pi TUI `/import` doesn't go through RPC dispatch; carry-forward per ADR-0080) | shipped | 0079 |
| `_extract_user_message_text` module-private helper (Pi `agent-session-runtime.ts:49-58`) | shipped | 0079 |
| `_apply_for_test` test seam REMOVED from `AgentSessionRuntime` — 6h₄b unit tests migrated to drive `switch_session` via real public API (P-331) | shipped | 0079 |
| NEW `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class — 4 handlers `(new_session, switch_session, fork, clone)` taking `(runtime_host, cmd)` instead of `(harness, cmd)` (P-326) | shipped | 0079 |
| `_bind_runtime_host(handler, runtime_host)` adapter closure (P-326 — keeps dispatch table uniform 2-arg `(harness, cmd)` shape) | shipped | 0079 |
| `_make_missing_runtime_handler(cmd_type)` Pi-shape error stub for `build_dispatch_table(runtime_host=None)` test fixture path (P-326-DRIFT RATIFIED) | shipped | 0079 |
| `build_dispatch_table(model_registry, *, runtime_host=None)` — `runtime_host` Optional with missing-runtime stub fallback (P-326-DRIFT RATIFIED) | shipped | 0079 |
| `_make_passthrough_runtime(harness, harness_factory, *, repo=None, fs=None)` — Pi defaults via `LocalFileSystem` + `JsonlSessionRepo(fs=...)` when caller omits (P-324-DRIFT RATIFIED) | shipped | 0079 |
| `run_rpc_mode(..., repo=None, fs=None)` signature extension — passthrough threads Pi defaults when `runtime_host=None` (P-324) | shipped | 0079 |
| `_handle_switch_session` real handler (Pi `rpc-mode.ts:563-569`) — wire shape `{cancelled}` (Pi line 568) | shipped | 0079 |
| `_handle_fork` real handler (Pi `rpc-mode.ts:571-577`) — wire shape `{cancelled, text?}` with `text` key OMITTED when `selected_text is None` (P-327 / P-298 pattern — `selectedText → text` rename) | shipped | 0079 |
| `_handle_clone` real handler (Pi `rpc-mode.ts:579-589`) — leaf_id captured BEFORE OLD harness dispose (P-328 ordering); wire shape `{cancelled}` only (Pi line 588 drops `selectedText` for clone) | shipped | 0079 |
| `_handle_new_session` REPLACED — Sprint 6d stub deleted; routes through `runtime_host.new_session(parent_session=cmd.parent_session)`; **ADR-0058 `parent_session` carry-forward CLOSES** (P-330) | shipped | 0079 |
| P-329 deliberate convergence — Aelix handlers MUST NOT call rebind manually (Pi belt-and-braces `await rebindSession()` at `rpc-mode.ts:566`/`:574`/`:586` NOT mirrored; runtime's `_finish_session_replacement` is single source of truth — exactly-once invocation per replace) | shipped | 0079 |
| W4 MINOR-1 — Double-catch collapse in `_handle_fork` + `_handle_clone` (keep `ValueError` arm Pi-documented at `:247`; drop blanket `except Exception`) | shipped | 0079 |
| W4 MINOR-2 — Blanket `except Exception` dropped from `_handle_switch_session` (outer `_handle_command` wraps) | shipped | 0079 |
| W4 MINOR-3 — `test_handle_fork_wire_shape_omits_text_when_none` rewritten to drive handler via mocked `AgentSessionRuntime` returning `RuntimeReplaceResult(cancelled=False, selected_text=None)` so handler-layer key-omission invariant is asserted end-to-end | shipped | 0079 |
| W4 MINOR-4 — `_handle_new_session` blanket `except RuntimeError` dropped (avoid masking noop-factory leaks; `Passthrough runtime cannot replace harness` now fails loud) | shipped | 0079 |
| `tests/pi_parity/test_phase_4_13_strict_superset.py` closure pin (29 / 0 / 29 + `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` full set equality + `DEFERRED_COMMANDS == {}` literal + handler invariants + `_apply_for_test` removed + wire shapes + rebind exactly-once + leaf_id pre-capture ordering + Pi line citations + 4-entry RUNTIME_HOST arity class) | shipped | 0080 |
| `tests/pi_parity/fixtures/pi_runtime_wire_734e08e.json` W0 fixture (SHA-pinned + immutable) | shipped | 0079 |
| `tests/runtime/test_agent_session_runtime_replace_apis.py` (NEW — real `switch_session` / `new_session` / `fork` over tmp-path `JsonlSessionRepo`) | shipped | 0079 |
| `tests/runtime/test_switch_session.py` / `test_fork.py` / `test_new_session_real.py` (NEW — runtime-layer unit tests for each replace API) | shipped | 0079 |
| `tests/rpc/test_rpc_mode_switch_fork_clone.py` (NEW — 3 handler integration tests + arity / dispatch wiring + rebind invocation count + leaf_id pre-capture ordering + W4 MINOR-3 rewrite) | shipped | 0079 |
| `tests/rpc/test_rpc_mode_new_session_parent.py` (NEW — Sprint 6d stub removal regression; asserts `parent_session_path` actually persists) | shipped | 0079 |
| Cascade pin allowlist updates — `test_phase_4_4`/`4_6`/`4_8`/`4_9`/`4_10`/`4_11`/`4_12_strict_superset.py` count cascades to 29 supported / 0 deferred | shipped | 0080 |
| 6h₄b test migrations — `tests/runtime/test_agent_session_runtime.py` + `tests/rpc/test_rpc_mode_*.py` drop `_apply_for_test` usage and migrate to real `switch_session` (P-331) | shipped | 0079 |
| ADR-0034 amendment — Sprint 6h₄c PHASE 4 RPC CLOSURE row (3 of 29 RpcCommand variants land + cumulative 29 of 29) | shipped | 0034 |
| ADR-0076 amendment — Sprint 6h₄c PHASE 4 RPC CLOSURE note + P-323 line-citation correction (`:528-557` → verified `:563-569` / `:571-577` / `:579-589`) | shipped | 0076 |
| ADR-0078 amendment — Sprint 6h₄c foundation → wiring complete note + P-323 line-citation correction (`:566` / `:574` / `:586` → verified `:563-569` / `:571-577` / `:579-589`) | shipped | 0078 |
| P-307 `session_shutdown` extension event emit from `AgentHarness.dispose()` | deferred to Sprint 6h₅+ | 0080 |
| P-308 Real `session_before_switch` / `session_before_fork` extension cancel hooks (currently `_emit_before_switch` / `_emit_before_fork` no-op `False`) | deferred to Sprint 6h₅+ | 0080 |
| P-313 `HarnessFactory` 4-field refresh | **DROPPED** (harness-rebuild encapsulates services + diagnostics + model_fallback_message via factory closure; redundant for Aelix) | 0080 |
| P-314 `with_session: Callable[[ReplacedSessionContext], Awaitable[None]] \| None` 2-stage callback | deferred to Sprint 6h₅+ | 0080 |
| P-315 `set_rebind_session` / `set_before_session_invalidate` optional-cb signature widening | deferred to Sprint 6h₅+ | 0080 |
| `assertSessionCwdExists` Pi parity (cwd-on-disk validation before session swap) | deferred to Sprint 6h₅+ | 0080 |
| `previousSessionFile` / `sessionStartEvent` tracking (extension event payload) | deferred to Sprint 6h₅+ | 0080 |
| Pi `forkFrom` cross-cwd import (no RPC wire today) | deferred to Sprint 6h₅+ | 0080 |
| Pi `setup` callback in `new_session` | deferred to Sprint 6h₅+ | 0080 |
| `import_from_jsonl` real runtime body (no RPC wire today — Pi TUI `/import` doesn't go through dispatch) | deferred to Sprint 6h₅+ | 0080 |

### Sprint 6h₄b sub-table (Phase 4.12 closure — FOUNDATION ONLY)

Counts UNCHANGED at **26 supported / 3 deferred / 29 total**. NO new
RPC commands wired in this sprint — the foundation is `AgentSessionRuntime`
+ the `rebindSession` seam. Sprint 6h₄c (ADR-0078) wires
`switch_session` / `fork` / `clone` on top of this foundation.

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_agent_core.runtime` package (NEW) — `aelix_agent_core.runtime.__init__` re-exports `AgentSessionRuntime` / `AgentSessionRuntimeDiagnostic` / `HarnessFactory` / `RuntimeReplaceResult` | shipped | 0077 |
| `aelix_agent_core.runtime._types` (NEW) — `HarnessFactory: Callable[[Session], Awaitable[AgentHarness]]` type alias + `RuntimeReplaceResult` frozen `(cancelled, selected_text)` dataclass + `AgentSessionRuntimeDiagnostic` frozen `(code, message)` dataclass | shipped | 0077 |
| `aelix_agent_core.runtime.agent_session_runtime.AgentSessionRuntime` (NEW — Pi `agent-session-runtime.ts:67-374` port; 5 read-only getters `harness` / `session` / `cwd` / `diagnostics` / `model_fallback_message` + 7 public methods + private `_apply` / `_teardown_current` / `_finish_session_replacement` replace seam + test-only `_apply_for_test`) | shipped | 0077 |
| Harness-rebuild pattern (P-302 BINDING — preserves `_state.session_id` + action bindings + merged tools + cached session-name invariants vs. Pi session-swap which has none of those captures) | shipped | 0077 |
| `rebind_session` closure in `rpc_mode.py` (P-303 — Pi `rpc-mode.ts:310-349` subset: `_Capture` cell + re-subscribe only; `bindExtensions` / `commandContextActions` waveform deferred to 6h₄c) | shipped | 0077 |
| `run_rpc_mode` signature shim accepts optional `runtime_host: AgentSessionRuntime \| None = None` + `harness_factory: HarnessFactory \| None = None` (P-309 — backward-compat for 26 already-wired handlers) | shipped | 0077 |
| `_make_passthrough_runtime(harness, harness_factory)` helper with `_noop_factory` that RAISES `RuntimeError` (W4 LOW-3 — fail loudly on accidental replace instead of silently re-binding to the same stale harness) | shipped | 0077 |
| 4 stubbed public replace APIs (`switch_session` / `new_session` / `fork` / `import_from_jsonl`) raise `NotImplementedError("Sprint 6h₄c — ADR-0078")` (P-310 — async signatures preserved for 6h₄c body fill) | shipped | 0077 |
| `_emit_before_switch` / `_emit_before_fork` async no-op stubs return `False` (P-308 — Aelix has no `session_before_switch` / `session_before_fork` hook events today; real cancel surface defers to 6h₄c+) | shipped | 0077 |
| `set_rebind_session` (P-305 — Pi `:99-101` fire-and-await) + `set_before_session_invalidate` (P-307 — Pi `:111-113` sync) seam setters | shipped | 0077 |
| `dispose()` calls `before_session_invalidate` then `await harness.dispose()` (P-307 — Pi `:366-373` order preserved; `session_shutdown` emit gap recorded in ADR-0078 carry-forward) | shipped | 0077 |
| Diagnostics list copied on constructor + getter (P-318 — Aelix-additive safety; Pi takes reference) | shipped | 0077 |
| DEFERRED owner rebrand 0076 → 0078 in `DEFERRED_COMMANDS` (W4 MEDIUM-1 + W5 P-312 + W5 P-319 — per spec §D.5; rebrand applied with cascade pin allowlist updates) | shipped | 0078 |
| Cascade pin allowlist updates — `test_phase_4_4`/`4_9`/`4_10`/`4_11_strict_superset.py` extended with ADR-0078 prefix | shipped | 0078 |
| Closure pin `tests/pi_parity/test_phase_4_12_strict_superset.py` (26 / 3 / 29 unchanged + DEFERRED owners cite ADR-0078 ONLY + 15 Pi member line-range pins + runtime class shape pins + frozen dataclass field locks + `run_rpc_mode` signature pin + `_make_passthrough_runtime` import pin + Pi fixture immutability + architecture decision = `"harness-rebuild"`) | shipped | 0078 |
| `tests/pi_parity/fixtures/pi_agent_session_runtime_734e08e.json` (W0 fixture — 15 Pi member line-ranges + architecture decision = `"harness-rebuild"` + P-302 / P-307 / P-308 documented notes) | shipped | 0077 |
| `tests/runtime/test_agent_session_runtime.py` (29+ unit tests — constructor + 5 getters + 2 seam setters + 4 stub returns + `_apply_for_test` replace seam exercise + dispose order + frozen dataclass locks + P-306 `_state.session_id` invariant test added in W6 per W4 NIT-2) | shipped | 0077 |
| `tests/rpc/test_rpc_mode_rebind.py` (rebind closure integration — subscription rebalance + listener-count balance per replace + smoke test for `runtime_host` kwarg renamed per W4 NIT-1) | shipped | 0077 |
| `tests/rpc/test_rpc_mode_runtime_shim.py` (NEW — 7 P-309 / P-311 back-compat regression tests added in W6 per W5 P-311 MUST-FIX 2: bare-harness call works + passthrough identity + raising no-op factory + dispatch reads `capture.harness` + `runtime_host` harness wins + wired handlers still callable + 3 deferred return ADR-0078 in error string) | shipped | 0078 |
| `tests/rpc/test_rpc_mode_deferred.py` + `test_rpc_mode_stdin_stdout.py` extended ADR allowlists for ADR-0078 rebrand | shipped | 0078 |
| ADR-0034 amendment — Sprint 6h₄b row (foundation sprint, counts unchanged) | shipped | 0034 |
| ADR-0076 amendment — Sprint 6h₄b foundation update note appended (DEFERRED ownership rebrands ADR-0076 → ADR-0078) | shipped | 0076 |
| 3 session-tree commands (switch_session / fork / clone) — wire on 6h₄b runtime foundation | deferred to Sprint 6h₄c | 0078 |
| Real `_emit_before_switch` / `_emit_before_fork` extension cancel hooks (P-308 fill-in) | deferred to Sprint 6h₄c | 0078 |
| P-307 `session_shutdown` extension event emit from `AgentHarness.dispose()` | deferred to Sprint 6h₄c | 0078 |
| P-313 widen `HarnessFactory` for full Pi field refresh (`_services` / `_diagnostics` / `_modelFallbackMessage`) | deferred to Sprint 6h₄c | 0078 |
| P-314 `with_session: Callable[[ReplacedSessionContext], Awaitable[None]] \| None = None` 2-stage callback | deferred to Sprint 6h₄c | 0078 |
| P-315 `set_rebind_session` / `set_before_session_invalidate` optional-cb signature widening | deferred to Sprint 6h₄c | 0078 |

### Sprint 6h₄a sub-table (Phase 4.11 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_agent_core.harness._fork_point.ForkPointInfo` (NEW — `@dataclass(frozen=True)` with fields `(entry_id: str, text: str)` for Pi inline anonymous `Array<{entryId, text}>` shape, P-295) | shipped | 0075 |
| `AgentHarness.get_user_messages_for_forking()` (Pi parity `agent-session.ts:2867-2900` `getUserMessagesForForking`; async per P-294 — Aelix `Session.get_entries()` is async) | shipped | 0075 |
| `AgentHarness.get_last_assistant_text()` (Pi parity `agent-session.ts:3063-3070` `getLastAssistantText`; reverse-walk + aborted-empty filter per P-297) | shipped | 0075 |
| `AgentHarness._extract_user_message_text` (Pi parity `agent-session.ts:2887-2898`; list-only walk with defensive string branch per P-296 — unreachable under Aelix type system but kept byte-for-byte with Pi source) | shipped | 0075 |
| 2 RPC handlers (`_handle_get_fork_messages` / `_handle_get_last_assistant_text`) + camelCase wire serializer (Pi-shape `[{"entryId": ..., "text": ...}]`) + dispatch entries | shipped | 0075 |
| `_handle_get_last_assistant_text` Pi key-omission parity — `data = {"text": text} if text is not None else {}` (P-298 SYNTHESIS — matches Pi `JSON.stringify({text: undefined}) → {}` and existing Sprint 6h₃ `_session_stats_to_dict` undefined-skip pattern) | shipped | 0075 |
| Aborted-empty filter `stop_reason == "aborted" AND len(content) == 0` (P-297 — Pi `agent-session.ts:3063-3070`) | shipped | 0075 |
| Line citation correction — `rpc-mode.ts:591-594` / `:596-599` (W0 verified; ADR-0074 had estimated `:563-566`/`:568-571`; drift captured per P-293) | shipped | 0075 / 0076 |
| `tests/pi_parity/test_phase_4_11_strict_superset.py` closure pin (26 supported / 3 deferred + ForkPointInfo shape + Pi-camelCase wire + async harness method + aborted-empty filter + Pi key-omission parity + W0-verified line numbers) | shipped | 0076 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin: SUPPORTED 24 → 26, DEFERRED 5 → 3) | shipped | 0076 |
| `tests/pi_parity/test_phase_4_6/4_8/4_9/4_10_strict_superset.py` count cascade updates (SUPPORTED 24 → 26, DEFERRED 5 → 3) | shipped | 0076 |
| `tests/rpc/test_rpc_mode_deferred.py` + `test_rpc_mode_stdin_stdout.py` count updates (5 → 3 deferred) | shipped | 0076 |
| `tests/pi_parity/fixtures/pi_session_navigation_734e08e.json` (W0 fixture — verified Pi handler bodies at `:591-594` / `:596-599`) | shipped | 0075 |
| ADR-0034 amendment — Sprint 6h₄a Phase 4.11 partition (2 of 29 + 26 of 29 cumulative) | shipped | 0034 |
| ADR-0074 amendment — line-citation correction note (P-293 — `:563-566`/`:568-571` → `:591-594`/`:596-599`) | shipped | 0074 |
| 3 session-tree commands (switch_session / fork / clone) | deferred to Sprint 6h₄b | 0076 |
| Pi `AgentSessionRuntime` full port (runtimeHost.switchSession / fork / fork({position: "at"})) | deferred to Sprint 6h₄b | 0076 |
| `SessionManager.getLeafId()` for `clone` command | deferred to Sprint 6h₄b | 0076 |
| `rebindSession()` seam (P-126 Sprint 6f multi-sprint carry-forward — Sprint 6f → 6h₁ → 6h₂ → 6h₃ → 6h₄a accumulation) | deferred to Sprint 6h₄b | 0076 |
| `_get_context_usage_safe` real implementation (P-282 carry-forward from ADR-0074) | deferred to Sprint 6h₄b+ | 0076 |
| Live `session_id` read via session manager (P-291 carry-forward from ADR-0074) | deferred to Sprint 6h₄b+ | 0076 |
| Pi-source-grep verification tooling (P-286 carry-forward from ADR-0074) | deferred to Sprint 6h₄b+ | 0076 |
| Pi HTML visual fidelity + session-tree entry source (P-280 carry-forward from ADR-0074) | deferred to Sprint 6h₅ | 0076 |

### Sprint 6h₃ sub-table (Phase 4.10 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_agent_core.harness._session_stats` (NEW — `SessionStats` 10-field dataclass + `SessionStatsTokens` 5-field sub-shape + `aggregate_session_stats` pure-function aggregator + `_read` dict-fallback helper) | shipped | 0073 |
| `aelix_coding_agent._export_html` (NEW — minimal HTML5 emitter with per-role sections + Pi-shape default `aelix-session-<basename>.html` cwd-relative path) | shipped | 0073 |
| `AgentHarness.get_session_stats()` + `AgentHarness.export_to_html()` methods (Pi parity + harness-side precondition raises for in-memory / missing session) | shipped | 0073 |
| 2 RPC handlers (`_handle_get_session_stats` / `_handle_export_html`) + Pi-shape `_session_stats_to_dict` (W6 P-275 BLOCKING — Pi-shape `contextUsage {tokens, contextWindow, percent}`) | shipped | 0073 |
| Aggregator `totalMessages = len(messages)` (W6 P-276 BLOCKING — matches Pi `agent-session.ts:2935` `state.messages.length`) | shipped | 0073 |
| Pi error parity on `export_to_html` (W6 P-279 MAJOR — `export-html.ts:242-248` raises on in-memory / empty session) | shipped | 0073 |
| Pi-shape default `outputPath = aelix-session-<basename>.html` cwd-relative (W6 P-281 MAJOR — `export-html.ts:273-277`) | shipped | 0073 |
| Aggregator dict-shape `usage` fallback via `_read` helper (W6 P-283) | shipped | 0073 |
| Drop dead `hasattr(session, "messages")` branch (W6 W4 HIGH P-292 — pyright regression fix) | shipped | 0073 |
| Drop `getattr(cmd, "output_path", None)` in `_handle_export_html` (W6 W4 MEDIUM-1) | shipped | 0073 |
| Drop `path.parent` truthiness tautology in `_export_html` (W6 W4 MEDIUM-2) | shipped | 0073 |
| Line citation corrections (W6 P-277/P-278/P-286 — `rpc-mode.ts:553-561` + `agent-session.ts:2901-2945` in 7+ files: rpc_mode.py docstrings + _session_stats.py module docstring + harness/core.py method docstrings + closure pin + Pi fixture + spec) | shipped | 0073 |
| `tests/pi_parity/test_phase_4_10_strict_superset.py` closure pin | shipped | 0074 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin: SUPPORTED 22 → 24, DEFERRED 7 → 5) | shipped | 0074 |
| `tests/pi_parity/test_phase_4_6_strict_superset.py` / `4_8` / `4_9` count updates (SUPPORTED 22 → 24, DEFERRED 7 → 5) | shipped | 0074 |
| ADR-0034 amendment — Sprint 6h₃ Phase 4.10 partition (2 of 29 + 24 of 29 cumulative) | shipped | 0034 |
| Pi `AgentSessionRuntime` port (runtimeHost.switchSession / fork) | deferred to Sprint 6h₄ | 0074 |
| `SessionManager.getLeafId` for `clone` command | deferred to Sprint 6h₄ | 0074 |
| `rebindSession` seam (P-126 Sprint 6f carry-forward) | deferred to Sprint 6h₄ | 0074 |
| 5 session-tree commands (switch_session / fork / clone / get_fork_messages / get_last_assistant_text) | deferred to Sprint 6h₄ | 0074 |
| `_get_context_usage_safe` real implementation (P-282 — model registry + per-turn tracking) | deferred to Sprint 6h₄ | 0074 |
| Live `session_id` read via session manager (P-291) | deferred to Sprint 6h₄ | 0074 |
| Pi-source-grep verification tooling (P-286) | deferred to Sprint 6h₄ | 0074 |
| Pi HTML visual fidelity + session-tree entry source (P-280) | deferred to Sprint 6h₅ | 0074 |

### Sprint 6h₂ sub-table (Phase 4.9 closure)

| Item | Status | Owner ADR |
|---|---|---|
| 9 RPC handlers (steer / follow_up / cycle_thinking_level / set_steering_mode / set_follow_up_mode / set_auto_compaction / set_auto_retry / abort_retry / abort_bash) | shipped | 0071 |
| Harness setters (5 new + 4 new AgentState fields: `auto_compaction_enabled` / `auto_retry_enabled` / `retry_aborted` / `bash_aborted`) | shipped | 0071 |
| `AgentHarness.auto_compaction_enabled` + `AgentHarness.auto_retry_enabled` public properties | shipped | 0071 |
| `_MessageQueue.set_mode(mode)` helper (W6 P-265 BLOCKING — defensive runtime validation) | shipped | 0071 |
| `cycle_thinking_level` `supportsThinking()` guard (W6 P-254 BLOCKING — `!!this.model?.reasoning` short-circuit) | shipped | 0071 |
| `images` keyword-only marker on `AgentHarness.steer` / `AgentHarness.follow_up` (W6 P-263 MAJOR) | shipped | 0071 |
| Strict `_decode_images` (camelCase only + required-field validation, W6 P-262 BLOCKING — drops snake_case acceptance and silent-empty-string coercion) | shipped | 0071 |
| `RpcSessionState.auto_retry_enabled` field (12 → 13) + `_handle_get_state` real source (W6 P-264 BLOCKING) | shipped | 0071 |
| `typing.cast(QueueMode, mode)` in `set_steering_mode` / `set_follow_up_mode` (W4 LOW-3) | shipped | 0071 |
| `build_dispatch_table` docstring + deferred handler error string updates (W4 NIT) | shipped | 0071 |
| Line citation corrections — 14 docstrings + W0 fixture cite W5-audited `rpc-mode.ts:483-547` + `agent-session.ts` method sites (W6 P-258 BLOCKING) | shipped | 0071 |
| `tests/pi_parity/test_phase_4_9_strict_superset.py` closure pin (28 tests including 11 W6 regression pins) | shipped | 0072 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (12 → 13 `RpcSessionState` field count + Pi fixture extension) | shipped | 0072 |
| `tests/pi_parity/test_phase_4_6_strict_superset.py` W4 NIT renames (count-free names) | shipped | 0072 |
| `tests/pi_parity/fixtures/pi_rpc_9_commands_734e08e.json` W6 P-258 line-number corrections (528-635 → 483-547) | shipped | 0072 |
| ADR-0034 amendment — Sprint 6h₂ Phase 4.9 partition (9 of 29 + 22 of 29 cumulative) | shipped | 0034 |
| Pi `SettingsManager` (`coding-agent/src/core/settings-manager.ts`) disk persistence (P-255 / P-256) | deferred to Sprint 6h₃ | 0072 |
| Pi `agent-harness.ts` retry loop with `AbortController` (P-257) | deferred to Sprint 6h₃ | 0072 |
| `queue_update` event payload Pi-shape `string[]` vs Aelix `list[UserMessage]` (P-259) | deferred to Sprint 6h₃ | 0072 |
| `steer` / `follow_up` Pi-side `_throwIfExtensionCommand` + `_expandSkillCommand` + `expandPromptTemplate` expanders (P-260) | deferred to Sprint 6h₃ | 0072 |
| `cycle_thinking_level` sync vs async asymmetry (P-266 — documented Pi divergence; Aelix `set_thinking_level` is async) | tracked | 0072 |
| Spec citation tweak — `SettingsManager` upstream source (P-267) | deferred to Sprint 6h₃ | 0072 |
| 5 session-tree commands (switch_session / fork / clone / get_fork_messages / get_last_assistant_text) | deferred to Sprint 6h₃ | 0072 |
| 2 session-inspection commands (get_session_stats / export_html) | deferred to Sprint 6h₃ | 0072 |

### Sprint 6h sub-table (Phase 4.8 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_agent_core.harness.prompt_templates` (Pi port of `prompt-templates.ts` — 5 functions + 4 types, P-216) | shipped | 0069 |
| `aelix_agent_core.harness.skills` (Pi port of `skills.ts` — 2 functions + 4 types + `.gitignore`/`.ignore`/`.fdignore` honouring via pathspec, P-217) | shipped | 0069 |
| `aelix_agent_core.harness._frontmatter` (shared YAML frontmatter parser — W4 m4) | shipped | 0069 |
| `aelix_agent_core.harness._extension_runner.ExtensionRunner` + `ResolvedCommand` (Pi `runner.ts:512-551` with `{name}:{N}` disambiguation — W6 P-224 BLOCKING) | shipped | 0069 |
| `aelix_agent_core.harness.core.AgentHarness` wires `extension_runner` / `prompt_templates` / `skills` properties + 2 setters | shipped | 0069 |
| `aelix_coding_agent.extensions.api.ExtensionSourceInfo` Pi `{path, scope, origin}` field extension (W6 P-225 BLOCKING) | shipped | 0069 |
| `aelix_coding_agent.rpc.rpc_mode._handle_get_commands` (Pi `rpc-mode.ts:622-653` — 3-source aggregation + Pi-shape `sourceInfo` wire per W6 P-225 + `invocation_name` per W6 P-224) | shipped | 0069 |
| `PromptTemplate.description` / `content` default `""` (W6 P-226 MAJOR) | shipped | 0069 |
| YAML parse failures surface error text in `parse_failed` diagnostic (W6 P-233 MINOR) | shipped | 0069 |
| Case-insensitive `.md` extension strip via `name.lower().endswith(".md")` (W6 P-234 MINOR) | shipped | 0069 |
| `pi_get_commands_734e08e.json` fixture name-regex text correction (W6 P-227 MINOR) | shipped | 0069 / 0070 |
| `disable-model-invocation` tautological test → real sentinel (integer `1` is truthy but not `is True`) (W6 W4 m2) | shipped | 0069 |
| `PyYAML>=6.0` + `pathspec>=0.12` added to `aelix-agent-core` pyproject (P-222) | shipped | 0069 |
| `tests/pi_parity/test_phase_4_8_strict_superset.py` closure pin (22+ tests covering W2 surface + W6 must-fix regressions) | shipped | 0070 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin updated: SUPPORTED 12 → 13, DEFERRED 17 → 16) | shipped | 0070 |
| `tests/pi_parity/test_phase_4_6_strict_superset.py` strengthening (Sprint 6f closure pin updated: SUPPORTED 12 → 13, DEFERRED 17 → 16) | shipped | 0070 |
| `tests/harness/test_extension_runner.py` (Pi disambiguation regression — 3-way collision + explicit-disambiguation collision + source_info forward + None fallback) | shipped | 0069 |
| `tests/harness/test_harness_session_aggregation.py` (harness-side aggregation hooks + defensive copy invariant) | shipped | 0069 |
| `tests/rpc/test_rpc_mode_get_commands.py` (Pi-shape sourceInfo wire regression + 3-source aggregation in Pi insertion order + `"skill:"` prefix) | shipped | 0069 |
| `tests/harness/test_frontmatter.py` + `test_prompt_templates.py` + `test_skills.py` (unit coverage for all 5 ports + 2 new types) | shipped | 0069 |
| ADR-0034 amendment — Sprint 6h₁ prompt-templates + skills + `get_commands` row | shipped | 0034 |
| 16 remaining RPC commands (steer / follow_up / cycle_thinking_level / queue / auto / abort_bash / session inspection / session tree / extension UI bridge) | deferred to Sprint 6h₂ | 0070 |
| Workspace-scoped model selection (`cycle_model.isScoped: true` path) | deferred to Sprint 6h₂ | 0070 |
| `applyProviderConfig` for `register_provider.config.models` | deferred to Sprint 6h₂ | 0070 |
| `enableGitHubCopilotModel` POST automation | deferred to Sprint 6h₂ | 0070 |
| `loadSourcedPromptTemplates` / `loadSourcedSkills` source-tagged variants | deferred to Sprint 6h₂ | 0070 |
| `image-models.ts` + `image-models.generated.ts` parallel image-model registry | deferred to Sprint 6h₃ | 0070 |
| Typed `Model.compat` discriminated union | deferred to Sprint 6h₃ | 0070 |
| pathspec `gitwildmatch` → `gitignore` flavour cutover when pathspec 0.13 lands | tracked | 0070 |
| W4 m3 (unbounded recursion under filesystem loops — Pi has the same behaviour; Aelix matches) | closed | 0070 |
| W4 NIT-2..NIT-5 (cosmetic) | closed | 0070 |

### Sprint 5b sub-table (Phase 3.2 closure)

| Item | Status | Owner ADR |
|---|---|---|
| 7 built-in coding tools | shipped | 0042 |
| `create_coding_tools` / `create_read_only_tools` / `create_all_tools` factories | shipped | 0042 |
| `input` emit site (`AgentHarness.prompt()` head) | shipped | 0042 |
| `user_bash` emit site (minimal CLI `!/!!` parser) | shipped | 0042 |
| `resources_discover` emit site (`bootstrap` / `reload_resources`) | shipped | 0042 |
| 8 tool-typed `ToolCallHookEvent` variants | shipped | 0043 |
| 8 symmetric `ToolResultHookEvent` variants | shipped | 0043 |
| `make_tool_call_event` / `make_tool_result_event` factories | shipped | 0043 |
| `is_tool_call_event_type` / `is_tool_result_event_type` narrow helpers | shipped | 0043 |
| `ExtensionCommandContext` (4 bound + 2 raise) | shipped | 0042 |
| Sprint 5a ergonomics — cached session name + `_pending_tasks` GC + `_ensure_loop` | shipped | 0042 |
| 4 wired stubs (`send_message` / `send_user_message` / `append_entry` / `get_commands`) | shipped | 0042 |
| `tests/pi_parity/test_phase_3_2_strict_superset.py` closure pin | shipped | 0044 |
| DEFERRED_ALLOWLIST Phase-4-only (3 provider entries) | shipped | 0044 |

### Sprint 6g sub-table (Phase 4.7 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_ai.streaming.KnownProvider` Literal (32 strings, Pi semantic order verbatim from `types.ts:23-55` — P-208 MAJOR fix) | shipped | 0067 |
| `aelix_ai.streaming.Model.compat: dict[str, Any] \| None` passthrough field (P-200) | shipped | 0067 / 0064 |
| `aelix_ai.models_generated.json` full Pi catalog (32 providers / 942 models) | shipped | 0067 |
| `aelix_ai.models_generated._load_catalog` fail-fast on Pi-required fields (P-209 MAJOR fix) | shipped | 0067 |
| `aelix_coding_agent.core.defaults.DEFAULT_THINKING_LEVEL = "medium"` (P-205 BLOCKING fix — was incorrectly `"off"` in Sprint 6g₁ ship) | shipped | 0067 |
| `aelix_coding_agent.core.defaults.is_valid_thinking_level` (Pi `cli/args.ts::isValidThinkingLevel`) | shipped | 0067 |
| `aelix_coding_agent.core.model_resolver` (7 functions + 3 helpers ported from Pi `model-resolver.ts:1-637` — actual 637 LOC, NOT 439 per P-215) | shipped | 0067 |
| `DEFAULT_MODEL_PER_PROVIDER` (32-row map from Pi `:14-47`) | shipped | 0067 |
| `RestoreModelResult` frozen dataclass mirrors Pi `restoreModelFromSession` return shape (P-206 MAJOR fix) | shipped | 0067 |
| `_glob_match_pi_minimatch` per-segment helper for Pi `minimatch` `/`-boundary semantics (P-207 MAJOR fix) | shipped | 0067 |
| `_openai_compat.get_compat` catalog `Model.compat` merge wiring confirmed (P-210 MAJOR — spec §J text was stale at ship) | shipped | 0067 |
| `tests/pi_parity/test_phase_4_7_strict_superset.py` closure pin (32 tests — KnownProvider order + DEFAULT_THINKING_LEVEL + Model.compat + RestoreModelResult + glob /-boundary + catalog 32 providers + canonical models) | shipped | 0068 |
| `tests/coding_agent/core/test_defaults.py` + `test_model_resolver.py` (49 tests — 7 functions + 3 helpers + integration with real ModelRegistry) | shipped | 0067 |
| `tests/providers/test_openai_compat_with_catalog.py` (3 P-210 regressions — zai/glm-5v-turbo + zai/glm-4.5-air partial dict + baseline-vs-merged comparison) | shipped | 0067 |
| `tests/test_models_generated.py::test_load_catalog_raises_keyerror_on_missing_required_field` (P-209 regression) | shipped | 0067 |
| `tests/pi_parity/fixtures/pi_model_resolver_734e08e.json` (W0 fixture — line refs corrected per P-215; `default_thinking_level` corrected to `"medium"` per P-205) | shipped | 0067 / 0068 |
| ADR-0034 amendment — Sprint 6g₁ model-resolver port + full catalog + KnownProvider + Model.compat | shipped | 0034 |
| ADR-0064 amendment — Sprint 6g₁ adds `compat` field (6 additive Model fields total) | shipped | 0064 |
| Typed `Model.compat` discriminated union (`OpenAICompletionsCompat \| OpenAICodexResponsesCompat \| …`) | deferred to Sprint 6g₂ | 0068 |
| `get_commands` RPC command + prompt-templates + skills surface | deferred to Sprint 6g₂ | 0068 |
| 16 remaining RPC commands (queue / session tree / extension UI bridge / auto modes / retry / etc.) | deferred to Sprint 6g₂ | 0068 |
| `image-models.ts` + `image-models.generated.ts` parallel image-model registry | deferred to Sprint 6g₃ | 0068 |
| `chalk`-colored CLI output | deferred to Sprint 6h / Phase 5 TUI | 0067 |
| Workspace-scoped model selection (`isScoped: true` path) | deferred to Sprint 6g₂ | 0068 |
| `applyProviderConfig` for `register_provider.config.models` + `models.json` schema | deferred to Sprint 6g₂ | 0068 |
| `enableGitHubCopilotModel` POST automation | deferred to Sprint 6g₂ | 0068 |
| `Model.knowledgeCutoff` / `Model.releaseDate` (Pi-untyped runtime additions) | deferred — Pi types catch up | 0068 |
| W4 NIT-2..NIT-5 (cosmetic) | deferred to Sprint 6h | 0068 |

### Sprint 6f sub-table (Phase 4.6 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_ai.streaming.ModelCost` (frozen per-million rate) + `Cost = ModelCost` back-compat alias for Sprint 6a/6b callers (P-169) | shipped | 0064 |
| `aelix_ai.streaming.UsageCost` (mutable resolved cost, mirrors Pi `Usage.cost` in-place mutation) + `Usage` dataclass (P-168) | shipped | 0064 |
| Pi `Model.thinking_level_map: dict[str, str \| int \| None] \| None` (P-165) | shipped | 0064 |
| Pi `Model.max_tokens` + `Model.context_window` (plain int, P-166) | shipped | 0064 |
| Pi `Model.headers: dict[str, str] \| None` + `_model_to_dict` RPC wire (P-167 / P-178 MAJOR) | shipped | 0064 |
| `aelix_ai.models` 7 Pi-parity helpers (`get_all_models` / `get_models_for_provider` / `get_model_by_id` / `find_model_with_provider` / `get_default_model` / `coerce_thinking_level` + `EXTENDED_THINKING_LEVELS` 6-value Pi parity) | shipped | 0064 / 0066 |
| `aelix_ai.models_generated` 13-model seed catalog (Anthropic + OpenAI + GitHub Copilot — ≥10 models / ≥3 providers, P-174) | shipped | 0064 |
| `aelix_coding_agent.model_registry.ModelRegistry` (14 methods + `create` / `in_memory` factories) | shipped | 0065 |
| `ResolvedRequestAuth` discriminated union (`ok: bool` discriminator + `api_key` + `auth_header: bool` P-180 Pi-strict bool, NOT str + `error`) | shipped | 0065 |
| `ProviderConfigInput` dataclass (Sprint 6f₁ minimum shape; full `models.json` schema deferred to Sprint 6g) | shipped | 0065 |
| `_load_error` cleared at top of every `_load_models`; multi-provider failures newline-joined (P-175) | shipped | 0065 |
| `is_using_oauth` trusts AuthStorage discriminator exclusively (drops legacy `get_oauth_provider` extra guard, P-176) | shipped | 0065 |
| `asyncio.get_event_loop()` → `asyncio.get_running_loop()` migration (P-184, Python 3.12 deprecation) | shipped | 0065 |
| Harness `current_model` property + `set_current_model` writes `_state.model` directly (P-187 — no override layer) | shipped | 0065 |
| Harness `has_configured_auth` enforcement before `set_model` swap (P-172 BLOCKING) | shipped | 0065 |
| `cycle_model` wrap-around no-op when `len(models) <= 1` (P-170 BLOCKING) + `thinking_level` persistence + `coerce_thinking_level` clamp (P-171 BLOCKING / P-182) | shipped | 0065 |
| `aelix_coding_agent.rpc.rpc_mode` `set_model` / `cycle_model` / `get_available_models` handlers (moved from `DEFERRED_COMMANDS` → `SUPPORTED_COMMANDS`) | shipped | 0058 / 0066 |
| `tests/pi_parity/test_phase_4_6_strict_superset.py` closure pin (7 helpers + 14 ModelRegistry methods + 17 deferred / 12 supported + seed catalog + `Model.headers` + `current_model` reads `_state.model`) | shipped | 0066 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin updated: SUPPORTED 9 → 12, DEFERRED 20 → 17, P-181) | shipped | 0066 |
| `tests/pi_parity/fixtures/pi_model_registry_734e08e.json` Pi-parity fixture | shipped | 0066 |
| `tests/model_registry/test_model_registry.py` + `test_oauth_modify_models_integration.py` | shipped | 0065 |
| `tests/test_models.py` + `tests/test_models_generated.py` + `tests/test_harness_current_model.py` | shipped | 0064 / 0065 |
| `tests/rpc/test_rpc_mode_set_model.py` + `test_rpc_mode_cycle_model.py` + `test_rpc_mode_get_available_models.py` + `test_w6_regressions_6f.py` (W6 P-170/P-171/P-172/P-182/P-187/P-181/P-179 regression pins) | shipped | 0058 / 0065 / 0066 |
| ADR-0034 amendment — Sprint 6f ModelRegistry runtime + Pi `Model` field shape (5 new fields) | shipped | 0034 |
| ADR-0049 amendment — Sprint 6f 5 new Model field shapes (cost / thinking_level_map / max_tokens / context_window / headers) | shipped | 0049 |

### Sprint 6e sub-table (Phase 4.5 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_ai.oauth.github_copilot` (Copilot device-code grant + `_start_device_flow` + `_poll_for_github_access_token` + `refresh_github_copilot_token` + `_modify_copilot_models`) | shipped | 0059 |
| Copilot `enterpriseUrl` (camelCase, raw user input) preserved in `OAuthCredentials.extra` (P-147) | shipped | 0059 |
| Copilot `proxy-ep` → `Model.base_url` injection via `modify_models` Protocol callback (Sprint 6c P-102 closed) | shipped | 0059 |
| Copilot poll order `fetch → check → sleep` (Pi `github-copilot.ts:188-226`, W4 M1) + `math.ceil` wait interval (P-144) | shipped | 0059 |
| Copilot `is_dataclass(model)` raises `TypeError` on non-dataclass routed model (P-145 / P-146) | shipped | 0059 |
| `aelix_ai.oauth.openai_codex` (Codex PKCE-callback at port 1455 path `/auth/callback` + `_decode_jwt_payload` + `_get_account_id` + `login_openai_codex` + `refresh_openai_codex_token`) | shipped | 0060 |
| Codex persisted `accountId` (Pi camelCase, P-138) + `originator=pi` (P-140) + `id_token` extras preservation (D.2-authorized) | shipped | 0060 |
| RFC 7519 §3 base64url JWT decode (Pi `atob` bug correction, D.2-authorized) | shipped | 0060 |
| Codex `PI_OAUTH_CODEX_CALLBACK_HOST` → shared `PI_OAUTH_CALLBACK_HOST` → `127.0.0.1` env fallback (P-149) | shipped | 0060 |
| `aelix_ai.oauth.auth_storage` 12 cascade methods + reload-and-retry on OAuth refresh failure (P-142) + DEBUG logging (W4 m5) | shipped | 0061 |
| `aelix_ai.oauth._resolve_config.resolve_config_value` (`!cmd` + `${ENV}` expansion, P-141) | shipped | 0061 |
| `AuthStatus` (frozen) + `AuthSource` 6-value Literal + `FallbackResolver` type alias | shipped | 0061 |
| `aelix_ai.oauth._helpers.maybe_await` (single-owner, drains Anthropic/Copilot/Codex duplicates, P-157) | shipped | 0059 / 0060 |
| `aelix_ai.oauth._registry._OAUTH_DEFERRED_PROVIDERS` drained to `{}` (3/3 Pi built-in providers live; n3) | shipped | 0063 |
| `aelix_ai.oauth._registry._PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]` marked CLOSED | shipped | 0053 / 0061 |
| `src/aelix/__main__.py` `auth login/logout/status/list` subparser (preserves `--mode rpc` back-compat) | shipped | 0062 |
| `aelix auth status <unknown>` → exit 2 + stderr diagnostic (P-152) | shipped | 0062 |
| `aelix auth login` RuntimeError → exit 1 + stderr diagnostic (n1) | shipped | 0062 |
| `tests/pi_parity/test_phase_4_5_strict_superset.py` closure pin (3 live + 0 deferred + 12 cascade methods + `AuthSource` enum + Copilot/Codex constants) | shipped | 0063 |
| `tests/pi_parity/test_phase_4_3_strict_superset.py` strengthening (Sprint 6c closure pin updated: live ∪ deferred = 3 + per-provider `modify_models` attribute) | shipped | 0063 |
| `tests/pi_parity/fixtures/pi_oauth_copilot_codex_734e08e.json` Pi-parity fixture | shipped | 0063 |
| `tests/oauth/test_github_copilot.py` + `test_openai_codex.py` + `test_auth_storage_cascade.py` + `test_resolve_config.py` + `test_copilot_modify_models_integration.py` + `test_types_authstatus.py` | shipped | 0059 / 0060 / 0061 |
| `tests/cli/test_auth_subcommand.py` (subprocess-based CLI regression suite) | shipped | 0062 |
| ADR-0034 amendment — Sprint 6e OAuth catalog complete (3 of 3 Pi providers live) | shipped | 0034 |
| ADR-0053 amendment — Copilot/Codex + cascade carry-forwards marked RESOLVED | shipped | 0053 |

### Sprint 6d sub-table (Phase 4.4 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_coding_agent.rpc._jsonl` (LF-only framing + `JsonlLineReader` + `serialize_json_line` + `attach_jsonl_line_reader`) | shipped | 0056 |
| `aelix_coding_agent.rpc.rpc_types` (29 RpcCommand variants + `RpcSuccessResponse` + `RpcErrorResponse` + `RpcSessionState` 12-field + 9-method UI request + 3-shape UI response) | shipped | 0057 |
| `aelix_coding_agent.rpc.rpc_mode` (9 supported handlers + 20 deferred error stubs + event pipe + SIGTERM/SIGHUP handlers + stdout takeover) | shipped | 0058 |
| `aelix_coding_agent.rpc.rpc_client` (subprocess wrapper + 29-method command surface + `wait_for_idle`/`collect_events`/`prompt_and_wait`) | shipped | 0058 |
| CLI `--mode {interactive,rpc}` flag (`src/aelix/__main__.py`) | shipped | 0058 |
| `AgentHarness` public properties (`pending_message_count` / `session_file` / `session_name` / `steering_mode` / `follow_up_mode`) (P-118) | shipped | 0058 |
| Pi `BashResult` 4/5-key wire shape on `_handle_bash` (P-115 BLOCKING) | shipped | 0058 |
| `_handle_get_state` reads only public harness surface; AST-walk closure pin (P-118) | shipped | 0058 |
| `_handle_new_session` rejects `parent_session` with Sprint-6f deferral envelope (P-117) | shipped | 0058 |
| `_handle_prompt` logs synchronous failures to stderr (P-119 / W4 m2) | shipped | 0058 |
| Parse-path errors always emit `command="parse"` (P-120) | shipped | 0058 |
| `is_streaming` covers every non-idle phase (P-116) | shipped | 0058 |
| `RpcClient.stop()` rejects pending requests with `RpcClientError("rpc", "RPC server stopped")` (W4 M3) | shipped | 0058 |
| `RpcClient` stderr capture capped at 10 MB FIFO (W4 M4) | shipped | 0058 |
| `RpcClient` stale-response logging (W4 m8) | shipped | 0058 |
| `RpcClient` stop wait timeout (5s after SIGKILL, W4 m9) | shipped | 0058 |
| Count drift 28 → 29 / 19 → 20 across docstrings + fixture (W4 M2 / P-121) | shipped | 0058 |
| `command_to_json` wire_key bind (W4 m6 pyright remap) | shipped | 0058 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` closure pin (P-127 U+2028 round-trip + P-128 per-variant field-set + W4 M5 session_file regression) | shipped | 0058 |
| `tests/rpc/test_w6_regressions.py` W6 regression suite (14 tests pinning BLOCKING + MAJOR finds) | shipped | 0058 |
| ADR-0020 Draft → Accepted (Sprint 6d closure) | shipped | 0020 |
| ADR-0034 amendment — Sprint 6d RPC mode partition (9 of 29 RpcCommand variants live) | shipped | 0034 |

### Sprint 6c sub-table (Phase 4.3 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `aelix_ai.oauth.types` (`OAuthCredentials` + `OAuthPrompt`/`AuthInfo`/`SelectOption`/`SelectPrompt`/`LoginCallbacks` + `OAuthProvider` Protocol) | shipped | 0051 |
| `aelix_ai.oauth._pkce` (`generate_pkce` + base64url helper, RFC 7636) | shipped | 0051 |
| `aelix_ai.oauth._oauth_page` (`oauth_success_html` / `oauth_error_html`) | shipped | 0051 |
| `aelix_ai.oauth._callback_server` (`HTTPServer` daemon thread + asyncio bridge) | shipped | 0051 |
| `aelix_ai.oauth._registry` (`get/register/unregister/reset_oauth_provider`) | shipped | 0051 |
| `aelix_ai.oauth._high_level` (`get_oauth_api_key_from_credentials`) | shipped | 0051 |
| `aelix_ai.oauth.anthropic` (login + refresh + `_AnthropicOAuthProvider` + constants) | shipped | 0052 |
| `providers/anthropic.py` Bearer header injection on `is_oauth_token` (P-94) | shipped | 0052 |
| `login_anthropic` late-manual-input fallback (Pi `anthropic.ts:294-307`, P-93) | shipped | 0052 |
| `CALLBACK_HOST` / `CALLBACK_PORT` / `CALLBACK_PATH` wired into `start_callback_server` (P-98) | shipped | 0052 |
| Scope (+ unknown response fields) preserved in `OAuthCredentials.extra` (W4 m7) | shipped | 0052 |
| `aelix_ai.oauth.auth_storage.AuthStorage` (load/save/get/set/remove/get_oauth_api_key) | shipped | 0053 |
| Atomic write (tmp + fsync + `os.replace`) (W4 M1) | shipped | 0053 |
| `0o700` parent dir + `0o600` file mode (Pi parity) | shipped | 0053 |
| `asyncio.Lock` in-process + `fcntl.flock` cross-process POSIX | shipped | 0053 |
| `fcntl` failure handler broadened to `except BaseException` (W4 M5) | shipped | 0053 |
| `default_auth_path` honors `XDG_CONFIG_HOME` (W4 m9) | shipped | 0053 |
| `OAuthCredentials.from_json` raises clear `ValueError` (W4 m1) | shipped | 0051 |
| `start_callback_server` friendly `RuntimeError` on port-in-use (W4 m8) | shipped | 0051 |
| `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (W4 m3 / W5 P-99) | shipped | 0051 |
| `BaseHTTPRequestHandler.log_message` override matches base signature (W4 m4) | shipped | 0051 |
| ADR-0035 amendment — `"auth"` fires only on SDK 401/403 (not eager OAuth detection) | shipped | 0035 |
| ADR-0034 amendment — Sprint 6c OAuth partition (1 of 3 OAuth providers live) | shipped | 0034 |
| RPC mode formal carry-forward (`_PHASE_4_DEFERRED_FEATURES["rpc-mode"]`) | shipped | 0054 |
| AuthStorage layered-cascade carry-forward (`_PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]`) | shipped | 0053 / 0055 |
| Sprint 6a regression `test_sdk_401_translates_to_harness_auth_error` (W4 M3) | shipped | 0055 |
| Sprint 6c W6 regression suite (P-94 bearer header + P-103 OAuth refresh failure E2E) | shipped | 0055 |
| Closure pin strengthening (P-100 live ∪ deferred == 3 sum + P-95 cascade carry) | shipped | 0055 |
| `tests/pi_parity/test_phase_4_3_strict_superset.py` closure pin (1 of 3 OAuth providers live + 2 deferred) | shipped | 0055 |

### Sprint 6b sub-table (Phase 4.2 closure)

| Item | Status | Owner ADR |
|---|---|---|
| `_env_api_keys.py` (30-row provider→envvar table) | shipped | 0047 / 0048 |
| `_sanitize_unicode.py` (`sanitize_surrogates`) | shipped | 0048 |
| `_streaming_json.py` (`parse_streaming_json` lenient) | shipped | 0048 |
| `_openai_client.py` (`openai>=1.50,<2.0` SDK wrapper) | shipped | 0047 |
| `_openai_compat.py` (17-field dataclass + `detect_compat` + `get_compat` with camelCase alias accept) | shipped | 0047 |
| `_transform_messages.py` (shared cross-provider infra) | shipped | 0048 |
| `openai_completions.py` (main adapter, 2 of 9 KnownApi) | shipped | 0047 |
| `aelix_ai.models.clamp_thinking_level` (Sprint 6d full map deferred) | shipped | 0047 / 0050 |
| `ThinkingContent` dataclass + provenance trio + `ImageContent.mime_type/data` + `ToolResultMessage.tool_name` | shipped | 0049 |
| `_map_stop_reason` returns Pi `"toolUse"` (P-57) | shipped | 0050 |
| `_open_stream` uses `with_raw_response.create(**params)` (C-1) | shipped | 0050 |
| `_normalize_tool_call_id` 40-char clamp for every provider (M-6) | shipped | 0050 |
| `stream_simple_openai_completions` sync factory + eager auth raise (P-62) | shipped | 0050 |
| `convert_tools` drops Anthropic `input_schema` leak (P-63) | shipped | 0050 |
| Qwen / qwen-chat-template → `COMPAT_DEFERRED_ALLOWLIST` (M-2) | shipped | 0050 |
| W0 fixture `minimax` / `minimax-cn` rows (P-79) | shipped | 0050 |
| `tests/pi_parity/test_phase_4_2_strict_superset.py` closure pin + parametrized behavior assertions (P-76) | shipped | 0050 |
| `tests/providers/test_w6_regressions.py` regression suite | shipped | 0050 |
| ADR-0034 amendment (2 of 9 KnownApi cardinality note) | shipped | 0034 |
| ADR-0045 amendment §F.2 (Anthropic retrofit deferred) | shipped | 0045 |

### Sprint 6a sub-table (Phase 4.1 closure)

| Item | Status | Owner ADR |
|---|---|---|
| 12 `AssistantMessageEvent` variants (8 new + rename + alias + 2 backfills + P-39d spelling fix) | shipped | 0037 |
| `Provider` Protocol + `register_provider_object` + `unregister_providers_by_source` | shipped | 0045 |
| `SimpleStreamOptions` extensions (cache_retention / transport / timeout_ms / max_retries / max_retry_delay_ms / on_payload / on_response / reasoning / session_id / client) | shipped | 0045 |
| `ProviderResponse` dataclass | shipped | 0045 |
| Anthropic adapter (`aelix_ai.providers.anthropic` + `register_all()` under `aelix-ai.builtin` source_id) | shipped | 0045 |
| `_apply_stream_options_patch` deep-merge port (P-41 fix) | shipped | 0046 |
| `_make_stream_fn` closure + 3 emit-site method bridges (`before_provider_request` / `before_provider_payload` / `after_provider_response`) | shipped | 0046 |
| `AgentHarnessError` widened to 10 codes (`"auth"` Sprint 6a + `"session"`/`"branch_summary"`/`"aborted"` Literal cleanup) | shipped | 0035 |
| ADR-0034 repo slug correction (`badlogic/pi-mono` → `earendil-works/pi`) | shipped | 0034 |
| ADR-0037 / 0038 / 0035 Draft → Accepted | shipped | 0037 / 0038 / 0035 |
| `tests/pi_parity/test_phase_4_strict_superset.py` closure pin | shipped | 0046 |
| DEFERRED_ALLOWLIST closed to `{}` (Phase 2.1 → Phase 4.1 strict superset complete) | shipped | 0046 |

> **번호 공백 (0016)**: ADR-0016 "Phase machine expansion"은 ADR-0023(Compaction + Branch Summary)으로 supersede됩니다. 번호를 재사용하지 않고 gap으로 보존합니다.

## Relationships

```text
0001 (Aelix name)
  └─ 0006 narrows ADR-0001 Consequences (DeepSight 양가절 제거)

0002 (Small kernel → Pi pi-agent-core boundary)
  └─ 0004 reinforces (policy/guardrail은 core 밖, built-in extension)

0003 (pi agent reference + Pi Parity Binding Rule)
  ├─ 0004 implements ADR-0003의 "built-in extension" 약속
  ├─ 0005 reflects pi agent의 multi-source(npm/git) marketplace 흐름
  ├─ 0029 mechanizes — Pi parity test harness로 수동 audit 보강
  └─ 모든 ADR의 상위 원칙: ADR 없는 divergence는 버그

0006 (Standalone platform)
  └─ defines 1st-class 청중: 외부 개발자 + 사내 + customer-site

0009 (Python-first SDK — Phase 1.2 SDK choice)
  └─ 0020 supersedes (partial) — RPC mode Phase 4로 다언어 클라이언트 구체화

0011 (Hook event catalogue v1 — Phase 1.2 임시 결정)
  ├─ 0013 specializes — message_end result type None으로 pin
  ├─ 0014 depends — mutation vs observational 분류가 error policy 기반
  └─ 0017 supersedes — Full Hook Event Catalogue v2 (Phase 2.1)

0013 (message_end observational — Phase 1.2 임시 결정)
  └─ 0018 supersedes — replacement reducer with role preservation (Phase 2.1)

0014 (Hook error policy — Phase 1.2 임시 결정)
  └─ 0019 supersedes — Pi "continue" default + per-handler opt-in (Phase 2.1)

0015 (Accepted — Monorepo layout — uv workspaces)
  ├─ 0020 depends — aelix-rpc는 packages/aelix-rpc/ 패키지
  ├─ 0022 depends — session/ 모듈은 packages/aelix-agent-core/ 위치
  ├─ 0023 depends — compaction.py는 packages/aelix-agent-core/ 위치
  └─ 0031 implements — hatchling은 uv workspace per-package backend

0016 (deferred Phase machine)
  └─ 0023 supersedes — Compaction + Branch Summary (Phase 2.2)

0017 (Full Hook Event Catalogue v2)
  ├─ 0018 depends — message_end result type 변경은 v2 catalogue와 함께
  ├─ 0023 adds emit sites — session_before_compact 등 emit site 추가
  ├─ 0027 depends — asyncio.gather는 ADR-0021 impl (P-7 reversal, Sprint 3c); ADR-0017 tool setters와 연동
  └─ 0030 depends — assert_never는 ADR-0017 28-event 확장 시 적용

0021 (Parallel tool execution)
  └─ 0027 specifies — asyncio.TaskGroup을 Phase 2.1 구현 방식으로 결정

0022 (Session Manager)
  ├─ 0023 depends — compaction/branch_summary는 Session interface에 의존
  └─ 0025 extends — sessionId/messages는 ADR-0022 land 시 _TurnState 추가

0024 (Queue default one-at-a-time)
  └─ 즉시 적용 fix — 재평가 보고서 F-1 해소

0025 (F-10 minimal _TurnState)
  ├─ 0017 ← extends (tools/activeTools/streamOptions/resources/thinkingLevel)
  └─ 0022 ← extends (messages/sessionId)

0029 (Pi parity acceptance test harness)
  ├─ 0026 depends — workspace-root tests/가 tests/pi_parity/ 홈
  └─ 0032 supports — W5 Pi parity audit의 기계화

0032 (Sprint workflow — mandatory gates)
  └─ 0029 mechanizes W5 — parity test harness가 성숙하면 W5 scope 축소 가능

0034 (Pi reference version pin)
  ├─ 0003 refines — pi agent primary reference에 SHA pin 추가
  └─ 0029 supports — Pi parity test harness가 pin SHA에 anchored

0035 (Error code taxonomy)
  ├─ 0017 owns "aborted" wiring (Phase 2.1)
  ├─ 0022 owns "session" wiring (Phase 2.2)
  ├─ 0023 owns "compaction" / "branch_summary" wiring (Phase 2.2)
  ├─ 0025 follows — minimal-shell pattern (문서 먼저, code on demand)
  └─ 0030 depends — assert_never가 10-code 전체에 적용

0036 (Loop AgentEvent vs Harness HookEvent — F-7)
  ├─ 0011 amends — 16 hook events는 HookEvent union 소속임을 명시
  ├─ 0017 cross-references — Phase 2.1 28-event 확장은 HookEvent에만 적용
  ├─ 0029 splits parity tests — loop vs harness 별도 lane
  └─ 0030 doubles scope — 두 union 각각 exhaustiveness 적용

0037 (Streaming event union — Pi parity)
  ├─ 0025 follows — minimal-shell pattern (documentation 먼저)
  ├─ 0030 depends — Phase 4 expanded union에 assert_never 적용
  └─ 0038 paired — Phase 4 adapters가 12-event union emit

0038 (stream_simple dispatch shell — Phase 1 boundary)
  ├─ 0017 enables — Phase 2.x `before_provider_request` 가 dispatch 사용 가능
  ├─ 0020 paired — Phase 4 provider work / RPC mode와 함께 land
  ├─ 0025 sibling — 동일 minimal-shell + owning-ADR cadence
  └─ 0037 paired — Phase 4 adapters가 expanded event union emit

0039 (Phase 2.1 strict superset closure)
  ├─ 0017 closes — `tool_execution_update` + tool-result `message_start/end` emit sites landed (Sprint 3d / P-9)
  ├─ 0021 closes — §E matrix rows 3 + 6 implemented in same sprint
  ├─ 0029 mechanizes — E.5 closure pin (`tests/pi_parity/test_phase_2_1_strict_superset.py`) is the durable regression guard
  ├─ 0034 anchors — fixture pinned to SHA `734e08e`
  ├─ 0022 forward — `session_*` events deferred to Phase 2.2 emit owner
  ├─ 0023 forward — `session_before_compact` / `session_compact` / `session_before_tree` / `session_tree` deferred owner
  └─ 0038 forward — `before_provider_request` / `before_provider_payload` / `after_provider_response` deferred to Phase 4 provider adapter
```

## Open Questions (pending ADRs)

`../02-initial-requirements.md` 열린 질문 5개 중 현재 상태.

| Q   | 질문                                                                  | 상태                                                                                                |
| --- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Q1  | Extension isolation: process / container / remote — 무엇이 기본?      | 결정됨 — in-process 고정, isolation은 protocol에 두지 않음 (ADR-0007).                              |
| Q2  | Marketplace pack signing과 trust chain 형식?                          | 결정됨 — source-책임 그대로, 1차 통합 schema 도입 안 함 (ADR-0010).                                  |
| Q3  | Aelix SDK는 Python-only로 시작 vs 다언어 manifest contract 우선?      | 결정됨 — Python-first SDK (ADR-0009). 다언어 외부 클라이언트는 Phase 4 RPC mode (ADR-0020 Draft).   |
| Q4  | Agent orchestration: runtime core vs extension layer?                 | 결정됨 — 단일 agent loop는 core, multi-agent orchestration은 extension layer (ADR-0008).             |
| Q5  | DeepSight 통합: product integration vs extension pack?                | 결정됨 — Mode A Product Integration. DeepSight는 use case 중 하나 (ADR-0006).                       |

Phase 1.2 sprint spec Section F의 후속 ADR 상태.

| Proposed ADR | 상태                                                                                                                              |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| ADR-0011 Hook event catalogue v1        | 결정됨 (ADR-0011) → **Phase 1.2 임시 결정으로 강등**. Phase 2.1에 ADR-0017로 대체 예정.              |
| ADR-0012 Extension discovery model      | 보류 → Phase 3에서 ADR-0028로 부분 해소. entry_points primary + directory fallback 결정.             |
| ADR-0013 message_end reducer policy     | 결정됨 (ADR-0013) → **Phase 1.2 임시 결정으로 강등**. Phase 2.1에 ADR-0018로 대체 예정.              |
| ADR-0014 Hook error policy              | 결정됨 (ADR-0014) → **Phase 1.2 임시 결정으로 강등**. Phase 2.1에 ADR-0019로 대체 예정.              |
| ADR-0015 ExtensionContext UI surface    | **번호 재배정** — ADR-0015는 Monorepo Layout (uv workspaces)로 사용 **(Accepted)**. UI surface는 ADR-0033 (Phase 5). |
| ADR-0016 Phase machine expansion        | **ADR-0023로 supersede** — Compaction + Branch Summary (Phase 2.2).                                   |

Sprint 2 ADRs 상태.

| ADR   | 제목                                                        | Status                        |
| ----- | ----------------------------------------------------------- | ----------------------------- |
| 0015  | Monorepo Layout — uv Workspaces                             | Accepted (Phase 1.3 shipped)  |
| 0025  | F-10 Minimal Turn-State Snapshot Rationale                  | Accepted (Phase 1.3 shipped)  |
| 0026  | Workspace-Root Pytest Layout                                | Accepted (Sprint 2 shipped)   |
| 0027  | asyncio.TaskGroup for Parallel Tool Execution               | Accepted (Sprint 3c / Phase 2.1.3 shipped — DECISION REVERSED to asyncio.gather) |
| 0028  | Extension Auto-Discovery via entry_points                   | Draft (Phase 3)               |
| 0029  | Pi-Parity Acceptance Test Harness                           | Draft (Phase 2.1+)            |
| 0030  | Hook Event Exhaustiveness via assert_never                  | Draft (Phase 2.1)             |
| 0031  | Build Backend Choice — Hatchling                            | Accepted (Sprint 2 shipped)   |
| 0032  | Sprint Workflow — Review + Pi Parity + Suggestions          | Accepted (Sprint 2 onwards)   |

Sprint 2.5 ADRs 상태 (Phase 1.4 hygiene).

| ADR   | 제목                                                                 | Status                                                                |
| ----- | -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 0034  | Pi Reference Version Pin                                             | Accepted (Sprint 2.5 shipped)                                         |
| 0035  | Error Code Taxonomy                                                  | Draft (Phase 1.4 shipped — Aelix subset; full taxonomy Phase 2.1+)    |
| 0036  | Loop AgentEvent vs Harness HookEvent Distinction (F-7)               | Accepted (Sprint 3a — code-land: AgentEventName + AgentHarnessEventName aliases) |
| 0037  | Streaming Event Union (Pi Parity)                                    | Draft (Phase 1.4 shell; adapter coverage Phase 4)                     |
| 0038  | stream_simple Dispatch Shell — Phase 1 Boundary                      | Accepted (Sprint 2.5 / Phase 1.4 shipped — body lands Phase 4)        |

Sprint 3a ADRs 상태 (Phase 2.1.1 hook bus expansion).

| ADR   | 제목                                                                 | Status                                                                |
| ----- | -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 0017  | Full Hook Event Catalogue v2 (28 events)                             | Accepted (Sprint 3a / Phase 2.1.1 shipped — Pi-verified)              |
| 0019  | Hook Error Policy v2 — Pi `"throw"` Default + Aelix `"continue"` Opt-in | Accepted (Sprint 3a / Phase 2.1.1 shipped — v3 reframe)            |
| 0030  | Hook Event Exhaustiveness via assert_never                            | Accepted (Sprint 3a / Phase 2.1.1 shipped)                            |
| 0036  | Loop AgentEvent vs Harness HookEvent Distinction (F-7) — code-land    | Accepted (Sprint 3a — AgentEventName + AgentHarnessEventName aliases) |

Sprint 3b ADRs 상태 (Phase 2.1.2 — setters + nextTurn/appendMessage + pendingSessionWrites).

| ADR   | 제목                                                                 | Sprint 3a Status                       | Sprint 3b Status                                                                                          |
| ----- | -------------------------------------------------------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 0013  | message_end Reducer Policy — Observational in Phase 1.2              | Accepted (Phase 1.2 임시 결정)         | Accepted (Phase 1.2 임시 결정 — Sprint 3b 재확정: message_end는 영구 observational)                          |
| 0017  | Full Hook Event Catalogue v2                                         | Accepted (Sprint 3a)                   | Accepted (+ §"Setter emit sites landed Sprint 3b" — P-4 correction: setters don't emit `queue_update`)     |
| 0018  | message_end Replacement Reducer                                      | Draft (Phase 2.1)                      | **Deprecated** — Pi has no message_end reducer at SHA 734e08e (Sprint 3b §0 verdict)                       |
| 0036  | Loop AgentEvent vs Harness HookEvent Distinction (F-7)               | Accepted (Sprint 3a)                   | Accepted (unchanged)                                                                                       |

Sprint 3c ADRs 상태 (Phase 2.1.3 parallel tool execution).

| ADR   | 제목                                                      | Status                                                                  |
| ----- | --------------------------------------------------------- | ----------------------------------------------------------------------- |
| 0017  | Full Hook Event Catalogue v2 (Sprint 3c subsection added) | Accepted (Sprint 3c — Tool execution dispatch subsection added)         |
| 0021  | Parallel-Mode Tool Execution + Per-Tool Override          | Accepted (Sprint 3c / Phase 2.1.3 shipped)                              |
| 0027  | asyncio.gather for Parallel Tool Execution (P-7 reversal) | Accepted (Sprint 3c / Phase 2.1.3 shipped — DECISION REVERSED)          |

Sprint 3d ADRs 상태 (Phase 2.1.4 — Phase 2.1 strict-superset closure / P-9).

| ADR   | 제목                                                                  | Status                                                                  |
| ----- | --------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 0017  | Full Hook Event Catalogue v2 (Sprint 3d subsection added)             | Accepted (Sprint 3d — `tool_execution_update` + tool-result message emit sites landed) |
| 0021  | Parallel-Mode Tool Execution (§E matrix rows 3 + 6 implemented)       | Accepted (Sprint 3d / Phase 2.1.4 — partial-emit drain + `_emit_tool_result_message`) |
| 0039  | Phase 2.1 Strict Superset Closure                                     | Accepted (Sprint 3d / Phase 2.1.4 shipped — P-1..P-9 roster + E.5 closure pin) |

Sprint 4a ADRs 상태 (Phase 2.2.1 Session Manager + JsonlSessionRepo).

| ADR   | 제목                                                              | Status                                                                |
| ----- | ----------------------------------------------------------------- | --------------------------------------------------------------------- |
| 0017  | Session message_end wiring subsection added                       | Accepted (Sprint 4a — append-then-emit Pi parity)                     |
| 0022  | Session Manager + JSONL Persistence                               | Accepted (Sprint 4a / Phase 2.2.1 shipped — major revision per P-11/P-12/P-13) |
| 0025  | F-10 Minimal Turn-State Snapshot — Sprint 4b extension note added | Accepted (Sprint 4b — _TurnState.messages + session_id shipped)       |
| 0039  | Phase 2.1 Strict Superset Closure — P-11 lockdown added           | Accepted (no Phase 2.1 regression)                                    |

Sprint 4b ADRs 상태 (Phase 2.2.2 compact + navigate + Phase machine + 4 session emits).

| ADR   | 제목                                                              | Status                                                                |
| ----- | ----------------------------------------------------------------- | --------------------------------------------------------------------- |
| 0017  | Session emit sites + payload extensions subsection added          | Accepted (Sprint 4b — P-17/P-18/P-19/P-20 payload extensions)         |
| 0022  | Sprint 4a → 4b transition plan marker → Completed                 | Accepted (4b deferred items all shipped)                              |
| 0023  | Compaction + Branch Summary                                       | Accepted (Sprint 4b / Phase 2.2.2 shipped — P-14/P-15/P-16 verified)  |
| 0025  | F-10 Minimal Turn-State — messages/session_id extension shipped   | Accepted (Sprint 4b — _TurnState fields populated)                    |
| 0039  | Phase 2.1 closure — DEFERRED_ALLOWLIST trimmed                    | Accepted (4 session_* removed; ADR-0040 supersedes Phase 2.2 tracking)|
| 0040  | Phase 2.2 Strict Superset Closure                                 | Accepted (Sprint 4b / Phase 2.2.2 shipped — closure)                  |

Sprint 5a ADRs 상태 (Phase 3.1.1 Extension auto-discovery + ExtensionAPI full surface + 3 event registration).

| ADR   | 제목                                                                              | Status                                                                              |
| ----- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 0017  | Phase 3.1 event additions subsection added (input/user_bash/resources_discover)   | Accepted (Sprint 5a — 28 → 31 events; P-24/P-25/P-26 closure)                       |
| 0019  | error_mode overload extends to 3 new Sprint 5a events                             | Accepted (Sprint 5a — no v3 change, surface extension)                              |
| 0028  | Extension Auto-Discovery — P-21 REVERSAL (directory-scan PRIMARY)                 | **Accepted (Sprint 5a / Phase 3.1.1 — Draft → Accepted with corrected framing)**    |
| 0033  | ExtensionContext UI surface — `ui` field exposed as deferred-raise (Phase 5)      | Deferred (Phase 5) — Sprint 5a placeholder attribute landed                         |
| 0039  | Phase 2.1 closure — DEFERRED_ALLOWLIST extended with 3 Sprint 5a events           | Accepted (3 entries added with ADR-0042 owner)                                      |
| 0041  | **Phase 3.1 Extension API Full Surface Closure**                                  | **Accepted (Sprint 5a / Phase 3.1.1 shipped — P-21~P-28 roster + closure pin)**     |

Draft ADR 및 target Phase 요약 (전체).

| ADR   | 제목                                       | Target Phase |
| ----- | ------------------------------------------ | ------------ |
| 0029  | Pi-Parity Acceptance Test Harness          | Phase 2.1+ (foundation shipped Sprint 3a) |
| 0033  | ExtensionContext UI surface                | Phase 5 (Sprint 5a exposes attribute as deferred-raise) |
| 0035  | Error Code Taxonomy (Literal widening)     | Per owning ADR (0017 done; 0022 / 0023 / Phase 4) |
| 0037  | Streaming Event Union — adapter coverage   | Phase 4      |

Open question이 ADR로 정리되면 이 표를 함께 갱신합니다.
