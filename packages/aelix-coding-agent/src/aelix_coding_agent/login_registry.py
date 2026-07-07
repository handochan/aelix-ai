"""Extension-contributed login providers (Issue #77).

An extension can add its OWN entry to the interactive ``/login`` method list —
e.g. a corporate provider ``telnaut`` whose sign-in is "enter your employee
number" — by registering a :class:`LoginProvider`. When the user picks it in the
wizard, its :attr:`LoginProvider.authenticate` handler runs, driving whatever
custom credential prompts it wants through a :class:`LoginContext` (the SAME
masked ``select`` / ``prompt`` / ``confirm`` / ``notify`` dialogs the built-in
sub-flows use), and returns the credential string. The wizard then persists it
via ``auth_storage`` under the provider id — the extension never touches the
protected auth store directly.

This mirrors the OAuth registry (``aelix_ai.oauth._registry``): a small
PROCESS-GLOBAL store the ``/login`` wizard reads at open time. It is populated by
:meth:`ExtensionAPI.register_login_provider` (queued + replayed on every harness
(re)build via ``_ExtensionRuntime.bind_login_registries``). Being process-global,
registrations from one session are visible to another in the same process —
tests must call :func:`reset_login_providers`, and an extension that is removed on
``/reload`` must ``unregister_login_provider`` in its teardown.

Turn-time model access is a SEPARATE concern: pair this with
``ExtensionAPI.register_provider(name, ProviderConfigInput(...))`` so the same
provider id resolves an adapter + (with ``models=``) surfaces rows in ``/model``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class LoginContext:
    """Dialog primitives handed to a :class:`LoginProvider.authenticate` handler.

    A thin wrapper over the ``/login`` wizard's injected TUI callables so a
    custom login flow can drive ``select`` / ``prompt`` (with secret masking) /
    ``confirm`` / ``notify`` WITHOUT importing any protected TUI internals. All
    methods are interactive-only — an extension login flow only runs when the
    wizard runs (an interactive TTY).
    """

    def __init__(
        self,
        *,
        select: Callable[..., Awaitable[str | None]],
        prompt: Callable[..., Awaitable[str | None]],
        confirm: Callable[..., Awaitable[bool]],
        notify: Callable[..., None],
    ) -> None:
        self._select = select
        self._prompt = prompt
        self._confirm = confirm
        self._notify = notify

    async def select(self, message: str, options: list[str]) -> str | None:
        """Pick one option; ``None`` when the user cancels (Esc)."""

        return await self._select(message, options)

    async def prompt(
        self, message: str, *, placeholder: str | None = None, password: bool = False
    ) -> str | None:
        """Read a line of input. ``password=True`` masks the echo (secrets).

        Returns ``None`` when the user cancels (Esc).
        """

        return await self._prompt(message, placeholder=placeholder, password=password)

    async def confirm(self, title: str, message: str) -> bool:
        """Yes/no confirmation dialog."""

        return await self._confirm(title, message)

    def notify(self, message: str, *, kind: str = "info") -> None:
        """Emit a transient status line (info / warning / error)."""

        self._notify(message, kind=kind)


# The authenticate handler: given a LoginContext, drive the custom sign-in and
# return the credential string to store under the provider id, or None to cancel.
LoginAuthenticate = Callable[[LoginContext], Awaitable[str | None]]


@dataclass(frozen=True)
class LoginProvider:
    """An extension-contributed ``/login`` method.

    - ``id`` — the provider id the credential is stored under (must match the
      ``register_provider`` name so turns authenticate against it).
    - ``name`` — the label shown in the ``/login`` method list.
    - ``authenticate`` — an async handler ``(LoginContext) -> str | None`` that
      collects credentials and returns the value to persist (``None`` = cancel).
    """

    id: str
    name: str
    authenticate: LoginAuthenticate
    # Reserved for future metadata (e.g. an icon / description) without a
    # breaking signature change; ignored today.
    meta: dict[str, Any] = field(default_factory=dict)


# Process-global store (mirrors aelix_ai.oauth._registry). Keyed by provider id;
# last-write-wins so an extension re-registering on /reload replaces cleanly.
_REGISTRY: dict[str, Any] = {}


def register_login_provider(provider: Any) -> None:
    """Register (or replace) a login provider by its ``id``.

    Duck-typed: ``provider`` need only expose ``id`` / ``name`` / ``authenticate``
    (a :class:`LoginProvider` is the canonical shape). A provider with no ``id``
    is ignored rather than raising — registration must never crash extension
    setup.
    """

    provider_id = getattr(provider, "id", None)
    if not provider_id:
        return
    _REGISTRY[str(provider_id)] = provider


def unregister_login_provider(provider_id: str) -> None:
    """Remove a login provider by id (no-op if absent)."""

    _REGISTRY.pop(str(provider_id), None)


def get_login_providers() -> list[Any]:
    """All registered login providers, in registration order (dict insertion)."""

    return list(_REGISTRY.values())


def reset_login_providers() -> None:
    """Clear the registry — for test isolation (the store is process-global)."""

    _REGISTRY.clear()


__all__ = [
    "LoginAuthenticate",
    "LoginContext",
    "LoginProvider",
    "get_login_providers",
    "register_login_provider",
    "reset_login_providers",
    "unregister_login_provider",
]
