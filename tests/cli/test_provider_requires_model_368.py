"""#368 — ``--provider`` without ``--model`` is pi's usage error, in every mode.

pi 0c453048b (``main.ts:469-474`` @ b223082bb): ``--provider`` with no
``--model`` ends with "--provider requires --model (for example: --provider
<name> --model <pattern>)" and exit 1, interactive and RPC included, before
``session_start`` (``AgentSession.bindExtensions``). On ``a7435b9f`` aelix said
"model '?' (provider 'openai') could not be resolved ..." in print/json, and
the TUI and ``--mode rpc`` STARTED on an unrunnable model (rc 0 for RPC) — every
mode after loading the extensions and running their ``session_start``.

Now ``_async_main`` refuses it from argv alone. With no agent profile the
check runs before the project-trust gate — no trust question, no throwaway
vote load of the user, global and ``-e`` extensions, no MCP, no session
extension load, no ``session_start`` (verify round 1, B2) — also in a cwd
holding only ``.aelix/settings.json``, which the gate asks for since #369, and
whose file is not read there. With ``--agent`` /
``--agent-file`` it runs right after the profile overlay, so a profile's
``provider:``/``model:`` count; the trust gate (and its vote load) can precede
it there, as ADR-0250 §7 item 14 states. A settings ``defaultModel`` is not a
``--model`` and a settings ``defaultProvider`` alone is not a ``--provider``
(pi reads settings after the check); ``--provider ""`` is no provider (pi:
falsy). The one exemption is ADR-0250 §2.6's: a shell
``OPENROUTER_DEFAULT_MODEL`` with ``--provider`` naming OpenRouter is let
through WITHOUT asking for a key (``openrouter_default_named``, the argv half
``resolve_route`` step 0 also asks) — step 0 decides it after the extensions
load, so a key an extension's ``setup()`` registers still runs the model
(verify round 1, B1), and no key gets a7435b9f's text.

Drives the real ``_async_main`` (mode entries stubbed) with every HTTP request
recorded by an ``httpx.MockTransport``: fake keys, an isolated agent dir,
nothing leaves the process.
"""

from __future__ import annotations

import io
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli import test_late_provider_landing_367 as _l367
from tests.cli.test_late_provider_refused_367 import _spy_launch_model, _stub_mode, _stub_print

env = _l367.env
wire = _l367.wire
attached = _l367.attached


def _line(provider: str) -> str:
    return f"Error: --provider requires --model (for example: --provider {provider} --model <id>)"


_UNRESOLVED = "Error: model '?' (provider 'openai') could not be resolved"

_MARKER_EXTENSION = textwrap.dedent(
    """
    import os

    def setup(aelix):
        with open(os.environ["MARKER_368"], "a") as f:
            f.write("setup\\n")

        async def _on_start(event, ctx):
            with open(os.environ["MARKER_368"], "a") as f:
                f.write("session_start\\n")

        aelix.on("session_start", _on_start)
    """
)


@pytest.fixture
def marker(env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ``-e`` extension that records its ``setup()`` and its ``session_start``."""

    log = env / "marker.log"
    monkeypatch.setenv("MARKER_368", str(log))
    (env / "marker.py").write_text(_MARKER_EXTENSION, encoding="utf-8")
    return log


def _marks(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def _settings(env: Path, settings: dict[str, str]) -> None:
    (env / "agent" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _profile(env: Path, name: str, front: str) -> Path:
    path = env / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: probe\n{front}---\nprobe\n", encoding="utf-8")
    return path


# === every mode ================================================================


@pytest.mark.parametrize("mode", ["print", "json"])
async def test_print_and_json_say_pis_line(
    env: Path,
    wire: Any,
    marker: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """a7435b9f: rc 1 with "model '?' (provider 'openai') could not be resolved"
    after ``setup()`` and ``session_start`` ran."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    turns = _stub_print(monkeypatch)
    launched = _spy_launch_model(monkeypatch)
    flags = ["-p", "hi"] if mode == "print" else ["--mode", "json", "hi"]
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "marker.py"), "--provider", "openai", *flags]
    )
    out, err = capsys.readouterr()
    assert (code, turns, launched, list(wire), out) == (1, [], [], [], "")
    assert _line("openai") in err
    assert "could not be resolved" not in err
    assert _marks(marker) == []


