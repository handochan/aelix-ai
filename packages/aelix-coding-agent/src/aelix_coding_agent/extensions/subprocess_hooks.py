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
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _retained_handle,
    containment_spawn_kwargs,
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

    The child is spawned into a tree (:class:`aelix_ai.utils._process_tree.ProcessTree`,
    #202 / ADR-0238): a process group of its own on POSIX, a Job Object on
    Windows. Before #202 the timeout path signalled only the shell, so
    ``sh -c "sleep 6 | cat"`` left ``sleep`` and ``cat`` running and on Windows
    the command ``cmd.exe /c`` had launched was orphaned every time. On timeout
    the teardown is soft (group ``SIGTERM`` / ``CTRL_BREAK_EVENT``) → bounded
    ``wait()`` → hard (``killpg(SIGKILL)`` / ``taskkill /T /F`` + the job) →
    bounded ``wait()``, returning ``timed_out=True`` / ``exit_code=124``. A soft
    signal ``soft_kill`` could not SEND skips the grace outright — there is
    nothing to wait for — and goes straight to the hard rung.

    Cancellation anywhere in that ladder — in ``communicate()`` OR in either of
    the teardown's waits — hard-kills the tree before the
    :class:`asyncio.CancelledError` propagates; ``close()`` in the ``finally``
    is a release and signals nothing on POSIX.

    The tree is attached with ``kill_on_close=False``: a hook is allowed to
    background a helper and exit 0, and ``close()`` must not take that away
    (revision 1 of the spec did, measured).
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
            # process_group=0 on POSIX (a new group in the SAME session, so a
            # hook that wants the terminal keeps it), CREATE_NEW_PROCESS_GROUP
            # on Windows. Both give the teardown below something to aim at.
            **containment_spawn_kwargs(),
        )
    except OSError as exc:
        raise SubprocessHookError(
            f"failed to spawn subprocess hook {command!r}: {exc}"
        ) from exc

    # Attach BEFORE the first await: on win32 the job is assigned here and only
    # descendants created after the assignment inherit membership.
    tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc))

    timeout_s = timeout_ms / 1000.0
    try:
        try:
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(input=payload.encode()),
                    timeout=timeout_s,
                )
            except TimeoutError:
                # Teardown: soft to the whole tree → bounded wait → hard. A
                # PytestUnraisableExceptionWarning: "Event loop is closed" may
                # appear in tests — the known CPython asyncio subprocess
                # transport __del__ artifact (OS pipes close when the process
                # dies), NOT a leak. Worst case on win32 is a cmd.exe batch job
                # answering CTRL_BREAK with its Y/N prompt and burning the whole
                # 1.0 s grace: ~0.2 + 1.0 + the kill for
                # test_run_subprocess_timeout, still inside its < 2.0 s bound.
                escalate = False
                if proc.returncode is None:
                    # The shell forwards nothing to its pipeline, so signal the
                    # group rather than the shell (#202).
                    #
                    # ``returncode is None`` does NOT prove the pid is still
                    # unreaped: asyncio's child watcher calls ``waitpid`` on its
                    # own thread and a LATER loop callback copies the status into
                    # ``returncode`` (measured — review posix2/F4). The honest
                    # bound on a stale number here is that callback latency plus
                    # the grace below, not "the pid cannot have been reused".
                    # It is still safe: a NON-EMPTY process group's id cannot be
                    # recycled while any member lives (POSIX §3.293; Linux pins
                    # it through ``attach_pid(PIDTYPE_PGID)``), and an empty one
                    # has nothing to kill (``killpg`` → ``ESRCH``). The residual
                    # case — the number recycled AND its new holder having made
                    # itself a group leader, inside that window — is accepted and
                    # stated in ADR-0238.
                    #
                    # ``soft_kill`` reports whether the signal was SENT. ``False``
                    # means nothing reached the tree (no attached console on
                    # win32, ``ESRCH`` on POSIX), so the grace below would buy
                    # nothing: skip it and escalate at once (review win-leg/F2).
                    escalate = not tree.soft_kill(whole_group=True)
                if not escalate:
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except TimeoutError:
                        escalate = True
                if escalate:
                    tree.hard_kill()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                return HookSubprocessOutcome(
                    exit_code=_TIMEOUT_EXIT_CODE,
                    stdout="",
                    stderr=f"<hook timed out after {timeout_ms}ms>",
                    timed_out=True,
                )
        except asyncio.CancelledError:
            # A cancelled hook must not leave its tree behind — and the
            # cancellation can arrive anywhere in the ladder above, not only in
            # ``communicate()``. This handler sits OUTSIDE the timeout teardown
            # for that reason: with it nested beside the teardown, a cancel that
            # landed during the 1.0 s grace or the 5.0 s hard wait escaped it and
            # left the whole hook tree running (measured — review posix2/F6),
            # because ``finally`` only calls ``close()``, which is a release and
            # signals nothing on POSIX. Nothing waits on the child after this, so
            # there is no grace to spend: kill hard and let the cancellation
            # continue.
            tree.hard_kill()
            raise

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
    finally:
        # Release, never a kill: on POSIX close() signals nothing, and the tree
        # was attached with kill_on_close=False so on win32 it does not either.
        tree.close()


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
