"""Prompt templates under the settings filter (#511, ADR-0279 D1, D10).

The ``prompts`` lists use the skills grammar: a plain path adds a directory,
``!glob`` / ``+X`` / ``-X`` decide which templates load. There is no panel
(owner decision 13); the filter applies at launch and on ``/reload``.
``--prompt-template`` paths are forced on, as ``--skill`` is; a template FILE
named there is that one template (C17), not its whole directory.

The kernel's :class:`PromptTemplate` carries no path and ``tests/pi_parity``
pins its fields, so each file is loaded on its own here to keep the path the
``!glob`` and exact-path entries need. The enumeration is the kernel's
(``prompt_templates._load_templates_from_dir``): direct ``.md`` children that
are files, sorted by name.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from aelix_agent_core.harness.prompt_templates import (
    PromptTemplate,
    PromptTemplateDiagnostic,
    load_prompt_templates,
)

from .resource_filter import (
    Target,
    effective,
    forget_resolved_paths,
    matches_any,
    source_entries,
    source_path,
    split_entry,
)
from .resource_selection import SettingsLists


@dataclass(frozen=True)
class PromptCatalog:
    templates: list[PromptTemplate] = field(default_factory=list)
    """The templates that load (enabled)."""
    diagnostics: list[PromptTemplateDiagnostic] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    off: list[str] = field(default_factory=list)
    """Names the settings turned off."""


def _files(directory: str) -> list[Path] | None:
    path = Path(directory)
    try:
        if path.is_dir():
            return [
                p
                for p in sorted(path.iterdir(), key=lambda p: p.name)
                if p.is_file() and p.name.endswith(".md")
            ]
        if path.is_file() and path.name.endswith(".md"):
            return [path]
    except OSError:
        return None
    return []


def load_prompt_catalog(
    sources: Sequence[tuple[str, str]],
    lists: SettingsLists,
    *,
    check_entries: bool = True,
) -> PromptCatalog:
    """Load ``(scope, path)`` *sources* plus the lists' path entries, then filter.

    *scope* is ``cli`` (a ``--prompt-template`` path: forced on, and a FILE
    there is that one template, never its directory's siblings), ``project``
    or ``global``. A path is a directory (its ``.md`` children) or one ``.md``
    file. Two sources that reach the same file load it once; a name that is
    already loaded is kept from the first source, as ``/<name>`` would expand.
    """

    forget_resolved_paths()  # a symlink may point elsewhere since the last load (MF1)
    ordered = list(sources)
    problems = list(lists.problems)
    for scope_list in (lists.global_list, lists.project_list):
        if scope_list is None:
            continue
        for entry in source_entries(scope_list.entries):
            directory = source_path(entry, scope_list.base_dir)
            if not os.path.exists(directory):
                problems.append(
                    f'{lists.label(scope_list.scope)}: "prompts" entry "{entry}" does not exist; it is ignored'
                )
                continue
            ordered.append((scope_list.scope, directory))

    diagnostics: list[PromptTemplateDiagnostic] = []
    loaded: list[tuple[PromptTemplate, str, str]] = []
    seen: set[str] = set()
    for scope, directory in ordered:
        files = _files(directory)
        if files is None:
            # Let the kernel produce its own diagnostic for an unreadable path.
            diagnostics.extend(load_prompt_templates(directory).diagnostics)
            continue
        for file in files:
            real = os.path.realpath(file)
            if real in seen:
                continue
            seen.add(real)
            result = load_prompt_templates(file)
            diagnostics.extend(result.diagnostics)
            loaded.extend((t, str(file), scope) for t in result.templates)

    kept: list[PromptTemplate] = []
    off: list[str] = []
    targets: list[Target] = []
    for template, path, scope in loaded:
        target = Target((template.name,), path, scope)
        targets.append(target)
        on = scope == "cli" or effective(target, lists.global_list, lists.project_list)[0]
        if on:
            kept.append(template)
        else:
            off.append(template.name)

    if check_entries:
        for scope_list in (lists.global_list, lists.project_list):
            if scope_list is None:
                continue
            for entry in scope_list.entries:
                if split_entry(entry)[0] and not matches_any(
                    entry, targets, scope_list.base_dir, list_scope=scope_list.scope
                ):
                    problems.append(
                        f'{lists.label(scope_list.scope)}: "prompts" entry "{entry}" matches no prompt template'
                    )
    return PromptCatalog(kept, diagnostics, problems, off)


__all__ = ["PromptCatalog", "load_prompt_catalog"]
