"""Sprint 6e · Phase 4.5 — AuthStorage layered cascade tests (P-133)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from aelix_ai.oauth.auth_storage import AuthStorage
from aelix_ai.oauth.types import OAuthCredentials


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    return tmp_path / "subdir" / "auth.json"


@pytest.fixture(autouse=True)
def _clear_auth_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_AUTH_PATH", raising=False)


@pytest.fixture(autouse=True)
def _clear_known_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe env vars used by ``get_env_api_key`` so tests are deterministic."""

    for key in (
        "ANTHROPIC_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "COPILOT_GITHUB_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


# === Runtime override layer ===


async def test_set_runtime_api_key_persists_in_memory(auth_path: Path) -> None:
    """Runtime override is in-memory only — never written to disk."""

    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_runtime_api_key("openai", "sk-runtime")
    assert await storage.get_api_key_cascade("openai") == "sk-runtime"
    # Even if ``load()`` created an empty auth.json sentinel for locking,
    # the runtime key MUST NOT have been written into it.
    if auth_path.exists():
        import json as _json

        contents = auth_path.read_text() or "{}"
        parsed = _json.loads(contents) if contents.strip() else {}
        assert "openai" not in parsed


async def test_remove_runtime_api_key_round_trip(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_runtime_api_key("openai", "sk-runtime")
    storage.remove_runtime_api_key("openai")
    assert await storage.get_api_key_cascade("openai") is None


# === Fallback resolver ===


async def test_set_fallback_resolver_used_in_cascade(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_fallback_resolver(lambda p: "FB-KEY" if p == "openai" else None)
    assert await storage.get_api_key_cascade("openai") == "FB-KEY"
    assert await storage.get_api_key_cascade("other") is None


async def test_include_fallback_false_skips_fallback(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_fallback_resolver(lambda _: "FB-KEY")
    assert await storage.get_api_key_cascade("any", include_fallback=False) is None


# === has_auth ===


async def test_has_auth_returns_true_for_runtime(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_runtime_api_key("openai", "sk-x")
    assert await storage.has_auth("openai") is True


async def test_has_auth_returns_true_for_stored(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-stored")
    assert await storage.has_auth("openai") is True


async def test_has_auth_returns_true_for_env(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    storage = AuthStorage(auth_path)
    await storage.load()
    assert await storage.has_auth("openai") is True


async def test_has_auth_returns_true_for_fallback(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_fallback_resolver(lambda _: "FB")
    assert await storage.has_auth("openai") is True


async def test_has_auth_returns_false_when_no_source(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    assert await storage.has_auth("unknown") is False


# === get_auth_status ===


async def test_get_auth_status_stored(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-stored")
    status = await storage.get_auth_status("openai")
    assert status.configured is True
    assert status.source == "stored"


async def test_get_auth_status_runtime(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_runtime_api_key("openai", "sk-x")
    status = await storage.get_auth_status("openai")
    assert status.configured is False
    assert status.source == "runtime"
    assert status.label == "--api-key"


async def test_get_auth_status_environment(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    storage = AuthStorage(auth_path)
    await storage.load()
    status = await storage.get_auth_status("openai")
    assert status.source == "environment"
    assert status.label == "OPENAI_API_KEY"


async def test_get_auth_status_fallback(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_fallback_resolver(lambda _: "FB")
    status = await storage.get_auth_status("openai")
    assert status.source == "fallback"
    assert status.label == "custom provider config"


async def test_get_auth_status_unconfigured(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    status = await storage.get_auth_status("unknown")
    assert status.configured is False
    assert status.source is None


# === list / has / get_all ===


async def test_list_has_get_all_consistent(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-1")
    await storage.set_api_key("anthropic", "sk-2")
    assert set(storage.list()) == {"openai", "anthropic"}
    assert storage.has("openai") is True
    assert storage.has("missing") is False
    all_data = storage.get_all()
    assert set(all_data.keys()) == {"openai", "anthropic"}
    # get_all returns a SHALLOW copy.
    all_data["mutated"] = {"type": "api_key", "key": "x"}
    assert "mutated" not in storage.list()


# === drain_errors ===


async def test_drain_errors_returns_and_clears(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()

    def _raising_resolver(_provider: str) -> str | None:
        raise RuntimeError("resolver boom")

    storage.set_fallback_resolver(_raising_resolver)
    # Triggers fallback path → resolver raises → error recorded.
    await storage.get_api_key_cascade("openai")
    drained = storage.drain_errors()
    assert len(drained) == 1
    assert "resolver boom" in str(drained[0])
    # Second drain is empty.
    assert storage.drain_errors() == []


# === login / logout ===


async def test_login_calls_provider_and_persists(auth_path: Path) -> None:
    from aelix_ai.oauth import register_oauth_provider, unregister_oauth_provider
    from aelix_ai.oauth.types import OAuthLoginCallbacks

    captured: dict[str, object] = {}

    class _StubProvider:
        id = "stub-login"
        name = "Stub"
        uses_callback_server = False

        async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
            captured["called"] = True
            return OAuthCredentials(
                refresh="r", access="a", expires=int(time.time() * 1000) + 10_000
            )

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            return credentials

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    register_oauth_provider(_StubProvider())
    try:
        storage = AuthStorage(auth_path)
        await storage.load()

        def on_auth(_info: object) -> None:
            return None

        def on_prompt(_p: object) -> str:
            return ""

        callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)
        await storage.login("stub-login", callbacks)
        assert captured.get("called") is True
        creds = await storage.get_oauth("stub-login")
        assert creds is not None
        assert creds.access == "a"
    finally:
        unregister_oauth_provider("stub-login")


async def test_login_unknown_provider_raises(auth_path: Path) -> None:
    from aelix_ai.oauth.types import OAuthLoginCallbacks

    storage = AuthStorage(auth_path)
    await storage.load()

    def on_auth(_info: object) -> None:
        return None

    def on_prompt(_p: object) -> str:
        return ""

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)
    with pytest.raises(RuntimeError) as ei:
        await storage.login("totally-fictional", callbacks)
    assert "Unknown OAuth provider" in str(ei.value)


async def test_logout_aliases_remove(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-1")
    await storage.logout("openai")
    assert storage.has("openai") is False


# === get_api_key_cascade precedence (Pi-strict) ===


async def test_cascade_runtime_beats_stored(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-stored")
    storage.set_runtime_api_key("openai", "sk-runtime")
    storage.set_fallback_resolver(lambda _: "sk-fallback")
    assert await storage.get_api_key_cascade("openai") == "sk-runtime"


async def test_cascade_stored_beats_env(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-stored")
    storage.set_fallback_resolver(lambda _: "sk-fallback")
    assert await storage.get_api_key_cascade("openai") == "sk-stored"


async def test_cascade_env_beats_fallback(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_fallback_resolver(lambda _: "sk-fallback")
    assert await storage.get_api_key_cascade("openai") == "sk-env"


async def test_cascade_fallback_is_last_resort(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    storage.set_fallback_resolver(lambda _: "sk-fallback")
    assert await storage.get_api_key_cascade("openai") == "sk-fallback"


async def test_cascade_returns_none_when_no_layer_has_key(auth_path: Path) -> None:
    storage = AuthStorage(auth_path)
    await storage.load()
    assert await storage.get_api_key_cascade("unknown") is None


# === Sprint 6e W6 (P-141) — resolve_config_value indirection ===


async def test_stored_api_key_resolves_env_var_indirection(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pi parity: stored ``"key": "MY_API_KEY"`` reads env ``MY_API_KEY``.

    Without ``resolve_config_value``, the env-var NAME would have leaked
    as the API key. With the helper wired, the env value is substituted.
    """

    monkeypatch.setenv("MY_API_KEY", "sk-from-env-indirection")
    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "MY_API_KEY")
    # Both the persistence-only and the cascade resolvers go through
    # ``resolve_config_value``.
    assert await storage.get_api_key("openai") == "sk-from-env-indirection"
    assert (
        await storage.get_api_key_cascade("openai")
        == "sk-from-env-indirection"
    )


async def test_stored_api_key_command_indirection_is_cached(
    auth_path: Path,
) -> None:
    """Pi parity: ``"key": "!<cmd>"`` runs the shell command and caches.

    The cache scope is per-AuthStorage-instance, so a second read of the
    same provider does NOT re-fork the shell.
    """

    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "!echo sk-from-shell")
    first = await storage.get_api_key("openai")
    second = await storage.get_api_key("openai")
    assert first == "sk-from-shell"
    assert second == "sk-from-shell"
    # Cache populated with the post-``!`` command key.
    assert storage._resolve_cache.get("echo sk-from-shell") == "sk-from-shell"


async def test_stored_api_key_literal_passes_through(auth_path: Path) -> None:
    """When the stored value is neither ``!cmd`` nor an env-var name, it
    passes through verbatim."""

    storage = AuthStorage(auth_path)
    await storage.set_api_key("openai", "sk-literal-12345")
    assert await storage.get_api_key("openai") == "sk-literal-12345"


# === Sprint 6e W6 (P-142) — reload-after-refresh-failure cross-process race ===


async def test_cascade_oauth_refresh_failure_returns_sibling_token(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pi parity: on OAuth refresh failure, re-read auth.json + return
    the sibling-process-written token if it is now non-expired.

    Pi auth-storage.ts:487-498 mirrors this exact recovery path.
    """

    from aelix_ai.oauth import (
        OAuthCredentials,
        register_oauth_provider,
        unregister_oauth_provider,
    )
    from aelix_ai.oauth.types import OAuthLoginCallbacks

    class _FlakyProvider:
        id = "race-test"
        name = "Race Test"
        uses_callback_server = False

        async def login(
            self, callbacks: OAuthLoginCallbacks
        ) -> OAuthCredentials:
            return OAuthCredentials(refresh="r", access="a", expires=1)

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            raise RuntimeError("upstream refresh boom")

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    register_oauth_provider(_FlakyProvider())
    try:
        storage = AuthStorage(auth_path)
        # Seed an expired entry.
        await storage.set_oauth(
            "race-test",
            OAuthCredentials(refresh="rt-old", access="at-old", expires=1),
        )
        # Simulate a sibling process that already refreshed the token
        # successfully and wrote a NEW non-expired entry to disk.
        sibling_creds = OAuthCredentials(
            refresh="rt-new",
            access="at-sibling",
            expires=int(time.time() * 1000) + 60_000,
        )
        import json

        auth_path.write_text(
            json.dumps(
                {
                    "race-test": {"type": "oauth", **sibling_creds.to_json()}
                }
            )
        )
        # The Pi recovery path: refresh raises → reload → return the
        # sibling-written token because it is now non-expired.
        result = await storage.get_api_key_cascade("race-test")
        assert result == "at-sibling"
    finally:
        unregister_oauth_provider("race-test")


async def test_cascade_oauth_refresh_failure_no_sibling_returns_none(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pi parity (P-142): refresh failure + no sibling-written token →
    return None WITHOUT falling through to env or fallback.

    Pi auth-storage.ts:498 explicitly returns undefined here; it does
    NOT consult env or fallback layers because the OAuth source is the
    declared authoritative one.
    """

    from aelix_ai.oauth import (
        OAuthCredentials,
        register_oauth_provider,
        unregister_oauth_provider,
    )
    from aelix_ai.oauth.types import OAuthLoginCallbacks

    class _AlwaysFailsProvider:
        id = "always-fails"
        name = "Always Fails"
        uses_callback_server = False

        async def login(
            self, callbacks: OAuthLoginCallbacks
        ) -> OAuthCredentials:
            return OAuthCredentials(refresh="r", access="a", expires=1)

        async def refresh_token(
            self, credentials: OAuthCredentials
        ) -> OAuthCredentials:
            raise RuntimeError("upstream refresh boom")

        def get_api_key(self, credentials: OAuthCredentials) -> str:
            return credentials.access

    register_oauth_provider(_AlwaysFailsProvider())
    monkeypatch.setenv("ALWAYS_FAILS_API_KEY", "sk-env-should-NOT-be-returned")
    try:
        storage = AuthStorage(auth_path)
        await storage.set_oauth(
            "always-fails",
            OAuthCredentials(refresh="rt", access="at", expires=1),
        )
        storage.set_fallback_resolver(lambda _: "sk-fallback-should-NOT-be-returned")

        result = await storage.get_api_key_cascade("always-fails")
        # Pi-strict: returns None, does NOT fall through.
        assert result is None
        # The recorded error is the wrapped refresh failure with the
        # original ``upstream refresh boom`` cause attached.
        drained = storage.drain_errors()
        assert len(drained) >= 1
        # The wrapped RuntimeError message + original ``__cause__``.
        wrapper = drained[0]
        assert "refresh OAuth token" in str(wrapper)
        cause = getattr(wrapper, "__cause__", None)
        assert cause is not None
        assert "upstream refresh boom" in str(cause)
    finally:
        unregister_oauth_provider("always-fails")
