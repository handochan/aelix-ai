"""``selections/list`` and ``selections/set``: the panels' selection service over control-v1.

#624, ADR-0305 D1-D10. The same service objects the TUI panels use
(``cli/entry.py``'s ``_selection_controls``), with the same refusal texts
(``core/resource_selection.edit_refusal`` / ``scope_refusal``), the same
save-then-apply (``commit``) and the same no-op drop (inside
``ResourceSelection.save``), so for the same files and the same ordered changes
the TUI and RPC write the same bytes with the same effects.

The wire shape is an explicit camelCase serializer, never ``dataclasses.asdict``
(D3): a future dataclass field must not become a wire field by accident. The TUI's
``/trust`` pointer is not sent: an RPC client decides trust elsewhere, so the two
trust texts are replaced by ``RPC_UNTRUSTED`` wherever they appear (D3, opus L8).
A save error's "reopen /mcp" is "list again" here: an RPC client takes a fresh
snapshot with ``selections/list`` (D3, verifier F4).

The ``mcp`` kind's file is resolved, read and parsed in a worker thread
(``McpSelection.snapshot_off_loop``, Codex CX-10); the settings kinds read their
settings files on the loop, as every other control-v1 method does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.core.mcp_selection import REOPEN_MCP
from aelix_coding_agent.core.resource_selection import (
    CREATE_PROJECT_FILE_HINT,
    CREATE_PROJECT_FILE_NOTE,
    RPC_UNTRUSTED,
    UNTRUSTED_LOCK_REASON,
    UNTRUSTED_SCOPE_REASON,
    ApplyResult,
    ItemState,
    SaveResult,
    Snapshot,
    commit,
    edit_refusal,
    scope_refusal,
)

if TYPE_CHECKING:
    from aelix_coding_agent.rpc.control import RpcControlPlane

# Module-private: bounds on one request's size, not extension-band policy
# constants (tests/agents/test_p2_band_boundaries.py), as control_ui's are.
_MAX_CHANGES = 1024
"""At most this many entries in one ``selections/set`` (D4)."""
_MAX_KEY_CHARS = 8192
"""A row key is at most this many characters (D4, opus L3)."""
QUOTE_CHARS = 120
"""An error quotes at most this many characters of a client's string."""
CONSENT_TIMEOUT_SECONDS = 45.0
"""How long the create-the-project-file dialog waits (D4 "The bound", D6)."""
SCOPES = ("global", "project")
RPC_LIST_AGAIN = "list again"
"""What RPC says instead of the TUI's "reopen /mcp": take a fresh ``selections/list``."""
_DID_NOT_LOAD = " did not load: "
"""How a scope's reason says its file did not load (``McpSelection._scopes``,
``ResourceSelection._status``); an unknown key then names it (live-RPC 2)."""


@dataclass(frozen=True)
class _DialogOptions:
    timeout: float


def rpc_words(text: str) -> str:
    """*text* in RPC words (D3): no ``/trust`` pointer (an RPC client decides trust
    elsewhere) and "list again" for "reopen /mcp" (it lists with ``selections/list``)."""

    return (
        text.replace(UNTRUSTED_SCOPE_REASON, RPC_UNTRUSTED)
        .replace(UNTRUSTED_LOCK_REASON, RPC_UNTRUSTED)
        .replace(f"; {REOPEN_MCP}", f"; {RPC_LIST_AGAIN}")
    )


def _words(text: str | None) -> str | None:
    return None if text is None else rpc_words(text)


def _quote(text: str) -> str:
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS] + "…"


# --- the wire shapes (D2, D3, D4) ---------------------------------------------------


def serialize_row(state: ItemState) -> dict[str, Any]:
    """One row, field by field (D3's table)."""

    item, lock = state.item, state.lock
    return {
        "key": item.key,
        "label": item.label,
        "role": item.role,
        "invocation": item.invocation,
        "description": item.description,
        "group": item.group,
        "groupLabel": item.group_label,
        "location": item.location,
        "identities": list(item.identities),
        "path": item.path,
        "scope": item.scope,
        "sourceBase": item.source_base,
        "packagePath": item.package_path,
        "saved": state.saved,
        "applied": state.applied,
        "inForce": state.in_force,
        "pending": state.pending,
        "decidedBy": state.decided_by,
        "lock": (
            None
            if lock is None
            else {
                "value": lock.value,
                "reason": rpc_words(lock.reason),
                "tag": lock.tag,
                "kind": lock.kind,
                "refusal": rpc_words(lock.refusal),
            }
        ),
        "problem": _words(state.problem),
        "live": state.live,
        "liveNote": list(state.live_note) if state.live_note is not None else None,
        "refusal": _words(edit_refusal(state)),
    }


