"""The rigs the control-v1 fixtures run on, the case runner, and the floor capture (#623, ADR-0302 D8).

A fixture case names one rig:

* ``bare`` - the ``server-ws`` shape: a passthrough runtime over a two-tool
  harness, and no service at all (``aelix_server/rpc_ws.py`` injects none);
* ``full`` - every service: a permission posture, a ``SettingsManager`` over the
  seeded files (trusted project), an ``AuthStorage`` file, an
  ``AgentProfileService`` over the seeded user profile, a fake bound subagent
  runtime, aelix's own tools, and populated collections (the fixture extension
  ``ui_extension.py`` with a hook, a tool and commands; a skill; a prompt
  template; a persisted session). An MCP lease is a case's ``before`` request
  (``mcp/configure`` over the stdio fixture server ``seed.mcp_script``);
* ``turn`` - ``full`` with a model turn held open, for the idle refusals;
* ``child`` - what the real ``--mode rpc`` child of ``test_real_child.py`` gets:
  the files :func:`seed_files` writes and the CLI's own kind of services built
  from them (untrusted project), the fixture extension, and nothing that exists
  only in process (no fake subagent runtime). A ``"replay": true`` case runs in
  process on this rig and again on the real child.

Each in-process case gets a fresh rig under its own root.

HOW A LATER LANE ADDS TO VERSION 1 (ADR-0302 D9):

* **A service.** Add it in ONE place, :func:`rpc_services`: it returns the keyword
  set that both ``run_rpc_mode(...)`` and ``RpcControlPlane(...)`` take, per rig.
  A new keyword of those two signatures (``selections=...``, say) is one more
  entry in that dict for the rigs that serve it, built from ``seed`` and the
  harness handed in. Nothing else in this file names a service keyword.
* **A fixture.** A new ``fixtures/<method with / as .>.json`` (the shape is in any
  existing file and in ADR-0302 D8), with ``"since": "#<issue>"``. Each case
  names a rig, its ``params``, and a ``data`` (success) or ``error`` (refusal)
  JSON Schema; ``sensitive`` names another legal value per param; ``before``
  holds requests sent first on the same wire; ``during`` a command started before
  the case's request and left running while it is sent (``"ended_by_case":
  true`` when the case's request ends it, so a check may read its own answer:
  :func:`run_case`); ``setup`` names an entry of :data:`SETUPS` and ``check`` an
  entry of :data:`CHECKS` when a case needs a state or an assertion no request
  can make. ``test_input_coverage.py`` says what a new param's cases must cover.
* **A case** in an existing file: the same, with its own ``"since"``. Never edit
  a ``"since": "v1"`` case: it is frozen as written (its digest is in the floor).
  A new value of an open enum (``"open": true``: validation accepts values it
  does not list, :func:`opened`) needs no edit to pass the v1 cases; list it in
  your own case's enum to make it a known value.
* Then ``uv run python scripts/check_control_v1.py --update`` adds the new
  entries to ``floor_v1.json`` (it refuses to drop, loosen or edit one, and a
  schema keyword or case key it does not capture), and the change commits both.
  ``test_additive_only.py`` fails until it has; ``V1_SHA256`` there stays.

Tokens in ``params`` and ``before``: a string ``"$rig:<name>"`` is replaced by
that rig value (:attr:`Rig.values`), and ``{"$gen": "strings", "count": n,
"prefix": p}``, ``{"$gen": "text", "count": n, "char": c, "prefix": p}`` (``n``
characters in all) and ``{"$gen": "objects", "count": n, "template": {...}}``
(``{n}`` in a string is the index) by a generated value, so a boundary case does
not spell 256 entries out.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import socket
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
FLOOR = HERE / "floor_v1.json"
EXTENSION_FILE = HERE / "ui_extension.py"
RIG_NAMES = ("bare", "full", "turn", "child")
WAIT_S = 60.0
PROFILE = "reviewer"
SKILL = "fixture-skill"
PROMPT = "fixture-prompt"
OAUTH_PROVIDER = "fixture-oauth"

MCP_STDIO = """from mcp.server.fastmcp import FastMCP
server = FastMCP("fixture")
@server.tool()
def echo(value: str) -> str:
    return value
server.run()
"""

MCP_NET = """import sys
from mcp.server.fastmcp import FastMCP
server = FastMCP("fixture", host="127.0.0.1", port=int(sys.argv[2]), log_level="ERROR")
@server.tool()
def echo(value: str) -> str:
    return value
