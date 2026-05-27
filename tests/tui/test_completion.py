"""Sprint 6h₁₀d (§D) — DescriptorCommandCompleter unit tests.

Pure: no Application/TTY. A fake routes dict + a ``Document`` drive the completer
directly. The store is read by reference, so mutating it mid-session must change
the offered completions (the "live source" contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aelix_coding_agent.tui.commands import BuiltinCommand
from aelix_coding_agent.tui.completion import (
    DescriptorCommandCompleter,
    FileMentionCompleter,
    wants_completion,
)
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document


@dataclass
class _Route:
    """Stand-in for a CommandRoutePayload (read via getattr defensively)."""

    command: str
    description: str = ""
    keybind: str | None = None


def _complete(routes: dict[str, Any], text: str) -> list[Any]:
    completer = DescriptorCommandCompleter(lambda: routes)
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def _complete_union(
    routes: dict[str, Any], builtins: list[BuiltinCommand], text: str
) -> list[Any]:
    completer = DescriptorCommandCompleter(lambda: routes, builtins=builtins)
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def test_empty_command_is_skipped() -> None:
    # A route whose command is "" must not yield a bare "/" completion.
    routes = {"ext:blank": _Route(command=""), "ext:deploy": _Route(command="deploy")}
    out = _complete(routes, "/")
    assert [c.text for c in out] == ["/deploy"]


def test_same_command_is_deduped() -> None:
    # A cross-namespace re-point can leave two keys with one command; the menu
    # must offer it once.
    routes = {
        "a:deploy": _Route(command="deploy", description="old"),
        "b:deploy": _Route(command="deploy", description="new"),
    }
    out = _complete(routes, "/dep")
    assert [c.text for c in out] == ["/deploy"]


def test_slash_prefix_yields_matching_command() -> None:
    routes = {"ext:deploy": _Route(command="deploy", description="Deploy the app")}
    out = _complete(routes, "/de")
    assert len(out) == 1
    completion = out[0]
    assert completion.text == "/deploy"
    assert completion.display_text == "deploy"
    # Replaces the whole typed slash word.
    assert completion.start_position == -len("/de")


def test_non_slash_line_yields_nothing() -> None:
    routes = {"ext:deploy": _Route(command="deploy", description="Deploy the app")}
    assert _complete(routes, "deploy") == []
    assert _complete(routes, "hello /deploy") == []


def test_description_in_display_meta() -> None:
    routes = {"ext:deploy": _Route(command="deploy", description="Deploy the app")}
    out = _complete(routes, "/")
    assert len(out) == 1
    assert "Deploy the app" in out[0].display_meta_text


def test_keybind_appended_to_display_meta() -> None:
    routes = {"ext:deploy": _Route(command="deploy", description="Deploy", keybind="c-d")}
    out = _complete(routes, "/dep")
    assert len(out) == 1
    assert out[0].display_meta_text == "Deploy [c-d]"


def test_live_source_mutation_surfaces_new_completion() -> None:
    routes: dict[str, Any] = {}
    completer = DescriptorCommandCompleter(lambda: routes)

    doc = Document(text="/de", cursor_position=3)
    assert list(completer.get_completions(doc, CompleteEvent())) == []

    # Mutate the live source in place — the same completer must now offer it.
    routes["ext:deploy"] = _Route(command="deploy", description="Deploy")
    out = list(completer.get_completions(doc, CompleteEvent()))
    assert [c.text for c in out] == ["/deploy"]


def test_two_routes_filter_by_prefix() -> None:
    routes = {
        "ext:deploy": _Route(command="deploy", description="Deploy"),
        "ext:destroy": _Route(command="destroy", description="Destroy"),
        "ext:build": _Route(command="build", description="Build"),
    }
    # "/de" matches deploy + destroy but not build.
    out = _complete(routes, "/de")
    assert sorted(c.text for c in out) == ["/deploy", "/destroy"]

    # "/b" matches only build.
    out = _complete(routes, "/b")
    assert [c.text for c in out] == ["/build"]


def test_empty_slash_lists_all_commands() -> None:
    routes = {
        "ext:deploy": _Route(command="deploy"),
        "ext:build": _Route(command="build"),
    }
    out = _complete(routes, "/")
    assert sorted(c.text for c in out) == ["/build", "/deploy"]


def test_faulty_source_is_contained() -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("boom")

    completer = DescriptorCommandCompleter(_boom)
    doc = Document(text="/de", cursor_position=3)
    assert list(completer.get_completions(doc, CompleteEvent())) == []


# === Sprint 6h₁₂a — built-in ∪ descriptor-route palette =====================


def test_builtin_prefix_yields_builtin() -> None:
    builtins = [BuiltinCommand("help", "List available commands")]
    out = _complete_union({}, builtins, "/h")
    assert [c.text for c in out] == ["/help"]
    assert out[0].display_text == "help"
    assert "List available commands" in out[0].display_meta_text


def test_union_lists_builtins_and_routes() -> None:
    builtins = [BuiltinCommand("help", "List available commands")]
    routes = {"ext:deploy": _Route(command="deploy", description="Deploy")}
    out = _complete_union(routes, builtins, "/")
    texts = [c.text for c in out]
    assert "/help" in texts
    assert "/deploy" in texts
    # Built-ins are listed first.
    assert texts[0] == "/help"


def test_builtin_wins_dedup_over_route() -> None:
    # A descriptor route that re-points "help" must not shadow the built-in.
    builtins = [BuiltinCommand("help", "Built-in help")]
    routes = {"ext:help": _Route(command="help", description="Extension help")}
    out = _complete_union(routes, builtins, "/h")
    assert [c.text for c in out] == ["/help"]
    assert "Built-in help" in out[0].display_meta_text


def test_union_without_routes_still_offers_builtins() -> None:
    builtins = [
        BuiltinCommand("help", "List available commands"),
        BuiltinCommand("quit", "Exit Aelix"),
    ]
    out = _complete_union({}, builtins, "/")
    assert sorted(c.text for c in out) == ["/help", "/quit"]


def test_union_non_slash_yields_nothing() -> None:
    builtins = [BuiltinCommand("help", "List available commands")]
    assert _complete_union({}, builtins, "help") == []


# === Sprint 6h₁₄a (ADR-0121) — @file mention completer ======================


def _file_complete(cwd: Path, text: str) -> list[Any]:
    completer = FileMentionCompleter(str(cwd))
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def _make_tree(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "foo.py").write_text("x")
    (root / "src" / "fizz.py").write_text("x")
    (root / "setup.py").write_text("x")
    (root / ".hidden").write_text("x")


def test_at_mention_lists_matching_paths(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    out = _file_complete(tmp_path, "@s")
    texts = sorted(c.text for c in out)
    # A directory gets a trailing slash; a file does not.
    assert texts == ["@setup.py", "@src/"]


def test_at_mention_drills_into_dir(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    out = _file_complete(tmp_path, "@src/f")
    assert sorted(c.text for c in out) == ["@src/fizz.py", "@src/foo.py"]


def test_at_mention_replaces_whole_token(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    out = _file_complete(tmp_path, "@set")
    assert len(out) == 1
    assert out[0].text == "@setup.py"
    assert out[0].start_position == -len("@set")


def test_at_mention_works_mid_line(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    out = _file_complete(tmp_path, "please read @set")
    assert [c.text for c in out] == ["@setup.py"]


def test_at_mention_hides_dotfiles_unless_dot_typed(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    assert all(".hidden" not in c.text for c in _file_complete(tmp_path, "@"))
    # Explicitly typing a dot surfaces them.
    assert any(".hidden" in c.text for c in _file_complete(tmp_path, "@.h"))


def test_at_mention_non_at_token_yields_nothing(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    assert _file_complete(tmp_path, "setup") == []
    assert _file_complete(tmp_path, "email@host") == []  # @ not at token start


def test_at_mention_missing_dir_yields_nothing(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    assert _file_complete(tmp_path, "@nope/x") == []


def test_at_mention_respects_max_results(tmp_path: Path) -> None:
    for i in range(50):
        (tmp_path / f"file{i:02d}.txt").write_text("x")
    completer = FileMentionCompleter(str(tmp_path), max_results=10)
    doc = Document(text="@file", cursor_position=len("@file"))
    out = list(completer.get_completions(doc, CompleteEvent()))
    assert len(out) == 10


def test_wants_completion_triggers() -> None:
    assert wants_completion("/he") is True
    assert wants_completion("@src") is True
    assert wants_completion("read @sr") is True
    assert wants_completion("hello world") is False
    assert wants_completion("read @src ") is False  # token ended (trailing space)
    assert wants_completion("") is False
