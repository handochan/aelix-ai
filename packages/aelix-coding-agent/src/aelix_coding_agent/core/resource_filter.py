"""The settings filter grammar for skills, prompts and extensions (#511, ADR-0279).

Pure functions, no I/O beyond ``os.path`` string work: what one ``skills`` /
``prompts`` / ``extensions`` list in a settings file says about one resource,
what the two files together say, and the minimal edit that makes a list say
something else.

pi reference (``coding-agent/src/core/package-manager.ts`` @ ``318d9add6``):

* ``matchesAnyPattern`` (``:715-740``) — a ``!glob`` is tried against the path
  relative to the file's base directory, the basename and the absolute path,
  and for a ``SKILL.md`` also against its parent directory in the same three
  forms. Globs are minimatch's: ``*`` stays inside one segment, ``**`` crosses
  segments, and neither matches a segment that starts with ``.``.
* ``matchesAnyExactPattern`` (``:747-766``) — ``+X`` / ``-X`` compare X (a
  leading ``./`` dropped, ``~`` expanded) for equality with the relative or the
  absolute path, and the parent directory's for a ``SKILL.md``. So ``-vue*``
  matches nothing.
* ``isEnabledByOverrides`` (``:772-797``) — within one list ``!`` is applied
  first, then ``+``, then ``-``.

Aelix divergences (ADR-0279 D1, D2; ADR-0235 makes them choices, not bugs):

* ``+X`` / ``-X`` also match an item's IDENTITY — a skill or prompt name, an
  extension's manifest, distribution or entry-point name — and a ``!glob`` is
  also tried against it. The panels write the identity form (``-pdf-tools``),
  so an entry survives the skill moving directory.
* A list yields an OPINION (on, off, or none) rather than a filtered path set;
  :func:`effective` takes the project file's opinion, else the global file's,
  else on. pi filters each scope's own resources only; here a global name or
  glob entry reaches a project item too.
* A plain entry (no prefix) is a SOURCE — a path that adds a directory to scan —
  and never an opinion about an item. :func:`edit` never removes one.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Scope = Literal["global", "project"]

_PREFIXES = ("!", "+", "-")
_GLOB_CHARS = re.compile(r"[*?\[{]")


class FilterTarget(Protocol):
    """What an entry can match: names, its file's path, and where it was found."""

    @property
    def identities(self) -> tuple[str, ...]: ...

    @property
    def path(self) -> str | None: ...

    @property
    def scope(self) -> str | None:
        """The discovery scope: ``cli``, ``project``, ``global`` or ``bundled`` (``None``: unknown)."""
        ...


@dataclass(frozen=True)
class Target:
    """A plain :class:`FilterTarget` for callers with no richer item type."""

    identities: tuple[str, ...]
    path: str | None = None
    scope: str | None = None


def reaches(target: FilterTarget, list_scope: Scope | None) -> bool:
    """Whether a list's PATH forms may name *target* (ADR-0279 D2).

    A name or a glob on a name reaches every item; a path reaches only the
    items of its own file's scope — the project file's paths project items, the
    global file's paths everything else. Resolving a path against the file's
    directory does not ensure that by itself (an absolute path or ``../`` goes
    anywhere), so it is enforced here. An unknown scope on either side is not
    restricted.
    """

    scope = target.scope
    if list_scope is None or scope is None:
        return True
    return (scope == "project") == (list_scope == "project")


@dataclass(frozen=True)
class ScopeList:
    """One settings file's list for one resource kind, with the directory its paths resolve against."""

    scope: Scope
    entries: tuple[str, ...]
    base_dir: str


def split_entry(entry: str) -> tuple[str, str]:
    """``("!", "foo*")`` for ``"!foo*"``; ``("", path)`` for a plain source entry."""

    if entry[:1] in _PREFIXES:
        return entry[0], entry[1:]
    return "", entry


