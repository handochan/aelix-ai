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
optional ``models_json_path``. P0 #4 (ADR-0140) lands the real
``models.json`` loader (custom models + provider/model overrides +
``apiKey``/header config-value indirection) — see
:mod:`aelix_coding_agent.models_json`.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix_ai.oauth import AuthStorage
from aelix_ai.oauth._resolve_config import (
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers_or_throw,
)
from aelix_ai.oauth.types import AuthStatus, OAuthProvider
from aelix_ai.streaming import Model

from .models_json import (
    empty_custom_models_result,
    load_built_in_models,
    load_custom_models,
    merge_custom_models,
)


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

    # Pi parity: ``ProviderConfigInput.name`` — display name source for
    # :meth:`get_provider_display_name` (P0 #4 / ADR-0140).
    name: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    auth_header: bool | None = None
    oauth: OAuthProvider | None = None
    # Custom catalog entries — Sprint 6g wires the merge path.
    models: dict[str, Model] | None = None


@dataclass
class ProviderRequestConfig:
    """Pi parity: ``model-registry.ts::ProviderRequestConfig``.

    The request-time auth subset extracted from a ``models.json`` provider
    block (or a dynamically-registered :class:`ProviderConfigInput`) and
    consulted by :meth:`ModelRegistry.get_api_key_and_headers` /
    :meth:`get_provider_auth_status`. ``api_key`` may be a literal, an
    env-var name, or a ``!command`` indirection (resolved lazily at
    request time via :func:`aelix_ai.oauth._resolve_config`).
    """

    api_key: str | None = None
    headers: dict[str, str] | None = None
    auth_header: bool | None = None


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

    Sprint 6f₁ surface — 14 public methods. P0 #4 (ADR-0140) lands the
    ``models.json`` loader (Pi ``loadCustomModels(path)``): a non-``None``
    ``models_json_path`` is read on every load and may add custom
    providers/models or override built-ins.
    """

    def __init__(
        self,
        auth_storage: AuthStorage,
        models_json_path: str | None = None,
    ) -> None:
        # Pi parity: constructor stores ``authStorage`` + ``modelsJsonPath``
        # then runs ``loadModels``. ``models_json_path=None`` = in-memory
        # (no models.json); a path is read on every load (P0 #4 / ADR-0140).
        self._auth_storage = auth_storage
        self._models_json_path = models_json_path
        self._models: list[Model] = []
        # Pi ``providerRequestConfigs`` / ``modelRequestHeaders`` — rebuilt
        # from models.json (+ re-applied registered providers) on every
        # load; cleared at the top of :meth:`_load_models`.
        self._provider_request_configs: dict[str, ProviderRequestConfig] = {}
        self._model_request_headers: dict[str, dict[str, str]] = {}
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
        """Pi parity: ``model-registry.ts::ModelRegistry.create``.

        Defaults ``models_json_path`` to ``<agent-dir>/models.json`` (Pi
        ``join(getAgentDir(), "models.json")``) when omitted, so the CLI
        picks up a user's custom models without an explicit path. Pass an
        explicit path to override; use :meth:`in_memory` for no models.json.
        """

        if models_json_path is None:
            from .cli.config import get_agent_dir

            models_json_path = str(Path(get_agent_dir()) / "models.json")
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
        # Pi parity: a models.json (or re-applied registered) provider
        # ``apiKey`` counts as configured auth even before it's resolved
        # (Pi ``providerRequestConfigs.get(p)?.apiKey !== undefined``).
        request_config = self._provider_request_configs.get(provider)
        if request_config is not None and request_config.api_key is not None:
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

        Resolution order (Pi):

        1. ``api_key`` = AuthStorage cascade (``include_fallback=False``);
           else the ``models.json`` provider ``apiKey`` resolved via
           :func:`resolve_config_value_or_throw` (env-var / ``!command`` /
           literal indirection).
        2. ``headers`` = ``model.headers`` < provider request-config headers
           < per-model request headers (each value resolved through the
           same indirection; later sources win).
        3. If the provider config sets ``authHeader``, attach
           ``Authorization: Bearer <api_key>`` (erroring when no key).

        Pi parity (P0 #4 / ADR-0140): a provider with NO resolvable key now
        returns ``ok=True`` with ``api_key=None`` (OAuth-only providers
        attach their bearer via ``model.headers`` from ``modify_models``);
        the prior ``ok=False`` "No configured auth" early-return diverged.
        Any resolution failure (e.g. a ``!command`` that produced no
        output) is reported as ``ok=False`` with the message (Pi try/catch).
        """

        try:
            provider = model.provider
            provider_config = self._provider_request_configs.get(provider)

            api_key = await self._auth_storage.get_api_key_cascade(
                provider, include_fallback=False
            )
            if (
                api_key is None
                and provider_config is not None
                and provider_config.api_key
            ):
                api_key = resolve_config_value_or_throw(
                    provider_config.api_key,
                    f'API key for provider "{provider}"',
                )

            provider_headers = resolve_headers_or_throw(
                provider_config.headers if provider_config is not None else None,
                f'provider "{provider}"',
            )
            model_headers = resolve_headers_or_throw(
                self._model_request_headers.get(
                    self._get_model_request_key(provider, model.id)
                ),
                f'model "{provider}/{model.id}"',
            )

            headers: dict[str, str] = {}
            if model.headers or provider_headers or model_headers:
                headers = {
                    **(model.headers or {}),
                    **(provider_headers or {}),
                    **(model_headers or {}),
                }

            if provider_config is not None and provider_config.auth_header:
                if not api_key:
                    return ResolvedRequestAuth(
                        ok=False, error=f'No API key found for "{provider}"'
                    )
                headers = {**headers, "Authorization": f"Bearer {api_key}"}

            return ResolvedRequestAuth(ok=True, api_key=api_key, headers=headers)
        except Exception as exc:  # noqa: BLE001 — Pi reports the message.
            return ResolvedRequestAuth(ok=False, error=str(exc))

    async def get_api_key_for_provider(self, provider: str) -> str | None:
        """Pi parity: ``model-registry.ts::getApiKeyForProvider``.

        AuthStorage cascade first; else the ``models.json`` provider
        ``apiKey`` resolved uncached (env-var / ``!command`` / literal).
        """

        api_key = await self._auth_storage.get_api_key_cascade(
            provider, include_fallback=False
        )
        if api_key is not None:
            return api_key
        provider_config = self._provider_request_configs.get(provider)
        if provider_config is not None and provider_config.api_key:
            return resolve_config_value_uncached(provider_config.api_key)
        return None

    async def get_provider_auth_status(self, provider: str) -> AuthStatus:
        """Pi parity: ``model-registry.ts::getProviderAuthStatus``.

        Delegates to :meth:`AuthStorage.get_auth_status` for the
        layered-source resolution (stored / runtime / environment /
        fallback). When no source resolves, falls back to the
        ``models.json`` provider ``apiKey`` and reports its source
        (``models_json_command`` for a ``!command``, ``environment`` when
        the value names a set env var, else ``models_json_key``). Reports
        source WITHOUT exposing the credential value or refreshing OAuth.
        """

        auth_status = await self._auth_storage.get_auth_status(provider)
        if auth_status.source:
            return auth_status

        provider_config = self._provider_request_configs.get(provider)
        provider_api_key = (
            provider_config.api_key if provider_config is not None else None
        )
        if not provider_api_key:
            return auth_status

        if provider_api_key.startswith("!"):
            return AuthStatus(configured=True, source="models_json_command")
        if os.environ.get(provider_api_key):
            return AuthStatus(
                configured=True, source="environment", label=provider_api_key
            )
        return AuthStatus(configured=True, source="models_json_key")

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

    def reset(self) -> None:
        """Pi parity: ``model-registry.ts::reset`` naming alias.

        Sprint 6h₇c §B (Phase 5a-iii-γ, ADR-0093, P-446) — Pi-parity
        naming alias for :meth:`refresh`. Pi's ``resetApiProviders()``
        composition (``register-builtins.ts:400-403``) plus the
        :meth:`AgentHarness.reload` chain (`agent-session.ts:2389`)
        call ``modelRegistry.reset()``. Aelix retains :meth:`refresh`
        for backward compatibility; both invoke :meth:`_load_models`
        — semantic identity.
        """

        self.refresh()

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

        # P0 #4 (ADR-0140): a registered / models.json provider ``name``
        # takes precedence (Pi ``registeredProvider?.name`` first). Two
        # INTENTIONAL divergences from Pi (cosmetic, off the loader data
        # path) are kept so this sprint introduces no UI shift:
        #   1. the built-in display map sits ABOVE the OAuth-registry name
        #      lookup (Pi checks ``oauthProvider?.name`` before ``BUILT_IN``)
        #      — preserves bare built-in names ("Anthropic", not "Anthropic
        #      (Claude Pro/Max)");
        #   2. the final fallback title-cases an unknown id (``my-prov`` →
        #      ``My-Prov``) where Pi returns the RAW id — the pre-existing
        #      Sprint 6f₁ behavior, retained for back-compat.
        # The full Pi precedence is a separate P2-cosmetic item.
        config = self._registered_providers.get(provider)
        if config is not None and config.name:
            return config.name
        if provider in _BUILT_IN_DISPLAY_NAMES:
            return _BUILT_IN_DISPLAY_NAMES[provider]
        if config is not None and config.oauth is not None and config.oauth.name:
            return config.oauth.name
        return provider.title()

    # ── models.json request-config helpers (Pi parity) ─────────────
    @staticmethod
    def _get_model_request_key(provider: str, model_id: str) -> str:
        """Pi parity: ``model-registry.ts::getModelRequestKey``."""

        return f"{provider}:{model_id}"

    def _store_provider_request_config(
        self,
        provider_name: str,
        *,
        api_key: str | None,
        headers: dict[str, str] | None,
        auth_header: bool | None,
    ) -> None:
        """Pi parity: ``model-registry.ts::storeProviderRequestConfig``.

        Only stores a config carrying at least one of
        ``apiKey``/``headers``/``authHeader`` (Pi early-returns otherwise).
        """

        if not api_key and not headers and not auth_header:
            return
        self._provider_request_configs[provider_name] = ProviderRequestConfig(
            api_key=api_key, headers=headers, auth_header=auth_header
        )

    def _store_provider_request_config_from_config(
        self, provider_name: str, provider_config: dict[str, Any]
    ) -> None:
        """``loadCustomModels`` callback — adapts a JSON provider block.

        Pi passes the whole ``providerConfig`` to ``storeProviderRequestConfig``
        which reads ``apiKey``/``headers``/``authHeader`` off it.
        """

        self._store_provider_request_config(
            provider_name,
            api_key=provider_config.get("apiKey"),
            headers=provider_config.get("headers"),
            auth_header=provider_config.get("authHeader"),
        )

    def _store_model_headers(
        self, provider: str, model_id: str, headers: dict[str, str] | None
    ) -> None:
        """Pi parity: ``model-registry.ts::storeModelHeaders``."""

        key = self._get_model_request_key(provider, model_id)
        if not headers or len(headers) == 0:
            self._model_request_headers.pop(key, None)
            return
        self._model_request_headers[key] = headers

    # ── Loading pipeline ───────────────────────────────────────────
    def _load_models(self) -> None:
        """Pi parity: ``model-registry.ts::loadModels``.

        Pipeline:

        1. Load custom models + overrides from ``models.json`` (P0 #4 /
           ADR-0140). The per-load request-config maps are cleared first,
           then repopulated via the
           :meth:`_store_provider_request_config_from_config` /
           :meth:`_store_model_headers` callbacks. A parse/validate failure
           is recorded on ``_load_error`` (built-ins still load).
        2. Load built-ins with provider/model overrides applied, then merge
           the custom models on top (custom wins on a ``(provider, id)``
           conflict).
        3. Re-apply dynamically-registered providers' request configs so
           they survive the map clear (Pi rebuilds these in ``refresh``).
        4. Apply each OAuth provider's ``modify_models`` callback when live
           credentials exist (Pi P-132).

        Sprint 6f W6 (P-175): multiple provider failures within one pass
        are joined with newlines so :meth:`get_error` surfaces every cause.
        """

        # Pi parity: the request-config maps are fully rebuilt from the
        # current models.json each load, so clear them first.
        self._provider_request_configs.clear()
        self._model_request_headers.clear()

        # Step 1: custom models + overrides from models.json.
        if self._models_json_path is not None:
            result = load_custom_models(
                self._models_json_path,
                store_provider_request_config=(
                    self._store_provider_request_config_from_config
                ),
                store_model_headers=self._store_model_headers,
            )
        else:
            result = empty_custom_models_result()
        # P-175: a successful load drops any stale error (result.error is
        # None on success); a failed parse keeps built-ins + records why.
        self._load_error = result.error

        # Step 2: built-ins (with overrides) + merge custom on top.
        built_in = load_built_in_models(result.overrides, result.model_overrides)
        loaded = merge_custom_models(built_in, result.models)

        # Step 3: re-apply dynamically-registered providers' request configs
        # (register_provider stores into _registered_providers; the maps
        # were just cleared above).
        for name, config in self._registered_providers.items():
            self._store_provider_request_config(
                name,
                api_key=config.api_key,
                headers=config.headers,
                auth_header=config.auth_header,
            )

        # Step 4: OAuth modify_models callbacks (Pi P-132 wire-up).
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
    "ProviderRequestConfig",
    "ResolvedRequestAuth",
]
