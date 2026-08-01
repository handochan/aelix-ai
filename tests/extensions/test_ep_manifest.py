"""Issue #91 — entry-point manifest resolution must never import the plugin.

Every fixture here is a GENUINE installed-wheel image: a real
``<name>-<version>.dist-info`` with ``METADATA``, ``WHEEL``, ``INSTALLER``,
``entry_points.txt`` and a ``RECORD`` carrying real ``sha256=`` digests and
byte sizes, written into a throwaway ``site/`` directory that is put on
``sys.path`` for the duration of one test. That shape is what makes
``importlib.metadata`` hand us a real ``Distribution`` with real ``files()``
and a real ``locate_file("")`` root — which is the whole surface under test.

Two hygiene rules are pinned by tests at the bottom of this file, because this
technique is new to the repo and must not poison the other ~410 test modules:

* nothing calls ``importlib.invalidate_caches()`` on the way IN (measured: not
  needed — a fresh directory is a fresh ``FastPath``), and
* after teardown the synthetic distribution is fully gone from
  ``importlib.metadata``.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.metadata
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.ep_manifest import (
    MANIFESTS_GROUP,
    EpApiLevelRefusal,
    EpOutcome,
    resolve_entry_point_manifest,
)

# --------------------------------------------------------------------------
# Fixture construction
# --------------------------------------------------------------------------

MANIFEST = "aelix-plugin.toml"

#: Every synthetic pack's entry module writes this marker at IMPORT time (module
#: top level), so "we did not import it" is provable by the absence of a file
#: rather than by trusting ``sys.modules`` alone. ``test_import_marker_control``
#: proves the mechanism actually fires when the module IS imported, so the
#: no-import assertions are not vacuous.
EXT_PY = '''\
import os
import pathlib

_markers = os.environ.get("EP91_MARKERS")
if _markers:
    pathlib.Path(_markers, "{name}.imported").write_text("imported")


def setup(api):  # pragma: no cover - never reached; resolution must not import
    pathlib.Path(os.environ["EP91_MARKERS"], "{name}.setup_ran").write_text("ran")
'''


def manifest_toml(
    plugin_id: str,
    *,
    entry: str | None = None,
    level: int = 1,
    min_level: int = 1,
    capabilities: str = "",
    contributes: str = "",
    license_: str = "Apache-2.0",
) -> str:
    entry_block = f'\n[plugin.entry]\npython = "{entry}"\n' if entry else "\n"
    return (
        f"[plugin]\n"
        f'id = "{plugin_id}"\n'
        f'name = "{plugin_id} pack"\n'
        f'version = "0.1.0"\n'
        f'description = "synthetic pack for issue #91 tests"\n'
        f'authors = ["Aelix Tests"]\n'
        f'repository = "https://example.invalid/{plugin_id}"\n'
        f'license = "{license_}"\n'
        f"\n[plugin.api]\n"
        f"level = {level}\n"
        f"min_level = {min_level}\n"
        f"{entry_block}"
        f"\n[capabilities]\n{capabilities}"
        f"\n[activation]\non_startup_finished = true\n"
        f"\n{contributes}"
    )


def _record_line(rel: str, path: Path) -> str:
    data = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{rel},sha256={digest},{len(data)}"


def install_dist(
    site: Path,
    dist_name: str,
    *,
    version: str = "0.1.0",
    entry_points: Mapping[str, Mapping[str, str]] | None = None,
    files: Mapping[str, str] | None = None,
    record_extra: Sequence[str] = (),
    record_omit: Sequence[str] = (),
) -> Path:
    """Write a real installed-wheel image into ``site``; return the dist-info dir.

    ``files`` maps site-relative POSIX paths to file contents; each written file
    is recorded in ``RECORD`` with its real digest and size. ``record_extra``
    adds raw RECORD lines for paths we deliberately do NOT write (or write
    outside the root) — that is how the fence cases are built. ``record_omit``
    drops written files from RECORD — that is how the editable shape is built.
    """

    files = dict(files or {})
    dist_info = site / f"{dist_name}-{version}.dist-info"
    dist_info.mkdir(parents=True)

    record: list[str] = []
    for rel, content in files.items():
        target = site / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if rel not in record_omit:
            record.append(_record_line(rel, target))

    meta = {
        "METADATA": (
            f"Metadata-Version: 2.1\nName: {dist_name.replace('_', '-')}\n"
            f"Version: {version}\nSummary: synthetic\n"
        ),
        "WHEEL": "Wheel-Version: 1.0\nGenerator: aelix-tests\nRoot-Is-Purelib: true\n",
        "INSTALLER": "aelix-tests\n",
    }
    if entry_points:
        blocks = []
        for group, mapping in entry_points.items():
            body = "\n".join(f"{name} = {value}" for name, value in mapping.items())
            blocks.append(f"[{group}]\n{body}\n")
        meta["entry_points.txt"] = "\n".join(blocks)

    for name, content in meta.items():
        target = dist_info / name
        target.write_text(content, encoding="utf-8")
        record.append(_record_line(f"{dist_info.name}/{name}", target))

    record.extend(record_extra)
    record.append(f"{dist_info.name}/RECORD,,")
    (dist_info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    return dist_info


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway site-packages on ``sys.path``.

    Deliberately uses a bare ``sys.path.insert`` and NOT
    ``monkeypatch.syspath_prepend`` — the latter calls
    ``importlib.invalidate_caches()``, which would hide the measured fact that
    no cache invalidation is required on the way in.
    """

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()
    monkeypatch.setenv("EP91_MARKERS", str(markers))
    sys.path.insert(0, str(site_dir))
    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))


