"""Issue #21 — manifest ``contributes.tui_widgets`` → chrome widget adapter.

The declarative half of the extension widget surface (ADR-0182). A manifest
may declare

.. code-block:: toml

    [contributes]
    tui_widgets = [{ slot = "above_editor", factory = "my_mod:make_widget" }]

and ``run_tui`` paints those widgets through the SAME ``ctx.ui.set_widget``
path extensions use imperatively: ``slot`` selects the placement (the
:data:`~aelix_coding_agent.extensions.ext_ui.WidgetPlacement` literals) and
``factory`` is a ``module:attr`` import spec resolved through the loader's
colon-form resolver and treated as a
:data:`~aelix_coding_agent.extensions.ext_ui.WidgetFactory` —
``AelixTUIContext.set_widget`` invokes it once with ``(tui, theme)`` and
stores the rendered lines (the static-snapshot semantics shared with the
imperative path).

Resolving ``factory`` is an IMPORT, so this adapter must only ever run
against LOADED (eager) extensions — never inside a metadata-only scan
(ADR-0181 Slice A) and never for a pending lazy shell
(``contributes.tui_widgets`` forces eager in ``_is_lazy_eligible``;
``pending`` is a defensive second fence).

TUI-only by construction: the only caller lives in ``run_tui``'s ``_rebind``
(the headless ``ctx.ui`` stub raises on ``set_widget``).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, cast, get_args

from aelix_coding_agent.extensions.ext_ui import (
    ExtensionWidgetOptions,
    WidgetPlacement,
)

if TYPE_CHECKING:
    from collections.abc import Collection

    from aelix_coding_agent.extensions.ext_ui import (
        ExtensionUIContext,
        WidgetFactory,
    )

logger = logging.getLogger(__name__)

# Derived from the WidgetPlacement Literal so a new placement can't drift.
_PLACEMENTS = get_args(WidgetPlacement)


def apply_manifest_widgets(
    runner: Any,
    ui: ExtensionUIContext,
    applied: dict[str, str],
    *,
    pending: Collection[str] = (),
) -> None:
    """Reconcile the chrome against the CURRENT extension set's widget contribs.

    The caller re-runs this on every ``_rebind`` (startup, /resume·/fork
    swaps, and #24 /reload all rebuild the extension set): every surviving
    contrib is (re-)applied — the factory re-renders, so content refreshes —
    and keys in ``applied`` that no longer correspond to a loaded contrib are
    un-painted. ``applied`` maps chrome widget key → placement and is mutated
    in place. Never raises past a contrib: a faulty slot/factory is skipped
    with a warning (a bad widget must not break the TUI); ``runner`` is read
    duck-typed (``None`` → un-paint everything).
    """

    new: dict[str, str] = {}
    try:
        extensions = list(getattr(runner, "extensions", None) or [])
    except Exception:  # noqa: BLE001 — a raising runner property must not
        # skip the reconcile below (stale widgets still un-paint).
        logger.warning("extension runner unreadable; un-painting all manifest widgets", exc_info=True)
        extensions = []
    for ext in extensions:
        name = getattr(ext, "name", "<unknown>")
        manifest = getattr(ext, "manifest", None)
        contribs = manifest.contributes.tui_widgets if manifest is not None else []
        if not contribs:
            continue
        if name in pending:
            # Defensive fence: contributes.tui_widgets forces eager loading
            # (loader._is_lazy_eligible), so a pending lazy shell should never
            # carry widget contribs — and resolving ``factory`` is an import,
            # which must never happen for a not-yet-activated plugin.
            logger.warning(
                "extension %r tui_widgets skipped: plugin is pending lazy "
                "activation (widgets require eager loading)",
                name,
            )
            continue
        for index, contrib in enumerate(contribs):
            slot = contrib.slot
            if slot not in _PLACEMENTS:
                logger.warning(
                    "extension %r tui_widgets[%d] skipped: unknown slot %r "
                    "(expected one of %s)",
                    name,
                    index,
                    slot,
                    "/".join(_PLACEMENTS),
                )
                continue
            key = f"ext:{name}:tui_widgets[{index}]"
            if key in new:
                # Two loaded extensions share a name (e.g. the same plugin id
                # discovered in both the project and global dirs) — FIRST
                # registration wins, matching get_shortcuts (review LOW:
                # silent last-wins would dual-paint when the slots differ).
                logger.warning(
                    "extension %r tui_widgets[%d] skipped: widget key %r "
                    "already applied by an earlier extension",
                    name,
                    index,
                    key,
                )
                continue
            try:
                # Function-local import — the command_dispatch convention for
                # reaching loader helpers without a module-level dependency.
                from aelix_coding_agent.extensions.loader import _factory_from_module

                factory = _factory_from_module(contrib.factory)
            except Exception:  # noqa: BLE001 — a bad contrib must not break the TUI
                logger.warning(
                    "extension %r tui_widgets[%d] skipped: factory %r failed "
                    "to resolve",
                    name,
                    index,
                    contrib.factory,
                    exc_info=True,
                )
                continue
            options = ExtensionWidgetOptions(placement=cast("WidgetPlacement", slot))
            try:
                ui.set_widget(key, cast("WidgetFactory", factory), options)
            except Exception:  # noqa: BLE001 — a bad contrib must not break the TUI
                logger.warning(
                    "extension %r tui_widgets[%d] skipped: factory %r raised "
                    "while rendering",
                    name,
                    index,
                    contrib.factory,
                    exc_info=True,
                )
                continue
            new[key] = slot
    for key, placement in applied.items():
        # Un-paint on removal AND on placement change: the chrome keeps one
        # dict PER placement, so a slot that moved (plugin edit + /reload)
        # leaves the old placement's entry behind unless it is popped here —
        # targeting the OLD placement cannot clobber the freshly painted new
        # one (review MEDIUM, live-reproduced permanent leak).
        if key not in new or new[key] != placement:
            with contextlib.suppress(Exception):
                ui.set_widget(
                    key,
                    None,
                    ExtensionWidgetOptions(
                        placement=cast("WidgetPlacement", placement)
                    ),
                )
    applied.clear()
    applied.update(new)
