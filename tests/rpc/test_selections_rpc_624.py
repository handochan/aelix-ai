"""#624 (ADR-0305 D1-D7) — ``selections/list`` and ``selections/set`` over a real ``run_rpc_mode``.

Every case runs on a control-v1 rig (``tests/rpc/control_v1/rigs.py``): the
seeded files, the services the CLI injects, and the five selection controls the
CLI's own ``_selection_controls`` builds. What the fixtures cannot say is here:

* refusals write nothing (the file's bytes before and after);
* every row a kind locks or cannot change is refused with exactly the panel's
  words, and no placeholder row is ever changeable (opus L3);
* an untrusted folder: per-kind rows (Codex 14), no ``/trust`` anywhere on the
  wire (opus L8), and nothing under ``.aelix/`` opened (opus M4);
* cancelling: a queued set, a running save (``not_cancellable``), and the consent
  dialog (cancellable, then the mutation lock is free; opus M2);
* the consent dialog's bound (45 s, patched small), a declined and a confirmed one;
* a deferred apply and the last writer (opus M8), an unreadable global file;
* the ``mcp`` writer's lock held by another process: the loop keeps answering;
* ``SECRET-624`` in ``mcp.json`` reaches no wire record;
* ``capabilities.selectionKinds`` without an agent service.
"""

from __future__ import annotations

import asyncio
import builtins
import io
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.core import resource_selection
from aelix_coding_agent.core.resource_selection import ResourceSelection, edit_refusal
from aelix_coding_agent.rpc import control_selection

from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")

KINDS = ("skill", "tool", "extension", "agent", "mcp")
SECRET = "SECRET-624"


async def _ask(rig: rigs.Rig, request_id: str, method: str, **params: Any) -> dict[str, Any]:
    return await rigs.request(rig.wire, request_id, method, params)


def _bytes(root: Path) -> dict[str, bytes]:
    """Every settings and mcp.json file under *root*, by relative path."""

    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*.json"))
        if p.name in ("settings.json", "mcp.json")
    }


def _gate(rig: rigs.Rig) -> resource_selection.ApplyGate:
    return rig.services["selections"]["tool"].kind._gate  # noqa: SLF001 — the rig's one gate


def _all_files(rig: rigs.Rig) -> dict[str, bytes]:
    assert rig.seed is not None
    files = _bytes(rig.seed.root)
    files.update(
        {f"env:{k}": v for k, v in _bytes(Path(os.environ["AELIX_CODING_AGENT_DIR"])).items()}
    )
    return files


# --- refusals write nothing ---------------------------------------------------------


MALFORMED = [
    (
        "unknown row",
        {"kind": "skill", "scope": "global", "changes": [{"key": "nope", "on": False}]},
    ),
    (
        "on not a bool",
        {"kind": "skill", "scope": "global", "changes": [{"key": "fixture-skill", "on": 0}]},
    ),
    (
        "changes an object",
        {"kind": "skill", "scope": "global", "changes": {"fixture-skill": False}},
    ),
    (
        "1025 entries",
        {
            "kind": "skill",
            "scope": "global",
            "changes": [{"key": f"s{n}", "on": True} for n in range(1025)],
        },
    ),
    (
        "key too long",
        {"kind": "skill", "scope": "global", "changes": [{"key": "k" * 8193, "on": False}]},
    ),
    (
        "key twice",
        {
            "kind": "skill",
            "scope": "global",
            "changes": [
                {"key": "fixture-skill", "on": False},
                {"key": "fixture-skill", "on": False},
            ],
        },
    ),
    ("missing scope", {"kind": "skill", "changes": [{"key": "fixture-skill", "on": False}]}),
    (
        "unknown scope",
        {"kind": "skill", "scope": "x", "changes": [{"key": "fixture-skill", "on": False}]},
    ),
    ("locked row", {"kind": "tool", "scope": "global", "changes": [{"key": "find", "on": True}]}),
]


@pytest.mark.parametrize(("label", "params"), MALFORMED, ids=[m[0] for m in MALFORMED])
async def test_a_refused_set_writes_nothing(
    label: str, params: dict[str, Any], tmp_path: Path
) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        before = _all_files(rig)
        answer = await _ask(rig, "set", "selections/set", **params)
        assert answer["success"] is False, answer
        assert len(answer["error"]) <= 400, "an error quotes at most 120 characters of a key"
        assert _all_files(rig) == before


async def test_the_long_key_error_quotes_at_most_120_characters(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="global",
            changes=[{"key": "q" * 8193, "on": False}],
        )
    assert answer["error"].count("q") == 120, answer["error"]


