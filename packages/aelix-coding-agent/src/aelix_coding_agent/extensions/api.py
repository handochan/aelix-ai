"""Extension surface — ``Extension`` dataclass, ``ExtensionAPI``, runtime.

This module mirrors pi-agent-core's ``extension-runner`` split:

- :class:`Extension` is a mutable container the loader populates while a
  factory runs (handlers, tools, flags, cleanups).
- :class:`ExtensionAPI` is the concrete façade an extension factory receives
  as its single argument (``def setup(aelix: ExtensionAPI) -> None``). It
  mutates the bound :class:`Extension` and delegates "actions" to a
  :class:`_ExtensionRuntime` whose method table starts as throwing stubs and
  is replaced when :class:`~aelix_agent_core.harness.core.AgentHarness` calls
  :meth:`_ExtensionRuntime.bind_core`.
- :class:`ExtensionContext` is the small read-only view a handler receives
  alongside its event. Per D.1.4 it is a concrete class with a
  ``__getattribute__`` shim that asserts the runtime is still active before
  resolving any non-internal attribute.

Sprint 5a (Phase 3.1, ADR-0041) extends the surface to Pi parity:

- :class:`ExtensionAPI` grows from 8 to 48 methods (P-22): registerCommand /
  registerShortcut / registerMessageRenderer / registerProvider /
  unregisterProvider, sendMessage / sendUserMessage / appendEntry /
  setSessionName / getSessionName / setLabel / exec / getAllTools /
  getCommands / setModel / getThinkingLevel / setThinkingLevel +
  ``events`` property.
- :class:`Extension` extends to 7 collections + 2 metadata fields (P-27):
  commands / shortcuts / message_renderers / source_info / resolved_path.
- :class:`ExtensionRuntimeActions` extends from 3 to 15 actions (P-28).
- :class:`ExtensionContext` extends to 14 non-UI fields (P-23): has_ui,
  session_manager, model_registry, signal, has_pending_messages, shutdown,
  get_context_usage, compact.

Most new methods are throwing-stub delegates pending Sprint 5b CLI / Phase 4
provider / Phase 5 UI emit sites; ``set_session_name`` / ``get_session_name``
/ ``set_label`` / ``set_model`` / ``set_thinking_level`` / ``exec`` /
``get_all_tools`` are bound for real at Sprint 5a per the spec table.

Sprint 6h₉c (Phase 5b-foundation, ADR-0100) closes the ``ui`` deferral:
:attr:`ExtensionContext.ui` returns a typed :class:`ExtensionUIContext`
(headless singleton by default; replaced via
:meth:`_ExtensionRuntime.bind_ui` in Sprint 6h₁₀b). This clears the
Sprint 5a phantom "ADR-0033" citation (that ADR was a reserved slot in
``docs/decisions/`` between 0032 and 0034 that was never written —
ADR-0100 is the actual closure).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, overload

from aelix_agent_core.contracts import PluginManifest
from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    AbortHandler,
    AfterProviderResponseHandler,
    AgentEndHandler,
    AgentStartHandler,
    BeforeAgentStartHandler,
    BeforeProviderPayloadHandler,
    BeforeProviderRequestHandler,
    ContextHandler,
    HookCleanup,
    HookErrorMode,
    HookEventName,
    HookHandler,
    InputHandler,
    MessageEndHandler,
    MessageStartHandler,
    MessageUpdateHandler,
    ModelSelectHandler,
    QueueUpdateHandler,
    ResourcesDiscoverHandler,
    ResourcesUpdateHandler,
    SavePointHandler,
    SessionBeforeCompactHandler,
    SessionBeforeForkHandler,
    SessionBeforeSwitchHandler,
    SessionBeforeTreeHandler,
    SessionCompactHandler,
    SessionShutdownHandler,
    SessionStartHandler,
    SessionTreeHandler,
    SettledHandler,
    ThinkingLevelSelectHandler,
    ToolCallHandler,
    ToolExecutionEndHandler,
    ToolExecutionStartHandler,
    ToolExecutionUpdateHandler,
    ToolResultHandler,
    TurnEndHandler,
    TurnStartHandler,
    UserBashHandler,
)
from aelix_agent_core.types import AgentTool
from aelix_ai.streaming import Model

from .ext_ui import ExtensionUIContext
from .headless_ui import HEADLESS_UI_CONTEXT

if TYPE_CHECKING:
    pass


# === Errors ===


class ExtensionError(Exception):
    """Raised when an extension or its context is used incorrectly.

    ``code`` follows the Pi extension-runtime taxonomy:
    - ``"stale"`` — the runtime has been disposed/invalidated.
    - ``"unbound"`` — an action stub was invoked before ``bind_core``.
    - ``"invalid_state"`` — an extension API call is illegal in the current
      harness phase.
    """

    def __init__(
        self,
        code: Literal["stale", "unbound", "invalid_state"],
        message: str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause


# === Sprint 5a (Phase 3.1) — supporting types for full ExtensionContext ===


@dataclass(frozen=True)
class ContextUsage:
    """Pi ``ContextUsage`` (``types.ts:281-287``).

    Reported by :meth:`ExtensionContext.get_context_usage`. Sprint 5a
    populates this opportunistically from the last
    :class:`~aelix_agent_core.harness.hooks.MessageEndHookEvent` cost when
    the harness threads it through; until Phase 4 provider work lands the
    cost details, this returns ``None``.
    """

    tokens: int | None
    context_window: int
    percent: float | None


@dataclass(frozen=True)
class ExecResult:
    """Pi ``ExecResult`` (``exec.ts:22-27``).

    Aelix port of :func:`subprocess.run` boundaries: matches Pi's
    ``stdout / stderr / code / killed`` shape exactly so a Pi extension
    calling ``aelix.exec(...)`` sees identical fields.
    """

    stdout: str
    stderr: str
    code: int
    killed: bool


class ReadonlySessionManager(Protocol):
    """Pi ``ReadonlySessionManager`` minimal Protocol shim.

    Sprint 5a exposes only the property surface
    :meth:`ExtensionContext.session_manager` callers need today (the
    underlying :class:`aelix_agent_core.session.Session` instance). Full
    Pi method coverage lands when ADR-0042 + Phase 4 widen the surface.
    """

    def get_session(self) -> Any | None:
        """Return the currently attached :class:`Session`, or ``None``."""


class ModelRegistry(Protocol):
    """Pi ``ModelRegistry`` minimal Protocol stub (Sprint 5a).

    Sprint 5a ships only the ``register_provider`` / ``unregister_provider``
    surface plus :meth:`get_models` so extension authors can write code
    against a stable surface. The full implementation (API-key resolution,
    OAuth, model selection) is owned by ADR-0038 (Phase 4 provider
    adapter).
    """

    def register_provider(self, name: str, config: Any) -> None: ...

    def unregister_provider(self, name: str) -> None: ...

    def get_models(self) -> list[Any]: ...


class _StubModelRegistry:
    """Default :class:`ModelRegistry` stub installed when no provider lands.

    Tracks pending registrations into a list so a Phase 4 provider adapter
    can later replay them after binding. All read methods return empty
    collections so existing extension code surface-checks pass.
    """

    def __init__(self) -> None:
        self._registrations: list[tuple[str, Any]] = []

    def register_provider(self, name: str, config: Any) -> None:
        # Last write wins per Pi ``registerProvider`` semantics.
        self._registrations = [(n, c) for n, c in self._registrations if n != name]
        self._registrations.append((name, config))

    def unregister_provider(self, name: str) -> None:
        self._registrations = [(n, c) for n, c in self._registrations if n != name]

    def get_models(self) -> list[Any]:
        return []

    @property
    def registrations(self) -> list[tuple[str, Any]]:
        return list(self._registrations)


@dataclass(frozen=True)
class SlashCommandInfo:
    """Pi ``SlashCommandInfo`` minimal stub (Sprint 5a).

    Returned by :meth:`ExtensionAPI.get_commands`. Sprint 5a always
    returns ``[]`` (no slash command surface yet); Sprint 5b CLI loop
    populates this from the per-runtime registry.
    """

    name: str
    description: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class ToolInfo:
    """Pi ``ToolInfo`` minimal stub (Sprint 5a).

    Snapshot returned by :meth:`ExtensionAPI.get_all_tools`. Only the
    ``name`` and ``description`` fields are populated in Sprint 5a; the
    full Pi shape (parameters schema, source metadata) lands with
    ADR-0042.
    """

    name: str
    description: str | None = None


# === Sprint 5a (Phase 3.1) — minimal in-process EventBus port ===


class EventBus:
    """Pi ``EventBus`` port (``event-bus.ts``).

    Channels are arbitrary strings; subscribers receive the raw ``data``
    payload. Per-handler try/except containment matches Pi's
    ``createEventBus`` behaviour. Constructed once per ``_ExtensionRuntime``
    and reused for the lifetime of the runtime so every loaded extension
    observes the same instance via :attr:`ExtensionAPI.events`.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Any], Any]]] = {}

    def emit(self, channel: str, data: Any = None) -> None:
        for handler in list(self._handlers.get(channel, ())):
            # Pi parity (``createEventBus`` ``event-bus.ts``): per-handler
            # exception containment — a faulty subscriber never breaks the
            # publish path.
            with contextlib.suppress(Exception):
                handler(data)

    def on(
        self, channel: str, handler: Callable[[Any], Any]
    ) -> Callable[[], None]:
        bucket = self._handlers.setdefault(channel, [])
        bucket.append(handler)

        def unsubscribe() -> None:
            try:
                bucket.remove(handler)
            except ValueError:
                return

        return unsubscribe

    def clear(self) -> None:
        self._handlers.clear()


