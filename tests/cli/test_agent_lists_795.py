"""#795, #796 (ADR-0304 D3, D9, D13, D14) — how ``cli/entry.py`` wires the applied lists.

* the ``agents`` lists are read at launch into one holder, re-read by a
  ``/reload`` build, put back when that build raises, and not re-read by
  ``/new``;
* ``AgentsExtension`` gets both live getters — the lists and the identity — in
  ``-p``, ``--mode json``, ``--mode rpc`` and the TUI;
* the identity getter answers the launch identity before ``agent_service``
  exists, and the service's ``active`` after (a ``/agents use`` moves it, a
  failed one leaves it);
* ADR-0304 D13's warnings print once at launch, at depth 0 only;
* ``build_agent_control`` runs in every mode.

Drives the real ``_async_main`` with the mode entries stubbed, in
``test_default_thinking_every_mode_286``'s sandbox (no key leaves; no network).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.entry import _identity_of
from aelix_coding_agent.core.agent_selection import AgentControl
from aelix_coding_agent.subagent_contract import DEPTH_ENV_VAR

from tests.cli import test_default_thinking_every_mode_286 as _base
from tests.cli.test_default_thinking_every_mode_286 import (
    _LAUNCH,
    _MODES,
    _launch,
    _settings,
    _stub_modes,
)

# That file's sandbox fixtures, under the same names (a bare import would read
# as a redefinition wherever a test takes them).
env = _base.env
mode = _base.mode


def _profile(env: Path, name: str, extra: str = "") -> None:
    folder = env / "agent" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {name} agent\n{extra}---\n\nDo it.\n", encoding="utf-8"
    )


def _spy_extension(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    import aelix_agents
    from aelix_agents import AgentsExtension

    made: list[Any] = []

    def _make(*args: Any, **kwargs: Any) -> Any:
        ext = AgentsExtension(*args, **kwargs)
        made.append(ext)
        return ext

    monkeypatch.setattr(aelix_agents, "AgentsExtension", _make)
    return made


def _spy_control(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    built: list[Any] = []
    real = entry_mod.build_agent_control

    def _build(*args: Any, **kwargs: Any) -> Any:
        control = real(*args, **kwargs)
        built.append(control)
        return control

    monkeypatch.setattr(entry_mod, "build_agent_control", _build)
    return built


async def test_every_mode_hands_the_extension_both_getters_and_builds_the_control(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, agents=["-general-purpose"])
    _profile(env, "orchestrator", "agents: [explorer]\n")
    made = _spy_extension(monkeypatch)
    built = _spy_control(monkeypatch)
    seen = _stub_modes(monkeypatch)
    code = await _launch(env, *_MODES[mode], *_LAUNCH, "--agents", "--agent", "orchestrator")
    assert code == 0, capsys.readouterr().err
    (ext,) = made
    lists = ext.agent_lists()
    assert lists.global_list.entries == ("-general-purpose",)
    identity = ext.active_profile()
    assert identity is not None and identity.name == "orchestrator"
    assert identity.agents == ("explorer",)
    assert len(built) == 1 and isinstance(built[0], AgentControl)
    view = ext.runtime.delegation() if ext.runtime is not None else None
    assert view is not None
    assert [p.name for p in view.available] == ["explorer"]
    assert seen.get("runtime") is not None


async def test_delegation_off_builds_no_extension_whatever_the_list_says(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, agents=["+reviewer"], features={"agents": False})
    _profile(env, "reviewer")
    made = _spy_extension(monkeypatch)
    seen = _stub_modes(monkeypatch)
    assert await _launch(env, *_MODES[mode], *_LAUNCH) == 0
    assert made == []
    assert "agent" not in [t.name for t in seen["runtime"].harness.state.tools]


async def test_the_identity_getter_answers_the_launch_identity_before_the_service_exists(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Contract §10 / verifier V83: ``AgentsExtension`` holds the getter before
    ``AgentProfileService`` is built; read then, it answers the ``--agent`` identity."""

    from aelix_coding_agent.agents import service as service_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _profile(env, "orchestrator", "agents: [explorer]\n")
    made = _spy_extension(monkeypatch)
    early: list[Any] = []
    real = service_mod.AgentProfileService

    def _service(*args: Any, **kwargs: Any) -> Any:
        early.append(made[0].active_profile() if made else "no extension yet")
        return real(*args, **kwargs)

    monkeypatch.setattr(service_mod, "AgentProfileService", _service)
    _stub_modes(monkeypatch)
    code = await _launch(env, "-p", "hi", *_LAUNCH, "--agents", "--agent", "orchestrator")
    assert code == 0, capsys.readouterr().err
    assert early, "the service was never built"
    assert getattr(early[0], "name", early[0]) == "orchestrator"