def serialize_snapshot(snapshot: Snapshot, effect: str) -> dict[str, Any]:
    """``selections/list``'s answer (D2)."""

    return {
        "kind": snapshot.kind,
        "effect": effect,
        "scopes": {
            scope: {
                "path": status.path,
                "writable": status.writable,
                "reason": _words(status.reason),
                "exists": status.exists,
            }
            for scope in SCOPES
            if (status := snapshot.scopes.get(scope)) is not None
        },
        "items": _serialize_rows(snapshot),
        "problems": [rpc_words(p) for p in snapshot.problems],
        "notices": [rpc_words(n) for n in snapshot.notices],
        "unreadable": [rpc_words(u) for u in snapshot.unreadable],
        "policyPending": snapshot.policy_pending,
    }


def _serialize_rows(snapshot: Snapshot) -> list[dict[str, Any]]:
    rows = [serialize_row(state) for state in snapshot.states]
    keys = {row["key"] for row in rows}
    for row in rows:
        key = row["key"]
        try:
            key.encode("utf-8")
        except UnicodeEncodeError as exc:
            if snapshot.kind == "mcp" or row["role"] == "item":
                raise ValueError(
                    f"Cannot list {snapshot.kind}: a selection key is not valid UTF-8"
                ) from exc
            # Read-only rows still need distinct keys for display. Retain all
            # source bytes and reserve the keys of the real rows in this snapshot.
            display_key = "readonly:" + key.encode("utf-8", "surrogatepass").hex()
            if display_key in keys:
                raise ValueError(f"Cannot list {snapshot.kind}: a display key collides") from exc
            keys.add(display_key)
            row["key"] = display_key
        for identity in row["identities"]:
            identity.encode("utf-8")
    return rows


def serialize_save(save: SaveResult | None) -> dict[str, Any]:
    """``written``, ``changed`` and ``mismatches`` of a save (D4); empty when nothing was saved."""

    if save is None:
        return {"written": [], "changed": [], "mismatches": []}
    return {
        "written": list(save.written),
        "changed": [{"label": label, "saved": saved} for label, saved in save.changed],
        "mismatches": [rpc_words(m) for m in save.mismatches],
    }


def serialize_apply(apply: ApplyResult | None) -> dict[str, Any] | None:
    """An :class:`ApplyResult`, or ``None`` where nothing is applied in place (D4)."""

    if apply is None:
        return None
    return {
        "applied": apply.applied,
        "deferred": apply.deferred,
        "turnedOn": list(apply.turned_on),
        "turnedOff": list(apply.turned_off),
        "warning": _words(apply.warning),
    }


# --- the methods --------------------------------------------------------------------


def _control(plane: RpcControlPlane, params: Mapping[str, Any]) -> tuple[str, Any]:
    kind = params.get("kind")
    served = plane.selections
    if not isinstance(kind, str) or kind not in served:
        shown = _quote(kind) if isinstance(kind, str) else type(kind).__name__
        raise ValueError(
            f"Unknown selection kind: {shown} (this host serves: {', '.join(sorted(served))})"
        )
    return kind, served[kind]


def _effect(control: Any) -> str:
    return str(getattr(control.kind, "effect", "next_request"))


async def _snapshot(control: Any) -> Snapshot:
    """The kind's snapshot; a selection that can read off the loop does (Codex CX-10)."""

    off_loop = getattr(control.selection, "snapshot_off_loop", None)
    if off_loop is not None:
        return await off_loop(control.kind)
    return control.selection.snapshot(control.kind)


async def list_selection(plane: RpcControlPlane, params: Mapping[str, Any]) -> dict[str, Any]:
    """``selections/list {kind}``: what both files say, what is applied, and why not (D2)."""

    _kind, control = _control(plane, params)
    return serialize_snapshot(await _snapshot(control), _effect(control))


def _validate_changes(raw: object) -> dict[str, bool]:
    """``changes`` as an ordered ``key -> on``, or the refusal (D4 item 2)."""

    if not isinstance(raw, list):
        raise ValueError("changes must be an array of {key, on}")
    if len(raw) > _MAX_CHANGES:
        raise ValueError(f"changes has {len(raw)} entries; at most {_MAX_CHANGES}")
    edits: dict[str, bool] = {}
    for index, change in enumerate(raw):
        if (
            not isinstance(change, dict)
            or not isinstance(change.get("key"), str)
            or not isinstance(change.get("on"), bool)
        ):
            raise ValueError(f"changes[{index}] must be {{key: string, on: boolean}}")
        key: str = change["key"]
        if len(key) > _MAX_KEY_CHARS:
            raise ValueError(
                f"changes[{index}].key is longer than {_MAX_KEY_CHARS} characters: {_quote(key)}"
            )
        if key in edits:
            raise ValueError(f"changes names {_quote(key)} twice")
        edits[key] = change["on"]
    return edits


