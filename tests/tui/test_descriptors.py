"""Sprint 6h₁₀c (ADR-0106) — DescriptorRegistry + DescriptorRenderer tests.

Headless, no real TTY and no real sleeps. The registry is exercised directly;
the renderer is driven against an inspectable fake chrome / fake footer and a
fake loop+spawn so toast auto-dismiss is deterministic. The probe seam is driven
through the real ``EventBus`` (``api.py``) with a fake extension subscriber.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.contracts.descriptor import ActionDescriptor, DescriptorEnvelope
from aelix_coding_agent.extensions.api import EventBus
from aelix_coding_agent.tui.descriptors import (
    DescriptorRegistry,
    DescriptorRenderer,
    ListModulesProbe,
)

# --- builders ----------------------------------------------------------------


def _env(
    kind: str, *, ns: str = "ext", id_: str = "a", removed: bool = False, **payload: Any
) -> DescriptorEnvelope:
    body: dict[str, Any] = {"kind": kind, **payload}
    return DescriptorEnvelope(
        kind=kind, namespace=ns, id=id_, payload=body, removed=removed  # type: ignore[arg-type]
    )


# --- fakes -------------------------------------------------------------------


class FakeChrome:
    def __init__(self) -> None:
        self.status: dict[str, str | None] = {}
        self.footer_line: str | None = None
        self.header_line: str | None = None
        self.breadcrumb_line: str | None = None
        self.widgets: dict[str, list[str] | None] = {}
        self.floats: list[object] = []
        self.printed: list[object] = []

    def set_status(self, key: str, text: str | None) -> None:
        self.status[key] = text

    def set_footer_line(self, text: str) -> None:
        self.footer_line = text

    def set_header_line(self, text: str) -> None:
        self.header_line = text

    def set_breadcrumb_line(self, text: str) -> None:
        self.breadcrumb_line = text

    def set_widget(self, key: str, lines: list[str] | None, *, above: bool = True) -> None:  # noqa: ARG002
        self.widgets[key] = lines

    def add_float(self, float_: object) -> None:
        self.floats.append(float_)

    def remove_float(self, float_: object) -> None:
        if float_ in self.floats:
            self.floats.remove(float_)

    async def print_above(self, renderable: object) -> None:
        self.printed.append(renderable)


class FakeFooter:
    def __init__(self) -> None:
        self._statuses: dict[str, str] = {}

    def set_status(self, key: str, text: str | None) -> None:
        if text is None:
            self._statuses.pop(key, None)
        else:
            self._statuses[key] = text

    def get_extension_statuses(self) -> dict[str, str]:
        return dict(self._statuses)


class FakeLoop:
    """Records call_later schedules; ``fire_all`` runs the due callbacks."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[float, Any]] = []

    def call_later(self, delay: float, cb: Any) -> object:
        self.scheduled.append((delay, cb))
        return object()

    def fire_all(self) -> None:
        for _delay, cb in list(self.scheduled):
            cb()
        self.scheduled.clear()


def _make_renderer() -> tuple[DescriptorRenderer, DescriptorRegistry, FakeChrome, FakeFooter, FakeLoop, list[Any]]:
    chrome = FakeChrome()
    footer = FakeFooter()
    registry = DescriptorRegistry()
    loop = FakeLoop()
    spawned: list[Any] = []

    def _spawn(coro: Any) -> None:
        spawned.append(coro)
        # Close the coroutine so it is never GC'd un-awaited (no real loop here).
        if hasattr(coro, "close"):
            coro.close()

    renderer = DescriptorRenderer(
        chrome,  # type: ignore[arg-type]
        footer,  # type: ignore[arg-type]
        registry,
        loop=loop,  # type: ignore[arg-type]
        spawn=_spawn,
    )
    registry.on_apply = renderer.render
    registry.on_remove = renderer.clear
    return renderer, registry, chrome, footer, loop, spawned


# === Registry: apply / replace / remove ======================================


def test_registry_apply_and_replace_by_key() -> None:
    reg = DescriptorRegistry()
    reg.apply(_env("status-item", id_="x", text="first"))
    reg.apply(_env("status-item", id_="x", text="second"))
    items = reg.by_kind("status-item")
    assert len(items) == 1
    assert items[0].payload.text == "second"  # type: ignore[union-attr]


