"""#408 - the turn auto-retry reads ``settings.json``'s ``retry`` block.

pi's ``_prepareRetry`` reads ``settingsManager.getRetrySettings()`` at every
retry (``agent-session.ts:3792-3801`` @ 3daa20280): ``enabled``,
``maxRetries`` (retries AFTER the first attempt: ``attempt > maxRetries``
stops), ``baseDelayMs`` (``base * 2^(attempt-1)``) and ``maxAgentDelayMs`` (the
cap, ``retryDelayMs``, ``packages/ai/src/utils/retry.ts:129-133``). Its
``autoRetryEnabled`` getter / ``setAutoRetryEnabled`` setter ARE the setting
(``:3846-3856``), so rpc ``set_auto_retry`` writes the global settings file.

Before #408 the harness used module constants (3 retries, 2 s base) and its own
toggle whatever the settings said. The module constants stay the fallback for
a value the settings do not carry - read at decision time, so a test that
monkeypatches them still governs a harness whose settings are ``{}``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.settings import RetrySettings, Settings, SettingsManager
from aelix_coding_agent.rpc.rpc_mode import _handle_command, _handle_set_auto_retry
from aelix_coding_agent.rpc.rpc_types import RpcErrorResponse, RpcSuccessResponse


def _err() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="")], stop_reason="error", error_message="502 Bad Gateway"
    )


def _ok() -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")


class _Rig:
    """A harness whose every model attempt is scripted (default: a retryable 502)."""

    def __init__(self, sm: SettingsManager | None, plan: list[str] | None = None) -> None:
        self.harness = AgentHarness(
            AgentHarnessOptions(session=Session(MemorySessionStorage()), settings_manager=sm)
        )
        self.harness._state.auto_compaction_enabled = False
        self.events: list[Any] = []
        self.harness.subscribe(lambda ev: self.events.append(ev) or None)
        self.attempts = 0
        self.plan = list(plan or [])
        h = self.harness

        async def _fake_run(
            prompts: Any, *, system_prompt: Any = None, pending_inputs: Any = None
        ) -> list[Any]:
            self.attempts += 1
            h._state.messages.extend(prompts)
            answer = self.plan.pop(0) if self.plan else "502"
            h._state.messages.append(_ok() if answer == "ok" else _err())
            return list(h._state.messages)

        h._run = _fake_run  # type: ignore[method-assign]

    @property
    def starts(self) -> list[tuple[int, int, int]]:
        return [
            (e.attempt, e.max_attempts, e.delay_ms)
            for e in self.events
            if isinstance(e, AutoRetryStartEvent)
        ]

    @property
    def ends(self) -> list[tuple[bool, int]]:
        return [(e.success, e.attempt) for e in self.events if isinstance(e, AutoRetryEndEvent)]

    def reset(self) -> None:
        self.events.clear()
        self.attempts = 0


@pytest.fixture
def fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fallback that never sleeps for real: a row whose settings DO apply
    shows its own delays, and one where they did not would show these."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)


async def test_retry_enabled_false_ends_the_turn_on_the_first_failure(fail_fast: None) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": {"enabled": False}}))
    await rig.harness.prompt("hi")
    assert rig.attempts == 1
    assert rig.starts == [] and rig.ends == []
    assert rig.harness.auto_retry_enabled is False


async def test_max_retries_and_base_delay_come_from_settings(fail_fast: None) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 1, "baseDelayMs": 7}}))
    await rig.harness.prompt("hi")
    assert rig.attempts == 2  # the first attempt and ONE retry: maxRetries counts retries
    assert rig.starts == [(1, 1, 7)]
    assert rig.ends == [(False, 1)]


async def test_max_retries_zero_sends_once_and_emits_no_retry_event(fail_fast: None) -> None:
    """pi emits nothing here: its final ``auto_retry_end`` needs ``_retryAttempt > 0``."""

    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 0}}))
    await rig.harness.prompt("hi")
    assert rig.attempts == 1
    assert rig.starts == [] and rig.ends == []
    assert rig.harness._retry_attempt == 0


async def test_backoff_doubles_from_the_settings_base() -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": {"baseDelayMs": 2}}))
    await rig.harness.prompt("hi")
    # maxRetries is not set: the fallback (3) applies, the base is the setting's.
    assert rig.starts == [(1, 3, 2), (2, 3, 4), (3, 3, 8)]
    assert rig.attempts == 4


async def test_max_agent_delay_caps_the_backoff() -> None:
    """pi ``retryDelayMs``: ``min(base * 2^(n-1), maxAgentDelayMs)``."""

    rig = _Rig(
        SettingsManager.in_memory(
            {"retry": {"maxRetries": 4, "baseDelayMs": 3, "maxAgentDelayMs": 10}}
        )
    )
    await rig.harness.prompt("hi")
    assert [d for _, _, d in rig.starts] == [3, 6, 10, 10]


def test_the_default_cap_is_pis_sixty_seconds() -> None:
    policy = core_mod._auto_retry_policy(SettingsManager.in_memory({"retry": {"maxRetries": 9}}))
    assert [policy.delay_ms(n) for n in range(1, 10)] == [
        2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000, 60000,
    ]


async def test_unset_values_fall_back_to_the_module_constants_read_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings ``{}`` (what CLI tests write): the constants - as patched NOW - govern."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_MAX_DELAY_MS", 1)
    rig = _Rig(SettingsManager.in_memory({}))
    await rig.harness.prompt("hi")
    assert rig.starts == [(1, 2, 1), (2, 2, 1)]
    assert rig.attempts == 3


async def test_settings_are_read_at_each_retry_decision(fail_fast: None) -> None:
    """A settings change between prompts (``/reload``, a setter) applies to the next retry."""

    sm = SettingsManager.in_memory({"retry": {"maxRetries": 1, "baseDelayMs": 1}})
    rig = _Rig(sm)
    await rig.harness.prompt("one")
    assert rig.attempts == 2
    rig.reset()
    sm.apply_overrides(Settings(retry=RetrySettings(max_retries=2, base_delay_ms=3)))
    await rig.harness.prompt("two")
    assert rig.attempts == 3
    assert rig.starts == [(1, 2, 3), (2, 2, 6)]
    rig.reset()
    sm.apply_overrides(Settings(retry=RetrySettings(enabled=False)))
    await rig.harness.prompt("three")
    assert rig.attempts == 1 and rig.starts == []


async def test_disabled_during_a_sequence_closes_it_once(fail_fast: None) -> None:
    """A retry.enabled that turns false mid-sequence stops it and the #147 arm closes it."""

    sm = SettingsManager.in_memory({"retry": {"maxRetries": 3, "baseDelayMs": 1}})
    rig = _Rig(sm)

    def _disable(ev: Any) -> None:
        if isinstance(ev, AutoRetryStartEvent):
            sm.apply_overrides(Settings(retry=RetrySettings(enabled=False)))

    rig.harness.subscribe(_disable)
    await rig.harness.prompt("hi")
    assert rig.attempts == 2
    assert rig.starts == [(1, 3, 1)]
    assert rig.ends == [(False, 1)]
    assert rig.harness._retry_attempt == 0


