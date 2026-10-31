"""#512 (ADR-0282) — the one MCP server state vocabulary.

``core/mcp_servers.py`` owns the words ``/mcp``, ``/extension`` and
``aelix status`` print, the rule that picks one, and the sentence that says how
to turn a disabled server back on. These pin that the rule is what ADR-0282
says, that every word has a meaning written down, and that the hint names the
file and the key in rows short enough for an 80-column viewer that clips.
"""

from __future__ import annotations

import json
import subprocess
import sys
import typing
from pathlib import Path

import pytest
from aelix_agent_core.contracts import McpServerContrib
from aelix_coding_agent.core.mcp_servers import (
    STATE_MEANINGS,
    DisabledMcpServer,
    McpServerState,
    McpServerStatus,
    config_label,
    config_statuses,
    connection_status,
    enable_hint,
    runtime_statuses,
)

from tests.env_sandbox import sandbox_home


class _Conn:
    def __init__(self, name: str, transport: str = "stdio", *, connected: bool) -> None:
        self.name = name
        self.transport = transport
        self.connected = connected
        self._contrib = McpServerContrib(name=name, transport="stdio", command=f"/bin/{name}")


class _Manager:
    def __init__(self, conns, *, failed=(), disabled=()) -> None:
        self.connections = {c.name: c for c in conns}
        self.failed = frozenset(failed)
        self.disabled = tuple(disabled)


def _off(name: str, config: str = "~/.aelix/agent/mcp.json") -> DisabledMcpServer:
    return DisabledMcpServer(
        contrib=McpServerContrib(name=name, transport="http", url="https://example.test/mcp"),
        config=config,
    )


def test_every_state_has_a_written_meaning() -> None:
    """A word added to the Literal without a meaning is a word no doc explains."""

    assert set(STATE_MEANINGS) == set(typing.get_args(McpServerState))
    assert set(typing.get_args(McpServerState)) == {
        "connected",
        "failed",
        "disconnected",
        "disabled",
        "enabled",
    }


def test_connected_wins_then_failed_then_disconnected() -> None:
    manager = _Manager(
        [_Conn("up", connected=True), _Conn("broke", connected=False), _Conn("gone", connected=False)],
        failed={"broke", "up"},  # a name that failed once and is connected now is connected
    )
    states = {s.name: s.state for s in runtime_statuses(manager)}
    assert states == {"up": "connected", "broke": "failed", "gone": "disconnected"}


def test_disabled_servers_are_listed_last_with_their_file() -> None:
    manager = _Manager([_Conn("up", connected=True)], disabled=[_off("quiet")])
    rows = runtime_statuses(manager)
    assert [(r.name, r.state) for r in rows] == [("up", "connected"), ("quiet", "disabled")]
    assert rows[1].transport == "http"
    assert rows[1].config == "~/.aelix/agent/mcp.json"
    assert rows[1].endpoint == "https://example.test/mcp"


def test_a_manager_shaped_without_failed_or_disabled_still_reads() -> None:
    """The pre-#512 duck shape (``connections`` only) — test doubles, and #25."""

    class _Bare:
        connections = {"x": _Conn("x", connected=False)}

    assert [(r.name, r.state) for r in runtime_statuses(_Bare())] == [("x", "disconnected")]
    assert runtime_statuses(None) == []
    assert connection_status(object()).name == "?"


def test_config_statuses_say_enabled_and_disabled() -> None:
    on = McpServerContrib(name="on", transport="stdio", command="srv")
    rows = config_statuses([on], [_off("off")])
    assert [(r.name, r.state) for r in rows] == [("on", "enabled"), ("off", "disabled")]


def test_the_hint_names_the_file_and_the_key_once_per_file() -> None:
    rows = [
        McpServerStatus("a", "stdio", "disabled", config="~/.aelix/agent/mcp.json"),
        McpServerStatus("b", "stdio", "disabled", config="~/.aelix/agent/mcp.json"),
        McpServerStatus("c", "stdio", "connected"),
    ]
    hint = enable_hint(rows)
    assert len(hint) == 2, "one file → one two-line hint, not one per server"
    assert '"enabled": false' in hint[0] and "~/.aelix/agent/mcp.json" in hint[0]
    assert "true" in hint[1] and "delete the key" in hint[1]
    assert enable_hint([McpServerStatus("c", "stdio", "connected")]) == []


