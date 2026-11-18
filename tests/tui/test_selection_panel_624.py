"""#624 (ADR-0305 D4, D6, D10, D15) — the panel over the shared pieces, with its words unchanged.

* Space and ←/→ say what ``edit_refusal`` / ``scope_refusal`` say, which are the
  panel's texts at ``a1606f58`` plus the placeholder rule (a row a save would drop
  is refused instead of looking saved).
* A project ``settings.json`` deleted after Enter's check and before the save's
  locked read is not re-created: "Not saved: …", and the next Enter asks.
* An edit another writer made a no-op writes nothing: "the settings files already
  said this", the bytes unchanged.
* A ``restart`` kind's pending row says "restart" and "the next aelix start
  applies it"; an Enter that wrote nothing names the kind's file word.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.core.resource_selection import (
    ApplyResult,
    ItemState,
    Lock,
    ResourceItem,
    SaveResult,
    ScopeStatus,
    Snapshot,
)
from aelix_coding_agent.tui.selection_panel import (
    SelectionModel,
    SelectionPanelBody,
    _hint,
    _state_line,
    cells,
    plan_layout,
    render_panel,
    summary,
)

from tests.core.test_resource_selection_624 import (
    CASES,
    NOT_NOW,
    _bench,
    _delete_before_the_locked_read,
    _toggle_refusal_now,
)

_SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _plain(text: str) -> str:
    return _SGR.sub("", text)


def _pick(model: SelectionModel, key: str) -> None:
    model.cursor = next(i for i, s in enumerate(model.visible()) if s.item.key == key)


# --- Space and ←/→ ---------------------------------------------------------------


@pytest.mark.parametrize("state", CASES)
def test_space_refuses_with_the_old_words_and_the_placeholder_rule(state: ItemState) -> None:
    scopes = {"global": ScopeStatus("global", "/g", True)}
    model = SelectionModel(Snapshot("k", (state,), scopes), "Things")  # type: ignore[arg-type]
    refusal = model.toggle()
    old = _toggle_refusal_now(state)
    if old is None and state.item.role == "item" and state.item.identities:
        assert refusal is None and model.edits == {"row": False}
        return
    want = old if old is not None else "row cannot be turned on or off here"
    assert refusal == want
    assert model.message == ("warn", want)
    assert model.edits == {}, "a refused row records no edit"


@pytest.mark.parametrize(
    ("scope", "status", "want"),
    [
        ("project", ScopeStatus("project", "/p", True), None),
        (
            "project",
            ScopeStatus("project", "/p", False, "this folder is not trusted (/trust to decide)"),
            "Cannot save to This project: this folder is not trusted (/trust to decide)",
        ),
        ("project", None, "Cannot save to This project: not available"),
    ],
)
def test_the_arrows_refuse_with_the_old_words(scope: str, status: Any, want: str | None) -> None:
    scopes = {"global": ScopeStatus("global", "/g", True)}
    if status is not None:
        scopes[scope] = status
    model = SelectionModel(Snapshot("k", (), scopes), "Things")  # type: ignore[arg-type]
    assert model.set_target(scope) == want  # type: ignore[arg-type]
    if want is None:
        assert model.target == scope and model.message is None
    else:
        assert model.target == "global" and model.message == ("warn", want)


def test_the_create_question_keeps_its_words() -> None:
    model = SelectionModel(Snapshot("k", (), {}), "Things")
    model.confirm_create = True
    assert _plain(_hint(model, 100)).strip() == (
        "Enter create and save · Esc back · next launch may ask; -p may need --approve"
    )


# --- the race through the panel (ADR-0305 D6) ---------------------------------------


async def test_the_panel_says_not_saved_and_the_next_enter_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection, kind, manager, _agent, work = _bench(tmp_path, project_json={})
    project = work / ".aelix" / "settings.json"
    body = SelectionPanelBody(selection, kind, title="Skills", noun=("skill", "skills"))
    body.plan(100, 30)
    assert body.model.set_target("project") is None
    _pick(body.model, "pdf")
    assert body.handle("space") == "handled" and body.model.edits == {"pdf": False}
    barrier = _delete_before_the_locked_read(manager, project, monkeypatch)
    barrier["armed"] = 1
    assert body.handle("enter") == "save"
    assert await body.save() is None, "the panel stays open"
    assert barrier["deleted"] == 1
    assert body.model.message == ("error", f"✖ Not saved: {project} {NOT_NOW}")
    assert not project.exists() and body.model.edits == {"pdf": False}
    barrier["armed"] = 0
    # The second Enter asks, as it does for a project file that never existed.
    assert body.handle("enter") == "save"
    assert await body.save() is None
    assert body.model.confirm_create
    assert body.model.message == (
        "warn",
        "This creates .aelix/settings.json: the folder falls under project trust.",
    )
    assert not project.exists()
    # The confirmation creates it.
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    assert json.loads(project.read_text(encoding="utf-8")) == {"skills": ["-pdf"]}


# --- the concurrent writer (§3.3 scenario 2, at the panel) -------------------------


async def test_an_edit_another_writer_made_a_no_op_writes_nothing(tmp_path: Path) -> None:
    selection, kind, _manager, agent, _work = _bench(tmp_path, global_json={})
    body = SelectionPanelBody(selection, kind, title="Skills", noun=("skill", "skills"))
    body.plan(100, 30)
    _pick(body.model, "pdf")
    body.handle("space")
    assert body.model.edits == {"pdf": False}
    raw = '{"skills": ["+pdf", "-pdf"]}'
    (agent / "settings.json").write_text(raw, encoding="utf-8")  # the other writer
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    assert (agent / "settings.json").read_text(encoding="utf-8") == raw
    assert lines[0].plain.split("\n")[0] == "✓ Skills: the settings files already said this"


async def test_an_edit_that_is_still_a_change_is_written(tmp_path: Path) -> None:
    selection, kind, _manager, agent, _work = _bench(tmp_path, global_json={})
    body = SelectionPanelBody(selection, kind, title="Skills", noun=("skill", "skills"))
    body.plan(100, 30)
    _pick(body.model, "pdf")
    body.handle("space")
    (agent / "settings.json").write_text('{"skills": ["-deploy"]}', encoding="utf-8")
    body.handle("enter")
    lines = await body.save()
    assert lines is not None
    assert json.loads((agent / "settings.json").read_text(encoding="utf-8")) == {
        "skills": ["-deploy", "-pdf"]
    }


# --- restart's words (ADR-0305 D15, opus M1) ----------------------------------------


def _pending_row() -> ItemState:
    item = ResourceItem(kind="k", key="srv", label="srv", identities=("srv",))
    return ItemState(item, saved=False, applied=True, decided_by="global")


@pytest.mark.parametrize(
    ("effect", "word", "clause"),
    [
        ("next_request", "off · pending", "Saved off · now on · Enter applies the saved value"),
        ("reload", "off · /reload", "Saved off · now on · /reload applies it"),
        ("restart", "off · restart", "Saved off · now on · the next aelix start applies it"),
    ],
)
def test_a_pending_row_says_when_it_takes_effect(effect: str, word: str, clause: str) -> None:
    state = _pending_row()
    scopes = {"global": ScopeStatus("global", "/g", True)}
    model = SelectionModel(Snapshot("k", (state,), scopes), "Things")  # type: ignore[arg-type]
    model.effect = effect
    assert model.status(state)[0] == word
    assert _plain(_state_line(model, state, 120)).strip() == clause


def test_an_enter_that_wrote_nothing_names_the_kinds_file() -> None:
    nothing = SaveResult(True)
    default = summary("Skills", nothing, ApplyResult(True), effect_note="")
    assert default.plain.split("\n")[0] == "✓ Skills: the settings files already said this"
    mcp = summary("MCP servers", nothing, None, effect_note="x", files_word="mcp.json")
    assert mcp.plain == "✓ MCP servers: mcp.json already said this"


class _RestartSelection:
    """A selection whose save writes and whose apply must never be called."""

    def __init__(self) -> None:
        item = ResourceItem(kind="mcp", key="srv", label="srv", identities=("srv",))
        scopes = {"global": ScopeStatus("global", "/g/mcp.json", True)}
        self.snap = Snapshot("mcp", (ItemState(item, True, True, "global"),), scopes)  # type: ignore[arg-type]
        self.applied = 0

    def snapshot(self, _kind: Any) -> Snapshot:
        return self.snap

    def needs_new_project_file(self, _scope: Any, _edits: Any) -> bool:
        return False

    async def save(self, _kind: Any, _scope: Any, _edits: Any, **_kw: Any) -> SaveResult:
        return SaveResult(True, None, ("/g/mcp.json",), (("srv", False),))

    async def apply(self, _kind: Any, *_a: Any, **_kw: Any) -> ApplyResult:
        self.applied += 1
        return ApplyResult(True)


class _RestartKind:
    kind = "mcp"
    effect = "restart"
    files_word = "mcp.json"


async def test_a_restart_kind_saves_applies_nothing_and_says_the_next_start() -> None:
    selection = _RestartSelection()
    note = "the next aelix start applies it; /reload does not reconnect MCP servers"
    body = SelectionPanelBody(
        selection,  # type: ignore[arg-type]
        _RestartKind(),  # type: ignore[arg-type]
        title="MCP servers",
        effect_note=note,
        width=lambda: 100,
    )
    body.model.toggle()
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None and selection.applied == 0
    assert lines[0].plain.split("\n") == [
        "✓ MCP servers saved to /g/mcp.json",
        "    off  srv",
        f"  {note}",
    ]


# --- fix round 1: a long reason is drawn whole, the list keeps its rows ----------------


def _locked(reason: str, n: int = 6) -> Snapshot:
    """*n* rows; the first is locked with *reason*; both scopes writable (short ←/→ texts)."""

    rows = [
        ItemState(
            ResourceItem(kind="k", key=f"r{i}", label=f"r{i}", identities=(f"r{i}",)),
            True,
            True,
            "global",
            Lock(True, reason, tag="locked", kind="cli") if i == 0 else None,
        )
        for i in range(n)
    ]
    scopes = {s: ScopeStatus(s, f"/{s}", True) for s in ("global", "project")}  # type: ignore[misc]
    return Snapshot("k", tuple(rows), scopes)  # type: ignore[arg-type]


def _frame(model: SelectionModel, width: int, rows: int) -> list[str]:
    layout = plan_layout(model, width, rows)
    return [_plain(r) for r in render_panel(model, layout)]


@pytest.mark.parametrize("width", [80, 120])
def test_a_locked_rows_long_reason_is_drawn_whole_in_the_detail_rows(width: int) -> None:
    """Any kind, not only /mcp: the lock line takes the description's rows it needs."""

    reason = "kept by " + "/".join(f"dir{i:02d}" for i in range(40)) + "/file.json for this run"
    model = SelectionModel(_locked(reason), "Things")
    frame = _frame(model, width, 24 - 7)
    whole = f"Locked on: {reason}"
    assert cells(whole) > 2 * width, "anti-vacuity: the default three rows would cut it"
    rule = max(i for i, r in enumerate(frame) if r and set(r) == {"─"})
    detail = frame[rule + 2 : -1]
    assert "".join("".join(detail).split()) == "".join(whole.split()), detail
    assert all(cells(r) <= width for r in frame)