def test_registry_remove_drops_key_and_signals_clear() -> None:
    reg = DescriptorRegistry()
    cleared: list[tuple[str, str]] = []
    reg.on_remove = lambda kind, key: cleared.append((kind, key))
    reg.apply(_env("status-item", id_="x", text="hi"))
    reg.apply(_env("status-item", id_="x", text="hi", removed=True))
    assert reg.by_kind("status-item") == []
    assert cleared == [("status-item", "ext:x")]


def test_registry_many_kind_keeps_distinct_ids() -> None:
    reg = DescriptorRegistry()
    reg.apply(_env("footer-segment", id_="a", text="A"))
    reg.apply(_env("footer-segment", id_="b", text="B"))
    assert len(reg.by_kind("footer-segment")) == 2


def test_registry_one_subkey_dedup_on_discriminator_not_id() -> None:
    # Two tool-renderer-desc with the same tool_name but DIFFERENT id collapse to one.
    reg = DescriptorRegistry()
    reg.apply(_env("tool-renderer-desc", id_="id1", tool_name="grep", view="table"))
    reg.apply(_env("tool-renderer-desc", id_="id2", tool_name="grep", view="text"))
    items = reg.by_kind("tool-renderer-desc")
    assert len(items) == 1
    assert items[0].payload.view == "text"  # type: ignore[union-attr]


def test_registry_command_route_dedup_on_command() -> None:
    reg = DescriptorRegistry()
    reg.apply(_env("command-route", id_="i1", command="deploy", description="one"))
    reg.apply(_env("command-route", id_="i2", command="deploy", description="two"))
    items = reg.by_kind("command-route")
    assert len(items) == 1
    assert items[0].payload.description == "two"  # type: ignore[union-attr]


def test_registry_emission_order_preserved() -> None:
    reg = DescriptorRegistry()
    reg.apply(_env("footer-segment", id_="c", text="C"))
    reg.apply(_env("footer-segment", id_="a", text="A"))
    reg.apply(_env("footer-segment", id_="b", text="B"))
    texts = [e.payload.text for e in reg.by_kind("footer-segment")]  # type: ignore[union-attr]
    assert texts == ["C", "A", "B"]


def test_registry_replace_keeps_latest_emission_order() -> None:
    reg = DescriptorRegistry()
    reg.apply(_env("status-item", id_="a", text="A"))
    reg.apply(_env("status-item", id_="b", text="B"))
    reg.apply(_env("status-item", id_="a", text="A2"))  # re-emit a → moves to back
    texts = [e.payload.text for e in reg.by_kind("status-item")]  # type: ignore[union-attr]
    assert texts == ["B", "A2"]


# === Renderer: per-kind dispatch =============================================


def test_render_status_item_sets_status_with_level_color() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("status-item", id_="s", text="warn", level="warning"))
    assert "ext:s" in chrome.status
    assert chrome.status["ext:s"] is not None
    assert "warn" in chrome.status["ext:s"]  # styled text still contains the message


def test_render_footer_segment_composes_footer_line() -> None:
    renderer, registry, chrome, footer, *_ = _make_renderer()
    registry.apply(_env("footer-segment", id_="a", text="Left"))
    registry.apply(_env("footer-segment", id_="b", text="Right"))
    assert chrome.footer_line == "Left  Right"
    assert footer.get_extension_statuses()["ext:a"] == "Left"


def test_render_footer_segment_removal_recomposes() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("footer-segment", id_="a", text="Left"))
    registry.apply(_env("footer-segment", id_="b", text="Right"))
    registry.apply(_env("footer-segment", id_="a", text="Left", removed=True))
    assert chrome.footer_line == "Right"


