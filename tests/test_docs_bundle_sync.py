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
import subprocess
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
    assert [p.as_posix() for p in found] == ["advanced/nested.md"]


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
    # ``main()`` names the file with ``str(p)`` — native separator — so the
    # expectation is built the same way rather than assumed to be POSIX.
    assert str(Path("advanced") / "nested.md") in captured.err
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


def test_every_guide_is_checked_out_with_lf_line_endings() -> None:
    """``read``'s byte cap is measured on the file as it sits on disk (#485).

    A Windows checkout turns LF into CRLF by default (``core.autocrlf``), one
    byte more per line: ``extension-authoring.md`` was 51326B there against the
    51200B cap, on all three Windows legs, and 50418B everywhere else.
    ``.gitattributes`` pins both copies to LF. ``git check-attr`` proves the
    patterns still match every guide on any checkout; the bytes prove the pin
    took effect, which only a Windows checkout can show.
    """
    assert BUNDLED_DOCS, _EMPTY
    paths = [GUIDES / n for n in SOURCE_GUIDES] + [BUNDLED / n for n in BUNDLED_DOCS]
    out = subprocess.run(
        ["git", "check-attr", "eol", "--", *(str(p.relative_to(REPO)) for p in paths)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert len(out) == len(paths), out
    for line in out:
        assert line.endswith(": eol: lf"), (
            f"{line!r}: .gitattributes no longer pins this guide to LF (#485)"
        )
    for path in paths:
        assert b"\r\n" not in path.read_bytes(), (
            f"{path} was checked out with CRLF line endings, so `read` measures it "
            f"one byte per line larger than the wheel ships it (#485)"
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


#: A heading line outside a fenced block. A ``#`` line inside a fence is a shell
#: or Python comment, and a heading may not be indented past three spaces.
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def _anchor(heading: str) -> str:
    """The anchor GitHub gives a heading: lower-case, every character that is
    not a word character, a space or a hyphen removed, then each space turned
    into a hyphen. Nothing is collapsed, so an em dash between two spaces
    leaves two hyphens (``capabilities--three-are-gates-six-are-documentation``,
    which the extension guide links to and GitHub resolves)."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _sections(text: str) -> dict[str, str]:
    """Every anchor a document's headings define, in order, mapped to the text
    under that heading up to the next one. A repeated heading gets ``-1``,
    ``-2`` and so on, as on GitHub."""
    sections: dict[str, list[str]] = {}
    seen: dict[str, int] = {}
    in_fence = False
    body: list[str] | None = None
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and (match := _HEADING.match(line)):
            base = _anchor(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            body = sections.setdefault(base if count == 0 else f"{base}-{count}", [])
            continue
        if body is not None:
            body.append(line)
    return {anchor: "\n".join(lines).strip() for anchor, lines in sections.items()}


def _anchors(text: str) -> set[str]:
    """Every anchor a document's headings define."""
    return set(_sections(text))


def _fragment_links(text: str) -> list[tuple[str, str]]:
    """``(file, fragment)`` for every link with a ``#`` in it that stays inside
    the bundled docs; ``file`` is ``""`` for a link to the same document."""
    out = []
    for raw in _LINK.findall(text):
        if raw.startswith(("http://", "https://", "mailto:")) or "#" not in raw:
            continue
        target, fragment = raw.split("#", 1)
        out.append((target, fragment))
    return out


def test_the_anchor_rule_matches_anchors_github_resolves() -> None:
    """Guards the guard below. Each pair is a heading in these guides and the
    anchor a link elsewhere in them already uses, so the rule is checked
    against what GitHub produced rather than against itself."""
    assert _anchor("How a `--model` string becomes a provider") == (
        "how-a---model-string-becomes-a-provider"
    )
    assert _anchor("Capabilities — three are gates, six are documentation") == (
        "capabilities--three-are-gates-six-are-documentation"
    )
    assert _anchor("Custom providers with their own `/login` method") == (
        "custom-providers-with-their-own-login-method"
    )
    assert _anchor("Descriptors (runtime-emitted, not manifest)") == (
        "descriptors-runtime-emitted-not-manifest"
    )
    # A fenced comment is not a heading, and a repeated heading is numbered.
    text = "# Title\n\n```bash\n# not a heading\n```\n\n## Notes\n\n## Notes\n"
    assert _anchors(text) == {"title", "notes", "notes-1"}


def test_every_fragment_link_in_a_bundled_doc_names_a_heading_there() -> None:
    """#490 split two guides that had reached ``read``'s byte cap, so whole
    sections changed files. The test above only proves that the FILE a link
    names exists: a link whose section moved still names a file that is there,
    and lands at the top of the wrong guide. So every ``#fragment`` must be an
    anchor that a heading of its target defines.

    It found one already broken on the tree before the split (7faf4b9a):
    ``tls-and-corporate-ca.md`` linked ``#python-313-rejects-certificates-312-accepts``
    while the heading is ``### 4. Python 3.13 rejects certificates 3.12 accepts``,
    whose anchor starts with ``4-``.
    """
    assert BUNDLED_DOCS, _EMPTY
    checked = 0
    broken: list[str] = []
    for name in BUNDLED_DOCS:
        doc = BUNDLED / name
        for target, fragment in _fragment_links(doc.read_text(encoding="utf-8")):
            path = doc if not target else doc.parent / target
            if not path.is_file():
                continue  # the relative-link test above reports a missing file
            checked += 1
            if fragment not in _anchors(path.read_text(encoding="utf-8")):
                broken.append(f"{name} -> {target}#{fragment}")
    # Guards the guard: these guides carry dozens of fragment links, and a
    # scanner that found none would pass over a directory of broken ones.
    assert checked >= 20, f"fragment scanner found only {checked} links"
    assert broken == [], (
        "fragment links in the BUNDLED docs that name no heading of their "
        "target:\n  " + "\n  ".join(broken) + "\nPoint each one at the section "
        "where it lives now (`<guide>.md#<anchor>`)."
    )


#: #490 split the two guides nearest ``read``'s byte cap: ``(old guide, new
#: guide, anchor of the first moved heading)``. The old guide keeps that heading
#: over one paragraph that names the new guide, so links to it still land.
_SPLIT_490 = (
    ("providers-and-models.md", "credentials.md", "providing-an-api-key"),
    (
        "extension-authoring.md",
        "extension-providers.md",
        "custom-providers-with-their-own-login-method",
    ),
)

#: The compaction fixes of #469, #563 and #565, on top of the providers edits
#: merged before them, put ``providers-and-models.md`` at 52018 bytes, over the
#: cap, so its two compaction sections became ``compaction.md`` (pi keeps
#: compaction in its own doc too). The stub is the one of the two headings that
#: stays there, over a pointer.
_SPLIT_COMPACTION = (("providers-and-models.md", "compaction.md", "automatic-compaction"),)

_SPLITS = _SPLIT_490 + _SPLIT_COMPACTION


def _bundled(name: str) -> str:
    return (BUNDLED / name).read_text(encoding="utf-8")


def test_the_sections_490_moved_are_no_longer_in_the_guide_they_left() -> None:
    """A copy is not a move. A split that copied the sections into the new
    guide and left them in the old one frees no bytes, and the cap test stays
    green until that guide reaches the cap again. Checked on headings, not on
    a byte count against the pre-split sizes: the trimmed guides are meant to
    grow back into the room the split made."""
    assert BUNDLED_DOCS, _EMPTY
    for old, new, stub in _SPLITS:
        new_anchors = list(_sections(_bundled(new)))
        moved = set(new_anchors[1:]) - {stub}  # [0] is the new guide's title
        assert moved, f"{new} has no sections besides its title and #{stub}"
        left = sorted(moved & _anchors(_bundled(old)))
        assert left == [], f"{old} still has the sections {new} took: {left}"


def test_the_old_anchors_490_kept_lead_to_the_new_guides() -> None:
    """``#providing-an-api-key`` and ``#custom-providers-with-their-own-login-method``
    were linked from outside the bundle before #490 (issues, branches, other
    READMEs). No guide links to them now, so the fragment test cannot see them
    go: each must stay a heading of its old guide, over one paragraph that
    links to the guide the text moved to. ``#automatic-compaction`` is held the
    same way since the compaction split."""
    assert BUNDLED_DOCS, _EMPTY
    for old, new, stub in _SPLITS:
        sections = _sections(_bundled(old))
        assert stub in sections, f"{old} lost the #{stub} heading"
        pointer = sections[stub]
        assert f"]({new})" in pointer or f"]({new}#" in pointer, (old, stub, pointer)
        assert "\n\n" not in pointer, f"{old}#{stub} should be one pointer paragraph"


#: Prose that sends a reader to text #490 moved by a link with no ``#fragment``,
#: which the fragment test cannot check: ``(guide, words in the paragraph or
#: table row, the guide that paragraph must link to)``.
_POINTERS_490 = (
    ("getting-started.md", "For provider keys see", "credentials.md"),
    ("getting-started.md", "for the full list of providers", "credentials.md"),
    ("tls-and-corporate-ca.md", "provider keys and environment variables", "credentials.md"),
    ("providers-and-models.md", "How to pick a model", "credentials.md"),
    ("extension-authoring.md", "`register_login_provider(provider)`", "extension-providers.md"),
    ("extension-authoring.md", "`register_api_adapter(api, stream_fn)`", "extension-providers.md"),
)

#: The same for the compaction split. This link carries ``#automatic-compaction``,
#: which the stub heading still defines in ``providers-and-models.md``, so the
#: fragment test would pass a link left pointing there.
_POINTERS_COMPACTION = (
    (
        "getting-started.md",
        "that fails after the answer does not change the exit code",
        "compaction.md",
    ),
)


def _blocks_with(text: str, words: str) -> list[str]:
    """The paragraphs, or for a table its rows, that contain ``words``, with
    whitespace collapsed so a rewrapped line does not hide them."""
    blocks: list[str] = []
    for paragraph in text.split("\n\n"):
        rows = [line for line in paragraph.splitlines() if line.startswith("|")]
        blocks.extend(rows or [paragraph])
    flat = (" ".join(block.split()) for block in blocks)
    return [block for block in flat if words in block]


def test_the_prose_that_points_at_490s_moved_text_names_the_new_guides() -> None:
    """A link to the old guide with no ``#fragment`` still resolves, so neither
    link test fails when it is left behind; the reader lands on a guide that no
    longer has the text the sentence promises."""
    assert BUNDLED_DOCS, _EMPTY
    wrong: list[str] = []
    for guide, words, target in _POINTERS_490 + _POINTERS_COMPACTION:
        blocks = _blocks_with(_bundled(guide), words)
        assert len(blocks) == 1, f"{guide}: {len(blocks)} blocks contain {words!r}"
        if f"]({target}" not in blocks[0]:
            wrong.append(f"{guide}: {words!r} does not link to {target}")
    # The guides index lists every guide, so the new ones too.
    index = _bundled("README.md")
    wrong += [
        f"README.md does not list {name}"
        for name in BUNDLED_DOCS
        if f"]({name})" not in index and name != "README.md"
    ]
    assert wrong == [], "\n".join(wrong)
