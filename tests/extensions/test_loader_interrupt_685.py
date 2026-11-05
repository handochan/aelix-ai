"""#685 — a Ctrl+C while an extension's import or ``setup()`` runs names the extension.

#664 made a ``KeyboardInterrupt`` from an extension one line naming it. A real
Ctrl+C is not one: ``asyncio.run`` turns the first SIGINT into a cancel of the
main task, so the loader saw a ``CancelledError`` (async ``setup()``) or nothing
at all (a sync ``setup()`` holds the loop; the cancel waited for the next
suspension — the model turn, in ``-p``), and ``asyncio.run`` raised its own
``KeyboardInterrupt`` at the end, which ``main_sync`` printed as a traceback.

Here the main task's cancel is what the loader sees: ``task.cancel()`` from a
callback or from the extension's own sync code, as asyncio's SIGINT handler does.
The real SIGINT rows are ``tests/cli/test_ctrl_c_during_setup_685.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.loader import (
    ExtensionInterrupted,
    interrupted_extension,
    load_extensions,
)


def _ext(root: Path, name: str, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


def _marker(root: Path, name: str) -> tuple[Path, Path]:
    """An extension whose ``setup()`` writes a file: did it run?"""

    flag = root / f"{name}.ran"
    return _ext(root, name, f"def setup(aelix):\n    open({str(flag)!r}, 'w').close()\n"), flag


async def test_a_cancel_during_an_async_setup_carries_the_extension(tmp_path: Path) -> None:
    started = tmp_path / "started"
    slow = _ext(
        tmp_path,
        "slow_ext",
        "import asyncio\n\n"
        "async def setup(aelix):\n"
        f"    open({str(started)!r}, 'w').close()\n"
        "    await asyncio.sleep(60)\n",
    )
    after, ran = _marker(tmp_path, "after_ext")
    task = asyncio.ensure_future(load_extensions([slow, after]))
    for _ in range(500):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists(), "the slow setup() never started"
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension.endswith("slow_ext.py") and named.during == "setup()"
    assert not ran.exists()


@pytest.mark.parametrize(
    ("body", "during"),
    [
        # The cancel request made while the import holds the loop.
        (
            "import asyncio\nasyncio.current_task().cancel()\n\ndef setup(aelix):\n    pass\n",
            "import",
        ),
        ("import asyncio\n\ndef setup(aelix):\n    asyncio.current_task().cancel()\n", "setup()"),
    ],
)
async def test_a_cancel_requested_while_sync_code_held_the_loop_is_taken_after_it(
    tmp_path: Path, body: str, during: str
) -> None:
    """Before, the cancel waited for the next suspension and the next extension loaded."""

    held = _ext(tmp_path, "held_ext", body)
    after, ran = _marker(tmp_path, "after_ext")
    with pytest.raises(asyncio.CancelledError) as caught:
        # A task of its own: the extension cancels the task it runs in.
        await asyncio.ensure_future(load_extensions([held, after]))
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension.endswith("held_ext.py") and named.during == during
    assert not ran.exists(), "the extension after the interrupted one loaded"


async def test_no_cancel_means_no_extra_turn_and_nothing_marked(tmp_path: Path) -> None:
    first, first_ran = _marker(tmp_path, "first_ext")
    second, second_ran = _marker(tmp_path, "second_ext")
    loaded = await load_extensions([first, second])
    assert [Path(e.name).name for e in loaded.extensions] == ["first_ext.py", "second_ext.py"]
    assert first_ran.exists() and second_ran.exists()


def test_interrupted_extension_reads_the_chain_asyncio_run_leaves() -> None:
    """``asyncio.run`` raises ``KeyboardInterrupt()`` while handling the task's cancel."""

    cancelled = asyncio.CancelledError()
    cancelled._aelix_extension_interrupted = ("slow.py", "setup()")  # type: ignore[attr-defined]
    try:
        try:
            raise cancelled
        except asyncio.CancelledError:
            raise KeyboardInterrupt  # noqa: B904 — the shape asyncio.Runner.run makes
    except KeyboardInterrupt as exc:
        named = interrupted_extension(exc)
    assert named is not None
    assert str(named) == "interrupted while the extension slow.py was running its setup()"
    # An ExtensionInterrupted is its own answer; a bare one, or one over an
    # unmarked cancel (a Ctrl+C outside any extension), has none.
    own = ExtensionInterrupted("x.py", "import")
    assert interrupted_extension(own) is own
    assert interrupted_extension(KeyboardInterrupt()) is None
    try:
        try:
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise KeyboardInterrupt  # noqa: B904
    except KeyboardInterrupt as exc:
        assert interrupted_extension(exc) is None