def test_footer_segment_delegates_to_shared_composer_when_wired() -> None:
    # Regression (footer ownership): when refresh_footer is wired (production via
    # context._refresh_footer), the renderer publishes the segment to the shared
    # footer store and triggers the single composer (which keeps ⎇ branch +
    # non-descriptor statuses) instead of overwriting the footer line itself.
    chrome = FakeChrome()
    footer = FakeFooter()
    registry = DescriptorRegistry()
    calls: list[int] = []
    renderer = DescriptorRenderer(
        chrome,  # type: ignore[arg-type]
        footer,  # type: ignore[arg-type]
        registry,
        refresh_footer=lambda: calls.append(1),
    )
    registry.on_apply = renderer.render
    registry.on_remove = renderer.clear

    registry.apply(_env("footer-segment", id_="a", text="Left"))
    assert footer.get_extension_statuses()["ext:a"] == "Left"  # published to shared store
    assert calls == [1]  # shared composer invoked
    assert chrome.footer_line is None  # renderer did NOT compose the line itself

    registry.apply(_env("footer-segment", id_="a", text="Left", removed=True))
    assert "ext:a" not in footer.get_extension_statuses()  # cleared from shared store
    assert calls == [1, 1]  # composer invoked again on removal


def test_render_toast_adds_float_and_schedules_dismiss() -> None:
    renderer, registry, chrome, _footer, loop, _ = _make_renderer()
    registry.apply(_env("toast", id_="t", text="hi", level="success", auto_dismiss_ms=4000))
    assert len(chrome.floats) == 1
    assert len(loop.scheduled) == 1
    assert loop.scheduled[0][0] == 4.0  # 4000ms / 1000
    loop.fire_all()
    assert chrome.floats == []  # dismissed, no real sleep


def test_render_toast_no_dismiss_when_ms_zero() -> None:
    renderer, registry, chrome, _footer, loop, _ = _make_renderer()
    registry.apply(_env("toast", id_="t", text="sticky", auto_dismiss_ms=0))
    assert len(chrome.floats) == 1
    assert loop.scheduled == []  # 0 → no timer


def test_render_breadcrumb_sets_breadcrumb_row_not_header() -> None:
    # §D: breadcrumbs land in the dedicated breadcrumb row, freeing the header
    # line (the ``set_header`` factory row) from collision.
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("breadcrumb", id_="a", label="Home"))
    registry.apply(_env("breadcrumb", id_="b", label="Repo"))
    assert chrome.breadcrumb_line == "Home › Repo"
    assert chrome.header_line is None  # header line untouched


def test_render_breadcrumb_coexists_with_header_line() -> None:
    # §D: a header line set by the set_header factory and a descriptor breadcrumb
    # row are independent — neither overwrites the other.
    renderer, registry, chrome, *_ = _make_renderer()
    chrome.set_header_line("Aelix")
    registry.apply(_env("breadcrumb", id_="a", label="Home"))
    assert chrome.header_line == "Aelix"
    assert chrome.breadcrumb_line == "Home"


def test_render_breadcrumb_removal_recomposes() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("breadcrumb", id_="a", label="Home"))
    registry.apply(_env("breadcrumb", id_="b", label="Repo"))
    registry.apply(_env("breadcrumb", id_="a", label="Home", removed=True))
    assert chrome.breadcrumb_line == "Repo"


def test_render_agent_metric_composes_single_strip_with_level_color() -> None:
    # §E: ALL agent-metrics compose into ONE widget slot (a horizontal strip),
    # not one widget per metric key.
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("agent-metric", id_="m1", label="tokens", value=42, delta="+3"))
    registry.apply(_env("agent-metric", id_="m2", label="cost", value="0.01", level="warning"))
    # Single shared slot — no per-key widgets.
    assert "ext:m1" not in chrome.widgets
    assert "ext:m2" not in chrome.widgets
    slot = chrome.widgets[DescriptorRenderer._AGENT_METRIC_SLOT]
    assert slot is not None
    rendered = "\n".join(slot)
    assert "tokens: 42 (+3)" in rendered
    assert "cost: 0.01" in rendered


