"""#405 (ADR-0255 §12, §16) — a catalog or record spec is classified by its spelling.

``classify_target`` decided "is this target a path?" by asking the PROCESS cwd whether
the string existed there. ``discover install`` refused that case since #131, but
``aelix extension update [name]`` (every record is re-installed as a ``CatalogSpec``
since #392) and the Python API ``install_extension(CatalogSpec)`` did not: a cwd
directory or symlink named like the package turned a pypi record into the absolute path
of that cwd entry — measured on 61f03b67 and a18bcc1d with the real CLI (``update`` and
``update local-ext`` from a repository: rc 0, ``--upgrade <cwd>/w/local_ext-9.9-…whl``).

Decisions pinned here:

* a :class:`CatalogSpec` (what the catalog resolver returns, and what ``update`` wraps
  each record in) is classified from its OWN SPELLING only — a package requirement is a
  package, an absolute path a path, a URL (``file:///`` included) the URL it is, a git
  remote git — whatever the cwd holds;
* one spelled as a RELATIVE path (``./x``, a bare ``x.whl``) has no reading that does
  not consult the cwd, so ``install_extension`` and ``verify_and_pin`` refuse it before
  anything runs; a ``path`` record that is not absolute (only a hand edit writes one:
  aelix has recorded paths absolute since records began, 5817d1ac) is refused by
  ``update`` for that record, which goes on with the others;
* ``discover install`` installs the package even beside a cwd entry named like it (it
  refused, #131);
* what the user TYPES keeps its meaning: ``aelix extension install local-ext`` beside a
  ``./local-ext`` still installs that directory, and ``aelix extension update
  ./local-ext`` (a typed path matching no record) upgrades that path (review round 2),
  exactly as typed — never stripped — and resolved as a typed install resolves it
  (review round 3);
* whitespace (review round 3): a package, URL or git ``CatalogSpec`` is stripped once
  where it is built, so every check and the installer read one string; a path spelling
  keeps its exact string (a leading space makes it relative, refused);
* a requirement with a version specifier, a marker or a URL is a package before the
  bare-archive test (``x==1.0+v.whl``); a bare ``x.whl`` is a path (review round 3).

Every row injects a recording runner (no installer runs) and an isolated agent dir.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import extension_catalog as ec
from aelix_coding_agent.cli import extension_install as ei
from aelix_coding_agent.cli import extension_pins
from aelix_coding_agent.cli.extension_install import run_extension_command_async

from tests.env_sandbox import sandbox_home


class _Runner:
    """Records ``(argv, cwd)``; never runs an installer."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(argv), cwd))
        return subprocess.CompletedProcess(args=argv, returncode=0)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The cwd: a cloned repository holding a ``local-ext`` directory named like the
    package, a wheel under ``w/``, and a bare ``x-1.0-py3-none-any.whl``."""

    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", "")
    monkeypatch.delenv("UV_CONFIG_FILE", raising=False)
    monkeypatch.setattr(ei, "resolve_install_backend", lambda _runner: ei.PIP_BACKEND)
    root = tmp_path / "repo"
    (root / "w").mkdir(parents=True)
    (root / "w" / "local_ext-9.9-py3-none-any.whl").write_bytes(b"CWD-NAMED-ENTRY")
    (root / "local-ext").mkdir()
    (root / "local-ext" / "pyproject.toml").write_text(
        '[project]\nname = "local-ext"\nversion = "9.9"\n', encoding="utf-8"
    )
    (root / "x-1.0-py3-none-any.whl").write_bytes(b"a cwd archive")
    monkeypatch.chdir(root)
    return root


def _symlinked(repo: Path) -> None:
    """Swap the ``local-ext`` directory for a symlink to the cwd's wheel."""

    (repo / "local-ext" / "pyproject.toml").unlink()
    (repo / "local-ext").rmdir()
    try:
        os.symlink(repo / "w" / "local_ext-9.9-py3-none-any.whl", repo / "local-ext")
    except OSError as exc:  # pragma: no cover — Windows without symlink rights
        pytest.skip(f"cannot create a symlink here: {exc}")


def _never_the_cwd(repo: Path, argv: list[str]) -> None:
    joined = " ".join(argv)
    assert str(repo) not in joined and str(repo.resolve()) not in joined, argv


def _records(*records: dict[str, str]) -> SettingsManager:
    return SettingsManager.in_memory({"extensionSources": list(records)})


_PYPI_RECORD = {"spec": "local-ext", "kind": "pypi", "name": "local-ext"}


# === update ==================================================================


@pytest.mark.parametrize("shape", ["dir", "symlink"])
@pytest.mark.parametrize(
    "argv",
    [["update"], ["update", "local-ext"]],
    ids=["update", "update-name"],
)
async def test_update_reinstalls_the_recorded_package_not_a_cwd_entry_named_like_it(
    repo: Path, shape: str, argv: list[str]
) -> None:
    """The measured defect: rc 0 with ``--upgrade <cwd>/local-ext`` (a directory) or
    the wheel a ``local-ext`` symlink points at."""

    if shape == "symlink":
        _symlinked(repo)
    runner = _Runner()

    code = await run_extension_command_async(
        [*argv, "--yes", "--no-verify"], settings=_records(_PYPI_RECORD), runner=runner
    )

    assert code == 0
    assert len(runner.calls) == 1
    assert runner.calls[0][0][-2:] == ["--upgrade", "local-ext"]
    _never_the_cwd(repo, runner.calls[0][0])


