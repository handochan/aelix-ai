"""#240 — the headless seam that drops a cached ``models.json`` credential.

A ``!command`` credential is resolved once per registry load since #240, and the
canonical reason to use one is a SHORT-LIVED token (``!gcloud auth
print-access-token``, ~1 h). An ``aelix -p …`` run is one user turn but many API
requests over unbounded wall time, so it is exactly the surface where a frozen
token expires mid-run — and it has no ``/reload`` and no ``/login`` to recover
with. ADR-0140's first draft called print mode "one-turn" and left it to those
two commands; that was wrong about the request count and about the wall time.

So print mode gets the same recovery the TUI has, keyed on the same signal: the
TERMINAL assistant message, not a raised exception. ``harness.prompt`` does not
raise for a provider failure — the adapter converts it into an
``AssistantErrorEvent`` and the agent loop returns on it.

The helpers come from :mod:`tests.cli.test_print_mode`, whose mock ``stream_fn``
already produces each terminal shape.
"""

from __future__ import annotations

from typing import Any

from aelix_coding_agent.cli.entry import _async_main
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.cli.test_print_mode import (
    _aborted_stream,
    _error_stream,
    _new_harness,
    _new_runtime,
    _ok_stream,
)


class _CountingRegistry:
    """Counts :meth:`clear_config_value_cache` and nothing else.

    Deliberately NOT a ``ModelRegistry``: the seam is duck-typed
    (:func:`aelix_coding_agent.model_registry.clear_command_value_cache`)
    because ``run_print_mode``'s parameter is optional and an embedder can pass
    anything, and a stub is the only way to count calls without a real
    ``models.json`` on disk.
    """

    def __init__(self) -> None:
        self.cleared = 0

    def clear_config_value_cache(self) -> None:
        self.cleared += 1


async def test_a_headless_turn_that_errors_drops_the_cached_credential(
    capsys: Any,
) -> None:
    """The recovery a headless run has, and the only one it has.

    ``_error_stream`` terminates with ``stop_reason == "error"`` and
    ``run_print_mode`` returns 1 — the shape a 401 on a frozen helper token
    takes. Without the clear the next request in the run reuses the rejected
    value, and there is no ``/reload`` to fix it.
    """

    harness = _new_harness(_error_stream("401 Unauthorized"))
    registry = _CountingRegistry()
    exit_code = await run_print_mode(
        _new_runtime(harness),
        mode="text",
        messages=[],
        initial_message="ping",
        model_registry=registry,
    )
    capsys.readouterr()

    assert exit_code == 1
    assert registry.cleared == 1


async def test_each_residual_message_gets_its_own_recovery(capsys: Any) -> None:
    """One clear per errored turn, not one per run.

    ``aelix -p`` can carry several messages; leaving the residual loop out would
    spend every message after the first on a credential already known to be
    rejected.
    """

    harness = _new_harness(_error_stream("401 Unauthorized"))
    registry = _CountingRegistry()
    exit_code = await run_print_mode(
        _new_runtime(harness),
        mode="text",
        messages=["second"],
        initial_message="first",
        model_registry=registry,
    )
    capsys.readouterr()

    assert exit_code == 1
    assert registry.cleared == 2


async def test_a_successful_headless_run_keeps_the_cached_credential(
    capsys: Any,
) -> None:
    """The control — clearing unconditionally would undo the whole of #240."""

    harness = _new_harness(_ok_stream("hello"))
    registry = _CountingRegistry()
    exit_code = await run_print_mode(
        _new_runtime(harness),
        mode="text",
        messages=["second"],
        initial_message="first",
        model_registry=registry,
    )
    capsys.readouterr()

    assert exit_code == 0
    assert registry.cleared == 0


async def test_an_aborted_headless_run_keeps_the_cached_credential(
    capsys: Any,
) -> None:
    """An abort is a signal or a user, not a credential.

    ``run_print_mode`` still exits 1 for ``"aborted"`` (step 8 treats both
    terminal reasons alike), so the exit code cannot stand in for "the key was
    rejected" — which is why the clear reads ``stop_reason`` itself.
    """

    harness = _new_harness(_aborted_stream())
    registry = _CountingRegistry()
    exit_code = await run_print_mode(
        _new_runtime(harness),
        mode="text",
        messages=[],
        initial_message="ping",
        model_registry=registry,
    )
    capsys.readouterr()

    assert exit_code == 1
    assert registry.cleared == 0


async def test_no_registry_is_the_no_op_every_older_caller_relies_on(
    capsys: Any,
) -> None:
    """``model_registry`` is optional, and omitting it must not raise.

    Every ``run_print_mode`` call that predates #240 — and every embedder —
    passes nothing, so the errored path has to stay a clean exit 1.
    """

    harness = _new_harness(_error_stream("401 Unauthorized"))
    exit_code = await run_print_mode(
        _new_runtime(harness),
        mode="text",
        messages=[],
        initial_message="ping",
    )
    capsys.readouterr()

    assert exit_code == 1


async def test_the_cli_hands_print_mode_the_registry_its_auth_callback_uses(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The wiring, not just the seam.

    ``run_print_mode``'s recovery is worth nothing if ``_async_main`` hands it a
    DIFFERENT object from the one ``_make_auth_callback`` closed over: the clear
    would drop an empty dict while the live cache kept serving the rejected key,
    and every case above would still be green. So this drives the real CLI path
    with both ends captured and asserts IDENTITY.
    """

    import sys

    from aelix_coding_agent.cli import entry as entry_mod

    from tests.cli.test_api_key_auth import _FakePipedStdin
    from tests.env_sandbox import sandbox_home

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-T240-WIRING")

    auth_registry: list[Any] = []
    real_make = entry_mod._make_auth_callback

    def _spy_make(registry: Any) -> Any:
        auth_registry.append(registry)
        return real_make(registry)

    monkeypatch.setattr(entry_mod, "_make_auth_callback", _spy_make)

    seen: dict[str, Any] = {}

    async def _capture(_runtime: Any, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    import aelix_coding_agent.modes as modes_mod

    monkeypatch.setattr(modes_mod, "run_print_mode", _capture)

    code = await _async_main(
        ["--print", "--model", "anthropic/claude-3-5-haiku-latest", "hello"]
    )

    assert code == 0
    assert auth_registry, "the auth callback was never wired"
    assert seen["model_registry"] is auth_registry[0], seen
