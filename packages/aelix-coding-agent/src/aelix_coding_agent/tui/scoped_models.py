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


def scoped_model_rows(
    models: list[Any],
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
    """

    rows: list[tuple[str, str, str]] = []
    for model in models:
        model_id = getattr(model, "id", None) or "?"
        provider = getattr(model, "provider", None) or "?"
        key = f"{provider}/{model_id}"
        rows.append((key, f"[{provider}] {model_id}", f"provider: {provider}"))
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

    options = scoped_model_rows(models)
    all_ids = {oid for oid, _, _ in options}  # canonical ``provider/id`` keys
    # ``get_enabled_models() is None`` is the "all enabled" sentinel → start with
    # everything checked. A concrete list is the persisted allow-list; intersect
    # with the live catalog so a stale id (model no longer available) doesn't ghost
    # a phantom checkbox.
    try:
        enabled = settings_manager.get_enabled_models()
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ could not read enabled models: {exc}", style="bold red"))
        return
    if enabled is None:
        selected = set(all_ids)
    else:
        # A persisted entry is either the CANONICAL ``provider/id`` (new form) or a
        # LEGACY bare ``id`` (written before provider-qualification). Pre-check a row
        # when the allow-list contains its canonical key OR — only for SLASH-FREE
        # legacy entries — its bare id (which pre-checks every provider row exposing
        # that id). A slashed entry is treated as canonical only: matching it as a
        # bare id would leak into a different provider whose model id itself contains
        # a slash (openrouter's "openai/gpt-4o"). Building only from live option keys
        # drops stale entries automatically (no phantom checkbox).
        enabled_lower = {e.strip().lower() for e in enabled if e and e.strip()}
        enabled_bare = {e for e in enabled_lower if "/" not in e}
        selected = set()
        for oid, _, _ in options:
            _, _, bare_id = oid.partition("/")
            if oid.lower() in enabled_lower or bare_id.lower() in enabled_bare:
                selected.add(oid)

    def _preview(chosen: set[str], _toggles: dict[str, bool]) -> list[str]:
        if chosen >= all_ids:
            return ["All models enabled (no scoping)."]
        return [f"{len(chosen)} of {len(all_ids)} models enabled."]

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
    # Canonical "all" → None (don't pin the allow-list to today's catalog).
    patterns = None if chosen >= all_ids else sorted(chosen)
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
