"""#469 - the harness reads settings.json's ``compaction`` block.

pi reads ``settingsManager.getCompactionSettings(model)`` at every threshold check,
overflow recovery and compaction (``agent-session.ts`` ``_checkCompaction``,
``_runAutoCompaction``, ``compact`` @ fdce48eb4): ``enabled`` gates both automatic
compactions, ``reserveTokens`` sets the threshold (``contextTokens > contextWindow -
reserveTokens``) and ``keepRecentTokens`` the cut (``prepareCompaction(pathEntries,
settings)``). Its ``autoCompactionEnabled`` getter / ``setAutoCompactionEnabled`` setter
ARE the setting, so rpc ``set_auto_compaction`` writes the global settings file.

Before #469 aelix read none of it: the reserve (16384) and the kept tail (20000) were
module constants and ``enabled`` an in-memory toggle, so ``compaction.keepRecentTokens:
20`` left rpc ``compact`` at "Nothing to compact" (the owner's 2026-10-21 measurement).
The constants stay the fallback for a value the settings do not carry, read at the
decision (#408's pattern for ``retry.*``).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    _compaction_policy,
)
from aelix_agent_core.harness.hooks import SessionBeforeCompactHookEvent
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.session import compaction as compaction_mod
from aelix_agent_core.session.compaction import prepare_compaction
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.settings import Settings, SettingsManager
from aelix_ai.settings.types import CompactionSettings
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_command,
    _handle_get_state,
    _handle_set_auto_compaction,
)
from aelix_coding_agent.rpc.rpc_types import RpcErrorResponse, RpcSuccessResponse

_OVERFLOW_MSG = "prompt is too long: 213462 tokens > 200000 maximum"


def _override(summary: str = "SUMMARY_469") -> Any:
    async def fn(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary=summary, first_kept_entry_id=prep.first_kept_entry_id, tokens_before=7
        )

    return fn


async def _small_session(turns: int = 3) -> Session:
    """``turns`` short exchanges: ~60 tokens a prompt, far below the 20000 default."""

    session = Session(MemorySessionStorage())
    for i in range(turns):
        await session.append_message(
            UserMessage(content=[TextContent(text=f"turn {i}: " + "word " * 48)])
        )
        await session.append_message(
            AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )
    return session


async def _large_session() -> Session:
    """Six ~7500-token prompts (``tests/test_compact.py``'s seed)."""

    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


def _harness(
    sm: SettingsManager | None, session: Session | None = None, *, window: int = 200_000
) -> AgentHarness:
    h = AgentHarness(
        AgentHarnessOptions(
            session=session if session is not None else Session(MemorySessionStorage()),
            settings_manager=sm,
            _summarizer_override=_override(),
        )
    )
    h._state.model = SimpleNamespace(context_window=window, provider="probe", id="m")  # type: ignore[assignment]
    h._state.auto_retry_enabled = False
    return h


def _used(tokens: int) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="ok")], stop_reason="end_turn", usage={"input": tokens}
    )


def _spy_compact(h: AgentHarness) -> list[str]:
    calls: list[str] = []

    async def _spy(_ci: object = None, *, reason: str = "manual", **_kw: object) -> Any:
        calls.append(reason)
        return CompactResult(summary="(spy)", first_kept_entry_id="x", tokens_before=0)

    h.compact = _spy  # type: ignore[method-assign]
    return calls


def _sm(compaction: Any) -> SettingsManager:
    return SettingsManager.in_memory({"compaction": compaction})


# === the reader ==================================================================


def test_no_manager_and_empty_settings_take_the_constants() -> None:
    for sm in (None, SettingsManager.in_memory({})):
        policy = _compaction_policy(sm)
        assert (policy.enabled, policy.reserve_tokens, policy.keep_recent_tokens) == (
            None,
            16384,
            20000,
        )


def test_the_three_keys_are_read() -> None:
    policy = _compaction_policy(
        _sm({"enabled": False, "reserveTokens": 60000, "keepRecentTokens": 20})
    )
    assert (policy.enabled, policy.reserve_tokens, policy.keep_recent_tokens) == (False, 60000, 20)


def test_zero_and_an_integral_float_are_accepted() -> None:
    """pi accepts any non-negative safe integer, ``0`` included; JSON ``20.0`` is that number."""

    policy = _compaction_policy(_sm({"reserveTokens": 0, "keepRecentTokens": 20.0}))
    assert (policy.reserve_tokens, policy.keep_recent_tokens) == (0, 20)
    assert isinstance(policy.keep_recent_tokens, int)


