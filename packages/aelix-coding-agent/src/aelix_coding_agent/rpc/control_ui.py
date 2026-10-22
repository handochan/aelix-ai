"""Correlated, bounded dialogs for explicitly enabled RPC command surfaces."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from aelix_coding_agent.extensions.headless_ui import HeadlessExtensionUIContext


class RpcCommandUI(HeadlessExtensionUIContext):
    def __init__(self, emit: Any) -> None:
        self.emit = emit
        self.enabled = False
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def request(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("RPC dialog client is unavailable; enable ui/enable before running this command")
        if len(self.pending) >= 32:
            raise RuntimeError("Too many pending RPC dialogs")
        request_id = "ui-" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.emit({"type": "extension_ui_request", "id": request_id, "method": method, **params})
            return await asyncio.wait_for(future, 300)
        finally:
            self.pending.pop(request_id, None)

    def respond(self, payload: dict[str, Any]) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str):
            return
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(payload)

    def close(self) -> None:
        for future in list(self.pending.values()):
            if not future.done():
                future.set_result({"cancelled": True})

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        result = await self.request("select", title=title, options=options)
        value = result.get("value")
        return value if not result.get("cancelled") and value in options else None

    async def confirm(self, title: str, message: str, opts: Any = None) -> bool:
        result = await self.request("confirm", title=title, message=message)
        return not result.get("cancelled", False) and result.get("confirmed") is True

    async def input(self, title: str, placeholder: str | None = None, opts: Any = None) -> str | None:
        result = await self.request("input", title=title, placeholder=placeholder)
        value = result.get("value")
        return value if not result.get("cancelled") and isinstance(value, str) else None

    async def editor(self, title: str, prefill: str | None = None, opts: Any = None) -> str | None:
        result = await self.request("editor", title=title, prefill=prefill)
        value = result.get("value")
        return value if not result.get("cancelled") and isinstance(value, str) else None

    def notify(self, message: str, kind: Any = "info") -> None:
        self.emit({"type": "extension_ui_request", "method": "notify", "message": message, "kind": kind})

    def set_status(self, key: str, text: str | None) -> None:
        self.emit({"type": "extension_ui_request", "method": "setStatus", "key": key, "text": text})
