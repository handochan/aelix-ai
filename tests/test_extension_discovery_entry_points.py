"""Sprint 5a (Phase 3.1) — entry_points discovery (Aelix-additive per P-21).

Tests :func:`aelix_coding_agent.extensions.loader._discover_via_entry_points`
in isolation and the directory-vs-entry_points ordering invariant: a
factory registered via ``entry_points`` lands LAST so installed packages
never shadow project-local files.

UPDATED for issue #91 twice over. First: discovery is metadata-only now and
returns a CARRIER, and the import happens at load time in ``_resolve_factory``
(``ep.load()`` at discovery is gone). Second (this pass): the sys.path
PROVENANCE gate. A bare ``EntryPoint`` has ``dist is None``, which the gate
now REFUSES fail-closed — nothing proves the endpoint's code is installed into
this environment rather than committed into a hostile checkout. So the tests
that need a LOADABLE endpoint use a GENUINE installed-wheel image
(``install_dist``) written into a throwaway ``site/`` that stands in for
site-packages (declared trusted via the ``environment_site_dirs`` seam, exactly
as ``tests/extensions/test_ep_wiring.py`` does), and the one test that exercised
the dist-less shape now pins the fail-closed refusal it produces.
"""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from aelix_coding_agent.extensions import loader as L
from aelix_coding_agent.extensions.loader import (
    _discover_via_entry_points,
    discover_and_load_extensions,
)

from tests.extensions.test_ep_manifest import install_dist

GROUP = "aelix.extensions"


def _setup_src(flag: str) -> str:
    """A real importable module whose ``setup`` registers ``flag``."""

    return (
        f"def setup(aelix):\n"
        f"    aelix.register_flag({flag!r}, type='bool', default=True)\n"
    )