def test_render_agent_metric_removal_recomposes_strip() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("agent-metric", id_="m1", label="tokens", value=1))
    registry.apply(_env("agent-metric", id_="m2", label="cost", value=2))
    registry.apply(_env("agent-metric", id_="m1", label="tokens", value=1, removed=True))
    slot = chrome.widgets[DescriptorRenderer._AGENT_METRIC_SLOT]
    assert slot is not None
    rendered = "\n".join(slot)
    assert "cost: 2" in rendered
    assert "tokens" not in rendered
    # Removing the last metric clears the slot entirely.
    registry.apply(_env("agent-metric", id_="m2", label="cost", value=2, removed=True))
    assert chrome.widgets[DescriptorRenderer._AGENT_METRIC_SLOT] is None


def test_render_command_route_stores_metadata_only() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    registry.apply(_env("command-route", id_="r", command="deploy", description="ship it"))
    assert "ext:r" in renderer.command_routes
    # PARTIAL: no chrome surface touched (no autocomplete this sprint).
    assert chrome.footer_line is None
    assert chrome.status == {}


def test_render_command_route_replace_drops_stale_key() -> None:
    # Regression: the registry dedups command-route on the `command` discriminator,
    # so a same-command re-emit under a different id must NOT leave a stale
    # render-side route (keyed by ns:id) behind.
    renderer, registry, *_ = _make_renderer()
    registry.apply(_env("command-route", id_="a", command="deploy", description="old"))
    registry.apply(_env("command-route", id_="b", command="deploy", description="new"))
    assert "ext:a" not in renderer.command_routes  # stale dropped
    assert "ext:b" in renderer.command_routes
    assert len(renderer.command_routes) == 1
    # A different command coexists.
    registry.apply(_env("command-route", id_="c", command="rollback", description="x"))
    assert set(renderer.command_routes) == {"ext:b", "ext:c"}


def test_render_tool_result_table_prints_above() -> None:
    renderer, registry, _chrome, _footer, _loop, spawned = _make_renderer()
    env = _env(
        "tool-renderer-desc",
        id_="tr",
        tool_name="grep",
        view="table",
        columns=[{"key": "file", "header": "File"}, {"key": "n", "header": "N"}],
    )
    registry.apply(env)
    renderer.render_tool_result(env, rows=[{"file": "a.py", "n": 3}])
    assert len(spawned) == 1  # a print_above coroutine scheduled
    table = renderer.build_tool_renderable(env, rows=[{"file": "a.py", "n": 3}])
    assert table.__class__.__name__ == "Table"


def test_build_tool_renderable_views() -> None:
    renderer, *_ = _make_renderer()
    grid = renderer.build_tool_renderable(_env("tool-renderer-desc", tool_name="t", view="grid"), rows=["x"])
    form = renderer.build_tool_renderable(_env("tool-renderer-desc", tool_name="t", view="form"), rows=[{"k": "v"}])
    text = renderer.build_tool_renderable(_env("tool-renderer-desc", tool_name="t", view="text"), rows=["line"])
    assert grid.__class__.__name__ == "Columns"
    assert form.__class__.__name__ == "Table"
    assert text.__class__.__name__ == "Panel"


def test_render_management_modal_open_spawns_show_modal() -> None:
    renderer, registry, _chrome, _footer, _loop, spawned = _make_renderer()
    env = _env("management-modal", id_="mm", command="settings", title="Settings", view="form")
    registry.apply(env)
    assert spawned == []  # apply does NOT auto-open (no blocking modal at probe)
    renderer.open_modal(env)
    assert len(spawned) == 1


# === Unknown kind / invalid item ============================================


def test_registry_unknown_kind_logged_and_dropped() -> None:
    reg = DescriptorRegistry()
    applied: list[Any] = []
    reg.on_apply = lambda env, key: applied.append((env, key))

    class _FakeEnv:
        kind = "not-a-real-kind"
        namespace = "ext"
        id = "z"
        removed = False
        payload = None

    reg.apply(_FakeEnv())  # type: ignore[arg-type]
    assert applied == []


def test_collect_validates_and_drops_invalid() -> None:
    reg = DescriptorRegistry()
    probe = ListModulesProbe()
    # valid dict item
    probe.modules.append(
        {"kind": "status-item", "namespace": "ext", "id": "ok", "payload": {"kind": "status-item", "text": "fine"}}
    )
    # invalid: payload.kind mismatch → model_validate raises → dropped
    probe.modules.append(
        {"kind": "status-item", "namespace": "ext", "id": "bad", "payload": {"kind": "toast", "text": "x"}}
    )
    # invalid: not a dict / model
    probe.modules.append("garbage")
    reg.collect(probe)
    items = reg.by_kind("status-item")
    assert len(items) == 1
    assert items[0].id == "ok"


