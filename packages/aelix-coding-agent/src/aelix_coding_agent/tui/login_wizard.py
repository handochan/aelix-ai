"""DI flow for the ``/login`` + ``/logout`` auth wizard (Sprint WP-8, Feature 1).

The interactive flows live in
:func:`aelix_coding_agent.tui.shell._open_login` / ``_open_logout``; this module
owns the pure, dependency-injected wizard logic so the WHOLE flow is
unit-testable without standing up the prompt-toolkit app — like
:mod:`aelix_coding_agent.tui.model_picker` and
:mod:`aelix_coding_agent.tui.mcp_viewer`.

Three login methods are offered (Pi parity: ``interactive-mode.ts`` auth menu):

1. **OAuth** — sign in to a subscription / account via the built-in OAuth
   providers (anthropic / github-copilot / openai-codex). The dialog callables
   are mapped onto an :class:`aelix_ai.oauth.types.OAuthLoginCallbacks` bundle and
   handed to :meth:`AuthStorage.login`, which runs the device/browser flow and
   persists the resulting OAuth credentials.
2. **API key** — pick a built-in provider (the keys of
   :data:`aelix_ai.providers._env_api_keys.ENV_API_KEYS`) and store a raw API key
   via :meth:`AuthStorage.set_api_key` (persists immediately — no ``save()``).
3. **Custom provider** — store an API key for an OpenAI- / Anthropic- /
   Gemini-compatible endpoint. When the host wired model-fetch (``multiselect`` +
   ``model_registry``) the flow also fetches the endpoint's model list and
   registers the picked models into ``models.json`` so they appear in ``/model``
   immediately; otherwise (or on any fetch/registration failure) it degrades to
   an HONEST "add via models.json" note — the stored key is never lost.

Backing APIs are PROTECTED, READ-ONLY (``aelix_ai.oauth`` / ``aelix_ai.providers``);
this module only CALLS them. ``auth_storage`` is the SAME object behind
``ModelRegistry.create(auth_storage)`` so a stored key is visible to model
resolution immediately (no reload).

Every failure mode commits a red ``Text`` and returns — never crashes the REPL.
"""

from __future__ import annotations

import contextlib
import webbrowser
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# The custom-provider protocol shapes (label -> protocol id). Pi exposes
# OpenAI / Anthropic / Gemini-compatible endpoints; we additionally split the
# OpenAI shape into its two wire APIs — chat/completions (``openai-completions``,
# what most OpenAI-compatible gateways speak) and the Responses API
# (``openai-responses``, e.g. OpenAI's own gpt-5.x / o-series endpoint). The
# protocol choice DRIVES the registered model's ``api`` field (see
# :data:`_PROTOCOL_API`); auth storage itself is protocol-agnostic (it stores the
# key under the user-chosen provider id, so the choice is also surfaced in the
# honest fallback note).
_CUSTOM_PROTOCOLS: list[str] = [
    "OpenAI-compatible",
    "OpenAI-compatible (Responses API)",
    "Anthropic-compatible",
    "Gemini-compatible",
]

_METHOD_OAUTH = "Using OAuth (sign in to a subscription / account)"
_METHOD_API_KEY = "Using an API key (built-in provider)"
_METHOD_CUSTOM = "Custom provider (OpenAI / Anthropic / Gemini-compatible endpoint)"


async def run_login(
    *,
    auth_storage: Any,
    select: Callable[..., Awaitable[str | None]],
    prompt_input: Callable[..., Awaitable[str | None]],
    confirm: Callable[..., Awaitable[bool]],
    notify: Callable[..., None],
    commit: Callable[[object], None],
    multiselect: Callable[..., Awaitable[Any]] | None = None,
    model_registry: Any = None,
    settings_manager: Any = None,
) -> None:
    """Drive the ``/login`` wizard end-to-end (Sprint WP-8, Feature 1).

    Module-level + dependency-injected (duck-typed ``auth_storage`` + the dialog
    callables + ``commit``) so the whole flow is unit-testable without
    prompt-toolkit. ``shell.py`` wires the live
    :class:`aelix_coding_agent.tui.context.AelixTUIContext`
    ``select`` / ``input`` / ``confirm`` / ``notify`` + the output committer.

    Step 1 picks a method; each method has its own sub-flow. Esc/cancel at any
    prompt aborts cleanly (no write). Every exception is surfaced as a red line.
    ``confirm`` is accepted for parity with the wiring contract (custom-provider
    flows may grow a confirmation later); it is currently unused by the happy
    paths but kept in the signature so the seam is stable.
    """

    from rich.panel import Panel  # local import keeps this module import-light
    from rich.text import Text

    if auth_storage is None:
        commit(Text("Login unavailable (no auth storage).", style="bold red"))
        return

    # Issue #77 — extension-contributed login providers appear in the method
    # list AFTER the three built-ins; picking one runs its custom auth handler
    # (e.g. a corporate 'telnaut' whose sign-in prompts for an employee number).
    ext_login_labels, ext_login_by_label = _collect_login_providers()

    method = await select(
        "Add a provider",
        [_METHOD_OAUTH, _METHOD_API_KEY, _METHOD_CUSTOM, *ext_login_labels],
    )
    if not method:
        return

    if method == _METHOD_OAUTH:
        await _run_oauth(
            auth_storage=auth_storage,
            select=select,
            prompt_input=prompt_input,
            notify=notify,
            commit=commit,
            Panel=Panel,
            Text=Text,
        )
    elif method == _METHOD_API_KEY:
        await _run_api_key(
            auth_storage=auth_storage,
            select=select,
            prompt_input=prompt_input,
            commit=commit,
            Text=Text,
            model_registry=model_registry,
        )
    elif method == _METHOD_CUSTOM:
        await _run_custom(
            auth_storage=auth_storage,
            select=select,
            prompt_input=prompt_input,
            commit=commit,
            Text=Text,
            multiselect=multiselect,
            model_registry=model_registry,
            settings_manager=settings_manager,
        )
    elif method in ext_login_by_label:
        await _run_login_provider(
            ext_login_by_label[method],
            auth_storage=auth_storage,
            select=select,
            prompt_input=prompt_input,
            confirm=confirm,
            notify=notify,
            commit=commit,
            Text=Text,
        )
    else:  # pragma: no cover — select only returns one of the offered labels
        commit(Text(f"✖ login: unknown method {method!r}", style="bold red"))