async def test_a_retry_that_succeeds_still_reports_success(fail_fast: None) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 5, "baseDelayMs": 1}}), ["502", "ok"])
    await rig.harness.prompt("hi")
    assert rig.attempts == 2
    assert rig.starts == [(1, 5, 1)]
    assert rig.ends == [(True, 1)]


@pytest.mark.parametrize(
    "retry",
    [
        {"maxRetries": "1", "baseDelayMs": "1"},
        {"maxRetries": True, "baseDelayMs": False},
        {"maxRetries": None, "baseDelayMs": None, "maxAgentDelayMs": None},
        {"maxRetries": [1], "baseDelayMs": {"x": 1}},
        {"enabled": "false"},
        {"enabled": 0},
    ],
    ids=["strings", "booleans", "nulls", "containers", "enabled-string", "enabled-zero"],
)
async def test_a_value_of_the_wrong_type_is_ignored_not_raised(
    fail_fast: None, retry: dict[str, Any]
) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": retry}))
    await rig.harness.prompt("hi")
    assert rig.starts == [(1, 3, 1), (2, 3, 2), (3, 3, 4)]
    assert rig.harness.auto_retry_enabled is True


@pytest.mark.parametrize("retry", [True, "off", 3, ["enabled"]])
async def test_a_retry_entry_that_is_not_an_object_is_ignored(
    fail_fast: None, retry: object
) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": retry}))
    await rig.harness.prompt("hi")
    assert len(rig.starts) == 3
    assert rig.harness.auto_retry_enabled is True


