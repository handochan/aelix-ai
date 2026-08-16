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
    if not drifted and not unlocked:
        print(f"citations OK — {build_stats(cites)['gated']} gated, none drifted.")
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
        print(f"\n{c.where}: `{c.target_raw}:{c.num_text}` is a NEW citation with no locked anchor.")
    print(
        f"\n{len(drifted)} drifted, {len(unlocked)} unlocked. "
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
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
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
        path.write_text("".join(lines), encoding="utf-8")

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
    return cmd_lock(quiet=False, remaining=len(stuck))


def cmd_lock(quiet: bool = False, remaining: int = 0) -> int:
    tree = Tree()
    cites = iter_citations()
    anchors: dict[str, list[str]] = {}
    unanchorable = 0
    for c in cites:
        if not c.target:
            continue
        a = tree.anchor(c.target, c.start, c.end)
        if a is None:
            unanchorable += 1
            continue
        anchors[c.key] = a
    stats = build_stats(cites)
    stats["out_of_range"] = unanchorable
    save_lock(anchors, stats)
    if not quiet:
        print(
            f"locked {len(anchors)} anchor(s) over {stats['gated']} gated citation(s) "
            f"({stats['unresolved']} ungated, {unanchorable} out of range)."
        )
    return 1 if (unanchorable or remaining) else 0


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
