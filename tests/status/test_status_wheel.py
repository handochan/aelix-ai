"""``aelix_status`` must be IN THE WHEEL, not merely in the checkout (#101).

WHY THIS TEST IS BUILT AND NOT READ. ``aelix_status`` is a second top-level
import package inside the ``aelix-coding-agent`` distribution, and hatchling
ships only what ``[tool.hatch.build.targets.wheel] packages`` names. Omit the
entry and every test in this repo still passes — the source tree is on
``PYTHONPATH``, ``import aelix_status`` works, the extension loads, the tool
answers — while every ``pip install aelix-coding-agent`` user gets an
``ImportError`` at startup. ADR-0197 hit exactly this with ``aelix_agents`` and
left the shout in the pyproject comment: "WITHOUT THIS SECOND ENTRY THE EXTENSION
IS ABSENT FROM THE WHEEL".

Asserting against the pyproject config instead would be circular: it would check
that the line I wrote says what I wrote. The wheel is the artifact users get, so
the wheel is what gets opened.

SHOWN FAILING FIRST. Run against the tree with the package directory created and
``packages = ["src/aelix_coding_agent", "src/aelix_agents"]`` still unedited:

    FAILED test_status_package_ships_in_the_wheel - AssertionError: aelix_status
    is absent from the wheel ... nearest entries: []

Modelled on ``tests/cli/test_agent_context.py::
test_prompt_pointer_paths_ship_in_the_built_wheel``, including its skip: a CI
image with no builder must not fail spuriously, but if it skips everywhere the
packaging guarantee is unverified.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import aelix_status
import pytest

# Every module a consumer imports, plus the marker that makes the package's
# annotations visible to a type checker. ``py.typed`` is listed because it is
# the one file whose absence is invisible at runtime — the package would import
# fine and silently type as ``Any`` for every downstream user.
REQUIRED_FILES = (
    "aelix_status/__init__.py",
    "aelix_status/extension.py",
    "aelix_status/snapshot.py",
    "aelix_status/py.typed",
)


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> set[str]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("no `uv` on PATH; cannot build a wheel to inspect")

    # ``.../src/aelix_status/__init__.py`` -> ``packages/aelix-coding-agent``.
    pkg_root = Path(aelix_status.__file__).resolve().parents[2]
    assert (pkg_root / "pyproject.toml").is_file(), pkg_root

    out_dir = tmp_path_factory.mktemp("wheel")
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=pkg_root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build.returncode == 0, build.stderr

    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        return set(zf.namelist())


def test_status_package_ships_in_the_wheel(wheel_names: set[str]) -> None:
    missing = [name for name in REQUIRED_FILES if name not in wheel_names]
    nearest = sorted(n for n in wheel_names if n.startswith("aelix_status"))
    assert not missing, (
        f"aelix_status is absent from the wheel: {missing}. Add "
        '"src/aelix_status" to [tool.hatch.build.targets.wheel] packages in '
        f"packages/aelix-coding-agent/pyproject.toml. nearest entries: {nearest}"
    )


def test_the_sibling_packages_did_not_regress(wheel_names: set[str]) -> None:
    """The other two roots must still be there.

    Paired with the test above because ``packages`` is a LIST and the edit that
    adds an entry is one keystroke away from replacing one. A wheel carrying
    ``aelix_status`` and nothing else would satisfy the assertion above.
    """

    for anchor in (
        "aelix_coding_agent/__init__.py",
        "aelix_agents/__init__.py",
    ):
        assert anchor in wheel_names, anchor
