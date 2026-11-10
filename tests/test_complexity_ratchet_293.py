"""#293: the Ruff C901 complexity ratchet (ADR-0300).

The owner's decision of 2026-09-19: a function not in ``complexity-baseline.json``
may score at most 20 under Ruff ``C901``; a listed one may not rise above its
score; a score that falls lowers the baseline; no ``# noqa: C901``.

CI runs this as its own step on every leg:

    uv run python scripts/check_complexity.py --check --require-base --github-event

so this file does not run the gate on the real tree. It pins the step, the
committed baseline's form, and - on small trees in ``tmp_path``, measured by the
real ruff - each way the ratchet must fail, pass, lower itself and refuse to rise.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PureWindowsPath
from types import ModuleType

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_complexity.py"


def _load() -> ModuleType:
    # Registered before exec_module: the script defines dataclasses, which look
    # their own module up in sys.modules.
    spec = importlib.util.spec_from_file_location("_check_complexity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load()

MOD = "packages/pkg/mod.py"


def _fn(name: str, score: int, indent: str = "", *, args: str = "x", head: str = "def") -> str:
    """A function Ruff scores ``score``: one ``if`` per point above 1."""
    inner = indent + "    "
    lines = [f"{indent}{head} {name}({args}):"]
    for i in range(score - 1):
        lines += [f"{inner}if x == {i}:", f"{inner}    return {i}"]
    lines.append(f"{inner}return -1")
    return "\n".join(lines) + "\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def _tree(tmp_path: Path, files: dict[str, str | bytes], branch: str = "main") -> Path:
    """A git repository with ``files`` added (not committed): the gate measures tracked files."""
    root = tmp_path / "repo"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    _git(root, "init", "-q", "-b", branch)
    _git(root, "add", "-A")
    return root


@pytest.fixture(scope="module")
def ruff(tmp_path_factory: pytest.TempPathFactory) -> str:
    return gate.ruff_version(tmp_path_factory.mktemp("ruff"))


def _baseline(root: Path, ruff: str, functions: dict[str, int]) -> Path:
    path = root / gate.BASELINE_NAME
    gate.write_baseline(path, ruff, functions)
    return path


def _entries(root: Path) -> dict[str, int]:
    return json.loads((root / gate.BASELINE_NAME).read_text(encoding="utf-8"))["functions"]


def _run(root: Path, *args: str, github: dict[str, str] | None = None) -> tuple[int, str]:
    """The script in a subprocess, with no ``GITHUB_*`` variable but those in ``github``.

    GitHub Actions sets ``GITHUB_ACTIONS`` and ``GITHUB_STEP_SUMMARY`` for every
    step, the pytest step included: inherited, they would make the script
    annotate and append to that step's real job summary.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env.update(github or {})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _measured(root: Path) -> dict[str, int]:
    files = gate.tracked_sources(root)
    return gate.measure(root, files, gate.run_ruff(root, files))


# === the policy, CI and the committed baseline ==================================


def test_the_limit_is_the_owners_twenty() -> None:
    assert gate.LIMIT == 20
    assert gate.SCOPE == ("packages", "src")


def test_ci_runs_the_ratchet_on_every_leg_before_the_tests() -> None:
    """Every leg of the test job, so the Windows legs normalise real Windows paths."""
    job = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"][
        "test"
    ]
    names = [step.get("name") for step in job["steps"]]
    step = next(s for s in job["steps"] if "check_complexity.py" in str(s.get("run", "")))
    assert (
        step["run"]
        == "uv run python scripts/check_complexity.py --check --require-base --github-event"
    )
    assert "continue-on-error" not in step and "if" not in step
    index = names.index(step["name"])
    assert (
        names.index("Sync workspace (all members + dev group)")
        < index
        < names.index("Test gate (pytest)")
    )
    # The history check needs the merge base with origin/main in the clone.
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0


def test_the_committed_baseline_is_what_the_script_writes_for_the_locked_ruff() -> None:
    path = REPO / gate.BASELINE_NAME
    text = path.read_text(encoding="utf-8")
    baseline = gate.parse_baseline(text, gate.BASELINE_NAME)
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "ruff"]
    assert locked == [baseline.ruff], "uv.lock moved ruff: run --init in that change (ADR-0300)"
    assert baseline.functions and all(score > gate.LIMIT for score in baseline.functions.values())
    for key in baseline.functions:
        rel = key.split("::", 1)[0]
        assert "\\" not in rel and rel.split("/", 1)[0] in gate.SCOPE, key
    expected = json.dumps(
        {
            "about": gate._ABOUT,
            "ruff": baseline.ruff,
            "functions": dict(sorted(baseline.functions.items())),
        },
        indent=2,
        ensure_ascii=True,
    )
    assert text.replace("\r\n", "\n") == expected + "\n", "edited by hand: write it with the script"


# === the ratchet ================================================================


def test_a_new_function_over_twenty_fails_and_one_at_twenty_passes(
    tmp_path: Path, ruff: str
) -> None:
    root = _tree(tmp_path, {MOD: _fn("fine", 20) + _fn("busy", 21)})
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"NEW     {MOD}::busy  scores 21 (limit 20)" in out
    assert "::fine" not in out
    assert "Never add or" in out and "`# noqa: C901` is refused" in out


def test_a_raised_score_fails_with_old_and_new(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 26)})
    _baseline(root, ruff, {f"{MOD}::busy": 25})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"RAISED  {MOD}::busy  25 -> 26" in out


def test_a_score_at_its_baseline_passes(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 25)})
    _baseline(root, ruff, {f"{MOD}::busy": 25})
    code, out = _run(root, "--check")
    assert code == 0, out
    assert "complexity OK" in out and "1 over the limit" in out


def test_a_lowered_score_fails_until_update_lowers_the_baseline(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 23)})
    _baseline(root, ruff, {f"{MOD}::busy": 25})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"STALE   {MOD}::busy  fell 25 -> 23" in out and "--update" in out

    code, out = _run(root, "--update")
    assert code == 0, out
    assert _entries(root) == {f"{MOD}::busy": 23}
    assert _run(root, "--check")[0] == 0
    # The lower score is the ceiling now: climbing back to 25 fails.
    (root / MOD).write_text(_fn("busy", 25), encoding="utf-8")
    code, out = _run(root, "--check")
    assert code == 1 and f"RAISED  {MOD}::busy  23 -> 25" in out, out


