"""``test:sleeper`` — the synthetic third kind that proves the job service is generic (#722).

It imports nothing from delegation or ``bash``: a runner that spawns a Python
child that sleeps, registers it, reports progress and returns its output. The
gate tests run it through every surface; the AST scan proves the core never
names it. Knobs on :class:`Sleeper` let a test break any adapter or runner
operation (ADR-0293's failure table).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Mapping
from typing import Any

from aelix_coding_agent.jobs.contract import (
    ClientOutcome,
    HelperCleanup,
    JobSnapshot,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    Recovered,
    RelationSpec,
)

KIND = "test:sleeper"
SETTINGS_KEY = "sleeper"

_SCRIPT = (
    "import sys, time\ntime.sleep(float(sys.argv[1]))\nprint('slept', sys.argv[1], flush=True)\n"
)


def _spawn_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": 0x0000_0200}
    return {"start_new_session": True}


class SleeperAdapter:
    name = KIND
    label = "sleeper"
    settings_key = SETTINGS_KEY

    def __init__(
        self,
        *,
        version: int = 1,
        notify: bool = True,
        bound: int = 4,
        fail: frozenset[str] = frozenset(),
        recovered: Recovered | None = None,
        tag: str = "",
    ) -> None:
        self.version = version
        self.capabilities = KindCapabilities(default_bound=bound, notify=notify, control_hint=None)
        self.fail = fail
        self.recovered = recovered
        self.tag = tag
        self.calls: list[str] = []

    def _maybe(self, op: str) -> None:
        self.calls.append(op)
        if op in self.fail:
            raise RuntimeError(f"{op} broke")

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        """The joined input: ``{"fact": <the outcome fact>, "records": <this job's own records>}``."""

        self._maybe("decode")
        fact = client.get("fact", {})
        output = next(
            (
                str(r.get("output", ""))
                for r in client.get("records", [])
                if r.get("custom_type") == "test.sleeper" and "output" in r
            ),
            "",
        )
        return ClientOutcome(
            status=fact.get("status", "done"),
            brief=str(fact.get("brief", "")),
            payload={"output": output},
        )

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody:
        self._maybe("render_notice")
        output = str(outcome.payload.get("output", "")).strip()
        return NoticeBody(
            f"{self.tag}sleeper said: {output or outcome.brief}", next_action="Nothing to do."
        )

    async def finalize_at_placement(self, outcome: ClientOutcome, branch: list[Any]) -> str | None:
        self._maybe("finalize_at_placement")
        return f"(placed on a branch of {len(branch)} entries)"

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        self._maybe("render_rows")
        return [f"zz {snapshot.title} ({snapshot.state})"[:width]]

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        self._maybe("output")
        text = str(job.payload.get("output", ""))
        return OutputSlice(
            text[offset : offset + limit], offset, len(text), offset + limit < len(text)
        )

    async def recover(
        self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]
    ) -> Recovered | None:
        self._maybe("recover")
        for record in client_records:
            if record.get("custom_type") == "test.sleeper" and record.get("status") == "done":
                return Recovered(
                    "done", "recovered from the client's record", {"output": "from record"}
                )
        return self.recovered

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec:
        self._maybe("relation")
        return RelationSpec()


class Sleeper:
    """The runner. ``params``: ``seconds``, ``fail`` (ops to break), ``launch`` (``fail`` to refuse)."""

    instances: list[Sleeper] = []

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.seconds = float(params.get("seconds", 0.1))
        self.fail = frozenset(params.get("fail", ()))
        self.launch = params.get("launch", "ok")
        self.hang_settle = bool(params.get("hang_settle", False))
        self.ignore_stop = bool(params.get("ignore_stop", False))
        self.records_fail = bool(params.get("records_fail", False))
        self.stopped = asyncio.Event()
        self.proc: asyncio.subprocess.Process | None = None
        self.ctx: Any = None
        self.calls: list[str] = []
        Sleeper.instances.append(self)

    def _maybe(self, op: str) -> None:
        self.calls.append(op)
        if op in self.fail:
            raise RuntimeError(f"{op} broke")

    async def run(self, ctx: Any) -> ClientOutcome:
        self.ctx = ctx
        self._maybe("run")
        if self.launch == "fail":
            ctx.launch_failed("no such program: sleeper")
            return ClientOutcome("failed", "launch failed")
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _SCRIPT,
            str(self.seconds),
            stdout=asyncio.subprocess.PIPE,
            **_spawn_kwargs(),
        )
        ctx.register_process(self.proc, pgid=self.proc.pid if sys.platform != "win32" else None)
        ctx.launched()
        ctx.progress({"phase": "sleeping"})
        reading = asyncio.ensure_future(self.proc.communicate())
        self._pending_read = reading
        stopping = asyncio.ensure_future(self.stopped.wait())
        try:
            done, _ = await asyncio.wait({reading, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopping.cancel()
        if reading in done:
            out, _ = reading.result()
            text = (out or b"").decode()
            ctx.progress({"phase": "done"})
            return ClientOutcome("done", f"slept {self.seconds}s", {"output": text})
        # The service's kill legs end the child; ``settle`` waits for its pipe.
        self._reading = reading
        return ClientOutcome("stopped", "stopped before it woke", {"output": ""})

    def request_stop(self, reason: str) -> None:
        self._maybe("request_stop")
        if not self.ignore_stop:
            self.stopped.set()

    async def settle(self) -> None:
        self._maybe("settle")
        if self.hang_settle:
            await asyncio.Event().wait()
        reading = getattr(self, "_reading", None)
        if reading is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reading

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        self._maybe("helper_cleanup")
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        self._maybe("records")
        return [
            (
                "test.sleeper",
                {
                    "seconds": self.seconds,
                    "status": outcome.status,
                    "output": str(outcome.payload.get("output", "")),
                },
            )
        ]


def register(service: Any, *, source: str = "", **adapter_kwargs: Any) -> SleeperAdapter:
    adapter = SleeperAdapter(**adapter_kwargs)
    service.register_kind(adapter, Sleeper, source=source)
    return adapter


async def reap_all() -> None:
    """Test hygiene: end every sleeper's child and finish reading its pipe on the test's own loop.

    A runner a test left mid-barrier (a hanging ``settle``) still holds its
    child's stdout transport; collected after the loop closed, it raises from
    ``__del__`` into whichever test runs next (seen on Linux CI-like runs).
    """

    for runner in list(Sleeper.instances):
        proc = runner.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
        if proc is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)
        reading = getattr(runner, "_reading", None) or getattr(runner, "_pending_read", None)
        if reading is not None and not reading.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(reading, 5)
    Sleeper.instances.clear()
