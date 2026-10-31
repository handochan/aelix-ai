"""#641: an installed pack's module name never resolves from the project directory.

The loader imports three things BY NAME: ``-e <module>`` (#114), a manifest's
``[plugin.entry] python`` and an ``aelix.extensions`` entry point's module.
Under ``python -m`` the cwd is ``sys.path[0]``, and before #641 only the first
of the three took it off, so a project file named like a global pack's module
was imported in its place — in every delegated child, which ran as ``python -m
aelix_coding_agent`` in the project folder.

These tests build that ordering in-process — the project directory FIRST on
``sys.path``, an "installed" directory after it — and pin that the installed
module wins for each import, that a project module already in ``sys.modules``
is refused rather than reused, and which spellings of the cwd the guard
removes — and that it removes only what the LAUNCH put there (``''`` and
``sys.path[0]``), not a ``PYTHONPATH`` or ``.pth`` entry, which a flat-layout
``pip install -e .`` run from its own root depends on.
``tests/agents_ext/test_child_safe_path_641.py`` measures the spawned child
itself.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.extensions import loader as L
from aelix_coding_agent.extensions.api import ExtensionFactory
from aelix_coding_agent.extensions.loader import discover_and_load_extensions

from tests.env_sandbox import child_env
from tests.extensions.test_ep_manifest import install_dist, manifest_toml

_GENUINE = """\
ORIGIN = "genuine"


def setup(aelix):
    return None
"""

_HOSTILE = """\
import pathlib

pathlib.Path({sentinel!r}).write_text("imported", encoding="utf-8")
ORIGIN = "project"


def setup(aelix):
    pathlib.Path({sentinel!r} + ".setup").write_text("ran", encoding="utf-8")
"""


class Layout:
    """A project directory that shadows names an installed directory provides."""

    def __init__(self, tmp_path: Path) -> None:
        self.cwd = tmp_path / "proj"
        self.site = tmp_path / "site"
        self.agent_dir = tmp_path / "agent"
        self.marks = tmp_path / "marks"
        for d in (self.cwd, self.site, self.agent_dir, self.marks):
            d.mkdir()
        self.names: list[str] = []

    def sentinel(self, name: str) -> Path:
        return self.marks / f"SENTINEL-{name}"

    def shadow(self, name: str, *, package: str | None = None) -> None:
        """``name`` in the INSTALLED directory, and a hostile twin in the project.

        ``package`` makes both sides a package holding ``name`` as a submodule
        (``package.name``), the shape of an entry-point pack.
        """

        self.names.append(package or name)
        src_genuine = _GENUINE
        src_hostile = _HOSTILE.format(sentinel=str(self.sentinel(package or name)))
        if package is None:
            (self.site / f"{name}.py").write_text(src_genuine, encoding="utf-8")
            (self.cwd / f"{name}.py").write_text(src_hostile, encoding="utf-8")
            return
        hostile = self.cwd / package
        hostile.mkdir()
        (hostile / "__init__.py").write_text(src_hostile, encoding="utf-8")
        (hostile / f"{name}.py").write_text(src_hostile, encoding="utf-8")

    def global_pack(self, plugin_id: str, entry: str) -> None:
        pack = self.agent_dir / "extensions" / plugin_id
        pack.mkdir(parents=True)
        (pack / "aelix-plugin.toml").write_text(
            manifest_toml(plugin_id, entry=entry), encoding="utf-8"
        )


def _launched_with(monkeypatch: pytest.MonkeyPatch, *entries: str | Path) -> None:
    """Model a launch that put ``entries`` on ``sys.path`` (``python -m``: the cwd)."""

    monkeypatch.setattr(L, "_LAUNCH_PATH_ENTRIES", tuple(str(e) for e in entries))


@pytest.fixture(autouse=True)
def _launched_with_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No row inherits how pytest itself was started; each says what it models.

    The loader reads the launch's entries once, at import
    (``_LAUNCH_PATH_ENTRIES``), so a ``python -m pytest`` run would otherwise
    hand its own ``sys.path[0]`` to every row.
    """

    _launched_with(monkeypatch)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Layout]:
    """``sys.path`` = [project, installed, …] — what ``python -m`` gives a child.

    ``entry_points()`` is scoped to the installed directory and that directory
    is declared a real site directory, exactly as ``test_ep_wiring``'s ``site``
    fixture does, so an endpoint here passes the #91 provenance gate the way a
    ``pip install`` would.
    """

    lay = Layout(tmp_path)
    monkeypatch.syspath_prepend(str(lay.site))
    monkeypatch.syspath_prepend(str(lay.cwd))
    _launched_with(monkeypatch, lay.cwd)

    real_entry_points = importlib.metadata.entry_points

    def scoped(*, group: str) -> importlib.metadata.EntryPoints:
        return importlib.metadata.EntryPoints(
            ep
            for ep in real_entry_points(group=group)
            if ep.dist is not None and str(ep.dist.locate_file("")).startswith(str(lay.site))
        )

    monkeypatch.setattr(importlib.metadata, "entry_points", scoped)
    real_env_dirs = L.environment_site_dirs
    monkeypatch.setattr(L, "environment_site_dirs", lambda: real_env_dirs() | {lay.site.resolve()})
    try:
        yield lay
    finally:
        for name in list(sys.modules):
            if name.split(".")[0] in lay.names:
                del sys.modules[name]
        importlib.invalidate_caches()


