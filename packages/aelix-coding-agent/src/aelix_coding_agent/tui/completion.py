"""Sprint 6h₁₀d (§A) — command-route live autocomplete completer.

The 6h₁₀c descriptor renderer stores ``command-route`` payloads in
:attr:`DescriptorRenderer.command_routes` but never surfaced them. This completer
reads that store **live by reference** (a ``get_routes`` callable) so descriptors
appended/removed during the session immediately change the offered completions.

Pure + unit-testable: no Application needed — feed a
:class:`~prompt_toolkit.document.Document` and a fake routes dict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from aelix_coding_agent.tui.commands import BuiltinCommand


class DescriptorCommandCompleter(Completer):
    """Offer ``/<command>`` completions: built-ins ∪ live descriptor routes.

    Sprint 6h₁₂a (ADR-0110): the palette now unions first-party built-in commands
    with descriptor command-routes, deduped by command name (built-in wins) so a
    descriptor cannot shadow ``/help`` etc. Built-ins are listed first.

    :param get_routes: a callable returning the live route store (mapping of
        ``ns:id`` → command-route payload). Read on every keystroke so the source
        dict can be mutated in place and have new completions appear immediately.
    :param builtins: the first-party command registry (static for the session).
    """

    def __init__(
        self,
        get_routes: Callable[[], Mapping[str, Any]],
        builtins: list[BuiltinCommand] | None = None,
    ) -> None:
        self._get_routes = get_routes
        self._builtins = builtins or []

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # The typed command word, slash-prefixed (e.g. "/de"). Slash commands are
        # a single token, so the whole prefix up to the cursor is the word.
        typed_with_slash = text
        typed = typed_with_slash[1:]

        seen: set[str] = set()

        # Built-ins first; they win on a name clash with a descriptor route.
        for command in self._builtins:
            name = command.name
            if not name or not name.startswith(typed) or name in seen:
                continue
            seen.add(name)
            yield Completion(
                "/" + name,
                start_position=-len(typed_with_slash),
                display=name,
                display_meta=command.description,
            )

        try:
            routes = self._get_routes()
        except Exception:  # noqa: BLE001 — a faulty source must not break input
            return

        for payload in routes.values():
            command = getattr(payload, "command", None)
            # Skip empty/non-str commands and dedup same-command routes (a
            # cross-namespace re-point can leave two keys with one command).
            if not isinstance(command, str) or not command or not command.startswith(typed):
                continue
            if command in seen:
                continue
            seen.add(command)
            description = getattr(payload, "description", "") or ""
            keybind = getattr(payload, "keybind", None)
            meta = f"{description} [{keybind}]" if keybind else description
            yield Completion(
                "/" + command,
                start_position=-len(typed_with_slash),
                display=command,
                display_meta=meta,
            )


__all__ = ["DescriptorCommandCompleter"]
