# Sprint 2 / Phase 1.3 — Monorepo Migration + Pi Parity Hygiene Spec

**Status:** Draft (mechanically executable)
**Owner:** executor
**Inputs:** ADR-0015 Draft; Sprint 1 re-eval §4/§6; Sprint 1 commit (143 tests, 24 ADRs)
**Outcome:** uv workspaces (`packages/aelix-ai`, `packages/aelix-agent-core`, `packages/aelix-coding-agent`), F-9/F-10/F-11 fixed, F-12 resolved structurally, umbrella `aelix` retained for ergonomic re-export, demo + tests + lint + pyright spike all green at the end.

---

## A. Final repo tree (post-migration)

```text
aelix-ai/                                           # workspace root (same git repo)
├── pyproject.toml                                  # [tool.uv.workspace] + dev tools + aelix script
├── uv.lock
├── README.md
├── LICENSE
├── .python-version
├── .gitignore
├── .env / .env.example
├── docs/
│   └── decisions/                                  # ADRs unchanged
├── scripts/
│   └── pyright_spike.py                            # workspace-root (unchanged location)
├── tests/                                          # workspace-root shared tests (see decision below)
│   ├── conftest.py                                 # NEW — pytest plugin shim if needed
│   ├── test_agent.py
│   ├── test_agent_harness.py
│   ├── test_agent_loop.py
│   ├── test_agent_regression.py
│   ├── test_builtin_guardrail.py
│   ├── test_builtin_policy.py
│   ├── test_extension_api.py
│   ├── test_extension_loader.py
│   ├── test_hooks.py
│   ├── test_loop_with_hooks.py
│   ├── test_set_active_tools.py                    # NEW (F-9)
│   └── test_turn_state_rebuild.py                  # NEW (F-10)
├── src/
│   └── aelix/                                      # umbrella thin re-export package (F-12 resolution)
│       ├── __init__.py                             # static re-exports (no __getattr__)
│       └── __main__.py                             # demo entry — imports from sibling packages
└── packages/
    ├── aelix-ai/                                   # ↔ pi packages/ai
    │   ├── pyproject.toml
    │   └── src/
    │       └── aelix_ai/
    │           ├── __init__.py
    │           ├── messages.py
    │           ├── streaming.py
    │           └── tools.py
    ├── aelix-agent-core/                           # ↔ pi packages/agent
    │   ├── pyproject.toml
    │   └── src/
    │       └── aelix_agent_core/
    │           ├── __init__.py
    │           ├── types.py                        # ex agent/types.py + ConvertToLlmFn alias (F-11)
    │           ├── loop.py                         # ex agent/loop.py
    │           ├── agent.py                        # ex agent/agent.py
    │           ├── default_convert.py              # ex agent/default_convert.py
    │           └── harness/
    │               ├── __init__.py
    │               ├── core.py                     # ex harness/core.py (F-9 + F-10 fixes)
    │               └── hooks.py                    # ex harness/hooks.py
    └── aelix-coding-agent/                         # ↔ pi packages/coding-agent
        ├── pyproject.toml
        ├── src/
        │   └── aelix_coding_agent/
        │       ├── __init__.py
        │       ├── extensions/
        │       │   ├── __init__.py
        │       │   ├── api.py                      # ex extensions/api.py
        │       │   └── loader.py                   # ex extensions/loader.py
        │       └── builtin/
        │           ├── __init__.py
        │           ├── policy.py                   # ex builtin/policy.py
        │           └── guardrail.py                # ex builtin/guardrail.py
        └── examples/
            └── echo/
                ├── __init__.py
                └── echo.py                         # ex examples/echo.py
```

### Decision rules applied

**Boundary (matches Pi):**
- `aelix-ai/`: pure data + streaming + tool *definition* types. No agent loop, no hook bus, no extensions. (Pi `packages/ai`.)
- `aelix-agent-core/`: agent loop + `Agent` class + `default_convert` + AgentHarness + HookBus + hook event/result dataclasses. (Pi `packages/agent`.)
- `aelix-coding-agent/`: ExtensionAPI surface + extension loader + built-in `PolicyExtension`/`GuardrailExtension` + examples. (Pi `packages/coding-agent`.)

**Rationale:** Pi `packages/coding-agent` contains both extension runtime types *and* the built-in policy/guardrail tools. Aelix's `extensions/api.py` is the analogue of Pi's `ExtensionAPI`/`ExtensionContext`, which Pi ships under `coding-agent`. Phase 1.2 ADR-0007 already cited this. The `built-in` extensions are coding-agent-scoped per ADR-0004.

