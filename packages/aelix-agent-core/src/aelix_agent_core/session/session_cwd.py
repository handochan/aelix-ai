"""Pi parity port: ``packages/agent/src/harness/session/session-cwd.ts:1-59``.

Sprint 6h₅a (Phase 4.14, ADR-0081, P-337). Diagnostic helpers for the
``MissingSessionCwdError`` raised when a session-replacement target
references a working directory that no longer exists on disk.

Aelix divergence: Pi uses sync ``existsSync(...)`` from ``node:fs``;
Aelix :class:`FileSystem` is all-async (``session/fs.py:33-52``), so the
helper functions are themselves ``async`` and accept an injected ``fs``.

Pi call sites: factory bootstrap (``:391``) and ``importFromJsonl``
(``:352``) are NOT wired in Sprint 6h₅a (Aelix factory pattern P-302 +
``importFromJsonl`` stub). Only ``switch_session`` post-metadata-load
runs the assertion in 6h₅a.

W5 P-346 / P-347 (BLOCKING FIX) — error/prompt format Pi-verbatim:

  - :func:`format_missing_session_cwd_error` matches Pi ``:30-37``
    verbatim: starts with "Stored session working directory does not
    exist: <cwd>", conditional "Session file: <path>" line, and
    unconditional "Current working directory: <fallback>" line.
  - :func:`format_missing_session_cwd_prompt` (P-347) is a new Pi-parity
    port of ``session-cwd.ts:40-42`` — the TUI-side confirmation prompt
    rendered when the user opts to continue in the current cwd.
  - :class:`SessionCwdIssue` field shapes shift: ``session_file`` is
    now optional (``str | None`` — Pi field is ``string | undefined``)
    and ``fallback_cwd`` is required-non-optional (``str``) because the
    Pi error/prompt format ALWAYS renders it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_agent_core.session.fs import FileSystem
    from aelix_agent_core.session.session import Session


@dataclass(frozen=True)
class SessionCwdIssue:
    """Pi parity: ``SessionCwdIssue`` (``session-cwd.ts:1-15``).

    Sprint 6h₅a (ADR-0081, P-337 / P-346). Diagnostic triple carrying
    the optional session file path, the missing working directory, and
    the (required) fallback cwd the caller would have used.

    P-346 field shape change (Pi parity):

      - ``session_file: str | None`` — Pi ``string | undefined``. The
        factory-bootstrap call site has no session path yet, so this
        field is optional. The conditional "Session file: <path>" line
        in :func:`format_missing_session_cwd_error` is gated on
        ``session_file is not None``.
      - ``fallback_cwd: str`` (required, non-optional) — Pi
        ``fallbackCwd: string``. The Pi error/prompt format ALWAYS
        renders this field, so callers MUST supply it.
    """

    session_cwd: str
    fallback_cwd: str
    session_file: str | None = None


class MissingSessionCwdError(Exception):
    """Pi parity: ``MissingSessionCwdError`` (``session-cwd.ts:30-44``).

    Sprint 6h₅a (ADR-0081, P-337). Carries the :class:`SessionCwdIssue`
    so callers (RPC error wrappers / CLI prompts) can render actionable
    diagnostics.
    """

    def __init__(self, issue: SessionCwdIssue) -> None:
        super().__init__(format_missing_session_cwd_error(issue))
        self.name = "MissingSessionCwdError"
        self.issue = issue


def format_missing_session_cwd_error(issue: SessionCwdIssue) -> str:
    """Pi parity: ``formatMissingSessionCwdError`` (``session-cwd.ts:30-37``).

    Sprint 6h₅a (ADR-0081, P-346 — BLOCKING FIX). Renders the
    diagnostic as a newline-separated human-readable error message
    matching Pi verbatim:

    .. code-block:: text

       Stored session working directory does not exist: <session_cwd>
       Session file: <session_file>            # only when set
       Current working directory: <fallback_cwd>

    The "Session file" line is conditional on ``issue.session_file``
    being non-``None`` (Pi: ``issue.sessionFile ?? undefined`` gate);
    the "Current working directory" line is unconditional because
    ``fallback_cwd`` is a required field on :class:`SessionCwdIssue`.
    """

    lines = [
        f"Stored session working directory does not exist: {issue.session_cwd}",
    ]
    if issue.session_file is not None:
        lines.append(f"Session file: {issue.session_file}")
    lines.append(f"Current working directory: {issue.fallback_cwd}")
    return "\n".join(lines)


def format_missing_session_cwd_prompt(issue: SessionCwdIssue) -> str:
    """Pi parity: ``formatMissingSessionCwdPrompt`` (``session-cwd.ts:40-42``).

    Sprint 6h₅a (ADR-0081, P-347 — BLOCKING FIX). Renders the
    confirmation prompt rendered when the TUI offers the user the
    option to continue in the current cwd instead of the missing
    stored cwd. Pi verbatim:

    .. code-block:: text

       cwd from session file does not exist
       <session_cwd>

       continue in current cwd
       <fallback_cwd>
    """

    return (
        f"cwd from session file does not exist\n{issue.session_cwd}"
        f"\n\ncontinue in current cwd\n{issue.fallback_cwd}"
    )


async def get_missing_session_cwd_issue(
    session: Session,
    fallback_cwd: str | None,
    *,
    fs: FileSystem,
) -> SessionCwdIssue | None:
    """Pi parity: ``getMissingSessionCwdIssue`` (``session-cwd.ts:17-28``).

    Sprint 6h₅a (ADR-0081, P-337). Returns ``None`` when:

      - session has no ``session_file`` AND no ``fallback_cwd`` (no way
        to construct a useful diagnostic), OR
      - session metadata exposes no ``cwd`` attribute, OR
      - the cwd exists on disk per ``fs.exists``.

    Otherwise returns a :class:`SessionCwdIssue` carrying the diagnostic
    triple ``{session_cwd, fallback_cwd, session_file?}``. The ``fs``
    parameter is keyword-only because Pi uses sync ``existsSync`` and
    Aelix's all-async :class:`FileSystem` requires the caller to inject
    the implementation explicitly.

    P-346 NOTE: ``fallback_cwd`` is required-non-optional on the issue
    dataclass. When the caller passes ``None``, this helper returns
    ``None`` (no diagnostic possible).
    """

    session_file = session.session_file
    metadata = await session.get_metadata()
    session_cwd: str | None = getattr(metadata, "cwd", None)
    if not session_cwd:
        return None
    if await fs.exists(session_cwd):
        return None
    if fallback_cwd is None:
        # Without a fallback we cannot render the Pi-format diagnostic.
        return None
    return SessionCwdIssue(
        session_cwd=session_cwd,
        fallback_cwd=fallback_cwd,
        session_file=session_file,
    )


async def assert_session_cwd_exists(
    session: Session,
    fallback_cwd: str | None,
    *,
    fs: FileSystem,
) -> None:
    """Pi parity: ``assertSessionCwdExists`` (``session-cwd.ts:58-63``).

    Sprint 6h₅a (ADR-0081, P-337). Raises
    :class:`MissingSessionCwdError` when the session's cwd is set and
    does not resolve on disk. No-op when metadata is absent or fs check
    passes.
    """

    issue = await get_missing_session_cwd_issue(session, fallback_cwd, fs=fs)
    if issue is not None:
        raise MissingSessionCwdError(issue)


__all__ = [
    "MissingSessionCwdError",
    "SessionCwdIssue",
    "assert_session_cwd_exists",
    "format_missing_session_cwd_error",
    "format_missing_session_cwd_prompt",
    "get_missing_session_cwd_issue",
]
