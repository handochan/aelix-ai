"""Sprint 6h₅b · Phase 4.15 — :meth:`AgentSessionRuntime.import_from_jsonl`
real body (ADR-0083, P-360).

Pi parity: ``agent-session-runtime.ts:329-364``.

Replaces the Sprint 6h₄c ``NotImplementedError`` stub. The real body:

  1. resolve + existence probe (raises :class:`SessionImportFileNotFoundError`)
  2. mkdir + compute destination under sessions root
  3. cancel hook short-circuit
  4. copy when source != destination
  5. metadata load + ``cwd`` override via :func:`dataclasses.replace`
  6. open + assert cwd
  7. finish replacement (no ``with_session`` — Pi signature confirms)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import SessionBeforeSwitchResult
from aelix_agent_core.runtime import (
    AgentSessionRuntime,
    SessionImportFileNotFoundError,
)
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _new_harness(
    session: Session | None = None,
    extensions: list[Extension] | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
            extensions=extensions or [],
        )
    )


def _make_runtime(
    harness: AgentHarness,
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
) -> AgentSessionRuntime:
    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(harness, _factory, repo=repo, fs=fs)


async def test_missing_path_raises_session_import_file_not_found_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:329-364``: missing path → :class:`SessionImportFileNotFoundError`."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    with pytest.raises(SessionImportFileNotFoundError):
        await runtime.import_from_jsonl(str(tmp_path / "missing.jsonl"))


async def test_session_import_file_not_found_error_pi_message_and_attr(
    tmp_path: Path,
) -> None:
    """Sprint 6h₅b W6 (P-366 W5 MAJOR fix): the error message + attribute
    match Pi ``agent-session-runtime.ts:39-47`` verbatim — ``File not
    found: ${filePath}`` plus a ``file_path`` attr (snake_case of Pi
    ``filePath``).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    missing = str(tmp_path / "missing.jsonl")
    with pytest.raises(SessionImportFileNotFoundError) as exc_info:
        await runtime.import_from_jsonl(missing)
    err = exc_info.value
    # Pi-verbatim message format: ``File not found: ${filePath}``.
    assert str(err) == f"File not found: {missing}"
    # Pi attribute (Aelix snake_case): ``file_path``.
    assert err.file_path == missing


async def test_same_dir_skips_copy(tmp_path: Path) -> None:
    """When the resolved path equals the destination (already under the
    sessions root for the current cwd) the copy step is skipped — the
    operation still completes.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    source_meta = await source.get_metadata()
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    result = await runtime.import_from_jsonl(source_meta.path)
    assert result.cancelled is False
    # Session opened from same path.
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.path == source_meta.path


async def test_cwd_override_rewrites_metadata(tmp_path: Path) -> None:
    """The optional ``cwd`` argument rewrites the loaded metadata's
    ``cwd`` field via :func:`dataclasses.replace`.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source_cwd = str(tmp_path / "src")
    other_cwd = str(tmp_path / "other")
    Path(source_cwd).mkdir(parents=True, exist_ok=True)
    Path(other_cwd).mkdir(parents=True, exist_ok=True)

    source = await repo.create(JsonlSessionCreateOptions(cwd=source_cwd))
    source_meta = await source.get_metadata()
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    result = await runtime.import_from_jsonl(source_meta.path, cwd=other_cwd)
    assert result.cancelled is False
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.cwd == other_cwd


async def test_cancel_short_circuits_and_skips_copy(
    tmp_path: Path,
) -> None:
    """An extension cancelling ``session_before_switch`` short-circuits
    the import — returns ``cancelled=True`` and does NOT touch disk.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source_cwd = str(tmp_path / "src")
    other_cwd = str(tmp_path / "other")
    Path(source_cwd).mkdir(parents=True, exist_ok=True)
    Path(other_cwd).mkdir(parents=True, exist_ok=True)
    source = await repo.create(JsonlSessionCreateOptions(cwd=source_cwd))
    source_meta = await source.get_metadata()

    extension = Extension(name="canceller")
    api = ExtensionAPI(extension, runtime=_new_harness().runtime)

    async def cancel(event: Any, ctx: Any) -> SessionBeforeSwitchResult:
        return SessionBeforeSwitchResult(cancel=True)

    api.on("session_before_switch", cancel)  # type: ignore[arg-type]

    runtime = _make_runtime(
        _new_harness(session=source, extensions=[extension]), repo, fs
    )

    result = await runtime.import_from_jsonl(source_meta.path, cwd=other_cwd)
    assert result.cancelled is True


async def test_different_dir_copies_file(tmp_path: Path) -> None:
    """When source and destination differ, the JSONL is copied into the
    canonical sessions root for the target cwd.
    """

    fs = LocalFileSystem()
    other_root = tmp_path / "other_sessions"
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(other_root))

    # Place a source JSONL OUTSIDE the canonical sessions root via a
    # separate repo so the destination differs from the source path.
    outside_root = tmp_path / "outside"
    outside_repo = JsonlSessionRepo(fs=fs, sessions_root=str(outside_root))
    foreign = await outside_repo.create(
        JsonlSessionCreateOptions(cwd=str(tmp_path))
    )
    foreign_meta = await foreign.get_metadata()

    runtime = _make_runtime(_new_harness(session=foreign), repo, fs)

    result = await runtime.import_from_jsonl(foreign_meta.path)
    assert result.cancelled is False

    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    # The destination is inside the OTHER (canonical) sessions root.
    assert str(other_root) in new_meta.path
    # The source still exists (copy, not move).
    assert Path(foreign_meta.path).exists()
