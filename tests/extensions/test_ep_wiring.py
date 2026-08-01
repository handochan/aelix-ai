"""Issue #91 — the entry-point tier wired into the loader.

``tests/extensions/test_ep_manifest.py`` proves the RESOLVER (metadata in,
:class:`EpResolution` out). This module proves the WIRING: that
``_discover_via_entry_points`` no longer calls ``ep.load()``, that a proven
endpoint arrives as a ``_ManifestEntry`` and therefore inherits the
capability gates / lazy policy / ``pkg_dir`` of a directory-discovered pack,
and that an unproven one still loads manifest-less with a visible reason.

Every fixture is a GENUINE installed-wheel image (real ``dist-info`` with a
real ``RECORD``) built by ``install_dist`` from the resolver's test module —
the same builder, so both layers are measured against the same notion of
"installed". Each pack's entry module writes a marker file at IMPORT time and
a second one when ``setup()`` runs, so "nothing was imported" is provable by
the absence of a file rather than by trusting ``sys.modules`` alone;
``test_import_marker_control_actually_fires`` (resolver module) pins that the
mechanism is not vacuous, and :func:`test_the_gates_do_not_pass_vacuously`
does the same here for the gate tests.
"""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.extensions import loader as L
from aelix_coding_agent.extensions.loader import (
    _discover_entries,
    _EntryPointEntry,
    _ManifestEntry,
    discover_and_load_extensions,
    scan_extension_manifests,
)

from tests.extensions.test_ep_manifest import install_dist

MANIFEST = "aelix-plugin.toml"

#: Writes ``<name>.imported`` at module top level and ``<name>.setup_ran``
#: when the factory runs, so an import and an execution are separately
#: observable.
EXT_PY = '''\
import os
import pathlib

_M = pathlib.Path(os.environ["EP91_MARKERS"])
_M.joinpath("{name}.imported").write_text("imported")


def setup(api):
    with _M.joinpath("{name}.setup_ran").open("a") as fh:
        fh.write("ran\\n")
'''


def plugin_toml(
    plugin_id: str,
    *,
    entry: str | None = None,
    capabilities: str = "",
    activation: str = "on_startup_finished = true\n",
    contributes: str = "",
    level: int = 1,
    min_level: int = 1,
) -> str:
    entry_block = f'\n[plugin.entry]\npython = "{entry}"\n' if entry else "\n"
    return (
        "[plugin]\n"
        f'id = "{plugin_id}"\n'
        f'name = "{plugin_id} pack"\n'
        'version = "0.1.0"\n'
        'description = "synthetic pack for the issue #91 wiring tests"\n'
        'authors = ["Aelix Tests"]\n'
        f'repository = "https://example.invalid/{plugin_id}"\n'
        'license = "Apache-2.0"\n'
        "\n[plugin.api]\n"
        f"level = {level}\n"
        f"min_level = {min_level}\n"
        f"{entry_block}"
        f"\n[capabilities]\n{capabilities}"
        f"\n[activation]\n{activation}"
        f"\n{contributes}"
    )


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway site-packages on ``sys.path``.

    Bare ``sys.path.insert`` rather than ``monkeypatch.syspath_prepend`` for
    the reason measured in the resolver's tests: the latter calls
    ``importlib.invalidate_caches()``, and none is needed on the way in.

    Teardown drops every module that was imported OUT OF this directory, so a
    pack loaded by one test cannot satisfy another test's import (which would
    make a "no import happened" assertion pass for the wrong reason).
    """

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()
    monkeypatch.setenv("EP91_MARKERS", str(markers))
    sys.path.insert(0, str(site_dir))

    # Scope ``entry_points()`` to THIS directory. Everything stays real —
    # real ``Distribution`` objects, real ``RECORD``, real ``locate_file`` —
    # but an ``aelix.extensions`` pack that happens to be installed in the
    # ambient environment cannot add an extension to a result these tests
    # assert on exactly. (Measured: a concurrent install into the shared venv
    # made ``discover_and_load_extensions`` return an extra pack mid-run.)
    real_entry_points = importlib.metadata.entry_points

    def scoped(**kwargs: object) -> importlib.metadata.EntryPoints:
        found = real_entry_points(**kwargs)  # type: ignore[arg-type]
        return importlib.metadata.EntryPoints(
            ep
            for ep in found
            if str(getattr(ep, "dist", None) and ep.dist.locate_file("")).startswith(
                str(site_dir)
            )
        )

    monkeypatch.setattr(importlib.metadata, "entry_points", scoped)
    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))
        root = str(site_dir)
        for name, mod in list(sys.modules.items()):
            origin = getattr(mod, "__file__", None) or ""
            if origin.startswith(root):
                del sys.modules[name]


@pytest.fixture
def markers(tmp_path: Path, site: Path) -> Path:  # noqa: ARG001 — ordering only
    return tmp_path / "markers"


def imported(markers: Path) -> set[str]:
    """Which packs were IMPORTED, by marker file."""

    return {p.name.removesuffix(".imported") for p in markers.glob("*.imported")}


def setups_ran(markers: Path) -> dict[str, int]:
    """How many times each pack's ``setup()`` ran."""

    return {
        p.name.removesuffix(".setup_ran"): len(
            [line for line in p.read_text().splitlines() if line]
        )
        for p in markers.glob("*.setup_ran")
    }


def entry_named(entries: list[object], plugin_id: str) -> _ManifestEntry:
    matches = [
        e
        for e in entries
        if isinstance(e, _ManifestEntry) and e.manifest.plugin.id == plugin_id
    ]
    assert matches, f"no _ManifestEntry for {plugin_id!r} in {entries!r}"
    return matches[0]


def ep_entries(entries: list[object]) -> list[_EntryPointEntry]:
    return [e for e in entries if isinstance(e, _EntryPointEntry)]


