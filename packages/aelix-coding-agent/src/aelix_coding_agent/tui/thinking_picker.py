"""Pure helpers + DI flow for the ``/thinking`` rich picker (Sprint 6h₂₇, ADR-0155, WP-7).

The interactive flow lives in :func:`aelix_coding_agent.tui.shell._open_thinking_picker`
(it drives :meth:`AelixTUIContext.select`). These helpers are deliberately
side-effect-free + dependency-light so the label FORMATTING and the whole
end-to-end flow are unit-testable without standing up the prompt-toolkit modal —
exactly like :mod:`aelix_coding_agent.tui.model_picker`.

Pi parity: the reasoning-level selector. Data source is
:func:`aelix_ai.models.get_supported_thinking_levels` (the model's supported
levels — ``["off"]`` for a non-reasoning model); the setter is
:meth:`AgentHarness.set_thinking_level`. The :meth:`AgentHarness.cycle_thinking_level`
method only advances ONE step so it can't power a picker — this flow calls
``get_supported_thinking_levels`` + ``set_thinking_level`` directly (both public,
already exercised by core).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def thinking_picker_labels(levels: list[str], current: str) -> list[str]:
    """Build the numbered option labels for the picker.

    ``"N. {level}"``; the currently-active level is marked with a leading
    ``✱ ``. The labels are unique (the ``N.`` prefix guarantees it), so the
    caller can recover the chosen level by exact-label index — the same lossless
    round-trip ``model_picker_labels`` / ``_open_settings`` use (never a
    ``startswith`` scan).
    """

    labels: list[str] = []
    for i, level in enumerate(levels, start=1):
        marker = "✱ " if level == current else ""
        labels.append(f"{marker}{i}. {level}")
    return labels


async def run_thinking_picker(
    *,
    harness: Any,
    select: Callable[..., Awaitable[str | None]],
    commit: Callable[[object], None],
) -> None:
    """Drive the ``/thinking`` picker end-to-end (Sprint 6h₂₇, ADR-0155, WP-7).

    Module-level + dependency-injected (duck-typed ``harness`` + ``select`` /
    ``commit`` callables) so the WHOLE flow is unit-testable without standing up
    the prompt-toolkit app. ``shell.py`` wires the live
    :meth:`AelixTUIContext.select` / output-committer into it.

    ``harness`` must expose ``current_model`` (a :class:`Model` carrying
    ``reasoning: bool``) + an async ``set_thinking_level``. Every failure mode
    (no current model, ``get_supported_thinking_levels`` import/raise, a
    non-reasoning model, an empty / off-only level set, missing setter, the
    switch raising, an unknown chosen row) surfaces a committed message and
    returns — never crashes the REPL.
    """

    from rich.text import Text  # local import keeps this module import-light

    try:
        from aelix_ai.models import get_supported_thinking_levels
    except Exception:  # noqa: BLE001 — degrade rather than crash on a missing dep
        get_supported_thinking_levels = None  # type: ignore[assignment]

    model = getattr(harness, "current_model", None)
    setter = getattr(harness, "set_thinking_level", None)
    if model is None or get_supported_thinking_levels is None or not callable(setter):
        commit(Text("Thinking level is unavailable.", style="yellow"))
        return
    if not getattr(model, "reasoning", False):
        commit(Text("This model has no thinking levels to choose.", style="yellow"))
        return
    try:
        levels = list(get_supported_thinking_levels(model))
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ thinking levels failed: {exc}", style="bold red"))
        return
    if not levels or levels == ["off"]:
        commit(Text("This model has no thinking levels to choose.", style="yellow"))
        return

    state = getattr(harness, "_state", None)
    current = (getattr(state, "thinking_level", None) or "off") if state else "off"
    labels = thinking_picker_labels(levels, current)
    choice = await select("Select Thinking Level", labels)
    if not choice:
        return
    # Recover the chosen level by exact-label index (lossless — labels carry a
    # unique "N." prefix). ValueError surfaces rather than silently no-op'ing.
    try:
        idx = labels.index(choice)
    except ValueError:
        commit(Text(f"✖ thinking: unknown row {choice!r}", style="bold red"))
        return
    chosen = levels[idx]
    try:
        await setter(chosen)
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ thinking switch failed: {exc}", style="bold red"))
        return
    commit(Text(f"thinking → {chosen}", style="green"))


__all__ = ["thinking_picker_labels", "run_thinking_picker"]