def _origin(name: str) -> str:
    return sys.modules[name].ORIGIN


def test_the_layout_reproduces_python_m(layout: Layout) -> None:
    """Control: without the loader, this ``sys.path`` imports the PROJECT file.

    Proves every "the installed module won" assertion below is not vacuous —
    it is what ``importlib.import_module`` did in a delegated child before #641.
    """

    layout.shadow("aelix_t641_control")
    importlib.invalidate_caches()

    importlib.import_module("aelix_t641_control")

    assert _origin("aelix_t641_control") == "project"
    assert layout.sentinel("aelix_t641_control").exists()


async def test_a_global_manifest_pack_imports_the_installed_module(layout: Layout) -> None:
    """``[plugin.entry] python = "<mod>:setup"`` in ``<agent_dir>/extensions``."""

    layout.shadow("aelix_t641_dirpack")
    layout.global_pack("dirpack", "aelix_t641_dirpack:setup")
    importlib.invalidate_caches()

    result = await discover_and_load_extensions(
        [], cwd=layout.cwd, agent_dir=layout.agent_dir, no_project_local=True
    )

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["dirpack"]
    assert _origin("aelix_t641_dirpack") == "genuine"
    assert not layout.sentinel("aelix_t641_dirpack").exists()


async def test_a_manifest_less_entry_point_imports_the_installed_module(
    layout: Layout,
) -> None:
    """An endpoint with no manifest: ``_factory_from_entry_point``'s import."""

    layout.shadow("aelix_t641_plainep")
    install_dist(
        layout.site,
        "aelix_t641_plain",
        entry_points={"aelix.extensions": {"plain": "aelix_t641_plainep:setup"}},
        files={"aelix_t641_plainep.py": _GENUINE},
    )
    importlib.invalidate_caches()

    result = await discover_and_load_extensions([], cwd=layout.cwd, agent_dir=layout.agent_dir)

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["setup"]
    assert _origin("aelix_t641_plainep") == "genuine"
    assert not layout.sentinel("aelix_t641_plainep").exists()


async def test_a_bound_entry_point_imports_the_installed_module(layout: Layout) -> None:
    """An endpoint whose shipped manifest names ``pkg.ext:setup``.

    The project holds ``pkg/`` — a package — so the head of the dotted name is
    what the project directory would have answered.
    """

    layout.shadow("ext", package="aelix_t641_boundpkg")
    install_dist(
        layout.site,
        "aelix_t641_bound",
        entry_points={"aelix.extensions": {"bound": "aelix_t641_boundpkg.ext:setup"}},
        files={
            "aelix_t641_boundpkg/__init__.py": "",
            "aelix_t641_boundpkg/ext.py": _GENUINE,
            "aelix_t641_boundpkg/aelix-plugin.toml": manifest_toml(
                "boundpack", entry="aelix_t641_boundpkg.ext:setup"
            ),
        },
    )
    importlib.invalidate_caches()

    result = await discover_and_load_extensions([], cwd=layout.cwd, agent_dir=layout.agent_dir)

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["boundpack"]
    assert _origin("aelix_t641_boundpkg.ext") == "genuine"
    assert not layout.sentinel("aelix_t641_boundpkg").exists()


