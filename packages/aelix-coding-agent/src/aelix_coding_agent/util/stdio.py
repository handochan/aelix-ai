"""Std-stream encoding hardening for non-UTF-8 consoles and pipes (N-3).

Issue #110 P7. Windows is the motivating platform: the console defaults to a
legacy code page (cp949 / cp932 / cp936 / cp1252), and when stdout is
**redirected** — which is what CI, ``aelix -p > out.txt`` and ``| jq`` all do —
CPython picks the locale encoding rather than the console's. The CLI prints
box-drawing characters, arrows, em dashes and arbitrary model output, so a
``print()`` on such a stream raises ``UnicodeEncodeError`` and kills the process
mid-command. Measured on darwin under ``PYTHONIOENCODING=cp949``: ``aelix
--help``, ``aelix status``, ``aelix docs <topic>`` and ``aelix extension source
list`` each died with an unhandled traceback and exit 1.

Exactly one case gets its encoding rewritten — **a redirected output stream** —
because it is the only one where UTF-8 is unambiguously right:

- A **pipe or file has no code page.** Nothing downstream is bound to the
  locale, so UTF-8 is the only interoperable target — and it is what makes a CI
  log or a redirected artifact readable at all. Keeping cp949 there produces
  bytes no UTF-8 reader can decode, with ``?`` where the interesting characters
  were.
- A **real console on a legacy code page** genuinely cannot render the glyph.
  Re-encoding would produce mojibake, so the encoding is kept and only the error
  handler is relaxed: an unencodable character prints as ``\\u2588`` instead of
  ending the run.
- An **input stream** is never re-encoded at all — see below.

A stream already on UTF-8 is left completely alone, which makes this helper a
provable no-op on POSIX, on macOS, and on a modern Windows console (PEP 528
reports ``utf-8``). That property is what keeps it out of the way of the
existing suite.

``errors=`` is always passed explicitly. ``reconfigure(encoding=...)`` silently
resets the error handler to ``strict``, discarding CPython's stdout
``surrogateescape`` / stderr ``backslashreplace`` defaults — so the naive
one-liner would *introduce* ``UnicodeEncodeError`` on paths that work today
(a filename carrying a surrogate from ``os.fsdecode``).

Input streams keep their code page: choosing a decoder from BYTES is the only
way to serve both Windows input conventions, so that job belongs to
:func:`read_all_text` rather than to a blind ``reconfigure``. See its docstring
for why the declared encoding is tried first.

Streams are injected rather than discovered so the whole policy is testable on
Linux without a Windows runner — the same approach
``tests/tools/test_bash_shell_win32.py`` takes for shell resolution.
"""

from __future__ import annotations

import codecs
import sys
from collections.abc import Iterable
from typing import Any

# ``io.UnsupportedOperation`` subclasses both, and ``BrokenPipeError``
# subclasses ``OSError``. Deliberately NOT ``Exception``: a real bug in here
# should surface, not be swallowed at launch.
_RECONFIGURE_ERRORS = (AttributeError, ValueError, LookupError, OSError)

# ``codecs.lookup`` canonicalises the spellings CPython can produce —
# ``cp65001``, ``U8``, ``utf8``, ``UTF-8`` all resolve to ``utf-8``. Hand-rolled
# string munging misses those. ``utf-8-sig`` stays a DISTINCT name and is listed
# deliberately: it is already lossless, and rewriting it to plain ``utf-8``
# would silently drop a BOM the environment explicitly asked for.
_UTF8_NAMES = frozenset({"utf-8", "utf-8-sig"})