# === Runtime: throwing-stub actions, replaced at bind_core ===


@dataclass
class ExtensionRuntimeActions:
    """The action table the harness installs via :meth:`bind_core`.

    Sprint 5a (Phase 3.1, P-28) extends the action surface from 3 to 15
    actions to match Pi ``ExtensionActions`` (``types.ts:1471-1488``).
    Defaults are throwing stubs created by :func:`_make_throwing_stub`;
    the harness rebinds the real implementations for the actions that
    have a Sprint 5a home (``set_session_name`` / ``get_session_name`` /
    ``set_label`` / ``set_model`` / ``set_thinking_level`` /
    ``get_thinking_level`` / ``get_all_tools`` / ``exec`` plus the
    Sprint 3a trio).

    The remaining 4 (``send_message`` / ``send_user_message`` /
    ``append_entry`` / ``get_commands``) stay as throwing stubs in
    Sprint 5a; ADR-0042 (Sprint 5b CLI loop) and ADR-0043 (built-in
    tools) land them.
    """

    # Sprint 3a originals.
    get_active_tools: Callable[[], list[str]]
    set_active_tools: Callable[[list[str]], None]
    get_system_prompt: Callable[[], str]
    # Sprint 5a additions — 12 new actions for full Pi parity.
    send_message: Callable[..., Any]
    send_user_message: Callable[..., Any]
    append_entry: Callable[..., Any]
    set_session_name: Callable[[str], None]
    get_session_name: Callable[[], str | None]
    set_label: Callable[[str, str | None], None]
    get_all_tools: Callable[[], list[ToolInfo]]
    get_commands: Callable[[], list[SlashCommandInfo]]
    set_model: Callable[[Model], Awaitable[bool]]
    get_thinking_level: Callable[[], str]
    set_thinking_level: Callable[[str], None]
    exec: Callable[..., Awaitable[ExecResult]]


def _make_throwing_stub(name: str) -> Callable[..., Any]:
    """Build an action stub that raises until the harness rebinds the runtime."""

    def stub(*_args: Any, **_kwargs: Any) -> Any:
        raise ExtensionError(
            "unbound",
            f"ExtensionAPI.{name}() called before AgentHarness bound the runtime.",
        )

    stub.__name__ = f"_stub_{name}"
    return stub


def _default_actions() -> ExtensionRuntimeActions:
    return ExtensionRuntimeActions(
        get_active_tools=_make_throwing_stub("get_active_tools"),
        set_active_tools=_make_throwing_stub("set_active_tools"),
        get_system_prompt=_make_throwing_stub("get_system_prompt"),
        # Sprint 5a additions — all default to throwing stubs.
        send_message=_make_throwing_stub("send_message"),
        send_user_message=_make_throwing_stub("send_user_message"),
        append_entry=_make_throwing_stub("append_entry"),
        set_session_name=_make_throwing_stub("set_session_name"),
        get_session_name=_make_throwing_stub("get_session_name"),
        set_label=_make_throwing_stub("set_label"),
        get_all_tools=_make_throwing_stub("get_all_tools"),
        get_commands=_make_throwing_stub("get_commands"),
        set_model=_make_throwing_stub("set_model"),
        get_thinking_level=_make_throwing_stub("get_thinking_level"),
        set_thinking_level=_make_throwing_stub("set_thinking_level"),
        exec=_make_throwing_stub("exec"),
    )