async def test_a_nested_cancel_names_the_inner_extension(tmp_path: Path) -> None:
    """As Codex R10 for ``ExtensionInterrupted``: the outer loader keeps the inner name."""

    started = tmp_path / "started"
    inner = _ext(
        tmp_path / "inner",
        "inner_ext",
        "import asyncio\n\n"
        "async def setup(aelix):\n"
        f"    open({str(started)!r}, 'w').close()\n"
        "    await asyncio.sleep(60)\n",
    )
    outer = _ext(
        tmp_path,
        "outer_ext",
        "from pathlib import Path\n\n"
        "from aelix_coding_agent.extensions.loader import load_extensions\n\n\n"
        "async def setup(aelix):\n"
        f"    await load_extensions([Path({str(inner)!r})])\n",
    )
    task = asyncio.ensure_future(load_extensions([outer]))
    for _ in range(500):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    named = interrupted_extension(caught.value)
    assert named is not None
    assert named.extension.endswith("inner_ext.py") and named.during == "setup()"


# --- fix round 1 (Codex H/M + verify S2/S3) ---------------------------------


async def _cancel_once_started(task: asyncio.Future[object], started: Path) -> None:
    for _ in range(500):
        if started.exists():
            break
        await asyncio.sleep(0.01)
    assert started.exists(), "the extension never started"
    task.cancel()


@pytest.mark.parametrize(
    ("body", "during"),
    [
        # A sync import / setup() the Ctrl+C lands in, which then fails.
        (
            "import asyncio\nasyncio.current_task().cancel()\nraise RuntimeError('after the ctrl-c')\n",
            "import",
        ),
        (
            "import asyncio\n\ndef setup(aelix):\n    asyncio.current_task().cancel()\n"
            "    raise RuntimeError('after the ctrl-c')\n",
            "setup()",
        ),
        (
            "import asyncio, sys\n\ndef setup(aelix):\n    asyncio.current_task().cancel()\n"
            "    sys.exit(3)\n",
            "setup()",
        ),
    ],
    ids=["sync-import-raises", "sync-setup-raises", "sync-setup-exits"],
)
async def test_a_cancel_in_a_step_that_then_fails_still_ends_the_load(
    tmp_path: Path, body: str, during: str
) -> None:
    """Before, the failure was recorded, the next extension loaded, and the cancel
    landed later, unmarked: a traceback (or, in -p, a model request first)."""

    held = _ext(tmp_path, "held_ext", body)
    after, ran = _marker(tmp_path, "after_ext")
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.ensure_future(load_extensions([held, after]))
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension is not None and named.extension.endswith("held_ext.py")
    assert named.during == during
    assert not ran.exists(), "the extension after the interrupted one loaded"


