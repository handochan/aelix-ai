"""#304 audit — every ``_ctx``-derived value on ``SubagentHost``, in three states.

For each ``AgentsExtension._host_*`` getter: what it answers when the hook context
is None / STALE / LIVE, wired the way ``cli/entry.py`` wires it (a bound runtime,
the late-binding ``model`` and ``session`` getters, the pre-hook fallbacks). A row
that differs from the LIVE column in either of the other two is the #304 defect
wearing a different field name.

Run:  uv run --no-sync python .omc/specs/304-probe-ctx-audit.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from aelix_agents.extension import AgentsExtension
from aelix_ai.streaming import Model
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    _default_actions,
    _ExtensionRuntime,
)

LIVE_MODEL = Model(id="anthropic/claude-haiku-4.5", provider="openrouter", api="openai-completions")
DEAD_MODEL = Model(id="dead/previous-session", provider="openrouter", api="openai-completions")
TOOLS = ["read", "bash"]


def _ext(tmp: Path, state: str) -> AgentsExtension:
    agent_dir, cwd = tmp / "agent", tmp / "project"
    cwd.mkdir(parents=True, exist_ok=True)

    runtime = _ExtensionRuntime()
    # What ``AgentHarness`` does on every build: install the real action table.
    # ``ExtensionAPI.get_active_tools`` goes through it.
    actions = _default_actions()
    actions.get_active_tools = lambda: list(TOOLS)
    runtime.bind_core(actions)

    api = ExtensionAPI(Extension(name="aelix-agents"), runtime)
    wiring: dict[str, Any] = {}
    # Runs UNMODIFIED on the branch base, where the field does not exist yet —
    # so the two tables below are produced by one file, not two.
    if "model" in AgentsExtension.__dataclass_fields__:
        wiring["model"] = lambda: LIVE_MODEL  # entry.py's _live_model_of(session_host)
    ext = AgentsExtension(
        posture=PermissionPosture(mode=PermissionMode.DEFAULT),
        agent_dir=str(agent_dir), cwd=str(cwd), project_trusted=True,
        no_context_files=lambda: False, session=lambda: None, **wiring,
    )
    ext(api)

    if state == "none":
        return ext
    # The dead session's context: its own runtime, its own (now wrong) values.
    dead = _ExtensionRuntime()
    dead_actions = _default_actions()
    dead_actions.get_active_tools = lambda: ["read", "bash", "write", "edit"]
    dead.bind_core(dead_actions)
    host_runtime = runtime if state == "live" else dead
    ctx = ExtensionContext(
        host_runtime, cwd=str(cwd),
        model=LIVE_MODEL if state == "live" else DEAD_MODEL,
        is_idle=lambda: True, abort=lambda: None,
        get_active_tools=(lambda: list(TOOLS)) if state == "live" else dead_actions.get_active_tools,
        get_system_prompt=lambda: "system", is_project_trusted=lambda: True,
    )
    if state == "stale":
        host_runtime.invalidate("stale")
    ext._ctx = ctx
    return ext


def _ask(fn: Any) -> str:
    try:
        return repr(fn())
    except Exception as exc:  # noqa: BLE001
        return f"!! raises {exc.__class__.__name__}"


GETTERS = ["_host_cwd", "_host_posture", "_host_active_tools", "_host_context_files",
           "_host_consent_context", "_host_project_trusted", "_host_model_registry",
           "_host_model", "_host_has_ui", "_host_session"]

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    exts = {s: _ext(tmp / s, s) for s in ("none", "stale", "live")}
    width = max(len(g) for g in GETTERS)
    print(f"{'getter':<{width}}  {'_ctx=None':<30}  {'_ctx=STALE':<30}  _ctx=LIVE")
    print("-" * (width + 100))
    bad = 0
    for g in GETTERS:
        cells = []
        for s in ("none", "stale", "live"):
            v = _ask(getattr(exts[s], g))
            cells.append(v[:28] + ".." if len(v) > 30 else v)
        differs = cells[0] != cells[2] or cells[1] != cells[2]
        bad += 1 if differs else 0
        flag = "  <-- differs from LIVE" if differs else ""
        print(f"{g:<{width}}  {cells[0]:<30}  {cells[1]:<30}  {cells[2]}{flag}")
    print(f"\n{bad} getter(s) answer differently without a live hook context")
