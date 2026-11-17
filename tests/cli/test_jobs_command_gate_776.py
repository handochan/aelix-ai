"""#776 guard — with the job gate off there is no ``/jobs`` and so no panel (ADR-0293 §12, ADR-0299).

``/jobs`` is registered only by the jobs bridge extension, which ``cli/entry.py``
builds only while ``jobs/activation.py`` ``ACTIVE`` is set; the panel is reached
only through ``/jobs``. Drives the real ``_async_main`` for ``-p`` with the gate
off and on and records every ``/jobs`` registration the run made. A guard: it
passes on ``7e2feb50`` too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.extension import JobsExtension

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env and wire are fixtures
    _write_models,
    env,
    wire,
)


@pytest.mark.parametrize("on", [False, True], ids=["gate-off", "gate-on"])
async def test_the_jobs_command_exists_only_with_the_gate_on(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    on: bool,
) -> None:
    monkeypatch.setattr(activation, "ACTIVE", on)
    monkeypatch.setattr(activation, "_switched_off", False)
    registered: list[str] = []
    real = JobsExtension.__call__

    def call(self: JobsExtension, aelix: Any) -> None:
        register = aelix.register_command

        def spy(name: str, **kwargs: Any) -> Any:
            registered.append(name)
            return register(name, **kwargs)

        aelix.register_command = spy
        try:
            real(self, aelix)
        finally:
            aelix.register_command = register

    monkeypatch.setattr(JobsExtension, "__call__", call)
    _write_models(env, "rec")
    try:
        assert await entry_mod._async_main(["--model", "rec/m1", "-p", "hello"]) == 0
    finally:
        activation.install_service(None)
    assert len(wire) == 1, "the prompt reached the model (positive control)"
    assert ("jobs" in registered) is on