def test_non_finite_and_negative_numbers() -> None:
    policy = core_mod._auto_retry_policy(
        SettingsManager.in_memory(
            {"retry": {"maxRetries": -2, "baseDelayMs": -50, "maxAgentDelayMs": float("inf")}}
        )
    )
    # Negative counts as 0 (what pi ends up doing); infinity is ignored (fallback cap).
    assert policy.max_retries == 0
    assert policy.delay_ms(1) == 0
    assert policy.max_delay_ms == 60_000
    nan = core_mod._auto_retry_policy(
        SettingsManager.in_memory({"retry": {"baseDelayMs": float("nan")}})
    )
    assert nan.delay_ms(1) == 2000


async def test_a_fractional_max_retries_counts_like_pis_comparison(fail_fast: None) -> None:
    """pi stops at ``attempt > 1.5``, i.e. after one retry; the event shows ``1``."""

    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 1.5, "baseDelayMs": 1}}))
    await rig.harness.prompt("hi")
    assert rig.attempts == 2
    assert rig.starts == [(1, 1, 1)]


async def test_settings_enabled_wins_over_the_harness_toggle(fail_fast: None) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": {"enabled": True, "maxRetries": 1}}))
    rig.harness._state.auto_retry_enabled = False
    assert rig.harness.auto_retry_enabled is True
    await rig.harness.prompt("hi")
    assert rig.attempts == 2


async def test_without_a_settings_manager_the_toggle_and_constants_govern(fail_fast: None) -> None:
    rig = _Rig(None)
    assert rig.harness.auto_retry_enabled is True
    rig.harness.set_auto_retry_enabled(False)
    assert rig.harness.auto_retry_enabled is False
    await rig.harness.prompt("hi")
    assert rig.attempts == 1
    rig.reset()
    rig.harness.set_auto_retry_enabled(True)
    await rig.harness.prompt("again")
    assert rig.starts == [(1, 3, 1), (2, 3, 2), (3, 3, 4)]


async def test_set_auto_retry_writes_the_global_setting(fail_fast: None) -> None:
    """pi ``setAutoRetryEnabled`` is ``settingsManager.setRetryEnabled``."""

    sm = SettingsManager.in_memory({"retry": {"enabled": False, "maxRetries": 1}})
    rig = _Rig(sm)
    assert rig.harness.auto_retry_enabled is False
    rig.harness.set_auto_retry_enabled(True)
    global_retry = sm.get_global_settings().retry
    assert global_retry is not None and global_retry.enabled is True
    assert sm.get_retry_enabled() is True
    assert rig.harness.auto_retry_enabled is True
    await rig.harness.prompt("hi")
    assert rig.attempts == 2


async def test_set_auto_retry_persists_into_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write reaches ``settings.json`` and keeps the block's other keys."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    path = agent / "settings.json"
    path.write_text(
        json.dumps({"retry": {"enabled": False, "baseDelayMs": 5, "maxAgentDelayMs": 9}}), "utf-8"
    )
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=False)
    harness = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), settings_manager=sm)
    )
    assert harness.auto_retry_enabled is False
    harness.set_auto_retry_enabled(True)
    await sm.flush()
    assert json.loads(path.read_text("utf-8")) == {
        "retry": {"enabled": True, "baseDelayMs": 5, "maxAgentDelayMs": 9}
    }


async def test_a_project_setting_still_wins_after_set_auto_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As in pi: the setter writes the GLOBAL file; a project ``retry.enabled`` stays in force."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    (cwd / ".aelix").mkdir(parents=True)
    (cwd / ".aelix" / "settings.json").write_text(json.dumps({"retry": {"enabled": False}}), "utf-8")
    path = agent / "settings.json"
    path.write_text("{}", "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=True)
    harness = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), settings_manager=sm)
    )
    assert harness.auto_retry_enabled is False
    harness.set_auto_retry_enabled(True)
    await sm.flush()
    assert json.loads(path.read_text("utf-8")) == {"retry": {"enabled": True}}
    assert harness.auto_retry_enabled is False


