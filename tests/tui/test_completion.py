"""Sprint 6h₁₀d (§D) — DescriptorCommandCompleter unit tests.

Pure: no Application/TTY. A fake routes dict + a ``Document`` drive the completer
directly. The store is read by reference, so mutating it mid-session must change
the offered completions (the "live source" contract).
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.cli import config as cli_config
from aelix_coding_agent.tui import completion as completion_mod
from aelix_coding_agent.tui.commands import BuiltinCommand
from aelix_coding_agent.tui.completion import (
    _FD_TIMEOUT,
    DescriptorCommandCompleter,
    FileMentionCompleter,
    _completion_value,
    _enumerate_tree,
    _extract_mention,
    _fd_enumerate,
    _fuzzy_score,
    wants_completion,
)
from aelix_coding_agent.util import tools_manager
from aelix_coding_agent.util.tools_manager import get_tool_path
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


def _complete_union(routes: dict[str, Any], builtins: list[BuiltinCommand], text: str) -> list[Any]:
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
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == ["@src/deep/widget_helper.py"]
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


def _stub_fd(
    monkeypatch: Any, tmp_path: Path, stdout: str | bytes, returncode: int = 0
) -> list[list[str]]:
    """Make the code believe ``fd`` is on PATH and answers ``stdout``.

    The seam is ``completion_mod.run_contained`` since #221, and MISSING that
    re-seam is GREEN rather than red unless every consumer checks: no CI leg has
    ``fd`` installed, so a stub left on ``subprocess.run`` leaves the real
    ``run_contained`` to answer ``FileNotFoundError``, ``_fd_enumerate`` to
    return ``None`` and the walk fallback to satisfy the very assertions the
    case makes about the fd path (#221 review TP9). Hence the returned ``calls``
    list — every consumer asserts the seam was actually entered.

    The seam also pins the CALL: exactly ``timeout=_FD_TIMEOUT`` and
    ``cwd=str(base)`` and nothing else, so a site that grew a
    ``capture_output``/``text``/``env`` kwarg back is red here.
    """

    calls: list[list[str]] = []
    raw = stdout.encode() if isinstance(stdout, str) else stdout

    def _fake_run_contained(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(argv))
        assert kwargs.pop("timeout", None) == _FD_TIMEOUT
        assert kwargs.pop("cwd", None) == str(tmp_path)
        assert not kwargs, f"the fd site passed run_contained kwargs it should not: {kwargs}"
        return subprocess.CompletedProcess(list(argv), returncode, raw, b"")

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: "fd")
    monkeypatch.setattr(completion_mod, "run_contained", _fake_run_contained)
    return calls


def test_fd_enumerate_builds_safe_argv(monkeypatch: Any, tmp_path: Path) -> None:
    # fd is invoked with a fixed argv (no shell, no user input) and its output is
    # parsed into relative paths with any leading ./ and trailing / stripped.
    calls = _stub_fd(monkeypatch, tmp_path, "src/foo.py\n./README.md\nsrc/deep/\n")
    out = _fd_enumerate("fd", tmp_path)
    assert out == ["src/foo.py", "README.md", "src/deep"]
    assert len(calls) == 1
    # #231: fd is asked for files, directories AND symlinks. Without ``--type l``
    # a symlink is neither, so every link the walk listed was missing from fd's
    # answer (measured: the walk returned a dir link, a file link, a broken link
    # and a self-loop; fd returned none of the four).
    types = [calls[0][i + 1] for i, tok in enumerate(calls[0]) if tok == "--type"]
    assert types == ["f", "d", "l"]
    # No user-controlled pattern in the argv (injection-free).
    assert "-e" not in calls[0]


def test_fd_never_goes_through_subprocess_run(monkeypatch: Any, tmp_path: Path) -> None:
    # #221: the site spawns through run_contained, so the tree fd started dies
    # with it. A regression to plain subprocess.run would leak that tree AND, on
    # Windows, hang in CPython's untimed post-kill communicate() — neither of
    # which a parsing assertion can see. So the OLD door is nailed shut.
    calls = _stub_fd(monkeypatch, tmp_path, "src/foo.py\n")

    def _forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the fd site went through subprocess.run, not run_contained")

    monkeypatch.setattr(completion_mod.subprocess, "run", _forbidden)
    assert _fd_enumerate("fd", tmp_path) == ["src/foo.py"]
    assert len(calls) == 1


def test_fd_enumerate_replaces_undecodable_bytes(monkeypatch: Any, tmp_path: Path) -> None:
    # ``text=True`` decoded with the LOCALE codec and STRICT errors, so one
    # latin-1 filename under the tree raised UnicodeDecodeError — a ValueError,
    # which `except (OSError, SubprocessError)` does not catch — out of the
    # completer on a keystroke. utf-8/replace makes it one wrong candidate
    # instead (#221 §A.4).
    calls = _stub_fd(monkeypatch, tmp_path, b"src/caf\xe9.py\n")
    assert _fd_enumerate("fd", tmp_path) == ["src/caf�.py"]
    assert len(calls) == 1


def test_enumerate_tree_falls_back_to_walk_without_fd(monkeypatch: Any, tmp_path: Path) -> None:
    # With no fd binary, the dependency-free os.walk enumerator is used and still
    # produces fuzzy matches (proving the fallback path is wired).
    _deep_tree(tmp_path)
    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == ["@src/deep/widget_helper.py"]


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


def test_at_mention_fd_path_excludes_heavy_dirs(monkeypatch: Any, tmp_path: Path) -> None:
    # Issue #39 review (MEDIUM): the fd path must exclude _EXCLUDE_DIRS just like
    # the walk path, even when fd (relying only on .gitignore) would surface them.
    _deep_tree(tmp_path)
    calls = _stub_fd(
        monkeypatch, tmp_path, "node_modules/pkg/index.js\nsrc/completion.py\n.git/objects/abc\n"
    )
    # node_modules and .git are pruned regardless of the enumerator.
    assert _file_complete(tmp_path, "@indexjs") == []
    assert "@src/completion.py" in {c.text for c in _file_complete(tmp_path, "@comp")}
    # One enumeration per completer: _file_complete builds a fresh
    # FileMentionCompleter and the TTL cache lives on the instance.
    assert len(calls) == 2


def test_at_mention_fd_present_end_to_end(monkeypatch: Any, tmp_path: Path) -> None:
    # The headline fd-when-present path, driven end to end: fd output flows
    # through _enumerate_tree → fuzzy → is_dir resolution (a dir gets a slash).
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "src" / "deep").mkdir()
    calls = _stub_fd(monkeypatch, tmp_path, "src/app.py\nsrc/deep\n")
    assert [c.text for c in _file_complete(tmp_path, "@app")] == ["@src/app.py"]
    # src/deep resolves as a real directory → trailing slash.
    assert "@src/deep/" in {c.text for c in _file_complete(tmp_path, "@deep")}
    assert len(calls) == 2


def test_fd_failure_falls_back_to_walk(monkeypatch: Any, tmp_path: Path) -> None:
    # fd on PATH but the subprocess fails (nonzero) or raises → the completer must
    # transparently fall through to the os.walk enumerator.
    _deep_tree(tmp_path)
    # (a) nonzero return code.
    calls = _stub_fd(monkeypatch, tmp_path, "", returncode=1)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == ["@src/deep/widget_helper.py"]
    assert len(calls) == 1

    # (b) the run raises (timeout / OSError) → also falls back. The raise comes
    # out of the run_contained SEAM now, which is where a real fd timeout ends
    # up: TimeoutExpired is a SubprocessError, so the site's except catches it
    # and the tree fd spawned is already dead by then.
    boom_calls: list[list[str]] = []

    def _boom(argv: list[str], **_kwargs: Any) -> None:
        boom_calls.append(list(argv))
        raise subprocess.TimeoutExpired(cmd="fd", timeout=_FD_TIMEOUT)

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: "fd")
    monkeypatch.setattr(completion_mod, "run_contained", _boom)
    assert [c.text for c in _file_complete(tmp_path, "@widhel")] == ["@src/deep/widget_helper.py"]
    assert len(boom_calls) == 1


# === Issue #231 — what the two enumerators really offer ====================
#
# ``_enumerate_tree`` claimed fd and the walk "produce the SAME set of matchable
# paths on every machine". They never did. #231 closes the two divergences that
# cost one line each — the shared exclude predicate now runs on whichever arm
# answered, and ``--type l`` puts symlinks back into fd's answer — resolves the
# ``fd`` Aelix downloads for the ``find`` tool BEFORE the one on PATH, and
# documents the three that survive (git's ignore rules, undecodable names, and
# how the two arms spend the enumeration cap).

_FD_NAME = "fd.exe" if sys.platform == "win32" else "fd"
_OTHER_FD_NAME = "fd" if sys.platform == "win32" else "fd.exe"


def _stage_fd_binary(directory: Path, name: str = _FD_NAME) -> str:
    """Put an executable, never-run ``fd`` in ``directory``; return its path.

    The executable bit is load-bearing: ``shutil.which`` skips a non-executable
    file, so a staged ``fd`` without it resolves to ``None`` and the case would
    pass while pinning nothing. Nothing here ever SPAWNS the file — every case
    asserts on the resolved path only.

    Compare the resolved path with ``Path`` equality, never ``==`` on the
    strings: on win32 ``shutil.which`` builds its answer as ``cmd + ext`` for
    each PATHEXT entry and returns that, so it hands back the PATHEXT spelling
    (``fd.EXE``, uppercase on every stock Windows and in CPython's own
    ``_WIN_DEFAULT_PATHEXT``) while this helper returns the ``fd.exe`` it wrote.
    Same FILE, different string. ``WindowsPath`` comparison normcases and
    ``PosixPath`` does not, so one form is right on both legs.
    """

    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / name
    binary.write_text("")
    binary.chmod(0o755)
    return str(binary)


def _fd_lookup_seams(monkeypatch: Any, tmp_path: Path, bin_dir: Path, path_dir: Path) -> None:
    """Point the agent bin dir at ``bin_dir`` and PATH at ``path_dir`` alone.

    The bin-dir seam is ``cli.config.get_bin_dir`` — the DEFINING module, because
    ``_fd_binary`` imports the name function-locally and
    ``monkeypatch.setattr(completion_mod, "get_bin_dir", …)`` therefore raises
    ``AttributeError``. Reaching for ``HOME`` does not work either: this package's
    autouse ``_isolate_agent_dir`` sets ``AELIX_CODING_AGENT_DIR``, which
    ``get_agent_dir`` prefers over ``Path.home()``.

    ``PATH`` is emptied so a developer's real ``fd`` cannot answer, and the cwd is
    moved into an empty directory because on win32 ``shutil.which`` searches the
    process CWD BEFORE the directory handed to ``path=`` (unconditionally on 3.11;
    under ``NeedCurrentDirectoryForExePath`` on 3.12).
    """

    monkeypatch.setattr(cli_config, "get_bin_dir", lambda: str(bin_dir))
    monkeypatch.setenv("PATH", str(path_dir))
    empty_cwd = tmp_path / "cwd"
    empty_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(empty_cwd)


def test_fd_binary_prefers_the_managed_copy_over_path(monkeypatch: Any, tmp_path: Path) -> None:
    # #231: the copy ``ensure_tool`` downloads for the ``find`` tool wins over a
    # copy on PATH — the order ``get_tool_path`` already gives ``find``/``grep``.
    # Measured the other way round, with a fake ``fd`` prepended to PATH and the
    # managed copy present, the ``@`` menu and the ``find`` tool resolved to
    # DIFFERENT binaries.
    managed = _stage_fd_binary(tmp_path / "agent" / "bin")
    on_path = _stage_fd_binary(tmp_path / "elsewhere")
    assert managed != on_path
    _fd_lookup_seams(monkeypatch, tmp_path, tmp_path / "agent" / "bin", tmp_path / "elsewhere")
    assert Path(completion_mod._fd_binary() or "") == Path(managed)


def test_fd_binary_finds_the_managed_copy_when_path_has_none(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # The new lookup's success path, which is the live configuration on a machine
    # that has run ``find`` and never installed fd itself: nothing on PATH, a
    # managed copy present. Before #231 that machine got the os.walk fallback.
    managed = _stage_fd_binary(tmp_path / "agent" / "bin")
    _fd_lookup_seams(monkeypatch, tmp_path, tmp_path / "agent" / "bin", tmp_path / "empty")
    assert Path(completion_mod._fd_binary() or "") == Path(managed)

    # The OTHER spelling is not a hit, and that holds on every leg: PATHEXT is
    # win32-only so a POSIX lookup ignores ``fd.exe``, and neither 3.11 nor 3.12
    # matches an extension-less ``fd`` on win32.
    other = tmp_path / "agent" / "bin-other"
    _stage_fd_binary(other, _OTHER_FD_NAME)
    monkeypatch.setattr(cli_config, "get_bin_dir", lambda: str(other))
    assert completion_mod._fd_binary() is None


def test_fd_binary_falls_back_to_path_without_a_managed_copy(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # No managed copy — the bin dir does not even exist — so PATH answers. This is
    # every machine that has never run ``find``, and the arm ADR-0193 wrote the
    # dependency-free fallback for still sits behind it.
    on_path = _stage_fd_binary(tmp_path / "elsewhere")
    _fd_lookup_seams(monkeypatch, tmp_path, tmp_path / "agent" / "bin", tmp_path / "elsewhere")
    assert not (tmp_path / "agent" / "bin").exists()
    assert Path(completion_mod._fd_binary() or "") == Path(on_path)


def test_fd_binary_survives_a_broken_agent_dir(monkeypatch: Any, tmp_path: Path) -> None:
    # A faulty config must never reach the user as a traceback on a keystroke: the
    # bin-dir lookup fails soft, PATH still answers, and with nothing on PATH the
    # answer is ``None`` (→ the walk), not an exception out of the completer.
    on_path = _stage_fd_binary(tmp_path / "elsewhere")
    _fd_lookup_seams(monkeypatch, tmp_path, tmp_path / "agent" / "bin", tmp_path / "elsewhere")

    def _broken() -> str:
        raise RuntimeError("agent dir is unreadable")

    monkeypatch.setattr(cli_config, "get_bin_dir", _broken)
    assert Path(completion_mod._fd_binary() or "") == Path(on_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert completion_mod._fd_binary() is None


def test_fd_binary_agrees_with_the_find_tool(monkeypatch: Any, tmp_path: Path) -> None:
    # Drift pin: the ``@`` menu and the ``find`` tool resolve fd through the same
    # order, so they can never run different binaries. ``get_tool_path``
    # short-circuits on the managed copy, so this costs no subprocess — its PATH
    # arm is an unbounded ``subprocess.run([cmd, "--version"])`` and is never
    # reached here.
    bin_dir = tmp_path / "agent" / "bin"
    managed = _stage_fd_binary(bin_dir)
    _stage_fd_binary(tmp_path / "elsewhere")
    _fd_lookup_seams(monkeypatch, tmp_path, bin_dir, tmp_path / "elsewhere")
    # ``tools_manager`` reads the bin dir through its OWN ``_bin_dir``, which the
    # session-wide ``_no_real_tool_downloads`` fixture redirects to a per-session
    # temp dir; without pointing it at the same directory this case would compare
    # two different bin dirs and not the resolution ORDER it is here to pin
    # (``monkeypatch`` is function-scoped and the last write wins, so this
    # re-stub overrides the session fixture for this case and is undone with
    # it).
    monkeypatch.setattr(tools_manager, "_bin_dir", lambda: str(bin_dir))
    assert Path(get_tool_path("fd") or "") == Path(managed)
    assert Path(completion_mod._fd_binary() or "") == Path(get_tool_path("fd") or "")

    # The two shapes this pin could not see until they were measured, both of
    # which used to resolve to a DIFFERENT binary. (i) ``_download_tool``
    # ``shutil.move``s the binary to its final path and chmods it on the NEXT
    # line, so a kill between them leaves a managed copy that exists and is not
    # executable — and ``ensure_tool`` short-circuits on ``get_tool_path``, so
    # it is never repaired. ``shutil.which`` skipped it and PATH answered.
    # Windows ``os.access`` ignores X_OK, so there the chmod is a no-op and the
    # assertion holds for the other reason; the answer is the same on every leg.
    Path(managed).chmod(0o644)
    assert Path(completion_mod._fd_binary() or "") == Path(get_tool_path("fd") or "")
    Path(managed).chmod(0o755)
    # (ii) a directory named ``fd``: ``.exists()`` is True, ``_access_check``
    # is False. Same divergence, no platform arm needed.
    Path(managed).unlink()
    Path(managed).mkdir()
    assert Path(completion_mod._fd_binary() or "") == Path(get_tool_path("fd") or "")
    Path(managed).rmdir()
    _stage_fd_binary(Path(managed).parent)
    # (iii) win32 only in effect: ``shutil.which`` searched the process CWD
    # before the directory handed to ``path=``, so an ``fd.exe`` sitting in the
    # user's project directory outranked the managed copy. The managed arm no
    # longer goes through ``which`` at all. ``_fd_lookup_seams`` has already
    # chdir'd into an empty dir, so this stages one there.
    _stage_fd_binary(Path.cwd())
    assert Path(completion_mod._fd_binary() or "") == Path(get_tool_path("fd") or "")


def test_the_walk_applies_the_shared_exclude_list_to_files(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # #231: ``_has_excluded_component`` ran on fd's output ONLY, and the walk
    # pruned directory NAMES, so a FILE named like an excluded directory was
    # offered by the walk and hidden by fd — measured symmetric difference
    # ``['.git', 'build', 'src/dist']`` on a tree with no git repo and no fd in
    # sight. The common instance is the ``.git`` gitdir POINTER FILE that every
    # linked worktree and every submodule carries: this issue's own worktree
    # enumerated 1458 paths on the walk and 1457 on fd, the one path being it.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt-231\n")
    (tmp_path / "build").write_text("#!/bin/sh\necho build\n")
    (tmp_path / "src" / "dist").write_text("x")
    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    assert sorted(_enumerate_tree(tmp_path)) == ["src", "src/app.py"]


def test_an_empty_fd_answer_is_an_answer_not_a_fallback(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # ``if cands is None:`` must not become ``if not cands:``. An fd that
    # legitimately answers with zero paths — everything ignored, or an empty
    # tree — is answering; falling through to the walk there would offer the
    # whole ignored tree, which is the precise divergence #231 closes.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("")
    calls = _stub_fd(monkeypatch, tmp_path, "")
    assert _enumerate_tree(tmp_path) == []
    assert calls, "the fd seam was never entered"


def test_the_walk_offers_git_ignored_files_that_the_stubbed_fd_hides(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # The divergence #231 documents instead of closing, both arms in one case:
    # inside a git repo fd hides what ``.gitignore`` hides and the walk hides
    # nothing. Approximating it in Python would need fd's whole ``ignore`` crate —
    # root and nested ``.gitignore``, ``.git/info/exclude``, the global
    # ``core.excludesFile``, plus ``.ignore``/``.fdignore`` — so the contract is
    # pinned as a characterisation, not repaired.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / ".gitignore").write_text("ignored-by-git.md\n")
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "tracked.md").write_text("x")
    (tmp_path / "ignored-by-git.md").write_text("x")

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    walk = sorted(_enumerate_tree(tmp_path))
    assert walk == [
        ".gitignore",
        "README.md",
        "ignored-by-git.md",
        "src",
        "src/app.py",
        "tracked.md",
    ]

    # The fd side is the MEASURED output of the real binary on this tree, not a
    # live spawn. Two reasons, both measured: no CI leg has an fd at all, and a
    # real fd OUTSIDE a git repo applies no gitignore-family rule while still
    # honouring an ``.ignore``/``.fdignore`` — including one in a directory ABOVE
    # ``tmp_path`` — so a live binary would make this case measure the machine it
    # ran on. Recorded command, in the tree built above:
    #     $ git init -q && ~/.aelix/agent/bin/fd --type f --type d --type l \
    #           --hidden --color never --exclude … --max-results 20000
    #     .gitignore  README.md  src  src/app.py  tracked.md
    _stub_fd(monkeypatch, tmp_path, ".gitignore\nREADME.md\nsrc\nsrc/app.py\ntracked.md\n")
    fd_arm = sorted(_enumerate_tree(tmp_path))
    assert "ignored-by-git.md" in walk
    assert "ignored-by-git.md" not in fd_arm
    assert set(walk) - set(fd_arm) == {"ignored-by-git.md"}


def test_fd_lists_symlinks_like_the_walk(monkeypatch: Any, tmp_path: Path) -> None:
    # (a) the argv half — ``--type f --type d`` made a symlink neither, so on a
    #     tree with a dir link, a file link, a broken link and a self-loop the walk
    #     returned all four and fd returned NONE of them.
    calls = _stub_fd(monkeypatch, tmp_path, "real\n")
    _fd_enumerate("fd", tmp_path)
    assert [calls[0][i + 1] for i, tok in enumerate(calls[0]) if tok == "--type"] == ["f", "d", "l"]

    # (b) the agreement half, on a tree that carries symlinks AND excluded-name
    #     entries — the combination that needs BOTH #231 changes. Measured: with
    #     ``--type l`` alone the arms still differ (the walk also offers the ``.git``
    #     and ``build`` files); with the shared exclude predicate on the walk too
    #     they are exactly equal.
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "thing.py").write_text("x")
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n")
    (tmp_path / "build").write_text("#!/bin/sh\n")
    linked = ["broken", "dirlink", "filelink", "loop", "real", "real/thing.py"]
    expected = linked
    try:
        (tmp_path / "dirlink").symlink_to(tmp_path / "real", target_is_directory=True)
        (tmp_path / "filelink").symlink_to(tmp_path / "real" / "thing.py")
        (tmp_path / "broken").symlink_to(tmp_path / "nowhere")
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
        # A symlink whose NAME is excluded: pruned as a dirname by the walk and by
        # fd's --exclude, so it must be absent from both answers.
        (tmp_path / "dist").symlink_to(tmp_path / "real", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        # Unprivileged Windows refuses symlink creation. No skip — every case runs
        # on every leg — so the same tree WITHOUT links must still come out equal,
        # and the -q log carries why this leg's link arm was thin.
        for name in ("dirlink", "filelink", "broken", "loop", "dist"):
            leftover = tmp_path / name
            if leftover.is_symlink():
                leftover.unlink()
        expected = ["real", "real/thing.py"]
        warnings.warn(f"symlinks unavailable on this leg: {exc!r}", stacklevel=1)

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    assert sorted(_enumerate_tree(tmp_path)) == expected
    # Feed the stub the list BEFORE the shared post-filter, so this arm tests
    # the post-filter instead of echoing its own input. The real fd never emits
    # these two — its argv carries ``--exclude`` — so they are here purely to
    # make removing the post-filter from ``_enumerate_tree`` turn this red.
    # The two arms' EQUALITY rests on the design's M4b live-fd measurement,
    # not on this stub; what this half pins is the argv and the post-filter.
    _stub_fd(monkeypatch, tmp_path, "\n".join([*expected, "dist", ".git"]) + "\n")
    assert sorted(_enumerate_tree(tmp_path)) == expected


def test_the_walk_silently_truncates_at_the_cap(monkeypatch: Any, tmp_path: Path) -> None:
    # ``_TREE_ENUM_CAP`` bounds both arms, but only the walk spends that budget on
    # paths the VCS ignores, and it is a hard stop in walk order rather than a
    # sample: ``os.walk`` descends depth-first in ``os.scandir`` order, so once one
    # large subtree exhausts the budget every directory not yet reached is missing.
    # Nothing logs it and there is no latency signal either, because truncating is
    # FASTER. Measured at full size: an ignored 22 000-file ``target/`` beside
    # twelve real source files left the ``@`` menu two rows where fd gave twelve.
    # The cap is lowered here instead — a real 20 000-path fixture costs 0.88 s and
    # 22 000 inodes on every leg.
    heavy = tmp_path / "target"  # deliberately NOT in _EXCLUDE_DIRS
    heavy.mkdir()
    for i in range(25):
        (heavy / f"artifact{i:02d}.o").write_text("x")
    (tmp_path / "real_source.py").write_text("x")
    whole_tree = {"target", "real_source.py", *(f"target/artifact{i:02d}.o" for i in range(25))}

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    monkeypatch.setattr(completion_mod, "_TREE_ENUM_CAP", 8)
    truncated = _enumerate_tree(tmp_path)

    # Exactly the cap, and a strict subset — never WHICH paths survive: that is
    # ``os.scandir`` order, a filesystem property rather than a contract.
    assert len(truncated) == 8
    assert set(truncated) < whole_tree
    warnings.warn(
        f"the walk cap dropped {len(whole_tree) - len(truncated)} of {len(whole_tree)} paths"
        " with no signal to the user",
        stacklevel=1,
    )


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
