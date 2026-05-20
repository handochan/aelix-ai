"""Pi parity: ``packages/coding-agent/src/modes/rpc/rpc-types.ts`` (262 LOC).

RPC command / response / session-state / slash-command / extension-UI
types. 29 ``RpcCommand`` variants (Pi ``rpc-types.ts:19-69``), 24 success
``RpcResponse`` variants + 1 uniform error envelope, ``RpcSessionState``
(12 fields, Pi ``:90-103``), 9 ``RpcExtensionUIRequest`` methods + 3
``RpcExtensionUIResponse`` shapes.

JSON serialization: snake_case Python ↔ camelCase Pi wire. Per-class
``to_json()`` / ``from_json()`` helpers handle the remap. ``parse_rpc_command``
is the discriminator-based dispatcher.

Sprint 6d ships every type Pi has on the wire. Bridge logic (handler
implementations) is in ``rpc_mode.py``; nine commands are wired to the
existing :class:`AgentHarness` surface, the other 20 return
:class:`RpcErrorResponse` per Pi parity (see ``rpc_mode.DEFERRED_COMMANDS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# === Helpers — snake_case ↔ camelCase ============================================

def _camel(snake: str) -> str:
    """Pi parity: convert ``snake_case`` field names to ``camelCase`` for
    the wire. ``"thinking_level" → "thinkingLevel"``.
    """

    parts = snake.split("_")
    if len(parts) == 1:
        return snake
    return parts[0] + "".join(p.title() for p in parts[1:])


def _snake(camel: str) -> str:
    """Pi parity: convert ``camelCase`` to ``snake_case`` on the parse path."""

    out: list[str] = []
    for ch in camel:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


# === RpcCommand variants (29, Pi rpc-types.ts:19-69) =============================
#
# Each variant carries an optional ``id`` echoed back in the response for
# client-side correlation. ``type`` is the discriminator literal.


@dataclass(frozen=True)
class RpcCommandPrompt:
    """Pi parity: ``rpc-types.ts:22``."""

    message: str
    images: list[dict[str, Any]] | None = None
    streaming_behavior: Literal["steer", "followUp"] | None = None
    id: str | None = None
    type: Literal["prompt"] = "prompt"


@dataclass(frozen=True)
class RpcCommandSteer:
    """Pi parity: ``rpc-types.ts:23``."""

    message: str
    images: list[dict[str, Any]] | None = None
    id: str | None = None
    type: Literal["steer"] = "steer"


@dataclass(frozen=True)
class RpcCommandFollowUp:
    """Pi parity: ``rpc-types.ts:24``."""

    message: str
    images: list[dict[str, Any]] | None = None
    id: str | None = None
    type: Literal["follow_up"] = "follow_up"


@dataclass(frozen=True)
class RpcCommandAbort:
    """Pi parity: ``rpc-types.ts:25``."""

    id: str | None = None
    type: Literal["abort"] = "abort"


@dataclass(frozen=True)
class RpcCommandNewSession:
    """Pi parity: ``rpc-types.ts:26``."""

    parent_session: str | None = None
    id: str | None = None
    type: Literal["new_session"] = "new_session"


@dataclass(frozen=True)
class RpcCommandGetState:
    """Pi parity: ``rpc-types.ts:29``."""

    id: str | None = None
    type: Literal["get_state"] = "get_state"


@dataclass(frozen=True)
class RpcCommandSetModel:
    """Pi parity: ``rpc-types.ts:32``."""

    provider: str
    model_id: str
    id: str | None = None
    type: Literal["set_model"] = "set_model"


@dataclass(frozen=True)
class RpcCommandCycleModel:
    """Pi parity: ``rpc-types.ts:33``."""

    id: str | None = None
    type: Literal["cycle_model"] = "cycle_model"


@dataclass(frozen=True)
class RpcCommandGetAvailableModels:
    """Pi parity: ``rpc-types.ts:34``."""

    id: str | None = None
    type: Literal["get_available_models"] = "get_available_models"


@dataclass(frozen=True)
class RpcCommandSetThinkingLevel:
    """Pi parity: ``rpc-types.ts:37``."""

    level: str
    id: str | None = None
    type: Literal["set_thinking_level"] = "set_thinking_level"


@dataclass(frozen=True)
class RpcCommandCycleThinkingLevel:
    """Pi parity: ``rpc-types.ts:38``."""

    id: str | None = None
    type: Literal["cycle_thinking_level"] = "cycle_thinking_level"


@dataclass(frozen=True)
class RpcCommandSetSteeringMode:
    """Pi parity: ``rpc-types.ts:41``."""

    mode: Literal["all", "one-at-a-time"]
    id: str | None = None
    type: Literal["set_steering_mode"] = "set_steering_mode"


@dataclass(frozen=True)
class RpcCommandSetFollowUpMode:
    """Pi parity: ``rpc-types.ts:42``."""

    mode: Literal["all", "one-at-a-time"]
    id: str | None = None
    type: Literal["set_follow_up_mode"] = "set_follow_up_mode"


@dataclass(frozen=True)
class RpcCommandCompact:
    """Pi parity: ``rpc-types.ts:45``."""

    custom_instructions: str | None = None
    id: str | None = None
    type: Literal["compact"] = "compact"


@dataclass(frozen=True)
class RpcCommandSetAutoCompaction:
    """Pi parity: ``rpc-types.ts:46``."""

    enabled: bool = True
    id: str | None = None
    type: Literal["set_auto_compaction"] = "set_auto_compaction"


@dataclass(frozen=True)
class RpcCommandSetAutoRetry:
    """Pi parity: ``rpc-types.ts:49``."""

    enabled: bool = True
    id: str | None = None
    type: Literal["set_auto_retry"] = "set_auto_retry"


@dataclass(frozen=True)
class RpcCommandAbortRetry:
    """Pi parity: ``rpc-types.ts:50``."""

    id: str | None = None
    type: Literal["abort_retry"] = "abort_retry"


@dataclass(frozen=True)
class RpcCommandBash:
    """Pi parity: ``rpc-types.ts:53``."""

    command: str
    id: str | None = None
    type: Literal["bash"] = "bash"


@dataclass(frozen=True)
class RpcCommandAbortBash:
    """Pi parity: ``rpc-types.ts:54``."""

    id: str | None = None
    type: Literal["abort_bash"] = "abort_bash"


@dataclass(frozen=True)
class RpcCommandGetSessionStats:
    """Pi parity: ``rpc-types.ts:57``."""

    id: str | None = None
    type: Literal["get_session_stats"] = "get_session_stats"


@dataclass(frozen=True)
class RpcCommandExportHtml:
    """Pi parity: ``rpc-types.ts:58``."""

    output_path: str | None = None
    id: str | None = None
    type: Literal["export_html"] = "export_html"


@dataclass(frozen=True)
class RpcCommandSwitchSession:
    """Pi parity: ``rpc-types.ts:59``."""

    session_path: str = ""
    id: str | None = None
    type: Literal["switch_session"] = "switch_session"


@dataclass(frozen=True)
class RpcCommandFork:
    """Pi parity: ``rpc-types.ts:60``."""

    entry_id: str = ""
    id: str | None = None
    type: Literal["fork"] = "fork"


@dataclass(frozen=True)
class RpcCommandClone:
    """Pi parity: ``rpc-types.ts:61``."""

    id: str | None = None
    type: Literal["clone"] = "clone"


@dataclass(frozen=True)
class RpcCommandGetForkMessages:
    """Pi parity: ``rpc-types.ts:62``."""

    id: str | None = None
    type: Literal["get_fork_messages"] = "get_fork_messages"


@dataclass(frozen=True)
class RpcCommandGetLastAssistantText:
    """Pi parity: ``rpc-types.ts:63``."""

    id: str | None = None
    type: Literal["get_last_assistant_text"] = "get_last_assistant_text"


@dataclass(frozen=True)
class RpcCommandSetSessionName:
    """Pi parity: ``rpc-types.ts:64``."""

    name: str = ""
    id: str | None = None
    type: Literal["set_session_name"] = "set_session_name"


@dataclass(frozen=True)
class RpcCommandGetMessages:
    """Pi parity: ``rpc-types.ts:67``."""

    id: str | None = None
    type: Literal["get_messages"] = "get_messages"


@dataclass(frozen=True)
class RpcCommandGetCommands:
    """Pi parity: ``rpc-types.ts:70``."""

    id: str | None = None
    type: Literal["get_commands"] = "get_commands"


RpcCommand = (
    RpcCommandPrompt
    | RpcCommandSteer
    | RpcCommandFollowUp
    | RpcCommandAbort
    | RpcCommandNewSession
    | RpcCommandGetState
    | RpcCommandSetModel
    | RpcCommandCycleModel
    | RpcCommandGetAvailableModels
    | RpcCommandSetThinkingLevel
    | RpcCommandCycleThinkingLevel
    | RpcCommandSetSteeringMode
    | RpcCommandSetFollowUpMode
    | RpcCommandCompact
    | RpcCommandSetAutoCompaction
    | RpcCommandSetAutoRetry
    | RpcCommandAbortRetry
    | RpcCommandBash
    | RpcCommandAbortBash
    | RpcCommandGetSessionStats
    | RpcCommandExportHtml
    | RpcCommandSwitchSession
    | RpcCommandFork
    | RpcCommandClone
    | RpcCommandGetForkMessages
    | RpcCommandGetLastAssistantText
    | RpcCommandSetSessionName
    | RpcCommandGetMessages
    | RpcCommandGetCommands
)


# Discriminator → dataclass mapping. ``parse_rpc_command`` reads payload
# ``type`` and dispatches; ``RPC_COMMAND_TYPES`` is the closure-pin allowlist.
_RPC_COMMAND_REGISTRY: dict[str, type] = {
    "prompt": RpcCommandPrompt,
    "steer": RpcCommandSteer,
    "follow_up": RpcCommandFollowUp,
    "abort": RpcCommandAbort,
    "new_session": RpcCommandNewSession,
    "get_state": RpcCommandGetState,
    "set_model": RpcCommandSetModel,
    "cycle_model": RpcCommandCycleModel,
    "get_available_models": RpcCommandGetAvailableModels,
    "set_thinking_level": RpcCommandSetThinkingLevel,
    "cycle_thinking_level": RpcCommandCycleThinkingLevel,
    "set_steering_mode": RpcCommandSetSteeringMode,
    "set_follow_up_mode": RpcCommandSetFollowUpMode,
    "compact": RpcCommandCompact,
    "set_auto_compaction": RpcCommandSetAutoCompaction,
    "set_auto_retry": RpcCommandSetAutoRetry,
    "abort_retry": RpcCommandAbortRetry,
    "bash": RpcCommandBash,
    "abort_bash": RpcCommandAbortBash,
    "get_session_stats": RpcCommandGetSessionStats,
    "export_html": RpcCommandExportHtml,
    "switch_session": RpcCommandSwitchSession,
    "fork": RpcCommandFork,
    "clone": RpcCommandClone,
    "get_fork_messages": RpcCommandGetForkMessages,
    "get_last_assistant_text": RpcCommandGetLastAssistantText,
    "set_session_name": RpcCommandSetSessionName,
    "get_messages": RpcCommandGetMessages,
    "get_commands": RpcCommandGetCommands,
}


# Per-command field name remapping (snake_case Python ↔ camelCase Pi wire).
# Only commands with multi-word fields need an explicit entry; single-word
# fields (``message``, ``mode``, ``enabled``, ``command``, ``name``) are
# identical on both sides.
_COMMAND_FIELD_REMAP: dict[str, dict[str, str]] = {
    # snake → camel for serialize; camel → snake on parse via _snake().
    "set_model": {"model_id": "modelId"},
    "new_session": {"parent_session": "parentSession"},
    "compact": {"custom_instructions": "customInstructions"},
    "export_html": {"output_path": "outputPath"},
    "switch_session": {"session_path": "sessionPath"},
    "fork": {"entry_id": "entryId"},
    "prompt": {"streaming_behavior": "streamingBehavior"},
}


def parse_rpc_command(payload: dict[str, Any]) -> RpcCommand:
    """Pi parity: discriminator-based parse of an incoming JSONL record.

    Reads ``payload["type"]``, maps to the matching :class:`RpcCommand`
    dataclass, remaps camelCase fields to snake_case, and constructs the
    variant. Raises :class:`ValueError` for unknown types and
    :class:`TypeError` for missing required fields.
    """

    cmd_type = payload.get("type")
    if not isinstance(cmd_type, str):
        raise ValueError(f"RpcCommand payload missing 'type' field: {payload!r}")
    cls = _RPC_COMMAND_REGISTRY.get(cmd_type)
    if cls is None:
        raise ValueError(f"Unknown RpcCommand type: {cmd_type!r}")

    remap = _COMMAND_FIELD_REMAP.get(cmd_type, {})
    # Build a snake_case-keyed kwargs dict. Pi sends camelCase on the wire
    # for multi-word fields; we accept both for forgiving parse semantics.
    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "type":
            continue
        # Try inverse remap first (camel-on-wire → snake-on-Python).
        inverse = {v: k for k, v in remap.items()}
        if key in inverse:
            kwargs[inverse[key]] = value
        else:
            # Already snake_case, or a single-word field.
            kwargs[key] = value
    return cls(**kwargs)


def command_to_json(cmd: RpcCommand) -> dict[str, Any]:
    """Serialize an :class:`RpcCommand` to a wire-shaped JSON dict.

    Drops ``None`` values, remaps multi-word snake_case fields to
    camelCase per :data:`_COMMAND_FIELD_REMAP`, and preserves the ``type``
    discriminator literal.
    """

    cmd_type: str = cmd.type
    remap: dict[str, str] = _COMMAND_FIELD_REMAP.get(cmd_type) or {}
    out: dict[str, Any] = {}
    # Always emit ``type`` first for human-readable wire output.
    out["type"] = cmd_type
    for f in cmd.__dataclass_fields__.values():
        name = f.name
        if name == "type":
            continue
        value = getattr(cmd, name)
        if value is None:
            continue
        # W4 m6 — pyright reportArgumentType: bind the lookup to a
        # ``str`` local before subscripting to satisfy the narrowing
        # check on ``dict[str, Any]``.
        wire_key: str = remap.get(name, name)
        out[wire_key] = value
    return out


# === RpcResponse (Pi rpc-types.ts:113-208) =======================================
#
# Pi has a 24-variant success union + 1 uniform error envelope. Aelix
# collapses success into a single dataclass with ``data: Any`` because the
# per-command data shape is enforced at handler-construction time, not at
# the type level. The closure pin asserts the wire shape matches Pi.


@dataclass(frozen=True)
class RpcSuccessResponse:
    """Pi parity: success envelope, ``rpc-types.ts:113-202``.

    Wire shape: ``{id?, type: "response", command, success: true, data?}``.
    ``data`` is omitted (not emitted as ``null``) when the command has no
    payload (Pi: ``data === undefined ? undefined : data``).
    """

    command: str
    data: Any = None
    id: str | None = None
    success: Literal[True] = True
    type: Literal["response"] = "response"

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "response", "command": self.command, "success": True}
        if self.id is not None:
            out["id"] = self.id
        if self.data is not None:
            out["data"] = self.data
        return out


@dataclass(frozen=True)
class RpcErrorResponse:
    """Pi parity: error envelope, ``rpc-types.ts:204-205``.

    Uniform across every command — any command can fail. ``id`` echo is
    critical for client-side correlation. ``success: false`` is the
    discriminator the client uses to route to ``getData`` vs. throw.
    """

    command: str
    error: str
    id: str | None = None
    success: Literal[False] = False
    type: Literal["response"] = "response"

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": "response",
            "command": self.command,
            "success": False,
            "error": self.error,
        }
        if self.id is not None:
            out["id"] = self.id
        return out


RpcResponse = RpcSuccessResponse | RpcErrorResponse


def parse_rpc_response(payload: dict[str, Any]) -> RpcResponse:
    """Parse an incoming response envelope.

    Used by :class:`RpcClient` to dispatch responses to pending requests
    by ``id``. Raises :class:`ValueError` on malformed input.
    """

    if payload.get("type") != "response":
        raise ValueError(f"Expected type='response', got {payload!r}")
    command = payload.get("command")
    if not isinstance(command, str):
        raise ValueError(f"Response missing 'command' field: {payload!r}")
    success = payload.get("success")
    request_id = payload.get("id")
    if success is True:
        return RpcSuccessResponse(
            command=command,
            data=payload.get("data"),
            id=request_id,
        )
    if success is False:
        error_msg = payload.get("error", "")
        return RpcErrorResponse(
            command=command,
            error=str(error_msg),
            id=request_id,
        )
    raise ValueError(f"Response 'success' field is not bool: {payload!r}")


# === RpcSessionState (Pi rpc-types.ts:90-103) ====================================


@dataclass(frozen=True)
class RpcSessionState:
    """Pi parity: ``rpc-types.ts:90-103``.

    Sprint 6h₂ W6 (P-264 BLOCKING): adds ``auto_retry_enabled`` to the
    wire surface symmetric with ``auto_compaction_enabled`` so the
    Pi ``RpcSessionState`` shape stays a strict superset of the harness
    auto-mode state. Pi field names are camelCase on the wire; Aelix
    uses snake_case in Python and remaps on serialize via
    :meth:`to_json`.
    """

    session_id: str = ""
    thinking_level: str = "off"
    is_streaming: bool = False
    is_compacting: bool = False
    steering_mode: Literal["all", "one-at-a-time"] = "all"
    follow_up_mode: Literal["all", "one-at-a-time"] = "all"
    message_count: int = 0
    pending_message_count: int = 0
    auto_compaction_enabled: bool = True
    # Sprint 6h₂ (P-264): Pi default mirrors ``session.autoRetryEnabled``
    # (``agent-session.ts:2536-2538``) — toggled via the new RPC
    # ``set_auto_retry`` handler.
    auto_retry_enabled: bool = True
    model: dict[str, Any] | None = None
    session_file: str | None = None
    session_name: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Pi-shape camelCase serialization for the wire."""

        return {
            "model": self.model,
            "thinkingLevel": self.thinking_level,
            "isStreaming": self.is_streaming,
            "isCompacting": self.is_compacting,
            "steeringMode": self.steering_mode,
            "followUpMode": self.follow_up_mode,
            "sessionFile": self.session_file,
            "sessionId": self.session_id,
            "sessionName": self.session_name,
            "autoCompactionEnabled": self.auto_compaction_enabled,
            "autoRetryEnabled": self.auto_retry_enabled,
            "messageCount": self.message_count,
            "pendingMessageCount": self.pending_message_count,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RpcSessionState:
        """Parse a Pi-shape camelCase dict into a snake_case :class:`RpcSessionState`."""

        return cls(
            model=payload.get("model"),
            thinking_level=payload.get("thinkingLevel", "off"),
            is_streaming=bool(payload.get("isStreaming", False)),
            is_compacting=bool(payload.get("isCompacting", False)),
            steering_mode=payload.get("steeringMode", "all"),
            follow_up_mode=payload.get("followUpMode", "all"),
            session_file=payload.get("sessionFile"),
            session_id=payload.get("sessionId", ""),
            session_name=payload.get("sessionName"),
            auto_compaction_enabled=bool(payload.get("autoCompactionEnabled", True)),
            auto_retry_enabled=bool(payload.get("autoRetryEnabled", True)),
            message_count=int(payload.get("messageCount", 0)),
            pending_message_count=int(payload.get("pendingMessageCount", 0)),
        )


# === RpcSlashCommand (Pi rpc-types.ts:76-86) =====================================


@dataclass(frozen=True)
class RpcSlashCommand:
    """Pi parity: ``rpc-types.ts:76-86``."""

    name: str
    source: Literal["extension", "prompt", "skill"]
    description: str | None = None
    source_info: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "source": self.source}
        if self.description is not None:
            out["description"] = self.description
        if self.source_info is not None:
            out["sourceInfo"] = self.source_info
        return out


