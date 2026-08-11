"""ImplConsumers (ADR-0161) — the ``/scoped-models`` interactive flow.

Mirrors :func:`aelix_coding_agent.tui.model_picker.run_model_picker`: the WHOLE
flow is module-level + dependency-injected (duck-typed ``registry`` /
``settings_manager`` + ``multiselect`` / ``commit`` callables) so it is
unit-testable without standing up the prompt-toolkit app. ``shell.py`` wires the
live :class:`ModelRegistry` + held :class:`SettingsManager` +
:meth:`AelixTUIContext.multiselect` into it.

The user multi-selects which catalog models are *enabled* (the pi ``/scoped-models``
allow-list, backed by ``Settings.enabled_models``). On confirm:

* every model checked → ``set_enabled_models(None)`` (the canonical "all enabled"
  sentinel, so the catalog isn't pinned to today's id list);
* a subset → ``set_enabled_models(sorted(keys))`` where each key is the canonical
  ``provider/id`` (so two providers sharing a bare id stay distinct, and a legacy
  bare-id allow-list is transparently re-persisted in canonical form here);

then ``flush()`` lands the write and the flow re-reads ``get_enabled_models()`` to
commit a round-trip confirmation line. The setter mutates the merged view
synchronously, so the read-back is reliable even before the disk task lands.

ENFORCEMENT IS ACTIVE (ADR-0162): the allow-list this command persists now
RESTRICTS the model list the user sees/selects. The intersection lives in
:func:`aelix_coding_agent.core.scoped_models_filter.scoped_available` (read LIVE
on every call, so a change here takes effect on the next ``/model`` open with no
restart). :meth:`ModelRegistry.get_available` itself is UNCHANGED (auth-only, pi
parity); ``scoped_available`` is layered at the consumers. Scoped consumers: the
``/model`` picker (headline) and ``--list-models`` (CLI parity). An empty-match
allow-list degrades to the full list (never a lockout). PARTIAL SCOPE: the RPC
``set_model`` / ``cycle_model`` / ``get_available_models`` handlers are NOT
scoped this turn (the protected harness has no model-list rotation, but RPC's
``run_rpc_mode`` is not threaded a ``SettingsManager`` today) — an external RPC
client can still reach a disabled model; the scope is a TUI/CLI-surface guard,
not a hard policy boundary. The startup model resolution
(``resolve_cli_model`` / current selection) is intentionally NOT scoped so a
default/chosen model outside the allow-list stays usable.

THE SEED IS DELIBERATELY UNSCOPED: this picker keeps seeding from the FULL
auth-filtered :meth:`ModelRegistry.get_available` (not the scoped helper) so a
previously-DISABLED model is still visible + re-checkable here — scoping the seed
would make a disabled model invisible and permanently un-re-enableable.

RUNNABILITY (#153): ``get_available`` is AUTH-only, so it offered checkboxes for
models this build cannot run at all — with keys held for mistral / cloudflare /
azure it offered 113 of them, every one unrunnable, while ``/model`` hid all 113.
Ticking one narrows the allow-list that then RESTRICTS ``/model``. The picker now
splits by REASON CLASS (see ``core.runnable_models``):

* a RECOVERABLE block (``config-missing`` / ``vertex-config-missing`` /
  ``no-host``) is shown, tickable, and ANNOTATED — ``/scoped-models`` declares an
  intent for later, and one env var away is exactly what a user comes here to
  enable (the ``/login`` precedent, #151, not ``/model``'s hide-it precedent);
* a DEAD END (``no-adapter`` / ``unresolved-api``) is HIDDEN and counted, because
  no configuration makes it run in this build — UNLESS it is already in an
  EXPLICIT allow-list, in which case it stays visible and pre-ticked so the user
  can still remove it.

Three invariants hold across that filtering.

1. NOTHING PERSISTED IS SILENTLY DROPPED. Every seeded id is OFFERED (that is
   what the dead-end carve-out is for), and any that somehow was not is carried
   forward into the saved list — so a model that is merely unrunnable TODAY keeps
   its scoping.
2. NOTHING HIDDEN IS SILENTLY WRITTEN. The seed is the offered rows under the
   ``None`` sentinel, and the persisted list under an explicit one.
   ``multiselect`` returns its internal set seeded from ``selected`` and only
   ever toggles a RENDERED row, so seeding from the whole catalog would hand
   every hidden id back on the first save — the hiding would undo itself in one
   keystroke and fill the user's allow-list with ids the build cannot run.
3. "ALL MODELS" STAYS REACHABLE. "Everything ticked → ``None``" is measured
   against what the picker could OFFER (plus what it carried), not against the
   whole catalog: a target containing untickable rows is a target no user can
   hit, which would pin the open-ended sentinel to a concrete id list forever.

HONEST CONSTRAINT: ``set_enabled_models`` writes the GLOBAL scope only — there is
NO ``set_project_enabled_models`` on the SettingsManager surface, so this is a
global allow-list (pi parity). Per-project would need a forbidden aelix-ai edit.

Every failure mode (no registry, no SettingsManager, list failure, empty catalog,
the setter raising, Esc) surfaces a committed message and returns — never crashes
the REPL.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# :meth:`AelixTUIContext.multiselect` renders an option row as
# ``"▸ " + "[✓] " + label`` (6 columns of chrome) inside a frame capped at
# ``context._PICK_MAX_WIDTH`` (78), and it NEVER wraps or truncates a row — a
# longer row is clipped by the terminal. Measured on the real catalog, a per-row
# env-var hint put 70 of 139 rows past 78 columns and the clip landed mid-token
# (``…claude-3-5-haiku  (needs setup: set``), hiding the second variable name
# entirely. So the label carries a SHORT marker and is fitted to this budget;
# the variable names are named once, in the grouped line under the list.
# ``tests/tui/test_scoped_models_runnability.py`` pins this against the widget's
# own constant so the two cannot drift.
_ROW_CHROME = 6
_LABEL_BUDGET = 78 - _ROW_CHROME

# The label-sized verdicts. Deliberately reason-CLASS words, not reasons: the
# reasons themselves (env var names) are too long for a row and are printed once
# in the grouped ``blocked_summary`` line, where they wrap instead of clipping.
_NEEDS_SETUP = "  (needs setup)"
_UNUSABLE = "  (unusable)"


def _display_width(text: str) -> int:
    """Display COLUMNS of ``text`` (East-Asian wide chars count 2).

    The same wcwidth-backed measure ``context._visible_len`` uses to size the
    picker frame — a label measured in codepoints would under-count a CJK
    provider name and overflow the frame anyway. Falls back to ``len`` if
    prompt-toolkit is unavailable (this module is import-light on purpose).
    """

    try:
        from prompt_toolkit.utils import get_cwidth

        return get_cwidth(text)
    except Exception:  # noqa: BLE001 — sizing must never break a flow
        return len(text)


def _fit(label: str, limit: int = _LABEL_BUDGET) -> str:
    """``label`` shortened with a MIDDLE ellipsis to at most ``limit`` columns.

    Middle rather than tail because both ends carry identity: the head is
    ``[provider]`` and the tail is what distinguishes
    ``…/nemotron-3-120b-a12b`` from its siblings. The picker's substring filter
    also matches the row's ``id`` key (the untruncated canonical
    ``provider/id``), so a row shortened here is still reachable by typing the
    full model id.
    """

    if limit < 8 or _display_width(label) <= limit:
        return label
    keep = limit - 1  # one column for the ellipsis
    head = (keep + 1) // 2
    tail = keep - head

    def _built(h: int) -> str:
        return f"{label[:h]}…{label[-tail:]}" if tail else f"{label[:h]}…"

    out = _built(head)
    while head > 1 and _display_width(out) > limit:  # wide chars: shrink to fit
        head -= 1
        out = _built(head)
    return out


def scoped_model_rows(
    models: list[Any],
    *,
    apis: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Build ``(id, label, description)`` multiselect rows for ``models``.

    The stable ``id`` is the CANONICAL ``provider/id`` key — the value stored in
    ``enabled_models`` and the identity every consumer uses. Keying on the bare
    model id instead would collapse models that share an id across providers (181
    such ids in the catalog; ``gpt-4o`` spans 4 providers): they'd render one
    shared checkbox, toggle together, and persist provider-agnostically so
    enabling one provider's model silently enabled every other's. The label stays
    the human ``[provider] id`` form (we do NOT reuse ``model_picker_labels``: its
    numeric ``N.`` prefix is meaningless in a checkbox list). Description = provider.

    #153: a model that CANNOT RUN gets a SHORT verdict appended to the LABEL, in
    the ``/login`` picker's vocabulary (``(needs setup)`` for something the user
    can fix, ``(unusable)`` for a dead end). The verdict has to ride the label
    because :meth:`AelixTUIContext.multiselect` never renders the third tuple
    element — it destructures it as ``_desc`` and drops it — so a
    description-only annotation would be invisible. Wiring a per-row detail
    panel through that shared widget (``/statusline`` uses it too) is out of
    scope for #153; the env var NAMES are instead printed once, in the grouped
    ``blocked_summary`` line the caller commits under the list, where a Rich
    renderable WRAPS instead of being clipped.

    The whole row is fitted to :data:`_LABEL_BUDGET` columns (middle ellipsis)
    because the widget clips rather than wraps: the first cut of this annotation
    carried the env var names inline and pushed 70 of 139 real rows past the
    frame, so the reason — the headline deliverable — was cut mid-word at a
    normal 80-column terminal. The ``[provider] id`` prefix is kept intact
    whenever it fits so the picker's case-insensitive substring filter still
    finds a row by provider or by model id; when it does not fit, the filter
    still matches the row's untruncated canonical ``provider/id`` key.

    ``apis`` is the registered-adapter set (defaults to the live one). When it is
    EMPTY — headless, or providers not wired yet — nothing is annotated and
    nothing is hidden, matching
    :func:`~aelix_coding_agent.core.runnable_models.partition_runnable`'s
    fail-open rule: unknown is not broken. (The width fit still applies there:
    it is a rendering concern, not a runnability one, and the widget clipped
    those rows before this change too.)
    """

    from ..core.runnable_models import (
        blocked_message,
        blocked_reason,
        is_recoverable_block,
        supported_apis,
    )

    apis = supported_apis() if apis is None else apis
    rows: list[tuple[str, str, str]] = []
    for model in models:
        model_id = getattr(model, "id", None) or "?"
        provider = getattr(model, "provider", None) or "?"
        key = f"{provider}/{model_id}"
        label = f"[{provider}] {model_id}"
        description = f"provider: {provider}"
        reason = blocked_reason(model, apis) if apis else ""
        if reason:
            marker = _NEEDS_SETUP if is_recoverable_block(reason) else _UNUSABLE
            label = _fit(label, _LABEL_BUDGET - _display_width(marker)) + marker
            detail = blocked_message(model, apis)
            if detail:
                description = f"provider: {provider} — {detail}"
        else:
            label = _fit(label)
        rows.append((key, label, description))
    return rows


