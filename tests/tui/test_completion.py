"""Sprint 6h₁₀d (§D) — DescriptorCommandCompleter unit tests.

Pure: no Application/TTY. A fake routes dict + a ``Document`` drive the completer
directly. The store is read by reference, so mutating it mid-session must change
the offered completions (the "live source" contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aelix_coding_agent.tui.commands import BuiltinCommand
from aelix_coding_agent.tui.completion import DescriptorCommandCompleter
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