@pytest.mark.parametrize("mode", ["interactive", "rpc"])
async def test_interactive_and_rpc_exit_before_starting(
    env: Path,
    wire: Any,
    marker: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """a7435b9f: the TUI started with a Warning and RPC answered ``get_state``
    with model id '' (rc 0), both after ``session_start``. Now exit 1 before
    either mode starts — pi's diagnostics exit, not ADR-0250 §2.4's hold."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    handed, flags = _stub_mode(monkeypatch, mode)
    launched = _spy_launch_model(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "marker.py"), "--provider", "openai", *flags]
    )
    err = capsys.readouterr().err
    assert (code, handed, launched, list(wire)) == (1, [], [], [])
    assert _line("openai") in err
    assert "Warning:" not in err
    assert _marks(marker) == []


async def test_no_extension_loads_and_session_start_never_fires(
    env: Path,
    wire: Any,
    marker: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same extension DOES load and start on the valid pair (the control),
    so the empty marker above is the check's doing, not a dead fixture."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        [
            "--no-session",
            "-e",
            str(env / "marker.py"),
            "--provider",
            "openai",
            "--model",
            "gpt-4o-mini",
            "-p",
            "hi",
        ]
    )
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert _marks(marker) == ["setup", "session_start"]
    assert _line("openai") not in capsys.readouterr().err


# === what counts as --provider / --model =======================================


async def test_the_provider_is_echoed_as_typed(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--provider", "OpenAI", "-p", "hi"])
    assert (code, turns) == (1, [])
    assert _line("OpenAI") in capsys.readouterr().err


async def test_a_settings_default_model_is_not_a_model_flag(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """pi consults settings after the check (``main.ts:497-500``)."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _settings(env, {"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"})
    turns = _stub_print(monkeypatch)
    launched = _spy_launch_model(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--provider", "openai", "-p", "hi"])
    assert (code, turns, launched) == (1, [], [])
    assert _line("openai") in capsys.readouterr().err


async def test_a_settings_default_provider_alone_keeps_its_message(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No flag was typed, so this is not pi's usage error: unchanged."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _settings(env, {"defaultProvider": "openai"})
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, turns) == (1, [])
    assert _UNRESOLVED in err
    assert "--provider requires --model" not in err


async def test_the_settings_pair_alone_still_launches(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _settings(env, {"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"})
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "-p", "hi"])
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert "--provider requires --model" not in capsys.readouterr().err


async def test_the_pair_is_unchanged(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--provider", "openai", "--model", "gpt-4o-mini", "-p", "hi"]
    )
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert "--provider requires --model" not in capsys.readouterr().err


# === agent profiles ============================================================


async def test_a_provider_only_profile_gets_the_same_line(
    env: Path,
    wire: Any,
    marker: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The profile overlay feeds ``parsed.provider``; a7435b9f printed the
    "could not be resolved" line after loading the extensions."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    path = _profile(env, "provonly", "provider: openai\n")
    turns = _stub_print(monkeypatch)
    launched = _spy_launch_model(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "marker.py"), "--agent-file", str(path), "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns, launched, list(wire)) == (1, [], [], [])
    assert "Agent profile: provonly" in err
    assert err.index("Agent profile: provonly") < err.index(_line("openai"))
    assert _marks(marker) == []


async def test_the_profile_and_its_delegated_child_agree(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``agents/resolver.py::child_model_flags`` hands a provider-only profile's
    child ``--provider`` alone; that child now says the same line the in-process
    overlay does (the resolver's anti-drift rule: both channels agree)."""

    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.agents.resolver import child_model_flags

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    profile = AgentProfile(
        name="provonly",
        description="d",
        body="b",
        file_path="/p.md",
        scope="user",
        provider="openai",
    )
    flags = child_model_flags(profile, None, None)
    assert flags == ["--provider", "openai"]
    _stub_print(monkeypatch)
    child = await entry_mod._async_main(["--no-session", *flags, "-p", "hi"])
    child_err = capsys.readouterr().err
    path = _profile(env, "provonly", "provider: openai\n")
    parent = await entry_mod._async_main(["--no-session", "--agent-file", str(path), "-p", "hi"])
    parent_err = capsys.readouterr().err
    assert (child, parent) == (1, 1)
    assert _line("openai") in child_err
    assert _line("openai") in parent_err


async def test_a_profile_model_with_a_typed_provider_launches(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The profile's ``model:`` counts as ``--model``."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    path = _profile(env, "withmodel", "model: gpt-4o-mini\n")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--agent-file", str(path), "--provider", "openai", "-p", "hi"]
    )
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert "--provider requires --model" not in capsys.readouterr().err


# === OPENROUTER_DEFAULT_MODEL (ADR-0250 §2.6) ==================================


@pytest.mark.parametrize(
    "provider", [["--provider", "openrouter"], ["--provider", "OpenRouter"], []]
)
async def test_the_openrouter_default_model_rung_still_picks_the_model(
    env: Path,
    wire: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: list[str],
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-368")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "openrouter/auto")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", *provider, "-p", "hi"])
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openrouter", "openrouter/auto")]
    assert "--provider requires --model" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("environ", "provider"),
    [
        ({"OPENROUTER_API_KEY": "or-fake-368"}, "openrouter"),
        (
            {"OPENROUTER_API_KEY": "or-fake-368", "OPENROUTER_DEFAULT_MODEL": "openrouter/auto"},
            "openai",
        ),
        ({"OPENROUTER_API_KEY": "or-fake-368", "OPENROUTER_DEFAULT_MODEL": ""}, "openrouter"),
    ],
    ids=["no-default-model", "another-provider", "empty-default-model"],
)
async def test_outside_the_rung_the_line_applies(
    env: Path,
    wire: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environ: dict[str, str],
    provider: str,
) -> None:
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--provider", provider, "-p", "hi"])
    assert (code, turns, list(wire)) == (1, [], [])
    assert _line(provider) in capsys.readouterr().err


async def test_the_rung_with_no_key_is_left_to_step_zero(
    env: Path, wire: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check asks no credential (verify round 1, B1), so ``--provider
    openrouter`` with the variable and no key is let through and step 0 refuses
    it with a7435b9f's text, unchanged."""

    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "openrouter/auto")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", "--provider", "openrouter", "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, turns, list(wire)) == (1, [], [])
    assert "Error: model '?' (provider 'openrouter') could not be resolved" in err
    assert "--provider requires --model" not in err


_OPENROUTER_KEY_EXTENSION = textwrap.dedent(
    """
    from aelix_coding_agent.model_registry import ProviderConfigInput

    def setup(aelix):
        aelix.register_provider("openrouter", ProviderConfigInput(api_key="or-ext-fake-368"))
    """
)


@pytest.mark.parametrize("provider", ["openrouter", "OPENROUTER"])
async def test_an_openrouter_key_an_extension_registers_still_runs_the_rung(
    env: Path,
    wire: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
) -> None:
    """Verify round 1, B1: on a7435b9f this ran ``openrouter/auto``; da112cdd
    asked for the key before any extension loaded and exited 1, while the same
    launch with no ``--provider`` still ran (step 0 decides after the
    extensions load). The check now reads argv only."""

    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "openrouter/auto")
    (env / "orkey.py").write_text(_OPENROUTER_KEY_EXTENSION, encoding="utf-8")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--provider", provider, "-e", str(env / "orkey.py"), "-p", "hi"]
    )
    assert code == 0
    assert [(m.provider, m.id) for m in turns] == [("openrouter", "openrouter/auto")]
    assert "--provider requires --model" not in capsys.readouterr().err


