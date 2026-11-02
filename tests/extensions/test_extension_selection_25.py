"""#25 (ADR-0284) — the ``extensions`` settings filter, its phases and the inventory.

What is pinned here:

* the identities each carrier answers to (D1), and the first is what a panel writes;
* phase G reads the global file only, so a project entry cannot keep an extension
  from voting; phase P reads both (ADR-0279 D2);
* ``-e`` is never filtered, including a path another tier found first (Codex F06);
* an extension turned off is NEVER imported — the vote load, the factory, the
  manifest scan, ``aelix status`` and the panel (a sentinel writes a file on import);
* the inventory's failed / deferred / activation-failed states (Codex F09), the
  ``/reload`` line, the MCP launch inventory (F11), the reload phase (F08) and
  Windows path entries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_coding_agent.core import resource_filter
from aelix_coding_agent.core.extension_selection import (
    ExtensionInventory,
    ExtensionKind,
    McpLaunch,
    McpOwner,
    build_inventory,
    describe,
    extension_filter,
    list_warnings,
    mcp_launch,
    reload_failure_text,
    reload_lines,
    target_of,
    tool_rows,
    without_hidden,
)
from aelix_coding_agent.core.resource_filter import ScopeList, Target
from aelix_coding_agent.core.resource_selection import ResourceSelection, SettingsLists
from aelix_coding_agent.extensions.loader import (
    _EntryPointEntry,
    _ManifestEntry,
    activate_pending_extension,
    discover_and_load_extensions,
    discover_extension_records,
    scan_extension_manifests,
)

from tests.env_sandbox import sandbox_home

SENTINEL_BODY = (
    "from pathlib import Path\nPath({mark!r}).write_text('imported')\ndef setup(aelix):\n    pass\n"
)

MANIFEST = """\
[plugin]
id = "{pid}"
name = "{pid}"
version = "1.0.0"
description = "{desc}"
authors = ["a"]
repository = "https://example.invalid/x"
license = "MIT"

[plugin.api]
level = 1
min_level = 1

[activation]
{activation}

[capabilities]
shell_exec = true

