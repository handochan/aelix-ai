"""#131 (ADR-0255) — a catalog entry's ``source`` is placed by the catalog, not the cwd.

Before the fix ``discover install`` handed an entry's raw ``source`` to the
installer, which reads a relative path (``./acme``, ``file:acme``) from the
process working directory — and labelled one that did not exist there a
package. A relative ``./acme`` in a local catalog therefore installed the
directory beside the catalog from one cwd and whatever ``./acme`` the cwd held
from every other (cwd substitution); a bare ``acme`` meant as that directory
went to the package index — the shapes #131 names. These rows run the real CLI
handlers from a cwd that is NOT the catalog's directory, with an injected pip
runner that records the argv (no pip, no network), and an isolated agent dir.

Decisions pinned here (round 3: an ALLOWLIST — refuse, never rewrite):

* accepted: (A) a package requirement without a direct reference (unchanged, to
  the index); (B) an absolute URL — https / http / git+ / git:// / ssh:// /
  git@host:path / file:/// / file://localhost/ — or ``name @ <https, http, git+
  or absolute file URL>``, passed through BYTE-IDENTICAL (fragments, extras);
  (C) an absolute path, a ``~`` path, a ``./`` / ``../`` path or a bare archive
  file name, optionally with ``[extras]``;
* a relative (C) in a LOCAL catalog resolves against that catalog FILE's
  PHYSICAL directory, never the cwd (``file://`` and bare-path locations alike, a
  symlinked catalog included). The cwd (``elsewhere/``) holds a competing copy of
  every relative source, so a resolver that consulted the cwd first would install
  it and fail these rows; ``link/..`` follows the link; ``[extras]`` are checked
  without and handed on with;
* a relative (C) in a catalog with no local base (``https``, git, a location
  that is itself relative) is refused, naming the entry and catalog; an absolute
  or ``~`` path is taken from any catalog;
* REFUSED, with the accepted forms listed: ``name @ <relative path or bare
  word>``, every non-absolute ``file:`` URL, a source starting with ``-``, a
  relative path without ``./`` / ``../``, and a bare name that also sits beside
  a local catalog; a package spec that names a file in the cwd (which the
  installer would install instead) is refused too;
* a path and its ``[extras]`` reach the installer, the verify-and-stage copy and
  the pin as two values (``ResolvedPath``) — a sibling literally named
  ``x.whl[feature]`` changes nothing, as for pip and uv (round-3 review, P1);
* a bare archive name is any name ``scan_artifacts`` lists (spaces included), and
  the archive suffixes are pip's whole list (``.tar.lz`` included);
* every path shown before consent, and every verify line after it, is
  terminal-safe;
* a relative catalog LOCATION is anchored at refresh, with a notice, and stays
  selectable by ``--catalog <the spec as registered>``; a path ``--catalog``
  also selects the local catalog file it names (a relative one from the cwd, an
  absolute one through a symlink); "registered" is asked of the registered
  sources, not the cache — a registered catalog with no cached copy, or whose last
  refresh failed, is named as such, never "matches no registered catalog";
* what the user types on the CLI (``extension install ./x``) keeps its meaning.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import extension_catalog as ec
from aelix_coding_agent.cli import extension_install as ei
from aelix_coding_agent.cli.extension_install import run_extension_command_async

from tests.env_sandbox import sandbox_home


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """``catdir/`` holds the catalog and a local pack; the cwd is ``elsewhere/``.

    ``elsewhere/`` (and ``tmp_path`` for ``../``) hold a COMPETING copy of every
    relative source the rows use: a resolver that looked in the cwd first, or fell
    back to it, finds one and installs it (round-1 review: such a mutant passed).
    """

    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", "")
    catdir = tmp_path / "catdir"
    (catdir / "local-ext").mkdir(parents=True)
    (catdir / "local-ext" / "pyproject.toml").write_text(
        '[project]\nname = "local-ext"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (catdir / "local_ext-0.1.0-py3-none-any.whl").write_bytes(b"not really a wheel")
    sibling = tmp_path / "sibling" / "ext"
    sibling.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for decoy in ("local-ext", "gone-ext", "real-ext"):
        (elsewhere / decoy).mkdir()
        (elsewhere / decoy / "pyproject.toml").write_text(
            f'[project]\nname = "{decoy}"\nversion = "6.6.6"\n', encoding="utf-8"
        )
    (elsewhere / "local_ext-0.1.0-py3-none-any.whl").write_bytes(b"the cwd's decoy")
    (tmp_path / "local-ext").mkdir()  # what ../local-ext names from elsewhere/
    monkeypatch.chdir(elsewhere)
    return {"root": tmp_path, "catdir": catdir, "elsewhere": elsewhere, "sibling": sibling}


def _no_decoy(layout: dict[str, Path], argv: list[str]) -> None:
    """Nothing the runner got points into the cwd (or at the ``../`` decoy)."""

    joined = " ".join(argv)
    assert str(layout["elsewhere"]) not in joined
    assert str(layout["elsewhere"].resolve()) not in joined
    assert str((layout["root"] / "local-ext").resolve()) not in argv


def _uri(path: Path | str, extras: str = "", name: str = "") -> str:
    """What the installer gets for a catalog path (review round 5): the resolved path
    as a ``file://`` URI — ``name[extras] @ <uri>`` when the entry asks for extras —
    never the bare path string a backend would parse again."""

    uri = Path(path).as_uri()
    return f"{name}{extras} @ {uri}" if extras else uri


def _symlink(link: Path, target: Path, *, is_dir: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError as exc:  # pragma: no cover — Windows without symlink rights
        pytest.skip(f"cannot create a symlink here: {exc}")


def _wheel(directory: Path, name: str, version: str) -> Path:
    """A minimally valid wheel carrying real core metadata (the index scan reads it)."""

    dist = name.replace("-", "_")
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"{dist}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        zf.writestr(f"{dist}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return path


def _write_catalog(path: Path, entries: list[dict[str, object]]) -> None:
    doc = {"schemaVersion": 1, "name": "private", "extensions": entries}
    path.write_text(json.dumps(doc), encoding="utf-8")


async def _register_and_refresh(location: str) -> SettingsManager:
    mem = SettingsManager.in_memory({"extensionSources": [{"spec": location, "kind": "catalog"}]})
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    return mem


def _seed(root: Path, location: str, entries: list[tuple[str, str]]) -> SettingsManager:
    """Write the merged cache directly — for locations no test can fetch (https, git)."""

    ec.save_catalogs(
        [
            ec.Catalog(
                location=location,
                name="remote",
                entries=tuple(
                    ec.CatalogEntry(name=n, source=s, catalog_name="remote") for n, s in entries
                ),
                fetched_at=ec.now_iso(),
            )
        ],
        ec.cache_file_path(root / "agent"),
    )
    return SettingsManager.in_memory()


async def _install(mem: SettingsManager, name: str) -> tuple[int, _FakeRunner]:
    runner = _FakeRunner()
    code = await run_extension_command_async(
        ["discover", "install", name, "--yes", "--no-verify"],
        settings=mem,
        runner=runner,
    )
    return code, runner


# === a LOCAL catalog: relative resolves against the catalog file ===========


async def test_dot_relative_source_resolves_beside_a_file_url_catalog(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": "./local-ext"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "local-ext")

    assert code == 0
    out = capsys.readouterr().out
    want = str((layout["catdir"] / "local-ext").resolve())
    assert len(runner.calls) == 1
    assert _uri(want) in runner.calls[0]
    assert "./local-ext" not in runner.calls[0]
    _no_decoy(layout, runner.calls[0])
    assert f"Install extension from path: {want}" in out
    assert "from pypi" not in out
    # The Resolved line shows both: the ABSOLUTE spec that installs, and what the
    # catalog said (round-1 S23: a Resolved line still showing the raw source).
    assert (
        f"Resolved local-ext -> {want} (from catalog private; the catalog says './local-ext')"
        in out
    )


async def test_parent_relative_source_resolves_beside_a_bare_path_catalog(
    layout: dict[str, Path],
) -> None:
    sub = layout["catdir"] / "sub"
    sub.mkdir()
    cat = sub / "catalog.json"
    # From the cwd (`elsewhere/`), `../local-ext` names nothing; from the catalog's
    # directory it is `catdir/local-ext`.
    _write_catalog(cat, [{"name": "up", "source": "../local-ext"}])
    # A bare absolute path location, the form `source add --catalog <path>` stores.
    mem = await _register_and_refresh(str(cat.resolve()))

    code, runner = await _install(mem, "up")

    assert code == 0
    assert _uri((layout["catdir"] / "local-ext").resolve()) in runner.calls[0]
    _no_decoy(layout, runner.calls[0])


async def test_a_bare_wheel_filename_is_a_path_not_a_package(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The bare filename an older ``index --relative`` emitted (no separator)."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "whl", "source": "local_ext-0.1.0-py3-none-any.whl"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "whl")

    assert code == 0
    want = str((layout["catdir"] / "local_ext-0.1.0-py3-none-any.whl").resolve())
    assert _uri(want) in runner.calls[0]
    _no_decoy(layout, runner.calls[0])
    assert "Install extension from path:" in capsys.readouterr().out


async def test_a_missing_relative_path_is_refused_not_looked_up(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing beside the catalog — present in the cwd, which must not stand in."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "gone", "source": "./gone-ext"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "gone")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "'gone'" in err
    assert "private" in err
    assert "does not exist" in err


async def test_a_missing_absolute_path_is_refused_not_looked_up(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cat = layout["catdir"] / "catalog.json"
    missing = layout["root"] / "nowhere" / "ext"
    _write_catalog(cat, [{"name": "abs", "source": str(missing)}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "abs")

    assert code == 2
    assert runner.calls == []
    assert "does not exist" in capsys.readouterr().err


async def test_an_existing_absolute_path_still_installs(layout: dict[str, Path]) -> None:
    cat = layout["catdir"] / "catalog.json"
    target = layout["sibling"].resolve()
    _write_catalog(cat, [{"name": "abs", "source": str(target)}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "abs")

    assert code == 0
    assert _uri(target) in runner.calls[0]


async def test_a_home_relative_source_expands(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox_home(monkeypatch, layout["root"])
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "home", "source": "~/sibling/ext"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "home")

    assert code == 0
    assert _uri(layout["sibling"].resolve()) in runner.calls[0]


# === no local base: refused, never read from the cwd ========================


@pytest.mark.parametrize(
    ("location", "why"),
    [
        ("https://catalog.example.invalid/catalog.json", "not read from a local directory"),
        ("git+https://git.example.invalid/org/catalog.git", "not read from a local directory"),
        # A cache written before refresh anchored relative locations: the message
        # must say what is true of it, and what fixes it.
        ("catalogs/relative-location.json", "is itself a relative path"),
    ],
)
@pytest.mark.parametrize(
    "source",
    ["./local-ext", "../local-ext", "./local-ext[extra]", "local_ext-0.1.0-py3-none-any.whl"],
)
async def test_a_relative_source_without_a_local_base_is_refused(
    layout: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    location: str,
    why: str,
    source: str,
) -> None:
    mem = _seed(layout["root"], location, [("local-ext", source)])

    code, runner = await _install(mem, "local-ext")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "'local-ext'" in err
    assert "remote" in err  # the catalog's label
    assert "nothing to resolve against" in err
    assert why in err
    if why == "is itself a relative path":
        assert "aelix extension discover --refresh" in err


async def test_a_remote_relative_source_never_picks_up_a_cwd_copy(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inverse confusion: an https catalog's ``./local-ext`` must not install
    whatever ``./local-ext`` the user happens to be standing next to."""
    monkeypatch.chdir(layout["catdir"])
    mem = _seed(
        layout["root"], "https://catalog.example.invalid/c.json", [("local-ext", "./local-ext")]
    )

    code, runner = await _install(mem, "local-ext")

    assert code == 2
    assert runner.calls == []


# === path shapes the round-1 review found (#131 round 2) ===================


async def test_a_dot_slash_source_with_a_scheme_inside_is_still_a_path(
    layout: dict[str, Path],
) -> None:
    """``./https://local-ext`` STARTS like a path, so it is one (pi's ``parseSource``
    agrees): it resolves beside the catalog, never reaching the installer raw."""
    if sys.platform == "win32":
        pytest.skip("':' is not allowed in a Windows directory name")
    (layout["catdir"] / "https:" / "local-ext").mkdir(parents=True)
    (layout["elsewhere"] / "https:" / "local-ext").mkdir(parents=True)  # decoy
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "u", "source": "./https://local-ext"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "u")

    assert code == 0
    assert _uri((layout["catdir"] / "https:" / "local-ext").resolve()) in runner.calls[0]
    assert "./https://local-ext" not in runner.calls[0]
    _no_decoy(layout, runner.calls[0])


