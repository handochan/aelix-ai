"""install.sh Step 2 finds uv where the Astral installer actually put it (#279).

THE DEFECT. Step 2 bootstraps uv with Astral's installer and then has to make
uv visible to its own process. It looked in ``${XDG_BIN_HOME:-$HOME/.local/bin}``
only, but the Astral installer chooses among MORE places, in this order
(uv's install.sh, ``install()``): ``UV_INSTALL_DIR``, ``CARGO_DIST_FORCE_INSTALL_DIR``,
``UV_UNMANAGED_INSTALL`` (flat, or ``<dir>/bin`` for a cargo home), then
``XDG_BIN_HOME``, then ``XDG_DATA_HOME/../bin``, then ``~/.local/bin``. Measured
while building the install-sh-e2e job, on 7d163232 with uv's installer 0.13.0:
``XDG_DATA_HOME=$HOME/data`` put uv in ``$HOME/bin`` and install.sh died with
``uv still not found after install; add '$HOME/.local/bin' to PATH`` - uv
installed, the install aborted, and the advice named a directory uv was never in.

These rows EXECUTE install.sh's real Step 2 with a stand-in for the Astral
installer that drops ``uv`` where the real one would for that environment, and a
``curl`` that fails the GitHub API call so the run stops at Step 3. Reaching
Step 3's error is the proof Step 2 found uv. The real installer runs end to end
in CI's install-sh-e2e job; this file is what pins each precedence arm.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.env_sandbox import child_env

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="install.sh is the POSIX installer; Windows users run install.ps1"
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SH = _REPO_ROOT / "install.sh"

_STEP3 = "failed to query the GitHub releases API"
_LOST = "uv still not found after install"


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(
    tmp_path: Path,
    env_for: dict[str, str],
    uv_lands_in: str,
    *,
    stale_uv_in: str | None = None,
    unset_home: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh with uv absent; the fake Astral installer puts uv in ``uv_lands_in``.

    ``{home}`` in any value is replaced by the sandbox HOME. ``stale_uv_in``
    plants an executable ``uv`` that prints ``stale-uv`` in that directory
    before the run (off PATH, as a leftover install would be); the fresh one
    prints ``fresh-uv``. The curl stub records, at the Step 3 API call, which
    ``uv`` the script now resolves (``command -v uv``, then its output) into
    ``<tmp_path>/uv-at-step3.txt``. ``unset_home`` removes HOME from the child.
    """

    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    target = uv_lands_in.format(home=home)
    if stale_uv_in is not None:
        _write_exe(Path(stale_uv_in.format(home=home)) / "uv", "echo stale-uv\n")
    fake_installer = (
        f'mkdir -p "{target}"\n'
        f"printf '#!/bin/sh\\necho fresh-uv\\n' > \"{target}/uv\"\n"
        f'chmod +x "{target}/uv"\n'
    )
    (tmp_path / "fake-uv-installer.sh").write_text(fake_installer, encoding="utf-8")
    # `curl [opts] -o <out> <url>`: the Astral installer URL gets the fake; any
    # GitHub API call (Step 3) records which uv Step 2 left on PATH, then fails
    # like an HTTP 403 would (curl -f exits 22).
    _write_exe(
        stubs / "curl",
        'out=""; url=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) url="$1"; shift ;; esac; done\n'
        'case "$url" in\n'
        f'  https://astral.sh/uv/install.sh) cp "{tmp_path}/fake-uv-installer.sh" "$out" ;;\n'
        f'  *) {{ command -v uv; uv; }} > "{tmp_path}/uv-at-step3.txt" 2>&1; exit 22 ;;\n'
        "esac\n",
    )
    env = child_env(
        home,
        PATH=f"{stubs}{os.pathsep}/usr/bin{os.pathsep}/bin",
        **{key: value.format(home=home) for key, value in env_for.items()},
    )
    if unset_home:
        del env["HOME"]
    assert shutil.which("uv", path=env["PATH"]) is None, "uv must be absent for Step 2's bootstrap arm"
    return subprocess.run(
        ["sh", str(_SH)], env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False,
    )


