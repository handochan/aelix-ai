"""#624 fix round 1 — row and snapshot fields over a real ``run_rpc_mode``, each against a hand-written value.

Verify r1 F3: ``inForce``, ``mismatches``, ``policyPending`` and ``liveNote`` (its mutants
B05, B27, B30 and B31) passed every test, because the parity test compares two sides of
ONE serializer. Here each expected value is written out from what the test set up — the
files it wrote and the session state it gave the kind — never computed by the code under
test.

And two rows the live RPC check found (verify r1, live-RPC 3 and 5):

* the built-in ``JobsExtension`` (#722) was an ``item`` with no identity and no lock,
  which ADR-0305 D3 says no row is: it now carries the lock that says what decides it;
* the extension kind's MCP status rows said ``saved: true, applied: false`` while their
  tag said ``connected``: like the mcp kind's own status rows (D16), ``saved`` and
  ``applied`` now both say whether this launch started the server.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.core.mcp_servers import McpServerStatus

from tests.rpc.test_selections_same_bytes_624 import (
    _call,
    _env,  # noqa: F401  (the fixture, by name)
    _seed,
    _selection_rig,
    _wire,
)

pytestmark = pytest.mark.usefixtures("_env")


def _row(listing: dict[str, Any], label: str) -> dict[str, Any]:
    (row,) = [r for r in listing["items"] if r["label"] == label]
    return row


async def _listed(rig: Any, kind: str) -> dict[str, Any]:
    async with _wire(rig) as wire:
        return await _call(wire, "list", "selections/list", {"kind": kind})


# --- inForce (B05) ---------------------------------------------------------------------


async def test_in_force_is_what_the_session_uses_while_the_file_says_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lint.py is loaded this session; after a set the global file turns it off, and the
    extension kind applies on /reload: in force (and applied) yes, saved no, pending."""

    _seed(tmp_path)
    rig = await _selection_rig(tmp_path, monkeypatch)
    try:
        async with _wire(rig) as wire:
            before = await _call(wire, "list", "selections/list", {"kind": "extension"})
            lint = _row(before, "lint.py")
            assert (lint["saved"], lint["applied"], lint["inForce"]) == (True, True, True)
            await _call(
                wire,
                "set",
                "selections/set",
                {
                    "kind": "extension",
                    "scope": "global",
                    "changes": [{"key": lint["key"], "on": False}],
                },
            )
            after = await _call(wire, "list2", "selections/list", {"kind": "extension"})
    finally:
        for cleanup in rig.cleanups:
            await cleanup()
    settings = json.loads((tmp_path / "agent" / "settings.json").read_text("utf-8"))
    assert any(e.startswith("-lint") for e in settings["extensions"]), settings
    lint = _row(after, "lint.py")
    assert lint["lock"] is None
    assert (lint["saved"], lint["applied"], lint["inForce"], lint["pending"]) == (
        False,
        True,
        True,
        True,
    )


# --- mismatches (B27) ------------------------------------------------------------------


async def test_a_global_save_this_projects_file_overrides_answers_its_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    project = tmp_path / "work" / ".aelix" / "settings.json"
    project.write_text('{"theme": "dark", "skills": ["+alpha"]}\n', encoding="utf-8")
    rig = await _selection_rig(tmp_path, monkeypatch)
    try:
        async with _wire(rig) as wire:
            listed = await _call(wire, "list", "selections/list", {"kind": "skill"})
            alpha = _row(listed, "alpha")
            assert (alpha["saved"], alpha["decidedBy"]) == (True, "project")
            answer = await _call(
                wire,
                "set",
                "selections/set",
                {
                    "kind": "skill",
                    "scope": "global",
                    "changes": [{"key": alpha["key"], "on": False}],
                },
            )
    finally:
        for cleanup in rig.cleanups:
            await cleanup()
    assert json.loads((tmp_path / "agent" / "settings.json").read_text("utf-8"))["skills"] == [
        "-alpha"
    ]
    assert answer["written"] == [str(tmp_path / "agent" / "settings.json")]
    assert answer["changed"] == []
    assert answer["mismatches"] == ["alpha: saved, but this project's file keeps it on here"]