@pytest.mark.parametrize(
    "value",
    ["5000", True, False, None, [1], {"a": 1}, -1, 1.5, float("nan"), float("inf"), 2**53],
    ids=["str", "true", "false", "null", "list", "dict", "neg", "frac", "nan", "inf", "unsafe"],
)
def test_a_value_pi_would_refuse_falls_back(value: Any) -> None:
    """pi throws ``Expected a non-negative safe integer``; a raise here would fail the
    turn that just succeeded, so the value is ignored instead (#408's rule)."""

    policy = _compaction_policy(_sm({"reserveTokens": value, "keepRecentTokens": value}))
    assert (policy.reserve_tokens, policy.keep_recent_tokens) == (16384, 20000)


@pytest.mark.parametrize("block", [False, True, "off", [], 3], ids=["false", "true", "str", "list", "int"])
def test_a_compaction_that_is_not_an_object_is_ignored(block: Any) -> None:
    policy = _compaction_policy(_sm(block))
    assert (policy.enabled, policy.reserve_tokens, policy.keep_recent_tokens) == (
        None,
        16384,
        20000,
    )


@pytest.mark.parametrize("value", ["false", 0, 1, None], ids=["str", "zero", "one", "null"])
def test_a_non_boolean_enabled_leaves_the_toggle_in_force(value: Any) -> None:
    assert _compaction_policy(_sm({"enabled": value})).enabled is None


def test_the_fallbacks_are_the_constants_as_they_are_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_mod, "_AUTO_COMPACT_RESERVE_TOKENS", 111)
    monkeypatch.setattr(compaction_mod, "KEEP_RECENT_TOKENS", 222)
    policy = _compaction_policy(SettingsManager.in_memory({}))
    assert (policy.reserve_tokens, policy.keep_recent_tokens) == (111, 222)


# === reserveTokens: the threshold ================================================


async def test_reserve_tokens_moves_the_threshold_earlier() -> None:
    """150000 of a 200000 window: under the default 183616, over 200000 - 60000."""

    for settings, expected in (({}, []), ({"reserveTokens": 60000}, ["threshold"])):
        h = _harness(_sm(settings), await _small_session())
        calls = _spy_compact(h)
        h._state.messages = [_used(150_000)]
        await h._check_auto_compaction()
        assert calls == expected, settings


async def test_reserve_tokens_moves_the_threshold_later() -> None:
    """190000: over the default 183616, under 200000 - 1000."""

    for settings, expected in (({}, ["threshold"]), ({"reserveTokens": 1000}, [])):
        h = _harness(_sm(settings), await _small_session())
        calls = _spy_compact(h)
        h._state.messages = [_used(190_000)]
        await h._check_auto_compaction()
        assert calls == expected, settings


async def test_reserve_tokens_0_compacts_only_past_the_window() -> None:
    for settings, expected in (({}, ["threshold"]), ({"reserveTokens": 0}, [])):
        h = _harness(_sm(settings), await _small_session())
        calls = _spy_compact(h)
        h._state.messages = [_used(199_999)]
        await h._check_auto_compaction()
        assert calls == expected, settings


async def test_a_settings_change_applies_to_the_next_check() -> None:
    """Read at each check, not at construction: ``/reload`` reaches the next one."""

    sm = SettingsManager.in_memory({})
    h = _harness(sm, await _small_session())
    calls = _spy_compact(h)
    h._state.messages = [_used(150_000)]
    await h._check_auto_compaction()
    assert calls == []
    sm.apply_overrides(Settings(compaction=CompactionSettings(reserve_tokens=60000)))
    await h._check_auto_compaction()
    assert calls == ["threshold"]


# === enabled: both automatic compactions, the getter and the setter ==============


async def test_enabled_false_stops_the_threshold_compaction() -> None:
    for settings, expected in (({}, ["threshold"]), ({"enabled": False}, [])):
        h = _harness(_sm(settings), await _small_session())
        calls = _spy_compact(h)
        h._state.messages = [_used(190_000)]
        await h._check_auto_compaction()
        assert calls == expected, settings


async def test_settings_enabled_true_wins_over_the_harness_toggle() -> None:
    """As #408's ``retry.enabled``: a boolean in the settings is what applies."""

    h = _harness(_sm({"enabled": True}), await _small_session())
    h._state.auto_compaction_enabled = False
    calls = _spy_compact(h)
    h._state.messages = [_used(190_000)]
    await h._check_auto_compaction()
    assert calls == ["threshold"]
    assert h.auto_compaction_enabled is True


