"""Built-in PermissionExtension — interactive allow/deny gate on ``tool_call``.

Phase 1 of the tool-call permission/approval system. Modelled on
``@gotgenes/pi-permission-system``: mutating tools (bash-family + write-family)
are gated behind an interactive 4-option dialog when a UI is attached:

- ``Yes`` — allow this one call.
- ``Yes, for this session`` — allow + synthesize an ephemeral wildcard rule so
  similar calls in this session are auto-approved.
- ``No`` — block with a generic denial reason.
- ``No, provide reason`` — block with a user-supplied reason.

Esc / cancellation (``select`` returns ``None``) is treated as a denial.

Design notes:

- Read-only tools are silently allowed (``return None``) — no prompt.
- Headless / print / RPC runs (``not ctx.has_ui``) default to ALLOW so the
  non-interactive behaviour is preserved; :class:`GuardrailExtension` still
  hard-blocks dangerous patterns separately.
- Session rules are *ephemeral* — held in-memory for the process lifetime and
  cleared on the ``session_shutdown`` hook (which exists per
  :class:`~aelix_agent_core.harness.hooks.SessionShutdownHookEvent`).
- Prompts are serialized through an :class:`asyncio.Lock` so parallel tool
  calls cannot race two modals; the session-allow set is re-checked inside the
  lock to avoid prompting twice for a rule a concurrent prompt just added.

Registered AFTER :class:`GuardrailExtension` in ``cli/entry.py`` so hard-deny
guardrail patterns (e.g. ``rm -rf``) short-circuit via first-block-wins BEFORE
the permission prompt is shown.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
)

from aelix_coding_agent.builtin.guardrail import _BASH_TOOLS, _WRITE_TOOLS
from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext

# Mutating tools gated by the permission prompt — the union of the bash-family
# and write-family sets the guardrail uses.
_MUTATING = _BASH_TOOLS | _WRITE_TOOLS

# Dialog option labels (pi-permission-system parity).
_YES = "Yes"
_YES_SESSION = "Yes, for this session"
_NO = "No"
_NO_REASON = "No, provide reason"
_OPTIONS = [_YES, _YES_SESSION, _NO, _NO_REASON]


def _command_from_args(args: dict[str, Any]) -> str:
    """Best-effort extraction of the command string from a bash-family call."""

    for key in ("command", "cmd", "shell_command", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _path_from_args(args: dict[str, Any]) -> str:
    """Best-effort extraction of the target path from a write-family call."""

    for key in ("path", "file_path", "file", "filename", "filepath", "target"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _rule_key(tool_name: str, args: dict[str, Any]) -> str:
    """Build the exact, TOOL-NAMESPACED rule key a call is matched against.

    The ``bash:`` / ``write:`` / ``tool:`` namespace prefix is literal in both
    the key and the synthesized wildcard, so a write rule can NEVER fnmatch a
    bash key and vice versa (W4 code-review MEDIUM — fnmatch ``*`` crosses
    spaces, so an un-namespaced ``src/*`` would match a bash ``src/foo.sh ...``).
    """

    if tool_name in _BASH_TOOLS:
        return f"bash:{_command_from_args(args) or tool_name}"
    if tool_name in _WRITE_TOOLS:
        return f"write:{_path_from_args(args) or tool_name}"
    return f"tool:{tool_name}"


def _session_wildcard(tool_name: str, args: dict[str, Any]) -> str:
    """Synthesize a TOOL-NAMESPACED ephemeral session rule from a call.

    NEVER emits a bare ``*`` (W4 code-review HIGH): a ``*`` wildcard would
    fnmatch EVERY future rule_key, so approving-for-session one innocuous call
    would silently disarm the whole gate for the session. A call with no safe
    scope is pinned to its EXACT key instead.

    - bash-family: a multi-token command → ``bash:{tok0} {tok1} *`` (matches
      that command prefix, e.g. ``git status --short`` → ``bash:git status *``);
      a single-token command → ``bash:{command}`` EXACT (no broadening prefix).
    - write-family: a path with a parent dir → ``write:{parent}/*`` (covers
      that directory and its descendants for the session — acceptable for an
      ephemeral approval); a bare filename (no parent) → ``write:{path}`` EXACT.
    - fallback: ``tool:{tool_name}`` exact.
    """

    if tool_name in _BASH_TOOLS:
        command = _command_from_args(args)
        if not command:
            return f"bash:{tool_name}"
        tokens = command.split()
        if len(tokens) <= 1:
            return f"bash:{command}"  # exact — a single token has no safe prefix
        return f"bash:{tokens[0]} {tokens[1]} *"
    if tool_name in _WRITE_TOOLS:
        path = _path_from_args(args)
        if not path:
            return f"write:{tool_name}"
        norm = path.replace("\\", "/")
        parent = norm.rsplit("/", 1)[0] if "/" in norm else ""
        return f"write:{parent}/*" if parent else f"write:{norm}"
    return f"tool:{tool_name}"


def _summary(tool_name: str, args: dict[str, Any]) -> str:
    """A short one-line summary of the call for the dialog title."""

    if tool_name in _BASH_TOOLS:
        return _command_from_args(args).strip()[:120]
    if tool_name in _WRITE_TOOLS:
        return _path_from_args(args).strip()[:120]
    return ""


@dataclass
class PermissionExtension:
    """Interactive allow/deny gate registered as a built-in extension.

    Instances are valid
    :class:`~aelix_coding_agent.extensions.api.ExtensionFactory` callables —
    ``__call__(self, aelix)`` registers the ``tool_call`` + ``session_shutdown``
    handlers.
    """

    _session_allows: set[str] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __call__(self, aelix: ExtensionAPI) -> None:
        """Setup: register the ``tool_call`` + ``session_shutdown`` handlers."""

        aelix.on("tool_call", self._on_tool_call)
        aelix.on("session_shutdown", self._on_shutdown)

    def _is_session_allowed(self, rule_key: str) -> bool:
        return any(fnmatch(rule_key, w) for w in self._session_allows)

    async def _on_tool_call(
        self,
        event: ToolCallHookEvent,
        ctx: ExtensionContext,
    ) -> ToolCallResult | None:
        # Read-only tools are silently allowed.
        if event.tool_name not in _MUTATING:
            return None

        rule_key = _rule_key(event.tool_name, event.args)

        # Session-approved (wildcard match) → allow without prompting.
        if self._is_session_allowed(rule_key):
            return None

        # Headless / print / RPC default = ALLOW (preserve non-interactive
        # behaviour; the guardrail still hard-blocks separately).
        if not ctx.has_ui:
            return None

        # Serialize prompts so parallel tool calls never race two modals.
        async with self._lock:
            # Re-check inside the lock — a concurrent prompt may have just
            # added a matching session rule.
            if self._is_session_allowed(rule_key):
                return None

            summary = _summary(event.tool_name, event.args)
            title = f"Allow {event.tool_name}? {summary}".rstrip()
            # Fail SAFE: if the UI prompt itself raises mid-turn (terminal
            # detached / app torn down), block rather than let the exception
            # abort the turn via the hook's throw default (W4 code-review MEDIUM).
            try:
                choice = await ctx.ui.select(title, _OPTIONS)
            except Exception as exc:  # noqa: BLE001 — deny-on-error is fail-safe
                return ToolCallResult(
                    block=True,
                    reason=(
                        "Permission prompt unavailable; denied for safety "
                        f"({exc.__class__.__name__})."
                    ),
                )

            if choice == _YES:
                return None
            if choice == _YES_SESSION:
                self._session_allows.add(
                    _session_wildcard(event.tool_name, event.args)
                )
                return None
            if choice == _NO:
                return ToolCallResult(block=True, reason="Denied by the user.")
            if choice == _NO_REASON:
                try:
                    reason = await ctx.ui.input("Why is this denied?")
                except Exception:  # noqa: BLE001 — reason is optional; still deny
                    reason = None
                return ToolCallResult(
                    block=True,
                    reason=f"Denied by the user: {reason or '(no reason given)'}",
                )
            # None (Esc / cancelled) or any unexpected value → deny.
            return ToolCallResult(
                block=True,
                reason="Denied by the user (cancelled).",
            )

    def _on_shutdown(
        self,
        _event: SessionShutdownHookEvent,
        _ctx: ExtensionContext,
    ) -> None:
        self._session_allows.clear()


__all__ = ["PermissionExtension"]
