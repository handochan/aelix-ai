"""#25 (ADR-0284 D8; Codex C21) — the ``/extension`` Installed tab as an on/off panel.

Driven through ``tabbed``'s real key bindings over a recording chrome:

* Tab / Shift-Tab switch tabs; ←/→ is the "Save to" control on Installed only
  and still switches tabs on Discover and Sources;
* typing searches on Installed (``q`` included); ``q`` still closes Sources;
* unsaved edits survive a tab switch, and Esc on another tab says they were discarded;
* a failed Enter keeps the modal open on Installed with its edits;
* a save closes in ONE ``print_above_many`` with its summary and the reload note;
* every tab draws the same number of rows, none wider than the terminal.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_coding_agent.core.extension_selection import (
    ExtensionControl,
    ExtensionKind,
    build_inventory,
)
from aelix_coding_agent.core.resource_selection import ResourceSelection
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.extension_manager import run_extension_manager
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.width import cells_by_cluster
from prompt_toolkit.key_binding.key_bindings import _parse_key

from tests.env_sandbox import sandbox_home

_SGR = re.compile(r"\x1b\[[0-9;]*m")


class _Chrome:
    scrollback_columns = 80
    buffer = None

    def __init__(self, rows: int = 24, columns: int = 80) -> None:
        self.events: list[Any] = []
        self.content: Any = None
        size = SimpleNamespace(rows=rows, columns=columns)
        self.app = SimpleNamespace(output=SimpleNamespace(get_size=lambda: size))

    def mount_modal(self, _content: Any, *, focus: Any = None) -> None:
        self.events.append("mount")

    def unmount_modal(self, _content: Any = None) -> None:
        self.events.append("unmount")

    def is_modal_open(self) -> bool:
        return False  # #701: show_modal refocuses the input when no modal is left

    def focus(self, target: Any) -> None:
        self.content = target

    def focus_input(self) -> None:
        self.events.append("focus_input")

    def invalidate(self) -> None:
        return None

    def set_footer_line(self, *_a: Any, **_k: Any) -> None:
        return None

    async def print_above_many(self, renderables: Any, *, apply_before_redraw: Any = None) -> None:
        self.events.append(("print", [getattr(r, "plain", str(r)) for r in renderables]))
        if apply_before_redraw is not None:
            apply_before_redraw()
        self.events.append("redraw")


async def _inventory(home: Path) -> Any:
    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "weather.py").write_text("def setup(aelix):\n    pass\n")
    (ext / "lint.py").write_text("def setup(aelix):\n    pass\n")
    loaded = await discover_and_load_extensions([], cwd=home, agent_dir=home / "agent")
    return build_inventory(loaded)


async def _open(
    home: Path,
    global_text: str = "{}",
    *,
    rows: int = 24,
    width: int = 80,
    project_trusted: bool = True,
    inventory: Any = None,
    **kind_kw: Any,
) -> tuple[_Chrome, asyncio.Task[None], InMemorySettingsStorage, list[Any]]:
    inventory = inventory if inventory is not None else await _inventory(home)
    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda _c: global_text)
    manager = SettingsManager.from_storage(storage)
    control = ExtensionControl(
        selection=ResourceSelection(
            manager,
            global_base=str(home / "agent"),
            project_base=str(home / ".aelix"),
            project_trusted=project_trusted,
        ),
        kind=ExtensionKind(inventory=lambda: inventory, agent_dir=str(home / "agent"), **kind_kw),
        holder={"inventory": inventory},
    )
    chrome = _Chrome(rows=rows, columns=width)
    ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))  # type: ignore[arg-type]
    commits: list[Any] = []
    task = asyncio.ensure_future(
        run_extension_manager(
            extensions=[],
            mcp_manager=None,
            tabbed=ctx.tabbed,
            commit=commits.append,
            control=control,
            width=lambda: width,
        )
    )
    for _ in range(50):
        await asyncio.sleep(0)
        if chrome.content is not None:
            break
    assert chrome.content is not None, "the modal never mounted"
    return chrome, task, storage, commits


async def _press(chrome: _Chrome, key: str, data: str | None = None) -> None:
    bindings = chrome.content.content.get_key_bindings()
    wanted = (_parse_key(key),)
    handlers = [b.handler for b in bindings.bindings if b.keys == wanted]
    assert handlers, f"no binding for {key}"
    handlers[0](SimpleNamespace(data=data if data is not None else key))
    for _ in range(30):
        await asyncio.sleep(0)


def _frame(chrome: _Chrome) -> list[str]:
    return _SGR.sub("", chrome.content.content.text().value).split("\n")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sandbox_home(monkeypatch, tmp_path / "home")
    return tmp_path


async def test_tab_switches_and_left_right_is_save_to_on_installed_only(home: Path) -> None:
    chrome, task, _storage, _c = await _open(home)
    assert "[Installed]" in _frame(chrome)[0] and "Save to [Global]" in _frame(chrome)[0]
    await _press(chrome, "right")
    assert "[This project]" in _frame(chrome)[0]  # the scope moved, the tab did not
    await _press(chrome, "tab")
    assert "[Discover]" in _frame(chrome)[0] and "Save to" not in _frame(chrome)[0]
    await _press(chrome, "right")
    assert "[Sources]" in _frame(chrome)[0]  # Discover keeps ←/→ as tab switching
    await _press(chrome, "s-tab")
    await _press(chrome, "s-tab")
    assert "[Installed]" in _frame(chrome)[0]
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)


async def test_q_searches_on_installed_and_closes_sources(home: Path) -> None:
    chrome, task, _storage, _c = await _open(home)
    await _press(chrome, "q", "q")
    assert "Search: q" in _frame(chrome)[1]
    assert not task.done()
    await _press(chrome, "backspace")
    await _press(chrome, "tab")
    await _press(chrome, "tab")
    assert "[Sources]" in _frame(chrome)[0]
    await _press(chrome, "q", "q")
    await asyncio.wait_for(task, timeout=2)


async def test_unsaved_edits_survive_a_tab_switch_and_esc_elsewhere_says_so(home: Path) -> None:
    chrome, task, storage, _c = await _open(home)
    await _press(chrome, "end")
    await _press(chrome, "space")
    assert any("* unsaved" in line for line in _frame(chrome))
    await _press(chrome, "tab")
    await _press(chrome, "s-tab")
    assert any("* unsaved" in line for line in _frame(chrome))
    await _press(chrome, "tab")
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)
    (printed,) = [e for e in chrome.events if isinstance(e, tuple)]
    assert printed[1] == ["Extensions: 1 unsaved change discarded."]
    assert storage.global_content == "{}"


async def test_a_failed_enter_keeps_the_modal_open_on_installed(home: Path) -> None:
    # The global file did not load and the project is not trusted: no scope
    # is writable, so the save is refused and says why.
    chrome, task, storage, _c = await _open(home, "{ broken", project_trusted=False)
    await _press(chrome, "end")
    await _press(chrome, "space")
    await _press(chrome, "enter")
    assert not task.done()
    frame = _frame(chrome)
    assert "[Installed]" in frame[0]
    assert any("Not saved" in line for line in frame)
    assert any("* unsaved" in line for line in frame)
    assert "unmount" not in chrome.events
    assert storage.global_content == "{ broken"
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)


async def test_a_save_closes_in_one_frame_with_the_reload_note(home: Path) -> None:
    chrome, task, storage, _c = await _open(home)
    await _press(chrome, "end")  # weather.py, the last row
    await _press(chrome, "space")
    await _press(chrome, "enter")
    await asyncio.wait_for(task, timeout=2)
    printed = [i for i, e in enumerate(chrome.events) if isinstance(e, tuple)]
    assert len(printed) == 1
    i = printed[0]
    assert chrome.events[i + 1 : i + 4] == ["unmount", "focus_input", "redraw"]
    text = "\n".join(chrome.events[i][1])
    assert "Extensions saved to" in text and "off  weather.py" in text
    assert "/reload (or a restart) loads the new set" in text
    assert json.loads(storage.global_content or "{}")["extensions"] == ["-weather"]


@pytest.mark.parametrize(("rows", "width"), [(24, 50), (24, 80), (40, 120)])
async def test_every_tab_draws_the_same_height_never_wider(
    home: Path, rows: int, width: int
) -> None:
    chrome, task, _storage, _c = await _open(home, rows=rows, width=width)
    heights = set()
    for _ in range(3):
        frame = _frame(chrome)
        heights.add(len(frame))
        assert all(cells_by_cluster(line) <= width for line in frame), frame
        await _press(chrome, "tab")
    assert len(heights) == 1
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)


# --- review round 1 (V9, V10, V12) ------------------------------------------------------


async def _mcp_inventory(home: Path) -> Any:
    from tests.extensions.test_extension_selection_25 import _pack

    ext = home / "agent" / "extensions"
    _pack(ext, "jira-tools-with-a-long-plugin-id", mcp="jira", hooks=True)
    loaded = await discover_and_load_extensions([], cwd=home, agent_dir=home / "agent")
    return build_inventory(loaded)


async def test_a_restart_note_wraps_under_its_own_indent_at_50_columns(home: Path) -> None:
    """Verify V9: the note's rest started at column 0 at 50 columns."""

    inventory = await _mcp_inventory(home)
    chrome, task, _storage, _c = await _open(home, width=50, inventory=inventory)
    await _press(chrome, "end")  # the MCP row, read-only
    await _press(chrome, "up")  # the pack
    await _press(chrome, "space")
    await _press(chrome, "enter")
    await asyncio.wait_for(task, timeout=2)
    (printed,) = [e for e in chrome.events if isinstance(e, tuple)]
    lines = "\n".join(printed[1]).split("\n")
    start = next(i for i, line in enumerate(lines) if "jira-tools-with-a-long-plugin-id:" in line)
    note = lines[start:]
    assert len(note) >= 2, note
    assert note[0].startswith("  jira-tools") and all(n.startswith("    ") for n in note[1:])
    assert all(cells_by_cluster(line) <= 50 for line in lines), lines


