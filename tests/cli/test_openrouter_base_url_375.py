"""#375 / ADR-0251 §11 — ``OPENROUTER_BASE_URL`` reaches every OpenRouter model, not only the launch.

The guide said the variable "applies to every route that lands on OpenRouter";
only the launch applied it (``runtime_bootstrap._openrouter_base`` after
composition). Every copy the registry handed out kept ``models.json``
``providers.openrouter.baseUrl`` or the catalog's ``https://openrouter.ai/api/v1``,
so ``/model openrouter/<id>`` (and its picker, and an embedder's rpc
``set_model`` / ``cycle_model``) sent the prompt and the user's OpenRouter key to
openrouter.ai although they had redirected OpenRouter to a gateway. (A resumed
session was not affected: ``-c`` rebuilds through ``resolve_route``, measured on
402a8013 in review round 2. aelix has no Ctrl+P model cycling, and ``--models``
prints "not yet implemented".) Measured on 402a8013 with
``.omc/probes/375-live/impl/probe/repro.py``: launch at the variable's host, every
registry copy at the ``models.json`` gateway.

Now one function owns it (``model_registry.with_openrouter_base_url``): the
registry applies it to every model it hands out and the launch calls it on what
it builds. Precedence is the launch's since #344: the variable beats a
``models.json`` provider ``baseUrl``, a per-model ``baseUrl`` and the catalog
host, for ``provider == "openrouter"`` only.

Real :class:`ModelRegistry` over a ``tmp_path`` models.json; fake keys; no socket.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import resolve_route
from aelix_coding_agent.model_registry import ModelRegistry

_ENV = "http://127.0.0.1:9/or-env/v1"
_GW = "http://127.0.0.1:9/or-gw/v1"
_PER_MODEL = "http://127.0.0.1:9/or-per-model/v1"
_OTHER_GW = "http://127.0.0.1:9/other-gw/v1"
_CATALOG = "https://openrouter.ai/api/v1"

_PROVIDERS: dict[str, Any] = {
    # The built-in, re-pointed, plus a models entry with its own per-model baseUrl.
    "openrouter": {
        "baseUrl": _GW,
        "models": [{"id": "mine/pinned-model", "baseUrl": _PER_MODEL}],
    },
    # An OpenRouter-compatible gateway the user named something else, serving
    # the same id: never re-pointed by the variable.
    "openrouter-proxy": {
        "baseUrl": _OTHER_GW,
        "api": "openai-completions",
        "apiKey": "gw-fake",
        "models": [{"id": "openai/gpt-4o-mini"}],
    },
}


@pytest.fixture
def scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV")
        ):
            monkeypatch.delenv(name)
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    return agent


async def _registry(agent: Path, providers: dict[str, Any] | None = None) -> ModelRegistry:
    (agent / "models.json").write_text(
        json.dumps({"providers": _PROVIDERS if providers is None else providers}),
        encoding="utf-8",
    )
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    return registry


def _or_hosts(models: list[Model]) -> set[str]:
    return {m.base_url for m in models if m.provider == "openrouter"}


# ── the registry: every accessor ───────────────────────────────────────────────


async def test_every_registry_copy_of_an_openrouter_model_carries_the_variable(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED on 402a8013: ``find`` / ``get_all`` / ``get_available`` answered ``_GW``."""

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed)
    found = registry.find("openrouter", "openai/gpt-4o-mini")
    assert found is not None and found.base_url == _ENV
    assert _or_hosts(registry.get_all()) == {_ENV}
    assert _or_hosts(registry.get_available()) == {_ENV}
    # The per-model baseUrl loses too - the launch's precedence since #344.
    pinned = registry.find("openrouter", "mine/pinned-model")
    assert pinned is not None and pinned.base_url == _ENV


