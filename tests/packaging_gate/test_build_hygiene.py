"""Packaging-hygiene gate — #111 B-7.

WHAT WENT WRONG. ``.gitignore`` carried ``.omc/state/``. A gitignore pattern with
a slash in the middle is ANCHORED to the directory holding the ``.gitignore``, so
that rule matched only ``/.omc/state/`` at the repo root. A nested
``packages/aelix-coding-agent/src/aelix_coding_agent/.omc/state/`` — which OMC
writes on a maintainer's machine, and which holds ``agent-replay-*.jsonl``
session transcripts — matched nothing. hatchling selects build files by honouring
the VCS ignore rules, found no rule, and swept the maintainer's transcripts into
both the published wheel and the sdist. The root sdist was worse still: 51
internal sprint plans and review memos under ``.omc/specs/``, plus ``.omc/wiki/``
session logs, ``CLAUDE.md``, ``AGENTS.md`` and root ``aelix-session-*.html``.

WHAT WENT WRONG THE SECOND TIME (#143). ``.claude/`` — where Claude Code keeps
per-checkout state, including ``.claude/worktrees/<name>/``, a COMPLETE working
copy of this repo per agent session — was in ``.git/info/exclude`` only. That
file is per-clone and does not travel, so a fresh ``actions/checkout`` in CI
never had it, every release built clean, and nobody saw the hole. A maintainer's
local ``uv build --sdist`` at ee1623c swept ``.claude/worktrees/`` into the
tarball — internal working copies, uncommitted work included, ready to attach to
a public GitHub Release.

HOW BIG IS NOT A FIXED NUMBER, and the tests below are written so that it never
has to be. Three builds of that SAME commit measured 20,039,596 bytes / 3,065
entries / 1,994 under ``.claude/worktrees/``; then 4,112 entries six minutes
later; then 48,004,955 bytes / 8,304 entries / 7,233 later the same day. The
commit never changed — agents kept creating worktrees. Read any of those as a
floor observed at a moment, not as a property of the repo; the assertions below
plant a fixed set of probes and count leaks, so what they measure does not
depend on how many sessions happen to be alive.

``.uv-cache`` is the same class and makes the point about the belt sharpest: uv
writes a ``.gitignore`` containing ``*`` INSIDE its cache, so ``git check-ignore``
calls everything in there ignored — and the same ee1623c sdist still shipped five
of those files, because hatchling reads the ROOT ``.gitignore`` and not nested
ones. Git's opinion about a path is not evidence about an artifact.

The first leak was found because the artifact was inspected; this one was found
the same way. Neither was found by a test, which is why the tests below plant.

WHY THIS TEST EXISTS, WHY IT PLANTS FILES, AND WHY IT DELETES ``.gitignore``.
A test that merely builds and asserts "no ``.omc`` in the artifact" is VACUOUS on
a clean checkout: CI's ``actions/checkout`` is a fresh clone, the scratch files
are untracked, so they are not there to leak and the assertion passes on a
completely unfixed tree. So every build below PLANTS the offending files first.

Planting alone is still not enough, and this is the subtle part. Every pattern in
the pyproject ``exclude`` lists is ALSO in ``.gitignore``, and hatchling honours
VCS ignore rules when selecting files — measured directly: strip ``.omc``,
``CLAUDE.md``, ``AGENTS.md`` and ``aelix-session-*.html`` out of
``packages/aelix-coding-agent/pyproject.toml`` entirely and a planted
``agent-replay-*.jsonl`` STILL does not reach the wheel, because ``.gitignore``
catches it. A build run in the live worktree therefore exercises the belt, never
the suspenders, and would keep reporting a clean artifact after the exclude lists
had been mangled beyond repair.

So the fixtures below copy the repo to a temp tree, DELETE ``.gitignore`` there,
and build from the copy. The exclude lists are then the only thing standing
between the planted scratch and the artifact, which is precisely the property
being asserted. Measured on this tree: copy with ``.gitignore`` removed and the
exclude lists stripped -> 7 planted paths leak into the wheel and 64 into the
sdist; restore the exclude lists -> 0 leak either way, and the four content
files below all survive.

Building from a copy has a second payoff. Planting into the LIVE worktree wrote
``CLAUDE.md``, ``AGENTS.md``, ``aelix-session-*.html`` and ``.omc/**`` into the
repo root, and this same change made all four git-ignored — so a leftover after a
SIGKILL (which no ``finally`` can catch) would be invisible to ``git status``,
and a stray root ``AGENTS.md`` is silently ingested as project context by every
subsequent agent run (``cli/agent_context.discover_context_files``). The temp
copy cannot leave anything behind in the worktree at all.

THE OTHER HALF, AND THE DANGEROUS ONE. These distributions are CONTENT carriers,
not just code. ``examples/INDEX.md``, ``examples/echo/aelix-plugin.toml``,
``examples/echo/echo.py`` and ``extensions/api.py`` ship inside the
aelix-coding-agent wheel, and the #117 self-extension signpost cites them by
ABSOLUTE PATH. An over-broad exclusion (``*.md``, ``*.toml``, a careless ``.omc``
glob) drops them silently, and the failure mode is not "broken" — it is
unshippable: an installed agent confidently names files that are not on disk.
So every exclusion assertion here is paired with a presence assertion. Both
directions, or the guard is worth nothing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import unicodedata
import warnings
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every pyproject in the workspace, DISCOVERED rather than listed. A hardcoded
# list is the exact rot this test claims to prevent: `aelix-server` was added to
# `packages/` after the others, and a sixth package added the same way would
# never be visited by a literal dict — the guard would stay green while the new
# wheel shipped `.omc/state/agent-replay-*.jsonl`. Measured both ways: with a
# literal dict an unguarded `packages/aelix-newpkg/` left the whole file green
# while its wheel leaked; with the discovery below the same package fails
# `test_every_build_target_declares_the_exclusions[aelix-newpkg]`.
ALL_PYPROJECTS = {
    (path.parent.name if path.parent != REPO_ROOT else "aelix"): path
    for path in [
        REPO_ROOT / "pyproject.toml",
        *sorted(REPO_ROOT.glob("packages/*/pyproject.toml")),
    ]
}

# Discovery must not be allowed to fail open: an empty or truncated glob would
# vacuum the parametrised test below into nothing and report success.
_KNOWN_PACKAGES = {"aelix", "aelix-ai", "aelix-agent-core", "aelix-coding-agent", "aelix-server"}
assert set(ALL_PYPROJECTS) >= _KNOWN_PACKAGES, (
    f"pyproject discovery lost known packages: {sorted(_KNOWN_PACKAGES - set(ALL_PYPROJECTS))}"
)

CODING_AGENT_REL = Path("packages/aelix-coding-agent/src/aelix_coding_agent")

# Directories that must not be dragged into the build copy: caches and build
# leftovers that would slow the copy down without changing what is measured.
# `__pycache__` is deliberately NOT skipped for correctness reasons — a probe one
# is planted explicitly so its exclusion is still asserted.
#
# `.claude` is skipped for the same reason and one stronger (#143): on a
# maintainer's machine it holds `.claude/worktrees/<name>/` — entire working
# copies of this repo, one per agent session, 28 MB and growing while the suite
# runs. Copying that per module would cost minutes and make the fixture's size
# depend on how many agents happen to be alive. Skipping it cannot weaken the
# assertion, because the `.claude` probes below are planted explicitly; it makes
# the test STRONGER by fixing what is measured instead of sampling the machine.
#
# `.uv-cache` is skipped on the same terms: a repo-local `UV_CACHE_DIR` holds
# downloaded wheels and unpacked sdists and is bounded only by what the machine
# has fetched, so copying it would make the fixture's cost depend on that. Its
# probes are planted explicitly below too.
_COPY_SKIP = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    ".ruff_cache",
    ".pytest_cache",
    "__pycache__",
    "*.egg-info",
    ".claude",
    ".uv-cache",
    # #190 — the maintainer's REAL credentials. `build_tree` deletes the copy's
    # `.gitignore` (see the module docstring), which is precisely what stops git
    # from suppressing this one, and nothing in `_is_developer_state` used to
    # match it — so every local run of this suite wrote a tarball containing
    # ANTHROPIC_API_KEY, GH_TOKEN and PYPI_TOKEN into a pytest temp dir, and
    # every assertion passed. CI never saw it: `actions/checkout` has no `.env`.
    # Skipped here so the secret never leaves the repo directory at all, and a
    # FAKE one is planted below so the assertion still has something to catch.
    ".env",
    ".env.local",
)

# Content files that MUST survive every exclusion, keyed by their in-wheel path.
# The first three are the #114 example corpus; all four are cited by absolute
# path by the #117 signpost.
REQUIRED_WHEEL_CONTENT = (
    "aelix_coding_agent/examples/INDEX.md",
    "aelix_coding_agent/examples/echo/aelix-plugin.toml",
    "aelix_coding_agent/examples/echo/echo.py",
    "aelix_coding_agent/extensions/api.py",
)

# The exclusion patterns every build target must declare. Kept as a set so the
# static check is order-insensitive but membership-exact.
REQUIRED_EXCLUDES = {
    ".claude",
    # Not a duplicate of `.claude` — see `test_the_team_config_negation_does_not_
    # reopen_the_claude_hole` and the long note in the root pyproject. `.gitignore`
    # negates this one path so git may track the team config, and in hatchling's
    # combined spec that file-level negation outranks the directory pattern.
    ".claude/settings.json",
    # #733 (ADR-0303): the committed skill mirror and subagents, negated in
    # `.gitignore` as directories, and the canonical skill tree, which nothing
    # in `.gitignore` covers. A directory negation did not outrank `.claude` in
    # hatchling's spec (measured), so the first two are named for the next
    # negation that might; `.agents` is load-bearing on its own.
    ".claude/skills",
    ".claude/agents",
    ".agents",
    ".uv-cache",
    ".omc",
    "__pycache__",
    "*.pyc",
    "CLAUDE.md",
    "AGENTS.md",
    "aelix-session-*.html",
    ".env",
    ".env.local",
}

_UV = shutil.which("uv")

# The source checkout is required: these tests build the repo. A test run against
# an installed distribution has nothing to build.
pytestmark = pytest.mark.skipif(
    _UV is None or not (REPO_ROOT / "pyproject.toml").is_file(),
    reason="packaging hygiene needs `uv` and the source checkout",
)


def _plant(path: Path, content: str) -> None:
    """Write a scratch file, creating parents. Only ever called inside the copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _is_developer_state(name: str) -> bool:
    """True for an archive member that is a maintainer's local state, not product.

    ONE predicate for both the wheel and the sdist on purpose. It used to be the
    same expression copy-pasted into each test, and #143 showed what that costs:
    adding a new leaking directory means remembering to add it in two places, and
    the half that got forgotten keeps testing green over a real leak.
    """
    parts = Path(name).parts
    return (
        ".claude" in parts
        # #733 — not local state but the project's agent tooling (the canonical
        # skills); tracked in git and never part of a distribution.
        or ".agents" in parts
        or ".uv-cache" in parts
        or ".omc" in parts
        or "__pycache__" in parts
        or name.endswith((".pyc", ".pyo"))
        or Path(name).name in {"CLAUDE.md", "AGENTS.md"}
        or (Path(name).name.startswith("aelix-session-") and name.endswith(".html"))
        # #190 — `.env` / `.env.local` are credentials, not merely local state.
        # `.env.example` is deliberately NOT here: it carries zero values on any
        # line and is the only human-readable enumeration of the supported
        # environment variables, so it ships on purpose.
        or Path(name).name in {".env", ".env.local"}
    )


