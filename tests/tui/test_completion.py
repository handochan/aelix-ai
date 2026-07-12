"""Sprint 6h₁₀d (§D) — DescriptorCommandCompleter unit tests.

Pure: no Application/TTY. A fake routes dict + a ``Document`` drive the completer
directly. The store is read by reference, so mutating it mid-session must change
the offered completions (the "live source" contract).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.tui import completion as completion_mod
from aelix_coding_agent.tui.commands import BuiltinCommand
from aelix_coding_agent.tui.completion import (
    DescriptorCommandCompleter,
    FileMentionCompleter,
    _completion_value,
    _extract_mention,
    _fd_enumerate,
    _fuzzy_score,
    wants_completion,
)
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.buffer import Buffer, CompletionState
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.output import DummyOutput

if TYPE_CHECKING:
    from collections.abc import Iterator


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


def _complete_ext(
    routes: dict[str, Any],
    builtins: list[BuiltinCommand],
    ext_commands: list[tuple[str, str]],
    text: str,
) -> list[Any]:
    completer = DescriptorCommandCompleter(
        lambda: routes, builtins=builtins, get_ext_commands=lambda: ext_commands
    )
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def test_ext_commands_are_offered() -> None:
    # Issue #9: extension commands join the palette.
    out = _complete_ext({}, [], [("hello", "Greet"), ("deploy", "Ship")], "/hel")
    assert [c.text for c in out] == ["/hello"]


def test_builtin_wins_over_ext_command_collision() -> None:
    builtins = [BuiltinCommand("model", "Pick a model")]
    out = _complete_ext({}, builtins, [("model", "ext model")], "/mod")
    # Only the built-in /model is offered (ext is deduped against it).
    assert [c.text for c in out] == ["/model"]


def test_descriptor_route_wins_over_ext_command_collision() -> None:
    routes = {"ns:deploy": _Route(command="deploy", description="route deploy")}
    out = _complete_ext(routes, [], [("deploy", "ext deploy")], "/dep")
    # Exactly one /deploy (the descriptor route); the ext one is deduped.
    assert [c.text for c in out] == ["/deploy"]


def test_ext_commands_absent_when_source_unset() -> None:
    completer = DescriptorCommandCompleter(lambda: {}, builtins=[])
    doc = Document(text="/", cursor_position=1)
    assert list(completer.get_completions(doc, CompleteEvent())) == []


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
    # Issue #39: a non-trivial prefix is now a fuzzy whole-tree search. "s"
    # subsequence-matches setup.py + src/ (root prefix hits, ranked first) plus
    # the files under src/. The top results must include the two root entries,
    # and a directory still carries a trailing slash while a file does not.
    _make_tree(tmp_path)
    texts = {c.text for c in _file_complete(tmp_path, "@s")}
    assert {"@setup.py", "@src/"} <= texts
    assert "@src/" in texts  # directory → trailing slash
    assert "@setup.py" in texts  # file → no trailing slash


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


# === Issue #39 — fuzzy whole-tree search + quoted-path mentions ==============


def _deep_tree(root: Path) -> None:
    (root / "src" / "deep").mkdir(parents=True)
    (root / "src" / "completion.py").write_text("x")
    (root / "src" / "deep" / "widget_helper.py").write_text("x")
    (root / "README.md").write_text("x")
    (root / "my file.txt").write_text("x")  # a space in the name
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("x")
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "objects" / "abc").write_text("x")


def test_at_mention_fuzzy_matches_across_path(tmp_path: Path) -> None:
    # A subsequence that spans directory components matches the deep file.
    _deep_tree(tmp_path)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == [
        "@src/deep/widget_helper.py"
    ]
    # "comp" fuzzy-finds the nested completion.py.
    assert "@src/completion.py" in {c.text for c in _file_complete(tmp_path, "@comp")}


def test_at_mention_empty_prefix_lists_cwd_toplevel(tmp_path: Path) -> None:
    # An empty prefix is a cheap top-level listing (not a whole-tree walk): only
    # direct children of cwd, dotfiles hidden.
    _deep_tree(tmp_path)
    texts = {c.text for c in _file_complete(tmp_path, "@")}
    assert "@README.md" in texts
    assert "@src/" in texts
    assert '@"my file.txt"' in texts  # space → quoted
    # A nested file is NOT listed at the top level.
    assert "@src/completion.py" not in texts
    # Hidden .git is not listed at the top level (dotfile).
    assert not any(".git" in t for t in texts)


def test_at_mention_trailing_slash_drills_one_level(tmp_path: Path) -> None:
    _deep_tree(tmp_path)
    texts = {c.text for c in _file_complete(tmp_path, "@src/")}
    assert texts == {"@src/completion.py", "@src/deep/"}


def test_at_mention_fuzzy_excludes_heavy_dirs(tmp_path: Path) -> None:
    # node_modules and .git are pruned from the fuzzy tree.
    _deep_tree(tmp_path)
    assert _file_complete(tmp_path, "@indexjs") == []  # node_modules/pkg/index.js
    assert _file_complete(tmp_path, "@objabc") == []  # .git/objects/abc


def test_at_mention_quoted_prefix_completes_with_spaces(tmp_path: Path) -> None:
    # A quoted mention keeps a space-containing partial as one token and inserts
    # the closing quote for a file.
    _deep_tree(tmp_path)
    out = _file_complete(tmp_path, '@"my fi')
    assert '@"my file.txt"' in {c.text for c in out}


def test_at_mention_space_path_inserted_quoted(tmp_path: Path) -> None:
    # Even from an UNquoted prefix, a completion whose path has a space is quoted.
    _deep_tree(tmp_path)
    out = _file_complete(tmp_path, "@my")
    assert '@"my file.txt"' in {c.text for c in out}


def test_wants_completion_quoted_mention_with_space() -> None:
    assert wants_completion('@"my fi') is True
    assert wants_completion('read @"my long fi') is True
    # A CLOSED quote is a complete mention → no longer completing.
    assert wants_completion('@"done" and') is False


def test_extract_mention_variants() -> None:
    assert _extract_mention("@comp") == ("comp", False, 5)
    assert _extract_mention('@"my fi') == ("my fi", True, 7)
    assert _extract_mention("say @src/de") == ("src/de", False, 7)
    assert _extract_mention("foo@bar") is None  # @ not at token start
    assert _extract_mention("no mention here") is None
    assert _extract_mention('@"closed" x') is None  # closing quote typed


def test_completion_value_quoting() -> None:
    assert _completion_value("src/foo.py", False, False) == "@src/foo.py"
    assert _completion_value("src", True, False) == "@src/"
    assert _completion_value("a b.txt", False, False) == '@"a b.txt"'  # space → quoted
    assert _completion_value("a b", True, False) == '@"a b/'  # dir keeps quote open
    assert _completion_value("plain", True, True) == '@"plain/'  # quoted mode dir
    assert _completion_value("plain.py", False, True) == '@"plain.py"'  # quoted mode file


def test_fuzzy_score_ranks_prefix_before_scattered() -> None:
    # An exact prefix outranks a scattered subsequence match.
    prefix = _fuzzy_score("comp", "completion.py")
    scattered = _fuzzy_score("comp", "src/my_composite_map.py")
    assert prefix is not None and scattered is not None
    assert prefix > scattered
    # A non-subsequence returns None.
    assert _fuzzy_score("xyz", "completion.py") is None


def test_fd_enumerate_builds_safe_argv(monkeypatch: Any, tmp_path: Path) -> None:
    # fd is invoked with a fixed argv (no shell, no user input) and its output is
    # parsed into relative paths with any leading ./ and trailing / stripped.
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = "src/foo.py\n./README.md\nsrc/deep/\n"

    def _fake_run(argv: list[str], **kwargs: Any) -> _Proc:
        calls.append(argv)
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs.get("timeout")  # a timeout is always set
        return _Proc()

    monkeypatch.setattr(completion_mod.subprocess, "run", _fake_run)
    out = _fd_enumerate("fd", tmp_path)
    assert out == ["src/foo.py", "README.md", "src/deep"]
    # No user-controlled pattern in the argv (injection-free).
    assert "--type" in calls[0] and "-e" not in calls[0]


def test_enumerate_tree_falls_back_to_walk_without_fd(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # With no fd binary, the dependency-free os.walk enumerator is used and still
    # produces fuzzy matches (proving the fallback path is wired).
    _deep_tree(tmp_path)
    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == [
        "@src/deep/widget_helper.py"
    ]


def test_fuzzy_falls_back_to_dir_listing_on_no_hit(tmp_path: Path) -> None:
    # A prefix that fuzzy-matches nothing but names a real directory drills it.
    _deep_tree(tmp_path)
    assert _file_complete(tmp_path, "@zzz/x") == []  # missing dir, no fuzzy hit


def test_extract_mention_quote_aware_at_inside_open_quote() -> None:
    # Issue #39 review: an '@' typed INSIDE an open '@"' is a literal path char,
    # not a fresh mention — the whole quoted span (incl. the inner @) is one
    # mention, so accepting a completion replaces the whole thing (no broken
    # nested @-path buffer).
    m = _extract_mention('@"my dir @x')
    assert m == ("my dir @x", True, len('@"my dir @x'))
    # A closed quote followed by a fresh open quote → the second, open mention.
    assert _extract_mention('@"a" @"b c') == ("b c", True, len('@"b c'))


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _stub_fd(monkeypatch: Any, stdout: str, returncode: int = 0) -> None:
    """Make the code believe fd is on PATH and returns ``stdout``."""

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: "fd")
    monkeypatch.setattr(
        completion_mod.subprocess,
        "run",
        lambda *a, **k: _FakeProc(stdout, returncode),
    )


def test_at_mention_fd_path_excludes_heavy_dirs(monkeypatch: Any, tmp_path: Path) -> None:
    # Issue #39 review (MEDIUM): the fd path must exclude _EXCLUDE_DIRS just like
    # the walk path, even when fd (relying only on .gitignore) would surface them.
    _deep_tree(tmp_path)
    _stub_fd(monkeypatch, "node_modules/pkg/index.js\nsrc/completion.py\n.git/objects/abc\n")
    # node_modules and .git are pruned regardless of the enumerator.
    assert _file_complete(tmp_path, "@indexjs") == []
    assert "@src/completion.py" in {c.text for c in _file_complete(tmp_path, "@comp")}


def test_at_mention_fd_present_end_to_end(monkeypatch: Any, tmp_path: Path) -> None:
    # The headline fd-when-present path, driven end to end: fd output flows
    # through _enumerate_tree → fuzzy → is_dir resolution (a dir gets a slash).
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "src" / "deep").mkdir()
    _stub_fd(monkeypatch, "src/app.py\nsrc/deep\n")
    assert [c.text for c in _file_complete(tmp_path, "@app")] == ["@src/app.py"]
    # src/deep resolves as a real directory → trailing slash.
    assert "@src/deep/" in {c.text for c in _file_complete(tmp_path, "@deep")}


def test_fd_failure_falls_back_to_walk(monkeypatch: Any, tmp_path: Path) -> None:
    # fd on PATH but the subprocess fails (nonzero) or raises → the completer must
    # transparently fall through to the os.walk enumerator.
    _deep_tree(tmp_path)
    # (a) nonzero return code.
    _stub_fd(monkeypatch, "", returncode=1)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == [
        "@src/deep/widget_helper.py"
    ]

    # (b) subprocess raises (timeout / OSError) → also falls back.
    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: "fd")

    def _boom(*a: Any, **k: Any) -> None:
        raise completion_mod.subprocess.TimeoutExpired(cmd="fd", timeout=2.0)

    monkeypatch.setattr(completion_mod.subprocess, "run", _boom)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == [
        "@src/deep/widget_helper.py"
    ]


def test_at_mention_max_results_ordering_is_stable(tmp_path: Path) -> None:
    # Issue #39 review (NIT): the top-N must be a DETERMINISTIC ordered set, not
    # just any 10. Equal-scoring ties break by (shorter path, lexicographic).
    for i in range(50):
        (tmp_path / f"file{i:02d}.txt").write_text("x")
    completer = FileMentionCompleter(str(tmp_path), max_results=5)
    doc = Document(text="@file", cursor_position=len("@file"))
    out = [c.text for c in completer.get_completions(doc, CompleteEvent())]
    # All 50 fuzzy-match "file" with the same score → lexicographic tiebreak wins.
    assert out == ["@file00.txt", "@file01.txt", "@file02.txt", "@file03.txt", "@file04.txt"]


def test_at_mention_quoted_dir_drill_in_keeps_quote_open(tmp_path: Path) -> None:
    # A quoted mention resolving to a directory keeps the quote OPEN so the user
    # can keep drilling; re-completing the drilled path still works.
    (tmp_path / "my dir").mkdir()
    (tmp_path / "my dir" / "note.md").write_text("x")
    # '@"my ' lists the space-dir with an OPEN quote + trailing slash.
    assert '@"my dir/' in {c.text for c in _file_complete(tmp_path, '@"my ')}
    # After drilling in, the trailing-slash listing drills that directory.
    assert '@"my dir/note.md"' in {c.text for c in _file_complete(tmp_path, '@"my dir/')}


def test_at_mention_symlink_dir_does_not_hang(tmp_path: Path) -> None:
    # os.walk uses followlinks=False, so a self-referential dir symlink cannot
    # loop the enumeration; the completer returns promptly.
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "thing.py").write_text("x")
    try:
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlinks unsupported here")
    assert "@real/thing.py" in {c.text for c in _file_complete(tmp_path, "@thing")}


# === Sprint 6h₂₆ (ADR-0156) — marked completions-menu CONTROL ===============
#
# The custom dropdown control adds a selected-row marker + a (current/total)
# match counter. These tests drive ``_MarkedCompletionsMenuControl.create_content``
# headlessly under ``set_app`` over a Buffer carrying a hand-built
# ``CompletionState`` — no Application.run, no TTY (pure UIControl).


@contextmanager
def _menu_control(
    completions: list[Completion], complete_index: int | None
) -> Iterator[tuple[Any, Buffer]]:
    """Yield a ``_MarkedCompletionsMenuControl`` + the focused buffer it reads.

    The control resolves completions via ``get_app().current_buffer``, so a tiny
    Application is mounted (focused on the buffer) and entered with ``set_app``;
    the buffer's ``complete_state`` is set directly to the requested completions.
    """

    from aelix_coding_agent.tui.chrome import _MarkedCompletionsMenuControl

    buf = Buffer(name="input")
    ctrl = BufferControl(buffer=buf)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = Application(layout=Layout(Window(ctrl), focused_element=ctrl))
        with set_app(app):
            buf.set_document(Document("/", 1), bypass_readonly=True)
            buf.complete_state = CompletionState(
                original_document=buf.document,
                completions=completions,
                complete_index=complete_index,
            )
            yield _MarkedCompletionsMenuControl(), buf


def _line_text(fragments: Any) -> str:
    return "".join(text for _style, text in fragments)


def test_menu_marker_and_counter_render() -> None:
    completions = [
        Completion("/deploy", display="deploy", display_meta="Deploy the app"),
        Completion("/destroy", display="destroy", display_meta="Destroy the app"),
    ]
    with _menu_control(completions, complete_index=1) as (control, _buf):
        content = control.create_content(40, 10)
        # (a) one synthetic counter row beyond the two completions.
        assert content.line_count == len(completions) + 1
        # (d) cursor tracks the highlighted index.
        assert content.cursor_position.y == 1
        non_current = _line_text(content.get_line(0))
        current = _line_text(content.get_line(1))
        counter = _line_text(content.get_line(content.line_count - 1))
        # (b) the marker leads the current row; a plain space leads the other.
        assert current.startswith("→")
        assert non_current.startswith(" ") and not non_current.startswith("→")
        # The description column (display_meta) still renders.
        assert "Deploy the app" in non_current
        # (c) the trailing row is the 1-based match counter.
        assert "(2/2)" in counter


def test_menu_counter_is_none_index_safe() -> None:
    # complete_index=None (nothing highlighted) must render "(1/N)" without
    # crashing — confirms the ``(index or 0) + 1`` guard and a y=0 cursor.
    completions = [
        Completion("/a", display="a"),
        Completion("/b", display="b"),
        Completion("/c", display="c"),
    ]
    with _menu_control(completions, complete_index=None) as (control, _buf):
        content = control.create_content(40, 10)
        assert content.cursor_position.y == 0
        counter = _line_text(content.get_line(content.line_count - 1))
        assert "(1/3)" in counter


def test_menu_empty_state_is_inert() -> None:
    # With complete_state=None the control returns an empty UIContent and
    # preferred_height is 0 (renders nothing when there is nothing to show).
    from aelix_coding_agent.tui.chrome import _MarkedCompletionsMenuControl

    buf = Buffer(name="input")
    ctrl = BufferControl(buffer=buf)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = Application(layout=Layout(Window(ctrl), focused_element=ctrl))
        with set_app(app):
            buf.complete_state = None
            control = _MarkedCompletionsMenuControl()
            content = control.create_content(40, 10)
            assert content.line_count == 0
            assert control.preferred_height(40, 10, True, None) == 0


def test_menu_preferred_height_counts_counter_row() -> None:
    completions = [Completion("/x", display="x"), Completion("/y", display="y")]
    with _menu_control(completions, complete_index=0) as (control, _buf):
        # preferred_height includes the synthetic counter row (+1).
        assert control.preferred_height(40, 10, True, None) == len(completions) + 1
