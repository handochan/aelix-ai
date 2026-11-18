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
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from aelix_agent_core.contracts.manifest import McpServerContrib

    from aelix_coding_agent.core.mcp_servers import DisabledMcpServer

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


def packaged_skills_dir() -> Path:
    """The skills directory that ships INSIDE the wheel (#115).

    Aelix-original: pi ships zero built-in skills, so there is no pi analogue.
    The reason aelix does is that #115's channel is otherwise empty on a fresh
    install — the two user-facing tiers are ``~/.aelix/agent/skills`` and
    ``<cwd>/.aelix/skills``, both of which a new user has never created, so a
    correctly wired catalog would still deliver nothing and the feature would
    be indistinguishable from the bug it fixes.

    Resolved from the package location at CALL time, exactly like
    ``agent_context._package_pointer``, so it is right for a source checkout
    and for an installed user in site-packages. Returns the path whether or not
    it exists: ``load_skills`` skips a missing directory silently, which is the
    behaviour a stripped-down or partially-installed package should get.

    NOT under ``get_agent_dir()``: that directory is user-writable state, and a
    packaged default written there would be indistinguishable from a skill the
    user authored — and would survive an upgrade that changed it.
    """

    return Path(__file__).resolve().parents[1] / "skills"


def get_bin_dir() -> str:
    """Pi parity: ``getBinDir`` (``config.ts:483-485`` → ``join(getAgentDir(),
    "bin")``).

    Directory where :mod:`aelix_coding_agent.util.tools_manager` installs
    auto-downloaded ``rg`` / ``fd`` binaries and which
    :func:`aelix_coding_agent.util.shell_env.get_shell_env` prepends to
    ``PATH`` so spawned bash commands can resolve them. Pi default is
    ``~/.pi/agent/bin`` → Aelix ``~/.aelix/agent/bin``.
    """

    return str(Path(get_agent_dir()) / "bin")


def get_session_dir() -> str | None:
    """Pi parity: :data:`ENV_SESSION_DIR` override.

    Returns the tilde-expanded value of :data:`ENV_SESSION_DIR` when
    set, else :data:`None` (callers fall back to their own default,
    typically the ``JsonlSessionRepo`` ``sessions_root``).
    """

    env = os.environ.get(ENV_SESSION_DIR)
    return expand_tilde_path(env) if env else None


McpConfigSource = Literal["env", "project", "global"]
"""Where the resolved MCP config came from — used by the Project Trust gate
(Sprint P0 #10) to drop ONLY auto-discovered ``project`` contribs from an
untrusted directory while keeping user-chosen ``env`` / ``global`` ones."""


@dataclass(frozen=True)
class McpConfig:
    """The one ``mcp.json`` a launch reads, split by what a launch does with it.

    #512 (ADR-0282). ``contribs`` are the servers to connect; ``disabled`` the
    entries with ``"enabled": false`` — listed by ``/mcp``, ``/extension`` and
    ``aelix status``, never connected, their tools never registered.
    ``warnings`` are the entries that were skipped and why; ``declared`` every
    name, skipped or not. ``source`` and ``path`` say which file it was (both
    :data:`None` when there is none).
    """

    contribs: list[McpServerContrib] = field(default_factory=list)
    disabled: list[McpServerContrib] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    declared: list[str] = field(default_factory=list)
    """Every server name the file declares, the skipped ones included: the names
    a manifest server cannot take (``cli/entry.py``, ADR-0282 D5)."""
    source: McpConfigSource | None = None
    path: Path | None = None
    cwd: str = ""

    @property
    def label(self) -> str:
        """The file as a surface names it (``core.mcp_servers.config_label``)."""

        from aelix_coding_agent.core.mcp_servers import config_label

        if self.path is None:
            return ""
        return config_label(self.path, self.source, cwd=self.cwd)

    def disabled_servers(self) -> list[DisabledMcpServer]:
        from aelix_coding_agent.core.mcp_servers import DisabledMcpServer

        return [DisabledMcpServer(contrib=c, config=self.label) for c in self.disabled]

    @property
    def off(self) -> frozenset[str]:
        """The declared names this file starts nothing for: its disabled entries
        and the ones it skipped. What a delegated child must not start either
        (``core.mcp_servers.McpNameRecord``, ADR-0282 D9)."""

        return frozenset(self.declared) - {c.name for c in self.contribs}


_JSON_TYPES: tuple[tuple[type | tuple[type, ...], str], ...] = (
    (str, "a string"),
    ((int, float), "a number"),
    (list, "a list"),
    (dict, "an object"),
)


def _json_type(value: object) -> str:
    """The JSON type of a parsed value, for a warning that must not quote it.

    #624 (ADR-0305 D14, Codex 1): the warning quoted the value, and an
    ``enabled`` mistyped as a string or a list can hold whatever was pasted
    there; ``aelix status`` prints these warnings, and its output is pasted
    into bug reports.
    """

    if value is None:
        return "null"
    for types, word in _JSON_TYPES:
        if isinstance(value, types):
            return word
    return type(value).__name__


