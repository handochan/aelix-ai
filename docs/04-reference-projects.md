# Reference Projects

Status: Accepted

## 목적

Aelix는 완전히 새로운 추상 설계가 아니라, 이미 존재하는 agent runtime과 coding
agent 생태계의 장단점을 비교하면서 구현합니다. 이 문서는 계속 참조할 프로젝트와
각 프로젝트에서 관찰할 관점을 명시합니다.

## Primary Reference

### pi agent

pi agent는 Aelix의 가장 직접적인 구현 기준입니다.

Aelix의 목표는 pi agent를 Python으로 거의 완벽에 가깝게 구현하는 것입니다.
다만 pi agent의 날것에 가까운 유연성을 그대로 가져오는 데서 끝나지 않고,
permission system과 최소 guardrail을 built-in extension으로 포함해 더 안정적인
기반에서 시작합니다.

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

- pi agent는 primary reference로 둡니다.
- 다른 프로젝트는 그대로 복제하기보다 특정 설계 관점별로 비교합니다.
- deepsight-ai의 기능을 무비판적으로 이식하지 않습니다. 복잡도가 커진 원인을 먼저 분리합니다.
- reference에서 가져온 결정은 필요하면 `decisions/`에 ADR로 남깁니다.

