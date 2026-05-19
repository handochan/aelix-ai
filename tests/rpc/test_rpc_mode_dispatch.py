"""Pi parity: ``parse_rpc_command`` happy path + bad type.

Sprint 6d §G unit test for the dispatcher entry.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RPC_COMMAND_TYPES,
    parse_rpc_command,
)


def test_parse_happy_path_prompt() -> None:
    cmd = parse_rpc_command({"type": "prompt", "message": "hi", "id": "r1"})
    assert cmd.type == "prompt"
    assert cmd.message == "hi"  # type: ignore[union-attr]
    assert cmd.id == "r1"


def test_parse_bad_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_rpc_command({"type": "frobnicate"})


def test_parse_missing_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_rpc_command({"foo": "bar"})


def test_dispatch_table_covers_all_29_commands() -> None:
    """9 supported + 20 deferred should cover Pi's full RpcCommand set."""

    table = build_dispatch_table()
    assert set(table.keys()) == RPC_COMMAND_TYPES


def test_supported_and_deferred_partition_is_disjoint() -> None:
    overlap = SUPPORTED_COMMANDS & set(DEFERRED_COMMANDS.keys())
    assert overlap == set()


def test_supported_plus_deferred_equals_pi_command_types() -> None:
    union = SUPPORTED_COMMANDS | set(DEFERRED_COMMANDS.keys())
    assert union == RPC_COMMAND_TYPES
