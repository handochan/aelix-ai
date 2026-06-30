"""Sprint P0 #10 — Project Trust (Option A+: minimal gate + on-disk store).

Gates the two arbitrary-code-execution surfaces aelix exposes from an
**auto-discovered** project-local directory:

- ``cwd/.aelix/extensions/`` — project-local ``.py`` files / packages that
  ``importlib`` ``exec_module``'s arbitrary Python with full user privileges.
- ``cwd/.aelix/mcp.json`` — project-local MCP server declarations that spawn
  arbitrary subprocesses on connect.

Explicit ``-e <path>`` extensions, ``$AELIX_MCP_CONFIG`` / global MCP, and
installed entry-point extensions are **USER choices**, not project-local
auto-discovery, so they are NEVER gated. ``AGENTS.md`` is also NOT gated
(pi parity: it is markdown context, not code — ``trust-manager.ts`` does not
list it among the trust-requiring resources).

This is a since-pin pi feature (pi added Project Trust after aelix's pin
``734e08e``). Ground truth is pi HEAD:

- ``core/trust-manager.ts`` — ``ProjectTrustStore`` (disk persistence),
  ``hasTrustRequiringProjectResources``, ``getProjectTrustOptions``.
- ``core/project-trust.ts:46-96`` — ``resolveProjectTrusted`` orchestrator.
- ``cli/project-trust.ts`` — the UI bridge (TUI vs non-interactive).

Aelix narrows the resource set to the two live surfaces (pi's
``settings.json``/``skills``/``prompts``/``themes``/``SYSTEM.md``/
``APPEND_SYSTEM.md`` loaders do not exist in aelix yet — Sprint spec §2.2).

Issue #5 (Lane C) closed ONE of the originally-deferred protected-core items
end-to-end — ``ctx.is_project_trusted()`` (the event types live in
``aelix-agent-core`` :mod:`aelix_agent_core.harness.hooks`; the context bridge is
wired by the harness and reaches production). It also *implements and tests* the
``project_trust`` extension event (:func:`emit_project_trust_event`) and the
``defaultProjectTrust`` handling inside :func:`resolve_project_trusted`, so the
orchestrator can follow pi's full order when those inputs are supplied.

Those last two, however, remain **deferred at the production bootstrap**: the
sole CLI caller (``entry.py`` ``_resolve_project_trust``) does not yet pass
``extensions=`` or ``default_project_trust=``, so in the shipped CLI the
``project_trust`` event is never fired and ``defaultProjectTrust`` is always
treated as ``"ask"``. Wiring them at the call site is a follow-up — it needs the
user/global extensions loaded BEFORE trust resolution plus a ``SettingsManager``
source for the default (related to #44).

Non-interactive (print/json/rpc) default is **DENY** (pi parity,
``project-trust.ts:86-88``): without ``--approve`` and without a persisted
decision, an untrusted directory drops its project-local resources.

Persistence (Option A+): on-disk ``<agent_dir>/trust.json`` (i.e.
``~/.aelix/agent/trust.json``), pi ``ProjectTrustStore`` shape — a
``Record<absCanonicalPath, bool | None>`` map, JSON object with keys sorted,
nearest-ancestor walk (trusting a parent transitively trusts children; a
child ``False`` overrides an ancestor ``True``). Writes are atomic
(temp file + ``os.replace``). The lock strategy is **best-effort**: aelix
does not depend on ``filelock`` / ``proper-lockfile``, so we rely on the
atomic temp+rename for crash-safety and accept single-process best-effort
for concurrent writers (a single-user-local product almost never has two
aelix processes racing the same ``trust.json``). This is documented as an
intentional simplification of pi's ``proper-lockfile`` sync lock.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from aelix_agent_core.harness.hooks import (
    ProjectTrustContext,
    ProjectTrustEventResult,
    ProjectTrustHookEvent,
)

from .config import CONFIG_DIR_NAME, get_agent_dir

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from typing import Any

# Pi ``DefaultProjectTrust`` (``settings-manager.ts:61``): the global-only
# "default project trust" setting consulted AFTER the extension event + the
# on-disk store but BEFORE prompting. Default ``"ask"``.
DefaultProjectTrust = Literal["ask", "always", "never"]

__all__ = [
    "DefaultProjectTrust",
    "ProjectTrustPromptResult",
    "ProjectTrustStore",
    "emit_project_trust_event",
    "format_project_trust_prompt",
    "has_trust_requiring_project_resources",
    "project_trust_options",
    "resolve_project_trusted",
]


# === Resource detection (pi ``hasTrustRequiringProjectResources``) ===========


def has_trust_requiring_project_resources(cwd: Path) -> bool:
    """Pi parity (narrowed): does ``cwd`` hold gate-requiring project resources?

    Pi source: ``trust-manager.ts:184-206`` checks ``cwd/.pi/<7 resources>``
    plus ``.agents/skills``. Aelix narrows this to the two surfaces it
    actually loads (Sprint spec §2.2):

    Returns ``True`` iff EITHER:

    - ``cwd/.aelix/extensions/`` exists as a directory with at least one
      entry (an empty dir loads nothing → no gate), OR
    - ``cwd/.aelix/mcp.json`` is a file.

    Otherwise ``False`` → there is nothing dangerous to gate, so
    :func:`resolve_project_trusted` trusts the directory without prompting
    (pi ``project-trust.ts:60-62``).
    """

    aelix_dir = cwd / CONFIG_DIR_NAME

    extensions_dir = aelix_dir / "extensions"
    try:
        if extensions_dir.is_dir() and any(extensions_dir.iterdir()):
            return True
    except OSError:
        # Unreadable directory — treat as no discoverable resources.
        pass

    mcp_json = aelix_dir / "mcp.json"
    try:
        if mcp_json.is_file():
            return True
    except OSError:
        pass

    return False


# === Prompt contract (the UI returns this) ==================================


@dataclass(frozen=True)
class ProjectTrustPromptResult:
    """Result of a trust prompt — pi ``ProjectTrustUpdate`` (narrowed).

    :param trusted: the resolved decision for this run.
    :param remember: persist the decision to ``trust.json`` when ``True``;
        ``False`` for the "session only" options (pi's session-only options
        carry empty ``updates`` → nothing written).
    :param target: the directory the decision applies to. ``None`` means
        the active ``cwd`` (the common case); a "Trust parent folder" option
        sets this to the parent so the store entry covers the parent (and,
        via nearest-ancestor inheritance, its children).
    """

    trusted: bool
    remember: bool
    target: Path | None = None


# === Prompt wording + options (pi ``getProjectTrustOptions``) ===============


def format_project_trust_prompt(cwd: Path) -> str:
    """Pi parity (``.aelix`` substituted): the trust prompt body.

    Pi source: ``project-trust.ts:24-26`` ``formatProjectTrustPrompt``.
    """

    return (
        "Trust project folder?\n"
        f"{cwd}\n\n"
        "This allows Aelix to load .aelix extensions and MCP servers, "
        "which can execute arbitrary code on your machine."
    )


# Option labels (pi ``getProjectTrustOptions(cwd, {includeSessionOnly:true})``,
# ``.aelix`` narrowed). The "session only" variants are NOT persisted.
_OPT_TRUST = "Trust"
_OPT_TRUST_SESSION = "Trust (this session only)"
_OPT_NO_TRUST = "Do not trust"
_OPT_NO_TRUST_SESSION = "Do not trust (this session only)"
_OPT_TRUST_PARENT_FMT = "Trust parent folder ({parent})"


def project_trust_options(cwd: Path, *, include_parent: bool = True) -> list[str]:
    """Pi-faithful option list for the trust selector.

    Order mirrors pi's ``getProjectTrustOptions``: Trust, then the optional
    Trust-parent, then the session-only + do-not-trust variants. ``cwd`` is
    canonicalized for the parent label only; the parent option is omitted at
    the filesystem root (no distinct parent).
    """

    options = [_OPT_TRUST]
    if include_parent:
        parent = cwd.parent
        if parent != cwd:
            options.append(_OPT_TRUST_PARENT_FMT.format(parent=parent))
    options.extend([_OPT_TRUST_SESSION, _OPT_NO_TRUST, _OPT_NO_TRUST_SESSION])
    return options


def interpret_trust_option(label: str, cwd: Path) -> ProjectTrustPromptResult:
    """Map a selected option label back to a :class:`ProjectTrustPromptResult`.

    The "session only" options set ``remember=False`` (not persisted). The
    "Trust parent folder" option targets the parent so the persisted entry
    covers it (and inherits to children via the nearest-ancestor walk).
    """

    if label == _OPT_TRUST:
        return ProjectTrustPromptResult(trusted=True, remember=True)
    if label.startswith("Trust parent folder"):
        return ProjectTrustPromptResult(
            trusted=True, remember=True, target=cwd.parent
        )
    if label == _OPT_TRUST_SESSION:
        return ProjectTrustPromptResult(trusted=True, remember=False)
    if label == _OPT_NO_TRUST:
        return ProjectTrustPromptResult(trusted=False, remember=True)
    if label == _OPT_NO_TRUST_SESSION:
        return ProjectTrustPromptResult(trusted=False, remember=False)
    # Defensive: an unknown label denies for this session (never persisted).
    return ProjectTrustPromptResult(trusted=False, remember=False)


# === On-disk store (pi ``ProjectTrustStore``) ===============================


class ProjectTrustStore:
    """On-disk per-project trust map (pi ``trust-manager.ts:208-244``).

    File: ``<agent_dir>/trust.json`` (default ``~/.aelix/agent/trust.json``).
    Shape: a JSON object ``{<absCanonicalPath>: true | false | null}``, keys
    sorted on write. ``get`` walks UP the directory tree to the nearest
    decided entry (pi ``findNearestTrustEntry``), so trusting a parent
    transitively trusts children and a child ``False`` overrides an ancestor
    ``True``. ``null`` values are treated as "undecided" (skipped by the walk
    and never written by :meth:`set`).

    Validation (pi ``readTrustFile``): the top level MUST be a JSON object and
    every value MUST be ``true | false | null`` — otherwise :class:`ValueError`.
    Writes are atomic (temp file + ``os.replace``). Best-effort locking only
    (see module docstring).
    """

    def __init__(self, agent_dir: str | Path | None = None) -> None:
        base = Path(agent_dir) if agent_dir is not None else Path(get_agent_dir())
        self._path = base / "trust.json"

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def _canonical(cwd: Path) -> str:
        """Canonical absolute key (pi uses ``realpath``-style canonical paths)."""

        try:
            return str(Path(os.path.abspath(cwd)).resolve(strict=False))
        except OSError:
            return str(Path(os.path.abspath(cwd)))

    def _read(self) -> dict[str, bool | None]:
        """Load + validate the store; missing/empty file → ``{}``.

        Raises:
            ValueError: when the file is not a JSON object or holds a value
            outside ``true | false | null`` (pi ``readTrustFile`` throws).
        """

        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ValueError(f"Cannot read trust store {self._path}: {exc}") from exc
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid trust store {self._path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"Invalid trust store {self._path}: top level must be an object"
            )
        out: dict[str, bool | None] = {}
        for key, value in data.items():
            if value is not None and not isinstance(value, bool):
                raise ValueError(
                    f"Invalid trust store {self._path}: value for {key!r} "
                    f"must be true, false, or null"
                )
            out[str(key)] = value
        return out

    def _write(self, data: dict[str, bool | None]) -> None:
        """Atomic write (temp + ``os.replace``); keys sorted (pi parity)."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {k: data[k] for k in sorted(data)}, indent=2, sort_keys=True
        )
        # Atomic temp+rename so a partial write never corrupts the store and a
        # concurrent reader sees either the old or the new file (best-effort
        # lock — see module docstring). ``os.replace`` is atomic on the same fs.
        tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        finally:
            # Clean up the temp file if the replace failed mid-flight.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def get(self, cwd: Path) -> bool | None:
        """Nearest-ancestor decision for ``cwd`` (pi ``findNearestTrustEntry``).

        Walks UP from the canonical ``cwd`` to the filesystem root, returning
        the first non-``None`` decision found. ``None`` means "undecided" —
        no ancestor has a stored ``True``/``False``.

        A malformed store raises :class:`ValueError` (the caller decides
        whether to surface it or fail safe).
        """

        data = self._read()
        if not data:
            return None
        current = Path(self._canonical(cwd))
        while True:
            key = str(current)
            if key in data and data[key] is not None:
                return data[key]
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def set(self, cwd: Path, trusted: bool) -> None:
        """Persist a decision for the canonical ``cwd`` (pi ``store.set``).

        Reads, mutates, and atomically rewrites the store. A malformed
        existing store raises :class:`ValueError` (we refuse to clobber a file
        we cannot parse).
        """

        data = self._read()
        data[self._canonical(cwd)] = trusted
        self._write(data)


