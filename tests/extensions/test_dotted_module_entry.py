"""Issue #114 — ``-e <dotted.module>`` from the CLI, and the shipped examples.

The defect was a **CLI/library divergence**, not a missing feature:
``_resolve_factory`` has always handled a ``str`` module reference (bare
``pkg.mod`` → top-level ``setup``; ``pkg.mod:factory`` → named callable), but
``_discover_entries`` coerced every configured ``str`` to ``Path`` first, so
that branch was unreachable from ``-e``. The identical string loaded through
``load_extensions`` and failed through the CLI with
``Extension file not found: <cwd>/pkg.mod``.

The equivalence tests below are the load-bearing ones: they assert the two
surfaces agree, because *disagreement* is the bug. The classification tests
pin the safety property that makes the change non-breaking — an existing path
always wins, and anything unclassifiable keeps the historical path reading.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from aelix_agent_core.contracts.manifest import (
    LICENSE_WHITELIST,
    PluginManifest,
    parse_manifest_toml,
)
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.extensions import loader as loader_mod
from aelix_coding_agent.extensions.loader import (
    _is_module_ref,
    discover_and_load_extensions,
    load_extensions,
    scan_extension_manifests,
)

# The two extensions Aelix actually ships. The #117 self-extension signpost
# points the model at echo by absolute path and calls it "worked example, read
# this one" — #114 is about making the documented way to RUN it work.
ECHO_REF = "aelix_coding_agent.examples.echo.echo"
SELFHOSTED_REF = "aelix_coding_agent.examples.selfhosted.selfhosted"

_MANIFEST_PATH = (
    Path(loader_mod.__file__).resolve().parents[1]
    / "examples"
    / "echo"
    / "aelix-plugin.toml"
)


def _isolated(tmp_path: Path) -> tuple[Path, Path]:
    """A cwd + agent_dir pair that cannot pick up the developer's real
    ``~/.aelix`` or the repo's own ``.aelix/extensions``."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    return cwd, agent_dir


async def _via_cli_path(
    entries: list[str], *, cwd: Path, agent_dir: Path
) -> object:
    """Exactly what ``cli/entry.py`` calls (``[str(p) for p in
    parsed.extensions]`` into ``discover_and_load_extensions``), with the
    ambient discovery tiers switched off so only ``-e`` is under test."""
    return await discover_and_load_extensions(
        entries,
        cwd=cwd,
        agent_dir=agent_dir,
        no_discovery=True,
        no_project_local=True,
    )


# --- The two shipped examples load by their dotted path -------------------


@pytest.mark.asyncio
async def test_shipped_echo_example_loads_by_dotted_module_path(
    tmp_path: Path,
) -> None:
    """The user-visible promise: ``aelix -e
    aelix_coding_agent.examples.echo.echo`` runs the example the signpost
    tells the agent to read."""
    cwd, agent_dir = _isolated(tmp_path)
    result = await _via_cli_path([ECHO_REF], cwd=cwd, agent_dir=agent_dir)

    assert result.errors == []
    assert [e.name for e in result.extensions] == [ECHO_REF]
    # setup() really ran: the tool and the slash command are registered.
    (ext,) = result.extensions
    assert list(ext.tools) == ["echo"]
    assert "hello" in ext.commands


@pytest.mark.asyncio
async def test_shipped_selfhosted_example_loads_by_dotted_module_path(
    tmp_path: Path,
) -> None:
    cwd, agent_dir = _isolated(tmp_path)
    result = await _via_cli_path([SELFHOSTED_REF], cwd=cwd, agent_dir=agent_dir)

    assert result.errors == []
    assert [e.name for e in result.extensions] == [SELFHOSTED_REF]


@pytest.mark.asyncio
async def test_module_callable_colon_form_works_from_the_cli_path(
    tmp_path: Path,
) -> None:
    """``module.path:callable`` was broken from ``-e`` too, not just the bare
    form — the Path coercion hit every string."""
    cwd, agent_dir = _isolated(tmp_path)
    result = await _via_cli_path(
        [f"{ECHO_REF}:setup"], cwd=cwd, agent_dir=agent_dir
    )

    assert result.errors == []
    (ext,) = result.extensions
    assert list(ext.tools) == ["echo"]


