"""Std-stream hardening against a non-UTF-8 code page (N-3, issue #110 P7).

Windows is the target, but nothing here needs a Windows runner: the failure is
driven by the stream's *encoding*, not by ``sys.platform``, so a
``TextIOWrapper`` built over ``BytesIO`` with ``encoding="cp949"`` reproduces it
exactly on Linux and macOS. That is the same injection style
``tests/tools/test_bash_shell_win32.py`` uses, and it is why these cases carry
no ``skipif`` — a test that only runs on the platform we cannot run on is not a
regression guard.

The bug being pinned, measured on darwin before the fix:

    $ PYTHONIOENCODING=cp949 aelix --help        -> UnicodeEncodeError, exit 1
    $ echo "안녕하세요" | PYTHONIOENCODING=cp949 aelix -p
                                                 -> UnicodeDecodeError, exit 1
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_coding_agent.util.stdio import harden_stdio, harden_stream, read_all_text

# A single line exercising every class of character the CLI actually emits:
# box drawing (TUI frames), an arrow and an em dash (help text), and Korean
# (user content).
SAMPLE = "─│┌ → — 안녕하세요"

# The code pages fail on DISJOINT sets, which is why de-Unicode-ing the source
# literals was never an option — no single ASCII-safe rewrite satisfies all of
# them. Measured:
#
#     char   cp949   cp1252   cp932
#     ≠      ok      FAIL     ok
#     ─ → │  ok      FAIL     ok
#     —      FAIL    ok       FAIL
#     █ ═ ✓  FAIL    FAIL     FAIL
CP1252_ONLY = "≠"  # kills ``aelix --help`` on a Western code page
CP949_ONLY = "—"  # the em dash in ``extension source list`` rows
UNENCODABLE_ANYWHERE = "█"  # a TUI block glyph; no legacy code page has it


def _wrapper(encoding: str, *, errors: str = "strict") -> io.TextIOWrapper:
    """A writable non-tty stream on ``encoding`` — i.e. a pipe or a file."""

    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors=errors)


# === the redirected case: a pipe has no code page, so UTF-8 is the target ===


@pytest.mark.parametrize("codepage", ["cp949", "cp1252", "cp932", "cp936"])
def test_redirected_stream_is_reencoded_to_utf8(codepage: str) -> None:
    """The CI case. Bytes on the pipe must be readable UTF-8, not code page."""

    stream = _wrapper(codepage)
    harden_stream(stream)

    assert stream.encoding == "utf-8"

    stream.write(SAMPLE)
    stream.flush()
    raw = stream.buffer.getvalue()  # type: ignore[attr-defined]
    assert raw.decode("utf-8") == SAMPLE


@pytest.mark.parametrize(
    ("codepage", "char"),
    [("cp1252", CP1252_ONLY), ("cp949", CP949_ONLY), ("cp932", UNENCODABLE_ANYWHERE)],
)
def test_redirected_stream_survives_the_char_that_kills_that_codepage(
    codepage: str, char: str
) -> None:
    """Each code page dies on a different character; all three must survive."""

    stream = _wrapper(codepage)
    harden_stream(stream)

    stream.write(char)  # must not raise
    stream.flush()
    assert stream.buffer.getvalue().decode("utf-8") == char  # type: ignore[attr-defined]


def test_redirected_stream_never_left_on_strict() -> None:
    """Regression guard for the reconfigure trap.

    ``reconfigure(encoding=...)`` silently resets ``errors`` to ``strict``. A
    later "simplify this to one line" refactor that drops the explicit
    ``errors=`` would reintroduce ``UnicodeEncodeError`` on a lone surrogate —
    which Windows ``os.fsdecode`` can produce from a filename.
    """

    stream = _wrapper("cp949")
    harden_stream(stream)

    # Assert the DOCUMENTED handler, not merely "not strict" — "replace" also
    # satisfies != "strict" while throwing away the byte the rationale calls
    # "lossless and debuggable".
    assert stream.errors == "backslashreplace"
    stream.write("file-\udcff.txt")  # must not raise
    stream.flush()


@pytest.mark.parametrize(
    "handler", ["replace", "backslashreplace", "xmlcharrefreplace", "ignore"]
)
def test_a_handler_that_cannot_raise_is_preserved(handler: str) -> None:
    """We upgrade the encoding without discarding a workable intent."""

    stream = _wrapper("cp949", errors=handler)
    harden_stream(stream)

    assert stream.encoding == "utf-8"
    assert stream.errors == handler


@pytest.mark.parametrize("handler", ["strict", "surrogateescape"])
def test_a_handler_that_can_still_raise_is_replaced(handler: str) -> None:
    """The blocker Codex caught, and the reason "not strict" is not the test.

    ``surrogateescape`` is total on DECODE but raises on ENCODE for an
    ordinary unencodable character — and it is CPython's Windows default for
    the std streams. Preserving it would have left the one platform this
    module exists for still crashing, while every macOS/Linux test passed.
    """

    stream = _FakeTty(io.BytesIO(), encoding="cp949", errors=handler)

    harden_stream(stream)

    assert stream.errors == "backslashreplace"
    stream.write(UNENCODABLE_ANYWHERE)  # must not raise
    stream.flush()


# === the console case: a legacy tty genuinely cannot render the glyph ===


class _FakeTty(io.TextIOWrapper):
    """A ``TextIOWrapper`` that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def test_legacy_console_keeps_its_codepage_and_only_relaxes_errors() -> None:
    """Re-encoding a real cp949 console to UTF-8 would produce mojibake.

    So the tty branch keeps the encoding — Korean still renders natively — and
    only stops the stream from raising.
    """

    stream = _FakeTty(io.BytesIO(), encoding="cp949", errors="strict")

    harden_stream(stream)

    assert stream.encoding == "cp949"
    assert stream.errors == "backslashreplace"

    # ``≠`` IS encodable in cp949 (0xa1 0xc1) — a block glyph is not.
    stream.write(UNENCODABLE_ANYWHERE)  # must degrade, not raise
    stream.flush()
    # backslashreplace keeps the codepoint recoverable in a bug report, which
    # "?" would not.
    assert b"\\u2588" in stream.buffer.getvalue()  # type: ignore[attr-defined]