async def run_scoped_models(
    *,
    registry: Any,
    settings_manager: Any,
    multiselect: Callable[..., Awaitable[Any]],
    commit: Callable[[object], None],
) -> None:
    """Drive the ``/scoped-models`` picker end-to-end (ImplConsumers, ADR-0161).

    ``registry`` must expose ``get_available() -> list[Model]``;
    ``settings_manager`` must expose ``get_enabled_models`` / ``set_enabled_models``
    / ``flush``. ``multiselect`` is :meth:`AelixTUIContext.multiselect`.
    """

    from rich.text import Text  # local import keeps this module import-light

    if registry is None:
        commit(Text("Scoped models unavailable (no model registry).", style="yellow"))
        return
    if settings_manager is None:
        commit(Text("Scoped models unavailable (no settings manager).", style="yellow"))
        return
    try:
        models = list(registry.get_available())
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ model list failed: {exc}", style="bold red"))
        return
    if not models:
        commit(
            Text(
                "No models available — set a provider API key "
                "(e.g. OPENROUTER_API_KEY / ANTHROPIC_API_KEY) then retry /scoped-models.",
                style="yellow",
            )
        )
        return

    from ..core.runnable_models import (
        blocked_reason,
        blocked_summary,
        is_recoverable_block,
        supported_apis,
    )

    apis = supported_apis()
    all_rows = scoped_model_rows(models, apis=apis)
    # ``all_ids`` is the WHOLE auth-filtered catalog, NOT the visible subset —
    # it is the DENOMINATOR of the "N of M models enabled" preview, so the count
    # a user reads is a count of the catalog, not of what today's env happens to
    # let the picker draw. (The "everything ticked → persist None"
    # canonicalisation does NOT test against it; see ``collapse_target`` below.)
    all_ids = {oid for oid, _, _ in all_rows}  # canonical ``provider/id`` keys
    try:
        enabled = settings_manager.get_enabled_models()
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ could not read enabled models: {exc}", style="bold red"))
        return
    # ``get_enabled_models() is None`` is the "all enabled" sentinel; a concrete
    # list is the persisted allow-list. Only the EXPLICIT list produces a seed
    # here, because only an explicit list is something the user actually
    # persisted and can therefore lose. Under the sentinel the seed is decided
    # AFTER the visibility pass, from the offered rows alone — see below.
    explicit = enabled is not None
    preselected: set[str] = set()
    if explicit:
        # A persisted entry is either the CANONICAL ``provider/id`` (new form) or a
        # LEGACY bare ``id`` (written before provider-qualification). Pre-check a row
        # when the allow-list contains its canonical key OR — only for SLASH-FREE
        # legacy entries — its bare id (which pre-checks every provider row exposing
        # that id). A slashed entry is treated as canonical only: matching it as a
        # bare id would leak into a different provider whose model id itself contains
        # a slash (openrouter's "openai/gpt-4o"). Building only from live CATALOG
        # keys drops stale entries automatically (no phantom checkbox) — note this
        # is ``all_rows``, not the visible subset, so an entry that is merely
        # unrunnable today is still seeded and therefore still saved (#153).
        enabled_lower = {e.strip().lower() for e in enabled or [] if e and e.strip()}
        enabled_bare = {e for e in enabled_lower if "/" not in e}
        for oid, _, _ in all_rows:
            _, _, bare_id = oid.partition("/")
            if oid.lower() in enabled_lower or bare_id.lower() in enabled_bare:
                preselected.add(oid)

    # #153 — visibility, decided PER REASON CLASS rather than by one blanket rule.
    #
    # * RECOVERABLE (config-missing / vertex-config-missing / no-host): shown,
    #   annotated, tickable. ``/scoped-models`` declares an intent for LATER, so a
    #   model one env var away is exactly the thing a user comes here to enable;
    #   this follows the ``/login`` precedent (#151), not ``/model``'s (which
    #   switches the CURRENT model, where "cannot run" means "cannot pick").
    # * DEAD END (no-adapter / unresolved-api): hidden, and counted in a dim line
    #   naming the reason. No env var, no key, no configuration makes these run in
    #   this build, so a checkbox for one can only ever record a wish that costs
    #   the user a narrowed allow-list.
    #
    # …EXCEPT a dead end the user has ALREADY enabled, which stays visible and
    # pre-ticked. Hiding it would leave an entry in the persisted allow-list with
    # no way to remove it from the very picker that owns that list. The carve-out
    # is deliberately keyed on an EXPLICIT allow-list: under the ``None``
    # sentinel nobody ticked anything, so the first-run screenful of unrunnable
    # rows is still hidden — which is the state issue #153 measured.
    options: list[tuple[str, str, str]] = []
    hidden_models: list[Any] = []
    shown_blocked: list[Any] = []
    for model, row in zip(models, all_rows, strict=True):
        reason = blocked_reason(model, apis) if apis else ""
        dead_end = bool(reason) and not is_recoverable_block(reason)
        if dead_end and not (explicit and row[0] in preselected):
            hidden_models.append(model)
            continue
        options.append(row)
        if reason:
            shown_blocked.append(model)

    if not options:
        commit(
            Text(
                f"No selectable models — none of the {len(hidden_models)} "
                f"available model(s) can run in this build "
                f"({blocked_summary(hidden_models, apis)}). Nothing was changed.",
                style="yellow",
            )
        )
        return
    if hidden_models:
        commit(
            Text(
                f"({len(hidden_models)} model(s) hidden — "
                f"{blocked_summary(hidden_models, apis)})",
                style="dim",
            )
        )
    # A blocked row that IS shown carries a SHORT verdict in its own label
    # (``(needs setup)`` / ``(unusable)``); the env var names live HERE, once,
    # grouped by reason. They cannot ride the rows: the picker clips a row at
    # the frame width instead of wrapping it, and inline names put 70 of 139
    # real rows past 78 columns — clipped mid-variable-name at an 80-column
    # terminal. This line is a committed Rich renderable, so it wraps.
    if shown_blocked:
        commit(
            Text(
                f"({len(shown_blocked)} model(s) shown cannot run right now — "
                f"{blocked_summary(shown_blocked, apis)}; "
                "those rows are marked in the list)",
                style="dim",
            )
        )

    # THE SEED, and the ceiling the "all models" collapse is measured against.
    #
    # Under an EXPLICIT allow-list the seed is what the user persisted, and every
    # seeded row is offered (a seeded dead end is carved back in above), so the
    # user can un-tick anything they previously chose.
    #
    # Under the ``None`` sentinel there is NO persisted list, so there is nothing
    # to protect: the seed is the OFFERED rows only. Seeding it from the whole
    # catalog — what the first cut of #153 did — undid the hiding in one
    # keystroke, because :meth:`AelixTUIContext.multiselect` starts its internal
    # set as ``set(selected)`` and only ever toggles a RENDERED row, so a hidden
    # row came back in ``chosen`` and was written to disk. Measured live from an
    # absent settings.json, ONE Space + Enter persisted 138 ids, 70 of them
    # models this build cannot run — and re-opening then found them in an
    # explicit list, so the carve-out made them visible and pre-ticked again.
    offered = {oid for oid, _, _ in options}
    selected = preselected if explicit else set(offered)
    # Seeded but NOT offered — see the carry-forward at the save. Empty under
    # today's visibility rules (both branches above seed only offered rows); it
    # is computed rather than assumed so a future rule that hides a seeded row
    # cannot silently start dropping it.
    carried = {oid for oid in selected if oid not in offered}
    # "Everything ticked → persist None" must be REACHABLE, and it is only
    # reachable through ids the picker can actually toggle. Testing ``chosen``
    # against the whole catalog made the sentinel unreachable the moment one
    # dead end was hidden: the user ticks every row the picker has and still
    # falls short by the hidden ones, so the open-ended "all models" was pinned
    # to a concrete id list that a later build (or a later env var) cannot grow,
    # with no enable-all affordance in ``multiselect`` to escape it.
    collapse_target = offered | carried

    hidden_note = (
        f"{len(hidden_models)} unrunnable model(s) not shown, and not in this "
        "selection."
        if hidden_models
        else ""
    )

    def _preview(chosen: set[str], _toggles: dict[str, bool]) -> list[str]:
        # The collapse branch persists ``None`` — no allow-list at all — so the
        # hidden rows are covered by it and the note would contradict the line
        # above it.
        if chosen >= collapse_target:
            return ["All models enabled (no scoping)."]
        lines = [f"{len(chosen)} of {len(all_ids)} models enabled."]
        if hidden_note:
            lines.append(hidden_note)
        return lines

    try:
        result = await multiselect(
            "Scoped models — choose which models are enabled",
            options,
            selected=selected,
            preview=_preview,
        )
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ scoped-models picker failed: {exc}", style="bold red"))
        return
    if result is None:
        return  # Esc / cancelled — no write

    chosen, _toggles = result
    # #153 — CARRY FORWARD every seeded id the picker never offered.
    #
    # The saved allow-list is rebuilt from "what the picker displayed and the
    # user ticked", so anything filtered out of the display would be silently
    # written OUT of the allow-list. A user who scoped some cloudflare models and
    # later opened this picker with CLOUDFLARE_ACCOUNT_ID unset would have that
    # scoping erased by a save they thought changed nothing — and setting the
    # variable afterwards would not bring it back. That is a worse bug than the
    # one #153 fixes, so the rule is: an id that was never SHOWN cannot have been
    # UN-ticked, therefore it keeps whatever state it was seeded with.
    #
    # This mirrors pi's ``scoped-models-selector.ts``, whose ``enabledIds`` is a
    # delta mutated by ``toggle``/``enableAll``/``clearAll`` — never a snapshot
    # rebuilt from the visible rows — so ids it filters out of the list survive a
    # Ctrl+S. It is asserted HERE rather than left to ``multiselect``'s internals
    # because ``multiselect`` is dependency-injected: a caller's implementation is
    # free to return only the ids it rendered.
    #
    # ``carried`` is empty under today's visibility rules and that is the point:
    # the protection is the pairing of "seed only what you offer" with "offer
    # every seeded row", not this union. The union stays because the rule must
    # survive a future visibility change, and because it costs one set operation.
    chosen = set(chosen) | carried
    # Canonical "all" → None: the allow-list is not pinned to today's catalog.
    # Measured against what the picker COULD offer (plus what it carried), not
    # against the whole catalog — see ``collapse_target``.
    patterns = None if chosen >= collapse_target else sorted(chosen)
    try:
        settings_manager.set_enabled_models(patterns)
        await settings_manager.flush()
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ scoped-models save failed: {exc}", style="bold red"))
        return

    # Read-back round-trip confirmation (the setter mutates the merged view
    # synchronously, so this reflects the new state reliably).
    #
    # ENFORCED (ADR-0162): the allow-list now RESTRICTS the /model picker (and
    # --list-models) via scoped_models_filter.scoped_available — read live, so
    # it takes effect immediately on the next /model open. The message states the
    # active effect. (RPC handlers remain unscoped this turn — see the module
    # docstring's PARTIAL SCOPE note.)
    with contextlib.suppress(Exception):
        readback = settings_manager.get_enabled_models()
        if readback is None:
            commit(
                Text(
                    "scoped models → all models enabled "
                    "(persisted, global scope)",
                    style="green",
                )
            )
        else:
            commit(
                Text(
                    f"scoped models → {len(readback)} model(s) enabled "
                    "(persisted, global scope; /model now restricted to these)",
                    style="green",
                )
            )


__all__ = ["run_scoped_models", "scoped_model_rows"]
