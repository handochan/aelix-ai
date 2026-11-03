"""#664 — an extension whose import or ``setup()`` raises ``SystemExit`` or ``KeyboardInterrupt``.

Before: both are ``BaseException``, so they went past the loader's two
``except Exception`` and out of the harness factory — ``sys.exit(1)`` in a
``setup()`` ended ``aelix -p`` with no output and the TUI on ``/reload``
(``.omc/specs/25-followups-evidence/i2b/``).

Owner decision (2026-11-03): ``SystemExit`` is an ordinary load error naming the
extension, which is skipped while the rest load; ``KeyboardInterrupt`` still ends
the run, as :class:`ExtensionInterrupted` naming the extension and the step;
``asyncio.CancelledError`` passes through unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime, ReloadSeed
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.core.extension_selection import (
    ExtensionKind,
    build_inventory,
    reload_lines,
)
from aelix_coding_agent.extensions.loader import (
    ExtensionInterrupted,
    activate_pending_extension,
    discover_and_load_extensions,
    load_extensions,
)

from tests.extensions.test_extension_selection_25 import _load, _pack, home  # noqa: F401

GOOD = "def setup(aelix):\n    aelix.register_command('good', handler=lambda a, c: 'ok')\n"


def _ext(root: Path, name: str, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("raised", "text"),
    [
        ("sys.exit(1)", "sys.exit(1)"),
        ("sys.exit()", "sys.exit()"),
        ("raise SystemExit('flip: setup gave up')", "flip: setup gave up"),
    ],
)
async def test_a_setup_that_exits_is_a_load_error_and_the_rest_load(
    tmp_path: Path, raised: str, text: str
) -> None:
    bad = _ext(tmp_path, "exit_ext", f"import sys\n\ndef setup(aelix):\n    {raised}\n")
    good = _ext(tmp_path, "good_ext", GOOD)
    result = await load_extensions([bad, good])
    [error] = result.errors
    assert error.error == text
    assert error.path.endswith("exit_ext.py")
    [failed] = [o for o in result.outcomes if o.error is not None]
    assert failed.error_type == "SystemExit"
    assert [sorted(e.commands) for e in result.extensions] == [["good"]]


async def test_an_import_that_exits_is_a_load_error(tmp_path: Path) -> None:
    bad = _ext(tmp_path, "exit_at_import", "import sys\nsys.exit(2)\n")
    good = _ext(tmp_path, "good_ext", GOOD)
    result = await load_extensions([bad, good])
    [error] = result.errors
    assert (error.path, error.error) == (str(bad), "sys.exit(2)")
    assert [o.error_type for o in result.outcomes if o.error is not None] == ["SystemExit"]
    assert len(result.extensions) == 1


@pytest.mark.parametrize(
    ("body", "during"),
    [
        ("def setup(aelix):\n    raise KeyboardInterrupt\n", "setup()"),
        ("raise KeyboardInterrupt\n", "import"),
    ],
)
async def test_a_keyboard_interrupt_still_ends_the_run_naming_the_extension(
    tmp_path: Path, body: str, during: str
) -> None:
    bad = _ext(tmp_path, "kbi_ext", body)
    with pytest.raises(KeyboardInterrupt) as caught:
        await load_extensions([bad, _ext(tmp_path, "good_ext", GOOD)])
    err = caught.value
    assert isinstance(err, ExtensionInterrupted)
    assert err.extension.endswith("kbi_ext.py") and err.during == during
    assert str(err) == f"interrupted while the extension {err.extension} was running its {during}"


async def test_a_cancel_passes_through_unchanged(tmp_path: Path) -> None:
    bad = _ext(
        tmp_path, "cancel_ext", "import asyncio\n\ndef setup(aelix):\n    raise asyncio.CancelledError\n"
    )
    with pytest.raises(asyncio.CancelledError) as caught:
        await load_extensions([bad])
    assert not isinstance(caught.value, ExtensionInterrupted)


async def test_the_panel_and_the_reload_line_name_system_exit(home: Path) -> None:  # noqa: F811
    ext = home / "agent" / "extensions"
    _ext(ext, "weather", "def setup(aelix):\n    pass\n")
    before = await _load(home, [])
    _ext(ext, "weather", "import sys\n\ndef setup(aelix):\n    sys.exit(1)\n")
    after = await _load(home, [])
    lines = reload_lines(before, after)
    assert ("red", "✖ weather.py failed to load: SystemExit: sys.exit(1) (see /extension)") in lines
    kind = ExtensionKind(inventory=lambda: after)
    assert list(kind.item_problems().values()) == ["SystemExit: sys.exit(1)"]


async def test_a_deferred_pack_that_exits_on_its_first_command_fails_alone(
    home: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ext = home / "agent" / "extensions"
    pack = _pack(ext, "lazy", entry="lazy_exit_664:setup", activation='on_command = ["lazy-go"]')
    (pack / "lazy_exit_664.py").write_text("import sys\n\ndef setup(aelix):\n    sys.exit(3)\n")
    monkeypatch.syspath_prepend(str(pack))
    loaded = await discover_and_load_extensions([], cwd=home, agent_dir=home / "agent")
    inventory = build_inventory(loaded)
    with pytest.raises(RuntimeError, match=r"^SystemExit: sys.exit\(3\)$"):
        await activate_pending_extension(loaded.runtime, "lazy")
    (row,) = inventory.rows
    assert inventory.deferred_state(row) == "failed: SystemExit: sys.exit(3)"


# --- the rebuilds: /reload, /new, /resume, /fork --------------------------------------


async def _stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    yield AssistantStartEvent(partial=AssistantMessage(content=[]))
    yield AssistantEndEvent(
        message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
    )


class _Rig:
    """A real runtime whose factory loads one extension file, as every build does."""

    def __init__(self, tmp: Path) -> None:
        self.ext = _ext(tmp / "exts", "flip", "def setup(aelix):\n    pass\n")
        self.good = _ext(tmp / "exts", "good_ext", GOOD)
        self.errors: list[Any] = []
        self.runtime: AgentSessionRuntime
        self.session: Session
        self.other: Session

    async def factory(self, session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        loaded = await load_extensions([self.ext, self.good])
        self.errors = loaded.errors
        return AgentHarness(
            AgentHarnessOptions(
                model=Model(id="fixture", provider="fixture"),
                stream_fn=_stream,
                session=session,
                extensions=loaded.extensions,
                runtime=loaded.runtime,
            )
        )

    @classmethod
    async def create(cls, tmp: Path) -> _Rig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        rig.other = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await rig.other.append_message(UserMessage(content=[TextContent(text="elsewhere")]))
        rig.session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await rig.session.append_message(UserMessage(content=[TextContent(text="hello one")]))
        rig.runtime = AgentSessionRuntime(await rig.factory(rig.session), rig.factory, repo=repo, fs=fs)
        return rig


async def _rebuild(rig: _Rig, how: str) -> None:
    if how == "reload":
        await rig.runtime.reload()
    elif how == "new":
        await rig.runtime.new_session()
    elif how == "resume":
        assert rig.other.session_file is not None
        await rig.runtime.switch_session(rig.other.session_file)
    else:
        entry = (await rig.session.get_entries())[-1]
        await rig.runtime.fork(entry.id, position="at")


@pytest.mark.parametrize("how", ["reload", "new", "resume", "fork"])
async def test_a_rebuild_with_an_extension_that_exits_loads_the_rest(
    tmp_path: Path, how: str
) -> None:
    rig = await _Rig.create(tmp_path)
    rig.ext.write_text("def setup(aelix):\n    raise SystemExit('flip: setup gave up')\n")
    await _rebuild(rig, how)
    assert [(e.path, e.error) for e in rig.errors] == [(str(rig.ext), "flip: setup gave up")]
    assert rig.runtime.stopped is None
    commands = [n for e in rig.runtime.harness.extension_runner.extensions for n in e.commands]
    assert commands == ["good"]


async def test_a_keyboard_interrupt_in_a_reload_names_the_extension_and_stops(
    tmp_path: Path,
) -> None:
    """The interrupt ends the run; until it does, nothing runs ungated (#663)."""

    rig = await _Rig.create(tmp_path)
    rig.ext.write_text("def setup(aelix):\n    raise KeyboardInterrupt\n")
    with pytest.raises(ExtensionInterrupted) as caught:
        await rig.runtime.reload()
    assert caught.value.during == "setup()"
    assert rig.runtime.stopped is not None
