"""A job kind that swallows every cancel until it is released (#777 PR c1, L6; contract r2 §6, r4 errata E5).

Test-only, loaded with ``-e`` through the gate-on launcher, for the lead's pty run::

    python tests/jobs/gated_cli.py -e tests/jobs/fixtures/hold_kind_ext.py

``/hold [path]`` starts one job of kind :data:`KIND`. It owns no process. Its run
waits for a release and catches ``CancelledError`` in a loop until then, so a
stop never ends it: ``/reload`` stops the session's jobs, waits ``CLEANUP_BOUND``
(10 s) and leaves it ``unresolved(reload_timeout)`` — the footer's ``⚙ 1
unresolved`` and ``reload_timeout`` in ``/jobs``. The release here is a file,
``path`` (``hold-release`` in the cwd when none is given): once it exists the run
honours the cancel it swallowed, and the job ends ``stopped`` with its facts in
the session it was started in and its slot back.

``tests/jobs/test_hold_kind_777.py`` loads this file and releases the same
runner with an ``asyncio.Event`` instead (:func:`extension`). An ``asyncio.shield``
around a sleep cannot make this state (Codex r1 #10): the cancel lands in the
coroutine that awaits the shield, so the run ends at the first one. This
module imports nothing from ``tests``: an ``-e`` file is imported from its
path, with the repository not on ``sys.path``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
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

KIND = "test:hold"
RELEASE_FILE = "hold-release"
"""The release file ``/hold`` waits for when it is given no path, in the cwd."""

Release = Callable[[Mapping[str, Any]], Callable[[], Awaitable[None]]]
"""A job's params -> what its run awaits until it is released."""


def file_release(params: Mapping[str, Any]) -> Callable[[], Awaitable[None]]:
    """Released once ``params["release"]`` exists; polled every 0.1 s."""

    path = str(params["release"])

    async def wait() -> None:
        while not os.path.exists(path):
            await asyncio.sleep(0.1)

    return wait


class HoldAdapter:
    name = KIND
    label = "hold"
    settings_key = "hold"
    version = 1

    def __init__(self) -> None:
        self.capabilities = KindCapabilities(default_bound=4, notify=True, control_hint=None)

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        fact = client.get("fact", {})
        return ClientOutcome(status=fact.get("status", "done"), brief=str(fact.get("brief", "")))

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody:
        return NoticeBody(
            f"the hold ended {outcome.status}: {outcome.brief}", next_action="Nothing to do."
        )

    async def finalize_at_placement(self, outcome: ClientOutcome, branch: list[Any]) -> str | None:
        return None

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        return [f"hold {snapshot.title} ({snapshot.state})"[:width]]

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        return None

    async def recover(
        self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]
    ) -> Recovered | None:
        return None

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec:
        return RelationSpec()

    def restored_note(self, facts: Mapping[str, Any]) -> str | None:
        return None


class HoldRunner:
    """Waits for its release; every cancel before it is counted and swallowed, and honoured after it."""

    instances: list[HoldRunner] = []

    def __init__(self, release: Callable[[], Awaitable[None]]) -> None:
        self._release = release
        self.cancels = 0
        self.stops: list[str] = []
        self.released = False
        HoldRunner.instances.append(self)

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        while True:
            try:
                await self._release()
            except asyncio.CancelledError:
                self.cancels += 1  # swallowed: the kind this file is
                continue
            break
        self.released = True
        if self.cancels:
            raise asyncio.CancelledError  # the swallowed cancel, honoured once released
        return ClientOutcome("done", "released before any stop")

    def request_stop(self, reason: str) -> None:
        self.stops.append(reason)  # ignored: only the release ends it

    async def settle(self) -> None:
        return None

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return [("test.hold", {"status": outcome.status, "cancels": self.cancels})]


def extension(release: Release) -> Callable[[Any], None]:
    """The extension's ``setup``, its runs released by ``release``."""

    def setup(aelix: Any) -> None:
        jobs = aelix.jobs  # an AttributeError with the gate off: start aelix through the launcher
        jobs.register_kind(HoldAdapter(), lambda params: HoldRunner(release(params)))

        async def hold(args: str, _ctx: Any) -> str:
            path = args.strip() or os.path.join(os.getcwd(), RELEASE_FILE)
            job_id = await jobs.start(KIND, title="hold", params={"release": path})
            return f"started job {job_id}; it ends once {path} exists"

        aelix.register_command(
            "hold", handler=hold, description="Start a job that swallows every stop until released."
        )

    return setup


setup = extension(file_release)
