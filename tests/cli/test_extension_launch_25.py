"""#25 review round 1 (verify V5, V6) — the ``extensions`` filter where ``_async_main`` applies it.

The sentinel tests in ``tests/extensions/test_extension_selection_25.py`` call the
loaders themselves, so nine mutations of the call sites survived (verify V6).
These run ``_async_main``:

* ``--print`` in a folder with project extensions: the trust vote load and the
  manifest scan both skip an extension the global file turns off — it is never
  imported and its MCP server is never handed to the manager;
* interactive, with ``run_tui`` stubbed: a ``/reload`` after turning a pack off
  leaves its MCP tool out of the harness, ``/tool`` lists the off extensions, the
  holders stay on the running build when a rebuild raises (F08), and a delegated
  child's environment carries what the parent keeps off (V5);
* a child launched with that record does not import what it names;
* ``_build_harness_options`` does not print load errors under the TUI (D6, V11).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import aelix_coding_agent.cli.entry as entry_mod
import pytest
from aelix_agent_core.types import AgentTool
from aelix_coding_agent.core.extension_selection import (
    EXTENSIONS_PARENT_OFF_ENV,
    ExtensionOffRecord,
    carrier_key,
)

from tests.env_sandbox import sandbox_home
from tests.extensions.test_extension_selection_25 import SENTINEL_BODY, _pack


class _PipedStdin:
    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


class _TTYStdin:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover — never read on a TTY
        return ""


class _Conn:
    def __init__(self, name: str) -> None:
        self.name = name
        self.transport = "stdio"
        self.connected = True


class _Manager:
    """An ``McpClientManager`` double: records what it was handed, connects nothing."""

    handed: list[list[str]] = []

    def __init__(self, contribs: list[Any], **_kw: Any) -> None:
        names = [c.name for c in contribs]
        _Manager.handed.append(names)
        self.connections = {n: _Conn(n) for n in names}
        self.failed = frozenset()
        self.disabled = ()
        self._tools = {n: (AgentTool(name=f"{n}__run", description="mcp"),) for n in names}

    @property
    def tools_by_server(self) -> dict[str, tuple[AgentTool, ...]]:
        return dict(self._tools)

    async def connect_all(self) -> list[Any]:
        return []

    async def collect_agent_tools(self) -> list[AgentTool]:
        return [t for ts in self._tools.values() for t in ts]

    async def disconnect_all(self) -> None:
        return None


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agent = tmp_path / "agent"
    (agent / "extensions").mkdir(parents=True)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.delenv(EXTENSIONS_PARENT_OFF_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.setattr(entry_mod, "McpClientManager", _Manager)
    _Manager.handed = []
    return agent


def _sentinels(agent: Path, mark: Path) -> Path:
    ext = agent / "extensions"
    (ext / "sentlegacy.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    pack = _pack(ext, "sentpack", entry="sentpack_mod_launch_25:setup", mcp="sent-srv")
    (pack / "sentpack_mod_launch_25.py").write_text(SENTINEL_BODY.format(mark=str(mark)))
    return pack


async def test_the_vote_load_and_the_scan_skip_an_extension_turned_off(
    agent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = tmp_path / "IMPORTED"
    pack = _sentinels(agent, mark)
    monkeypatch.syspath_prepend(str(pack))
    (agent / "settings.json").write_text(json.dumps({"extensions": ["-sentlegacy", "-sentpack"]}))
    proj = tmp_path / "proj"
    # A project extension: the folder asks for trust, so the vote load runs.
    (proj / ".aelix" / "extensions").mkdir(parents=True)
    (proj / ".aelix" / "extensions" / "probe.py").write_text("def setup(aelix):\n    pass\n")
    assert entry_mod.has_trust_requiring_project_resources(proj)
    monkeypatch.setattr(sys, "stdin", _PipedStdin())
    monkeypatch.chdir(proj)
    code = await entry_mod._async_main(
        ["--no-session", "--print", "--provider", "anthropic", "--model", "x"]
    )
    assert code in (0, 1)
    assert not mark.exists()
    # The off pack's server never reached the manager (no other server is declared).
    assert all("sent-srv" not in names for names in _Manager.handed)


async def test_a_reload_hides_the_off_packs_tools_and_keeps_the_running_build_on_a_raise(
    agent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_agents.print_channel import build_child_env

    from tests.agents_ext.test_print_channel_spawn import _profile

    _pack(agent / "extensions", "owner", mcp="shared", hooks=True)
    (agent / "extensions" / "weather.py").write_text("def setup(aelix):\n    pass\n")
    settings = agent / "settings.json"
    settings.write_text("{}")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "stdin", _TTYStdin())
    seen: dict[str, Any] = {}

    async def _stub_run_tui(runtime: Any, **kw: Any) -> int:
        control = kw["extension_control"]
        tools = kw["tool_control"]
        seen["launch"] = [t.name for t in runtime.harness.state.tools]
        settings.write_text(json.dumps({"extensions": ["-owner", "-weather"]}))
        await runtime.reload()
        seen["after"] = [t.name for t in runtime.harness.state.tools]
        committed = control.inventory()
        seen["hidden"] = committed.mcp_hidden
        seen["placeholders"] = sorted(
            i.label for i in tools.kind.items() if i.key.startswith("ext-off:")
        )
        env = build_child_env(_profile(), base={})
        seen["child"] = ExtensionOffRecord.from_env(env.get(EXTENSIONS_PARENT_OFF_ENV))
        # A rebuild that raises after the options were built publishes nothing.
        real = entry_mod.apply_post_registration_tool_policy

        async def _raise(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("late build failure")

        monkeypatch.setattr(entry_mod, "apply_post_registration_tool_policy", _raise)
        settings.write_text("{}")
        try:
            await runtime.reload()
        except RuntimeError:
            seen["raised"] = True
        monkeypatch.setattr(entry_mod, "apply_post_registration_tool_policy", real)
        seen["kept"] = control.inventory() is committed
        return 0

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    code = await entry_mod._async_main(["--no-session"])
    assert code == 0
    assert "shared__run" in seen["launch"]
    assert "shared__run" not in seen["after"]
    assert seen["hidden"] == {"shared"}
    assert seen["placeholders"] == ["owner", "weather.py"]
    assert seen["raised"] and seen["kept"]
    child: ExtensionOffRecord = seen["child"]
    assert carrier_key(agent.resolve() / "extensions" / "weather.py") in child.keys
    assert carrier_key(agent.resolve() / "extensions" / "owner") in child.keys


async def test_a_child_does_not_import_what_its_parent_kept_off(
    agent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = tmp_path / "IMPORTED"
    pack = _sentinels(agent, mark)
    monkeypatch.syspath_prepend(str(pack))
    record = ExtensionOffRecord(
        frozenset(
            {
                str(carrier_key(agent.resolve() / "extensions" / "sentlegacy.py")),
                str(carrier_key(agent.resolve() / "extensions" / "sentpack")),
            }
        )
    )
    # A key this child never discovers: it still passes it on to its own children.
    record = record | ExtensionOffRecord(frozenset({"path:/nowhere/else.py"}))
    monkeypatch.setenv(EXTENSIONS_PARENT_OFF_ENV, record.to_env())
    work = tmp_path / "work"
    # A project extension: the folder asks for trust, so the vote load runs too.
    (work / ".aelix" / "extensions").mkdir(parents=True)
    (work / ".aelix" / "extensions" / "probe.py").write_text("def setup(aelix):\n    pass\n")
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "stdin", _PipedStdin())
    published: list[Any] = []
    real_set = entry_mod.set_launch_extension_off

    def _spy(value: Any) -> None:
        published.append(value)
        real_set(value)

    monkeypatch.setattr(entry_mod, "set_launch_extension_off", _spy)
    code = await entry_mod._async_main(
        ["--no-session", "--print", "--provider", "anthropic", "--model", "x"]
    )
    assert code in (0, 1)
    assert not mark.exists()
    assert all("sent-srv" not in names for names in _Manager.handed)
    # Read once and removed: the child's own tools do not hand it on.
    assert EXTENSIONS_PARENT_OFF_ENV not in os.environ
    # What it records for its own children: its own off-set and what it inherited.
    built = [r for r in published if r is not None]
    assert built and record.keys <= built[-1].keys


async def test_load_errors_are_not_printed_under_the_tui(
    agent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aelix_agent_core.session import MemorySessionStorage, Session
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.cli.args import Args

    (agent / "extensions" / "broken.py").write_text(
        "def setup(aelix):\n    raise RuntimeError('boom')\n"
    )
    monkeypatch.chdir(tmp_path)
    manager = SettingsManager.create(cwd=str(tmp_path), agent_dir=agent, project_trusted=True)

    async def build(**kw: Any) -> str:
        await entry_mod._build_harness_options(
            Args(), Session(MemorySessionStorage()), settings_manager=manager, **kw
        )
        return capsys.readouterr().err

    assert "Warning: extension load:" in await build(app_mode="print")
    # D6: a /reload under the TUI commits its line instead.
    assert "Warning: extension load:" not in await build(app_mode="interactive", on_reload=True)
    # V11: so do /new, /fork and /resume once the TUI is up.
    assert "Warning: extension load:" not in await build(
        app_mode="interactive", quiet_load_errors=True
    )
