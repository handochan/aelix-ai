"""#25 review round 1 — what Codex (R01–R09) and the independent verify (V1–V12) found.

Each test fails on ``3ce3d461``:

* V1: a pack saved on that failed to load leaves the server it declared in use,
  and every surface says so — "hidden" comes only from the hiding a build applied;
* R01: an installed pack the directory tier found first keeps its entry-point
  and distribution names; R02: a ``pyproject.toml`` package is named by its directory;
* R03: the pack whose contribution the MCP manager kept owns the server; R07:
  hiding a server removes exactly its own wrappers;
* R04: MCP on → off → on leaves the tools active again, under the tool policy;
* R06: a refused manifest reaches ``/reload``'s line; R08: a new shortcut key on an
  extension that was already on does too;
* V2: a name two extensions share is written as a form only the edited row answers to;
* V3: path entries match under a symlinked agent directory;
* V4, V8, R09: the ``--no-extensions`` notice, built-in entries, one row per ``-e`` file;
* V5: a delegated child inherits what its parent kept off, to suppress only.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_coding_agent.core.extension_selection import (
    EXTENSIONS_PARENT_OFF_ENV,
    ExtensionInventory,
    ExtensionKind,
    ExtensionOffRecord,
    build_inventory,
    describe_record,
    display_labels,
    extension_filter,
    failure_lines,
    list_warnings,
    listing_inventory,
    mcp_launch,
    pass_extension_off_to_child,
    reload_lines,
    set_launch_extension_off,
    without_hidden,
)
from aelix_coding_agent.core.resource_selection import ResourceSelection
from aelix_coding_agent.extensions import loader

from tests.env_sandbox import sandbox_home
from tests.extensions.test_extension_selection_25 import SENTINEL_BODY, _lists, _pack


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sandbox_home(monkeypatch, tmp_path / "home")
    return tmp_path


async def _discover(root: Path, agent: Path, entries: list[str], **kw: Any) -> Any:
    return await loader.discover_and_load_extensions(
        [],
        cwd=root,
        agent_dir=agent,
        entry_filter=extension_filter(_lists(entries, global_base=str(agent)), phase="P"),
        **kw,
    )


# --- V1 / R05: a failed pack's MCP server is in use, and is said to be -----------------


async def test_a_failed_packs_server_is_never_called_hidden(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = home / "agent"
    site = home / "site"
    site.mkdir()
    pkg = _pack(agent / "extensions", "failpack", entry="failmcp_25:setup", mcp="shared")
    module = site / "failmcp_25.py"
    module.write_text("def setup(aelix):\n    pass\n")
    monkeypatch.syspath_prepend(str(site))
    records: list[Any] = []
    manifests = loader.scan_extension_manifests([], cwd=home, agent_dir=agent, records=records)
    contribs = [c for m in manifests for c in m.contributes.mcp_servers]
    run = SimpleNamespace(name="shared__run")
    launch = mcp_launch(records, contribs, set(), {"shared": (run,)})
    before = build_inventory(await _discover(home, agent, []))
    sys.modules.pop("failmcp_25", None)
    module.write_text("def setup(aelix):\n    raise RuntimeError('broken setup')\n")
    loaded = await _discover(home, agent, [])
    # The settings say on, so the factory hides nothing: the tools stay in use.
    hidden = launch.hidden(_lists([], global_base=str(agent)))
    assert hidden == frozenset()
    assert launch.visible([run], hidden) == [run]
    after = replace(build_inventory(loaded), mcp_hidden=hidden)
    kind = ExtensionKind(
        inventory=lambda: after,
        mcp_launch=lambda: launch,
        mcp_statuses=lambda: [SimpleNamespace(name="shared", state="connected", transport="stdio")],
    )
    lock = kind.locks()["mcp:shared"]
    assert lock.tag == "connected" and lock.reason == "Connected · tools in use"
    item = next(i for i in kind.items() if i.key == "mcp:shared")
    assert "failpack failed to load, but this server started at launch" in item.description
    lines = reload_lines(before, after, launch=launch)
    texts = [t for _s, t in lines]
    assert texts[0] == "Extensions after /reload: failpack failed"
    assert "✖ failpack failed to load: RuntimeError: broken setup (see /extension)" in texts
    assert "shared (MCP) stays connected; its tools stay in use" in texts
    assert not any("hidden" in t for t in texts)
    assert pkg.exists()
    # Then turned off: it reads off, and the server's tools are hidden.
    hidden_now = launch.hidden(_lists(["-failpack"], global_base=str(agent)))
    off = replace(build_inventory(await _discover(home, agent, ["-failpack"])), mcp_hidden=hidden_now)
    later = [t for _s, t in reload_lines(after, off, launch=launch)]
    assert later[0] == "Extensions after /reload: failpack off"
    assert "shared (MCP) stays connected until restart; its tools are hidden" in later


async def test_hidden_is_what_the_build_applied(home: Path) -> None:
    """Turned off: the line says hidden once, the build that hid it."""

    agent = home / "agent"
    _pack(agent / "extensions", "jira-tools", mcp="jira", hooks=True)
    records: list[Any] = []
    manifests = loader.scan_extension_manifests([], cwd=home, agent_dir=agent, records=records)
    launch = mcp_launch(records, [c for m in manifests for c in m.contributes.mcp_servers], set())
    on = build_inventory(await _discover(home, agent, []))
    hidden = launch.hidden(_lists(["-jira-tools"], global_base=str(agent)))
    off = replace(build_inventory(await _discover(home, agent, ["-jira-tools"])), mcp_hidden=hidden)
    first = [t for _s, t in reload_lines(on, off, launch=launch)]
    assert "jira (MCP) stays connected until restart; its tools are hidden" in first
    again = [t for _s, t in reload_lines(off, off, launch=launch)]
    assert again == []


# --- R01 / R02: identities discovery used to lose ------------------------------------


async def test_an_installed_pack_found_by_a_symlink_keeps_its_installed_names(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = home / "site"
    site.mkdir()
    pkg = _pack(site, "crosspack", entry="crosspack:setup")
    mark = home / "imported"
    (pkg / "__init__.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    dist = site / "Ep_Pack-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: Ep_Pack\nVersion: 1.0\n")
    (dist / "entry_points.txt").write_text("[aelix.extensions]\nep-alias = crosspack:setup\n")
    (dist / "RECORD").write_text("crosspack/__init__.py,,\ncrosspack/aelix-plugin.toml,,\n")
    agent = home / "agent"
    (agent / "extensions").mkdir(parents=True)
    (agent / "extensions" / "crosspack").symlink_to(pkg, target_is_directory=True)
    monkeypatch.syspath_prepend(str(site))
    for entry in ("-ep-alias", "-ep-pack"):
        sys.modules.pop("crosspack", None)
        loaded = await loader.discover_and_load_extensions(
            [],
            cwd=home,
            agent_dir=agent,
            trusted_ep_dists=frozenset({"ep-pack"}),
            entry_filter=extension_filter(_lists([entry], global_base=str(agent)), phase="G"),
        )
        (record,) = loaded.discovered
        # The directory tier's carrier survives, with its own tier, and the names.
        assert record.tier == "global" and not record.explicit
        assert describe_record(record).identities == ("crosspack", "ep-alias", "ep-pack")
        assert not record.selected and not mark.exists(), entry


async def test_a_pyproject_package_is_named_by_its_directory(home: Path) -> None:
    agent = home / "agent"
    bundle = agent / "extensions" / "bundle"
    bundle.mkdir(parents=True)
    mark = home / "imported"
    (bundle / "pyproject.toml").write_text('[tool.aelix]\nextensions = ["worker.py"]\n')
    (bundle / "worker.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    for entry in ("-bundle", "-extensions/bundle", "-worker", "-extensions/bundle/worker.py"):
        loaded = await _discover(home, agent, [entry])
        (record,) = loaded.discovered
        info = describe_record(record)
        assert info.identities == ("bundle", "worker") and info.label == "bundle/worker.py"
        assert not record.selected and not mark.exists(), entry
        # No "names no extension" warning for any of the four spellings.
        lists = _lists([entry], global_base=str(agent))
        assert list_warnings(lists, loaded.discovered, check_names=True) == []
    on = await _discover(home, agent, [])
    assert [r.selected for r in on.discovered] == [True] and mark.exists()


# --- R03 / R07: who owns a server, and which tools are its own ---------------------


async def test_the_pack_whose_server_runs_owns_it(home: Path) -> None:
    agent = home / "agent"
    _pack(home / ".aelix" / "extensions", "project-pack", mcp="shared", hooks=True)
    _pack(agent / "extensions", "global-pack", mcp="shared", hooks=True)
    records: list[Any] = []
    manifests = loader.scan_extension_manifests([], cwd=home, agent_dir=agent, records=records)
    kept, _notices, _refusals = loader.gate_manifest_mcp_contribs(reversed(manifests))
    # The manager keeps the LAST contribution of a name: the project pack's.
    assert [m.plugin.id for m in manifests] == ["project-pack", "global-pack"]
    launch = mcp_launch(records, kept, set())
    assert launch.owners["shared"].plugin == "project-pack"
    project_lists = _lists([], ["-project-pack"], global_base=str(agent), project_base=str(home))
    assert launch.hidden(project_lists) == {"shared"}
    assert launch.hidden(_lists(["-global-pack"], global_base=str(agent))) == frozenset()


def test_hiding_a_server_removes_exactly_its_own_wrappers() -> None:
    search = SimpleNamespace(name="jira__search")
    audit = SimpleNamespace(name="jira__admin__audit")
    wrappers = {"jira": (search,), "jira__admin": (audit,)}
    assert without_hidden([search, audit], frozenset({"jira"}), wrappers) == [audit]
    assert without_hidden([search, audit], frozenset({"jira__admin"}), wrappers) == [search]


# --- R04: on -> off -> on keeps the MCP tools active ----------------------------------


async def test_mcp_tools_come_back_active_after_on_off_on(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.runtime import AgentSessionRuntime
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionRepo,
        LocalFileSystem,
    )
    from aelix_agent_core.types import AgentTool
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import (
        _build_harness_options,
        _extension_lists,
        apply_post_registration_tool_policy,
    )

    from tests.runtime.test_runtime_reload import _stream

    agent = home / "agent"
    _pack(agent / "extensions", "owner", mcp="shared", hooks=True)
    settings = agent / "settings.json"

    def write(off: bool) -> None:
        settings.write_text(
            json.dumps({"defaultTools": ["-bash"], "extensions": ["-owner"] if off else []})
        )

    write(False)
    monkeypatch.chdir(home)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    manager = SettingsManager.create(cwd=str(home), agent_dir=agent, project_trusted=True)
    records: list[Any] = []
    manifests = loader.scan_extension_manifests([], cwd=home, agent_dir=agent, records=records)
    run = AgentTool(name="shared__run", description="mcp")
    launch = mcp_launch(
        records,
        [c for m in manifests for c in m.contributes.mcp_servers],
        set(),
        {"shared": (run,)},
    )
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(home / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(home)))

    async def factory(session: Any, *, reload_seed: Any = None) -> AgentHarness:
        hidden = launch.hidden(_extension_lists(manager, str(home), True))
        opts = await _build_harness_options(
            Args(),
            session,
            settings_manager=manager,
            project_trusted=True,
            app_mode="interactive",
            on_reload=reload_seed is not None,
            mcp_tools=launch.visible([run], hidden),
        )
        opts.stream_fn = _stream()
        harness = AgentHarness(opts)
        await apply_post_registration_tool_policy(harness, Args(), None)
        return harness

    runtime = AgentSessionRuntime(await factory(session), factory, repo=repo, fs=fs)

    def active() -> list[str]:
        return list(runtime.harness.state.active_tool_names or [])

    assert "shared__run" in active() and "bash" not in active()
    write(True)
    await runtime.reload()
    assert "shared__run" not in [t.name for t in runtime.harness.state.tools]
    write(False)
    await runtime.reload()
    assert "shared__run" in [t.name for t in runtime.harness.state.tools]
    # Back in the active set, and the policy still keeps bash off.
    assert "shared__run" in active() and "bash" not in active()
    await runtime.harness.dispose()


# --- R06 / R08: what /reload says that it did not ------------------------------------


async def test_a_refused_manifest_reaches_the_reload_line(home: Path) -> None:
    agent = home / "agent"
    pkg = _pack(agent / "extensions", "broken", hooks=True)
    before = build_inventory(await _discover(home, agent, []))
    (pkg / "aelix-plugin.toml").write_text("[plugin\n")
    after = build_inventory(await _discover(home, agent, []))
    texts = [t for _s, t in reload_lines(before, after)]
    assert texts[0] == "Extensions after /reload: broken failed"
    assert any(t.startswith("✖ broken was not loaded: Invalid manifest") for t in texts)
    # A new malformed pack, with nothing loaded before: still said.
    fresh = [t for _s, t in reload_lines(ExtensionInventory(), after)]
    assert fresh[0] == "Extensions after /reload: no change"
    assert any("was not loaded: Invalid manifest" in t for t in fresh)


async def test_a_new_shortcut_on_an_extension_already_on_is_said(home: Path) -> None:
    ext = home / "agent" / "extensions" / "short.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("def setup(aelix):\n    pass\n")
    before = build_inventory(await _discover(home, home / "agent", []))
    ext.write_text(
        "def setup(aelix):\n    aelix.register_shortcut('alt+9', handler=lambda: None)\n"
    )
    after = build_inventory(await _discover(home, home / "agent", []))
    lines = reload_lines(before, after, bound_shortcuts=frozenset())
    assert lines == [
        ("", "Extensions after /reload: no change"),
        ("yellow", "short.py's shortcut keys (alt+9) bind at the next launch"),
    ]
    # Said once: the next reload, with the key still unbound, does not repeat it.
    assert reload_lines(after, after, bound_shortcuts=frozenset()) == []


# --- V2: a name two extensions share ---------------------------------------------------


async def _shared_name(
    home: Path,
) -> tuple[Any, ExtensionKind, ResourceSelection, InMemorySettingsStorage]:
    agent = home / "agent"
    (agent / "extensions").mkdir(parents=True)
    (agent / "extensions" / "weather.py").write_text("def setup(aelix):\n    pass\n")
    (home / ".aelix" / "extensions").mkdir(parents=True)
    (home / ".aelix" / "extensions" / "weather.py").write_text("def setup(aelix):\n    pass\n")
    inventory = build_inventory(await _discover(home, agent, []))
    storage = InMemorySettingsStorage()
    manager = SettingsManager.from_storage(storage)
    kind = ExtensionKind(inventory=lambda: inventory)
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(home / ".aelix"), project_trusted=True
    )
    return inventory, kind, selection, storage


async def test_one_of_two_same_name_rows_is_written_by_its_path(home: Path) -> None:
    inventory, kind, selection, storage = await _shared_name(home)
    labels = display_labels(inventory.rows)
    assert sorted(labels.values()) == ["weather.py (global)", "weather.py (project)"]
    keys = {s.item.label: s.item.key for s in selection.snapshot(kind).states}
    result = await selection.save(kind, "project", {keys["weather.py (project)"]: False})
    assert result.ok, result.error
    assert json.loads(storage.project_content or "{}")["extensions"] == ["-extensions/weather.py"]
    # Once per row: only the edited one changed.
    assert result.changed == (("weather.py (project)", False),)
    saved = {s.item.label: s.saved for s in selection.snapshot(kind).states}
    assert saved["weather.py (project)"] is False and saved["weather.py (global)"] is True
    # The global row cannot be named by a path in the project's file: refused, nothing written.
    refused = await selection.save(kind, "project", {keys["weather.py (global)"]: False})
    assert not refused.ok and "save it to Global" in (refused.error or "")
    global_save = await selection.save(kind, "global", {keys["weather.py (global)"]: False})
    assert global_save.ok
    assert json.loads(storage.global_content or "{}")["extensions"] == ["-extensions/weather.py"]


async def test_tool_placeholders_tell_two_same_name_extensions_apart(home: Path) -> None:
    from aelix_coding_agent.core.extension_selection import tool_rows

    agent = home / "agent"
    (agent / "extensions").mkdir(parents=True)
    (agent / "extensions" / "weather.py").write_text("def setup(aelix):\n    pass\n")
    (home / ".aelix" / "extensions").mkdir(parents=True)
    (home / ".aelix" / "extensions" / "weather.py").write_text("def setup(aelix):\n    pass\n")
    inventory = build_inventory(await _discover(home, agent, ["-weather"]))
    labels = sorted(item.label for item, _lock in tool_rows(inventory))
    assert labels == ["weather.py (global)", "weather.py (project)"]


async def test_turning_one_back_on_keeps_the_other_off(home: Path) -> None:
    _inventory, kind, selection, storage = await _shared_name(home)
    storage.with_lock("project", lambda _current: json.dumps({"extensions": ["-weather"]}))
    keys = {s.item.label: s.item.key for s in selection.snapshot(kind).states}
    project, global_ = keys["weather.py (project)"], keys["weather.py (global)"]
    # Dropping ``-weather`` would turn the global one on too, and this file
    # cannot name it alone: refused, and it says what to do.
    refused = await selection.save(kind, "project", {project: True})
    assert not refused.ok
    assert "would also turn weather.py (global) on" in (refused.error or "")
    assert "keep it off in Global first" in (refused.error or "")
    assert json.loads(storage.project_content or "{}")["extensions"] == ["-weather"]
    # Done that way, each save changes its own row and nothing else.
    first = await selection.save(kind, "global", {global_: False})
    assert first.ok and first.changed == ()
    second = await selection.save(kind, "project", {project: True})
    assert second.ok, second.error
    assert second.changed == (("weather.py (project)", True),)
    saved = {s.item.label: s.saved for s in selection.snapshot(kind).states}
    assert saved["weather.py (project)"] is True and saved["weather.py (global)"] is False


async def test_a_same_file_entry_naming_two_rows_is_replaced_by_one_naming_the_other(
    home: Path,
) -> None:
    """Two global extensions both answer to ``jira`` (a pack and a legacy file)."""

    agent = home / "agent"
    _pack(agent / "extensions", "jira", hooks=True)
    (agent / "extensions" / "jira.py").write_text("def setup(aelix):\n    pass\n")
    inventory = build_inventory(await _discover(home, agent, []))
    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _current: json.dumps({"extensions": ["-jira"]}))
    kind = ExtensionKind(inventory=lambda: inventory)
    selection = ResourceSelection(
        SettingsManager.from_storage(storage),
        global_base=str(agent),
        project_base=str(home / ".aelix"),
        project_trusted=True,
    )
    keys = {s.item.label: s.item.key for s in selection.snapshot(kind).states}
    assert {"jira", "jira.py"} <= set(keys)
    result = await selection.save(kind, "global", {keys["jira.py"]: True})
    assert result.ok, result.error
    assert json.loads(storage.global_content or "{}")["extensions"] == ["-extensions/jira"]
    assert result.changed == (("jira.py", True),)


# --- V3: a symlinked agent directory ---------------------------------------------------


async def test_path_entries_match_under_a_symlinked_agent_dir(home: Path) -> None:
    real = home / "real-agent"
    (real / "extensions").mkdir(parents=True)
    mark = home / "imported"
    (real / "extensions" / "sentcontrol.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    link = home / "agent-link"
    link.symlink_to(real, target_is_directory=True)
    entry = "-extensions/sentcontrol.py"
    loaded = await _discover(home, link, [entry])
    (record,) = loaded.discovered
    assert not record.selected and not mark.exists()
    lists = _lists([entry], global_base=str(link))
    assert list_warnings(lists, loaded.discovered, check_names=True) == []


# --- V4, V8, R09 ---------------------------------------------------------------------------


def test_builtin_entries_are_warned_about() -> None:
    lists = _lists(["-builtin:guardrail", "!builtin:*", "+builtin:agents"])
    warnings = list_warnings(lists, [], check_names=True)
    for entry in ("-builtin:guardrail", "!builtin:*", "+builtin:agents"):
        assert any(f"{entry!r} is ignored: Guardrail, Permission" in w for w in warnings), entry
    # Not also "names no extension found".
    assert not any("names no extension" in w for w in warnings)


async def test_no_extensions_lists_an_e_file_once(home: Path) -> None:
    agent = home / "agent"
    weather = agent / "extensions" / "weather.py"
    weather.parent.mkdir(parents=True)
    weather.write_text("def setup(aelix):\n    pass\n")
    loaded = await loader.discover_and_load_extensions(
        [str(weather)], cwd=home, agent_dir=agent, no_discovery=True
    )
    inventory = build_inventory(loaded, no_extensions=True)
    listing = listing_inventory(loader.discover_extension_records([], cwd=home, agent_dir=agent))
    kind = ExtensionKind(inventory=lambda: inventory, listing=lambda: listing)
    locks = kind.locks()
    rows = [(i.label, locks[i.key].tag) for i in kind.items() if i.label == "weather.py"]
    assert rows == [("weather.py", "-e")]
    assert kind.notices() == [
        "--no-extensions: only -e extensions load this run; run without it to turn the others on and off."
    ]


# --- V5: what a delegated child inherits -------------------------------------------------


async def test_a_child_keeps_off_what_its_parent_kept_off(home: Path) -> None:
    agent = home / "agent"
    ext = agent / "extensions"
    ext.mkdir(parents=True)
    mark = home / "imported"
    (ext / "sentlegacy.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    (ext / "kept.py").write_text("def setup(aelix):\n    pass\n")
    pack = _pack(ext, "sentpack", entry="sentpack_mod_25:setup", mcp="sent-srv")
    (pack / "sentpack_mod_25.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    # The parent: its (project) settings turned the two sentinels off.
    parent = await _discover(home, agent, ["-sentlegacy", "-sentpack"])
    record = ExtensionOffRecord.of(parent.discovered)
    env: dict[str, str] = {}
    set_launch_extension_off(record)
    try:
        pass_extension_off_to_child(env)
    finally:
        set_launch_extension_off(None)
    inherited = ExtensionOffRecord.from_env(env[EXTENSIONS_PARENT_OFF_ENV])
    assert inherited == record and len(inherited.keys) == 2
    # The child: its own settings say nothing, yet neither phase imports them.
    for phase in ("G", "P"):
        child = await loader.discover_and_load_extensions(
            [],
            cwd=home,
            agent_dir=agent,
            entry_filter=extension_filter(_lists([]), phase=phase, inherited=inherited),  # type: ignore[arg-type]
        )
        assert [(describe_record(r).label, r.selected) for r in child.discovered] == [
            ("kept.py", True),
            ("sentlegacy.py", False),
            ("sentpack", False),
        ]
        assert not mark.exists()
    # Nor does the scan dial the pack's server.
    scanned = loader.scan_extension_manifests(
        [],
        cwd=home,
        agent_dir=agent,
        entry_filter=extension_filter(_lists([]), phase="P", inherited=inherited),
    )
    assert scanned == []
    # Suppress only: a record cannot turn on what the child's settings turn off.
    kept_off = await loader.discover_and_load_extensions(
        [],
        cwd=home,
        agent_dir=agent,
        entry_filter=extension_filter(
            _lists(["-kept"], global_base=str(agent)), phase="P", inherited=inherited
        ),
    )
    assert [r.selected for r in kept_off.discovered] == [False, False, False]
    # ``-e`` is never covered.
    explicit = await loader.discover_and_load_extensions(
        [str(ext / "kept.py")],
        cwd=home,
        agent_dir=agent,
        no_discovery=True,
        entry_filter=extension_filter(
            _lists([]), phase="P", inherited=ExtensionOffRecord(every=True)
        ),
    )
    assert [r.selected for r in explicit.discovered] == [True]


def test_the_off_record_is_bounded_and_lenient() -> None:
    assert ExtensionOffRecord.from_env("not json") == ExtensionOffRecord()
    assert ExtensionOffRecord.from_env('{"off": [1, "path:/x"], "all": "yes"}') == (
        ExtensionOffRecord(frozenset({"path:/x"}))
    )
    big = ExtensionOffRecord(frozenset(f"path:/{'x' * 200}/{n}" for n in range(200)))
    raw = big.to_env()
    assert len(raw) < 100 and ExtensionOffRecord.from_env(raw).every
    assert ExtensionOffRecord.of([], no_extensions=True).every


def test_the_child_env_builder_passes_the_record_and_the_launch_removes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_agents.print_channel import build_child_env
    from aelix_agents.rpc_channel import build_rpc_child_env

    from tests.agents_ext.test_print_channel_spawn import _profile

    record = ExtensionOffRecord(frozenset({"path:/a/b.py"}))
    set_launch_extension_off(record)
    try:
        for builder in (build_child_env, build_rpc_child_env):
            env = builder(_profile(), base={})
            assert ExtensionOffRecord.from_env(env[EXTENSIONS_PARENT_OFF_ENV]) == record
    finally:
        set_launch_extension_off(None)
    # A process that recorded nothing passes an inherited record through.
    env = build_child_env(_profile(), base={EXTENSIONS_PARENT_OFF_ENV: record.to_env()})
    assert env[EXTENSIONS_PARENT_OFF_ENV] == record.to_env()


# --- V11: what a /new, /fork or /resume build failed to load --------------------------------


async def test_failure_lines_name_failed_and_refused(home: Path) -> None:
    agent = home / "agent"
    (agent / "extensions").mkdir(parents=True)
    (agent / "extensions" / "broken.py").write_text(
        "def setup(aelix):\n    raise ValueError('nope')\n"
    )
    (agent / "extensions" / "bad").mkdir()
    (agent / "extensions" / "bad" / "aelix-plugin.toml").write_text("[plugin\n")
    inventory = build_inventory(await _discover(home, agent, []))
    texts = [t for _s, t in failure_lines(inventory)]
    assert texts[0] == "✖ broken.py failed to load: ValueError: nope (see /extension)"
    assert texts[1].startswith("✖ bad was not loaded: Invalid manifest")
    assert failure_lines(None) == []


def test_environment_is_untouched_by_these_tests() -> None:
    assert EXTENSIONS_PARENT_OFF_ENV not in os.environ


# --- V6: what no test pinned -------------------------------------------------------------


def test_built_in_lock_reasons_and_applied_follow_the_build() -> None:
    from aelix_coding_agent.core.extension_selection import EntryInfo, ExtensionRow

    def builtin(name: str) -> ExtensionRow:
        info = EntryInfo(name, (f"builtin:{name}",), None, None, builtin=name)
        return ExtensionRow(f"builtin:{name}", info, "builtin", False, True, True)

    broken = ExtensionRow(
        "global:/x/broken.py",
        EntryInfo("broken.py", ("broken",), "/x/broken.py", "global"),
        "global",
        False,
        True,
        False,
        error="RuntimeError: boom",
    )
    ok = ExtensionRow(
        "global:/x/ok.py",
        EntryInfo("ok.py", ("ok",), "/x/ok.py", "global"),
        "global",
        False,
        True,
        True,
    )
    rows = (builtin("guardrail"), builtin("permission"), builtin("status"), broken, ok)
    kind = ExtensionKind(inventory=lambda: ExtensionInventory(rows))
    locks = kind.locks()
    assert [locks[f"builtin:{n}"].reason for n in ("guardrail", "permission", "status")] == [
        "always on (Guardrail gate)",
        "always on (/permissions)",
        "always on (aelix_status)",
    ]
    assert {locks[f"builtin:{n}"].tag for n in ("guardrail", "permission", "status")} == {
        "always on"
    }
    # Applied is what the build LOADED: saved on and failed is not applied.
    assert kind.applied() == {
        "builtin:guardrail",
        "builtin:permission",
        "builtin:status",
        "global:/x/ok.py",
    }
