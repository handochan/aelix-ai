"""Issue #23 — the first-run onboarding predicate.

``entry.should_offer_first_run_login`` owns the whole decision: on an
interactive launch at a real terminal, with no session continuation and no
explicit model intent, offer the ``/login`` wizard when NO provider credential
resolves from any auth layer.

Every registry in this file is a REAL :class:`ModelRegistry` over a REAL
:class:`AuthStorage` on ``tmp_path``. A mock with ``get_available() -> []``
would pass with or without the fix and would prove nothing about the six auth
layers ``has_configured_auth`` consults — which are exactly what "no
credentials" means. So each "credentials exist" case seeds a different layer
and asserts the predicate goes False through that layer alone.

pi has no equivalent: pi refuses only a NON-interactive run with no model
(``main.ts:660-663``) and never onboards interactively. This is aelix-original.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.providers._env_api_keys import ENV_API_KEYS
from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import should_offer_first_run_login
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput


@pytest.fixture(autouse=True)
def _scrub_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider API-key env var from the test process.

    Without this the developer's own ``ANTHROPIC_API_KEY`` / ``OPENROUTER_API_KEY``
    would make ``get_available()`` non-empty and every "fires" assertion would be
    a false RED (or, worse, a false GREEN once inverted).
    """

    for names in ENV_API_KEYS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


async def _registry(tmp_path: Path, *, models_json: dict | None = None) -> ModelRegistry:
    """A real registry over a real (empty) auth.json on ``tmp_path``."""

    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    models_json_path: str | None = None
    if models_json is not None:
        path = tmp_path / "models.json"
        path.write_text(json.dumps(models_json), encoding="utf-8")
        models_json_path = str(path)
    return ModelRegistry(auth, models_json_path)


def _call(
    registry: ModelRegistry,
    *,
    parsed: Args | None = None,
    app_mode: str = "interactive",
    stdin_is_tty: bool = True,
    stdout_is_tty: bool = True,
    depth: int = 0,
) -> bool:
    return should_offer_first_run_login(
        parsed if parsed is not None else Args(),
        app_mode,  # type: ignore[arg-type]
        registry,
        stdin_is_tty=stdin_is_tty,
        stdout_is_tty=stdout_is_tty,
        subagent_depth_value=depth,
    )


# === It fires ===============================================================


async def test_fires_on_a_true_first_run(tmp_path: Path) -> None:
    """The whole point: interactive TTY + zero resolvable credentials."""

    registry = await _registry(tmp_path)
    assert registry.get_available() == [], "fixture must be credential-free"
    assert registry.get_all(), "the built-in catalog must still be loaded"
    assert _call(registry) is True


# === It does not fire — one test per guarded context ========================


@pytest.mark.parametrize("app_mode", ["print", "json", "rpc"])
async def test_never_fires_outside_interactive(tmp_path: Path, app_mode: str) -> None:
    """--print / -p, --mode json and --mode rpc all land here.

    A wizard in any of these is a hang in someone's CI, which is why this is the
    first thing the predicate checks.
    """

    registry = await _registry(tmp_path)
    assert _call(registry, app_mode=app_mode) is False


async def test_never_fires_when_stdin_is_not_a_tty(tmp_path: Path) -> None:
    """Piped stdin. ``resolve_app_mode`` already turns this into "print", so the
    arm is belt-and-braces — assert it independently of the mode."""

    registry = await _registry(tmp_path)
    assert _call(registry, stdin_is_tty=False) is False


async def test_never_fires_when_stdout_is_not_a_tty(tmp_path: Path) -> None:
    """Redirected stdout. ``resolve_app_mode`` reads stdin ONLY, so without this
    arm ``aelix > log.txt`` on a real keyboard would open a modal into a file."""

    registry = await _registry(tmp_path)
    assert _call(registry, stdout_is_tty=False) is False


