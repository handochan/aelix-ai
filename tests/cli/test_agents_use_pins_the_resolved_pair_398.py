"""#398 — ``/agents use`` writes back the pair it put on the session, so ``/new`` lands there too.

``AgentProfileService.use`` completes ``parsed`` with the model it set, so that a
later ``/new``, ``/fork`` or ``/resume`` rebuild resolves the same one (#98) —
and every rebuild resolves ``parsed`` as ``--model`` beside ``--provider``
(``resolve_route``'s step E). On 7faf4b9a only the PROVIDER half was written:
``parsed.model`` kept the string as typed. Beside a provider, that string can
name another id. ``--model openrouter/auto`` resolved without a provider is
OpenRouter's ``auto`` (the prefix is read off), while ``('openrouter/auto',
'openrouter')`` is the catalogue's literal ``openrouter/auto`` — so after a use
whose route applied (a profile's own ``model:``/``provider:``, or a use that
drops such a profile's route and re-resolves the launch inputs), ``/new``
moved the session from ``auto`` to ``openrouter/auto``. A models.json provider
listing both ``gpt`` and ``mygw/gpt`` moved the same way. (A use where no route
applies keeps the live model and writes nothing since #412.)

Now both halves are the model's, spelled so that step E finds exactly that id
(:func:`aelix_coding_agent.agents.service._pinned_model_spelling`): the id,
or for an id that begins with its own provider's prefix ``<provider>/<id>``,
which the step strips back to it. Writing the bare id there instead loses a
custom ``mygw/custom-x`` to the strip (``custom-x``). Leaving the provider
unwritten instead (the other direction #398 named) breaks the pin #370 round 2
relies on: a profile's own ``model:`` resolves without ``--api-key``'s rule and
the rebuild with it (``tests/cli/test_api_key_unknown_prefix_370.py::
test_agents_use_of_a_profiles_own_model_resolves_as_before_api_key``).

Two corners stay as on 7faf4b9a and are NOT pinned here (ADR-0196's #398
Known corners): a use landing on a catalogue provider beside a models.json
provider named like it up to case (``openai`` / ``OpenAI``) is rebuilt on the
user's one, since step E reads the provider that way; and a provider listing
both ``mygw/x`` and ``mygw/mygw/x`` has the prefixed spelling of ``mygw/x``
found as the other id.

These drive the real ``_async_main`` (the TUI entry stubbed, so no turn runs and
nothing is sent), as ``test_agents_use_keeps_live_model_412.py`` does. Hermetic:
fake keys, an isolated agent dir and home, no network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.agents.service import _pinned_model_spelling
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import register_providers, resolve_route
from aelix_coding_agent.model_registry import ModelRegistry

from tests.cli.test_launch_route_344 import _FakePipedStdin, _FakeTTYStdin
from tests.cli.test_session_model_restore_376 import _ident, _session, _sessions
from tests.env_sandbox import sandbox_home

_AUTO = ("openrouter", "auto")
_LITERAL_AUTO = ("openrouter", "openrouter/auto")

# A user provider whose ids repeat its own prefix: ``mygw/gpt`` next to ``gpt``
# (the catalogue's only such pair is OpenRouter's ``auto`` / ``openrouter/auto``),
# and a slash id ``org/m1`` that does not begin with it — listed beside its own
# prefixed twin ``mygw/org/m1``, so a spelling that qualifies every slash id
# (``mygw/org/m1``) is found as that other id and moves ``/new`` (#398 review
# round 2). Never contacted: no turn runs.
_MODELS_JSON = {
    "providers": {
        "mygw": {
            "baseUrl": "http://127.0.0.1:9/gw/v1",
            "apiKey": "fake-mygw-398",
            "api": "openai-completions",
            "models": [
                {"id": "gpt"},
                {"id": "mygw/gpt"},
                {"id": "org/m1"},
                {"id": "mygw/org/m1"},
            ],
        }
    }
}

_PROFILES = {
    "plain": "",
    "orprof": "provider: openrouter\n",
    "autoprof": "model: openrouter/auto\n",
    "gwprof": "model: mygw/gpt\n",
    "gwslash": "model: mygw/org/m1\n",
    "gwcustom": "model: mygw/mygw/custom-x\n",
    "orvendor": "model: openrouter/anthropic/claude-sonnet-4\n",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``test_session_model_restore_376.py``'s sandbox, with the profiles above,
    the models.json ``mygw`` and a fake OpenRouter key of the user's own."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_", "CLOUDFLARE_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    (agent / "agents").mkdir(parents=True)
    (agent / "models.json").write_text(json.dumps(_MODELS_JSON), encoding="utf-8")
    for name, extra in _PROFILES.items():
        (agent / "agents" / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: d\n{extra}inherit_extensions: true\n---\n{name}\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-398")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    register_providers()
    return tmp_path


async def _tui(
    monkeypatch: pytest.MonkeyPatch, env: Path, argv: list[str], steps: list[str]
) -> dict[str, Any]:
    """Launch interactive (the TUI stubbed); per step: ``/agents use <name>``,
    ``--none``, ``new``, ``resume:<file>`` (``/resume`` of that session file),
    ``fork`` (``/fork`` at the current session's first user message) or
    ``reload``. Records the model after each step and the pair on ``parsed``."""

    import aelix_coding_agent.tui as tui_pkg

    seen: dict[str, Any] = {"after": [], "parsed": []}

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        service = kwargs["agent_service"]
        seen["launch"] = _ident(runtime.harness.current_model)
        for step in steps:
            if step == "new":
                await runtime.new_session()
            elif step.startswith("resume:"):
                await runtime.switch_session(step[len("resume:") :])
            elif step == "fork":
                entries = await runtime.harness.session.get_branch()
                first = next(
                    e
                    for e in entries
                    if getattr(getattr(e, "message", None), "role", None) == "user"
                )
                await runtime.fork(first.id, position="at")
            elif step == "reload":
                await runtime.reload()
            else:
                await service.use(None if step == "--none" else step, harness=runtime.harness)
            seen["after"].append(_ident(runtime.harness.current_model))
            seen["parsed"].append((service.parsed.model, service.parsed.provider))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--approve", *argv])
    assert code == 0
    return seen


# === 1. the issue: OpenRouter's auto stays auto through /new ===================


@pytest.mark.parametrize(
    ("argv", "steps", "after"),
    [
        (
            ["--model", "openrouter/auto", "--agent", "orprof"],
            ["plain", "new"],
            [_AUTO, _AUTO],
        ),
        (
            ["--model", "openrouter/auto"],
            ["orprof", "plain", "new"],
            [_LITERAL_AUTO, _AUTO, _AUTO],
        ),
        ([], ["autoprof", "new"], [_AUTO, _AUTO]),
    ],
    ids=["drop-launch-agent", "drop-an-earlier-use", "profile-model"],
)
async def test_new_after_a_use_whose_route_applied_stays_on_openrouters_auto(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    steps: list[str],
    after: list[tuple[str, str]],
) -> None:
    """THE DEFECT. On 7faf4b9a each ``/new`` here landed on ``openrouter/auto``
    with ``parsed`` at ``('openrouter/auto', 'openrouter')``. Rows: a use that
    drops the launch ``--agent``'s ``provider: openrouter`` (the launch inputs
    re-resolved to ``auto``), the same after an earlier use applied that
    profile, and a profile whose own ``model: openrouter/auto`` resolves to
    ``auto``. ``orprof`` itself names the provider beside the typed string, so
    it is the literal id (ADR-0250 §2, ``--provider openrouter --model
    openrouter/auto``) — and that, too, is what ``parsed`` now says."""

    seen = await _tui(monkeypatch, env, argv, steps)
    assert seen["after"] == after
    assert seen["parsed"][-1] == ("auto", "openrouter")


async def test_the_literal_openrouter_auto_a_profile_named_survives_new(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other id of the pair: ``provider: openrouter`` beside a typed
    ``--model openrouter/auto`` is the catalogue's literal ``openrouter/auto``,
    and ``/new`` keeps it — written back as ``openrouter/openrouter/auto``,
    which step E strips back to the literal id (on 7faf4b9a the typed string
    found it directly)."""

    seen = await _tui(monkeypatch, env, ["--model", "openrouter/auto"], ["orprof", "new"])
    assert seen["launch"] == _AUTO
    assert seen["after"] == [_LITERAL_AUTO, _LITERAL_AUTO]


