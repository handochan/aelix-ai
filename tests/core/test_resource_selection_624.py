"""#624 (ADR-0305 D4, D6, D10) — what the panel and RPC share in ``core/resource_selection.py``.

* The no-op drop inside ``ResourceSelection.save``: an edit whose value the
  files already say is not written, so an edit made against an older snapshot
  (another writer changed the file meanwhile) writes nothing (Codex 12).
* Consent inside the write: a project ``settings.json`` that existed when the
  caller checked and is gone when the lock is held is NOT re-created, unless the
  caller says the creation was confirmed (Codex 6). The barrier deletes the file
  after the transaction's lock and before its read.
* ``edit_refusal`` and ``scope_refusal`` say exactly what the panel's Space and
  ←/→ said at ``a1606f58``, for every role and lock kind, plus the placeholder
  rule; ``commit`` saves, then applies unless the effect is reload or restart.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_ai.settings.settings_manager import SettingsFileError
from aelix_coding_agent.core.resource_selection import (
    ApplyResult,
    ItemState,
    Lock,
    ResourceItem,
    ResourceSelection,
    SaveResult,
    ScopeStatus,
    SettingsLists,
    Snapshot,
    scope_refusal,
)
from aelix_coding_agent.core.skill_selection import SkillKind, SkillSource, load_skill_catalog

NOT_NOW = (
    "does not exist now; creating it puts this folder under project trust and needs a confirmation"
)


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


class _Harness:
    is_idle = True

    def __init__(self, names: list[str]) -> None:
        self.skills = [type("S", (), {"name": n})() for n in names]


def _bench(tmp_path: Path, *, global_json: Any = None, project_json: Any = None):
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir()
    work.mkdir()
    root = agent / "skills"
    for name in ("pdf", "deploy", "notes"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d {name}\n---\nb\n", encoding="utf-8"
        )
    if global_json is not None:
        (agent / "settings.json").write_text(
            global_json if isinstance(global_json, str) else json.dumps(global_json),
            encoding="utf-8",
        )
    if project_json is not None:
        (work / ".aelix").mkdir()
        (work / ".aelix" / "settings.json").write_text(json.dumps(project_json), encoding="utf-8")
    manager = SettingsManager.create(work, agent, project_trusted=True)
    holder = {"result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())}
    harness = _Harness(["pdf", "deploy", "notes"])
    kind = SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(work),
        project_trusted=True,
        project_skills_dir=str(work / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(work / ".aelix"), project_trusted=True
    )
    return selection, kind, manager, agent, work


def _delete_before_the_locked_read(
    manager: SettingsManager, path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    """Another process deletes *path* after the transaction's lock and before its read."""

    storage = manager._storage  # noqa: SLF001 — the barrier sits inside the transaction
    real_read = storage._read_text_strict  # type: ignore[attr-defined]  # noqa: SLF001
    real_transact = storage.transact  # type: ignore[attr-defined]
    state = {"armed": 0, "deleted": 0, "inside": 0}

    def transact(scope: Any, fn: Any) -> None:
        state["inside"] = 1
        try:
            real_transact(scope, fn)
        finally:
            state["inside"] = 0

    def read(target: Path) -> str | None:
        if state["inside"] and state["armed"] and Path(target) == path and path.exists():
            path.unlink()
            state["deleted"] += 1
        return real_read(target)

    monkeypatch.setattr(storage, "transact", transact)
    monkeypatch.setattr(storage, "_read_text_strict", read)
    return state


# --- consent inside the write (ADR-0305 D6, Codex 6) ------------------------------


async def test_a_project_file_deleted_before_the_locked_read_is_not_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection, kind, manager, _agent, work = _bench(tmp_path, project_json={"theme": "dark"})
    project = work / ".aelix" / "settings.json"
    assert not selection.needs_new_project_file("project", {"pdf": False}), "it exists now"
    barrier = _delete_before_the_locked_read(manager, project, monkeypatch)
    barrier["armed"] = 1
    result = await selection.save(kind, "project", {"pdf": False})
    assert barrier["deleted"] == 1, "anti-vacuity: the barrier ran inside the transaction"
    assert result.ok is False
    assert result.error == f"{project} {NOT_NOW}"
    assert not project.exists(), "the folder was not put under project trust"


async def test_a_confirmed_save_creates_the_file_the_race_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection, kind, manager, _agent, work = _bench(tmp_path, project_json={})
    project = work / ".aelix" / "settings.json"
    barrier = _delete_before_the_locked_read(manager, project, monkeypatch)
    barrier["armed"] = 1
    result = await selection.save(kind, "project", {"pdf": False}, may_create_project_file=True)
    assert barrier["deleted"] == 1
    assert result.ok and result.written == (str(project),)
    assert json.loads(project.read_text(encoding="utf-8")) == {"skills": ["-pdf"]}