async def test_a_dot_slash_source_with_a_scheme_inside_is_refused_from_https(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    mem = _seed(
        layout["root"],
        "https://catalog.example.invalid/c.json",
        [("u", "./https://local-ext")],
    )

    code, runner = await _install(mem, "u")

    assert code == 2
    assert runner.calls == []
    assert "nothing to resolve against" in capsys.readouterr().err


# === the round-3 allowlist: refused forms (never rewritten) ================

_HASH = "0" * 64
_HTTPS_LOCATION = "https://catalog.example.invalid/catalog.json"

#: (source, a phrase the refusal must carry). Every one of these, handed on, is read
#: by the installer from the PROCESS cwd (uv: ``name @ ./x``, ``name @ x``,
#: ``file:x``, ``file:``) or as an installer option (``-e x``).
_REFUSED = [
    ("local-ext @ ./local-ext", "is a direct reference to './local-ext'"),
    ("local-ext@./local-ext", "is a direct reference to './local-ext'"),
    ("local-ext @ local-ext", "is a direct reference to 'local-ext'"),
    ("local-ext @ ../local-ext", "is a direct reference to '../local-ext'"),
    (
        "local-ext @ ./local_ext-0.1.0-py3-none-any.whl",
        "is a direct reference to './local_ext-0.1.0-py3-none-any.whl'",
    ),
    ("local-ext @ file:local-ext", "is a direct reference to 'file:local-ext'"),
    ("local-ext @ file:", "is a direct reference to 'file:'"),
    (
        f"local-ext @ file:local_ext-0.1.0-py3-none-any.whl#sha256={_HASH}",
        "is a direct reference to 'file:local_ext",
    ),
    ("file:local-ext", "is a relative file: URL — uv reads it from the current directory"),
    # The two shapes uv was measured reading from the cwd, with the wheel beside the
    # local catalog: a bare archive name inside them must not make them paths.
    ("file:local_ext-0.1.0-py3-none-any.whl", "is a relative file: URL"),
    (
        "local-ext @ local_ext-0.1.0-py3-none-any.whl",
        "is a direct reference to 'local_ext-0.1.0-py3-none-any.whl'",
    ),
    ("file:./local-ext", "is a relative file: URL"),
    ("file:", "is a relative file: URL"),
    ("file:.", "is a relative file: URL"),
    ("file:#subdirectory=child", "is a relative file: URL"),
    (f"file:local_ext-0.1.0-py3-none-any.whl#sha256={_HASH}", "is a relative file: URL"),
    ("file:local_ext-0.1.0-py3-none-any.whl[feature]", "is a relative file: URL"),
    # Review round 5 (Codex): 'file:/abs' names no cwd — the message says the
    # spelling is unsupported, not that the installer would read the cwd.
    (
        "file:/abs/local-ext",
        "is a file: URL written with one slash ('file:/…'), a spelling aelix does not "
        "accept — write it with three, file:///<absolute path>",
    ),
    (
        "local-ext @ file:/abs/local_ext-0.1.0-py3-none-any.whl",
        "which is a file: URL written with one slash ('file:/…')",
    ),
    # Round-3 verify (B): host-form file: URLs. uv reads ``file://local-ext`` as
    # ``<cwd>/local-ext``, ``file://localhost.evil/x`` as ``<cwd>/localhost.evil/x``
    # (measured: test_uv_reads_a_scheme_less_direct_reference_from_the_cwd).
    ("file://local-ext", "is a file: URL naming the host 'local-ext'"),
    ("file://local_ext-0.1.0-py3-none-any.whl", "naming the host"),
    ("file://localhost.evil/local-ext", "naming the host 'localhost.evil'"),
    ("file://localhost", "with no absolute path after the host"),
    ("local-ext @ file://local-ext", "is a direct reference to 'file://local-ext'"),
    ("local-ext @ file://./local-ext", "is a direct reference to 'file://./local-ext'"),
    (
        "local-ext @ file://localhost.evil/local-ext",
        "is a direct reference to 'file://localhost.evil/local-ext'",
    ),
    (
        "git+file:local-ext",
        "is a relative git+file: URL, which aelix does not accept — write "
        "git+file:///<absolute path>",
    ),
    ("git+https:///local-ext", "is not an absolute URL — 'git+https:' needs '//' and a host"),
    ("https:local-ext", "is not an absolute URL — 'https:' needs '//' and a host after it"),
    # Review round 7 (verify6 NB): "(a URL needs a host)" was said of URLs that have
    # one; the message now names what is wrong with each.
    (
        "ftp://h.example.invalid/x.whl",
        "uses the URL scheme 'ftp:', which a catalog source may not use",
    ),
    ("svn+https://h.example.invalid/r", "uses the URL scheme 'svn+https:'"),
    (
        "local-ext @ ssh://git@h.example.invalid/o/r.git",
        "uses the URL scheme 'ssh:', which a 'name @' reference may not use — write "
        "'name @ git+ssh://…', or the URL on its own",
    ),
    ("local-ext @ git://h.example.invalid/o/r.git", "which a 'name @' reference may not use"),
    # Review round 7 (Codex 6 P1): the host of a file URL is empty or 'localhost' in
    # lowercase, byte-exact — uv read 'file://LOCALHOST/<abs>' as '<cwd>/LOCALHOST/<abs>'.
    (
        "file://LOCALHOST/abs/local_ext-0.1.0-py3-none-any.whl",
        "is a file: URL naming the host 'LOCALHOST' — aelix accepts the host only empty "
        "or as 'localhost' in lowercase; write file:///<absolute path>",
    ),
    ("file://LocalHost/abs/local-ext", "naming the host 'LocalHost' — aelix accepts"),
    (
        "local-ext @ file://LOCALHOST/abs/local_ext-0.1.0-py3-none-any.whl",
        "which is a file: URL naming the host 'LOCALHOST'",
    ),
    ("git+file://LOCALHOST/abs/repo", "is a git+file: URL naming the host 'LOCALHOST'"),
    # Review round 7 (Codex 6 P1): no percent-escape in a file URL's path — uv decodes
    # '%23' to '#' and cuts there (it installed a sibling 'trusted' for
    # 'trusted%23release').
    (
        "file:///abs/trusted%23release",
        "is a file: URL whose path holds a percent-escape ('%'), which aelix does not "
        "accept: the installer decodes it (uv reads '%23' as '#' and cuts the path "
        "there, opening another one) — name the path directly instead (an absolute "
        "path, or ./<path> beside the catalog)",
    ),
    ("file://localhost/abs/trusted%20", "holds a percent-escape"),
    ("file:///abs/x%2Fy/local_ext-0.1.0-py3-none-any.whl", "holds a percent-escape"),
    (
        "local-ext @ file:///abs/trusted%23release",
        "which is a file: URL whose path holds a percent-escape",
    ),
    (
        "local-ext[feature] @ file:///abs/a%5Bb%5D/local_ext-0.1.0-py3-none-any.whl",
        "holds a percent-escape",
    ),
    # verify round 7 (item 3): git+file: has its own words — git ignores the host,
    # and it is pip, not uv, that cuts a git+file: path at '%23'.
    (
        "git+file:///abs/r%23x",
        "is a git+file: URL whose path holds a percent-escape ('%'), which aelix does "
        "not accept: pip cuts the path at a '%23' and clones another repository — "
        "write git+file:///<absolute path> without escapes",
    ),
    (
        "git+file://h.example.invalid/abs/repo",
        "is a git+file: URL naming the host 'h.example.invalid', an unsupported "
        "spelling — aelix accepts a git+file: host only empty or as 'localhost' in "
        "lowercase; write git+file:///<absolute path>",
    ),
    ("local-ext @ git+file:local-ext", "is a direct reference to 'git+file:local-ext'"),
    # Review round 5 (Codex P1): a scheme not written in lowercase is refused — uv
    # read 'local-ext @ FILE:///<abs>' as the path '<cwd>/FILE:/<abs>'.
    ("FILE:///abs/local_ext-0.1.0-py3-none-any.whl", "writes the URL scheme 'FILE'"),
    (
        "local-ext @ FILE:///abs/local_ext-0.1.0-py3-none-any.whl",
        "writes the URL scheme 'FILE' with upper-case letters — write it in lowercase ('file:')",
    ),
    ("File:///abs/local_ext-0.1.0-py3-none-any.whl", "writes the URL scheme 'File'"),
    ("Https://h.example.invalid/x.whl", "writes the URL scheme 'Https'"),
    ("local-ext @ HTTP://h.example.invalid/x.whl", "writes the URL scheme 'HTTP'"),
    ("GIT+https://h.example.invalid/o/r.git", "writes the URL scheme 'GIT+https'"),
    ("git+HTTPS://h.example.invalid/o/r.git", "writes the URL scheme 'git+HTTPS'"),
    ("local-ext @ Git+https://h.example.invalid/o/r.git", "writes the URL scheme 'Git+https'"),
    ("SSH://h.example.invalid/o/r.git", "writes the URL scheme 'SSH'"),
    ("Git://h.example.invalid/o/r.git", "writes the URL scheme 'Git'"),
    # Review round 5 (verify5 / decision 3): a git repository after 'name @' needs
    # 'git+' — uv clones it, pip downloads it as an archive and fails; the installer
    # used to prefix 'git+' to the NAME, which pip read as a path in the cwd.
    (
        "acme-notes @ https://h.example.invalid/o/r.git",
        "write 'acme-notes @ git+https://h.example.invalid/o/r.git'",
    ),
    ("acme-notes @ http://h.example.invalid/o/r.git/", "a git repository URL without 'git+'"),
    (
        "acme-notes @ https://h.example.invalid/o/r.git@" + "a" * 40,
        "a git repository URL without 'git+'",
    ),
    ("-e local-ext", "starts with '-'"),
    (" -e local-ext", "starts with '-'"),  # the check runs on the stripped source
    # Round-3 review: the installer routes a name ending in .git as git
    # ("git+acme.git"), never to the index (A) promises.
    ("acme.git", "the installer takes a name ending in '.git' for a git URL"),
    # Round-4 verify W17: the .git check is case-insensitive, as classify_target's is.
    ("ACME.GIT", "the installer takes a name ending in '.git' for a git URL"),
    ("--index-url=https://evil.example.invalid/simple", "starts with '-'"),
    ("sub/local-ext", "is neither a package name"),
    # A separator makes it no bare archive name: a relative path needs ./ (guide).
    ("wheels/local_ext-0.1.0-py3-none-any.whl", "is neither a package name"),
    # Round-4 verify W09: so does a backslash (a separator on Windows) — no bare name.
    ("wheels\\local_ext-0.1.0-py3-none-any.whl", "is neither a package name"),
    (".", "is neither a package name"),
    ("local-ext; python_version >= '3'", "is neither a package name"),
]


@pytest.mark.parametrize("catalog_kind", ["local", "https"])
@pytest.mark.parametrize(("source", "why"), _REFUSED)
async def test_a_source_outside_the_allowlist_is_refused_never_rewritten(
    layout: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    catalog_kind: str,
    source: str,
    why: str,
) -> None:
    """Round-2 review: rewriting relative forms dropped ``#sha256`` /
    ``#subdirectory`` fragments and missed ``name @ ./x`` and ``file:`` — uv
    then read them from the cwd. Now nothing is rewritten: a form outside the
    allowlist is refused before consent, from a local catalog AND an https one,
    with the entry, the catalog and the accepted forms named."""
    if catalog_kind == "local":
        cat = layout["catdir"] / "catalog.json"
        _write_catalog(cat, [{"name": "e", "source": source}])
        mem = await _register_and_refresh(cat.as_uri())
        label = "private"
    else:
        mem = _seed(layout["root"], _HTTPS_LOCATION, [("e", source)])
        label = "remote"

    code, runner = await _install(mem, "e")

    assert code == 2
    assert runner.calls == []
    captured = capsys.readouterr()
    assert "Install extension from" not in captured.out
    assert "Resolved" not in captured.out
    err = captured.err
    assert f"catalog entry 'e' (catalog '{label}')" in err
    assert f"source '{source}'" in err
    assert why in err
    assert ec.ACCEPTED_SOURCE_FORMS in err.replace("\n", " ")


# === the round-3 allowlist: accepted forms, passed exactly ==================


#: (B) and (A) forms; ``{uri}`` / ``{posix}`` stand for the pack beside the catalog.
#: Every one must reach the installer BYTE-IDENTICAL — fragments, extras, marker.
_ACCEPTED = [
    "foo-pkg",
    "foo-pkg==1.2",
    "foo-pkg[extra]>=1,<2",
    f"https://h.example.invalid/x-1.0-py3-none-any.whl#sha256={_HASH}",
    f"x @ https://h.example.invalid/x-1.0-py3-none-any.whl#sha256={_HASH}",
    "x[feature] @ https://h.example.invalid/x-1.0.zip#subdirectory=child&egg=x",
    "x @ https://h.example.invalid/x-1.0.zip ; python_version >= '3'",
    "git+https://h.example.invalid/r.git@" + "b" * 40 + "#subdirectory=child",
    "x @ git+https://h.example.invalid/r.git@" + "b" * 40 + "#egg=x",
    "git+ssh://git@h.example.invalid/o/r.git",
    "{uri}#sha256=" + _HASH,
    "local-ext[feature] @ {uri}#subdirectory=child",
    "file://localhost{posix}",
    # Round-3 review (Codex cat 4): plain http is an accepted ENTRY source (the
    # catalog-location TLS rule does not apply to it); an https-only check passed
    # every earlier row.
    "http://h.example.invalid/x-1.0-py3-none-any.whl",
    f"x @ http://h.example.invalid/x-1.0-py3-none-any.whl#sha256={_HASH}",
]


@pytest.mark.parametrize("catalog_kind", ["local", "https"])
@pytest.mark.parametrize("template", _ACCEPTED)
async def test_an_accepted_url_or_package_source_is_passed_exactly(
    layout: dict[str, Path], catalog_kind: str, template: str
) -> None:
    target = (layout["catdir"] / "local-ext").resolve()
    if "{posix}" in template and os.name == "nt":
        pytest.skip("file://localhost + a drive path is a POSIX-only spelling")
    source = template.replace("{uri}", target.as_uri()).replace("{posix}", target.as_posix())
    if catalog_kind == "local":
        cat = layout["catdir"] / "catalog.json"
        _write_catalog(cat, [{"name": "e", "source": source}])
        mem = await _register_and_refresh(cat.as_uri())
    else:
        mem = _seed(layout["root"], _HTTPS_LOCATION, [("e", source)])

    code, runner = await _install(mem, "e")

    assert code == 0
    assert runner.calls[0][-1] == source
    _no_decoy(layout, runner.calls[0])


@pytest.mark.parametrize(
    ("source", "argv"),
    [
        ("git://h.example.invalid/o/r.git", "git+git://h.example.invalid/o/r.git"),
        ("ssh://git@h.example.invalid/o/r.git", "git+ssh://git@h.example.invalid/o/r.git"),
        ("https://h.example.invalid/o/r.git", "git+https://h.example.invalid/o/r.git"),
    ],
)
async def test_a_bare_git_transport_is_re_prefixed_by_the_installer(
    layout: dict[str, Path], source: str, argv: str
) -> None:
    """What the guide's (B) row states: the installer (not the resolver) gives
    ``git://``, ``ssh://`` and a URL whose path ends in ``.git`` the ``git+`` pip and
    uv need, exactly as for a typed target (round-3 verify)."""
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("g", source)])

    code, runner = await _install(mem, "g")

    assert code == 0
    assert runner.calls[0][-1] == argv


async def test_an_scp_git_source_is_accepted(layout: dict[str, Path]) -> None:
    """``git@host:path`` names a remote, never the cwd; the installer's own
    ``_normalize_git_spec`` turns it into ``git+ssh://`` exactly as for a typed one."""
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("g", "git@h.example.invalid:o/r.git")])

    code, runner = await _install(mem, "g")

    assert code == 0
    assert runner.calls[0][-1] == "git+ssh://git@h.example.invalid/o/r.git"


@pytest.mark.parametrize("catalog_kind", ["local", "https", "git"])
async def test_an_absolute_or_home_path_is_taken_from_any_catalog(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch, catalog_kind: str
) -> None:
    """ADR-0255 (C): an absolute or ``~`` path names no cwd and no catalog
    directory, so a served catalog may carry one too (a shared mount); it must
    exist, and installs as the resolved absolute path (CHANGELOG says exactly this)."""
    sandbox_home(monkeypatch, layout["root"])
    absolute = str(layout["sibling"].resolve())
    entries = [("abs", absolute), ("home", "~/sibling/ext"), ("gone", "/nowhere/131/ext")]
    if catalog_kind == "local":
        cat = layout["catdir"] / "catalog.json"
        _write_catalog(cat, [{"name": n, "source": s} for n, s in entries])
        mem = await _register_and_refresh(cat.as_uri())
    else:
        location = (
            _HTTPS_LOCATION
            if catalog_kind == "https"
            else "git+https://git.example.invalid/org/catalog.git"
        )
        mem = _seed(layout["root"], location, entries)

    for name in ("abs", "home"):
        code, runner = await _install(mem, name)
        assert code == 0, name
        assert runner.calls[0][-1] == _uri(absolute), name
    code, runner = await _install(mem, "gone")
    assert code == 2
    assert runner.calls == []


#: Every suffix ``_ARCHIVE_SUFFIXES`` declares, written out here on purpose: a
#: three-suffix list passed every round-2 row (Codex category 4).
_ARCHIVE_SUFFIXES_DECLARED = (
    ".whl",
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar",
    ".tar.bz2",
    ".tbz",
    ".tar.xz",
    ".txz",
    ".tlz",
    ".tar.lz",
    ".tar.lzma",
)


@pytest.mark.parametrize("suffix", _ARCHIVE_SUFFIXES_DECLARED)
async def test_a_bare_archive_name_is_a_path_for_every_declared_suffix(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], suffix: str
) -> None:
    """A bare ``ext-1.0<suffix>`` beside a local catalog installs THAT file; a
    shorter suffix list reads it as a package name (refused here as ambiguous,
    or sent to the index from a served catalog)."""
    artifact = layout["catdir"] / f"ext-1.0{suffix}"
    artifact.write_bytes(b"archive")
    (layout["elsewhere"] / artifact.name).write_bytes(b"the cwd's decoy")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "a", "source": artifact.name}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "a")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri(artifact.resolve())
    _no_decoy(layout, runner.calls[0])
    assert ec.split_path_extras(artifact.name)[0].lower().endswith(ec._ARCHIVE_SUFFIXES)


def test_the_declared_archive_suffixes_are_the_ones_the_rows_cover() -> None:
    """A suffix added to the module without a row here fails this one."""
    assert ec._ARCHIVE_SUFFIXES == _ARCHIVE_SUFFIXES_DECLARED


@pytest.mark.parametrize("suffix", [".tlz", ".tar.lz", ".tar.lzma"])
async def test_a_bare_lzip_or_lzma_archive_from_https_is_refused_not_sent_to_uv(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], suffix: str
) -> None:
    """Round-3 verify: pip's ARCHIVE_EXTENSIONS and uv's looks_like_archive both read
    these three as files, so (A) "to the index" was false for them — uv opened
    ``x.tar.lz`` from the cwd. Path-shaped now: refused from a served catalog."""
    (layout["elsewhere"] / f"x{suffix}").write_bytes(b"the cwd's decoy")
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("z", f"x{suffix}")])

    code, runner = await _install(mem, "z")

    assert code == 2
    assert runner.calls == []
    assert "nothing to resolve against" in capsys.readouterr().err


@pytest.mark.parametrize(
    "filename",
    [
        "team notes-1.0.tar.gz",  # the round-3 review's sdist, uv installs it
        "acme notes (beta)-1.0-py3-none-any.whl",
        "ünïcode_pack-1.0-py3-none-any.whl",
        "x+y=z~1.0.tar.gz",
    ],
)
async def test_a_bare_archive_name_the_old_generator_wrote_still_installs(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], filename: str
) -> None:
    """``scan_artifacts`` lists ANY file whose name ends in an indexed suffix, and the
    old ``index --relative`` wrote that name bare. Round 3's character whitelist
    refused the spaced one (round-3 review, P2); the rule is now scan_artifacts'
    own — no separator, an archive suffix — minus names that read as a URL or a
    ``name @`` reference (still refused: ``file:x.whl``, ``name @ x.whl``)."""
    artifact = layout["catdir"] / filename
    artifact.write_bytes(b"archive")
    (layout["elsewhere"] / filename).write_bytes(b"the cwd's decoy")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "a", "source": filename}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "a")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri(artifact.resolve())
    _no_decoy(layout, runner.calls[0])


async def test_the_old_generator_output_for_a_spaced_sdist_installs(
    layout: dict[str, Path],
) -> None:
    """The exact document the pre-#131 generator produced (``relative_to`` = the
    scan directory, ``str(path.relative_to(...))``): a bare spaced file name."""
    sdist = layout["catdir"] / "team notes-1.0.tar.gz"
    pkg_info = b"Metadata-Version: 2.1\nName: team-notes\nVersion: 1.0\n"
    member = tarfile.TarInfo("team notes-1.0/PKG-INFO")
    member.size = len(pkg_info)
    with tarfile.open(sdist, "w:gz") as tf:
        tf.addfile(member, io.BytesIO(pkg_info))
    (artifact,) = ec.scan_artifacts(layout["catdir"])
    old_source = str(artifact.path.resolve().relative_to(layout["catdir"].resolve()))
    assert old_source == "team notes-1.0.tar.gz"
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "team-notes", "source": old_source}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "team-notes")

    assert code == 0
    assert runner.calls[0][-1] == _uri(sdist.resolve())


async def test_a_resolved_path_is_shown_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-2 review: ``./link`` holds no control byte, but its symlink TARGET's
    name did, and the resolved path reached the Resolved and Install lines raw (an
    OSC 52 clipboard write) before consent. Shown through ``safe_for_terminal``;
    the path the installer gets is the real one."""
    if sys.platform == "win32":
        pytest.skip("control characters are not allowed in a Windows file name")
    evil = layout["root"] / "evil\x1b]52;c;cHduZWQ=\x07dir"
    evil.mkdir()
    _symlink(layout["catdir"] / "link", evil, is_dir=True)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "s", "source": "./link"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "s")

    assert code == 0
    assert runner.calls[0][-1] == _uri(evil.resolve())
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        assert "\x1b" not in stream
        assert "\x07" not in stream
    safe_dir = str(evil.resolve()).replace("\x1b", "").replace("\x07", "")
    assert f"Resolved s -> {safe_dir} (from catalog private" in captured.out
    assert f"Install extension from path: {safe_dir}" in captured.out


async def test_the_resolved_line_shows_what_the_catalog_said_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``the catalog says '…'`` half of the Resolved line quotes the raw source.
    The parser drops a source holding a C0/C1 control byte, but not a BiDi override
    (U+202E), which re-orders what the reader sees (round-3 verify V16)."""
    name = "local\u202eext"
    (layout["catdir"] / name).mkdir()
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "b", "source": f"./{name}"}])
    mem = await _register_and_refresh(cat.as_uri())
    capsys.readouterr()

    code, runner = await _install(mem, "b")

    assert code == 0
    assert runner.calls[0][-1] == _uri((layout["catdir"] / name).resolve())
    out = capsys.readouterr().out
    assert "\u202e" not in out
    assert "the catalog says './localext')" in out


@pytest.mark.parametrize("doc_name", [None, "private"])
async def test_a_refusal_naming_the_catalog_directory_is_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], doc_name: str | None
) -> None:
    """A refusal quotes the catalog's directory (the missing candidate path) and,
    for a catalog with no ``name``, its location as the label. A control byte in
    that directory's name never reaches the screen raw. (A control byte in the
    ``source`` itself never gets this far: the parser skips such an entry.)"""
    if sys.platform == "win32":
        pytest.skip("control characters are not allowed in a Windows file name")
    catdir = layout["root"] / "cat\x1b]52;c;eA==\x07dir"
    catdir.mkdir()
    cat = catdir / "catalog.json"
    doc: dict[str, object] = {
        "schemaVersion": 1,
        "extensions": [
            {"name": "gone", "source": "./gone"},
            {"name": "direct", "source": "x @ ./x"},
        ],
    }
    if doc_name:
        doc["name"] = doc_name
    cat.write_text(json.dumps(doc), encoding="utf-8")
    mem = await _register_and_refresh(str(cat))
    capsys.readouterr()

    for name, why in (("gone", "does not exist"), ("direct", "is a direct reference")):
        code, runner = await _install(mem, name)
        assert code == 2
        assert runner.calls == []
        err = capsys.readouterr().err
        assert why in err
        if name == "gone" or doc_name is None:  # quoted: the path, or the label
            assert "]52;c;eA==" in err  # the text survives, inert
        assert "\x1b" not in err
        assert "\x07" not in err


def _uv_dry_run(uv: str, spec: str, cwd: Path, scratch: Path) -> str:
    env = {**os.environ, "UV_CACHE_DIR": str(scratch / "uv-cache"), "UV_NO_CONFIG": "1"}
    proc = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--dry-run",
            "--offline",
            "--no-deps",
            "--target",
            str(scratch / "target"),
            "--python",
            sys.executable,
            spec,
        ],
        cwd=cwd,
        capture_output=True,
        env=env,
        timeout=120,
    )
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")


