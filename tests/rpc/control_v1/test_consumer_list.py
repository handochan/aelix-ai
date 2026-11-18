"""``docs/contracts/control-v1.md`` and the fixtures' ``consumers`` say the same thing (#623, ADR-0302 D5).

* §3.1: one row per consumed method; its direct reads and whether it is
  forwarded whole equal the aelix-acp ``consumers`` rows of that method's
  fixture. A method aelix-acp does not send is listed in §6 instead.
* §4: the direct reads per event frame type, and whether it is forwarded whole,
  equal the event fixtures' aelix-acp rows.
* §6: exactly the methods whose fixture has no consumer.
* Every path a consumer reads directly is a path the floor requires of a
  success case (``data`` or ``frame``); every path forwarded whole is in the
  floor's ``forwarded`` set, which §3.2 and §4.1 list exactly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tests.rpc.control_v1 import rigs

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs" / "contracts" / "control-v1.md"
FLOOR = json.loads(rigs.FLOOR.read_text(encoding="utf-8"))
FIXTURES = rigs.load_fixtures()
ACP = "handochan/aelix-acp"
PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\])?)*")
NONE = {"", "—", "-"}


def _section(title: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(title)
    following = re.search(r"\n#{2,3} ", text[start + len(title) :])
    return text[start : start + len(title) + (following.start() if following else len(text))]


def _rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        rows.append(cells)
    return rows[1:]


def _ticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def _reads(cell: str) -> set[str]:
    """The paths a "Direct reads" cell names: per ``;`` item, the ticked paths before its ``(``."""

    if cell in NONE:
        return set()
    paths = set()
    for item in re.split(r";\s*(?![^(]*\))", cell):
        head = item.split("(", 1)[0]
        paths.update(p for p in _ticked(head) if PATH.fullmatch(p))
    return paths


def _acp(fixture: dict[str, Any]) -> tuple[set[str], bool]:
    rows = [c for c in fixture["consumers"] if c["repo"] == ACP]
    reads = {path for c in rows if c["how"] == "direct" for path in c["reads"]}
    return reads, any(c["how"] == "forwarded" for c in rows)


def _method(name: str) -> dict[str, Any]:
    return next(f for f in FIXTURES if f.get("method") == name)


def _doc_methods() -> dict[str, tuple[set[str], bool, bool]]:
    """method -> (direct reads, forwarded whole, sent) from §3.1."""

    out = {}
    for cells in _rows(_section("### 3.1")):
        for method in _ticked(cells[0]):
            out[method] = (_reads(cells[2]), cells[3] not in NONE, cells[1] != "not sent")
    return out


def test_every_consumed_method_has_one_row_that_matches_its_fixture() -> None:
    doc = _doc_methods()
    consumed = {f["method"] for f in FIXTURES if "method" in f and _acp(f) != (set(), False)}
    sent = {m for m, (_r, _f, is_sent) in doc.items() if is_sent}
    sending = {
        f["method"]
        for f in FIXTURES
        if "method" in f and any(c["repo"] == ACP for c in f["consumers"])
    }
    assert sent == sending, f"§3.1 rows vs fixtures with an aelix-acp consumer: {sent ^ sending}"
    assert consumed <= sent
    for method in sorted(sent):
        reads, forwarded, _ = doc[method]
        assert (reads, forwarded) == _acp(_method(method)), (
            f"{method}: §3.1 says reads {sorted(reads)}, forwarded {forwarded}; the fixture "
            f"{sorted(_acp(_method(method))[0])}, {_acp(_method(method))[1]}"
        )


def test_served_not_consumed_is_exactly_the_fixtures_with_no_consumer() -> None:
    listed = {m for cells in _rows(_section("## 6.")) for m in _ticked(cells[0]) if "/" in m}
    unread = {f["method"] for f in FIXTURES if "method" in f and not f["consumers"]}
    assert listed == unread, listed ^ unread
    not_sent = {m for m, (_r, _f, is_sent) in _doc_methods().items() if not is_sent}
    assert not_sent <= listed, "a §3.1 row says 'not sent' but §6 does not list it"


def test_every_event_row_matches_its_fixture() -> None:
    doc: dict[str, tuple[set[str], bool]] = {}
    for cells in _rows(_section("## 4.")):
        frame = _ticked(cells[0])[0]
        reads, forwarded = doc.get(frame, (set(), False))
        doc[frame] = (reads | _reads(cells[2]), forwarded or cells[3] not in NONE)
    for fixture in (f for f in FIXTURES if "event" in f):
        assert doc.get(fixture["frameType"]) == _acp(fixture), (
            f"{fixture['frameType']}: §4 says {doc.get(fixture['frameType'])}, the fixture {_acp(fixture)}"
        )


def test_every_direct_read_is_a_floor_path() -> None:
    missing = []
    for fixture in FIXTURES:
        section, ident = _floor_key(fixture)
        entry = FLOOR[section][ident]
        required = entry.get("response") or entry.get("frame") or entry.get("data", {})
        for consumer in fixture["consumers"]:
            for path in consumer["reads"]:
                if section == "errors":
                    ok = any(
                        case["kind"] in ("error", "launch") for case in entry["cases"].values()
                    )
                else:
                    ok = path in required
                if not ok:
                    missing.append(f"{fixture['_file']}: {path}")
    assert missing == [], "read but not required by any success case:\n" + "\n".join(missing)


def _floor_key(fixture: dict[str, Any]) -> tuple[str, str]:
    if "method" in fixture:
        return "methods", fixture["method"]
    if "event" in fixture:
        return "events", fixture["event"]
    if "error" in fixture:
        return "errors", fixture["error"]
    return "envelope", "aelix_control"


def _listed(title: str) -> dict[str, set[str]]:
    return {_ticked(cells[0])[0]: set(_ticked(cells[1])) for cells in _rows(_section(title))}


def test_forwarded_method_paths_are_the_floors() -> None:
    listed = _listed("### 3.2")
    forwarded = {f["method"] for f in FIXTURES if "method" in f and _acp(f)[1]}
    assert set(listed) == forwarded, set(listed) ^ forwarded
    for method in sorted(forwarded):
        floor = set(FLOOR["methods"][method]["forwarded"])
        assert listed[method] == floor, (
            f"{method}: §3.2 lacks {sorted(floor - listed[method])}, has extra {sorted(listed[method] - floor)}"
        )


def test_forwarded_frame_paths_are_the_floors() -> None:
    listed = _listed("### 4.1")
    for fixture in (f for f in FIXTURES if "event" in f and _acp(f)[1]):
        floor = set(FLOOR["events"][fixture["event"]]["forwarded"])
        assert listed.get(fixture["frameType"]) == floor, fixture["frameType"]