async def test_a_global_save_still_creates_the_global_file(tmp_path: Path) -> None:
    """``may_create`` is the project file's question only: the global file needs no trust."""

    selection, kind, _manager, agent, _work = _bench(tmp_path)
    assert not (agent / "settings.json").exists()
    result = await selection.save(kind, "global", {"pdf": False})
    assert result.ok
    assert json.loads((agent / "settings.json").read_text(encoding="utf-8")) == {"skills": ["-pdf"]}


@pytest.mark.parametrize("may_create", [False, True])
async def test_edit_list_checks_may_create_after_its_lock(tmp_path: Path, may_create: bool) -> None:
    _selection, _kind, manager, _agent, work = _bench(tmp_path)
    (work / ".aelix").mkdir()
    project = work / ".aelix" / "settings.json"
    if may_create:
        assert await manager.edit_list("project", "skills", lambda _old: ["-x"], may_create=True)
        assert json.loads(project.read_text(encoding="utf-8")) == {"skills": ["-x"]}
        return
    with pytest.raises(SettingsFileError) as info:
        await manager.edit_list("project", "skills", lambda _old: ["-x"], may_create=False)
    assert str(info.value) == f"{project} {NOT_NOW}"
    assert not project.exists()


async def test_edit_list_may_create_false_writes_an_existing_file(tmp_path: Path) -> None:
    _selection, _kind, manager, agent, _work = _bench(tmp_path, global_json={"theme": "dark"})
    assert await manager.edit_list("global", "skills", lambda _old: ["-x"], may_create=False)
    assert json.loads((agent / "settings.json").read_text(encoding="utf-8")) == {
        "theme": "dark",
        "skills": ["-x"],
    }


# --- the no-op drop (ADR-0305 D4, Codex 12) ---------------------------------------


async def test_only_no_ops_write_nothing(tmp_path: Path) -> None:
    """Without the drop the per-item edit rewrites ``+pdf`` away (measured: ``[]``)."""

    raw = '{"skills": ["+pdf", "-deploy"], "n": 1.50}\n'
    selection, kind, _manager, agent, _work = _bench(tmp_path, global_json=raw)
    result = await selection.save(kind, "global", {"pdf": True, "deploy": False})
    assert result == SaveResult(True)
    assert (agent / "settings.json").read_text(encoding="utf-8") == raw


async def test_another_writers_list_makes_the_edit_a_no_op(tmp_path: Path) -> None:
    """§3.3 scenario 2, case 1: the file now says ``["+pdf","-pdf"]`` (off) and the edit is off."""

    selection, kind, _manager, agent, _work = _bench(tmp_path, global_json={})
    edits = {"pdf": False}  # made against a snapshot where pdf was on
    assert next(s for s in selection.snapshot(kind).states if s.item.key == "pdf").saved
    raw = '{"skills": ["+pdf", "-pdf"]}'
    (agent / "settings.json").write_text(raw, encoding="utf-8")  # the other writer
    result = await selection.save(kind, "global", edits)
    assert result.ok and result.written == () and result.changed == ()
    assert (agent / "settings.json").read_text(encoding="utf-8") == raw


async def test_another_writers_list_leaves_the_edit_a_real_change(tmp_path: Path) -> None:
    """§3.3 scenario 2, case 2: the other writer turned deploy off; pdf off is still a change."""

    selection, kind, _manager, agent, _work = _bench(tmp_path, global_json={})
    (agent / "settings.json").write_text('{"skills": ["-deploy"]}', encoding="utf-8")
    result = await selection.save(kind, "global", {"pdf": False, "deploy": False})
    assert result.ok and result.written == (str(agent / "settings.json"),)
    assert result.changed == (("pdf", False),)
    assert json.loads((agent / "settings.json").read_text(encoding="utf-8")) == {
        "skills": ["-deploy", "-pdf"]
    }


# --- edit_refusal / scope_refusal: the panel's words (ADR-0305 D3, D10) -------------


def _base_toggle_refusal(state: ItemState) -> str | None:
    """``SelectionModel.toggle``'s refusal at ``a1606f58``, verbatim."""

    if state.item.role == "info":
        return (
            (state.lock.refusal or state.lock.reason)
            if state.lock is not None
            else f"{state.item.label} is status only"
        )
    if state.lock is not None:
        if state.lock.kind == "unavailable":
            name = f"{state.item.label} is unavailable"
        elif state.item.identities:
            name = f"{state.item.label} is locked"
        else:
            name = "Locked"
        return f"{name}: {state.lock.reason}"
    return None


