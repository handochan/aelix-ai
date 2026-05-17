# 0002. Start with a Small Runtime Kernel

Status: Accepted

## Context

Aelix는 장기적으로 orchestration, policy, marketplace, multi-agent coordination,
SDK, customer-site deployment를 포함할 수 있습니다. 그러나 초기부터 모든 기능을
core에 넣으면 runtime contract가 불안정해지고 확장 생태계를 만들기 어렵습니다.

## Decision

초기 구현은 작은 runtime kernel로 시작합니다.

Kernel의 초기 책임은 다음으로 제한합니다.

- extension registration
- extension invocation
- execution context creation
- policy decision before execution
- structured extension result

Marketplace는 code execution이 아니라 manifest indexing부터 시작합니다.

## Consequences

- Aelix runtime core는 Pi의 `pi-agent-core` 경계와 동일하게: loop + hook bus + extension runtime + (Phase 2.2+) session manager까지 포함합니다. Pi citation: `packages/agent/src/harness/agent-harness.ts` (~996 LOC). Multi-agent orchestration / domain extensions는 core 밖입니다.
- Extension contract와 manifest schema가 먼저 안정화됩니다.
- Enterprise policy와 marketplace trust model을 나중에 추가하기 쉬워집니다.