def validate_entries(raw: object) -> tuple[list[str], list[str]]:
    """The usable entries of a settings value, and one message per problem.

    A value that is not a list, a non-string member, an empty string, a bare
    prefix with nothing after it and a ``!glob`` :func:`glob_problem` refuses
    are problems; the caller warns and ignores them (ADR-0279 D8). ``None``
    (the key is absent) is an empty list.
    """

    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"expected a list of strings, got {type(raw).__name__}"]
    kept: list[str] = []
    problems: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            problems.append(f"{entry!r} is not a string")
            continue
        stripped = entry.strip()
        if not stripped or (stripped[:1] in _PREFIXES and not stripped[1:].strip()):
            problems.append(f"{entry!r} names nothing")
            continue
        prefix, body = split_entry(stripped)
        if prefix == "!" and (problem := glob_problem(_posix(_expand(body)))) is not None:
            # Refused, not evaluated: a glob past the limits matches nothing (M1).
            problems.append(f"{entry!r} is too complex to match: {problem}")
            continue
        kept.append(stripped)
    return kept, problems


def _expand(pattern: str) -> str:
    """pi ``normalizePath``: ``~`` and ``~/…`` expand to the home directory."""

    if pattern == "~" or pattern.startswith(("~/", "~\\")):
        return os.path.expanduser(pattern)
    return pattern


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def _rel(base_dir: str, path: str) -> str | None:
    try:
        return _posix(os.path.relpath(path, base_dir))
    except ValueError:  # another drive on Windows: there is no relative form
        return None


@functools.lru_cache(maxsize=4096)
def _realpath(path: str) -> str:
    """``os.path.realpath``, once per path: every entry is matched against every
    item's forms, and resolving costs a ``lstat`` per path component.

    Only within one evaluation: :func:`forget_resolved_paths` empties it."""

    return os.path.realpath(path)


def forget_resolved_paths() -> None:
    """Empty :func:`_realpath`'s cache — at the start of every catalog load and
    snapshot, so a symlink re-pointed since the last one (then a ``/reload`` or
    a panel opened) is judged by its new target (#511 finish pass, MF1)."""

    _realpath.cache_clear()


def _forms(
    target: FilterTarget, base_dir: str, *, paths: bool = True
) -> tuple[list[str], list[str]]:
    """``(glob_forms, exact_forms)`` of *target*'s path, as pi derives them.

    With ``paths`` false only the NAME forms (the file's and, for a
    ``SKILL.md``, its directory's basename) are returned (:func:`reaches`).
    The resolved spelling of a symlinked path is a form too, so an entry may
    name either.
    """

    path = target.path
    if not path:
        return [], []
    name = os.path.basename(path)
    is_skill = name == "SKILL.md"
    names = [name] + ([os.path.basename(os.path.dirname(path))] if is_skill else [])
    if not paths:
        return [n for n in names if n], []
    spellings = [path]
    real = _realpath(path)
    if real != path:
        spellings.append(real)
    globs: list[str] = []
    exact: list[str] = []
    for spelling in spellings:
        rel = _rel(base_dir, spelling)
        globs += [f for f in (rel, _posix(spelling)) if f]
        exact += [f for f in (rel, _posix(spelling)) if f]
        if is_skill:
            parent = os.path.dirname(spelling)
            parent_rel = _rel(base_dir, parent)
            globs += [f for f in (parent_rel, _posix(parent)) if f]
            exact += [f for f in (parent_rel, _posix(parent)) if f]
    return globs + [n for n in names if n], exact


_RANGE = re.compile(r"^(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])(?:\.\.(-?\d+))?$")

#: The most alternatives one pattern's braces may expand to, and its longest
#: length. minimatch has no limit (``skill{1..10000000}`` takes it 192 ms); here
#: a pattern past either is REFUSED — warned about and evaluated as matching
#: nothing (:func:`glob_problem`) — rather than allowed to stall a launch.
BRACE_LIMIT = 256
#: Of those, how many may still be globs after expansion (a literal one is a
#: set lookup): each glob alternative is matched against every item's forms.
GLOB_ALTERNATIVE_LIMIT = 32
PATTERN_LIMIT = 512