{extra}
"""


def _lists(
    global_entries: list[str] | None = None,
    project_entries: list[str] | None = None,
    *,
    global_base: str = "/g",
    project_base: str = "/p",
) -> SettingsLists:
    return SettingsLists(
        ScopeList("global", tuple(global_entries or ()), global_base),
        ScopeList("project", tuple(project_entries or ()), project_base)
        if project_entries is not None
        else None,
    )


def _pack(
    root: Path,
    pid: str,
    *,
    entry: str | None = None,
    activation: str = "on_startup_finished = true",
    mcp: str | None = None,
    desc: str = "A pack",
    hooks: bool = False,
) -> Path:
    pkg = root / pid
    pkg.mkdir(parents=True)
    extra = ""
    if entry is not None:
        extra += f'[plugin.entry]\npython = "{entry}"\n\n'
    if mcp is not None:
        extra += (
            f'[[contributes.mcp_servers]]\nname = "{mcp}"\ntransport = "stdio"\ncommand = "true"\n'
        )
    if hooks:
        # A pack with no Python entry loads only with a hook (a config-only pack).
        extra += '[[contributes.hooks]]\nevent = "session_start"\ncommand = "true"\n'
    (pkg / "aelix-plugin.toml").write_text(
        MANIFEST.format(pid=pid, desc=desc, activation=activation, extra=extra)
    )
    return pkg


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sandbox_home(monkeypatch, tmp_path / "home")
    return tmp_path


# --- identities (D1) --------------------------------------------------------------


def test_identities_follow_adr_0284(tmp_path: Path) -> None:
    pkg = _pack(tmp_path, "jira-tools", mcp="jira")
    from aelix_coding_agent.extensions.loader import _load_manifest_from_dir

    manifest = _load_manifest_from_dir(pkg)
    assert manifest is not None
    plain = describe(_ManifestEntry(manifest=manifest, pkg_dir=pkg), "global")
    assert plain.identities == ("jira-tools",)
    assert plain.label == "jira-tools" and plain.mcp_servers == ("jira",)
    ep = _EntryPointEntry(name="jira", module="m", attr="setup", dist="Jira_Tools.Pack", reason="")
    bound = describe(_ManifestEntry(manifest=manifest, pkg_dir=pkg, ep_ref=ep), "installed")
    # plugin.id, then the entry-point name, then the PEP 503 distribution name.
    assert bound.identities == ("jira-tools", "jira", "jira-tools-pack")
    assert bound.scope == "global"
    bare = describe(ep, "installed")
    assert bare.identities == ("jira", "jira-tools-pack")
    py = describe(tmp_path / "weather.py", "global")
    assert (py.label, py.identities, py.path) == (
        "weather.py",
        ("weather",),
        str(tmp_path / "weather.py"),
    )
    (tmp_path / "lint").mkdir()
    init = describe(tmp_path / "lint" / "__init__.py", "project")
    assert (init.label, init.identities, init.path, init.scope) == (
        "lint",
        ("lint",),
        str(tmp_path / "lint"),
        "project",
    )
    from aelix_coding_agent.builtin.guardrail import GuardrailExtension

    builtin = describe(GuardrailExtension(), "builtin")
    assert builtin.identities == ("builtin:guardrail",) and builtin.builtin == "guardrail"
    assert describe("pkg.mod:factory", "cli").identities == ("pkg.mod:factory",)


# --- the phases -------------------------------------------------------------------


def _record(entry: Any, tier: str, *, explicit: bool = False) -> Any:
    return SimpleNamespace(entry=entry, tier=tier, explicit=explicit or tier == "cli")


def test_phase_g_reads_the_global_file_only_and_phase_p_both(tmp_path: Path) -> None:
    records = [_record(tmp_path / "weather.py", "global"), _record(tmp_path / "lint.py", "global")]
    lists = _lists(["-lint"], ["-weather"])
    assert extension_filter(lists, phase="G")(records) == [True, False]
    assert extension_filter(lists, phase="P")(records) == [False, False]


def test_builtins_and_e_are_never_filtered(tmp_path: Path) -> None:
    from aelix_coding_agent.builtin.guardrail import GuardrailExtension

    records = [
        _record(GuardrailExtension(), "builtin"),
        _record(tmp_path / "dbg.py", "cli"),
        _record(tmp_path / "weather.py", "global", explicit=True),
    ]
    lists = _lists(["-builtin:guardrail", "-dbg", "-weather", "!*"])
    assert extension_filter(lists, phase="P")(records) == [True, True, True]


def test_a_filter_that_raises_loads_everything(tmp_path: Path) -> None:
    class Boom:
        def __getattr__(self, _name: str) -> Any:
            raise RuntimeError("boom")

    lists = SettingsLists(Boom(), None)  # type: ignore[arg-type]
    assert extension_filter(lists, phase="P")([_record(tmp_path / "a.py", "global")]) == [True]


async def test_e_naming_a_path_a_tier_found_first_is_never_filtered(home: Path) -> None:
    """Codex F06: the dedup keeps the global tier's carrier; ``-e`` marks it."""

    agent = home / "agent"
    (agent / "extensions").mkdir(parents=True)
    weather = agent / "extensions" / "weather.py"
    weather.write_text("def setup(aelix):\n    aelix.WEATHER = True\n")
    lists = _lists(["-weather"], global_base=str(agent))
    loaded = await discover_and_load_extensions(
        [str(weather)],
        cwd=home,
        agent_dir=agent,
        entry_filter=extension_filter(lists, phase="P"),
    )
    (record,) = [r for r in loaded.discovered if r.tier != "builtin"]
    assert record.tier == "global" and record.explicit and record.selected
    assert record.outcome is not None and record.outcome.extension is not None


# --- the sentinel: an off extension is never imported ------------------------------


