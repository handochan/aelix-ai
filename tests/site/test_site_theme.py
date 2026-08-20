"""The homepage must render on the reader's ground, and both grounds must be legible.

WHY THIS FILE EXISTS. ``site/`` is published straight to GitHub Pages with no
build step, and nothing in this repository has ever read it. That is how
ADR-0228 happened: a colour shipped that measured 1.16:1 on a real terminal
scheme because there was no gate on colour at all, only on prose. Adding a
second palette doubles the surface for exactly that mistake, and the light half
is the half nobody looks at -- a maintainer on a dark laptop can break it and
see nothing.

So this file measures the stylesheet the way a reader meets it:

1. Both schemes resolve. Every custom property is defined in both, so the light
   scheme can never fall through to a dark value by omission.
2. Every text token clears WCAG AA (4.5:1) on every surface token it can land
   on, in BOTH schemes -- not just the pairs used today.
3. Anything with a fill of its own is measured TWICE: the text on it, and the
   band itself against the page. A control that reads fine but dissolves into
   the page is the ADR-0228 failure exactly, and text contrast alone cannot see
   it.
4. The terminal card is dark-locked. It is a picture of the real ANSI banner;
   if the light scheme could reach inside it the page would show something
   Aelix never prints.
5. The inline lockup matches the canonical brand vectors -- the CSS that swaps
   the word and the cursor pixel is checked against the bytes of
   ``lockup-dark.svg`` and ``lockup-light.svg`` themselves, so the page cannot
   drift from BRAND.md.

Every assertion has a positive control: a detector that silently stopped
matching would otherwise pass by seeing nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "site" / "index.html"
BRAND = REPO_ROOT / "docs" / "assets" / "brand"

#: WCAG 2.2 AA for normal-size text.
AA_TEXT = 4.5
#: WCAG 2.2 1.4.11 for a user-interface component against what is adjacent.
AA_NON_TEXT = 3.0


# --------------------------------------------------------------------------
# colour maths
# --------------------------------------------------------------------------


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.strip()
    if text.startswith("#"):
        text = text[1:]
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    raise ValueError(f"not an opaque hex colour: {colour!r}")


_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


def flatten(colour: str, behind: str) -> str:
    """Composite ``colour`` (hex or rgba) over the opaque ``behind``."""
    match = _RGBA.fullmatch(colour.strip())
    if not match:
        return colour.strip()
    r, g, b = (int(match.group(i)) for i in (1, 2, 3))
    alpha = float(match.group(4)) if match.group(4) is not None else 1.0
    br, bg, bb = _rgb(behind)
    mixed = (round(f * alpha + k * (1 - alpha)) for f, k in ((r, br), (g, bg), (b, bb)))
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def luminance(colour: str) -> float:
    r, g, b = _rgb(colour)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fore: str, back: str) -> float:
    """WCAG contrast ratio. ``fore`` may be translucent; it is flattened first."""
    lf, lb = luminance(flatten(fore, back)), luminance(back)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------
# a very small stylesheet reader
# --------------------------------------------------------------------------

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STYLE = re.compile(r"<style>(.*?)</style>", re.S)
_LIGHT_AT = re.compile(r"@media\s*\(\s*prefers-color-scheme:\s*light\s*\)\s*\{", re.I)
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,([^()]*))?\)")


def _stylesheet(html: str) -> str:
    found = _STYLE.search(html)
    assert found, "site/index.html has no <style> block"
    return _COMMENT.sub("", found.group(1))


def _split_by_scheme(css: str) -> tuple[str, str]:
    """Return (everything outside a light media query, everything inside one)."""
    outside: list[str] = []
    inside: list[str] = []
    cursor = 0
    while True:
        found = _LIGHT_AT.search(css, cursor)
        if not found:
            outside.append(css[cursor:])
            return "".join(outside), "\n".join(inside)
        outside.append(css[cursor : found.start()])
        depth, index = 1, found.end()
        while index < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        inside.append(css[found.end() : index - 1])
        cursor = index


def _rules(css: str) -> list[tuple[str, str]]:
    out = []
    for chunk in css.split("}"):
        if "{" not in chunk:
            continue
        selector, body = chunk.split("{", 1)
        selector = " ".join(selector.split())
        if selector and not selector.startswith("@"):
            out.append((selector, body))
    return out


def _declarations(body: str) -> list[tuple[str, str]]:
    out = []
    for piece in body.split(";"):
        if ":" not in piece:
            continue
        name, value = piece.split(":", 1)
        out.append((name.strip(), " ".join(value.split())))
    return out


def _root_tokens(css: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for selector, body in _rules(css):
        if selector != ":root":
            continue
        for name, value in _declarations(body):
            if name.startswith("--"):
                tokens[name] = value
    return tokens


def _resolve(value: str, tokens: dict[str, str], depth: int = 0) -> str:
    assert depth < 20, f"cyclic var() while resolving {value!r}"

    def swap(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        if name in tokens:
            return _resolve(tokens[name], tokens, depth + 1)
        assert fallback is not None, f"undefined custom property {name}"
        return _resolve(fallback.strip(), tokens, depth + 1)

    previous, current = None, value
    while previous != current:
        previous, current = current, _VAR.sub(swap, current)
    return " ".join(current.split())


HTML = PAGE.read_text(encoding="utf-8")
CSS = _stylesheet(HTML)
OUTSIDE, INSIDE = _split_by_scheme(CSS)

DARK_RAW = _root_tokens(OUTSIDE)
LIGHT_RAW = {**DARK_RAW, **_root_tokens(INSIDE)}
DARK = {k: _resolve(v, DARK_RAW) for k, v in DARK_RAW.items()}
LIGHT = {k: _resolve(v, LIGHT_RAW) for k, v in LIGHT_RAW.items()}
SCHEMES = {"dark": DARK, "light": LIGHT}


# --------------------------------------------------------------------------
# what each token is for
# --------------------------------------------------------------------------

#: Text that can land on any general surface.
TEXT = ("--fg", "--fg-strong", "--fg-muted", "--accent")

#: Surfaces general text can land on.
SURFACES = ("--bg", "--bg-elev")

#: (text, its one fill). These pair up and are never measured against the page:
#: --btn-fg is paper, and on the light ground paper IS the page.
PAIRED = (("--btn-fg", "--btn-bg"), ("--btn-fg-hover", "--btn-bg-hover"))

#: Fills that form a control, so the band itself has to read against the page.
FILLS = ("--btn-bg", "--btn-bg-hover")

#: Hairlines. Measured against the page for presence, not for AA.
LINES = ("--border", "--accent-soft")

#: Brand constants: the identity ramp, deliberately identical in both schemes.
BRAND_CONSTANTS = ("--glow", "--current", "--deep", "--ink", "--ground", "--paper")

#: The terminal card's own ramp, dark-locked in both schemes.
TERMINAL = ("--term-bg", "--term-line", "--term-dot", "--term-dim")

#: Not a colour.
NON_COLOUR = ("--mono",)

CLASSIFIED = (
    set(TEXT)
    | set(SURFACES)
    | {name for pair in PAIRED for name in pair}
    | set(FILLS)
    | set(LINES)
    | set(BRAND_CONSTANTS)
    | set(TERMINAL)
    | set(NON_COLOUR)
    | {"--accent-hover"}
)


def test_every_token_is_classified() -> None:
    """A new token must be given a role here, or the tables below quietly skip it
    and the gate reports a clean sheet it never measured."""

    declared = set(DARK_RAW)
    assert declared == CLASSIFIED, (
        "the stylesheet and this file disagree about which tokens exist.\n"
        f"  in the stylesheet only: {sorted(declared - CLASSIFIED)}\n"
        f"  in this file only:      {sorted(CLASSIFIED - declared)}"
    )


#: Exactly the tokens the light block re-points. A role missing here does not
#: error -- it silently keeps its dark value, which is how a light palette ends
#: up half dark, so the set is pinned rather than merely non-empty.
LIGHT_OVERRIDES = frozenset(
    {
        "--bg",
        "--bg-elev",
        "--fg",
        "--fg-strong",
        "--fg-muted",
        "--border",
        "--accent",
        "--accent-hover",
        "--accent-soft",
        "--btn-bg-hover",
        "--btn-fg-hover",
    }
)

#: Roles that are deliberately the SAME on both grounds. The primary button is
#: the one element that does not change: the deep fill carries paper text at
#: 5.10:1 on either ground, and it is the ground behind it that moves.
SHARED_ROLES = frozenset({"--btn-bg", "--btn-fg"})


def test_the_light_scheme_re_points_exactly_the_roles_it_should() -> None:
    declared = set(_root_tokens(INSIDE))
    assert declared == set(LIGHT_OVERRIDES), (
        "the light block and this file disagree about which roles move.\n"
        f"  only in the stylesheet: {sorted(declared - LIGHT_OVERRIDES)}\n"
        f"  only in this file:      {sorted(LIGHT_OVERRIDES - declared)}"
    )
    for name in sorted(LIGHT_OVERRIDES):
        assert DARK[name] != LIGHT[name], (
            f"{name} is re-declared for the light scheme but resolves to the "
            f"same value ({DARK[name]}) -- the override does nothing"
        )
    for name in sorted(SHARED_ROLES):
        assert DARK[name] == LIGHT[name], f"{name} is meant to be scheme-independent"


def test_the_light_scheme_is_actually_light() -> None:
    """Without this, deleting the whole light block leaves every ratio above
    passing: the light scheme would just BE the dark one, and a gate that only
    measures contrast would report a clean sheet."""

    assert luminance(DARK["--bg"]) < 0.05, (
        f"the default ground {DARK['--bg']} is not dark"
    )
    assert luminance(LIGHT["--bg"]) > 0.80, (
        f"the light ground {LIGHT['--bg']} is not light -- is the light block "
        "still there?"
    )
    assert luminance(LIGHT["--fg"]) < luminance(LIGHT["--bg"]), (
        "the light scheme paints light text on a light ground"
    )


def test_the_page_declares_it_supports_both_schemes() -> None:
    """Without ``color-scheme``, form controls, scrollbars and the paint before
    first layout stay dark on a light page."""

    assert re.search(r"html\s*\{[^}]*color-scheme:\s*light dark", CSS), (
        "html is not declared as supporting both colour schemes"
    )


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("text", TEXT)
def test_text_clears_aa_on_every_surface(scheme: str, surface: str, text: str) -> None:
    tokens = SCHEMES[scheme]
    got = contrast(tokens[text], tokens[surface])
    assert got >= AA_TEXT, (
        f"{scheme}: {text} ({tokens[text]}) on {surface} ({tokens[surface]}) "
        f"is {got:.2f}:1, below AA {AA_TEXT}"
    )


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize(("text", "fill"), PAIRED)
def test_paired_text_clears_aa_on_its_own_fill(scheme: str, text: str, fill: str) -> None:
    tokens = SCHEMES[scheme]
    got = contrast(tokens[text], tokens[fill])
    assert got >= AA_TEXT, (
        f"{scheme}: {text} ({tokens[text]}) on {fill} ({tokens[fill]}) is {got:.2f}:1"
    )


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("fill", FILLS)
def test_a_filled_control_also_reads_as_a_band(scheme: str, fill: str) -> None:
    """The second contrast. ADR-0228 recommended a colour on text contrast alone
    and the band vanished into the terminal background in 14 of 25 schemes; the
    lesson is that an element with a fill has two ratios, not one."""

    tokens = SCHEMES[scheme]
    got = contrast(tokens[fill], tokens["--bg"])
    assert got >= AA_NON_TEXT, (
        f"{scheme}: the {fill} band ({tokens[fill]}) against the page "
        f"({tokens['--bg']}) is {got:.2f}:1, below {AA_NON_TEXT} -- the control "
        "dissolves into the page even though its label reads fine"
    )


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("line", LINES)
def test_hairlines_are_present_in_both_schemes(scheme: str, line: str) -> None:
    """Cards are separated by their border, not by their fill (the fill is 1.06:1
    against the page in both schemes, by design). So the border is what has to
    survive -- if it flattens, the card boundary is gone."""

    tokens = SCHEMES[scheme]
    behind = tokens["--bg-elev"] if line == "--accent-soft" else tokens["--bg"]
    got = contrast(tokens[line], behind)
    assert got >= 1.2, f"{scheme}: {line} ({tokens[line]}) on {behind} is {got:.2f}:1"


def test_the_brand_constants_never_move_between_schemes() -> None:
    for name in BRAND_CONSTANTS:
        assert DARK[name] == LIGHT[name], (
            f"{name} was re-pointed for the light scheme. BRAND.md is explicit: "
            "the six brand colours are the identity ramp; the ROLES move, the "
            "ramp does not."
        )


def _terminal_declarations(css: str, tokens: dict[str, str]) -> dict[tuple[str, str], str]:
    """Every colour the terminal card paints, resolved."""

    wanted = {".term", ".term .bar", ".term .dot", ".tt", ".td", ".tp"}
    out = {}
    for selector, body in _rules(css):
        if selector not in wanted:
            continue
        for name, value in _declarations(body):
            if name in ("color", "background", "border", "border-bottom"):
                out[(selector, name)] = _resolve(value, tokens)
    return out


def test_the_terminal_card_is_dark_locked() -> None:
    """It is a picture of the real ANSI banner on a real terminal. Re-tinting it
    for a light page would show a thing Aelix never prints."""

    dark = _terminal_declarations(OUTSIDE, DARK_RAW)
    light = _terminal_declarations(OUTSIDE, LIGHT_RAW)
    assert dark, "the probe found no terminal-card colours at all"
    assert dark == light, (
        "the light scheme reaches inside the terminal card: "
        f"{ {k: (dark[k], light[k]) for k in dark if dark[k] != light[k]} }"
    )


def test_the_terminal_probe_can_see_a_leak() -> None:
    """Positive control for the test above."""

    leaked = OUTSIDE.replace(
        ".tt { color: var(--paper); font-weight: 700; }",
        ".tt { color: var(--fg-strong); font-weight: 700; }",
    )
    assert leaked != OUTSIDE, "the control could not doctor the stylesheet"
    dark = _terminal_declarations(leaked, DARK_RAW)
    light = _terminal_declarations(leaked, LIGHT_RAW)
    assert dark != light, "the probe cannot see a role token leaking into the terminal"


def _svg_fill(path: Path, marker: str) -> str:
    """Pull one fill out of a canonical brand vector."""

    text = path.read_text(encoding="utf-8")
    if marker == "word":
        found = re.search(r'<g fill="(#[0-9A-Fa-f]{6})">', text)
    else:
        found = re.search(r'<path fill="(#[0-9A-Fa-f]{6})" d="M462 12', text)
    assert found, f"could not read the {marker} fill out of {path.name}"
    return found.group(1).upper()


@pytest.mark.parametrize(
    ("scheme", "vector"), [("dark", "lockup-dark.svg"), ("light", "lockup-light.svg")]
)
def test_the_inline_lockup_matches_the_canonical_vector(scheme: str, vector: str) -> None:
    """The homepage inlines the lockup instead of linking it, so it cannot be
    kept honest by brand-sync. This is that check: the two fills the CSS swaps
    must equal the two fills that differ between the canonical files."""

    tokens = SCHEMES[scheme]
    css = OUTSIDE if scheme == "dark" else INSIDE
    fills = {
        selector: _resolve(dict(_declarations(body))["fill"], tokens)
        for selector, body in _rules(css)
        if selector in (".lockup .word", ".lockup .pixel")
    }
    assert set(fills) == {".lockup .word", ".lockup .pixel"}, (
        f"the {scheme} scheme does not set both lockup fills: {sorted(fills)}"
    )
    path = BRAND / vector
    assert fills[".lockup .word"].upper() == _svg_fill(path, "word")
    assert fills[".lockup .pixel"].upper() == _svg_fill(path, "pixel")


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
def test_the_browser_chrome_colour_matches_the_page(scheme: str) -> None:
    """``theme-color`` paints the mobile address bar. One value for both schemes
    puts a dark bar above a light page."""

    found = re.findall(
        r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})" '
        r'media="\(prefers-color-scheme: (\w+)\)">',
        HTML,
    )
    by_scheme = {name: colour for colour, name in found}
    assert set(by_scheme) == {"dark", "light"}, f"theme-color metas found: {found}"
    assert by_scheme[scheme].upper() == SCHEMES[scheme]["--bg"].upper(), (
        f"the {scheme} theme-color is {by_scheme[scheme]} but the page ground is "
        f"{SCHEMES[scheme]['--bg']}"
    )