**Tests placement — workspace-root shared `tests/`:**
- Picked over per-package because Aelix tests already cross multiple modules (`test_loop_with_hooks.py` imports from harness + extensions + builtin + ai). Splitting requires deduping fixtures. Workspace-root keeps Sprint 1 test files as-is with only import rewrites.
- Pi reference: Pi keeps tests per-package because each TS package owns its own vitest config. Python pytest aggregates fine across a single `testpaths`.
- Trade-off: when Phase 3 adds coding-agent-only tests, they live alongside agent-core tests instead of next to their target. Acceptable for Phase 1.3; revisit at Phase 3 if test count exceeds ~400.

**`scripts/pyright_spike.py`** stays workspace-root (build-tool concern, not a package member). `pyproject.toml` pyright config (if added) references `src/**` from workspace-root.

**`examples/echo/`** lives under `packages/aelix-coding-agent/examples/echo/`. Mirrors Pi (`packages/coding-agent/examples/extensions/*`). The umbrella demo (`src/aelix/__main__.py`) imports from `aelix_coding_agent.examples.echo`.

**Demo entry (`uv run aelix`):**
- Workspace-root `pyproject.toml` declares `[project.scripts] aelix = "aelix.__main__:main"`.
- Umbrella `src/aelix/` is a real package owned by the workspace root and depends on the three workspace siblings via `[tool.uv.sources]`.
- `aelix/__main__.py` imports `from aelix_agent_core import Agent, ...`, `from aelix_ai import ...`, `from aelix_coding_agent.examples.echo import echo_tool`.

---

## B. File-by-file move map

