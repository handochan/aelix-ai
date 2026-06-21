"""Pi parity: ``packages/coding-agent/src/cli/list-models.ts`` (111 LOC TS).

Sprint 6h₇a (Phase 5a-iii-α, ADR-0090, P-415). Lists available models
(filtered by :func:`aelix_coding_agent.model_registry.ModelRegistry.get_available`),
optionally narrowed by a fuzzy search pattern, and prints a 6-column
table (provider / model / context / max-out / thinking / images).

Aelix-additive divergences from Pi (BINDING — see ADR-0090):

1. **Plain stderr text** for the load-error warning — Pi uses
   ``chalk.yellow(...)``; Aelix is ANSI-free in 6h₇a (ANSI handling
   lands with Phase 5b TUI).
2. **Inline no-models-available message** — Pi delegates to
   ``formatNoModelsAvailableMessage()`` in ``core/auth-guidance.ts``.
   That helper has not been ported; the inline string keeps 6h₇a
   self-contained without anticipating the auth-guidance shape.

Pi citation at SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``:
``packages/coding-agent/src/cli/list-models.ts:1-111``; dispatched
from ``main.ts:624-627``.
"""

from __future__ import annotations

import sys

from aelix_ai.streaming import Model

from ..model_registry import ModelRegistry
from ..util.fuzzy import fuzzy_filter


def format_token_count(count: int) -> str:
    """Pi parity: ``formatTokenCount`` (``list-models.ts:13-25``).

    Renders a non-negative integer as a human-readable token count:

    - ``count >= 1_000_000`` → ``"<N>M"`` (e.g., 1_500_000 → ``"1.5M"``,
      2_000_000 → ``"2M"`` — trailing ``.0`` is stripped to match Pi
      ``millions % 1 === 0`` branch).
    - ``count >= 1_000`` → ``"<N>K"`` (e.g., 200_000 → ``"200K"``,
      1500 → ``"1.5K"``).
    - Otherwise → :class:`str` of the raw count.
    """

    if count >= 1_000_000:
        millions = count / 1_000_000
        if millions % 1 == 0:
            return f"{int(millions)}M"
        return f"{millions:.1f}M"
    if count >= 1_000:
        thousands = count / 1_000
        if thousands % 1 == 0:
            return f"{int(thousands)}K"
        return f"{thousands:.1f}K"
    return str(count)


def _model_get_text(model: Model) -> str:
    r"""Pi parity: ``(m) => \`${m.provider} ${m.id}\``` (``list-models.ts:46``).

    The fuzzy key combines provider + id so a pattern like
    ``anthropic claude`` filters on both halves via the token-AND
    semantics of :func:`fuzzy_filter`.
    """

    return f"{model.provider} {model.id}"


async def list_models(
    model_registry: ModelRegistry,
    search_pattern: str | bool | None = None,
    settings_manager: object | None = None,
) -> None:
    """Pi parity: ``listModels`` (``list-models.ts:30-111``).

    Body order:

    1. Surface :meth:`ModelRegistry.get_error` as a plain stderr
       warning (Aelix-additive divergence — no ANSI).
    2. Fetch available models via :meth:`ModelRegistry.get_available`
       (Pi parity for ``getAvailable()``) — narrowed by the persisted
       ``enabled_models`` allow-list when ``settings_manager`` is supplied
       (ADR-0162 enforcement; an empty-match list degrades to all + a stderr
       warning so ``--list-models`` is never empty due to a stale allow-list).
    3. If empty: print the inline no-models-available fallback and
       return.
    4. If ``search_pattern`` is a non-empty string: fuzzy-filter the
       set on the ``"<provider> <id>"`` key.
    5. If the filtered set is empty: print
       ``f"No models matching {search_pattern!r}"`` and return.
    6. Sort by (provider, id) ascending — matches Pi
       ``localeCompare`` (case-insensitive — we lowercase per Pi
       string semantics).
    7. Print 6-column header + rows (provider / model / context /
       max-out / thinking / images).

    The ``search_pattern`` parameter accepts the same tri-state as
    :attr:`aelix_coding_agent.cli.args.Args.list_models`: :data:`None`
    or :data:`True` ≡ no pattern; :class:`str` ≡ pattern.
    """

    load_error = model_registry.get_error()
    if load_error:
        # Aelix-additive divergence: plain stderr text (no chalk/ANSI).
        print(
            f"Warning: errors loading models.json:\n{load_error}",
            file=sys.stderr,
        )

    if settings_manager is not None:
        # ADR-0162: scope to the persisted enabled_models allow-list (parity
        # with the /model picker). The helper degrades an empty-match list to
        # the full set + warns, so --list-models is never empty due to a stale
        # allow-list. Read LIVE — no startup snapshot.
        from ..core.scoped_models_filter import scoped_available

        models = await scoped_available(
            model_registry,
            settings_manager,
            warn=lambda m: print(f"Warning: {m}", file=sys.stderr),
        )
    else:
        models = model_registry.get_available()

    if not models:
        # Aelix-additive divergence: inline fallback (Pi delegates to
        # ``formatNoModelsAvailableMessage`` in auth-guidance.ts which
        # is not yet ported).
        print(
            "No models available. Run 'aelix auth' to configure a provider."
        )
        return

    # Coerce the tri-state ``--list-models`` argument: only honour
    # :class:`str` patterns. ``True`` and :data:`None` skip filtering.
    pattern: str | None = search_pattern if isinstance(search_pattern, str) else None

    filtered: list[Model] = models
    if pattern:
        filtered = fuzzy_filter(models, pattern, _model_get_text)

    if not filtered:
        print(f'No models matching "{pattern}"')
        return

    # Pi parity: stable sort by (provider, id) lowercase. We re-sort
    # AFTER fuzzy filter so the printed table is always alphabetical
    # regardless of fuzzy-score ordering — matches Pi behavior at
    # ``list-models.ts:54-58``.
    filtered = sorted(
        filtered,
        key=lambda m: (m.provider.lower(), m.id.lower()),
    )

    rows: list[dict[str, str]] = [
        {
            "provider": m.provider,
            "model": m.id,
            "context": format_token_count(m.context_window),
            "max_out": format_token_count(m.max_tokens),
            "thinking": "yes" if m.reasoning else "no",
            "images": "yes" if "image" in m.input else "no",
        }
        for m in filtered
    ]

    headers = {
        "provider": "provider",
        "model": "model",
        "context": "context",
        "max_out": "max-out",
        "thinking": "thinking",
        "images": "images",
    }

    widths = {
        key: max(len(headers[key]), *(len(row[key]) for row in rows))
        for key in headers
    }

    column_order = ("provider", "model", "context", "max_out", "thinking", "images")

    def _render(row: dict[str, str]) -> str:
        return "  ".join(row[col].ljust(widths[col]) for col in column_order)

    print(_render(headers))
    for row in rows:
        print(_render(row))


__all__ = [
    "format_token_count",
    "list_models",
]
