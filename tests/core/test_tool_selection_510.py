"""#510 (ADR-0283; ADR-0279 D15) — ``defaultTools``, the policy builder and the ``/tool`` kind.

The grammar is pi's (``settings-manager.ts`` ``resolveDefaultTools`` /
``mergeDefaultTools``) with the aelix base of every tool on (owner decision 3):
plain names replace the built-in set only; ``±`` reach any tool; an absent key
is every tool on and ``[]`` is "no built-ins" (Codex F04). The editor keeps a
replacing list replacing and moves one tool at a time (Codex F03).
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.tool_policy import ToolPolicy
from aelix_agent_core.types import AgentTool
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.core.resource_filter import ScopeList
from aelix_coding_agent.core.resource_selection import ResourceItem, ResourceSelection
from aelix_coding_agent.core.tool_selection import (
    DefaultToolsGrammar,
    EditRefused,
    ToolKind,
    build_tool_policy,
    default_tool_value,
    default_tools_lists,
    launch_tool_warnings,
    merge_default_tools,
    resolve_saved_off,
)
from aelix_coding_agent.extensions.api import Extension

B = frozenset({"read", "bash", "edit", "write", "grep", "find", "ls"})
NAMES = [*sorted(B), "weather", "demo__add"]


def _list(scope: str, entries: list[str] | None) -> ScopeList | None:
    if entries is None:
        return None
    return ScopeList(scope, tuple(entries), "", present=True)  # type: ignore[arg-type]


def _on(g: list[str] | None, p: list[str] | None) -> list[str]:
    merged = merge_default_tools(_list("global", g), _list("project", p))
    return [n for n in NAMES if default_tool_value(n, merged, B)[0]]


# --- the grammar ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("global_entries", "project_entries", "on"),
    [
        (None, None, NAMES),
        ([], None, ["weather", "demo__add"]),  # F04: [] is "no built-ins"
        (["read"], None, ["read", "weather", "demo__add"]),
        (["-bash"], None, [n for n in NAMES if n != "bash"]),
        (["read", "+grep", "-weather"], None, ["grep", "read", "demo__add"]),
        (["-demo__add"], None, [n for n in NAMES if n != "demo__add"]),  # ± reach MCP tools
        (["weather"], None, ["weather", "demo__add"]),  # a plain name never turns an ext tool off
        # project modifiers add to the global list; a project plain name replaces it
        (["read"], ["+bash"], ["bash", "read", "weather", "demo__add"]),
        (["read", "-weather"], ["ls"], ["ls", "weather", "demo__add"]),
        # F04, separately: a project [] modifies nothing over a global list ...
        (["read"], [], ["read", "weather", "demo__add"]),
        # ... and is "no built-ins" alone (pi: mergeDefaultTools(undefined, []) = [])
        (None, [], ["weather", "demo__add"]),
        (None, ["-ls"], [n for n in NAMES if n != "ls"]),
    ],
)
def test_the_grammar_table(
    global_entries: list[str] | None, project_entries: list[str] | None, on: list[str]
) -> None:
    assert sorted(_on(global_entries, project_entries)) == sorted(on)


def test_decided_by_names_the_file() -> None:
    merged = merge_default_tools(_list("global", ["read", "-weather"]), _list("project", ["+ls"]))
    assert default_tool_value("ls", merged, B) == (True, "project")
    assert default_tool_value("bash", merged, B) == (False, "global")
    assert default_tool_value("weather", merged, B) == (False, "global")
    assert default_tool_value("other", merged, B) == (True, None)


def test_saved_off_is_exact_over_every_name_the_lists_spell() -> None:
    g = _list("global", ["read", "-weather", "+nope"])
    assert resolve_saved_off(g, None, B) == (B - {"read"}) | {"weather"}
    assert resolve_saved_off(None, None, B) == frozenset()


def test_validation_refuses_globs_and_never_turns_a_typo_into_no_builtins() -> None:
    g = DefaultToolsGrammar(B)
    assert g.validate(None) == (None, [])
    entries, problems = g.validate(["read", "bash*", "!x", "", "+", 3])
    assert entries == ["read"]
    assert len(problems) == 5 and any("exact names only" in p for p in problems)
    # A list whose every entry is refused reads as ABSENT, not as [] (no built-ins).
    assert g.validate(["bash*"])[0] is None
    assert g.validate([]) == ([], [])
    assert g.validate("read")[0] is None


# --- the editor (Codex F03) -------------------------------------------------------


def _item(name: str) -> ResourceItem:
    return ResourceItem(kind="tool", key=name, label=name, identities=(name,))


def _read(scope: str, raw: list[str] | None) -> ScopeList:
    """A raw list as the launch and the panel read it: through the grammar's validation."""

    entries, _problems = DefaultToolsGrammar(B).validate(raw)
    return ScopeList(scope, tuple(entries or ()), "", present=entries is not None)  # type: ignore[arg-type]


