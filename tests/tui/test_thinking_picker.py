"""Unit tests for the /thinking picker helpers (Sprint 6h₂₇, ADR-0155, WP-7)."""

from __future__ import annotations

from typing import Any

from aelix_coding_agent.tui.thinking_picker import (
    run_thinking_picker,
    thinking_picker_labels,
)


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


# === thinking_picker_labels (PURE) =========================================


def test_labels_numbered_and_mark_current() -> None:
    labels = thinking_picker_labels(["off", "low", "high"], "low")
    assert labels == ["1. off", "✱ 2. low", "3. high"]
    # The "N." prefix keeps labels unique → lossless index recovery.
    assert len(set(labels)) == 3


def test_labels_no_current_match_marks_nothing() -> None:
    labels = thinking_picker_labels(["off", "high"], "medium")
    assert not any(label.startswith("✱") for label in labels)


# === run_thinking_picker flow (DI) =========================================


class _Model:
    def __init__(
        self, *, reasoning: bool = True, thinking_level_map: dict | None = None
    ) -> None:
        self.reasoning = reasoning
        # get_supported_thinking_levels iterates EXTENDED_THINKING_LEVELS and
        # consults this map; {} → all of off/minimal/low/medium/high (xhigh is
        # excluded when its key is absent — Pi parity).
        self.thinking_level_map = thinking_level_map if thinking_level_map is not None else {}


class _State:
    def __init__(self, thinking_level: str | None) -> None:
        self.thinking_level = thinking_level


class _Harness:
    def __init__(
        self,
        *,
        model: _Model | None,
        level: str | None = None,
        fail_set: bool = False,
    ) -> None:
        self.current_model = model
        self._state = _State(level)
        self.set_calls: list[str] = []
        self._fail = fail_set

    async def set_thinking_level(self, level: str) -> None:
        if self._fail:
            raise RuntimeError("switch failed")
        self.set_calls.append(level)
        self._state.thinking_level = level


async def _select_unreachable(*_a: Any, **_k: Any) -> str | None:
    raise AssertionError("select must not be called on this path")


async def test_run_switches_to_selected_level() -> None:
    harness = _Harness(model=_Model(), level="low")
    committed: list[object] = []
    captured: dict[str, Any] = {}

    async def select(title: str, options: list[str]) -> str | None:
        captured["options"] = options
        # choose the "high" row (last in the off/low/medium/high set)
        return next(o for o in options if o.endswith("high"))

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == ["high"]
    assert any("thinking →" in _plain(c) for c in committed)
    # the current level (low) is marked in the offered options.
    assert any(o.startswith("✱") and o.endswith("low") for o in captured["options"])


async def test_run_cancel_does_not_switch() -> None:
    harness = _Harness(model=_Model(), level="low")
    committed: list[object] = []

    async def select(title: str, options: list[str]) -> str | None:
        return None  # user pressed Esc

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == []
    assert committed == []  # no error commit on a clean cancel


async def test_run_non_reasoning_model_degrades() -> None:
    harness = _Harness(model=_Model(reasoning=False))
    committed: list[object] = []
    await run_thinking_picker(
        harness=harness, select=_select_unreachable, commit=committed.append
    )
    assert harness.set_calls == []
    assert any("no thinking levels" in _plain(c) for c in committed)


async def test_run_no_current_model_unavailable() -> None:
    harness = _Harness(model=None)
    committed: list[object] = []
    await run_thinking_picker(
        harness=harness, select=_select_unreachable, commit=committed.append
    )
    assert harness.set_calls == []
    assert any("unavailable" in _plain(c) for c in committed)


async def test_run_switch_failure_surfaced() -> None:
    harness = _Harness(model=_Model(), level="low", fail_set=True)
    committed: list[object] = []

    async def select(title: str, options: list[str]) -> str | None:
        return options[0]

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == []
    assert any("thinking switch failed" in _plain(c) for c in committed)
