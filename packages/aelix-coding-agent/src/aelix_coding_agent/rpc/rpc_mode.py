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

Sprint 6h₁ (ADR-0069 / P-219) drops 1 entry from
:data:`DEFERRED_COMMANDS` and adds it to :data:`SUPPORTED_COMMANDS`:
``get_commands``. The counts move to **13 supported / 16 deferred /
29 total**.

Sprint 6h₂ (ADR-0071 / P-245~P-253) wires **9** additional commands:
``steer`` / ``follow_up`` / ``cycle_thinking_level`` /
``set_steering_mode`` / ``set_follow_up_mode`` /
``set_auto_compaction`` / ``set_auto_retry`` / ``abort_retry`` /
``abort_bash``. The counts move to **22 supported / 7 deferred /
29 total**.

Sprint 6h₃ (ADR-0073 / P-268~P-274) wires **2** more commands:
``get_session_stats`` (Pi ``rpc-mode.ts:553-556`` →
``agent-session.ts:2901-2945``) and ``export_html`` (Pi
``rpc-mode.ts:558-561`` → ``coding-agent/src/core/export-html/``).
The counts move to **24 supported / 5 deferred / 29 total**. The
remaining 5 session-tree commands (``switch_session`` / ``fork`` /
``clone`` / ``get_fork_messages`` / ``get_last_assistant_text``)
are owned by ADR-0074 and defer to Sprint 6h₄.

Sprint 6h₄a (ADR-0075 / ADR-0076 / P-293~P-298) wires **2** more
read-only session-navigation commands: ``get_fork_messages`` (Pi
``rpc-mode.ts:591-594`` → ``agent-session.ts:2870-2885``) and
``get_last_assistant_text`` (Pi ``rpc-mode.ts:596-599`` →
``agent-session.ts:3059-3081``). The counts move to **26 supported /
3 deferred / 29 total**. The remaining 3 session-tree commands
(``switch_session`` / ``fork`` / ``clone``) defer to Sprint 6h₄b
per ADR-0076 (porting Pi ``AgentSessionRuntime`` +
``SessionManager.getLeafId`` + ``rebindSession`` seam).

P-293 line drift: ADR-0074's carry-forward roster estimated
``rpc-mode.ts:563-566`` / ``:568-571`` for the two new handlers; W0
verification at SHA 734e08e puts the actual case sites at
``591-594`` / ``596-599``. ADR-0075 records the supersession.

P-298 SYNTHESIS: ``_handle_get_last_assistant_text`` mirrors Pi's
``JSON.stringify({text: undefined})`` → ``{}`` key-omission. When
:meth:`AgentHarness.get_last_assistant_text` returns :data:`None`
the handler emits ``data == {}`` — consistent with the existing
:func:`_session_stats_to_dict` undefined-skip pattern asserted by
Sprint 6h₃ closure pin.

Sprint 6h₄b (ADR-0077 / ADR-0078 / P-302~P-310) — FOUNDATION-ONLY port
of Pi ``AgentSessionRuntime`` (``core/agent-session-runtime.ts:67-374``)
and the ``rebindSession`` closure (``rpc-mode.ts:310-349``). No new RPC
commands wired; counts stay at 26 supported / 3 deferred / 29 total.
Owner rebrand to ADR-0078 applied per spec §D.5; foundation lands
without wiring. The 3 deferred commands rebrand to ADR-0078 owner
(Sprint 6h₄c). The :func:`run_rpc_mode` signature accepts a NEW
optional ``runtime_host: AgentSessionRuntime | None = None``
parameter; existing callers continue to work unchanged (P-309 compat
shim).

Sprint 6h₄c (ADR-0079 / ADR-0080 / P-323~P-331) — PHASE 4 CLOSURE.
Wires the 3 last DEFERRED session-tree commands (``switch_session`` /
``fork`` / ``clone``) on top of the Sprint 6h₄b
:class:`AgentSessionRuntime` foundation. Counts move to **29 supported /
0 deferred / 29 total** — full Pi parity for the ``RpcCommand``
discriminator union.