def _collect_login_providers() -> tuple[list[str], dict[str, Any]]:
    """Build the (labels, label->provider) pair for extension login providers.

    Labels are the providers' display names, de-duplicated against each other
    and the three built-in method strings so ``select`` can round-trip the pick.
    A broken registry never breaks ``/login`` — any failure yields empties.
    """

    labels: list[str] = []
    by_label: dict[str, Any] = {}
    try:
        from aelix_coding_agent.login_registry import get_login_providers

        providers = list(get_login_providers())
    except Exception:  # noqa: BLE001 — a bad registry must never break /login
        return [], {}
    builtins = (_METHOD_OAUTH, _METHOD_API_KEY, _METHOD_CUSTOM)
    for provider in providers:
        base = str(getattr(provider, "name", "") or getattr(provider, "id", "") or "").strip()
        if not base:
            continue
        label, n = base, 2
        while label in by_label or label in builtins:
            label = f"{base} ({n})"
            n += 1
        labels.append(label)
        by_label[label] = provider
    return labels, by_label


async def _run_login_provider(
    provider: Any,
    *,
    auth_storage: Any,
    select: Callable[..., Awaitable[str | None]],
    prompt_input: Callable[..., Awaitable[str | None]],
    confirm: Callable[..., Awaitable[bool]],
    notify: Callable[..., None],
    commit: Callable[[object], None],
    Text: Any,
) -> None:
    """Run an extension-registered login provider's custom auth flow (Issue #77).

    Hands the extension's ``authenticate`` handler a :class:`LoginContext` (the
    same masked ``select`` / ``prompt`` / ``confirm`` / ``notify`` dialogs the
    built-in sub-flows use) so it can collect whatever credentials it needs — a
    corporate 'telnaut' asks for an employee number here — then persists the
    returned credential under the provider id via ``auth_storage`` (the extension
    never touches the protected auth store itself). A ``None`` return is a clean
    cancel; any handler exception degrades to a red line, never crashing the REPL.
    """

    from aelix_coding_agent.login_registry import LoginAuthenticate, LoginContext

    provider_id = getattr(provider, "id", None)
    name = str(getattr(provider, "name", None) or provider_id or "?")
    authenticate = getattr(provider, "authenticate", None)
    if not provider_id or not callable(authenticate):
        commit(Text(f"✖ login: '{name}' has no valid authenticate handler.", style="bold red"))
        return

    ctx = LoginContext(
        select=select, prompt=prompt_input, confirm=confirm, notify=notify
    )
    try:
        credential = await cast(LoginAuthenticate, authenticate)(ctx)
    except Exception as exc:  # noqa: BLE001 — an extension handler must not crash the REPL
        commit(Text(f"✖ {name} login failed: {exc}", style="bold red"))
        return

    if credential is None:
        return  # explicit cancel — nothing stored
    if not isinstance(credential, str) or not credential.strip():
        commit(Text(f"✖ {name} login returned no credential — nothing stored.", style="bold red"))
        return

    try:
        await auth_storage.set_api_key(str(provider_id), credential.strip())
    except Exception as exc:  # noqa: BLE001 — persistence failure must degrade
        commit(Text(f"✖ failed to store credential: {exc}", style="bold red"))
        return

    commit(Text(f"signed in to {name}", style="green"))
    await _commit_status(
        auth_storage=auth_storage, provider=str(provider_id), commit=commit, Text=Text
    )


async def _run_oauth(
    *,
    auth_storage: Any,
    select: Callable[..., Awaitable[str | None]],
    prompt_input: Callable[..., Awaitable[str | None]],
    notify: Callable[..., None],
    commit: Callable[[object], None],
    Panel: Any,
    Text: Any,
) -> None:
    """OAuth sub-flow: pick a provider, run the device/browser login, persist."""

    try:
        from aelix_ai.oauth._registry import get_oauth_providers
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ OAuth unavailable: {exc}", style="bold red"))
        return

    try:
        providers = list(get_oauth_providers())
    except Exception as exc:  # noqa: BLE001
        commit(Text(f"✖ OAuth provider list failed: {exc}", style="bold red"))
        return
    if not providers:
        commit(Text("No OAuth providers available.", style="yellow"))
        return

    labels = [getattr(p, "name", getattr(p, "id", "?")) for p in providers]
    chosen_label = await select("Sign in with", labels)
    if not chosen_label:
        return
    try:
        idx = labels.index(chosen_label)
    except ValueError:  # pragma: no cover — select returns an offered label
        commit(Text(f"✖ login: unknown provider {chosen_label!r}", style="bold red"))
        return
    provider = providers[idx]
    provider_id = getattr(provider, "id", None)
    provider_name = getattr(provider, "name", provider_id) or "?"
    if not provider_id:
        commit(Text("✖ login: provider has no id.", style="bold red"))
        return

    callbacks = _build_oauth_callbacks(
        select=select,
        prompt_input=prompt_input,
        notify=notify,
        commit=commit,
        Panel=Panel,
    )

    try:
        await auth_storage.login(provider_id, callbacks)
    except RuntimeError as exc:
        # AuthStorage.login raises RuntimeError("Unknown OAuth provider: ...").
        commit(Text(f"✖ {exc}", style="bold red"))
        return
    except Exception as exc:  # noqa: BLE001 — any login failure must degrade
        commit(Text(f"✖ OAuth login failed: {exc}", style="bold red"))
        return

    commit(Text(f"signed in to {provider_name}", style="green"))