def test_update_drops_entries_within_the_limit_and_entries_gone(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("calm", 20) + _fn("busy", 30)})
    _baseline(root, ruff, {f"{MOD}::calm": 25, f"{MOD}::removed": 40, f"{MOD}::busy": 30})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"STALE   {MOD}::calm  25 -> 20, within the limit now" in out
    assert f"GONE    {MOD}::removed  (baseline 40): no function has this identity now" in out

    code, out = _run(root, "--update")
    assert code == 0, out
    assert _entries(root) == {f"{MOD}::busy": 30}
    assert f"dropped {MOD}::calm  25 -> -" in out and f"dropped {MOD}::removed  40 -> -" in out


def test_update_never_raises_a_score_or_adds_an_entry(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 27) + _fn("fresh", 22) + _fn("lower", 24)})
    path = _baseline(root, ruff, {f"{MOD}::busy": 25, f"{MOD}::lower": 26})
    code, out = _run(root, "--update")
    assert code == 1, out
    assert _entries(root) == {f"{MOD}::busy": 25, f"{MOD}::lower": 24}
    assert "not written (--update never adds an entry or raises a score)" in out
    assert f"RAISED  {MOD}::busy  25 -> 27" in out and f"NEW     {MOD}::fresh  scores 22" in out

    # Nothing to lower: the file is left byte for byte.
    before = path.read_bytes()
    assert _run(root, "--update")[0] == 1
    assert path.read_bytes() == before


def test_a_baseline_from_another_ruff_fails_until_init(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 25)})
    _baseline(root, "0.0.1", {f"{MOD}::busy": 25})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"was measured with ruff 0.0.1; this is ruff {ruff}" in out and "--init" in out
    assert _run(root, "--update")[0] == 1
    code, out = _run(root, "--rename", f"{MOD}::busy", f"{MOD}::other")
    assert code == 1 and "measured with ruff 0.0.1" in out, out
    code, out = _run(root, "--init")
    assert code == 0 and f"(ruff 0.0.1 -> {ruff}): 0 changed" in out, out
    assert _run(root, "--check")[0] == 0


def test_init_lists_every_function_over_twenty_and_what_changed(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("a", 21) + _fn("b", 20) + _fn("c", 30)})
    code, out = _run(root, "--init")
    assert code == 0, out
    assert "2 functions over 20" in out
    assert _entries(root) == {f"{MOD}::a": 21, f"{MOD}::c": 30}
    (root / MOD).write_text(_fn("a", 22) + _fn("c", 29), encoding="utf-8")
    code, out = _run(root, "--init")
    assert code == 0 and "2 changed" in out, out
    assert f"raised  {MOD}::a  21 -> 22" in out and f"lowered {MOD}::c  30 -> 29" in out


def test_a_missing_or_malformed_baseline_cannot_be_measured_against(
    tmp_path: Path, ruff: str
) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 21)})
    code, out = _run(root, "--check")
    assert code == 2 and "complexity-baseline.json is missing" in out and "--init" in out, out
    (root / gate.BASELINE_NAME).write_text(
        json.dumps({"ruff": ruff, "functions": {f"{MOD}::busy": 20}}), encoding="utf-8"
    )
    code, out = _run(root, "--check")
    assert code == 2 and "lists only functions over 20" in out, out
    (root / gate.BASELINE_NAME).write_text(json.dumps({"functions": {}}), encoding="utf-8")
    assert _run(root, "--check")[0] == 2


def test_unreadable_inputs_cannot_be_measured_against(tmp_path: Path, ruff: str) -> None:
    """A missing radon report and a baseline that is not UTF-8 exit 2, not with a traceback."""
    root = _tree(tmp_path, {MOD: _fn("busy", 21)})
    code, out = _run(root, "--trend", "--radon-json", str(tmp_path / "missing.json"))
    assert code == 2 and "missing.json could not be read" in out and "Traceback" not in out, out
    (tmp_path / "garbage.json").write_bytes(b"\xff not json")
    code, out = _run(root, "--trend", "--radon-json", str(tmp_path / "garbage.json"))
    assert code == 2 and "is not radon's JSON" in out and "Traceback" not in out, out
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    code, out = _run(root, "--trend", "--radon-json", str(tmp_path / "list.json"))
    assert code == 2 and "not an object of files" in out and "Traceback" not in out, out
    (root / gate.BASELINE_NAME).write_bytes(b'{"ruff": "\xff", "functions": {}}')
    code, out = _run(root, "--check")
    assert code == 2 and "could not be read as UTF-8" in out and "Traceback" not in out, out


def test_a_baseline_key_must_name_a_path_and_a_function() -> None:
    with pytest.raises(gate.MeasureError, match="is not `<path>::<qualname>`"):
        gate.parse_baseline('{"ruff": "0.15.13", "functions": {"busy": 25}}', "b.json")


def test_the_baseline_is_written_sorted(tmp_path: Path) -> None:
    path = tmp_path / gate.BASELINE_NAME
    gate.write_baseline(path, "0.15.13", {"z.py::b": 22, "a.py::a": 21, "m.py::c": 23})
    written = json.loads(path.read_text(encoding="utf-8"))["functions"]
    assert list(written) == ["a.py::a", "m.py::c", "z.py::b"]


def test_a_file_ruff_cannot_parse_stops_the_measurement(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 21), "packages/pkg/broken.py": "def f(:\n"})
    _baseline(root, ruff, {f"{MOD}::busy": 21})
    code, out = _run(root, "--check")
    assert code == 2 and "ruff could not measure" in out and "broken.py" in out, out


# === what is measured ===========================================================


#: Directories ruff's own file discovery skips even under ``--isolated``.
_RUFF_SKIPS = ["dist", "venv", "site-packages", "_build", "node_modules", ".venv", "build"]