def test_the_identity_box_answers_the_launch_then_the_service() -> None:
    launch = object()
    box: dict[str, Any] = {"launch": launch, "service": None}
    assert _identity_of(box) is launch
    service = type("S", (), {"active": None})()
    box["service"] = service
    assert _identity_of(box) is None
    service.active = "other"
    assert _identity_of(box) == "other"


async def test_the_holder_is_reread_by_reload_put_back_on_a_failed_build_and_kept_by_new(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, agents=["-general-purpose"])
    made = _spy_extension(monkeypatch)
    entries: list[tuple[str, ...]] = []

    async def drive(runtime: Any) -> None:
        ext = made[0]
        entries.append(ext.agent_lists().global_list.entries)
        _settings(env, agents=["-explorer"])
        await runtime.new_session()
        entries.append(ext.agent_lists().global_list.entries)
        await runtime.reload()
        entries.append(ext.agent_lists().global_list.entries)
        _settings(env, agents=["-reviewer"])
        real = entry_mod._build_harness_options

        async def _fails(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("the build fails")

        monkeypatch.setattr(entry_mod, "_build_harness_options", _fails)
        with contextlib.suppress(Exception):  # the failed reload is the point
            await runtime.reload()
        monkeypatch.setattr(entry_mod, "_build_harness_options", real)
        entries.append(ext.agent_lists().global_list.entries)

    _stub_modes(monkeypatch, drive)
    code = await _launch(env, "-p", "hi", *_LAUNCH, "--agents")
    assert code == 0, capsys.readouterr().err
    assert entries == [
        ("-general-purpose",),
        ("-general-purpose",),  # /new does not re-read
        ("-explorer",),  # /reload does
        ("-explorer",),  # a failed reload build is put back
    ]


async def test_agents_use_moves_the_getter_and_a_failed_use_leaves_it(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from tests.cli.test_launch_route_344 import _FakeTTYStdin

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _profile(env, "orchestrator", "agents: [explorer]\n")
    _profile(env, "solo", "agents: []\n")
    _profile(env, "broken", "tools: [no-such-tool]\n")
    made = _spy_extension(monkeypatch)
    names: list[Any] = []
    failures: list[str] = []
    import aelix_coding_agent.tui as tui_pkg

    async def _tui(runtime: Any, **kwargs: Any) -> int:
        ext = made[0]
        service = kwargs["agent_service"]
        names.append(ext.active_profile().name)
        await service.use("solo", harness=runtime.harness)
        names.append(ext.active_profile().name)
        view = ext.runtime.delegation()
        names.append(tuple(p.name for p in view.available))
        try:
            await service.use("broken", harness=runtime.harness)
        except Exception as exc:  # noqa: BLE001 — the failed use is the point
            failures.append(str(exc))
        names.append(ext.active_profile().name)
        assert isinstance(kwargs["agent_control"], AgentControl)
        return 0

    monkeypatch.setattr(tui_pkg, "run_tui", _tui)
    code = await _launch(env, *_LAUNCH, "--agents", "--agent", "orchestrator")
    assert code == 0, capsys.readouterr().err
    assert failures, "the broken profile's use must fail"
    assert names == ["orchestrator", "solo", (), "solo"]


async def test_the_launch_warnings_print_once_at_depth_zero(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, agents=["explorer", "+nobody"])
    _profile(env, "orchestrator", "agents: [explorer, reviewr]\n")

    async def drive(runtime: Any) -> None:
        await runtime.reload()

    _stub_modes(monkeypatch, drive)
    assert await _launch(env, "-p", "hi", *_LAUNCH, "--agents", "--agent", "orchestrator") == 0
    err = capsys.readouterr().err
    plain = (
        '"agents" entry "explorer" would add a profile directory, which is not supported; it '
        'is ignored; to turn the profile \'explorer\' on or off, write "+explorer" or "-explorer"'
    )
    assert err.count(plain) == 1
    assert err.count('"agents" entry "+nobody" matches no agent profile') == 1
    assert (
        err.count(
            "Warning: agent profile 'orchestrator': agents: names 'reviewr', which no agent "
            "profile found here has"
        )
        == 1
    )
    # The profile's own prefix, not a settings file's (ADR-0304 D13).
    assert "Warning: settings: agent profile" not in err
    # And a settings file's problem keeps ITS prefix, ADR-0279 D8's (verify r2 F3, D17).
    settings_lines = [line for line in err.splitlines() if '"agents" entry' in line]
    assert len(settings_lines) == 2
    assert all(line.startswith("Warning: settings: ") for line in settings_lines), settings_lines


async def test_an_unknown_name_in_the_identity_list_is_warned_with_no_settings_entry(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verifier V87 and V79: the identity's list alone takes the discovery that
    finds the unknown name; a delegated child (depth 1) says nothing."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _profile(env, "orchestrator", "agents: [explorer, reviewr]\n")
    _stub_modes(monkeypatch)
    line = (
        "Warning: agent profile 'orchestrator': agents: names 'reviewr', which no agent "
        "profile found here has"
    )
    assert await _launch(env, "-p", "hi", *_LAUNCH, "--agents", "--agent", "orchestrator") == 0
    err = capsys.readouterr().err
    assert '"agents" entry' not in err  # no settings entry at all
    assert err.count(line) == 1, err
    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    assert await _launch(env, "-p", "hi", *_LAUNCH, "--agent", "orchestrator") == 0
    child = capsys.readouterr().err
    assert "Agent profile: orchestrator" in child  # the identity was taken
    assert "names 'reviewr'" not in child


async def test_agent_and_agents_use_take_a_profile_that_is_off(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0304 D6 / D11 (owner: "위임에서만 막기"): ``--agent`` and ``/agents use`` are
    human doors; an off profile becomes the identity, while the model's view reads
    it ``off``."""

    import sys

    import aelix_coding_agent.tui as tui_pkg

    from tests.cli.test_launch_route_344 import _FakeTTYStdin

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, agents=["-general-purpose", "-explorer"])
    made = _spy_extension(monkeypatch)
    seen: list[tuple[str, str]] = []

    async def _tui(runtime: Any, **kwargs: Any) -> int:
        ext = made[0]
        service = kwargs["agent_service"]
        view = ext.runtime.delegation()
        seen.append((ext.active_profile().name, view.verdict("general-purpose")))
        await service.use("explorer", harness=runtime.harness)
        view = ext.runtime.delegation()
        seen.append((ext.active_profile().name, view.verdict("explorer")))
        return 0

    monkeypatch.setattr(tui_pkg, "run_tui", _tui)
    code = await _launch(env, *_LAUNCH, "--agents", "--agent", "general-purpose")
    assert code == 0, capsys.readouterr().err
    assert seen == [("general-purpose", "off"), ("explorer", "off")]


async def test_no_launch_warnings_in_a_delegated_child(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    _settings(env, agents=["explorer", "+nobody"])
    made = _spy_extension(monkeypatch)
    _stub_modes(monkeypatch)
    assert await _launch(env, "-p", "hi", *_LAUNCH) == 0
    err = capsys.readouterr().err
    assert '"agents" entry' not in err
    assert made == []
    from aelix_ai.settings import SettingsManager

    manager = SettingsManager.create(Path.cwd(), env / "agent", project_trusted=True)
    assert manager.get_global_settings().agents == ["explorer", "+nobody"]
    assert entry_mod._agent_lists(manager, str(Path.cwd()), True).global_list is None
    monkeypatch.setenv(DEPTH_ENV_VAR, "0")
    depth_zero = entry_mod._agent_lists(manager, str(Path.cwd()), True)
    assert depth_zero.global_list is not None
    assert depth_zero.global_list.entries == ("explorer", "+nobody")


async def test_a_project_file_that_did_not_load_is_named_and_applies_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    project = Path.cwd() / ".aelix"
    project.mkdir()
    (project / "settings.json").write_text('{"agents": ["-explorer"], oops', encoding="utf-8")
    made = _spy_extension(monkeypatch)
    _stub_modes(monkeypatch)
    assert await _launch(env, "-p", "hi", *_LAUNCH, "--agents", "--approve") == 0
    err = capsys.readouterr().err
    line = (
        f'{project / "settings.json"} did not load, so its "agents" list is not applied: every '
        "agent profile it turns off is on this run (fix the file, then /reload)"
    )
    assert err.count(line) == 1, err
    view = made[0].runtime.delegation()
    assert "explorer" in [p.name for p in view.available]
