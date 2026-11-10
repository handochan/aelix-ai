#!/usr/bin/env python3
"""Run the gates your diff needs before you push, and say which ones you still owe (#733, ADR-0303).

    uv run --frozen python scripts/preflight.py              # run them
    uv run --frozen python scripts/preflight.py --plan       # list them and why; run nothing
    uv run --frozen python scripts/preflight.py --claims -   # also print a "How it was verified" block

``--frozen`` makes that one ``uv run`` use ``uv.lock`` as it is, which is the
form that is safe in a checkout other sessions share.

CI runs three gates on every leg (``CONTRIBUTING.md``, "The gates your PR has to
pass"), and the full suite is one of them. Several others fail in files you
never touched or only on a platform you are not on, and which ones your change
can break depends on what it touched. This script reads the change and runs the
gates that change can fail. The table is :data:`GATES`; a new gate is one row.

THE CHANGE is everything since the merge base with ``--base`` (default
``origin/main``, then ``main``): the commits, what is staged, what is not, and
untracked files git does not ignore. Paths deleted in the change count for
deciding which gates run and are never passed to one.

WHAT IT NEVER DOES:

* run the full suite. It prints that command last, as the step you still owe;
* change the tree, unless you pass ``--fix-citations``;
* run a gate with ``--plan``. ``--plan`` reads the change with git and starts no
  gate and no other program: ``--docker`` asks ``docker info`` only when that
  gate runs, and is skipped then if docker does not answer;
* call ``uv`` for a gate, with ONE exception. Every other gate runs through this
  interpreter (``python -m pytest``, ``python -m ruff``, ``python scripts/...``),
  because a nested ``uv run`` can resync the ``.venv`` under a test run. The
  exception is ``--pythons``: another interpreter needs its own environment, and
  ``uv run --isolated --frozen --all-packages -p 3.X`` is the one way to get
  ``uv.lock``'s exact packages on it without writing to ``.venv`` (``--isolated``
  builds a throwaway environment, ``--frozen`` installs ``uv.lock`` as it is
  without re-resolving it, and ``VIRTUAL_ENV`` is dropped from its
  environment). ``tests/test_preflight.py`` runs that gate with a real ``uv``
  and checks that the project has no ``.venv`` afterwards and that a stale lock
  is used as it is, and checks every other gate starts this interpreter.

EXIT STATUS: 0 when every gate that ran passed, 1 when one failed, 2 for a usage
error (an unknown gate name, a base that cannot be found, ``--only pythons``
without ``--pythons``, a ``--claims`` file in a directory that does not exist).
A gate with nothing to run is skipped with the reason, never counted as passed.

THE OPT-IN GATES run the touched-test set somewhere else: ``--docker`` in a
Linux container built from your tracked and staged changes (skipped, not
failed, when docker is missing), ``--pythons 3.11,3.14`` on other interpreters,
``--windows-clock`` under ``tests/plugins/coarse_clock.py``, which gives
``time.monotonic`` the 15.625 ms tick Windows has on Python 3.12 and older.
Naming one in ``--only`` turns it on too; ``pythons`` still needs its versions.

``--claims FILE`` writes each gate's command, exit status, duration and the end
of its output as Markdown for a PR's "How it was verified"; ``--claims -``
prints it after the summary instead, so nothing lands in the tree. Commands are
shown as typed from the repository root (``python`` for this interpreter); the
checkout's path and the home directory are replaced in the output.

Output a console cannot encode (a non-ASCII path on a cp1252 console) is
printed as a backslash escape rather than ending the run.

Standard library only, and no aelix import: it has to run on a tree whose
packages do not import.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent

FULL_SUITE = "uv run pytest -p no:cacheprovider -q"
PYTEST = ("-m", "pytest", "-p", "no:cacheprovider", "-q")
TOUCHED_FLAGS = ("-W", "error::pytest.PytestUnraisableExceptionWarning")
BUNDLED_DOCS = "packages/aelix-coding-agent/src/aelix_coding_agent/docs/"
TAIL_LINES = 15
SOURCES = ("committed", "staged", "unstaged", "untracked")
DOCKER_INFO_TIMEOUT = 30.0
"""Seconds ``docker info`` has to answer before the ``docker`` gate is skipped."""


class UsageError(Exception):
    """A request preflight cannot carry out; exit status 2."""


class GateSkipped(Exception):
    """Raised by a step at run time: the gate is skipped, for the reason given, not failed."""


# --- the change --------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True) -> str:
    """One git call that takes no optional lock.

    ``git status`` refreshes a stale index and writes it back under
    ``index.lock``; the commands preflight runs did not on git 2.54 even without
    the flag (measured for #733), so the flag guards other git versions, and
    ``tests/test_preflight.py`` pins that every git argv here carries it.
    """

    proc = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise UsageError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


class ChangeSet(NamedTuple):
    base_ref: str
    base_sha: str
    head_sha: str
    paths: dict[str, frozenset[str]]
    """Repository-relative POSIX path -> which of :data:`SOURCES` it came from."""
    base_top_level: frozenset[str]
    """The top-level names the base commit has, to tell a new top-level path."""
    repo: Path

    def exists(self, path: str) -> bool:
        return (self.repo / path).is_file()


def resolve_base(repo: Path, ref: str | None) -> tuple[str, str]:
    """``(ref, merge-base sha)``; ``--base`` if given, else ``origin/main``, then ``main``."""

    candidates = [ref] if ref else ["origin/main", "main"]
    for candidate in candidates:
        if not git(repo, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}", check=False).strip():
            continue
        base = git(repo, "merge-base", "HEAD", candidate, check=False).strip()
        if base:
            return candidate, base
    tried = ", ".join(candidates)
    raise UsageError(f"no merge base with {tried}; pass --base REF")


def _names(output: str) -> list[str]:
    return [name for name in output.split("\0") if name]


def changed_paths(repo: Path, base_sha: str) -> dict[str, frozenset[str]]:
    """Every path the change touches, from the merge base through the untracked files."""

    found: dict[str, set[str]] = {}
    listings = {
        "committed": git(repo, "diff", "--name-only", "-z", "--no-renames", base_sha, "HEAD"),
        "staged": git(repo, "diff", "--name-only", "-z", "--no-renames", "--cached"),
        "unstaged": git(repo, "diff", "--name-only", "-z", "--no-renames"),
        "untracked": git(repo, "ls-files", "-z", "--others", "--exclude-standard"),
    }
    for source, output in listings.items():
        for name in _names(output):
            found.setdefault(name, set()).add(source)
    return {name: frozenset(sources) for name, sources in sorted(found.items())}


def load_changes(repo: Path, ref: str | None) -> ChangeSet:
    base_ref, base_sha = resolve_base(repo, ref)
    head = git(repo, "rev-parse", "HEAD").strip()
    top = frozenset(_names(git(repo, "ls-tree", "-z", "--name-only", base_sha)))
    return ChangeSet(base_ref, base_sha, head, changed_paths(repo, base_sha), top, repo)


# --- what a path is ------------------------------------------------------------

_PRODUCT = re.compile(r"^(?:packages/[^/]+/src|src)/.+\.py$")


def is_product_py(path: str) -> bool:
    """Shipped source: ``packages/<pkg>/src/**.py`` or the umbrella's ``src/**.py``."""

    return bool(_PRODUCT.match(path))


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") and name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


def module_name(path: str) -> str | None:
    """The dotted name a product or test-helper ``.py`` imports as, or ``None``."""

    match = re.match(r"^(?:packages/[^/]+/src|src)/(.+)\.py$", path)
    if match:
        parts = match.group(1).split("/")
    elif path.startswith("tests/") and path.endswith(".py") and not is_test_file(path):
        parts = path[: -len(".py")].split("/")
    else:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or parts[-1] == "conftest":
        return None
    return ".".join(parts)


def _naming_patterns(dotted: str) -> list[re.Pattern[str]]:
    """Ways a test file names *dotted*: the dotted path itself, or ``from <parent> import <leaf>``."""

    parts = dotted.split(".")
    patterns = []
    if len(parts) >= 2:
        patterns.append(re.compile(rf"(?<![\w.]){re.escape(dotted)}(?!\w)"))
        parent, leaf = re.escape(".".join(parts[:-1])), re.escape(parts[-1])
        patterns.append(re.compile(rf"from\s+{parent}\s+import\s+[^\n]*\b{leaf}\b"))
        patterns.append(re.compile(rf"from\s+{parent}\s+import\s+\([^)]*\b{leaf}\b", re.S))
    else:
        # A top-level package: its bare name is in every docstring, so only an import counts.
        patterns.append(re.compile(rf"^\s*(?:from|import)\s+{re.escape(dotted)}(?![\w.])", re.M))
    if parts[0] == "tests" and len(parts) >= 3:
        # Under pytest's rootdir import a helper is also importable without the `tests.` prefix.
        patterns.extend(_naming_patterns(".".join(parts[1:])))
    return patterns


def touched_tests(changes: ChangeSet) -> list[str]:
    """Changed test files, and test files that name a changed module or script (an approximation).

    A module is named by its dotted path or by ``from <parent> import <leaf>``; a
    top-level package only by an import line; a ``scripts/`` file by its file name.
    Re-exports, ``importlib`` strings built at run time and relative imports
    inside ``tests/`` are missed, which is why the list is printed.
    """

    chosen = {p for p in changes.paths if is_test_file(p) and changes.exists(p)}
    patterns = [pattern for p in changes.paths for pattern in _naming_patterns_for(p)]
    if patterns:
        for test in sorted((changes.repo / "tests").rglob("*.py")):
            rel = test.relative_to(changes.repo).as_posix()
            if rel in chosen or not is_test_file(rel):
                continue
            try:
                text = test.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(pattern.search(text) for pattern in patterns):
                chosen.add(rel)
    return sorted(chosen)


def _naming_patterns_for(path: str) -> list[re.Pattern[str]]:
    if re.fullmatch(r"scripts/[^/]+\.py", path):
        # A script is not imported by name; its tests name its file.
        return [re.compile(rf"(?<![\w.-]){re.escape(path.rsplit('/', 1)[-1])}(?![\w.])")]
    dotted = module_name(path)
    return _naming_patterns(dotted) if dotted else []


# --- the gates -----------------------------------------------------------------


class Context(NamedTuple):
    repo: Path
    python: str
    changes: ChangeSet
    tests: list[str]
    args: argparse.Namespace


class Step(NamedTuple):
    shown: str
    """The command as a contributor would type it from the repository root."""
    argv: list[str] | None = None
    run: Callable[[Context, float], tuple[int | None, str]] | None = None
    """For a gate that is not one command: returns ``(exit status or None on timeout, output)``."""
    env: dict[str, str] | None = None


Builder = Callable[[Context], "list[Step] | str"]
"""A gate's steps for this change, or the reason it is skipped."""


class Gate(NamedTuple):
    name: str
    when: Callable[[str, ChangeSet], bool] | None
    """A changed path that makes this gate run; ``None`` runs it always."""
    when_text: str
    build: Builder
    timeout: float
    hint: str = ""
    opt_in: str | None = None
    """The flag that turns this gate on; it never runs without that flag or ``--only``."""


def _py(ctx: Context, *args: str) -> Step:
    return Step(shown=" ".join(("python", *args)), argv=[ctx.python, *args])


def _pytest(ctx: Context, *paths: str, flags: tuple[str, ...] = ()) -> list[Step] | str:
    """One pytest step over the *paths* that exist, or a skip reason when none does.

    Never a pytest with no path: that is the full suite, which preflight does not run.
    """

    present = [p for p in paths if (ctx.repo / p).exists()]
    if not present:
        return f"none of {', '.join(paths)} is in this tree"
    return [_py(ctx, *PYTEST, *flags, *present)]


def _untracked_tests(ctx: Context, timeout: float) -> tuple[int | None, str]:
    untracked = [p for p, s in ctx.changes.paths.items() if "untracked" in s and p.startswith("tests/") and p.endswith(".py")]
    if not untracked:
        return 0, "no untracked .py under tests/"
    lines = ["untracked .py files under tests/:"] + [f"  {p}" for p in untracked]
    lines.append(
        "git add them: the citation gate and the HOME-sandbox gate read `git ls-files` and do "
        "not see a file git does not know, so they would pass over it."
    )
    return 1, "\n".join(lines)


CITATIONS_HINT = (
    "Repair (CONTRIBUTING.md, 'The citation lock'): run `uv run python scripts/check_citations.py "
    "--fix` (or this script with --fix-citations) and commit what it changes. If --fix exits "
    "non-zero, it named citations it could not place and left the gate red on purpose: open each "
    "file, find the construct the sentence is about and use that line number, never the old one "
    "plus a delta. A cited block edited where it stands keeps its number: `--lock` re-pins the "
    "text and prints every anchor it replaced; check that list before committing it."
)


def _citations(ctx: Context) -> list[Step] | str:
    steps = []
    if ctx.args.fix_citations:
        steps.append(_py(ctx, "scripts/check_citations.py", "--fix"))
    steps.append(_py(ctx, "scripts/check_citations.py", "--check"))
    return steps


def _touched(ctx: Context) -> list[Step] | str:
    if not ctx.tests:
        return "no touched test file"
    return _pytest(ctx, *ctx.tests, flags=TOUCHED_FLAGS)


def pyright_errors(report: str, named: list[Path]) -> list[str]:
    """The errors pyright's ``--outputjson`` *report* gives for the *named* files, and no others."""

    wanted = {os.path.normcase(str(p.resolve())) for p in named}
    data = json.loads(report)
    errors = []
    for diag in data.get("generalDiagnostics", []):
        if diag.get("severity") != "error":
            continue
        path = os.path.normcase(str(Path(diag.get("file", "")).resolve()))
        if path not in wanted:
            continue
        line = diag.get("range", {}).get("start", {}).get("line", 0) + 1
        errors.append(f"{Path(path).name} line {line}: {diag.get('message', '').splitlines()[0]}")
    return errors


def _pyright_windows(ctx: Context) -> list[Step] | str:
    files = [
        p
        for p in ctx.changes.paths
        if (is_product_py(p) or (p.startswith("scripts/") and p.endswith(".py")))
        and p != "scripts/pyright_spike.py"
        and ctx.changes.exists(p)
    ]
    if not files:
        return "no changed product or script .py left on disk"
    if importlib.util.find_spec("pyright") is None:
        return "pyright is not installed in this environment (uv sync)"

    def run(c: Context, timeout: float) -> tuple[int | None, str]:
        argv = [c.python, "-m", "pyright", "--pythonplatform", "Windows", "--outputjson", *files]
        rc, out, err = _spawn(argv, c, timeout, merge=False)
        if rc is None:
            return None, out + err
        try:
            errors = pyright_errors(out, [c.repo / f for f in files])
        except ValueError:
            return 1, "pyright did not produce a JSON report:\n" + out + err
        if errors:
            return 1, "\n".join([f"{len(errors)} error(s) with --pythonplatform Windows:", *errors])
        return 0, f"{len(files)} file(s), no errors with --pythonplatform Windows"

    shown = "python -m pyright --pythonplatform Windows " + " ".join(files)
    return [Step(shown=shown, run=run)]


def _uv_version_from_ci(repo: Path) -> str | None:
    try:
        text = (repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^\s*version:\s*"(\d+\.\d+\.\d+)"', text, re.M)
    return match.group(1) if match else None


DOCKERFILE = """\
FROM python:{python}-slim
RUN apt-get update -qq && apt-get install -y -qq git procps >/dev/null && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir {uv}
ENV UV_PYTHON=/usr/local/bin/python UV_PYTHON_DOWNLOADS=never
WORKDIR /w
COPY . /w/
RUN git init -q && git add -A && git -c user.name=preflight -c user.email=preflight@localhost \\
    -c commit.gpgsign=false commit -qm preflight
RUN uv sync --frozen --all-packages
"""


def _docker(ctx: Context) -> list[Step] | str:
    """The container gate. Building it starts nothing: ``docker info`` is asked when it runs."""

    if not ctx.tests:
        return "no touched test file"
    docker = shutil.which("docker")
    if docker is None:
        return "docker is not installed"
    tag = f"aelix-preflight-{uuid.uuid4().hex[:10]}"

    def run(c: Context, timeout: float) -> tuple[int | None, str]:
        try:
            probe = subprocess.run([docker, "info"], capture_output=True, timeout=DOCKER_INFO_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError):
            raise GateSkipped("docker did not answer `docker info`") from None
        if probe.returncode != 0:
            raise GateSkipped(f"docker is not running (`docker info` exited {probe.returncode})")
        return _docker_run(c, docker, tag, timeout)

    shown = (
        f"docker info (when the gate runs; no answer skips it); "
        f"git archive <git stash create, else HEAD> | docker build -t {tag} (python:<.python-version>-slim, "
        f"git, uv sync --frozen --all-packages); docker run --rm --init {tag} .venv/bin/python "
        f"{' '.join(PYTEST + TOUCHED_FLAGS)} <touched tests>; docker rmi {tag}"
    )
    return [Step(shown=shown, run=run)]


def _docker_run(ctx: Context, docker: str, tag: str, timeout: float) -> tuple[int | None, str]:
    deadline = time.monotonic() + timeout
    log: list[str] = []
    tree = git(ctx.repo, "stash", "create").strip() or "HEAD"
    log.append(f"image from {'the tracked and staged changes (git stash create)' if tree != 'HEAD' else 'HEAD'}")
    archived = set(_names(git(ctx.repo, "ls-tree", "-r", "-z", "--name-only", tree)))
    tests = [t for t in ctx.tests if t in archived]
    left_out = [t for t in ctx.tests if t not in archived]
    if left_out:
        log.append("not in the image (untracked; git add them): " + ", ".join(left_out))
    if not tests:
        return 0, "\n".join([*log, "no touched test file is in the image"])
    python = (ctx.repo / ".python-version").read_text(encoding="utf-8").strip() if (
        ctx.repo / ".python-version"
    ).is_file() else "3.12"
    uv = _uv_version_from_ci(ctx.repo)
    workdir = Path(tempfile.mkdtemp(prefix="aelix-preflight-"))
    name = f"{tag}-run"
    try:
        context = workdir / "ctx"
        context.mkdir()
        archive = subprocess.run(
            ["git", "--no-optional-locks", "archive", "--format=tar", tree],
            cwd=ctx.repo,
            capture_output=True,
            check=True,
        ).stdout
        tar_path = workdir / "tree.tar"
        tar_path.write_bytes(archive)
        with tarfile.open(tar_path) as tf:
            if sys.version_info >= (3, 12):
                tf.extractall(context, filter="data")
            else:
                tf.extractall(context)  # our own git archive
        dockerfile = workdir / "Dockerfile"
        dockerfile.write_text(DOCKERFILE.format(python=python, uv=f"uv=={uv}" if uv else "uv"), encoding="utf-8")
        rc, out = _execute(
            [docker, "build", "-q", "-t", tag, "-f", str(dockerfile), str(context)], ctx, max(1.0, deadline - time.monotonic())
        )
        log.append(f"docker build -> {rc}")
        if rc != 0:
            return rc, "\n".join([*log, out])
        argv = [docker, "run", "--rm", "--init", "--name", name, tag, ".venv/bin/python", *PYTEST, *TOUCHED_FLAGS, *tests]
        rc, out = _execute(argv, ctx, max(1.0, deadline - time.monotonic()))
        log.append(f"docker run ({len(tests)} test file(s), python {python}) -> {rc}")
        return rc, "\n".join([*log, out])
    finally:
        for cleanup in ([docker, "rm", "-f", name], [docker, "rmi", "-f", tag]):
            subprocess.run(cleanup, capture_output=True, timeout=120, check=False)
        shutil.rmtree(workdir, ignore_errors=True)


def _pythons(ctx: Context) -> list[Step] | str:
    """The one gate that calls ``uv`` (the module docstring says why): a throwaway environment per version."""

    if not ctx.tests:
        return "no touched test file"
    if not ctx.args.pythons:
        return "no version given (--pythons 3.11,3.14)"
    uv = shutil.which("uv")
    if uv is None:
        return "uv is not on PATH"
    # An exported VIRTUAL_ENV (a maintainer shell often points it at the main
    # checkout) is not this run's environment; uv is told nothing about it.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    steps = []
    for version in ctx.args.pythons:
        argv = [uv, "run", "--isolated", "--frozen", "--all-packages", "-p", version, "python", *PYTEST, *TOUCHED_FLAGS, *ctx.tests]
        shown = f"uv run --isolated --frozen --all-packages -p {version} python {' '.join(PYTEST + TOUCHED_FLAGS)} <touched tests>"
        steps.append(Step(shown=shown, argv=argv, env=env))
    return steps


def _windows_clock(ctx: Context) -> list[Step] | str:
    if not ctx.tests:
        return "no touched test file"
    if not (ctx.repo / "tests" / "plugins" / "coarse_clock.py").is_file():
        return "tests/plugins/coarse_clock.py is not in this tree"
    return _pytest(ctx, *ctx.tests, flags=("-p", "tests.plugins.coarse_clock", *TOUCHED_FLAGS))


def _packaging_path(path: str, changes: ChangeSet) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path in {".gitignore", ".gitattributes", "uv.lock", "install.sh", "install.ps1"}
        or name == "pyproject.toml"
        or path.split("/", 1)[0] not in changes.base_top_level
    )


def _any_py(path: str, changes: ChangeSet) -> bool:
    return path.endswith(".py")


def _product(path: str, changes: ChangeSet) -> bool:
    return is_product_py(path)


def _docs(path: str, changes: ChangeSet) -> bool:
    return path.startswith(("docs/guides/", BUNDLED_DOCS))


def _package_or_src(path: str, changes: ChangeSet) -> bool:
    return (path.startswith("packages/") and path.endswith(".py")) or path.startswith("src/")


def _windows_typed(path: str, changes: ChangeSet) -> bool:
    return is_product_py(path) or (path.startswith("scripts/") and path.endswith(".py"))


def _tests_or_product(path: str, changes: ChangeSet) -> bool:
    return path.startswith("tests/") or is_product_py(path)


#: The tests that read every file under ``tests/`` or every product source for a
#: rule each file must keep. ``touched-tests`` selects a test file only by the
#: module or script it names, so a change to a file they scan does not select
#: them, and a change to any file can fail them: #733's round 1 set
#: ``HOME`` directly in ``tests/test_preflight.py``, and only the full suite's
#: ``test_no_test_sets_home_outside_the_helper`` saw it. A bare path is a file
#: whose every test belongs to such a scan; ``file::test`` is one test of a mixed
#: file. Not here: ``band`` and ``pi-parity`` run their own scans on every
#: product change, and a scan of one subtree or of named files is not a rule
#: every file keeps. ``tests/test_preflight.py`` checks that each entry exists.
HYGIENE_TESTS = (
    "tests/test_env_sandbox_windows.py::test_no_test_sets_home_outside_the_helper",
    "tests/test_env_sandbox_windows.py::test_no_test_hand_builds_a_child_home",
    "tests/test_coarse_clock_plugin.py::test_the_plugin_is_not_collected_and_not_loaded_by_conftest",
    "tests/test_user_facing_raise_strings.py",
    "tests/cli/test_p2_import_direction.py",
    "tests/cli/test_agent_context.py::test_reload_instruction_is_mode_agnostic",
    "tests/cli/test_project_settings_trust_369.py::test_every_production_settings_manager_passes_project_trust",
    "tests/cli/test_prompt_tool_honesty.py::test_nothing_writes_the_registry_or_the_active_set_outside_commit_tools",
    "tests/cli/test_withdrawn_default_sweep_434.py::test_every_resolution_call_is_classified",
    "tests/cli/test_withdrawn_default_sweep_434.py::test_seed_callers_pass_parseds_pair_through",
    "tests/core/test_skill_selection_511.py::test_every_set_skills_call_goes_through_effective_skills",
    "tests/packaging_gate/test_declared_dependencies.py",
    "tests/tui/test_event_renderer.py::test_every_production_echo_call_passes_a_width",
    "tests/tui/test_own_pickers_marked.py",
    "tests/tui/test_settings_rows.py::test_the_call_site_scanner_can_tell_a_call_from_a_mention",
    "tests/tui/test_settings_rows.py::test_the_gitignore_row_has_a_live_consumer",
    "tests/tui/test_settings_rows.py::test_the_inert_and_wired_lists_are_measured_not_declared",
    "tests/util/test_offline.py::test_no_module_outside_the_predicate_reads_an_offline_name",
)


def _hygiene(ctx: Context) -> list[Step] | str:
    present = [t for t in HYGIENE_TESTS if (ctx.repo / t.split("::", 1)[0]).is_file()]
    if not present:
        return "none of the HYGIENE_TESTS files is in this tree"
    return [_py(ctx, *PYTEST, *present)]


#: The gates, in the order they run. A new gate is one row: a name, the changed
#: paths that make it run (``None`` = always), a builder for its command, and a
#: timeout in seconds. #293's complexity ratchet joins as one row once it merges.
GATES: tuple[Gate, ...] = (
    Gate("untracked-tests", None, "always", lambda c: [Step(
        shown="git ls-files --others --exclude-standard -- tests", run=_untracked_tests)], 60),
    Gate("ruff", None, "always", lambda c: [_py(c, "-m", "ruff", "check", ".")], 300,
         hint="`ruff check --fix` takes the fixable ones; `ruff format` is deliberately not enforced."),
    Gate("types", None, "always", lambda c: [_py(c, "scripts/check_types.py")], 900),
    Gate("citations", None, "always", _citations, 600, hint=CITATIONS_HINT),
    Gate("docs-bundle", _docs, "docs/guides/ or its bundled copy",
         lambda c: _pytest(c, "tests/test_docs_bundle_sync.py", "tests/cli/test_docs_signpost.py"), 600,
         hint="Both copies move together: `uv run python scripts/sync_bundled_docs.py` (ADR-0218)."),
    Gate("import-direction", _package_or_src, "a .py under packages/, or anything under src/",
         lambda c: [_py(c, "scripts/check_import_direction.py")], 300,
         hint="Imports only go down (CONTRIBUTING.md gate 4, ADR-0015)."),
    Gate("band", _product, "a product .py (packages/*/src, src)",
         lambda c: _pytest(c, "tests/agents/test_p2_band_boundaries.py"), 600),
    Gate("packaging", _packaging_path,
         ".gitignore, .gitattributes, a pyproject.toml, uv.lock, the installers, or a new top-level path",
         lambda c: _pytest(c, "tests/packaging_gate"), 900,
         hint=".gitignore and every pyproject exclude list move together (CONTRIBUTING.md gate 3)."),
    Gate("pi-parity", _product, "a product .py (packages/*/src, src)",
         lambda c: _pytest(c, "tests/pi_parity"), 900),
    Gate("test-hygiene", _tests_or_product, "a path under tests/ or a product .py", _hygiene, 600,
         hint="Each of these reads every test file or every product source; its message names the file to fix, usually with the line."),
    Gate("touched-tests", _any_py, "a .py", _touched, 1800),
    Gate("pyright-windows", _windows_typed, "a product .py or a scripts/ .py", _pyright_windows, 900,
         hint="Guard a POSIX-only name with `if sys.platform != \"win32\":`; CI type-checks on Windows too."),
    Gate("docker", _any_py, "a .py", _docker, 3600, opt_in="--docker"),
    Gate("pythons", _any_py, "a .py", _pythons, 3600, opt_in="--pythons 3.11,3.14"),
    Gate("windows-clock", _any_py, "a .py", _windows_clock, 1800, opt_in="--windows-clock"),
)

