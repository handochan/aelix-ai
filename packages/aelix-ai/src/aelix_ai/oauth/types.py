"""OAuth client framework — types (Sprint 6c · Phase 4.3 · §B).

Pi parity: ``packages/ai/src/utils/oauth/types.ts`` (SHA 734e08e).

Mirrors Pi's ``OAuthCredentials`` flat-with-extensible shape, the
``OAuthProviderInterface`` Protocol (renamed ``OAuthProvider`` here to
match the Aelix Protocol naming convention; Pi's deprecated alias is
purely TypeScript noise), and the callback bundle that ships into
:func:`login_anthropic` and friends.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# Pi parity: ``coding-agent/src/core/auth-storage.ts:38`` — the 6-value
# source enum that ``getAuthStatus`` returns. Sprint 6e wires the first
# four (stored/runtime/environment/fallback); ``models_json_*`` are
# tracked but unused until models.json plumbing lands in Sprint 7+.
AuthSource = Literal[
    "stored",
    "runtime",
    "environment",
    "fallback",
    "models_json_key",
    "models_json_command",
]

# Pi parity: ``auth-storage.ts:194`` ``fallbackResolver?: (provider) =>
# string | undefined``. Sprint 6e wires this for AuthStorage's last-resort
# cascade layer (Sprint 7+ uses it for models.json custom-provider keys).
FallbackResolver = Callable[[str], "str | None"]


@dataclass
class OAuthCredentials:
    """Pi parity: ``utils/oauth/types.ts:3-8``.

    Schema: ``refresh + access + expires + extensible extra``. ``expires``
    is unix-ms with a 5-min safety margin baked in at exchange time
    (Pi ``Date.now() + expires_in*1000 - 5*60*1000``).

    The ``extra`` field carries provider-specific fields (Copilot uses
    ``endpoint``; Codex uses ``id_token`` / ``account_id`` / ``scope``)
    so the dataclass round-trips Pi's ``[key: string]: unknown`` index
    signature via :meth:`to_json` / :meth:`from_json`.
    """

    refresh: str
    access: str
    expires: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Serialize to flat dict (Pi shape) — extras merged at top level."""

        return {
            "refresh": self.refresh,
            "access": self.access,
            "expires": self.expires,
            **self.extra,
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> OAuthCredentials:
        """Parse a Pi-shape dict; unknown keys land in :attr:`extra`.

        Sprint 6c W6 (W4 m1): raises a clear :class:`ValueError` with
        the missing field names instead of bubbling a bare
        :class:`KeyError`. Callers (CLI, harness adapter) get an
        actionable diagnostic.
        """

        known = {"refresh", "access", "expires"}
        missing = known - obj.keys()
        if missing:
            raise ValueError(
                f"OAuthCredentials missing required fields: {sorted(missing)}"
            )
        return cls(
            refresh=str(obj["refresh"]),
            access=str(obj["access"]),
            expires=int(obj["expires"]),
            extra={k: v for k, v in obj.items() if k not in known},
        )


@dataclass(frozen=True)
class AuthStatus:
    """Pi parity: ``coding-agent/src/core/auth-storage.ts:36-40`` ``AuthStatus``.

    Reports whether a provider has credentials available without
    exposing the credential value itself or triggering OAuth refresh.
    ``configured`` is True only for the ``stored`` source (Pi parity:
    ``getAuthStatus`` only returns ``configured: true`` for stored
    credentials; runtime/env/fallback sources are reported but flagged
    as not-yet-persisted).
    """

    configured: bool
    source: AuthSource | None = None
    label: str | None = None


@dataclass
class OAuthPrompt:
    """Pi parity: ``types.ts:15-19`` ``OAuthPrompt``."""

    message: str
    placeholder: str | None = None
    allow_empty: bool = False


@dataclass
class OAuthAuthInfo:
    """Pi parity: ``types.ts:21-24`` ``OAuthAuthInfo``."""

    url: str
    instructions: str | None = None


@dataclass
class OAuthSelectOption:
    """Pi parity: ``types.ts:26-29`` ``OAuthSelectOption``."""

    id: str
    label: str


@dataclass
class OAuthSelectPrompt:
    """Pi parity: ``types.ts:31-34`` ``OAuthSelectPrompt``."""

    message: str
    options: list[OAuthSelectOption]


@dataclass
class OAuthLoginCallbacks:
    """Pi parity: ``types.ts:36-44`` ``OAuthLoginCallbacks``.

    All callbacks are sync-or-async; the framework awaits any coroutine
    result via :func:`_maybe_await` (Sprint 6a pattern from
    ``providers/anthropic.py``).

    ``signal`` is the Aelix equivalent of Pi's ``AbortSignal`` — we type
    it ``Any`` so callers can pass an :class:`asyncio.Event`-shaped
    object, an SDK ``AbortSignal``, or :data:`None`. The Anthropic OAuth
    flow does not currently consult ``signal`` (Pi parity); it lands in
    Sprint 6e alongside the Copilot/Codex flows.
    """

    on_auth: Callable[[OAuthAuthInfo], None | Awaitable[None]]
    on_prompt: Callable[[OAuthPrompt], str | Awaitable[str]]
    on_progress: Callable[[str], None | Awaitable[None]] | None = None
    on_manual_code_input: Callable[[], str | Awaitable[str]] | None = None
    on_select: (
        Callable[[OAuthSelectPrompt], str | None | Awaitable[str | None]] | None
    ) = None
    signal: Any | None = None


@runtime_checkable
class OAuthProvider(Protocol):
    """Pi parity: ``types.ts:46-64`` ``OAuthProviderInterface``.

    Aelix names it ``OAuthProvider`` (matches the Aelix Protocol naming
    convention in ``api_registry``); Pi's deprecated ``OAuthProvider``
    alias is purely TypeScript noise (it shadows ``OAuthProviderId``).

    Sprint 6c forward-compat clause (spec §J): ``modify_models`` is
    declared as ``Optional[Callable]`` but unwired in 6c — Sprint 6e
    wires it for Copilot (which needs base URL injection per copilot
    subscription tier).
    """

    id: str
    name: str
    uses_callback_server: bool

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...

    async def refresh_token(
        self, credentials: OAuthCredentials
    ) -> OAuthCredentials: ...

    def get_api_key(self, credentials: OAuthCredentials) -> str: ...


__all__ = [
    "AuthSource",
    "AuthStatus",
    "FallbackResolver",
    "OAuthAuthInfo",
    "OAuthCredentials",
    "OAuthLoginCallbacks",
    "OAuthPrompt",
    "OAuthProvider",
    "OAuthSelectOption",
    "OAuthSelectPrompt",
]
