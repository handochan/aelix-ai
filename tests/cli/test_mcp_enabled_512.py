"""#512 (ADR-0282) — ``"enabled": false`` in ``mcp.json``.

Pins, at the loader and at the one seam every mode connects through
(``cli/entry.py``): a disabled entry is listed but never connected and never
contributes tools; a non-boolean ``enabled`` is warned and skipped, as pi skips
it; an entry with no ``command`` / ``url`` is warned at load (pi's project
override shape, which overrides nothing here); and an ``mcp.json`` entry keeps
the name it shares with a manifest server whether it is enabled or not.

The spawn tests use a real ``McpClientManager`` and a real child process that
writes a marker file, because "not connected" is only worth something if
"never started" is what was measured.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.cli import config
from aelix_coding_agent.cli import entry as entry_mod

_ARGV = ["--no-session", "--print", "--provider", "anthropic", "--model", "x"]


class _FakePipedStdin:
    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    (tmp_path / "proj").mkdir()
    return tmp_path


def _write_mcp(path: Path, servers: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def _global(env: Path, servers: dict) -> Path:
    return _write_mcp(env / "agent" / "mcp.json", servers)


# --- the loader -----------------------------------------------------------------


def test_enabled_false_is_listed_and_not_connected(env: Path) -> None:
    _global(
        env,
        {
            "on": {"command": "srv-on"},
            "explicit": {"command": "srv", "enabled": True},
            "off": {"command": "srv-off", "args": ["-x"], "enabled": False},
        },
    )
    cfg = config.load_mcp_config(str(env / "proj"))
    assert [c.name for c in cfg.contribs] == ["on", "explicit"]
    assert [c.name for c in cfg.disabled] == ["off"]
    assert cfg.disabled[0].args == ["-x"], "the entry is kept whole, to be turned back on"
    assert cfg.warnings == []
    assert cfg.source == "global"
    # The pre-#512 3-tuple is the CONNECT list: a caller of it cannot start one.
    contribs, warnings, source = config.load_mcp_server_contribs(str(env / "proj"))
    assert [c.name for c in contribs] == ["on", "explicit"]
    assert (warnings, source) == ([], "global")


@pytest.mark.parametrize(
    ("value", "shown"),
    [("false", '"false"'), (0, "0"), (1, "1"), (None, "null"), ([], "[]"), ({}, "{}")],
)
def test_a_non_boolean_enabled_is_warned_and_skipped(env: Path, value, shown: str) -> None:
    """pi rejects the entry (``validateMcpServerConfig``: "enabled must be a
    boolean"); skipping is also the side that starts nothing. ``"false"`` is the
    case that matters: treated as truthy, it would START the server the user
    meant to turn off."""

    _global(env, {"odd": {"command": "srv", "enabled": value}, "fine": {"command": "srv"}})
    cfg = config.load_mcp_config(str(env / "proj"))
    assert [c.name for c in cfg.contribs] == ["fine"], "startup continues with the rest"
    assert cfg.disabled == []
    (warning,) = cfg.warnings
    assert "'odd'" in warning
    assert '"enabled" must be true or false' in warning
    assert f"not {shown}" in warning
    assert "not started" in warning


def test_a_long_non_boolean_enabled_is_bounded_in_the_warning(env: Path) -> None:
    _global(env, {"odd": {"command": "srv", "enabled": "x" * 500}})
    (warning,) = config.load_mcp_config(str(env / "proj")).warnings
    assert len(warning) < len(str(env)) + 200


@pytest.mark.parametrize(
    "spec",
    [{"transport": "stdio"}, {"transport": "http"}, {"transport": "sse"}, {"args": ["x"]}],
)
def test_an_entry_with_no_command_or_url_is_warned_at_load(env: Path, spec: dict) -> None:
    """Before #512 such an entry reached the manager and failed at connect with
    a transport error; a disabled one would have been listed as if it were a
    server. Now it is said at load, the way pi says it."""

    _global(env, {"empty": spec})
    cfg = config.load_mcp_config(str(env / "proj"))
    assert cfg.contribs == [] and cfg.disabled == []
    (warning,) = cfg.warnings
    assert '\'empty\' needs "command" (stdio) or "url" (http/sse); not started' in warning
    assert "global server" not in warning, "the override note is for the project file"


def test_pis_project_override_shape_says_it_overrides_nothing(env: Path) -> None:
    """pi's documented way to turn a global server off in one project
    (``docs/mcp.md``: ``{"internal-tools": {"enabled": false}}`` in the project
    file) does NOT work here: aelix reads one file, so the project file
    replaces the global one. The warning has to say that, or the user sees
    every global server vanish and blames the wrong thing."""

    _global(env, {"internal-tools": {"command": "srv"}})
    _write_mcp(env / "proj" / ".aelix" / "mcp.json", {"internal-tools": {"enabled": False}})
    cfg = config.load_mcp_config(str(env / "proj"))
    assert cfg.source == "project"
    assert cfg.contribs == [] and cfg.disabled == []
    (warning,) = cfg.warnings
    assert "aelix reads only this file here" in warning
    from aelix_coding_agent.core.mcp_servers import config_label

    global_label = config_label(env / "agent" / "mcp.json", "global")
    assert f"not {global_label} as well" in warning, "it names the file it replaced"
    assert "cannot change or turn off a global server" in warning


def test_the_label_names_the_project_file_relative(env: Path) -> None:
    _write_mcp(env / "proj" / ".aelix" / "mcp.json", {"a": {"command": "srv", "enabled": False}})
    cfg = config.load_mcp_config(str(env / "proj"))
    assert cfg.label == ".aelix/mcp.json"
    (off,) = cfg.disabled_servers()
    assert (off.contrib.name, off.config) == ("a", ".aelix/mcp.json")
    assert config.load_mcp_config(str(env / "nowhere")).label == ""


# --- the entry seam -------------------------------------------------------------


class _Spy:
    """Records what reached ``McpClientManager`` — the last gate before a spawn."""

    calls: list[tuple[list[str], list[str]]]

    def __init__(self, contribs, *, disabled=()):
        _Spy.calls.append(([c.name for c in contribs], [d.contrib.name for d in disabled]))

    async def connect_all(self):
        return []

    async def collect_agent_tools(self):
        return []

    async def disconnect_all(self):
        return None


@pytest.fixture()
def spy(monkeypatch: pytest.MonkeyPatch) -> type[_Spy]:
    _Spy.calls = []
    monkeypatch.setattr(entry_mod, "McpClientManager", _Spy)
    return _Spy


async def test_a_disabled_entry_reaches_the_manager_only_as_disabled(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch
) -> None:
    _global(env, {"on": {"command": "srv"}, "off": {"command": "srv", "enabled": False}})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    assert spy.calls == [(["on"], ["off"])]


async def test_only_disabled_entries_still_build_an_inventory(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise ``/mcp`` would say "No MCP servers configured." about a file
    that configures one."""

    _global(env, {"off": {"command": "srv", "enabled": False}})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    assert spy.calls == [([], ["off"])]


async def test_an_untrusted_project_file_lists_nothing_and_notices_only_real_skips(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _write_mcp(env / "proj" / ".aelix" / "mcp.json", {"off": {"command": "srv", "enabled": False}})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    err = capsys.readouterr().err
    assert spy.calls == [], "an untrusted file has no say, its disabled entries included"
    assert "servers skipped in an untrusted directory" not in err, "nothing would have started"


def _pack(extensions_dir: Path, server: str) -> None:
    pkg = extensions_dir / "shadow-pack"
    pkg.mkdir(parents=True)
    (pkg / "aelix-plugin.toml").write_text(
        textwrap.dedent(f"""
            [plugin]
            id = "shadow-pack"
            name = "Shadow Pack"
            version = "0.1.0"
            description = "Declares a stdio MCP server"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/shadow-pack"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [activation]
            on_startup_finished = true

            [[contributes.mcp_servers]]
            name = "{server}"
            transport = "stdio"
            command = "manifest-srv"

            [capabilities]
            shell_exec = true
        """).strip(),
        encoding="utf-8",
    )


@pytest.mark.parametrize("enabled", [False, True])
async def test_an_mcp_json_entry_keeps_its_name_from_a_manifest_server(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys, enabled: bool
) -> None:
    """MEASURED before #512 with this test's fixture: an enabled mcp.json entry
    already won (``McpClientManager``'s last-wins dict) while the manifest's
    "starts MCP server" notice still printed; and filtering only enabled entries
    would have let a DISABLED one hand its name to the manifest server, which
    would then start. pi: "a server in mcp.json takes precedence over a
    registered server of the same name" (``extensions/mcp/index.ts``)."""

    proj = env / "proj"
    _pack(proj / ".aelix" / "extensions", "shared")
    _write_mcp(proj / ".aelix" / "mcp.json", {"shared": {"command": "json-srv", "enabled": enabled}})
    monkeypatch.chdir(proj)
    await entry_mod._async_main([*_ARGV, "--approve"])
    err = capsys.readouterr().err
    assert spy.calls == [(["shared"], []) if enabled else ([], ["shared"])]
    notices = [ln for ln in err.splitlines() if "'shared'" in ln and "Notice:" in ln]
    assert len(notices) == 1, err
    (notice,) = notices
    assert "'shadow-pack'" in notice and "is not started" in notice
    assert ".aelix/mcp.json defines a server of the same name" in notice
    assert ("turns it off" in notice) is (not enabled)
    assert "starts MCP server 'shared'" not in err, "the old notice claimed a start"


async def test_a_manifest_server_with_its_own_name_is_unaffected(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    proj = env / "proj"
    _pack(proj / ".aelix" / "extensions", "pack-only")
    _write_mcp(proj / ".aelix" / "mcp.json", {"other": {"command": "srv", "enabled": False}})
    monkeypatch.chdir(proj)
    await entry_mod._async_main([*_ARGV, "--approve"])
    err = capsys.readouterr().err
    assert spy.calls == [(["pack-only"], ["other"])]
    assert "plugin 'shadow-pack' starts MCP server 'pack-only'" in err


# --- never started, measured ----------------------------------------------------


def _marker_server(marker: Path, *, enabled: bool) -> dict:
    """A stdio "server" that writes ``marker`` and exits: started ⇔ marker exists."""

    return {
        "command": sys.executable,
        "args": ["-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('x')", str(marker)],
        "enabled": enabled,
    }


async def test_a_disabled_server_is_never_spawned(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    on, off = env / "on.marker", env / "off.marker"
    _global(env, {"on": _marker_server(on, enabled=True), "off": _marker_server(off, enabled=False)})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    assert on.exists(), "anti-vacuity: the enabled sibling WAS started by the same path"
    assert not off.exists()
    err = capsys.readouterr().err
    assert "MCP server failed" in err and "'off'" not in err.split("MCP server failed", 1)[1]


# --- the guide's examples -------------------------------------------------------

_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "guides" / "mcp-servers.md"


def test_every_guide_example_loads_without_a_warning(env: Path) -> None:
    """``tests/test_guide_examples_are_valid.py``'s rule, for this guide: an
    example a user copies is checked by the loader the product runs."""

    import re

    blocks = re.findall(r"```json\n(.*?)```", _GUIDE.read_text(encoding="utf-8"), re.S)
    assert len(blocks) >= 2
    loaded = []
    for index, block in enumerate(blocks):
        (env / "agent").mkdir(exist_ok=True)
        (env / "agent" / "mcp.json").write_text(block, encoding="utf-8")
        cfg = config.load_mcp_config(str(env / "proj"))
        assert cfg.warnings == [], (index, cfg.warnings)
        assert cfg.contribs or cfg.disabled, index
        loaded.append(cfg)
    # The example that turns a server off really does.
    assert [c.name for c in loaded[-1].disabled] == ["filesystem"]


@pytest.mark.parametrize("entry", [{"command": "srv", "enabled": "false"}, {"enabled": False}])
async def test_a_skipped_entry_still_keeps_its_name(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys, entry: dict
) -> None:
    """ADR-0282 D5, a deliberate step past pi (whose invalid entries claim no
    name): the user who typed ``"enabled": "false"`` meant OFF, and the entry
    being skipped must not let an extension's server of that name start."""

    proj = env / "proj"
    _pack(proj / ".aelix" / "extensions", "shared")
    _write_mcp(proj / ".aelix" / "mcp.json", {"shared": entry})
    monkeypatch.chdir(proj)
    await entry_mod._async_main([*_ARGV, "--approve"])
    err = capsys.readouterr().err
    assert spy.calls == [], "nothing to connect and nothing to list"
    assert "'shared'" in err and "not started" in err
    assert "(skipped: see its warning above)" in err


async def test_an_untrusted_project_file_claims_no_name(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloned repo's untrusted ``.aelix/mcp.json`` must not be able to switch
    off a server from the user's own global extension by naming it."""

    _pack(env / "agent" / "extensions", "shared")  # the user's global tier
    _write_mcp(env / "proj" / ".aelix" / "mcp.json", {"shared": {"enabled": "false"}})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    assert spy.calls == [(["shared"], [])], spy.calls


# --- review round 1 (2026-11-01): where the names count -------------------------


def _marker_pack(extensions_dir: Path, server: str, marker: Path) -> None:
    """A manifest pack whose ``server`` is the marker-writing stdio "server"."""

    pkg = extensions_dir / "shadow-pack"
    pkg.mkdir(parents=True)
    args = json.dumps(
        ["-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('x')", str(marker)]
    )
    (pkg / "aelix-plugin.toml").write_text(
        textwrap.dedent(f"""
            [plugin]
            id = "shadow-pack"
            name = "Shadow Pack"
            version = "0.1.0"
            description = "Declares a stdio MCP server"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/shadow-pack"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [activation]
            on_startup_finished = true

            [[contributes.mcp_servers]]
            name = "{server}"
            transport = "stdio"
            command = {json.dumps(sys.executable)}
            args = {args}

            [capabilities]
            shell_exec = true
        """).strip(),
        encoding="utf-8",
    )


async def test_an_untrusted_project_file_leaves_the_global_files_names_counting(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """#512 verify V1. The guide's way to keep an extension's server off —
    an entry of its name with ``"enabled": false`` in the global file — used to
    stop working in any directory with an untrusted ``.aelix/mcp.json``: that
    file is the one picked, it was then dropped whole, and no file's names
    counted. ``{"mcpServers": {}}`` was enough. Now the global file's names
    still count there; nothing from either file starts."""

    _pack(env / "agent" / "extensions", "shared")  # the user's global tier
    _global(env, {"shared": {"command": "srv", "enabled": False}, "mine": {"command": "srv"}})
    _write_mcp(env / "proj" / ".aelix" / "mcp.json", {})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    err = capsys.readouterr().err
    assert spy.calls == [], "neither the extension's server nor the global file's starts"
    (notice,) = [ln for ln in err.splitlines() if "'shared'" in ln]
    assert "'shadow-pack'" in notice and "is not started" in notice
    from aelix_coding_agent.core.mcp_servers import config_label

    label = config_label(env / "agent" / "mcp.json", "global")
    assert f"{label} defines a server of the same name and turns it off" in notice


async def test_the_global_files_skipped_entry_keeps_its_name_untrusted_too(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _pack(env / "agent" / "extensions", "shared")
    _global(env, {"shared": {"command": "srv", "enabled": "false"}})
    _write_mcp(env / "proj" / ".aelix" / "mcp.json", {})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    err = capsys.readouterr().err
    assert spy.calls == []
    assert "(an entry it skips as invalid)" in err, "its warning was not printed here"
    assert "must be true or false" not in err, "the global file is read for names only"


def _record_at_connect(spy_cls: type[_Spy]):
    """The launch's record as it stands when the manager is built."""

    from aelix_coding_agent.core import mcp_servers

    seen: list[object] = []
    original = spy_cls.__init__

    def init(self, contribs, *, disabled=()):
        seen.append(mcp_servers._launch_names)
        original(self, contribs, disabled=disabled)

    return seen, init


async def test_a_launch_records_what_it_turned_off_and_claimed_for_its_children(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0282 D9, the parent half: set before anything connects, from the
    file whose names count, and cleared when the launch ends."""

    from aelix_coding_agent.core import mcp_servers
    from aelix_coding_agent.core.mcp_servers import McpNameRecord

    seen, init = _record_at_connect(spy)
    monkeypatch.setattr(spy, "__init__", init)
    _write_mcp(
        env / "proj" / ".aelix" / "mcp.json",
        {
            "on": {"command": "srv"},
            "off": {"command": "srv", "enabled": False},
            "typo": {"command": "srv", "enabled": "false"},
        },
    )
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main([*_ARGV, "--approve"])
    assert seen == [
        McpNameRecord(off=frozenset({"off", "typo"}), claimed=frozenset({"on", "off", "typo"}))
    ]
    assert mcp_servers._launch_names is None, "the launch is over"


async def test_a_delegated_child_keeps_off_what_its_parent_turned_off(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """ADR-0282 D9, the child half (#512 review 512-01, verify V3): the child
    reads its OWN file — here the global one, with ``xsrv`` enabled — and the
    parent's record says ``xsrv`` is off and ``shared`` is claimed. It starts
    neither, keeps ``keep``, and hands the union on to a child of its own."""

    import os

    from aelix_agents.print_channel import build_child_env
    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.core.mcp_servers import MCP_PARENT_NAMES_ENV, McpNameRecord

    seen, init = _record_at_connect(spy)
    profile = AgentProfile(name="kid", description="d", body="b", file_path="/x/kid.md", scope="user")
    in_environ: list[str | None] = []
    handed_on: list[str | None] = []

    def init_and_look(self, contribs, *, disabled=()):
        in_environ.append(os.environ.get(MCP_PARENT_NAMES_ENV))
        handed_on.append(build_child_env(profile, base=dict(os.environ)).get(MCP_PARENT_NAMES_ENV))
        init(self, contribs, disabled=disabled)

    monkeypatch.setattr(spy, "__init__", init_and_look)
    _pack(env / "agent" / "extensions", "shared")
    _global(env, {"xsrv": {"command": "srv"}, "keep": {"command": "srv"}})
    parent = McpNameRecord(off=frozenset({"xsrv"}), claimed=frozenset({"shared"}))
    monkeypatch.setenv(MCP_PARENT_NAMES_ENV, parent.to_env())
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    err = capsys.readouterr().err
    assert spy.calls == [(["keep"], [])], err
    # Verify R3: the variable is named, not a parent — the user's own
    # environment, or a .env their AELIX_DOTENV_ALLOW lists, can set it too.
    said = "$AELIX_MCP_PARENT_NAMES (the names a delegating aelix passes on)"
    assert f"MCP server 'xsrv' is not started: {said} keeps it off." in err
    assert f"declares MCP server 'shared', which is not started: {said} reserves that name" in err
    assert "delegated this agent" not in err
    union = McpNameRecord(off=frozenset({"xsrv"}), claimed=frozenset({"xsrv", "keep", "shared"}))
    assert seen == [union]
    # Verify R4: read once and removed, so this agent's bash and api.exec do not
    # inherit it — and its own delegation still hands the union on.
    assert in_environ == [None]
    assert [McpNameRecord.from_env(raw) for raw in handed_on] == [union]


async def test_a_record_only_suppresses(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name the parent claimed but did NOT turn off does not stop the child's
    own entry of that name, and nothing in a record can add a server."""

    from aelix_coding_agent.core.mcp_servers import MCP_PARENT_NAMES_ENV, McpNameRecord

    _global(env, {"both": {"command": "srv"}})
    record = McpNameRecord(claimed=frozenset({"both", "ghost"}))
    monkeypatch.setenv(MCP_PARENT_NAMES_ENV, record.to_env())
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    assert spy.calls == [(["both"], [])]


async def test_a_launch_whose_names_pass_the_bound_says_how_many_it_leaves_out(
    env: Path, spy: type[_Spy], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """#512 verify R5: past 16 KiB of names the record leaves names out rather
    than fail every delegation's spawn — and the launch says so."""

    from aelix_coding_agent.core import mcp_servers
    from aelix_coding_agent.core.mcp_servers import _RECORD_MAX_BYTES

    seen, init = _record_at_connect(spy)
    monkeypatch.setattr(spy, "__init__", init)
    _global(env, {f"s{i:04d}-" + "n" * 60: {"command": "srv", "enabled": False} for i in range(400)})
    monkeypatch.chdir(env / "proj")
    await entry_mod._async_main(_ARGV)
    err = capsys.readouterr().err
    (record,) = seen
    assert isinstance(record, mcp_servers.McpNameRecord)
    assert len(record.to_env()) <= _RECORD_MAX_BYTES
    unpassed = record.unpassed()
    assert unpassed > 0
    assert (
        f"Warning: MCP config: {unpassed} server name(s) are not passed to delegated agents "
        "(the list is capped at 16 KiB)" in err
    )


@pytest.mark.parametrize("channel", ["print", "rpc"])
@pytest.mark.parametrize("entry", [{"enabled": False}, {"enabled": "false"}])
def test_a_real_child_on_either_channel_does_not_start_what_the_parent_kept_off(
    env: Path, monkeypatch: pytest.MonkeyPatch, channel: str, entry: dict
) -> None:
    """#512 review 512-01, across the process boundary. The parent trusts the
    project, whose ``.aelix/mcp.json`` keeps ``shared`` off; the user's global
    extension declares ``shared``. The child gets ``--no-approve`` (so the
    project file is untrusted for it) and no ``$AELIX_MCP_CONFIG``. With the
    parent's record in the environment the child builders write, its
    ``shared`` never starts; without it (the control), it does."""

    import os
    import subprocess

    from aelix_agents.print_channel import build_child_argv, build_child_env
    from aelix_agents.rpc_channel import build_rpc_child_argv, build_rpc_child_env
    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.core import mcp_servers
    from aelix_coding_agent.core.mcp_servers import McpNameRecord

    proj = env / "proj"
    marker = env / "shared.marker"
    _marker_pack(env / "agent" / "extensions", "shared", marker)
    _write_mcp(proj / ".aelix" / "mcp.json", {"shared": {"command": "json-srv", **entry}})
    prompt = env / "prompt.txt"
    prompt.write_text("probe", encoding="utf-8")
    profile = AgentProfile(
        name="probe",
        description="probe",
        body="probe",
        file_path=str(env / "probe.md"),
        scope="user",
        model="x",
        provider="probe512-nonexistent",
        inherit_extensions=True,
    )
    build_argv, build_env = (
        (build_child_argv, build_child_env)
        if channel == "print"
        else (build_rpc_child_argv, build_rpc_child_env)
    )
    argv = build_argv(
        profile,
        prompt_path=str(prompt),
        task="probe",
        permission_mode=PermissionMode.DEFAULT,
        child_cwd=str(proj),
        parent_cwd=str(proj),
    )
    assert "--no-approve" in argv, "the premise: the child does not trust the project"

    def run(record: McpNameRecord | None) -> bool:
        marker.unlink(missing_ok=True)
        monkeypatch.setattr(mcp_servers, "_launch_names", record)
        child_env = build_env(profile, base=dict(os.environ))
        # Bytes, not text: on Windows ``text=True`` decodes with the ANSI code
        # page, and the child's notices are UTF-8.
        subprocess.run(argv, cwd=proj, env=child_env, input=b"", capture_output=True, timeout=60)
        return marker.exists()

    assert run(None) is True, "anti-vacuity: without the record the child starts it"
    # What the parent's launch records for this file (pinned above).
    assert run(McpNameRecord(off=frozenset({"shared"}), claimed=frozenset({"shared"}))) is False


# --- review round 1: what a warning may say ------------------------------------


def test_an_invalid_entrys_warning_names_the_field_never_its_value(env: Path) -> None:
    """#512 review 512-02: ``str(ValidationError)`` quotes ``input_value``, so a
    url given as a list put the endpoint and its token into the warning that
    the startup and ``aelix status`` print."""

    _global(
        env,
        {
            "docs": {"url": ["https://example.test/mcp?token=PLANTED_SECRET_512"]},
            "tok": {"command": "srv", "env": {"API_TOKEN": 12345}},
        },
    )
    warnings = config.load_mcp_config(str(env / "proj")).warnings
    text = "\n".join(warnings)
    assert "PLANTED_SECRET_512" not in text and "example.test" not in text
    assert "12345" not in text and "input_value" not in text
    assert any("'docs': url: Input should be a valid string; not started" in w for w in warnings)
    assert any("'tok': env.API_TOKEN: Input should be a valid string" in w for w in warnings)


@pytest.mark.parametrize(
    ("spec", "warned"),
    [
        ({"command": "srv", "disabled": True}, True),
        ({"command": "srv", "disabled": "yes"}, True),
        ({"command": "srv", "disabled": False}, False),
        ({"command": "srv", "disabled": True, "enabled": False}, False),
    ],
)
def test_clines_disabled_key_is_said_not_honoured(env: Path, spec: dict, warned: bool) -> None:
    """#512 verify V8: ``"disabled": true`` is another client's spelling; aelix
    reads ``enabled``, so that server STARTS — and used to start silently."""

    _global(env, {"x": spec})
    cfg = config.load_mcp_config(str(env / "proj"))
    assert [c.name for c in cfg.contribs] == ([] if spec.get("enabled") is False else ["x"])
    if warned:
        (warning,) = cfg.warnings
        # Verify R2: not "starts" — trust and an inherited record decide that.
        assert '\'x\': "disabled" does not turn it off — aelix reads "enabled"' in warning
        assert "starts" not in warning
        assert '"enabled": false to keep it off' in warning
    else:
        assert cfg.warnings == []