| Current path | New path | Reason |
|---|---|---|
| `src/aelix/__init__.py` | `src/aelix/__init__.py` (rewritten) | Umbrella thin re-export (F-12). Static imports only — drop `__getattr__`. |
| `src/aelix/__main__.py` | `src/aelix/__main__.py` (rewritten) | Demo entry. Update imports to new package names; keep behavior. |
| `src/aelix/ai/__init__.py` | `packages/aelix-ai/src/aelix_ai/__init__.py` | Same re-export shape, internal imports become relative or absolute `aelix_ai.*`. |
| `src/aelix/ai/messages.py` | `packages/aelix-ai/src/aelix_ai/messages.py` | Pure types — no internal imports to rewrite. |
| `src/aelix/ai/streaming.py` | `packages/aelix-ai/src/aelix_ai/streaming.py` | `from aelix.ai.messages` → `from aelix_ai.messages`. |
| `src/aelix/ai/tools.py` | `packages/aelix-ai/src/aelix_ai/tools.py` | `from aelix.ai.messages` → `from aelix_ai.messages`. |
| `src/aelix/agent/__init__.py` | `packages/aelix-agent-core/src/aelix_agent_core/__init__.py` | Re-export surface; internal imports rewritten. |
| `src/aelix/agent/types.py` | `packages/aelix-agent-core/src/aelix_agent_core/types.py` | Add `ConvertToLlmFn` alias (F-11). Cross-package imports → `aelix_ai.*`. |
| `src/aelix/agent/loop.py` | `packages/aelix-agent-core/src/aelix_agent_core/loop.py` | Same-package imports → `aelix_agent_core.types`; cross-package → `aelix_ai.*`. |
| `src/aelix/agent/agent.py` | `packages/aelix-agent-core/src/aelix_agent_core/agent.py` | Same-package; `from aelix.ai.streaming` → `from aelix_ai.streaming`. |
| `src/aelix/agent/default_convert.py` | `packages/aelix-agent-core/src/aelix_agent_core/default_convert.py` | Trivial import rewrite. |
| `src/aelix/harness/__init__.py` | `packages/aelix-agent-core/src/aelix_agent_core/harness/__init__.py` | Re-export shape kept; subpackage of `aelix_agent_core`. |
| `src/aelix/harness/core.py` | `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` | F-9 + F-10 fixes here. Imports of `aelix.extensions.api` → `aelix_coding_agent.extensions.api`. **WARNING: creates upward dep from agent-core to coding-agent** — see §H risk 1 for resolution. |
| `src/aelix/harness/hooks.py` | `packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py` | `TYPE_CHECKING` import of `aelix.extensions.api.ExtensionContext` → forward-ref string `"aelix_coding_agent.extensions.api.ExtensionContext"` (still TYPE_CHECKING). |
| `src/aelix/extensions/__init__.py` | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/__init__.py` | Re-export shape; imports → relative or `aelix_coding_agent.extensions.*`. |
| `src/aelix/extensions/api.py` | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` | `from aelix.agent.types` → `from aelix_agent_core.types`; `from aelix.ai.streaming` → `from aelix_ai.streaming`; `from aelix.harness.hooks` → `from aelix_agent_core.harness.hooks`. |
| `src/aelix/extensions/loader.py` | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` | Same-package import rewrites only. |
| `src/aelix/builtin/__init__.py` | `packages/aelix-coding-agent/src/aelix_coding_agent/builtin/__init__.py` | Re-export shape preserved. |
| `src/aelix/builtin/policy.py` | `packages/aelix-coding-agent/src/aelix_coding_agent/builtin/policy.py` | `from aelix.extensions.api` → `from aelix_coding_agent.extensions.api`; `from aelix.harness.hooks` → `from aelix_agent_core.harness.hooks`. |
| `src/aelix/builtin/guardrail.py` | `packages/aelix-coding-agent/src/aelix_coding_agent/builtin/guardrail.py` | Same import rewrites as policy.py. |
| `src/aelix/examples/__init__.py` | `packages/aelix-coding-agent/examples/echo/__init__.py` | Demo location lives under coding-agent (per ADR mapping). |
| `src/aelix/examples/echo.py` | `packages/aelix-coding-agent/examples/echo/echo.py` | `from aelix.agent.types` → `from aelix_agent_core.types`; `from aelix.ai.*` → `from aelix_ai.*`. |
| `src/aelix.egg-info/**` | (deleted) | Setuptools metadata; regenerated by build backend. |
| `src/aelix/**/__pycache__/**` | (deleted) | Stale bytecode. |
| `tests/test_agent.py` | `tests/test_agent.py` | Import rewrites only. |
| `tests/test_agent_harness.py` | `tests/test_agent_harness.py` | Import rewrites only. |
| `tests/test_agent_loop.py` | `tests/test_agent_loop.py` | Import rewrites only. |
| `tests/test_agent_regression.py` | `tests/test_agent_regression.py` | Import rewrites only — drop `__getattr__` regression check (F-12 makes it obsolete) OR rewrite to assert static umbrella surface. **Pick: rewrite** — see §E F-12. |
| `tests/test_builtin_guardrail.py` | `tests/test_builtin_guardrail.py` | Import rewrites only. |
| `tests/test_builtin_policy.py` | `tests/test_builtin_policy.py` | Import rewrites only. |
| `tests/test_extension_api.py` | `tests/test_extension_api.py` | Import rewrites only. |
| `tests/test_extension_loader.py` | `tests/test_extension_loader.py` | Import rewrites only. |
| `tests/test_hooks.py` | `tests/test_hooks.py` | Import rewrites only. |
| `tests/test_loop_with_hooks.py` | `tests/test_loop_with_hooks.py` | Import rewrites only. |
| (none) | `tests/test_set_active_tools.py` | NEW — F-9 acceptance tests. |
| (none) | `tests/test_turn_state_rebuild.py` | NEW — F-10 acceptance tests. |

Total files moved: 18 source + 10 tests + 1 demo. Total NEW: 4 pyproject files + 2 test files + 4 package `__init__.py` placeholders.

---

## C. Per-package `pyproject.toml` content (exact text)

**Build backend choice: hatchling.** Justification: (a) uv's first-class recommendation for workspace members, (b) declarative-only — no `[tool.setuptools.packages.find]` boilerplate, (c) `src/` layout supported out of the box, (d) Pi uses no build backend (npm), so we are free to pick the modern Python idiom. Workspace root keeps no build backend dependency itself (it is a workspace anchor + script provider; it never publishes).

### C.1 Workspace root: `/workspaces/aelix-ai/pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "aelix"
version = "0.1.0"
description = "Aelix umbrella — convenience re-exports + demo entry."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "Aelix Contributors" }]
dependencies = [
  "aelix-ai",
  "aelix-agent-core",
  "aelix-coding-agent",
]

[project.scripts]
aelix = "aelix.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/aelix"]

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
aelix-ai = { workspace = true }
aelix-agent-core = { workspace = true }
aelix-coding-agent = { workspace = true }

[dependency-groups]
dev = [
  "pyright>=1.1.409",
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "ruff>=0.6",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "SIM"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["B011"]

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.pyright]
include = ["src", "packages/*/src", "scripts"]
pythonVersion = "3.11"
```

### C.2 `/workspaces/aelix-ai/packages/aelix-ai/pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "aelix-ai"
version = "0.1.0"
description = "Aelix AI primitives — messages, tools, streaming types. Pi-ai parity."
readme = "../../README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "Aelix Contributors" }]
dependencies = []

