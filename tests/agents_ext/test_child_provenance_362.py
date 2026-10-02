"""#362 / ADR-0250 — a delegated child decides a route the way its parent would.

Two halves:

* PROVENANCE. A child inherits the parent's environment wholesale
  (``build_child_env``), so a key a cwd ``.env`` admitted in the parent reaches
  the child looking exported. ``load_dotenv`` therefore records the names in
  ``AELIX_DOTENV_ADMITTED`` — which ``build_child_env`` and the bash tool's
  ``get_shell_env`` pass along — and a child that re-reads the same ``.env``
  UNIONS the inherited record instead of overwriting it. Proved here with a REAL
  child on the wire: the #362 planted capture (``ANTHROPIC_API_KEY`` exported,
  ``OPENROUTER_API_KEY`` from the project ``.env``,
  ``--model anthropic/claude-haiku-4.5``) went to OpenRouter on ``aa026d08``.
* THE PIN (``resolver._pin_route``). The parent splits ``--model`` into
  ``--model <id> --provider <p>`` only where the child could not reach its
  route alone: a user-defined provider (the child loads no extensions by
  default, #344 M3). A credential-based decision — including one only the
  parent's ``--api-key`` made — is re-derived by the child, and must be, because a
  profile's own ``extensions:`` give the child providers the parent never saw:
  pinning the parent's guard-2 answer sent ``childext/m1`` to OpenRouter (the
  #362 critique, M1; the real-spawn row below).

Fake keys only; every endpoint is a local recorder (``tests/route_wire.py``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.print_channel import build_child_env
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import child_model_flags, profile_to_flags
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

from tests.env_sandbox import child_env
from tests.route_wire import WireRecorder

_CHILD_TIMEOUT = 120


@pytest.fixture
def scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "retryprobe": {
                        "api": "openai-completions",
                        "baseUrl": "http://127.0.0.1:9/rp/v1",
                        "apiKey": "rp-fake-literal",
                        "models": [{"id": "held-model"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    return agent


async def _registry(agent: Path, *, ext: bool = True) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    if ext:
        # The PARENT loaded this extension; a default child (``--no-extensions``) does not.
        registry.register_provider(
            "extprov",
            ProviderConfigInput(
                api_key="ext-fake-literal",
                models={
                    "m1": Model(
                        id="m1",
                        provider="extprov",
                        api="openai-completions",
                        base_url="http://127.0.0.1:9/ext/v1",
                    )
                },
            ),
        )
    return registry


def _profile(
    model: str | None = None, provider: str | None = None, extensions: tuple[str, ...] = ()
) -> AgentProfile:
    return AgentProfile(
        name="p",
        description="d",
        body="b",
        file_path="/p.md",
        scope="user",
        model=model,
        provider=provider,
        extensions=extensions,
    )


# === the pin ====================================================================

PINS: list[tuple[Any, ...]] = [
    # id, env, --model, --provider, profile extensions, expected flags
    (
        "user-defined models.json",
        {"OPENROUTER_API_KEY": "or"},
        "retryprobe/held-model",
        None,
        (),
        ["--model", "held-model", "--provider", "retryprobe"],
    ),
    (
        "user-defined extension",
        {"OPENROUTER_API_KEY": "or"},
        "extprov/m1",
        None,
        (),
        ["--model", "m1", "--provider", "extprov"],
    ),
    (
        "user-defined extension, profile brings others",
        {"OPENROUTER_API_KEY": "or"},
        "extprov/m1",
        None,
        ("/abs/other.py",),
        ["--model", "m1", "--provider", "extprov"],
    ),
    (
        "auth swap: the child decides it the same way",
        {"OPENROUTER_API_KEY": "or"},
        "openai/gpt-4o-mini",
        None,
        (),
        ["--model", "openai/gpt-4o-mini"],
    ),
    (
        "prefix, no raw candidate",
        {"ANTHROPIC_API_KEY": "ant"},
        "anthropic/claude-haiku-4-5",
        None,
        (),
        ["--model", "anthropic/claude-haiku-4-5"],
    ),
    (
        "guard 2: never pinned",
        {"OPENROUTER_API_KEY": "or"},
        "newlab/model-x",
        None,
        (),
        ["--model", "newlab/model-x"],
    ),
    (
        "C1 a profile's own extension provider",
        {"OPENROUTER_API_KEY": "or"},
        "childext/m1",
        None,
        ("/abs/childext.py",),
        ["--model", "childext/m1"],
    ),
    (
        "error: as typed",
        {"OPENROUTER_API_KEY": "or"},
        "gpt-4o-mini",
        None,
        (),
        ["--model", "gpt-4o-mini"],
    ),
    (
        "explicit provider: the parent's spelling",
        {},
        "x-ai/grok-4.3",
        "OPENROUTER",
        (),
        ["--model", "x-ai/grok-4.3", "--provider", "openrouter"],
    ),
]


@pytest.mark.parametrize(
    ("row", "env", "model", "provider", "extensions", "expected"),
    PINS,
    ids=[p[0] for p in PINS],
)
async def test_the_child_is_pinned_only_where_it_could_not_decide_alone(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    row: str,
    env: dict[str, str],
    model: str,
    provider: str | None,
    extensions: tuple[str, ...],
    expected: list[str],
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    registry = await _registry(scrubbed)
    flags = child_model_flags(_profile(model, provider, extensions), None, registry)
    assert flags == expected, row


@pytest.mark.parametrize("openrouter", ["none", "dotenv"])
async def test_a_route_only_the_parents_api_key_decided_is_not_pinned(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, openrouter: str
) -> None:
    """``--api-key`` never reaches a child, so the child decides without it — unpinned.

    SUBJECT CHANGED in the #362 review round: ``d58cbb3e`` pinned this route
    (``--provider openrouter``). The parent's ``--api-key`` sits on
    ``openrouter`` as a runtime override and resolves ``openai/gpt-4o-mini`` by
    pi's swap to OpenRouter; a child, which never holds that key, has no
    OpenRouter credential of its own (had it one, the key would not have changed
    the route). Pinned, it authenticated OpenRouter with what it had — with a
    project ``.env`` ``OPENROUTER_API_KEY``, the planted key the parent kept out
    of routing (the review's R13: ``OR /or/v1/chat/completions
    model=anthropic/claude-haiku-4-5 token=or_env``). Unpinned, it re-decides
    from its own credentials, as every other credential decision is left to it.
    """

    if openrouter == "dotenv":
        _load_project_dotenv(monkeypatch, tmp_path / "project", "OPENROUTER_API_KEY=planted\n")
    registry = await _registry(scrubbed)
    registry._auth_storage.set_runtime_api_key("openrouter", "or-runtime-fake")
    for model in ("openai/gpt-4o-mini", "anthropic/claude-haiku-4-5", "newlab/model-x"):
        assert child_model_flags(_profile(model), None, registry) == ["--model", model], model
        assert child_model_flags(_profile(model, extensions=("/abs/x.py",)), None, registry) == [
            "--model",
            model,
        ], model


# === provenance reaches every child =============================================


def _load_project_dotenv(monkeypatch: pytest.MonkeyPatch, project: Path, text: str) -> None:
    project.mkdir(exist_ok=True)
    (project / ".env").write_text(text, encoding="utf-8")
    for line in text.splitlines():
        name = line.partition("=")[0].strip()
        if name and name not in os.environ:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "")
    monkeypatch.delenv("AELIX_DOTENV_ADMITTED")
    load_dotenv(str(project / ".env"))


def test_the_record_reaches_a_delegated_child_and_the_bash_tool(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aelix_coding_agent.util.shell_env import get_shell_env

    _load_project_dotenv(monkeypatch, tmp_path / "project", "OPENROUTER_API_KEY=planted\n")
    assert os.environ.get("AELIX_DOTENV_ADMITTED") == "OPENROUTER_API_KEY"
    assert build_child_env(_profile("x"))["AELIX_DOTENV_ADMITTED"] == "OPENROUTER_API_KEY"
    assert get_shell_env()["AELIX_DOTENV_ADMITTED"] == "OPENROUTER_API_KEY"


@pytest.fixture
def wire() -> Iterator[tuple[WireRecorder, WireRecorder]]:
    proxy, endpoint = WireRecorder("PROXY"), WireRecorder("ENDPOINT")
    try:
        yield proxy, endpoint
    finally:
        proxy.close()
        endpoint.close()


def _spawn_child(
    argv: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--no-session", *argv, "-p", "hi"],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_CHILD_TIMEOUT,
        # Windows: the child writes UTF-8; text=True alone decodes cp1252.
        encoding="utf-8",
        errors="replace",
    )


def _hermetic_agent(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "childhome"
    agent = home / "agent"
    agent.mkdir(parents=True)
    (home / ".config").mkdir()
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    # One attempt: the recorder answers 403/400, and a retry ladder only slows the row.
    (agent / "settings.json").write_text(
        json.dumps({"retry": {"enabled": False}}), encoding="utf-8"
    )
    return home, agent


async def test_a_real_child_does_not_let_the_parents_dotenv_key_choose_its_route(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wire: tuple[WireRecorder, WireRecorder],
) -> None:
    """The planted capture, one process down (design row A10, G1 real child).

    The parent exports ``ANTHROPIC_API_KEY`` and its project ``.env`` carries
    ``OPENROUTER_API_KEY``. The child gets ``build_child_env`` — the parent's
    environment, record included — and starts in the same project, so it
    re-reads the same ``.env`` (and admits nothing: the keys are already set).
    It must send ``anthropic/claude-haiku-4.5`` to Anthropic on the user's own
    key, never to OpenRouter on the file's. OpenRouter is pointed at a local
    recorder through an EXPORTED ``OPENROUTER_BASE_URL`` so a leak is seen.
    """

    proxy, openrouter = wire
    project = tmp_path / "project"
    home, agent = _hermetic_agent(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-shell-fake")
    _load_project_dotenv(monkeypatch, project, "OPENROUTER_API_KEY=or-dotenv-planted\n")
    inherited = build_child_env(_profile("anthropic/claude-haiku-4.5"))
    # What ``build_child_env`` hands the child, on a hermetic base: the two keys
    # and — when the loader wrote one — the record.
    passed = {
        name: inherited[name]
        for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "AELIX_DOTENV_ADMITTED")
        if name in inherited
    }
    env = child_env(
        home,
        AELIX_CODING_AGENT_DIR=str(agent),
        AELIX_SETTINGS_PATH=str(agent / "settings.json"),
        PI_OFFLINE="1",
        HTTPS_PROXY=proxy.url,
        https_proxy=proxy.url,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        OPENROUTER_BASE_URL=f"{openrouter.url}/api/v1",
        AELIX_STDIN_TIMEOUT="1",
        **passed,
    )
    done = await asyncio.to_thread(
        _spawn_child, ["--model", "anthropic/claude-haiku-4.5"], env, project
    )
    # The wire first: on aa026d08 the red line is what OpenRouter received.
    assert openrouter.posts == [], (openrouter.posts, done.stderr[-800:])
    assert proxy.hosts() == ["api.anthropic.com"], (proxy.connects, done.stderr[-800:])


_CHILDEXT = textwrap.dedent(
    """
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        aelix.register_provider(
            "childext",
            ProviderConfigInput(
                name="child-only ext probe",
                api_key="childext-fake-literal",
                models={{
                    "m1": Model(
                        id="m1",
                        provider="childext",
                        api="openai-completions",
                        base_url={base_url!r},
                    )
                }},
            ),
        )
    """
)


async def test_a_real_child_reaches_its_profiles_own_extension_provider(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wire: tuple[WireRecorder, WireRecorder],
) -> None:
    """The critique's C1 on the wire: the parent never loads ``childext``.

    The profile declares ``extensions: [childext.py]`` and ``model: childext/m1``;
    ``OPENROUTER_API_KEY`` is exported. In the parent, ``childext`` is an unknown
    prefix and guard 2 answers OpenRouter — pinning that answer launched the child
    with ``--provider openrouter`` and its prompt went to ``openrouter.ai``
    (``/tmp/362-work/critic/live/run_child_argv.out``). The child must be left to
    resolve it with its own extension loaded.
    """

    proxy, endpoint = wire
    project = tmp_path / "project"
    project.mkdir()
    home, agent = _hermetic_agent(tmp_path)
    ext = tmp_path / "childext.py"
    ext.write_text(_CHILDEXT.format(base_url=f"{endpoint.url}/childext/v1"), encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    registry = await _registry(scrubbed, ext=False)
    profile = AgentProfile(
        name="p",
        description="d",
        body="b",
        file_path=str(tmp_path / "p.md"),
        scope="user",
        model="childext/m1",
        extensions=(str(ext),),
    )
    (tmp_path / "p.md").write_text("b", encoding="utf-8")
    flags = profile_to_flags(profile, prompt_path=profile.file_path, model_registry=registry)
    assert "--provider" not in flags, flags
    env = child_env(
        home,
        AELIX_CODING_AGENT_DIR=str(agent),
        AELIX_SETTINGS_PATH=str(agent / "settings.json"),
        PI_OFFLINE="1",
        HTTPS_PROXY=proxy.url,
        https_proxy=proxy.url,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        OPENROUTER_API_KEY="or-shell-fake",
    )
    done = await asyncio.to_thread(_spawn_child, flags, env, project)
    assert "openrouter.ai" not in proxy.hosts(), (proxy.connects, done.stderr[-800:])
    assert ("/childext/v1/chat/completions", "m1") in endpoint.posts, done.stderr[-800:]


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
async def test_a_real_child_does_not_let_a_project_default_provider_choose_its_route(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wire: tuple[WireRecorder, WireRecorder],
    with_dotenv: bool,
) -> None:
    """Verify round 4, B1, one process down: the child resolves through ``resolve_route`` too.

    The project ships ``.aelix/settings.json`` ``{"defaultProvider": "anthropic"}``
    (and, in one half, a ``.env`` ``ANTHROPIC_API_KEY``); the user exports
    ``OPENROUTER_API_KEY``; a profile asks for the bare ``claude-haiku-4-5``.
    The parent pins nothing (a credential decision is the child's), and the
    child, started in the project, reads the same merged settings. On
    ``001ef77d`` the ``.env`` half went to ``api.anthropic.com`` (the settings
    default broke the tie before any auth check); now both halves are pi's
    ambiguity refusal and nothing leaves.
    """

    proxy, openrouter = wire
    project = tmp_path / "project"
    project.mkdir()
    (project / ".aelix").mkdir()
    (project / ".aelix" / "settings.json").write_text(
        json.dumps({"defaultProvider": "anthropic"}), encoding="utf-8"
    )
    home, agent = _hermetic_agent(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-shell-fake")
    if with_dotenv:
        _load_project_dotenv(monkeypatch, project, "ANTHROPIC_API_KEY=ant-dotenv-planted\n")
    registry = await _registry(scrubbed, ext=False)
    flags = profile_to_flags(
        _profile("claude-haiku-4-5"), prompt_path="/p.md", model_registry=registry
    )
    assert "--provider" not in flags, flags
    model_at = flags.index("--model")
    inherited = build_child_env(_profile("claude-haiku-4-5"))
    passed = {
        name: inherited[name]
        for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "AELIX_DOTENV_ADMITTED")
        if name in inherited
    }
    env = child_env(
        home,
        AELIX_CODING_AGENT_DIR=str(agent),
        AELIX_SETTINGS_PATH=str(agent / "settings.json"),
        PI_OFFLINE="1",
        HTTPS_PROXY=proxy.url,
        https_proxy=proxy.url,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        OPENROUTER_BASE_URL=f"{openrouter.url}/api/v1",
        AELIX_STDIN_TIMEOUT="1",
        **passed,
    )
    done = await asyncio.to_thread(_spawn_child, ["--model", flags[model_at + 1]], env, project)
    assert proxy.hosts() == [], (proxy.connects, done.stderr[-800:])
    assert openrouter.posts == [], (openrouter.posts, done.stderr[-800:])
    assert 'Model "claude-haiku-4-5" is ambiguous across providers' in done.stderr, done.stderr[
        -800:
    ]
    assert done.returncode != 0
