"""The target predicates both Windows dialects share (#204, ADR-0237).

``common.py`` is where a rule is written once for ``powershell.py`` and
``cmd.py``, which means a hole in it is a hole in both at once — and every case
below is one that was measured open through the real gate under BOTH dialects
before it was closed. The bucket tables in the two classifier modules pin the
verdicts; this module pins the predicate, because a verdict can also be right
by accident.

These tests RUN on Linux, macOS and the gating ``windows-latest`` leg alike.
``common.py`` is pure string work — no ``pathlib``, no ``os.path``, no
``sys.platform`` — precisely so that a Windows path can be reasoned about from
a POSIX box and a POSIX path from a Windows one, so nothing here is skipped.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.builtin.shell_classifiers.common import (
    _windows_form,
    protected_write_target,
    sensitive_read_target,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        # Win32 collapses repeated separators and strips a trailing dot (and a
        # trailing space) from a component before it resolves a path.
        # ``str.startswith`` does neither, so each spelling below opened the
        # identical file while measuring ASK against a DENY prefix.
        ("C:\\\\Windows\\y", "c:\\windows\\y"),
        ("C:\\Windows.\\y", "c:\\windows\\y"),
        ("C:/\\Windows/y", "c:\\windows\\y"),
        ("C:\\Windows\\\\y", "c:\\windows\\y"),
        ("C:\\Windows \\y", "c:\\windows\\y"),
        # A LEADING separator is drive-relative and must survive: it is what
        # ``protected_write_target`` compares against each prefix's drive-less
        # tail.
        ("\\Windows\\x", "\\windows\\x"),
        # ``.`` and ``..`` are components, not names with a trailing dot.
        (".\\x", ".\\x"),
        ("..\\x", "..\\x"),
    ],
)
def test_windows_form_canonicalises_what_win32_canonicalises(
    target: str, expected: str
) -> None:
    assert _windows_form(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        "\\\\srv\\share\\x",  # UNC: the share may be anything
        # The UNC test runs BEFORE the separator collapse, on purpose: a leading
        # run of separators is refused rather than collapsed into a
        # drive-relative path this classifier would then claim to recognise.
        "\\\\\\Windows\\x",
        "C:\\PROGRA~1\\x",  # 8.3: expanding it needs the disk
        "%SystemRoot%\\x",  # never expanded
        "$env:SystemRoot\\x",
    ],
)
def test_windows_form_claims_nothing_it_cannot_resolve(target: str) -> None:
    """``None`` means "no prefix match is claimed", which leaves the caller on
    its ASK branch rather than on DENY."""

    assert _windows_form(target) is None


@pytest.mark.parametrize(
    "target",
    [
        "C:\\Windows\\y",
        "C:\\\\Windows\\y",
        "C:\\Windows.\\y",
        "C:/\\Windows/y",
        "c:\\windows\\system32\\drivers\\etc\\hosts",
        "\\Windows\\x",
        "/etc/passwd",  # ``pwsh`` runs on POSIX; the union is checked
        "Env:\\Path",  # a provider drive is not a file at all
    ],
)
def test_protected_write_targets(target: str) -> None:
    assert protected_write_target(target) is True


@pytest.mark.parametrize("target", ["out.txt", ".\\build\\out.js", None])
def test_ordinary_write_targets_are_not_protected(target: str | None) -> None:
    assert protected_write_target(target) is False


@pytest.mark.parametrize(
    "target",
    [
        # The two spellings that were already refused …
        "~\\.ssh\\id_rsa",
        "C:\\Users\\me\\.aws\\credentials",
        # … and the one character that defeated both tables. A wildcard is not
        # a sensitive BASENAME, and ``C:\\Users\\me\\.ssh`` is not the
        # ``~``-anchored PREFIX, so ``type C:\\Users\\me\\.ssh\\*`` and
        # ``Get-Content C:\\Users\\me\\.ssh\\*`` were both ALLOW at the gate.
        "C:\\Users\\me\\.ssh\\*",
        "/Users/me/.ssh/*",
        "C:\\Users\\me\\.aws\\*",
        "/home/me/.gnupg/",
        "D:\\backup\\home\\me\\.docker\\config.json",
        "C:\\Users\\me\\.config\\gh\\hosts.yml",
        "C:\\Users\\me\\Documents\\PowerShell\\profile.ps1",
        # The credential families the first denylist never named. Each measured
        # ALLOW at the real gate under BOTH dialects while `main` c6d424c
        # answered ASK for every Windows line, so each was an ASK -> ALLOW move
        # this branch introduced — the same defect class as the wildcard row
        # above, on the same predicate.
        "C:\\Users\\me\\.kube\\config",
        "C:\\Users\\me\\.kube\\*",
        "/home/me/.kube/config",
        "C:\\Users\\me\\.azure\\accessTokens.json",
        "C:\\Users\\me\\.terraform.d\\credentials.tfrc.json",
        "C:\\Users\\me\\.git-credentials",
        # gcloud has no ``~``-anchored spelling to derive a segment from: the
        # store is ``%APPDATA%\\gcloud`` on Windows, ``~/.config/gcloud`` on
        # POSIX. Both spellings, or the rule is written in the wrong place.
        "C:\\Users\\me\\AppData\\Roaming\\gcloud\\credentials.db",
        "/home/me/.config/gcloud/credentials.db",
        # STEM, not exact basename: ``credentials`` was refused beside
        # ``credentials.db`` and ``credentials.tfrc.json``, which were not.
        "D:\\loot\\credentials.db",
        "C:\\app\\.env.local",
        "C:\\app\\.env.production",
        # Private keys and a password store outside any ``.ssh`` directory.
        # ``permission.py:338-355`` already refuses to WRITE all three.
        "C:\\Users\\me\\keys\\id_ecdsa",
        "C:\\Users\\me\\keys\\id_dsa",
        "C:\\Users\\me\\.pgpass",
        # A basename that IS in the table, spelled the two ways Win32 resolves
        # to the identical file and ``str.__eq__`` does not: a trailing dot is
        # stripped from a component before the path resolves, and ``::$DATA``
        # is the default NTFS stream. Both measured ALLOW at the gate in BOTH
        # dialects while ``main`` c6d424c answered ASK — an ASK -> ALLOW move on
        # a private key. The trailing-dot rule was already written for the WRITE
        # side (``_canonical``); only the read side compared raw. The trailing
        # SPACE twin passed only by accident, because ``_unquote`` strips it.
        "C:\\Users\\me\\keys\\id_rsa.",
        "C:\\Users\\me\\keys\\id_rsa ",
        "C:\\Users\\me\\keys\\id_rsa::$DATA",
        "C:\\Users\\me\\.netrc.",
        "C:\\Users\\me\\.pgpass.",
        "C:\\Users\\me\\.git-credentials.",
        "C:\\app\\.env.",
        "\\\\attacker.example\\share\\x.csv",  # NTLM relay, not a slow read
        "Env:\\Path",  # a provider drive dumps session state
    ],
)
def test_sensitive_read_targets(target: str) -> None:
    assert sensitive_read_target(target) is True


@pytest.mark.parametrize(
    "target",
    [
        "a.txt",
        "*.txt",
        "C:\\Users\\me\\notes\\*",
        ".\\src\\main.py",
        "C:\\Windows\\win.ini",  # protected to WRITE, unremarkable to read
        # Deliberately NOT sensitive: ``.gitconfig`` NAMES a credential helper,
        # it does not hold the credential. It is in the protected-WRITE prefix
        # set for that reason and stays out of the read denylist, so the
        # difference is pinned rather than left to the next reader to rediscover.
        "C:\\Users\\me\\.gitconfig",
        # ``.environment`` does not start with the ``.env.`` stem, and a stem
        # that swallowed it would ASK on an ordinary directory name.
        "C:\\src\\.environment",
        None,
    ],
)
def test_ordinary_read_targets_are_not_sensitive(target: str | None) -> None:
    assert sensitive_read_target(target) is False
