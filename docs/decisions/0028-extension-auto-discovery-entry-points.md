# 0028. Extension Auto-Discovery via importlib.metadata.entry_points

Status: Draft (Phase 3 implementation)

Supersedes (partial): ADR-0012 (Extension Discovery Model — Deferred)

## Context

ADR-0012는 extension discovery를 Phase 3에서 결정하기로 defer했습니다.
미결정 항목: `~/.aelix/extensions`, `pyproject.toml`, `entry_points` 우선순위.

Pi는 두 가지 discovery 방식을 사용합니다:

1. Directory scan: `~/.pi/extensions/` 내 파일을 `jiti` runtime loader로 동적 로드
2. (사실상) explicit import: 코드에서 직접 extension을 import하는 방식

Pi의 JavaScript 생태계에는 `entry_points`에 상응하는 first-class 표준이 없습니다.

Python 생태계는 PEP 621 `[project.entry-points]`를 제공합니다. 이는 installed
package가 그룹별 entry point를 선언하고, 소비자가 `importlib.metadata`로
discovery하는 표준 메커니즘입니다.

## Decision

Phase 3에서 `importlib.metadata.entry_points` 기반 auto-discovery를 주 방식으로
채택합니다.

### Extension 선언 (extension 패키지 측)

```toml
# my-aelix-extension/pyproject.toml
[project.entry-points."aelix.extensions"]
my-ext = "my_aelix_extension:MyExtension"
```

### Extension 로더 (Aelix runtime 측)

```python
from importlib.metadata import entry_points

def _discover_extensions() -> list[type[Extension]]:
    eps = entry_points(group="aelix.extensions")
    return [ep.load() for ep in eps]
```

### Fallback discovery paths (Pi parity 옵션)

entry_points로 등록되지 않은 extension을 위한 fallback:

1. `~/.aelix/extensions/*.py` — Pi의 `~/.pi/extensions/` 대응
2. `.aelix/extensions/*.py` — project-local extensions

Trust verdict (ADR-0010)는 distribution metadata (`importlib.metadata.packages_distributions`)에
첨부합니다. fallback path extensions는 별도 trust 평가가 필요합니다.

## Rationale

- `pip install aelix-ext-foo` 한 번으로 등록이 완료됩니다. directory placement
  불필요합니다.
- 편집 가능 install (`pip install -e ./my-ext`)이 자동으로 작동합니다.
  개발 중인 extension의 즉시 반영이 가능합니다.
- `importlib.metadata`는 표준 라이브러리입니다. 추가 의존성이 없습니다.
- Pi parity는 fallback paths (`~/.aelix/extensions/`)로 행동 수준에서 유지됩니다.

## Consequences

- ADR-0012의 "미결정" 상태를 해소합니다. entry_points가 primary, directory
  scan이 fallback입니다.
- Extension 패키지는 표준 Python packaging 도구로 배포 및 설치됩니다.
- Aelix-only convenience입니다 (additive divergence). Pi에는 상응하는
  first-class 메커니즘이 없습니다.
- Phase 3에서 ADR-0012를 이 ADR로 Supersede 처리합니다.
- Marketplace (ADR-0005) integration: marketplace를 통해 설치된 extension은
  entry_points 방식으로 자동 등록됩니다.
