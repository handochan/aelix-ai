"""Sprint 6h₁₀a (ADR-0104) — harness-event → Rich renderer for the TUI shell.

:class:`EventRenderer` is the TUI's :meth:`AgentHarness.subscribe` sink. It
mirrors the rpc/print frontends (``rpc_mode._on_agent_event`` /
``print_mode._emit``) but renders to a Rich console instead of serializing to
JSONL/stdout.

Two event layers are dispatched (verified shapes):

- **harness layer** — :data:`~aelix_agent_core.types.AgentEvent` (``message_*``,
  ``tool_execution_*``, ``turn_*``, ``agent_*``).
- **streaming layer** — :data:`~aelix_ai.streaming.AssistantMessageEvent`
  carried inside ``MessageUpdateEvent.assistant_message_event`` (``text_delta``,
  ``thinking_delta``, ``done``, ``error``, …).

Dispatch is ``match`` on the ``type`` Literal; an unrecognised ``type`` is a
no-op so future event variants never crash the TUI (forward-compatible).

Thin-shell scope (Sprint 6h₁₀a): streamed assistant text via
:class:`~aelix_coding_agent.tui.stream.StreamRenderer`; thinking rendered dim
on completion; tool calls rendered as a header + result. Tool-argument
streaming (``toolcall_*``) is intentionally a no-op — tool rendering keys off
the harness-layer ``tool_execution_*`` events to avoid double-render. Live
chrome / descriptor rendering / themes land in Sprint 6h₁₀b.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import AssistantMessage
from rich.console import Console
from rich.text import Text

from .stream import StreamRenderer

if TYPE_CHECKING:
    from aelix_agent_core.types import AgentEvent
    from aelix_ai.streaming import AssistantMessageEvent


def _result_text(result: Any) -> str:
    """Extract a printable string from a ``ToolResult`` (or any payload).

    Defensive: ``ToolResult.content`` is a list of content blocks (each may
    expose ``.text``) or a plain string; fall back to ``str`` otherwise.
    """

    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            parts.append(text if isinstance(text, str) else str(block))
        return "\n".join(parts)
    return str(content)


def _compact_args(args: dict[str, Any]) -> str:
    """One-line, length-capped argument summary for a tool header."""

    if not args:
        return ""
    items = ", ".join(f"{k}={v!r}" for k, v in args.items())
    items = items.replace("\n", " ")  # keep the header on one line
    return items if len(items) <= 80 else items[:77] + "…"


class EventRenderer:
    """Renders the harness :data:`AgentEvent` stream to a Rich console.

    :param console: shared output console (→ terminal scrollback).
    :param stream_factory: builds the per-message text :class:`StreamRenderer`;
        injectable for tests. Defaults to ``StreamRenderer(console)``.
    """

    def __init__(
        self,
        console: Console,
        *,
        stream_factory: Callable[[], StreamRenderer] | None = None,
    ) -> None:
        self._console = console
        self._stream_factory = stream_factory or (lambda: StreamRenderer(console))
        self._text_stream: StreamRenderer | None = None
        self._text_accum: str = ""
        self._thinking_accum: str = ""

    def on_agent_event(self, event: AgentEvent) -> None:
        """Subscribe sink — synchronous (no ``await``; no turn reentrancy).

        Comparing the ``type`` Literal inline lets the type checker narrow the
        discriminated union, so each branch accesses only fields that exist on
        the matched variant.
        """

        if event.type == "message_start":
            self._reset_message_state()
        elif event.type == "message_update":
            self._on_stream_event(event.assistant_message_event)
        elif event.type == "message_end":
            # The agent loop delivers terminal failures as a MessageEndEvent
            # whose message carries stop_reason ∈ {"error","aborted"} +
            # error_message (loop.py:299-310) — the streaming-layer "error"
            # event is never re-emitted as a MessageUpdateEvent — so the error
            # must be surfaced here, mirroring run_print_mode (print_mode.py).
            self._finalize_text()
            self._render_message_error(event.message)
        elif event.type == "turn_end":
            self._finalize_text()
        elif event.type == "tool_execution_start":
            self._render_tool_start(event.tool_name, event.args)
        elif event.type == "tool_execution_end":
            self._render_tool_end(event.result, event.is_error)
        # tool_execution_update / turn_start / agent_start / agent_end /
        # unknown → no-op (forward-compatible).

    def finalize(self) -> None:
        """Close any open streamed-text region (e.g. after a turn error)."""

        self._finalize_text()

    # === streaming-layer dispatch ===========================================

    def _on_stream_event(self, sev: AssistantMessageEvent) -> None:
        if sev.type == "text_delta":
            if self._text_stream is None:
                self._text_stream = self._stream_factory()
            self._text_accum += sev.delta
            self._text_stream.update(self._text_accum)
        elif sev.type == "text_end":
            if sev.content:
                self._text_accum = sev.content
            self._finalize_text()
        elif sev.type == "thinking_delta":
            self._thinking_accum += sev.delta
        elif sev.type == "thinking_end":
            self._flush_thinking(sev.content or self._thinking_accum)
        elif sev.type in ("done", "end"):
            self._finalize_text()
        elif sev.type == "error":
            self._finalize_text()
            message = sev.error_message or f"request {sev.reason}"
            self._console.print(Text(f"✖ {message}", style="bold red"))
        # *_start / toolcall_* → no-op.

    # === rendering helpers ==================================================

    def _reset_message_state(self) -> None:
        # Finalize any text left open by a prior message (defensive).
        self._finalize_text()
        self._text_accum = ""
        self._thinking_accum = ""

    def _finalize_text(self) -> None:
        if self._text_stream is not None:
            self._text_stream.update(self._text_accum, final=True)
            self._text_stream = None
            self._text_accum = ""

    def _render_message_error(self, message: object) -> None:
        if isinstance(message, AssistantMessage) and message.stop_reason in (
            "error",
            "aborted",
        ):
            detail = message.error_message or f"request {message.stop_reason}"
            self._console.print(Text(f"✖ {detail}", style="bold red"))

    def _flush_thinking(self, content: str) -> None:
        # Defensive: close any open text region before this out-of-band print
        # so it never lands inside an active Live region (the canonical flow
        # finalizes via message_end first, but don't depend on emission order).
        self._finalize_text()
        text = content.strip()
        self._thinking_accum = ""
        if text:
            self._console.print(Text(text, style="dim italic"))

    def _render_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        self._finalize_text()
        summary = _compact_args(args)
        label = f"⚙ {tool_name}({summary})" if summary else f"⚙ {tool_name}"
        self._console.print(Text(label, style="cyan"))

    def _render_tool_end(self, result: Any, is_error: bool) -> None:
        self._finalize_text()
        text = _result_text(result).rstrip()
        if not text:
            return
        self._console.print(Text(text, style="red" if is_error else ""))


__all__ = ["EventRenderer"]
