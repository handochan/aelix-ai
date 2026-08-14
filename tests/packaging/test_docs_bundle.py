"""The bundled user guides really reach the WHEEL — asserted against a built
artifact, never against the source tree.

#101 ships ``docs/guides/*.md`` inside the aelix-coding-agent wheel at
``aelix_coding_agent/docs/`` so an installed user can read them offline
(``aelix docs``). ``tests/test_docs_bundle_sync.py`` keeps the two source-tree
copies identical; it says NOTHING about packaging. A file can be on disk,
byte-perfect, committed, and still absent from the wheel — that is precisely the
failure this repo has shipped before (#111 B-7, #143), and the failure the
extension-authoring guide itself warns third-party authors about.

So this file builds the real wheel with ``uv build`` and reads its namelist.

WHY IT DOES NOT DELETE ``.gitignore`` the way ``test_build_hygiene.py`` does.
That fixture removes the belt because it is asserting ABSENCE — with
``.gitignore`` in place hatchling suppresses the planted scratch on its own and
the ``exclude`` lists are never exercised. Here the assertion is PRESENCE, so
the ordinary build is the right build: it is exactly the artifact a maintainer
publishes, belt and all. A presence assertion cannot be made vacuous by the belt
being on — only by the corpus being empty, which is asserted first.

NO SDIST -> WHEEL ROUND TRIP HERE, and that is a gap, not a decision.
``tests/packaging/`` has no such pattern to reuse: ``test_build_hygiene.py``
builds a wheel and an sdist from the same source copy and inspects each, but
never unpacks the sdist and rebuilds a wheel out of it. So "the sdist is
sufficient to rebuild a wheel that still carries the docs" is UNTESTED by
this file. Writing that pattern is a packaging-wide change (it would want to
cover every target, not just the docs) and belongs with the file that owns
those builds.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDES = REPO_ROOT / "docs" / "guides"
WHEEL_DOCS_PREFIX = "aelix_coding_agent/docs/"

_UV = shutil.which("uv")

pytestmark = pytest.mark.skipif(
    _UV is None or not (REPO_ROOT / "pyproject.toml").is_file(),
    reason="the wheel-content gate needs `uv` and the source checkout",
)


@pytest.fixture(scope="module")
def wheel_names() -> Iterator[list[str]]:
    """Namelist of a real ``uv build --package aelix-coding-agent`` wheel."""
    assert _UV is not None
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run(
            [_UV, "build", "--package", "aelix-coding-agent", "--out-dir", out],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"`uv build --package aelix-coding-agent` failed with "
                f"{result.returncode}\n--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            )
        wheels = list(Path(out).glob("aelix_coding_agent-*.whl"))
        assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
        with zipfile.ZipFile(wheels[0]) as zf:
            yield zf.namelist()


def test_every_guide_is_in_the_wheel(wheel_names: list[str]) -> None:
    """Every ``docs/guides/*.md`` ships, under ``aelix_coding_agent/docs/``.

    Driven off the SOURCE guide list rather than the bundled one so that a guide
    which was never bundled at all still fails here — reading only the bundled
    directory would make this test agree with whatever mistake was made.
    """
    guides = sorted(p.name for p in GUIDES.glob("*.md"))
    assert len(guides) >= 7, f"guide discovery returned {guides}"

    present = set(wheel_names)
    missing = [n for n in guides if f"{WHEEL_DOCS_PREFIX}{n}" not in present]
    assert missing == [], (
        f"guides missing from the built wheel: {missing}\n"
        "`aelix docs` would offer nothing for these on an installed machine. "
        "Check `uv run python scripts/sync_bundled_docs.py`, then the "
        "`exclude` list in packages/aelix-coding-agent/pyproject.toml."
    )


def test_the_wheel_carries_no_orphan_doc(wheel_names: list[str]) -> None:
    """Nothing under ``aelix_coding_agent/docs/`` that is not a guide.

    The shipped artifact is the last place an orphan can hide, and it is the
    only place that matters: ``aelix docs`` derives its topic list from this
    directory as installed.
    """
    guides = {p.name for p in GUIDES.glob("*.md")}
    shipped = [
        n[len(WHEEL_DOCS_PREFIX) :]
        for n in wheel_names
        if n.startswith(WHEEL_DOCS_PREFIX) and n != WHEEL_DOCS_PREFIX
    ]
    assert shipped, "no docs at all reached the wheel"
    orphans = [n for n in shipped if n not in guides]
    assert orphans == [], f"wheel ships docs with no source guide: {orphans}"


_LINK = re.compile(r"\]\(([^)\s]+)\)")


def test_every_relative_link_in_a_shipped_doc_targets_another_shipped_doc(
    wheel_names: list[str],
) -> None:
    """A relative link in a shipped doc must point at a file that also shipped.

    Checked against the wheel rather than the tree because the tree has
    neighbours the wheel does not: ``docs/guides/`` sits beside
    ``docs/decisions/``, so ``../decisions/0149-….md`` resolves in a checkout
    and is dead for every installed user.
    """
    present = set(wheel_names)
    broken: list[str] = []
    for name in sorted(p.name for p in GUIDES.glob("*.md")):
        text = (GUIDES / name).read_text(encoding="utf-8")
        for raw in _LINK.findall(text):
            if raw.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = raw.split("#", 1)[0]
            if not target:
                continue
            member = f"{WHEEL_DOCS_PREFIX}{target}"
            # Normalise `../` so an escaping link produces a member name that
            # cannot be in the wheel, rather than a Path that quietly resolves.
            if "/" in target:
                member = str(Path(WHEEL_DOCS_PREFIX + target))
            if member not in present:
                broken.append(f"{name} -> {raw} (not in the wheel as {member})")
    assert broken == [], (
        "relative links in shipped docs that an installed user cannot follow:\n  "
        + "\n  ".join(broken)
    )


def test_the_wheel_still_carries_the_help_registry(wheel_names: list[str]) -> None:
    """The docs are useless without the accessor that finds them.

    Paired with the content assertions on purpose: `aelix_coding_agent/help/` is
    a package with no ``.py`` sibling in the wheel's own metadata to vouch for
    it, so a build-config change that dropped it would otherwise show up only as
    an ImportError on a user's machine.
    """
    assert "aelix_coding_agent/help/registry.py" in wheel_names
    assert "aelix_coding_agent/help/__init__.py" in wheel_names
    assert "aelix_coding_agent/cli/docs.py" in wheel_names


def test_the_docs_resolve_from_an_INSTALLED_layout_not_the_source_tree(
    tmp_path: Path,
) -> None:
    """``bundled_docs_dir()`` must answer correctly when the package is a
    subdirectory of an install root rather than of ``src/``.

    This is the assertion the ``parents[1]`` vs ``parents[2]`` choice turns on,
    and it is the one a source-tree test cannot make: in a checkout both depths
    are wrong in *visible* ways, while in an install ``parents[2]`` is
    site-packages — a directory that exists, has no ``docs/``, and therefore
    reports "no guides are bundled" instead of raising.

    The wheel is UNPACKED rather than pip-installed on purpose: an install would
    pull ~40 dependencies from the network and make a packaging gate fail on an
    air-gapped machine for a reason that has nothing to do with packaging.
    Unpacking reproduces the only thing under test — the directory nesting the
    resolver walks.

    The child asserts the resolved path is inside the UNPACKED root, so the test
    cannot pass by silently resolving against the repo it was launched from.
    """
    import os

    wheel_root = tmp_path / "installed"
    wheel_root.mkdir()
    assert _UV is not None
    out = tmp_path / "dist"
    result = subprocess.run(
        [_UV, "build", "--package", "aelix-coding-agent", "--out-dir", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    (wheel,) = out.glob("aelix_coding_agent-*.whl")
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(wheel_root)

    src = REPO_ROOT / "packages"
    env = dict(os.environ)
    # The unpacked wheel FIRST so `aelix_coding_agent` resolves from it; the
    # workspace sources after it only to satisfy its two hard dependencies.
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(wheel_root),
            str(src / "aelix-agent-core" / "src"),
            str(src / "aelix-ai" / "src"),
        ]
    )
    code = (
        "import aelix_coding_agent.help as h, pathlib, sys;"
        "root = pathlib.Path(sys.argv[1]).resolve();"
        "d = h.bundled_docs_dir();"
        "assert d.is_relative_to(root), f'resolved outside the install root: {d}';"
        "assert d.parent.name == 'aelix_coding_agent', d;"
        "names = h.topic_names();"
        "assert names, 'no topics in the installed layout';"
        "print(len(names))"
    )
    child = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, str(wheel_root)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env=env,
    )
    assert child.returncode == 0, f"{child.stdout}\n{child.stderr}"
    guides = len(list(GUIDES.glob("*.md")))
    assert child.stdout.strip() == str(guides), (
        f"installed layout offers {child.stdout.strip()} topics, "
        f"repo has {guides} guides"
    )