def test_the_hint_rows_fit_an_80_column_viewer_that_clips() -> None:
    """``/extension``'s tabbed viewer does not wrap; it indents rows by two.

    The project and global labels are what a user sees in the usual case."""

    for label in (".aelix/mcp.json", "~/.aelix/agent/mcp.json"):
        for line in enable_hint([McpServerStatus("a", "stdio", "disabled", config=label)]):
            assert len("  " + line) <= 78, line


def test_config_label_shortens_the_usual_files(tmp_path: Path, monkeypatch) -> None:
    home = sandbox_home(monkeypatch, tmp_path / "home")
    cwd = tmp_path / "proj"
    assert config_label(cwd / ".aelix" / "mcp.json", "project", cwd=cwd) == ".aelix/mcp.json"
    assert (
        config_label(home / ".aelix" / "agent" / "mcp.json", "global") == "~/.aelix/agent/mcp.json"
    )
    elsewhere = tmp_path / "agent" / "mcp.json"
    assert config_label(elsewhere, "global") == str(elsewhere)
    # $AELIX_MCP_CONFIG is the user's own spelling of a path: printed as given.
    assert config_label(home / "x.json", "env") == str(home / "x.json")


def test_the_module_imports_no_rich_and_no_mcp_sdk() -> None:
    """``aelix status`` reads it; the MCP SDK imports ``rich`` (measured), and
    ``rich`` is the optional ``[tui]`` extra. Subprocess: this session already
    imported both."""

    code = (
        "import sys, aelix_coding_agent.core.mcp_servers, aelix_coding_agent.cli.config;"
        "print(sorted({m.split('.')[0] for m in sys.modules} & {'rich', 'mcp', 'prompt_toolkit'}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    assert out.strip() == "[]"


# --- review round 1 (2026-11-01) --------------------------------------------------


def test_display_endpoint_shows_a_url_as_its_scheme_host_and_port() -> None:
    """#512 verify V7: ``/mcp`` printed ``…/mcp?token=…`` whole, while
    ``aelix status`` prints no endpoint for that very reason. Verify R7: a key
    carried in the PATH (``/mcp/<key>/sse``) still showed after the query was cut."""

    from aelix_coding_agent.core.mcp_servers import display_endpoint

    assert display_endpoint("http://127.0.0.1:49360/mcp?token=TOKSECRET") == (
        "http://127.0.0.1:49360/…"
    )
    assert display_endpoint("https://hooks.example.test/mcp/KEYSECRET512/sse") == (
        "https://hooks.example.test/…"
    )
    assert display_endpoint("https://user:pw@example.test/mcp#frag") == "https://example.test/…"
    assert display_endpoint("https://example.test/mcp") == "https://example.test/…"
    assert display_endpoint("https://example.test/") == "https://example.test/"
    assert display_endpoint("https://example.test") == "https://example.test"
    assert display_endpoint("http://[::1]:8080/m?k=v") == "http://[::1]:8080/…"
    assert display_endpoint("/usr/bin/srv") == "/usr/bin/srv", "a stdio command is shown as is"
    assert display_endpoint("http://[::1/x?token=T") == "?", "unparseable says nothing"


def test_the_name_record_round_trips_names_only() -> None:
    """ADR-0282 D9: what a delegated child inherits is two sets of NAMES — any
    character a JSON key can hold, as ASCII (an env value holds no NUL)."""

    from aelix_coding_agent.core.mcp_servers import McpNameRecord

    record = McpNameRecord(off=frozenset({"off", "서버\x00"}), claimed=frozenset({"on"}))
    raw = record.to_env()
    assert raw.isascii() and "\x00" not in raw
    back = McpNameRecord.from_env(raw)
    assert back.off == {"off", "서버\x00"}
    assert back.claimed == {"on", "off", "서버\x00"}, "an off name is claimed too"
    assert json.loads(raw).keys() == {"off", "claimed"}, "nothing but names travels"
    # Verify R5: an off name is written once — ``from_env`` claims it again.
    assert json.loads(raw)["claimed"] == ["on"]
    # An off name keeps an extension's server of that name off too, even in a
    # record that does not list it as claimed.
    assert McpNameRecord.from_env('{"off": ["a"], "claimed": []}').claimed == {"a"}


@pytest.mark.parametrize(
    "raw", [None, "", "x", "[]", '{"off": "a"}', '{"off": [1, null], "claimed": {}}']
)
def test_a_malformed_record_suppresses_nothing(raw: str | None) -> None:
    from aelix_coding_agent.core.mcp_servers import McpNameRecord

    assert not McpNameRecord.from_env(raw)


def test_a_deeply_nested_record_suppresses_nothing_and_does_not_raise() -> None:
    """#512 verify R1: ``json.loads`` raises ``RecursionError``, not
    ``ValueError``, on a deep enough value, and the launch exited 1 with a
    traceback."""

    from aelix_coding_agent.core.mcp_servers import McpNameRecord

    assert not McpNameRecord.from_env("[" * 100_000 + "]" * 100_000)


def test_the_record_is_bounded_off_names_first() -> None:
    """#512 verify R5: about 1.24 MB of names failed every delegation's spawn
    with ``Argument list too long``. The record stays under the bound; the
    ``off`` names go first, and ``unpassed`` counts the ones left out."""

    from aelix_coding_agent.core.mcp_servers import _RECORD_MAX_BYTES, McpNameRecord

    off = frozenset(f"off-{i:05d}-" + "o" * 40 for i in range(300))
    claimed = frozenset(f"on-{i:05d}-" + "c" * 40 for i in range(300))
    record = McpNameRecord(off=off, claimed=claimed | {"x" * 70_000})
    raw = record.to_env()
    assert len(raw) <= _RECORD_MAX_BYTES < 70_000
    back = McpNameRecord.from_env(raw)
    assert back.off == off, "every off name fits, and they are kept first"
    assert back.claimed < record.claimed | off
    assert record.unpassed() == len(record.claimed - back.claimed) > 0
    small = McpNameRecord(off=frozenset({"a"}), claimed=frozenset({"b"}))
    assert small.unpassed() == 0


def test_the_child_env_carries_the_launch_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one builder both channels use (``build_rpc_child_env`` calls
    ``build_child_env``) puts the parent's record in the child's environment:
    replaced when the launch recorded one, removed when it recorded none, and
    passed through untouched by a process whose launch never recorded."""

    from aelix_agents.print_channel import build_child_env
    from aelix_agents.rpc_channel import build_rpc_child_env
    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.core import mcp_servers
    from aelix_coding_agent.core.mcp_servers import MCP_PARENT_NAMES_ENV, McpNameRecord

    profile = AgentProfile(name="kid", description="d", body="b", file_path="/x/kid.md", scope="user")
    inherited = {MCP_PARENT_NAMES_ENV: McpNameRecord(off=frozenset({"old"})).to_env()}
    monkeypatch.setattr(mcp_servers, "_launch_names", None)
    assert build_child_env(profile, base=inherited)[MCP_PARENT_NAMES_ENV] == inherited[
        MCP_PARENT_NAMES_ENV
    ]
    record = McpNameRecord(off=frozenset({"off"}), claimed=frozenset({"on"}))
    monkeypatch.setattr(mcp_servers, "_launch_names", record)
    for build in (build_child_env, build_rpc_child_env):
        env = build(profile, base=inherited)
        assert McpNameRecord.from_env(env[MCP_PARENT_NAMES_ENV]) == McpNameRecord(
            off=frozenset({"off"}), claimed=frozenset({"off", "on"})
        ), build
    monkeypatch.setattr(mcp_servers, "_launch_names", McpNameRecord())
    assert MCP_PARENT_NAMES_ENV not in build_child_env(profile, base=inherited)


def test_a_dotenv_cannot_set_the_record() -> None:
    """A cloned repo's ``.env`` must not be able to switch the user's servers off
    through the child's door either: ``AELIX_`` is a prefix it cannot set
    unless the user's own ``AELIX_DOTENV_ALLOW`` lists it."""

    from aelix_coding_agent.cli.runtime_bootstrap import _dotenv_key_allowed
    from aelix_coding_agent.core.mcp_servers import MCP_PARENT_NAMES_ENV

    assert MCP_PARENT_NAMES_ENV.startswith("AELIX_")
    assert _dotenv_key_allowed(MCP_PARENT_NAMES_ENV) is False
