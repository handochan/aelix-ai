"""#279 — CI executes install.sh, the install line the README leads with.

THE GAP THIS CLOSES. Until #279 nothing executed ``install.sh``. Its only gate
was ``tests/packaging_gate/test_install_ps1_parity.py``, regex over text, while
``install.ps1`` had an end-to-end job since #106 whose first run found two
defects every regex had passed over. #263's interpreter promise (the tool
environment is built on 3.11-3.13) was therefore proven on Windows and on POSIX
by nothing at all.

What CI runs is only worth something while it is the thing users run, so this
file holds the ``install-sh-e2e`` job to the docs and to the arms it claims:

* the install steps are README's one-liner (and the guide's knob form) with the
  one substitution that makes a branch test its own bytes — the URL;
* uv is asserted ABSENT before the first pass, so Step 2's bootstrap arm is the
  one measured, and every arm is its own step with its own assert step, so a
  red run names the arm;
* the post-condition script (``.github/scripts/assert-install-sh.sh``) is
  executed here against a stub tool environment, and each post-condition is
  shown to FIRE — a checker that cannot fail would make the job a green light.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.env_sandbox import child_env

REPO_ROOT = Path(__file__).resolve().parents[1]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ASSERT_SH = REPO_ROOT / ".github" / "scripts" / "assert-install-sh.sh"
README = REPO_ROOT / "README.md"
GUIDE = REPO_ROOT / "docs" / "guides" / "getting-started.md"

#: The published URL the docs print, and what CI substitutes for it: the
#: checked-out file, so a branch that breaks install.sh goes red on that branch.
RAW_URL = "https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh"
FILE_URL = '"file://$GITHUB_WORKSPACE/install.sh"'

#: ubuntu-latest's sh is dash; macos-latest's is bash 3.2 in POSIX mode.
RUNNERS = {"ubuntu-latest", "macos-latest"}

_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _job() -> dict[str, Any]:
    return _jobs()["install-sh-e2e"]


def _steps() -> list[dict[str, Any]]:
    return _job()["steps"]


def _install_steps() -> list[dict[str, Any]]:
    return [s for s in _steps() if str(s.get("name", "")).startswith("Install (")]


def _readme_one_liner() -> str:
    lines = [ln.strip() for ln in README.read_text(encoding="utf-8").splitlines()]
    found = [ln for ln in lines if ln.startswith("curl ") and ln.endswith("install.sh | sh")]
    assert len(found) == 1, found
    return found[0]


def _guide_knob_form() -> str:
    """The guide's spelling for passing a variable through the pipe, minus the variables."""

    text = GUIDE.read_text(encoding="utf-8")
    m = re.search(r'^\s*(sh -c "\$\(curl [^)]*install\.sh\)")\s*$', text, re.MULTILINE)
    assert m, "getting-started.md no longer shows the `sh -c \"$(curl ...)\"` knob form"
    return m.group(1)


# === the job is the README's line, run where it matters ======================


def test_the_job_runs_on_both_posix_runners() -> None:
    job = _job()
    assert job["runs-on"] == "${{ matrix.os }}"
    assert set(job["strategy"]["matrix"]["os"]) == RUNNERS
    assert job["strategy"].get("fail-fast") is False, "a red ubuntu leg must not hide the macOS one"
    assert "${{ matrix.os }}" in job["name"], "branch protection requires each leg by name"


def test_the_bootstrap_pass_and_the_rerun_are_the_readme_line() -> None:
    """Byte for byte, but for the URL. A user's ``| sh`` reads the script from a
    PIPE, which is what lets a child that reads stdin eat the rest of it; any
    other spelling (``sh install.sh``) would not measure that."""

    want = _readme_one_liner().replace(RAW_URL, FILE_URL)
    assert want != _readme_one_liner(), "the README URL changed; update RAW_URL"
    pipe_passes = [s for s in _install_steps() if "AELIX_" not in s["run"]]
    assert len(pipe_passes) == 2, [s["name"] for s in pipe_passes]
    for step in pipe_passes:
        assert step["run"].strip() == want, step["name"]