async def test_update_of_an_unrecorded_name_is_the_package_too(repo: Path) -> None:
    runner = _Runner()

    code = await run_extension_command_async(
        ["update", "local-ext", "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
        runner=runner,
    )

    assert code == 0
    assert runner.calls[0][0][-1] == "local-ext"
    _never_the_cwd(repo, runner.calls[0][0])


@pytest.mark.parametrize("spec", ["local-ext", "./local-ext", "w/local_ext-9.9-py3-none-any.whl"])
async def test_a_relative_path_record_is_refused_and_the_next_record_still_runs(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], spec: str
) -> None:
    """aelix records a path absolute; a relative one (a hand edit)
    names no fixed file. It was resolved against the cwd — ``update`` installed the
    cwd's ``local-ext``. It is refused for that record; the others still update."""

    elsewhere = tmp_path / "elsewhere-ext"
    elsewhere.mkdir()
    runner = _Runner()

    code = await run_extension_command_async(
        ["update", "--yes", "--no-verify"],
        settings=_records(
            {"spec": spec, "kind": "path", "name": "rel-ext"},
            {"spec": str(elsewhere), "kind": "path", "name": "abs-ext"},
        ),
        runner=runner,
    )

    assert code == 2
    assert len(runner.calls) == 1  # only the absolute record ran
    assert runner.calls[0][0][-1] == elsewhere.resolve().as_uri()
    err = capsys.readouterr().err
    assert f"the recorded path '{spec}' is relative" in err
    assert "never reads an install record from the current directory" in err


