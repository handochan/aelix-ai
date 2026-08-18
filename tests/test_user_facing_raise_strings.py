"""Issue #189 — a drift guard on the one place "is this user-visible?" is decidable.

``tui/shell.py`` renders ``f"✖ {exc}"`` verbatim for anything that escapes
``harness.prompt``, and ``modes/print_mode.py`` prints ``str(exc)`` to stderr.
So a string literal handed to a ``raise`` inside the product packages is a
user-visible string BY CONSTRUCTION — no reachability analysis required. That
makes ``ast.Raise`` the only syntactic shape in this codebase where a static
gate can be both cheap and honest.

WHY NOT SCAN ALL STRING LITERALS. Measured at the commit that introduced this
file: the four tokens #189 names appear in ~24 of 16,973 non-docstring literals
across the product packages, and only ~4 of those are reachable by a user. The
rest are ``__all__`` entries (six adapters export ``register_all``), values in
private deferred-feature dicts, pydantic ``Field(description=…)`` metadata, and
an RPC error payload addressed to a machine. A gate with an 83% false-positive
rate is a gate that gets weakened the first time it fires. Scoped to ``raise``
arguments the same rule matched **3 hits out of 774 literals, all three of them
genuine** — and all three were fixed in the commit that added this file, so it
ships with an EMPTY allowlist and no maintenance tax.

KNOWN GAPS, stated rather than hidden — this gate is a drift guard, not the
#189 gate. ``tests/tui/test_first_run_no_provider.py`` is the gate; it drives
the real ``run_tui``.

  1. INDIRECTION IS INVISIBLE. A ``raise`` whose message comes from a
     module-level constant, a factory function, or a lambda has no literal for
     this scanner to see. ``extensions/headless_ui.py`` builds its 28 raises
     through ``_raise_headless(...)`` and ``extensions/api.py`` throws from
     inside a lambda; both carry sprint tokens today and neither is visible
     here. Resolving names would need an f-string-and-``.format()`` resolver
     that is wrong the first time someone reaches for either.
  2. THE OTHER HALF OF THE GLASS IS NOT A ``raise``. Text also arrives via
     ``ctx.commit(Text(...))``, a ``/help`` ``description=`` kwarg, and
     ``logger.warning``. Each is a different syntactic shape needing its own
     rule and its own allowlist.
  3. COUNT IS A RUNTIME PROPERTY. "Printed twice" — half of what #189 was
     filed about — cannot be expressed here at all.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The four packages whose exceptions can reach a terminal. ``aelix_agents`` is
# the bundled delegation extension; its failures surface through
# ``tui/commands.py`` the same way.
_PRODUCT_SRC = (
    "packages/aelix-ai/src",
    "packages/aelix-agent-core/src",
    "packages/aelix-coding-agent/src",
    "packages/aelix-server/src",
)

# #189's four tokens, plus the two sibling import prefixes and the RST
# double-backtick that rendered literally on the user's screen. Lowercased
# comparison except for ``Sprint `` — the trailing space is what keeps it off
# ``sprint`` inside an ordinary English sentence.
_LEAK_TOKENS = (
    "Sprint ",
    "register_all",
    "stream_fn",
    "aelix_ai.",
    "aelix_agent_core.",
    "aelix_coding_agent.",
    "``",
)


def _tracked_product_sources() -> list[Path]:
    """Git-tracked ``.py`` under the four product ``src`` roots.

    ``git ls-files`` rather than ``rglob`` on purpose: a maintainer's untracked
    scratch file must not be able to turn this gate red, and a build artifact
    must not be able to keep it green.
    """

    out = subprocess.run(
        ["git", "ls-files", "--", *[f"{root}/**/*.py" for root in _PRODUCT_SRC]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    return [REPO_ROOT / line for line in out if line.strip()]


def _raise_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string constant that is an argument to a ``raise Xxx(...)``.

    Walks the whole call, so implicit concatenation (which the parser has
    already folded) and f-string literal parts are both covered — an f-string
    arrives as a ``JoinedStr`` whose ``Constant`` parts this catches while its
    interpolated values, correctly, stay invisible.
    """

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        for sub in ast.walk(node.exc):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.append((sub.lineno, sub.value))
    return found


def _leaks(literals: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    hits = []
    for lineno, text in literals:
        for token in _LEAK_TOKENS:
            if token in text:
                hits.append((lineno, token, text))
    return hits


def test_no_raise_message_names_our_internals() -> None:
    """The gate itself."""

    offenders: list[str] = []
    scanned = 0
    literals_seen = 0
    for path in _tracked_product_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanned += 1
        literals = _raise_literals(tree)
        literals_seen += len(literals)
        rel = path.relative_to(REPO_ROOT)
        for lineno, token, text in _leaks(literals):
            offenders.append(f"{rel}:{lineno} — {token!r} in {text[:110]!r}")

    # A walk that found nothing must not read as a clean walk. Both floors are
    # far below the values measured when this gate was written (247 files, 774
    # literals), so ordinary churn never trips them but an empty or
    # mis-rooted scan does.
    assert scanned > 150, f"only {scanned} product sources scanned"
    assert literals_seen > 400, f"only {literals_seen} raise-literals seen"

    assert not offenders, (
        "raise() messages carrying repo-internal vocabulary reach the glass "
        "verbatim (tui/shell.py prints f'✖ {exc}'). Rewrite for the reader who "
        "will actually see it and keep the maintainer's half in a comment or "
        "docstring:\n  " + "\n  ".join(offenders)
    )


def test_the_scanner_detects_a_leak_when_there_is_one() -> None:
    """The positive control. A zero above is worth nothing without this.

    Each of the seven tokens gets its own planted raise, so a scanner that
    silently stopped matching one of them cannot hide behind the other six.
    """

    for token in _LEAK_TOKENS:
        source = f'''
def f():
    raise ValueError(
        "leading text {token} trailing text"
    )
'''
        hits = _leaks(_raise_literals(ast.parse(source)))
        assert hits, f"scanner blind to {token!r}"
        assert hits[0][1] == token

    # ...and the converse: an ordinary message must NOT fire, or the gate would
    # be unfalsifiable.
    clean = 'def f():\n    raise ValueError("the file could not be read")\n'
    assert not _leaks(_raise_literals(ast.parse(clean)))


def test_the_scanner_ignores_the_same_words_outside_a_raise() -> None:
    """Scope control — this is the whole reason the gate is shippable.

    ``__all__ = ["register_all"]`` is correct code in six adapters. A gate that
    fired on it would be turned off within a week.
    """

    source = '''
__all__ = ["register_all"]

DEFERRED = {"thing": "deferred to Sprint 7"}


def f(stream_fn):
    """Docstring naming ``aelix_ai.providers.anthropic.register_all()``."""

    return stream_fn
'''
    assert not _leaks(_raise_literals(ast.parse(source)))
