"""#522's verdicts and #695's per-attempt timeouts, driven through the real case's procedure.

The containment case still runs real children under the original busy loop.
These rows force the reported and silent outcomes that a fast local host may
never produce naturally, so retrying a genuine regression cannot turn CI green.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tools.bash import ExecExitResult

from tests.tools import test_bash_tool_containment as containment

_FULL = (b"x" * 1023 + b"\n") * 2048
_ROOT = Path(__file__).resolve().parents[2]


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


def _bounds(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the outer bound the case hands ``_bounded`` on each attempt (#695).

    The command ``timeout`` is only half of an attempt's budget. ``_bounded``
    fails the case by name when ``exec`` has not returned within its bound,
    so a bound left at the first attempt's 25 s would end the second attempt
    by name at 25 s, before its 40 s timeout.
    """

    bounds: list[float] = []
    real = containment._bounded  # pyright: ignore[reportPrivateUsage]

    async def recorded(
        task: asyncio.Task[ExecExitResult], bound: float, what: str
    ) -> ExecExitResult:
        bounds.append(bound)
        return await real(task, bound, what)

    monkeypatch.setattr(containment, "_bounded", recorded)
    return bounds


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
    bounds = _bounds(monkeypatch)

    with pytest.warns(UserWarning) as warnings:
        await containment.test_every_byte_is_delivered_under_a_loaded_loop(tmp_path, [])

    assert calls == [20.0, 40.0]
    assert bounds == [25.0, 45.0]
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
    bounds = _bounds(monkeypatch)

    with pytest.warns(UserWarning), pytest.raises(pytest.fail.Exception) as failed:
        await containment.test_every_byte_is_delivered_under_a_loaded_loop(tmp_path, [])

    assert len(calls) == containment.ATTEMPTS == 3
    assert calls == [20.0, 40.0, 80.0]
    assert bounds == [25.0, 45.0, 85.0]
    message = str(failed.value)
    assert "never measured confirmed delivery" in message
    for attempt in range(1, 4):
        assert f"attempt {attempt}: THE PREMISE FAILED" in message
    assert "s of 80s, 1 chunks" in message


async def test_three_timeouts_still_end_inside_the_hang_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#695: the doubled timeouts must not turn a failure by name into a hang dump.

    Three command timeouts run through the real case, which records each
    attempt's command ``timeout`` and the outer bound it hands ``_bounded``
    (kill and drain included). The three bounds the case used, not a sum
    written here, have to stay under the per-test ``faulthandler_timeout``
    in ``pyproject.toml`` with 60 s to spare. The spare covers the reference
    run's own 20 s cap and the 5 s ``_bounded`` waits after a bound expires.
    Otherwise three timeouts would end as an anonymous 240 s dump instead of
    failing by name.
    """

    timed_out = (ExecExitResult(None, timed_out=True), _FULL[:-1024])
    calls = _results(monkeypatch, [timed_out] * containment.ATTEMPTS)
    bounds = _bounds(monkeypatch)

    with pytest.warns(UserWarning), pytest.raises(pytest.fail.Exception) as failed:
        await containment.test_every_byte_is_delivered_under_a_loaded_loop(tmp_path, [])

    assert "never measured confirmed delivery" in str(failed.value)
    assert containment.LOADED_LOOP_BUDGETS == (20.0, 40.0, 80.0)
    assert calls == [20.0, 40.0, 80.0]
    assert bounds == [25.0, 45.0, 85.0]
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    guard = float(config["tool"]["pytest"]["ini_options"]["faulthandler_timeout"])
    assert sum(bounds) + 60.0 <= guard