def test_get_retry_settings_carries_max_agent_delay() -> None:
    assert SettingsManager.in_memory({}).get_retry_settings()["maxAgentDelayMs"] == 60000
    sm = SettingsManager.in_memory({"retry": {"maxAgentDelayMs": 1234}})
    assert sm.get_retry_settings()["maxAgentDelayMs"] == 1234


async def test_an_edited_file_applies_after_a_settings_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_fast: None
) -> None:
    """``/reload`` (``AgentSessionRuntime.reload``) calls ``settings_manager.reload()``;
    the next retry decision then sees the edited file."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    path = agent / "settings.json"
    path.write_text(json.dumps({"retry": {"maxRetries": 1, "baseDelayMs": 1}}), "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=False)
    rig = _Rig(sm)
    await rig.harness.prompt("one")
    assert rig.attempts == 2
    path.write_text(json.dumps({"retry": {"enabled": False}}), "utf-8")
    rig.reset()
    await rig.harness.prompt("two")
    assert rig.attempts == 2  # not re-read until a reload, as in pi
    await sm.reload()
    rig.reset()
    await rig.harness.prompt("three")
    assert rig.attempts == 1 and rig.starts == []


# === #408 review round 2 =======================================================


def _cancel_ends(rig: _Rig) -> list[tuple[bool, int, str | None]]:
    return [
        (e.success, e.attempt, e.final_error)
        for e in rig.events
        if isinstance(e, AutoRetryEndEvent)
    ]


_ZERO_WAITS = [
    {"baseDelayMs": 0},
    {"maxAgentDelayMs": 0},
    {"baseDelayMs": -5},
    {"maxAgentDelayMs": -5},
]
_ZERO_IDS = ["base-0", "cap-0", "base-negative", "cap-negative"]


@pytest.mark.parametrize("retry", _ZERO_WAITS, ids=_ZERO_IDS)
@pytest.mark.parametrize(
    "how", ["abort_retry", "await_abort", "abort_retry_scheduled", "abort_flag"]
)
async def test_an_abort_at_a_zero_ms_wait_ends_the_retry(
    retry: dict[str, Any], how: str
) -> None:
    """Review round 2, item 2: ``wait_for(event.wait(), timeout=0)`` raised
    TimeoutError even with the event set, so an abort made while
    ``auto_retry_start`` was emitted was lost at a 0 ms wait and the turn was
    sent again (base 0: 3 attempts; base 1: 1). Each way to abort ends it now:
    ``abort_retry()`` and ``await abort()`` in the subscriber, an
    ``abort_retry()`` that lands inside the wait, and the turn's abort flag
    alone (an extension's ``ctx.abort()``; pi re-checks it after
    ``_prepareRetry``, ``agent-session.ts:1954-1957``)."""

    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 2, **retry}}))
    h = rig.harness

    async def _abort(ev: Any) -> None:
        if not isinstance(ev, AutoRetryStartEvent):
            return
        if how == "abort_retry":
            h.abort_retry()
        elif how == "await_abort":
            await h.abort()
        elif how == "abort_retry_scheduled":
            asyncio.get_running_loop().call_soon(h.abort_retry)
        else:
            h._mark_abort()

    h.subscribe(_abort)
    await h.prompt("hi")
    assert rig.attempts == 1
    assert rig.starts == [(1, 2, 0)]
    assert _cancel_ends(rig) == [(False, 1, "Retry cancelled")]
    assert h._retry_attempt == 0


@pytest.mark.parametrize("retry", _ZERO_WAITS, ids=_ZERO_IDS)
async def test_a_zero_ms_wait_without_an_abort_retries_at_once(retry: dict[str, Any]) -> None:
    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 2, **retry}}))
    await rig.harness.prompt("hi")
    assert rig.attempts == 3
    assert rig.starts == [(1, 2, 0), (2, 2, 0)]
    assert _cancel_ends(rig) == [(False, 2, "502 Bad Gateway")]


async def test_the_turns_abort_flag_ends_a_timed_retry_too() -> None:
    """The flag alone (no ``abort_retry``) at a 1 ms wait: pi's re-check after the wait."""

    rig = _Rig(SettingsManager.in_memory({"retry": {"maxRetries": 2, "baseDelayMs": 1}}))
    h = rig.harness
    h.subscribe(lambda ev: h._mark_abort() if isinstance(ev, AutoRetryStartEvent) else None)
    await h.prompt("hi")
    assert rig.attempts == 1
    assert _cancel_ends(rig) == [(False, 1, "Retry cancelled")]


