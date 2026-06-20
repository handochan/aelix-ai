"""P0 #7 (ITEM 6) — ``--api-key`` harness-auth wiring tests.

Pi parity: ``packages/coding-agent/src/main.ts:574-582`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` (``setRuntimeApiKey`` + the
"--api-key requires a model" diagnostic) plus the harness consumption at
``agent-harness.ts`` (Aelix: ``core.py:_make_stream_fn`` @3447).

Covers:

- ``--api-key`` sets a runtime override that the stream actually sees
  (``_make_auth_callback`` adapts the registry → harness dict contract).
- ``--api-key`` with no resolvable provider → Pi-verbatim error + exit 1.
- env-only auth still resolves WITHOUT a callback (regression guard for
  design (i): the harness callback stays ``None`` on the env path so the
  adapter's direct ``get_env_api_key`` resolution is untouched).
- ``--api-key`` runtime override wins over an ambient env var (cascade
  layer 1 beats layer 4).
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai import (
    AssistantDoneEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    clear_providers,
    register_provider,
)
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.entry import _async_main, _make_auth_callback
from aelix_coding_agent.model_registry import ModelRegistry


@pytest.fixture(autouse=True)
def _reset_registry() -> object:
    clear_providers()
    yield
    clear_providers()


class _FakePipedStdin:
    """Non-tty stdin so ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


def _register_key_capturing_provider(seen: dict[str, object]) -> None:
    """Register an ``anthropic-messages`` provider that records the
    ``api_key`` / ``headers`` the harness threads into the stream call."""

    async def capture(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen["api_key"] = options.api_key
        seen["headers"] = options.headers
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    register_provider("anthropic-messages", capture)


# === _make_auth_callback unit behavior =======================================


async def test_auth_callback_surfaces_runtime_key(tmp_path: Path) -> None:
    """A runtime override set on AuthStorage flows through the registry
    adapter as the harness dict ``{"apiKey": ...}``."""

    storage = AuthStorage(tmp_path / "auth.json")
    await storage.load()
    storage.set_runtime_api_key("anthropic", "sk-RUNTIME-123")
    registry = ModelRegistry.in_memory(storage)

    callback = _make_auth_callback(registry)
    result = await callback(Model(provider="anthropic", id="claude-3"))
    assert result == {"apiKey": "sk-RUNTIME-123", "headers": {}}


async def test_auth_callback_returns_none_when_no_key(tmp_path: Path) -> None:
    """No key + no headers → ``None`` ("no opinion") so the harness's
    "neither apiKey nor headers" guard is not tripped and the adapter env
    fallback can still resolve."""

    storage = AuthStorage(tmp_path / "auth.json")
    await storage.load()
    registry = ModelRegistry.in_memory(storage)

    callback = _make_auth_callback(registry)
    result = await callback(Model(provider="no-such-provider", id="x"))
    assert result is None


# === stream sees the key (end-to-end through the harness) ====================


async def test_runtime_key_reaches_the_stream(tmp_path: Path) -> None:
    """``set_runtime_api_key`` → ``_make_auth_callback`` → harness →
    ``options.api_key`` observed by the provider."""

    seen: dict[str, object] = {}
    _register_key_capturing_provider(seen)

    storage = AuthStorage(tmp_path / "auth.json")
    await storage.load()
    storage.set_runtime_api_key("anthropic", "sk-STREAM-SEES-ME")
    registry = ModelRegistry.in_memory(storage)

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", provider="anthropic", id="c"),
            get_api_key_and_headers=_make_auth_callback(registry),
        )
    )
    await harness.prompt("hi")
    assert seen["api_key"] == "sk-STREAM-SEES-ME"


# === --api-key with no provider → pi error + exit 1 ==========================


async def test_api_key_no_provider_exits_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secret = "sk-NOPROVIDER-LEAK"
    code = await _async_main(["--api-key", secret, "--print"])
    err = capsys.readouterr().err
    assert code == 1
    # Pi-verbatim diagnostic (main.ts:574-582).
    assert (
        "--api-key requires a model to be specified via --model, "
        "--provider/--model, or --models" in err
    )
    # SECURITY: never echo the key value.
    assert secret not in err


# === env-only path still authenticates (regression guard) ====================