@pytest.mark.parametrize(
    "tail",
    [
        "    try:\n        await asyncio.sleep(60)\n    finally:\n        raise RuntimeError('cleanup')\n",
        # Fix round 2: the error's chain is the evidence, here and in a group.
        "    try:\n        await asyncio.sleep(60)\n    except asyncio.CancelledError:\n"
        "        raise RuntimeError('stopped')\n",
        "    async def child():\n        try:\n            await asyncio.sleep(60)\n"
        "        finally:\n            raise RuntimeError('child cleanup')\n"
        "    async with asyncio.TaskGroup() as tg:\n        tg.create_task(child())\n"
        "        await asyncio.sleep(60)\n",
    ],
    ids=["finally-raises-over-it", "raises-in-its-except", "taskgroup-child-raises-over-it"],
)
async def test_a_cancel_an_async_setup_did_not_pass_on_still_ends_the_load(
    tmp_path: Path, tail: str
) -> None:
    """Before, the run went on: the next extension loaded and, in -p, the model was asked."""

    started = tmp_path / "started"
    held = _ext(
        tmp_path,
        "held_ext",
        "import asyncio\n\n"
        "async def setup(aelix):\n"
        f"    open({str(started)!r}, 'w').close()\n" + tail,
    )
    after, ran = _marker(tmp_path, "after_ext")
    task = asyncio.ensure_future(load_extensions([held, after]))
    await _cancel_once_started(task, started)
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension is not None and named.extension.endswith("held_ext.py")
    assert named.during == "setup()"
    assert not ran.exists()


async def test_a_cancel_between_two_extensions_names_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SIGINT in the loader's own code, after A's setup() returned: B's async
    setup() received it and was named, or (B sync) it escaped the loader unmarked."""

    import aelix_coding_agent.extensions.loader as loader_mod

    first, first_ran = _marker(tmp_path, "first_ext")
    second = _ext(tmp_path, "second_ext", "import asyncio\n\nasync def setup(aelix):\n    await asyncio.sleep(0)\n")
    third, third_ran = _marker(tmp_path, "third_ext")
    real = loader_mod._entry_point_source_info
    fired: list[object] = []

    def _info(entry: object) -> object:
        # Runs after an extension's import and setup() have returned.
        if not fired:
            fired.append(entry)
            current = asyncio.current_task()
            assert current is not None
            current.cancel()  # what asyncio.run's SIGINT handler does
        return real(entry)

    monkeypatch.setattr(loader_mod, "_entry_point_source_info", _info)
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.ensure_future(load_extensions([first, second, third]))
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension is None
    assert str(named) == "interrupted while loading extensions"
    assert first_ran.exists() and not third_ran.exists()


async def test_a_cancel_after_the_last_extension_is_taken_by_the_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aelix_coding_agent.extensions.loader as loader_mod

    only, _ran = _marker(tmp_path, "only_ext")
    real = loader_mod._entry_point_source_info

    def _info(entry: object) -> object:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        return real(entry)

    monkeypatch.setattr(loader_mod, "_entry_point_source_info", _info)
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.ensure_future(load_extensions([only]))
    named = interrupted_extension(caught.value)
    assert named is not None and named.extension is None


async def test_a_cancel_from_before_the_loader_is_not_the_first_extensions(tmp_path: Path) -> None:
    """Pending when the loader began, it went into the first await — an async
    setup() — and that extension was named for a Ctrl+C it never saw."""

    first = _ext(tmp_path, "first_ext", "import asyncio\n\nasync def setup(aelix):\n    await asyncio.sleep(0)\n")

    async def _launch() -> object:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()  # requested before the loader runs, not yet delivered
        return await load_extensions([first])

    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.ensure_future(_launch())
    assert interrupted_extension(caught.value) is None


async def test_a_nested_loader_s_own_cancel_names_the_extension_it_ran_in(tmp_path: Path) -> None:
    """The inner loader names no extension for a cancel between its steps; the
    outer one names the extension whose setup() was running that loader."""

    import aelix_coding_agent.extensions.loader as loader_mod

    inner = _ext(tmp_path / "inner", "inner_ext", "def setup(aelix):\n    pass\n")
    outer = _ext(
        tmp_path,
        "outer_ext",
        "import asyncio\nfrom pathlib import Path\n\n"
        "import aelix_coding_agent.extensions.loader as L\n\n\n"
        "async def setup(aelix):\n"
        "    real = L._entry_point_source_info\n"
        "    def info(entry):\n"
        "        asyncio.current_task().cancel()\n"
        "        return real(entry)\n"
        "    L._entry_point_source_info = info\n"
        "    try:\n"
        f"        await L.load_extensions([Path({str(inner)!r})])\n"
        "    finally:\n"
        "        L._entry_point_source_info = real\n",
    )
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.ensure_future(loader_mod.load_extensions([outer]))
    named = interrupted_extension(caught.value)
    assert named is not None
    assert named.extension is not None and named.extension.endswith("outer_ext.py")
    assert named.during == "setup()"


async def test_a_cancel_during_a_deferred_activation_names_the_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify S2: the mark on a deferred pack's first activation had no test."""

    from aelix_coding_agent.extensions.loader import activate_pending_extension

    from tests.extensions.test_lazy_activation import _build

    started = tmp_path / "started"
    _harness, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_cancel_685",
        module_src=(
            "import asyncio\n\n"
            "async def setup(aelix):\n"
            f"    open({str(started)!r}, 'w').close()\n"
            "    await asyncio.sleep(60)\n"
        ),
    )
    task = asyncio.ensure_future(activate_pending_extension(runtime, "lazy-plug"))
    await _cancel_once_started(task, started)
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension == "lazy-plug" and named.during == "setup()"
    # Still recorded as the pack's failure, as before #685.
    assert runtime.activation_failures["lazy-plug"].startswith("CancelledError")