New :data:`_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class (P-326) carries
4 handlers operating on :class:`AgentSessionRuntime` instead of
:class:`AgentHarness`: ``new_session`` (MOVED from HARNESS_ONLY per
P-330 — replaces the Sprint 6d stub that rejected ``parent_session``) +
``switch_session`` + ``fork`` + ``clone``.

:func:`run_rpc_mode` signature extends with optional ``repo`` / ``fs``
keyword parameters (P-324) — supplied by the caller for explicit
``runtime_host`` setup, or defaulted to :class:`LocalFileSystem` +
:class:`JsonlSessionRepo` when constructing the passthrough.

Aelix handlers DO NOT call rebind manually (P-329 deliberate
convergence): Pi belt-and-braces handler-side ``await rebindSession()``
at ``rpc-mode.ts:566`` / ``:574`` / ``:586`` is NOT mirrored — the
runtime's ``_finish_session_replacement`` auto-invokes the registered
callback as single source of truth (verified by closure-pin assertion
that ``_rebind_session`` is awaited exactly 1× per replace).

Pi line citations at SHA 734e08e per Sprint 6h₄c W0 (P-323):
- ``rpc-mode.ts:563-569`` (``switch_session`` handler — 7 lines)
- ``rpc-mode.ts:571-577`` (``fork`` handler — 7 lines)
- ``rpc-mode.ts:579-589`` (``clone`` handler — 11 lines)
- ``rpc-mode.ts:277-282`` (``new_session`` handler — Sprint 6d stub
  replaced via runtime_host route per P-330)

Pi line citations at SHA 734e08e per Sprint 6h₂ W5/W6 audit (P-258):
the 9 case sites in ``rpc-mode.ts`` live at lines 483-547 (NOT
528-635 as earlier drafts suggested) and delegate to ``AgentSession``
methods in ``coding-agent/src/core/agent-session.ts`` — each handler
docstring below cites BOTH the case site and the session method.
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

from aelix_ai.messages import ImageContent

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
    RpcSlashCommand,
    RpcSuccessResponse,
    parse_rpc_command,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness

    from aelix_coding_agent.model_registry import ModelRegistry

# Sprint 6h₄b (ADR-0077, P-302~P-310): import the runtime layer for the
# new ``runtime_host`` optional parameter on :func:`run_rpc_mode` +
# the ``_make_passthrough_runtime`` helper used by the back-compat
# shim. The imports stay at module level (not behind ``TYPE_CHECKING``)
# because the helpers are CALLED at runtime by :func:`run_rpc_mode`.
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.runtime._types import HarnessFactory

# Sprint 6h₄c (ADR-0079, P-324): import the FileSystem + JsonlSessionRepo
# surface for the new ``repo`` / ``fs`` keyword parameters threaded into
# :func:`_make_passthrough_runtime` and :func:`run_rpc_mode`.
from aelix_agent_core.session.fs import FileSystem, LocalFileSystem
from aelix_agent_core.session.jsonl_repo import JsonlSessionRepo

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

# Sprint 6h₄c (ADR-0079 / ADR-0080 / P-323~P-331) wires the 3 last
# DEFERRED session-tree commands (``switch_session`` / ``fork`` /
# ``clone``) on top of the 6h₄b :class:`AgentSessionRuntime`
# foundation. The counts move to **29 supported / 0 deferred / 29
# total** — full Pi parity for the RPC roster (Phase 4 closure).
DEFERRED_COMMANDS: dict[str, str] = {}


# Supported command discriminator → handler name mapping. Sprint 6f W2
# (ADR-0065) wires set_model / cycle_model / get_available_models →
# count moves from 9 to 12. The closure pin
# (``tests/pi_parity/test_phase_4_4_strict_superset.py``) asserts the
# union of ``SUPPORTED_COMMANDS`` and ``DEFERRED_COMMANDS`` equals the
# 29-element Pi ``RpcCommand`` discriminator set (W4 M2 / P-121).
# Sprint 6h₂ (ADR-0071) adds 9: steer / follow_up / cycle_thinking_level
# / set_steering_mode / set_follow_up_mode / set_auto_compaction /
# set_auto_retry / abort_retry / abort_bash → 13 → 22 supported.
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
        # Sprint 6h₁ (ADR-0069 / P-219).
        "get_commands",
        # Sprint 6h₂ (ADR-0071 / P-245~P-253) — 9 new wired commands.
        "steer",
        "follow_up",
        "cycle_thinking_level",
        "set_steering_mode",
        "set_follow_up_mode",
        "set_auto_compaction",
        "set_auto_retry",
        "abort_retry",
        "abort_bash",
        # Sprint 6h₃ (ADR-0073 / P-268~P-274) — 2 session-inspection
        # commands. Pi parity: ``rpc-mode.ts:475-478`` + ``:480-483``.
        "get_session_stats",
        "export_html",
        # Sprint 6h₄a (ADR-0075 / P-293~P-298) — 2 read-only session-
        # navigation commands. Pi parity: ``rpc-mode.ts:591-594`` +
        # ``:596-599`` (W0 verified at SHA 734e08e — supersedes the
        # ADR-0074 ``563-566`` / ``568-571`` estimates).
        "get_fork_messages",
        "get_last_assistant_text",
        # Sprint 6h₄c (ADR-0079 / P-323~P-331) — 3 session-tree
        # commands wired on top of the 6h₄b runtime foundation. Pi
        # parity: ``rpc-mode.ts:563-569`` / ``:571-577`` / ``:579-589``
        # (W0 verified at SHA 734e08e — supersedes the ADR-0078
        # carry-forward roster estimates ``:566`` / ``:574`` / ``:586``).
        "switch_session",
        "fork",
        "clone",
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
    runtime_host: AgentSessionRuntime,
    cmd: RpcCommandNewSession,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:277-282`` (new_session handler).

    Sprint 6h₄c (ADR-0079, P-330): replaces the Sprint 6d stub (which
    rejected ``parent_session`` with an :class:`RpcErrorResponse` —
    old body at ``rpc_mode.py:309-347``) by routing through
    :meth:`AgentSessionRuntime.new_session` with
    ``parent_session=cmd.parent_session``. The Sprint 6d ``ADR-0058 —
    parent_session deferred`` carry-forward CLOSES here.

    P-329 deliberate convergence — this handler MUST NOT call
    ``rebind_session`` manually. The runtime's
    ``_finish_session_replacement`` auto-invokes the registered callback
    as single source of truth; Pi's belt-and-braces handler-side rebind
    is NOT mirrored.
    """

    # W4 MINOR-4 (Sprint 6h₄c W6): drop the blanket ``except RuntimeError``
    # catch. The outer :func:`_handle_command` wraps any exception into a
    # Pi-shape error envelope, so the inner catch is redundant; removing it
    # also avoids masking unrelated RuntimeError leaks (e.g. noop-factory
    # ``Passthrough runtime cannot replace harness``) which should fail
    # loud rather than wire back as a successful "new_session" envelope.
    result = await runtime_host.new_session(
        parent_session=cmd.parent_session
    )
    return RpcSuccessResponse(
        id=cmd.id,
        command="new_session",
        data={"cancelled": result.cancelled},
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
        # Sprint 6h₂ (P-252): real source instead of hardcoded True.
        # The Sprint 6d W4 m1 placeholder is retired — the harness now
        # exposes ``auto_compaction_enabled`` as a public property
        # backed by ``AgentState.auto_compaction_enabled`` (toggled via
        # :meth:`AgentHarness.set_auto_compaction_enabled`).
        auto_compaction_enabled=harness.auto_compaction_enabled,
        # Sprint 6h₂ W6 (P-264 BLOCKING): symmetric ``auto_retry_enabled``
        # surface — RPC clients can now observe the toggle set via the
        # ``set_auto_retry`` command.
        auto_retry_enabled=harness.auto_retry_enabled,
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


# === Sprint 6h₁ (ADR-0069, P-219) — get_commands handler =====================
#
# Sprint 6h₁ W6 (P-225 BLOCKING): the wire-shape ``sourceInfo`` matches
# Pi ``source-info.ts:1-12`` exactly — ``{path, source, scope, origin}``
# plus optional ``baseDir``. The three synthesisers below emit that
# shape from each source's own metadata.


def _source_info_payload(
    *,
    path: str | None,
    source: str | None,
    scope: str | None,
    origin: str | None,
    base_dir: str | None,
) -> dict[str, Any]:
    """Pi parity: build the wire-shape ``sourceInfo`` dict.

    Defaults match Pi's "sensible fallback" — ``scope="user"`` and
    ``origin="top-level"`` when callers omit them. Emits ``baseDir``
    only when supplied so the JSON wire matches Pi's
    ``JSON.stringify`` undefined-skip behaviour.
    """

    payload: dict[str, Any] = {
        "path": path or "",
        "source": source or "",
        "scope": scope or "user",
        "origin": origin or "top-level",
    }
    if base_dir:
        payload["baseDir"] = base_dir
    return payload


def _registered_command_source_info(resolved: Any) -> dict[str, Any]:
    """Pi parity (P-225): wire-shape ``sourceInfo`` for an extension
    command, derived from the owning extension's
    :class:`ExtensionSourceInfo` (attached by
    :meth:`ExtensionRunner.get_registered_commands` per P-229).
    """

    info = resolved.source_info
    if info is None:
        return _source_info_payload(
            path=None,
            source="unknown",
            scope=None,
            origin=None,
            base_dir=None,
        )
    # Pi byte-for-byte: prefer the explicit ``path`` field, then
    # ``base_dir``; ``source`` is the Pi enum ("user" / "project" /
    # "package" / "global"), falling back to ``identifier`` and finally
    # the Aelix ``source`` distinguisher.
    return _source_info_payload(
        path=getattr(info, "path", None) or getattr(info, "base_dir", None),
        source=getattr(info, "identifier", None) or info.source,
        scope=getattr(info, "scope", None),
        origin=getattr(info, "origin", None),
        base_dir=getattr(info, "base_dir", None),
    )


def _prompt_template_source_info(template: Any) -> dict[str, Any]:
    """Pi parity (P-225): wire-shape ``sourceInfo`` for a prompt template.

    Pi attaches a :class:`SourceInfo` whose ``path`` is the template's
    loaded file path. Sprint 6h₁ doesn't yet thread the file path back
    onto :class:`PromptTemplate` (the Pi loader populates it at
    ``loadTemplateFromFile`` time); when the field is missing we still
    emit the Pi default shape so the wire is well-formed.
    """

    path = getattr(template, "file_path", None) or getattr(template, "path", None)
    return _source_info_payload(
        path=path,
        source=getattr(template, "source", None),
        scope=getattr(template, "scope", None),
        origin=getattr(template, "origin", None),
        base_dir=None,
    )


def _skill_source_info(skill: Any) -> dict[str, Any]:
    """Pi parity (P-225): wire-shape ``sourceInfo`` for a skill.

    Pi sets ``path`` to the SKILL.md path and ``source`` to the
    discovered source name. Aelix's :class:`Skill` carries ``file_path``
    directly; we surface it as both ``path`` and the wire ``source``
    fallback so RPC clients can locate the skill on disk even without
    application-side sourcing.
    """

    file_path = getattr(skill, "file_path", None)
    return _source_info_payload(
        path=file_path,
        source=getattr(skill, "source", None) or file_path,
        scope=getattr(skill, "scope", None),
        origin=getattr(skill, "origin", None),
        base_dir=None,
    )


async def _handle_get_commands(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandGetCommands``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:622-653`` ``get_commands`` handler.

    Aggregates 3 sources, in Pi insertion order:

    1. **Extension commands** — ``harness.extension_runner.get_registered_commands()``
       returns :class:`ResolvedCommand`. Wire ``name`` ←
       :attr:`ResolvedCommand.invocation_name` (Pi
       ``command.invocationName`` — disambiguated per P-224).
       ``source="extension"``.
    2. **Prompt templates** — ``harness.prompt_templates``. Wire
       ``name`` ← ``template.name``. ``source="prompt"``.
    3. **Skills** — ``harness.skills``. Wire ``name`` ←
       ``f"skill:{skill.name}"`` (Pi prefix convention).
       ``source="skill"``.

    Each entry's wire ``sourceInfo`` follows Pi ``source-info.ts:1-12``
    shape ``{path, source, scope, origin, baseDir?}`` per W6 P-225.
    """

    commands: list[RpcSlashCommand] = []

    # Source 1: extension commands. Pi parity: ``rpc-mode.ts:625-632``.
    # Sprint 6h₁ W6 (P-224): :attr:`ResolvedCommand.invocation_name`
    # carries the Pi-disambiguated wire name.
    for resolved in harness.extension_runner.get_registered_commands():
        commands.append(
            RpcSlashCommand(
                name=resolved.invocation_name,
                description=resolved.command.description,
                source="extension",
                source_info=_registered_command_source_info(resolved),
            )
        )

    # Source 2: prompt templates. Pi parity: ``rpc-mode.ts:634-641``.
    for template in harness.prompt_templates:
        commands.append(
            RpcSlashCommand(
                name=template.name,
                description=template.description,
                source="prompt",
                source_info=_prompt_template_source_info(template),
            )
        )

    # Source 3: skills (prefixed with ``"skill:"``). Pi parity:
    # ``rpc-mode.ts:643-650``.
    for skill in harness.skills:
        commands.append(
            RpcSlashCommand(
                name=f"skill:{skill.name}",
                description=skill.description,
                source="skill",
                source_info=_skill_source_info(skill),
            )
        )

    # Pi parity: ``return success(id, "get_commands", { commands })``.
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_commands",
        data={"commands": [c.to_json() for c in commands]},
    )


# === Sprint 6h₂ (ADR-0071, P-245~P-253) — 9 new handlers =====================
#
# Pi parity ``rpc-mode.ts:528-635``. Each handler is a thin wrapper
# around the corresponding harness method. The set_steering_mode /
# set_follow_up_mode handlers catch :exc:`ValueError` (Pi uses TS narrow
# types; Aelix runtime-checks) and surface an :class:`RpcErrorResponse`.


def _decode_images(
    payload: list[dict[str, Any]] | None,
) -> list[ImageContent] | None:
    """Pi parity: decode the RPC ``images`` wire shape into
    :class:`ImageContent` instances.

    Sprint 6h₂ (P-251): the RPC layer carries ``images`` as plain
    ``dict`` records — each entry mirrors the Pi
    ``ImageContent.{mimeType, data}`` wire shape. Returns :data:`None`
    when the payload is missing or empty so the harness can fall
    through to its text-only enqueue path.

    Sprint 6h₂ W6 (P-262 BLOCKING): strict validation — Pi sends
    ``ImageContent.mimeType`` in camelCase only (Pi TS type narrows at
    compile time). Missing ``mimeType`` / ``data`` fields raise
    :exc:`ValueError`; the outer dispatcher surfaces the failure as a
    Pi-shape :class:`RpcErrorResponse` instead of silently coercing
    empty strings into the harness queue. W4 LOW-1 also closes here.
    """

    if not payload:
        return None
    decoded: list[ImageContent] = []
    for i, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(
                f"ImageContent[{i}] must be a dict, got {type(entry).__name__}"
            )
        if "mimeType" not in entry or "data" not in entry:
            raise ValueError(
                f"ImageContent[{i}] missing required 'mimeType' or 'data' field"
            )
        decoded.append(
            ImageContent(
                mime_type=str(entry["mimeType"]),
                data=str(entry["data"]),
            )
        )
    return decoded


async def _handle_steer(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandSteer``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:483-486`` ``steer`` handler
    (delegates to ``agent-session.ts:1181-1192`` ``session.steer``).

    Sprint 6h₂ W6 (P-262 BLOCKING): :func:`_decode_images` strict-
    validates the wire payload — a malformed ``ImageContent`` entry
    raises :exc:`ValueError`, which the outer dispatcher surfaces as a
    Pi-shape ``RpcErrorResponse``.
    """

    images = _decode_images(cmd.images)
    await harness.steer(cmd.message, images=images)
    return RpcSuccessResponse(id=cmd.id, command="steer")


async def _handle_follow_up(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandFollowUp``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:488-491`` ``follow_up`` handler
    (delegates to ``agent-session.ts:1206-1215`` ``session.followUp``)."""

    images = _decode_images(cmd.images)
    await harness.follow_up(cmd.message, images=images)
    return RpcSuccessResponse(id=cmd.id, command="follow_up")


async def _handle_cycle_thinking_level(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandCycleThinkingLevel``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:486-490`` ``cycle_thinking_level``
    handler (delegates to ``agent-session.ts:1537-1548``
    ``session.cycleThinkingLevel``).

    Returns ``data: None`` when the harness has nothing to cycle
    (``cycle_thinking_level`` returns :data:`None`); otherwise wraps
    the level in ``{level: <ThinkingLevel>}`` per Pi's response shape.
    """

    level = await harness.cycle_thinking_level()
    if level is None:
        return RpcSuccessResponse(
            id=cmd.id, command="cycle_thinking_level", data=None
        )
    return RpcSuccessResponse(
        id=cmd.id,
        command="cycle_thinking_level",
        data={"level": level},
    )


async def _handle_set_steering_mode(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandSetSteeringMode``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:498-501`` ``set_steering_mode`` handler
    (delegates to ``agent-session.ts:1587-1592``
    ``session.setSteeringMode``).

    Sprint 6h₂ W6 (P-261): the runtime validation lives on the harness
    setter — Pi narrows the ``mode`` argument at compile time, Aelix
    mirrors via :exc:`ValueError` and the handler surfaces a Pi-shape
    error envelope. Documented Aelix-additive defense.
    """

    try:
        harness.set_steering_mode(cmd.mode)
    except ValueError as exc:
        return RpcErrorResponse(
            id=cmd.id, command="set_steering_mode", error=str(exc)
        )
    return RpcSuccessResponse(id=cmd.id, command="set_steering_mode")


async def _handle_set_follow_up_mode(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandSetFollowUpMode``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:503-506`` ``set_follow_up_mode`` handler
    (delegates to ``agent-session.ts:1594-1599``
    ``session.setFollowUpMode``)."""

    try:
        harness.set_follow_up_mode(cmd.mode)
    except ValueError as exc:
        return RpcErrorResponse(
            id=cmd.id, command="set_follow_up_mode", error=str(exc)
        )
    return RpcSuccessResponse(id=cmd.id, command="set_follow_up_mode")


async def _handle_set_auto_compaction(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandSetAutoCompaction``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:516-519`` ``set_auto_compaction``
    handler (delegates to ``agent-session.ts:2026-2034``
    ``session.setAutoCompactionEnabled``)."""

    harness.set_auto_compaction_enabled(cmd.enabled)
    return RpcSuccessResponse(id=cmd.id, command="set_auto_compaction")


async def _handle_set_auto_retry(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandSetAutoRetry``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:525-528`` ``set_auto_retry`` handler
    (delegates to ``agent-session.ts:2540-2545``
    ``session.setAutoRetryEnabled``)."""

    harness.set_auto_retry_enabled(cmd.enabled)
    return RpcSuccessResponse(id=cmd.id, command="set_auto_retry")


async def _handle_abort_retry(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandAbortRetry``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:530-533`` ``abort_retry`` handler
    (delegates to ``agent-session.ts:2511-2516``
    ``session.abortRetry``)."""

    harness.abort_retry()
    return RpcSuccessResponse(id=cmd.id, command="abort_retry")


async def _handle_abort_bash(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandAbortBash``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:544-547`` ``abort_bash`` handler
    (delegates to ``agent-session.ts:2622-2625``
    ``session.abortBash``)."""

    harness.abort_bash()
    return RpcSuccessResponse(id=cmd.id, command="abort_bash")


# === Sprint 6h₃ (ADR-0073, P-268~P-274) — 2 session-inspection handlers =====
#
# Pi parity:
#   - ``rpc-mode.ts:553-556`` → ``session.getSessionStats()`` →
#     :class:`SessionStats` (``agent-session.ts:212-223``).
#   - ``rpc-mode.ts:558-561`` → ``session.exportToHtml(outputPath)`` →
#     ``{path: string}``.
#
# Both handlers wrap the harness methods added this sprint and serialize
# the response in Pi camelCase. The :func:`_session_stats_to_dict` helper
# OMITs ``sessionFile`` / ``contextUsage`` when :data:`None` so the
# JSON-on-the-wire matches Pi's ``JSON.stringify`` undefined-skip.


def _session_stats_to_dict(stats: Any) -> dict[str, Any]:
    """Pi parity: serialize :class:`SessionStats` to the Pi camelCase
    wire shape (``agent-session.ts:212-223``).

    Required keys: ``sessionId`` / ``userMessages`` / ``assistantMessages``
    / ``toolCalls`` / ``toolResults`` / ``totalMessages`` / ``tokens`` /
    ``cost``. Optional keys: ``sessionFile`` (omitted when :data:`None`)
    and ``contextUsage`` (omitted when :data:`None`).

    Token sub-dict is camelCase per Pi (``cacheRead`` / ``cacheWrite``).
    ``contextUsage`` is the Pi-shape ``{tokens, contextWindow, percent}``
    (``extensions/types.ts`` ``ContextUsage``). Sprint 6h₃ W6 (P-275)
    aligns the wire emit with the Aelix :class:`ContextUsage` dataclass
    at ``extensions/api.py:122-135`` whose snake_case fields
    (``tokens`` / ``context_window`` / ``percent``) map directly into
    the Pi camelCase wire keys.
    """

    out: dict[str, Any] = {
        "sessionId": stats.session_id,
        "userMessages": stats.user_messages,
        "assistantMessages": stats.assistant_messages,
        "toolCalls": stats.tool_calls,
        "toolResults": stats.tool_results,
        "totalMessages": stats.total_messages,
        "tokens": {
            "input": stats.tokens.input,
            "output": stats.tokens.output,
            "cacheRead": stats.tokens.cache_read,
            "cacheWrite": stats.tokens.cache_write,
            "total": stats.tokens.total,
        },
        "cost": stats.cost,
    }
    if stats.session_file is not None:
        out["sessionFile"] = stats.session_file
    if stats.context_usage is not None:
        cu = stats.context_usage
        # Pi parity: extensions/types.ts ContextUsage = { tokens,
        # contextWindow, percent }. Aelix ContextUsage at
        # extensions/api.py:122-135 already matches Pi field names
        # (tokens / context_window / percent → snake_case ↔ camelCase
        # mapping).
        out["contextUsage"] = {
            "tokens": getattr(cu, "tokens", None),
            "contextWindow": int(getattr(cu, "context_window", 0) or 0),
            "percent": getattr(cu, "percent", None),
        }
    return out


async def _handle_get_session_stats(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandGetSessionStats``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:553-556`` ``get_session_stats`` handler.

    Pi body: ``const stats = session.getSessionStats(); return
    success(id, "get_session_stats", stats);`` — the response ``data``
    carries the full :class:`SessionStats` object verbatim (no
    wrapper). Aelix mirrors via :func:`_session_stats_to_dict` so the
    wire shape stays Pi-camelCase.

    Delegates to :meth:`AgentHarness.get_session_stats` which
    aggregates from the in-memory message list (Pi parity:
    ``agent-session.ts:2901-2945``).
    """

    stats = harness.get_session_stats()
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_session_stats",
        data=_session_stats_to_dict(stats),
    )


async def _handle_export_html(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandExportHtml``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:558-561`` ``export_html`` handler.

    Pi body: ``const path = await session.exportToHtml(command.outputPath);
    return success(id, "export_html", { path });`` — the response
    ``data`` is the singleton ``{path: string}`` shape. Aelix mirrors
    via :meth:`AgentHarness.export_to_html` which delegates to the
    minimal HTML emitter at
    :func:`aelix_coding_agent._export_html.export_html`.

    Sprint 6h₃ ships a minimal renderer (Pi wire contract only); Pi
    visual fidelity (CSS framework, syntax highlighting, image
    rendering) defers to Sprint 6h₅+ per ADR-0074.
    """

    path = harness.export_to_html(cmd.output_path)
    return RpcSuccessResponse(
        id=cmd.id,
        command="export_html",
        data={"path": path},
    )


# === Sprint 6h₄a (ADR-0075, P-293~P-298) — 2 session-navigation handlers ====
#
# Pi parity:
#   - ``rpc-mode.ts:591-594`` → ``session.getUserMessagesForForking()`` →
#     :class:`Array<{entryId, text}>` (``agent-session.ts:2870-2885``).
#   - ``rpc-mode.ts:596-599`` → ``session.getLastAssistantText()`` →
#     ``string | undefined`` (``agent-session.ts:3059-3081``).
#
# P-293: ADR-0074 carry-forward roster estimated lines ``563-566`` /
# ``568-571``; W0 verification at SHA 734e08e puts the actual case sites
# at ``591-594`` / ``596-599``. ADR-0075 records the supersession.
#
# P-298 SYNTHESIS (binding): :func:`_handle_get_last_assistant_text` mirrors
# Pi's ``JSON.stringify({text: undefined})`` → ``{}`` key-omission. When
# :meth:`AgentHarness.get_last_assistant_text` returns :data:`None` the
# handler emits ``data == {}`` — consistent with the existing
# :func:`_session_stats_to_dict` undefined-skip pattern asserted by the
# Sprint 6h₃ closure pin.


def _fork_points_to_dict(points: list[Any]) -> list[dict[str, str]]:
    """Pi parity: serialize ``list[ForkPointInfo]`` to the Pi camelCase
    wire shape ``Array<{entryId, text}>`` (``agent-session.ts:2870``).

    Sprint 6h₄a (ADR-0075, P-295). Aelix introduces
    :class:`ForkPointInfo` to name Pi's inline anonymous return type;
    the wire shape stays Pi-byte-for-byte camelCase.
    """

    return [{"entryId": p.entry_id, "text": p.text} for p in points]


async def _handle_get_fork_messages(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandGetForkMessages``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:591-594`` ``get_fork_messages`` handler
    (delegates to ``agent-session.ts:2870-2885``
    ``session.getUserMessagesForForking``).

    Pi body: ``const messages = session.getUserMessagesForForking();
    return success(id, "get_fork_messages", { messages });`` — the
    response ``data`` wraps the camelCase array in ``{messages: [...]}``.
    """

    points = await harness.get_user_messages_for_forking()
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_fork_messages",
        data={"messages": _fork_points_to_dict(points)},
    )


async def _handle_get_last_assistant_text(
    harness: AgentHarness,
    cmd: Any,  # ``RpcCommandGetLastAssistantText``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:596-599`` ``get_last_assistant_text``
    handler (delegates to ``agent-session.ts:3059-3081``
    ``session.getLastAssistantText``).

    Pi body: ``const text = session.getLastAssistantText(); return
    success(id, "get_last_assistant_text", { text });`` — Pi's
    ``JSON.stringify`` drops ``text`` from the payload when the
    session method returns :data:`undefined`. P-298 SYNTHESIS: Aelix
    mirrors with ``data = {"text": text} if text is not None else {}``
    so the wire bytes match Pi's key-omission behaviour. This
    consistency with the existing :func:`_session_stats_to_dict`
    undefined-skip pattern is asserted by the Sprint 6h₄a closure pin.
    """

    text = harness.get_last_assistant_text()
    data: dict[str, Any] = {"text": text} if text is not None else {}
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_last_assistant_text",
        data=data,
    )


# === Sprint 6h₄c (ADR-0079, P-323~P-331) — 3 session-tree handlers ===========
#
# Pi parity:
#   - ``rpc-mode.ts:563-569`` → ``runtimeHost.switchSession(path)``
#     (``agent-session-runtime.ts:175-198``).
#   - ``rpc-mode.ts:571-577`` → ``runtimeHost.fork(entry_id)``
#     (``agent-session-runtime.ts:234-320``).
#   - ``rpc-mode.ts:579-589`` → ``runtimeHost.fork(leaf_id, position="at")``
#     (Pi clone is a fork-at-leaf).
#
# P-323 line drift: ADR-0076 estimated ``rpc-mode.ts:528-557``; ADR-0078's
# carry-forward roster estimated ``:566`` / ``:574`` / ``:586`` (3-line
# stub citations). W0 verification at SHA ``734e08e`` puts the actual case
# sites at ``:563-569`` / ``:571-577`` / ``:579-589``. ADR-0079 records the
# supersession.
#
# P-326: each handler takes ``(runtime_host, cmd)`` instead of
# ``(harness, cmd)`` — a NEW arity class
# :data:`_SUPPORTED_HANDLERS_RUNTIME_HOST` carries them; :func:`_bind_runtime_host`
# adapts the 2-arg shape so the dispatch table stays uniform.
#
# P-329 deliberate convergence: Aelix handlers DO NOT call rebind manually.
# The runtime's ``_finish_session_replacement`` auto-invokes the registered
# callback; Pi belt-and-braces handler-side ``await rebindSession()`` at
# ``rpc-mode.ts:566`` / ``:574`` / ``:586`` is NOT mirrored.


async def _handle_switch_session(
    runtime_host: AgentSessionRuntime,
    cmd: Any,  # ``RpcCommandSwitchSession``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:563-569`` (switch_session handler).

    Delegates to :meth:`AgentSessionRuntime.switch_session` which routes
    through ``repo.open(load_jsonl_session_metadata(fs, path))`` →
    ``_finish_session_replacement(new_session)`` (Pi
    ``agent-session-runtime.ts:175-198``). Returns the Pi wire shape
    ``{cancelled}`` (Pi line 568).

    P-329 deliberate convergence — this handler MUST NOT call
    ``rebind_session`` manually. The runtime's
    ``_finish_session_replacement`` auto-invokes the registered callback
    as single source of truth; Pi's belt-and-braces handler-side rebind
    at ``rpc-mode.ts:566`` is NOT mirrored.
    """

    # W4 MINOR-2 (Sprint 6h₄c W6): drop the inner blanket
    # ``except Exception`` catch — the outer :func:`_handle_command`
    # already wraps every exception into a Pi-shape error envelope
    # (``rpc_mode.py`` ``_handle_command`` ``except Exception`` arm).
    # The handler stays pure — :class:`SessionError` from ``repo.open``
    # propagates and reaches the wire as the same envelope.
    result = await runtime_host.switch_session(cmd.session_path)
    return RpcSuccessResponse(
        id=cmd.id,
        command="switch_session",
        data={"cancelled": result.cancelled},
    )


async def _handle_fork(
    runtime_host: AgentSessionRuntime,
    cmd: Any,  # ``RpcCommandFork``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:571-577`` (fork handler).

    Delegates to :meth:`AgentSessionRuntime.fork` which routes through
    ``repo.fork(...)`` over the harness's current session (Pi
    ``agent-session-runtime.ts:234-320``). Returns the Pi wire shape
    ``{text?, cancelled}`` (Pi line 576).

    P-327 wire shape — ``selectedText`` → ``text`` rename: Pi
    ``rpc-mode.ts:576`` returns ``success(id, "fork", {text:
    result.selectedText, cancelled: result.cancelled})``; the Pi wire
    key is ``text`` (NOT ``selectedText``). Aelix mirrors with
    key-omission per Sprint 6h₄a P-298: when ``result.selected_text``
    is :data:`None` the ``text`` key is omitted entirely (matches Pi
    ``JSON.stringify({text: undefined})`` → ``{cancelled: false}``).
    Pattern is identical to :func:`_handle_get_last_assistant_text`.

    P-329 deliberate convergence — this handler MUST NOT call
    ``rebind_session`` manually (see :func:`_handle_switch_session`).
    """

    # W4 MINOR-1 (Sprint 6h₄c W6): keep the ``except ValueError`` arm
    # (Pi-documented error path: "Invalid entry ID for forking" at Pi
    # ``agent-session-runtime.ts:247``); drop the redundant
    # ``except Exception`` since the outer :func:`_handle_command` already
    # wraps non-ValueError exceptions into a Pi-shape envelope.
    try:
        result = await runtime_host.fork(cmd.entry_id)
    except ValueError as exc:
        return RpcErrorResponse(
            id=cmd.id, command="fork", error=str(exc)
        )
    data: dict[str, Any] = {"cancelled": result.cancelled}
    if result.selected_text is not None:
        data["text"] = result.selected_text
    return RpcSuccessResponse(
        id=cmd.id, command="fork", data=data
    )


async def _handle_clone(
    runtime_host: AgentSessionRuntime,
    cmd: Any,  # ``RpcCommandClone``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:579-589`` (clone handler).

    Pi 3-step waveform:
      1. Capture ``leaf_id`` BEFORE entering the fork waveform (P-328 —
         OLD session is disposed during fork; leaf must be captured
         against the LIVE session). The capture order matches Pi line
         580 verbatim.
      2. Error envelope ``"Cannot clone session: no current entry
         selected"`` when ``leaf_id`` is :data:`None` (Pi parity
         ``:582``).
      3. Delegate to :meth:`AgentSessionRuntime.fork` with
         ``position="at"`` — Pi line 584. The ``position="at"``
         differentiates clone from fork: clone forks AT the current leaf
         (no user-message walk), fork walks BACK to the parent.
      4. Wire shape: ``{cancelled}`` — Pi DROPS ``selectedText`` for
         clone (Pi line 588 returns only ``{cancelled: result.cancelled}``).

    Aelix-divergence acknowledged: Pi ``Session.get_leaf_id()`` is sync;
    Aelix ``Session.get_leaf_id()`` is ``async def``. The ``await`` is
    necessary; pre-capture ordering is preserved because the ``await``
    resolves BEFORE the ``runtime_host.fork(...)`` call enters its
    replace waveform.

    P-329 deliberate convergence — this handler MUST NOT call
    ``rebind_session`` manually (see :func:`_handle_switch_session`).
    """

    session = runtime_host.session
    if session is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="clone",
            error="Cannot clone session: no current entry selected",
        )
    leaf_id = await session.get_leaf_id()
    if leaf_id is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="clone",
            error="Cannot clone session: no current entry selected",
        )
    # W4 MINOR-1 (Sprint 6h₄c W6): keep ``except ValueError`` (Pi-documented
    # error path); drop redundant ``except Exception`` per ADR-0079 — the
    # outer :func:`_handle_command` wraps any other exception into a
    # Pi-shape envelope.
    try:
        result = await runtime_host.fork(leaf_id, position="at")
    except ValueError as exc:
        return RpcErrorResponse(
            id=cmd.id, command="clone", error=str(exc)
        )
    # Pi line 588: clone DROPS selected_text from the wire (only
    # ``{cancelled}`` reaches the client).
    return RpcSuccessResponse(
        id=cmd.id,
        command="clone",
        data={"cancelled": result.cancelled},
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
            error=f"{cmd_type} not implemented ({owner_adr})",
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
    # Sprint 6h₄c (ADR-0079, P-330): ``new_session`` MOVED from
    # HARNESS_ONLY to :data:`_SUPPORTED_HANDLERS_RUNTIME_HOST` — the
    # handler now routes through :meth:`AgentSessionRuntime.new_session`
    # which persists ``parent_session`` lineage via
    # ``repo.create(parent_session_path=...)``. The Sprint 6d ADR-0058
    # carry-forward closes.
    "get_state": _handle_get_state,
    "get_messages": _handle_get_messages,
    "compact": _handle_compact,
    "bash": _handle_bash,
    "set_thinking_level": _handle_set_thinking_level,
    "set_session_name": _handle_set_session_name,
    # Sprint 6h₁ (ADR-0069, P-219): 2-arg handler — reads
    # ``harness.extension_runner`` / ``harness.prompt_templates`` /
    # ``harness.skills`` directly.
    "get_commands": _handle_get_commands,
    # Sprint 6h₂ (ADR-0071, P-245~P-253) — 9 new handlers; each a thin
    # wrapper around the corresponding harness method.
    "steer": _handle_steer,
    "follow_up": _handle_follow_up,
    "cycle_thinking_level": _handle_cycle_thinking_level,
    "set_steering_mode": _handle_set_steering_mode,
    "set_follow_up_mode": _handle_set_follow_up_mode,
    "set_auto_compaction": _handle_set_auto_compaction,
    "set_auto_retry": _handle_set_auto_retry,
    "abort_retry": _handle_abort_retry,
    "abort_bash": _handle_abort_bash,
    # Sprint 6h₃ (ADR-0073, P-268~P-274) — 2 session-inspection handlers.
    "get_session_stats": _handle_get_session_stats,
    "export_html": _handle_export_html,
    # Sprint 6h₄a (ADR-0075, P-293~P-298) — 2 session-navigation handlers.
    "get_fork_messages": _handle_get_fork_messages,
    "get_last_assistant_text": _handle_get_last_assistant_text,
}

_SUPPORTED_HANDLERS_HARNESS_REGISTRY: dict[
    str,
    Callable[[Any, Any, Any], Awaitable[RpcResponse]],
] = {
    "set_model": _handle_set_model,
    "cycle_model": _handle_cycle_model,
    "get_available_models": _handle_get_available_models,
}

# Sprint 6h₄c (ADR-0079, P-326): NEW arity class for handlers that take
# ``(runtime_host, cmd)`` instead of ``(harness, cmd)``. The 3 session-
# tree handlers (``switch_session`` / ``fork`` / ``clone``) operate on
# :class:`AgentSessionRuntime` directly; the 4th entry
# (``new_session``) MOVED here from HARNESS_ONLY per P-330 because it
# now routes through :meth:`AgentSessionRuntime.new_session` for
# ``parent_session`` lineage persistence.
_SUPPORTED_HANDLERS_RUNTIME_HOST: dict[
    str, Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]]
] = {
    "new_session": _handle_new_session,
    "switch_session": _handle_switch_session,
    "fork": _handle_fork,
    "clone": _handle_clone,
}

# Sprint 6f W2 (ADR-0065): legacy alias preserved for Sprint 6d tests
# that import :data:`_SUPPORTED_HANDLERS` — the alias points at the
# 2-arg subset so the existing assertions keep matching.
_SUPPORTED_HANDLERS: dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
] = _SUPPORTED_HANDLERS_HARNESS_ONLY


def build_dispatch_table(
    model_registry: ModelRegistry | None = None,
    *,
    runtime_host: AgentSessionRuntime | None = None,
) -> dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
]:
    """Build the full 29-command dispatch table.

    Sprint 6h₂ (ADR-0071): 22 supported + 7 deferred = 29 total.
    Sprint 6h₃ (ADR-0073): 24 supported + 5 deferred = 29 total —
    wires ``get_session_stats`` + ``export_html``.
    Sprint 6h₄a (ADR-0075 / ADR-0076): 26 supported + 3 deferred = 29
    total — wires ``get_fork_messages`` + ``get_last_assistant_text``.
    Sprint 6h₄c (ADR-0079 / ADR-0080): **29 supported + 0 deferred =
    29 total — PHASE 4 CLOSURE.** Wires ``switch_session`` / ``fork`` /
    ``clone`` via the new :data:`_SUPPORTED_HANDLERS_RUNTIME_HOST`
    arity class. The ``runtime_host`` argument carries the
    :class:`AgentSessionRuntime` closed over by
    :func:`_bind_runtime_host` for each of the 4 runtime-host handlers
    (``new_session`` MOVED from HARNESS_ONLY per P-330).
    Returned fresh on every call so tests can introspect without
    leaking state. The supported handlers are real callables; the
    deferred ones are :func:`_make_deferred_handler` closures.

    The ``model_registry`` argument is threaded into the 3 new
    Sprint 6f handlers (``set_model`` / ``cycle_model`` /
    ``get_available_models``). When :data:`None`, the 3 model
    handlers return a :class:`RpcErrorResponse` reporting the missing
    registry (Pi parity: ``rpc-mode.ts`` requires a ModelRegistry to
    be bound to the runtime host).

    The ``runtime_host`` argument is **optional** for back-compat with
    Sprint 6d / 6h₄a test paths that only exercise the HARNESS_ONLY +
    HARNESS_REGISTRY subsets. When :data:`None`, the 4 runtime-host
    handlers are routed through a stub that returns an
    :class:`RpcErrorResponse` reporting the missing runtime — production
    callers (`:func:`run_rpc_mode`) always supply one.
    """

    table: dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]] = dict(
        _SUPPORTED_HANDLERS_HARNESS_ONLY
    )
    # Adapt the 3-arg handlers to the dispatch table's 2-arg shape by
    # closing over ``model_registry``.
    for cmd_type, three_arg in _SUPPORTED_HANDLERS_HARNESS_REGISTRY.items():
        table[cmd_type] = _bind_registry(three_arg, model_registry)
    # Sprint 6h₄c (P-326): adapt the runtime-host handlers to the
    # dispatch table's 2-arg shape by closing over ``runtime_host``. When
    # ``runtime_host`` is :data:`None`, install a "missing runtime"
    # stub so test callers that don't construct a runtime see a Pi-
    # shape error envelope rather than crash on attribute access.
    for cmd_type, runtime_handler in _SUPPORTED_HANDLERS_RUNTIME_HOST.items():
        if runtime_host is None:
            table[cmd_type] = _make_missing_runtime_handler(cmd_type)
        else:
            table[cmd_type] = _bind_runtime_host(
                runtime_handler, runtime_host
            )
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


def _bind_runtime_host(
    handler: Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]],
    runtime_host: AgentSessionRuntime,
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Sprint 6h₄c (ADR-0079, P-326): adapt a 2-arg
    ``(runtime_host, cmd)`` handler to the dispatch table's 2-arg
    ``(harness, cmd)`` shape by closing over ``runtime_host``. The
    ``harness`` positional is ignored (Pi parity — these handlers
    operate on the runtime, not the harness directly).
    """

    async def _adapted(_harness: Any, cmd: Any) -> RpcResponse:
        return await handler(runtime_host, cmd)

    return _adapted


