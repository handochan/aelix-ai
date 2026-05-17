# 0031. Build Backend Choice — Hatchling

Status: Accepted (Sprint 2 shipped)

## Context

Sprint 2 spec §C에서 hatchling build backend를 선택했습니다. Python 생태계의
build backend 후보:

| Backend | 특징 | 탈락 이유 |
| --- | --- | --- |
| **setuptools** | 가장 범용적 | `find_packages()` 보일러플레이트, `src/` layout 비직관 |
| **poetry** | 의존성 관리 통합 | uv와 중복, lockfile 충돌 위험 |
| **flit** | 경량 | `src/` layout 미지원, 유연성 부족 |
| **hatchling** (채택) | declarative, uv first-class | — |

Pi는 npm이므로 build backend 비교 대상이 없습니다. Python 모던 idiom을
자유롭게 선택할 수 있는 영역입니다.

## Decision

모든 per-package `pyproject.toml`은 hatchling build backend를 사용합니다:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aelix_agent_core"]  # 단일 선언
```

적용 대상:
- `packages/aelix-ai/pyproject.toml`
- `packages/aelix-agent-core/pyproject.toml`
- `packages/aelix-coding-agent/pyproject.toml` (Phase 3)
- 이후 추가될 모든 packages/*

## Rationale

1. **uv 공식 first-class 권장**: uv 문서가 hatchling을 workspace 환경의
   기본 backend로 예시합니다.
2. **declarative-only**: `find_packages()` 보일러플레이트 없이
   `packages = ["src/<pkg>"]` 단일 선언으로 wheel 빌드가 완성됩니다.
3. **`src/` layout 즉시 지원**: `src/` 아래에 패키지를 두는 Sprint 2 layout과
   자연스럽게 통합됩니다.
4. **향후 확장성**: Phase 5에서 TUI/web-ui assets 추가 시
   `[tool.hatch.build.targets.wheel.shared-data]`로 비코드 asset을 wheel에
   포함할 수 있습니다.

## Consequences

- 모든 4개 pyproject.toml (workspace root 제외 3 packages + 향후 추가분)에
  binding됩니다. setuptools 자동 의존이 제거됩니다.
- `uv build` 명령으로 wheel을 생성합니다. `python setup.py` 사용 불가.
- Phase 4+ wheel 빌드/배포 시 hatchling 도구 사용합니다
  (`hatch build`, `hatch publish`).
- workspace root `pyproject.toml`은 distribution target이 없으므로
  build-system 선언이 불필요합니다. `[tool.uv.workspace]`만 선언합니다.
- Pi parity와 무관합니다 (Pi는 npm, build backend 개념 없음).
  Python 모던 idiom 자유 선택 영역입니다.
