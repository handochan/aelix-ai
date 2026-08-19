"""Issue #154 — ``extension install`` reports the BINDING VERDICT it already knows.

``classify_installed_endpoints()`` decides — import-free and offline — whether an
installed pack's ``aelix-plugin.toml`` will bind. Before #154 only ``verify`` and
``list`` called it, so ``install`` printed an unconditional "Installed. Restart
aelix" for a pack the SAME build, at the SAME instant, would refuse to load.

These tests exercise the real seam end to end, not a mocked verdict: the injected
pip runner WRITES a genuine installed-wheel image (a real ``dist-info`` with a
real ``RECORD``) into a throwaway ``site/`` on ``sys.path``, exactly as
``tests/cli/test_extension_verify.py`` does. So the ledger diff, the attribution,
the provenance fence and the metadata-only resolver all run for real, and a test
that passes proves the CLI path — not that a stub returned what it was told to.

The fixture trap this issue's own repro fell into is guarded explicitly: the
first attempt at the incompatible pack failed as MALFORMED on a missing
``plugin.repository`` — the WRONG reason, while looking like a passing API-level
test. Every incompatible-pack assertion here therefore also asserts the REASON
text, so a fixture that regresses into a schema error fails instead of passing.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import cast

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import extension_catalog
from aelix_coding_agent.cli import extension_install as EI
from aelix_coding_agent.cli.extension_install import run_extension_command

from tests.extensions.test_ep_manifest import (
    EXT_PY,
    MANIFEST,
    install_dist,
    manifest_toml,
)

#: The exact success line a healthy install must still print, byte for byte.
RESTART_LINE = (
    "Installed. Restart aelix (or /reload in the TUI) so the loader "
    "discovers it via entry_points."
)


def test_exit_codes_are_the_documented_four() -> None:
    """``install``'s exit contract, pinned. ADR-0185 gave this verb a 3-way split
    (0 / pip's code / 2 = never ran); #154 adds a FOURTH value rather than
    overloading pip's ``1``, so a script can still tell "nothing landed on disk"
    from "installed but it will not bind"."""

    assert EI._INSTALL_NOT_BOUND == 3
    assert EI._EXIT_DIDNT_RUN == 2
    assert EI._INSTALLER_FAILED == 1
    # Four VALUES, pairwise distinct — the claim "a script can tell the four
    # apart" is only true if nothing else on this verb can return them, which is
    # why the installer's own code is normalised rather than passed through.
    assert len({0, EI._INSTALLER_FAILED, EI._EXIT_DIDNT_RUN, EI._INSTALL_NOT_BOUND}) == 4


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep settings / agent-dir / project scope off the developer's real files."""

    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.chdir(tmp_path)


def _scoped_entry_points(site_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope ``entry_points()`` to ``site_dir`` so an ambient install cannot leak in.

    Without this an extension pack that happens to be installed in the developer's
    real environment would join every count assertion below.
    """

    real_ep = importlib.metadata.entry_points
    root = str(site_dir)

    def scoped(**kwargs: str) -> importlib.metadata.EntryPoints:
        # Every caller under test passes ``group=``; that overload really does
        # return ``EntryPoints`` at runtime, while the 3.11 stub's no-argument
        # overload is what makes the inferred type ambiguous.
        found = cast("Iterable[importlib.metadata.EntryPoint]", real_ep(**kwargs))
        return importlib.metadata.EntryPoints(
            ep
            for ep in found
            if (dist := ep.dist) is not None
            and str(dist.locate_file("")).startswith(root)
        )

    monkeypatch.setattr(importlib.metadata, "entry_points", scoped)


@pytest.fixture
def trusted_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway site-packages on ``sys.path``, DECLARED TRUSTED.

    Standing in for a real ``pip install`` target: a dist written here passes the
    sys.path provenance fence, so the verdict under test is about the MANIFEST,
    not about provenance.
    """

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    sys.path.insert(0, str(site_dir))
    _scoped_entry_points(site_dir, monkeypatch)
    real_env = EI.environment_site_dirs
    monkeypatch.setattr(
        EI, "environment_site_dirs", lambda: real_env() | frozenset({site_dir.resolve()})
    )
    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))
        _drop_modules(site_dir)


@pytest.fixture
def untrusted_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The same throwaway site, NOT declared trusted.

    This is what ``pip install --target`` / ``PIP_TARGET`` plus a matching
    ``PYTHONPATH`` produces on the real install path — MEASURED reachable, which
    is why ``install`` grew ``--trust-extension-path``.
    """

    site_dir = tmp_path / "outside"
    site_dir.mkdir()
    sys.path.insert(0, str(site_dir))
    _scoped_entry_points(site_dir, monkeypatch)
    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))
        _drop_modules(site_dir)


def _drop_modules(site_dir: Path) -> None:
    root = str(site_dir)
    for name, mod in list(sys.modules.items()):
        origin = getattr(mod, "__file__", None) or ""
        if origin.startswith(root):
            del sys.modules[name]


# --- pack shapes -----------------------------------------------------------


def _bound_files(name: str, *, module: str | None = None) -> dict[str, str]:
    pkg = module or name
    return {
        f"{pkg}/__init__.py": "",
        f"{pkg}/ext.py": EXT_PY.format(name=pkg),
        f"{pkg}/{MANIFEST}": manifest_toml(name, entry=f"{pkg}.ext:setup"),
    }


def _future_files(name: str) -> dict[str, str]:
    """A pack declaring an API level this host cannot satisfy (host is at 1)."""

    return {
        f"{name}/__init__.py": "",
        f"{name}/ext.py": EXT_PY.format(name=name),
        f"{name}/{MANIFEST}": manifest_toml(
            name, entry=f"{name}.ext:setup", level=7, min_level=7
        ),
    }


class _InstallingRunner:
    """A pip runner that actually LANDS a synthetic dist in the throwaway site.

    Without this the ledger never moves and every attribution path would be
    vacuously empty — the classic "test passes because nothing happened" shape.
    """

    def __init__(
        self,
        site: Path,
        dist: str,
        *,
        files: dict[str, str],
        entry_points: dict[str, str],
        version: str = "0.1.0",
        returncode: int = 0,
    ) -> None:
        self.site = site
        self.dist = dist
        self.files = files
        self.entry_points = entry_points
        self.version = version
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if self.returncode == 0:
            install_dist(
                self.site,
                self.dist,
                version=self.version,
                entry_points={"aelix.extensions": self.entry_points},
                files=self.files,
            )
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode)


class _NoopRunner:
    """Reports success and installs NOTHING — pip's "Requirement already satisfied"."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0)


