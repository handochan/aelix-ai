"""#611 S3d (#778): ``backgroundJobs.enabled: false`` in the GLOBAL settings turns background jobs off.

The owner chose a global setting on 2026-11-12 (OQ-S3-4, "전역 설정 (권장)");
the recommended option's details — the key, global scope only, read once at
start — are contract r1 §8's. ``jobs/activation.py`` ``enabled_at_start`` reads
it as soon as the CLI's settings exist, before any extension loads (and in
``aelix status`` before its load), and ``is_active()`` answers from it for the
rest of the process, the service condition included.

Each case drives the product: the real ``_async_main`` for ``-p`` and for the
TUI dispatch (``run_tui`` stubbed to record what it is handed), a scripted
OpenAI-compatible stream behind httpx's mock transport that records what the
model was offered, and, for the delegated child, the real CLI through
``tests/jobs/gated_cli.py`` (#777 PR c0) against a local SSE server. A launch
with the setting false is compared with one with the gate off, field by field.

Measured with the product reverted and these tests as they are. On ``52bc607a``
the CLI reads no ``enabled``: a switched-off launch builds the service and
offers ``job``, ``spawn`` and background ``bash``, every extension ``setup()``
sees the gate on, no warning is printed, ``get_background_jobs_settings`` has no
``enabled``, ``/settings`` has no row and the child is gated. The cases that
call ``enabled_at_start`` directly (the extension probe, ``never_read``,
project scope, read once, the row kept when switched off) fail there for want
of it. Three are guards and pass there:
``test_with_the_setting_absent_or_true_jobs_are_on_as_before``,
``test_with_the_gate_off_there_is_no_row`` and the child test's
``setting-absent`` case; ``test_with_the_gate_off_the_setting_is_never_read``
guards the new function only. On ``639dcdf6`` (#778 fix round 1) six fail: the
trust-vote load, ``aelix status``, the concurrent write and the three non-object
values; ``test_the_settings_keep_their_other_keys`` passes there (it pins
verify's mutant S2).
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness import core as harness_core
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.extensions.loader import load_extension_from_factory
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs import service as service_mod
from aelix_coding_agent.tui.settings_rows import apply_setting, build_settings_rows

from tests.cli.test_entry_router import _FakeTTYStdin
from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env is a fixture
    _write_models,
    env,
)
from tests.jobs.test_gated_cli_611 import (
    CHILD_ANSWER,
    PROFILE,
    REAL_CHILD,
    ModelServer,
    Scratch,
    agent_actions,
    all_text,
    call,
    offers_background_bash,
    say,
    tools,
)

_OFF = {"backgroundJobs": {"enabled": False}}
_WARNING = "settings backgroundJobs.enabled must be true or false; using true"


class _Launch:
    """Spies on one in-process launch: what the CLI built, and what the model and the TUI were handed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, env: Path) -> None:  # noqa: F811
        import aelix_coding_agent.tui as tui_pkg
        import httpx

        self.env = env
        self.bodies: list[dict[str, Any]] = []
        self.services = 0
        self.inbox: list[bool] = []
        self.tui: list[dict[str, Any]] = []
        launch = self

        def answer(request: httpx.Request) -> httpx.Response:
            launch.bodies.append(json.loads(request.content or b"{}"))
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            for chunk in chunks:
                chunk.update(id="p", object="chat.completion.chunk")
            data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data.encode())

        real_client = httpx.AsyncClient.__init__

        def client(self: Any, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(answer)
            kwargs["mounts"] = {}
            kwargs["trust_env"] = False
            kwargs.pop("proxy", None)
            real_client(self, *args, **kwargs)

        real_service = service_mod.JobService.__init__

        def service(self: Any, *args: Any, **kwargs: Any) -> None:
            launch.services += 1
            real_service(self, *args, **kwargs)

        real_harness = harness_core.AgentHarness.__init__

        def harness(self: Any, options: Any, *args: Any, **kwargs: Any) -> None:
            launch.inbox.append(options.notice_inbox)
            real_harness(self, options, *args, **kwargs)

        async def run_tui(runtime: Any, **kwargs: Any) -> int:
            launch.tui.append(kwargs)
            return 0

        monkeypatch.setattr(httpx.AsyncClient, "__init__", client)
        monkeypatch.setattr(service_mod.JobService, "__init__", service)
        monkeypatch.setattr(harness_core.AgentHarness, "__init__", harness)
        monkeypatch.setattr(tui_pkg, "run_tui", run_tui)
        _write_models(env, "rec")

    def settings(self, value: dict[str, Any] | None) -> None:
        path = self.env / "agent" / "settings.json"
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(value), encoding="utf-8")

    async def run(self, *argv: str) -> dict[str, Any]:
        """One launch; what it built and offered, as plain values to compare."""

        self.bodies.clear()
        self.inbox.clear()
        self.tui.clear()
        self.services = 0
        try:
            code = await entry_mod._async_main(["--agents", "--model", "rec/m1", *argv])
            seen: dict[str, Any] = {
                "code": code,
                "services": self.services,
                "inbox": list(self.inbox),
                "active": activation.is_active(),
            }
        finally:
            activation.install_service(None)
        if self.bodies:
            first = self.bodies[0]
            seen.update(
                tools=sorted(tools(first)),
                background_bash=offers_background_bash(first),
                agent_actions=agent_actions(first),
            )
        if self.tui:
            seen["tui_jobs"] = "jobs" in self.tui[0]
        return seen


