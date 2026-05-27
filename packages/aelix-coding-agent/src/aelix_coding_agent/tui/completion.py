"""Sprint 6h₁₀d (§A) — command-route live autocomplete completer.

The 6h₁₀c descriptor renderer stores ``command-route`` payloads in
:attr:`DescriptorRenderer.command_routes` but never surfaced them. This completer
reads that store **live by reference** (a ``get_routes`` callable) so descriptors
appended/removed during the session immediately change the offered completions.

Pure + unit-testable: no Application needed — feed a
:class:`~prompt_toolkit.document.Document` and a fake routes dict.

Sprint 6h₁₄a (ADR-0121) adds :class:`FileMentionCompleter` — an ``@file`` path
completer (pi ``@`` mention parity). Per pi at SHA 734e08e, ``@`` in the
interactive editor is purely an autocomplete affordance that inserts a path
string; the file CONTENT is NOT expanded into the prompt (only the CLI ``@file``
ARGUMENT path inlines ``<file>…</file>``). So this completer just inserts the
path text and the model reads the file with its own tools. (pi uses ``fd`` for
fuzzy whole-tree search; Aelix does dependency-free directory-listing prefix
completion, one path component at a time.)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from aelix_coding_agent.tui.commands import BuiltinCommand


def _last_token(text_before_cursor: str) -> str:
    """The trailing whitespace-delimited token (``""`` when the text ends in
    whitespace or is empty). Mirrors pi's ``findLastDelimiter`` token split."""

    if not text_before_cursor or text_before_cursor[-1].isspace():
        return ""
    parts = text_before_cursor.rsplit(maxsplit=1)
    return parts[-1] if parts else ""


def wants_completion(text_before_cursor: str) -> bool:
    """True when the cursor is in a completable context: a ``/`` slash command
    (line start) or an ``@file`` mention token. Drives ``complete_while_typing``
    so ordinary prose types uninterrupted."""

    if text_before_cursor.startswith("/"):
        return True
    return _last_token(text_before_cursor).startswith("@")


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


class FileMentionCompleter(Completer):
    """Offer filesystem path completions for an ``@file`` mention token.

    Triggered only when the trailing token starts with ``@`` (anywhere in the
    line — pi completes the token under the cursor, not just at line start). The
    inserted value is the path text (``@src/foo.py``); directories get a trailing
    ``/`` so the user can drill in. NO file content is read or expanded — that
    matches pi interactive-mode behavior (the model reads the file via its tools).

    :param get_cwd: callable returning the session working directory (read live so
        a ``/resume`` cwd change is reflected). Plain ``str`` cwd also accepted.
    :param max_results: cap on offered completions (avoids a huge menu in big dirs).
    """

    def __init__(
        self, get_cwd: Callable[[], str] | str, max_results: int = 30
    ) -> None:
        self._get_cwd = get_cwd if callable(get_cwd) else (lambda: get_cwd)
        self._max_results = max_results

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        token = _last_token(document.text_before_cursor)
        if not token.startswith("@"):
            return
        prefix = token[1:]  # the path text after '@'

        # Split the path prefix into an already-typed directory part and the
        # partial leaf being completed (one component at a time, like a shell).
        if "/" in prefix:
            dir_part, _, partial = prefix.rpartition("/")
        else:
            dir_part, partial = "", prefix

        try:
            base = Path(self._get_cwd())
        except Exception:  # noqa: BLE001 — a faulty cwd source must not break input
            return
        search_dir = base / dir_part if dir_part else base

        try:
            entries = sorted(search_dir.iterdir(), key=lambda p: p.name)
        except OSError:  # missing dir / permission — no completions
            return

        partial_lower = partial.lower()
        count = 0
        for entry in entries:
            name = entry.name
            # Hide dotfiles unless the partial explicitly starts with a dot.
            if not partial.startswith(".") and name.startswith("."):
                continue
            if not name.lower().startswith(partial_lower):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            rel = f"{dir_part}/{name}" if dir_part else name
            suffix = "/" if is_dir else ""
            yield Completion(
                "@" + rel + suffix,
                start_position=-len(token),
                display=name + suffix,
            )
            count += 1
            if count >= self._max_results:
                return


__all__ = ["DescriptorCommandCompleter", "FileMentionCompleter", "wants_completion"]
