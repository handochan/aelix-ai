"""#511 (ADR-0279 D11; Codex C21) — the panel's own save and close lifecycle.

Driven through the panel's real key bindings over a recording chrome: a save
that fails keeps the panel open with its edits; Ctrl+J saves like Enter and
Ctrl+C discards like Esc; and a close unmounts INSIDE the one
``print_above_many`` that prints its summary, so the summary and the panel's
removal are one frame.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_coding_agent.core.resource_selection import ResourceSelection, SettingsLists
from aelix_coding_agent.core.skill_selection import SkillKind, SkillSource, load_skill_catalog
from aelix_coding_agent.tui.selection_panel import run_selection_panel
from prompt_toolkit.key_binding.key_bindings import _parse_key


class _Chrome:
    scrollback_columns = 80
    buffer = None

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.content: Any = None
        size = SimpleNamespace(rows=24, columns=80)
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

    async def print_above_many(self, renderables: Any, *, apply_before_redraw: Any = None) -> None:
        self.events.append(("print", [getattr(r, "plain", str(r)) for r in renderables]))
        if apply_before_redraw is not None:
            apply_before_redraw()
        self.events.append("redraw")


class _Harness:
    is_idle = True

    def __init__(self) -> None:
        self.skills: list[Any] = []
        self.state = SimpleNamespace(resources={}, active_tool_names=None, tools=[])

    def set_skills(self, skills: list[Any]) -> None:
        self.skills = list(skills)

    def rebuild_system_prompt(self) -> None:
        return None

    async def set_resources(self, resources: dict[str, Any]) -> None:
        self.state.resources = resources


def _setup(
    tmp_path: Path, global_text: str | None
) -> tuple[ResourceSelection, SkillKind, InMemorySettingsStorage]:
    root = tmp_path / "skills"
    for name in ("deploy", "pdf"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb\n")
    storage = InMemorySettingsStorage()
    if global_text is not None:
        storage.with_lock("global", lambda _c: global_text)
    manager = SettingsManager.from_storage(storage)
    holder = {"result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())}
    harness = _Harness()
    harness.skills = list(holder["result"].skills)
    kind = SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager,
        global_base=str(tmp_path),
        project_base=str(tmp_path / ".aelix"),
        project_trusted=True,
    )
    return selection, kind, storage


async def _open(
    chrome: _Chrome, selection: ResourceSelection, kind: SkillKind
) -> tuple[asyncio.Task[None], list[Any]]:
    commits: list[Any] = []
    ctx = SimpleNamespace(chrome=chrome, commit=commits.append)
    task = asyncio.ensure_future(run_selection_panel(ctx, selection, kind, title="Skills"))
    for _ in range(20):
        await asyncio.sleep(0)
        if chrome.content is not None:
            break
    assert chrome.content is not None, "the panel never mounted"
    return task, commits


async def _press(chrome: _Chrome, key: str) -> None:
    bindings = chrome.content.content.get_key_bindings()
    wanted = (_parse_key(key),)
    handlers = [b.handler for b in bindings.bindings if b.keys == wanted]
    assert handlers, f"no binding for {key}"
    handlers[0](SimpleNamespace(data=key))
    for _ in range(20):
        await asyncio.sleep(0)


def _model(chrome: _Chrome) -> Any:
    return chrome.content.content.model


@pytest.mark.parametrize("enter", ["enter", "c-j"])
async def test_a_failed_save_keeps_the_panel_open_with_its_edits(
    tmp_path: Path, enter: str
) -> None:
    selection, kind, storage = _setup(tmp_path, "{ broken")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    model = _model(chrome)
    # The global file did not load, so the control starts on (and stays on) a
    # scope it can tell about; force the edit to the unwritable global scope.
    model.target = "global"
    await _press(chrome, "space")
    assert model.edits
    await _press(chrome, enter)
    assert not task.done()
    assert model.edits and model.message is not None and model.message[0] == "error"
    assert "unmount" not in chrome.events
    assert storage.global_content == "{ broken"
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.parametrize("enter", ["enter", "c-j"])
async def test_a_save_closes_in_the_same_frame_as_its_summary(tmp_path: Path, enter: str) -> None:
    selection, kind, storage = _setup(tmp_path, "{}")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    await _press(chrome, "space")  # deploy off
    await _press(chrome, enter)
    await asyncio.wait_for(task, timeout=2)
    printed = [i for i, e in enumerate(chrome.events) if isinstance(e, tuple)]
    assert len(printed) == 1
    i = printed[0]
    # unmount and refocus happen INSIDE the print, before its one redraw.
    assert chrome.events[i + 1 : i + 4] == ["unmount", "focus_input", "redraw"]
    assert "saved to" in chrome.events[i][1][0]
    assert '"-deploy"' in (storage.global_content or "")


@pytest.mark.parametrize("cancel", ["escape", "c-c"])
async def test_cancel_discards_and_closes_in_one_frame(tmp_path: Path, cancel: str) -> None:
    selection, kind, storage = _setup(tmp_path, "{}")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    await _press(chrome, "space")
    await _press(chrome, cancel)
    await asyncio.wait_for(task, timeout=2)
    (event,) = [e for e in chrome.events if isinstance(e, tuple)]
    assert event[1] == ["Skills: 1 unsaved change discarded."]
    assert storage.global_content == "{}"


async def test_toggling_twice_then_enter_writes_nothing(tmp_path: Path) -> None:
    """C19: the promise is about edits before Enter — no write, not even a reformat."""

    selection, kind, storage = _setup(tmp_path, '{"theme":"dark"}')
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    await _press(chrome, "space")
    await _press(chrome, "space")
    await _press(chrome, "enter")
    await asyncio.wait_for(task, timeout=2)
    assert storage.global_content == '{"theme":"dark"}'


def _press_now(chrome: _Chrome, key: str) -> None:
    """A key handler run with NO yield after it — keys buffered in one read."""

    bindings = chrome.content.content.get_key_bindings()
    wanted = (_parse_key(key),)
    handler = next(b.handler for b in bindings.bindings if b.keys == wanted)
    handler(SimpleNamespace(data=key))


async def test_buffered_space_enter_esc_saves_once_and_says_nothing_was_discarded(
    tmp_path: Path,
) -> None:
    """Codex X5: Esc used to run before the save task claimed the panel."""

    selection, kind, storage = _setup(tmp_path, "{}")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    _press_now(chrome, "space")
    _press_now(chrome, "enter")
    _press_now(chrome, "enter")  # a repeated Enter schedules no second save
    _press_now(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)
    printed = [e[1] for e in chrome.events if isinstance(e, tuple)]
    assert len(printed) == 1 and "saved to" in printed[0][0]
    assert not any("discarded" in line for lines in printed for line in lines)
    assert json.loads(storage.global_content or "{}") == {"skills": ["-deploy"]}


# --- round 2 (R2: X5b, X8b) ------------------------------------------------------


async def test_a_save_claimed_by_enter_does_nothing_once_the_panel_is_closing(
    tmp_path: Path,
) -> None:
    """X5b: ``_enter`` re-checks ``closing`` when it starts.

    Enter claims the panel synchronously and spawns the save; if the panel was
    marked closing before that task ran, the task must neither save nor print
    a summary for a panel that is going away.
    """

    selection, kind, storage = _setup(tmp_path, "{}")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    model = _model(chrome)
    _press_now(chrome, "space")
    _press_now(chrome, "enter")
    model.closing = True  # closed before the save task got to run
    for _ in range(20):
        await asyncio.sleep(0)
    assert json.loads(storage.global_content or "{}") == {}
    assert not any(isinstance(e, tuple) for e in chrome.events)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_panel_too_small_for_one_row_refuses_every_edit_key(tmp_path: Path) -> None:
    """X8b: with no list row on screen, nothing may change unseen — Esc still closes."""

    selection, kind, storage = _setup(tmp_path, "{}")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    model = _model(chrome)
    size = chrome.app.output.get_size()
    size.rows = 9  # the panel's cap falls to two rows: no list row is left
    layout = chrome.content.content.current_layout()
    assert layout.height < layout.fixed_rows + 1
    cursor = model.cursor
    for key in ("down", "space", "enter", "c-j", "end", "right"):
        await _press(chrome, key)
    bindings = chrome.content.content.get_key_bindings()
    typed = next(b.handler for b in bindings.bindings if b.keys == (_parse_key("<any>"),))
    typed(SimpleNamespace(data="p"))
    assert model.edits == {} and model.cursor == cursor and model.target == "global"
    assert model.search == ""
    assert json.loads(storage.global_content or "{}") == {}
    size.rows = 24  # room again: the same keys work
    await _press(chrome, "space")
    assert model.edits
    await _press(chrome, "escape")
    await asyncio.wait_for(task, timeout=2)
    assert json.loads(storage.global_content or "{}") == {}
