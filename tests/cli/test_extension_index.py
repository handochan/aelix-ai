"""Issue #68 — ``aelix extension index <dir>`` generates a catalog document.

Hermetic: wheels and sdists are crafted here rather than built, so the suite
needs no pip and no network. The wheel shape is the real one (a zip carrying
``<dist>-<ver>.dist-info/METADATA``), and every generated document is fed back
through :func:`extension_catalog.parse_catalog` — the same parser
``discover`` uses — so these tests pin the generator against the actual
contract rather than against a second copy of the schema.

The generator was also exercised against a genuinely built wheel (the
``examples/starter`` pack) during development; its ``sha256`` matched
``sha256sum`` and the document round-tripped through
``source add --catalog file://…`` + ``discover --offline --refresh``.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from aelix_coding_agent.cli import extension_catalog as ec
from aelix_coding_agent.cli import extension_install as ei


@pytest.fixture(autouse=True)
def _isolate_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "agent" / "settings.json"))


def _wheel(
    directory: Path,
    name: str,
    version: str,
    *,
    summary: str | None = "A test pack",
    homepage: str | None = None,
    project_url: str | None = None,
) -> Path:
    """Write a minimally valid wheel carrying real core metadata."""

    dist = name.replace("-", "_")
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if summary:
        lines.append(f"Summary: {summary}")
    if homepage:
        lines.append(f"Home-page: {homepage}")
    if project_url:
        lines.append(f"Project-URL: {project_url}")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{dist}-{version}.dist-info/METADATA", "\n".join(lines) + "\n")
        zf.writestr(f"{dist}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        zf.writestr(f"{dist}/__init__.py", "")
    return path


def _sdist(directory: Path, name: str, version: str, summary: str = "An sdist") -> Path:
    path = directory / f"{name}-{version}.tar.gz"
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nSummary: {summary}\n"
    with tarfile.open(path, "w:gz") as tf:
        raw = meta.encode()
        info = tarfile.TarInfo(f"{name}-{version}/PKG-INFO")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entries(document: dict) -> list[dict]:
    return document["extensions"]  # type: ignore[return-value]


# === scan + build (pure) =============================================


def test_reads_metadata_and_hashes_the_artifact(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "notes-ext", "1.2.3", summary="Take notes")

    found = ec.scan_artifacts(tmp_path)

    assert len(found) == 1
    art = found[0]
    assert art.name == "notes-ext"
    assert art.version == "1.2.3"
    assert art.summary == "Take notes"
    assert art.sha256 == _sha256(wheel), "sha256 must hash the artifact bytes"


def test_generated_document_parses_as_a_catalog(tmp_path: Path) -> None:
    """The real contract: the emitted document must satisfy parse_catalog."""
    wheel = _wheel(tmp_path, "notes-ext", "1.2.3", summary="Take notes")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path), name="Acme")
    catalog = ec.parse_catalog(json.dumps(document), location="file://acme")

    assert catalog.name == "Acme"
    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    assert entry.name == "notes-ext"
    assert entry.version == "1.2.3"
    assert entry.description == "Take notes"
    assert entry.sha256 == _sha256(wheel)
    assert Path(entry.source) == wheel.resolve()


def test_entry_resolves_unambiguously_by_name(tmp_path: Path) -> None:
    """`discover install <name>` has to find exactly one candidate."""
    _wheel(tmp_path, "notes-ext", "1.0.0")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))
    catalog = ec.parse_catalog(json.dumps(document), location="file://acme")
    resolved, candidates = ec.resolve_entry([catalog], "notes-ext")

    assert resolved is not None
    assert len(candidates) == 1


def test_source_is_absolute_and_routes_to_a_path(tmp_path: Path) -> None:
    """An absolute source is what makes the entry installable from any cwd.

    ``classify_target`` resolves a path against the PROCESS cwd, so a relative
    source read from elsewhere falls through to a pypi lookup of the same name.
    Absolute is therefore the default, and this pins it.
    """
    _wheel(tmp_path, "notes-ext", "1.0.0")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))
    source = _entries(document)[0]["source"]

    assert Path(source).is_absolute()
    assert ei.classify_target(source) == "path"


def test_relative_opt_in_emits_bare_filenames(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "notes-ext", "1.0.0")

    document = ec.build_index_catalog(
        ec.scan_artifacts(tmp_path), relative_to=tmp_path.resolve()
    )

    assert _entries(document)[0]["source"] == wheel.name


def test_relative_tolerates_an_unresolved_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scanning an unresolved root must still measure against it.

    The builder is exported, so the CLI is not the only caller that can hold a
    root in one form and artifacts in another. Comparing the two forms raises,
    so the builder resolves both sides itself rather than trusting the caller.
    """
    wheel = _wheel(tmp_path, "notes-ext", "1.0.0")
    monkeypatch.chdir(tmp_path)

    document = ec.build_index_catalog(
        ec.scan_artifacts(Path(".")), relative_to=Path(".")
    )

    assert _entries(document)[0]["source"] == wheel.name


