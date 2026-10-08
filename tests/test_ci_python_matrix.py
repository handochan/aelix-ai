"""#192 — CI runs every interpreter the installers can hand a user, on both runners.

THE GAP THIS CLOSES. ``install.sh`` and ``install.ps1`` build the tool
environment with ``uv tool install --python '>=3.11,<3.14'`` (ADR-0241), and uv
takes the NEWEST interpreter in that range — 3.13 on any machine that has it or
can download it. Until 2026-10-08 the test matrix was 3.11 and 3.12, so the
interpreter users were actually given had never been executed by CI. It cost a
real user first: 3.13 turns on ``X509_V_FLAG_X509_STRICT``, and aelix's TLS
remedy on a strict interpreter told every non-strict failure (an untrusted
root, a bad signature) that it was a strict-only rejection. At 8f7d98aa a 3.13
run failed eleven tests no 3.11 or 3.12 leg could see: nine on that remedy,
and two guards in test_tls_strict.py that asserted a pre-3.13 interpreter.

So the matrix is not a list someone remembers to bump. It is DERIVED from the
installer's default request here: every minor version that request admits must
be a leg, on every runner, and nothing outside it may pose as coverage. Widening
the installer without adding the leg goes red, and so does dropping a leg.

The interpreter request is read from ``install.sh``'s assignment line;
``tests/packaging_gate/test_install_ps1_parity.py`` already holds
``install.ps1`` to the same value.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import SpecifierSet

REPO_ROOT = Path(__file__).resolve().parents[1]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: Both runners the test job claims to cover; a version that runs on only one of
#: them is the gap this file exists for, half-closed.
RUNNERS = {"ubuntu-latest", "windows-latest"}

#: Far enough past any interpreter this project could meet that a range ending
#: beyond it would be a range with no ceiling at all, which the installer must
#: never have (ADR-0241: an open top lands on 3.14 and #262).
_SEARCH = range(0, 40)


def _test_job() -> dict[str, Any]:
    doc = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return doc["jobs"]["test"]


def _installer_request() -> str:
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    # ``\s*$``, not ``$``: a windows checkout may carry CRLF, and ``$`` stops
    # before ``\n`` only, so the ``\r`` would hide the line on that leg.
    match = re.search(r'^AELIX_PYTHON="\$\{AELIX_PYTHON:-([^}]*)\}"\s*$', text, re.MULTILINE)
    assert match, "install.sh no longer assigns AELIX_PYTHON with a ':-' default"
    return match[1]


def _minors(spec: str) -> list[str]:
    allowed = SpecifierSet(spec)
    found = [f"3.{n}" for n in _SEARCH if allowed.contains(f"3.{n}")]
    assert found, f"{spec!r} admits no CPython 3 minor at all"
    assert f"3.{_SEARCH[-1]}" not in found, f"{spec!r} has no ceiling"
    return found


def test_the_installer_range_is_what_it_is_today() -> None:
    """Guard on the derivation itself: 3.11, 3.12 and 3.13, nothing else.

    If this goes red the installer moved; the matrix row below then says what
    the matrix has to become."""

    assert _minors(_installer_request()) == ["3.11", "3.12", "3.13"]


def test_the_matrix_is_exactly_the_installer_range() -> None:
    matrix = _test_job()["strategy"]["matrix"]
    legs = [str(v) for v in matrix["python-version"]]
    want = _minors(_installer_request())
    assert sorted(legs, key=lambda v: tuple(map(int, v.split(".")))) == want, (
        f"ci.yml runs {legs}, but install.sh / install.ps1 can build a user's "
        f"environment on any of {want} (uv takes the newest one it finds). "
        "An interpreter users get and CI never ran is issue 192."
    )
    assert len(set(legs)) == len(legs), f"a version is listed twice: {legs}"


def test_every_version_runs_on_both_runners() -> None:
    """No ``include`` / ``exclude`` may carve a version off one runner.

    Windows 3.13 is the leg most likely to differ (``time.monotonic`` changes
    clock there), so "3.13 on ubuntu only" must not read as 3.13 covered."""

    job = _test_job()
    assert job["runs-on"] == "${{ matrix.os }}"
    matrix = job["strategy"]["matrix"]
    assert set(matrix["os"]) == RUNNERS, matrix["os"]
    assert "exclude" not in matrix, matrix.get("exclude")
    assert "include" not in matrix, matrix.get("include")
    assert job["strategy"].get("fail-fast") is False, (
        "fail-fast would cancel the other legs on the first red one, and a red "
        "3.13 leg must not hide whether 3.11/3.12 are green"
    )


def test_the_matrix_floor_is_every_package_requires_python_floor() -> None:
    """The bottom leg is the oldest interpreter the packages accept."""

    floor = _minors(_installer_request())[0]
    for pyproject in [REPO_ROOT / "pyproject.toml", *sorted(REPO_ROOT.glob("packages/*/pyproject.toml"))]:
        spec = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["requires-python"]
        admitted = [f"3.{n}" for n in _SEARCH if SpecifierSet(spec).contains(f"3.{n}")]
        assert admitted[0] == floor, (pyproject.relative_to(REPO_ROOT).as_posix(), spec, floor)


def test_the_contributor_interpreter_is_one_of_the_legs() -> None:
    """``.python-version`` is what ``uv sync`` gives a contributor."""

    pinned = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    legs = [str(v) for v in _test_job()["strategy"]["matrix"]["python-version"]]
    assert pinned in legs, (pinned, legs)


def test_each_leg_has_its_own_check_name() -> None:
    """Branch protection requires checks BY NAME, so two legs sharing one would
    let a single green one satisfy both."""

    name = _test_job()["name"]
    assert "${{ matrix.os }}" in name and "${{ matrix.python-version }}" in name, name