def test_uv_reads_a_scheme_less_direct_reference_from_the_cwd(
    layout: dict[str, Path], tmp_path: Path
) -> None:
    """WHY ``name @ ./x``, ``name @ x`` and ``file:x`` are refused, measured on the
    supported uv backend: it opens them from the PROCESS cwd — the decoy in
    ``elsewhere/``, never the copy beside the catalog — with no error."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("no `uv` on PATH; the installer measurement needs it")
    for directory in (layout["catdir"], layout["elsewhere"]):
        (directory / "local_ext-0.1.0-py3-none-any.whl").unlink()
        _wheel(directory, "local-ext", "0.1.0")
    whl = "local_ext-0.1.0-py3-none-any.whl"
    for spec in (
        f"local-ext @ ./{whl}",
        f"local-ext @ {whl}",
        f"file:{whl}",
        f"file://{whl}",  # round-3 verify: a host-form file URL, read as <cwd>/<host>
        f"local-ext @ file://{whl}",
    ):
        out = _uv_dry_run(uv, spec, layout["elsewhere"], tmp_path)
        assert "Would install 1 package" in out, (spec, out)
        assert "elsewhere" in out, (spec, out)
        assert "catdir" not in out, (spec, out)


def test_uv_splits_path_extras_even_beside_a_literal_bracketed_sibling(
    layout: dict[str, Path], tmp_path: Path
) -> None:
    """WHY the installer must not prefer a sibling literally named
    ``x.whl[feature]``: uv (like pip's ``_strip_extras``) splits the trailing group
    whatever exists and opens ``x.whl`` — measured with the sibling a symlink to a
    different wheel (round-3 review, P1)."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("no `uv` on PATH; the installer measurement needs it")
    good = _wheel(layout["catdir"], "acme-notes", "1.0")
    bad = _wheel(layout["elsewhere"], "acme-notes", "9.0")
    _symlink(layout["catdir"] / f"{good.name}[feature]", bad, is_dir=False)

    out = _uv_dry_run(uv, f"{good}[feature]", layout["elsewhere"], tmp_path)

    assert "Would install 1 package" in out, out
    assert "acme-notes==1.0" in out or good.name in out, out
    assert bad.name not in out, out


# === the extras travel apart from the path (round-3 review, P1) ============


def _collision(layout: dict[str, Path]) -> tuple[Path, Path]:
    """``catdir/x-1.0.whl`` (the real one) and, beside it, a symlink LITERALLY named
    ``x-1.0.whl[feature]`` pointing at a 9.0 wheel in the cwd."""
    good = _wheel(layout["catdir"], "acme-notes", "1.0")
    bad = _wheel(layout["elsewhere"], "acme-notes", "9.0")
    _symlink(layout["catdir"] / f"{good.name}[feature]", bad, is_dir=False)
    return good, bad


async def test_a_literal_bracketed_sibling_does_not_redirect_an_unverified_install(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    good, bad = _collision(layout)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "x", "source": f"./{good.name}[feature]"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "x")

    assert code == 0
    want = f"{good.resolve()}[feature]"
    assert runner.calls[0][-1] == _uri(good.resolve(), "[feature]", "acme_notes")
    assert bad.name not in " ".join(runner.calls[0])
    out = capsys.readouterr().out
    assert f"Resolved x -> {want} (from catalog private" in out
    assert f"Install extension from path: {want}" in out


async def test_a_literal_bracketed_sibling_does_not_redirect_verification_or_the_pin(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Default verification hashes, stages and pins the wheel the Resolved line
    names — not the sibling — and the extras go back on the staged argv."""
    good, bad = _collision(layout)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "x", "source": f"./{good.name}[feature]"}])
    mem = await _register_and_refresh(cat.as_uri())
    runner = _FakeRunner()

    code = await run_extension_command_async(
        ["discover", "install", "x", "--yes"], settings=mem, runner=runner
    )

    assert code == 0
    staged = runner.calls[0][-1]
    # The staged COPY, by URI, with the extras in PEP 508 form (review round 5).
    assert staged.startswith("acme_notes[feature] @ file://")
    assert staged.endswith(f"/{good.name}")
    assert good.resolve().as_uri() not in staged
    out = capsys.readouterr().out
    assert f"first acquisition of path {good.name} " in out
    assert bad.name not in out
    pins = json.loads((layout["root"] / "agent" / "extension_pins.json").read_text("utf-8"))
    assert list(pins["pins"]) == [str(good.resolve())]
    assert pins["pins"][str(good.resolve())]["sha256"] == ei.extension_pins.sha256_file(good)


async def test_a_literal_bracketed_sibling_directory_does_not_redirect(
    layout: dict[str, Path],
) -> None:
    (layout["catdir"] / "tree").mkdir()
    (layout["catdir"] / "tree" / "pyproject.toml").write_text(
        '[project]\nname = "tree-pkg"\nversion = "1.0"\n', encoding="utf-8"
    )
    (layout["elsewhere"] / "evil-tree").mkdir()
    _symlink(layout["catdir"] / "tree[extra]", layout["elsewhere"] / "evil-tree", is_dir=True)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "t", "source": "./tree[extra]"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "t")

    assert code == 0
    assert runner.calls[0][-1] == _uri((layout["catdir"] / "tree").resolve(), "[extra]", "tree-pkg")
    _no_decoy(layout, runner.calls[0])


async def test_discover_install_never_re_splits_a_catalog_path(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path and its extras reach the installer, the verify-and-stage copy, the
    pin and the install record as the resolver split them: the typed-target
    re-split (``_path_extras``) is never asked about a catalog path."""
    whl = _wheel(layout["catdir"], "acme-notes", "1.4.0")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "x", "source": f"./{whl.name}[feature]"}])
    mem = await _register_and_refresh(cat.as_uri())
    asked: list[str] = []

    def _spy(target: str) -> tuple[str, str]:
        asked.append(target)
        return target, ""

    monkeypatch.setattr(ei, "_path_extras", _spy)
    runner = _FakeRunner()

    code = await run_extension_command_async(
        ["discover", "install", "x", "--yes"], settings=mem, runner=runner
    )

    assert code == 0
    assert asked == []
    assert runner.calls[0][-1].startswith("acme_notes[feature] @ file://")
    assert runner.calls[0][-1].endswith(f"/{whl.name}")
    pins = json.loads((layout["root"] / "agent" / "extension_pins.json").read_text("utf-8"))
    assert list(pins["pins"]) == [str(whl.resolve())]
    # Since review round 7 the record is the installer URI itself, so ``update``
    # hands over exactly what was installed.
    recorded = [s.spec for s in mem.get_extension_sources() if s.kind == "path"]
    assert recorded == [f"acme_notes[feature] @ {whl.resolve().as_uri()}"]


def test_the_resolver_hands_over_the_path_and_the_extras_apart(
    layout: dict[str, Path],
) -> None:
    good, _ = _collision(layout)
    entry = ec.CatalogEntry(
        name="x",
        source=f"./{good.name}[feature]",
        catalog_location=(layout["catdir"] / "catalog.json").as_uri(),
    )

    target = ec.resolve_entry_target(entry)

    assert target == ec.ResolvedPath(str(good.resolve()), "[feature]")
    assert ec.resolve_entry_source(entry) == (f"{good.resolve()}[feature]", True)
    # The installer's helpers take the two values as given — no re-split.
    assert ei.classify_target(target) == "path"
    assert ei._install_spec(target, "path") == f"{good.resolve()}[feature]"
    assert ei._pin_identity(target, "path") == str(good.resolve())
    assert ei._target_source_key(target, "path") == os.path.realpath(good)
    assert ei._target_dist_hint(target, "path") == "acme_notes"


def test_a_typed_target_splits_its_extras_as_the_backend_does(
    layout: dict[str, Path],
) -> None:
    """The typed reading agrees with pip and uv too: the literal sibling no longer
    wins the split, so the file hashed and pinned is the one installed."""
    good = _wheel(layout["elsewhere"], "acme-notes", "1.0")
    bad = _wheel(layout["root"], "acme-notes", "9.0")
    _symlink(layout["elsewhere"] / f"{good.name}[feature]", bad, is_dir=False)
    typed = f"./{good.name}[feature]"

    assert ei._path_extras(typed) == (f"./{good.name}", "[feature]")
    assert ei._pin_identity(typed, "path") == str(good.resolve())
    assert ei._install_spec(typed, "path") == f"{good.resolve()}[feature]"


async def test_a_path_that_resolves_to_a_bracketed_name_is_handed_over_as_named(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``./link`` -> ``dir[x]``: as the path string ``<abs>/dir[x]``, pip and uv read
    ``[x]`` as extras and open ``<abs>/dir`` — another path; round 3 refused it.
    Since review round 5 the installer gets the URI, whose ``%5Bx%5D`` both
    backends open literally, so it installs ``dir[x]`` itself."""
    (layout["root"] / "dir").mkdir()
    (layout["root"] / "dir[x]").mkdir()
    _symlink(layout["catdir"] / "link", layout["root"] / "dir[x]", is_dir=True)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "b", "source": "./link"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "b")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri((layout["root"] / "dir[x]").resolve())
    assert "%5Bx%5D" in runner.calls[0][-1]


async def test_a_bare_archive_name_with_extras_is_a_path(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-3 verify V08: ``x.whl[feature]`` — a bare archive name with pip's extras."""
    whl = _wheel(layout["catdir"], "acme-notes", "1.4.0")
    (layout["elsewhere"] / whl.name).write_bytes(b"the cwd's decoy")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "b", "source": f"{whl.name}[feature]"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "b")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri(whl.resolve(), "[feature]", "acme_notes")
    _no_decoy(layout, runner.calls[0])


# === every install / verify line is terminal-safe (round-3 review) =========


async def test_the_first_acquisition_line_is_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``./clean.tar.gz`` -> a file whose name holds an OSC 52 sequence: the
    post-consent verify status line printed the target's basename raw (Codex P2)."""
    if sys.platform == "win32":
        pytest.skip("control characters are not allowed in a Windows file name")
    evil = layout["root"] / "evil\x1b]52;c;cHduZWQ=\x07.tar.gz"
    evil.write_bytes(b"an sdist")
    _symlink(layout["catdir"] / "clean.tar.gz", evil, is_dir=False)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "o", "source": "./clean.tar.gz"}])
    mem = await _register_and_refresh(cat.as_uri())
    runner = _FakeRunner()

    code = await run_extension_command_async(
        ["discover", "install", "o", "--yes"], settings=mem, runner=runner
    )

    assert code == 0
    # the real name reaches the installer, percent-encoded in its file:// URI
    assert runner.calls[0][-1].endswith(Path("/" + evil.name).as_uri()[len("file://") :])
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        assert "\x1b" not in stream
        assert "\x07" not in stream
    assert "first acquisition of path evil]52;c;cHduZWQ=.tar.gz" in captured.out


async def test_a_verify_refusal_is_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A changed artifact under an existing pin: the refusal quotes its name."""
    if sys.platform == "win32":
        pytest.skip("control characters are not allowed in a Windows file name")
    evil = layout["root"] / "evil\x1b]52;c;cHduZWQ=\x07.tar.gz"
    evil.write_bytes(b"first bytes")
    _symlink(layout["catdir"] / "clean.tar.gz", evil, is_dir=False)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "o", "source": "./clean.tar.gz"}])
    mem = await _register_and_refresh(cat.as_uri())
    flags = ["discover", "install", "o", "--yes"]
    assert await run_extension_command_async(flags, settings=mem, runner=_FakeRunner()) == 0
    evil.write_bytes(b"changed bytes")
    capsys.readouterr()
    runner = _FakeRunner()

    code = await run_extension_command_async(flags, settings=mem, runner=runner)

    assert code == 2
    assert runner.calls == []
    captured = capsys.readouterr()
    assert "Verification refused" in captured.err
    assert "evil]52;c;cHduZWQ=.tar.gz" in captured.err
    for stream in (captured.out, captured.err):
        assert "\x1b" not in stream
        assert "\x07" not in stream


async def test_the_no_backend_message_is_terminal_safe(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Round-3 verify V13: printing the no-backend message raw passed every row."""
    if sys.platform == "win32":
        pytest.skip("control characters are not allowed in a Windows file name")
    evil = layout["root"] / "evil\x1b]52;c;eA==\x07dir"
    evil.mkdir()
    _symlink(layout["catdir"] / "link", evil, is_dir=True)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "n", "source": "./link"}])
    mem = await _register_and_refresh(cat.as_uri())
    monkeypatch.setattr(ei, "detect_install_backend", lambda: None)

    code = await run_extension_command_async(
        ["discover", "install", "n", "--yes", "--no-verify"], settings=mem, runner=None
    )

    assert code == 2
    captured = capsys.readouterr()
    assert "no usable package installer" in captured.err
    assert "evil]52;c;eA==dir" in captured.err
    for stream in (captured.out, captured.err):
        assert "\x1b" not in stream
        assert "\x07" not in stream


async def test_the_anchoring_notice_is_terminal_safe(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Round-3 verify V15: the refresh notice quotes the cwd it anchored at."""
    if sys.platform == "win32":
        pytest.skip("control characters are not allowed in a Windows file name")
    catdir = layout["root"] / "cat\x1b]52;c;eA==\x07dir"
    catdir.mkdir()
    _write_catalog(catdir / "catalog.json", [{"name": "e", "source": "./x"}])
    monkeypatch.chdir(catdir)

    await _register_and_refresh("catalog.json")

    captured = capsys.readouterr()
    assert "catalog location 'catalog.json' is a relative path" in captured.out
    assert "cat]52;c;eA==dir" in captured.out
    for stream in (captured.out, captured.err):
        assert "\x1b" not in stream
        assert "\x07" not in stream


# === the relative registration stays selectable (round-2 review) ===========


@pytest.mark.parametrize("doc_name", [None, "private"])
async def test_a_relative_registration_stays_selectable_by_the_spec_as_registered(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    doc_name: str | None,
) -> None:
    """Round-2 review: after refresh anchored ``catalog.json``, ``--catalog
    catalog.json`` matched neither the anchored location nor the label, and the
    command said "no catalog entry … (try … --refresh)" right after a refresh that
    listed it. The registered spec is cached (``registeredAs``) and matches, from
    any cwd; the absolute location matches too."""
    cat = layout["catdir"] / "catalog.json"
    doc: dict[str, object] = {
        "schemaVersion": 1,
        "extensions": [{"name": "entry", "source": "./local-ext"}],
    }
    if doc_name:
        doc["name"] = doc_name
    cat.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.chdir(layout["catdir"])
    mem = await _register_and_refresh("catalog.json")
    capsys.readouterr()
    cache = json.loads(ec.cache_file_path(layout["root"] / "agent").read_text("utf-8"))
    assert [c.get("registeredAs") for c in cache["catalogs"]] == ["catalog.json"]

    anchored = str(Path.cwd() / "catalog.json")
    for cwd in (layout["elsewhere"], layout["catdir"]):
        monkeypatch.chdir(cwd)
        for selector in ("catalog.json", anchored):
            runner = _FakeRunner()
            code = await run_extension_command_async(
                ["discover", "install", "entry", "--catalog", selector, "--yes", "--no-verify"],
                settings=mem,
                runner=runner,
            )
            err = capsys.readouterr().err
            assert code == 0, (cwd, selector, err)
            assert "--refresh" not in err
            assert runner.calls[0][-1] == _uri((layout["catdir"] / "local-ext").resolve())


async def test_a_relative_registration_is_selected_case_insensitively(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--catalog`` matches label, location and the spec as registered without
    regard to case (round-3 verify V17: a case-sensitive registeredAs passed)."""
    cat = layout["catdir"] / "Catalog.JSON"
    _write_catalog(cat, [{"name": "entry", "source": "./local-ext"}])
    monkeypatch.chdir(layout["catdir"])
    mem = await _register_and_refresh("Catalog.JSON")
    monkeypatch.chdir(layout["elsewhere"])
    capsys.readouterr()

    for selector in ("Catalog.JSON", "catalog.json"):
        runner = _FakeRunner()
        code = await run_extension_command_async(
            ["discover", "install", "entry", "--catalog", selector, "--yes", "--no-verify"],
            settings=mem,
            runner=runner,
        )
        assert code == 0, (selector, capsys.readouterr().err)
        assert runner.calls[0][-1] == _uri((layout["catdir"] / "local-ext").resolve())


def test_registered_as_round_trips_through_the_cache_and_is_omitted_when_unset() -> None:
    plain = ec.Catalog(location="/abs/catalog.json")
    assert "registeredAs" not in plain.to_json()
    anchored = ec.Catalog(location="/abs/catalog.json", registered_as="catalog.json")
    again = ec.Catalog.from_json(anchored.to_json())
    assert again is not None
    assert again.registered_as == "catalog.json"


async def test_a_symlink_then_dotdot_follows_the_link(layout: dict[str, Path]) -> None:
    """``./link/../real-ext`` with ``link -> mounted/inside`` is ``mounted/real-ext``
    to the filesystem; a lexical normpath made it ``catdir/real-ext`` (Codex 3a).

    What aelix hands on must be the directory the OS itself opens for that spelling,
    asked of the OS (a marker file read through the spelling), not hard-coded: on
    POSIX the kernel follows ``link`` before ``..``; on Windows the Win32 path layer
    collapses ``link\\..`` lexically before any link is looked at, so the OS opens
    ``catdir/real-ext`` there — windows CI 37561017353, where ``Path.resolve()``
    agreed with it."""
    root = layout["root"]
    (root / "mounted" / "inside").mkdir(parents=True)
    (root / "mounted" / "real-ext").mkdir()
    (layout["catdir"] / "real-ext").mkdir()  # what a lexical normpath would pick
    (root / "mounted" / "real-ext" / "WHICH").write_text("followed", encoding="utf-8")
    (layout["catdir"] / "real-ext" / "WHICH").write_text("lexical", encoding="utf-8")
    _symlink(layout["catdir"] / "link", root / "mounted" / "inside", is_dir=True)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "s", "source": "./link/../real-ext"}])
    mem = await _register_and_refresh(cat.as_uri())
    spelled = os.path.join(layout["catdir"], "link", os.pardir, "real-ext")
    with open(os.path.join(spelled, "WHICH"), encoding="utf-8") as fh:
        opened = fh.read()  # which copy the OS reaches through the catalog's spelling
    by_marker = {
        "followed": root / "mounted" / "real-ext",
        "lexical": layout["catdir"] / "real-ext",
    }

    code, runner = await _install(mem, "s")

    assert code == 0
    assert runner.calls[0][-1] == _uri(by_marker[opened].resolve())
    assert Path(os.path.realpath(spelled)) == by_marker[opened].resolve()
    if sys.platform == "win32":
        assert opened == "lexical"
    else:
        assert opened == "followed"
        assert runner.calls[0][-1] == _uri((root / "mounted" / "real-ext").resolve())


@pytest.mark.parametrize(
    ("source", "artifact", "extras", "project"),
    [
        (
            "./acme_notes-1.4.0-py3-none-any.whl[feature]",
            "acme_notes-1.4.0-py3-none-any.whl",
            "[feature]",
            "acme_notes",  # the wheel's file name
        ),
        ("./local-ext[extra,other]", "local-ext", "[extra,other]", "local-ext"),  # pyproject
    ],
)
async def test_a_local_path_with_extras_keeps_them(
    layout: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    source: str,
    artifact: str,
    extras: str,
    project: str,
) -> None:
    """pip strips ``[extras]`` before it opens the file, so the existence check does
    too, and the extras ride along — since review round 5 as PEP 508's
    ``name[extras] @ file:///…``, the name read from the artifact (Codex 3b)."""
    _wheel(layout["catdir"], "acme-notes", "1.4.0")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "x", "source": source}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "x")

    assert code == 0
    want = f"{(layout['catdir'] / artifact).resolve()}{extras}"
    assert runner.calls[0][-1] == _uri((layout["catdir"] / artifact).resolve(), extras, project)
    assert f"Install extension from path: {want}" in capsys.readouterr().out


async def test_a_verified_wheel_with_extras_stages_the_file_and_pins_it(
    layout: dict[str, Path],
) -> None:
    """With verification on, the WHEEL is hashed and staged (not mistaken for an
    unverifiable directory), the extras go back on the staged argv, and the pin is
    keyed by the file, not by the extras asked of it."""
    whl = _wheel(layout["catdir"], "acme-notes", "1.4.0")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "x", "source": f"./{whl.name}[feature]"}])
    mem = await _register_and_refresh(cat.as_uri())
    runner = _FakeRunner()

    code = await run_extension_command_async(
        ["discover", "install", "x", "--yes"], settings=mem, runner=runner
    )

    assert code == 0
    staged = runner.calls[0][-1]
    assert staged.startswith("acme_notes[feature] @ file://")
    assert staged.endswith(f"/{whl.name}")
    assert whl.resolve().as_uri() not in staged  # the staged copy, not the original
    pins = json.loads((layout["root"] / "agent" / "extension_pins.json").read_text("utf-8"))
    # Asked of the parsed pins, not of ``json.dumps`` (which doubles a Windows
    # path's backslashes — windows CI 37561017353): one pin, keyed by the file.
    assert list(pins["pins"]) == [str(whl.resolve())]
    assert not any("[feature]" in key for key in pins["pins"])
    assert "[feature]" not in json.dumps(pins)


def test_typed_extras_on_an_existing_path_classify_as_a_path(
    layout: dict[str, Path],
) -> None:
    """The one change to what a typed target means: aelix now agrees with pip
    that ``./x.whl[feature]`` is that file. A bare ``name[extra]`` stays a package
    even beside a ``./name`` directory, as it does for pip."""
    (layout["elsewhere"] / "pkg-1.0-py3-none-any.whl").write_bytes(b"x")
    assert ei.classify_target("./pkg-1.0-py3-none-any.whl[feature]") == "path"
    assert ei.classify_target("pkg-1.0-py3-none-any.whl[feature]") == "path"
    assert ei.classify_target("local-ext[extra]") == "pypi"  # ./local-ext exists here
    assert ei.classify_target("./missing.whl[feature]") == "pypi"  # unchanged reading