@pytest.fixture
def markers(tmp_path: Path, site: Path) -> Path:  # noqa: ARG001 - ordering only
    return tmp_path / "markers"


def get_ep(name: str, group: str = "aelix.extensions") -> importlib.metadata.EntryPoint:
    matches = [ep for ep in importlib.metadata.entry_points(group=group) if ep.name == name]
    assert matches, f"entry point {name!r} not visible in group {group!r}"
    return matches[0]


# --------------------------------------------------------------------------
# BOUND
# --------------------------------------------------------------------------


def test_bound_plain_wheel(site: Path) -> None:
    install_dist(
        site,
        "plainpack",
        entry_points={"aelix.extensions": {"plain": "plainpack.ext:setup"}},
        files={
            "plainpack/__init__.py": "",
            "plainpack/ext.py": EXT_PY.format(name="plainpack"),
            f"plainpack/{MANIFEST}": manifest_toml("plainpack", entry="plainpack.ext:setup"),
        },
    )

    res = resolve_entry_point_manifest(get_ep("plain"))

    assert res.outcome is EpOutcome.BOUND
    assert res.bound is True
    assert res.manifest is not None
    assert res.manifest.plugin.id == "plainpack"
    # pkg_dir has exactly ONE producer: manifest_path.resolve().parent.
    assert res.pkg_dir == (site / "plainpack" / MANIFEST).resolve().parent
    assert res.pkg_dir == (site / "plainpack").resolve()


def test_bound_dotted_entry_module_walks_up_to_the_package(site: Path) -> None:
    install_dist(
        site,
        "deeppack",
        entry_points={"aelix.extensions": {"deep": "deeppack.sub.ext:setup"}},
        files={
            "deeppack/__init__.py": "",
            "deeppack/sub/__init__.py": "",
            "deeppack/sub/ext.py": EXT_PY.format(name="deeppack"),
            f"deeppack/{MANIFEST}": manifest_toml("deeppack", entry="deeppack.sub.ext:setup"),
        },
    )

    res = resolve_entry_point_manifest(get_ep("deep"))

    assert res.outcome is EpOutcome.BOUND
    assert res.pkg_dir == (site / "deeppack").resolve()


def test_bound_multi_manifest_dist_binds_the_entry_modules_own_manifest(site: Path) -> None:
    """One dist, two packages, two manifests — naive first-match binds the wrong one.

    Measured on ``aelix-ep-danger``, which ships two packages' manifests and
    declares attacker-chosen entry-point names.
    """

    install_dist(
        site,
        "multipack",
        entry_points={
            "aelix.extensions": {
                "m-alpha": "multi_alpha.ext:setup",
                "m-beta": "multi_beta.ext:setup",
            }
        },
        files={
            "multi_alpha/__init__.py": "",
            "multi_alpha/ext.py": EXT_PY.format(name="multi_alpha"),
            f"multi_alpha/{MANIFEST}": manifest_toml(
                "multi-alpha", entry="multi_alpha.ext:setup", capabilities="shell_exec = true\n"
            ),
            "multi_beta/__init__.py": "",
            "multi_beta/ext.py": EXT_PY.format(name="multi_beta"),
            f"multi_beta/{MANIFEST}": manifest_toml("multi-beta", entry="multi_beta.ext:setup"),
        },
    )

    beta = resolve_entry_point_manifest(get_ep("m-beta"))
    alpha = resolve_entry_point_manifest(get_ep("m-alpha"))

    assert beta.outcome is EpOutcome.BOUND
    assert beta.manifest is not None
    assert beta.manifest.plugin.id == "multi-beta"
    assert beta.pkg_dir == (site / "multi_beta").resolve()
    # The wrong bind would have handed beta alpha's shell_exec=true.
    assert beta.manifest.capabilities.shell_exec is False

    assert alpha.outcome is EpOutcome.BOUND
    assert alpha.manifest is not None
    assert alpha.manifest.plugin.id == "multi-alpha"
    assert alpha.pkg_dir == (site / "multi_alpha").resolve()


