"""Pi parity: ``config.ts`` constants + helpers.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-390).

Pi citations at SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``:

- ``APP_NAME`` mirrors Pi ``config.ts`` ``APP_NAME = "pi"`` — Aelix
  substitutes ``"aelix"`` (Sprint 6h₃ HTML-export precedent).
- ``CONFIG_DIR_NAME`` mirrors Pi ``".pi"`` → Aelix ``".aelix"``.
- ``ENV_AGENT_DIR`` / ``ENV_SESSION_DIR`` mirror Pi's ``PI_*`` env
  prefix → Aelix ``AELIX_*`` prefix.
- ``VERSION`` mirrors Pi's package.json read — Aelix reads PEP 621
  ``[project] version`` via :func:`importlib.metadata.version`.
- :func:`expand_tilde_path` mirrors Pi ``expandTildePath``.
- :func:`get_agent_dir` mirrors Pi ``getAgentDir``.
"""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_agent_core.contracts.manifest import McpServerContrib

APP_NAME = "aelix"
"""Pi parity: ``APP_NAME = "pi"`` — Aelix substitutes ``"aelix"``
(Sprint 6h₃ HTML-export precedent)."""

CONFIG_DIR_NAME = ".aelix"
"""Pi parity: Pi ``.pi`` → Aelix ``.aelix``."""

ENV_AGENT_DIR = "AELIX_CODING_AGENT_DIR"
"""Pi parity: Pi ``PI_CODING_AGENT_DIR`` → Aelix
``AELIX_CODING_AGENT_DIR``."""

ENV_SESSION_DIR = "AELIX_CODING_AGENT_SESSION_DIR"
"""Pi parity: Pi ``PI_CODING_AGENT_SESSION_DIR`` → Aelix
``AELIX_CODING_AGENT_SESSION_DIR``."""

ENV_MCP_CONFIG = "AELIX_MCP_CONFIG"
"""Explicit override path for the MCP servers config (Claude-Code-style JSON)."""


def _get_version() -> str:
    """Pi parity: VERSION read from package.json.

    Aelix reads PEP 621 ``[project] version`` via
    :func:`importlib.metadata.version`. Falls back to ``"0.0.0-dev"``
    when the distribution is not installed (e.g., source-tree run
    without ``uv pip install -e .``).
    """

    try:
        return version("aelix-coding-agent")
    except PackageNotFoundError:
        return "0.0.0-dev"


VERSION = _get_version()
"""Pi parity: package version exposed for ``--version`` flag."""


def expand_tilde_path(path: str) -> str:
    """Pi parity: ``expandTildePath``.

    Expands a leading ``~`` (alone) or ``~/`` prefix to the user's
    home directory. Returns the path unchanged when no tilde prefix
    is present (e.g., ``/abs/path`` or ``relative/path``).
    """

    if path == "~":
        return str(Path.home())
    if path.startswith("~/"):
        return str(Path.home() / path[2:])
    return path


def get_agent_dir() -> str:
    """Pi parity: ``getAgentDir``.

    Returns :data:`ENV_AGENT_DIR` if set (with tilde expansion), else
    ``~/.aelix/agent``. Pi default is ``~/.pi/agent``.
    """

    env = os.environ.get(ENV_AGENT_DIR)
    if env:
        return expand_tilde_path(env)
    return str(Path.home() / CONFIG_DIR_NAME / "agent")


def get_session_dir() -> str | None:
    """Pi parity: :data:`ENV_SESSION_DIR` override.

    Returns the tilde-expanded value of :data:`ENV_SESSION_DIR` when
    set, else :data:`None` (callers fall back to their own default,
    typically the ``JsonlSessionRepo`` ``sessions_root``).
    """

    env = os.environ.get(ENV_SESSION_DIR)
    return expand_tilde_path(env) if env else None


def load_mcp_server_contribs(cwd: str) -> tuple[list[McpServerContrib], list[str]]:
    """Load MCP server definitions from a Claude-Code-style JSON config.

    Resolution precedence:

    1. ``$AELIX_MCP_CONFIG`` (explicit override), else
    2. ``<cwd>/.aelix/mcp.json`` (project-local), else
    3. ``<get_agent_dir()>/mcp.json`` (user global).

    File shape::

        {"mcpServers": {"<name>": {"command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "env": {...}}}}

    ``transport`` defaults to ``"stdio"`` when ``command`` is present, else
    ``"http"`` when ``url`` is present (an explicit ``transport`` always wins).
    Missing file → ``([], [])``. Per-entry errors (bad JSON / invalid entry)
    are returned as warning strings rather than raised, so one malformed
    server never aborts startup.
    """

    from aelix_agent_core.contracts.manifest import McpServerContrib

    override = os.environ.get(ENV_MCP_CONFIG)
    if override:
        path = Path(expand_tilde_path(override))
    else:
        local = Path(cwd) / CONFIG_DIR_NAME / "mcp.json"
        path = local if local.is_file() else Path(get_agent_dir()) / "mcp.json"

    if not path.is_file():
        return [], []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{path}: {exc}"]

    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return [], [f"{path}: missing or invalid 'mcpServers' object"]

    contribs: list[McpServerContrib] = []
    warnings: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            warnings.append(f"{path}: server {name!r} is not an object")
            continue
        transport = spec.get("transport") or ("stdio" if spec.get("command") else "http")
        try:
            contribs.append(
                McpServerContrib(
                    name=name,
                    transport=transport,
                    command=spec.get("command"),
                    args=list(spec.get("args") or []),
                    url=spec.get("url"),
                    env=dict(spec.get("env") or {}),
                )
            )
        except Exception as exc:  # noqa: BLE001 — Pydantic ValidationError et al.
            warnings.append(f"{path}: server {name!r}: {exc}")

    return contribs, warnings


__all__ = [
    "APP_NAME",
    "CONFIG_DIR_NAME",
    "ENV_AGENT_DIR",
    "ENV_MCP_CONFIG",
    "ENV_SESSION_DIR",
    "VERSION",
    "expand_tilde_path",
    "get_agent_dir",
    "get_session_dir",
    "load_mcp_server_contribs",
]