[tool.hatch.build.targets.wheel]
packages = ["src/aelix_ai"]
```

### C.3 `/workspaces/aelix-ai/packages/aelix-agent-core/pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "aelix-agent-core"
version = "0.1.0"
description = "Aelix agent loop + harness + hook bus. Pi-agent-core parity."
readme = "../../README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "Aelix Contributors" }]
dependencies = ["aelix-ai"]

[tool.uv.sources]
aelix-ai = { workspace = true }

[tool.hatch.build.targets.wheel]
packages = ["src/aelix_agent_core"]
```

### C.4 `/workspaces/aelix-ai/packages/aelix-coding-agent/pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "aelix-coding-agent"
version = "0.1.0"
description = "Aelix coding agent — ExtensionAPI + built-ins + example tools. Pi-coding-agent parity (non-UI)."
readme = "../../README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "Aelix Contributors" }]
dependencies = [
  "aelix-ai",
  "aelix-agent-core",
]

[tool.uv.sources]
aelix-ai = { workspace = true }
aelix-agent-core = { workspace = true }

[tool.hatch.build.targets.wheel]
packages = ["src/aelix_coding_agent"]
```

---

## D. Import path rewrite map (exhaustive, sed-friendly)

Apply with `find packages/ src/ tests/ -name '*.py' -exec sed -i -f rewrites.sed {} +`. Order matters: more-specific patterns first.

| Old import | New import |
|---|---|
| `from aelix.ai.messages import` | `from aelix_ai.messages import` |
| `from aelix.ai.streaming import` | `from aelix_ai.streaming import` |
| `from aelix.ai.tools import` | `from aelix_ai.tools import` |
| `from aelix.ai import` | `from aelix_ai import` |
| `from aelix.agent.types import` | `from aelix_agent_core.types import` |
| `from aelix.agent.loop import` | `from aelix_agent_core.loop import` |
| `from aelix.agent.agent import` | `from aelix_agent_core.agent import` |
| `from aelix.agent.default_convert import` | `from aelix_agent_core.default_convert import` |
| `from aelix.agent import` | `from aelix_agent_core import` |
| `import aelix.agent as` | `import aelix_agent_core as` |
| `from aelix.harness.core import` | `from aelix_agent_core.harness.core import` |
| `from aelix.harness.hooks import` | `from aelix_agent_core.harness.hooks import` |
| `from aelix.harness import` | `from aelix_agent_core.harness import` |
| `import aelix.harness` | `import aelix_agent_core.harness` |
| `from aelix.extensions.api import` | `from aelix_coding_agent.extensions.api import` |
| `from aelix.extensions.loader import` | `from aelix_coding_agent.extensions.loader import` |
| `from aelix.extensions import` | `from aelix_coding_agent.extensions import` |
| `from aelix.builtin.policy import` | `from aelix_coding_agent.builtin.policy import` |
| `from aelix.builtin.guardrail import` | `from aelix_coding_agent.builtin.guardrail import` |
| `from aelix.builtin import` | `from aelix_coding_agent.builtin import` |
| `from aelix.examples.echo import` | `from aelix_coding_agent.examples.echo.echo import` |

---

## E. F-9 / F-10 / F-11 / F-12 fix specs

### F-9. `_action_set_active_tools` non-destructive

**Bug:** `src/aelix/harness/core.py:346-348` permanently drops tools from `self._state.tools`. After `set_active_tools(["a"])`, calling `set_active_tools(["a","b"])` cannot revive `b`.

**Fix (in `aelix_agent_core/types.py`, `AgentState`):**
```python
@dataclass
class AgentState:
    system_prompt: str = ""
    model: Model = field(default_factory=Model)
    tools: list[AgentTool] = field(default_factory=list)
    active_tool_names: list[str] | None = None   # NEW. None = all tools active.
    messages: list[AgentMessage] = field(default_factory=list)
    thinking_level: str = "off"
```

**Fix (in `aelix_agent_core/harness/core.py`):**

```python
def _action_get_active_tools(self) -> list[str]:
    if self._state.active_tool_names is None:
        return [tool.name for tool in self._state.tools]
    active = set(self._state.active_tool_names)
    return [tool.name for tool in self._state.tools if tool.name in active]

