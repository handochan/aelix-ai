"""Issue #91 — the manifest MCP capability gate at the REAL seam (entry-level).

``tests/extensions/test_mcp_servers_gate_manifest.py`` pins the gate function
and proves the subprocess never spawns. This module pins the two things only
``cli/entry.py`` can answer:

1. the refused server never reaches ``McpClientManager`` — the gate sits on
   the correct side of the connect call, so refusal happens BEFORE the spawn
   rather than after it;
2. the refusal is PRINTED. A silently dropped server is the failure mode the
   audit was chasing in the first place (a pack that stops working with no
   explanation is indistinguishable from a broken host), so visibility is a
   requirement, not a nicety.

Entry-level fixtures mirror ``test_project_trust.py`` §5 (fake piped stdin →
print mode, isolated agent dir, env-authed provider so the run gets past the
no-model guard), and ``McpClientManager`` is spied exactly as it is there.
Patch module OBJECTS, per the repo convention.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.cli import entry as entry_mod

_ARGV = ["--no-session", "--print", "--provider", "anthropic", "--model", "x"]


class _FakePipedStdin:
    """Non-tty stdin → ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean cwd + isolated agent dir (no real ~/.aelix leakage)."""
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    return tmp_path


def _write_pack(proj: Path, *, capabilities: str, marker: Path) -> None:
    pkg = proj / ".aelix" / "extensions" / "mcp-entry-plug"
    pkg.mkdir(parents=True)
    pkg.joinpath("aelix-plugin.toml").write_text(
        textwrap.dedent(f"""
            [plugin]
            id = "mcp-entry-plug"
            name = "MCP Entry Plugin"
            version = "0.1.0"
            description = "Declares a stdio MCP server"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/mcp-entry-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [activation]
            on_startup_finished = true

            [[contributes.mcp_servers]]
            name = "entry-probe"
            transport = "stdio"
            command = "/bin/sh"
            args = ["-c", "printf SPAWNED > {marker}"]

            [contributes.mcp_servers.env]
            GITHUB_TOKEN = "ghp_TESTONLY_ENTRY_SECRET_0123456789"

            {capabilities}
        """).strip(),
        encoding="utf-8",
    )


async def _run(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """Run ``_async_main`` in ``proj``; return the server names that reached
    ``McpClientManager`` (the last gate before a spawn)."""
    seen: list[str] = []

    class _SpyManager:
        def __init__(self, contribs):
            seen.extend(c.name for c in contribs)

        async def connect_all(self):
            return []

        async def collect_agent_tools(self):
            return []

        async def disconnect_all(self):
            return None

    monkeypatch.setattr(entry_mod, "McpClientManager", _SpyManager)
    monkeypatch.chdir(proj)
    # ``--approve`` trusts the project dir, so the project-trust gate is NOT
    # what suppresses the server — the capability gate is the only thing under
    # test here.
    await entry_mod._async_main([*_ARGV, "--approve"])
    return seen


async def test_entry_refuses_ungated_manifest_mcp_server_and_says_so(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    proj = _isolated_env / "proj"
    proj.mkdir()
    marker = _isolated_env / "entry_spawned.marker"
    _write_pack(proj, capabilities="", marker=marker)

    seen = await _run(proj, monkeypatch)
    err = capsys.readouterr().err

    # (1) it never reached the manager → it was refused BEFORE the spawn.
    assert seen == []
    assert not marker.exists()
    # (2) and the user was told why, in ONE legible line.
    refusals = [ln for ln in err.splitlines() if "MCP server refused" in ln]
    assert len(refusals) == 1
    (refusal,) = refusals
    assert "'mcp-entry-plug'" in refusal
    assert "'entry-probe'" in refusal
    assert "shell_exec" in refusal
    # Prose, not the ``ExtensionLoadError(path=..., error=...)`` dataclass-repr
    # shape the audit flagged. (Scoped to the refusal line on purpose: this
    # pack ALSO trips a PRE-EXISTING, unrelated warning later in startup —
    # a manifest with no ``[plugin.entry] python`` cannot load as an
    # extension — and that one IS rendered as a raw ExtensionLoadError repr.
    # Not this change's regression, and not this test's subject.)
    assert "ExtensionLoadError(" not in refusal
    # Never the plugin-supplied token, anywhere on stderr.
    assert "ghp_TESTONLY_ENTRY_SECRET_0123456789" not in err
    assert "GITHUB_TOKEN" not in err


async def test_entry_still_connects_a_manifest_server_with_shell_exec(
    _isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The legitimate pack must be unaffected — the gate refuses, it does not
    disable the family."""
    proj = _isolated_env / "proj"
    proj.mkdir()
    marker = _isolated_env / "entry_spawned.marker"
    _write_pack(
        proj, capabilities="[capabilities]\nshell_exec = true", marker=marker
    )

    seen = await _run(proj, monkeypatch)
    err = capsys.readouterr().err

    assert seen == ["entry-probe"]
    assert "MCP server refused" not in err
    # ...and the grant is ANNOUNCED. An allow path that prints nothing is how
    # a pack granted shell_exec for its hook gets a free, silent stdio spawn
    # from the same manifest; nothing else in the product ever shows a user a
    # capability flag, so this line is the whole signal.
    notices = [ln for ln in err.splitlines() if ln.startswith("Notice: plugin ")]
    assert len(notices) == 1
    (notice,) = notices
    assert "'mcp-entry-plug'" in notice
    assert "'entry-probe'" in notice
    assert "capabilities.shell_exec=true" in notice
    # Same leak contract as the refusal, on the path where env is live.
    assert "ghp_TESTONLY_ENTRY_SECRET_0123456789" not in err
    assert "GITHUB_TOKEN" not in err
