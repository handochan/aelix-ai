"""Subprocess hook dispatch lane (Tier 4b, Sprint 6h₉e, ADR-0102).

**Nature — Aelix-additive.** Pi has *no* subprocess hook lane in core at the
pinned SHA ``734e08e``; this module imports **zero** Pi behavior. The reference
standard is **Claude Code's documented hook system** (code.claude.com/docs/en/
hooks), NOT Pi. The in-process :class:`~aelix_agent_core.harness.hooks.HookBus`
reducer semantics (which ARE Pi-parity, ADR-0017) are untouched.

The subprocess lane is a *second, separate lane* layered on top of the existing
in-process hook bus via a normal ``api.on(...)`` registration (wired by
``loader.py``). Each declared ``[[contributes.hooks]]`` manifest entry registers
an in-process handler that, when its event fires, spawns the declared shell
command, passes a Claude-Code-style JSON envelope on stdin, and maps the
command's stdout-JSON / stderr / exit-code back to the matching Aelix hook
result type.

Wire-protocol fidelity (CC parity — load-bearing casing):

- stdin envelope keys are **snake_case** (we *write* them):
  ``hook_event_name``, ``session_id``, ``cwd``, ``tool_name``, ``tool_use_id``,
  ``tool_input``, ``is_error``, ``prompt``, ``source``, ...
- stdout control JSON keys are **camelCase** (we *read* them):
  ``continue``, ``decision``, ``reason``, ``hookSpecificOutput``,
  ``permissionDecision``, ``permissionDecisionReason``.

Exit-code semantics (CC parity):

- ``0`` → parse stdout JSON for control.
- ``2`` → blocking (stdout ignored, stderr fed back as the block reason; only
  actionable on a ``tool_call`` event in v1).
- other non-zero → non-blocking error (logged, execution continues = fail-open).

**Fail-open rule (matches CC):** spawn failure, timeout, invalid JSON, and
non-{0,2} exit codes are all non-blocking (return ``None`` = allow). The ONLY
fail-closed paths are explicit ``exit 2`` or ``permissionDecision: "deny"`` /
``decision: "block"`` on a ``tool_call`` event. The handler NEVER raises.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aelix_agent_core.contracts import HookContrib
from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    HookEvent,
    HookHandler,
    ToolCallResult,
)

from aelix_coding_agent.extensions.loader import ExtensionManifestError

if TYPE_CHECKING:
    from aelix_coding_agent.extensions.api import ExtensionContext

_log = logging.getLogger(__name__)


# Captured stdout is capped before JSON parse, aligning with Claude Code's
# documented ~10k hook-output limit (an Aelix-applied safety bound).
_STDOUT_CAP = 10_000

# Exit code returned by :func:`run_hook_subprocess` on timeout (shell ``timeout``
# convention).
_TIMEOUT_EXIT_CODE = 124


class SubprocessHookError(Exception):
    """Internal spawn-failure signal — never escapes a handler (fail-open)."""


@dataclass(frozen=True)
class HookSubprocessOutcome:
    """The captured result of one subprocess hook invocation."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


# === Event allowlist ===

# The subset of the 35 ADR-0017 events a subprocess hook may bind to. This both
# validates ``HookContrib.event`` AND prevents a performance footgun (binding a
# subprocess to a high-frequency streaming event like ``message_update`` would
# spawn a process per update). v1 set = clean Claude-Code analogs.
SUBPROCESS_HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "before_agent_start",  # ~ UserPromptSubmit (run start)
        "input",  # ~ UserPromptSubmit (raw input)
        "tool_call",  # ~ PreToolUse   (ONLY actionable/blockable in v1)
        "tool_result",  # ~ PostToolUse  (observational v1)
        "user_bash",  # Aelix `!` bash (observational)
        "session_start",  # ~ SessionStart (observational)
        "session_shutdown",  # ~ SessionEnd   (observational)
        "agent_end",  # ~ Stop         (observational)
    }
)

# Cross-check invariant: every allowlisted event must be a real registered hook
# event. Membership in HOOK_RESULT_TYPES is necessary but not sufficient — the
# event must ALSO be in SUBPROCESS_HOOK_EVENTS. Verified at import (and by test).
assert set(HOOK_RESULT_TYPES) >= SUBPROCESS_HOOK_EVENTS, (
    "SUBPROCESS_HOOK_EVENTS contains an event not registered in HOOK_RESULT_TYPES"
)


# === Spawn core ===


