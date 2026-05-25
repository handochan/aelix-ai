"""Aelix server — FastAPI + uvicorn daemon (Sprint 6h₉f, ADR-0103).

Aelix-additive (ADR-0097 multi-frontend). Pi has no server daemon — Pi pin
``734e08e`` held, zero Pi feature imported. The server is a thin WebSocket
transport adapter over the existing ``run_rpc_mode`` (the JSONL RPC wire is
identical to the TUI stdio transport — no translation layer).
"""

from __future__ import annotations

from aelix_server.app import create_app
from aelix_server.config import ServerConfig

__all__ = [
    "ServerConfig",
    "create_app",
]