@pytest.mark.parametrize("width", [50, 80])
async def test_a_reload_failure_banner_wraps_instead_of_being_cut(home: Path, width: int) -> None:
    """Verify V10: the banner's reason was cut at 50 and 80 columns."""

    failure = (
        "✖ /reload failed during the rebuild: RuntimeError: a factory error with a long "
        "message — kept the previous settings; fix the cause, then /reload again"
    )
    chrome, task, _storage, _c = await _open(
        home, width=width, last_reload_failure=lambda: failure
    )
    heights = set()
    for _ in range(3):
        frame = _frame(chrome)
        heights.add(len(frame))
        assert all(cells_by_cluster(line) <= width for line in frame), frame
        await _press(chrome, "tab")
    assert len(heights) == 1
    joined = " ".join(" ".join(_frame(chrome)).split())
    assert "kept the previous settings; fix the cause, then /reload again" in joined
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)


async def test_a_search_counts_the_mcp_row_it_shows(home: Path) -> None:
    """Verify V12: "0 matches" over a listed MCP server."""

    inventory = await _mcp_inventory(home)
    statuses = [SimpleNamespace(name="demo-srv", transport="stdio", state="connected")]
    chrome, task, _storage, _c = await _open(
        home, inventory=inventory, mcp_statuses=lambda: statuses
    )
    for char in "demo-srv":
        await _press(chrome, "<any>", char)
    frame = _frame(chrome)
    assert "1 match" in frame[1] and "0 matches" not in frame[1], frame[1]
    assert any("demo-srv" in line for line in frame[2:])
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)


