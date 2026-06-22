"""WP-2 (ADR-0160) — the ``/statusline`` interactive flow (dependency-injected).

Mirrors :func:`aelix_coding_agent.tui.model_picker.run_model_picker`: the WHOLE
flow is module-level + DI (duck-typed ``segments`` / ``load`` / ``save`` /
``multiselect`` / ``commit`` / ``refresh_footer``) so it is unit-testable without
standing up the prompt-toolkit app. ``shell.py`` wires the live
:class:`StatuslineStore` + :meth:`AelixTUIContext.multiselect` into it.

The user toggles which footer segments render (plus a "Use theme colors" flag);
on confirm the enabled-id set + flag persist atomically and the live footer
repaints. Esc → no write. Every failure commits a message and returns (never
crashes the REPL). The ADR-0159 invariants live inside the footer producers, so
unchecking a segment can only HIDE it — the permission badge's leading position +
omit-when-no-provider rule are unaffected by the enabled-set.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aelix_coding_agent.tui.footer_segments import FooterSegment
    from aelix_coding_agent.tui.statusline_store import StatuslineConfig


def _sample_footer(
    segments: list[FooterSegment], enabled: set[str]
) -> list[str]:
    """A static sample-footer preview for the tentative enabled-set.

    Uses placeholder values (the live producers read context state we don't want
    to evaluate mid-picker) so the user sees the ORDER + which segments survive.
    """

    sample: dict[str, str] = {
        "permission-mode": "● default",
        "steering": "⏵⏵ all",
        "pending-queued": "⋯ 2 queued",
        "current-dir": "📂 ~/proj",
        "model": "✱ gpt-4o",
        "context-remaining": "◔ 42% · 84K/200K",
        "git-branch": "⎇ main",
        "input-tokens": "↑ 12,345",
        "output-tokens": "↓ 6,789",
        "cost": "$ 0.0421",
    }
    parts = [
        sample.get(s.id, s.label) for s in segments if s.id in enabled
    ]
    if not parts:
        return ["Preview: (empty footer)"]
    return ["Preview:", "  " + "  ·  ".join(parts)]


async def run_statusline_picker(
    *,
    segments: list[FooterSegment],
    load: Callable[[], StatuslineConfig],
    save: Callable[[StatuslineConfig], None],
    multiselect: Callable[..., Awaitable[Any]],
    commit: Callable[[object], None],
    refresh_footer: Callable[[], None] | None = None,
) -> None:
    """Drive the ``/statusline`` picker end-to-end (WP-2, ADR-0160).

    :param segments: the footer-segment registry (id/label/description).
    :param load: read the persisted :class:`StatuslineConfig` (degrades on a
        missing/corrupt file — never raises).
    :param save: persist a :class:`StatuslineConfig` atomically.
    :param multiselect: the :meth:`AelixTUIContext.multiselect` checkbox picker.
    :param commit: commit a Rich renderable into scrollback.
    :param refresh_footer: repaint the live footer after a successful save.
    """

    from rich.text import Text

    from aelix_coding_agent.tui.statusline_store import StatuslineConfig

    if not segments:
        commit(Text("Statusline has no segments to configure.", style="yellow"))
        return
    try:
        config = load()
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ statusline load failed: {exc}", style="bold red"))
        return

    # If the store has never been written (load returned an empty enabled set is
    # impossible — load() degrades to the registry defaults), the current selection
    # IS the persisted/default set. Seed the multiselect from it.
    current = set(config.enabled)
    options = [(s.id, s.label, s.description) for s in segments]

    def _preview(enabled: set[str], toggles: dict[str, bool]) -> list[str]:
        return _sample_footer(segments, enabled)

    try:
        result = await multiselect(
            "Status line — toggle footer segments",
            options,
            selected=current,
            # Seed both toggles from the PERSISTED config so a confirm preserves
            # them. The 3rd tuple element is the initial checked state — without
            # it the picker would reset ``multiline`` (and ``use_theme_colors``)
            # to False on every confirm, silently wiping a stored ``True``.
            extra_toggles=[
                ("use_theme_colors", "Use theme colors", config.use_theme_colors),
                ("multiline", "Multi-line status line", config.multiline),
            ],
            preview=_preview,
        )
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ statusline picker failed: {exc}", style="bold red"))
        return
    if result is None:
        return  # Esc / cancelled — no write

    chosen, toggles = result
    new_config = StatuslineConfig(
        enabled=[s.id for s in segments if s.id in chosen],  # registry order
        use_theme_colors=bool(toggles.get("use_theme_colors", config.use_theme_colors)),
        # Carry the multiline flag through the rebuild — the toggle is seeded from
        # (and round-trips) the persisted value, so confirming the picker never
        # silently collapses a multi-line footer back to single-line.
        multiline=bool(toggles.get("multiline", config.multiline)),
    )
    try:
        save(new_config)
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ statusline save failed: {exc}", style="bold red"))
        return
    commit(
        Text(
            f"status line → {len(new_config.enabled)} segment(s) enabled",
            style="green",
        )
    )
    if refresh_footer is not None:
        with contextlib.suppress(Exception):
            refresh_footer()


__all__ = ["run_statusline_picker"]
