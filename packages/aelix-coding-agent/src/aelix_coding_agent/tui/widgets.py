"""Sprint 6h₁₀b §B — Concrete TUI widget implementations.

Three concrete classes that satisfy the ``Component`` / ``Container`` Protocols
defined in :mod:`aelix_coding_agent.extensions.widget_protocols`:

- :class:`LinesComponent` — static list-of-strings display widget.
- :class:`RichComponent` — wraps any Rich renderable; renders to ANSI lines.
- :class:`VStack` — vertical container that concatenates child renders.

All three implement ``handle_input`` and ``invalidate`` as no-ops (or
forwarding stubs) so they satisfy the ``@runtime_checkable Component``
Protocol without requiring focus / cache machinery in this sprint.
"""

from __future__ import annotations

import io

from rich.console import Console

from aelix_coding_agent.extensions.widget_protocols import Component


class LinesComponent:
    """Static list-of-strings :class:`~widget_protocols.Component`.

    :param lines: initial list of ANSI/plain text lines to display.

    ``render(width)`` returns a shallow copy of the current lines list.
    ``set_lines(lines)`` replaces the stored lines in-place.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines: list[str] = list(lines)

    def set_lines(self, lines: list[str]) -> None:
        """Replace stored lines."""
        self._lines = list(lines)

    def render(self, width: int) -> list[str]:  # noqa: ARG002
        return list(self._lines)

    def handle_input(self, data: str) -> None:  # noqa: ARG002
        pass

    def invalidate(self) -> None:
        pass


class RichComponent:
    """Rich-renderable :class:`~widget_protocols.Component`.

    :param renderable: any object accepted by :meth:`rich.console.Console.print`
        (e.g. :class:`rich.text.Text`, :class:`rich.panel.Panel`, plain ``str``).

    ``render(width)`` creates a temporary ``StringIO``-backed console at the
    requested *width*, prints the renderable, and returns the ANSI output split
    into lines.
    """

    def __init__(self, renderable: object) -> None:
        self._renderable = renderable

    def render(self, width: int) -> list[str]:
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, width=width)
        console.print(self._renderable, end="")
        return buf.getvalue().splitlines()

    def handle_input(self, data: str) -> None:  # noqa: ARG002
        pass

    def invalidate(self) -> None:
        pass


class VStack:
    """Vertical :class:`~widget_protocols.Container`.

    Children are stored in insertion order. ``render(width)`` concatenates
    each child's ``render(width)`` output top-to-bottom.

    ``invalidate()`` forwards to all children so cached render state is
    cleared transitively.
    """

    def __init__(self) -> None:
        self._children: list[Component] = []

    # --- Container interface -------------------------------------------------

    def add_child(self, child: Component) -> None:
        self._children.append(child)

    def remove_child(self, child: Component) -> None:
        self._children.remove(child)

    def clear(self) -> None:
        self._children.clear()

    # --- Component interface -------------------------------------------------

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self._children:
            lines.extend(child.render(width))
        return lines

    def handle_input(self, data: str) -> None:  # noqa: ARG002
        pass

    def invalidate(self) -> None:
        for child in self._children:
            child.invalidate()


__all__ = ["LinesComponent", "RichComponent", "VStack"]
