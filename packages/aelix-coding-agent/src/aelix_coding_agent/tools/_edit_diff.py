"""Edit matching + diff helpers — Pi parity ``core/tools/edit-diff.ts`` (734e08e).

Ports pi's exact-then-fuzzy matching, original-content right-to-left edit
application, the line-numbered diff renderer, and the verbatim error strings.
Kept separate from ``edit.py`` mirroring pi's module split.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass

# Pi parity ``normalizeForFuzzyMatch`` character classes.
_SMART_SINGLE = re.compile("[‘’‚‛]")
_SMART_DOUBLE = re.compile("[“”„‟]")
_DASHES = re.compile("[‐‑‒–—―−]")
_SPECIAL_SPACES = re.compile("[  -   　]")


class EditError(Exception):
    """Raised by :func:`apply_edits_to_normalized_content` with a pi-verbatim
    message; the tool turns it into an ``is_error`` result."""


@dataclass(frozen=True)
class _MatchedEdit:
    edit_index: int
    match_index: int
    match_length: int
    new_text: str


# --- line-ending / BOM helpers (Pi parity) ---------------------------------


def strip_bom(text: str) -> tuple[str, str]:
    """Return ``(bom, text_without_bom)`` — pi ``stripBom`` on the decoded str."""

    if text.startswith("﻿"):
        return "﻿", text[1:]
    return "", text


def detect_line_ending(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    if ending == "\n":
        return text
    return text.replace("\n", ending)


# --- fuzzy matching (Pi parity ``normalizeForFuzzyMatch`` + ``fuzzyFindText``)


def normalize_for_fuzzy_match(text: str) -> str:
    """Pi parity ``normalizeForFuzzyMatch`` — NFKC, per-line trailing-ws trim,
    then smart-quote / dash / special-space folding. No lowercasing."""

    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SMART_SINGLE.sub("'", text)
    text = _SMART_DOUBLE.sub('"', text)
    text = _DASHES.sub("-", text)
    text = _SPECIAL_SPACES.sub(" ", text)
    return text


@dataclass(frozen=True)
class _FuzzyMatch:
    found: bool
    index: int
    match_length: int
    used_fuzzy: bool


def fuzzy_find_text(content: str, old_text: str) -> _FuzzyMatch:
    """Pi parity ``fuzzyFindText`` — exact ``indexOf`` first, else fuzzy in the
    normalized space (matchLength is the fuzzy oldText length there)."""

    exact = content.find(old_text)
    if exact != -1:
        return _FuzzyMatch(True, exact, len(old_text), False)
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    idx = fuzzy_content.find(fuzzy_old)
    if idx == -1:
        return _FuzzyMatch(False, -1, 0, False)
    return _FuzzyMatch(True, idx, len(fuzzy_old), True)


def count_occurrences(content: str, old_text: str) -> int:
    """Pi parity ``countOccurrences`` — ALWAYS count in the fuzzy-normalized
    space (no exact-first fast path). Used for the uniqueness guard, so an
    exact + a fuzzy-equivalent occurrence must both count (else the guard would
    silently edit one of two semantically-identical matches)."""

    return normalize_for_fuzzy_match(content).count(normalize_for_fuzzy_match(old_text))


# --- error strings (Pi parity, single vs multi form by edit count) ---------


def _empty_old_text_error(path: str, i: int, total: int) -> str:
    if total == 1:
        return f"oldText must not be empty in {path}."
    return f"edits[{i}].oldText must not be empty in {path}."


def _not_found_error(path: str, i: int, total: int) -> str:
    if total == 1:
        return (
            f"Could not find the exact text in {path}. The old text must match "
            "exactly including all whitespace and newlines."
        )
    return (
        f"Could not find edits[{i}] in {path}. The oldText must match exactly "
        "including all whitespace and newlines."
    )


def _duplicate_error(path: str, i: int, total: int, occurrences: int) -> str:
    if total == 1:
        return (
            f"Found {occurrences} occurrences of the text in {path}. The text "
            "must be unique. Please provide more context to make it unique."
        )
    return (
        f"Found {occurrences} occurrences of edits[{i}] in {path}. Each oldText "
        "must be unique. Please provide more context to make it unique."
    )


def _no_change_error(path: str, total: int) -> str:
    if total == 1:
        return (
            f"No changes made to {path}. The replacement produced identical "
            "content. This might indicate an issue with special characters or "
            "the text not existing as expected."
        )
    return f"No changes made to {path}. The replacements produced identical content."


# --- apply (Pi parity ``applyEditsToNormalizedContent``) --------------------


def apply_edits_to_normalized_content(
    normalized_content: str, edits: list[dict], path: str
) -> tuple[str, str]:
    """Pi parity ``applyEditsToNormalizedContent``.

    ``edits`` is a list of ``{"oldText", "newText"}`` dicts. Each ``oldText`` is
    matched against the ORIGINAL (base) content — not a running buffer — and
    edits apply right-to-left by match index. If ANY edit needs fuzzy matching,
    the whole base switches to the fuzzy-normalized content (pi's behavior).
    Raises :class:`EditError` with a pi-verbatim message on any failure.
    """

    normalized_edits = [
        {
            "oldText": normalize_to_lf(e.get("oldText", "")),
            "newText": normalize_to_lf(e.get("newText", "")),
        }
        for e in edits
    ]
    total = len(normalized_edits)
    for i, e in enumerate(normalized_edits):
        if len(e["oldText"]) == 0:
            raise EditError(_empty_old_text_error(path, i, total))

    initial = [fuzzy_find_text(normalized_content, e["oldText"]) for e in normalized_edits]
    base_content = (
        normalize_for_fuzzy_match(normalized_content)
        if any(m.used_fuzzy for m in initial)
        else normalized_content
    )

    matched: list[_MatchedEdit] = []
    for i, e in enumerate(normalized_edits):
        m = fuzzy_find_text(base_content, e["oldText"])
        if not m.found:
            raise EditError(_not_found_error(path, i, total))
        occ = count_occurrences(base_content, e["oldText"])
        if occ > 1:
            raise EditError(_duplicate_error(path, i, total, occ))
        matched.append(_MatchedEdit(i, m.index, m.match_length, e["newText"]))

    matched.sort(key=lambda m: m.match_index)
    for i in range(1, len(matched)):
        prev, cur = matched[i - 1], matched[i]
        if prev.match_index + prev.match_length > cur.match_index:
            raise EditError(
                f"edits[{prev.edit_index}] and edits[{cur.edit_index}] overlap "
                f"in {path}. Merge them into one edit or target disjoint regions."
            )

    new_content = base_content
    for m in reversed(matched):
        new_content = (
            new_content[: m.match_index]
            + m.new_text
            + new_content[m.match_index + m.match_length :]
        )

    if base_content == new_content:
        raise EditError(_no_change_error(path, total))
    return base_content, new_content


# --- diff renderer (Pi parity ``generateDiffString``) ----------------------


def generate_diff_string(
    old_content: str, new_content: str, context_lines: int = 4
) -> tuple[str, int]:
    """Pi parity ``generateDiffString`` — a line-numbered +/-/space diff with
    long-context elision. Returns ``(diff_text, first_changed_line)`` where
    ``first_changed_line`` is the NEW-file 1-based line of the first change
    (``-1`` when there is no change). Used for ``EditToolDetails`` / the TUI
    edit card; not sent to the model.
    """

    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    width = len(str(max(len(old_lines), len(new_lines)) or 1))
    elision = " " + " " * width + " ..."
    out: list[str] = []
    first_changed = -1

    def num(n: int) -> str:
        return str(n).rjust(width)

    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # Pi parity: context lines are numbered by the OLD line number and
            # show the unchanged content. Index ``old_lines`` with the OLD (i)
            # offset — always in-bounds for an ``equal`` opcode, and identical
            # to ``new_lines[j…]`` content-wise (avoids the i!=j out-of-bounds /
            # phantom-context bug on line-count-changing edits).
            run = i2 - i1
            if run > context_lines * 2:
                for k in range(context_lines):
                    out.append(f" {num(i1 + k + 1)} {old_lines[i1 + k]}")
                out.append(elision)
                for k in range(context_lines, 0, -1):
                    out.append(f" {num(i2 - k + 1)} {old_lines[i2 - k]}")
            else:
                for k in range(run):
                    out.append(f" {num(i1 + k + 1)} {old_lines[i1 + k]}")
        else:  # delete / insert / replace
            if first_changed == -1:
                first_changed = j1 + 1
            for k in range(i1, i2):
                out.append(f"-{num(k + 1)} {old_lines[k]}")
            for k in range(j1, j2):
                out.append(f"+{num(k + 1)} {new_lines[k]}")
    return "\n".join(out), first_changed


def prepare_edit_arguments(args: dict) -> dict:
    """Pi parity ``prepareArguments`` — coerce ``edits`` sent as a JSON string,
    and fold legacy top-level ``oldText``/``newText`` into the ``edits`` array."""

    out = dict(args)
    if isinstance(out.get("edits"), str):
        try:
            parsed = json.loads(out["edits"])
            if isinstance(parsed, list):
                out["edits"] = parsed
        except (ValueError, TypeError):
            pass
    if isinstance(out.get("oldText"), str) and isinstance(out.get("newText"), str):
        # Pi parity: only seed from an existing edits ARRAY — a non-list value
        # (e.g. a string that failed JSON.parse) is discarded, NOT spread into
        # characters.
        existing = out.get("edits")
        edits = list(existing) if isinstance(existing, list) else []
        edits.append({"oldText": out["oldText"], "newText": out["newText"]})
        out = {k: v for k, v in out.items() if k not in ("oldText", "newText")}
        out["edits"] = edits
    return out


__all__ = [
    "EditError",
    "apply_edits_to_normalized_content",
    "count_occurrences",
    "detect_line_ending",
    "fuzzy_find_text",
    "generate_diff_string",
    "normalize_for_fuzzy_match",
    "normalize_to_lf",
    "prepare_edit_arguments",
    "restore_line_endings",
    "strip_bom",
]