# --- The equivalence that IS the bug --------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        ECHO_REF,
        SELFHOSTED_REF,
        f"{ECHO_REF}:setup",
        # The FAILING half. Divergence on the error path reopens #114 just as
        # surely as divergence on the load path, and it is where the two
        # surfaces actually drifted: ``load_extensions`` reaches
        # ``_resolve_factory``'s str branch without consulting
        # ``_is_module_ref``, so a message that hard-codes that predicate's
        # rationale can contradict the filesystem. Compared, not asserted
        # empty.
        "aelix_no_such_zz",
        "aelix_no_such_zz.sub",
        f"{ECHO_REF}:no_such_callable_zz",
    ],
)
@pytest.mark.asyncio
async def test_cli_path_and_library_api_agree_on_the_same_string(
    ref: str, tmp_path: Path
) -> None:
    """Pin the equivalence for module-SHAPED strings: the CLI tier-3 path and
    ``load_extensions`` must reach the same outcome, loading OR failing, for
    the same string. Before #114 the CLI errored while the library loaded, on
    inputs identical down to the character.

    Scoped to module-shaped strings on purpose — the equivalence does NOT hold
    in general and must not be asserted as if it did. Tier-3 discovery
    deliberately adds directory expansion the library API has no equivalent
    for, so ``'d'`` with a real ``cwd/d/`` directory loads via the CLI and
    fails via ``load_extensions``, by design.
    """
    cwd, agent_dir = _isolated(tmp_path)

    via_cli = await _via_cli_path([ref], cwd=cwd, agent_dir=agent_dir)
    via_lib = await load_extensions([ref], cwd=cwd)

    assert [e.name for e in via_cli.extensions] == [
        e.name for e in via_lib.extensions
    ]
    assert [(e.path, e.error) for e in via_cli.errors] == [
        (e.path, e.error) for e in via_lib.errors
    ]


@pytest.mark.asyncio
async def test_argv_parsing_feeds_the_loader_the_unmodified_string(
    tmp_path: Path,
) -> None:
    """Close the last gap between this test module and a real ``aelix -e``:
    drive the actual argv parser, then hand its output to the loader the way
    ``cli/entry.py`` does. Nothing between the shell and the loader rewrites
    the reference."""
    cwd, agent_dir = _isolated(tmp_path)
    parsed = parse_args(["-e", ECHO_REF, "--print", "hi"])

    assert parsed.extensions == [ECHO_REF]

    result = await _via_cli_path(
        [str(p) for p in parsed.extensions], cwd=cwd, agent_dir=agent_dir
    )
    assert result.errors == []
    assert [e.name for e in result.extensions] == [ECHO_REF]


# --- Path inputs behave exactly as before ---------------------------------


@pytest.mark.asyncio
async def test_relative_absolute_and_directory_path_entries_are_unchanged(
    tmp_path: Path,
) -> None:
    """``-e ./ext.py``, ``-e /abs/ext.py`` and ``-e some_dir/`` keep loading
    from disk. The predicate must not have stolen any of them."""
    cwd, agent_dir = _isolated(tmp_path)
    body = textwrap.dedent(
        """
        def setup(aelix):
            return None
        """
    ).strip()
    (cwd / "rel_ext.py").write_text(body, encoding="utf-8")
    abs_ext = tmp_path / "abs_ext.py"
    abs_ext.write_text(body, encoding="utf-8")
    pkg_dir = cwd / "extdir"
    pkg_dir.mkdir()
    (pkg_dir / "dir_ext.py").write_text(body, encoding="utf-8")

    result = await _via_cli_path(
        ["./rel_ext.py", str(abs_ext), "extdir/"],
        cwd=cwd,
        agent_dir=agent_dir,
    )

    assert result.errors == []
    assert len(result.extensions) == 3


@pytest.mark.asyncio
async def test_a_dotted_name_that_exists_on_disk_is_read_as_a_path(
    tmp_path: Path,
) -> None:
    """The plan's central ambiguity rule, and the reason this change cannot
    break a working input: ``mypkg.ext`` is module-SHAPED, but a real file at
    that name wins. Precedent — ``classify_target``: "a local path WINS if it
    exists on disk"."""
    cwd, agent_dir = _isolated(tmp_path)
    disk_entry = cwd / "mypkg.ext"
    disk_entry.mkdir()
    (disk_entry / "on_disk.py").write_text(
        "def setup(aelix):\n    return None\n", encoding="utf-8"
    )

    assert _is_module_ref("mypkg.ext", cwd=cwd) is False

    result = await _via_cli_path(["mypkg.ext"], cwd=cwd, agent_dir=agent_dir)
    assert result.errors == []
    # Loaded from the directory on disk, NOT imported as a module.
    assert len(result.extensions) == 1


