"""Sprint 5a (Phase 3.1) — entry_points discovery (Aelix-additive per P-21).

Tests :func:`aelix_coding_agent.extensions.loader._discover_via_entry_points`
in isolation and the directory-vs-entry_points ordering invariant: a
factory registered via ``entry_points`` lands LAST so installed packages
never shadow project-local files.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_coding_agent.extensions.loader import (
    _discover_via_entry_points,
    discover_and_load_extensions,
)


class _FakeEntryPoint:
    def __init__(self, name: str, factory: object, *, fail: bool = False) -> None:
        self.name = name
        self.value = f"fake:{name}"
        self._factory = factory
        self._fail = fail

    def load(self) -> object:
        if self._fail:
            raise ImportError(f"fake-fail-{self.name}")
        return self._factory


def _ep_factory(flag: str):
    def setup(aelix: ExtensionAPI) -> None:
        aelix.register_flag(flag, type="bool", default=True)

    return setup


def test_discover_via_entry_points_returns_loaded_factories() -> None:
    eps = [_FakeEntryPoint("ext_a", _ep_factory("ep_a"))]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        out = _discover_via_entry_points(set())
    assert len(out) == 1
    factory, error = out[0]
    assert error is None
    assert factory is not None


def test_discover_via_entry_points_contains_failures() -> None:
    eps = [
        _FakeEntryPoint("ext_a", _ep_factory("ep_a")),
        _FakeEntryPoint("ext_b", object(), fail=True),
    ]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        out = _discover_via_entry_points(set())
    assert len(out) == 2
    # First entry succeeded.
    assert out[0][1] is None
    # Second entry collected an error and did NOT abort the wave.
    assert out[1][0] is None
    assert out[1][1] is not None
    assert "fake-fail-ext_b" in out[1][1].error


def test_discover_via_entry_points_dedups_by_ep_key() -> None:
    eps = [
        _FakeEntryPoint("ext_a", _ep_factory("ep_a")),
        _FakeEntryPoint("ext_a", _ep_factory("ep_a")),  # duplicate
    ]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        out = _discover_via_entry_points(set())
    assert len(out) == 1


async def test_discover_directory_wins_over_entry_points(tmp_path: Path) -> None:
    """P-21: project-local directory must load BEFORE entry_points."""

    cwd = tmp_path / "proj"
    local = cwd / ".aelix" / "extensions" / "ext.py"
    local.parent.mkdir(parents=True)
    local.write_text(
        textwrap.dedent(
            """\
            def setup(aelix):
                aelix.register_flag("from_local", type="bool", default=True)
            """
        )
    )

    eps = [_FakeEntryPoint("ext_remote", _ep_factory("from_ep"))]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        result = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=tmp_path / "no_global"
        )

    assert len(result.errors) == 0
    # Both should load (no shadowing — they have distinct flag names).
    flags = {f for ext in result.extensions for f in ext.flags}
    assert {"from_local", "from_ep"} <= flags
    # Directory entries always come before entry_points entries.
    flag_order = []
    for ext in result.extensions:
        flag_order.extend(ext.flags.keys())
    assert flag_order.index("from_local") < flag_order.index("from_ep")


async def test_discover_no_entry_points_silently_continues(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=[],
    ):
        result = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=tmp_path / "no_global"
        )
    assert result.extensions == []
    assert result.errors == []
