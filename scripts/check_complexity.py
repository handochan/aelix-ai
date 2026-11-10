#!/usr/bin/env python3
"""The complexity ratchet: no function's Ruff C901 score rises, and no new one passes 20.

Run: ``uv run python scripts/check_complexity.py --check`` (CI runs it on every
leg with ``--require-base --github-event``). Issue #293, ADR-0300. The owner's
decision of 2026-09-19: the gate is Ruff ``C901``; a function not in the
baseline may score at most 20; the functions already over 20 are listed in
``complexity-baseline.json`` with their score; a score that rises fails CI and a
score that falls lowers the baseline, which therefore only goes down; and no
``# noqa: C901``.

THE MEASUREMENT. ``ruff check`` over exactly the ``.py`` and ``.pyi`` files git
tracks under ``packages/`` and ``src/``, named one by one (``git ls-files``), with
only C901 selected and ``max-complexity`` 0, so every ``def`` is reported with its
score. Ruff lints a file it is given by name even where its own discovery would
skip it (its default excludes such as ``dist/``, ``.venv/`` or ``node_modules/``,
and ``.gitignore``), and an untracked file is not measured: CI's checkout has
none. ``--isolated`` makes ruff read no configuration file, so no ``ignore``,
``per-file-ignores`` or ``exclude`` in a ``pyproject.toml`` can take a function
out of the gate; ``--ignore-noqa`` makes ruff ignore every ``# noqa`` comment.
Both are also refused outright (below), so nobody believes one works. Anything
ruff writes to stderr (``Failed to lint`` a file that is not UTF-8, say) stops
the gate, and so does a ``def`` in a measured file that ruff did not report:
every one of them is. The score
is Ruff's number as Ruff reports it: Ruff adds a nested function's branches to
the function it sits in, and reports the nested function on its own too, so a
closure is counted in its parent and under its own name. The two numbers are not
added up anywhere here. The score depends on the Ruff version, which ``uv.lock``
pins; the baseline records the version it was measured with (THE RULER below).

IDENTITY. A function is ``<path>::<qualname>``: the path is relative to the
repository root and written with ``/`` on every OS, and the qualname is the
dotted path of the classes and functions it is defined in, spelled as
``__qualname__`` spells it (``Class.method``, ``outer.<locals>.inner``). Ruff
reports only the bare name and where it is, so the qualname comes from the
file's AST: the ``def`` with that name whose header holds that line. Two
definitions with the same qualname in one file (a ``def`` in each arm of an
``if``, a property's getter and setter, a name defined twice) are numbered in
source order: the first keeps the bare qualname and the n-th is ``<qualname>#n``.
``@overload`` stubs are numbered on their own, ``<qualname>#overload<n>``, so
adding an overload does not renumber the implementation. Reordering same-named
definitions, or adding one above another (an ``if TYPE_CHECKING`` stub), does
renumber them, and nothing can tell which of them is which. So among the
same-named definitions of one file the gate holds their scores as a set: the
scores over 20, highest first, may not pass the listed ones, highest first, and
there may not be more of them (a renumbering, reported as STALE and rewritten by
``--update``). The cost: scores can move from one same-named definition to
another, listed or not, as long as the set does not rise past the listed set,
so an unlisted one can take a listed one's score without being NEW, and with
three or more several may rise (ADR-0300 section 2).

``--check`` fails on:

* a function over 20 that is not in the baseline (NEW);
* a function whose score is above its baseline score (RAISED);
* a baseline entry the tree no longer matches (STALE): its score fell, it is at
  20 or below now, no function has that identity any more (removed, renamed or
  moved), or same-named definitions were renumbered. The fix is ``--update``,
  committed with the change, so that a lower score is the new ceiling at once
  instead of leaving room to climb back;
* a ``# noqa`` comment naming C901 or a prefix of it (``C90``, ``C9``, ``C``) in
  the measured files, and a ruff ``ignore``/``extend-ignore``/
  ``per-file-ignores``/``extend-per-file-ignores`` entry naming one (or ``ALL``)
  in a ``pyproject.toml``, ``ruff.toml`` or ``.ruff.toml`` at the root or under
  ``packages/`` and ``src/``;
* a baseline measured with another Ruff version than the one running;
* (THE HISTORY CHECK) a baseline that gained an entry or a higher score than the
  baseline at ``git merge-base HEAD <--base>`` (default ``origin/main``), which
  is how CI tells "the code got simpler" from "someone raised the number by
  hand". Each entry is paired, one to one, with an entry the merge base had and
  that scored at least as high: its own, a same-named definition's of its file
  (a renumbering), or one whose function is gone from the tree now (a rename,
  what ``--rename`` writes). An entry left unpaired fails. The check is skipped,
  and says so, when the merge base has no baseline or was measured with another
  Ruff; without git or the ref it says so and passes, unless ``--require-base``
  (CI) makes that a failure. ``--github-event`` (CI) compares a push to ``main``
  with the commit that push replaced instead: there the merge base with
  ``origin/main`` is ``HEAD`` itself.

``--update`` writes only what lowers: a score that fell is rewritten, an entry
at 20 or below or gone is dropped, and renumbered same-named definitions get
their new identities. It never adds an entry and never raises a score; whatever
it could not write it reports, it leaves the entries of a function name with a
NEW or RAISED definition as they are, and it exits 1 while one remains.

``--rename OLD NEW`` carries a baseline entry to a new identity (a function
renamed or moved to another file or class): OLD must be gone from the tree, NEW
must be over 20 and not yet listed, and NEW's score must not be above OLD's.
Moving a listed definition out from before a same-named one, or in above a
listed one, renumbers the others and takes two changes (ADR-0300, Consequences).

``--init`` writes the baseline from the tree as it is: every function over 20
with its score. It is for the first baseline and for a Ruff upgrade, and it
prints what changed against the baseline it replaces. Used anywhere else it is
a hand raise, which the history check refuses.

THE RULER. A Ruff upgrade that moves scores re-measures; it is not a code
change. Such a change bumps ruff in ``uv.lock``, runs ``--init`` and commits the
baseline, and nothing else; ``--check`` then prints the old and new scores side
by side instead of comparing them, for the reviewer. In GitHub Actions it also
writes that list to the job summary and raises a warning annotation that names
it, so the green step is not the only place it shows.

``--trend`` prints the per-release trend value (not a gate): radon 6.0.1's
cyclomatic complexity over the same directories, with how many are over 20 and
over 50, counted two ways. First the trend value, every function, method and
closure counted once, as the owner decided on 2026-11-09; then, for comparison,
the 2026-09-19 way, radon's recursive count as docs/05 made it, which lists
every method twice. It runs radon through ``uvx`` (no dependency added).

Exit: 0 clean; 1 a problem, each named with what to do; 2 the measurement could
not be made (git or ruff failed, ruff warned, a file did not parse, the baseline
or the radon report is missing, unreadable or malformed).
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The owner's limit (2026-09-19). Not read from the baseline, so the baseline
#: cannot move it.
LIMIT = 20
BASELINE_NAME = "complexity-baseline.json"
#: The directories measured: those of the owner's 2026-09-19 measurement.
SCOPE = ("packages", "src")
RADON = "radon==6.0.1"
_RUFF_CONFIGS = ("pyproject.toml", "ruff.toml", ".ruff.toml")
_IGNORE_KEYS = ("ignore", "extend-ignore", "per-file-ignores", "extend-per-file-ignores")
_MESSAGE = re.compile(r"^`(?P<name>[^`]+)` is too complex \((?P<score>\d+) > (?P<limit>\d+)\)$")
_NOQA = re.compile(r"noqa\s*:\s*(?P<codes>[A-Za-z0-9][A-Za-z0-9,\s]*)", re.IGNORECASE)
#: C901 and every rule selector that covers it: ``C90`` (mccabe), ``C9`` and ``C``.
_C901 = re.compile(r"C(?:9(?:0(?:1)?)?)?", re.IGNORECASE)
#: Windows refuses a command line over 32,767 characters, so the files go to ruff
#: in batches well under it (on 6caaae09: 325 files, 21,966 characters).
_ARGV_BUDGET = 8_000
_ABOUT = (
    "Ruff C901 complexity ratchet (ADR-0300, #293): every function in packages/ and src/ "
    "scoring over 20, with its score. Only goes down. Written by "
    "scripts/check_complexity.py --update / --rename / --init; never raise a score or add "
    "an entry by hand."
)

_TODO = """\
What to do:
  NEW / RAISED  Make the function simpler, so that its score is at most 20 (NEW) or at most
                its baseline score (RAISED). Move a responsibility where it belongs; splitting
                it into pieces only to lower the number is not the fix (ADR-0300).
                `# noqa: C901` is refused, and the measurement ignores it anyway. Never add or
                raise a baseline entry by hand: CI compares the baseline with main's.
  STALE         Run `uv run python scripts/check_complexity.py --update` and commit
                complexity-baseline.json with the change: a lower score is the new ceiling,
                and renumbered same-named definitions get their new identities.
  GONE          If the function was renamed or moved, carry its entry instead:
                `uv run python scripts/check_complexity.py --rename OLD NEW`.
  HISTORY       The baseline gained an entry or a score against the merge base: restore those
                lines and make the function simpler. Without a merge base, fetch the base ref
                (CI's checkout has the full history)."""


class MeasureError(Exception):
    """The measurement could not be made; exit 2."""


@dataclass(frozen=True)
class _Def:
    qualname: str
    name: str
    first: int  # the `def` line
    last: int  # the first body line: the name sits between the two
    col: int
    overload: bool


@dataclass
class Baseline:
    ruff: str
    functions: dict[str, int]


@dataclass
class Findings:
    new: list[tuple[str, int]] = field(default_factory=list)
    raised: list[tuple[str, int, int]] = field(default_factory=list)
    fell: list[tuple[str, int, int]] = field(default_factory=list)
    low: list[tuple[str, int, int]] = field(default_factory=list)
    gone: list[tuple[str, int]] = field(default_factory=list)
    #: (name, listed entries, entries over the limit now) of same-named
    #: definitions whose numbers moved and whose scores, as a set, did not rise.
    renumbered: list[tuple[str, dict[str, int], dict[str, int]]] = field(default_factory=list)

    @property
    def violations(self) -> bool:
        return bool(self.new or self.raised)

    @property
    def stale(self) -> bool:
        return bool(self.fell or self.low or self.gone or self.renumbered)


# --- measuring ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, check=False)
    except OSError as exc:
        raise MeasureError(f"could not run {cmd[0]}: {exc}") from exc


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").strip()


def ruff_version(root: Path) -> str:
    proc = _run([sys.executable, "-m", "ruff", "--version"], root)
    match = re.fullmatch(r"ruff (\S+)", _text(proc.stdout))
    if proc.returncode != 0 or match is None:
        raise MeasureError(
            f"`{sys.executable} -m ruff --version` failed ({_text(proc.stderr) or _text(proc.stdout)}). "
            "Run this script with the project's interpreter: `uv run python scripts/check_complexity.py`."
        )
    return match.group(1)


def _sources(root: Path, *flags: str) -> list[str]:
    """The ``.py``/``.pyi`` paths ``git ls-files <flags>`` lists under the measured directories."""
    proc = _run(["git", "-C", str(root), "ls-files", "-z", *flags, "--", *SCOPE], root)
    if proc.returncode != 0:
        raise MeasureError(
            f"`git ls-files` failed in {root} ({_text(proc.stderr)}): the gate measures the "
            "files git tracks, so it runs in a git checkout"
        )
    names = proc.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return sorted(name for name in names if name.endswith((".py", ".pyi")))


def tracked_sources(root: Path) -> list[str]:
    """The measured files: every tracked ``.py``/``.pyi`` under ``packages/`` and ``src/``."""
    files = [rel for rel in _sources(root) if (root / rel).is_file()]
    if not files:
        raise MeasureError(f"git tracks no .py or .pyi file under {', '.join(SCOPE)} in {root}")
    return files


def _batches(files: list[str]) -> list[list[str]]:
    batches: list[list[str]] = [[]]
    size = 0
    for rel in files:
        if batches[-1] and size + len(rel) + 3 > _ARGV_BUDGET:
            batches.append([])
            size = 0
        batches[-1].append(rel)
        size += len(rel) + 3
    return batches


def run_ruff(root: Path, files: list[str]) -> list[dict[str, Any]]:
    """Every ``def`` in ``files`` (relative to ``root``), as ruff's JSON diagnostics."""
    diagnostics: list[dict[str, Any]] = []
    for batch in _batches(files):
        cmd = [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--ignore-noqa",
            "--no-cache",
            "--select",
            "C901",
            "--config",
            "lint.mccabe.max-complexity=0",
            "--output-format",
            "json",
            "--exit-zero",
            *batch,
        ]
        proc = _run(cmd, root)
        if proc.returncode != 0:
            raise MeasureError(f"ruff exited {proc.returncode}: {_text(proc.stderr)}")
        if _text(proc.stderr):
            # "warning: Failed to lint <file>: stream did not contain valid UTF-8"
            # and the like: a file ruff did not measure.
            raise MeasureError(
                f"ruff did not measure everything it was given: {_text(proc.stderr)}"
            )
        try:
            found = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MeasureError(f"ruff's JSON output did not parse: {exc}") from exc
        if not isinstance(found, list):
            raise MeasureError("ruff's JSON output is not a list")
        diagnostics += found
    return diagnostics


def relative_key_path(filename: str, root: str, flavour: type[PurePath] = Path) -> str:
    """``filename`` relative to ``root``, with ``/``: the path part of an identity.

    ``flavour`` is the path type: ``Path`` for this machine, ``PureWindowsPath``
    to read a Windows path anywhere (drive letters and case do not matter there).
    """
    path, base = flavour(filename), flavour(root)
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        raise MeasureError(f"ruff reported {filename}, which is not under {root}") from None


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "overload":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "overload":
            return True
    return False


def definitions(tree: ast.Module) -> list[_Def]:
    """Every ``def`` in ``tree`` with its qualname. Iterative: no recursion limit."""
    found: list[_Def] = []
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + child.name
                found.append(
                    _Def(
                        qualname,
                        child.name,
                        child.lineno,
                        child.body[0].lineno,
                        child.col_offset,
                        _is_overload(child),
                    )
                )
                stack.append((child, qualname + ".<locals>."))
            elif isinstance(child, ast.ClassDef):
                stack.append((child, prefix + child.name + "."))
            else:
                stack.append((child, prefix))
    return found


def identities(defs: list[_Def]) -> dict[_Def, str]:
    """The qualname part of each definition's identity, numbered when it repeats."""
    groups: dict[tuple[str, bool], list[_Def]] = defaultdict(list)
    for d in defs:
        groups[(d.qualname, d.overload)].append(d)
    keys: dict[_Def, str] = {}
    for (qualname, overload), members in groups.items():
        members.sort(key=lambda d: (d.first, d.col))
        for n, d in enumerate(members, start=1):
            if overload:
                keys[d] = f"{qualname}#overload{n}"
            else:
                keys[d] = qualname if n == 1 else f"{qualname}#{n}"
    return keys


def the_def(defs: list[_Def], name: str, row: int, rel: str) -> _Def:
    """The definition ruff means by ``name`` at ``row``: the one whose header holds that line.

    A nested def that is its parent's first statement, with the parent's name,
    starts on the line where the parent's header ends; there the def that
    starts on the reported line is the one ruff reports (it reports the name).
    """
    here = [d for d in defs if d.name == name and d.first <= row <= d.last]
    exact = [d for d in here if d.first == row]
    if len(here) > 1 and len(exact) == 1:
        return exact[0]
    if len(here) != 1:
        raise MeasureError(
            f"ruff reported `{name}` at line {row} of {rel}, and {len(here)} definitions match it"
        )
    return here[0]


_DEF_TEXT = re.compile(rb"\bdef\b")


def measure(root: Path, files: list[str], diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    """``{identity: score}`` for every function in ``files``, as ruff reported them."""
    by_file: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
    base = str(root.resolve())
    for diag in diagnostics:
        filename = str(Path(str(diag.get("filename", ""))).resolve())
        if diag.get("code") != "C901":
            row = (diag.get("location") or {}).get("row")
            raise MeasureError(
                f"ruff could not measure {filename} (line {row}): {diag.get('message')}"
            )
        match = _MESSAGE.match(str(diag.get("message", "")))
        if match is None:
            raise MeasureError(
                f"ruff's C901 message {diag.get('message')!r} is not in the form this script reads; "
                "a Ruff upgrade changed it (see THE RULER in this script)"
            )
        rel = relative_key_path(filename, base)
        if not rel.endswith((".py", ".pyi")):
            raise MeasureError(
                f"ruff measured {rel}, which is not a Python source file this gate can name"
            )
        by_file[rel].append((int(diag["location"]["row"]), match["name"], int(match["score"])))

    scores: dict[str, int] = {}
    for rel in sorted(set(files) | set(by_file)):
        reported = by_file.get(rel, [])
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            raise MeasureError(f"{rel} could not be read: {exc}") from exc
        if not reported and not _DEF_TEXT.search(data):
            continue
        try:
            tree = ast.parse(data, filename=rel)
        except (SyntaxError, ValueError) as exc:
            raise MeasureError(f"{rel} did not parse: {exc}") from exc
        defs = definitions(tree)
        keys = identities(defs)
        seen: set[_Def] = set()
        for row, name, score in reported:
            d = the_def(defs, name, row, rel)
            if d in seen:
                raise MeasureError(f"ruff reported `{name}` at line {d.first} of {rel} twice")
            seen.add(d)
            scores[f"{rel}::{keys[d]}"] = score
        missed = [d for d in defs if d not in seen]
        if missed:
            # max-complexity 0 reports every def: one that is missing escaped the gate.
            raise MeasureError(
                f"ruff did not report {len(missed)} of the {len(defs)} definitions in {rel}, "
                f"the first `{missed[0].qualname}` at line {missed[0].first}"
            )
    return scores


def noqa_problems(root: Path, files: list[str]) -> list[str]:
    """``# noqa`` comments naming C901, or a prefix of it, in ``files``."""
    problems: list[str] = []
    for rel in sorted(files):
        data = (root / rel).read_bytes()
        if not re.search(rb"(?i)noqa", data):
            continue
        try:
            tokens = list(tokenize.tokenize(io.BytesIO(data).readline))
        except (tokenize.TokenError, SyntaxError) as exc:
            raise MeasureError(f"{rel} did not tokenize: {exc}") from exc
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            match = _NOQA.search(tok.string)
            if match and any(
                _C901.fullmatch(code) for code in re.split(r"[,\s]+", match["codes"]) if code
            ):
                problems.append(f"NOQA    {rel} line {tok.start[0]}: {tok.string.strip()}")
    return problems


def _config_files(root: Path) -> list[Path]:
    found = [root / name for name in _RUFF_CONFIGS if (root / name).is_file()]
    for top in SCOPE:
        if (root / top).is_dir():
            for name in _RUFF_CONFIGS:
                found += [p for p in (root / top).rglob(name) if ".venv" not in p.parts]
    return sorted(set(found))


def config_problems(root: Path) -> list[str]:
    """Ruff ignore entries covering C901 in the configuration files near the measured code."""
    problems: list[str] = []
    for path in _config_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise MeasureError(f"{rel} did not parse: {exc}") from exc
        if path.name == "pyproject.toml":
            ruff, label = data.get("tool", {}).get("ruff"), "tool.ruff"
        else:
            ruff, label = data, ""
        if not isinstance(ruff, dict):
            continue
        tables = [(label, ruff)]
        if isinstance(ruff.get("lint"), dict):
            tables.append((f"{label}.lint".lstrip("."), ruff["lint"]))
        for where, table in tables:
            for key in _IGNORE_KEYS:
                value = table.get(key)
                entries: list[tuple[str, object]] = []
                if isinstance(value, list):
                    entries = [("", v) for v in value]
                elif isinstance(value, dict):
                    entries = [
                        (f" {pattern!r}", v)
                        for pattern, codes in value.items()
                        for v in (codes if isinstance(codes, list) else [codes])
                    ]
                for pattern, code in entries:
                    if isinstance(code, str) and (_C901.fullmatch(code) or code == "ALL"):
                        section = f"{where}.{key}".lstrip(".")
                        problems.append(f"CONFIG  {rel} [{section}]{pattern}: {code}")
    return problems


# --- the baseline ------------------------------------------------------------------------


def parse_baseline(text: str, where: str) -> Baseline:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MeasureError(f"{where} is not JSON: {exc}") from exc
    ruff = data.get("ruff") if isinstance(data, dict) else None
    functions = data.get("functions") if isinstance(data, dict) else None
    if not isinstance(ruff, str) or not isinstance(functions, dict):
        raise MeasureError(f'{where} must be an object with "ruff" (a version) and "functions"')
    for key, score in functions.items():
        if "::" not in key or type(score) is not int:
            raise MeasureError(
                f"{where}: entry {key!r}: {score!r} is not `<path>::<qualname>`: <integer>"
            )
        if score <= LIMIT:
            raise MeasureError(
                f"{where}: entry {key!r} has score {score}, but the baseline lists only functions over {LIMIT}"
            )
    return Baseline(ruff, dict(functions))


def load_baseline(path: Path) -> Baseline:
    if not path.is_file():
        raise MeasureError(
            f"{path.name} is missing. The first baseline is made once with "
            "`uv run python scripts/check_complexity.py --init`."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MeasureError(f"{path.name} could not be read as UTF-8 text: {exc}") from exc
    return parse_baseline(text, path.name)


def write_baseline(path: Path, ruff: str, functions: dict[str, int]) -> None:
    body = {"about": _ABOUT, "ruff": ruff, "functions": dict(sorted(functions.items()))}
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(body, indent=2, ensure_ascii=True) + "\n")


def name_of(key: str) -> str:
    """``key`` without its ``#n``/``#overload<n>`` number: what same-named definitions share."""
    path, _, qualname = key.rpartition("::")
    return f"{path}::{qualname.split('#', 1)[0]}"


def fits_under(scores: list[int], ceilings: list[int]) -> bool:
    """Whether each score can take its own ceiling, one to one, and stay at or under it.

    Sorted highest first, the i-th score must be at most the i-th ceiling.
    """
    if len(scores) > len(ceilings):
        return False
    return all(
        score <= ceiling
        for score, ceiling in zip(
            sorted(scores, reverse=True), sorted(ceilings, reverse=True), strict=False
        )
    )


def compare(measured: dict[str, int], baseline: dict[str, int]) -> Findings:
    found = Findings()
    for key, score in sorted(measured.items()):
        if score <= LIMIT:
            continue
        if key not in baseline:
            found.new.append((key, score))
        elif score > baseline[key]:
            found.raised.append((key, baseline[key], score))
        elif score < baseline[key]:
            found.fell.append((key, baseline[key], score))
    for key, was in sorted(baseline.items()):
        if key not in measured:
            found.gone.append((key, was))
        elif measured[key] <= LIMIT:
            found.low.append((key, was, measured[key]))
    # Same-named definitions renumbered: a NEW or RAISED one whose name's scores,
    # as a set, still fit under the listed ones is a renumbering, not a rise.
    for name in sorted({name_of(k) for k, _ in found.new} | {name_of(k) for k, *_ in found.raised}):
        listed = {k: s for k, s in baseline.items() if name_of(k) == name}
        now = {k: s for k, s in measured.items() if name_of(k) == name and s > LIMIT}
        if not listed or not fits_under(list(now.values()), list(listed.values())):
            continue
        found.renumbered.append((name, listed, now))
        found.new = [f for f in found.new if name_of(f[0]) != name]
        found.raised = [f for f in found.raised if name_of(f[0]) != name]
        found.fell = [f for f in found.fell if name_of(f[0]) != name]
        found.low = [f for f in found.low if name_of(f[0]) != name]
        found.gone = [f for f in found.gone if name_of(f[0]) != name]
    return found


def _leaf(key: str) -> str:
    return key.rsplit("::", 1)[1].split("#", 1)[0].rsplit(".", 1)[-1]


def report_lines(found: Findings) -> list[str]:
    lines = [f"NEW     {key}  scores {score} (limit {LIMIT})" for key, score in found.new]
    lines += [f"RAISED  {key}  {was} -> {now}" for key, was, now in found.raised]
    lines += [f"STALE   {key}  fell {was} -> {now}" for key, was, now in found.fell]
    lines += [f"STALE   {key}  {was} -> {now}, within the limit now" for key, was, now in found.low]
    for key, was in found.gone:
        hint = [new for new, score in found.new if _leaf(new) == _leaf(key) and score <= was]
        also = f"; renamed? --rename {key} {hint[0]}" if hint else ""
        lines.append(f"GONE    {key}  (baseline {was}): no function has this identity now{also}")
    for name, listed, now in found.renumbered:
        lines.append(f"STALE   {name}  same-named definitions {_renumbering(listed, now)}")
    return lines


def _renumbering(listed: dict[str, int], now: dict[str, int]) -> str:
    def spell(entries: dict[str, int]) -> str:
        return ", ".join(f"{k.rpartition('::')[2]} {s}" for k, s in sorted(entries.items()))

    return f"renumbered: listed {spell(listed)}; now {spell(now) or 'none over the limit'}"


def _ruler_lines(was: str, now: str) -> list[str]:
    return [
        f"RUFF    {BASELINE_NAME} was measured with ruff {was}; this is ruff {now}.",
        "        A Ruff upgrade re-measures: in the change that bumps ruff in uv.lock, and in nothing",
        "        else, run `uv run python scripts/check_complexity.py --init` and commit the baseline.",
    ]


def diff_lines(old: dict[str, int], new: dict[str, int]) -> list[str]:
    lines = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        kind = (
            "added" if a is None else "dropped" if b is None else "raised" if b > a else "lowered"
        )
        lines.append(f"  {kind:<8}{key}  {'-' if a is None else a} -> {'-' if b is None else b}")
    return lines


# --- the history check -------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return _run(["git", "-C", str(root), *args], root)


def unpaired(current: dict[str, int], old: dict[str, int], vanished: set[str]) -> list[str]:
    """The entries of ``current`` that cannot each take an entry of ``old`` of their own.

    An entry may take an ``old`` entry that scored at least as much and is its
    own, a same-named definition's (a renumbering), or in ``vanished`` (a rename
    of a function gone from the tree). One to one: augmenting paths (Kuhn), so
    the pairing is a maximum one; an entry keeping its own key and score comes
    first, so what is left unpaired is a raise or an addition.
    """
    taker: dict[str, str] = {}

    def options(key: str) -> list[str]:
        score = current[key]
        mine = [key] if old.get(key, -1) >= score else []
        others = [
            o
            for o in old
            if o != key and old[o] >= score and (name_of(o) == name_of(key) or o in vanished)
        ]
        return mine + sorted(others, key=lambda o: (old[o], o))

    def place(key: str, tried: set[str]) -> bool:
        for slot in options(key):
            if slot in tried:
                continue
            tried.add(slot)
            if slot not in taker or place(taker[slot], tried):
                taker[slot] = key
                return True
        return False

    order = sorted(current, key=lambda k: (old.get(k, -1) < current[k], k))
    return [key for key in order if not place(key, set())]


def _announce_ruler(was: str, now: str, short: str, changes: list[str]) -> None:
    """In GitHub Actions, put a re-measurement where a reviewer sees it: the job summary and a warning."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    title = f"Complexity baseline re-measured: ruff {was} at {short} -> ruff {now}, {len(changes)} changed"
    print(f"::warning title=Complexity ratchet::{title}. The job summary lists every change.")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        body = "\n".join(f"    {line.strip()}" for line in changes) or "    (no score moved)"
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"### {title}\n\nNot compared, because the ruler changed. A change that bumps ruff "
                "re-measures and changes nothing else (ADR-0300 \u00a77): check that every line "
                f"below comes from the new ruff, not from code.\n\n{body}\n"
            )


def github_base(default: str) -> tuple[str, list[str]]:
    """The ref a GitHub Actions run compares with, and a note saying which and why.

    On a push to ``main`` the merge base with ``origin/main`` is ``HEAD`` itself,
    so it compares with the commit the push replaced, the event's ``before``.
    Every other event, and a push that created ``main``, keeps ``default``.
    """
    if (
        os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        return default, []
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    try:
        before = json.loads(Path(path).read_text(encoding="utf-8")).get("before")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise MeasureError(
            f"a push to main, and its event file {path!r} did not read: {exc}"
        ) from exc
    if not isinstance(before, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", before):
        raise MeasureError(f"a push to main, and its event's `before` is {before!r}, not a commit")
    if not before.strip("0"):
        return default, [f"HISTORY a push that created main: compared with {default}"]
    return before, [f"HISTORY a push to main: compared with {before[:8]}, the main it replaced"]


def history_problems(
    root: Path, current: Baseline, measured: dict[str, int], base_ref: str, require: bool
) -> tuple[list[str], list[str]]:
    """``(problems, notes)`` from comparing the baseline with the merge base's."""
    try:
        proc = _git(root, "merge-base", "HEAD", base_ref)
    except MeasureError as exc:
        proc, why = None, str(exc)
    else:
        why = _text(proc.stderr) or f"git merge-base exited {proc.returncode}"
    if proc is None or proc.returncode != 0:
        line = f"HISTORY not compared: no merge base with {base_ref} ({why})"
        if require:
            return [line + "; --require-base makes this a failure"], []
        return [], [line]
    sha = _text(proc.stdout)
    short = sha[:8]
    spec = f"{sha}:./{BASELINE_NAME}"
    if _git(root, "cat-file", "-e", spec).returncode != 0:
        return [], [f"HISTORY merge base {short} has no {BASELINE_NAME}: nothing to compare"]
    old = parse_baseline(_text(_git(root, "show", spec).stdout), f"{BASELINE_NAME} at {short}")
    if old.ruff != current.ruff:
        changes = diff_lines(old.functions, current.functions)
        notes = [
            f"HISTORY not compared: the merge base {short} measured with ruff {old.ruff}, this "
            f"baseline with ruff {current.ruff}. Review the re-measurement by hand:",
            *changes,
        ]
        _announce_ruler(old.ruff, current.ruff, short, changes)
        return [], notes
    problems: list[str] = []
    vanished = {
        key for key in old.functions if key not in current.functions and key not in measured
    }
    for key in unpaired(current.functions, old.functions, vanished):
        score = current.functions[key]
        if key in old.functions:
            problems.append(
                f"HISTORY {key}  raised in the baseline: {old.functions[key]} at {short} -> {score}"
            )
        else:
            problems.append(
                f"HISTORY {key}  added to the baseline ({score}) and not a rename of a function "
                f"gone since {short} or a renumbering; the baseline only goes down"
            )
    notes = (
        []
        if problems
        else [f"HISTORY baseline compared with merge base {short}: nothing added or raised"]
    )
    return problems, notes


# --- the trend value ---------------------------------------------------------------------


def radon_trend(report: dict[str, Any]) -> dict[str, tuple[int, int, int]]:
    """``{"once": ..., "docs05": ...}``: (count, CC > 20, CC > 50) from radon's ``cc -j``.

    radon lists each method twice - at the file's top level and inside its
    class's ``methods`` - so ``once`` skips class blocks; ``docs05`` also walks
    the classes' methods, which is how docs/05 counted 4,774 on 2026-09-19.
    """
    errors = [name for name, blocks in report.items() if not isinstance(blocks, list)]
    if errors:
        raise MeasureError(
            f"radon could not read {len(errors)} file(s): {', '.join(sorted(errors)[:5])}"
        )

    def walk(blocks: list[dict[str, Any]], out: list[int], methods_again: bool) -> None:
        for block in blocks:
            if block.get("type") == "class":
                if methods_again:
                    walk(block.get("methods", []), out, methods_again)
                continue
            out.append(int(block["complexity"]))
            walk(block.get("closures", []), out, methods_again)

    result = {}
    for label, again in (("once", False), ("docs05", True)):
        scores: list[int] = []
        for blocks in report.values():
            walk(blocks, scores, again)
        result[label] = (len(scores), sum(s > 20 for s in scores), sum(s > 50 for s in scores))
    return result


def _radon_report(root: Path) -> dict[str, Any]:
    uvx = shutil.which("uvx")
    uv = shutil.which("uv")
    if uvx:
        launcher = [uvx]
    elif uv:
        launcher = [uv, "tool", "run"]
    else:
        raise MeasureError("--trend runs radon through uvx, and neither uvx nor uv is on PATH")
    proc = _run([*launcher, "--from", RADON, "radon", "cc", "-j", *SCOPE], root)
    if proc.returncode != 0:
        raise MeasureError(f"radon exited {proc.returncode}: {_text(proc.stderr)}")
    return _radon_json(proc.stdout, "radon's output")


def _radon_json(raw: bytes, where: str) -> dict[str, Any]:
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasureError(f"{where} is not radon's JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise MeasureError(f"{where} is not radon's JSON: not an object of files")
    return report


# --- the commands ------------------------------------------------------------------------


def _measure_all(root: Path) -> tuple[str, list[str], dict[str, int]]:
    version = ruff_version(root)
    files = tracked_sources(root)
    measured = measure(root, files, run_ruff(root, files))
    if not measured:
        raise MeasureError(
            f"ruff measured no function in {', '.join(SCOPE)}: the gate would be blind"
        )
    return version, files, measured


def _untracked_note(root: Path) -> list[str]:
    untracked = _sources(root, "--others", "--exclude-standard")
    if not untracked:
        return []
    return [
        f"UNTRACKED {len(untracked)} .py/.pyi file(s) under {' and '.join(SCOPE)} are not measured "
        f"until `git add`ed, and CI will measure them: {', '.join(untracked[:3])}"
        + (", ..." if len(untracked) > 3 else "")
    ]


def cmd_check(root: Path, base_ref: str, require_base: bool, github_event: bool = False) -> int:
    baseline_path = root / BASELINE_NAME
    baseline = load_baseline(baseline_path)
    version, files, measured = _measure_all(root)
    problems: list[str] = []
    notes: list[str] = _untracked_note(root)
    if github_event:
        base_ref, why = github_base(base_ref)
        notes += why
    if baseline.ruff != version:
        problems += _ruler_lines(baseline.ruff, version)
        found = Findings()
    else:
        found = compare(measured, baseline.functions)
        problems += report_lines(found)
    problems += noqa_problems(root, files)
    problems += config_problems(root)
    more, history_notes = history_problems(root, baseline, measured, base_ref, require_base)
    problems += more
    notes += history_notes
    over = sum(score > LIMIT for score in measured.values())
    summary = (
        f"ruff {version}, limit {LIMIT}: {len(measured)} functions in {' and '.join(s + '/' for s in SCOPE)}, "
        f"{over} over the limit; {BASELINE_NAME} lists {len(baseline.functions)}"
    )
    for note in notes:
        print(note)
    if problems:
        print(f"complexity FAILED - {summary}", file=sys.stderr)
        for line in problems:
            print(line, file=sys.stderr)
        print(_TODO, file=sys.stderr)
        return 1
    print(f"complexity OK - {summary}, none above its baseline score")
    return 0


def cmd_update(root: Path) -> int:
    path = root / BASELINE_NAME
    baseline = load_baseline(path)
    version, _files, measured = _measure_all(root)
    if baseline.ruff != version:
        for line in _ruler_lines(baseline.ruff, version):
            print(line, file=sys.stderr)
        print("--update does not compare scores across Ruff versions.", file=sys.stderr)
        return 1
    found = compare(measured, baseline.functions)
    functions = dict(baseline.functions)
    # A name with a NEW or RAISED definition keeps its entries as they are, so
    # that once the code is fixed its renumbering still has them to fit under.
    blocked = {name_of(k) for k, _ in found.new} | {name_of(k) for k, *_ in found.raised}
    held = sorted(
        k
        for k in [
            *(f[0] for f in found.fell),
            *(f[0] for f in found.low),
            *(f[0] for f in found.gone),
        ]
        if name_of(k) in blocked
    )
    for key, _was, now in found.fell:
        if key not in held:
            functions[key] = now
    for key in [f[0] for f in found.low] + [f[0] for f in found.gone]:
        if key not in held:
            del functions[key]
    for _name, listed, now in found.renumbered:
        for key in listed:
            del functions[key]
        functions.update(now)
    # A renumbering is one line per name: per key it would read as an addition or a raise.
    moved = {name for name, _, _ in found.renumbered}
    written = diff_lines(
        {k: v for k, v in baseline.functions.items() if name_of(k) not in moved},
        {k: v for k, v in functions.items() if name_of(k) not in moved},
    )
    written += [f"  {name}  {_renumbering(listed, now)}" for name, listed, now in found.renumbered]
    if written:
        write_baseline(path, version, functions)
        print(f"{BASELINE_NAME} updated ({len(written)} entries):")
        for line in written:
            print(line)
    else:
        print(f"{BASELINE_NAME} unchanged: no score fell")
    left = report_lines(Findings(new=found.new, raised=found.raised))
    if left:
        print("not written (--update never adds an entry or raises a score):", file=sys.stderr)
        for line in left:
            print(line, file=sys.stderr)
        for key in held:
            print(
                f"HELD    {key}  left as listed until the NEW or RAISED definition of its name is fixed",
                file=sys.stderr,
            )
        if found.gone:
            print(
                "A GONE entry dropped above may be a rename of a NEW function: restore "
                f"{BASELINE_NAME} and use --rename OLD NEW instead.",
                file=sys.stderr,
            )
        print(_TODO, file=sys.stderr)
        return 1
    return 0


def cmd_rename(root: Path, old: str, new: str) -> int:
    path = root / BASELINE_NAME
    baseline = load_baseline(path)
    version, _files, measured = _measure_all(root)
    refusal = None
    if baseline.ruff != version:
        refusal = f"the baseline was measured with ruff {baseline.ruff}, this is ruff {version}"
    elif old not in baseline.functions:
        refusal = f"{old} is not in {BASELINE_NAME}"
    elif old in measured:
        refusal = f"{old} still exists (score {measured[old]}); a rename leaves nothing under the old identity"
        if name_of(old) == name_of(new):
            refusal += (
                "; same-named definitions that were reordered or gained one above are renumbered, "
                "which --update writes"
            )
    elif new in baseline.functions:
        refusal = f"{new} is already in {BASELINE_NAME}"
    elif new not in measured:
        refusal = f"no function has the identity {new}"
    elif measured[new] <= LIMIT:
        refusal = f"{new} scores {measured[new]}, within the limit: run --update to drop {old}"
    elif measured[new] > baseline.functions[old]:
        refusal = f"{new} scores {measured[new]}, above {old}'s {baseline.functions[old]}: a rename does not raise"
    if refusal:
        print(f"--rename refused: {refusal}", file=sys.stderr)
        return 1
    functions = dict(baseline.functions)
    del functions[old]
    functions[new] = measured[new]
    write_baseline(path, version, functions)
    print(f"{BASELINE_NAME}: {old} ({baseline.functions[old]}) -> {new} ({measured[new]})")
    return 0


def cmd_init(root: Path) -> int:
    path = root / BASELINE_NAME
    version, _files, measured = _measure_all(root)
    functions = {key: score for key, score in measured.items() if score > LIMIT}
    old = load_baseline(path) if path.is_file() else None
    write_baseline(path, version, functions)
    print(f"{BASELINE_NAME} written: ruff {version}, {len(functions)} functions over {LIMIT}")
    if old is not None:
        changes = diff_lines(old.functions, functions)
        ruler = "" if old.ruff == version else f" (ruff {old.ruff} -> {version})"
        print(f"against the baseline it replaced{ruler}: {len(changes)} changed")
        for line in changes:
            print(line)
    return 0


def cmd_trend(root: Path, radon_json: Path | None) -> int:
    if radon_json is None:
        report = _radon_report(root)
    else:
        try:
            raw = radon_json.read_bytes()
        except OSError as exc:
            raise MeasureError(f"--radon-json {radon_json} could not be read: {exc}") from exc
        report = _radon_json(raw, str(radon_json))
    trend = radon_trend(report)
    print(f"{RADON} cc -j {' '.join(SCOPE)} (a trend value, not a gate):")
    count, over20, over50 = trend["once"]
    print(
        f"  the trend value, each function, method and closure once (the owner's decision of "
        f"2026-11-09): {count}; CC > 20: {over20}; CC > 50: {over50}"
    )
    count, over20, over50 = trend["docs05"]
    print(
        f"  the 2026-09-19 way, which counts each method twice (radon's recursive count; for "
        f"comparison, not the trend value): {count}; CC > 20: {over20}; CC > 50: {over50}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="the CI gate")
    mode.add_argument(
        "--update", action="store_true", help="lower the baseline (never raises or adds)"
    )
    mode.add_argument(
        "--rename", nargs=2, metavar=("OLD", "NEW"), help="carry an entry to a renamed function"
    )
    mode.add_argument(
        "--init",
        action="store_true",
        help="write the baseline from the tree (first time, Ruff upgrade)",
    )
    mode.add_argument(
        "--trend", action="store_true", help="print the per-release radon trend value"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="the repository to measure")
    parser.add_argument(
        "--base",
        default="origin/main",
        help="history check: compare with the merge base of HEAD and this ref",
    )
    parser.add_argument(
        "--require-base",
        action="store_true",
        help="history check: fail when the merge base cannot be found",
    )
    parser.add_argument(
        "--github-event",
        action="store_true",
        help="history check in GitHub Actions: a push to main compares with the commit it replaced",
    )
    parser.add_argument(
        "--radon-json",
        type=Path,
        help="--trend: read this radon cc -j output instead of running radon",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            return cmd_check(root, args.base, args.require_base, args.github_event)
        if args.update:
            return cmd_update(root)
        if args.rename:
            return cmd_rename(root, *args.rename)
        if args.init:
            return cmd_init(root)
        return cmd_trend(root, args.radon_json)
    except MeasureError as exc:
        print(f"complexity: cannot measure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