server.run(transport=sys.argv[1])
"""


# === The files ================================================================


@dataclass(frozen=True)
class Seed:
    root: Path
    home: Path
    agent_dir: Path
    settings_path: Path
    cwd: Path
    auth_path: Path
    sessions: Path
    mcp_script: Path
    mcp_net_script: Path


def seed_files(root: Path) -> Seed:
    """Write the files every rig with services reads, and the real child too.

    A user profile naming its tools, a skill, a prompt template, a global
    ``settings.json`` with the resource lists, a trusted-project
    ``.aelix/settings.json`` whose list ``settings/get`` must NOT show, an
    ``mcp.json`` with one server turned off (nothing starts at launch), and the
    two MCP fixture server scripts. Nothing outside ``root``.
    """

    root.mkdir(parents=True, exist_ok=True)
    home, agent, cwd = root / "home", root / "agent", root / "work"
    for directory in (
        home,
        agent / "agents",
        agent / "prompt-templates",
        cwd / ".aelix",
        root / "sessions",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (agent / "agents" / f"{PROFILE}.md").write_text(
        f"---\nname: {PROFILE}\ndescription: Reviews the fixture\ntools: [read, grep]\n---\n\n"
        "You review the fixture.\n",
        encoding="utf-8",
    )
    # A profile that does not parse: ``agents/list.diagnostics`` is not empty.
    (agent / "agents" / "broken.md").write_text(
        "---\nname: broken\n---\n\nNo description.\n", "utf-8"
    )
    skill = agent / "skills" / SKILL
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: A control-v1 fixture skill\n---\n\nUse the fixture.\n",
        encoding="utf-8",
    )
    (agent / "prompt-templates" / f"{PROMPT}.md").write_text(
        "---\ndescription: A control-v1 fixture prompt\n---\nGreet $1\n", encoding="utf-8"
    )
    settings = agent / "settings.json"
    settings.write_text(
        json.dumps({"skills": ["-no-such-skill"], "prompts": [], "retryEnabled": True}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (cwd / ".aelix" / "settings.json").write_text(
        json.dumps({"skills": ["-project-only-entry"]}, indent=2) + "\n", encoding="utf-8"
    )
    mcp_script, net_script = root / "mcp_fixture.py", root / "mcp_net_fixture.py"
    mcp_script.write_text(MCP_STDIO, encoding="utf-8")
    net_script.write_text(MCP_NET, encoding="utf-8")
    (agent / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "seeded": {
                        "command": sys.executable,
                        "args": [str(mcp_script)],
                        "enabled": False,
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return Seed(
        root=root,
        home=home,
        agent_dir=agent,
        settings_path=settings,
        cwd=cwd,
        auth_path=agent / "auth.json",
        sessions=root / "sessions",
        mcp_script=mcp_script,
        mcp_net_script=net_script,
    )


# === Fixtures and tokens ======================================================


def load_fixtures() -> list[dict[str, Any]]:
    """Every fixture file, each with ``_file`` (its name) added."""

    fixtures = []
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_file"] = path.name
        fixtures.append(data)
    return fixtures


def fixture_id(fixture: dict[str, Any]) -> str:
    """``method`` for a method, ``event:<family>``, ``envelope`` or ``error:<name>``."""

    if "method" in fixture:
        return str(fixture["method"])
    if "event" in fixture:
        return f"event:{fixture['event']}"
    if "error" in fixture:
        return f"error:{fixture['error']}"
    return "envelope"


def case_since(fixture: dict[str, Any], case: dict[str, Any]) -> str:
    return str(case.get("since", fixture["since"]))


def resolve(value: Any, values: dict[str, Any]) -> Any:
    """``value`` with every ``$rig:`` and ``$gen`` token replaced."""

    if isinstance(value, str) and value.startswith("$rig:"):
        return values[value[len("$rig:") :]]
    if isinstance(value, list):
        return [resolve(item, values) for item in value]
    if isinstance(value, dict):
        gen = value.get("$gen")
        if gen == "strings":
            return [f"{value.get('prefix', 'x')}{n}" for n in range(int(value["count"]))]
        if gen == "text":
            prefix = str(value.get("prefix", ""))
            return prefix + str(value.get("char", "x")) * (int(value["count"]) - len(prefix))
        if gen == "objects":
            return [
                _numbered(resolve(value["template"], values), n) for n in range(int(value["count"]))
            ]
        return {key: resolve(item, values) for key, item in value.items()}
    return value


def _numbered(template: Any, n: int) -> Any:
    if isinstance(template, str):
        return template.replace("{n}", str(n))
    if isinstance(template, list):
        return [_numbered(item, n) for item in template]
    if isinstance(template, dict):
        return {key: _numbered(item, n) for key, item in template.items()}
    return template


# === Services: the ONE place the RPC keyword set is built =====================


class FakeSubagents:
    """The fake bound subagent runtime of the ``full`` rig.

    ``spawn`` publishes the three progress channels on the harness's event bus
    while it runs, so an ``agents/run`` is the parent of those
    ``aelix_subagent`` frames.
    """

    def __init__(self, bus: Any) -> None:
        from aelix_coding_agent.subagent_contract import CONTRACT_VERSION

        self.contract_version = CONTRACT_VERSION
        self.bus = bus

    def resolve_profile(self, name: str, *, allow_project: bool = False) -> Any:
        from aelix_coding_agent.agents.profile import AgentProfile
        from aelix_coding_agent.subagent_contract import ResolvedProfile

        profile = AgentProfile(
            name=name, description="d", body="b", file_path=f"/agents/{name}.md", scope="user"
        )
        return ResolvedProfile(
            name=name, profile=profile, source_path=profile.file_path, scope="user"
        )

    async def spawn(self, resolved: Any, task: str, **_: Any) -> Any:
        from aelix_coding_agent.subagent_contract import SubagentProgress, SubagentResult

        for channel, state, tool in (
            ("subagent_start", "starting", None),
            ("subagent_tool", "running", "read"),
            ("subagent_end", "done", None),
        ):
            self.bus.emit(
                channel,
                SubagentProgress(
                    id="child-1",
                    profile=resolved.name,
                    state=state,  # type: ignore[arg-type]
                    current_tool=tool,
                    elapsed_ms=5,
                    tokens=7,
                    cost=0.25,
                    model="fixture-model",
                    provider="fixture",
                ),
            )
        return SubagentResult(
            id="child-1", profile=resolved.name, ok=True, status="ok", summary=f"did: {task}"
        )


#: A fixture's ``requires`` -> the :func:`rpc_services` keyword whose absence
#: takes its method out of ``capabilities.methods`` (``test_floor.py``).
#: ``subagents`` is no keyword: it is the harness's bound delegation runtime,
#: which :func:`open_rig` binds for ``full`` and ``turn``.
REQUIRES: dict[str, str] = {
    "settings": "settings_manager",
    "posture": "permission_posture",
    "auth": "auth_storage",
    "agents": "agent_service",
    "selections": "selections",
}


def bind_fake_subagents(harness: Any) -> None:
    """Bind :class:`FakeSubagents` as the harness's delegation runtime (``agents/run`` is served)."""

    harness.runtime._subagents = FakeSubagents(harness.runtime.event_bus)


