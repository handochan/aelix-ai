"""#511 (ADR-0279 D10) — prompt templates filtered by the ``prompts`` lists, no panel."""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_coding_agent.core.prompt_selection import load_prompt_catalog
from aelix_coding_agent.core.resource_filter import ScopeList
from aelix_coding_agent.core.resource_selection import SettingsLists


def _templates(root: Path, *names: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / f"{name}.md").write_text(f"---\ndescription: {name} it\n---\nDo {name} $1\n")
    return root


def test_globs_names_and_paths_filter_templates(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    root = _templates(agent / "prompt-templates", "review", "explain", "debug", "deploy")
    lists = SettingsLists(
        ScopeList("global", ("!explain.md", "-debug", "-prompt-templates/deploy.md"), str(agent)),
        None,
    )
    catalog = load_prompt_catalog([("global", str(root))], lists)
    assert [t.name for t in catalog.templates] == ["review"]
    assert sorted(catalog.off) == ["debug", "deploy", "explain"]
    assert catalog.problems == []


def test_a_command_line_directory_is_forced_on(tmp_path: Path) -> None:
    root = _templates(tmp_path / "cli", "review")
    lists = SettingsLists(ScopeList("global", ("-review",), str(tmp_path)), None)
    catalog = load_prompt_catalog([("cli", str(root))], lists)
    assert [t.name for t in catalog.templates] == ["review"]


def test_the_project_file_overrides_and_a_path_entry_adds_a_source(tmp_path: Path) -> None:
    extra = _templates(tmp_path / "shared", "standup")
    lists = SettingsLists(
        ScopeList("global", (str(extra), "-standup"), str(tmp_path)),
        ScopeList("project", ("+standup", "-nothing"), str(tmp_path / "work" / ".aelix")),
    )
    catalog = load_prompt_catalog([], lists)
    assert [t.name for t in catalog.templates] == ["standup"]
    assert any('"-nothing" matches no prompt template' in p for p in catalog.problems)


def test_a_template_file_on_the_command_line_is_that_template_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C17: ``--prompt-template dir/review.md`` used to load (and force on) every sibling."""

    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import _load_prompt_catalog

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    shared = _templates(tmp_path / "shared", "review", "secret-sibling")
    work = tmp_path / "work"
    work.mkdir()
    manager = SettingsManager.create(work, tmp_path / "agent", project_trusted=True)
    catalog = _load_prompt_catalog(
        Args(prompt_templates=[str(shared / "review.md")]), str(work), True, manager
    )
    assert [t.name for t in catalog.templates] == ["review"]


def test_a_reload_sees_changed_added_and_removed_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/reload`` rebuilds through ``_load_prompt_catalog``; it reads the disk each time."""

    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import _load_prompt_catalog

    agent = tmp_path / "agent"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    root = _templates(agent / "prompt-templates", "review", "explain")
    work = tmp_path / "work"
    work.mkdir()
    manager = SettingsManager.create(work, agent, project_trusted=True)
    first = _load_prompt_catalog(Args(), str(work), True, manager)
    assert sorted(t.name for t in first.templates) == ["explain", "review"]
    (root / "review.md").write_text("---\ndescription: CHANGED\n---\nnew body\n")
    (root / "explain.md").unlink()
    _templates(root, "added")
    second = _load_prompt_catalog(Args(), str(work), True, manager)
    assert sorted(t.name for t in second.templates) == ["added", "review"]
    assert next(t for t in second.templates if t.name == "review").description == "CHANGED"


def test_the_project_file_decides_over_the_global_one_for_templates(tmp_path: Path) -> None:
    root = _templates(tmp_path / "project-templates", "standup")
    lists = SettingsLists(
        ScopeList("global", ("-standup",), str(tmp_path)),
        ScopeList("project", ("+standup",), str(tmp_path / "work" / ".aelix")),
    )
    catalog = load_prompt_catalog([("project", str(root))], lists)
    assert [t.name for t in catalog.templates] == ["standup"]
