"""Sprint 6h₄c · Phase 4.13 closure pin (ADR-0079 / ADR-0080).

PHASE 4 RPC CLOSURE: wires the 3 last DEFERRED session-tree commands
(``switch_session`` / ``fork`` / ``clone``) on top of the 6h₄b
:class:`AgentSessionRuntime` foundation. Counts move to **29 supported
/ 0 deferred / 29 total** — full Pi parity for the ``RpcCommand``
discriminator union.

Pi parity invariants:
  - 29 supported + 0 deferred = 29 total.
  - 3 new handlers wired (NOT stubbed).
  - Runtime constructor extended with required keyword-only ``repo`` +
    ``fs`` (P-324).
  - ``_apply_for_test`` test seam REMOVED (P-331).
  - Wire shapes: P-327 fork ``selectedText → text`` + key-omission,
    P-328 clone drops ``text`` entirely.
  - P-329 deliberate convergence: handlers DO NOT manually rebind.
  - P-328 ordering: ``leaf_id`` captured BEFORE OLD harness dispose.
  - Pi line citations in handler docstrings.

Closure date: **2026-05-21**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster: P-323 ~ P-331.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    _SUPPORTED_HANDLERS_HARNESS_ONLY,
    _SUPPORTED_HANDLERS_RUNTIME_HOST,
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    _bind_runtime_host,
    _handle_clone,
    _handle_fork,
    _handle_new_session,
    _handle_switch_session,
    build_dispatch_table,
    run_rpc_mode,
)
from aelix_coding_agent.rpc.rpc_types import (
    RPC_COMMAND_TYPES,
    RpcCommandClone,
    RpcCommandFork,
    RpcCommandSwitchSession,
    RpcSuccessResponse,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict[str, Any]:
    return json.loads(
        (_FIXTURES / "pi_runtime_wire_734e08e.json").read_text()
    )


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _new_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _make_runtime_with_source(
    tmp_path: Path,
) -> tuple[AgentSessionRuntime, JsonlSessionRepo, LocalFileSystem, Session]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    return runtime, repo, fs, source


# === §A — counts (CLOSURE) ====================================================


def test_supported_count_is_29() -> None:
    """Phase 4 closure: 29 supported commands."""

    assert len(SUPPORTED_COMMANDS) == 29


def test_deferred_count_is_zero() -> None:
    """Phase 4 closure: DEFERRED dict is empty."""

    assert len(DEFERRED_COMMANDS) == 0


def test_supported_equals_pi_command_types() -> None:
    """Phase 4 closure: SUPPORTED == RPC_COMMAND_TYPES (full Pi parity,
    NOT just disjoint union).
    """

    assert SUPPORTED_COMMANDS == RPC_COMMAND_TYPES


# === §B — 3 new handlers wired (NOT stubbed) =================================


@pytest.mark.parametrize(
    "cmd_type", ["switch_session", "fork", "clone", "new_session"]
)
def test_runtime_host_handler_routes_through_bind_runtime_host(
    cmd_type: str, tmp_path: Path,
) -> None:
    """§B — the runtime-host handlers resolve through the
    :func:`_bind_runtime_host` adapter, NOT
    :func:`_make_deferred_handler`.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))

    async def _factory(_s: Session) -> AgentHarness:
        return _new_harness()

    runtime = AgentSessionRuntime(
        _new_harness(), _factory, repo=repo, fs=fs
    )
    table = build_dispatch_table(runtime_host=runtime)
    handler = table[cmd_type]
    name = getattr(handler, "__qualname__", repr(handler))
    assert "_bind_runtime_host" in name or "_adapted" in name
    assert "deferred" not in name.lower()


# === §C — runtime constructor extension (P-324) ==============================


