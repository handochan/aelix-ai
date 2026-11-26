"""#926 (ADR-0321): recorded extensions a reinstall removed — list, restore, and the start.

An aelix reinstall (the install line's ``uv tool install --force``, or ``uv tool
upgrade``) rebuilds the tool environment from its receipt and drops every extension
``aelix extension install`` added; the install record (``extensionSources``) and the
pin (``extension_pins.json``) stay. Measured on the issue's machine:
``aelix extension list`` said ``No extensions installed`` with two pins on disk.

These tests run the real record → pin → install → verdict path, with a runner that
lands a genuine installed-wheel image in a throwaway site on ``sys.path``
(``tests/cli/test_extension_install_verdict.py``'s shapes), so a passing restore is
an install the ledger, the attribution and the binding verdict all saw.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import ExtensionSourceObject, SettingsManager
from aelix_coding_agent.cli import extension_install as EI
from aelix_coding_agent.cli import extension_pins, extension_restore
from aelix_coding_agent.cli.extension_install import run_extension_command
from aelix_coding_agent.cli.extension_restore import missing_recorded_extensions, restore_at_start

from tests.cli.test_extension_install_verdict import (
    _bound_files,
    _drop_modules,
    _InstallingRunner,
    _record_direct_url,
    _scoped_entry_points,
)
from tests.extensions.test_ep_manifest import install_dist
from tests.process_tree.test_process_tree_real_processes import strays as _strays_fixture

_SHA = "c36703d3" + "0" * 32
_OTHER = "5fcb5878" + "1" * 32
_REPO = "git+https://github.com/handochan/restore-ext-926.git"

#: The real-process cleanup fixture, re-exported (the F9 case spawns a tree).
strays = _strays_fixture


@pytest.fixture
def trusted_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway site-packages on ``sys.path``, trusted by the provenance fence
    (``tests/cli/test_extension_install_verdict.py``'s fixture of the same name)."""

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


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.delenv("AELIX_SUBAGENT_DEPTH", raising=False)
    monkeypatch.chdir(tmp_path)


def _agent(tmp_path: Path) -> Path:
    return tmp_path / "agent"


def _pin(tmp_path: Path, *pins: extension_pins.Pin) -> None:
    extension_pins.save_pins(
        {p.identity: p for p in pins}, extension_pins.pins_file_path(_agent(tmp_path))
    )


def _git_pin(sha: str = _SHA) -> extension_pins.Pin:
    return extension_pins.Pin(identity=_REPO, kind="git", mode="tofi", git_sha=sha)


def _mem(*sources: ExtensionSourceObject) -> SettingsManager:
    mem = SettingsManager.in_memory()
    mem.set_extension_sources(list(sources))
    return mem


def _git_record(sha: str = _SHA, name: str | None = "restore-ext-926") -> ExtensionSourceObject:
    return ExtensionSourceObject(spec=f"{_REPO}@{sha}", kind="git", name=name)


def _runner(site: Path, dist: str = "restore-ext-926", **kw: Any) -> _InstallingRunner:
    mod = dist.replace("-", "_")
    files = _bound_files(dist, module=mod)
    return _InstallingRunner(site, mod, files=files, entry_points={dist: f"{mod}.ext:setup"}, **kw)


# === which records are missing, and which can come back at a pin ============


def test_a_git_record_at_its_pinned_commit_is_restorable(
    trusted_site: Path, tmp_path: Path
) -> None:
    _pin(tmp_path, _git_pin())
    [rec] = missing_recorded_extensions([_git_record()])
    assert rec.label == "restore-ext-926"
    assert str(rec.target) == f"{_REPO}@{_SHA}"
    assert rec.detail == "git https://github.com/handochan/restore-ext-926.git@c36703d30000"
    assert rec.problem is None


@pytest.mark.parametrize(
    ("record", "pins", "problem"),
    [
        (
            ExtensionSourceObject(spec=f"{_REPO}@main", kind="git", name="restore-ext-926"),
            [_git_pin()],
            "recorded at a branch or tag",
        ),
        (_git_record(), [], "no pin records its commit"),
        (
            _git_record(),
            [extension_pins.Pin(identity=_REPO, kind="git", mode="tofi")],
            "no pin records its commit",
        ),
    ],
)
def test_a_git_record_without_its_pin_is_listed_but_not_restorable(
    trusted_site: Path,
    tmp_path: Path,
    record: ExtensionSourceObject,
    pins: list[extension_pins.Pin],
    problem: str,
) -> None:
    _pin(tmp_path, *pins)
    [rec] = missing_recorded_extensions([record])
    assert rec.target is None and not rec.stale
    assert problem in (rec.problem or "")
    assert rec.command == f"aelix extension install {record.spec}"