def test_collect_accepts_model_instances() -> None:
    reg = DescriptorRegistry()
    probe = ListModulesProbe()
    probe.modules.append(_env("breadcrumb", id_="b", label="Home"))
    reg.collect(probe)
    assert len(reg.by_kind("breadcrumb")) == 1


# === Probe seam (EventBus) ===================================================


def test_probe_seam_extension_appends_and_renders() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    bus = EventBus()

    def _fake_extension(probe: ListModulesProbe) -> None:
        probe.modules.append(_env("status-item", id_="ext1", text="from-ext"))

    # Extension subscribes first (load order), registry.collect subscribes after.
    bus.on("ui:list-modules", _fake_extension)
    bus.on("ui:list-modules", registry.collect)

    bus.emit("ui:list-modules", ListModulesProbe())
    assert "ext:ext1" in chrome.status
    assert chrome.status["ext:ext1"] == "from-ext"  # info level → no styling


def test_probe_seam_invalid_item_dropped_no_render() -> None:
    renderer, registry, chrome, *_ = _make_renderer()
    bus = EventBus()

    def _bad_extension(probe: ListModulesProbe) -> None:
        probe.modules.append({"kind": "nope"})

    bus.on("ui:list-modules", _bad_extension)
    bus.on("ui:list-modules", registry.collect)
    bus.emit("ui:list-modules", ListModulesProbe())
    assert chrome.status == {}


# === §A — ActionDescriptor reverse-channel (dispatch_action) =================


def _action(confirm: str | None = None) -> ActionDescriptor:
    return ActionDescriptor(
        plugin_id="fix", action="restart", payload={"id": 1}, confirm=confirm
    )


def test_dispatch_action_emits_plugin_action_with_dict() -> None:
    bus = EventBus()
    captured: list[Any] = []
    bus.on("plugin_action", captured.append)
    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()
    renderer = DescriptorRenderer(
        chrome, footer, registry, event_bus=bus  # type: ignore[arg-type]
    )
    renderer.dispatch_action(_action())
    assert len(captured) == 1
    assert captured[0] == {
        "plugin_id": "fix",
        "action": "restart",
        "payload": {"id": 1},
        "confirm": None,
    }


async def test_dispatch_action_confirm_awaits_then_emits_on_yes() -> None:
    bus = EventBus()
    captured: list[Any] = []
    bus.on("plugin_action", captured.append)
    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()
    asked: list[str] = []

    async def _confirm(message: str) -> bool:
        asked.append(message)
        return True

    spawned: list[Any] = []
    renderer = DescriptorRenderer(
        chrome,  # type: ignore[arg-type]
        footer,  # type: ignore[arg-type]
        registry,
        event_bus=bus,
        confirm=_confirm,
        spawn=lambda coro: spawned.append(coro),
    )
    renderer.dispatch_action(_action(confirm="Restart?"))
    assert len(spawned) == 1  # confirm path spawned, no sync emit yet
    assert captured == []
    await spawned[0]
    assert asked == ["Restart?"]
    assert len(captured) == 1


async def test_dispatch_action_confirm_no_emit_on_decline() -> None:
    bus = EventBus()
    captured: list[Any] = []
    bus.on("plugin_action", captured.append)
    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()

    async def _confirm(_message: str) -> bool:
        return False

    spawned: list[Any] = []
    renderer = DescriptorRenderer(
        chrome,  # type: ignore[arg-type]
        footer,  # type: ignore[arg-type]
        registry,
        event_bus=bus,
        confirm=_confirm,
        spawn=lambda coro: spawned.append(coro),
    )
    renderer.dispatch_action(_action(confirm="Sure?"))
    await spawned[0]
    assert captured == []  # declined → no emit


