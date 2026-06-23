"""Tool and ToolResult types.

Follows the pi-ai tool shape: ``name``, ``description``, JSON-Schema-shaped
``parameters``, and an async ``execute`` callable.

:func:`validate_tool_arguments` (issue #13) is the real JSON-Schema validation
gate the agent loop runs before every tool dispatch. Pi parity:
``validateToolArguments`` (``packages/ai/src/utils/validation.ts``) — a
**coerce-then-validate** pass that (1) leniently coerces obvious string→scalar
mistakes weak models make (``"5"`` → ``5``, ``"true"`` → ``True``), (2)
preserves unknown keys (additive, never strips), and (3) validates required
fields + types, raising :class:`ToolArgumentValidationError` with a structured,
model-readable message when the args are malformed. The loop catches that
exception and returns it as an ``is_error`` tool result so the model
re-corrects on its next turn (it is NEVER an uncaught crash).
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema
import jsonschema.validators
from jsonschema.exceptions import ValidationError

from aelix_ai.messages import ImageContent, TextContent

# Tool result content reuses the same text/image content blocks as messages.
ToolContent = list[TextContent | ImageContent]


# Sprint 3d (P-9): partial-result callback type. Pi parity ——
# ``AgentToolUpdateCallback`` in ``packages/agent/src/types.ts:357-358`` is a
# sync ``(partialResult) => void``; Aelix tolerates both sync and async return
# types so handlers and tool authors can fan out without an awkward
# ``inspect.iscoroutine`` dance. The runtime (`_execute_and_finalize`) drains
# every scheduled emission before returning, mirroring Pi's
# ``await Promise.all(updateEvents)`` at ``agent-loop.ts:630``.
#
# Note: Pi ``AgentToolUpdateCallback`` is strictly sync (``void``); Aelix
# accepts ``Awaitable[None]`` for type-compat only — the runtime does NOT
# await user-returned awaitables (matches Pi semantics). Only the
# runtime-scheduled internal emission task is drained.
ToolPartialCallback = Callable[["ToolResult"], Awaitable[None] | None]


@dataclass(frozen=True)
class ToolExecutionContext:
    """Minimal context passed to a tool's ``execute`` callable.

    Phase 1.1 keeps this lightweight. Harness layers will extend it with
    session manager, UI handles, audit metadata, and cancellation in later
    phases.

    Sprint 3d (P-9) adds the optional ``on_partial`` callback. Pi parity:
    ``AgentToolUpdateCallback`` (``types.ts:357-358``) — fire-and-forget; the
    runtime guarantees every partial-emit fan-out is drained before the tool's
    final result is returned to the loop (mirrors Pi
    ``await Promise.all(updateEvents)`` at ``agent-loop.ts:630``).

    ``on_partial`` is ``None`` when no harness is registered (bare-loop
    callers); tools MUST tolerate the ``None`` value before invoking it.

    ``model`` is the resolved :class:`~aelix_ai.streaming.Model` for the
    current turn (Pi parity: ``ctx.model`` on the tool execute signature,
    ``agent-loop.ts``). It is ``None`` for bare-loop callers that do not
    thread a model through the loop; tools that read it (e.g. the read
    tool's non-vision image note) MUST tolerate ``None``. Typed ``Any`` to
    avoid a hard import coupling onto the streaming module.
    """

    tool_call_id: str = ""
    signal: Any | None = None  # AbortSignal placeholder for Phase 2
    # Sprint 3d (P-9). See ``ToolPartialCallback`` for the Pi-equivalent
    # contract.
    on_partial: ToolPartialCallback | None = None
    # P0 #3 HEAVY (ADR-0139). Pi parity ``ctx.model`` — the model executing
    # the current turn. ``None`` for bare-loop callers (note simply absent).
    model: Any | None = None


@dataclass(frozen=True)
class ToolResult:
    """Result returned by a tool ``execute`` callable.

    ``terminate`` is a hint that the loop may stop after the current batch.
    The loop only honors it when every finalized tool result in the batch
    sets it (matches pi-agent-core semantics).
    """

    content: ToolContent = field(default_factory=list)
    details: Any | None = None
    is_error: bool = False
    terminate: bool = False


# An async callable that performs the tool's work.
ToolExecute = Callable[
    [dict[str, Any], ToolExecutionContext],
    Awaitable[ToolResult],
]


@dataclass(frozen=True)
class Tool:
    """Provider-agnostic tool definition.

    ``parameters`` is a JSON-Schema-shaped ``dict`` so any provider adapter
    can emit the right wire format. ``execute`` is optional at the type level
    so Tool definitions can be registered before their handler is bound — the
    agent loop will refuse to call a tool whose ``execute`` is ``None``.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    execute: ToolExecute | None = None


class ToolArgumentValidationError(Exception):
    """Raised by :func:`validate_tool_arguments` when args fail their schema.

    The message is the structured, model-readable text (Pi parity
    ``validation.ts`` throw shape). The agent loop catches this specific type
    at the tool-dispatch site and converts it into an ``is_error`` tool result
    so the model re-grounds on its next turn — it must NEVER escape uncaught.
    """


# Pi truncates nothing here, but a coding agent's write/edit args can be huge;
# echoing the full payload back in a validation error would waste the model's
# context (and weak local models choke on it). Cap the echoed args at the same
# 2000-char budget the compaction serializer uses for tool results.
_VALIDATION_ARGS_ECHO_MAX_CHARS = 2000


def _coerce_scalar(value: Any, schema_type: str | None) -> Any:
    """Pi parity ``coercePrimitiveByType`` (``validation.ts``) — coerce a value
    of ANY type toward the schema's declared scalar type.

    Mirrors pi's full coercion table so loosely-typed weak-model args still
    dispatch (the headline goal of issue #13):

    - ``number`` / ``integer``: ``null`` → ``0``; ``bool`` → ``1``/``0``;
      numeric string → number (int when integral; a fractional string stays a
      string for "integer" so the validator rejects it).
    - ``boolean``: ``null`` → ``False``; numeric ``1``/``0`` → ``True``/``False``;
      ``"true"``/``"false"`` → bool. Strings are matched CASE-INSENSITIVELY —
      an INTENTIONAL aelix divergence from pi (pi is case-sensitive), because
      weak local models emit ``"True"``/``"False"``.
    - ``string``: ``null`` → ``""``; ``bool`` → ``"true"``/``"false"``; number
      → its string form (no trailing ``.0``).

    On any ambiguity the ORIGINAL value is returned unchanged so the validator
    surfaces the real type error rather than guessing.
    """

    if schema_type in ("number", "integer"):
        if value is None:
            return 0
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return value
            try:
                parsed = float(stripped)
            except ValueError:
                return value
            if not math.isfinite(parsed):
                return value
            # Prefer int when integral so downstream tools that index/slice get
            # a real int; jsonschema "number" accepts both int and float.
            if parsed == int(parsed):
                return int(parsed)
            # A fractional value can't satisfy "integer" — leave it for the
            # validator to reject with a clear type error.
            return value if schema_type == "integer" else parsed
        return value
    if schema_type == "boolean":
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        # bool is a subclass of int — the isinstance(bool) above already handled
        # real booleans, so this only sees genuine ints/floats.
        if isinstance(value, (int, float)):
            if value == 1:
                return True
            if value == 0:
                return False
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low == "true":
                return True
            if low == "false":
                return False
        return value
    if schema_type == "string":
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            # pi uses JS String(n): an integral float renders without ".0".
            if isinstance(value, float) and value == int(value):
                return str(int(value))
            return str(value)
        return value
    return value


def _coerce(value: Any, schema: Any) -> Any:
    """Recursively coerce ``value`` toward ``schema`` (Pi ``coerceWithJsonSchema``).

    Walks object ``properties`` and array ``items`` so nested shapes (e.g. the
    edit tool's ``edits`` array of objects) are coerced too. Unknown keys are
    left in place (additive — never stripped). A union ``type`` (list) is left
    untouched (ambiguous to coerce); the validator handles it.
    """

    if not isinstance(schema, dict):
        return value
    schema_type = schema.get("type")

    if schema_type == "object" or "properties" in schema:
        if isinstance(value, dict):
            props = schema.get("properties")
            if isinstance(props, dict):
                for key, subschema in props.items():
                    if key in value:
                        value[key] = _coerce(value[key], subschema)
        return value

    if schema_type == "array" or "items" in schema:
        if isinstance(value, list):
            items = schema.get("items")
            if isinstance(items, dict):
                for i, item in enumerate(value):
                    value[i] = _coerce(item, items)
        return value

    # Scalar leaf: coerce for ANY source type (null/number/bool/string) toward
    # the declared scalar type. A union ``type`` (list) is left untouched.
    if isinstance(schema_type, str):
        return _coerce_scalar(value, schema_type)
    return value


def _format_validation_error(
    tool: Tool,
    original_args: dict[str, Any],
    errors: list[ValidationError],
) -> str:
    """Pi parity error text (``validation.ts``): named, path-qualified, with a
    pretty echo of the ORIGINAL (pre-coercion) args so the model sees exactly
    what it sent."""

    lines = [f'Validation failed for tool "{tool.name}":']
    for err in errors:
        path = getattr(err, "json_path", None) or "$"
        lines.append(f"  - {path}: {err.message}")
    try:
        received = json.dumps(original_args, indent=2, default=str)
    except (TypeError, ValueError):
        received = repr(original_args)
    if len(received) > _VALIDATION_ARGS_ECHO_MAX_CHARS:
        received = received[:_VALIDATION_ARGS_ECHO_MAX_CHARS] + "\n… (truncated)"
    lines.append("")
    lines.append("Received arguments:")
    lines.append(received)
    return "\n".join(lines)


async def validate_tool_arguments(
    tool: Tool, args: dict[str, Any]
) -> dict[str, Any]:
    """Coerce-then-validate the model's tool args against ``tool.parameters``.

    Pi parity ``validateToolArguments``. Returns the (possibly coerced) args on
    success; raises :class:`ToolArgumentValidationError` with a structured,
    model-readable message on failure. Lenient by design (aelix runs weak local
    models that emit loosely-typed args): unknown keys are preserved and obvious
    string→scalar mistakes are coerced before validation.

    Defensive: a missing / empty / non-object / malformed ``parameters`` schema
    (MCP- and extension-registered tools may supply anything) is treated as
    "nothing to enforce" — the args pass through uncoerced rather than crash.
    The function is async to match the call-site contract (validation is sync).
    """

    out = copy.deepcopy(args)
    schema = tool.parameters if isinstance(tool.parameters, dict) else None

    # Nothing usable to enforce → pass through (MCP tools with no/empty schema).
    if not schema:
        return out

    # Best-effort coercion; a glitch on an exotic schema must never block the
    # tool, so fall back to the uncoerced copy.
    try:
        out = _coerce(out, schema)
    except Exception:  # noqa: BLE001 — coercion is best-effort, never fatal
        out = copy.deepcopy(args)

    # Pick the validator draft from the schema's own ``$schema`` (MCP tools may
    # declare 2020-12), defaulting to Draft 7. ``check_schema`` first rejects a
    # malformed schema (e.g. a bogus ``type``): we cannot validate against it,
    # so the args pass through rather than block dispatch — MCP- and
    # extension-registered tools may ship arbitrary schemas.
    try:
        validator_cls = jsonschema.validators.validator_for(
            schema, default=jsonschema.Draft7Validator
        )
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(
            validator.iter_errors(out), key=lambda e: list(e.absolute_path)
        )
    except Exception:  # noqa: BLE001
        # An arbitrary / exotic schema (a bogus ``type``, an unresolvable or
        # recursive ``$ref``, a stack-blowing instance) must NEVER crash the
        # turn — issue #13's contract is "malformed schema → pass through".
        # jsonschema surfaces these as SchemaError, referencing.Unresolvable, or
        # RecursionError (none a common base), so we catch broadly here, exactly
        # as pi's validateToolArguments does. Our own ToolArgumentValidationError
        # is raised BELOW this block, so it is never swallowed.
        return out

    if not errors:
        return out
    raise ToolArgumentValidationError(
        _format_validation_error(tool, args, errors)
    )


__all__ = [
    "Tool",
    "ToolArgumentValidationError",
    "ToolContent",
    "ToolExecute",
    "ToolExecutionContext",
    "ToolPartialCallback",
    "ToolResult",
    "validate_tool_arguments",
]