@pytest.fixture
def launch(env: Path, monkeypatch: pytest.MonkeyPatch) -> _Launch:  # noqa: F811
    return _Launch(monkeypatch, env)


def _gate(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(activation, "ACTIVE", on)
    # ``raising=False`` so the guards run on a tree without the switch (52bc607a).
    monkeypatch.setattr(activation, "_switched_off", False, raising=False)


async def test_switched_off_a_print_run_is_the_gate_off_run(
    launch: _Launch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No service, no inbox, and the model offered exactly what the gate off offers: no ``job``,
    no ``job_stop``, no ``spawn``, no background ``bash``."""

    _gate(monkeypatch, False)
    launch.settings(_OFF)
    gate_off = await launch.run("-p", "hello")
    _gate(monkeypatch, True)
    switched_off = await launch.run("-p", "hello")

    assert switched_off == gate_off
    assert switched_off["code"] == 0 and switched_off["services"] == 0
    assert switched_off["inbox"] == [False] and switched_off["active"] is False
    assert not {"job", "job_stop"} & set(switched_off["tools"])
    assert "spawn" not in switched_off["agent_actions"] and not switched_off["background_bash"]


async def test_switched_off_the_tui_gets_no_jobs_so_no_footer_and_no_jobs_command(
    launch: _Launch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_tui`` draws the footer segment and registers ``/jobs`` from its ``jobs`` argument only."""

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    _gate(monkeypatch, False)
    launch.settings(_OFF)
    gate_off = await launch.run("--no-session")
    _gate(monkeypatch, True)
    switched_off = await launch.run("--no-session")

    assert switched_off == gate_off
    assert switched_off["tui_jobs"] is False and switched_off["services"] == 0


async def _probe_extension_api() -> dict[str, Any]:
    """What an extension sees of ``ExtensionAPI.jobs`` and ``post_notice`` (``test_facade_611``'s probe)."""

    seen: dict[str, Any] = {}

    def factory(aelix: Any) -> None:
        seen["hasattr_jobs"] = hasattr(aelix, "jobs")
        seen["getattr_post_notice"] = getattr(aelix, "post_notice", None)
        seen["getmembers"] = [n for n, _ in inspect.getmembers(aelix) if n in ("jobs", "post_notice")]
        for name in ("jobs", "post_notice"):
            try:
                getattr(aelix, name)
            except AttributeError as exc:
                seen[name] = (type(exc).__name__, str(exc), exc.name, exc.obj is aelix)

    await load_extension_from_factory(factory, name="probe")
    return seen


async def test_switched_off_an_extension_sees_jobs_and_post_notice_as_the_gate_off_shows_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gate(monkeypatch, False)
    gate_off = await _probe_extension_api()
    _gate(monkeypatch, True)
    assert activation.enabled_at_start(SettingsManager.in_memory(_OFF)) is False
    assert await _probe_extension_api() == gate_off
    assert gate_off["jobs"] == ("AttributeError", "'ExtensionAPI' object has no attribute 'jobs'", "jobs", True)


async def test_with_the_setting_absent_or_true_jobs_are_on_as_before(
    launch: _Launch, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Guard (passes on ``52bc607a``): the gate on and no setting, or ``true``, is today's run,
    and says nothing about the setting (verify mutant A9: a default other than ``True`` warned)."""

    _gate(monkeypatch, True)
    for value in (None, {"backgroundJobs": {"enabled": True, "limits": {"agents": 2}}}):
        launch.settings(value)
        seen = await launch.run("-p", "hello")
        assert seen["code"] == 0 and seen["services"] == 1 and seen["inbox"] == [True]
        assert {"job", "job_stop"} <= set(seen["tools"]) and seen["background_bash"]
        assert "spawn" in seen["agent_actions"] and seen["active"] is True
        err = capsys.readouterr().err
        assert "backgroundJobs" not in err, err


async def test_the_settings_keep_their_other_keys() -> None:
    """``limits`` and ``maxTotal`` still reach admission beside ``enabled`` (verify mutant S2).
    Fails on ``52bc607a``, which returns no ``enabled``; passes on ``639dcdf6``."""

    value = {"limits": {"agents": 2}, "maxTotal": 3, "enabled": False}
    assert SettingsManager.in_memory({"backgroundJobs": value}).get_background_jobs_settings() == value


@pytest.mark.parametrize("value", [False, [1], "off"], ids=["false", "list", "str"])
async def test_a_background_jobs_value_that_is_not_an_object_warns_once_and_jobs_stay_on(
    launch: _Launch,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: Any,
) -> None:
    """``"backgroundJobs": false`` reads as absent, with one warning (verify F7). On ``639dcdf6``
    jobs stayed on with nothing on stderr."""

    _gate(monkeypatch, True)
    launch.settings({"backgroundJobs": value})
    seen = await launch.run("-p", "hello")

    assert seen["services"] == 1 and seen["active"] is True and "job" in seen["tools"]
    err = capsys.readouterr().err
    assert err.count("backgroundJobs") == 1, err
    assert "Warning: settings backgroundJobs must be an object; using its defaults" in err


_SETUP_PROBE = """
import json
from aelix_coding_agent.jobs import activation


def setup(aelix):
    seen = {{
        "is_active": activation.is_active(),
        "hasattr_jobs": hasattr(aelix, "jobs"),
        "hasattr_post_notice": hasattr(aelix, "post_notice"),
    }}
    try:
        aelix.jobs
    except AttributeError as exc:
        seen["jobs_error"] = [type(exc).__name__, str(exc)]
    with open({out!r}, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(seen) + "\\n")
"""


def _setup_probe(env: Path) -> Path:  # noqa: F811
    """A GLOBAL extension whose ``setup()`` records what it sees of the gate, one line per load."""

    out = env / "setups.jsonl"
    extensions = env / "agent" / "extensions"
    extensions.mkdir(parents=True, exist_ok=True)
    (extensions / "probe.py").write_text(_SETUP_PROBE.format(out=str(out)), encoding="utf-8")
    return out


def _setups(out: Path) -> list[dict[str, Any]]:
    lines = out.read_text(encoding="utf-8").splitlines() if out.exists() else []
    out.unlink(missing_ok=True)
    return [json.loads(line) for line in lines]


async def test_switched_off_every_extension_setup_sees_the_gate_off_the_trust_vote_load_too(
    launch: _Launch, env: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """A project with an extension of its own makes a ``-p`` launch load the global extensions
    once to vote on its trust, before the job service is built; each ``setup()`` sees what it
    sees with the gate off. On ``639dcdf6`` the vote load's saw ``is_active`` true, ``post_notice``
    and a ``JobsInactive`` (verify F3, Codex C1)."""

    project_ext = Path.cwd() / ".aelix" / "extensions"
    project_ext.mkdir(parents=True)
    (project_ext / "proj.py").write_text("def setup(aelix):\n    pass\n", encoding="utf-8")
    out = _setup_probe(env)
    launch.settings(_OFF)
    _gate(monkeypatch, False)
    await launch.run("-p", "hello")
    gate_off = _setups(out)
    _gate(monkeypatch, True)
    await launch.run("-p", "hello")
    switched_off = _setups(out)

    assert len(gate_off) >= 2, "the vote load and the launch's own load both ran"
    assert switched_off == gate_off
    assert gate_off[0] == {
        "is_active": False,
        "hasattr_jobs": False,
        "hasattr_post_notice": False,
        "jobs_error": ["AttributeError", "'ExtensionAPI' object has no attribute 'jobs'"],
    }


async def test_switched_off_aelix_status_loads_its_extensions_with_the_gate_off(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    """``aelix status`` loads the extensions a launch here would and reads the switch first.
    On ``639dcdf6`` it never read it: ``is_active`` true and ``post_notice`` present (Codex C1)."""

    out = _setup_probe(env)
    launch_settings = env / "agent" / "settings.json"
    launch_settings.write_text(json.dumps(_OFF), encoding="utf-8")
    _gate(monkeypatch, False)
    assert await entry_mod._async_main(["status", "--json"]) == 0
    gate_off = _setups(out)
    _gate(monkeypatch, True)
    assert await entry_mod._async_main(["status", "--json"]) == 0
    switched_off = _setups(out)
    capsys.readouterr()

    assert len(gate_off) == 1 and switched_off == gate_off
    assert gate_off[0]["is_active"] is False and gate_off[0]["hasattr_post_notice"] is False


@pytest.mark.parametrize("value", ["no", 0, None, "false"], ids=["str-no", "int-0", "null", "str-false"])
async def test_a_value_that_is_not_a_bool_warns_once_and_jobs_stay_on(
    launch: _Launch,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: Any,
) -> None:
    _gate(monkeypatch, True)
    launch.settings({"backgroundJobs": {"enabled": value}})
    seen = await launch.run("-p", "hello")

    assert seen["services"] == 1 and seen["active"] is True and "job" in seen["tools"]
    err = capsys.readouterr().err
    assert err.count("backgroundJobs.enabled") == 1, err
    assert f"Warning: {_WARNING}" in err


async def test_aelix_status_warns_once_about_a_background_jobs_that_is_not_an_object(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    """``aelix status`` reads the switch as a launch does, and says so on stderr once
    (verify-r2 F3, mutant S3). Passes on ``8f07404f``."""

    (env / "agent" / "settings.json").write_text(json.dumps({"backgroundJobs": False}), encoding="utf-8")
    _gate(monkeypatch, True)
    assert await entry_mod._async_main(["status", "--json"]) == 0
    err = capsys.readouterr().err
    assert err.count("backgroundJobs") == 1, err
    assert "Warning: settings backgroundJobs must be an object; using its defaults" in err


async def test_with_the_gate_off_the_setting_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A guard of the new function only, for ADR-0293 §12 ("the ``backgroundJobs`` settings are
    never read"): ``enabled_at_start`` reads nothing while the gate is off. It cannot pass on
    ``52bc607a``, which has no ``enabled_at_start``."""

    class _Unread:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"read {name} with the gate off")

    _gate(monkeypatch, False)
    assert activation.enabled_at_start(_Unread()) is False
    assert activation.is_active() is False


def _manager(tmp_path: Path, global_value: dict[str, Any] | None, project_value: dict[str, Any] | None) -> SettingsManager:
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "repo"
    (cwd / ".aelix").mkdir(parents=True)
    agent_dir.mkdir()
    if global_value is not None:
        (agent_dir / "settings.json").write_text(json.dumps(global_value), encoding="utf-8")
    if project_value is not None:
        (cwd / ".aelix" / "settings.json").write_text(json.dumps(project_value), encoding="utf-8")
    return SettingsManager.create(cwd, agent_dir, project_trusted=True)


@pytest.mark.parametrize(
    ("global_value", "project_value", "expected"),
    [
        (None, _OFF, True),
        (_OFF, {"backgroundJobs": {"enabled": True}}, False),
    ],
    ids=["project-false-ignored", "project-true-ignored"],
)
async def test_a_project_value_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    global_value: dict[str, Any] | None,
    project_value: dict[str, Any],
    expected: bool,
) -> None:
    """A trusted repository's ``.aelix/settings.json`` neither turns jobs off nor back on."""

    sm = _manager(tmp_path, global_value, project_value)
    assert sm.get_project_settings().background_jobs == project_value["backgroundJobs"], "the project file was read"
    _gate(monkeypatch, True)
    assert activation.enabled_at_start(sm) is expected
    assert activation.is_active() is expected
    assert sm.get_background_jobs_enabled() is expected


@pytest.mark.parametrize("at_start", [True, False], ids=["on-at-start", "off-at-start"])
async def test_it_is_read_once_a_change_while_running_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_start: bool
) -> None:
    sm = _manager(tmp_path, {"backgroundJobs": {"enabled": at_start}}, None)
    _gate(monkeypatch, True)
    assert activation.enabled_at_start(sm) is at_start

    sm.set_background_jobs_enabled(not at_start)
    await sm.flush()
    assert sm.get_background_jobs_enabled() is (not at_start), "the change was made"
    assert activation.is_active() is at_start, "and nothing read it: it applies at the next launch"


