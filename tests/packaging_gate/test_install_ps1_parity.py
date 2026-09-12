"""``install.ps1`` must not drift from ``install.sh`` (#106).

The Windows installer cannot be executed here — there is no PowerShell on the
Linux CI box — so it is unverifiable by running it. What IS verifiable, and what
actually matters for a script whose job is a security gate, is that it still
makes the same promises as the POSIX installer: the same configuration surface,
the same checksum gate, the same uv invocation.

These tests are a drift alarm, not a substitute for executing the script, and
the ``install.ps1 e2e`` job in .github/workflows/ci.yml now does the executing
on both Windows hosts. Its first run (33862346729) found two defects that every
assertion in this file passed straight over — the byte[] response body and the
CP1252 em dash below — which is the calibration for how much this file proves.
"""

from __future__ import annotations

import os
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


_ENV_VARS = [
    "AELIX_VERSION",
    "AELIX_EXTRAS",
    "AELIX_REPO",
    "AELIX_PYTHON",
    "UV_VERSION",
    "GITHUB_TOKEN",
]

# The interpreter range both installers request. Kept here as ONE constant so a
# drift between the two scripts fails as a parity error rather than as a user
# report from whichever platform was edited second (#263).
_PY_REQUEST = ">=3.11,<3.14"


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
    [
        ("AELIX_EXTRAS", "tui"),
        ("AELIX_REPO", "handochan/aelix-ai"),
    ],
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


def test_both_installers_constrain_the_interpreter(sh: str, ps1: str) -> None:
    """#263 -- ``uv tool install`` consults neither ``.python-version`` nor
    ``uv.lock``; it resolves an interpreter fresh and takes the newest it finds.
    Measured on a box carrying 3.11 through 3.14, the unflagged command built the
    tool environment on 3.14.5, where ``openai<2.0`` raises
    ``'typing.Union' object has no attribute '__discriminator__'`` mid-turn
    (#262).

    SABOTAGE: drop ``--python`` from either script. The platform that kept it
    installs a tested interpreter and the other ships the crash, which is exactly
    the one-sided drift this file exists to catch -- so the assertion is on BOTH,
    and on the same request.
    """

    # ON THE INVOCATION LINE, not anywhere in the file. A review sabotaged the
    # first version of this test by deleting the flag from the real command and
    # parking the string in a TRAILING comment on an unrelated line --
    # `_code_lines` drops whole-line comments only, `sh -n` still said OK, and
    # the suite came back byte-identical to baseline. The line is the thing that
    # runs, so the line is the thing asserted.
    for name, text, prefix, want in (
        ("install.sh", sh, "uv tool install", '--python "$AELIX_PYTHON"'),
        ("install.ps1", ps1, "& uv tool install", "--python $AelixPython"),
    ):
        hits = [ln.strip() for ln in _code_lines(text) if ln.strip().startswith(prefix)]
        assert len(hits) == 1, f"{name}: expected one {prefix!r} line, found {hits}"
        # The flag must carry the KNOB, not a hard-coded literal: a literal would
        # silently win over AELIX_PYTHON and make the documented override a lie.
        assert want in hits[0], f"{name}: {want!r} is not on the invocation line: {hits[0]!r}"