# --- #683: a locked row that failed to load ------------------------------------


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("raise RuntimeError('cli boom')", "RuntimeError: cli boom"),
        ("import sys; sys.exit(1)", "SystemExit: sys.exit(1)"),
    ],
)
async def test_a_failed_e_extension_says_failed_and_its_load_error(
    home: Path, body: str, error: str
) -> None:
    """#683: the ``-e`` lock's tag and line hid the load error a global row shows."""

    from aelix_coding_agent.tui.selection_panel import _RED

    ext = home / "agent" / "extensions"
    ext.mkdir(parents=True)
    (ext / "globalbroken.py").write_text(
        "def setup(aelix):\n    raise RuntimeError('global boom')\n"
    )
    cli = home / "cli"
    cli.mkdir()
    (cli / "clibroken.py").write_text(f"def setup(aelix):\n    {body}\n")
    (cli / "cliok.py").write_text("def setup(aelix):\n    pass\n")
    loaded = await discover_and_load_extensions(
        [str(cli / "clibroken.py"), str(cli / "cliok.py")], cwd=home, agent_dir=home / "agent"
    )
    chrome, task, _storage, _c = await _open(home, inventory=build_inventory(loaded))
    seen: dict[str, tuple[str, str]] = {}
    for _ in range(12):
        frame = _frame(chrome)
        cursor = next(line for line in frame if line.lstrip().startswith("▸"))
        label = next(
            (n for n in ("clibroken.py", "cliok.py", "globalbroken.py") if n in cursor), None
        )
        if label is not None and label not in seen:
            raw = chrome.content.content.text().value.split("\n")
            [state] = [r for r in raw if _SGR.sub("", r).strip().startswith(("Locked", "Saved"))]
            seen[label] = (cursor.rstrip(), state)
        if len(seen) == 3:
            break
        await _press(chrome, "down")
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)

    row, state = seen["clibroken.py"]
    assert row.endswith(" failed"), row
    assert _SGR.sub("", state).strip() == (
        f"Locked on: loaded by -e for this run · failed to load: {error}"
    )
    assert _RED in state
    # The -e extension that loaded, and the failed global one, are as before.
    row, state = seen["cliok.py"]
    assert row.endswith(" -e"), row
    assert _SGR.sub("", state).strip() == "Locked on: loaded by -e for this run"
    assert _RED not in state
    row, state = seen["globalbroken.py"]
    assert row.endswith(" failed"), row
    assert _SGR.sub("", state).strip() == "Saved on · failed to load: RuntimeError: global boom"


