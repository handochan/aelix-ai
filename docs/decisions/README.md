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
| 0020 | [RPC Mode for Multi-Language Clients](0020-rpc-mode-multi-language-clients.md)                                                                          | Draft (Phase 4)                     | `aelix mode rpc` — stdin/stdout JSON 프로토콜. Pi `--mode rpc` 그대로 port. ADR-0009 부분 대체.                                                     |
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
| 0020  | RPC Mode for Multi-Language Clients        | Phase 4      |
| 0029  | Pi-Parity Acceptance Test Harness          | Phase 2.1+ (foundation shipped Sprint 3a) |
| 0033  | ExtensionContext UI surface                | Phase 5 (Sprint 5a exposes attribute as deferred-raise) |
| 0035  | Error Code Taxonomy (Literal widening)     | Per owning ADR (0017 done; 0022 / 0023 / Phase 4) |
| 0037  | Streaming Event Union — adapter coverage   | Phase 4      |

Open question이 ADR로 정리되면 이 표를 함께 갱신합니다.
