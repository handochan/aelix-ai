"""Issue #11 — env-driven tool timeout options (entry.py wiring).

``_tool_options_from_env`` translates ``AELIX_BASH_DEFAULT_TIMEOUT`` /
``AELIX_BASH_MAX_TIMEOUT`` / ``AELIX_TOOL_SEARCH_TIMEOUT`` into the per-tool
``options`` dict passed to ``create_all_tools``. Only configured knobs are
included so each tool keeps its own module default otherwise.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.cli.entry import _env_float, _tool_options_from_env

_ENV_KEYS = (
    "AELIX_BASH_DEFAULT_TIMEOUT",
    "AELIX_BASH_MAX_TIMEOUT",
    "AELIX_TOOL_SEARCH_TIMEOUT",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_no_env_yields_empty_options() -> None:
    assert _tool_options_from_env() == {}


def test_bash_default_and_max_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_BASH_DEFAULT_TIMEOUT", "120")
    monkeypatch.setenv("AELIX_BASH_MAX_TIMEOUT", "1800")
    opts = _tool_options_from_env()
    assert opts["bash"] == {"default_timeout": 120.0, "max_timeout": 1800.0}
    assert "grep" not in opts and "find" not in opts


def test_search_timeout_wires_both_grep_and_find(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AELIX_TOOL_SEARCH_TIMEOUT", "90")
    opts = _tool_options_from_env()
    assert opts["grep"] == {"timeout": 90.0}
    assert opts["find"] == {"timeout": 90.0}


def test_zero_values_pass_through_for_disable_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 0 is a meaningful value for bash knobs (disable default / lift cap), so it
    # must be forwarded — not dropped.
    monkeypatch.setenv("AELIX_BASH_DEFAULT_TIMEOUT", "0")
    monkeypatch.setenv("AELIX_BASH_MAX_TIMEOUT", "0")
    opts = _tool_options_from_env()
    assert opts["bash"] == {"default_timeout": 0.0, "max_timeout": 0.0}


def test_search_timeout_zero_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 0/negative search timeout has no disable semantics (search can't hang
    # the loop) — it is ignored so the 30s default stands.
    monkeypatch.setenv("AELIX_TOOL_SEARCH_TIMEOUT", "0")
    assert "grep" not in _tool_options_from_env()


def test_unparseable_value_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_BASH_DEFAULT_TIMEOUT", "not-a-number")
    monkeypatch.setenv("AELIX_BASH_MAX_TIMEOUT", "600")
    opts = _tool_options_from_env()
    assert opts["bash"] == {"max_timeout": 600.0}


def test_env_float_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_BASH_DEFAULT_TIMEOUT", "-5")
    assert _env_float("AELIX_BASH_DEFAULT_TIMEOUT") is None
