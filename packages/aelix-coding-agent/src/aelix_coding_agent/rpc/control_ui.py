"""Correlated, bounded dialogs for explicitly enabled RPC command surfaces (ADR-0275).

Bound onto the runtime only around ``commands/run`` and ``agents/run``. It is a
dialog channel for those flows and NOT a tool-approval channel: the host approves
tool calls through its own ``tool_call`` hook, so ``provides_tool_approval`` tells
the built-in permission gate to keep its headless verdict (#594).

Every dialog ends the same way however it ends — a reply, a timeout, the caller's
signal, or the host shutting down: an answer, or the cancelled value. Methods with
no RPC meaning (terminal widgets, editor control, themes) are no-ops here, as in
pi's RPC mode, instead of the headless binding's ``NotImplementedError`` — an
extension that saw ``ctx.has_ui`` is entitled to call them.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from aelix_coding_agent.extensions.ext_ui import SetThemeResult, ThemeInfo
from aelix_coding_agent.extensions.headless_ui import HeadlessExtensionUIContext

# A dialog nobody answers still ends. The ACP adapter answers its own client
# requests within 290 s, so this outer bound is only ever reached by a host that
# never replies.
DIALOG_TIMEOUT_SECONDS = 300.0
# Module-private: a bound on one host's open dialogs, not an extension-band
# policy constant (tests/agents/test_p2_band_boundaries.py).
_MAX_PENDING_DIALOGS = 32


class RpcCommandUI(HeadlessExtensionUIContext):
    provides_tool_approval = False

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self.emit = emit
        self.enabled = False
        self.closed = False
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def request(self, method: str, opts: Any = None, **params: Any) -> dict[str, Any]:
        """One correlated dialog. Returns the reply, or ``{"cancelled": True}``."""

        if self.closed:
            return {"cancelled": True}
        if not self.enabled:
            raise RuntimeError(
                "RPC dialog client is unavailable; enable ui/enable before running this command"
            )
        if len(self.pending) >= _MAX_PENDING_DIALOGS:
            raise RuntimeError("Too many pending RPC dialogs")
        signal = getattr(opts, "signal", None)
        if callable(getattr(signal, "is_set", None)) and signal.is_set():  # type: ignore[union-attr]
            return {"cancelled": True}
        requested = getattr(opts, "timeout", None)
        timeout = DIALOG_TIMEOUT_SECONDS
        if isinstance(requested, int | float) and requested > 0:
            timeout = min(float(requested), DIALOG_TIMEOUT_SECONDS)

        request_id = "ui-" + uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        waiters: set[asyncio.Future[Any]] = {future}
        aborted: asyncio.Future[Any] | None = None
        if callable(getattr(signal, "wait", None)):
            aborted = asyncio.ensure_future(signal.wait())  # type: ignore[union-attr]
        if aborted is not None:
            waiters.add(aborted)
        try:
            self.emit(
                {"type": "extension_ui_request", "id": request_id, "method": method, **params}
            )
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if future.done() and not future.cancelled():
                return future.result()
            return {"cancelled": True}
        finally:
            self.pending.pop(request_id, None)
            if aborted is not None:
                aborted.cancel()

    def respond(self, payload: dict[str, Any]) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, str):
            return
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(payload)

    def close(self) -> None:
        """The client is gone: settle every open dialog and refuse new ones as cancelled."""

        self.closed = True
        self.enabled = False
        for future in list(self.pending.values()):
            if not future.done():
                future.set_result({"cancelled": True})

    # === Dialogs ===

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        result = await self.request("select", opts, title=title, options=options)
        value = result.get("value")
        return value if not result.get("cancelled") and value in options else None

    async def confirm(self, title: str, message: str, opts: Any = None) -> bool:
        result = await self.request("confirm", opts, title=title, message=message)
        return not result.get("cancelled", False) and result.get("confirmed") is True

    async def input(
        self, title: str, placeholder: str | None = None, opts: Any = None
    ) -> str | None:
        result = await self.request("input", opts, title=title, placeholder=placeholder)
        value = result.get("value")
        return value if not result.get("cancelled") and isinstance(value, str) else None

    async def editor(self, title: str, prefill: str | None = None, opts: Any = None) -> str | None:
        result = await self.request("editor", opts, title=title, prefill=prefill)
        value = result.get("value")
        return value if not result.get("cancelled") and isinstance(value, str) else None

    # === Fire-and-forget notices (the methods the ACP adapter renders) ===

    def notice(self, method: str, **fields: Any) -> None:
        """A request that expects no reply (it still carries an id, as in pi)."""

        if self.closed:
            return
        self.emit(
            {
                "type": "extension_ui_request",
                "id": "ui-" + uuid.uuid4().hex,
                "method": method,
                **fields,
            }
        )

    def notify(self, message: str, kind: Any = "info") -> None:
        self.notice("notify", message=message, kind=kind)

    def set_status(self, key: str, text: str | None) -> None:
        self.notice("setStatus", key=key, text=text)

    def set_widget(self, key: str, content: Any, options: Any = None) -> None:
        # pi's RPC mode forwards string lines only; a component factory has no wire form.
        if content is not None and not isinstance(content, list):
            return
        lines = [str(line) for line in content] if content is not None else None
        self.notice(
            "setWidget",
            key=key,
            lines=lines,
            placement=getattr(options, "placement", None),
            text="\n".join(lines) if lines else None,
        )

    def set_title(self, title: str) -> None:
        self.notice("setTitle", title=title, text=title)

    # === No RPC meaning: no-ops, as in pi's RPC mode ===

    def on_terminal_input(self, handler: Any) -> Callable[[], None]:
        return lambda: None

    def set_working_message(self, message: str | None = None) -> None:
        return None

    def set_working_visible(self, visible: bool) -> None:
        return None

    def set_working_indicator(self, options: Any = None) -> None:
        return None

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        return None

    def set_footer(self, factory: Any) -> None:
        return None

    def set_header(self, factory: Any) -> None:
        return None

    async def custom(self, factory: Any, options: Any = None) -> object:
        return None

    def paste_to_editor(self, text: str) -> None:
        return None

    def set_editor_text(self, text: str) -> None:
        return None

    def get_editor_text(self) -> str:
        return ""

    def set_editor_component(self, factory: Any) -> None:
        return None

    def get_editor_component(self) -> Any:
        return None

    def add_autocomplete_provider(self, factory: Any) -> None:
        return None

    def get_all_themes(self) -> list[ThemeInfo]:
        return []

    def get_theme(self, name: str) -> Any:
        return None

    def set_theme(self, theme: Any) -> SetThemeResult:
        return SetThemeResult(success=False, error="Theme switching is not supported over RPC")

    def get_tools_expanded(self) -> bool:
        return False

    def set_tools_expanded(self, expanded: bool) -> None:
        return None