def test_every_tracked_file_is_measured_wherever_it_is(tmp_path: Path, ruff: str) -> None:
    """Ruff's default excludes and ``.gitignore`` do not apply: the files are named to it."""
    files: dict[str, str | bytes] = {MOD: _fn("calm", 3), ".gitignore": "gen_*.py\n"}
    files |= {f"packages/pkg/{d}/x.py": _fn("busy", 22) for d in _RUFF_SKIPS}
    files["src/stubs/typed.pyi"] = _fn("typed", 23)
    files["packages/pkg/notes.txt"] = _fn("not_python", 30)
    root = _tree(tmp_path, files)
    (root / "packages/pkg/gen_x.py").write_text(_fn("made", 22), encoding="utf-8")
    _git(root, "add", "-f", "packages/pkg/gen_x.py")
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 1, out
    for d in _RUFF_SKIPS:
        assert f"NEW     packages/pkg/{d}/x.py::busy  scores 22" in out, d
    assert "NEW     packages/pkg/gen_x.py::made  scores 22" in out
    assert "NEW     src/stubs/typed.pyi::typed  scores 23" in out
    assert "not_python" not in out
    assert f"{len(_RUFF_SKIPS) + 3} functions" in out


def test_an_untracked_file_is_not_measured_and_is_named(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("calm", 3)})
    (root / "packages/pkg/scratch.py").write_text(_fn("busy", 22), encoding="utf-8")
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 0, out
    assert "UNTRACKED 1 .py/.pyi file(s)" in out and "packages/pkg/scratch.py" in out
    _git(root, "add", "packages/pkg/scratch.py")
    code, out = _run(root, "--check")
    assert code == 1 and "NEW     packages/pkg/scratch.py::busy  scores 22" in out, out


def test_a_file_ruff_does_not_measure_stops_the_gate(tmp_path: Path, ruff: str) -> None:
    """Ruff skips a file that is not UTF-8 with a warning on stderr and exit 0."""
    latin = b"# -*- coding: latin-1 -*-\nS = '\xe9'\n" + _fn("busy", 22).encode()
    root = _tree(tmp_path, {MOD: _fn("calm", 3), "packages/pkg/latin.py": latin})
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 2, out
    assert "ruff did not measure everything it was given" in out and "latin.py" in out


def test_outside_a_git_checkout_the_gate_cannot_measure(tmp_path: Path, ruff: str) -> None:
    root = tmp_path / "plain"
    (root / "packages/pkg").mkdir(parents=True)
    (root / MOD).write_text(_fn("busy", 21), encoding="utf-8")
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 2 and "`git ls-files` failed" in out, out


def test_a_def_ruff_did_not_report_stops_the_measurement(tmp_path: Path) -> None:
    root = _tree(
        tmp_path, {MOD: _fn("one", 3) + _fn("two", 4), "packages/pkg/other.py": _fn("x", 2)}
    )
    files = gate.tracked_sources(root)
    diagnostics = gate.run_ruff(root, files)
    assert len(diagnostics) == 3
    two = [d for d in diagnostics if "`two`" in d["message"]]
    with pytest.raises(
        gate.MeasureError, match=r"did not report 1 of the 2 definitions in packages/pkg/mod\.py"
    ):
        gate.measure(root, files, [d for d in diagnostics if d not in two])
    other = [d for d in diagnostics if d["filename"].endswith("other.py")]
    with pytest.raises(
        gate.MeasureError, match=r"did not report 1 of the 1 definitions in packages/pkg/other\.py"
    ):
        gate.measure(root, files, [d for d in diagnostics if d not in other])
    with pytest.raises(gate.MeasureError, match="twice"):
        gate.measure(root, files, diagnostics + two)


def test_files_go_to_ruff_in_batches_and_all_are_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows caps a command line at 32,767 characters; the real tree's list is 21,966."""
    names = [f"packages/pkg/m{i}.py" for i in range(7)]
    root = _tree(tmp_path, {name: _fn(f"f{i}", 2) for i, name in enumerate(names)})
    monkeypatch.setattr(gate, "_ARGV_BUDGET", 50)
    batches = gate._batches(sorted(names))
    assert len(batches) > 2 and sum(batches, []) == sorted(names)
    assert all(sum(len(n) + 3 for n in b) <= 50 for b in batches)
    assert _measured(root) == {f"{name}::f{i}": 2 for i, name in enumerate(names)}


# === identity ===================================================================


def test_nested_functions_keep_ruffs_number_and_their_own_identity(tmp_path: Path) -> None:
    outer = (
        "def outer(x):\n"
        + _fn("inner", 15, "    ")
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(10))
        + "    return inner(x)\n"
    )
    klass = "class Box:\n" + _fn("method", 21, "    ", args="self, x")
    local_class = (
        "def factory(x):\n    class Local:\n"
        + _fn("go", 21, "        ", args="self, x")
        + "    return Local\n"
    )
    root = _tree(tmp_path, {MOD: outer + klass + local_class + _fn("worker", 3, head="async def")})
    scores = _measured(root)
    # Ruff adds the nested function's whole score to its parent: 1 + 10 ifs + 15.
    assert scores[f"{MOD}::outer"] == 26
    assert scores[f"{MOD}::outer.<locals>.inner"] == 15
    assert scores[f"{MOD}::Box.method"] == 21
    assert scores[f"{MOD}::factory.<locals>.Local.go"] == 21
    assert scores[f"{MOD}::factory"] == 22
    assert scores[f"{MOD}::worker"] == 3


def test_duplicate_qualnames_are_numbered_in_source_order(tmp_path: Path) -> None:
    conditional = (
        "import sys\nif sys.platform == 'win32':\n"
        + _fn("pick", 22, "    ")
        + "else:\n"
        + _fn("pick", 23, "    ")
    )
    overloads = (
        "from typing import overload\n"
        "@overload\ndef parse(x: int) -> int: ...\n"
        "@overload\ndef parse(x: str) -> str: ...\n" + _fn("parse", 24)
    )
    prop = (
        "class P:\n    @property\n"
        + _fn("v", 21, "    ", args="self, x=0")
        + "    @v.setter\n"
        + _fn("v", 22, "    ", args="self, x")
    )
    same_nested = (
        "def host(x):\n" + _fn("cb", 21, "    ") + _fn("cb", 25, "    ") + "    return cb\n"
    )
    root = _tree(tmp_path, {MOD: conditional + overloads + prop + same_nested})
    scores = _measured(root)
    assert scores[f"{MOD}::pick"] == 22 and scores[f"{MOD}::pick#2"] == 23
    assert scores[f"{MOD}::parse"] == 24
    assert scores[f"{MOD}::parse#overload1"] == 1 and scores[f"{MOD}::parse#overload2"] == 1
    assert scores[f"{MOD}::P.v"] == 21 and scores[f"{MOD}::P.v#2"] == 22
    assert scores[f"{MOD}::host.<locals>.cb"] == 21 and scores[f"{MOD}::host.<locals>.cb#2"] == 25

    # A third overload above the implementation does not renumber it.
    (root / MOD).write_text(
        conditional
        + overloads.replace(
            "@overload\ndef parse(x: str)",
            "@overload\ndef parse(x: bytes) -> bytes: ...\n@overload\ndef parse(x: str)",
        ),
        encoding="utf-8",
    )
    again = _measured(root)
    assert again[f"{MOD}::parse"] == 24 and again[f"{MOD}::parse#overload3"] == 1