def _values(ge: list[str] | None, pe: list[str] | None, universe: set[str]) -> dict[str, bool]:
    merged = merge_default_tools(_read("global", ge), _read("project", pe))
    return {n: default_tool_value(n, merged, B)[0] for n in universe}


def _apply(
    g: DefaultToolsGrammar,
    scope: str,
    ge: list[str] | None,
    pe: list[str] | None,
    name: str,
    want: bool,
) -> tuple[list[str] | None, list[str] | None]:
    """One edit as ``ResourceSelection.save`` makes it: the other file, read and validated."""

    target = ge if scope == "global" else pe
    new = g.edit(
        None if target is None else list(target),
        _item(name),
        want,
        scope=scope,  # type: ignore[arg-type]
        global_list=_read("global", ge) if scope == "project" else None,
        base_dir="",
        project_list=_read("project", pe) if scope == "global" else None,
    )
    return (new, pe) if scope == "global" else (ge, new)


_REFUSED_ENTRIES = ["bash*", "!read", "+", "-", "", "  ", "*", "+gr*", " -ls ", "read "]


def _raw_list(rng: random.Random) -> list[str] | None:
    """Absent, ``[]``, or names / ``±`` entries with refused and unstripped ones mixed in."""

    roll = rng.random()
    if roll < 0.15:
        return None
    if roll < 0.25:
        return []
    out: list[str] = []
    for _ in range(rng.randint(1, 5)):
        pick, name = rng.random(), rng.choice([*NAMES, "ghost"])
        if pick < 0.15:
            out.append(rng.choice(_REFUSED_ENTRIES))
        elif pick < 0.45:
            out.append(name)
        elif pick < 0.75:
            out.append("+" + name)
        else:
            out.append("-" + name)
    return out


