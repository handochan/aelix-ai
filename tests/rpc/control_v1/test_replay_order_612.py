"""#612 fix round 1 (Codex C-L10): each ``children/events`` case that answers events pins its ordered seq list.

Codex mutated real fixture answers in memory: the ordinary cursor case took a
reversed slice ``[5, 4, 3, 2]`` and the stale cursor case took ``[46, 301]`` in
place of all 256 kept frames. Each case now carries ``prefixItems`` with every
seq in order, and ``minItems``/``maxItems`` with the count. The real answer still
passes; every reordered, dropped or extra frame is refused. On ``111e02d7`` the
two mutations Codex measured, and the others here, were accepted.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")

FIXTURE = next(f for f in rigs.load_fixtures() if f.get("method") == "children/events")
ANSWERING = [case for case in FIXTURE["cases"] if "data" in case and "allOf" in case["data"]["properties"]["events"]]


def _mutations(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "reversed": list(reversed(events)),
        "first and last only": [events[0], events[-1]],
        "one dropped": events[:1] + events[2:],
        "one repeated": [*events, events[-1]],
        "two swapped": [events[1], events[0], *events[2:]],
    }


def _pinned_order(case: dict[str, Any]) -> tuple[list[int], int | None, int | None]:
    """The seq list a case's ``prefixItems`` pins, in order, and its events' ``minItems``/``maxItems``."""

    events = case["data"]["properties"]["events"]
    prefixes = [part["prefixItems"] for part in events["allOf"] if "prefixItems" in part]
    seqs = [item["properties"]["data"]["properties"]["seq"]["const"] for prefix in prefixes for item in prefix]
    return seqs, events.get("minItems"), events.get("maxItems")


def test_every_case_that_answers_events_pins_its_order() -> None:
    """Fix round 2, N3: each answering case pins its ordered seq list and its count, not only its name.

    On ``111e02d7``'s fixture the cases carried ``contains`` for some seqs and no
    ``prefixItems``, no ``minItems`` and no ``maxItems``: every list below read ``[]``.
    """

    assert {case["name"]: _pinned_order(case) for case in ANSWERING} == {
        "replays a child's kept events from the oldest": ([1, 2, 3, 4, 5], 5, 5),
        "afterSeq skips what the host has": ([2, 3, 4, 5], 4, 4),
        "an afterSeq older than the ring answers gap": (list(range(46, 302)), 256, 256),
        "a turn does not refuse children/events": ([1, 2, 3, 4, 5], 5, 5),
    }


@pytest.mark.parametrize("case", ANSWERING, ids=[case["name"] for case in ANSWERING])
async def test_the_real_answer_passes_and_a_reordered_slice_is_refused(case: dict[str, Any], tmp_path: Path) -> None:
    result, cleanup = await rigs.run_in_process(FIXTURE, case, tmp_path)
    try:
        answer = result.response["data"]
        assert not rigs.schema_rejects(case["data"], answer), "the real answer is refused"
        for name, events in _mutations(answer["events"]).items():
            wrong = copy.deepcopy(answer)
            wrong["events"] = events
            assert rigs.schema_rejects(case["data"], wrong), f"{name}: {[e['data']['seq'] for e in events][:8]} accepted"
    finally:
        cleanup()