def test_a_name_on_a_continuation_line_is_still_its_def(tmp_path: Path) -> None:
    # Ruff reports where the name is; the AST knows where ``def`` is. A backslash
    # between them puts the two on different lines.
    source = _fn("late", 21).replace("def late(x):", "def \\\n    late(x):", 1)
    root = _tree(tmp_path, {MOD: source})
    assert _measured(root) == {f"{MOD}::late": 21}


def test_a_nested_def_with_its_parents_name_as_its_first_statement(tmp_path: Path) -> None:
    """The child starts on the line where the parent's header ends; ruff means the child there."""
    plain = "def wrap(x):\n" + _fn("wrap", 3, "    ") + "    return wrap(x)\n"
    decorated = (
        "import functools\n\n\ndef deco(f):\n    @functools.wraps(f)\n"
        + _fn("deco", 4, "    ")
        + "    return deco\n"
    )
    root = _tree(tmp_path, {MOD: plain, "packages/pkg/dec.py": decorated})
    # The parent's score holds the child's, as everywhere (1 + 3, 1 + 4).
    assert _measured(root) == {
        f"{MOD}::wrap": 4,
        f"{MOD}::wrap.<locals>.wrap": 3,
        "packages/pkg/dec.py::deco": 5,
        "packages/pkg/dec.py::deco.<locals>.deco": 4,
    }


def test_an_ambiguous_definition_stops_the_measurement() -> None:
    """Valid source never gets here; a ruff that reported elsewhere would, and must not pick one."""
    a = gate._Def("f", "f", 1, 5, 0, False)
    b = gate._Def("g.<locals>.f", "f", 2, 6, 4, False)
    assert gate.the_def([a, b], "f", 1, MOD) is a
    assert gate.the_def([a, b], "f", 2, MOD) is b
    with pytest.raises(gate.MeasureError, match="2 definitions match it"):
        gate.the_def([a, b], "f", 3, MOD)
    with pytest.raises(gate.MeasureError, match="0 definitions match it"):
        gate.the_def([a, b], "f", 9, MOD)


def test_an_identity_keeps_the_case_of_its_path(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"packages/Pkg/Mod.py": _fn("Busy", 21)})
    assert _measured(root) == {"packages/Pkg/Mod.py::Busy": 21}
    rel = gate.relative_key_path(
        r"C:\Work\Repo\packages\Pkg\Mod.py", r"c:\work\repo", PureWindowsPath
    )
    assert rel == "packages/Pkg/Mod.py"


def test_a_windows_path_becomes_the_same_posix_identity() -> None:
    rel = gate.relative_key_path(
        r"C:\Users\runneradmin\Work\Repo\packages\pkg\mod.py",
        r"c:\users\RUNNERADMIN\work\repo",
        PureWindowsPath,
    )
    assert rel == MOD
    assert gate.relative_key_path("/w/repo/packages/pkg/mod.py", "/w/repo") == MOD
    with pytest.raises(gate.MeasureError, match="not under"):
        gate.relative_key_path(r"D:\elsewhere\mod.py", r"C:\repo", PureWindowsPath)


def test_identities_do_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 21)})
    monkeypatch.chdir(tmp_path)
    assert _measured(root) == {f"{MOD}::busy": 21}
    monkeypatch.chdir(root / "packages")
    assert _measured(root) == {f"{MOD}::busy": 21}


# === no noqa, no ignore =========================================================


@pytest.mark.parametrize(
    "comment",
    [
        "# noqa: C901",
        "#noqa:C901",
        "# NOQA: E501, c901",
        "# noqa: C90",
        "# noqa: C9",
        "# noqa: C",
        "# ruff: noqa: C901",
    ],
)
def test_a_noqa_naming_c901_is_refused_and_does_not_hide_the_function(
    tmp_path: Path, ruff: str, comment: str
) -> None:
    source = _fn("busy", 21).replace("def busy(x):", f"def busy(x):  {comment}", 1)
    if comment.startswith("# ruff:"):
        source = f"{comment}\n" + _fn("busy", 21)
    root = _tree(tmp_path, {MOD: source})
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"NOQA    {MOD} line 1: {comment}" in out
    assert f"NEW     {MOD}::busy  scores 21" in out


def test_noqa_for_other_codes_and_noqa_in_strings_pass(tmp_path: Path, ruff: str) -> None:
    source = 'TEXT = "# noqa: C901"\n' + _fn("calm", 3).replace(
        "def calm(x):", "def calm(x):  # noqa: E501, C4", 1
    )
    root = _tree(tmp_path, {MOD: source})
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 0, out


@pytest.mark.parametrize(
    ("rel", "toml"),
    [
        ("pyproject.toml", '[tool.ruff.lint.per-file-ignores]\n"packages/*" = ["C901"]\n'),
        ("pyproject.toml", '[tool.ruff]\nextend-ignore = ["C90"]\n'),
        ("packages/pkg/ruff.toml", '[lint]\nignore = ["E501", "C901"]\n'),
        ("packages/.ruff.toml", 'per-file-ignores = { "x.py" = "C9" }\n'),
        ("pyproject.toml", '[tool.ruff.lint]\nignore = ["C"]\n'),
        ("ruff.toml", '[lint]\nextend-ignore = ["ALL"]\n'),
    ],
)
def test_a_ruff_ignore_naming_c901_is_refused_and_does_not_hide_the_function(
    tmp_path: Path, ruff: str, rel: str, toml: str
) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 21), rel: toml})
    _baseline(root, ruff, {})
    code, out = _run(root, "--check")
    assert code == 1, out
    flagged = out.split(f"CONFIG  {rel} [", 1)[1].splitlines()[0]
    assert flagged.endswith(("C901", "C90", "C9", ": C", ": ALL")), flagged
    assert f"NEW     {MOD}::busy  scores 21" in out