@pytest.mark.parametrize("rig_name", ["full", "child"])
async def test_every_row_that_cannot_change_is_refused_with_its_own_words(
    rig_name: str, tmp_path: Path
) -> None:
    """Each kind's locked and placeholder rows: the set errors with ``edit_refusal``'s text.

    And the meta-check (opus L3): a row that is not an ``item``, or names nothing,
    is never changeable — it carries a lock or gets the placeholder refusal.
    """

    setup = "untrusted_project_resources" if rig_name == "child" else "an_extension_on_disk"
    async with rigs.open_rig(rig_name, tmp_path / "rig", setup=setup) as rig:
        refused = 0
        for kind in KINDS:
            control = rig.services["selections"][kind]
            snapshot = control.selection.snapshot(control.kind)
            for state in snapshot.states:
                refusal = edit_refusal(state)
                if state.item.role != "item" or not state.item.identities:
                    assert refusal is not None, f"{kind} {state.item.key}: changeable placeholder"
                if refusal is None:
                    continue
                before = _all_files(rig)
                answer = await _ask(
                    rig,
                    f"set-{refused}",
                    "selections/set",
                    kind=kind,
                    scope="global",
                    changes=[{"key": state.item.key, "on": not state.in_force}],
                )
                assert answer.get("error") == control_selection.rpc_words(refusal), (kind, answer)
                assert _all_files(rig) == before, (kind, state.item.key)
                refused += 1
    assert refused >= (5 if rig_name == "child" else 3), "anti-vacuity: locked rows were found"


# --- an untrusted folder ------------------------------------------------------------


async def test_an_untrusted_folder_lists_its_per_kind_rows_without_the_trust_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 14's table, opus L8 (no ``/trust``) and opus M4 (nothing under .aelix opened)."""

    opened: list[str] = []
    real_open, real_os_open = builtins.open, os.open
    project: list[Path] = []

    def guard(path: Any) -> None:
        if project and str(project[0]) in os.path.abspath(os.fspath(path)):
            opened.append(os.fspath(path))
            raise PermissionError(f"opened under the untrusted project: {path}")

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, str | os.PathLike):
            guard(file)
        return real_open(file, *args, **kwargs)

    def guarded_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        guard(path)
        return real_os_open(path, *args, **kwargs)

    async with rigs.open_rig("child", tmp_path / "rig", setup="untrusted_project_resources") as rig:
        assert rig.seed is not None
        project.append(rig.seed.cwd / ".aelix")
        monkeypatch.setattr(builtins, "open", guarded_open)
        monkeypatch.setattr(io, "open", guarded_open)  # Path.read_text / read_bytes
        monkeypatch.setattr(os, "open", guarded_os_open)
        with pytest.raises(PermissionError):  # anti-vacuity: the guard bites here
            (rig.seed.cwd / ".aelix" / "mcp.json").read_text("utf-8")
        opened.clear()
        rows: dict[str, list[dict[str, Any]]] = {}
        for kind in KINDS:
            answer = await _ask(rig, f"list-{kind}", "selections/list", kind=kind)
            assert answer["success"] is True, answer
            data = answer["data"]
            assert data["scopes"]["project"]["writable"] is False
            assert data["scopes"]["project"]["reason"] in (
                "this folder is not trusted",
                None,
            ) or "mcp.json" in (data["scopes"]["project"]["reason"] or "")
            rows[kind] = data["items"]
        refused = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="project",
            changes=[{"key": "fixture-skill", "on": False}],
        )
        monkeypatch.setattr(builtins, "open", real_open)
        monkeypatch.setattr(io, "open", real_open)
        monkeypatch.setattr(os, "open", real_os_open)
        records = json.dumps(rig.wire.records)
    assert refused["error"] == "Cannot save to This project: this folder is not trusted"
    assert "/trust" not in records
    assert opened == [], opened

    def locked(kind: str) -> list[tuple[str, str, str]]:
        return [
            (r["role"], r["lock"]["kind"], r["lock"]["tag"])
            for r in rows[kind]
            if r["lock"] is not None
            and r["lock"]["kind"] in ("", "untrusted")
            and "trusted" in r["lock"]["reason"]
        ]

    assert ("item", "", "") in locked("skill"), rows["skill"]
    assert ("item", "untrusted", "untrusted") in locked("extension"), rows["extension"]
    assert ("placeholder", "", "") in locked("agent"), rows["agent"]
    assert locked("mcp") == [("placeholder", "untrusted", "untrusted")], rows["mcp"]
    assert [r for r in rows["mcp"] if r["role"] == "item"] == [], (
        "no server rows from an unread file"
    )


# --- cancelling ---------------------------------------------------------------------


