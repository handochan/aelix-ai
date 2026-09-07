"""#238 — the three hops between ``get_respect_gitignore`` and the ``fd`` argv.

On ``main`` nothing under ``tests/`` mentions ``_build_input_completer`` or
``_wire_descriptors`` at all, and that gap is not theoretical: two independent
critique lanes implemented #238's product change, wrote every other case, then
deleted the ``respect_gitignore=`` keyword at each hop in turn. The row was dead
for every user and the suite stayed green — ``tests/tui`` + ``tests/pi_parity`` +
``tests/settings_manager`` 2151 passed, ``ruff check packages/`` clean (no ``ARG``
rules are selected, so an unused parameter is silent), ``pyright --pythonplatform
Windows`` 0 errors. The #84 defect shipping certified-wired, from the opposite
direction.

Three behavioural cases, one per hop, each measured to fail only for its own
hop, plus an AST sweep for the one thing behaviour cannot see: a NEW call site
added later without the keyword.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aelix_ai.settings import SettingsManager
from aelix_coding_agent.tui import completion as completion_mod
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.completion import _FD_TIMEOUT
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime, _BusExtRuntime

_SHELL_SRC = (
    Path(__file__).resolve().parents[2]
    / "packages/aelix-coding-agent/src/aelix_coding_agent/tui/shell.py"
)


def _stub_fd(monkeypatch: Any, base: Path, stdout: str) -> list[list[str]]:
    """Same seam as ``test_completion.py``: ``completion_mod.run_contained``.

    Pins the call as well as the argv — ``timeout=_FD_TIMEOUT``, ``cwd=str(base)``
    and nothing else — so a site that grew an extra kwarg is red here too.
    """

    calls: list[list[str]] = []

    def _fake(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(argv))
        assert kwargs.pop("timeout", None) == _FD_TIMEOUT
        assert kwargs.pop("cwd", None) == str(base)
        assert not kwargs, f"unexpected run_contained kwargs: {kwargs}"
        return subprocess.CompletedProcess(list(argv), 0, stdout.encode(), b"")

    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: "fd")
    monkeypatch.setattr(completion_mod, "run_contained", _fake)
    return calls


def _tree(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x")


async def _wait(predicate, *, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@asynccontextmanager
async def _harness_chrome(
    harness: FakeHarness,
) -> AsyncGenerator[tuple[FakeRuntime, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        yield FakeRuntime(harness), AelixChrome(), pipe


def _drive_at_menu(completer: Any) -> list[str]:
    doc = Document(text="@app", cursor_position=4)
    return [c.text for c in completer.get_completions(doc, CompleteEvent())]


async def _argv_from_run_tui(
    monkeypatch: Any, tmp_path: Path, harness: FakeHarness, *, respect: bool | None = None
) -> list[list[str]]:
    """Launch the real ``run_tui`` and drive the installed ``@`` completer.

    ``respect=None`` hands ``run_tui`` no ``SettingsManager`` at all — the arm
    ``_respect_gitignore``'s ``settings_manager is None`` branch answers (⑭b).
    """

    _tree(tmp_path)
    calls = _stub_fd(monkeypatch, tmp_path, "src/app.py\n")
    sm: SettingsManager | None = None
    if respect is not None:
        sm = SettingsManager.in_memory({})
        sm.set_respect_gitignore(respect)

    async with _harness_chrome(harness) as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=sm,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: _drive_at_menu(chrome.buffer.completer) != [])
        assert _drive_at_menu(chrome.buffer.completer) == ["@src/app.py"]
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=10)

    assert calls, "the @ menu never reached the fd seam"
    return calls


# === ⑫ the BUILDER hop ======================================================


def test_the_built_completer_carries_the_toggle(monkeypatch: Any, tmp_path: Path) -> None:
    """``_build_input_completer`` forwards into ``FileMentionCompleter``.

    Driven through the real merged completer (``merge_completers`` +
    ``ThreadedCompleter``), not by reaching inside it, so a forward that lands
    on the wrong sub-completer is red.
    """

    _tree(tmp_path)
    calls = _stub_fd(monkeypatch, tmp_path, "src/app.py\n")

    off = tui_shell._build_input_completer(
        lambda: {}, [], str(tmp_path), None, respect_gitignore=lambda: False
    )
    assert _drive_at_menu(off) == ["@src/app.py"]
    assert "--no-ignore" in calls[0]

    on = tui_shell._build_input_completer(
        lambda: {}, [], str(tmp_path), None, respect_gitignore=lambda: True
    )
    assert _drive_at_menu(on) == ["@src/app.py"]
    assert "--no-ignore" not in calls[1]

    # The keyword OMITTED — the `respect_gitignore or (lambda: True)` fallback.
    # `None` is not "no callable": it coerces to `lambda: None`, which is falsy,
    # so without the guard this arm asks fd for --no-ignore. ⑫-⑮ all pass an
    # EXPLICIT callable, so nothing else reaches this branch — measured, the
    # guard can be deleted with the full suite (10280 passed) still green.
    default = tui_shell._build_input_completer(lambda: {}, [], str(tmp_path), None)
    assert _drive_at_menu(default) == ["@src/app.py"]
    assert "--no-ignore" not in calls[2], calls[2]


# === ⑬ the _wire_descriptors hop ===========================================


class _ModalHarness(FakeHarness):
    """A harness with a real EventBus, so ``_wire_descriptors`` installs the
    completer and ``run_tui``'s fallback site is never reached."""

    def __init__(self) -> None:
        super().__init__()
        self.runtime = _BusExtRuntime()