# === RpcExtensionUIRequest (9 methods, Pi rpc-types.ts:213-245) =================
#
# TYPES ONLY — Sprint 6d ships the wire shape. The bridge (sending these
# from extensions, parsing responses) is Sprint 6f per ADR-0058.


@dataclass(frozen=True)
class RpcExtensionUIRequestSelect:
    """Pi parity: ``rpc-types.ts:215``."""

    id: str
    title: str
    options: list[str]
    timeout: int | None = None
    method: Literal["select"] = "select"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestConfirm:
    """Pi parity: ``rpc-types.ts:216``."""

    id: str
    title: str
    message: str
    timeout: int | None = None
    method: Literal["confirm"] = "confirm"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestInput:
    """Pi parity: ``rpc-types.ts:217-223``."""

    id: str
    title: str
    placeholder: str | None = None
    timeout: int | None = None
    method: Literal["input"] = "input"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestEditor:
    """Pi parity: ``rpc-types.ts:224``."""

    id: str
    title: str
    prefill: str | None = None
    method: Literal["editor"] = "editor"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestNotify:
    """Pi parity: ``rpc-types.ts:225-231``."""

    id: str
    message: str
    notify_type: Literal["info", "warning", "error"] | None = None
    method: Literal["notify"] = "notify"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestSetStatus:
    """Pi parity: ``rpc-types.ts:232-238``."""

    id: str
    status_key: str
    status_text: str | None
    method: Literal["setStatus"] = "setStatus"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestSetWidget:
    """Pi parity: ``rpc-types.ts:239-245``."""

    id: str
    widget_key: str
    widget_lines: list[str] | None
    widget_placement: Literal["aboveEditor", "belowEditor"] | None = None
    method: Literal["setWidget"] = "setWidget"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestSetTitle:
    """Pi parity: ``rpc-types.ts:246``."""

    id: str
    title: str
    method: Literal["setTitle"] = "setTitle"
    type: Literal["extension_ui_request"] = "extension_ui_request"


