"""``site/latest-version.json`` must describe the version that actually shipped.

THE FAILURE THIS PREVENTS. The launch-time update check reads exactly one
source. If the release ritual bumps the package version and forgets the feed,
the check keeps reporting the previous release forever — and it fails in the
quietest possible way, because "no update available" is also what a correct
check says most of the time. Nobody would notice until a user asked why they
were never told about beta.2.

That is the same shape as the defects this repo keeps finding: a mechanism that
works, pointed at stale data, reporting success. So the feed is not maintained
by discipline; it is maintained by this file.

WHY THE VERSION AND NOT THE TAG IS THE ANCHOR. The tag is ``v0.1.0-beta.1`` and
the wheel version is ``0.1.0b1``: the same release, two spellings. Comparing
them as strings is what makes an update check announce an update the user
already has, so the assertion below compares them the way the product does —
through ``packaging.version.Version``, which normalises both to the same value.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FEED = REPO_ROOT / "site" / "latest-version.json"
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"


def _feed() -> dict:
    return json.loads(FEED.read_text(encoding="utf-8"))


def _declared_version() -> str:
    data = tomllib.loads(ROOT_PYPROJECT.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_the_feed_is_published_from_the_pages_directory() -> None:
    """It only reaches users if GitHub Pages serves it.

    ``pages.yml`` uploads ``site/`` and nothing else, so a feed written anywhere
    else is a file nobody fetches. Also guards against someone "tidying" it into
    ``docs/``.
    """

    assert FEED.is_file(), f"{FEED} is missing"
    workflow = (REPO_ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )
    assert "path: site" in workflow


def test_the_feed_names_the_version_this_repo_declares() -> None:
    """The whole point. Bump one and this fails until you bump the other."""

    from packaging.version import Version

    feed = _feed()
    latest = feed["latest"]
    declared = _declared_version()

    assert Version(latest["version"]) == Version(declared), (
        f"site/latest-version.json says {latest['version']!r} but pyproject.toml "
        f"declares {declared!r}. Update the feed in the same commit as the "
        "version bump — the update check reads it and nothing else."
    )
    # The tag is a different spelling of the same release, not a different one.
    assert Version(latest["tag"].lstrip("v")) == Version(declared)
    assert latest["url"].endswith(latest["tag"])
    assert latest["prerelease"] is Version(declared).is_prerelease


def test_a_stable_entry_is_never_older_than_a_stable_latest() -> None:
    """``latestStable`` exists so a user on a stable is not offered a beta.

    It may be ``null`` while no stable has shipped. Once one has, it must not
    contradict ``latest``.
    """

    from packaging.version import Version

    feed = _feed()
    stable = feed.get("latestStable")
    if stable is None:
        assert Version(feed["latest"]["version"]).is_prerelease, (
            "latestStable is null but latest is a stable release — a user on a "
            "stable would then be told about nothing at all"
        )
        return
    assert not Version(stable["version"]).is_prerelease
    assert Version(stable["version"]) <= Version(feed["latest"]["version"])


def test_the_product_reads_this_exact_file_shape() -> None:
    """The gate is worthless if it validates a shape the code does not consume.

    Drives the real ``choose_release`` against the real committed feed, from the
    version below it and from the version it names.
    """

    from aelix_coding_agent.update_check import choose_release
    from packaging.version import Version

    feed = _feed()
    declared = _declared_version()

    # Someone already on this release is told nothing.
    assert choose_release(declared, feed) is None

    # Someone on an older PRERELEASE is told about exactly this release.
    chosen = choose_release("0.0.1b1", feed)
    assert chosen is not None, "the committed feed offers nothing to an old build"
    assert chosen.version == feed["latest"]["version"]

    # ...and someone on an older STABLE is told nothing while only betas exist.
    # Not an oversight — it is the asymmetry in ``choose_release``: a user who
    # is on a stable opted out of the beta train, and offering them a
    # prerelease would nag every stable user the moment any beta is cut. This
    # assertion is here because the first version of this test used a stable as
    # "an old build" and read its correct silence as a bug.
    if Version(feed["latest"]["version"]).is_prerelease:
        assert choose_release("0.0.1", feed) is None
