# 0008. Agent Loop in Core, Orchestration in Extensions

Status: Accepted

## Context

`02-initial-requirements.md`의 Open Question Q4는 "Agent orchestration은
runtime core에 둘 것인가, extension layer로 둘 것인가"를 미결정 항목으로
두었습니다.

orchestration이라는 단어는 두 수준을 가리킬 수 있습니다.

- L1 단일 agent loop: LLM 호출, tool call, 결과 처리, 반복
- L2 multi-agent orchestration: subagent delegation, planner, parallel
  execution, shared task coordination

Pi에서 두 수준은 다음과 같이 분리되어 있습니다.

- `pi-agent-core`: agent runtime with tool calling and state management (L1)
- `pi-subagents` 같은 extension: subagent delegation, parallel execution (L2)

ADR-0002는 "작은 kernel"을 원칙으로, kernel을 extension invocation,
ExecutionContext, registry, lifecycle hook으로 제한했습니다. ADR-0003은 pi
agent를 primary reference로 두었습니다. ADR-0004는 policy/guardrail을
built-in extension으로 두었습니다.

## Decision

Aelix는 orchestration을 다음과 같이 두 수준으로 분리합니다.

- **L1 단일 agent loop는 runtime core 책임**입니다. invocation, tool call
  처리, 결과 dispatch, lifecycle hook 발행이 여기 포함됩니다. 이는 ADR-0002
  kernel 범위에 자연스럽게 포함됩니다.
- **L2 multi-agent / planner / subagent orchestration은 extension layer**의
  책임입니다. runtime core에는 multi-agent 개념을 두지 않습니다.

L2 orchestration extension은 lifecycle hook과 invocation API를 사용해 다른
extension을 호출하거나 병렬 실행을 구성할 수 있습니다.

## Consequences

- runtime core는 단일 agent loop까지만 책임지고, multi-agent coordination은
  protocol 안에 들어오지 않습니다.
- subagent helper, planner, parallel execution 같은 기능은 모두 extension으로
  제공됩니다. 일부는 기본 번들에 포함될 수 있지만 core가 아닙니다.
- pi agent의 `pi-agent-core` + `pi-subagents` 분리 모델과 동형입니다.
- multi-agent algorithm은 protocol 변경 없이 새 extension으로 교체/추가될
  수 있습니다. ADR-0002 "Stable Contracts, Replaceable Internals"와 정합.
- `02-initial-requirements.md` Runtime 절의 "장기적으로 orchestration,
  multi-agent coordination 포함"이라는 표현은 "extension layer에서"라는
  맥락으로 해석합니다. 필요 시 문서를 후속 갱신합니다.