def test_the_post_install_helpers_read_a_path_target_without_its_extras(
    layout: dict[str, Path],
) -> None:
    """``_target_dist_hint`` and ``_target_source_key`` look at the FILE, not the
    extras asked of it (round-2 verify: dropping the split in either passed every
    row). The hint names the wheel's distribution; the key matches pip's
    ``direct_url.json`` record of the file."""
    whl = _wheel(layout["elsewhere"], "acme-notes", "1.4.0")
    target = f"./{whl.name}[feature]"
    assert ei._target_dist_hint(target, "path") == "acme_notes"
    assert ei._target_source_key(target, "path") == os.path.realpath(whl)
    tree = "./local-ext[extra]"  # elsewhere/local-ext has a [project] name
    assert ei._target_dist_hint(tree, "path") == "local-ext"
    assert ei._target_source_key(tree, "path") == os.path.realpath(
        layout["elsewhere"] / "local-ext"
    )


async def test_a_relative_catalog_location_is_anchored_at_refresh(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hand-edited ``{"spec": "catalog.json"}`` is read from the cwd at refresh;
    that directory is recorded (and said), so install works from anywhere after
    (Codex 3c: it used to be refused with a false "not a local file")."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": "./local-ext"}])
    monkeypatch.chdir(layout["catdir"])
    mem = await _register_and_refresh("catalog.json")
    out = capsys.readouterr().out
    assert "catalog location 'catalog.json' is a relative path" in out
    cached = ec.load_cached_catalog(layout["root"] / "agent")
    assert [c.location for c in cached] == [str(Path.cwd() / "catalog.json")]

    monkeypatch.chdir(layout["elsewhere"])
    code, runner = await _install(mem, "local-ext")

    assert code == 0
    assert runner.calls[0][-1] == _uri((layout["catdir"] / "local-ext").resolve())


async def test_source_add_stores_a_relative_catalog_path_absolute(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration is where the user typed the path, so it is anchored there."""
    _write_catalog(layout["catdir"] / "catalog.json", [])
    monkeypatch.chdir(layout["catdir"])
    mem = SettingsManager.in_memory()

    assert (
        await run_extension_command_async(
            ["source", "add", "--catalog", "catalog.json"], settings=mem
        )
        == 0
    )

    specs = [s.spec for s in mem.get_extension_sources()]
    assert specs == [str((layout["catdir"] / "catalog.json").resolve())]


async def test_source_add_relative_then_the_same_relative_catalog_selector(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Round-3 verify: ``source add --catalog catalog.json`` (stored absolute), then
    ``discover install … --catalog catalog.json`` from that directory said "no
    catalog entry … (try … --refresh)". A relative selector now names the file it
    names from the cwd; from a directory where it names no registered catalog, the
    error says that instead of suggesting a refresh."""
    _write_catalog(layout["catdir"] / "catalog.json", [{"name": "c", "source": "./local-ext"}])
    monkeypatch.chdir(layout["catdir"])
    mem = SettingsManager.in_memory()
    assert (
        await run_extension_command_async(
            ["source", "add", "--catalog", "catalog.json"], settings=mem
        )
        == 0
    )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    capsys.readouterr()
    # Stored absolute, so nothing was anchored: no registeredAs (round-3 verify V19).
    cache = json.loads(ec.cache_file_path(layout["root"] / "agent").read_text("utf-8"))
    assert [c.get("registeredAs") for c in cache["catalogs"]] == [None]
    argv = ["discover", "install", "c", "--catalog", "catalog.json", "--yes", "--no-verify"]

    for selector_cwd in (layout["catdir"], layout["catdir"] / "sub"):
        selector_cwd.mkdir(exist_ok=True)
        monkeypatch.chdir(selector_cwd)
        sel = "catalog.json" if selector_cwd == layout["catdir"] else "../catalog.json"
        runner = _FakeRunner()
        code = await run_extension_command_async(
            [*argv[:4], sel, *argv[5:]], settings=mem, runner=runner
        )
        assert code == 0, capsys.readouterr().err
        assert runner.calls[0][-1] == _uri((layout["catdir"] / "local-ext").resolve())

    monkeypatch.chdir(layout["elsewhere"])  # catalog.json names nothing registered here
    runner = _FakeRunner()
    code = await run_extension_command_async(argv, settings=mem, runner=runner)
    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "matches no registered catalog" in err
    assert "--refresh" not in err


async def test_a_resolved_path_that_vanishes_before_install_is_refused(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of the catalog-vs-installer check (round-1 S11): a path the
    catalog placed that is gone when the installer classifies it would read as a
    package name — refused instead."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": "./local-ext"}])
    mem = await _register_and_refresh(cat.as_uri())
    real = ec.resolve_entry_target

    def _then_vanish(entry: ec.CatalogEntry) -> str | ec.ResolvedPath:
        target = real(entry)
        assert isinstance(target, ec.ResolvedPath)
        shutil.rmtree(target.path)
        return target

    monkeypatch.setattr(ec, "resolve_entry_target", _then_vanish)

    code, runner = await _install(mem, "local-ext")

    assert code == 2
    assert runner.calls == []
    assert "disappeared before the install started" in capsys.readouterr().err


def test_an_entry_parsed_straight_from_a_fetch_resolves_beside_its_catalog(
    layout: dict[str, Path],
) -> None:
    """``parse_catalog`` (the fetch path, before any cache) carries the location
    too — a caller holding fresh entries places them the same way."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": "./local-ext"}])

    (entry,) = ec.fetch_catalog(cat.as_uri()).entries

    assert entry.catalog_location == cat.as_uri()
    assert ec.resolve_entry_source(entry) == (str((layout["catdir"] / "local-ext").resolve()), True)


# === package specs: unchanged, unless ambiguous ============================


async def test_a_package_spec_is_handed_on_unchanged(layout: dict[str, Path]) -> None:
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "pkg", "source": "foo-pkg==1.2"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "pkg")

    assert code == 0
    assert "foo-pkg==1.2" in runner.calls[0]


async def test_a_bare_name_that_also_sits_beside_the_catalog_is_refused(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``"source": "local-ext"`` meant as the directory next to the catalog was a
    path from ``catdir`` and a public-index lookup from everywhere else."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": "local-ext"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "local-ext")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "exists beside the catalog" in err
    assert "./local-ext" in err


@pytest.mark.parametrize("source", ["local-ext[extra]", "local-ext[extra,other]"])
async def test_a_bare_name_with_extras_beside_the_catalog_is_refused_too(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], source: str
) -> None:
    """``local-ext[extra]`` beside ``catdir/local-ext`` is the same ambiguity as the
    bare name (round-3 verify V11: skipping the check when extras are present
    passed every row); the suggested ``./local-ext[extra]`` installs that copy."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": source}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "local-ext")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "exists beside the catalog" in err
    assert f"./{source}" in err


async def test_a_version_pinned_name_beside_the_catalog_is_a_package(
    layout: dict[str, Path],
) -> None:
    """A version specifier says "package": no ambiguity with ``./local-ext``."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "local-ext", "source": "local-ext==0.1.0"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "local-ext")

    assert code == 0
    assert runner.calls[0][-1] == "local-ext==0.1.0"


async def test_a_package_spec_shadowed_by_the_cwd_is_refused(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    (layout["elsewhere"] / "foo-pkg").mkdir()
    mem = _seed(layout["root"], "https://catalog.example.invalid/c.json", [("pkg", "foo-pkg")])

    code, runner = await _install(mem, "pkg")

    assert code == 2
    assert runner.calls == []
    assert "exists in the current directory" in capsys.readouterr().err


async def test_a_git_source_is_handed_on_unchanged(layout: dict[str, Path]) -> None:
    sha = "b" * 40
    mem = _seed(
        layout["root"],
        "https://catalog.example.invalid/c.json",
        [("g", f"git+https://h.example.invalid/r.git@{sha}")],
    )

    code, runner = await _install(mem, "g")

    assert code == 0
    assert any(sha in a for a in runner.calls[0])


# === what the user types keeps its meaning =================================


async def test_explicit_cli_install_of_a_relative_path_is_unchanged(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(layout["catdir"])
    runner = _FakeRunner()

    code = await run_extension_command_async(
        ["install", "./local-ext", "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
        runner=runner,
    )

    assert code == 0
    assert "Install extension from path: ./local-ext" in capsys.readouterr().out
    assert ei.classify_target("./not-here") == "pypi"  # the typed form's old reading


# === the index generator's --relative now matches the resolver =============


async def test_index_relative_with_out_elsewhere_installs_from_any_cwd(
    layout: dict[str, Path],
) -> None:
    wheels = layout["root"] / "wheels"
    wheels.mkdir()
    whl = _wheel(wheels, "local-ext", "0.1.0")
    out = layout["root"] / "published" / "catalog.json"

    assert (
        await run_extension_command_async(
            ["index", str(wheels), "--out", str(out), "--relative"],
            settings=SettingsManager.in_memory(),
        )
        == 0
    )
    source = json.loads(out.read_text(encoding="utf-8"))["extensions"][0]["source"]
    assert source == f"../wheels/{whl.name}"

    mem = await _register_and_refresh(out.as_uri())
    code, runner = await _install(mem, "local-ext")

    assert code == 0
    assert _uri(whl.resolve()) in runner.calls[0]


def test_index_relative_to_stdout_measures_from_the_directory(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    wheels = layout["root"] / "wheels"
    wheels.mkdir()
    whl = _wheel(wheels, "local-ext", "0.1.0")

    assert ei.run_extension_command(["index", str(wheels), "--out", "-", "--relative"]) == 0

    doc = json.loads(capsys.readouterr().out)
    assert doc["extensions"][0]["source"] == f"./{whl.name}"


def test_index_relative_to_stdout_through_a_symlinked_directory(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-4 verify W23: measuring from the directory AS TYPED (a symlink to the
    scanned one) instead of the resolved one gave ``../real/x.whl``."""
    wheels = layout["root"] / "real-wheels"
    wheels.mkdir()
    whl = _wheel(wheels, "local-ext", "0.1.0")
    link = layout["root"] / "wheels-link"
    _symlink(link, wheels, is_dir=True)

    assert ei.run_extension_command(["index", str(link), "--out", "-", "--relative"]) == 0

    doc = json.loads(capsys.readouterr().out)
    assert doc["extensions"][0]["source"] == f"./{whl.name}"


async def test_index_relative_to_stdout_ignores_a_symlinked_catalog_json_there(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-3 review (P2): ``--out -`` writes no file, yet measured from the target
    of a ``<dir>/catalog.json`` symlink — ``../../elsewhere/x.whl`` — so the saved
    output did not install. It measures from the scanned directory."""
    wheels = layout["root"] / "wheels"
    wheels.mkdir()
    whl = _wheel(wheels, "local-ext", "0.1.0")
    nested = layout["root"] / "catdir2" / "nested"
    nested.mkdir(parents=True)
    (nested / "catalog.json").write_text("{}", encoding="utf-8")
    _symlink(wheels / "catalog.json", nested / "catalog.json", is_dir=False)

    code = await run_extension_command_async(
        ["index", str(wheels), "--relative", "--out", "-"], settings=SettingsManager.in_memory()
    )

    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["extensions"][0]["source"] == f"./{whl.name}"
    portable = wheels / "portable.json"
    portable.write_text(json.dumps(doc), encoding="utf-8")
    mem = await _register_and_refresh(portable.as_uri())
    code, runner = await _install(mem, "local-ext")
    assert code == 0
    assert runner.calls[0][-1] == _uri(whl.resolve())


async def test_index_relative_and_install_agree_on_a_symlinked_catalog(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``published/catalog.json`` -> ``physical/catalog.json``: ``index --relative``
    measures from the physical directory, and so does the resolver — registering
    either the link or the printed path installs the wheel (Codex 3d)."""
    root = layout["root"]
    physical = root / "physical"
    physical.mkdir()
    whl = _wheel(physical, "acme-notes", "1.4.0")
    (physical / "catalog.json").write_text("{}", encoding="utf-8")
    (root / "published").mkdir()
    _symlink(root / "published" / "catalog.json", physical / "catalog.json", is_dir=False)

    assert (
        await run_extension_command_async(
            [
                "index",
                str(physical),
                "--relative",
                "--out",
                str(root / "published" / "catalog.json"),
            ],
            settings=SettingsManager.in_memory(),
        )
        == 0
    )
    hint = (physical / "catalog.json").resolve().as_uri()
    assert f"aelix extension source add --catalog {hint}" in capsys.readouterr().out
    source = json.loads((physical / "catalog.json").read_text("utf-8"))["extensions"][0]["source"]
    assert source == f"./{whl.name}"

    for location in ((root / "published" / "catalog.json").as_uri(), hint):
        mem = await _register_and_refresh(location)
        code, runner = await _install(mem, "acme-notes")
        assert code == 0, location
        assert runner.calls[0][-1] == _uri(whl.resolve()), location


def test_index_relative_across_drives_exits_2_and_writes_nothing(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Windows ``os.path.relpath`` raises ``ValueError`` across drives; simulated
    here (round-1 S24): a usage error, and no catalog file appears."""
    wheels = layout["root"] / "wheels"
    wheels.mkdir()
    _wheel(wheels, "local-ext", "0.1.0")
    out = layout["root"] / "published" / "catalog.json"

    def _cross_drive(path: object, start: object = None) -> str:
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", _cross_drive)

    code = ei.run_extension_command(["index", str(wheels), "--out", str(out), "--relative"])

    assert code == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert "--relative cannot express the artifacts relative to" in err
    assert "mount 'D:'" in err


# === the predicate ========================================================


@pytest.mark.parametrize(
    ("source", "is_path"),
    [
        ("./x", True),
        ("../x", True),
        ("/abs/x", True),
        ("~/x", True),
        ("sub/x", True),
        ("sub\\x", True),
        (".\\x", True),
        ("C:\\x", True),
        ("C:x", True),
        (".", True),
        ("..", True),
        ("./https://x", True),
        (".\\https://x", True),
        ("../git+https://h/r.git", True),
        ("x-1.0-py3-none-any.whl[feature]", True),
        ("./x[extra]", True),
        ("x-1.0-py3-none-any.whl", True),
        ("x-1.0.tar.gz", True),
        ("x-1.0.zip", True),
        ("name", False),
        ("name==1.2", False),
        ("name[extra]>=1,<2", False),
        ("-weird-pkg", False),
        ("git+https://h/r.git@abc", False),
        ("git@github.com:o/r.git", False),
        ("https://h/x-1.0-py3-none-any.whl", False),
        ("name @ https://h/x.whl", False),
        ("file:///abs/x.whl", False),
        ("file:local-ext", False),  # a URL to this TYPED-target predicate
        ("file:sub/local-ext", False),  # …even with a separator in it
        ("file:x-1.0-py3-none-any.whl", False),  # …or an archive suffix
        ("local-ext @ file:local-ext", False),
    ],
)
def test_source_looks_like_path(source: str, is_path: bool) -> None:
    assert ec.source_looks_like_path(source) is is_path


# === review round 4: the registered sources decide, not the cache ==========


def _two_catalogs(layout: dict[str, Path]) -> tuple[Path, Path]:
    """``catdir/catalog.json`` ("private", entry ``a``) and ``other/catalog.json``
    ("other", entry ``b``), both with a ``./local-ext`` beside them."""

    first = layout["catdir"] / "catalog.json"
    _write_catalog(first, [{"name": "a", "source": "./local-ext"}])
    other = layout["root"] / "other"
    (other / "local-ext").mkdir(parents=True)
    second = other / "catalog.json"
    doc = {
        "schemaVersion": 1,
        "name": "other",
        "extensions": [{"name": "b", "source": "./local-ext"}],
    }
    second.write_text(json.dumps(doc), encoding="utf-8")
    return first, second


async def _cli(mem: SettingsManager, *args: str) -> tuple[int, _FakeRunner]:
    runner = _FakeRunner()
    code = await run_extension_command_async(list(args), settings=mem, runner=runner)
    return code, runner


@pytest.mark.parametrize("spelling", ["path", "file-uri", "through-a-symlink"])
async def test_a_registered_catalog_not_fetched_yet_is_named_as_such(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], spelling: str
) -> None:
    """Round-4 verify (B): after ``source add --catalog /abs/catalog.json`` and before
    ``discover --refresh``, ``--catalog <that path>`` said "matches no registered
    catalog" (the cache was asked, not the registered sources). It names the
    registered catalog, says the cache has no copy, and says how to fetch it; after
    the refresh the same selector installs. ``source add`` stores a symlinked path
    resolved, and that spelling selects it too."""
    first, second = _two_catalogs(layout)
    if spelling == "through-a-symlink":
        link = layout["root"] / "link"
        _symlink(link, second.parent, is_dir=True)
        given = str(link / "catalog.json")
    else:
        given = str(second)
    selector = second.as_uri() if spelling == "file-uri" else given
    mem = SettingsManager.in_memory()
    assert (
        await run_extension_command_async(["source", "add", "--catalog", str(first)], settings=mem)
        == 0
    )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    assert (
        await run_extension_command_async(["source", "add", "--catalog", given], settings=mem) == 0
    )
    assert [s.spec for s in mem.get_extension_sources()][-1] == str(second.resolve())
    capsys.readouterr()
    argv = ["discover", "install", "b", "--catalog", selector, "--yes", "--no-verify"]

    code, runner = await _cli(mem, *argv)

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "matches no registered catalog" not in err
    assert f"names the registered catalog '{second.resolve()}'" in err
    assert "the catalog cache has no copy recorded for it" in err
    assert "Fetch it with: aelix extension discover --refresh" in err

    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    code, runner = await _cli(mem, *argv)

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri((second.parent / "local-ext").resolve())


async def test_a_catalog_an_offline_refresh_skipped_is_named_as_not_fetched(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """An https catalog registered beside a local one, refreshed ``--offline``: it is
    skipped, so the cache has no copy — not "no registered catalog"."""
    first, _ = _two_catalogs(layout)
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": str(first), "kind": "catalog"},
                {"spec": _HTTPS_LOCATION, "kind": "catalog"},
            ]
        }
    )
    assert (
        await run_extension_command_async(["discover", "--refresh", "--offline"], settings=mem) == 0
    )
    capsys.readouterr()

    code, runner = await _cli(mem, "discover", "install", "x", "--catalog", _HTTPS_LOCATION)

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert f"names the registered catalog '{_HTTPS_LOCATION}'" in err
    assert "--refresh" in err
    assert "matches no registered catalog" not in err

    # Case-insensitively, as a cached catalog is selected (round-5 F11).
    code, _ = await _cli(mem, "discover", "install", "x", "--catalog", _HTTPS_LOCATION.upper())

    assert code == 2
    assert "names the registered catalog" in capsys.readouterr().err


async def test_the_built_in_default_catalog_counts_as_registered(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The default catalog is registered without ``source add``; before its first
    refresh ``--catalog <its URL>`` names it as not fetched (round-5 F07)."""
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _HTTPS_LOCATION)
    mem = SettingsManager.in_memory()

    code, runner = await _cli(mem, "discover", "install", "x", "--catalog", _HTTPS_LOCATION)

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert f"names the registered catalog '{_HTTPS_LOCATION}'" in err
    assert "--refresh" in err


async def test_a_catalog_whose_last_refresh_failed_is_named_with_its_error(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The cache keeps a failed fetch as an error row; ``--catalog`` naming it says
    the refresh failed and why, instead of "no catalog entry named …"."""
    first, second = _two_catalogs(layout)
    second.write_text("{not json", encoding="utf-8")
    mem = SettingsManager.in_memory()
    for cat in (first, second):
        assert (
            await run_extension_command_async(
                ["source", "add", "--catalog", str(cat)], settings=mem
            )
            == 0
        )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    capsys.readouterr()

    code, runner = await _cli(mem, "discover", "install", "b", "--catalog", str(second))

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert f"names the catalog '{second.resolve()}', whose last refresh failed: " in err
    assert "is not valid UTF-8 JSON" in err
    assert "fetch it again with: aelix extension discover --refresh" in err
    assert "no catalog entry named" not in err

    # Its name ("other") was never read, so a label selector cannot pick it — and
    # the error says a refresh comes first.
    code, runner = await _cli(mem, "discover", "install", "b", "--catalog", "other")

    assert code == 2
    assert "1 registered catalog(s) have no fetched copy" in capsys.readouterr().err


async def test_a_relative_registration_cached_before_registered_as_says_refresh(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hand-edited ``{"spec": "catalog.json"}`` whose cache was written before
    ``registeredAs`` existed: ``--catalog catalog.json`` from elsewhere names the
    registered spec and says to refresh (it said "matches no registered catalog");
    after a refresh it installs from anywhere."""
    _two_catalogs(layout)
    monkeypatch.chdir(layout["catdir"])
    mem = await _register_and_refresh("catalog.json")
    path = ec.cache_file_path(layout["root"] / "agent")
    cache = json.loads(path.read_text("utf-8"))
    for block in cache["catalogs"]:
        block.pop("registeredAs", None)
    path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.chdir(layout["elsewhere"])
    capsys.readouterr()
    argv = ["discover", "install", "a", "--catalog", "catalog.json", "--yes", "--no-verify"]

    code, runner = await _cli(mem, *argv)

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "names the registered catalog 'catalog.json'" in err
    assert "--refresh" in err

    monkeypatch.chdir(layout["catdir"])
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    monkeypatch.chdir(layout["elsewhere"])
    code, runner = await _cli(mem, *argv)

    assert code == 0
    assert runner.calls[0][-1] == _uri((layout["catdir"] / "local-ext").resolve())

    # Now cached under its registered spec (registeredAs): a selector that names
    # nothing gets no "not fetched yet" note (round-5 F10).
    code, _ = await _cli(mem, *argv[:4], "nowhere", *argv[5:])

    assert code == 2
    err = capsys.readouterr().err
    assert "matches no registered catalog" in err
    assert "no fetched copy" not in err


async def test_a_selector_naming_nothing_registered_says_so_and_why_a_name_may_be_unknown(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The label of a registered catalog that was never fetched is not known yet: the
    error says no registered catalog matches AND that one has no fetched copy. With
    every catalog fetched, a selector naming nothing registered gets no refresh hint
    (see test_source_add_relative_then_the_same_relative_catalog_selector)."""
    first, second = _two_catalogs(layout)
    mem = SettingsManager.in_memory()
    assert (
        await run_extension_command_async(["source", "add", "--catalog", str(first)], settings=mem)
        == 0
    )
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    assert (
        await run_extension_command_async(["source", "add", "--catalog", str(second)], settings=mem)
        == 0
    )
    capsys.readouterr()

    code, runner = await _cli(mem, "discover", "install", "b", "--catalog", "other")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "--catalog 'other' matches no registered catalog" in err
    assert "1 registered catalog(s) have no fetched copy" in err
    assert "run 'aelix extension discover --refresh' first" in err


async def test_without_a_selector_an_unknown_name_keeps_the_plain_hint(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-4 verify W22: the catalog diagnosis runs only for ``--catalog``."""
    first, second = _two_catalogs(layout)
    mem = SettingsManager.in_memory()
    assert (
        await run_extension_command_async(["source", "add", "--catalog", str(first)], settings=mem)
        == 0
    )
    assert (
        await run_extension_command_async(["source", "add", "--catalog", str(second)], settings=mem)
        == 0
    )
    capsys.readouterr()

    code, runner = await _cli(mem, "discover", "install", "b")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "Error: no catalog entry named 'b' (try: aelix extension discover --refresh)." in err
    assert "registered catalog" not in err


# === review round 4: the verify lane's surviving mutants =====================


@pytest.mark.parametrize("catalog_kind", ["local", "https"])
async def test_an_upper_case_archive_suffix_is_still_a_bare_archive_name(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], catalog_kind: str
) -> None:
    """Round-4 verify W10: pip's archive test lower-cases the suffix, so
    ``x.WHL`` is a file to it: beside a local catalog it installs from there, and
    from an https catalog it is refused (no local base) — never handed to the
    index as a package name."""
    name = "upper_ext-1.0-py3-none-any.WHL"
    (layout["catdir"] / name).write_bytes(b"a wheel")
    if catalog_kind == "local":
        cat = layout["catdir"] / "catalog.json"
        _write_catalog(cat, [{"name": "u", "source": name}])
        mem = await _register_and_refresh(cat.as_uri())
    else:
        mem = _seed(layout["root"], _HTTPS_LOCATION, [("u", name)])

    code, runner = await _install(mem, "u")

    if catalog_kind == "local":
        assert code == 0
        assert runner.calls[0][-1] == _uri((layout["catdir"] / name).resolve())
    else:
        assert code == 2
        assert runner.calls == []
        assert "Resolved" not in capsys.readouterr().out


def test_a_space_before_path_extras_is_dropped_as_pip_drops_it() -> None:
    """Round-4 verify NB: pip 26.2.1's ``strip_extras`` right-strips the path part."""
    assert ec.split_path_extras("./x.whl [feature]") == ("./x.whl", "[feature]")
    assert ec.split_path_extras("./x.whl[feature]") == ("./x.whl", "[feature]")
    assert ec.split_path_extras("./x.whl ") == ("./x.whl ", "")


async def test_a_spaced_extras_source_installs_the_file_pip_would_open(
    layout: dict[str, Path],
) -> None:
    """``./x.whl [feature]`` beside both ``x.whl`` and ``x.whl `` (a trailing
    space): aelix used to check, show and stage ``x.whl ``, while pip opens
    ``x.whl``. Now both agree on ``x.whl``, and the argv carries the extras."""
    whl = layout["catdir"] / "local_ext-0.1.0-py3-none-any.whl"
    if sys.platform != "win32":
        (layout["catdir"] / f"{whl.name} ").write_bytes(b"the spaced sibling")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "s", "source": f"./{whl.name} [feature]"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "s")

    assert code == 0
    assert runner.calls[0][-1] == _uri(whl.resolve(), "[feature]", "local_ext")


_OSC52 = "\x1b]52;c;cHduZWQ=\x07"


def _assert_inert(captured: pytest.CaptureResult[str]) -> None:
    for stream in (captured.out, captured.err):
        assert "\x1b" not in stream
        assert "\x07" not in stream


@pytest.mark.parametrize(
    ("flags", "line"),
    [
        (["--strict"], "Verification error (strict) — pip not run: boom ]52;c;cHduZWQ="),
        ([], "Warning: integrity verification skipped (boom ]52;c;cHduZWQ="),
    ],
)
async def test_a_verify_exception_is_printed_terminal_safe(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    line: str,
) -> None:
    """Round-4 verify W11 / W12: an internal verify error is quoted after consent."""
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "w", "source": "./local_ext-0.1.0-py3-none-any.whl"}])
    mem = await _register_and_refresh(cat.as_uri())

    def boom(*_a: object, **_k: object) -> object:
        raise RuntimeError(f"boom {_OSC52}")

    monkeypatch.setattr(ei, "verify_and_pin", boom)

    code, _ = await _cli(mem, "discover", "install", "w", "--yes", *flags)

    assert code == (2 if flags else 0)
    captured = capsys.readouterr()
    assert line in captured.err
    _assert_inert(captured)