# --- fix round 2 (verify B1): a higher count alone is not a cancel ----------
#
# On 3.11 and 3.12 a stdlib TaskGroup whose child fails after the body finished
# leaves ``cancelling()`` one up with no cancel behind it (fixed in 3.13). Round
# 1 read that as a Ctrl+C: the load ended in a CancelledError, a traceback at
# launch. The TaskGroup rows bite on 3.11 and 3.12 only; ``_LEAVES_THE_COUNT_UP``
# leaves the loader the same state on every Python.

_TG_STARTS = (
    "import asyncio\n\n"
    "async def _start(ok):\n"
    "    await asyncio.sleep(0.01)\n"
    "    if not ok:\n"
    "        raise OSError('server b: connection refused')\n\n"
    "async def setup(aelix):\n"
)
_TG_CAUGHT = _TG_STARTS + (
    "    try:\n"
    "        async with asyncio.TaskGroup() as tg:\n"
    "            tg.create_task(_start(True))\n"
    "            tg.create_task(_start(False))\n"
    "    except* OSError:\n"
    "        pass\n"
)
_TG_UNCAUGHT = _TG_STARTS + (
    "    async with asyncio.TaskGroup() as tg:\n"
    "        tg.create_task(_start(True))\n"
    "        tg.create_task(_start(False))\n"
)
# The TaskGroup cancels the other task, whose ``finally`` raises over it: that
# cancel is in a member's chain, not in the group's.
_TG_OTHER_RAISES_OVER_ITS_CANCEL = _TG_STARTS.replace(
    "async def setup(aelix):\n",
    "async def _serve():\n"
    "    try:\n"
    "        await asyncio.sleep(60)\n"
    "    finally:\n"
    "        raise RuntimeError('server a: stop failed')\n\n"
    "async def setup(aelix):\n",
) + (
    "    async with asyncio.TaskGroup() as tg:\n"
    "        tg.create_task(_serve())\n"
    "        tg.create_task(_start(False))\n"
)
_LEAVES_THE_COUNT_UP = (
    "import asyncio\n\n"
    "async def setup(aelix):\n"
    "    asyncio.current_task().cancel()\n"
    "    try:\n"
    "        await asyncio.sleep(0)\n"
    "    except asyncio.CancelledError:\n"
    "        pass\n"
)


@pytest.mark.parametrize(
    "body",
    [_TG_CAUGHT, _TG_UNCAUGHT, _TG_OTHER_RAISES_OVER_ITS_CANCEL],
    ids=["caught", "uncaught", "uncaught-other-task-raises-over-its-cancel"],
)
async def test_a_taskgroup_failing_after_its_body_does_not_end_the_load(
    tmp_path: Path, body: str
) -> None:
    tg = _ext(tmp_path, "tg_ext", body)
    after, ran = _marker(tmp_path, "after_ext")
    # A task of its own: the count it leaves stays there.
    loaded = await asyncio.ensure_future(load_extensions([tg, after]))
    assert ran.exists(), "the extension after the TaskGroup one did not load"
    names = [Path(e.name).name for e in loaded.extensions]
    if body is _TG_CAUGHT:
        assert names == ["tg_ext.py", "after_ext.py"] and loaded.errors == []
    else:
        assert names == ["after_ext.py"]
        [failure] = loaded.errors
        assert failure.path.endswith("tg_ext.py")
        assert loaded.outcomes[0].error_type == "ExceptionGroup"


