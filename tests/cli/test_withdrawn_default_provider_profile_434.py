"""#434 (ADR-0264 §4.4) — review round 2: a provider-only profile, and a rolled-back use.

A profile naming only ``provider:`` keeps the settings default's model string in
``parsed``; when the pair still equals the removed default, it is skipped (the
round-1 version marked it inactive and landed on the removed id). A use that
fails after its overlay restores the provenance with ``parsed``, so ``/new``
after it does not land on the removed id. Driven through ``_async_main`` with
#398's harness (its ``orprof`` profile names only the OpenRouter provider).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

import tests.cli.test_agents_use_pins_the_resolved_pair_398 as harness398
from tests.cli.test_agents_use_pins_the_resolved_pair_398 import _tui, env  # noqa: F401 - fixture
from tests.cli.test_session_model_restore_376 import _ident, _sessions, _settings

_URL = "http://127.0.0.1:9"
_GONE = ("openrouter", "acme/withdrawn-1")


def _hold(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", _URL)
    entry = {
        "url": f"{_URL}/v1/index.json",
        "index": {"schema": "aelix-model-catalog-index/1", "catalogs": []},
        "checkedAt": 1.0,
        "revisions": {},
        "removed": {"openrouter": [[_GONE[1], 1.0]]},
    }
    (root / "agent" / "models-store.json").write_text(
        json.dumps({"version": 1, "salt": "0" * 64, "entries": {"aelix-hosted": entry}}),
        encoding="utf-8",
    )
    _settings(root, defaultProvider=_GONE[0], defaultModel=_GONE[1])


@pytest.mark.parametrize("steps", [["orprof"], ["orprof", "new"], ["plain", "new"]])
async def test_a_provider_only_profile_keeps_the_removed_default_skipped(
    env: Path, monkeypatch: pytest.MonkeyPatch, steps: list[str]  # noqa: F811
) -> None:
    _hold(env, monkeypatch)

    seen = await _tui(monkeypatch, env, [], steps)

    assert _GONE not in seen["after"], seen


async def test_a_rolled_back_use_restores_the_seed_so_new_skips_the_removed_default(
    env: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import aelix_coding_agent.tui as tui_pkg

    _hold(env, monkeypatch)
    (env / "agent" / "agents" / "boom.md").write_text(
        "---\nname: boom\ndescription: d\nprovider: anthropic\nmodel: claude-opus-4-1\n"
        "inherit_extensions: true\n---\nb\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        real = runtime.harness.set_model

        async def failing(_model: Any) -> None:  # the use fails after its overlay
            raise RuntimeError("boom")

        runtime.harness.set_model = failing
        with pytest.raises(Exception):  # noqa: B017 - any refusal is the rollback's trigger
            await kwargs["agent_service"].use("boom", harness=runtime.harness)
        runtime.harness.set_model = real
        await runtime.new_session()
        seen["after"] = _ident(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(sys, "stdin", harness398._FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)

    assert await entry_mod._async_main(["--session-dir", _sessions(env), "--approve"]) == 0
    assert seen["after"] != _GONE