@pytest.mark.parametrize(
    ("patched", "line"),
    [
        ("_record_pin", "Warning: could not record integrity pin: boom ]52;c;cHduZWQ="),
        ("_upsert_source", "Warning: could not record install source: boom ]52;c;cHduZWQ="),
    ],
)
async def test_a_record_failure_is_printed_terminal_safe(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    patched: str,
    line: str,
) -> None:
    """Round-4 verify W13 / W14: the best-effort pin and source records quote the
    exception after the install ran."""
    whl = _wheel(layout["catdir"], "acme-notes", "1.4.0")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "w", "source": f"./{whl.name}"}])
    mem = await _register_and_refresh(cat.as_uri())

    def boom(*_a: object, **_k: object) -> object:
        raise RuntimeError(f"boom {_OSC52}")

    monkeypatch.setattr(ei, patched, boom)

    code, runner = await _cli(mem, "discover", "install", "w", "--yes")

    assert runner.calls  # the install itself ran
    assert code in (0, ei._INSTALL_NOT_BOUND)
    captured = capsys.readouterr()
    assert line in captured.err
    _assert_inert(captured)


#: A catalog cannot carry this (the cache loader drops an entry with control
#: characters), so these two rows TYPE it: the lines are filtered for any target.
_EVIL_URL_SPEC = f"acme-notes @ https://h.example.invalid/{_OSC52}acme_notes-1.4.0-py3-none-any.whl"


async def test_the_pypi_download_line_is_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-4 verify W15: ``--verify-pypi``'s download line quotes the spec; it is
    filtered (the argv the runner gets keeps the bytes)."""
    mem = SettingsManager.in_memory()

    code, runner = await _cli(mem, "install", _EVIL_URL_SPEC, "--yes", "--verify-pypi")

    assert code in (0, 2, ei._INSTALL_NOT_BOUND)  # the fake download yields no file
    assert "download" in runner.calls[0]
    assert _EVIL_URL_SPEC in runner.calls[0]  # the real bytes reach the runner
    captured = capsys.readouterr()
    assert "→ verify (download): " in captured.out
    assert "h.example.invalid/]52;c;cHduZWQ=acme_notes" in captured.out
    _assert_inert(captured)


async def test_the_uv_verify_refusal_is_terminal_safe(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Round-4 verify W16: the uv backend's fail-closed pypi-verify refusal quotes
    the target."""
    mem = SettingsManager.in_memory()
    uv = ei.InstallBackend(name="uv", uv_path=Path(sys.executable).resolve())
    monkeypatch.setattr(ei, "resolve_install_backend", lambda _runner: uv)

    code, runner = await _cli(mem, "install", _EVIL_URL_SPEC, "--yes", "--verify-pypi")

    assert code == 2
    assert runner.calls == []
    captured = capsys.readouterr()
    assert "Error: refusing to install" in captured.err
    assert "]52;c;cHduZWQ=acme_notes" in captured.err
    _assert_inert(captured)


# === review round 5: one hand-off for every catalog path ===================
#
# Every remaining round-5 P1 had one shape: aelix resolved a source right, then
# handed the installer a STRING that uv or pip parsed again differently. A catalog
# path now reaches both as a percent-encoded ``file://`` URI (``name[extras] @ uri``
# with extras); a scheme not written in lowercase is refused; ``name @ git+…`` is
# never given a second ``git+``. The real-backend rows run uv / pip offline against
# fixtures with an in-tree build backend and read back WHICH copy was installed.

_BACKEND = """\
import pathlib, zipfile
HERE = pathlib.Path(__file__).parent
def build_wheel(wd, config_settings=None, metadata_directory=None):
    origin = (HERE / "ORIGIN").read_text().strip()
    di = "local_ext-1.0.dist-info"
    meta = "Metadata-Version: 2.1\\nName: local-ext\\nVersion: 1.0\\nProvides-Extra: feature\\n"
    files = {di + "/METADATA": meta,
             di + "/WHEEL": "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
             "local_ext.py": "ORIGIN = %r\\n" % origin}
    files[di + "/RECORD"] = "".join(p + ",,\\n" for p in [*files, di + "/RECORD"])
    name = "local_ext-1.0-py3-none-any.whl"
    with zipfile.ZipFile(pathlib.Path(wd) / name, "w") as z:
        for p, v in files.items():
            z.writestr(p, v)
    return name
"""
_PYPROJECT = (
    '[project]\nname = "local-ext"\nversion = "1.0"\n'
    "[project.optional-dependencies]\nfeature = []\n"
    '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'
)


def _real_wheel(directory: Path, origin: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    di = "local_ext-1.0.dist-info"
    files = {
        f"{di}/METADATA": "Metadata-Version: 2.1\nName: local-ext\nVersion: 1.0\n"
        "Provides-Extra: feature\n",
        f"{di}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "local_ext.py": f"ORIGIN = {origin!r}\n",
    }
    files[f"{di}/RECORD"] = "".join(f"{p},,\n" for p in [*files, f"{di}/RECORD"])
    path = directory / "local_ext-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path


def _real_project(directory: Path, origin: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (directory / "backend.py").write_text(_BACKEND, encoding="utf-8")
    (directory / "ORIGIN").write_text(origin, encoding="utf-8")
    return directory


def _real_sdist(directory: Path, origin: str) -> Path:
    import tarfile

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "local_ext-1.0.tar.gz"
    members = {
        "pyproject.toml": _PYPROJECT,
        "backend.py": _BACKEND,
        "ORIGIN": origin,
        "PKG-INFO": "Metadata-Version: 2.1\nName: local-ext\nVersion: 1.0\n",
    }
    with tarfile.open(path, "w:gz") as tf:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(f"local_ext-1.0/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _pip_python() -> str | None:
    """A Python whose ``-m pip`` runs: ``AELIX_TEST_PIP_PYTHON``, else this one."""

    import importlib.util

    explicit = os.environ.get("AELIX_TEST_PIP_PYTHON")
    if explicit:
        return explicit
    return sys.executable if importlib.util.find_spec("pip") is not None else None


class _RealBackend:
    """Runs aelix's install argv tail on a REAL backend, offline, into a target
    directory, and reports which copy landed (the installed module's ORIGIN)."""

    def __init__(self, backend: str, scratch: Path, env: dict[str, str] | None = None) -> None:
        self.backend = backend
        self.scratch = scratch
        self.env = env or {}
        self.calls: list[list[str]] = []
        self.output = ""

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        tail = argv[argv.index("install") + 1 :]
        target = self.scratch / f"target-{len(self.calls)}"
        env = {
            **os.environ,
            "UV_CACHE_DIR": str(self.scratch / "uv-cache"),
            "UV_NO_CONFIG": "1",
            "UV_OFFLINE": "1",
            "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            **self.env,
        }
        env.pop("VIRTUAL_ENV", None)
        if self.backend == "uv":
            uv = shutil.which("uv")
            assert uv is not None
            cmd = [uv, "pip", "install", "--python", sys.executable, "--target", str(target)]
        else:
            python = _pip_python()
            assert python is not None
            cmd = [python, "-m", "pip", "install", "--target", str(target)]
        proc = subprocess.run([*cmd, *tail], capture_output=True, env=env, timeout=180)
        self.output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.target = target
        return subprocess.CompletedProcess(args=argv, returncode=proc.returncode)

    def origin(self) -> str | None:
        module = self.target / "local_ext.py"
        if not module.is_file():
            return None
        return module.read_text("utf-8").split("=", 1)[1].strip().strip("'\"")


def _need(backend: str) -> None:
    if backend == "uv" and shutil.which("uv") is None:
        pytest.skip("no `uv` on PATH; the hand-off measurement needs it")
    if backend == "pip" and _pip_python() is None:
        pytest.skip("no pip for this interpreter (set AELIX_TEST_PIP_PYTHON to one)")


def _decoy_link(catdir: Path, name: str, elsewhere: Path, make: object) -> None:
    """``catdir/<name>`` -> a decoy in the cwd: what a backend that re-parsed the
    resolved string (dropping ``[]``, ``[x]``, a trailing space, everything after
    ``;``) would open instead."""

    decoy = elsewhere / f"decoy-{name}"
    make(decoy, "decoy")  # type: ignore[operator]
    _symlink(catdir / name, decoy, is_dir=decoy.is_dir())


#: (case id, the catalog source, the directory the artifact sits in or IS, the
#: stripped sibling a re-parse would open, kind, extras, verify). POSIX-only names
#: (a trailing space, ``[]``) are skipped on Windows.
_HANDOFF_CASES = [
    ("empty-brackets-dir", "./link", "trusted[]", "trusted", "dir", "", False),
    ("trailing-space-dir", "./link", "trusted ", "trusted", "dir", "", False),
    ("bracket-group-dir-extras", "./brk[x][feature]", "brk[x]", "brk", "dir", "[feature]", False),
    ("semicolon-wheel-extras", "./rel;x/WHL[feature]", "rel;x", "rel", "wheel", "[feature]", False),
    ("bracket-sdist-extras", "./t[x]/SDIST[feature]", "t[x]", "t", "sdist", "[feature]", False),
    ("staged-wheel-extras", "./w[]/WHL[feature]", "w[]", "w", "wheel", "[feature]", True),
]


@pytest.mark.parametrize("backend", ["uv", "pip"])
@pytest.mark.parametrize(
    ("case", "source", "holder", "stripped", "kind", "extras", "verify"),
    _HANDOFF_CASES,
    ids=[c[0] for c in _HANDOFF_CASES],
)
async def test_a_catalog_path_reaches_the_real_backend_as_the_copy_it_names(
    layout: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    case: str,
    source: str,
    holder: str,
    stripped: str,
    kind: str,
    extras: str,
    verify: bool,
) -> None:
    """Codex pass 5 (P1): ``./link`` -> ``trusted[]`` / ``trusted `` installed the
    decoy ``trusted`` on uv (a trailing space on pip too) with the bare path string;
    ``;`` broke pip. Through the URI hand-off — and ``name[extras] @ uri`` for a
    wheel, an sdist and a project directory with extras — both backends install the
    catalog's copy, the extras accepted, default verification's staged copy too."""
    _need(backend)
    if sys.platform == "win32":
        pytest.skip("these names are not valid on Windows")
    catdir = layout["catdir"]
    make = {"dir": _real_project, "wheel": _real_wheel, "sdist": _real_sdist}[kind]
    if kind == "dir":
        real = make(catdir / holder, "catalog")
        _decoy_link(catdir, stripped, layout["elsewhere"], _real_project)
        if source == "./link":
            _symlink(catdir / "link", real, is_dir=True)
    else:
        real = make(catdir / holder, "catalog")
        decoy = make(layout["elsewhere"] / f"decoy-{stripped}", "decoy")
        _symlink(catdir / stripped, decoy.parent, is_dir=True)
        source = source.replace("WHL", real.name).replace("SDIST", real.name)
    cat = catdir / "catalog.json"
    _write_catalog(cat, [{"name": "probe", "source": source}])
    mem = await _register_and_refresh(cat.as_uri())
    runner = _RealBackend(backend, tmp_path / "scratch")
    flags = ["--yes"] if verify else ["--yes", "--no-verify"]

    code = await run_extension_command_async(
        ["discover", "install", "probe", *flags], settings=mem, runner=runner
    )

    err = capsys.readouterr().err
    assert code in (0, ei._INSTALL_NOT_BOUND), (err, runner.output)
    handed = runner.calls[0][-1]
    if extras:
        assert handed.startswith(f"local_ext{extras} @ file://") or handed.startswith(
            f"local-ext{extras} @ file://"
        ), handed
    else:
        assert handed == real.resolve().as_uri()
    assert runner.origin() == "catalog", runner.output


@pytest.mark.parametrize("backend", ["uv", "pip"])
async def test_the_bare_path_hand_off_installs_the_decoy_which_is_why(
    layout: dict[str, Path], tmp_path: Path, backend: str
) -> None:
    """WHY the hand-off is a URI, measured: the bare path string of a directory
    named ``trusted `` (one trailing space) installs the stripped sibling
    ``trusted`` — a decoy — on both backends; its URI installs the right one."""
    _need(backend)
    if sys.platform == "win32":
        pytest.skip("a trailing space is not valid in a Windows file name")
    real = _real_project(layout["catdir"] / "trusted ", "catalog")
    _real_project(layout["catdir"] / "trusted", "decoy")
    for spec, want in ((str(real), "decoy"), (real.as_uri(), "catalog")):
        runner = _RealBackend(backend, tmp_path / f"scratch-{want}")
        result = runner([sys.executable, "-m", "pip", "install", spec])
        assert result.returncode == 0, runner.output
        assert runner.origin() == want, (spec, runner.output)


@pytest.mark.parametrize(
    "layout_kind", ["the-entry-itself", "through-a-symlink", "a-parent-directory"]
)
async def test_a_resolved_path_holding_a_hash_is_refused(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], layout_kind: str
) -> None:
    """Codex pass 5 (P1): ``./link`` -> ``trusted#release`` installed the decoy
    ``trusted`` on uv — it cuts at ``#`` even when it arrives as ``%23`` in a
    ``file://`` URI (measured: ``test_uv_cuts_a_hash_in_every_spelling``), so no
    hand-off survives it. Refused before consent, wherever the ``#`` sits."""
    if sys.platform == "win32":
        pytest.skip("POSIX file names")
    catdir = layout["catdir"]
    if layout_kind == "the-entry-itself":
        (catdir / "trusted#release").mkdir()
        source = "./trusted#release"
    elif layout_kind == "through-a-symlink":
        (catdir / "trusted#release").mkdir()
        _symlink(catdir / "link", catdir / "trusted#release", is_dir=True)
        source = "./link"
    else:
        (catdir / "rel#1").mkdir()
        _wheel(catdir / "rel#1", "acme-notes", "1.0")
        source = "./rel#1/acme_notes-1.0-py3-none-any.whl"
    cat = catdir / "catalog.json"
    _write_catalog(cat, [{"name": "h", "source": source}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "h")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "contains '#', which uv reads as the start of a URL fragment" in err
    assert "'h'" in err


def test_uv_cuts_a_hash_in_every_spelling(layout: dict[str, Path], tmp_path: Path) -> None:
    """WHY a ``#`` is refused rather than encoded: every uv opens ``<dir>/trusted``
    for ``trusted#release`` as a bare path and as ``file://…/trusted%23release`` —
    the URI aelix hands over for a path without extras. ``name @ file://…%23…`` was
    cut the same way before uv 0.11.27, which reads it whole ("Encode hashes in file
    paths", astral-sh/uv#19807; #393 measured 0.11.14/0.11.26 decoy, 0.11.27/0.11.33/
    0.12.23 catalog, .omc/probes/393-live/impl/probe-hash-spellings.txt)."""
    _need("uv")
    uv = shutil.which("uv")
    assert uv is not None
    version = _uv_version(uv)
    real = _real_project(layout["catdir"] / "trusted#release", "catalog")
    _real_project(layout["catdir"] / "trusted", "decoy")
    named = f"local-ext @ {real.as_uri()}"
    for spec in (str(real), real.as_uri(), named):
        expected = "decoy"
        if spec == named:
            if version is None:
                continue
            if version >= _UV_READS_A_NAMED_HASH_PATH_WHOLE:
                expected = "catalog"
        runner = _RealBackend("uv", tmp_path / f"s{len(spec)}")
        runner([sys.executable, "-m", "pip", "install", spec])
        assert runner.origin() == expected, (spec, version, runner.output)


def test_the_installer_arg_is_a_uri_and_names_the_project_for_extras(
    layout: dict[str, Path],
) -> None:
    """The hand-off itself, per artifact kind: no extras -> the URI; extras -> the
    PEP 508 ``name[extras] @ uri`` with the name read from the artifact; a name aelix
    cannot read with extras, or a ``#``, is a refusal (CatalogError), never a guess."""
    whl = _wheel(layout["catdir"], "acme-notes", "1.4.0")
    sdist = layout["catdir"] / "team_notes-2.0.tar.gz"
    sdist.write_bytes(b"x")
    tree = layout["catdir"] / "local-ext"  # pyproject name "local-ext"
    bare = layout["catdir"] / "no-name"
    bare.mkdir()
    rp = ec.ResolvedPath

    assert rp(str(whl)).installer_arg() == whl.as_uri()
    assert rp(str(whl), "[a,b]").installer_arg() == f"acme_notes[a,b] @ {whl.as_uri()}"
    assert rp(str(sdist), "[x]").installer_arg() == f"team_notes[x] @ {sdist.as_uri()}"
    assert rp(str(tree), "[x]").installer_arg() == f"local-ext[x] @ {tree.as_uri()}"
    assert rp(str(bare)).installer_arg() == bare.as_uri()
    with pytest.raises(ec.CatalogError, match="cannot read its project name"):
        rp(str(bare), "[x]").installer_arg()
    with pytest.raises(ec.CatalogError, match="contains '#'"):
        rp(str(layout["catdir"] / "a#b")).installer_arg()
    # str() stays the readable form the Resolved / Install lines and the record use.
    assert str(rp(str(whl), "[a]")) == f"{whl}[a]"


async def test_a_directory_with_extras_and_no_readable_name_is_refused(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """pip takes extras on a local path only as ``name[extras] @ file:///…``; a
    directory without a ``[project] name`` gives none, so it is refused before
    consent instead of being handed a path pip and uv would re-parse."""
    (layout["catdir"] / "setup-only").mkdir()
    (layout["catdir"] / "setup-only" / "setup.py").write_text("", encoding="utf-8")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "s", "source": "./setup-only[extra]"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "s")

    assert code == 2
    assert runner.calls == []
    assert "cannot read its project name" in capsys.readouterr().err


async def test_a_posix_backslash_is_part_of_the_name(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex pass 5 (cat 4): ``./team\\notes`` names ONE directory ``team\\notes`` on
    POSIX. A resolver that normalised the backslash to ``/`` followed ``team`` (a
    link to the cwd's decoy) and passed every row; the URI carries it as ``%5C``."""
    if sys.platform == "win32":
        pytest.skip("on Windows a backslash IS the separator")
    (layout["catdir"] / "team\\notes").mkdir()
    (layout["elsewhere"] / "decoy-team" / "notes").mkdir(parents=True)
    _symlink(layout["catdir"] / "team", layout["elsewhere"] / "decoy-team", is_dir=True)
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "b", "source": "./team\\notes"}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "b")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri((layout["catdir"] / "team\\notes").resolve())
    assert "%5C" in runner.calls[0][-1]
    _no_decoy(layout, runner.calls[0])


# --- 'name @ git+…' passes unchanged (decision 3) ---------------------------


_GIT_SHA = "a" * 40


@pytest.mark.parametrize(
    "source",
    [
        "acme-notes @ git+https://h.example.invalid/o/r.git",
        f"acme-notes @ git+https://h.example.invalid/o/r.git@{_GIT_SHA}",
        "acme-notes[feature] @ git+ssh://git@h.example.invalid/o/r.git",
        # verify6 X46: a marker after the URL is still a direct reference (routed git,
        # passed unchanged), not a package spec.
        "acme-notes @ git+https://h.example.invalid/o/r.git ; python_version >= '3'",
    ],
)
async def test_a_named_git_reference_reaches_the_installer_unchanged(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], source: str
) -> None:
    """verify5 (blocking): the installer prefixed ``git+`` to the NAME —
    ``git+acme-notes @ https://…`` — which uv rejects and pip read as a path in the
    cwd (it built the cwd's decoy). The string now goes through as written, routed
    as git (its pin key is the repository, as for the bare URL)."""
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("g", source)])

    code, runner = await _install(mem, "g")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == source
    assert "Install extension from git: " in capsys.readouterr().out


