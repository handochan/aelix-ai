"""#344 (critique S5) — ``/model``'s UNDECIDED fallback resolves WITH the session's registry.

``resolve_model_argument`` answers UNDECIDED not only without a registry but when
the registry's introspection fails (``get_available`` raising, measured by
reading ``core/model_argument.py``'s two ``except`` arms). ``/model`` then falls
back to the launch resolver — which it used to call as ``resolve_model(args,
None)``, dropping the registry it was holding. Under ``OPENROUTER_API_KEY`` rung 0
then could not see the models.json provider and ``/model retryprobe/held-model``
switched the session to OpenRouter.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command


class _IntrospectionFails(ModelRegistry):
    """A real registry whose auth-filtered view raises — the UNDECIDED trigger."""

    def get_available(self) -> list[Any]:
        raise RuntimeError("introspection failed")


class _Harness:
    def __init__(self) -> None:
        self.current_model = None
        self.set_calls: list[Any] = []

    async def set_model(self, model: Any) -> None:
        self.set_calls.append(model)
        self.current_model = model


def test_undecided_model_argument_keeps_the_models_json_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "retryprobe": {
                        "api": "openai-completions",
                        "baseUrl": "http://127.0.0.1:9/v1",
                        "apiKey": "retryprobe-fake-literal",
                        "models": [{"id": "held-model"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    storage = AuthStorage(tmp_path / "auth.json")
    asyncio.run(storage.load())
    registry = _IntrospectionFails(storage, str(tmp_path / "models.json"))

    harness = _Harness()
    committed: list[object] = []
    ctx = CommandContext(
        chrome=object(),  # type: ignore[arg-type]
        harness=harness,  # type: ignore[arg-type]
        commit=committed.append,
        cwd=str(tmp_path),
        commands=list(BUILTIN_COMMANDS),
        model_registry=registry,
    )
    command = match_command("/model", ctx.commands)
    assert command is not None and command.handler is not None
    asyncio.run(cast(Coroutine[Any, Any, None], command.handler(ctx, "retryprobe/held-model")))

    assert len(harness.set_calls) == 1, committed
    switched = harness.set_calls[0]
    assert (switched.provider, switched.id, switched.base_url) == (
        "retryprobe",
        "held-model",
        "http://127.0.0.1:9/v1",
    )
