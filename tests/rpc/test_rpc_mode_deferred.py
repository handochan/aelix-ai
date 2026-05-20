"""Pi parity: every command in :data:`DEFERRED_COMMANDS` returns an
:class:`RpcErrorResponse` with ``success: false`` and a message that
identifies the owning ADR.

Sprint 6d §G unit test for the 20-command deferred allowlist (ADR-0058).
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    _make_deferred_handler,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RPC_COMMAND_TYPES,
    RpcErrorResponse,
)


class _StubCommand:
    def __init__(self, cmd_id: str | None = None) -> None:
        self.id = cmd_id


async def test_every_deferred_command_returns_error_response() -> None:
    """Each remaining deferred command maps to an :class:`RpcErrorResponse`.

    Sprint 6h₂ (ADR-0071 / ADR-0072) shrinks the deferred set to the 7
    session-tree + session-inspection commands; the owner string moved
    from ADR-0058 to ADR-0072.
    """

    for cmd_type, owner_adr in DEFERRED_COMMANDS.items():
        handler = _make_deferred_handler(cmd_type, owner_adr)
        response = await handler(None, _StubCommand(cmd_id=f"req-{cmd_type}"))
        assert isinstance(response, RpcErrorResponse)
        assert response.success is False
        assert response.command == cmd_type
        assert response.id == f"req-{cmd_type}"
        # ADR string surfaces in the error so consumers can map to the
        # owning sprint without re-reading the spec.
        assert ("ADR-0058" in response.error) or ("ADR-0072" in response.error)
        assert cmd_type in response.error


async def test_deferred_handler_preserves_none_id() -> None:
    """Pi parity: id field is optional and echoes None when not supplied."""

    handler = _make_deferred_handler(
        "steer", "ADR-0058 — Sprint 6f harness command paths"
    )
    response = await handler(None, _StubCommand())
    assert isinstance(response, RpcErrorResponse)
    assert response.id is None


def test_deferred_command_count_matches_spec() -> None:
    """Spec §0 P-107: 20 deferred commands per ADR-0058 closure pin
    (W4 M2 / P-121 — fixture ``rpc_command_types`` is authoritative).

    Pi RpcCommand variant count is 29 = 9 supported + 20 deferred. The
    spec preamble undercounts by one; the fixture's ``rpc_command_types``
    list is the source of truth for the wire surface.
    """

    # 9 supported + 20 deferred = 29 = Pi RpcCommand variant count.
    # ``get_commands`` and ``extension_ui_*`` were tabled in §A "NOT in
    # scope" — the implementation counts them in the deferred set since
    # they're Pi-wire commands that need future work.
    assert len(DEFERRED_COMMANDS) == len(RPC_COMMAND_TYPES) - len(
        SUPPORTED_COMMANDS
    )


def test_deferred_dict_owner_adr_format() -> None:
    """All deferred entries cite a closure ADR.

    Sprint 6d closure was ADR-0058; Sprint 6h₂ (ADR-0072) re-homed the
    remaining 7 carry-forward commands. Accept either prefix so the pin
    remains green across the transition.
    """

    for owner in DEFERRED_COMMANDS.values():
        assert ("ADR-0058" in owner) or ("ADR-0072" in owner)


def test_full_dispatch_table_deferred_route() -> None:
    """A built dispatch table routes every deferred command to an
    :class:`RpcErrorResponse` producer.
    """

    table = build_dispatch_table()
    for cmd_type in DEFERRED_COMMANDS:
        assert cmd_type in table


@pytest.mark.parametrize("cmd_type", sorted(DEFERRED_COMMANDS.keys()))
async def test_dispatch_table_deferred_handler_returns_error(cmd_type: str) -> None:
    """Each deferred handler in the dispatch table yields a Pi error envelope."""

    table = build_dispatch_table()
    handler = table[cmd_type]
    response = await handler(None, _StubCommand(cmd_id="x"))
    assert isinstance(response, RpcErrorResponse)
    assert response.command == cmd_type
    assert response.success is False