def test_the_list_keeps_three_rows_when_a_reason_needs_more_than_there_is() -> None:
    """A reason longer than the screen allows grows the detail rows only so far: three list
    rows stay (``_LIST_KEPT``), and the reason is cut with ``…`` where it must be."""

    reason = "kept by " + "/".join(f"dir{i:03d}" for i in range(400))
    model = SelectionModel(_locked(reason), "Things")
    layout = plan_layout(model, 80, 24 - 7)
    assert layout.height == 17 and layout.viewport == 3, layout
    frame = [_plain(r) for r in render_panel(model, layout)]
    assert frame[-2].rstrip().endswith("…"), frame
    rule = max(i for i, r in enumerate(frame) if r and set(r) == {"─"})
    assert rule - 2 == 3, "three list rows between the search row and the rule"
    assert any(r.startswith(" ▸ (✓) r0") for r in frame[2:rule]), frame


def test_a_locked_rows_load_error_after_its_lock_is_drawn_whole() -> None:
    """The state line can be longer than Space's refusal: #683's "· failed to load: …"
    follows the lock's reason there, and Space says the reason alone."""

    error = "RuntimeError: " + "/".join(f"dir{i:02d}" for i in range(30)) + "/setup.py raised"
    snapshot = _locked("loaded by -e for this run")
    first = snapshot.states[0]
    states = (ItemState(first.item, True, True, "global", first.lock, problem=error),)
    model = SelectionModel(Snapshot("k", states + snapshot.states[1:], snapshot.scopes), "Things")
    frame = _frame(model, 80, 24 - 7)
    whole = f"Locked on: loaded by -e for this run · failed to load: {error}"
    rule = max(i for i, r in enumerate(frame) if r and set(r) == {"─"})
    assert "".join("".join(frame[rule + 2 : -1]).split()) == "".join(whole.split()), frame


def test_a_short_lock_line_keeps_the_description_and_one_row() -> None:
    model = SelectionModel(_locked("loaded by -e for this run"), "Things")
    layout = plan_layout(model, 80, 24 - 7)
    assert layout.description_rows == 2, "nothing long: the default rows"
    frame = _frame(model, 80, 24 - 7)
    assert frame[-2].strip() == "Locked on: loaded by -e for this run"