@pytest.mark.asyncio
async def test_a_missing_file_still_produces_todays_error_message(
    tmp_path: Path,
) -> None:
    """Plan §6: a file that genuinely does not exist keeps the old error.
    Every path-SHAPED spelling (suffix, separator, absolute) still lands in
    ``_factory_from_file``."""
    cwd, agent_dir = _isolated(tmp_path)

    result = await _via_cli_path(
        ["./nope.py", "missing_dir/nope.py", str(tmp_path / "gone.py")],
        cwd=cwd,
        agent_dir=agent_dir,
    )

    assert result.extensions == []
    assert len(result.errors) == 3
    for err in result.errors:
        assert err.error.startswith("Extension file not found: ")


# --- Classification safety net --------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "./ext.py",  # relative, .py
        "/abs/ext.py",  # absolute, .py
        "ext.py",  # bare .py — suffix clause
        "some_dir/",  # trailing separator
        "sub/dir/mod",  # embedded separator, no suffix
        "..",  # relative-parent — exists
        ".",  # cwd — exists
        "",  # empty
        "C:\\ext",  # Windows drive, backslash
        "C:/ext",  # Windows drive, forward slash
        "9x:setup",  # leading digit — not an identifier
        "a:b:c",  # two colons — not module:callable
        "pkg.mod:",  # empty callable
        ":setup",  # empty module
        "pkg..mod",  # empty dotted segment
        "pkg.mod ",  # trailing space — not an identifier
        "has-a-dash",  # hyphen — not an identifier
        "pkg.mod:call able",  # space in callable
    ],
)
def test_unclassifiable_or_path_shaped_entries_keep_the_path_reading(
    entry: str, tmp_path: Path
) -> None:
    """The regression-safety proof the plan asks for (§2 clause 6): every
    input we cannot confidently call a module keeps TODAY's behaviour, so
    #114 can only ADD working inputs and can never move a previously-working
    one into a new failure mode."""
    assert _is_module_ref(entry, cwd=tmp_path) is False


@pytest.mark.parametrize(
    "entry",
    [
        "pkg",
        "pkg.mod",
        "pkg.sub.mod",
        "_private.mod",
        "pkg9.mod9",
        "pkg.mod:setup",
        "pkg.sub.mod:build_ext",
        "c:setup",  # one-letter module in colon form (POSIX)
    ],
)
def test_module_shaped_entries_are_classified_as_modules(
    entry: str, tmp_path: Path
) -> None:
    assert _is_module_ref(entry, cwd=tmp_path) is True


def test_windows_drive_letter_is_a_path_not_module_callable(
    tmp_path: Path,
) -> None:
    """Ordering check the plan demanded be verified rather than assumed.

    The plan expected clause 3 (path separator) to exclude drive letters
    before the colon clause could fire. On POSIX it does NOT: ``os.sep`` is
    ``/`` and ``os.altsep`` is ``None``, so ``C:\\ext`` contains no separator
    this platform recognises. The explicit drive-prefix clause is what makes
    the exclusion hold on every platform — without it, a Windows path parsed
    on a POSIX host (a config file shared across a team) would be handed to
    ``import_module`` as module ``C``, callable ``ext``."""
    assert _is_module_ref("C:\\ext", cwd=tmp_path) is False
    assert _is_module_ref("C:/ext", cwd=tmp_path) is False
    assert _is_module_ref("c:\\ext\\sub.py", cwd=tmp_path) is False


