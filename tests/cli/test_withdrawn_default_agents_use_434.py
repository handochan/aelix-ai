"""#434 (ADR-0264 §4.4) — ``/agents use`` and a settings default a hold removed.

The skip of a removed default reads WHO put the pair in ``parsed``: the launch
records the settings pair (``record_settings_seed``), active while ``parsed``
holds it, and ``/agents use`` sets it to whether the pair it leaves there is the
baseline's (a use that names no route, ``--none``) or the profile's own. So:

* launched with ``--agent`` on a profile that names a model, the baseline still
  holds the removed settings default: ``--none`` back to it, and the ``/new``
  after, land on the provider default, never the removed id (Codex review 1);
* a profile that names the removed pair itself is a pick, as typed: it resolves
  to it (the collision the bare equality got wrong);
* a rolled-back use leaves the provenance as it was.

These drive the real ``_async_main`` through #412's harness (the TUI stubbed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cli.test_agents_use_keeps_live_model_412 import _tui, env  # noqa: F401 - fixture
from tests.cli.test_session_model_restore_376 import _settings

_GONE = ("openrouter", "acme/withdrawn-1")
_URL = "http://127.0.0.1:9"


@pytest.fixture
def held(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", _URL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    entry = {
        "url": f"{_URL}/v1/index.json",
        "index": {"schema": "aelix-model-catalog-index/1", "catalogs": []},
        "checkedAt": 1.0,
        "revisions": {},
        "removed": {"openrouter": [[_GONE[1], 1.0]]},
    }
    (env / "agent" / "models-store.json").write_text(
        json.dumps({"version": 1, "salt": "0" * 64, "entries": {"aelix-hosted": entry}}),
        encoding="utf-8",
    )
    (env / "agent" / "agents" / "gone.md").write_text(
        "---\nname: gone\ndescription: names the removed pair\nprovider: openrouter\n"
        "model: acme/withdrawn-1\ninherit_extensions: true\n---\ng\n",
        encoding="utf-8",
    )
    _settings(env, defaultProvider=_GONE[0], defaultModel=_GONE[1])
    return env


async def test_none_back_to_a_removed_settings_default_never_lands_on_it(
    held: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = await _tui(
        monkeypatch,
        ["--session-dir", str(held / "sessions"), "--agent", "opusprov"],
        ["--none", "new"],
    )

    assert seen["launch"] == ("anthropic", "claude-opus-4-1")
    assert _GONE not in seen["after"], seen["after"]
    assert seen["after"][0] == seen["after"][1]


async def test_a_profile_naming_the_removed_pair_is_a_pick_as_typed(
    held: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = await _tui(monkeypatch, ["--session-dir", str(held / "sessions")], ["gone", "--none"])

    assert seen["launch"] != _GONE
    assert seen["after"][0] == _GONE  # the profile's own route
    assert seen["after"][1] != _GONE  # back to the settings default: skipped
