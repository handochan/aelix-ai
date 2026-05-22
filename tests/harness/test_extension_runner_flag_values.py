"""Sprint 6h₇c §C (Phase 5a-iii-γ, ADR-0093) — ``flag_values`` primitive tests.

Pi parity: ``runner.ts:409-411`` (P-447).

Verifies:

- :attr:`_ExtensionRuntime.flag_values` defaults to empty dict.
- :meth:`_ExtensionRuntime.get_flag_values` returns a shallow copy
  (mutating the returned dict does NOT affect the runtime).
- :meth:`_ExtensionRuntime.set_flag_value` mutates the internal dict
  (last-write-wins on the same name).
- :class:`ExtensionRunner` delegates correctly to the runtime; when no
  runtime bridge is wired, the getter returns ``{}`` and the setter
  silently drops the mutation.
"""

from __future__ import annotations

from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_coding_agent.extensions.api import _ExtensionRuntime


def test_runtime_flag_values_default_empty() -> None:
    """Fresh runtime starts with an empty flag-values dict."""

    runtime = _ExtensionRuntime()

    assert runtime.flag_values == {}
    assert runtime.get_flag_values() == {}


def test_set_flag_value_mutates_internal_dict() -> None:
    """``set_flag_value`` persists into ``flag_values``."""

    runtime = _ExtensionRuntime()
    runtime.set_flag_value("verbose", True)
    runtime.set_flag_value("model", "anthropic/claude-3-5-sonnet")

    assert runtime.flag_values == {
        "verbose": True,
        "model": "anthropic/claude-3-5-sonnet",
    }
    assert runtime.get_flag_values() == {
        "verbose": True,
        "model": "anthropic/claude-3-5-sonnet",
    }


def test_set_flag_value_last_write_wins() -> None:
    """Re-setting an existing flag overwrites the previous value."""

    runtime = _ExtensionRuntime()
    runtime.set_flag_value("verbose", True)
    runtime.set_flag_value("verbose", False)

    assert runtime.flag_values == {"verbose": False}


def test_get_flag_values_returns_shallow_copy() -> None:
    """Mutating the returned dict MUST NOT affect the runtime (Pi parity).

    Pi ``runner.ts:409`` uses ``new Map(this.flagValues)`` (shallow
    copy). Aelix returns ``dict(self.flag_values)`` — same semantic.
    """

    runtime = _ExtensionRuntime()
    runtime.set_flag_value("verbose", True)

    snapshot = runtime.get_flag_values()
    snapshot["verbose"] = False
    snapshot["new_key"] = "leaked"

    # Internal state unchanged by the snapshot mutation.
    assert runtime.flag_values == {"verbose": True}
    assert runtime.get_flag_values() == {"verbose": True}


def test_extension_runner_delegates_get_flag_values() -> None:
    """``ExtensionRunner.get_flag_values`` delegates to the wired runtime."""

    runtime = _ExtensionRuntime()
    runtime.set_flag_value("debug", True)

    runner = ExtensionRunner(_runtime=runtime)

    assert runner.get_flag_values() == {"debug": True}


def test_extension_runner_delegates_set_flag_value() -> None:
    """``ExtensionRunner.set_flag_value`` mutates the bound runtime."""

    runtime = _ExtensionRuntime()
    runner = ExtensionRunner(_runtime=runtime)

    runner.set_flag_value("flag", "value")

    assert runtime.flag_values == {"flag": "value"}
    assert runner.get_flag_values() == {"flag": "value"}


def test_extension_runner_get_flag_values_unwired_returns_empty() -> None:
    """When no runtime bridge is wired, getter returns ``{}`` (safe default).

    Matches the Sprint 6h₅a ``_emit`` / ``_has_handlers`` no-op pattern.
    """

    runner = ExtensionRunner()  # No runtime bound.

    assert runner.get_flag_values() == {}


def test_extension_runner_set_flag_value_unwired_is_noop() -> None:
    """When no runtime bridge is wired, setter silently drops the mutation.

    Matches the Sprint 6h₅a ``emit`` unwired no-op pattern.
    """

    runner = ExtensionRunner()  # No runtime bound.

    # MUST NOT raise; subsequent get must still return empty.
    runner.set_flag_value("flag", True)

    assert runner.get_flag_values() == {}


def test_extension_runner_get_flag_values_returns_shallow_copy() -> None:
    """End-to-end shallow-copy semantic through the ExtensionRunner facade."""

    runtime = _ExtensionRuntime()
    runtime.set_flag_value("k1", "v1")
    runner = ExtensionRunner(_runtime=runtime)

    snapshot = runner.get_flag_values()
    snapshot["k1"] = "tampered"
    snapshot["k2"] = "added"

    # Runtime untouched.
    assert runtime.flag_values == {"k1": "v1"}
    assert runner.get_flag_values() == {"k1": "v1"}