@pytest.mark.parametrize("seed", [510, 1, 2])
def test_editing_one_tool_moves_that_tool_and_no_other(seed: int) -> None:
    """The property Codex F03 asked for, over RAW lists in both scopes (Codex F01, verify V1/V2).

    Every value is read as the launch reads it — refused entries dropped, an
    all-refused list absent. Per edit: the target takes ``want`` in the edited
    file's own view (P1); no other tool moves in ANY view the file is read in —
    for a global edit both the global file alone and the two files together
    (P2); a satisfied request writes nothing (P3); toggling back restores
    every value (P4). A refusal writes nothing, and only a global edit under
    a project list can need one.
    """

    g = DefaultToolsGrammar(B)
    rng = random.Random(seed)
    universe = {*NAMES, "unnamed_ext"}
    edits = refused = 0
    for _ in range(4000):
        ge, pe = _raw_list(rng), _raw_list(rng)
        scope = rng.choice(["global", "project"])
        name, want = rng.choice([*NAMES, "unnamed_ext"]), rng.random() < 0.5
        own = ge if scope == "global" else pe
        views = [(ge, None), (ge, pe)] if scope == "global" else [(ge, pe)]
        before = [_values(g_, p_, universe) for g_, p_ in views]
        case = (scope, ge, pe, name, want)
        try:
            nge, npe = _apply(g, scope, ge, pe, name, want)
        except EditRefused:
            refused += 1
            assert scope == "global" and _read("project", pe).present, case
            continue
        new = nge if scope == "global" else npe
        if before[0][name] == want:
            assert new == own, case  # P3
            continue
        edits += 1
        after_views = [(nge, None), (nge, npe)] if scope == "global" else [(nge, npe)]
        after = [_values(g_, p_, universe) for g_, p_ in after_views]
        assert after[0][name] is want, (case, new)  # P1
        for a, b in zip(after, before, strict=True):
            assert {n: a[n] for n in universe - {name}} == {n: b[n] for n in universe - {name}}, (
                case,
                new,
            )  # P2
        back_ge, back_pe = _apply(g, scope, nge, npe, name, not want)
        back_views = (
            [(back_ge, None), (back_ge, back_pe)] if scope == "global" else [(back_ge, back_pe)]
        )
        assert [_values(g_, p_, universe) for g_, p_ in back_views] == before, (case, new)  # P4
    assert edits > 1000 and refused < 100, (edits, refused)


def test_a_refused_entry_is_never_a_plain_name() -> None:
    """Codex F01 / verify V2: the editor reads the list as the launch does."""

    g = DefaultToolsGrammar(B)
    # F01: ``bash*`` is refused, so ``["read", "bash*"]`` is "read only". Dropping
    # ``read`` would leave a list read as ABSENT — every built-in on.
    out = g.edit(
        ["read", "bash*"], _item("read"), False, scope="global", global_list=None, base_dir=""
    )
    assert out == ["read", "bash*", "-read"]
    assert _values(out, None, set(B)) == dict.fromkeys(B, False)
    # An entry is matched by its stripped form: ``-bash `` is ``-bash``.
    assert (
        g.edit(["-bash "], _item("bash"), True, scope="global", global_list=None, base_dir="")
        is None
    )
    # V2: a blank entry is not a plain name, so dropping ``edit`` would make the
    # list modifier-only and turn every other built-in on.
    out = g.edit(
        ["edit", "-ls", "  "], _item("edit"), False, scope="project", global_list=None, base_dir=""
    )
    assert out == ["edit", "-ls", "  ", "-edit"]
    assert not any(_values(None, out, set(B)).values())


def test_a_global_save_keeps_a_project_list_that_builds_on_it() -> None:
    """Verify V1: global absent, project ``[]`` (no built-ins), ``weather`` off on Global.

    ``["-weather"]`` turns weather off in the global file and nothing else there,
    but here, where the project's ``[]`` builds on the global list, it turns
    every built-in ON. ``["weather", "-weather"]`` keeps them off here but turns
    them off in every folder without a project list. No edit holds both: refused.
    """

    g = DefaultToolsGrammar(B)
    with pytest.raises(EditRefused, match="save it to This project instead") as info:
        _apply(g, "global", None, [], "weather", False)
    assert "bash, edit, find, grep, ls, read, write here" in str(info.value)
    # The other way round there is one: ``+weather`` keeps the global list
    # modifier-only, so the project's ``[]`` still builds on every built-in.
    assert _apply(g, "global", ["-weather"], [], "weather", True) == (["+weather"], [])
    # Without the project list the same global edit is the plain one.
    assert _apply(g, "global", None, None, "weather", False) == (["-weather"], None)