# === renames ====================================================================


def test_a_rename_is_carried_by_rename_and_nothing_else(tmp_path: Path, ruff: str) -> None:
    other = "packages/pkg/moved.py"
    root = _tree(tmp_path, {other: _fn("busy", 28)})
    _baseline(root, ruff, {f"{MOD}::busy": 30})
    code, out = _run(root, "--check")
    assert code == 1, out
    assert f"NEW     {other}::busy  scores 28" in out
    assert (
        f"GONE    {MOD}::busy  (baseline 30): no function has this identity now; renamed? --rename {MOD}::busy {other}::busy"
        in out
    )

    assert _run(root, "--rename", f"{MOD}::busy", f"{other}::nothing")[0] == 1
    code, out = _run(root, "--rename", f"{MOD}::busy", f"{other}::busy")
    assert code == 0, out
    assert _entries(root) == {f"{other}::busy": 28}
    assert _run(root, "--check")[0] == 0


def test_rename_refuses_a_raise_an_old_name_still_there_and_a_function_within_the_limit(
    tmp_path: Path, ruff: str
) -> None:
    root = _tree(tmp_path, {MOD: _fn("kept", 25) + _fn("bigger", 31) + _fn("small", 12)})
    _baseline(root, ruff, {f"{MOD}::kept": 25, f"{MOD}::old": 30})
    before = (root / gate.BASELINE_NAME).read_bytes()
    for old, new, why in [
        (f"{MOD}::old", f"{MOD}::bigger", "a rename does not raise"),
        (f"{MOD}::kept", f"{MOD}::bigger", "still exists"),
        (f"{MOD}::old", f"{MOD}::small", "within the limit"),
        (f"{MOD}::old", f"{MOD}::kept", "already in"),
    ]:
        code, out = _run(root, "--rename", old, new)
        assert code == 1 and why in out, out
    assert (root / gate.BASELINE_NAME).read_bytes() == before


def test_rename_carries_an_equal_score(tmp_path: Path, ruff: str) -> None:
    """A pure move keeps its score: the most common rename."""
    root = _tree(tmp_path, {MOD: _fn("moved", 30)})
    _baseline(root, ruff, {f"{MOD}::old": 30})
    code, out = _run(root, "--rename", f"{MOD}::old", f"{MOD}::moved")
    assert code == 0, out
    assert _entries(root) == {f"{MOD}::moved": 30}


# === renumbered same-named definitions ==========================================

_PICK_25_3 = "import sys\nif sys.platform == 'win32':\n" + _fn("pick", 25, "    ")
_PICK_25_3 += "else:\n" + _fn("pick", 3, "    ")
_PICK_3_25 = "import sys\nif sys.platform != 'win32':\n" + _fn("pick", 3, "    ")
_PICK_3_25 += "else:\n" + _fn("pick", 25, "    ")


def _renumbered(tmp_path: Path, ruff: str, before: str, listed: dict[str, int]) -> Path:
    """``before`` with ``listed`` on ``main``, on a branch ``work``."""
    root = _tree(tmp_path, {MOD: before})
    _baseline(root, ruff, {f"{MOD}::{k}": v for k, v in listed.items()})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "work")
    return root


def _settles(root: Path, expected: dict[str, int]) -> None:
    """``--update`` writes ``expected`` and both checks then pass, the history check committed."""
    code, out = _run(root, "--update")
    assert code == 0, out
    assert _entries(root) == {f"{MOD}::{k}": v for k, v in expected.items()}
    # One line per renumbered name, never an "added" or "raised" key.
    assert f"  {MOD}::{next(iter(expected)).split('#')[0]}  renumbered: listed" in out, out
    assert "added" not in out and "raised" not in out, out
    assert _run(root, "--check")[0] == 0
    _git(root, "commit", "-q", "-am", "renumbered")
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0 and "nothing added or raised" in out, out


def test_reordered_same_named_definitions_are_renumbered_by_update(
    tmp_path: Path, ruff: str
) -> None:
    root = _renumbered(tmp_path, ruff, _PICK_25_3, {"pick": 25})
    (root / MOD).write_text(_PICK_3_25, encoding="utf-8")
    code, out = _run(root, "--check")
    assert code == 1, out
    assert (
        f"STALE   {MOD}::pick  same-named definitions renumbered: listed pick 25; now pick#2 25"
        in out
    )
    assert f"NEW     {MOD}" not in out
    code, out = _run(root, "--rename", f"{MOD}::pick", f"{MOD}::pick#2")
    assert code == 1 and "renumbered, which --update writes" in out, out
    _settles(root, {"pick#2": 25})


def test_two_listed_same_named_definitions_swapped_are_renumbered(
    tmp_path: Path, ruff: str
) -> None:
    first = "import sys\nif sys.platform == 'win32':\n" + _fn("pick", 30, "    ")
    first += "else:\n" + _fn("pick", 25, "    ")
    swapped = "import sys\nif sys.platform != 'win32':\n" + _fn("pick", 25, "    ")
    swapped += "else:\n" + _fn("pick", 30, "    ")
    root = _renumbered(tmp_path, ruff, first, {"pick": 30, "pick#2": 25})
    (root / MOD).write_text(swapped, encoding="utf-8")
    code, out = _run(root, "--check")
    assert code == 1 and f"RAISED  {MOD}" not in out, out
    assert "renumbered: listed pick 30, pick#2 25; now pick 25, pick#2 30" in out
    _settles(root, {"pick": 25, "pick#2": 30})


def test_a_stub_above_a_listed_function_renumbers_it(tmp_path: Path, ruff: str) -> None:
    root = _renumbered(tmp_path, ruff, _fn("load", 25), {"load": 25})
    stub = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    def load(x): ...\nelse:\n"
    (root / MOD).write_text(stub + _fn("load", 25, "    "), encoding="utf-8")
    code, out = _run(root, "--check")
    assert code == 1 and "renumbered: listed load 25; now load#2 25" in out, out
    _settles(root, {"load#2": 25})


def test_an_overload_above_a_listed_function_does_not_renumber_it(
    tmp_path: Path, ruff: str
) -> None:
    root = _renumbered(tmp_path, ruff, _fn("load", 25), {"load": 25})
    stub = "from typing import overload\n\n\n@overload\ndef load(x: int) -> int: ...\n"
    (root / MOD).write_text(stub + _fn("load", 25), encoding="utf-8")
    code, out = _run(root, "--check")
    assert code == 0, out


