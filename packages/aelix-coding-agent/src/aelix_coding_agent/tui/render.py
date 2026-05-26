"""Sprint 6h₁₀a (ADR-0104) / 6h₁₀b (ADR-0105) — harness-event → output renderer.

:class:`EventRenderer` is the TUI's :meth:`AgentHarness.subscribe` sink. It
mirrors the rpc/print frontends but renders to the TUI instead of JSONL/stdout.

Sprint 6h₁₀b rework (ADR-0105): output no longer goes to a Rich ``Live`` (the
live region is owned by the prompt-toolkit chrome). Instead the renderer emits
through two synchronous sinks the chrome shell wires up:

- ``commit(renderable)`` — a finished Rich renderable → scrollback (queued, then
  flushed above the chrome via ``in_terminal``).
- ``set_tail(ansi)`` — the in-progress streamed-text window → the chrome stream
  widget (``StreamRenderer`` owns the window/throttle logic).

Dispatch is ``match`` on the ``type`` Literal; unknown types are no-ops
(forward-compatible). Terminal failures surface on ``message_end`` via
``stop_reason`` (loop.py path; the streaming ``error`` event is never re-emitted
as a ``MessageUpdateEvent``). Out-of-band prints finalize any open text stream
first so they never interleave with the live tail.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import AssistantMessage
from rich.text import Text

from .stream import StreamRenderer

if TYPE_CHECKING:
    from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
    from aelix_agent_core.types import AgentEvent
    from aelix_ai.streaming import AssistantMessageEvent

    from .descriptors import DescriptorRenderer


def _result_text(result: Any) -> str:
    """Extract a printable string from a ``ToolResult`` (or any payload)."""

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
    items = items.replace("\n", " ")
    return items if len(items) <= 80 else items[:77] + "…"


class EventRenderer:
    """Renders the harness :data:`AgentEvent` stream via commit/tail sinks.

    :param commit: sync sink for a finished Rich renderable (→ scrollback).
    :param set_tail: sync sink for the in-progress streamed-text window (→ chrome).
    :param width: render width for streamed text.
    """

    def __init__(
        self,
        *,
        commit: Callable[[object], None],
        set_tail: Callable[[str], None],
        width: int = 80,
    ) -> None:
        self._commit = commit
        self._set_tail = set_tail
        self._width = width
        self._text_stream: StreamRenderer | None = None
        self._text_accum: str = ""
        self._thinking_accum: str = ""
        # §B — live tool-result interception. Late-bound by run_tui to read the
        # descriptor registry by reference (returns a matching tool-renderer-desc
        # envelope for a tool_name, or None). ``descriptor_renderer`` builds the
        # custom view. Both unset → default Text-dump rendering (unchanged).
        self.get_tool_renderer_desc: Callable[[str], DescriptorEnvelope | None] | None = None
        self.descriptor_renderer: DescriptorRenderer | None = None

    def on_agent_event(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._reset_message_state()
        elif event.type == "message_update":
            self._on_stream_event(event.assistant_message_event)
        elif event.type == "message_end":
            # Terminal failures arrive here as a MessageEndEvent whose message
            # carries stop_reason ∈ {"error","aborted"} (loop.py:299-310).
            self._finalize_text()
            self._render_message_error(event.message)
        elif event.type == "turn_end":
            self._finalize_text()
        elif event.type == "tool_execution_start":
            self._render_tool_start(event.tool_name, event.args)
        elif event.type == "tool_execution_end":
            self._render_tool_end(event.tool_name, event.result, event.is_error)
        # tool_execution_update / turn_start / agent_* / unknown → no-op.

    def finalize(self) -> None:
        """Close any open streamed-text window (e.g. after a turn error)."""

        self._finalize_text()

    # === streaming-layer dispatch ==========================================

    def _on_stream_event(self, sev: AssistantMessageEvent) -> None:
        if sev.type == "text_delta":
            if self._text_stream is None:
                self._text_stream = self._new_stream()
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
            self._commit(Text(f"✖ {message}", style="bold red"))

    # === helpers ===========================================================

    def _new_stream(self) -> StreamRenderer:
        return StreamRenderer(
            commit=lambda ansi: self._commit(Text.from_ansi(ansi)),
            set_tail=self._set_tail,
            width=self._width,
        )

    def _reset_message_state(self) -> None:
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
            self._commit(Text(f"✖ {detail}", style="bold red"))

    def _flush_thinking(self, content: str) -> None:
        self._finalize_text()
        text = content.strip()
        self._thinking_accum = ""
        if text:
            self._commit(Text(text, style="dim italic"))

    def _render_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        self._finalize_text()
        summary = _compact_args(args)
        label = f"⚙ {tool_name}({summary})" if summary else f"⚙ {tool_name}"
        self._commit(Text(label, style="cyan"))

    def _render_tool_end(self, tool_name: str, result: Any, is_error: bool) -> None:
        self._finalize_text()
        text = _result_text(result).rstrip()
        # §B — a stored tool-renderer-desc for this tool_name renders a custom view
        # (table/grid/form/text) instead of the default Text dump. The default
        # rendering is unchanged whenever no descriptor matches (or the lookup /
        # build raises — a faulty renderer must not swallow tool output).
        if self._render_with_descriptor(tool_name, text):
            return
        if not text:
            return
        self._commit(Text(text, style="red" if is_error else ""))

    def _render_with_descriptor(self, tool_name: str, text: str) -> bool:
        lookup = self.get_tool_renderer_desc
        renderer = self.descriptor_renderer
        if lookup is None or renderer is None or not tool_name:
            return False
        try:
            envelope = lookup(tool_name)
            if envelope is None:
                return False
            rows = renderer.project_tool_result(envelope, text)
            self._commit(renderer.build_tool_renderable(envelope, rows))
        except Exception:  # noqa: BLE001 — fall back to default on any failure
            return False
        return True


__all__ = ["EventRenderer"]