async def test_a_project_module_already_imported_is_refused(layout: Layout) -> None:
    """``import_module`` answers from ``sys.modules`` without reading ``sys.path``.

    Something imported the project's file before the loader ran — what any
    import of that name does in a process started with ``python -m`` there.
    The path guard cannot help; the cached module is refused instead, and its
    ``setup()`` never runs.
    """

    layout.shadow("aelix_t641_cached")
    layout.global_pack("cachedpack", "aelix_t641_cached:setup")
    importlib.invalidate_caches()
    importlib.import_module("aelix_t641_cached")
    assert _origin("aelix_t641_cached") == "project"

    result = await discover_and_load_extensions(
        [], cwd=layout.cwd, agent_dir=layout.agent_dir, no_project_local=True
    )

    assert result.extensions == []
    [error] = result.errors
    assert error.path == "cachedpack"
    assert "'aelix_t641_cached' is already imported from the project directory" in error.error
    assert str(layout.cwd / "aelix_t641_cached.py") in error.error
    assert not Path(str(layout.sentinel("aelix_t641_cached")) + ".setup").exists()


@pytest.mark.parametrize(
    "where",
    [pytest.param(".venv", id="venv-inside-project"), pytest.param("src", id="editable-src")],
)
async def test_a_cached_module_from_below_the_project_is_not_a_project_module(
    layout: Layout, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """The definition: found THROUGH the project directory, not merely under it.

    A virtualenv inside the project (``./.venv/…/site-packages``) and an
    editable install's ``./src`` are both below the cwd and both legitimate;
    "under the cwd" would refuse every module installed into the first.
    """

    below = layout.cwd / where / "lib"
    below.mkdir(parents=True)
    (below / "aelix_t641_below.py").write_text(_GENUINE, encoding="utf-8")
    layout.names.append("aelix_t641_below")
    monkeypatch.syspath_prepend(str(below))
    layout.global_pack("belowpack", "aelix_t641_below:setup")
    importlib.invalidate_caches()
    importlib.import_module("aelix_t641_below")

    result = await discover_and_load_extensions(
        [], cwd=layout.cwd, agent_dir=layout.agent_dir, no_project_local=True
    )

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["belowpack"]


@pytest.mark.parametrize("shape", ["regular", "namespace"])
async def test_a_project_package_already_imported_is_refused(layout: Layout, shape: str) -> None:
    """The package branch of ``_module_locations``: a cached ``pkg`` refuses ``pkg.ext``.

    A regular package is placed by its ``__init__``'s directory, a namespace
    package by its ``__path__`` portions; either sitting directly in the
    project folder is what a project-folder ``sys.path[0]`` produced.
    """

    package = f"aelix_t641_{shape}pkg"
    layout.names.append(package)
    hostile = layout.cwd / package
    hostile.mkdir()
    genuine = layout.site / package
    genuine.mkdir()
    sentinel = str(layout.sentinel(package))
    (hostile / "ext.py").write_text(_HOSTILE.format(sentinel=sentinel), encoding="utf-8")
    (genuine / "ext.py").write_text(_GENUINE, encoding="utf-8")
    if shape == "regular":
        (hostile / "__init__.py").write_text("", encoding="utf-8")
        (genuine / "__init__.py").write_text("", encoding="utf-8")
    layout.global_pack(f"{shape}pack", f"{package}.ext:setup")
    importlib.invalidate_caches()
    importlib.import_module(package)

    result = await discover_and_load_extensions(
        [], cwd=layout.cwd, agent_dir=layout.agent_dir, no_project_local=True
    )

    assert result.extensions == []
    [error] = result.errors
    assert error.path == f"{shape}pack"
    assert f"{package!r} is already imported from the project directory" in error.error
    assert not Path(sentinel + ".setup").exists()


@pytest.mark.skipif(
    sys.platform == "win32", reason="control characters are not legal in Windows names"
)
async def test_the_cached_refusal_flattens_the_project_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The location is a path in the project folder, whose name a clone chose."""

    cwd = tmp_path / "proj\x1b[2J\x1b]0;pwned\x07\nFORGED"
    cwd.mkdir()
    agent_dir = tmp_path / "agent"
    (cwd / "aelix_t641_esc.py").write_text(_GENUINE, encoding="utf-8")
    pack = agent_dir / "extensions" / "escpack"
    pack.mkdir(parents=True)
    (pack / "aelix-plugin.toml").write_text(
        manifest_toml("escpack", entry="aelix_t641_esc:setup"), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(cwd))
    _launched_with(monkeypatch, cwd)
    monkeypatch.delitem(sys.modules, "aelix_t641_esc", raising=False)
    importlib.invalidate_caches()
    importlib.import_module("aelix_t641_esc")
    try:
        result = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=agent_dir, no_project_local=True
        )
    finally:
        sys.modules.pop("aelix_t641_esc", None)

    [error] = result.errors
    assert "already imported from the project directory" in error.error
    assert not any(ch in error.error for ch in "\x1b\x07\n")
    assert "proj[2J]0;pwnedFORGED" in error.error


# --------------------------------------------------------------------------
# A flat-layout editable install, run from its own root (#641 verify V1)
# --------------------------------------------------------------------------


@pytest.fixture
def flat_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The process runs in a flat-layout pack's root, which its ``.pth`` put on ``sys.path``.

    ``pip install -e .`` of a ``packages = ["pkg"]`` project writes a ``.pth``
    naming the project root, so site appends the cwd itself — at the END of
    ``sys.path``, nowhere near the ``sys.path[0]`` the console script's launch
    gave its own ``bin`` folder.
    """

    root = tmp_path / "starter"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "path", [str(tmp_path / "bin"), *sys.path, str(root)])
    _launched_with(monkeypatch, tmp_path / "bin")
    yield root
    for name in [n for n in sys.modules if n.startswith("aelix_t641_flat")]:
        del sys.modules[name]
    importlib.invalidate_caches()