# A failure over a cancel the step made and undid itself: the cancel is in the
# error's chain, the count is back where it was.
_FAILS_OVER_ITS_OWN_CANCEL = (
    "import asyncio\n\n"
    "async def setup(aelix):\n"
    "    task = asyncio.current_task()\n"
    "    task.cancel()\n"
    "    try:\n"
    "        await asyncio.sleep(0)\n"
    "    except asyncio.CancelledError:\n"
    "        task.uncancel()\n"
    "        raise RuntimeError('server c: no answer')\n"
)
# The same from a TaskGroup whose child fails while the body waits. Not after
# ``_LEAVES_THE_COUNT_UP`` on 3.13+: that TaskGroup reads the count as a cancel
# from outside and delivers it again.
_TG_FAILS_IN_ITS_BODY = (
    "import asyncio\n\n"
    "async def _boom():\n"
    "    raise OSError('server c: connection refused')\n\n"
    "async def setup(aelix):\n"
    "    async with asyncio.TaskGroup() as tg:\n"
    "        tg.create_task(_boom())\n"
    "        await asyncio.sleep(60)\n"
)


@pytest.mark.parametrize(
    ("first", "second"),
    [(_LEAVES_THE_COUNT_UP, _FAILS_OVER_ITS_OWN_CANCEL), (_TG_CAUGHT, _TG_FAILS_IN_ITS_BODY)],
    ids=["count-up", "taskgroup"],
)
async def test_a_count_left_up_is_not_held_against_the_next_extension(
    tmp_path: Path, first: str, second: str
) -> None:
    """The next one fails with a cancel in its error's chain that it made itself:
    its failure, not a Ctrl+C."""

    a = _ext(tmp_path, "a_ext", first)
    b = _ext(tmp_path, "b_ext", second)
    c, c_ran = _marker(tmp_path, "c_ext")
    loaded = await asyncio.ensure_future(load_extensions([a, b, c]))
    assert [Path(e.name).name for e in loaded.extensions] == ["a_ext.py", "c_ext.py"]
    [failure] = loaded.errors
    assert failure.path.endswith("b_ext.py")
    assert c_ran.exists()


@pytest.mark.parametrize(
    "tail",
    [
        "    async with asyncio.timeout(0.01):\n        await asyncio.sleep(60)\n",
        "    try:\n        async with asyncio.timeout(0.01):\n            await asyncio.sleep(60)\n"
        "    except TimeoutError:\n        raise RuntimeError('server did not answer')\n",
    ],
    ids=["timeout-escapes", "raises-over-the-timeout"],
)
async def test_a_timeout_s_own_cancel_in_the_chain_is_not_a_ctrl_c(
    tmp_path: Path, tail: str
) -> None:
    held = _ext(tmp_path, "held_ext", _LEAVES_THE_COUNT_UP + tail)
    after, ran = _marker(tmp_path, "after_ext")
    loaded = await asyncio.ensure_future(load_extensions([held, after]))
    [failure] = loaded.errors
    assert failure.path.endswith("held_ext.py")
    assert ran.exists()


# aiohttp <= 3.10.5 and async-timeout <= 4.0.2 time out by cancelling the task
# without undoing it, then raise TimeoutError while that cancel is handled: with
# no ``from`` (async-timeout's __aexit__) or ``from None`` (aiohttp's
# TimerContext). The count stays up and the cancel is only the error's
# __context__. Fix round 2 read it as a Ctrl+C on every Python (verify r3 S-A).
_OLD_LIBRARY_TIMEOUT = (
    "import asyncio\n\n"
    "class _Timer:\n"
    "    async def __aenter__(self):\n"
    "        task = asyncio.current_task()\n"
    "        self._handle = asyncio.get_running_loop().call_later(0.01, task.cancel)\n\n"
    "    async def __aexit__(self, exc_type, exc, tb):\n"
    "        self._handle.cancel()\n"
    "        if exc_type is asyncio.CancelledError:\n"
    "            raise asyncio.TimeoutError@FROM@\n\n"
    "async def setup(aelix):\n"
    "    async with _Timer():\n"
    "        await asyncio.sleep(60)\n"
)


