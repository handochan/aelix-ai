"""Regression coverage for extension flags on the first harness build.

``register_flag`` always worked; its user-facing half did not. ``_harness_factory``
seeded ``flag_values`` only from a :class:`ReloadSeed`, which is ``None`` on every
non-reload build, so ``parsed.unknown_flags`` never reached the extension runtime.

These tests drive the real ``_async_main`` because the seeding happens inside its
``_harness_factory`` closure. The extension records the values it observes, and the
run stops after the first harness build without attempting a turn or network access.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

_BUILT = 42
"""Sentinel exit code meaning the run reached the first harness build."""


class _StopAfterBuild(Exception):
    """Raised by the runtime spy once the harness exists."""


class _FakePipedStdin:
    """Non-tty stdin makes ``_async_main`` resolve to print mode."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a hermetic cwd, agent directory, and settings file."""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _flag_extension(tmp_path: Path, observed: Path) -> Path:
    """Create a tier-3 extension that declares flags and records their values."""

    ext = tmp_path / "flag_ext.py"
    ext.write_text(
        textwrap.dedent(
            f"""
            import json

            def setup(aelix):
                aelix.register_flag(
                    "greet", type="str", default="DEFAULT", description="probe"
                )
                aelix.register_flag(
                    "loud", type="bool", default=False, description="probe"
                )
                observed = {{
                    "greet": aelix.get_flag("greet"),
                    "loud": aelix.get_flag("loud"),
                }}
                with open({str(observed)!r}, "w") as fh:
                    json.dump(observed, fh)
            """
        ).strip()
        + "\n"
    )
    return ext


async def _run_to_harness(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Run ``_async_main`` in ``cwd``, stopping at the first harness build."""

    monkeypatch.chdir(cwd)
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        await real_create(harness, factory, **kwargs)
        raise _StopAfterBuild

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    try:
        return await entry_mod._async_main(argv)
    except _StopAfterBuild:
        return _BUILT


def _base_argv(ext: Path) -> list[str]:
    return [
        "--no-session",
        "--print",
        "-e",
        str(ext),
        "--provider",
        "anthropic",
        "--model",
        "probe-model",
    ]


def _read(observed: Path) -> dict[str, Any]:
    import json

    assert observed.exists(), "setup() never ran: the extension did not load"
    return json.loads(observed.read_text())


async def test_cli_flag_reaches_get_flag_on_first_build(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--greet hello`` is visible during the first extension setup."""

    observed = tmp_path / "observed.json"
    ext = _flag_extension(tmp_path, observed)
    code = await _run_to_harness([*_base_argv(ext), "--greet", "hello"], _isolated_env, monkeypatch)
    assert code == _BUILT
    assert _read(observed)["greet"] == "hello"


async def test_valueless_cli_flag_reaches_get_flag_as_true(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare ``--loud`` arrives as ``True``."""

    observed = tmp_path / "observed.json"
    ext = _flag_extension(tmp_path, observed)
    code = await _run_to_harness([*_base_argv(ext), "--loud"], _isolated_env, monkeypatch)
    assert code == _BUILT
    assert _read(observed)["loud"] is True


async def test_declared_default_survives_when_no_cli_flag_is_given(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no extension flag, the extension's own defaults still win."""

    observed = tmp_path / "observed.json"
    ext = _flag_extension(tmp_path, observed)
    code = await _run_to_harness(_base_argv(ext), _isolated_env, monkeypatch)
    assert code == _BUILT
    got = _read(observed)
    assert got["greet"] == "DEFAULT"
    assert got["loud"] is False


async def test_only_the_named_flag_is_seeded(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting one flag leaves the other on its declared default."""

    observed = tmp_path / "observed.json"
    ext = _flag_extension(tmp_path, observed)
    code = await _run_to_harness([*_base_argv(ext), "--greet", "hello"], _isolated_env, monkeypatch)
    assert code == _BUILT
    got = _read(observed)
    assert got["greet"] == "hello"
    assert got["loud"] is False