def test_several_versions_collapse_to_one_entry_newest_first(tmp_path: Path) -> None:
    """Two versions must not become two same-named entries.

    ``resolve_entry`` refuses an ambiguous name rather than picking one, so a
    per-file entry would make a multi-version directory uninstallable by name.
    """
    _wheel(tmp_path, "notes-ext", "0.9.0")
    newest = _wheel(tmp_path, "notes-ext", "0.10.0")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))
    entries = _entries(document)

    assert len(entries) == 1
    # 0.10 is newer than 0.9 — numeric ordering, not string ordering.
    assert entries[0]["version"] == "0.10.0"
    assert entries[0]["versions"] == ["0.10.0", "0.9.0"]
    assert Path(entries[0]["source"]) == newest.resolve()
    assert entries[0]["sha256"] == _sha256(newest)


def test_sdists_are_indexed_too(tmp_path: Path) -> None:
    sdist = _sdist(tmp_path, "legacy-ext", "2.0.0")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))
    entry = _entries(document)[0]

    assert entry["name"] == "legacy-ext"
    assert entry["version"] == "2.0.0"
    assert entry["sha256"] == _sha256(sdist)


def test_homepage_falls_back_to_project_url(tmp_path: Path) -> None:
    _wheel(tmp_path, "notes-ext", "1.0.0", project_url="Homepage, https://acme.test/n")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))

    assert _entries(document)[0]["homepage"] == "https://acme.test/n"


def test_unreadable_archives_are_skipped_not_fatal(tmp_path: Path) -> None:
    """A stray archive in the wheel directory must not abort the scan."""
    _wheel(tmp_path, "good-ext", "1.0.0")
    (tmp_path / "not-a-wheel.whl").write_bytes(b"this is not a zip")
    (tmp_path / "empty.tar.gz").write_bytes(b"")
    with zipfile.ZipFile(tmp_path / "nometa-1.0-py3-none-any.whl", "w") as zf:
        zf.writestr("nometa/__init__.py", "")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))

    assert [e["name"] for e in _entries(document)] == ["good-ext"]


def test_scan_is_not_recursive(tmp_path: Path) -> None:
    """A neighbouring build tree must not be swept in."""
    _wheel(tmp_path, "top-ext", "1.0.0")
    nested = tmp_path / "subdir"
    nested.mkdir()
    _wheel(nested, "nested-ext", "1.0.0")

    document = ec.build_index_catalog(ec.scan_artifacts(tmp_path))

    assert [e["name"] for e in _entries(document)] == ["top-ext"]


def test_output_is_deterministic(tmp_path: Path) -> None:
    _wheel(tmp_path, "b-ext", "1.0.0")
    _wheel(tmp_path, "a-ext", "1.0.0")

    first = ec.build_index_catalog(ec.scan_artifacts(tmp_path), updated="fixed")
    second = ec.build_index_catalog(ec.scan_artifacts(tmp_path), updated="fixed")

    assert first == second
    assert [e["name"] for e in _entries(first)] == ["a-ext", "b-ext"]


# === CLI =============================================================


