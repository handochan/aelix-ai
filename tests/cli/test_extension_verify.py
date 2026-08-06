"""Issue #91 — ``aelix extension verify`` (the import-free BOUND gate) and the
``extension list`` manifest-binding annotation (ADR-0207).

The synthetic packs here are GENUINE installed-wheel images built by
``tests.extensions.test_ep_manifest.install_dist`` — a real ``dist-info`` with a
real ``RECORD`` written into a throwaway ``site/`` on ``sys.path``. That is what
gives ``importlib.metadata`` a real ``Distribution`` whose ``locate_file("")``
resolves inside the directory we declare trusted, so the packs pass the sys.path
provenance gate exactly as a ``pip install`` target would.

Two properties are pinned that the whole feature rests on:

* verify NEVER imports the pack (asserted with the import-marker fixture, whose
  positive control lives in ``test_ep_manifest`` — the marker mechanism is proven
  to fire, so the "no marker" assertion here is not vacuous), and
* the exit codes are stable: 0 iff every reported endpoint is BOUND, 1 for any
  non-BOUND, 2 for a usage error or a named target matching nothing.
"""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.cli import extension_install as EI
from aelix_coding_agent.extensions import loader as L
from aelix_coding_agent.extensions.ep_manifest import EpOutcome

from tests.extensions.test_ep_manifest import (
    EXT_PY,
    MANIFEST,
    install_dist,
    manifest_toml,
)


