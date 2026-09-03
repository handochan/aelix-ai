"""Pi-parity source scans must decode as UTF-8, not as the ambient code page.

Ten cases in this directory died on ``windows-latest`` with::

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x81 in position
    32445: character maps to <undefined>

The scans read repository files — ``harness/core.py`` and the pinned Pi
fixtures — through ``Path.read_text`` with no ``encoding=``. That defers to the
interpreter's locale encoding, which is UTF-8 on macOS and Linux and **cp1252**
on the GitHub Windows runner. The files are UTF-8 and full of ``—``, ``→`` and
subscript digits (``₁`` is ``e2 82 81`` — that 0x81 is the byte in the message),
so the read raised before a single parity assertion ran.

This is a *file*-read bug and is NOT what issue #110's N-3 fixed. ``harden_stdio``
covers ``sys.stdout`` / ``sys.stderr`` / ``sys.stdin`` only, and
``util/stdio.read_all_text`` is the wrong tool here on purpose: its contract is
"try the DECLARED code page first, UTF-8 second", which is right for stdin bytes
of unknown provenance and wrong for a repo file of known provenance — it would
turn a loud ``UnicodeDecodeError`` into silent mojibake (``—`` read as ``â€"``),
and a source scan that mojibakes its input reports a false PASS. The fix is an
explicit ``encoding="utf-8"`` at each read site.

WHY THERE IS NO ``skipif``. Same reasoning as
``tests/cli/test_stdio_encoding_win32.py``: the failure is driven by the
*default encoding*, not by ``sys.platform``, so it can be injected. The seam is
``io.text_encoding`` — ``pathlib.Path.read_text`` routes its ``encoding``
argument through it before opening, so substituting a legacy code page for
``None`` reproduces the runner exactly. Measured on darwin with the fix
reverted::

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x81 in position
    31721: character maps to <undefined>

— the same byte and the same codec; the position differs only because CI checks
out CRLF.

Patching ``locale.getpreferredencoding`` does NOT work and was measured inert:
since 3.11 ``TextIOWrapper`` resolves the locale encoding in C via
``_Py_GetLocaleEncodingObject`` and never calls back into the ``locale`` module.
A test built on that would have passed with the fix removed and pinned nothing.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).parent

# The ANSI code page on ``windows-latest``. Every file these helpers read
# contains at least one byte that is undefined in it.
_RUNNER_CODE_PAGE = "cp1252"


@contextmanager
def _ambient_encoding(code_page: str) -> Iterator[list[str | None]]:
    """Make every ``encoding=``-less text read resolve to ``code_page``.

    Yields the list of encodings that were actually requested, so a caller can
    assert the seam was reached. If a future ``pathlib`` stops consulting
    ``io.text_encoding`` the list comes back empty and the case fails loudly
    rather than passing vacuously.
    """

    real = io.text_encoding
    requested: list[str | None] = []

    def _fake(encoding: str | None = None, stacklevel: int = 2) -> str:
        requested.append(encoding)
        if encoding is None:
            return code_page
        return real(encoding, stacklevel + 1)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(io, "text_encoding", _fake)
        yield requested


def _load(filename: str) -> Any:
    """Import a sibling test module by path.

    Pytest's rootdir doesn't expose ``tests`` as a package, so this is the same
    ``spec_from_file_location`` route ``test_phase_2_2_strict_superset.py``
    takes to reuse ``test_phase_2_1_strict_superset``.
    """

    name = f"_read_encoding_{Path(filename).stem}"
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The four modules CI actually failed in, and the helper that did the reading.
_SCAN_HELPERS: tuple[tuple[str, str], ...] = (
    ("test_phase_2_1_strict_superset.py", "_emit_scope_text"),
    ("test_phase_2_2_strict_superset.py", "_harness_core_text"),
    ("test_phase_4_6_strict_superset.py", "_load_fixture"),
    ("test_phase_4_8_strict_superset.py", "_load_fixture"),
)


@pytest.mark.parametrize(("filename", "helper_name"), _SCAN_HELPERS)
def test_scan_helper_reads_utf8_under_a_legacy_ambient_encoding(
    filename: str, helper_name: str
) -> None:
    """The windows-latest failure, reproduced by injection and pinned."""

    helper: Callable[[], Any] = getattr(_load(filename), helper_name)

    with _ambient_encoding(_RUNNER_CODE_PAGE) as requested:
        # Raised UnicodeDecodeError here on windows-latest, and raises here on
        # any platform once the explicit encoding is removed.
        value = helper()

    assert requested, (
        f"{filename}::{helper_name} never reached io.text_encoding — the "
        f"injection is inert and this case proves nothing"
    )
    # Not merely "not None": naming the code page explicitly would decode
    # without raising and hand the scan mojibake.
    assert set(requested) == {"utf-8"}, (
        f"{filename}::{helper_name} read with {sorted(map(str, set(requested)))}; "
        f"every repo read must name utf-8"
    )
    assert value


def test_emit_scope_text_is_not_mojibake_under_a_legacy_ambient_encoding() -> None:
    """The em dash must survive as one character, not as ``â€"``."""

    emit_scope_text: Callable[[], str] = _load(
        "test_phase_2_1_strict_superset.py"
    )._emit_scope_text

    with _ambient_encoding(_RUNNER_CODE_PAGE):
        source = emit_scope_text()

    assert "—" in source
    assert "â€" not in source


# ``.read_text(`` with no ``encoding=`` before the closing paren. ``[^()]``
# spans newlines, so a wrapped call is matched too.
_AMBIENT_READ = re.compile(r"\.read_text\((?![^()]*encoding=)")


def test_no_pi_parity_module_reads_a_file_at_the_ambient_encoding() -> None:
    """Guard for the other 24 sites, which mojibake rather than raise.

    Only ``harness/core.py`` and two fixtures carry a byte cp1252 rejects; the
    rest of this directory's reads decode on the runner and quietly produce the
    wrong characters, which for a source scan is the worse failure. Held to
    ``read_text`` because that is the only mechanism in use here — a bare
    ``open()`` or ``.readlines()`` would need an explicit encoding too, and
    there are currently none to check.
    """

    offenders: list[str] = []
    for path in sorted(_HERE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _AMBIENT_READ.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.name}:{line}")

    assert offenders == [], (
        f"read_text() without encoding= resolves to the runner's code page: "
        f"{offenders}"
    )