def rpc_services(
    name: str,
    seed: Seed | None,
    harness: Any,
    live: Callable[[], Any] | None = None,
    inventory: Any = None,
) -> dict[str, Any]:
    """The keyword set ``run_rpc_mode(...)`` and ``RpcControlPlane(...)`` both take, for rig ``name``.

    The one place a service is added (module docstring). ``bare`` gets none, as
    ``server-ws`` does. ``full`` and ``turn`` trust the project; ``child`` does
    not, as the real child launched in an unapproved folder does not. *live*
    reaches the harness in use (the runtime's, after a reload or a branch) and
    *inventory* is the extension inventory ``/extension`` would list (#624).
    """

    if name == "bare" or seed is None:
        return {}
    from aelix_ai.oauth import AuthStorage
    from aelix_ai.settings.settings_manager import SettingsManager
    from aelix_ai.settings.storage import FileSettingsStorage
    from aelix_coding_agent.agents.service import AgentProfileService
    from aelix_coding_agent.builtin.permission_mode import PermissionPosture
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.extensions.loader import ExtensionLoadError

    trusted = name in ("full", "turn")
    settings = SettingsManager.from_storage(
        FileSettingsStorage(seed.cwd, seed.agent_dir, global_path=seed.settings_path),
        project_trusted=trusted,
    )
    agents = AgentProfileService(
        cwd=str(seed.cwd),
        project_trusted=trusted,
        parsed=Args(),
        baseline=Args(),
        skills_holder={"result": None},
        agent_dir=str(seed.agent_dir),
    )
    # ``resources/list.extensionErrors`` is populated on the rigs that populate.
    broken = ExtensionLoadError(path=str(seed.root / "broken_extension.py"), error="SyntaxError")
    errors = [broken] if trusted else []
    # #624 (ADR-0305): the five selection controls, built by the CLI's own helper.
    controls = selection_controls(
        seed,
        trusted=trusted,
        settings=settings,
        agents=agents,
        extension_errors=errors,
        live=live if live is not None else (lambda: harness),
        inventory=inventory,
    )
    return {
        "permission_posture": PermissionPosture(),
        "settings_manager": settings,
        "auth_storage": AuthStorage(seed.auth_path),
        "agent_service": agents,
        "extension_errors": errors,
        "selections": controls.by_kind(),
    }


class _Live:
    """What ``_selection_controls`` reads ``.harness`` from: the harness in use now."""

    def __init__(self, live: Callable[[], Any]) -> None:
        self._live = live

    @property
    def harness(self) -> Any:
        return self._live()


def selection_controls(
    seed: Seed,
    *,
    trusted: bool,
    settings: Any,
    agents: Any,
    extension_errors: list[Any],
    live: Callable[[], Any],
    inventory: Any = None,
) -> Any:
    """``cli/entry.py``'s ``_selection_controls`` over the seeded files (#624, ADR-0305 D10).

    The skill catalog is the seeded agent dir's skills; the extension inventory
    is *inventory* (``None``: only the built-in rows); the MCP launch started
    nothing. ``resolve_mcp_config`` reads the process's agent dir, which
    ``rig_env`` points at a directory of its own, so the seeded ``mcp.json`` is
    copied there: the ``mcp`` kind then reads the same file the child reads.
    """

    import shutil

    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.config import get_agent_dir
    from aelix_coding_agent.cli.entry import _selection_controls, _settings_bases
    from aelix_coding_agent.core.agent_selection import build_agent_control
    from aelix_coding_agent.core.extension_selection import mcp_launch
    from aelix_coding_agent.core.mcp_servers import McpNameRecord
    from aelix_coding_agent.core.resource_selection import ApplyGate, SettingsLists
    from aelix_coding_agent.core.skill_selection import SkillSource, load_skill_catalog

    env_agent = Path(get_agent_dir())
    if env_agent != seed.agent_dir and (seed.agent_dir / "mcp.json").is_file():
        env_agent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed.agent_dir / "mcp.json", env_agent / "mcp.json")
    gate = ApplyGate()
    global_base, project_base = _settings_bases(settings, str(seed.cwd))
    agent_control = build_agent_control(
        settings,
        cwd=str(seed.cwd),
        project_trusted=trusted,
        global_base=global_base,
        project_base=project_base,
        agent_dir=str(seed.agent_dir),
        holder={"result": SettingsLists()},
        delegation=agents.delegation,
        harness=live,
        gate=gate,
    )
    catalog = load_skill_catalog(
        [SkillSource("global", str(seed.agent_dir / "skills"))], SettingsLists()
    )
    return _selection_controls(
        settings_manager=settings,
        cwd=str(seed.cwd),
        project_trusted=trusted,
        runtime=_Live(live),
        parsed=Args(),
        skills_holder={"result": catalog},
        gate=gate,
        mcp_manager=None,
        extension_errors=extension_errors,
        extension_holder={"inventory": inventory, "records": [], "reload_failure": None},
        mcp_launch_record=mcp_launch([], [], set(), None),
        mcp_inherited=McpNameRecord(),
        agent_control=agent_control,
        eager_listing=False,
    )


# === Setups and checks ========================================================