@pytest.mark.parametrize(
    ("retry", "delays"),
    [
        ({"baseDelayMs": 1.5}, [2, 3, 6]),
        ({"baseDelayMs": 2.5}, [2, 5, 10]),
        ({"baseDelayMs": 1.5, "maxAgentDelayMs": -5}, [0, 0, 0]),
        ({"baseDelayMs": 3, "maxAgentDelayMs": 4.5}, [3, 4, 4]),
    ],
    ids=["base-1.5", "base-2.5-half-even", "cap-negative", "cap-4.5"],
)
async def test_delays_are_whole_milliseconds_rounded_half_to_even(
    retry: dict[str, Any], delays: list[int]
) -> None:
    """Review round 2, item 5: pins the rounding the docstring and ADR declare
    (Python ``round``) and that a negative ``maxAgentDelayMs`` counts as 0."""

    rig = _Rig(SettingsManager.in_memory({"retry": retry}))
    await rig.harness.prompt("hi")
    got = [d for _, _, d in rig.starts]
    assert got == delays
    assert all(type(d) is int for d in got)


async def test_a_float_base_survives_more_than_1024_retries() -> None:
    """Review round 2, item 4: ``1.0 * 2**1024`` raised OverflowError inside the turn."""

    rig = _Rig(
        SettingsManager.in_memory(
            {"retry": {"maxRetries": 1100, "baseDelayMs": 1.0, "maxAgentDelayMs": 0}}
        )
    )
    await rig.harness.prompt("hi")
    assert rig.attempts == 1101
    assert _cancel_ends(rig) == [(False, 1100, "502 Bad Gateway")]


def test_a_delay_too_large_for_a_float_is_the_cap() -> None:
    def delays(retry: dict[str, Any], *attempts: int) -> list[int]:
        policy = core_mod._auto_retry_policy(SettingsManager.in_memory({"retry": retry}))
        return [policy.delay_ms(n) for n in attempts]

    assert delays({"baseDelayMs": 1.0, "maxAgentDelayMs": 7}, 1025, 5000) == [7, 7]
    assert delays({"baseDelayMs": 10**400}, 1) == [60_000]
    # pi's ``retryDelayMs`` never returns more than Number.MAX_SAFE_INTEGER.
    assert delays({"baseDelayMs": 1e300, "maxAgentDelayMs": 10**400}, 1, 2000) == [
        2**53 - 1,
        2**53 - 1,
    ]


_NOT_OBJECTS = [False, True, "off", [], 3]


@pytest.mark.parametrize("shape", _NOT_OBJECTS, ids=["false", "true", "string", "list", "number"])
async def test_rpc_set_auto_retry_replaces_a_retry_that_is_not_an_object(
    fail_fast: None, shape: object
) -> None:
    """Review round 2, item 3: ``{"retry": false}`` made ``set_retry_enabled`` raise
    AttributeError, rpc answered ``success: false``, and the toggle had flipped.
    The write now replaces the value with ``{"enabled": ...}``, as the read path
    already ignored it; the reply and the state agree."""

    sm = SettingsManager.in_memory({"retry": shape})
    rig = _Rig(sm)
    assert rig.harness.auto_retry_enabled is True
    response = await _handle_command(
        rig.harness,
        {"type": "set_auto_retry", "enabled": False, "id": "r"},
        {"set_auto_retry": _handle_set_auto_retry},
    )
    assert isinstance(response, RpcSuccessResponse)
    assert rig.harness.auto_retry_enabled is False
    assert sm.get_global_settings().retry == RetrySettings(enabled=False)
    await rig.harness.prompt("hi")
    assert rig.attempts == 1