def _unknown_row(snapshot: Snapshot, kind: str, key: str) -> str:
    """D4 item 3. When a file did not load, its rows are unknown too: say which, in the
    listing's words (the ``mcp`` kind's rows ARE its file's entries; live-RPC 2)."""

    text = f"Unknown {kind} row: {_quote(key)}"
    if not snapshot.unreadable:
        return text
    reasons = [
        rpc_words(status.reason)
        for scope in SCOPES
        if (status := snapshot.scopes.get(scope)) is not None
        and status.reason is not None
        and _DID_NOT_LOAD in status.reason
    ]
    return "; ".join([text, *reasons])


def _refuse_rows(snapshot: Snapshot, kind: str, edits: Mapping[str, bool]) -> None:
    """A key that is no row, or a row the panel would not let change (D4 items 3, 4)."""

    rows = {state.item.key: state for state in snapshot.states}
    for key in edits:
        state = rows.get(key)
        if state is None:
            raise ValueError(_unknown_row(snapshot, kind, key))
        refusal = edit_refusal(state)
        if refusal is not None:
            raise ValueError(rpc_words(refusal))


def _scope(params: Mapping[str, Any], snapshot: Snapshot, edits: Mapping[str, bool]) -> Any:
    """The scope to write (D4 item 5): required with changes, and writable."""

    scope = params.get("scope")
    if scope is None and not edits:
        return None
    if scope is None:
        raise ValueError("scope is required with changes: global or project")
    if scope not in SCOPES:
        shown = _quote(scope) if isinstance(scope, str) else type(scope).__name__
        raise ValueError(f"Unknown scope: {shown} (global or project)")
    if edits:
        refusal = scope_refusal(snapshot, scope)
        if refusal is not None:
            raise ValueError(rpc_words(refusal))
    return scope


async def _consent_to_new_project_file(
    plane: RpcControlPlane, control: Any, scope: Any, edits: Mapping[str, bool], snapshot: Snapshot
) -> bool:
    """Whether the save may create ``.aelix/settings.json``: the panel's question, as a dialog (D6).

    ``False`` when the save creates nothing. A client boolean is never consent
    (ADR-0275 §8): only a ``confirm`` dialog answered ``confirmed: true``. The
    wait is cancellable by ``commands/cancel`` (D4, opus M2) and bounded.
    """

    if scope is None or not edits or not control.selection.needs_new_project_file(scope, edits):
        return False
    status = snapshot.scopes.get(scope)
    path = (status.path if status is not None else None) or ".aelix/settings.json"
    if not plane.ui.enabled:
        raise ValueError(
            f"Saving to this project creates {path}, which puts the folder under project "
            "trust; turn on dialogs (ui/enable) to confirm it, or create the file first"
        )
    operation = plane.current_operation()
    if operation is not None:
        operation.cancellable = True
    try:
        confirmed = await plane.ui.confirm(
            CREATE_PROJECT_FILE_NOTE,
            CREATE_PROJECT_FILE_HINT,
            _DialogOptions(timeout=CONSENT_TIMEOUT_SECONDS),
        )
    finally:
        if operation is not None:
            # Once the save starts, a cancel could follow a write that happened:
            # ``not_cancellable`` from here on (D4).
            operation.cancellable = False
    if not confirmed:
        raise ValueError(f"Not saved: creating {path} was not confirmed")
    return True


async def set_selection(plane: RpcControlPlane, params: Mapping[str, Any]) -> dict[str, Any]:
    """``selections/set {kind, scope, changes}``: the panel's Enter (D4).

    Refused, writing nothing, in D4's order: the kind; the shape of
    ``changes``; a key that is no row; a row the panel refuses; the scope; the
    project file's creation without consent; then a save that failed.
    """

    kind, control = _control(plane, params)
    edits = _validate_changes(params.get("changes"))
    snapshot = await _snapshot(control)
    _refuse_rows(snapshot, kind, edits)
    scope = _scope(params, snapshot, edits)
    may_create = await _consent_to_new_project_file(plane, control, scope, edits, snapshot)
    save, applied = await commit(
        control.selection,
        control.kind,
        scope,
        edits,
        may_create_project_file=may_create,
    )
    if save is not None and not save.ok:
        raise ValueError(rpc_words(f"Not saved: {save.error}"))
    return {
        "kind": kind,
        "scope": scope,
        "effect": _effect(control),
        **serialize_save(save),
        "apply": serialize_apply(applied),
    }


async def selection_call(
    plane: RpcControlPlane, method: str, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Route ``selections/<verb>`` (``RpcControlPlane._families``)."""

    if method == "selections/list":
        return await list_selection(plane, params)
    if method == "selections/set":
        return await set_selection(plane, params)
    raise ValueError(f"Unsupported runtime control method: {method}")


__all__ = [
    "CONSENT_TIMEOUT_SECONDS",
    "RPC_LIST_AGAIN",
    "list_selection",
    "rpc_words",
    "selection_call",
    "serialize_apply",
    "serialize_row",
    "serialize_save",
    "serialize_snapshot",
    "set_selection",
]
