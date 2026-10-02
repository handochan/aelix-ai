"""#362 / ADR-0250 guard 1 in-session — a ``.env`` key never picks what ``/model`` switches to.

``/model <argument>`` resolves over ``get_available()``, which counts a credential
a cwd ``.env`` supplied — and the switch is then PERSISTED as the default model,
so every later launch in that repo runs on it. Measured on ``aa026d08``
(``/tmp/362-work/design/probe_model_arg.out``,
``/tmp/362-work/critic/probe_critic.out``) with ``OPENROUTER_API_KEY`` exported
and the vendor key from a ``.env``: each row above "unchanged by the guard" went
to the vendor on the planted key.

The rule (ADR-0250 §2.8): whenever the session holds a route-authenticating
credential of the user's own (``--api-key`` included), a cwd ``.env`` key never
chooses the destination — every provider only a ``.env`` key authenticates
leaves the pool, and where only such a provider could serve the string,
``/model`` refuses (fail-closed). For the rows below that is the answer the
session gives with NO ``.env``; not for every session (an allow-list naming only
the ``.env`` provider's models, a stored key NAMING a variable only the ``.env``
sets — ADR-0250 §2.8). A prefix naming a provider the user defined is exempt:
the prefix chose the destination and a ``.env`` key may authenticate it, as at
launch (round 3, the round-2 verification of ``ecb4e0bc``, B1). A session with
only ``.env`` credentials is left as it was (ADR-0250 §6, the stated residual).

SUBJECT CHANGED in the Codex cross-review round of ``854bf319``. That commit
deferred to the launch resolver instead, and exempted a match on the session's
CURRENT provider; Codex measured both (``/tmp/362-work/codex/probe_model_simple.py``,
``probe_model_mock.py``): on ``openai`` with ``OPENROUTER_API_KEY`` exported, a cwd
``.env`` ``OPENAI_API_KEY`` moved ``/model openai/gpt-4o-mini`` from
``openrouter.ai`` to ``api.openai.com`` on the file's key, and moved
``/model openai/o1-pro`` off a models.json ``OpenAI`` the user defined. The
deferral also let a ``.env`` key turn a refusal into a guard-2 OpenRouter switch
(M-a, M-d). The rows M-a..M-c and "the current provider is no licence" now
expect the no-``.env`` answer, which
:func:`test_the_answer_with_the_dotenv_is_the_answer_without_it` asserts for
every row.

A REAL ``ModelRegistry`` and the REAL ``load_dotenv``; fake keys.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv
from aelix_coding_agent.core.model_argument import resolve_model_argument
from aelix_coding_agent.model_registry import ModelRegistry

_OR_SONNET = Model(
    id="anthropic/claude-sonnet-4.5", provider="openrouter", api="openai-completions"
)
_ANT_HAIKU = Model(id="claude-haiku-4-5", provider="anthropic", api="anthropic-messages")
_OAI_MINI = Model(id="gpt-4o-mini", provider="openai", api="openai-responses")


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


async def _resolve(agent: Path, argument: str, current: Model) -> tuple[Any, ...]:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    res = await resolve_model_argument(argument, registry=registry, current_model=current)
    if res.error is not None:
        return ("error", res.error)
    assert res.model is not None
    return (res.model.provider, res.model.id)


OR_SH = {"OPENROUTER_API_KEY": "or-shell-fake"}
ROWS: list[tuple[Any, ...]] = [
    # id, exported, .env, argument, current, expected (provider, id) or ("error", <substring>)
    (
        "M1",
        OR_SH,
        "OPENAI_API_KEY=planted\n",
        "openai/gpt-4o-mini",
        _OR_SONNET,
        ("openrouter", "openai/gpt-4o-mini"),
    ),
    (
        "planted",
        {"ANTHROPIC_API_KEY": "ant-shell-fake"},
        "OPENROUTER_API_KEY=planted\n",
        "anthropic/claude-haiku-4.5",
        _ANT_HAIKU,
        ("anthropic", "claude-haiku-4.5"),
    ),
    # Codex C1 on 854bf319: the session is ON openai (only the .env authenticates
    # it); the current provider is no licence.
    (
        "C1 current provider .env-only",
        OR_SH,
        "OPENAI_API_KEY=planted\n",
        "openai/gpt-4o-mini",
        _OAI_MINI,
        ("openrouter", "openai/gpt-4o-mini"),
    ),
    # M-a..M-d: without the .env these are refused (no Anthropic/OpenAI key of
    # the user's, and /model's #136 backfill needs a credentialled named
    # provider); 854bf319 deferred to --model, where guard 2 sent them to
    # OpenRouter only when the .env key was present. With the .env the refusal
    # names the provider it set aside — for M-a/M-d, ids the catalog does not
    # hold, the generic refusal used to stay silent about it (verify r3).
    (
        "M-a backfill",
        OR_SH,
        "ANTHROPIC_API_KEY=planted\n",
        "anthropic/claude-new-9",
        _OR_SONNET,
        ("error", "anthropic: the key came from a project .env"),
    ),
    (
        "M-d backfill",
        OR_SH,
        "OPENAI_API_KEY=planted\n",
        "openai/gpt-9-new",
        _OR_SONNET,
        ("error", "openai: the key came from a project .env"),
    ),
    (
        "M-b bare unique",
        OR_SH,
        "OPENAI_API_KEY=planted\n",
        "gpt-4o-mini",
        _OR_SONNET,
        ("error", "openai: the key came from a project .env"),
    ),
    (
        "M-c bare unique",
        OR_SH,
        "ANTHROPIC_API_KEY=planted\n",
        "claude-haiku-4-5",
        _OR_SONNET,
        ("error", "anthropic: the key came from a project .env"),
    ),
    (
        "the current provider is no licence",
        OR_SH,
        "ANTHROPIC_API_KEY=planted\n",
        "claude-haiku-4-5",
        _ANT_HAIKU,
        ("error", "anthropic: the key came from a project .env"),
    ),
    # F7 (Codex's second cross-review of a0edf615): an openrouter/<id> is
    # route-deciding in /model — the built-in (NOT re-pointed) prefix gets no
    # exemption while only a .env authenticates it and the user holds a key
    # elsewhere. Stricter than the launch, where openrouter/<id> is an explicit
    # route the .env key authenticates (ADR-0250 §2.5, §2.8, §7). Codex's mutant
    # (exempt every "openrouter/" reference from guard 1) passed all 49 rows of
    # the three guard files; these three turn it red.
    (
        "F7 openrouter/<vendor id>",
        {"OPENAI_API_KEY": "own-oai-fake"},
        "OPENROUTER_API_KEY=planted\n",
        "openrouter/openai/gpt-4o-mini",
        _OAI_MINI,
        ("error", "openrouter: the key came from a project .env"),
    ),
    (
        "F7 openrouter/auto",
        {"OPENAI_API_KEY": "own-oai-fake"},
        "OPENROUTER_API_KEY=planted\n",
        "openrouter/auto",
        _OAI_MINI,
        ("error", "openrouter: the key came from a project .env"),
    ),
    (
        "F7 openrouter/<uncatalogued>",
        {"OPENAI_API_KEY": "own-oai-fake"},
        "OPENROUTER_API_KEY=planted\n",
        "openrouter/newlab/model-x",
        _OAI_MINI,
        ("error", "openrouter: the key came from a project .env"),
    ),
    # --- unchanged by the guard ------------------------------------------------------
    (
        "dual key",
        {**OR_SH, "OPENAI_API_KEY": "oai-shell-fake"},
        "",
        "openai/gpt-4o-mini",
        _OR_SONNET,
        ("openai", "gpt-4o-mini"),
    ),
    ("OR only", OR_SH, "", "openai/gpt-4o-mini", _OR_SONNET, ("openrouter", "openai/gpt-4o-mini")),
    (
        "#362 row",
        {"ANTHROPIC_API_KEY": "ant-shell-fake"},
        "OPENROUTER_API_KEY=planted\n",
        "anthropic/claude-haiku-4-5",
        _OR_SONNET,
        ("anthropic", "claude-haiku-4-5"),
    ),
    (
        "dotenv only, bare",
        {},
        "OPENAI_API_KEY=planted\n",
        "gpt-4o-mini",
        _OR_SONNET,
        ("openai", "gpt-4o-mini"),
    ),
    (
        "dotenv only, slashed",
        {},
        "OPENROUTER_API_KEY=planted\n",
        "openai/gpt-4o-mini",
        _OR_SONNET,
        ("openrouter", "openai/gpt-4o-mini"),
    ),
]


@pytest.mark.parametrize(
    ("row", "exported", "dotenv", "argument", "current", "expected"),
    ROWS,
    ids=[r[0] for r in ROWS],
)
async def test_a_dotenv_key_does_not_choose_what_model_switches_to(
    agent: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    row: str,
    exported: dict[str, str],
    dotenv: str,
    argument: str,
    current: Model,
    expected: tuple[str, str],
) -> None:
    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    if dotenv:
        _dotenv(monkeypatch, tmp_path, dotenv)
    got = await _resolve(agent, argument, current)
    if expected[0] == "error":
        assert got[0] == "error" and expected[1] in got[1], (row, got)
    else:
        assert got == expected, row


async def test_the_guard_never_switches_behind_the_allow_list(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The launch route is OpenRouter, which ``/scoped-models`` excludes: refuse, do not switch."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    monkeypatch.setenv("XAI_API_KEY", "xai-shell-fake")
    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=planted\n")

    class _AllowList:
        def get_enabled_models(self) -> list[str]:
            return ["openai/gpt-4o-mini", "xai/grok-4"]

    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    res = await resolve_model_argument(
        "openai/gpt-4o-mini",
        registry=registry,
        current_model=_OR_SONNET,
        settings_manager=_AllowList(),
    )
    assert res.model is None and res.error is not None
    # Subject changed (Codex round): 854bf319 asked --model and named its route
    # (``openrouter/openai/gpt-4o-mini``); now the .env-only ``openai`` leaves the
    # pool and the refusal is the no-.env one, with the .env named.
    assert "openai: the key came from a project .env" in res.error
    assert "\n" not in res.error and json.dumps(res.error)  # one printable line


async def test_the_several_providers_refusal_stands_when_no_dotenv_key_is_in_the_pool(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard re-asks the launch resolver about ``/model``'s refusal only for a ``.env`` key.

    ``gpt-4o-mini`` is pooled on ``openai`` (exported) and the user's ``mygw``
    (a literal models.json key) — no ``.env`` anywhere. ``/model``'s own answer
    is its several-providers refusal; the launch resolver would pick ``mygw``
    (rule U). Nothing a ``.env`` supplied is involved, so the guard leaves
    ``/model``'s answer alone (the #362 review's SB41 dropped that check and
    nothing turned red).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "oai-shell-fake")
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "mygw": {
                        "api": "openai-completions",
                        "baseUrl": "http://127.0.0.1:9/gw/v1",
                        "apiKey": "gw-fake-literal",
                        "models": [{"id": "gpt-4o-mini"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    got = await _resolve(agent, "gpt-4o-mini", _OR_SONNET)
    assert got == (
        "error",
        "model 'gpt-4o-mini' is served by several providers — name one: "
        "mygw/gpt-4o-mini, openai/gpt-4o-mini",
    )


@pytest.mark.parametrize(
    ("row", "exported", "dotenv", "argument", "current", "expected"),
    [r for r in ROWS if r[2]],
    ids=[r[0] for r in ROWS if r[2]],
)
async def test_the_answer_with_the_dotenv_is_the_answer_without_it(
    agent: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    row: str,
    exported: dict[str, str],
    dotenv: str,
    argument: str,
    current: Model,
    expected: tuple[str, str],
) -> None:
    """Per row: holding a key of your own, the ``.env`` changes nothing.

    True of these rows, not of every session (ADR-0250 §2.8 names the
    exceptions, which refuse rather than switch, and the user-defined prefix,
    where a ``.env`` key authenticates the provider the prefix chose). Except,
    too, where the session has NO credential of the user's own (the two
    "dotenv only" rows): ADR-0250 §6, the stated residual.
    """

    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    without = await _resolve(agent, argument, current)
    _dotenv(monkeypatch, tmp_path, dotenv)
    with_dotenv = await _resolve(agent, argument, current)
    if not exported:
        assert with_dotenv != without, row  # the residual, stated
        return
    if without[0] == "error":
        # The refusal names the .env provider it set aside; the rest is the same.
        assert with_dotenv[0] == "error", (row, with_dotenv)
        assert with_dotenv[1].startswith(without[1]), (row, without, with_dotenv)
    else:
        assert with_dotenv == without, row


def _models_json(agent: Path, providers: dict[str, Any]) -> None:
    (agent / "models.json").write_text(json.dumps({"providers": providers}), encoding="utf-8")


_USER_OPENAI = {
    "OpenAI": {
        "api": "openai-completions",
        "baseUrl": "https://custom.invalid/v1",
        "apiKey": "custom-fake",
        "models": [{"id": "m1"}],
    }
}


async def _registry(agent: Path) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage, str(agent / "models.json"))


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_codex_c1_the_request_goes_to_openrouter_on_the_users_key(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """Codex C1 (``probe_model_simple.py``): host and bearer, with and without the ``.env``."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=openai-env-fake\n")
    registry = await _registry(agent)
    res = await resolve_model_argument(
        "openai/gpt-4o-mini", registry=registry, current_model=_OAI_MINI
    )
    assert res.error is None and res.model is not None
    assert (res.model.provider, res.model.id) == ("openrouter", "openai/gpt-4o-mini")
    assert "openrouter.ai" in res.model.base_url
    auth = await registry.get_api_key_and_headers(res.model)
    assert auth.api_key == "or-shell-fake"


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_codex_c2_a_provider_you_defined_keeps_its_prefix(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """Codex C2 (``probe_model_mock.py``): the user's ``OpenAI`` at ``custom.invalid`` wins.

    854bf319 with the ``.env``: ``openai o1-pro https://api.openai.com/v1`` on
    ``Bearer openai-env-fake``.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    _models_json(agent, _USER_OPENAI)
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=openai-env-fake\n")
    registry = await _registry(agent)
    res = await resolve_model_argument("openai/o1-pro", registry=registry, current_model=_OAI_MINI)
    assert res.error is None and res.model is not None
    assert (res.model.provider, res.model.id, res.model.base_url) == (
        "OpenAI",
        "o1-pro",
        "https://custom.invalid/v1",
    )
    auth = await registry.get_api_key_and_headers(res.model)
    assert auth.api_key == "custom-fake"


async def test_a_provider_you_defined_wins_its_prefix_over_a_credentialled_built_in(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case rule with no ``.env`` at all: ``/model`` lands where ``--model`` does.

    ``OPENAI_API_KEY`` exported, a models.json ``OpenAI``: on 854bf319 ``/model
    openai/gpt-4o-mini`` matched the built-in ``openai`` (the pool's canonical
    key, case-folded) while ``--model openai/gpt-4o-mini`` stays inside the
    provider the user defined (ADR-0250 §2.1 step 1).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "oai-shell-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(agent, _USER_OPENAI)
    got = await _resolve(agent, "openai/gpt-4o-mini", _OR_SONNET)
    assert got == ("OpenAI", "gpt-4o-mini")
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    route = resolve_route("openai/gpt-4o-mini", None, await _registry(agent))
    assert (route.model.provider, route.model.id) == got


async def test_a_provider_you_defined_keeps_its_prefix_on_a_dotenv_key_never_crossed(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a ``.env`` authenticates it: the string stays inside it — never OpenRouter's id.

    SUBJECT CHANGED in round 3 (the round-2 verification of ``ecb4e0bc``, B1).
    This row was "refused naming it": guard 1 took the ``.env``-only ``OpenAI``
    out of the pool and the case rule then found it empty. ``--model
    openai/gpt-4o-mini`` runs it on the ``.env`` key (ADR-0250 step 3), so
    ``/model`` refusing it was a regression. The prefix names a provider the
    user defined — the user's own choice of destination — so the ``.env`` key
    only authenticates it. What the row always guarded still holds: on
    854bf319 the string went to OpenRouter's verbatim id (the pool's bare-id
    reading), and it does not.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(agent, {"OpenAI": {**_USER_OPENAI["OpenAI"], "apiKey": "MYGW_KEY"}})
    _dotenv(monkeypatch, tmp_path, "MYGW_KEY=planted\n")
    registry = await _registry(agent)
    res = await resolve_model_argument(
        "openai/gpt-4o-mini", registry=registry, current_model=_OR_SONNET
    )
    assert res.error is None and res.model is not None, res.error
    assert (res.model.provider, res.model.id, res.model.base_url) == (
        "OpenAI",
        "gpt-4o-mini",
        "https://custom.invalid/v1",
    )
    assert res.caution is not None  # #136: an id the provider does not list
    assert (await registry.get_api_key_and_headers(res.model)).api_key == "planted"


async def test_a_provider_you_defined_that_the_allow_list_excludes_is_refused_not_crossed(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/scoped-models`` leaves only OpenRouter: ``openai/m1`` is refused naming ``OpenAI``.

    The case rule's "not offered" refusal (the half of the old row that still
    refuses): OpenRouter lists ``openai/gpt-4o-mini`` and its key is exported,
    and the string still does not leave the provider the user defined.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(agent, _USER_OPENAI)

    class _AllowList:
        def get_enabled_models(self) -> list[str]:
            return ["openrouter/openai/gpt-4o-mini"]

    res = await resolve_model_argument(
        "openai/gpt-4o-mini",
        registry=await _registry(agent),
        current_model=_OR_SONNET,
        settings_manager=_AllowList(),
    )
    assert res.model is None and res.error is not None
    assert "names 'OpenAI', a provider you defined, which this session does not offer" in (
        res.error
    )


_MYGW_ENV_KEY = {
    "MyGw": {
        "api": "openai-completions",
        "baseUrl": "https://mygw.invalid/v1",
        "apiKey": "MYGW_API_KEY",
        "models": [{"id": "m1"}, {"id": "gpt-4o-mini"}],
    }
}


@pytest.mark.parametrize(
    ("argument", "expected_id", "caution"),
    [("mygw/m1", "m1", False), ("MYGW/new-id", "new-id", True)],
    ids=["listed id", "unlisted id (#136)"],
)
async def test_model_names_a_provider_you_defined_and_its_dotenv_key_authenticates_it(
    agent: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argument: str,
    expected_id: str,
    caution: bool,
) -> None:
    """Round-2 verification B1 (``probe_model_more.py ud_env_key``): ``/model`` lands where ``--model`` does.

    A models.json ``MyGw`` whose ``apiKey`` names ``MYGW_API_KEY``, that
    variable in the cwd ``.env``, ``OPENROUTER_API_KEY`` exported. On
    ``ecb4e0bc``: "'mygw/m1' REFUSED … a provider you defined, which this
    session does not offer"; the launch ran ``MyGw m1 https://mygw.invalid/v1``
    on ``mygw-env-fake``. The ``.env`` key authenticates the route the prefix
    chose (ADR-0250 §2.2) — the user's host, the ``.env`` key as the bearer.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(agent, _MYGW_ENV_KEY)
    _dotenv(monkeypatch, tmp_path, "MYGW_API_KEY=mygw-env-fake\n")
    registry = await _registry(agent)
    res = await resolve_model_argument(argument, registry=registry, current_model=_OR_SONNET)
    assert res.error is None and res.model is not None, res.error
    got = (res.model.provider, res.model.id, res.model.base_url)
    assert got == ("MyGw", expected_id, "https://mygw.invalid/v1")
    assert (res.caution is not None) is caution
    assert (await registry.get_api_key_and_headers(res.model)).api_key == "mygw-env-fake"
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    route = resolve_route(argument, None, registry)
    assert route.error is None
    assert (route.model.provider, route.model.id, route.model.base_url) == got


async def test_a_re_pointed_built_in_is_a_provider_you_defined_too(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``providers.openai.baseUrl`` re-points ``openai``; ``OPENAI_API_KEY`` is only in the ``.env``.

    ``openai/gpt-4o-mini`` names the provider the user re-pointed, so it goes to
    the user's gateway on the ``.env`` key — the launch's answer (step 3; the key
    order is #363's). On ``ecb4e0bc`` it was refused ("names 'openai', a provider
    you defined, which this session does not offer"). This is the one place the
    ``.env`` turns a refusal into a switch: without it the provider has no key at
    all, and the prefix, not the key, chose the destination.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(agent, {"openai": {"baseUrl": "https://gw.invalid/v1"}})
    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=openai-env-fake\n")
    registry = await _registry(agent)
    res = await resolve_model_argument(
        "openai/gpt-4o-mini", registry=registry, current_model=_OR_SONNET
    )
    assert res.error is None and res.model is not None, res.error
    got = (res.model.provider, res.model.id, res.model.base_url)
    assert got == ("openai", "gpt-4o-mini", "https://gw.invalid/v1")
    assert (await registry.get_api_key_and_headers(res.model)).api_key == "openai-env-fake"
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    route = resolve_route("openai/gpt-4o-mini", None, registry)
    assert (route.model.provider, route.model.id, route.model.base_url) == got


@pytest.mark.parametrize("argument", ["m1", "gpt-4o-mini"])
async def test_a_bare_id_still_does_not_reach_a_provider_only_a_dotenv_key_authenticates(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argument: str
) -> None:
    """The B1 fix is for a prefix NAMING the provider; a bare id decides nothing on a ``.env`` key.

    Same session as above. Without a prefix the user has not chosen ``MyGw``,
    so the ``.env``-only provider stays out of the pool and the refusal names
    the ``.env`` (sabotage "spare user-defined providers from the drop" turns
    this red).
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(agent, _MYGW_ENV_KEY)
    _dotenv(monkeypatch, tmp_path, "MYGW_API_KEY=mygw-env-fake\n")
    got = await _resolve(agent, argument, _OR_SONNET)
    assert got[0] == "error" and "MyGw: the key came from a project .env" in got[1], got


async def _resolve_typed(
    agent: Path, argument: str, current: Model, typed: tuple[str, str]
) -> tuple[Any, ...]:
    """``_resolve`` in a session launched with ``--api-key`` (``set_runtime_api_key``)."""

    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    storage.set_runtime_api_key(*typed)
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    res = await resolve_model_argument(argument, registry=registry, current_model=current)
    if res.error is not None:
        return ("error", res.error)
    assert res.model is not None
    return (res.model.provider, res.model.id)


@pytest.mark.parametrize("argument", ["gpt-4o-mini", "openai/gpt-4o-mini"])
async def test_an_api_key_is_the_users_own_credential_so_a_dotenv_key_still_decides_nothing(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argument: str
) -> None:
    """Round-2 verification B2 (``probe_apikey_only.py``): ``--api-key`` counts as the user's own.

    The session's only credential of the user's own is ``--api-key`` on
    ``anthropic``; ``OPENAI_API_KEY`` comes from the cwd ``.env``. The guard
    applies, so ``/model`` refuses rather than switch to ``openai`` on the
    file's key. Sabotage X14 (the "held" check reads only configured
    credentials, not runtime overrides) treated this session as ``.env``-only
    and answered ``('openai', 'gpt-4o-mini')`` with every other row green.
    """

    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=openai-env-fake\n")
    got = await _resolve_typed(agent, argument, _ANT_HAIKU, ("anthropic", "typed-fake"))
    assert got[0] == "error" and "openai: the key came from a project .env" in got[1], got


@pytest.mark.parametrize("argument", ["claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"])
async def test_the_provider_you_typed_an_api_key_for_stays_in_the_pool(
    agent: Path, monkeypatch: pytest.MonkeyPatch, argument: str
) -> None:
    """Round-2 verification B2: ``--api-key`` on ``anthropic`` plus an exported OpenRouter key.

    The guard applies (the OpenRouter key is the user's own), and ``anthropic``
    must stay: the key the user typed is theirs too. Sabotage X12 (the drop
    predicate reads only configured credentials) took ``anthropic`` out and
    refused both strings with every other row green.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    got = await _resolve_typed(agent, argument, _ANT_HAIKU, ("anthropic", "typed-fake"))
    assert got == ("anthropic", "claude-sonnet-4-5")


async def test_two_providers_you_defined_differing_in_case_are_refused(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launch's clash rule: a prefix spelling neither ``OpenAI`` nor ``OPENAI``."""

    _models_json(
        agent,
        {
            "OpenAI": _USER_OPENAI["OpenAI"],
            "OPENAI": {**_USER_OPENAI["OpenAI"], "baseUrl": "https://other.invalid/v1"},
        },
    )
    got = await _resolve(agent, "Openai/m1", _OR_SONNET)
    assert got[0] == "error" and "differ only in case" in got[1], got
    assert "'OPENAI' and 'OpenAI'" in got[1]


async def test_inside_a_provider_you_defined_an_unplaceable_id_is_refused(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its models disagree on the api, so no backfill: refused, never sent elsewhere.

    OpenRouter lists ``openai/gpt-4o-mini`` verbatim and its key is exported —
    the string still does not leave the provider the user defined.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    _models_json(
        agent,
        {
            "OpenAI": {
                **_USER_OPENAI["OpenAI"],
                "models": [{"id": "m1"}, {"id": "m2", "api": "anthropic-messages"}],
            }
        },
    )
    got = await _resolve(agent, "openai/gpt-4o-mini", _OR_SONNET)
    assert got[0] == "error" and "'OpenAI' (a provider you defined)" in got[1], got


async def test_a_dotenv_key_does_not_make_a_bare_id_ambiguous(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``gpt-4o-mini``: the user's ``openai`` alone, not "served by several providers".

    ``mygw``'s key comes from a cwd ``.env`` (a models.json ``apiKey`` naming
    ``MYGW_KEY``). This is the several-providers path the first verification
    asked a row for (V2): on 854bf319 the refusal was handed to ``--model``; now
    the ``.env``-only ``mygw`` never reaches the pool, so the match is unique.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "oai-shell-fake")
    _models_json(
        agent,
        {
            "mygw": {
                "api": "openai-completions",
                "baseUrl": "http://127.0.0.1:9/gw/v1",
                "apiKey": "MYGW_KEY",
                "models": [{"id": "gpt-4o-mini"}],
            }
        },
    )
    _dotenv(monkeypatch, tmp_path, "MYGW_KEY=planted\n")
    assert await _resolve(agent, "gpt-4o-mini", _OR_SONNET) == ("openai", "gpt-4o-mini")


# --- Codex's second cross-review of a0edf615 (F3, F5, F6) -------------------------------


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_an_own_credential_on_a_provider_with_no_models_counts(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """F3 (``probe_edges.py held-tui``): "the user holds a key of their own" does not read model rows.

    ``auth.json`` holds a literal key for ``private-seat``, a provider registered
    with no models. ``a0edf615`` read the held set off ``get_available()``, saw no
    row of it, treated the session as ``.env``-only and, with the ``.env``,
    switched ``/model openai/gpt-4o-mini`` to ``api.openai.com`` on
    ``Bearer project-fake``. Now: refused both ways, the ``.env`` one naming it.
    """

    from aelix_coding_agent.model_registry import ProviderConfigInput

    (agent / "auth.json").write_text(
        json.dumps({"private-seat": {"type": "api_key", "key": "own-seat-fake"}}), encoding="utf-8"
    )
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=project-fake\n")
    registry = await _registry(agent)
    registry.register_provider("private-seat", ProviderConfigInput(name="seat, no models"))
    assert not [m for m in registry.get_all() if m.provider == "private-seat"]
    res = await resolve_model_argument(
        "openai/gpt-4o-mini", registry=registry, current_model=_OR_SONNET
    )
    assert res.model is None and res.error is not None
    assert ("openai: the key came from a project .env" in res.error) is with_dotenv
    from aelix_coding_agent.cli.runtime_bootstrap import holds_route_auth

    assert holds_route_auth(registry)


@pytest.mark.parametrize(
    "source",
    [
        "runtime override",
        "models.json literal, no models",
        "auth.json only, provider unknown to the registry",
        "registration literal, no models",
        "registration oauth only, no models",
    ],
)
async def test_every_source_of_an_own_credential_counts_without_model_rows(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    """F3: the candidate names come from the registry's own sources, not only ``auth.json``.

    One row per source of ``ModelRegistry.route_auth_candidates``, each on its
    own (verify round 4, N1: the auth.json and registered-provider sources were
    pinned only through each other — the F3 row above puts ``private-seat`` in
    ``auth.json`` AND registers it, so dropping either source alone kept every
    test green). A registration carrying an ``api_key`` (or headers) is ALSO a
    request config (``_load_models`` step 3 re-stores it), so that row holds
    with the registered-provider source gone; the ``oauth``-only registration
    is the one only that source names.
    """

    if source == "models.json literal, no models":
        _models_json(
            agent,
            {
                "seat": {
                    "api": "openai-completions",
                    "baseUrl": "https://seat.invalid/v1",
                    "apiKey": "own-literal-fake",
                    "models": [],
                }
            },
        )
    if source == "auth.json only, provider unknown to the registry":
        (agent / "auth.json").write_text(
            json.dumps({"seat": {"type": "api_key", "key": "own-seat-fake"}}), encoding="utf-8"
        )
    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=project-fake\n")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    if source == "runtime override":
        storage.set_runtime_api_key("seat", "typed-fake")
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    if source == "registration literal, no models":
        from aelix_coding_agent.model_registry import ProviderConfigInput

        registry.register_provider(
            "seat", ProviderConfigInput(name="seat, no models", api_key="own-literal-fake")
        )
    if source == "registration oauth only, no models":
        from types import SimpleNamespace

        from aelix_coding_agent.model_registry import ProviderConfigInput

        oauth: Any = SimpleNamespace(name="seat login")
        registry.register_provider("seat", ProviderConfigInput(name="seat, no models", oauth=oauth))
        assert "seat" not in registry._provider_request_configs
    if source.startswith("auth.json"):
        assert "seat" not in registry.get_registered_providers()
    assert not [m for m in registry.get_all() if m.provider == "seat"]
    res = await resolve_model_argument("gpt-4o-mini", registry=registry, current_model=_OR_SONNET)
    assert res.model is None and res.error is not None
    assert "openai: the key came from a project .env" in res.error
    from aelix_coding_agent.cli.runtime_bootstrap import holds_route_auth

    assert holds_route_auth(registry)


async def test_a_listed_id_the_allow_list_excludes_names_the_allow_list(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5 (``probe_edges.py custom-scoped-refusal``): the refusal names the real cause.

    ``MyGw`` lists ``m1`` and ``m2`` on one host; the allow-list keeps only
    ``MyGw/m1``. ``a0edf615`` said ``m2`` is not one MyGw offers "and its models
    do not agree on one api and base URL" — both false.
    """

    _models_json(
        agent,
        {
            "MyGw": {
                "api": "openai-completions",
                "baseUrl": "https://custom.invalid/v1",
                "apiKey": "gateway-fake",
                "models": [{"id": "m1"}, {"id": "m2"}],
            }
        },
    )

    class _AllowList:
        def get_enabled_models(self) -> list[str]:
            return ["MyGw/m1"]

    res = await resolve_model_argument(
        "MyGw/m2", registry=await _registry(agent), settings_manager=_AllowList()
    )
    assert res.model is None and res.error is not None
    assert "is one 'MyGw' (a provider you defined) lists, but it is not offered here" in res.error
    assert "/scoped-models excludes it" in res.error
    assert "do not agree" not in res.error


async def test_a_re_pointed_openrouter_is_a_provider_you_defined_too(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F6 (``probe_edges.py repoint-or``): ``/model`` and ``--model`` agree on a re-pointed ``openrouter``.

    models.json re-points ``openrouter`` (``baseUrl`` only), ``OPENAI_API_KEY`` is
    exported, the OpenRouter key is only in the ``.env``. ``a0edf615`` refused
    ``/model openrouter/auto`` (the launch's set drops ``openrouter``, so the
    case rule never owned it) while ``--model openrouter/auto`` runs on the
    re-pointed host with the ``.env`` key. A re-pointed built-in is a provider
    the user defined (ADR-0250 §2.8); the F7 rows above keep the built-in
    ``openrouter`` route-deciding.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "own-oai-fake")
    _models_json(agent, {"openrouter": {"baseUrl": "https://own-router.invalid/v1"}})
    _dotenv(monkeypatch, tmp_path, "OPENROUTER_API_KEY=planted\n")
    registry = await _registry(agent)
    res = await resolve_model_argument(
        "openrouter/auto", registry=registry, current_model=_OAI_MINI
    )
    assert res.error is None and res.model is not None, res.error
    assert (res.model.provider, res.model.id, res.model.base_url) == (
        "openrouter",
        "auto",
        "https://own-router.invalid/v1",
    )
    assert (await registry.get_api_key_and_headers(res.model)).api_key == "planted"
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    route = resolve_route("openrouter/auto", None, registry)
    assert route.error is None
    assert (route.model.provider, route.model.id, route.model.base_url) == (
        res.model.provider,
        res.model.id,
        res.model.base_url,
    )


# --- Codex's third cross-review of 5e983992 (C1, C2, C3) -------------------------------


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_a_fallback_resolver_does_not_hand_back_a_dotenv_key(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """C1 (``probe_fallback_dotenv.py``): the same host and bearer with and without the ``.env``.

    An embedder installs the shipped ``get_env_api_key`` as the fallback
    resolver. ``has_route_auth``'s environment layer declined the ``.env``
    ``OPENAI_API_KEY``, and the fallback branch handed the same value back as
    the user's own: on 5e983992, with the ``.env``, ``/model openai/gpt-4o-mini``
    went to ``api.openai.com`` on ``Bearer repo-openai-fake``. A fallback answer
    equal to the value of a name in the record no longer counts.
    """

    from aelix_ai.providers._env_api_keys import get_env_api_key

    monkeypatch.setenv("OPENROUTER_API_KEY", "own-router-fake")
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=repo-openai-fake\n")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    storage.set_fallback_resolver(get_env_api_key)
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert not registry.has_route_auth("openai")
    res = await resolve_model_argument(
        "openai/gpt-4o-mini", registry=registry, current_model=_OR_SONNET
    )
    assert res.error is None and res.model is not None, res.error
    assert (res.model.provider, res.model.id) == ("openrouter", "openai/gpt-4o-mini")
    assert "openrouter.ai" in res.model.base_url
    assert (await registry.get_api_key_and_headers(res.model)).api_key == "own-router-fake"


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_an_installed_fallback_resolver_counts_as_your_own_credential(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, with_dotenv: bool
) -> None:
    """C2 (``probe_fallback.py``): a provider only a resolver answers for is in no candidate set.

    ``private-seat`` has no rows, no registration and no stored entry; the
    embedder's resolver answers for it from an exported variable. On 5e983992
    ``holds_route_auth`` was False, so with a ``.env`` ``OPENAI_API_KEY``
    ``/model openai/gpt-4o-mini`` went to ``api.openai.com`` on the file's key and
    RPC ``cycle_model`` rotated to ``openai``. A resolver cannot be enumerated,
    so an installed one counts (ADR-0250 §2.8): refused both ways, the rotation
    selects nothing both ways.
    """

    from types import SimpleNamespace

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_coding_agent.cli.runtime_bootstrap import holds_route_auth
    from aelix_coding_agent.rpc.rpc_mode import _handle_cycle_model

    monkeypatch.setenv("PRIVATE_SEAT_API_KEY", "own-private-seat-fake")
    if with_dotenv:
        _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=repo-openai-fake\n")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    storage.set_fallback_resolver(
        lambda p: os.environ.get("PRIVATE_SEAT_API_KEY") if p == "private-seat" else None
    )
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert "private-seat" not in registry.route_auth_candidates()
    assert holds_route_auth(registry)
    res = await resolve_model_argument(
        "openai/gpt-4o-mini", registry=registry, current_model=_OR_SONNET
    )
    assert res.model is None and res.error is not None
    assert ("openai: the key came from a project .env" in res.error) is with_dotenv
    harness = AgentHarness(AgentHarnessOptions(model=Model(id="unset", provider="unknown")))
    await _handle_cycle_model(harness, registry, SimpleNamespace(id="c"))
    assert harness.current_model.provider == "unknown"


@pytest.mark.parametrize("resolver", [False, True], ids=["no resolver", "resolver installed"])
async def test_the_cost_of_counting_a_resolver_is_the_strict_guard_for_a_dotenv_only_session(
    agent: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resolver: bool
) -> None:
    """C2's stated cost (ADR-0250 §2.8, §6): only ``.env`` credentials, plus an installed resolver.

    Without a resolver the session holds nothing of its own, and ``/model``
    keeps the residual answer (the ``.env`` key authenticates the route the
    string names). With one, the session counts as holding a credential, so the
    ``.env``-only provider is set aside and ``/model`` refuses.
    """

    _dotenv(monkeypatch, tmp_path, "OPENAI_API_KEY=repo-openai-fake\n")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    if resolver:
        storage.set_fallback_resolver(lambda p: None)
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    res = await resolve_model_argument("openai/gpt-4o-mini", registry=registry)
    if resolver:
        assert res.model is None and res.error is not None
        assert "openai: the key came from a project .env" in res.error
    else:
        assert res.error is None and res.model is not None
        assert (res.model.provider, res.model.id) == ("openai", "gpt-4o-mini")


_TENANT_TEMPLATE = "https://{TENANT_KEY}.owner-gateway.invalid/v1"


@pytest.mark.parametrize(
    ("row", "exported", "dotenv", "switches"),
    [
        (
            "dotenv fills the template",
            {},
            "TENANT_KEY=repo-chosen.invalid/v1#\nOPENROUTER_API_KEY=repo-router-fake\n",
            False,
        ),
        (
            "the same value exported",
            {"TENANT_KEY": "repo-chosen.invalid/v1#"},
            "OPENROUTER_API_KEY=repo-router-fake\n",
            True,
        ),
    ],
    ids=["dotenv", "exported"],
)
async def test_model_does_not_let_a_dotenv_value_address_your_template(
    agent: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    row: str,
    exported: dict[str, str],
    dotenv: str,
    switches: bool,
) -> None:
    """C3 (``probe_template.py``) in-session: ``/model openrouter/auto`` on the user's template.

    models.json re-points ``openrouter`` at ``https://{TENANT_KEY}.owner-gateway
    .invalid/v1`` (a provider the user defined, so a ``.env`` key may
    authenticate it). On 5e983992 the ``.env``'s ``TENANT_KEY`` filled the
    placeholder and the request went to host ``repo-chosen.invalid`` with the
    repo's key. Now the ``.env`` value leaves the token unexpanded: the model
    is not runnable and the refusal says to export the variable. Exported, the
    value is the user's choice and the switch happens; the host is expanded at
    client construction.
    """

    from urllib.parse import urlsplit

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.providers._base_url import expand_base_url
    from aelix_coding_agent.cli.model_switch import switch_model_argument
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    monkeypatch.setenv("OPENAI_API_KEY", "own-openai-fake")
    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    _models_json(agent, {"openrouter": {"baseUrl": _TENANT_TEMPLATE}})
    _dotenv(monkeypatch, tmp_path, dotenv)
    registry = await _registry(agent)
    harness = AgentHarness(AgentHarnessOptions(model=registry.find("openai", "gpt-4o-mini")))
    result = await switch_model_argument(
        "openrouter/auto",
        harness=harness,
        model_registry=registry,
        settings_manager=None,
        warn=lambda _line: None,
        persist=False,
    )
    if not switches:
        assert result.model is None and result.refusal is not None, row
        assert "TENANT_KEY came from a project .env" in result.refusal
        assert "export it in your shell" in result.refusal
        assert harness.current_model.provider == "openai"
        return
    assert result.refusal is None and result.model is not None, (row, result.refusal)
    assert harness.current_model.base_url == _TENANT_TEMPLATE
    expanded = expand_base_url(harness.current_model.base_url) or ""
    assert urlsplit(expanded).hostname == "repo-chosen.invalid"
