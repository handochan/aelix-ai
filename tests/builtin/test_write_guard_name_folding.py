"""The write guards must not fold open on case, trailing shapes, or NTFS streams.

Both write guards decided sensitivity by comparing a raw path component against
an all-lowercase set. Filesystems Aelix supports do not agree that those are
distinct files, so several spellings of the same target were auto-allowed while
the canonical spelling was correctly refused. Measured at ``0c9da7d`` before the
fold, 13 bypasses with no broken controls::

    permission._is_auto_allowable_write   -> True (auto-allowed, no prompt)
        .ENV   .Env   ".env "   .env::$DATA   .ENV.LOCAL
        Id_Rsa   ID_RSA   .SSH/AUTHORIZED_KEYS
        .AELIX/agents/e.md   ".aelix /agents/e.md"

    guardrail._write_dotenv               -> None (write permitted)
        .ENV   ".env "   /home/u/proj/.ENV   C:\\proj\\.env   C:\\proj\\.ENV

THIS IS NOT A WINDOWS BUG. Both guards are string comparison, so they fold open
on any host — every row above was reproduced on Linux — and the case family is
live on macOS's default case-insensitive APFS, which ``install.sh`` explicitly
targets. Issue #108 files this under Windows; that triage parks a
shipped-platform bypass behind an unshipped-platform roadmap.

``.AELIX/agents/e.md`` is the load-bearing row: ADR-0197 §(i) names the
``.aelix`` component as the HARD PREREQUISITE for bounded widening of a
delegated child, and a child that can write ``.aelix/agents/*.md`` authors the
parent's next identity. A prerequisite that folds open on case is not one.

A casefold-only fix would be the worst outcome available — it closes the case
family, looks fixed, and leaves the trailing-shape and stream families open.
:func:`~aelix_coding_agent.builtin.guardrail._guard_key` folds all three or
none, and ``test_sensitive_sets_are_keyed_in_folded_form`` keeps the sets in the
shape the fold produces so a future mixed-case entry cannot land dead.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_coding_agent.builtin.guardrail import _guard_key, _write_dotenv
from aelix_coding_agent.builtin.permission import (
    _SENSITIVE_BASENAMES,
    _SENSITIVE_DIR_COMPONENTS,
    _is_auto_allowable_write,
)

_CWD = "/proj"


def _write_event(path: str) -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="write",
        args={"path": path},
    )


# ============================================================
# _guard_key — the fold itself
# ============================================================


@pytest.mark.parametrize(
    ("component", "expected", "why"),
    [
        (".env", ".env", "already canonical"),
        (".ENV", ".env", "case: NTFS and APFS are case-insensitive"),
        (".Env", ".env", "case, mixed"),
        (".env ", ".env", "Win32 strips a trailing space"),
        (".env.", ".env", "Win32 strips a trailing dot"),
        (".env  ..", ".env", "Win32 strips any run of trailing spaces/dots"),
        (".env::$DATA", ".env", "NTFS stream suffix opens the default stream"),
        (".ENV::$DATA", ".env", "stream suffix and case together"),
        ("Id_Rsa", "id_rsa", "case, sensitive basename"),
        (".AELIX", ".aelix", "case, sensitive dir component"),
        (".env.local", ".env.local", "an interior dot is not trailing"),
        ("notes.md", "notes.md", "ordinary name is untouched"),
        ("C:", "c", "a Windows drive letter folds harmlessly"),
    ],
)
def test_guard_key_folds_each_shape(
    component: str, expected: str, why: str
) -> None:
    assert _guard_key(component) == expected, why


@pytest.mark.parametrize("component", [".", ".."])
def test_guard_key_never_folds_a_dot_component_to_nothing(component: str) -> None:
    """``normpath`` removes these first, but a caller that skips it must not get
    a key that silently matches nothing."""

    assert _guard_key(component) == component


# ============================================================
# permission._is_auto_allowable_write
# ============================================================


@pytest.mark.parametrize(
    ("path", "why"),
    [
        (".env", "baseline: the canonical spelling was always refused"),
        (".ENV", "case"),
        (".Env", "case, mixed"),
        (".env ", "trailing space"),
        (".env.", "trailing dot"),
        (".env::$DATA", "NTFS stream suffix"),
        (".env.local", "baseline: the .env. prefix rule"),
        (".ENV.LOCAL", "case, through the prefix rule"),
        ("id_rsa", "baseline: sensitive basename"),
        ("Id_Rsa", "case, sensitive basename"),
        ("ID_RSA", "case, sensitive basename, upper"),
        (".ssh/authorized_keys", "baseline: sensitive dir component"),
        (".SSH/AUTHORIZED_KEYS", "case, dir component and basename together"),
        (".Ssh/authorized_keys", "case, dir component only"),
        (".aelix/agents/e.md", "baseline: ADR-0197 §(i) prerequisite"),
        (".AELIX/agents/e.md", "case, ADR-0197 §(i) prerequisite"),
        (".aelix /agents/e.md", "trailing space on the dir component"),
        (".AELIX/../.aelix/agents/e.md", "case survives normpath collapsing"),
    ],
)
def test_folded_sensitive_writes_are_not_auto_allowable(
    path: str, why: str
) -> None:
    assert _is_auto_allowable_write(path, _CWD) is False, why


@pytest.mark.parametrize(
    "path",
    [
        "notes.md",
        "src/app.py",
        "docs/env.md",
        "config/environment.yaml",
        ".envoy/config.json",
        "tests/test_id_rsa_parser.py",
        "sshd/notes.txt",
    ],
)
def test_ordinary_writes_stay_auto_allowable(path: str) -> None:
    """The fold must not widen what counts as sensitive.

    ``.envoy`` and ``environment.yaml`` share a prefix with ``.env`` but are not
    it; ``test_id_rsa_parser.py`` merely contains a sensitive name.
    """

    assert _is_auto_allowable_write(path, _CWD) is True


# ============================================================
# guardrail._write_dotenv
# ============================================================


@pytest.mark.parametrize(
    ("path", "why"),
    [
        (".env", "baseline"),
        (".ENV", "case"),
        (".env ", "trailing space"),
        (".env::$DATA", "NTFS stream suffix"),
        ("/home/u/proj/.ENV", "case, absolute POSIX path"),
        ("proj/.ENV.LOCAL", "case, through the .env. prefix rule"),
        (
            "C:\\proj\\.env",
            "backslash separator: unnormalised, the whole path became the "
            "basename and no Windows-shaped dotenv write was ever refused",
        ),
        ("C:\\proj\\.ENV", "backslash and case together"),
    ],
)
def test_dotenv_guardrail_refuses_folded_spellings(path: str, why: str) -> None:
    verdict = _write_dotenv(_write_event(path))
    assert verdict is not None, why
    assert "dotenv" in verdict


@pytest.mark.parametrize(
    "path",
    ["notes.md", "src/app.py", ".envoy/config.json", "config/environment.yaml"],
)
def test_dotenv_guardrail_permits_ordinary_paths(path: str) -> None:
    assert _write_dotenv(_write_event(path)) is None


# ============================================================
# The invariant that keeps the fold honest
# ============================================================


@pytest.mark.parametrize(
    "name", sorted(_SENSITIVE_BASENAMES | _SENSITIVE_DIR_COMPONENTS)
)
def test_sensitive_sets_are_keyed_in_folded_form(name: str) -> None:
    """Every entry must already equal its own fold.

    Lookups run the CANDIDATE through :func:`_guard_key` and compare against
    these sets verbatim. An entry that is not itself in folded form — ``Id_RSA``,
    ``.SSH``, ``"crontab "`` — can therefore never match anything, and would be
    dead security config that reads as live.
    """

    assert _guard_key(name) == name
