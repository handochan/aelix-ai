"""#624 (ADR-0305 D4, D10; Codex 11, 12) — the TUI panel and RPC write the same bytes with the same effects.

For every kind (``skill``, ``tool``, ``extension``, ``agent``, ``mcp``) and three
scenarios, TWO rigs are built from one seed under two roots (:func:`_selection_rig`),
so nothing one path applies can reach the other:

* the TUI path drives ``SelectionPanelBody`` with keys (Space, the arrows, Enter)
  and awaits its save;
* the RPC path sends ``selections/list`` then ``selections/set`` with the same
  changes as an ARRAY in the same order, through ``run_rpc_mode`` over a wire.

Then every settings and ``mcp.json`` file is byte-identical between the roots,
the two snapshots serialise the same (the root replaced by ``<root>``), the
``SaveResult`` and ``ApplyResult`` are equal, and so is the runtime effect:
``kind.applied()``, the harness's active tools (tool), its skills (skill); for
``extension`` and ``mcp`` nothing is applied and the changed rows are pending.

Scenarios: ``plain``; ``concurrent`` — another writer changes the file between
the panel opening and Enter, and between ``selections/list`` and
``selections/set`` (case 1 leaves the user's edit a no-op: nothing written;
case 2 leaves it a real change: the same bytes); ``none`` — no edits, while
another writer changed the file, so both apply that change.

Each scenario saves to the global file and, as a second case, to this project's
(#624 fix round 1, verify V-F5: every case saved to Global, and the panel's ←
changed nothing, since it opens there). The panel's target is MOVED with the
arrows where the kind allows it, from the same seeded state on both sides: for
the four settings kinds the edits are made on Global and → takes them to This
project, or → then ← brings them back to Global. The ``mcp`` kind has one
writable file (ADR-0305 D12): with a project ``.aelix/mcp.json`` seeded the panel
opens on This project and ← is refused, without one it opens on Global and → is
refused, so no switch is possible there and the refusal is asserted instead: the
panel's warning and RPC's answer to a ``selections/set`` of the other scope are the
same sentence, which the test spells out (#624 fix round 2, verify V-F5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.core.mcp_servers import config_label
from aelix_coding_agent.rpc.control_selection import serialize_snapshot
from aelix_coding_agent.tui import selection_panel
from aelix_coding_agent.tui.selection_panel import SelectionPanelBody

from tests.env_sandbox import sandbox_home

KINDS = ("skill", "tool", "extension", "agent", "mcp")
SCENARIOS = ("plain", "concurrent-noop", "concurrent-real", "none")
TARGETS = ("global", "project")

#: Hand-formatted, two members on one line, a non-canonical number: the writer
#: keeps every byte it does not change (ADR-0305 D14).
MCP_JSON = (
    "{\n"
    '  "mcpServers": {\n'
    '    "one": {"command": "true", "args": []},\n'
    '    "two": {\n'
    '      "command": "true",\n'
    '      "env": {"RATE": "1.50"}\n'
    "    },\n"
    '    "three": {"command": "true", "enabled": false}\n'
    "  }\n"
    "}\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed(root: Path, *, project_mcp: bool = False) -> None:
    """The same files under every root (the seed is the function itself).

    *project_mcp*: a trusted ``.aelix/mcp.json`` too, which a launch here reads
    instead of the global one (ADR-0305 D12): the ``mcp`` kind's project case.
    """

    agent, work = root / "agent", root / "work"
    _write(
        agent / "settings.json",
        '{\n  "retryEnabled": true,\n  "compaction": {"threshold": 1.50},\n  "skills": []\n}\n',
    )
    _write(work / ".aelix" / "settings.json", '{"theme": "dark"}\n')
    for name in ("alpha", "beta", "gamma"):
        _write(
            agent / "skills" / name / "SKILL.md",
            f"---\nname: {name}\ndescription: skill {name}\n---\nbody\n",
        )
    for name in ("reviewer", "helper"):
        _write(
            agent / "agents" / f"{name}.md",
            f"---\nname: {name}\ndescription: profile {name}\n---\n\nYou help.\n",
        )
    # Two global extensions, and a project one sharing the name ``weather``.
    for folder in (agent / "extensions", work / ".aelix" / "extensions"):
        _write(folder / "weather.py", "def setup(aelix):\n    pass\n")
    _write(agent / "extensions" / "lint.py", "def setup(aelix):\n    pass\n")
    _write(agent / "mcp.json", MCP_JSON)
    if project_mcp:
        _write(work / ".aelix" / "mcp.json", MCP_JSON)


@dataclass
class SelectionRig:
    root: Path
    controls: dict[str, Any]
    harness: Any
    services: dict[str, Any]
    cleanups: list[Callable[[], Any]] = field(default_factory=list)


async def _selection_rig(root: Path, monkeypatch: pytest.MonkeyPatch) -> SelectionRig:
    """Everything built from *root*: settings manager, harness, inventory, the five controls."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.settings import SettingsManager
    from aelix_ai.streaming import Model
    from aelix_coding_agent.agents.service import AgentProfileService
    from aelix_coding_agent.builtin.permission_mode import PermissionPosture
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import _selection_controls, _settings_bases
    from aelix_coding_agent.core.agent_selection import build_agent_control
    from aelix_coding_agent.core.extension_selection import build_inventory, mcp_launch
    from aelix_coding_agent.core.mcp_servers import McpNameRecord
    from aelix_coding_agent.core.resource_selection import ApplyGate, settings_lists
    from aelix_coding_agent.core.skill_selection import (
        SkillSource,
        effective_skills,
        load_skill_catalog,
    )
    from aelix_coding_agent.extensions.loader import discover_and_load_extensions
    from aelix_coding_agent.tools import create_all_tools

    agent, work = root / "agent", root / "work"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    manager = SettingsManager.create(work, agent, project_trusted=True)
    global_base, project_base = _settings_bases(manager, str(work))

    def lists(key: str) -> Any:
        return settings_lists(
            manager, key, global_base=global_base, project_base=project_base, project_trusted=True
        )

    skills_holder = {
        "result": load_skill_catalog(
            [SkillSource("global", str(agent / "skills"))], lists("skills")
        )
    }
    agent_lists = {"result": lists("agents")}
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="fixture", provider="fixture"),
            cwd=str(work),
            tools=list(create_all_tools(str(work)).values()),
            project_trusted=True,
        )
    )
    harness.set_skills(effective_skills(skills_holder))
    service = AgentProfileService(
        cwd=str(work),
        project_trusted=True,
        parsed=Args(),
        baseline=Args(),
        skills_holder=skills_holder,
        agent_dir=str(agent),
        agent_lists=lambda: agent_lists["result"],
    )
    loaded = await discover_and_load_extensions([], cwd=work, agent_dir=agent)
    gate = ApplyGate()
    agent_control = build_agent_control(
        manager,
        cwd=str(work),
        project_trusted=True,
        global_base=global_base,
        project_base=project_base,
        agent_dir=str(agent),
        holder=agent_lists,
        delegation=service.delegation,
        harness=lambda: harness,
        gate=gate,
    )

    class _Runtime:
        @property
        def harness(self) -> Any:
            return harness

    controls = _selection_controls(
        settings_manager=manager,
        cwd=str(work),
        project_trusted=True,
        runtime=_Runtime(),
        parsed=Args(),
        skills_holder=skills_holder,
        gate=gate,
        mcp_manager=None,
        extension_errors=[],
        extension_holder={
            "inventory": build_inventory(loaded),
            "records": [],
            "reload_failure": None,
        },
        mcp_launch_record=mcp_launch([], [], set(), None),
        mcp_inherited=McpNameRecord(),
        agent_control=agent_control,
        eager_listing=True,
    ).by_kind()
    services = {
        "permission_posture": PermissionPosture(),
        "settings_manager": manager,
        "agent_service": service,
        "selections": controls,
    }
    return SelectionRig(root, controls, harness, services, [harness.dispose])