# --- policyPending (B30) ---------------------------------------------------------------


async def test_a_list_written_by_hand_is_policy_pending_until_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#795 (ADR-0304 D10): the agent kind applies LISTS; one the session has not taken yet."""

    _seed(tmp_path)
    rig = await _selection_rig(tmp_path, monkeypatch)
    try:
        assert (await _listed(rig, "agent"))["policyPending"] is False
        settings = tmp_path / "agent" / "settings.json"
        data = json.loads(settings.read_text("utf-8"))
        data["agents"] = ["-reviewer"]
        settings.write_text(json.dumps(data), encoding="utf-8")  # another writer, by hand
        assert (await _listed(rig, "agent"))["policyPending"] is True
    finally:
        for cleanup in rig.cleanups:
            await cleanup()


# --- liveNote (B31) --------------------------------------------------------------------


async def test_a_started_server_that_is_not_connected_carries_its_live_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    rig = await _selection_rig(tmp_path, monkeypatch)
    rig.controls["mcp"].kind.manager = SimpleNamespace(
        connections={
            "one": SimpleNamespace(name="one", transport="stdio", connected=True),
            "two": SimpleNamespace(name="two", transport="stdio", connected=False),
        },
        failed=frozenset({"two"}),
        disabled=(),
    )
    try:
        listed = await _listed(rig, "mcp")
    finally:
        for cleanup in rig.cleanups:
            await cleanup()
    one, two = _row(listed, "one"), _row(listed, "two")
    assert (one["live"], one["liveNote"]) == (True, None)
    assert two["live"] is False
    assert two["liveNote"] == ["failed", "did not connect at launch; it has no tools this session"]


# --- live-RPC 5: the extension kind's MCP status rows ------------------------------------


async def test_the_extension_kinds_mcp_rows_say_saved_and_applied_alike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """saved and applied both say whether this launch started the server, as the mcp kind's
    status rows do (ADR-0305 D16); the lock says whether it is connected now."""

    _seed(tmp_path)
    rig = await _selection_rig(tmp_path, monkeypatch)
    statuses = [
        McpServerStatus(name="marker", transport="stdio", state="connected"),
        McpServerStatus(name="urlsrv", transport="http", state="failed"),
        McpServerStatus(name="gone", transport="stdio", state="disconnected"),
        McpServerStatus(name="offsrv", transport="stdio", state="disabled"),
    ]
    rig.controls["extension"].kind.mcp_statuses = lambda: statuses
    try:
        listed = await _listed(rig, "extension")
    finally:
        for cleanup in rig.cleanups:
            await cleanup()
    rows = {r["label"]: r for r in listed["items"] if r["role"] == "info"}
    seen = {
        name: (r["saved"], r["applied"], r["pending"], r["lock"]["value"], r["lock"]["tag"])
        for name, r in rows.items()
    }
    assert seen == {
        "marker": (True, True, False, True, "connected"),
        "urlsrv": (True, True, False, False, "failed"),
        "gone": (True, True, False, False, "disconnected"),
        "offsrv": (False, False, False, False, "disabled"),
    }


# --- live-RPC 3: no identity-less row without a lock --------------------------------------


