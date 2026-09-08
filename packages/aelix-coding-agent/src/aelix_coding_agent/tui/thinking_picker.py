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

import contextlib
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def thinking_level_display(model: Any, level: str) -> str:
    """Render ``level`` as the tier ``model`` actually receives for it (#251).

    ``"high"`` when the model calls the level by the same name, ``"xhigh (max)"``
    when it does not. The contract is that the parenthesis names the tier the
    REQUEST carries, so the clamp is resolved BEFORE the catalog's rename: a
    level the model does not support is served by another one
    (:func:`aelix_ai.models.clamp_thinking_level`, which the adapters call too),
    and only then does ``thinking_level_map`` say what that tier is called on the
    wire. Measured over the vendored catalog, the rule adds a suffix to 2909
    (model, level) pairs — 51 renames, 1013 clamps, 1845 non-reasoning rows.

    One adapter breaks that contract, and the break is the adapter's rather than
    this rule's: both Google adapters revive a clamped ``off`` back into ``high``
    (``google_generative_ai.py:307-308``, ``google_vertex.py:413-414``), so
    ``(off)`` understates the 13 non-reasoning Gemini/Gemma/Vertex-Llama catalog
    rows — those requests do carry thinking. The other adapter families honour it
    (``_anthropic_transforms.py`` returns ``{}`` on a non-reasoning model and
    ``thinking: disabled`` otherwise, both OpenAI Responses adapters map a clamped
    ``off`` to ``None``, ``openai_completions`` gates every reasoning parameter on
    ``is_reasoning``). Tracked as #256, not papered over here.

    Four cases stay bare on purpose:

    - ``off`` — ``harness/core.py`` folds ``off``/unset to ``reasoning=None``
      before an adapter sees it, so ``off`` never reaches one as a tier and there
      is no true value to show. (The cost, measured: on the 100 catalog models
      that do not support ``off`` the request then carries no reasoning field at
      all and the model thinks at its own default.)
    - a case-only rename — Google spells its enum ``HIGH``/``LOW``/``MINIMAL``;
      that is the same tier, so a suffix would be noise on 16 rows.
    - a non-``str`` mapping — ``_anthropic_transforms.py`` guards on
      ``isinstance(mapped, str)`` and sends a coarse ``"high"`` where
      ``openai_completions._native_effort`` forwards the number, so there is no
      one true token. The catalog carries no int values today.
    - anything ``model`` cannot answer — no model, no map, no key, a ``None`` or
      blank value, a clamp that raises. Pure display; it degrades to the level.

    ``model`` is duck-typed (``reasoning`` + ``thinking_level_map``) so callers
    can pass a partial harness's ``current_model``, or ``None``.
    """

    if not level:
        return level
    if level == "off":
        return level
    # No ``model is None`` short-circuit: with ``model=None`` the clamp below
    # raises into the ``except`` and the ``getattr`` fall-through returns ``level``
    # anyway, so a guard would be a branch no test could distinguish (measured:
    # 0.63 µs/call on that path, and the whole suite stays green without it).
    effective = level
    try:
        # Local import: this module stays import-light (see the module docstring).
        from aelix_ai.models import clamp_thinking_level

        # ``or level`` never fires at runtime — ``clamp_thinking_level`` returns
        # None only for a None level, and ``level`` is truthy here — but it is not
        # decoration: the function is typed ``str | None``, and without it
        # ``scripts/check_types.py`` reports two reportOptionalMemberAccess on the
        # ``casefold`` calls below. Keep it, or narrow some other way.
        effective = clamp_thinking_level(model, level) or level
    except Exception:  # noqa: BLE001 — a partial model degrades to the raw level
        pass
    value = (getattr(model, "thinking_level_map", None) or {}).get(effective)
    native = value.strip() if isinstance(value, str) else ""
    shown = native if native and native.casefold() != effective.casefold() else effective
    if shown.casefold() == level.casefold():
        return level
    return f"{level} ({shown})"


def thinking_picker_labels(
    levels: list[str], current: str, model: Any | None = None
) -> list[str]:
    """Build the numbered option labels for the picker.

    ``"N. {level}"``, or ``"N. {level} ({tier})"`` when ``model`` is given and it
    receives that level under another name (#251) — see
    :func:`thinking_level_display`. ``model`` is keyword-optional so the pure
    contract without it is the pre-#251 one. The labels are unique either way
    (the ``N.`` prefix guarantees it — a catalog can map several levels onto one
    native tier), so the caller can recover the chosen level by exact-label index
    — the same lossless round-trip ``model_picker_labels`` / ``_open_settings``
    use (never a ``startswith`` scan). The currently-active level is marked with
    a leading ``✱ ``; the caller clamps what it passes as ``current``.

    Pi divergence (ADR-0235, no ADR required): Pi's selector rows carry the level
    name alone.
    """

    labels: list[str] = []
    for i, level in enumerate(levels, start=1):
        marker = "✱ " if level == current else ""
        labels.append(f"{marker}{i}. {thinking_level_display(model, level)}")
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
        from aelix_ai.models import (
            clamp_thinking_level,
            get_supported_thinking_levels,
        )
    except Exception:  # noqa: BLE001 — degrade rather than crash on a missing dep
        # Both names are BOUND, not left unbound: the guard below returns on
        # ``get_supported_thinking_levels is None`` so the ``clamp_thinking_level``
        # branch is never reached with None, but ``scripts/check_types.py`` reports
        # two reportPossiblyUnboundVariable if this line goes.
        clamp_thinking_level = None  # type: ignore[assignment]
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
    # #251 — clamp the MARKER, not the label helper: ``set_model`` leaves
    # ``_state.thinking_level`` alone, so the stored level can be one this model
    # does not offer (``xhigh`` on gpt-5.1). Unclamped, ``✱`` would land on no row
    # at all while the footer reads ``🧠 xhigh (high)``. Doing it here rather than
    # inside ``thinking_picker_labels`` keeps that helper pure and its
    # "no match marks nothing" contract intact.
    if clamp_thinking_level is not None:
        # An unclamped marker beats no picker, so a raise here is not fatal.
        with contextlib.suppress(Exception):
            # ``or current`` is unreachable for the same reason as in
            # ``thinking_level_display`` (``current`` is already ``... or "off"``),
            # and kept for the same one: it is what narrows ``str | None`` back to
            # ``str`` for ``thinking_picker_labels``' typed ``current`` parameter.
            current = clamp_thinking_level(model, current) or current
    labels = thinking_picker_labels(levels, current, model=model)
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
        # ``callable()`` narrows to "returns object"; this lookup's contract
        # is an awaitable setter, and the except below is what handles a host
        # that does not honour it.
        await cast("Awaitable[None]", setter(chosen))
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ thinking switch failed: {exc}", style="bold red"))
        return
    commit(Text(f"thinking → {thinking_level_display(model, chosen)}", style="green"))


__all__ = ["thinking_level_display", "thinking_picker_labels", "run_thinking_picker"]