def test_an_empty_python_request_does_not_disarm_the_gate(sh: str, ps1: str) -> None:
    """``uv tool install --python ""`` does NOT fail. uv ignores an empty request
    and goes back to the newest interpreter -- measured: exit 0, environment on
    3.14.5, which is the state #263 exists to prevent.

    That is reachable by accident rather than by malice: the sibling knobs use
    ``${VAR-default}`` because a set-but-empty value is meaningful for them
    (``AELIX_EXTRAS=`` installs the bare CLI, and the README teaches that
    spelling), so the same habit applied here would silently turn the gate off.

    Asserted by EXECUTION, not by substring: the shell is the thing that has to
    agree. install.ps1 gets the substring form because ``if ($env:X)`` is already
    false for an empty string and there is no pwsh on every runner.
    """

    import subprocess

    line = next(
        ln for ln in sh.splitlines() if ln.startswith("AELIX_PYTHON=")
    )
    for value, expected in ((None, _PY_REQUEST), ("", _PY_REQUEST), ("3.12", "3.12")):
        env = {"PATH": os.environ.get("PATH", "")}
        if value is not None:
            env["AELIX_PYTHON"] = value
        out = subprocess.run(
            ["sh", "-c", f'{line}; printf "%s" "$AELIX_PYTHON"'],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout
        assert out == expected, f"AELIX_PYTHON={value!r} resolved to {out!r}"

    # ps1 gets a regex rather than an execution because this file is designed to
    # run where there is no PowerShell at all (see the module docstring). It ties
    # BOTH halves: the truthiness test, which is what makes empty behave like
    # unset there, AND the literal it falls back to. A cross-review caught the
    # first version of this assertion checking only `if ($env:AELIX_PYTHON)` --
    # with that alone, changing the ps1 default to `3.14` still passed, which is
    # the precise drift this file exists to catch.
    assert re.search(
        r"\$env:AELIX_PYTHON\s*\)\s*\{\s*\$env:AELIX_PYTHON\s*\}\s*else\s*\{\s*"
        + re.escape(repr(_PY_REQUEST).replace('"', "'"))
        + r"\s*\}",
        ps1,
    ), f"install.ps1 must fall back to {_PY_REQUEST!r} when AELIX_PYTHON is unset or empty"


def test_the_windows_postcondition_asserts_the_same_range() -> None:
    """The range literal lives in FOUR places, not the two the ADR used to claim:
    both installers, ``_PY_REQUEST`` here, and -- since the Windows e2e grew an
    interpreter post-condition -- ``assert-install-ps1.ps1`` twice, once as the
    numeric bounds it compares and once as the literal in its failure message.

    A review flagged that as a maintenance trap: move the ceiling and the next
    editor is sent to two of four sites. It fails CLOSED (a stale post-condition
    rejects a now-valid interpreter rather than accepting a broken one), so it is
    a cost rather than a hazard -- but a checked invariant costs less than a
    comment asking people to remember.
    """

    text = (_REPO_ROOT / ".github" / "scripts" / "assert-install-ps1.ps1").read_text()

    m = re.fullmatch(r">=3\.(\d+),<3\.(\d+)", _PY_REQUEST)
    assert m, f"_PY_REQUEST is not the shape this test knows how to check: {_PY_REQUEST!r}"
    lo, hi = int(m.group(1)), int(m.group(2)) - 1

    assert f"-lt {lo}" in text, f"post-condition floor is not {lo} (from {_PY_REQUEST!r})"
    assert f"-gt {hi}" in text, f"post-condition ceiling is not {hi} (from {_PY_REQUEST!r})"
    assert _PY_REQUEST in text, "the post-condition's failure message must name the request it enforces"


def test_both_installers_pin_the_exact_version(sh: str, ps1: str) -> None:
    """The pin is what makes the checksum gate binding, in BOTH installers.

    ``--find-links`` only ADDS candidates — the PyPI index stays enabled so
    third-party dependencies resolve — and uv picks the best across both
    sources. Asking for the bare name ``aelix`` therefore lets a PyPI release
    of that name outrank the local wheels, and Step 4 would have verified
    artifacts that the install command then discards. Only ``==<version>`` from
    the verified manifest forces uv onto the checksum-verified wheel.

    Fails if EITHER installer drops the pin, which is the point: the hole is
    identical on both platforms and so is the fix.
    """

    for name, text in (("install.sh", sh), ("install.ps1", ps1)):
        code = "\n".join(_code_lines(text))
        assert "==" in code, name
        # The pinned target, both with and without extras.
        assert re.search(r'aelix\[\$\w+\]==\$\w+', code), name
        assert re.search(r'"aelix==\$\w+"', code), name


def test_neither_installer_ships_an_unpinned_target(sh: str, ps1: str) -> None:
    """Guard the exact pre-fix spellings so a revert cannot pass silently."""

    for name, text in (("install.sh", sh), ("install.ps1", ps1)):
        code = "\n".join(_code_lines(text))
        assert 'target="aelix"' not in code, name
        assert "target=\"aelix[$AELIX_EXTRAS]\"" not in code, name
        assert "{ \"aelix[$AelixExtras]\" }" not in code, name


def test_version_is_parsed_from_the_meta_wheel_not_the_tag(sh: str, ps1: str) -> None:
    """A tag is ``v0.1.0-beta.1``; PEP 440 normalizes it to ``0.1.0b1``.

    The tag is therefore NOT a usable version specifier, so both scripts read
    the version out of the ``aelix-<VER>-py3-none-any.whl`` filename. The
    sibling distributions escape their hyphen to an underscore
    (``aelix_ai-…``), which is what makes an ``aelix-`` prefix select the
    meta-package alone.
    """

    for name, text in (("install.sh", sh), ("install.ps1", ps1)):
        code = "\n".join(_code_lines(text))
        # PowerShell wraps the field in a capture group; awk does not.
        assert re.search(r"aelix-\(?\[\^-\]\+\)?-py3-none-any", code), name


def test_both_abort_when_the_version_cannot_be_parsed(sh: str, ps1: str) -> None:
    """No version means no pin means no gate — that must stop the install."""

    for name, text in (("install.sh", sh), ("install.ps1", ps1)):
        assert "could not parse the aelix version from SHA256SUMS" in text, name


# === crude syntax sanity (no PowerShell available here) =====================


def test_braces_balance(ps1: str) -> None:
    """Cheap structural check: no comment or string in this file contains a
    brace, so a mismatch is a real unclosed block."""

    assert ps1.count("{") == ps1.count("}")
    assert ps1.count("(") == ps1.count(")")


def test_cleanup_is_in_a_finally(ps1: str) -> None:
    """The temp dir holds downloaded wheels; a failed install must not leave them."""

    assert re.search(r"finally\s*\{[^}]*Remove-Item", ps1, re.DOTALL) is not None


# === encoding: the .ps1 files must mean the same thing to every host ========


_PS1_FILES = [_PS1, _REPO_ROOT / ".github" / "scripts" / "assert-install-ps1.ps1"]


@pytest.mark.parametrize("path", _PS1_FILES, ids=lambda p: p.name)
def test_ps1_is_ascii_only(path: Path) -> None:
    """Windows PowerShell 5.1 parses a BOM-less .ps1 as the ANSI code page.

    Both files ship without a BOM, so on the ``powershell`` leg of the
    ``install.ps1 e2e`` job they are decoded as CP1252 rather than as UTF-8.
    There the three bytes of a UTF-8 em dash end in 0x94, which CP1252 maps to
    U+201D RIGHT DOUBLE QUOTATION MARK -- a character the PowerShell tokenizer
    accepts as a double quote. The em dash that sat inside install.ps1's Step 4
    error string therefore CLOSED that string early and the whole file failed
    to parse: run 33862346729, job 100989402525, the first execution of this
    script on a Windows host. Reproduced by decoding the file as cp1252 and
    handing it to the pwsh 7.6.5 parser, which reports the runner's two errors
    verbatim (MissingEndCurlyBrace, MissingCatchOrFinally).

    Nothing before that run could have caught it. ``irm <url> | iex`` decodes
    correctly because raw.githubusercontent.com sends ``charset=utf-8``, and
    pwsh's Get-Content defaults to UTF-8; only a 5.1 host reading the bytes off
    disk sees it. ASCII-only removes the class instead of the instance.

    ``install.sh`` is deliberately exempt and keeps its em dashes: sh is
    byte-oriented and never re-decodes its own source.
    """

    offenders = [
        (lineno, line)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(ord(ch) > 127 for ch in line)
    ]
    assert not offenders, f"{path.name} is not ASCII: {offenders}"


@pytest.mark.parametrize("path", _PS1_FILES, ids=lambda p: p.name)
def test_ps1_has_no_bom(path: Path) -> None:
    """A BOM is the other fix for the above, and a worse one.

    It makes 5.1 decode correctly, but it is invisible, does not survive an
    editor that "helpfully" strips it, and would put U+FEFF at the front of the
    string ``irm`` hands to ``iex``. ASCII-only is the rule that is enforced;
    this asserts a BOM was not smuggled in beside it, which would make that
    rule untested in practice.
    """

    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