async def test_a_running_save_is_not_cancellable_and_a_queued_set_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    real_save = ResourceSelection.save

    async def held_save(self: ResourceSelection, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await real_save(self, *args, **kwargs)

    monkeypatch.setattr(ResourceSelection, "save", held_save)
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        assert rig.seed is not None
        before = rig.seed.settings_path.read_bytes()
        change = [{"key": "fixture-skill", "on": False}]
        rig.wire.send(
            rigs_control("running", "selections/set", kind="skill", scope="global", changes=change)
        )
        await asyncio.wait_for(entered.wait(), rigs.WAIT_S)
        rig.wire.send(
            rigs_control(
                "queued",
                "selections/set",
                kind="tool",
                scope="global",
                changes=[{"key": "read", "on": False}],
            )
        )
        await asyncio.sleep(0)
        verdict = await _ask(rig, "cancel-running", "commands/cancel")
        queued = await _ask(rig, "cancel-queued", "commands/cancel", requestId="queued")
        release.set()
        running = await rig.wire.response("running")
        dropped = await rig.wire.response("queued")
        after = rig.seed.settings_path.read_bytes()
    assert verdict["data"] == {
        "cancelled": False,
        "method": "selections/set",
        "reason": "not_cancellable",
    }
    assert queued["data"] == {"cancelled": True, "method": "selections/set"}
    assert running["success"] is True, running
    assert dropped == {**dropped, "success": False, "error": "Command cancelled"}
    assert after != before and b'"-read"' not in after, "the queued tool change wrote nothing"


def rigs_control(request_id: str, method: str, **params: Any) -> dict[str, Any]:
    return {"type": "aelix_control", "id": request_id, "method": method, "params": params}


@pytest.mark.parametrize("by_id", [False, True])
async def test_a_cancel_during_the_consent_dialog_ends_it_and_frees_the_lock(
    by_id: bool, tmp_path: Path
) -> None:
    async with rigs.open_rig("full", tmp_path / "rig", setup="project_settings_absent") as rig:
        assert rig.seed is not None
        target = rig.seed.cwd / ".aelix" / "settings.json"
        await _ask(rig, "ui", "ui/enable", enabled=True)
        rig.wire.send(
            rigs_control(
                "set",
                "selections/set",
                kind="skill",
                scope="project",
                changes=[{"key": "fixture-skill", "on": False}],
            )
        )
        dialog = await rig.wire.record(lambda r: r.get("type") == "extension_ui_request")
        assert dialog["method"] == "confirm"
        params = {"requestId": "set"} if by_id else {}
        verdict = await _ask(rig, "cancel", "commands/cancel", **params)
        answer = await rig.wire.response("set")
        started = time.monotonic()
        after = await _ask(rig, "after", "settings/set", key="retry_enabled", value=False)
        waited = time.monotonic() - started
    assert verdict["data"] == {"cancelled": True, "method": "selections/set"}
    assert answer["success"] is False and answer["error"] == "Command cancelled"
    assert not target.exists()
    assert after["success"] is True, after
    assert waited < 5, f"the mutation lock was still held ({waited:.1f} s)"


@pytest.mark.parametrize("reply", [{"confirmed": False}, {"cancelled": True}, None])
async def test_a_dialog_not_confirmed_creates_nothing(
    reply: dict[str, Any] | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No, cancel and the bound (patched from 45 s to 0.2 s): "creating <path> was not confirmed"."""

    monkeypatch.setattr(control_selection, "CONSENT_TIMEOUT_SECONDS", 0.2)
    async with rigs.open_rig("full", tmp_path / "rig", setup="project_settings_absent") as rig:
        assert rig.seed is not None
        target = rig.seed.cwd / ".aelix" / "settings.json"
        await _ask(rig, "ui", "ui/enable", enabled=True)
        rig.wire.send(
            rigs_control(
                "set",
                "selections/set",
                kind="skill",
                scope="project",
                changes=[{"key": "fixture-skill", "on": False}],
            )
        )
        dialog = await rig.wire.record(lambda r: r.get("type") == "extension_ui_request")
        if reply is not None:
            rig.wire.send({"type": "extension_ui_response", "id": dialog["id"], **reply})
        answer = await rig.wire.response("set")
    assert answer["success"] is False
    assert answer["error"] == f"Not saved: creating {target} was not confirmed"
    assert not target.exists()


async def test_a_confirmed_dialog_creates_the_file_and_a_boolean_never_does(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig", setup="project_settings_absent") as rig:
        assert rig.seed is not None
        target = rig.seed.cwd / ".aelix" / "settings.json"
        boolean = await _ask(
            rig,
            "bool",
            "selections/set",
            kind="skill",
            scope="project",
            createProjectFile=True,
            changes=[{"key": "fixture-skill", "on": False}],
        )
        assert boolean["success"] is False and not target.exists()
        await _ask(rig, "ui", "ui/enable", enabled=True)
        rig.wire.send(
            rigs_control(
                "set",
                "selections/set",
                kind="skill",
                scope="project",
                changes=[{"key": "fixture-skill", "on": False}],
            )
        )
        dialog = await rig.wire.record(lambda r: r.get("type") == "extension_ui_request")
        rig.wire.send({"type": "extension_ui_response", "id": dialog["id"], "confirmed": True})
        answer = await rig.wire.response("set")
    assert answer["success"] is True, answer
    assert answer["data"]["written"] == [str(target)]
    assert json.loads(target.read_text("utf-8"))["skills"] == ["-fixture-skill"]


# --- deferred apply, the last writer, unreadable files ------------------------------


async def _settled(predicate: Any) -> None:
    deadline = time.monotonic() + rigs.WAIT_S
    while not predicate():
        assert time.monotonic() < deadline, "never settled"
        await asyncio.sleep(0.01)


async def test_a_deferred_apply_answers_deferred_and_lands_when_the_gate_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0305 D7: the save's write ran while a harness build held the gate."""

    real_save = ResourceSelection.save
    holder: list[rigs.Rig] = []

    async def save_then_hold(self: ResourceSelection, *args: Any, **kwargs: Any) -> Any:
        result = await real_save(self, *args, **kwargs)
        _gate(holder[0]).__enter__()  # a rebuild started while the write ran off the loop
        return result

    monkeypatch.setattr(ResourceSelection, "save", save_then_hold)
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        holder.append(rig)
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="tool",
            scope="global",
            changes=[{"key": "read", "on": False}],
        )
        pending = await _ask(rig, "list-1", "selections/list", kind="tool")
        assert "read" in rig.runtime.harness.runtime.actions.get_active_tools()
        _gate(rig).__exit__(None, None, None)
        await _settled(lambda: "read" not in rig.runtime.harness.runtime.actions.get_active_tools())
        settled = await _ask(rig, "list-2", "selections/list", kind="tool")
    assert answer["data"]["apply"] == {
        "applied": False,
        "deferred": True,
        "turnedOn": [],
        "turnedOff": ["read"],
        "warning": None,
    }
    row = {r["key"]: r for r in pending["data"]["items"]}["read"]
    assert (row["saved"], row["applied"], row["pending"]) == (False, True, True)
    row = {r["key"]: r for r in settled["data"]["items"]}["read"]
    assert (row["saved"], row["applied"], row["pending"]) == (False, False, False)
    assert not [r for r in rig.wire.records if r.get("type") not in ("response", "rig")], (
        "no event reports a deferred apply's end in version 1"
    )


async def test_the_last_writer_wins_a_deferred_tool_apply_over_tools_set_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0305 D7 (opus M8): the deferred delta lands over what tools/set_active set."""

    real_save = ResourceSelection.save
    holder: list[rigs.Rig] = []

    async def save_then_hold(self: ResourceSelection, *args: Any, **kwargs: Any) -> Any:
        result = await real_save(self, *args, **kwargs)
        _gate(holder[0]).__enter__()
        return result

    monkeypatch.setattr(ResourceSelection, "save", save_then_hold)
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        holder.append(rig)
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="tool",
            scope="global",
            changes=[{"key": "read", "on": False}],
        )
        assert answer["data"]["apply"]["deferred"] is True
        session = await _ask(rig, "active", "tools/set_active", names=["read", "grep"])
        assert session["data"]["activeTools"] == ["read", "grep"]
        _gate(rig).__exit__(None, None, None)
        actions = rig.runtime.harness.runtime.actions
        await _settled(lambda: "read" not in actions.get_active_tools())
        final = actions.get_active_tools()
    assert final == ["grep"], "the deferred delta (read off) landed over the session's list"


async def test_an_unreadable_global_file_is_listed_and_nothing_is_written(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig", setup="global_settings_unreadable") as rig:
        before = _all_files(rig)
        listed = await _ask(rig, "list", "selections/list", kind="skill")
        project = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="project",
            changes=[{"key": "fixture-skill", "on": False}],
        )
        nothing = await _ask(rig, "none", "selections/set", kind="skill", changes=[])
        after = _all_files(rig)
    assert listed["data"]["unreadable"] and listed["data"]["problems"], listed
    assert project["error"].startswith("Not saved: Cannot save: "), project
    assert nothing["data"]["apply"]["warning"].startswith("kept what was applied:"), nothing
    assert after == before


# --- the mcp kind over the wire -----------------------------------------------------

LOCKER = textwrap.dedent(
    """
    import fcntl, os, sys, time
    fd = os.open(sys.argv[1], os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="the directory lock is POSIX flock")
async def test_the_loop_keeps_answering_while_another_process_holds_the_mcp_lock(
    tmp_path: Path,
) -> None:
    """A writer waiting 1 s on the file's directory lock runs off the event loop (ADR-0305 D13)."""

    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        target = Path(os.environ["AELIX_CODING_AGENT_DIR"]) / "mcp.json"
        locker = subprocess.Popen(
            [sys.executable, "-c", LOCKER, str(target.parent), "1.0"], stdout=subprocess.PIPE
        )
        try:
            assert locker.stdout is not None and locker.stdout.readline().strip() == b"locked"
            started = time.monotonic()
            rig.wire.send(
                rigs_control(
                    "set",
                    "selections/set",
                    kind="mcp",
                    scope="global",
                    changes=[{"key": "seeded", "on": True}],
                )
            )
            listed = await _ask(rig, "list", "selections/list", kind="mcp")
            cancel = await _ask(rig, "cancel", "commands/cancel")
            answered = time.monotonic() - started
            done = await rig.wire.response("set")
        finally:
            locker.wait(10)
    assert listed["success"] is True and answered < 0.9, answered
    assert cancel["data"]["reason"] == "not_cancellable"
    assert done["success"] is True and done["data"]["written"] == [str(target)], done
    assert '"enabled": true' in target.read_text("utf-8")


async def test_no_secret_from_mcp_json_reaches_the_wire(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        target = Path(os.environ["AELIX_CODING_AGENT_DIR"]) / "mcp.json"
        target.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "keyed": {
                            "command": f"/bin/{SECRET}",
                            "args": [f"--token={SECRET}"],
                            "env": {"TOKEN": SECRET},
                        },
                        "remote": {
                            "url": f"https://h/{SECRET}?k={SECRET}",
                            "headers": {"Authorization": SECRET},
                            "transport": "http",
                        },
                        "odd": {"command": "true", "enabled": SECRET},
                        "listy": {"command": "true", "enabled": [SECRET]},
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        await _ask(rig, "list", "selections/list", kind="mcp")
        await _ask(
            rig,
            "set",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "keyed", "on": False}, {"key": "remote", "on": False}],
        )
        await _ask(
            rig,
            "refused",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "odd", "on": True}],
        )
        records = json.dumps(rig.wire.records)
    assert SECRET not in records
    assert records.count('"refusal"') >= 4


