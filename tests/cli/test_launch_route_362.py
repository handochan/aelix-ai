"""#362 / ADR-0250 at the LAUNCH: the route's refusals, warnings, ``--api-key`` and the late path.

``test_route_follows_pi_362.py`` pins the resolver. These drive the real
``_async_main`` (the mode entry stubbed, so no turn runs and nothing is sent):

* print/json refuse an ambiguous or unknown ``--model`` with pi's own text, before
  any request (on ``aa026d08`` an OpenRouter key turned both into an OpenRouter
  id, and without one the run reached ``No provider registered for
  api='unknown'`` at the first turn);
* the custom-id Warning and guard 2's Note are printed once, on stderr;
* ``--api-key`` with a bare extension id is no longer refused before the
  extensions load (ADR-0249 §6), and follows a dual-key user's route to OpenAI;
* a provider registered in ``session_start`` is refused at launch with or without
  an OpenRouter key (#367; #362 had switched to it — the critique's S5 — and the
  rows below that pinned the switch say their subject changed), and an explicit
  ``/model`` to it afterwards still reaches it, on a ``.env`` key too.

Hermetic: fake keys, an isolated agent dir, no network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

from tests.cli.test_launch_route_344 import (
    _EXT,
    _EXTENSION,
    _SESSION_START_EXTENSION,
    _FakePipedStdin,
)
from tests.env_sandbox import sandbox_home


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
    return tmp_path


def _dotenv(monkeypatch: pytest.MonkeyPatch, env: Path, text: str) -> None:
    path = env / "cwd" / ".env"
    path.write_text(text, encoding="utf-8")
    for name in [line.partition("=")[0] for line in text.splitlines()] + ["AELIX_DOTENV_ADMITTED"]:
        if name and name not in os.environ:
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


@pytest.mark.parametrize(
    ("exported", "model", "error"),
    [
        (
            {"OPENROUTER_API_KEY": "or-fake"},
            "gpt-4o-mini",
            'Error: Model "gpt-4o-mini" is ambiguous across providers: '
            "azure-openai-responses/gpt-4o-mini, cloudflare-ai-gateway/gpt-4o-mini, "
            "openai/gpt-4o-mini. No matching provider is authenticated. Use --provider or "
            "provider/model.",
        ),
        (
            {},
            "newlab/model-x",
            'Error: Model "newlab/model-x" not found. Use --list-models to see available models.',
        ),
    ],
    ids=["ambiguous", "not-found"],
)
@pytest.mark.parametrize("mode_flags", [["-p"], ["--mode", "json", "-p"]], ids=["print", "json"])
async def test_print_and_json_refuse_with_pis_text_before_any_turn(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exported: dict[str, str],
    model: str,
    error: str,
    mode_flags: list[str],
) -> None:
    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--model", model, *mode_flags, "hi"])
    captured = capsys.readouterr()
    assert turns == []
    assert code == 1
    assert error in captured.err.splitlines()
    assert captured.out == ""


@pytest.mark.parametrize(
    ("exported", "model", "line"),
    [
        (
            {"ANTHROPIC_API_KEY": "ant-fake"},
            "anthropic/claude-new-9",
            'Warning: Model "claude-new-9" not found for provider "anthropic". Using custom model id.',
        ),
        (
            {"OPENROUTER_API_KEY": "or-fake"},
            "newlab/model-x",
            'Note: Model "newlab/model-x" is not in this build\'s catalog; sending it to '
            "OpenRouter as written.",
        ),
    ],
    ids=["custom-id", "guard2"],
)
async def test_the_route_warning_is_printed_once_on_stderr(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exported: dict[str, str],
    model: str,
    line: str,
) -> None:
    """Printed after the first build; a rebuild (``/reload``) does not print it again."""

    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    from aelix_coding_agent import modes

    rebuilt: list[Any] = []

    async def _drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        await runtime_host.reload()
        rebuilt.append(runtime_host.harness.current_model)

    monkeypatch.setattr(modes, "run_rpc_mode", _drive)
    code = await entry_mod._async_main(["--no-session", "--model", model, "--mode", "rpc"])
    captured = capsys.readouterr()
    assert code == 0 and len(rebuilt) == 1
    assert captured.err.splitlines().count(line) == 1, captured.err
    assert line not in captured.out


async def test_api_key_with_a_bare_extension_id_reaches_the_extension(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0249 §6: ``-e extprov.py --model m1 --api-key K`` was "--api-key requires a model".

    The early check ran before the extension loaded. pi's step 2 makes the bare
    id the extension's (the one provider that serves it), and the key follows
    the harness model.
    """

    ext = env / "extprov.py"
    ext.write_text(_EXTENSION, encoding="utf-8")
    attached: list[str] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy(self: AuthStorage, provider: str, api_key: str) -> None:
        attached.append(provider)
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "m1", "--api-key", "k-fake", "-p", "hi"]
    )
    assert code == 0
    assert attached == ["extprov"]
    assert [(m.provider, m.id, m.base_url) for m in turns] == [("extprov", "m1", _EXT)]