@dataclass
class Setup:
    """A state no request can make: ``pre`` edits the seeded files, ``post`` the running rig."""

    pre: Callable[[Seed], None] | None = None
    post: Callable[[Rig], Awaitable[None]] | None = None
    inventory: Callable[[Seed], Awaitable[Any]] | None = None
    """#624: the extension inventory ``/extension`` and ``selections/*`` kind
    ``extension`` list, built before the services (``None``: the built-in rows only)."""


def _unreadable_global_settings(seed: Seed) -> None:
    seed.settings_path.write_text("{", encoding="utf-8")


async def _hold_the_original_session(rig: Rig) -> None:
    """A second holder takes the original session's writer lock once the branch moved off it."""

    from aelix_agent_core.session.session_lock import SessionWriterLock

    runtime = rig.runtime
    original = runtime.harness.session_file
    own = SessionWriterLock(original)
    assert own.try_acquire(), "the rig could not take its own session lock"
    runtime.set_writer_lock(own)
    fork = runtime.fork

    async def fork_then_hold(*args: Any, **kwargs: Any) -> Any:
        result = await fork(*args, **kwargs)
        other = SessionWriterLock(original)
        assert other.try_acquire(), "the second holder could not take the released lock"
        rig.cleanups.append(other.release)
        return result

    runtime.fork = fork_then_hold


async def _net_mcp_servers(rig: Rig) -> None:
    """Start the http and sse MCP fixture servers; their urls become ``$rig:`` values."""

    for transport, path, key in (
        ("streamable-http", "/mcp", "mcp_http_url"),
        ("sse", "/sse", "mcp_sse_url"),
    ):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(rig.seed.mcp_net_script),  # type: ignore[union-attr]
            transport,
            str(port),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rig.processes.append(proc)
        await _wait_for_port(port, proc)
        rig.values[key] = f"http://127.0.0.1:{port}{path}"


async def _wait_for_port(port: int, proc: asyncio.subprocess.Process) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    while loop.time() < deadline:
        if proc.returncode is not None:
            raise RuntimeError(f"the MCP fixture server exited with {proc.returncode}")
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return
    raise TimeoutError(f"the MCP fixture server on port {port} did not listen")


async def _hold_the_rebuild(rig: Rig) -> None:
    """The next harness build (a reload's) waits until the case is answered."""

    rig.hold = asyncio.Event()


async def _an_installed_extension(rig: Rig) -> None:
    """One ``aelix.extensions`` entry point, as an ``aelix extension install`` leaves."""

    import importlib.metadata
    from types import SimpleNamespace

    real = importlib.metadata.entry_points
    entry = SimpleNamespace(
        name="fixture-pack",
        value="aelix_fixture_pack:setup",
        dist=SimpleNamespace(name="aelix-fixture-pack", version="1.0.0"),
    )

    def entry_points(**kwargs: Any) -> Any:
        if kwargs.get("group") == "aelix.extensions":
            return [entry]
        return real(**kwargs)

    importlib.metadata.entry_points = entry_points  # type: ignore[assignment]
    rig.cleanups.append(lambda: setattr(importlib.metadata, "entry_points", real))


async def _stopped_harness(rig: Rig) -> None:
    rig.runtime._stopped = "The session stopped: a rebuild and its restore both failed (fixture)"


async def _unbound_subagents(rig: Rig) -> None:
    rig.harness.runtime._subagents = None


def _project_settings_absent(seed: Seed) -> None:
    """A trusted project with no ``.aelix/settings.json``: a project save would create it."""

    (seed.cwd / ".aelix" / "settings.json").unlink()


def _mcp_servers(seed: Seed, names: list[str]) -> None:
    servers = {name: {"command": "true"} for name in names}
    (seed.agent_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8"
    )


#: ``selections/set``'s bounds (``rpc/control_selection.py``): a change list of
#: 1,024 entries and a key of 8,192 characters are accepted, one more refused.
MAX_CHANGES = 1024
MAX_KEY = 8192
LONG_SERVER = "k" * MAX_KEY


async def _discovered_extension(seed: Seed) -> Any:
    """A global extension file on disk, discovered and loaded as the CLI does (#624)."""

    from aelix_coding_agent.core.extension_selection import build_inventory
    from aelix_coding_agent.extensions.loader import discover_and_load_extensions

    folder = seed.agent_dir / "extensions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "listed.py").write_text("def setup(aelix):\n    pass\n", encoding="utf-8")
    loaded = await discover_and_load_extensions([], cwd=seed.cwd, agent_dir=seed.agent_dir)
    return build_inventory(loaded)


def _untrusted_project_resources(seed: Seed) -> None:
    """A project skill, agent profile, extension and ``mcp.json``: in an untrusted
    folder each kind lists one locked row for them, and nothing of them is read."""

    project = seed.cwd / ".aelix"
    (project / "skills" / "local").mkdir(parents=True, exist_ok=True)
    (project / "skills" / "local" / "SKILL.md").write_text(
        "---\nname: local\ndescription: A project skill\n---\nbody\n", encoding="utf-8"
    )
    (project / "agents").mkdir(parents=True, exist_ok=True)
    (project / "agents" / "local.md").write_text(
        "---\nname: local\ndescription: A project profile\n---\n\nbody\n", encoding="utf-8"
    )
    (project / "extensions").mkdir(parents=True, exist_ok=True)
    (project / "extensions" / "local.py").write_text(
        "def setup(aelix):\n    pass\n", encoding="utf-8"
    )
    (project / "mcp.json").write_text(
        json.dumps({"mcpServers": {"local": {"command": "true"}}}) + "\n", encoding="utf-8"
    )