def _toggle_refusal_now(state: ItemState) -> str | None:
    """The panel's refusal now: :func:`_base_toggle_refusal`, but for one combination no
    row reached at ``a1606f58``. A row with no identity locked ``always_on`` (a built-in
    such as ``JobsExtension``, which had no lock there: verify r1, live-RPC 3) is named as
    the other built-ins are, "<label> is locked: …", not "Locked: …" (#624 fix round 2,
    verify N5)."""

    lock = state.lock
    if (
        state.item.role != "info"
        and lock is not None
        and lock.kind == "always_on"
        and not state.item.identities
    ):
        return f"{state.item.label} is locked: {lock.reason}"
    return _base_toggle_refusal(state)


_ROLES = ("item", "placeholder", "info")
_LOCK_KINDS = ("", "cli", "always_on", "unavailable", "profile", "shadowed", "untrusted", "status")
_LOCKS: tuple[Lock | None, ...] = (
    None,
    *(Lock(False, f"why {k or 'unset'}", tag="t", kind=k) for k in _LOCK_KINDS),  # type: ignore[arg-type]
    *(Lock(True, "why on", kind=k, refusal="managed elsewhere") for k in _LOCK_KINDS),  # type: ignore[arg-type]
)
CASES = [
    ItemState(
        ResourceItem(kind="k", key="row", label="row", identities=ids, role=role),  # type: ignore[arg-type]
        saved=True,
        applied=True,
        decided_by=None,
        lock=lock,
    )
    for role, ids, lock in itertools.product(_ROLES, ((), ("row",)), _LOCKS)
]


@pytest.mark.parametrize("state", CASES)
def test_edit_refusal_is_the_panels_old_text_plus_the_placeholder_rule(state: ItemState) -> None:
    from aelix_coding_agent.core.resource_selection import edit_refusal

    old = _toggle_refusal_now(state)
    if old is not None:
        assert edit_refusal(state) == old
    elif state.item.role != "item" or not state.item.identities:
        assert edit_refusal(state) == "row cannot be turned on or off here"
    else:
        assert edit_refusal(state) is None


def test_the_cases_cover_every_role_and_lock_kind() -> None:
    assert {s.item.role for s in CASES} == set(_ROLES)
    assert {s.lock.kind for s in CASES if s.lock is not None} == set(_LOCK_KINDS)
    assert sum(1 for s in CASES if _base_toggle_refusal(s) is None) == 4
    # The one combination whose words changed since a1606f58: 2 roles x 2 locks.
    changed = [s for s in CASES if _toggle_refusal_now(s) != _base_toggle_refusal(s)]
    assert len(changed) == 4
    assert {_toggle_refusal_now(s) for s in changed} == {
        "row is locked: why always_on",
        "row is locked: why on",
    }


def _snapshot(scopes: dict[str, ScopeStatus]) -> Snapshot:
    return Snapshot("skill", (), scopes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scope", "status", "want"),
    [
        ("global", ScopeStatus("global", "/g", True), None),
        ("project", ScopeStatus("project", "/p", True, None, False), None),
        (
            "project",
            ScopeStatus("project", "/p", False, "this folder is not trusted (/trust to decide)"),
            "Cannot save to This project: this folder is not trusted (/trust to decide)",
        ),
        (
            "global",
            ScopeStatus("global", "/g", False, "/g did not load: boom"),
            "Cannot save to Global: /g did not load: boom",
        ),
    ],
)
def test_scope_refusal_is_the_panels_arrow_text(
    scope: str, status: ScopeStatus, want: str | None
) -> None:
    from aelix_coding_agent.core.resource_selection import scope_refusal

    assert scope_refusal(_snapshot({scope: status}), scope) == want


def test_scope_refusal_refuses_a_scope_the_snapshot_does_not_have() -> None:
    from aelix_coding_agent.core.resource_selection import scope_refusal

    snapshot = _snapshot({"global": ScopeStatus("global", "/g", True)})
    assert scope_refusal(snapshot, "project") == "Cannot save to This project: not available"


def test_the_untrusted_scope_reason_is_the_constant() -> None:
    from aelix_coding_agent.core import resource_selection as rs

    assert rs.UNTRUSTED_SCOPE_REASON == "this folder is not trusted (/trust to decide)"
    assert rs.UNTRUSTED_LOCK_REASON == "this folder is not trusted; /trust to decide"
    assert rs.RPC_UNTRUSTED == "this folder is not trusted"
    assert rs.CREATE_PROJECT_FILE_NOTE == (
        "This creates .aelix/settings.json: the folder falls under project trust."
    )
    assert rs.CREATE_PROJECT_FILE_HINT == "next launch may ask; -p may need --approve"


# --- commit: save, then apply unless reload/restart (ADR-0305 D10) -----------------


_SAVED = SaveResult(True)
_APPLIED = ApplyResult(True)


