"""#344 / ADR-0249 — the LAUNCH path reaches the route the resolver decides.

(Rung 0 itself was replaced by pi's order in #362 / ADR-0250; X1, S and ``--api-key``
following the route are what ADR-0250 kept, and ``tests/cli/test_launch_route_362.py``
adds its own launch rows. The ``session_start`` late switch is gone: #367 refuses a
launch model naming such a provider, as pi does — the rows below that pinned the
switch say so, and ``tests/cli/test_late_provider_refused_367.py`` has the sweep's
other entry points.)

``test_provider_prefix_rung.py`` pins ``resolve_model``; these drive the real
``_async_main`` up to the first harness build (the spy on
``create_agent_session_runtime`` stops it before any turn) and read the model
the harness will really use:

* X1 — an EXTENSION provider is selectable at launch. The model used to be
  resolved before ``discover_and_load_extensions`` ran, so ``--model extprov/m1``
  went to OpenRouter with a key set (C4) and was refused as ``api='unknown'``
  without one (C5), as was ``--provider extprov --model m1`` (C6);
* the same after ``/reload`` — with an extension that only starts registering
  the provider after the first build, so a bind that ran on the first build only
  would stay invisible to a rebuild test that reused the first registration;
* ``--api-key`` follows the resolved route, per rung (critique S1, design C21);
* the settings.json ``defaultModel`` slash form (C17) and an ``--agent``
  profile's ``model:`` (C15) — both reach ``resolve_model`` as ``--model``.

Hermetic: fake keys, an isolated agent dir, no network (the run never gets to a
turn).
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli import entry as entry_mod

from tests.env_sandbox import sandbox_home

_BUILT = 42
_RP = "http://127.0.0.1:9/v1"
_EXT = "http://127.0.0.1:9/ext/v1"

_EXTENSION = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        aelix.register_provider(
            "extprov",
            ProviderConfigInput(
                name="ext probe",
                api_key="ext-fake-literal",
                models={{
                    "m1": Model(
                        id="m1",
                        provider="extprov",
                        api="openai-completions",
                        base_url={_EXT!r},
                    )
                }},
            ),
        )
    """
)


class _StopAfterBuild(Exception):
    pass