def test_the_git_helpers_read_a_named_reference_by_its_url() -> None:
    named = f"acme-notes @ git+https://h.example.invalid/o/r.git@{_GIT_SHA}"
    bare = f"git+https://h.example.invalid/o/r.git@{_GIT_SHA}"
    assert ei.classify_target(named) == "git"
    assert ei.classify_target("acme-notes @ git+https://h.example.invalid/o/r.git") == "git"
    assert ei._normalize_git_spec(named) == named
    assert ei._pin_identity(named, "git") == ei._pin_identity(bare, "git")
    assert ei._target_source_key(named, "git") == ei._target_source_key(bare, "git")
    assert ei._extract_git_sha(named) == _GIT_SHA
    # scp-style ``git@host:path`` is a remote, not ``git @ host:path``
    assert ei._normalize_git_spec("git@h.example.invalid:o/r.git") == (
        "git+ssh://git@h.example.invalid/o/r.git"
    )
    assert ec.direct_reference_url("git@h.example.invalid:o/r.git") is None
    # any other direct reference stays a package-style spec the backend fetches
    assert ei.classify_target("acme-notes @ https://h.example.invalid/x.whl") == "pypi"


#: windows CI 37561017353 (uv 0.11.14): ``name @ git+file:///C:/…/repo`` — the exact
#: string aelix hands over — exits 2 with "The channel closed unexpectedly": uv's
#: resolver panics (``Git URL is invalid: AmbiguousAuthority``) on the ``:`` of the
#: drive letter followed by the ``@<sha>`` it appends, astral-sh/uv#19887, fixed in
#: uv 0.11.27 (astral-sh/uv#20086). Reproduced on POSIX with a ``C:`` directory in the
#: repository path: uv 0.11.19 panics the same way, 0.11.27 installs it
#: (.omc/probes/131-live/win/uv-git-file-colon.txt).
_UV_WIN32_GIT_FILE_PANIC = (
    "uv < 0.11.27 panics on a git+file:///C:/... URL (astral-sh/uv#19887, "
    "'The channel closed unexpectedly'); the argv aelix hands over is asserted above"
)

#: #393: the first uv release with astral-sh/uv#20086. CI runs a newer one, so the
#: windows leg installs for real; only an older ``uv`` on PATH skips.
_UV_GIT_FILE_DRIVE_FIXED = (0, 11, 27)
#: #393: the same release stopped cutting ``name @ file://…%23…`` at the ``%23``.
_UV_READS_A_NAMED_HASH_PATH_WHOLE = (0, 11, 27)


def _uv_version(uv: str) -> tuple[int, int, int] | None:
    """The version of the ``uv`` the real backend would run, from ``uv --version``
    (``uv 0.11.14 (3fdfdc7d4 2026-05-12 ...)``, ``uv 0.11.19 (Homebrew ...)``);
    ``None`` when it does not say one."""

    proc = subprocess.run([uv, "--version"], capture_output=True, timeout=60)
    match = re.match(rb"uv (\d+)\.(\d+)\.(\d+)", proc.stdout.strip())
    if proc.returncode != 0 or match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def _uv_panics_on_a_drive_letter() -> bool:
    """True only for a uv KNOWN to predate the fix: one that does not say its
    version runs the install, so the measurement never goes quiet by itself."""

    uv = shutil.which("uv")
    if uv is None:
        return False
    version = _uv_version(uv)
    return version is not None and version < _UV_GIT_FILE_DRIVE_FIXED


def _git_repo(directory: Path) -> Path | None:
    git = shutil.which("git")
    if git is None:
        return None
    _real_project(directory, "git-remote")
    for argv in (
        [git, "init", "-q"],
        [git, "add", "-A"],
        [git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(argv, cwd=directory, check=True, capture_output=True, timeout=60)
    return directory


@pytest.mark.parametrize("backend", ["uv", "pip"])
async def test_a_named_git_reference_installs_on_the_real_backend(
    layout: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], backend: str
) -> None:
    """``name @ git+file:///<repo>`` as written is what both backends parse (the
    ``name @ git+https://…`` measurement is in .omc/probes/131-live/fix6/measure-git.txt,
    over a local dumb-HTTP server); the cwd holds a decoy at what pip made of the
    old ``git+name @ …`` string."""
    _need(backend)
    repo = _git_repo(tmp_path / "repo")
    if repo is None:
        pytest.skip("no `git` on PATH")
    source = f"local-ext @ git+{repo.resolve().as_uri()}"
    # What aelix hands over, on every platform: the string as written.
    handed_code, handed = await _install(
        _seed(layout["root"], _HTTPS_LOCATION, [("g", source)]), "g"
    )
    assert handed_code == 0, capsys.readouterr().err
    assert handed.calls[0][-1] == source
    if backend == "uv" and sys.platform == "win32" and _uv_panics_on_a_drive_letter():
        pytest.skip(_UV_WIN32_GIT_FILE_PANIC)
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("g", source)])
    runner = _RealBackend(backend, tmp_path / "scratch")

    code = await run_extension_command_async(
        ["discover", "install", "g", "--yes", "--no-verify"], settings=mem, runner=runner
    )

    assert code in (0, ei._INSTALL_NOT_BOUND), (capsys.readouterr().err, runner.output)
    assert runner.calls[0][-1] == source
    assert runner.origin() == "git-remote", runner.output


# --- the collision guard compares the NAME (decision 5) ---------------------


@pytest.mark.parametrize(
    ("source", "beside", "outcome"),
    [
        # Codex pass 5 (P2): a file literally named like the versioned spec refused it.
        ("local-ext==1.0", "local-ext==1.0", "index"),
        # A version specifier says "package" (ADR-0255 (5)): not checked.
        ("local-ext==1.0", "local-ext", "index"),
        ("local-ext[extra]>=1", "local-ext", "index"),
        # No specifier: the NAME is compared, extras aside.
        ("local-ext", "local-ext", "refused"),
        ("local-ext[extra]", "local-ext", "refused"),
        ("local-ext[extra]", "local-ext[extra]", "index"),
        # A marker is refused by its own rule, whatever sits beside the catalog.
        (
            'local-ext==1.0; python_version >= "3"',
            'local-ext==1.0; python_version >= "3"',
            "marker",
        ),
    ],
)
async def test_the_neighbour_guard_compares_only_an_unversioned_name(
    layout: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    source: str,
    beside: str,
    outcome: str,
) -> None:
    if sys.platform == "win32" and any(c in beside for c in '<>:"|?*'):
        pytest.skip("not a valid Windows file name")
    shutil.rmtree(layout["catdir"] / "local-ext")
    (layout["catdir"] / beside).write_text("unrelated\n", encoding="utf-8")
    cat = layout["catdir"] / "catalog.json"
    _write_catalog(cat, [{"name": "n", "source": source}])
    mem = await _register_and_refresh(cat.as_uri())

    code, runner = await _install(mem, "n")

    err = capsys.readouterr().err
    if outcome == "index":
        assert code == 0, err
        assert runner.calls[0][-1] == source
    elif outcome == "refused":
        assert code == 2
        assert runner.calls == []
        assert "exists beside the catalog — write './local-ext" in err
    else:
        assert code == 2
        assert "is neither a package name" in err


# --- messages (decision 4) ----------------------------------------------------


@pytest.mark.parametrize("variable", ["AELIX_OFFLINE", "PI_OFFLINE"])
async def test_offline_the_not_fetched_message_says_how_to_refresh_online(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    variable: str,
) -> None:
    """verify5 N3: under ``AELIX_OFFLINE=1`` the not-fetched message advised
    ``discover --refresh``, which skips a network catalog offline. It names the
    offline skip and how to refresh online; a LOCAL catalog (fetched offline too)
    keeps the plain advice."""
    first, _ = _two_catalogs(layout)
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": str(first), "kind": "catalog"},
                {"spec": _HTTPS_LOCATION, "kind": "catalog"},
            ]
        }
    )
    monkeypatch.setenv(variable, "1")

    code, runner = await _cli(mem, "discover", "install", "x", "--catalog", _HTTPS_LOCATION)

    assert code == 2
    err = capsys.readouterr().err
    assert "a refresh skipped it while offline" in err
    assert "Fetch it online: unset AELIX_OFFLINE and PI_OFFLINE" in err
    assert "Fetch it with:" not in err

    code, _ = await _cli(mem, "discover", "install", "x", "--catalog", str(first))

    err = capsys.readouterr().err
    assert "Fetch it with: aelix extension discover --refresh" in err
    assert "skipped it while offline" not in err  # a local file is not skipped

    code, _ = await _cli(mem, "discover", "install", "x", "--catalog", "nothing-here")

    err = capsys.readouterr().err
    assert "matches no registered catalog" in err
    assert "fetch them online: unset AELIX_OFFLINE and PI_OFFLINE" in err
    assert "not known yet: run 'aelix extension discover --refresh' first" not in err


async def test_online_the_not_fetched_message_names_the_offline_skip(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": _HTTPS_LOCATION, "kind": "catalog"}]}
    )

    code, _ = await _cli(mem, "discover", "install", "x", "--catalog", _HTTPS_LOCATION)

    assert code == 2
    err = capsys.readouterr().err
    assert (
        "since it was registered, a refresh skipped it while offline (--offline, AELIX_OFFLINE or PI_OFFLINE skip a network catalog), or the cache was written"
        in err
    )
    assert "Fetch it with: aelix extension discover --refresh" in err


async def test_a_failed_https_refresh_offline_says_how_to_refresh_online(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ec.save_catalogs(
        [ec.Catalog(location=_HTTPS_LOCATION, error="failed to fetch: boom")],
        ec.cache_file_path(layout["root"] / "agent"),
    )
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": _HTTPS_LOCATION, "kind": "catalog"}]}
    )
    monkeypatch.setenv("AELIX_OFFLINE", "1")

    code, _ = await _cli(mem, "discover", "install", "x", "--catalog", _HTTPS_LOCATION)

    assert code == 2
    err = capsys.readouterr().err
    assert "whose last refresh failed: failed to fetch: boom" in err
    assert "Fix that, then fetch it again online: unset AELIX_OFFLINE" in err


async def test_a_relative_registration_read_elsewhere_names_where_its_copy_came_from(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """verify5 R1: a hand-edited ``{"spec": "catalog.json"}`` refreshed in
    ``catdir/`` (cached with ``registeredAs``); from ``elsewhere/``, ``--catalog
    ./catalog.json`` (or its absolute path) names ``elsewhere/catalog.json``. It
    said "the catalog cache has no copy recorded for it" — false: the copy read in
    ``catdir/`` is there. It says where the copy came from and how to select it;
    the spec as registered still selects it."""
    _two_catalogs(layout)
    monkeypatch.chdir(layout["catdir"])
    mem = await _register_and_refresh("catalog.json")
    monkeypatch.chdir(layout["elsewhere"])
    capsys.readouterr()
    copy = str(layout["catdir"] / "catalog.json")

    for selector in ("./catalog.json", str(layout["elsewhere"] / "catalog.json")):
        code, runner = await _cli(mem, "discover", "install", "a", "--catalog", selector)

        assert code == 2, selector
        assert runner.calls == []
        err = capsys.readouterr().err
        assert "has no copy recorded" not in err, selector
        assert "names the registered catalog 'catalog.json'" in err, selector
        assert f"but the cache's copy of it was read from '{copy}'" in err, selector

    code, runner = await _cli(
        mem, "discover", "install", "a", "--catalog", "catalog.json", "--yes", "--no-verify"
    )

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == _uri((layout["catdir"] / "local-ext").resolve())


# --- verify5's surviving mutants --------------------------------------------


async def test_every_selector_diagnosis_is_terminal_safe(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """verify5 V02-V05: the failed catalog's location and error, the registered
    location and the selector each reach a message; an OSC 52 payload in each
    prints inert."""
    evil_failed = f"https://h.example.invalid/f{_OSC52}/catalog.json"
    evil_registered = f"https://h.example.invalid/r{_OSC52}/catalog.json"
    ec.save_catalogs(
        [ec.Catalog(location=evil_failed, error=f"boom {_OSC52}")],
        ec.cache_file_path(layout["root"] / "agent"),
    )
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": evil_failed, "kind": "catalog"},
                {"spec": evil_registered, "kind": "catalog"},
            ]
        }
    )

    # The cache loader already cleans a stored error, so the failed message is also
    # asked directly with the raw values (V02 / V03).
    print(
        ei._catalog_selector_problem(
            mem, [ec.Catalog(location=evil_failed, error=f"boom {_OSC52}")], evil_failed
        ),
        file=sys.stderr,
    )
    failed = capsys.readouterr()
    await _cli(mem, "discover", "install", "x", "--catalog", evil_registered)
    registered = capsys.readouterr()
    await _cli(mem, "discover", "install", "x", "--catalog", f"nothing{_OSC52}")
    nothing = capsys.readouterr()

    assert "whose last refresh failed: boom ]52;c;cHduZWQ=" in failed.err
    assert "names the catalog 'https://h.example.invalid/f]52;c;cHduZWQ=/catalog" in failed.err
    assert "names the registered catalog 'https://h.example.invalid/r]52;" in registered.err
    assert "--catalog 'nothing]52;c;cHduZWQ=' matches no registered catalog" in nothing.err
    for captured in (failed, registered, nothing):
        _assert_inert(captured)


