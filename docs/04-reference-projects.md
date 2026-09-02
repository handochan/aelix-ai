# Reference Projects

Status: Accepted

## 목적

Aelix는 완전히 새로운 추상 설계가 아니라, 이미 존재하는 agent runtime과 coding
agent 생태계의 장단점을 비교하면서 구현합니다. 이 문서는 계속 참조할 프로젝트와
각 프로젝트에서 관찰할 관점을 명시합니다.

## Primary Reference

### pi agent

pi agent는 Aelix의 primary reference입니다.

Aelix는 pi agent를 Python으로 재구현해 왔고, 그 포팅은 A 단계 종료(ADR-0086)로
닫혔습니다. **이제 목표는 parity가 아닙니다** — pi agent는 따라가야 할 기준이 아니라
*검증된 참조 구현*이며, divergence는 ADR을 요구하지 않습니다(ADR-0235).

문제가 생기거나 새 표면을 설계할 때는 pi agent의 **최신** 코드와 이슈를 먼저
확인합니다. 상당 부분이 그것의 포팅이므로 같은 문제를 이미 풀어 놓았을 가능성이
높습니다. 다만 명세가 아니라 증거로 읽습니다.

기존 ADR이 인용하는 SHA 핀(`734e08e`, ADR-0034)은 그 인용들이 해석되는 앵커로
남아 있으며, 현재 작업이 맞춰야 할 버전이 아닙니다.

포팅 범위에서 pi agent와 의도적으로 달랐던 부분은 그대로입니다: permission system과
최소 guardrail을 built-in extension으로 포함해 더 안정적인 기반에서 시작합니다
(ADR-0004).

참조 관점:

- agent runtime loop
- tool/extension invocation model
- state and context handling
- extension surface
- developer experience
- raw flexibility와 안정성 사이의 tradeoff

## Coding Agent References

### opencode

참조 관점:

- coding agent UX
- tool execution model
- session and project context handling
- extensibility pattern

### oh my opencode / oh my openagent

참조 관점:

- opencode 계열 extension ecosystem
- agent customization pattern
- community-driven pack 또는 preset 구조
- marketplace/registry로 확장 가능한 packaging 방식

### Claude Code

참조 관점:

- mature coding agent interaction model
- permission prompt와 tool approval UX
- workspace-aware execution
- codebase navigation and editing workflow
- safety boundary와 productivity 사이의 균형

### oh my claudecode

참조 관점:

- Claude Code 사용성을 확장하는 convention
- command, preset, workflow packaging
- user-level customization layer

### clawcodex

참조 관점:

- Codex-style coding agent workflow
- local agent runtime adaptation
- command/tool abstraction
- project-aware automation pattern

## Internal Reference

### deepsight-ai

deepsight-ai는 이미 구현 중이던 내부 프로젝트이며, Aelix를 다시 시작하게 된
직접적인 배경입니다. 기존 구현은 너무 복잡해졌지만, 그 안에는 많은 시행착오와
재사용할 만한 설계 판단이 있습니다.

참조 관점:

- 복잡도가 커진 원인
- 유지해야 할 좋은 추상화
- 버려야 할 과도한 결합
- domain platform과 범용 runtime의 경계
- customer-site deployment 경험
- permission, policy, workflow 관련 시행착오

## 참조 원칙

- pi agent는 primary reference로 두되, parity 대상이 아니라 검증된 참조 구현으로 다룹니다(ADR-0235).
- 다른 프로젝트는 그대로 복제하기보다 특정 설계 관점별로 비교합니다.
- deepsight-ai의 기능을 무비판적으로 이식하지 않습니다. 복잡도가 커진 원인을 먼저 분리합니다.
- reference에서 가져온 결정은 필요하면 `decisions/`에 ADR로 남깁니다.