def _record_direct_url(dist_info: Path, url: str, **extra: object) -> None:
    """Write the PEP 610 record pip writes for every DIRECT reference.

    Not a convenience: it is the fixture that makes the "no-op reinstall" tests
    real. pip stores the source URL with ``git+`` and any ``@<rev>`` / ``#egg=``
    already stripped, and a directory as ``file://`` + ``os.path.abspath`` — the
    exact shapes measured in a throwaway venv on this machine:

        gitpack   {"url": "file:///…/packs/gitpack",
                   "vcs_info": {"vcs": "git", "commit_id": "a9c9d94…"}}
        legacypack {"dir_info": {}, "url": "file:///…/packs/legacypack"}
    """

    body: dict[str, object] = {"url": url, **extra}
    (dist_info / "direct_url.json").write_text(json.dumps(body), encoding="utf-8")


def _uri(path: Path) -> str:
    """``file://`` for ``path``, spelled the way pip spells it (abspath, not realpath)."""

    return Path(os.path.abspath(path)).as_uri()


def _mem() -> SettingsManager:
    return SettingsManager.in_memory()


def _src(tmp_path: Path, name: str = "src") -> str:
    """A real local directory so ``classify_target`` says ``path`` (never pypi)."""

    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return str(d)


# === the defect: an incompatible pack was called "Installed. Restart" ======


