"""#624 (ADR-0305 D3, D11–D17) — the ``mcp`` selection kind, at the selection level.

``McpSelection`` over real ``mcp.json`` files in ``tmp_path`` and a real
``SettingsManager`` (its live trust flag is what ``/trust`` moves). What is pinned:

* which scope is writable: the file in force only (no project file → global; a
  trusted project file → project; an untrusted one, or ``$AELIX_MCP_CONFIG`` →
  nothing), with ADR-0305 D12's words, and ``.aelix/mcp.json`` never created;
* an untrusted project's file is never opened (``open``/``os.open``/``read_*``
  fail on it), lists no server and warns nothing;
* trust revoked and the file in force changing are refused when the snapshot is
  taken and again inside the write's lock (D13), with the bytes unchanged;
* the rows (D3, D16): a skipped entry is a placeholder locked ``unavailable`` with
  the loader's warning, an extension's server is a status row, a server only the
  manager knows (an ACP lease) is no row; and every placeholder row the five kinds
  produce carries a lock or gets the placeholder refusal (opus L3);
* ``effect: restart`` (D15): nothing applied, the row ``pending``; ``aelix status
  --json`` flips the state with no new key (D17);
* ``SECRET-624`` in no row, problem, refusal or save error; a cancel tells the
  writer to stop.
"""

from __future__ import annotations

import asyncio
import builtins
import dataclasses
import io
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.core import mcp_selection
from aelix_coding_agent.core.mcp_selection import (
    NOT_APPLIED,
    UNTRUSTED_KEY,
    build_mcp_control,
    changed_text,
)
from aelix_coding_agent.core.mcp_servers import STATE_MEANINGS, McpNameRecord, config_label
from aelix_coding_agent.core.resource_selection import (
    ApplyResult,
    ItemState,
    commit,
    edit_refusal,
    scope_refusal,
)

from tests.env_sandbox import sandbox_home

S = "SECRET-624"
ONE_FILE = (
    "aelix reads one mcp.json: a .aelix/mcp.json here would replace {g} in this folder, so "
    "its other servers would stop here too; turning a global server off in one project "
    "waits for #644"
)

# Hand-formatted: two members on one line, a secret in env, a url query and a header.
GLOBAL_TEXT = (
    "{\n"
    '  "mcpServers": {\n'
    '    "docs": {"url": "https://mcp.example.test/mcp?key=SECRET-624", "headers": {"A": "SECRET-624"}},\n'
    '    "fs": {\n'
    '      "command": "npx", "args": ["-y", "SECRET-624"],\n'
    '      "env": {"TOKEN": "SECRET-624"}\n'
    "    },\n"
    '    "quiet": {"command": "q", "enabled": false},\n'
    '    "odd": {"command": "o", "enabled": "SECRET-624"}\n'
    "  }\n"
    "}\n"
)


class _Bench:
    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, trusted: bool = True
    ) -> None:
        sandbox_home(monkeypatch, tmp_path / "home")
        self.agent = tmp_path / "agent"
        self.work = tmp_path / "work"
        self.agent.mkdir(parents=True)
        self.work.mkdir()
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(self.agent))
        monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
        monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)
        self.global_file = self.agent / "mcp.json"
        self.project_file = self.work / ".aelix" / "mcp.json"
        self.manager = SettingsManager.create(self.work, self.agent, project_trusted=trusted)
        self.trusted = trusted
        self.connections: dict[str, Any] = {}
        self.disabled: list[Any] = []
        self.owners: dict[str, Any] = {}

    def control(self, *, inherited: Any = None) -> Any:
        manager = SimpleNamespace(
            connections=self.connections, failed=frozenset(), disabled=tuple(self.disabled)
        )
        return build_mcp_control(
            cwd=str(self.work),
            agent_dir=str(self.agent),
            project_trusted=self.trusted,
            settings_manager=self.manager,
            mcp_manager=manager,
            launch_record=SimpleNamespace(owners=self.owners),
            inherited=inherited,
        )

    def connect(self, name: str, *, connected: bool = True, transport: str = "stdio") -> None:
        self.connections[name] = SimpleNamespace(
            name=name, transport=transport, connected=connected
        )

    def write_project(self, text: str) -> None:
        self.project_file.parent.mkdir(exist_ok=True)
        self.project_file.write_text(text, encoding="utf-8")

    @property
    def global_label(self) -> str:
        return config_label(self.global_file, "global")


def _rows(snapshot: Any) -> dict[str, ItemState]:
    return {s.item.key: s for s in snapshot.states}