def test_drive_relative_path_is_a_path_only_when_running_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``C:ext`` (drive-relative) overlaps the legitimate one-letter
    ``module:callable`` form. Deliberate split: on Windows the path reading
    wins, because drive-relative paths are a native spelling there and a
    one-letter module is vanishingly rare; on POSIX ``C:ext`` cannot be a
    path at all, so the module reading is kept.

    ``os.name`` is patched through the loader's own ``_OS_NAME`` seam, NOT on
    the stdlib ``os`` module: ``loader.os is os``, and ``pathlib`` dispatches
    ``Path()`` to ``WindowsPath`` off ``os.name``, so patching it there would
    make every ``Path`` in the interpreter a ``WindowsPath`` for the duration
    of this test — including any built by pytest's failure reporting if an
    assertion below failed.
    """
    assert _is_module_ref("C:ext", cwd=tmp_path) is True

    monkeypatch.setattr(loader_mod, "_OS_NAME", "nt")
    assert _is_module_ref("C:ext", cwd=tmp_path) is False
    assert _is_module_ref("c:setup", cwd=tmp_path) is False
    # A dotted module is still a module on Windows.
    assert _is_module_ref("pkg.mod", cwd=tmp_path) is True


# --- The one error message that deliberately changed ----------------------


@pytest.mark.asyncio
async def test_unresolvable_bare_name_names_both_interpretations(
    tmp_path: Path,
) -> None:
    """A dotless bare name with nothing on disk is genuinely ambiguous, and
    #114 moves it from the path reading to the module reading (so
    ``-e my_installed_ext`` works). Nothing that WORKED changed — a bare name
    resolving on disk is claimed by the exists clause — but the failure
    message did, so it must name BOTH readings."""
    cwd, agent_dir = _isolated(tmp_path)

    result = await _via_cli_path(
        ["aelix_no_such_ext_zz"], cwd=cwd, agent_dir=agent_dir
    )

    assert result.extensions == []
    (err,) = result.errors
    assert "No module named 'aelix_no_such_ext_zz'" in err.error
    assert str(cwd / "aelix_no_such_ext_zz") in err.error
    assert "./aelix_no_such_ext_zz.py" in err.error


@pytest.mark.asyncio
async def test_a_missing_dependency_inside_the_extension_is_not_rewritten(
    tmp_path: Path,
) -> None:
    """The guard that makes the message rewrite safe. A module which itself
    imports something missing raises ``ModuleNotFoundError`` too; blaming the
    user's spelling ("and no extension file at …") for a missing third-party
    dependency would be strictly worse diagnostics than the case the rewrite
    fixes. This is also why the reverse design — import first, fall back to
    the path reading on failure — was rejected."""
    cwd, agent_dir = _isolated(tmp_path)
    pkg = tmp_path / "sitedir" / "aelix_dep_probe_zz"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ext.py").write_text(
        "import aelix_absent_dependency_zz\n\n"
        "def setup(aelix):\n    return None\n",
        encoding="utf-8",
    )
    import sys

    sys.path.insert(0, str(tmp_path / "sitedir"))
    try:
        result = await _via_cli_path(
            ["aelix_dep_probe_zz.ext"], cwd=cwd, agent_dir=agent_dir
        )
    finally:
        sys.path.remove(str(tmp_path / "sitedir"))
        sys.modules.pop("aelix_dep_probe_zz", None)
        sys.modules.pop("aelix_dep_probe_zz.ext", None)

    (err,) = result.errors
    assert err.error == "No module named 'aelix_absent_dependency_zz'"
    assert "no extension file at" not in err.error


@pytest.mark.asyncio
async def test_module_import_ignores_the_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-e <module>`` must NOT resolve against the project directory.

    ``importlib.import_module`` uses the ambient ``sys.path``, and under
    ``python -m aelix_coding_agent`` — verbatim the command
    ``aelix_agents.print_channel`` uses to spawn every subagent, with the
    child's cwd — ``sys.path[0]`` IS the cwd. Before this guard, ``-e acme``
    inside a cloned repo containing ``acme.py`` imported the REPO's file and
    ran its top-level code, outside the Project Trust gate (tier-3 explicit
    entries load regardless of ``no_project_local``) and unaffected by
    ``--no-approve``. ``_is_module_ref`` does not catch it: the entry is
    ``acme`` while the file is ``acme.py``, so the on-disk clause never fires.
    """
    cwd, agent_dir = _isolated(tmp_path)
    marker = tmp_path / "PROJECT_LOCAL_CODE_RAN"
    (cwd / "aelix_shadow_probe_zz.py").write_text(
        f"import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('owned')\n\n"
        "def setup(aelix):\n    return None\n",
        encoding="utf-8",
    )
    # Simulate `python -m`: the project directory is sys.path[0].
    monkeypatch.syspath_prepend(str(cwd))

    result = await _via_cli_path(
        ["aelix_shadow_probe_zz"], cwd=cwd, agent_dir=agent_dir
    )

    assert result.extensions == []
    assert not marker.exists(), "project-local module was imported and executed"
    (err,) = result.errors
    # And the refusal is explained, not silent — the file IS right there.
    assert "aelix_shadow_probe_zz.py" in err.error
    assert "does not import extensions from the project directory" in err.error
    assert "./aelix_shadow_probe_zz.py" in err.error


