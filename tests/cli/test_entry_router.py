"""Sprint 6h₆ (Phase 5a-i + 5a-ii, ADR-0089) — ``cli/entry.py`` tests.

Covers:
  - :func:`resolve_app_mode` decision table (Pi parity, main.ts:96-113).
  - :func:`to_print_output_mode` mapping.
  - ``--rpc`` + ``@file`` guard.
  - ``--version`` short-circuit.
  - ``--help`` short-circuit.
  - ``--list-models`` wired path (Sprint 6h₇a — was deferred-error in 6h₆).
  - Interactive mode dispatches to :func:`run_tui` (Sprint 6h₁₀a) +
    the missing-``[tui]``-extra graceful-degrade path.
  - Piped stdin → print mode promotion.
  - ``python -m aelix_coding_agent --version`` end-to-end smoke.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import (
    _async_main,
    resolve_app_mode,
    to_print_output_mode,
)

# === resolve_app_mode decision table (Pi main.ts:96-113) ====================


def test_resolve_rpc_explicit() -> None:
    args = Args(mode="rpc")
    assert resolve_app_mode(args, stdin_is_tty=True) == "rpc"


def test_resolve_rpc_overrides_print_flag() -> None:
    args = Args(mode="rpc", print_mode=True)
    assert resolve_app_mode(args, stdin_is_tty=False) == "rpc"


def test_resolve_json_explicit() -> None:
    args = Args(mode="json")
    assert resolve_app_mode(args, stdin_is_tty=True) == "json"


def test_resolve_json_overrides_print_flag() -> None:
    args = Args(mode="json", print_mode=True)
    assert resolve_app_mode(args, stdin_is_tty=False) == "json"


def test_resolve_print_flag() -> None:
    args = Args(print_mode=True)
    assert resolve_app_mode(args, stdin_is_tty=True) == "print"


def test_resolve_piped_stdin_promotes_to_print() -> None:
    args = Args()
    assert resolve_app_mode(args, stdin_is_tty=False) == "print"


def test_resolve_default_interactive() -> None:
    args = Args()
    assert resolve_app_mode(args, stdin_is_tty=True) == "interactive"


# === to_print_output_mode ====================================================


def test_to_print_output_mode_json() -> None:
    assert to_print_output_mode("json") == "json"


def test_to_print_output_mode_print_is_text() -> None:
    assert to_print_output_mode("print") == "text"


def test_to_print_output_mode_other_falls_back_text() -> None:
    # Defensive: any non-json mode maps to text.
    assert to_print_output_mode("rpc") == "text"
    assert to_print_output_mode("interactive") == "text"


# === --version short-circuit =================================================


async def test_version_prints_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--version"])
    captured = capsys.readouterr()
    assert code == 0
    # VERSION is non-empty (test_config asserts this).
    assert captured.out.strip()


# === --help short-circuit ====================================================


async def test_help_prints_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await _async_main(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "aelix" in captured.out.lower()
    assert "--help" in captured.out


# === --list-models wired (Sprint 6h₇a / ADR-0090) ============================


async def test_list_models_invokes_list_models_and_exits_0(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--list-models`` runs the real :func:`list_models` (no longer a
    deferred stderr diagnostic) and returns exit code 0.

    With an isolated empty ``AELIX_CODING_AGENT_DIR`` (no auth.json),
    the registry's :meth:`get_available` yields zero auth-configured
    models, so the path lands on the inline "No models available"
    fallback (NOT the deferred ``--list-models requires
    SettingsManager`` stderr diagnostic the 6h₆ scope emitted).
    """

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path))
    code = await _async_main(["--list-models"])
    captured = capsys.readouterr()
    assert code == 0
    # The deferred stderr diagnostic from 6h₆ MUST NOT appear.
    assert "SettingsManager" not in captured.err
    # Either the table header lands on stdout OR the inline fallback;
    # both are acceptable per ADR-0090 §C step 3.
    combined = captured.out + captured.err
    assert (
        "No models available" in combined
        or "provider" in combined
    )