def test_dispatch_action_emit_failure_is_contained() -> None:
    class _BoomBus:
        def emit(self, channel: str, data: Any) -> None:
            raise RuntimeError("bus down")

    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()
    renderer = DescriptorRenderer(
        chrome, footer, registry, event_bus=_BoomBus()  # type: ignore[arg-type]
    )
    renderer.dispatch_action(_action())  # must not raise


def test_dispatch_action_no_event_bus_is_noop() -> None:
    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()
    renderer = DescriptorRenderer(chrome, footer, registry)  # type: ignore[arg-type]
    renderer.dispatch_action(_action())  # no bus → silently dropped, no raise


def _binding_for(kb: Any, key: str) -> Any:
    for binding in kb.bindings:
        if tuple(str(k) for k in binding.keys) == (key,):
            return binding
    raise AssertionError(f"no key binding for {key!r}")


def test_modal_action_key_dispatches_via_reverse_channel() -> None:
    # §C↔§A integration: a modal action bound to a number key fires
    # dispatch_action (→ plugin_action) and closes the modal.
    bus = EventBus()
    captured: list[Any] = []
    bus.on("plugin_action", captured.append)
    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()
    renderer = DescriptorRenderer(
        chrome, footer, registry, event_bus=bus  # type: ignore[arg-type]
    )
    closed: list[bool] = []
    kb = renderer._build_modal_keybindings([_action()], lambda: closed.append(True))

    _binding_for(kb, "1").handler(None)  # press "1"
    assert captured == [_action().model_dump(mode="json")]  # dispatched to plugin
    assert closed == [True]  # modal closed after dispatch


def test_render_action_hints_lists_numbered_actions() -> None:
    chrome, footer, registry = FakeChrome(), FakeFooter(), DescriptorRegistry()
    renderer = DescriptorRenderer(chrome, footer, registry)  # type: ignore[arg-type]
    a1 = ActionDescriptor(plugin_id="p", action="save")
    a2 = ActionDescriptor(plugin_id="p", action="delete")
    hint = renderer._render_action_hints([a1, a2])
    assert "[1] save" in hint.plain
    assert "[2] delete" in hint.plain


# === §B — tool-result projection (project_tool_result) =======================


def test_project_tool_result_table_parses_json_list() -> None:
    env = _env("tool-renderer-desc", tool_name="grep", view="table")
    rows = DescriptorRenderer.project_tool_result(env, '[{"file": "a.py"}, {"file": "b.py"}]')
    assert rows == [{"file": "a.py"}, {"file": "b.py"}]


def test_project_tool_result_table_wraps_single_dict() -> None:
    env = _env("tool-renderer-desc", tool_name="t", view="form")
    rows = DescriptorRenderer.project_tool_result(env, '{"k": "v"}')
    assert rows == [{"k": "v"}]


def test_project_tool_result_rows_path_dotted_lookup() -> None:
    env = _env("tool-renderer-desc", tool_name="t", view="table", rows_path="data.items")
    rows = DescriptorRenderer.project_tool_result(env, '{"data": {"items": [{"n": 1}]}}')
    assert rows == [{"n": 1}]


def test_project_tool_result_text_view_uses_raw() -> None:
    env = _env("tool-renderer-desc", tool_name="t", view="text")
    rows = DescriptorRenderer.project_tool_result(env, "plain output")
    assert rows == ["plain output"]


def test_project_tool_result_text_path_extracts() -> None:
    env = _env("tool-renderer-desc", tool_name="t", view="text", text_path="message")
    rows = DescriptorRenderer.project_tool_result(env, '{"message": "hi there"}')
    assert rows == ["hi there"]


def test_project_tool_result_non_json_falls_back_to_raw() -> None:
    env = _env("tool-renderer-desc", tool_name="t", view="table")
    rows = DescriptorRenderer.project_tool_result(env, "not json at all")
    assert rows == ["not json at all"]


def test_project_tool_result_unresolved_rows_path_falls_back() -> None:
    env = _env("tool-renderer-desc", tool_name="t", view="table", rows_path="missing.key")
    rows = DescriptorRenderer.project_tool_result(env, '{"data": []}')
    assert rows == ["{\"data\": []}"]
