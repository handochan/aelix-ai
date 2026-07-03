"""Issue #21 (W1) — ``scan_extension_manifests``: metadata-only manifest scan.

The scan powers the ``contributes.mcp_servers`` merge in ``cli/entry.py``,
which runs BEFORE the first harness build (MCP connects once at startup).
Properties under test: (a) manifests surface through the same 4-tier
discovery; (b) plugin code is NEVER imported or executed — the entry module
may not even exist; (c) the Project-Trust gate (``no_project_local``) is
honored exactly like the full loader.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.loader import scan_extension_manifests

_MCP_MANIFEST = textwrap.dedent("""
    [plugin]
    id = "mcp-declarer"
    name = "MCP Declarer"
    version = "0.1.0"
    description = "Declares an MCP server in its manifest"
    authors = ["Test <test@example.com>"]
    repository = "https://github.com/example/mcp-declarer"
    license = "MIT"

    [plugin.api]
    level = 1
    min_level = 1

    [plugin.entry]
    python = "module_that_does_not_exist_anywhere:setup"

    [activation]
    on_startup_finished = true

    [contributes]
    mcp_servers = [{ name = "files", transport = "stdio", command = "npx" }]
""").strip()


def _make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Project cwd + isolated agent_dir (never leak the real ~/.aelix)."""
    cwd = tmp_path / "proj"
    (cwd / ".aelix" / "extensions").mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    return cwd, agent_dir


def test_scan_finds_project_manifest_without_executing(tmp_path: Path) -> None:
    """The manifest surfaces from the project tier, and — the point of the
    scan — no plugin code runs: the declared entry module does not even
    exist, which would make the full loader error at factory resolution."""
    cwd, agent_dir = _make_dirs(tmp_path)
    pkg = cwd / ".aelix" / "extensions" / "mcp-declarer"
    pkg.mkdir()
    (pkg / "aelix-plugin.toml").write_text(_MCP_MANIFEST, encoding="utf-8")

    manifests = scan_extension_manifests([], cwd=cwd, agent_dir=agent_dir)

    assert len(manifests) == 1
    servers = manifests[0].contributes.mcp_servers
    assert [(s.name, s.transport, s.command) for s in servers] == [
        ("files", "stdio", "npx")
    ]


def test_scan_respects_project_trust_gate(tmp_path: Path) -> None:
    """no_project_local=True (untrusted directory) suppresses the
    project-local tier — its manifest MCP servers must never surface."""
    cwd, agent_dir = _make_dirs(tmp_path)
    pkg = cwd / ".aelix" / "extensions" / "mcp-declarer"
    pkg.mkdir()
    (pkg / "aelix-plugin.toml").write_text(_MCP_MANIFEST, encoding="utf-8")

    manifests = scan_extension_manifests(
        [], cwd=cwd, agent_dir=agent_dir, no_project_local=True
    )

    assert manifests == []


def test_scan_finds_global_tier_manifest(tmp_path: Path) -> None:
    """User-global (~/.aelix/extensions, here agent_dir) manifests surface
    even when the project tier is suppressed — mirroring the full loader's
    trust semantics (global is user-chosen, never gated)."""
    cwd, agent_dir = _make_dirs(tmp_path)
    pkg = agent_dir / "extensions" / "mcp-declarer"
    pkg.mkdir()
    (pkg / "aelix-plugin.toml").write_text(_MCP_MANIFEST, encoding="utf-8")

    manifests = scan_extension_manifests(
        [], cwd=cwd, agent_dir=agent_dir, no_project_local=True
    )

    assert len(manifests) == 1
    assert manifests[0].plugin.id == "mcp-declarer"


def test_scan_ignores_bare_py_extensions_and_runs_no_code(
    tmp_path: Path,
) -> None:
    """Bare ``.py`` extensions carry no manifest — the scan skips them and,
    critically, never imports them (a module-level marker write proves it)."""
    cwd, agent_dir = _make_dirs(tmp_path)
    marker = tmp_path / "executed.marker"
    (cwd / ".aelix" / "extensions" / "bare.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('EXECUTED')\n"
        "def setup(aelix):\n"
        "    pass\n",
        encoding="utf-8",
    )

    manifests = scan_extension_manifests([], cwd=cwd, agent_dir=agent_dir)

    assert manifests == []
    assert not marker.exists()  # scan never imported the module


def test_scan_never_resolves_entry_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry_points tier is EXCLUDED from the scan: resolving an endpoint
    (``ep.load()``) is a module import, which would break the metadata-only
    contract (adversarial-review finding). A spy on the tier proves the scan
    never touches it while the full loader still does."""
    from aelix_coding_agent.extensions import loader as loader_mod

    calls: list[str] = []

    def _spy(seen_ep: set[str]) -> list:
        calls.append("resolved")
        return []

    monkeypatch.setattr(loader_mod, "_discover_via_entry_points", _spy)
    cwd, agent_dir = _make_dirs(tmp_path)

    scan_extension_manifests([], cwd=cwd, agent_dir=agent_dir)
    assert calls == []  # scan skipped the tier entirely

    entries, _errors = loader_mod._discover_entries(
        [], cwd=cwd, agent_dir=agent_dir
    )
    assert calls == ["resolved"]  # the full-load path still resolves it
