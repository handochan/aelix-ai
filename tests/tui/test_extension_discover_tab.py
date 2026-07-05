"""Unit tests for the /extension Discover tab (Issue #65, ADR-0188).

Covers the PURE builders + the type-to-filter predicate the ``tabbed`` viewer
applies on the Discover tab, without standing up prompt-toolkit. Companion to
``tests/tui/test_extension_manager.py`` (which owns the Installed/Sources tabs).
"""

from __future__ import annotations

import re
from typing import Any

from aelix_coding_agent.cli.extension_catalog import Catalog, CatalogEntry
from aelix_coding_agent.tui.extension_manager import build_discover_lines

# Mirrors context.py's SGR-strip regex — the filter matches VISIBLE text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _filtered_body(lines: list[str], flt: str) -> list[str]:
    """Replicate the tabbed() filter predicate over body lines (case-insensitive
    substring on the ANSI-stripped visible text), exactly as context.py does."""

    needle = flt.lower()
    return [line for line in lines if needle in _ANSI_RE.sub("", line).lower()]


# === build_discover_lines: empty state ===


def test_discover_empty_state_when_no_catalogs() -> None:
    lines = build_discover_lines([])
    assert lines
    body = "\n".join(lines)
    assert "No catalog registered" in body
    # Points the user at the CLI registration verb (the tab is read-only).
    assert any("aelix extension source add --catalog" in ln for ln in lines)
    # Honest advisory note is present.
    assert "advisory" in body.lower()


def test_discover_empty_state_none_input() -> None:
    # None (no getter wired / getter returned None) degrades like empty.
    lines = build_discover_lines(None)
    assert any("No catalog registered" in ln for ln in lines)


# === build_discover_lines: populated ===


def test_discover_renders_entries_grouped_by_catalog() -> None:
    cat = Catalog(
        location="https://c/catalog.json",
        name="Acme Catalog",
        entries=(
            CatalogEntry(
                name="cool-ext",
                source="cool-ext==1.2.3",
                description="A cool extension",
                version="1.2.3",
            ),
            CatalogEntry(name="bare-ext", source="git+https://h/r.git"),
        ),
    )
    lines = build_discover_lines([cat])
    body = "\n".join(lines)
    # Grouped under the catalog label (its document name wins over location).
    assert "Acme Catalog:" in body
    # Entry row carries name, version and description.
    assert any(
        "cool-ext" in ln and "1.2.3" in ln and "A cool extension" in ln for ln in lines
    )
    # A bare entry (no version/description) still renders its name.
    assert any(ln.strip() == "bare-ext" for ln in lines)
    # Read-only footer points at the CLI discover verb.
    assert any("aelix extension discover" in ln for ln in lines)


def test_discover_uses_display_version_versions_fallback() -> None:
    # No explicit `version`; display_version() falls back to versions[0].
    cat = Catalog(
        location="file:///c.json",
        name="Cat",
        entries=(
            CatalogEntry(name="multi", source="multi", versions=("9.9.9", "8.8.8")),
        ),
    )
    lines = build_discover_lines([cat])
    assert any("multi" in ln and "9.9.9" in ln for ln in lines)
    # The second version is NOT shown (display_version returns only the first).
    assert not any("8.8.8" in ln for ln in lines)


def test_discover_label_falls_back_to_location_when_no_name() -> None:
    cat = Catalog(
        location="file:///srv/catalog.json",
        name=None,
        entries=(CatalogEntry(name="x", source="x"),),
    )
    lines = build_discover_lines([cat])
    # Catalog.label() returns the raw location when the document has no name.
    assert any("file:///srv/catalog.json:" in ln for ln in lines)


def test_discover_multiple_catalogs_each_grouped() -> None:
    a = Catalog(location="a", name="Alpha", entries=(CatalogEntry(name="ea", source="ea"),))
    b = Catalog(location="b", name="Beta", entries=(CatalogEntry(name="eb", source="eb"),))
    lines = build_discover_lines([a, b])
    body = "\n".join(lines)
    assert "Alpha:" in body
    assert "Beta:" in body
    assert "ea" in body
    assert "eb" in body


# === build_discover_lines: per-catalog error / empty ===


def test_discover_error_line_for_failed_catalog() -> None:
    cat = Catalog(location="https://c/x.json", name="Broken", error="connection refused")
    lines = build_discover_lines([cat])
    body = "\n".join(lines)
    assert "Broken:" in body
    # A fetch failure surfaces a ⚠ row rather than silently dropping the source.
    assert any("⚠" in ln and "connection refused" in ln for ln in lines)