@dataclass(frozen=True)
class RpcExtensionUIRequestSetEditorText:
    """Pi parity: ``rpc-types.ts:247``."""

    id: str
    text: str
    method: Literal["set_editor_text"] = "set_editor_text"
    type: Literal["extension_ui_request"] = "extension_ui_request"


RpcExtensionUIRequest = (
    RpcExtensionUIRequestSelect
    | RpcExtensionUIRequestConfirm
    | RpcExtensionUIRequestInput
    | RpcExtensionUIRequestEditor
    | RpcExtensionUIRequestNotify
    | RpcExtensionUIRequestSetStatus
    | RpcExtensionUIRequestSetWidget
    | RpcExtensionUIRequestSetTitle
    | RpcExtensionUIRequestSetEditorText
)


# Wire-side 9-method allowlist (Pi rpc-types.ts:215-247).
RPC_EXTENSION_UI_REQUEST_METHODS: frozenset[str] = frozenset(
    {
        "select",
        "confirm",
        "input",
        "editor",
        "notify",
        "setStatus",
        "setWidget",
        "setTitle",
        "set_editor_text",
    }
)


# === RpcExtensionUIResponse (3 shapes, Pi rpc-types.ts:253-256) =================


@dataclass(frozen=True)
class RpcExtensionUIResponseValue:
    """Pi parity: ``rpc-types.ts:254`` — for select/input/editor methods."""

    id: str
    value: str
    type: Literal["extension_ui_response"] = "extension_ui_response"


