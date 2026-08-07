"""``install.ps1`` must not drift from ``install.sh`` (#106).

The Windows installer cannot be executed here — there is no PowerShell on the
Linux CI box, and no windows-latest leg yet — so it is unverifiable by running
it. What IS verifiable, and what actually matters for a script whose job is a
security gate, is that it still makes the same promises as the POSIX installer:
the same configuration surface, the same checksum gate, the same uv invocation.

These tests are a drift alarm, not a substitute for executing the script. The
remaining risk is recorded in SLICE-STATUS.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SH = _REPO_ROOT / "install.sh"
_PS1 = _REPO_ROOT / "install.ps1"


@pytest.fixture(scope="module")
def sh() -> str:
    return _SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1() -> str:
    return _PS1.read_text(encoding="utf-8")


def test_installer_exists() -> None:
    assert _PS1.is_file(), "install.ps1 is the first-party Windows installer (#106)"


def test_marked_experimental(ps1: str) -> None:
    """A script with no live coverage must say so where a user will see it."""

    head = "\n".join(ps1.splitlines()[:12])
    assert "EXPERIMENTAL" in head


# === same configuration surface =============================================


_ENV_VARS = ["AELIX_VERSION", "AELIX_EXTRAS", "AELIX_REPO", "UV_VERSION", "GITHUB_TOKEN"]


@pytest.mark.parametrize("name", _ENV_VARS)
def test_same_env_vars(sh: str, ps1: str, name: str) -> None:
    assert name in sh
    assert name in ps1


def test_no_extra_env_vars_in_ps1(ps1: str) -> None:
    """A knob only Windows honours is drift; add it to both or neither."""

    found = {
        m for m in re.findall(r"\$env:([A-Z][A-Z0-9_]+)", ps1)
    } - {"PATH", "USERPROFILE", "UV_INSTALL_VERSION"}
    assert found == set(_ENV_VARS), found


@pytest.mark.parametrize(
    ("var", "default"),
    [("AELIX_EXTRAS", "tui"), ("AELIX_REPO", "handochan/aelix-ai")],
)
def test_same_defaults(sh: str, ps1: str, var: str, default: str) -> None:
    assert f"{{{var}-{default}}}" in sh  # ${AELIX_EXTRAS-tui}
    assert re.search(rf"\$env:{var}.*'{re.escape(default)}'", ps1) is not None


# === the checksum gate is the point of both scripts =========================


def test_verifies_against_sha256sums(ps1: str) -> None:
    assert "SHA256SUMS" in ps1
    assert "Get-FileHash" in ps1
    assert "SHA256" in ps1


def test_aborts_on_mismatch(ps1: str) -> None:
    """Not a warning — the install must stop."""

    assert "SECURITY: checksum mismatch" in ps1
    assert "SECURITY: checksum mismatch" in _SH.read_text(encoding="utf-8")


def test_aborts_when_a_wheel_is_absent_from_the_manifest(sh: str, ps1: str) -> None:
    """An unlisted wheel must not install unverified."""

    for text in (sh, ps1):
        assert "is absent from SHA256SUMS" in text


def test_hash_comparison_is_case_insensitive(ps1: str) -> None:
    """``Get-FileHash`` returns UPPERCASE; the manifest is lowercase.

    A case-sensitive ``-ne`` here would reject every correct wheel — the gate
    would fail closed, but nobody could install at all.
    """

    assert "-ine" in ps1 or "-ieq" in ps1


def test_only_first_party_wheels_are_fetched(sh: str, ps1: str) -> None:
    assert "aelix*.whl" in sh
    assert "aelix*.whl" in ps1


# === the uv invocation ======================================================


def test_same_uv_install_flags(sh: str, ps1: str) -> None:
    for text in (sh, ps1):
        assert "uv tool install --force --find-links" in text


def _code_lines(text: str) -> list[str]:
    """Drop whole-line comments. Both scripts comment with a leading ``#``."""

    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_never_uses_no_index(sh: str, ps1: str) -> None:
    """``--no-index`` would make third-party dependencies unresolvable.

    Both files DISCUSS it in comments, so only executable lines are checked.
    """

    for text in (sh, ps1):
        assert not [line for line in _code_lines(text) if "--no-index" in line]


def test_ps1_carries_no_version_pin_just_like_sh(sh: str, ps1: str) -> None:
    """Measured parity, deliberately preserved.

    ``install.sh`` pins the release TAG (which wheels are downloaded and
    checksum-verified) but does NOT pin the package version handed to uv. The
    Windows script must not quietly invent a different resolution policy; if
    the pin is added it belongs in both at once. See the note in install.ps1.
    """

    assert "aelix[$AELIX_EXTRAS]" in sh
    assert 'aelix[$AelixExtras]' in ps1
    for text in (sh, ps1):
        assert "aelix==" not in text


# === crude syntax sanity (no PowerShell available here) =====================


def test_braces_balance(ps1: str) -> None:
    """Cheap structural check: no comment or string in this file contains a
    brace, so a mismatch is a real unclosed block."""

    assert ps1.count("{") == ps1.count("}")
    assert ps1.count("(") == ps1.count(")")


def test_cleanup_is_in_a_finally(ps1: str) -> None:
    """The temp dir holds downloaded wheels; a failed install must not leave them."""

    assert re.search(r"finally\s*\{[^}]*Remove-Item", ps1, re.DOTALL) is not None