def test_the_check_and_step_zero_share_one_definition(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One definition of "names the rung": step 0 asks it (plus a key), and so
    does the check (with no key)."""

    from aelix_coding_agent.cli import runtime_bootstrap as rb
    from aelix_coding_agent.cli.args import parse_args

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-368")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "openrouter/auto")
    assert rb.resolve_route(None, "openrouter").kind == "openrouter_default"
    assert (
        entry_mod._provider_requires_model(parse_args(["--provider", "openrouter"]), None) is None
    )
    monkeypatch.setattr(rb, "openrouter_default_named", lambda *a, **k: False)
    assert rb.resolve_route(None, "openrouter").kind == "none"
    assert entry_mod._provider_requires_model(
        parse_args(["--provider", "openrouter"]), None
    ) == _line("openrouter")


# === before the trust gate, MCP and the vote load (verify round 1, B2) ========


def _trust_resources(env: Path, kind: str) -> None:
    """Trust-requiring ``.aelix/`` resources in the cwd (no ``--approve``).

    ``settings``: a ``.aelix/settings.json`` and nothing else — a resource the
    trust gate asks for since #369 (ADR-0252). ``both``: ``mcp.json`` and an
    ``extensions/`` entry together."""

    aelix = env / "cwd" / ".aelix"
    aelix.mkdir(exist_ok=True)
    if kind == "settings":
        (aelix / "settings.json").write_text(
            json.dumps({"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}),
            encoding="utf-8",
        )
    if kind in ("mcp", "both"):
        (aelix / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    if kind in ("extension", "both"):
        (aelix / "extensions").mkdir()
        (aelix / "extensions" / "proj.py").write_text(
            "def setup(aelix):\n    raise SystemExit('project extension ran')\n",
            encoding="utf-8",
        )


# Every read-``open`` of a path under a watched ``.aelix`` dir, while one is
# watched (the test's own writes of the fixtures are not reads). An audit hook
# cannot be removed, so it is installed once and does nothing while
# ``_WATCHED`` is empty.
_WATCHED: list[str] = []
_OPENED: list[str] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if not _WATCHED or event != "open" or not isinstance(args[0], (str, Path)):
        return
    mode, flags = args[1], args[2]
    reading = ("r" in mode and "+" not in mode) if mode else (flags & 3) == os.O_RDONLY
    if reading and any(str(args[0]).startswith(w) for w in _WATCHED):
        _OPENED.append(str(args[0]))


sys.addaudithook(_audit)


@pytest.fixture
def project_reads(env: Path) -> Any:
    """What startup opened under the cwd's ``.aelix/``, and the trust value
    ``SettingsManager.set_project_trusted`` was given (#369: the project
    ``settings.json`` is read only after that call says trusted)."""

    from aelix_ai.settings import SettingsManager

    told: list[bool] = []
    real = SettingsManager.set_project_trusted

    def _spy(self: Any, trusted: bool) -> Any:
        told.append(trusted)
        return real(self, trusted)

    _OPENED.clear()
    _WATCHED.append(str((env / "cwd" / ".aelix").resolve()))
    _WATCHED.append(str(env / "cwd" / ".aelix"))
    mp = pytest.MonkeyPatch()
    mp.setattr(SettingsManager, "set_project_trusted", _spy)
    try:
        yield told, _OPENED
    finally:
        mp.undo()
        _WATCHED.clear()


def _spy_trust(monkeypatch: pytest.MonkeyPatch, answer: bool = False) -> list[str]:
    """The trust gate's calls (each one is the interactive trust question)."""

    asked: list[str] = []

    async def _spy(parsed: Any, cwd: str, app_mode: str, **kwargs: Any) -> bool:
        asked.append(app_mode)
        return answer

    monkeypatch.setattr(entry_mod, "_resolve_project_trust", _spy)
    return asked


@pytest.mark.parametrize("kind", ["mcp", "extension", "both", "settings"])
@pytest.mark.parametrize("mode", ["print", "interactive", "rpc"])
async def test_no_trust_question_and_no_vote_load_before_the_line(
    env: Path,
    wire: Any,
    marker: Path,
    project_reads: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    mode: str,
) -> None:
    """Verify round 1, B2: da112cdd checked after the trust gate, so in a cwd
    with ``.aelix/`` resources the throwaway vote load ran the ``-e``
    extension's ``setup()`` first (and interactive asked the trust question).
    With no agent profile the check now comes before both. ``settings``: a
    cwd holding only ``.aelix/settings.json``, which the gate asks for since
    #369 — the line comes first there too, and the project settings are
    neither handed a trust answer nor opened."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _trust_resources(env, kind)
    asked = _spy_trust(monkeypatch)
    if mode == "print":
        handed: list[Any] = _stub_print(monkeypatch)
        flags = ["-p", "hi"]
    else:
        handed, flags = _stub_mode(monkeypatch, mode)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "marker.py"), "--provider", "openai", *flags]
    )
    err = capsys.readouterr().err
    assert (code, handed, asked, list(wire)) == (1, [], [], [])
    assert _line("openai") in err
    assert _marks(marker) == []
    told, opened = project_reads
    assert (told, opened) == ([], [])


@pytest.mark.parametrize("trusted", [False, True], ids=["untrusted", "trusted"])
async def test_a_settings_only_cwd_asks_for_trust_on_the_pair(
    env: Path,
    wire: Any,
    marker: Path,
    project_reads: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    trusted: bool,
) -> None:
    """The control for the ``settings`` rows above: the same cwd (only
    ``.aelix/settings.json``) DOES reach the trust gate (#369) when the command
    is well formed — the vote load runs the ``-e`` ``setup()`` and the gate is
    asked. Untrusted, the file stays unopened; trusted, it is read — which is
    what shows the read probe sees a read."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _trust_resources(env, "settings")
    asked = _spy_trust(monkeypatch, trusted)
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        [
            "--no-session",
            "-e",
            str(env / "marker.py"),
            "--provider",
            "openai",
            "--model",
            "gpt-4o-mini",
            "-p",
            "hi",
        ]
    )
    err = capsys.readouterr().err
    assert (code, asked) == (0, ["print"])
    assert [(m.provider, m.id) for m in turns] == [("openai", "gpt-4o-mini")]
    assert "--provider requires --model" not in err
    assert _marks(marker)[:1] == ["setup"]
    told, opened = project_reads
    assert told == [trusted]
    assert [Path(o).name for o in opened] == (["settings.json"] if trusted else [])


@pytest.mark.parametrize("kind", ["mcp", "settings"])
async def test_with_a_profile_the_trust_gate_comes_first_as_documented(
    env: Path,
    wire: Any,
    marker: Path,
    project_reads: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    """ADR-0250 §7 item 14: a profile's ``provider:`` is known only after the
    overlay, which needs the trust answer — so the trust gate (and its vote
    load: the ``-e`` ``setup()`` once) runs first. Nothing after it does: no
    session extension load, no ``session_start``, no request. ``settings``: a
    cwd holding only ``.aelix/settings.json`` is such a directory since #369;
    untrusted, the file stays unopened."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _trust_resources(env, kind)
    asked = _spy_trust(monkeypatch)
    path = _profile(env, "provonly", "provider: openai\n")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "-e", str(env / "marker.py"), "--agent-file", str(path), "-p", "hi"]
    )
    err = capsys.readouterr().err
    assert (code, turns, asked, list(wire)) == (1, [], ["print"], [])
    assert _line("openai") in err
    assert _marks(marker) == ["setup"]
    told, opened = project_reads
    assert (told, opened) == ([False], [])


_MCP_MARKER = "import sys\nopen(sys.argv[1], 'a').write('mcp spawned\\n')\n"


@pytest.fixture
def mcp_marker(env: Path) -> Path:
    """An agent-dir ``mcp.json`` whose stdio server records that it was spawned."""

    import sys

    log = env / "mcp.log"
    (env / "mcp_marker.py").write_text(_MCP_MARKER, encoding="utf-8")
    server = {"command": sys.executable, "args": [str(env / "mcp_marker.py"), str(log)]}
    (env / "agent" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"probe": server}}), encoding="utf-8"
    )
    return log


@pytest.mark.parametrize(
    "launch",
    [
        ["--provider", "openai"],
        ["--agent-file", "PROFILE"],
    ],
    ids=["typed", "profile"],
)
async def test_no_mcp_server_is_spawned(
    env: Path,
    wire: Any,
    mcp_marker: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    launch: list[str],
) -> None:
    """The check returns before MCP connects (a decision taken early but acted
    on after ``connect_all`` would spawn the server first)."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    path = _profile(env, "provonly", "provider: openai\n")
    turns = _stub_print(monkeypatch)
    argv = [str(path) if a == "PROFILE" else a for a in launch]
    code = await entry_mod._async_main(["--no-session", *argv, "-p", "hi"])
    assert (code, turns, list(wire)) == (1, [], [])
    assert _line("openai") in capsys.readouterr().err
    assert not mcp_marker.exists()


async def test_the_mcp_marker_is_live_on_the_pair(
    env: Path, wire: Any, mcp_marker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the same ``mcp.json`` IS spawned on a runnable launch."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--provider", "openai", "--model", "gpt-4o-mini", "-p", "hi"]
    )
    assert code == 0
    assert mcp_marker.read_text(encoding="utf-8").split() == ["mcp", "spawned"]


@pytest.mark.parametrize("argv", [["--provider", ""], ["--provider="]], ids=["space", "equals"])
async def test_an_empty_provider_is_no_provider(
    env: Path,
    wire: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """pi treats ``--provider ""`` as falsy (no check); aelix keeps its
    a7435b9f text, "No model selected."."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-368")
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(["--no-session", *argv, "-p", "hi"])
    err = capsys.readouterr().err
    assert (code, turns, list(wire)) == (1, [], [])
    assert "No model selected." in err
    assert "--provider requires --model" not in err


# === the exits that come first, and --api-key =================================


async def test_help_and_list_models_still_exit_0(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await entry_mod._async_main(["--provider", "openai", "--help"]) == 0
    assert "--provider requires --model" not in capsys.readouterr().err
    assert (
        await entry_mod._async_main(["--provider", "openai", "--list-models", "gpt-4o-mini"]) == 0
    )
    assert "--provider requires --model" not in capsys.readouterr().err


async def test_api_key_gets_the_line_and_is_attached_nowhere(
    env: Path,
    wire: Any,
    attached: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    turns = _stub_print(monkeypatch)
    code = await entry_mod._async_main(
        ["--no-session", "--provider", "openai", "--api-key", "sk-fake-typed", "-p", "hi"]
    )
    assert (code, turns, attached, list(wire)) == (1, [], [], [])
    assert _line("openai") in capsys.readouterr().err


def test_help_says_provider_requires_model() -> None:
    from aelix_coding_agent.cli.args import print_help

    out = io.StringIO()
    print_help(out)
    assert (
        "  --provider <id>       Provider to search for --model (requires --model)\n"
        in out.getvalue()
    )
