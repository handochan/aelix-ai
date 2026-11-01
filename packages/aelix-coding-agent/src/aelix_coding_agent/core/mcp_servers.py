"""MCP server states: the one vocabulary every surface that lists servers prints.

#512, ADR-0282. ``/mcp``, the ``/extension`` Installed tab and ``aelix status``
each list MCP servers, and #25's Installed tab will be the fourth. Before #512
the two TUI surfaces each turned ``connection.connected`` into their own word
and ``aelix status`` listed none, so a server ``mcp.json`` keeps off
(``"enabled": false``) had no word anywhere. The words, what each means, the
rule that picks one and the sentence that says how to turn a server back on
live HERE; the surfaces only render what :func:`runtime_statuses` and
:func:`config_statuses` return.

Import-light on purpose: ``aelix status`` reads this module, and it must run
without the ``[tui]`` extra and without the MCP SDK, which imports ``rich``.
``McpClientManager`` is read duck-typed for the same reason.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, MutableMapping

    from aelix_agent_core.contracts import McpServerContrib

McpServerState = Literal["connected", "failed", "disconnected", "disabled", "enabled"]
"""Every state a listed MCP server can be in.

``connected`` / ``failed`` / ``disconnected`` / ``disabled`` describe a running
session; ``enabled`` / ``disabled`` describe a config read without one
(``aelix status``). ``disabled`` is the same fact in both: the entry is listed
and nothing was started for it."""

STATE_MEANINGS: Mapping[McpServerState, str] = MappingProxyType(
    {
        "connected": "connected at launch; its tools are registered",
        "failed": "did not connect at launch; it has no tools this session",
        "disconnected": "not connected now, and its launch connect did not fail",
        "disabled": '"enabled": false in mcp.json; not started, no tools, still listed',
        "enabled": "starts at the next launch (aelix status starts no server)",
    }
)
"""What each state means, in the words the guide and ADR-0282 use."""


@dataclass(frozen=True)
class DisabledMcpServer:
    """An ``mcp.json`` entry with ``"enabled": false``.

    Kept as the contribution itself, not a summary, so a later toggle (the
    deferred ``/mcp`` UI) has everything it would connect. ``config`` is the
    display label of the file that turns it off (:func:`config_label`).
    """

    contrib: McpServerContrib
    config: str


@dataclass(frozen=True)
class McpServerStatus:
    """One listed server, as every surface renders it."""

    name: str
    transport: str
    state: McpServerState
    endpoint: str = ""
    """The url, or the stdio command without its args; ``""`` when unknown.
    ``aelix status`` does not print it — a url can carry a token."""
    config: str | None = None
    """For ``disabled``: the label of the file that turns it off."""


def _endpoint(contrib: Any) -> str:
    if contrib is None:
        return ""
    return getattr(contrib, "url", None) or getattr(contrib, "command", None) or ""


def connection_status(conn: Any, *, failed: Iterable[str] = ()) -> McpServerStatus:
    """One connection's row. The rule: connected wins; otherwise a name whose
    launch connect raised (``failed``) is ``failed`` and any other is
    ``disconnected``. getattr-guarded — a render path never raises on an odd shape."""

    name = getattr(conn, "name", None) or "?"
    if getattr(conn, "connected", False):
        state: McpServerState = "connected"
    elif name in set(failed):
        state = "failed"
    else:
        state = "disconnected"
    return McpServerStatus(
        name=name,
        transport=getattr(conn, "transport", None) or "?",
        state=state,
        endpoint=_endpoint(getattr(conn, "_contrib", None)),
    )


def runtime_statuses(manager: Any) -> list[McpServerStatus]:
    """The servers a running session lists: its connections, then the disabled.

    ``manager`` is an ``McpClientManager`` or anything shaped like one —
    ``connections`` (name → connection with ``name`` / ``transport`` /
    ``connected``), optionally ``failed`` (the names whose launch connect
    raised) and ``disabled`` (:class:`DisabledMcpServer`). Every attribute is
    getattr-guarded so an odd shape degrades instead of raising in a render
    path. ``None`` lists nothing. Disabled servers come last, as pi orders them
    (``extensions/mcp/index.ts`` ``attentionRank``).
    """

    if manager is None:
        return []
    failed = frozenset(getattr(manager, "failed", None) or ())
    rows = [
        connection_status(conn, failed=failed)
        for conn in list((getattr(manager, "connections", None) or {}).values())
    ]
    for entry in list(getattr(manager, "disabled", None) or ()):
        contrib = getattr(entry, "contrib", None)
        rows.append(
            McpServerStatus(
                name=getattr(contrib, "name", None) or "?",
                transport=getattr(contrib, "transport", None) or "?",
                state="disabled",
                endpoint=_endpoint(contrib),
                config=getattr(entry, "config", None),
            )
        )
    return rows


def config_statuses(
    contribs: Iterable[McpServerContrib], disabled: Iterable[DisabledMcpServer]
) -> list[McpServerStatus]:
    """The servers a config lists with no session: ``enabled``, then ``disabled``."""

    rows = [
        McpServerStatus(name=c.name, transport=c.transport, state="enabled", endpoint=_endpoint(c))
        for c in contribs
    ]
    rows.extend(
        McpServerStatus(
            name=d.contrib.name,
            transport=d.contrib.transport,
            state="disabled",
            endpoint=_endpoint(d.contrib),
            config=d.config,
        )
        for d in disabled
    )
    return rows


def config_label(path: Path | str, source: str | None, *, cwd: Path | str | None = None) -> str:
    """How a surface names the ``mcp.json`` that declared a server.

    Short enough for an 80-column row in the usual cases: the project file is
    always ``<cwd>/.aelix/mcp.json`` and is named relative to the directory the
    user launched in; a file under the home directory is named with ``~``. An
    ``$AELIX_MCP_CONFIG`` path is the user's own and is printed as given.
    """

    p = Path(path)
    if source == "project" and cwd is not None:
        try:
            return p.relative_to(Path(cwd)).as_posix()
        except ValueError:
            pass
    if source == "env":
        return str(p)
    try:
        home = Path.home()
    except (RuntimeError, KeyError):  # no resolvable home: print the path
        return str(p)
    try:
        rel = p.relative_to(home)
    except ValueError:
        return str(p)
    return "~/" + rel.as_posix()


def disabled_note(status: McpServerStatus) -> str:
    """The key and the file that keep ``status`` off: ``"enabled": false in <file>``.

    For a surface with no room for :func:`enable_hint`'s two rows —
    ``/extension``'s tabbed modal does not scroll and bottom-truncates, so it
    puts this on the server's own row instead."""

    return f'"enabled": false in {status.config}' if status.config else '"enabled": false'