async def test_two_network_catalogs_are_never_the_same_file(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """verify5 V07 / V08: an https selector and an https registration both name no
    local file (``None``); ``None == None`` must not make a typo "the registered
    catalog", nor make another https catalog's copy "the copy of" an unfetched one."""
    registered = "https://registered.example.invalid/catalog.json"
    fetched = "https://fetched.example.invalid/catalog.json"
    ec.save_catalogs(
        [ec.Catalog(location=fetched, name="fetched", fetched_at=ec.now_iso())],
        ec.cache_file_path(layout["root"] / "agent"),
    )
    mem = SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": registered, "kind": "catalog"},
                {"spec": fetched, "kind": "catalog"},
            ]
        }
    )

    await _cli(mem, "discover", "install", "x", "--catalog", "https://typo.example.invalid/c.json")
    typo = capsys.readouterr().err
    await _cli(mem, "discover", "install", "x", "--catalog", registered)
    unfetched = capsys.readouterr().err

    assert "matches no registered catalog" in typo
    assert "1 registered catalog(s) have no fetched copy" in typo
    assert f"names the registered catalog '{registered}', but the catalog cache has no" in (
        unfetched
    )
    assert ec.location_matches_selector(registered, "https://typo.example.invalid/c.json") is False
    assert ec.cached_copy(ec.load_cached_catalog(layout["root"] / "agent"), registered) is None


async def test_a_healthy_catalog_beside_a_failed_one_keeps_the_plain_hint(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """verify5 V01: a selector picking a healthy catalog AND a failed one found the
    name in neither — the catalog is not the reason, so the plain hint stands."""
    ec.save_catalogs(
        [
            ec.Catalog(location=_HTTPS_LOCATION, name="team", fetched_at=ec.now_iso()),
            ec.Catalog(location="https://second.example.invalid/c.json", name="team", error="boom"),
        ],
        ec.cache_file_path(layout["root"] / "agent"),
    )
    mem = SettingsManager.in_memory()

    code, _ = await _cli(mem, "discover", "install", "nope", "--catalog", "team")

    assert code == 2
    err = capsys.readouterr().err
    assert "no catalog entry named 'nope'" in err
    assert "whose last refresh failed" not in err


async def test_the_built_in_default_counts_in_the_unfetched_note(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """verify5 V14: the note counts the effective registered list — the built-in
    default included — not only the stored sources."""
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", _HTTPS_LOCATION)
    mem = SettingsManager.in_memory()

    code, _ = await _cli(mem, "discover", "install", "x", "--catalog", "nothing-here")

    assert code == 2
    assert "1 registered catalog(s) have no fetched copy" in capsys.readouterr().err


def test_a_blank_selector_matches_no_registered_location() -> None:
    """verify5 V19: a blank selector is "no selector", never a match for a blank-ish
    registered spec."""
    assert ec.location_matches_selector("  ", "  ") is False
    assert ec.location_matches_selector("", "") is False


def test_index_relative_to_stdout_from_a_literal_tilde_directory(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """verify5 W23 (round 4 called it equivalent; it is not): ``index '~/wheels'``
    with the ``~`` unexpanded (as cmd.exe passes it) measures from the EXPANDED,
    resolved directory — the argument as typed gave ``../../../home/wheels/x.whl``."""
    home = layout["root"] / "home"
    sandbox_home(monkeypatch, home)
    wheels = home / "wheels"
    wheels.mkdir(parents=True)
    whl = _wheel(wheels, "local-ext", "0.1.0")

    assert ei.run_extension_command(["index", "~/wheels", "--out", "-", "--relative"]) == 0

    doc = json.loads(capsys.readouterr().out)
    assert doc["extensions"][0]["source"] == f"./{whl.name}"


# === review round 7 (2026-10-07): the final round ==========================
#
# verify6 B1 (a round-6 regression): an scp-style git target whose user is not
# ``git`` read as a PEP 508 direct reference. verify6 B2: ``update`` re-installed a
# recorded catalog path from a string the backend parsed again. Codex 6 P1: a file
# URL with an upper-case ``localhost`` or a percent-escape opened another path on uv.
# Codex 6 cat 2 / 4 and verify6's two surviving mutants (X39, X46).


_SCP_TARGETS = [
    ("alice@h.example.invalid:o/r.git", "git+ssh://alice@h.example.invalid/o/r.git"),
    ("deploy@git.corp.invalid:team/ext.git", "git+ssh://deploy@git.corp.invalid/team/ext.git"),
    ("alice@h.example.invalid:o/r", "git+ssh://alice@h.example.invalid/o/r"),
    ("git@h.example.invalid:o/r.git", "git+ssh://git@h.example.invalid/o/r.git"),
]


@pytest.mark.parametrize(("target", "argv"), _SCP_TARGETS)
def test_an_scp_target_with_any_user_is_git(target: str, argv: str) -> None:
    """verify6 B1: round 6 excluded only ``git@`` from the direct-reference reading,
    so ``alice@h:o/r.git`` became ``alice @ h:o/r.git`` — kind pypi, handed raw, and
    uv read ``h:o/r.git`` as a path in the cwd. Any ``<user>@host:path`` is git, as
    on aab1f210 (and, without ``.git``, now too)."""
    assert ec.is_scp_git(target)
    assert ec.direct_reference_url(target) is None
    assert ei.classify_target(target) == "git"
    assert ei._normalize_git_spec(target) == argv
    assert ei.build_pip_args(target, "git")[-1] == argv
    assert ei.classify_source(target) == "git"


@pytest.mark.parametrize(
    "spec",
    [
        "local-ext@file:local-ext",  # a host spelled like a scheme: the 'name @ file:' ref
        "local-ext@./local-ext",
        "local-ext @ h.example.invalid:o/r.git",  # spaces: PEP 508, not scp
        "alice@h.example.invalid://o/r",
        "alice@h.example.invalid:",
    ],
)
def test_what_is_not_an_scp_target(spec: str) -> None:
    assert not ec.is_scp_git(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "C:\\x",
        "C:x",
        "C:/x",
        "c:/x",
        "C:\\Users\\me@corp.example.invalid\\ext",  # an '@' inside the path
        "C:/me@h.example.invalid:o/r",
        "C:x@h.example.invalid:o/r",
    ],
)
def test_a_windows_drive_path_is_never_an_scp_target(spec: str) -> None:
    """Windows CI 37561017353 sweep: a drive path (``C:\\x``, drive-relative ``C:x``,
    ``C:/x``) is never scp-style git, on any platform — the scp user cannot hold a
    ``:``, a ``/`` or a ``\\``, so a drive letter can never be read as ``<user>@<host>``;
    nor is it a direct reference, and the catalog reads it as path-shaped first."""
    assert not ec.is_scp_git(spec)
    assert ec.direct_reference_url(spec) is None
    assert ei.classify_target(spec) != "git"
    assert ec._starts_like_a_path(spec)


@pytest.mark.parametrize(("target", "argv"), _SCP_TARGETS[:2])
async def test_a_typed_scp_install_with_any_user_reaches_the_installer_as_git(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], target: str, argv: str
) -> None:
    mem = SettingsManager.in_memory()

    code, runner = await _cli(mem, "install", target, "--yes", "--no-verify")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == argv
    assert f"Install extension from git: {target}" in capsys.readouterr().out


@pytest.mark.parametrize(("target", "argv"), _SCP_TARGETS[:2])
async def test_source_add_registers_an_scp_target_with_any_user(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], target: str, argv: str
) -> None:
    """verify6 B1: ``source add deploy@git.corp:team/ext.git`` said "is not a valid
    source" on round 6."""
    mem = SettingsManager.in_memory()

    code, _runner = await _cli(mem, "source", "add", target)

    assert code == 0, capsys.readouterr().err
    assert [(s.spec, s.kind) for s in mem.get_extension_sources()] == [(argv, "git")]


@pytest.mark.parametrize("catalog_kind", ["local", "https"])
async def test_an_scp_catalog_source_with_any_user_is_accepted(
    layout: dict[str, Path], catalog_kind: str
) -> None:
    source = "deploy@git.corp.invalid:team/ext.git"
    if catalog_kind == "local":
        cat = layout["catdir"] / "catalog.json"
        _write_catalog(cat, [{"name": "g", "source": source}])
        mem = await _register_and_refresh(cat.as_uri())
    else:
        mem = _seed(layout["root"], _HTTPS_LOCATION, [("g", source)])

    code, runner = await _install(mem, "g")

    assert code == 0
    assert runner.calls[0][-1] == "git+ssh://deploy@git.corp.invalid/team/ext.git"


@pytest.mark.parametrize("backend", ["uv", "pip"])
async def test_a_typed_scp_install_never_reaches_a_cwd_path_on_the_real_backend(
    layout: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend: str,
) -> None:
    """verify6 B1, measured: from a cwd holding a git repository at
    ``h.example.invalid:o/r.git``, round 6 handed ``alice@h.example.invalid:o/r.git``
    raw and uv opened ``file://<cwd>/h.example.invalid:o/r.git``. Now the backend
    gets ``git+ssh://alice@…`` (offline, no ssh: it fails without touching the cwd).

    The decoy cannot exist on Windows (``:`` is not allowed in a file name, WinError
    123 on windows CI 37561017353), so neither can the cwd substitution it measures;
    the classification and the argv are asserted on every platform first."""
    _need(backend)
    target = "alice@h.example.invalid:o/r.git"
    assert ei.classify_target(target) == "git"
    handed_code, handed = await _cli(
        SettingsManager.in_memory(), "install", target, "--yes", "--no-verify"
    )
    assert handed_code == 0, capsys.readouterr().err
    assert handed.calls[0][-1] == "git+ssh://alice@h.example.invalid/o/r.git"
    if sys.platform == "win32":
        pytest.skip("':' is not allowed in a Windows directory name: the cwd decoy cannot exist")
    if _git_repo(layout["elsewhere"] / "h.example.invalid:o" / "r.git") is None:
        pytest.skip("no `git` on PATH")
    monkeypatch.setenv("GIT_SSH_COMMAND", "false")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    runner = _RealBackend(backend, tmp_path / "scratch")

    await run_extension_command_async(
        ["install", target, "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
        runner=runner,
    )

    assert runner.calls[0][-1] == "git+ssh://alice@h.example.invalid/o/r.git"
    assert runner.origin() is None, runner.output
    assert str(layout["elsewhere"].resolve()) not in runner.output, runner.output
    assert "file://" not in runner.output, runner.output


# --- verify6 B2: update re-installs a recorded path through the URI ---------


def test_a_path_record_is_the_installer_uri_and_reads_back(layout: dict[str, Path]) -> None:
    whl = _wheel(layout["catdir"], "acme-notes", "1.4.0")
    odd = layout["catdir"] / "trusted[]"
    odd.mkdir()
    rp = ec.ResolvedPath

    # a typed path keeps the plain absolute path it always recorded
    assert ei._path_record_spec(str(whl)) == str(whl.resolve())
    for placed in (rp(str(whl)), rp(str(whl), "[a,b]"), rp(str(odd))):
        spec = ei._path_record_spec(placed)
        assert spec == placed.installer_arg()
        assert ec.resolved_path_from_installer_arg(spec) == placed
        assert ei._recorded_path_target(spec) == placed
        assert ei._source_identity(spec, "path") == (
            f"{Path(placed.path).resolve()}{placed.extras}"
        )
    # an older record (the plain absolute path) dedupes with the URI record
    assert ei._source_identity(str(odd), "path") == ei._source_identity(odd.as_uri(), "path")
    # anything else is not an installer URI
    for other in (str(whl), "https://h.example.invalid/x.whl", "x @ file:///a ; os_name == 'nt'"):
        assert ec.resolved_path_from_installer_arg(other) is None


@pytest.mark.parametrize(
    "name",
    [
        "trusted[]",  # '[' and ']' are valid Windows file-name characters
        pytest.param(
            "trusted ",
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="Windows strips a trailing space: 'trusted ' IS 'trusted' there "
                "(WinError 183 on windows CI 37561017353)",
            ),
        ),
    ],
)
def test_an_older_bare_path_record_keeps_a_whole_name_that_exists(
    layout: dict[str, Path], name: str
) -> None:
    """A record an older version wrote is the plain path: the whole string when it
    exists (``trusted[]``, ``trusted `` — never stripped), beside a ``trusted`` too."""
    catdir = layout["catdir"]
    (catdir / name).mkdir()
    (catdir / "trusted").mkdir()
    assert ei._recorded_path_target(str(catdir / name)) == ec.ResolvedPath(
        str((catdir / name).resolve())
    )


def test_an_older_bare_path_record_is_re_derived(layout: dict[str, Path]) -> None:
    """A record an older version wrote is the plain path: its ``[extras]`` split off
    only when the whole is missing and the rest exists (the whole name that exists:
    :func:`test_an_older_bare_path_record_keeps_a_whole_name_that_exists`)."""
    catdir = layout["catdir"]
    # the whole name wins when it exists, even when the part before '[x]' does too
    (catdir / "brk[x]").mkdir()
    (catdir / "brk").mkdir()
    assert ei._recorded_path_target(str(catdir / "brk[x]")) == ec.ResolvedPath(
        str((catdir / "brk[x]").resolve())
    )
    whl = _wheel(catdir, "acme-notes", "1.4.0")
    assert ei._recorded_path_target(f"{whl}[feature]") == ec.ResolvedPath(
        str(whl.resolve()), "[feature]"
    )
    gone = catdir / "gone-ext"
    assert ei._recorded_path_target(str(gone)) == ec.ResolvedPath(str(gone.resolve()))


@pytest.mark.parametrize("backend", ["uv", "pip"])
@pytest.mark.parametrize("name", ["trusted[]", "trusted "])
@pytest.mark.parametrize("record", ["this-version", "older-version"])
async def test_update_reinstalls_the_catalog_copy_a_record_names(
    layout: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    name: str,
    record: str,
) -> None:
    """verify6 B2, measured on round 6: ``discover install`` of ``./link`` ->
    ``trusted[]`` / ``trusted `` installed the catalog copy, then ``update``
    re-installed from the recorded STRING, which uv (and pip for ``trusted ``) read
    as the sibling ``trusted`` — a decoy. The record is the installer URI now, and an
    older record (the bare path) is re-derived into one: the catalog copy again."""
    _need(backend)
    if sys.platform == "win32":
        pytest.skip("these names are not valid on Windows")
    catdir = layout["catdir"]
    real = _real_project(catdir / name, "catalog")
    _decoy_link(catdir, "trusted", layout["elsewhere"], _real_project)
    _symlink(catdir / "link", real, is_dir=True)
    cat = catdir / "catalog.json"
    _write_catalog(cat, [{"name": "probe", "source": "./link"}])
    mem = await _register_and_refresh(cat.as_uri())
    runner = _RealBackend(backend, tmp_path / "scratch")
    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem, runner=runner
    )
    assert code in (0, ei._INSTALL_NOT_BOUND), (capsys.readouterr().err, runner.output)
    assert runner.origin() == "catalog", runner.output
    paths = [s for s in mem.get_extension_sources() if s.kind == "path"]
    assert [s.spec for s in paths] == [real.resolve().as_uri()]
    if record == "older-version":
        from aelix_ai.settings import ExtensionSourceObject

        others = [s for s in mem.get_extension_sources() if s.kind != "path"]
        mem.set_extension_sources(
            [*others, ExtensionSourceObject(spec=str(real.resolve()), kind="path")]
        )

    code = await run_extension_command_async(
        ["update", "--yes", "--no-verify"], settings=mem, runner=runner
    )

    assert code in (0, ei._INSTALL_NOT_BOUND), (capsys.readouterr().err, runner.output)
    assert runner.calls[-1][-1] == real.resolve().as_uri()
    assert "--upgrade" in runner.calls[-1]
    assert runner.origin() == "catalog", runner.output


async def test_update_with_default_verification_hands_the_staged_copy_as_a_uri(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The verify-and-stage copy of a recorded path goes over as a URI too (a
    ``ResolvedPath`` into the same gate ``discover install`` uses)."""
    if sys.platform == "win32":
        pytest.skip("POSIX file names")
    whl = _real_wheel(layout["catdir"] / "w[]", "catalog")
    from aelix_ai.settings import ExtensionSourceObject

    mem = SettingsManager.in_memory()
    mem.set_extension_sources([ExtensionSourceObject(spec=str(whl), kind="path")])

    code, runner = await _cli(mem, "update", "--yes")

    assert code == 0, capsys.readouterr().err
    handed = runner.calls[-1][-1]
    assert handed.startswith("file://"), handed
    assert handed.endswith("/local_ext-1.0-py3-none-any.whl"), handed


# --- Codex 6 P1: file URL host and percent-escapes, on the real backend ------


@pytest.mark.parametrize("backend", ["uv", "pip"])
@pytest.mark.parametrize(
    "template",
    [
        "file://LOCALHOST{posix}",
        "file://LocalHost{posix}",
        "local-ext @ file://LOCALHOST{posix}",
    ],
)
async def test_an_upper_case_localhost_file_url_never_installs_the_cwd_copy(
    layout: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    template: str,
) -> None:
    """Codex 6 P1, measured on round 6: uv read ``file://LOCALHOST/<abs>`` as
    ``<cwd>/LOCALHOST/<abs>`` and installed a cwd decoy (exit 0). Refused before
    the installer runs, with the ``file:///`` spelling to use."""
    _need(backend)
    if os.name == "nt":
        pytest.skip("file://<host> + a drive path is a POSIX-only spelling")
    whl = _real_wheel(layout["catdir"] / "wheels", "catalog").resolve()
    host = template.split("//", 1)[1].split("{", 1)[0]
    _real_wheel(layout["elsewhere"] / host / str(whl.parent).lstrip("/"), "decoy")
    source = template.replace("{posix}", whl.as_posix())
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("probe", source)])
    runner = _RealBackend(backend, tmp_path / "scratch")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem, runner=runner
    )

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert f"naming the host '{host}'" in err
    assert "write file:///<absolute path>" in err


@pytest.mark.parametrize("backend", ["uv", "pip"])
@pytest.mark.parametrize("named", [False, True])
async def test_a_percent_escaped_file_url_is_refused_before_the_backend(
    layout: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    named: bool,
) -> None:
    """Codex 6 P1, measured on round 6: ``file:///<dir>/trusted%23release`` — the
    catalog's ``trusted#release`` — installed the sibling ``trusted`` on uv (exit
    0). A file URL whose path holds any ``%`` is refused; the message says to name
    the path directly."""
    _need(backend)
    if sys.platform == "win32":
        pytest.skip("POSIX file names")
    real = _real_project(layout["catdir"] / "trusted#release", "catalog")
    _real_project(layout["catdir"] / "trusted", "decoy")
    url = real.resolve().as_uri()
    assert "%23" in url
    source = f"local-ext @ {url}" if named else url
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("probe", source)])
    runner = _RealBackend(backend, tmp_path / "scratch")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem, runner=runner
    )

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "holds a percent-escape" in err
    assert "name the path directly instead" in err


def test_uv_reads_an_upper_case_localhost_and_a_percent_escape_elsewhere(
    layout: dict[str, Path], tmp_path: Path
) -> None:
    """WHY both are refused (uv 0.11): ``file://LOCALHOST/<abs>`` installs
    ``<cwd>/LOCALHOST/<abs>``; ``file:///<dir>/trusted%23release`` installs
    ``<dir>/trusted``. Measured from the test's cwd (``elsewhere/``)."""
    _need("uv")
    if os.name == "nt":
        pytest.skip("POSIX-only spellings")
    whl = _real_wheel(layout["catdir"] / "wheels", "catalog").resolve()
    _real_wheel(layout["elsewhere"] / "LOCALHOST" / str(whl.parent).lstrip("/"), "cwd")
    real = _real_project(layout["catdir"] / "trusted#release", "catalog")
    _real_project(layout["catdir"] / "trusted", "sibling")
    for spec, want in (
        (f"file://LOCALHOST{whl.as_posix()}", "cwd"),
        (real.resolve().as_uri(), "sibling"),
    ):
        runner = _RealBackend("uv", tmp_path / f"s-{want}")
        runner([sys.executable, "-m", "pip", "install", spec])
        assert runner.origin() == want, (spec, runner.output)


# --- Codex 6 cat 2: messages true of what happens ---------------------------