# === Diagnostic-error short-circuit ==========================================


async def test_diagnostic_error_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A parse-time error diagnostic (e.g., --mode bogus) → exit 1."""

    code = await _async_main(["--mode", "bogus"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err
    assert "--mode" in captured.err


# === --rpc + @file guard =====================================================


async def test_rpc_plus_file_arg_returns_1(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    f = tmp_path / "foo.txt"
    f.write_text("hi")
    code = await _async_main(["--mode", "rpc", f"@{f}"])
    captured = capsys.readouterr()
    assert code == 1
    assert "rpc" in captured.err.lower()
    assert "@file" in captured.err or "file" in captured.err.lower()


# === Interactive mode → run_tui dispatch (Sprint 6h₁₀a / ADR-0104) ===========


class _FakeTTYStdin:
    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover — never read on a TTY
        return ""


async def test_interactive_mode_dispatches_to_run_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TTY stdin invocation with no --print flag picks "interactive" and
    dispatches to :func:`run_tui` with the constructed runtime + cwd
    (replacing the Phase 5b ``NotImplementedError`` carry-forward).
    """

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())

    calls: list[tuple[object, str, object, object]] = []
    tui_permission: dict[str, object] = {}

    async def _stub_run_tui(
        runtime: object,
        *,
        cwd: str,
        model_registry: object = None,
        mcp_manager: object = None,
        permission_ext: object = None,
        permission_posture: object = None,
        settings_manager: object = None,
        auth_storage: object = None,
        extensions: object = None,
    ) -> int:
        # Sprint 6h₂₆ (ADR-0154): the real model_registry must be threaded so
        # /model can list get_available() — the harness does not expose it.
        # Sprint 6h₂₇ (ADR-0155): mcp_manager is threaded the same way for /mcp.
        # WP-0 (ADR-0157): the held permission_ext + posture are threaded so
        # shift+tab + the approval dialog operate on the gate's own state.
        # WP-2 (ADR-0160): the held SettingsManager is threaded for /settings +
        # /scoped-models + /statusline.
        # WP-8 (Features 1 + 3): the held auth_storage (for /login + /logout) +
        # the discovered extensions list (for /extension) are threaded.
        calls.append((runtime, cwd, model_registry, mcp_manager))
        tui_permission["ext"] = permission_ext
        tui_permission["posture"] = permission_posture
        tui_permission["auth_storage"] = auth_storage
        tui_permission["extensions"] = extensions
        return 0

    # WP-0 nit: capture the held permission objects entry.py constructs so we can
    # assert run_tui receives the SAME instances (not a fresh per-call object) —
    # the held-ref guarantee that survives /resume / /new / /fork rebuilds.
    import aelix_coding_agent.cli.entry as entry_mod

    created: dict[str, object] = {}
    real_ext_cls = entry_mod.PermissionExtension
    real_posture_cls = entry_mod.PermissionPosture

    def _capturing_posture(*a: object, **k: object) -> object:
        obj = real_posture_cls(*a, **k)  # type: ignore[arg-type]
        created["posture"] = obj
        return obj

    def _capturing_ext(*a: object, **k: object) -> object:
        obj = real_ext_cls(*a, **k)  # type: ignore[arg-type]
        created["ext"] = obj
        return obj

    monkeypatch.setattr(entry_mod, "PermissionPosture", _capturing_posture)
    monkeypatch.setattr(entry_mod, "PermissionExtension", _capturing_ext)

    # Patch run_tui at its real home on the module object; ``modes.__getattr__``
    # resolves through ``from aelix_coding_agent.tui import run_tui`` and picks
    # up the stub. Patching ``modes.run_tui`` directly would pollute
    # ``modes.__dict__`` on teardown (monkeypatch restores the __getattr__ value
    # as a real attribute, shadowing the lazy accessor for later tests). Using
    # the module object (not a dotted string) avoids re-resolving
    # ``aelix_coding_agent.tui`` via getattr.
    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)

    code = await _async_main(["--no-session"])
    assert code == 0
    assert len(calls) == 1
    runtime, cwd, model_registry, mcp_manager = calls[0]
    assert runtime is not None
    assert cwd  # a concrete cwd string was passed
    # The real ModelRegistry is threaded through (so /model can list models).
    assert model_registry is not None
    # mcp_manager is threaded too (None here: --no-session run has no MCP
    # contribs, so entry.py leaves it None — the kwarg must still be accepted).
    assert mcp_manager is None
    # WP-0 nit: run_tui must receive the SAME held permission instances entry.py
    # built (identity, not a fresh per-call object) so shift+tab + the gate share
    # state across rebuilds. ``created`` holds the singletons entry.py constructed.
    assert tui_permission["ext"] is not None
    assert tui_permission["posture"] is not None
    assert tui_permission["ext"] is created.get("ext")
    assert tui_permission["posture"] is created.get("posture")
    # WP-8 (Feature 1): a real AuthStorage is threaded (so /login storing a key
    # is visible to the model registry built over the SAME object).
    assert tui_permission["auth_storage"] is not None
    # WP-8 (Feature 3): the extensions list is threaded (a list, even if empty —
    # the kwarg must always be a stable value, never None, for the viewer).
    assert isinstance(tui_permission["extensions"], list)


