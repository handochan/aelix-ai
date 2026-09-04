"""Composition and target predicates shared by the two Windows dialects (#204).

ADR-0237. Everything here is pure string work on literals — no ``pathlib``, no
``os.path``, no ``sys.platform`` — because the gate reasons about a Windows
path while running on POSIX (and about ``/etc/passwd`` while running under
``pwsh`` on Windows). ``posixpath`` would hand back the whole
``C:\\…\\hosts`` string as a "basename"; ``ntpath`` is not importable as a
platform-independent choice. The prefix matcher is therefore written out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from aelix_coding_agent.builtin.bash_classifier import (
    _PROTECTED_WRITE_PREFIXES,
    Verdict,
)


def fold(verdicts: Iterable[Verdict]) -> Verdict:
    """ADR-0158 strictest-wins: the MAX over the parts, ASK for NO parts.

    NOT ``max(verdicts, default=Verdict.ALLOW)``. ``max``'s identity is ALLOW
    and an empty part list is exactly the case that must not be permissive:
    measured against ``tree_sitter_powershell`` 0.26.4, ``$x`` parses with
    ``has_error=False`` and ZERO ``command`` nodes, so an ALLOW identity
    auto-runs every expression-only line.
    """

    worst: Verdict | None = None
    for verdict in verdicts:
        worst = verdict if worst is None else max(worst, verdict)
    return Verdict.ASK if worst is None else worst


# ``PROGRA~1`` needs the filesystem to expand, so a component in 8.3 short form
# is never compared against a prefix — the answer would be a guess.
_SHORT_NAME = re.compile(r".+~\d+$")

# Windows surfaces whose mutation is not recoverable inside an agent turn. The
# last block is what a filesystem-only rule misses: ``Set-Content Env:\Path``
# mutates the environment of every later command and ``Function:\prompt``
# rebinds a function, and neither is a file.
_WINDOWS_PROTECTED_PREFIXES: tuple[str, ...] = (
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\programdata",
    r"\appdata\roaming\microsoft\windows\start menu\programs\startup",
    r"~\documents\powershell",
    r"~\documents\windowspowershell",
    r"~\.ssh",
    r"~\.aws",
    r"~\.config",
    r"~\.gitconfig",
    r".git",
    "hklm:",
    r"hkcu:\software\microsoft\windows\currentversion\run",
    "env:",
    "function:",
    "alias:",
    "cert:",
)

# Read targets whose CONTENTS are a credential or a session-hijack primitive.
# The ALLOW tier reads files; "read-only" is not the same claim as "harmless to
# read", which is the gap ``Get-Content ~\.ssh\id_rsa`` walks through.
_SENSITIVE_READ_PREFIXES: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gh",
    "~/.docker",
    # The three rows below were each measured ALLOW at the real gate on this
    # branch in BOTH dialects (`type C:\Users\me\.kube\config` under cmd,
    # `Get-Content` under pwsh) while `main` c6d424c answered ASK for every
    # Windows line, so each was an ASK -> ALLOW move #204 introduced. Same
    # defect class as the wildcard evasion `_SENSITIVE_SEGMENTS` closes, on the
    # same predicate: a credential store the table simply never named.
    # `~/.kube/config` embeds bearer tokens and client keys inline,
    # `~/.azure/accessTokens.json` is the CLI's OAuth cache, and
    # `~/.terraform.d/credentials.tfrc.json` is a Terraform Cloud API token.
    "~/.kube",
    "~/.azure",
    "~/.terraform.d",
    "$profile",
    "~/documents/powershell",
    "~/documents/windowspowershell",
)
_SENSITIVE_BASENAMES = frozenset(
    {
        ".env",
        ".netrc",
        "_netrc",
        "credentials",
        "id_rsa",
        "id_ed25519",
        # `id_ecdsa`, `id_dsa` and `.pgpass` are private keys and a password
        # store that `permission.py:338-355` already refuses to WRITE without a
        # prompt; reading one out is the same secret by the same name, and
        # measured ALLOW here in both dialects outside a `.ssh` directory
        # (`type C:\Users\me\keys\id_ecdsa`).
        "id_ecdsa",
        "id_dsa",
        ".pgpass",
        # A plaintext `https://user:password@host` store. Not `.gitconfig`,
        # which is deliberately absent: it NAMES a credential helper, it does
        # not hold the credential.
        ".git-credentials",
        ".pypirc",
        ".npmrc",
    }
)

# Basenames matched by STEM rather than in full. The exact-match table above
# refused `~/.aws/credentials` while measuring ALLOW on the two stores that
# carry an extension — gcloud's `credentials.db` and Terraform's
# `credentials.tfrc.json` — which is the exact-vs-suffix half of the same
# finding. `.env.` follows `permission.py:407`, which has folded `.env.local`
# in with `.env` for writes since the write guard existed.
_SENSITIVE_BASENAME_STEMS: tuple[str, ...] = ("credentials.", ".env.")

# The same directories, matched as a run of components ANYWHERE in the path
# instead of only under ``~``. Measured on this branch before the segment rule
# existed, through the real gate: ``type C:\Users\me\.ssh\*`` was ALLOW while
# ``type C:\Users\me\.ssh\id_rsa`` was ASK, and the pwsh spellings
# (``gc /Users/me/.ssh/*``, ``Select-String -Path C:\Users\me\.ssh\* BEGIN``)
# were ALLOW too. Neither table could see them: ``*`` is not a sensitive
# BASENAME and ``C:\Users\me\.ssh`` is not the ``~``-anchored PREFIX, so the
# whole control was one character from evasion in both dialects.
_SENSITIVE_SEGMENTS: tuple[tuple[str, ...], ...] = tuple(
    tuple(prefix.removeprefix("~/").split("/"))
    for prefix in _SENSITIVE_READ_PREFIXES
    if prefix.startswith("~/")
) + (
    # gcloud is the one store with no `~`-anchored spelling to derive from: its
    # database is `%APPDATA%\gcloud\credentials.db` on Windows and
    # `~/.config/gcloud/credentials.db` on POSIX. Both measured ALLOW in both
    # dialects. A `~/.config/gcloud` prefix would have caught only the POSIX
    # spelling, so the rule is written where it covers both — as a
    # one-component run matched anywhere.
    ("gcloud",),
)

# Targets that are the whole tree rather than a file. ``C:\`` is in no prefix
# set and is the flagship case of #204's issue body.
_ROOT_TARGETS = frozenset(
    {
        "",
        "*",
        ".",
        "..",
        ".\\*",
        "./*",
        ".\\",
        "./",
        "/",
        "\\",
        "~",
        "$home",
        "%userprofile%",
    }
)
_DRIVE_ROOT = re.compile(r"[a-z]:")


def _unquote(target: str) -> str:
    """The literal a quote-stripped token denotes, casefolded."""

    text = target.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text.casefold()


def _windows_form(target: str) -> str | None:
    """``target`` as a comparable Windows path, or ``None`` if it must not be.

    ``str.startswith`` on the raw token is trivially evaded, so the separator
    is normalised and the device prefixes are stripped. Three shapes return
    ``None`` — meaning "no prefix match is claimed", which leaves the caller on
    its ASK branch rather than on DENY:

    - a UNC path (``\\\\srv\\share``), because the share may be anything;
    - an 8.3 component (``C:\\PROGRA~1``), because expanding it needs the disk;
    - anything carrying ``$env:`` / ``%VAR%`` / ``$HOME``, because this
      classifier never expands a variable (the same rule as
      ``bash_classifier.py``'s protected-redirect note).
    """

    text = _unquote(target).replace("/", "\\")
    if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
        text = text[4:]
    if text.startswith("\\\\"):
        return None
    if "$" in text or "%" in text:
        return None
    text = _canonical(text)
    if any(_SHORT_NAME.fullmatch(part) for part in text.split("\\")):
        return None
    return text


def _canonical(text: str) -> str:
    """The two canonicalisations Win32 performs and ``str.startswith`` does not.

    Measured on this branch before this ran: ``echo x > C:\\\\Windows\\y``,
    ``echo x > C:\\Windows.\\y`` and ``echo x > C:/\\Windows/y`` were all ASK
    while ``echo x > C:\\Windows\\y`` was DENY, in both dialects — a repeated
    separator or a trailing dot inside the matched region moved the string out
    of every prefix while Win32 opens the identical file (repeated separators
    collapse; trailing dots and spaces are stripped from a component before the
    path is resolved). A LEADING separator is kept: ``\\Windows\\x`` is
    drive-relative and :func:`protected_write_target` compares it against each
    prefix's drive-less tail.
    """

    parts = text.split("\\")
    lead = "\\" if parts and parts[0] == "" else ""
    kept = [part if part in (".", "..") else (part.rstrip(". ") or part) for part in parts if part]
    return lead + "\\".join(kept)


def _component(part: str) -> str:
    """One path component as Win32 opens it, for comparison against a name table.

    Two spellings name the identical file and neither is an exact string match.
    Measured at the gate on this branch before this existed, in BOTH dialects
    (`type`, `more`, `findstr`, `Get-Content`, `gc`, `Select-String -Path`):
    ``C:\\Users\\me\\keys\\id_rsa.`` was ALLOW while ``…\\id_rsa`` was ASK, and
    so were ``.netrc.``, ``.pgpass.``, ``.git-credentials.``, ``id_ecdsa.`` and
    ``…\\id_rsa::$DATA``. On `main` c6d424c every one of those lines was ASK, so
    the trailing dot and the NTFS default data stream were an ASK -> ALLOW move
    on a private key — a wildcard-class evasion of an entry that IS in the
    table, not the incompleteness ADR-0237's Non-goals cover. The trailing-dot
    half of the rule was already written for the WRITE side in
    :func:`_canonical`; the read side compared raw.

    ``or part`` keeps ``.``, ``..`` and a bare ``C:`` intact rather than
    collapsing them to the empty string.
    """

    return (part.split(":", 1)[0] or part).rstrip(". ") or part


def _matches(text: str, prefix: str, sep: str) -> bool:
    return text == prefix.rstrip(sep) or text.startswith(prefix.rstrip(sep) + sep)


def protected_write_target(target: str | None) -> bool:
    """Whether writing to ``target`` is unrecoverable enough to DENY.

    The union of the Windows prefixes above and ``bash_classifier``'s POSIX
    ``_PROTECTED_WRITE_PREFIXES`` (#204 F23), because ``pwsh`` runs on both:
    ``Set-Content /etc/hosts x`` is a real command under ``pwsh`` on Linux and
    the bash DENY floor measures it ASK, so this is the only rule that catches
    it.
    """

    if target is None:
        return False
    posix = _unquote(target).replace("\\", "/")
    if any(_matches(posix, prefix.casefold(), "/") for prefix in _PROTECTED_WRITE_PREFIXES):
        return True
    windows = _windows_form(target)
    if windows is None:
        return False
    for prefix in _WINDOWS_PROTECTED_PREFIXES:
        if _matches(windows, prefix, "\\"):
            return True
        # Drive-relative (``\Windows\x``) names the current drive's root, so it
        # is compared against each prefix's drive-less tail.
        head, _, tail = prefix.partition(":")
        if tail and len(head) == 1 and not _has_drive(windows) and _matches(windows, tail, "\\"):
            return True
    return False


def _has_drive(text: str) -> bool:
    return len(text) >= 2 and text[1] == ":"


def is_root_or_protected_target(target: str | None) -> bool:
    """Whether a recursive delete of ``target`` takes the whole tree (#204 F12).

    ``None`` — no target node at all — is TRUE and is the case the prefix set
    cannot express: measured, ``Get-ChildItem | Remove-Item -Recurse -Force``
    parses as two commands, the second with only ``command_parameter``
    children, and it deletes the working directory.
    """

    if target is None:
        return True
    text = _unquote(target)
    if text in _ROOT_TARGETS:
        return True
    if _DRIVE_ROOT.fullmatch(text.replace("/", "\\").rstrip("\\")):
        return True
    return protected_write_target(target)


def sensitive_read_target(target: str | None) -> bool:
    """Whether READING ``target`` leaks a credential or reaches off the box.

    A UNC source is included because an outbound SMB fetch to an attacker host
    is an NTLM-relay surface, not merely a slow read, and a provider drive
    (``Env:``, ``Variable:``, ``HKLM:``) is included because it is not a file
    at all — a "read-only" cmdlet pointed at it dumps session state.

    The four tables are deliberately redundant: a basename, a basename STEM, a
    ``~``-anchored prefix and a SEGMENT run. The prefix and basename tables
    alone were both defeated by a trailing ``*`` (see
    :data:`_SENSITIVE_SEGMENTS`), and one wildcard is the cheapest evasion
    there is; the stem table exists because an exact basename match let
    ``credentials.db`` past while refusing ``credentials``.
    """

    if target is None:
        return False
    text = _unquote(target)
    if text.replace("/", "\\").startswith("\\\\"):
        return True
    posix = text.replace("\\", "/")
    basename = _component(posix.rsplit("/", 1)[-1])
    if basename in _SENSITIVE_BASENAMES or basename.startswith(_SENSITIVE_BASENAME_STEMS):
        return True
    if any(_matches(posix, prefix, "/") for prefix in _SENSITIVE_READ_PREFIXES):
        return True
    parts = [_component(part) for part in posix.split("/") if part]
    if any(
        parts[start : start + len(segment)] == list(segment)
        for segment in _SENSITIVE_SEGMENTS
        for start in range(len(parts))
    ):
        return True
    head, sep, _ = posix.partition(":")
    return bool(sep) and len(head) > 1


__all__ = [
    "fold",
    "is_root_or_protected_target",
    "protected_write_target",
    "sensitive_read_target",
]