async def run_hook_subprocess(
    command: str,
    payload: str,
    *,
    timeout_ms: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> HookSubprocessOutcome:
    """Spawn ``command`` (shell form), write ``payload`` to stdin, capture output.

    Shell form matches CC's ``sh -c "<command>"`` shell-form hooks (single
    command string, no args field). Does NOT raise on non-zero exit — returns
    the outcome. MAY raise :class:`SubprocessHookError` only on a genuine spawn
    failure (e.g. ``OSError`` from ``create_subprocess_shell``); the caller
    catches it and fails open.

    On timeout: teardown via the rpc_client pattern — ``terminate()`` →
    bounded ``wait()`` → ``kill()`` → bounded ``wait()`` — and return an
    outcome with ``timed_out=True`` / ``exit_code=124``.
    """

    # env: full inherited environment + caller overrides. AELIX_PROJECT_DIR is
    # an *Aelix-additive* convenience (CC analog of $CLAUDE_PROJECT_DIR); it is
    # NOT a Pi/CC import — documented as additive in ADR-0102.
    proc_env: dict[str, str] = dict(os.environ)
    if cwd is not None:
        proc_env["AELIX_PROJECT_DIR"] = cwd
    if env is not None:
        proc_env.update(env)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=proc_env,
        )
    except OSError as exc:
        raise SubprocessHookError(
            f"failed to spawn subprocess hook {command!r}: {exc}"
        ) from exc

    timeout_s = timeout_ms / 1000.0
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=payload.encode()),
            timeout=timeout_s,
        )
    except TimeoutError:
        # Teardown (model: rpc_client.py stop()): terminate → bounded wait →
        # kill → bounded wait, suppressing ProcessLookupError / TimeoutError.
        # The child is reliably reaped here. A PytestUnraisableExceptionWarning:
        # "Event loop is closed" may appear in tests — that is the known CPython
        # asyncio subprocess transport __del__ artifact (OS pipes close when the
        # process dies), NOT a resource leak.
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
        return HookSubprocessOutcome(
            exit_code=_TIMEOUT_EXIT_CODE,
            stdout="",
            stderr=f"<hook timed out after {timeout_ms}ms>",
            timed_out=True,
        )

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if len(stdout) > _STDOUT_CAP:
        _log.debug(
            "subprocess hook stdout truncated from %d to %d chars",
            len(stdout),
            _STDOUT_CAP,
        )
        stdout = stdout[:_STDOUT_CAP]

    # proc.returncode is set after communicate() completes.
    return HookSubprocessOutcome(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
    )


# === Serialization (stdin envelope — snake_case) ===


def _best_effort_session_id(ctx: ExtensionContext) -> str:
    """Return a best-effort session id, or ``""`` when none is reachable.

    ``ExtensionContext`` exposes no clean session-id surface in Phase 5b; the
    ``session_manager`` Protocol only offers ``get_session()``. We probe it
    defensively (the property raises when no session is attached) and fall
    back to the session's ``session_file`` or ``""`` (spec allows best-effort).
    """

    try:
        sm = ctx.session_manager
    except Exception:  # noqa: BLE001 — best-effort; no session attached
        return ""
    try:
        session = sm.get_session()
    except Exception:  # noqa: BLE001 — best-effort
        return ""
    if session is None:
        return ""
    session_file = getattr(session, "session_file", None)
    return session_file if isinstance(session_file, str) else ""


def serialize_hook_event(event: HookEvent, ctx: ExtensionContext) -> dict[str, Any]:
    """Build the snake_case stdin envelope for ``event`` (CC parity).

    Common keys always present: ``hook_event_name``, ``cwd``, ``session_id``.
    Event-specific extras are added per ``event.type`` (NOT ``isinstance`` —
    the 7 typed ``*ToolCallHookEvent`` variants + ``CustomToolCallHookEvent``
    all carry ``type == "tool_call"`` and route through the ``tool_call``
    branch).
    """

    event_type = getattr(event, "type", "")
    payload: dict[str, Any] = {
        "hook_event_name": event_type,
        "cwd": ctx.cwd,
        "session_id": _best_effort_session_id(ctx),
    }

    if event_type == "tool_call":
        payload["tool_name"] = getattr(event, "tool_name", "")
        payload["tool_use_id"] = getattr(event, "tool_call_id", "")
        payload["tool_input"] = getattr(event, "args", {})
    elif event_type == "tool_result":
        payload["tool_name"] = getattr(event, "tool_name", "")
        payload["tool_use_id"] = getattr(event, "tool_call_id", "")
        payload["tool_input"] = getattr(event, "args", {})
        payload["is_error"] = getattr(event, "is_error", False)
    elif event_type == "input":
        payload["prompt"] = getattr(event, "text", "")
        payload["source"] = getattr(event, "source", "")
    elif event_type == "user_bash":
        payload["command"] = getattr(event, "command", "")
        payload["cwd"] = getattr(event, "cwd", "") or ctx.cwd
        payload["exclude_from_context"] = getattr(event, "exclude_from_context", False)
    elif event_type == "session_start":
        payload["reason"] = getattr(event, "reason", "")
        payload["previous_session_file"] = getattr(event, "previous_session_file", None)
    elif event_type == "session_shutdown":
        payload["reason"] = getattr(event, "reason", "")
        payload["target_session_file"] = getattr(event, "target_session_file", None)
    elif event_type == "before_agent_start":
        payload["prompt"] = getattr(event, "prompt", "")
        payload["system_prompt"] = getattr(event, "system_prompt", "")
    # agent_end → common keys only.

    return payload