async def test_without_a_setting_the_harness_toggle_still_answers() -> None:
    h = _harness(SettingsManager.in_memory({}), await _small_session())
    h._state.auto_compaction_enabled = False
    calls = _spy_compact(h)
    h._state.messages = [_used(190_000)]
    await h._check_auto_compaction()
    assert calls == []
    assert h.auto_compaction_enabled is False


async def test_enabled_false_stops_the_overflow_recovery() -> None:
    """pi returns before its overflow case when ``!settings.enabled`` (``_checkCompaction``)."""

    for settings, expected_runs, expected_compacts in (
        ({}, 2, ["overflow"]),
        ({"enabled": False}, 1, []),
    ):
        h = _harness(_sm(settings))
        calls = _spy_compact(h)
        runs: list[None] = []

        async def _fake_run(
            prompts: Any, *, system_prompt: Any = None, pending_inputs: Any = None, h=h, runs=runs
        ) -> list[Any]:
            runs.append(None)
            if len(runs) == 1:
                h._state.messages.extend(prompts)
                h._state.messages.append(
                    AssistantMessage(
                        content=[TextContent(text="")],
                        stop_reason="error",
                        error_message=_OVERFLOW_MSG,
                    )
                )
            else:
                h._state.messages.append(_used(10))
            return list(h._state.messages)

        h._run = _fake_run  # type: ignore[method-assign]
        await h.prompt("big")
        assert (len(runs), calls) == (expected_runs, expected_compacts), settings


def test_the_getter_reports_the_setting() -> None:
    h = _harness(_sm({"enabled": False}))
    assert h._state.auto_compaction_enabled is True
    assert h.auto_compaction_enabled is False
    assert _harness(None).auto_compaction_enabled is True


async def test_get_state_reports_the_setting() -> None:
    h = _harness(_sm({"enabled": False}))
    h._state.model = None  # get_state serialises a real model; none is enough here
    response = await _handle_command(h, {"type": "get_state", "id": "s"}, {"get_state": _handle_get_state})
    assert isinstance(response, RpcSuccessResponse)
    assert response.data["autoCompactionEnabled"] is False


