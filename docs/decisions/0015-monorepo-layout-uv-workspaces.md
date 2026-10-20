# 0015. Monorepo Layout — uv Workspaces

Status: Accepted (Phase 1.3 shipped); mapping rows partially Superseded by: ADR-0133
**Amended 2026-10-20 (#292): "역방향 의존 금지" is now a CI gate. No package
imports a workspace package it does not declare, `TYPE_CHECKING` imports
included; the 13 kernel imports of `aelix_coding_agent` that predate the gate
sit on an exact list - a new one fails until it is listed, a removed one until
its entry is removed - and this ADR forbids adding an entry except to follow a
listed import that moved or was demoted. See `## Amendment (2026-10-20, #292)`.**

> **Amendment (2026-06-16, ADR-0133).** The uv-workspaces decision stands,
> but three forward-looking rows of the "Pi 패키지 → Aelix 패키지" mapping
> below were never realised and are **superseded by ADR-0133**:
> - `packages/tui → aelix-tui`: no `aelix-tui` package. pi's `packages/tui`
>   is a generic terminal-UI **library** (no `bin`, no app deps); ADR-0088
>   filled that role with **prompt-toolkit + Rich**, and the chat UI lives
>   inside `aelix-coding-agent` (`aelix_coding_agent/tui/`) — exactly as pi
>   keeps its chat UI inside `packages/coding-agent`. The split was
>   evaluated and rejected (ADR-0133).
> - `Pi --mode rpc → aelix-rpc`: the RPC surface ships inside
>   `aelix_coding_agent/rpc/` (ADR-0056); no standalone `aelix-rpc`.
> - `packages/web-ui → aelix-web-ui`: the Web frontend lives in a separate
>   repo `aelix-web` (ADR-0097 D3), not an in-repo package.
>
> The actually-shipped workspace is: `aelix-ai`, `aelix-agent-core`,
> `aelix-coding-agent` (TUI inside + `[tui]`/`[images]` extras),
> `aelix-server` (Aelix-additive, ADR-0097 / ADR-0103). The package-dependency
> rule below should read `aelix-coding-agent → aelix-agent-core → aelix-ai`
> (and `aelix-server → aelix-coding-agent`); the `aelix-tui → aelix-agent-core`
> rule is moot.

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

**Examples placement (Sprint 2 implementation note):** `examples/echo/` lives at
`packages/aelix-coding-agent/src/aelix_coding_agent/examples/echo/` (inside the
importable package tree) so the umbrella demo's
`from aelix_coding_agent.examples.echo.echo import echo_tool` resolves through the
wheel install. The spec's original §A tree placed examples at
`packages/aelix-coding-agent/examples/echo/` (outside `src/`), which would require
packaging examples as data files rather than modules. The implemented location is the
better engineering choice and is now binding.

---

ADR-0025 (ExtensionContext UI surface, Phase 5)는 이전 스펙에서 "ADR-0015"로
forward되었던 항목입니다. 번호 충돌로 인해 ADR-0025로 재배정합니다.

---

*Provenance: Sprint 2 Phase 1.3 spec (`.omc/specs/sprint-2-phase-1-3-spec.md`).
Commit reference to be added by git-master on merge.*

---

## Amendment (2026-10-20, #292): the dependency rule is a gate, and the kernel's existing breaches sit on an exact list

The rule above ("패키지 간 허용 의존성 ... 역방향 의존 금지") was never checked,
and it did not hold. Measured on `7faf4b9a` with an AST walk of every
distribution's source tree, `aelix-agent-core` imports `aelix_coding_agent` in
13 statements (18 imported names), all in `harness/`: 10 inside functions of
`AgentHarness` (the default `_ExtensionRuntime` and the `bind_core` action
table in `__init__`, `export_to_html`, the context-usage readers, the context and
command-context factories, the `get_commands` and `get_all_tools` views) and 3
under `TYPE_CHECKING` (`core.py`, `_extension_runner.py`, `hooks.py`). None is
at module level, so `import aelix_agent_core` works without the higher package,
but with `aelix_coding_agent` made unimportable `AgentHarness(AgentHarnessOptions())`
raises `ModuleNotFoundError`. The other four distributions break the rule
nowhere. On that commit a new reverse import in the kernel, or an `aelix_ai`
module importing `aelix_agent_core`, passed ruff, the type gate and the
structural tests that read the kernel's source.

1. **The rule, stated precisely.** A distribution's source may import its own
   top-level packages and those of the workspace distributions it declares in
   `[project]` `dependencies` or `optional-dependencies`, directly or through
   one of them (dependency groups are development-only). That is
   `aelix-ai` <- `aelix-agent-core` <- `aelix-coding-agent` <- `aelix-server`
   and `aelix`. The last two both sit on `aelix-coding-agent` and neither
   imports the other. `aelix-coding-agent` ships three top-level packages
   (`aelix_coding_agent`, `aelix_agents`, `aelix_status`), and all three are
   "above" the kernel.
2. **The gate.** `scripts/check_import_direction.py` holds the graph
   (`WORKSPACE`) and checks the tree against it; `tests/test_import_direction_292.py`
   runs it on every CI leg, and also holds `WORKSPACE` equal to what the
   `pyproject.toml` files declare. Declaring an upward dependency to quiet the
   gate is the cycle this ADR forbids, and it fails as one.
3. **What counts.** Every `import` and `from ... import`, of three kinds:
   `module` (runs on import), `function` (inside a `def` or `lambda`; runs when
   called) and `type_checking` (under typing's own flag: a `TYPE_CHECKING`
   imported from `typing` or `typing_extensions`, or `typing.TYPE_CHECKING`,
   that nothing else in the file binds; never runs). A `TYPE_CHECKING` the file
   defines or rebinds, or any other flag, guards code that runs. Also counted:
   a call to `import_module`, `find_spec`, `resolve_name` or `__import__`, by
   that name or through a name the file binds to one by a `from ... import ...
   as` alias or by assigning it to a bare name (plain, annotated or `:=`, and
   chains of those), whose module is a string literal - a relative name
   resolved against a literal package, positional or `package=` (`__import__`'s
   `level` is not read). `TYPE_CHECKING` imports count, because the kernel's own
   signatures then name the higher package and #292 pinned them with the rest.
   pi's `check-runtime-deps.mjs`, which checks every public package's imports
   against its declared dependencies, exempts `import type`; aelix diverges
   there, and checks only edges between workspace packages, not third-party
   declarations. Among the forms the gate does not see: a module name built at
   run time; an importer reached through `getattr` or another file, or bound
   any other way (tuple unpacking, a `for` target, a parameter default, a `:=`
   called in place); `__import__` with `level` >= 1, whose relative name it
   reads as absolute; a call such as `importlib.resources.files`,
   `pkgutil.get_data` or `pydoc.locate` that imports a package it is given by
   name; `runpy`; a `sys.modules` lookup; an entry-point string; `exec`. The
   script's docstring lists the forms known not to be seen.
4. **The list is exact, and only this ADR's policy keeps it shrinking.** The
   18 names are pinned in `ALLOWLIST` by file, kind, module and imported name,
   with a count, never by line, so lines added or removed around a site do not
   matter. The tree must match the list exactly, and nothing else is compared:
   a new site fails until it is listed, and a listed site that is gone fails
   until its entry is removed or its count lowered. The gate cannot stop a
   change that edits `ALLOWLIST` itself; it makes every such change a diff a
   reviewer sees. The policy is that the list only shrinks. Adding an entry to
   let a new import pass is forbidden. An entry may change its file or kind
   only to follow a listed import that moved to another file of the same
   distribution or under `TYPE_CHECKING`.
5. **Inverting the 10 runtime imports is a separate issue.** The kernel
   receives what it needs from `aelix-coding-agent` (a Protocol or callback
   passed in, as `bind_core` already does for actions) or the shared types move
   down. It is done when a clean environment with only the `aelix-agent-core`
   wheel and its declared dependencies can construct and use `AgentHarness`.
   This amendment makes every new reverse import fail until it is listed, and
   forbids listing one.
