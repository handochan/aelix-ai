"""Sprint 6h₁₀b (ADR-0104) — AelixFooterData unit tests."""

from __future__ import annotations

from pathlib import Path

from aelix_coding_agent.extensions.widget_protocols import ReadonlyFooterDataProvider
from aelix_coding_agent.tui.footer_data import AelixFooterData

# ---------------------------------------------------------------------------
# get_git_branch
# ---------------------------------------------------------------------------


def test_branch_from_ref(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    fd = AelixFooterData(str(tmp_path))
    assert fd.get_git_branch() == "main"


def test_branch_feature_slash(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/feature/my-branch\n", encoding="utf-8")

    fd = AelixFooterData(str(tmp_path))
    assert fd.get_git_branch() == "feature/my-branch"


def test_branch_detached_head(tmp_path: Path) -> None:
    sha = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    assert len(sha) == 40

    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(sha + "\n", encoding="utf-8")

    fd = AelixFooterData(str(tmp_path))
    assert fd.get_git_branch() == sha[:7]


def test_branch_no_git_dir(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    assert fd.get_git_branch() is None


def test_branch_git_is_file(tmp_path: Path) -> None:
    """Worktree: .git is a plain file — return None this sprint."""
    git = tmp_path / ".git"
    git.write_text("gitdir: /some/other/path\n", encoding="utf-8")

    fd = AelixFooterData(str(tmp_path))
    assert fd.get_git_branch() is None


def test_branch_missing_head(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    # No HEAD file

    fd = AelixFooterData(str(tmp_path))
    assert fd.get_git_branch() is None


# ---------------------------------------------------------------------------
# set_status / get_extension_statuses
# ---------------------------------------------------------------------------


def test_set_status_add(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    fd.set_status("a", "x")
    assert fd.get_extension_statuses() == {"a": "x"}


def test_set_status_update(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    fd.set_status("a", "x")
    fd.set_status("a", "y")
    assert fd.get_extension_statuses() == {"a": "y"}


def test_set_status_remove(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    fd.set_status("a", "x")
    fd.set_status("a", None)
    assert fd.get_extension_statuses() == {}


def test_set_status_remove_missing_is_noop(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    fd.set_status("nonexistent", None)  # must not raise
    assert fd.get_extension_statuses() == {}


def test_get_extension_statuses_returns_copy(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    fd.set_status("k", "v")
    snapshot = fd.get_extension_statuses()
    snapshot["k"] = "mutated"
    assert fd.get_extension_statuses() == {"k": "v"}


# ---------------------------------------------------------------------------
# on_branch_change / notify_branch_change
# ---------------------------------------------------------------------------


def test_on_branch_change_fires(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    calls: list[int] = []
    fd.on_branch_change(lambda: calls.append(1))
    fd.notify_branch_change()
    assert calls == [1]


def test_on_branch_change_fires_multiple(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    calls: list[str] = []
    fd.on_branch_change(lambda: calls.append("a"))
    fd.on_branch_change(lambda: calls.append("b"))
    fd.notify_branch_change()
    assert calls == ["a", "b"]


def test_on_branch_change_unsubscribe(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    calls: list[int] = []
    unsub = fd.on_branch_change(lambda: calls.append(1))
    unsub()
    fd.notify_branch_change()
    assert calls == []


def test_on_branch_change_unsubscribe_idempotent(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    calls: list[int] = []
    unsub = fd.on_branch_change(lambda: calls.append(1))
    unsub()
    unsub()  # second call must not raise
    fd.notify_branch_change()
    assert calls == []


def test_on_branch_change_unsubscribe_leaves_others(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    calls: list[str] = []
    unsub_a = fd.on_branch_change(lambda: calls.append("a"))
    fd.on_branch_change(lambda: calls.append("b"))
    unsub_a()
    fd.notify_branch_change()
    assert calls == ["b"]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_isinstance_readonly_footer_data_provider(tmp_path: Path) -> None:
    fd = AelixFooterData(str(tmp_path))
    assert isinstance(fd, ReadonlyFooterDataProvider)
