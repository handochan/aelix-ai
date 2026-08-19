"""Drift guard: the user guides bundled into the wheel stay identical to
``docs/guides/`` — in BOTH directions — and stay shippable.

#101 bundles ``docs/guides/*.md`` into the wheel at
``aelix_coding_agent/docs/`` so an installed user can read them with no network
and no checkout (``aelix docs``). That means two copies of every guide, and a
duplication defended by discipline is a duplication that drifts.

This is deliberately the same shape as ``tests/test_license_sync.py``, which
solved the identical problem for LICENSE / NOTICE / THIRD-PARTY-NOTICES.md: glob
both sides rather than list them, compare BYTES, and put the repair command in
the assertion message. Two sync tests written two different ways is how the
second one ends up weaker than the first.

WHY BIDIRECTIONAL. A one-directional test ("every guide has a bundled copy")
cannot see an ORPHAN — a bundled file whose guide was renamed or deleted. That
file keeps shipping in every wheel, and ``aelix docs`` keeps offering it as a
topic, because the topic list is derived from the bundled directory. Measured:
plant ``docs/stale-guide.md`` under the package with no counterpart in
``docs/guides/`` and only ``test_every_bundled_doc_has_a_source_guide`` fails.

WHAT THIS FILE DOES NOT PROVE. Reading the source tree says nothing about
packaging — the bundled files could be excluded from the wheel and every
assertion here would still pass. ``tests/packaging_gate/test_docs_bundle.py`` builds
a real wheel and asserts against its namelist. Both, or neither is worth much.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import re
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
GUIDES = REPO / "docs" / "guides"
PKG = REPO / "packages" / "aelix-coding-agent" / "src" / "aelix_coding_agent"
BUNDLED = PKG / "docs"
CODING_AGENT_PYPROJECT = REPO / "packages" / "aelix-coding-agent" / "pyproject.toml"

#: The repair command, printed in every failure message so the fix does not have
#: to be reconstructed from the diff.
_RESYNC = "uv run python scripts/sync_bundled_docs.py"

# Globbed, not listed: a guide added to `docs/guides/` must fail this file until
# it is bundled, which a hardcoded list could never do.
#
# The glob is NON-recursive and that is now a checked property rather than an
# assumption — see `test_no_guide_hides_in_a_subdirectory`. Until #101's L1
# review this comment was false for a guide one directory down: the file was
# skipped by every glob in the pipeline and this file stayed green.
SOURCE_GUIDES = sorted(p.name for p in GUIDES.glob("*.md"))
BUNDLED_DOCS = sorted(p.name for p in BUNDLED.glob("*.md")) if BUNDLED.is_dir() else []

#: Asserted at the top of every test that iterates the bundled corpus. Without
#: it those tests PASS over an empty/missing directory — measured: before the
#: bundle existed, 5 of the 7 tests in this file were green over zero files.
_EMPTY = f"the bundled docs corpus is empty; this test would pass over nothing. Fix: {_RESYNC}"


def test_guide_discovery_is_not_empty() -> None:
    """Guards the guard: an empty glob would make every test below vacuous."""
    assert len(SOURCE_GUIDES) >= 7, SOURCE_GUIDES
    assert "extension-authoring.md" in SOURCE_GUIDES
    assert "project-trust.md" in SOURCE_GUIDES


def _sync_script() -> Any:
    """Load ``scripts/sync_bundled_docs.py`` — a script, not a package.

    Imported rather than re-implemented so the refusal and this guard cannot
    disagree about what "nested" means. ``scripts/`` has no ``__init__.py`` and
    is not on ``sys.path``, hence the explicit spec.
    """

    path = REPO / "scripts" / "sync_bundled_docs.py"
    spec = importlib.util.spec_from_file_location("_sync_bundled_docs", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_guide_hides_in_a_subdirectory() -> None:
    """``docs/guides/`` is FLAT, and the flatness is load-bearing (#101 L1).

    Every glob in this pipeline — this file, `scripts/sync_bundled_docs.py`,
    `tests/packaging_gate/test_docs_bundle.py`, `help.registry.topics` — is `*.md`,
    not `**/*.md`. So a guide written one directory down was skipped in
    complete silence. Measured on this tree with `docs/guides/advanced/
    nested-guide.md` planted:

        tests/test_docs_bundle_sync.py    7 passed
        scripts/sync_bundled_docs.py      "8 guide(s) bundled — 0 updated"
        aelix docs                        the same 8 topics

    Nothing said the ninth file existed. It never shipped, never became a
    topic, and never failed anything.
    """
    nested = _sync_script().nested_guides(GUIDES)
    assert nested == [], (
        f"guides in a subdirectory of docs/guides/: "
        f"{[str(p) for p in nested]}\n"
        f"They ship nowhere and are offered nowhere. Move them to "
        f"docs/guides/<name>.md. Fix: {_RESYNC} (which now refuses too)."
    )


def test_the_subdirectory_guard_is_not_vacuous(tmp_path: Path) -> None:
    """Guards the guard above, which passes trivially on a flat tree.

    Drives the same function over a synthetic guides directory containing one
    nested file, so "no nested guides" is earned rather than a consequence of
    the checker returning `[]` unconditionally.
    """
    guides = tmp_path / "guides"
    (guides / "advanced").mkdir(parents=True)
    (guides / "top.md").write_text("# top\n", encoding="utf-8")
    (guides / "advanced" / "nested.md").write_text("# nested\n", encoding="utf-8")

    found = _sync_script().nested_guides(guides)
    assert [str(p) for p in found] == ["advanced/nested.md"]


def test_the_sync_script_refuses_a_nested_guide_rather_than_skipping_it(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    """The refusal itself: exit 1, on stderr, naming the file.

    A checker nobody calls is the same silence in a different place, so
    ``main()`` is driven with ``GUIDES`` pointed at the synthetic tree.
    """
    guides = tmp_path / "guides"
    (guides / "advanced").mkdir(parents=True)
    (guides / "top.md").write_text("# top\n", encoding="utf-8")
    (guides / "advanced" / "nested.md").write_text("# nested\n", encoding="utf-8")
    bundled = tmp_path / "bundled"

    module = _sync_script()
    monkeypatch.setattr(module, "GUIDES", guides)
    monkeypatch.setattr(module, "BUNDLED", bundled)

    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == "", "a refusal must not look like a successful sync"
    assert "advanced/nested.md" in captured.err
    assert not bundled.exists(), "nothing was copied under a refusal"


def test_every_guide_has_a_byte_equal_bundled_copy() -> None:
    assert BUNDLED.is_dir(), f"{BUNDLED} missing — create it: {_RESYNC}"
    for name in SOURCE_GUIDES:
        bundled = BUNDLED / name
        assert bundled.is_file(), (
            f"{name} is in docs/guides/ but not bundled — an installed user "
            f"cannot read it with `aelix docs`. Fix: {_RESYNC}"
        )
        assert bundled.read_bytes() == (GUIDES / name).read_bytes(), (
            f"{bundled} has drifted from docs/guides/{name}. The wheel copy is "
            f"what users read, so this is a shipped-wrong-docs bug. Fix: {_RESYNC}"
        )


def test_every_bundled_doc_has_a_source_guide() -> None:
    """The direction a one-way sync test cannot see.

    An orphan keeps shipping AND keeps appearing as an `aelix docs` topic (the
    topic list is derived from this directory), so it is worse than a stale file
    on disk — it is a stale file the product advertises.
    """
    assert BUNDLED_DOCS, _EMPTY
    orphans = [name for name in BUNDLED_DOCS if name not in SOURCE_GUIDES]
    assert orphans == [], (
        f"bundled docs with no counterpart in docs/guides/: {orphans}\n"
        f"They still ship in every wheel and still appear as `aelix docs` "
        f"topics. Delete them, or restore the guide. Fix: {_RESYNC}"
    )


def _wheel_excludes() -> list[str]:
    with open(CODING_AGENT_PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    return list(data["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"])


def test_the_exclude_list_still_contains_the_names_this_guard_is_about() -> None:
    """Guards the guard below, which derives its forbidden set from the pyproject.

    Without this, deleting ``CLAUDE.md`` from ``exclude`` would make the
    filename guard pass by having nothing left to forbid.
    """
    excludes = _wheel_excludes()
    assert "CLAUDE.md" in excludes
    assert "AGENTS.md" in excludes


def test_no_bundled_doc_is_silently_dropped_by_the_wheel_exclude_list() -> None:
    """A bundled doc must not match any wheel ``exclude`` pattern.

    NOT theoretical. ``exclude`` carries bare ``CLAUDE.md`` and ``AGENTS.md``,
    and hatchling drops a file matching either ANYWHERE in the tree — while
    ``AGENTS.md`` is also the name of a topic #101 documents (project trust
    explains why an AGENTS.md is ungated). A guide named for its subject would
    vanish from the wheel with no error at all: the source tree keeps it, every
    sync test above stays green, and only an installed user notices.

    ``build/`` and ``dist/`` are checked separately: they are not in this
    project's exclude list, but they are hatchling/VCS-ignore conventions and a
    docs subdirectory named either would be swept for reasons no assertion here
    would explain.
    """
    assert BUNDLED_DOCS, _EMPTY
    excludes = _wheel_excludes()
    for name in BUNDLED_DOCS:
        rel = f"aelix_coding_agent/docs/{name}"
        for pattern in excludes:
            assert not fnmatch.fnmatch(name, pattern), (
                f"bundled doc {name!r} matches wheel exclude pattern "
                f"{pattern!r} — hatchling would drop it from the wheel with no "
                f"error. Rename the guide."
            )
            assert not fnmatch.fnmatch(rel, pattern), (
                f"bundled doc path {rel!r} matches wheel exclude {pattern!r}."
            )

    for path in BUNDLED.rglob("*") if BUNDLED.is_dir() else []:
        parts = set(path.relative_to(BUNDLED).parts)
        assert not (parts & {"build", "dist"}), (
            f"{path} sits under a build/ or dist/ directory inside the bundled "
            f"docs — those names are swept by build tooling. Rename it."
        )


#: ``](target)`` — markdown inline links only. Reference-style links are not used
#: in these guides; if one is added this regex silently stops covering it, which
#: is why `test_the_link_scanner_sees_links` below pins a known count.
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _relative_link_targets(text: str) -> list[str]:
    out = []
    for raw in _LINK.findall(text):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append(raw.split("#", 1)[0])
    return [t for t in out if t]


def test_the_link_scanner_sees_links() -> None:
    """Guards the guard: a regex that matched nothing would pass the test below
    on a directory of entirely broken links."""
    assert BUNDLED_DOCS, _EMPTY
    total = sum(
        len(_relative_link_targets((BUNDLED / name).read_text(encoding="utf-8")))
        for name in BUNDLED_DOCS
    )
    assert total >= 10, f"link scanner found only {total} relative links"


def test_every_relative_link_in_a_bundled_doc_resolves_to_a_bundled_doc() -> None:
    """A relative link that leaves the bundled directory is dead for an
    installed user.

    ``docs/guides/`` sits beside ``docs/decisions/`` in the repo, so
    ``../decisions/0188-….md`` resolved there and looked fine. Bundled at
    ``aelix_coding_agent/docs/``, the same link points at
    ``aelix_coding_agent/decisions/``, which does not exist and never will. The
    fix is an absolute URL, which is correct in both places — this test is what
    makes that a rule instead of a habit.
    """
    assert BUNDLED_DOCS, _EMPTY
    broken: list[str] = []
    for name in BUNDLED_DOCS:
        doc = BUNDLED / name
        for target in _relative_link_targets(doc.read_text(encoding="utf-8")):
            resolved = (doc.parent / target).resolve()
            try:
                resolved.relative_to(BUNDLED.resolve())
            except ValueError:
                broken.append(f"{name} -> {target} (escapes the bundled docs dir)")
                continue
            if not resolved.is_file():
                broken.append(f"{name} -> {target} (no such file)")
    assert broken == [], (
        "relative links in the BUNDLED docs that an installed user cannot "
        "follow:\n  " + "\n  ".join(broken) + "\nUse an absolute "
        "https://github.com/handochan/aelix-ai/blob/main/… URL for anything "
        "outside docs/guides/."
    )
