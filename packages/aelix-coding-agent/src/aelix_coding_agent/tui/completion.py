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
path text and the model reads the file with its own tools.

Issue #39 upgrades that completer to pi's whole-tree behaviour:

* **Fuzzy whole-tree search.** A non-trivial ``@`` prefix is matched as a
  case-insensitive *subsequence* against every relative path in the tree
  (``@comp`` → ``src/…/completion.py``), ranked, and capped. The tree is
  enumerated with the ``fd`` binary when it is present (fast + ``.gitignore``
  aware) and falls back to a bounded, dependency-free ``os.walk`` (with a curated
  exclude list) otherwise — so *every* user gets fuzzy matching and ``fd`` is only
  ever a speed upgrade, never a hard dependency (keeps Aelix's air-gap posture).
  The enumeration is TTL-cached so keystroke-frequency completion stays snappy.
  No user input is ever passed to the subprocess (we enumerate all, filter in
  Python) — so there is no regex/shell-injection surface.
* **Quoted-path mentions.** ``@"path with spaces"`` is parsed as a single mention
  (whitespace inside the quotes does not terminate the token), and any completion
  whose path contains a space is inserted quoted.

An empty prefix and a trailing-slash prefix (``@src/``) still use the cheap
one-level directory listing (the fast drill-in), so those common cases skip the
whole-tree walk entirely.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from aelix_coding_agent.tui.commands import BuiltinCommand


# Issue #39 — whole-tree enumeration tuning.
_TREE_CACHE_TTL = 2.0  # seconds a cached enumeration is reused (keystroke-frequency)
_TREE_ENUM_CAP = 20000  # max paths enumerated (bounds a huge monorepo walk)
_FD_TIMEOUT = 2.0  # seconds before the fd subprocess is abandoned → walk fallback
# Directories the dependency-free ``os.walk`` fallback never descends into (``fd``
# gets this for free from ``.gitignore`` + its ``--exclude .git``). Heuristic, not
# exhaustive — just the usual heavy/uninteresting trees.
_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".next",
        ".cache",
    }
)


class _Mention(NamedTuple):
    """A parsed ``@`` mention under the cursor.

    :param prefix: the path text after ``@`` (unquoted; may contain spaces when
        the mention was opened with ``@"``).
    :param quoted: True when the mention was opened with ``@"`` (an open quote).
    :param replace_len: characters from the ``@`` to the cursor — the negative of
        this is the ``Completion.start_position`` (so the whole mention, quote and
        all, is replaced by the inserted value).
    """

    prefix: str
    quoted: bool
    replace_len: int


def _last_token(text_before_cursor: str) -> str:
    """The trailing whitespace-delimited token (``""`` when the text ends in
    whitespace or is empty). Mirrors pi's ``findLastDelimiter`` token split."""

    if not text_before_cursor or text_before_cursor[-1].isspace():
        return ""
    parts = text_before_cursor.rsplit(maxsplit=1)
    return parts[-1] if parts else ""


def _extract_mention(text_before_cursor: str) -> _Mention | None:
    """Return the ``@`` mention active at the cursor, or ``None`` when there is none.

    Scans left-to-right, quote-aware, so an ``@`` typed *inside* an open ``@"…``
    quoted mention is treated as a literal path character (not a fresh mention).
    A mention begins at an ``@`` that is at the start of the line or preceded by
    whitespace (so ``foo@bar`` — an email-like token — is NOT a mention). A quoted
    mention (``@"…``) may contain spaces and stays "open" (active) until its
    closing quote is typed; an unquoted mention ends at the first whitespace. The
    mention returned is the one that reaches the cursor (the end of the text).
    """

    text = text_before_cursor
    n = len(text)
    i = 0
    start = -1
    quoted = False
    while i < n:
        if text[i] == "@" and (i == 0 or text[i - 1].isspace()):
            if i + 1 < n and text[i + 1] == '"':
                close = text.find('"', i + 2)
                if close == -1:  # unclosed quote → active mention through the cursor
                    start, quoted = i, True
                    break
                i = close + 1  # a closed quoted mention — skip past it and keep scanning
                continue
            j = i + 1
            while j < n and not text[j].isspace():
                j += 1
            if j == n:  # unquoted mention runs to the cursor
                start, quoted = i, False
                break
            i = j  # this unquoted mention already ended before the cursor
            continue
        i += 1
    if start == -1:
        return None
    prefix = text[start + 2 :] if quoted else text[start + 1 :]
    return _Mention(prefix=prefix, quoted=quoted, replace_len=n - start)