async def test_api_key_follows_a_dual_key_users_route_to_the_vendor(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both keys exported: ``openai/gpt-4o-mini`` is OpenAI's (pi), and so is ``--api-key``."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "oai-fake")
    attached: list[str] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy(self: AuthStorage, provider: str, api_key: str) -> None:
        attached.append(provider)
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--model", "openai/gpt-4o-mini", "--api-key", "k-fake", "-p", "hi"]
    )
    assert code == 0
    assert attached == ["openai"]
    assert [m.provider for m in turns] == ["openai"]


@pytest.mark.parametrize("openrouter", ["dotenv", "none"])
async def test_a_session_start_provider_the_launch_could_not_place_is_refused(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    openrouter: str,
) -> None:
    """#367 decision 2 — subject changed: this row pinned the critique's S5 switch.

    With no OpenRouter key of the user's own, ``sessext/m1`` is an unknown prefix
    the launch holds (it is registered only in ``session_start``). On
    ``aa026d08`` a ``.env`` OpenRouter key put it on OpenRouter first (then the
    late switch moved it), and with no key the first turn failed with ``No
    provider registered for api='unknown'``; #362 switched both. Now both get
    the refusal an exported OpenRouter key gets, before any request.
    """

    if openrouter == "dotenv":
        _dotenv(monkeypatch, env, "OPENROUTER_API_KEY=or-dotenv-planted\n")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    assert (
        "Error: The launch model \"sessext/m1\" names provider 'sessext', which an "
        "extension registered while a session was starting (for example in a "
        "session_start handler), after the launch model was "
        "chosen. Register 'sessext' in the extension's setup() (its factory) to use it "
        "at launch. No prompt was sent."
    ) in err
    assert "openrouter" not in err.lower().replace("openrouter_api_key", "")


async def test_openrouter_default_model_from_a_dotenv_selects_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A24: on ``aa026d08`` the ``.env`` value chose the model AND OpenRouter for it."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _dotenv(
        monkeypatch,
        env,
        "OPENROUTER_API_KEY=or-dotenv-planted\nOPENROUTER_DEFAULT_MODEL=anthropic/claude-haiku-4.5\n",
    )
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "-p", "hi"])
    err = capsys.readouterr().err
    assert turns == []
    assert code == 1
    assert "No model selected" in err


# === the #362 review round ======================================================


def _spy_runtime_keys(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attached: list[str] = []
    real_set = AuthStorage.set_runtime_api_key

    def _spy(self: AuthStorage, provider: str, api_key: str) -> None:
        attached.append(provider)
        real_set(self, provider, api_key)

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _spy)
    return attached


@pytest.mark.parametrize(
    ("model", "landed"),
    [
        ("anthropic/claude-haiku-4-5", ("anthropic", "claude-haiku-4-5")),
        ("anthropic/claude-new-9", ("anthropic", "claude-new-9")),
        ("openai/gpt-4o-mini", ("openai", "gpt-4o-mini")),
        ("anthropic/claude-haiku-4.5", ("anthropic", "claude-haiku-4.5")),
    ],
    ids=["R03-widened-guard2", "R02-guard2", "A40-swap", "raw-match"],
)
async def test_api_key_is_not_carried_to_openrouter_for_a_vendor_prefix(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    model: str,
    landed: tuple[str, str],
) -> None:
    """The #362 review's must_fix: ``--api-key`` went to ``openrouter.ai`` as the bearer.

    With ``OPENROUTER_API_KEY`` exported, ``--model anthropic/claude-haiku-4-5
    --api-key K`` resolved by guard 2's widened arm to OpenRouter and K — typed
    for Anthropic — replaced the user's own OpenRouter key there (measured on
    ``d58cbb3e``: ``OR /or/v1/chat/completions model=anthropic/claude-haiku-4-5
    token=apikey``, with a Note claiming "you hold no anthropic credential of
    your own"). The key now keeps the string on the provider its prefix names.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    attached = _spy_runtime_keys(monkeypatch)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--model", model, "--api-key", "k-fake", "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert attached == [landed[0]]
    assert [(m.provider, m.id) for m in turns] == [landed]
    assert "OpenRouter" not in err


@pytest.mark.parametrize(
    ("exported", "model"),
    [({"OPENROUTER_API_KEY": "or-fake"}, "gpt-4o-mini"), ({}, "newlab/model-x")],
    ids=["ambiguous-no-provider", "not-found-keeps-the-prefix"],
)
async def test_a_route_that_does_not_resolve_gets_no_api_key(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    exported: dict[str, str],
    model: str,
) -> None:
    """ADR-0250 §2.7: "A route that does not resolve gets no key".

    The ambiguous placeholder has no provider (``_attach_api_key``'s own guard;
    the review's SB37 removed it and nothing turned red). The not-found one keeps
    the typed prefix as its provider for the late path, and on ``d58cbb3e`` got
    the key on ``newlab`` (the review's nit 1).
    """

    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    attached = _spy_runtime_keys(monkeypatch)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--model", model, "--api-key", "k-fake", "-p", "hi"]
    )
    assert (code, turns, attached) == (1, [], [])


async def test_api_key_with_a_session_start_provider_the_launch_could_not_place_is_refused(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#367 decision 4 — subject changed: the late switch attached the key to ``sessext``.

    Held at launch (no key attached); the launch model is refused, so no
    provider gets the key and no turn runs.
    """

    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    attached = _spy_runtime_keys(monkeypatch)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(ext), "--model", "sessext/m1", "--api-key", "k-fake", "-p", "hi"]
    )
    assert (code, turns, attached) == (1, [], [])
    assert "Register 'sessext' in the extension's setup()" in capsys.readouterr().err