async def test_the_cwd_collision_message_names_aelix_s_own_reading(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex 6: for ``review-ext[feature]`` beside a cwd directory literally named so,
    the refusal said the package "would be installed from there" — pip and uv ignore
    that directory. It is aelix's own ``classify_target`` that reads a target existing
    on disk as a path; the message says so (and the install still never reaches it)."""
    (layout["elsewhere"] / "review-ext[feature]").mkdir()
    assert ei.classify_target("review-ext[feature]") == "path"
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("probe", "review-ext[feature]")])

    code, runner = await _install(mem, "probe")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert (
        "a file or directory named 'review-ext[feature]' exists in the current "
        "directory, and aelix's installer takes a target that exists on disk for a "
        "local path — it would install that instead of the package" in err
    )


def test_the_extras_refusal_says_why_aelix_needs_the_name(layout: dict[str, Path]) -> None:
    """Codex 6: "pip takes extras on a local path only as 'name[extras] @ …'" was
    false (pip and uv take '<abs path>[feature]'); the need comes from aelix's URI
    hand-off."""
    bare = layout["catdir"] / "no-name"
    bare.mkdir()

    with pytest.raises(ec.CatalogError) as exc:
        ec.ResolvedPath(str(bare), "[x]").installer_arg()

    text = str(exc.value)
    assert "aelix hands a local path to the installer as a file:// URI" in text
    assert "extras on a URI need the project's name" in text
    assert "pip takes extras" not in text


# --- Codex 6 cat 4 / verify6 survivors ---------------------------------------


@pytest.mark.parametrize(
    ("file_name", "project"),
    [
        ("review-ext-1.0.tar.gz", "review-ext"),
        ("a-b-c-2.0.1.zip", "a-b-c"),
        ("review_ext-1.0-py3-none-any.whl", "review_ext"),
    ],
)
def test_a_dashed_sdist_name_keeps_every_dash_but_the_version_one(
    layout: dict[str, Path], file_name: str, project: str
) -> None:
    """Codex 6 cat 4: ``stem.split("-", 1)`` (the wheel rule) passed all 414 rows and
    asked for ``review[feature]`` — both backends then refused the metadata name
    ``review-ext``. An sdist's name is everything before its LAST dash."""
    artifact = layout["catdir"] / file_name
    artifact.write_bytes(b"x")

    assert ec.local_project_name(artifact) == project
    assert ec.ResolvedPath(str(artifact), "[feature]").installer_arg() == (
        f"{project}[feature] @ {artifact.as_uri()}"
    )


async def test_a_relative_registration_whose_refresh_failed_elsewhere_says_it_failed(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """verify6 X39: the selector names a registered catalog through a file the cache
    did not read it from (a relative ``{"spec": "catalog.json"}`` refreshed in
    ``catdir/``, selected as ``./catalog.json`` from ``elsewhere/``) and that copy is
    an ERROR row: the message is the failed refresh with its error, not "read from
    another file"."""
    _two_catalogs(layout)
    (layout["catdir"] / "catalog.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(layout["catdir"])
    mem = SettingsManager.in_memory(
        {"extensionSources": [{"spec": "catalog.json", "kind": "catalog"}]}
    )
    # every registered catalog failed, so the refresh itself exits 2
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 2
    monkeypatch.chdir(layout["elsewhere"])
    capsys.readouterr()

    code, runner = await _cli(mem, "discover", "install", "a", "--catalog", "./catalog.json")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "whose last refresh failed: " in err
    assert "is not valid UTF-8 JSON" in err
    assert "the cache's copy of it was read from" not in err


# === verify round 7 (2026-10-07) ============================================
#
# Item 1 (a regression): a TYPED path record with extras on a project with no
# ``[project] name`` made ``update`` raise ``CatalogError`` from ``installer_arg`` —
# a traceback, rc 1, and every later extension skipped. A plain record the URI
# cannot carry keeps the typed hand-off; a catalog (URI) record keeps the URI; one
# extension's error never stops the others. Item 3: the ``git+file:`` refusals said
# what uv does with a ``file:`` URL. Item 4: no row had an scp user holding ``.``,
# ``_`` or ``~``.

_LEGACY_PYPROJECT = (
    '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'
)
#: The in-tree backend, its wheel asking for ``featdep`` under the extra ``feature``.
_LEGACY_BACKEND = _BACKEND.replace(
    "Provides-Extra: feature", "Provides-Extra: feature\\nRequires-Dist: featdep; extra == 'feature'"
)


def _legacy_project(directory: Path, origin: str) -> Path:
    """A ``[build-system]``-only project: no ``[project] name`` for aelix to read."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(_LEGACY_PYPROJECT, encoding="utf-8")
    (directory / "backend.py").write_text(_LEGACY_BACKEND, encoding="utf-8")
    (directory / "ORIGIN").write_text(origin, encoding="utf-8")
    assert ec.local_project_name(directory) is None
    return directory


def _featdep_links(directory: Path) -> Path:
    """A find-links directory holding the extra's dependency ``featdep``."""

    directory.mkdir(parents=True, exist_ok=True)
    di = "featdep-1.0.dist-info"
    files = {
        f"{di}/METADATA": "Metadata-Version: 2.1\nName: featdep\nVersion: 1.0\n",
        f"{di}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "featdep.py": "X = 1\n",
    }
    files[f"{di}/RECORD"] = "".join(f"{p},,\n" for p in [*files, f"{di}/RECORD"])
    with zipfile.ZipFile(directory / "featdep-1.0-py3-none-any.whl", "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return directory


def test_a_typed_path_record_the_uri_cannot_carry_keeps_the_typed_hand_off(
    layout: dict[str, Path],
) -> None:
    """verify7 item 1: the plain record ``<abs>/legacy[feature]`` (what a typed
    ``install ./legacy[feature]`` wrote) is handed back as the user's string — the
    absolute path with its extras, as round 6 did — never a ``ResolvedPath`` whose
    URI hand-off raises. A named project's record still goes over as the URI, and a
    catalog record (the URI) keeps the URI hand-off even when it cannot be built."""
    legacy = _legacy_project(layout["catdir"] / "legacy", "typed")
    record = f"{legacy.resolve()}[feature]"

    target = ei._recorded_path_target(record)

    assert target == record
    assert isinstance(target, str)
    assert ei.classify_target(target) == "path"
    assert ei.build_pip_args(target, "path", upgrade=True)[-2:] == ["--upgrade", record]
    named = _real_project(layout["catdir"] / "named", "named")
    assert ei._recorded_path_target(f"{named.resolve()}[feature]") == ec.ResolvedPath(
        str(named.resolve()), "[feature]"
    )
    uri_record = f"local-ext[feature] @ {legacy.resolve().as_uri()}"
    kept = ei._recorded_path_target(uri_record)
    assert kept == ec.ResolvedPath(str(legacy.resolve()), "[feature]")
    with pytest.raises(ec.CatalogError, match="cannot read its project name"):
        kept.installer_arg()  # type: ignore[union-attr]


@pytest.mark.parametrize("verify", ["--no-verify", "default"])
async def test_update_of_a_typed_legacy_record_with_extras_runs(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], verify: str
) -> None:
    """verify7 item 1 through the CLI, with and without default verification: the
    typed install and the update of its record both hand the absolute path with the
    extras (on 7dc0e05d the update raised from ``installer_arg``)."""
    legacy = _legacy_project(layout["elsewhere"] / "legacy", "typed")
    flags = ["--yes", "--no-verify"] if verify == "--no-verify" else ["--yes"]
    mem = SettingsManager.in_memory()
    code, runner = await _cli(mem, "install", "./legacy[feature]", *flags)
    assert code == 0, capsys.readouterr().err
    record = f"{legacy.resolve()}[feature]"
    assert [(s.spec, s.kind) for s in mem.get_extension_sources()] == [(record, "path")]

    code, runner = await _cli(mem, "update", *flags)

    assert code == 0, capsys.readouterr().err
    assert runner.calls[-1][-2:] == ["--upgrade", record]
    assert [(s.spec, s.kind) for s in mem.get_extension_sources()] == [(record, "path")]


@pytest.mark.parametrize("backend", ["uv", "pip"])
async def test_update_reinstalls_a_typed_legacy_path_with_its_extra_on_the_real_backend(
    layout: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    backend: str,
) -> None:
    """verify7 item 1, measured: ``install ./legacy[feature]`` works on both backends;
    on 7dc0e05d ``update`` of its record raised. Now it re-installs the same project
    WITH the extra's dependency (``featdep``), as the install did."""
    _need(backend)
    legacy = _legacy_project(layout["elsewhere"] / "legacy", "typed-legacy")
    links = _featdep_links(tmp_path / "links")
    runner = _RealBackend(
        backend,
        tmp_path / "scratch",
        env={"UV_FIND_LINKS": str(links), "PIP_FIND_LINKS": str(links)},
    )
    mem = SettingsManager.in_memory()
    code = await run_extension_command_async(
        ["install", "./legacy[feature]", "--yes", "--no-verify"], settings=mem, runner=runner
    )
    assert code in (0, ei._INSTALL_NOT_BOUND), (capsys.readouterr().err, runner.output)
    assert (runner.target / "featdep.py").is_file(), runner.output
    record = f"{legacy.resolve()}[feature]"

    code = await run_extension_command_async(
        ["update", "--yes", "--no-verify"], settings=mem, runner=runner
    )

    assert code in (0, ei._INSTALL_NOT_BOUND), (capsys.readouterr().err, runner.output)
    assert runner.calls[-1][-2:] == ["--upgrade", record]
    assert runner.origin() == "typed-legacy", runner.output
    assert (runner.target / "featdep.py").is_file(), runner.output


@pytest.mark.parametrize("order", ["failing-first", "failing-last"])
async def test_a_record_that_cannot_be_updated_never_stops_the_others(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], order: str
) -> None:
    """verify7 item 1: one record's error escaped ``update`` as a traceback and every
    later record was skipped. A catalog record (the URI) whose project lost its name
    still cannot be handed over — that is reported, the healthy record IS updated,
    and the exit code says one failed (2: it never ran)."""
    from aelix_ai.settings import ExtensionSourceObject

    legacy = _legacy_project(layout["catdir"] / "legacy", "x")
    healthy = _real_wheel(layout["catdir"] / "wheels", "healthy").resolve()
    failing = ExtensionSourceObject(
        spec=f"local-ext[feature] @ {legacy.resolve().as_uri()}", kind="path"
    )
    ok = ExtensionSourceObject(spec=str(healthy), kind="path")
    mem = SettingsManager.in_memory()
    mem.set_extension_sources([failing, ok] if order == "failing-first" else [ok, failing])

    code, runner = await _cli(mem, "update", "--yes", "--no-verify")

    assert code == ei._EXIT_DIDNT_RUN
    assert [call[-1] for call in runner.calls] == [healthy.as_uri()]
    captured = capsys.readouterr()
    assert "Error: could not update " in captured.err
    assert "cannot read its project name" in captured.err
    assert "update summary: 2 pack(s)" in captured.out
    assert "1 failed" in captured.out


async def test_an_unexpected_error_in_one_update_is_reported_terminal_safe(
    layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any exception from one record is caught, reported through the terminal-safe
    filter, and the next record still runs."""
    from aelix_ai.settings import ExtensionSourceObject

    first = _real_wheel(layout["catdir"] / "one", "one").resolve()
    second = _real_wheel(layout["catdir"] / "two", "two").resolve()
    mem = SettingsManager.in_memory()
    mem.set_extension_sources(
        [
            ExtensionSourceObject(spec=str(first), kind="path"),
            ExtensionSourceObject(spec=str(second), kind="path"),
        ]
    )
    real = ei._upgrade_source

    def flaky(source, *args, **kwargs):  # type: ignore[no-untyped-def]
        if source.spec == str(first):
            raise RuntimeError("boom \x1b[2J")
        return real(source, *args, **kwargs)

    monkeypatch.setattr(ei, "_upgrade_source", flaky)

    code, runner = await _cli(mem, "update", "--yes", "--no-verify")

    assert code == ei._EXIT_DIDNT_RUN
    assert [call[-1] for call in runner.calls] == [second.as_uri()]
    err = capsys.readouterr().err
    assert "Error: could not update " in err
    assert "boom" in err
    assert "\x1b" not in err


_GIT_FILE_REFUSED = [
    "git+file://h.example.invalid/abs/repo",
    "git+file://LOCALHOST/abs/repo",
    "git+file://localhost.evil/abs/repo",
    "git+file:///abs/t%23r",
    "git+file://localhost/abs/t%20r",
    "local-ext @ git+file://h.example.invalid/abs/repo",
    "local-ext @ git+file:///abs/t%23r",
]


@pytest.mark.parametrize("source", _GIT_FILE_REFUSED)
async def test_a_git_file_refusal_gives_its_own_reason_not_the_file_one(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], source: str
) -> None:
    """verify7 item 3: the git+file: refusals reused the file: reasons — "uv reads
    such a host as a directory under the current directory", "uv reads '%23' as
    '#'" — measured false for git+file: (git ignores the host; for '%23' it is pip
    that cuts, test_what_the_backends_do_with_a_git_file_host_and_a_percent_escape).
    The refusal stays, with git+file:'s own words."""
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("probe", source)])

    code, runner = await _install(mem, "probe")

    assert code == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "write git+file:///<absolute path>" in err
    assert "uv reads" not in err
    assert "current directory" not in err
    if "%" in source:
        assert "pip cuts the path at a '%23' and clones another repository" in err
    else:
        assert "an unsupported spelling" in err


@pytest.mark.parametrize(
    ("source", "why"),
    [
        ("file://h.example.invalid/abs/x", "uv reads such a host as a directory under the current"),
        ("file://LOCALHOST/abs/x", "uv reads any other spelling of the host as a directory"),
        ("file:///abs/t%23r", "uv reads '%23' as '#' and cuts the path there"),
    ],
)
async def test_a_file_refusal_keeps_the_uv_reason_it_was_measured_for(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], source: str, why: str
) -> None:
    mem = _seed(layout["root"], _HTTPS_LOCATION, [("probe", source)])

    code, _runner = await _install(mem, "probe")

    assert code == 2
    assert why in capsys.readouterr().err


def _origin_repo(directory: Path, origin: str) -> Path:
    git = shutil.which("git")
    assert git is not None
    _real_project(directory, origin)
    for argv in (
        [git, "init", "-q"],
        [git, "add", "-A"],
        [git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(argv, cwd=directory, check=True, capture_output=True, timeout=60)
    return directory


@pytest.mark.parametrize("backend", ["uv", "pip"])
def test_what_the_backends_do_with_a_git_file_host_and_a_percent_escape(
    layout: dict[str, Path], tmp_path: Path, backend: str
) -> None:
    """WHY the git+file: refusals say what they say (uv 0.11.19, pip 26.2.1; the
    verify7 measurement, .omc/probes/131-live/verify7/gitfile-measure.txt): git
    ignores the host — ``git+file://h<abs>`` and ``git+file://LOCALHOST<abs>`` clone
    ``<abs>`` on both backends, never ``<cwd>/h<abs>`` — and ``git+file:///<dir>/t%23r``
    clones ``t#r`` on uv but the sibling ``t`` on pip."""
    _need(backend)
    if os.name == "nt" or shutil.which("git") is None:
        pytest.skip("POSIX paths and `git` on PATH")
    root = (tmp_path / "g").resolve()
    repo = _origin_repo(root / "repo", "abs-repo")
    for host in ("h", "LOCALHOST"):
        _origin_repo(layout["elsewhere"] / host / str(repo).lstrip("/"), "cwd-decoy")
    _origin_repo(root / "t#r", "hash-repo")
    _origin_repo(root / "t", "sibling")
    runner = _RealBackend(backend, tmp_path / "scratch")
    seen = {}
    for label, spec in (
        ("h", f"local-ext @ git+file://h{repo}"),
        ("LOCALHOST", f"local-ext @ git+file://LOCALHOST{repo}"),
        ("pct", f"local-ext @ git+file://{root}/t%23r"),
    ):
        runner(["install", spec])
        seen[label] = runner.origin()

    assert seen["h"] == "abs-repo", runner.output
    assert seen["LOCALHOST"] == "abs-repo", runner.output
    assert seen["pct"] == ("hash-repo" if backend == "uv" else "sibling"), runner.output


_SCP_DOTTED = [
    ("first.last@git.corp.invalid:team/ext.git", "git+ssh://first.last@git.corp.invalid/team/ext.git"),
    ("first.last@git.corp.invalid:team/ext", "git+ssh://first.last@git.corp.invalid/team/ext"),
    ("a_b~c@git.corp.invalid:team/ext.git", "git+ssh://a_b~c@git.corp.invalid/team/ext.git"),
    ("a_b~c@git.corp.invalid:team/ext", "git+ssh://a_b~c@git.corp.invalid/team/ext"),
]


@pytest.mark.parametrize(("target", "argv"), _SCP_DOTTED)
def test_an_scp_user_with_dot_underscore_or_tilde_is_git(target: str, argv: str) -> None:
    """verify7 item 4: no row had such a user, so dropping ``.`` from the scp user
    class passed every row."""
    assert ec.is_scp_git(target)
    assert ec.direct_reference_url(target) is None
    assert ei.classify_target(target) == "git"
    assert ei.build_pip_args(target, "git")[-1] == argv
    assert ei.classify_source(target) == "git"


@pytest.mark.parametrize(("target", "argv"), _SCP_DOTTED)
async def test_a_typed_scp_install_with_a_dotted_user_reaches_the_installer_as_git(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], target: str, argv: str
) -> None:
    code, runner = await _cli(SettingsManager.in_memory(), "install", target, "--yes", "--no-verify")

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][-1] == argv
    assert f"Install extension from git: {target}" in capsys.readouterr().out


@pytest.mark.parametrize(("target", "argv"), _SCP_DOTTED)
async def test_source_add_registers_an_scp_target_with_a_dotted_user(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str], target: str, argv: str
) -> None:
    mem = SettingsManager.in_memory()

    code, _runner = await _cli(mem, "source", "add", target)

    assert code == 0, capsys.readouterr().err
    assert [(s.spec, s.kind) for s in mem.get_extension_sources()] == [(argv, "git")]


@pytest.mark.parametrize("catalog_kind", ["local", "https"])
@pytest.mark.parametrize(("source", "argv"), _SCP_DOTTED)
async def test_an_scp_catalog_source_with_a_dotted_user_is_accepted(
    layout: dict[str, Path], catalog_kind: str, source: str, argv: str
) -> None:
    if catalog_kind == "local":
        cat = layout["catdir"] / "catalog.json"
        _write_catalog(cat, [{"name": "g", "source": source}])
        mem = await _register_and_refresh(cat.as_uri())
    else:
        mem = _seed(layout["root"], _HTTPS_LOCATION, [("g", source)])

    code, runner = await _install(mem, "g")

    assert code == 0
    assert runner.calls[0][-1] == argv


_REPO = Path(__file__).resolve().parents[2]
_THREAT_MODEL_DOCS = (
    "docs/guides/private-catalog.md",
    "packages/aelix-coding-agent/src/aelix_coding_agent/docs/private-catalog.md",
    "docs/decisions/0255-a-catalog-source-is-placed-by-its-catalog-not-the-cwd.md",
    "CHANGELOG.md",
)


@pytest.mark.parametrize("doc", _THREAT_MODEL_DOCS)
def test_the_threat_model_claims_only_what_131_guarantees(doc: str) -> None:
    """verify7 item 2: the guide (and its bundled copy), ADR-0255 §12 and the
    CHANGELOG said a trusted catalog's benign entry — a package name included — is
    never satisfied from the directory aelix runs in. On uv a ``uv.toml`` /
    ``[tool.uv]`` there redirected a package entry (aelix honours uv's own config,
    ADR-0200). The claim is what #131 does — no source resolved against the cwd, no
    string the installer would read from there — and the uv config is a stated
    limit."""
    text = (_REPO / doc).read_text(encoding="utf-8")
    if doc.startswith("docs/decisions/"):
        text = text[text.index("## 12. Threat model") : text.index("## 13.")]
    text = " ".join(text.split())
    for old in (
        "is never satisfied by whatever happens to sit in your current directory",
        "is never satisfied by something that happens to sit in the directory",
        "nothing in the directory you run `aelix` in can stand in for what a benign entry names",
    ):
        assert old not in text, (doc, old)
    assert "never resolves an entry's" in text
    assert "`[tool.uv]`" in text
    assert "ADR-0200" in text
