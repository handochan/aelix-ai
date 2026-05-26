"""Sprint 6h₁₀b §B — Widget unit tests.

Covers :class:`LinesComponent`, :class:`RichComponent`, and :class:`VStack`
against the :class:`~widget_protocols.Component` /
:class:`~widget_protocols.Container` Protocol contracts.
"""

from __future__ import annotations

from aelix_coding_agent.extensions.widget_protocols import Component, Container
from aelix_coding_agent.tui.widgets import LinesComponent, RichComponent, VStack
from rich.text import Text

# === LinesComponent ==========================================================


def test_lines_component_render_returns_copy() -> None:
    lc = LinesComponent(["a", "b"])
    result = lc.render(80)
    assert result == ["a", "b"]
    # Must be a copy, not the same list object.
    result.append("c")
    assert lc.render(80) == ["a", "b"]


def test_lines_component_render_empty() -> None:
    assert LinesComponent([]).render(80) == []


def test_lines_component_set_lines() -> None:
    lc = LinesComponent(["x"])
    lc.set_lines(["p", "q", "r"])
    assert lc.render(80) == ["p", "q", "r"]


def test_lines_component_handle_input_noop() -> None:
    lc = LinesComponent(["a"])
    lc.handle_input("anything")  # must not raise


def test_lines_component_invalidate_noop() -> None:
    lc = LinesComponent(["a"])
    lc.invalidate()  # must not raise


def test_lines_component_satisfies_component_protocol() -> None:
    assert isinstance(LinesComponent([]), Component)


# === RichComponent ===========================================================


def test_rich_component_render_contains_text() -> None:
    rc = RichComponent(Text("hello"))
    lines = rc.render(80)
    assert len(lines) >= 1
    combined = "\n".join(lines)
    assert "hello" in combined


def test_rich_component_render_plain_string() -> None:
    rc = RichComponent("world")
    lines = rc.render(80)
    assert any("world" in line for line in lines)


def test_rich_component_width_respected() -> None:
    # A string that fits in 80 chars must produce at most 1 line at width=80.
    rc = RichComponent(Text("short"))
    assert len(rc.render(80)) == 1


def test_rich_component_handle_input_noop() -> None:
    rc = RichComponent(Text("x"))
    rc.handle_input("key")  # must not raise


def test_rich_component_invalidate_noop() -> None:
    rc = RichComponent(Text("x"))
    rc.invalidate()  # must not raise


def test_rich_component_satisfies_component_protocol() -> None:
    assert isinstance(RichComponent(Text("hi")), Component)


# === VStack ==================================================================


def test_vstack_empty_render() -> None:
    assert VStack().render(80) == []


def test_vstack_single_child() -> None:
    vs = VStack()
    vs.add_child(LinesComponent(["line1", "line2"]))
    assert vs.render(80) == ["line1", "line2"]


def test_vstack_two_children_concatenates() -> None:
    vs = VStack()
    vs.add_child(LinesComponent(["a", "b"]))
    vs.add_child(LinesComponent(["c"]))
    assert vs.render(80) == ["a", "b", "c"]


def test_vstack_add_remove_child() -> None:
    vs = VStack()
    child = LinesComponent(["x"])
    vs.add_child(child)
    assert vs.render(80) == ["x"]
    vs.remove_child(child)
    assert vs.render(80) == []


def test_vstack_clear() -> None:
    vs = VStack()
    vs.add_child(LinesComponent(["a"]))
    vs.add_child(LinesComponent(["b"]))
    vs.clear()
    assert vs.render(80) == []


def test_vstack_invalidate_forwards_to_children() -> None:
    """VStack.invalidate() must call invalidate() on each child without raising."""

    class TrackingComponent:
        invalidated: int = 0

        def render(self, width: int) -> list[str]:
            return []

        def handle_input(self, data: str) -> None:
            pass

        def invalidate(self) -> None:
            self.invalidated += 1

    tc1 = TrackingComponent()
    tc2 = TrackingComponent()
    vs = VStack()
    vs.add_child(tc1)  # type: ignore[arg-type]
    vs.add_child(tc2)  # type: ignore[arg-type]
    vs.invalidate()
    assert tc1.invalidated == 1
    assert tc2.invalidated == 1


def test_vstack_satisfies_container_protocol() -> None:
    assert isinstance(VStack(), Container)


def test_vstack_satisfies_component_protocol() -> None:
    assert isinstance(VStack(), Component)


def test_vstack_handle_input_noop() -> None:
    vs = VStack()
    vs.handle_input("data")  # must not raise
