"""Issue #22 — register_provider bootstrap replay into the real ModelRegistry.

The extension API ``register_provider`` queues registrations onto
``_ExtensionRuntime.pending_provider_registrations`` while extensions are
loading. Before #22 those pending registrations were NEVER replayed into the
live :class:`aelix_coding_agent.model_registry.ModelRegistry`
(``_ExtensionRuntime.bind_model_registry`` existed but had no caller), so an
extension/custom-registered provider silently never resolved in ``/model`` or
at stream time.

Pi parity: ``ExtensionRunner.bindCore`` flushes
``runtime.pendingProviderRegistrations`` into ``modelRegistry``
(``packages/coding-agent/src/core/extensions/runner.ts:344-377`` @
927e98068cda276bf9188f4774fb927c89823388). Aelix performs the bind at the
single bootstrap point in ``entry._harness_factory`` (the harness is built
there for every mode and on each rebuild).

Two layers are covered:
  1. the replay CONTRACT — ``bind_model_registry`` makes a pending
     registration resolvable (was a no-op before the bind);
  2. the WIRING — a real ``_async_main`` launch actually performs the bind so
     the live registry threaded into ``run_tui`` carries the provider.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.entry import _async_main
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput


class _FakeTTYStdin:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover — never read on a TTY
        return ""


async def _ready_registry(tmp_path: Path) -> ModelRegistry:
    auth = AuthStorage(path=tmp_path / "auth.json")
    await auth.load()
    return ModelRegistry.create(auth)


# === Layer 1: the bind_model_registry replay contract ========================


async def test_bind_replays_pending_registration_into_real_registry(
    tmp_path: Path,
) -> None:
    """An extension that calls ``register_provider`` during setup becomes
    resolvable in the real registry ONLY after ``bind_model_registry`` runs.
    """

    def _factory(api: object) -> None:
        api.register_provider(  # type: ignore[attr-defined]
            "acme-cloud",
            ProviderConfigInput(name="Acme Cloud", api_key="sk-acme-test"),
        )

    loaded = await discover_and_load_extensions(
        [_factory], cwd=tmp_path, no_discovery=True
    )
    # The registration is QUEUED on the runtime but not in any real registry.
    assert any(
        n == "acme-cloud" for n, _ in loaded.runtime.pending_provider_registrations
    )

    registry = await _ready_registry(tmp_path)
    # No-op state (pre-#22): a registry never bound to the runtime is blind to
    # the provider — the display name falls back to the title-cased id and no
    # auth is configured for it.
    assert registry.get_provider_display_name("acme-cloud") == "Acme-Cloud"
    assert not registry.has_configured_auth(Model(provider="acme-cloud"))

    # Bootstrap bind (what entry._harness_factory now performs after building
    # the harness) flushes the queue into the live registry.
    loaded.runtime.bind_model_registry(registry)

    assert loaded.runtime.pending_provider_registrations == []  # drained
    # Resolvable now: the registered config name + api_key are live.
    assert registry.get_provider_display_name("acme-cloud") == "Acme Cloud"
    assert registry.has_configured_auth(Model(provider="acme-cloud"))
    # And the runtime now points at the REAL registry (no longer the stub), so
    # post-bind registrations also land immediately (pi "takes effect now").
    assert loaded.runtime.model_registry is registry


# === Layer 2: the entry._harness_factory wiring ==============================


async def test_async_main_bootstrap_binds_registry_for_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real ``_async_main`` launch flushes an ``-e`` extension's
    ``register_provider`` into the LIVE ModelRegistry threaded into ``run_tui``.

    Drives interactive mode (TTY stdin) with ``run_tui`` stubbed so the launch
    returns right after the harness is built + bound. The clean cwd makes
    project-trust auto-resolve (no prompt) and the isolated agent dir keeps real
    auth/settings out of the run.
    """

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())

    # An explicit (-e) extension that registers a custom provider during setup.
    ext = tmp_path / "prov_ext.py"
    ext.write_text(
        textwrap.dedent(
            """
            from aelix_coding_agent.model_registry import ProviderConfigInput

            def setup(api):
                api.register_provider(
                    "acme-cloud",
                    ProviderConfigInput(name="Acme Cloud", api_key="sk-acme-test"),
                )
            """
        ),
        encoding="utf-8",
    )

    seen: dict[str, object] = {}

    async def _stub_run_tui(
        runtime: object, *, model_registry: object = None, **_k: object
    ) -> int:
        seen["model_registry"] = model_registry
        seen["runtime_registry"] = runtime.harness.runtime.model_registry  # type: ignore[attr-defined]
        return 0

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)

    code = await _async_main(["--no-session", "-e", str(ext)])
    assert code == 0

    real_reg = seen["model_registry"]
    assert isinstance(real_reg, ModelRegistry)
    # The harness's runtime was bound to the SAME live registry threaded into
    # run_tui (proves _harness_factory called bind_model_registry).
    assert seen["runtime_registry"] is real_reg
    # The extension-registered provider is resolvable in that live registry —
    # this was a no-op before #22 (pending registrations never replayed).
    assert real_reg.get_provider_display_name("acme-cloud") == "Acme Cloud"
    assert real_reg.has_configured_auth(Model(provider="acme-cloud"))