# === The project_trust extension event (pi ``emitProjectTrustEvent``) =======


async def emit_project_trust_event(
    extensions: Sequence[Any],
    event: ProjectTrustHookEvent,
    ctx: ProjectTrustContext,
) -> tuple[ProjectTrustEventResult | None, list[str]]:
    """Pi parity ``emitProjectTrustEvent`` (``extensions/runner.ts:197-227``).

    Walks every extension's ``project_trust`` handlers in registration order.
    The FIRST handler returning a ``"yes"``/``"no"`` decision wins;
    ``"undecided"`` (or no handlers) falls through. Handler exceptions are
    collected (never raised) so one bad extension cannot abort startup.

    Only *already-loaded* extensions get a vote — at trust-resolution time the
    untrusted project-local tier has NOT been loaded yet, so this is the
    user-trusted (explicit ``-e`` / global / entry-point) surface deciding
    whether to trust the project, exactly mirroring Pi's security model.

    Returns ``(result, errors)`` where ``result`` is the deciding
    :class:`ProjectTrustEventResult` (or ``None`` when every handler deferred)
    and ``errors`` is a list of formatted handler-error messages.
    """

    errors: list[str] = []
    for ext in extensions:
        handlers_map = getattr(ext, "handlers", None)
        if not handlers_map:
            continue
        handlers = handlers_map.get("project_trust")
        if not handlers:
            continue
        for handler in handlers:
            try:
                raw = handler(event, ctx)
                result = await raw if inspect.isawaitable(raw) else raw
            except Exception as exc:  # noqa: BLE001 — one bad ext never aborts startup
                name = getattr(ext, "name", None) or getattr(ext, "path", "?")
                errors.append(
                    f'Extension "{name}" project_trust error: {exc}'
                )
                continue
            # A handler that returns ``None``/``undecided`` defers to the next.
            if result is None or result.trusted == "undecided":
                continue
            return result, errors
    return None, errors


