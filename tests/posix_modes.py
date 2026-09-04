"""POSIX mode-bit assertions, and what NTFS does to them (#211).

On Windows the ``mode`` argument to ``os.open`` and ``os.chmod`` toggles one
bit — ``FILE_ATTRIBUTE_READONLY`` — and nothing else. There are no per-file
group/other bits to set, so ``os.stat`` reports 0o666 for every writable file
and 0o777 for every directory no matter what the product code asked for. This
is not a product defect and no product change can turn it green: five surfaces
(``session/fs.py``, ``_export_html/format.py``, ``aelix_agents/prompt_file.py``,
``tui/login_wizard.py``, ``util/tools_manager.py``) all open at 0o600 and all
read back 0o666 there. Access control on NTFS is an ACL question with no mode
-bit counterpart, so a 0o600 assertion is *unreachable* on that platform rather
than merely unsatisfied — which is the exception the repo already skips for
(``tests/conftest.py`` ``mkdir_or_skip``: "genuinely unreachable rather than
merely untested"), against the usual rule of injecting the condition and
running everywhere.

Two shapes, because "unreachable" applies to the assertion, not always to the
test:

1. :data:`posix_modes_only` — a whole-test ``skipif`` for a case that verifies
   *nothing but* mode bits (or whose setup ``chmod`` does not stick either, so
   the precondition it needs cannot be staged at all). Skipping it throws away
   no Windows coverage because there was none to keep.
2. :func:`assert_mode` — for a case that also verifies content, ordering or
   schema. Only the mode comparison is neutralised; everything else keeps
   running on Windows, which is where the real regressions in these files will
   show up.

The precedent is ``tests/oauth/test_auth_storage.py:64``,
``@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")``
— the same platform, the same 0600 property, on ``auth.json``.

The Windows branch of :func:`assert_mode` is an explicit ``if`` that returns,
never a ``try``/``except`` around the comparison: a swallowed exception would
also swallow a missing file, a wrong path, or a real failure on POSIX.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

__all__ = ["POSIX_MODES", "assert_mode", "posix_modes_only"]

POSIX_MODES = sys.platform != "win32"  # NTFS has no mode bits; see #211

posix_modes_only = pytest.mark.skipif(
    not POSIX_MODES, reason="NTFS has no POSIX mode bits (#211)"
)


def assert_mode(path: str | Path, expected: int) -> None:
    """Assert ``path``'s permission bits are ``expected``; bits-only no-op on Windows.

    ``path`` must exist on every platform; the rest of the calling test still
    runs there — see the module docstring for why only the bit comparison is
    dropped.
    """

    # stat() on every platform: the file's EXISTENCE is still asserted on
    # Windows, only the bits are not.
    actual = stat.S_IMODE(Path(path).stat().st_mode)
    if not POSIX_MODES:
        return  # NTFS reports 0o666/0o777 regardless of what was requested.
    assert actual == expected, (
        f"{path} is {oct(actual)}, expected {oct(expected)}"
    )