def _action_set_active_tools(self, names: list[str]) -> None:
    known = {t.name for t in self._state.tools}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise AgentHarnessError(
            "invalid_argument",
            f"set_active_tools: unknown tool name(s): {unknown!r}",
        )
    self._state.active_tool_names = list(names)
```

**Fix (in `AgentHarness._run`, the `AgentContext` construction):**
```python
active = self._state.active_tool_names
if active is None:
    active_tools = list(self._state.tools)
else:
    active_set = set(active)
    active_tools = [t for t in self._state.tools if t.name in active_set]

context = AgentContext(
    system_prompt=system_prompt,
    messages=list(self._state.messages),
    tools=active_tools,
)
```

**New tests in `tests/test_set_active_tools.py`:**
1. `test_set_active_tools_filters_without_dropping_tools`
2. `test_set_active_tools_none_means_all_active`
3. `test_set_active_tools_unknown_name_raises`

### F-10. `createTurnState` rebuild per turn (partial — Phase 1.3 scope)

**Fix:** add per-turn snapshot for `system_prompt` so `before_agent_start` chain does not leak across `prompt()` calls.

```python
@dataclass
class _TurnState:
    system_prompt: str
    model: Model

class AgentHarness:
    def __init__(self, options): ...
        self._turn_state: _TurnState | None = None

    def _current_system_prompt(self) -> str:
        return (
            self._turn_state.system_prompt
            if self._turn_state is not None
            else self._state.system_prompt
        )
```

In `prompt()`, set `_turn_state` after `before_agent_start` chain resolves, before `_run`:
```python
self._turn_state = _TurnState(
    system_prompt=system_prompt,
    model=self._state.model,
)
try:
    return await self._run(prompts, system_prompt=system_prompt)
finally:
    self._turn_state = None
```

Modify `_make_context` and `_action_get_system_prompt` to use `_current_system_prompt`.

**New tests in `tests/test_turn_state_rebuild.py`:**
1. `test_second_prompt_sees_original_system_prompt`
2. `test_get_system_prompt_during_turn_reflects_chained_prompt`

### F-11. `convert_to_llm` type alias dedup

In `aelix_agent_core/types.py`:
```python
ConvertToLlmFn = Callable[
    [list[AgentMessage]],
    Awaitable[list[Message]] | list[Message],
]
```

Replace duplicated signatures in `AgentLoopConfig`, `AgentHarnessOptions`, `AgentOptions` with `ConvertToLlmFn`.

### F-12. Static umbrella re-exports (resolves lazy `__getattr__`)

Rewrite `src/aelix/__init__.py`:
```python
"""Aelix umbrella — convenience re-exports across workspace packages."""

from aelix_ai import (
    AssistantMessage, Message, Model, TextContent, Tool,
    ToolCallContent, ToolResult, ToolResultMessage, UserMessage,
)
from aelix_agent_core import (
    Agent, AgentOptions, AgentState, AgentTool,
)
from aelix_agent_core.harness import (
    AgentHarness, AgentHarnessOptions,
)
from aelix_coding_agent.builtin import (
    GuardrailExtension, PolicyExtension,
)

