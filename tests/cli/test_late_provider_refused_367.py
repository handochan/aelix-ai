"""#367 — a launch model naming a provider registered in ``session_start`` is refused.

pi registers an extension's providers before startup model selection only when
the extension registers them from its factory (``docs/custom-provider.md``);
``session_start`` fires later (``AgentSession.bindExtensions``), so pi's
``main.ts`` refuses such a ``--model`` (``resolveCliModel``'s error, exit 1).
#344 switched to the provider instead; the owner's decision on #367 (2026-10-02)
is to follow pi. These drive the real ``_async_main`` (the mode entry stubbed,
so no turn runs and nothing is sent). ``tests/cli/test_launch_route_344.py`` has the
``--model <late>/<id>`` rows in every mode (print/json refused, interactive/RPC held,
rebuilds held, ``--api-key`` attached to no provider — since round 4 a pending launch
attaches it only after the late decision, so a refused one never had it anywhere) and
``test_launch_route_362.py``
the OpenRouter-less and ``.env`` ones; these are the sweep's other entry points:

* ``--provider <late> --model <id>``, a bare id, settings ``defaultProvider`` /
  ``defaultModel``, an ``--agent`` profile's ``model:`` and an id the provider
  does not list — each refused in print with the same message, with an
  OpenRouter key of the user's own and without;
* ``--api-key`` with such a model gets the same refusal (#362's Codex F4 retired);
* an explicit ``/model`` to the provider in the held session switches (decision 5);
* the print gate judges the harness's own model, so the held placeholder never
  reaches a turn even if the late-route check says nothing.

Hermetic: fake keys, an isolated agent dir, no network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

from tests.cli.test_launch_route_344 import (
    _EXT,
    _SESSION_START_EXTENSION,
    _FakePipedStdin,
    _FakeTTYStdin,
)
from tests.env_sandbox import sandbox_home

_REFUSAL = (
    "The launch model \"{subject}\" names provider 'sessext', which an extension "
    "registered while a session was starting (for example in a session_start handler), "
    "after the launch model was chosen. "
    "Register 'sessext' in the extension's setup() (its factory) to use it at launch."
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    (tmp_path / "sessext.py").write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    return tmp_path


def _openrouter(monkeypatch: pytest.MonkeyPatch, env: Path, where: str) -> None:
    if where == "exported":
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    elif where == "dotenv":
        path = env / "cwd" / ".env"
        path.write_text("OPENROUTER_API_KEY=or-dotenv-planted\n", encoding="utf-8")
        for name in ("OPENROUTER_API_KEY", "AELIX_DOTENV_ADMITTED"):
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
        load_dotenv(str(path))


def _stub_print(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    return turns


def _spy_launch_model(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The model the first build resolved, as ``create_agent_session_runtime`` saw it."""

    seen: list[tuple[str, str]] = []
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        seen.append((harness.current_model.provider, harness.current_model.id))
        return await real_create(harness, factory, **kwargs)

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    return seen


def _stub_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> tuple[list[Any], list[str]]:
    """Stub the interactive / RPC entry; return (models handed to it, mode flags)."""

    handed: list[Any] = []
    if mode == "interactive":
        import aelix_coding_agent.tui as tui_pkg

        async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
            handed.append(runtime.harness.current_model)
            return 0

        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
        return handed, []
    from aelix_coding_agent import modes

    async def _stub_run_rpc(harness: Any, **kwargs: Any) -> None:
        handed.append(harness.current_model)

    monkeypatch.setattr(modes, "run_rpc_mode", _stub_run_rpc)
    return handed, ["--mode", "rpc"]


# === the sweep's sibling entry points ==========================================