def _build_oauth_callbacks(
    *,
    select: Callable[..., Awaitable[str | None]],
    prompt_input: Callable[..., Awaitable[str | None]],
    notify: Callable[..., None],
    commit: Callable[[object], None],
    Panel: Any,
) -> Any:
    """Map the TUI dialog callables onto an ``OAuthLoginCallbacks`` bundle.

    - ``on_auth`` → commit a Panel with the verification URL + instructions and
      best-effort open the URL in a browser (guarded — a headless box must not
      crash the flow).
    - ``on_prompt`` / ``on_manual_code_input`` → ``prompt_input``.
    - ``on_select`` → ``select`` over the option labels (returns the option id).
    - ``on_progress`` → ``notify``.
    """

    from aelix_ai.oauth.types import (
        OAuthAuthInfo,
        OAuthLoginCallbacks,
        OAuthPrompt,
        OAuthSelectPrompt,
    )
    from rich.text import Text

    async def on_auth(info: OAuthAuthInfo) -> None:
        url = getattr(info, "url", "") or ""
        instructions = getattr(info, "instructions", None)
        body_lines = [f"Open this URL to authorize:\n{url}"]
        if instructions:
            body_lines.append(str(instructions))
        commit(
            Panel(
                Text("\n\n".join(body_lines)),
                title="OAuth sign-in",
                border_style="cyan",
            )
        )
        if url:
            # Best-effort browser launch; a headless / no-display environment
            # raises (or returns False) and the user falls back to the URL.
            with contextlib.suppress(Exception):
                webbrowser.open(url)

    async def on_prompt(prompt: OAuthPrompt) -> str:
        message = getattr(prompt, "message", "") or "Enter value"
        placeholder = getattr(prompt, "placeholder", None)
        # The OAuth device/manual flow carries the verification/authorization
        # code through this prompt — mask it so the secret is never echoed.
        answer = await prompt_input(message, placeholder=placeholder, password=True)
        return answer or ""

    async def on_manual_code_input() -> str:
        answer = await prompt_input("Paste the authorization code", password=True)
        return answer or ""

    async def on_progress(message: str) -> None:
        notify(str(message), kind="info")

    async def on_select(prompt: OAuthSelectPrompt) -> str | None:
        message = getattr(prompt, "message", "") or "Select"
        options = list(getattr(prompt, "options", []) or [])
        labels = [getattr(o, "label", getattr(o, "id", "?")) for o in options]
        chosen = await select(message, labels)
        if not chosen:
            return None
        try:
            idx = labels.index(chosen)
        except ValueError:  # pragma: no cover — select returns an offered label
            return None
        return getattr(options[idx], "id", None)

    return OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_progress=on_progress,
        on_manual_code_input=on_manual_code_input,
        on_select=on_select,
    )


async def _run_api_key(
    *,
    auth_storage: Any,
    select: Callable[..., Awaitable[str | None]],
    prompt_input: Callable[..., Awaitable[str | None]],
    commit: Callable[[object], None],
    Text: Any,
    model_registry: Any = None,
) -> None:
    """API-key sub-flow: pick a provider, store a non-empty key.

    The picker lists the built-in providers (``ENV_API_KEYS``) UNIONED with any
    extension-registered providers (``model_registry.get_registered_providers``,
    Issue #77) so a provider an extension added via ``register_provider`` can take
    a plain API key here. The raw provider id is the selectable label so
    ``set_api_key(id, key)`` stores under the correct id.
    """

    try:
        from aelix_ai.providers._env_api_keys import ENV_API_KEYS

        provider_ids = set(ENV_API_KEYS.keys())
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ provider list failed: {exc}", style="bold red"))
        return
    # Union in extension-registered provider ids (best-effort — a missing/odd
    # registry must not break the built-in list).
    with contextlib.suppress(Exception):
        if model_registry is not None:
            provider_ids |= set(model_registry.get_registered_providers())
    providers = sorted(provider_ids)
    if not providers:
        commit(Text("No built-in providers available.", style="yellow"))
        return

    provider = await select("Provider", providers)
    if not provider:
        return

    # ``password=True`` masks the secret so it is never echoed to the screen or
    # left in the terminal scrollback (WP-8 Feature 1 secret-entry hardening).
    key = await prompt_input(f"API key for {provider}", password=True)
    if not key or not key.strip():
        commit(Text("✖ no API key entered — nothing stored.", style="bold red"))
        return

    try:
        await auth_storage.set_api_key(provider, key.strip())
    except Exception as exc:  # noqa: BLE001 — persistence failure must degrade
        commit(Text(f"✖ failed to store key: {exc}", style="bold red"))
        return

    commit(Text(f"API key stored for {provider}", style="green"))
    await _commit_status(
        auth_storage=auth_storage, provider=provider, commit=commit, Text=Text
    )