def test_korean_still_renders_natively_on_a_cp949_console() -> None:
    """The reason the tty branch exists at all."""

    stream = _FakeTty(io.BytesIO(), encoding="cp949", errors="strict")
    harden_stream(stream)

    stream.write("안녕하세요")
    stream.flush()
    raw = stream.buffer.getvalue()  # type: ignore[attr-defined]
    assert raw.decode("cp949") == "안녕하세요"


# === the no-op property that keeps the existing suite green ===


@pytest.mark.parametrize("spelling", ["utf-8", "UTF-8", "utf8", "UTF_8"])
def test_utf8_streams_are_left_completely_alone(spelling: str) -> None:
    """POSIX, macOS and a modern Windows console (PEP 528) all land here.

    Both the encoding AND the error handler must be untouched — CPython's
    stdout default is ``surrogateescape`` and stderr's is ``backslashreplace``,
    and silently narrowing either would be a regression on the platforms that
    were already correct.
    """

    stream = _wrapper(spelling, errors="surrogateescape")
    before = (stream.encoding, stream.errors)
    calls: list[object] = []
    monkeypatch_free = stream.reconfigure

    def _spy(**kwargs: object) -> None:  # pragma: no cover - must never run
        calls.append(kwargs)
        monkeypatch_free(**kwargs)

    stream.reconfigure = _spy  # type: ignore[method-assign]

    harden_stream(stream)

    assert calls == [], "a UTF-8 stream must not be reconfigured at all"
    assert (stream.encoding, stream.errors) == before


# === stdin: decode from bytes, because the two Windows cases disagree ===


def test_piped_stdin_decodes_utf8_under_a_legacy_codepage() -> None:
    """``echo "안녕하세요" | aelix -p`` died with UnicodeDecodeError.

    UTF-8 bytes arriving on a cp949 stdin is the common modern case — any
    editor, any UTF-8 producer upstream.
    """

    payload = "안녕하세요 테스트"
    stream = io.TextIOWrapper(
        io.BytesIO(payload.encode("utf-8")), encoding="cp949", errors="strict"
    )

    assert read_all_text(stream) == payload


def test_genuine_codepage_bytes_still_decode_correctly() -> None:
    """The regression guard that shaped the design.

    ``type 요청.txt | aelix -p`` where the file is a Notepad "ANSI" save. This
    decoded CORRECTLY before N-3, and an earlier draft that simply forced
    stdin to UTF-8 turned it into '?? ???׸? ??????' — a silent corruption
    strictly worse than the crash being fixed. UTF-8 is tried first, the code
    page second, so both cases win.
    """

    payload = "이 버그를 고쳐줘"
    stream = io.TextIOWrapper(
        io.BytesIO(payload.encode("cp949")), encoding="cp949", errors="strict"
    )

    assert read_all_text(stream) == payload


