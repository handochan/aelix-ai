"""The homepage must render on the reader's ground, and both grounds must be legible.

WHY THIS FILE EXISTS. ``site/`` is published straight to GitHub Pages with no
build step, and nothing in this repository reads its stylesheet. That is how
ADR-0228 happened: a colour shipped that measured 1.16:1 on a real terminal
scheme because there was no gate on colour at all, only on prose. Adding a
second palette doubles the surface for exactly that mistake, and the light half
is the half nobody looks at -- a maintainer on a dark laptop can break it and
see nothing.

WHAT IT MEASURES.

1. Every custom property has a declared role, and the light block re-points
   exactly the roles it is meant to -- each one actually changing value.
2. The cascade is read the way a browser reads it. A ``@media`` query carries no
   specificity, so a ``:root`` BELOW the light block would win for a light
   reader; the token maps are therefore built in document order, and a separate
   test pins that no ``:root`` follows the light block at all.
3. Every colour lives in a token. A hex literal in any rule body is a failure,
   because a literal is invisible to a palette and a palette is the only thing
   the tables below can measure.
4. Every text token clears WCAG AA (4.5:1) on every surface token it can land
   on, in BOTH schemes -- not just the pairs used today. Anything with a fill or
   an outline of its own is measured TWICE: the text on it, and the band itself
   against the page. A control that reads fine but dissolves into the page is
   the ADR-0228 failure exactly, and text contrast alone cannot see it.
5. The terminal card is dark-locked, fills AND ``color-scheme`` -- it is a
   picture of the real ANSI banner, and if the light scheme could reach inside
   it the page would show something Aelix never prints. Checked against BOTH
   rule sets, since a leak is far likelier to be written inside the light block
   than outside it.
6. The inline lockup matches the canonical brand vectors, checked against the
   bytes of ``lockup-dark.svg`` and ``lockup-light.svg`` themselves, so the page
   cannot drift from BRAND.md.

Points 2, 3 and the both-rule-sets half of 5 exist because an adversarial review
demonstrated the earlier version passing while the page was broken.

Every detector has a positive control: one that silently stopped matching would
otherwise pass by seeing nothing.
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
#: A hairline is a hint, not a component; it only has to be present.
HAIRLINE = 1.2


# --------------------------------------------------------------------------
# colour maths
# --------------------------------------------------------------------------


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.strip()
    if not text.startswith("#"):
        raise ValueError(f"not an opaque hex colour: {colour!r}")
    text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


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
# a very small stylesheet reader, in document order
# --------------------------------------------------------------------------

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STYLE = re.compile(r"<style>(.*?)</style>", re.S)
_LIGHT_AT = re.compile(r"@media\s*\(\s*prefers-color-scheme:\s*light\s*\)\s*\{", re.I)
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,([^()]*))?\)")
#: A literal colour: hex, or any of the functional notations.
_LITERAL = re.compile(r"#[0-9A-Fa-f]{3,8}\b|\brgba?\(|\bhsla?\(|\bcolor-mix\(")


def _stylesheet(html: str) -> str:
    found = _STYLE.search(html)
    assert found, "site/index.html has no <style> block"
    return _COMMENT.sub("", found.group(1))


def segments(css: str) -> list[tuple[str, bool]]:
    """The stylesheet in document order as (text, is_inside_a_light_media_block)."""
    out: list[tuple[str, bool]] = []
    cursor = 0
    while True:
        found = _LIGHT_AT.search(css, cursor)
        if not found:
            out.append((css[cursor:], False))
            return out
        out.append((css[cursor : found.start()], False))
        depth, index = 1, found.end()
        while index < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        out.append((css[found.end() : index - 1], True))
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


def rules_in_order(css: str) -> list[tuple[str, str, bool]]:
    """(selector, body, is_inside_light) across the whole sheet, in document order."""
    out = []
    for text, in_light in segments(css):
        for selector, body in _rules(text):
            out.append((selector, body, in_light))
    return out


def _declarations(body: str) -> list[tuple[str, str]]:
    out = []
    for piece in body.split(";"):
        if ":" not in piece:
            continue
        name, value = piece.split(":", 1)
        out.append((name.strip(), " ".join(value.split())))
    return out


def token_map(css: str, *, include_light: bool) -> dict[str, str]:
    """Merge every ``:root`` in DOCUMENT ORDER, the way equal specificity resolves.

    A media query adds no specificity, so a ``:root`` written after the light
    block wins for a light reader. Merging by dictionary insertion instead would
    model a cascade browsers do not have -- and did, until a review showed the
    earlier version of this file passing on a page whose light palette a later
    ``:root`` had silently undone.
    """
    tokens: dict[str, str] = {}
    for selector, body, in_light in rules_in_order(css):
        if selector != ":root" or (in_light and not include_light):
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

DARK_RAW = token_map(CSS, include_light=False)
LIGHT_RAW = token_map(CSS, include_light=True)
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

#: A control's OUTLINE is also the control: .btn:hover draws its border in this
#: and nothing else changes, so if it flattens the button loses its edge exactly
#: when the reader is pointing at it.
OUTLINES = ("--accent-hover",)

#: Hairlines. Measured against the page for presence, not for AA.
LINES = ("--border", "--accent-soft")

#: Brand constants: the identity ramp, deliberately identical in both schemes.
BRAND_CONSTANTS = ("--glow", "--current", "--deep", "--ink", "--ground", "--paper")

#: The terminal card's own ramp, dark-locked in both schemes.
TERMINAL = ("--term-bg", "--term-line", "--term-dot", "--term-dim",
            "--term-a1", "--term-a2", "--term-a3", "--term-a4", "--term-a5", "--term-a6")

#: Not a colour.
NON_COLOUR = ("--mono",)

CLASSIFIED = (
    set(TEXT)
    | set(SURFACES)
    | {name for pair in PAIRED for name in pair}
    | set(FILLS)
    | set(OUTLINES)
    | set(LINES)
    | set(BRAND_CONSTANTS)
    | set(TERMINAL)
    | set(NON_COLOUR)
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

#: The only selectors the light block is allowed to touch. Anything else there
#: is a rule the token tables cannot see.
LIGHT_SELECTORS = frozenset({":root", ".lockup .word", ".lockup .pixel"})

#: Selectors that belong to the dark-locked terminal card.
_TERMINAL_SELECTOR = re.compile(r"^\.(term\b|t[1-6]$|tt$|td$|tp$)")


def _light_root_names() -> set[str]:
    names: set[str] = set()
    for selector, body, in_light in rules_in_order(CSS):
        if in_light and selector == ":root":
            names.update(n for n, _ in _declarations(body) if n.startswith("--"))
    return names


# --------------------------------------------------------------------------


def test_every_token_is_classified() -> None:
    """A new token must be given a role above, or the tables below quietly skip
    it and the gate reports a clean sheet it never measured."""

    declared = set(DARK_RAW)
    assert declared == CLASSIFIED, (
        "the stylesheet and this file disagree about which tokens exist.\n"
        f"  in the stylesheet only: {sorted(declared - CLASSIFIED)}\n"
        f"  in this file only:      {sorted(CLASSIFIED - declared)}"
    )


def test_no_root_block_follows_the_light_block() -> None:
    """A ``@media`` query adds no specificity, so a ``:root`` written after the
    light block wins for a light reader and undoes the palette. Keeping every
    other ``:root`` above it is what makes "the light block wins" true rather
    than merely intended."""

    seen_light = False
    for selector, _body, in_light in rules_in_order(CSS):
        if in_light:
            seen_light = True
            continue
        assert not (seen_light and selector == ":root"), (
            "a :root block sits below the light @media block. Everything it "
            "declares beats the light palette for a light reader."
        )


def test_the_light_scheme_re_points_exactly_the_roles_it_should() -> None:
    declared = _light_root_names()
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


def test_the_light_block_touches_only_known_selectors() -> None:
    """A non-:root rule inside the light block paints something no token table
    can see -- ``.feat { background: #0B0F14 }`` there would give a light reader
    black cards carrying light-mode text, and every ratio below would still
    pass."""

    found = {selector for selector, _body, in_light in rules_in_order(CSS) if in_light}
    assert found == set(LIGHT_SELECTORS), (
        f"the light block touches {sorted(found)}; expected {sorted(LIGHT_SELECTORS)}"
    )


def test_the_light_scheme_is_actually_light() -> None:
    """Without this, deleting the whole light block leaves every ratio above
    passing: the light scheme would just BE the dark one, and a gate that only
    measures contrast would report a clean sheet."""

    assert luminance(DARK["--bg"]) < 0.05, f"the default ground {DARK['--bg']} is not dark"
    assert luminance(LIGHT["--bg"]) > 0.80, (
        f"the light ground {LIGHT['--bg']} is not light -- is the light block still there?"
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


def _literals_outside_root(css: str) -> list[tuple[str, str]]:
    out = []
    for selector, body, _in_light in rules_in_order(css):
        if selector == ":root":
            continue
        for name, value in _declarations(body):
            if _LITERAL.search(value):
                out.append((selector, f"{name}: {value}"))
    return out


def test_every_colour_lives_in_a_token() -> None:
    """A hex written straight into a rule cannot follow a palette, and no table
    in this file can see it. ``.sub { color: #C6D6DC }`` is 1.42:1 on the light
    ground and would have passed every other test here."""

    stray = _literals_outside_root(CSS)
    assert not stray, (
        "colour literals outside :root -- they cannot follow the scheme:\n"
        + "\n".join(f"  {sel} {{ {decl} }}" for sel, decl in stray)
    )


def test_the_literal_detector_can_see_a_literal() -> None:
    """Positive control."""

    doctored = CSS.replace(
        ".sub { max-width: 62ch;", ".sub { color: #C6D6DC; max-width: 62ch;", 1
    )
    assert doctored != CSS, "the control could not doctor the stylesheet"
    assert _literals_outside_root(doctored), "the detector cannot see a hardcoded colour"


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
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("outline", OUTLINES)
def test_a_control_outline_reads_against_both_surfaces(
    scheme: str, surface: str, outline: str
) -> None:
    """.btn:hover changes nothing but this border. If it flattens, the button
    loses its edge at the moment the reader is pointing at it."""

    tokens = SCHEMES[scheme]
    got = contrast(tokens[outline], tokens[surface])
    assert got >= AA_NON_TEXT, (
        f"{scheme}: {outline} ({tokens[outline]}) on {surface} is {got:.2f}:1, "
        f"below {AA_NON_TEXT}"
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
    assert got >= HAIRLINE, f"{scheme}: {line} ({tokens[line]}) on {behind} is {got:.2f}:1"


def test_the_brand_constants_never_move_between_schemes() -> None:
    for name in BRAND_CONSTANTS:
        assert DARK[name] == LIGHT[name], (
            f"{name} was re-pointed for the light scheme. BRAND.md is explicit: "
            "the six brand colours are the identity ramp; the ROLES move, the "
            "ramp does not."
        )


def _terminal_colours(css: str, *, include_light: bool) -> dict[tuple[str, str], str]:
    """Every colour the terminal card paints, resolved for one scheme.

    Property names are not enumerated -- any declaration that resolves to a
    colour counts. An earlier version listed four property names and could not
    see ``background-color`` on a selector it did cover.
    """
    tokens = token_map(css, include_light=include_light)
    out = {}
    for selector, body, in_light in rules_in_order(css):
        if in_light and not include_light:
            continue
        if not _TERMINAL_SELECTOR.match(selector):
            continue
        for name, value in _declarations(body):
            resolved = _resolve(value, tokens)
            if _LITERAL.search(resolved):
                out[(selector, name)] = resolved
    return out


def test_the_terminal_card_is_dark_locked() -> None:
    """It is a picture of the real ANSI banner on a real terminal. Re-tinting it
    for a light page would show a thing Aelix never prints. Both rule sets are
    read, not just the one outside the media query: a leak is likelier to be
    written INSIDE the light block, next to every other light-mode rule."""

    dark = _terminal_colours(CSS, include_light=False)
    light = _terminal_colours(CSS, include_light=True)
    assert dark, "the probe found no terminal-card colours at all"
    differ = {k: (dark.get(k), light.get(k)) for k in set(dark) | set(light)
              if dark.get(k) != light.get(k)}
    assert not differ, f"the light scheme reaches inside the terminal card: {differ}"


@pytest.mark.parametrize(
    ("where", "doctored"),
    [
        (
            "outside the light block",
            lambda css: css.replace(
                ".tt { color: var(--paper); font-weight: 700; }",
                ".tt { color: var(--fg-strong); font-weight: 700; }",
            ),
        ),
        (
            "inside the light block, after the card's own rules",
            lambda css: css + "\n@media (prefers-color-scheme: light) {\n"
            "  .term { background: var(--bg-elev); }\n}\n",
        ),
        (
            "as a property name the old probe did not list",
            lambda css: css.replace(
                "background: var(--term-dot); }", "background-color: var(--border); }"
            ),
        ),
        (
            "on a ramp step the old probe did not cover",
            lambda css: css.replace(".t5 { color: var(--term-a5); }", ".t5 { color: var(--fg); }"),
        ),
    ],
)
def test_the_terminal_probe_can_see_each_shape_of_leak(where: str, doctored) -> None:
    """Positive controls, one per hole an adversarial review demonstrated."""

    leaked = doctored(CSS)
    assert leaked != CSS, f"the control for '{where}' could not doctor the stylesheet"
    dark = _terminal_colours(leaked, include_light=False)
    light = _terminal_colours(leaked, include_light=True)
    assert dark != light, f"the probe cannot see a leak {where}"


def test_the_terminal_card_opts_out_of_the_page_colour_scheme() -> None:
    """``color-scheme`` is what the UA paints a scroll container's scrollbar
    with, and ``.term`` is ``overflow-x: auto``. The page now says ``light
    dark``, so without this the card gets a pale scrollbar across its near-black
    foot -- the one theme role that still reached inside it."""

    rule = next((body for sel, body, _ in rules_in_order(CSS) if sel == ".term"), None)
    assert rule is not None, "no .term rule found at all"
    assert re.search(r"color-scheme:\s*dark", rule), (
        ".term does not pin color-scheme: dark, so its scrollbar follows the page"
    )


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
    want_light = scheme == "light"
    fills = {}
    for selector, body, in_light in rules_in_order(CSS):
        if selector not in (".lockup .word", ".lockup .pixel") or in_light != want_light:
            continue
        fills[selector] = _resolve(dict(_declarations(body))["fill"], tokens)
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