# Custom-provider protocol → the registered adapter ``api`` id. All now have a
# native adapter, so their models can be auto-fetched + registered:
# OpenAI-compatible (``openai-completions``), OpenAI-compatible Responses API
# (``openai-responses`` — same OpenAI-shaped ``/v1/models`` catalog probe with
# Bearer auth, but turns run against ``/v1/responses``; adapter un-hidden in #15
# Workflow B / ADR-0172), Anthropic-compatible (``anthropic-messages``,
# OpenAI-shaped ``/v1/models``), and Gemini-compatible (``google-generative-ai``,
# the Gemini Developer API ListModels — #15/ADR-0173 un-hid the adapter, #36
# wires it here). ``google-vertex`` is intentionally NOT offered (OAuth/ADC + a
# ``{location}`` base-url, no API-key /models list) — a Vertex custom endpoint
# stays on the honest manual-note fallback.
_PROTOCOL_API: dict[str, str | None] = {
    "OpenAI-compatible": "openai-completions",
    "OpenAI-compatible (Responses API)": "openai-responses",
    "Anthropic-compatible": "anthropic-messages",
    "Gemini-compatible": "google-generative-ai",
}


async def _run_custom(
    *,
    auth_storage: Any,
    select: Callable[..., Awaitable[str | None]],
    prompt_input: Callable[..., Awaitable[str | None]],
    commit: Callable[[object], None],
    Text: Any,
    multiselect: Callable[..., Awaitable[Any]] | None = None,
    model_registry: Any = None,
    settings_manager: Any = None,
) -> None:
    """Custom-provider sub-flow: pick a protocol, gather id/url/key, store key.

    For an endpoint with a registered adapter — **OpenAI-compatible**,
    **Anthropic-compatible** (whose ``/v1/models`` is OpenAI-shaped), or
    **Gemini-compatible** (``google-generative-ai``, the Gemini Developer API
    ListModels) — and when the host wired ``multiselect`` + ``model_registry``,
    the flow then fetches ``{base_url}/models``, lets the user pick which models
    to add, and writes them to ``models.json`` so they appear in ``/model``
    immediately — no manual models.json editing. Every other case (no adapter, no
    fetch wiring, a fetch/registration failure) degrades to the HONEST "add via
    models.json" note; the stored key is never lost.
    """

    protocol = await select("Endpoint protocol", list(_CUSTOM_PROTOCOLS))
    if not protocol:
        return

    provider_id = await prompt_input("Provider id (e.g. my-endpoint)")
    if not provider_id or not provider_id.strip():
        commit(Text("✖ no provider id entered — nothing stored.", style="bold red"))
        return
    provider_id = provider_id.strip()

    base_url = await prompt_input("Base URL (e.g. https://host/v1)")
    if base_url is None:
        # Esc on the URL prompt aborts; an empty string is allowed (some
        # endpoints default the base url), but a cancel is an explicit abort.
        return
    base_url = base_url.strip()

    # ``password=True`` masks the secret so it is never echoed to the screen or
    # left in the terminal scrollback (WP-8 Feature 1 secret-entry hardening).
    key = await prompt_input(f"API key for {provider_id}", password=True)
    if not key or not key.strip():
        commit(Text("✖ no API key entered — nothing stored.", style="bold red"))
        return
    key = key.strip()

    try:
        await auth_storage.set_api_key(provider_id, key)
    except Exception as exc:  # noqa: BLE001 — persistence failure must degrade
        commit(Text(f"✖ failed to store key: {exc}", style="bold red"))
        return

    commit(Text(f"API key stored for {provider_id}", style="green"))

    # Try to fetch + register models so the user doesn't have to hand-edit
    # models.json. This covers every protocol with a registered adapter that
    # exposes a model-list endpoint: OpenAI-compatible (``openai-completions``)
    # and its Responses-API variant (``openai-responses`` — the same OpenAI-shaped
    # ``/v1/models`` Bearer probe), Anthropic-compatible (``anthropic-messages``,
    # OpenAI-shaped ``/v1/models``), and Gemini-compatible (``google-generative-ai``,
    # Gemini ListModels with ``x-goog-api-key`` — #36/#15). Issue #49: an
    # Anthropic-compatible custom provider used to show "no model registered".
    api = _PROTOCOL_API.get(protocol)
    if (
        api is not None
        and base_url
        and multiselect is not None
        and model_registry is not None
    ):
        registered = await _register_custom_models(
            provider_id=provider_id,
            base_url=base_url,
            api=api,
            api_key=key,
            model_registry=model_registry,
            multiselect=multiselect,
            commit=commit,
            Text=Text,
            settings_manager=settings_manager,
        )
        if registered:
            return  # success message already committed

    # HONEST fallback note: the auth half is done, but no model was registered
    # (other protocol, no fetch wiring, empty/failed fetch). The base URL is NOT
    # persisted by auth storage (only the API key is) — surface it as a reminder
    # of what to put in the models.json entry rather than implying it is wired.
    note_lines = [
        f"Stored a {protocol} API key for '{provider_id}'.",
        "",
        "NOTE: the credential is saved, but no model was registered yet.",
        "Add the provider's model(s) via models.json or the --models flag for",
        "them to appear in /model — auth alone does not register a model.",
    ]
    if base_url:
        note_lines.append("")
        note_lines.append(
            f"Use this base URL in the models.json entry (NOT stored here): {base_url}"
        )
    commit(Text("\n".join(note_lines), style="yellow"))


