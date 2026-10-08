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
import contextlib
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from aelix_ai.oauth import AuthStorage, StoredCredentialError
from aelix_ai.oauth._resolve_config import (
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers_or_throw,
)
from aelix_ai.oauth.types import AuthStatus, OAuthProvider
from aelix_ai.streaming import Model

from .models_json import (
    ProviderOverride,
    compose_built_in_model,
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

    ``retry_reason`` (aelix-additive, #379 / ADR-0251 §4): set on ``ok=False``
    only when the failure is a stored OAuth refresh that failed on a transient
    cause (:attr:`aelix_ai.oauth.OAuthRefreshError.retry_reason` - the token
    endpoint answered ``429``/``500``/``502``-``504``/``520``/``524``, pi's set,
    or could not be reached). The CLI's auth
    callback raises it on, and the harness retries that turn as it retries a
    provider's ``502``. pi carries no such field: its retry reads the error's
    text.
    """

    ok: bool
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    retry_reason: str | None = None


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
    # #363 / ADR-0251: True when ``api_key`` is the ``models.json`` provider
    # ``apiKey`` - the loader's callback stored this config, or an extension
    # ``register_provider`` for the same name that carries no ``api_key``
    # replaced it and kept that key (``_load_models`` step 3, review round 1
    # R5). False when a registration's own ``api_key`` (or none) is here. Only
    # a models.json ``api_key`` comes before the environment; a registration's
    # stays after it until #365 (see :meth:`ModelRegistry._request_api_key`).
    from_models_json: bool = False


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
        # #240 — successful ``!command`` resolutions of the values in the two
        # maps above, keyed on the full ``"!cmd"`` string. NOT named
        # ``_resolve_cache``: ``AuthStorage._resolve_cache`` has the same name
        # and type but a different key space (``value[1:]``) and stores empty
        # output (#242), so identical names would type-check as interchangeable.
        # Built BEFORE the ``_load_models`` call below, which clears it.
        self._command_value_cache: dict[str, str] = {}
        self._registered_providers: dict[str, ProviderConfigInput] = {}
        self._load_error: str | None = None
        # #344 — which providers the USER defined, and the models.json ``baseUrl``
        # of each built-in one they re-pointed. Both are rebuilt by every
        # ``_load_models`` (models.json is re-read there, and a
        # ``register_provider`` re-runs it), so they can never describe a file
        # or an extension set the registry no longer holds.
        self._user_defined_providers: frozenset[str] = frozenset()
        self._base_url_overrides: dict[str, str] = {}
        # #363 — the models.json provider overrides (``baseUrl`` / ``compat``) and
        # ``modelOverrides`` of the BUILT-IN providers, from the same load, so the
        # launch path composes a catalog hit exactly as ``/model``'s copy is
        # composed (:meth:`compose_built_in`).
        self._built_in_overrides: dict[str, ProviderOverride] = {}
        self._built_in_model_overrides: dict[str, dict[str, dict[str, Any]]] = {}
        # #375 — (the ``self._models`` list, the ``OPENROUTER_BASE_URL`` value,
        # the models handed out) of the last :meth:`_served_models` call. Keyed
        # on the list's identity: every load assigns a new one.
        self._served_cache: tuple[list[Model], str | None, list[Model]] | None = None
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
    def _served_models(self) -> list[Model]:
        """``self._models`` as every accessor hands them out (#375).

        The one place a registry model gets :func:`with_openrouter_base_url`:
        :meth:`get_all`, :meth:`get_available`, :meth:`find` and
        :meth:`compose_built_in` all read through here, so ``/model``, its
        picker (also as ``/scoped-models`` narrows it), the first model after
        ``/login`` (``find_initial_model``), ``--list-models`` and an
        embedder's rpc ``set_model`` / ``cycle_model`` get the same OpenRouter
        ``base_url`` the launch does. Applied when READ, not
        when loaded: the variable is read at the moment a model is handed out
        (the launch reads it the same way), so a registry built before
        ``load_dotenv`` admitted a hatched value still agrees with the launch.
        The copies are cached per (load, value), so a model's identity is
        stable between two reads while neither changes.
        """

        base_url = openrouter_base_url()
        cached = self._served_cache
        if cached is not None and cached[0] is self._models and cached[1] == base_url:
            return cached[2]
        served = (
            [with_openrouter_base_url(m, base_url) for m in self._models]
            if base_url
            else self._models
        )
        self._served_cache = (self._models, base_url, served)
        return served

    def get_all(self) -> list[Model]:
        """Pi parity: ``model-registry.ts::getAll``.

        OpenRouter models carry ``OPENROUTER_BASE_URL`` when it is set (#375,
        :meth:`_served_models`).
        """

        return list(self._served_models())

    def get_available(self) -> list[Model]:
        """Pi parity: ``model-registry.ts::getAvailable``.

        Filters :meth:`get_all` to models for which
        :meth:`has_configured_auth` returns True. Insertion order is
        preserved (matches Pi Map iteration order — the seed catalog
        defines the canonical order for ``cycle_model`` rotation).
        """

        return [m for m in self._served_models() if self.has_configured_auth(m)]

    def find(self, provider: str, model_id: str) -> Model | None:
        """Pi parity: ``model-registry.ts::find`` (OpenRouter base as :meth:`get_all`)."""

        for m in self._served_models():
            if m.provider == provider and m.id == model_id:
                return m
        return None

    # ── Auth resolution ────────────────────────────────────────────
    def has_configured_auth(self, model: Model) -> bool:
        """Pi parity: ``model-registry.ts::hasConfiguredAuth``.

        Returns :data:`True` if ANY auth layer has a key for
        ``model.provider`` (runtime override, stored credential,
        models.json ``apiKey``, env var, registered ProviderConfigInput,
        fallback resolver). Does NOT trigger OAuth refresh. The layers are
        checked in the request's order (#363, :meth:`_request_api_key`), but
        the answer is a union, so the order cannot change it.

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
        # Pi parity: a models.json (or re-applied registered) provider
        # ``apiKey`` counts as configured auth even before it's resolved
        # (Pi ``providerRequestConfigs.get(p)?.apiKey !== undefined``).
        request_config = self._provider_request_configs.get(provider)
        if request_config is not None and request_config.api_key is not None:
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

    def has_route_auth(self, provider: str, *, runtime_overrides: bool = True) -> bool:
        """:meth:`has_configured_auth` without a credential a cwd ``.env`` supplied.

        #362 / ADR-0250 guard 1. pi's ``resolveCliModel`` lets auth decide three
        things — a bare id several providers serve, a ``<provider>/<id>`` whose
        provider is unauthenticated while the raw id is authenticated elsewhere,
        and a raw fallback (``model-resolver.ts:470-504``, ``:525-540`` @
        88ff80b98) — and npm pi reads no ``.env``. aelix does
        (``runtime_bootstrap.load_dotenv``), so in pi's order a cloned repo's
        ``.env`` key would CHOOSE the route: measured on the design prototype,
        ``OPENAI_API_KEY`` from a ``.env`` moved an OpenRouter user's
        ``openai/gpt-4o-mini`` to ``api.openai.com`` under the planted key. This
        is the predicate every such judgement asks instead. A ``.env``
        credential still AUTHENTICATES a route once chosen — the request's
        bearer is :meth:`get_api_key_and_headers`. Since #363 (ADR-0251) that
        bearer is a ``models.json`` ``apiKey`` ahead of the environment, so a
        route layer 4 chose (a literal or ``!command`` ``apiKey``) also carries
        that key, not a ``.env`` one. The layers below are a union: their order
        decides nothing here.

        The layers, with what a ``.env`` can reach in each:

        1. runtime override (``--api-key``) — True; ``runtime_overrides=False``
           asks what a delegated child, which never receives it, would see;
        2. ``auth.json`` (``/login``; api key or OAuth, an expired OAuth too —
           pi counts it, the user's own file): True, except an api-key entry
           that names an environment variable a ``.env`` supplied;
        3. environment: a name of ``ENV_API_KEYS[provider]`` that is set and
           not in the record (``core.dotenv_provenance``);
        4. a models.json / registration ``apiKey``: a ``!command`` or a literal
           counts; the name of a set variable counts unless a ``.env`` supplied
           it (``resolve_config_value`` reads ``os.environ.get(name, name)``);
        5. a registration's ``oauth`` — True;
        6. the fallback resolver: a ``str`` answer counts unless it carries the
           current value of a variable the record names
           (:func:`_derived_from_dotenv`). Codex's third cross-review of #362
           (C1): an embedder's ``set_fallback_resolver(get_env_api_key)`` (the
           shipped helper) handed layer 3's excluded ``.env`` key straight back,
           and ``/model openai/gpt-4o-mini`` went to ``api.openai.com`` on it
           while the user's own OpenRouter key was exported; the fourth (F1)
           measured the same through a resolver that prefixed, suffixed or
           stripped the value, or returned it as ``bytes``. A resolver is opaque,
           so the answer is compared by VALUE; one that ENCODES a ``.env`` value
           (a hash, base64) is the embedder's to avoid - a resolver must not
           derive its answer from a variable
           :func:`aelix_ai.dotenv_record.dotenv_supplied` names. Only this
           predicate applies the rule: :meth:`has_configured_auth` and the
           request's bearer are unchanged (a fallback key still authenticates a
           chosen route).
        """

        from aelix_ai.providers._env_api_keys import ENV_API_KEYS

        from .core.dotenv_provenance import dotenv_admitted_names, env_name

        admitted = dotenv_admitted_names()

        def _named_value_counts(value: str) -> bool:
            # ``!command`` and literals are the user's own config; an env-var
            # name counts unless the .env put it there.
            if value.startswith("!"):
                return True
            if os.environ.get(value) is not None:
                return env_name(value) not in admitted
            return True

        if runtime_overrides and provider in self._auth_storage._runtime_overrides:
            return True
        if self._auth_storage.has(provider):
            stored = self._auth_storage.get_all().get(provider) or {}
            key = stored.get("key") if stored.get("type") == "api_key" else None
            if not isinstance(key, str) or not key or _named_value_counts(key):
                return True
        for name in ENV_API_KEYS.get(provider, ()):
            if os.environ.get(name) and env_name(name) not in admitted:
                return True
        request_config = self._provider_request_configs.get(provider)
        if (
            request_config is not None
            and request_config.api_key is not None
            and _named_value_counts(request_config.api_key)
        ):
            return True
        config = self._registered_providers.get(provider)
        if config is not None:
            if config.api_key and _named_value_counts(config.api_key):
                return True
            if config.oauth is not None:
                return True
        fallback = self._auth_storage._fallback_resolver
        if fallback is not None:
            try:
                answer = fallback(provider)
            except Exception:  # noqa: BLE001 — sync, swallowed as has_configured_auth does
                answer = None
            if isinstance(answer, str) and str.strip(answer):
                planted = [value for name in admitted if (value := os.environ.get(name))]
                if not _derived_from_dotenv(answer, planted):
                    return True
        return False

    def has_fallback_resolver(self) -> bool:
        """Is a fallback resolver installed (``AuthStorage.set_fallback_resolver``)?

        #362 / ADR-0250 §2.8: :func:`~aelix_coding_agent.cli.runtime_bootstrap.holds_route_auth`
        counts an installed resolver as a credential source of the user's own,
        because a resolver is a function — the providers it answers for cannot be
        listed (:meth:`route_auth_candidates` cannot name them).
        """

        return self._auth_storage._fallback_resolver is not None

    def route_auth_candidates(self) -> frozenset[str]:
        """Every provider name a credential of the user's own could sit on, rows or not.

        #362 / ADR-0250 guard 1 asks "does the user hold a route-authenticating
        credential of their own for ANY provider". Reading that off model rows
        (``get_available()``) missed a provider that holds one but serves no
        model — an ``auth.json`` key for a provider registered with no models
        (Codex's second cross-review of ``a0edf615``, F3: with the ``.env``,
        ``/model openai/gpt-4o-mini`` then went to ``api.openai.com`` on the
        file's key). The names come from the registry's own sources: model
        rows, ``auth.json`` entries, runtime (``--api-key``) overrides,
        models.json provider entries and extension registrations, models or
        not. :func:`~aelix_coding_agent.cli.runtime_bootstrap.holds_route_auth`
        asks :meth:`has_route_auth` of each.
        """

        names: set[str] = {m.provider for m in self._models}
        names.update(self._provider_request_configs)
        names.update(self._registered_providers)
        names.update(self._auth_storage._runtime_overrides)
        # An unreadable auth.json adds no names.
        with contextlib.suppress(Exception):
            names.update(self._auth_storage.get_all())
        return frozenset(n for n in names if n)

    async def get_api_key_and_headers(self, model: Model) -> ResolvedRequestAuth:
        """Pi parity: ``model-registry.ts::getApiKeyAndHeaders``.

        Returns a :class:`ResolvedRequestAuth` carrying:

        - ``api_key``: :meth:`_request_api_key` - pi's order (#363 / ADR-0251).
        - ``headers``: merged from per-provider ``ProviderConfigInput``
          + OAuth provider override (Copilot adds ``COPILOT_HEADERS``
          via the provider's :meth:`OAuthProvider.modify_models`
          callback; the registry only forwards what
          :class:`ProviderConfigInput` carries here).

        Resolution order (Pi):

        1. ``api_key`` = :meth:`_request_api_key`: ``--api-key``, then
           ``auth.json`` (which owns the provider), then the ``models.json``
           provider ``apiKey`` (resolved via :func:`resolve_config_value_or_throw`
           - env-var / ``!command`` / literal indirection), then the
           environment, then an extension registration's ``api_key``.
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

        #240: this is the harness's PER-REQUEST auth callback, and all three
        resolution sites above share :attr:`_command_value_cache`, so a
        ``!command`` forks once per registry load rather than once per request
        (measured on darwin before: 2 spawns and 8.44 ms of blocked event loop
        on every call). Only successes are cached, so a failing helper is
        retried; the AuthStorage cascade and the env/literal branch are
        untouched. Divergence from Pi, whose registry path is uncached
        (ADR-0235 permits it).
        """

        try:
            provider = model.provider
            provider_config = self._provider_request_configs.get(provider)

            api_key = await self._request_api_key(provider)

            provider_headers = resolve_headers_or_throw(
                provider_config.headers if provider_config is not None else None,
                f'provider "{provider}"',
                cache=self._command_value_cache,
            )
            model_headers = resolve_headers_or_throw(
                self._model_request_headers.get(
                    self._get_model_request_key(provider, model.id)
                ),
                f'model "{provider}/{model.id}"',
                cache=self._command_value_cache,
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
            # #379: a stored OAuth refresh that failed on a transient cause says
            # so; every other failure (StoredCredentialError's own reasons, a
            # header !command, an authHeader with no key) has no reason.
            retry_reason = exc.retry_reason if isinstance(exc, StoredCredentialError) else None
            return ResolvedRequestAuth(ok=False, error=str(exc), retry_reason=retry_reason)

    async def _request_api_key(self, provider: str, *, uncached: bool = False) -> str | None:
        """The key a request to ``provider`` carries: pi's order (#363 / ADR-0251).

        1. ``--api-key`` (a runtime override) and
        2. ``auth.json`` (``/login``; an api key or OAuth): a stored credential
           OWNS the provider whatever it yields - the AuthStorage cascade
           answers (``stored_owns=True``) and nothing below is asked. An entry
           that gives no key RAISES a
           :class:`~aelix_ai.oauth.StoredCredentialError`: a failed OAuth
           refresh (:class:`~aelix_ai.oauth.OAuthRefreshError`; pi
           ``packages/ai/src/auth/resolve.ts:56-87``, ``:142`` @ b223082bb), an
           ``api_key`` whose key is empty or resolves empty (``"!true"``), an
           OAuth record whose OAuth provider is not registered (expired or
           not), an entry of an unknown type (review round 2). So
           :meth:`get_api_key_and_headers` answers ``ok=False`` and the request
           fails before anything is sent - or, for a refresh that failed on a
           transient cause (pi's ``429``/``500``/``502``-``504``/``520``/``524``,
           or unreachable, #379), the turn is
           retried, and each attempt asks this step again, never a step below
           while the login is stored (a logout meanwhile: ADR-0251 §12.6).
           Answering "no key" instead was not
           enough (review round 1, R1): the CLI's auth callback reads no key and
           no headers as "no opinion", and the adapter then reads the
           environment itself, so an exported vendor key went to the gateway;
        3. the ``models.json`` provider ``apiKey`` (env-var name / ``!command`` /
           literal, :func:`resolve_config_value_or_throw`) - pi's
           ``composeApiKeyAuth.resolve`` hands this ``rawKey`` to the built-in
           resolver as the credential and asks the environment only when there
           is none (``coding-agent/src/core/provider-composer.ts:457-479``);
        4. the environment (``ENV_API_KEYS``; ``include_fallback=False``, so the
           fallback resolver never authenticates a request, as before);
        5. an extension ``register_provider`` ``api_key`` - where it was before
           #363. pi puts it in step 3 (``configuredApiKey = extension?.apiKey ??
           config?.apiKey``, ``provider-composer.ts:388-393``); aelix keeps it
           after the environment until #365 lands, because a registration that
           brings models for a catalogued name still leaves the catalogue's rows
           in the registry, and step 3 would put the extension's key on them
           (measured: ``/model`` row ``openai/gpt-4o-mini`` on
           ``api.openai.com`` went from the exported vendor key to the
           extension's).

        Before #363 the AuthStorage cascade (1, 2, environment) ran first and
        the provider ``apiKey`` only when it found nothing, so an exported
        vendor key or a cwd ``.env`` one beat a ``models.json`` ``apiKey`` - and
        went to the gateway that ``apiKey`` was for.
        """

        storage = self._auth_storage
        await storage._ensure_loaded()
        if storage._runtime_overrides.get(provider) or storage.has(provider):
            return await storage.get_api_key_cascade(
                provider, include_fallback=False, stored_owns=True
            )
        config = self._provider_request_configs.get(provider)
        if config is not None and config.api_key and config.from_models_json:
            if uncached:
                return resolve_config_value_uncached(config.api_key)
            return resolve_config_value_or_throw(
                config.api_key,
                f'API key for provider "{provider}"',
                cache=self._command_value_cache,
            )
        api_key = await storage.get_api_key_cascade(provider, include_fallback=False)
        if api_key is not None:
            return api_key
        if config is not None and config.api_key:
            if uncached:
                return resolve_config_value_uncached(config.api_key)
            return resolve_config_value_or_throw(
                config.api_key,
                f'API key for provider "{provider}"',
                cache=self._command_value_cache,
            )
        return None

    async def get_api_key_for_provider(self, provider: str) -> str | None:
        """Pi parity: ``model-registry.ts::getApiKeyForProvider``.

        The order of :meth:`get_api_key_and_headers` (:meth:`_request_api_key`,
        #363), with a ``models.json`` ``apiKey`` resolved uncached (env-var /
        ``!command`` / literal). A stored entry that gives no key (a failed
        OAuth refresh, an empty key, an unregistered OAuth provider) gives
        :data:`None`, as pi's ``getApiKeyForProvider`` catches the error
        (``model-registry.ts:198-204`` @ b223082bb) - never a key from below.
        """

        try:
            return await self._request_api_key(provider, uncached=True)
        except StoredCredentialError:
            return None

    async def get_provider_auth_status(self, provider: str) -> AuthStatus:
        """Pi parity: ``model-runtime.ts::getProviderAuthStatus``.

        Reports the source the request's key comes from, in the order of
        :meth:`_request_api_key` (#363 / ADR-0251; pi ``model-runtime.ts:638-648``
        @ b223082bb): ``runtime`` (``--api-key``) first, then ``stored``
        (``auth.json``); then a ``models.json`` provider ``apiKey``
        (``models_json_command`` for a ``!command``, ``environment`` when the
        value names a set env var, else ``models_json_key``); then the
        environment / fallback answer of :meth:`AuthStorage.get_auth_status`;
        then an extension registration's ``api_key``, reported the same way as a
        ``models.json`` one (where it was before #363). Reports source WITHOUT
        exposing the credential value or refreshing OAuth.

        ``runtime`` is checked here, before :meth:`AuthStorage.get_auth_status`,
        which checks ``stored`` first (as the pi ``auth-storage.ts:342-361`` @
        734e08e it ports did): with both ``--api-key`` and an ``auth.json``
        entry the request carries the ``--api-key`` key, and the status said
        ``stored`` (review round 1, R2). The shape is :class:`AuthStorage`'s
        (``configured=False``, label ``--api-key``); pi reports
        ``configured: true``.
        """

        storage = self._auth_storage
        await storage._ensure_loaded()
        if storage._runtime_overrides.get(provider):
            return AuthStatus(configured=False, source="runtime", label="--api-key")
        auth_status = await storage.get_auth_status(provider)
        if auth_status.source in ("stored", "runtime"):
            return auth_status

        provider_config = self._provider_request_configs.get(provider)
        provider_api_key = (
            provider_config.api_key if provider_config is not None else None
        )
        if auth_status.source and not (
            provider_api_key and provider_config is not None and provider_config.from_models_json
        ):
            return auth_status
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

    def clear_config_value_cache(self) -> None:
        """Drop the cached ``models.json`` ``!command`` values (#240).

        Aelix-only; Pi's registry path is uncached and has nothing to clear
        (its ``clearConfigValueCache`` belongs to the auth family). This drops
        the resolved credential values WITHOUT re-reading ``models.json`` and
        WITHOUT re-running every OAuth ``modify_models`` callback, which
        :meth:`refresh` would do — so the next request pays one shell start per
        distinct ``!command`` and nothing else.

        Callers today, all through :func:`clear_command_value_cache`: the TUI's
        ``/reload`` (neither of whose arms reaches :meth:`_load_models`), the
        TUI's end-of-turn handler, and print mode's — the last two whenever the
        turn ended with ``stop_reason == "error"``, since a turn that failed may
        have failed on a credential this cache is still serving. The trigger is
        the TERMINAL MESSAGE, not a raised exception: every shipping adapter
        converts a provider failure into an ``AssistantErrorEvent``, so a 401
        returns normally out of ``harness.prompt`` (measured, #240 review).

        The subagent channels share the registry and have no such seam; they are
        child-owned and end with their child, so they rely on the parent's
        recoveries, ``/login`` and process exit.
        """

        self._command_value_cache.clear()

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

    def get_registered_providers(self) -> dict[str, ProviderConfigInput]:
        """Dynamically-registered provider configs, keyed by name (a copy).

        The public enumeration accessor over :attr:`_registered_providers` (Issue
        #77) — lets the ``/login`` wizard list extension-registered providers in
        its API-key sub-flow. Returns a shallow copy so callers cannot mutate the
        live registry.
        """

        return dict(self._registered_providers)

    def get_user_defined_providers(self) -> frozenset[str]:
        """The providers whose endpoint the USER chose, rather than this build (#344).

        Three sources, recomputed on every :meth:`_load_models`:

        * a ``models.json`` provider this build's catalog does not know (a
          custom provider — ``validate_config_semantics`` requires its
          ``baseUrl``);
        * a CATALOGUED provider whose ``models.json`` entry sets a
          provider-level ``baseUrl`` (the user re-pointed it at a proxy or a
          gateway). ``modelOverrides`` / ``headers`` / ``compat`` alone do NOT
          count: they tune a provider without moving it, and the owner's own
          ``models.json`` carries such entries for ``openrouter``, ``zai``,
          ``huggingface`` and ``opencode*``;
        * an extension ``register_provider`` provider — every one whose name is
          not catalogued, and a catalogued name only when the registration brings
          models (an auth-only or header-only registration tunes a built-in the
          same way ``headers`` does). The launch resolver then scopes such a
          catalogued name to the models the registration brought
          (``runtime_bootstrap._registration_models``).

        ``cli.runtime_bootstrap.resolve_route`` (ADR-0250) reads this so that a
        ``<provider>/<id>`` under one of these never leaves it — a custom id
        there or a refusal, never OpenRouter (guard 2 skips it) — and to match
        such a prefix first and alone, with aelix's case rule. It is a statement
        about configuration only: no credential is consulted, so a key in a cwd
        ``.env`` cannot change it.
        """

        return self._user_defined_providers

    def get_base_url_override(self, provider: str) -> str | None:
        """The ``models.json`` provider-level ``baseUrl`` of a BUILT-IN provider (#344).

        ``None`` for a provider the user did not re-point (and for custom
        providers, whose models already carry their own ``baseUrl``). Lets the
        launch path adopt the override onto a static catalog hit — the registry
        copy already has it, the catalog entry ``resolve_model`` returns first
        does not.
        """

        return self._base_url_overrides.get(provider)

    def compose_built_in(self, model: Model) -> Model:
        """A catalog model as ``/model``'s registry copy has it (#363 / ADR-0251).

        The launch path calls this on a static catalog hit
        (``cli.runtime_bootstrap._compose_catalog_model``) so the launch model
        and ``/model``'s copy agree.

        1. When the registry holds a copy with the same ``provider``, ``id`` and
           ``api``, that copy is the answer, field for field - the ``models.json``
           composition AND everything ``_load_models`` applied after it: an OAuth
           provider's ``modify_models`` (review round 1, R4: a launch dropped the
           name, window, compat and headers ``/model`` kept), a ``models.json``
           ``models`` entry or an extension's model that redefines the id on the
           same ``api``.
        2. Otherwise - no copy, or one on another ``api`` (the catalog ``api``
           stays, ADR-0249 decision 3) - the catalog model composed as
           :func:`~aelix_coding_agent.models_json.load_built_in_models` composes
           it: the provider ``baseUrl`` and ``compat``, then the ``modelOverrides``
           entry (:func:`~aelix_coding_agent.models_json.compose_built_in_model`).
           A provider the user did not configure comes back unchanged.

        Neither step can change ``provider``, ``id`` or ``api``. Both answers carry
        ``OPENROUTER_BASE_URL`` for an OpenRouter model (#375,
        :func:`with_openrouter_base_url`), as every other copy the registry hands
        out does.
        """

        copy = self.find(model.provider, model.id)
        if copy is not None and copy.api == model.api:
            return copy
        return with_openrouter_base_url(
            compose_built_in_model(
                model,
                self._built_in_overrides.get(model.provider),
                (self._built_in_model_overrides.get(model.provider) or {}).get(model.id),
            )
        )

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
        from_models_json: bool = False,
    ) -> None:
        """Pi parity: ``model-registry.ts::storeProviderRequestConfig``.

        Only stores a config carrying at least one of
        ``apiKey``/``headers``/``authHeader`` (Pi early-returns otherwise).
        ``from_models_json`` records where it came from (#363, see
        :attr:`ProviderRequestConfig.from_models_json`).
        """

        if not api_key and not headers and not auth_header:
            return
        self._provider_request_configs[provider_name] = ProviderRequestConfig(
            api_key=api_key,
            headers=headers,
            auth_header=auth_header,
            from_models_json=from_models_json,
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
            from_models_json=True,
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
        # #240: the cached ``!command`` values were resolved FROM the two maps
        # above, so they are stale the moment those are rebuilt — a load is the
        # one moment an ``apiKey`` or a header value can have changed on disk.
        # This is also the invalidation seam ``refresh()`` / ``reset()`` /
        # ``register_provider()`` / ``/login`` all reach for free.
        self._command_value_cache.clear()

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

        # #344 — record what the user defined, from the SAME load that builds
        # ``self._models`` (see :meth:`get_user_defined_providers`). A failed
        # models.json parse yields an empty result, so it defines nothing — the
        # same models the registry then serves.
        from aelix_ai.models import get_providers

        catalogued = frozenset(get_providers())
        self._base_url_overrides = {
            name: override.base_url
            for name, override in result.overrides.items()
            if name in catalogued and override.base_url
        }
        self._built_in_overrides = {
            name: override for name, override in result.overrides.items() if name in catalogued
        }
        self._built_in_model_overrides = {
            name: dict(per_model)
            for name, per_model in result.model_overrides.items()
            if name in catalogued
        }
        self._user_defined_providers = frozenset(
            {name for name, override in result.overrides.items() if override.base_url}
            | {m.provider for m in result.models if m.provider not in catalogued}
            | {
                name
                for name, config in self._registered_providers.items()
                if name not in catalogued or config.models
            }
        )

        # Step 2: built-ins (with overrides) + merge custom on top.
        built_in = load_built_in_models(result.overrides, result.model_overrides)
        loaded = merge_custom_models(built_in, result.models)

        # Step 3: re-apply dynamically-registered providers' request configs
        # (register_provider stores into _registered_providers; the maps
        # were just cleared above). A registration that carries no
        # ``api_key`` keeps the models.json ``apiKey`` of the same name at the
        # request's step 3 (#363 review round 1, R5; pi ``configuredApiKey =
        # extension?.apiKey ?? config?.apiKey``, ``provider-composer.ts:388-393``
        # @ b223082bb); before, a headers-only registration dropped it and the
        # exported vendor key went out. One that carries nothing leaves the
        # models.json config as it is (the store's early return).
        for name, config in self._registered_providers.items():
            kept_key: str | None = None
            prior = self._provider_request_configs.get(name)
            if (
                not config.api_key
                and (config.headers or config.auth_header)
                and prior is not None
                and prior.from_models_json
            ):
                kept_key = prior.api_key
            self._store_provider_request_config(
                name,
                api_key=config.api_key or kept_key,
                headers=config.headers,
                auth_header=config.auth_header,
                from_models_json=kept_key is not None,
            )

        # Step 3b (Issue #77 Gap B): merge dynamically-registered providers'
        # catalog models so ``register_provider(ProviderConfigInput(models=...))``
        # actually contributes ``/model`` rows — not just auth wiring. A model
        # left at the default provider (``"unknown"``) is stamped with the
        # registration name so (provider, id) dedup + auth resolution line up.
        # ``merge_custom_models`` dedups on (provider, id) with the registered
        # entry winning, so re-running this on every ``register_provider`` +
        # ``modify_models`` pass is idempotent. Placed before Step 4 so the OAuth
        # ``modify_models`` callbacks see these rows too.
        registered_models: list[Model] = []
        for name, config in self._registered_providers.items():
            for model in (config.models or {}).values():
                provider = getattr(model, "provider", "") or ""
                registered_models.append(
                    model
                    if provider and provider != "unknown"
                    else replace(model, provider=name)
                )
        if registered_models:
            loaded = merge_custom_models(loaded, registered_models)

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
                # ``modify`` is a duck-typed provider hook, so its return is
                # unconstrained. The except below catches a hook that RAISES, but
                # whatever a hook RETURNS was adopted straight into
                # ``self._models`` — including ``None``, which would break every
                # later ``get_available()`` with nothing recorded in
                # ``get_error()``. Reject a non-list so it lands on
                # ``_load_error`` like any other per-provider failure.
                returned = modify(loaded, creds)
                if not isinstance(returned, list):
                    raise TypeError(  # noqa: TRY301
                        f"modify_models returned {type(returned).__name__}, "
                        "expected a list of models"
                    )
                loaded = cast("list[Model]", returned)
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


def openrouter_base_url() -> str | None:
    """The ``OPENROUTER_BASE_URL`` in force now, or ``None`` when unset or empty (#375).

    Read from ``os.environ`` at call time. It gets there from the shell, or from
    a project ``.env`` only when the user listed the name in
    ``AELIX_DOTENV_ALLOW`` (``cli.runtime_bootstrap.load_dotenv``,
    ``_DOTENV_LOCKED``, ADR-0203); this function admits nothing itself.
    """

    return os.environ.get("OPENROUTER_BASE_URL") or None


def with_openrouter_base_url(model: Model, base_url: str | None = None) -> Model:
    """``model`` with ``OPENROUTER_BASE_URL`` as its ``base_url`` when it is an OpenRouter model.

    #375 / ADR-0251 §11. The one function that applies the variable. It is an
    aelix addition: pi has no such variable. pi's ``models.json`` re-points
    OpenRouter with ``providers.openrouter.baseUrl``, and a ``baseUrl`` on one
    of that provider's ``models`` entries wins over the provider's for that
    model. The variable applies to every
    model whose ``provider`` is exactly ``"openrouter"``, wherever that model
    was produced: the launch (``cli.runtime_bootstrap._openrouter_base``) and
    every copy the registry hands out (:meth:`ModelRegistry._served_models`,
    :meth:`ModelRegistry.compose_built_in`). Precedence, the launch's since
    #344: the variable beats a ``models.json`` provider ``baseUrl``, a per-model
    ``baseUrl`` (a ``models`` entry under ``providers.openrouter``) and the
    catalog's host. Any other provider is returned unchanged - a custom provider
    with its own ``baseUrl``, or an OpenRouter-compatible gateway the user named
    something else, even one serving the same ids or sitting at
    ``https://openrouter.ai/api/v1``, and a provider spelled in another case
    (``OpenRouter``): the match is on the name, exactly, never on the host.
    ``provider``, ``id``, ``api`` and every other field stay - ``headers``,
    ``compat``, ``cost`` and the rest are the input's own.

    ``base_url`` is the value to apply; omitted, :func:`openrouter_base_url`
    reads it.
    """

    if model.provider != "openrouter":
        return model
    value = base_url if base_url is not None else openrouter_base_url()
    if not value or model.base_url == value:
        return model
    return replace(model, base_url=value)


# A fallback answer is matched by containment only when both strings are at
# least this long: a short common run inside a long key is chance, not derivation.
_DERIVED_MIN = 8


def _derived_from_dotenv(answer: str, planted: list[str]) -> bool:
    """Does a fallback ``answer`` carry a value a cwd ``.env`` supplied (#362, ADR-0250 §2.8)?

    Equal after stripping whitespace and case-folding, or - both at least
    :data:`_DERIVED_MIN` characters - one containing the other:
    ``"namespace:" + value``, ``value + ":namespace"``, ``value.strip()`` and
    ``value.lower()`` all hand the planted key back (Codex's fourth and fifth
    cross-reviews of #362). The answer is read through the BUILTIN ``str.strip``
    (a ``str`` subclass may override ``strip``/``__contains__``; the fifth pass
    measured one hiding an equal answer), so every comparison is on a plain
    ``str``. A planted value under :data:`_DERIVED_MIN` characters is matched by
    equality only - containment would refuse an own vault key that happens to
    contain ``dev`` - so a resolver prefixing a 4-character ``.env`` value is the
    embedder's to avoid, like an encoding. Every planted value is checked, not
    the first that differs (F4: an any/all slip passed every test).
    """

    mine = str.strip(answer).casefold()
    for value in planted:
        theirs = value.strip().casefold()
        if not theirs:
            continue
        if mine == theirs:
            return True
        if (
            len(mine) >= _DERIVED_MIN
            and len(theirs) >= _DERIVED_MIN
            and (theirs in mine or mine in theirs)
        ):
            return True
    return False


def clear_command_value_cache(registry: object | None) -> None:
    """Issue #240 — drop ``registry``'s cached ``models.json`` ``!command`` values.

    The duck-typed front door to :meth:`ModelRegistry.clear_config_value_cache`.
    It lives here, beside the method, rather than in any one surface because
    THREE call sites need it and none of them holds a typed registry: the TUI's
    ``/reload``, the TUI's failed-turn recovery, and print mode's. The registry
    is an optional parameter on both ``run_tui`` and
    :func:`~aelix_coding_agent.modes.print_mode.run_print_mode` that tests pass
    stubs for, and an embedder can supply anything.

    Fully suppressed on purpose: no caller has a user-visible failure mode worth
    interrupting for. The cost of NOT clearing is one stale credential; the cost
    of clearing is one shell start.
    """

    clear = getattr(registry, "clear_config_value_cache", None)
    if clear is not None:
        with contextlib.suppress(Exception):
            clear()


__all__ = [
    "ModelRegistry",
    "ProviderConfigInput",
    "ProviderRequestConfig",
    "ResolvedRequestAuth",
    "clear_command_value_cache",
    "openrouter_base_url",
    "with_openrouter_base_url",
]