@pytest.mark.parametrize("how", ["global-pack", "dash-e"])
async def test_a_flat_editable_install_imports_from_its_own_root(
    flat_root: Path, tmp_path: Path, how: str
) -> None:
    (flat_root / "aelix_t641_flat.py").write_text(_GENUINE, encoding="utf-8")
    agent_dir = tmp_path / "agent"
    configured: list[str | Path | ExtensionFactory] = []
    if how == "global-pack":
        pack = agent_dir / "extensions" / "flatpack"
        pack.mkdir(parents=True)
        (pack / "aelix-plugin.toml").write_text(
            manifest_toml("flatpack", entry="aelix_t641_flat:setup"), encoding="utf-8"
        )
    else:
        configured.append("aelix_t641_flat")
    importlib.invalidate_caches()

    result = await discover_and_load_extensions(
        configured, cwd=flat_root, agent_dir=agent_dir, no_project_local=True
    )

    assert result.errors == []
    assert len(result.extensions) == 1
    assert _origin("aelix_t641_flat") == "genuine"


async def test_a_flat_editable_module_already_imported_is_not_refused(
    flat_root: Path, tmp_path: Path
) -> None:
    """Imported through its ``.pth`` entry — by the vote load, or the build before
    a ``/reload`` — it is what a fresh import would find, so it is reused."""

    (flat_root / "aelix_t641_flatc.py").write_text(_GENUINE, encoding="utf-8")
    agent_dir = tmp_path / "agent"
    pack = agent_dir / "extensions" / "flatcpack"
    pack.mkdir(parents=True)
    (pack / "aelix-plugin.toml").write_text(
        manifest_toml("flatcpack", entry="aelix_t641_flatc:setup"), encoding="utf-8"
    )
    importlib.invalidate_caches()
    importlib.import_module("aelix_t641_flatc")

    result = await discover_and_load_extensions(
        [], cwd=flat_root, agent_dir=agent_dir, no_project_local=True
    )

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["flatcpack"]


def test_a_module_with_no_recorded_location_is_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hand-injected module cannot be placed, so it is not called a project's."""

    import types

    injected = types.ModuleType("aelix_t641_injected")
    monkeypatch.setitem(sys.modules, "aelix_t641_injected", injected)

    assert L._cached_project_module("aelix_t641_injected", L._project_dir_keys(tmp_path)) is None


# --------------------------------------------------------------------------
# Which ``sys.path`` spellings the guard reads as the project directory
# --------------------------------------------------------------------------