def test_the_renumbering_written_by_hand_passes_the_history_check(
    tmp_path: Path, ruff: str
) -> None:
    root = _renumbered(tmp_path, ruff, _fn("load", 25), {"load": 25})
    stub = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    def load(x): ...\nelse:\n"
    _commit(root, stub + _fn("load", 25, "    "), {f"{MOD}::load#2": 25}, ruff)
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0 and "nothing added or raised" in out, out


def test_an_addition_beside_a_listed_same_named_definition_is_named_as_added(
    tmp_path: Path, ruff: str
) -> None:
    """The entry that kept its own key and score is paired first, so the addition is the leftover."""
    two = "import sys\nif sys.platform == 'win32':\n" + _fn("pick", 2, "    ")
    two += "else:\n" + _fn("pick", 30, "    ")
    root = _renumbered(tmp_path, ruff, two, {"pick#2": 30})
    raised = "import sys\nif sys.platform == 'win32':\n" + _fn("pick", 25, "    ")
    raised += "else:\n" + _fn("pick", 30, "    ")
    _commit(root, raised, {f"{MOD}::pick": 25, f"{MOD}::pick#2": 30}, ruff)
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 1, out
    assert f"HISTORY {MOD}::pick  added to the baseline (25)" in out
    assert "raised in the baseline" not in out


def test_a_renumbering_may_not_raise_a_score_or_add_an_entry(tmp_path: Path, ruff: str) -> None:
    stub = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    def load(x): ...\nelse:\n"
    root = _renumbered(tmp_path, ruff, _fn("load", 25), {"load": 25})
    # Renumbered and raised: NEW, and --update keeps `load` listed until it is fixed.
    (root / MOD).write_text(stub + _fn("load", 26, "    "), encoding="utf-8")
    code, out = _run(root, "--check")
    assert code == 1 and f"NEW     {MOD}::load#2  scores 26" in out, out
    code, out = _run(root, "--update")
    assert code == 1 and f"HELD    {MOD}::load  left as listed" in out, out
    assert _entries(root) == {f"{MOD}::load": 25}
    (root / MOD).write_text(stub + _fn("load", 25, "    "), encoding="utf-8")
    assert _run(root, "--update")[0] == 0 and _entries(root) == {f"{MOD}::load#2": 25}
    # The raise written into the baseline by hand fails the history check.
    _commit(root, stub + _fn("load", 26, "    "), {f"{MOD}::load#2": 26}, ruff)
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 1 and f"HISTORY {MOD}::load#2  added to the baseline (26)" in out, out
    # A second same-named definition over the limit is a net entry, not a renumbering.
    _commit(root, _fn("load", 25) + _fn("load", 22), None, ruff)
    _baseline(root, ruff, {f"{MOD}::load": 25})
    code, out = _run(root, "--check")
    assert code == 1 and f"NEW     {MOD}::load#2  scores 22" in out, out
    _commit(
        root, _fn("load", 25) + _fn("load", 22), {f"{MOD}::load": 25, f"{MOD}::load#2": 22}, ruff
    )
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 1 and f"HISTORY {MOD}::load#2  added to the baseline (22)" in out, out


def test_update_holds_a_name_whose_definition_is_raised_until_it_is_fixed(
    tmp_path: Path, ruff: str
) -> None:
    """Reordered and one raised by 1: lowering the other entry would leave no passing command."""
    arms = "import sys\nif sys.platform == 'win32':\n{}else:\n{}"
    root = _renumbered(
        tmp_path,
        ruff,
        arms.format(_fn("f", 30, "    "), _fn("f", 25, "    ")),
        {"f": 30, "f#2": 25},
    )
    (root / MOD).write_text(arms.format(_fn("f", 26, "    "), _fn("f", 30, "    ")), "utf-8")
    code, out = _run(root, "--check")
    assert code == 1 and f"RAISED  {MOD}::f#2  25 -> 30" in out, out
    code, out = _run(root, "--update")
    assert code == 1 and f"HELD    {MOD}::f  left as listed" in out, out
    assert _entries(root) == {f"{MOD}::f": 30, f"{MOD}::f#2": 25}
    # The raise undone, the reorder alone is a renumbering.
    (root / MOD).write_text(arms.format(_fn("f", 25, "    "), _fn("f", 30, "    ")), "utf-8")
    _settles(root, {"f": 25, "f#2": 30})


def test_moving_a_listed_definition_out_from_before_a_same_named_one_takes_two_changes(
    tmp_path: Path, ruff: str
) -> None:
    """The documented exception (ADR-0300 Consequences): no command carries it in one change.

    Moving the listed ``pick`` out leaves its unlisted sibling wearing its
    identity, so ``--rename`` is refused and ``--update`` cannot add the moved
    one. The route: renumber first and merge, then move the last-numbered one.
    """
    win = "packages/pkg/_win.py"
    root = _renumbered(tmp_path, ruff, _PICK_25_3, {"pick": 25})
    stay = _fn("pick", 3, "    ")
    (root / MOD).write_text(
        f"import sys\nif sys.platform == 'win32':\n    from pkg._win import pick\nelse:\n{stay}",
        encoding="utf-8",
    )
    (root / win).write_text(_fn("pick", 25), encoding="utf-8")
    _git(root, "add", "-A")
    code, out = _run(root, "--rename", f"{MOD}::pick", f"{win}::pick")
    assert code == 1 and f"{MOD}::pick still exists (score 3)" in out, out
    code, out = _run(root, "--update")
    assert code == 1 and f"NEW     {win}::pick  scores 25" in out, out
    _git(root, "reset", "-q", "--hard")
    # First change: reorder, so that the listed one is last-numbered; merged.
    (root / MOD).write_text(_PICK_3_25, encoding="utf-8")
    _settles(root, {"pick#2": 25})
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--ff-only", "work")
    _git(root, "checkout", "-q", "-b", "move")
    # Second change: move it; nothing after it is renumbered, so it is a rename.
    (root / MOD).write_text(
        f"import sys\nif sys.platform != 'win32':\n{stay}else:\n    from pkg._win import pick\n",
        encoding="utf-8",
    )
    (root / win).write_text(_fn("pick", 25), encoding="utf-8")
    _git(root, "add", "-A")
    code, out = _run(root, "--rename", f"{MOD}::pick#2", f"{win}::pick")
    assert code == 0, out
    _git(root, "commit", "-q", "-m", "moved")
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0 and "nothing added or raised" in out, out
    assert _entries(root) == {f"{win}::pick": 25}