async def test_capabilities_lists_no_agent_kind_without_an_agent_service(tmp_path: Path) -> None:
    from aelix_coding_agent.rpc.control import RpcControlPlane
    from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime

    seed = rigs.seed_files(tmp_path / "seed")
    gate = asyncio.Event()
    gate.set()
    harness = await rigs.build_harness("full", seed, gate, None)
    services = rigs.rpc_services("full", seed, harness)
    services["selections"].pop("agent")
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None), **services)
    try:
        capabilities = await plane.handle("capabilities", {})
        refused = await plane.handle("selections/list", {"kind": "skill"})
        with pytest.raises(
            ValueError,
            match=r"^Unknown selection kind: agent \(this host serves: extension, mcp, skill, tool\)$",
        ):
            await plane.handle("selections/list", {"kind": "agent"})
    finally:
        await harness.dispose()
    assert capabilities["selectionKinds"] == ["extension", "mcp", "skill", "tool"]
    assert {"selections/list", "selections/set"} <= set(capabilities["methods"])
    assert refused["kind"] == "skill"


# --- what the verify pass's mutants showed unguarded --------------------------------


async def test_a_key_that_is_not_a_string_is_refused_by_its_index(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="global",
            changes=[{"key": 3, "on": True}],
        )
    assert answer["error"] == "changes[0] must be {key: string, on: boolean}"