# Anthropic pins its API version in the official SDK at ``2023-06-01`` (it sends
# this on every request, including ``GET /v1/models``). We mirror that pinned
# value so a genuine Anthropic-compatible endpoint accepts the model-list probe.
_ANTHROPIC_VERSION = "2023-06-01"


def _model_list_headers(api: str | None, api_key: str) -> dict[str, str]:
    """Auth headers for the ``GET {base_url}/models`` probe, keyed by ``api``.

    A genuine Anthropic-compatible endpoint (``anthropic-messages``) authenticates
    with ``x-api-key`` and REQUIRES an ``anthropic-version`` header — a
    ``Authorization: Bearer`` token 401s there. The Gemini Developer API
    (``google-generative-ai``) authenticates with ``x-goog-api-key`` (the
    google-genai SDK header) — Bearer also 401s there. OpenAI-shaped endpoints
    take the key as a Bearer token. No key → no auth header (some endpoints list
    models unauthenticated).
    """

    if not api_key:
        return {}
    if api is not None and api.startswith("anthropic"):
        return {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
    if api is not None and api.startswith("google"):
        return {"x-goog-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


# The Gemini Developer API paginates ListModels via a ``nextPageToken`` — follow
# it, but cap the follow so a misbehaving / adversarial endpoint can never spin
# the login flow forever. 20 pages × Gemini's default page size covers every
# real model list with room to spare (each page is de-duped into ``ids`` anyway).
_MAX_MODEL_LIST_PAGES = 20


def _model_list_items(data: Any) -> Any:
    """Pull the model array out of the common ListModels response shapes.

    ``{"data": [...]}`` (OpenAI), ``{"models": [...]}`` (Gemini ListModels), or a
    bare list. ``None`` when the shape is unrecognized (the caller treats it as
    empty).
    """

    if isinstance(data, dict):
        items = data.get("data")
        if items is None:
            items = data.get("models")
        return items
    if isinstance(data, list):
        return data
    return None


def _gemini_supports_generate_content(item: dict[str, Any]) -> bool:
    """``True`` iff a Gemini ListModels item can serve ``generateContent``.

    Gemini stamps each model with ``supportedGenerationMethods`` (e.g.
    ``["generateContent", "countTokens"]``); embedding / imagen / aqa-only models
    omit ``generateContent`` and are unusable as chat models, so they are dropped.
    Conservative when the field is ABSENT (or not a list) — KEEP the item rather
    than risk over-filtering a valid endpoint whose ListModels omits the
    capability array; only a present list that lacks ``generateContent`` is
    dropped. Applied for ``google-*`` apis ONLY (see :func:`_fetch_openai_model_ids`).
    """

    methods = item.get("supportedGenerationMethods")
    if isinstance(methods, list):
        return "generateContent" in methods
    return True


async def _fetch_openai_model_ids(
    base_url: str, api_key: str, *, api: str | None = None, timeout: float = 10.0
) -> list[str]:
    """``GET {base_url}/models`` → sorted, de-duped model ids.

    Handles the common response shapes: ``{"data": [{"id": …}]}`` (OpenAI),
    ``{"models": [{"name": …}]}`` (Gemini ListModels), and a bare list. Raises on
    transport / HTTP error (the caller degrades). The auth headers depend on
    ``api``: an Anthropic-compatible endpoint gets ``x-api-key`` +
    ``anthropic-version``, the Gemini Developer API gets ``x-goog-api-key``, and
    everything else sends the key as a Bearer token (see
    :func:`_model_list_headers`).

    For ``google-*`` apis two Gemini-only refinements apply (ADR-0190, the
    optional polish deferred in ADR-0175 §Remaining); the OpenAI / Anthropic
    paths are untouched — exactly one GET, no capability filter:

    - The ``models/`` prefix Gemini returns on each ``name`` is stripped so ids
      match the catalog id (``gemini-2.0-flash``).
    - Only items whose ``supportedGenerationMethods`` includes ``generateContent``
      are kept (drops embedding / imagen / aqa-only models); items MISSING the
      field are kept — see :func:`_gemini_supports_generate_content`.
    - ``nextPageToken`` is followed (``?pageToken=<token>`` appended to the same
      ``/models`` URL, key + base preserved) and ids accumulate across pages
      until the token is absent/empty, repeats, or ``_MAX_MODEL_LIST_PAGES`` is
      reached — a guard against an infinite pagination loop.
    """

    from urllib.parse import quote

    import httpx

    is_google = api is not None and api.startswith("google")
    url = base_url.rstrip("/") + "/models"
    headers = _model_list_headers(api, api_key)
    ids: set[str] = set()
    async with httpx.AsyncClient(timeout=timeout) as client:
        page_url = url
        seen_tokens: set[str] = set()
        for _ in range(_MAX_MODEL_LIST_PAGES):
            resp = await client.get(page_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            for item in _model_list_items(data) or []:
                if isinstance(item, str):
                    ids.add(item)
                    continue
                if not isinstance(item, dict):
                    continue
                # generateContent capability filter — google-* apis ONLY.
                if is_google and not _gemini_supports_generate_content(item):
                    continue
                mid = item.get("id") or item.get("name")
                if not mid:
                    continue
                mid = str(mid)
                # Gemini ListModels returns ``name: "models/gemini-2.0-flash"``;
                # strip the ``models/`` prefix so the registered id matches the
                # catalog id (``gemini-2.0-flash``) and reads cleanly in /model.
                if is_google and mid.startswith("models/"):
                    mid = mid[len("models/") :]
                ids.add(mid)

            # Only the Gemini Developer API paginates; OpenAI / Anthropic do a
            # single GET (this breaks on the first pass for them). Stop when the
            # token is absent/empty (last page) or repeats (endpoint looping).
            next_token = ""
            if is_google and isinstance(data, dict):
                next_token = str(data.get("nextPageToken") or "")
            if not is_google or not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            sep = "&" if "?" in url else "?"
            page_url = f"{url}{sep}pageToken={quote(next_token, safe='')}"
    return sorted(ids)


def _write_custom_models_json(
    path: str,
    provider_id: str,
    base_url: str,
    api: str,
    api_key: str,
    model_ids: list[str],
) -> None:
    """Merge a custom provider + its models into ``models.json`` at ``path``.

    Creates the file if absent; merges into an existing config (preserving other
    providers + any extra fields already on this provider's models). The
    ``apiKey`` IS written — the models.json loader's semantic validator REQUIRES
    it for a non-built-in provider that defines models
    (``models_json.validate_config_semantics``); without it the whole file is
    rejected and the custom models never load. To match ``auth.json``'s
    protection, the file is chmod-ed ``0o600`` after the atomic write so the
    stored secret is owner-only.
    """

    import json
    import os
    from pathlib import Path

    p = Path(path)
    config: dict[str, Any] = {}
    if p.exists():
        with contextlib.suppress(Exception):
            from aelix_coding_agent.models_json import strip_json_comments

            text = p.read_text(encoding="utf-8")
            if text.strip():
                loaded = json.loads(strip_json_comments(text))
                if isinstance(loaded, dict):
                    config = loaded
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = config["providers"] = {}
    entry = providers.get(provider_id)
    if not isinstance(entry, dict):
        entry = {}
    entry["name"] = entry.get("name") or provider_id
    if base_url:
        entry["baseUrl"] = base_url
    entry["api"] = api
    entry["apiKey"] = api_key  # REQUIRED by the loader's semantic validator.
    # Merge model ids, keeping any existing model dict (with its extra fields).
    existing: dict[str, Any] = {
        m["id"]: m
        for m in entry.get("models", [])
        if isinstance(m, dict) and m.get("id")
    }
    for mid in model_ids:
        existing.setdefault(mid, {"id": mid})
    entry["models"] = [existing[k] for k in sorted(existing)]
    providers[provider_id] = entry

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    tmp.replace(p)
    # The file now holds a secret — restrict to owner-only (mirrors auth.json).
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)


def _deauthorize_provider_in_models_json(path: str, provider_id: str) -> bool:
    """De-authorize ``provider_id`` in the models.json at ``path`` WITHOUT losing
    user-authored config.

    A custom-provider ``/login`` writes the provider's ``apiKey`` into models.json
    (:func:`_write_custom_models_json`); that copy keeps ``has_configured_auth``
    :data:`True` even after ``auth.json`` is cleared. This strips ONLY the ``apiKey``
    field — preserving the block's model definitions / ``modelOverrides`` /
    ``baseUrl`` (which may be HAND-AUTHORED: the /login fallback explicitly tells
    users to add models via models.json) — so the provider drops out of
    ``get_available`` while its configuration survives a re-login. The block itself
    is deleted only when stripping the key leaves it with no substantive content (a
    pure credential holder).

    Returns :data:`True` iff the file was changed. A missing / empty / unparseable
    file, an absent provider block, or a block carrying NO ``apiKey`` (e.g. a
    built-in provider's hand-authored override-only block, or one whose auth lives
    entirely in ``auth.json``) is a no-op → :data:`False`, leaving that config
    untouched. Rewrites atomically as owner-only ``0o600`` — the file may hold OTHER
    providers' secrets. NOTE: like :func:`_write_custom_models_json`, the rewrite
    reformats and drops any JSONC ``//`` comments.
    """

    import json
    import os
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.strip():
        return False

    from aelix_coding_agent.models_json import strip_json_comments

    try:
        loaded = json.loads(strip_json_comments(text))
    except ValueError:
        return False  # malformed JSON — never clobber a file we cannot parse
    if not isinstance(loaded, dict):
        return False
    providers = loaded.get("providers")
    if not isinstance(providers, dict):
        return False
    entry = providers.get(provider_id)
    # No block, or a block with no apiKey to remove → nothing to de-authorize.
    # (Crucially, this leaves hand-authored override-only blocks fully intact.)
    if not isinstance(entry, dict) or "apiKey" not in entry:
        return False

    del entry["apiKey"]
    # Keep the block if it still carries substantive config (model defs / overrides /
    # connection settings); drop it only when it was a pure credential holder.
    has_substance = any(
        entry.get(k)
        for k in ("models", "baseUrl", "headers", "compat", "modelOverrides")
    )
    if not has_substance:
        del providers[provider_id]

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
    # chmod the tmp BEFORE the atomic replace so the live file is never briefly
    # world-readable while it still holds other providers' secrets.
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    tmp.replace(p)
    return True


def _scoped_hidden_ids(
    settings_manager: Any,
    provider_id: str,
    model_ids: list[str],
    model_registry: Any = None,
) -> list[str]:
    """Subset of ``model_ids`` a concrete ``enabled_models`` allow-list HIDES from /model.

    Returns ``[]`` when no scope is active (``enabled_models`` is the ``None``/``[]``
    "all enabled" sentinel), when every id already matches, OR when the allow-list
    matches ZERO models in the available catalog — because :func:`scoped_available`
    then triggers its empty-match LOCKOUT GUARD and shows the FULL list, so nothing
    is actually hidden. Reuses the ``/scoped-models`` identity matcher
    (:func:`_pattern_matches`) so the verdict tracks what :func:`scoped_available`
    actually shows. Never raises.
    """

    if settings_manager is None:
        return []
    try:
        patterns = settings_manager.get_enabled_models()
    except Exception:  # noqa: BLE001 — a settings read must never break /login
        return []
    if not patterns:  # None (sentinel) or [] → all enabled, nothing hidden
        return []
    from types import SimpleNamespace

    from aelix_coding_agent.core.scoped_models_filter import _pattern_matches

    # Mirror scoped_available's empty-match lockout guard: if the allow-list
    # matches NOTHING in the available catalog, /model degrades to the FULL list
    # (no lockout), so the just-added models are NOT hidden. Best-effort — a
    # registry without get_available() (or a read failure) falls through to the
    # pattern-only check below.
    if model_registry is not None:
        try:
            catalog = list(model_registry.get_available())
        except Exception:  # noqa: BLE001 — never break /login on a registry read
            catalog = None
        if catalog is not None and not any(
            any(_pattern_matches(p, m) for p in patterns) for m in catalog
        ):
            return []

    hidden: list[str] = []
    for mid in model_ids:
        model = SimpleNamespace(provider=provider_id, id=mid)
        if not any(_pattern_matches(p, model) for p in patterns):
            hidden.append(mid)
    return hidden


async def _register_custom_models(
    *,
    provider_id: str,
    base_url: str,
    api: str,
    api_key: str,
    model_registry: Any,
    multiselect: Callable[..., Awaitable[Any]],
    commit: Callable[[object], None],
    Text: Any,
    settings_manager: Any = None,
) -> bool:
    """Fetch the endpoint's models, let the user pick, persist + reload.

    Returns ``True`` when ≥1 model was registered (a success line is committed);
    ``False`` on any failure / empty fetch / no selection (the caller then shows
    the honest models.json note). Never raises.
    """

    try:
        ids = await _fetch_openai_model_ids(base_url, api_key, api=api)
    except Exception as exc:  # noqa: BLE001 — degrade to the manual note
        commit(
            Text(
                f"Could not fetch models from {base_url}/models ({exc}).",
                style="yellow",
            )
        )
        return False
    if not ids:
        commit(Text(f"No models returned by {base_url}/models.", style="yellow"))
        return False

    options = [(mid, mid, f"{provider_id} model") for mid in ids]
    try:
        result = await multiselect(
            f"Models from '{provider_id}' — choose which to add",
            options,
            selected=set(ids),
        )
    except Exception as exc:  # noqa: BLE001 — degrade to the manual note
        commit(Text(f"Model picker failed: {exc}", style="yellow"))
        return False
    if result is None:
        return False  # Esc — fall through to the note (key already stored)
    chosen, _toggles = result
    if not chosen:
        commit(Text("No models selected — none added.", style="yellow"))
        return False

    path = getattr(model_registry, "_models_json_path", None)
    if not path:
        from pathlib import Path

        from aelix_coding_agent.cli.config import get_agent_dir

        path = str(Path(get_agent_dir()) / "models.json")
    try:
        _write_custom_models_json(
            path, provider_id, base_url, api, api_key, sorted(chosen)
        )
    except Exception as exc:  # noqa: BLE001 — persistence failure must degrade
        commit(Text(f"✖ failed to write models.json: {exc}", style="bold red"))
        return False

    # Reload so the new models appear in /model immediately (the registry re-reads
    # models.json on every load — ADR-0140). Guarded: a reload failure still
    # leaves a valid persisted file picked up next launch.
    with contextlib.suppress(Exception):
        model_registry._load_models()

    # Scope-aware confirmation: a concrete /scoped-models allow-list can HIDE the
    # just-added models from /model (scoped_available intersects the auth catalog
    # with enabled_models). Warning honestly beats the old unconditional "they now
    # appear in /model", which was false whenever a restrictive scope was active.
    hidden = _scoped_hidden_ids(
        settings_manager, provider_id, sorted(chosen), model_registry=model_registry
    )
    if not hidden:
        commit(
            Text(
                f"Added {len(chosen)} model(s) for '{provider_id}' → they now "
                "appear in /model.",
                style="green",
            )
        )
    elif len(hidden) == len(chosen):
        commit(
            Text(
                f"Added {len(chosen)} model(s) for '{provider_id}', but your active "
                "/scoped-models allow-list hides them from /model — run "
                "/scoped-models to enable them.",
                style="yellow",
            )
        )
    else:
        shown = len(chosen) - len(hidden)
        commit(
            Text(
                f"Added {len(chosen)} model(s) for '{provider_id}' → {shown} now "
                f"appear in /model; {len(hidden)} are hidden by your /scoped-models "
                "allow-list (run /scoped-models to enable).",
                style="yellow",
            )
        )
    return True


async def _commit_status(
    *,
    auth_storage: Any,
    provider: str,
    commit: Callable[[object], None],
    Text: Any,
) -> None:
    """Best-effort confirmation reading ``get_auth_status`` (guarded)."""

    try:
        status = await auth_storage.get_auth_status(provider)
    except Exception:  # noqa: BLE001 — confirmation is best-effort; never raise
        return
    source = getattr(status, "source", None)
    if source:
        commit(Text(f"  ({provider} auth source: {source})", style="dim"))


async def run_logout(
    *,
    auth_storage: Any,
    select: Callable[..., Awaitable[str | None]],
    confirm: Callable[..., Awaitable[bool]],
    commit: Callable[[object], None],
    model_registry: Any = None,
    settings_manager: Any = None,
) -> None:
    """Drive the ``/logout`` flow end-to-end (Sprint WP-8, Feature 1).

    Loads the stored credentials, lists the stored provider ids
    (:meth:`AuthStorage.list`, which is populated only after ``load()``), lets
    the user pick one, confirms, then removes it via :meth:`AuthStorage.logout`.
    An empty store, an Esc at the picker, or a declined confirmation all return
    without removing anything. Every exception is surfaced as a red line.

    Cross-file de-authorization (S1): :meth:`AuthStorage.logout` clears only
    ``auth.json`` + the runtime override. When ``model_registry`` /
    ``settings_manager`` are supplied, this ALSO (a) removes the provider's block
    from ``models.json`` — a custom-provider ``/login`` persists its ``apiKey``
    there too, and that copy keeps ``has_configured_auth`` :data:`True` (so the
    "logged-out" provider's models kept resolving and a plaintext secret survived
    on disk) — and reloads the registry so the change takes effect in-session; and
    (b) prunes the provider's canonical ``provider/id`` entries from the
    ``settings.json`` scoped-models allow-list. An environment/``.env`` key is a
    separate auth source ``/logout`` cannot delete — it is still only warned about.
    Both cleanups are best-effort and never abort the logout.
    """

    from rich.text import Text  # local import keeps this module import-light

    if auth_storage is None:
        commit(Text("Logout unavailable (no auth storage).", style="bold red"))
        return

    try:
        await auth_storage.load()
        ids = list(auth_storage.list())
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ failed to read stored credentials: {exc}", style="bold red"))
        return

    if not ids:
        commit(Text("No stored credentials.", style="yellow"))
        return

    provider = await select("Remove credentials for", sorted(ids))
    if not provider:
        return

    ok = await confirm("Remove credentials", f"Remove stored credentials for {provider}?")
    if not ok:
        return

    try:
        await auth_storage.logout(provider)
    except Exception as exc:  # noqa: BLE001 — removal failure must degrade
        commit(Text(f"✖ failed to remove credentials: {exc}", style="bold red"))
        return

    commit(Text(f"Removed stored credentials for {provider}", style="green"))

    # S1 cross-file de-authorization: reconcile models.json + settings.json so a
    # logout actually de-authorizes (historically it touched only auth.json).
    if model_registry is not None:
        models_json_path = getattr(model_registry, "_models_json_path", None)
        if models_json_path:
            try:
                cleared = _deauthorize_provider_in_models_json(
                    models_json_path, provider
                )
            except Exception as exc:  # noqa: BLE001 — cleanup must not abort logout
                commit(
                    Text(
                        f"Note: could not update models.json for {provider}: {exc}",
                        style="yellow",
                    )
                )
            else:
                if cleared:
                    # Re-read models.json so has_configured_auth flips to False and
                    # the provider's models drop out of /model + /scoped-models now.
                    with contextlib.suppress(Exception):
                        model_registry._load_models()
                    commit(
                        Text(
                            f"Also cleared {provider}'s API key from models.json "
                            "(model definitions kept).",
                            style="green",
                        )
                    )

    if settings_manager is not None:
        # Prune scoped-models allow-list entries that refer ONLY to the logged-out
        # provider. Registry-aware (NOT a string prefix test): an entry is kept if it
        # still matches any model of a DIFFERENT provider — so a legacy bare id, or an
        # openrouter model whose id literally begins "openai/", is never dropped when
        # a same-named native provider logs out. An emptied list collapses to None.
        with contextlib.suppress(Exception):
            enabled = settings_manager.get_enabled_models()
            if enabled:
                from ..core.scoped_models_filter import _pattern_matches

                catalog = (
                    list(model_registry.get_all())
                    if model_registry is not None
                    else []
                )

                def _refers_only_to_logged_out(
                    entry: str, _catalog: Any = catalog, _provider: str = provider
                ) -> bool:
                    matched = [m for m in _catalog if _pattern_matches(entry, m)]
                    return bool(matched) and all(
                        m.provider == _provider for m in matched
                    )

                kept = [
                    e for e in enabled if not _refers_only_to_logged_out(e)
                ]
                if len(kept) != len(enabled):
                    settings_manager.set_enabled_models(kept or None)
                    await settings_manager.flush()

    # /logout clears the stored + runtime credentials, but an API key in the
    # ENVIRONMENT / .env is a separate auth source it cannot delete — the
    # provider's models stay available (still resolve via the env var). Warn so
    # the user isn't confused when the models don't disappear from /model.
    try:
        from aelix_ai.providers._env_api_keys import find_env_keys

        env_names = find_env_keys(provider)
    except Exception:  # noqa: BLE001 — a diagnostic must never break logout
        env_names = None
    if env_names:
        commit(
            Text(
                f"Note: {provider} still has an API key in your environment "
                f"({', '.join(env_names)}) — its models remain available. "
                "Unset it (or edit your .env) to fully disable.",
                style="yellow",
            )
        )


__all__ = ["run_login", "run_logout"]