def test_runtime_init_signature_has_required_repo_and_fs() -> None:
    """P-324: ``repo`` + ``fs`` are required keyword-only with no default."""

    sig = inspect.signature(AgentSessionRuntime.__init__)
    params = sig.parameters
    assert "repo" in params
    assert "fs" in params
    assert params["repo"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["fs"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["repo"].default is inspect.Parameter.empty
    assert params["fs"].default is inspect.Parameter.empty


def test_runtime_init_requires_repo_kwarg() -> None:
    """P-324 enforcement: omitting ``repo`` raises TypeError."""

    h = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h

    with pytest.raises(TypeError):
        AgentSessionRuntime(h, _factory, fs=LocalFileSystem())  # type: ignore[call-arg]


def test_runtime_init_requires_fs_kwarg() -> None:
    """P-324 enforcement: omitting ``fs`` raises TypeError."""

    h = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h

    fs = LocalFileSystem()
    with pytest.raises(TypeError):
        AgentSessionRuntime(h, _factory, repo=JsonlSessionRepo(fs=fs))  # type: ignore[call-arg]


def test_apply_for_test_removed_p331() -> None:
    """P-331: ``_apply_for_test`` REMOVED — tests migrate to public
    ``switch_session``.
    """

    assert not hasattr(AgentSessionRuntime, "_apply_for_test")


# === §D — wire shape pins =====================================================


async def test_handle_fork_wire_shape_includes_text_when_present(
    tmp_path: Path,
) -> None:
    """P-327 wire: ``{cancelled, text}`` when ``selected_text`` is non-None."""

    runtime, _, _, source = await _make_runtime_with_source(tmp_path)
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    response = await _handle_fork(
        runtime, RpcCommandFork(entry_id=entry_id, id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.data == {"cancelled": False, "text": "hi"}


async def test_handle_fork_wire_shape_omits_text_key_when_none() -> None:
    """P-327 key-omission per P-298: ``text`` key OMITTED when
    ``selected_text is None`` (matches Pi
    ``JSON.stringify({text: undefined})``).

    W4 MINOR-3 (Sprint 6h₄c W6 rewrite): drive ``_handle_fork`` through
    a mocked runtime that returns
    ``RuntimeReplaceResult(cancelled=False, selected_text=None)`` so the
    handler-layer key-omission invariant is asserted end-to-end (not
    just the inline envelope construction).
    """

    from aelix_agent_core.runtime._types import RuntimeReplaceResult

    runtime = MagicMock(spec=AgentSessionRuntime)
    runtime.fork = AsyncMock(
        return_value=RuntimeReplaceResult(
            cancelled=False, selected_text=None
        )
    )
    cmd = RpcCommandFork(id="test-1", entry_id="entry-abc")
    response = await _handle_fork(runtime, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "fork"
    assert response.data == {"cancelled": False}
    assert "text" not in response.data


async def test_handle_clone_wire_shape_drops_text(tmp_path: Path) -> None:
    """P-328 wire: clone returns ``{cancelled}`` only (Pi line 588
    drops ``selectedText``).
    """

    runtime, _, _, source = await _make_runtime_with_source(tmp_path)
    await source.append_message(
        UserMessage(content=[TextContent(text="leaf")])
    )
    response = await _handle_clone(runtime, RpcCommandClone(id="r"))
    assert isinstance(response, RpcSuccessResponse)
    assert response.data == {"cancelled": False}
    assert "text" not in response.data


async def test_handle_switch_session_wire_shape(tmp_path: Path) -> None:
    """Pi parity ``:568``: switch_session returns ``{cancelled}``."""

    runtime, repo, _, _ = await _make_runtime_with_source(tmp_path)
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_metadata = await target.get_metadata()
    response = await _handle_switch_session(
        runtime,
        RpcCommandSwitchSession(session_path=target_metadata.path, id="r"),
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.data == {"cancelled": False}


# === §E — P-329 deliberate convergence ========================================


async def test_handle_switch_session_invokes_rebind_exactly_once(
    tmp_path: Path,
) -> None:
    """P-329: handler does NOT call rebind manually — runtime auto-
    invokes the registered callback EXACTLY ONCE per replace.
    """

    runtime, repo, _, _ = await _make_runtime_with_source(tmp_path)
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_metadata = await target.get_metadata()
    cb = AsyncMock()
    runtime.set_rebind_session(cb)
    await _handle_switch_session(
        runtime,
        RpcCommandSwitchSession(session_path=target_metadata.path, id="r"),
    )
    assert cb.await_count == 1


async def test_handle_fork_invokes_rebind_exactly_once(
    tmp_path: Path,
) -> None:
    """P-329."""

    runtime, _, _, source = await _make_runtime_with_source(tmp_path)
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text="x")])
    )
    cb = AsyncMock()
    runtime.set_rebind_session(cb)
    await _handle_fork(runtime, RpcCommandFork(entry_id=entry_id, id="r"))
    assert cb.await_count == 1


async def test_handle_clone_invokes_rebind_exactly_once(
    tmp_path: Path,
) -> None:
    """P-329."""

    runtime, _, _, source = await _make_runtime_with_source(tmp_path)
    await source.append_message(
        UserMessage(content=[TextContent(text="x")])
    )
    cb = AsyncMock()
    runtime.set_rebind_session(cb)
    await _handle_clone(runtime, RpcCommandClone(id="r"))
    assert cb.await_count == 1


# === §F — P-328 clone leaf-id pre-capture ordering ===========================


async def test_clone_get_leaf_id_resolves_before_dispose(
    tmp_path: Path,
) -> None:
    """P-328 ordering: ``get_leaf_id`` resolves BEFORE the OLD harness's
    ``dispose()`` is awaited.
    """

    runtime, _, _, source = await _make_runtime_with_source(tmp_path)
    await source.append_message(
        UserMessage(content=[TextContent(text="leaf")])
    )
    order: list[str] = []

    original_get_leaf_id = source.get_leaf_id

    async def _get_leaf_id_spy() -> str | None:
        result = await original_get_leaf_id()
        order.append("get_leaf_id")
        return result

    source.get_leaf_id = _get_leaf_id_spy  # type: ignore[method-assign]

    old_harness = runtime.harness
    original_dispose = old_harness.dispose

    async def _dispose_spy() -> None:
        order.append("dispose")
        await original_dispose()

    old_harness.dispose = _dispose_spy  # type: ignore[method-assign]

    await _handle_clone(runtime, RpcCommandClone(id="r"))
    assert order[:2] == ["get_leaf_id", "dispose"]


# === §G — run_rpc_mode signature (P-324) ======================================


def test_run_rpc_mode_signature_has_optional_repo_kwarg() -> None:
    sig = inspect.signature(run_rpc_mode)
    assert "repo" in sig.parameters
    assert sig.parameters["repo"].default is None


def test_run_rpc_mode_signature_has_optional_fs_kwarg() -> None:
    sig = inspect.signature(run_rpc_mode)
    assert "fs" in sig.parameters
    assert sig.parameters["fs"].default is None


def test_build_dispatch_table_signature_has_runtime_host_kwarg() -> None:
    sig = inspect.signature(build_dispatch_table)
    assert "runtime_host" in sig.parameters
    assert (
        sig.parameters["runtime_host"].kind
        == inspect.Parameter.KEYWORD_ONLY
    )


# === §H — Pi line-citation pins ===============================================


def test_handle_switch_session_docstring_cites_pi_lines() -> None:
    assert _handle_switch_session.__doc__ is not None
    assert "563-569" in _handle_switch_session.__doc__


def test_handle_fork_docstring_cites_pi_lines() -> None:
    assert _handle_fork.__doc__ is not None
    assert "571-577" in _handle_fork.__doc__


def test_handle_clone_docstring_cites_pi_lines() -> None:
    assert _handle_clone.__doc__ is not None
    assert "579-589" in _handle_clone.__doc__


def test_handle_new_session_docstring_cites_pi_lines_and_p330() -> None:
    assert _handle_new_session.__doc__ is not None
    assert "277-282" in _handle_new_session.__doc__
    assert "P-330" in _handle_new_session.__doc__


def test_runtime_switch_session_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime.switch_session.__doc__ is not None
    assert "175-198" in AgentSessionRuntime.switch_session.__doc__


def test_runtime_new_session_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime.new_session.__doc__ is not None
    assert "200-232" in AgentSessionRuntime.new_session.__doc__


def test_runtime_fork_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime.fork.__doc__ is not None
    assert "234-320" in AgentSessionRuntime.fork.__doc__


# === §I — cumulative cascade allowlists =======================================


def test_cascade_pins_remain_green_over_empty_deferred() -> None:
    """4.4 / 4.9 / 4.10 / 4.11 / 4.12 cascade pins still pass over the
    empty DEFERRED dict — they assert ``len(DEFERRED_COMMANDS) == 0``.
    """

    assert DEFERRED_COMMANDS == {}


# === §J — fixture =============================================================


def test_fixture_pi_sha_pinned() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_fixture_phase_id() -> None:
    fixture = _load_fixture()
    assert fixture["phase"] == "4.13"


def test_fixture_closure_counts() -> None:
    fixture = _load_fixture()
    assert fixture["closure_supported"] == 29
    assert fixture["closure_deferred"] == 0
    assert fixture["closure_total"] == 29


def test_fixture_pi_handler_lines() -> None:
    fixture = _load_fixture()
    assert fixture["pi_handler_lines"] == {
        "switch_session": "563-569",
        "fork": "571-577",
        "clone": "579-589",
        "new_session": "277-282",
    }


def test_fixture_aelix_drops_in_memory_fork() -> None:
    fixture = _load_fixture()
    assert fixture["aelix_drops_in_memory_fork"] is True


def test_fixture_handler_side_rebind_decision() -> None:
    fixture = _load_fixture()
    assert (
        fixture["handler_side_rebind"]
        == "deliberate-convergence-runtime-owns"
    )


def test_fixture_p331_apply_for_test_removed() -> None:
    fixture = _load_fixture()
    assert fixture["p_331_apply_for_test_removed"] is True


# === §K — arity / move pin (P-326 + P-330) ====================================


def test_new_session_moved_to_runtime_host_arity_class() -> None:
    """P-330 arity change."""

    assert "new_session" in _SUPPORTED_HANDLERS_RUNTIME_HOST
    assert "new_session" not in _SUPPORTED_HANDLERS_HARNESS_ONLY


def test_runtime_host_arity_class_has_four_entries() -> None:
    """P-326: the 4-entry runtime-host class — new_session +
    switch_session + fork + clone.
    """

    assert set(_SUPPORTED_HANDLERS_RUNTIME_HOST.keys()) == {
        "new_session",
        "switch_session",
        "fork",
        "clone",
    }


def test_bind_runtime_host_callable() -> None:
    assert callable(_bind_runtime_host)