SETUPS: dict[str, Setup] = {
    "project_settings_absent": Setup(pre=_project_settings_absent),
    "many_mcp_servers": Setup(
        pre=lambda seed: _mcp_servers(seed, [f"s{n}" for n in range(MAX_CHANGES)])
    ),
    "a_long_mcp_server_name": Setup(pre=lambda seed: _mcp_servers(seed, [LONG_SERVER])),
    "an_extension_on_disk": Setup(inventory=_discovered_extension),
    "untrusted_project_resources": Setup(pre=_untrusted_project_resources),
    "global_settings_unreadable": Setup(pre=_unreadable_global_settings),
    "hold_the_original_session": Setup(post=_hold_the_original_session),
    "net_mcp_servers": Setup(post=_net_mcp_servers),
    "an_installed_extension": Setup(post=_an_installed_extension),
    "hold_the_rebuild": Setup(post=_hold_the_rebuild),
    "stopped_harness": Setup(post=_stopped_harness),
    "no_subagents": Setup(post=_unbound_subagents),
}


def _session_file_exists(rig: Rig, result: CaseResult) -> None:
    data = result.response["data"]
    assert Path(data["sessionFile"]).is_file(), data
    assert data["sessionFile"] != rig.values["session_file"], data


def _cancelled_the_during_command(rig: Rig, result: CaseResult) -> None:
    assert result.during is not None and result.during["success"] is False, result.during
    assert result.during["error"] == "Command cancelled", result.during


def _global_list_not_the_project_one(rig: Rig, result: CaseResult) -> None:
    project = json.loads((rig.seed.cwd / ".aelix" / "settings.json").read_text("utf-8"))  # type: ignore[union-attr]
    shown = result.response["data"]["settings"]["skill_paths"]
    assert shown != project["skills"], shown


CHECKS: dict[str, Callable[[Rig, CaseResult], None]] = {
    "session_file_exists": _session_file_exists,
    "cancelled_the_during_command": _cancelled_the_during_command,
    "global_list_not_the_project_one": _global_list_not_the_project_one,
}


# === The rig ==================================================================


@pytest.fixture
def rig_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every rig off the developer's home: code that resolves the agent dir
    or ``~`` itself (``AgentProfileService.use`` scanning skills) lands in tmp."""

    from tests.env_sandbox import sandbox_home

    sandbox_home(monkeypatch, tmp_path / "env-home")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "env-agent"))
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


@dataclass
class Rig:
    name: str
    seed: Seed | None
    wire: Any
    harness: Any
    runtime: Any
    task: asyncio.Task[None] | None = None
    values: dict[str, Any] = field(default_factory=dict)
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    processes: list[asyncio.subprocess.Process] = field(default_factory=list)
    cleanups: list[Callable[[], Any]] = field(default_factory=list)
    hold: asyncio.Event | None = None
    """Set by the ``hold_the_rebuild`` setup: the next harness build waits on it."""
    services: dict[str, Any] = field(default_factory=dict)
    """The keyword set ``run_rpc_mode`` was given (:func:`rpc_services`)."""

    def mark(self, event: str) -> None:
        """Put a ``{"type": "rig", "event": ...}`` record on the wire, for a ``during`` to wait on."""

        self.wire.write((json.dumps({"type": "rig", "event": event}) + "\n").encode("utf-8"))

    def release(self) -> None:
        if self.hold is not None:
            self.hold.set()


def _tool(name: str) -> Any:
    from aelix_agent_core.types import AgentTool

    return AgentTool(name=name, description=name, parameters={"type": "object", "properties": {}})


def _streamer(gate: asyncio.Event) -> Any:
    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent

    async def stream(model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        await gate.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return stream


async def _fixture_extension() -> Any:
    import importlib.util

    from aelix_coding_agent.extensions.loader import load_extension_from_factory

    spec = importlib.util.spec_from_file_location("_control_v1_ui_extension", EXTENSION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extension = await load_extension_from_factory(module.setup, name="ui_extension")
    extension.resolved_path = str(EXTENSION_FILE)
    return extension


async def build_harness(name: str, seed: Seed | None, gate: asyncio.Event, session: Any) -> Any:
    """The harness of rig ``name``: aelix's own tools and the fixture extension, or two bare tools."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.harness.prompt_templates import load_prompt_templates
    from aelix_agent_core.harness.skills import load_skills
    from aelix_agent_core.harness.tool_policy import ToolPolicy
    from aelix_ai.streaming import Model
    from aelix_coding_agent.tools import create_all_tools

    model = Model(id="fixture", provider="fixture")
    if name == "bare" or seed is None:
        return AgentHarness(
            AgentHarnessOptions(
                model=model, stream_fn=_streamer(gate), tools=[_tool("alpha"), _tool("beta")]
            )
        )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=model,
            stream_fn=_streamer(gate),
            session=session,
            cwd=str(seed.cwd),
            tools=list(create_all_tools(str(seed.cwd)).values()),
            extensions=[await _fixture_extension()],
            # ``find`` is kept off for the run, as ``--exclude-tools find`` does:
            # the ``tools/set_active`` refusal case names it.
            tool_policy=ToolPolicy(exclude=("find",)) if name != "child" else None,
            project_trusted=name != "child",
        )
    )
    harness.set_skills(list(load_skills(str(seed.agent_dir / "skills")).skills))
    harness.set_prompt_templates(
        list(load_prompt_templates(str(seed.agent_dir / "prompt-templates")).templates)
    )
    return harness