@pytest.mark.parametrize(
    ("argv", "settings", "subject"),
    [
        (["--provider", "sessext", "--model", "m1"], {}, "sessext/m1"),
        (["--model", "m1"], {}, "m1"),
        ([], {"defaultProvider": "sessext", "defaultModel": "m1"}, "sessext/m1"),
        (["--model", "m1"], {"defaultProvider": "sessext"}, "m1"),
        (["--agent", "lateprof"], {}, "sessext/m1"),
        (["--model", "sessext/m2"], {}, "sessext/m2"),
    ],
    ids=["provider-flag", "bare-id", "settings-pair", "settings-provider", "agent", "unlisted-id"],
)
@pytest.mark.parametrize("openrouter", ["exported", "none"])
async def test_every_launch_input_naming_it_is_refused(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    settings: dict[str, str],
    subject: str,
    openrouter: str,
) -> None:
    """The sweep's sibling entry points, print mode.

    On 5dee21d1 the bare id, the settings ``defaultProvider`` split, the profile
    and the unlisted id were switched like ``--model sessext/m1``; the
    ``--provider`` flag and the settings pair (which the late check never looked
    at) passed the print gate — it judged the re-resolve, which by then found
    ``sessext`` — and failed at the first turn with ``No provider registered for
    api='unknown'``.
    """

    _openrouter(monkeypatch, env, openrouter)
    agents = env / "agent" / "agents"
    agents.mkdir()
    (agents / "lateprof.md").write_text(
        "---\nname: lateprof\ndescription: probe\nmodel: sessext/m1\n---\nprobe\n",
        encoding="utf-8",
    )
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), *argv, "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    assert "Error: " + _REFUSAL.format(subject=subject) + " No prompt was sent." in err


@pytest.mark.parametrize("openrouter", ["exported", "none"])
async def test_api_key_with_a_session_start_provider_is_refused_the_same_way(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    openrouter: str,
) -> None:
    """Decision 4. #362's Codex F4 is retired: ``late`` is registered in
    ``session_start`` with no key of its own, ``--model late/m1 --api-key K``
    was refused because the shared switch ran before the key moved to ``late``
    ("a provider you defined, which this session does not offer"). It now gets
    the refusal every launch model naming such a provider gets.
    """

    _openrouter(monkeypatch, env, openrouter)
    keyless = _SESSION_START_EXTENSION.replace('api_key="ext-fake-literal",', "")
    (env / "late.py").write_text(keyless.replace('"sessext"', '"late"'), encoding="utf-8")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        [
            "--no-session",
            "-e",
            str(env / "late.py"),
            "--model",
            "late/m1",
            "--api-key",
            "k-fake",
            "-p",
            "hi",
        ]
    )
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    refusal = _REFUSAL.format(subject="late/m1").replace("'sessext'", "'late'")
    assert f"Error: {refusal} No prompt was sent." in err
    assert "does not offer" not in err


# === an explicit /model afterwards ============================================


async def test_an_explicit_model_to_the_provider_then_switches(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 5, confirmed: ``/model sessext/m1`` in the held session is the user's own
    later choice, and the provider is in the registry by then."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_coding_agent.cli.model_switch import switch_model_argument

    _openrouter(monkeypatch, env, "exported")
    landed: list[tuple[str, str, str, str]] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        harness = runtime.harness
        switched = await switch_model_argument(
            "sessext/m1",
            harness=harness,
            model_registry=kwargs["model_registry"],
            settings_manager=None,
            warn=lambda _line: None,
        )
        assert switched.refusal is None, switched.refusal
        model = harness.current_model
        landed.append((model.provider, model.id, model.api, model.base_url))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), "--model", "sessext/m1"]
    )
    assert code == 0
    assert landed == [("sessext", "m1", "openai-completions", _EXT)]


# === the print gate judges the harness's model ================================


