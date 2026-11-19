"""What each host advertises, against the floor (#623, ADR-0302 D8, D10).

* A host given every service lists every method the floor names for ``cli-rpc``.
* ``aelix-server``'s WebSocket ``/rpc`` lists exactly the floor's ``server-ws``
  methods: it injects no service (ADR-0302 D10).
* Every method the control plane serves has a fixture, and every fixture's
  method is served: a new method cannot land without one.
* A fixture's ``requires`` is the service whose absence takes the method out of
  ``capabilities.methods``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.rpc.control import (
    CONCURRENT_METHODS,
    MUTATING_METHODS,
    READ_ONLY_METHODS,
    RpcControlPlane,
)

from tests.env_sandbox import sandbox_home
from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")

FLOOR = json.loads(rigs.FLOOR.read_text(encoding="utf-8"))


def _served_on(host: str) -> set[str]:
    return {m for m, entry in FLOOR["methods"].items() if host in entry["hosts"]} - {"capabilities"}


async def _plane(tmp_path: Path, drop: str | None = None) -> tuple[Any, RpcControlPlane]:
    from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime

    seed = rigs.seed_files(tmp_path / "seed")
    gate = asyncio.Event()
    gate.set()
    harness = await rigs.build_harness("full", seed, gate, None)
    runtime = _make_passthrough_runtime(harness, None)
    if drop != "subagents":
        rigs.bind_fake_subagents(harness)
    services = rigs.rpc_services("full", seed, harness)
    if drop in rigs.REQUIRES:
        services.pop(rigs.REQUIRES[drop])
    return harness, RpcControlPlane(runtime, **services)


async def test_a_host_with_every_service_lists_every_cli_rpc_method(tmp_path: Path) -> None:
    harness, plane = await _plane(tmp_path)
    try:
        listed = set((await plane.handle("capabilities", {}))["methods"])
    finally:
        await harness.dispose()
    assert listed == _served_on("cli-rpc"), listed ^ _served_on("cli-rpc")


@pytest.mark.parametrize(
    "requires", sorted({e["requires"] for e in FLOOR["methods"].values()} - {None})
)
async def test_requires_names_the_service_whose_absence_unlists_the_method(
    requires: str, tmp_path: Path
) -> None:
    assert requires in rigs.REQUIRES or requires == "subagents", (
        f"name {requires!r} in rigs.REQUIRES"
    )
    harness, plane = await _plane(tmp_path, drop=requires)
    try:
        listed = set((await plane.handle("capabilities", {}))["methods"])
    finally:
        await harness.dispose()
    needing = {m for m, e in FLOOR["methods"].items() if e["requires"] == requires}
    assert not listed & needing, f"listed without {requires}: {listed & needing}"
    assert _served_on("cli-rpc") - needing <= listed


def test_every_served_method_has_a_fixture_and_every_fixture_is_served() -> None:
    # #612 (contract r5 §9.4; Codex 14): the lock-free interventions are served too.
    served = (
        set(READ_ONLY_METHODS)
        | set(MUTATING_METHODS)
        | set(CONCURRENT_METHODS)
        | {"commands/cancel"}
    )
    assert served == set(FLOOR["methods"]), served ^ set(FLOOR["methods"])


def test_the_websocket_host_lists_exactly_the_server_ws_methods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_server import ServerConfig, create_app
    from fastapi.testclient import TestClient

    sandbox_home(monkeypatch, tmp_path / "home")
    config = ServerConfig(
        bind="127.0.0.1",
        port=0,
        cwd=str(tmp_path),
        model="",
        provider="",
        schemas_dir=(Path(__file__).resolve().parents[3] / "docs" / "contracts"),
    )
    with TestClient(create_app(config)) as client, client.websocket_connect("/rpc") as ws:
        ws.send_text(
            json.dumps({"type": "aelix_control", "id": "c", "method": "capabilities", "params": {}})
        )
        while True:
            frame = json.loads(ws.receive_text())
            if frame.get("id") == "c":
                break
    assert frame["success"] is True, frame
    assert set(frame["data"]["methods"]) == _served_on("server-ws")
