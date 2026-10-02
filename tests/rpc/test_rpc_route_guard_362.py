"""#362 / ADR-0250 guard 1 over RPC — ``cycle_model`` is an implicit chooser, ``set_model`` is not.

Codex's second cross-review of ``a0edf615`` (F2, ``probe_edges.py``
``rpc-cycle``): with ``OPENROUTER_API_KEY`` exported and ``OPENAI_API_KEY`` only
in a cwd ``.env``, ``cycle_model`` moved a session on
``openrouter/z-ai/glm-5.3-flash`` to ``openai/gpt-4`` — the request then went to
``api.openai.com`` on the file's key. The client names no provider there, so
the rotation now leaves out every provider only a ``.env`` authenticates while
the user holds a credential of their own (the helper ``/model <arg>`` uses,
``core.model_argument._route_aware_pool``). ``set_model`` NAMES the provider,
like ``--provider`` at launch and the no-argument picker, so a ``.env`` key may
authenticate the route the client named: that is pinned here so a later change
is deliberate (ADR-0250 §2.8). The shipped ``aelix --mode rpc`` passes
``run_rpc_mode`` no registry (both commands answer "requires a ModelRegistry");
these rows hold for a program that passes one.

A REAL ``ModelRegistry`` and the REAL ``load_dotenv``; fake keys.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput
from aelix_coding_agent.rpc.rpc_mode import _handle_cycle_model, _handle_set_model


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    path = tmp_path / "agent"
    path.mkdir()
    (path / "models.json").write_text("{}", encoding="utf-8")
    (path / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(path))
    return path


def _dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    path = tmp_path / "project" / ".env"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    for name in [line.partition("=")[0] for line in text.splitlines()] + ["AELIX_DOTENV_ADMITTED"]:
        if name and name not in os.environ:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
    load_dotenv(str(path))


async def _registry(agent: Path) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage, str(agent / "models.json"))


async def _cycle(registry: ModelRegistry, current: Model) -> tuple[Any, Model]:
    harness = AgentHarness(AgentHarnessOptions(model=current))
    reply = await _handle_cycle_model(harness, registry, SimpleNamespace(id="c"))
    current_model = harness.current_model
    assert current_model is not None
    return reply, current_model


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_cycle_model_does_not_rotate_onto_a_provider_only_a_dotenv_key_authenticates(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """F2: the next model, its host and its bearer are the same with and without the ``.env``.

    ``a0edf615`` with the ``.env``: ``("openai", "gpt-4")`` on ``Bearer project-fake``.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "own-or-fake")
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=project-fake\n")
    registry = await _registry(agent)
    current = [m for m in registry.get_available() if m.provider == "openrouter"][-1]
    reply, chosen = await _cycle(registry, current)
    assert reply.success and reply.data is not None
    # The rotation wraps from the last OpenRouter model to the first model left
    # in it — OpenRouter's, not the .env's vendor.
    first_or = next(m for m in registry.get_available() if m.provider == "openrouter")
    assert (chosen.provider, chosen.id) == (first_or.provider, first_or.id)
    assert "openrouter.ai" in chosen.base_url
    assert (await registry.get_api_key_and_headers(chosen)).api_key == "own-or-fake"


async def test_a_rotation_left_with_one_model_returns_no_data(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard 1 leaves one model of the user's own: ``data`` is ``None``, as for any one-model list."""

    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "MyGw": {
                        "api": "openai-completions",
                        "baseUrl": "https://mygw.invalid/v1",
                        "apiKey": "own-literal-fake",
                        "models": [{"id": "m1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=project-fake\n")
    registry = await _registry(agent)
    current = registry.find("MyGw", "m1")
    assert current is not None
    assert len({m.provider for m in registry.get_available()}) == 2  # MyGw + the .env's openai
    reply, chosen = await _cycle(registry, current)
    assert reply.success and reply.data is None
    assert chosen is current


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_an_own_credential_on_a_provider_with_no_models_still_keeps_the_dotenv_out(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """F3 over RPC: ``auth.json`` holds a key for a provider with no model rows.

    Reading "the user holds a credential of their own" off model rows saw
    nothing, so the ``.env``'s OpenAI models were the whole rotation.
    """

    (agent / "auth.json").write_text(
        json.dumps({"private-seat": {"type": "api_key", "key": "own-seat-fake"}}), encoding="utf-8"
    )
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=project-fake\n")
    registry = await _registry(agent)
    registry.register_provider("private-seat", ProviderConfigInput(name="seat, no models"))
    reply, chosen = await _cycle(registry, Model(id="unset", provider="unknown", api="unknown"))
    assert reply.success and reply.data is None
    assert chosen.provider == "unknown"


async def test_set_model_names_the_provider_so_a_dotenv_key_may_authenticate_it(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PINNED, deliberately: ``set_model`` names the provider — the client's choice, not the key's.

    Like ``--provider openai`` at launch and a pick in the no-argument picker
    (ADR-0250 §2.8): with ``OPENROUTER_API_KEY`` exported and ``OPENAI_API_KEY``
    only in the ``.env``, ``{"type":"set_model","provider":"openai",
    "modelId":"gpt-4o-mini"}`` switches to ``openai`` and the ``.env`` key is its
    bearer. Changing this is an owner decision, not a fix.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "own-or-fake")
    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=project-fake\n")
    registry = await _registry(agent)
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="unset", provider="unknown", api="unknown"))
    )
    reply = await _handle_set_model(
        harness,
        registry,
        SimpleNamespace(id="s", provider="openai", model_id="gpt-4o-mini"),
    )
    assert reply.success
    selected = harness.current_model
    assert selected is not None
    assert (selected.provider, selected.id) == ("openai", "gpt-4o-mini")
    assert (await registry.get_api_key_and_headers(selected)).api_key == "project-fake"