@pytest.fixture
def trusted_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway site-packages on ``sys.path``, DECLARED TRUSTED.

    ``entry_points()`` is scoped to it (so an ambient install cannot
    contaminate an exact-match assertion), and :func:`environment_site_dirs` is
    extended to include it — i.e. this directory stands in for a genuine
    ``pip install`` target, which is what makes its packs pass the provenance
    gate. Teardown drops any module imported out of it.
    """

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    sys.path.insert(0, str(site_dir))

    real_ep = importlib.metadata.entry_points

    def scoped(**kwargs: object) -> importlib.metadata.EntryPoints:
        found = real_ep(**kwargs)  # type: ignore[arg-type]
        return importlib.metadata.EntryPoints(
            ep
            for ep in found
            if str(
                getattr(ep, "dist", None) and ep.dist.locate_file("")
            ).startswith(str(site_dir))
        )

    monkeypatch.setattr(importlib.metadata, "entry_points", scoped)

    real_env = L.environment_site_dirs
    monkeypatch.setattr(
        L, "environment_site_dirs", lambda: real_env() | frozenset({site_dir.resolve()})
    )
    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))
        root = str(site_dir)
        for name, mod in list(sys.modules.items()):
            origin = getattr(mod, "__file__", None) or ""
            if origin.startswith(root):
                del sys.modules[name]


def test_dist_less_entry_point_is_refused_fail_closed() -> None:
    """Was ``…returns_carriers_without_importing``. A bare ``EntryPoint`` has no
    owning distribution, so the provenance gate cannot prove its code is
    installed — it is REFUSED fail-closed (issue #91), not degraded to a
    manifest-less load. No carrier, and the reason names the missing dist."""

    eps = [EntryPoint("ext_a", "ep_mod_a:setup", GROUP)]
    original = importlib.metadata.entry_points
    try:
        importlib.metadata.entry_points = lambda *a, **k: eps  # type: ignore[assignment]
        out = _discover_via_entry_points(set())
    finally:
        importlib.metadata.entry_points = original  # type: ignore[assignment]

    assert len(out) == 1
    entry, error = out[0]
    assert entry is None  # fail-closed: nothing loadable is produced
    assert error is not None
    assert error.path == "entry_point:ext_a"
    assert "untrusted sys.path provenance" in error.error
    assert "no owning distribution" in error.error


async def test_one_broken_endpoint_never_aborts_the_wave(
    trusted_site: Path,
) -> None:
    """A good installed pack still loads even when a sibling endpoint cannot be
    imported. The good and bad packs are BOTH genuinely installed (they pass
    provenance); the bad one names a module that does not exist, so it fails at
    LOAD time — after discovery — and the good pack is unaffected."""

    install_dist(
        trusted_site,
        "okdist",
        entry_points={GROUP: {"ext_a": "ep_mod_ok:setup"}},
        files={"ep_mod_ok.py": _setup_src("ep_a")},
    )
    # A real, trusted dist whose entry names a module it never ships.
    install_dist(
        trusted_site,
        "baddist",
        entry_points={GROUP: {"ext_b": "ep_mod_missing:setup"}},
        files={"baddist_marker.py": "# nothing importable named ep_mod_missing\n"},
    )

    result = await discover_and_load_extensions(
        [], cwd=trusted_site / "proj", agent_dir=trusted_site / "no_global"
    )

    flags = {f for ext in result.extensions for f in ext.flags}
    assert "ep_a" in flags  # the good pack loaded
    failures = [e.error for e in result.errors if e.path == "entry_point:ext_b"]
    # Discovery notice (no manifest could be proved) then the load-time import
    # failure. Neither stops the good pack.
    assert any("No module named 'ep_mod_missing'" in f for f in failures), failures


def test_discover_via_entry_points_dedups_by_module_and_attr(
    trusted_site: Path,
) -> None:
    """Dedup key is the ``module:attr`` TARGET, not ``name=value``: two trusted
    distributions may spell the same factory under different endpoint names,
    and loading it twice would run ``setup()`` twice on one runtime."""

    install_dist(
        trusted_site,
        "twina",
        entry_points={GROUP: {"ext_a": "ep_mod_a:setup"}},
        files={"ep_mod_a.py": _setup_src("twin_flag")},
    )
    install_dist(
        trusted_site,
        "twinb",
        entry_points={GROUP: {"ext_a_alias": "ep_mod_a:setup"}},
        files={"twinb_marker.py": "# alias dist; same module:attr target\n"},
    )

    out = _discover_via_entry_points(set())

    carriers = [e for e, _err in out if e is not None]
    assert carriers != []
    assert len(carriers) == 1  # one target => one carrier


async def test_discover_directory_wins_over_entry_points(
    trusted_site: Path,
) -> None:
    """P-21: project-local directory must load BEFORE entry_points."""

    install_dist(
        trusted_site,
        "remotedist",
        entry_points={GROUP: {"ext_remote": "ep_mod_remote:setup"}},
        files={"ep_mod_remote.py": _setup_src("from_ep")},
    )

    cwd = trusted_site / "proj"
    local = cwd / ".aelix" / "extensions" / "ext.py"
    local.parent.mkdir(parents=True)
    local.write_text(_setup_src("from_local"))

    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=trusted_site / "no_global"
    )

    flags = {f for ext in result.extensions for f in ext.flags}
    assert {"from_local", "from_ep"} <= flags
    # Directory entries always come before entry_points entries.
    flag_order: list[str] = []
    for ext in result.extensions:
        flag_order.extend(ext.flags.keys())
    assert flag_order.index("from_local") < flag_order.index("from_ep")


async def test_discover_no_entry_points_silently_continues(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    original = importlib.metadata.entry_points
    try:
        importlib.metadata.entry_points = lambda *a, **k: []  # type: ignore[assignment]
        result = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=tmp_path / "no_global"
        )
    finally:
        importlib.metadata.entry_points = original  # type: ignore[assignment]
    assert result.extensions == []
    assert result.errors == []
