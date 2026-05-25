"""``create_app`` factory + lifespan + route registration (Sprint 6h₉f §4.2).

Uses the modern ``lifespan=`` async context manager (NOT the deprecated
``@app.on_event``). A module-level ``app = create_app()`` is exposed so
``uvicorn.run("aelix_server.app:app", ...)`` can import it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from aelix_server.config import ServerConfig
from aelix_server.rpc_ws import rpc_websocket
from aelix_server.schemas import get_schema


def create_app(config: ServerConfig | None = None) -> FastAPI:
    """Build the aelix-server FastAPI application.

    Stores ``config`` plus the single-flight ``/rpc`` state
    (``rpc_active`` flag) on ``app.state`` via the lifespan.
    """

    resolved_config = config or ServerConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.config = resolved_config
        app.state.rpc_active = False  # single-flight flag for /rpc
        yield

    app = FastAPI(title="aelix-server", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/schemas/{name}")
    async def schemas(name: str, request: Request) -> FileResponse:
        return await get_schema(name, request)

    @app.websocket("/rpc")
    async def rpc(websocket: WebSocket) -> None:
        await rpc_websocket(websocket)

    return app


# Module-level instance for ``uvicorn.run("aelix_server.app:app", ...)``.
app = create_app()