def _invalid_reason(exc: Exception) -> str:
    """Why an entry did not validate: each field and its reason, never its value.

    ``str()`` of a pydantic ``ValidationError`` quotes the input
    (``input_value=['https://…?token=…']``), and a url or an ``env`` value is
    where a credential lives; ``aelix status`` prints these warnings, and its
    output is pasted into bug reports (#512 review 512-02). ``errors()`` gives
    the location and the message separately, so only those two are kept. Any
    other exception here comes from ``list()`` / ``dict()`` over ``args`` /
    ``env``, whose messages name a type or a length, not a value.
    """

    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return str(exc)
    parts = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc", ())) or "entry"
        if len(loc) > 40:
            loc = loc[:39] + "…"
        parts.append(f"{loc}: {item.get('msg', 'invalid')}")
    return "; ".join(parts) or f"invalid ({type(exc).__name__})"


def load_mcp_config(cwd: str) -> McpConfig:
    """Load MCP server definitions from a Claude-Code-style JSON config.

    Resolution precedence — ONE file, the first that exists:

    1. ``$AELIX_MCP_CONFIG`` (explicit override), else
    2. ``<cwd>/.aelix/mcp.json`` (project-local), else
    3. ``<get_agent_dir()>/mcp.json`` (user global).

    The files are NOT merged (ADR-0282 keeps this; pi merges, and lets a project
    entry override only ``enabled`` / ``exposure`` of a global server — the
    deferred question). A project file therefore replaces the global one in that
    directory, which is why an entry with no ``command`` or ``url`` there gets a
    warning that says so: it is pi's override shape, and here it overrides
    nothing.

    File shape::

        {"mcpServers": {"<name>": {"command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "env": {...}, "enabled": true}}}

    ``transport`` defaults to ``"stdio"`` when ``command`` is present, else
    ``"http"`` when ``url`` is present (an explicit ``transport`` always wins).
    ``enabled`` (#512) defaults to ``true``; ``false`` keeps the entry listed
    without connecting it. Per-entry errors (bad JSON, an invalid entry, a
    non-boolean ``enabled`` — pi ``core/mcp-servers.ts`` ``validateMcpServerConfig``
    rejects it the same way, and skipping is the side that starts nothing — a
    stdio entry with no ``command`` or an http/sse one with no ``url``) are
    returned as warning strings rather than raised, so one malformed server
    never aborts startup.

    ``source`` tags which tier the file came from (``"env"`` / ``"project"`` /
    ``"global"``). The Project Trust gate (Sprint P0 #10) uses it to suppress
    ONLY ``"project"`` (auto-discovered ``cwd/.aelix/mcp.json``) servers in an
    untrusted directory — ``"env"`` (``$AELIX_MCP_CONFIG``) and ``"global"`` are
    explicit user choices and are never gated.

    That last sentence has a PRECONDITION (ADR-0203): ``$AELIX_MCP_CONFIG`` is
    an "explicit user choice" only because ``cli/runtime_bootstrap.py``
    ``load_dotenv`` refuses to let a cwd ``.env`` set it. ``.env`` is read
    before the trust gate exists, so until that fix a cloned repo could nominate
    its own MCP config here and have the ``command`` spawned at startup —
    reproduced end-to-end, including under ``--no-approve``. Leaving this tier
    ungated is a decision about the USER's intent, and it is sound only while
    the env var behind it can carry nothing else.
    """

    source, path = resolve_mcp_config(cwd)
    return _read_mcp_config(path, source, cwd)


def resolve_mcp_config(cwd: str) -> tuple[McpConfigSource, Path]:
    """Which ``mcp.json`` a launch in *cwd* reads, and where it came from — without opening it.

    :func:`load_mcp_config`'s precedence, and nothing else: the project file is
    only ``stat``-ed (``is_file``), so an untrusted folder's file is never
    opened (#624, ADR-0305 D13). ``/mcp`` resolves with this at every snapshot
    and again inside its write's lock, so the panel and the launch cannot
    disagree about the file.
    """

    override = os.environ.get(ENV_MCP_CONFIG)
    if override:
        return "env", Path(expand_tilde_path(override))
    local = Path(cwd) / CONFIG_DIR_NAME / "mcp.json"
    if local.is_file():
        return "project", local
    return "global", Path(get_agent_dir()) / "mcp.json"


