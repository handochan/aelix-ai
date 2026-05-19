"""Pi parity: ``packages/coding-agent/src/modes/rpc/rpc-mode.ts`` (492 LOC).

RPC mode entry point + per-command handlers + signal-aware shutdown +
event pipe. Headless operation behind a JSONL stdin/stdout wire.

Protocol (Pi parity):

- Commands arrive as JSONL on stdin; ``parse_rpc_command`` dispatches.
- Responses are emitted as JSONL on stdout (correlated by optional ``id``).
- Session events flow out via :meth:`AgentHarness.subscribe` → stdout
  WITHOUT transformation (Pi ``session.subscribe((event) => output(event))``).
- ``contextlib.redirect_stdout(sys.stderr)`` hijacks any stray ``print()`` so
  the JSONL stream stays uncorrupted (Pi ``takeOverStdout()``).
- SIGTERM / SIGHUP trigger graceful shutdown (Pi
  ``rpc-mode.ts:registerSignalHandlers``).

Sprint 6d ships **9 supported handlers** wired to the existing
:class:`AgentHarness` surface and **20 deferred handlers** as
:class:`RpcErrorResponse` stubs per Pi parity (P-107 + ADR-0058 closure).
The Pi RpcCommand variant count is **29** (W4 M2 / P-121 — the original
spec text said "28" but the fixture's ``rpc_command_types`` list is the
authoritative wire surface).

Sprint 6f W2 (ADR-0065 / P-168 / P-169) drops 3 entries from
:data:`DEFERRED_COMMANDS` and adds them to :data:`SUPPORTED_COMMANDS`:
``set_model`` / ``cycle_model`` / ``get_available_models``. The
counts move to **12 supported / 17 deferred / 29 total**.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.rpc._jsonl import (
    JsonlLineReader,
    serialize_json_line,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommand,
    RpcCommandBash,
    RpcCommandCompact,
    RpcCommandGetMessages,
    RpcCommandGetState,
    RpcCommandNewSession,
    RpcCommandPrompt,
    RpcCommandSetSessionName,
    RpcCommandSetThinkingLevel,
    RpcErrorResponse,
    RpcResponse,
    RpcSessionState,
    RpcSuccessResponse,
    parse_rpc_command,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness

    from aelix_coding_agent.model_registry import ModelRegistry

# Sprint 6f W2 (ADR-0065): import :class:`Model` for the
# ``_model_to_dict`` serializer. Lazy/runtime import to keep the
# import graph cycle-free.
from aelix_ai.streaming import Model

# === Deferred command allowlist (P-107 + ADR-0058) ============================
#
# 20 commands Aelix's current AgentHarness can't satisfy directly. Every
# entry returns :class:`RpcErrorResponse` with the owning ADR string in
# the error message — the wire shape matches Pi's
# ``{success: false, error: "..."}`` envelope. The closure pin asserts
# 9 supported + 20 deferred = 29 total (= Pi RpcCommand variant count).

DEFERRED_COMMANDS: dict[str, str] = {
    "steer": "ADR-0058 — Sprint 6g harness command paths",
    "follow_up": "ADR-0058 — Sprint 6g harness command paths",
    "cycle_thinking_level": "ADR-0058 — Sprint 6g",
    "set_steering_mode": "ADR-0058 — Sprint 6g",
    "set_follow_up_mode": "ADR-0058 — Sprint 6g",
    "set_auto_compaction": "ADR-0058 — Sprint 6g",
    "set_auto_retry": "ADR-0058 — Sprint 6g",
    "abort_retry": "ADR-0058 — Sprint 6g",
    "abort_bash": "ADR-0058 — Sprint 6g bash cancellation token",
    "get_session_stats": "ADR-0058 — Sprint 6g session inspection",
    "export_html": "ADR-0058 — Sprint 6g session inspection",
    "switch_session": "ADR-0058 — Sprint 6g session tree navigation",
    "fork": "ADR-0058 — Sprint 6g session tree navigation",
    "clone": "ADR-0058 — Sprint 6g session tree navigation",
    "get_fork_messages": "ADR-0058 — Sprint 6g session tree navigation",
    "get_last_assistant_text": "ADR-0058 — Sprint 6g session tree navigation",
    "get_commands": "ADR-0058 — Sprint 6g extension/skill/template aggregation",
}


# Supported command discriminator → handler name mapping. Sprint 6f W2
# (ADR-0065) wires set_model / cycle_model / get_available_models →
# count moves from 9 to 12. The closure pin
# (``tests/pi_parity/test_phase_4_4_strict_superset.py``) asserts the
# union of ``SUPPORTED_COMMANDS`` and ``DEFERRED_COMMANDS`` equals the
# 29-element Pi ``RpcCommand`` discriminator set (W4 M2 / P-121).
SUPPORTED_COMMANDS: frozenset[str] = frozenset(
    {
        "prompt",
        "abort",
        "new_session",
        "get_state",
        "get_messages",
        "compact",
        "bash",
        "set_thinking_level",
        "set_session_name",
        # Sprint 6f W2 (ADR-0065 / P-168 / P-169).
        "set_model",
        "cycle_model",
        "get_available_models",
    }
)


# === Helpers ===================================================================


def _dataclass_to_dict(value: Any) -> Any:
    """Convert dataclass-tree values to plain dicts for JSON serialization.

    Pi emits ``AgentSessionEvent`` objects directly (TS plain objects); the
    Aelix equivalent is a dataclass tree. ``dataclasses.asdict`` produces
    the Pi-shape ``{type, ...}`` dict the wire expects.
    """

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Serialize a harness session event to a Pi-shape dict for stdout.

    Pi parity: ``rpc-mode.ts:86-87`` — events flow out without
    transformation (``session.subscribe((event) => output(event))``).
    """

    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        return dataclasses.asdict(event)
    if isinstance(event, dict):
        return event
    # Fallback for non-dataclass listeners — rely on JSON-serializability.
    return {"event": repr(event)}


