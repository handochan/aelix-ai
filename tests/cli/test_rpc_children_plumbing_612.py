"""#612 (contract r5 §7.4, §9.5): ``aelix --mode rpc`` hands the control plane its job service and permission extension.

``children/notices`` and the ``aelix_notice`` wake need the job service the
launch built; the approval back-channel (ADR-0308) binds on the process's own
``PermissionExtension``. Fails on ``567753c0``: ``run_rpc_mode`` is handed
neither (no ``jobs`` and no ``permissions`` keyword).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs.service import JobService

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 — the fixture
    _stub_rpc,
    _write_models,
    env,
)


async def test_rpc_is_handed_the_launchs_job_service_and_permission_extension(
    env: Path,  # noqa: F811 — the fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_models(env, "rec")
    handed = _stub_rpc(monkeypatch)
    assert await entry_mod._async_main(["--no-session", "--model", "rec/m1", "--mode", "rpc"]) == 0
    (kwargs,) = handed
    assert isinstance(kwargs["jobs"], JobService)
    assert isinstance(kwargs["permissions"], PermissionExtension)


async def test_with_background_jobs_off_rpc_is_handed_no_job_service(
    env: Path,  # noqa: F811 — the fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_models(env, "rec")
    (env / "agent" / "settings.json").write_text(json.dumps({"backgroundJobs": {"enabled": False}}), encoding="utf-8")
    handed = _stub_rpc(monkeypatch)
    assert await entry_mod._async_main(["--no-session", "--model", "rec/m1", "--mode", "rpc"]) == 0
    (kwargs,) = handed
    assert kwargs["jobs"] is None
    assert isinstance(kwargs["permissions"], PermissionExtension)


async def test_the_agents_extension_asks_the_launchs_permission_extension_who_answers(
    env: Path,  # noqa: F811 — the fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0308: ``asks_parent`` is "the one ``PermissionExtension`` has a ``child_approver``", and
    the arbiter is its ``decide_for_child``. An RPC launch binds no approver until the host opts in
    (``ui/enable {childApprovals: true}``), so its children's argv and clamp are the base's until then;
    ``-p`` and json never bind one."""

    from aelix_agents.extension import AgentsExtension

    _write_models(env, "rec")
    made: list[AgentsExtension] = []
    real_init = AgentsExtension.__init__

    def spy(self: AgentsExtension, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]
        made.append(self)

    monkeypatch.setattr(AgentsExtension, "__init__", spy)
    handed = _stub_rpc(monkeypatch)
    assert await entry_mod._async_main(["--no-session", "--model", "rec/m1", "--mode", "rpc", "--agents"]) == 0
    (ext,) = made
    (kwargs,) = handed
    permissions = kwargs["permissions"]
    assert ext.asks_parent is not None and ext.asks_parent() is False
    assert ext.decide_for_child == permissions.decide_for_child

    async def approver(_request: object) -> tuple[str, None]:
        return ("yes", None)

    permissions.child_approver = approver
    assert ext.asks_parent() is True, "read live: an RPC host's opt-in reaches the next spawn"