@contextlib.asynccontextmanager
async def open_rig(name: str, root: Path, *, setup: str | None = None) -> AsyncIterator[Rig]:
    """A running ``run_rpc_mode`` for rig ``name`` over an in-memory JSONL wire."""

    from aelix_agent_core.runtime import create_agent_session_runtime
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionRepo,
        LocalFileSystem,
    )
    from aelix_ai.messages import TextContent, UserMessage
    from aelix_ai.oauth import register_oauth_provider, unregister_oauth_provider
    from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

    from tests.rpc.test_runtime_control_lifecycle_594 import Wire

    if name not in RIG_NAMES:
        raise ValueError(f"unknown rig {name!r}")
    hook = SETUPS[setup] if setup is not None else Setup()
    seed = None if name == "bare" else seed_files(root)
    if seed is not None and hook.pre is not None:
        hook.pre(seed)
    gate = asyncio.Event()
    if name != "turn":
        gate.set()
    wire = Wire()
    runtime: Any = None
    values: dict[str, Any] = {"python": sys.executable}
    box: list[Rig] = []
    if seed is None:
        harness = await build_harness(name, None, gate, None)
    else:
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(seed.sessions))
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(seed.cwd)))
        await session.append_message(UserMessage(content=[TextContent(text="hello")]))

        async def factory(session: Any, *, reload_seed: object = None) -> Any:
            hold = box[0].hold if box else None
            if hold is not None:
                box[0].mark("build_entered")
                await hold.wait()
            return await build_harness(name, seed, gate, session)

        harness = await build_harness(name, seed, gate, session)
        runtime = await create_agent_session_runtime(harness, factory, repo=repo, fs=fs)
        values.update(
            cwd=str(seed.cwd),
            root=str(seed.root),
            mcp_script=str(seed.mcp_script),
            session_file=harness.session_file,
            entry_id=await session.get_leaf_id(),
        )
    rig = Rig(
        name=name, seed=seed, wire=wire, harness=harness, runtime=runtime, values=values, gate=gate
    )
    box.append(rig)
    if name in ("full", "turn"):
        bind_fake_subagents(harness)
        register_oauth_provider(_FixtureOAuth())
        rig.cleanups.append(lambda: unregister_oauth_provider(OAUTH_PROVIDER))
    inventory = (
        await hook.inventory(seed) if hook.inventory is not None and seed is not None else None
    )
    if inventory is not None:
        values["listed_key"] = next(
            row.key for row in inventory.rows if (row.info.path or "").endswith("listed.py")
        )
    services = rpc_services(
        name,
        seed,
        harness,
        live=(lambda: runtime.harness) if runtime is not None else None,
        inventory=inventory,
    )
    rig.services = services
    if "auth_storage" in services:
        await services["auth_storage"].load()
    rig.task = asyncio.create_task(
        run_rpc_mode(
            harness,
            runtime_host=runtime,
            stdin=wire.reader,
            stdout_write=wire.write,
            install_signal_handlers=False,
            **services,
        )
    )
    try:
        # One round trip first: the control plane exists (and is subscribed to
        # the event bus) only once ``run_rpc_mode`` has started reading.
        await request(wire, "rig-ready", "capabilities", {})
        if hook.post is not None:
            await hook.post(rig)
        if name == "turn":
            await _hold_a_turn(rig)
        yield rig
    finally:
        await _close(rig)


async def _hold_a_turn(rig: Rig) -> None:
    rig.wire.send({"type": "prompt", "id": "rig-turn", "message": "hold the turn"})
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    while rig.runtime.harness.phase == "idle":
        if loop.time() > deadline:
            raise TimeoutError("the turn rig's turn never started")
        await asyncio.sleep(0.01)


async def _close(rig: Rig) -> None:
    rig.gate.set()
    rig.release()
    try:
        rig.wire.send(eof=True)
        if rig.task is not None:
            await asyncio.wait_for(rig.task, WAIT_S)
    finally:
        live = rig.runtime.harness if rig.runtime is not None else rig.harness
        with contextlib.suppress(Exception):
            await live.dispose()
        if rig.runtime is not None:
            with contextlib.suppress(Exception):
                await rig.runtime.dispose()
        for proc in rig.processes:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 15)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
        for cleanup in reversed(rig.cleanups):
            cleanup()


class _FixtureOAuth:
    """A fake OAuth provider: one progress notice, the auth url, a select, an input."""

    id = OAUTH_PROVIDER
    name = "Fixture OAuth"
    uses_callback_server = False

    async def login(self, callbacks: Any) -> Any:
        from aelix_ai.oauth.types import (
            OAuthAuthInfo,
            OAuthCredentials,
            OAuthPrompt,
            OAuthSelectOption,
            OAuthSelectPrompt,
        )

        callbacks.on_progress("fixture login started")
        await callbacks.on_auth(
            OAuthAuthInfo(url="https://auth.invalid/authorize", instructions="Open the url")
        )
        choice = await callbacks.on_select(
            OAuthSelectPrompt(
                message="Pick an account",
                options=[OAuthSelectOption(id="a1", label="Account one")],
            )
        )
        code = await callbacks.on_prompt(OAuthPrompt(message="Paste the code", placeholder="code"))
        return OAuthCredentials(
            refresh="r",
            access="a",
            expires=4_102_444_800_000,
            extra={"choice": choice, "code": code},
        )

    async def refresh_token(self, credentials: Any) -> Any:
        return credentials

    def get_api_key(self, credentials: Any) -> str:
        return str(credentials.access)


# === The case runner ==========================================================


@dataclass
class CaseResult:
    params: Any = None
    response: dict[str, Any] = field(default_factory=dict)
    frames: list[dict[str, Any]] = field(default_factory=list)
    during: dict[str, Any] | None = None


async def _wait(awaitable: Awaitable[Any], what: str, wire: Any) -> Any:
    try:
        return await asyncio.wait_for(awaitable, WAIT_S)
    except TimeoutError:
        tail = json.dumps(wire.records[-5:], default=str)[:2000]
        raise AssertionError(f"timed out waiting for {what}; last records: {tail}") from None