async def test_the_settings_row_writes_the_global_key_and_applies_next_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R13 (the lead's call): a "Background jobs" row after "Agent delegation", like it persist-only."""

    sm = _manager(tmp_path, {"backgroundJobs": {"limits": {"agents": 2}}}, {"theme": "dark"})
    _gate(monkeypatch, True)
    rows = build_settings_rows(sm)
    keys = [row.key for row in rows]
    assert keys[keys.index("features_agents") + 1] == "background_jobs"
    row = rows[keys.index("background_jobs")]
    assert (row.label, row.kind, row.live, row.read(sm)) == ("Background jobs", "bool", False, "on")
    assert "applies next launch" in row.help and len(row.help) <= 78

    result = apply_setting(row, sm)
    await sm.flush()
    assert (result.kind, result.live) == ("ok", None)
    assert result.message == "background jobs → off (takes effect after you restart aelix)"
    written = json.loads((tmp_path / "agent" / "settings.json").read_text(encoding="utf-8"))
    assert written["backgroundJobs"] == {"limits": {"agents": 2}, "enabled": False}, "the other keys stay"
    project = json.loads((tmp_path / "repo" / ".aelix" / "settings.json").read_text(encoding="utf-8"))
    assert project == {"theme": "dark"}, "the project file is not written"
    assert activation.is_active() is True, "this launch is unchanged"

    assert apply_setting(row, sm).message.startswith("background jobs → on")


async def test_the_row_writes_only_its_key_over_what_another_writer_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like "Agent delegation", the row writes ``backgroundJobs.enabled`` alone: a limit another
    writer changed in the file after this manager read it stays (verify F6). On ``639dcdf6`` the
    whole object was written from the manager's copy, so ``limits.agents`` went back to 2."""

    sm = _manager(tmp_path, {"backgroundJobs": {"limits": {"agents": 2}, "maxTotal": 5}}, None)
    path = tmp_path / "agent" / "settings.json"
    disk = json.loads(path.read_text(encoding="utf-8"))
    disk["backgroundJobs"]["limits"]["agents"] = 7
    disk["theme"] = "dark"
    path.write_text(json.dumps(disk), encoding="utf-8")
    _gate(monkeypatch, True)
    row = {r.key: r for r in build_settings_rows(sm)}["background_jobs"]

    assert apply_setting(row, sm).kind == "ok"
    await sm.flush()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"backgroundJobs": {"limits": {"agents": 7}, "maxTotal": 5, "enabled": False}, "theme": "dark"}


