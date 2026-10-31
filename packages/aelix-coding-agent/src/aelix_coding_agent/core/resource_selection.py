"""The shared resource selection contract (#511, ADR-0279 D3–D7).

One service behind every per-kind on/off panel (``/skill`` now, ``/tool``
#510, ``/extension`` #25): what both settings files say about each item
(*saved*), what this session runs with (*applied*), what cannot change for
this run (*locked*), how a batch of panel edits is written, and how a saved
state is applied. A kind plugs in through :class:`ResourceKind`; nothing here
imports prompt_toolkit or knows what a skill is.

States (ADR-0279 D5): *unsaved* lives only in the panel; *saved* is a fresh
evaluation of both files on disk; *applied* is what the running session uses;
*pending* is saved ≠ applied and not locked; *locked* is fixed for this run.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from .resource_filter import (
    Scope,
    ScopeList,
    edit,
    effective,
    forget_resolved_paths,
    validate_entries,
)

if TYPE_CHECKING:
    from aelix_ai.settings import SettingsManager

Effect = Literal["next_request", "reload"]


@dataclass(frozen=True, kw_only=True)
class ResourceItem:
    """One row of a panel: an item a settings list can name (ADR-0279 D2, C15).

    Five things a single ``key`` used to stand for are kept apart: the row's
    id, the name it is invoked by, the aliases a settings entry matches, the
    scope it was discovered in, and the directory it came from.
    """

    kind: str
    key: str
    """The ROW id, unique per row: a shadowed duplicate shares its winner's name."""
    label: str
    invocation: str = ""
    """The name it is invoked by (``/skill:<invocation>``); ``""`` for a row with none."""
    identities: tuple[str, ...] = ()
    """The aliases ``+X`` / ``-X`` / ``!glob`` match; the first is the form the panel writes."""
    description: str = ""
    group: str = ""
    """Rows with the same group are listed under one header, in first-seen order."""
    group_label: str = ""
    path: str | None = None
    """The item's file, absolute, for the path forms of an entry."""
    scope: str | None = None
    """Where it was discovered: ``cli``, ``project``, ``global`` or ``bundled``."""
    source_base: str | None = None
    """The directory it was discovered under."""
    location: str = ""
    """Where the item lives, shortened for display."""


@dataclass(frozen=True)
class Lock:
    """A value this run keeps whatever the files say, and why."""

    value: bool
    reason: str


@dataclass(frozen=True)
class ItemState:
    item: ResourceItem
    saved: bool
    applied: bool
    decided_by: Scope | None
    lock: Lock | None = None

    @property
    def in_force(self) -> bool:
        """What this run uses (the lock's value, else what is applied)."""

        return self.lock.value if self.lock is not None else self.applied

    @property
    def pending(self) -> bool:
        return self.lock is None and self.saved != self.applied


@dataclass(frozen=True)
class ScopeStatus:
    """Whether a panel may save to a scope, and the file it would write."""

    scope: Scope
    path: str | None
    writable: bool
    reason: str | None = None
    exists: bool = True


@dataclass(frozen=True)
class Snapshot:
    kind: str
    states: tuple[ItemState, ...]
    scopes: Mapping[Scope, ScopeStatus]
    problems: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    """Files that SHOULD have been read and could not be (Codex X3). Not an absent
    or untrusted scope: their entries are unknown, so nothing may be applied
    from this snapshot (an off skill would read as on)."""


@dataclass(frozen=True)
class SaveResult:
    ok: bool
    error: str | None = None
    written: tuple[str, ...] = ()
    """The files whose bytes changed."""
    changed: tuple[tuple[str, bool], ...] = ()
    """``(label, saved value)`` for every item whose saved value moved."""
    mismatches: tuple[str, ...] = ()
    """Items whose saved value is still not what was asked, said in a sentence."""


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    deferred: bool = False
    turned_on: tuple[str, ...] = ()
    turned_off: tuple[str, ...] = ()
    warning: str | None = None


