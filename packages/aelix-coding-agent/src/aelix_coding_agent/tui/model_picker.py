"""Pure helpers for the ``/model`` rich picker (Sprint 6h₂₆, ADR-0154, WP-7).

The interactive flow lives in :func:`aelix_coding_agent.tui.shell._open_model_picker`
(it drives :meth:`AelixTUIContext.select` with the ``detail`` panel). These helpers
are deliberately side-effect-free + dependency-light so the label/detail FORMATTING
is unit-testable without standing up the prompt-toolkit modal.

Pi parity: ``interactive-mode.ts`` model selector (numbered, provider-tagged rows
+ a per-highlight detail footer). Data source is
:meth:`aelix_coding_agent.model_registry.ModelRegistry.get_available`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aelix_ai.streaming import Model

# Width of the divider that separates the option list from the detail footer.
_DETAIL_DIVIDER = "─" * 52


def model_picker_labels(
    models: list[Model],
    current_id: str | None = None,
    current_provider: str | None = None,
) -> list[str]:
    """Build the numbered, provider-tagged option labels for the picker.

    ``"N. [provider] {id}"``; the currently-active model is marked with a
    leading ``✱ ``. The labels are unique (the ``N.`` prefix guarantees it), so
    the caller can recover the chosen :class:`Model` by exact-label index — the
    same lossless round-trip ``_open_settings`` uses (no ``startswith`` scan).
    """

    labels: list[str] = []
    for i, model in enumerate(models, start=1):
        raw_id = getattr(model, "id", None)
        raw_provider = getattr(model, "provider", None)
        # Compare the RAW (id, provider) BEFORE the "?" display fallback, and
        # only when an actual current model is set (current_id is not None) —
        # otherwise two providerless models could both normalize to "?" and get
        # falsely marked (W-review 6h₂₆ MEDIUM).
        is_current = (
            current_id is not None
            and raw_id == current_id
            and raw_provider == current_provider
        )
        provider = raw_provider or "?"
        model_id = raw_id or "?"
        marker = "✱ " if is_current else ""
        labels.append(f"{marker}{i}. [{provider}] {model_id}")
    return labels


def model_detail_lines(model: Model) -> list[str]:
    """Detail-footer lines for ``model`` (modality / context window / base url / api key).

    Mirrors the user's ``/model`` mockup. Every field degrades gracefully so a
    sparse/partial :class:`Model` never breaks rendering.
    """

    inputs = list(getattr(model, "input", []) or [])
    if inputs == ["text"]:
        modality = "text-only"
    elif inputs:
        modality = ", ".join(inputs)
    else:
        modality = "unknown"

    context_window = getattr(model, "context_window", 0) or 0
    context_label = f"{context_window:,} tokens" if context_window else "unknown"

    base_url = getattr(model, "base_url", "") or "(provider default)"
    provider = getattr(model, "provider", "") or "?"

    return [
        _DETAIL_DIVIDER,
        f"Modality:       {modality}",
        f"Context Window: {context_label}",
        f"Base URL:       {base_url}",
        f"API Key:        {_api_key_env(provider)}",
    ]


def _api_key_env(provider: str) -> str:
    """The canonical API-key env-var name for ``provider`` (``—`` if unknown).

    Reads the shared provider→env map so the footer shows the real var the user
    must set (e.g. ``openrouter`` → ``OPENROUTER_API_KEY``) rather than a guess.
    """

    try:
        from aelix_ai.providers._env_api_keys import ENV_API_KEYS

        envs = ENV_API_KEYS.get(provider, [])
        if envs:
            return envs[0]
    except Exception:  # noqa: BLE001 — detail footer must never raise
        pass
    return "—"


async def run_model_picker(
    *,
    registry: Any,
    harness: Any,
    select: Callable[..., Awaitable[str | None]],
    commit: Callable[[object], None],
    refresh_footer: Callable[[], None] | None = None,
    settings_manager: Any = None,
) -> None:
    """Drive the ``/model`` picker end-to-end (Sprint 6h₂₆, ADR-0154, WP-7).

    Module-level + dependency-injected (duck-typed ``registry``/``harness`` +
    ``select``/``commit``/``refresh_footer`` callables) so the WHOLE flow is
    unit-testable without standing up the prompt-toolkit app. ``shell.py`` wires
    the live ``AelixTUIContext.select`` / output-committer into it.

    ``registry`` must expose ``get_available() -> list[Model]``; ``harness`` must
    expose ``current_model`` + ``set_model``. Every failure mode (no registry,
    list failure, empty catalog, missing ``set_model``, switch raising, unknown
    chosen row) surfaces a committed message and returns — never crashes the REPL.

    ENFORCEMENT (ADR-0162): when ``settings_manager`` is supplied, the offered
    list is narrowed to the persisted ``enabled_models`` allow-list via
    :func:`aelix_coding_agent.core.scoped_models_filter.scoped_available` (read
    LIVE — a runtime ``/scoped-models`` change is reflected on the next open).
    An empty-match allow-list degrades to the full list with a warning (never a
    lockout). When ``settings_manager`` is :data:`None` the full auth-filtered
    list is shown (unchanged behaviour).
    """

    from rich.text import Text  # local import keeps this module import-light

    if registry is None:
        commit(Text("Model picker unavailable (no model registry).", style="yellow"))
        return
    try:
        from aelix_coding_agent.core.scoped_models_filter import scoped_available

        models = list(
            await scoped_available(
                registry,
                settings_manager,
                warn=lambda m: commit(Text(m, style="yellow")),
            )
        )
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ model list failed: {exc}", style="bold red"))
        return
    # WP-8 follow-up — hide models whose ``api`` has no registered adapter (e.g.
    # ``openai-responses``, used by OpenAI / GitHub-Copilot gpt-5.x) so the user
    # can't pick a model that fails at the first turn with the cryptic
    # ``No provider registered for api=...``. Empty registered set (headless /
    # providers not wired) → no filtering (partition_runnable returns all).
    from aelix_coding_agent.core.runnable_models import partition_runnable

    models, blocked = partition_runnable(models)
    if blocked:
        commit(
            Text(
                f"({len(blocked)} model(s) hidden — their API has no adapter in "
                "this build, e.g. openai-responses / Copilot gpt-5.x)",
                style="dim",
            )
        )
    if not models:
        if blocked:
            commit(
                Text(
                    "No runnable models — every available model uses an API this "
                    "build has no adapter for (e.g. openai-responses).",
                    style="yellow",
                )
            )
        else:
            commit(
                Text(
                    "No models available — set a provider API key "
                    "(e.g. OPENROUTER_API_KEY / ANTHROPIC_API_KEY) then retry /model.",
                    style="yellow",
                )
            )
        return

    current = getattr(harness, "current_model", None)
    labels = model_picker_labels(
        models, getattr(current, "id", None), getattr(current, "provider", None)
    )
    # ``detail`` gets the ORIGINAL option index; labels and models share order.
    choice = await select(
        "Select Model", labels, detail=lambda i: model_detail_lines(models[i])
    )
    if not choice:
        return
    # Recover the chosen Model by exact-label index (lossless — labels carry a
    # unique "N." prefix). ValueError surfaces rather than silently no-op'ing
    # (W-review 6h₂₄ MEDIUM-1 pattern, same as _open_settings).
    try:
        row_idx = labels.index(choice)
    except ValueError:
        commit(Text(f"✖ model: unknown row {choice!r}", style="bold red"))
        return
    chosen = models[row_idx]
    # Defensive guard (the list is already filtered): never switch to a model
    # whose api has no adapter — surface the actionable reason, not the cryptic
    # provider error the agent loop would raise on the first turn.
    from aelix_coding_agent.core.runnable_models import is_runnable, unsupported_message

    if not is_runnable(chosen):
        commit(Text(unsupported_message(chosen), style="bold red"))
        return
    if not hasattr(harness, "set_model"):
        commit(Text("Model switching is unavailable.", style="yellow"))
        return
    try:
        await harness.set_model(chosen)
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ model switch failed: {exc}", style="bold red"))
        return
    # Persist as the default (pi parity: setModel → setDefaultModelAndProvider) so
    # the pick SURVIVES restart / /new — the same behaviour as /settings → Default
    # model. Only a real (provider, id) is pinned; the ESC path returned above, so
    # this runs only on an actual switch. Guarded so a settings failure never
    # aborts the (already-applied) live switch.
    chosen_provider = getattr(chosen, "provider", "")
    chosen_id = getattr(chosen, "id", "")
    if settings_manager is not None and chosen_provider and chosen_id:
        with contextlib.suppress(Exception):
            settings_manager.set_default_model_and_provider(chosen_provider, chosen_id)
            await settings_manager.flush()
    commit(Text(f"model → {getattr(chosen, 'id', '?')}", style="green"))
    # The footer ✱ segment is a cached string — refresh so it reflects the new
    # model immediately (not only on the next unrelated repaint).
    if refresh_footer is not None:
        with contextlib.suppress(Exception):
            refresh_footer()


__all__ = ["model_picker_labels", "model_detail_lines", "run_model_picker"]
