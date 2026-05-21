"""Sprint 6h₅a · Phase 4.14 — :mod:`session.session_cwd` unit tests (P-337).

Pi parity: ``packages/agent/src/harness/session/session-cwd.ts:1-59`` (SHA
``734e08e``). Aelix divergence: helper functions are ``async`` because
the Aelix :class:`FileSystem` Protocol is all-async (Pi uses sync
``existsSync``).

W5 P-346 / P-347 additions:

  - :func:`format_missing_session_cwd_error` output matches Pi verbatim
    (header "Stored session working directory does not exist: ..."
    + conditional "Session file: ..." + unconditional "Current working
    directory: ...").
  - :func:`format_missing_session_cwd_prompt` (P-347) is new — TUI
    confirmation prompt port of Pi ``session-cwd.ts:40-42``.
  - :class:`SessionCwdIssue` shape: ``session_file: str | None``
    (optional, default None); ``fallback_cwd: str`` (required).
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_agent_core.session.session_cwd import (
    MissingSessionCwdError,
    SessionCwdIssue,
    assert_session_cwd_exists,
    format_missing_session_cwd_error,
    format_missing_session_cwd_prompt,
    get_missing_session_cwd_issue,
)


class _FakeFs:
    """Async :class:`FileSystem` stub — only ``exists`` is exercised."""

    cwd = "/work"

    def __init__(self, exists: bool) -> None:
        self._exists = exists
        self.exists_calls: list[str] = []

    async def exists(self, path: str) -> bool:
        self.exists_calls.append(path)
        return self._exists


class _FakeSession:
    """Async-shaped :class:`Session` stub with the 2 surface points
    :func:`get_missing_session_cwd_issue` touches.
    """

    def __init__(
        self,
        *,
        session_file: str | None,
        cwd: str | None,
    ) -> None:
        self.session_file = session_file
        self._cwd = cwd

    async def get_metadata(self) -> Any:
        class _Meta:
            pass

        m = _Meta()
        if self._cwd is not None:
            m.cwd = self._cwd  # type: ignore[attr-defined]
        return m


async def test_get_missing_returns_none_when_cwd_exists_on_disk() -> None:
    """When the cwd resolves, no issue is constructed."""

    fs = _FakeFs(exists=True)
    session = _FakeSession(session_file="/sessions/abc.jsonl", cwd="/real")
    issue = await get_missing_session_cwd_issue(
        session,  # type: ignore[arg-type]
        fallback_cwd="/work",
        fs=fs,  # type: ignore[arg-type]
    )
    assert issue is None
    assert fs.exists_calls == ["/real"]


async def test_get_missing_returns_issue_when_cwd_does_not_exist() -> None:
    """The unhappy path produces the diagnostic triple."""

    fs = _FakeFs(exists=False)
    session = _FakeSession(session_file="/sessions/abc.jsonl", cwd="/gone")
    issue = await get_missing_session_cwd_issue(
        session,  # type: ignore[arg-type]
        fallback_cwd="/work",
        fs=fs,  # type: ignore[arg-type]
    )
    assert issue == SessionCwdIssue(
        session_cwd="/gone",
        fallback_cwd="/work",
        session_file="/sessions/abc.jsonl",
    )


async def test_get_missing_returns_issue_when_session_file_is_none_but_fallback_given() -> None:
    """P-346 shape: ``session_file`` is optional. Factory-bootstrap-style
    callers with no session path can still surface a diagnostic when the
    metadata cwd is missing AND a fallback is supplied.
    """

    fs = _FakeFs(exists=False)
    session = _FakeSession(session_file=None, cwd="/gone")
    issue = await get_missing_session_cwd_issue(
        session,  # type: ignore[arg-type]
        fallback_cwd="/work",
        fs=fs,  # type: ignore[arg-type]
    )
    assert issue == SessionCwdIssue(
        session_cwd="/gone", fallback_cwd="/work", session_file=None
    )


async def test_get_missing_returns_none_when_fallback_cwd_is_none() -> None:
    """P-346: with no fallback, no Pi-format diagnostic can render."""

    fs = _FakeFs(exists=False)
    session = _FakeSession(session_file="/s.jsonl", cwd="/gone")
    issue = await get_missing_session_cwd_issue(
        session,  # type: ignore[arg-type]
        fallback_cwd=None,
        fs=fs,  # type: ignore[arg-type]
    )
    assert issue is None


async def test_missing_session_cwd_error_carries_issue_and_message() -> None:
    """The error message renders all three diagnostic fields."""

    issue = SessionCwdIssue(
        session_cwd="/missing", fallback_cwd="/w", session_file="/s.jsonl"
    )
    rendered = format_missing_session_cwd_error(issue)
    assert "/missing" in rendered
    assert "/s.jsonl" in rendered
    assert "/w" in rendered

    err = MissingSessionCwdError(issue)
    assert err.issue is issue
    # Pi parity: ``MissingSessionCwdError`` carries a ``name`` attr matching JS.
    assert err.name == "MissingSessionCwdError"
    assert str(err) == rendered


def test_format_missing_session_cwd_error_matches_pi_verbatim() -> None:
    """Pi parity ``:30-37``: header + conditional Session file +
    unconditional Current working directory.
    """

    issue = SessionCwdIssue(
        session_cwd="/gone",
        fallback_cwd="/work",
        session_file="/sessions/abc.jsonl",
    )
    rendered = format_missing_session_cwd_error(issue)
    assert rendered == (
        "Stored session working directory does not exist: /gone\n"
        "Session file: /sessions/abc.jsonl\n"
        "Current working directory: /work"
    )


def test_format_missing_session_cwd_error_omits_session_file_when_none() -> None:
    """Pi parity ``:30-37``: the Session file line is conditional on
    ``issue.session_file is not None``.
    """

    issue = SessionCwdIssue(
        session_cwd="/gone", fallback_cwd="/work", session_file=None
    )
    rendered = format_missing_session_cwd_error(issue)
    assert rendered == (
        "Stored session working directory does not exist: /gone\n"
        "Current working directory: /work"
    )
    assert "Session file" not in rendered


def test_format_missing_session_cwd_prompt_matches_pi_verbatim() -> None:
    """Pi parity ``:40-42``: TUI confirmation prompt.

    Pi ``session-cwd.ts:40-42``::

        cwd from session file does not exist
        <issue.session_cwd>

        continue in current cwd
        <issue.fallback_cwd>
    """

    issue = SessionCwdIssue(
        session_cwd="/gone",
        fallback_cwd="/work",
        session_file="/sessions/abc.jsonl",
    )
    rendered = format_missing_session_cwd_prompt(issue)
    assert rendered == (
        "cwd from session file does not exist\n/gone\n\n"
        "continue in current cwd\n/work"
    )


async def test_assert_raises_when_issue_present() -> None:
    """:func:`assert_session_cwd_exists` re-raises the issue as the error."""

    fs = _FakeFs(exists=False)
    session = _FakeSession(session_file="/s.jsonl", cwd="/gone")
    with pytest.raises(MissingSessionCwdError) as exc_info:
        await assert_session_cwd_exists(
            session,  # type: ignore[arg-type]
            fallback_cwd="/work",
            fs=fs,  # type: ignore[arg-type]
        )
    assert exc_info.value.issue.session_cwd == "/gone"
    assert exc_info.value.issue.fallback_cwd == "/work"


async def test_assert_is_noop_when_fallback_is_none() -> None:
    """P-346: without a fallback, the helper returns ``None`` and the
    assertion is a no-op (cannot construct a Pi-shape diagnostic).
    """

    fs = _FakeFs(exists=False)
    session = _FakeSession(session_file="/s.jsonl", cwd="/gone")
    await assert_session_cwd_exists(
        session,  # type: ignore[arg-type]
        fallback_cwd=None,
        fs=fs,  # type: ignore[arg-type]
    )