def _no_open(path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any open or read of *path* is recorded and fails; ``is_file``/``stat`` are allowed."""

    seen: list[str] = []
    target = os.path.realpath(path)

    def _hit(file: Any) -> bool:
        if not isinstance(file, (str, bytes, os.PathLike)):
            return False
        return os.path.realpath(os.fsdecode(file)) == target

    real_open, real_os_open = builtins.open, os.open
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def _open(file: Any, *a: Any, **k: Any) -> Any:
        if _hit(file):
            seen.append("open")
            raise AssertionError("opened the untrusted project file")
        return real_open(file, *a, **k)

    def _os_open(file: Any, *a: Any, **k: Any) -> int:
        if _hit(file):
            seen.append("os.open")
            raise AssertionError("opened the untrusted project file")
        return real_os_open(file, *a, **k)

    def _read_text(self: Path, *a: Any, **k: Any) -> str:
        if _hit(self):
            seen.append("read_text")
            raise AssertionError("read the untrusted project file")
        return real_text(self, *a, **k)

    def _read_bytes(self: Path) -> bytes:
        if _hit(self):
            seen.append("read_bytes")
            raise AssertionError("read the untrusted project file")
        return real_bytes(self)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(io, "open", _open)
    monkeypatch.setattr(os, "open", _os_open)
    monkeypatch.setattr(Path, "read_text", _read_text)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    return seen


# --- which file, which scope (D12) ---------------------------------------------------


async def test_no_project_file_writes_the_global_file_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.connect("docs")
    bench.connect("fs")
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    assert snap.kind == "mcp"
    assert snap.scopes["global"].writable and snap.scopes["global"].exists
    project = snap.scopes["project"]
    assert not project.writable and not project.exists
    assert project.reason == ONE_FILE.format(g=bench.global_label)
    assert scope_refusal(snap, "project") == (
        f"Cannot save to This project: {ONE_FILE.format(g=bench.global_label)}"
    )
    # A save named for the project is refused, and creates nothing.
    refused = await control.selection.save(control.kind, "project", {"docs": False})
    assert not refused.ok
    assert not (bench.work / ".aelix").exists()
    from aelix_coding_agent.cli.config import load_mcp_config

    loaded = load_mcp_config(str(bench.work))
    saved = await control.selection.save(control.kind, "global", {"docs": False})
    assert saved.ok and saved.written == (str(bench.global_file),)
    # The loader agrees (§3.7): docs moves to the disabled list, nothing else changes.
    after = load_mcp_config(str(bench.work))
    assert [c.name for c in after.disabled] == ["docs", "quiet"]
    assert [c.name for c in after.contribs] == [c.name for c in loaded.contribs if c.name != "docs"]
    assert after.warnings == loaded.warnings
    assert saved.changed == (("docs", False),)
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT.replace(
        '"headers": {"A": "SECRET-624"}}', '"headers": {"A": "SECRET-624"}, "enabled": false}'
    )
    assert not (bench.work / ".aelix").exists(), ".aelix/mcp.json is never created"
    assert control.selection.needs_new_project_file("project", {"docs": False}) is False


async def test_no_file_at_all_says_the_global_file_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    assert snap.states == () and snap.problems == ()
    assert not snap.scopes["global"].writable
    assert snap.scopes["global"].reason == f"{bench.global_label} does not exist"
    refused = await control.selection.save(control.kind, "global", {"x": False})
    assert not refused.ok and not bench.global_file.exists()


async def test_a_trusted_project_file_writes_the_project_file_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.write_project('{"mcpServers": {"local": {"command": "l", "args": []}}}\n')
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    assert list(_rows(snap)) == ["local"]
    assert _rows(snap)["local"].decided_by == "project"
    assert snap.scopes["project"].writable
    assert snap.scopes["global"].reason == (
        f"aelix reads .aelix/mcp.json in this folder, not {bench.global_label}"
    )
    result = await control.selection.save(control.kind, "project", {"local": False})
    assert result.ok and result.written == (str(bench.project_file),)
    assert bench.project_file.read_text(encoding="utf-8") == (
        '{"mcpServers": {"local": {"command": "l", "args": [], "enabled": false}}}\n'
    )
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT


async def test_an_untrusted_project_file_is_never_opened_and_lists_no_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch, trusted=False)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.write_project('{"mcpServers": {"evil": {"command": "SECRET-624", "enabled": 7}}}')
    before = bench.project_file.read_bytes()
    bench.owners["ext-srv"] = SimpleNamespace(plugin="pack", started=False)
    seen = _no_open(bench.project_file, monkeypatch)
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    rows = _rows(snap)
    assert list(rows) == [UNTRUSTED_KEY, "extension:ext-srv"]
    holder = rows[UNTRUSTED_KEY]
    assert holder.item.role == "placeholder"
    assert holder.lock is not None and (holder.lock.kind, holder.lock.tag) == (
        "untrusted",
        "untrusted",
    )
    assert holder.lock.reason == "this folder is not trusted; /trust to decide"
    assert snap.problems == () and snap.unreadable == ()
    assert not snap.scopes["global"].writable and not snap.scopes["project"].writable
    assert snap.scopes["project"].reason == "this folder is not trusted (/trust to decide)"
    result = await control.selection.save(control.kind, "project", {"evil": False})
    assert not result.ok
    assert seen == [], seen
    monkeypatch.undo()
    assert bench.project_file.read_bytes() == before


async def test_trust_revoked_mid_session_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    text = '{"mcpServers": {"local": {"command": "l"}}}'
    bench.write_project(text)
    control = bench.control()
    assert control.selection.snapshot(control.kind).scopes["project"].writable
    bench.manager.set_project_trusted(False)
    snap = control.selection.snapshot(control.kind)
    assert not snap.scopes["project"].writable
    assert list(_rows(snap)) == [UNTRUSTED_KEY]
    result = await control.selection.save(control.kind, "project", {"local": False})
    assert not result.ok and "not trusted" in (result.error or "")
    assert bench.project_file.read_text(encoding="utf-8") == text


async def test_trust_revoked_while_the_write_waits_is_refused_inside_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    text = '{"mcpServers": {"local": {"command": "l"}}}'
    bench.write_project(text)
    control = bench.control()
    real = mcp_selection.write_enabled

    def _revoke_then_write(*a: Any, **k: Any) -> Any:
        bench.manager.set_project_trusted(False)  # /trust off while the save waited
        return real(*a, **k)

    monkeypatch.setattr(mcp_selection, "write_enabled", _revoke_then_write)
    result = await control.selection.save(control.kind, "project", {"local": False})
    assert result.error == "this folder is not trusted (/trust to decide); nothing written"
    assert bench.project_file.read_text(encoding="utf-8") == text


async def test_the_file_in_force_changing_under_an_open_snapshot_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """opus M3: a checkout brings a trusted ``.aelix/mcp.json`` under an open panel."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    assert snap.scopes["global"].writable  # the target the panel opened with
    project_text = '{"mcpServers": {"docs": {"command": "d"}}}'
    bench.write_project(project_text)
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert result == dataclasses.replace(result, ok=False)
    assert result.error == changed_text(".aelix/mcp.json")
    assert result.error == (
        "the file aelix reads here changed (now .aelix/mcp.json); nothing written; reopen /mcp"
    )
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT
    assert bench.project_file.read_text(encoding="utf-8") == project_text


async def test_the_file_in_force_changing_while_the_write_waits_is_refused_inside_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    real = mcp_selection.write_enabled
    project_text = '{"mcpServers": {"docs": {"command": "d"}}}'

    def _arrive_then_write(*a: Any, **k: Any) -> Any:
        bench.write_project(project_text)  # a checkout lands while the save waits
        return real(*a, **k)

    monkeypatch.setattr(mcp_selection, "write_enabled", _arrive_then_write)
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert result.error == changed_text(".aelix/mcp.json")
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT
    assert bench.project_file.read_text(encoding="utf-8") == project_text


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
async def test_a_project_file_linked_to_the_global_file_is_still_another_file_in_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D13: (source, real path) — the same real file reached as the project's is refused."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    real = mcp_selection.write_enabled

    def _link_then_write(*a: Any, **k: Any) -> Any:
        bench.project_file.parent.mkdir()
        bench.project_file.symlink_to(bench.global_file)
        return real(*a, **k)

    monkeypatch.setattr(mcp_selection, "write_enabled", _link_then_write)
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert result.error == changed_text(".aelix/mcp.json")
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT


async def test_another_global_file_in_force_is_refused_inside_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D13: the same source, another real path (the agent dir moved under the save)."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    other = tmp_path / "other-agent"
    other.mkdir()
    (other / "mcp.json").write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    real = mcp_selection.write_enabled

    def _move_then_write(*a: Any, **k: Any) -> Any:
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(other))
        return real(*a, **k)

    monkeypatch.setattr(mcp_selection, "write_enabled", _move_then_write)
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert result.error == changed_text(config_label(other / "mcp.json", "global"))
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT
    assert (other / "mcp.json").read_text(encoding="utf-8") == GLOBAL_TEXT


