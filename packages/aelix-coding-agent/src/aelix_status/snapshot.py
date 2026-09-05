"""``RuntimeSnapshot`` — the typed, frozen answer to "what am I running as?" (#101).

Every field below names the runtime object it was read from. That is not
decoration: the whole point of #101 is that an agent asked to write an extension
must stop *guessing* its own state, and a snapshot assembled from plausible
defaults would be the same guess with a dataclass around it.

WHY THE BUILDERS TAKE CALLABLES AND CATCH. No SOURCE PROBE here may raise. This
snapshot is built inside a tool the model calls, and a tool that throws because
one source was unbound teaches the model that introspection is unreliable — which
sends it straight back to guessing. Every source is therefore probed and degraded
individually, and the degraded value is one that is safe to be wrong about.
``rpc_ws.py:90-94`` is the live example: it builds ``AgentHarnessOptions(model=,
session=, cwd=)`` with no ``tools=`` and no ``project_trusted=``, so a snapshot
taken under it must survive several missing sources at once.

The extension reducers below are ``getattr``-guarded rather than wrapped, which
is a narrower promise and the honest one: everything in ``LoadExtensionsResult.
extensions`` is an ``Extension`` dataclass built by ``_invoke_factory``, so a
missing attribute is the only malformation that can actually arrive, and a
property that raises something other than ``AttributeError`` cannot.

WHAT IS DELIBERATELY ABSENT. The issue's draft listed ``extension_paths``. It is
dropped, measured:

* ``Extension.source_info`` carries a TIER LABEL and nothing else. It had zero
  writers when #101 shipped — probed by loading one extension per tier
  (project / global / explicit / inline prepend) through the real
  ``discover_and_load_extensions``, every one came back ``source_info=None`` —
  and the M1 review is why it now has one: ``loader._entry_point_source_info``
  writes ``ExtensionSourceInfo(source="entry_points")`` for the endpoint tier.
  Its ``path`` / ``base_dir`` fields are deliberately still unwritten, for the
  reason the next two bullets give.
* ``Extension.resolved_path`` is set on exactly one branch — ``loader.py:282``,
  ``isinstance(entry, _ManifestEntry)`` — so it is ``None`` for every
  non-manifest extension.
* For the other tiers the only path-shaped value is ``Extension.name``, which for
  the directory tiers IS an absolute filesystem path. The same probe returned
  ``name='/tmp/tmp2520a9g8/proj/.aelix/extensions/projext.py'``. On a real
  machine that is ``$HOME`` and the OS username, handed to a model that may be
  a hosted third party.

So the location question is answered by a ``scope`` LABEL on each extension
instead — which tier it came from, not where it lives.

ANCHOR CONVENTION (ADR-0197's, verbatim in spirit): every ``file:line`` below was
re-read against this tree. They are the EVIDENCE for a decision, not a
maintenance contract — ``cli/entry.py`` in particular moves under every
concurrent track, so nothing there is cited by line at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_agent_core.contracts import AELIX_API_LEVEL

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# The five labels a caller can act on, plus the honest sixth.
#
# ``entry_point`` is the pip-installed tier — what ``aelix extension install``
# and every marketplace pack produce. It is the ONLY tier that cannot be
# reconstructed from the finished ``Extension``, which is why the loader records
# it (``loader._entry_point_source_info``) instead of this file guessing.
# Measured on two real installed-wheel images before that writer existed::
#
#     manifest-BOUND pack  resolved_path='<site>/boundpack'  -> "explicit"
#     manifest-LESS pack   name='setup'                      -> "unclassified"
#
# ``"explicit"`` means "the user typed ``-e <path>``". Reporting the most common
# install shape as its opposite is the failure this whole tool exists to stop.
#
# ``unclassified`` is not a hedge either, it is the residual measured limit.
# ``loader.py``'s ``_resolve_factory`` produces ``Extension.name`` from six
# branches, and two of them return a Python qualname rather than a path:
#
#   * ``isinstance(entry, _EntryPointEntry)`` (:1862-1865) — a manifest-less
#     installed ``aelix.extensions`` pack, now separated by its recorded tier;
#   * ``callable(entry) and not isinstance(entry, (str, Path))`` (:1870-1873) —
#     an inline factory, which is what every PREPENDED built-in is.
#
# Both compute ``getattr(factory, "__qualname__", None) or type(factory).__name__``,
# so the two are byte-identical AT THAT LAYER; what separates them now is the
# ``source_info`` the loader writes for the endpoint one. An inline factory has
# no such record, and inventing a "builtin" label for it would mean keeping a
# hardcoded list of built-in class names — the same list
# ``tui/extension_manager.py:58-60`` keeps. A label that is true beats a label that
# is specific and wrong.
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
SCOPE_EXPLICIT = "explicit"
SCOPE_ENTRY_POINT = "entry_point"
SCOPE_UNCLASSIFIED = "unclassified"

#: The value ``loader._entry_point_source_info`` writes into
#: :attr:`Extension.source_info`. Matched, never re-derived: it is
#: ``ExtensionSourceInfo.source``'s own ``Literal`` member (``api.py:959``), so
#: a rename there is a type error rather than a silently wrong label here.
_LOADER_ENTRY_POINT_SOURCE = "entry_points"

MAX_EMITTED_CHARS = 128
"""Longest author-controlled string this snapshot will emit, per field.

