# 0020. RPC Mode for Multi-Language Clients

Status: Accepted (Sprint 6d / Phase 4.4 / W6 shipped)
Supersedes (partial): ADR-0009 ("future RPC ADR" indefinite deferral)

## Sprint 6d closure (2026-05-19)

Sprint 6d shipped the JSONL protocol + RpcCommand/Response/SessionState
types + rpc_mode dispatcher + RpcClient subprocess wrapper + CLI
`--mode rpc` flag. **9 commands** wired directly to the existing
`AgentHarness` surface; **20 commands** ship as Pi-shape
`RpcErrorResponse` stubs per the `DEFERRED_COMMANDS` allowlist
(ADR-0058). Sub-sprints 6e (ModelRegistry + extension/skill aggregation)
and 6f (steer/follow_up paths + session tree navigation + bash
cancellation + extension UI bridge) close the remaining 20-command
surface.

The Pi parity invariant (live + deferred == 29) is mechanically enforced
by `tests/pi_parity/test_phase_4_4_strict_superset.py` (ADR-0058 closure
pin).

## Context

Pi 1.0은 `pi --mode rpc`(stdin/stdout JSON 프로토콜)로 다언어 클라이언트를
지원합니다. Python, Java, Go, Rust 등 다른 언어 앱이 Pi를 자식 프로세스로 띄워
JSON message를 stdin으로 보내고 stdout에서 응답을 받는 방식입니다.

ADR-0009는 다언어 외부 클라이언트를 "향후 RPC ADR"로 indefinitely defer했습니다.
Pi가 1.0에서 이미 `--mode rpc`를 ship했으므로, 1차 원칙(Pi parity)에 따라 Phase 4
에 명시적으로 스케줄합니다.

`--mode rpc`는 Pi baseline 기능입니다. indefinite defer는 Pi parity 결여이므로
이 ADR이 ADR-0009를 부분 대체합니다(Python-first SDK 결정 자체는 유지).

## Decision

Phase 4에서 `packages/aelix-rpc/` 별도 패키지를 작성합니다(ADR-0015 monorepo).

- **CLI subcommand**: `aelix mode rpc` (또는 `aelix --mode rpc`).
- **프로토콜**: stdin/stdout JSON 라인 기반. Pi `--mode rpc` 프로토콜 v1을 그대로 port합니다.
- **Schema**: Pi RPC schema를 인용합니다. Aelix-specific extension은 명시적으로 명시합니다.
- **스트리밍**: JSON-lines streaming (각 줄 = 한 event 또는 chunk).
- **인증/authorization**: Phase 4 real provider 구현(ADR-TBD)과 연동합니다.

Pi RPC 프로토콜 참조: Pi `packages/agent/src/rpc/` 및 `pi --mode rpc` CLI entry.

## Consequences

- `packages/aelix-rpc/` 패키지: `pyproject.toml` 독립, `aelix-agent-core` 의존.
- `aelix mode rpc` subcommand 추가. stdin 열림 → event loop 시작 → stdout JSON 응답.
- Phase 4 이전에는 Python SDK만으로 Aelix runtime을 호출합니다(ADR-0009 유지).
- 다언어 sample 클라이언트(Java, Go, Rust)는 Phase 6에서 제공합니다.
- Pi RPC schema와의 drift는 `test_rpc_mode_handles_pi_protocol_v1_messages`로 방어합니다.