def load_global_mcp_config(cwd: str) -> McpConfig:
    """The user's global ``mcp.json`` alone, whichever file a launch reads.

    For its NAMES (ADR-0282 D5, amended 2026-11-01): where an untrusted project
    ``.aelix/mcp.json`` is the file :func:`load_mcp_config` picks, nothing from
    either file starts, but the global file's names still keep an extension's
    server of the same name off — the user wrote them, the repo did not. Its
    servers are never connected from this function's result.
    """

    return _read_mcp_config(Path(get_agent_dir()) / "mcp.json", "global", cwd)


def _read_mcp_config(path: Path, source: McpConfigSource, cwd: str) -> McpConfig:
    from aelix_agent_core.contracts.manifest import McpServerContrib

    if not path.is_file():
        return McpConfig(cwd=cwd)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        return McpConfig(warnings=[_unread(path, exc)], source=source, path=path, cwd=cwd)

    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return McpConfig(
            warnings=[f"{path}: missing or invalid 'mcpServers' object"],
            source=source,
            path=path,
            cwd=cwd,
        )

    contribs: list[McpServerContrib] = []
    disabled: list[McpServerContrib] = []
    warnings: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            warnings.append(f"{path}: server {name!r} is not an object")
            continue
        enabled = spec.get("enabled", True)
        if not isinstance(enabled, bool):
            warnings.append(
                f'{path}: server {name!r}: "enabled" must be true or false, not '
                f"{_json_type(enabled)}; not started"
            )
            continue
        transport = spec.get("transport") or ("stdio" if spec.get("command") else "http")
        try:
            contrib = McpServerContrib(
                name=name,
                transport=transport,
                command=spec.get("command"),
                args=list(spec.get("args") or []),
                url=spec.get("url"),
                env=dict(spec.get("env") or {}),
            )
        except Exception as exc:  # noqa: BLE001 — Pydantic ValidationError et al.
            warnings.append(f"{path}: server {name!r}: {_invalid_reason(exc)}; not started")
            continue
        if not (contrib.command if contrib.transport == "stdio" else contrib.url):
            message = (
                f'{path}: server {name!r} needs "command" (stdio) or "url" (http/sse); not started'
            )
            if source == "project" and not any(k in spec for k in ("command", "url", "transport")):
                from aelix_coding_agent.core.mcp_servers import config_label

                global_file = config_label(Path(get_agent_dir()) / "mcp.json", "global")
                message += (
                    f". aelix reads only this file here, not {global_file} as well, "
                    "so an entry in it cannot change or turn off a global server"
                )
            warnings.append(message)
            continue
        if enabled and spec.get("disabled", False) is not False:
            # Cline's spelling. Said, not honoured: the key aelix reads is
            # ``enabled``, and reading a second one would make two keys that
            # can disagree (#512 verify V8).
            # Not "starts": trust and an inherited record decide that, later
            # (#512 verify R2).
            warnings.append(
                f'{path}: server {name!r}: "disabled" does not turn it off — aelix reads '
                '"enabled" (write "enabled": false to keep it off)'
            )
        (contribs if enabled else disabled).append(contrib)

    return McpConfig(
        contribs=contribs,
        disabled=disabled,
        warnings=warnings,
        declared=[str(name) for name in servers],
        source=source,
        path=path,
        cwd=cwd,
    )


def _unread(path: Path, exc: BaseException) -> str:
    """Why *path* was not read, never quoting its bytes (#624, ADR-0305 D14, opus L4).

    ``UnicodeDecodeError`` (not UTF-8) and ``RecursionError`` (nested too deep)
    escaped the loader and ended the launch; a decode error names the byte. A
    syntax error gives where it is, ``line N column M``: two numbers, no value
    (#624 fix round 1, the lead's call).
    """

    if isinstance(exc, OSError):
        return f"{path}: {exc}"
    if isinstance(exc, json.JSONDecodeError):
        return f"{path}: not valid JSON at line {exc.lineno} column {exc.colno} (JSONDecodeError)"
    return f"{path}: not valid JSON ({type(exc).__name__})"


def load_mcp_server_contribs(
    cwd: str,
) -> tuple[list[McpServerContrib], list[str], McpConfigSource | None]:
    """The servers to CONNECT from :func:`load_mcp_config`, its warnings and source.

    The pre-#512 3-tuple. Disabled entries are not in it, so a caller that
    connects what this returns can never start one; a surface that lists
    servers reads :func:`load_mcp_config` for them.
    """

    config = load_mcp_config(cwd)
    return config.contribs, config.warnings, config.source


__all__ = [
    "APP_NAME",
    "CONFIG_DIR_NAME",
    "ENV_AGENT_DIR",
    "ENV_MCP_CONFIG",
    "ENV_SESSION_DIR",
    "VERSION",
    "McpConfig",
    "McpConfigSource",
    "expand_tilde_path",
    "get_agent_dir",
    "get_bin_dir",
    "get_session_dir",
    "load_global_mcp_config",
    "load_mcp_config",
    "load_mcp_server_contribs",
    "packaged_skills_dir",
    "resolve_mcp_config",
]