WHY THERE IS A CAP AT ALL — the claim it replaces was false. This module used to
justify emitting ``plugin.version`` with "A secret cannot be smuggled through
either", on the strength of the field being schema-constrained.
``PluginIdentity.version`` is constrained in SHAPE only::

    ^\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z-.]+)?(\\+[0-9A-Za-z-.]+)?$

(``contracts/manifest.py:40-43``) — no ``max_length``, and the prerelease and
build tails are unbounded ``[0-9A-Za-z-.]+`` runs. Measured on this tree: a
manifest with ``version = "1.0.0-<4132 chars>"`` parsed, loaded through the real
``discover_and_load_extensions``, and its 4138-character version reached the
tool's output verbatim.

64 is what the schema actually bounds (``plugin.id`` is
``^[a-z][a-z0-9-]{0,63}$``), so 128 leaves a full semver's worth of room past
the widest identity the manifest permits while still bounding the hostile case
by ~30x.

TWO OTHER FIELDS HAVE THE SAME SHAPE and get the same treatment, because three
separate caps drift:

* ``Extension.name`` for a manifest-less pack is ``getattr(factory,
  "__qualname__", None) or type(factory).__name__`` — ``__qualname__`` is a
  plain writable attribute, neither length- nor charset-constrained;
* a tool name is whatever an extension passed to ``register_tool``.