async def test_never_fires_in_a_subagent_child(tmp_path: Path) -> None:
    """AELIX_SUBAGENT_DEPTH > 0. Delegated children are already non-interactive
    (profile_to_argv forces --mode json -p --no-session or --mode rpc); this arm
    means no future argv can ever drop a child into a modal."""

    registry = await _registry(tmp_path)
    assert _call(registry, depth=1) is False
    # ...and the depth arm refuses on its own, with an otherwise-firing input.
    assert _call(registry, depth=1, app_mode="interactive") is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("continue_session", True),
        ("resume", True),
        ("resume_id", "abc123"),
        ("fork", "entry-7"),
        ("session", "/tmp/session.jsonl"),
    ],
)
async def test_never_fires_when_continuing_a_session(
    tmp_path: Path, field: str, value: object
) -> None:
    """--continue / --resume / --resume <id> / --fork / --session.

    Resuming prior work is not a first run, even with zero credentials.
    """

    registry = await _registry(tmp_path)
    parsed = Args()
    setattr(parsed, field, value)
    assert _call(registry, parsed=parsed) is False


async def test_never_fires_with_explicit_model_flag(tmp_path: Path) -> None:
    registry = await _registry(tmp_path)
    parsed = Args(model="gpt-5.4")
    parsed.provided.add("model")
    assert _call(registry, parsed=parsed) is False


async def test_never_fires_with_explicit_provider_flag(tmp_path: Path) -> None:
    registry = await _registry(tmp_path)
    parsed = Args(provider="openai")
    parsed.provided.add("provider")
    assert _call(registry, parsed=parsed) is False


async def test_never_fires_with_models_csv(tmp_path: Path) -> None:
    registry = await _registry(tmp_path)
    assert _call(registry, parsed=Args(models=["openai/gpt-5.4"])) is False


async def test_never_fires_with_api_key_flag(tmp_path: Path) -> None:
    """--api-key is a credential the user handed us on the command line."""

    registry = await _registry(tmp_path)
    assert _call(registry, parsed=Args(api_key="sk-from-argv")) is False


async def test_never_fires_when_registry_introspection_raises(
    tmp_path: Path,
) -> None:
    """Fail CLOSED: a broken registry must not nag."""

    registry = await _registry(tmp_path)

    def _boom() -> list:
        raise RuntimeError("registry exploded")

    registry.get_available = _boom  # type: ignore[method-assign]
    assert _call(registry) is False


# === It does not fire — one test per CREDENTIAL SOURCE ======================
# These are the false-positive cases that would nag a configured user. Each
# seeds exactly ONE of the layers ``has_configured_auth`` consults.


async def test_never_fires_with_a_stored_api_key(tmp_path: Path) -> None:
    """Layer 2a — auth.json holding an API key."""

    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    await auth.set_api_key("anthropic", "sk-stored")
    registry = ModelRegistry(auth, None)
    assert registry.get_available(), "seeding must actually configure auth"
    assert _call(registry) is False


async def test_never_fires_with_an_oauth_record_only(tmp_path: Path) -> None:
    """Layer 2b — auth.json holding ONLY an OAuth record (no API key anywhere).

    The Copilot / Claude-subscription user. ``AuthStorage.has`` is membership in
    ``_data``, so an oauth entry counts exactly like a key.
    """

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "github-copilot": {
                    "type": "oauth",
                    "refresh": "r",
                    "access": "a",
                    "expires": 9999999999999,
                }
            }
        ),
        encoding="utf-8",
    )
    auth = AuthStorage(path=auth_path)
    await auth.load()
    registry = ModelRegistry(auth, None)
    assert registry.get_available(), "the oauth record must configure auth"
    assert _call(registry) is False


async def test_never_fires_with_an_env_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Layer 3 — a process env var, read live."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    registry = await _registry(tmp_path)
    assert registry.get_available(), "the env var must configure auth"
    assert _call(registry) is False


async def test_never_fires_with_a_runtime_override(tmp_path: Path) -> None:
    """Layer 1 — ``--api-key`` after it has been pushed onto AuthStorage."""

    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    auth.set_runtime_api_key("openai", "sk-runtime")
    registry = ModelRegistry(auth, None)
    assert registry.get_available()
    assert _call(registry) is False


