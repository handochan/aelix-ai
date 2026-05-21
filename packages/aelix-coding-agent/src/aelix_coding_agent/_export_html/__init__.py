"""Pi parity: ``packages/coding-agent/src/core/export-html/`` package
re-export.

Sprint 6h₅c (Phase 4.16, ADR-0085, P-372). The Sprint 6h₃ minimal renderer
lived in a single ``_export_html.py`` file; visual fidelity (CSS framework,
syntax highlighting via Pygments, markdown rendering via markdown-it-py,
inline image rendering) is large enough to warrant a directory layout
mirroring Pi's ``coding-agent/src/core/export-html/``. The public surface
is unchanged — callers still import ``export_html`` from
:mod:`aelix_coding_agent._export_html`.

NOT in scope for Sprint 6h₅c (carry-forward to 6h₅d):

  - ANSI → HTML pipeline (Pi ``ansi-to-html.ts``).
  - Per-tool renderer templates (Pi ``tool-renderer.ts``).
  - Sidebar / tree-navigation client-side JS.
  - Pi luminance-based color-derivation theme.
"""

from __future__ import annotations

from aelix_coding_agent._export_html.format import export_html

__all__ = ["export_html"]