@pytest.mark.parametrize(
    ("prefix", "arm"),
    [("AELIX_EXTRAS=''", "bare CLI"), ("AELIX_PYTHON=''", "empty"), ("AELIX_PYTHON=3.11", "set")],
)
def test_the_knob_passes_are_the_guides_form(prefix: str, arm: str) -> None:
    """``VAR=x curl ... | sh`` reaches curl only; the guide teaches
    ``VAR=x sh -c "$(curl ...)"``, so that is what the knob passes run."""

    want = f"{prefix} {_guide_knob_form().replace(RAW_URL, FILE_URL)}"
    matches = [s for s in _install_steps() if s["run"].strip() == want]
    assert len(matches) == 1, (want, [s["run"] for s in _install_steps()])
    assert arm in matches[0]["name"]


def test_uv_is_asserted_absent_before_the_first_install() -> None:
    """Otherwise Step 2's bootstrap arm can go dead in CI without a sound."""

    names = [s.get("name", "") for s in _steps()]
    absent = next(i for i, n in enumerate(names) if n.startswith("uv must be ABSENT"))
    first_install = names.index(_install_steps()[0]["name"])
    assert absent < first_install
    check = _steps()[absent]["run"]
    assert "command -v uv" in check and "exit 1" in check
    assert "bootstrap" in _install_steps()[0]["name"]


def test_every_install_is_followed_by_its_own_assert() -> None:
    """One assert per pass, naming the same arm, from a SEPARATE step."""

    steps = _steps()
    arms = []
    for i, step in enumerate(steps):
        if step not in _install_steps():
            continue
        nxt = steps[i + 1]
        arm = re.fullmatch(r"Install \((.+)\)", step["name"])
        assert arm, step["name"]
        assert nxt["name"].startswith("Assert what the script promised"), nxt["name"]
        assert nxt["run"].strip() == 'sh "$GITHUB_WORKSPACE/.github/scripts/assert-install-sh.sh"'
        assert nxt["env"]["E2E_ARM"].split(" - ")[0] == arm.group(1).split(" - ")[0], (step["name"], nxt["env"])
        arms.append(nxt["env"])
    assert len(arms) == 5
    assert len({a["E2E_ARM"] for a in arms}) == 5, "two passes share an arm name"
    # The knobs the asserts expect match what the passes set.
    by_arm = {a["E2E_ARM"]: a for a in arms}
    bare = next(a for k, a in by_arm.items() if "AELIX_EXTRAS=" in k)
    assert bare["E2E_EXPECT_EXTRAS"] == ""
    pinned = next(a for k, a in by_arm.items() if "AELIX_PYTHON=3.11" in k)
    assert pinned["E2E_EXPECT_PYTHON"] == "3.11"
    for k, a in by_arm.items():
        if a is not bare:
            assert "E2E_EXPECT_EXTRAS" not in a, k
        if a is not pinned:
            assert "E2E_EXPECT_PYTHON" not in a, k


def test_the_empty_python_pass_runs_with_the_trap_armed() -> None:
    """An ignored ``--python ""`` and the default agree on a box whose newest
    interpreter is inside the range; 3.14 must be on the box first."""

    names = [s.get("name", "") for s in _steps()]
    arm = next(i for i, n in enumerate(names) if n.startswith("Put Python 3.14 on the box"))
    empty = next(i for i, n in enumerate(names) if n.startswith("Install (pass 4 - AELIX_PYTHON="))
    assert arm < empty
    assert "uv python install 3.14" in _steps()[arm]["run"]
    assert '!= "3.14"' in _steps()[arm]["run"] and "exit 1" in _steps()[arm]["run"]


def test_installs_run_outside_the_checkout_under_pipefail() -> None:
    job = _job()
    assert job["defaults"]["run"]["shell"] == "bash", (
        "an explicit `shell: bash` is what adds -o pipefail; without it a failed "
        "curl hands sh an empty script and the step is green"
    )
    for step in _steps():
        if step.get("name", "").startswith(("Install (", "Assert what")):
            assert step.get("working-directory") == "${{ runner.temp }}", step["name"]


