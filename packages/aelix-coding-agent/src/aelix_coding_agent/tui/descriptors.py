"""Sprint 6h₁₀c (ADR-0106) — Tier-2 descriptor registry + 8-kind renderer.

Net-new *consumer* wiring for the cross-surface descriptor protocol (ADR-0095).
Neither the host-side ``ui:list-modules`` emitter nor any descriptor surface code
existed before this sprint; the contracts package (``DescriptorEnvelope`` + the 8
payloads, ``SLOT_MULTIPLICITY``) is byte-frozen and read-only here.

Two collaborators, owned by ``run_tui`` (``shell.py``):

- :class:`DescriptorRegistry` — a keyed, stateful store. Every descriptor is
  identified by ``(kind, namespace, id)``; ``apply`` does idempotent replace,
  ``removed=True`` drops the key and signals the renderer to clear its chrome
  state. ``many`` kinds keep all entries ordered by an emission counter;
  ``one``-per-subkey kinds (``tool-renderer-desc`` / ``command-route`` /
  ``management-modal``) dedup on the **payload discriminator**
  (``tool_name`` / ``command``), not ``id`` (``slots.py:20-24``).
- :class:`DescriptorRenderer` — per-kind dispatch onto the existing live chrome /
  overlay / footer (Sprint 6h₁₀b destinations, §1.3 table).

The probe object :class:`ListModulesProbe` is emitted once at session start over
``runtime.event_bus`` (``api.py`` ``EventBus``); T1 extensions append descriptors
during the synchronous emit (ADR-0095:130-149). :meth:`DescriptorRegistry.collect`
is the subscriber: it validates each appended item via
``DescriptorEnvelope.model_validate`` (tolerating a ``dict`` *or* a model) and
logs+drops invalid items (forward-compat, ADR-0095:162-164).

Deferred (explicit, §1.6 — NOT implemented here):

- ``ctx.ui.invalidate_descriptors()`` live re-probe (would add a contract method;
  6h₁₀c does a one-shot session-start probe only).
- ``command-route`` live autocomplete completion (autocomplete dispatch deferred
  since ADR-0105:86); the route metadata is stored but not wired to completion.
- ``ActionDescriptor`` reverse-channel (``plugin_action`` emit back to a plugin);
  ``management-modal`` actions render but do not dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
from aelix_agent_core.contracts.slots import SLOT_MULTIPLICITY
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aelix_coding_agent.extensions.widget_protocols import OverlayOptions
from aelix_coding_agent.tui.overlay import make_float, show_modal
from aelix_coding_agent.tui.widgets import RichComponent

if TYPE_CHECKING:
    from collections.abc import Callable

    from prompt_toolkit.layout import Float
    from prompt_toolkit.layout.containers import AnyContainer

    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.footer_data import AelixFooterData

_log = logging.getLogger(__name__)

# Discriminator field on the payload that keys ``one``-per-subkey dedup.
_SUBKEY_FIELD: dict[str, str] = {
    "tool-renderer-desc": "tool_name",
    "command-route": "command",
    "management-modal": "command",
}

# status-item / agent-metric level → theme role (themes.fg role names).
_LEVEL_STYLE: dict[str, str] = {
    "info": "",
    "success": "green",
    "warning": "yellow",
    "error": "red",
}

# toast level → Rich Panel border style.
_TOAST_BORDER: dict[str, str] = {
    "info": "blue",
    "success": "green",
    "warning": "yellow",
    "error": "red",
}

_RENDER_WIDTH = 80


@dataclass
class ListModulesProbe:
    """Mutable probe emitted on ``ui:list-modules`` (ADR-0095:130-149).

    Host emits one instance; T1 extensions append descriptors (``dict`` or
    :class:`DescriptorEnvelope`) to :attr:`modules` during the synchronous emit.
    """

    modules: list[Any] = field(default_factory=list)


@dataclass
class _Entry:
    """A stored descriptor + its emission order + its chrome key."""

    envelope: DescriptorEnvelope
    order: int
    key: str


class DescriptorRegistry:
    """Keyed, stateful store for descriptors (multiplicity / dedup / removal).

    Keyed by ``(kind, namespace, id)`` for ``many`` kinds and by
    ``(kind, namespace, <discriminator>)`` for ``one``-per-subkey kinds. ``apply``
    overwrites idempotently; ``removed=True`` drops the key and notifies the
    renderer (via :attr:`on_apply` / :attr:`on_remove`) so it can clear chrome
    state. Emission order is a monotonic counter used to render ``many`` kinds in
    arrival order.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], _Entry] = {}
        self._counter: int = 0
        # Renderer callbacks, wired by run_tui after construction. ``apply`` fires
        # on_apply(envelope, chrome_key); a removal fires on_remove(kind, key).
        self.on_apply: Callable[[DescriptorEnvelope, str], None] | None = None
        self.on_remove: Callable[[str, str], None] | None = None

    # --- subkey / chrome-key derivation ----------------------------------

    @staticmethod
    def _subkey(envelope: DescriptorEnvelope) -> str:
        """The dedup discriminator: payload field for ``one`` kinds, else ``id``."""
        field_name = _SUBKEY_FIELD.get(envelope.kind)
        if field_name is not None:
            value = getattr(envelope.payload, field_name, None)
            if value is not None:
                return str(value)
        return envelope.id

    @classmethod
    def _store_key(cls, envelope: DescriptorEnvelope) -> tuple[str, str, str]:
        return (envelope.kind, envelope.namespace, cls._subkey(envelope))

    @staticmethod
    def chrome_key(envelope: DescriptorEnvelope) -> str:
        """Stable per-descriptor key for chrome setters (``ns:id``)."""
        return f"{envelope.namespace}:{envelope.id}"

    # --- mutation ---------------------------------------------------------

    def apply(self, envelope: DescriptorEnvelope) -> None:
        """Idempotent replace or remove a descriptor by its store key.

        Unknown ``kind`` is logged + dropped (forward-compat). ``removed=True``
        drops the key and fires :attr:`on_remove`; otherwise the entry is stored
        (overwriting any prior at the same key) and :attr:`on_apply` fires.
        """
        if envelope.kind not in SLOT_MULTIPLICITY:
            _log.warning("descriptor: unknown kind %r dropped", envelope.kind)
            return

        store_key = self._store_key(envelope)
        chrome_key = self.chrome_key(envelope)

        if envelope.removed:
            prior = self._entries.pop(store_key, None)
            if prior is not None and self.on_remove is not None:
                # Contain a faulty renderer (one bad descriptor must not abort the
                # rest of a probe batch) but log it — a silently swallowed render
                # is undebuggable ("my toast didn't show").
                try:
                    self.on_remove(envelope.kind, prior.key)
                except Exception:  # noqa: BLE001 - contained + logged
                    _log.warning(
                        "descriptor: clear failed for %r", prior.key, exc_info=True
                    )
            return

        self._counter += 1
        self._entries[store_key] = _Entry(
            envelope=envelope, order=self._counter, key=chrome_key
        )
        if self.on_apply is not None:
            try:
                self.on_apply(envelope, chrome_key)
            except Exception:  # noqa: BLE001 - contained + logged
                _log.warning(
                    "descriptor: render failed for %r", chrome_key, exc_info=True
                )

    def collect(self, probe: ListModulesProbe) -> None:
        """``ui:list-modules`` subscriber: validate + apply each probe item.

        Each ``probe.modules`` item is validated via
        ``DescriptorEnvelope.model_validate`` (tolerating a ``dict`` or a model).
        Invalid items are logged + dropped (ADR-0095:162-164).
        """
        for item in list(getattr(probe, "modules", []) or []):
            try:
                envelope = DescriptorEnvelope.model_validate(item)
            except Exception as exc:  # noqa: BLE001 — forward-compat: drop bad items
                _log.warning("descriptor: invalid module dropped: %s", exc)
                continue
            self.apply(envelope)

    # --- read access (emission-ordered) -----------------------------------

    def entries(self) -> list[_Entry]:
        """All stored entries, ordered by emission counter."""
        return sorted(self._entries.values(), key=lambda e: e.order)

    def by_kind(self, kind: str) -> list[DescriptorEnvelope]:
        """Stored envelopes of ``kind``, emission-ordered."""
        return [e.envelope for e in self.entries() if e.envelope.kind == kind]