# --- the edits each kind makes ------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    edits: tuple[tuple[str, bool], ...]
    """``(row label, wanted)`` in toggle order; a label repeated is toggled twice."""
    target: str = "global"
    noop: tuple[str, str] = ("", "")
    """``(file, label)``: the concurrent writer makes this label's edit a no-op."""


PLANS: dict[str, Plan] = {
    "skill": Plan((("alpha", False), ("beta", False), ("beta", True), ("gamma", False))),
    "tool": Plan((("read", False), ("grep", False), ("ls", False), ("ls", True))),
    "extension": Plan(
        (("lint.py", False), ("weather.py (global)", False), ("weather.py (project)", False))
    ),
    "agent": Plan((("reviewer", False), ("helper", False), ("helper", True))),
    "mcp": Plan((("one", False), ("three", True), ("two", False))),
}

#: The key the concurrent writer gives each list, as in Codex 12's reproducer.
LIST_KEYS = {
    "skill": "skills",
    "tool": "defaultTools",
    "extension": "extensions",
    "agent": "agents",
}


def _plan(kind: str, scenario: str, target: str = "global") -> Plan:
    """Codex 12's case 1 is ONE edit the other writer makes a no-op; ``none`` has no edit."""

    if scenario == "none":
        return Plan((), target)
    if scenario == "concurrent-noop":
        return Plan(PLANS[kind].edits[:1], target)
    return Plan(PLANS[kind].edits, target)