class GlobTooComplex(ValueError):
    """A glob this matcher will not evaluate (#511 round 2, M1); the message says why."""


def _brace_range(body: str) -> list[str] | None:
    """``1..3``, ``01..10``, ``1..9..2``, ``a..e`` — brace-expansion's sequences (minimatch).

    The length is checked BEFORE anything is generated, so ``{1..10000000}``
    costs nothing but its refusal.
    """

    match = _RANGE.match(body)
    if match is None:
        return None
    lo, hi, step_text = match.groups()
    step = abs(int(step_text)) if step_text else 1
    step = step or 1
    numeric = lo.lstrip("-").isdigit() and hi.lstrip("-").isdigit()
    if numeric:
        a, b = int(lo), int(hi)
    elif lo.isdigit() or hi.isdigit():
        return None
    else:
        a, b = ord(lo), ord(hi)
    if abs(b - a) // step + 1 > BRACE_LIMIT:
        raise GlobTooComplex(f"{{{body}}} expands to more than {BRACE_LIMIT} alternatives")
    if numeric:

        def padded(text: str) -> bool:
            digits = text.lstrip("-")
            return len(digits) > 1 and digits.startswith("0")

        width = max(len(lo), len(hi)) if padded(lo) or padded(hi) else 0
        values = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
        out = []
        for v in values:
            sign = "-" if v < 0 else ""
            out.append(sign + str(abs(v)).zfill(max(0, width - len(sign))))
        return out
    codes = range(a, b + 1, step) if a <= b else range(a, b - 1, -step)
    return [chr(c) for c in codes]


def _expand_braces(pattern: str) -> list[str]:
    """minimatch's brace expansion: ``{a,b}`` (nested) and ``{1..3}`` / ``{a..c}`` sequences.

    ``{x}`` and ``{}`` stay literal, as in minimatch; a ``\\{`` is not a brace.
    Raises :class:`GlobTooComplex` past :data:`BRACE_LIMIT` alternatives — the
    count is checked as the expansion grows, so ``{a,b}`` repeated 20 times
    stops after about a thousand, not a million.
    """

    depth = 0
    start = -1
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                body = pattern[start + 1 : i]
                head, tail = pattern[:start], pattern[i + 1 :]
                parts = _brace_range(body)
                if parts is None:
                    split: list[str] = []
                    level = 0
                    last = 0
                    for j, c in enumerate(body):
                        if c == "{":
                            level += 1
                        elif c == "}":
                            level -= 1
                        elif c == "," and level == 0:
                            split.append(body[last:j])
                            last = j + 1
                    if split:
                        split.append(body[last:])
                        parts = split
                if parts is not None:
                    out: list[str] = []
                    for part in parts:
                        out.extend(_expand_braces(head + part + tail))
                        if len(out) > BRACE_LIMIT:
                            raise GlobTooComplex(
                                f"its braces expand to more than {BRACE_LIMIT} alternatives"
                            )
                    return out
                # ``{x}`` with no comma or sequence is literal; keep scanning after it.
        i += 1
    return [pattern]


_POSIX_CLASSES = {
    "alnum": "a-zA-Z0-9",
    "alpha": "a-zA-Z",
    "digit": "0-9",
    "lower": "a-z",
    "upper": "A-Z",
    "space": " \\t\\n\\r\\f\\v",
    "xdigit": "0-9A-Fa-f",
    "word": "\\w",
}