async def test_the_print_gate_judges_the_harness_model_not_only_the_re_resolve(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision 2's cause: the #98 gate re-resolves after ``session_start``.

    With the late-route check silenced, the OpenRouter-less launch holds the
    placeholder while the gate's re-resolve finds ``sessext``; judging only the
    re-resolve let the run reach its first turn (``No provider registered for
    api='unknown'``). The harness's own model is judged too, so no turn starts.
    """

    from aelix_coding_agent.cli import runtime_bootstrap
    from aelix_coding_agent.core.runnable_models import is_runnable

    monkeypatch.setattr(runtime_bootstrap, "late_registered_route", lambda *a, **k: None)
    # ``is_runnable`` fails open with no adapter registered (``register_providers``
    # runs in ``main_sync``, which this bypasses); judge against the one
    # ``sessext`` uses, without touching the process-wide api registry.
    monkeypatch.setattr(
        entry_mod, "is_runnable", lambda model: is_runnable(model, {"openai-completions"})
    )
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), "--model", "sessext/m1", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    assert "Error: model 'm1' (provider 'sessext') could not be resolved" in err


# === verify round 1 (B1): every re-resolver of the launch inputs keeps the hold ====

_PLAIN = "---\nname: plainprof\ndescription: probe without a model\n---\nplain probe\n"
_LATE = "---\nname: lateprof\ndescription: probe\nmodel: sessext/m1\n---\nprobe\n"


def _profiles(env: Path) -> None:
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "plainprof.md").write_text(_PLAIN, encoding="utf-8")
    (agents / "lateprof.md").write_text(_LATE, encoding="utf-8")


async def _agents_use(
    env: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], profile: str | None
) -> dict[str, Any]:
    """Launch held (interactive, the TUI stubbed), run ``/agents use`` as the TUI
    does, then ``/new``; return what the harness is on after each and the status."""

    import aelix_coding_agent.tui as tui_pkg

    seen: dict[str, Any] = {}

    def _route(runtime: Any) -> tuple[str, str, str]:
        model = runtime.harness.current_model
        return (model.provider, model.id, model.api)

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        seen["launch"] = _route(runtime)
        seen["status"] = await kwargs["agent_service"].use(profile, harness=runtime.harness)
        seen["use"] = _route(runtime)
        await runtime.new_session()
        seen["new"] = _route(runtime)
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    seen["code"] = await entry_mod._async_main(["-e", str(env / "sessext.py"), "--approve", *argv])
    return seen


_HELD = ("sessext", "m1", "unknown")
_SWITCHED = ("sessext", "m1", "openai-completions")


@pytest.mark.parametrize("profile", ["plainprof", None], ids=["no-model-profile", "none"])
@pytest.mark.parametrize(
    ("argv", "settings", "openrouter", "subject"),
    [
        (["--model", "sessext/m1"], {}, "exported", "sessext/m1"),
        (["--model", "sessext/m1"], {}, "none", "sessext/m1"),
        (["--model", "m1"], {}, "none", "m1"),
        ([], {"defaultProvider": "sessext", "defaultModel": "m1"}, "exported", "sessext/m1"),
    ],
    ids=["slash-or", "slash-noor", "bare", "settings-pair"],
)
async def test_agents_use_naming_no_model_keeps_the_hold(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str | None,
    argv: list[str],
    settings: dict[str, str],
    openrouter: str,
    subject: str,
) -> None:
    """Verify round 1, B1. On dcc78170 ``/agents use plainprof`` (and ``--none``)
    reset ``parsed`` to the CLI baseline and re-resolved it over the registry
    ``session_start`` had filled: the harness went to ``sessext`` (api
    ``openai-completions``) and the next prompt was sent there — measured in a
    pty, "EXT POST /sess/v1/chat/completions" — though nobody picked a model.
    It now keeps the placeholder and says why again; ``/new`` after it stays held.
    """

    _openrouter(monkeypatch, env, openrouter)
    _profiles(env)
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    seen = await _agents_use(env, monkeypatch, argv, profile)
    assert seen["code"] == 0
    assert (seen["launch"], seen["use"], seen["new"]) == (_HELD, _HELD, _HELD)
    assert _REFUSAL.format(subject=subject) in seen["status"]
    assert "No prompt will be sent for it; run /model to select a model." in seen["status"]


@pytest.mark.parametrize(
    "settings",
    [{"defaultProvider": "sessext", "defaultModel": "m1"}, {"defaultModel": "sessext/m1"}],
    ids=["settings-pair", "settings-model-same-string"],
)
async def test_agents_use_of_a_profile_naming_the_model_switches(
    env: Path, monkeypatch: pytest.MonkeyPatch, settings: dict[str, str]
) -> None:
    """The user's explicit choice, unchanged: held from settings (not a flag, so
    the profile's ``model:`` applies), ``/agents use lateprof`` names
    ``sessext/m1`` itself and the provider is in the registry by then. The
    second row's profile string equals the held launch string: the profile
    named it, so it switches though the pair is the hold's key."""

    _openrouter(monkeypatch, env, "exported")
    _profiles(env)
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    seen = await _agents_use(env, monkeypatch, [], "lateprof")
    assert (seen["code"], seen["launch"], seen["use"]) == (0, _HELD, _SWITCHED)
    assert "session_start" not in seen["status"]


async def test_agents_use_whose_model_a_cli_flag_overrides_keeps_the_hold(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--model sessext/m1`` beats a profile's ``model:`` ("CLI flags override
    model"), so the model in effect after ``/agents use lateprof`` is the launch
    input the launch refused, re-resolved — held, like a profile naming none.
    On dcc78170 it switched to ``sessext``."""

    _openrouter(monkeypatch, env, "exported")
    _profiles(env)
    seen = await _agents_use(env, monkeypatch, ["--model", "sessext/m1"], "lateprof")
    assert (seen["code"], seen["launch"], seen["use"], seen["new"]) == (0, _HELD, _HELD, _HELD)
    assert "CLI flags override model" in seen["status"]
    assert _REFUSAL.format(subject="sessext/m1") in seen["status"]


# === verify round 1 (N1, N2): what the refusal says where ========================


async def test_rpc_hold_does_not_promise_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """N1. ``aelix --mode rpc`` wires no model registry, so ``set_model`` answers
    "requires a ModelRegistry"; dcc78170's warning said "run /model" there too."""

    handed, flags = _stub_mode(monkeypatch, "rpc")
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), "--model", "sessext/m1", *flags]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert [(m.provider, m.id, m.api) for m in handed] == [_HELD]
    assert _REFUSAL.format(subject="sessext/m1") in err
    assert "run /model" not in err
    assert "this RPC session cannot select another model" in err
    assert "restart with another --model" in err


