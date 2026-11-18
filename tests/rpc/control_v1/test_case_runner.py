"""The case runner's ``during`` command: what a check sees is the command's own answer (#623, Codex CX-9).

A case with a ``during`` command starts it, sends its own request, and reads the
answer. When the case's request is meant to end the command (``"ended_by_case":
true``: a ``commands/cancel``, or a reload the case's answer releases), the
runner waits for the command's OWN answer before anything else; only then, and
in a ``finally`` whatever happened, does it end a command still running with a
cancel by ``requestId`` of its own. That cleanup's answer never reaches
``CaseResult.during``, so a cancel that answers ``cancelled: true`` without
ending the command fails its case instead of being rescued by the cleanup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")


def _fixture_case(fixture_id: str, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = next(f for f in rigs.load_fixtures() if rigs.fixture_id(f) == fixture_id)
    return fixture, next(case for case in fixture["cases"] if case["name"] == name)


@pytest.mark.parametrize(
    ("fixture_id", "name"),
    [
        ("commands/cancel", "cancels the running command"),
        ("envelope", "a cancelled operation answers Command cancelled"),
    ],
)
async def test_a_cancel_that_answers_without_ending_the_command_fails_its_case(
    fixture_id: str, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-id cancel that only answers ``cancelled: true`` (the command runs on)."""

    from aelix_coding_agent.rpc.control import RpcControlPlane

    real = RpcControlPlane.cancel

    def answers_only(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("requestId") is None:
            running = next((op for op in self.operations if op.active), None)
            if running is not None:
                return {"cancelled": True, "method": running.method}
        return real(self, params)

    monkeypatch.setattr(RpcControlPlane, "cancel", answers_only)
    monkeypatch.setattr(rigs, "WAIT_S", 5.0)
    fixture, case = _fixture_case(fixture_id, name)
    with pytest.raises(AssertionError):
        result, _check = await rigs.run_in_process(fixture, case, tmp_path / "rig")
        rigs.assert_case(fixture, case, result)


async def test_the_cleanup_cancel_never_answers_for_the_case(tmp_path: Path) -> None:
    """The sensitive re-send cancels nothing; the runner ends the command itself, unseen."""

    fixture, case = _fixture_case("commands/cancel", "cancels by requestId")
    async with rigs.open_rig(case["rig"], tmp_path / "rig") as rig:
        result = await rigs.run_case(
            fixture, case, rig.wire, rig.values, override={"requestId": "not-running"}
        )
        # Ended by the runner before it returned, not by the rig's shutdown.
        ended = [r for r in rig.wire.records if r.get("id") == "during" and "success" in r]
        assert ended and ended[0]["error"] == "Command cancelled", rig.wire.records[-3:]
    assert result.response["data"] == {"cancelled": False}, result.response
    assert result.during is None, result.during


async def test_the_case_ends_its_own_command(tmp_path: Path) -> None:
    fixture, case = _fixture_case("commands/cancel", "cancels the running command")
    result, check = await rigs.run_in_process(fixture, case, tmp_path / "rig")
    rigs.assert_case(fixture, case, result)
    check()
    assert result.during is not None and result.during["error"] == "Command cancelled"


def test_every_case_a_during_check_reads_waits_for_the_commands_own_answer() -> None:
    reads_during = {"cancelled_the_during_command"}
    for fixture in rigs.load_fixtures():
        for case in fixture["cases"]:
            if case.get("check") in reads_during:
                assert case["during"].get("ended_by_case") is True, (
                    f"{fixture['_file']} {case['name']!r}: its check reads the during command's "
                    "answer, so the case must end it (ended_by_case)"
                )