def test_cli_writes_catalog_json_beside_the_wheels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = _wheel(tmp_path, "notes-ext", "1.0.0")

    rc = ei.run_extension_command(["index", str(tmp_path), "--name", "Acme"])

    assert rc == 0
    written = tmp_path / "catalog.json"
    assert written.is_file()
    document = json.loads(written.read_text())
    assert document["name"] == "Acme"
    assert document["schemaVersion"] == ec.SCHEMA_VERSION
    assert _entries(document)[0]["sha256"] == _sha256(wheel)
    out = capsys.readouterr().out
    assert "1 extension" in out
    # The next step is named, so the operator is not left guessing.
    assert "source add --catalog" in out


def test_cli_relative_flag_works_from_a_relative_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`index . --relative` must not crash.

    `--relative` exists for a wheelhouse that travels, and the guide tells
    operators to run from inside it — so `.` is the argument they will actually
    type. It used to raise ValueError out of `relative_to`: the scan walked an
    UNRESOLVED root and yielded bare `foo.whl` paths, while `relative_to` was
    handed the RESOLVED root, and a bare path is not under an absolute one. The
    build call sits outside the handler's try/except, so it surfaced as a raw
    traceback and exit 1 rather than a clean error.
    """
    wheel = _wheel(tmp_path, "notes-ext", "1.0.0")
    monkeypatch.chdir(tmp_path)

    rc = ei.run_extension_command(["index", ".", "--relative"])

    assert rc == 0, capsys.readouterr().err
    catalog = ec.parse_catalog(
        (tmp_path / "catalog.json").read_text(), location="file://rel"
    )
    assert [e.source for e in catalog.entries] == [wheel.name]


def test_cli_relative_directory_without_the_flag_still_emits_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same unresolved-root path, on the default (absolute) branch."""
    wheel = _wheel(tmp_path, "notes-ext", "1.0.0")
    monkeypatch.chdir(tmp_path)

    assert ei.run_extension_command(["index", "."]) == 0

    source = json.loads((tmp_path / "catalog.json").read_text())["extensions"][0][
        "source"
    ]
    assert Path(source).is_absolute()
    assert Path(source) == wheel.resolve()


def test_cli_out_dash_writes_stdout_and_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _wheel(tmp_path, "notes-ext", "1.0.0")

    rc = ei.run_extension_command(["index", str(tmp_path), "--out", "-"])

    assert rc == 0
    assert not (tmp_path / "catalog.json").exists()
    assert json.loads(capsys.readouterr().out)["extensions"][0]["name"] == "notes-ext"


def test_cli_out_path_writes_elsewhere(tmp_path: Path) -> None:
    _wheel(tmp_path, "notes-ext", "1.0.0")
    out = tmp_path / "published" / "index.json"

    rc = ei.run_extension_command(["index", str(tmp_path), "--out", str(out)])

    assert rc == 0
    assert json.loads(out.read_text())["extensions"][0]["name"] == "notes-ext"


def test_cli_empty_directory_is_a_valid_empty_catalog(tmp_path: Path) -> None:
    rc = ei.run_extension_command(["index", str(tmp_path)])

    assert rc == 0
    catalog = ec.parse_catalog(
        (tmp_path / "catalog.json").read_text(), location="file://empty"
    )
    assert catalog.entries == ()


def test_cli_rejects_a_missing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = ei.run_extension_command(["index", str(tmp_path / "nope")])

    assert rc == ei._EXIT_DIDNT_RUN
    assert "not a directory" in capsys.readouterr().err


def test_cli_rejects_a_missing_argument_and_unknown_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ei.run_extension_command(["index"]) == ei._EXIT_DIDNT_RUN
    assert ei.run_extension_command(["index", str(tmp_path), "--nope"]) == (
        ei._EXIT_DIDNT_RUN
    )
    assert ei.run_extension_command(["index", str(tmp_path), "--out"]) == (
        ei._EXIT_DIDNT_RUN
    )
    assert "requires a <dir>" in capsys.readouterr().err


def test_index_is_advertised_in_the_usage(capsys: pytest.CaptureFixture[str]) -> None:
    ei.run_extension_command(["--help"])
    assert "index <dir>" in capsys.readouterr().out
