"""#434 (ADR-0264 §4.4) — a model a hold withdrew from the published catalog.

A rollback or withdrawal index carries ``hold``; a check that stores it adds to
the hosted entry's ``removed`` the ids its revisions no longer name. A **removed
id** is one ``removed`` holds and the route rows lack. Then (the owner's choice
of 2026-10-19, pi's resume as ``core/sdk.ts:223-251``@99fe148 picks a model):

* a resume on a removed id is not restored and falls back, saying
  ``Could not restore model <p>/<id> (withdrawn from the published catalog)``;
* a settings default, then ``OPENROUTER_DEFAULT_MODEL``, naming one counts as
  unset: the run takes ``DEFAULT_MODEL_PER_PROVIDER``'s runnable pick, never the
  removed id, and says ``Default model <p>/<id> was withdrawn …`` when there was
  no session model; a shipped default is kept;
* a typed string still resolves as typed, and an id a release dropped, or a
  custom id nothing removed, still restores (#376);
* ``/reload`` keeps the open session's model; ``switch_session`` (``/resume``,
  ``/fork``, ``/import`` build the same way) falls back.

These drive the real ``_async_main`` through #376's harness (mode entries
stubbed, no turn runs). The hold is stored as a check would store it, in the
isolated agent dir's ``models-store.json``, for a loopback catalog URL no test
contacts (the cache phase reads it; nothing here refreshes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_session_model_restore_376 import (  # noqa: F401 - fixtures
    _argv,
    _ident,
    _session,
    _sessions,
    _settings,
    _stub_modes,
    env,
    mode,
)

_URL = "http://127.0.0.1:9"
_GONE = ("openrouter", "acme/withdrawn-1")
_OTHER = ("openrouter", "acme/never-removed-1")
_OR_DEFAULT = ("openrouter", "openai/gpt-5.4")  # DEFAULT_MODEL_PER_PROVIDER["openrouter"]
_OR_SONNET = ("openrouter", "anthropic/claude-sonnet-4.5")


def _store_hold(env: Path, *removed: tuple[str, str]) -> None:  # noqa: F811
    """``models-store.json`` as a check of a withdrawal index leaves it."""

    entry = {
        "url": f"{_URL}/v1/index.json",
        "index": {"schema": "aelix-model-catalog-index/1", "catalogs": []},
        "checkedAt": 1.0,
        "revisions": {},
        "removed": {},
    }
    for provider, model_id in removed:
        entry["removed"].setdefault(provider, []).append([model_id, 1.0])
    (env / "agent" / "models-store.json").write_text(
        json.dumps({"version": 1, "salt": "0" * 64, "entries": {"aelix-hosted": entry}}),
        encoding="utf-8",
    )


@pytest.fixture
def hold(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", _URL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    _store_hold(env, _GONE)
    return env


async def test_a_resume_on_a_removed_id_falls_back_and_says_why(
    hold: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str  # noqa: F811
) -> None:
    """r8's rollback reach: #376 restored it as a custom id on the user's own key."""

    _settings(hold, defaultProvider="openrouter", defaultModel=_OR_SONNET[1])
    await _session(hold, _GONE)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(hold, mode))

    err = capsys.readouterr().err
    assert code == 0, err
    assert _ident(seen["model"]) == _OR_SONNET
    assert seen["fallback"] == (
        "Could not restore model openrouter/acme/withdrawn-1 "
        "(withdrawn from the published catalog). Using openrouter/anthropic/claude-sonnet-4.5"
    )


async def test_a_removed_settings_default_is_skipped_on_a_resume_of_it(
    hold: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """§8: the resume's fallback is the settings pair; naming the removed id, it is unset."""

    _settings(hold, defaultProvider=_GONE[0], defaultModel=_GONE[1])
    await _session(hold, _GONE)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(hold, "rpc")) == 0
    assert _ident(seen["model"]) == _OR_DEFAULT
    assert seen["fallback"] == (
        "Could not restore model openrouter/acme/withdrawn-1 "
        "(withdrawn from the published catalog). Using openrouter/openai/gpt-5.4"
    )