def _build(args: list[str], cwd: Path, out_dir: Path) -> None:
    assert _UV is not None
    result = subprocess.run(
        [_UV, "build", *args, "--out-dir", str(out_dir)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"`uv build {' '.join(args)}` failed with {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


@pytest.fixture(scope="module")
def build_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the repo with ``.gitignore`` removed and scratch files planted.

    Removing ``.gitignore`` is the whole point: with it in place hatchling's
    VCS-ignore handling suppresses every planted file on its own, so the pyproject
    ``exclude`` lists are never exercised and a mangled exclude list still tests
    green. Without it the exclude lists are the only defence, which is the
    property these tests exist to assert.

    Planted here rather than in the worktree so nothing can be left behind: the
    planted names are all git-ignored now, so a leftover would be invisible to
    ``git status``, and a stray root ``AGENTS.md`` is auto-ingested as project
    context by later agent runs.
    """
    root = tmp_path_factory.mktemp("buildtree") / "repo"
    shutil.copytree(REPO_ROOT, root, ignore=_COPY_SKIP, symlinks=True)

    gitignore = root / ".gitignore"
    assert gitignore.is_file(), "expected a repo .gitignore to remove"
    gitignore.unlink()

    # --- package-tree scratch: the exact four files that leaked, plus a
    # __pycache__/.pyc pair and the agent-instruction filenames.
    pkg = root / CODING_AGENT_REL
    state = pkg / ".omc" / "state"
    _plant(state / "mission-state.json", '{"probe": "packaging-hygiene"}')
    _plant(state / "last-tool-error.json", '{"probe": "packaging-hygiene"}')
    _plant(state / "subagent-tracking.json", '{"probe": "packaging-hygiene"}')
    _plant(
        state / "agent-replay-00000000-0000-0000-0000-000000000000.jsonl",
        '{"probe": "maintainer transcript content"}\n',
    )
    _plant(pkg / "__pycache__" / "packaging_probe.cpython-311.pyc", "probe")
    _plant(pkg / "AGENTS.md", "# probe\n")
    _plant(pkg / "CLAUDE.md", "# probe\n")
    _plant(pkg / "aelix-session-packaging-probe.html", "<html></html>")
    # #143: a nested `.claude` reaches the WHEEL, the same way the nested `.omc`
    # did. Planted under the packaged tree so the wheel assertion is not relying
    # on the root probes below, which only the sdist ever sees.
    _plant(pkg / ".claude" / "settings.local.json", '{"probe": "packaging-hygiene"}')
    _plant(pkg / ".uv-cache" / "CACHEDIR.TAG", "Signature: 8a477f597d28d172\n")
    # #733: a nested `.agents/skills` reaches the wheel unless `.agents` is
    # excluded; nothing in `.gitignore` names it.
    _plant(pkg / ".agents" / "skills" / "packaging-probe" / "SKILL.md", "---\nname: packaging-probe\n---\n")

    # --- repo-root scratch: what the sdist swept in.
    _plant(
        root / ".omc" / "specs" / "packaging-probe-internal-plan.md",
        "# internal sprint plan, never publish\n",
    )
    _plant(root / ".omc" / "wiki" / "packaging-probe-session.md", "session log\n")
    _plant(root / "CLAUDE.md", "# probe\n")
    _plant(root / "AGENTS.md", "# probe\n")
    _plant(root / "aelix-session-packaging-probe.html", "<html></html>")
    # #190 — stand-ins for the real credential files `_COPY_SKIP` now refuses to
    # copy. Deliberately shaped like the real thing (a `KEY=value` line) so a
    # future secret-scanning assertion has something to find, and deliberately
    # fake so this repo never writes a live key to a temp dir.
    _plant(root / ".env", "ANTHROPIC_API_KEY=sk-probe-not-a-real-key\n")
    _plant(root / ".env.local", "PYPI_TOKEN=pypi-probe-not-a-real-token\n")

    # --- #143: the repo-root `.claude/` tree, which is what the sdist swept in.
    # `.claude/worktrees/<name>/` is a COMPLETE working copy of this repo that
    # Claude Code checks out per agent session, so the leak is not a few stray
    # settings files — it is the whole repository, N times over, including
    # whatever uncommitted work those sessions are holding. Two representative
    # files from such a copy, plus the settings file that was the only `.claude`
    # path previously git-ignored.
    _plant(root / ".claude" / "settings.local.json", '{"probe": "packaging-hygiene"}')
    worktree = root / ".claude" / "worktrees" / "packaging-probe-session"
    _plant(worktree / "pyproject.toml", '[project]\nname = "probe-working-copy"\n')
    _plant(
        worktree / "packages" / "aelix-coding-agent" / "src" / "probe_unreleased.py",
        "# uncommitted work in progress, never publish\n",
    )

    # --- #733: the committed agent tooling. `.agents/skills` is canonical and
    # tracked, so a clean checkout HAS it, and only the `.agents` exclude keeps
    # it out; the `.claude` mirror and subagents are tracked too.
    _plant(root / ".agents" / "skills" / "packaging-probe" / "SKILL.md", "---\nname: packaging-probe\n---\n")
    _plant(root / ".claude" / "skills" / "packaging-probe" / "SKILL.md", "---\nname: packaging-probe\n---\n")
    _plant(root / ".claude" / "agents" / "packaging-probe.md", "---\nname: packaging-probe\n---\n")

    # --- #143 follow-up: `.uv-cache`, a repo-local `UV_CACHE_DIR`. The exact five
    # names main's real sdist shipped at ee1623c. Note `.uv-cache/.gitignore`
    # among them: uv writes `*` into it, git therefore reports every one of these
    # ignored, and hatchling shipped them anyway because it reads only the ROOT
    # `.gitignore`. Planting the real names keeps that distinction on the record.
    cache = root / ".uv-cache"
    _plant(cache / ".gitignore", "*\n")
    _plant(cache / ".lock", "")
    _plant(cache / "CACHEDIR.TAG", "Signature: 8a477f597d28d172\n")
    _plant(cache / "sdists-v9" / ".gitignore", "")
    _plant(cache / "interpreter-v4" / "probe" / "probe.msgpack", "probe")
    return root


def test_the_build_tree_really_has_no_gitignore_belt(build_tree: Path) -> None:
    """Guards the guard: if ``.gitignore`` survives into the copy, or the planted
    scratch is missing, every assertion below silently becomes vacuous."""
    assert not (build_tree / ".gitignore").exists(), (
        "the build copy still has a .gitignore — hatchling would suppress the "
        "planted files on its own and the exclusion assertions would prove nothing"
    )
    for probe in (
        build_tree / CODING_AGENT_REL / ".omc" / "state" / "mission-state.json",
        build_tree / CODING_AGENT_REL / "__pycache__" / "packaging_probe.cpython-311.pyc",
        build_tree / CODING_AGENT_REL / "AGENTS.md",
        build_tree / CODING_AGENT_REL / ".claude" / "settings.local.json",
        build_tree / CODING_AGENT_REL / ".uv-cache" / "CACHEDIR.TAG",
        build_tree / CODING_AGENT_REL / ".agents" / "skills" / "packaging-probe" / "SKILL.md",
        build_tree / ".agents" / "skills" / "packaging-probe" / "SKILL.md",
        build_tree / ".claude" / "skills" / "packaging-probe" / "SKILL.md",
        build_tree / ".claude" / "agents" / "packaging-probe.md",
        build_tree / ".omc" / "specs" / "packaging-probe-internal-plan.md",
        build_tree / ".claude" / "worktrees" / "packaging-probe-session" / "pyproject.toml",
        build_tree / ".uv-cache" / "CACHEDIR.TAG",
        build_tree / "aelix-session-packaging-probe.html",
        build_tree / ".env",
        build_tree / ".env.local",
    ):
        assert probe.is_file(), f"scratch probe was not planted: {probe}"

    # The other half of the #190 finding: the REAL one must not have travelled.
    # `_COPY_SKIP` is what stops it, and a skip that silently stopped working
    # would leave this test green while the secret sat in /tmp.
    if (REPO_ROOT / ".env").is_file():
        assert (build_tree / ".env").read_text() != (
            REPO_ROOT / ".env"
        ).read_text(), "the maintainer's real .env was copied into the build tree"


@pytest.fixture(scope="module")
def coding_agent_wheel(
    build_tree: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[list[str]]:
    """Build the aelix-coding-agent wheel from the planted, belt-less copy."""
    out_dir = tmp_path_factory.mktemp("wheel")
    _build(["--package", "aelix-coding-agent"], build_tree, out_dir)
    wheels = list(out_dir.glob("aelix_coding_agent-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as zf:
        yield zf.namelist()


@pytest.fixture(scope="module")
def coding_agent_direct_wheel(
    build_tree: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[list[str]]:
    """The same wheel built straight from the source tree (``uv build --wheel``), not from its sdist.

    ``--package`` builds the sdist first and the wheel from it, so a path the
    sdist list excludes never reaches the wheel's own list. Built directly, only
    the wheel's exclude list stands between a nested ``.agents`` (or ``.omc``,
    ``.claude``) and the artifact (#733 verify F12: without ``.agents`` there,
    this build shipped ``aelix_coding_agent/.agents/skills/p/SKILL.md``).
    """
    out_dir = tmp_path_factory.mktemp("directwheel")
    _build(["--wheel", "--package", "aelix-coding-agent"], build_tree, out_dir)
    wheels = list(out_dir.glob("aelix_coding_agent-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as zf:
        yield zf.namelist()


def test_the_wheel_built_directly_carries_no_developer_state(coding_agent_direct_wheel: list[str]) -> None:
    leaked = [name for name in coding_agent_direct_wheel if _is_developer_state(name)]
    assert leaked == [], f"developer state leaked into the directly built aelix-coding-agent wheel: {leaked}"
    # Guards the guard: it is the real package, built from the planted tree
    # (test_the_build_tree_really_has_no_gitignore_belt checks the probes are there).
    assert "aelix_coding_agent/__init__.py" in coding_agent_direct_wheel


@pytest.fixture(scope="module")
def root_sdist(
    build_tree: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[list[str]]:
    """Build the root sdist from the planted, belt-less copy."""
    out_dir = tmp_path_factory.mktemp("sdist")
    _build(["--sdist"], build_tree, out_dir)
    tarballs = list(out_dir.glob("aelix-*.tar.gz"))
    assert len(tarballs) == 1, f"expected exactly one sdist, got {tarballs}"
    with tarfile.open(tarballs[0]) as tf:
        # Strip the `aelix-<version>/` prefix so assertions read as repo paths.
        yield [name.split("/", 1)[1] for name in tf.getnames() if "/" in name]


def test_wheel_carries_no_developer_state(coding_agent_wheel: list[str]) -> None:
    """No planted scratch file reaches the wheel."""
    leaked = [name for name in coding_agent_wheel if _is_developer_state(name)]
    assert leaked == [], (
        "developer state leaked into the aelix-coding-agent wheel: "
        f"{leaked}\nAn `agent-replay-*.jsonl` here is maintainer transcript "
        "content — an information-disclosure bug, not just untidiness."
    )


def test_wheel_still_carries_the_signpost_content_files(coding_agent_wheel: list[str]) -> None:
    """The exclusions did not take the example corpus down with them.

    Guards the #117 self-extension signpost, which cites these by ABSOLUTE PATH.
    If this fails, the exclusion patterns were widened (``*.md``, ``*.toml``, an
    over-eager glob) — narrow them, do not delete this test.
    """
    present = set(coding_agent_wheel)
    missing = [path for path in REQUIRED_WHEEL_CONTENT if path not in present]
    assert missing == [], (
        f"content files dropped from the wheel: {missing}\n"
        "The #117 signpost names these by absolute path, so an installed agent "
        "would point users at files that do not exist. Narrow the `exclude` "
        "patterns in packages/aelix-coding-agent/pyproject.toml."
    )


def test_wheel_still_carries_both_top_level_packages(coding_agent_wheel: list[str]) -> None:
    """ADR-0197: ``aelix_agents`` is a second import package inside this wheel."""
    roots = {name.split("/", 1)[0] for name in coding_agent_wheel if "/" in name}
    assert "aelix_coding_agent" in roots
    assert "aelix_agents" in roots, (
        "aelix_agents vanished from the wheel — `from aelix_agents import "
        "AgentsExtension` would fail for every installed user while passing in "
        "every source checkout."
    )


def test_sdist_carries_no_developer_state(root_sdist: list[str]) -> None:
    """No repo-root developer state reaches the sdist.

    The sdist default file set is the whole repo root, which made it the worse
    leak of the two: internal sprint plans, review memos and session logs, then
    (#143) whole ``.claude/worktrees/<name>/`` working copies of the repo, and
    alongside them a repo-local ``.uv-cache/`` that git considered fully ignored.
    """
    leaked = [name for name in root_sdist if _is_developer_state(name)]
    assert leaked == [], f"developer state leaked into the root sdist: {leaked}"


#: #777 — the gated test launcher and the ``sitecustomize`` it puts first on a
#: delegated child's ``PYTHONPATH``. Python imports a ``sitecustomize.py`` it
#: finds on the path at every start-up, so one that shipped would run in every
#: interpreter of the installing user's environment. #777's PRs c1 and c2 (one PR,
#: the lead's call) add the ``-e`` extension whose job swallows every stop (L6) and
#: the TUI launcher, test-only as the launcher is.
GATED_LAUNCHER_FILES = (
    "tests/jobs/gated_cli.py",
    "tests/jobs/gated_site/sitecustomize.py",
    "tests/jobs/fixtures/hold_kind_ext.py",
    "tests/jobs/gated_tui_child.py",
)


def test_the_gated_test_launcher_ships_in_no_artifact(
    root_sdist: list[str],
    coding_agent_wheel: list[str],
    coding_agent_direct_wheel: list[str],
    release_sdist: tuple[Path, list[str]],
) -> None:
    for path in GATED_LAUNCHER_FILES:
        assert (REPO_ROOT / path).is_file(), f"{path} moved: name it here again"
    basenames = {path.rsplit("/", 1)[-1] for path in GATED_LAUNCHER_FILES}
    for artifact in (root_sdist, coding_agent_wheel, coding_agent_direct_wheel, release_sdist[1]):
        shipped = [name for name in artifact if name.rsplit("/", 1)[-1] in basenames]
        assert shipped == [], f"the gated test launcher shipped: {shipped}"


def test_sdist_still_carries_sources_and_licensing(root_sdist: list[str]) -> None:
    """The sdist exclusions did not amputate the distribution itself."""
    present = set(root_sdist)
    required = [
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "NOTICE",
        "THIRD-PARTY-NOTICES.md",
        "packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py",
        "packages/aelix-coding-agent/src/aelix_coding_agent/examples/INDEX.md",
        "packages/aelix-coding-agent/src/aelix_coding_agent/examples/echo/echo.py",
        "packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py",
    ]
    missing = [path for path in required if path not in present]
    assert missing == [], f"the sdist exclusions dropped real distribution content: {missing}"


@pytest.fixture(scope="module")
def belted_root_sdist(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[str]]:
    """The root sdist built with ``.gitignore`` KEPT, plus a `.claude/settings.json`.

    THE ONE FIXTURE HERE THAT DOES NOT DELETE THE BELT, and it exists because
    deleting the belt hides a whole class of defect. Every other build above
    removes ``.gitignore`` so the pyproject ``exclude`` lists are measured alone —
    right for asking "do the excludes work", and structurally blind to any way in
    which ``.gitignore`` can make the artifact WORSE.

    It can. hatchling does not consult the two lists in sequence: it concatenates
    the ``.gitignore`` lines and the ``exclude`` patterns into a single
    ``pathspec.GitIgnoreSpec``, and in that spec a pattern matching the FILE
    outranks a pattern matching only its parent DIRECTORY irrespective of order.
    ``.gitignore`` negates ``/.claude/settings.json`` on purpose — that file is
    Claude Code's checked-in TEAM config and git has to be allowed to track it —
    and that file-level negation therefore beat the directory-level ``.claude``
    in the pyprojects. Measured, not reasoned: with the negation added and only
    ``.claude`` declared, a real ``uv build --sdist`` put ``.claude/settings.json``
    back into the tarball — re-opened by the very branch that closed #143.

    So this fixture reproduces the maintainer's actual build — belt on, team
    config present — and the test below is what stops a future ``.gitignore``
    negation from re-opening #143 one path at a time.
    """
    root = tmp_path_factory.mktemp("beltedtree") / "repo"
    shutil.copytree(REPO_ROOT, root, ignore=_COPY_SKIP, symlinks=True)
    assert (root / ".gitignore").is_file(), "the belt is the point of this fixture"

    _plant(root / ".claude" / "settings.json", '{"probe": "team config, git-tracked"}')
    _plant(root / ".claude" / "settings.local.json", '{"probe": "personal, ignored"}')
    _plant(
        root / ".claude" / "worktrees" / "belted-probe" / "probe_unreleased.py",
        "# uncommitted work in progress, never publish\n",
    )
    # #733: the paths `.gitignore` now negates as directories (the skill mirror
    # and the subagents) and the canonical skill tree it never ignored. With the
    # belt on, git would track all three; none may ship.
    _plant(root / ".claude" / "skills" / "belted-probe" / "SKILL.md", "---\nname: belted-probe\n---\n")
    _plant(root / ".claude" / "agents" / "belted-probe.md", "---\nname: belted-probe\n---\n")
    _plant(root / ".agents" / "skills" / "belted-probe" / "SKILL.md", "---\nname: belted-probe\n---\n")

    out_dir = tmp_path_factory.mktemp("beltedsdist")
    _build(["--sdist"], root, out_dir)
    tarballs = list(out_dir.glob("aelix-*.tar.gz"))
    assert len(tarballs) == 1, f"expected exactly one sdist, got {tarballs}"
    with tarfile.open(tarballs[0]) as tf:
        yield [name.split("/", 1)[1] for name in tf.getnames() if "/" in name]


def test_the_team_config_negation_does_not_reopen_the_claude_hole(
    belted_root_sdist: list[str],
) -> None:
    """A git-trackable ``.claude/settings.json`` still reaches no artifact."""
    leaked = [name for name in belted_root_sdist if _is_developer_state(name)]
    assert leaked == [], (
        "developer state leaked into the sdist built WITH .gitignore in place: "
        f"{leaked}\nA `.gitignore` negation outranked a pyproject `exclude` "
        "directory pattern. Declare the exact negated path in every pyproject "
        "`exclude` list too — do not remove the negation, and do not widen the "
        "patterns."
    )


def test_the_belted_sdist_is_otherwise_a_real_sdist(belted_root_sdist: list[str]) -> None:
    """Guards the guard: an empty or amputated tarball would pass the test above."""
    present = set(belted_root_sdist)
    missing = [
        path
        for path in ("pyproject.toml", "README.md", "LICENSE", ".gitignore")
        if path not in present
    ]
    assert missing == [], f"the belted sdist is not a real sdist: {missing}"


@pytest.fixture(scope="module")
def sdist_built_from_under_a_dot_claude_path(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[list[str]]:
    """The root sdist, belt ON, built from a root that itself sits under ``.claude/``.

    Every other fixture here builds from a neutral temp directory, and that is
    exactly why none of them can see this defect. THIS repo is developed from
    agent worktrees under ``.claude/worktrees/<session>/``, so a maintainer's
    real ``uv build`` frequently runs with ``.claude`` sitting in its own root
    path — a configuration the rest of this file never reproduces.

    In that configuration an UNANCHORED ``.gitignore`` rule naming ``.claude``
    matches the build root itself, and hatchling responds by discarding the whole
    file for that build. Everything the belt alone excludes then ships. Measured
    twice while writing #143, with ``.claude/`` and again with ``**/*/.claude/``:
    both times a planted ``.env.local`` reached the tarball, in a repo whose real
    ``.env`` holds provider keys and a PyPI token. Anchoring to ``/.claude/``
    fixed it in the same tree with the same command.

    ``.env.local`` is the probe on purpose: it is excluded by ``.gitignore`` and
    by NOTHING in any pyproject, so it can only survive the trip if the belt was
    honoured. A probe covered by both lists would pass even with the belt thrown
    away, and prove nothing.
    """
    root = tmp_path_factory.mktemp("dotclaudetree") / ".claude" / "worktrees" / "probe"
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO_ROOT, root, ignore=_COPY_SKIP, symlinks=True)
    assert (root / ".gitignore").is_file(), "the belt is the point of this fixture"
    assert ".claude" in root.parts, "the build root must sit under a .claude path"

    _plant(root / ".env.local", "SECRET_PROBE=must-never-ship\n")

    out_dir = tmp_path_factory.mktemp("dotclaudesdist")
    _build(["--sdist"], root, out_dir)
    tarballs = list(out_dir.glob("aelix-*.tar.gz"))
    assert len(tarballs) == 1, f"expected exactly one sdist, got {tarballs}"
    with tarfile.open(tarballs[0]) as tf:
        yield [name.split("/", 1)[1] for name in tf.getnames() if "/" in name]


def test_a_build_root_under_dot_claude_still_honours_the_belt(
    sdist_built_from_under_a_dot_claude_path: list[str],
) -> None:
    """An ignore rule must not match the build root — it costs the whole file."""
    names = sdist_built_from_under_a_dot_claude_path
    leaked = [name for name in names if name == ".env.local" or name.startswith(".venv/")]
    assert leaked == [], (
        "a file excluded ONLY by .gitignore reached the sdist when the build root "
        f"sat under a `.claude/` path: {leaked}\nSome rule in .gitignore now "
        "matches the build root itself, so hatchling discarded the entire file "
        "for this build. Anchor it (`/.claude/`, not `.claude/` and not "
        "`**/*/.claude/`). Coverage for nested copies belongs in the pyproject "
        "`exclude` lists, which are read as build config and do not trip this."
    )
    assert _is_developer_state_absent(names), (
        f"developer state also leaked: {[n for n in names if _is_developer_state(n)]}"
    )


def _is_developer_state_absent(names: list[str]) -> bool:
    """Small helper so the assertion above reads as one thought."""
    return not any(_is_developer_state(name) for name in names)


@pytest.mark.parametrize("name", sorted(ALL_PYPROJECTS))
def test_every_build_target_declares_the_exclusions(name: str) -> None:
    """Static rot-guard: a new package cannot silently re-open the hole.

    The build tests above only cover aelix-coding-agent and the root sdist —
    building all ten artifacts per suite run is not worth the minutes. This
    reads the remaining targets' declarations instead, over a DISCOVERED set of
    pyprojects so a package added tomorrow is covered without anyone remembering
    to add it here.
    """
    with ALL_PYPROJECTS[name].open("rb") as fh:
        targets = tomllib.load(fh).get("tool", {}).get("hatch", {}).get("build", {}).get(
            "targets", {}
        )
    assert targets, f"{name}: no [tool.hatch.build.targets.*] section at all"
    for target_name in ("wheel", "sdist"):
        target = targets.get(target_name)
        assert target is not None, (
            f"{name}: no [tool.hatch.build.targets.{target_name}] section. Without "
            "it the target falls back to hatchling's defaults, which sweep in "
            "whatever developer state happens to be on disk."
        )
        declared = set(target.get("exclude", []))
        missing = REQUIRED_EXCLUDES - declared
        assert not missing, (
            f"{name}: [tool.hatch.build.targets.{target_name}] is missing "
            f"exclude patterns {sorted(missing)}"
        )


# === #190 — the artifact the release actually publishes ======================
#
# WHY A FIFTH FIXTURE RATHER THAN NEW ASSERTIONS ON THE FOUR ABOVE. Those four
# build from a `copytree` of the LIVE worktree, which is the right source for
# what they assert (the exclude lists hold without the .gitignore belt) and the
# wrong source for anything about size or inventory: the same commit measured
# 6,341,061 B from a clean worktree and 10,325,521 B from a checkout carrying a
# maintainer's untracked scratch. The module docstring above says exactly this —
# "HOW BIG IS NOT A FIXED NUMBER" — and it is correct FOR THAT SOURCE. A build
# seeded from `git ls-files` is a different animal: its file set is a pure
# function of what is tracked, so "how big" and "what is in it" become stable
# properties worth asserting.
#
# It is also the only fixture here that builds what `release.yml` builds.
# `actions/checkout` is a fresh clone, so the release artifact contains tracked
# files and nothing else — which is why #190's headline (10.2 MB, a scraped
# GOV.UK captcha page, 3.7 MB of abandoned logo drafts) described a dirty local
# build and not the published tarball. Every one of those files is untracked.
# The real, tracked overage was `tests/` at 551 members / 24.5% of the tarball.


def _tracked_top_level() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    return {line.split("/", 1)[0] for line in out if line.strip()}


# Every top-level entry the published sdist is allowed to contain. An EXACT
# allow-list, not a "known bad" list: a predicate that enumerates known-bad will
# always trail the next stray file, and #190 is what that looks like — the
# existing `_is_developer_state` is a real gate that does exactly what #111 B-7
# built it for, and it passed a tarball with 551 test files in it because
# nothing about `tests/` looks like developer state.
#
# Measured against history: 14 of these 24 entries were added after the initial
# commit, on 7 distinct dates over 93 days. So this list needs a one-line edit
# roughly every two weeks of active development — always alongside a deliberate
# new top-level file, which is exactly the moment someone should be asked "is
# this meant to be published?". Files added under `docs/`, `packages/`, `tests/`
# and friends never touch it.
ALLOWED_SDIST_TOP_LEVEL = frozenset(
    {
        # metadata hatchling generates
        "PKG-INFO",
        # the project itself
        "packages",
        "src",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        # what a consumer reads
        "README.md",
        "README.ko.md",
        "CHANGELOG.md",
        "LICENSE",
        "NOTICE",
        "THIRD-PARTY-NOTICES.md",
        "TRADEMARK.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "RELEASING.md",
        ".env.example",
        "docs",
        "site",
        "sbom",
        # how it is built, installed and released
        ".github",
        ".gitignore",
        # #485: pins the guides to LF; a consumer who unpacks the sdist into a
        # checkout gets the same line endings `read`'s cap is measured on
        ".gitattributes",
        "install.sh",
        "install.ps1",
        "scripts",
    }
)

# +26% over the artifact measured when this line was written (4,756,148 B), so
# ordinary growth never trips it. Chosen against the failure it exists to catch
# rather than against the current number alone: it still fails the pre-#190
# tarball (6,343,886 B) and the dirty-worktree build (10,308,093 B) that
# produced the issue's headline. A cap loose enough to admit those would be
# decorative.
#
# #387 — the cap was reached by organic doc growth, not by a leak: main aab1f210
# built 5,974,799 B with LF endings, and a Windows checkout (CRLF on every text
# file — this fixture builds from the WORKING TREE, so it sees them) adds about
# 24 KB: the #288 branch failed both windows legs of CI 37507000678 at
# 6,004,132 B, against 5,980,113 B for that tree built locally with LF. The release itself builds
# on Linux (`release.yml`). Rather than raise the cap, #387 dropped
# `docs/assets/demo.gif` and its tooling from the sdist: 3,666,895 B LF and
# 3,690,549 B with every text file converted to CRLF. The cap stays where it is
# because its reasons above still hold.
#
# #816 — reached again six weeks later, by ADRs and code about equally (a per-file
# gzip estimate of the growth since #387): main
# a1606f58 built 5,942,894 B LF and 5,982,322 B CRLF (#387's conversion of
# every UTF-8 text file, guides included, which a real Windows checkout keeps
# LF through .gitattributes), and the #623/#624 branch e2922307 built
# 6,059,123 B LF / 6,098,770 B CRLF, over the cap.
# `docs/decisions/` was 2,182,953 B of a1606f58's tarball. Rather than raise
# the cap, #816 dropped the ADRs from the sdist (they stay in git): with only
# that exclude, a1606f58 builds 3,759,941 B LF / 3,784,399 B CRLF and e2922307
# 3,815,018 B / 3,839,299 B, and the only members that left are the ones under
# `docs/decisions/` (294 at a1606f58). The cap stays, for the same reasons.
MAX_SDIST_BYTES = 6_000_000

#: #831: when this names a file, the size test writes `{"size": N, "cap": C}` there
#: right after it measures. `scripts/preflight.py`'s `sdist-size` gate names one and
#: passes only on what it finds there, so a run whose size test never measured writes
#: no report and cannot pass it (code that writes the report itself, a plugin say, can).
SIZE_REPORT_ENV = "AELIX_SDIST_SIZE_REPORT"


def tracked_on_disk(repo: Path) -> bytes:
    """The paths `git ls-files` lists in *repo* that are on disk, NUL-terminated for `tar -T`.

    A tracked file deleted or moved without `git rm` or `git mv` is still listed;
    `tar` archives the rest but exits non-zero, which `release_tar`'s `check=True` raised
    (#831: the `sdist-size` gate failed on a plain `rm` of a guide). Dropping those builds
    the tracked files as they are on
    disk; on a checkout with nothing deleted the list is `git ls-files` unchanged.
    `lexists`, so a tracked symlink is kept even when its target is gone.
    """
    names = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    root = os.fsencode(repo)
    return b"".join(
        name + b"\0"
        for name in names.split(b"\0")
        if name and os.path.lexists(os.path.join(root, name))
    )


def release_tar(repo: Path) -> bytes:
    """The tar stream the release tree is seeded with: *repo*'s tracked files on disk (#831)."""

    return subprocess.run(
        ["tar", "--null", "-T", "-", "-cf", "-"],
        cwd=repo,
        input=tracked_on_disk(repo),
        capture_output=True,
        check=True,
    ).stdout


@pytest.fixture(scope="module")
def release_sdist(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    """The root sdist, built from tracked files only — the release path.

    Seeded with `git ls-files` piped through tar rather than `git archive HEAD`
    so that UNCOMMITTED edits to tracked files are measured. Otherwise this gate
    would only ever see the previous commit and could not fail on the change
    that is being written. A tracked file missing on disk is left out
    (`release_tar`, `tracked_on_disk`).

    `.gitignore` is deliberately KEPT here (unlike `build_tree`): the release
    build has it, so removing it would measure something the release never does.
    """
    root = tmp_path_factory.mktemp("release") / "repo"
    root.mkdir(parents=True)
    tar_out = release_tar(REPO_ROOT)
    subprocess.run(
        ["tar", "-x", "-C", str(root)], input=tar_out, capture_output=True, check=True
    )

    out_dir = root.parent / "dist"
    _build(["--sdist"], root, out_dir)
    tarballs = sorted(out_dir.glob("aelix-*.tar.gz"))
    assert len(tarballs) == 1, f"expected one root sdist, got {tarballs}"
    with tarfile.open(tarballs[0]) as tf:
        members = tf.getnames()
    return tarballs[0], members


def test_the_release_sdist_fixture_is_seeded_from_tracked_files_only(
    release_sdist: tuple[Path, list[str]],
) -> None:
    """Guards the guard, the same way `build_tree` does.

    A fixture that silently produced an empty or tiny tarball would make both
    assertions below pass while measuring nothing.
    """
    _, members = release_sdist
    assert len(members) > 400, f"only {len(members)} members — fixture is broken"
    for required in (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py",
    ):
        assert any(
            n.split("/", 1)[1] == required for n in members if "/" in n
        ), f"the release sdist is missing {required}"


def test_the_release_sdist_carries_nothing_it_does_not_declare(
    release_sdist: tuple[Path, list[str]],
) -> None:
    """The inventory assertion #190 asks for."""
    _, members = release_sdist
    top = {n.split("/", 1)[1].split("/", 1)[0] for n in members if "/" in n}
    stray = sorted(top - ALLOWED_SDIST_TOP_LEVEL)
    assert not stray, (
        "the published sdist would carry top-level entries nothing declares: "
        f"{stray}. Either exclude them in [tool.hatch.build.targets.sdist] or "
        "add them to ALLOWED_SDIST_TOP_LEVEL — deliberately, having decided "
        "they belong in a permanent public artifact."
    )
    # And the three the issue named, by name, so a regression reads as itself
    # rather than as a nameless allow-list violation.
    for gone in ("tests", "citations.lock.json", "SLICE-STATUS.md"):
        assert gone not in top, f"{gone} is back in the published sdist"
    # #733: tracked, and still not published — the hook settings now, the
    # skills and subagents once they are committed.
    for gone in (".claude", ".agents"):
        assert gone not in top, f"{gone}/ reached the published sdist"
    # `.env.example` ships on purpose; a blanket `.env*` exclusion would take it
    # with the credentials and this is where that would be noticed.
    assert ".env.example" in top
    assert ".env" not in top


def test_the_release_sdist_stays_under_the_size_cap(
    release_sdist: tuple[Path, list[str]],
) -> None:
    """The bulk assertion #190 asks for.

    PyPI never allows a filename to be re-uploaded, so the first `0.1.0` sdist
    is permanent. This is the cheapest possible check that nobody has quietly
    added a few megabytes to it.

    #831: with `AELIX_SDIST_SIZE_REPORT` set, the size goes to that file before
    it is compared, and `scripts/preflight.py`'s `sdist-size` gate reads it there:
    #387 and #816 both reached the cap by ordinary growth, and nothing said how
    close it was until a run failed. The comparison is `pytest.fail`, not an
    `assert`, which `python -O` and `--assert=plain` together remove.
    """
    path, _ = release_sdist
    size = path.stat().st_size
    report = os.environ.get(SIZE_REPORT_ENV)
    if report:
        Path(report).write_text(
            json.dumps({"size": size, "cap": MAX_SDIST_BYTES}), encoding="utf-8"
        )
    if size > MAX_SDIST_BYTES:
        pytest.fail(
            f"the published sdist is {size:,} B, over the {MAX_SDIST_BYTES:,} B cap. "
            "Find what grew (`tar -tvzf` and sort by size) before raising this."
        )


# The size test called with a stand-in sdist of a chosen size (#831): what
# preflight's `sdist-size` gate reads is the report, so the report and the cap are
# pinned here without building anything.


def _sdist_of(tmp_path: Path, size: int) -> tuple[Path, list[str]]:
    path = tmp_path / "aelix-0.0.0.tar.gz"
    with path.open("wb") as fh:
        fh.truncate(size)
    return path, []


@pytest.mark.parametrize("size", [MAX_SDIST_BYTES - 2_040_336, MAX_SDIST_BYTES])
def test_the_size_test_reports_the_size_and_the_cap_and_passes_up_to_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    report = tmp_path / "report.json"
    monkeypatch.setenv(SIZE_REPORT_ENV, str(report))
    with warnings.catch_warnings():
        # A warning on a pass was #831's first way to report; `-W error` failed it.
        warnings.simplefilter("error")
        test_the_release_sdist_stays_under_the_size_cap(_sdist_of(tmp_path, size))
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "size": size,
        "cap": MAX_SDIST_BYTES,
    }


def test_the_size_test_reports_before_it_fails_over_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    monkeypatch.setenv(SIZE_REPORT_ENV, str(report))
    over = MAX_SDIST_BYTES + 1
    with pytest.raises(
        pytest.fail.Exception,
        match=r"^the published sdist is 6,000,001 B, over the 6,000,000 B cap\. ",
    ):
        test_the_release_sdist_stays_under_the_size_cap(_sdist_of(tmp_path, over))
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "size": over,
        "cap": MAX_SDIST_BYTES,
    }


def test_the_size_test_writes_no_report_unless_one_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SIZE_REPORT_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    sdist = _sdist_of(tmp_path, 10)
    test_the_release_sdist_stays_under_the_size_cap(sdist)
    assert sorted(p.name for p in tmp_path.iterdir()) == [sdist[0].name]


def test_the_cap_holds_under_python_O(tmp_path: Path) -> None:
    """Codex C2: `PYTHONOPTIMIZE=1` with `--assert=plain` removed a bare `assert`, and an
    oversize sdist passed. The size test is run under `python -O` here, where an
    `assert` in it is gone (the control below proves the flag took)."""

    script = tmp_path / "under_o.py"
    script.write_text(
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "try:\n"
        "    assert False\n"
        "except AssertionError:\n"
        "    raise SystemExit(4)\n"
        "spec = importlib.util.spec_from_file_location('hygiene_under_o', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "try:\n"
        "    module.test_the_release_sdist_stays_under_the_size_cap((Path(sys.argv[2]), []))\n"
        "except pytest.fail.Exception as exc:\n"
        "    print(exc)\n"
        "    raise SystemExit(3)\n",
        encoding="utf-8",
    )
    sdist, _ = _sdist_of(tmp_path, MAX_SDIST_BYTES + 1)
    env = {k: v for k, v in os.environ.items() if k != SIZE_REPORT_ENV}
    proc = subprocess.run(
        [sys.executable, "-O", str(script), __file__, str(sdist)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "over the 6,000,000 B cap" in proc.stdout


def test_a_tracked_file_missing_on_disk_is_left_out_of_the_release_tree(
    tmp_path: Path,
) -> None:
    """#831 F1: an unstaged delete or a plain `mv` of a tracked file made the seeding `tar` exit non-zero."""

    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1", GIT_CEILING_DIRECTORIES=str(tmp_path))
    for name in ("kept.md", "edited.md", "deleted.md", "moved.md"):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
    subprocess.run([*git, "init", "-q"], cwd=repo, env=env, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=repo, env=env, check=True)
    posix = sys.platform != "win32"
    if posix:
        # A tracked symlink whose target is gone is still on disk, and tar archives it.
        os.symlink("nowhere.md", repo / "dangling.md")
        subprocess.run([*git, "add", "-A"], cwd=repo, env=env, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "a link"], cwd=repo, env=env, check=True)
    (repo / "edited.md").write_bytes(b"edited, not staged\n")  # bytes: write_text is CRLF on win32
    (repo / "deleted.md").unlink()
    (repo / "moved.md").rename(repo / "renamed.md")  # untracked now

    names = tracked_on_disk(repo)
    assert names == (b"dangling.md\0" if posix else b"") + b"edited.md\0kept.md\0"
    tar_out = release_tar(repo)  # what the fixture seeds the release tree with
    # Unpacked the way the fixture unpacks it (macOS tar adds `._` members for
    # extended attributes and folds them back in on extraction).
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(["tar", "-x", "-C", str(out)], input=tar_out, capture_output=True, check=True)
    assert sorted(p.name for p in out.iterdir()) == ["dangling.md"] * posix + ["edited.md", "kept.md"]
    assert (out / "edited.md").read_bytes() == b"edited, not staged\n"


def test_the_inventory_check_catches_a_stray(
    release_sdist: tuple[Path, list[str]],
) -> None:
    """The positive control. A clean inventory proves nothing on its own.

    Runs the same set arithmetic the real assertion runs, over the real member
    list plus one planted name, so a refactor that broke the top-level parse
    (an off-by-one on the `split`, say) fails here rather than going quietly
    permissive.
    """
    _, members = release_sdist
    planted = [*members, "aelix-0.1.0b1/uk4414917.html"]
    top = {n.split("/", 1)[1].split("/", 1)[0] for n in planted if "/" in n}
    assert sorted(top - ALLOWED_SDIST_TOP_LEVEL) == ["uk4414917.html"]


# #387 — the demo recording and the tooling that produces it stay in git (the
# READMEs load the GIF from raw.githubusercontent.com) but not in the sdist.
DEMO_ONLY_PATHS = (
    "docs/assets/demo.gif",
    "docs/assets/demo.tape",
    "docs/assets/dedup_frames.py",
    "docs/assets/make_demo_fixture.py",
)
DEMO_GIF_URL = (
    "https://raw.githubusercontent.com/handochan/aelix-ai/main/docs/assets/demo.gif"
)


def test_the_release_sdist_leaves_the_demo_recording_behind(
    release_sdist: tuple[Path, list[str]],
) -> None:
    """#387: the 2.3 MB demo GIF was ~40% of the sdist and nothing reads it there.

    The brand assets beside it are tens of KB and are what `TRADEMARK.md`
    points at, so they stay — and are asserted here, so a broadened exclude
    (`docs/assets`) reads as a regression rather than as a size win.
    """
    _, members = release_sdist
    inner = {n.split("/", 1)[1] for n in members if "/" in n}
    for gone in DEMO_ONLY_PATHS:
        assert gone not in inner, f"{gone} is back in the published sdist"
    for kept in (
        "README.md",
        "README.ko.md",
        "docs/assets/brand/BRAND.md",
        "docs/assets/brand/mark.svg",
        "docs/assets/brand/lockup-stacked.png",
    ):
        assert kept in inner, f"the release sdist lost {kept}"
    # Excluded from the BUILD, not from git: the absolute URL the READMEs use
    # only resolves while main still tracks the GIF.
    tracked = subprocess.run(
        ["git", "ls-files", "--", *DEMO_ONLY_PATHS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert sorted(tracked) == sorted(DEMO_ONLY_PATHS)


def _tracked(*pathspecs: str) -> list[str]:
    return subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split("\0")[:-1]


def test_the_release_sdist_leaves_the_adrs_behind(
    release_sdist: tuple[Path, list[str]],
) -> None:
    """#816: the ADRs were 2.2 MB of the 5.9 MB sdist and nothing reads them there.

    `docs/` still ships, so `docs` stays in ALLOWED_SDIST_TOP_LEVEL for a
    reason: every tracked guide and brand asset is asserted here, so a
    broadened exclude (`/docs`) reads as a regression rather than as a size win.
    """
    _, members = release_sdist
    inner = {n.split("/", 1)[1] for n in members if "/" in n}
    adrs_shipped = sorted(
        n for n in inner if n == "docs/decisions" or n.startswith("docs/decisions/")
    )
    assert adrs_shipped == [], (
        f"{len(adrs_shipped)} ADR members are back in the published sdist: {adrs_shipped[:3]}"
    )
    kept = _tracked("docs/guides", "docs/assets/brand")
    assert any(p.startswith("docs/guides/") for p in kept), kept
    assert any(p.startswith("docs/assets/brand/") for p in kept), kept
    # Compared in NFC: macOS bsdtar writes a seeded name to disk as NFD.
    shipped = {unicodedata.normalize("NFC", n) for n in inner}
    lost = [p for p in kept if unicodedata.normalize("NFC", p) not in shipped]
    assert lost == [], f"the release sdist lost {lost}"
    # Excluded from the BUILD, not from git: the guides link the ADRs by an
    # absolute URL on `main`.
    adrs = _tracked("docs/decisions")
    assert "docs/decisions/README.md" in adrs
    assert any(re.fullmatch(r"docs/decisions/\d{4}-[^/]+\.md", p) for p in adrs), adrs[:5]


@pytest.mark.parametrize("readme", ["README.md", "README.ko.md"])
def test_the_readme_loads_the_demo_gif_by_absolute_url(readme: str) -> None:
    """#387: a relative `docs/assets/demo.gif` breaks wherever the repo tree is
    not beside the README — on the PyPI project page always, and in the sdist
    now that the GIF is excluded from it. The lockup already uses this form.
    """
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    srcs = re.findall(r'src="([^"]*demo\.gif)"', text)
    assert srcs == [DEMO_GIF_URL], f"{readme} demo image src: {srcs}"


def test_the_predicate_itself_knows_a_credential_file() -> None:
    """`_is_developer_state` must catch `.env` on its own.

    FOUND BY SABOTAGE. Deleting the `.env` clause from the predicate left every
    build-based assertion above GREEN, because the pyproject `exclude` lists
    now stop `.env` before the predicate is ever consulted. Two independent
    defences, one of them untested — and the untested one is the whole reason
    the other can be edited safely. So it gets a direct unit test rather than
    an indirect one through a tarball that cannot reach it.

    `.env.example` is the control in both directions: it must NOT match, or a
    blanket `.env*` rule would take the only human-readable list of supported
    environment variables with it.
    """

    assert _is_developer_state(".env")
    assert _is_developer_state("aelix-0.1.0b1/.env")
    assert _is_developer_state("aelix-0.1.0b1/.env.local")
    assert not _is_developer_state("aelix-0.1.0b1/.env.example")
    # #733: the agent tooling, tracked or not, at the root or nested.
    assert _is_developer_state("aelix-0.1.0b1/.agents/skills/x/SKILL.md")
    assert _is_developer_state("aelix_coding_agent/.agents/skills/x/SKILL.md")
    assert _is_developer_state("aelix-0.1.0b1/.claude/skills/x/SKILL.md")
    # And the predicate is not simply returning True for everything.
    assert not _is_developer_state("aelix-0.1.0b1/README.md")
