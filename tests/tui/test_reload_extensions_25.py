"""#25 (ADR-0284 D6; the design's M3, Codex F08) — what ``/reload`` says about extensions.

Through the real ``run_tui`` input loop over a scripted runtime:

* a reload that changed the extension set commits ONE entry naming the change
  and the failure;
* a reload that raised in the rebuild says nothing was applied, and the panel's
  banner says so until a build succeeds;
* a reload that raised AFTER the new harness was installed says it applied;
* nothing is printed when nothing changed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aelix_coding_agent.core.extension_selection import (
    EntryInfo,
    ExtensionControl,
    ExtensionInventory,
    ExtensionKind,
    ExtensionRow,
)
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    FakeRuntime,
    _harness_chrome,
    _NewRuntime,
    _quit_within,
    _spy_commits,
    _wait,
)


class _Registry:
    def clear_config_value_cache(self) -> None:
        pass

    def get_available(self) -> list[Any]:
        return []


def _row(label: str, *, loaded: bool, error: str | None = None) -> ExtensionRow:
    info = EntryInfo(
        label=label, identities=(label.removesuffix(".py"),), path=None, scope="global"
    )
    return ExtensionRow(
        key=f"global:{label}",
        info=info,
        tier="global",
        explicit=False,
        selected=error is not None or loaded,
        loaded=loaded,
        error=error,
    )


def _control(holder: dict[str, Any]) -> ExtensionControl:
    return ExtensionControl(
        selection=None,  # type: ignore[arg-type]
        kind=ExtensionKind(
            inventory=lambda: holder.get("inventory"),
            last_reload_failure=lambda: holder.get("reload_failure"),
        ),
        holder=holder,
    )


class _ScriptedRuntime(FakeRuntime):
    def __init__(self, harness: FakeHarness, script: Any) -> None:
        super().__init__(harness)
        self._script = script
        self.reload_phase: str | None = None

    async def reload(self) -> None:
        self.reloads += 1
        await self._script(self)


async def _drive(
    runtime: FakeRuntime, chrome: Any, pipe: Any, control: ExtensionControl, want: str
) -> list[str]:
    commits = _spy_commits(chrome)
    task = asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=False,
            model_registry=_Registry(),  # type: ignore[arg-type]
            extension_control=control,
        )
    )
    await _wait(lambda: chrome.app.is_running)
    pipe.send_text("/reload\n")
    await _wait(lambda: runtime.reloads >= 1 and any(want in c for c in commits) or False)
    pipe.send_text("/quit\n")
    await _quit_within(task)
    return commits


async def test_a_reload_that_changed_the_set_commits_one_entry() -> None:
    holder: dict[str, Any] = {
        "inventory": ExtensionInventory(
            (_row("weather.py", loaded=True), _row("lint.py", loaded=False))
        )
    }

    async def script(runtime: _ScriptedRuntime) -> None:
        holder["inventory"] = ExtensionInventory(
            (
                _row("weather.py", loaded=False),
                _row("lint.py", loaded=True),
                _row("broken.py", loaded=False, error="RuntimeError: boom"),
            )
        )
        runtime.reload_phase = "done"

    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ScriptedRuntime(base.harness, script)
        commits = await _drive(runtime, chrome, pipe, _control(holder), "Extensions after /reload")
    [entry] = [c for c in commits if "Extensions after /reload" in c]
    assert "weather.py off" in entry and "lint.py on" in entry
    assert "✖ broken.py failed to load: RuntimeError: boom (see /extension)" in entry
    assert not any("Warning: extension load" in c for c in commits)


async def test_a_reload_that_raised_in_the_rebuild_applied_nothing() -> None:
    before = ExtensionInventory((_row("weather.py", loaded=True),))
    holder: dict[str, Any] = {"inventory": before}

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime.reload_phase = "build"
        raise RuntimeError("factory boom")

    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ScriptedRuntime(base.harness, script)
        control = _control(holder)
        commits = await _drive(runtime, chrome, pipe, control, "/reload failed")
    [entry] = [c for c in commits if "/reload failed" in c]
    line = " ".join(entry.split())  # wrapped at the terminal width
    assert "during the rebuild: RuntimeError: factory boom" in line
    assert "nothing applied" in line
    # The holders still say what the running build loaded; the panel says why.
    assert holder["inventory"] is before
    assert control.kind.notices()[0] == line


async def test_a_reload_that_raised_after_the_install_says_it_applied() -> None:
    holder: dict[str, Any] = {"inventory": ExtensionInventory()}

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime._harness = FakeHarness()  # the new harness is installed
        runtime.reload_phase = "resources"
        raise RuntimeError("resources_discover hook handler raised: late")

    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ScriptedRuntime(base.harness, script)
        commits = await _drive(runtime, chrome, pipe, _control(holder), "/reload applied")
    [entry] = [c for c in commits if "/reload applied" in c]
    line = " ".join(entry.split())
    assert "but re-discovering resources failed" in line and "nothing applied" not in line


async def test_nothing_is_said_when_nothing_changed() -> None:
    same = ExtensionInventory((_row("weather.py", loaded=True),))
    holder: dict[str, Any] = {"inventory": same}

    async def script(runtime: _ScriptedRuntime) -> None:
        runtime.reload_phase = "done"

    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ScriptedRuntime(base.harness, script)
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extension_control=_control(holder),
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: runtime.reloads >= 1)
        for _ in range(20):
            await asyncio.sleep(0)
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert not any("Extensions after /reload" in c or "/reload failed" in c for c in commits)


# --- review round 1 -------------------------------------------------------------------


async def test_a_new_session_says_what_failed_to_load_below_its_banner() -> None:
    """Verify V11: ``/new`` printed ``Warning: extension load:`` raw over the input."""

    holder: dict[str, Any] = {
        "inventory": ExtensionInventory(
            (_row("broken.py", loaded=False, error="RuntimeError: boom"),)
        )
    }
    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _NewRuntime(base.harness)
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extension_control=_control(holder),
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/new\n")
        await _wait(lambda: any("failed to load" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    [entry] = [c for c in commits if "failed to load" in c]
    assert entry.startswith("Extensions in this session:")
    assert "✖ broken.py failed to load: RuntimeError: boom (see /extension)" in entry


async def test_a_resumed_session_says_what_failed_to_load_below_its_banner() -> None:
    """Round-1 verify R1: ``/resume`` committed its banner and nothing else."""

    from tests.tui.test_run_tui_smoke import (
        _ResumeMeta,
        _ResumeRepo,
        _ResumeRuntime,
        _ResumeSession,
    )

    holder: dict[str, Any] = {
        "inventory": ExtensionInventory(
            (_row("broken.py", loaded=False, error="RuntimeError: boom"),)
        )
    }
    repo = _ResumeRepo(
        [
            _ResumeMeta("aaaaaaaa", "/s/active.jsonl", "2026-11-03T15:00"),
            _ResumeMeta("bbbbbbbb", "/s/target.jsonl", "2026-11-03T14:00"),
        ]
    )
    async with _harness_chrome() as (base, chrome, pipe):
        runtime = _ResumeRuntime(
            base.harness,
            repo,
            _ResumeSession("/s/active.jsonl", []),
            target=_ResumeSession("/s/target.jsonl", []),
        )
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extension_control=_control(holder),
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/resume\n")
        await _wait(lambda: chrome.is_modal_open(), what="the /resume picker to mount")
        pipe.send_text("\r")
        await _wait(lambda: any("failed to load" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    resumed = next(i for i, c in enumerate(commits) if c.startswith("↻ Resumed session"))
    [entry] = [c for c in commits[resumed + 1 :] if "failed to load" in c]
    assert entry.startswith("Extensions in this session:")
    assert "✖ broken.py failed to load: RuntimeError: boom (see /extension)" in entry


async def test_extension_opens_over_the_live_lists_not_copies(
    monkeypatch: Any,
) -> None:
    """The design's M3 (verify V6): ``run_tui`` copied the lists the factory refills."""

    import aelix_coding_agent.tui.extension_manager as manager

    seen: dict[str, Any] = {}

    async def _capture(**kw: Any) -> None:
        seen.update(kw)

    monkeypatch.setattr(manager, "run_extension_manager", _capture)
    extensions: list[Any] = []
    errors: list[Any] = []
    async with _harness_chrome() as (base, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                base,  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                extensions=extensions,
                extension_errors=errors,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/extension\n")
        await _wait(lambda: "extensions" in seen)
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert seen["extensions"] is extensions and seen["errors"] is errors


def test_a_wrapped_first_line_never_reads_as_the_next_line() -> None:
    """Verify V9: at 50 columns the first line's rest and the next line shared an indent."""

    from aelix_coding_agent.tui.shell import _reload_text

    lines = [
        ("bold red", "✖ /reload failed during the rebuild: RuntimeError: boom — nothing applied; /reload again or restart"),
        ("red", "✖ broken.py failed to load: RuntimeError: a long reason that wraps (see /extension)"),
    ]
    rows = _reload_text(lines, 50).plain.split("\n")
    assert all(len(r) <= 50 for r in rows), rows
    second = next(i for i, r in enumerate(rows) if "broken.py" in r)
    assert all(r.startswith("  ") and not r.startswith("   ") for r in rows[1:second])
    assert rows[second].startswith("    ✖ broken.py")
    assert all(r.startswith("      ") for r in rows[second + 1 :])