class DescriptorRenderer:
    """Per-kind dispatch of descriptors onto the live chrome / overlay / footer.

    Wired to the live components by ``run_tui``. ``render(envelope, key)`` is the
    apply path; ``clear(kind, key)`` is the removal path. The §1.3 mapping:

    - ``footer-segment`` (FULL) → ``footer.set_status`` + recompose →
      ``chrome.set_footer_line``; emission-ordered. Tooltip ignored (no hover).
    - ``status-item`` (FULL) → ``chrome.set_status``; level → color.
    - ``toast`` (FULL) → ``chrome.add_float`` (non-capturing Float) + auto-dismiss
      via ``loop.call_later(auto_dismiss_ms/1000, ...)``; level → border color.
    - ``tool-renderer-desc`` (FULL renderer) → Rich Table/Columns/form/Panel →
      ``RichComponent.render(width)`` → ``chrome.print_above`` via
      :meth:`render_tool_result`. Live tool-result interception is out of scope
      this sprint; the descriptor is stored and the renderer is exercisable.
    - ``management-modal`` (FULL render+open) → ``show_modal``. ``ActionDescriptor``
      reverse-channel dispatch is DEFERRED (§1.6).
    - ``command-route`` (PARTIAL) → metadata stored only; live autocomplete
      completion is DEFERRED (ADR-0105:86).
    - ``breadcrumb`` (DEGRADE) → ``chrome.set_header_line(chain)``.
    - ``agent-metric`` (DEGRADE) → ``chrome.set_widget(key, lines)``.
    """

    def __init__(
        self,
        chrome: AelixChrome,
        footer: AelixFooterData,
        registry: DescriptorRegistry,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        spawn: Callable[[Any], Any] | None = None,
        refresh_footer: Callable[[], None] | None = None,
        width: int = _RENDER_WIDTH,
    ) -> None:
        self._chrome = chrome
        self._footer = footer
        self._registry = registry
        self._loop = loop
        # The footer line is composed by ONE owner (``context._refresh_footer``:
        # ``⎇ branch`` + all extension statuses). footer-segment descriptors land
        # in ``footer.set_status`` like any status, so we trigger that shared
        # composer rather than overwriting the line (which would drop the branch).
        # Falls back to a descriptor-only recompose when unwired (standalone tests).
        self._refresh_footer_cb = refresh_footer
        # ``spawn`` schedules a coroutine (modal open / print_above). Defaults to
        # ``asyncio.ensure_future``; injectable for headless tests.
        self._spawn = spawn if spawn is not None else asyncio.ensure_future
        self._width = width
        # Active toast Floats keyed by chrome_key, so a removal/replace can drop
        # the prior Float before adding the new one.
        self._toast_floats: dict[str, Float] = {}
        # Stored command-route metadata (PARTIAL: not wired to completion yet).
        self.command_routes: dict[str, Any] = {}

    def _loop_now(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is not None:
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    # --- dispatch entry points (wired as registry callbacks) --------------

    def render(self, envelope: DescriptorEnvelope, key: str) -> None:
        handler = getattr(self, f"_render_{envelope.kind.replace('-', '_')}", None)
        if handler is None:
            _log.warning("descriptor: no renderer for kind %r", envelope.kind)
            return
        handler(envelope, key)

    def clear(self, kind: str, key: str) -> None:
        if kind == "footer-segment":
            self._footer.set_status(key, None)
            self._do_refresh_footer()
        elif kind == "status-item":
            self._chrome.set_status(key, None)
        elif kind == "agent-metric":
            self._chrome.set_widget(key, None)
        elif kind == "toast":
            self._dismiss_toast(key)
        elif kind == "command-route":
            self.command_routes.pop(key, None)
        elif kind == "breadcrumb":
            self._recompose_breadcrumbs()
        # tool-renderer-desc / management-modal hold no standing chrome state.

    # --- footer-segment (FULL) --------------------------------------------

    def _render_footer_segment(self, envelope: DescriptorEnvelope, key: str) -> None:
        payload = envelope.payload
        icon = getattr(payload, "icon", None)
        text = getattr(payload, "text", "")
        segment = f"{icon} {text}".strip() if icon else text
        self._footer.set_status(key, segment)
        self._do_refresh_footer()

    def _do_refresh_footer(self) -> None:
        # Prefer the single shared footer composer (preserves branch + non-descriptor
        # statuses); fall back to a descriptor-only recompose when unwired.
        if self._refresh_footer_cb is not None:
            self._refresh_footer_cb()
        else:
            self._recompose_footer()

    def _recompose_footer(self) -> None:
        # Emission-ordered compose: walk the registry's footer-segment entries in
        # order, joining their footer-store text.
        statuses = self._footer.get_extension_statuses()
        parts: list[str] = []
        for env in self._registry.by_kind("footer-segment"):
            key = self._registry.chrome_key(env)
            if key in statuses:
                parts.append(statuses[key])
        self._chrome.set_footer_line("  ".join(parts))

    # --- status-item (FULL) -----------------------------------------------

    def _render_status_item(self, envelope: DescriptorEnvelope, key: str) -> None:
        payload = envelope.payload
        text = getattr(payload, "text", "")
        level = getattr(payload, "level", "info")
        self._chrome.set_status(key, self._style(text, level))

    @staticmethod
    def _style(text: str, level: str) -> str:
        color = _LEVEL_STYLE.get(level, "")
        if not color:
            return text
        return "".join(RichComponent(Text(text, style=color)).render(_RENDER_WIDTH))

    # --- toast (FULL) -----------------------------------------------------

    def _render_toast(self, envelope: DescriptorEnvelope, key: str) -> None:
        payload = envelope.payload
        text = getattr(payload, "text", "")
        level = getattr(payload, "level", "info")
        auto_dismiss_ms = getattr(payload, "auto_dismiss_ms", 4000)

        # Replace any prior toast at this key.
        self._dismiss_toast(key)

        border = _TOAST_BORDER.get(level, "blue")
        panel = Panel(Text(text), border_style=border, expand=False)
        content = self._float_content(panel)
        options = OverlayOptions(anchor="top-right", offset_x=1, offset_y=1, non_capturing=True)
        float_ = make_float(content, options)
        self._toast_floats[key] = float_
        self._chrome.add_float(float_)

        if auto_dismiss_ms and auto_dismiss_ms > 0:
            loop = self._loop_now()
            if loop is not None:
                loop.call_later(auto_dismiss_ms / 1000.0, lambda: self._dismiss_toast(key))

    def _dismiss_toast(self, key: str) -> None:
        float_ = self._toast_floats.pop(key, None)
        if float_ is not None:
            self._chrome.remove_float(float_)

    def _float_content(self, renderable: object) -> AnyContainer:
        from prompt_toolkit.formatted_text import ANSI
        from prompt_toolkit.layout import Window
        from prompt_toolkit.layout.controls import FormattedTextControl

        lines = RichComponent(renderable).render(self._width)
        return Window(
            FormattedTextControl(ANSI("\n".join(lines))), dont_extend_height=True
        )

    # --- tool-renderer-desc (FULL renderer) -------------------------------

    def _render_tool_renderer_desc(self, envelope: DescriptorEnvelope, key: str) -> None:  # noqa: ARG002
        # Stored in the registry on apply; live tool-result interception is out of
        # scope this sprint. The renderable build is exercised via render_tool_result.
        return

    def render_tool_result(self, envelope: DescriptorEnvelope, rows: Any = None) -> None:
        """Build a Rich renderable for a tool-renderer-desc + commit it above.

        ``view``: ``table`` → Rich ``Table``; ``grid`` → Rich ``Columns``; ``form``
        → a label/value ``Table`` (no header); ``text`` → Rich ``Panel``. The
        renderable is rendered to ANSI lines and committed via ``print_above``.
        """
        renderable = self.build_tool_renderable(envelope, rows)
        self._spawn(self._chrome.print_above(renderable))

    @staticmethod
    def build_tool_renderable(envelope: DescriptorEnvelope, rows: Any = None) -> object:
        payload = envelope.payload
        view = getattr(payload, "view", "text")
        title = getattr(payload, "title", None)
        columns = getattr(payload, "columns", None) or []
        row_data = rows if isinstance(rows, list) else []

        if view == "table":
            table = Table(title=title)
            headers = [str(c.get("header", c.get("key", ""))) for c in columns]
            for header in headers:
                table.add_column(header)
            keys = [str(c.get("key", c.get("header", ""))) for c in columns]
            for row in row_data:
                if isinstance(row, dict):
                    table.add_row(*[str(row.get(k, "")) for k in keys])
                else:
                    table.add_row(str(row))
            return table

        if view == "grid":
            cells = [Text(str(item)) for item in row_data]
            return Columns(cells, title=title) if cells else Columns([Text(title or "")])

        if view == "form":
            form = Table(show_header=False, box=None, title=title)
            form.add_column("field")
            form.add_column("value")
            for row in row_data:
                if isinstance(row, dict):
                    for label, value in row.items():
                        form.add_row(str(label), str(value))
            return form

        # text
        body = "\n".join(str(r) for r in row_data) if row_data else ""
        return Panel(Text(body), title=title)

    # --- management-modal (FULL render + open) ----------------------------

    def _render_management_modal(self, envelope: DescriptorEnvelope, key: str) -> None:  # noqa: ARG002
        # Stored in the registry on apply; opened on demand via open_modal so the
        # session-start probe does not pop a blocking modal. ActionDescriptor
        # reverse-channel dispatch is DEFERRED (§1.6).
        return

    def open_modal(self, envelope: DescriptorEnvelope) -> None:
        """Open the management-modal for ``envelope`` as a focusable Float."""
        renderable = self.build_tool_renderable(envelope)

        def build(_result: asyncio.Future[Any]) -> AnyContainer:
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import HSplit

            kb = KeyBindings()
            kb.add("escape")(lambda _e: _resolve(_result, None))
            kb.add("c-c")(lambda _e: _resolve(_result, None))
            inner = self._float_content(renderable)
            return HSplit([inner], key_bindings=kb)

        self._spawn(show_modal(self._chrome, build))

    # --- command-route (PARTIAL — store only) -----------------------------

    def _render_command_route(self, envelope: DescriptorEnvelope, key: str) -> None:
        # Store route metadata; live autocomplete completion is DEFERRED
        # (ADR-0105:86 autocomplete dispatch deferred). The registry dedups
        # command-route on the `command` discriminator (not id), so a same-command
        # re-emit under a different id replaces one store entry — drop any prior
        # render-side route for the same command (keyed here by ns:id) so a stale
        # description/keybind can't linger for a re-pointed command.
        command = getattr(envelope.payload, "command", None)
        if command is not None:
            stale = [
                k
                for k, p in self.command_routes.items()
                if k != key and getattr(p, "command", None) == command
            ]
            for k in stale:
                del self.command_routes[k]
        self.command_routes[key] = envelope.payload

    # --- breadcrumb (DEGRADE → header line) -------------------------------

    def _render_breadcrumb(self, envelope: DescriptorEnvelope, key: str) -> None:  # noqa: ARG002
        self._recompose_breadcrumbs()

    def _recompose_breadcrumbs(self) -> None:
        labels = [
            str(getattr(env.payload, "label", "")) for env in self._registry.by_kind("breadcrumb")
        ]
        self._chrome.set_header_line(" › ".join(p for p in labels if p))

    # --- agent-metric (DEGRADE → widget) ----------------------------------

    def _render_agent_metric(self, envelope: DescriptorEnvelope, key: str) -> None:
        payload = envelope.payload
        label = getattr(payload, "label", "")
        value = getattr(payload, "value", "")
        delta = getattr(payload, "delta", None)
        line = f"{label}: {value}"
        if delta:
            line = f"{line} ({delta})"
        self._chrome.set_widget(key, [line])


def _resolve(result: asyncio.Future[Any], value: Any) -> None:
    if not result.done():
        result.set_result(value)


__all__ = ["DescriptorRegistry", "DescriptorRenderer", "ListModulesProbe"]