async def test_never_fires_for_a_models_json_provider_with_an_unset_env_var(
    tmp_path: Path,
) -> None:
    """Layer 4 — the self-hosted false positive.

    A models.json provider whose ``apiKey`` NAMES an env var that is not set
    still counts as configured (pi parity: ``providerRequestConfigs.get(p)
    ?.apiKey !== undefined``). Nagging this user every launch is precisely the
    failure this test exists to prevent.
    """

    registry = await _registry(
        tmp_path,
        models_json={
            "providers": {
                "mycorp": {
                    "name": "MyCorp",
                    "api": "openai-completions",
                    "baseUrl": "https://llm.mycorp.internal/v1",
                    # An env-var NAME, and that variable is not set anywhere.
                    "apiKey": "MYCORP_LLM_KEY_THAT_IS_NOT_SET",
                    "models": [
                        {
                            "id": "mycorp-1",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {
                                "input": 1.0,
                                "output": 2.0,
                                "cacheRead": 0.0,
                                "cacheWrite": 0.0,
                            },
                            "contextWindow": 128000,
                            "maxTokens": 8192,
                        }
                    ],
                }
            }
        },
    )
    assert "MYCORP_LLM_KEY_THAT_IS_NOT_SET" not in __import__("os").environ
    available = registry.get_available()
    assert [m.provider for m in available] == ["mycorp"], available
    assert _call(registry) is False


async def test_never_fires_for_an_extension_registered_provider(
    tmp_path: Path,
) -> None:
    """Layer 5 — issue #77.

    This is why the predicate is evaluated AFTER the harness build: a provider
    only an extension knows about lands on the registry via
    ``bind_model_registry`` inside ``_harness_factory``. Judged any earlier, this
    user is nagged on every single launch.
    """

    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    registry = ModelRegistry(auth, None)
    assert _call(registry) is True, "baseline: nothing configured yet"
    registry.register_provider(
        "telnaut",
        ProviderConfigInput(
            name="Telnaut",
            api_key="sk-ext",
            models={
                "telnaut-1": Model(
                    id="telnaut-1",
                    name="Telnaut 1",
                    api="openai-completions",
                    provider="telnaut",
                    base_url="https://telnaut.example/v1",
                    reasoning=False,
                    input=["text"],
                    cost=ModelCost(
                        input=1.0, output=2.0, cache_read=0.0, cache_write=0.0
                    ),
                    context_window=100000,
                    max_tokens=4096,
                )
            },
        ),
    )
    assert registry.get_available(), "the registered provider must configure auth"
    assert _call(registry) is False


async def test_never_fires_with_a_fallback_resolver(tmp_path: Path) -> None:
    """Layer 6 — an embedder's fallback credential resolver."""

    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    auth.set_fallback_resolver(lambda provider: provider == "anthropic")
    registry = ModelRegistry(auth, None)
    assert registry.get_available()
    assert _call(registry) is False


# === No new environment surface (ADR-0203) ==================================


async def test_no_environment_variable_can_switch_this_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The predicate reads NO env var of its own.

    Every ``os.environ`` read is a new consumer a hostile cwd ``.env`` may try to
    drive, and ADR-0203's argument is that the dangerous set is not enumerable.
    The plausible kill-switch names must therefore do nothing at all.
    """

    registry = await _registry(tmp_path)
    for name in (
        "AELIX_ONBOARDING_SKIP",
        "AELIX_NO_ONBOARDING",
        "AELIX_FIRST_RUN_LOGIN",
        "ONBOARDING_SKIP",
        "NO_ONBOARDING",
        "CI",
    ):
        monkeypatch.setenv(name, "1")
    assert _call(registry) is True


# === The WIRING: _async_main really passes the verdict to run_tui ============
# The predicate above is pure; these two drive the real entry point so the call
# site (and its placement AFTER the harness build) is pinned, not assumed.


class _FakeTTY:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover — never read on a TTY
        return ""

    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        return None


async def _run_tui_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Drive ``_async_main`` interactively with a stubbed ``run_tui``."""

    import sys

    import aelix_coding_agent.tui as tui_pkg
    from aelix_coding_agent.cli.entry import _async_main

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr(sys, "stdout", _FakeTTY())

    captured: dict[str, Any] = {}

    async def _stub_run_tui(runtime: object, **kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    assert await _async_main(["--no-session"]) == 0
    return captured


async def test_async_main_asks_for_onboarding_on_a_bare_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kwargs = await _run_tui_kwargs(tmp_path, monkeypatch)
    assert kwargs["first_run_login"] is True
    # ...and the registry it judged is the one it threaded (same object).
    assert kwargs["model_registry"].get_available() == []


async def test_async_main_does_not_ask_when_a_key_is_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-configured")
    kwargs = await _run_tui_kwargs(tmp_path, monkeypatch)
    assert kwargs["first_run_login"] is False