# === Output parsing (stdout control JSON — camelCase) ===


def parse_hook_output(event_type: str, outcome: HookSubprocessOutcome) -> Any:
    """Map a subprocess outcome to an Aelix hook result, or ``None``.

    Returns the Aelix hook result object for the event (only
    :class:`ToolCallResult` in v1), or ``None`` (no opinion / observational /
    fail-open). Fail-open is the default for everything except an explicit
    ``exit 2`` or a ``permissionDecision: "deny"`` / ``decision: "block"`` on
    a ``tool_call`` event.
    """

    # 1. Timeout → fail-open.
    if outcome.timed_out:
        _log.warning("subprocess hook timed out (fail-open): %s", outcome.stderr)
        return None

    # 2. Exit 2 → blocking.
    if outcome.exit_code == 2:
        if event_type == "tool_call":
            return ToolCallResult(
                block=True,
                reason=outcome.stderr.strip() or "blocked by subprocess hook",
            )
        _log.info(
            "subprocess hook requested block on non-blockable event %s", event_type
        )
        return None

    # 3. Exit 0 → parse stdout control JSON.
    if outcome.exit_code == 0:
        stdout = outcome.stdout.strip()
        if not stdout:
            return None
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            _log.debug("subprocess hook emitted invalid JSON (fail-open)")
            return None
        # Defensive: hook may print non-dict JSON (e.g. "true").
        if not isinstance(data, dict):
            return None
        if event_type == "tool_call":
            hook_specific = data.get("hookSpecificOutput")
            if not isinstance(hook_specific, dict):
                hook_specific = {}
            deny = (
                hook_specific.get("permissionDecision") == "deny"
                or data.get("decision") == "block"
            )
            if deny:
                reason = (
                    hook_specific.get("permissionDecisionReason")
                    or data.get("reason")
                    or "denied by subprocess hook"
                )
                return ToolCallResult(block=True, reason=reason)
            # allow / ask / no-opinion are all observational in v1.
            return None
        # Other allowlisted events → observational in v1.
        return None

    # 4. Any other exit code → non-blocking error (fail-open).
    stripped_stderr = outcome.stderr.strip()
    first_stderr_line = stripped_stderr.splitlines()[0] if stripped_stderr else ""
    _log.info(
        "subprocess hook exited %d (non-blocking, fail-open): %s",
        outcome.exit_code,
        first_stderr_line,
    )
    return None


# === Handler factory ===


def make_subprocess_handler(contrib: HookContrib) -> HookHandler:
    """Build an in-process :class:`HookHandler` that dispatches to ``contrib``.

    The handler NEVER raises — fail-open is the contract (belt-and-suspenders
    with the ``error_mode="continue"`` registration in ``loader.py``).
    """

    async def _handler(event: HookEvent, ctx: ExtensionContext) -> Any:
        try:
            payload = json.dumps(serialize_hook_event(event, ctx), default=str)
            outcome = await run_hook_subprocess(
                contrib.command,
                payload,
                timeout_ms=contrib.timeout_ms,
                cwd=ctx.cwd,
            )
            return parse_hook_output(getattr(event, "type", ""), outcome)
        except Exception as exc:  # noqa: BLE001 — fail-open lane
            _log.warning(
                "subprocess hook %r failed (fail-open): %r", contrib.command, exc
            )
            return None

    return _handler


# === Validation helper ===


def validate_subprocess_hook_event(event: str) -> None:
    """Raise :class:`ExtensionManifestError` if ``event`` is not bindable.

    - Unknown to ``HOOK_RESULT_TYPES`` → "unknown hook event".
    - Known but not in :data:`SUBPROCESS_HOOK_EVENTS` → lists the allowed set.
    """

    if event not in HOOK_RESULT_TYPES:
        raise ExtensionManifestError(f"unknown hook event {event!r}")
    if event not in SUBPROCESS_HOOK_EVENTS:
        allowed = ", ".join(sorted(SUBPROCESS_HOOK_EVENTS))
        raise ExtensionManifestError(
            f"hook event {event!r} is not subprocess-bindable; "
            f"allowed subprocess hook events are: {allowed}"
        )


__all__ = [
    "SUBPROCESS_HOOK_EVENTS",
    "HookSubprocessOutcome",
    "SubprocessHookError",
    "make_subprocess_handler",
    "parse_hook_output",
    "run_hook_subprocess",
    "serialize_hook_event",
    "validate_subprocess_hook_event",
]
