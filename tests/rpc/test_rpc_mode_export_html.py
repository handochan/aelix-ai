"""Sprint 6h₃ (ADR-0073, P-270/P-279/P-281) — RPC ``export_html`` handler tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_export_html,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandExportHtml,
    RpcErrorResponse,
    RpcSuccessResponse,
)


def _quiet_stream_fn() -> Any:
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


def _make_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


async def _make_jsonl_harness(
    tmp_path: Path, session_id: str = "rpc-export"
) -> tuple[AgentHarness, str]:
    """Build a harness with a real JSONL session so ``export_html``
    passes the Pi precondition gate (P-279).
    """

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session

    fs = LocalFileSystem()
    file_path = str(tmp_path / f"{session_id}.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id=session_id
    )
    session = Session(storage)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            session=session,
        )
    )
    return harness, file_path


async def test_export_html_with_output_path_returns_path(tmp_path: Path) -> None:
    """Pi parity: ``{output_path}`` → response ``{path}`` is the resolved path."""

    out = tmp_path / "session.html"
    harness, _file_path = await _make_jsonl_harness(tmp_path)
    try:
        cmd = RpcCommandExportHtml(id="r1", output_path=str(out))
        response = await _handle_export_html(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "export_html"
        assert isinstance(response.data, dict)
        assert response.data == {"path": str(out.resolve())}
        assert out.exists()
    finally:
        await harness.dispose()


async def test_export_html_without_output_path_returns_pi_shape_default(
    tmp_path: Path,
) -> None:
    """Pi parity (P-281 W6): ``output_path=None`` → cwd-relative
    ``aelix-session-<basename>.html`` path returned.
    """

    import os

    harness, _file_path = await _make_jsonl_harness(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        cmd = RpcCommandExportHtml(id="r2", output_path=None)
        response = await _handle_export_html(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        path = response.data["path"]
        p = Path(path)
        assert p.exists()
        # Pi-shape default name.
        assert p.name == "aelix-session-rpc-export.html"
    finally:
        os.chdir(cwd)
        await harness.dispose()


async def test_export_html_in_memory_session_returns_rpc_error() -> None:
    """Pi parity (P-279 W6): an in-memory session surfaces a Pi-shape
    :class:`RpcErrorResponse` (not a success envelope). The RPC outer
    dispatcher (:func:`_handle_command`) converts the harness-side
    :exc:`RuntimeError` into a Pi-shape error envelope.
    """

    from aelix_coding_agent.rpc.rpc_mode import _handle_command

    harness = _make_harness()
    try:
        table = build_dispatch_table()
        response = await _handle_command(
            harness,
            {"type": "export_html", "id": "r-in-mem", "output_path": None},
            table,
        )
        assert isinstance(response, RpcErrorResponse)
        assert response.command == "export_html"
        assert "in-memory" in response.error
    finally:
        await harness.dispose()


async def test_dispatch_table_routes_export_html() -> None:
    """The dispatcher table contains the real handler (not a deferred stub)."""

    table = build_dispatch_table()
    handler = table.get("export_html")
    assert handler is not None
    name = getattr(handler, "__qualname__", repr(handler))
    assert "deferred" not in name.lower()


async def test_export_html_wire_shape_is_single_path_key(tmp_path: Path) -> None:
    """Pi parity: ``data`` is exactly ``{path: str}`` — no extra keys."""

    out = tmp_path / "x.html"
    harness, _file_path = await _make_jsonl_harness(tmp_path)
    try:
        cmd = RpcCommandExportHtml(id="r3", output_path=str(out))
        response = await _handle_export_html(harness, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert isinstance(response.data, dict)
        # Strict: exactly one key, the Pi-shaped ``path``.
        assert set(response.data.keys()) == {"path"}
        assert isinstance(response.data["path"], str)
    finally:
        await harness.dispose()