class ResourceKind(Protocol):
    """One resource type a panel manages. #511 ships ``SkillKind``."""

    kind: str
    settings_key: str
    effect: Effect

    def items(self) -> list[ResourceItem]:
        """Every item to list, off ones included. Must never import or run disabled code."""
        ...

    def locks(self) -> dict[str, Lock]: ...

    def applied(self) -> set[str]:
        """Keys this session uses now."""
        ...

    def notices(self) -> list[str]: ...

    def busy(self) -> bool:
        """Whether applying now would land inside a running turn (ADR-0279 D6)."""
        ...

    async def wait_until_idle(self) -> None: ...

    async def apply(self, enabled: set[str]) -> ApplyResult:
        """Make *enabled* the set in use. Called only when :meth:`busy` is false."""
        ...


@dataclass(frozen=True)
class SettingsLists:
    """The two settings files' lists for one resource kind, already validated."""

    global_list: ScopeList | None = None
    project_list: ScopeList | None = None
    problems: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)
    """Scope → the file to name in a warning."""

    def label(self, scope: str) -> str:
        return self.labels.get(scope, f"the {scope} settings")


def settings_lists(
    manager: SettingsManager | None,
    key: str,
    *,
    global_base: str,
    project_base: str,
    project_trusted: bool,
) -> SettingsLists:
    """The lists as this process loaded them (launch, ``/reload``, ``/agents use``).

    The panels read the files again instead (:meth:`ResourceSelection.snapshot`).
    """

    if manager is None:
        return SettingsLists()
    problems: list[str] = []
    labels = {
        scope: str(manager.scope_path(scope) or f"the {scope} settings")
        for scope in ("global", "project")
    }

    def _one(scope: Literal["global", "project"]) -> ScopeList | None:
        if scope == "project" and not project_trusted:
            return None
        settings = (
            manager.get_global_settings() if scope == "global" else manager.get_project_settings()
        )
        raw = getattr(settings, key, None)
        entries, bad = validate_entries(raw)
        problems.extend(f'{labels[scope]}: "{key}" entry {p}; it is ignored' for p in bad)
        return ScopeList(scope, tuple(entries), global_base if scope == "global" else project_base)

    return SettingsLists(_one("global"), _one("project"), tuple(problems), labels)