@pytest.mark.parametrize(
    ("entries", "name", "first", "back"),
    [
        (["read"], "bash", ["read", "+bash"], ["read"]),
        (["read"], "read", [], ["read"]),
        (["read", "grep"], "read", ["grep"], ["grep", "+read"]),
        (["-bash"], "bash", None, ["-bash"]),
        (None, "bash", ["-bash"], None),
        ([], "bash", ["bash"], []),
        ([], "weather", ["weather", "-weather"], ["weather"]),
    ],
)
def test_toggling_back_restores_what_the_list_says(
    entries: list[str] | None,
    name: str,
    first: list[str] | None,
    back: list[str] | None,
) -> None:
    g = DefaultToolsGrammar(B)
    value = name in _on(entries, None)
    once = g.edit(entries, _item(name), not value, scope="global", global_list=None, base_dir="")
    assert once == first
    twice = g.edit(once, _item(name), value, scope="global", global_list=None, base_dir="")
    assert _on(twice, None) == _on(entries, None)
    assert twice == back


def test_an_already_satisfied_request_returns_the_list_unchanged() -> None:
    g = DefaultToolsGrammar(B)
    for entries in (["read"], ["read", "+read"], ["-bash", "-bash"], [], None):
        for name in NAMES:
            want = name in _on(entries, None)
            out = g.edit(entries, _item(name), want, scope="global", global_list=None, base_dir="")
            assert out == entries


# --- saving through the service (edit_list keeps [] apart from absent) -----------


def _manager(
    global_doc: dict[str, Any] | None = None,
) -> tuple[SettingsManager, InMemorySettingsStorage]:
    storage = InMemorySettingsStorage()
    if global_doc is not None:
        storage.with_lock("global", lambda _c: json.dumps(global_doc))
    return SettingsManager.from_storage(storage), storage


def _global(storage: InMemorySettingsStorage) -> dict[str, Any]:
    out: list[Any] = [None]

    def _read(current: str | None) -> None:
        out[0] = json.loads(current) if current else {}

    storage.with_lock("global", _read)
    return out[0]


class _Kind:
    kind = "tool"
    settings_key = "defaultTools"
    effect = "next_request"

    def __init__(self) -> None:
        self.grammar = DefaultToolsGrammar(B)

    def items(self) -> list[ResourceItem]:
        return [_item(n) for n in NAMES]

    def locks(self) -> dict[str, Any]:
        return {}

    def applied(self) -> set[str]:
        return set(NAMES)

    def notices(self) -> list[str]:
        return []

    def busy(self) -> bool:
        return False

    async def wait_until_idle(self) -> None:
        return None

    async def apply(self, enabled: set[str]) -> Any:
        raise AssertionError("not applied here")


@pytest.mark.parametrize(
    ("doc", "edits", "written"),
    [
        ({}, {"bash": False}, ["-bash"]),
        ({"defaultTools": ["-bash"]}, {"bash": True}, None),  # emptied modifier list: removed
        ({"defaultTools": []}, {"bash": True}, ["bash"]),  # [] stays replacing
        ({"defaultTools": ["read"]}, {"read": False}, []),  # written as [], not removed
        (
            {"defaultTools": ["read"]},
            {"weather": False, "grep": True},
            ["read", "-weather", "+grep"],
        ),
    ],
)
async def test_a_save_writes_the_list_keeping_an_explicit_empty_one(
    doc: dict[str, Any], edits: dict[str, bool], written: list[str] | None
) -> None:
    manager, storage = _manager(doc)
    selection = ResourceSelection(manager, global_base="", project_base="", project_trusted=True)
    kind: Any = _Kind()
    result = await selection.save(kind, "global", edits)
    assert result.ok, result.error
    on_disk = _global(storage)
    assert on_disk.get("defaultTools", None) == written
    if written is None:
        assert "defaultTools" not in on_disk
    snap = selection.snapshot(kind)
    for key, want in edits.items():
        assert next(s.saved for s in snap.states if s.item.key == key) is want


def test_launch_lists_read_the_loaded_settings_and_keep_presence() -> None:
    manager, _storage = _manager({"defaultTools": []})
    lists = default_tools_lists(manager, project_trusted=True)
    assert lists.global_list is not None and lists.global_list.present
    assert lists.global_list.entries == ()
    absent = default_tools_lists(_manager({})[0], project_trusted=True)
    assert absent.global_list is not None and not absent.global_list.present