# --------------------------------------------------------------------------
# ABSENT / UNPROVEN
# --------------------------------------------------------------------------


def test_absent_dist_ships_no_manifest(site: Path) -> None:
    install_dist(
        site,
        "nomanifest",
        entry_points={"aelix.extensions": {"bare": "nomanifest.ext:setup"}},
        files={
            "nomanifest/__init__.py": "",
            "nomanifest/ext.py": EXT_PY.format(name="nomanifest"),
        },
    )

    res = resolve_entry_point_manifest(get_ep("bare"))

    assert res.outcome is EpOutcome.ABSENT
    assert res.manifest is None
    assert res.pkg_dir is None
    assert "installs no aelix-plugin.toml" in res.reason


def test_unproven_editable_record_lists_only_pth_and_dist_info(
    site: Path, tmp_path: Path
) -> None:
    """Measured: an editable install's RECORD lists only the ``.pth`` + dist-info.

    The sources exist — outside the site root, reachable only through the
    import system — so metadata alone cannot prove anything. Degrade.
    """

    src = tmp_path / "src" / "editablepack"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "ext.py").write_text(EXT_PY.format(name="editablepack"), encoding="utf-8")
    (src / MANIFEST).write_text(
        manifest_toml(
            "editablepack",
            entry="editablepack.ext:setup",
            capabilities="shell_exec = true\n",
        ),
        encoding="utf-8",
    )

    install_dist(
        site,
        "editablepack",
        entry_points={"aelix.extensions": {"editable": "editablepack.ext:setup"}},
        files={"__editable__.editablepack-0.1.0.pth": f"{src.parent}\n"},
    )

    res = resolve_entry_point_manifest(get_ep("editable"))

    assert res.outcome is EpOutcome.UNPROVEN
    assert res.manifest is None
    assert res.pkg_dir is None
    assert "editablepack" in res.reason


def test_unproven_when_record_is_absent(site: Path) -> None:
    dist_info = install_dist(
        site,
        "norecord",
        entry_points={"aelix.extensions": {"norec": "norecord.ext:setup"}},
        files={
            "norecord/__init__.py": "",
            "norecord/ext.py": EXT_PY.format(name="norecord"),
            f"norecord/{MANIFEST}": manifest_toml("norecord", entry="norecord.ext:setup"),
        },
    )
    (dist_info / "RECORD").unlink()

    res = resolve_entry_point_manifest(get_ep("norec"))

    assert res.outcome is EpOutcome.UNPROVEN
    assert res.manifest is None


# --------------------------------------------------------------------------
# MISPLACED
# --------------------------------------------------------------------------


def test_misplaced_names_the_offending_path(site: Path) -> None:
    """The design's biggest acknowledged loss turned into a visible error."""

    install_dist(
        site,
        "misplacedpack",
        entry_points={"aelix.extensions": {"misplaced": "misplacedpack.ext:setup"}},
        files={
            "misplacedpack/__init__.py": "",
            "misplacedpack/ext.py": EXT_PY.format(name="misplacedpack"),
            f"misplaced_data/{MANIFEST}": manifest_toml(
                "misplacedpack", entry="misplacedpack.ext:setup"
            ),
        },
    )

    res = resolve_entry_point_manifest(get_ep("misplaced"))

    assert res.outcome is EpOutcome.MISPLACED
    assert res.manifest is None
    assert res.pkg_dir is None
    assert str((site / "misplaced_data" / MANIFEST).resolve()) in res.reason
    assert MANIFESTS_GROUP in res.reason


# --------------------------------------------------------------------------
# FENCED
# --------------------------------------------------------------------------


def test_fenced_absolute_record_entry(site: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / MANIFEST).write_text(
        manifest_toml("evilpack", entry="fencedabs.ext:setup", capabilities="shell_exec = true\n"),
        encoding="utf-8",
    )

    install_dist(
        site,
        "fencedabs",
        entry_points={"aelix.extensions": {"fenced-abs": "fencedabs.ext:setup"}},
        files={
            "fencedabs/__init__.py": "",
            "fencedabs/ext.py": EXT_PY.format(name="fencedabs"),
        },
        record_extra=[f"{(outside / MANIFEST).as_posix()},,"],
    )

    res = resolve_entry_point_manifest(get_ep("fenced-abs"))

    assert res.outcome is EpOutcome.FENCED
    assert res.manifest is None
    assert "is an absolute path" in res.reason