def test_no_setup_uv_and_every_action_pinned_by_sha() -> None:
    for job_id, job in _jobs().items():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses is None:
                continue
            assert _PINNED_ACTION.match(uses.split(" ")[0]), (job_id, uses)
    assert not any(
        str(s.get("uses", "")).startswith("astral-sh/setup-uv@") for s in _steps()
    ), "setup-uv would put uv on PATH and kill the bootstrap arm"


# === the post-condition script can fail ======================================

_SHELLS = [s for s in ("sh", "dash") if shutil.which(s)]

_VERSION = "0.1.0b2"
_SUMS = (
    f"{'a' * 64}  aelix-{_VERSION}-py3-none-any.whl\n"
    f"{'b' * 64}  aelix_ai-{_VERSION}-py3-none-any.whl\n"
)


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _Box:
    """A stub of what install.sh leaves behind: uv, gh, curl, the tool env."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.stub_bin = root / "stubs"
        self.tool_dir = root / "tools"
        self.bin_dir = root / "toolbin"
        self.env_dir = self.tool_dir / "aelix"
        self.state = root / "state"
        self.state.mkdir(parents=True)
        # Post-condition 4 asks `mktemp -d` where install.sh's download dir
        # would be and scans that directory. The real answer is shared by every
        # process of this user (the per-user temp dir on macOS, /tmp on Linux),
        # so a concurrent install.sh - another test, another checkout's suite -
        # would be a "leftover" here. This stand-in answers with a directory of
        # the box's own and, like macOS's mktemp, ignores TMPDIR.
        self.temp_root = root / "mktemp-root"
        self.temp_root.mkdir()
        real_mktemp = shutil.which("mktemp") or "/usr/bin/mktemp"
        _write_exe(self.stub_bin / "mktemp", f'exec "{real_mktemp}" -d "{self.temp_root}/tmp.XXXXXXXX"\n')
        self._req = ""
        self._python_line = ""
        _write_exe(
            self.stub_bin / "uv",
            f'case "$*" in\n'
            f'  "tool dir") echo "{self.tool_dir}" ;;\n'
            f'  "tool dir --bin") echo "{self.bin_dir}" ;;\n'
            f'  "tool list") printf \'aelix v%s\\n- aelix\\n\' "$(cat "{root}/listed")" ;;\n'
            f'  *) exit 2 ;;\n'
            f'esac\n',
        )
        _write_exe(self.stub_bin / "gh", "echo v0.1.0-beta.2\n")
        (root / "sums").write_text(_SUMS, encoding="utf-8")
        _write_exe(self.stub_bin / "curl", f'cat "{root}/sums"\n')
        self.set_version(_VERSION)
        self.set_python("3.13.13", '">=3.11, <3.14"')
        self.set_extras(tui=True)

    def set_version(self, version: str, *, listed: str | None = None) -> None:
        """``aelix --version`` prints ``version``; ``uv tool list`` reports ``listed`` (default: the same)."""

        _write_exe(self.bin_dir / "aelix", f"echo {version}\n")
        (self.root / "listed").write_text(version if listed is None else listed, encoding="utf-8")

    def set_python(self, version_info: str, recorded: str) -> None:
        self.env_dir.mkdir(parents=True, exist_ok=True)
        (self.env_dir / "pyvenv.cfg").write_text(
            f"home = /x/bin\nimplementation = CPython\nversion_info = {version_info}\n", encoding="utf-8"
        )
        self._python_line = f"python = {recorded}\n"
        self._write_receipt()

    def set_extras(self, *, tui: bool, importable: bool | None = None) -> None:
        self._req = (
            f'requirements = [{{ name = "aelix", extras = ["tui"], specifier = "=={_VERSION}" }}]\n'
            if tui
            else f'requirements = [{{ name = "aelix", specifier = "=={_VERSION}" }}]\n'
        )
        importable = tui if importable is None else importable
        _write_exe(self.env_dir / "bin" / "python", "exit 0\n" if importable else "exit 1\n")
        self._write_receipt()

    def _write_receipt(self) -> None:
        (self.env_dir / "uv-receipt.toml").write_text("[tool]\n" + self._req + self._python_line, encoding="utf-8")

    def run(self, shell: str, *, path_head: list[Path] | None = None, **knobs: str) -> subprocess.CompletedProcess[str]:
        path = os.pathsep.join(str(p) for p in [*(path_head or []), self.stub_bin, self.bin_dir]) + os.pathsep + "/usr/bin:/bin"
        env = child_env(self.root, PATH=path, E2E_STATE_DIR=str(self.state), E2E_ARM="stub arm", **knobs)
        return subprocess.run(
            [shutil.which(shell) or shell, str(ASSERT_SH)], env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60, check=False,
        )


def _failed(result: subprocess.CompletedProcess[str], post: str) -> bool:
    return result.returncode == 1 and f"::error::install.sh e2e [stub arm] post-condition {post}:" in result.stdout


pytestmark_posix = pytest.mark.skipif(
    sys.platform == "win32",
    reason="assert-install-sh.sh is POSIX sh and runs only on the ubuntu/macOS legs of install-sh-e2e",
)


@pytestmark_posix
@pytest.mark.parametrize("shell", _SHELLS)
def test_the_assert_script_passes_a_good_install(tmp_path: Path, shell: str) -> None:
    box = _Box(tmp_path)
    result = box.run(shell)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all post-conditions hold" in result.stdout
    assert (box.state / "aelix-e2e-asserted").exists()


@pytestmark_posix
@pytest.mark.parametrize("shell", _SHELLS)
def test_each_post_condition_fires(tmp_path: Path, shell: str) -> None:
    """Every check the job relies on, made false one at a time."""

    # (1) another aelix earlier on PATH than the uv tool launcher.
    box = _Box(tmp_path / "p1")
    _write_exe(tmp_path / "p1" / "other" / "aelix", f"echo {_VERSION}\n")
    assert _failed(box.run(shell, path_head=[tmp_path / "p1" / "other"]), "1")

    # (1) no launcher at all.
    box = _Box(tmp_path / "p1b")
    (box.bin_dir / "aelix").unlink()
    assert _failed(box.run(shell), "1")

    # (2) the installed version is not the one SHA256SUMS named. Each witness
    # is made false ALONE, so dropping either check goes red: first the
    # launcher's `--version` (uv's record agrees with SHA256SUMS) ...
    box = _Box(tmp_path / "p2")
    box.set_version("0.1.0b1", listed=_VERSION)
    assert _failed(box.run(shell), "2")

    # ... then uv's own record (`uv tool list`), with `--version` agreeing.
    box = _Box(tmp_path / "p2b")
    box.set_version(_VERSION, listed="0.1.0b1")
    assert _failed(box.run(shell), "2")

    # (3) the environment landed on 3.14 - the #262 crash.
    box = _Box(tmp_path / "p3")
    box.set_python("3.14.5", '">=3.11, <3.14"')
    assert _failed(box.run(shell), "3")

    # (3) below the default range's floor: 3.10 under the default request.
    box = _Box(tmp_path / "p3f")
    box.set_python("3.10.18", '">=3.11, <3.14"')
    assert _failed(box.run(shell), "3")

    # (3) an empty AELIX_PYTHON reached uv: no request recorded, though the
    # interpreter happened to land inside the range.
    box = _Box(tmp_path / "p3b")
    box.set_python("3.13.13", '""')
    assert _failed(box.run(shell), "3")

    # (3) AELIX_PYTHON=3.11 asked for, 3.13 delivered. Each witness made false
    # ALONE, as for (2): first the interpreter (uv recorded the request '3.11'
    # correctly, the environment is on 3.13) ...
    box = _Box(tmp_path / "p3c")
    box.set_python("3.13.13", '"3.11"')
    assert _failed(box.run(shell, E2E_EXPECT_PYTHON="3.11"), "3")

    # ... then the recorded request (the environment is on 3.11, but uv
    # recorded the default range, not the '3.11' the pass asked for).
    box = _Box(tmp_path / "p3c2")
    box.set_python("3.11.15", '">=3.11, <3.14"')
    assert _failed(box.run(shell, E2E_EXPECT_PYTHON="3.11"), "3")

    # (3b) AELIX_EXTRAS= asked for the bare CLI. Each witness ALONE: first uv
    # recorded [tui] in the receipt, though prompt_toolkit is not importable ...
    box = _Box(tmp_path / "p3d")
    box.set_extras(tui=True, importable=False)
    assert _failed(box.run(shell, E2E_EXPECT_EXTRAS=""), "3")

    # ... then the receipt is bare, but prompt_toolkit is importable.
    box = _Box(tmp_path / "p3d2")
    box.set_extras(tui=False, importable=True)
    assert _failed(box.run(shell, E2E_EXPECT_EXTRAS=""), "3")

    # (3b) the default tui pass, each witness ALONE: first [tui] recorded but
    # its dependency is not importable ...
    box = _Box(tmp_path / "p3e")
    box.set_extras(tui=True, importable=False)
    assert _failed(box.run(shell), "3")

    # ... then prompt_toolkit is importable, but uv recorded no [tui] extra.
    box = _Box(tmp_path / "p3g")
    box.set_extras(tui=False, importable=True)
    assert _failed(box.run(shell), "3")

    # (0) the install step before this assert did nothing: the receipt is
    # older than the previous assert's marker.
    box = _Box(tmp_path / "p0")
    assert box.run(shell).returncode == 0
    receipt = box.env_dir / "uv-receipt.toml"
    marker = box.state / "aelix-e2e-asserted"
    old = marker.stat().st_mtime - 60
    os.utime(receipt, (old, old))
    assert _failed(box.run(shell), "0")


@pytestmark_posix
@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize("version_info", ["3.11.15", "3.12.12", "3.13.13"])
def test_every_minor_inside_the_default_range_passes(tmp_path: Path, shell: str, version_info: str) -> None:
    """Both ends of ``>=3.11,<3.14`` under the default request. The floor and
    the ceiling are each compared once; an off-by-one in the floor (``-le``
    for ``-lt``) would reject 3.11, the range's own first minor, and turn a
    good install red."""

    box = _Box(tmp_path)
    box.set_python(version_info, '">=3.11, <3.14"')
    result = box.run(shell)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"tool interpreter: Python {version_info.rsplit('.', 1)[0]}" in result.stdout


@pytestmark_posix
@pytest.mark.parametrize("shell", _SHELLS)
def test_a_leftover_download_dir_fires(tmp_path: Path, shell: str) -> None:
    """(4) install.sh's `trap cleanup` did not run. The leftover is planted
    where `mktemp -d` puts things, exactly as install.sh would have made it,
    and TMPDIR points somewhere else.

    The box's ``mktemp`` ignores TMPDIR, as macOS's does for a bare ``-d``
    (the per-user temp dir wins), so a checker that scanned ``$TMPDIR``
    instead of asking mktemp looks in the wrong place and passes - on every
    host, not only on a Mac, where the real mktemp would have shown it."""

    box = _Box(tmp_path)
    elsewhere = tmp_path / "tmpdir"
    elsewhere.mkdir()
    leftover = box.temp_root / "tmp.aelix279"
    leftover.mkdir()
    (leftover / ".wheels").write_text("aelix-0.1.0b2-py3-none-any.whl\n", encoding="utf-8")
    result = box.run(shell, TMPDIR=str(elsewhere))
    assert _failed(result, "4"), result.stdout + result.stderr
    assert leftover.name in result.stdout
    shutil.rmtree(leftover)
    result = box.run(shell, TMPDIR=str(elsewhere))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"temp dir cleaned under {box.temp_root}" in result.stdout


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed on this host")
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a Windows checkout may carry CRLF, which shellcheck rejects; sh only ever reads the POSIX runners' copy",
)
def test_the_assert_script_is_shellcheck_clean() -> None:
    result = subprocess.run(
        ["shellcheck", "-s", "sh", str(ASSERT_SH)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_assert_script_is_posix_sh() -> None:
    """A fallback for hosts without shellcheck, which flags these itself under ``-s sh``."""

    text = ASSERT_SH.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "#!/bin/sh"  # splitlines: a Windows checkout may carry CRLF
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for bashism in (r"\[\[ ", r"(?m)^\s*local ", r"\bfunction\s+\w+", r"\$'", r"<<<", r"\bpipefail\b"):
        assert not re.search(bashism, code), bashism