@pytest.mark.parametrize("shape", _NOT_OBJECTS, ids=["false", "true", "string", "list", "number"])
async def test_set_auto_retry_rewrites_a_non_object_retry_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: object
) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    path = agent / "settings.json"
    path.write_text(json.dumps({"retry": shape, "theme": "dark"}), "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=False)
    harness = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), settings_manager=sm)
    )
    harness.set_auto_retry_enabled(False)
    await sm.flush()
    assert sm.drain_errors() == []
    assert json.loads(path.read_text("utf-8")) == {"retry": {"enabled": False}, "theme": "dark"}
    assert harness.auto_retry_enabled is False


async def test_a_failed_settings_write_leaves_the_toggle_where_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever makes the write raise, rpc's ``success: false`` is true: nothing moved."""

    sm = SettingsManager.in_memory({})
    rig = _Rig(sm)

    def _refuse(enabled: bool) -> None:
        raise OSError("settings write refused")

    monkeypatch.setattr(sm, "set_retry_enabled", _refuse)
    response = await _handle_command(
        rig.harness,
        {"type": "set_auto_retry", "enabled": False, "id": "r"},
        {"set_auto_retry": _handle_set_auto_retry},
    )
    assert isinstance(response, RpcErrorResponse)
    assert response.error == "settings write refused"
    assert rig.harness.state.auto_retry_enabled is True
    assert rig.harness.auto_retry_enabled is True


# === #408 review round 3 =======================================================


def _always_retryable(calls: list[int]) -> Any:
    from aelix_ai.streaming import AssistantErrorEvent, AssistantStartEvent

    async def stream_fn(model: Any, context: Any, options: Any = None) -> Any:
        calls.append(1)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        failed = AssistantMessage(content=[], stop_reason="error", error_message="rate limit exceeded")
        yield AssistantErrorEvent(reason="error", error=failed, error_message="rate limit exceeded")

    return stream_fn


@pytest.mark.parametrize("base_delay_ms", [300, 60_000], ids=["300ms", "60s"])
async def test_an_extensions_ctx_abort_during_the_wait_ends_it_at_once(base_delay_ms: int) -> None:
    """Review round 3, item 1: ``ctx.abort()`` set only the turn's abort flag,
    which was read after the wait, so the retry was cancelled only when the
    whole wait had run (60 s here: the turn did not end within 10 s). In pi
    ``ctx.abort()`` is ``this.abort()``, whose ``abortRetry()`` wakes the sleep.
    The abort is scheduled 100 ms after ``auto_retry_start`` - inside the wait,
    on the loop's own clock, so it lands before the wait's timeout at any load."""

    calls: list[int] = []
    sm = SettingsManager.in_memory({"retry": {"maxRetries": 2, "baseDelayMs": base_delay_ms}})
    h = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()),
            stream_fn=_always_retryable(calls),
            settings_manager=sm,
        )
    )
    h._state.auto_compaction_enabled = False
    events: list[Any] = []
    h.subscribe(lambda ev: events.append(ev) or None)
    ctx_box: list[Any] = []

    async def _keep_ctx(_event: Any, ctx: Any) -> None:
        if not ctx_box:
            ctx_box.append(ctx)

    h.hooks.on("turn_end", _keep_ctx)  # type: ignore[call-overload]

    def _abort_later(ev: Any) -> None:
        if isinstance(ev, AutoRetryStartEvent):
            asyncio.get_running_loop().call_later(0.1, ctx_box[0].abort)

    h.subscribe(_abort_later)
    await asyncio.wait_for(h.prompt("hi"), timeout=10)
    assert len(calls) == 1
    assert [(e.attempt, e.delay_ms) for e in events if isinstance(e, AutoRetryStartEvent)] == [
        (1, base_delay_ms)
    ]
    assert [
        (e.success, e.attempt, e.final_error) for e in events if isinstance(e, AutoRetryEndEvent)
    ] == [(False, 1, "Retry cancelled")]