def _make_missing_runtime_handler(
    cmd_type: str,
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Sprint 6h₄c (ADR-0079, P-326): produce a Pi-shape error stub for
    runtime-host handlers when :func:`build_dispatch_table` is called
    without an explicit ``runtime_host``. Tests that only exercise the
    HARNESS_ONLY / HARNESS_REGISTRY subsets keep working unchanged.
    """

    async def _handler(_harness: Any, cmd: Any) -> RpcResponse:
        return RpcErrorResponse(
            id=getattr(cmd, "id", None),
            command=cmd_type,
            error=(
                f"{cmd_type} requires an AgentSessionRuntime — none "
                "configured (pass ``runtime_host=`` to build_dispatch_table)"
            ),
        )

    return _handler


# === Sprint 6h₄b (ADR-0077, P-309) — passthrough runtime shim ================


def _make_passthrough_runtime(
    harness: AgentHarness,
    harness_factory: HarnessFactory | None,
    *,
    repo: JsonlSessionRepo | None = None,
    fs: FileSystem | None = None,
) -> AgentSessionRuntime:
    """Construct a no-replace :class:`AgentSessionRuntime` wrapping the
    passed harness. Used by :func:`run_rpc_mode` when caller passes no
    explicit ``runtime_host`` so the 29 wired handlers keep working
    without API breakage (P-309 / Sprint 6h₄c P-324).

    When ``harness_factory is None`` a closure that RAISES on invocation
    is installed (W4 LOW-3) — calling any of the 4 still-stubbed replace
    APIs from 6h₄b still raises :class:`NotImplementedError`, so the
    raising factory is unreachable from production paths. The explicit
    raise (vs. returning the original harness) makes accidental misuse
    in tests / 6h₄c integration paths fail loudly instead of silently
    re-binding to the same stale harness.

    Sprint 6h₄c (ADR-0079, P-324): accepts optional ``repo`` + ``fs``
    keyword parameters which are threaded into
    :class:`AgentSessionRuntime`. Defaults: :class:`LocalFileSystem` +
    :class:`JsonlSessionRepo(fs=LocalFileSystem())` mirror Pi's default
    cwd-rooted session root.

    Pi parity citations:
      - Constructor mirrors Pi ``agent-session-runtime.ts:67-74``.
      - Replace path is the private seam (``:149-173``); no public
        replace API is invoked from the passthrough.
    """

    if harness_factory is None:
        async def _noop_factory(_new_session: Any) -> AgentHarness:
            raise RuntimeError(
                "Passthrough runtime cannot replace harness — caller "
                "must pass an explicit harness_factory to run_rpc_mode"
            )
        harness_factory = _noop_factory
    effective_fs = fs if fs is not None else LocalFileSystem()
    effective_repo = (
        repo if repo is not None else JsonlSessionRepo(fs=effective_fs)
    )
    return AgentSessionRuntime(
        harness, harness_factory, repo=effective_repo, fs=effective_fs
    )


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
    runtime_host: AgentSessionRuntime | None = None,
    harness_factory: HarnessFactory | None = None,
    repo: JsonlSessionRepo | None = None,
    fs: FileSystem | None = None,
    stdin: asyncio.StreamReader | None = None,
    stdout_write: Callable[[bytes], None] | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Pi parity: ``rpc-mode.ts:runRpcMode`` (lines 50-492).

    Hijacks ``sys.stdout`` (redirects to ``sys.stderr`` so tool ``print``
    output stays out of the JSONL stream — Pi ``takeOverStdout``), attaches
    a JSONL line reader to stdin, subscribes to harness session events,
    and dispatches commands until EOF or a shutdown signal.

    Sprint 6h₄b (ADR-0077, P-309): the signature accepts an optional
    ``runtime_host`` (:class:`AgentSessionRuntime`). When :data:`None`,
    a no-replace passthrough runtime is constructed via
    :func:`_make_passthrough_runtime` so the 26 existing handlers keep
    working without API breakage. The ``rebind_session`` closure
    (mirror of Pi ``rpc-mode.ts:310-349``) is installed on the runtime
    so a future replace operation refreshes the event-pipe subscription
    against the NEW harness.

    Args:
        harness: Active :class:`AgentHarness` whose surface backs the
            wired RPC handlers. When ``runtime_host`` is supplied the
            ``runtime_host.harness`` value is the LIVE handler target
            (the captured reference is refreshed on rebind).
        model_registry: Optional :class:`ModelRegistry` backing the 3
            Sprint 6f model commands (``set_model`` / ``cycle_model`` /
            ``get_available_models``). When :data:`None` the 3 handlers
            return a "no registry configured" error (Pi parity:
            ModelRegistry is bound to the runtime host).
        runtime_host: Optional :class:`AgentSessionRuntime` (Sprint
            6h₄b ADR-0077). When :data:`None`, a passthrough runtime
            wraps the passed ``harness`` (P-309 back-compat shim).
        harness_factory: Optional :class:`HarnessFactory` used by the
            passthrough runtime. When :data:`None` (and
            ``runtime_host`` is also :data:`None`), a no-op factory
            returns the same harness — the 4 still-stubbed replace
            APIs from 6h₄b still raise :class:`NotImplementedError`.
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

    shutdown_event = asyncio.Event()

    # === Sprint 6h₄b (ADR-0077, P-309): runtime host construction =============
    # When the caller passes no explicit ``runtime_host``, construct a
    # passthrough that wraps the supplied ``harness`` so all 29 wired
    # handlers keep working without API breakage. When the caller DOES
    # pass a ``runtime_host``, ignore the loose ``harness`` for the
    # event-pipe seam — the LIVE harness is ``runtime_host.harness``
    # (refreshed by the rebind closure below per Pi `rpc-mode.ts:310-349`).
    #
    # Sprint 6h₄c (ADR-0079, P-324): when ``runtime_host`` is explicit,
    # caller MUST NOT supply ``repo`` / ``fs`` (they're owned by the
    # runtime). When ``runtime_host`` is None, the passthrough threads
    # ``repo`` / ``fs`` (defaulting to LocalFileSystem +
    # JsonlSessionRepo) into the constructed AgentSessionRuntime.
    if runtime_host is None:
        runtime_host = _make_passthrough_runtime(
            harness, harness_factory, repo=repo, fs=fs
        )
    elif repo is not None or fs is not None:
        raise RuntimeError(
            "repo and fs must not be supplied when runtime_host is "
            "explicit — the runtime owns them"
        )

    # Sprint 6h₄c (ADR-0079, P-326): the dispatch table now requires
    # ``runtime_host`` for the 4 runtime-host handlers (new_session +
    # switch_session + fork + clone).
    dispatch = build_dispatch_table(
        model_registry, runtime_host=runtime_host
    )

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

    # === Sprint 6h₄b (ADR-0077, P-303) — ``rebind_session`` closure =========
    # Pi parity: ``rpc-mode.ts:310-349``. Pi closes over outer ``let
    # session`` + ``let unsubscribe`` variables; Python closures can read
    # but not rebind enclosing names, so we attach them to a lightweight
    # container (``_Capture``) that both the rebind closure and the
    # dispatch loop read through.
    #
    # Sprint 6h₄b ships the FOUNDATION subset: reassign capture + tear
    # down old subscription + re-subscribe to the new harness's
    # ``subscribe()``. The Pi ``bindExtensions`` /
    # ``commandContextActions`` waveform (Pi ``:315-345``) is NOT wired
    # in 6h₄b — Aelix's ``_runtime.bind_core`` already ran during the
    # NEW harness's ``__init__`` (P-302). Sprint 6h₄c wires the
    # explicit action surface when the 3 DEFERRED RPC handlers move.

    class _Capture:
        """6h₄b — mutable cell for the ``rebind_session`` closure (P-303).

        Pi closes over outer ``let session`` + ``let unsubscribe``
        variables (``rpc-mode.ts:310-349``); Python closures can read
        but not rebind enclosing names, so we attach them to a
        lightweight container.
        """

        harness: AgentHarness
        unsubscribe: Callable[[], None]

    capture = _Capture()
    capture.harness = runtime_host.harness
    capture.unsubscribe = capture.harness.subscribe(_on_agent_event)

    async def rebind_session(new_harness: AgentHarness) -> None:
        """Pi parity: ``rebindSession`` closure (``rpc-mode.ts:310-349``).

        Sprint 6h₄b — FOUNDATION subset (P-303). Reassigns the captured
        harness, tears down the previous subscription, and re-subscribes
        to the new harness's event stream. The ``bindExtensions`` /
        ``commandContextActions`` waveform (Pi ``:315-345``) is NOT
        wired in 6h₄b — Aelix's ``_runtime.bind_core`` already ran
        during the NEW harness's ``__init__`` (P-302). Sprint 6h₄c
        wires the explicit action surface.
        """
        capture.harness = new_harness
        capture.unsubscribe()
        capture.unsubscribe = capture.harness.subscribe(_on_agent_event)

    # Pi parity: ``rpc-mode.ts:306-308`` registration site.
    runtime_host.set_rebind_session(rebind_session)

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
            # Sprint 6h₄b (P-309): read the LIVE harness through the
            # capture cell so dispatch always sees the most recent
            # rebind result. When no replace has occurred,
            # ``capture.harness`` is identical to the original ``harness``
            # argument — zero behavior change for the 26 wired handlers.
            response = await _handle_command(
                capture.harness, payload, dispatch
            )
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
        # Sprint 6h₄b (P-303): the LIVE unsubscribe handle lives on the
        # capture cell — rebinds replace it each time.
        capture.unsubscribe()
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
        # Pi parity: ``await runtimeHost.dispose()`` (Pi
        # ``rpc-mode.ts`` end-of-run teardown). Sprint 6h₄b routes
        # through :meth:`AgentSessionRuntime.dispose` which fires
        # ``beforeSessionInvalidate`` then disposes the LIVE harness.
        with contextlib.suppress(Exception):
            await runtime_host.dispose()
        if own_stdin:
            # asyncio's pipe transport is closed when the reader hits EOF;
            # nothing to actively pause on the Python side.
            pass


__all__ = [
    "DEFERRED_COMMANDS",
    "SUPPORTED_COMMANDS",
    "_make_passthrough_runtime",
    "build_dispatch_table",
    "run_rpc_mode",
]
