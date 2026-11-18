"""#624 (ADR-0305 D13, D14; Codex 1, opus L4, opus M4) — the ``mcp.json`` loader.

* An invalid ``"enabled"`` is named by its JSON type, never quoted: a string or a
  list there can hold whatever was pasted, and the warning reaches the launch's
  stderr, ``aelix status`` and its ``--json`` (which go into bug reports).
* ``SECRET-624`` planted in ``enabled`` (a string, a list), ``env``, ``args``, a
  url's query and path, ``headers``, ``transport`` and ``command`` reaches none of
  those three outputs. The launch is the real ``_async_main`` with the MCP manager
  replaced by a spy, so nothing is spawned or dialled.
* A file that is not UTF-8, or is nested too deep to parse, is a warning, not an
  exception out of the loader (both crashed a launch at ``a1606f58``), and the
  warning quotes no byte.
* ``resolve_mcp_config`` is the loader's precedence, stat-only: it never opens
  the project file.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import config
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.status import run_status_command

from tests.env_sandbox import sandbox_home

S = "SECRET-624"
_ARGV = ["--no-session", "--print", "--provider", "anthropic", "--model", "x"]


class _FakePipedStdin:
    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    (tmp_path / "agent").mkdir()
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path / "proj")
    return tmp_path


def _global(env: Path, servers: Any) -> Path:
    path = env / "agent" / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


# --- the type word ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "word"),
    [
        (S, "a string"),
        ([S], "a list"),
        ({S: S}, "an object"),
        (1.5, "a number"),
        (None, "null"),
    ],
)
def test_an_invalid_enabled_is_named_by_its_type(env: Path, value: Any, word: str) -> None:
    _global(env, {"odd": {"command": "srv", "enabled": value}})
    (warning,) = config.load_mcp_config(str(env / "proj")).warnings
    assert warning.endswith(
        f": server 'odd': \"enabled\" must be true or false, not {word}; not started"
    )
    assert S not in warning


# --- SECRET-624 at launch, in status, in status --json -------------------------

SERVERS: dict[str, Any] = {
    "enabled-string": {"command": "srv", "enabled": S},
    "enabled-list": {"command": "srv", "enabled": [S]},
    "args-string": {"command": "srv", "args": S},
    "args-item": {"command": "srv", "args": [{"k": S}]},
    "env-string": {"command": "srv", "env": S},
    "env-value": {"command": "srv", "env": {"TOKEN": S}},
    "env-bad-value": {"command": "srv", "env": {"K": [S]}},
    "url-query": {"url": f"https://mcp.example.test/mcp?key={S}"},
    "url-path": {"url": f"https://mcp.example.test/{S}/sse", "transport": "sse"},
    "url-bad": {"url": 5, "headers": {"H": S}},
    "headers": {"url": "https://mcp.example.test/mcp", "headers": {"Authorization": S}},
    "transport-bad": {"command": "srv", "transport": S},
    "command-number": {"command": 5, "args": [S]},
    "command-secret": {"command": S, "enabled": False},
    "not-object": S,
}


class _Spy:
    """Stands in for ``McpClientManager``: nothing is spawned or dialled."""

    def __init__(self, contribs: Any, *, disabled: Any = ()) -> None:
        self.names = [c.name for c in contribs]

    async def connect_all(self) -> list[Any]:
        return []

    async def collect_agent_tools(self) -> list[Any]:
        return []

    async def disconnect_all(self) -> None:
        return None


async def test_no_secret_reaches_the_launch_status_or_its_json(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _global(env, SERVERS)
    monkeypatch.setattr(entry_mod, "McpClientManager", _Spy)
    await entry_mod._async_main(_ARGV)
    launch = capsys.readouterr().err
    assert await run_status_command(["--no-extensions"]) == 0
    text = capsys.readouterr().out
    assert await run_status_command(["--no-extensions", "--json"]) == 0
    report = capsys.readouterr().out
    mcp = json.loads(report)["mcp"]
    # Anti-vacuity: the entries were read, warned about and listed.
    assert "'enabled-string'" in launch and "'enabled-list'" in launch
    assert len(mcp["warnings"]) >= 9, mcp["warnings"]
    assert {s["name"] for s in mcp["servers"]} >= {"env-value", "url-query", "headers"}
    for where, out in (("launch stderr", launch), ("status", text), ("status --json", report)):
        assert S not in out, (where, [ln for ln in out.splitlines() if S in ln])
    assert "not a string" in launch and "not a list" in launch


# --- what used to escape the loader ---------------------------------------------


def test_a_file_that_is_not_utf8_is_a_warning(env: Path) -> None:
    path = env / "agent" / "mcp.json"
    path.write_bytes(b'{"mcpServers": {"a": {"command": "\xff\xfeSECRET-624"}}}')
    cfg = config.load_mcp_config(str(env / "proj"))
    assert cfg.warnings == [f"{path}: not valid JSON (UnicodeDecodeError)"]
    assert cfg.contribs == [] and cfg.source == "global"


def test_a_file_nested_too_deep_is_a_warning(env: Path) -> None:
    """Real 100,000-deep input is one warning, never a crash. What the parser does with it
    depends on the platform: RecursionError on macOS, a JSONDecodeError on some Linux 3.14
    builds, and on others (ubuntu-latest 3.14 in CI) it parses, and the list where the
    servers belong is reported. The RecursionError branch itself is forced in the next test."""

    path = env / "agent" / "mcp.json"
    path.write_text('{"mcpServers": ' + "[" * 100_000 + "]" * 100_000 + "}", encoding="utf-8")
    (warning,) = config.load_mcp_config(str(env / "proj")).warnings
    assert (
        warning == f"{path}: not valid JSON (RecursionError)"
        or warning == f"{path}: missing or invalid 'mcpServers' object"
        or re.fullmatch(
            rf"{re.escape(str(path))}: not valid JSON at line 1 column \d+ \(JSONDecodeError\)",
            warning,
        )
    ), warning


def test_a_recursion_error_is_a_warning(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _global(env, {"a": {"command": "srv"}})

    def _deep(*_a: Any, **_k: Any) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(config.json, "loads", _deep)
    assert config.load_mcp_config(str(env / "proj")).warnings == [
        f"{path}: not valid JSON (RecursionError)"
    ]


#: A JSON syntax error AT a secret: line 4, the column where the unquoted value starts.
BROKEN = '{\n  "mcpServers": {\n    "a": {"command": "srv",\n          "env": {"T": SECRET-624}}\n  }\n}\n'


def test_a_json_error_names_its_class_not_the_text(env: Path) -> None:
    path = env / "agent" / "mcp.json"
    path.write_text('{"mcpServers": {"a": "SECRET-624', encoding="utf-8")
    assert config.load_mcp_config(str(env / "proj")).warnings == [
        f"{path}: not valid JSON at line 1 column 22 (JSONDecodeError)"
    ]


def test_a_json_error_gives_its_line_and_column_and_no_value(env: Path) -> None:
    """#624 fix round 1 (the lead's call): the numbers come back, nothing else from the file."""

    path = env / "agent" / "mcp.json"
    path.write_text(BROKEN, encoding="utf-8")
    with pytest.raises(json.JSONDecodeError) as raised:
        json.loads(BROKEN)
    assert (raised.value.lineno, raised.value.colno) == (4, 24), "the error sits on the secret"
    assert BROKEN.splitlines()[3][23:].startswith(S)
    (warning,) = config.load_mcp_config(str(env / "proj")).warnings
    assert warning == f"{path}: not valid JSON at line 4 column 24 (JSONDecodeError)"
    assert S not in warning