def _class(pattern: str, i: int) -> tuple[str, int] | None:
    """``[...]`` starting at *i*: ``(regex, index after it)``, or ``None`` when unclosed (literal)."""

    j = i + 1
    negate = j < len(pattern) and pattern[j] in "!^"
    if negate:
        j += 1
    body: list[str] = []
    first = True
    while j < len(pattern):
        c = pattern[j]
        if c == "]" and not first:
            inner = "".join(body)
            return (f"[^{inner}/]" if negate else f"[{inner}]"), j + 1
        first = False
        if c == "[" and pattern.startswith("[:", j):
            close = pattern.find(":]", j + 2)
            name = pattern[j + 2 : close] if close != -1 else ""
            if name in _POSIX_CLASSES:
                body.append(_POSIX_CLASSES[name])
                j = close + 2
                continue
        if c == "\\" and j + 1 < len(pattern):
            body.append(re.escape(pattern[j + 1]))
            j += 2
            continue
        body.append(c if c == "-" else re.escape(c))
        j += 1
    return None


def _close_paren(pattern: str, i: int) -> int:
    """The index of the ``)`` closing the ``(`` at *i*, or ``-1``."""

    depth = 0
    j = i
    while j < len(pattern):
        c = pattern[j]
        if c == "\\":
            j += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _split_alternatives(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    last = 0
    j = 0
    while j < len(body):
        c = body[j]
        if c == "\\":
            j += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            parts.append(body[last:j])
            last = j + 1
        j += 1
    parts.append(body[last:])
    return parts


# --- the segment matcher -------------------------------------------------------
#
# #511 round 2 (M1): round 1 handed a segment to Python's backtracking regex
# engine as minimatch's own translation, and nested quantifiers — ``*(*(?))x``,
# ``*(a|aa)b``, ``@(*|*)@(*|*)x`` — backtracked exponentially: one settings
# entry hung ``aelix`` at skill loading. The translation is kept, node for node
# (so the answers are round 1's, and minimatch's on the pinned corpus), but it
# is now EVALUATED by a table: for every node and every start position, the set
# of positions it can end at, as an int bitmask, filled from the segment's end
# backwards. Every match finishes: the work is polynomial — about nodes ×
# characters² Python steps, as each node at each start walks the end positions
# its groups reach — not exponential, though built input can still take
# seconds (ADR-0279 Limits). A segment with no extglob takes ``fnmatch``'s
# construction instead, in C.

_LIT, _ANY, _CLASS, _STAR, _GUARD, _GROUP, _NEG = range(7)

#: Nesting deeper than this is refused (:class:`GlobTooComplex`).
NESTING_LIMIT = 16

_Node = tuple[Any, ...]


def _build(pattern: str, at_start: bool, seqs: list[tuple[_Node, ...]], depth: int = 0) -> int:
    """Append *pattern*'s node sequence (children first) to *seqs*; return its index.

    The nodes are round 1's regex, piece for piece: ``dot: false`` puts a
    guard (``(?!\\.)``) before a wildcard that could take the segment's first
    character, and ``!(a|b)rest`` is "not ``(a|b)rest`` from here, then any run,
    then ``rest``" — minimatch's ``(?:(?!(?:a|b)rest$)[^/]*?)rest``.
    """

    if depth > NESTING_LIMIT:
        raise GlobTooComplex(f"its groups nest more than {NESTING_LIMIT} deep")
    out: list[_Node] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c in "@?*+!" and i + 1 < len(pattern) and pattern[i + 1] == "(":
            close = _close_paren(pattern, i + 1)
            if close != -1:
                alternatives = tuple(
                    _build(a, at_start, seqs, depth + 1)
                    for a in _split_alternatives(pattern[i + 2 : close])
                )
                if c == "!":
                    rest = seqs.pop(_build(pattern[close + 1 :], False, seqs, depth + 1))
                    out.append((_NEG, alternatives, at_start))
                    out.extend(rest)
                    break
                out.append((_GROUP, c, alternatives))
                if c in "@+":
                    at_start = False
                i = close + 1
                continue
        if c == "*":
            if at_start:
                out.append((_GUARD,))
            if not (out and out[-1][0] == _STAR):  # ``**`` inside a segment is ``*``
                out.append((_STAR,))
        elif c == "?":
            if at_start:
                out.append((_GUARD,))
            out.append((_ANY,))
        elif c == "[" and (parsed := _class(pattern, i)) is not None:
            if at_start:
                out.append((_GUARD,))
            out.append((_CLASS, re.compile(parsed[0])))
            i = parsed[1]
            at_start = False
            continue
        elif c == "\\" and i + 1 < len(pattern):
            out.append((_LIT, pattern[i + 1]))
            i += 2
            at_start = False
            continue
        else:
            out.append((_LIT, c))
        at_start = False
        i += 1
    seqs.append(tuple(out))
    return len(seqs) - 1


_LITERAL = "literal"


@functools.lru_cache(maxsize=4096)
def _segment_program(pattern: str) -> Any:
    """How to match one segment: :data:`_LITERAL` (string equality), a compiled
    regex (no extglob), or the node sequences for the table (root last).

    A ``[…]`` that Python cannot compile (``[z-a]``) makes the whole segment a
    literal, as in round 1. Raises :class:`GlobTooComplex`.
    """

    if not any(ch in pattern for ch in "*?[\\("):
        return _LITERAL
    seqs: list[tuple[_Node, ...]] = []
    try:
        _build(pattern, True, seqs)
    except re.error:
        return _LITERAL
    if len(seqs) > 1:
        return tuple(seqs)
    return _fast_regex(seqs[0])


def _fast_regex(seq: tuple[_Node, ...]) -> re.Pattern[str] | str:
    """A segment with no extglob, as Python 3.11's ``fnmatch.translate`` does it.

    Each interior ``*fixed`` becomes the atomic group ``(?>.*?fixed)``: every
    fixed piece is a run of one-character matchers, so its leftmost place is
    always a right one, and the group never backtracks. Only the last star
    backtracks, once per position.
    """

    pieces: list[str | None] = []  # ``None`` is a star
    for node in seq:
        kind = node[0]
        if kind == _STAR:
            pieces.append(None)
        elif kind == _LIT:
            pieces.append(re.escape(node[1]))
        elif kind == _ANY:
            pieces.append(".")
        elif kind == _CLASS:
            pieces.append(node[1].pattern)
        else:
            pieces.append("(?!\\.)")
    out: list[str] = []
    i = 0
    while i < len(pieces) and pieces[i] is not None:
        out.append(pieces[i] or "")
        i += 1
    while i < len(pieces):
        i += 1  # the star
        fixed: list[str] = []
        while i < len(pieces) and pieces[i] is not None:
            fixed.append(pieces[i] or "")
            i += 1
        joined = "".join(fixed)
        out.append(f"(?>.*?{joined})" if i < len(pieces) else ".*" + joined)
    try:
        return re.compile("(?s:" + "".join(out) + ")\\Z")
    except re.error:
        return _LITERAL


def _run(seqs: tuple[tuple[_Node, ...], ...], segment: str) -> bool:
    """The table: ``ends[s][k][pos]`` has bit ``e`` set when ``seqs[s][k:]`` can
    match ``segment[pos:e]``. Every node reads only cells at ``pos`` or later and
    child sequences come first, so one sweep from the end fills it."""

    n = len(segment)
    ends = [[[0] * (n + 2) for _ in range(len(seq) + 1)] for seq in seqs]
    # per node: a ``*()``/``+()``'s closure, or a ``!()``'s suffix union
    extra = [[[0] * (n + 2) for _ in range(len(seq))] for seq in seqs]

    def bits(mask: int) -> list[int]:
        found = []
        while mask:
            low = mask & -mask
            found.append(low.bit_length() - 1)
            mask ^= low
        return found

    for pos in range(n, -1, -1):
        ch = segment[pos] if pos < n else ""
        here = 1 << pos
        for s, seq in enumerate(seqs):
            table = ends[s]
            table[len(seq)][pos] = here
            for k in range(len(seq) - 1, -1, -1):
                node = seq[k]
                kind = node[0]
                after = table[k + 1]
                if kind == _LIT:
                    result = after[pos + 1] if ch == node[1] else 0
                elif kind == _ANY:
                    result = after[pos + 1] if ch else 0
                elif kind == _CLASS:
                    result = after[pos + 1] if ch and node[1].fullmatch(ch) else 0
                elif kind == _STAR:
                    result = after[pos] | table[k][pos + 1]
                elif kind == _GUARD:
                    result = 0 if ch == "." else after[pos]
                elif kind == _GROUP:
                    once = 0
                    for a in node[2]:
                        once |= ends[a][0][pos]
                    op = node[1]
                    if op == "@":
                        reach = once
                    elif op == "?":
                        reach = once | here
                    else:
                        closure = here
                        for e in bits(once & ~here):
                            closure |= extra[s][k][e]
                        extra[s][k][pos] = closure
                        if op == "*":
                            reach = closure
                        else:
                            reach = 0
                            for e in bits(once):
                                reach |= extra[s][k][e]
                    result = 0
                    for e in bits(reach):
                        result |= after[e]
                else:  # _NEG
                    suffix = after[pos] | extra[s][k][pos + 1]
                    extra[s][k][pos] = suffix
                    look = 0
                    for a in node[1]:
                        look |= ends[a][0][pos]
                    blocked = any((after[e] >> n) & 1 for e in bits(look))
                    result = 0 if blocked or (node[2] and ch == ".") else suffix
                table[k][pos] = result
    return bool((ends[-1][0][0] >> n) & 1)


def _segment_matches(pattern: str, segment: str) -> bool:
    program = _segment_program(pattern)
    if program is _LITERAL:
        return pattern == segment
    if isinstance(program, re.Pattern):
        return program.match(segment) is not None
    return _run(program, segment)


def _segments_match(pattern: Sequence[str], path: Sequence[str]) -> bool:
    """Segment lists, ``**`` included, memoized: polynomial for any number of ``**`` (M1).

    minimatch's rules: ``**`` takes zero or more segments, none starting with
    ``.``; a TRAILING ``**`` takes at least one (``a/**`` does not match ``a``,
    round 2 R1); adjacent ``**`` are one.
    """

    collapsed: list[str] = []
    for part in pattern:
        if part == "**" and collapsed and collapsed[-1] == "**":
            continue
        collapsed.append(part)
    pats = collapsed
    memo: dict[tuple[int, int], bool] = {}

    def go(i: int, j: int) -> bool:
        key = (i, j)
        if key in memo:
            return memo[key]
        if i == len(pats):
            result = j == len(path)
        elif pats[i] == "**":
            least = 1 if i == len(pats) - 1 else 0
            result = False
            for skip in range(len(path) - j + 1):
                if skip and path[j + skip - 1].startswith("."):
                    break
                if skip >= least and go(i + 1, j + skip):
                    result = True
                    break
        else:
            result = j < len(path) and _segment_matches(pats[i], path[j]) and go(i + 1, j + 1)
        memo[key] = result
        return result

    return go(0, 0)


@functools.lru_cache(maxsize=4096)
def _compile(
    pattern: str,
) -> tuple[bool, frozenset[str], tuple[tuple[str, ...], ...]]:
    """``(negate, literal alternatives, glob alternatives as segment lists)``.

    An alternative with no glob syntax at all is compared as a whole string —
    ``skill{1..200}`` is a set lookup, not two hundred matches. Raises
    :class:`GlobTooComplex`.
    """

    if len(pattern) > PATTERN_LIMIT:
        raise GlobTooComplex(f"it is longer than {PATTERN_LIMIT} characters")
    negate = False
    while pattern.startswith("!"):
        negate = not negate
        pattern = pattern[1:]
    literal: set[str] = set()
    globs: list[tuple[str, ...]] = []
    for alt in _expand_braces(pattern):
        parts = tuple(_dotdot(alt.split("/")))
        if any(part == "**" or _segment_program(part) is not _LITERAL for part in parts):
            globs.append(parts)
        else:
            literal.add("/".join(parts))
    if len(globs) > GLOB_ALTERNATIVE_LIMIT:
        raise GlobTooComplex(
            f"its braces expand to more than {GLOB_ALTERNATIVE_LIMIT} glob alternatives"
        )
    return negate, frozenset(literal), tuple(globs)


def _dotdot(parts: list[str]) -> list[str]:
    """minimatch's level-one optimisation: ``a/../b`` is ``b``."""

    out: list[str] = []
    for part in parts:
        if part == ".." and out and out[-1] not in ("..", ".", "**"):
            out.pop()
            continue
        out.append(part)
    return out or [""]


def glob_problem(pattern: str) -> str | None:
    """Why *pattern* is refused, or ``None`` — for the launch and panel warnings (M1)."""

    try:
        _compile(pattern)
    except GlobTooComplex as exc:
        return str(exc)
    return None


def glob_matches(value: str, pattern: str) -> bool:
    """minimatch(value, pattern) with pi's options (all defaults): see the module doc.

    Brace alternation and sequences, ``*`` ``?`` ``**`` ``[…]`` (``!``/``^``
    negation, POSIX classes), the extglobs ``@()`` ``?()`` ``*()`` ``+()``
    ``!()``, ``\\`` escapes, ``dot: false``, a trailing ``**`` that needs a
    segment, a leading ``#`` (a comment: matches nothing) and a second leading
    ``!`` (minimatch's own negation). ``tests/core/test_resource_filter_511.py``
    pins the answers minimatch 10.2.6 (pi's lockfile) gives on a differential
    corpus, and times the adversarial cases. A pattern :func:`glob_problem`
    refuses matches nothing.
    """

    if pattern.startswith("#"):
        return False
    try:
        negate, literal, alternatives = _compile(pattern)
    except GlobTooComplex:
        return False
    matched = value in literal
    if not matched and alternatives:
        parts = value.split("/")
        matched = any(_segments_match(alt, parts) for alt in alternatives)
    return matched != negate


def matches_glob(
    target: FilterTarget, pattern: str, base_dir: str, *, list_scope: Scope | None = None
) -> bool:
    """Whether ``!pattern`` excludes *target* (pi ``matchesAnyPattern`` plus identities)."""

    normalized = _posix(_expand(pattern))
    globs, _exact = _forms(target, base_dir, paths=reaches(target, list_scope))
    return any(glob_matches(form, normalized) for form in (*globs, *target.identities))


def matches_exact(
    target: FilterTarget, body: str, base_dir: str, *, list_scope: Scope | None = None
) -> bool:
    """Whether ``+body`` / ``-body`` names *target* (pi ``matchesAnyExactPattern`` plus identities)."""

    if body in target.identities:
        return True
    stripped = body[2:] if body.startswith(("./", ".\\")) else body
    normalized = _posix(_expand(stripped))
    _globs, exact = _forms(target, base_dir, paths=reaches(target, list_scope))
    return normalized in exact


def evaluate(
    target: FilterTarget,
    entries: Sequence[str],
    base_dir: str,
    *,
    list_scope: Scope | None = None,
) -> bool | None:
    """One list's opinion of *target*: ``False`` off, ``True`` on, ``None`` no opinion.

    ``!`` entries first, then ``+``, then ``-`` — the last kind that matches
    decides, so ``-x`` beats ``+x`` beats ``!x*`` regardless of their order in
    the file (pi ``isEnabledByOverrides``).
    """

    opinion: bool | None = None
    split = [split_entry(e) for e in entries]
    scope = list_scope
    if any(p == "!" and matches_glob(target, b, base_dir, list_scope=scope) for p, b in split):
        opinion = False
    if any(p == "+" and matches_exact(target, b, base_dir, list_scope=scope) for p, b in split):
        opinion = True
    if any(p == "-" and matches_exact(target, b, base_dir, list_scope=scope) for p, b in split):
        opinion = False
    return opinion


def effective(
    target: FilterTarget,
    global_list: ScopeList | None,
    project_list: ScopeList | None,
) -> tuple[bool, Scope | None]:
    """The saved value of *target* and the file that decided it (``None``: the default, on)."""

    for scope_list in (project_list, global_list):
        if scope_list is None:
            continue
        opinion = evaluate(
            target, scope_list.entries, scope_list.base_dir, list_scope=scope_list.scope
        )
        if opinion is not None:
            return opinion, scope_list.scope
    return True, None


def targets_exactly(
    entry: str, target: FilterTarget, base_dir: str, *, list_scope: Scope | None = None
) -> bool:
    """Whether a ``!`` / ``+`` / ``-`` *entry* names *target* exactly (a glob that also matches it does not)."""

    prefix, body = split_entry(entry)
    if not prefix:
        return False
    if prefix == "!" and _GLOB_CHARS.search(body):
        return False
    return matches_exact(target, body, base_dir, list_scope=list_scope)


def edit(
    entries: Sequence[str],
    target: FilterTarget,
    want: bool,
    inherited: bool,
    base_dir: str,
    *,
    list_scope: Scope | None = None,
) -> list[str]:
    """The smallest change to *entries* after which this list makes *target* ``want``.

    1. Drop every ``!`` / ``+`` / ``-`` entry that names *target* exactly.
    2. Evaluate what is left; no opinion means *inherited* (the global file's
       value for a project edit, on for a global one).
    3. Append ``-name`` / ``+name`` (the first identity) only if that still
       differs from ``want``.

    So toggling twice returns the list without the item's exact entries, a
    hand-written glob survives (an exact ``+name`` is appended to beat it), and
    a plain source entry is never touched.
    """

    kept = [e for e in entries if not targets_exactly(e, target, base_dir, list_scope=list_scope)]
    opinion = evaluate(target, kept, base_dir, list_scope=list_scope)
    value = inherited if opinion is None else opinion
    if value != want and target.identities:
        kept.append(("+" if want else "-") + target.identities[0])
    return kept


def matches_any(
    entry: str,
    targets: Sequence[FilterTarget],
    base_dir: str,
    *,
    list_scope: Scope | None = None,
) -> bool:
    """Whether a ``!`` / ``+`` / ``-`` entry reaches at least one of *targets* (unknown-entry warnings)."""

    prefix, body = split_entry(entry)
    if prefix == "!":
        return any(matches_glob(t, body, base_dir, list_scope=list_scope) for t in targets)
    return any(matches_exact(t, body, base_dir, list_scope=list_scope) for t in targets)


def source_path(entry: str, base_dir: str) -> str:
    """A plain entry resolved as pi ``resolvePath`` does: ``~`` expanded, relative to *base_dir*."""

    expanded = _expand(entry)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(base_dir, expanded))


def source_entries(entries: Sequence[str]) -> list[str]:
    """The plain (source) entries of a list, in order."""

    return [e for e in entries if not split_entry(e)[0]]


__all__ = [
    "FilterTarget",
    "reaches",
    "Scope",
    "ScopeList",
    "Target",
    "edit",
    "effective",
    "evaluate",
    "forget_resolved_paths",
    "glob_matches",
    "glob_problem",
    "matches_any",
    "matches_exact",
    "matches_glob",
    "source_entries",
    "source_path",
    "split_entry",
    "targets_exactly",
    "validate_entries",
]