# === 2. other ids where the prefix and a catalogue id meet =====================


@pytest.mark.parametrize(
    ("profile", "after"),
    [
        ("gwprof", ("mygw", "gpt")),
        ("gwslash", ("mygw", "org/m1")),
        ("gwcustom", ("mygw", "mygw/custom-x")),
        ("orvendor", ("openrouter", "anthropic/claude-sonnet-4")),
    ],
    ids=[
        "twin-id",
        "slash-id-beside-its-prefixed-twin",
        "custom-id-with-its-own-prefix",
        "openrouter-vendor-id",
    ],
)
async def test_a_profile_model_stays_the_same_id_through_new(
    env: Path, monkeypatch: pytest.MonkeyPatch, profile: str, after: tuple[str, str]
) -> None:
    """``twin-id`` is the issue's shape on a models.json provider: ``mygw/gpt``
    resolves to the prefix ``mygw`` and the id ``gpt``, and on 7faf4b9a ``/new``
    found the provider's other id ``mygw/gpt``. The slash id is the same shape
    one level down (review round 2): ``mygw/org/m1`` resolves to ``org/m1``,
    and the provider lists ``mygw/org/m1`` too — on 7faf4b9a ``/new`` found
    that, and so does a spelling that qualifies every slash id. The other two
    held on 7faf4b9a and must keep holding: OpenRouter's vendor id, and a custom
    id that begins with its own provider's prefix — which, written back as the
    bare id, would lose its prefix to step E (``/new`` on ``custom-x``)."""

    seen = await _tui(monkeypatch, env, [], [profile, "new"])
    assert seen["after"] == [after, after]