async def test_switched_off_the_row_is_still_offered_so_jobs_can_be_turned_back_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = _manager(tmp_path, _OFF, None)
    _gate(monkeypatch, True)
    assert activation.enabled_at_start(sm) is False
    rows = {row.key: row for row in build_settings_rows(sm)}
    assert rows["background_jobs"].read(sm) == "off"
    assert apply_setting(rows["background_jobs"], sm).message.startswith("background jobs → on")
    assert activation.is_active() is False, "until the next launch"


async def test_with_the_gate_off_there_is_no_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: before the gate opens nothing reads the setting, so ``/settings`` does not offer it."""

    _gate(monkeypatch, False)
    keys = [row.key for row in build_settings_rows(_manager(tmp_path, None, None))]
    assert "background_jobs" not in keys and "features_agents" in keys


def _run_one_agent(body: dict[str, Any], index: int) -> dict[str, Any]:
    if index == 0:
        return call("agent", {"profile": PROFILE, "task": "say the answer"})
    return say(f"parent turn {index}")


@REAL_CHILD
@pytest.mark.parametrize("switched_off", [True, False], ids=["setting-false", "setting-absent"])
def test_a_delegated_child_reads_the_same_value(tmp_path: Path, switched_off: bool) -> None:
    """Contract r2 §7: a parent with the setting false runs a child that has no job service either.

    Both processes are gated by the c0 launcher (#777) and read the same global
    file. A child is granted built-in tools only, so its gate reads on the wire as
    ``bash``'s ``run_in_background``; with the setting absent it is offered, the
    control. ``setting-false`` fails on ``52bc607a`` (the parent is offered ``job``);
    ``setting-absent`` is a guard and passes there.
    """

    scratch = Scratch(tmp_path)
    if switched_off:
        scratch.settings(_OFF)
    with ModelServer(_run_one_agent) as server:
        scratch.point_at(server)
        done = scratch.run("-p", "go")

    assert done.returncode == 0, done.stderr
    assert len(server.child) == 1 and CHILD_ANSWER in all_text(server.parent[1])
    parent, child = server.parent[0], server.child[0]
    assert ("job" in tools(parent)) is not switched_off
    assert ("spawn" in agent_actions(parent)) is not switched_off
    assert offers_background_bash(child) is not switched_off