def _keys(control: Any) -> dict[str, str]:
    return {s.item.label: s.item.key for s in control.selection.snapshot(control.kind).states}


def _changes(control: Any, plan: Plan) -> list[dict[str, Any]]:
    """The edits that survive the panel's own double-toggle cancel, in first-edit order."""

    keys = _keys(control)
    wanted: dict[str, bool] = {}
    saved = {s.item.key: s.saved for s in control.selection.snapshot(control.kind).states}
    for label, want in plan.edits:
        key = keys[label]
        if want == saved[key]:
            wanted.pop(key, None)
        else:
            wanted[key] = want
    return [{"key": key, "on": on} for key, on in wanted.items()]


def _other_writer(root: Path, kind: str, scenario: str, target: str = "global") -> None:
    """Another process edits the file between the open and the save (the same bytes in both roots).

    The file the save writes: the global one, or this project's for the project case.
    """

    if scenario == "plain":
        return
    folder = root / "agent" if target == "global" else root / "work" / ".aelix"
    if kind == "mcp":
        path = folder / "mcp.json"
        text = path.read_text("utf-8")
        if scenario == "concurrent-noop":
            text = text.replace(
                '"one": {"command": "true", "args": []}',
                '"one": {"command": "true", "args": [], "enabled": false}',
            )
        else:
            text = text.replace(
                '"three": {"command": "true", "enabled": false}\n',
                '"three": {"command": "true", "enabled": false},\n    "four": {"command": "true"}\n',
            )
        path.write_text(text, encoding="utf-8")
        return
    path = folder / "settings.json"
    data = json.loads(path.read_text("utf-8"))
    first = PLANS[kind].edits[0][0]
    name = first.removesuffix(".py").split(" ")[0] if kind != "extension" else "lint"
    if scenario == "concurrent-noop":
        data[LIST_KEYS[kind]] = [f"+{name}", f"-{name}"]  # the user's "off" is now a no-op
    else:
        data[LIST_KEYS[kind]] = [f"-{'gamma' if kind == 'skill' else 'zzz-not-a-row'}"]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --- the two paths ------------------------------------------------------------------


def _other_scope_refusal(root: Path, target: str) -> str:
    """The ``mcp`` kind's refusal of the file not in force (ADR-0305 D12), spelled out.

    *target* is the file in force: the global one when no project ``.aelix/mcp.json``
    is seeded, this project's when one is.
    """

    label = config_label(root / "agent" / "mcp.json", "global")
    if target == "global":
        return (
            "Cannot save to This project: aelix reads one mcp.json: a .aelix/mcp.json here "
            f"would replace {label} in this folder, so its other servers would stop here too; "
            "turning a global server off in one project waits for #644"
        )
    return f"Cannot save to Global: aelix reads .aelix/mcp.json in this folder, not {label}"


def _move_target(body: SelectionPanelBody, kind: str, target: str) -> str | None:
    """Move the "Save to" control to *target* with the arrows, as a user would.

    The four settings kinds open on Global with both files writable: → is a real
    switch to This project (the edits follow it), and → then ← a real switch back.
    The ``mcp`` kind opens on its one writable file; the other arrow is refused,
    and its warning's text is returned.
    """

    model = body.model
    if kind == "mcp":
        assert model.target == target, "the panel opens on the file in force"
        body.handle("right" if target == "global" else "left")
        assert model.message is not None and model.message[0] == "warn", model.message
        assert model.target == target
        return model.message[1]
    assert model.target == "global"
    assert body.handle("right") == "handled" and model.target == "project", model.message
    if target == "global":
        assert body.handle("left") == "handled" and model.target == "global", model.message
    assert model.message is None
    return None


