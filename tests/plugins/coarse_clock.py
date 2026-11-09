"""Opt-in pytest plugin: a ``time.monotonic`` that ticks the way Windows' does on Python 3.12 and older.

Load it by name, from the repository root, and only then::

    uv run python -m pytest -p tests.plugins.coarse_clock <tests>

It is never collected (the name does not match ``test_*.py``) and nothing loads
it unasked: no ``conftest.py`` imports it, no entry point names it, and
``tests/test_coarse_clock_plugin.py`` drives it in a child process only.
``scripts/preflight.py --windows-clock`` runs the touched tests under it (#733).

WHAT IT REPRODUCES (#672, #673). On Windows, CPython 3.12 and older read
``time.monotonic`` from a clock that advances in steps of 15.625 ms, and
``time.get_clock_info("monotonic").resolution`` says so. asyncio's event loop
takes that resolution as its clock resolution and runs every timer due within
it as ready. So two reads in quick succession are equal, ``asyncio.sleep(0.01)``
can finish in the same loop turn it started in, and repeated short sleeps merge.
The #672/#673 branch failed one test on the Windows py3.11 and py3.12 legs only
and then hung there until the step limit; with this plugin both happened on
macOS in one run, before another CI round was spent.

Installed in ``pytest_configure`` and taken back in ``pytest_unconfigure``: while
it is on, ``time.monotonic()`` returns the real value rounded down to a multiple
of 15.625 ms and ``get_clock_info("monotonic").resolution`` is ``0.015625``.

WHAT IT DOES NOT REPRODUCE. Only callers that look ``time.monotonic`` up on the
module when they call it see the coarse clock (the event loop's ``time()``
does). A ``from time import monotonic`` bound before pytest configured keeps the
real clock, and ``time.monotonic_ns``, ``time.perf_counter`` and ``time.time``
are untouched. Nothing else about Windows is modelled: not its scheduler or
``select`` granularity, its processes, signals or job objects, its paths or its
encodings. Python 3.13 and later on Windows use a fine-grained clock, so this
models the 3.11 and 3.12 legs only.
"""

from __future__ import annotations

import math
import time
import types
from typing import Any

TICK = 0.015625
"""One Windows clock tick in seconds (1/64 s)."""

_saved: dict[str, Any] = {}


def coarse_monotonic() -> float:
    """The real monotonic clock rounded down to a whole number of ticks."""

    return math.floor(_saved["monotonic"]() / TICK) * TICK


def coarse_get_clock_info(name: str) -> Any:
    """``time.get_clock_info`` with the monotonic clock's resolution set to one tick."""

    info = _saved["get_clock_info"](name)
    if name != "monotonic":
        return info
    return types.SimpleNamespace(
        implementation=info.implementation,
        monotonic=info.monotonic,
        adjustable=info.adjustable,
        resolution=TICK,
    )


def install() -> None:
    """Replace the two ``time`` functions; a second call changes nothing."""

    if _saved:
        return
    _saved["monotonic"] = time.monotonic
    _saved["get_clock_info"] = time.get_clock_info
    time.monotonic = coarse_monotonic
    time.get_clock_info = coarse_get_clock_info


def uninstall() -> None:
    """Put the real functions back."""

    if not _saved:
        return
    time.monotonic = _saved.pop("monotonic")
    time.get_clock_info = _saved.pop("get_clock_info")


def pytest_configure(config: Any) -> None:
    install()


def pytest_unconfigure(config: Any) -> None:
    uninstall()


def pytest_report_header(config: Any) -> str:
    return "coarse clock: time.monotonic in 15.625 ms ticks (tests.plugins.coarse_clock)"