# === the history check ==========================================================


def _history(tmp_path: Path, ruff: str, base_ruff: str | None = None) -> Path:
    """A repo whose ``main`` has ``busy`` at 25 in code and baseline, on a branch ``work``."""
    root = _tree(tmp_path, {MOD: _fn("busy", 25) + _fn("old", 30)})
    _baseline(root, base_ruff or ruff, {f"{MOD}::busy": 25, f"{MOD}::old": 30})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "work")
    return root


def _commit(root: Path, source: str, functions: dict[str, int] | None, ruff: str) -> None:
    (root / MOD).write_text(source, encoding="utf-8")
    if functions is not None:
        _baseline(root, ruff, functions)
    _git(root, "commit", "-q", "-am", "change")


def test_a_baseline_raised_by_hand_fails_against_the_merge_base(tmp_path: Path, ruff: str) -> None:
    root = _history(tmp_path, ruff)
    _commit(root, _fn("busy", 27) + _fn("old", 30), {f"{MOD}::busy": 27, f"{MOD}::old": 30}, ruff)
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 1, out
    assert f"HISTORY {MOD}::busy  raised in the baseline: 25 at" in out and "-> 27" in out
    assert f"RAISED  {MOD}" not in out  # the code matches the edited baseline; only history sees it


def test_an_entry_added_by_hand_fails_against_the_merge_base(tmp_path: Path, ruff: str) -> None:
    root = _history(tmp_path, ruff)
    _commit(
        root,
        _fn("busy", 25) + _fn("old", 30) + _fn("fresh", 22),
        {f"{MOD}::busy": 25, f"{MOD}::old": 30, f"{MOD}::fresh": 22},
        ruff,
    )
    code, out = _run(root, "--check", "--base", "main")
    assert code == 1, out
    assert f"HISTORY {MOD}::fresh  added to the baseline (22) and not a rename" in out


def test_a_rename_and_a_lowered_score_pass_against_the_merge_base(
    tmp_path: Path, ruff: str
) -> None:
    root = _history(tmp_path, ruff)
    (root / MOD).write_text(_fn("busy", 23) + _fn("renamed", 29), encoding="utf-8")
    assert _run(root, "--rename", f"{MOD}::old", f"{MOD}::renamed")[0] == 0
    assert _run(root, "--update")[0] == 0
    _git(root, "commit", "-q", "-am", "rename and lower")
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0, out
    assert "nothing added or raised" in out
    assert _entries(root) == {f"{MOD}::busy": 23, f"{MOD}::renamed": 29}


def test_a_rename_may_not_carry_a_higher_score_against_the_merge_base(
    tmp_path: Path, ruff: str
) -> None:
    # ``old`` (30) is gone and ``renamed`` scores 31: written in by hand, since
    # --rename refuses it, the history check still does.
    root = _history(tmp_path, ruff)
    _commit(
        root,
        _fn("busy", 25) + _fn("renamed", 31),
        {f"{MOD}::busy": 25, f"{MOD}::renamed": 31},
        ruff,
    )
    code, out = _run(root, "--check", "--base", "main")
    assert code == 1 and f"HISTORY {MOD}::renamed  added to the baseline (31)" in out, out


def test_an_addition_paired_with_a_function_still_in_the_tree_is_not_a_rename(
    tmp_path: Path, ruff: str
) -> None:
    # ``old`` dropped to 18 and left the baseline; it did not vanish, so its 30
    # cannot carry a new function.
    root = _history(tmp_path, ruff)
    _commit(
        root,
        _fn("busy", 25) + _fn("old", 18) + _fn("fresh", 22),
        {f"{MOD}::busy": 25, f"{MOD}::fresh": 22},
        ruff,
    )
    code, out = _run(root, "--check", "--base", "main")
    assert code == 1 and f"HISTORY {MOD}::fresh  added" in out, out


def test_a_rename_at_the_same_score_passes_against_the_merge_base(
    tmp_path: Path, ruff: str
) -> None:
    root = _history(tmp_path, ruff)
    _commit(
        root, _fn("busy", 25) + _fn("moved", 30), {f"{MOD}::busy": 25, f"{MOD}::moved": 30}, ruff
    )
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0 and "nothing added or raised" in out, out


def test_one_vanished_entry_carries_one_rename_only(tmp_path: Path, ruff: str) -> None:
    """``old`` (30) is gone; two additions at 25 cannot both be its rename."""
    root = _history(tmp_path, ruff)
    _commit(
        root,
        _fn("busy", 25) + _fn("a", 25) + _fn("b", 25),
        {f"{MOD}::busy": 25, f"{MOD}::a": 25, f"{MOD}::b": 25},
        ruff,
    )
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 1, out
    assert out.count("added to the baseline (25)") == 1, out


def test_a_branch_behind_a_main_that_lowered_a_score_passes(tmp_path: Path, ruff: str) -> None:
    """The comparison is with the merge base, not with main's tip, which moved on."""
    root = _history(tmp_path, ruff)
    (root / "packages/pkg/new.py").write_text(_fn("calm", 3), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "unrelated work")
    _git(root, "checkout", "-q", "main")
    _commit(root, _fn("busy", 23) + _fn("old", 30), {f"{MOD}::busy": 23, f"{MOD}::old": 30}, ruff)
    _git(root, "checkout", "-q", "work")
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0 and "nothing added or raised" in out, out


def test_the_history_check_compares_with_origin_main_by_default(tmp_path: Path, ruff: str) -> None:
    root = _history(tmp_path, ruff)
    _git(root, "update-ref", "refs/remotes/origin/main", "main")
    _git(root, "branch", "-D", "main")
    _commit(root, _fn("busy", 27) + _fn("old", 30), {f"{MOD}::busy": 27, f"{MOD}::old": 30}, ruff)
    code, out = _run(root, "--check")
    assert code == 1 and f"HISTORY {MOD}::busy  raised in the baseline: 25 at" in out, out


