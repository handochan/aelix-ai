"""``WS /rpc`` transport bridge over ``run_rpc_mode`` (Sprint 6h₉f §4.3).

The core of aelix-server: a full-duplex JSONL RPC bridge over a single
WebSocket, reusing ``run_rpc_mode`` verbatim. The JSONL RPC wire format is
identical to the TUI stdio transport, so there is no translation layer — WS
text frames are fed into an :class:`asyncio.StreamReader` (the ``stdin``
seam) and ``run_rpc_mode``'s ``stdout_write`` byte sink is drained back to
the socket.

Single-flight (§4.3): only ONE active ``/rpc`` connection at a time. A second
concurrent connection is rejected with ``close(code=1013)`` BEFORE
``accept()``. This both matches the single-user dev model and avoids
``run_rpc_mode``'s process-global ``redirect_stdout(sys.stderr)`` nesting
across concurrent connections.

Empty-line note (W1 verification): ``run_rpc_mode``'s internal ``_on_line``
guards empty lines (``rpc_mode.py:1997-1999`` — ``if not line.strip():
return``), so feeding ``text.encode() + b"\\n"`` is safe even when a client
frame already ends in a newline. We additionally strip a single trailing
newline from the received text before re-adding exactly one ``\\n`` so each
frame is a clean single JSONL line.

MCP cross-task hazard (W0 / §0): ``McpClientManager`` is NOT yet wired into
the harness lifecycle, so the ADR-0101 anyio cancel-scope cross-task
constraint is NOT triggered here. We still keep the reader / writer /
``run_rpc_mode`` on one anyio task group so any FUTURE per-connection MCP
resource opens + closes on the same task. Documented for the MCP-integration
sprint.
"""

from __future__ import annotations

import asyncio

import anyio
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime.agent_session_runtime import (
    create_agent_session_runtime,
)
from aelix_agent_core.session.fs import LocalFileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.session import Session
from aelix_ai.streaming import Model
from aelix_coding_agent.modes import run_rpc_mode
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from aelix_server.config import ServerConfig


async def rpc_websocket(websocket: WebSocket) -> None:
    """Full-duplex JSONL RPC bridge for one WebSocket connection.

    Lifecycle: single-flight guard → ``accept`` → per-connection harness +
    runtime (mirrors ``cli/entry.py:277-302``) → anyio task group with a
    ws→reader pump, a queue→ws drain (the SOLE sender), and ``run_rpc_mode``
    → ``finally`` resets ``rpc_active``.
    """

    config: ServerConfig = websocket.app.state.config

    # --- single-flight guard (BEFORE accept) ---------------------------------
    # Reject the 2nd concurrent connection with 1013 ("try again later").
    if websocket.app.state.rpc_active:
        await websocket.close(code=1013)
        return
    websocket.app.state.rpc_active = True
    await websocket.accept()
    try:
        # --- per-connection harness + runtime (mirror cli/entry.py:277-302) --
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs)
        session = await repo.create(JsonlSessionCreateOptions(cwd=config.cwd))
        model = Model(id=config.model, provider=config.provider)

        async def _harness_factory(new_session: Session) -> AgentHarness:
            return AgentHarness(
                AgentHarnessOptions(
                    model=model,
                    session=new_session,
                    cwd=config.cwd,
                )
            )

        harness = await _harness_factory(session)
        runtime = await create_agent_session_runtime(
            harness, _harness_factory, repo=repo, fs=fs
        )

        # --- transport bridge ------------------------------------------------
        reader = asyncio.StreamReader()
        out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def stdout_write(data: bytes) -> None:
            # SYNCHRONOUS sink — run_rpc_mode calls this from the agent-turn
            # task. Never await here; hand the bytes to the single writer
            # task via an unbounded queue.
            out_queue.put_nowait(data)

        async def pump_ws_to_reader() -> None:
            try:
                while True:
                    text = await websocket.receive_text()
                    # Normalise to exactly one trailing newline so each WS
                    # frame becomes one clean JSONL line (empty lines are
                    # tolerated by _on_line, but keep the wire tidy).
                    line = text.rstrip("\n")
                    reader.feed_data(line.encode("utf-8") + b"\n")
            except WebSocketDisconnect:
                pass
            finally:
                # Signals run_rpc_mode's stdin EOF → its main loop returns
                # cleanly and disposes the runtime.
                reader.feed_eof()

        async def drain_queue_to_ws() -> None:
            # The ONLY task that calls websocket.send_*  (single-sender
            # invariant — required for WebSocket correctness).
            while True:
                item = await out_queue.get()
                if item is None:
                    return
                try:
                    await websocket.send_text(item.decode("utf-8"))
                except (WebSocketDisconnect, RuntimeError):
                    return

        async with anyio.create_task_group() as tg:
            tg.start_soon(pump_ws_to_reader)
            tg.start_soon(drain_queue_to_ws)
            # run_rpc_mode owns command dispatch + event emission; it returns
            # at stdin EOF (client disconnect) and disposes runtime_host in
            # its own teardown.
            await run_rpc_mode(
                harness,
                runtime_host=runtime,
                harness_factory=_harness_factory,
                stdin=reader,
                stdout_write=stdout_write,
                install_signal_handlers=False,
            )
            # run_rpc_mode returned (EOF). Stop the writer, then cancel the
            # pump if it is still blocked on receive.
            out_queue.put_nowait(None)
            tg.cancel_scope.cancel()
    finally:
        websocket.app.state.rpc_active = False
