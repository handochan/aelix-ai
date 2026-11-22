"""#845 - every ADR has an entry of its own in the ADR index, and no index link names a missing file.

On 0436193c, ``docs/decisions/README.md`` had no entry for 100 of the 300 ADR files (most of
0094 to 0216, ADR-0157 among them), although the close-out skill says the index has one entry
per ADR. This test is what holds that rule.

An entry is a line of the README's ``## Index`` section, outside a fenced code block, that is
either

- a table row (it starts with ``|``) whose first cell names the ADR: the first four-digit number
  in that cell is the ADR's, written bare, linked (``[0157](...)``) or as ``ADR-0157``; or
- a list item (it starts with ``- ``) whose text starts with the ADR's number, bare, linked
  (``[0157 — ...](...)``) or as ``ADR-0157``. Its indented continuation lines belong to it.

The entry must also link that ADR's own file. A line that only mentions an ADR further on (a row
whose first cell has no number, a list item that starts with prose) is not its entry. The ADR
files are listed from the directory, not from git, so a new ADR that is not yet added to git fails
here too. Two entries for one ADR are not refused: ADR-0221 already had two on 0436193c.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISIONS = REPO_ROOT / "docs" / "decisions"
INDEX = DECISIONS / "README.md"

_NUMBER = re.compile(r"(?<!\d)\d{4}(?!\d)")
# The first cell of a table row: everything up to the first "|" that is not escaped.
_FIRST_CELL = re.compile(r"^\|((?:\\.|[^|\\])*)\|")
# A list item whose text starts with an ADR number: bare, linked, or as ADR-NNNN.
_LIST_ITEM_NUMBER = re.compile(r"^- \[?(?:ADR-)?(\d{4})(?!\d)")
# A line that opens or closes a fenced code block (CommonMark: at most three spaces of indent).
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A relative link to an ADR file, with an optional "./" and an optional "#anchor".
_ADR_LINK = re.compile(r"\]\((?:\./)?(\d{4}-[^)\s#]*\.md)(?:#[^)\s]*)?\)")


def _adr_files() -> list[str]:
    return sorted(p.name for p in DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md"))


def _outside_fences(lines: list[str]) -> list[str]:
    """``lines`` with each line of a fenced code block, its fences included, made empty."""
    kept: list[str] = []
    fence = ""
    for line in lines:
        match = _FENCE.match(line)
        if not fence:
            if match is not None:
                fence = match.group(1)
            kept.append("" if fence else line)
            continue
        # A closing fence: the opening's character, at least as many, and nothing after it.
        if (
            match is not None
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not match.group(2).strip()
        ):
            fence = ""
        kept.append("")
    return kept


def _entry_number(line: str) -> str | None:
    """The ADR number whose entry ``line`` starts, or None when it starts no entry."""
    if line.startswith("|"):
        cell = _FIRST_CELL.match(line)
        number = _NUMBER.search(cell.group(1)) if cell is not None else None
        return number.group() if number is not None else None
    item = _LIST_ITEM_NUMBER.match(line)
    return item.group(1) if item is not None else None


def _index_entries(text: str) -> list[tuple[str, str]]:
    """(ADR number, entry text) for each entry of the ``## Index`` section."""
    lines = text.splitlines()
    start = lines.index("## Index")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = _outside_fences(lines[start + 1 : end])
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(section):
        line = section[i]
        i += 1
        number = _entry_number(line)
        if number is None:
            continue
        block = [line]
        if line.startswith("- "):
            while i < len(section) and section[i][:1] in (" ", "\t") and section[i].strip():
                block.append(section[i])
                i += 1
        entries.append((number, "\n".join(block)))
    return entries


def test_every_adr_file_has_an_entry_of_its_own() -> None:
    files = _adr_files()
    assert files, DECISIONS
    entries = _index_entries(INDEX.read_text(encoding="utf-8"))
    without = [
        name
        for name in files
        if not any(
            number == name[:4] and name in _ADR_LINK.findall(entry) for number, entry in entries
        )
    ]
    assert without == [], (
        f"{len(without)} ADR files have no entry of their own under '## Index' in "
        "docs/decisions/README.md (a table row whose first cell names the ADR, or a list item "
        f"that starts with its number, and which links its file): {without}"
    )


def test_no_adr_link_in_the_index_names_a_missing_file() -> None:
    links = set(_ADR_LINK.findall(INDEX.read_text(encoding="utf-8")))
    assert links, INDEX
    # Compared by exact name: a case-insensitive file system would find a miscased link.
    present = {p.name for p in DECISIONS.iterdir()}
    gone = sorted(link for link in links if link not in present)
    assert gone == [], f"docs/decisions/README.md links ADR files that do not exist: {gone}"
