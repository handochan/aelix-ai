"""Shared fixtures for the built-in tool tests.

P0 #3 HEAVY (ADR-0139): grep/find now resolve ripgrep/fd via
:func:`aelix_coding_agent.util.tools_manager.ensure_tool`. The existing
grep/find behavior tests were written against the pure-Python fallback (rg/fd
absent), so force that path deterministically here — independent of whether a
system rg/fd happens to be on PATH — by stubbing ``ensure_tool`` (as imported
into the grep/find modules) to return ``None``. The rg/fd-backed path is locked
in separately by ``tests/tools/test_grep_tool.py::test_grep_rg_path_basename``
and the dedicated download tests in ``tests/util/test_tools_manager.py``.

The grep/find module **objects** are captured at import time and patched
directly (not via a ``"a.b.c"`` string), so the patch is immune to a prior test
reloading the ``aelix_coding_agent`` package (which would drop the ``tools``
attribute and break string-path resolution).
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.tools import find as _find_mod
from aelix_coding_agent.tools import grep as _grep_mod


@pytest.fixture(autouse=True)
def _force_python_grep_find(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_tool(_tool: str, silent: bool = True) -> None:
        return None

    monkeypatch.setattr(_grep_mod, "ensure_tool", _no_tool)
    monkeypatch.setattr(_find_mod, "ensure_tool", _no_tool)