def test_discover_empty_catalog_shows_no_extensions_listed() -> None:
    cat = Catalog(location="c", name="Empty", entries=())
    lines = build_discover_lines([cat])
    assert any("(no extensions listed)" in ln for ln in lines)


def test_discover_error_takes_precedence_over_empty_note() -> None:
    # An errored catalog shows ⚠, not the "(no extensions listed)" note.
    cat = Catalog(location="c", name="E", entries=(), error="boom")
    lines = build_discover_lines([cat])
    assert any("⚠" in ln and "boom" in ln for ln in lines)
    assert not any("(no extensions listed)" in ln for ln in lines)


# === build_discover_lines: getattr-guarded against odd shapes ===


def test_discover_getattr_guarded_bare_catalog() -> None:
    # A catalog-shaped object missing every attribute degrades to "?" label with
    # no entries, never raising in the render path.
    bare: Any = object()
    lines = build_discover_lines([bare])
    body = "\n".join(lines)
    assert "?:" in body
    assert "(no extensions listed)" in body


def test_discover_getattr_guarded_odd_entry() -> None:
    # An entry with no name and no display_version() degrades to "?" and omits
    # the version, rather than raising.
    class _OddEntry:
        description = "lonely"

    class _Cat:
        name = "Odd"
        error = None
        entries = (_OddEntry(),)

        def label(self) -> str:
            return "Odd"

    lines = build_discover_lines([_Cat()])
    body = "\n".join(lines)
    assert "Odd:" in body
    assert any(ln.strip().startswith("?") and "lonely" in ln for ln in lines)


def test_discover_label_callable_raising_degrades() -> None:
    # A label() that raises must not crash the render — it degrades to name/loc.
    class _Cat:
        name = "FromName"
        location = "loc"
        error = None
        entries = ()

        def label(self) -> str:
            raise RuntimeError("nope")

    lines = build_discover_lines([_Cat()])
    # Falls back to the `name` attribute after label() raised.
    assert any("FromName:" in ln for ln in lines)


# === filter predicate (pure) — the tabbed() type-to-filter mechanism ===


def test_filter_predicate_narrows_visible_rows() -> None:
    cat = Catalog(
        location="c",
        name="Cat",
        entries=(
            CatalogEntry(name="alpha-tool", source="alpha", description="first thing"),
            CatalogEntry(name="beta-tool", source="beta", description="second thing"),
            CatalogEntry(name="gamma-tool", source="gamma", description="third thing"),
        ),
    )
    lines = build_discover_lines([cat])

    # Filtering on a substring of one entry keeps only rows containing it.
    matched = _filtered_body(lines, "beta")
    assert any("beta-tool" in ln for ln in matched)
    assert not any("alpha-tool" in ln for ln in matched)
    assert not any("gamma-tool" in ln for ln in matched)


def test_filter_predicate_is_case_insensitive() -> None:
    cat = Catalog(
        location="c",
        name="Cat",
        entries=(CatalogEntry(name="CamelExt", source="s", description="Mixed Case"),),
    )
    lines = build_discover_lines([cat])
    # Upper-cased needle still matches the lower/mixed-case visible text.
    assert any("CamelExt" in ln for ln in _filtered_body(lines, "CAMELEXT"))
    assert any("CamelExt" in ln for ln in _filtered_body(lines, "camelext"))


def test_filter_predicate_matches_description_text() -> None:
    cat = Catalog(
        location="c",
        name="Cat",
        entries=(
            CatalogEntry(name="x1", source="x1", description="linter for python"),
            CatalogEntry(name="x2", source="x2", description="formatter for go"),
        ),
    )
    lines = build_discover_lines([cat])
    matched = _filtered_body(lines, "linter")
    assert any("x1" in ln for ln in matched)
    assert not any("x2" in ln for ln in matched)


def test_filter_predicate_empty_needle_keeps_all() -> None:
    cat = Catalog(
        location="c",
        name="Cat",
        entries=(CatalogEntry(name="one", source="one"), CatalogEntry(name="two", source="two")),
    )
    lines = build_discover_lines([cat])
    # An empty filter (initial state) is a no-op — every line stays visible.
    assert _filtered_body(lines, "") == lines


def test_filter_predicate_no_match_yields_empty_body() -> None:
    cat = Catalog(location="c", name="Cat", entries=(CatalogEntry(name="only", source="only"),))
    lines = build_discover_lines([cat])
    # A needle matching nothing filters the body to empty (the tab then renders
    # its own dim "(no matches)" placeholder).
    assert _filtered_body(lines, "zzz-nonexistent") == []