@pytest.mark.parametrize(("char", "utf8_lookalike"), [("책", "å"), ("짜", "¥")])
def test_codepage_bytes_that_are_ALSO_valid_utf8_decode_as_the_codepage(
    char: str, utf8_lookalike: str
) -> None:
    """The blocker Codex caught in a UTF-8-first draft.

    Short cp949 strings can themselves be valid UTF-8 — cp949 "책" is
    ``c3 a5``, which UTF-8 reads as "å". A UTF-8-first rule silently
    mistranslates them, and the earlier "genuine codepage" test missed it
    because its phrase happened to be invalid UTF-8. Trying the DECLARED
    encoding first is what makes the function strictly additive.
    """

    raw = char.encode("cp949")
    assert raw.decode("utf-8") == utf8_lookalike, "fixture must stay ambiguous"

    stream = io.TextIOWrapper(io.BytesIO(raw), encoding="cp949", errors="strict")

    assert read_all_text(stream) == char


def test_a_utf8_bom_is_stripped_rather_than_riding_into_the_prompt() -> None:
    """A BOM decoded as a literal U+FEFF would become part of the prompt."""

    stream = io.TextIOWrapper(
        io.BytesIO(b"\xef\xbb\xbf" + "안녕".encode()), encoding="cp949", errors="strict"
    )

    assert read_all_text(stream) == "안녕"


def test_stdin_that_is_neither_utf8_nor_its_codepage_never_raises() -> None:
    """Last resort: a mangled prompt the user can see beats a traceback."""

    stream = io.TextIOWrapper(
        io.BytesIO(b"\xff\xfe\x00\x01 junk"), encoding="ascii", errors="strict"
    )

    assert "junk" in read_all_text(stream)  # must not raise


def test_read_all_text_falls_back_to_the_text_layer_without_a_buffer() -> None:
    """pytest's ``DontReadFromInput`` and embedder StringIOs have no bytes."""

    assert read_all_text(io.StringIO("plain text")) == "plain text"


def test_input_stream_encoding_is_never_rewritten() -> None:
    """harden_stream must leave the code page for read_all_text to use."""

    stream = io.TextIOWrapper(io.BytesIO(b""), encoding="cp949", errors="strict")

    harden_stream(stream, reading=True)

    assert stream.encoding == "cp949"
    assert stream.errors != "strict"


def test_a_legacy_console_on_the_read_side_is_relaxed_not_reencoded() -> None:
    """Interactive cp949 console: typed Korean must keep decoding natively."""

    stream = _FakeTty(io.BytesIO("안녕".encode("cp949")), encoding="cp949", errors="strict")

    harden_stream(stream, reading=True)

    assert stream.encoding == "cp949"
    assert stream.read() == "안녕"


def test_reconfigure_after_a_first_read_is_swallowed() -> None:
    """harden_stdio's docstring names this ordering hazard; pin it.

    CPython raises io.UnsupportedOperation once a read has started. It
    subclasses both ValueError and OSError, so the guard catches it — but
    nothing asserted that until now.
    """

    stream = io.TextIOWrapper(io.BytesIO(b"abc"), encoding="cp949", errors="strict")
    stream.read(1)

    assert harden_stream(stream, reading=True) is None  # must not raise


# === guards: a helper that fails at launch is worse than the bug ===


def test_a_stream_without_reconfigure_is_ignored() -> None:
    """``io.StringIO`` and pytest's ``DontReadFromInput`` have no reconfigure."""

    assert harden_stream(io.StringIO()) is None
    assert harden_stream(None) is None


def test_a_write_only_test_double_is_ignored() -> None:
    """Shaped like the fakes in ``tests/cli/test_first_run_onboarding.py``."""

    class _Double:
        encoding = "cp949"

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return False

    assert harden_stream(_Double()) is None


def test_a_closed_stream_never_raises() -> None:
    stream = _wrapper("cp949")
    stream.close()

    assert harden_stream(stream) is None


def test_a_detached_stream_never_raises() -> None:
    stream = _wrapper("cp949")
    stream.detach()

    assert harden_stream(stream) is None


def test_harden_stdio_visits_all_three_streams() -> None:
    """stdin included — the defect that made this test file necessary."""

    seen: list[tuple[str, bool]] = []

    class _Probe:
        encoding = "cp949"

        def __init__(self, name: str) -> None:
            self.name = name

        def isatty(self) -> bool:
            return False

        def reconfigure(self, **kwargs: object) -> None:
            seen.append((self.name, "encoding" in kwargs))

    harden_stdio(
        (
            (_Probe("stdin"), True),
            (_Probe("stdout"), False),
            (_Probe("stderr"), False),
        )
    )

    assert [name for name, _ in seen] == ["stdin", "stdout", "stderr"]
    # Only the two OUTPUT streams get their encoding rewritten.
    assert [rewrote for _, rewrote in seen] == [False, True, True]