class _Recorder:
    def __init__(self, *, save: SaveResult | Exception = _SAVED, apply: Any = _APPLIED) -> None:
        self._save, self._apply = save, apply
        self.calls: list[tuple[str, Any]] = []

    async def save(
        self, kind: Any, scope: Any, edits: Any, *, may_create_project_file: bool = False
    ) -> SaveResult:
        self.calls.append(("save", (scope, dict(edits), may_create_project_file)))
        if isinstance(self._save, Exception):
            raise self._save
        return self._save

    async def apply(self, kind: Any, snapshot: Any = None, *, on_deferred: Any = None) -> Any:
        self.calls.append(("apply", on_deferred))
        if isinstance(self._apply, Exception):
            raise self._apply
        return self._apply


class _Kind:
    def __init__(self, effect: str) -> None:
        self.effect = effect


@pytest.mark.parametrize("effect", ["next_request", "reload", "restart"])
async def test_commit_saves_then_applies_only_a_next_request_kind(effect: str) -> None:
    from aelix_coding_agent.core.resource_selection import commit

    recorder = _Recorder()
    later = object()
    save, applied = await commit(
        recorder,
        _Kind(effect),
        "project",
        {"a": False},
        may_create_project_file=True,
        on_deferred=later,  # type: ignore[arg-type]
    )
    assert save == SaveResult(True)
    if effect == "next_request":
        assert applied == ApplyResult(True)
        assert recorder.calls == [("save", ("project", {"a": False}, True)), ("apply", later)]
    else:
        assert applied is None
        assert recorder.calls == [("save", ("project", {"a": False}, True))]


async def test_commit_with_no_edits_saves_nothing_and_applies() -> None:
    from aelix_coding_agent.core.resource_selection import commit

    recorder = _Recorder()
    assert await commit(recorder, _Kind("next_request"), "global", {}) == (None, ApplyResult(True))
    assert [c[0] for c in recorder.calls] == ["apply"]
    recorder = _Recorder()
    assert await commit(recorder, _Kind("restart"), "global", {}) == (None, None)
    assert recorder.calls == []


async def test_commit_passes_no_consent_unless_asked() -> None:
    from aelix_coding_agent.core.resource_selection import commit

    recorder = _Recorder()
    await commit(recorder, _Kind("next_request"), "project", {"a": True})
    assert recorder.calls[0] == ("save", ("project", {"a": True}, False))


@pytest.mark.parametrize("failure", [SaveResult(False, "disk full"), OSError("disk full")])
async def test_a_failed_save_is_returned_and_nothing_applied(failure: Any) -> None:
    from aelix_coding_agent.core.resource_selection import commit

    recorder = _Recorder(save=failure)
    save, applied = await commit(recorder, _Kind("next_request"), "global", {"a": False})
    assert save == SaveResult(False, "disk full")
    assert applied is None
    assert [c[0] for c in recorder.calls] == ["save"]


async def test_an_apply_that_raises_is_a_warning_or_the_callers_answer() -> None:
    from aelix_coding_agent.core.resource_selection import commit

    recorder = _Recorder(apply=RuntimeError("boom"))
    save, applied = await commit(recorder, _Kind("next_request"), "global", {"a": False})
    assert save == SaveResult(True)
    assert applied == ApplyResult(False, warning="saved, but not applied (boom)")
    seen: list[str] = []
    answer = ApplyResult(False, warning="mine")

    def _on_error(exc: Exception) -> ApplyResult | None:
        seen.append(str(exc))
        return answer

    recorder = _Recorder(apply=RuntimeError("boom"))
    assert await commit(
        recorder, _Kind("next_request"), "global", {"a": False}, on_apply_error=_on_error
    ) == (SaveResult(True), answer)
    assert seen == ["boom"]


def test_an_untrusted_project_scope_says_the_trust_pointer_in_the_panel(tmp_path: Path) -> None:
    """Verify V-F6 (mutant C12): ``_status``'s untrusted reason, pinned by its words, not the name."""

    agent, work = tmp_path / "agent", tmp_path / "work"
    (work / ".aelix").mkdir(parents=True)
    agent.mkdir()
    manager = SettingsManager.create(work, agent, project_trusted=False)
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(work / ".aelix"), project_trusted=False
    )
    holder = {"result": load_skill_catalog([], SettingsLists())}
    kind = SkillKind(
        holder=holder,
        harness=lambda: _Harness([]),
        cwd=str(work),
        project_trusted=False,
        project_skills_dir=str(work / ".aelix" / "skills"),
    )
    project = selection.snapshot(kind).scopes["project"]
    assert (project.writable, project.reason, project.exists) == (
        False,
        "this folder is not trusted (/trust to decide)",
        False,
    )
    assert scope_refusal(selection.snapshot(kind), "project") == (
        "Cannot save to This project: this folder is not trusted (/trust to decide)"
    )