@pytest.mark.parametrize("raise_from", ["", " from None"], ids=["async-timeout", "aiohttp"])
async def test_an_old_library_s_timeout_is_not_a_ctrl_c(tmp_path: Path, raise_from: str) -> None:
    held = _ext(tmp_path, "held_ext", _OLD_LIBRARY_TIMEOUT.replace("@FROM@", raise_from))
    after, ran = _marker(tmp_path, "after_ext")
    loaded = await asyncio.ensure_future(load_extensions([held, after]))
    [failure] = loaded.errors
    assert failure.path.endswith("held_ext.py")
    assert loaded.outcomes[0].error_type == "TimeoutError"
    assert ran.exists()


@pytest.mark.parametrize("first", [_LEAVES_THE_COUNT_UP, _TG_CAUGHT], ids=["count-up", "taskgroup"])
async def test_a_cancel_after_a_count_left_up_still_ends_the_load(
    tmp_path: Path, first: str
) -> None:
    a = _ext(tmp_path, "a_ext", first)
    held = _ext(tmp_path, "held_ext", "import asyncio\n\ndef setup(aelix):\n    asyncio.current_task().cancel()\n")
    after, ran = _marker(tmp_path, "after_ext")
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.ensure_future(load_extensions([a, held, after]))
    named = interrupted_extension(caught.value)
    assert isinstance(named, ExtensionInterrupted)
    assert named.extension is not None and named.extension.endswith("held_ext.py")
    assert named.during == "setup()"
    assert not ran.exists()


async def test_a_count_rise_between_steps_with_no_cancel_behind_it_ends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The between-extensions check reads the same count (fix round 1, Codex M)."""

    import aelix_coding_agent.extensions.loader as loader_mod

    first, first_ran = _marker(tmp_path, "first_ext")
    second, second_ran = _marker(tmp_path, "second_ext")
    real_info = loader_mod._entry_point_source_info
    real_requests = loader_mod._cancel_requests
    raised: list[int] = []

    def _info(entry: object) -> object:
        # Runs in the loader's own code, after first_ext's setup() returned.
        raised.append(1)
        return real_info(entry)

    monkeypatch.setattr(loader_mod, "_entry_point_source_info", _info)
    monkeypatch.setattr(loader_mod, "_cancel_requests", lambda: real_requests() + len(raised[:1]))
    loaded = await asyncio.ensure_future(load_extensions([first, second]))
    assert [Path(e.name).name for e in loaded.extensions] == ["first_ext.py", "second_ext.py"]
    assert first_ran.exists() and second_ran.exists()


async def test_a_cancel_an_async_setup_caught_and_returned_from_is_not_seen(tmp_path: Path) -> None:
    """Stated limit (ADR-0284's #685 note): the count is all that is left of it,
    and the count is what a TaskGroup leaves on 3.11 and 3.12. The load goes on,
    as before #685."""

    started = tmp_path / "started"
    held = _ext(
        tmp_path,
        "held_ext",
        "import asyncio\n\n"
        "async def setup(aelix):\n"
        f"    open({str(started)!r}, 'w').close()\n"
        "    try:\n        await asyncio.sleep(60)\n    except asyncio.CancelledError:\n        pass\n",
    )
    after, ran = _marker(tmp_path, "after_ext")
    task = asyncio.ensure_future(load_extensions([held, after]))
    await _cancel_once_started(task, started)
    loaded = await task
    assert [Path(e.name).name for e in loaded.extensions] == ["held_ext.py", "after_ext.py"]
    assert ran.exists()
