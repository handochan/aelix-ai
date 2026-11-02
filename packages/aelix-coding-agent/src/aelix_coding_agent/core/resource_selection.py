"""The shared resource selection contract (#511, ADR-0279 D3–D7).

One service behind every per-kind on/off panel (``/skill``, ``/tool`` #510,
``/extension`` #25): what both settings files say about each item (*saved*),
what this session runs with (*applied*), what cannot change for this run
(*locked*), how a batch of panel edits is written, and how a saved state is
applied. A kind plugs in through :class:`ResourceKind`, and a kind whose list
is not D1's per-item grammar brings its own :class:`SelectionGrammar`
(``defaultTools``, ADR-0279 D15); nothing here imports prompt_toolkit or knows
what a skill or a tool is.

States (ADR-0279 D5): *unsaved* lives only in the panel; *saved* is a fresh
evaluation of both files on disk; *applied* is what the running session uses;
*pending* is saved ≠ applied and not locked; *locked* is fixed for this run.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

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
Role = Literal["item", "placeholder", "info"]
LockKind = Literal["", "cli", "always_on", "unavailable", "profile", "shadowed", "untrusted"]


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
    role: Role = "item"
    """``item`` is something a list can name. A ``placeholder`` stands for what
    cannot be listed (a server that is off, an untrusted folder) and an ``info``
    row is read-only status (#25's MCP servers, drawn with a dot): neither is
    counted, saved or toggled (#510)."""


@dataclass(frozen=True)
class Lock:
    """A value this run keeps whatever the files say, and why."""

    value: bool
    reason: str
    tag: str = ""
    """The status column's word (``excluded``, ``--tools``, ``server off``…);
    ``""`` draws ``locked`` (``shadowed`` for that kind)."""
    kind: LockKind = ""
    """What fixed it, which picks the state line's wording (#510, ADR-0283):
    ``cli`` and ``profile`` "Locked on/off", ``unavailable`` "Unavailable",
    ``shadowed`` "Not loaded", ``always_on`` "Locked on", ``""`` and
    ``untrusted`` "Locked"."""


@dataclass(frozen=True)
class ItemState:
    item: ResourceItem
    saved: bool
    applied: bool
    decided_by: Scope | None
    lock: Lock | None = None
    problem: str | None = None
    """Why an item that is saved on did not load (#25's failed extension)."""
    live: bool | None = None
    """Whether it is in use right now, when that can differ from the applied
    selection — a tool an extension turned off this run. ``None``: same as
    :attr:`applied`."""

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


class SelectionGrammar(Protocol):
    """How one settings list is read, evaluated and edited (ADR-0279 D15).

    :class:`PerItemGrammar` is D1's: each entry is an opinion about the items
    it names. ``defaultTools`` (#510) is set-level — a plain name replaces the
    built-in set — so it evaluates every item against the whole list and keeps
    an absent key apart from ``[]`` (:attr:`nullable`, Codex F04).
    """

    nullable: bool
    """The list may be ``None`` (absent) as well as ``[]``; ``edit_list`` keeps them apart."""
    checks_both_files: bool
    """A GLOBAL edit is checked against this project's list too (#510 verify V1): the
    save reads it first, refuses when it cannot, and passes it as ``project_list``."""

    def validate(self, raw: object) -> tuple[list[str] | None, list[str]]:
        """``(entries or None when absent, problems)`` for a raw settings value."""
        ...

    def evaluate_all(
        self,
        items: Sequence[ResourceItem],
        global_list: ScopeList | None,
        project_list: ScopeList | None,
    ) -> dict[str, tuple[bool, Scope | None]]:
        """Each item's saved value and the file that decided it, by row key."""
        ...

    def edit(
        self,
        entries: list[str] | None,
        item: ResourceItem,
        want: bool,
        *,
        scope: Scope,
        global_list: ScopeList | None,
        base_dir: str,
        project_list: ScopeList | None = None,
    ) -> list[str] | None:
        """The list after the smallest change that makes *item* ``want`` in *scope*'s file.

        May raise to refuse: the save then writes nothing and reports the message.
        """
        ...


class PerItemGrammar:
    """D1's grammar: :func:`~.resource_filter.effective` and :func:`~.resource_filter.edit`."""

    nullable = False
    checks_both_files = False

    def validate(self, raw: object) -> tuple[list[str] | None, list[str]]:
        entries, bad = validate_entries(raw)
        return entries, bad

    def evaluate_all(
        self,
        items: Sequence[ResourceItem],
        global_list: ScopeList | None,
        project_list: ScopeList | None,
    ) -> dict[str, tuple[bool, Scope | None]]:
        return {item.key: effective(item, global_list, project_list) for item in items}

    def edit(
        self,
        entries: list[str] | None,
        item: ResourceItem,
        want: bool,
        *,
        scope: Scope,
        global_list: ScopeList | None,
        base_dir: str,
        project_list: ScopeList | None = None,
    ) -> list[str] | None:
        inherited = True if scope == "global" else effective(item, global_list, None)[0]
        return edit(list(entries or ()), item, want, inherited, base_dir, list_scope=scope)


_PER_ITEM_GRAMMAR = PerItemGrammar()


def grammar_of(kind: object) -> SelectionGrammar:
    """The kind's own grammar, else D1's."""

    grammar = getattr(kind, "grammar", None)
    return grammar if grammar is not None else _PER_ITEM_GRAMMAR


class ResourceKind(Protocol):
    """One resource type a panel manages: ``SkillKind`` (#511), ``ToolKind`` (#510).

    Optional members, read with ``getattr``: ``grammar`` (a
    :class:`SelectionGrammar`; D1's when absent), ``live()`` (the keys in use
    right now, when an applied selection can be narrowed at run time) and
    ``item_problems()`` (row key → why it failed to load).
    """

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
        grammar: SelectionGrammar = _PER_ITEM_GRAMMAR,
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
        entries, bad = grammar.validate(raw)
        problems.extend(f'{label}: "{key}" entry {p}; it is ignored' for p in bad)
        base = self.global_base if scope == "global" else self.project_base
        return ScopeList(scope, tuple(entries or ()), base, present=entries is not None)

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
        grammar = grammar_of(kind)
        key = kind.settings_key
        global_list = self._scope_list("global", key, problems, unreadable, grammar)
        project_list = self._scope_list("project", key, problems, unreadable, grammar)
        locks = kind.locks()
        applied = kind.applied()
        live_of = getattr(kind, "live", None)
        live = cast("set[str]", live_of()) if callable(live_of) else None
        problems_of = getattr(kind, "item_problems", None)
        failed = cast("Mapping[str, str]", problems_of()) if callable(problems_of) else {}
        items = kind.items()
        values = grammar.evaluate_all(items, global_list, project_list)
        states = []
        for item in items:
            saved, decided_by = values.get(item.key, (True, None))
            states.append(
                ItemState(
                    item,
                    saved,
                    item.key in applied,
                    decided_by,
                    locks.get(item.key),
                    problem=failed.get(item.key),
                    live=(item.key in live) if live is not None else None,
                )
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
        inherited value is the global file's as it is on disk now; a global
        edit under a grammar that :attr:`~SelectionGrammar.checks_both_files`
        is checked against this project's file as it is on disk now (#510
        verify V1). A grammar that refuses an edit writes nothing either.
        """

        manager = self.settings_manager
        if manager is None:
            return SaveResult(False, "settings are not available in this session")
        status = self._status(scope)
        if not status.writable:
            return SaveResult(False, f"Cannot save to {status.path or scope}: {status.reason}")
        before = self.snapshot(kind)
        items = {s.item.key: s.item for s in before.states}
        wanted = {
            k: want
            for k, want in edits.items()
            if k in items and items[k].identities and items[k].role == "item"
        }
        if not wanted:
            return SaveResult(True)
        key = kind.settings_key
        grammar = grammar_of(kind)
        problems: list[str] = []
        unreadable: list[str] = []
        global_now = (
            self._scope_list("global", key, problems, unreadable, grammar)
            if scope == "project"
            else None
        )
        project_now = (
            self._scope_list("project", key, problems, unreadable, grammar)
            if scope == "global" and getattr(grammar, "checks_both_files", False)
            else None
        )
        if unreadable:
            # A project edit's inherited value is the global file's, and a global
            # edit's effect here depends on the project's: unknown either way.
            return SaveResult(False, f"Cannot save: {unreadable[0]}")
        base = self.global_base if scope == "global" else self.project_base
        lists_seen: list[tuple[list[str] | None, list[str] | None]] = []

        def _apply_edits(entries: list[str] | None) -> list[str] | None:
            original = list(entries) if entries is not None else None
            for item_key, want in wanted.items():
                entries = grammar.edit(
                    entries,
                    items[item_key],
                    want,
                    scope=scope,
                    global_list=global_now,
                    base_dir=base,
                    project_list=project_now,
                )
            lists_seen.append((original, list(entries) if entries is not None else None))
            return entries

        try:
            await manager.edit_list(scope, key, _apply_edits, nullable=grammar.nullable)
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
            s.item.key
            for s in snapshot.states
            if s.item.role == "item" and (s.lock.value if s.lock is not None else s.saved)
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
        and reports through *on_deferred*. A second deferral replaces the first;
        busy with nothing pending (the files say what is applied), nothing is
        deferred and an earlier deferral is dropped.

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
        on, off = _delta(snap)
        if not on and not off:
            # Nothing saved differs from what is applied (#510 TUI): there is
            # nothing to wait for, and "applies when the turn ends" would be
            # a promise about nothing.
            return ApplyResult(True)

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


class ApplyGate:
    """Held while a harness is being built; an apply waits for it (#511, Codex X2).

    The runtime keeps its old harness pointer while the factory builds the
    new one, and the factory reads the skills holder (and, #510, the tool
    settings), installs them on the candidate and then AWAITS (session
    recording, extension setup). An apply landing in that window would update
    the outgoing harness while the candidate keeps what it read — a holder, a
    prompt and a harness that disagree. The factory holds this gate for its
    whole build; every kind's ``busy`` is true while it is held, and the waiter
    re-checks the LIVE harness once it opens. The runtime swaps its pointer
    right after the factory returns, with no ``await`` in between, so the next
    check sees the installed harness. :attr:`generation` counts builds. ONE
    gate for every selection (ADR-0283): the factory builds skills and tools
    in the same pass.
    """

    def __init__(self) -> None:
        self._depth = 0
        self._open = asyncio.Event()
        self._open.set()
        self.generation = 0

    @property
    def held(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> ApplyGate:
        self._depth += 1
        self.generation += 1
        self._open.clear()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._open.set()

    async def wait_open(self) -> None:
        await self._open.wait()


def harness_busy(harness: Callable[[], Any], gate: ApplyGate | None) -> bool:
    """A turn is running on the live harness, or a harness is being built (D6)."""

    if gate is not None and gate.held:
        return True
    live = harness()
    return live is not None and not getattr(live, "is_idle", True)


async def wait_harness_idle(harness: Callable[[], Any], gate: ApplyGate | None) -> None:
    """Until the LIVE harness is idle — or has been replaced (C8).

    ``/reload``, ``/new`` and ``/resume`` swap the harness; a waiter parked on
    the old one's idle event returns as soon as the swap is seen, and
    :meth:`ResourceSelection.apply`'s loop re-checks the new one.
    """

    if gate is not None and gate.held:
        await gate.wait_open()
        return
    live = harness()
    if live is None or not harness_busy(harness, gate):
        return
    waiter = asyncio.ensure_future(live.wait_for_idle())
    try:
        while not waiter.done():
            await asyncio.wait({waiter}, timeout=0.25)
            if harness() is not live:
                return
    finally:
        if not waiter.done():
            waiter.cancel()


__all__ = [
    "ApplyGate",
    "ApplyResult",
    "SettingsLists",
    "settings_lists",
    "Effect",
    "ItemState",
    "Lock",
    "LockKind",
    "PerItemGrammar",
    "ResourceItem",
    "ResourceKind",
    "ResourceSelection",
    "Role",
    "SaveResult",
    "ScopeStatus",
    "SelectionGrammar",
    "Snapshot",
    "grammar_of",
    "harness_busy",
    "wait_harness_idle",
]
