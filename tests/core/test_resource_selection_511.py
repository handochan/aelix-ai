"""#511 (ADR-0279 D2–D5) — ``ResourceSelection``: fresh reads, batch saves, honest reports."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.core.resource_selection import ResourceSelection, SettingsLists
from aelix_coding_agent.core.skill_selection import SkillKind, SkillSource, load_skill_catalog


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


class _Harness:
    is_idle = True

    def __init__(self, names: list[str]) -> None:
        self.skills = [type("S", (), {"name": n})() for n in names]


def _bench(
    tmp_path: Path,
    *,
    trusted: bool = True,
    global_json: Any = None,
    project_json: Any = None,
    mkdirs: bool = True,
):
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir()
    work.mkdir(exist_ok=not mkdirs)
    root = agent / "skills"
    for name in ("pdf", "deploy", "notes"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d {name}\n---\nb\n")
    if global_json is not None:
        (agent / "settings.json").write_text(
            global_json if isinstance(global_json, str) else json.dumps(global_json)
        )
    if project_json is not None:
        (work / ".aelix").mkdir()
        (work / ".aelix" / "settings.json").write_text(json.dumps(project_json))
    manager = SettingsManager.create(work, agent, project_trusted=trusted)
    holder = {"result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())}
    harness = _Harness(["pdf", "deploy", "notes"])
    kind = SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(work),
        project_trusted=trusted,
        project_skills_dir=str(work / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(work / ".aelix"), project_trusted=trusted
    )
    return selection, kind, agent, work


def _state(snapshot: Any, key: str) -> Any:
    return next(s for s in snapshot.states if s.item.key == key)


async def test_save_writes_the_identity_form_and_reports_it(tmp_path: Path) -> None:
    selection, kind, agent, _work = _bench(
        tmp_path, global_json={"theme": "dark", "skills": ["!old-*"]}
    )
    result = await selection.save(kind, "global", {"pdf": False})
    assert result.ok and result.written == (str(agent / "settings.json"),)
    assert json.loads((agent / "settings.json").read_text()) == {
        "theme": "dark",
        "skills": ["!old-*", "-pdf"],
    }
    assert result.changed == (("pdf", False),)
    snapshot = selection.snapshot(kind)
    pdf = _state(snapshot, "pdf")
    assert pdf.saved is False and pdf.applied is True and pdf.pending


async def test_a_toggle_back_writes_nothing(tmp_path: Path) -> None:
    raw = '{"skills": ["-pdf"]}'
    selection, kind, agent, _work = _bench(tmp_path, global_json=raw)
    result = await selection.save(kind, "global", {"pdf": False})
    assert result.ok and result.written == ()
    assert (agent / "settings.json").read_text() == raw


async def test_a_project_save_inherits_from_the_global_file(tmp_path: Path) -> None:
    selection, kind, _agent, work = _bench(
        tmp_path, global_json={"skills": ["-deploy"]}, project_json={}
    )
    result = await selection.save(kind, "project", {"deploy": True, "notes": True})
    assert result.ok
    # notes is already on globally: no project entry for it.
    assert json.loads((work / ".aelix" / "settings.json").read_text()) == {"skills": ["+deploy"]}
    deploy = _state(selection.snapshot(kind), "deploy")
    assert deploy.saved is True and deploy.decided_by == "project"


async def test_a_global_save_the_project_file_overrides_is_reported(tmp_path: Path) -> None:
    selection, kind, _agent, _work = _bench(
        tmp_path, global_json={}, project_json={"skills": ["+pdf"]}
    )
    result = await selection.save(kind, "global", {"pdf": False})
    assert result.ok
    assert result.mismatches == ("pdf: saved, but this project's file keeps it on here",)


async def test_an_unloadable_file_refuses_before_writing(tmp_path: Path) -> None:
    selection, kind, agent, _work = _bench(tmp_path, global_json="{ broken")
    result = await selection.save(kind, "global", {"pdf": False})
    assert not result.ok and "did not load" in (result.error or "")
    assert (agent / "settings.json").read_text() == "{ broken"
    snapshot = selection.snapshot(kind)
    assert snapshot.scopes["global"].writable is False
    assert any("not valid JSON" in p for p in snapshot.problems)


async def test_an_untrusted_project_cannot_be_a_target(tmp_path: Path) -> None:
    selection, kind, _agent, work = _bench(tmp_path, trusted=False)
    result = await selection.save(kind, "project", {"pdf": False})
    assert not result.ok and "not trusted" in (result.error or "")
    assert not (work / ".aelix").exists()
    assert selection.snapshot(kind).scopes["project"].writable is False


async def test_creating_the_project_file_is_flagged_first(tmp_path: Path) -> None:
    selection, _kind, _agent, work = _bench(tmp_path)
    assert selection.needs_new_project_file("project", {"pdf": False})
    assert not selection.needs_new_project_file("global", {"pdf": False})
    (work / ".aelix").mkdir()
    (work / ".aelix" / "settings.json").write_text("{}")
    assert not selection.needs_new_project_file("project", {"pdf": False})


async def test_another_sessions_save_shows_pending_and_apply_takes_it(tmp_path: Path) -> None:
    selection, kind, agent, _work = _bench(tmp_path, global_json={})
    (agent / "settings.json").write_text(json.dumps({"skills": ["-notes"]}))  # session B
    snapshot = selection.snapshot(kind)
    assert _state(snapshot, "notes").pending
    assert selection.enabled(snapshot) == {"pdf", "deploy"}


async def test_a_malformed_list_is_reported_and_ignored(tmp_path: Path) -> None:
    selection, kind, _agent, _work = _bench(tmp_path, global_json={"skills": ["-pdf", 7]})
    snapshot = selection.snapshot(kind)
    assert _state(snapshot, "pdf").saved is False
    assert any("7 is not a string" in p for p in snapshot.problems)


# --- C4: trust is an input to every read and write -----------------------------


class _SpyStorage:
    """An in-memory storage that records every scope it is asked to open."""

    def __init__(self) -> None:
        from aelix_ai.settings import InMemorySettingsStorage

        self.inner = InMemorySettingsStorage()
        self.opened: list[str] = []

    def with_lock(self, scope: str, fn: Any) -> None:
        self.opened.append(scope)
        self.inner.with_lock(scope, fn)  # type: ignore[arg-type]

    def transact(self, scope: str, fn: Any) -> None:
        self.opened.append(scope)
        self.inner.with_lock(scope, fn)  # type: ignore[arg-type]


async def test_an_untrusted_project_is_never_opened_by_a_snapshot_or_a_save(tmp_path: Path) -> None:
    storage = _SpyStorage()
    storage.inner.with_lock("project", lambda _c: json.dumps({"skills": ["-pdf"]}))
    manager = SettingsManager.from_storage(storage, project_trusted=False)  # type: ignore[arg-type]
    storage.opened.clear()
    _sel, kind, _agent, _work = _bench(tmp_path)
    selection = ResourceSelection(
        manager,
        global_base=str(tmp_path),
        project_base=str(tmp_path / ".aelix"),
        project_trusted=True,
    )
    snapshot = selection.snapshot(kind)
    assert all(s.saved for s in snapshot.states)  # the project's "-pdf" was not read
    assert not selection.needs_new_project_file("project", {"pdf": False})
    result = await selection.save(kind, "project", {"pdf": False})
    assert not result.ok
    await selection.save(kind, "global", {"pdf": False})
    assert "project" not in storage.opened


async def test_a_revoked_trust_is_honoured_after_launch(tmp_path: Path) -> None:
    selection, kind, _agent, work = _bench(tmp_path, project_json={"skills": ["-pdf"]})
    assert _state(selection.snapshot(kind), "pdf").saved is False
    assert selection.settings_manager is not None
    selection.settings_manager.set_project_trusted(False)
    assert _state(selection.snapshot(kind), "pdf").saved is True
    result = await selection.save(kind, "project", {"pdf": True})
    assert not result.ok and "not trusted" in (result.error or "")


async def test_trust_revoked_while_an_edit_waits_for_the_queue_refuses(tmp_path: Path) -> None:
    _selection, _kind, agent, work = _bench(tmp_path, project_json={})
    manager = SettingsManager.create(work, agent, project_trusted=True)
    draining = asyncio.Event()
    release = asyncio.Event()
    real_flush = manager.flush

    async def slow_flush() -> None:  # the queue drain the edit waits on
        draining.set()
        await release.wait()
        await real_flush()

    manager.flush = slow_flush  # type: ignore[method-assign]
    task = asyncio.ensure_future(manager.edit_list("project", "skills", lambda e: [*e, "-x"]))
    await draining.wait()  # the edit passed its first trust check and is draining
    manager.set_project_trusted(False)
    release.set()
    with pytest.raises(RuntimeError, match="not trusted"):
        await task
    assert "-x" not in (work / ".aelix" / "settings.json").read_text()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
async def test_an_untrusted_fifo_settings_file_never_blocks_the_panel(tmp_path: Path) -> None:
    (tmp_path / "work" / ".aelix").mkdir(parents=True)
    mkfifo = os.mkfifo  # pyright: ignore[reportAttributeAccessIssue] — POSIX-only, skipped elsewhere
    mkfifo(tmp_path / "work" / ".aelix" / "settings.json")
    selection, kind, _agent, _work = _bench(tmp_path, trusted=False, mkdirs=False)
    snapshot = await asyncio.wait_for(asyncio.to_thread(selection.snapshot, kind), timeout=5)
    assert snapshot.scopes["project"].writable is False
    assert snapshot.scopes["project"].exists is False  # not even a stat said otherwise


# --- C20: one scope per save -------------------------------------------------------


async def test_a_save_writes_one_file_only(tmp_path: Path) -> None:
    selection, kind, agent, work = _bench(tmp_path, global_json={}, project_json={})
    before_project = (work / ".aelix" / "settings.json").read_bytes()
    result = await selection.save(kind, "global", {"pdf": False, "deploy": False})
    assert result.ok and result.written == (str(agent / "settings.json"),)
    assert (work / ".aelix" / "settings.json").read_bytes() == before_project


# --- C12: a plain source is never removed by an override edit --------------------


async def test_disable_restart_reenable_keeps_a_custom_source(tmp_path: Path) -> None:
    custom = tmp_path / "opt" / "custom"
    (custom / "pdf-tools").mkdir(parents=True)
    (custom / "pdf-tools" / "SKILL.md").write_text("---\nname: pdf-tools\ndescription: d\n---\nb\n")
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir()
    work.mkdir()
    (agent / "settings.json").write_text(json.dumps({"skills": [str(custom)]}))

    def bench() -> tuple[ResourceSelection, SkillKind]:
        from aelix_coding_agent.core.resource_selection import settings_lists

        manager = SettingsManager.create(work, agent, project_trusted=True)
        lists = settings_lists(
            manager,
            "skills",
            global_base=str(agent),
            project_base=str(work / ".aelix"),
            project_trusted=True,
        )
        holder = {"result": load_skill_catalog([], lists)}
        kind = SkillKind(
            holder=holder,
            harness=lambda: None,
            cwd=str(work),
            project_trusted=True,
            project_skills_dir=str(work / ".aelix" / "skills"),
        )
        return ResourceSelection(
            manager, global_base=str(agent), project_base=str(work / ".aelix"), project_trusted=True
        ), kind

    selection, kind = bench()
    assert (await selection.save(kind, "global", {"pdf-tools": False})).ok
    assert json.loads((agent / "settings.json").read_text()) == {
        "skills": [str(custom), "-pdf-tools"]
    }
    selection, kind = bench()  # a restart: the source still finds the off skill
    assert _state(selection.snapshot(kind), "pdf-tools").saved is False
    assert (await selection.save(kind, "global", {"pdf-tools": True})).ok
    assert json.loads((agent / "settings.json").read_text()) == {"skills": [str(custom)]}


# --- X3: a file that could not be read applies nothing -------------------------


async def test_an_unreadable_file_never_turns_its_off_skills_back_on(tmp_path: Path) -> None:
    """Codex X3: the failed read used to evaluate as "no entries", so pdf came back on."""

    selection, kind, agent, _work = _bench(tmp_path, global_json={"skills": ["-pdf"]})
    holder_before = kind._holder["result"]
    (agent / "settings.json").write_text("{ broken after launch")
    snapshot = selection.snapshot(kind)
    assert snapshot.unreadable and "not valid JSON" in snapshot.unreadable[0]
    result = await selection.apply(kind, snapshot)
    assert result.applied is False
    # Round 2 (R6): the path once, then why — not "<path>: <path> is not valid JSON".
    warning = result.warning or ""
    assert warning.startswith(f"kept what was applied: {agent / 'settings.json'} is not valid JSON")
    assert warning.count(str(agent / "settings.json")) == 1
    assert kind._holder["result"] is holder_before
    # A project save, whose inherited value is the global file's, is refused too.
    (tmp_path / "work" / ".aelix").mkdir()
    (tmp_path / "work" / ".aelix" / "settings.json").write_text("{}")
    project = await selection.save(kind, "project", {"pdf": True})
    assert not project.ok and "Cannot save" in (project.error or "")