WHAT THIS IS NOT. It is not a secrecy control, and saying so is the point: a
40-character API token fits under any cap worth having, and this projection
reports plugin identity deliberately. What the cap bounds is COST and RENDERING
— the output is read by a model on the turn that asked for it, and one hostile
pack must not be able to spend the context window on a version string.
"""

_TRUNCATED = "…"
"""Appended when a value was cut, so a bounded string is never mistaken for the
whole one. Counted INSIDE the cap, so the emitted length never exceeds it."""

# DELETION, not escaping, and DUPLICATED rather than imported — the same table
# and the same two decisions as ``cli/agent_context._CONTROL_KILL``, whose
# comment records the measurement (a directory named
# ``proj\x1b]0;pwned\x07\x1b[31mZ`` emitting an unterminated OSC title-set into
# stderr). Duplicated because this package must not import ``cli`` for a
# four-element table; the range covers ``\x9b``, the one-byte CSI an ESC-only
# filter misses.
#
# ``json.dumps`` would ESCAPE a control byte rather than pass it through, so
# this is not what keeps the payload well-formed. It is what keeps a tool
# result from rendering as several lines of invented structure in a transcript
# that a model then reads as the tool's own output.
_CONTROL_KILL = dict.fromkeys([*range(0x20), 0x7F, *range(0x80, 0xA0)])


def bounded_emitted_value(value: str) -> str:
    """One author-controlled string, de-fanged and capped. See
    :data:`MAX_EMITTED_CHARS`."""

    cleaned = value.translate(_CONTROL_KILL)
    if len(cleaned) <= MAX_EMITTED_CHARS:
        return cleaned
    return cleaned[: MAX_EMITTED_CHARS - len(_TRUNCATED)] + _TRUNCATED


@dataclass(frozen=True)
class ExtensionSnapshot:
    """One loaded extension, reduced to what is safe to hand a model.

    ASSEMBLED FIELD BY FIELD ON PURPOSE. ``repr()`` / ``dataclasses.asdict()`` /
    ``manifest.model_dump()`` over an :class:`~aelix_coding_agent.extensions.api.Extension`
    all reach ``manifest.contributes.mcp_servers[].env``, which holds
    plugin-supplied API tokens. Measured on this tree: a manifest carrying
    ``env = { GH_TOKEN = "ghp_PLANTED_SECRET_abc123" }`` produced a 1250-char
    ``repr(Extension)`` containing that literal. Redaction here is not a filter
    over a serialised object — no serialisation of the object ever happens.

    ``name`` and ``version`` come from the manifest's ``[plugin]`` table when
    there is one. ``PluginIdentity.id`` is ``^[a-z][a-z0-9-]{0,63}$``, which
    bounds both its charset and its length; ``PluginIdentity.version`` is a
    semver pattern (``contracts/manifest.py:38,40-43``) which bounds NEITHER —
    its prerelease and build tails are unbounded ``[0-9A-Za-z-.]+`` runs and the
    field carries no ``max_length``.

    An earlier revision of this docstring read "A secret cannot be smuggled
    through either". That was false, and measured false: a pack with
    ``version = "1.0.0-<4132 chars>"`` validates, loads, and reached this tool's
    output verbatim. What is true is narrower and is what the code now does —
    every author-controlled string is emitted through
    :func:`bounded_emitted_value`, which caps length and strips control bytes.
    A short secret still fits under any usable cap; see
    :data:`MAX_EMITTED_CHARS` for what the bound is and is not.

    ``plugin.name`` — free text, 1..128 chars — remains deliberately NOT used:
    ``id`` is the identity a caller can act on, and a second author-controlled
    display string buys nothing.
    """

    name: str
    scope: str
    version: str | None = None
    has_manifest: bool = False


@dataclass(frozen=True)
class RuntimeSnapshot:
    """What this aelix process is, right now.

    Field sources, each read at snapshot time rather than captured at startup:

    ``version``
        ``cli/config.py``'s ``VERSION`` — ``importlib.metadata.version(
        "aelix-coding-agent")``, ``"0.0.0-dev"`` when the distribution is not
        installed. The same string ``--version`` prints, which is the point: an
        agent reading bundled docs needs the version those docs were built for.

    ``cwd``
        ``ExtensionContext.cwd`` — the harness's own resolved absolute cwd
        (``_build_harness_options``'s ``cwd = str(Path.cwd())`` → ``AgentHarnessOptions.cwd``
        → ``harness/core.py:_make_context``). Emitted RAW, and that is not a
        disclosure decision made here: ``build_system_prompt`` already emits
        ``- Working directory: {cwd_abs}`` at ``cli/agent_context.py:1067``, so
        the model was told this before its first turn.

    ``mode``
        The resolved app mode — ``"interactive" | "print" | "json" | "rpc"``,
        wired from ``entry.py``'s ``resolve_app_mode(parsed, stdin_is_tty)``
        result. NOT re-derived from ``sys.argv`` or from ``stdin.isatty()`` here:
        re-deriving would be a second opinion that can disagree with the one the
        process actually acted on, and ``resolve_app_mode`` reads a stdin TTY
        state sampled once at startup. ``"unknown"`` when unwired.

    ``project_trusted``
        FAILS CLOSED. See :func:`resolve_project_trusted_fail_closed` for why
        this may not simply call ``ctx.is_project_trusted()``.

    ``active_tools`` / ``all_tools``
        ``ExtensionContext.get_active_tools()`` and
        ``ExtensionAPI.get_all_tools()``. NAMES ONLY — a ``ToolInfo`` also
        carries a description, and an extension-registered tool's description is
        author-controlled text this snapshot has no reason to re-emit.

    ``loaded_extensions``
        The live discovered-extension list ``entry.py`` already keeps for
        ``/extension`` (``_build_harness_options``'s ``captured_extensions``
        holder, repopulated on every harness rebuild). Reduced to
        :class:`ExtensionSnapshot`.

    ``manifest_api_level``
        ``AELIX_API_LEVEL`` (``contracts/api_level.py:9``), currently ``1``.

        NAMED FOR WHAT IT IS. The issue drafted this as ``extension_api_version``,
        and there is no such thing in this tree: the Python extension surface
        (``ExtensionAPI``) carries no version at all, and the only versioned
        extension contract is the manifest's ``[plugin.api] level`` / ``min_level``
        pair, which is checked against this integer. Shipping it as
        "extension_api_version" would tell a model there is a versioned API
        surface to target, and it would then invent version numbers for one.
    """

    version: str
    cwd: str
    mode: str
    project_trusted: bool
    active_tools: tuple[str, ...]
    all_tools: tuple[str, ...]
    loaded_extensions: tuple[ExtensionSnapshot, ...]
    manifest_api_level: int = AELIX_API_LEVEL

    def to_dict(self) -> dict[str, Any]:
        """The wire shape, spelled out rather than ``asdict``-ed.

        ``dataclasses.asdict`` would work today and would silently start
        exporting whatever a future field holds. This snapshot's whole job is to
        be a narrow surface, so the export is a whitelist too.
        """

        return {
            "version": self.version,
            "cwd": self.cwd,
            "mode": self.mode,
            "project_trusted": self.project_trusted,
            "active_tools": list(self.active_tools),
            "all_tools": list(self.all_tools),
            "loaded_extensions": [
                {
                    "name": e.name,
                    "scope": e.scope,
                    "version": e.version,
                    "has_manifest": e.has_manifest,
                }
                for e in self.loaded_extensions
            ],
            "manifest_api_level": self.manifest_api_level,
        }


def resolve_project_trusted_fail_closed(
    getter: Callable[[], bool] | None,
) -> bool:
    """Project trust, resolved so that "no evidence" means NOT trusted.

    THIS DELIBERATELY DOES NOT CALL ``ctx.is_project_trusted()``. That getter
    reports ``True`` in two different situations and cannot distinguish them:

    * trust was resolved to ``True`` — the real answer; and
    * nothing ever bound it. ``ExtensionContext.__init__`` installs
      ``is_project_trusted or (lambda: True)`` (``extensions/api.py:1135``) and
      ``AgentHarnessOptions.project_trusted`` defaults to ``True``
      (``harness/core.py:289``), both citing pi's ``runner.ts:273`` pre-bind
      default.

    That second case is not hypothetical. ``rpc_ws.py:90-94`` constructs
    ``AgentHarnessOptions`` with three arguments and no ``project_trusted=``, so
    an extension there is told "trusted" by a harness that was never asked.
    Reporting "trusted, go ahead" on the strength of a default is precisely the
    un-protection this issue exists to remove.

    So the only accepted source is a getter wired explicitly by the caller that
    RESOLVED trust — ``entry.py``'s ``_resolve_project_trust`` result. Unwired,
    or raising, is ``False``.

    The cost is stated rather than hidden: under a harness that really is trusted
    but did not wire this, the snapshot under-reports. That direction makes an
    agent more conservative about project-local resources; the other direction
    makes it confident about a permission nobody granted.
    """

    if getter is None:
        return False
    try:
        return bool(getter())
    except Exception:  # noqa: BLE001 — an unreadable trust decision is no decision
        return False


def _is_path_like(value: str) -> bool:
    """Is this ``Extension.name`` a filesystem path rather than a qualname?

    Three shapes reach here from ``_resolve_factory``: an absolute path (the two
    directory tiers and ``-e /abs/x.py``), a relative path (``-e ./x.py``), and a
    Python qualname. ``os.sep`` covers the first two on both platforms;
    ``os.altsep`` is ``"/"`` on Windows and ``None`` elsewhere.

    A trailing ``.py`` is NOT used as the test. ``_resolve_factory`` reaches its
    ``entry.endswith(".py")`` branch for a bare ``"x.py"`` with no separator, but
    so does a dotted module reference ending in a module called ``py``
    (``pkg.py``), and misreading the latter as a path only changes a label.
    """

    if not value:
        return False
    if os.sep in value:
        return True
    return bool(os.altsep) and os.altsep in value


def _within(path: str, root: str) -> bool:
    """Is ``path`` inside ``root``? Lexical, and that is deliberate.

    ``os.path.realpath`` would follow symlinks, which means a snapshot builder
    would stat the filesystem — inside a tool call, on paths supplied by whatever
    the loader found. ``normpath`` + ``abspath`` is enough for a display label,
    and a label is all this is: nothing downstream makes an authority decision
    from ``scope``.

    ``abspath`` anchors a RELATIVE path (only ``-e ./x.py`` produces one; both
    directory tiers hand the loader absolute paths already) to the process cwd,
    which is the same anchor the loader used to open it.
    """

    if not root:
        return False
    root_abs = os.path.normpath(os.path.abspath(root))
    path_abs = os.path.normpath(os.path.abspath(path))
    return path_abs == root_abs or path_abs.startswith(root_abs + os.sep)


def classify_scope(extension: Any, *, cwd: str, agent_dir: str | None) -> str:
    """Which discovery tier did this extension come from?

    ASKED FIRST, ANSWERED BY THE LOADER. ``Extension.source_info`` is the only
    record of a discovery tier that survives onto the object, and since #101's
    M1 review the loader writes it for the endpoint tier
    (``loader._entry_point_source_info``). It is consulted BEFORE the path
    heuristic because the heuristic is wrong for exactly that tier: an installed
    pack's ``resolved_path`` is its site-packages directory, which is path-like
    and inside neither directory tier, so the fallthrough reports ``-e``.

    Everything else is RECONSTRUCTED, because nothing records it:
    ``LoadExtensionsResult`` is a flat ``list[Extension]``. The path used is
    ``resolved_path`` when set (manifest packs, ``loader.py:282``) and ``name``
    otherwise, matched against the two directory tiers documented at
    ``loader.py:457-458``: ``<cwd>/.aelix/extensions`` and
    ``<agent_dir>/extensions``. Everything else path-shaped came from ``-e``.

    getattr-guarded throughout, like ``tui/extension_manager.py``'s builders, so
    an unexpected extension shape produces a label instead of an exception.
    """

    source_info = getattr(extension, "source_info", None)
    if getattr(source_info, "source", None) == _LOADER_ENTRY_POINT_SOURCE:
        return SCOPE_ENTRY_POINT

    raw = getattr(extension, "resolved_path", None) or getattr(extension, "name", "")
    raw = str(raw or "")
    if not _is_path_like(raw):
        return SCOPE_UNCLASSIFIED
    if _within(raw, os.path.join(cwd, ".aelix", "extensions")):
        return SCOPE_PROJECT
    if agent_dir and _within(raw, os.path.join(agent_dir, "extensions")):
        return SCOPE_GLOBAL
    return SCOPE_EXPLICIT


def _extension_label(extension: Any) -> str:
    """A name for an extension that is not a $HOME-bearing absolute path.

    Manifest packs get their ``[plugin] id`` — pattern-constrained, so it cannot
    carry a payload. Everything else gets ``Extension.name``, and when that name
    is a path only its BASENAME survives: the directory tiers set
    ``name = str(<absolute path>)``, measured as
    ``'/tmp/tmp2520a9g8/agent/extensions/globext.py'`` under a temp ``$HOME``
    stand-in, which on a user's machine is their home directory and username.

    ``os.path.basename`` is not enough on its own for a Windows path processed on
    POSIX (``C:\\x\\y.py`` has no ``os.sep``), but such a name is not path-like by
    :func:`_is_path_like` there either, so it is returned whole — a Windows path
    printed on a POSIX host is already an impossible input, not a leak channel.
    """

    manifest = getattr(extension, "manifest", None)
    plugin = getattr(manifest, "plugin", None)
    plugin_id = getattr(plugin, "id", None)
    if plugin_id:
        return bounded_emitted_value(str(plugin_id))
    name = str(getattr(extension, "name", "") or "")
    if not name:
        return "?"
    if _is_path_like(name):
        return bounded_emitted_value(os.path.basename(name.rstrip(os.sep))) or "?"
    return bounded_emitted_value(name)


def _extension_version(extension: Any) -> str | None:
    """The pack's declared version, BOUNDED — see :data:`MAX_EMITTED_CHARS`.

    ``plugin.version``'s schema pattern constrains its shape and not its size,
    so this is the field the M2 review measured a 4138-character payload
    through. ``plugin.id`` above is capped too, for uniformity rather than
    need: its own pattern already stops at 64 characters.
    """

    manifest = getattr(extension, "manifest", None)
    plugin = getattr(manifest, "plugin", None)
    version = getattr(plugin, "version", None)
    return bounded_emitted_value(str(version)) if version else None


def summarise_extensions(
    extensions: Iterable[Any] | None,
    *,
    cwd: str,
    agent_dir: str | None,
) -> tuple[ExtensionSnapshot, ...]:
    """Reduce the live ``list[Extension]`` to the safe projection.

    Order is preserved — it is the load order, which is the order that decides
    hook precedence, so re-sorting would destroy information the model can use.
    """

    out: list[ExtensionSnapshot] = []
    for ext in list(extensions or ()):
        out.append(
            ExtensionSnapshot(
                name=_extension_label(ext),
                scope=classify_scope(ext, cwd=cwd, agent_dir=agent_dir),
                version=_extension_version(ext),
                has_manifest=getattr(ext, "manifest", None) is not None,
            )
        )
    return tuple(out)