@pytest.mark.asyncio
async def test_a_genuinely_installed_module_still_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterpart: blocking the cwd must not block the installed
    environment. Only the project directory comes off ``sys.path``."""
    cwd, agent_dir = _isolated(tmp_path)
    site = tmp_path / "sitedir"
    site.mkdir()
    (site / "aelix_installed_probe_zz.py").write_text(
        "def setup(aelix):\n    return None\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(site))

    result = await _via_cli_path(
        ["aelix_installed_probe_zz"], cwd=cwd, agent_dir=agent_dir
    )

    assert result.errors == []
    assert [e.name for e in result.extensions] == ["aelix_installed_probe_zz"]


def test_sys_path_is_restored_and_extension_additions_survive(
    tmp_path: Path,
) -> None:
    """The guard must leave ``sys.path`` as it found it — and must not undo a
    mutation the extension made while importing. Restoring by assigning a
    saved list back would silently drop the extension's own append."""
    import sys

    original = list(sys.path)
    with loader_mod._import_path_without_cwd(tmp_path):
        assert str(tmp_path) not in sys.path
        sys.path.append("/aelix-probe-added-by-extension")
    try:
        assert sys.path[: len(original)] == original
        assert "/aelix-probe-added-by-extension" in sys.path
    finally:
        sys.path.remove("/aelix-probe-added-by-extension")
    assert sys.path == original