# --- the policy builder --------------------------------------------------------------


def test_build_tool_policy_tags_a_typed_flag_and_a_profile_overlay() -> None:
    parsed = parse_args(["-xt", "bash,github__*", "--tools", "read,bash", "-p", "x"])
    policy = build_tool_policy(parsed, saved_off=frozenset({"edit"}))
    assert policy.exclude == ("bash", "github__*")
    assert policy.allow == frozenset({"read", "bash"}) and policy.allow_tag == "--tools"
    parsed.provided.discard("tools")  # what a profile's ``tools:`` overlay looks like
    assert build_tool_policy(parsed).allow_tag == "profile"
    assert build_tool_policy(parse_args(["--tools", "", "-p", "x"])).allow is None
    assert build_tool_policy(parse_args(["--no-tools", "-p", "x"])).no_tools


def test_exclude_tools_unions_when_repeated_and_drops_blanks() -> None:
    parsed = parse_args(["-xt", "bash, ls,", "--exclude-tools", "ls,github__*", "-p", "x"])
    assert parsed.exclude_tools == ["bash", "ls", "github__*"]


def test_an_exclude_tools_that_excludes_nothing_says_so() -> None:
    """Verify V7: pi's parse is kept — and a safety flag that excludes nothing warns."""

    joined = parse_args(["--exclude-tools=bash", "-p", "x"])
    assert joined.exclude_tools == [] and joined.unknown_flags == {"exclude-tools": "bash"}
    assert [d["type"] for d in joined.diagnostics] == ["warning"]
    assert "excludes nothing; write it with a space" in joined.diagnostics[0]["message"]
    flag = parse_args(["-xt", "--tools", "read", "-p", "x"])
    assert flag.exclude_tools == ["--tools"] and flag.tools == []
    message = flag.diagnostics[0]["message"]
    assert flag.diagnostics[0]["type"] == "warning"
    assert "took the next argument --tools as the tools to exclude" in message
    # A value that is not a registered flag is not echoed: it may be anything.
    other = parse_args(["-xt", "-secret-ish", "-p", "x"])
    assert "-secret-ish" not in other.diagnostics[0]["message"]
    assert parse_args(["-xt", "bash", "-p", "x"]).diagnostics == []


def test_launch_warnings_name_a_dead_pattern_a_beaten_tools_name_and_an_unknown_setting() -> None:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="mock"),
            tools=[AgentTool(name=n, description="") for n in ("read", "bash")],
            tool_policy=ToolPolicy(exclude=("bash", "gh__*")),
        )
    )
    parsed = parse_args(["-xt", "bash,gh__*", "--tools", "bash,read", "-p", "x"])
    manager, _ = _manager({"defaultTools": ["-nosuch", "read*"]})
    warnings = launch_tool_warnings(
        harness, parsed, default_tools_lists(manager, project_trusted=True)
    )
    assert '--exclude-tools "gh__*" matches no tool loaded at launch' in warnings
    assert any("--tools names bash, which --exclude-tools removes" in w for w in warnings)
    assert any('"-nosuch" names no tool loaded at launch' in w for w in warnings)
    assert any("exact names only" in w for w in warnings)
    assert not any('"bash" matches no tool' in w for w in warnings)


# --- the /tool kind ---------------------------------------------------------------


def _live_harness(policy: ToolPolicy) -> AgentHarness:
    ext = Extension(name="/x/weather.py")
    ext.tools["weather"] = AgentTool(name="weather", description="Look up the weather\nmore")
    status = Extension(name="StatusExtension")
    status.tools["aelix_status"] = AgentTool(name="aelix_status", description="Report state")
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="mock"),
            tools=[AgentTool(name=n, description=f"{n} it") for n in ("read", "bash", "edit")]
            + [AgentTool(name="demo__add", description="Add two numbers")],
            extensions=[ext, status],
            tool_policy=policy,
        )
    )


