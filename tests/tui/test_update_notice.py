"""The update notice on the real launch path.

Everything here goes through the production ``run_tui``. The decision logic has
its own 38 unit tests; what these measure is the part a unit test cannot — that
the check is actually started, actually gated, actually committed to scrollback
where it survives the chrome's repaint, and that no failure of it can keep a
user out of their REPL.

WHY NOT stderr. ``cli/entry.py`` records the measurement: a line written to
stderr before ``run_tui`` paints is erased by the chrome's repaint. That is why
the #98 unrunnable-model warning is suppressed for first-run users, and it is
why this notice is a scrollback commit instead.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_run_tui_smoke import (
    FakeRuntime,
    _harness_chrome,
    _spy_commits,
    _wait,
)


def _feed(version: str, prerelease: bool = True) -> dict[str, Any]:
    tag = "v" + version.replace("b", "-beta.")
    return {
        "schemaVersion": 1,
        "latest": {
            "version": version,
            "tag": tag,
            "prerelease": prerelease,
            "url": f"https://example.invalid/releases/tag/{tag}",
        },
        "latestStable": None,
    }


class _Settings:
    """The one method the launch path asks about."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def get_check_for_updates(self) -> bool:
        return self._enabled


@pytest.fixture
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the agent dir at a temp dir and force a known running version.

    Without the first, the check writes its cache into the real ``~/.aelix``.
    Without the second, the test's verdict depends on whatever version the
    working copy happens to declare.
    """

    import aelix_coding_agent.cli.config as config

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "VERSION", "0.1.0b1")
    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.delenv("AELIX_OFFLINE", raising=False)
    return tmp_path


def _launch(
    runtime: FakeRuntime, chrome: AelixChrome, settings: object | None
) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=False,
            settings_manager=settings,  # type: ignore[arg-type]
        )
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetch: Any,
    settings: object | None = None,
) -> list[str]:
    import aelix_coding_agent.update_check as uc

    monkeypatch.setattr(uc, "default_fetch", fetch)
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome, _Settings() if settings is None else settings)
        await _wait(lambda: chrome.app.is_running)
        # The banner is committed before the notice, so waiting for it puts us
        # after launch without racing the notice we are about to look for.
        await _wait(lambda: any("Aelix Agent Runtime" in c for c in commits))
        await asyncio.sleep(0.3)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    return commits


async def test_a_newer_release_is_announced_once_with_a_command(
    _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commits = await _run(monkeypatch, fetch=lambda: _feed("0.1.0b2"))
    shown = "\n".join(commits)
    assert "0.1.0b2 is available" in shown, shown[-800:]
    assert shown.count("is available") == 1
    assert "https://example.invalid/releases/tag/v0.1.0-beta.2" in shown


async def test_the_same_version_says_nothing(
    _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commits = await _run(monkeypatch, fetch=lambda: _feed("0.1.0b1"))
    assert "is available" not in "\n".join(commits)


async def test_the_setting_turns_it_off_and_no_fetch_happens(
    _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#84's rule made concrete: the row has a production consumer, and this is
    the test that the consumer is real rather than declared."""

    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        return _feed("0.1.0b2")

    commits = await _run(monkeypatch, fetch=fetch, settings=_Settings(enabled=False))
    assert calls == [], "the check ran despite checkForUpdates being off"
    assert "is available" not in "\n".join(commits)


@pytest.mark.parametrize("var", ["PI_OFFLINE", "AELIX_OFFLINE"])
async def test_offline_suppresses_the_check_entirely(
    _isolated: Path, monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """README.ko.md promises ``--offline`` blocks the requests aelix makes for
    itself. This is that promise, asserted."""

    monkeypatch.setenv(var, "1")
    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        return _feed("0.1.0b2")

    commits = await _run(monkeypatch, fetch=fetch)
    assert calls == []
    assert "is available" not in "\n".join(commits)


async def test_a_failing_check_is_invisible_and_the_repl_still_works(
    _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A startup complaint about a failed CHECK is worse than no check."""

    def boom() -> dict[str, Any]:
        raise OSError("no network")

    async with _harness_chrome() as (runtime, chrome, pipe):
        import aelix_coding_agent.update_check as uc

        monkeypatch.setattr(uc, "default_fetch", boom)
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome, _Settings())
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello there\n")
        await _wait(
            lambda: runtime.harness.prompts == [("hello there", "interactive")]
        )
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    shown = "\n".join(commits)
    for noise in ("is available", "update", "Update", "no network", "Traceback"):
        assert noise not in shown, f"the failed check leaked {noise!r}"


async def test_the_answer_is_cached_so_the_next_launch_is_free(
    _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 24h cadence is what makes this safe behind a shared NAT — and the
    cached ANSWER is what keeps the notice visible without a second fetch."""

    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        return _feed("0.1.0b2")

    first = await _run(monkeypatch, fetch=fetch)
    assert "0.1.0b2 is available" in "\n".join(first)
    second = await _run(monkeypatch, fetch=fetch)
    assert calls == [1], f"the network was consulted {len(calls)} times"
    assert "0.1.0b2 is available" in "\n".join(second)

    cache = json.loads((_isolated / "update_check.json").read_text(encoding="utf-8"))
    assert "last_checked_at" in cache
