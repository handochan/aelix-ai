"""The CLI turns the kernel's notice inbox on only while background jobs are active (#722, ADR-0293 §12).

``cli/entry.py`` builds every harness with ``notice_inbox=jobs_ext is not None``:
the job service's bridge exists only while ``jobs/activation.py`` ``ACTIVE`` is
set, so with the gate off every harness the CLI builds has no inbox, as before
#722, and with it on every one has. Drives the real ``_async_main`` for ``-p``
and for RPC through a ``new_session`` rebuild, and records the option of each
``AgentHarness`` it builds. An inbox turned on with the gate off (verify R14,
``notice_inbox=True``) shows here and nowhere else: the kernel's own default is
``tests/harness/test_notice_inbox_722.py::test_with_the_inbox_off_the_kernel_is_what_it_was``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.harness import core as harness_core
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs import activation

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env and wire are fixtures
    _drive_real_rpc,
    _write_models,
    env,
    wire,
)


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """``notice_inbox`` of every harness built, in order."""

    seen: list[bool] = []
    real = harness_core.AgentHarness.__init__

    def init(self: harness_core.AgentHarness, options: harness_core.AgentHarnessOptions, *a: object, **k: object) -> None:
        seen.append(options.notice_inbox)
        real(self, options, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(harness_core.AgentHarness, "__init__", init)
    return seen


@pytest.fixture(params=[False, True], ids=["gate-off", "gate-on"])
def gate(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setattr(activation, "ACTIVE", request.param)
    request.addfinalizer(lambda: activation.install_service(None))
    return bool(request.param)


async def test_print_mode_builds_its_harness_with_the_inbox_only_while_jobs_are_active(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    gate: bool,
    built: list[bool],
) -> None:
    _write_models(env, "rec")
    code = await entry_mod._async_main(["--model", "rec/m1", "-p", "hello"])
    assert code == 0
    assert len(wire) == 1, "the prompt reached the model"
    assert built == [gate]


async def test_rpc_and_its_new_session_rebuild_get_the_inbox_only_while_jobs_are_active(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    gate: bool,
    built: list[bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_models(env, "rec")
    responses = _drive_real_rpc(
        monkeypatch,
        [
            {"type": "prompt", "id": "p1", "message": "hi"},
            {"type": "new_session", "id": "n1"},
            {"type": "prompt", "id": "p2", "message": "again"},
        ],
    )
    code = await entry_mod._async_main(["--model", "rec/m1", "--mode", "rpc"])
    assert code == 0
    assert [r["success"] for r in responses] == [True, True, True], responses
    assert len(wire) == 2, "both prompts reached the model"
    assert built == [gate, gate], "the launch's harness and the one new_session rebuilt"
