"""ModelRegistry runtime — Sprint 6f W2 (ADR-0065).

Pi parity: ``packages/coding-agent/src/core/model-registry.ts``
(SHA 734e08e, 820 LOC subset). Sprint 6f₁ ships the RUNTIME 14 public
methods. Sprint 6g ports the full catalog (``models.generated.ts`` 428
KB), the ``models.json`` schema validator + comment-stripping
(TypeBox-equivalent), and ``model-resolver.ts`` (~530 LOC — partial-id
matching + provider auto-detect) per spec §J.

Surface (Pi parity):

- Factory: :meth:`ModelRegistry.create`, :meth:`ModelRegistry.in_memory`
- Model access: :meth:`get_all`, :meth:`get_available`, :meth:`find`
- Auth resolution: :meth:`has_configured_auth`,
  :meth:`get_api_key_and_headers`, :meth:`get_api_key_for_provider`,
  :meth:`get_provider_auth_status`, :meth:`is_using_oauth`
- Lifecycle: :meth:`refresh`, :meth:`get_error`
- Dynamic registration: :meth:`register_provider`,
  :meth:`unregister_provider`
- Display: :meth:`get_provider_display_name`

The constructor takes an :class:`AuthStorage` (Sprint 6c+6e) +
optional ``models_json_path`` (raises :class:`NotImplementedError`
until Sprint 6g).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.models import get_models, get_providers
from aelix_ai.oauth import AuthStorage
from aelix_ai.oauth.types import AuthStatus, OAuthProvider
from aelix_ai.streaming import Model


@dataclass
class ResolvedRequestAuth:
    """Pi parity: ``model-registry.ts::ResolvedRequestAuth``.

    Wire shape:

    - ``ok=True``: ``{ok: True, api_key?: str, headers: dict[str, str]}``.
      ``api_key`` may be :data:`None` for OAuth-only providers that
      attach the bearer token via headers.
    - ``ok=False``: ``{ok: False, error: str}``.
    """

    ok: bool
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ProviderConfigInput:
    """Pi parity: ``model-registry.ts::ProviderConfigInput`` (subset).

    Sprint 6f₁ ships the runtime-relevant fields; Sprint 6g wires the
    ``models.json`` schema (``apiKey`` env-var indirection,
    ``auth_header`` selection, full ``oauth`` registration).

    Sprint 6f W6 (P-180): ``auth_header`` is :class:`bool` to match
    Pi ``ProviderConfigInput.authHeader: boolean | undefined`` (Pi
    ``model-registry.ts:68``). The Sprint 6f W2 ``str | None`` type
    leaked the Sprint 6c stored-header-name semantics into the new
    config shape; Pi treats ``authHeader`` as a switch for
    Authorization-vs-x-api-key, not a header name.
    """

    api_key: str | None = None
    headers: dict[str, str] | None = None
    auth_header: bool | None = None
    oauth: OAuthProvider | None = None
    # Custom catalog entries — Sprint 6g wires the merge path.
    models: dict[str, Model] | None = None


# Pi parity: ``model-registry.ts`` — provider display names. Sprint 6f₁
# ships a minimal lookup; Sprint 6g aggregates from registered
# ProviderConfigInput entries.
_BUILT_IN_DISPLAY_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "github-copilot": "GitHub Copilot",
    "openai-codex": "OpenAI Codex",
}


class ModelRegistry:
    """Pi parity: ``coding-agent/src/core/model-registry.ts:ModelRegistry``.

    Sprint 6f₁ surface — 14 public methods. The ``models.json`` loader
    (Pi ``loadCustomModels(path)``) is deferred to Sprint 6g; passing
    a non-:data:`None` ``models_json_path`` raises
    :class:`NotImplementedError`.
    """

    def __init__(
        self,
        auth_storage: AuthStorage,
        models_json_path: str | None = None,
    ) -> None:
        # Pi parity: constructor stores ``authStorage`` + ``modelsJsonPath``
        # + ``modifyModelsCallbacks``. Sprint 6f₁ ships in-memory only.
        if models_json_path is not None:
            raise NotImplementedError(
                "models.json loading deferred to Sprint 6g "
                "(ADR-0065 §Carry-forward / ADR-0066)"
            )
        self._auth_storage = auth_storage
        self._models_json_path = models_json_path
        self._models: list[Model] = []
        self._provider_request_configs: dict[str, ProviderConfigInput] = {}
        self._registered_providers: dict[str, ProviderConfigInput] = {}
        self._load_error: str | None = None
        self._load_models()

    # ── Factories ──────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        auth_storage: AuthStorage,
        models_json_path: str | None = None,
    ) -> ModelRegistry:
        """Pi parity: ``model-registry.ts::ModelRegistry.create``."""

        return cls(auth_storage, models_json_path)

    @classmethod
    def in_memory(cls, auth_storage: AuthStorage) -> ModelRegistry:
        """Pi parity: ``model-registry.ts::ModelRegistry.inMemory``.

        In-memory registry (no ``models.json`` path). Sprint 6f₁'s
        canonical factory until Sprint 6g lands the disk loader.
        """

        return cls(auth_storage, None)

    # ── Model access ───────────────────────────────────────────────
    def get_all(self) -> list[Model]:
        """Pi parity: ``model-registry.ts::getAll``."""

        return list(self._models)

    def get_available(self) -> list[Model]:
        """Pi parity: ``model-registry.ts::getAvailable``.

        Filters :meth:`get_all` to models for which
        :meth:`has_configured_auth` returns True. Insertion order is
        preserved (matches Pi Map iteration order — the seed catalog
        defines the canonical order for ``cycle_model`` rotation).
        """

        return [m for m in self._models if self.has_configured_auth(m)]

    def find(self, provider: str, model_id: str) -> Model | None:
        """Pi parity: ``model-registry.ts::find``."""

        for m in self._models:
            if m.provider == provider and m.id == model_id:
                return m
        return None

    # ── Auth resolution ────────────────────────────────────────────
    def has_configured_auth(self, model: Model) -> bool:
        """Pi parity: ``model-registry.ts::hasConfiguredAuth``.

        Returns :data:`True` if ANY auth layer has a key for
        ``model.provider`` (runtime override, stored credential, env
        var, registered ProviderConfigInput, fallback resolver). Does
        NOT trigger OAuth refresh.

        Implementation note: this is a sync method (matches Pi). It
        consults :class:`AuthStorage` state plus the dynamic provider
        registry — both are sync-readable on the Aelix runtime.
        """

        provider = model.provider
        # Runtime override / stored / env / fallback via AuthStorage.
        # AuthStorage stores its runtime overrides + stored credentials
        # in-memory; the env / fallback layers are sync-readable.
        if provider in self._auth_storage._runtime_overrides:
            return True
        if self._auth_storage.has(provider):
            return True
        from aelix_ai.providers._env_api_keys import get_env_api_key

        if get_env_api_key(provider):
            return True
        # Dynamic registration: ProviderConfigInput.api_key or oauth.
        config = self._registered_providers.get(provider)
        if config is not None:
            if config.api_key:
                return True
            if config.oauth is not None:
                return True
        # Pi parity: fallback resolver consulted for has_configured_auth.
        fallback = self._auth_storage._fallback_resolver
        if fallback is not None:
            try:
                if fallback(provider):
                    return True
            except Exception:  # noqa: BLE001
                # Errors are accumulated on the storage when invoked via
                # ``has_auth`` (Sprint 6e); ``has_configured_auth`` is
                # sync so we swallow silently to match Pi.
                pass
        return False

    async def get_api_key_and_headers(self, model: Model) -> ResolvedRequestAuth:
        """Pi parity: ``model-registry.ts::getApiKeyAndHeaders``.

        Returns a :class:`ResolvedRequestAuth` carrying:

        - ``api_key``: resolved via :meth:`AuthStorage.get_api_key_cascade`
          (Sprint 6e contract, ``include_fallback=False`` per Pi).
        - ``headers``: merged from per-provider ``ProviderConfigInput``
          + OAuth provider override (Copilot adds ``COPILOT_HEADERS``
          via the provider's :meth:`OAuthProvider.modify_models`
          callback; the registry only forwards what
          :class:`ProviderConfigInput` carries here).

        Sprint 6f₁ ships the canonical bridge — Sprint 6g extends with
        models.json-driven header packs.
        """

        provider = model.provider
        api_key = await self._auth_storage.get_api_key_cascade(
            provider, include_fallback=False
        )
        headers: dict[str, str] = {}
        # Pi parity: ProviderConfigInput.headers merged onto the
        # outgoing request.
        config = self._registered_providers.get(provider)
        if config is not None and config.headers is not None:
            headers.update(config.headers)
        if api_key is None:
            return ResolvedRequestAuth(
                ok=False,
                error=f"No configured auth for provider: {provider}",
            )
        return ResolvedRequestAuth(ok=True, api_key=api_key, headers=headers)

    async def get_api_key_for_provider(self, provider: str) -> str | None:
        """Pi parity: ``model-registry.ts::getApiKeyForProvider``.

        Thin wrapper over :meth:`AuthStorage.get_api_key_cascade` so the
        registry is the single source of truth for resolved keys.
        """

        return await self._auth_storage.get_api_key_cascade(
            provider, include_fallback=False
        )

    async def get_provider_auth_status(self, provider: str) -> AuthStatus:
        """Pi parity: ``model-registry.ts::getProviderAuthStatus``.

        Delegates to :meth:`AuthStorage.get_auth_status` for the
        layered-source resolution (stored / runtime / environment /
        fallback). Reports source WITHOUT exposing the credential value
        or refreshing OAuth tokens.
        """

        return await self._auth_storage.get_auth_status(provider)

    def is_using_oauth(self, model: Model) -> bool:
        """Pi parity: ``model-registry.ts::isUsingOAuth``.

        Pi behavior (verbatim): ``cred?.type === "oauth"`` — a single
        check against the AuthStorage discriminator. Sprint 6f W6
        (P-176) drops the Sprint 6f W2 ``get_oauth_provider(provider)
        is None`` early-return so the registry trusts the storage
        discriminator exclusively. A provider with stored OAuth
        credentials but no registered ``OAuthProvider`` still reports
        ``True`` (matches Pi semantics — the registration table is
        about login orchestration, not credential typing).
        """

        provider = model.provider
        entry = self._auth_storage._data.get(provider)
        if entry is None:
            return False
        return entry.get("type") == "oauth"

    # ── Lifecycle ──────────────────────────────────────────────────
    def refresh(self) -> None:
        """Pi parity: ``model-registry.ts::refresh``.

        Reloads built-in models from :mod:`aelix_ai.models` + re-applies
        OAuth ``modify_models`` callbacks for every registered OAuth
        provider that has live credentials in :class:`AuthStorage`.
        Sprint 6g extends this with disk reload (``models.json``).
        """

        self._load_models()

    def get_error(self) -> str | None:
        """Pi parity: ``model-registry.ts::getError``.

        Returns the last load-pipeline error (Sprint 6g surfaces
        models.json parse failures here) or :data:`None`.
        """

        return self._load_error

    # ── Dynamic registration ───────────────────────────────────────
    def register_provider(
        self, name: str, config: ProviderConfigInput
    ) -> None:
        """Pi parity: ``model-registry.ts::registerProvider``.

        Dynamically registers a provider config (typically backing a
        custom OAuth integration). Sprint 6f₁ ships the in-memory dict
        update; Sprint 6g wires the models.json-driven shape.
        """

        self._registered_providers[name] = config
        # Pi parity: re-run loadModels so modify_models callbacks pick
        # up the new provider's catalog entries.
        self._load_models()

    def unregister_provider(self, name: str) -> None:
        """Pi parity: ``model-registry.ts::unregisterProvider``."""

        self._registered_providers.pop(name, None)
        self._load_models()

    # ── Display ────────────────────────────────────────────────────
    def get_provider_display_name(self, provider: str) -> str:
        """Pi parity: ``model-registry.ts::getProviderDisplayName``.

        Sprint 6f₁ returns a built-in title-case mapping for the
        known providers + a Python ``str.title()`` fallback for
        unknown names. Sprint 6g pulls the display name from the
        ProviderConfigInput / registered OAuth provider config (Pi's
        canonical source) once :class:`ProviderConfigInput.name` is
        wired through models.json.
        """

        if provider in _BUILT_IN_DISPLAY_NAMES:
            return _BUILT_IN_DISPLAY_NAMES[provider]
        config = self._registered_providers.get(provider)
        if config is not None and config.oauth is not None:
            return config.oauth.name
        return provider.title()

    # ── Loading pipeline ───────────────────────────────────────────
    def _load_models(self) -> None:
        """Pi parity: ``model-registry.ts::loadModels``.

        Pipeline:

        1. Load built-ins from :func:`aelix_ai.models.get_providers` +
           :func:`aelix_ai.models.get_models` (Pi
           ``loadBuiltInModels``).
        2. (Sprint 6g) Load custom from :data:`_models_json_path`.
        3. Apply OAuth ``modify_models`` callback for every registered
           OAuth provider that has live credentials in
           :class:`AuthStorage` (Pi P-132).

        Sprint 6f W6 (P-175): ``_load_error`` is cleared at the top
        of every call so a successful reload after a transient failure
        actually drops the stale error string. Multiple provider
        failures within one pass are joined with newlines so
        :meth:`get_error` surfaces every cause.
        """

        # P-175: reset error state before reloading so successful
        # refreshes drop stale failures. Multi-provider failures
        # within this pass accumulate via newline join below.
        self._load_error = None

        # Step 1: built-ins.
        loaded: list[Model] = []
        for provider in get_providers():
            loaded.extend(get_models(provider))

        # Step 2: Sprint 6g — load custom from models.json.

        # Step 3: OAuth modify_models callbacks (Pi P-132 wire-up).
        # Consult every registered OAuth provider; if AuthStorage has a
        # live credential AND the provider exposes ``modify_models``,
        # invoke it. Pi parity: per-provider error swallowed onto
        # ``_load_error`` for ``get_error()`` retrieval.
        for oauth_provider in self._registered_oauth_providers():
            modify = getattr(oauth_provider, "modify_models", None)
            if not callable(modify):
                continue
            creds = self._read_oauth_credentials_sync(oauth_provider.id)
            if creds is None:
                continue
            try:
                loaded = modify(loaded, creds)
            except Exception as exc:  # noqa: BLE001
                err_line = (
                    f"modify_models failed for provider "
                    f"{oauth_provider.id!r}: {exc}"
                )
                # P-175 (multi-provider): append rather than overwrite
                # so :meth:`get_error` surfaces every modify_models
                # failure within this pass.
                if self._load_error is None:
                    self._load_error = err_line
                else:
                    self._load_error = f"{self._load_error}\n{err_line}"

        self._models = loaded

    # ── Internal helpers ───────────────────────────────────────────
    def _registered_oauth_providers(self) -> list[OAuthProvider]:
        """Enumerate every OAuth provider considered by ``_load_models``.

        Pi parity: ``model-registry.ts::loadModels`` iterates the
        registered OAuth providers in the OAuth registry. Aelix
        ports this by consulting :mod:`aelix_ai.oauth._registry` for
        the live set.
        """

        from aelix_ai.oauth._registry import get_oauth_providers

        return list(get_oauth_providers())

    def _read_oauth_credentials_sync(self, provider_id: str) -> Any | None:
        """Sync read of stored OAuth credentials (no refresh).

        Pi parity: ``model-registry.ts::loadModels`` reads
        ``authStorage.getOAuth(id)`` synchronously inside the load
        pipeline. The Aelix :class:`AuthStorage` exposes async getters;
        we synthesize the credential dataclass from the in-memory
        ``_data`` snapshot. Returns :data:`None` if absent or non-OAuth.
        """

        from aelix_ai.oauth.types import OAuthCredentials

        # Pi parity: AuthStorage's in-memory ``_data`` is the
        # synchronously-readable snapshot. The async :meth:`load` lazy-
        # initializes it; callers (RPC mode, CLI) always call
        # :meth:`load` before constructing the ModelRegistry. Sprint
        # 6f₁ schedules a load on first use so refresh() can fire from
        # async contexts that haven't.
        #
        # Sprint 6f W6 (P-184 / W4 m2): use ``asyncio.get_running_loop``
        # to detect "we're inside an event loop already" instead of
        # ``get_event_loop`` (deprecated in Python 3.12+ when no loop
        # is running). The migration matches the Sprint 6c P-99 oauth-
        # framework cleanup.
        if not self._auth_storage._loaded:
            try:
                asyncio.get_running_loop()
                # Caller is in an async context — best-effort skip
                # rather than block. The next refresh() after load
                # will pick up the credentials.
                return None
            except RuntimeError:
                # No running loop — safe to drive a sync load via a
                # fresh loop, then dispose it.
                pass
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._auth_storage.load())
            finally:
                loop.close()
        entry = self._auth_storage._data.get(provider_id)
        if entry is None or entry.get("type") != "oauth":
            return None
        creds_dict = {k: v for k, v in entry.items() if k != "type"}
        try:
            return OAuthCredentials.from_json(creds_dict)
        except ValueError:
            return None


__all__ = [
    "ModelRegistry",
    "ProviderConfigInput",
    "ResolvedRequestAuth",
]