@pytest.fixture
def trusted_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway site-packages on ``sys.path``, DECLARED TRUSTED.

    ``entry_points()`` is scoped to it so an ambient install cannot contaminate
    an exact-count assertion, and ``environment_site_dirs`` is extended on BOTH
    the CLI classifier's module and the loader's, so a pack installed here passes
    the provenance gate — i.e. this directory stands in for a real ``pip
    install`` target. The import-marker directory is wired the same way
    ``test_ep_manifest`` does, so "we did not import it" is provable by the
    absence of a file.
    """

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()
    monkeypatch.setenv("EP91_MARKERS", str(markers))
    sys.path.insert(0, str(site_dir))

    real_ep = importlib.metadata.entry_points

    def scoped(**kwargs: object) -> importlib.metadata.EntryPoints:
        found = real_ep(**kwargs)  # type: ignore[arg-type]
        return importlib.metadata.EntryPoints(
            ep
            for ep in found
            if str(getattr(ep, "dist", None) and ep.dist.locate_file("")).startswith(
                str(site_dir)
            )
        )

    monkeypatch.setattr(importlib.metadata, "entry_points", scoped)

    real_env = EI.environment_site_dirs
    trusted = lambda: real_env() | frozenset({site_dir.resolve()})  # noqa: E731
    monkeypatch.setattr(EI, "environment_site_dirs", trusted)
    monkeypatch.setattr(L, "environment_site_dirs", trusted)

    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))
        root = str(site_dir)
        for name, mod in list(sys.modules.items()):
            origin = getattr(mod, "__file__", None) or ""
            if origin.startswith(root):
                del sys.modules[name]


def _markers(site_dir: Path) -> Path:
    return site_dir.parent / "markers"


# --- pack shapes -----------------------------------------------------------


def _install_bound(site: Path, name: str = "boundpack", ep: str = "bound") -> None:
    install_dist(
        site,
        name,
        entry_points={"aelix.extensions": {ep: f"{name}.ext:setup"}},
        files={
            f"{name}/__init__.py": "",
            f"{name}/ext.py": EXT_PY.format(name=name),
            f"{name}/{MANIFEST}": manifest_toml(name, entry=f"{name}.ext:setup"),
        },
    )


def _install_setuptools_dropped_manifest(site: Path) -> None:
    """The footgun: a setuptools DEFAULT build ships the code but DROPS the
    manifest, so RECORD lists the package's files and no ``aelix-plugin.toml``.
    That is precisely the ABSENT shape.
    """

    install_dist(
        site,
        "droppedpack",
        entry_points={"aelix.extensions": {"dropped": "droppedpack.ext:setup"}},
        files={
            "droppedpack/__init__.py": "",
            "droppedpack/ext.py": EXT_PY.format(name="droppedpack"),
        },
    )


def _install_malformed(site: Path) -> None:
    install_dist(
        site,
        "badtomlpack",
        entry_points={"aelix.extensions": {"badtoml": "badtomlpack.ext:setup"}},
        files={
            "badtomlpack/__init__.py": "",
            "badtomlpack/ext.py": EXT_PY.format(name="badtomlpack"),
            f"badtomlpack/{MANIFEST}": "[plugin\nid = broken",
        },
    )


# --- verify exit codes -----------------------------------------------------


def test_verify_bound_wheel_exits_zero(trusted_site: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _install_bound(trusted_site)

    rc = EI.run_extension_command(["verify"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "BOUND" in out
    assert "all 1 endpoint(s) BOUND" in out


def test_verify_setuptools_dropped_manifest_exits_nonzero_with_hint(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_setuptools_dropped_manifest(trusted_site)

    rc = EI.run_extension_command(["verify"])

    assert rc == EI._VERIFY_NOT_BOUND
    combined = capsys.readouterr()
    text = combined.out + combined.err
    assert "ABSENT" in text
    # The hint MUST name the setuptools / package-data cause — that is what turns
    # a silent inert install into an actionable failure.
    assert "setuptools" in text
    assert "package-data" in text
    assert "hatchling" in text


def test_verify_malformed_exits_nonzero(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_malformed(trusted_site)

    rc = EI.run_extension_command(["verify"])

    assert rc == EI._VERIFY_NOT_BOUND
    text = capsys.readouterr().out
    assert "MALFORMED" in text


def test_verify_mixed_is_nonzero_when_any_endpoint_is_not_bound(
    trusted_site: Path,
) -> None:
    _install_bound(trusted_site)
    _install_setuptools_dropped_manifest(trusted_site)

    rc = EI.run_extension_command(["verify"])

    assert rc == EI._VERIFY_NOT_BOUND


def test_verify_named_target_filters_to_one(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_bound(trusted_site)
    _install_setuptools_dropped_manifest(trusted_site)

    # Verify ONLY the bound pack (by entry-point name) — the dropped one must not
    # drag the exit code down when it is not the target.
    rc = EI.run_extension_command(["verify", "bound"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "boundpack" in out
    assert "droppedpack" not in out


def test_verify_named_target_not_installed_is_usage_error(trusted_site: Path) -> None:
    _install_bound(trusted_site)

    rc = EI.run_extension_command(["verify", "nope-not-here"])

    assert rc == EI._EXIT_DIDNT_RUN


def test_verify_unknown_flag_is_usage_error(trusted_site: Path) -> None:
    rc = EI.run_extension_command(["verify", "--frobnicate"])

    assert rc == EI._EXIT_DIDNT_RUN


def test_verify_no_endpoints_is_zero(trusted_site: Path) -> None:
    # Nothing installed into the scoped site → nothing to verify, vacuously OK.
    rc = EI.run_extension_command(["verify"])

    assert rc == 0


# --- THE headline: verify never imports the pack ---------------------------


def test_verify_never_imports_the_pack(trusted_site: Path) -> None:
    """verify runs the metadata-only resolver — it must not import plugin code.

    Each pack writes an import marker at MODULE TOP LEVEL;
    ``test_ep_manifest.test_import_marker_control_actually_fires`` proves that
    marker fires on a real import, so an absent marker here is a real negative.
    """

    _install_bound(trusted_site)
    _install_setuptools_dropped_manifest(trusted_site)
    _install_malformed(trusted_site)
    before = set(sys.modules)

    EI.run_extension_command(["verify"])

    gained = {m for m in set(sys.modules) - before if m.split(".")[0] in {
        "boundpack", "droppedpack", "badtomlpack"
    }}
    assert gained == set(), f"verify imported plugin modules: {sorted(gained)}"
    markers = _markers(trusted_site)
    assert not list(markers.glob("*.imported")), "verify imported a pack (marker written)"


# --- list annotation -------------------------------------------------------


def test_list_annotates_absent_pack_as_inert(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_setuptools_dropped_manifest(trusted_site)

    rc = EI.run_extension_command(["list"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "droppedpack" in out
    assert "no manifest" in out
    assert "inert" in out


def test_list_shows_bound_pack_without_an_inert_note(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_bound(trusted_site)

    rc = EI.run_extension_command(["list"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "boundpack" in out
    assert "inert" not in out


def test_list_does_not_import_the_pack(trusted_site: Path) -> None:
    _install_setuptools_dropped_manifest(trusted_site)
    before = set(sys.modules)

    EI.run_extension_command(["list"])

    gained = {m for m in set(sys.modules) - before if m.split(".")[0] == "droppedpack"}
    assert gained == set()
    assert not list(_markers(trusted_site).glob("*.imported"))


# --- the negative that guards Deliverable 2: NO startup nag for ABSENT -----


def test_absent_endpoint_is_not_nagged_at_startup(trusted_site: Path) -> None:
    """An imperative-only (manifest-less) installed pack loads QUIETLY.

    The loader emits a load error for MISPLACED / MALFORMED / FENCED (a manifest
    exists but could not bind), but ABSENT — the ordinary "ships no manifest"
    case — must produce a carrier with NO error, or every plain entry-point pack
    would nag on every start. This pins that ``list``/``verify`` are the only
    surfaces, not startup.
    """

    _install_setuptools_dropped_manifest(trusted_site)

    rows = L._discover_via_entry_points(set())

    def _names_dropped(entry: object, err: object) -> bool:
        # A manifest-less endpoint is carried as an ``_EntryPointEntry`` whose
        # ``.name`` is the entry-point name; a BOUND one as a ``_ManifestEntry``
        # with ``.ep_ref``. An error row names the endpoint in ``.path``.
        if entry is not None and getattr(entry, "name", None) == "dropped":
            return True
        ep_ref = getattr(entry, "ep_ref", None)
        if ep_ref is not None and getattr(ep_ref, "name", None) == "dropped":
            return True
        return err is not None and "dropped" in err.path

    dropped = [(entry, err) for entry, err in rows if _names_dropped(entry, err)]
    assert dropped, "the dropped pack was not discovered at all"
    # It IS carried (loads manifest-less) and it is NOT accompanied by an error.
    assert all(err is None for _entry, err in dropped), (
        f"ABSENT pack nagged at startup: {[e for _e, e in dropped if e]}"
    )


def test_absent_is_the_outcome_the_classifier_reports(trusted_site: Path) -> None:
    """Tie the two halves together: the same shape verify calls ABSENT is what
    list annotates and what the loader loads quietly."""

    _install_setuptools_dropped_manifest(trusted_site)

    statuses = EI.classify_installed_endpoints()
    dropped = [s for s in statuses if s.ep_name == "dropped"]
    assert len(dropped) == 1
    assert dropped[0].outcome is EpOutcome.ABSENT
    assert dropped[0].bound is False
