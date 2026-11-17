"""The delegation filter's structural gate (#795, #796, ADR-0304 D5).

Structural regression protection, NOT a proof: see ``delegation_gate.py``'s
docstring for what it matches and what it does not follow. Two halves:

* the REAL tree — every ``.py`` under ``packages/aelix-coding-agent/src/
  aelix_agents/`` and ``.../aelix_coding_agent/`` — holds every exact set and
  every dataflow rule;
* the checker's own positive controls — a clean synthetic tree that reports
  nothing, and synthetic bypasses each of which reports exactly the rule named:
  the contract's thirteen (G1-G13), and from fix round 1 one for every rule
  that had none (A1, A2, B2, B3, C2, C3, D2-D4, D7, E2, E3, F4), the
  product-core start sinks (D1-D3, D5, D7: Codex 1 r1), a re-check that can
  be skipped inside its own expression (F3: Codex 2 r1), the copy forms under
  an import alias (B1: Codex 5 r1) and M2's ``attrgetter`` and dynamic-import
  forms; from fix round 2, the round-2 verifier's X5 and X6 doors and their
  product-core variants (B2-B4, D8) and a control for each checker clause no
  self-test had reached (A3's ``complete_profile``, M1 aliases, F2's rebinding
  and uncalled sink, F3's count, order and refusal, the last two told apart by
  their words). They are strings in this file, so they run on every push.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import delegation_gate as gate  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2] / "packages" / "aelix-coding-agent" / "src"


def _real_tree() -> dict[str, str]:
    sources: dict[str, str] = {}
    for package in (gate.AGENTS, gate.CORE):
        for path in sorted((_ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            sources[path.relative_to(_ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return sources


def test_the_real_tree_keeps_every_door_on_the_delegation_view() -> None:
    sources = _real_tree()
    assert len(sources) > 100
    missing = gate.expected_files() - set(sources)
    assert not missing, f"a file the gate's sets name is gone (rename the set too): {missing}"
    violations = gate.check(sources)
    assert violations == [], "\n".join(str(v) for v in violations)


# === The synthetic tree =====================================================

_RUNTIME = """
from aelix_coding_agent.agents.discovery import ProfileError, complete_profile, discover_profiles
from aelix_coding_agent.agents.discovery import resolve_profile as discover_profile
from aelix_coding_agent.core.agent_selection import delegation_view
from aelix_coding_agent.subagent_contract import ResolvedProfile


def delegation_for(host):
    discovered = discover_profiles(host.cwd(), project_trusted=True, agent_dir=None)
    return delegation_view(discovered, host.agent_lists(), host.active_profile())


def _resolved(profile, *, allow_project):
    if profile.scope == "project" and not allow_project:
        raise ProfileError("project")
    return ResolvedProfile(name=profile.name, profile=profile, source_path="", scope="")


class _SubagentRuntimeImpl:
    def resolve_profile(self, name, *, allow_project=False):
        return _resolved(discover_profile(name, cwd="."), allow_project=allow_project)

    def delegation(self):
        return delegation_for(self.host)

    def resolve_for_model(self, name):
        view = self.delegation()
        if view.verdict(name) != "ok":
            raise ProfileError(name)
        return _resolved(complete_profile(view.winner(name), view.discovered), allow_project=False)

    def _listed_predicate(self):
        def listed(profile):
            return self.delegation().verdict(profile) == "ok"

        return listed

    async def spawn(self, resolved, task):
        return await self._spawn_consented(resolved, task)

    async def _spawn_consented(self, resolved, task):
        return await self._run(None, resolved, task)

    async def spawn_granted(self, grant, resolved, task):
        return await self._run(grant, resolved, task)

    async def resume_granted(self, grant, resolved, task):
        return await self._run(grant, resolved, task)
"""

_EXTENSION = """
from aelix_agents.batch import run_batch
from aelix_agents.jobs_client import _Launch
from aelix_agents.runtime import DelegationRefused, delegation_for
from aelix_agents.tool import PendingSpawn


