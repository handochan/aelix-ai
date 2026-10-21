"""#380 — ``/trust`` shows what pi's trust selector shows, not the start-up risk text.

Owner decision 2026-10-21: as pi (``trust-selector.ts`` at ``fdce48eb4``). On
main ``27fa21c5`` ``/trust`` passed ``format_project_trust_prompt`` to aelix's
own picker frame, which does not wrap, so a pty at 120 columns showed the
heading, the path and the first row of the risk paragraph cut at ``on your m``;
the skills and settings clauses never reached the screen
(``.omc/probes/b4-b-live/380/before/fresh.120.2_trust_open.screen``), and it
never said what was saved. pi's ``TrustSelectorComponent`` shows the project
path, ``Saved decision: <trusted|untrusted> (<path>)`` or ``(inherited from
<path>)``, ``Current session: trusted|untrusted`` and the options with ``✓`` on
the saved one, the cursor on it.

pi's ``Trust parent folder`` also writes ``{path: cwd, decision: null}``: it
deletes the folder's own entry. aelix wrote the parent only, so after a saved
``Do not trust`` on the folder, ``/trust`` → ``Trust parent folder`` said
"Project trusted (saved)" and the folder stayed untrusted
(``before/saved-untrusted-pick.120.raw``'s trust.json: parent ``true``, folder
``false``).

This is the one exception to #399's rule that aelix's own pickers draw byte
for byte as main did: the frame is still ``own=True``'s, only what ``/trust``
puts in it changed (``tests/tui/test_select_title_held.py`` keeps the frame).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli.project_trust import (
    ProjectTrustStore,
    ProjectTrustStoreEntry,
    format_trust_decision,
    project_trust_options,
    saved_trust_option,
    trust_option_updates,
    trust_selector_title,
)
from aelix_coding_agent.tui.width import cells_by_cluster

from tests.tui.test_select_title_held import _Dialog


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "work" / "repo"
    (project / ".aelix").mkdir(parents=True)
    (project / ".aelix" / "settings.json").write_text("{}", encoding="utf-8")
    return project


def _key(path: Path) -> str:
    return ProjectTrustStore.canonical(path)


# === What the rows say ======================================================


@pytest.mark.parametrize(
    ("where", "decision", "words", "marked"),
    [
        (None, None, "none", None),
        ("cwd", True, "trusted ({cwd})", "Trust"),
        ("cwd", False, "untrusted ({cwd})", "Do not trust"),
        ("parent", True, "trusted (inherited from {parent})", "parent"),
        ("parent", False, "untrusted (inherited from {parent})", None),
        ("grandparent", True, "trusted (inherited from {grandparent})", None),
    ],
)
def test_the_saved_decision_and_its_mark_follow_pi(
    tmp_path: Path, where: str | None, decision: bool | None, words: str, marked: str | None
) -> None:
    cwd = _project(tmp_path)
    dirs = {"cwd": cwd, "parent": cwd.parent, "grandparent": cwd.parent.parent}
    store = ProjectTrustStore(tmp_path / "agent")
    if where is not None:
        assert decision is not None
        store.set(dirs[where], decision)
    entry = store.get_entry(cwd)
    keys = {name: _key(path) for name, path in dirs.items()}
    assert format_trust_decision(cwd, entry) == words.format(**keys)

    labels = project_trust_options(cwd, include_session_only=False)
    expected = labels[1] if marked == "parent" else marked
    assert saved_trust_option(cwd, labels, entry) == expected

    title = trust_selector_title(cwd, entry, session_trusted=bool(decision))
    assert title.split("\n") == [
        "Project trust",
        str(cwd),
        f"Saved decision: {words.format(**keys)}",
        f"Current session: {'trusted' if decision else 'untrusted'}",
    ]
    # The start-up prompt's risk text is not repeated here (owner decision).
    assert "arbitrary code" not in title


def test_get_entry_names_the_directory_that_decided(tmp_path: Path) -> None:
    cwd = _project(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    assert store.get_entry(cwd) is None
    store.set(cwd.parent, True)
    assert store.get_entry(cwd) == ProjectTrustStoreEntry(path=_key(cwd.parent), decision=True)
    store.set(cwd, False)
    assert store.get_entry(cwd) == ProjectTrustStoreEntry(path=_key(cwd), decision=False)
    assert store.get(cwd) is False


def test_trusting_the_parent_clears_the_folders_own_entry(tmp_path: Path) -> None:
    cwd = _project(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    store.set(cwd, False)
    parent_label = project_trust_options(cwd, include_session_only=False)[1]
    assert trust_option_updates(parent_label, cwd) == [(cwd.parent, True), (cwd, None)]
    store.set_many(trust_option_updates(parent_label, cwd))
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved == {_key(cwd.parent): True}
    assert store.get(cwd) is True


def test_the_other_options_write_what_they_did(tmp_path: Path) -> None:
    cwd = _project(tmp_path)
    assert trust_option_updates("Trust", cwd) == [(cwd, True)]
    assert trust_option_updates("Do not trust", cwd) == [(cwd, False)]
    assert trust_option_updates("Trust (this session only)", cwd) == []
    assert trust_option_updates("Do not trust (this session only)", cwd) == []


# === /trust in the running TUI ==============================================


@pytest.mark.parametrize(("session_trusted", "width"), [(False, 80), (True, 120)])
async def test_slash_trust_shows_pi_s_rows_and_saves_pi_s_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_trusted: bool, width: int
) -> None:
    import aelix_coding_agent.tui.shell as shell_mod
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.tui.shell import run_tui

    def _columns(chrome: Any, *, max_width: int | None = None) -> int:
        # The test output reports 80 columns; this terminal has *width*.
        del chrome
        return min(width, max_width) if max_width is not None else width

    monkeypatch.setattr(shell_mod, "terminal_columns", _columns)

    from tests.tui.test_run_tui_smoke import _spy_commits
    from tests.tui.test_run_tui_statusline_settings import _harness_chrome, _quit_within, _wait

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    project = _project(tmp_path / ("a-long-directory-name-" * 3))
    ProjectTrustStore(agent_dir).set(project, False)

    offered: list[tuple[str, list[str], int]] = []
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(project),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=SettingsManager.in_memory(project_trusted=session_trusted),
            )
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.runtime.bound))
        ctx = runtime.harness.runtime.bound[0]

        async def _select(
            title: str, options: list[str], *_a: Any, initial_index: int = 0, **_kw: Any
        ) -> str:
            offered.append((title, list(options), initial_index))
            return options[1]  # Trust parent folder

        ctx.select = _select  # type: ignore[method-assign]
        pipe.send_text("/trust\n")
        await _wait(lambda: any("Project trusted" in c for c in commits))
        pipe.send_text("/quit\n")
        code = await _quit_within(task)

    assert code == 0
    title, options, initial = offered[0]
    lines = [
        "Project trust",
        str(project),
        f"Saved decision: untrusted ({_key(project)})",
        f"Current session: {'trusted' if session_trusted else 'untrusted'}",
    ]
    # Cut to the terminal's width (pi wraps): every row fits, the rows are the
    # lines with nothing dropped, and a row is as wide as the terminal allows,
    # not a fixed width. The path is wider than the screen, so the saved
    # decision, which repeats it, takes more than one row.
    assert cells_by_cluster(lines[2]) > width
    rows = title.split("\n")
    assert all(cells_by_cluster(row) <= width for row in rows), rows
    assert max(cells_by_cluster(row) for row in rows) == width, rows
    assert "".join(rows) == "".join(lines)
    assert len(rows) > len(lines)
    assert (rows[0], rows[-1]) == (lines[0], lines[3])
    assert rows[rows.index("Saved decision: untrusted ") + 1].startswith("(")
    assert options == ["  Trust", f"  Trust parent folder ({project.parent})", "✓ Do not trust"]
    assert initial == 2
    line = next(c for c in commits if "Project trusted" in c)
    assert (
        line == f"Project trusted (saved): {project.parent}\nRestart aelix for this to take effect."
    )
    saved = json.loads((agent_dir / "trust.json").read_text(encoding="utf-8"))
    assert saved == {_key(project.parent): True}
    assert ProjectTrustStore(agent_dir).get(project) is True


# === The frame it is drawn in ===============================================


@pytest.mark.parametrize("width", [80, 120])
async def test_the_rows_are_on_screen_whole_in_the_own_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    """Four title rows, as ``/trust`` had: at 80x11 nothing is cut, ``Do not
    trust`` (the only answer that saves a revocation) and the counter included."""

    # A short path: the own frame cuts a row at the screen's edge (not this test).
    cwd = Path(tmp_path.anchor) / "srv" / "repo"
    entry = ProjectTrustStoreEntry(path=_key(cwd), decision=False)
    labels = project_trust_options(cwd, include_session_only=False)
    shown = ["  Trust", f"  {labels[1]}", f"✓ {labels[2]}"]
    title = trust_selector_title(cwd, entry, session_trusted=False)
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, shown, initial_index=2, own=True))
    rows = dialog.paint(width, 11).split("\n")
    assert rows[:4] == [
        "Project trust",
        str(cwd),
        f"Saved decision: untrusted ({_key(cwd)})",
        "Current session: untrusted",
    ]
    assert rows[5:9] == [
        "    Trust",
        f"    Trust parent folder ({cwd.parent})",
        "▸ ✓ Do not trust",
        "  (3/3)",
    ]
    assert rows[10] == "↑/↓ move · type to filter · Enter select · Esc cancel"
    dialog.press("enter")
    assert await dialog.answer() == shown[2]


@pytest.mark.parametrize("width", [40, 80, 120])
def test_own_title_rows_cut_after_a_space_at_the_width_given(width: int) -> None:
    from aelix_coding_agent.tui.context import _own_title_rows

    path = "/srv/" + "a-long-directory-name/" * 4 + "repo"
    line = f"Saved decision: trusted (inherited from {path})"
    title = f"Project trust\n{line}"
    rows = _own_title_rows(title, width).split("\n")
    assert rows[0] == "Project trust"
    assert all(cells_by_cluster(row) <= width for row in rows), rows
    assert "".join(rows[1:]) == line
    if cells_by_cluster(line) <= width:
        assert rows[1:] == [line]  # a line that fits is drawn as it was
    else:
        # Broken after "from ": the path starts a row of its own.
        assert rows[1] == "Saved decision: trusted (inherited from "
        assert rows[2].startswith("/srv/a-long-directory-name/"), rows


def test_own_title_rows_blank_a_control_character_as_the_frame_does() -> None:
    from aelix_coding_agent.tui.context import _own_title_rows

    assert (
        _own_title_rows("Project trust\n/srv/evil\x1b[8mx", 80) == "Project trust\n/srv/evil [8mx"
    )
