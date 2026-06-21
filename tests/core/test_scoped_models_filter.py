"""enabled_models enforcement tests (ADR-0162).

Proves :func:`aelix_coding_agent.core.scoped_models_filter.scoped_available`:

* ``None`` allow-list (sentinel) → identical to ``get_available()``.
* ``[]`` (empty list) → treated as all (no lockout), distinct from None.
* concrete exact ids → exactly those, in ``get_available()`` ORDER (projection).
* glob ``"openai/*"`` → only openai (``*`` does not cross ``/``).
* concrete list matching ZERO available → full list + warn() exactly once.
* ``settings_manager=None`` → full list (defensive).
* LIVENESS: set_enabled_models([...]) then the NEXT call reflects it (read at
  call time, no startup snapshot).
"""

from __future__ import annotations

from typing import Any

from aelix_ai.streaming import Model
from aelix_coding_agent.core.scoped_models_filter import scoped_available


class _FakeRegistry:
    """Duck-typed ModelRegistry: only ``get_available()`` is consulted.

    ``resolve_model_scope`` reads ``get_available()`` to match patterns, so this
    double is sufficient to exercise the full chain (no AuthStorage needed).
    """

    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def get_available(self) -> list[Model]:
        return list(self._models)


class _LiveSettings:
    """A SettingsManager double whose allow-list can mutate at runtime."""

    def __init__(self, patterns: list[str] | None) -> None:
        self._patterns = patterns

    def get_enabled_models(self) -> list[str] | None:
        return None if self._patterns is None else list(self._patterns)

    def set_enabled_models(self, patterns: list[str] | None) -> None:
        self._patterns = patterns


def _catalog() -> list[Model]:
    # Insertion order is the canonical order the picker / cycle assume.
    return [
        Model(id="gpt-4o", provider="openai"),
        Model(id="gpt-4o-mini", provider="openai"),
        Model(id="claude-opus", provider="anthropic"),
        Model(id="glm-4.5", provider="openrouter"),
    ]


async def test_none_patterns_is_all_enabled_sentinel() -> None:
    reg = _FakeRegistry(_catalog())
    out = await scoped_available(reg, _LiveSettings(None))
    assert [m.id for m in out] == [m.id for m in _catalog()]


async def test_empty_list_is_all_no_lockout() -> None:
    # patterns=[] is DISTINCT from None but degrades to the same all-enabled
    # outcome (no lockout). Pinned explicitly.
    reg = _FakeRegistry(_catalog())
    out = await scoped_available(reg, _LiveSettings([]))
    assert [m.id for m in out] == [m.id for m in _catalog()]


async def test_concrete_subset_returns_those_in_get_available_order() -> None:
    reg = _FakeRegistry(_catalog())
    # Allow-list given in a DIFFERENT order than the catalog — the result must
    # follow get_available() insertion order, not pattern order.
    out = await scoped_available(
        reg, _LiveSettings(["anthropic/claude-opus", "openai/gpt-4o"])
    )
    assert [(m.provider, m.id) for m in out] == [
        ("openai", "gpt-4o"),
        ("anthropic", "claude-opus"),
    ]


async def test_glob_pattern_scopes_to_provider_no_cross_slash() -> None:
    reg = _FakeRegistry(_catalog())
    out = await scoped_available(reg, _LiveSettings(["openai/*"]))
    assert {m.provider for m in out} == {"openai"}
    assert {m.id for m in out} == {"gpt-4o", "gpt-4o-mini"}


async def test_zero_match_concrete_list_degrades_to_all_and_warns() -> None:
    reg = _FakeRegistry(_catalog())
    warnings: list[str] = []
    out = await scoped_available(
        reg, _LiveSettings(["does-not-exist/nope"]), warn=warnings.append
    )
    # Lockout guard: full list back, warn fired EXACTLY once.
    assert [m.id for m in out] == [m.id for m in _catalog()]
    assert len(warnings) == 1
    assert "matched no available models" in warnings[0]


async def test_settings_manager_none_returns_full_list() -> None:
    reg = _FakeRegistry(_catalog())
    out = await scoped_available(reg, None)
    assert [m.id for m in out] == [m.id for m in _catalog()]


async def test_settings_read_exception_degrades_to_full() -> None:
    class _Boom:
        def get_enabled_models(self) -> Any:
            raise RuntimeError("settings unreadable")

    reg = _FakeRegistry(_catalog())
    out = await scoped_available(reg, _Boom())
    assert [m.id for m in out] == [m.id for m in _catalog()]


async def test_liveness_reflects_runtime_change_without_rebuild() -> None:
    reg = _FakeRegistry(_catalog())
    settings = _LiveSettings(None)

    # First call: all enabled.
    first = await scoped_available(reg, settings)
    assert len(first) == len(_catalog())

    # User runs /scoped-models → set a 1-model allow-list at runtime.
    settings.set_enabled_models(["openai/gpt-4o-mini"])

    # The SAME registry + SAME settings instance now reflects the new list on
    # the very next call (read-at-call-time, no startup snapshot, no rebuild).
    second = await scoped_available(reg, settings)
    assert [(m.provider, m.id) for m in second] == [("openai", "gpt-4o-mini")]
