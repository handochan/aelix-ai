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
3. **Custom provider** — store an API key for an OpenAI / Anthropic /
   Gemini-compatible endpoint. The auth half works; the model itself must still
   be added via ``models.json`` / ``--models`` to become selectable — we say so
   HONESTLY rather than implying the model is ready.

Backing APIs are PROTECTED, READ-ONLY (``aelix_ai.oauth`` / ``aelix_ai.providers``);
this module only CALLS them. ``auth_storage`` is the SAME object behind
``ModelRegistry.create(auth_storage)`` so a stored key is visible to model
resolution immediately (no reload).

Every failure mode commits a red ``Text`` and returns — never crashes the REPL.
"""

from __future__ import annotations

import contextlib
import webbrowser
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# The three custom-provider protocol shapes (label -> protocol id). Pi exposes
# OpenAI / Anthropic / Gemini-compatible endpoints; the protocol choice is purely
# informational here (auth storage is protocol-agnostic — it stores the key under
# the user-chosen provider id), so we surface it in the honest note.
_CUSTOM_PROTOCOLS: list[str] = [
    "OpenAI-compatible",
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

    method = await select(
        "Add a provider",
        [_METHOD_OAUTH, _METHOD_API_KEY, _METHOD_CUSTOM],
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
        )
    else:  # pragma: no cover — select only returns one of the offered labels
        commit(Text(f"✖ login: unknown method {method!r}", style="bold red"))


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
) -> None:
    """API-key sub-flow: pick a built-in provider, store a non-empty key."""

    try:
        from aelix_ai.providers._env_api_keys import ENV_API_KEYS

        providers = sorted(ENV_API_KEYS.keys())
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ provider list failed: {exc}", style="bold red"))
        return
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


# Custom-provider protocol → the registered adapter ``api`` id. Only
# OpenAI-compatible and Anthropic-compatible have an adapter in this build;
# Gemini-compatible has none (``None``) so its models can't be auto-registered.
_PROTOCOL_API: dict[str, str | None] = {
    "OpenAI-compatible": "openai-completions",
    "Anthropic-compatible": "anthropic-messages",
    "Gemini-compatible": None,
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
) -> None:
    """Custom-provider sub-flow: pick a protocol, gather id/url/key, store key.

    For an **OpenAI-compatible** endpoint (when the host wired ``multiselect`` +
    ``model_registry``) the flow then fetches ``{base_url}/models``, lets the user
    pick which models to add, and writes them to ``models.json`` so they appear in
    ``/model`` immediately — no manual models.json editing. Every other case
    (other protocols, no fetch wiring, a fetch/registration failure) degrades to
    the HONEST "add via models.json" note; the stored key is never lost.
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

    # OpenAI-compatible → try to fetch + register models so the user doesn't have
    # to hand-edit models.json. The only protocol with both a ``/models`` list
    # endpoint AND a registered adapter (``openai-completions``).
    api = _PROTOCOL_API.get(protocol)
    if (
        protocol == "OpenAI-compatible"
        and api is not None
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


async def _fetch_openai_model_ids(
    base_url: str, api_key: str, *, timeout: float = 10.0
) -> list[str]:
    """``GET {base_url}/models`` (OpenAI-compatible) → sorted, de-duped model ids.

    Handles the common response shapes: ``{"data": [{"id": …}]}`` (OpenAI),
    ``{"models": [...]}``, and a bare list. Raises on transport / HTTP error
    (the caller degrades). The key is sent as a Bearer token.
    """

    import httpx

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    items: Any = None
    if isinstance(data, dict):
        items = data.get("data")
        if items is None:
            items = data.get("models")
    elif isinstance(data, list):
        items = data
    ids: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict):
            mid = item.get("id") or item.get("name")
            if mid:
                ids.add(str(mid))
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
) -> bool:
    """Fetch the endpoint's models, let the user pick, persist + reload.

    Returns ``True`` when ≥1 model was registered (a success line is committed);
    ``False`` on any failure / empty fetch / no selection (the caller then shows
    the honest models.json note). Never raises.
    """

    try:
        ids = await _fetch_openai_model_ids(base_url, api_key)
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

    commit(
        Text(
            f"Added {len(chosen)} model(s) for '{provider_id}' → they now appear "
            "in /model.",
            style="green",
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
) -> None:
    """Drive the ``/logout`` flow end-to-end (Sprint WP-8, Feature 1).

    Loads the stored credentials, lists the stored provider ids
    (:meth:`AuthStorage.list`, which is populated only after ``load()``), lets
    the user pick one, confirms, then removes it via :meth:`AuthStorage.logout`.
    An empty store, an Esc at the picker, or a declined confirmation all return
    without removing anything. Every exception is surfaced as a red line.
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


__all__ = ["run_login", "run_logout"]