@pytest.mark.parametrize(
    ("env_for", "uv_lands_in"),
    [
        pytest.param({}, "{home}/.local/bin", id="default-home-local-bin"),
        pytest.param({"XDG_BIN_HOME": "{home}/xbin"}, "{home}/xbin", id="XDG_BIN_HOME"),
        # The measured defect: a custom XDG_DATA_HOME sends uv to <it>/../bin.
        pytest.param({"XDG_DATA_HOME": "{home}/data"}, "{home}/data/../bin", id="XDG_DATA_HOME"),
        pytest.param({"UV_INSTALL_DIR": "{home}/opt/uv"}, "{home}/opt/uv", id="UV_INSTALL_DIR"),
        # UV_INSTALL_DIR wins over XDG_BIN_HOME in the Astral installer.
        pytest.param(
            {"UV_INSTALL_DIR": "{home}/opt/uv", "XDG_BIN_HOME": "{home}/xbin"},
            "{home}/opt/uv",
            id="UV_INSTALL_DIR-over-XDG_BIN_HOME",
        ),
        # A cargo-home install dir is the "cargo-home" layout: uv goes to <dir>/bin.
        pytest.param({"UV_INSTALL_DIR": "{home}/.cargo"}, "{home}/.cargo/bin", id="UV_INSTALL_DIR-cargo-home"),
        pytest.param({"UV_UNMANAGED_INSTALL": "{home}/ci-uv"}, "{home}/ci-uv", id="UV_UNMANAGED_INSTALL"),
        # Second in the Astral installer's order; flat (measured with uv's
        # installer 0.13.0: CARGO_DIST_FORCE_INSTALL_DIR=<dir> put uv in <dir>).
        pytest.param(
            {"CARGO_DIST_FORCE_INSTALL_DIR": "{home}/cd"}, "{home}/cd", id="CARGO_DIST_FORCE_INSTALL_DIR"
        ),
    ],
)
def test_step2_finds_the_bootstrapped_uv(tmp_path: Path, env_for: dict[str, str], uv_lands_in: str) -> None:
    result = _run(tmp_path, env_for, uv_lands_in)
    assert "installing uv (Astral)" in result.stdout, result.stdout + result.stderr
    assert _LOST not in result.stderr, result.stderr
    assert _STEP3 in result.stderr, result.stdout + result.stderr
    assert result.returncode == 1


def test_step2_names_every_place_it_looked_when_uv_is_nowhere(tmp_path: Path) -> None:
    """The failure message used to name ONE directory, the wrong one."""

    result = _run(tmp_path, {"XDG_DATA_HOME": "{home}/data"}, "{home}/somewhere-else")
    assert result.returncode == 1
    assert _LOST in result.stderr, result.stderr
    assert f"'{tmp_path / 'home' / 'data'}/../bin'" in result.stderr, result.stderr
    assert f"'{tmp_path / 'home'}/.local/bin'" in result.stderr, result.stderr
    assert _STEP3 not in result.stderr


def test_step2_looks_in_the_astral_installers_order(tmp_path: Path) -> None:
    """First executable uv wins, so the ORDER is what keeps a stale uv in a
    lower-precedence directory from shadowing the one just installed. The
    failure message lists the places in the order they were tried."""

    result = _run(
        tmp_path,
        {"UV_INSTALL_DIR": "{home}/opt/uv", "XDG_BIN_HOME": "{home}/xbin", "XDG_DATA_HOME": "{home}/data"},
        "{home}/somewhere-else",
    )
    assert _LOST in result.stderr, result.stderr
    home = tmp_path / "home"
    order = [f"'{home}/opt/uv'", f"'{home}/xbin'", f"'{home}/data/../bin'", f"'{home}/.local/bin'"]
    positions = [result.stderr.find(p) for p in order]
    assert -1 not in positions, (order, result.stderr)
    assert positions == sorted(positions), (order, result.stderr)


