"""ExtensionRunner — aggregation surface for loaded Extensions.

Pi parity: ``session.extensionRunner.getRegisteredCommands()`` (Pi
``runner.ts:512-551``) — returns the **disambiguated** list of all
registered commands across loaded extensions, wrapped in
:class:`ResolvedCommand` so callers see both the original
:class:`RegisteredCommand` and the resolved ``invocation_name`` Pi uses
on the wire.

Sprint 6h₁ (ADR-0069, P-219/P-220) introduces this surface so the
``get_commands`` RPC handler can iterate extension-registered commands
without reaching into the per-Extension ``commands`` dict.

Sprint 6h₁ W6 (ADR-0069, P-224/P-229) extends the runner to disambiguate
colliding command names with the Pi ``{name}:{N}`` suffix loop and to
forward the owning extension's :class:`ExtensionSourceInfo` onto every
resolved command. Pi attaches ``sourceInfo`` at resolution time rather
than at registration time (the registry's :class:`RegisteredCommand`
intentionally does NOT carry it — the owning extension is the authority).

Sprint 6h₅a (Phase 4.14, ADR-0081, P-333) extends the runner with
Pi-parity ``emit()`` / ``has_handlers()`` methods that delegate to the
harness :class:`HookBus` via two callable bridge fields wired at
construction (``harness/core.py:632-634``). Pi top-level
``emitSessionShutdownEvent`` (``runner.ts:177-189``) and the cancel
aggregator (``runner.ts:680-712``) flow through these methods so existing
:class:`HookBus` infrastructure (sequential await + first-cancel-wins +
per-handler ``error_mode`` isolation) is the single source of truth for
extension dispatch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Avoid the harness↔coding-agent runtime import cycle (D.1.9).
    from aelix_coding_agent.extensions.api import (
        Extension,
        ExtensionSourceInfo,
        RegisteredCommand,
    )

    from aelix_agent_core.harness.hooks import HookEvent, HookEventName


@dataclass(frozen=True)
class ResolvedCommand:
    """Pi parity: ``runner.ts:1061-1067`` ``ResolvedCommand``.

    Wraps a :class:`RegisteredCommand` with:

    - ``invocation_name`` — the disambiguated name Pi uses on the wire
      (the original :attr:`RegisteredCommand.name` when unique, or
      ``{name}:{N}`` when colliding with another extension's command).
    - ``source_info`` — the :class:`ExtensionSourceInfo` of the owning
      extension, forwarded by :meth:`ExtensionRunner.get_registered_commands`.
      Pi attaches this at resolution time; the registry's
      :class:`RegisteredCommand` does NOT carry it (the owning extension
      is the authority for source metadata).

    Sprint 6h₁ W6 (P-224/P-229).
    """

    command: RegisteredCommand
    invocation_name: str
    source_info: ExtensionSourceInfo | None


@dataclass(frozen=True)
class ExtensionRunner:
    """Pi parity: ``ExtensionRunner`` aggregation surface.

    Wraps the list of loaded :class:`Extension` instances and exposes
    the read methods Pi's RPC + extension dispatch surface needs.
    Defaults to an empty extension list (and unwired bridge callables)
    so callers that have no extensions still get a valid runner instance.

    Sprint 6h₅a (Phase 4.14, ADR-0081, P-333) — extends the Sprint 6h₁
    commands-only aggregation surface with Pi-parity ``emit()`` /
    ``has_handlers()``. Both methods delegate to the harness
    :class:`HookBus` via two callable bridge fields wired at construction
    time by :class:`AgentHarness` (``harness/core.py:632-634``). The
    dataclass remains ``frozen=True`` — the callables are read-only
    after construction. Pi cancel-aggregation semantics
    (``runner.ts:680-712``) are inherited from
    :func:`HookBus._reducer_session_before` (first-cancel-wins
    short-circuit + sequential await + per-handler ``error_mode``
    isolation). When the bridges are unwired (default), ``emit`` returns
    ``None`` and ``has_handlers`` returns ``False`` — safe no-op for
    tests / harnesses that haven't wired the bus.
    """

    extensions: list[Extension] = field(default_factory=list)
    # Sprint 6h₅a (Phase 4.14, ADR-0081, P-333) — HookBus bridges.
    _emit: Callable[[HookEvent], Awaitable[Any]] | None = None
    _has_handlers: Callable[[HookEventName], bool] | None = None

    async def emit(self, event: HookEvent) -> Any:
        """Pi parity: ``ExtensionRunner.emit`` (``runner.ts:680-712``).

        Sprint 6h₅a (ADR-0081, P-333). Delegates to :meth:`HookBus.emit`
        when a bridge callable was injected at construction; returns
        ``None`` (no-op) when unwired. The bridge resolves to the
        harness's tested reducer/observer pipeline (sequential await +
        first-cancel-wins + per-handler ``error_mode`` isolation).
        """

        if self._emit is None:
            return None
        return await self._emit(event)

    def has_handlers(self, event_name: HookEventName) -> bool:
        """Pi parity: ``ExtensionRunner.hasHandlers``.

        Sprint 6h₅a (ADR-0081, P-333). Delegates to
        :meth:`HookBus.has_handlers`; returns ``False`` when unwired.
        """

        if self._has_handlers is None:
            return False
        return self._has_handlers(event_name)

    def get_registered_commands(self) -> list[ResolvedCommand]:
        """Pi parity: ``ExtensionRunner.getRegisteredCommands()`` (Pi
        ``runner.ts:512-551`` ``resolveRegisteredCommands``).

        Two-pass aggregation:

        1. **First pass** — count occurrences of each :attr:`RegisteredCommand.name`
           across every loaded extension, in extension-load order and then
           command-registration order (Pi ``Map.values()`` insertion order;
           Python's ``dict.values()`` matches).
        2. **Second pass** — assign ``invocation_name``. When a name is
           unique, use it verbatim. When colliding, the first occurrence
           keeps the bare name and subsequent ones get ``{name}:{N}``
           starting at ``N=1`` and incrementing until the candidate name
           does not collide with another extension that happened to
           explicitly register the disambiguated form.

        Each :class:`ResolvedCommand` also carries the owning extension's
        :attr:`Extension.source_info` (P-229) so RPC clients see the
        full Pi ``sourceInfo`` wire shape downstream.
        """

        # First pass: count occurrences in insertion order.
        counts: dict[str, int] = {}
        pairs: list[tuple[Extension, RegisteredCommand]] = []
        for ext in self.extensions:
            for cmd in ext.commands.values():
                counts[cmd.name] = counts.get(cmd.name, 0) + 1
                pairs.append((ext, cmd))

        # Second pass: assign invocation_name with Pi ``{name}:{N}``
        # disambiguation suffix loop.
        seen: dict[str, int] = {}
        taken: set[str] = set()
        resolved: list[ResolvedCommand] = []
        for ext, cmd in pairs:
            base = cmd.name
            if counts[base] == 1:
                invocation_name = base
            else:
                idx = seen.get(base, 0)
                invocation_name = base if idx == 0 else f"{base}:{idx}"
                # Avoid collisions with another extension that happened to
                # register the disambiguated form ``{base}:{idx}`` explicitly.
                while invocation_name in taken:
                    idx += 1
                    invocation_name = f"{base}:{idx}"
                seen[base] = idx + 1
            taken.add(invocation_name)
            resolved.append(
                ResolvedCommand(
                    command=cmd,
                    invocation_name=invocation_name,
                    source_info=ext.source_info,
                )
            )
        return resolved


__all__ = ["ExtensionRunner", "ResolvedCommand"]