GATE_NAMES = tuple(g.name for g in GATES)


# --- running -------------------------------------------------------------------


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(extra if extra is not None else os.environ)
    # The gates find `pyright` and `ruff` next to this interpreter, as `uv run` would.
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    return env


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _spawn(
    argv: list[str], ctx: Context, timeout: float, env: dict[str, str] | None = None, merge: bool = True
) -> tuple[int | None, str, str]:
    """Run *argv* from the repository root in its own process group.

    Returns ``(exit status, stdout, stderr)``, decoded as UTF-8 with replacement;
    the status is ``None`` when it ran past *timeout* and its group was killed.
    With *merge* stderr is folded into stdout, in order.
    """

    err_to = subprocess.STDOUT if merge else subprocess.PIPE
    if sys.platform == "win32":
        proc = subprocess.Popen(
            argv, cwd=ctx.repo, env=_child_env(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=err_to, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen(
            argv, cwd=ctx.repo, env=_child_env(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=err_to, start_new_session=True,
        )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, err = proc.communicate()
        note = f"\n[preflight] timed out after {timeout:.0f} s; its process group was killed"
        return None, _text(out) + note, _text(err)
    except BaseException:
        _kill_tree(proc)
        proc.communicate()
        raise
    return proc.returncode, _text(out), _text(err)


def _text(data: bytes | None) -> str:
    return data.decode("utf-8", errors="replace") if data else ""


def _execute(argv: list[str], ctx: Context, timeout: float, env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """:func:`_spawn` with stderr folded into the output."""

    rc, out, _err = _spawn(argv, ctx, timeout, env)
    return rc, out


class Outcome(NamedTuple):
    gate: Gate
    status: str
    """``passed``, ``failed`` or ``skipped``."""
    reason: str
    """Why it was skipped, or what triggered it."""
    steps: list[tuple[Step, int | None, float, str]]


def _trigger(gate: Gate, changes: ChangeSet) -> list[str]:
    if gate.when is None:
        return ["always"]
    return [p for p in changes.paths if gate.when(p, changes)]


def plan(ctx: Context) -> list[tuple[Gate, str, str, list[Step]]]:
    """``(gate, "run" or "skip", trigger or reason, steps)`` for every gate, in order."""

    args = ctx.args
    rows = []
    for gate in GATES:
        if args.only and gate.name not in args.only:
            rows.append((gate, "skip", "not in --only", []))
            continue
        if gate.name in args.skip:
            rows.append((gate, "skip", "--skip", []))
            continue
        if gate.opt_in and not _opted_in(gate, args):
            rows.append((gate, "skip", f"opt-in: {gate.opt_in}", []))
            continue
        hits = _trigger(gate, ctx.changes)
        if not hits:
            rows.append((gate, "skip", f"no changed path matches: {gate.when_text}", []))
            continue
        built = gate.build(ctx)
        if isinstance(built, str):
            rows.append((gate, "skip", built, []))
            continue
        if not built:
            rows.append((gate, "skip", "nothing to run", []))
            continue
        more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
        rows.append((gate, "run", hits[0] + more, built))
    return rows


def _opted_in(gate: Gate, args: argparse.Namespace) -> bool:
    if gate.name in args.only:
        return True
    return {"docker": args.docker, "pythons": bool(args.pythons), "windows-clock": args.windows_clock}[gate.name]


def run_gate(gate: Gate, steps: list[Step], ctx: Context) -> list[tuple[Step, int | None, float, str]]:
    done = []
    deadline = time.monotonic() + gate.timeout
    for step in steps:
        started = time.monotonic()
        remaining = max(1.0, deadline - started)
        if step.run is not None:
            try:
                rc, out = step.run(ctx, remaining)
            except (OSError, subprocess.SubprocessError, UsageError, ValueError) as exc:
                rc, out = 1, f"[preflight] {step.shown}: {type(exc).__name__}: {exc}"
        else:
            assert step.argv is not None
            rc, out = _execute(step.argv, ctx, remaining, env=step.env)
        done.append((step, rc, time.monotonic() - started, out))
    return done


def _tail(text: str, n: int = TAIL_LINES) -> list[str]:
    return text.rstrip("\n").splitlines()[-n:]


def _scrub(text: str, repo: Path) -> str:
    """No checkout path, home directory or interpreter path in what a PR body gets."""

    for raw, shown in (
        (sys.executable, "python"),
        (str(repo), "."),
        (repo.as_posix(), "."),
        (str(Path.home()), "~"),
    ):
        if raw:
            text = text.replace(raw, shown)
    return text


# --- output ----------------------------------------------------------------------


def _header(ctx: Context) -> str:
    counts = {s: sum(1 for v in ctx.changes.paths.values() if s in v) for s in SOURCES}
    detail = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    return (
        f"preflight: base {ctx.changes.base_ref} ({ctx.changes.base_sha[:8]}), HEAD {ctx.changes.head_sha[:8]}, "
        f"{len(ctx.changes.paths)} changed path(s){f' ({detail})' if detail else ''}"
    )


def _print_tests(ctx: Context) -> None:
    if not ctx.tests:
        return
    print(f"touched tests ({len(ctx.tests)}; the changed test files and the tests that name a changed module):")
    for test in ctx.tests:
        print(f"  {test}")
    conftests = [p for p in ctx.changes.paths if p.endswith("conftest.py")]
    if conftests:
        print(f"  note: {', '.join(conftests)} changed; only the full suite measures a conftest")


def print_plan(ctx: Context, rows: list[tuple[Gate, str, str, list[Step]]]) -> None:
    print(_header(ctx))
    shown = list(ctx.changes.paths)
    for path in shown[:40]:
        print(f"  {path}  [{', '.join(sorted(ctx.changes.paths[path]))}]")
    if len(shown) > 40:
        print(f"  +{len(shown) - 40} more")
    _print_tests(ctx)
    width = max(len(n) for n in GATE_NAMES)
    print("would run:")
    for gate, verdict, why, steps in rows:
        if verdict == "run":
            print(f"  {gate.name:<{width}}  {why}")
            for step in steps:
                print(f"  {'':<{width}}    $ {_scrub(step.shown, ctx.repo)}")
    print("would skip:")
    for gate, verdict, why, _steps in rows:
        if verdict == "skip":
            print(f"  {gate.name:<{width}}  {why}")
    print(f"still owed after these: the full suite, which preflight never runs: {FULL_SUITE}")


def _status(results: list[tuple[Step, int | None, float, str]]) -> str:
    """``passed`` only when at least one step ran and every step exited 0."""

    return "passed" if results and all(rc == 0 for _s, rc, _d, _o in results) else "failed"


def render_claims(ctx: Context, outcomes: list[Outcome]) -> str:
    """The "How it was verified" block, with no checkout path, home directory or interpreter path."""

    fence = "````"
    lines = [
        "## How it was verified",
        "",
        f"`python scripts/preflight.py` on `{ctx.changes.head_sha[:8]}` against `{ctx.changes.base_ref}` "
        f"(`{ctx.changes.base_sha[:8]}`), {len(ctx.changes.paths)} changed path(s).",
        "",
        "| gate | result | time |",
        "|---|---|---|",
    ]
    for o in outcomes:
        took = sum(d for _s, _rc, d, _o in o.steps)
        result = o.status if o.status != "skipped" else f"skipped: {o.reason}"
        lines.append(f"| {o.gate.name} | {result} | {f'{took:.1f} s' if o.steps else ''} |")
    for o in outcomes:
        for step, rc, took, out in o.steps:
            status = "timed out" if rc is None else f"exit {rc}"
            lines += ["", f"**{o.gate.name}**: {status}, {took:.1f} s", "", fence, f"$ {step.shown}"]
            lines += [_scrub(line, ctx.repo) for line in _tail(out)]
            lines.append(fence)
    lines += ["", f"Not run by preflight: the full suite, `{FULL_SUITE}`.", ""]
    return _scrub("\n".join(lines), ctx.repo)


def _epilog() -> str:
    """The gate table for ``--help``, from :data:`GATES` itself."""

    width = max(len(n) for n in GATE_NAMES)
    lines = ["gates, in the order they run (a gate runs when a changed path matches its trigger):"]
    for gate in GATES:
        when = gate.when_text if gate.opt_in is None else f"{gate.when_text}; opt-in: {gate.opt_in}"
        lines.append(f"  {gate.name:<{width}}  {when}")
    lines.append(f"never run: the full suite, `{FULL_SUITE}`; run it last yourself.")
    return "\n".join(lines)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="preflight.py",
        description="Run the gates this change needs (see the module docstring).",
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base", metavar="REF", help="compare with the merge base of HEAD and REF (default origin/main, then main)")
    parser.add_argument("--plan", action="store_true", help="list the gates and why; run nothing")
    parser.add_argument("--only", default="", metavar="A,B", help="run only these gates")
    parser.add_argument("--skip", default="", metavar="A,B", help="do not run these gates")
    parser.add_argument("--fix-citations", action="store_true", help="run check_citations.py --fix before --check (changes the tree)")
    parser.add_argument("--docker", action="store_true", help="also run the touched tests in a Linux container")
    parser.add_argument("--pythons", default="", metavar="3.11,3.14", help="also run the touched tests on these interpreters")
    parser.add_argument("--windows-clock", action="store_true", help="also run the touched tests under the coarse-clock plugin")
    parser.add_argument(
        "--claims", metavar="FILE", help='write a Markdown "How it was verified" block to FILE, or print it with -'
    )
    args = parser.parse_args(argv)
    args.only = [n for n in args.only.split(",") if n]
    args.skip = [n for n in args.skip.split(",") if n]
    args.pythons = [v for v in args.pythons.split(",") if v]
    unknown = sorted(set(args.only + args.skip) - set(GATE_NAMES))
    if unknown:
        raise UsageError(f"unknown gate(s): {', '.join(unknown)}; the gates are {', '.join(GATE_NAMES)}")
    bad = [v for v in args.pythons if not re.fullmatch(r"3\.\d+", v)]
    if bad:
        raise UsageError(f"--pythons takes versions like 3.11: {', '.join(bad)}")
    if "pythons" in args.only and not args.pythons:
        raise UsageError("--only pythons runs nothing without versions: add --pythons 3.11,3.14")
    if args.claims and args.claims != "-":
        target = Path(args.claims)
        if target.is_dir() or not target.parent.is_dir():
            raise UsageError(f"--claims {args.claims}: no such directory to write it in (or pass --claims -)")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse(argv)
        changes = load_changes(REPO, args.base)
    except UsageError as exc:
        print(f"preflight: {exc}", file=sys.stderr)
        return 2
    ctx = Context(REPO, sys.executable, changes, touched_tests(changes), args)
    rows = plan(ctx)
    if args.plan:
        print_plan(ctx, rows)
        return 0

    print(_header(ctx))
    _print_tests(ctx)
    outcomes: list[Outcome] = []
    running = [r for r in rows if r[1] == "run"]
    number = 0
    for gate, verdict, why, steps in rows:
        if verdict == "skip":
            outcomes.append(Outcome(gate, "skipped", why, []))
            continue
        # Counted per gate started, so one skipped while it ran does not lend its number to the next.
        number += 1
        print(f"[{number}/{len(running)}] {gate.name} ({why}) ...", flush=True)
        try:
            results = run_gate(gate, steps, ctx)
        except GateSkipped as exc:
            outcomes.append(Outcome(gate, "skipped", str(exc), []))
            print(f"    skipped: {exc}", flush=True)
            continue
        status = _status(results)
        outcomes.append(Outcome(gate, status, why, results))
        took = sum(d for _s, _rc, d, _o in results)
        print(f"    {status} in {took:.1f} s", flush=True)
        if status == "failed":
            for step, rc, _took, out in results:
                if rc != 0:
                    print(f"    $ {_scrub(step.shown, ctx.repo)}  ->  {'timed out' if rc is None else f'exit {rc}'}")
                    for line in _tail(out):
                        print(f"    | {line}")
            if gate.hint:
                print(f"    hint: {gate.hint}")

    width = max(len(n) for n in GATE_NAMES)
    print("summary:")
    for o in outcomes:
        if o.status == "skipped":
            print(f"  skipped  {o.gate.name:<{width}}  {o.reason}")
        else:
            took = sum(d for _s, _rc, d, _o in o.steps)
            print(f"  {o.status:<7}  {o.gate.name:<{width}}  {took:.1f} s")
    if args.claims == "-":
        print('claims (paste under "How it was verified"):')
        sys.stdout.write(render_claims(ctx, outcomes))
        print("end of claims")
    elif args.claims:
        claims = Path(args.claims)
        claims.write_text(render_claims(ctx, outcomes), encoding="utf-8")
        print(f"claims written to {claims}")
    failed = [o.gate.name for o in outcomes if o.status == "failed"]
    print(f"still owed: the full suite, which preflight never runs: {FULL_SUITE}")
    return 1 if failed else 0


def _escape_unencodable_output() -> None:
    """A path the console cannot encode prints as an escape instead of ending the run.

    A Windows console or redirect on a legacy code page (cp1252) cannot encode a
    Korean file name, and ``print`` would raise ``UnicodeEncodeError``.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(errors="backslashreplace")


if __name__ == "__main__":
    _escape_unencodable_output()
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\npreflight: interrupted", file=sys.stderr)
        raise SystemExit(130) from None
