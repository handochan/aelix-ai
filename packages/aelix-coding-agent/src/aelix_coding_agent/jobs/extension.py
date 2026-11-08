"""The built-in extension that connects the job service to each harness build (#722, ADR-0293).

Built once by the CLI, only while background jobs are active, and threaded into
every build by reference (as the permission and delegation extensions are). It
registers the ``job`` and ``job_stop`` tools and the ``/jobs`` command, and turns a
``session_shutdown`` into the service's stop of that session's jobs — every
reason stops them: ``/new``, ``/resume``, ``/fork`` and quitting end the owner,
and ``/reload`` stops them too (owner decision 4, 2026-11-07). The stop is
bounded by the service's ``CLEANUP_BOUND``; what it cannot confirm in time is
left ``unresolved`` and finishes in the background.

It decides nothing about a tool call and subscribes to nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.jobs.tool import create_job_stop_tool, create_job_tool

if TYPE_CHECKING:
    from aelix_coding_agent.extensions.api import ExtensionAPI
    from aelix_coding_agent.jobs.service import JobService


class JobsExtension:
    name = "aelix-jobs"

    def __init__(self, service: JobService) -> None:
        self._service = service
        self.command = JobsCommand(service)

    def __call__(self, aelix: ExtensionAPI) -> None:
        aelix.register_tool(create_job_tool(self._service))
        aelix.register_tool(create_job_stop_tool(self._service))
        aelix.register_command(
            "jobs",
            handler=self.command,
            description="Background jobs: list, <id>, result <id>, stop <id>, send",
        )
        aelix.on("session_shutdown", self._on_shutdown)

    async def _on_shutdown(self, event: Any, _ctx: Any) -> None:
        reason = getattr(event, "reason", "quit")
        await self._service.owner_ended(
            self._service.owner, "reload" if reason == "reload" else "owner_end"
        )


__all__ = ["JobsExtension"]
