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

#: The answers a ``childApproval`` dialog offers (#612, ADR-0308). ``id`` is OPEN on
#: the wire, ``kind`` closed (``session/request_permission``'s option kinds); there
#: is no ``options`` array, and any reply value that is not one of these ids is a
#: cancel, so a client that renders it as a generic dialog can approve nothing.
CHILD_APPROVAL_CHOICES: tuple[dict[str, str], ...] = (
    {"id": "yes", "label": "Yes", "kind": "allow_once"},
    {"id": "yes_session", "label": "Yes, for this agent", "kind": "allow_always"},
    {"id": "no", "label": "No", "kind": "reject_once"},
)


class RpcCommandUI(HeadlessExtensionUIContext):
    provides_tool_approval = False

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self.emit = emit
        self.enabled = False
        self.closed = False
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._methods: dict[str, str] = {}
        """Each open dialog's method, so :meth:`withdraw_child_approvals` finds the children's."""
        self._child_withdrawals = 0

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
        self._methods[request_id] = method
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
            self._methods.pop(request_id, None)
            if aborted is not None:
                aborted.cancel()

    async def child_approval(self, request: Any) -> tuple[str, str | None]:
        """Ask the host about one delegated child's call (#612, ADR-0308): a decision and its reason.

        One ``extension_ui_request`` with ``method: "childApproval"``, answered by
        an offered choice id; every other ending is ``cancel`` with the reason
        the child's model reads, told apart here because :meth:`request` returns
        a bare ``{"cancelled": True}`` for the client gone (:meth:`close`) and for
        no answer within :data:`DIALOG_TIMEOUT_SECONDS` alike (§7 review L3). The
        host's own reply is the one that carries its ``id``; the client gone is
        :attr:`closed`; what is left is the bound. No clock is read: a loop's
        timer may fire up to its clock's resolution early (15.6 ms on Windows),
        so "elapsed ≥ the bound" would call a timeout the host's cancel. Raises
        what :meth:`request` raises (dialogs off, too many open): the arbiter
        answers ``cancel`` naming it. One the host took back while it was open
        (:meth:`withdraw_child_approvals`) ends ``cancel`` "no one can answer
        approvals here" (#612 fix round 1, P-L3).
        """

        withdrawals = self._child_withdrawals
        # The method is a literal here: the control-v1 coverage gate reads it (``_ui_methods``).
        reply = await self.request(
            "childApproval",
            None,
            title=f"{request.requester}: {_child_title(request)}",
            child={
                "id": request.child_id,
                "profile": request.profile,
                "sessionId": request.session_id,
                "jobId": request.job_id,
                "parentToolCallId": request.parent_tool_call_id,
                "background": request.background,
                "cwd": request.cwd,
                "epoch": request.epoch,
            },
            tool={
                "callId": request.call_id,
                "name": request.tool_name,
                "kind": request.kind,
                "args": request.args,
            },
            choices=[dict(choice) for choice in CHILD_APPROVAL_CHOICES],
        )
        value = reply.get("value")
        if not reply.get("cancelled") and value in {c["id"] for c in CHILD_APPROVAL_CHOICES}:
            return (str(value), None)
        if self.closed:
            return ("cancel", "the host is gone")
        if "id" in reply:  # the host's own ``extension_ui_response``: a cancel, or no offered choice
            return ("cancel", "the host cancelled the request")
        if self._child_withdrawals != withdrawals:
            from aelix_coding_agent.builtin.permission import CHILD_NO_APPROVER_REASON

            return ("cancel", CHILD_NO_APPROVER_REASON)
        return ("cancel", f"no answer from the host within {DIALOG_TIMEOUT_SECONDS:g} s")

    def withdraw_child_approvals(self) -> None:
        """The host stopped answering children (``ui/enable`` took the binding back, P-L3).

        Each ``childApproval`` dialog still open ends now, as no one there to
        answer it: the arbiter releases the gate's lock and the child reads "Not
        approved: no one can answer approvals here."; a reply the host sends for
        it later is ignored (:meth:`respond` finds nothing open). Other dialogs
        are untouched.
        """

        self._child_withdrawals += 1
        for request_id, method in list(self._methods.items()):
            future = self.pending.get(request_id)
            if method == "childApproval" and future is not None and not future.done():
                future.set_result({"cancelled": True})

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


def _child_title(request: Any) -> str:
    """The question a child's call asks, as the TUI dialog titles it (``approval_dialog``)."""

    args = request.args if isinstance(request.args, dict) else {}
    if request.kind == "bash":
        return "Run shell command?"
    if request.kind in ("write", "edit"):
        path = next((args[k] for k in ("path", "file_path") if isinstance(args.get(k), str)), "")
        verb = "Create/overwrite" if request.kind == "write" else "Edit"
        return f"{verb} {path or '(unknown path)'}?"
    return f"Allow {request.tool_name}?"