def wants_completion(text_before_cursor: str) -> bool:
    """True when the cursor is in a completable context: a ``/`` slash command
    (line start) or an ``@file`` mention token (including a quoted ``@"…`` token
    that contains spaces). Drives ``complete_while_typing`` so ordinary prose
    types uninterrupted."""

    if text_before_cursor.startswith("/"):
        return True
    return _extract_mention(text_before_cursor) is not None


def _fuzzy_score(pattern: str, candidate: str) -> int | None:
    """Case-insensitive subsequence score of ``pattern`` against ``candidate``.

    Returns ``None`` when ``pattern`` is not a subsequence of ``candidate``.
    Higher is better: contiguous runs, matches at a path/word boundary, and
    matches inside the basename are rewarded; gaps and long candidates penalized.
    A basename- or full-prefix hit gets a large bonus so the literal match a user
    is most likely aiming for ranks first.
    """

    if not pattern:
        return 0
    pat = pattern.lower()
    cand = candidate.lower()
    base_start = cand.rfind("/") + 1  # 0 when there is no slash
    score = 0
    ci = 0
    prev = -1
    for pc in pat:
        idx = cand.find(pc, ci)
        if idx == -1:
            return None
        if idx == prev + 1:
            score += 8  # contiguous with the previous match
        if idx == base_start or (idx > 0 and cand[idx - 1] in "/._- "):
            score += 6  # at a word / path boundary
        if idx >= base_start:
            score += 2  # inside the basename
        score -= idx - ci  # gap penalty
        prev = idx
        ci = idx + 1
    score -= len(cand) // 40  # mild shorter-is-better bias
    if cand.startswith(pat):
        score += 20
    if cand[base_start:].startswith(pat):
        score += 15
    return score


def _posix(path: str) -> str:
    """Normalize OS path separators to ``/`` (no-op on POSIX)."""

    return path.replace(os.sep, "/")


def _fd_binary() -> str | None:
    """The ``fd`` executable on PATH (``fd`` or Debian's ``fdfind``), or ``None``.

    Not memoized — it is only consulted once per (TTL-cached) enumeration, so the
    PATH scan cost is negligible and a test that installs/removes ``fd`` on PATH
    is reflected immediately.
    """

    return shutil.which("fd") or shutil.which("fdfind")


def _has_excluded_component(rel: str) -> bool:
    """True when any path component of ``rel`` is in :data:`_EXCLUDE_DIRS`.

    The single source of truth for the exclude contract: applied to BOTH the
    ``fd`` and ``os.walk`` outputs so the two enumerators agree regardless of
    ``.gitignore`` presence (Issue #39 review — fd otherwise only honoured
    ``.gitignore`` and leaked node_modules/.venv in a gitignore-less tree)."""

    return any(part in _EXCLUDE_DIRS for part in rel.split("/"))


def _fd_enumerate(fd_bin: str, base: Path) -> list[str] | None:
    """Enumerate the tree under ``base`` with ``fd`` (``.gitignore`` aware).

    Returns relative POSIX paths (dirs and files, no trailing slash), capped at
    :data:`_TREE_ENUM_CAP`, or ``None`` on any failure so the caller falls back to
    the ``os.walk`` enumerator. No user input is passed to the subprocess. The
    ``_EXCLUDE_DIRS`` are passed to ``fd`` (so it never descends them) AND
    ``--max-results`` bounds fd's own output so a pathological non-gitignored tree
    can't materialize a huge stdout within the timeout window.
    """

    argv = [fd_bin, "--type", "f", "--type", "d", "--hidden", "--color", "never"]
    for excluded in _EXCLUDE_DIRS:
        argv += ["--exclude", excluded]
    argv += ["--max-results", str(_TREE_ENUM_CAP)]
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
            argv,
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=_FD_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out: list[str] = []
    for raw in proc.stdout.splitlines():
        line = raw.rstrip("/")
        if line.startswith("./"):
            line = line[2:]
        if line:
            out.append(line)
        if len(out) >= _TREE_ENUM_CAP:
            break
    return out