def test_every_lock_kind_shows_a_load_error_it_carries() -> None:
    """#683, the other run locks: a built-in that failed reads ``failed`` too."""

    from aelix_coding_agent.core.resource_selection import (
        ItemState,
        Lock,
        ResourceItem,
        ScopeStatus,
        Snapshot,
    )
    from aelix_coding_agent.tui.selection_panel import SelectionModel, plan_layout, render_panel

    def item(key: str) -> ResourceItem:
        return ResourceItem(
            kind="extension", key=key, label=key, identities=(key,), group="g", group_label="G"
        )

    states = [
        ItemState(
            item("guardrail"),
            True,
            True,
            None,
            Lock(True, "always on (Guardrail gate)", tag="always on", kind="always_on"),
            problem="RuntimeError: gate",
        ),
        ItemState(
            item("agents"),
            True,
            False,
            None,
            Lock(False, "follows [features] agents (/settings)", tag="/settings", kind="always_on"),
            problem="ImportError: agents",
        ),
        ItemState(
            item("status"),
            True,
            True,
            None,
            Lock(True, "always on (aelix_status)", tag="always on", kind="always_on"),
        ),
    ]
    scopes = {
        "global": ScopeStatus("global", "/g/settings.json", True),
        "project": ScopeStatus("project", "/p/.aelix/settings.json", True),
    }
    model = SelectionModel(
        Snapshot("extension", tuple(states), scopes), "Extensions", ("extension", "extensions")
    )
    expected = {
        "guardrail": (
            "failed",
            "Locked on: always on (Guardrail gate) · failed to load: RuntimeError: gate",
        ),
        "agents": (
            "failed",
            "Locked off: follows [features] agents (/settings) · failed to load: ImportError: agents",
        ),
        "status": ("always on", "Locked on: always on (aelix_status)"),
    }
    for index, (key, (tag, line)) in enumerate(expected.items()):
        model.cursor = index
        frame = [_SGR.sub("", r) for r in render_panel(model, plan_layout(model, 110, 20))]
        [row] = [r for r in frame if f" {key} " in r and r.lstrip().startswith(("▸", "("))]
        assert row.rstrip().endswith(f" {tag}"), row
        assert any(r.strip() == line for r in frame), "\n".join(frame)
