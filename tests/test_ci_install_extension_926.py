"""#926 (ADR-0321): CI holds that a re-run of the install line keeps an extension.

The ``install-sh-e2e`` and ``install-ps1-e2e`` jobs install an extension the way a
user does after pass 1 (the starter that ships inside aelix, committed to a local
git repository and installed at that commit), and pass 2 — the re-run, the
documented upgrade — must carry it over: still discovered, named in the new
receipt, and kept by ``uv tool upgrade aelix``. This file holds the jobs to that
shape and shows that the two new post-conditions of ``assert-install-sh.sh`` can
fail.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_ci_install_sh_e2e import _SHELLS, CI, _Box, _failed, _write_exe

_EXT = "aelix-starter-ext"


def _job(name: str) -> list[dict[str, Any]]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"][name]["steps"]


@pytest.mark.parametrize("job", ["install-sh-e2e", "install-ps1-e2e"])
def test_an_extension_is_added_before_the_rerun_and_asserted_after_it(job: str) -> None:
    steps = _job(job)
    names = [s.get("name", "") for s in steps]
    add = names.index("Add a test extension the way a user does (#926)")
    rerun = next(i for i, n in enumerate(names) if n.startswith("Install (pass 2"))
    assert names[add - 1].startswith("Assert what the script promised (pass 1")
    assert add + 1 == rerun
    run = steps[add]["run"]
    assert "examples" in run and "starter" in run, (
        "the fixture is the starter that ships inside aelix"
    )
    assert "aelix extension install" in run and "git+file://" in run and "--yes" in run
    after = steps[rerun + 1]
    assert after["name"].startswith("Assert what the script promised (pass 2")
    assert after["env"]["E2E_EXPECT_EXTENSION"] == _EXT
    assert after["env"]["E2E_EXPECT_UPGRADE_KEEPS"] == "1"
    others = [s for s in steps if s is not after and "E2E_EXPECT_EXTENSION" in (s.get("env") or {})]
    assert others == []


def _with_extension(
    box: _Box, *, listed: bool = True, in_receipt: bool = True, upgrade: str = "keep"
) -> None:
    """The stub aelix lists the extension until a stub ``uv tool upgrade`` drops it."""

    flag = box.root / "dropped"
    line = (
        f"  aelix-starter [{_EXT} 0.1.0]"
        if listed
        else "No extensions installed (via entry_points)."
    )
    _write_exe(
        box.bin_dir / "aelix",
        f'case "$1" in\n'
        f'  --version) cat "{box.root}/version" ;;\n'
        f'  extension) if [ -e "{flag}" ]; then echo "No extensions installed."; else echo "{line}"; fi ;;\n'
        f"esac\n",
    )
    (box.root / "version").write_text(box.release + "\n", encoding="utf-8")
    links = box.root / "deleted-find-links"
    receipt = box.env_dir / "uv-receipt.toml"
    text = receipt.read_text(encoding="utf-8")
    if in_receipt:
        text = text.replace(
            "requirements = [",
            f'requirements = [{{ name = "{_EXT}", git = "file:///x?rev=abc" }}, ',
            1,
        )
    receipt.write_text(
        text + f'\n[tool.options]\nfind-links = ["file://{links}"]\n', encoding="utf-8"
    )
    upgrade_body = {
        "keep": f'[ -d "{links}" ] || {{ echo "Failed to read --find-links directory"; exit 1; }}; echo "Nothing to upgrade"',
        "drop": f'touch "{flag}"; echo "Updated aelix"',
        "fail": 'echo "error: no"; exit 1',
    }[upgrade]
    stub = (box.stub_bin / "uv").read_text(encoding="utf-8")
    stub = stub.replace(
        "  *) exit 2 ;;", f'  "tool upgrade aelix") {upgrade_body} ;;\n  *) exit 2 ;;'
    )
    (box.stub_bin / "uv").write_text(stub, encoding="utf-8")


_POSIX = pytest.mark.skipif(sys.platform == "win32", reason="assert-install-sh.sh is POSIX sh")
_KNOBS = {"E2E_EXPECT_EXTENSION": _EXT, "E2E_EXPECT_UPGRADE_KEEPS": "1"}


@_POSIX
@pytest.mark.parametrize("shell", _SHELLS)
def test_a_carried_extension_passes(tmp_path: Path, shell: str) -> None:
    box = _Box(tmp_path)
    _with_extension(box)
    result = box.run(shell, **_KNOBS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"extension carried over: {_EXT} (discovered, and in the receipt)" in result.stdout
    assert f"uv tool upgrade aelix kept {_EXT} (Nothing to upgrade)" in result.stdout
    # The find-links directory made for the upgrade is gone again (post-condition 4).
    assert not (tmp_path / "deleted-find-links").exists()


@_POSIX
@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize(
    ("setup", "post"),
    [
        ({"listed": False}, "5"),
        ({"in_receipt": False}, "5"),
        ({"upgrade": "drop"}, "6"),
        ({"upgrade": "fail"}, "6"),
    ],
)
def test_each_extension_post_condition_fires(
    tmp_path: Path, shell: str, setup: dict[str, Any], post: str
) -> None:
    box = _Box(tmp_path)
    _with_extension(box, **setup)
    assert _failed(box.run(shell, **_KNOBS), post)


@_POSIX
@pytest.mark.parametrize("shell", _SHELLS)
def test_a_pass_without_the_knob_does_not_look_for_an_extension(tmp_path: Path, shell: str) -> None:
    box = _Box(tmp_path)
    _with_extension(box, listed=False, in_receipt=False, upgrade="fail")
    assert box.run(shell).returncode == 0