def _walk_enumerate(base: Path) -> list[str]:
    """Dependency-free tree enumeration via ``os.walk``, pruning
    :data:`_EXCLUDE_DIRS` and capped at :data:`_TREE_ENUM_CAP`."""

    out: list[str] = []
    base_str = str(base)
    for root, dirs, files in os.walk(base_str):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, base_str)
        prefix = "" if rel_root == "." else _posix(rel_root) + "/"
        for name in dirs:
            out.append(prefix + name)
            if len(out) >= _TREE_ENUM_CAP:
                return out
        for name in files:
            out.append(prefix + name)
            if len(out) >= _TREE_ENUM_CAP:
                return out
    return out


def _enumerate_tree(base: Path) -> list[str]:
    """All relative POSIX paths under ``base`` (``fd`` when present, else walk).

    The ``_EXCLUDE_DIRS`` post-filter is applied to the fd output too (in addition
    to fd's own ``--exclude``) so the fd and walk enumerators produce the SAME set
    of matchable paths on every machine — fd is only ever a speed upgrade, never a
    change in WHICH files complete (Issue #39 review)."""

    fd_bin = _fd_binary()
    if fd_bin is not None:
        cands = _fd_enumerate(fd_bin, base)
        if cands is not None:
            return [p for p in cands if not _has_excluded_component(p)]
    return _walk_enumerate(base)


def _completion_value(rel: str, is_dir: bool, quoted: bool) -> str:
    """The ``@``-prefixed text a completion inserts.

    Directories carry a trailing ``/`` (so the user can drill in). A path with a
    space — or a mention that was opened with ``@"`` — is inserted quoted; the
    quote stays OPEN for a directory (drilling continues inside the quotes) and is
    CLOSED for a file.
    """

    suffix = "/" if is_dir else ""
    body = rel + suffix
    if not (quoted or " " in rel):
        return "@" + body
    if is_dir:
        return '@"' + body
    return '@"' + body + '"'


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
        get_ext_commands: Callable[[], list[tuple[str, str]]] | None = None,
    ) -> None:
        self._get_routes = get_routes
        self._builtins = builtins or []
        # Issue #9: live ``(invocation_name, description)`` source for
        # extension-registered commands (read every keystroke so a /reload or
        # session swap reflects immediately). Built-ins + descriptor routes win
        # on a name collision (they are yielded first and dedup via ``seen``).
        self._get_ext_commands = get_ext_commands

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

        # Issue #9: extension-registered commands, last (built-ins + descriptor
        # routes win on a name collision via ``seen``).
        if self._get_ext_commands is not None:
            try:
                ext_commands = self._get_ext_commands()
            except Exception:  # noqa: BLE001 — a faulty source must not break input
                ext_commands = []
            for name, description in ext_commands:
                if not name or not name.startswith(typed) or name in seen:
                    continue
                seen.add(name)
                yield Completion(
                    "/" + name,
                    start_position=-len(typed_with_slash),
                    display=name,
                    display_meta=description or "",
                )