def test_fenced_dotdot_record_entry(site: Path, tmp_path: Path) -> None:
    """RECORD legitimately contains ``..`` entries and locate_file does NOT
    normalise them — they resolve outside the root with ``exists() is True``."""

    outside = tmp_path / "site_sibling"
    outside.mkdir()
    (outside / MANIFEST).write_text(
        manifest_toml("evilpack", entry="fenceddots.ext:setup"), encoding="utf-8"
    )

    install_dist(
        site,
        "fenceddots",
        entry_points={"aelix.extensions": {"fenced-dots": "fenceddots.ext:setup"}},
        files={
            "fenceddots/__init__.py": "",
            "fenceddots/ext.py": EXT_PY.format(name="fenceddots"),
        },
        record_extra=[f"../site_sibling/{MANIFEST},,"],
    )

    # Sanity: the escaping entry really does point at an existing file.
    assert (site / f"../site_sibling/{MANIFEST}").resolve().is_file()

    res = resolve_entry_point_manifest(get_ep("fenced-dots"))

    assert res.outcome is EpOutcome.FENCED
    assert "contains '..'" in res.reason


def test_fenced_symlink_escape_resolves_outside_the_root(site: Path, tmp_path: Path) -> None:
    """The fence resolves BOTH sides — a relative, ``..``-free entry can still
    escape through a symlinked package directory."""

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / MANIFEST).write_text(
        manifest_toml("evilpack", entry="linkpack.ext:setup"), encoding="utf-8"
    )
    (site / "linkpack").symlink_to(outside, target_is_directory=True)

    install_dist(
        site,
        "linkpack",
        entry_points={"aelix.extensions": {"fenced-link": "linkpack.ext:setup"}},
        record_extra=[f"linkpack/{MANIFEST},,"],
    )

    res = resolve_entry_point_manifest(get_ep("fenced-link"))

    assert res.outcome is EpOutcome.FENCED
    assert "outside the dist root" in res.reason


def test_fenced_manifest_at_the_wheel_root(site: Path) -> None:
    """A manifest at the wheel root would make ``pkg_dir`` == site-packages,
    turning ``ext_themes``' ``is_relative_to(pkg_dir)`` fence into a read of
    every file under site-packages."""

    install_dist(
        site,
        "rootpack",
        entry_points={"aelix.extensions": {"fenced-root": "rootpack.ext:setup"}},
        files={
            "rootpack/__init__.py": "",
            "rootpack/ext.py": EXT_PY.format(name="rootpack"),
            MANIFEST: manifest_toml("rootpack", entry="rootpack.ext:setup"),
        },
    )

    res = resolve_entry_point_manifest(get_ep("fenced-root"))

    assert res.outcome is EpOutcome.FENCED
    assert "sits at the dist root" in res.reason
    assert res.pkg_dir is None


def test_fenced_record_entry_that_is_not_a_file(site: Path) -> None:
    """A RECORD entry can exist without being a file.

    MEASURED on CPython 3.12.1: ``Distribution.files`` already drops entries
    whose ``locate().exists()`` is False (``skip_missing_files``), so the
    "listed but deleted" shape never reaches the fence on 3.12 — but a
    DIRECTORY named ``aelix-plugin.toml`` exists and is not a file, and on the
    3.11 leg (``target-version = "py311"``) the missing-file shape does reach
    us. ``is_file()`` is therefore not dead code.
    """

    (site / "ghostpack" / MANIFEST).mkdir(parents=True)

    install_dist(
        site,
        "ghostpack",
        entry_points={"aelix.extensions": {"ghost": "ghostpack.ext:setup"}},
        files={
            "ghostpack/__init__.py": "",
            "ghostpack/ext.py": EXT_PY.format(name="ghostpack"),
        },
        record_extra=[f"ghostpack/{MANIFEST},,"],
    )

    res = resolve_entry_point_manifest(get_ep("ghost"))

    assert res.outcome is EpOutcome.FENCED
    assert "is not a file" in res.reason


# --------------------------------------------------------------------------
# MALFORMED / refusal
# --------------------------------------------------------------------------


def test_malformed_toml_syntax_degrades_with_the_absolute_path(site: Path) -> None:
    install_dist(
        site,
        "badtoml",
        entry_points={"aelix.extensions": {"bad-toml": "badtoml.ext:setup"}},
        files={
            "badtoml/__init__.py": "",
            "badtoml/ext.py": EXT_PY.format(name="badtoml"),
            f"badtoml/{MANIFEST}": "[plugin\nid = broken",
        },
    )

    res = resolve_entry_point_manifest(get_ep("bad-toml"))

    assert res.outcome is EpOutcome.MALFORMED
    assert res.manifest is None
    assert res.pkg_dir is None
    assert str((site / "badtoml" / MANIFEST).resolve()) in res.reason