async def test_set_auto_retry_false_holds_over_a_trusted_projects_non_object_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_fast: None
) -> None:
    """Review round 3, item 2: a trusted project's ``{"retry": false}`` replaces
    the global block in the merged view, so the global ``retry.enabled`` that
    ``set_auto_retry`` writes does not reach it; the harness's own toggle is what
    answers ``get_state`` and stops the retry. pi's setter writes the setting
    only - that version here leaves get_state True and the turn retrying."""

    agent = tmp_path / "agent"
    agent.mkdir()
    cwd = tmp_path / "cwd"
    (cwd / ".aelix").mkdir(parents=True)
    path = agent / "settings.json"
    path.write_text("{}", "utf-8")
    (cwd / ".aelix" / "settings.json").write_text(json.dumps({"retry": False}), "utf-8")
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path))
    sm = SettingsManager.create(cwd, agent, project_trusted=True)
    rig = _Rig(sm)
    assert rig.harness.auto_retry_enabled is True
    response = await _handle_command(
        rig.harness,
        {"type": "set_auto_retry", "enabled": False, "id": "r"},
        {"set_auto_retry": _handle_set_auto_retry},
    )
    await sm.flush()
    assert isinstance(response, RpcSuccessResponse)
    assert json.loads(path.read_text("utf-8")) == {"retry": {"enabled": False}}
    assert rig.harness.auto_retry_enabled is False
    await rig.harness.prompt("hi")
    assert rig.attempts == 1
    assert rig.starts == []


# === #408 review round 4 =======================================================


@pytest.mark.parametrize("base_delay_ms", [3000, 60_000], ids=["3s", "60s"])
async def test_an_extensions_ctx_abort_while_auto_retry_start_is_delivered_ends_it_at_once(
    base_delay_ms: int,
) -> None:
    """Review round 4, item 1: a ``ctx.abort()`` made while an async subscriber
    still holds the ``auto_retry_start`` emit lands before the retry's abort
    event exists, so it sets only the turn's abort flag - which was read only
    after the whole wait (3 s here: the turn returned 3.04 s after the abort,
    1 call). The flag is read before the wait now as well, as ``abort()`` and
    ``abort_retry()`` in the same window already ended it (#334). pi's emit is
    sync, so a listener's abort there sleeps the full delay: this goes beyond it.
    Measured on the loop's clock from the abort; the 60 s row is bounded by
    ``wait_for`` 10 s."""

    calls: list[int] = []
    sm = SettingsManager.in_memory({"retry": {"maxRetries": 2, "baseDelayMs": base_delay_ms}})
    h = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()),
            stream_fn=_always_retryable(calls),
            settings_manager=sm,
        )
    )
    h._state.auto_compaction_enabled = False
    events: list[Any] = []
    h.subscribe(lambda ev: events.append(ev) or None)
    ctx_box: list[Any] = []
    aborted_at: list[float] = []
    event_at_abort: list[object] = []

    async def _keep_ctx(_event: Any, ctx: Any) -> None:
        if not ctx_box:
            ctx_box.append(ctx)

    h.hooks.on("turn_end", _keep_ctx)  # type: ignore[call-overload]
    loop = asyncio.get_running_loop()

    async def _abort_while_delivered(ev: Any) -> None:
        if isinstance(ev, AutoRetryStartEvent) and not aborted_at:
            event_at_abort.append(h._retry_abort_event)
            aborted_at.append(loop.time())
            ctx_box[0].abort()
            await asyncio.sleep(0.05)

    h.subscribe(_abort_while_delivered)
    await asyncio.wait_for(h.prompt("hi"), timeout=10)
    returned_after = loop.time() - aborted_at[0]
    assert event_at_abort == [None]
    assert len(calls) == 1
    assert [(e.attempt, e.delay_ms) for e in events if isinstance(e, AutoRetryStartEvent)] == [
        (1, base_delay_ms)
    ]
    assert [
        (e.success, e.attempt, e.final_error) for e in events if isinstance(e, AutoRetryEndEvent)
    ] == [(False, 1, "Retry cancelled")]
    assert returned_after < 1.5