@pytest.mark.parametrize(
    ("env_for", "fresh_in", "stale_in"),
    [
        pytest.param({"UV_INSTALL_DIR": "{home}/opt/uv"}, "{home}/opt/uv", "{home}/.local/bin", id="UV_INSTALL_DIR"),
        pytest.param({"XDG_DATA_HOME": "{home}/data"}, "{home}/data/../bin", "{home}/.local/bin", id="XDG_DATA_HOME"),
        # UV_UNMANAGED_INSTALL ranks above ~/.local/bin, and
        # CARGO_DIST_FORCE_INSTALL_DIR above both ~/.local/bin and
        # XDG_BIN_HOME: a leftover uv in a lower-ranked dir must not shadow
        # the one just installed.
        pytest.param(
            {"UV_UNMANAGED_INSTALL": "{home}/ci-uv"}, "{home}/ci-uv", "{home}/.local/bin", id="UV_UNMANAGED_INSTALL"
        ),
        pytest.param(
            {"CARGO_DIST_FORCE_INSTALL_DIR": "{home}/cd"}, "{home}/cd", "{home}/.local/bin",
            id="CARGO_DIST_FORCE_INSTALL_DIR",
        ),
        pytest.param(
            {"CARGO_DIST_FORCE_INSTALL_DIR": "{home}/cd", "XDG_BIN_HOME": "{home}/xbin"}, "{home}/cd", "{home}/xbin",
            id="CARGO_DIST_FORCE_INSTALL_DIR-over-XDG_BIN_HOME",
        ),
        # Inside one named dir the flat layout is tried before the cargo-home
        # ``<dir>/bin``: a leftover ``<dir>/bin/uv`` must not shadow the uv
        # just installed flat into ``<dir>``.
        pytest.param(
            {"UV_INSTALL_DIR": "{home}/opt/uv"}, "{home}/opt/uv", "{home}/opt/uv/bin", id="flat-before-dir-bin"
        ),
    ],
)
def test_step2_first_uv_found_wins_over_a_stale_one(
    tmp_path: Path, env_for: dict[str, str], fresh_in: str, stale_in: str
) -> None:
    """The first executable uv in the Astral installer's order wins.

    A leftover ``~/.local/bin/uv`` (lowest precedence, not on PATH) must not
    shadow the uv just installed into a higher-precedence directory: Step 3 and
    every later ``uv`` call must run the fresh one. If the search kept going
    after the first hit (``break`` instead of ``break 2``), the LAST uv found
    would win and this row would see ``stale-uv``. The last row pins the order
    inside one directory (``$_d`` before ``$_d/bin``).
    """

    result = _run(tmp_path, env_for, fresh_in, stale_uv_in=stale_in)
    assert _STEP3 in result.stderr, result.stdout + result.stderr
    seen = (tmp_path / "uv-at-step3.txt").read_text(encoding="utf-8").splitlines()
    fresh = fresh_in.format(home=tmp_path / "home")
    assert seen == [f"{fresh}/uv", "fresh-uv"], seen


@pytest.mark.parametrize(
    ("env_for", "uv_lands_in"),
    [
        # The regression row: before #279 this worked, because
        # ``${XDG_BIN_HOME:-$HOME/.local/bin}`` expanded HOME only when
        # XDG_BIN_HOME was empty.
        pytest.param({"XDG_BIN_HOME": "{home}/xbin"}, "{home}/xbin", id="XDG_BIN_HOME"),
        # Never worked before #279 (UV_INSTALL_DIR was not looked at, and the
        # fallback expanded HOME); the new search must not need HOME for it.
        pytest.param({"UV_INSTALL_DIR": "{home}/opt/uv"}, "{home}/opt/uv", id="UV_INSTALL_DIR"),
    ],
)
def test_step2_does_not_need_home_when_the_install_dir_is_named(
    tmp_path: Path, env_for: dict[str, str], uv_lands_in: str
) -> None:
    """``set -u`` and no HOME: with the install dir named, Step 2 must not touch HOME.

    The #279 search list first spelled its last place as ``"$HOME/.local/bin"``
    unconditionally, so a HOME-less environment (some service managers and
    containers) died with ``HOME: unbound variable`` although the uv it had
    just installed was in the directory it was told about.
    """

    result = _run(tmp_path, env_for, uv_lands_in, unset_home=True)
    assert "unbound variable" not in result.stderr, result.stderr
    assert _STEP3 in result.stderr, result.stdout + result.stderr
    seen = (tmp_path / "uv-at-step3.txt").read_text(encoding="utf-8").splitlines()
    assert seen == [f"{uv_lands_in.format(home=tmp_path / 'home')}/uv", "fresh-uv"], seen
