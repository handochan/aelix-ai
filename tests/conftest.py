"""Session-wide test guards.

P0 #3 HEAVY (ADR-0139): ``grep``/``find`` now call
:func:`aelix_coding_agent.util.tools_manager.ensure_tool`, which may download
ripgrep/fd from GitHub when the binary is absent. To keep the whole suite
hermetic (no network, no rate-limit flakiness) and to avoid polluting
``~/.aelix/agent/bin``, this autouse fixture:

- redirects the tool bin dir to a clean per-session temp dir, and
- makes the network primitives raise, so any real download attempt fails fast
  (``ensure_tool`` then returns ``None`` and the caller uses its fallback).

Tests that exercise the download path itself
(``tests/util/test_tools_manager.py``) re-stub these primitives with working
mocks inside the test body, which override this autouse setup (same
function-scoped ``monkeypatch``, last write wins).
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.util import tools_manager as _tm


@pytest.fixture(autouse=True)
def _no_real_tool_downloads(tmp_path_factory, monkeypatch):
    bin_dir = tmp_path_factory.mktemp("aelix_bin")
    monkeypatch.setattr(_tm, "_bin_dir", lambda: str(bin_dir))

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(_tm, "_get_latest_version", _blocked)
    monkeypatch.setattr(_tm, "_download_file", _blocked)