async def test_the_late_path_leaves_a_held_route_that_is_still_not_runnable(
    env: Path,
) -> None:
    """``late_registered_route`` on a held route a provider the LAUNCH knew cannot run.

    A user-defined provider with no api and no models (an auth-only
    registration) holds ``emptyext/anything`` on ``api='unknown'`` at launch, and
    the re-resolve lands on the same placeholder. Registered before the launch
    resolve (``setup()``), that is a refusal the launch already reports, not a
    late registration (the review's SB38 removed this return and nothing turned
    red). #367 — subject changed in part: registered in ``session_start``
    (absent from ``launch_providers``), the same string is a late one and gets
    the late refusal, which says where to register it.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import late_registered_route, resolve_model
    from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

    agent = env / "agent"
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    registry.register_provider("emptyext", ProviderConfigInput(api_key="x"))
    held = resolve_model("emptyext/anything", None, registry)
    assert (held.provider, held.api) == ("emptyext", "unknown")
    assert (
        late_registered_route(
            "emptyext/anything", None, registry, launch_providers=frozenset({"emptyext"})
        )
        is None
    )
    late = late_registered_route("emptyext/anything", None, registry, launch_providers=frozenset())
    assert late is not None and "names provider 'emptyext'" in late


# === round 3: the round-2 verification of ecb4e0bc ===============================


_SESSION_START_ENV_KEY_EXTENSION = _SESSION_START_EXTENSION.replace(
    'api_key="ext-fake-literal"', 'api_key="SESSENV_API_KEY"'
).replace('"sessext"', '"sessenv"')


@pytest.mark.parametrize("own_key", ["openrouter-exported", "none"])
async def test_model_reaches_a_session_start_provider_whose_key_is_in_the_dotenv(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    own_key: str,
) -> None:
    """Round-2 verification B1, real launch: ``-e sessenv.py --model sessenv/m1``.

    The extension registers ``sessenv`` in ``session_start`` with an
    ``api_key`` naming ``SESSENV_API_KEY``, which the cwd ``.env`` supplies. With
    ``OPENROUTER_API_KEY`` exported, ``ecb4e0bc`` refused the late switch —
    ``/model``'s guard took the ``.env``-keyed provider out of its pool — and B1
    fixed it there. #367 — subject changed: the launch no longer switches, so the
    launch model is held (interactive) and the row now pins what B1 fixed in the
    ``/model`` path itself: an explicit ``/model sessenv/m1`` in that session
    reaches the user's host with the ``.env`` key as the bearer, with or without
    an own key elsewhere (the prefix names a provider the user defined, §2.8).
    """

    import aelix_coding_agent.tui as tui_pkg
    from aelix_coding_agent.cli.model_switch import switch_model_argument

    from tests.cli.test_launch_route_344 import _FakeTTYStdin

    if own_key == "openrouter-exported":
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    _dotenv(monkeypatch, env, "SESSENV_API_KEY=sessenv-dotenv-fake\n")
    ext = env / "sessenv.py"
    ext.write_text(_SESSION_START_ENV_KEY_EXTENSION, encoding="utf-8")

    seen: list[tuple[str, str, str, str | None]] = []

    async def _stub_run_tui(runtime: Any, **kwargs: Any) -> int:
        harness = runtime.harness
        seen.append((harness.current_model.provider, harness.current_model.api, "", None))
        registry = kwargs["model_registry"]
        switched = await switch_model_argument(
            "sessenv/m1",
            harness=harness,
            model_registry=registry,
            settings_manager=None,
            warn=lambda _line: None,
        )
        assert switched.refusal is None, switched.refusal
        model = harness.current_model
        auth = await registry.get_api_key_and_headers(model)
        seen.append((model.provider, model.id, model.base_url, auth.api_key))
        return 0

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session", "-e", str(ext), "--model", "sessenv/m1"])
    err = capsys.readouterr().err
    assert (code, seen) == (
        0,
        [
            ("sessenv", "unknown", "", None),
            ("sessenv", "m1", _EXT, "sessenv-dotenv-fake"),
        ],
    ), err
    assert "names provider 'sessenv'" in err and "run /model" in err


# === Codex's third cross-review (C3): a .env value never addresses a request =======

_TENANT_TEMPLATE = "https://{TENANT_KEY}.owner-gateway.invalid/v1"
_REPO_TENANT = "repo-chosen.invalid/v1#"


@pytest.mark.parametrize(
    ("row", "exported", "dotenv", "models_json", "model", "host"),
    [
        # The user's template, the cloned repo's .env fills the placeholder: no request.
        (
            "or-template-dotenv",
            {"OPENAI_API_KEY": "own-openai-fake"},
            f"TENANT_KEY={_REPO_TENANT}\nOPENROUTER_API_KEY=repo-router-fake\n",
            {"openrouter": {"baseUrl": _TENANT_TEMPLATE}},
            "openrouter/auto",
            None,
        ),
        (
            "user-defined-template-dotenv",
            {"OPENAI_API_KEY": "own-openai-fake"},
            f"TENANT_KEY={_REPO_TENANT}\nMYGW_API_KEY=mygw-dotenv-fake\n",
            {
                "mygw": {
                    "api": "openai-completions",
                    "baseUrl": _TENANT_TEMPLATE,
                    "apiKey": "MYGW_API_KEY",
                    "models": [{"id": "m1"}],
                }
            },
            "mygw/m1",
            None,
        ),
        # The same value exported: the user chose it, so it is expanded.
        (
            "or-template-exported",
            {"OPENAI_API_KEY": "own-openai-fake", "TENANT_KEY": _REPO_TENANT},
            "OPENROUTER_API_KEY=repo-router-fake\n",
            {"openrouter": {"baseUrl": _TENANT_TEMPLATE}},
            "openrouter/auto",
            "repo-chosen.invalid",
        ),
        # Cloudflare's ids from a .env are template configuration (ADR-0203): expanded.
        (
            "cloudflare-dotenv",
            {"OPENAI_API_KEY": "own-openai-fake"},
            "CLOUDFLARE_ACCOUNT_ID=acct1\nCLOUDFLARE_GATEWAY_ID=gw1\n"
            "CLOUDFLARE_API_KEY=cf-dotenv-fake\n",
            {},
            "cloudflare-ai-gateway/claude-3-5-haiku",
            "gateway.ai.cloudflare.com",
        ),
    ],
    ids=[
        "or-template-dotenv",
        "user-defined-template-dotenv",
        "or-template-exported",
        "cloudflare-dotenv",
    ],
)
async def test_a_dotenv_value_never_fills_the_host_of_a_template(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    row: str,
    exported: dict[str, str],
    dotenv: str,
    models_json: dict[str, Any],
    model: str,
    host: str | None,
) -> None:
    """Codex's third cross-review of #362, C3 (``probe_template.py``), at the launch.

    ``_base_url.expand_base_url`` filled any ``{NAME}`` from ``os.environ``, so a
    cloned repo's ``.env`` ``TENANT_KEY=repo-chosen.invalid/v1#`` (admitted as a
    credential by its ``_KEY`` suffix) turned the user's
    ``https://{TENANT_KEY}.owner-gateway.invalid/v1`` into host
    ``repo-chosen.invalid`` with the repo's key (5e983992, real CLI:
    ``CONNECT repo-chosen.invalid:443``). A ``.env`` name now fills a
    placeholder only as ADR-0203 template configuration (the two Cloudflare
    ids); otherwise the token stays, the model is not runnable, and the refusal
    says to export the variable. Exported, the same value is the user's choice.
    """

    from aelix_ai.providers._base_url import expand_base_url
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    # The launch's runnable gate fails open with no adapter registered (entry.py).
    register_providers()
    for name, value in exported.items():
        monkeypatch.setenv(name, value)
    (env / "agent" / "models.json").write_text(
        json.dumps({"providers": models_json}), encoding="utf-8"
    )
    _dotenv(monkeypatch, env, dotenv)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--model", model, "-p", "hi"])
    err = capsys.readouterr().err
    if host is None:
        assert (code, turns) == (1, []), (row, err)
        assert "set the environment variable(s) TENANT_KEY" in err, err
        assert "TENANT_KEY came from a project .env" in err, err
        assert "export it in your shell" in err, err
        return
    assert code == 0 and len(turns) == 1, (row, err)
    expanded = expand_base_url(turns[0].base_url) or ""
    assert urlsplit(expanded).hostname == host, expanded
