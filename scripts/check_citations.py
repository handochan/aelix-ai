#!/usr/bin/env python3
"""Keep ``<file>.py:NNN`` citations pointing at what they were written to point at.

WHY THIS EXISTS. This repository explains itself by citing its own source with
line numbers — 742 live citations at the time this was written, in docstrings,
test module headers, and the guides that ship inside the wheel. Line numbers
rot the moment anything above them moves, silently and in bulk. It had already
happened three times before anyone counted:

* #101 broke 9 of the 65 citations it touched, by inserting above them;
* the #120/#167/#161 batch displaced five constructs and broke 19 more;
* and when the whole tree was finally measured, **490 of 742 were already
  wrong** — two thirds. A reader following ``cli/agent_context.py:158-159``
  for the context fence landed in the middle of an unrelated docstring.

Re-deriving them by hand is the job this file replaces. The rule the repo kept
re-learning is *"re-derive the citation, never add a delta to it"*, and that is
mechanisable: a citation is a claim that some specific TEXT lives at some line,
so pin the text and the line follows.

HOW IT WORKS.

``citations.lock.json`` records, for every gated citation, the normalised source
text at the cited range. ``--check`` re-reads the tree and compares; when the
text has moved it searches for it and tells you the new number. ``--fix``
applies those relocations to the citing files and rewrites the lock. ``--lock``
accepts the tree as it stands — which is only ever correct straight after a
repair, because it enshrines whatever is there, right or wrong.

WHAT ``--fix`` REFUSES TO DO, AND WHY (#310). ``--fix`` used to rewrite the
whole lock afterwards, including the entries it had just reported as *not*
relocatable — enshrining whatever text happened to be sitting at the stale line
number as the new right answer. Measured at ``fad2e28``, where the defect was
found: inserting six lines into ``harness/core.py`` and editing one cited
comment drifted 71 citations; ``--fix`` relocated 59, named the other 12 as
needing a human, and then re-locked all 12 anyway, so the very next ``--check``
printed ``citations OK — 937 gated, none drifted.`` over twelve citations nobody
had repaired. (The totals track the tree, not the defect: the same probe against
this branch's base ``96f93c0c`` drifts 77, relocates 65, sticks on the same 12
and goes green at ``950 gated``.) One of them was re-locked
onto the single character ``)``. A gate that converts its own failures into
green is worse than no gate, so an anchor ``--fix`` could not relocate now keeps
its OLD text and ``--check`` stays red until a human re-derives the number.

``--lock`` may still overwrite such an anchor — it is the escape hatch for the
one case nothing else covers, a cited block edited *in place* whose citation is
still correct — but it now prints every anchor it replaces, so the act is
explicit rather than silent. That report is ``--lock``'s alone: under ``--fix``
the same list can fill up with citations that merely relocated ONTO a range some
other citation used to hold, which is a key changing hands and not an edit at
all (``.omc/specs/310-overwrite.py``). A repair pass may not narrate an edit it
cannot see, for the same reason it may not re-lock a failure.

AN ANCHOR THAT CANNOT IDENTIFY ITS TARGET IS NOT A GATE (#310). ``--check``
passes a citation when the text at the cited range still equals the locked text.
If that text is ``)``, or a blank line, the comparison succeeds against any line
the drift happens to land on, and the citation rots under a green gate. Measured
over the 552 anchors this branch's base ``96f93c0c`` locked: 21 match more than
one place in the file they point into, and two of those were *blank lines*
matching 344 and 181 places — both of those citations were already wrong (off by
15 and 54 lines) and the gate had never been able to say so. Repairing those two
is what takes this tree to 19. A blank- or punctuation-only anchor is therefore
refused outright at lock time; the merely ambiguous ones are counted here and
pinned in ``tests/test_citation_drift.py`` so the set can only shrink.

WHAT IS GATED, AND WHAT DELIBERATELY IS NOT.

Gated: ``packages/**``, ``tests/**``, ``docs/guides/**`` and root ``*.md``.
These are the things a reader follows *today* — running code, its tests, and
the guides that ship to users.

Not gated: ``docs/decisions/`` and ``.omc/specs/``. An ADR is a dated record of
a decision, and a sprint plan is a record of a plan. Rewriting their line
numbers to match today's tree would make them claim they were written about
code that did not exist yet. They are allowed to age.

Also not gated: citations whose path does not resolve to exactly one tracked
``.py`` file (pi's TypeScript sources, the mcp SDK, httpx internals, and a
handful of genuinely ambiguous bare stems). ``--report`` counts them, and
``tests/test_citation_drift.py`` pins the counts so that ungated set cannot
grow quietly.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO / "citations.lock.json"

# --- what counts as a live citation site -------------------------------------

GATED_PREFIXES = ("packages/", "tests/", "docs/guides/")
GATED_SUFFIXES = (".py", ".md")


def is_gated_site(rel: str) -> bool:
    """Is this file's prose something a reader follows against today's tree?"""

    if not rel.endswith(GATED_SUFFIXES):
        return False
    if rel.startswith(GATED_PREFIXES):
        return True
    # Root-level markdown — README, SECURITY, SLICE-STATUS — is live.
    return "/" not in rel and rel.endswith(".md")


# A full citation: an optionally-qualified path ending in .py, a line, an
# optional end line. The lookbehind keeps `a/b.py:1` from also matching `b.py:1`.
FULL = re.compile(r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py):(\d+)(?:-(\d+))?")

# A continuation: ``(`project_trust.py:161`, `:196`, `:244`)``. Two digits
# minimum, because `:8` in prose is far more often a port or a column.
CONT = re.compile(r"(?<![\w/.:-]):(\d{2,4})(?:-(\d+))?(?![\w.-])")


@dataclass
class Citation:
    citing_file: str
    citing_line: int  # 1-indexed
    col: int  # 0-indexed start of the NUMBER part within the line
    num_text: str  # exactly the text to rewrite, e.g. "572-576"
    target_raw: str  # the path as written, e.g. "harness/core.py"
    target: str | None  # resolved repo-relative path, or None
    start: int
    end: int

    @property
    def key(self) -> str:
        return f"{self.target}:{self.start}-{self.end}"

    @property
    def where(self) -> str:
        return f"{self.citing_file}:{self.citing_line}"


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def _suffix_index(py_files: list[str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for f in py_files:
        parts = f.split("/")
        for i in range(len(parts)):
            index["/".join(parts[i:])].add(f)
    return index


def _module_of(rel: str) -> str:
    """``packages/aelix-coding-agent/src/aelix_agents/tool.py`` -> ``aelix_agents``.

    Used only to break ties, and only in the direction a reader would: a bare
    ``stream.py:40`` written inside ``aelix_agents/`` means its sibling, not the
    unrelated ``aelix_coding_agent/tui/stream.py`` that shares the stem. Ties
    this does not break — chiefly bare stems cited from ``tests/``, which sits
    under no source module — stay ungated rather than guessed at.
    """

    parts = rel.split("/")
    if len(parts) >= 4 and parts[0] == "packages" and parts[2] == "src":
        return parts[3]
    return ""


def iter_citations(repo_files: list[str] | None = None) -> list[Citation]:
    """Every citation on a gated site, resolved where resolution is unambiguous."""

    files = repo_files if repo_files is not None else _tracked_files()
    py_files = [f for f in files if f.endswith(".py")]
    index = _suffix_index(py_files)

    found: list[Citation] = []
    for rel in files:
        if not is_gated_site(rel):
            continue
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(split_lines(text), 1):
            spans: list[tuple[int, int, str]] = []
            for m in FULL.finditer(line):
                raw_path = m.group(1)
                start, end = int(m.group(2)), int(m.group(3) or m.group(2))
                spans.append((*m.span(), raw_path))
                found.append(
                    Citation(
                        citing_file=rel,
                        citing_line=lineno,
                        col=m.start(2),
                        num_text=line[m.start(2) : m.end()],
                        target_raw=raw_path,
                        target=None,
                        start=start,
                        end=end,
                    )
                )
            if not spans:
                continue
            for m in CONT.finditer(line):
                if any(a <= m.start() < b for a, b, _ in spans):
                    continue
                # The NEAREST PRECEDING citation on the line, not the last one on
                # it. Measured: `reaper.py:278` reads
                #     ``(:189-193). Since ``agents/resolver.py:314-315`` makes …``
                # where the bare number continues ``modes/print_mode.py`` from four
                # lines up. Taking "the last full citation on the line" attributed
                # it to `resolver.py` and rewrote a CORRECT citation into a wrong
                # one — the exact harm this file exists to prevent, arriving by a
                # different door. A continuation with nothing before it on its own
                # line is left ungated rather than guessed at; cross-line
                # inheritance is what the author meant there, and this tool cannot
                # tell that from a coincidence.
                preceding = [t for _a, b, t in spans if b <= m.start()]
                if not preceding:
                    continue
                start, end = int(m.group(1)), int(m.group(2) or m.group(1))
                found.append(
                    Citation(
                        citing_file=rel,
                        citing_line=lineno,
                        col=m.start(1),
                        num_text=line[m.start(1) : m.end()],
                        target_raw=preceding[-1],
                        target=None,
                        start=start,
                        end=end,
                    )
                )

    lengths: dict[str, int] = {}

    def _fits(rel: str, end: int) -> bool:
        """Can the cited line even exist in this candidate?"""

        if rel not in lengths:
            try:
                lengths[rel] = len(split_lines((REPO / rel).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                lengths[rel] = 0
        return end <= lengths[rel]

    for c in found:
        cands = index.get(c.target_raw, set())
        if len(cands) == 1:
            c.target = next(iter(cands))
        elif len(cands) > 1:
            # RANGE FIRST, because a citation cannot point past the end of the
            # file it names — and this outranks proximity. Measured: the sibling
            # rule below picked `tui/stream.py` (160 lines) for a
            # ``stream.py:559-563`` written in `tui/commands.py`, where the
            # sentence plainly meant `aelix_agents/stream.py` (697) — its own
            # parenthetical cites `aelix_agents/envelope.py` two words later.
            # Proximity is a guess; fitting is a fact.
            fits = {f for f in cands if _fits(f, c.end)}
            if len(fits) == 1:
                c.target = next(iter(fits))
                continue
            if fits:
                cands = fits
            here = c.citing_file.rsplit("/", 1)[0]
            sibling = {f for f in cands if f.rsplit("/", 1)[0] == here}
            if len(sibling) == 1:
                c.target = next(iter(sibling))
                continue
            mod = _module_of(c.citing_file)
            same = {f for f in cands if _module_of(f) == mod} if mod else set()
            if len(same) == 1:
                c.target = next(iter(same))
    return found


# --- anchors ------------------------------------------------------------------

_WS = re.compile(r"\s+")
_TRIVIAL = re.compile(r'^[\s)\]}\'"#,:]*$')
MAX_ANCHOR_LINES = 8


def norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def is_trivial_anchor(anchor: list[str]) -> bool:
    """Does this anchor assert anything at all about where the citation points?

    A blank line, a lone ``)``, a bare ``#``, a docstring's closing quotes —
    each occurs by the hundred in the file it is supposed to pin, so
    ``have == want`` succeeds
    against whatever line the drift lands on and the citation rots green. Two
    such anchors were live in this tree, both the empty string, and both of
    their citations were already pointing somewhere their own sentence was not
    about. The repair is not a cleverer anchor, it is a wider citation: cite the
    construct, not the blank line under it.
    """

    return not anchor or all(_TRIVIAL.match(a) for a in anchor)


def split_lines(text: str) -> list[str]:
    """Line numbering the way every OTHER tool counts, i.e. on ``\\n`` alone.

    NOT ``str.splitlines()``. Python also breaks on U+2028, U+2029, form feed,
    vertical tab and U+0085; ``sed``, ``awk``, ``git blame``, editors, and the
    tracebacks a reader compares against do not. Five tracked files in this repo
    contain such a character, and this repository's most-cited target,
    ``cli/agent_context.py``, is one of them — every citation into it came out
    exactly one line low, silently, which is how the first pass wrote
    ``cli/agent_context.py:1068`` for a construct sitting at 1067.

    A trailing newline is dropped so ``len()`` is the line count a human would
    give, rather than that plus a phantom empty last line.
    """

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


class Tree:
    """Reads the WORKING tree, not git — so the tool is usable before you commit."""

    def __init__(self) -> None:
        self._cache: dict[str, list[str] | None] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self._cache:
            p = REPO / rel
            try:
                self._cache[rel] = split_lines(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                self._cache[rel] = None
        return self._cache[rel]

    def anchor(self, rel: str, start: int, end: int) -> list[str] | None:
        lines = self.lines(rel)
        if lines is None or start < 1 or end > len(lines) or end < start:
            return None
        return [norm(lines[i - 1]) for i in range(start, min(end, start + MAX_ANCHOR_LINES - 1) + 1)]

    def relocate(self, rel: str, anchor: list[str]) -> list[int]:
        """Where does this exact block live now? Empty if it is not there verbatim.

        Deliberately exact, and that was a correction. Near-match relocation was
        tried first and measured across 479 drifted citations: anchoring on a
        block's most distinctive surviving line and subtracting its offset put
        ``agents/resolver.py:183-206`` on a bare ``profile: AgentProfile,``
        parameter line and ``harness/loop.py:396-405`` on an unrelated
        ``isinstance`` comment. The assumption it makes — that the lines above
        the surviving one survived too — is exactly what a rewrite breaks.

        Writing a wrong number into the tree under a GREEN gate is worse than
        the rot this file exists to stop, so a block whose text was edited
        rather than moved is handed to a human. :meth:`relocate_hint` is what
        gives that human somewhere to start.
        """

        lines = self.lines(rel)
        if lines is None or not anchor:
            return []
        nn = [norm(x) for x in lines]
        return [i + 1 for i in range(len(nn) - len(anchor) + 1) if nn[i : i + len(anchor)] == anchor]

    def relocate_hint(self, rel: str, anchor: list[str]) -> list[int]:
        """Advisory only: where the block's most distinctive line went.

        Printed as a starting point, never applied — see :meth:`relocate`.
        """

        lines = self.lines(rel)
        if lines is None or not anchor:
            return []
        nn = [norm(x) for x in lines]
        distinctive = [a for a in anchor if len(a) >= 12 and not _TRIVIAL.match(a)]
        if not distinctive:
            return []
        key = max(distinctive, key=len)
        offset = anchor.index(key)
        return [i + 1 - offset for i, a in enumerate(nn) if a == key and i + 1 - offset >= 1]


# --- the lock -----------------------------------------------------------------


def ambiguous_anchors(anchors: dict[str, list[str]], tree: Tree) -> list[str]:
    """Anchors that match more than one place in the file they point into.

    The boundary is uniqueness in the target, not length or token count, and
    that was chosen by measuring both. BOTH LOCKS HOLD 552 ANCHORS — the repair
    in this commit swapped two, it did not add any — so the anchor count cannot
    say which lock a figure was taken from, and each figure below names its own.
    At the branch base ``96f93c0c``, where 21 anchors were ambiguous, a
    24-character threshold on the anchor's text flags 27, and 21 of those name
    exactly one place perfectly well, so it reaches 6 of the 21. On THIS tree,
    with the two blank anchors repaired, the same threshold flags 25, the same
    21 are innocent, and it reaches 4 of the 19. Either way length is wrong
    about 21 to reach a handful, and it is not a weaker version of the right
    question but a different one. Re-derive both with
    ``.omc/specs/310-threshold.py``, which also sweeps 10 readings of "length"
    across thresholds 4..64 so neither number rests on a single lucky metric.
    ``relocate`` already answers the real question — "can this text name one
    place?" — and the answer is free, because a gate that cannot locate the
    block it is watching cannot check it either.

    These are not refused (that would fail 19 live citations whose repair is a
    prose change in another lane's files); they are counted, and the count is
    pinned by ``tests/test_citation_drift.py`` so it can only shrink.
    """

    return sorted(k for k, a in anchors.items() if len(tree.relocate(k.rsplit(":", 1)[0], a)) > 1)


def load_lock() -> dict[str, list[str]]:
    if not LOCK_PATH.exists():
        return {}
    raw = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return raw.get("anchors", {})


def save_lock(anchors: dict[str, list[str]], stats: dict[str, int]) -> None:
    payload = {
        "_readme": (
            "Generated by scripts/check_citations.py. Each key is a cited range; "
            "each value is the normalised source text that was there when the "
            "citation was written. Do not hand-edit: run the script."
        ),
        "stats": stats,
        "anchors": dict(sorted(anchors.items())),
    }
    LOCK_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def build_stats(cites: list[Citation]) -> dict[str, int]:
    return {
        "sites": len(cites),
        "gated": sum(1 for c in cites if c.target),
        "unresolved": sum(1 for c in cites if not c.target),
        "citing_files": len({c.citing_file for c in cites}),
    }


# --- the three verbs ----------------------------------------------------------


@dataclass
class Drift:
    cite: Citation
    expected: list[str]
    actual: list[str] | None
    suggestion: tuple[int, int] | None


def find_drift(cites: list[Citation], anchors: dict[str, list[str]], tree: Tree) -> tuple[list[Drift], list[Citation]]:
    drifted: list[Drift] = []
    unlocked: list[Citation] = []
    for c in cites:
        if not c.target:
            continue
        want = anchors.get(c.key)
        if want is None:
            unlocked.append(c)
            continue
        have = tree.anchor(c.target, c.start, c.end)
        if have == want:
            continue
        hits = tree.relocate(c.target, want)
        suggestion = None
        if len(hits) == 1:
            span = c.end - c.start
            suggestion = (hits[0], hits[0] + span)
        drifted.append(Drift(cite=c, expected=want, actual=have, suggestion=suggestion))
    return drifted, unlocked


def cmd_check() -> int:
    tree = Tree()
    cites = iter_citations()
    anchors = load_lock()
    if not anchors:
        print("citations.lock.json is missing or empty — run --lock once.", file=sys.stderr)
        return 2
    drifted, unlocked = find_drift(cites, anchors, tree)
    generic = ambiguous_anchors(anchors, tree)
    note = (
        f"\n{len(generic)} locked anchor(s) match more than one place in their own target — "
        "those citations are recorded, not gated. `--report` names them."
        if generic
        else ""
    )
    if not drifted and not unlocked:
        print(f"citations OK — {build_stats(cites)['gated']} gated, none drifted.{note}")
        return 0
    for d in drifted:
        c = d.cite
        print(f"\n{c.where}: `{c.target_raw}:{c.num_text}` no longer points at what it cited.")
        print(f"    cited     : {d.expected[0][:100] if d.expected else '(empty)'}")
        actual = d.actual[0][:100] if d.actual else "(out of range)"
        print(f"    now there : {actual}")
        if d.suggestion:
            a, b = d.suggestion
            print(f"    moved to  : {a}-{b}" if b != a else f"    moved to  : {a}")
        else:
            hint = tree.relocate_hint(c.target or "", d.expected)
            where = f" (its most distinctive line is now near {hint[0]})" if len(hint) == 1 else ""
            print(f"    moved to  : the block was EDITED, not moved{where} — re-derive by hand.")
    for c in unlocked:
        have = tree.anchor(c.target or "", c.start, c.end)
        if have is not None and is_trivial_anchor(have):
            # Refused at lock time, so it can never become locked by running
            # --fix; saying "new citation" here would send the reader in a loop.
            print(
                f"\n{c.where}: `{c.target_raw}:{c.num_text}` cites a blank or "
                "punctuation-only line — there is nothing to anchor on. Widen it "
                "to the construct the sentence is about; --fix cannot."
            )
            continue
        print(f"\n{c.where}: `{c.target_raw}:{c.num_text}` is a NEW citation with no locked anchor.")
    print(
        f"\n{len(drifted)} drifted, {len(unlocked)} unlocked.{note} "
        "Run `python scripts/check_citations.py --fix` to relocate what can be relocated.",
        file=sys.stderr,
    )
    return 1


def cmd_fix() -> int:
    tree = Tree()
    cites = iter_citations()
    anchors = load_lock()
    drifted, unlocked = find_drift(cites, anchors, tree)

    # Group by citing file and rewrite right-to-left so earlier spans stay valid.
    by_file: dict[str, list[Drift]] = defaultdict(list)
    stuck: list[Drift] = []
    for d in drifted:
        if d.suggestion:
            by_file[d.cite.citing_file].append(d)
        else:
            stuck.append(d)

    changed = 0
    for rel, items in sorted(by_file.items()):
        path = REPO / rel
        # ``split_lines``, NOT ``splitlines()`` — the same rule that function
        # exists to enforce, and this WRITE path was the one place still breaking
        # it. ``citing_line`` is produced by ``split_lines``, so indexing a
        # ``splitlines()`` list with it is off by one for every line after a
        # U+2028 / U+2029 / form feed / vertical tab / U+0085 — and this repo's
        # most-cited file, ``cli/agent_context.py``, carries exactly one U+2028.
        #
        # MEASURED, not theorised. A ``--fix`` run relocating ``shell.py:3063``
        # wrote `3123` onto the FRONT of a neighbouring line and left the citation
        # itself untouched, producing a source file that no longer parsed. Nothing
        # in the tool noticed: the drift check reads with ``split_lines`` and so
        # never re-read the line it had corrupted. ADR-0224 sabotage-guarded three
        # line-counting sites; this was a fourth, on the only path that WRITES.
        text = path.read_text(encoding="utf-8")
        trailing = text.endswith("\n")
        lines = split_lines(text)
        items.sort(key=lambda d: (d.cite.citing_line, d.cite.col), reverse=True)
        for d in items:
            a, b = d.suggestion  # type: ignore[misc]
            new_text = str(a) if b == a else f"{a}-{b}"
            i = d.cite.citing_line - 1
            line = lines[i]
            before = line[: d.cite.col]
            after = line[d.cite.col + len(d.cite.num_text) :]
            lines[i] = before + new_text + after
            changed += 1
        path.write_text("\n".join(lines) + ("\n" if trailing else ""), encoding="utf-8")

    print(f"relocated {changed} citation(s) across {len(by_file)} file(s).")
    if stuck:
        print(f"\n{len(stuck)} could NOT be relocated automatically — re-derive by hand:")
        for d in stuck:
            print(f"  {d.cite.where}: `{d.cite.target_raw}:{d.cite.num_text}`")
            print(f"      cited: {d.expected[0][:90] if d.expected else '(empty)'}")
    if unlocked:
        print(f"\n{len(unlocked)} new citation(s) had no anchor; they are locked as written.")

    # Re-scan: the rewrite moved nothing in the citing files' own line numbering,
    # but the citations now name different ranges, so the lock is rebuilt fresh.
    #
    # KEEP, and this is the whole of #310. Rebuilding an anchor means reading
    # whatever is at the cited range NOW — which for a citation this run just
    # failed to relocate is, by definition, not what it cited. Locking that text
    # makes the next --check green over a citation nobody repaired, which is the
    # exact failure this file exists to prevent, arriving from inside the tool.
    # The old text stays, so the gate keeps failing until a human re-derives.
    return cmd_lock(quiet=False, remaining=len(stuck), keep={d.cite.key: d.expected for d in stuck})


def cmd_lock(
    quiet: bool = False, remaining: int = 0, keep: dict[str, list[str]] | None = None
) -> int:
    tree = Tree()
    cites = iter_citations()
    previous = load_lock()
    anchors: dict[str, list[str]] = {}
    unanchorable = 0
    blank: list[Citation] = []
    for c in cites:
        if not c.target:
            continue
        if keep is not None and c.key in keep:
            # ``--fix`` could not relocate this one. See the note at its call.
            anchors[c.key] = keep[c.key]
            continue
        a = tree.anchor(c.target, c.start, c.end)
        if a is None:
            unanchorable += 1
            continue
        if is_trivial_anchor(a):
            blank.append(c)
            continue
        anchors[c.key] = a
    ambiguous = ambiguous_anchors(anchors, tree)
    stats = build_stats(cites)
    stats["out_of_range"] = unanchorable
    stats["blank_line"] = len(blank)
    stats["ambiguous"] = len(ambiguous)
    # Anchors kept under the SAME key with different text. Under ``--lock`` that
    # is the override this command exists for: a block edited WHERE IT STOOD.
    #
    # It is not only that, and an earlier revision of this comment asserted it
    # was — "under ``--fix`` this list is empty by construction" — which is
    # false, and the print below inherited the falsehood. Ten lines reproduce it
    # (``.omc/specs/310-overwrite.py``): two citations, both blocks pushed down,
    # the first one relocates ONTO the exact range the second used to hold. The
    # key changes hands with nothing edited anywhere, the lock that gets written
    # is correct, and the report called it an in-place edit and sent the reader
    # after a bug in ``keep`` that is not there.
    #
    # So the report belongs to ``--lock`` alone — the verb where overwriting is a
    # human decision — and it now states only what it knows: this range was
    # pinned to other text. ``keep is None`` IS that test: ``cmd_fix`` always
    # passes a dict, empty when nothing was stuck. Under ``--fix`` a genuine
    # in-place edit cannot reach here at all, because it fails to relocate and
    # leaves through ``keep`` with its old text intact — which is #310.
    replaced = sorted(k for k, a in anchors.items() if k in previous and previous[k] != a)
    save_lock(anchors, stats)
    if not quiet:
        print(
            f"locked {len(anchors)} anchor(s) over {stats['gated']} gated citation(s) "
            f"({stats['unresolved']} ungated, {unanchorable} out of range, "
            f"{len(ambiguous)} too generic to gate)."
        )
        if replaced and keep is None:
            print(f"\nOVERWROTE {len(replaced)} anchor(s) — this range was pinned to other text:")
            for k in replaced:
                print(f"  {k}\n      was: {previous[k][0][:90] if previous[k] else '(empty)'}")
        for c in blank:
            print(
                f"\n{c.where}: `{c.target_raw}:{c.num_text}` cites a blank or "
                "punctuation-only line — there is nothing to anchor on, so the gate "
                "cannot tell this citation from any other. Widen it to the construct."
            )
    return 1 if (unanchorable or remaining or blank) else 0


def cmd_report() -> int:
    cites = iter_citations()
    stats = build_stats(cites)
    for k, v in stats.items():
        print(f"  {k:14} {v}")
    ungated: dict[str, int] = defaultdict(int)
    for c in cites:
        if not c.target:
            ungated[c.target_raw] += 1
    print("\n  ungated targets:")
    for t, n in sorted(ungated.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4}  {t}")
    tree = Tree()
    locked = load_lock()
    generic = ambiguous_anchors(locked, tree)
    print(f"\n  too generic to gate ({len(generic)}) — the anchor names more than one line:")
    for k in generic:
        hits = tree.relocate(k.rsplit(":", 1)[0], locked[k])
        print(f"    {len(hits):4}x  {k}  {locked[k][0][:60]!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="verify citations against the lock (default)")
    g.add_argument("--fix", action="store_true", help="relocate drifted citations, then rewrite the lock")
    g.add_argument("--lock", action="store_true", help="accept the tree as it stands and rewrite the lock")
    g.add_argument("--report", action="store_true", help="print what is gated and what is not")
    args = p.parse_args(argv)
    if args.fix:
        return cmd_fix()
    if args.lock:
        return cmd_lock()
    if args.report:
        return cmd_report()
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
