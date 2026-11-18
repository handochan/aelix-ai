"""Every control-v1 fixture case, in process, through ``run_rpc_mode`` over a JSONL wire (#623, ADR-0302 D8).

Each case gets a fresh rig (``rigs.py``) under its own ``tmp_path``. The envelope
of ``fixtures/envelope.json`` is asserted on every answer, then the case's own
``data`` or ``error`` schema, every frame an event case names, and the case's
``check`` when it has one. A ``source`` case reads the files it names: the two
producers of the locked-session text (ADR-0302 D2.5). A ``launch`` case runs
only on the real child (``test_real_child.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")

REPO = Path(__file__).resolve().parents[3]
CASES = rigs.in_process_cases()
SOURCES = [
    (fixture, case)
    for fixture in rigs.load_fixtures()
    for case in fixture["cases"]
    if "source" in case
]


def _id(pair: tuple[dict[str, Any], dict[str, Any]]) -> str:
    fixture, case = pair
    return f"{rigs.fixture_id(fixture)}::{case['name']}"


@pytest.mark.parametrize("pair", CASES, ids=[_id(p) for p in CASES])
async def test_case(pair: tuple[dict[str, Any], dict[str, Any]], tmp_path: Path) -> None:
    fixture, case = pair
    result, _check = await rigs.run_in_process(fixture, case, tmp_path / "rig")
    rigs.assert_case(fixture, case, result)


@pytest.mark.parametrize("pair", SOURCES, ids=[_id(p) for p in SOURCES])
def test_source(pair: tuple[dict[str, Any], dict[str, Any]]) -> None:
    _fixture, case = pair
    for source in case["source"]:
        text = (REPO / source["path"]).read_text(encoding="utf-8")
        assert source["contains"] in text, f"{source['path']} no longer says {source['contains']!r}"


def test_every_case_runs_somewhere() -> None:
    """In process, on the real child, or as a source check: no case is silently skipped."""

    for fixture in rigs.load_fixtures():
        for case in fixture["cases"]:
            ran = (
                (fixture, case) in CASES
                or "source" in case
                or ("launch" in case and case.get("replay") is True)
            )
            assert ran, f"{fixture['_file']} {case['name']!r} runs nowhere"