# === The orchestrator (pi ``resolveProjectTrusted`` — event now wired) ======


# A prompt callback: given the cwd, return the user's choice, or ``None`` on
# cancel (Esc / Ctrl+C). The caller (entry.py) supplies a closure that drives
# the one-shot TUI selector (A1 seam).
if TYPE_CHECKING:
    PromptCallback = Callable[[Path], Awaitable[ProjectTrustPromptResult | None]]


async def resolve_project_trusted(
    cwd: Path,
    *,
    override: bool | None,
    has_ui: bool,
    prompt: PromptCallback | None = None,
    store: ProjectTrustStore | None = None,
    agent_dir: str | Path | None = None,
    extensions: Sequence[Any] | None = None,
    project_trust_ctx: ProjectTrustContext | None = None,
    default_project_trust: DefaultProjectTrust = "ask",
    on_extension_error: Callable[[str], None] | None = None,
) -> bool:
    """Resolve whether ``cwd``'s project-local resources are trusted.

    Pi source: ``project-trust.ts:46-96`` ``resolveProjectTrusted``. Issue #5
    (Lane C) *implements and tests* the ``project_trust`` extension event and the
    ``defaultProjectTrust`` setting inside this orchestrator (both were stubbed
    out in the original Option A+ landing). NOTE: these two steps fire only when
    the caller threads ``extensions=`` / ``default_project_trust=``; the
    production CLI bootstrap (``entry.py`` ``_resolve_project_trust``) does not
    yet pass either, so in the shipped CLI the event step and
    ``defaultProjectTrust`` remain DEFERRED (call-site wiring is a follow-up —
    see the module docstring).

    Resolution order (pi-faithful):

    1. ``override`` (``--approve`` / ``--no-approve``): short-circuit
       (no prompt, no persistence).
    2. No trust-requiring resources → ``True`` (nothing dangerous to gate).
    3. ``project_trust`` extension event (when ``extensions`` supplied): the
       first ``"yes"``/``"no"`` decision wins; ``remember=True`` persists it.
       Handler errors are reported via ``on_extension_error``.
    4. Persisted ``trust.json`` decision (nearest-ancestor) → return it if
       not ``None``.
    5. ``default_project_trust``: ``"always"`` → ``True``, ``"never"`` →
       ``False``, ``"ask"`` → fall through.
    6. ``has_ui`` → prompt; otherwise **DENY** (``False``) — non-interactive
       deny-by-default (pi ``project-trust.ts:86-88``).
    7. On a prompt result: persist unless "session only", then return its
       ``trusted``. A cancelled prompt (``None``) → ``False``.

    Backward-compat: with ``extensions=None`` and ``default_project_trust="ask"``
    (the defaults), steps 3 and 5 are inert, so the resolution reproduces the
    original Option A+ order exactly.

    A malformed store is treated as "no decision" (we do NOT block startup on
    a corrupt trust file — we fall through to the prompt/deny path, which is
    the safe direction).
    """

    # 1. Explicit override wins (no prompt, no persist).
    if override is not None:
        return override

    # 2. Nothing dangerous to gate → trust.
    if not has_trust_requiring_project_resources(cwd):
        return True

    trust_store = store if store is not None else ProjectTrustStore(agent_dir)

    # 3. project_trust extension event (pi: emitted BEFORE the store lookup).
    if extensions:
        ctx = project_trust_ctx or ProjectTrustContext(
            cwd=str(cwd), has_ui=has_ui
        )
        result, errors = await emit_project_trust_event(
            extensions, ProjectTrustHookEvent(cwd=str(cwd)), ctx
        )
        if on_extension_error is not None:
            for message in errors:
                on_extension_error(message)
        if result is not None:
            trusted = result.trusted == "yes"
            if result.remember is True:
                # Honor the in-session decision even if persistence fails.
                with contextlib.suppress(ValueError, OSError):
                    trust_store.set(cwd, trusted)
            return trusted

    # 4. Persisted decision (nearest-ancestor walk).
    try:
        decision = trust_store.get(cwd)
    except ValueError:
        # Corrupt store → fall through to prompt/deny (safe direction).
        decision = None
    if decision is not None:
        return decision

    # 5. defaultProjectTrust (global-only setting). "ask" falls through.
    if default_project_trust == "always":
        return True
    if default_project_trust == "never":
        return False

    # 6. No UI → deny-by-default (pi non-interactive default).
    if not has_ui or prompt is None:
        return False

    # 7. Prompt the user.
    result = await prompt(cwd)
    if result is None:
        # Cancelled (Esc / Ctrl+C) → deny.
        return False
    if result.remember:
        target = result.target if result.target is not None else cwd
        # Persisting failed (corrupt/unwritable store) — honor the in-session
        # decision anyway; do not crash the run.
        with contextlib.suppress(ValueError, OSError):
            trust_store.set(target, result.trusted)
    return result.trusted