# Handlers that CANNOT raise, PER DIRECTION. "Not strict" is NOT the test:
# ``surrogateescape`` is total on DECODE but raises on ENCODE for any ordinary
# unencodable character — measured, ``"█"`` on a cp949 stream. It round-trips
# only the surrogates it created itself.
#
# A non-strict default is NOT exotic and NOT Windows-only.
# ``Python/initconfig.c::config_get_stdio_errors`` reaches ``surrogateescape``
# by three separate routes: UTF-8 mode (PEP 540), the legacy C/POSIX locale,
# and any PEP 538 locale-coercion target — which includes ``C.UTF-8``, the
# default locale in most container and CI images. Measured here: ambient
# ``LANG=C.UTF-8`` gives ``utf8_mode=0`` and ``errors=surrogateescape``, while
# ``LC_ALL=en_US.UTF-8`` gives ``strict``.
#
# So "preserve anything that is not strict" would have preserved a raising
# handler on ordinary Linux CI too. It stayed invisible there only because a
# UTF-8 stream encodes everything and never reaches the raise. TWO conditions
# must coincide, and only the second is Windows-shaped:
#
#   1. a preserved non-total handler  — near-universal in CI
#   2. a codec that can actually fail — a legacy code page
#
# Windows supplies (2). Its ``#else`` arm returns ``surrogateescape``
# unconditionally ("On Windows, always use surrogateescape by default" —
# verbatim, unchanged in 3.11 and 3.12), so a REDIRECTED Windows stdout carries
# the ANSI code page *with* it. A Windows CONSOLE is unaffected: PEP 528
# reports ``utf-8`` and the early return above fires. ``stderr`` was never at
# risk; it defaults to ``backslashreplace``.
#
# The Windows half is read from CPython source, NOT observed on a Windows host
# — the first ``windows-latest`` run is what confirms it.
_TOTAL_ENCODE_ERRORS = frozenset(
    {"replace", "backslashreplace", "xmlcharrefreplace", "namereplace", "ignore"}
)
_TOTAL_DECODE_ERRORS = frozenset(
    {"replace", "backslashreplace", "surrogateescape", "ignore"}
)


def _is_utf8(encoding: str | None) -> bool:
    """True when ``encoding`` already names a lossless UTF-8 codec."""

    if not encoding:
        return False
    try:
        return codecs.lookup(encoding).name in _UTF8_NAMES
    except LookupError:
        return False


def harden_stream(stream: Any, *, reading: bool = False) -> str | None:
    """Re-encode one std stream in place; return the encoding now in force.

    Returns :data:`None` when the stream was left untouched — it is
    :data:`None` itself (``pythonw``), has no ``reconfigure`` (``io.StringIO``,
    pytest's ``DontReadFromInput``, a test double), or reconfiguration failed.

    ``reading`` marks an input stream. Its encoding is deliberately left
    ALONE: the byte-level choice belongs to :func:`read_all_text`. Forcing
    UTF-8 here would be a *regression* — a genuinely cp949-encoded prompt
    (Notepad's "ANSI" save, ``type prompt.txt | aelix -p``) decoded correctly
    before and would become replacement characters after. Only the error
    handler is relaxed, so a stray byte can never kill the run.

    The handler is chosen per DIRECTION, not by "is it strict?" — see
    :data:`_TOTAL_ENCODE_ERRORS`. Preserving ``surrogateescape`` on an output
    stream would leave Windows exactly as broken as it is today.
    """

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return None

    try:
        encoding = stream.encoding
    except _RECONFIGURE_ERRORS:
        return None

    # Already lossless — never touch it. This early return is what makes the
    # helper inert on every platform that was already correct.
    if _is_utf8(encoding):
        return encoding

    try:
        is_tty = bool(stream.isatty())
    except _RECONFIGURE_ERRORS:
        is_tty = False

    current = getattr(stream, "errors", None)
    if reading:
        errors = current if current in _TOTAL_DECODE_ERRORS else "replace"
    else:
        errors = current if current in _TOTAL_ENCODE_ERRORS else "backslashreplace"

    try:
        if reading or is_tty:
            # An input stream, or a legacy console: keep the code page. Korean
            # still renders natively in a cp949 terminal, and a cp949 pipe
            # still decodes correctly. Only stop it from raising.
            reconfigure(errors=errors)
            return encoding

        reconfigure(encoding="utf-8", errors=errors)
    except _RECONFIGURE_ERRORS:
        # Replaced/closed/detached stream, or a codec lookup failure. Never
        # fail launch over output hygiene.
        return None

    return getattr(stream, "encoding", None)