async def test_aelix_mcp_config_locks_every_row_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    env_file = tmp_path / "chosen.json"
    env_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(env_file))
    bench.connect("docs")
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    reason = f"$AELIX_MCP_CONFIG chooses the file this run; edit {env_file} by hand"
    for key in ("docs", "fs", "quiet"):
        lock = _rows(snap)[key].lock
        assert lock is not None
        assert (lock.kind, lock.tag, lock.reason) == ("cli", "$AELIX_MCP_CONFIG", reason)
    assert _rows(snap)["docs"].lock.value is True  # type: ignore[union-attr]
    assert _rows(snap)["fs"].lock.value is False  # type: ignore[union-attr]
    for scope in ("global", "project"):
        assert not snap.scopes[scope].writable
        assert snap.scopes[scope].reason == reason
    assert edit_refusal(_rows(snap)["docs"]) == f"docs is locked: {reason}"
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert not result.ok
    assert env_file.read_text(encoding="utf-8") == GLOBAL_TEXT
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT


# --- the rows (D3, D16) ---------------------------------------------------------------


def test_the_rows_say_name_transport_state_and_file_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.connect("docs", transport="http")
    bench.connect("fs", connected=False)
    bench.connect("pack-srv")
    bench.connect("lease")  # an ACP client's server: the manager knows it, the file does not
    bench.owners["pack-srv"] = SimpleNamespace(plugin="Weather Pack", started=True)
    bench.owners["idle-srv"] = SimpleNamespace(plugin="Idle Pack", started=False)
    bench.owners["docs"] = SimpleNamespace(plugin="Shadow Pack", started=False)  # the file wins
    bench.disabled.append(
        SimpleNamespace(contrib=SimpleNamespace(name="quiet", transport="stdio"), config="x")
    )
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    rows = _rows(snap)
    assert list(rows) == ["docs", "fs", "quiet", "odd", "extension:pack-srv", "extension:idle-srv"]
    docs, fs, quiet, odd = rows["docs"], rows["fs"], rows["quiet"], rows["odd"]
    assert (docs.item.description, docs.saved, docs.applied, docs.live) == (
        "http",
        True,
        True,
        True,
    )
    assert docs.live_note is None, "a connected server has no 'not in use' words"
    assert (fs.applied, fs.live, fs.live_note) == (
        True,
        False,
        ("disconnected", STATE_MEANINGS["disconnected"]),
    )
    assert (quiet.saved, quiet.applied, quiet.lock) == (False, False, None)
    assert docs.item.location == bench.global_label
    # The skipped entry: a placeholder locked unavailable, the loader's warning its problem.
    assert odd.item.role == "placeholder" and odd.item.identities == ()
    assert odd.lock is not None and (odd.lock.kind, odd.lock.tag) == ("unavailable", "skipped")
    assert (
        odd.problem == "server 'odd': \"enabled\" must be true or false, not a string; not started"
    )
    assert snap.problems == (f"{bench.global_file}: {odd.problem}",)
    # An extension's server: status only.
    pack = rows["extension:pack-srv"]
    assert pack.item.role == "info" and pack.lock is not None and pack.lock.kind == "status"
    assert pack.lock.value is True and pack.lock.tag == "connected"
    assert (pack.saved, pack.applied, pack.pending) == (True, True, False)
    assert edit_refusal(pack) == (
        "pack-srv is declared by Weather Pack · /extension turns the plugin on and off"
    )
    idle = rows["extension:idle-srv"]
    assert idle.lock is not None and (idle.lock.value, idle.lock.tag) == (False, "not started")
    assert (idle.saved, idle.applied) == (False, False)
    assert "lease" not in {s.item.label for s in snap.states}
    assert control.kind.applied() == {"docs", "fs", "lease", "extension:pack-srv"}


