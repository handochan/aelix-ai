"""Pi parity: YAML frontmatter parsing — shared between prompt_templates + skills.

Pi co-locates ``parseFrontmatter`` inside each of ``prompt-templates.ts``
and ``skills.ts``; Aelix extracts the implementation here so the two
loaders share a single source of truth (Sprint 6h₁ W6 — W4 m4).
Behaviour is byte-for-byte equivalent to Pi:

- Normalise ``\\r\\n`` / ``\\r`` line endings to ``\\n``.
- No leading ``---`` → no frontmatter; return ``({}, body)``.
- No closing ``\\n---`` → no frontmatter; return ``({}, body)``.
- YAML between delimiters parsed via :func:`yaml.safe_load`.
- Parse failure → return ``(None, body, error_message)``; callers
  surface the error in the diagnostic message (Sprint 6h₁ W6 — P-233).
"""

from __future__ import annotations

from typing import Any

import yaml  # PyYAML


def parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Extract YAML frontmatter between ``---`` delimiters.

    Returns ``(frontmatter_dict, body, error_message)``.

    - On success: ``(dict, body, None)``.
    - On YAML parse error: ``(None, body, error_message)`` — callers
      surface ``error_message`` in their ``parse_failed`` diagnostic
      (Sprint 6h₁ W6, P-233).
    - Empty / missing frontmatter returns ``({}, body, None)``.
    """

    # Pi parity: CR/CRLF normalisation.
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")

    # Pi parity: no leading ``---`` → no frontmatter, body is the entire
    # normalised content.
    if not normalized.startswith("---"):
        return {}, normalized, None

    # Pi parity: closing ``\n---`` after the leading ``---``
    # (start index 3 in Pi → search from index 3).
    end_index = normalized.find("\n---", 3)
    if end_index == -1:
        return {}, normalized, None

    # Pi parity:
    # - ``yamlString = normalized.slice(4, endIndex)``
    # - ``body = normalized.slice(endIndex + 4).trim()``
    yaml_string = normalized[4:end_index]
    body = normalized[end_index + 4 :].strip()

    try:
        parsed = yaml.safe_load(yaml_string)
    except yaml.YAMLError as exc:
        # Sprint 6h₁ W6 (P-233): preserve YAML's diagnostic message so the
        # downstream ``parse_failed`` diagnostic is actionable instead of
        # the generic "failed to parse YAML frontmatter".
        return None, body, str(exc)

    frontmatter: dict[str, Any]
    if parsed is None:
        frontmatter = {}
    elif isinstance(parsed, dict):
        # Cast — yaml.safe_load returns Any.
        frontmatter = {str(k): v for k, v in parsed.items()}
    else:
        frontmatter = {}

    return frontmatter, body, None


__all__ = ["parse_frontmatter"]