class _ExtensionRuntime:
    """Tracks liveness and holds the rebindable action table.

    One runtime is created per ``load_extensions`` call (D.1.7) and shared
    by every ExtensionAPI it spawns. :meth:`invalidate` flips the runtime
    into a stale state — every subsequent context attribute access raises
    :class:`ExtensionError` with code ``"stale"``.

    Sprint 5a (Phase 3.1) extends the runtime with the shared
    :class:`EventBus`, the :class:`ModelRegistry`, and a
    ``pending_provider_registrations`` queue so
    :meth:`ExtensionAPI.register_provider` calls made during extension
    setup are buffered until the Phase 4 provider adapter binds the real
    registry.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> None:
        self._actions: ExtensionRuntimeActions = _default_actions()
        self._stale_message: str | None = None
        # Sprint 5a additions.
        self._event_bus: EventBus = event_bus or EventBus()
        self._model_registry: ModelRegistry = (
            model_registry or _StubModelRegistry()
        )
        # Provider registrations queued during extension setup; the Phase 4
        # adapter (ADR-0038) replays them after binding.
        self.pending_provider_registrations: list[tuple[str, Any]] = []
        self.pending_provider_unregistrations: list[str] = []
        # Sprint 6h₇c (Phase 5a-iii-γ, ADR-0093 §C, P-447) — flag values
        # runtime state container. Pi parity: ``runner.ts:409-411``
        # ``getFlagValues`` / ``setFlagValue`` over a ``Map<string,
        # boolean | string>``. Aelix uses ``dict`` (Python idiom);
        # shallow-copy semantic preserved.
        self.flag_values: dict[str, bool | str] = {}
        # Sprint 6h₉c (ADR-0100) — ExtensionUIContext binding.
        # Default: HEADLESS_UI_CONTEXT singleton (raises per method).
        # Sprint 6h₁₀b: replace via bind_ui() with concrete prompt-
        # toolkit + Rich + Aelix widget layer impl.
        self._ui: ExtensionUIContext = HEADLESS_UI_CONTEXT

    @property
    def actions(self) -> ExtensionRuntimeActions:
        return self._actions

    @property
    def is_stale(self) -> bool:
        return self._stale_message is not None

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def model_registry(self) -> ModelRegistry:
        return self._model_registry

    # ── Sprint 6h₉c (Phase 5b-foundation, ADR-0100) ───────────────
    # ExtensionUIContext binding. Default is :data:`HEADLESS_UI_CONTEXT`
    # (raises NotImplementedError per method); Sprint 6h₁₀b
    # (Phase 5c-tui) supplies a concrete prompt-toolkit + Rich impl
    # via :meth:`bind_ui` once the AgentHarness bridge wiring lands.

    @property
    def ui(self) -> ExtensionUIContext:
        """Currently bound :class:`ExtensionUIContext`.

        Defaults to :data:`HEADLESS_UI_CONTEXT`; replaced by
        :meth:`bind_ui` in Sprint 6h₁₀b.
        """

        return self._ui

    def bind_ui(self, ui: ExtensionUIContext) -> None:
        """Replace the headless UI with a concrete binding (Sprint 6h₁₀b).

        Idempotent: passing the same binding again is a no-op. Passing
        :data:`HEADLESS_UI_CONTEXT` reverts to the headless default and
        flips :attr:`ExtensionContext.has_ui` back to ``False``.
        """

        self._ui = ui

    def bind_core(self, actions: ExtensionRuntimeActions) -> None:
        """Install real action implementations (called by AgentHarness)."""

        self._actions = actions

    def bind_model_registry(self, registry: ModelRegistry) -> None:
        """Phase 4 hook — swap the model-registry implementation.

        Replays any pending provider registrations through the new
        registry so extensions that called ``register_provider`` during
        setup land cleanly post-bind.
        """

        self._model_registry = registry
        for name, config in self.pending_provider_registrations:
            # Containment — a faulty Phase 4 registry implementation must
            # not abort the bind path; pending registrations replay
            # best-effort.
            with contextlib.suppress(Exception):
                registry.register_provider(name, config)
        self.pending_provider_registrations.clear()
        for name in self.pending_provider_unregistrations:
            with contextlib.suppress(Exception):
                registry.unregister_provider(name)
        self.pending_provider_unregistrations.clear()

    def invalidate(self, message: str | None = None) -> None:
        """Pi parity: ``_ExtensionRuntime.invalidate`` default-msg align.

        Sprint 6h₅b (Phase 4.15, ADR-0083, P-362). Default argument now
        aligns with :data:`PI_STALENESS_MESSAGE` (Pi verbatim string from
        ``runner.ts:467``) so a caller bypassing
        :meth:`ExtensionRunner.invalidate` sees the SAME staleness
        message as callers routing through the runner. ``None`` is the
        sentinel meaning "use the Pi default"; explicit strings (e.g.
        ``"AgentHarness has been disposed"`` from
        :meth:`AgentHarness.dispose`) continue to override.
        """

        from aelix_agent_core.runtime._types import PI_STALENESS_MESSAGE

        self._stale_message = message if message is not None else PI_STALENESS_MESSAGE

    def assert_active(self) -> None:
        if self._stale_message is not None:
            raise ExtensionError("stale", self._stale_message)

    # ── Sprint 6h₇c (Phase 5a-iii-γ, ADR-0093 §C, P-447) ──────────
    # Pi parity: ``runner.ts:409-411`` ``getFlagValues`` /
    # ``setFlagValue``. The runtime owns the flag-values dict; the
    # outer :class:`ExtensionRunner` delegates here.

    def get_flag_values(self) -> dict[str, bool | str]:
        """Pi parity: ``_ExtensionRuntime.getFlagValues`` (``runner.ts:409``).

        Returns a SHALLOW COPY of the flag-values dict (Pi parity ``Map``
        shallow copy via ``new Map(this.flagValues)``). Mutations to the
        returned dict do NOT affect the runtime's internal state.
        """

        return dict(self.flag_values)

    def set_flag_value(self, name: str, value: bool | str) -> None:
        """Pi parity: ``_ExtensionRuntime.setFlagValue`` (``runner.ts:411``).

        Mutates the runtime's internal flag-values dict. Last write wins
        per flag name (Pi parity).
        """

        self.flag_values[name] = value


# === Extension container ===


@dataclass
class ExtensionFlag:
    """Declarative flag registered by an extension."""

    name: str
    type: Literal["bool", "str"]
    default: bool | str | None = None
    description: str | None = None


@dataclass
class RegisteredCommand:
    """Pi ``RegisteredCommand`` minimal port (Sprint 5a stub).

    Sprint 5a stores commands so a Sprint 5b CLI loop can read them off
    :attr:`Extension.commands`; the actual dispatch (autocomplete /
    invocation) lands in ADR-0042.
    """

    name: str
    handler: Callable[..., Any]
    description: str | None = None
    source: str | None = None  # extension name (Aelix flavour of Pi sourceInfo)


@dataclass
class ExtensionShortcut:
    """Pi ``ExtensionShortcut`` minimal port (Sprint 5a stub)."""

    key: str
    handler: Callable[..., Any]
    description: str | None = None


MessageRenderer = Callable[..., Any]
"""Pi ``MessageRenderer`` — Sprint 5a registers, Phase 5 renders."""


@dataclass(frozen=True)
class ExtensionSourceInfo:
    """Pi ``SourceInfo`` port (``source-info.ts:1-12``).

    Carries the discovered origin of an extension so a Sprint 5b CLI loop
    can surface "where did this command come from?" in autocomplete /
    error messages.

    Sprint 5a (P-27) shipped the bare minimum — ``source`` (an Aelix
    flavor: ``"project"`` / ``"global"`` / ``"explicit"`` /
    ``"entry_points"`` / ``"inline"``) plus an optional ``base_dir``.

    Sprint 6h₁ (ADR-0069, P-221) added the optional ``identifier``
    field to match Pi ``SourceInfo.identifier`` so the ``get_commands``
    RPC handler can disambiguate commands that share a name.

    Sprint 6h₁ W6 (ADR-0069, P-225 BLOCKING) extends the dataclass with
    the three remaining Pi ``SourceInfo`` fields so the wire shape
    emitted by ``_handle_get_commands`` matches Pi byte-for-byte:

    - ``path`` (Pi ``SourceInfo.path``) — filesystem path to the source.
    - ``scope`` (Pi ``"user" | "project" | "temporary"``) — defaults to
      ``"user"`` so existing extension callers that did not supply a
      scope still emit a Pi-shape wire payload.
    - ``origin`` (Pi ``"package" | "top-level"``) — defaults to
      ``"top-level"`` for the same back-compat reason.

    The Sprint 5a ``source`` Literal is an Aelix-additive distinguisher
    (entry-points vs. inline vs. project, etc.) that the wire layer
    falls back to when no explicit ``path``/``identifier`` is set.
    """

    source: Literal["project", "global", "explicit", "entry_points", "inline"]
    base_dir: str | None = None
    identifier: str | None = None
    # Sprint 6h₁ W6 (P-225) — Pi SourceInfo wire-shape fields.
    path: str | None = None
    scope: Literal["user", "project", "temporary"] = "user"
    origin: Literal["package", "top-level"] = "top-level"


@dataclass
class Extension:
    """The mutable record the loader populates while a factory runs.

    ``handler_error_modes`` (ADR-0019 v3) carries the per-handler error
    policy keyed by ``(event_name, id(handler))``; the harness threads these
    into :class:`HookBus` registration when wiring the extension.

    Sprint 5a (Phase 3.1, P-27) extends the record to Pi parity:

    - ``commands`` / ``shortcuts`` / ``message_renderers`` — the three new
      registration buckets exposed via :class:`ExtensionAPI` methods.
    - ``source_info`` / ``resolved_path`` — metadata captured by the
      loader during discovery (Pi parity ``Extension.sourceInfo`` +
      ``Extension.resolvedPath`` at ``types.ts:1540-1547``).
    - ``cleanups`` (existing) remains an Aelix-additive convenience over
      Pi's ``EventBus`` model.
    """

    name: str
    handlers: dict[HookEventName, list[HookHandler]] = field(default_factory=dict)
    tools: dict[str, AgentTool] = field(default_factory=dict)
    flags: dict[str, ExtensionFlag] = field(default_factory=dict)
    cleanups: list[HookCleanup] = field(default_factory=list)
    handler_error_modes: dict[tuple[HookEventName, int], HookErrorMode] = field(
        default_factory=dict
    )
    # === Sprint 5a (Phase 3.1) additions (P-27) ===
    commands: dict[str, RegisteredCommand] = field(default_factory=dict)
    shortcuts: dict[str, ExtensionShortcut] = field(default_factory=dict)
    message_renderers: dict[str, MessageRenderer] = field(default_factory=dict)
    source_info: ExtensionSourceInfo | None = None
    resolved_path: str | None = None
    # === Sprint 6h₉b §A — manifest-discovered extensions ===
    # ``aelix-plugin.toml``-discovered extensions carry their parsed
    # :class:`~aelix_agent_core.contracts.PluginManifest`; legacy
    # ``pyproject.toml [tool.aelix]`` / ``__init__.py`` discovery paths
    # leave this ``None``. Runtime consumers (Sprint 6h₉c/d/e/f) read
    # declared capabilities / activation / contributes through this field.
    manifest: PluginManifest | None = None


ExtensionFactory = Callable[["ExtensionAPI"], Any]


# === ExtensionContext (concrete with stale guard, D.1.4) ===


class ExtensionContext:
    """Read-only view passed to every hook handler.

    Per D.1.4 this is a concrete class — not a Protocol — so ``__getattribute__``
    can call :meth:`_ExtensionRuntime.assert_active` before resolving any
    non-internal attribute. Internal attributes are anything starting with
    an underscore plus the explicit allowlist (``assert_active``).

    Sprint 5a (Phase 3.1, P-23) extends from 5 to 14 non-UI fields to match
    Pi ``ExtensionContext`` at ``types.ts:280-310``. New fields:
    ``has_ui``, ``session_manager``, ``model_registry``, ``signal``,
    ``has_pending_messages``, ``shutdown``, ``get_context_usage``,
    ``compact``.

    Sprint 6h₉c (Phase 5b-foundation, ADR-0100) closed the ``ui``
    field (Pi field 1) by typing it as
    :class:`~aelix_coding_agent.extensions.ext_ui.ExtensionUIContext`
    and binding the headless default singleton
    (:data:`~aelix_coding_agent.extensions.headless_ui.HEADLESS_UI_CONTEXT`);
    ``has_ui`` now reflects the bound state. The concrete prompt-toolkit
    + Rich + Aelix widget layer impl lands in Sprint 6h₁₀b
    (Phase 5c-tui). The Sprint 5a docstrings cited a phantom "ADR-0033"
    reserved slot that was never written; ADR-0100 is the actual
    closure.
    """

    # assert_active is exempt from the staleness pre-check because it IS the
    # staleness check itself.  Blocking access to it via __getattribute__ before
    # delegating would prevent callers from ever learning the runtime is stale.
    _INTERNAL_NAMES: frozenset[str] = frozenset({"assert_active"})

    def __init__(
        self,
        runtime: _ExtensionRuntime,
        *,
        cwd: str,
        model: Model | None,
        is_idle: Callable[[], bool],
        abort: Callable[[], None],
        get_active_tools: Callable[[], list[str]],
        get_system_prompt: Callable[[], str],
        # Sprint 5a additions — all default to safe-noop closures so older
        # call sites (tests / harness pre-5a) keep working.
        session_manager: ReadonlySessionManager | None = None,
        signal: Any | None = None,
        has_pending_messages: Callable[[], bool] | None = None,
        shutdown: Callable[[], None] | None = None,
        get_context_usage: Callable[[], ContextUsage | None] | None = None,
        compact: Callable[..., None] | None = None,
    ) -> None:
        # Bypass our own __setattr__ guard via object.__setattr__ for the
        # private slots we want excluded from the staleness check.
        object.__setattr__(self, "_runtime", runtime)
        object.__setattr__(self, "_cwd", cwd)
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_is_idle", is_idle)
        object.__setattr__(self, "_abort", abort)
        object.__setattr__(self, "_get_active_tools", get_active_tools)
        object.__setattr__(self, "_get_system_prompt", get_system_prompt)
        # Sprint 5a additions.
        object.__setattr__(self, "_session_manager", session_manager)
        object.__setattr__(self, "_signal", signal)
        object.__setattr__(
            self, "_has_pending_messages", has_pending_messages or (lambda: False)
        )
        object.__setattr__(
            self,
            "_shutdown",
            shutdown
            or (
                lambda: (_ for _ in ()).throw(
                    ExtensionError(
                        "invalid_state",
                        "ExtensionContext.shutdown() requires a Sprint 5b CLI binding.",
                    )
                )
            ),
        )
        object.__setattr__(
            self, "_get_context_usage", get_context_usage or (lambda: None)
        )
        object.__setattr__(self, "_compact_action", compact or (lambda **_: None))

    def __getattribute__(self, name: str) -> Any:
        # Allow private/dunder access without the staleness check.
        if name.startswith("_") or name in ExtensionContext._INTERNAL_NAMES:
            return object.__getattribute__(self, name)
        runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
        runtime.assert_active()
        return object.__getattribute__(self, name)

    # --- Public surface (Sprint 3a baseline) ---

    @property
    def cwd(self) -> str:
        return object.__getattribute__(self, "_cwd")

    @property
    def model(self) -> Model | None:
        """Current model bound to this harness, or ``None`` if cleared.

        Pi parity note (F-8): Pi declares this field as ``Model<TApi> | undefined``
        with a generic API parameter (Pi ``ExtensionContext.model``). Aelix
        erases the generic and uses ``Model | None`` because the API
        distinguisher in Aelix is the runtime string ``model.api`` (see
        ``aelix_ai.streaming.Model``), not a static type parameter. Callers
        that need narrowing should ``match model.api:`` rather than rely on a
        static API type. Phase 2.x may revisit with PEP 695 generics; until
        then this is a documented gap, not a divergence.
        """

        return object.__getattribute__(self, "_model")

    def is_idle(self) -> bool:
        return object.__getattribute__(self, "_is_idle")()

    def abort(self) -> None:
        object.__getattribute__(self, "_abort")()

    def get_active_tools(self) -> list[str]:
        return list(object.__getattribute__(self, "_get_active_tools")())

    def get_system_prompt(self) -> str:
        return object.__getattribute__(self, "_get_system_prompt")()

    def assert_active(self) -> None:
        object.__getattribute__(self, "_runtime").assert_active()

    # --- Sprint 5a (Phase 3.1) additions — P-23 closure ---

    @property
    def has_ui(self) -> bool:
        """Pi ``hasUI`` — True only when a concrete (non-headless) TUI is bound.

        Sprint 6h₉c (ADR-0100) clarifies: ``has_ui`` reflects whether a
        concrete UI binding has been installed via
        :meth:`_ExtensionRuntime.bind_ui` (Sprint 6h₁₀b). The headless
        default does NOT flip this to True — extensions should guard
        ``ctx.ui.*`` calls with ``if ctx.has_ui:`` to avoid the
        :exc:`NotImplementedError` raised by the headless binding in
        Phase 5b.

        Once Sprint 6h₁₀b lands the concrete binding via AgentHarness
        bridge wiring, ``has_ui`` flips to ``True`` and ``ctx.ui.*``
        calls succeed.

        (Sprint 5a code comments cited "ADR-0033" as the placeholder
        owner; that ADR was a reserved slot never written, replaced by
        ADR-0100 in Sprint 6h₉c.)
        """

        runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
        return runtime.ui is not HEADLESS_UI_CONTEXT

    @property
    def ui(self) -> ExtensionUIContext:
        """Pi ``ui: ExtensionUIContext`` — ADR-0100 (Sprint 6h₉c) closure.

        (Sprint 5a code comments cited a phantom "ADR-0033" reserved
        slot that was never written; ADR-0100 is the actual closure.)

        Returns the headless singleton (:data:`HEADLESS_UI_CONTEXT`) by
        default, which raises :exc:`NotImplementedError` per method
        call with a pointer to Sprint 6h₁₀b (Phase 5c-tui). When an
        AgentHarness binds a real TUI via its bridge wiring (Sprint
        6h₁₀b), a concrete :class:`ExtensionUIContext` implementation
        replaces the headless singleton via
        :meth:`_ExtensionRuntime.bind_ui` (new in this sprint).

        :attr:`has_ui` remains ``False`` until the bridge wiring lands —
        the headless binding is "structurally present, semantically
        deferred" so static type checkers see the right surface but
        runtime calls fail fast with actionable errors.
        """

        runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
        return runtime.ui

    @property
    def session_manager(self) -> ReadonlySessionManager:
        """Pi ``sessionManager`` — read-only session manager view.

        Raises :class:`ExtensionError("invalid_state")` when no session is
        attached (Aelix backward-compat path per ADR-0022). Callers that
        need optional access should use ``ctx.is_idle()`` + try/except.
        """

        sm = object.__getattribute__(self, "_session_manager")
        if sm is None:
            raise ExtensionError(
                "invalid_state",
                "ExtensionContext.session_manager unavailable — "
                "no Session attached to AgentHarness.",
            )
        return sm

    @property
    def model_registry(self) -> ModelRegistry:
        """Pi ``modelRegistry`` — shared per-runtime model registry."""

        runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
        return runtime.model_registry

    @property
    def signal(self) -> Any | None:
        """Pi ``signal`` — current abort signal, or ``None`` when idle."""

        return object.__getattribute__(self, "_signal")

    def has_pending_messages(self) -> bool:
        """Pi ``hasPendingMessages`` — true when steer/follow_up queue has work."""

        return bool(object.__getattribute__(self, "_has_pending_messages")())

    def shutdown(self) -> None:
        """Pi ``shutdown`` — gracefully shut down the harness.

        Sprint 5a default raises :class:`ExtensionError("invalid_state")`;
        ADR-0042 (Sprint 5b CLI loop) supplies the real binding.
        """

        object.__getattribute__(self, "_shutdown")()

    def get_context_usage(self) -> ContextUsage | None:
        """Pi ``getContextUsage`` — last-known context window utilisation."""

        return object.__getattribute__(self, "_get_context_usage")()

    def compact(
        self,
        *,
        custom_instructions: str | None = None,
        on_complete: Callable[[Any], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> None:
        """Pi ``compact(options?)`` — fire-and-forget compaction trigger.

        Default Sprint 5a binding wraps :meth:`AgentHarness.compact` via
        :func:`asyncio.create_task` so the call returns immediately. When
        no harness binding is supplied (test seam) this is a no-op.
        """

        action = object.__getattribute__(self, "_compact_action")
        action(
            custom_instructions=custom_instructions,
            on_complete=on_complete,
            on_error=on_error,
        )


# === ExtensionAPI (concrete class with @overload narrowing, D.1.2) ===


class ExtensionAPI:
    """Handle passed to extension factories.

    Mutates a bound :class:`Extension` for registrations; delegates actions
    to the shared :class:`_ExtensionRuntime`. The 35 :meth:`on` overloads
    (Sprint 6h₅a Phase 4.14 ADR-0081 added 4 extension session lifecycle
    events on top of the Sprint 5a 31-overload baseline) mirror
    :class:`~aelix_agent_core.harness.hooks.HookBus.on` so pyright narrows
    the handler signature per ``HookEventName`` literal (see D.1.2 + the
    spike in ``scripts/pyright_spike.py``).

    ADR-0019 v3: each overload exposes ``error_mode: HookErrorMode`` with
    the default ``"throw"`` matching Pi shipped behavior.
    """

    def __init__(
        self,
        extension: Extension,
        runtime: _ExtensionRuntime,
    ) -> None:
        self._extension = extension
        self._runtime = runtime

    # --- Subscription (35 overloads — Sprint 3a 16 + Sprint 5a 12 + Sprint 6h₅a 4 = 35;
    #     Sprint 6h₅a Phase 4.14 ADR-0081 added the 4 extension session lifecycle events) ---

    @overload
    def on(
        self,
        event: Literal["context"],
        handler: ContextHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["before_agent_start"],
        handler: BeforeAgentStartHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["tool_call"],
        handler: ToolCallHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["tool_result"],
        handler: ToolResultHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["message_end"],
        handler: MessageEndHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["agent_start"],
        handler: AgentStartHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["agent_end"],
        handler: AgentEndHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["turn_start"],
        handler: TurnStartHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["turn_end"],
        handler: TurnEndHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["message_start"],
        handler: MessageStartHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["message_update"],
        handler: MessageUpdateHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["tool_execution_start"],
        handler: ToolExecutionStartHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["tool_execution_update"],
        handler: ToolExecutionUpdateHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["tool_execution_end"],
        handler: ToolExecutionEndHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_before_compact"],
        handler: SessionBeforeCompactHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["settled"],
        handler: SettledHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    # --- Sprint 3a additions (12 new overloads) ---
    @overload
    def on(
        self,
        event: Literal["queue_update"],
        handler: QueueUpdateHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["save_point"],
        handler: SavePointHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["abort"],
        handler: AbortHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["before_provider_request"],
        handler: BeforeProviderRequestHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["before_provider_payload"],
        handler: BeforeProviderPayloadHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["after_provider_response"],
        handler: AfterProviderResponseHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_compact"],
        handler: SessionCompactHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_before_tree"],
        handler: SessionBeforeTreeHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_tree"],
        handler: SessionTreeHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["model_select"],
        handler: ModelSelectHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["thinking_level_select"],
        handler: ThinkingLevelSelectHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["resources_update"],
        handler: ResourcesUpdateHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    # --- Sprint 5a (Phase 3.1) additions ---
    @overload
    def on(
        self,
        event: Literal["input"],
        handler: InputHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["user_bash"],
        handler: UserBashHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["resources_discover"],
        handler: ResourcesDiscoverHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    # --- Sprint 6h₅a (Phase 4.14, ADR-0081) additions ---
    @overload
    def on(
        self,
        event: Literal["session_start"],
        handler: SessionStartHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_before_switch"],
        handler: SessionBeforeSwitchHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_before_fork"],
        handler: SessionBeforeForkHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...
    @overload
    def on(
        self,
        event: Literal["session_shutdown"],
        handler: SessionShutdownHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]: ...

    def on(  # pyright: ignore[reportInconsistentOverload]
        self,
        event: HookEventName,
        handler: HookHandler,
        *,
        cleanup: HookCleanup | None = None,
        error_mode: HookErrorMode = "throw",
    ) -> Callable[[], None]:
        """Subscribe ``handler`` to ``event`` on this extension.

        Returns an unsubscribe callable. Handlers are stored on the bound
        :class:`Extension` and later wired into the harness ``HookBus`` when
        the harness is constructed. The unsubscribe simply removes the entry
        from the extension's handler list — if the harness has already
        registered the handler into its bus, that registration also needs
        to be torn down (the harness does this in :meth:`dispose`).

        ADR-0019 v3: ``error_mode`` defaults to ``"throw"`` matching Pi
        shipped behavior. ``"continue"`` is an Aelix additive opt-in.

        NOTE: 35 ``@overload`` declarations above provide static narrowing
        per event name (handler param typed as ``XxxHandler`` with
        ``XxxHookEvent`` payload — Sprint 6h₅a Phase 4.14 ADR-0081 added
        4 on top of the Sprint 5a 31-overload baseline). The runtime impl
        uses the generic ``HookHandler`` signature (``HookEvent`` union)
        which pyright cannot reconcile with the narrowed overloads —
        pyright lacks the contravariance proof. The narrowing is verified
        by ``scripts/pyright_spike.py`` which exercises each overload
        against a concrete handler and asserts pyright sees the narrowed
        payload type. Suppression is scoped to ``reportInconsistentOverload`` only.
        """

        if event not in HOOK_RESULT_TYPES:
            raise KeyError(f"Unknown hook event: {event!r}")
        bucket = self._extension.handlers.setdefault(event, [])
        bucket.append(handler)
        self._extension.handler_error_modes[(event, id(handler))] = error_mode
        if cleanup is not None:
            self._extension.cleanups.append(cleanup)

        def unsubscribe() -> None:
            try:
                bucket.remove(handler)
            except ValueError:
                return
            self._extension.handler_error_modes.pop((event, id(handler)), None)

        return unsubscribe

    # --- Registration ---

    def register_tool(self, tool: AgentTool) -> None:
        """Register a tool. Last write wins within a single extension.

        Application-supplied tools (``AgentHarnessOptions.tools``) win over
        extension tools at harness assembly time per D.1.13 M-9.
        """

        self._extension.tools[tool.name] = tool

    # Phase 1.3: CLI flag plumbing — currently registered but not wired to a parser.
    def register_flag(
        self,
        name: str,
        *,
        type: Literal["bool", "str"],
        default: bool | str | None = None,
        description: str | None = None,
    ) -> None:
        """Declare a flag. CLI integration is deferred to Phase 1.3+."""

        self._extension.flags[name] = ExtensionFlag(
            name=name,
            type=type,
            default=default,
            description=description,
        )

    def get_flag(self, name: str) -> bool | str | None:
        """Return the flag's current value (Phase 1.2: always the default)."""

        flag = self._extension.flags.get(name)
        if flag is None:
            return None
        return flag.default

    def add_cleanup(self, cleanup: HookCleanup) -> Callable[[], None]:
        """Register a cleanup callable. LIFO on harness dispose."""

        self._extension.cleanups.append(cleanup)

        def unregister() -> None:
            try:
                self._extension.cleanups.remove(cleanup)
            except ValueError:
                return

        return unregister

    # --- Sprint 5a (Phase 3.1) — registration mutators (P-22) ---

    def register_command(
        self,
        name: str,
        *,
        handler: Callable[..., Any],
        description: str | None = None,
    ) -> None:
        """Register a slash-style command. Pi ``registerCommand`` (``types.ts:1142``).

        Sprint 5a stores the command on :attr:`Extension.commands`; the CLI
        loop (ADR-0042) reads it back to populate slash-command completion.
        """

        self._extension.commands[name] = RegisteredCommand(
            name=name,
            handler=handler,
            description=description,
            source=self._extension.name,
        )

    def register_shortcut(
        self,
        key: str,
        *,
        handler: Callable[..., Any],
        description: str | None = None,
    ) -> None:
        """Register a keyboard shortcut. Pi ``registerShortcut`` (``types.ts:1145-1150``).

        Sprint 5a stores the shortcut on :attr:`Extension.shortcuts`; the
        TUI (Phase 5) wires the dispatch table.
        """

        self._extension.shortcuts[key] = ExtensionShortcut(
            key=key, handler=handler, description=description
        )

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: MessageRenderer,
    ) -> None:
        """Register a custom-message renderer. Pi ``registerMessageRenderer``."""

        self._extension.message_renderers[custom_type] = renderer

    def register_provider(self, name: str, config: Any) -> None:
        """Pi ``registerProvider`` (``types.ts:1292``).

        Sprint 5a queues the registration onto
        :attr:`_ExtensionRuntime.pending_provider_registrations` so the
        Phase 4 provider adapter (ADR-0038) can flush it once
        :meth:`_ExtensionRuntime.bind_model_registry` is called.
        Until then the in-process :class:`_StubModelRegistry` also tracks
        the registration so ``ctx.model_registry`` callers see something.
        """

        self._runtime.pending_provider_registrations.append((name, config))
        # Best-effort fan-out to the current in-process registry so
        # ``ctx.model_registry`` callers see the registration immediately.
        # Phase 4 (ADR-0038) replays via ``_ExtensionRuntime.bind_model_registry``.
        with contextlib.suppress(Exception):
            self._runtime.model_registry.register_provider(name, config)

    def unregister_provider(self, name: str) -> None:
        """Pi ``unregisterProvider`` (``types.ts:1305``)."""

        self._runtime.pending_provider_unregistrations.append(name)
        # Also drop any matching pending registration so order is preserved.
        self._runtime.pending_provider_registrations = [
            (n, c) for n, c in self._runtime.pending_provider_registrations
            if n != name
        ]
        with contextlib.suppress(Exception):
            self._runtime.model_registry.unregister_provider(name)

    # --- Sprint 5a actions — delegate to ExtensionRuntimeActions stubs (P-22) ---

    def send_message(
        self,
        message: Any,
        *,
        trigger_turn: bool = False,
        deliver_as: Literal["steer", "follow_up", "next_turn"] | None = None,
    ) -> None:
        """Pi ``sendMessage`` (``types.ts:1178-1182``). Throwing stub in Sprint 5a."""

        self._runtime.assert_active()
        self._runtime.actions.send_message(
            message, trigger_turn=trigger_turn, deliver_as=deliver_as
        )

    def send_user_message(
        self,
        content: Any,
        *,
        deliver_as: Literal["steer", "follow_up"] | None = None,
    ) -> None:
        """Pi ``sendUserMessage`` (``types.ts:1190-1192``). Throwing stub in 5a."""

        self._runtime.assert_active()
        self._runtime.actions.send_user_message(content, deliver_as=deliver_as)

    def append_entry(self, custom_type: str, data: Any = None) -> None:
        """Pi ``appendEntry`` (``types.ts:1195``). Throwing stub in Sprint 5a."""

        self._runtime.assert_active()
        self._runtime.actions.append_entry(custom_type, data)

    def set_session_name(self, name: str) -> None:
        """Pi ``setSessionName`` (``types.ts:1200``)."""

        self._runtime.assert_active()
        self._runtime.actions.set_session_name(name)

    def get_session_name(self) -> str | None:
        """Pi ``getSessionName`` (``types.ts:1203``)."""

        self._runtime.assert_active()
        return self._runtime.actions.get_session_name()

    def set_label(self, entry_id: str, label: str | None) -> None:
        """Pi ``setLabel`` (``types.ts:1206``)."""

        self._runtime.assert_active()
        self._runtime.actions.set_label(entry_id, label)

    async def exec(
        self,
        command: str,
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_ms: int | None = None,
    ) -> ExecResult:
        """Pi ``exec`` (``types.ts:1209``) — direct port of ``exec.ts execCommand``.

        Sprint 5a binds this through :class:`subprocess.run` regardless of
        whether the harness has supplied an override, so an extension can
        shell out at setup time. The Sprint 5b CLI may install a richer
        action (signal handling / streaming output) via
        :class:`ExtensionRuntimeActions.exec`.
        """

        self._runtime.assert_active()
        # Sprint 5a: exec is one of the few actions that has a real Sprint-5a
        # binding regardless of harness — the spec §B table requires the
        # subprocess port to land in 5a. Sprint 5b / Phase 5 may install a
        # richer streaming variant via ``ExtensionRuntimeActions.exec``; we
        # detect that by attempting the bound action first and using its
        # ``unbound`` stub error as the signal to run the local port.
        action = self._runtime.actions.exec
        try:
            return await action(
                command, args, cwd=cwd, env=env, timeout_ms=timeout_ms
            )
        except ExtensionError as exc:
            if exc.code != "unbound":
                raise

        # Sprint 5a default — in-process subprocess port of Pi ``execCommand``.
        timeout_s: float | None = (timeout_ms / 1000) if timeout_ms else None
        env_dict = dict(os.environ)
        if env is not None:
            env_dict.update(env)
        killed = False
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [command, *args],
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env_dict,
                timeout=timeout_s,
                check=False,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            killed = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            code = 124
        except FileNotFoundError as exc:
            stdout = ""
            stderr = str(exc)
            code = 127
        return ExecResult(stdout=stdout, stderr=stderr, code=code, killed=killed)

    def get_all_tools(self) -> list[ToolInfo]:
        """Pi ``getAllTools`` (``types.ts:1215``).

        Sprint 5a: snapshot the harness ``_tools`` list as a
        :class:`ToolInfo` view. When the harness hasn't bound this action
        the stub returns an empty list (matches Pi's "no tools yet" pre-init
        behaviour).
        """

        self._runtime.assert_active()
        try:
            return list(self._runtime.actions.get_all_tools())
        except ExtensionError as exc:
            if exc.code == "unbound":
                return []
            raise

    def get_commands(self) -> list[SlashCommandInfo]:
        """Pi ``getCommands`` (``types.ts:1221``).

        Sprint 5a returns ``[]`` — the slash-command registry binding is
        owned by ADR-0042 (Sprint 5b CLI loop).
        """

        self._runtime.assert_active()
        try:
            return list(self._runtime.actions.get_commands())
        except ExtensionError as exc:
            if exc.code == "unbound":
                return []
            raise

    async def set_model(self, model: Model) -> bool:
        """Pi ``setModel`` (``types.ts:1228``)."""

        self._runtime.assert_active()
        result = self._runtime.actions.set_model(model)
        if asyncio.iscoroutine(result):
            return bool(await result)
        return bool(result)

    def get_thinking_level(self) -> str:
        """Pi ``getThinkingLevel`` (``types.ts:1231``)."""

        self._runtime.assert_active()
        return self._runtime.actions.get_thinking_level()

    def set_thinking_level(self, level: str) -> None:
        """Pi ``setThinkingLevel`` (``types.ts:1234``)."""

        self._runtime.assert_active()
        self._runtime.actions.set_thinking_level(level)

    # --- Actions (Sprint 3a baseline — delegate to runtime) ---

    def get_active_tools(self) -> list[str]:
        self._runtime.assert_active()
        return list(self._runtime.actions.get_active_tools())

    def set_active_tools(self, tool_names: list[str]) -> None:
        self._runtime.assert_active()
        self._runtime.actions.set_active_tools(list(tool_names))

    def get_system_prompt(self) -> str:
        self._runtime.assert_active()
        return self._runtime.actions.get_system_prompt()

    # --- Sprint 5a — events property (P-22 #18) ---

    @property
    def events(self) -> EventBus:
        """Pi ``events: EventBus`` — shared per-runtime event bus."""

        return self._runtime.event_bus

    # --- Internal helpers ---

    @property
    def extension(self) -> Extension:
        return self._extension

    @property
    def runtime(self) -> _ExtensionRuntime:
        return self._runtime


__all__ = [
    "ContextUsage",
    "EventBus",
    "ExecResult",
    "Extension",
    "ExtensionAPI",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionFactory",
    "ExtensionFlag",
    "ExtensionRuntimeActions",
    "ExtensionShortcut",
    "ExtensionSourceInfo",
    "MessageRenderer",
    "ModelRegistry",
    "ReadonlySessionManager",
    "RegisteredCommand",
    "SlashCommandInfo",
    "ToolInfo",
    "_ExtensionRuntime",
    "_StubModelRegistry",
]
