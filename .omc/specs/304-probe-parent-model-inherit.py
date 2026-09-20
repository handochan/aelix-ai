"""#304 probe — does ``/agents run`` put the parent's --model on the child argv?

Drives the REAL ``/agents run`` door (``_SubagentRuntimeImpl.spawn``) through the
REAL ``PrintChannel``, with only the argv builder replaced by a spy that records
the command line and hands back a no-op process. Nothing about the model path is
stubbed: the host is the one ``AgentsExtension._host()`` builds, and the
``parent_model`` the channel forwards is ``host.model``, exactly as
``__post_init__`` wires it.

The ``model`` getter is the REAL ``cli/entry.py::_live_model_of``, driven by a
stand-in runtime host, so what is measured is the product wiring and not a
re-statement of it.

Four states — the three the issue names, plus the one the audit turned up:

  A. ``_ctx is None``      — ``/agents run`` is the first thing typed in a new TUI
  B. ``_ctx`` is STALE     — ``/agents run`` right after ``/new`` (the old session's
                             context; every attribute raises ExtensionError)
  C. ``_ctx`` is LIVE      — a hook has fired this session (the only working case)
  D. ``_ctx`` is LIVE but its ``model`` is the SNAPSHOT from the last hook —
                             ``/model <id>`` then ``/agents run``, with no turn in
                             between. ``ExtensionContext.model`` is
                             ``object.__getattribute__(self, "_model")``, frozen when
                             the context was built; a slash command fires no hook.

Run:  uv run --no-sync python .omc/specs/304-probe-parent-model-inherit.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import PrintChannel
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli.entry import _live_model_of
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    _ExtensionRuntime,
)
from aelix_coding_agent.subagent_contract import ResolvedProfile

PARENT_MODEL = Model(
    id="anthropic/claude-haiku-4.5", provider="openrouter", api="openai-completions"
)
STALE_MODEL = Model(id="stale/previous-session", provider="openrouter", api="openai-completions")
BOOT_MODEL = Model(id="boot/before-slash-model", provider="openrouter", api="openai-completions")


def _profile(agent_dir: Path) -> ResolvedProfile:
    path = agent_dir / "agents" / "scout.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: scout\ndescription: scout agent\n---\n\nYou are the scout.\n",
        encoding="utf-8",
    )
    p = AgentProfile(
        name="scout", description="scout agent", body="You are the scout.",
        file_path=str(path), scope="user",
    )
    return ResolvedProfile(name="scout", profile=p, source_path=str(path), scope="user")


def _context(runtime: _ExtensionRuntime, cwd: Path, model: Model | None) -> ExtensionContext:
    return ExtensionContext(
        runtime, cwd=str(cwd), model=model, is_idle=lambda: True, abort=lambda: None,
        get_active_tools=lambda: ["read"], get_system_prompt=lambda: "system",
    )


async def _probe(label: str, ctx_state: str, tmp: Path) -> list[str]:
    root = tmp / label
    agent_dir, cwd = root / "agent", root / "project"
    cwd.mkdir(parents=True, exist_ok=True)

    runtime = _ExtensionRuntime()
    api = ExtensionAPI(Extension(name="aelix-agents"), runtime)

    # ``cli/entry.py``'s ``session_host`` holder, carrying an
    # ``AgentSessionRuntime`` stand-in whose ``harness.current_model`` is what
    # ``/model`` writes. ``_live_model_of`` below is the real entry.py function.
    class _Harness:
        current_model = PARENT_MODEL

    class _Runtime:
        harness = _Harness()
        session = None

    session_host: dict[str, Any] = {"runtime": _Runtime()}

    # Wired EXACTLY as cli/entry.py wires it (the ``AgentsExtension(...)`` call
    # in ``_async_main``). ``AELIX_304_UNWIRED=1`` drops the ``model=`` line and
    # reproduces the branch-base behaviour in the same run of the same file.
    wiring: dict[str, Any] = {}
    if not os.environ.get("AELIX_304_UNWIRED"):
        wiring["model"] = lambda: _live_model_of(session_host)
    ext = AgentsExtension(
        posture=PermissionPosture(mode=PermissionMode.DEFAULT),
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        project_trusted=False,
        no_context_files=lambda: False,
        session=lambda: None,
        **wiring,
    )
    ext(api)

    if ctx_state == "stale":
        dead = _ExtensionRuntime()
        stale = _context(dead, cwd, STALE_MODEL)
        dead.invalidate("stale")
        ext._ctx = stale
    elif ctx_state == "live":
        ext._ctx = _context(runtime, cwd, PARENT_MODEL)
    elif ctx_state == "snapshot":
        # The last hook built its context while the parent was on BOOT_MODEL;
        # ``/model anthropic/claude-haiku-4.5`` then moved the harness on.
        ext._ctx = _context(runtime, cwd, BOOT_MODEL)

    seen: list[list[str]] = []

    def _spy(*_a: Any, **kw: Any) -> list[str]:
        model = kw.get("parent_model")
        seen.append(["--model", getattr(model, "id", None) or "<none>"])
        return [sys.executable, "-c", "pass"]

    impl = ext.runtime
    assert impl is not None
    impl.channel = PrintChannel(parent_model=impl.host.model, argv_builder=_spy)

    await impl.spawn(_profile(agent_dir), "run it")
    return seen[0] if seen else ["<argv never built>"]


async def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        wired = "NOT WIRED (branch base)" if os.environ.get("AELIX_304_UNWIRED") else "wired (#304)"
        print(f"parent's effective model: {PARENT_MODEL.id}    host model getter: {wired}\n")
        rows = [
            ("A  _ctx is None  (first command in a fresh TUI)", "none"),
            ("B  _ctx is STALE (right after /new, /resume, /fork)", "stale"),
            ("C  _ctx is LIVE  (a hook fired this session)", "live"),
            ("D  _ctx is LIVE, model is the pre-/model snapshot", "snapshot"),
        ]
        bad = 0
        for label, state in rows:
            got = await _probe(state, state, tmp)
            ok = got[1] == PARENT_MODEL.id
            bad += 0 if ok else 1
            print(f"  {label}\n      child argv parent_model = {got[1]}   {'OK' if ok else 'WRONG'}")
        print(f"\n{bad}/{len(rows)} states do NOT inherit the parent's model")
        return bad


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