def _matches(record: dict[str, Any], wanted: dict[str, Any]) -> bool:
    return all(record.get(key) == value for key, value in wanted.items())


async def request(wire: Any, request_id: str, method: str, params: Any) -> dict[str, Any]:
    wire.send({"type": "aelix_control", "id": request_id, "method": method, "params": params})
    return await _wait(wire.response(request_id), f"the response to {request_id}", wire)


async def run_case(
    fixture: dict[str, Any],
    case: dict[str, Any],
    wire: Any,
    values: dict[str, Any],
    *,
    override: dict[str, Any] | None = None,
    emit_subagent: Callable[[str, Any], None] | None = None,
    on_answered: Callable[[], None] | None = None,
    prefix: str = "",
) -> CaseResult:
    """Send one case's ``before``, ``during`` and request over ``wire``; collect what came back.

    ``wire`` is anything with ``send``, ``records``, ``response`` and ``record``
    (the in-memory ``Wire`` or the real child's pipes). ``override`` replaces
    params (the ``sensitive`` re-send). ``prefix`` keeps request ids apart when
    several cases share one wire (the real child).

    A ``during`` command with ``"ended_by_case": true`` must be ended by the
    case's own request: its own answer is ``result.during``, read before any
    cleanup. Any ``during`` command still running afterwards (a re-send with
    another ``requestId``, a case without that flag, a failure) is ended by a
    cancel of the runner's own in a ``finally``, and that answer reaches no
    assertion (Codex CX-9).
    """

    result = CaseResult()
    during = case.get("during")
    values.pop("during_id", None)
    failed = True
    try:
        await _send_case(
            fixture, case, wire, values, result, override, emit_subagent, prefix, during
        )
        if on_answered is not None:
            on_answered()
        if during is not None and during.get("ended_by_case") is True and override is None:
            # The case's own request ends the command (its cancel, or the hold its
            # answer released): read the command's OWN answer, before any cleanup
            # could answer for it (Codex CX-9).
            result.during = await _wait(
                wire.response(f"{prefix}during"),
                "the during command's own answer (the case was to end it; no cleanup ran)",
                wire,
            )
        failed = False
    finally:
        # A command the case left running is ended here, whatever happened; its
        # answer is not ``result.during``. On a failure the cleanup keeps quiet,
        # so the failure is what the test reports.
        if values.get("during_id") == f"{prefix}during":
            if failed:
                with contextlib.suppress(Exception):
                    await _end_during(wire, prefix)
            else:
                await _end_during(wire, prefix)
    return result


async def _send_case(
    fixture: dict[str, Any],
    case: dict[str, Any],
    wire: Any,
    values: dict[str, Any],
    result: CaseResult,
    override: dict[str, Any] | None,
    emit_subagent: Callable[[str, Any], None] | None,
    prefix: str,
    during: dict[str, Any] | None,
) -> None:
    for index, step in enumerate(case.get("before", [])):
        answer = await request(
            wire, f"{prefix}before-{index}", step["method"], resolve(step.get("params", {}), values)
        )
        assert answer.get("success") is True, f"before step {index} failed: {answer}"
    if during is not None:
        values["during_id"] = f"{prefix}during"
        wire.send(
            {
                "type": "aelix_control",
                "id": f"{prefix}during",
                "method": during["method"],
                "params": resolve(during.get("params", {}), values),
            }
        )
        await _wait(
            wire.record(lambda r: _matches(r, during["wait_for"])), "the during marker", wire
        )
    trigger = case.get("trigger")
    seen = {id(record) for record in wire.records}
    if trigger is not None and "emit_subagent" in trigger:
        assert emit_subagent is not None, "this transport cannot emit subagent progress"
        emitted = trigger["emit_subagent"]
        emit_subagent(emitted["event"], emitted["value"])
    else:
        call = trigger if trigger is not None else case
        method = call.get("method", fixture.get("method"))
        params = resolve(call.get("params", {}), values)
        if override:
            params = {**params, **resolve(override, values)}
        result.params = params
        wire.send(
            {"type": "aelix_control", "id": f"{prefix}case", "method": method, "params": params}
        )
    family = case.get("frameType", fixture.get("frameType"))
    answered = f"{prefix}case" if trigger is None or "emit_subagent" not in trigger else None

    def next_frame(record: dict[str, Any]) -> bool:
        if record.get("type") == "response" and record.get("id") == answered:
            return True  # answered before the frame came (a refused re-send): stop here
        return id(record) not in seen and record.get("type") == family

    for index, step in enumerate(case.get("frames", [])):
        frame = await _wait(wire.record(next_frame), f"frame {index} of {case['name']}", wire)
        if frame.get("type") == "response":
            break
        seen.add(id(frame))
        result.frames.append(frame)
        reply = step.get("reply")
        if reply is not None:
            wire.send({"type": "extension_ui_response", "id": frame.get("id"), **reply})
    if trigger is None or "emit_subagent" not in trigger:
        result.response = await _wait(
            wire.response(f"{prefix}case"), f"the response to {case['name']}", wire
        )


def _answered(wire: Any, request_id: str) -> bool:
    return any(r.get("type") == "response" and r.get("id") == request_id for r in wire.records)


async def _end_during(wire: Any, prefix: str) -> None:
    """End a ``during`` command the case left running; what it answers reaches no assertion.

    It runs in ``run_case``'s ``finally``: after the case's own answer was read,
    or when the case failed, so a rig never closes over a running command.
    """

    if _answered(wire, f"{prefix}during"):
        return
    wire.send(
        {
            "type": "aelix_control",
            "id": f"{prefix}cleanup",
            "method": "commands/cancel",
            "params": {"requestId": f"{prefix}during"},
        }
    )
    await _wait(wire.response(f"{prefix}during"), "the during command's end (cleanup)", wire)