async def test_no_secret_from_a_json_syntax_error_reaches_the_launch_or_status(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (env / "agent" / "mcp.json").write_text(BROKEN, encoding="utf-8")
    monkeypatch.setattr(entry_mod, "McpClientManager", _Spy)
    await entry_mod._async_main(_ARGV)
    launch = capsys.readouterr().err
    assert await run_status_command(["--no-extensions"]) == 0
    text = capsys.readouterr().out
    assert await run_status_command(["--no-extensions", "--json"]) == 0
    report = capsys.readouterr().out
    # Anti-vacuity: the warning, with its numbers, reached all three.
    for where, out in (("launch stderr", launch), ("status", text), ("status --json", report)):
        assert "line 4 column 24" in out, (where, out[-600:])
        assert S not in out, (where, [ln for ln in out.splitlines() if S in ln])


def test_an_os_error_keeps_its_own_text(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _global(env, {"a": {"command": "srv"}})

    def _denied(self: Path, *_a: Any, **_k: Any) -> str:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", _denied)
    assert config.load_mcp_config(str(env / "proj")).warnings == [
        f"{path}: [Errno 13] Permission denied: {str(path)!r}"
    ]


# --- resolve_mcp_config -----------------------------------------------------------


def _no_open(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any open of *path* fails the test; ``is_file``/``stat`` are allowed."""

    def _refuse(target: Any) -> bool:
        return Path(os.fspath(target)).resolve() == path.resolve()

    real_open, real_os_open = builtins.open, os.open
    real_read_text, real_read_bytes = Path.read_text, Path.read_bytes

    def _open(file: Any, *a: Any, **k: Any) -> Any:
        assert not (isinstance(file, (str, os.PathLike)) and _refuse(file)), "opened"
        return real_open(file, *a, **k)

    def _os_open(file: Any, *a: Any, **k: Any) -> int:
        assert not _refuse(file), "opened"
        return real_os_open(file, *a, **k)

    def _read_text(self: Path, *a: Any, **k: Any) -> str:
        assert not _refuse(self), "read"
        return real_read_text(self, *a, **k)

    def _read_bytes(self: Path) -> bytes:
        assert not _refuse(self), "read"
        return real_read_bytes(self)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(io, "open", _open)
    monkeypatch.setattr(os, "open", _os_open)
    monkeypatch.setattr(Path, "read_text", _read_text)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes)


def test_resolve_picks_the_project_file_without_opening_it(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = env / "proj" / ".aelix" / "mcp.json"
    project.parent.mkdir()
    project.write_text('{"mcpServers": {"a": {"command": "SECRET-624"}}}', encoding="utf-8")
    _no_open(project, monkeypatch)
    assert config.resolve_mcp_config(str(env / "proj")) == ("project", project)


def test_resolve_falls_back_to_the_global_file(env: Path) -> None:
    assert config.resolve_mcp_config(str(env / "proj")) == (
        "global",
        env / "agent" / "mcp.json",
    )
    # A directory where the project file would be is not a file: still global.
    (env / "proj" / ".aelix" / "mcp.json").mkdir(parents=True)
    assert config.resolve_mcp_config(str(env / "proj"))[0] == "global"


def test_resolve_takes_the_env_file_first(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (env / "proj" / ".aelix").mkdir()
    (env / "proj" / ".aelix" / "mcp.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_MCP_CONFIG", "~/elsewhere.json")
    source, path = config.resolve_mcp_config(str(env / "proj"))
    assert source == "env"
    assert path == Path(config.expand_tilde_path("~/elsewhere.json"))


def test_load_reads_the_file_resolve_names(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = env / "other.json"
    other.write_text('{"mcpServers": {"x": {"command": "srv"}}}', encoding="utf-8")
    monkeypatch.setattr(config, "resolve_mcp_config", lambda _cwd: ("env", other))
    cfg = config.load_mcp_config(str(env / "proj"))
    assert (cfg.source, cfg.path, [c.name for c in cfg.contribs]) == ("env", other, ["x"])