def test_malformed_schema_violation_degrades_with_the_absolute_path(site: Path) -> None:
    install_dist(
        site,
        "badschema",
        entry_points={"aelix.extensions": {"bad-schema": "badschema.ext:setup"}},
        files={
            "badschema/__init__.py": "",
            "badschema/ext.py": EXT_PY.format(name="badschema"),
            # Valid TOML, invalid manifest: no [plugin] identity, no [activation].
            f"badschema/{MANIFEST}": "[plugin.api]\nlevel = 1\nmin_level = 1\n",
        },
    )

    res = resolve_entry_point_manifest(get_ep("bad-schema"))

    assert res.outcome is EpOutcome.MALFORMED
    assert str((site / "badschema" / MANIFEST).resolve()) in res.reason


def test_api_min_level_above_host_refuses_instead_of_degrading(site: Path) -> None:
    """The single place issue #91 knowingly breaks a pack that loads today."""

    install_dist(
        site,
        "futurepack",
        entry_points={"aelix.extensions": {"future": "futurepack.ext:setup"}},
        files={
            "futurepack/__init__.py": "",
            "futurepack/ext.py": EXT_PY.format(name="futurepack"),
            f"futurepack/{MANIFEST}": manifest_toml(
                "futurepack", entry="futurepack.ext:setup", level=999, min_level=999
            ),
        },
    )

    with pytest.raises(EpApiLevelRefusal) as excinfo:
        resolve_entry_point_manifest(get_ep("future"))

    assert "futurepack" in str(excinfo.value)
    assert str((site / "futurepack" / MANIFEST).resolve()) in str(excinfo.value)


# --------------------------------------------------------------------------
# Cross-dist hijack
# --------------------------------------------------------------------------


def test_thief_entry_point_cannot_borrow_another_dists_manifest(site: Path) -> None:
    """An entry point pointing into a module owned by a DIFFERENT dist must not
    yield that dist's manifest (recovery-era ``thief.py``)."""

    install_dist(
        site,
        "victimdist",
        entry_points={"aelix.extensions": {"victim": "victimpkg.ext:setup"}},
        files={
            "victimpkg/__init__.py": "",
            "victimpkg/ext.py": EXT_PY.format(name="victimpkg"),
            f"victimpkg/{MANIFEST}": manifest_toml(
                "victimpack",
                entry="victimpkg.ext:setup",
                capabilities="ui_tui_trusted = true\nshell_exec = true\n",
            ),
        },
    )
    install_dist(
        site,
        "thiefdist",
        entry_points={"aelix.extensions": {"thief": "victimpkg.ext:setup"}},
        files={
            "thiefpkg/__init__.py": "",
            "thiefpkg/ext.py": EXT_PY.format(name="thiefpkg"),
        },
    )

    victim = resolve_entry_point_manifest(get_ep("victim"))
    thief = resolve_entry_point_manifest(get_ep("thief"))

    # The victim itself still binds normally.
    assert victim.outcome is EpOutcome.BOUND
    assert victim.manifest is not None
    assert victim.manifest.capabilities.shell_exec is True

    # The thief inherits nothing at all.
    assert thief.outcome is not EpOutcome.BOUND
    assert thief.outcome is EpOutcome.UNPROVEN
    assert thief.manifest is None
    assert thief.pkg_dir is None
    assert "victimpkg" in thief.reason


def test_thief_that_ships_its_own_manifest_still_cannot_bind_it(site: Path) -> None:
    install_dist(
        site,
        "victim2dist",
        entry_points={"aelix.extensions": {"victim2": "victim2pkg.ext:setup"}},
        files={
            "victim2pkg/__init__.py": "",
            "victim2pkg/ext.py": EXT_PY.format(name="victim2pkg"),
            f"victim2pkg/{MANIFEST}": manifest_toml("victim2pack", entry="victim2pkg.ext:setup"),
        },
    )
    install_dist(
        site,
        "thief2dist",
        entry_points={"aelix.extensions": {"thief2": "victim2pkg.ext:setup"}},
        files={
            "thief2pkg/__init__.py": "",
            f"thief2pkg/{MANIFEST}": manifest_toml(
                "thief2pack",
                entry="victim2pkg.ext:setup",
                capabilities="shell_exec = true\n",
            ),
        },
    )

    thief = resolve_entry_point_manifest(get_ep("thief2"))

    assert thief.outcome is EpOutcome.MISPLACED
    assert thief.manifest is None
    assert str((site / "thief2pkg" / MANIFEST).resolve()) in thief.reason


# --------------------------------------------------------------------------
# Optional aelix.manifests group (decision iv)
# --------------------------------------------------------------------------


SOLO_FILES = {
    "soloext.py": EXT_PY.format(name="soloext"),
    f"solo_manifest/{MANIFEST}": manifest_toml("solopack", entry="soloext:setup"),
}


