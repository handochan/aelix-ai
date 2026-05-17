# 0015. Monorepo Layout — uv Workspaces

Status: Draft (Phase 1.3 finalization)

## Context

Pi는 npm workspaces로 `packages/ai`, `packages/agent`, `packages/coding-agent`,
`packages/tui`, `packages/web-ui` 5개를 별도 패키지로 출시합니다. 각 패키지는
독립 `package.json`을 보유하고 workspace root에서 `npm workspaces`로 연결됩니다.

현재 Aelix는 단일 `aelix` PyPI 패키지(`src/aelix/`)로 모든 모듈을 포함합니다.
1차 원칙(Pi parity)에 따라 패키지 boundary를 Pi와 정렬해야 합니다.

Phase 1.2 commit 이후 단일 패키지 구조를 유지하면 Phase 2+ 에서 패키지 분리
비용이 누적되고, Pi의 패키지 간 의존성 규칙(예: `aelix-tui`는 `aelix-agent-core`에
의존하되 역방향 의존 금지)을 강제할 수 없습니다.

## Decision

Phase 1.3 경계에서 uv workspaces로 마이그레이션합니다.

Pi 패키지 → Aelix 패키지 매핑:

| Pi 패키지 | Aelix 패키지 | 출시 Phase |
| --- | --- | --- |
| `packages/ai` | `packages/aelix-ai/` | Phase 1.3 |
| `packages/agent` | `packages/aelix-agent-core/` | Phase 1.3 |
| `packages/coding-agent` | `packages/aelix-coding-agent/` | Phase 3 |
| `packages/tui` | `packages/aelix-tui/` | Phase 5 |
| `packages/web-ui` | `packages/aelix-web-ui/` | Phase 6 |
| Pi `--mode rpc` | `packages/aelix-rpc/` | Phase 4 (ADR-0020) |

workspace root 구조:

```text
aelix-ai/                              # workspace root (현재 repo)
├── pyproject.toml                     # [tool.uv.workspace] members = ["packages/*"]
├── uv.lock
└── packages/
    ├── aelix-ai/                      # ↔ pi packages/ai
    │   └── pyproject.toml
    ├── aelix-agent-core/              # ↔ pi packages/agent
    │   └── pyproject.toml
    ├── aelix-coding-agent/            # ↔ pi packages/coding-agent (Phase 3)
    ├── aelix-tui/                     # ↔ pi packages/tui (Phase 5)
    ├── aelix-web-ui/                  # ↔ pi packages/web-ui (Phase 6)
    └── aelix-rpc/                     # ↔ Pi --mode rpc (Phase 4)
```

## Consequences

- 각 패키지는 독립 `pyproject.toml`을 보유합니다. workspace root
  `pyproject.toml`에 `[tool.uv.workspace] members = ["packages/*"]`를 선언합니다.
- Import path 변경: `from aelix.harness.hooks` → `from aelix_agent_core.harness.hooks`.
  `from aelix.types` → `from aelix_ai.types`. 마이그레이션은 기계적 rename입니다.
- `uv run aelix` entry point는 workspace root `pyproject.toml`에 wrapper로 유지합니다.
  실제 implementation은 `packages/aelix-agent-core/`에 위치합니다.
- Phase 1.2 commit 직후 마이그레이션 sprint를 진행합니다(1-2일 예상).
- `uv sync` 단일 명령으로 workspace 전체를 bootstrap할 수 있습니다(uv 0.5+ 기준).
- 패키지 간 허용 의존성: `aelix-agent-core` → `aelix-ai`; `aelix-coding-agent` →
  `aelix-agent-core`; 역방향 의존 금지.

---

ADR-0025 (ExtensionContext UI surface, Phase 5)는 이전 스펙에서 "ADR-0015"로
forward되었던 항목입니다. 번호 충돌로 인해 ADR-0025로 재배정합니다.
