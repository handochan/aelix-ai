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
| 0012 | Extension Discovery Model                                                                                                                              | Deferred                            | Phase 3에서 결정. (`~/.aelix/extensions`, `pyproject.toml`, `entry_points` 우선순위 미결정.)                                                        |
| 0013 | [message_end Reducer Policy — Observational in Phase 1.2](0013-message-end-observational-in-1-2.md)                                                    | Accepted (Phase 1.2 임시 결정)      | Phase 1.2의 `message_end`는 observational only. Phase 2.1에 ADR-0018로 대체.                                                                        |
| 0014 | [Hook Error Policy — Mutation hooks throw; Lifecycle observational hooks swallow+log](0014-hook-error-policy.md)                                       | Accepted (Phase 1.2 임시 결정)      | Mutation hook 예외 → `AgentHarnessError` raise; lifecycle observational 예외 → swallow. Phase 2.1에 ADR-0019로 대체.                                |
| 0015 | [Monorepo Layout — uv Workspaces](0015-monorepo-layout-uv-workspaces.md)                                                                               | Draft (Phase 1.3 finalization)      | Phase 1.3 경계에서 uv workspaces 마이그레이션. Pi `packages/*` 구조와 1:1 매핑.                                                                    |
| 0017 | [Full Hook Event Catalogue v2](0017-full-hook-event-catalogue-v2.md)                                                                                   | Draft (Phase 2.1 finalization)      | Pi `AgentHarnessEvent` 전체 ~30개 event 등록. ADR-0011 대체.                                                                                        |
| 0018 | [message_end Replacement Reducer (Pi parity)](0018-message-end-replacement-reducer.md)                                                                  | Draft (Phase 2.1)                   | `message_end`에 role-preserving replacement reducer 구현. ADR-0013 대체.                                                                            |
| 0019 | [Hook Error Policy v2 — Pi `"continue"` Default](0019-hook-error-policy-v2-pi-continue-default.md)                                                     | Draft (Phase 2.1)                   | 모든 hook `"continue"` default + per-handler `error_mode="throw"` opt-in. ADR-0014 대체.                                                           |
| 0020 | [RPC Mode for Multi-Language Clients](0020-rpc-mode-multi-language-clients.md)                                                                          | Draft (Phase 4)                     | `aelix mode rpc` — stdin/stdout JSON 프로토콜. Pi `--mode rpc` 그대로 port. ADR-0009 부분 대체.                                                     |
| 0021 | [Parallel-Mode Tool Execution + Per-Tool Override](0021-parallel-tool-execution.md)                                                                     | Draft (Phase 2.1)                   | default parallel 실행 + per-tool `execution_mode="sequential"` override.                                                                           |
| 0022 | [Session Manager + JSONL Persistence](0022-session-manager-jsonl-persistence.md)                                                                        | Draft (Phase 2.2)                   | `Session` interface + `JsonlSessionRepo`. `~/.aelix/sessions/{id}.jsonl` append-only.                                                              |
| 0023 | [Compaction + Branch Summary](0023-compaction-branch-summary.md)                                                                                        | Draft (Phase 2.2)                   | `compact()` + `navigateTree()` + Phase machine `idle\|turn\|compaction\|branch_summary`. ADR-0016 대체.                                             |
| 0024 | [Queue Default `"one-at-a-time"` (Pi parity)](0024-queue-default-one-at-a-time.md)                                                                      | Accepted (Phase 1.2 follow-up fix)  | `steering_mode` / `follow_up_mode` default를 `"all"` → `"one-at-a-time"`으로 즉시 flip.                                                            |
| 0025 | ExtensionContext UI surface                                                                                                                             | Deferred (Phase 5)                  | `ExtensionUIContext` 전체 표면. Phase 5 TUI/Web UI 구현 시 결정. (구 spec의 "ADR-0015" 항목 → 번호 충돌로 0025로 재배정.)                           |

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

0015 (Monorepo layout — uv workspaces)
  ├─ 0020 depends — aelix-rpc는 packages/aelix-rpc/ 패키지
  ├─ 0022 depends — session/ 모듈은 packages/aelix-agent-core/ 위치
  └─ 0023 depends — compaction.py는 packages/aelix-agent-core/ 위치

0016 (deferred Phase machine)
  └─ 0023 supersedes — Compaction + Branch Summary (Phase 2.2)

0017 (Full Hook Event Catalogue v2)
  ├─ 0018 depends — message_end result type 변경은 v2 catalogue와 함께
  └─ 0023 adds emit sites — session_before_compact 등 emit site 추가

0022 (Session Manager)
  └─ 0023 depends — compaction/branch_summary는 Session interface에 의존

0024 (Queue default one-at-a-time)
  └─ 즉시 적용 fix — 재평가 보고서 F-1 해소
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
| ADR-0012 Extension discovery model      | 보류 — Phase 3에서 결정. ADR-0012 deferred.                                                          |
| ADR-0013 message_end reducer policy     | 결정됨 (ADR-0013) → **Phase 1.2 임시 결정으로 강등**. Phase 2.1에 ADR-0018로 대체 예정.              |
| ADR-0014 Hook error policy              | 결정됨 (ADR-0014) → **Phase 1.2 임시 결정으로 강등**. Phase 2.1에 ADR-0019로 대체 예정.              |
| ADR-0015 ExtensionContext UI surface    | **번호 재배정** — ADR-0015는 Monorepo Layout (uv workspaces)로 사용. UI surface는 ADR-0025 (Phase 5). |
| ADR-0016 Phase machine expansion        | **ADR-0023로 supersede** — Compaction + Branch Summary (Phase 2.2).                                   |

Draft ADR 및 target Phase 요약.

| ADR   | 제목                                       | Target Phase |
| ----- | ------------------------------------------ | ------------ |
| 0015  | Monorepo Layout — uv Workspaces            | Phase 1.3    |
| 0017  | Full Hook Event Catalogue v2               | Phase 2.1    |
| 0018  | message_end Replacement Reducer            | Phase 2.1    |
| 0019  | Hook Error Policy v2 — Pi continue default | Phase 2.1    |
| 0020  | RPC Mode for Multi-Language Clients        | Phase 4      |
| 0021  | Parallel-Mode Tool Execution               | Phase 2.1    |
| 0022  | Session Manager + JSONL Persistence        | Phase 2.2    |
| 0023  | Compaction + Branch Summary                | Phase 2.2    |
| 0024  | Queue Default "one-at-a-time"              | Phase 1.2 (즉시) |
| 0025  | ExtensionContext UI surface                | Phase 5      |

Open question이 ADR로 정리되면 이 표를 함께 갱신합니다.