def _removed_by_guard(entry: str, cwd: Path) -> bool:
    """Is ``entry``, put at ``sys.path[0]`` by the launch, off the path inside the guard?"""

    sys.path.insert(0, entry)
    try:
        with pytest.MonkeyPatch.context() as mp:
            _launched_with(mp, entry)
            with L._import_path_without_cwd(cwd):
                inside = entry in sys.path
        assert sys.path[0] == entry, "the guard did not restore the entry"
        return not inside
    finally:
        sys.path.remove(entry)


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param(lambda cwd: "", id="empty"),
        pytest.param(lambda cwd: ".", id="dot"),
        pytest.param(lambda cwd: str(cwd) + os.sep, id="trailing-separator"),
        pytest.param(lambda cwd: str(cwd / "sub" / ".."), id="dot-dot"),
    ],
)
def test_the_guard_removes_every_spelling_of_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling
) -> None:
    """#114 compared raw strings, so ``'.'`` or ``<cwd>/`` stayed on the path."""

    cwd = tmp_path / "proj"
    (cwd / "sub").mkdir(parents=True)
    monkeypatch.chdir(cwd)

    assert _removed_by_guard(spelling(cwd), cwd)


def test_the_guard_removes_a_symlinked_spelling(tmp_path: Path) -> None:
    """``/tmp`` → ``/private/tmp`` on macOS is the everyday case."""

    cwd = tmp_path / "proj"
    cwd.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(cwd, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a directory symlink here: {exc}")

    assert _removed_by_guard(str(link), cwd)


@pytest.mark.skipif(sys.platform != "win32", reason="case and separators fold on Windows only")
@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param(str.upper, id="upper-case"),
        pytest.param(str.lower, id="lower-case"),
        pytest.param(lambda s: s.replace("\\", "/"), id="forward-slashes"),
    ],
)
def test_the_guard_folds_case_and_separators_on_windows(tmp_path: Path, spelling) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()

    assert _removed_by_guard(spelling(str(cwd)), cwd)


def test_the_guard_keeps_a_directory_below_the_cwd(tmp_path: Path) -> None:
    """``./src`` on ``sys.path`` is an editable install, not the project directory."""

    cwd = tmp_path / "proj"
    (cwd / "src").mkdir(parents=True)

    assert not _removed_by_guard(str(cwd / "src"), cwd)


#: ``sys.path`` shapes, by name. ``CWD`` is the project folder (and the cwd),
#: ``SITE`` an installed directory, ``bin`` a console script's folder, ``SHIM``
#: a script's own folder elsewhere, ``OTHER`` something an extension inserted.
_DIRS = ("CWD", "SITE", "bin", "SHIM", "OTHER")


@pytest.mark.parametrize(
    ("launch", "before", "inside"),
    [
        # The console script: its folder at [0], and a flat editable install's
        # ``.pth`` appends the cwd — the user's (#641 verify V1).
        pytest.param(["bin"], ["bin", "SITE", "CWD"], ["bin", "SITE", "CWD"], id="pth-entry"),
        # ``-P``: ``sys.path[0]`` is the first folder ``PYTHONPATH`` names.
        pytest.param([], ["CWD", "SITE"], ["CWD", "SITE"], id="pythonpath-under-safe-path"),
        # ``python -m`` in that flat root: the launch's cwd goes, the ``.pth``'s stays.
        pytest.param(["CWD"], ["CWD", "SITE", "CWD"], ["SITE", "CWD"], id="python-m-and-pth"),
        pytest.param(
            ["CWD"], ["CWD", "CWD", "SITE"], ["CWD", "SITE"], id="python-m-and-pythonpath-names-it"
        ),
        # A script elsewhere keeps its own folder (the rpc client's ``cli_path`` shim).
        pytest.param(["SHIM"], ["SHIM", "SITE"], ["SHIM", "SITE"], id="a-script-s-own-folder"),
        # #641 verify R1: the launch's cwd entry, not at ``sys.path[0]``.
        pytest.param(["CWD"], ["OTHER", "CWD", "SITE"], ["OTHER", "SITE"], id="behind-an-insert"),
        pytest.param(
            ["bin", "CWD"], ["bin", "CWD", "SITE"], ["bin", "SITE"], id="console-empty-segment"
        ),
        pytest.param(["CWD", "CWD"], ["CWD", "CWD", "SITE"], ["SITE"], id="python-m-empty-segment"),
    ],
)
def test_the_guard_takes_off_what_the_launch_put_there_wherever_it_sits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launch: list[str],
    before: list[str],
    inside: list[str],
) -> None:
    """Only the launch's entries go, found by value: a ``PYTHONPATH`` folder or
    ``.pth`` entry naming the cwd is the user's and stays."""

    dirs = {name: tmp_path / name.lower() for name in _DIRS}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.chdir(dirs["CWD"])
    tail = [p for p in sys.path if p != "" and not L._dir_keys(p) & L._dir_keys(str(dirs["CWD"]))]
    monkeypatch.setattr(sys, "path", [str(dirs[n]) for n in before] + tail)
    _launched_with(monkeypatch, *(dirs[n] for n in launch))

    with L._import_path_without_cwd(dirs["CWD"]):
        assert sys.path == [str(dirs[n]) for n in inside] + tail
    assert sys.path == [str(dirs[n]) for n in before] + tail


