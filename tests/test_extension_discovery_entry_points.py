"""Sprint 5a (Phase 3.1) — entry_points discovery (Aelix-additive per P-21).

Tests :func:`aelix_coding_agent.extensions.loader._discover_via_entry_points`
in isolation and the directory-vs-entry_points ordering invariant: a
factory registered via ``entry_points`` lands LAST so installed packages
never shadow project-local files.

UPDATED for issue #91. This module used to assert that discovery RETURNED A
LOADED FACTORY — i.e. that ``_discover_via_entry_points`` had already called
``ep.load()`` and therefore already imported (and run the module-level code
of) every installed pack, before any capability gate had seen its manifest.
That call is deleted: discovery is metadata-only now and returns a CARRIER,
and the import happens at load time in ``_resolve_factory``. The old
assertions encoded the defect, so they are rewritten rather than deleted —
each one keeps testing the property it was really about (a factory reaches
the loader / one bad endpoint never aborts the wave / directories win over
endpoints), just through the new shape.

The stubs are real ``importlib.metadata.EntryPoint`` objects rather than a
hand-rolled fake with a ``.load()`` method: after #91 the loader reads
``ep.module`` / ``ep.attr`` / ``ep.dist``, so a fake that only implements
``load()`` would test nothing that exists. A bare ``EntryPoint`` has
``dist is None``, which is exactly the UNPROVEN case — the pack loads
manifest-less and says why.
"""

from __future__ import annotations

import textwrap
from importlib.metadata import EntryPoint
from pathlib import Path
from unittest.mock import patch

import pytest
from aelix_coding_agent.extensions.loader import (
    _discover_via_entry_points,
    _EntryPointEntry,
    discover_and_load_extensions,
)

GROUP = "aelix.extensions"


def _write_ep_module(tmp_path: Path, module: str, flag: str) -> None:
    """A real importable module whose ``setup`` registers ``flag``."""

    (tmp_path / f"{module}.py").write_text(
        textwrap.dedent(
            f"""\
            def setup(aelix):
                aelix.register_flag({flag!r}, type="bool", default=True)
            """
        )
    )


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name, value, GROUP)


def test_discover_via_entry_points_returns_carriers_without_importing() -> None:
    """Was ``…returns_loaded_factories``. Discovery no longer loads anything:
    it returns the endpoint's target as data, plus the reason its manifest
    could not be proved (no ``dist`` on a bare EntryPoint)."""

    eps = [_ep("ext_a", "ep_mod_a:setup")]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        out = _discover_via_entry_points(set())

    assert len(out) == 1
    entry, error = out[0]
    assert isinstance(entry, _EntryPointEntry)
    assert (entry.module, entry.attr) == ("ep_mod_a", "setup")
    # Degrade-with-a-reason (issue #91 decision i), not a silent inert install.
    assert error is not None
    assert "unproven" in error.error


async def test_one_broken_endpoint_never_aborts_the_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ``…contains_failures``, which simulated ``ep.load()`` raising at
    DISCOVERY. The failure it stood for — an endpoint that cannot be imported
    — now surfaces at LOAD time, so the test follows it there: the good pack
    still loads and the bad one is reported, not fatal."""

    monkeypatch.syspath_prepend(str(tmp_path))
    _write_ep_module(tmp_path, "ep_mod_ok", "ep_a")

    eps = [_ep("ext_a", "ep_mod_ok:setup"), _ep("ext_b", "ep_mod_missing:setup")]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        result = await discover_and_load_extensions(
            [], cwd=tmp_path / "proj", agent_dir=tmp_path / "no_global"
        )

    flags = {f for ext in result.extensions for f in ext.flags}
    assert "ep_a" in flags
    # Two reports for the bad endpoint, in the order they happened: the
    # discovery-time "no manifest could be proved" notice, then the load-time
    # import failure. Neither stops the good pack.
    failures = [e.error for e in result.errors if e.path == "entry_point:ext_b"]
    assert len(failures) == 2, [(e.path, e.error) for e in result.errors]
    assert "loaded WITHOUT its manifest" in failures[0]
    assert "No module named 'ep_mod_missing'" in failures[1]


def test_discover_via_entry_points_dedups_by_module_and_attr() -> None:
    """Dedup key moved from ``name=value`` to the ``module:attr`` TARGET:
    two distributions may spell the same factory under different endpoint
    names, and loading it twice runs ``setup()`` twice on one runtime."""

    eps = [_ep("ext_a", "ep_mod_a:setup"), _ep("ext_a_alias", "ep_mod_a:setup")]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        out = _discover_via_entry_points(set())

    assert [e for e, _err in out if e is not None] != []
    assert len([e for e, _err in out if isinstance(e, _EntryPointEntry)]) == 1


async def test_discover_directory_wins_over_entry_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-21: project-local directory must load BEFORE entry_points."""

    monkeypatch.syspath_prepend(str(tmp_path))
    _write_ep_module(tmp_path, "ep_mod_remote", "from_ep")

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

    eps = [_ep("ext_remote", "ep_mod_remote:setup")]
    with patch(
        "aelix_coding_agent.extensions.loader.importlib.metadata.entry_points",
        return_value=eps,
    ):
        result = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=tmp_path / "no_global"
        )

    # Both should load (no shadowing — they have distinct flag names). The
    # endpoint pack now also carries a "loaded WITHOUT its manifest" notice:
    # a bare EntryPoint has no distribution, so nothing proves where its files
    # live. That is a REPORT, not a failure — the pack still loads.
    assert [e.path for e in result.errors] == ["entry_point:ext_remote"]
    assert "loaded WITHOUT its manifest" in result.errors[0].error
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