# === wiring: defining the helper is inert unless the entry point calls it ===


def test_main_sync_hardens_before_anything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering is load-bearing: stdin cannot be reconfigured after its first
    read, and every later step can emit a diagnostic to stdout."""

    from aelix_coding_agent.cli import entry

    order: list[str] = []
    monkeypatch.setattr(entry, "harden_stdio", lambda: order.append("stdio"))
    monkeypatch.setattr(entry, "_inject_truststore", lambda: order.append("inject"))
    monkeypatch.setattr(entry, "load_dotenv", lambda: order.append("dotenv"))
    monkeypatch.setattr(entry, "register_providers", lambda: order.append("providers"))
    monkeypatch.setattr(entry.asyncio, "run", lambda coro: (coro.close(), 0)[1])
    monkeypatch.setattr(entry.sys, "argv", ["aelix", "--version"])

    with pytest.raises(SystemExit):
        entry.main_sync()

    assert order == ["stdio", "inject", "dotenv", "providers"]


def test_umbrella_main_hardens_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``python -m aelix`` — the third console entry, and the RPC child argv."""

    import aelix.__main__ as umbrella

    order: list[str] = []
    monkeypatch.setattr(umbrella, "harden_stdio", lambda: order.append("stdio"))
    monkeypatch.setattr(umbrella.asyncio, "run", lambda coro: order.append("run") or coro.close())

    umbrella.main(["--mode", "interactive"])

    assert order[0] == "stdio"


def test_server_main_sync_hardens_before_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The third console script — uncovered by the first pass."""

    # A hard import on purpose: aelix-server is a workspace member and is
    # always installed, so importorskip here would let the guard vanish
    # silently — the failure mode issue #203 is about.
    from aelix_server import main as server_main

    order: list[str] = []
    monkeypatch.setattr(server_main, "harden_stdio", lambda: order.append("stdio"))
    monkeypatch.setattr(server_main, "main", lambda: order.append("boot"))

    server_main.main_sync()

    assert order == ["stdio", "boot"]


# === end-to-end: the real binary, no Windows host required ===


@pytest.mark.parametrize("codepage", ["cp949", "cp1252"])
def test_real_cli_help_survives_a_legacy_codepage(
    codepage: str, tmp_path: Path
) -> None:
    """``PYTHONIOENCODING`` reproduces the Windows locale on any platform.

    Two assertions, and the second is the one the first pass failed: exiting 0
    is not enough if the artifact is code-page bytes with ``?`` where the
    interesting characters were. A CI log nobody can decode is exactly the
    noise N-3 exists to remove.
    """

    # cwd is pinned to a scratch dir so the child finds no project ``.env``:
    # the assertion must not depend on which credentials a developer happens
    # to have (house pattern: tests/cli/test_pipe_robustness.py:488).
    env = {**os.environ, "PYTHONIOENCODING": codepage}
    proc = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--help"],
        env=env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    proc.stdout.decode("utf-8")  # raises UnicodeDecodeError if still code page


def test_read_piped_stdin_decodes_through_read_all_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring guard for the stdin half.

    There is deliberately NO end-to-end subprocess test for this. Measured:
    reaching ``_read_piped_stdin`` (entry.py:1961) with a hermetic child is
    easy, but every hermetic child then dies at model selection
    (entry.py:3061) with output identical for an empty and a populated stdin —
    and the crash it would otherwise show is already prevented by the error
    handler alone, so "no traceback" passes even with the decode reverted.
    That is precisely the vacuous-pass shape issue #203 exists about, so the
    guarantee is pinned where it is actually observable: the decode is unit
    tested above, and the wiring is asserted here.
    """

    import asyncio

    from aelix_coding_agent.cli import entry

    payload = "이 버그를 고쳐줘"
    seen: list[object] = []

    def _fake_read_all_text(stream: object) -> str:
        seen.append(stream)
        return payload

    monkeypatch.setattr(entry, "read_all_text", _fake_read_all_text)
    monkeypatch.setattr(entry.sys.stdin, "isatty", lambda: False)

    result = asyncio.run(entry._read_piped_stdin(required=False))

    assert result == payload
    assert seen == [entry.sys.stdin], "must decode the real sys.stdin"