async def test_run_tui_hands_the_toggle_to_the_at_menu_descriptor_arm(
    monkeypatch: Any, tmp_path: Path
) -> None:
    calls = await _argv_from_run_tui(monkeypatch, tmp_path, _ModalHarness(), respect=False)
    assert "--no-ignore" in calls[0], calls[0]


# === ⑭ the run_tui fallback hop ============================================


async def test_run_tui_hands_the_toggle_to_the_at_menu_no_descriptor_arm(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # A plain FakeHarness exposes no ``event_bus``, so ``_wire_descriptors``
    # returns ``(None, None)`` and ``run_tui`` installs the completer itself.
    calls = await _argv_from_run_tui(monkeypatch, tmp_path, FakeHarness(), respect=False)
    assert "--no-ignore" in calls[0], calls[0]


async def test_run_tui_leaves_the_at_menu_narrow_when_the_toggle_is_on(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # The ON mirror: the default must not widen the menu for everyone.
    calls = await _argv_from_run_tui(monkeypatch, tmp_path, FakeHarness(), respect=True)
    assert "--no-ignore" not in calls[0], calls[0]


async def test_run_tui_without_a_settings_manager_keeps_the_at_menu_narrow(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # ⑭b `_respect_gitignore`'s OTHER job (design §B.4): absorbing
    # `settings_manager is None` (older entrypoints / test fakes). ⑮ pins that
    # the nested def is an ast.Call site; nothing pins that its None arm returns
    # True, so flipping `return True` to `return False` is silent today.
    calls = await _argv_from_run_tui(monkeypatch, tmp_path, FakeHarness())
    assert "--no-ignore" not in calls[0], calls[0]


# === ⑮ the AST sweep — a NEW call site is a decision, not a silent gap ======


def test_every_completer_call_site_passes_the_toggle() -> None:
    """Behaviour covers the three hops that exist; this covers the fourth.

    A call site added later without ``respect_gitignore=`` would restore the
    inert row without failing ⑫-⑭, because those drive the two arms that exist
    today. The count is asserted for the mirror reason: silently dropping a hop
    must not read as compliance.
    """

    tree = ast.parse(_SHELL_SRC.read_text(encoding="utf-8"), filename=str(_SHELL_SRC))
    wanted = {"_build_input_completer", "_wire_descriptors"}
    sites: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else None
        if name not in wanted:
            continue
        has_kw = any(kw.arg == "respect_gitignore" for kw in node.keywords)
        sites.append((name, node.lineno, has_kw))

    missing = [f"{name} at shell.py:{lineno}" for name, lineno, ok in sites if not ok]
    assert not missing, f"completer call sites without respect_gitignore=: {missing}"
    assert len(sites) == 3, (
        f"expected the 3 known completer call sites, found {len(sites)}: "
        f"{[(n, ln) for n, ln, _ in sites]} — a new hop needs its own keyword "
        "and this count updated in the same commit"
    )