@pytest.mark.parametrize("openrouter", ["exported", "none"])
async def test_a_provider_with_no_model_is_worded_as_a_provider(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    openrouter: str,
) -> None:
    """N2. A provider with no model: dcc78170 said 'The launch model
    "--provider sessext" names provider ...'. Since #368 a typed ``--provider
    sessext`` alone never gets here (pi's "--provider requires --model" exits
    before any extension loads — ``test_provider_requires_model_368.py``); a
    settings ``defaultProvider`` alone, no flags, is the input that still does."""

    _openrouter(monkeypatch, env, openrouter)
    (env / "agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "sessext"}), encoding="utf-8"
    )
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "-e", str(env / "sessext.py"), "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    assert "The launch model" not in err
    assert "--provider requires --model" not in err
    assert (
        "Error: The launch provider 'sessext' (no model named) was registered by an "
        "extension while a session was starting (for example in a session_start handler), "
        "after the launch route was chosen. "
        "Register 'sessext' in the extension's setup() (its factory) to use it at "
        "launch. No prompt was sent."
    ) in err


@pytest.mark.parametrize("openrouter", ["exported", "none"])
async def test_a_typed_provider_with_no_model_never_reaches_the_late_check(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    openrouter: str,
) -> None:
    """#368: the N2 input as typed (``--provider sessext`` alone) is pi's usage
    error now, before ``sessext.py`` loads — no late-provider text."""

    _openrouter(monkeypatch, env, openrouter)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "sessext.py"), "--provider", "sessext", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    assert "registered by an extension" not in err
    assert (
        "Error: --provider requires --model (for example: --provider sessext --model <id>)" in err
    )