def test_a_name_the_parent_keeps_off_is_locked_for_this_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control(inherited=McpNameRecord(off=frozenset({"docs"})))
    lock = _rows(control.selection.snapshot(control.kind))["docs"].lock
    assert lock is not None and (lock.value, lock.kind, lock.tag) == (False, "cli", "inherited")
    assert "AELIX_MCP_PARENT_NAMES" in lock.reason


def test_an_unreadable_file_is_named_and_nothing_is_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_bytes(b'{"mcpServers": {"a": "\xffSECRET-624"}}')
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    want = f"{bench.global_file}: not valid JSON (UnicodeDecodeError)"
    assert snap.unreadable == (want,) and snap.problems == (want,)
    assert not snap.scopes["global"].writable
    assert snap.scopes["global"].reason == (
        f"{bench.global_label} did not load: not valid JSON (UnicodeDecodeError)"
    )


# --- effect: restart (D15) and aelix status (D17) ---------------------------------------


async def test_a_restart_kind_is_saved_never_applied_and_left_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.connect("docs")
    control = bench.control()
    assert control.kind.effect == "restart" and control.kind.files_word == "mcp.json"
    save, applied = await commit(control.selection, control.kind, "global", {"docs": False})
    assert save is not None and save.ok and applied is None
    docs = _rows(control.selection.snapshot(control.kind))["docs"]
    assert (docs.saved, docs.applied, docs.pending) == (False, True, True)
    assert await control.selection.apply(control.kind) == ApplyResult(False, warning=NOT_APPLIED)
    assert await control.kind.apply(set()) == ApplyResult(False, warning=NOT_APPLIED)
    assert control.kind.busy() is False