@pytest.mark.parametrize(
    ("safe_path", "pythonpath", "pth", "expected"),
    [
        pytest.param(False, None, False, ["PATH0"], id="no-pythonpath"),
        pytest.param(True, None, False, [], id="safe-path"),
        pytest.param(False, "{site}", False, ["PATH0"], id="named-only"),
        pytest.param(False, "", False, ["PATH0"], id="set-but-empty"),
        pytest.param(False, "{sep}{site}", False, ["PATH0", "CWD"], id="leading-empty"),
        pytest.param(False, "{site}{sep}", False, ["PATH0", "CWD"], id="trailing-empty"),
        pytest.param(False, "{sep}{sep}{site}", False, ["PATH0", "CWD"], id="two-empty-one-entry"),
        pytest.param(True, "{sep}{site}", False, ["CWD"], id="safe-path-empty-segment"),
        # ``site`` dropped the ``.pth`` line as a duplicate: the entry is the user's.
        pytest.param(False, "{sep}{site}", True, ["PATH0"], id="empty-segment-and-a-pth-names-it"),
    ],
)
def test_which_entries_the_launch_put_there(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    safe_path: bool,
    pythonpath: str | None,
    pth: bool,
    expected: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    path0 = str(tmp_path / "bin")
    spelled = None if pythonpath is None else pythonpath.format(sep=os.pathsep, site="/site")
    names = {"PATH0": path0, "CWD": os.getcwd()}
    asked: list[str] = []

    def pth_names(folder: str) -> bool:
        asked.append(folder)
        return pth

    got = L._interpreter_path_entries(
        [path0, "/site"], safe_path=safe_path, pythonpath=spelled, pth_names=pth_names
    )

    assert got == tuple(names[n] for n in expected)
    assert asked == ([os.getcwd()] if "CWD" in expected or pth else [])


@pytest.mark.parametrize(
    ("file", "line", "names_it"),
    [
        pytest.param("flat.pth", "{cwd}", True, id="absolute"),
        pytest.param("flat.pth", "../cwd", True, id="relative-to-the-site-dir"),
        pytest.param("flat.pth", "{cwd}/", True, id="trailing-separator"),
        pytest.param("flat.pth", "# {cwd}", False, id="comment"),
        pytest.param("flat.pth", "import sys; sys.path.append({cwd!r})", False, id="import-line"),
        pytest.param("flat.pth", "{other}", False, id="another-folder"),
        pytest.param("flat.txt", "{cwd}", False, id="not-a-pth-file"),
    ],
)
def test_which_pth_lines_name_the_folder(
    tmp_path: Path, file: str, line: str, names_it: bool
) -> None:
    site, cwd, other = (tmp_path / n for n in ("site", "cwd", "other"))
    for d in (site, cwd, other):
        d.mkdir()
    (site / "empty.pth").write_text("\n", encoding="utf-8")
    (site / file).write_text(
        line.format(cwd=str(cwd), other=str(other)) + "\n", encoding="utf-8"
    )

    assert L._site_pth_names(str(cwd), [str(tmp_path / "missing"), str(site)]) is names_it


def test_the_interpreter_s_own_site_dirs_are_read(tmp_path: Path) -> None:
    """The default: this venv's site-packages, which name no fresh folder."""

    assert L._site_pth_names(str(tmp_path)) is False


_LAUNCH_RUNNER = """\
import sys
from pathlib import Path

from aelix_coding_agent.extensions import loader

if len(sys.argv) > 1:
    sys.path.insert(0, sys.argv[1])  # what a global file extension may do
loader._import_module_outside_cwd("aelix_t641_launch", cwd=Path.cwd())
print(sys.modules["aelix_t641_launch"].ORIGIN)
"""


@pytest.mark.parametrize(
    ("how", "pythonpath", "insert", "origin"),
    [
        pytest.param("python -m", ["tools", "site"], False, "genuine", id="python-m"),
        # #641 verify R1, each "project" on round 1 and "genuine" on base.
        pytest.param("console", ["", "site"], False, "genuine", id="console-empty-segment"),
        pytest.param("python -m", ["tools", "site"], True, "genuine", id="python-m-behind-an-insert"),
        pytest.param("python -m", ["", "tools", "site"], False, "genuine", id="python-m-empty-segment"),
        # The user named the folder: D4 leaves it, so the project file answers.
        pytest.param("python -m", ["tools", "cwd", "site"], False, "project", id="pythonpath-names-it"),
    ],
)
def test_a_real_launch_imports_the_installed_module(
    tmp_path: Path, how: str, pythonpath: list[str], insert: bool, origin: str
) -> None:
    """A real interpreter, so the launch's entries are the ones it recorded at import."""

    cwd, site, tools, other = (tmp_path / n for n in ("cwd", "site", "tools", "other"))
    for d in (cwd, site, tools, other):
        d.mkdir()
    sentinel = tmp_path / "SENTINEL"
    (site / "aelix_t641_launch.py").write_text(_GENUINE, encoding="utf-8")
    (cwd / "aelix_t641_launch.py").write_text(
        _HOSTILE.format(sentinel=str(sentinel)), encoding="utf-8"
    )
    (tools / "aelix_t641_runner.py").write_text(_LAUNCH_RUNNER, encoding="utf-8")
    named = {"": "", "site": str(site), "tools": str(tools), "cwd": str(cwd)}
    inherited = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    env = child_env(
        tmp_path / "home",
        PYTHONPATH=os.pathsep.join([named[n] for n in pythonpath] + inherited),
    )
    if how == "console":
        argv = [sys.executable, str(tools / "aelix_t641_runner.py")]
    else:
        argv = [sys.executable, "-m", "aelix_t641_runner"]
    if insert:
        argv.append(str(other))

    # Bytes, not ``text=True``: the Windows leg decodes cp1252 there.
    result = subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=120
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8", "replace").strip() == origin
    assert sentinel.exists() is (origin == "project")


def _case_alias(path: Path) -> Path | None:
    """``path`` with its last component in another case, if that names it too."""

    alias = path.with_name(path.name.swapcase())
    try:
        return alias if os.path.samefile(alias, path) else None
    except OSError:
        return None


def test_the_guard_removes_the_cwd_spelled_in_another_case(tmp_path: Path) -> None:
    """A case-insensitive volume (macOS's default): ``normcase`` folds nothing there."""

    cwd = tmp_path / "proj"
    cwd.mkdir()
    alias = _case_alias(cwd)
    if alias is None:
        pytest.skip("this filesystem is case-sensitive")

    assert _removed_by_guard(str(alias), cwd)


async def test_a_module_cached_through_another_case_of_the_cwd_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    alias = _case_alias(cwd)
    if alias is None:
        pytest.skip("this filesystem is case-sensitive")
    agent_dir = tmp_path / "agent"
    (cwd / "aelix_t641_alias.py").write_text(_GENUINE, encoding="utf-8")
    pack = agent_dir / "extensions" / "aliaspack"
    pack.mkdir(parents=True)
    (pack / "aelix-plugin.toml").write_text(
        manifest_toml("aliaspack", entry="aelix_t641_alias:setup"), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(alias))
    _launched_with(monkeypatch, alias)
    monkeypatch.delitem(sys.modules, "aelix_t641_alias", raising=False)
    importlib.invalidate_caches()
    importlib.import_module("aelix_t641_alias")
    try:
        result = await discover_and_load_extensions(
            [], cwd=cwd, agent_dir=agent_dir, no_project_local=True
        )
    finally:
        sys.modules.pop("aelix_t641_alias", None)

    assert result.extensions == []
    [error] = result.errors
    assert "already imported from the project directory" in error.error