@pytest.mark.parametrize(("length", "shown"), [(120, "u" * 120), (121, "u" * 120 + "…")])
async def test_an_error_quotes_120_characters_whole_and_cuts_one_more(
    length: int, shown: str, tmp_path: Path
) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="global",
            changes=[{"key": "u" * length, "on": False}],
        )
    assert answer["error"] == f"Unknown skill row: {shown}"


async def test_the_kind_is_refused_before_the_changes(tmp_path: Path) -> None:
    """ADR-0305 D4's order: an unknown kind with malformed changes names the kind."""

    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        answer = await _ask(rig, "set", "selections/set", kind="nope", changes="x")
    assert answer["error"].startswith("Unknown selection kind: nope ")


async def test_the_consent_dialog_waits_45_seconds_at_most(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.rpc.control_ui import RpcCommandUI

    timeouts: list[float] = []
    real_confirm = RpcCommandUI.confirm

    async def confirm(self: RpcCommandUI, title: str, message: str, opts: Any = None) -> bool:
        timeouts.append(opts.timeout)
        return await real_confirm(self, title, message, opts)

    monkeypatch.setattr(RpcCommandUI, "confirm", confirm)
    async with rigs.open_rig("full", tmp_path / "rig", setup="project_settings_absent") as rig:
        await _ask(rig, "ui", "ui/enable", enabled=True)
        rig.wire.send(
            rigs_control(
                "set",
                "selections/set",
                kind="skill",
                scope="project",
                changes=[{"key": "fixture-skill", "on": False}],
            )
        )
        dialog = await rig.wire.record(lambda r: r.get("type") == "extension_ui_request")
        rig.wire.send({"type": "extension_ui_response", "id": dialog["id"], "cancelled": True})
        await rig.wire.response("set")
    assert timeouts == [45.0]


async def test_once_the_dialog_is_answered_the_save_is_not_cancellable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0305 D4: the flag is cleared when the dialog ends, before the save starts."""

    entered, release = asyncio.Event(), asyncio.Event()
    real_save = ResourceSelection.save

    async def held_save(self: ResourceSelection, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await real_save(self, *args, **kwargs)

    monkeypatch.setattr(ResourceSelection, "save", held_save)
    async with rigs.open_rig("full", tmp_path / "rig", setup="project_settings_absent") as rig:
        assert rig.seed is not None
        await _ask(rig, "ui", "ui/enable", enabled=True)
        rig.wire.send(
            rigs_control(
                "set",
                "selections/set",
                kind="skill",
                scope="project",
                changes=[{"key": "fixture-skill", "on": False}],
            )
        )
        dialog = await rig.wire.record(lambda r: r.get("type") == "extension_ui_request")
        rig.wire.send({"type": "extension_ui_response", "id": dialog["id"], "confirmed": True})
        await asyncio.wait_for(entered.wait(), rigs.WAIT_S)
        verdict = await _ask(rig, "cancel", "commands/cancel", requestId="set")
        release.set()
        answer = await rig.wire.response("set")
        created = (rig.seed.cwd / ".aelix" / "settings.json").is_file()
    assert verdict["data"] == {
        "cancelled": False,
        "method": "selections/set",
        "reason": "not_cancellable",
    }
    assert answer["success"] is True and created


async def test_a_project_file_deleted_under_the_set_is_not_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0305 D6 (Codex 6) over RPC: the file exists at the check and is gone at the locked read."""

    from tests.core.test_resource_selection_624 import NOT_NOW, _delete_before_the_locked_read

    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        assert rig.seed is not None
        project = rig.seed.cwd / ".aelix" / "settings.json"
        barrier = _delete_before_the_locked_read(
            rig.services["settings_manager"], project, monkeypatch
        )
        barrier["armed"] = 1
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="project",
            changes=[{"key": "fixture-skill", "on": False}],
        )
    assert barrier["deleted"] == 1, "anti-vacuity: the barrier ran inside the transaction"
    assert answer["error"] == f"Not saved: {project} {NOT_NOW}"
    assert not project.exists()
    assert not [r for r in rig.wire.records if r.get("type") == "extension_ui_request"]


