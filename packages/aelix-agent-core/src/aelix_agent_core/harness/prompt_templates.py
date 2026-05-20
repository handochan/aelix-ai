"""Pi parity: ``packages/agent/src/harness/prompt-templates.ts`` (SHA 734e08e).

Loads prompt templates from disk (``.md`` files with optional YAML
frontmatter), supports shell-style arg substitution, and emits
diagnostics for failures.

Pi reference: ``prompt-templates.ts:1-267``. The Aelix port mirrors Pi
line-by-line:

- :func:`load_prompt_templates` (Pi ``loadPromptTemplates``, lines 30-62)
- :func:`parse_command_args` (Pi ``parseCommandArgs``, lines 223-246)
- :func:`substitute_args` (Pi ``substituteArgs``, lines 249-262)
- :func:`format_prompt_template_invocation` (Pi
  ``formatPromptTemplateInvocation``, lines 265-267)

Aelix divergences from Pi (intentional, justified):

- No ``ExecutionEnv`` abstraction. Pi's ``ExecutionEnv`` exists for
  browser/Node interop; Aelix uses :mod:`pathlib` directly.
- ``loadSourcedPromptTemplates`` (Pi lines 70-93) is NOT ported — the
  Aelix harness wires sources via ``ExtensionSourceInfo`` on the
  caller side (Sprint 5a). Sprint 6h₁ scope is the bare loader surface.
- ``parseFrontmatter`` lives in :mod:`._frontmatter` so the skills
  loader can share the same implementation (Sprint 6h₁ W6 — W4 m4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aelix_agent_core.harness._frontmatter import parse_frontmatter

PromptTemplateDiagnosticCode = Literal[
    "file_info_failed",
    "list_failed",
    "read_failed",
    "parse_failed",
]


@dataclass(frozen=True)
class PromptTemplate:
    """Pi parity: ``PromptTemplate`` interface (``types.ts``).

    Shape:
      - ``name`` ← filename without ``.md`` extension
      - ``description`` ← frontmatter ``description`` OR first body line
        truncated to 60 chars (with ``...`` ellipsis when truncated).
        Sprint 6h₁ W6 (P-226 MAJOR): defaults to ``""`` so callers that
        omit the field match Pi's "optional with empty default" behaviour.
      - ``content`` ← markdown body after frontmatter
    """

    name: str
    description: str = ""
    content: str = ""


@dataclass(frozen=True)
class PromptTemplateDiagnostic:
    """Pi parity: ``PromptTemplateDiagnostic`` (``prompt-templates.ts:7-16``)."""

    code: PromptTemplateDiagnosticCode
    message: str
    path: str
    type: Literal["warning"] = "warning"


@dataclass(frozen=True)
class LoadPromptTemplatesResult:
    """Pi parity: ``{promptTemplates, diagnostics}`` return type.

    Aelix uses Python naming (``templates`` vs ``promptTemplates``) for
    the field while preserving Pi semantics.
    """

    templates: list[PromptTemplate] = field(default_factory=list)
    diagnostics: list[PromptTemplateDiagnostic] = field(default_factory=list)


# Pi parity: ``prompt-templates.ts:30-62`` — ``loadPromptTemplates``.
def load_prompt_templates(
    paths: str | Path | list[str | Path],
) -> LoadPromptTemplatesResult:
    """Pi parity: ``loadPromptTemplates(env, paths)``.

    For each input path:
      - Directory: load direct ``.md`` children (NON-recursive),
        sorted by name (Pi ``a.name.localeCompare(b.name)``).
      - File: load if ``.md``, skip otherwise.
      - Missing path: silently skip (Pi: ``info.error.code === "not_found"``
        skipped; other errors emit ``file_info_failed`` diagnostic).
    """

    templates: list[PromptTemplate] = []
    diagnostics: list[PromptTemplateDiagnostic] = []

    normalized: list[Path] = []
    if isinstance(paths, (str, Path)):
        normalized.append(Path(paths))
    else:
        normalized.extend(Path(p) for p in paths)

    for path in normalized:
        # Pi parity (``prompt-templates.ts:37-48``): ``env.fileInfo`` →
        # ``not_found`` is silent; other errors emit ``file_info_failed``.
        try:
            exists = path.exists()
        except OSError as exc:
            diagnostics.append(
                PromptTemplateDiagnostic(
                    code="file_info_failed",
                    message=str(exc),
                    path=str(path),
                )
            )
            continue
        if not exists:
            # Pi: ``not_found`` → silent skip (no diagnostic).
            continue

        # Pi parity (``prompt-templates.ts:49-59``): kind resolution +
        # branch on file/directory. We use ``path.is_dir()`` / ``is_file()``
        # which already follow symlinks — Pi's ``canonicalPath`` fallback
        # is implicit.
        try:
            if path.is_dir():
                _load_templates_from_dir(path, templates, diagnostics)
            elif path.is_file() and path.name.endswith(".md"):
                _load_template_from_file(path, templates, diagnostics)
        except OSError as exc:
            diagnostics.append(
                PromptTemplateDiagnostic(
                    code="file_info_failed",
                    message=str(exc),
                    path=str(path),
                )
            )

    return LoadPromptTemplatesResult(templates=templates, diagnostics=diagnostics)


# Pi parity: ``prompt-templates.ts:95-121`` — ``loadTemplatesFromDir``.
def _load_templates_from_dir(
    directory: Path,
    templates: list[PromptTemplate],
    diagnostics: list[PromptTemplateDiagnostic],
) -> None:
    """Non-recursive enumeration of ``.md`` children, sorted by name."""

    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        # Pi parity (``prompt-templates.ts:102-109``): ``list_failed``.
        diagnostics.append(
            PromptTemplateDiagnostic(
                code="list_failed",
                message=str(exc),
                path=str(directory),
            )
        )
        return

    for entry in entries:
        # Pi parity (``prompt-templates.ts:114-118``): files only;
        # ``.md`` suffix only.
        if not entry.is_file():
            continue
        if not entry.name.endswith(".md"):
            continue
        _load_template_from_file(entry, templates, diagnostics)


# Pi parity: ``prompt-templates.ts:123-165`` — ``loadTemplateFromFile``.
def _load_template_from_file(
    file_path: Path,
    templates: list[PromptTemplate],
    diagnostics: list[PromptTemplateDiagnostic],
) -> None:
    """Read, parse frontmatter, and append a :class:`PromptTemplate`."""

    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        # Pi parity (``prompt-templates.ts:129-136``): ``read_failed``.
        diagnostics.append(
            PromptTemplateDiagnostic(
                code="read_failed",
                message=str(exc),
                path=str(file_path),
            )
        )
        return

    frontmatter, body, parse_error = parse_frontmatter(raw)
    if frontmatter is None:
        # Pi parity (``prompt-templates.ts:140-147``): ``parse_failed``.
        # Sprint 6h₁ W6 (P-233): surface the YAML error so the diagnostic
        # is actionable instead of generic.
        suffix = f": {parse_error}" if parse_error else ""
        diagnostics.append(
            PromptTemplateDiagnostic(
                code="parse_failed",
                message=f"failed to parse YAML frontmatter{suffix}",
                path=str(file_path),
            )
        )
        return

    # Pi parity (``prompt-templates.ts:150-156``): description from
    # frontmatter or first non-empty body line truncated to 60 chars.
    first_line: str | None = None
    for line in body.split("\n"):
        if line.strip():
            first_line = line
            break

    raw_desc = frontmatter.get("description")
    description: str = raw_desc if isinstance(raw_desc, str) else ""
    if not description and first_line is not None:
        description = first_line[:60]
        if len(first_line) > 60:
            description += "..."

    # Pi parity (``prompt-templates.ts:159``): name = basename without ``.md``.
    # Sprint 6h₁ W6 (P-234): case-insensitive strip via :meth:`str.lower`
    # so ``.md`` / ``.MD`` / ``.Md`` / ``.mD`` all normalise the same way.
    name = file_path.name
    if name.lower().endswith(".md"):
        name = name[:-3]

    templates.append(
        PromptTemplate(name=name, description=description, content=body)
    )


# Pi parity: ``prompt-templates.ts:223-246`` — ``parseCommandArgs``.
def parse_command_args(args_string: str) -> list[str]:
    """Pi parity: shell-style argument parser with single/double quotes.

    Handles:
      - Whitespace (space, tab) as separator.
      - Single (``'``) and double (``"``) quote pairs preserve internal
        whitespace.
      - Quote types do not nest (matches Pi behaviour byte-for-byte).
    """

    args: list[str] = []
    current = ""
    in_quote: str | None = None

    for char in args_string:
        if in_quote is not None:
            if char == in_quote:
                in_quote = None
            else:
                current += char
        elif char == '"' or char == "'":
            in_quote = char
        elif char == " " or char == "\t":
            if current:
                args.append(current)
                current = ""
        else:
            current += char

    if current:
        args.append(current)
    return args


# Pi parity: ``prompt-templates.ts:249-262`` — ``substituteArgs``.
_POSITIONAL_RE = re.compile(r"\$(\d+)")
_RANGE_RE = re.compile(r"\$\{@:(\d+)(?::(\d+))?\}")


def substitute_args(content: str, args: list[str]) -> str:
    """Pi parity: ``substituteArgs(content, args)``.

    Placeholders supported (order matches Pi):
      1. ``$N`` (1-indexed positional)
      2. ``${@:N}`` and ``${@:N:L}`` (slice from N, optional length L)
      3. ``$ARGUMENTS`` (all args joined with spaces)
      4. ``$@`` (alias for ``$ARGUMENTS``)
    """

    def _positional(match: re.Match[str]) -> str:
        # Pi parity: ``args[parseInt(num, 10) - 1] ?? ""``.
        num = int(match.group(1))
        idx = num - 1
        if 0 <= idx < len(args):
            return args[idx]
        return ""

    result = _POSITIONAL_RE.sub(_positional, content)

    def _range(match: re.Match[str]) -> str:
        start_str = match.group(1)
        length_str = match.group(2)
        # Pi parity: ``start = parseInt(startStr, 10) - 1; if (start < 0) start = 0``.
        start = int(start_str) - 1
        if start < 0:
            start = 0
        if length_str:
            length = int(length_str)
            return " ".join(args[start : start + length])
        return " ".join(args[start:])

    result = _RANGE_RE.sub(_range, result)

    all_args = " ".join(args)
    result = result.replace("$ARGUMENTS", all_args)
    result = result.replace("$@", all_args)
    return result


# Pi parity: ``prompt-templates.ts:265-267`` — ``formatPromptTemplateInvocation``.
def format_prompt_template_invocation(
    template: PromptTemplate,
    args: list[str] | None = None,
    prefix: str | None = None,
) -> str:
    """Pi parity: ``formatPromptTemplateInvocation(template, args)``.

    Pi signature is ``(template, args = [])``. The ``prefix`` argument
    is an Aelix-additive convenience: when supplied, the returned string
    is ``f"{prefix}{substituted}"`` (Sprint 6h₁ leaves the prefix path
    opt-in to keep Pi byte-parity when ``prefix=None``).
    """

    args_list = list(args) if args is not None else []
    substituted = substitute_args(template.content, args_list)
    if prefix is None:
        return substituted
    return f"{prefix}{substituted}"


__all__ = [
    "LoadPromptTemplatesResult",
    "PromptTemplate",
    "PromptTemplateDiagnostic",
    "PromptTemplateDiagnosticCode",
    "format_prompt_template_invocation",
    "load_prompt_templates",
    "parse_command_args",
    "substitute_args",
]