def test_single_module_pack_without_the_group_is_misplaced(site: Path) -> None:
    """Optional means optional: omitting the group changes nothing about the
    ladder — the pack behaves exactly as it would without the feature."""

    install_dist(
        site,
        "solonogroup",
        entry_points={"aelix.extensions": {"solo-nogroup": "soloext:setup"}},
        files=SOLO_FILES,
    )

    res = resolve_entry_point_manifest(get_ep("solo-nogroup"))

    assert res.outcome is EpOutcome.MISPLACED
    assert str((site / "solo_manifest" / MANIFEST).resolve()) in res.reason


def test_manifests_group_binds_a_single_module_pack(site: Path) -> None:
    install_dist(
        site,
        "solowithgroup",
        entry_points={
            "aelix.extensions": {"solo": "soloext:setup"},
            MANIFESTS_GROUP: {"solo": "solo_manifest"},
        },
        files=SOLO_FILES,
    )

    res = resolve_entry_point_manifest(get_ep("solo"))

    assert res.outcome is EpOutcome.BOUND
    assert res.manifest is not None
    assert res.manifest.plugin.id == "solopack"
    assert res.pkg_dir == (site / "solo_manifest").resolve()
    assert MANIFESTS_GROUP in res.reason


def test_manifests_group_cannot_point_outside_its_own_record(site: Path) -> None:
    install_dist(
        site,
        "greedyvictim",
        entry_points={"aelix.extensions": {"greedy-victim": "greedyvictimpkg.ext:setup"}},
        files={
            "greedyvictimpkg/__init__.py": "",
            "greedyvictimpkg/ext.py": EXT_PY.format(name="greedyvictimpkg"),
            f"greedyvictimpkg/{MANIFEST}": manifest_toml(
                "greedyvictim",
                entry="greedyvictimpkg.ext:setup",
                capabilities="shell_exec = true\n",
            ),
        },
    )
    install_dist(
        site,
        "greedydist",
        entry_points={
            "aelix.extensions": {"greedy": "greedypkg.ext:setup"},
            # Points at a manifest belonging to ANOTHER distribution.
            MANIFESTS_GROUP: {"greedy": "greedyvictimpkg"},
        },
        files={
            "greedypkg/__init__.py": "",
            "greedypkg/ext.py": EXT_PY.format(name="greedypkg"),
        },
    )

    res = resolve_entry_point_manifest(get_ep("greedy"))

    assert res.outcome is not EpOutcome.BOUND
    assert res.manifest is None
    assert res.pkg_dir is None


def test_manifests_group_bound_manifest_is_still_fenced_and_parsed(site: Path) -> None:
    install_dist(
        site,
        "declaredbad",
        entry_points={
            "aelix.extensions": {"declared-bad": "declaredbad.ext:setup"},
            MANIFESTS_GROUP: {"declared-bad": "declaredbad_data"},
        },
        files={
            "declaredbad/__init__.py": "",
            "declaredbad/ext.py": EXT_PY.format(name="declaredbad"),
            f"declaredbad_data/{MANIFEST}": "[plugin\nnope",
        },
    )

    res = resolve_entry_point_manifest(get_ep("declared-bad"))

    assert res.outcome is EpOutcome.MALFORMED
    assert str((site / "declaredbad_data" / MANIFEST).resolve()) in res.reason


# --------------------------------------------------------------------------
# THE headline acceptance criterion: no import, ever
# --------------------------------------------------------------------------


ALL_EP_NAMES = (
    "plain",
    "deep",
    "m-alpha",
    "m-beta",
    "bare",
    "editable",
    "misplaced",
    "fenced-root",
    "bad-toml",
    "solo",
)

#: Top-level module names owned by the synthetic packs installed for the
#: no-import test. None of them may ever appear in ``sys.modules``.
PLUGIN_TOP_LEVELS = frozenset(
    {
        "plainpack",
        "deeppack",
        "multi_alpha",
        "multi_beta",
        "nomanifest",
        "editablepack",
        "misplacedpack",
        "rootpack",
        "badtoml",
        "soloext",
    }
)