async def test_an_absolute_path_record_still_updates(repo: Path, tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    runner = _Runner()

    code = await run_extension_command_async(
        ["update", "--yes", "--no-verify"],
        settings=_records({"spec": str(pack), "kind": "path", "name": "pack"}),
        runner=runner,
    )

    assert code == 0
    assert runner.calls[0][0][-1] == pack.resolve().as_uri()


# === the Python API ==========================================================


@pytest.mark.parametrize("shape", ["dir", "symlink"])
def test_install_extension_of_a_catalog_spec_is_the_package(repo: Path, shape: str) -> None:
    if shape == "symlink":
        _symlinked(repo)
    runner = _Runner()

    assert (
        ei.install_extension(ec.CatalogSpec("local-ext"), yes=True, no_verify=True, runner=runner)
        == 0
    )

    assert runner.calls[0][0][-1] == "local-ext"
    _never_the_cwd(repo, runner.calls[0][0])


def test_what_the_resolver_hands_out_is_the_package(repo: Path) -> None:
    """``resolve_entry_target`` and ``resolve_entry_source`` return a ``CatalogSpec``
    for a package entry; neither is turned into the cwd's ``local-ext``."""

    entry = ec.CatalogEntry(
        name="probe", source="local-ext", catalog_location="https://cat.example.invalid/c.json"
    )
    for target in (ec.resolve_entry_target(entry), ec.resolve_entry_source(entry)[0]):
        runner = _Runner()
        assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 0
        assert runner.calls[0][0][-1] == "local-ext"
        _never_the_cwd(repo, runner.calls[0][0])


@pytest.mark.parametrize(
    "spec",
    ["./local-ext", "local-ext/", "x-1.0-py3-none-any.whl", "../repo/local-ext", "."],
)
def test_a_catalog_spec_spelled_as_a_relative_path_is_refused(
    repo: Path, capsys: pytest.CaptureFixture[str], spec: str
) -> None:
    runner = _Runner()

    assert ei.install_extension(ec.CatalogSpec(spec), yes=True, no_verify=True, runner=runner) == 2

    assert runner.calls == []
    assert "is a relative path, and it came from a catalog or an install record" in (
        capsys.readouterr().err
    )


def test_verify_and_pin_refuses_a_relative_catalog_spec_before_reading_it(
    repo: Path, tmp_path: Path
) -> None:
    runner = _Runner()
    with pytest.raises(extension_pins.VerifyRefusal, match="is a relative path"):
        ei.verify_and_pin(
            ec.CatalogSpec("./local-ext"),
            "path",
            ["pip", "install", "./local-ext"],
            strict=False,
            repin=False,
            verify_pypi=False,
            index_url=None,
            extra_index_urls=None,
            runner=runner,
            agent_dir=str(tmp_path / "agent"),
        )
    assert runner.calls == []
    assert not (tmp_path / "agent" / "extension_pins.json").exists()


def test_a_home_relative_catalog_spec_names_no_cwd_and_is_not_refused(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0255 (C) takes a ``~`` path from any catalog: it does not depend on the cwd."""

    home = sandbox_home(monkeypatch, tmp_path / "home")
    (home / "pack").mkdir(parents=True)
    spec = ec.CatalogSpec("~/pack")
    runner = _Runner()

    assert ei.classify_target(spec) == "path"
    assert ei._origin_spec_problem(spec) is None
    assert ei.install_extension(spec, yes=True, no_verify=True, runner=runner) == 0
    assert runner.calls[0][0][-1] == str((home / "pack").resolve())


def test_the_pin_identity_of_a_catalog_package_is_its_name(repo: Path) -> None:
    spec = ec.CatalogSpec("local-ext")
    assert ei._pin_identity(spec, ei.classify_target(spec)) == "local-ext"


# === classification by spelling ==============================================


def test_a_catalog_spec_is_classified_by_its_spelling_alone(repo: Path, tmp_path: Path) -> None:
    """Every row's string also names something in the cwd where it can, so a
    classifier that still asked the cwd would answer ``path`` for it."""

    (repo / "local-ext[feature]").mkdir()
    (repo / "acme.git").mkdir()
    absolute = tmp_path / "pack"
    absolute.mkdir()
    want = {
        "local-ext": "pypi",
        "local-ext[feature]": "pypi",
        "local-ext==1.0": "pypi",
        "acme.git": "git",
        "git+https://git.example.invalid/o/r.git": "git",
        "a_b~c@git.example.invalid:team/ext": "git",
        "git@git.example.invalid:team/ext.git": "git",
        "local-ext @ git+https://git.example.invalid/o/r.git": "git",
        "local-ext @ https://files.example.invalid/local_ext-1.0-py3-none-any.whl": "pypi",
        "https://files.example.invalid/local_ext-1.0-py3-none-any.whl": "pypi",
        absolute.as_uri(): "pypi",  # a file URL goes on as the URL it is
        str(absolute): "path",
        f"{absolute}[feature]": "path",
        "./local-ext": "path",
        "x-1.0-py3-none-any.whl": "path",
    }
    got = {spec: ei.classify_target(ec.CatalogSpec(spec)) for spec in want}
    assert got == want


def test_a_typed_target_keeps_the_cwd_reading(repo: Path) -> None:
    assert ei.classify_target("local-ext") == "path"
    assert ei.classify_target(ec.CatalogSpec("local-ext")) == "pypi"


async def test_a_typed_install_still_installs_the_cwd_entry(repo: Path) -> None:
    """ADR-0255 §2 (8): what the user types keeps its meaning — ``local-ext`` beside a
    ``./local-ext`` is that directory, installed from where it was typed."""

    runner = _Runner()

    code = await run_extension_command_async(
        ["install", "local-ext", "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
        runner=runner,
    )

    assert code == 0
    assert runner.calls[0][0][-1] == str((repo / "local-ext").resolve())
    assert runner.calls[0][1] is None


@pytest.mark.skipif(sys.platform != "win32", reason="a drive-relative path exists only on Windows")
def test_on_windows_a_drive_relative_catalog_spec_is_refused(repo: Path) -> None:
    """``C:x`` and a rooted ``\\x`` depend on the current drive and directory."""

    for spec in ("C:local-ext", "\\local-ext"):
        assert ei.classify_target(ec.CatalogSpec(spec)) == "path"
        assert ei._origin_spec_problem(ec.CatalogSpec(spec)) is not None


# === review round 2 ==========================================================
#
# 1 (verify): a caller of the Python API supplies ``kind`` — a CatalogSpec's kind is
#   its spelling's, and one that disagrees is refused before a file or the pin store
#   is read. 2 (verify): a path TYPED as ``update``'s filter is the user's own path.
#   3-6 (Codex): whitespace, the printed ``source remove`` command, a PEP 508 marker
#   holding ``/``, and the rows two surviving mutants showed missing.


def _verify(target: ei.InstallTarget, kind: str, agent: Path) -> ei._VerifyResult:
    return ei.verify_and_pin(
        target,
        kind,  # type: ignore[arg-type]
        ["pip", "install", str(target)],
        strict=False,
        repin=False,
        verify_pypi=False,
        index_url=None,
        extra_index_urls=None,
        runner=_Runner(),
        agent_dir=str(agent),
    )


def _no_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the row if the gate reads the pin store or stages a file."""

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("read before the refusal")

    monkeypatch.setattr(extension_pins, "load_pins", boom)
    monkeypatch.setattr(ei.shutil, "copy2", boom)
    monkeypatch.setattr(ei.tempfile, "mkdtemp", boom)


def _disagreeing(tmp_path: Path) -> list[tuple[ei.InstallTarget, str]]:
    absolute = tmp_path / "abs-ext"
    absolute.mkdir(exist_ok=True)
    return [
        # what a relative ``path`` record {spec: 'local-ext', kind: 'path'} holds
        (ec.CatalogSpec("local-ext"), "path"),
        (ec.CatalogSpec("local-ext"), "git"),
        (ec.CatalogSpec("git+https://git.example.invalid/o/r.git"), "path"),
        (ec.CatalogSpec(str(absolute)), "pypi"),
        (ec.ResolvedPath(str(absolute)), "pypi"),
    ]


@pytest.mark.parametrize("row", range(5))
def test_verify_and_pin_refuses_a_kind_the_spelling_does_not_give(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: int
) -> None:
    """Measured on 709982a3: ``verify_and_pin(CatalogSpec('local-ext'), 'path', …)``
    hashed and staged the cwd's ``local-ext`` (a symlink to its own wheel), returned
    an argv installing that copy and a TOFI pin naming it."""

    _symlinked(repo)
    target, kind = _disagreeing(tmp_path)[row]
    _no_reads(monkeypatch)

    with pytest.raises(extension_pins.VerifyRefusal, match="spelling"):
        _verify(target, kind, tmp_path / "agent")

    assert not (tmp_path / "agent" / "extension_pins.json").exists()


def test_verify_and_pin_takes_the_kind_the_spelling_gives(repo: Path, tmp_path: Path) -> None:
    """The guard refuses a disagreement only: the kind ``classify_target`` gives passes."""

    _symlinked(repo)
    spec = ec.CatalogSpec("local-ext")
    result = _verify(spec, ei.classify_target(spec), tmp_path / "agent")
    assert result.pip_args[-1] == "local-ext"
    assert result.pin is None


@pytest.mark.parametrize("row", range(5))
def test_build_pip_args_refuses_a_kind_the_spelling_does_not_give(
    repo: Path, tmp_path: Path, row: int
) -> None:
    """``build_pip_args(CatalogSpec('local-ext'), 'path')`` returned
    ``pip install <cwd>/w/local_ext-9.9-…whl`` on 709982a3."""

    _symlinked(repo)
    target, kind = _disagreeing(tmp_path)[row]

    with pytest.raises(ValueError, match="spelling"):
        ei.build_pip_args(target, kind)  # type: ignore[arg-type]


def test_a_relative_resolved_path_is_refused_not_resolved_or_raised(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``ResolvedPath`` a caller builds from a relative string raised ``ValueError``
    out of ``install_extension`` (``as_uri``) on 709982a3; its verify gate would hash
    the cwd's file. Refused, ``2``, nothing run."""

    runner = _Runner()
    target = ec.ResolvedPath("local-ext")

    assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 2
    assert runner.calls == []
    assert "is a relative path" in capsys.readouterr().err
    with pytest.raises(ValueError, match="is a relative path"):
        ei.build_pip_args(target, "path")


@pytest.mark.parametrize("shape", ["dir", "symlink"])
@pytest.mark.parametrize("typed", ["./local-ext", "local-ext/"])
async def test_update_of_a_typed_path_is_that_path_as_before_405(
    repo: Path, capsys: pytest.CaptureFixture[str], shape: str, typed: str
) -> None:
    """A filter TYPED as a path is the user's path, resolved as typed — what 8f7d98aa
    did (rc 0, ``--upgrade <cwd>/local-ext``, the installer in aelix's directory).
    709982a3 refused it with "it came from a catalog or an install record", which the
    user had not done."""

    if shape == "symlink":
        _symlinked(repo)
    runner = _Runner()

    code = await run_extension_command_async(
        ["update", typed, "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
        runner=runner,
    )

    err = capsys.readouterr().err
    assert code == 0, err
    assert "came from a catalog" not in err
    assert runner.calls[0][0][-2:] == ["--upgrade", str((repo / "local-ext").resolve())]
    assert runner.calls[0][1] == str(ei.catalog_installer_cwd())


@pytest.mark.parametrize(
    ("spec", "want"),
    [
        (" local-ext", "local-ext"),
        ("local-ext \n", "local-ext"),
        ("\tgit+https://git.example.invalid/o/r.git", "git+https://git.example.invalid/o/r.git"),
        (" https://files.example.invalid/x-1.0-py3-none-any.whl ", None),
    ],
)
def test_a_non_path_catalog_spec_is_stripped_once_where_it_is_wrapped(
    repo: Path, tmp_path: Path, spec: str, want: str | None
) -> None:
    """Review round 3 (verify r2): round 2 refused any surrounding whitespace, and so
    refused the git record a typed ``install 'git+file:///repo '`` writes, which
    8f7d98aa upgraded. A package, URL or git spelling is stripped where the
    ``CatalogSpec`` is built, and every check and the installer read that one string."""

    want = want or spec.strip()
    target = ec.CatalogSpec(spec)
    assert target == want and isinstance(target, ec.CatalogSpec)
    runner = _Runner()

    assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 0

    assert runner.calls[0][0][-1].removeprefix("git+") == want.removeprefix("git+")
    assert ei._origin_spec_problem(target, ei.classify_target(target)) is None
    _verify(target, ei.classify_target(target), tmp_path / "agent")


@pytest.mark.parametrize(
    "spec",
    [
        "git+https://git.example.invalid/o/r.git ",
        " ssh://git.example.invalid/o/r.git",
        " local-ext @ git+https://git.example.invalid/o/r.git ",
    ],
)
async def test_a_git_record_with_surrounding_whitespace_updates(
    repo: Path, capsys: pytest.CaptureFixture[str], spec: str
) -> None:
    """The regression round 2 introduced: ``update`` exited 2 ("begins or ends with
    whitespace … Fix the catalog or the record", no command to run) for a git record
    aelix's own typed install wrote. Upgraded as the stripped spec, as on 8f7d98aa."""

    runner = _Runner()

    code = await run_extension_command_async(
        ["update", "--yes", "--no-verify"],
        settings=_records({"spec": spec, "kind": "git"}),
        runner=runner,
    )

    assert code == 0, capsys.readouterr().err
    assert runner.calls[0][0][-1] == ei._normalize_git_spec(spec.strip())


@pytest.mark.parametrize("spec", [" @ABS@", "@ABS@ ", " ./local-ext", "x-1.0-py3-none-any.whl "])
def test_a_path_spelling_keeps_its_exact_string(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    spec: str,
) -> None:
    """A path is judged absolute or relative on its exact string, the string the
    installer resolves: ``' /abs/x.whl'`` is relative as written and refused (round 2,
    Codex: a stripped check passed it and ``<cwd>/' /abs/x.whl'``, a decoy, was
    installed); ``'/abs/x.whl '`` is absolute and is that exact name — never the
    stripped ``/abs/x.whl`` beside it."""

    trusted = tmp_path / "trusted" / "probe405-1.0-py3-none-any.whl"
    trusted.parent.mkdir()
    trusted.write_bytes(b"TRUSTED")
    value = spec.replace("@ABS@", str(trusted))
    target = ec.CatalogSpec(value)
    assert target == value
    assert ei.classify_target(target) == "path"
    runner = _Runner()
    if value != value.rstrip() and Path(value.rstrip()).is_absolute():
        if sys.platform == "win32":
            pytest.skip("Windows drops a trailing space from a file name")
        Path(value).write_bytes(b"TRAILING-SPACE NAME")
        assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 0
        assert runner.calls[0][0][-1] == value
        return
    if sys.platform != "win32" and value.startswith(" /"):
        decoy = repo / value
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_bytes(b"CWD-DECOY")

    assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 2

    assert runner.calls == []
    err = capsys.readouterr().err
    assert "is a relative path" in err
    assert ("it begins with whitespace" in err) == (value != value.lstrip())
    _no_reads(monkeypatch)
    with pytest.raises(extension_pins.VerifyRefusal, match="relative path"):
        _verify(target, "path", tmp_path / "agent")


def test_the_resolver_hands_out_a_source_without_its_surrounding_whitespace(
    repo: Path,
) -> None:
    """So no catalog entry is refused for the whitespace rule above (round 2)."""

    for source, want in (
        ("  local-ext  ", "local-ext"),
        (
            " https://files.example.invalid/local_ext-1.0-py3-none-any.whl ",
            "https://files.example.invalid/local_ext-1.0-py3-none-any.whl",
        ),
        (
            " local-ext @ git+https://git.example.invalid/o/r.git ",
            "local-ext @ git+https://git.example.invalid/o/r.git",
        ),
    ):
        entry = ec.CatalogEntry(
            name="probe", source=source, catalog_location="https://cat.example.invalid/c.json"
        )
        target = ec.resolve_entry_target(entry)
        assert isinstance(target, ec.CatalogSpec)
        assert target == want
        runner = _Runner()
        assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 0
        assert runner.calls[0][0][-1] == want


@pytest.mark.skipif(sys.platform == "win32", reason="the decoy name holds ':' on Windows")
async def test_a_path_record_with_a_leading_space_is_refused_not_read_from_the_cwd(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """709982a3 stripped the record to test it and resolved it unstripped: ``update``
    installed ``<cwd>/' /abs/x.whl'``, a decoy."""

    trusted = tmp_path / "trusted" / "probe405-1.0-py3-none-any.whl"
    trusted.parent.mkdir()
    trusted.write_bytes(b"TRUSTED")
    spec = f" {trusted}"
    decoy = repo / spec
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(b"CWD-DECOY")
    runner = _Runner()

    code = await run_extension_command_async(
        ["update", "--yes", "--no-verify"],
        settings=_records({"spec": spec, "kind": "path", "name": "probe405"}),
        runner=runner,
    )

    assert code == 2
    assert runner.calls == []
    assert "it begins with whitespace, which makes it relative" in capsys.readouterr().err


@pytest.mark.parametrize("spec", ["-local-ext", "local-ext", "it's ext", "--x"])
async def test_the_printed_source_remove_command_drops_the_record(
    repo: Path, capsys: pytest.CaptureFixture[str], spec: str
) -> None:
    """Codex, 709982a3: the advice ``aelix extension source remove '-local-ext'``
    failed as printed — ``source remove`` dropped an argument starting with ``-``.
    The printed command is run here exactly as a POSIX shell splits it."""

    import shlex

    settings = _records({"spec": spec, "kind": "path"})
    await run_extension_command_async(
        ["update", "--yes", "--no-verify"], settings=settings, runner=_Runner()
    )
    err = capsys.readouterr().err
    assert f"could not update {spec}:" in err  # labelled as written, not as a cwd path
    printed = err.split("Drop the record (", 1)[1].split(") and install it again", 1)[0]
    argv = shlex.split(printed)
    assert argv[:3] == ["aelix", "extension", "source"]

    code = await run_extension_command_async(argv[2:], settings=settings, runner=_Runner())

    assert code == 0, capsys.readouterr().err
    assert settings.get_extension_sources() == []


async def test_source_remove_still_ignores_flags_before_the_double_dash(repo: Path) -> None:
    settings = _records({"spec": "-local-ext", "kind": "path"})
    assert (
        await run_extension_command_async(["source", "remove", "-local-ext"], settings=settings)
        == 2
    )
    assert len(settings.get_extension_sources()) == 1


_MARKER_REQ = (
    'probe405; platform_version == "Darwin Kernel Version 25.6.0: '
    'root:xnu-12377.161.14~5/RELEASE_ARM64_T6050"'
)


def test_a_requirement_whose_marker_holds_a_slash_is_a_package(repo: Path) -> None:
    """Codex, 709982a3: the separator test refused this valid PEP 508 requirement as
    a relative path; 8f7d98aa installed it from the index."""

    runner = _Runner()
    spec = ec.CatalogSpec(_MARKER_REQ)

    assert ei.classify_target(spec) == "pypi"
    assert ei.install_extension(spec, yes=True, no_verify=True, runner=runner) == 0
    assert runner.calls[0][0][-1] == _MARKER_REQ


def test_round_2_spellings(repo: Path, tmp_path: Path) -> None:
    """Rows two mutants survived: a backslash is a separator on every platform (a
    mutant dropping it read ``sub\\ext`` as a package and installed it), and an
    absolute path ending in ``.git`` is a path (a mutant asking for the git shape
    first made it ``git+/abs/proj.git``)."""

    (repo / "sub").mkdir()
    (repo / "sub" / "ext").mkdir()
    proj = tmp_path / "proj.git"
    proj.mkdir()
    want = {
        "sub\\ext": "path",
        "sub\\ext[feature]": "path",
        str(proj): "path",
        _MARKER_REQ: "pypi",
    }
    assert {s: ei.classify_target(ec.CatalogSpec(s)) for s in want} == want
    runner = _Runner()
    assert (
        ei.install_extension(ec.CatalogSpec("sub\\ext"), yes=True, no_verify=True, runner=runner)
        == 2
    )
    assert runner.calls == []


# === review round 3 (verify r2 and Codex r2 on e55a9fc8) =====================
#   1 whitespace: replaced by the stripping above. 2: a typed update filter exactly as
#   typed, through the typed install's own resolution (mutants S14, S28). 3: a
#   requirement with a version specifier / marker / URL is a package before the
#   bare-archive test. 4: the origin check runs for every CatalogSpec. 5: the printed
#   ``source remove`` advice says it matches by name too.


async def _typed_update(filter_: str) -> tuple[int, _Runner]:
    runner = _Runner()
    code = await run_extension_command_async(
        ["update", filter_, "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
        runner=runner,
    )
    return code, runner


@pytest.mark.skipif(sys.platform == "win32", reason="Windows drops a trailing '.' or space")
async def test_a_typed_update_filter_is_not_stripped(repo: Path) -> None:
    """Codex r2: e55a9fc8 stripped the filter, so with a directory named ``' .'`` here
    ``update ' ./local-ext'`` upgraded ``./local-ext`` instead (8f7d98aa: ``' ./local-ext'``
    as typed). Kills mutant S14."""

    (repo / " .").mkdir()
    (repo / " ." / "local-ext").mkdir()

    code, runner = await _typed_update(" ./local-ext")

    assert code == 0
    assert runner.calls[0][0][-1] == str((repo / " ." / "local-ext").resolve())


async def test_a_typed_update_filter_is_resolved_as_a_typed_install_resolves_it(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``~`` is expanded and ``[extras]`` are split off an existing path before it is
    resolved — what ``aelix extension install <path>`` does (``_install_spec``). Mutant
    S28 (``Path(filter).resolve()``) gave ``<cwd>/~/pack`` and kept a symlink's name
    with the extras glued on."""

    home = sandbox_home(monkeypatch, tmp_path / "home")
    (home / "pack").mkdir(parents=True)
    code, runner = await _typed_update("~/pack")
    assert code == 0
    assert runner.calls[0][0][-1] == str((home / "pack").resolve())

    _symlinked(repo)
    code, runner = await _typed_update("./local-ext[feature]")
    assert code == 0
    wheel = (repo / "w" / "local_ext-9.9-py3-none-any.whl").resolve()
    assert runner.calls[0][0][-1] == f"{wheel}[feature]"
    assert runner.calls[0][0][-1] == ei._install_spec("./local-ext[feature]", "path")


@pytest.mark.parametrize(
    ("spec", "kind"),
    [
        ("path-probe==1.0+vendor.whl", "pypi"),
        ("x==1.0+v.whl", "pypi"),
        ("x==1.0+v.tar.gz", "pypi"),
        ("x[e]", "pypi"),
        ('x; python_version>"3"', "pypi"),
        ('x.whl; python_version>"3"', "pypi"),
        ("x.whl", "path"),
        ("x.tar.gz", "path"),
        ("pkg-1.0-py3-none-any.whl", "path"),
        ("x.whl[feature]", "path"),
    ],
)
def test_a_requirement_with_a_version_or_marker_is_a_package_a_bare_archive_a_path(
    repo: Path, spec: str, kind: str
) -> None:
    """Codex r2: ``CatalogSpec('path-probe==1.0+vendor.whl')`` — a valid requirement
    whose local version ends in ``.whl`` — was taken for a bare archive name, a relative
    path, and refused ("is a relative path", false); 8f7d98aa installed it. A string
    that parses as a requirement with a version specifier, a marker or a URL is a
    package or URL; a bare token ending in an archive suffix (``[extras]`` allowed:
    pip and uv open ``x.whl[feature]`` as a file) stays a path spelling, refused as
    relative."""

    target = ec.CatalogSpec(spec)
    runner = _Runner()

    assert ec.spelled_as_path(spec) is (kind == "path")
    assert ei.classify_target(target) == kind
    code = ei.install_extension(target, yes=True, no_verify=True, runner=runner)
    if kind == "pypi":
        assert code == 0
        assert runner.calls[0][0][-1] == spec
    else:
        assert code == 2
        assert runner.calls == []


@pytest.mark.parametrize("spec", ["./local-ext", "local-ext/", "x-1.0-py3-none-any.whl", " @ABS@"])
def test_the_origin_check_runs_when_the_callers_kind_agrees(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str
) -> None:
    """Codex r2 cat 4: a ``build_pip_args`` that checked a ``CatalogSpec`` only on a
    kind mismatch passed every row and returned ``pip install <cwd>/local-ext`` for
    ``build_pip_args(CatalogSpec('./local-ext'), 'path')`` — the kinds agree there.
    Both public ``(target, kind)`` functions refuse it."""

    value = spec.replace("@ABS@", str(tmp_path / "abs.whl"))
    target = ec.CatalogSpec(value)
    assert ei.classify_target(target) == "path"

    with pytest.raises(ValueError, match="is a relative path"):
        ei.build_pip_args(target, "path")
    _no_reads(monkeypatch)
    with pytest.raises(extension_pins.VerifyRefusal, match="is a relative path"):
        _verify(target, "path", tmp_path / "agent")


async def test_the_source_remove_advice_says_what_it_drops(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """verify r2/r3: ``source remove`` matches a source by spec, name OR path (before #405
    as now): run here, the printed command for a relative path record ``local-ext`` also
    drops the package record ``local-ext`` and an ABSOLUTE path record ``<cwd>/local-ext``
    (verify r3 measured 'Removed 3 source(s)' while the advice named spec and name only).
    It has no way to target one record exactly, so the advice says all of it — and the
    row runs the printed command here and from another directory to show it is true."""

    import shlex

    records = (
        {"spec": "local-ext", "kind": "path", "name": "rel-ext"},
        _PYPI_RECORD,
        {"spec": str(repo / "local-ext"), "kind": "path", "name": "my-local"},
        {"spec": "git+https://git.example.invalid/o/other.git", "kind": "git", "name": "other"},
    )
    settings = _records(*records)
    await run_extension_command_async(
        ["update", "rel-ext", "--yes", "--no-verify"], settings=settings, runner=_Runner()
    )
    err = " ".join(capsys.readouterr().err.split())
    here = ei.safe_for_terminal(ei._source_identity("local-ext", "path"))
    want = (
        "That source remove matches a source by its spec, its name OR its path, so it also "
        "drops any other source or record whose spec or name is 'local-ext' — a package "
        "record of that name included; install that one again afterwards — and any path "
        "record whose path resolves to 'local-ext' read from the directory you run it in "
        f"(run here: '{here}'); to keep such a path record, run it from another directory"
    )
    assert " ".join(want.split()) in err
    printed = err.split("Drop the record (", 1)[1].split(") and install it again", 1)[0]
    argv = shlex.split(printed)[2:]

    code = await run_extension_command_async(argv, settings=settings, runner=_Runner())
    assert code == 0
    assert [s.name for s in settings.get_extension_sources()] == ["other"]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    settings = _records(*records)
    code = await run_extension_command_async(argv, settings=settings, runner=_Runner())
    assert code == 0
    assert [s.name for s in settings.get_extension_sources()] == ["my-local", "other"]


# === review round 4 (verify r3 and Codex r3 on d49f51f0) =====================
#   1 a requirement whose local version ends in .git is a package (requirement first,
#   before the .git suffix). 2 a '~' or otherwise non-absolute ResolvedPath is refused,
#   never raised. 3 verify_and_pin checks a relative ResolvedPath when the kind agrees.
#   4 the source remove advice above says it drops by path too.

_GIT_LOCAL = "probe405==1.0+vendor.git"


@pytest.mark.parametrize(
    "spec", [_GIT_LOCAL, "probe405[e]==1.0+a.git", "probe405===1.0.git", 'probe405.git; python_version>"3"']
)
def test_a_requirement_whose_version_ends_in_git_is_the_package(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str
) -> None:
    """Codex r3: ``CatalogSpec('probe405==1.0+vendor.git')`` parses as a requirement with
    a version specifier, but the ``.git``-suffix test called it git, and the origin check
    then refused ``build_pip_args`` / ``verify_and_pin`` with kind ``pypi`` — 'spelled as a
    git source', false; 8f7d98aa installed it (Codex r3, ``INSTALLED VERSION
    1.0+vendor.git``). A requirement with a version specifier or a marker is a package
    before any git shape."""

    if sys.platform != "win32":  # a cwd decoy, where the name is legal
        (repo / spec).mkdir()
    target = ec.CatalogSpec(spec)

    assert ec.is_qualified_package_requirement(spec)
    assert ei.classify_target(target) == "pypi"
    assert ei.build_pip_args(target, "pypi")[-1] == spec
    assert _verify(target, "pypi", tmp_path / "agent").pip_args[-1] == spec
    runner = _Runner()
    assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 0
    assert runner.calls[0][0][-1] == spec
    with pytest.raises(ValueError, match="is spelled as a package requirement or URL, but it"):
        ei.build_pip_args(target, "git")


def test_a_bare_name_ending_in_git_and_a_typed_one_keep_the_git_reading(repo: Path) -> None:
    """Controls: only a version specifier or a marker makes it a package — a bare name
    ``acme.git`` (no specifier) stays git, as before; a TYPED string keeps today's
    reading whatever it holds."""

    assert not ec.is_qualified_package_requirement("acme.git")
    assert not ec.is_qualified_package_requirement("acme.git[x]")
    assert ei.classify_target(ec.CatalogSpec("acme.git")) == "git"
    assert ei.classify_target(_GIT_LOCAL) == "git"


def test_the_resolver_hands_out_a_versioned_name_ending_in_git_as_the_package(
    repo: Path,
) -> None:
    """The resolver refused any requirement ending in ``.git`` because the installer
    took it for a git URL; that is now true of a bare name only (``acme.git`` is still
    refused, test_catalog_relative_source_131)."""

    entry = ec.CatalogEntry(
        name="probe405", source=_GIT_LOCAL, catalog_location="https://h.example.invalid/c.json"
    )
    target = ec.resolve_entry_target(entry)
    assert isinstance(target, ec.CatalogSpec)
    assert target == _GIT_LOCAL
    assert ei.classify_target(target) == "pypi"
    bare = ec.CatalogEntry(
        name="acme", source="acme.git", catalog_location="https://h.example.invalid/c.json"
    )
    with pytest.raises(ec.CatalogError, match="for a git URL"):
        ec.resolve_entry_target(bare)


@pytest.mark.parametrize("path", ["~/pack", "~", " @ABS@", "pack"])
def test_a_resolved_path_that_is_not_absolute_as_written_is_refused_never_raised(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path: str,
) -> None:
    """Codex r3: ``ResolvedPath('~/pack')`` passed the origin check (``~`` expanded) and
    then raised ``ValueError: relative path can't be expressed as a file URI`` out of
    ``build_pip_args`` and ``install_extension``; ``verify_and_pin`` returned ``~/pack``
    unpinned. Its hand-off takes an absolute path as written: ``install_extension`` 2,
    ``verify_and_pin`` VerifyRefusal, ``build_pip_args`` a ValueError that says why."""

    home = sandbox_home(monkeypatch, tmp_path / "home")
    (home / "pack").mkdir(parents=True)
    (repo / "pack").mkdir()
    target = ec.ResolvedPath(path.replace("@ABS@", str(tmp_path / "abs")))
    runner = _Runner()

    assert ei.install_extension(target, yes=True, no_verify=True, runner=runner) == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "is a relative path" in err
    if path.startswith("~"):
        assert "a '~' is not expanded in a path handed over as resolved" in err
    with pytest.raises(ValueError, match="is a relative path"):
        ei.build_pip_args(target, "path")
    _no_reads(monkeypatch)
    with pytest.raises(extension_pins.VerifyRefusal, match="is a relative path"):
        _verify(target, "path", tmp_path / "agent")
    assert not (tmp_path / "agent" / "extension_pins.json").exists()


def test_verify_and_pin_refuses_a_relative_resolved_path_when_the_kind_agrees(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r3 cat 4: a ``verify_and_pin`` that checked the origin only for a
    ``CatalogSpec`` or a kind mismatch passed every row, then hashed, staged and pinned
    the cwd's ``probe-1.0-py3-none-any.whl`` for ``ResolvedPath(<that name>)`` with kind
    ``path`` — the kinds agree there."""

    (repo / "probe-1.0-py3-none-any.whl").write_bytes(b"a cwd wheel")
    target = ec.ResolvedPath("probe-1.0-py3-none-any.whl")
    _no_reads(monkeypatch)

    with pytest.raises(extension_pins.VerifyRefusal, match="is a relative path"):
        _verify(target, "path", tmp_path / "agent")

    assert not (tmp_path / "agent" / "extension_pins.json").exists()


def test_a_home_path_record_whose_resolve_fails_is_handed_over_expanded(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweep for item 2: a ``~/loop`` path record (a symlink loop — ``resolve()`` raises)
    became ``ResolvedPath('~/loop')`` and its hand-off failed with ``ValueError``
    (update reported it per record, exit 2; measured on d49f51f0). It goes over
    expanded, as every other branch."""

    home = sandbox_home(monkeypatch, tmp_path / "home")
    (home / "loop").mkdir(parents=True)

    def broken(self: Path, strict: bool = False) -> Path:
        raise RuntimeError("Symlink loop")

    monkeypatch.setattr(Path, "resolve", broken)
    placed = ei._recorded_path_target("~/loop")

    assert placed == ec.ResolvedPath(str(home / "loop"))
