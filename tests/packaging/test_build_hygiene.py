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

import shutil
import subprocess
import tarfile
import tomllib
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
    ".uv-cache",
    ".omc",
    "__pycache__",
    "*.pyc",
    "CLAUDE.md",
    "AGENTS.md",
    "aelix-session-*.html",
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
        or ".uv-cache" in parts
        or ".omc" in parts
        or "__pycache__" in parts
        or name.endswith((".pyc", ".pyo"))
        or Path(name).name in {"CLAUDE.md", "AGENTS.md"}
        or (Path(name).name.startswith("aelix-session-") and name.endswith(".html"))
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

    # --- repo-root scratch: what the sdist swept in.
    _plant(
        root / ".omc" / "specs" / "packaging-probe-internal-plan.md",
        "# internal sprint plan, never publish\n",
    )
    _plant(root / ".omc" / "wiki" / "packaging-probe-session.md", "session log\n")
    _plant(root / "CLAUDE.md", "# probe\n")
    _plant(root / "AGENTS.md", "# probe\n")
    _plant(root / "aelix-session-packaging-probe.html", "<html></html>")

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
        build_tree / ".omc" / "specs" / "packaging-probe-internal-plan.md",
        build_tree / ".claude" / "worktrees" / "packaging-probe-session" / "pyproject.toml",
        build_tree / ".uv-cache" / "CACHEDIR.TAG",
        build_tree / "aelix-session-packaging-probe.html",
    ):
        assert probe.is_file(), f"scratch probe was not planted: {probe}"


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