async def test_env_only_path_still_authenticates() -> None:
    """Regression guard (design (i)): WITHOUT ``--api-key`` the harness
    callback is ``None``, so the adapter's direct ``get_env_api_key``
    resolution is preserved. The provider sees ``api_key=None`` from the
    harness and falls back to the env var itself (mirrored here by the
    capturing provider observing the ``None`` the harness passes through)."""

    seen: dict[str, object] = {}
    _register_key_capturing_provider(seen)

    # No get_api_key_and_headers → harness threads api_key=None (the adapter
    # would env-resolve). The turn must NOT raise an auth error.
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", provider="anthropic", id="c"),
        )
    )
    await harness.prompt("hi")
    assert seen["api_key"] is None


async def test_async_main_env_only_does_not_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full ``_async_main`` env-only path (no ``--api-key``) constructs
    the registry but never emits an --api-key diagnostic and never exits 1
    on the auth wiring (it may still exit 1 later for the missing real key —
    that is the model turn, not the wiring)."""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code = await _async_main(["--print"])
    err = capsys.readouterr().err
    assert code in (0, 1)
    assert "--api-key" not in err


# === --api-key overrides env (cascade layer 1 beats layer 4) =================


async def test_runtime_key_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runtime override (``--api-key``) wins over an ambient env var for
    the same provider — cascade layer 1 beats layer 4."""

    # Seed an env key for openrouter (cascade layer 4).
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-FROM-ENV")

    storage = AuthStorage(tmp_path / "auth.json")
    await storage.load()
    # Runtime override (cascade layer 1) for the same provider.
    storage.set_runtime_api_key("openrouter", "sk-FROM-RUNTIME")
    registry = ModelRegistry.in_memory(storage)

    callback = _make_auth_callback(registry)
    result = await callback(Model(provider="openrouter", id="x"))
    assert result is not None
    assert result["apiKey"] == "sk-FROM-RUNTIME"


# === _async_main WIRES --api-key (drives the real CLI success branch) =========


async def test_async_main_api_key_sets_override_and_attaches_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the ``--api-key`` SUCCESS branch through the real ``_async_main``
    CLI path (not a direct AuthStorage/harness construction).

    With a resolvable provider (``--provider anthropic --model claude-3``),
    ``_async_main`` MUST (a) call ``AuthStorage.set_runtime_api_key(provider,
    key)`` and (b) attach a non-``None`` ``get_api_key_and_headers`` callback
    onto the harness options. This pins the 'sets+overrides' wiring through the
    CLI — deleting either line (mutation: replace with ``pass``) fails here,
    whereas the unit tests above construct the objects directly and never
    exercise this branch.
    """

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Real provider so the (mocked) turn does not fail before the wiring runs.
    _register_key_capturing_provider({})

    set_calls: list[tuple[str, str]] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy_set(self: AuthStorage, provider: str, api_key: str) -> None:
        set_calls.append((provider, api_key))
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy_set)

    captured: dict[str, object] = {}
    real_build = entry_mod._build_harness_options

    async def _spy_build(*args: object, **kwargs: object) -> object:
        captured["get_api_key_and_headers"] = kwargs.get("get_api_key_and_headers")
        return await real_build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(entry_mod, "_build_harness_options", _spy_build)

    await _async_main(
        [
            "--api-key",
            "sk-CLI-WIRES-ME",
            "--provider",
            "anthropic",
            "--model",
            "claude-3",
            "--print",
        ]
    )

    # (a) the runtime override was set with (provider, key) from the CLI.
    assert ("anthropic", "sk-CLI-WIRES-ME") in set_calls
    # (b) a non-None auth callback was threaded onto the harness options.
    assert captured["get_api_key_and_headers"] is not None


async def test_async_main_env_only_leaves_callback_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-invariant env-only guard (design (i)): WITHOUT ``--api-key`` the
    harness options' ``get_api_key_and_headers`` MUST stay ``None`` so the
    adapter's direct ``get_env_api_key`` resolution is preserved.

    Mutation proof this guard catches: forcing
    ``get_api_key_and_headers = _make_auth_callback(...)`` on the env-only
    path (the exact regression) makes ``captured`` non-``None`` and fails here
    — unlike the prior tautological guards that only checked exit codes.
    """

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    captured: dict[str, object] = {"sentinel": object()}
    real_build = entry_mod._build_harness_options

    async def _spy_build(*args: object, **kwargs: object) -> object:
        captured["get_api_key_and_headers"] = kwargs.get("get_api_key_and_headers")
        return await real_build(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(entry_mod, "_build_harness_options", _spy_build)

    await _async_main(["--print"])

    # The factory ran (sentinel replaced) and threaded None on the env path.
    assert "get_api_key_and_headers" in captured
    assert captured["get_api_key_and_headers"] is None