# --- the controls the CLI builds (cli/entry.py ``_selection_controls``) -------------


def _controls_inputs(tmp_path: Path, *, trusted: bool, no_extensions: bool) -> dict[str, Any]:
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.core.extension_selection import ExtensionInventory, mcp_launch
    from aelix_coding_agent.core.mcp_servers import McpNameRecord
    from aelix_coding_agent.core.resource_selection import ApplyGate, SettingsLists
    from aelix_coding_agent.core.skill_selection import load_skill_catalog

    agent, work = tmp_path / "agent", tmp_path / "work"
    (work / ".aelix").mkdir(parents=True)
    agent.mkdir()
    # The manager says trusted whatever the launch decided: a live /trust.
    manager = SettingsManager.create(work, agent, project_trusted=True)
    parsed = Args()
    parsed.no_extensions = no_extensions

    class _Runtime:
        harness = None

    return {
        "settings_manager": manager,
        "cwd": str(work),
        "project_trusted": trusted,
        "runtime": _Runtime(),
        "parsed": parsed,
        "skills_holder": {"result": load_skill_catalog([], SettingsLists())},
        "gate": ApplyGate(),
        "mcp_manager": None,
        "extension_errors": [],
        "extension_holder": {
            "inventory": ExtensionInventory(no_extensions=no_extensions),
            "records": [],
            "reload_failure": None,
        },
        "mcp_launch_record": mcp_launch([], [], set(), None),
        "mcp_inherited": McpNameRecord(),
        "agent_control": None,
    }


def test_a_launch_without_an_agent_service_serves_no_agent_kind(tmp_path: Path) -> None:
    from aelix_coding_agent.cli.entry import _selection_controls

    controls = _selection_controls(
        **_controls_inputs(tmp_path, trusted=True, no_extensions=False), eager_listing=True
    )
    assert sorted(controls.by_kind()) == ["extension", "mcp", "skill", "tool"]