class AgentsExtension:
    def _roster(self):
        return delegation_for(self._host())

    async def _on_tool_call(self, event, ctx, runtime, call):
        try:
            resolved = runtime.resolve_for_model(call.profile)
        except Exception as exc:
            return str(exc)
        self._pending[event.id] = PendingSpawn(grant=None, resolved=resolved, call=call)
        return None

    async def _approve_resume(self, event, ctx, runtime, call):
        try:
            resolved = runtime.resolve_for_model(call.profile)
        except Exception:
            return "changed"
        self._pending[event.id] = PendingSpawn(grant=None, resolved=resolved, call=call)
        return None

    async def _execute(self, pending, ctx):
        return await self._execute_pending(pending, ctx)

    async def _execute_pending(self, pending, ctx):
        runtime = self._runtime
        refused = None
        try:
            recheck = runtime.resolve_for_model(pending.call.profile)
            still_the_same = recheck.source_path == pending.grant.source_path
        except DelegationRefused as exc:
            refused, still_the_same = exc, False
        if not still_the_same:
            return refused
        if pending.call.action == "spawn":
            return await self._spawn(pending, ctx)
        if pending.resume is not None:
            return await runtime.resume_granted(pending.grant, pending.resolved, "task")
        if pending.call.mode == "single":
            return await runtime.spawn_granted(pending.grant, pending.resolved, "task")
        return await run_batch(runtime=runtime, grant=pending.grant, resolved=pending.resolved)

    async def _spawn(self, pending, ctx):
        launch = _Launch(grant=pending.grant, resolved=pending.resolved, task="t")
        return launch

    async def _control(self, call):
        return call
"""

_BATCH = """
class _Batch:
    pass


async def run_batch(*, runtime, grant, resolved):
    batch = _Batch(runtime=runtime, grant=grant, resolved=resolved)
    return await _member(batch, 0, "t")


async def _member(batch, index, task):
    return await batch.runtime.spawn_granted(batch.grant, batch.resolved, task)
"""

_DETACH = """
def product_allowlist():
    from aelix_agents.runtime import _SubagentRuntimeImpl

    return (
        _SubagentRuntimeImpl.spawn_granted,
        _SubagentRuntimeImpl.resume_granted,
        _SubagentRuntimeImpl._run,
    )
"""

_JOBS = """
from aelix_agents.runtime import SubagentHost


class _Launch:
    pass


def frozen_host(*, cwd):
    return SubagentHost(cwd=lambda: cwd)


class DelegationRunner:
    async def run(self, ctx):
        launch = self.launch
        return await launch.runtime._run(launch.grant, launch.resolved, launch.task)
"""

_COMMANDS = """
async def _agents_handler(ctx, args):
    return await _agents_run(ctx, args)


async def _agents_run(ctx, rest):
    runtime = ctx.runtime
    resolved = runtime.resolve_profile(rest)
    return await runtime.spawn(resolved, "task")
"""

_CONTROL = """
class RpcControlPlane:
    async def run_agent(self, params):
        runtime = self.runtime
        resolved = runtime.resolve_profile(params["name"])
        return await runtime.spawn(resolved, params["task"])
"""

_SERVICE = """
from aelix_coding_agent.core.agent_selection import delegation_view


class AgentProfileService:
    def delegation(self):
        return delegation_view(self.list(), None, self.active)