async def run_in_process(
    fixture: dict[str, Any],
    case: dict[str, Any],
    root: Path,
    *,
    override: dict[str, Any] | None = None,
) -> tuple[CaseResult, Callable[[], None]]:
    """Run one case on a fresh in-process rig; also return its ``check`` bound to that rig."""

    async with open_rig(case["rig"], root, setup=case.get("setup")) as rig:

        def emit(event: str, value: dict[str, Any]) -> None:
            from aelix_coding_agent.subagent_contract import SubagentProgress

            progress = SubagentProgress(**value) if "profile" in value else _Bare(**value)
            rig.runtime.harness.runtime.event_bus.emit(event, progress)

        result = await run_case(
            fixture,
            case,
            rig.wire,
            rig.values,
            override=override,
            emit_subagent=emit,
            on_answered=rig.release,
        )
        name = case.get("check") if override is None else None
        bound = (lambda: CHECKS[name](rig, result)) if name else (lambda: None)
        # A check reads the rig's files, which outlive the rig; run it now, while
        # the values it compares with are certainly the rig's.
        bound()
    return result, bound


class _Bare:
    """A progress value with only the attributes it was given (no parent, no profile)."""

    def __init__(self, **attributes: Any) -> None:
        self.__dict__.update(attributes)


def in_process_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every ``(fixture, case)`` the in-process tests run: not a launch or a source check."""

    return [
        (fixture, case)
        for fixture in load_fixtures()
        for case in fixture["cases"]
        if "launch" not in case and "source" not in case
    ]


def success(case: dict[str, Any]) -> bool:
    return "data" in case or "expect" in case or bool(case.get("frames"))


async def capture(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Every success case's real answer, in process: ``(fixture id, case) -> {data, frames}``.

    ``scripts/check_control_v1.py --update`` turns these into the floor's
    ``forwarded`` paths, and ``test_additive_only.py`` captures again to check them.
    """

    captured: dict[tuple[str, str], dict[str, Any]] = {}
    for number, (fixture, case) in enumerate(in_process_cases()):
        if not success(case):
            continue
        result, _check = await run_in_process(fixture, case, root / f"c{number}")
        captured[(fixture_id(fixture), case["name"])] = {
            "data": copy.deepcopy(result.response.get("data")),
            "frames": copy.deepcopy(result.frames),
        }
    return captured


# === Assertions =================================================================


def envelope_fixture() -> dict[str, Any]:
    return next(f for f in load_fixtures() if "envelope" in f)


_SUBSCHEMA = ("items", "contains", "not", "propertyNames")
_SUBSCHEMAS = ("allOf", "anyOf")


def opened(schema: Any) -> Any:
    """*schema* as a v1 client applies it: an ``"open": true`` enum restricts nothing.

    An open enum lists the values version 1 knows (ADR-0302 D3); a later value
    is an addition a client must accept, so validation drops the ``enum`` there
    and keeps the type. The floor still holds the listed values
    (``scripts/check_control_v1.py``), and a case pins a value it needs with
    ``const`` or ``contains`` (Codex CX-7).
    """

    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "enum" and schema.get("open") is True:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: opened(sub) for name, sub in value.items()}
        elif key in _SUBSCHEMA:
            out[key] = opened(value)
        elif key in _SUBSCHEMAS and isinstance(value, list):
            out[key] = [opened(sub) for sub in value]
        else:
            out[key] = value
    return out


def _validate(schema: dict[str, Any], value: Any, what: str) -> None:
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(opened(schema)).iter_errors(value), key=lambda e: list(e.path)
    )
    if errors:
        shown = json.dumps(value, default=str)
        raise AssertionError(
            f"{what} does not match its schema: {errors[0].message} at {list(errors[0].path)}; "
            f"value: {shown[:1500]}"
        )


def schema_rejects(schema: dict[str, Any], value: Any) -> bool:
    from jsonschema import Draft202012Validator

    return not Draft202012Validator(opened(schema)).is_valid(value)


def assert_case(
    fixture: dict[str, Any], case: dict[str, Any], result: CaseResult, prefix: str = ""
) -> None:
    """The envelope, then the case's ``data`` / ``error`` / ``expect`` and every frame."""

    label = f"{fixture['_file']} {case['name']!r}"
    envelope = envelope_fixture()
    response = result.response
    if response:
        assert response.get("id") == f"{prefix}case", f"{label}: not correlated: {response}"
        shape = envelope["response"]["success" if response.get("success") else "failure"]
        _validate(shape, response, f"{label} envelope")
        if "error" in case:
            assert response["success"] is False, f"{label}: expected a refusal, got {response}"
            _validate(case["error"], response["error"], f"{label} error")
        else:
            assert response["success"] is True, f"{label}: expected success, got {response}"
            _validate(case.get("data", case.get("expect", {})), response["data"], f"{label} data")
    assert len(result.frames) == len(case.get("frames", [])), label
    for index, (frame, step) in enumerate(zip(result.frames, case.get("frames", []), strict=True)):
        if "frame" in fixture:
            _validate(fixture["frame"], frame, f"{label} frame {index} (family)")
        _validate(step["frame"], frame, f"{label} frame {index}")
        if "reply" in step and "reply" in fixture:
            _validate(
                fixture["reply"],
                {"type": "extension_ui_response", "id": frame.get("id"), **step["reply"]},
                f"{label} reply {index}",
            )
