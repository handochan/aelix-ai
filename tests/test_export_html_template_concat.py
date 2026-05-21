"""Sprint 6h₅d §D (MINOR-1 carry-forward from ADR-0086) —
:data:`_THEME_CSS` string concatenation invariants.

The Sprint 6h₅c implementation interpolated ``{_PYGMENTS_CSS}`` into a
single 196-line f-string which forced brace-doubling on every CSS rule.
Sprint 6h₅d splits the constant into ``_BASE_THEME_CSS`` (everything
before the Pygments interpolation site) + :data:`_PYGMENTS_CSS` +
``_IMAGE_CSS`` (everything after) and concatenates with newline
separators.

These tests lock the three load-bearing observable invariants:

  - Pygments token class output is reachable through ``_THEME_CSS``.
  - Base-theme CSS variables + role section literals survive the refactor.
  - Image rule classes ship in the concatenated result.

The renderer-side fidelity is covered separately by
``tests/test_export_html_visual_fidelity.py``.
"""

from __future__ import annotations


def test_theme_css_contains_pygments_classes() -> None:
    from aelix_coding_agent._export_html.template import _THEME_CSS

    assert ".pyg" in _THEME_CSS


def test_theme_css_contains_base_theme() -> None:
    from aelix_coding_agent._export_html.template import _THEME_CSS

    assert "--bg: #1e1e1e" in _THEME_CSS
    assert ".message-image" in _THEME_CSS
    assert ".tool-image" in _THEME_CSS