def _kind(harness: AgentHarness, **kwargs: Any) -> ToolKind:
    return ToolKind(harness=lambda: harness, builtin=frozenset({"read", "bash", "edit"}), **kwargs)


def test_the_kind_groups_tools_by_source_with_excluded_and_placeholder_rows() -> None:
    harness = _live_harness(ToolPolicy(exclude=("bash",)))
    statuses = [
        SimpleNamespace(name="demo", state="connected", config=None),
        SimpleNamespace(name="github", state="disabled", config="~/.aelix/agent/mcp.json"),
    ]
    errors = [SimpleNamespace(path="/home/u/.aelix/agent/extensions/broken.py", error="boom")]
    kind = _kind(harness, mcp_statuses=lambda: statuses, extension_errors=lambda: errors)
    items = kind.items()
    groups = [(i.group_label, i.key) for i in items]
    assert groups[:3] == [("BUILT-IN", "read"), ("BUILT-IN", "bash"), ("BUILT-IN", "edit")]
    labels = [label for label, _k in groups]
    assert "EXTENSION  weather.py" in labels and "EXTENSION  status (built-in)" in labels
    assert ("MCP  demo", "demo__add") in groups and ("MCP  github", "mcp:github") in groups
    assert ("EXTENSION  broken.py", "ext-failed:broken.py") in groups
    weather = next(i for i in items if i.key == "weather")
    assert weather.description == "Look up the weather"  # the first line, as a sentence
    locks = kind.locks()
    assert locks["bash"].tag == "excluded" and "this run only" in locks["bash"].reason
    assert locks["mcp:github"].kind == "unavailable" and locks["mcp:github"].tag == "server off"
    assert locks["mcp:github"].reason == 'see /mcp · "enabled": false in ~/.aelix/agent/mcp.json'
    assert locks["ext-failed:broken.py"].tag == "failed"
    roles = {i.key: i.role for i in items}
    assert roles["mcp:github"] == "placeholder" and roles["read"] == "item"


def test_the_kind_locks_and_notices_follow_the_flags() -> None:
    harness = _live_harness(ToolPolicy(allow=frozenset({"read", "weather"})))
    kind = _kind(harness)
    locks = kind.locks()
    assert locks["read"].value and locks["read"].tag == "--tools"
    assert (
        not locks["bash"].value and locks["bash"].reason == "not named by --tools (this run only)"
    )
    assert kind.notices() == ["--tools: only the tools it names can be on this run."]
    nt = _kind(_live_harness(ToolPolicy(no_tools=True)))
    assert nt.notices()[0].startswith("--no-tools: every tool is off")


async def test_apply_is_a_delta_that_emits_resources_update_with_tools() -> None:
    harness = _live_harness(ToolPolicy(saved_off=frozenset({"edit"})))
    events: list[Any] = []

    async def _on(event: Any, _ctx: Any) -> None:
        events.append(event)

    harness.hooks.on("resources_update", _on)
    harness.runtime.actions.set_active_tools(
        ["read", "edit", "weather", "demo__add"]
    )  # bash narrowed
    kind = _kind(harness)
    enabled = {"read", "bash", "edit", "weather", "aelix_status", "demo__add"}  # edit back on
    result = await kind.apply(enabled)
    assert result.applied and result.turned_on == ("edit",) and result.turned_off == ()
    assert "bash" not in harness._action_get_active_tools()  # the narrowing survived
    assert harness.tool_policy.saved_off == frozenset()
    assert events and "edit" in events[-1].resources["tools"]
    assert "edit" not in events[-1].previous_resources["tools"]
    result = await kind.apply(enabled - {"weather"})
    assert result.turned_off == ("weather",)
    assert "weather" not in harness._action_get_active_tools()
    assert (await kind.apply(enabled - {"weather"})).turned_off == ()  # nothing moved: no event