def _two_prs_on_main(tmp_path: Path, ruff: str) -> tuple[Path, str]:
    """Two PRs from one main each carried ``dead`` (30) to a new function; both landed.

    Each PR's own run compared with that main and passed. Returns the repo, on
    ``main`` after both, and the commit the second landing replaced.
    """
    keep = _fn("keep", 25)
    root = _tree(tmp_path, {MOD: keep + _fn("dead", 30)})
    _baseline(root, ruff, {f"{MOD}::keep": 25, f"{MOD}::dead": 30})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "main0")
    _commit(root, keep + _fn("n1", 28), {f"{MOD}::keep": 25, f"{MOD}::n1": 28}, ruff)
    before = _git(root, "rev-parse", "HEAD")
    _commit(
        root,
        keep + _fn("n1", 28) + _fn("n2", 27),
        {f"{MOD}::keep": 25, f"{MOD}::n1": 28, f"{MOD}::n2": 27},
        ruff,
    )
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root, before


def _actions_env(tmp_path: Path, event: str, ref: str, before: str | None) -> dict[str, str]:
    path = tmp_path / f"event-{event}.json"
    path.write_text(json.dumps({} if before is None else {"before": before}), encoding="utf-8")
    return {"GITHUB_EVENT_NAME": event, "GITHUB_REF": ref, "GITHUB_EVENT_PATH": str(path)}


def test_a_push_to_main_compares_with_the_commit_it_replaced(tmp_path: Path, ruff: str) -> None:
    root, before = _two_prs_on_main(tmp_path, ruff)
    # Without the event, main's merge base with origin/main is HEAD: blind.
    code, out = _run(root, "--check", "--require-base")
    assert code == 0, out
    event = _actions_env(tmp_path, "push", "refs/heads/main", before)
    code, out = _run(root, "--check", "--require-base", "--github-event", github=event)
    assert code == 1, out
    assert f"HISTORY a push to main: compared with {before[:8]}, the main it replaced" in out
    assert f"HISTORY {MOD}::n2  added to the baseline (27)" in out


def test_other_github_events_keep_the_merge_base_with_origin_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "a" * 40
    for event, ref, before, want in [
        ("pull_request", "refs/pull/7/merge", sha, "origin/main"),
        ("push", "refs/heads/feature", sha, "origin/main"),
        ("push", "refs/heads/main", "0" * 40, "origin/main"),
        ("push", "refs/heads/main", sha, sha),
    ]:
        for name, value in _actions_env(tmp_path, event, ref, before).items():
            monkeypatch.setenv(name, value)
        assert gate.github_base("origin/main")[0] == want, (event, ref, before)
    for name, value in _actions_env(tmp_path, "push", "refs/heads/main", None).items():
        monkeypatch.setenv(name, value)
    with pytest.raises(gate.MeasureError, match="not a commit"):
        gate.github_base("origin/main")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(gate.MeasureError, match="did not read"):
        gate.github_base("origin/main")
    monkeypatch.delenv("GITHUB_EVENT_NAME")
    assert gate.github_base("origin/main") == ("origin/main", [])


def test_a_ruff_upgrade_is_shown_not_compared(tmp_path: Path, ruff: str) -> None:
    root = _history(tmp_path, ruff, base_ruff="0.0.1")
    _commit(root, _fn("busy", 26) + _fn("old", 30), {f"{MOD}::busy": 26, f"{MOD}::old": 30}, ruff)
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0, out
    assert f"measured with ruff 0.0.1, this baseline with ruff {ruff}" in out
    assert f"raised  {MOD}::busy  25 -> 26" in out
    assert "::warning" not in out
    # In GitHub Actions the list also goes to the job summary, and a warning names it.
    summary = tmp_path / "summary.md"
    actions = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}
    code, out = _run(root, "--check", "--base", "main", "--require-base", github=actions)
    assert code == 0, out
    assert "::warning title=Complexity ratchet::Complexity baseline re-measured: ruff 0.0.1" in out
    text = summary.read_text(encoding="utf-8")
    assert f"-> ruff {ruff}, 1 changed" in text and f"raised  {MOD}::busy  25 -> 26" in text


def test_without_a_merge_base_the_history_check_says_so(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 21)}, branch="work")
    _baseline(root, ruff, {f"{MOD}::busy": 21})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "no main branch here")
    code, out = _run(root, "--check", "--base", "main")
    assert code == 0 and "HISTORY not compared: no merge base with main" in out, out
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 1 and "--require-base makes this a failure" in out, out


def test_a_merge_base_without_a_baseline_has_nothing_to_compare(tmp_path: Path, ruff: str) -> None:
    root = _tree(tmp_path, {MOD: _fn("busy", 21)})
    _git(root, "commit", "-q", "-m", "before the gate")
    _baseline(root, ruff, {f"{MOD}::busy": 21})
    code, out = _run(root, "--check", "--base", "main", "--require-base")
    assert code == 0, out
    assert re.search(r"merge base [0-9a-f]{8} has no complexity-baseline\.json", out)


# === the trend value ============================================================


def test_the_trend_value_counts_each_method_once_and_the_old_count_follows(tmp_path: Path) -> None:
    """Since the owner's 2026-11-09 decision the trend value counts each method once; the
    2026-09-19 count, which lists each method twice, follows it for comparison."""
    method = {"type": "method", "name": "m", "classname": "K", "complexity": 55, "closures": []}
    report = {
        "a.py": [
            {
                "type": "function",
                "name": "f",
                "complexity": 21,
                "closures": [
                    {"type": "function", "name": "g", "complexity": 2, "closures": []},
                ],
            },
            {"type": "class", "name": "K", "complexity": 56, "methods": [method]},
            method,
        ],
        "b.py": [],
    }
    assert gate.radon_trend(report) == {"once": (3, 2, 1), "docs05": (4, 3, 2)}
    path = tmp_path / "radon.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    code, out = _run(tmp_path, "--trend", "--radon-json", str(path))
    assert code == 0, out
    lines = out.splitlines()
    assert lines[1:] == [
        "  the trend value, each function, method and closure once (the owner's decision of "
        "2026-11-09): 3; CC > 20: 2; CC > 50: 1",
        "  the 2026-09-19 way, which counts each method twice (radon's recursive count; for "
        "comparison, not the trend value): 4; CC > 20: 3; CC > 50: 2",
    ]
    assert "proposal" not in out
    with pytest.raises(gate.MeasureError, match="could not read 1"):
        gate.radon_trend({"a.py": {"error": "invalid syntax"}})