@pytest.mark.parametrize(("eager", "trusted"), [(True, True), (False, True), (False, False)])
def test_the_no_extensions_listing_is_read_once_eagerly_for_the_tui_and_lazily_for_rpc(
    eager: bool, trusted: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli import entry

    calls: list[dict[str, Any]] = []

    def discover(paths: Any, **kwargs: Any) -> list[Any]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(entry, "discover_extension_records", discover)
    controls = entry._selection_controls(  # noqa: SLF001
        **_controls_inputs(tmp_path, trusted=trusted, no_extensions=True), eager_listing=eager
    )
    assert len(calls) == (1 if eager else 0), "the TUI reads it before it draws; RPC does not"
    extension = controls.extension
    for _ in range(2):
        extension.selection.snapshot(extension.kind)
    assert len(calls) == 1, "read once, then cached"
    assert calls[0]["no_project_local"] is (not trusted)


def test_the_mcp_kind_takes_trust_from_the_launch_and_the_live_flag(tmp_path: Path) -> None:
    """An untrusted launch whose manager reports trusted still never reads the project file."""

    from aelix_coding_agent.cli.entry import _selection_controls
    from aelix_coding_agent.core.mcp_selection import UNTRUSTED_KEY

    inputs = _controls_inputs(tmp_path, trusted=False, no_extensions=False)
    (Path(inputs["cwd"]) / ".aelix" / "mcp.json").write_text(
        '{"mcpServers": {"local": {"command": "true"}}}\n', encoding="utf-8"
    )
    controls = _selection_controls(**inputs, eager_listing=False)
    snapshot = controls.mcp.selection.snapshot(controls.mcp.kind)
    assert [s.item.key for s in snapshot.states] == [UNTRUSTED_KEY]
    assert snapshot.scopes["project"].writable is False


# --- fix-r1: the mcp kind reads mcp.json off the event loop (Codex CX-10) -----------------


@pytest.mark.parametrize("method", ["selections/list", "selections/set"])
async def test_a_slow_mcp_json_read_does_not_hold_up_another_request(
    method: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read of ``mcp.json`` takes 0.5 s here (a slow filesystem, a large file).

    They run off the loop: a ``settings/get`` sent right after the request answers
    long before it, and a ticker on the loop never stalls for a read, which covers
    ``selections/set``'s three (its snapshot, and the save's before and after).
    """

    from aelix_coding_agent.cli import config

    real_read = config._read_mcp_config  # noqa: SLF001
    slow = {"on": False, "reads": 0}

    def _slow_read(*args: Any, **kwargs: Any) -> Any:
        if slow["on"]:
            slow["reads"] += 1
            time.sleep(0.5)
        return real_read(*args, **kwargs)

    async def _tick(ticks: list[float]) -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    monkeypatch.setattr(config, "_read_mcp_config", _slow_read)
    params: dict[str, Any] = {"kind": "mcp"}
    if method == "selections/set":
        params.update(scope="global", changes=[{"key": "seeded", "on": True}])
    ticks: list[float] = []
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        slow["on"] = True
        ticker = asyncio.ensure_future(_tick(ticks))
        started = time.monotonic()
        rig.wire.send(rigs_control("slow", method, **params))
        quick = await _ask(rig, "quick", "settings/get")
        quick_s = time.monotonic() - started
        answer = await rig.wire.response("slow")
        ticks.append(time.monotonic())  # a stall just before the answer counts too
        slow_s = time.monotonic() - started
        ticker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ticker
    assert quick["success"] is True, quick
    assert answer["success"] is True, answer
    assert slow["reads"] == (1 if method == "selections/list" else 3), slow
    assert slow_s >= 0.5 * slow["reads"], slow_s
    assert quick_s < 0.25, (quick_s, slow_s)
    stall = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
    assert stall < 0.25, f"the loop stalled {stall:.3f} s during a read"


# --- fix-r1: a file that does not load is named, not "Unknown row" (live-RPC 2) -------------


async def test_a_set_against_an_mcp_json_that_does_not_load_says_why(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        target = Path(os.environ["AELIX_CODING_AGENT_DIR"]) / "mcp.json"
        target.write_text('{"mcpServers": {"seeded": ', encoding="utf-8")
        before = target.read_bytes()
        listed = await _ask(rig, "list", "selections/list", kind="mcp")
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "seeded", "on": True}],
        )
    reason = listed["data"]["scopes"]["global"]["reason"]
    # The JSON syntax warning gives where, two numbers (fix round 1, the lead's call).
    assert reason.endswith("did not load: not valid JSON at line 1 column 27 (JSONDecodeError)"), (
        reason
    )
    assert answer["success"] is False
    assert answer["error"] == f"Unknown mcp row: seeded; {reason}", answer
    assert target.read_bytes() == before


async def test_an_unknown_row_with_every_file_loaded_says_only_that(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "nope", "on": True}],
        )
    assert answer["error"] == "Unknown mcp row: nope", answer


# --- fix-r1: save errors carry RPC words, not TUI pointers (verifier F4, live-RPC 1) ---------


async def test_the_file_changed_error_says_list_again_over_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project ``.aelix/mcp.json`` appears while the save waits: refused, in RPC words."""

    from aelix_coding_agent.core import mcp_selection

    real = mcp_selection.write_enabled
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        assert rig.seed is not None
        project = rig.seed.cwd / ".aelix" / "mcp.json"

        def _checkout_then_write(*args: Any, **kwargs: Any) -> Any:
            project.write_text('{"mcpServers": {"other": {"command": "true"}}}\n', "utf-8")
            return real(*args, **kwargs)

        monkeypatch.setattr(mcp_selection, "write_enabled", _checkout_then_write)
        target = Path(os.environ["AELIX_CODING_AGENT_DIR"]) / "mcp.json"
        before = target.read_bytes()
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "seeded", "on": True}],
        )
        monkeypatch.undo()
        # Listed again: the project file is in force now, and its scope is the writable one.
        again = await _ask(
            rig,
            "again",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "other", "on": False}],
        )
    changed = (
        "Not saved: the file aelix reads here changed (now .aelix/mcp.json); "
        "nothing written; list again"
    )
    assert answer["error"] == changed, answer
    assert again["success"] is False, again
    assert again["error"].startswith("Cannot save to Global: aelix reads .aelix/mcp.json"), again
    for error in (answer["error"], again["error"]):
        assert "reopen" not in error and "/mcp" not in error.replace("mcp.json", ""), error
    assert target.read_bytes() == before