def enable_hint(statuses: Iterable[McpServerStatus]) -> list[str]:
    """How to turn the listed disabled servers back on: the file and the key.

    Two short lines per distinct file, each under 78 columns for the usual
    labels. Empty when nothing listed is disabled.
    """

    seen: list[str] = []
    lines: list[str] = []
    for status in statuses:
        if status.state != "disabled" or not status.config or status.config in seen:
            continue
        seen.append(status.config)
        lines.append(f"Turned off by {disabled_note(status)}.")
        lines.append("Set it to true, or delete the key, and the next aelix start connects it.")
    return lines


def display_endpoint(endpoint: str) -> str:
    """``endpoint`` as a surface may show it: a url as its scheme, host and port
    with the rest elided (``https://example.test/…``), a stdio command as it is.

    A url's query is where an MCP endpoint's token usually rides
    (``…/mcp?token=…``), and ``/mcp`` printed it whole (#512 verify V7); some
    services carry the key in the path instead (``/mcp/<key>/sse``), which the
    query-only cut still printed (verify R7). ``aelix status`` prints no
    endpoint at all for the same reason. The server's name already tells two
    rows apart; the host says where it dials.
    """

    if "://" not in endpoint:
        return endpoint
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:  # an unparseable url says nothing safe to print
        return "?"
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    shown = f"{parts.scheme}://{netloc}"
    if parts.path.strip("/") or parts.query or parts.fragment or parts.username or parts.password:
        return shown + "/…"
    return shown + parts.path


# --- what a delegated child inherits (ADR-0282 D9) ------------------------------

MCP_PARENT_NAMES_ENV = "AELIX_MCP_PARENT_NAMES"
"""The environment variable a delegated child reads its parent's MCP names from.

``AELIX_``: a cwd ``.env`` cannot set it (``runtime_bootstrap._DOTENV_NEVER``)
unless the user's own ``AELIX_DOTENV_ALLOW`` lists it, so a cloned repo cannot
use it to switch the user's servers off. The launch reads it and removes it
from ``os.environ`` (``cli/entry.py``), so the child's bash and ``api.exec`` do
not hand it on; the child's own children get :data:`_launch_names` instead."""

_RECORD_MAX_BYTES = 16 * 1024
"""The most :meth:`McpNameRecord.to_env` writes (#512 verify R5).

Private on purpose: it is this encoder's transport limit, not a delegation
policy ``aelix_agents`` reads (ADR-0197; ``tests/agents/test_p2_band_boundaries.py``).

An environment string has a ceiling — Linux takes at most 128 KiB in one
(``MAX_ARG_STRLEN``), Windows documents 32,767 characters for a variable, and
macOS counts the whole environment and argv against ``ARG_MAX`` — and past it
every delegation's spawn failed with ``Argument list too long`` (measured at
about 1.24 MB of names on macOS). 16 KiB holds several hundred names. Past it
the child still spawns: the names that do not fit are left out, ``off`` names
kept first, and the launch warns how many (:meth:`McpNameRecord.unpassed_warning`)."""

_EMPTY_RECORD_BYTES = len(json.dumps({"off": [], "claimed": []}, separators=(",", ":")))


