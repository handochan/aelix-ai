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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, overload

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
    MessageEndHandler,
    MessageStartHandler,
    MessageUpdateHandler,
    ModelSelectHandler,
    QueueUpdateHandler,
    ResourcesUpdateHandler,
    SavePointHandler,
    SessionBeforeCompactHandler,
    SessionBeforeTreeHandler,
    SessionCompactHandler,
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
)
from aelix_agent_core.types import AgentTool
from aelix_ai.streaming import Model

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


# === Runtime: throwing-stub actions, replaced at bind_core ===


@dataclass
class ExtensionRuntimeActions:
    """The action table the harness installs via :meth:`bind_core`.

    Each callable is exposed on :class:`ExtensionAPI`. The defaults are
    throwing stubs created by :func:`_make_throwing_stub`.
    """

    get_active_tools: Callable[[], list[str]]
    set_active_tools: Callable[[list[str]], None]
    get_system_prompt: Callable[[], str]


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
    )


class _ExtensionRuntime:
    """Tracks liveness and holds the rebindable action table.

    One runtime is created per ``load_extensions`` call (D.1.7) and shared
    by every ExtensionAPI it spawns. :meth:`invalidate` flips the runtime
    into a stale state — every subsequent context attribute access raises
    :class:`ExtensionError` with code ``"stale"``.
    """

    def __init__(self) -> None:
        self._actions: ExtensionRuntimeActions = _default_actions()
        self._stale_message: str | None = None

    @property
    def actions(self) -> ExtensionRuntimeActions:
        return self._actions

    @property
    def is_stale(self) -> bool:
        return self._stale_message is not None

    def bind_core(self, actions: ExtensionRuntimeActions) -> None:
        """Install real action implementations (called by AgentHarness)."""

        self._actions = actions

    def invalidate(self, message: str = "extension runtime has been disposed") -> None:
        self._stale_message = message

    def assert_active(self) -> None:
        if self._stale_message is not None:
            raise ExtensionError("stale", self._stale_message)


# === Extension container ===


@dataclass
class ExtensionFlag:
    """Declarative flag registered by an extension."""

    name: str
    type: Literal["bool", "str"]
    default: bool | str | None = None
    description: str | None = None


@dataclass
class Extension:
    """The mutable record the loader populates while a factory runs.

    ``handler_error_modes`` (ADR-0019 v3) carries the per-handler error
    policy keyed by ``(event_name, id(handler))``; the harness threads these
    into :class:`HookBus` registration when wiring the extension.
    """

    name: str
    handlers: dict[HookEventName, list[HookHandler]] = field(default_factory=dict)
    tools: dict[str, AgentTool] = field(default_factory=dict)
    flags: dict[str, ExtensionFlag] = field(default_factory=dict)
    cleanups: list[HookCleanup] = field(default_factory=list)
    handler_error_modes: dict[tuple[HookEventName, int], HookErrorMode] = field(
        default_factory=dict
    )


ExtensionFactory = Callable[["ExtensionAPI"], Any]


# === ExtensionContext (concrete with stale guard, D.1.4) ===


class ExtensionContext:
    """Read-only view passed to every hook handler.

    Per D.1.4 this is a concrete class — not a Protocol — so ``__getattribute__``
    can call :meth:`_ExtensionRuntime.assert_active` before resolving any
    non-internal attribute. Internal attributes are anything starting with
    an underscore plus the explicit allowlist (``assert_active``).
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

    def __getattribute__(self, name: str) -> Any:
        # Allow private/dunder access without the staleness check.
        if name.startswith("_") or name in ExtensionContext._INTERNAL_NAMES:
            return object.__getattribute__(self, name)
        runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
        runtime.assert_active()
        return object.__getattribute__(self, name)

    # --- Public surface ---

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


# === ExtensionAPI (concrete class with @overload narrowing, D.1.2) ===


class ExtensionAPI:
    """Handle passed to extension factories.

    Mutates a bound :class:`Extension` for registrations; delegates actions
    to the shared :class:`_ExtensionRuntime`. The 28 :meth:`on` overloads
    (Sprint 3a) mirror :class:`~aelix_agent_core.harness.hooks.HookBus.on`
    so pyright narrows the handler signature per ``HookEventName`` literal
    (see D.1.2 + the spike in ``scripts/pyright_spike.py``).

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

    # --- Subscription (28 overloads — Sprint 3a) ---

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

        NOTE: 28 ``@overload`` declarations above provide static narrowing
        per event name (handler param typed as ``XxxHandler`` with
        ``XxxHookEvent`` payload). The runtime impl uses the generic
        ``HookHandler`` signature (``HookEvent`` union) which pyright cannot
        reconcile with the narrowed overloads — pyright lacks the
        contravariance proof. The narrowing is verified by
        ``scripts/pyright_spike.py`` which exercises each overload against
        a concrete handler and asserts pyright sees the narrowed payload
        type. Suppression is scoped to ``reportInconsistentOverload`` only.
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

    # --- Actions (delegate to runtime) ---

    def get_active_tools(self) -> list[str]:
        self._runtime.assert_active()
        return list(self._runtime.actions.get_active_tools())

    def set_active_tools(self, tool_names: list[str]) -> None:
        self._runtime.assert_active()
        self._runtime.actions.set_active_tools(list(tool_names))

    def get_system_prompt(self) -> str:
        self._runtime.assert_active()
        return self._runtime.actions.get_system_prompt()

    # --- Internal helpers ---

    @property
    def extension(self) -> Extension:
        return self._extension

    @property
    def runtime(self) -> _ExtensionRuntime:
        return self._runtime


__all__ = [
    "Extension",
    "ExtensionAPI",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionFactory",
    "ExtensionFlag",
    "ExtensionRuntimeActions",
    "_ExtensionRuntime",
]