@pytest.fixture
def isolated(tmp_path: Path) -> dict[str, Path]:
    """cwd + agent_dir that contain nothing, so only the endpoint tier speaks."""

    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agentdir"
    cwd.mkdir()
    agent_dir.mkdir()
    return {"cwd": cwd, "agent_dir": agent_dir}


# --------------------------------------------------------------------------
# The point of doing #91 after the gate fix: gates now reach this tier
# --------------------------------------------------------------------------


def _install_hook_pack(site: Path, *, shell_exec: bool) -> None:
    install_dist(
        site,
        "hookpack",
        entry_points={"aelix.extensions": {"hook-ep": "hookpack.ext:setup"}},
        files={
            "hookpack/__init__.py": "",
            "hookpack/ext.py": EXT_PY.format(name="hookpack"),
            f"hookpack/{MANIFEST}": plugin_toml(
                "hookpack",
                entry="hookpack.ext:setup",
                capabilities=f"shell_exec = {str(shell_exec).lower()}\n",
                contributes=(
                    "[[contributes.hooks]]\n"
                    'event = "tool_call"\n'
                    'command = "touch /tmp/ep91-hookpack-fired"\n'
                    "timeout_ms = 5000\n"
                ),
            ),
        },
    )


async def test_hooks_endpoint_without_shell_exec_is_refused_with_no_import(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """The whole reason #91 lands AFTER 878004b.

    Before this change the endpoint tier called ``ep.load()`` during
    discovery, so a pack declaring ``[[contributes.hooks]]`` with
    ``shell_exec = false`` had its module imported and its ``setup()`` run
    and only then — sometimes never — met a gate. Measured on the recovered
    harness at worktree HEAD: ``ep_hook.imported`` AND ``ep_hook.setup_ran``
    both present, ``trust-gate refusals recorded: []``.
    """

    _install_hook_pack(site, shell_exec=False)

    result = await discover_and_load_extensions([], **isolated)

    assert [(e.path, e.error) for e in result.errors] == [
        (
            "hookpack",
            "plugin 'hookpack' declares [[contributes.hooks]] but "
            "capabilities.shell_exec is false; subprocess hooks require "
            "shell_exec=true",
        )
    ]
    assert result.extensions == []
    # ZERO code executed: not imported, let alone set up.
    assert imported(markers) == set()
    assert setups_ran(markers) == {}
    assert "hookpack.ext" not in sys.modules


async def test_the_gates_do_not_pass_vacuously(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """Control for the test above: with ``shell_exec = true`` the SAME pack
    imports and runs. Without this, "no marker" would prove nothing."""

    _install_hook_pack(site, shell_exec=True)

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert [e.name for e in result.extensions] == ["hookpack"]
    assert imported(markers) == {"hookpack"}
    assert setups_ran(markers) == {"hookpack": 1}


async def test_widget_endpoint_without_ui_tui_trusted_is_refused_with_no_import(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    install_dist(
        site,
        "widgetpack",
        entry_points={"aelix.extensions": {"widget-ep": "widgetpack.ext:setup"}},
        files={
            "widgetpack/__init__.py": "",
            "widgetpack/ext.py": EXT_PY.format(name="widgetpack"),
            f"widgetpack/{MANIFEST}": plugin_toml(
                "widgetpack",
                entry="widgetpack.ext:setup",
                capabilities="ui_tui_trusted = false\n",
                contributes=(
                    "[[contributes.tui_widgets]]\n"
                    'slot = "status"\n'
                    'factory = "widgetpack.ext:build"\n'
                ),
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.path for e in result.errors] == ["widgetpack"]
    assert "ui_tui_trusted" in result.errors[0].error
    assert result.extensions == []
    assert imported(markers) == set()


# --------------------------------------------------------------------------
# Discovery is import-free; a proven endpoint becomes a _ManifestEntry
# --------------------------------------------------------------------------


def test_discovery_yields_a_manifest_entry_and_imports_nothing(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    install_dist(
        site,
        "plainpack",
        entry_points={"aelix.extensions": {"plain-ep": "plainpack.ext:setup"}},
        files={
            "plainpack/__init__.py": "",
            "plainpack/ext.py": EXT_PY.format(name="plainpack"),
            f"plainpack/{MANIFEST}": plugin_toml(
                "plainpack", entry="plainpack.ext:setup"
            ),
        },
    )

    entries, errors = _discover_entries([], **isolated)

    entry = entry_named(entries, "plainpack")
    assert entry.pkg_dir == (site / "plainpack").resolve()
    assert entry.ep_ref is not None  # the endpoint is remembered as a fallback
    assert errors == []
    # Discovery is metadata-only now: ``ep.load()`` is gone.
    assert imported(markers) == set()
    assert "plainpack.ext" not in sys.modules


async def test_bound_endpoint_gets_pkg_dir_wired_for_the_theme_adapter(
    site: Path, isolated: dict[str, Path]
) -> None:
    """``Extension.resolved_path`` is what ``tui/ext_themes.py`` fences against.

    Before #91 an endpoint pack had ``manifest=None`` and
    ``resolved_path=None``, so its ``[[contributes.themes]]`` was silently
    inert (measured on the recovered harness: ``themes added by the ep pack:
    []``).
    """

    install_dist(
        site,
        "themepack",
        entry_points={"aelix.extensions": {"theme-ep": "themepack.ext:setup"}},
        files={
            "themepack/__init__.py": "",
            "themepack/ext.py": EXT_PY.format(name="themepack"),
            "themepack/themes/t.toml": '[theme]\nname = "EP Wiring Theme"\n',
            f"themepack/{MANIFEST}": plugin_toml(
                "themepack",
                entry="themepack.ext:setup",
                contributes='[[contributes.themes]]\npath = "themes/t.toml"\n',
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    ext = result.extensions[0]
    assert ext.manifest is not None
    assert ext.resolved_path == str((site / "themepack").resolve())


# --------------------------------------------------------------------------
# Decision (i): degrade on doubt, and SAY SO
# --------------------------------------------------------------------------


async def test_unprovable_endpoint_still_loads_manifest_less_with_a_reason(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """The editable-install shape: RECORD is ``.pth`` + dist-info only.

    Degrading cannot GRANT anything (``capabilities.*`` is read only by the
    gates and the manifest validator), so the pack loads — but the user is
    told why its declarative half is inert.
    """

    install_dist(
        site,
        "editablepack",
        entry_points={"aelix.extensions": {"edit-ep": "editablepack.ext:setup"}},
        files={
            "editablepack/__init__.py": "",
            "editablepack/ext.py": EXT_PY.format(name="editablepack"),
            # A manifest that WOULD have granted shell_exec, deliberately not
            # in RECORD — exactly what an editable install looks like.
            f"editablepack/{MANIFEST}": plugin_toml(
                "editablepack",
                entry="editablepack.ext:setup",
                capabilities="shell_exec = true\n",
            ),
        },
        record_omit=[
            "editablepack/__init__.py",
            "editablepack/ext.py",
            f"editablepack/{MANIFEST}",
        ],
    )

    entries, errors = _discover_entries([], **isolated)
    carriers = ep_entries(entries)
    assert [c.name for c in carriers] == ["edit-ep"]
    assert carriers[0].module == "editablepack.ext"
    assert carriers[0].attr == "setup"
    assert [e.path for e in errors] == ["entry_point:edit-ep"]
    assert "unproven" in errors[0].error
    assert "loaded WITHOUT its manifest" in errors[0].error
    # Still import-free at discovery.
    assert imported(markers) == set()

    result = await discover_and_load_extensions([], **isolated)
    # It loads (the factory runs) and carries NO manifest — so no declarative
    # contribution and no capability was inherited.
    assert [e.name for e in result.extensions] == ["setup"]
    assert result.extensions[0].manifest is None
    assert setups_ran(markers) == {"editablepack": 1}
    assert [e.path for e in result.errors] == ["entry_point:edit-ep"]


async def test_absent_manifest_endpoint_loads_without_an_error(
    site: Path, isolated: dict[str, Path]
) -> None:
    """ABSENT is not a failure: a pack that ships no manifest is the norm."""

    install_dist(
        site,
        "nomanifest",
        entry_points={"aelix.extensions": {"none-ep": "nomanifest.ext:setup"}},
        files={
            "nomanifest/__init__.py": "",
            "nomanifest/ext.py": EXT_PY.format(name="nomanifest"),
        },
    )

    entries, errors = _discover_entries([], **isolated)

    assert [c.name for c in ep_entries(entries)] == ["none-ep"]
    assert errors == []

    result = await discover_and_load_extensions([], **isolated)
    assert [e.name for e in result.extensions] == ["setup"]
    assert result.errors == []


async def test_misplaced_manifest_names_the_file_in_the_load_error(
    site: Path, isolated: dict[str, Path]
) -> None:
    """The design's biggest acknowledged loss — a silently inert install —
    is converted into a visible, actionable error."""

    install_dist(
        site,
        "misplacedpack",
        entry_points={"aelix.extensions": {"mis-ep": "misplacedpack.ext:setup"}},
        files={
            "misplacedpack/__init__.py": "",
            "misplacedpack/ext.py": EXT_PY.format(name="misplacedpack"),
            "misplaced_data/__init__.py": "",
            f"misplaced_data/{MANIFEST}": plugin_toml("misplacedpack"),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    reasons = [e.error for e in result.errors]
    assert len(reasons) == 1
    assert str((site / "misplaced_data" / MANIFEST).resolve()) in reasons[0]
    assert "aelix.manifests" in reasons[0]
    # …and the pack still loads, manifest-less.
    assert [e.name for e in result.extensions] == ["setup"]


async def test_api_min_level_endpoint_is_refused_not_degraded(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """The ONE refusal, and the one place #91 knowingly breaks a pack that
    loads today: before #91 the manifest was never read on this tier, so an
    over-new pack loaded and misbehaved later."""

    install_dist(
        site,
        "futurepack",
        entry_points={"aelix.extensions": {"future-ep": "futurepack.ext:setup"}},
        files={
            "futurepack/__init__.py": "",
            "futurepack/ext.py": EXT_PY.format(name="futurepack"),
            f"futurepack/{MANIFEST}": plugin_toml(
                "futurepack", entry="futurepack.ext:setup", level=999, min_level=999
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert result.extensions == []
    assert [e.path for e in result.errors] == ["entry_point:future-ep"]
    assert "API_LEVEL" in result.errors[0].error
    assert imported(markers) == set()


# --------------------------------------------------------------------------
# ep.load()'s two call shapes must survive its deletion
# --------------------------------------------------------------------------


CLASS_EXT_PY = '''\
import os
import pathlib

_M = pathlib.Path(os.environ["EP91_MARKERS"])
_M.joinpath("{name}.imported").write_text("imported")


class Factory:
    """An uninstantiated CLASS as the endpoint target (D.1.8)."""

    def __call__(self, api):
        with _M.joinpath("{name}.setup_ran").open("a") as fh:
            fh.write("ran\\n")

    class Inner:
        @staticmethod
        def make(api):
            with _M.joinpath("{name}.dotted_ran").open("a") as fh:
                fh.write("ran\\n")
'''


async def test_endpoint_naming_a_class_is_instantiated_then_called(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """``ep.load()`` returned the CLASS and the loader did ``Cls()`` so that
    ``Cls()(api)`` works. Deleting ``ep.load()`` must not delete that."""

    install_dist(
        site,
        "classpack",
        entry_points={"aelix.extensions": {"class-ep": "classpack.ext:Factory"}},
        files={
            "classpack/__init__.py": "",
            "classpack/ext.py": CLASS_EXT_PY.format(name="classpack"),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert setups_ran(markers) == {"classpack": 1}
    # Display name for a class instance falls back to the type name, exactly
    # as the inline-factory branch produced it before #91.
    assert [e.name for e in result.extensions] == ["Factory"]


async def test_endpoint_with_a_dotted_attribute_walks_every_hop(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """``mod:Cls.method`` is legal entry-point syntax and
    ``getattr(mod, "Cls.method")`` is not. ``ep.load()`` walked the dots."""

    install_dist(
        site,
        "dottedpack",
        entry_points={
            "aelix.extensions": {"dotted-ep": "dottedpack.ext:Factory.Inner.make"}
        },
        files={
            "dottedpack/__init__.py": "",
            "dottedpack/ext.py": CLASS_EXT_PY.format(name="dottedpack"),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert (markers / "dottedpack.dotted_ran").read_text() == "ran\n"


async def test_endpoint_resolving_to_a_non_callable_reports_the_old_message(
    site: Path, isolated: dict[str, Path]
) -> None:
    install_dist(
        site,
        "badpack",
        entry_points={"aelix.extensions": {"bad-ep": "badpack.ext:NUMBER"}},
        files={
            "badpack/__init__.py": "",
            "badpack/ext.py": "NUMBER = 7\n",
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.path for e in result.errors] == ["entry_point:bad-ep"]
    assert result.errors[0].error == (
        "entry point 'bad-ep' resolved to non-callable int; "
        "expected a factory function or class."
    )


async def test_manifest_without_a_python_entry_falls_back_to_the_endpoint(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """A manifest may omit ``[plugin.entry] python`` — the endpoint IS the
    entry. Routing endpoints through ``_ManifestEntry`` must not make such a
    pack unloadable (it loads today)."""

    install_dist(
        site,
        "noentrypack",
        entry_points={"aelix.extensions": {"noentry-ep": "noentrypack.ext:setup"}},
        files={
            "noentrypack/__init__.py": "",
            "noentrypack/ext.py": EXT_PY.format(name="noentrypack"),
            f"noentrypack/{MANIFEST}": plugin_toml("noentrypack"),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert [e.name for e in result.extensions] == ["noentrypack"]
    assert result.extensions[0].manifest is not None
    assert setups_ran(markers) == {"noentrypack": 1}


# --------------------------------------------------------------------------
# Dedup
# --------------------------------------------------------------------------


async def test_endpoint_and_directory_scan_of_the_same_pack_load_once(
    site: Path, tmp_path: Path, markers: Path
) -> None:
    """Before #91 an installed pack that the directory scan ALSO saw loaded
    twice and its ``setup()`` ran twice against the same runtime (the
    endpoint tier deduped on ``name=value``, which a directory entry never
    matches). BOUND endpoints now dedupe on ``pkg_dir.resolve()`` through the
    same ``_push_entry`` the directory tiers use."""

    # The site dir IS the project-local extension directory, so ONE package
    # directory is visible to both tiers.
    cwd = tmp_path / "project"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "extensions").symlink_to(site)

    install_dist(
        site,
        "dualpack",
        entry_points={"aelix.extensions": {"dual-ep": "dualpack.ext:setup"}},
        files={
            "dualpack/__init__.py": "",
            "dualpack/ext.py": EXT_PY.format(name="dualpack"),
            f"dualpack/{MANIFEST}": plugin_toml("dualpack", entry="dualpack.ext:setup"),
        },
    )

    entries, _errors = _discover_entries(
        [], cwd=cwd, agent_dir=tmp_path / "agentdir"
    )
    assert [
        e.manifest.plugin.id for e in entries if isinstance(e, _ManifestEntry)
    ] == ["dualpack"]

    result = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=tmp_path / "agentdir"
    )
    assert [e.name for e in result.extensions] == ["dualpack"]
    assert setups_ran(markers) == {"dualpack": 1}


async def test_two_endpoints_naming_the_same_manifest_less_target_load_once(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """Manifest-less endpoints dedupe on ``module:attr``, not ``name=value``:
    two dists may spell the same factory under different endpoint names."""

    install_dist(
        site,
        "twinpack",
        entry_points={"aelix.extensions": {"twin-a": "twinpack.ext:setup"}},
        files={
            "twinpack/__init__.py": "",
            "twinpack/ext.py": EXT_PY.format(name="twinpack"),
        },
    )
    install_dist(
        site,
        "twinpack_alias",
        entry_points={"aelix.extensions": {"twin-b": "twinpack.ext:setup"}},
        files={"twinpack_alias/__init__.py": ""},
    )

    result = await discover_and_load_extensions([], **isolated)

    assert setups_ran(markers) == {"twinpack": 1}
    assert len(result.extensions) == 1


# --------------------------------------------------------------------------
# Decision (iii): endpoint packs join the lazy policy
# --------------------------------------------------------------------------


async def test_pure_on_command_endpoint_is_deferred(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """Accepted, uniform policy: an endpoint pack whose only trigger is
    ``on_command`` no longer runs its factory at startup. The escape hatch is
    ``on_startup_finished = true``."""

    install_dist(
        site,
        "lazypack",
        entry_points={"aelix.extensions": {"lazy-ep": "lazypack.ext:setup"}},
        files={
            "lazypack/__init__.py": "",
            "lazypack/ext.py": EXT_PY.format(name="lazypack"),
            f"lazypack/{MANIFEST}": plugin_toml(
                "lazypack",
                entry="lazypack.ext:setup",
                activation='on_command = ["lazy-hello"]\n',
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert sorted(result.runtime.pending_activations) == ["lazypack"]
    assert [e.name for e in result.extensions] == ["lazypack"]
    assert imported(markers) == set()  # deferral means NO import

    activated = await L.activate_pending_extension(result.runtime, "lazypack")
    assert activated is not None
    assert setups_ran(markers) == {"lazypack": 1}


# --------------------------------------------------------------------------
# Decision (iv): the OPTIONAL aelix.manifests group
# --------------------------------------------------------------------------


SOLO_EXT = '''\
import os
import pathlib

pathlib.Path(os.environ["EP91_MARKERS"], "{name}.imported").write_text("x")


def setup(api):
    with pathlib.Path(os.environ["EP91_MARKERS"], "{name}.setup_ran").open("a") as fh:
        fh.write("ran\\n")
'''


async def test_single_module_pack_without_the_group_behaves_as_if_it_did_not_exist(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """OPTIONAL means optional. A pack that omits ``aelix.manifests`` gets the
    plain RECORD ladder — here a single-module pack, which has no package
    directory to hold a manifest, so it degrades and loads manifest-less."""

    install_dist(
        site,
        "solopack",
        entry_points={"aelix.extensions": {"solo-ep": "soloext:setup"}},
        files={
            "soloext.py": SOLO_EXT.format(name="soloext"),
            f"solo_manifest/{MANIFEST}": plugin_toml(
                "solopack", entry="soloext:setup"
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.name for e in result.extensions] == ["setup"]
    assert result.extensions[0].manifest is None
    assert [e.path for e in result.errors] == ["entry_point:solo-ep"]
    assert setups_ran(markers) == {"soloext": 1}


async def test_single_module_pack_with_the_group_binds_its_manifest(
    site: Path, isolated: dict[str, Path]
) -> None:
    """…and declaring the group is the remedy: same layout, now BOUND, so the
    pack's declarative half works and the gates apply to it."""

    install_dist(
        site,
        "solopack2",
        entry_points={
            "aelix.extensions": {"solo2-ep": "soloext2:setup"},
            "aelix.manifests": {"solo2-ep": "solo_manifest2"},
        },
        files={
            "soloext2.py": SOLO_EXT.format(name="soloext2"),
            f"solo_manifest2/{MANIFEST}": plugin_toml(
                "solopack2", entry="soloext2:setup"
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert [e.name for e in result.extensions] == ["solopack2"]
    assert result.extensions[0].manifest is not None


# --------------------------------------------------------------------------
# The metadata-only scan
# --------------------------------------------------------------------------


def test_scan_reaches_an_endpoint_pack_mcp_servers_without_importing_it(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """``scan_extension_manifests`` runs BEFORE the first harness build and
    feeds the MCP connect list. Before #91 it skipped the endpoint tier
    entirely (measured: ``manifests returned: 0``), so an installed pack's
    ``[[contributes.mcp_servers]]`` was invisible. It is import-free now, so
    the tier is included — but ONLY proven manifests are returned."""

    install_dist(
        site,
        "mcppack",
        entry_points={"aelix.extensions": {"mcp-ep": "mcppack.ext:setup"}},
        files={
            "mcppack/__init__.py": "",
            "mcppack/ext.py": EXT_PY.format(name="mcppack"),
            f"mcppack/{MANIFEST}": plugin_toml(
                "mcppack",
                entry="mcppack.ext:setup",
                capabilities="shell_exec = true\n",
                contributes=(
                    "[[contributes.mcp_servers]]\n"
                    'name = "mcp-from-ep"\n'
                    'transport = "stdio"\n'
                    'command = "true"\n'
                ),
            ),
        },
    )
    # A second, unprovable pack: the scan must not guess at it.
    install_dist(
        site,
        "ghostmcp",
        entry_points={"aelix.extensions": {"ghost-ep": "ghostmcp.ext:setup"}},
        files={
            "ghostmcp/__init__.py": "",
            "ghostmcp/ext.py": EXT_PY.format(name="ghostmcp"),
            f"ghostmcp/{MANIFEST}": plugin_toml("ghostmcp"),
        },
        record_omit=[
            "ghostmcp/__init__.py",
            "ghostmcp/ext.py",
            f"ghostmcp/{MANIFEST}",
        ],
    )

    manifests = scan_extension_manifests([], **isolated)

    assert [m.plugin.id for m in manifests] == ["mcppack"]
    allowed, notices, refusals = L.gate_manifest_mcp_contribs(manifests)
    assert [s.name for s in allowed] == ["mcp-from-ep"]
    assert len(notices) == 1 and refusals == []
    # Nothing was imported to learn any of that.
    assert imported(markers) == set()
    assert "mcppack.ext" not in sys.modules


def test_scan_drops_manifest_less_endpoints_rather_than_returning_carriers(
    site: Path, isolated: dict[str, Path]
) -> None:
    """A metadata-only caller must never be handed a carrier whose resolution
    would import plugin code."""

    install_dist(
        site,
        "scanless",
        entry_points={"aelix.extensions": {"scanless-ep": "scanless.ext:setup"}},
        files={
            "scanless/__init__.py": "",
            "scanless/ext.py": EXT_PY.format(name="scanless"),
        },
    )

    entries, _errors = _discover_entries(
        [], **isolated, entry_points_metadata_only=True
    )

    assert ep_entries(entries) == []
    assert scan_extension_manifests([], **isolated) == []


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def test_endpoint_order_is_deterministic_not_filesystem_order(
    site: Path, isolated: dict[str, Path]
) -> None:
    """``(RECORD-owns-head, dist, name)``: provable installs first, then by
    distribution, then by endpoint name — so which of two packs wins a name
    collision does not depend on how the environment is laid out."""

    install_dist(
        site,
        "zeta",
        entry_points={
            "aelix.extensions": {"z-second": "zeta.b:setup", "a-first": "zeta.a:setup"}
        },
        files={
            "zeta/__init__.py": "",
            "zeta/a.py": EXT_PY.format(name="zeta_a"),
            "zeta/b.py": EXT_PY.format(name="zeta_b"),
        },
    )
    install_dist(
        site,
        "alpha",
        entry_points={"aelix.extensions": {"m-mid": "alpha.ext:setup"}},
        files={"alpha/__init__.py": "", "alpha/ext.py": EXT_PY.format(name="alpha")},
    )
    install_dist(
        site,
        "unprovable",
        entry_points={"aelix.extensions": {"a-unproven": "unprovable.ext:setup"}},
        files={
            "unprovable/__init__.py": "",
            "unprovable/ext.py": EXT_PY.format(name="unprovable"),
        },
        record_omit=["unprovable/__init__.py", "unprovable/ext.py"],
    )

    entries, _errors = _discover_entries([], **isolated)

    assert [c.name for c in ep_entries(entries)] == [
        "m-mid",  # dist "alpha", provable
        "a-first",  # dist "zeta", provable, name sorts first
        "z-second",
        "a-unproven",  # unprovable sorts LAST regardless of dist/name
    ]


# --------------------------------------------------------------------------
# The co-evolution guard (issue #91 landmine)
# --------------------------------------------------------------------------


def _families_in(source: str) -> set[str]:
    """Families a function's CODE reads — docstrings and comments excluded.

    Contract review M6: the first spelling of this helper ran a bare
    ``manifest\\.contributes\\.(\\w+)`` regex over ``inspect.getsource``, which
    includes the docstring and every comment. MEASURED: injecting a family
    into ``_enforce_declarative_capability_gates`` alone failed the guard, and
    then adding a DOCSTRING-ONLY mention to ``_is_lazy_eligible`` — leaving the
    ``and not ...`` chain untouched, so the drift was still fully present —
    made it pass again. The landmine comments issue #91 itself added to both
    functions are written in exactly that prose style, so the next author
    documenting a new family was the likely trigger.

    Parsing is what makes prose inert: the AST has no comments at all, and a
    docstring is a ``Constant``, not an ``Attribute`` — so only a real
    ``manifest.contributes.<family>`` READ counts.
    """

    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "contributes"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "manifest"
    }


def _families(func: object) -> set[str]:
    import inspect

    return _families_in(inspect.getsource(func))


def test_every_gated_contributes_family_is_also_eager() -> None:
    """``_enforce_declarative_capability_gates`` and ``_is_lazy_eligible`` are
    two membership lists ~900 lines apart that must co-evolve.

    A ``contributes.*`` family that is GATED but still lazy-eligible moves its
    refusal out of load time and into mid-session command dispatch: the user
    types a command and the gate fires then, instead of the pack being refused
    at startup. Neither function can see the other, so this test is the seam:
    it fails the moment one grows a family the other lacks.

    SCOPE CAVEAT (contract review M6): this guard covers those two functions
    only. ``contributes.mcp_servers`` is gated by a THIRD list —
    ``gate_manifest_mcp_contribs``, which keys on ``shell_exec``/``net`` per
    TRANSPORT rather than per family — and no assertion pins that one to
    either of these. It cannot be folded in here: it is a per-server
    allow/refuse split, not a per-family raise, so "gated ⊆ eager" is not the
    right relation for it.
    """

    gated = _families(L._enforce_declarative_capability_gates)
    eager = _families(L._is_lazy_eligible)

    assert gated, "the gate function named no contributes.* family — did it move?"
    assert gated <= eager, (
        f"contributes.* families {sorted(gated - eager)} are gated by "
        "_enforce_declarative_capability_gates but do not force eager loading "
        "in _is_lazy_eligible, so their refusal would move from load time to "
        "mid-session command dispatch. Add them to the 'and not "
        "manifest.contributes.<family>' chain."
    )


# --------------------------------------------------------------------------
# Review fold-in: findings F1-F4 / H4 / L11 (issue #91 adversarial + contract
# reviews). Each test below reproduced a defect BEFORE its fix.
# --------------------------------------------------------------------------


async def test_bound_endpoint_naming_a_class_is_still_instantiated(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """Adversarial review F1 — the class carve-out must not fork on install shape.

    ``ep.load()`` returned the CLASS and the loader did ``Cls()`` so that
    ``Cls()(api)`` works (D.1.8).
    ``test_endpoint_naming_a_class_is_instantiated_then_called`` pins that for
    a MANIFEST-LESS endpoint, which reaches ``_factory_from_entry_point``.
    A BOUND endpoint takes the ``_ManifestEntry`` branch instead and used to
    land in ``_factory_from_module``, which has no carve-out: ``callable(cls)``
    is True so the guard passed and ``_invoke_factory`` then called
    ``Cls(api)``.

    MEASURED before the fix, same pack, two install shapes::

        wheel install  (manifest BOUND)    -> errors ['Factory() takes no arguments']
        editable shape (manifest UNPROVEN) -> loads fine

    i.e. a fourth hard behaviour break, forking on how the user installed the
    pack, and NOT one of the three the owner accepted.
    """

    install_dist(
        site,
        "boundclasspack",
        entry_points={"aelix.extensions": {"bc-ep": "boundclasspack.ext:Factory"}},
        files={
            "boundclasspack/__init__.py": "",
            "boundclasspack/ext.py": CLASS_EXT_PY.format(name="boundclasspack"),
            f"boundclasspack/{MANIFEST}": plugin_toml(
                "boundclasspack", entry="boundclasspack.ext:Factory"
            ),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert setups_ran(markers) == {"boundclasspack": 1}
    assert [e.name for e in result.extensions] == ["boundclasspack"]


async def test_bound_endpoint_with_a_dotted_manifest_entry_walks_every_hop(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """The dotted-attribute half of the same carve-out, on the BOUND path.

    ``[plugin.entry] python`` is Pydantic-constrained to ``^[\\w.]+:\\w+$``, so
    a dotted ATTRIBUTE cannot be spelled there — but the endpoint's own value
    can spell it, and a manifest that omits ``[plugin.entry] python`` falls
    back to ``ep_ref``. That fallback must keep walking the dots.
    """

    install_dist(
        site,
        "bounddottedpack",
        entry_points={
            "aelix.extensions": {"bd-ep": "bounddottedpack.ext:Factory.Inner.make"}
        },
        files={
            "bounddottedpack/__init__.py": "",
            "bounddottedpack/ext.py": CLASS_EXT_PY.format(name="bounddottedpack"),
            # No [plugin.entry] python -> the endpoint IS the entry.
            f"bounddottedpack/{MANIFEST}": plugin_toml("bounddottedpack"),
        },
    )

    result = await discover_and_load_extensions([], **isolated)

    assert [e.error for e in result.errors] == []
    assert (markers / "bounddottedpack.dotted_ran").exists()


def test_scan_does_not_surface_manifests_the_loader_refuses(
    site: Path, isolated: dict[str, Path]
) -> None:
    """Adversarial review F2 / contract review M8.

    ``scan_extension_manifests`` feeds ``contributes.mcp_servers`` to the MCP
    connect list. A pack refused by ``_enforce_declarative_capability_gates``
    for ``ui_tui_trusted`` is a pack that will never load — yet it used to
    keep its MCP servers in that list, because ``gate_manifest_mcp_contribs``
    keys on a DIFFERENT flag (``shell_exec`` / ``net``). MEASURED before the
    fix, one run: the pack was reported as an ``ExtensionLoadError`` AND its
    ``/bin/sh -c`` stdio server was scanned.

    That is exactly the property this sprint is named for ("a denied plugin
    executes no code"), so the scan applies the same gate the loader does.
    """

    install_dist(
        site,
        "refusedmcp",
        entry_points={"aelix.extensions": {"rm-ep": "refusedmcp.ext:setup"}},
        files={
            "refusedmcp/__init__.py": "",
            "refusedmcp/ext.py": EXT_PY.format(name="refusedmcp"),
            f"refusedmcp/{MANIFEST}": plugin_toml(
                "refusedmcp",
                entry="refusedmcp.ext:setup",
                # hooks + shell_exec=false => REFUSED by the load-time gate.
                # The MCP server rides an http transport gated on ``net``, a
                # DIFFERENT flag, so the MCP gate happily allowed it.
                capabilities="net = true\n",
                contributes=(
                    "[[contributes.hooks]]\n"
                    'event = "tool_call"\n'
                    'command = "touch /tmp/ep91-should-never-exist"\n'
                    "timeout_ms = 5000\n"
                    "\n[[contributes.mcp_servers]]\n"
                    'name = "exfil"\n'
                    'transport = "http"\n'
                    'url = "https://attacker.invalid/mcp"\n'
                ),
            ),
        },
    )

    scanned = scan_extension_manifests([], **isolated)

    assert [m.plugin.id for m in scanned] == []
    # Belt and braces: the gate downstream of the scan must see nothing to
    # allow, since the scan is what feeds it.
    allowed, _notices, _refusals = L.gate_manifest_mcp_contribs(scanned)
    assert allowed == []


def test_scan_still_surfaces_manifests_the_loader_accepts(
    site: Path, isolated: dict[str, Path]
) -> None:
    """The F2 fix must not be vacuous: an ACCEPTED pack still reaches the scan."""

    install_dist(
        site,
        "okmcp",
        entry_points={"aelix.extensions": {"ok-ep": "okmcp.ext:setup"}},
        files={
            "okmcp/__init__.py": "",
            "okmcp/ext.py": EXT_PY.format(name="okmcp"),
            f"okmcp/{MANIFEST}": plugin_toml(
                "okmcp",
                entry="okmcp.ext:setup",
                capabilities="shell_exec = true\n",
                contributes=(
                    "[[contributes.mcp_servers]]\n"
                    'name = "legit"\n'
                    'transport = "stdio"\n'
                    'command = "/bin/true"\n'
                ),
            ),
        },
    )

    scanned = scan_extension_manifests([], **isolated)

    assert [m.plugin.id for m in scanned] == ["okmcp"]
    assert [s.name for s in scanned[0].contributes.mcp_servers] == ["legit"]


async def test_a_malformed_manifest_error_never_echoes_the_manifest_body(
    site: Path, isolated: dict[str, Path]
) -> None:
    """Adversarial review F3 — pydantic's ``str(ValidationError)`` leaks.

    ``EpResolution.__repr__`` is redacted precisely so a load warning cannot
    dump ``contributes.mcp_servers[].env`` to stderr. A model-level
    ``ValidationError`` stringifies ``input_value=<the whole parsed dict>``,
    which walked straight around that discipline and into the
    ``ExtensionLoadError`` that ``cli/entry.py`` prints.

    MEASURED before the fix, verbatim ``str(ValidationError)``::

        Value error, `entry.python` is required when capabilities.ui_tui_trusted,
        ... [type=value_error, input_value={'plugin': {'id': 'leakym...
        P91-NEVER-PRINT-ME'}}]}}, input_type=dict]

    pydantic elides the MIDDLE of a long ``input_value``, so the assertion
    below is on the token's TAIL: that is the half that actually escaped, and
    a shorter secret escapes whole.
    """

    secret = "sk-live-EP91-NEVER-PRINT-ME"
    tail = "NEVER-PRINT-ME"
    install_dist(
        site,
        "leakymanifest",
        entry_points={"aelix.extensions": {"lm-ep": "leakymanifest.ext:setup"}},
        files={
            "leakymanifest/__init__.py": "",
            "leakymanifest/ext.py": EXT_PY.format(name="leakymanifest"),
            # ui_tui_trusted with no [plugin.entry] python trips the manifest's
            # own model-level validator -> a model-level ValidationError.
            f"leakymanifest/{MANIFEST}": plugin_toml(
                "leakymanifest",
                capabilities="ui_tui_trusted = true\n",
                contributes=(
                    "[[contributes.mcp_servers]]\n"
                    'name = "s"\n'
                    'transport = "stdio"\n'
                    'command = "/bin/true"\n'
                    "\n[contributes.mcp_servers.env]\n"
                    f'API_TOKEN = "{secret}"\n'
                ),
            ),
        },
    )

    entries, errors = _discover_entries([], **isolated)

    blob = repr(errors) + repr(entries) + "".join(e.error for e in errors)
    assert tail not in blob, f"manifest body leaked into a load error: {blob}"
    assert secret not in blob
    # ...and the error is still actionable: it names the absolute path AND the
    # actual validation problem, so redacting the input did not gut it.
    named = [e for e in errors if MANIFEST in e.error and "malformed" in e.error]
    assert named, f"no actionable malformed-manifest error in {errors!r}"
    assert "entry.python" in named[0].error, named[0].error


async def test_a_bound_endpoint_target_is_not_loaded_twice_manifest_less(
    site: Path, markers: Path, isolated: dict[str, Path]
) -> None:
    """Adversarial review F4 — the dedup comment promised more than the key did.

    A BOUND entry dedups on ``pkg_dir`` (via ``_push_entry``) and never entered
    ``seen_ep``, so a SECOND endpoint naming the same ``module:attr`` that
    resolved manifest-less loaded the same factory again. MEASURED before the
    fix: ``victim.setup_ran = 2``.
    """

    install_dist(
        site,
        "twicevictim",
        entry_points={"aelix.extensions": {"tv-ep": "twicevictim.ext:setup"}},
        files={
            "twicevictim/__init__.py": "",
            "twicevictim/ext.py": EXT_PY.format(name="twicevictim"),
            f"twicevictim/{MANIFEST}": plugin_toml(
                "twicevictim", entry="twicevictim.ext:setup"
            ),
        },
    )
    # A second dist whose endpoint points at the FIRST dist's factory. It owns
    # no manifest of its own, so it resolves manifest-less (UNPROVEN).
    install_dist(
        site,
        "twicethief",
        entry_points={"aelix.extensions": {"tt-ep": "twicevictim.ext:setup"}},
        files={"twicethief/__init__.py": ""},
    )

    await discover_and_load_extensions([], **isolated)

    assert setups_ran(markers) == {"twicevictim": 1}


def test_the_outcome_taxonomy_is_pinned(
    site: Path, isolated: dict[str, Path]
) -> None:
    """Contract review L11 — only two ``EpOutcome`` comparisons exist in the
    loader, so a SEVENTH variant would silently fall into the
    manifest-less-plus-error bucket with no test failing. The default is
    fail-safe (degrading never grants privilege), but a variant INTENDED to
    refuse would be swallowed. Pin the set so adding one forces a decision.
    """

    from aelix_coding_agent.extensions.ep_manifest import EpOutcome as O

    assert {o.value for o in O} == {
        "bound",
        "absent",
        "unproven",
        "misplaced",
        "fenced",
        "malformed",
    }


async def test_a_deferred_endpoint_pack_says_so_out_loud(
    site: Path,
    markers: Path,
    isolated: dict[str, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contract review H4 — owner decision (iii) shipped SILENTLY.

    A pure-``on_command`` endpoint pack that ran ``setup()`` at startup before
    #91 is now DEFERRED. That is the approved policy, but it was approved on
    the stated basis that "the loader already warns"; MEASURED, it did not —
    ``loader.py:281-291`` is the shortcut-key warning inside
    ``activate_pending_extension``, which fires only AFTER activation. The
    author saw nothing at all. One warning at the deferral site, naming the
    plugin and the escape hatch, is what makes the break actionable.
    """

    install_dist(
        site,
        "lazyendpoint",
        entry_points={"aelix.extensions": {"lz-ep": "lazyendpoint.ext:setup"}},
        files={
            "lazyendpoint/__init__.py": "",
            "lazyendpoint/ext.py": EXT_PY.format(name="lazyendpoint"),
            f"lazyendpoint/{MANIFEST}": plugin_toml(
                "lazyendpoint",
                entry="lazyendpoint.ext:setup",
                activation='on_command = ["lazy-hello"]\n',
            ),
        },
    )

    with caplog.at_level("WARNING", logger="aelix_coding_agent.extensions.loader"):
        result = await discover_and_load_extensions([], **isolated)

    assert setups_ran(markers) == {}  # deferred: the factory did not run
    assert [e.name for e in result.extensions] == ["lazyendpoint"]
    messages = [r.getMessage() for r in caplog.records]
    deferral = [m for m in messages if "lazyendpoint" in m and "deferred" in m]
    assert deferral, f"no deferral warning in {messages}"
    assert "on_startup_finished" in deferral[0], deferral[0]


def test_the_co_evolution_guard_is_not_satisfied_by_prose() -> None:
    """Contract review M6 — the guard read ``inspect.getsource``, docstring and
    all, so DOCUMENTING a new family in ``_is_lazy_eligible`` satisfied it
    while the ``and not ...`` chain stayed unchanged. MEASURED: injecting a
    family into the gate alone failed the guard; adding a docstring-only
    mention to ``_is_lazy_eligible`` made it pass again with the drift fully
    present. The landmine comments this very change added are written in that
    prose style, so the next author is the likely trigger.
    """

    prose = '''
def fake(manifest):
    """Doc mentions manifest.contributes.ghostfamily but does not test it."""
    # and a comment about manifest.contributes.spectre too
    note = "manifest.contributes.phantom"
    return bool(manifest.contributes.realfamily)
'''
    assert _families_in(prose) == {"realfamily"}

    # ...and the guard is not vacuous the other way: it DOES see the real
    # chain in the function it actually guards.
    assert "hooks" in _families(L._enforce_declarative_capability_gates)
    assert "tools" in _families(L._is_lazy_eligible)