@dataclass
class ResourceSelection:
    """Read, save and apply selections for any :class:`ResourceKind` (ADR-0279)."""

    settings_manager: SettingsManager | None
    global_base: str
    project_base: str
    project_trusted: bool
    _deferred: dict[str, asyncio.Task[None]] = field(default_factory=dict, repr=False)

    # --- reading -----------------------------------------------------------

    def _project_readable(self) -> bool:
        """Trust as an INPUT to every read and write (ADR-0279 D3, C4).

        Both the launch decision and the manager's live flag must allow it, so
        a revocation after launch is honoured too. When they do not, the project
        file is never opened, locked or even stat'ed: its scope is empty.
        """

        manager = self.settings_manager
        return bool(self.project_trusted and manager is not None and manager.is_project_trusted())

    def _scope_list(
        self,
        scope: Scope,
        key: str,
        problems: list[str],
        unreadable: list[str] | None = None,
    ) -> ScopeList | None:
        """The scope's list, or ``None`` — for an absent or untrusted scope, or a FAILED read.

        A failure is also appended to *unreadable*, so a caller can tell "this
        file says nothing" from "this file could not be read" (Codex X3).
        """

        manager = self.settings_manager
        if manager is None or (scope == "project" and not self._project_readable()):
            return None
        label = self._path(scope) or f"the {scope} settings"
        try:
            raw = manager.read_list(scope, key)
        except Exception as exc:  # noqa: BLE001 — named, never silent
            # ``SettingsFileError`` already names the file; anything else gets the label.
            detail = str(exc) if label in str(exc) else f"{label}: {exc}"
            problems.append(detail)
            if unreadable is not None:
                unreadable.append(detail)
            return None
        entries, bad = validate_entries(raw)
        problems.extend(f'{label}: "{key}" entry {p}; it is ignored' for p in bad)
        base = self.global_base if scope == "global" else self.project_base
        return ScopeList(scope, tuple(entries), base)

    def _path(self, scope: Scope) -> str | None:
        manager = self.settings_manager
        if manager is None:
            return None
        path = manager.scope_path(scope)
        return str(path) if path is not None else None

    def _status(self, scope: Scope) -> ScopeStatus:
        path = self._path(scope)
        manager = self.settings_manager
        if manager is None:
            return ScopeStatus(scope, path, False, "settings are not available here", False)
        if scope == "project" and not self._project_readable():
            # Not even a stat: what an untrusted project holds is not looked at.
            return ScopeStatus(
                scope, path, False, "this folder is not trusted (/trust to decide)", False
            )
        exists = path is not None and os.path.lexists(path)
        error = manager.load_error(scope)
        if error is not None:
            return ScopeStatus(scope, path, False, f"{path or scope} did not load: {error}", exists)
        return ScopeStatus(scope, path, True, None, exists)

    def snapshot(self, kind: ResourceKind) -> Snapshot:
        """A fresh read of both files (the project's only when trusted), evaluated for every item."""

        forget_resolved_paths()  # fresh paths too: a symlink may have been re-pointed (MF1)
        problems: list[str] = []
        unreadable: list[str] = []
        global_list = self._scope_list("global", kind.settings_key, problems, unreadable)
        project_list = self._scope_list("project", kind.settings_key, problems, unreadable)
        locks = kind.locks()
        applied = kind.applied()
        states = []
        for item in kind.items():
            saved, decided_by = effective(item, global_list, project_list)
            states.append(
                ItemState(item, saved, item.key in applied, decided_by, locks.get(item.key))
            )
        return Snapshot(
            kind=kind.kind,
            states=tuple(states),
            scopes={"global": self._status("global"), "project": self._status("project")},
            problems=tuple(problems),
            notices=tuple(kind.notices()),
            unreadable=tuple(unreadable),
        )

    # --- saving ------------------------------------------------------------

    def needs_new_project_file(self, scope: Scope, edits: Mapping[str, bool]) -> bool:
        """Whether saving *edits* to *scope* would create ``.aelix/settings.json`` (ADR-0279 D3)."""

        if scope != "project" or not edits:
            return False
        status = self._status("project")
        return status.writable and not status.exists

    async def save(self, kind: ResourceKind, scope: Scope, edits: Mapping[str, bool]) -> SaveResult:
        """Write *edits* (``row key -> wanted value``) to ONE file, and report what both say now.

        One scope per save (ADR-0279 D4, C20), so a save either happened or did
        not: there is no half-written pair to explain. Refused up front —
        writing nothing — when *scope* cannot be written. A project edit's
        inherited value is the global file's as it is on disk now.
        """

        manager = self.settings_manager
        if manager is None:
            return SaveResult(False, "settings are not available in this session")
        status = self._status(scope)
        if not status.writable:
            return SaveResult(False, f"Cannot save to {status.path or scope}: {status.reason}")
        before = self.snapshot(kind)
        items = {s.item.key: s.item for s in before.states}
        wanted = {k: want for k, want in edits.items() if k in items and items[k].identities}
        if not wanted:
            return SaveResult(True)
        key = kind.settings_key
        problems: list[str] = []
        unreadable: list[str] = []
        global_now = (
            self._scope_list("global", key, problems, unreadable) if scope == "project" else None
        )
        if unreadable:
            # A project edit's inherited value is the global file's: unknown here.
            return SaveResult(False, f"Cannot save: {unreadable[0]}")
        base = self.global_base if scope == "global" else self.project_base
        lists_seen: list[tuple[list[str], list[str]]] = []

        def _apply_edits(entries: list[str]) -> list[str]:
            original = list(entries)
            for item_key, want in wanted.items():
                item = items[item_key]
                inherited = True if scope == "global" else effective(item, global_now, None)[0]
                entries = edit(entries, item, want, inherited, base, list_scope=scope)
            lists_seen.append((original, list(entries)))
            return entries

        try:
            await manager.edit_list(scope, key, _apply_edits)
        except Exception as exc:  # noqa: BLE001 — the panel shows it and stays open
            return SaveResult(False, f"{exc}")
        written = (
            (status.path or scope,) if lists_seen and lists_seen[-1][0] != lists_seen[-1][1] else ()
        )
        after = self.snapshot(kind)
        before_saved = {s.item.key: s.saved for s in before.states}
        changed = tuple(
            (s.item.label, s.saved)
            for s in after.states
            if before_saved.get(s.item.key) is not None and before_saved[s.item.key] != s.saved
        )
        mismatches: list[str] = []
        for state in after.states:
            want = wanted.get(state.item.key)
            if want is None or state.saved == want:
                continue
            where = "this project's file" if state.decided_by == "project" else "the global file"
            mismatches.append(
                f"{state.item.label}: saved, but {where} keeps it "
                f"{'on' if state.saved else 'off'} here"
            )
        return SaveResult(True, None, written, changed, tuple(mismatches))

    # --- applying ----------------------------------------------------------

    def enabled(self, snapshot: Snapshot) -> set[str]:
        """The keys a fresh *snapshot* says should be in use: locks first, then saved."""

        return {
            s.item.key for s in snapshot.states if (s.lock.value if s.lock is not None else s.saved)
        }

    async def apply(
        self,
        kind: ResourceKind,
        snapshot: Snapshot | None = None,
        *,
        on_deferred: Callable[[ApplyResult], None] | None = None,
    ) -> ApplyResult:
        """Apply what the files say now, or as soon as the session is idle (ADR-0279 D6).

        Idle: applied before this returns. Busy (a turn started by an extension
        or a background child): a task waits for idle, takes a FRESH snapshot
        and applies that — so of several saves made meanwhile the newest wins —
        and reports through *on_deferred*. A second deferral replaces the first.

        THE BOUNDARY IS EXCLUSIVE BECAUSE NOTHING YIELDS (C8). ``wait_for_idle``
        only says the harness WAS idle; another prompt may claim it before the
        waiter runs. So the waiter re-checks :meth:`ResourceKind.busy` and, when
        it is false, takes the snapshot and calls :meth:`ResourceKind.apply`
        with no ``await`` in between — and a kind's ``apply`` commits before its
        own first ``await``. ``AgentHarness.prompt`` claims its turn
        synchronously too (``harness/core.py``, "Flip phase synchronously
        BEFORE the first await"), so on one event loop the two cannot
        interleave. The kind reads the LIVE harness each time, so a
        ``/reload``, ``/new`` or ``/resume`` that replaced it while the waiter
        waited is applied to the replacement, not the disposed one.
        """

        snap = snapshot if snapshot is not None else self.snapshot(kind)
        if snap.unreadable:
            return _refused(snap)
        if not kind.busy():
            return await kind.apply(self.enabled(snap))
        previous = self._deferred.pop(kind.kind, None)
        if previous is not None and not previous.done():
            previous.cancel()

        async def _later() -> None:
            await kind.wait_until_idle()
            while kind.busy():
                await kind.wait_until_idle()
            try:
                # Inside the ``try`` (round 2, R6): a raising read is reported too.
                fresh = self.snapshot(kind)
                result = (
                    _refused(fresh) if fresh.unreadable else await kind.apply(self.enabled(fresh))
                )
            except Exception as exc:  # noqa: BLE001 — reported, never raised into the loop
                result = ApplyResult(False, warning=f"{exc}")
            if on_deferred is not None:
                on_deferred(result)

        self._deferred[kind.kind] = asyncio.get_running_loop().create_task(_later())
        on, off = _delta(snap)
        return ApplyResult(False, deferred=True, turned_on=on, turned_off=off)


def _refused(snapshot: Snapshot) -> ApplyResult:
    """Nothing applied: a file that should have been read could not be (Codex X3).

    The session keeps the selection it applied last; applying from a snapshot
    that is missing a file's entries would turn its off skills back on.
    """

    return ApplyResult(False, warning=f"kept what was applied: {snapshot.unreadable[0]}")


def _delta(snapshot: Snapshot) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(turned_on, turned_off)`` labels between what is applied and what is saved."""

    on = tuple(s.item.label for s in snapshot.states if s.pending and s.saved)
    off = tuple(s.item.label for s in snapshot.states if s.pending and not s.saved)
    return on, off


__all__ = [
    "ApplyResult",
    "SettingsLists",
    "settings_lists",
    "Effect",
    "ItemState",
    "Lock",
    "ResourceItem",
    "ResourceKind",
    "ResourceSelection",
    "SaveResult",
    "ScopeStatus",
    "Snapshot",
]
