# 0026. Workspace-Root Pytest Layout

Status: Accepted (Sprint 2 shipped)

## Context

Pi는 각 `packages/*`가 자체 `vitest.config.ts`를 보유하는 per-package test
layout을 사용합니다. Pi의 JavaScript/TypeScript 생태계에서는 이 구조가
자연스럽습니다.

Aelix Sprint 2 spec §A는 workspace-root 단일 `tests/` layout을 선택했습니다.
이 결정의 명시적 근거가 필요합니다.

### 고려한 대안

1. **Per-package tests** (`packages/aelix-agent-core/tests/`,
   `packages/aelix-ai/tests/`): Pi와 구조적으로 일치하지만 Python pytest
   생태계에서 cross-package fixture 공유가 어렵습니다.
2. **Workspace-root shared `tests/`** (채택): 단일 testpaths로 모든 패키지를
   커버합니다.

## Decision

Aelix는 workspace-root 공유 `tests/` layout을 유지합니다.

이유:

1. **Cross-cutting fixture 중복 방지**: Sprint 1의 `test_loop_with_hooks` 등
   기존 tests가 `aelix_agent_core` + `aelix_ai` 양쪽을 import합니다.
   per-package split 시 fixture가 중복되거나 shared conftest가 필요해집니다.
2. **pytest 단일 testpaths**: pytest는 `[tool.pytest.ini_options] testpaths = ["tests"]`
   선언 하나로 모든 workspace 패키지를 across 테스트할 수 있습니다.
   vitest와 architectural mismatch가 아닙니다.
3. **Pi parity acceptance test 자연스러운 위치**: ADR-0029의 `tests/pi_parity/`
   lane은 cross-package fixture (`aelix_agent_core` + `aelix_ai` 동시 사용)를
   활용합니다. workspace-root가 자연스러운 홈입니다.

## Consequences

- Phase 3 coding-agent-only tests는 `tests/` 아래에 agent-core tests와 나란히
  위치합니다. 개념적 분리는 디렉토리 명명으로 처리합니다 (예:
  `tests/coding_agent/`).
- `uv run pytest` 단일 명령으로 workspace 전체를 테스트합니다.
- Phase 3에서 전체 tests 수가 ~400개를 초과하면 per-package split을 재고합니다.
  그 시점에 새 ADR로 갱신합니다.
- Pi와 다른 **의도된 divergence** (additive, 행동은 동일합니다).
  Pi의 per-package vitest는 JavaScript 생태계 idiom이고, Aelix의 workspace-root
  pytest는 Python 생태계 idiom입니다.