async def test_a_deferred_apply_waits_for_a_turn_an_extension_started(tmp_path: Path) -> None:
    import asyncio

    harness = _live_harness(ToolPolicy())
    manager, _ = _manager({})
    selection = ResourceSelection(manager, global_base="", project_base="", project_trusted=True)
    busy = {"on": True}
    idle = asyncio.Event()

    class _Busy(ToolKind):
        def busy(self) -> bool:
            return busy["on"]

        async def wait_until_idle(self) -> None:
            await idle.wait()

    kind = _Busy(harness=lambda: harness, builtin=frozenset({"read", "bash", "edit"}))
    saved = await selection.save(kind, "global", {"bash": False})
    assert saved.ok
    notes: list[Any] = []
    first = await selection.apply(kind, on_deferred=notes.append)
    assert first.deferred and first.turned_off == ("bash",)
    assert "bash" in harness._action_get_active_tools()  # nothing applied inside the turn
    busy["on"] = False
    idle.set()
    for _ in range(50):
        await asyncio.sleep(0)
        if notes:
            break
    assert notes and notes[0].applied and notes[0].turned_off == ("bash",)
    assert "bash" not in harness._action_get_active_tools()


async def test_a_project_save_overrides_the_global_file_and_says_which_decides() -> None:
    """Scope precedence: a project ``+bash`` over a global ``-bash`` is on, decided by the project."""

    manager, storage = _manager({"defaultTools": ["-bash", "-weather"]})
    selection = ResourceSelection(manager, global_base="", project_base="", project_trusted=True)
    kind: Any = _Kind()
    result = await selection.save(kind, "project", {"bash": True})
    assert result.ok, result.error
    project: list[Any] = [None]

    def _read(current: str | None) -> None:
        project[0] = json.loads(current) if current else {}

    storage.with_lock("project", _read)
    assert project[0] == {"defaultTools": ["+bash"]}  # a modifier list: it adds to the global one
    snap = selection.snapshot(kind)
    by_key = {s.item.key: s for s in snap.states}
    assert by_key["bash"].saved and by_key["bash"].decided_by == "project"
    assert not by_key["weather"].saved and by_key["weather"].decided_by == "global"
    # A global edit the project file overrides is reported as a mismatch.
    again = await selection.save(kind, "global", {"bash": False})
    assert again.ok
    assert again.mismatches == ("bash: saved, but this project's file keeps it on here",)


async def test_a_refused_global_save_writes_nothing_and_says_why() -> None:
    """Verify V1 through the service: the project's ``[]`` is read from disk for a global save."""

    manager, storage = _manager({"other": 1})
    storage.with_lock("project", lambda _c: json.dumps({"defaultTools": []}))
    selection = ResourceSelection(manager, global_base="", project_base="", project_trusted=True)
    kind: Any = _Kind()
    before = selection.snapshot(kind)
    result = await selection.save(kind, "global", {"weather": False})
    assert not result.ok and result.error is not None
    assert "save it to This project instead" in result.error
    assert _global(storage) == {"other": 1}  # not written
    after = selection.snapshot(kind)
    assert [s.saved for s in after.states] == [s.saved for s in before.states]
    # The same toggle saved to the project file is the plain edit.
    saved = await selection.save(kind, "project", {"weather": False})
    assert saved.ok and saved.changed == (("weather", False),)


def test_a_child_gets_the_runs_patterns_and_the_names_the_setting_keeps_off() -> None:
    """Owner decision B: the parent's final saved-off set rides ``--exclude-tools``, suppress-only."""

    from aelix_coding_agent.core.tool_selection import delegated_exclusions

    policy = ToolPolicy(
        exclude=("bash", "gh__*"),
        allow=frozenset({"read", "grep", "edit"}),
        saved_off=frozenset({"weather", "read", "gh__x", "ls", "a b", "-x", "x,y"}),
    )
    # read: ``--tools`` names it, so it is on — not forwarded. gh__x: the
    # pattern already removes it. "a b", "-x", "x,y": no entry can carry them.
    assert delegated_exclusions(policy) == ("bash", "gh__*", "ls", "weather")
    assert delegated_exclusions(ToolPolicy()) == ()
    # Turned back on (a /tool apply, RPC): it leaves the saved-off set, and the child has it.
    assert delegated_exclusions(replace(policy, saved_off=frozenset({"ls"}))) == (
        "bash",
        "gh__*",
        "ls",
    )


