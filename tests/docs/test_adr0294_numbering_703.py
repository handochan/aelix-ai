"""#703 - ADR-0294's Decision items are numbered as they are read, and its "§N" say which.

Fix round 1 added two items (the OAuth refresh and the proxy tunnel) between items 2
and 3 and numbered them 5 and 6, so the source read 1, 2, 5, 6, 3, 4. CommonMark
renders an ordered list from its first number, so a reader saw 1 to 6 in order and
every "§N" written against the source numbers named another item: the Relates line's
"§2 below sends a refresh again" meant the refresh, which is the third item read.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR = (
    REPO_ROOT
    / "docs/decisions/0294-a-failure-the-strict-relaxation-answered-is-sent-again-and-shown-as-one-line.md"
)


def _decision(text: str) -> str:
    return text.split("\n## Decision\n", 1)[1].split("\n## ", 1)[0]


def _items(text: str) -> list[tuple[str, str]]:
    return re.findall(r"(?m)^(\d+)\. \*\*(.+?)\*\*", _decision(text))


def test_the_decision_items_are_numbered_in_reading_order() -> None:
    numbers = [number for number, _title in _items(ADR.read_text(encoding="utf-8"))]
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)]
    assert len(numbers) == 6


def test_every_section_reference_names_the_item_it_means() -> None:
    text = ADR.read_text(encoding="utf-8")
    titles = dict(_items(text))
    own = re.findall(r"(?<!ADR-\d\d\d\d )§(\d+)", text)
    assert own and set(own) <= set(titles), (own, sorted(titles))

    refresh = re.search(r"§(\d+) below sends a refresh again", text)
    assert refresh is not None
    assert titles[refresh.group(1)].startswith("An OAuth token refresh")
    handshake = re.search(r"before any byte of the request\s+was written \(§(\d+)\)", text)
    assert handshake is not None
    assert titles[handshake.group(1)].startswith("The adapter sends the request again")