__all__ = [
    "Agent", "AgentHarness", "AgentHarnessOptions", "AgentOptions",
    "AgentState", "AgentTool", "AssistantMessage", "GuardrailExtension",
    "Message", "Model", "PolicyExtension", "TextContent", "Tool",
    "ToolCallContent", "ToolResult", "ToolResultMessage", "UserMessage",
]
```

Rewrite `tests/test_agent_regression.py` to assert static surface (no `__getattr__` proxy).

---

## F. Migration step order

1. **Create package skeletons.** mkdir packages + placeholder `__init__.py` + 4 pyproject.toml files.
2. **Convert workspace root pyproject.toml** to workspace mode. Delete `src/aelix.egg-info/`.
3. **`git mv` per package** (one package at a time keeps blame intact). DO NOT move `src/aelix/__init__.py` or `__main__.py`.
4. **Delete stale dirs** (`src/aelix/ai`, `agent`, `harness`, `extensions`, `builtin`, `examples`, `__pycache__`, `egg-info`).
5. **Apply import rewrites via sed** (§D order most-specific-first). DO NOT sed-rewrite umbrella `__init__.py` or `__main__.py`.
6. **Hand-rewrite** `src/aelix/__init__.py` (per §E F-12) and `src/aelix/__main__.py` (new imports).
7. **Resolve harness → coding-agent forward reference** via TYPE_CHECKING + lazy local imports.
8. **Apply F-9, F-10, F-11 fixes** + new test files.
9. **`rm -rf .venv uv.lock` then `uv sync`**.
10. **`uv run pytest -v`** — expect `~148 passed`.
11. **`uv run aelix`** — identical 3-line demo.
12. **`uv run ruff check`** — clean.
13. **`uv run pyright scripts/pyright_spike.py`** — 8 errors (narrowing alive).
14. **`uv run pyright packages/*/src src/aelix`** — 0 errors.

---

## G. Verification commands and expected outputs

| Command | Expected output |
|---|---|
| `uv sync` | `Resolved 4 packages in ...` listing aelix/aelix-ai/aelix-agent-core/aelix-coding-agent. |
| `uv pip list \| grep aelix` | Four entries, each `0.1.0`. |
| `uv run pytest -v` | `============ ~148 passed in ~1.5s ============` (143 + 5 new). |
| `uv run aelix` | Three lines: tool call, tool ret, assistant. |
| `uv run ruff check` | `All checks passed!` |
| `uv run pyright scripts/pyright_spike.py` | `8 errors, 0 warnings, 0 informations`. |
| `uv run pyright packages/*/src src/aelix` | `0 errors, 0 warnings`. |
| `uv run python -c "import aelix_ai; import aelix_agent_core; import aelix_coding_agent; print('OK')"` | `OK` |
| `uv run python -c "import aelix; print(aelix.AgentHarness.__module__, aelix.PolicyExtension.__module__)"` | `aelix_agent_core.harness.core aelix_coding_agent.builtin.policy` |

---

## H. Risks and mitigations

### Risk 1 (HIGH): cycle — `aelix_agent_core.harness` imports `aelix_coding_agent.extensions.api`

**Mitigation:** keep `if TYPE_CHECKING:` blocks for type imports + lazy function-body imports for runtime. `aelix-agent-core/pyproject.toml` MUST NOT declare `aelix-coding-agent` as dependency.

Add regression test:
```python
def test_agent_core_does_not_require_coding_agent():
    import sys
    sys.modules.pop("aelix_coding_agent", None)
    from aelix_agent_core.harness import HookBus, HookEvent
    assert "aelix_coding_agent" not in sys.modules
```

### Risk 2 (MEDIUM): hatchling + workspace + relative `readme = "../../README.md"`
Acceptable for Phase 1.3 since not publishing. Track as deferred work.

### Risk 3 (MEDIUM): pytest discovery across workspace
Editable installs via hatchling should handle this. Fallback: add `pythonpath = ["packages/*/src", "src"]` to `[tool.pytest.ini_options]`.

### Risk 4 (LOW): TYPE_CHECKING forward-ref strings break with package rename
`from __future__ import annotations` already present everywhere. Pyright `[tool.pyright] include` covers new paths.

### Risk 5-8 (LOW): test_agent_regression cleanup, pyright `.venv` exclusion, `__pycache__` cleanup, `_resolve_stream_simple` inner import — all handled per spec sections.

### Unknown 1: uv version
Spec assumes `uv >= 0.5`. Check with `uv --version`.

### Unknown 2: `.venv` survival across workspace conversion
Safest: `rm -rf .venv uv.lock` before `uv sync` in step 9.

---

## I. Acceptance checklist

- [ ] Tree matches §A.
- [ ] All four `pyproject.toml` files exist with §C content.
- [ ] `grep -rn "from aelix\." packages/ src/ tests/` returns ZERO matches.
- [ ] `grep -rn "import aelix\." packages/ src/ tests/ | grep -v "import aelix_"` returns ZERO matches.
- [ ] `src/aelix/__init__.py` has no `__getattr__` definition.
- [ ] `aelix_agent_core/types.py` exports `ConvertToLlmFn`.
- [ ] `aelix_agent_core/types.py` `AgentState` has `active_tool_names: list[str] | None = None`.
- [ ] `tests/test_set_active_tools.py` has 3 tests, all pass.
- [ ] `tests/test_turn_state_rebuild.py` has 2 tests, all pass.
- [ ] `uv run pytest -v` reports 148 passed.
- [ ] `uv run aelix` produces 3-line demo.
- [ ] `uv run ruff check` clean.
- [ ] `uv run pyright scripts/pyright_spike.py` reports 8 errors.
- [ ] `uv run pyright packages/*/src src/aelix` reports 0 errors.
- [ ] No agent-core file references `aelix_coding_agent` outside TYPE_CHECKING or function-body lazy import.

End of spec.