async def test_interactive_seeds_model_from_persisted_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """WP-2 (ADR-0160): with NO ``--model``/``--provider`` flag, the startup model
    is seeded from the PERSISTED ``defaultModel``/``defaultProvider`` settings so
    the /settings → "Default model" choice actually applies on the next launch
    (not only the session that set it). The persisted default reaches the live
    harness's ``current_model``.
    """

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    # Isolate the agent dir and pre-seed the global settings.json with a default.
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(
        '{"defaultProvider": "anthropic", "defaultModel": "claude-3"}'
    )
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))

    seen: dict[str, object] = {}

    async def _stub_run_tui(runtime: object, **_k: object) -> int:
        harness = getattr(runtime, "harness", None)
        seen["model"] = getattr(harness, "current_model", None)
        return 0

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)

    code = await _async_main(["--no-session"])
    assert code == 0
    model = seen["model"]
    assert model is not None
    assert getattr(model, "provider", None) == "anthropic"
    assert getattr(model, "id", None) == "claude-3"


async def test_interactive_explicit_model_flag_overrides_persisted_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The explicit ``--model``/``--provider`` flags WIN over the persisted
    default (CLI > settings, pi parity) — the seed only fills the gap.
    """

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(
        '{"defaultProvider": "anthropic", "defaultModel": "claude-3"}'
    )
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))

    seen: dict[str, object] = {}

    async def _stub_run_tui(runtime: object, **_k: object) -> int:
        harness = getattr(runtime, "harness", None)
        seen["model"] = getattr(harness, "current_model", None)
        return 0

    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)

    code = await _async_main(
        ["--no-session", "--provider", "openai", "--model", "gpt-4o"]
    )
    assert code == 0
    model = seen["model"]
    assert model is not None
    # The CLI flag won — NOT the persisted anthropic/claude-3 default.
    assert getattr(model, "provider", None) == "openai"
    assert getattr(model, "id", None) == "gpt-4o"


async def test_interactive_missing_tui_extra_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the ``[tui]`` extra is absent, interactive mode prints an
    actionable install hint and returns exit code 1 (no stack trace).
    """

    import aelix_coding_agent.modes as modes_mod

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())

    # Simulate the extra missing: make the lazy ``run_tui`` resolution raise
    # ImportError exactly as a real absent prompt-toolkit would at import time.
    # (Overriding the PEP-562 module __getattr__ is deterministic; a fake
    # submodule is re-imported by IMPORT_FROM on AttributeError.)
    def _missing_run_tui(name: str):
        if name == "run_tui":
            raise ImportError(
                "No module named 'prompt_toolkit'", name="prompt_toolkit"
            )
        raise AttributeError(name)

    monkeypatch.setattr(modes_mod, "__getattr__", _missing_run_tui, raising=False)

    code = await _async_main(["--no-session"])
    assert code == 1
    err = capsys.readouterr().err.lower()
    assert "tui" in err and "extra" in err