async def test_a_dropped_route_re_resolves_a_twin_id_to_the_launch_model_and_new_keeps_it(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drop path on the models.json twin: the launch ``--model mygw/gpt``
    under ``--agent orprof`` is a custom id on OpenRouter; dropping that profile
    re-resolves the typed string alone to ``mygw``'s ``gpt``, and ``/new``
    stays there (7faf4b9a: ``mygw/gpt``)."""

    seen = await _tui(
        monkeypatch, env, ["--model", "mygw/gpt", "--agent", "orprof"], ["plain", "new"]
    )
    assert seen["launch"] == ("openrouter", "mygw/gpt")
    assert seen["after"] == [("mygw", "gpt"), ("mygw", "gpt")]


# === 3. every rebuild reads the pair: /resume, /fork, /reload ==================


async def test_resume_fork_and_reload_after_the_use_land_on_its_model(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile whose ``model:`` applied is the user's pick, so a rebuild of a
    session with history resolves ``parsed`` rather than restoring the record
    (#376, ``_user_named_a_model``): ``/resume`` of a session haiku answered,
    ``/fork`` inside it and ``/reload`` all land on ``auto`` (7faf4b9a:
    ``openrouter/auto`` on each)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-ant-398")
    path = await _session(env, ("anthropic", "claude-haiku-4-5"))
    seen = await _tui(monkeypatch, env, [], ["autoprof", f"resume:{path}", "fork", "reload"])
    assert seen["after"] == [_AUTO, _AUTO, _AUTO, _AUTO]


# === 4. the spelling is what step E reads back ================================


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("openrouter", "auto"),
        ("openrouter", "openrouter/auto"),
        ("openrouter", "openrouter/free"),
        ("openrouter", "anthropic/claude-sonnet-4"),
        ("openrouter", "newlab/model-x"),
        ("openrouter", "openrouter/newlab-x"),
        ("groq", "groq/compound"),
        ("anthropic", "claude-haiku-4-5"),
        ("mygw", "gpt"),
        ("mygw", "mygw/gpt"),
        ("mygw", "org/m1"),
        ("mygw", "mygw/org/m1"),
        ("mygw", "mygw/custom-x"),
        ("mygw", "MYGW/custom-y"),
    ],
)
async def test_the_spelling_resolves_back_to_exactly_that_id_under_its_provider(
    env: Path, provider: str, model_id: str
) -> None:
    """``resolve_route(spelling, provider)`` — the rebuild's own call for a
    completed pair — returns the same provider and id for catalogued ids (with
    and without their own prefix), OpenRouter custom ids, models.json ids (a
    slash id and its prefixed twin among them) and custom ids under a user
    provider, one spelled with its prefix in another case (step E strips
    case-insensitively)."""

    from aelix_ai.streaming import Model

    agent = env / "agent"
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    spelling = _pinned_model_spelling(Model(id=model_id, provider=provider))
    landed = resolve_route(spelling, provider, registry).model
    assert (landed.provider, landed.id) == (provider, model_id)