"""

_CLEAN: dict[str, str] = {
    "aelix_agents/runtime.py": _RUNTIME,
    "aelix_agents/extension.py": _EXTENSION,
    "aelix_agents/batch.py": _BATCH,
    "aelix_agents/detach.py": _DETACH,
    "aelix_agents/jobs_client.py": _JOBS,
    "aelix_coding_agent/tui/commands.py": _COMMANDS,
    "aelix_coding_agent/rpc/control.py": _CONTROL,
    "aelix_coding_agent/agents/service.py": _SERVICE,
}


def _with(path: str, old: str, new: str) -> dict[str, str]:
    sources = dict(_CLEAN)
    assert sources[path].count(old) == 1, old
    sources[path] = sources[path].replace(old, new)
    return sources


def _added(path: str, text: str) -> dict[str, str]:
    sources = dict(_CLEAN)
    sources[path] = sources.get(path, "") + text
    return sources


def _rules(sources: dict[str, str]) -> set[str]:
    return {v.rule for v in gate.check(sources)}


def test_the_clean_synthetic_tree_reports_nothing() -> None:
    assert gate.check(_CLEAN) == []


_EXT = "aelix_agents/extension.py"
_RUN = "aelix_agents/runtime.py"
_CMD = "aelix_coding_agent/tui/commands.py"
_RECHECK = "            recheck = runtime.resolve_for_model(pending.call.profile)\n"
_SINGLE = (
    '        if pending.call.mode == "single":\n'
    '            return await runtime.spawn_granted(pending.grant, pending.resolved, "task")'
)
_CONTROL = "    async def _control(self, call):\n        return call"


def _in_control(body: str) -> dict[str, str]:
    """``AgentsExtension._control`` doing *body*: no door belongs there (opus 2 (b))."""

    return _with(_EXT, _CONTROL, f"    async def _control(self, call):\n        {body}")


def _swapped(header: str, copy: str) -> dict[str, str]:
    """The approved identity copied in ``_execute_pending`` by *copy*, after *header*."""

    sources = _with(
        _EXT,
        _SINGLE,
        _SINGLE.replace("            return", f"            swapped = {copy}\n            return"),
    )
    sources[_EXT] = header + sources[_EXT]
    return sources


_BYPASSES: list[tuple[str, dict[str, str], set[str]]] = [
    (
        # G1 — Codex's probe: a new branch runs another object.
        "G1",
        _with(
            _EXT,
            '        if pending.call.mode == "single":',
            '        if pending.call.mode == "other":\n'
            "            return await runtime.spawn_granted(pending.grant, other_resolved, 'x')\n"
            '        if pending.call.mode == "single":',
        ),
        {"F2"},
    ),
    (
        # G2 — M1: a reference, not a call.
        "G2",
        _with(
            _EXT,
            "        except Exception as exc:\n            return str(exc)",
            "        except Exception as exc:\n"
            "            f = runtime.resolve_profile\n"
            "            f(call.profile)\n"
            "            return str(exc)",
        ),
        {"C1"},
    ),
    (
        # G3 — the hook's ``resolved`` from the unfiltered door, in an else branch.
        "G3",
        _with(
            _EXT,
            "        except Exception as exc:\n            return str(exc)\n"
            "        self._pending[event.id] = PendingSpawn(grant=None, resolved=resolved, call=call)",
            "        except Exception as exc:\n            return str(exc)\n"
            "        else:\n"
            "            resolved = runtime.resolve_profile(call.profile)\n"
            "        self._pending[event.id] = PendingSpawn(grant=None, resolved=resolved, call=call)",
        ),
        {"F1", "C1"},
    ),
    (
        # G4 — a dead binding above a PendingSpawn of another name: line order would pass it.
        "G4",
        _with(
            _EXT,
            "        self._pending[event.id] = PendingSpawn(grant=None, resolved=resolved, call=call)\n"
            "        return None\n\n    async def _approve_resume",
            "        if False:\n"
            "            other = runtime.resolve_for_model(call.profile)\n"
            "        other = self._other\n"
            "        self._pending[event.id] = PendingSpawn(grant=None, resolved=other, call=call)\n"
            "        return None\n\n    async def _approve_resume",
        ),
        {"F1"},
    ),
    (
        # G5 — the re-check moved inside a branch: not a statement of the body.
        "G5",
        _with(
            _EXT,
            "        refused = None\n        try:\n"
            "            recheck = runtime.resolve_for_model(pending.call.profile)\n"
            "            still_the_same = recheck.source_path == pending.grant.source_path\n"
            "        except DelegationRefused as exc:\n"
            "            refused, still_the_same = exc, False\n"
            "        if not still_the_same:\n            return refused\n",
            "        refused = None\n        still_the_same = True\n"
            '        if pending.call.mode == "single":\n'
            "            try:\n"
            "                recheck = runtime.resolve_for_model(pending.call.profile)\n"
            "                still_the_same = recheck.source_path == pending.grant.source_path\n"
            "            except DelegationRefused as exc:\n"
            "                refused, still_the_same = exc, False\n"
            "        if not still_the_same:\n            return refused\n",
        ),
        {"F3"},
    ),
    (
        # G6 — a control action that starts a child.
        "G6",
        _with(
            _EXT,
            "    async def _control(self, call):\n        return call",
            "    async def _control(self, call):\n"
            "        return await self._runtime.spawn_granted(call.grant, call.resolved, 't')",
        ),
        {"D1"},
    ),
    (
        # G7 — an identity object built from a profile file in a new helper.
        "G7",
        _added(
            "aelix_agents/runtime.py",
            "\n\ndef _helper(path):\n"
            "    from aelix_coding_agent.agents.discovery import load_profile_file\n\n"
            "    profile = load_profile_file(path, cwd='.').profile\n"
            "    return ResolvedProfile(name=profile.name, profile=profile, source_path=path, scope='')\n",
        ),
        {"B1", "A3"},
    ),
    (
        # G8a — M3: the discovery MODULE's resolve_profile in product-core passes.
        "G8a",
        _added(
            "aelix_coding_agent/tui/commands.py",
            "\n\ndef _peek(name):\n"
            "    from aelix_coding_agent.agents import discovery\n\n"
            "    return discovery.resolve_profile(name, cwd='.', project_trusted=False)\n",
        ),
        set(),
    ),
    (
        # G8b — and the runtime's resolve_profile in a new product-core function does not.
        "G8b",
        _added(
            "aelix_coding_agent/tui/commands.py",
            "\n\ndef _sneak(runtime, name):\n    return runtime.resolve_profile(name)\n",
        ),
        {"E1"},
    ),
    (
        # G9 — M2: the sink by its name as a string.
        "G9",
        _added(
            _EXT,
            "\n\ndef _by_string(runtime):\n    return getattr(runtime, 'spawn_granted')\n",
        ),
        {"M2"},
    ),
    (
        # G10 — a "retry" that runs the runtime's inner launch on its own.
        "G10",
        _added(
            _EXT,
            "\n\nasync def _retry(runtime, launch):\n"
            "    return await runtime._run(launch.grant, launch.resolved, launch.task)\n",
        ),
        {"D5"},
    ),
    (
        # G11 — the panel computing its own view instead of the service's.
        "G11",
        _added(
            "aelix_coding_agent/core/agent_selection.py",
            "\n\nclass AgentKind:\n"
            "    def items(self):\n"
            "        return delegation_view(self.discovered, None, None)\n",
        ),
        {"E4"},
    ),
    (
        # G12 — the user-typed door, reached from aelix_agents.
        "G12",
        _added(
            _EXT,
            "\n\nasync def _shortcut(runtime, resolved):\n    return await runtime.spawn(resolved, 't')\n",
        ),
        {"D6"},
    ),
    (
        # G13 — the approved identity swapped for another profile.
        "G13",
        _with(
            _EXT,
            '        if pending.call.mode == "single":\n'
            '            return await runtime.spawn_granted(pending.grant, pending.resolved, "task")',
            '        if pending.call.mode == "single":\n'
            "            swapped = dataclasses.replace(pending.resolved, profile=other)\n"
            '            return await runtime.spawn_granted(pending.grant, pending.resolved, "task")',
        ),
        {"B1"},
    ),
    # --- fix round 1: a control for every rule that had none ---------------
    (
        # A1 — a second profile source in aelix_agents.
        "A1",
        _added(
            _EXT,
            "\n\ndef _peek(host):\n"
            "    from aelix_coding_agent.agents.discovery import discover_profiles\n\n"
            "    return discover_profiles(host.cwd())\n",
        ),
        {"A1"},
    ),
    (
        # A2 — discovery's resolve_profile under its import alias…
        "A2-alias",
        _added(_RUN, "\n\ndef _again(name):\n    return discover_profile(name, cwd='.')\n"),
        {"A2"},
    ),
    (
        # …and through the discovery MODULE (M3), in a new aelix_agents function.
        "A2-module",
        _added(
            _EXT,
            "\n\ndef _again(name):\n"
            "    from aelix_coding_agent.agents import discovery\n\n"
            "    return discovery.resolve_profile(name, cwd='.')\n",
        ),
        {"A2"},
    ),
    (
        "B2",
        _in_control("return PendingSpawn(grant=None, resolved=call.resolved, call=call)"),
        {"B2"},
    ),
    (
        "B3",
        _in_control("return _Launch(grant=call.grant, resolved=call.resolved, task='t')"),
        {"B3"},
    ),
    ("C2", _in_control("return self._runtime.resolve_for_model(call.profile)"), {"C2"}),
    ("C3", _in_control("return self._runtime.delegation().available"), {"C3"}),
    (
        "D2",
        _in_control("return await self._runtime.resume_granted(call.grant, call.resolved, 't')"),
        {"D2"},
    ),
    (
        "D3",
        _in_control(
            "return await run_batch(runtime=self._runtime, grant=call.grant, resolved=call.resolved)"
        ),
        {"D3"},
    ),
    ("D4", _in_control("return await self._spawn(call, None)"), {"D4"}),
    ("D7", _in_control("return await self._execute_pending(call, None)"), {"D7"}),
    (
        "E2",
        _added(
            _CMD,
            "\n\nasync def _quick(runtime, resolved):\n    return await runtime.spawn(resolved, 't')\n",
        ),
        {"E2"},
    ),
    (
        "E3",
        _added(
            _CMD,
            "\n\ndef _model_door(runtime, name):\n    return runtime.resolve_for_model(name)\n",
        ),
        {"E3"},
    ),
    (
        # F4 — the background launch built from another object…
        "F4-launch",
        _with(
            _EXT,
            'launch = _Launch(grant=pending.grant, resolved=pending.resolved, task="t")',
            'launch = _Launch(grant=pending.grant, resolved=self._other, task="t")',
        ),
        {"F4"},
    ),
    (
        # …a batch member that runs another object…
        "F4-member",
        _with(
            "aelix_agents/batch.py",
            "spawn_granted(batch.grant, batch.resolved, task)",
            "spawn_granted(batch.grant, batch.other, task)",
        ),
        {"F4"},
    ),
    (
        # …and a batch built from something other than its own parameters.
        "F4-batch",
        _with(
            "aelix_agents/batch.py",
            "    batch = _Batch(runtime=runtime, grant=grant, resolved=resolved)",
            "    resolved = runtime.other\n"
            "    batch = _Batch(runtime=runtime, grant=grant, resolved=resolved)",
        ),
        {"F4"},
    ),
    # --- the start sinks in PRODUCT-CORE (Codex 1 r1) -----------------------
    (
        "D1-core",
        _added(
            _CMD,
            "\n\nasync def _again(runtime, grant, resolved):\n"
            "    return await runtime.spawn_granted(grant, resolved, 't')\n",
        ),
        {"D1"},
    ),
    (
        "D2-core",
        _added(
            _CMD,
            "\n\nasync def _again(runtime, grant, resolved):\n"
            "    return await runtime.resume_granted(grant, resolved, 't')\n",
        ),
        {"D2"},
    ),
    (
        "D3-core",
        _added(
            _CMD,
            "\n\nasync def _again(runtime, grant, resolved):\n"
            "    from aelix_agents.batch import run_batch\n\n"
            "    return await run_batch(runtime=runtime, grant=grant, resolved=resolved)\n",
        ),
        {"D3"},
    ),
    (
        "D5-core",
        _added(
            _CMD,
            "\n\nasync def _again(runtime, launch):\n"
            "    return await runtime._run(launch.grant, launch.resolved, launch.task)\n",
        ),
        {"D5"},
    ),
    (
        "D7-core",
        _added(
            _CMD,
            "\n\nasync def _again(extension, pending):\n"
            "    return await extension._execute_pending(pending, None)\n",
        ),
        {"D7"},
    ),
    # --- a re-check its own expression can skip (Codex 2 r1) ----------------
    (
        "F3-or",
        _with(
            _EXT,
            _RECHECK,
            "            recheck = pending.resolved or runtime.resolve_for_model(pending.call.profile)\n",
        ),
        {"F3"},
    ),
    (
        "F3-ifexp",
        _with(
            _EXT,
            _RECHECK,
            "            recheck = (\n"
            "                pending.resolved if True else runtime.resolve_for_model(pending.call.profile)\n"
            "            )\n",
        ),
        {"F3"},
    ),
    # --- B1's copy forms under an import alias (Codex 5 r1) -----------------
    (
        "B1-alias",
        _swapped(
            "from dataclasses import replace as swap\n", "swap(pending.resolved, profile=other)"
        ),
        {"B1"},
    ),
    ("B1-module", _swapped("import copy as c\n", "c.copy(pending.resolved)"), {"B1"}),
    ("B1-deepcopy", _swapped("import copy\n", "copy.deepcopy(pending.resolved)"), {"B1"}),
    # --- M2's other string forms -------------------------------------------
    (
        "M2-attrgetter",
        _added(
            _EXT,
            "\n\ndef _by_getter(runtime):\n"
            "    import operator\n\n"
            "    return operator.attrgetter('spawn_granted')(runtime)\n",
        ),
        {"M2"},
    ),
    (
        "M2-attrgetter-alias",
        _added(
            _EXT,
            "\n\ndef _by_getter(holder):\n"
            "    from operator import attrgetter as ag\n\n"
            "    return ag('runtime.resume_granted')(holder)\n",
        ),
        {"M2"},
    ),
    (
        "M2-import-module",
        _added(
            _EXT,
            "\n\ndef _profiles(host):\n"
            "    import importlib\n\n"
            "    return importlib.import_module('aelix_coding_agent.agents.discovery')\n",
        ),
        {"M2"},
    ),
    (
        "M2-dunder-import",
        _added(
            _EXT,
            "\n\ndef _profiles(host):\n"
            "    return __import__('aelix_coding_agent.agents.profile')\n",
        ),
        {"M2"},
    ),
    # --- fix round 2: the doors the round-2 verifier found (V2-F1) ---------
    (
        # X5 — a control action starts a child through the user door's inner launch…
        "X5",
        _in_control(
            "return await self._runtime._spawn_consented(\n"
            "            call.resolved, 't', child_cwd='.', on_event=None, reservation=None\n"
            "        )"
        ),
        {"D8"},
    ),
    (
        # …and so does product-core.
        "X5-core",
        _added(
            _CMD,
            "\n\nasync def _again(runtime, resolved):\n"
            "    return await runtime._spawn_consented(\n"
            "        resolved, 't', child_cwd='.', on_event=None, reservation=None\n"
            "    )\n",
        ),
        {"D8"},
    ),
    (
        # X6 — an identity built through the constructor helper outside the resolvers…
        "X6-resolved",
        _in_control("return _resolved(call.profile, allow_project=True)"),
        {"B4"},
    ),
    (
        # …from product-core, under its import…
        "X6-core-resolved",
        _added(
            _CMD,
            "\n\ndef _identity(profile):\n"
            "    from aelix_agents.runtime import _resolved\n\n"
            "    return _resolved(profile, allow_project=True)\n",
        ),
        {"B4"},
    ),
    (
        # …a background launch built and started from product-core…
        "X6-core-launch",
        _added(
            _CMD,
            "\n\nasync def _core_job(facade, grant, resolved):\n"
            "    from aelix_agents.jobs_client import KIND, _Launch\n\n"
            "    launch = _Launch(grant=grant, resolved=resolved, task='t')\n"
            "    return await facade.start(KIND, title='t', params={'launch': launch})\n",
        ),
        {"B3"},
    ),
    (
        # …an approval built in product-core and handed to the extension's launch…
        "X6-core-pending",
        _added(
            _CMD,
            "\n\nasync def _core_spawn(ext, call, resolved, ctx):\n"
            "    from aelix_agents.tool import PendingSpawn\n\n"
            "    return await ext._spawn(PendingSpawn(grant=None, resolved=resolved, call=call), ctx)\n",
        ),
        {"B2"},
    ),
    (
        # …and the verifier's X6 whole: discovery, the helper, a launch, the job.
        "X6-core-whole",
        _added(
            _CMD,
            "\n\nasync def _core_door(facade, cwd, grant, host, channel):\n"
            "    from aelix_agents.jobs_client import KIND, _Launch\n"
            "    from aelix_agents.runtime import _resolved\n"
            "    from aelix_coding_agent.agents.discovery import discover_profiles\n\n"
            "    profile = discover_profiles(cwd, project_trusted=False, agent_dir=None).profiles[0]\n"
            "    resolved = _resolved(profile, allow_project=False)\n"
            "    launch = _Launch(grant=grant, resolved=resolved, task='t', cwd=cwd)\n"
            "    return await facade.start(KIND, title='t', params={'launch': launch})\n",
        ),
        {"B3", "B4"},
    ),
    # --- fix round 2: the checker clauses no control reached (V2-F2 (c)) ----
    (
        # A3 — the missing_path escalation called outside the model door.
        "A3-complete",
        _in_control("return complete_profile(call.profile, call.discovered)"),
        {"A3"},
    ),
    (
        # M1 — a gated name loaded under an import alias: a profile source…
        "M1-alias",
        _added(
            _EXT,
            "\n\ndef _peek(host):\n"
            "    from aelix_coding_agent.agents.discovery import discover_profiles as scan\n\n"
            "    return scan(host.cwd())\n",
        ),
        {"A1"},
    ),
    (
        # …and the identity constructor.
        "M1-alias-ctor",
        _added(
            _RUN,
            "\n\ndef _forge(profile):\n"
            "    from aelix_coding_agent.subagent_contract import ResolvedProfile as RP\n\n"
            "    return RP(name=profile.name, profile=profile, source_path='', scope='')\n",
        ),
        {"B1"},
    ),
    (
        # F2 — ``pending`` rebound before the sinks…
        "F2-rebound",
        _with(
            _EXT,
            '        if pending.call.action == "spawn":',
            '        pending = self._swap(pending)\n        if pending.call.action == "spawn":',
        ),
        {"F2"},
    ),
    (
        # …and a sink loaded but called through a local.
        "F2-not-called",
        _with(
            _EXT,
            _SINGLE,
            '        if pending.call.mode == "single":\n'
            "            launch = runtime.spawn_granted\n"
            '            return await launch(pending.grant, pending.resolved, "task")',
        ),
        {"F2"},
    ),
    (
        # F3 — the re-check with no refusal after it…
        "F3-no-refusal",
        _with(_EXT, "        if not still_the_same:\n            return refused\n", ""),
        {"F3"},
    ),
    (
        # …a refusal that falls through instead of returning…
        "F3-refusal-falls-through",
        _with(
            _EXT,
            "        if not still_the_same:\n            return refused\n",
            "        if not still_the_same:\n            self._log(refused)\n",
        ),
        {"F3"},
    ),
    (
        # …a second re-check…
        "F3-two-rechecks",
        _with(
            _EXT,
            _RECHECK,
            _RECHECK + "            again = runtime.resolve_for_model(pending.call.profile)\n",
        ),
        {"F3"},
    ),
    (
        # …a re-check of another name than the call's…
        "F3-other-name",
        _with(
            _EXT,
            _RECHECK,
            "            recheck = runtime.resolve_for_model(pending.resolved.name)\n",
        ),
        {"F3"},
    ),
]

_SPAWN_BRANCH = (
    '        if pending.call.action == "spawn":\n'
    "            return await self._spawn(pending, ctx)\n"
)


def _sink_before_the_recheck() -> dict[str, str]:
    """``_execute_pending`` with the spawn branch moved above the re-check."""

    sources = _with(_EXT, _SPAWN_BRANCH, "")
    sources[_EXT] = sources[_EXT].replace(
        "        refused = None\n", _SPAWN_BRANCH + "        refused = None\n", 1
    )
    return sources


_BYPASSES.append(("F3-after-a-sink", _sink_before_the_recheck(), {"F3"}))

_F3_WHERE = "F3: extension.py::AgentsExtension._execute_pending: "
_F3_CLAUSES: list[tuple[str, dict[str, str], str]] = [
    (
        "order",
        _sink_before_the_recheck(),
        "the re-check is not a statement of the body, run on every path, before every sink",
    ),
    (
        "refusal",
        _with(_EXT, "        if not still_the_same:\n            return refused\n", ""),
        "no `if <what the re-check bound>: ... return` before the sinks",
    ),
    (
        "count",
        _with(
            _EXT,
            _RECHECK,
            _RECHECK + "            again = runtime.resolve_for_model(pending.call.profile)\n",
        ),
        "expected exactly one resolve_for_model(pending.call.profile)",
    ),
]


@pytest.mark.parametrize(
    ("label", "sources", "rules"), _BYPASSES, ids=[label for label, _s, _r in _BYPASSES]
)
def test_each_synthetic_bypass_reports_exactly_its_rule(
    label: str, sources: dict[str, str], rules: set[str]
) -> None:
    assert _rules(sources) == rules, f"{label}: {[str(v) for v in gate.check(sources)]}"


@pytest.mark.parametrize(
    ("label", "sources", "words"), _F3_CLAUSES, ids=[label for label, _s, _w in _F3_CLAUSES]
)
def test_each_f3_clause_reports_in_its_own_words(
    label: str, sources: dict[str, str], words: str
) -> None:
    """V2-F2 (c): a sink above the re-check also leaves no refusal between them, so
    the rule set alone cannot tell the order clause from the refusal clause."""

    assert [str(v) for v in gate.check(sources)] == [_F3_WHERE + words], label


def test_a_failure_names_the_rule_the_sets_and_the_table_line() -> None:
    violations = gate.check(_BYPASSES[5][1])  # G6
    assert len(violations) == 1
    text = str(violations[0])
    assert text.startswith("D1: ")
    assert "extension.py::AgentsExtension._control" in text
    assert "tests/agents/delegation_gate.py line " in text
    line = int(text.rsplit("line ", 1)[1].rstrip(")"))
    lines = Path(gate.__file__).read_text(encoding="utf-8").splitlines()
    # The table entry starts on that line; its rule name is there or on the next.
    assert "_rule(" in lines[line - 1]
    assert '"D1"' in " ".join(lines[line - 1 : line + 1])


def test_a_frozen_job_host_that_wires_the_getters_is_flagged() -> None:
    # F5: the job runner resolves nothing, so its host carries no policy getters.
    sources = _with(
        "aelix_agents/jobs_client.py",
        "    return SubagentHost(cwd=lambda: cwd)",
        "    return SubagentHost(cwd=lambda: cwd, agent_lists=lambda: None)",
    )
    assert _rules(sources) == {"F5"}