class FileMentionCompleter(Completer):
    """Offer filesystem path completions for an ``@file`` mention token.

    Triggered when an ``@`` mention is under the cursor (anywhere in the line — pi
    completes the token under the cursor, not just at line start), including a
    quoted ``@"path with spaces"`` token. The inserted value is the path text; a
    directory gets a trailing ``/`` so the user can drill in, and a path with a
    space is inserted quoted. NO file content is read or expanded — that matches
    pi interactive-mode behavior (the model reads the file via its own tools).

    Completion modes (Issue #39):

    * empty prefix or trailing-slash prefix (``@`` / ``@src/``) → a cheap
      one-level directory listing (fast drill-in, no whole-tree walk);
    * any other prefix → fuzzy whole-tree subsequence search (``fd`` when present,
      else a bounded ``os.walk``), ranked and capped, falling back to a
      directory-scoped prefix listing if nothing fuzzy-matches.

    :param get_cwd: callable returning the session working directory (read live so
        a ``/resume`` cwd change is reflected). Plain ``str`` cwd also accepted.
    :param max_results: cap on offered completions (avoids a huge menu in big dirs).
    """

    def __init__(
        self, get_cwd: Callable[[], str] | str, max_results: int = 30
    ) -> None:
        self._get_cwd = get_cwd if callable(get_cwd) else (lambda: get_cwd)
        self._max_results = max_results
        # TTL cache of the enumerated tree, keyed by base cwd → (monotonic_ts,
        # paths). Keeps keystroke-frequency fuzzy completion from re-walking the
        # whole tree on every keypress.
        self._tree_cache: dict[str, tuple[float, list[str]]] = {}

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        mention = _extract_mention(document.text_before_cursor)
        if mention is None:
            return
        try:
            base = Path(self._get_cwd())
        except Exception:  # noqa: BLE001 — a faulty cwd source must not break input
            return
        prefix = mention.prefix

        # Cheap path: an empty or trailing-slash prefix is a directory drill-in.
        if prefix == "" or prefix.endswith("/"):
            yield from self._list_directory(base, prefix, mention)
            return

        # Fuzzy whole-tree search.
        results = self._fuzzy_search(base, prefix)
        if results:
            for rel, is_dir in results:
                yield Completion(
                    _completion_value(rel, is_dir, mention.quoted),
                    start_position=-mention.replace_len,
                    display=rel + ("/" if is_dir else ""),
                )
            return

        # No fuzzy hit — fall back to a directory-scoped prefix listing so the
        # user still gets completions in edge cases (e.g. an odd partial leaf).
        yield from self._list_directory(base, prefix, mention)

    def _list_directory(
        self, base: Path, prefix: str, mention: _Mention
    ) -> Iterable[Completion]:
        """One-level listing of the directory named by ``prefix`` (the part before
        the last ``/``), filtered by the partial leaf after it — the fast drill-in
        used for empty / trailing-slash prefixes and as the no-fuzzy-hit fallback."""

        if "/" in prefix:
            dir_part, _, partial = prefix.rpartition("/")
        else:
            dir_part, partial = "", prefix
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
            yield Completion(
                _completion_value(rel, is_dir, mention.quoted),
                start_position=-mention.replace_len,
                display=name + ("/" if is_dir else ""),
            )
            count += 1
            if count >= self._max_results:
                return

    def _fuzzy_search(self, base: Path, prefix: str) -> list[tuple[str, bool]]:
        """Rank the enumerated tree by :func:`_fuzzy_score` against ``prefix`` and
        return the top ``max_results`` as ``(rel_path, is_dir)`` pairs."""

        tree = self._get_tree(base)
        scored: list[tuple[int, str]] = []
        for rel in tree:
            s = _fuzzy_score(prefix, rel)
            if s is not None:
                scored.append((s, rel))
        # Highest score first; ties broken by shorter path then lexicographic.
        scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
        out: list[tuple[str, bool]] = []
        for _, rel in scored[: self._max_results]:
            try:
                is_dir = (base / rel).is_dir()
            except OSError:
                is_dir = False
            out.append((rel, is_dir))
        return out

    def _get_tree(self, base: Path) -> list[str]:
        """The enumerated tree under ``base``, TTL-cached so a burst of keystrokes
        shares a single walk."""

        key = str(base)
        now = time.monotonic()
        cached = self._tree_cache.get(key)
        if cached is not None and now - cached[0] < _TREE_CACHE_TTL:
            return cached[1]
        tree = _enumerate_tree(base)
        # Bound the cache (a session rarely completes against >1-2 cwds).
        if len(self._tree_cache) > 4:
            self._tree_cache.clear()
        self._tree_cache[key] = (now, tree)
        return tree


__all__ = ["DescriptorCommandCompleter", "FileMentionCompleter", "wants_completion"]
