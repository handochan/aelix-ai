"""#704 — a successful ``/reload`` always leaves a line, and a ``/skill`` or ``/tool``
save always says what it did for the model.

Before: a ``/reload`` that changed no extension left nothing in the transcript
(pi's ``handleReloadCommand`` ends with "Reloaded keybindings, extensions,
skills, prompts, themes, and context files"), so a ``/reload`` after ``/skill``
read as ignored; and a panel save the apply found nothing to do for — a tool
already off this run, a skill the session already had — said only which file
it wrote.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.core.resource_selection import ApplyResult, SaveResult
from aelix_coding_agent.tui.selection_panel import summary
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_reload_extensions_25 import _control, _Registry, _row, _ScriptedRuntime
from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _quit_within,
    _spy_commits,
    _wait,
)
from tests.tui.test_selection_panel_lifecycle_511 import _Chrome, _open, _press, _setup

NOTE = "The model gets the new list from your next message."
FILE = ("/home/u/.aelix/agent/settings.json",)


def _plain(save: SaveResult | None, apply: ApplyResult | None, **kw: Any) -> list[str]:
    return summary("Skills", save, apply, effect_note=NOTE, width=100, **kw).plain.split("\n")


# --- summary(): every branch says what happened -------------------------------------


def test_a_save_the_session_took_says_the_note() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False),)), ApplyResult(True, turned_off=("pdf",))
    )
    assert rows == ["✓ Skills saved to " + FILE[0], "    off  pdf", "  " + NOTE]


def test_a_save_the_session_already_had_says_nothing_changes() -> None:
    rows = _plain(SaveResult(True, None, FILE, (("grep", False),)), ApplyResult(True))
    assert rows[-1] == "  Nothing changes for the model: this session already had grep off."
    assert NOTE not in "\n".join(rows)


def test_several_saves_the_session_already_had() -> None:
    rows = _plain(SaveResult(True, None, FILE, (("a", False), ("b", True))), ApplyResult(True))
    assert rows[-1] == "  Nothing changes for the model: this session already had these."


def test_a_save_something_else_holds_says_it_stays_and_why() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("grep", True),)),
        ApplyResult(True),
        held=(("grep", True, "it was turned off for this run"),),
    )
    assert rows[-1] == "  grep stays off in this session: it was turned off for this run."
    assert not any("Nothing changes" in r for r in rows)


def test_a_held_change_next_to_one_that_moved_says_both() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False), ("grep", True))),
        ApplyResult(True, turned_off=("pdf",)),
        held=(("grep", True, ""),),
    )
    assert rows[-2:] == ["  " + NOTE, "  grep stays off in this session."]


def test_a_deferred_save_says_when_and_the_note() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False),)),
        ApplyResult(False, deferred=True, turned_off=("pdf",)),
    )
    assert rows[-2:] == ["  A turn is running; this applies when it ends.", "  " + NOTE]


def test_a_mismatch_is_the_line_and_no_nothing_changes_claim() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (), ("pdf: saved, but this project's file keeps it on here",)),
        ApplyResult(True),
    )
    assert rows[-1] == "  pdf: saved, but this project's file keeps it on here"
    assert not any("Nothing changes" in r for r in rows)


# Codex 704-tests — the rows above pass with the summary() before #704 too, for
# the lines they say are the ones it said. These carry what #704 adds to each
# row, so that summary() fails them, and pin that a deferred or refused save
# says nothing about a change being held or already in place.


def test_the_changed_list_row_with_one_change_held() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False), ("grep", True))),
        ApplyResult(True, turned_off=("pdf",)),
        held=(("grep", True, "it was turned off for this run"),),
    )
    assert rows == [
        "✓ Skills saved to " + FILE[0],
        "    off  pdf",
        "    on   grep",
        "  " + NOTE,
        "  grep stays off in this session: it was turned off for this run.",
    ]


def test_the_mismatch_row_next_to_a_change_the_session_already_had() -> None:
    rows = _plain(
        SaveResult(
            True,
            None,
            FILE,
            (("deploy", True),),
            ("pdf: saved, but this project's file keeps it on here",),
        ),
        ApplyResult(True),
    )
    assert rows == [
        "✓ Skills saved to " + FILE[0],
        "    on   deploy",
        "  pdf: saved, but this project's file keeps it on here",
        "  Nothing changes for the model: this session already had deploy on.",
    ]


def test_a_deferred_save_says_nothing_of_held_or_already_had() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False), ("grep", True))),
        ApplyResult(False, deferred=True, turned_off=("pdf",)),
        held=(("grep", True, "it was turned off for this run"),),
    )
    assert rows == [
        "✓ Skills saved to " + FILE[0],
        "    off  pdf",
        "    on   grep",
        "  A turn is running; this applies when it ends.",
        "  " + NOTE,
    ]


def test_a_refused_apply_says_nothing_of_held_or_already_had() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False),)),
        ApplyResult(False, warning="kept what was applied: the file"),
        held=(("pdf", False, "x"),),
    )
    assert rows == [
        "✓ Skills saved to " + FILE[0],
        "    off  pdf",
        "  kept what was applied: the file.",
    ]


@pytest.mark.parametrize(
    ("save", "first"),
    [
        (None, "✓ Skills: nothing to save"),
        (SaveResult(True), "✓ Skills: the settings files already said this"),
    ],
)
def test_a_save_that_wrote_nothing_says_so(save: SaveResult | None, first: str) -> None:
    assert _plain(save, ApplyResult(True)) == [first]


def test_a_refused_apply_says_the_warning_not_nothing_changes() -> None:
    rows = _plain(
        SaveResult(True, None, FILE, (("pdf", False),)),
        ApplyResult(False, warning="kept what was applied: the file"),
    )
    assert rows[-1] == "  kept what was applied: the file."
    assert not any("Nothing changes" in r for r in rows)


# --- the panel computes ``held`` and ``already`` from a fresh read ------------------


async def test_the_panel_says_nothing_changes_for_a_skill_the_session_already_had(
    tmp_path: Path,
) -> None:
    # The file says deploy off, the session still has it on (another session's
    # save): turning it back on saves, and the session already had that.
    selection, kind, storage = _setup(tmp_path, '{"skills": ["-deploy"]}')
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    await _press(chrome, "space")  # deploy: saved off -> on
    await _press(chrome, "enter")
    await asyncio.wait_for(task, timeout=2)
    [printed] = [e[1] for e in chrome.events if isinstance(e, tuple)]
    text = " ".join(" ".join(printed).split())
    assert "on deploy" in text
    assert "Nothing changes for the model: this session already had deploy on." in text
    assert "next message" not in text


async def test_the_panel_says_the_note_when_the_session_changed(tmp_path: Path) -> None:
    selection, kind, _storage = _setup(tmp_path, "{}")
    chrome = _Chrome()
    task, _commits = await _open(chrome, selection, kind)
    await _press(chrome, "space")  # deploy off
    await _press(chrome, "enter")
    await asyncio.wait_for(task, timeout=2)
    [printed] = [e[1] for e in chrome.events if isinstance(e, tuple)]
    text = " ".join(printed)
    assert "off  deploy" in text and "Nothing changes" not in text and "stays" not in text


class _HeldSelection:
    """A tools-shaped selection: grep saved on is applied, but this run keeps it off."""

    def __init__(self) -> None:
        from aelix_coding_agent.core.resource_selection import (
            ItemState,
            ResourceItem,
            ScopeStatus,
            Snapshot,
        )

        item = ResourceItem(
            kind="tool",
            key="grep",
            label="grep",
            identities=("grep",),
            description="grep things",
            group="builtin",
            group_label="BUILT-IN",
        )
        scopes = {
            "global": ScopeStatus("global", FILE[0], True),
            "project": ScopeStatus("project", "/p/.aelix/settings.json", True),
        }
        self._before = Snapshot("tool", (ItemState(item, False, False, "global"),), scopes)
        # After the save: saved on and applied, but not live (an extension
        # turned it off for this run) — what the model gets is still no grep.
        self._after = Snapshot("tool", (ItemState(item, True, True, None, live=False),), scopes)
        self.saved = False

    def snapshot(self, _kind: Any) -> Any:
        return self._after if self.saved else self._before

    def needs_new_project_file(self, _scope: Any, _edits: Any) -> bool:
        return False

    async def save(self, _kind: Any, _scope: Any, _edits: Any) -> SaveResult:
        self.saved = True
        return SaveResult(True, None, FILE, (("grep", True),))

    async def apply(self, _kind: Any, **_kw: Any) -> ApplyResult:
        return ApplyResult(True)


async def test_the_body_reads_held_after_the_apply() -> None:
    """V7 — ``SelectionPanelBody._held``: a mutant that always returns ``()`` says
    "Nothing changes … already had grep on", which the model does not have."""

    from aelix_coding_agent.tui.selection_panel import SelectionPanelBody

    selection = _HeldSelection()
    body = SelectionPanelBody(
        selection,  # type: ignore[arg-type]
        SimpleNamespace(kind="tool", effect="next_request"),  # type: ignore[arg-type]
        title="Tools",
        effect_note=NOTE,
        width=lambda: 100,
    )
    body.model.toggle()  # grep: saved off -> on
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    rows = lines[0].plain.split("\n")
    assert rows[-1] == "  grep stays off in this session: it was turned off for this run.", rows
    assert not any("Nothing changes" in r for r in rows)


# --- the /skill note says what the model is shown ------------------------------------


class _ToolsHarness:
    def __init__(self, active: list[str] | None, *, raises: bool = False) -> None:
        self._active, self._raises = active, raises

    def _action_get_active_tools(self) -> list[str]:
        if self._raises:
            raise RuntimeError("boom")
        return list(self._active or [])


@pytest.mark.parametrize(
    ("harness", "withheld"),
    [
        (_ToolsHarness(["read", "bash"]), False),
        (_ToolsHarness([]), True),  # --no-tools
        (_ToolsHarness(["bash", "grep"]), True),  # --tools bash,grep: read off
        (_ToolsHarness(None, raises=True), False),  # unreadable: the usual note
        (SimpleNamespace(), False),  # no active-tool seam (an embedder)
    ],
)
def test_the_skill_note_says_when_the_model_is_not_shown_the_list(
    harness: Any, withheld: bool
) -> None:
    """Codex 704-skill-overclaim — the list reaches the model only with ``read``."""

    from aelix_coding_agent.tui.shell import skill_effect_note

    note = skill_effect_note(harness)
    if withheld:
        assert note == (
            "The model is not shown the skill list in this session: the read tool is off."
        )
    else:
        assert note.startswith("The model gets the new list from your next message")


def test_the_panel_reads_its_note_when_the_save_is_said(tmp_path: Path) -> None:
    from aelix_coding_agent.tui.selection_panel import SelectionPanelBody

    selection, kind, _storage = _setup(tmp_path, "{}")
    calls: list[int] = []
    body = SelectionPanelBody(
        selection, kind, title="Skills", effect_note=lambda: (calls.append(1), "LIVE")[1]
    )
    assert calls == []  # not read at open: the session can change while it is open
    assert body._effect_note() == "LIVE" and calls == [1]
    broken = SelectionPanelBody(
        selection,
        kind,
        title="Skills",
        effect_note=lambda: 1 / 0,  # type: ignore[arg-type,return-value]
    )
    assert broken._effect_note() == ""


# --- reload_success_line ------------------------------------------------------------


def test_a_reload_that_changed_nothing_says_no_changes() -> None:
    from aelix_coding_agent.tui.shell import reload_success_line

    same = {"skills": ["a"], "prompt templates": ["p"], "tools": ["read"]}
    line = reload_success_line(same, dict(same))
    assert line == (
        "✓ Reloaded settings, extensions, skills, prompt templates and context files · no changes"
    )


def test_a_reload_names_what_each_bucket_gained_and_lost() -> None:
    from aelix_coding_agent.tui.shell import reload_success_line

    before = {"skills": ["alpha", "beta"], "prompt templates": [], "tools": ["read", "grep"]}
    after = {"skills": ["beta", "gamma"], "prompt templates": ["fix"], "tools": ["read"]}
    line = reload_success_line(before, after)
    assert line.endswith(" · skills: +gamma, -alpha · prompt templates: +fix · tools: -grep"), line


def test_a_bucket_lists_four_names_then_a_count() -> None:
    from aelix_coding_agent.tui.shell import reload_success_line

    line = reload_success_line({"skills": []}, {"skills": list("abcdef")})
    assert line.endswith("skills: +a, +b, +c, +d, 2 more")


def test_with_the_extensions_entry_below_it_does_not_claim_no_changes() -> None:
    from aelix_coding_agent.tui.shell import reload_success_line

    same = {"skills": ["a"]}
    assert not reload_success_line(same, same, more_below=True).endswith("no changes")


def test_a_bucket_that_cannot_be_read_is_not_compared() -> None:
    from aelix_coding_agent.tui.shell import _reload_buckets, reload_success_line

    class Broken:
        @property
        def skills(self) -> list[Any]:
            raise RuntimeError("boom")

        prompt_templates: list[Any] = []

    buckets = _reload_buckets(Broken())
    assert buckets["skills"] is None and buckets["prompt templates"] == []
    # Codex 704-getter-raises: a list it could not read is not "no changes".
    line = reload_success_line(buckets, {**buckets, "skills": ["x"]})
    assert line.endswith(" · skills: not compared (could not be read)"), line
    assert "no changes" not in line
    # Unread after the reload is the same: the two lists were not compared.
    after = reload_success_line({**buckets, "skills": ["x"]}, buckets)
    assert after.endswith(" · skills: not compared (could not be read)"), after


# --- through the real input loop ----------------------------------------------------


async def _reload(runtime: Any, control: Any) -> list[str]:
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extension_control=control,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: any("Reloaded" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    return commits


async def test_a_successful_reload_commits_one_line_naming_the_skill_it_dropped() -> None:
    old = FakeHarness()
    old.skills = [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]  # type: ignore[attr-defined]
    new = FakeHarness()
    new.skills = [SimpleNamespace(name="beta")]  # type: ignore[attr-defined]

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime._harness = new
        runtime.reload_phase = "done"

    same = {"inventory": None}
    commits = await _reload(_ScriptedRuntime(old, script), _control(same))
    [line] = [c for c in commits if "Reloaded" in c]
    assert " ".join(line.split()) == (
        "✓ Reloaded settings, extensions, skills, prompt templates and context files"
        " · skills: -alpha"
    ), line


async def test_a_reload_with_an_extension_change_says_both_in_order() -> None:
    from aelix_coding_agent.core.extension_selection import ExtensionInventory

    holder: dict[str, Any] = {"inventory": ExtensionInventory((_row("lint.py", loaded=False),))}

    async def script(runtime: _ScriptedRuntime) -> None:
        holder["inventory"] = ExtensionInventory((_row("lint.py", loaded=True),))
        runtime.reload_phase = "done"

    harness = FakeHarness()
    commits = await _reload(_ScriptedRuntime(harness, script), _control(holder))
    heads = [c for c in commits if "Reloaded" in c or "Extensions after /reload" in c]
    assert len(heads) == 2 and "Reloaded" in heads[0] and "lint.py on" in heads[1]
    assert not heads[0].endswith("no changes")


async def test_the_kill_switch_arm_says_what_it_reloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_RELOAD_REBUILD", "0")
    harness = FakeHarness()

    async def script(_runtime: _ScriptedRuntime) -> None:
        raise AssertionError("the kill switch must not rebuild")

    commits = await _reload(_ScriptedRuntime(harness, script), _control({"inventory": None}))
    [line] = [c for c in commits if "Reloaded" in c]
    assert "the extensions' resources (AELIX_RELOAD_REBUILD is off)" in " ".join(line.split())
    assert harness.reloads == 1


# --- other ways in: an extension's ctx.reload(), the minimal REPL --------------------


class _CommandHarness(FakeHarness):
    """A harness with one extension command, ``/rl``, whose handler calls ``ctx.reload()``."""

    def __init__(self, skills: list[str]) -> None:
        super().__init__()
        self.skills = [SimpleNamespace(name=n) for n in skills]  # type: ignore[attr-defined]
        self.handled: list[str] = []
        self.caught: list[str] = []

        async def handler(args: str, ctx: Any) -> None:
            self.handled.append(args)
            try:
                await ctx.reload()
            except Exception as exc:
                self.caught.append(str(exc))
                raise

        resolved = SimpleNamespace(
            invocation_name="rl", command=SimpleNamespace(handler=handler, description="")
        )
        self.extension_runner = SimpleNamespace(  # type: ignore[attr-defined]
            extensions=[],
            get_command=lambda name: resolved if name == "rl" else None,
            get_registered_commands=lambda: [resolved],
        )

    def make_command_context(self, *, repo: Any = None, session_runtime: Any = None) -> Any:
        return SimpleNamespace(reload=session_runtime.reload)


async def test_an_extension_commands_ctx_reload_leaves_the_reload_line() -> None:
    """V4 / Codex 704-other-entries — pi routes ``ctx.reload()`` through
    ``handleReloadCommand``; it left no line in the TUI."""

    old = _CommandHarness(["alpha", "beta"])
    new = FakeHarness()
    new.skills = [SimpleNamespace(name="beta")]  # type: ignore[attr-defined]

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime._harness = new
        runtime.reload_phase = "done"

    runtime = _ScriptedRuntime(old, script)
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extension_control=_control({"inventory": None}),
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/rl now\n")
        await _wait(lambda: any("Reloaded" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert old.handled == ["now"] and runtime.reloads == 1
    [line] = [c for c in commits if "Reloaded" in c]
    assert " ".join(line.split()) == (
        "✓ Reloaded settings, extensions, skills, prompt templates and context files"
        " · skills: -alpha"
    ), line


async def test_a_failed_ctx_reload_still_raises_into_the_command() -> None:
    from aelix_coding_agent.tui.shell import _ReportedReloadRuntime

    reported: list[str] = []

    async def report(reload: Any) -> None:
        reported.append("before")
        await reload()
        reported.append("after")

    class _Failing:
        reloads = 0
        harness = None

        async def reload(self) -> None:
            self.reloads += 1
            raise RuntimeError("rebuild failed")

    inner = _Failing()
    proxy = _ReportedReloadRuntime(inner, report)
    with pytest.raises(RuntimeError, match="rebuild failed"):
        await proxy.reload()
    assert reported == ["before"] and inner.reloads == 1
    assert proxy.harness is None  # everything else is the runtime's own


async def test_a_failed_ctx_reload_through_run_tui_leaves_no_reloaded_line() -> None:
    """Verify F3 — the test above gives the proxy its own *report*. Through
    ``run_tui``: if its ``_reload_for_extension`` swallowed the error, it would
    commit "✓ Reloaded …" after a rebuild that failed, and the command would
    never see the failure."""

    old = _CommandHarness(["alpha"])

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime.reload_phase = "teardown"
        raise RuntimeError("rebuild failed")

    runtime = _ScriptedRuntime(old, script)
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extension_control=_control({"inventory": None}),
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/rl now\n")
        await _wait(lambda: any("/rl failed" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert old.handled == ["now"] and runtime.reloads == 1
    assert old.caught == ["rebuild failed"]  # the failure reached the command
    assert [c for c in commits if "/rl failed" in c] == ["/rl failed: rebuild failed"]
    assert not any("Reloaded" in c for c in commits), commits


async def test_the_minimal_repl_reload_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import builtins

    from aelix_coding_agent.cli.repl import run_repl

    lines = iter(["/reload"])

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", fake_input)
    harness = FakeHarness()
    await run_repl(harness, cwd=".")  # type: ignore[arg-type]
    assert harness.reloads == 1
    assert capsys.readouterr().out.splitlines()[-1] == "Reloaded the extensions' resources."
