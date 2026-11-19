"""Codex review of #797, H1: no RPC reply is lost to its UTF-8 encoding.

A ``str`` can hold a lone surrogate (``json.loads('"\\udcff"')``, an
``ImportError`` naming a path the file system gave as one), and UTF-8 cannot
encode it. The dispatch guard serialised the result (#594) but encoded it only
in the write after the guard, so the encode raised in the fire-and-forget task
and the id was never answered. Now ``aelix_control`` encodes inside its guard
(a correlated error naming the encode failure) and every reply is written by
``rpc_mode._reply_bytes``, which answers a reply it cannot encode with that
error, ASCII-escaped when the request's own id holds the character.

Fails on ``1099de2b``: the three cases that send a request each wait out their
5 s for a reply that never comes (``TimeoutError``), and
``test_a_reply_utf8_can_carry_is_written_as_it_was`` fails with ``AttributeError``,
as ``rpc_mode._reply_bytes`` does not exist there.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aelix_coding_agent.rpc import rpc_mode
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode
from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

from tests.rpc.test_runtime_control_lifecycle_594 import Wire, _control
from tests.rpc.test_runtime_control_surfaces_594 import _harness


async def _answer(request: dict[str, Any]) -> dict[str, Any]:
    harness = _harness()
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(harness, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    try:
        wire.reader.feed_data((json.dumps(request) + "\n").encode("ascii"))
        return await asyncio.wait_for(wire.response(request["id"]), 5)
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await harness.dispose()


async def test_a_control_result_utf8_cannot_carry_answers_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def odd(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"value": "path \udcff"}

    monkeypatch.setattr(RpcControlPlane, "_dispatch", odd)
    response = await _answer(_control("x", "mcp/status"))
    assert response["success"] is False and response["command"] == "aelix_control"
    # The control guard's own error (``control_error_message``), as for #594's.
    assert response["error"].startswith("'utf-8' codec can't encode character '\\udcff'"), response


async def test_a_classic_reply_utf8_cannot_carry_answers_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def odd(harness: Any, payload: dict[str, Any], dispatch: Any) -> Any:
        return RpcSuccessResponse(command="get_state", id=payload.get("id"), data={"sessionName": "\udcff"})

    monkeypatch.setattr(rpc_mode, "_handle_command", odd)
    response = await _answer({"type": "get_state", "id": "g1"})
    assert response == {
        "type": "response",
        "command": "get_state",
        "success": False,
        "id": "g1",
        "error": response["error"],
    }
    assert response["error"].startswith("the reply could not be written as UTF-8: "), response


async def test_a_request_whose_own_id_utf8_cannot_carry_is_still_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    async def plain(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"value": "fine"}

    monkeypatch.setattr(RpcControlPlane, "_dispatch", plain)
    response = await _answer(_control("id-\udcff", "mcp/status"))
    assert response["id"] == "id-\udcff" and response["success"] is False, response
    assert response["error"].startswith("the reply could not be written as UTF-8: "), response


def test_a_reply_utf8_can_carry_is_written_as_it_was() -> None:
    reply = RpcSuccessResponse(command="get_state", id="a", data={"name": "한글  "})
    assert rpc_mode._reply_bytes(reply) == (json.dumps(reply.to_json(), ensure_ascii=False) + "\n").encode("utf-8")