@dataclass(frozen=True)
class McpNameRecord:
    """The MCP server names a launch accepted, as a delegated child inherits them.

    ``off``: names its ``mcp.json`` starts nothing for — disabled entries and
    skipped (invalid) ones. ``claimed``: every name its ``mcp.json`` declares,
    ``off`` included — the names an extension's server may not take (D5).

    NAMES ONLY, and only ever used to SUPPRESS: a child drops its own
    ``mcp.json`` server whose name is in ``off`` and an extension's server whose
    name is in ``claimed``. It carries no command, url, environment or
    credential, so it cannot start anything, and a child reads nothing from it
    but these two sets.
    """

    off: frozenset[str] = frozenset()
    claimed: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.off or self.claimed)

    def __or__(self, other: McpNameRecord) -> McpNameRecord:
        return McpNameRecord(off=self.off | other.off, claimed=self.claimed | other.claimed)

    def _fitted(self) -> tuple[list[str], list[str], int]:
        """The ``off`` names and the claimed-only names that fit
        :data:`_RECORD_MAX_BYTES`, ``off`` first, and how many do not."""

        room = _RECORD_MAX_BYTES - _EMPTY_RECORD_BYTES
        off: list[str] = []
        claimed: list[str] = []
        unpassed = 0
        for kept, names in ((off, self.off), (claimed, self.claimed - self.off)):
            for name in sorted(names):
                cost = len(json.dumps(name, ensure_ascii=True)) + 1  # and its comma
                if cost > room:
                    unpassed += 1
                    continue
                room -= cost
                kept.append(name)
        return off, claimed, unpassed

    def to_env(self) -> str:
        """JSON, ASCII-only (a name can hold any character, an env value no NUL),
        at most :data:`_RECORD_MAX_BYTES`. ``claimed`` lists only the
        names not in ``off``; :meth:`from_env` adds ``off`` back."""

        off, claimed, _ = self._fitted()
        return json.dumps(
            {"off": off, "claimed": claimed}, ensure_ascii=True, separators=(",", ":")
        )

    def unpassed(self) -> int:
        """How many names :meth:`to_env` leaves out to stay under the bound."""

        return self._fitted()[2]

    def unpassed_warning(self) -> str | None:
        """The launch's one-line warning when :meth:`to_env` leaves names out."""

        unpassed = self.unpassed()
        if not unpassed:
            return None
        return (
            f"Warning: MCP config: {unpassed} server name(s) are not passed to delegated "
            f"agents (the list is capped at {_RECORD_MAX_BYTES // 1024} KiB), so an "
            "agent may start a server of one of those names."
        )

    @classmethod
    def from_env(cls, raw: str | None) -> McpNameRecord:
        """The record in ``raw``; empty for anything else. Lenient on purpose:
        only aelix writes it, and a malformed one can at worst suppress less."""

        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):  # RecursionError: a deeply nested value
            return cls()
        if not isinstance(data, dict):
            return cls()

        def names(key: str) -> frozenset[str]:
            value = data.get(key)
            if not isinstance(value, list):
                return frozenset()
            return frozenset(item for item in value if isinstance(item, str))

        off = names("off")
        return cls(off=off, claimed=names("claimed") | off)


_launch_names: McpNameRecord | None = None
"""What THIS process's launch accepted (``cli/entry.py`` sets it once its
``mcp.json`` and the inherited record are applied); :data:`None` before then,
and in a process that never ran that block."""


def inherited_mcp_names(environ: Mapping[str, str] | None = None) -> McpNameRecord:
    """The record a delegating aelix left in this process's environment."""

    source = os.environ if environ is None else environ
    return McpNameRecord.from_env(source.get(MCP_PARENT_NAMES_ENV))


def set_launch_mcp_names(record: McpNameRecord | None) -> None:
    """Record what this launch accepted, for the children it delegates to."""

    global _launch_names
    _launch_names = record


def pass_mcp_names_to_child(env: MutableMapping[str, str]) -> None:
    """Put this launch's record into a delegated child's environment.

    Called by ``aelix_agents.print_channel.build_child_env``, the one builder
    both channels use. A process whose launch recorded nothing leaves ``env``
    as inherited — a record handed down from further up still passes through.
    """

    if _launch_names is None:
        return
    if _launch_names:
        env[MCP_PARENT_NAMES_ENV] = _launch_names.to_env()
    else:
        env.pop(MCP_PARENT_NAMES_ENV, None)


__all__ = [
    "MCP_PARENT_NAMES_ENV",
    "STATE_MEANINGS",
    "DisabledMcpServer",
    "McpNameRecord",
    "McpServerState",
    "McpServerStatus",
    "config_label",
    "config_statuses",
    "connection_status",
    "disabled_note",
    "display_endpoint",
    "enable_hint",
    "inherited_mcp_names",
    "pass_mcp_names_to_child",
    "runtime_statuses",
    "set_launch_mcp_names",
]