async def test_the_real_kind_is_busy_while_a_build_holds_the_gate_or_a_turn_runs() -> None:
    """Verify V4: ``ToolKind.busy`` itself, not a subclass — the gate and the harness's phase.

    A held :class:`ApplyGate` (a harness being built) defers the apply until it
    opens; so does a harness whose ``is_idle`` is False (a turn an extension or
    a background child started) until it is idle again.
    """

    import asyncio

    from aelix_coding_agent.core.resource_selection import ApplyGate

    harness = _live_harness(ToolPolicy())
    gate = ApplyGate()
    kind = _kind(harness, gate=gate)
    manager, _ = _manager({})
    selection = ResourceSelection(manager, global_base="", project_base="", project_trusted=True)
    assert not kind.busy()
    assert (await selection.save(kind, "global", {"bash": False})).ok
    notes: list[Any] = []
    with gate:
        assert kind.busy()
        first = await selection.apply(kind, on_deferred=notes.append)
        assert first.deferred and "bash" in harness._action_get_active_tools()
    for _ in range(20):
        await asyncio.sleep(0)
    assert notes and notes[0].applied and notes[0].turned_off == ("bash",)
    assert not kind.busy()

    # A turn running on the live harness: the phase ``is_idle`` reads, and the
    # event ``wait_for_idle`` waits on, as ``prompt()`` sets them.
    harness._phase = "turn"
    harness._idle_event.clear()
    assert not harness.is_idle and kind.busy()
    assert (await selection.save(kind, "global", {"bash": True})).ok
    notes.clear()
    second = await selection.apply(kind, on_deferred=notes.append)
    assert second.deferred and "bash" not in harness._action_get_active_tools()
    harness._phase = "idle"
    harness._idle_event.set()
    for _ in range(40):
        await asyncio.sleep(0.01)
        if notes:
            break
    assert notes and notes[0].applied and notes[0].turned_on == ("bash",)


async def test_enter_during_a_turn_with_nothing_pending_defers_nothing() -> None:
    """TUI sub-lane: nothing changed, so nothing "applies when the turn ends".

    And a deferral an earlier Enter left is dropped once the files say what is
    applied again: it would report an apply of nothing after the turn.
    """

    from aelix_coding_agent.core.resource_selection import ApplyGate
    from aelix_coding_agent.tui.selection_panel import summary

    harness = _live_harness(ToolPolicy())
    gate = ApplyGate()
    kind = _kind(harness, gate=gate)
    manager, _ = _manager({})
    selection = ResourceSelection(manager, global_base="", project_base="", project_trusted=True)
    notes: list[Any] = []
    with gate:
        idle_apply = await selection.apply(kind, on_deferred=notes.append)
        assert idle_apply.applied and not idle_apply.deferred
        assert selection._deferred == {}
        text = summary("Tools", None, idle_apply, effect_note="From your next message.").plain
        assert "applies when it ends" not in text and "next message" not in text
        # An earlier Enter saved bash off and deferred; the next one put it back.
        assert (await selection.save(kind, "global", {"bash": False})).ok
        assert (await selection.apply(kind, on_deferred=notes.append)).deferred
        assert (await selection.save(kind, "global", {"bash": True})).ok
        again = await selection.apply(kind, on_deferred=notes.append)
        assert again.applied and not again.deferred and selection._deferred == {}
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0)
    assert notes == [] and "bash" in harness._action_get_active_tools()