# === Supported handlers (Pi parity per P-107) =================================


async def _handle_prompt(
    harness: AgentHarness,
    cmd: RpcCommandPrompt,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:237-260`` (prompt handler).

    Pi fires the prompt asynchronously and emits the response only after
    ``preflightResult(true)``. Aelix has no preflight callback yet — we
    schedule the prompt as a fire-and-forget task and acknowledge
    immediately (Pi's behavior when preflight succeeds synchronously).
    Errors during the run are emitted later via the event stream.
    """

    async def _run() -> None:
        # Errors are observable via the AgentEvent stream and stderr; the
        # response was already emitted (Pi parity: ``rpc-mode.ts:237-260``).
        # W4 m2 / P-119: do NOT silently suppress — log the exception to
        # stderr so the operator sees failures and ``wait_for_idle``
        # callers don't hang on a dropped error.
        try:
            await harness.prompt(cmd.message, source="rpc")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[rpc] prompt task failed: {exc!r}",
                file=sys.stderr,
                flush=True,
            )
            # NOTE: Pi parity (`rpc-mode.ts:379-401`) also emits a synthetic
            # terminal event so the client's `wait_for_idle` listener
            # unblocks. The Aelix harness does not yet expose a public
            # event-emit method to feed an `agent_end` event from outside;
            # Sprint 6f wires that bridge per ADR-0058 carry-forward.

    task = asyncio.create_task(_run())
    # Pin the task on the harness so disposal awaits completion. W4 m3:
    # `AgentHarness` always defines `_pending_tasks` (constructor seeds
    # the set at line ~590); the prior `hasattr` guard was dead. The
    # private-attr read is intentional — Sprint 6f gates a public
    # `track_task()` helper per ADR-0058 carry-forward.
    harness._pending_tasks.add(task)
    task.add_done_callback(harness._pending_tasks.discard)
    return RpcSuccessResponse(id=cmd.id, command="prompt")


async def _handle_abort(
    harness: AgentHarness,
    cmd: Any,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:272-275`` (abort handler)."""

    await harness.abort()
    return RpcSuccessResponse(id=cmd.id, command="abort")


async def _handle_new_session(
    harness: AgentHarness,
    cmd: RpcCommandNewSession,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:277-282`` (new_session handler).

    Aelix harness has no ``runtimeHost.newSession`` equivalent yet — the
    fork/clone Sprint 6f deliverable owns that. Sprint 6d ships the
    minimal cancel-aware shape: harness.abort() clears in-flight queues
    and the response reports ``cancelled: false`` because no extension
    veto path exists yet.

    P-117: ``parent_session`` lineage tracking requires session-tree
    navigation (fork/switch/clone) which is deferred to Sprint 6f per
    ADR-0058. Reject the request explicitly rather than silently dropping
    the field, so callers see a parity-shaped error envelope.
    """

    if cmd.parent_session is not None:
        return RpcErrorResponse(
            id=cmd.id,
            command="new_session",
            error=(
                "parent_session lineage tracking deferred to Sprint 6f "
                "(ADR-0058)"
            ),
        )
    if not harness.is_idle:
        await harness.abort()
        with contextlib.suppress(Exception):
            await harness.wait_for_idle()
    # Pi shape: ``{cancelled: bool}``. Aelix never cancels in this
    # Sprint 6d path; the deferred Sprint 6f work adds the runtimeHost
    # veto callback.
    return RpcSuccessResponse(
        id=cmd.id,
        command="new_session",
        data={"cancelled": False},
    )


async def _handle_get_state(
    harness: AgentHarness,
    cmd: RpcCommandGetState,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:288-302`` (get_state handler).

    Inspects the harness for the 12 :class:`RpcSessionState` fields.

    P-118: this handler reads only public properties on ``AgentHarness``;
    no ``_``-prefixed attribute access. The closure pin asserts the
    invariant so future drift trips mechanically.
    """

    state = harness.state

    # Sprint 6f W6 (P-187, ADR-0066): ``harness.current_model`` is now
    # a thin reader for ``state.model``. ``set_model`` / ``cycle_model``
    # writes ``_state.model`` directly per Pi ``agent-session.ts:1423``
    # so this read sees the latest model without the Sprint 6f W2
    # override-indirection branch.
    active_model = harness.current_model
    model_dict: dict[str, Any] | None = None
    if active_model is not None:
        # Pi parity: emit the Model record verbatim in camelCase
        # (Sprint 6f W2 — was ``_dataclass_to_dict`` snake-case; the
        # ``set_model`` / ``cycle_model`` response shape is camelCase
        # per Pi ``Model<Api>`` wire shape, so ``get_state`` aligns).
        model_dict = _model_to_dict(active_model)

    # P-116: ``is_streaming`` covers every non-idle phase (turn + tool
    # execution + compaction) so RPC clients see the harness as busy
    # whenever it is not idle. ``is_compacting`` is the strict subset.
    is_streaming = harness.phase != "idle"

    rpc_state = RpcSessionState(
        session_id=state.session_id or "",
        thinking_level=state.thinking_level,
        is_streaming=is_streaming,
        is_compacting=harness.phase == "compaction",
        steering_mode=harness.steering_mode,
        follow_up_mode=harness.follow_up_mode,
        message_count=len(state.messages),
        pending_message_count=harness.pending_message_count,
        # W4 m1 — Pi parity carry-forward: hardcoded ``True`` until
        # Sprint 6f wires AutoCompaction state through the harness. See
        # ``DEFERRED_COMMANDS["set_auto_compaction"]`` for the owning
        # ADR-0058 entry.
        auto_compaction_enabled=True,
        model=model_dict,
        session_file=harness.session_file,
        session_name=harness.session_name,
    )
    return RpcSuccessResponse(
        id=cmd.id, command="get_state", data=rpc_state.to_json()
    )


async def _handle_get_messages(
    harness: AgentHarness,
    cmd: RpcCommandGetMessages,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:447-449`` (get_messages handler)."""

    messages = [_dataclass_to_dict(m) for m in harness.messages]
    return RpcSuccessResponse(
        id=cmd.id, command="get_messages", data={"messages": messages}
    )


async def _handle_compact(
    harness: AgentHarness,
    cmd: RpcCommandCompact,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:358-361`` (compact handler)."""

    try:
        result = await harness.compact(cmd.custom_instructions)
    except Exception as exc:  # noqa: BLE001
        return RpcErrorResponse(
            id=cmd.id,
            command="compact",
            error=str(exc),
        )
    return RpcSuccessResponse(
        id=cmd.id,
        command="compact",
        data=_dataclass_to_dict(result),
    )


async def _handle_bash(
    harness: AgentHarness,
    cmd: RpcCommandBash,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:387-389`` (bash handler).

    Sprint 6d invokes the builtin bash tool's underlying operations
    directly (Pi ``session.executeBash`` ultimately funnels into the
    same shell exec). We sidestep the harness's tool-dispatch path
    because bash here is an ad-hoc command from the RPC client, not a
    model-issued tool call.
    """

    from aelix_coding_agent.tools._truncate import truncate_tail
    from aelix_coding_agent.tools.bash import create_local_bash_operations

    ops = create_local_bash_operations()
    cwd = harness._options.cwd or "."
    chunks: list[bytes] = []
    exit_result = await ops.exec(
        cmd.command,
        cwd,
        on_data=chunks.append,
        signal=None,
    )
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    body, info = truncate_tail(raw, max_lines=256, max_bytes=32 * 1024)
    # P-115 BLOCKING: Pi ``BashResult`` shape (Pi
    # ``coding-agent/core/bash-executor.ts:29-40``):
    #   ``{output: string, exitCode: number | undefined,
    #     cancelled: boolean, truncated: boolean,
    #     fullOutputPath?: string}``
    # Sprint 6d has no bash cancellation signal yet (see
    # ``DEFERRED_COMMANDS["abort_bash"]`` → ADR-0058 Sprint 6f), so
    # ``cancelled`` is always False. ``fullOutputPath`` is omitted when
    # the local executor produced no spill file.
    data: dict[str, Any] = {
        "output": body,
        "exitCode": exit_result.exit_code,
        "cancelled": False,
        "truncated": bool(info.truncated) if info else False,
    }
    full_path = getattr(info, "full_output_path", None) if info else None
    if full_path is not None:
        data["fullOutputPath"] = str(full_path)
    return RpcSuccessResponse(id=cmd.id, command="bash", data=data)


async def _handle_set_thinking_level(
    harness: AgentHarness,
    cmd: RpcCommandSetThinkingLevel,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:317-319`` (set_thinking_level handler)."""

    await harness.set_thinking_level(cmd.level)
    return RpcSuccessResponse(id=cmd.id, command="set_thinking_level")


async def _handle_set_session_name(
    harness: AgentHarness,
    cmd: RpcCommandSetSessionName,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:432-440`` (set_session_name handler)."""

    name = cmd.name.strip()
    if not name:
        return RpcErrorResponse(
            id=cmd.id,
            command="set_session_name",
            error="Session name cannot be empty",
        )
    if harness._session is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="set_session_name",
            error="set_session_name() requires options.session to be attached",
        )
    await harness._session.append_session_name(name)
    harness._cached_session_name = name
    return RpcSuccessResponse(id=cmd.id, command="set_session_name")


# === Sprint 6f W2 model handlers (P-168 / P-169 / ADR-0065) ==================
#
# Three handlers wire to the new ModelRegistry runtime. Each takes
# ``(harness, registry, cmd)`` — the dispatcher closure threads
# ``registry`` lazily (default: ``ModelRegistry.in_memory`` over
# whichever AuthStorage the harness has, or a fresh one).


def _model_to_dict(model: Model) -> dict[str, Any]:
    """Pi parity: serialize :class:`Model` to the Pi camelCase wire shape.

    Pi ``Model<Api>`` (``ai/src/types.ts``) is consumed by the RPC
    client as camelCase JSON: ``id``, ``name``, ``provider``, ``api``,
    ``cost``, ``thinkingLevelMap``, ``maxTokens``, ``contextWindow``,
    ``reasoning``, ``baseUrl``, ``headers``, ``input``.
    """

    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "api": model.api,
        "cost": {
            "input": model.cost.input,
            "output": model.cost.output,
            "cacheRead": model.cost.cache_read,
            "cacheWrite": model.cost.cache_write,
        },
        "thinkingLevelMap": model.thinking_level_map,
        "maxTokens": model.max_tokens,
        "contextWindow": model.context_window,
        "reasoning": model.reasoning,
        "baseUrl": model.base_url,
        "input": list(model.input),
        # Sprint 6f W6 (P-178): include ``headers`` when present. Pi
        # ``Model.headers?`` is optional; omit the key entirely (matches
        # ``JSON.stringify`` undefined-skip) when value is None.
        **({"headers": dict(model.headers)} if model.headers else {}),
    }


async def _handle_set_model(
    harness: AgentHarness,
    registry: Any,  # ``ModelRegistry`` — typed via TYPE_CHECKING import
    cmd: Any,  # ``RpcCommandSetModel``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:454-459`` ``handle_set_model``.

    Sprint 6f W6 (P-172): searches :meth:`ModelRegistry.get_available`
    (auth-filtered) instead of :meth:`find` (full catalog). Pi searches
    the available list so requests for a configured-but-unauthenticated
    provider return ``Model not found`` rather than silently selecting a
    model the harness can't actually call. Returns the Pi-shape
    ``Model<Api>`` dict on success, or a ``Model not found:
    {provider}/{model_id}`` error.
    """

    if registry is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="set_model",
            error="set_model requires a ModelRegistry — none configured",
        )
    # P-172: Pi ``rpc-mode.ts:454-459`` searches ``getAvailable()`` (the
    # auth-filtered list). The Sprint 6f W2 ``find()`` path iterated
    # the FULL catalog and would happily select an unauthenticated
    # provider's model; Sprint 6f W6 narrows to the configured-auth
    # subset to match Pi byte-for-byte.
    available = registry.get_available()
    model = next(
        (m for m in available if m.provider == cmd.provider and m.id == cmd.model_id),
        None,
    )
    if model is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="set_model",
            error=f"Model not found: {cmd.provider}/{cmd.model_id}",
        )
    harness.set_current_model(model)
    return RpcSuccessResponse(
        id=cmd.id, command="set_model", data=_model_to_dict(model)
    )


async def _handle_cycle_model(
    harness: AgentHarness,
    registry: Any,  # ``ModelRegistry``
    cmd: Any,  # ``RpcCommandCycleModel``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts::handle_cycle_model`` (Sprint 6f W2).

    Rotates to the next model in :meth:`ModelRegistry.get_available`
    (insertion order). Updates :meth:`AgentHarness.set_current_model`
    and emits the new model + clamped thinking level + ``isScoped``.

    Sprint 6f₁ always returns ``isScoped: False`` — workspace-scoped
    selection is Sprint 6g per spec §J.

    Returns ``data: None`` if no models are available (Pi parity).
    """

    from aelix_ai.models import clamp_thinking_level, models_are_equal

    if registry is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="cycle_model",
            error="cycle_model requires a ModelRegistry — none configured",
        )
    available = registry.get_available()
    # P-170: Pi ``agent-session.ts:1476`` returns ``undefined`` (Aelix
    # ``data: None``) when ``availableModels.length <= 1`` — rotation
    # against a single-model list is a no-op. The Sprint 6f W2 ``not
    # available`` guard only covered the empty case; Sprint 6f W6
    # widens to ``<= 1`` to match Pi byte-for-byte.
    if len(available) <= 1:
        return RpcSuccessResponse(id=cmd.id, command="cycle_model", data=None)
    current = harness.current_model
    next_index = 0
    if current is not None:
        for i, m in enumerate(available):
            if models_are_equal(m, current):
                next_index = (i + 1) % len(available)
                break
    next_model = available[next_index]
    harness.set_current_model(next_model)
    # P-182: Pi ``agent-session.ts:1490`` clamps via the harness's
    # current thinking level; on a fresh harness ``state.thinking_level``
    # may be :data:`None`, but the Pi caller always carries a level
    # string. Coerce to ``"off"`` so :func:`clamp_thinking_level`
    # produces a real Pi-shape level rather than propagating ``None``.
    current_level = harness.state.thinking_level or "off"
    thinking_level = clamp_thinking_level(next_model, current_level)
    # P-171: Pi ``agent-session.ts:1490`` calls
    # ``this.setThinkingLevel(thinkingLevel)`` BEFORE returning so the
    # next turn uses the clamped value. Sprint 6f W2 only computed
    # the clamped level for the response payload and never persisted
    # it. The clamp can be :data:`None` (Sprint 6b back-compat) so
    # only persist when the clamp produced a concrete level.
    if thinking_level is not None:
        await harness.set_thinking_level(thinking_level)
    return RpcSuccessResponse(
        id=cmd.id,
        command="cycle_model",
        data={
            "model": _model_to_dict(next_model),
            "thinkingLevel": thinking_level,
            "isScoped": False,
        },
    )


async def _handle_get_available_models(
    harness: AgentHarness,
    registry: Any,  # ``ModelRegistry``
    cmd: Any,  # ``RpcCommandGetAvailableModels``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts::handle_get_available_models``
    (Sprint 6f W2).

    Returns ``{models: [...]}`` filtered through
    :meth:`ModelRegistry.get_available` (configured-auth check).
    """

    if registry is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="get_available_models",
            error="get_available_models requires a ModelRegistry — none configured",
        )
    available = registry.get_available()
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_available_models",
        data={"models": [_model_to_dict(m) for m in available]},
    )


# === Deferred handler factory =================================================


def _make_deferred_handler(
    cmd_type: str, owner_adr: str
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Return a handler that emits an :class:`RpcErrorResponse` stub.

    Closure pin asserts every key in :data:`DEFERRED_COMMANDS` routes
    through this factory and produces ``success: false``.
    """

    async def _handler(_harness: Any, cmd: Any) -> RpcResponse:
        return RpcErrorResponse(
            id=getattr(cmd, "id", None),
            command=cmd_type,
            error=f"{cmd_type} not implemented in Sprint 6d ({owner_adr})",
        )

    return _handler


# Per-discriminator dispatch table. Constructed once at import time.
# Sprint 6f W2 (ADR-0065): set_model / cycle_model / get_available_models
# are 3-arg handlers (``harness, registry, cmd``); the others stay 2-arg
# (``harness, cmd``). The :func:`build_dispatch_table` factory closes
# over a ``model_registry`` arg so the 3-arg handlers always receive it.
_SUPPORTED_HANDLERS_HARNESS_ONLY: dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
] = {
    "prompt": _handle_prompt,
    "abort": _handle_abort,
    "new_session": _handle_new_session,
    "get_state": _handle_get_state,
    "get_messages": _handle_get_messages,
    "compact": _handle_compact,
    "bash": _handle_bash,
    "set_thinking_level": _handle_set_thinking_level,
    "set_session_name": _handle_set_session_name,
}

_SUPPORTED_HANDLERS_HARNESS_REGISTRY: dict[
    str,
    Callable[[Any, Any, Any], Awaitable[RpcResponse]],
] = {
    "set_model": _handle_set_model,
    "cycle_model": _handle_cycle_model,
    "get_available_models": _handle_get_available_models,
}

# Sprint 6f W2 (ADR-0065): legacy alias preserved for Sprint 6d tests
# that import :data:`_SUPPORTED_HANDLERS` — the alias points at the
# 2-arg subset so the existing assertions keep matching.
_SUPPORTED_HANDLERS: dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
] = _SUPPORTED_HANDLERS_HARNESS_ONLY


def build_dispatch_table(
    model_registry: ModelRegistry | None = None,
) -> dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
]:
    """Build the full 29-command dispatch table.

    Sprint 6f W2 (ADR-0065): 12 supported + 17 deferred = 29 total.
    Returned fresh on every call so tests can introspect without
    leaking state. The supported handlers are real callables; the
    deferred ones are :func:`_make_deferred_handler` closures.

    The ``model_registry`` argument is threaded into the 3 new
    Sprint 6f handlers (``set_model`` / ``cycle_model`` /
    ``get_available_models``). When :data:`None`, the 3 model
    handlers return a :class:`RpcErrorResponse` reporting the missing
    registry (Pi parity: ``rpc-mode.ts`` requires a ModelRegistry to
    be bound to the runtime host).
    """

    table: dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]] = dict(
        _SUPPORTED_HANDLERS_HARNESS_ONLY
    )
    # Adapt the 3-arg handlers to the dispatch table's 2-arg shape by
    # closing over ``model_registry``.
    for cmd_type, three_arg in _SUPPORTED_HANDLERS_HARNESS_REGISTRY.items():
        table[cmd_type] = _bind_registry(three_arg, model_registry)
    for cmd_type, adr in DEFERRED_COMMANDS.items():
        table[cmd_type] = _make_deferred_handler(cmd_type, adr)
    return table


def _bind_registry(
    handler: Callable[[Any, Any, Any], Awaitable[RpcResponse]],
    registry: ModelRegistry | None,
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Adapt a 3-arg ``(harness, registry, cmd)`` handler to the dispatch
    table's 2-arg ``(harness, cmd)`` shape by closing over the registry.
    """

    async def _adapted(harness: Any, cmd: Any) -> RpcResponse:
        return await handler(harness, registry, cmd)

    return _adapted


# === Entry point ==============================================================


async def _handle_command(
    harness: AgentHarness,
    payload: dict[str, Any],
    dispatch: dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]],
) -> RpcResponse:
    """Parse + dispatch a single command payload.

    Errors during parse return an :class:`RpcErrorResponse` with
    ``command="parse"`` (Pi parity, ``rpc-mode.ts:464-470``).
    """

    try:
        cmd: RpcCommand = parse_rpc_command(payload)
    except (ValueError, TypeError) as exc:
        # P-120: Pi parity (`rpc-mode.ts:464-470`) — every parse failure
        # (unknown type, missing required field, type/value error) emits
        # ``command="parse"``. The previous behavior echoed the user's
        # claimed ``type`` which broke clients dispatching on ``parse``.
        return RpcErrorResponse(
            id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            command="parse",
            error=f"Failed to parse command: {exc}",
        )
    handler = dispatch.get(cmd.type)
    if handler is None:
        return RpcErrorResponse(
            id=cmd.id,
            command=cmd.type,
            error=f"Unknown command: {cmd.type}",
        )
    try:
        return await handler(harness, cmd)
    except Exception as exc:  # noqa: BLE001 — wire-back to client as error
        return RpcErrorResponse(
            id=cmd.id,
            command=cmd.type,
            error=str(exc),
        )


async def run_rpc_mode(
    harness: AgentHarness,
    *,
    model_registry: ModelRegistry | None = None,
    stdin: asyncio.StreamReader | None = None,
    stdout_write: Callable[[bytes], None] | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Pi parity: ``rpc-mode.ts:runRpcMode`` (lines 50-492).

    Hijacks ``sys.stdout`` (redirects to ``sys.stderr`` so tool ``print``
    output stays out of the JSONL stream — Pi ``takeOverStdout``), attaches
    a JSONL line reader to stdin, subscribes to harness session events,
    and dispatches commands until EOF or a shutdown signal.

    Args:
        harness: Active :class:`AgentHarness` whose surface backs the 9
            supported commands.
        model_registry: Optional :class:`ModelRegistry` backing the 3
            Sprint 6f model commands (``set_model`` / ``cycle_model`` /
            ``get_available_models``). When :data:`None` the 3 handlers
            return a "no registry configured" error (Pi parity:
            ModelRegistry is bound to the runtime host).
        stdin: Optional :class:`asyncio.StreamReader`. When ``None`` the
            entry connects to ``sys.stdin.buffer`` via
            :func:`asyncio.connect_read_pipe`.
        stdout_write: Optional sink for outgoing JSONL records. When
            ``None`` writes are buffered through the real ``sys.stdout``
            file descriptor (bypassing the redirect).
        install_signal_handlers: Set False in tests to skip the
            SIGTERM/SIGHUP wiring (the test harness uses asyncio cancel
            instead).
    """

    # Pi takeOverStdout — pre-redirect the real stdout fd to stderr so
    # any stray ``print()`` from tools/extensions doesn't corrupt the
    # JSONL frame. Tests inject ``stdout_write`` to bypass.
    stdout_redirect_cm: contextlib.AbstractContextManager[Any] = (
        contextlib.redirect_stdout(sys.stderr)
    )
    if stdout_write is None:
        # Capture the real stdout descriptor BEFORE redirect, then route
        # all RPC writes through it.
        real_stdout_fd = sys.__stdout__
        if real_stdout_fd is None:
            real_stdout_fd = sys.stdout  # fallback for unusual environments

        def _write(data: bytes) -> None:
            try:
                real_stdout_fd.buffer.write(data)
                real_stdout_fd.buffer.flush()
            except (AttributeError, ValueError, OSError):
                # ``__stdout__`` may not expose ``.buffer`` in unusual
                # environments (e.g. captured by pytest); fall back to
                # the text stream.
                real_stdout_fd.write(data.decode("utf-8", errors="replace"))
                real_stdout_fd.flush()

        stdout_write = _write

    write_sink: Callable[[bytes], None] = stdout_write

    def _output(obj: Any) -> None:
        write_sink(serialize_json_line(obj).encode("utf-8"))

    # Connect stdin if not supplied.
    own_stdin = stdin is None
    if stdin is None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        stdin = reader

    dispatch = build_dispatch_table(model_registry)
    shutdown_event = asyncio.Event()

    # === Event pipe ===========================================================
    # Pi parity: session.subscribe((event) => output(event)). The Aelix
    # harness exposes ``subscribe`` with the same signature.

    def _on_agent_event(event: Any) -> None:
        try:
            payload = _event_to_dict(event)
            # Best-effort JSON-serializable check.
            try:
                json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                payload = {"type": getattr(event, "type", "unknown")}
            _output(payload)
        except Exception:  # noqa: BLE001
            # Pi swallows listener errors; matches.
            pass

    unsubscribe = harness.subscribe(_on_agent_event)

    # === Signal handlers ======================================================
    # Pi parity: SIGTERM always; SIGHUP on non-Windows.

    installed_handlers: list[tuple[int, Any]] = []
    if install_signal_handlers:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                previous = signal.getsignal(sig)
                loop.add_signal_handler(sig, shutdown_event.set)
                installed_handlers.append((sig, previous))
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows / non-main-thread paths fall through silently;
                # asyncio.run() in the main thread handles SIGINT via
                # KeyboardInterrupt regardless.
                pass

    # === Stdin reader =========================================================

    pending_command_tasks: set[asyncio.Task[Any]] = set()

    def _on_line(line: str) -> None:
        if not line.strip():
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            _output(
                RpcErrorResponse(
                    command="parse",
                    error=f"Failed to parse command: {exc}",
                ).to_json()
            )
            return
        if not isinstance(payload, dict):
            _output(
                RpcErrorResponse(
                    command="parse",
                    error="Command payload must be a JSON object",
                ).to_json()
            )
            return
        # Route extension UI responses to a no-op for Sprint 6d (bridge
        # deferred to Sprint 6f per ADR-0058). The wire shape is recognised
        # but not yet correlated.
        if payload.get("type") == "extension_ui_response":
            return

        async def _dispatch() -> None:
            response = await _handle_command(harness, payload, dispatch)
            _output(response.to_json())

        task = asyncio.create_task(_dispatch())
        pending_command_tasks.add(task)
        task.add_done_callback(pending_command_tasks.discard)

    reader_obj = JsonlLineReader(_on_line)
    stdin_eof = asyncio.Event()

    async def _pump_stdin() -> None:
        try:
            while True:
                chunk = await stdin.read(4096)
                if not chunk:
                    reader_obj.end()
                    stdin_eof.set()
                    return
                reader_obj.feed(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            stdin_eof.set()

    pump_task = asyncio.create_task(_pump_stdin())

    # === Main loop ============================================================
    try:
        with stdout_redirect_cm:
            await asyncio.wait(
                [
                    asyncio.create_task(shutdown_event.wait()),
                    asyncio.create_task(stdin_eof.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Drain any in-flight command dispatches before tearing down.
            if pending_command_tasks:
                await asyncio.gather(*pending_command_tasks, return_exceptions=True)
    finally:
        pump_task.cancel()
        with contextlib.suppress(BaseException):
            await pump_task
        unsubscribe()
        # Restore signal handlers.
        if install_signal_handlers:
            for sig, previous in installed_handlers:
                try:
                    loop = asyncio.get_running_loop()
                    loop.remove_signal_handler(sig)
                    if callable(previous):
                        signal.signal(sig, previous)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
        # Pi parity: ``await runtimeHost.dispose()``.
        with contextlib.suppress(Exception):
            await harness.dispose()
        if own_stdin:
            # asyncio's pipe transport is closed when the reader hits EOF;
            # nothing to actively pause on the Python side.
            pass


__all__ = [
    "DEFERRED_COMMANDS",
    "SUPPORTED_COMMANDS",
    "build_dispatch_table",
    "run_rpc_mode",
]