# --- fix-r1: escaped keys over RPC (Codex CX-11) -------------------------------------------


async def test_a_file_server_named_extension_colon_is_listed_and_set_by_its_key(
    tmp_path: Path,
) -> None:
    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        target = Path(os.environ["AELIX_CODING_AGENT_DIR"]) / "mcp.json"
        target.write_text(
            json.dumps({"mcpServers": {"extension:foo": {"command": "true"}}}, indent=2) + "\n",
            encoding="utf-8",
        )
        listed = await _ask(rig, "list", "selections/list", kind="mcp")
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="mcp",
            scope="global",
            changes=[{"key": "file:extension:foo", "on": False}],
        )
        saved = json.loads(target.read_text("utf-8"))["mcpServers"]
    items = [(i["key"], i["label"], i["identities"]) for i in listed["data"]["items"]]
    assert items == [("file:extension:foo", "extension:foo", ["extension:foo"])], items
    assert answer["success"] is True and answer["data"]["written"] == [str(target)], answer
    assert saved == {"extension:foo": {"command": "true", "enabled": False}}


# --- fix-r1: the no-op drop reads the merged value (verify r1 N2, ADR-0305 D4) ----------


async def test_a_global_set_to_what_this_projects_file_decides_writes_nothing(
    tmp_path: Path,
) -> None:
    """The drop compares with the row's ``saved``, which this project's file decides here.

    The global file says ``-fixture-skill`` and this project's ``+fixture-skill``, so
    the row is saved on, decided by the project. Turning it on in the GLOBAL file is a
    no-op for the drop: success, ``written: []``, and the global file keeps its ``-``.
    The panel cannot ask for this (its toggle starts from the row's value); the lead
    kept the behaviour (fix round 1) and ADR-0305 D4 says what an RPC client sees.
    """

    async with rigs.open_rig("full", tmp_path / "rig") as rig:
        seed = rig.seed
        assert seed is not None
        glob = (
            seed.settings_path if seed.settings_path.exists() else seed.agent_dir / "settings.json"
        )
        data = json.loads(glob.read_text("utf-8"))
        data["skills"] = ["-fixture-skill"]
        glob.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        (seed.cwd / ".aelix" / "settings.json").write_text(
            json.dumps({"skills": ["+fixture-skill"]}, indent=2) + "\n", encoding="utf-8"
        )
        listed = await _ask(rig, "list", "selections/list", kind="skill")
        row = next(r for r in listed["data"]["items"] if r["label"] == "fixture-skill")
        before = glob.read_bytes()
        answer = await _ask(
            rig,
            "set",
            "selections/set",
            kind="skill",
            scope="global",
            changes=[{"key": row["key"], "on": True}],
        )
        after = glob.read_bytes()
    assert (row["saved"], row["decidedBy"]) == (True, "project"), row
    assert answer["success"] is True, answer
    data = answer["data"]
    assert (data["written"], data["changed"], data["mismatches"]) == ([], [], []), data
    assert after == before
    assert json.loads(after)["skills"] == ["-fixture-skill"]