def test_incompatible_pack_is_reported_and_not_called_installed_restart(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _InstallingRunner(
        trusted_site,
        "futurepack",
        files=_future_files("futurepack"),
        entry_points={"futurepack": "futurepack.ext:setup"},
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out

    # Asserted FIRST on purpose: this is the defect in its observable form, so a
    # revert-check failure reads as the bug ("Restart aelix" for a pack that will
    # never load) rather than as a missing symbol.
    assert RESTART_LINE not in out
    assert code == 3
    assert "will NOT bind" in out
    assert "INCOMPATIBLE" in out
    # FIXTURE TRAP GUARD: prove it failed for the API-level reason, not because
    # the manifest was malformed / missing a required field.
    assert "requires API_LEVEL >= 7" in out
    assert "host has 1" in out
    assert "MALFORMED" not in out
    # The package is already on disk; the user gets the exact undo, not an
    # auto-uninstall.
    assert "nothing was undone" in out
    assert "aelix extension remove futurepack" in out
    assert "aelix extension verify futurepack" in out


def test_verify_and_install_now_agree_on_the_same_endpoint(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The heart of #154: the two commands ran the same primitive and said
    opposite things. Same build, same instant — now the same verdict text."""

    runner = _InstallingRunner(
        trusted_site,
        "futurepack",
        files=_future_files("futurepack"),
        entry_points={"futurepack": "futurepack.ext:setup"},
    )
    install_code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    install_out = capsys.readouterr().out
    verify_code = run_extension_command(["verify", "futurepack"])
    verify_out = capsys.readouterr().out

    assert install_code != 0 and verify_code != 0
    row = "  INCOMPATIBLE futurepack [futurepack 0.1.0]"
    assert row in install_out
    assert row in verify_out


# === no false alarm: a compatible pack is byte-unchanged ==================


def test_compatible_pack_output_is_unchanged(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _InstallingRunner(
        trusted_site,
        "goodpack",
        files=_bound_files("goodpack"),
        entry_points={"goodpack": "goodpack.ext:setup"},
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert RESTART_LINE in out
    # Not one new word of chrome for a healthy pack.
    for noise in ("BOUND", "will NOT bind", "nothing was undone", "extension remove"):
        assert noise not in out


def test_install_with_no_extension_endpoint_says_it_has_no_verdict(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing tied to an extension distribution ⇒ NO verdict, and it says so.

    Round 1 printed the pre-#154 line here and called that "honest". It is not:
    that line PROMISES "the loader discovers it via entry_points", and a
    distribution with no ``aelix.extensions`` endpoint is one the loader will
    never discover. Exit stays 0 — the installer did put something on disk, and
    ``_INSTALL_NOT_BOUND`` must keep meaning "this build KNOWS it will not bind".
    """

    runner = _NoopRunner()
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert EI._INSTALLED_NO_VERDICT in out
    assert RESTART_LINE not in out
    assert "will NOT bind" not in out


# === one distribution, several endpoints =================================


def test_one_distribution_several_endpoints(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Attribution is per DISTRIBUTION, and a distribution can expose many
    endpoints. Both halves must be reported under the one install."""

    files = {
        **_bound_files("duopack", module="duo_a"),
        # duo_b ships no manifest of its own; the one in duo_a is owned by the
        # same RECORD but not by duo_b's package.
        "duo_b/__init__.py": "",
        "duo_b/ext.py": EXT_PY.format(name="duo_b"),
    }
    runner = _InstallingRunner(
        trusted_site,
        "duopack",
        files=files,
        entry_points={"duo-a": "duo_a.ext:setup", "duo-b": "duo_b.ext:setup"},
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out

    assert code == EI._INSTALL_NOT_BOUND
    # BOTH endpoints of the one dist were considered — the count proves it.
    assert "1 of 2 endpoint(s) will NOT bind" in out
    assert "duo-b" in out
    # The healthy half is acknowledged rather than buried.
    assert "1 endpoint(s) of this install DID bind" in out
    # One remove line for the one distribution, not one per endpoint.
    assert out.count("aelix extension remove duopack") == 1


# === a pre-existing failure is NOT blamed on the new pack =================


def test_preexisting_broken_endpoint_is_not_blamed_on_the_new_pack(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_dist(
        trusted_site,
        "oldbroken",
        entry_points={"aelix.extensions": {"oldbroken": "oldbroken.ext:setup"}},
        files=_future_files("oldbroken"),
    )
    # NOT vacuous: the environment really does hold a non-bound endpoint that a
    # whole-environment report would have printed.
    preexisting = [
        s for s in EI.classify_installed_endpoints() if s.dist_name == "oldbroken"
    ]
    assert len(preexisting) == 1 and not preexisting[0].bound

    runner = _InstallingRunner(
        trusted_site,
        "newpack",
        files=_bound_files("newpack"),
        entry_points={"newpack": "newpack.ext:setup"},
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert RESTART_LINE in out
    assert "oldbroken" not in out
    assert "will NOT bind" not in out


def test_git_target_never_guesses_a_distribution_name(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repository name is not a distribution name. A git install that lands
    nothing new must not attribute an unrelated installed pack to itself.

    ``r`` here is named exactly like the repository AND is unbindable, so a build
    that guessed would print a verdict about a pack this command never touched.
    It carries no ``direct_url.json``, so pip's own record does not claim it
    either — leaving no verdict, which is what gets printed.
    """

    install_dist(
        trusted_site,
        "r",
        entry_points={"aelix.extensions": {"r": "r.ext:setup"}},
        files=_future_files("r"),
    )
    code = run_extension_command(
        ["install", "git+https://h/r.git", "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "will NOT bind" not in out  # ← the bystander is not blamed
    assert "API_LEVEL" not in out
    # …and the silence is stated, not dressed up as success.
    assert EI._INSTALLED_NO_VERDICT in out
    assert RESTART_LINE not in out


# === the reinstall case the ledger diff alone cannot see ==================


def test_reinstall_of_an_unchanged_pack_still_reports(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """pip is a no-op on "Requirement already satisfied", so nothing appears and
    no version moves. Attribution falls back to the name the TARGET carries, so
    the second install of a pack that cannot bind is not silently blessed."""

    install_dist(
        trusted_site,
        "futurepack",
        entry_points={"aelix.extensions": {"futurepack": "futurepack.ext:setup"}},
        files=_future_files("futurepack"),
    )
    code = run_extension_command(
        ["install", "futurepack", "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == EI._INSTALL_NOT_BOUND
    assert "requires API_LEVEL >= 7" in out
    assert RESTART_LINE not in out


# === the two targets that name NO distribution (round 2) ==================
#
# The name arm above closes the reinstall case only when the target names itself.
# Two very ordinary targets do not: a git URL (a repo name is not a dist name),
# and a source tree whose name cannot be read without executing it — setup.py
# only, a ``[tool.poetry]`` name, a ``dynamic`` name. Both were measured live on
# the round-1 build printing "Installed. Restart aelix" and exiting 0 for a pack
# ``verify`` refused at the same instant. What answers them is not a better
# guess: it is pip's OWN PEP 610 record of where each dist came from.


def test_repeat_install_of_a_tree_that_cannot_name_itself_still_reports(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """setup.py-only tree, installed twice. MEASURED shape, not invented."""

    tree = tmp_path / "legacytree"
    tree.mkdir()
    (tree / "setup.py").write_text("from setuptools import setup\n\nsetup()\n")
    # The gap itself, pinned: nothing in the tree DECLARES the name, so the hint
    # arm is silent and the ledger diff sees nothing across a no-op reinstall.
    assert EI._target_dist_hint(str(tree), "path") is None

    dist_info = install_dist(
        trusted_site,
        "legacypack",
        entry_points={"aelix.extensions": {"legacypack": "legacypack.ext:setup"}},
        files=_future_files("legacypack"),
    )
    _record_direct_url(dist_info, _uri(tree), dir_info={})

    code = run_extension_command(
        ["install", str(tree), "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == EI._INSTALL_NOT_BOUND
    assert "requires API_LEVEL >= 7" in out
    assert RESTART_LINE not in out
    assert EI._INSTALLED_NO_VERDICT not in out


def test_repeat_git_install_reports_via_pips_own_record(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A git target still names no distribution — but pip wrote down which dist
    it produced, and reading that is a lookup, not a guess.

    The requested revision is part of the target and NOT part of pip's record, so
    matching has to strip it; ``@main`` here is what proves that.
    """

    dist_info = install_dist(
        trusted_site,
        "gitpack",
        entry_points={"aelix.extensions": {"gitpack": "gitpack.ext:setup"}},
        files=_future_files("gitpack"),
    )
    _record_direct_url(
        dist_info,
        "https://h/gitpack.git",
        vcs_info={"vcs": "git", "commit_id": "a" * 40},
    )

    code = run_extension_command(
        ["install", "git+https://h/gitpack.git@main", "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == EI._INSTALL_NOT_BOUND
    assert "requires API_LEVEL >= 7" in out
    assert RESTART_LINE not in out


def test_a_dist_recorded_from_another_url_is_never_attributed(
    trusted_site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The record is matched EXACTLY. A near-miss attributes nothing rather than
    blaming a pack that came from somewhere else."""

    dist_info = install_dist(
        trusted_site,
        "otherpack",
        entry_points={"aelix.extensions": {"otherpack": "otherpack.ext:setup"}},
        files=_future_files("otherpack"),
    )
    _record_direct_url(dist_info, "https://h/otherpack.git")

    code = run_extension_command(
        ["install", "git+https://h/gitpack.git", "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "otherpack" not in out
    assert "will NOT bind" not in out
    assert EI._INSTALLED_NO_VERDICT in out


def test_repeat_install_of_a_healthy_pack_is_byte_identical(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The floor must not fire when attribution SUCCEEDS.

    Same unnameable tree as the legacy case, but the pack binds: the reinstall
    has to reach the real verdict and print the pre-#154 line byte for byte —
    otherwise "no new chrome for a healthy install" would hold only on the first
    install and quietly break on every one after it.
    """

    tree = tmp_path / "goodtree"
    tree.mkdir()
    (tree / "setup.py").write_text("from setuptools import setup\n\nsetup()\n")
    assert EI._target_dist_hint(str(tree), "path") is None

    dist_info = install_dist(
        trusted_site,
        "goodpack",
        entry_points={"aelix.extensions": {"goodpack": "goodpack.ext:setup"}},
        files=_bound_files("goodpack"),
    )
    _record_direct_url(dist_info, _uri(tree), dir_info={})

    code = run_extension_command(
        ["install", str(tree), "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert RESTART_LINE in out
    assert EI._INSTALLED_NO_VERDICT not in out
    for noise in ("BOUND", "will NOT bind", "nothing was undone", "extension remove"):
        assert noise not in out


def test_a_moved_ledger_wins_over_a_stale_pep610_record(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Why the record is a LAST resort and not a fourth union member.

    A record outlives the build that wrote it: rename the distribution your tree
    produces and the superseded dist still claims that directory. When something
    actually moved, that movement is the better answer — so the stale claimant
    is not reported and its unbindable manifest is not blamed on this install.
    """

    tree = tmp_path / "renamed"
    tree.mkdir()
    (tree / "setup.py").write_text("from setuptools import setup\n\nsetup()\n")
    stale = install_dist(
        trusted_site,
        "oldname",
        entry_points={"aelix.extensions": {"oldname": "oldname.ext:setup"}},
        files=_future_files("oldname"),
    )
    _record_direct_url(stale, _uri(tree), dir_info={})

    runner = _InstallingRunner(
        trusted_site,
        "newname",
        files=_bound_files("newname"),
        entry_points={"newname": "newname.ext:setup"},
    )
    code = run_extension_command(
        ["install", str(tree), "--yes", "--no-verify"], settings=_mem(), runner=runner
    )
    out = capsys.readouterr().out
    assert code == 0
    assert RESTART_LINE in out
    assert "oldname" not in out
    assert "will NOT bind" not in out


# === --trust-extension-path ==============================================


def test_untrusted_install_root_is_reported(
    untrusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _InstallingRunner(
        untrusted_site,
        "devpack",
        files=_bound_files("devpack"),
        entry_points={"devpack": "devpack.ext:setup"},
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out
    assert code == EI._INSTALL_NOT_BOUND
    assert "UNTRUSTED_PATH" in out
    assert "--trust-extension-path devpack" in out


def test_trust_extension_path_makes_the_same_install_bind(
    untrusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The developer vouch must mean on ``install`` exactly what it means on
    ``verify`` — otherwise install would keep calling a merely-unvouched pack
    broken, on every reinstall, forever."""

    runner = _InstallingRunner(
        untrusted_site,
        "devpack",
        files=_bound_files("devpack"),
        entry_points={"devpack": "devpack.ext:setup"},
    )
    code = run_extension_command(
        [
            "install",
            _src(tmp_path),
            "--yes",
            "--no-verify",
            "--trust-extension-path",
            "devpack",
        ],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert RESTART_LINE in out
    assert "UNTRUSTED_PATH" not in out
    # The vouch is a CLASSIFICATION input; it must never reach pip's argv.
    assert all("--trust-extension-path" not in arg for arg in runner.calls[0])


@pytest.mark.parametrize("spelling", ["--trust-extension-path", "--trust-extension-path="])
def test_trust_extension_path_requires_a_value(
    spelling: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Bare flag at the end (no value token) and the ``=``-joined form with an
    # empty value are two different parser branches; both must refuse.
    assert (
        run_extension_command(
            ["install", "pkg", spelling], settings=_mem(), runner=_NoopRunner()
        )
        == 2
    )
    assert "requires a DIST name" in capsys.readouterr().err


# === discover install shares the verdict by construction ==================


def test_discover_install_reports_the_same_verdict_and_code(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _src(tmp_path, "catsrc")
    extension_catalog.save_catalogs(
        [
            extension_catalog.Catalog(
                location="file:///corp",
                name="corp",
                entries=(
                    extension_catalog.CatalogEntry(
                        name="future-ext", source=source, catalog_name="corp"
                    ),
                ),
                fetched_at=extension_catalog.now_iso(),
            )
        ],
        extension_catalog.cache_file_path(str(tmp_path / "agent")),
    )
    runner = _InstallingRunner(
        trusted_site,
        "futurepack",
        files=_future_files("futurepack"),
        entry_points={"futurepack": "futurepack.ext:setup"},
    )
    code = run_extension_command(
        ["discover", "install", "future-ext", "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    out = capsys.readouterr().out
    assert code == EI._INSTALL_NOT_BOUND
    assert "requires API_LEVEL >= 7" in out
    assert RESTART_LINE not in out


# === the install's own failure modes are untouched ========================


def test_failed_install_keeps_its_own_exit_code_and_prints_no_verdict(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _InstallingRunner(
        trusted_site,
        "futurepack",
        files=_future_files("futurepack"),
        entry_points={"futurepack": "futurepack.ext:setup"},
        returncode=1,
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    captured = capsys.readouterr()
    # "the installer ran and FAILED", NOT the new not-bound code — the split of
    # ADR-0185 survives (0 / 1 / 2), with 3 added beside it.
    assert code == EI._INSTALLER_FAILED
    assert EI._INSTALL_NOT_BOUND != EI._INSTALLER_FAILED
    assert "will NOT bind" not in captured.out
    assert RESTART_LINE not in captured.out
    assert EI._INSTALLED_NO_VERDICT not in captured.out


@pytest.mark.parametrize("pip_code", [3, 2, 7, 23])
def test_installer_failure_never_returns_pips_own_colliding_code(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], pip_code: int
) -> None:
    """pip's exit codes are NOT disjoint from this verb's, so they cannot be
    passed through.

    ``pip/_internal/cli/status_codes.py``: ``VIRTUALENV_NOT_FOUND = 3`` — raised
    under ``--require-venv`` / ``PIP_REQUIRE_VIRTUALENV``, which orgs set
    globally — is exactly ``_INSTALL_NOT_BOUND``, and ``UNKNOWN_ERROR = 2`` is
    exactly ``_EXIT_DIDNT_RUN``. Passing them through made a script reading 3
    unable to tell "installed but inert" (something IS on disk) from "pip refused
    to run" (nothing is). The raw code is still SHOWN, so nothing is lost to a
    human reading the failure.
    """

    runner = _InstallingRunner(
        trusted_site,
        "futurepack",
        files=_future_files("futurepack"),
        entry_points={"futurepack": "futurepack.ext:setup"},
        returncode=pip_code,
    )
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=runner,
    )
    captured = capsys.readouterr()
    assert code == EI._INSTALLER_FAILED == 1
    assert code not in (EI._INSTALL_NOT_BOUND, EI._EXIT_DIDNT_RUN)
    assert f"(exit {pip_code})" in captured.err


def test_a_classification_fault_degrades_to_the_no_verdict_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The package IS on disk by the time the verdict is computable. A fault in
    computing it must never turn a completed install into a crash — and must not
    print a success line either, or stdout would promise the pack loads while
    stderr says the check never ran."""

    def boom(**_kw: object) -> list[EI.EndpointStatus]:
        raise RuntimeError("ledger exploded")

    # A dist really does appear across the install, so attribution is NON-EMPTY
    # and the classifier is genuinely reached. (Returning the same map twice made
    # this test vacuous: attribution was empty and ``boom`` never ran.)
    snapshots = iter([{}, {"anything": "1.0"}])
    monkeypatch.setattr(EI, "classify_installed_endpoints", boom)
    monkeypatch.setattr(EI, "_installed_ext_dists", lambda: next(snapshots))
    code = run_extension_command(
        ["install", _src(tmp_path), "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    captured = capsys.readouterr()
    assert code == 0
    assert EI._INSTALLED_NO_VERDICT in captured.out
    assert RESTART_LINE not in captured.out
    assert "ledger exploded" in captured.err


# === `update` owes the same verdict `install` owes ========================


def _path_sources(*rows: tuple[Path, str]) -> SettingsManager:
    """A settings store holding one recorded ``path`` source per row."""

    return SettingsManager.in_memory(
        {
            "extensionSources": [
                {"spec": str(tree), "kind": "path", "name": name} for tree, name in rows
            ]
        }
    )


def _unnameable_tree(tmp_path: Path, name: str) -> Path:
    """A setup.py-only tree: nothing in it DECLARES a distribution name.

    So attribution cannot take the hint arm and must reach pip's PEP 610 record —
    the same shape the real setuptools-default pack has.
    """

    tree = tmp_path / name
    tree.mkdir()
    (tree / "setup.py").write_text("from setuptools import setup\n\nsetup()\n")
    assert EI._target_dist_hint(str(tree), "path") is None
    return tree


def test_update_reports_the_verdict_instead_of_promising_a_restart(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect #154 exists to kill, on the sibling verb.

    ``update`` reinstalls through ``install_extension``, which announced an
    unconditional "Installed. Restart aelix" — so the build that had just taught
    ``install`` to tell the truth still had ``update`` promising a restart for a
    pack it KNEW could not bind. MEASURED live before this fix: ``extension update
    legacypack --yes`` → "Installed. Restart aelix …", exit 0, while ``extension
    verify legacypack`` at the same instant → "1 of 1 endpoint(s) NOT bound",
    exit 1.
    """

    tree = _unnameable_tree(tmp_path, "badtree")
    dist_info = install_dist(
        trusted_site,
        "futurepack",
        entry_points={"aelix.extensions": {"futurepack": "futurepack.ext:setup"}},
        files=_future_files("futurepack"),
    )
    _record_direct_url(dist_info, _uri(tree), dir_info={})

    code = run_extension_command(
        ["update", "futurepack", "--yes", "--no-verify"],
        settings=_path_sources((tree, "futurepack")),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    # Same fact as install ⇒ same code. "updated but now inert" is "installed and
    # inert"; a verb-dependent code would reintroduce the ambiguity #154 removed.
    assert code == EI._INSTALL_NOT_BOUND
    assert RESTART_LINE not in out
    # The REASON, not just the shape — a fixture that regresses into a schema
    # error would otherwise pass while proving nothing (see this module's header).
    assert "requires API_LEVEL >= 7" in out
    assert "aelix extension verify futurepack" in out


def test_update_of_a_healthy_pack_is_byte_identical(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single healthy pack gains NO new output from the update verdict.

    Same bar the install path had to clear: the compatible path stays exactly what
    it printed before #154 reached this verb — one line, no summary, no chrome.
    """

    tree = _unnameable_tree(tmp_path, "goodtree")
    dist_info = install_dist(
        trusted_site,
        "goodpack",
        entry_points={"aelix.extensions": {"goodpack": "goodpack.ext:setup"}},
        files=_bound_files("goodpack"),
    )
    _record_direct_url(dist_info, _uri(tree), dir_info={})

    code = run_extension_command(
        ["update", "goodpack", "--yes", "--no-verify"],
        settings=_path_sources((tree, "goodpack")),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert RESTART_LINE in out
    assert EI._INSTALLED_NO_VERDICT not in out
    for noise in ("BOUND", "will NOT bind", "update summary", "nothing was undone"):
        assert noise not in out


def test_update_with_nothing_to_attribute_says_it_has_no_verdict(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The floor fires on ``update`` too — and still exits 0.

    Nothing is recorded as coming from this tree, so there is no distribution to
    classify. That is not a success and must not print one; it is also not a
    failure, so the code stays 0 (see :data:`_INSTALLED_NO_VERDICT`).
    """

    tree = _unnameable_tree(tmp_path, "mystery")
    code = run_extension_command(
        ["update", "mysterypack", "--yes", "--no-verify"],
        settings=_path_sources((tree, "mysterypack")),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert EI._INSTALLED_NO_VERDICT in out
    assert RESTART_LINE not in out


def test_bare_update_ends_with_an_aggregate_verdict(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare ``update`` walks every source; the OUTCOME must survive the scroll.

    Each pack now prints install's full report, and for a pack that cannot bind
    that is a multi-line block ending in the ABSENT packaging hint. Three of those
    and the last thing on screen is one arbitrary pack's remove command. So the
    run closes by naming what happened to all of them.
    """

    good = _unnameable_tree(tmp_path, "goodtree")
    bad = _unnameable_tree(tmp_path, "badtree")
    mystery = _unnameable_tree(tmp_path, "mystery")
    gi = install_dist(
        trusted_site,
        "goodpack",
        entry_points={"aelix.extensions": {"goodpack": "goodpack.ext:setup"}},
        files=_bound_files("goodpack"),
    )
    _record_direct_url(gi, _uri(good), dir_info={})
    bi = install_dist(
        trusted_site,
        "futurepack",
        entry_points={"aelix.extensions": {"futurepack": "futurepack.ext:setup"}},
        files=_future_files("futurepack"),
    )
    _record_direct_url(bi, _uri(bad), dir_info={})

    code = run_extension_command(
        ["update", "--yes", "--no-verify"],
        settings=_path_sources(
            (good, "goodpack"), (bad, "futurepack"), (mystery, "mysterypack")
        ),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == EI._INSTALL_NOT_BOUND
    assert (
        "update summary: 3 pack(s) — 1 bound, 1 NOT bound, 1 no verdict, 0 failed."
        in out
    )
    assert "NOT bound: futurepack" in out
    assert "no verdict: mysterypack" in out
    # …and it really is the LAST thing read, which is the whole point.
    assert out.rstrip().endswith("To re-check one: aelix extension verify <name>")


def test_bare_update_of_healthy_packs_adds_no_summary(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The compatible path stays silent even across MANY packs.

    The summary is bad-news-only: an all-bound run prints exactly one
    :data:`RESTART_LINE` per pack and nothing else, byte-identical to what this
    verb printed before #154 touched it.
    """

    trees = []
    for name in ("packa", "packb"):
        tree = _unnameable_tree(tmp_path, f"{name}tree")
        dist_info = install_dist(
            trusted_site,
            name,
            entry_points={"aelix.extensions": {name: f"{name}.ext:setup"}},
            files=_bound_files(name),
        )
        _record_direct_url(dist_info, _uri(tree), dir_info={})
        trees.append((tree, name))

    code = run_extension_command(
        ["update", "--yes", "--no-verify"],
        settings=_path_sources(*trees),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert out.count(RESTART_LINE) == 2
    assert "update summary" not in out


def test_a_hard_failure_outranks_an_inert_pack_in_the_aggregate() -> None:
    """3 is the ONE code that asserts the installer succeeded.

    Under the old "first nonzero wins" fold, an early inert pack followed by a
    pack pip failed on outright would have exited 3 — telling a script everything
    landed when something did not. That rule was complete only while every code
    ``update`` could see meant "something went wrong"; 3 breaks it.
    """

    # 3 then a real failure ⇒ the failure wins, in both orders.
    assert EI._worse_update_code(EI._INSTALL_NOT_BOUND, EI._INSTALLER_FAILED) == (
        EI._INSTALLER_FAILED
    )
    assert EI._worse_update_code(EI._INSTALL_NOT_BOUND, EI._EXIT_DIDNT_RUN) == (
        EI._EXIT_DIDNT_RUN
    )
    assert EI._worse_update_code(EI._INSTALLER_FAILED, EI._INSTALL_NOT_BOUND) == (
        EI._INSTALLER_FAILED
    )
    # Among genuine failures the original rule stands: the earliest one wins.
    assert EI._worse_update_code(EI._INSTALLER_FAILED, EI._EXIT_DIDNT_RUN) == (
        EI._INSTALLER_FAILED
    )
    # 0 never displaces anything, and 3 survives a clean neighbour.
    assert EI._worse_update_code(0, EI._INSTALL_NOT_BOUND) == EI._INSTALL_NOT_BOUND
    assert EI._worse_update_code(EI._INSTALL_NOT_BOUND, 0) == EI._INSTALL_NOT_BOUND
    assert EI._worse_update_code(0, 0) == 0


# === attribution unit coverage ===========================================


def test_target_dist_hint_reads_a_source_tree_pyproject(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "pyproject.toml").write_text(
        '[project]\nname = "My_Pack"\nversion = "1.0"\n', encoding="utf-8"
    )
    assert EI._target_dist_hint(str(tree), "path") == "My_Pack"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("futurepack-1.0.0-py3-none-any.whl", "futurepack"),
        ("futurepack-1.0.0.tar.gz", "futurepack"),
    ],
)
def test_target_dist_hint_reads_an_artifact_filename(
    tmp_path: Path, filename: str, expected: str
) -> None:
    assert EI._target_dist_hint(str(tmp_path / filename), "path") == expected


def test_target_dist_hint_is_silent_for_git() -> None:
    assert EI._target_dist_hint("git+https://h/futurepack.git", "git") is None


def test_attributed_dists_covers_new_upgraded_and_named() -> None:
    before = {"kept": "1.0", "moved": "1.0"}
    after = {"kept": "1.0", "moved": "2.0", "fresh": "1.0"}
    got = EI._attributed_dists("kept", "pypi", before, after)
    # fresh = appeared, moved = version changed, kept = the target names it.
    assert got == frozenset({"fresh", "moved", "kept"})


@pytest.mark.parametrize(
    ("target", "kind", "expected"),
    [
        # The requested revision is part of the TARGET, never of pip's record.
        ("git+https://h/r.git@v1", "git", "https://h/r.git"),
        ("git+https://h/r.git@" + "b" * 40, "git", "https://h/r.git"),
        ("git+https://h/r.git#egg=r", "git", "https://h/r.git"),
        ("git+https://h/r.git", "git", "https://h/r.git"),
        # …but an ``@`` in the AUTHORITY is not a revision. Stripping it would
        # turn every ssh clone into a different URL and lose the match.
        ("git+ssh://git@h/r.git", "git", "ssh://git@h/r.git"),
        ("git+ssh://git@h/r.git@v1", "git", "ssh://git@h/r.git"),
        # A ``file:`` URL is compared as a PATH: the two sides are written by
        # different code and need not agree on percent-encoding or a trailing /.
        ("git+file:///a/b", "git", "/a/b"),
        ("git+file:///a/b/", "git", "/a/b"),
        ("git+file:///a/b%20c", "git", "/a/b c"),
        # pypi has no PEP 610 record at all — and its bare name IS the dist name.
        ("somepack==1.0", "pypi", None),
    ],
)
def test_target_source_key_normalisation(
    target: str, kind: EI.TargetKind, expected: str | None
) -> None:
    assert EI._target_source_key(target, kind) == expected


def test_target_source_key_of_a_directory_is_its_resolved_path(tmp_path: Path) -> None:
    tree = tmp_path / "t"
    tree.mkdir()
    assert EI._target_source_key(str(tree), "path") == str(tree.resolve())
    # …and the recorded file: URL for the same directory reduces to the same key.
    assert EI._url_fs_key(_uri(tree)) == str(tree.resolve())


def test_target_source_key_resolves_symlinks(tmp_path: Path) -> None:
    """A link and its target are the SAME source, and must key the same.

    ``_target_source_key`` normalised with ``abspath``, which does not resolve
    links, while :func:`_install_spec` hands pip ``Path.resolve()``'d — so pip
    recorded the realpath and the two keys could never match. Every spelling of
    the link below must reduce to the target's real path.
    """

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    want = str(real.resolve())

    assert EI._target_source_key(str(real), "path") == want
    for spelling in (
        str(link),  # the link itself
        str(link) + "/",  # …with a trailing slash
        "link",  # …relative (the fixture chdirs to tmp_path)
        str(link / ".." / "real"),  # …and '..' traversed THROUGH the link
    ):
        assert EI._target_source_key(spelling, "path") == want, spelling
    # Both sides of the PEP 610 comparison: the recorded file: URL and a
    # ``git+file://`` target spelled through the link reduce to the same key too.
    assert EI._url_fs_key(link.as_uri()) == want
    assert EI._target_source_key("git+" + link.as_uri(), "git") == want


def test_a_symlinked_target_keeps_a_healthy_packs_clean_line(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A HEALTHY pack installed through a link must not lose its verdict.

    The end-to-end cost of the key mismatch above, and a REGRESSION on the
    compatible path: attribution fell through to the no-verdict floor, so a pack
    that binds perfectly was told "this build has no binding verdict for it" on
    every repeat install. It failed safe — never a false success — but a healthy
    pack has to keep the pre-#154 line byte for byte.
    """

    real = _unnameable_tree(tmp_path, "realtree")
    link = tmp_path / "linktree"
    link.symlink_to(real, target_is_directory=True)

    dist_info = install_dist(
        trusted_site,
        "goodpack",
        entry_points={"aelix.extensions": {"goodpack": "goodpack.ext:setup"}},
        files=_bound_files("goodpack"),
    )
    # What pip really records: _install_spec resolves the link BEFORE pip ever
    # sees the target, so the record names the real directory, not the link.
    assert EI._install_spec(str(link), "path") == str(real.resolve())
    _record_direct_url(dist_info, Path(EI._install_spec(str(link), "path")).as_uri(), dir_info={})

    code = run_extension_command(
        ["install", str(link), "--yes", "--no-verify"],
        settings=_mem(),
        runner=_NoopRunner(),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert RESTART_LINE in out
    assert EI._INSTALLED_NO_VERDICT not in out


def test_recorded_source_key_survives_a_broken_record(tmp_path: Path) -> None:
    """A dist with no / unreadable / nameless ``direct_url.json`` is simply not a
    match — never an exception on a completed install."""

    site = tmp_path / "s"
    di = install_dist(site, "d", entry_points={"aelix.extensions": {"d": "d:setup"}})
    dist = importlib.metadata.Distribution.at(di)
    assert EI._recorded_source_key(dist) is None  # absent
    (di / "direct_url.json").write_text("{not json", encoding="utf-8")
    assert EI._recorded_source_key(dist) is None  # malformed
    (di / "direct_url.json").write_text('{"dir_info": {}}', encoding="utf-8")
    assert EI._recorded_source_key(dist) is None  # no url key


# === the ABSENT hint must point somewhere a user can reach ================


def test_absent_hint_names_paths_that_exist() -> None:
    """#154 newly prints this hint on the INSTALL path, so every user who
    installs a setuptools-default pack reads it — not just someone who ran
    ``verify``. It pointed at ``examples/starter/``, which is not a directory at
    the repo root and does not exist in an installed environment either."""

    repo_root = Path(__file__).resolve().parents[2]
    in_repo = "packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/"
    installed = "aelix_coding_agent/examples/starter/"

    assert in_repo in EI._ABSENT_HINT
    assert (repo_root / in_repo / "pyproject.toml").is_file()

    # The spelling an INSTALLED user has: hatchling ships every file under the
    # package directory, and tests/packaging_gate/test_build_hygiene.py builds the
    # wheel for real — this asserts the same tree the hint names is really there.
    assert installed in EI._ABSENT_HINT
    package_root = Path(EI.__file__).resolve().parents[1]
    assert package_root.name == "aelix_coding_agent"
    assert (package_root / "examples/starter/pyproject.toml").is_file()

    # #101: the hint used to name `docs/guides/extension-authoring.md`, and this
    # assertion used to prove it by reading that file OUT OF THE REPO — the same
    # source-tree check the paragraph above exists to reject. `docs/guides/` is
    # not in the wheel, so the one reader of this message, an installed user
    # whose pack just went inert, was sent to a path they do not have.
    #
    # It now names the `aelix docs extension` verb, which is checked the way the
    # `examples/starter/` half is: by resolving it through the SAME registry the
    # command uses, against the packaged copy rather than the repo's.
    from aelix_coding_agent.help import read_topic, resolve_topic

    assert "`aelix docs extension`" in EI._ABSENT_HINT
    topic = resolve_topic("extension")
    assert topic is not None, "the hint names a docs topic that does not resolve"
    assert topic.path.parent.parent.name == "aelix_coding_agent"
    assert "Packaging your extension" in read_topic(topic)


# === The summary's one actionable line must be one that works ==============


def _summary(results: list[tuple[str, int, frozenset[str]]]) -> str:
    """Render ``_print_update_summary`` for ``results`` and return the output."""

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        EI._print_update_summary(results)
    return buf.getvalue()


def test_every_non_empty_bucket_gets_its_own_detail_line() -> None:
    """A count the summary reports must be traceable to the packs behind it."""

    out = _summary(
        [
            ("goodpack", 0, frozenset({"goodpack"})),
            ("brokenpack", EI._INSTALL_NOT_BOUND, frozenset({"brokenpack"})),
            ("/tmp/packs/plainpack", 0, frozenset()),
            ("flippack", EI._INSTALLER_FAILED, frozenset()),
        ]
    )
    assert "1 bound, 1 NOT bound, 1 no verdict, 1 failed" in out
    assert "NOT bound: brokenpack" in out
    # The bucket the guide's sample used to elide.
    assert "no verdict: /tmp/packs/plainpack" in out
    assert "failed: flippack" in out


def test_the_recheck_hint_is_one_verify_would_actually_accept() -> None:
    """``verify <name>`` is offered only when every listed row carries a name.

    A recorded source whose distribution name was never detected is labelled
    with its PATH (``_source_identity``), and ``aelix extension verify
    /path/to/pack`` matches nothing installed — it exits 2. Printing that as the
    summary's single actionable line would make it useless for exactly the rows
    that need it, so a path-labelled row switches the hint to the argument-less
    form, which settles every endpoint at once.
    """

    named_only = _summary(
        [
            ("goodpack", 0, frozenset({"goodpack"})),
            ("brokenpack", EI._INSTALL_NOT_BOUND, frozenset({"brokenpack"})),
            ("legacypack", EI._INSTALL_NOT_BOUND, frozenset({"legacypack"})),
        ]
    )
    assert "To re-check one: aelix extension verify <name>" in named_only

    with_a_path = _summary(
        [
            ("goodpack", 0, frozenset({"goodpack"})),
            ("brokenpack", EI._INSTALL_NOT_BOUND, frozenset({"brokenpack"})),
            ("/tmp/packs/plainpack", 0, frozenset()),
        ]
    )
    assert "To re-check: aelix extension verify" in with_a_path
    assert "<name>" not in with_a_path


def test_an_all_bound_run_still_prints_no_summary_at_all() -> None:
    """The compatible path stays byte-identical — guards the guard above."""

    assert (
        _summary(
            [
                ("goodpack", 0, frozenset({"goodpack"})),
                ("otherpack", 0, frozenset({"otherpack"})),
            ]
        )
        == ""
    )