async def _tui(
    rig: SelectionRig,
    kind: str,
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    target: str = "global",
) -> tuple[tuple[Any, Any], str | None]:
    """The panel's ``(SaveResult, ApplyResult)``, and the ``mcp`` kind's refusal of the
    other arrow (``None`` for the settings kinds)."""

    control = rig.controls[kind]
    captured: list[tuple[Any, Any]] = []
    real_commit = selection_panel.commit

    async def recording(*args: Any, **kwargs: Any) -> Any:
        result = await real_commit(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(selection_panel, "commit", recording)
    body = SelectionPanelBody(control.selection, control.kind, title=kind, noun=(kind, kind + "s"))
    plan = _plan(kind, scenario, target)
    keys = _keys(control)
    for label, _want in plan.edits:
        body.model.cursor = next(
            i for i, s in enumerate(body.model.visible()) if s.item.key == keys[label]
        )
        assert body.handle("space") == "handled"
        assert body.model.message is None, body.model.message
    refusal = _move_target(body, kind, plan.target)
    _other_writer(rig.root, kind, scenario, plan.target)
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None, body.model.message
    monkeypatch.setattr(selection_panel, "commit", real_commit)
    assert len(captured) == 1
    return captured[0], refusal


@contextlib.asynccontextmanager
async def _wire(rig: SelectionRig) -> AsyncIterator[Any]:
    from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

    from tests.rpc.test_runtime_control_lifecycle_594 import Wire

    wire = Wire()
    task = asyncio.create_task(
        run_rpc_mode(
            rig.harness,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
            **rig.services,
        )
    )
    try:
        yield wire
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(task, 30)


async def _call(wire: Any, request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    wire.send({"type": "aelix_control", "id": request_id, "method": method, "params": params})
    answer = await asyncio.wait_for(wire.response(request_id), 30)
    assert answer["success"] is True, answer
    return answer["data"]


async def _rpc(
    rig: SelectionRig, kind: str, scenario: str, target: str = "global"
) -> tuple[dict[str, Any], str | None]:
    """``selections/set``'s answer, and for the ``mcp`` kind the error of a
    ``selections/set`` to the other scope sent first (``None`` for the settings kinds)."""

    control = rig.controls[kind]
    plan = _plan(kind, scenario, target)
    changes = _changes(control, plan)
    refusal: str | None = None
    async with _wire(rig) as wire:
        await _call(wire, "list", "selections/list", {"kind": kind})
        if kind == "mcp":
            # One real edit, so the scope is checked (every scenario, ``none`` too).
            other = "project" if target == "global" else "global"
            change = [{"key": _keys(control)["one"], "on": False}]
            wire.send(
                {
                    "type": "aelix_control",
                    "id": "other-scope",
                    "method": "selections/set",
                    "params": {"kind": kind, "scope": other, "changes": change},
                }
            )
            refused = await asyncio.wait_for(wire.response("other-scope"), 30)
            assert refused["success"] is False, refused
            refusal = refused["error"]
        _other_writer(rig.root, kind, scenario, target)
        params: dict[str, Any] = {"kind": kind, "changes": changes}
        if changes:
            params["scope"] = plan.target
        return await _call(wire, "set", "selections/set", params), refusal


# --- what is compared ---------------------------------------------------------------


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*.json"))
        if p.name in ("settings.json", "mcp.json")
    }


def _rooted(value: Any, root: Path) -> Any:
    # The root is replaced inside each decoded string, never in JSON text: json.dumps
    # doubles every backslash, so on Windows a JSON text never holds str(root).
    forms = sorted({str(root), os.path.realpath(root)}, key=len, reverse=True)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            for form in forms:
                node = node.replace(form, "<root>")
            return node
        if isinstance(node, dict):
            return {walk(k): walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(json.loads(json.dumps(value, sort_keys=True, default=str)))


def test_rooted_replaces_a_root_that_holds_a_backslash() -> None:
    # Windows paths hold backslashes; a POSIX name may hold one too, so this runs anywhere.
    root = Path("/tmp") / "a\\b"
    value = {
        "path": f"{root}/x.json",
        "rows": [{"why": f"{root} is locked"}],
        "files": {f"{root}/mcp.json": "kept"},
        "n": 1,
    }
    assert _rooted(value, root) == {
        "path": "<root>/x.json",
        "rows": [{"why": "<root> is locked"}],
        "files": {"<root>/mcp.json": "kept"},
        "n": 1,
    }


def _effect(rig: SelectionRig, kind: str) -> dict[str, Any]:
    control = rig.controls[kind]
    snapshot = control.selection.snapshot(control.kind)
    effect: dict[str, Any] = {
        "applied": sorted(control.kind.applied()),
        "pending": sorted(s.item.key for s in snapshot.states if s.pending),
    }
    if kind == "tool":
        effect["active"] = list(rig.harness.runtime.actions.get_active_tools())
    if kind == "skill":
        effect["skills"] = sorted(s.name for s in rig.harness.skills)
    return _rooted(effect, rig.root)


@pytest.fixture
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)


@pytest.mark.usefixtures("_env")
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("kind", KINDS)
async def test_the_panel_and_rpc_write_the_same_bytes_with_the_same_effects(
    kind: str, scenario: str, target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tui_root, rpc_root = tmp_path / "tui", tmp_path / "rpc"
    project_mcp = kind == "mcp" and target == "project"
    _seed(tui_root, project_mcp=project_mcp)
    _seed(rpc_root, project_mcp=project_mcp)
    assert _files(tui_root).keys() == _files(rpc_root).keys()
    seeded = _rooted({k: v.decode() for k, v in _files(tui_root).items()}, tui_root)

    tui = await _selection_rig(tui_root, monkeypatch)
    try:
        (tui_save, tui_apply), tui_refusal = await _tui(tui, kind, scenario, monkeypatch, target)
        tui_effect = _effect(tui, kind)
        tui_snapshot = serialize_snapshot(
            tui.controls[kind].selection.snapshot(tui.controls[kind].kind),
            tui.controls[kind].kind.effect,
        )
    finally:
        for cleanup in tui.cleanups:
            await cleanup()
    rpc = await _selection_rig(rpc_root, monkeypatch)
    try:
        answer, rpc_refusal = await _rpc(rpc, kind, scenario, target)
        rpc_effect = _effect(rpc, kind)
        rpc_snapshot = serialize_snapshot(
            rpc.controls[kind].selection.snapshot(rpc.controls[kind].kind),
            rpc.controls[kind].kind.effect,
        )
    finally:
        for cleanup in rpc.cleanups:
            await cleanup()

    # The mcp kind's other file: the panel's arrow and RPC's set refuse it in one sentence.
    if kind == "mcp":
        assert tui_refusal == _other_scope_refusal(tui_root, target), tui_refusal
        assert rpc_refusal == _other_scope_refusal(rpc_root, target), rpc_refusal
    else:
        assert tui_refusal is None and rpc_refusal is None
    # The bytes on disk.
    tui_files, rpc_files = _files(tui_root), _files(rpc_root)
    assert tui_files == rpc_files, {
        k: (tui_files.get(k), rpc_files.get(k))
        for k in tui_files.keys() | rpc_files.keys()
        if tui_files.get(k) != rpc_files.get(k)
    }
    written_now = _rooted({k: v.decode() for k, v in tui_files.items()}, tui_root)
    if scenario == "concurrent-noop":
        # Codex 12: the other writer's ``["+x","-x"]`` (or "enabled": false) is
        # what both leave on disk; neither path re-wrote it.
        assert answer["written"] == [], answer
    elif scenario == "plain":
        assert written_now != seeded, "anti-vacuity: the plain scenario wrote something"
        # The target's file, and only that one, changed (the other is the seed's).
        folder = "agent" if target == "global" else "work/.aelix"
        name = "mcp.json" if kind == "mcp" else "settings.json"
        moved = {k for k in written_now if written_now[k] != seeded[k]}
        assert moved == {str(Path(folder) / name)}, moved
        assert answer["scope"] == target
    # The save and the apply.
    save = (
        {"written": [], "changed": [], "mismatches": []}
        if tui_save is None
        else {
            "written": list(tui_save.written),
            "changed": [{"label": label, "saved": value} for label, value in tui_save.changed],
            "mismatches": list(tui_save.mismatches),
        }
    )
    assert _rooted(save, tui_root) == _rooted(
        {k: answer[k] for k in ("written", "changed", "mismatches")}, rpc_root
    )
    tui_applied = None if tui_apply is None else _rooted(asdict(tui_apply), tui_root)
    rpc_applied = (
        None
        if answer["apply"] is None
        else {
            "applied": answer["apply"]["applied"],
            "deferred": answer["apply"]["deferred"],
            "turned_on": answer["apply"]["turnedOn"],
            "turned_off": answer["apply"]["turnedOff"],
            "warning": answer["apply"]["warning"],
        }
    )
    assert tui_applied == _rooted(rpc_applied, rpc_root)
    # What each client sees afterwards, and what the session uses.
    assert _rooted(tui_snapshot, tui_root) == _rooted(rpc_snapshot, rpc_root)
    assert tui_effect == rpc_effect
    if kind in ("extension", "mcp") and scenario == "plain":
        assert answer["apply"] is None
        assert tui_effect["pending"], "the saved change waits for /reload or the next start"
    if scenario == "none" and kind == "skill":
        # The other writer turned gamma off; both applied what the file says now.
        assert "gamma" not in tui_effect["skills"]
