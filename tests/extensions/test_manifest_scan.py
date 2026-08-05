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


def test_scan_walks_entry_points_metadata_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``test_scan_never_resolves_entry_points`` — REWRITTEN for issue #91.

    The old assertion (``the scan skips the tier entirely``) was correct only
    because the tier's ONLY way to resolve an endpoint was ``ep.load()``, a
    module import. #91 deleted that call: endpoints are now resolved from
    installed metadata, so skipping the tier no longer buys the scan anything
    and costs it an installed pack's ``contributes.mcp_servers`` — the exact
    payload this scan exists to collect. So the property under test moves from
    "does not call the tier" to "calls it in METADATA-ONLY mode", which is what
    actually preserves the no-execution contract.

    The no-import half of the contract is measured for real — with a genuine
    installed dist, not a spy — in
    ``tests/extensions/test_ep_wiring.py::
    test_scan_reaches_an_endpoint_pack_mcp_servers_without_importing_it``.
    """

    from aelix_coding_agent.extensions import loader as loader_mod

    calls: list[bool] = []

    def _spy(
        seen_ep: set[str],
        *,
        metadata_only: bool = False,
        trusted_ep_dists: frozenset[str] = frozenset(),
    ) -> list:
        calls.append(metadata_only)
        return []

    monkeypatch.setattr(loader_mod, "_discover_via_entry_points", _spy)
    cwd, agent_dir = _make_dirs(tmp_path)

    scan_extension_manifests([], cwd=cwd, agent_dir=agent_dir)
    assert calls == [True]  # the scan walks the tier, metadata-only

    loader_mod._discover_entries([], cwd=cwd, agent_dir=agent_dir)
    assert calls == [True, False]  # the full-load path takes carriers too