async def test_the_jobs_built_in_is_locked_and_no_identity_less_row_is_unlocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every live launch lists ``builtin:JobsExtension`` (#722): no settings identity, and at
    ``beadfac4`` no lock either. ADR-0305 D3: such a row carries a lock that says why."""

    from aelix_coding_agent.extensions import loader
    from aelix_coding_agent.jobs.extension import JobsExtension
    from aelix_coding_agent.jobs.service import JobService

    real = loader.discover_and_load_extensions

    async def _with_jobs(*args: Any, **kwargs: Any) -> Any:
        service = JobService(settings=lambda: None, warn=lambda _t: None)
        kwargs["prepend"] = [JobsExtension(service, tools=False), *(kwargs.get("prepend") or [])]
        return await real(*args, **kwargs)

    monkeypatch.setattr(loader, "discover_and_load_extensions", _with_jobs)
    _seed(tmp_path)
    rig = await _selection_rig(tmp_path, monkeypatch)
    try:
        async with _wire(rig) as wire:
            listed = await _call(wire, "list", "selections/list", {"kind": "extension"})
            jobs = _row(listed, "JobsExtension")
            wire.send(
                {
                    "type": "aelix_control",
                    "id": "set",
                    "method": "selections/set",
                    "params": {
                        "kind": "extension",
                        "scope": "global",
                        "changes": [{"key": jobs["key"], "on": False}],
                    },
                }
            )
            refused = await asyncio.wait_for(wire.response("set"), 30)
    finally:
        for cleanup in rig.cleanups:
            await cleanup()
    assert (jobs["role"], jobs["identities"]) == ("item", [])
    assert jobs["lock"] == {
        "value": True,
        "reason": "follows backgroundJobs.enabled (/settings)",
        "tag": "/settings",
        "kind": "always_on",
        "refusal": "",
    }
    assert jobs["refusal"] == "JobsExtension is locked: follows backgroundJobs.enabled (/settings)"
    assert refused["success"] is False and refused["error"] == jobs["refusal"]
    unlocked = [
        r["key"]
        for r in listed["items"]
        if r["role"] == "item" and not r["identities"] and r["lock"] is None
    ]
    assert unlocked == [], unlocked


# --- every text field in RPC words (B07; ADR-0305 D3's rule) --------------------------


def test_every_text_a_listing_or_a_save_sends_is_in_rpc_words() -> None:
    """D3: the TUI-only texts are replaced wherever they appear, ``problems`` included.

    No kind puts a trust text into ``Snapshot.problems`` today (the verify pass's B07
    survived for that reason), so a hand-built snapshot carries one in every text
    field the serializers send; the expected words are written out here.
    """

    from aelix_coding_agent.core.mcp_selection import changed_text
    from aelix_coding_agent.core.resource_selection import (
        UNTRUSTED_LOCK_REASON,
        UNTRUSTED_SCOPE_REASON,
        ItemState,
        Lock,
        ResourceItem,
        SaveResult,
        ScopeStatus,
        Snapshot,
    )
    from aelix_coding_agent.rpc.control_selection import serialize_save, serialize_snapshot

    tui = f"{UNTRUSTED_SCOPE_REASON} · {UNTRUSTED_LOCK_REASON} · {changed_text('x.json')}"
    rpc = (
        "this folder is not trusted · this folder is not trusted · the file aelix reads here "
        "changed (now x.json); nothing written; list again"
    )
    item = ResourceItem(kind="skill", key="k", label="k", identities=("k",))
    state = ItemState(item, True, True, None, Lock(True, tui, refusal=tui), problem=tui)
    snapshot = Snapshot(
        kind="skill",
        states=(state,),
        scopes={"global": ScopeStatus("global", "/g", False, tui)},
        problems=(tui,),
        notices=(tui,),
        unreadable=(tui,),
    )
    listed = serialize_snapshot(snapshot, "next_request")
    saved = serialize_save(SaveResult(True, None, (), (), (tui,)))
    (row,) = listed["items"]
    assert listed["problems"] == [rpc]
    assert listed["notices"] == [rpc]
    assert listed["unreadable"] == [rpc]
    assert listed["scopes"]["global"]["reason"] == rpc
    assert (row["lock"]["reason"], row["lock"]["refusal"], row["problem"]) == (rpc, rpc, rpc)
    assert saved["mismatches"] == [rpc]
    text = json.dumps([listed, saved], ensure_ascii=False)
    assert "/trust" not in text and "reopen /mcp" not in text