@dataclass(frozen=True)
class RpcExtensionUIResponseConfirmed:
    """Pi parity: ``rpc-types.ts:255`` — for confirm method."""

    id: str
    confirmed: bool
    type: Literal["extension_ui_response"] = "extension_ui_response"


@dataclass(frozen=True)
class RpcExtensionUIResponseCancelled:
    """Pi parity: ``rpc-types.ts:256`` — for cancelled user interaction."""

    id: str
    cancelled: Literal[True] = True
    type: Literal["extension_ui_response"] = "extension_ui_response"


RpcExtensionUIResponse = (
    RpcExtensionUIResponseValue
    | RpcExtensionUIResponseConfirmed
    | RpcExtensionUIResponseCancelled
)


# === Closure-pin allowlists (Pi parity wire-shape constants) ====================

RPC_COMMAND_TYPES: frozenset[str] = frozenset(_RPC_COMMAND_REGISTRY.keys())
"""All 29 Pi RpcCommand type discriminators. Closure pin in
``tests/pi_parity/test_phase_4_4_strict_superset.py`` asserts the
count matches Pi exactly."""


__all__ = [
    "RPC_COMMAND_TYPES",
    "RPC_EXTENSION_UI_REQUEST_METHODS",
    "RpcCommand",
    "RpcCommandAbort",
    "RpcCommandAbortBash",
    "RpcCommandAbortRetry",
    "RpcCommandBash",
    "RpcCommandClone",
    "RpcCommandCompact",
    "RpcCommandCycleModel",
    "RpcCommandCycleThinkingLevel",
    "RpcCommandExportHtml",
    "RpcCommandFollowUp",
    "RpcCommandFork",
    "RpcCommandGetAvailableModels",
    "RpcCommandGetCommands",
    "RpcCommandGetForkMessages",
    "RpcCommandGetLastAssistantText",
    "RpcCommandGetMessages",
    "RpcCommandGetSessionStats",
    "RpcCommandGetState",
    "RpcCommandNewSession",
    "RpcCommandPrompt",
    "RpcCommandSetAutoCompaction",
    "RpcCommandSetAutoRetry",
    "RpcCommandSetFollowUpMode",
    "RpcCommandSetModel",
    "RpcCommandSetSessionName",
    "RpcCommandSetSteeringMode",
    "RpcCommandSetThinkingLevel",
    "RpcCommandSteer",
    "RpcCommandSwitchSession",
    "RpcErrorResponse",
    "RpcExtensionUIRequest",
    "RpcExtensionUIRequestConfirm",
    "RpcExtensionUIRequestEditor",
    "RpcExtensionUIRequestInput",
    "RpcExtensionUIRequestNotify",
    "RpcExtensionUIRequestSelect",
    "RpcExtensionUIRequestSetEditorText",
    "RpcExtensionUIRequestSetStatus",
    "RpcExtensionUIRequestSetTitle",
    "RpcExtensionUIRequestSetWidget",
    "RpcExtensionUIResponse",
    "RpcExtensionUIResponseCancelled",
    "RpcExtensionUIResponseConfirmed",
    "RpcExtensionUIResponseValue",
    "RpcResponse",
    "RpcSessionState",
    "RpcSlashCommand",
    "RpcSuccessResponse",
    "command_to_json",
    "parse_rpc_command",
    "parse_rpc_response",
]