async def test_set_auto_compaction_persists_into_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pi's ``setAutoCompactionEnabled`` is ``settingsManager.setCompactionEnabled``: the
    write reaches the GLOBAL ``settings.json`` and keeps the block's other keys."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    path = agent / "settings.json"
    path.write_text(json.dumps({"compaction": {"keepRecentTokens": 20}}), "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=False)
    h = _harness(sm)
    response = await _handle_command(
        h,
        {"type": "set_auto_compaction", "enabled": False, "id": "c"},
        {"set_auto_compaction": _handle_set_auto_compaction},
    )
    await sm.flush()
    assert isinstance(response, RpcSuccessResponse)
    assert json.loads(path.read_text("utf-8")) == {
        "compaction": {"enabled": False, "keepRecentTokens": 20}
    }
    assert h.auto_compaction_enabled is False
    assert h._state.auto_compaction_enabled is False


async def test_set_auto_compaction_replaces_a_non_object_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``"compaction": true`` in the global file: the setter replaced nothing and raised
    ``AttributeError`` on ``True.enabled`` (pi assigns into whatever is there)."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    path = agent / "settings.json"
    path.write_text(json.dumps({"compaction": True}), "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=False)
    h = _harness(sm)
    h.set_auto_compaction_enabled(False)
    await sm.flush()
    assert json.loads(path.read_text("utf-8")) == {"compaction": {"enabled": False}}
    assert h.auto_compaction_enabled is False


async def test_a_project_setting_still_wins_after_set_auto_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As in pi: the setter writes the GLOBAL file; a project ``compaction.enabled`` stays."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "settings.json").write_text(
        json.dumps({"compaction": {"enabled": True}}), "utf-8"
    )
    path = agent / "settings.json"
    path.write_text("{}", "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=True)
    h = _harness(sm)
    h.set_auto_compaction_enabled(False)
    await sm.flush()
    assert json.loads(path.read_text("utf-8")) == {"compaction": {"enabled": False}}
    assert h.auto_compaction_enabled is True


async def test_a_failed_settings_write_leaves_the_toggle_as_it_was() -> None:
    """The write goes first (#408 review round 2): rpc answers ``success: false`` and
    nothing has moved."""

    sm = SettingsManager.in_memory({})

    def _boom(_enabled: bool) -> None:
        raise OSError("read-only settings")

    sm.set_compaction_enabled = _boom  # type: ignore[method-assign]
    h = _harness(sm)
    response = await _handle_command(
        h,
        {"type": "set_auto_compaction", "enabled": False, "id": "c"},
        {"set_auto_compaction": _handle_set_auto_compaction},
    )
    assert isinstance(response, RpcErrorResponse)
    assert h._state.auto_compaction_enabled is True
    assert h.auto_compaction_enabled is True


def test_without_a_manager_the_setter_sets_the_toggle() -> None:
    h = _harness(None)
    h.set_auto_compaction_enabled(False)
    assert (h._state.auto_compaction_enabled, h.auto_compaction_enabled) == (False, False)


# === keepRecentTokens: the cut ===================================================


async def test_keep_recent_tokens_20_lets_a_small_session_compact() -> None:
    """The owner's 2026-10-21 rpc measurement: ``compact`` said 'Nothing to compact'."""

    h = _harness(SettingsManager.in_memory({}), await _small_session())
    with pytest.raises(AgentHarnessError, match="Nothing to compact"):
        await h.compact()

    session = await _small_session()
    h = _harness(_sm({"keepRecentTokens": 20}), session)
    result = await h.compact()
    assert result.summary.startswith("SUMMARY_469")
    entries = await session.get_entries()
    [compaction] = [e for e in entries if e.type == "compaction"]
    ids = [e.id for e in entries]
    # u0 a0 u1 a1 u2 a2: the last prompt (~60 tokens) alone reaches 20, so u2 is kept.
    assert ids.index(compaction.first_kept_entry_id) == 4  # type: ignore[union-attr]


async def test_keep_recent_tokens_0_keeps_only_the_last_message() -> None:
    """``0`` is a value, not "unset": pi's walk stops at the first message from the end."""

    session = await _small_session()
    h = _harness(_sm({"keepRecentTokens": 0}), session)
    await h.compact()
    entries = await session.get_entries()
    [compaction] = [e for e in entries if e.type == "compaction"]
    assert [e.id for e in entries].index(compaction.first_kept_entry_id) == 5  # type: ignore[union-attr]


async def test_keep_recent_tokens_larger_than_the_default_keeps_more() -> None:
    """Six ~7500-token prompts: 20000 keeps three (cut at 3), 30000 keeps four (cut at 2)."""

    for settings, kept_index in (({}, 3), ({"keepRecentTokens": 30000}, 2)):
        session = await _large_session()
        h = _harness(_sm(settings), session)
        await h.compact()
        entries = await session.get_entries()
        [compaction] = [e for e in entries if e.type == "compaction"]
        assert [e.id for e in entries].index(compaction.first_kept_entry_id) == kept_index  # type: ignore[union-attr]


async def test_the_threshold_compaction_cuts_with_the_setting() -> None:
    """Threshold and cut together, through the real ``compact()``: the rpc probe's run."""

    session = await _small_session()
    h = _harness(_sm({"reserveTokens": 60000, "keepRecentTokens": 20}), session)
    h._state.messages = [_used(150_000)]
    await h._check_auto_compaction()
    entries = await session.get_entries()
    assert [e.type for e in entries].count("compaction") == 1


async def test_the_preparation_carries_the_resolved_settings() -> None:
    """pi's ``preparation.settings``, what a ``session_before_compact`` handler reads."""

    seen: list[Any] = []

    async def on_before(ev: SessionBeforeCompactHookEvent, _ctx: Any) -> None:
        seen.append(ev.preparation.settings)

    h = _harness(_sm({"keepRecentTokens": 20, "reserveTokens": "x"}), await _small_session())
    h.hooks.on("session_before_compact", on_before)
    await h.compact()
    assert seen == [CompactionSettings(enabled=True, reserve_tokens=16384, keep_recent_tokens=20)]

    seen.clear()
    h = _harness(_sm({"enabled": False}), await _large_session())
    h.hooks.on("session_before_compact", on_before)
    await h.compact()
    assert seen == [CompactionSettings(enabled=False, reserve_tokens=16384, keep_recent_tokens=20000)]


async def test_prepare_compaction_without_settings_keeps_the_constant() -> None:
    entries = await (await _small_session()).get_branch()
    assert prepare_compaction(entries) is None
    assert prepare_compaction(entries, settings=CompactionSettings()) is None
    prep = prepare_compaction(entries, settings=CompactionSettings(keep_recent_tokens=20))
    assert prep is not None
    assert [e.id for e in entries].index(prep.first_kept_entry_id) == 4
    assert prep.settings == CompactionSettings(keep_recent_tokens=20)