class _FakePipedStdin:
    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scratch cwd + agent dir holding a models.json custom provider."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "retryprobe": {
                        "api": "openai-completions",
                        "baseUrl": _RP,
                        "apiKey": "retryprobe-fake-literal",
                        "models": [{"id": "held-model"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return tmp_path


async def _run_to_harness(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> int:
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        runtime = await real_create(harness, factory, **kwargs)
        captured["harness"] = harness
        captured["runtime"] = runtime
        raise _StopAfterBuild

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    try:
        return await entry_mod._async_main(argv)
    except _StopAfterBuild:
        return _BUILT


def _route(harness: Any) -> tuple[str, str, str, str]:
    model = harness.current_model
    return (model.provider, model.id, model.api, model.base_url)


# === X1 — an extension provider is selectable at launch ======================


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
@pytest.mark.parametrize(
    "flags",
    [["--model", "extprov/m1"], ["--provider", "extprov", "--model", "m1"]],
    ids=["slash", "split"],
)
async def test_extension_provider_is_the_launch_model(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    with_openrouter_key: bool,
) -> None:
    """C4 (slash + key → OpenRouter), C5 (slash, no key → api='unknown'), C6."""

    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "extprov.py"
    ext.write_text(_EXTENSION, encoding="utf-8")
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        ["--no-session", "--print", "-e", str(ext), *flags], monkeypatch, captured
    )
    assert code == _BUILT
    try:
        assert _route(captured["harness"]) == ("extprov", "m1", "openai-completions", _EXT)
    finally:
        await captured["runtime"].dispose()


async def test_a_provider_registered_only_after_reload_is_the_rebuilt_model(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The /reload moat: write the extension, ``/reload``, the model resolves.

    The first build's extension registers nothing, so the first harness takes
    OpenRouter (the key is set and ``extprov`` is unknown). The file is then
    rewritten to register ``extprov`` and the REAL ``AgentSessionRuntime.reload()``
    rebuilds: the rebuilt harness must resolve inside ``extprov``. A bind that
    ran only on the first build (the critique's sabotage) leaves the queue
    unreplayed at resolve time, and this goes red.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "extprov.py"
    ext.write_text("def setup(aelix):\n    return None\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        ["--no-session", "--print", "-e", str(ext), "--model", "extprov/m1"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    runtime = captured["runtime"]
    try:
        assert captured["harness"].current_model.provider == "openrouter"
        ext.write_text(_EXTENSION, encoding="utf-8")
        await runtime.reload()
        assert _route(runtime.harness) == ("extprov", "m1", "openai-completions", _EXT)
    finally:
        await runtime.dispose()


# === --api-key follows the resolved route ====================================


@pytest.mark.parametrize(
    ("flags", "with_extension", "recipient"),
    [
        (["--model", "retryprobe/held-model"], False, "retryprobe"),  # 0a models.json
        (["--model", "extprov/m1"], True, "extprov"),  # 0a extension (X')
        (["--model", "held-model"], False, "retryprobe"),  # 0b
        (["--model", "xai/grok-4.3"], False, "xai"),  # 0c
        # #362 review: was "openrouter" (the old rung, then pi's swap). SUBJECT
        # CHANGED — ``--api-key`` keeps a vendor prefix on its vendor (ADR-0250
        # §2.1 step 3b); K typed for OpenAI no longer reaches openrouter.ai.
        (["--model", "openai/gpt-4o-mini"], False, "openai"),
        (["--provider", "openai", "--model", "gpt-4o-mini"], False, "openai"),  # explicit
    ],
    ids=["0a-models-json", "0a-extension", "0b-bare", "0c-xai", "vendor-prefix", "explicit"],
)
async def test_api_key_is_attached_to_the_provider_the_run_uses(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    with_extension: bool,
    recipient: str,
) -> None:
    """C21: the key meant for ``retryprobe`` was sent to OpenRouter as the bearer."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    extension_flags: list[str] = []
    if with_extension:
        ext = env / "extprov.py"
        ext.write_text(_EXTENSION, encoding="utf-8")
        extension_flags = ["-e", str(ext)]
    attached: list[str] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy(self: AuthStorage, provider: str, api_key: str) -> None:
        assert api_key == "probe-key-fake-literal"
        attached.append(provider)
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [
            "--no-session",
            "--print",
            *extension_flags,
            *flags,
            "--api-key",
            "probe-key-fake-literal",
        ],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    try:
        # The recipient first: on the old code the red line names where the key went.
        assert attached == [recipient]
        assert captured["harness"].current_model.provider == recipient
    finally:
        await captured["runtime"].dispose()


# === the other two ways a slash string reaches resolve_model =================


async def test_settings_default_model_slash_form_is_not_openrouter(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C17: a hand-written ``defaultModel`` with no ``defaultProvider``."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    (env / "agent" / "settings.json").write_text(
        json.dumps({"defaultModel": "retryprobe/held-model"}), encoding="utf-8"
    )
    captured: dict[str, Any] = {}
    assert await _run_to_harness(["--no-session", "--print"], monkeypatch, captured) == _BUILT
    try:
        assert _route(captured["harness"])[:2] == ("retryprobe", "held-model")
    finally:
        await captured["runtime"].dispose()


async def test_agent_profile_slash_model_is_not_openrouter(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C15: ``--agent`` with a profile ``model: retryprobe/held-model``."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    agents = env / "agent" / "agents"
    agents.mkdir()
    (agents / "probe.md").write_text(
        "---\nname: probe\ndescription: probe profile\n"
        "model: retryprobe/held-model\n---\nYou are a probe.\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        ["--no-session", "--print", "--agent", "probe"], monkeypatch, captured
    )
    assert code == _BUILT
    try:
        assert _route(captured["harness"])[:2] == ("retryprobe", "held-model")
    finally:
        await captured["runtime"].dispose()


def _persisted_default(env: Path) -> tuple[Any, Any]:
    """settings.json's ``(defaultProvider, defaultModel)`` — ``(None, None)`` if unwritten."""

    path = env / "agent" / "settings.json"
    if not path.exists():
        return None, None
    settings = json.loads(path.read_text(encoding="utf-8"))
    return settings.get("defaultProvider"), settings.get("defaultModel")


# === a provider registered in session_start arrives after the launch resolve ==

_SESSION_START_EXTENSION = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        async def _on_start(event, ctx):
            aelix.register_provider(
                "sessext",
                ProviderConfigInput(
                    name="session-start probe",
                    api_key="ext-fake-literal",
                    models={{
                        "m1": Model(
                            id="m1",
                            provider="sessext",
                            api="openai-completions",
                            base_url={_EXT!r},
                        )
                    }},
                ),
            )

        aelix.on("session_start", _on_start)
    """
)


_LATE_REFUSAL = (
    "The launch model \"sessext/m1\" names provider 'sessext', which an extension "
    "registered while a session was starting (for example in a session_start handler), "
    "after the launch model was chosen. "
    "Register 'sessext' in the extension's setup() (its factory) to use it at launch."
)


@pytest.mark.parametrize("openrouter", ["exported", "none"])
@pytest.mark.parametrize("mode_flags", [["-p"], ["--mode", "json", "-p"]], ids=["print", "json"])
async def test_a_session_start_provider_is_refused_in_print_and_json(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode_flags: list[str],
    openrouter: str,
) -> None:
    """#367 — subject changed: this row pinned the #344 switch (R4b); the run is refused.

    X1 resolves after the ``setup()`` registrations, but a ``session_start``
    registration lands inside ``create_agent_session_runtime`` — after the first
    build. With an OpenRouter key of the user's own the harness holds the string
    as an OpenRouter id (guard 2 — the first build's model, spied below), and
    without one the not-found placeholder. pi refuses such a launch model
    (``main.ts`` exits on ``resolveCliModel``'s error); so do print and json now,
    before any request, naming the provider and ``setup()``. On 0fcc3333 this
    shape reached openrouter.ai (12 × ``CONNECT openrouter.ai:443``); #344
    switched it to ``sessext``.
    """

    if openrouter == "exported":
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    from aelix_coding_agent import modes

    launched: list[tuple[str, str]] = []
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        launched.append((harness.current_model.provider, harness.current_model.id))
        return await real_create(harness, factory, **kwargs)

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", *mode_flags, "hi"]
    )
    captured = capsys.readouterr()
    assert (code, turns) == (1, [])
    assert f"Error: {_LATE_REFUSAL} No prompt was sent." in captured.err.splitlines()
    assert "switched" not in captured.err and "Note:" not in captured.err
    assert captured.out == ""
    assert launched == [
        ("openrouter", "sessext/m1") if openrouter == "exported" else ("sessext", "m1")
    ]
    assert _persisted_default(env) == (None, None)


async def test_a_session_start_provider_is_refused_whatever_a_model_switch_would_say(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#367 — subject changed: this row pinned R4b's refusal of a switch ``/model`` refused.

    There is no switch to ask any more: with ``/model``'s path stubbed to accept
    anything, print still refuses with the launch's own reason.
    """

    from aelix_coding_agent import modes
    from aelix_coding_agent.cli import model_switch

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    asked: list[str] = []

    async def _accept(argument: str, **kwargs: Any) -> Any:
        asked.append(argument)
        return model_switch.ModelSwitch(model=kwargs["harness"].current_model)

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(model_switch, "switch_model_argument", _accept)
    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns, asked) == (1, [], [])
    assert f"Error: {_LATE_REFUSAL} No prompt was sent." in err
    assert "run /model" not in err


async def test_a_setup_provider_is_not_flagged_as_late(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guard for the refusal above: a ``setup()`` registration runs normally."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "extprov.py"
    ext.write_text(_EXTENSION, encoding="utf-8")
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "extprov/m1", "-p", "hi"]
    )
    assert code == 0
    assert [(m.provider, m.id, m.base_url) for m in turns] == [("extprov", "m1", _EXT)]
    assert "session_start" not in capsys.readouterr().err


# === interactive and RPC: held (#367; #344's D1 switched) — /model leaves it in the TUI; RPC has no model registry ===


class _FakeTTYStdin:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover — never read on a TTY
        return ""


async def _run_session_start_mode(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    extra: list[str] | None = None,
    *,
    openrouter: bool = True,
    model: str = "sessext/m1",
) -> tuple[int, Any]:
    """Launch ``--model <model>`` (default ``sessext/m1``) in ``mode``; return the model the mode got.

    The mode's entry (``run_tui`` / ``run_rpc_mode``) is stubbed to record the
    harness model it would have driven its first prompt with — the model a typed
    ``hi`` (TUI) or a ``prompt`` command (RPC) goes to. ``openrouter`` exports an
    OpenRouter key of the user's own (guard 2 then takes the string at launch).
    """

    if openrouter:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    handed: list[Any] = []
    if mode == "interactive":
        import aelix_coding_agent.tui as tui_pkg

        async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
            handed.append(runtime.harness.current_model)
            return 0

        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
        mode_flags: list[str] = []
    else:
        from aelix_coding_agent import modes

        async def _stub_run_rpc(harness: Any, **kwargs: Any) -> None:
            handed.append(harness.current_model)

        monkeypatch.setattr(modes, "run_rpc_mode", _stub_run_rpc)
        mode_flags = ["--mode", "rpc"]
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", model, *mode_flags, *(extra or [])]
    )
    return code, handed


@pytest.mark.parametrize("openrouter", ["exported", "none"])
@pytest.mark.parametrize("mode", ["interactive", "rpc"])
async def test_a_session_start_provider_holds_interactive_and_rpc(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    openrouter: str,
) -> None:
    """#367 — subject changed: this row pinned the #344 switch (fix round 2, D1).

    Interactive and RPC start held on ``Model('m1', 'sessext')`` with
    ``api='unknown'`` (one ``set_model``, so one ``model_select``), which every
    turn entry refuses before a request (the TUI's #189 gate names ``/model``;
    an RPC ``prompt`` fails at the adapter lookup). One Warning gives the
    reason and, interactive, ``/model``'s guidance — RPC says it cannot pick
    another model (#367 verify round 1, N1: ``aelix --mode rpc`` wires no model
    registry for ``set_model``); nothing is persisted. On 8c9d6397 the first
    prompt went to OpenRouter; #344 switched to ``sessext``.
    """

    from aelix_agent_core.harness.core import AgentHarness

    selected: list[tuple[str, str, str]] = []
    real_set_model = AgentHarness.set_model

    async def _spy_set_model(self: AgentHarness, model: Any) -> None:
        selected.append((model.provider, model.id, model.api))
        await real_set_model(self, model)

    monkeypatch.setattr(AgentHarness, "set_model", _spy_set_model)
    code, handed = await _run_session_start_mode(
        env, monkeypatch, mode, openrouter=openrouter == "exported"
    )
    err = capsys.readouterr().err
    assert [(m.provider, m.id, m.api) for m in handed] == [("sessext", "m1", "unknown")]
    assert code == 0
    assert selected == [("sessext", "m1", "unknown")]
    remedy = (
        "No prompt will be sent for it; run /model to select a model."
        if mode == "interactive"
        else "No prompt will be sent for it, and this RPC session cannot select another "
        "model (set_model has no model registry in --mode rpc); restart with another --model."
    )
    assert f"Warning: {_LATE_REFUSAL}\n         {remedy}\n" in err
    assert err.count("session_start") == 1
    assert _persisted_default(env) == (None, None)


async def test_api_key_is_not_left_on_openrouter_by_a_refused_launch(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#367 — subject changed: this row pinned X' (the key followed the late switch).

    Guard 2 puts ``sessext/m1`` on OpenRouter at the first build. Before #367's
    round 3 ``--api-key`` was attached there and then taken off when the launch
    was refused; since then (D4) a pending launch attaches the key only AFTER
    the late decision, so a refused launch never attaches it to any provider —
    not one ``/model`` away from being sent to OpenRouter; no provider holds it.
    """

    attached: dict[str, str] = {}
    real_set = AuthStorage.set_runtime_api_key
    real_remove = AuthStorage.remove_runtime_api_key

    def _spy_set(self: AuthStorage, provider: str, api_key: str) -> None:
        attached[provider] = api_key
        real_set(self, provider, api_key)

    def _spy_remove(self: AuthStorage, provider: str) -> None:
        attached.pop(provider, None)
        real_remove(self, provider)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy_set)
    monkeypatch.setattr(AuthStorage, "remove_runtime_api_key", _spy_remove)
    code, handed = await _run_session_start_mode(
        env, monkeypatch, "rpc", ["--api-key", "probe-key-fake-literal"]
    )
    assert [(m.provider, m.id, m.api) for m in handed] == [("sessext", "m1", "unknown")]
    assert code == 0
    assert attached == {}


async def test_a_late_route_that_cannot_even_be_held_refuses_the_launch(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The last resort: the placeholder itself is rejected (a ``model_select`` handler).

    Then the harness is still on OpenRouter, so the run must not start at all.
    (#367: the switch that came first is gone; the rest of the row stands.)
    """

    from aelix_agent_core.harness.core import AgentHarness

    async def _reject(self: AgentHarness, model: Any) -> None:
        raise RuntimeError("model_select handler said no")

    monkeypatch.setattr(AgentHarness, "set_model", _reject)
    code, handed = await _run_session_start_mode(env, monkeypatch, "rpc")
    err = capsys.readouterr().err
    assert handed == []
    assert code == 1
    assert f"Error: {_LATE_REFUSAL}" in err


# === Codex cross-review C1 at the launch: a case collision with the catalogue ==

_CUSTOM = "http://127.0.0.1:9/custom-openai/v1"


def _write_case_models(env: Path, names: dict[str, str]) -> None:
    (env / "agent" / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    name: {
                        "api": "openai-completions",
                        "baseUrl": base_url,
                        "apiKey": f"{name.lower()}-fake-literal",
                        "models": [{"id": "m1"}],
                    }
                    for name, base_url in names.items()
                }
            }
        ),
        encoding="utf-8",
    )


async def test_api_key_for_a_capitalised_custom_provider_never_goes_to_openrouter(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C1: ``--model openai/m1 --api-key K`` with a custom ``OpenAI``.

    On ``8c9d6397`` the harness took OpenRouter and ``K`` was attached to
    ``openrouter`` — Codex's mock transport caught it as the OpenRouter bearer.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    _write_case_models(env, {"OpenAI": _CUSTOM})
    attached: list[str] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy(self: AuthStorage, provider: str, api_key: str) -> None:
        attached.append(provider)
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        ["--no-session", "--print", "--model", "openai/m1", "--api-key", "fake-custom-cli"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    try:
        assert _route(captured["harness"]) == ("OpenAI", "m1", "openai-completions", _CUSTOM)
        assert attached == ["OpenAI"]
    finally:
        await captured["runtime"].dispose()


async def test_a_prefix_two_custom_providers_share_up_to_case_is_refused_naming_both(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``OpenAI`` and ``OPENAI`` both custom, ``-p --model openai/m1``: no turn."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    _write_case_models(env, {"OpenAI": _CUSTOM, "OPENAI": "http://127.0.0.1:9/shouted/v1"})
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    # ``is_runnable`` fails open with no adapter registered (``register_providers``
    # runs in ``main_sync``, which this bypasses); judge against the one this
    # provider uses, without touching the process-wide api registry.
    from aelix_coding_agent.core.runnable_models import is_runnable

    monkeypatch.setattr(
        entry_mod, "is_runnable", lambda model: is_runnable(model, {"openai-completions"})
    )
    code = await entry_mod._async_main(["--no-session", "--model", "openai/m1", "-p", "hi"])
    err = capsys.readouterr().err
    assert turns == []
    assert code == 1
    assert "'OPENAI' and 'OpenAI'" in err and "differ only in case" in err


async def test_an_id_the_session_start_provider_does_not_list_is_held_too(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#367 — subject changed: this row pinned R4c (the switch printed #136's caution).

    ``--model sessext/m2`` names the same late provider; it is held like
    ``sessext/m1``, and no caution is printed (there is no switch to caution).
    """

    code, handed = await _run_session_start_mode(env, monkeypatch, "rpc", model="sessext/m2")
    err = capsys.readouterr().err
    assert [(m.provider, m.id, m.api) for m in handed] == [("sessext", "m2", "unknown")]
    assert code == 0
    assert "The launch model \"sessext/m2\" names provider 'sessext'" in err
    assert "⚠" not in err


async def test_rebuilds_after_a_late_refusal_stay_held(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#367 — subject changed: R4d pinned that every rebuild stayed on ``sessext``.

    ``/new``, ``/reload``, ``/fork`` and ``/resume`` re-resolve ``--model
    sessext/m1`` in ``_build_harness_options`` — before their own
    ``session_start`` — over a registry that still holds the registration the
    previous runtime's ``session_start`` made (the reuse ADR-0249 records as
    pi's, Codex C4). Without the hold each rebuild would land on ``sessext`` and
    run what the launch refused; with it, every one stays on the placeholder.
    """

    from aelix_ai.messages import TextContent, UserMessage

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    from aelix_coding_agent import modes

    routes: dict[str, tuple[str, str, str]] = {}

    def _held(runtime: Any) -> tuple[str, str, str]:
        model = runtime.harness.current_model
        return (model.provider, model.id, model.api)

    async def _drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        runtime = runtime_host
        routes["launch"] = _held(runtime)
        first = runtime.session.session_file
        entry_id = await runtime.session.append_message(
            UserMessage(content=[TextContent(text="hi")])
        )
        await runtime.new_session()
        routes["new"] = _held(runtime)
        await runtime.reload()
        routes["reload"] = _held(runtime)
        await runtime.switch_session(first)
        routes["resume"] = _held(runtime)
        await runtime.fork(entry_id)
        routes["fork"] = _held(runtime)

    monkeypatch.setattr(modes, "run_rpc_mode", _drive)
    code = await entry_mod._async_main(["-e", str(ext), "--model", "sessext/m1", "--mode", "rpc"])
    assert routes == {
        op: ("sessext", "m1", "unknown") for op in ("launch", "new", "reload", "resume", "fork")
    }
    assert code == 0


# === Codex second pass on ebfe411a =============================================


@pytest.mark.parametrize("named", ["openai", "OPENAI"], ids=["catalogue-spelling", "other-case"])
async def test_api_key_for_a_named_custom_provider_never_goes_to_the_vendor(
    env: Path, monkeypatch: pytest.MonkeyPatch, named: str
) -> None:
    """F1: ``--model m1 --provider openai --api-key K`` with a custom ``OpenAI``.

    On ``ebfe411a`` the harness took the catalogue's ``openai`` and ``K`` was
    attached to it — Codex's mock transport saw ``('https://api.openai.com/v1/
    responses', 'Bearer fake-custom-cli', True)``; ``--provider OPENAI`` was
    ``api='unknown'``. Now both are the user's ``OpenAI``, key and all.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    _write_case_models(env, {"OpenAI": _CUSTOM})
    attached: list[str] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy(self: AuthStorage, provider: str, api_key: str) -> None:
        attached.append(provider)
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [
            "--no-session",
            "--print",
            "--model",
            "m1",
            "--provider",
            named,
            "--api-key",
            "fake-custom-cli",
        ],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    try:
        assert _route(captured["harness"]) == ("OpenAI", "m1", "openai-completions", _CUSTOM)
        assert attached == ["OpenAI"]
    finally:
        await captured["runtime"].dispose()


async def test_a_named_provider_two_custom_providers_share_up_to_case_is_refused(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``OpenAI`` and ``OPENAI`` both custom, ``-p --model m1 --provider openai``: no turn."""

    _write_case_models(env, {"OpenAI": _CUSTOM, "OPENAI": "http://127.0.0.1:9/shouted/v1"})
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    from aelix_coding_agent.core.runnable_models import is_runnable

    monkeypatch.setattr(
        entry_mod,
        "is_runnable",
        lambda model: is_runnable(model, {"openai-completions", "openai-responses"}),
    )
    code = await entry_mod._async_main(
        ["--no-session", "--model", "m1", "--provider", "openai", "-p", "hi"]
    )
    err = capsys.readouterr().err
    # The turn first: on ebfe411a it ran on the catalogue's openai.
    assert turns == []
    assert code == 1
    assert "Error: --provider openai matches" in err
    assert "'OPENAI' and 'OpenAI'" in err


_CASE_CLASH_SESSION_START_EXTENSION = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        async def _on_start(event, ctx):
            for name in ("SessExt", "SESSEXT"):
                aelix.register_provider(
                    name,
                    ProviderConfigInput(
                        api_key="ext-fake-literal",
                        models={{
                            "m1": Model(
                                id="m1",
                                provider=name,
                                api="openai-completions",
                                base_url={_EXT!r},
                            )
                        }},
                    ),
                )

        aelix.on("session_start", _on_start)
    """
)


@pytest.mark.parametrize("mode", ["interactive", "rpc"])
async def test_a_late_case_clash_holds_interactive_and_rpc(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """F3: ``session_start`` registers ``SessExt`` and ``SESSEXT``; ``--model sessext/m1``.

    Print refused it at its #98 gate, but on ``ebfe411a`` RPC started on
    ``openrouter sessext/m1`` and sent the prompt to OpenRouter (Codex's
    ``ambiguous_rpc_*`` lines), because the held spelling is in neither set and
    the late check returned nothing. Now every mode holds the harness on
    ``Model('m1', 'sessext')`` (``api='unknown'``, which no turn entry runs) and
    the notice names both providers — without asking ``/model``, whose
    credential-filtered pool could hold one of them and switch to it.
    """

    from aelix_agent_core.harness.core import AgentHarness

    selected: list[tuple[str, str]] = []
    real_set_model = AgentHarness.set_model

    async def _spy_set_model(self: AgentHarness, model: Any) -> None:
        selected.append((model.provider, model.id))
        await real_set_model(self, model)

    monkeypatch.setattr(AgentHarness, "set_model", _spy_set_model)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    # ``is_runnable`` fails open with no adapter registered (``register_providers``
    # runs in ``main_sync``, which this bypasses), and then the #98 startup warning
    # never fires whatever ``late_route_held`` is - so the "Once" below would pin
    # nothing (round-4 verification: deleting ``and not late_route_held`` left this
    # green while the real TUI printed the Warning twice). Judge runnability against
    # the api this provider uses, as the neighbouring rows do.
    from aelix_coding_agent.core.runnable_models import is_runnable

    monkeypatch.setattr(
        entry_mod, "is_runnable", lambda model: is_runnable(model, {"openai-completions"})
    )
    ext = env / "clash.py"
    ext.write_text(_CASE_CLASH_SESSION_START_EXTENSION, encoding="utf-8")
    handed: list[Any] = []
    if mode == "interactive":
        import aelix_coding_agent.tui as tui_pkg

        async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
            handed.append(runtime.harness.current_model)
            return 0

        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
        monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
        mode_flags: list[str] = []
    else:
        from aelix_coding_agent import modes

        async def _stub_run_rpc(harness: Any, **kwargs: Any) -> None:
            handed.append(harness.current_model)

        monkeypatch.setattr(modes, "run_rpc_mode", _stub_run_rpc)
        mode_flags = ["--mode", "rpc"]
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", *mode_flags]
    )
    err = capsys.readouterr().err
    # The route first: on ebfe411a the red line names openrouter.
    assert [(m.provider, m.id, m.api) for m in handed] == [("sessext", "m1", "unknown")]
    assert code == 0
    assert selected == [("sessext", "m1")]
    assert "'SESSEXT' and 'SessExt'" in err and "differ only in case" in err
    assert ("run /model" in err) == (mode == "interactive")
    assert ("restart with another --model" in err) == (mode == "rpc")
    # Once: the #98 startup warning stays quiet after the late-route Warning.
    assert err.count("differ only in case") == 1
    assert _persisted_default(env) == (None, None)


async def test_a_late_case_clash_refuses_print(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F3's print row: refused naming both, no turn — as on ``ebfe411a`` (a guard)."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "clash.py"
    ext.write_text(_CASE_CLASH_SESSION_START_EXTENSION, encoding="utf-8")
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    from aelix_coding_agent.core.runnable_models import is_runnable

    monkeypatch.setattr(
        entry_mod, "is_runnable", lambda model: is_runnable(model, {"openai-completions"})
    )
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert turns == []
    assert code == 1
    assert "'SESSEXT' and 'SessExt'" in err


_REGISTER_THEN_UNREGISTER_EXTENSION = textwrap.dedent(
    f"""
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        async def _on_start(event, ctx):
            aelix.register_provider(
                "sessext",
                ProviderConfigInput(
                    api_key="ext-fake-literal",
                    models={{
                        "m1": Model(
                            id="m1",
                            provider="sessext",
                            api="openai-completions",
                            base_url={_EXT!r},
                        )
                    }},
                ),
            )
            aelix.unregister_provider("sessext")

        aelix.on("session_start", _on_start)
    """
)


async def test_a_provider_unregistered_before_session_start_returns_is_unknown(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F2, a documented edge (ADR-0249 §2.3), pinned as a guard.

    A ``session_start`` handler that registers ``sessext`` and unregisters it
    before returning leaves no ``sessext`` in the registry: ``sessext/m1`` is an
    unknown prefix, which goes to OpenRouter by the owner's rule — the same as
    a launch that never registered it. No late switch. Since #362 / ADR-0250
    that rule is guard 2 (an exported OpenRouter key), and its one-line Note
    says so; nothing mentions ``session_start``.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    ext = env / "gone.py"
    ext.write_text(_REGISTER_THEN_UNREGISTER_EXTENSION, encoding="utf-8")
    from aelix_coding_agent import modes

    turns: list[Any] = []

    async def _no_turn(runtime: Any, **kwargs: Any) -> int:
        turns.append(runtime.harness.current_model)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _no_turn)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openrouter", "sessext/m1")]
    assert "session_start" not in err and "switched" not in err
    assert [line for line in err.splitlines() if line.startswith("Note:")] == [
        'Note: Model "sessext/m1" is not in this build\'s catalog; sending it to '
        "OpenRouter as written."
    ]