def _install_every_shape(site: Path, tmp_path: Path) -> None:
    install_dist(
        site,
        "plainpack",
        entry_points={"aelix.extensions": {"plain": "plainpack.ext:setup"}},
        files={
            "plainpack/__init__.py": EXT_PY.format(name="plainpack_init"),
            "plainpack/ext.py": EXT_PY.format(name="plainpack"),
            f"plainpack/{MANIFEST}": manifest_toml("plainpack", entry="plainpack.ext:setup"),
        },
    )
    install_dist(
        site,
        "deeppack",
        entry_points={"aelix.extensions": {"deep": "deeppack.sub.ext:setup"}},
        files={
            "deeppack/__init__.py": EXT_PY.format(name="deeppack_init"),
            "deeppack/sub/__init__.py": "",
            "deeppack/sub/ext.py": EXT_PY.format(name="deeppack"),
            f"deeppack/{MANIFEST}": manifest_toml("deeppack", entry="deeppack.sub.ext:setup"),
        },
    )
    install_dist(
        site,
        "multipack",
        entry_points={
            "aelix.extensions": {
                "m-alpha": "multi_alpha.ext:setup",
                "m-beta": "multi_beta.ext:setup",
            }
        },
        files={
            "multi_alpha/__init__.py": "",
            "multi_alpha/ext.py": EXT_PY.format(name="multi_alpha"),
            f"multi_alpha/{MANIFEST}": manifest_toml("multi-alpha", entry="multi_alpha.ext:setup"),
            "multi_beta/__init__.py": "",
            "multi_beta/ext.py": EXT_PY.format(name="multi_beta"),
            f"multi_beta/{MANIFEST}": manifest_toml("multi-beta", entry="multi_beta.ext:setup"),
        },
    )
    install_dist(
        site,
        "nomanifest",
        entry_points={"aelix.extensions": {"bare": "nomanifest.ext:setup"}},
        files={
            "nomanifest/__init__.py": "",
            "nomanifest/ext.py": EXT_PY.format(name="nomanifest"),
        },
    )
    src = tmp_path / "src" / "editablepack"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "ext.py").write_text(EXT_PY.format(name="editablepack"), encoding="utf-8")
    install_dist(
        site,
        "editablepack",
        entry_points={"aelix.extensions": {"editable": "editablepack.ext:setup"}},
        files={"__editable__.editablepack-0.1.0.pth": f"{src.parent}\n"},
    )
    install_dist(
        site,
        "misplacedpack",
        entry_points={"aelix.extensions": {"misplaced": "misplacedpack.ext:setup"}},
        files={
            "misplacedpack/__init__.py": "",
            "misplacedpack/ext.py": EXT_PY.format(name="misplacedpack"),
            f"misplaced_data/{MANIFEST}": manifest_toml(
                "misplacedpack", entry="misplacedpack.ext:setup"
            ),
        },
    )
    install_dist(
        site,
        "rootpack",
        entry_points={"aelix.extensions": {"fenced-root": "rootpack.ext:setup"}},
        files={
            "rootpack/__init__.py": "",
            "rootpack/ext.py": EXT_PY.format(name="rootpack"),
            MANIFEST: manifest_toml("rootpack", entry="rootpack.ext:setup"),
        },
    )
    install_dist(
        site,
        "badtoml",
        entry_points={"aelix.extensions": {"bad-toml": "badtoml.ext:setup"}},
        files={
            "badtoml/__init__.py": "",
            "badtoml/ext.py": EXT_PY.format(name="badtoml"),
            f"badtoml/{MANIFEST}": "[plugin\nid = broken",
        },
    )
    install_dist(
        site,
        "solowithgroup",
        entry_points={
            "aelix.extensions": {"solo": "soloext:setup"},
            MANIFESTS_GROUP: {"solo": "solo_manifest"},
        },
        files=SOLO_FILES,
    )


def test_resolution_never_imports_the_plugin(
    site: Path, markers: Path, tmp_path: Path
) -> None:
    """Issue #91's headline acceptance criterion.

    Every synthetic pack writes an import marker at MODULE TOP LEVEL. After
    resolving every shape we support, ``sys.modules`` must have gained nothing
    and not one marker may exist.
    """

    _install_every_shape(site, tmp_path)
    before = set(sys.modules)

    outcomes = {name: resolve_entry_point_manifest(get_ep(name)).outcome for name in ALL_EP_NAMES}

    # (a) Not one plugin module entered sys.modules.
    gained = set(sys.modules) - before
    plugin_modules = sorted(m for m in gained if m.split(".")[0] in PLUGIN_TOP_LEVELS)
    assert plugin_modules == [], f"resolution imported plugin modules: {plugin_modules}"

    # (b) Not one import marker was written. Each pack writes its marker at
    #     MODULE TOP LEVEL, and test_import_marker_control_actually_fires
    #     proves the mechanism works, so this is not a vacuous assertion.
    assert sorted(p.name for p in markers.iterdir()) == []

    # (c) Nothing AT ALL is imported on a warm pass. The first pass may pull in
    #     a lazy stdlib helper of importlib.metadata itself (MEASURED: the
    #     METADATA parse imports email.parser / email.feedparser on first use),
    #     which is not the plugin and not ours; a second pass must add zero.
    warm = set(sys.modules)
    for name in ALL_EP_NAMES:
        resolve_entry_point_manifest(get_ep(name))
    assert set(sys.modules) - warm == set()

    # And it actually did the work, rather than trivially bailing out.
    assert outcomes == {
        "plain": EpOutcome.BOUND,
        "deep": EpOutcome.BOUND,
        "m-alpha": EpOutcome.BOUND,
        "m-beta": EpOutcome.BOUND,
        "bare": EpOutcome.ABSENT,
        "editable": EpOutcome.UNPROVEN,
        "misplaced": EpOutcome.MISPLACED,
        "fenced-root": EpOutcome.FENCED,
        "bad-toml": EpOutcome.MALFORMED,
        "solo": EpOutcome.BOUND,
    }


