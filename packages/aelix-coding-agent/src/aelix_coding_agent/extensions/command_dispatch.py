"""CommandDispatchService — the single execution authority for extension
slash-commands (issue #9).

Pi executes extension commands centrally (``AgentSession.prompt`` →
``_tryExecuteExtensionCommand``) so every surface — interactive TUI, ``--mode
rpc``, print mode — runs the SAME dispatch semantics. Aelix's harness is
deliberately slash-unaware, so this service is the coding-agent-level equivalent:
the lowest common ancestor of the TUI input loop, ``rpc_mode``, and ``print_mode``
(all already hold the runtime host + ``harness.extension_runner`` + a bound UI).

Phase 1 (this change) wires the TUI. Phase 2 adds an additive RPC ``run_command``
that calls the same service with RPC bindings; print mode follows with no-op
bindings. Keeping ALL of split / lookup / context-construction / error-routing /
tri-state here is what guarantees the surfaces can never drift.

Pi semantics replicated (``agent-session.ts:_tryExecuteExtensionCommand``):

- name/args split on the FIRST space; ``args`` is the RAW remainder (not trimmed).
- handler is ``async (args: str, ctx: ExtensionCommandContext) -> None``; the
  return value is IGNORED by pi. Aelix adds a compatibility shim: a non-empty
  ``str`` return is rendered to the surface (the shipped ``echo`` example returns
  a greeting string), via the surface's ``emit_text`` — NOT ``ctx.ui.notify``,
  which is a 3-second transient toast and would flash a command's output away.
- a thrown handler is CAUGHT, reported via ``emit_error``, and STILL counts as
  HANDLED — it never falls through to the model.
- a lookup MISS returns ``NOT_A_COMMAND`` so the caller falls through to the
  model / built-in "unknown command" path. Built-ins are matched by the caller
  BEFORE this service, so built-ins win on a name collision (pi parity).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

# A handler's str return is a convenience, not a data channel — cap what we
# render so a pathological multi-MB return can't blow up the scrollback.
_MAX_RENDERED_RETURN_CHARS = 100_000


class DispatchOutcome(Enum):
    """Tri-state result of :meth:`CommandDispatchService.try_execute`.

    ``HANDLED`` / ``ERROR`` both mean "this was an extension command; do NOT
    fall through to the model". ``NOT_A_COMMAND`` means "not ours — fall through".
    """

    HANDLED = "handled"
    NOT_A_COMMAND = "not_a_command"
    ERROR = "error"


@dataclass(frozen=True)
class DispatchResult:
    outcome: DispatchOutcome
    command: str | None = None


@dataclass(frozen=True)
class CommandSurfaceBindings:
    """Per-surface output sinks (issue #9).

    The dispatch LOGIC is surface-agnostic; only these two sinks differ between
    the TUI (commit to scrollback / red line), RPC (text / error events), and
    print mode (stdout / stderr). ``ctx.ui.*`` is the OTHER, handler-driven
    channel and is bound separately via the runtime's ``bind_ui``.
    """

    emit_text: Callable[[str], None]
    emit_error: Callable[[str], None]


def _split_command(text: str) -> tuple[str, str]:
    """Pi name/args split: drop the leading ``/``, split on the FIRST space,
    keep ``args`` as the RAW remainder (pi ``slice(spaceIndex + 1)``)."""

    body = text[1:] if text.startswith("/") else text
    space = body.find(" ")
    if space == -1:
        return body, ""
    return body[:space], body[space + 1 :]


class CommandDispatchService:
    """Surface-agnostic executor for extension-registered slash commands.

    :param harness_provider: returns the LIVE harness on every call so the
        service survives session hot-swaps (``/resume`` / ``/new`` / ``/fork``
        rebind the harness; mirror the TUI's ``_rebind``).
    :param repo: optional :class:`JsonlSessionRepo` for the command context's
        ``fork`` fallback when no session runtime is bound.
    :param session_runtime: optional :class:`AgentSessionRuntime` powering the
        command context's ``new_session`` / ``fork`` / ``switch_session``.
    """

    def __init__(
        self,
        harness_provider: Callable[[], Any],
        *,
        repo: Any | None = None,
        session_runtime: Any | None = None,
    ) -> None:
        self._harness_provider = harness_provider
        self._repo = repo
        self._session_runtime = session_runtime

    def list_commands(self) -> list[tuple[str, str]]:
        """``(invocation_name, description)`` for autocomplete. Read live so a
        ``/reload`` or session swap reflects immediately. Empty when no harness /
        runner is available (headless tests)."""

        runner = self._runner()
        if runner is None:
            return []
        try:
            resolved = runner.get_registered_commands()
        except Exception:  # noqa: BLE001 — a faulty source must not break input
            return []
        out: list[tuple[str, str]] = []
        for rc in resolved:
            cmd = getattr(rc, "command", None)
            name = getattr(rc, "invocation_name", None)
            if not name:
                continue
            out.append((name, getattr(cmd, "description", "") or ""))
        # Issue #21 — pending (not-yet-activated) manifest plugins surface
        # their on_command triggers as STUBS so autocomplete/palette show
        # them; invoking one activates the plugin (see try_execute). The
        # description comes from the matching [[contributes.commands]] entry
        # when declared.
        try:
            registered = {n for n, _ in out}
            for record in self._pending_activations().values():
                manifest = getattr(record.extension, "manifest", None)
                if manifest is None:
                    continue
                declared = {
                    c.id: c.description for c in manifest.contributes.commands
                }
                for trigger in manifest.activation.on_command:
                    if trigger in registered:
                        continue
                    registered.add(trigger)  # dedupe repeats + cross-manifest
                    out.append((trigger, declared.get(trigger, "") or ""))
        except Exception:  # noqa: BLE001 — a faulty manifest must not break input
            pass
        return out

    def _pending_activations(self) -> dict[str, Any]:
        """Issue #21 — the live runtime's pending lazy activations ({} when
        no harness / no runtime is bound, or on pre-#21 runtimes)."""

        harness = self._harness_provider()
        runtime = getattr(harness, "runtime", None) if harness else None
        pending = getattr(runtime, "pending_activations", None)
        return pending if isinstance(pending, dict) else {}

    async def _activate_for_command(
        self, name: str, bindings: CommandSurfaceBindings
    ) -> tuple[Any | None, DispatchResult | None]:
        """Issue #21 — activate the pending plugin that declared ``name``.

        Returns ``(resolved_command, None)`` on success, ``(None, None)``
        when no pending plugin lists ``name`` in ``activation.on_command``
        (caller falls through to NOT_A_COMMAND), and
        ``(None, DispatchResult(ERROR))`` when activation was attempted and
        failed OR the activated plugin never registered the trigger it
        declared — both are plugin defects the user must see, not silent
        fall-throughs to the model.
        """

        target: str | None = None
        for plugin_id, record in self._pending_activations().items():
            manifest = getattr(record.extension, "manifest", None)
            if manifest is not None and name in manifest.activation.on_command:
                target = plugin_id
                break
        if target is None:
            return None, None

        try:
            # Function-local import: command_dispatch is imported by surfaces
            # that must not pay the loader import (and it avoids any future
            # loader↔dispatch cycle).
            from aelix_coding_agent.extensions.loader import (
                activate_pending_extension,
            )

            harness = self._harness_provider()
            await activate_pending_extension(harness.runtime, target)
        except Exception as exc:  # noqa: BLE001 — never crash the surface
            bindings.emit_error(
                f"/{name}: activating plugin {target!r} failed: {exc}"
            )
            return None, DispatchResult(DispatchOutcome.ERROR, command=name)

        try:
            runner = self._runner()
            get_command = getattr(runner, "get_command", None) if runner else None
            resolved = get_command(name) if callable(get_command) else None
        except Exception as exc:  # noqa: BLE001
            bindings.emit_error(f"/{name}: command lookup failed: {exc}")
            return None, DispatchResult(DispatchOutcome.ERROR, command=name)
        if resolved is None:
            bindings.emit_error(
                f"/{name}: plugin {target!r} declared this activation "
                "trigger but did not register the command"
            )
            return None, DispatchResult(DispatchOutcome.ERROR, command=name)
        return resolved, None

    def _runner(self) -> Any | None:
        harness = self._harness_provider()
        return getattr(harness, "extension_runner", None) if harness else None

    async def try_execute(
        self, text: str, bindings: CommandSurfaceBindings
    ) -> DispatchResult:
        """Resolve + run a ``/``-prefixed line as an extension command.

        Returns a :class:`DispatchResult`; the caller falls through to the model
        ONLY on :attr:`DispatchOutcome.NOT_A_COMMAND`. This method NEVER raises —
        a faulty extension registry, a bad command context, or a throwing handler
        all degrade to ``ERROR`` (reported via ``bindings.emit_error``) so the
        surface's input loop can stay un-guarded.
        """

        name, args = _split_command(text)
        if not name:
            return DispatchResult(DispatchOutcome.NOT_A_COMMAND)

        # Resolution touches arbitrary extension state (get_registered_commands
        # iterates every extension) — guard it like ``list_commands`` does so a
        # broken registry can never wedge the REPL.
        try:
            runner = self._runner()
            get_command = getattr(runner, "get_command", None) if runner else None
            resolved = get_command(name) if callable(get_command) else None
        except Exception as exc:  # noqa: BLE001 — a faulty registry must not crash the surface
            bindings.emit_error(f"/{name}: command lookup failed: {exc}")
            return DispatchResult(DispatchOutcome.ERROR, command=name)
        if resolved is None:
            # Issue #21 — VS Code-style on_command activation: a PENDING
            # manifest plugin whose activation.on_command lists this name is
            # activated now (module import + factory run + registry
            # refreshes), then the command re-resolves against the live
            # runner. No pending match → genuine NOT_A_COMMAND as before.
            resolved, activation_error = await self._activate_for_command(
                name, bindings
            )
            if activation_error is not None:
                return activation_error
            if resolved is None:
                return DispatchResult(
                    DispatchOutcome.NOT_A_COMMAND, command=name
                )

        handler = getattr(getattr(resolved, "command", None), "handler", None)
        if not callable(handler):
            bindings.emit_error(f"/{name}: command has no handler")
            return DispatchResult(DispatchOutcome.ERROR, command=name)

        harness = self._harness_provider()
        make_ctx = getattr(harness, "make_command_context", None)
        if not callable(make_ctx):
            bindings.emit_error(
                f"/{name}: command execution is unavailable (no command context)"
            )
            return DispatchResult(DispatchOutcome.ERROR, command=name)

        # Build the context + run the handler. Any failure is reported and STILL
        # counts as handled (pi: a thrown command never falls through to the model).
        try:
            ctx = make_ctx(
                repo=self._repo, session_runtime=self._session_runtime
            )
            result = handler(args, ctx)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 — never crash the surface
            bindings.emit_error(f"/{name} failed: {exc}")
            return DispatchResult(DispatchOutcome.ERROR, command=name)

        # str-return compatibility shim (Aelix-additive; pi ignores returns).
        if isinstance(result, str) and result.strip():
            rendered = result
            if len(rendered) > _MAX_RENDERED_RETURN_CHARS:
                rendered = (
                    rendered[:_MAX_RENDERED_RETURN_CHARS] + "\n… (truncated)"
                )
            bindings.emit_text(rendered)
        return DispatchResult(DispatchOutcome.HANDLED, command=name)


__all__ = [
    "CommandDispatchService",
    "CommandSurfaceBindings",
    "DispatchOutcome",
    "DispatchResult",
]
