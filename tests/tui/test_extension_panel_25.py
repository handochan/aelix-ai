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

    def mount_modal(self, _content: Any) -> None:
        self.events.append("mount")

    def unmount_modal(self) -> None:
        self.events.append("unmount")

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
) -> tuple[_Chrome, asyncio.Task[None], InMemorySettingsStorage, list[Any]]:
    inventory = await _inventory(home)
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
        kind=ExtensionKind(inventory=lambda: inventory, agent_dir=str(home / "agent")),
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