async def test_aelix_status_json_flips_the_state_and_gains_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aelix_coding_agent.cli.status import run_status_command

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    monkeypatch.chdir(bench.work)
    assert await run_status_command(["--no-extensions", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    control = bench.control()
    assert (await control.selection.save(control.kind, "global", {"docs": False})).ok
    assert await run_status_command(["--no-extensions", "--json"]) == 0
    after = json.loads(capsys.readouterr().out)

    def _paths(value: Any, at: str = "") -> set[str]:
        if isinstance(value, dict):
            return {p for k, v in value.items() for p in _paths(v, f"{at}.{k}")} | {at}
        if isinstance(value, list):
            return {p for v in value for p in _paths(v, f"{at}[]")} | {at}
        return {at}

    assert _paths(after) == _paths(before)
    states = {s["name"]: s["state"] for s in after["mcp"]["servers"]}
    assert {s["name"]: s["state"] for s in before["mcp"]["servers"]}["docs"] == "enabled"
    assert states["docs"] == "disabled"


# --- nothing printed (D14) ---------------------------------------------------------------


async def test_no_secret_in_any_row_problem_refusal_or_save_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.connect("docs")
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    texts = [json.dumps(dataclasses.asdict(snap), default=str)]
    texts += [edit_refusal(s) or "" for s in snap.states]
    texts += [scope_refusal(snap, scope) or "" for scope in ("global", "project")]
    # A refused write: a duplicate key makes the writer refuse (never quoting a value).
    dup = GLOBAL_TEXT.replace('"command": "q"', '"command": "q", "command": "SECRET-624"')
    bench.global_file.write_text(dup, encoding="utf-8")
    refused = await control.selection.save(control.kind, "global", {"quiet": True})
    assert not refused.ok and refused.error
    texts.append(refused.error)
    texts.append(
        (await control.selection.save(control.kind, "project", {"docs": False})).error or ""
    )
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(bench.global_file))
    env_snap = control.selection.snapshot(control.kind)
    texts.append(json.dumps(dataclasses.asdict(env_snap), default=str))
    texts.append(
        (await control.selection.save(control.kind, "global", {"docs": False})).error or ""
    )
    assert all(S not in t for t in texts), [t for t in texts if S in t]
    assert bench.global_file.read_text(encoding="utf-8") == dup


# --- a cancel tells the writer to stop ---------------------------------------------------


async def test_cancelling_the_save_sets_the_writers_abandoned_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    entered = threading.Event()
    seen: dict[str, Any] = {}

    def _waiting_write(
        target: Any, changes: Any, *, authorize: Any, abandoned: Any, **_kw: Any
    ) -> Any:
        entered.set()
        seen["abandoned"] = abandoned.wait(10)
        return SimpleNamespace(status="abandoned", detail="")

    monkeypatch.setattr(mcp_selection, "write_enabled", _waiting_write)
    task = asyncio.ensure_future(control.selection.save(control.kind, "global", {"docs": False}))
    while not entered.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(500):
        if "abandoned" in seen:
            break
        await asyncio.sleep(0.01)
    assert seen == {"abandoned": True}
    assert bench.global_file.read_text(encoding="utf-8") == GLOBAL_TEXT


async def test_the_writer_is_called_with_the_file_and_the_changes_in_edit_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    calls: list[Any] = []

    def _record(target: Any, changes: Any, *, authorize: Any, abandoned: Any, **_kw: Any) -> Any:
        calls.append((Path(target), list(changes), authorize(os.path.realpath(target))))
        return SimpleNamespace(status="unchanged", detail="")

    monkeypatch.setattr(mcp_selection, "write_enabled", _record)
    result = await control.selection.save(
        control.kind, "global", {"quiet": True, "docs": False, "fs": True, "odd": False}
    )
    # fs is already on (a no-op, dropped); odd is a placeholder (dropped).
    assert calls == [(bench.global_file, [("quiet", True), ("docs", False)], None)]
    assert result.ok and result.written == ()
    # Only no-ops: the writer is not called at all (no lock, no read).
    nothing = await control.selection.save(control.kind, "global", {"fs": True})
    assert nothing.ok and nothing.written == () and nothing.changed == ()
    assert len(calls) == 1


# --- every placeholder row carries a lock or the placeholder refusal (opus L3) ----------


def _kind_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[ItemState]]:
    """The rows each of the five kinds produces in a rig that has its placeholders."""

    from aelix_coding_agent.core.extension_selection import (
        ExtensionInventory,
        ExtensionKind,
        RefusedRow,
    )
    from aelix_coding_agent.core.resource_selection import Lock, ResourceItem, ResourceSelection
    from aelix_coding_agent.core.tool_selection import ToolKind

    from tests.core.test_resource_selection_624 import _bench as skill_bench
    from tests.core.test_tool_selection_510 import _live_harness
    from tests.tui.test_agents_panel_795 import _Scene

    out: dict[str, list[ItemState]] = {}
    # skill: an untrusted folder's summary row.
    (tmp_path / "skill").mkdir()
    selection, kind, _m, _a, work = skill_bench(tmp_path / "skill")
    (work / ".aelix" / "skills" / "x").mkdir(parents=True)
    (work / ".aelix" / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n")
    kind._project_trusted = False  # type: ignore[attr-defined]  # noqa: SLF001
    out["skill"] = list(selection.snapshot(kind).states)
    # tool: a disabled and a failed server, a failed extension, an extension turned off.
    from aelix_agent_core.harness.tool_policy import ToolPolicy  # noqa: PLC0415

    statuses = [
        SimpleNamespace(name="gh", state="disabled", config="~/.aelix/agent/mcp.json"),
        SimpleNamespace(name="dead", state="failed", config=None),
    ]
    errors = [SimpleNamespace(path="/u/.aelix/agent/extensions/broken.py", error="boom")]
    extra = [
        (
            ResourceItem(kind="tool", key="ext-off:w", label="w", role="placeholder"),
            Lock(False, "see /extension · off in settings", tag="ext off", kind="unavailable"),
        )
    ]
    tool = ToolKind(
        harness=lambda: _live_harness(ToolPolicy()),
        builtin=frozenset({"read", "bash", "edit"}),
        mcp_statuses=lambda: statuses,
        extension_errors=lambda: errors,
        extra_rows=lambda: extra,
    )
    tool_selection = ResourceSelection(None, global_base="", project_base="", project_trusted=True)
    out["tool"] = list(tool_selection.snapshot(tool).states)
    # extension: an untrusted folder, a refused carrier, MCP status rows.
    ext_dir = tmp_path / "ext" / ".aelix" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "a.py").write_text("raise SystemExit('read')\n")
    inventory = ExtensionInventory(refused=(RefusedRow("refused:x", "x.toml", "bad manifest"),))
    extension = ExtensionKind(
        inventory=lambda: inventory,
        mcp_statuses=lambda: [SimpleNamespace(name="srv", state="connected", transport="stdio")],
        cwd=str(tmp_path / "ext"),
        project_trusted=False,
    )
    out["extension"] = list(tool_selection.snapshot(extension).states)
    # agent: a profile file that did not load, and an untrusted folder.
    scene = _Scene(tmp_path / "agent-scene", monkeypatch, trusted=False)
    out["agent"] = list(scene.selection.snapshot(scene.kind).states)
    # mcp: a skipped entry, an extension's server, and (below) an untrusted project file.
    bench = _Bench(tmp_path / "mcp", monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.owners["pack-srv"] = SimpleNamespace(plugin="pack", started=False)
    control = bench.control()
    rows = list(control.selection.snapshot(control.kind).states)
    bench.write_project("{}")
    bench.manager.set_project_trusted(False)
    rows += list(control.selection.snapshot(control.kind).states)
    out["mcp"] = rows
    return out


def test_every_placeholder_row_carries_a_lock_or_the_placeholder_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    by_kind = _kind_rows(tmp_path, monkeypatch)
    seen_roles: set[tuple[str, str]] = set()
    for kind_name, states in by_kind.items():
        odd = [s for s in states if s.item.role != "item" or not s.item.identities]
        assert odd, f"anti-vacuity: the {kind_name} rig has no placeholder row"
        for state in odd:
            seen_roles.add((kind_name, state.item.role))
            assert edit_refusal(state) is not None, (kind_name, state.item.key)
            # At a1606f58 every such row carries a lock (ADR-0305 D3): the
            # placeholder rule is a backstop, not a text any kind shows today.
            assert state.lock is not None, (kind_name, state.item.key)
    assert {("tool", "placeholder"), ("extension", "info"), ("agent", "placeholder")} <= seen_roles
    assert {("mcp", "placeholder"), ("mcp", "info"), ("skill", "item")} <= seen_roles


# --- fix-r1: the file the snapshot read is the file written (Codex CX-3) ----------------------


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
async def test_a_retarget_before_the_writer_starts_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mcp.json`` links to A when the save reads it and to B before the worker resolves it.

    The real path the save read is kept with its snapshot and compared before the
    writer locks anything: neither A nor B is written.
    """

    bench = _Bench(tmp_path, monkeypatch)
    a, b = bench.agent / "a.json", bench.agent / "b.json"
    a.write_text(GLOBAL_TEXT, encoding="utf-8")
    b.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.global_file.symlink_to(a)
    control = bench.control()
    real = mcp_selection.write_enabled
    seen: list[Any] = []

    def _retarget_then_write(*args: Any, **kwargs: Any) -> Any:
        bench.global_file.unlink()
        bench.global_file.symlink_to(b)  # before the worker's own realpath
        seen.append(kwargs.get("expected_real"))
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp_selection, "write_enabled", _retarget_then_write)
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert not result.ok
    assert result.error == "mcp.json now resolves to another file; nothing written; try again"
    assert seen == [os.path.realpath(a)]
    assert a.read_text(encoding="utf-8") == GLOBAL_TEXT
    assert b.read_text(encoding="utf-8") == GLOBAL_TEXT


@pytest.mark.skipif(sys.platform == "win32", reason="a symlink needs a privilege on Windows")
async def test_a_retarget_after_the_writer_resolves_says_the_file_resolves_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mcp.json`` links to A when the writer resolves it and to B before it locks.

    The path is the same, so the refusal says what changed, as the check before the
    writer does: it now resolves to another file, not "the file aelix reads here
    changed (now <the same path>)" (verify r2, N6). Neither A nor B is written.
    """

    from aelix_coding_agent.core import mcp_config_edit

    bench = _Bench(tmp_path, monkeypatch)
    a, b = bench.agent / "a.json", bench.agent / "b.json"
    a.write_text(GLOBAL_TEXT, encoding="utf-8")
    b.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.global_file.symlink_to(a)
    control = bench.control()
    real_lock = mcp_config_edit._platform_lock
    locked: list[str] = []

    def _retarget_then_lock(real: str, *args: Any, **kwargs: Any) -> Any:
        bench.global_file.unlink()
        bench.global_file.symlink_to(b)  # after the worker's realpath, before its lock
        locked.append(real)
        return real_lock(real, *args, **kwargs)

    monkeypatch.setattr(mcp_config_edit, "_platform_lock", _retarget_then_lock)
    result = await control.selection.save(control.kind, "global", {"docs": False})
    assert locked == [os.path.realpath(a)]
    assert not result.ok
    assert result.error == "mcp.json now resolves to another file; nothing written; try again"
    assert a.read_text(encoding="utf-8") == GLOBAL_TEXT
    assert b.read_text(encoding="utf-8") == GLOBAL_TEXT


async def test_the_authorizer_refuses_a_lock_taken_on_another_real_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside the lock: the real path the writer locked must be the one the save read."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    control = bench.control()
    calls: list[Any] = []

    def _record(target: Any, changes: Any, *, authorize: Any, **kwargs: Any) -> Any:
        calls.append(
            (
                kwargs["expected_real"],
                authorize(os.path.realpath(target)),
                authorize(str(tmp_path / "elsewhere.json")),
            )
        )
        return SimpleNamespace(status="unchanged", detail="")

    monkeypatch.setattr(mcp_selection, "write_enabled", _record)
    await control.selection.save(control.kind, "global", {"docs": False})
    expected = os.path.realpath(bench.global_file)
    assert calls == [
        (
            expected,
            None,
            "mcp.json now resolves to another file; nothing written; try again",
        )
    ]


# --- fix-r1: row keys cannot collide (Codex CX-11) ----------------------------------------------

PREFIXED = {
    "mcpServers": {
        "extension:foo": {"command": "a"},
        "file:bar": {"command": "b"},
        "untrusted:project": {"command": "c"},
        "plain": {"command": "d"},
        "extension:off": {"command": "e", "enabled": False},
    }
}


async def test_a_file_server_named_like_another_namespace_hides_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file entry literally named ``extension:foo`` and an extension's ``foo`` both list."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(json.dumps(PREFIXED, indent=2) + "\n", encoding="utf-8")
    bench.connect("extension:foo")  # the file's entry started
    bench.connect("foo")  # the extension's server started
    bench.connect("off")  # an extension's server named like the file's disabled entry's tail
    bench.connect("file:bar", connected=False)  # started, not connected: a live note
    bench.owners["foo"] = SimpleNamespace(plugin="Pack", started=True)
    bench.owners["off"] = SimpleNamespace(plugin="Pack", started=True)
    control = bench.control()
    snap = control.selection.snapshot(control.kind)
    rows = _rows(snap)
    assert list(rows) == [
        "file:extension:foo",
        "file:file:bar",
        "file:untrusted:project",
        "plain",
        "file:extension:off",
        "extension:foo",
        "extension:off",
    ]
    for key, name in (
        ("file:extension:foo", "extension:foo"),
        ("file:file:bar", "file:bar"),
        ("file:untrusted:project", "untrusted:project"),
        ("plain", "plain"),
    ):
        assert (rows[key].item.label, rows[key].item.identities, rows[key].item.role) == (
            name,
            (name,),
            "item",
        )
    assert rows["extension:foo"].item.role == "info" and rows["extension:foo"].item.label == "foo"
    # applied and live are per row: the extension's started "off" does not make the
    # file's disabled "extension:off" look started.
    assert (
        rows["file:extension:off"].saved,
        rows["file:extension:off"].applied,
        rows["file:extension:off"].live,
    ) == (False, False, False)
    assert (rows["file:extension:foo"].applied, rows["file:extension:foo"].live) == (True, True)
    assert rows["file:file:bar"].live_note == ("disconnected", STATE_MEANINGS["disconnected"])
    assert rows["file:extension:foo"].live_note is None
    assert control.kind.applied() == {
        "file:extension:foo",
        "file:file:bar",
        "extension:foo",
        "extension:off",
    }
    result = await control.selection.save(
        control.kind, "global", {"file:extension:foo": False, "file:untrusted:project": False}
    )
    assert result.ok and result.written == (str(bench.global_file),)
    saved = json.loads(bench.global_file.read_text(encoding="utf-8"))["mcpServers"]
    assert saved["extension:foo"]["enabled"] is False
    assert saved["untrusted:project"]["enabled"] is False
    assert "enabled" not in saved["plain"] and "enabled" not in saved["file:bar"]


def _prefixed_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Bench:
    """``PREFIXED`` in force, with the two started servers whose names collide with a
    file row's name: the file's ``extension:foo`` connected (no extension ``foo``), and an
    extension's ``off`` connected beside the file's disabled ``extension:off``."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(json.dumps(PREFIXED, indent=2) + "\n", encoding="utf-8")
    bench.connect("extension:foo")  # the file's entry started; no extension "foo"
    bench.connect("off")  # an extension's server, named like the file's disabled entry's tail
    bench.owners["off"] = SimpleNamespace(plugin="Pack", started=True)
    return bench


def test_live_is_matched_by_row_key_not_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both ways: a connected extension ``off`` does not make the file's ``extension:off``
    live, and the file's own connected ``extension:foo`` is live with no extension ``foo``
    (the verify pass's mutant S12, ``live=name in live``, flips both)."""

    bench = _prefixed_bench(tmp_path, monkeypatch)
    control = bench.control()
    rows = _rows(control.selection.snapshot(control.kind))
    assert control.kind.live() == {"file:extension:foo", "extension:off"}
    assert rows["file:extension:off"].live is False
    assert rows["file:extension:foo"].live is True
    assert rows["plain"].live is False


def test_the_aelix_mcp_config_lock_is_matched_by_row_key_not_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under ``$AELIX_MCP_CONFIG`` a row's lock holds what this launch started for that
    row: the file's started ``extension:foo`` locked on, its ``extension:off`` locked off
    although an extension's ``off`` started (the verify pass's mutant S11, ``name in
    applied``, flips both)."""

    bench = _prefixed_bench(tmp_path, monkeypatch)
    env_file = tmp_path / "chosen.json"
    env_file.write_bytes(bench.global_file.read_bytes())
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(env_file))
    control = bench.control()
    rows = _rows(control.selection.snapshot(control.kind))
    assert control.kind.applied() == {"file:extension:foo", "extension:off"}
    locks = {key: rows[key].lock for key in ("file:extension:foo", "file:extension:off", "plain")}
    assert {key: (lock.tag, lock.value) for key, lock in locks.items() if lock} == {
        "file:extension:foo": ("$AELIX_MCP_CONFIG", True),
        "file:extension:off": ("$AELIX_MCP_CONFIG", False),
        "plain": ("$AELIX_MCP_CONFIG", False),
    }


def test_an_extension_server_named_like_a_file_entry_is_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader's rule (``mcp_launch``, #512): a name ``mcp.json`` declares is the file's,
    so an extension's server of that name has no status row; a key never decides it."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(json.dumps(PREFIXED, indent=2) + "\n", encoding="utf-8")
    bench.owners["plain"] = SimpleNamespace(plugin="Pack", started=False)
    bench.owners["extension:foo"] = SimpleNamespace(plugin="Pack", started=False)
    control = bench.control()
    keys = list(_rows(control.selection.snapshot(control.kind)))
    assert "extension:plain" not in keys and "extension:extension:foo" not in keys


def test_a_skipped_entry_named_with_a_reserved_prefix_keeps_its_own_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A placeholder for an invalid entry is keyed like any file entry: no clash with an
    extension's status row of the name after the prefix."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(
        json.dumps({"mcpServers": {"extension:odd": {"command": "o", "enabled": "x"}}}) + "\n",
        encoding="utf-8",
    )
    bench.connect("odd")
    bench.owners["odd"] = SimpleNamespace(plugin="Pack", started=True)
    control = bench.control()
    rows = _rows(control.selection.snapshot(control.kind))
    assert list(rows) == ["file:extension:odd", "extension:odd"]
    assert rows["file:extension:odd"].item.role == "placeholder"
    assert rows["extension:odd"].item.role == "info"