@pytest.mark.parametrize("source", ["settings", "variable"])
async def test_a_launch_never_lands_on_a_removed_default(
    hold: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,  # noqa: F811
    source: str,
) -> None:
    """r10's ``fallback_candidates``: never ``Using <the removed id>``."""

    if source == "settings":
        _settings(hold, defaultProvider=_GONE[0], defaultModel=_GONE[1])
    else:
        monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", _GONE[1])
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(["--session-dir", _sessions(hold), *_mode_args(mode)])

    err = capsys.readouterr().err
    assert code == 0, err
    assert _ident(seen["model"]) == _OR_DEFAULT
    line = (
        "Default model openrouter/acme/withdrawn-1 was withdrawn from the published "
        "catalog. Using openrouter/openai/gpt-5.4"
    )
    assert seen["fallback"] == line
    assert "Using openrouter/acme/withdrawn-1" not in err


def _mode_args(mode: str) -> list[str]:  # noqa: F811
    return {"rpc": ["--mode", "rpc"], "print": ["-p", "hi"], "json": ["--mode", "json", "hi"]}.get(
        mode, []
    )


async def test_a_shipped_default_is_kept_after_a_withdrawal(
    hold: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings(hold, defaultProvider=_OR_SONNET[0], defaultModel=_OR_SONNET[1])
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(["--session-dir", _sessions(hold), "--mode", "rpc"]) == 0
    assert _ident(seen["model"]) == _OR_SONNET
    assert seen["fallback"] is None


async def test_a_typed_removed_id_still_resolves_as_typed(
    hold: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What is offered and what a typed string reaches differ on purpose (§4.4)."""

    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        ["--session-dir", _sessions(hold), "--mode", "rpc", "--model", "openrouter/acme/withdrawn-1"]
    )

    assert code == 0
    assert _ident(seen["model"]) == _GONE
    assert seen["fallback"] is None


async def test_a_custom_id_nothing_removed_still_restores(
    hold: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#376's custom id (an id a release dropped is one too) comes back as before."""

    await _session(hold, _OTHER)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(hold, "rpc")) == 0
    assert _ident(seen["model"]) == _OTHER
    assert seen["fallback"] is None


async def test_reload_keeps_a_removed_id_and_switch_session_falls_back(
    env: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """A hold stored mid-process: ``/reload`` keeps the open session's model, as pi's does;
    a resume of that session (``switch_session``, ``/resume``, ``/fork``, ``/import``) falls back."""

    from aelix_coding_agent import model_registry as registry_mod
    from aelix_coding_agent import modes

    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", _URL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    _settings(env, defaultProvider=_OR_SONNET[0], defaultModel=_OR_SONNET[1])
    target = await _session(env, _GONE)
    registries: list[Any] = []
    real_create = registry_mod.ModelRegistry.create.__func__  # type: ignore[attr-defined]

    def _create(cls: Any, *args: Any, **kwargs: Any) -> Any:
        registry = real_create(cls, *args, **kwargs)
        registries.append(registry)
        return registry

    monkeypatch.setattr(registry_mod.ModelRegistry, "create", classmethod(_create))
    seen: list[tuple[tuple[str, str], str | None]] = []

    async def _drive(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any, **_host_services: Any
    ) -> None:
        def _note() -> None:
            seen.append(
                (_ident(runtime_host.harness.current_model), runtime_host.model_fallback_message)
            )

        _note()  # restored as a custom id: nothing removed it yet
        # The hold arrives (a check stored it): the registry now reads it.
        model_registry._removed = {"openrouter": frozenset({_GONE[1]})}
        await runtime_host.reload()
        _note()
        await runtime_host.switch_session(target)
        _note()

    monkeypatch.setattr(modes, "run_rpc_mode", _drive)
    code = await entry_mod._async_main(_argv(env, "rpc"))

    assert code == 0
    assert seen == [
        (_GONE, None),
        (_GONE, None),
        (
            _OR_SONNET,
            "Could not restore model openrouter/acme/withdrawn-1 "
            "(withdrawn from the published catalog). Using openrouter/anthropic/claude-sonnet-4.5",
        ),
    ]
