"""#612 fix round 2 (F1) — a job an extension starts from ``session_start`` runs for the session that is starting.

The CLI bound its job service to a rebuilt harness only after the rebuild's
``session_start`` handlers had run (``_after_session_start``). Fix round 1's
entry check (Codex C-H1) refuses a start for the bound session once its owner
ended, so a ``session_start`` handler's ``aelix.jobs.start`` on ``resources/reload``
— which the base ran under the session's own owner — and on a ``new_session``,
``switch_session`` or ``fork`` — which the base gave to the ENDED session's owner
— was refused with "the session ended before the job started" on ``510b186c``
(the verify's ``probes/start_in_session_start*.py``). The CLI now binds the
service before those handlers (``AgentSessionRuntime.set_before_session_start``),
so the start runs under the new session's owner on every path; a start from the
outgoing session's ``session_shutdown`` after the jobs extension's own is still
refused (Codex C-H1 stays fixed).

Drives the real ``_async_main`` in ``--mode rpc`` with an ``-e`` extension that
registers ``test:sleeper`` and records, for each ``session_start`` and
``session_shutdown`` it sees, what its ``jobs.start`` returned and whose job it
is. Each case of the first test fails on ``510b186c``: the outcome is the
``JobStartRefused`` sentence instead of a job of the new session.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env and wire are fixtures
    _write_models,
    env,
    wire,
)

_EXTENSION = textwrap.dedent(
    """
    import json
    from aelix_coding_agent.jobs import activation
    from tests.jobs import _sleeper

    OUT = {out!r}
    WHEN = {when!r}

    def _record(row):
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\\n")

    def setup(aelix):
        jobs = aelix.jobs
        jobs.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)

        async def _start(event, ctx, hook):
            reason = getattr(event, "reason", None)
            if reason == "startup":
                return
            session = ctx.session_manager.get_session()
            here = (await session.get_metadata()).id
            try:
                job_id = await jobs.start(_sleeper.KIND, title=reason, params={{"seconds": 30}})
            except Exception as exc:
                _record({{"hook": hook, "reason": reason, "session": here,
                          "refused": f"{{type(exc).__name__}}: {{exc}}"}})
                return
            snap = activation.current_service().snapshot(job_id, any_owner=True)
            _record({{"hook": hook, "reason": reason, "session": here, "owner": snap.owner,
                      "state": snap.state}})

        if "start" in WHEN:
            async def _on_start(event, ctx):
                await _start(event, ctx, "session_start")
            aelix.on("session_start", _on_start)
        if "shutdown" in WHEN:
            async def _on_shutdown(event, ctx):
                await _start(event, ctx, "session_shutdown")
            aelix.on("session_shutdown", _on_shutdown)
    """
)


def _drive(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    """Run the REAL ``run_rpc_mode`` on in-memory pipes, with ``client(send)`` as the host."""

    from aelix_coding_agent import modes

    real = modes.run_rpc_mode

    async def _wrapped(harness: Any, **kwargs: Any) -> None:
        reader = asyncio.StreamReader()
        lines: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def _write(data: bytes) -> None:
            for raw in data.decode("utf-8").splitlines():
                if raw.strip():
                    lines.put_nowait(json.loads(raw))

        async def send(cmd: dict[str, Any]) -> dict[str, Any]:
            reader.feed_data((json.dumps(cmd) + "\n").encode("utf-8"))
            while True:
                obj = await asyncio.wait_for(lines.get(), timeout=60)
                if obj.get("type") == "response" and obj.get("id") == cmd["id"]:
                    if cmd["type"] == "prompt" and obj.get("success"):
                        while (await asyncio.wait_for(lines.get(), timeout=60)).get("type") != "agent_end":
                            pass
                    return obj

        async def _client() -> None:
            try:
                await client(send)
            finally:
                reader.feed_eof()

        task = asyncio.create_task(_client())
        await real(harness, stdin=reader, stdout_write=_write, install_signal_handlers=False, **kwargs)
        await task

    monkeypatch.setattr(modes, "run_rpc_mode", _wrapped)


def _rows(out: Path) -> list[dict[str, Any]]:
    if not out.exists():
        return []
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _session_file(send: Any) -> str:
    stats = await send({"type": "get_session_stats", "id": "stats"})
    assert stats["success"] is True, stats
    return str(stats["data"]["sessionFile"])


@pytest.mark.parametrize("path", ["reload", "new_session", "switch_session", "fork"])
async def test_a_session_start_job_start_runs_for_the_session_that_is_starting(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Every replacement: the handler's start is not refused, and its job is the new session's.

    On ``510b186c`` each case recorded ``refused: "JobStartRefused: the session
    ended before the job started"``.
    """

    _write_models(env, "rec")
    out = env / "rows.jsonl"
    ext = env / "jobext.py"
    ext.write_text(_EXTENSION.format(out=str(out), when=("start",)), encoding="utf-8")
    replies: list[dict[str, Any]] = []

    async def client(send: Any) -> None:
        first = await _session_file(send)
        if path == "reload":
            replies.append(await send({"type": "aelix_control", "id": "r", "method": "resources/reload", "params": {}}))
        elif path == "new_session":
            replies.append(await send({"type": "new_session", "id": "n"}))
        elif path == "switch_session":
            replies.append(await send({"type": "new_session", "id": "n"}))
            replies.append(await send({"type": "switch_session", "id": "s", "sessionPath": first}))
        else:
            replies.append(await send({"type": "prompt", "id": "p", "message": "hi"}))
            points = await send({"type": "get_fork_messages", "id": "g"})
            entry = points["data"]["messages"][0]["entryId"]
            replies.append(await send({"type": "fork", "id": "f", "entryId": entry}))

    _drive(monkeypatch, client)
    code = await entry_mod._async_main(["-e", str(ext), "--model", "rec/m1", "--mode", "rpc"])
    assert code == 0
    assert all(reply.get("success") is not False for reply in replies), replies
    rows = [row for row in _rows(out) if row["hook"] == "session_start"]
    expected_reasons = {"reload": ["reload"], "new_session": ["new"], "switch_session": ["new", "resume"], "fork": ["fork"]}
    assert [row["reason"] for row in rows] == expected_reasons[path], rows
    for row in rows:
        assert "refused" not in row, row
        assert row["owner"] == row["session"], f"the job is not the starting session's: {row}"
        assert row["state"] == "running", row


async def test_a_session_shutdown_job_start_after_the_jobs_own_is_still_refused(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex C-H1 stays fixed through the CLI: the OUTGOING session's late start is refused, the new one's runs."""

    _write_models(env, "rec")
    out = env / "rows.jsonl"
    ext = env / "jobext.py"
    ext.write_text(_EXTENSION.format(out=str(out), when=("start", "shutdown")), encoding="utf-8")

    async def client(send: Any) -> None:
        reply = await send({"type": "new_session", "id": "n"})
        assert reply["success"] is True, reply

    _drive(monkeypatch, client)
    code = await entry_mod._async_main(["-e", str(ext), "--model", "rec/m1", "--mode", "rpc"])
    assert code == 0
    rows = _rows(out)
    shutdown = [row for row in rows if row["hook"] == "session_shutdown" and row["reason"] == "new"]
    start = [row for row in rows if row["hook"] == "session_start"]
    assert len(shutdown) == 1 and len(start) == 1, rows
    assert shutdown[0]["refused"] == "JobStartRefused: the session ended before the job started", rows
    assert start[0]["owner"] == start[0]["session"] != shutdown[0]["session"], rows
