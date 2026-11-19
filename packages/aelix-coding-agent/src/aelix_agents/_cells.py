"""Terminal cells for the delegation surfaces, with or without the ``tui`` extra (#797).

``tool.py`` and ``panel.py`` bound what they write in terminal cells. They used to
import ``rich.cells`` and ``wcwidth`` at module top, and both packages come only with
the ``tui`` extra (``packages/aelix-coding-agent/pyproject.toml``), so on an install
without it ``import aelix_agents`` raised ``ModuleNotFoundError`` and the ``agent``
tool was never registered (#797: Aelix Studio's bundle, a plain
``pip install aelix-coding-agent`` driven over RPC). The owner's choice, 2026-11-18:
"import 의존 제거 (권장)" - no import dependency, and no new package in the default
install.

So each function here resolves its library on its FIRST CALL, not at import:

* with the extra installed, it IS ``rich.cells.cell_len`` / ``set_cell_size`` and
  ``wcwidth.wcwidth``, so the TUI measures exactly what it measured before
  (``tests/agents_ext/test_cells_without_tui_797.py`` pins the identity);
* without it, the standard library's reading in
  :mod:`aelix_coding_agent.jobs.cells` (#776, ADR-0299), whose ``cells`` counts
  never fewer cells than ``rich`` 13.7.1 to 14.3.4 on its corpus; the same test file
  compares the two over wide, combining, emoji, zero width joiner and control
  characters, against the ``rich`` this checkout installs.

This module imports neither library when it is imported. On an install without the
extra, ``import aelix_agents.tool`` therefore loads neither; with the extra it can
still load ``rich``, through ``httpx`` (``aelix_coding_agent.agents`` reaches it via
``cli/runtime_bootstrap.py`` and the provider modules), which is not this module's
import (verify of #797, F1: measured ``['rich']`` with the extra, ``[]`` without).
"""

from __future__ import annotations

from collections.abc import Callable

from aelix_coding_agent.jobs import cells as _std

_Impl = tuple[Callable[[str], int], Callable[[str, int], str], Callable[[str], int]]

_impl: _Impl | None = None
"""The three functions in use, once the first call has resolved them."""


def _std_set_cell_size(text: str, total: int) -> str:
    """``text`` in exactly ``total`` cells by :mod:`~aelix_coding_agent.jobs.cells`:
    cut before the first piece that does not fit, padded with spaces, as
    ``rich.cells.set_cell_size`` returns it. Never splits a piece a cut must keep."""

    if total <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for piece in _std.clusters(text):
        width = _std.cells(piece)
        if used + width > total:
            break
        kept.append(piece)
        used += width
    return "".join(kept) + " " * (total - used)


def _resolve() -> _Impl:
    global _impl
    try:
        from rich.cells import cell_len as rich_len
        from rich.cells import set_cell_size as rich_size
    except ImportError:
        rich_len, rich_size = _std.cells, _std_set_cell_size
    try:
        from wcwidth import wcwidth as char_width
    except ImportError:
        char_width = _std.char_cells
    _impl = (rich_len, rich_size, char_width)
    return _impl


def cell_len(text: str) -> int:
    """The cells ``text`` takes: ``rich.cells.cell_len`` when ``rich`` is installed."""

    return (_impl or _resolve())[0](text)


def set_cell_size(text: str, total: int) -> str:
    """``text`` cut or padded to ``total`` cells: ``rich.cells.set_cell_size`` when
    ``rich`` is installed."""

    return (_impl or _resolve())[1](text, total)


def wcwidth(char: str) -> int:
    """One character's width as prompt_toolkit reads it: ``wcwidth.wcwidth`` when
    ``wcwidth`` is installed (``-1`` for a control character there).

    Without it, :func:`~aelix_coding_agent.jobs.cells.char_cells`, which can read a
    character narrower than ``wcwidth`` does (a regional indicator: 1, not 2). That
    install has no prompt_toolkit, so no panel is drawn by it to clip."""

    return (_impl or _resolve())[2](char)


__all__ = ["cell_len", "set_cell_size", "wcwidth"]