def test_a_git_record_whose_pin_holds_another_commit_is_not_its_latest_install(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r3 F1: the pin moves only with an install of another commit, so that
    commit is the later install. Restore offered the recorded (older) commit with
    `--repin`, and following it moved the pin back. Now nothing restores it, with or
    without `--repin`, and the command printed is the pinned commit's."""

    _pin(tmp_path, _git_pin(_SHA))
    runner = _runner(trusted_site)
    mem = _mem(_git_record(_OTHER))
    for extra in (["--repin"], []):
        code = run_extension_command(
            ["restore", "restore-ext-926", "--yes", *extra], settings=mem, runner=runner
        )
        assert code == 2
        out = capsys.readouterr().out
        assert out.splitlines() == [
            "Not restoring restore-ext-926 (git https://github.com/handochan/restore-ext-926.git@"
            "5fcb58781111): it is not the latest install (its pin holds c36703d30000, a commit "
            "installed after this record).",
            f"  Install the latest with: aelix extension install {_REPO}@{_SHA}",
        ]
    assert runner.calls == []
    pins = extension_pins.load_pins(extension_pins.pins_file_path(_agent(tmp_path)))
    assert pins[_REPO].git_sha == _SHA
    [rec] = missing_recorded_extensions([_git_record(_OTHER)])
    assert rec.target is None and rec.stale
    assert rec.problem == "its pin holds c36703d30000, a commit installed after this record"
    assert rec.command == f"aelix extension install {_REPO}@{_SHA}"


def test_the_install_command_printed_for_a_moved_pin_runs(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """It installs the pinned commit, so the gate passes without `--repin`."""

    _pin(tmp_path, _git_pin(_SHA))
    [rec] = missing_recorded_extensions([_git_record(_OTHER)])
    runner = _runner(trusted_site)
    argv = rec.command.split()[2:] + ["--yes"]
    assert run_extension_command(argv, settings=_mem(), runner=runner) == 0, capsys.readouterr()
    assert runner.calls[0][-1] == f"{_REPO}@{_SHA}"


def test_an_installed_record_is_not_missing(trusted_site: Path, tmp_path: Path) -> None:
    _pin(tmp_path, _git_pin())
    by_name = install_dist(
        trusted_site, "restore_ext_926", entry_points={"aelix.extensions": {"r": "x:setup"}}
    )
    assert missing_recorded_extensions([_git_record()]) == []
    # Codex r2 M5: by its name, never by the source another dist came from. The key
    # read from direct_url.json was its url, without the subdirectory field, so a
    # sibling of one commit read as this one, and `restore` said it was installed.
    for item in by_name.iterdir():
        item.unlink()
    by_name.rmdir()
    info = install_dist(
        trusted_site, "other_name", entry_points={"aelix.extensions": {"o": "x:setup"}}
    )
    _record_direct_url(
        info,
        "https://github.com/handochan/restore-ext-926.git",
        vcs_info={"vcs": "git", "commit_id": _SHA},
    )
    assert [r.label for r in missing_recorded_extensions([_git_record()])] == ["restore-ext-926"]


def test_a_pin_without_a_record_is_not_restored(trusted_site: Path, tmp_path: Path) -> None:
    """``extension remove`` drops the record and keeps the pin, and strict mode
    provisions pins for sources never installed (ADR-0187): neither may come back."""

    _pin(tmp_path, _git_pin())
    assert missing_recorded_extensions([]) == []


def test_a_repository_installed_at_two_commits_is_listed_once_with_the_later_one(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r3 F1/F2: what aelix 0.1.0 and 0.1.0b2 wrote for `install A` then
    `install B --repin` — the second record has no name — with the pin at B. The test
    this replaces seeded two named records, which no install writes."""

    _pin(tmp_path, _git_pin(_SHA))
    rows = [_git_record(_OTHER), _git_record(_SHA, name=None)]
    runner = _runner(trusted_site)
    assert (
        run_extension_command(["restore", "--all", "--yes"], settings=_mem(*rows), runner=runner)
        == 2
    )
    assert runner.calls == []
    assert run_extension_command(["list"], settings=_mem(*rows)) == 0
    out = capsys.readouterr().out
    assert out.count("restore-ext-926  (git") == 1  # list names it once
    assert (
        f"      not its latest install: a later record installed it from {_REPO}@{_SHA}\n"
        f"      install the latest: aelix extension install {_REPO}@{_SHA}\n"
    ) in out
    assert "--repin" not in out
    [rec] = missing_recorded_extensions(rows)
    assert rec.stale and rec.target is None
    assert rec.problem == f"a later record installed it from {_REPO}@{_SHA}"
    assert rec.command == f"aelix extension install {_REPO}@{_SHA}"


def test_path_and_package_records(trusted_site: Path, tmp_path: Path) -> None:
    wheel = tmp_path / "wheels" / "pathext_926-1.0.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"approved wheel bytes")
    gone = tmp_path / "wheels" / "goneext_926-1.0.0-py3-none-any.whl"
    unpinned = tmp_path / "wheels" / "bare_926-1.0.0-py3-none-any.whl"
    unpinned.write_bytes(b"never pinned")
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity=str(wheel.resolve()),
            kind="path",
            mode="tofi",
            sha256=hashlib.sha256(b"approved wheel bytes").hexdigest(),
        ),
        extension_pins.Pin(
            identity="pkgext-926",
            kind="pypi",
            mode="tofi",
            name="pkgext-926",
            version="2.1.0",
            sha256="ab" * 32,
        ),
        # A version with no sha256 (or a pin of another kind) is not an exact pin.
        extension_pins.Pin(
            identity="halfpin-926", kind="pypi", mode="tofi", name="halfpin-926", version="1.0"
        ),
        extension_pins.Pin(identity="pathkind-926", kind="path", mode="tofi", sha256="cd" * 32),
    )
    records = [
        ExtensionSourceObject(spec=str(wheel), kind="path", name="pathext-926"),
        ExtensionSourceObject(spec=str(gone), kind="path", name="goneext-926"),
        ExtensionSourceObject(spec=str(tmp_path / "wheels"), kind="path", name="dirext-926"),
        ExtensionSourceObject(spec="pkgext-926", kind="pypi", name="pkgext-926"),
        ExtensionSourceObject(spec="loose-926", kind="pypi", name="loose-926"),
        ExtensionSourceObject(spec=str(unpinned), kind="path", name="bare-926"),
        ExtensionSourceObject(spec="halfpin-926", kind="pypi", name="halfpin-926"),
        ExtensionSourceObject(spec="pathkind-926", kind="pypi", name="pathkind-926"),
    ]
    found = {r.label: r for r in missing_recorded_extensions(records, can_verify_pypi=True)}
    assert found["pathext-926"].target is not None
    assert "the file it was installed from is gone" in (found["goneext-926"].problem or "")
    assert "a directory has no single artifact to pin" in (found["dirext-926"].problem or "")
    assert (
        str(found["pkgext-926"].target) == "pkgext-926==2.1.0" and found["pkgext-926"].verify_pypi
    )
    assert "needs --verify-pypi" in (found["loose-926"].problem or "")
    assert "no pin records the file's sha256" in (found["bare-926"].problem or "")
    assert found["halfpin-926"].target is None and found["pathkind-926"].target is None
    # Verify r1 F7: without pip a package pin cannot be checked, so it is not offered.
    nopip = {r.label: r for r in missing_recorded_extensions(records, can_verify_pypi=False)}
    assert nopip["pkgext-926"].target is None
    assert nopip["pkgext-926"].problem == (
        "its pin is a version and a sha256, which only pip can check, and this environment has no pip"
    )
    assert nopip["pkgext-926"].command == (
        f"uv pip install --python {sys.executable} pip, then aelix extension restore pkgext-926"
    )


# === extension list =========================================================


def test_list_names_a_recorded_extension_that_is_not_installed(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue's state: two pins and two records, and ``list`` said only
    ``No extensions installed (via entry_points).``"""

    _pin(tmp_path, _git_pin())
    loose = ExtensionSourceObject(
        spec=f"{_REPO.replace('926', 'b926')}@main", kind="git", name="loose-926"
    )
    assert run_extension_command(["list"], settings=_mem(_git_record(), loose)) == 0
    out = capsys.readouterr().out
    assert "Recorded but not installed" in out
    assert (
        "  restore-ext-926  (git https://github.com/handochan/restore-ext-926.git@c36703d30000)"
        in out
    )
    assert "restore at its pin: aelix extension restore restore-ext-926" in out
    assert "  loose-926  (git https://github.com/handochan/restore-ext-b926.git)" in out
    assert "reinstall: aelix extension install" in out


# === extension restore ======================================================


def test_restore_installs_the_record_at_its_pinned_commit(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pin(tmp_path, _git_pin())
    runner = _runner(trusted_site)
    code = run_extension_command(["restore", "--yes"], settings=_mem(_git_record()), runner=runner)
    out = capsys.readouterr().out
    assert code == 0, out
    assert runner.calls and runner.calls[0][-1] == f"{_REPO}@{_SHA}"
    assert (
        "Restoring restore-ext-926 (git https://github.com/handochan/restore-ext-926.git@c36703d30000)"
        in out
    )
    assert "integrity verified" in out
    # Restored, so neither restore nor list has anything more to say.
    assert (
        run_extension_command(["restore", "--yes"], settings=_mem(_git_record()), runner=runner)
        == 0
    )
    assert "Nothing to restore" in capsys.readouterr().out
    assert len(runner.calls) == 1


def test_restore_refuses_a_file_whose_bytes_changed_without_repin(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = tmp_path / "pathext_926-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"approved")
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity=str(wheel.resolve()),
            kind="path",
            mode="tofi",
            sha256=hashlib.sha256(b"approved").hexdigest(),
        ),
    )
    wheel.write_bytes(b"changed since")
    record = ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name="pathext-926")
    runner = _runner(trusted_site, "pathext-926")
    assert (
        run_extension_command(
            ["restore", "pathext-926", "--yes"], settings=_mem(record), runner=runner
        )
        == 2
    )
    assert "integrity MISMATCH" in capsys.readouterr().err
    assert runner.calls == []
    assert (
        run_extension_command(
            ["restore", "pathext-926", "--yes", "--repin"], settings=_mem(record), runner=runner
        )
        == 0
    )
    assert len(runner.calls) == 1


def test_restore_of_an_unpinned_record_names_the_install_command(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _runner(trusted_site)
    assert (
        run_extension_command(
            ["restore", "--all", "--yes"], settings=_mem(_git_record()), runner=runner
        )
        == 2
    )
    out = capsys.readouterr().out
    assert "Cannot restore restore-ext-926" in out
    assert f"Reinstall it with: aelix extension install {_REPO}@{_SHA}" in out
    assert runner.calls == []


def test_restore_by_name(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pin(tmp_path, _git_pin())
    mem = _mem(_git_record())
    assert run_extension_command(["restore", "nope", "--yes"], settings=mem) == 2
    assert "no recorded extension named 'nope'" in capsys.readouterr().err
    install_dist(trusted_site, "restore_ext_926")
    assert run_extension_command(["restore", "restore-ext-926", "--yes"], settings=mem) == 0
    assert "restore-ext-926 is installed; nothing to restore." in capsys.readouterr().out
    assert run_extension_command(["restore", "restore-ext-926", "--all"], settings=mem) == 2
    assert "--all and a name are exclusive" in capsys.readouterr().err


# === the start (owner 2026-11-26: "receipt 등록 + 시작 시 복원 (권장)") =====


class _ChildRunner:
    """The start-up restore's installer child: lands the dist, or fails."""

    def __init__(self, site: Path, *, fail: bool = False) -> None:
        self.inner = _runner(site)
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if self.fail:
            sys.stdout.write(
                "error: Failed to fetch https://github.com/handochan/restore-ext-926.git\n"
            )
            return subprocess.CompletedProcess(argv, 1)
        return self.inner(argv, cwd=cwd)


def _start(
    app_mode: str,
    site: Path,
    *,
    offline: bool = False,
    tool_env: bool = True,
    fail: bool = False,
    no_extensions: bool = False,
    record: ExtensionSourceObject | None = None,
    records: list[ExtensionSourceObject] | None = None,
) -> _ChildRunner:
    child = _ChildRunner(site, fail=fail)
    restore_at_start(
        app_mode,
        offline=offline,
        no_extensions=no_extensions,
        settings=_mem(*(records or [record or _git_record()])),
        in_tool_env=lambda: tool_env,
        child_runner=child,
    )
    return child


def test_an_interactive_start_restores_at_the_pin_before_extensions_load(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pin(tmp_path, _git_pin())
    child = _start("interactive", trusted_site)
    out, err = capsys.readouterr()
    assert len(child.calls) == 1 and child.calls[0][-1] == f"{_REPO}@{_SHA}"
    assert out.splitlines() == [
        "aelix: restoring restore-ext-926, recorded but no longer installed in this environment...",
        "aelix: restored restore-ext-926 (git https://github.com/handochan/restore-ext-926.git@c36703d30000).",
    ]
    assert err == ""
    assert missing_recorded_extensions([_git_record()]) == []


def test_a_failed_start_up_restore_is_a_line_and_the_start_goes_on(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pin(tmp_path, _git_pin())
    _start("interactive", trusted_site, fail=True)
    out = capsys.readouterr().out
    assert (
        "aelix: could not restore restore-ext-926 (error: Failed to fetch "
        "https://github.com/handochan/restore-ext-926.git); retry with: aelix extension restore restore-ext-926"
    ) in out


@pytest.mark.parametrize("mode", ["print", "json", "rpc"])
def test_a_headless_start_installs_nothing_and_names_the_command(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    _pin(tmp_path, _git_pin())
    child = _start(mode, trusted_site)
    out, err = capsys.readouterr()
    assert child.calls == [] and out == ""
    assert err == (
        "aelix: 1 recorded extension(s) are not installed in this environment "
        "(restore-ext-926); restore them with: aelix extension restore\n"
    )


def test_an_offline_start_skips_with_a_notice(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pin(tmp_path, _git_pin())
    child = _start("interactive", trusted_site, offline=True)
    assert child.calls == []
    out = capsys.readouterr().out
    assert "aelix: offline, so not restoring restore-ext-926 (recorded, not installed)" in out
    assert "run `aelix extension restore` when online." in out


def test_the_offline_environment_name_skips_the_start_restore_too(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin(tmp_path, _git_pin())
    monkeypatch.setenv("AELIX_OFFLINE", "1")
    child = _start("interactive", trusted_site, offline=False)
    assert child.calls == []
    assert "aelix: offline, so not restoring restore-ext-926" in capsys.readouterr().out


_REPO2 = _REPO.replace("restore-ext-926", "dep-ext-926")


class _PackRunner:
    """Lands two extensions on its one call: a pack and an extension it depends on."""

    def __init__(self, site: Path) -> None:
        self.inner = [_runner(site), _runner(site, "dep-ext-926")]
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        for runner in self.inner:
            runner(argv, cwd=cwd)
        return subprocess.CompletedProcess(args=argv, returncode=0)


def _pack_records(tmp_path: Path) -> list[ExtensionSourceObject]:
    _pin(
        tmp_path,
        _git_pin(),
        extension_pins.Pin(identity=_REPO2, kind="git", mode="tofi", git_sha=_SHA),
    )
    dep = ExtensionSourceObject(spec=f"{_REPO2}@{_SHA}", kind="git", name="dep-ext-926")
    return [_git_record(), dep]


def test_restore_skips_a_record_an_earlier_restore_brought_along(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Of the records of one name only the last is used (verify r3 F1), so what an
    earlier restore of this run installed is another record's dependency."""

    runner = _PackRunner(trusted_site)
    mem = _mem(*_pack_records(tmp_path))
    assert run_extension_command(["restore", "--yes"], settings=mem, runner=runner) == 0
    assert [c[-1] for c in runner.calls] == [f"{_REPO}@{_SHA}"]


def test_an_unpinned_record_is_named_at_start_and_not_installed(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    child = _start("interactive", trusted_site)
    assert child.calls == []
    out = capsys.readouterr().out
    assert (
        "aelix: restore-ext-926 is recorded but not installed, and cannot be restored at a pin"
        in out
    )
    assert f"reinstall it with: aelix extension install {_REPO}@{_SHA}" in out


@pytest.mark.parametrize("why", ["not a uv tool env", "--no-extensions", "delegated"])
def test_the_start_does_nothing_outside_its_case(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    why: str,
) -> None:
    _pin(tmp_path, _git_pin())
    if why == "delegated":
        monkeypatch.setenv("AELIX_SUBAGENT_DEPTH", "1")
    child = _start(
        "interactive",
        trusted_site,
        tool_env=why != "not a uv tool env",
        no_extensions=why == "--no-extensions",
    )
    assert child.calls == []
    assert capsys.readouterr() == ("", "")


def test_the_start_never_raises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> list[object]:
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(extension_restore, "missing_recorded_extensions", boom)
    restore_at_start(
        "interactive", offline=False, no_extensions=False, settings=_mem(), in_tool_env=lambda: True
    )
    assert "could not check for recorded extensions (settings exploded)" in capsys.readouterr().err


# === the pieces around them ===================================================


def test_list_and_the_start_read_the_settings_without_writing_them(tmp_path: Path) -> None:
    """``SettingsManager.create`` holds an exclusive ``flock`` on an existing
    ``settings.json`` while it loads it (POSIX), and on a missing one reads nothing and
    creates nothing; ``list`` and the start take no lock and write nothing."""

    settings_file = tmp_path / "settings.json"
    assert extension_restore.read_only_settings().get_extension_sources() == []
    assert not settings_file.exists()
    settings_file.write_text(
        '{"extensionSources": [{"spec": "'
        + f"{_REPO}@{_SHA}"
        + '", "kind": "git", "name": "x-926"}]}',
        encoding="utf-8",
    )
    before = settings_file.read_bytes()
    [source] = extension_restore.read_only_settings().get_extension_sources()
    assert (source.spec, source.kind, source.name) == (f"{_REPO}@{_SHA}", "git", "x-926")
    assert settings_file.read_bytes() == before


def test_list_ends_with_the_missing_section_after_the_installed_rows(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _runner(trusted_site, "here-926")(["install"])
    _pin(tmp_path, _git_pin())
    assert run_extension_command(["list"], settings=_mem(_git_record())) == 0
    out = capsys.readouterr().out
    assert (
        out.index("Installed extensions:")
        < out.index("[here-926 0.1.0]")
        < out.index("Recorded but not installed")
    )
    assert "restore at its pin: aelix extension restore restore-ext-926" in out


async def test_the_start_hook_runs_before_the_harness_with_the_parsed_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_coding_agent.cli import entry

    seen: list[tuple[str, bool, bool]] = []

    class _Stop(Exception):
        pass

    def fake(app_mode: str, *, offline: bool, no_extensions: bool) -> None:
        seen.append((app_mode, offline, no_extensions))
        raise _Stop

    monkeypatch.setattr(extension_restore, "restore_at_start", fake)
    monkeypatch.setattr(entry, "_build_harness_options", lambda *a, **k: pytest.fail("built first"))
    for argv, want in (
        (["--offline", "-p", "hi"], ("print", True, False)),
        (["--no-extensions", "--mode", "json", "hi"], ("json", False, True)),
    ):
        with pytest.raises(_Stop):
            await entry._async_main(argv)  # tests/conftest.py supplies the exit stack
        assert seen[-1] == want


# === fix round 1 (verify r1 F4, F7, F9, F11; the mutants that survived) =========


def test_a_package_pin_without_pip_is_named_at_start_with_its_fix(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify r1 F7: it was offered as restorable, failed every start, and the line was
    cut before the fix it suggested."""

    monkeypatch.setattr(
        EI, "detect_install_backend", lambda: EI.InstallBackend(name="uv", uv_path="/abs/uv")
    )
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity="pkgext-926",
            kind="pypi",
            mode="tofi",
            name="pkgext-926",
            version="2.1.0",
            sha256="ab" * 32,
        ),
    )
    record = ExtensionSourceObject(spec="pkgext-926", kind="pypi", name="pkgext-926")
    child = _start("interactive", trusted_site, record=record)
    assert child.calls == []
    assert capsys.readouterr().out == (
        "aelix: pkgext-926 is recorded but not installed, and cannot be restored at a pin (its pin "
        "is a version and a sha256, which only pip can check, and this environment has no pip); "
        f"reinstall it with: uv pip install --python {sys.executable} pip, then aelix extension "
        "restore pkgext-926\n"
    )
    assert run_extension_command(["list"], settings=_mem(record)) == 0
    out = capsys.readouterr().out
    assert (
        "not restorable at a pin: its pin is a version and a sha256, which only pip can check"
        in out
    )


def _sigint_noop(_signum: int, _frame: object) -> None:
    """asyncio.Runner's first Ctrl+C: cancel the main task and return."""


def test_ctrl_c_during_the_start_up_restore_stops_it_and_the_start_goes_on(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r1 F4: a KeyboardInterrupt traceback, 3 of 3. Lane's call (ADR-0321 D5):
    Ctrl+C there means "stop restoring", and the start continues."""

    import signal
    import time

    _pin(tmp_path, _git_pin())
    seen: list[object] = []

    def interrupted(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[bytes]:
        seen.append(signal.getsignal(signal.SIGINT))
        signal.raise_signal(signal.SIGINT)  # the terminal's Ctrl+C
        time.sleep(5)  # never reached: the default handler raises at once
        raise AssertionError("the restore was not interrupted")

    previous = signal.signal(signal.SIGINT, _sigint_noop)
    try:
        started = time.monotonic()
        restore_at_start(
            "interactive",
            offline=False,
            no_extensions=False,
            settings=_mem(_git_record()),
            in_tool_env=lambda: True,
            child_runner=interrupted,
        )
        assert time.monotonic() - started < 4
        assert signal.getsignal(signal.SIGINT) is _sigint_noop
    finally:
        signal.signal(signal.SIGINT, previous)
    assert seen == [signal.default_int_handler]
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == (
        "aelix: restore stopped (Ctrl+C); not restored: restore-ext-926. They stay recorded: "
        "`aelix extension restore` puts them back."
    )


def test_the_start_up_child_ends_its_whole_tree_on_the_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strays: list[int]
) -> None:
    """Verify r1 F9: at the bound the root was killed and the `git` uv started ran on at
    ppid 1. The child here starts a grandchild, announces it, and sleeps."""

    import time

    from tests.process_probe import STATE_ALIVE
    from tests.process_tree.test_process_tree_real_processes import _await_dead

    monkeypatch.setattr(extension_restore, "_START_RESTORE_TIMEOUT_S", 1.0)
    marker = tmp_path / "pids"
    root = (
        "import os, subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)  # aelix926'])\n"
        f"open({str(marker)!r} + '.tmp', 'w').write(f'{{os.getpid()}} {{g.pid}}')\n"
        f"os.replace({str(marker)!r} + '.tmp', {str(marker)!r})\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    result = extension_restore._quiet_runner([sys.executable, "-c", root])
    elapsed = time.monotonic() - started
    pids = [int(x) for x in marker.read_text(encoding="utf-8").split()]
    strays.extend(pids)
    assert result.returncode == 124
    assert b"stopped after 1 s" in result.stderr
    assert elapsed < 15, elapsed
    for pid in pids:
        assert _await_dead(pid) != STATE_ALIVE, pid


def test_the_start_up_child_reads_no_stdin(tmp_path: Path) -> None:
    result = extension_restore._quiet_runner(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]
    )
    assert result.returncode == 0 and result.stdout.strip() == b"''"


def test_the_start_up_lines_break_at_spaces_on_a_terminal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r1 F11: at 120 columns the terminal cut `aelix extension restore` and a
    name in two."""

    import os
    import shutil

    text = (
        "aelix: aelix-starter-ext is recorded but not installed, and cannot be restored at a pin "
        "(recorded at a branch or tag, not a commit, so nothing pins it); reinstall it with: "
        "aelix extension install git+https://github.com/handochan/aelix-starter-ext.git@main"
    )
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(0, 0): os.terminal_size((60, 24))
    )
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    extension_restore._say(text)
    out = capsys.readouterr().out.splitlines()
    long_word = "git+https://github.com/handochan/aelix-starter-ext.git@main"
    assert all(len(line) <= 59 for line in out if long_word not in line)
    assert " ".join(line.strip() for line in out) == text
    assert out[-1].strip() == long_word and len(out) > 2


def test_the_start_never_repins(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file whose bytes changed since its pin: the start refuses it and leaves the pin;
    only an explicit `restore --repin` may move it (ADR-0187)."""

    wheel = tmp_path / "pathext_926-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"approved")
    approved = hashlib.sha256(b"approved").hexdigest()
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity=str(wheel.resolve()), kind="path", mode="tofi", sha256=approved
        ),
    )
    wheel.write_bytes(b"changed since")
    record = ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name="pathext-926")
    child = _start("interactive", trusted_site, record=record)
    assert child.calls == []
    assert "aelix: could not restore pathext-926 (" in capsys.readouterr().out
    pins = extension_pins.load_pins(extension_pins.pins_file_path(_agent(tmp_path)))
    assert pins[str(wheel.resolve())].sha256 == approved


def test_the_start_skips_a_record_an_earlier_restore_brought_along(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    child = _PackRunner(trusted_site)
    restore_at_start(
        "interactive",
        offline=False,
        no_extensions=False,
        settings=_mem(*_pack_records(tmp_path)),
        in_tool_env=lambda: True,
        child_runner=child,
    )
    assert [c[-1] for c in child.calls] == [f"{_REPO}@{_SHA}"]
    out = capsys.readouterr().out
    assert out.count("aelix: restored restore-ext-926") == 1
    assert "dep-ext-926 (" not in out


def test_of_the_records_of_one_name_only_the_last_is_used(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two named records of one name (aelix 0.1.0 wrote them when the extension was
    removed by hand between two installs from different sources): the earlier one,
    at its pin, is never restored in place of the later one."""

    _pin(tmp_path, _git_pin())
    later = ExtensionSourceObject(spec=str(tmp_path), kind="path", name="Restore_Ext.926")
    child = _start("interactive", trusted_site, record=None, records=[_git_record(), later])
    assert child.calls == []
    [rec] = missing_recorded_extensions([_git_record(), later])
    assert rec.source is later and rec.target is None
    assert "a directory has no single artifact to pin" in capsys.readouterr().out


def test_the_start_installs_with_the_environments_own_installer(
    trusted_site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uv tool environment has no pip: the child is `uv pip install`, not `python -m pip`."""

    monkeypatch.setattr(
        EI, "detect_install_backend", lambda: EI.InstallBackend(name="uv", uv_path="/abs/uv")
    )
    _pin(tmp_path, _git_pin())
    child = _start("interactive", trusted_site)
    assert child.calls and child.calls[0][:3] == ["/abs/uv", "pip", "install"]


def test_a_restore_that_does_not_bind_says_so(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.cli.test_extension_install_verdict import _future_files

    _pin(tmp_path, _git_pin())
    unbound = _InstallingRunner(
        trusted_site,
        "restore_ext_926",
        files=_future_files("restore_ext_926"),
        entry_points={"restore-ext-926": "restore_ext_926.ext:setup"},
    )
    assert (
        run_extension_command(["restore", "--yes"], settings=_mem(_git_record()), runner=unbound)
        == 3
    )
    capsys.readouterr()
    for item in sorted(trusted_site.rglob("*"), reverse=True):
        item.unlink() if item.is_file() else item.rmdir()
    _drop_modules(trusted_site)

    def child(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[bytes]:
        return unbound(argv, cwd=cwd)

    restore_at_start(
        "interactive",
        offline=False,
        no_extensions=False,
        settings=_mem(_git_record()),
        in_tool_env=lambda: True,
        child_runner=child,
    )
    assert (
        "aelix: restored restore-ext-926, but it does not bind on this build; see `aelix extension verify`."
        in capsys.readouterr().out
    )


def test_restore_refuses_an_unknown_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_extension_command(["restore", "--bogus"], settings=_mem()) == 2
    assert "unknown flag '--bogus'" in capsys.readouterr().err


def test_restore_finds_a_record_by_its_source(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _pin(tmp_path, _git_pin())
    runner = _runner(trusted_site)
    mem = _mem(_git_record())
    spec = f"{_REPO}@{_SHA}"
    assert run_extension_command(["restore", spec, "--yes"], settings=mem, runner=runner) == 0
    assert len(runner.calls) == 1


def test_only_a_uv_tool_environment_restores_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert extension_restore._in_uv_tool_environment() is False
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    assert extension_restore._in_uv_tool_environment() is True


def test_a_path_record_whose_pin_is_not_a_file_hash_is_not_restorable(
    trusted_site: Path, tmp_path: Path
) -> None:
    wheel = tmp_path / "pathext_926-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"w")
    for pin in (
        extension_pins.Pin(
            identity=str(wheel.resolve()), kind="git", mode="tofi", sha256="ab" * 32
        ),
        extension_pins.Pin(identity=str(wheel.resolve()), kind="path", mode="tofi"),
    ):
        _pin(tmp_path, pin)
        [rec] = missing_recorded_extensions(
            [ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name="pathext-926")]
        )
        assert rec.target is None and rec.problem == "no pin records the file's sha256"


def test_a_record_with_no_name_is_a_registered_source_not_an_install(
    trusted_site: Path, tmp_path: Path
) -> None:
    """``source add`` writes a row with no name and installs nothing; an install writes
    the name of what it installed. A nameless row is not evidence of an install."""

    wheel = tmp_path / "pathext_926-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"w")
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity=str(wheel.resolve()),
            kind="path",
            mode="tofi",
            sha256=hashlib.sha256(b"w").hexdigest(),
        ),
        _git_pin(),
    )
    records = [
        ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name=None),
        ExtensionSourceObject(spec="pkgext-926==1.0", kind="pypi", name=" "),
        _git_record(name=None),
    ]
    assert missing_recorded_extensions(records, can_verify_pypi=True) == []


def test_a_record_with_an_empty_spec_is_not_listed(trusted_site: Path, tmp_path: Path) -> None:
    assert (
        missing_recorded_extensions([ExtensionSourceObject(spec="  ", kind="git", name="x")]) == []
    )


def test_no_record_means_no_scan_of_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every start reaches this; with nothing recorded it reads neither pins nor dists."""

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("scanned")

    monkeypatch.setattr(extension_restore, "_Installed", boom)
    monkeypatch.setattr(extension_pins, "load_pins", boom)
    monkeypatch.setattr(EI, "detect_install_backend", boom)
    assert (
        missing_recorded_extensions([ExtensionSourceObject(spec="https://i/simple", kind="index")])
        == []
    )


def test_verify_says_when_recorded_extensions_are_not_installed(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify r1 F10: the issue's own diagnosis ran `extension verify`, which said only
    "nothing to verify"."""

    _pin(tmp_path, _git_pin())
    monkeypatch.setattr(extension_restore, "read_only_settings", lambda: _mem(_git_record()))
    assert run_extension_command(["verify"], settings=_mem()) == 0
    out = capsys.readouterr().out
    assert "No extensions installed (via entry_points); nothing to verify." in out
    assert (
        "1 recorded extension(s) are not installed in this environment (restore-ext-926); "
        "`aelix extension restore` puts back the ones pinned (`aelix extension list` says which)."
    ) in out


async def test_status_says_when_recorded_extensions_are_not_installed(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify r1 F10: `aelix status` said "Discovered extensions: none." and nothing of
    the two records the reinstall had emptied."""

    import json

    from aelix_coding_agent.cli.status import run_status_command

    _pin(tmp_path, _git_pin())
    monkeypatch.setattr(extension_restore, "read_only_settings", lambda: _mem(_git_record()))
    assert await run_status_command([]) == 0
    out = capsys.readouterr().out
    assert (
        "Recorded but not installed (1): restore-ext-926 — `aelix extension restore` puts back "
        "the ones pinned."
    ) in out
    assert await run_status_command(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recorded_not_installed"] == ["restore-ext-926"]


def test_the_no_pip_advice_says_what_a_rebuild_removes() -> None:
    """Verify r1 F10: both messages advised `uv tool install --force --with pip aelix`,
    which removes every extension not in the receipt, without saying so."""

    backend = EI.InstallBackend(name="uv", uv_path="/abs/uv")
    refusal = EI._uv_verify_refusal_message("x==1", backend)
    assert f"`uv pip install --python {EI.display_argv([sys.executable])} pip`" in refusal
    assert "It keeps the installed extensions, and an aelix update removes pip again" in refusal
    missing = EI._backend_missing_message("install", "x")
    for text in (refusal, missing):
        assert "removes every extension not in it" in text
        assert "`aelix extension restore --all` then puts back the pinned ones" in text


def test_restore_without_pip_does_not_offer_a_package_pin(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        EI, "detect_install_backend", lambda: EI.InstallBackend(name="uv", uv_path="/abs/uv")
    )
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity="pkgext-926",
            kind="pypi",
            mode="tofi",
            name="pkgext-926",
            version="2.1.0",
            sha256="ab" * 32,
        ),
    )
    record = ExtensionSourceObject(spec="pkgext-926", kind="pypi", name="pkgext-926")
    assert run_extension_command(["restore", "--all", "--yes"], settings=_mem(record)) == 2
    out = capsys.readouterr().out
    assert "Cannot restore pkgext-926 (pkgext-926==2.1.0) at a pin: its pin is a version" in out
    assert "  Reinstall it with: uv pip install --python" in out


def test_verify_adds_no_line_when_nothing_recorded_is_missing_or_for_one_name(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runner(trusted_site, "here-926")(["install"])
    monkeypatch.setattr(extension_restore, "read_only_settings", lambda: _mem())
    assert run_extension_command(["verify"], settings=_mem()) == 0
    assert "recorded extension" not in capsys.readouterr().out
    _pin(tmp_path, _git_pin())
    monkeypatch.setattr(extension_restore, "read_only_settings", lambda: _mem(_git_record()))
    assert run_extension_command(["verify", "here-926"], settings=_mem()) == 0
    assert "recorded extension" not in capsys.readouterr().out
    assert run_extension_command(["verify"], settings=_mem()) == 0
    assert "1 recorded extension(s) are not installed" in capsys.readouterr().out


# === fix round 2 (Codex review round 2, verify round 2) =====================


def _wheel_pin(tmp_path: Path, data: bytes = b"approved wheel") -> Path:
    wheel = tmp_path / "wheels" / "pathext_926-1.0.0-py3-none-any.whl"
    wheel.parent.mkdir(exist_ok=True)
    wheel.write_bytes(data)
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity=str(wheel.resolve()),
            kind="path",
            mode="tofi",
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )
    return wheel


def test_a_source_only_registered_stays_uninstalled_at_start_and_by_restore(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex r2 M1: a wheel with a provisioned pin, registered with `extension source
    add` and never installed, was installed and loaded by the next interactive start.
    `source add` is register-only (ADR-0186), and ADR-0321 D3 promises it stays so."""

    wheel = _wheel_pin(tmp_path)
    mem = _mem()
    assert run_extension_command(["source", "add", str(wheel)], settings=mem) == 0
    assert "Registered source: [path]" in capsys.readouterr().out
    child = _ChildRunner(trusted_site)
    restore_at_start(
        "interactive",
        offline=False,
        no_extensions=False,
        settings=mem,
        in_tool_env=lambda: True,
        child_runner=child,
    )
    assert child.calls == []
    assert capsys.readouterr() == ("", "")
    runner = _runner(trusted_site, "pathext-926")
    assert run_extension_command(["restore", "--all", "--yes"], settings=mem, runner=runner) == 0
    assert "Nothing to restore" in capsys.readouterr().out
    assert run_extension_command(["restore", str(wheel), "--yes"], settings=mem, runner=runner) == 2
    assert run_extension_command(["list"], settings=mem) == 0
    assert "Recorded but not installed" not in capsys.readouterr().out
    assert runner.calls == []


class _SubdirRunner:
    """Installs ``mono-<sub>`` for a git target ``…#subdirectory=<sub>``, with the PEP
    610 record uv writes: the URL and commit, and the subdirectory beside them."""

    def __init__(self, site: Path) -> None:
        self.site = site
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        sub = argv[-1].rsplit("#subdirectory=", 1)[1]
        dist = f"mono-{sub}"
        mod = dist.replace("-", "_")
        info = install_dist(
            self.site,
            mod,
            version="0.1.0",
            entry_points={"aelix.extensions": {dist: f"{mod}.ext:setup"}},
            files=_bound_files(dist, module=mod),
        )
        _record_direct_url(
            info,
            "https://github.com/handochan/restore-ext-926.git",
            vcs_info={"vcs": "git", "commit_id": _SHA},
            subdirectory=sub,
        )
        return subprocess.CompletedProcess(args=argv, returncode=0)


def test_two_extensions_of_one_commit_at_two_subdirectories_are_two(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex r2 M5: `restore --all` restored mono-alpha only, and `restore mono-beta`
    then said it was installed: the key read from direct_url.json was its url alone."""

    records = [
        ExtensionSourceObject(
            spec=f"{_REPO}@{_SHA}#subdirectory={sub}", kind="git", name=f"mono-{sub}"
        )
        for sub in ("alpha", "beta")
    ]
    _pin(
        tmp_path,
        *(
            extension_pins.Pin(
                identity=f"{_REPO}#subdirectory={sub}", kind="git", mode="tofi", git_sha=_SHA
            )
            for sub in ("alpha", "beta")
        ),
    )
    runner = _SubdirRunner(trusted_site)
    mem = _mem(*records)
    assert run_extension_command(["restore", "--all", "--yes"], settings=mem, runner=runner) == 0
    assert [c[-1].rsplit("=", 1)[1] for c in runner.calls] == ["alpha", "beta"]
    assert missing_recorded_extensions(records) == []
    capsys.readouterr()
    assert run_extension_command(["restore", "mono-beta", "--yes"], settings=mem) == 0
    assert "mono-beta is installed; nothing to restore." in capsys.readouterr().out


def test_restore_offline_takes_the_registered_index_as_install_does(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex r2 S3: `restore --offline` passed --no-index where `extension install
    --offline` passed the registered index for the wheel's dependencies."""

    wheel = _wheel_pin(tmp_path)
    index = (tmp_path / "deps").as_uri()
    record = ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name="pathext-926")
    registered = ExtensionSourceObject(spec=index, kind="index")
    restored = _runner(trusted_site, "pathext-926")
    code = run_extension_command(
        ["restore", "--all", "--yes", "--offline"],
        settings=_mem(record, registered),
        runner=restored,
    )
    assert code == 0, capsys.readouterr()
    import shutil

    for landed in trusted_site.glob("pathext_926*"):
        shutil.rmtree(landed)
    _drop_modules(trusted_site)
    installed = _runner(trusted_site, "pathext-926")
    assert (
        run_extension_command(
            ["install", str(wheel), "--yes", "--offline"],
            settings=_mem(registered),
            runner=installed,
        )
        == 0
    )
    [restore_argv], [install_argv] = restored.calls, installed.calls
    assert "--no-index" not in restore_argv and index in restore_argv
    assert restore_argv[:-1] == install_argv[:-1]


@pytest.mark.parametrize("why", ["not json", "unreadable"])
def test_restore_with_a_settings_file_it_cannot_read_says_so_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], why: str
) -> None:
    """Codex r2 S4: with settings.json at mode 000, `restore --all --yes` warned, then
    printed "every recorded extension is installed" and exited 0."""

    import os

    settings = tmp_path / "settings.json"
    if why == "not json":
        settings.write_text("{not json", encoding="utf-8")
    else:
        if sys.platform == "win32" or os.geteuid() == 0:
            pytest.skip("mode 000 does not stop this reader")
        settings.write_text('{"extensionSources": []}', encoding="utf-8")
        settings.chmod(0)
    try:
        assert run_extension_command(["restore", "--all", "--yes"]) == 2
    finally:
        settings.chmod(0o600)
    out, err = capsys.readouterr()
    assert "Nothing to restore" not in out
    assert "the settings file that holds the install records could not be read (" in err
    assert "so restore cannot tell what to restore; nothing was installed." in err


@pytest.mark.parametrize("download", [True, False])
def test_a_package_pin_is_offered_by_default_only_where_the_installer_downloads(
    trusted_site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, download: bool
) -> None:
    """Verify r2 N3 R6: the tests ran without pip, so the default was never computed
    with pip present."""

    monkeypatch.setattr(
        EI,
        "detect_install_backend",
        lambda: EI.InstallBackend(name="pip") if download else None,
    )
    _pin(
        tmp_path,
        extension_pins.Pin(
            identity="pkgext-926",
            kind="pypi",
            mode="tofi",
            name="pkgext-926",
            version="2.1.0",
            sha256="ab" * 32,
        ),
    )
    record = ExtensionSourceObject(spec="pkgext-926", kind="pypi", name="pkgext-926")
    [rec] = missing_recorded_extensions([record])
    assert (str(rec.target) if rec.target else None) == ("pkgext-926==2.1.0" if download else None)


def test_ctrl_c_names_only_what_was_not_restored(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r2 N3 R15: the list after Ctrl+C kept an entry restored before it."""

    import signal

    other_repo = _REPO.replace("926", "b926")
    _pin(
        tmp_path,
        _git_pin(),
        extension_pins.Pin(identity=other_repo, kind="git", mode="tofi", git_sha=_SHA),
    )
    first = _ChildRunner(trusted_site)

    def child(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[bytes]:
        if argv[-1].startswith(other_repo):
            signal.raise_signal(signal.SIGINT)
        return first(argv, cwd=cwd)

    previous = signal.signal(signal.SIGINT, _sigint_noop)
    try:
        restore_at_start(
            "interactive",
            offline=False,
            no_extensions=False,
            settings=_mem(
                _git_record(),
                ExtensionSourceObject(spec=f"{other_repo}@{_SHA}", kind="git", name="later-926"),
            ),
            in_tool_env=lambda: True,
            child_runner=child,
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("aelix: restored restore-ext-926 (")
    assert lines[-1].startswith("aelix: restore stopped (Ctrl+C); not restored: later-926. ")


def test_a_printed_command_is_never_broken_across_lines(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r2 N6: `reinstall it with: aelix extension install` / `  git+file://…
    --repin` — copying the visible lines split the command in two. And N3 R18: the
    lines break at the terminal's width, not at a fixed 40."""

    import os
    import shutil

    width = 100
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(0, 0): os.terminal_size((width, 24))
    )
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    command = "aelix extension install git+file:///home/u/src/a%20b@" + "c" * 40 + " --yes"
    text = (
        "aelix: aelix-starter-ext is recorded but not installed, and cannot be restored at a pin "
        "(recorded at c36703d30000, but the file it names is gone; `aelix extension restore "
        "aelix-starter-ext --repin` is a command); reinstall it with:"
    )
    extension_restore._say(text, command)
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == f"  {command}"
    wrapped = out[:-1]
    assert " ".join(line.strip() for line in wrapped) == text
    assert all(len(line) <= width - 1 for line in wrapped)
    assert max(len(line) for line in wrapped) > 60
    assert any("`aelix extension restore aelix-starter-ext --repin`" in line for line in wrapped)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    extension_restore._say(text, command)
    assert capsys.readouterr().out == f"{text} {command}\n"


def test_verify_names_what_is_missing_when_an_endpoint_does_not_bind(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify r2 N3 E5: the line was dropped from verify's not-bound branch unseen."""

    install_dist(
        trusted_site, "unbound_926", entry_points={"aelix.extensions": {"u": "unbound_926:setup"}}
    )
    _pin(tmp_path, _git_pin())
    monkeypatch.setattr(extension_restore, "read_only_settings", lambda: _mem(_git_record()))
    assert run_extension_command(["verify"], settings=_mem()) == EI._VERIFY_NOT_BOUND
    out = capsys.readouterr().out
    assert "endpoint(s) NOT bound" in out
    last = out.rstrip().splitlines()[-1]
    assert last.startswith(
        "1 recorded extension(s) are not installed in this environment (restore-ext-926)"
    )


async def test_status_reports_no_list_when_the_records_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r2 N3 ST3: a narrowed except clause survived; a status report never fails
    on this one block."""

    import json

    from aelix_coding_agent.cli.status import run_status_command

    def unreadable() -> SettingsManager:
        raise RuntimeError("settings.json: permission denied")

    monkeypatch.setattr(extension_restore, "read_only_settings", unreadable)
    assert await run_status_command(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recorded_not_installed"] is None


def test_a_distribution_whose_metadata_raises_is_passed_over(
    trusted_site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dist-info with no readable METADATA reads as no name today and raises
    KeyError once importlib's deprecation lands; neither hides the rest of the
    environment from the check."""

    import importlib.metadata

    class _Torn:
        @property
        def name(self) -> str:
            raise KeyError("Name")

    install_dist(trusted_site, "restore_ext_926")
    real = list(importlib.metadata.distributions())
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [_Torn(), *real])
    _pin(tmp_path, _git_pin())
    assert missing_recorded_extensions([_git_record()]) == []


# === verify r3 F1: never an older version than the user last installed =======


class _ReplacingRunner:
    """Installs ``restore-ext-926`` the way uv does over an installed one: the old
    dist-info goes and the new one lands, with the PEP 610 record of its source."""

    def __init__(self, site: Path) -> None:
        self.site = site
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        _wipe(self.site)
        info = install_dist(
            self.site,
            "restore_ext_926",
            version=f"0.1.{len(self.calls)}",
            entry_points={"aelix.extensions": {"restore-ext-926": "restore_ext_926.ext:setup"}},
            files=_bound_files("restore-ext-926", module="restore_ext_926"),
        )
        spec = argv[-1]
        if spec.startswith("git+"):
            url, _, sha = spec.removeprefix("git+").rpartition("@")
            _record_direct_url(info, url, vcs_info={"vcs": "git", "commit_id": sha})
        else:
            _record_direct_url(info, Path(spec).as_uri(), archive_info={})
        return subprocess.CompletedProcess(args=argv, returncode=0)


def _wipe(site: Path) -> None:
    """What an aelix reinstall does to the extension: its dist-info is gone."""

    import shutil

    for info in site.glob("restore_ext_926-*.dist-info"):
        shutil.rmtree(info)


def _wheel(tmp_path: Path, version: str) -> Path:
    wheel = tmp_path / "wheels" / f"restore_ext_926-{version}-py3-none-any.whl"
    wheel.parent.mkdir(exist_ok=True)
    wheel.write_bytes(f"wheel {version}".encode())
    return wheel


@pytest.mark.parametrize(
    "flow",
    ["git A, then git B --repin", "a newer wheel over an older one", "git after a wheel"],
)
def test_an_install_over_an_installed_extension_names_it_and_keeps_one_record(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], flow: str
) -> None:
    """Verify r3 F1 (a), the verifier's three flows through `aelix extension install`.
    The second install found no new distribution, so its record had no name and a
    restore after the wipe took the first. Now it names the distribution, the first
    record goes, and the restore brings back the second."""

    first, second = {
        "git A, then git B --repin": ([f"{_REPO}@{_OTHER}"], [f"{_REPO}@{_SHA}", "--repin"]),
        "a newer wheel over an older one": (
            [str(_wheel(tmp_path, "0.1.0"))],
            [str(_wheel(tmp_path, "0.2.0"))],
        ),
        "git after a wheel": ([str(_wheel(tmp_path, "0.1.0"))], [f"{_REPO}@{_SHA}"]),
    }[flow]
    runner = _ReplacingRunner(trusted_site)
    mem = _mem()
    for args in (first, second):
        code = run_extension_command(["install", *args, "--yes"], settings=mem, runner=runner)
        assert code == 0, capsys.readouterr()
    latest = EI._install_spec(second[0], EI.classify_target(second[0]))
    assert [(s.spec, s.name) for s in mem.get_extension_sources()] == [(latest, "restore-ext-926")]
    _wipe(trusted_site)
    [rec] = missing_recorded_extensions(mem.get_extension_sources())
    assert rec.target is not None and EI._install_spec(rec.target, rec.source.kind) == latest


def _dist_with_record(site: Path, name: str, **direct_url: object) -> None:
    dist = name.replace("_", "-")
    info = install_dist(site, name, entry_points={"aelix.extensions": {dist: f"{name}:setup"}})
    _record_direct_url(info, **direct_url)  # type: ignore[arg-type]


_INSTALLED = {"restore-ext-926", "mono-alpha", "mono-beta", "dir-one", "dir-two"}


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (f"{_REPO}@{_SHA}", "restore-ext-926"),
        (f"{_REPO.replace('handochan', 'other')}@{_SHA}", None),  # another url
        (f"{_REPO}@{_OTHER}", None),  # another commit
        (f"{_REPO}@main", "restore-ext-926"),  # no commit named: url and subdirectory
        (f"{_REPO}@{_SHA}#egg=x&subdirectory=beta", "mono-beta"),
        (f"{_REPO}@{_SHA}#subdirectory=", "restore-ext-926"),  # an empty one is none
        (f"{_REPO}@{_SHA}#subdirectory=gamma", None),
    ],
)
def test_the_distribution_a_git_target_is_matches_url_commit_and_subdirectory(
    trusted_site: Path, target: str, expected: str | None
) -> None:
    url = "https://github.com/handochan/restore-ext-926.git"
    vcs = {"vcs": "git", "commit_id": _SHA.upper()}
    _dist_with_record(trusted_site, "restore_ext_926", url=url, vcs_info=vcs)
    _dist_with_record(trusted_site, "mono_beta", url=url, vcs_info=vcs, subdirectory="beta")
    _dist_with_record(trusted_site, "mono_alpha", url=url, vcs_info=vcs, subdirectory="alpha")
    assert EI._dist_the_target_is(target, "git", _INSTALLED) == expected


def test_the_distribution_a_path_target_is_needs_exactly_one_extension_match(
    trusted_site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _dist_with_record(trusted_site, "dir_one", url=src.as_uri(), dir_info={})
    assert EI._dist_the_target_is(str(src), "path", _INSTALLED) == "dir-one"
    # Only a distribution that exposes an extension is named.
    assert EI._dist_the_target_is(str(src), "path", {"dir-two"}) is None
    _dist_with_record(trusted_site, "dir_two", url=src.as_uri(), dir_info={})
    assert EI._dist_the_target_is(str(src), "path", _INSTALLED) is None
    assert EI._dist_the_target_is("dir-one", "pypi", _INSTALLED) is None
    # A record that is not JSON, not an object, or without a url names nothing.
    (trusted_site / "dir_two-0.1.0.dist-info" / "direct_url.json").unlink()
    for body in ("{not json", "[]", '{"dir_info": {}}', '{"url": 7}'):
        (trusted_site / "dir_one-0.1.0.dist-info" / "direct_url.json").write_text(body)
        assert EI._dist_the_target_is(str(src), "path", _INSTALLED) is None
    import importlib.metadata

    def broken() -> list[object]:
        raise OSError("site unreadable")

    monkeypatch.setattr(importlib.metadata, "distributions", broken)
    assert EI._dist_the_target_is(str(src), "path", _INSTALLED) is None


def test_a_dist_whose_metadata_raises_does_not_hide_the_one_a_target_is(
    trusted_site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.metadata

    class _Torn:
        @property
        def name(self) -> str:
            raise KeyError("Name")

    src = tmp_path / "src"
    src.mkdir()
    _dist_with_record(trusted_site, "dir_one", url=src.as_uri(), dir_info={})
    real = list(importlib.metadata.distributions())
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [_Torn(), *real])
    assert EI._dist_the_target_is(str(src), "path", _INSTALLED) == "dir-one"


def test_a_bundle_over_its_installed_extension_keeps_the_extension_record(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A record of another name stays; only records of the name an install names go."""

    runner = _ReplacingRunner(trusted_site)
    other = ExtensionSourceObject(spec=f"{_REPO2}@{_SHA}", kind="git", name="dep-ext-926")
    registered = ExtensionSourceObject(spec="https://idx.example/simple", kind="index")
    mem = _mem(other, registered)
    assert (
        run_extension_command(["install", f"{_REPO}@{_SHA}", "--yes"], settings=mem, runner=runner)
        == 0
    )
    assert (
        run_extension_command(
            ["install", f"{_REPO}@{_OTHER}", "--yes", "--repin"], settings=mem, runner=runner
        )
        == 0
    )
    assert [(s.spec, s.name) for s in mem.get_extension_sources()] == [
        (other.spec, "dep-ext-926"),
        (registered.spec, None),
        (f"{_REPO}@{_OTHER}", "restore-ext-926"),
    ]


def _release_rows(tmp_path: Path, shape: str) -> list[ExtensionSourceObject]:
    """The records aelix 0.1.0 / 0.1.0b2 wrote (verify r3 rec-shapes, shapes2)."""

    old, new = _wheel(tmp_path, "0.1.0"), _wheel(tmp_path, "0.2.0")
    _pin(
        tmp_path,
        *(
            extension_pins.Pin(
                identity=str(wheel.resolve()),
                kind="path",
                mode="tofi",
                sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
            )
            for wheel in (old, new)
        ),
    )
    named_old = ExtensionSourceObject(spec=str(old.resolve()), kind="path", name="restore-ext-926")
    return {
        # 0.1.0b2: wheel 0.1.0, then wheel 0.2.0 over it.
        "wheel, then a newer wheel": [
            named_old,
            ExtensionSourceObject(spec=str(new.resolve()), kind="path"),
        ],
        # 0.1.0: wheel 0.1.0, then the same distribution from git (another repository).
        "wheel, then git": [
            named_old,
            ExtensionSourceObject(spec=f"{_REPO2}@{_SHA}", kind="git"),
        ],
    }[shape]


def test_a_wheel_record_a_later_wheel_of_its_distribution_replaced_is_not_restored(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = _release_rows(tmp_path, "wheel, then a newer wheel")
    newer = rows[1].spec
    child = _start("interactive", trusted_site, records=rows)
    assert child.calls == []
    out = capsys.readouterr().out
    assert (
        "aelix: restore-ext-926 is recorded but not installed, and that record is not its "
        f"latest install (a later record installed it from {newer}); install the latest with: "
        f"aelix extension install {newer}\n"
    ) == out
    [rec] = missing_recorded_extensions(rows)
    assert rec.stale and rec.target is None
    assert rec.command == f"aelix extension install {newer}"


def test_a_later_record_tied_to_nothing_keeps_the_automatic_restore_off_earlier_ones(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """0.1.0 wrote a git record with no name for the same distribution from git after a
    wheel; nothing ties it to the wheel. The start and `restore --all` leave the wheel
    alone; `restore <name>` restores it and says why it might not be the latest."""

    rows = _release_rows(tmp_path, "wheel, then git")
    why = (
        f"a later record ({_REPO2}@{_SHA}) names no extension and may be a newer install of "
        "it; `aelix extension restore restore-ext-926` restores this one"
    )
    child = _start("interactive", trusted_site, records=rows)
    assert child.calls == []
    assert capsys.readouterr().out == (
        f"aelix: not restoring restore-ext-926 automatically: a later record ({_REPO2}@{_SHA}) "
        "names no extension and may be a newer install of it; restore this one with: "
        "aelix extension restore restore-ext-926\n"
    )
    runner = _runner(trusted_site)
    mem = _mem(*rows)
    assert run_extension_command(["list"], settings=mem) == 0
    assert f"      not restored automatically: {why}\n" in capsys.readouterr().out
    assert run_extension_command(["restore", "--all", "--yes"], settings=mem, runner=runner) == 2
    assert capsys.readouterr().out == f"Not restoring restore-ext-926 automatically: {why}.\n"
    assert runner.calls == []
    [rec] = missing_recorded_extensions(rows)
    assert rec.held_by == f"{_REPO2}@{_SHA}" and rec.target is not None
    assert (
        run_extension_command(["restore", "restore-ext-926", "--yes"], settings=mem, runner=runner)
        == 0
    )
    out = capsys.readouterr().out
    assert out.startswith(
        f"Note: a later record ({_REPO2}@{_SHA}) names no extension; if it is a newer install "
        "of restore-ext-926, install that instead.\nRestoring restore-ext-926 ("
    )
    assert len(runner.calls) == 1


def test_what_ties_a_later_record_and_what_it_holds(trusted_site: Path, tmp_path: Path) -> None:
    """The 0.1.0 shape the verifier measured: a nameless git row of gitext's repository
    after dirext, whlext and a catalog row is tied to gitext, so it holds nothing back;
    a row tied to nothing holds only the records before it; an index or catalog row
    with no name is not an install."""

    wheel = _wheel(tmp_path, "0.1.0")
    _pin(
        tmp_path,
        _git_pin(_SHA),
        extension_pins.Pin(identity=_REPO2, kind="git", mode="tofi", git_sha=_SHA),
        extension_pins.Pin(
            identity=str(wheel.resolve()),
            kind="path",
            mode="tofi",
            sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        ),
    )
    whl = ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name="whl-926")
    rows = [
        _git_record(_OTHER),
        whl,
        ExtensionSourceObject(spec=str(tmp_path / "catalog.json"), kind="catalog"),
        ExtensionSourceObject(spec="https://idx.example/simple", kind="index"),
        _git_record(_SHA, name=None),
        ExtensionSourceObject(spec=f"{_REPO2}@{_SHA}", kind="git", name="dep-ext-926"),
    ]
    found = {r.label: r for r in missing_recorded_extensions(rows)}
    assert found["restore-ext-926"].stale
    assert found["whl-926"].held_by is None and found["whl-926"].target is not None
    assert found["dep-ext-926"].held_by is None
    untied = ExtensionSourceObject(spec=str(tmp_path / "elsewhere"), kind="path")
    found = {r.label: r for r in missing_recorded_extensions([*rows[:4], untied, *rows[5:]])}
    assert found["whl-926"].held_by == untied.spec
    assert found["dep-ext-926"].held_by is None
    # A wheel row of another distribution is tied to nothing, and a stale record is
    # named as stale, not as held.
    other_wheel = ExtensionSourceObject(
        spec=str(tmp_path / "other_926-1.0-py3-none-any.whl"), kind="path"
    )
    found = {r.label: r for r in missing_recorded_extensions([*rows[:5], other_wheel])}
    assert found["whl-926"].held_by == other_wheel.spec
    assert found["restore-ext-926"].stale and found["restore-ext-926"].held_by is None


def test_an_online_restore_takes_no_index_for_a_git_or_path_record(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r3 F5 R6 (`use_index = True`) is equivalent: `_install_index_args` gives a
    git or path target no index online whatever it is handed, so the argv is the same.
    This pins that end result: a registered index reaches neither argv."""

    wheel = _wheel_pin(tmp_path)
    _pin(
        tmp_path,
        _git_pin(),
        *extension_pins.load_pins(extension_pins.pins_file_path(_agent(tmp_path))).values(),
    )
    index = ExtensionSourceObject(spec="https://idx.example/simple", kind="index")
    path = ExtensionSourceObject(spec=str(wheel.resolve()), kind="path", name="pathext-926")
    for record, dist in ((_git_record(), "restore-ext-926"), (path, "pathext-926")):
        runner = _runner(trusted_site, dist)
        code = run_extension_command(
            ["restore", "--all", "--yes"], settings=_mem(record, index), runner=runner
        )
        assert code == 0, capsys.readouterr()
        [argv] = runner.calls
        assert index.spec not in argv and "--index-url" not in argv


def test_each_start_up_command_is_on_a_line_of_its_own(
    trusted_site: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify r3 F5 R15, R16: `_say` keeps a command on a line of its own, but two of
    its callers put the command back inside the text, where a wrap can split it."""

    import os
    import shutil

    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(0, 0): os.terminal_size((300, 24))
    )
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    _start("interactive", trusted_site)  # no pin: not restorable
    _pin(tmp_path, _git_pin())
    _start("interactive", trusted_site, fail=True)
    _start("interactive", trusted_site, record=_git_record(_OTHER))  # its pin moved: stale
    held = [_git_record(), ExtensionSourceObject(spec=f"{_REPO2}@{_SHA}", kind="git")]
    _start("interactive", trusted_site, records=held)
    lines = capsys.readouterr().out.splitlines()
    for command in (
        f"aelix extension install {_REPO}@{_SHA}",
        "aelix extension restore restore-ext-926",
    ):
        assert f"  {command}" in lines, lines
    assert [ln for ln in lines if ln.startswith("  aelix ")] == [
        f"  aelix extension install {_REPO}@{_SHA}",
        "  aelix extension restore restore-ext-926",
        f"  aelix extension install {_REPO}@{_SHA}",
        "  aelix extension restore restore-ext-926",
    ]


def test_a_reinstall_writes_its_record_last(
    trusted_site: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A record stayed where it was when its source was installed again, so a source
    registered in between held it back from the start as if it could be newer."""

    runner = _ReplacingRunner(trusted_site)
    mem = _mem()
    install = ["install", f"{_REPO}@{_SHA}", "--yes"]
    assert run_extension_command(install, settings=mem, runner=runner) == 0
    assert run_extension_command(["source", "add", f"{_REPO2}@{_SHA}"], settings=mem) == 0
    assert run_extension_command(install, settings=mem, runner=runner) == 0
    assert [(s.spec, s.name) for s in mem.get_extension_sources()] == [
        (f"{_REPO2}@{_SHA}", None),
        (f"{_REPO}@{_SHA}", "restore-ext-926"),
    ]
    _wipe(trusted_site)
    [rec] = missing_recorded_extensions(mem.get_extension_sources())
    assert rec.target is not None and rec.held_by is None


def test_a_target_with_no_source_key_names_nothing(trusted_site: Path) -> None:
    _dist_with_record(trusted_site, "restore_ext_926", url="")
    assert EI._dist_the_target_is("", "git", _INSTALLED) is None


def test_the_latest_of_two_later_records_and_a_hold_on_what_cannot_be_restored(
    trusted_site: Path, tmp_path: Path
) -> None:
    """Of two later records of one repository the last is the latest; a record that is
    not restorable anyway keeps its own lines under a later record tied to nothing."""

    third = "9" * 40
    _pin(tmp_path, _git_pin(_SHA))
    rows = [_git_record(_OTHER), _git_record(_SHA, name=None), _git_record(third, name=None)]
    [rec] = missing_recorded_extensions(rows)
    assert rec.command == f"aelix extension install {_REPO}@{third}"
    unpinned = ExtensionSourceObject(spec=f"{_REPO2}@{_SHA}", kind="git", name="dep-ext-926")
    untied = ExtensionSourceObject(spec=str(tmp_path), kind="path")
    [rec] = missing_recorded_extensions([unpinned, untied])
    assert rec.target is None and rec.held_by is None
    assert rec.problem == "no pin records its commit (installed with --no-verify?)"
