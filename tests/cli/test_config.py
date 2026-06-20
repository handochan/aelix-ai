"""Sprint 6h₆ (Phase 5a-i, ADR-0089) — ``cli/config.py`` tests.

Covers VERSION, APP_NAME, env-var defaults, expand_tilde_path, and
get_agent_dir / get_session_dir env override.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from aelix_coding_agent.cli import config
from aelix_coding_agent.cli.config import (
    APP_NAME,
    CONFIG_DIR_NAME,
    ENV_AGENT_DIR,
    ENV_SESSION_DIR,
    VERSION,
    expand_tilde_path,
    get_agent_dir,
    get_session_dir,
)


def test_app_name_is_aelix() -> None:
    """Sprint 6h₃ precedent: APP_NAME = "aelix" (Pi parity substitution)."""

    assert APP_NAME == "aelix"


def test_config_dir_name_dotaelix() -> None:
    """CONFIG_DIR_NAME mirrors Pi ``.pi`` → Aelix ``.aelix``."""

    assert CONFIG_DIR_NAME == ".aelix"


def test_env_var_names() -> None:
    """Pi parity: env vars use ``AELIX_*`` prefix."""

    assert ENV_AGENT_DIR == "AELIX_CODING_AGENT_DIR"
    assert ENV_SESSION_DIR == "AELIX_CODING_AGENT_SESSION_DIR"


def test_version_is_string() -> None:
    """VERSION should resolve to a non-empty string."""

    assert isinstance(VERSION, str)
    assert VERSION  # non-empty


def test_expand_tilde_path_bare_tilde() -> None:
    assert expand_tilde_path("~") == str(Path.home())


def test_expand_tilde_path_with_subpath() -> None:
    assert expand_tilde_path("~/foo") == str(Path.home() / "foo")
    assert expand_tilde_path("~/a/b/c") == str(Path.home() / "a" / "b" / "c")


def test_expand_tilde_path_no_tilde_returns_unchanged() -> None:
    assert expand_tilde_path("/abs/path") == "/abs/path"
    assert expand_tilde_path("relative/path") == "relative/path"
    assert expand_tilde_path("") == ""


def test_get_agent_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_AGENT_DIR, raising=False)
    assert get_agent_dir() == str(Path.home() / CONFIG_DIR_NAME / "agent")


def test_get_agent_dir_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_AGENT_DIR, "/custom/agent")
    assert get_agent_dir() == "/custom/agent"


def test_get_agent_dir_env_override_tilde(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_AGENT_DIR, "~/myagent")
    assert get_agent_dir() == str(Path.home() / "myagent")


def test_get_session_dir_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SESSION_DIR, raising=False)
    assert get_session_dir() is None


def test_get_session_dir_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SESSION_DIR, "/some/sessions")
    assert get_session_dir() == "/some/sessions"


def test_get_session_dir_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SESSION_DIR, "~/sess")
    assert get_session_dir() == str(Path.home() / "sess")


def test_module_exports() -> None:
    """Ensure ``__all__`` exposes the documented surface."""

    expected = {
        "APP_NAME",
        "CONFIG_DIR_NAME",
        "ENV_AGENT_DIR",
        "ENV_MCP_CONFIG",
        "ENV_SESSION_DIR",
        "VERSION",
        "McpConfigSource",
        "expand_tilde_path",
        "get_agent_dir",
        "get_bin_dir",
        "get_session_dir",
        "load_mcp_server_contribs",
    }
    assert set(config.__all__) == expected


def test_unused_os_import_not_leaked() -> None:
    """Smoke check that the module imports cleanly."""

    # Just confirm `os` works as a sanity check (the module imports it).
    assert os.environ is not None


# === load_mcp_server_contribs (Claude-Code-style mcp.json) =================


def test_load_mcp_contribs_missing_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    # Point the global fallback at an empty agent dir so nothing is found.
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    contribs, warnings, source = config.load_mcp_server_contribs(
        str(tmp_path / "proj")
    )
    assert contribs == []
    assert warnings == []
    assert source is None


def test_load_mcp_contribs_cwd_file_with_args(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    cwd = tmp_path / "proj"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "mcp.json").write_text(
        '{"mcpServers": {"fs": {"command": "npx", '
        '"args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}}}'
    )
    contribs, warnings, source = config.load_mcp_server_contribs(str(cwd))
    assert warnings == []
    assert source == "project"
    assert len(contribs) == 1
    assert contribs[0].name == "fs"
    assert contribs[0].transport == "stdio"  # inferred from command
    assert contribs[0].args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]


def test_load_mcp_contribs_malformed_json_warns(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    cwd = tmp_path / "proj"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "mcp.json").write_text("{not valid json")
    contribs, warnings, source = config.load_mcp_server_contribs(str(cwd))
    assert contribs == []
    assert len(warnings) == 1
    assert source == "project"


def test_load_mcp_contribs_bad_entry_isolated(tmp_path, monkeypatch) -> None:
    """One invalid server warns; valid siblings still load."""

    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    cwd = tmp_path / "proj"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "mcp.json").write_text(
        '{"mcpServers": {"good": {"command": "echo"}, "bad": "not-an-object"}}'
    )
    contribs, warnings, source = config.load_mcp_server_contribs(str(cwd))
    assert [c.name for c in contribs] == ["good"]
    assert len(warnings) == 1
    assert source == "project"


def test_load_mcp_contribs_env_override_precedence(tmp_path, monkeypatch) -> None:
    override = tmp_path / "custom-mcp.json"
    override.write_text('{"mcpServers": {"via_env": {"url": "http://x", "transport": "http"}}}')
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(override))
    # A cwd file exists too, but the env override wins.
    cwd = tmp_path / "proj"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "mcp.json").write_text('{"mcpServers": {"cwd_one": {"command": "echo"}}}')
    contribs, _, source = config.load_mcp_server_contribs(str(cwd))
    assert [c.name for c in contribs] == ["via_env"]
    assert source == "env"