def test_import_marker_control_actually_fires(site: Path, markers: Path) -> None:
    """Positive control — without this, the no-import test could pass vacuously."""

    install_dist(
        site,
        "controlpack",
        entry_points={"aelix.extensions": {"control": "controlpack.ext:setup"}},
        files={
            "controlpack/__init__.py": "",
            "controlpack/ext.py": EXT_PY.format(name="controlpack"),
            f"controlpack/{MANIFEST}": manifest_toml("controlpack", entry="controlpack.ext:setup"),
        },
    )
    assert not (markers / "controlpack.imported").exists()

    ep = get_ep("control")
    try:
        ep.load()  # what the loader used to do at DISCOVERY time
        assert (markers / "controlpack.imported").exists()
        assert "controlpack.ext" in sys.modules
    finally:
        for name in [m for m in sys.modules if m.split(".")[0] == "controlpack"]:
            del sys.modules[name]


# --------------------------------------------------------------------------
# Fixture hygiene — this technique must not poison the other ~410 test modules
# --------------------------------------------------------------------------


def test_no_invalidate_caches_needed_on_the_way_in(tmp_path: Path) -> None:
    """Measured NON-trap, pinned so nobody adds a defensive invalidation."""

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    install_dist(
        site_dir,
        "cachepack",
        entry_points={"aelix.extensions": {"cache": "cachepack.ext:setup"}},
        files={
            "cachepack/__init__.py": "",
            "cachepack/ext.py": EXT_PY.format(name="cachepack"),
            f"cachepack/{MANIFEST}": manifest_toml("cachepack", entry="cachepack.ext:setup"),
        },
    )
    sys.path.insert(0, str(site_dir))
    try:
        # No importlib.invalidate_caches() here, on purpose.
        names = {ep.name for ep in importlib.metadata.entry_points(group="aelix.extensions")}
        assert "cache" in names
    finally:
        sys.path.remove(str(site_dir))


def test_synthetic_dist_is_gone_after_teardown(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    install_dist(
        site_dir,
        "leakpack",
        entry_points={"aelix.extensions": {"leak": "leakpack.ext:setup"}},
        files={
            "leakpack/__init__.py": "",
            "leakpack/ext.py": EXT_PY.format(name="leakpack"),
            f"leakpack/{MANIFEST}": manifest_toml("leakpack", entry="leakpack.ext:setup"),
        },
    )

    sys.path.insert(0, str(site_dir))
    try:
        assert any(d.metadata["Name"] == "leakpack" for d in importlib.metadata.distributions())
    finally:
        sys.path.remove(str(site_dir))

    assert not any(d.metadata["Name"] == "leakpack" for d in importlib.metadata.distributions())
    assert "leak" not in {
        ep.name for ep in importlib.metadata.entry_points(group="aelix.extensions")
    }
    assert not [m for m in sys.modules if m.split(".")[0] == "leakpack"]


# --------------------------------------------------------------------------
# Secret hygiene
# --------------------------------------------------------------------------


def test_repr_does_not_leak_mcp_server_env_secrets(site: Path) -> None:
    """``EpResolution`` flows into loader warnings; a default dataclass repr
    would expand ``contributes.mcp_servers[].env`` into stderr."""

    contributes = (
        "[[contributes.mcp_servers]]\n"
        'name = "secretive"\n'
        'transport = "stdio"\n'
        'command = "true"\n'
        "[contributes.mcp_servers.env]\n"
        'API_TOKEN = "sk-super-secret-value"\n'
    )
    install_dist(
        site,
        "secretpack",
        entry_points={"aelix.extensions": {"secret": "secretpack.ext:setup"}},
        files={
            "secretpack/__init__.py": "",
            "secretpack/ext.py": EXT_PY.format(name="secretpack"),
            f"secretpack/{MANIFEST}": manifest_toml(
                "secretpack",
                entry="secretpack.ext:setup",
                capabilities="shell_exec = true\n",
                contributes=contributes,
            ),
        },
    )

    res = resolve_entry_point_manifest(get_ep("secret"))

    assert res.outcome is EpOutcome.BOUND
    assert "sk-super-secret-value" not in repr(res)
    assert "sk-super-secret-value" not in str(res)
    assert "secretpack" in repr(res)