def read_all_text(stream: Any) -> str:
    """Read an input stream to EOF, decoding the code page first and UTF-8 second.

    Two ordinary Windows cases disagree, and no rule separates them perfectly:

    - Genuine code-page bytes — Notepad's "ANSI" save, ``type prompt.txt |``,
      the output of any ``chcp 949`` program. These decoded correctly before
      N-3 and must keep doing so.
    - UTF-8 bytes arriving on a cp949 stdin — ``echo 안녕 | aelix -p``, any
      modern editor, anything piped from another UTF-8 program. This is the
      case that raised ``UnicodeDecodeError`` and killed the run.

    The declared encoding is tried FIRST because that makes this function
    **strictly additive**: every input that decoded before still decodes
    identically, and only the inputs that previously CRASHED take the new
    path. Ordering it the other way round looks appealing — UTF-8 is
    self-validating — but it is not safe, because short code-page strings can
    themselves be valid UTF-8 and would then be silently mistranslated:

        cp949 "책" -> c3 a5 -> valid UTF-8 "å"
        cp949 "짜" -> c2 a5 -> valid UTF-8 "¥"

    The reverse direction has no such overlap for the case that matters:
    UTF-8-encoded Hangul is *invalid* cp949 (``0xec`` is not a legal lead
    byte), so the fallback fires exactly when it should.

    Ambiguity is inherent, not eliminated. What is guaranteed is that nothing
    which worked before now breaks, and that the read never raises.
    """

    buffer = getattr(stream, "buffer", None)
    raw: bytes | None = None
    if buffer is not None:
        try:
            candidate = buffer.read()
        except _RECONFIGURE_ERRORS:
            candidate = None
        if isinstance(candidate, bytes):
            raw = candidate

    if raw is None:
        # No byte layer to inspect (pytest's DontReadFromInput, an embedder's
        # StringIO). The text layer is all there is.
        return str(stream.read())

    declared = getattr(stream, "encoding", None)
    if declared and not _is_utf8(declared):
        try:
            return raw.decode(declared)
        except (UnicodeDecodeError, LookupError):
            pass

    try:
        # ``utf-8-sig`` strips a BOM if present and is identical to ``utf-8``
        # otherwise, so it is the right decoder either way: a leading U+FEFF
        # would otherwise ride into the prompt as a literal character.
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    # Neither codec fits. Never raise on the way in — a mangled prompt beats a
    # traceback the user cannot act on.
    return raw.decode("utf-8", errors="replace")


def harden_stdio(
    streams: Iterable[tuple[Any, bool]] | None = None,
) -> None:
    """Apply :func:`harden_stream` to stdin, stdout and stderr.

    Call it as the first statement of a **console-script entry point** only.
    Reconfiguring the interpreter's std streams is a process-global side
    effect, so it belongs at the same boundary as ``_inject_truststore`` /
    ``load_dotenv`` — never in ``_async_main`` or a ``run_*`` mode, which the
    test suite calls in-process (``cli/runtime_bootstrap.py:31-34`` states the
    rule).

    stdin must be reconfigured before its first read; afterwards CPython raises
    ``io.UnsupportedOperation``. The console entries all call this before any
    argument parsing, so that ordering holds.
    """

    if streams is None:
        streams = (
            (sys.stdin, True),
            (sys.stdout, False),
            (sys.stderr, False),
        )

    for stream, reading in streams:
        harden_stream(stream, reading=reading)


__all__ = ["harden_stdio", "harden_stream", "read_all_text"]