async def test_without_the_variable_nothing_moves(scrubbed: Path) -> None:
    registry = await _registry(scrubbed)
    found = registry.find("openrouter", "openai/gpt-4o-mini")
    assert found is not None and found.base_url == _GW
    pinned = registry.find("openrouter", "mine/pinned-model")
    assert pinned is not None and pinned.base_url == _PER_MODEL
    plain = await _registry(scrubbed, {})
    assert _or_hosts(plain.get_all()) == {_CATALOG}


async def test_an_empty_variable_is_unset(scrubbed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_BASE_URL", "")
    registry = await _registry(scrubbed)
    assert _or_hosts(registry.get_all()) == {_GW, _PER_MODEL}
    model = resolve_route("openrouter/openai/gpt-4o-mini", None, registry).model
    assert model.base_url == _GW


async def test_no_other_provider_is_re_pointed(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gateway under another name keeps its ``baseUrl`` - even serving OpenRouter ids."""

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed)
    other = registry.find("openrouter-proxy", "openai/gpt-4o-mini")
    assert other is not None and other.base_url == _OTHER_GW
    vendor = registry.find("openai", "gpt-4o-mini")
    assert vendor is not None and vendor.base_url == "https://api.openai.com/v1"
    moved = {m.provider for m in registry.get_all() if m.base_url == _ENV}
    assert moved == {"openrouter"}
    routed = resolve_route("openrouter-proxy/openai/gpt-4o-mini", None, registry).model
    assert (routed.provider, routed.base_url) == ("openrouter-proxy", _OTHER_GW)


async def test_compose_built_in_without_a_registry_copy_carries_it(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composition branch (no copy with that id and api) applies it as well."""

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed)
    stray = Model(id="zz/not-in-any-catalog", provider="openrouter", api="openai-completions")
    assert registry.find(stray.provider, stray.id) is None
    composed = registry.compose_built_in(stray)
    assert (composed.provider, composed.id, composed.api) == (stray.provider, stray.id, stray.api)
    assert composed.base_url == _ENV


async def test_the_variable_is_read_when_a_model_is_handed_out(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry built BEFORE the value arrived (a hatched ``.env`` loads later) agrees.

    And a copy's identity is stable between two reads while nothing changes.
    """

    registry = await _registry(scrubbed)
    assert registry.find("openrouter", "openai/gpt-4o-mini").base_url == _GW  # type: ignore[union-attr]
    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    first = registry.find("openrouter", "openai/gpt-4o-mini")
    assert first is not None and first.base_url == _ENV
    assert registry.find("openrouter", "openai/gpt-4o-mini") is first
    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV + "2")
    assert registry.find("openrouter", "openai/gpt-4o-mini").base_url == _ENV + "2"  # type: ignore[union-attr]
    monkeypatch.delenv("OPENROUTER_BASE_URL")
    assert registry.find("openrouter", "openai/gpt-4o-mini").base_url == _GW  # type: ignore[union-attr]


# ── the launch and /model agree ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model_flag", "provider_flag"),
    [("openrouter/openai/gpt-4o-mini", None), ("openai/gpt-4o-mini", "openrouter")],
    ids=["prefix", "named-provider"],
)
def test_the_launch_without_a_registry_still_applies_it(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, model_flag: str, provider_flag: str | None
) -> None:
    """The launch's own call (``_openrouter_base``): a static catalog hit, no registry.

    Review round 2: the catalog model with and without the variable differs in
    ``base_url`` alone (a launch-side copy that dropped the name, cost or window
    passed every round-1 row).
    """

    plain = resolve_route(model_flag, provider_flag, None).model
    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    model = resolve_route(model_flag, provider_flag, None).model
    assert (model.provider, model.id, model.base_url) == ("openrouter", "openai/gpt-4o-mini", _ENV)
    assert _differing_fields(plain, model) == ["base_url"]


@pytest.mark.parametrize(
    ("model_flag", "provider_flag", "model_id"),
    [
        ("openrouter/openai/gpt-4o-mini", None, "openai/gpt-4o-mini"),
        ("openai/gpt-4o-mini", "openrouter", "openai/gpt-4o-mini"),
        ("openrouter/mine/pinned-model", None, "mine/pinned-model"),
    ],
    ids=["prefix", "named-provider", "models-json-entry"],
)
async def test_the_launch_and_the_model_command_hand_out_the_same_model(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_flag: str,
    provider_flag: str | None,
    model_id: str,
) -> None:
    """RED on 402a8013: the launch at ``_ENV``, ``/model``'s pick at ``_GW`` / ``_PER_MODEL``."""

    from aelix_coding_agent.core.model_argument import resolve_model_argument

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed)
    launch = resolve_route(model_flag, provider_flag, registry).model
    picked = await resolve_model_argument(f"openrouter/{model_id}", registry=registry)
    assert picked.model is not None
    assert launch.base_url == _ENV
    assert picked.model == launch


async def test_model_command_for_an_id_the_catalog_does_not_know_goes_to_the_variable(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#370 verify r1's pty row in-process: ``/model openrouter/newlab/model-x`` then a turn.

    The switch runs ``switch_model_argument`` - the ``/model`` handler's body - so the
    model the harness receives is the one a turn would use.
    """

    from aelix_coding_agent.cli.model_switch import switch_model_argument

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed, {})
    received: list[Model] = []

    async def _set_model(model: Model) -> None:
        received.append(model)

    harness = SimpleNamespace(current_model=None, set_model=_set_model)
    switch = await switch_model_argument(
        "openrouter/newlab/model-x",
        harness=harness,
        model_registry=registry,
        settings_manager=None,
        warn=lambda _line: None,
    )
    assert switch.refusal is None
    assert [(m.provider, m.id, m.base_url) for m in received] == [
        ("openrouter", "newlab/model-x", _ENV)
    ]
    launch = resolve_route("openrouter/newlab/model-x", None, registry).model
    assert launch.base_url == _ENV


# ── the other registry readers ─────────────────────────────────────────────────


async def test_the_scope_and_session_restore_library_functions_carry_it(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_model_scope`` / ``restore_model_from_session``: exported, no product caller.

    pi's names for ``--models`` and a session restore; aelix's CLI does not call
    them (``--models`` is not implemented, ``-c`` rebuilds through
    ``resolve_route``). An embedder that does gets the registry's copy.
    """

    from aelix_coding_agent.core.model_resolver import (
        resolve_model_scope,
        restore_model_from_session,
    )

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed)
    scoped = await resolve_model_scope(["openrouter/openai/gpt-4o-mini"], registry)
    assert [(s.model.provider, s.model.base_url) for s in scoped] == [("openrouter", _ENV)]
    restored = await restore_model_from_session(
        "openrouter", "openai/gpt-4o-mini", None, False, registry
    )
    assert restored.model is not None and restored.model.base_url == _ENV


async def test_rpc_set_model_and_cycle_model_carry_it(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The embedder's rpc path (``run_rpc_mode(model_registry=...)``)."""

    from aelix_coding_agent.rpc.rpc_mode import _handle_cycle_model, _handle_set_model
    from aelix_coding_agent.rpc.rpc_types import RpcCommandCycleModel, RpcCommandSetModel

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry = await _registry(scrubbed)
    chosen: list[Model] = []

    async def _set_level(_level: str) -> None:
        return None

    harness = SimpleNamespace(
        current_model=None,
        set_current_model=chosen.append,
        set_thinking_level=_set_level,
        state=SimpleNamespace(thinking_level="off"),
    )
    reply = await _handle_set_model(
        harness,  # type: ignore[arg-type]
        registry,
        RpcCommandSetModel(provider="openrouter", model_id="openai/gpt-4o-mini", id="1"),
    )
    assert getattr(reply, "success", True) is not False
    assert [(m.provider, m.base_url) for m in chosen] == [("openrouter", _ENV)]
    harness.current_model = chosen[-1]
    seen: set[tuple[str, str]] = set()
    for n in range(len(registry.get_available()) + 1):
        await _handle_cycle_model(harness, registry, RpcCommandCycleModel(id=str(n)))  # type: ignore[arg-type]
        harness.current_model = chosen[-1]
        seen.add((chosen[-1].provider, chosen[-1].base_url))
    assert ("openrouter", _ENV) in seen
    assert not any(p == "openrouter" and b != _ENV for p, b in seen)


# ── admission is unchanged ─────────────────────────────────────────────────────


@pytest.mark.parametrize("hatched", [False, True], ids=["dotenv-refused", "dotenv-hatched"])
async def test_a_project_dotenv_reaches_the_model_copies_only_through_the_hatch(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    hatched: bool,
) -> None:
    """``.env``'s ``OPENROUTER_BASE_URL`` stays refused unless ``AELIX_DOTENV_ALLOW`` names it.

    #375 widens where an admitted value applies, never what is admitted.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

    if hatched:
        monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    registry = await _registry(scrubbed)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"OPENROUTER_BASE_URL={_ENV}\n", encoding="utf-8")
    # Registers a restore of the variable's absence for whatever load_dotenv sets.
    monkeypatch.setenv("OPENROUTER_BASE_URL", "placeholder")
    monkeypatch.delenv("OPENROUTER_BASE_URL")
    load_dotenv(str(dotenv))
    capsys.readouterr()
    want = _ENV if hatched else _GW
    found = registry.find("openrouter", "openai/gpt-4o-mini")
    assert found is not None and found.base_url == want
    assert resolve_route("openrouter/openai/gpt-4o-mini", None, registry).model.base_url == want


# ── review round 2: only the provider named exactly "openrouter" moves ─────────
#
# Round-1 verify sabotage: re-pointing every model whose ``base_url`` is on
# openrouter.ai (V1) or whose provider is "openrouter" up to case (V7) passed all
# 365 rows, because the only other-name row used a host off openrouter.ai. Each
# row below is a provider that is NOT "openrouter" sitting at OpenRouter's own
# host, with its own key: the variable would send that key to the gateway it
# names. Kit ``.omc/probes/375-live/r2/``.

_ROW2_KEY = "other-fake"


async def _other_name_registry(agent: Path, case: str) -> tuple[ModelRegistry, str]:
    """A registry holding a non-``openrouter`` provider at ``_CATALOG``; returns (it, name)."""

    from aelix_coding_agent.model_registry import ProviderConfigInput

    entry = {
        "baseUrl": _CATALOG,
        "api": "openai-completions",
        "apiKey": _ROW2_KEY,
        "models": [{"id": "openai/gpt-4o-mini"}],
    }
    if case == "models-json-myor":
        return await _registry(agent, {"myor": entry}), "myor"
    if case == "models-json-OpenRouter":
        return await _registry(agent, {"OpenRouter": entry}), "OpenRouter"
    registry = await _registry(agent, {})
    registry.register_provider(
        "orext",
        ProviderConfigInput(
            api_key=_ROW2_KEY,
            models={
                "openai/gpt-4o-mini": Model(
                    id="openai/gpt-4o-mini",
                    provider="orext",
                    api="openai-completions",
                    base_url=_CATALOG,
                )
            },
        ),
    )
    return registry, "orext"


@pytest.mark.parametrize("case", ["models-json-myor", "extension-orext", "models-json-OpenRouter"])
async def test_a_provider_at_openrouters_host_under_another_name_keeps_its_host(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """``find`` / ``get_all`` / ``get_available`` / the launch / ``/model`` keep ``_CATALOG``.

    RED with the helper matching the host (``"openrouter.ai" in base_url``, round-1
    sabotage V1) for every case, and with the provider matched up to case (V7)
    for ``OpenRouter``. The built-in ``openrouter`` in the same registry is the
    positive control: it does move.
    """

    from aelix_coding_agent.core.model_argument import resolve_model_argument

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry, name = await _other_name_registry(scrubbed, case)

    found = registry.find(name, "openai/gpt-4o-mini")
    assert found is not None and (found.provider, found.base_url) == (name, _CATALOG)
    for listed in (registry.get_all(), registry.get_available()):
        mine = [m for m in listed if m.provider == name]
        assert mine and {m.base_url for m in mine} == {_CATALOG}
        assert _or_hosts(listed) == {_ENV}  # the built-in openrouter still moves

    launch = resolve_route(f"{name}/openai/gpt-4o-mini", None, registry).model
    assert (launch.provider, launch.id, launch.base_url) == (name, "openai/gpt-4o-mini", _CATALOG)
    picked = await resolve_model_argument(f"{name}/openai/gpt-4o-mini", registry=registry)
    assert picked.model is not None
    assert (picked.model.provider, picked.model.base_url) == (name, _CATALOG)


async def test_openrouter_spelled_in_another_case_lands_on_the_users_provider_and_its_host(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a models.json ``OpenRouter``, ``openrouter/<id>`` is the user's provider (pi's
    case rule, ADR-0250) - so the launch keeps that provider's host and key, not the variable.

    RED with the helper's provider check made case-insensitive (round-1 sabotage V7).
    """

    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    registry, _ = await _other_name_registry(scrubbed, "models-json-OpenRouter")
    for flag, provider_flag in (
        ("openrouter/openai/gpt-4o-mini", None),
        ("openai/gpt-4o-mini", "OpenRouter"),
    ):
        model = resolve_route(flag, provider_flag, registry).model
        assert (model.provider, model.base_url) == ("OpenRouter", _CATALOG), flag


# ── review round 2: the helper moves base_url and nothing else ─────────────────


def _every_field_set() -> Model:
    """An OpenRouter model whose every field differs from :class:`Model`'s default."""

    from dataclasses import fields

    from aelix_ai.streaming import ModelCost

    model = Model(
        id="openai/gpt-4o-mini",
        name="GPT-4o mini via OpenRouter",
        api="openai-completions",
        provider="openrouter",
        base_url=_GW,
        reasoning=True,
        input=["text", "image"],
        cost=ModelCost(input=0.15, output=0.6, cache_read=0.075, cache_write=0.3),
        context_window=128_000,
        max_tokens=16_384,
        thinking_level_map={"low": 1024, "high": None},
        headers={"X-Gateway-Tenant": "tenant-375"},
        compat={"supportsDeveloperRole": False},
    )
    default = Model()
    unset = [f.name for f in fields(Model) if getattr(model, f.name) == getattr(default, f.name)]
    # A field added to Model later must be set here too, or this row stops covering it.
    assert unset == [], unset
    return model


def _differing_fields(a: Model, b: Model) -> list[str]:
    from dataclasses import fields

    return [f.name for f in fields(Model) if getattr(a, f.name) != getattr(b, f.name)]


def test_the_helper_changes_base_url_and_no_other_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED with ``replace(moved, headers=None)`` (Codex round 1, item 4) or any other field dropped."""

    from aelix_coding_agent.model_registry import with_openrouter_base_url

    model = _every_field_set()
    passed = with_openrouter_base_url(model, _ENV)
    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    read = with_openrouter_base_url(model)
    for out in (passed, read):
        assert out.base_url == _ENV
        assert _differing_fields(model, out) == ["base_url"]
    monkeypatch.delenv("OPENROUTER_BASE_URL")
    assert with_openrouter_base_url(model) is model


async def test_registry_and_launch_copies_differ_from_the_unredirected_ones_only_in_base_url(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every OpenRouter copy, read without the variable and then with it, differs in ``base_url`` alone.

    Two sources: a models.json ``openrouter`` entry (name, reasoning, input, cost,
    window, compat - a models.json ``headers`` is request config, not a model
    field) and an extension registration under ``openrouter`` carrying every
    :class:`Model` field, ``headers`` and ``thinking_level_map`` included. The
    launch-vs-``/model`` rows above cannot see a field BOTH paths lose; this
    compares each path with itself. RED with the helper dropping ``headers``.
    """

    from dataclasses import replace

    from aelix_coding_agent.model_registry import ProviderConfigInput

    providers: dict[str, Any] = {
        "openrouter": {
            "baseUrl": _GW,
            "models": [
                {
                    "id": "mine/full-model",
                    "name": "Full model",
                    "api": "openai-completions",
                    "reasoning": True,
                    "input": ["text", "image"],
                    "cost": {"input": 1, "output": 2, "cacheRead": 0.5, "cacheWrite": 1.5},
                    "contextWindow": 64_000,
                    "maxTokens": 8_000,
                    "compat": {"supportsDeveloperRole": False},
                }
            ],
        }
    }
    registry = await _registry(scrubbed, providers)
    ext = replace(_every_field_set(), id="mine/ext-full")
    registry.register_provider("openrouter", ProviderConfigInput(models={ext.id: ext}))
    ids = ("mine/full-model", "mine/ext-full")

    def snapshot() -> tuple[list[Model], list[Model], list[Model]]:
        found = [registry.find("openrouter", i) for i in ids]
        assert all(m is not None for m in found)
        listed = [m for m in registry.get_all() if m.provider == "openrouter"]
        launched = [resolve_route(f"openrouter/{i}", None, registry).model for i in ids]
        return found, listed, launched  # type: ignore[return-value]

    before = snapshot()
    assert before[0][1].headers == ext.headers and before[0][1].thinking_level_map
    assert before[0][0].compat == {"supportsDeveloperRole": False}
    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    after = snapshot()

    for kind, b_list, a_list in zip(("find", "get_all", "launch"), before, after, strict=True):
        assert len(b_list) == len(a_list) >= 2, kind
        for b, a in zip(b_list, a_list, strict=True):
            assert (b.provider, b.id, a.base_url) == (a.provider, a.id, _ENV), (kind, b.id)
            assert _differing_fields(b, a) == ["base_url"], (kind, b.id)


# ── review round 3: the launch's own call over a duck-typed registry ──────────


class _DuckRegistry:
    """A registry that is not a :class:`ModelRegistry`: no ``compose_built_in``, no re-point."""

    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def find(self, provider: str, model_id: str) -> Model | None:
        return next((m for m in self._models if (m.provider, m.id) == (provider, model_id)), None)

    def get_all(self) -> list[Model]:
        return list(self._models)

    def get_available(self) -> list[Model]:
        return list(self._models)


def test_the_launch_over_a_duck_typed_registry_hit_changes_base_url_and_no_other_field(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launch's own call (``_openrouter_base``) is the only re-point a duck-typed hit gets.

    ``resolve_route`` finds an id the catalog does not know (``duck/only``) in the
    registry it was handed and takes that registry's model, ``headers`` included;
    a registry that is not a :class:`ModelRegistry` hands it out un-pointed, so the
    launch's call moves it. Round 2 called "the launch's own call drops
    ``headers``" (M6) equivalent; the round-2 verify measured it is not (kit
    ``.omc/probes/375-live/r2verify/kit/test_r2v_m6_duck.py``). RED under M6.
    """

    from dataclasses import replace

    duck = _DuckRegistry([replace(_every_field_set(), id="duck/only")])
    plain = resolve_route("openrouter/duck/only", None, duck).model
    assert (plain.id, plain.base_url, plain.headers) == (
        "duck/only",
        _GW,
        {"X-Gateway-Tenant": "tenant-375"},
    )
    monkeypatch.setenv("OPENROUTER_BASE_URL", _ENV)
    moved = resolve_route("openrouter/duck/only", None, duck).model
    assert (moved.provider, moved.id, moved.base_url) == ("openrouter", "duck/only", _ENV)
    assert _differing_fields(plain, moved) == ["base_url"]