@pytest.mark.asyncio
async def test_a_module_shaped_entry_shadowed_by_the_cwd_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Path-wins stays (the plan's acceptance case depends on it) but stops
    being silent. Otherwise the directory you happen to be standing in decides
    whether ``-e acme`` means "my installed extension" or "run this repo's
    code", with no trust gate and no diagnostic."""
    cwd, agent_dir = _isolated(tmp_path)
    shadow = cwd / "acme_shadow_zz"
    shadow.mkdir()
    (shadow / "payload.py").write_text(
        "def setup(aelix):\n    return None\n", encoding="utf-8"
    )

    with caplog.at_level("WARNING", logger=loader_mod.__name__):
        result = await _via_cli_path(
            ["acme_shadow_zz"], cwd=cwd, agent_dir=agent_dir
        )

    # Still loads from disk — the warning is advisory, not a refusal.
    assert result.errors == []
    assert len(result.extensions) == 1
    text = caplog.text
    assert "looks like a module reference" in text
    assert "acme_shadow_zz" in text
    assert "PROJECT-LOCAL" in text


def test_a_dangling_or_looping_symlink_keeps_the_path_reading(
    tmp_path: Path,
) -> None:
    """Clause 4 uses ``os.path.lexists``, not ``Path.exists``.

    ``Path.exists()`` follows symlinks and returns ``False`` for a dangling or
    looping one, which would classify a path the user demonstrably created as
    a dotted module and replace an accurate filesystem diagnosis with a
    misleading "no module named"."""
    cwd, _ = _isolated(tmp_path)
    (cwd / "stale_ext.mod").symlink_to(tmp_path / "gone")
    (cwd / "loop").symlink_to(cwd / "loop")

    assert _is_module_ref("stale_ext.mod", cwd=cwd) is False
    assert _is_module_ref("loop", cwd=cwd) is False


@pytest.mark.parametrize("entry", ["justaname\n", f"{ECHO_REF}\n"])
def test_a_trailing_newline_is_not_a_module_reference(
    entry: str, tmp_path: Path
) -> None:
    """The anchors are ``\\Z``, not ``$``. In Python ``$`` also matches just
    before a trailing newline, so a config- or file-sourced entry with
    trailing whitespace would classify as a module and splice a literal
    newline into the middle of the error message."""
    assert _is_module_ref(entry, cwd=tmp_path) is False


@pytest.mark.asyncio
async def test_library_api_error_does_not_contradict_the_filesystem(
    tmp_path: Path,
) -> None:
    """``load_extensions`` reaches ``_resolve_factory``'s str branch WITHOUT
    consulting ``_is_module_ref``, so the message must re-check rather than
    restate the predicate's rationale as fact. ``'d'`` with a real ``cwd/d``
    directory must not be told "nothing exists at that path", and ``'d/'``
    must not be told it has "no path separator"."""
    cwd, _ = _isolated(tmp_path)
    (cwd / "d").mkdir()
    (cwd / "d" / "x.py").write_text(
        "def setup(aelix):\n    return None\n", encoding="utf-8"
    )

    for ref in ("d", "d/"):
        result = await load_extensions([ref], cwd=cwd)
        (err,) = result.errors
        assert err.error == f"No module named {ref!r}", err.error
        assert "nothing exists at that path" not in err.error
        assert "no path separator" not in err.error


# --- Interaction with the rest of the discovery pipeline ------------------


@pytest.mark.asyncio
async def test_a_repeated_module_reference_loads_once(tmp_path: Path) -> None:
    """Module strings are deduped by the literal string, mirroring the
    ``Path.resolve()`` dedupe the path branch has always applied (before
    #114 a repeated module string deduped as a repeated cwd-relative path)."""
    cwd, agent_dir = _isolated(tmp_path)

    result = await _via_cli_path(
        [ECHO_REF, ECHO_REF], cwd=cwd, agent_dir=agent_dir
    )

    assert result.errors == []
    assert [e.name for e in result.extensions] == [ECHO_REF]


def test_manifest_scan_ignores_module_references(tmp_path: Path) -> None:
    """``scan_extension_manifests`` shares ``_discover_entries``, so it now
    sees bare ``str`` entries. A module reference carries no
    ``aelix-plugin.toml``, so it must contribute nothing — and, since the
    scan's contract is metadata-only, must not import anything either (the
    module below does not exist; a scan that imported would error)."""
    cwd, agent_dir = _isolated(tmp_path)

    manifests = scan_extension_manifests(
        [ECHO_REF, "aelix_no_such_ext_zz"],
        cwd=cwd,
        agent_dir=agent_dir,
        no_discovery=True,
        no_project_local=True,
    )

    assert manifests == []


# --- The repository's first real aelix-plugin.toml ------------------------


def test_the_shipped_example_manifest_round_trips_through_pluginmanifest() -> (
    None
):
    """The reference manifest exists to be COPIED, so it has to be valid
    against the strict (``extra="forbid"``) schema — a live session measured
    an unassisted agent inventing a fake ``tools_definition.json`` because no
    real manifest was on disk to imitate."""
    assert _MANIFEST_PATH.is_file(), _MANIFEST_PATH

    manifest = parse_manifest_toml(_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert isinstance(manifest, PluginManifest)
    # Round-trip: dump and re-validate, so no field survives only by
    # tolerance of the parser's flattening.
    assert PluginManifest.model_validate(manifest.model_dump()) == manifest
    assert manifest.plugin.license in LICENSE_WHITELIST


def test_the_examples_index_lists_every_shipped_example() -> None:
    """``INDEX.md`` is the discoverability half of #114 — nothing pointed at
    these examples before. Guard it against drift: a new example that never
    reaches the index is an example nobody finds."""
    examples_dir = _MANIFEST_PATH.parents[1]
    index = examples_dir / "INDEX.md"
    assert index.is_file(), index
    text = index.read_text(encoding="utf-8")

    shipped = sorted(
        f"{p.parent.name}/{p.name}"
        for p in examples_dir.glob("*/*.py")
        if p.name != "__init__.py"
    )
    assert shipped, "no examples found — has the layout moved?"
    for rel in shipped:
        assert rel in text, f"{rel} is not listed in {index}"

    # The runnable spelling the index promises must be the one that works.
    assert f"-e {ECHO_REF}" in text


def test_the_shipped_example_manifest_describes_the_code_beside_it() -> None:
    """A manifest is a declaration, not an implementation. If it drifts from
    ``echo.py`` it stops being worth copying, so pin the claims it makes."""
    manifest = parse_manifest_toml(_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest.entry.python == f"{ECHO_REF}:setup"
    assert [c.id for c in manifest.contributes.commands] == ["hello"]
    assert [t.name for t in manifest.contributes.tools] == ["echo"]

    from aelix_coding_agent.examples.echo import echo as echo_module

    assert callable(echo_module.setup)
    assert echo_module.echo_tool.name == "echo"