# === Piped stdin promotes to print mode ======================================


async def test_piped_stdin_promotes_to_print(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-TTY stdin promotes to print mode — but with no message
    and no initial content, the print path is a no-op (exit 0)."""

    class _FakePipedStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return ""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())

    # No initial message + no residual messages: print mode loop exits
    # cleanly without calling the harness.
    code = await _async_main(["--no-session"])
    # Should not raise NotImplementedError.
    assert code in (0, 1)


# === No-usable-model guard (ITEM #2 — auth-guidance + non-interactive abort) ==


class _FakePipedStdin:
    """Non-tty stdin so ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


async def test_no_provider_print_emits_guidance_and_exits_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ITEM #2: a non-interactive (print) run with NO provider (bare flags,
    no OpenRouter env) aborts BEFORE a turn with the "No model selected"
    auth-guidance on stderr + exit 1 (mirrors Pi's ``!session.model`` guard).
    """

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # No provider/model flags, and no OpenRouter env to infer a provider from.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)

    code = await _async_main(["--no-session", "--print"])
    err = capsys.readouterr().err
    assert code == 1
    # Pi-shape "no model selected" guidance, honestly adapted (no doc paths).
    assert "No model selected." in err
    # Honesty adaptation: references the REAL /model command + the env-var
    # route, and does NOT claim non-existent doc files OR a non-existent
    # /login command (Aelix has no /login — see auth_guidance honesty note).
    assert "/login" not in err
    assert "/model" in err
    assert "_API_KEY" in err
    assert "providers.md" not in err
    assert "models.md" not in err


async def test_provider_without_key_emits_no_api_key_guidance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider IS named but no key is resolvable for it (no env, no stored,
    no runtime override) → "No API key found for <provider>" + exit 1.
    """

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Ensure the anthropic provider has NO resolvable key in the environment.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)

    code = await _async_main(
        ["--provider", "anthropic", "--model", "claude-3", "--no-session", "--print"]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "No API key found for" in err
    # The provider display name appears in the message.
    assert "Anthropic" in err
    # Honesty: the env-var route is surfaced, and NO /login command is claimed
    # (Aelix does not register one — see auth_guidance honesty note).
    assert "/login" not in err
    assert "_API_KEY" in err


async def test_env_authenticated_print_passes_guard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal env-authenticated run is UNAFFECTED by the guard: with a
    provider + ``ANTHROPIC_API_KEY`` set, the guard passes and the run proceeds
    to the turn (which may still exit 1 on the mocked/absent real backend, but
    NEVER emits the auth-guidance message)."""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")

    code = await _async_main(
        ["--provider", "anthropic", "--model", "claude-3", "--no-session", "--print"]
    )
    err = capsys.readouterr().err
    # The guard did NOT fire — no auth-guidance on stderr.
    assert "No model selected." not in err
    assert "No API key found for" not in err
    # Exit code may be 0 or 1 (the latter only from the actual model turn,
    # not the guard) — what matters is the guard let it through.
    assert code in (0, 1)


# === End-to-end subprocess smoke tests =======================================


def test_module_dash_m_version() -> None:
    """Smoke: ``python -m aelix_coding_agent --version`` exits 0 with
    a non-empty version string on stdout.
    """

    result = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_module_dash_m_help() -> None:
    """Smoke: ``python -m aelix_coding_agent --help`` exits 0 and emits
    the help banner with ``aelix`` in it.
    """

    result = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "aelix" in result.stdout.lower()
    assert "--help" in result.stdout


# === Bare diagnostic guard =================================================


def test_args_module_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: Args + parse_args wiring through entry sees diagnostics."""

    from aelix_coding_agent.cli.args import parse_args

    parsed = parse_args(["--mode", "wat"])
    assert any(d["type"] == "error" for d in parsed.diagnostics)


# Silence unused-import warning if Any is not consumed above.
_UNUSED: Any = (io,)