def _sentinels(home: Path) -> tuple[Path, Path, Path]:
    """A global legacy file and a global manifest pack, both off; each writes *mark* on import."""

    agent = home / "agent"
    ext = agent / "extensions"
    ext.mkdir(parents=True)
    mark = home / "IMPORTED"
    (ext / "sentinel.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    pack = _pack(ext, "sentinel-pack", entry="sentinel_pack_mod:setup", mcp="sentinel-srv")
    (pack / "sentinel_pack_mod.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    (agent / "settings.json").write_text(
        json.dumps({"extensions": ["-sentinel", "-sentinel-pack"]})
    )
    return agent, mark, pack


async def test_the_sentinel_is_never_imported_by_any_loader(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, mark, pack = _sentinels(home)
    monkeypatch.syspath_prepend(str(pack))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    work = home / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    manager = SettingsManager.create(cwd=str(work), agent_dir=agent, project_trusted=False)
    from aelix_coding_agent.cli.entry import _extension_lists

    # 1. The vote load: phase G, the global file only.
    vote = await discover_and_load_extensions(
        [],
        cwd=work,
        agent_dir=agent,
        no_project_local=True,
        entry_filter=extension_filter(_extension_lists(manager, str(work), False), phase="G"),
    )
    assert not mark.exists()
    assert {r.tier for r in vote.discovered if not r.selected} == {"global"}

    # 2. The manifest scan: phase P; the off pack's MCP server is never returned.
    records: list[Any] = []
    manifests = scan_extension_manifests(
        [],
        cwd=work,
        agent_dir=agent,
        entry_filter=extension_filter(_extension_lists(manager, str(work), True), phase="P"),
        records=records,
    )
    assert manifests == [] and not mark.exists()
    assert mcp_launch(records, set(), set()).owners["sentinel-srv"].started is False

    # 3. The factory — the real ``_build_harness_options`` every build runs.
    from aelix_agent_core.session import MemorySessionStorage, Session
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import _build_harness_options

    holder: dict[str, Any] = {}
    await _build_harness_options(
        Args(),
        Session(MemorySessionStorage()),
        settings_manager=manager,
        captured_inventory=holder,
    )
    assert not mark.exists()
    inventory = holder["inventory"]
    off = {r.info.label for r in inventory.rows if not r.selected}
    assert off == {"sentinel.py", "sentinel-pack"}

    # 4. ``aelix status``: listed as off, never imported.
    from aelix_coding_agent.cli.status import _collect

    report = await _collect(discover=True)
    assert not mark.exists()
    assert {e["name"] for e in report["off_extensions"]} == {"sentinel.py", "sentinel-pack"}
    assert all(e["name"] != "sentinel.py" for e in report["discovered_extensions"])

    # 5. The panel: rows, locks and problems read the inventory only.
    kind = ExtensionKind(inventory=lambda: inventory, cwd=str(work), agent_dir=str(agent))
    items = {i.label: i for i in kind.items()}
    assert {"sentinel.py", "sentinel-pack"} <= set(items)
    kind.locks()
    kind.item_problems()
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(work / ".aelix"), project_trusted=False
    )
    snapshot = selection.snapshot(kind)
    state = {s.item.label: s for s in snapshot.states}
    assert state["sentinel.py"].saved is False and state["sentinel.py"].applied is False
    assert not mark.exists()


async def test_a_project_entry_cannot_keep_an_extension_from_voting(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase G: the project's file is not read before trust (ADR-0284 D2)."""

    agent = home / "agent"
    (agent / "extensions").mkdir(parents=True)
    (agent / "extensions" / "voter.py").write_text("def setup(aelix):\n    pass\n")
    work = home / "work"
    (work / ".aelix").mkdir(parents=True)
    (work / ".aelix" / "settings.json").write_text(json.dumps({"extensions": ["-voter"]}))
    manager = SettingsManager.create(cwd=str(work), agent_dir=agent, project_trusted=False)
    from aelix_coding_agent.cli.entry import _extension_lists

    lists = _extension_lists(manager, str(work), False)
    assert lists.project_list is None
    vote = await discover_and_load_extensions(
        [],
        cwd=work,
        agent_dir=agent,
        no_project_local=True,
        entry_filter=extension_filter(lists, phase="G"),
    )
    assert [r.selected for r in vote.discovered] == [True]
    # Trusted, phase P: the same entry turns it off.
    trusted = SettingsManager.create(cwd=str(work), agent_dir=agent, project_trusted=True)
    later = await discover_and_load_extensions(
        [],
        cwd=work,
        agent_dir=agent,
        entry_filter=extension_filter(_extension_lists(trusted, str(work), True), phase="P"),
    )
    assert [r.selected for r in later.discovered] == [False]


# --- the inventory and /reload's line --------------------------------------------


async def _load(home: Path, entries: list[str]) -> ExtensionInventory:
    agent = home / "agent"
    loaded = await discover_and_load_extensions(
        [],
        cwd=home,
        agent_dir=agent,
        entry_filter=extension_filter(_lists(entries, global_base=str(agent)), phase="P"),
    )
    return build_inventory(loaded)


async def test_off_on_and_failed_reach_the_reload_line(home: Path) -> None:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    (ext / "weather.py").write_text("def setup(aelix):\n    pass\n")
    (ext / "lint.py").write_text("def setup(aelix):\n    pass\n")
    (ext / "broken.py").write_text("def setup(aelix):\n    raise RuntimeError('boom')\n")
    before = await _load(home, ["-lint"])
    after = await _load(home, ["-weather"])
    lines = reload_lines(before, after)
    assert lines[0] == ("", "Extensions after /reload: lint.py on · weather.py off")
    assert ("red", "✖ broken.py failed to load: boom (see /extension)") in lines
    # Nothing changed and nothing failed: no line at all.
    (ext / "broken.py").unlink()
    quiet = await _load(home, [])
    assert reload_lines(quiet, quiet) == []
    # The panel: failed is its own state, never "pending".
    kind = ExtensionKind(inventory=lambda: after)
    problems = kind.item_problems()
    assert list(problems.values()) == ["boom"]


async def test_a_lazy_packs_failed_activation_is_a_problem(home: Path) -> None:
    """Codex F09: deferred → activation_failed reaches ``ItemState.problem``."""

    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    _pack(ext, "lazy", entry="no_such_module_25:setup", activation='on_command = ["lazy-go"]')
    loaded = await discover_and_load_extensions([], cwd=home, agent_dir=home / "agent")
    inventory = build_inventory(loaded)
    (row,) = inventory.rows
    assert row.deferred and inventory.deferred_state(row) == "deferred"
    kind = ExtensionKind(inventory=lambda: inventory)
    assert any("loads at its first command" in i.description for i in kind.items())
    with pytest.raises(ModuleNotFoundError):
        await activate_pending_extension(loaded.runtime, "lazy")
    assert inventory.deferred_state(row) is not None
    assert inventory.deferred_state(row).startswith("failed: ModuleNotFoundError")  # type: ignore[union-attr]
    assert "failed to activate" in kind.item_problems()[row.key]


async def test_an_entry_point_error_is_dropped_when_its_pack_is_off(home: Path) -> None:
    from aelix_coding_agent.extensions.loader import DiscoveredEntry, ExtensionLoadError

    err = ExtensionLoadError(path="entry_point:x", error="loaded WITHOUT its manifest")
    record = DiscoveredEntry(
        _EntryPointEntry(name="x", module="m", attr="", dist="x", reason=""),
        "installed",
        selected=False,
        discovery_error=err,
    )
    loaded = SimpleNamespace(discovered=[record], errors=[err], runtime=None)
    inventory = build_inventory(loaded)  # type: ignore[arg-type]
    assert inventory.refused == () and inventory.rows[0].note is None


# --- MCP servers a plugin declared (F11, owner decision 10) -------------------------


def test_a_started_server_whose_plugin_is_off_loses_its_tools_not_its_connection() -> None:
    target = Target(identities=("jira-tools",), path="/g/extensions/jira-tools", scope="global")
    launch = McpLaunch(
        {
            "jira": McpOwner("jira-tools", target, filtered=True, started=True),
            "other": McpOwner("other-pack", Target(("other-pack",)), filtered=True, started=False),
            "pinned": McpOwner("pinned", Target(("pinned",)), filtered=False, started=True),
        }
    )
    assert launch.hidden(_lists(["-jira-tools", "-pinned", "-other-pack"])) == {"jira"}
    assert launch.hidden(_lists([])) == frozenset()
    tools = [SimpleNamespace(name=n) for n in ("jira__search", "jira__get", "demo__add")]
    assert [t.name for t in without_hidden(tools, frozenset({"jira"}))] == ["demo__add"]


async def test_mcp_rows_say_hidden_and_connects_next_launch(home: Path) -> None:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    _pack(ext, "jira-tools", mcp="jira", hooks=True)
    _pack(ext, "late-pack", mcp="late", hooks=True)
    loaded = await discover_and_load_extensions(
        [],
        cwd=home,
        agent_dir=home / "agent",
        entry_filter=extension_filter(
            _lists(["-jira-tools"], global_base=str(home / "agent")), phase="P"
        ),
    )
    inventory = build_inventory(loaded)
    launch = McpLaunch(
        {
            "jira": McpOwner(
                "jira-tools", target_of(inventory.rows[0].info), filtered=True, started=True
            ),
            "late": McpOwner(
                "late-pack", target_of(inventory.rows[1].info), filtered=True, started=False
            ),
        }
    )
    statuses = [
        SimpleNamespace(name="jira", transport="stdio", state="connected"),
        SimpleNamespace(name="demo", transport="stdio", state="disabled", config="mcp.json"),
    ]
    kind = ExtensionKind(
        inventory=lambda: inventory, mcp_statuses=lambda: statuses, mcp_launch=lambda: launch
    )
    locks = kind.locks()
    assert locks["mcp:jira"].tag == "connected · hidden"
    assert (
        "jira-tools is off: its tools are hidden; disconnects at restart"
        in locks["mcp:jira"].reason
    )
    assert locks["mcp:jira"].refusal == "MCP servers are turned on and off in mcp.json · /mcp"
    assert locks["mcp:late"].tag == "not started"
    assert "connects at the next launch" in locks["mcp:late"].reason
    assert locks["mcp:demo"].tag == "disabled"
    roles = {i.key: i.role for i in kind.items()}
    assert roles["mcp:jira"] == roles["mcp:demo"] == "info"
    # The reload line: turning jira-tools off says its server stays connected.
    on = await _load(home, [])
    lines = reload_lines(on, inventory, launch=launch)
    assert ("yellow", "jira (MCP) stays connected until restart; its tools are hidden") in lines


# --- what waits for a restart (F10) --------------------------------------------------


async def test_providers_and_new_shortcut_keys_wait_for_a_restart(home: Path) -> None:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    (ext / "prov.py").write_text(
        "def setup(aelix):\n"
        "    aelix.register_provider('acme', {'baseUrl': 'http://x', 'models': []})\n"
        "    aelix.register_shortcut('ctrl+y', handler=lambda: None, description='y')\n"
    )
    on = await _load(home, [])
    (row,) = on.rows
    assert row.error is None
    assert row.providers == ("acme",) and row.shortcuts == ("ctrl+y",)
    off = await _load(home, ["-prov"])
    assert ("yellow", "acme (provider) stays in /model until restart") in reload_lines(on, off)
    assert (
        "yellow",
        "prov.py's shortcut keys (ctrl+y) bind at the next launch",
    ) in reload_lines(off, on, bound_shortcuts=frozenset())
    assert not any(
        "bind at" in text
        for _s, text in reload_lines(off, on, bound_shortcuts=frozenset({"ctrl+y"}))
    )


def test_reload_failure_text_tells_applied_from_nothing_applied() -> None:
    failed = reload_failure_text("the rebuild", RuntimeError("boom"), applied=False)
    assert failed.startswith("✖ /reload failed during the rebuild: RuntimeError: boom")
    assert "nothing applied" in failed
    later = reload_failure_text("session_start", RuntimeError("late"), applied=True)
    assert later.startswith("⚠ /reload applied the new extensions, but session_start failed")
    assert "nothing applied" not in later


async def test_the_runtime_names_the_step_a_reload_failed_in(tmp_path: Path) -> None:
    """Codex F08: a factory failure is ``build`` and leaves the old harness pointer."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.runtime import AgentSessionRuntime
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionRepo,
        LocalFileSystem,
    )
    from aelix_ai.streaming import Model

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    calls = {"n": 0}

    async def factory(new_session: Any, **_kw: Any) -> AgentHarness:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("factory boom")
        return AgentHarness(
            AgentHarnessOptions(model=Model(id="m", provider="p"), session=new_session)
        )

    harness = await factory(session)
    runtime = AgentSessionRuntime(harness, factory, repo=repo, fs=fs)
    assert runtime.reload_phase is None
    with pytest.raises(RuntimeError, match="factory boom"):
        await runtime.reload()
    assert runtime.reload_phase == "build"
    assert runtime.harness is harness


# --- warnings, Windows, /tool ------------------------------------------------------------


def test_launch_warnings_name_a_path_entry_and_an_entry_that_names_nothing(tmp_path: Path) -> None:
    records = [_record(tmp_path / "weather.py", "global")]
    lists = SettingsLists(
        ScopeList("global", ("-weather", "-nope", "./more-extensions"), str(tmp_path)),
        None,
        labels={"global": "settings.json"},
    )
    warnings = list_warnings(lists, records, check_names=True)
    assert any("'./more-extensions' would add an extension source by path" in w for w in warnings)
    assert any("'-nope' names no extension found at launch" in w for w in warnings)
    assert not any("'-weather'" in w for w in warnings)
    # --no-extensions: nothing was discovered, so names are not checked.
    assert not any("names no" in w for w in list_warnings(lists, [], check_names=False))


def test_windows_path_entries_compare_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = Target(
        identities=("weather",), path=str(tmp_path / "ext" / "weather.py"), scope="global"
    )
    upper = "-" + str(tmp_path / "EXT" / "Weather.py").replace("/", "\\")
    lists = _lists([upper], global_base=str(tmp_path))
    monkeypatch.setattr(resource_filter, "_OS_NAME", "posix")
    assert resource_filter.effective(target, lists.global_list, None) == (True, None)
    monkeypatch.setattr(resource_filter, "_OS_NAME", "nt")
    assert resource_filter.effective(target, lists.global_list, None) == (False, "global")


async def test_tool_rows_are_one_placeholder_per_off_extension(home: Path) -> None:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    (ext / "weather.py").write_text("def setup(aelix):\n    pass\n")
    (ext / "jira.py").write_text("def setup(aelix):\n    pass\n")
    inventory = await _load(home, ["-jira"])
    rows = tool_rows(inventory)
    assert [(i.label, i.role, lock.tag) for i, lock in rows] == [
        ("jira.py", "placeholder", "ext off")
    ]
    assert rows[0][1].reason.startswith("see /extension")


def test_the_no_extensions_listing_is_locked_off(home: Path) -> None:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    (ext / "weather.py").write_text("raise SystemExit('imported')\n")
    from aelix_coding_agent.core.extension_selection import listing_inventory

    listing = listing_inventory(discover_extension_records([], cwd=home, agent_dir=home / "agent"))
    kind = ExtensionKind(
        inventory=lambda: ExtensionInventory(no_extensions=True), listing=lambda: listing
    )
    labels = {i.label: i.key for i in kind.items()}
    assert "weather.py" in labels
    lock = kind.locks()[labels["weather.py"]]
    assert (lock.value, lock.tag) == (False, "--no-extensions")
    assert any("--no-extensions" in n for n in kind.notices())


def test_an_untrusted_project_is_one_locked_summary_row(tmp_path: Path) -> None:
    ext = tmp_path / ".aelix" / "extensions"
    (ext / "pkg").mkdir(parents=True)
    (ext / "pkg" / "__init__.py").write_text("raise SystemExit('read')\n")
    (ext / "not-an-extension").mkdir()
    (ext / "a.py").write_text("raise SystemExit('read')\n")
    (ext / ".hidden.py").write_text("")
    kind = ExtensionKind(inventory=lambda: None, cwd=str(tmp_path), project_trusted=False)
    (row,) = [i for i in kind.items() if i.key == "project-untrusted"]
    assert row.label == "2 extensions not loaded"
    assert kind.locks()["project-untrusted"].kind == "untrusted"


async def test_a_panel_save_writes_the_identity_and_matches_a_hand_edit(home: Path) -> None:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    (ext / "weather.py").write_text("def setup(aelix):\n    pass\n")
    inventory = await _load(home, [])
    storage = InMemorySettingsStorage()
    manager = SettingsManager.from_storage(storage)
    kind = ExtensionKind(inventory=lambda: inventory)
    selection = ResourceSelection(
        manager,
        global_base=str(home / "agent"),
        project_base=str(home / ".aelix"),
        project_trusted=True,
    )
    key = next(s.item.key for s in selection.snapshot(kind).states if s.item.label == "weather.py")
    result = await selection.save(kind, "global", {key: False})
    assert result.ok and result.changed == (("weather.py", False),)
    assert json.loads(storage.global_content or "{}")["extensions"] == ["-weather"]
    # What the file now says is what a hand edit of "-weather" says: off.
    after = selection.snapshot(kind)
    state = next(s for s in after.states if s.item.key == key)
    assert state.saved is False and state.applied is True and state.pending
    assert os.environ.get("HOME") == str(home / "home")
