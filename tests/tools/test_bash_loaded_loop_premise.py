"""#522's verdicts, driven through the real case's procedure with controlled results.

The containment case still runs real children under the original busy loop.
These rows force the reported and silent outcomes that a fast local host may
never produce naturally, so retrying a genuine regression cannot turn CI green.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tools.bash import ExecExitResult

from tests.tools import test_bash_tool_containment as containment

_FULL = (b"x" * 1023 + b"\n") * 2048


def _results(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[tuple[ExecExitResult, bytes]],
) -> list[float | None]:
    calls: list[float | None] = []

    def controlled(
        command: str,
        cwd: Path,
        chunks: list[bytes],
        **kwargs: Any,
    ) -> asyncio.Task[ExecExitResult]:
        index = len(calls)
        calls.append(kwargs.get("timeout"))
        assert index < len(outcomes), "the case retried an outcome it must fail immediately"
        result, payload = outcomes[index]

        async def deliver() -> ExecExitResult:
            await asyncio.sleep(0)
            chunks.append(payload)
            return result

        return asyncio.create_task(deliver())

    monkeypatch.setattr(containment, "_exec_task", controlled)
    return calls


@pytest.mark.parametrize(
    ("reported", "payload"),
    [
        (ExecExitResult(0, output_unconfirmed=True), _FULL[:-1024]),
        (ExecExitResult(0, output_unconfirmed=True), _FULL),
        (ExecExitResult(None, timed_out=True), _FULL[:-1024]),
        (ExecExitResult(None, timed_out=True, output_unconfirmed=True), _FULL[:-1024]),
    ],
    ids=["reported-tail-loss", "not-proven-but-complete", "timeout", "both-flags"],
)
async def test_reported_delivery_retries_and_requires_a_confirmed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reported: ExecExitResult,
    payload: bytes,
) -> None:
    calls = _results(monkeypatch, [(reported, payload), (ExecExitResult(0), _FULL)])

    with pytest.warns(UserWarning) as warnings:
        await containment.test_every_byte_is_delivered_under_a_loaded_loop(tmp_path, [])

    assert calls == [containment.DEADLINE * 4] * 2
    messages = [str(w.message) for w in warnings]
    assert sum("THE PREMISE FAILED, NOT THE CASE" in m for m in messages) == 1
    assert any("attempt 2" in m and "output_unconfirmed=False" in m for m in messages)


@pytest.mark.parametrize(
    ("result", "payload"),
    [
        (ExecExitResult(0), _FULL[:-1024]),
        (ExecExitResult(0), b"y" + _FULL[1:]),
        (ExecExitResult(7), _FULL),
    ],
    ids=["silent-tail-loss", "same-size-corruption", "bad-exit"],
)
@pytest.mark.parametrize("reported_first", [False, True], ids=["first", "after-a-reported-run"])
async def test_a_confirmed_regression_fails_without_retrying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: ExecExitResult,
    payload: bytes,
    reported_first: bool,
) -> None:
    outcomes = [(result, payload), (ExecExitResult(0), _FULL)]
    if reported_first:
        outcomes.insert(0, (ExecExitResult(0, output_unconfirmed=True), _FULL))
    calls = _results(monkeypatch, outcomes)

    with pytest.warns(UserWarning), pytest.raises(AssertionError):
        await containment.test_every_byte_is_delivered_under_a_loaded_loop(tmp_path, [])

    assert len(calls) == (2 if reported_first else 1)


async def test_every_reported_attempt_fails_instead_of_passing_or_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _results(
        monkeypatch,
        [
            (ExecExitResult(0, output_unconfirmed=True), _FULL),
            (ExecExitResult(None, timed_out=True), _FULL[:-1024]),
            (ExecExitResult(0, output_unconfirmed=True), _FULL[:-2048]),
        ],
    )

    with pytest.warns(UserWarning), pytest.raises(pytest.fail.Exception) as failed:
        await containment.test_every_byte_is_delivered_under_a_loaded_loop(tmp_path, [])

    assert len(calls) == containment.ATTEMPTS == 3
    message = str(failed.value)
    assert "never measured confirmed delivery" in message
    for attempt in range(1, 4):
        assert f"attempt {attempt}: THE PREMISE FAILED" in message
