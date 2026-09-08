"""#239 — the call sites really go through the shared decoder.

Every case here replaces :func:`win32_output_fallbacks` with a fixed
``("cp949",)`` and pushes REAL Korean bytes through a REAL call site. The
module-level name is what makes that possible: the decoder looks it up on its
own module at call time, so one ``monkeypatch.setattr`` turns a POSIX box into
the reported Korean Windows one for the length of the test.

Without these, reverting any single site to
``.decode("utf-8", errors="replace")`` would leave the suite green on every
leg — the decoder's own unit tests never touch a site, and CI's windows-latest
runner is ``en-US`` (ACP 1252, OEM 437), so it never executes the CJK arm
either.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from aelix_agents.print_channel import StderrRing
from aelix_ai.tools import ToolExecutionContext
from aelix_ai.utils import _child_output
from aelix_ai.utils._child_output import decode_child_output
from aelix_coding_agent.tools.bash import ExecExitResult, create_bash_tool

# ``"위치 줄:1 문자:14"`` — the fragment the issue reported, in the code page a
# Korean Windows PowerShell writes.
_KOREAN = "위치 줄:1 문자:14"
_CP949 = _KOREAN.encode("cp949")


@pytest.fixture
def korean_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_child_output, "win32_output_fallbacks", lambda: ("cp949",))


#: ``"ok: 한글"`` with its last byte gone (``6f 6b 3a 20 ed 95 9c ea b8``):
#: what a kill, an abort or #232's drain cap leaves when it lands inside the
#: final character. cp949 ACCEPTS the severed ``ed 95 9c ea b8`` and spells it
#: ``한湲``; the ragged tail claim keeps the severed character one U+FFFD.
#:
#: These cases ran on ``("cp437",)`` until #239's final pass, because a
#: single-byte page accepts every byte there is and so made every claim
#: visible. That page is now offered nothing at all (``_child_output``'s
#: ACCEPTANCE), which makes a claim on it a provable no-op — measured, all four
#: claim shapes on six single-byte chains equal ``errors="replace"`` over 351
#: exhaustive windows and 20000 random blobs — so a cut end is only observable
#: on a DBCS page now, and these cases install the one the issue was reported
#: from.
_CUT_UTF8 = "ok: 한글".encode()[:-1]


async def test_the_bash_tool_returns_readable_korean(korean_console, tmp_path) -> None:
    """``create_bash_tool``'s join-and-decode, driven through a fake ``operations``.

    A fake rather than a real spawn because the bytes have to be exactly the
    ones the issue reported; what is under test is the decode at the tool, not
    the shell that produced them.
    """

    class _Ops:
        async def exec(self, command, cwd, *, on_data, **kwargs):
            on_data(_CP949)
            return ExecExitResult(exit_code=0)

    tool = create_bash_tool(str(tmp_path), {"operations": _Ops()})
    result = await tool.execute({"command": "whatever"}, ToolExecutionContext(tool_call_id="t1"))

    text = result.content[0].text
    assert _KOREAN in text
    assert "�" not in text


async def test_the_bash_tool_reads_its_cut_tail_as_ragged(korean_console, tmp_path) -> None:
    """``ragged_tail=True`` at the bash tool, on the console that shows it.

    The tool's buffer ends wherever the read stopped, and a timeout kill, an
    abort or #232's idle drain cap can stop it mid-character. cp949 ACCEPTS the
    truncated ``ed 95 9c ea b8`` and spells it ``한湲``, so without the claim
    the severed character comes back as a confident wrong one where ``replace``
    had a single U+FFFD.
    """

    cut = _CUT_UTF8

    class _Ops:
        async def exec(self, command, cwd, *, on_data, **kwargs):
            on_data(cut)
            return ExecExitResult(exit_code=0)

    tool = create_bash_tool(str(tmp_path), {"operations": _Ops()})
    result = await tool.execute({"command": "whatever"}, ToolExecutionContext(tool_call_id="t2"))

    assert "ok: 한\ufffd" in result.content[0].text
    # Without the flag the same bytes come back as ``ok: 한湲``.
    assert decode_child_output(cut, fallbacks=("cp949",)) == "ok: 한湲"


@pytest.mark.skipif(sys.platform == "win32", reason="drives the POSIX `sh -c` candidate")
def test_a_bang_command_returns_readable_korean(korean_console) -> None:
    """``!command`` gets the DECODER even though it gets no UTF-8 preamble.

    Its stdout is a credential returned verbatim, so nothing may be prepended
    to the command it runs — but a value that came back as mojibake was never
    usable either.
    """

    import aelix_ai.oauth._resolve_config as rc

    script = f"import sys; sys.stdout.buffer.write({_CP949!r})"
    outcome = rc._run_shell_command(f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}")

    assert outcome is not None
    code, text = outcome
    assert code == 0
    assert text == _KOREAN


def test_the_subagent_stderr_ring_decodes_its_ragged_head_as_utf8(korean_console) -> None:
    """``StderrRing`` trims byte-exactly, so its head is a ragged window.

    Without ``ragged_head=True`` the cut sequence fails UTF-8 strict and the code
    page gets bytes that were a real character's tail — here ``95 9c`` comes
    back as cp949's ``븳`` instead of the two U+FFFD ``replace`` renders.

    The CP949 half after the space is what tells this apart from dropping the
    whole ``decode_child_output`` call: plain ``replace`` answers
    ``��국어오류 ��ġ ��:1 ����:14``. Three answers, three
    different strings, so both halves of the site are pinned.

    The second window pins the site's OTHER claim. Review 3 deleted
    ``ragged_tail=True`` from this site and from ``RpcClient.get_stderr`` and
    measured the FULL suite still green with byte-identical counts (10390
    passed / 20 skipped): the head claim died, the tail claim was unprotected.
    A window cut at BOTH ends separates all four claim shapes AND ``replace``,
    five inputs to five different strings — re-measured 2026-09-09 on
    darwin/CPython 3.12.13, and each of the four is asserted below.
    """

    window = "한국어오류".encode() + b" " + _CP949
    ring = StderrRing(max_bytes=len(window) - 1)
    ring.feed(b"xxxx")
    ring.feed(window)

    text = ring.text()

    # The trim ate ``한``'s lead byte; its two orphaned continuation bytes are
    # one U+FFFD each, exactly as ``.decode("utf-8", "replace")`` renders them.
    assert text == f"��국어오류 {_KOREAN}"
    assert decode_child_output(window[1:], fallbacks=("cp949",)) == f"븳국어오류 {_KOREAN}"
    assert window[1:].decode("utf-8", "replace") == "��국어오류 ��ġ ��:1 ����:14"

    # --- and the tail, which a dying child leaves half-written ---------------
    both_ends = window + b" " + _CUT_UTF8
    ring = StderrRing(max_bytes=len(both_ends) - 1)
    ring.feed(b"xxxx")
    ring.feed(both_ends)

    cut = both_ends[1:]
    # Head cut AND tail cut: the severed ``ed 95 9c ea b8`` keeps its ``한`` and
    # spends ONE U+FFFD on the byte that is missing its partner.
    assert ring.text() == f"��국어오류 {_KOREAN} ok: 한�"
    # Drop ``ragged_tail`` and cp949 spells that fragment ``한湲``, confidently.
    assert decode_child_output(cut, ragged_head=True, fallbacks=("cp949",)) == (
        f"��국어오류 {_KOREAN} ok: 한湲"
    )
    # Drop ``ragged_head`` instead and the orphaned ``95 9c`` is cp949's ``븳``.
    assert decode_child_output(cut, ragged_tail=True, fallbacks=("cp949",)) == (
        f"븳국어오류 {_KOREAN} ok: 한�"
    )
    assert decode_child_output(cut, fallbacks=("cp949",)) == f"븳국어오류 {_KOREAN} ok: 한湲"
    # And dropping the whole call is a fifth answer, not this one.
    assert cut.decode("utf-8", "replace") == "��국어오류 ��ġ ��:1 ����:14 ok: 한�"


# === the sites the review found unpinned ====================================
#
# The three cases above covered the bash tool, the ``!command`` and
# ``StderrRing``; the review reverted the other nine conversions in one
# mutation and measured the FULL suite still green (10355 passed / 17 skipped,
# byte-identical counts). Each case below is that mutation's cheapest
# falsifier: a real call site, the issue's own CP949 bytes, and the Korean
# console the fixture installs.

def _writes(raw: bytes) -> str:
    """A shell one-liner that puts exactly ``raw`` on stdout, for the real spawns."""

    return f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        f"import sys;sys.stdout.buffer.write(bytes.fromhex('{raw.hex()}'))"
    )


#: The same bytes as a shell one-liner, for the sites that really spawn.
_WRITES_CP949 = _writes(_CP949)


def test_the_subagent_line_assembler_decodes_each_line(korean_console) -> None:
    """``LineAssembler.feed`` and ``.flush`` — the subagent stdout pump.

    ``feed`` claims NO cut end: a line it emits is bounded by the newline the
    child wrote, and an over-budget line is dropped whole rather than cut.
    """

    from aelix_agents.stream import LineAssembler

    assembler = LineAssembler(max_line_bytes=4096)

    assert assembler.feed(_CP949 + b"\n") == [_KOREAN]
    assert assembler.feed(_CP949) == []  # no newline yet
    assert assembler.flush() == [_KOREAN]


def test_the_contained_run_output_decodes_like_a_child(korean_console) -> None:
    """ADR-0238's SITE-1, ``extensions/api.py::_decode_output``."""

    from aelix_coding_agent.extensions.api import _decode_output

    assert _decode_output(_CP949) == _KOREAN
    assert _decode_output(_CP949 + b"\r\n") == _KOREAN + "\n"


def test_the_fd_enumerator_offers_a_pickable_candidate(korean_console, monkeypatch) -> None:
    """``tui/completion.py::_fd_enumerate`` — a U+FFFD candidate matches nothing."""

    import subprocess

    from aelix_coding_agent.tui import completion as completion_mod

    monkeypatch.setattr(
        completion_mod,
        "run_contained",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0] if a else [], 0, stdout=b"src/" + _CP949 + b".py\n", stderr=b""
        ),
    )

    assert completion_mod._fd_enumerate("fd", Path(".")) == [f"src/{_KOREAN}.py"]


def test_a_git_clone_that_failed_reports_readable_korean(korean_console) -> None:
    """``cli/extension_catalog.py`` — both stderr renderings, one per branch."""

    import subprocess

    from aelix_coding_agent.cli import extension_catalog as ec

    def _exited_non_zero(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 128, stdout=b"", stderr=_CP949)

    with pytest.raises(ec.CatalogError) as non_zero:
        ec.fetch_catalog("git+ssh://host/x.git", git_runner=_exited_non_zero)
    assert _KOREAN in str(non_zero.value)

    def _timed_out(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 60.0, stderr=_CP949)

    with pytest.raises(ec.CatalogError) as timed_out:
        ec.fetch_catalog("git+ssh://host/y.git", git_runner=_timed_out)
    assert _KOREAN in str(timed_out.value)


def test_the_rpc_client_stderr_window_is_decoded_ragged(korean_console) -> None:
    """``RpcClient.get_stderr`` — and its ragged claims, which nothing pinned.

    Seeded exactly like ``StderrRing``'s case: the window's head is a cut UTF-8
    sequence. Without ``ragged_head=True`` the whole run fails strict and is offered
    to cp949, which accepts it — so the assertion below is what tells the flag
    apart from its absence, not just the conversion.

    The second window pins ``ragged_tail=True``, which review 3 deleted from
    this site and from ``StderrRing.text`` with the FULL suite staying green
    (10390 passed / 20 skipped, byte-identical): only the head half was
    protected. ``_drain_stderr``'s trim is byte-exact at both ends, so this
    buffer can be cut at both.
    """

    from aelix_coding_agent.rpc.rpc_client import RpcClient

    window = ("한국어오류".encode() + b" " + _CP949)[1:]

    client = RpcClient.__new__(RpcClient)
    client._stderr_buffer = bytearray(window)

    assert client.get_stderr() == f"��국어오류 {_KOREAN}"
    # Dropping the flag and dropping the whole call are two different wrong
    # answers, and neither is the one above.
    assert decode_child_output(window, fallbacks=("cp949",)) == f"븳국어오류 {_KOREAN}"
    assert window.decode("utf-8", "replace") == "��국어오류 ��ġ ��:1 ����:14"

    # --- and the tail ------------------------------------------------------
    cut = ("한국어오류".encode() + b" " + _CP949 + b" " + _CUT_UTF8)[1:]
    client._stderr_buffer = bytearray(cut)

    assert client.get_stderr() == f"��국어오류 {_KOREAN} ok: 한�"
    # Without ``ragged_tail`` the severed ``ea b8`` becomes a confident ``湲``;
    # without ``ragged_head`` the orphaned ``95 9c`` becomes ``븳``; without
    # either, both. Five inputs, five different strings.
    assert decode_child_output(cut, ragged_head=True, fallbacks=("cp949",)) == (
        f"��국어오류 {_KOREAN} ok: 한湲"
    )
    assert decode_child_output(cut, ragged_tail=True, fallbacks=("cp949",)) == (
        f"븳국어오류 {_KOREAN} ok: 한�"
    )
    assert decode_child_output(cut, fallbacks=("cp949",)) == f"븳국어오류 {_KOREAN} ok: 한湲"
    assert cut.decode("utf-8", "replace") == "��국어오류 ��ġ ��:1 ����:14 ok: 한�"


@pytest.mark.skipif(sys.platform == "win32", reason="the argv is quoted for a POSIX shell")
async def test_a_subprocess_hook_returns_readable_korean(korean_console, tmp_path) -> None:
    """``extensions/subprocess_hooks.py`` — a hook is a child like any other."""

    from aelix_coding_agent.extensions.subprocess_hooks import run_hook_subprocess

    outcome = await run_hook_subprocess(
        _WRITES_CP949, "{}", timeout_ms=20_000, cwd=str(tmp_path)
    )

    assert outcome.stdout == _KOREAN


@pytest.mark.skipif(sys.platform == "win32", reason="the argv is quoted for a POSIX shell")
async def test_the_repl_bang_escape_returns_readable_korean(korean_console, tmp_path) -> None:
    """``cli/repl.py`` — the TUI/CLI ``!`` escape, a REAL spawn through the shell."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.streaming import Model
    from aelix_coding_agent.cli.repl import handle_user_bash

    harness = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    try:
        output = await handle_user_bash(
            harness, _WRITES_CP949, exclude_from_context=True, cwd=str(tmp_path)
        )
    finally:
        await harness.dispose()

    assert output == _KOREAN


@pytest.mark.skipif(sys.platform == "win32", reason="the argv is quoted for a POSIX shell")
async def test_the_rpc_exec_returns_readable_korean(korean_console, tmp_path) -> None:
    """``rpc/rpc_mode.py::_handle_bash`` — the RPC client's ad-hoc command."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.streaming import Model
    from aelix_coding_agent.rpc.rpc_mode import RpcCommandBash, _handle_bash

    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), cwd=str(tmp_path))
    )
    try:
        response = await _handle_bash(harness, RpcCommandBash(command=_WRITES_CP949, id="r"))
    finally:
        await harness.dispose()

    data = response.data
    assert isinstance(data, dict)
    assert _KOREAN in str(data["output"])


@pytest.mark.skipif(sys.platform == "win32", reason="the argv is quoted for a POSIX shell")
async def test_run_cancellable_returns_readable_korean(korean_console) -> None:
    """``tools/_subprocess.py`` — the rg/fd helper, pinned on the POSIX leg too.

    Its rewritten U+FFFD case only reaches the fallback on win32, so off
    Windows nothing noticed this conversion at all.
    """

    from aelix_coding_agent.tools._subprocess import run_cancellable

    result = await run_cancellable(
        [sys.executable, "-c", f"import sys;sys.stdout.buffer.write(bytes.fromhex('{_CP949.hex()}'))"]
    )

    assert result is not None
    assert result[0] == _KOREAN


# === the sites the #239 CROSS-REVIEW found unclaimed =========================
#
# Codex finding 5 plus the four the verification pass added. Each of these
# decodes a buffer whose TAIL is a byte-exact cut — a kill, a timeout, a drain
# cap, a child that died mid-line — and each used to hand that cut straight to
# the console code page. They install the ``en-US`` chain because a single-byte
# page is the one that accepts a severed character and spells it; on cp949 the
# floor answers and nothing is visible.


@pytest.mark.skipif(sys.platform == "win32", reason="the argv is quoted for a POSIX shell")
async def test_the_rpc_bash_path_reads_its_cut_tail_as_ragged(korean_console, tmp_path) -> None:
    """``rpc/rpc_mode.py::_handle_bash`` — the one bash path with an abort signal.

    It registers an :class:`AbortSignal` with the harness, so it is the site
    whose buffer really does end wherever the kill landed
    (``_watch_signal`` -> ``_end_the_tree`` -> ``_drain_after_the_exit``), and
    it was the one site of the three that never claimed it. The child here
    writes the cut bytes directly rather than being killed, because what is
    under test is the decode and not the kill — ``tests/tools`` owns that.
    """

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.streaming import Model
    from aelix_coding_agent.rpc.rpc_mode import RpcCommandBash, _handle_bash

    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), cwd=str(tmp_path))
    )
    try:
        response = await _handle_bash(harness, RpcCommandBash(command=_writes(_CUT_UTF8), id="r"))
    finally:
        await harness.dispose()

    data = response.data
    assert isinstance(data, dict)
    assert str(data["output"]).strip() == "ok: 한\ufffd"
    # Without the claim the same bytes come back spelled by cp949.
    assert decode_child_output(_CUT_UTF8, fallbacks=("cp949",)) == "ok: 한湲"


@pytest.mark.skipif(sys.platform == "win32", reason="the argv is quoted for a POSIX shell")
async def test_the_repl_bang_escape_reads_its_cut_tail_as_ragged(korean_console, tmp_path) -> None:
    """``cli/repl.py`` — no signal and no timeout, but #232's drain cap cuts it.

    ``_drain_to_the_end`` ends the read at a READ boundary once the root has
    exited and the pipe has been idle, so a helper the command backgrounded can
    still be writing. Rarer than the abort path, same repair.
    """

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.streaming import Model
    from aelix_coding_agent.cli.repl import handle_user_bash

    harness = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    try:
        output = await handle_user_bash(
            harness, _writes(_CUT_UTF8), exclude_from_context=True, cwd=str(tmp_path)
        )
    finally:
        await harness.dispose()

    assert output.strip() == "ok: 한\ufffd"


def test_a_git_clone_that_timed_out_reads_its_partial_stderr_as_ragged(korean_console) -> None:
    """``cli/extension_catalog.py`` — the TIMEOUT branch only.

    ``run_contained``'s ``TimeoutExpired`` carries "everything the reader has
    read so far", which ends at an arbitrary byte. The non-zero-exit branch one
    line below gets a COMPLETED stream and stays unclaimed, which is why only
    one of the two changed.
    """

    import subprocess

    from aelix_coding_agent.cli import extension_catalog as ec

    def _timed_out(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 60.0, stderr=_CUT_UTF8)

    with pytest.raises(ec.CatalogError) as timed_out:
        ec.fetch_catalog("git+ssh://host/y.git", git_runner=_timed_out)

    assert "ok: 한\ufffd" in str(timed_out.value)
    assert "한湲" not in str(timed_out.value)


async def test_an_extension_exec_that_timed_out_reads_its_cut_output_as_ragged(
    korean_console, monkeypatch, tmp_path
) -> None:
    """``extensions/api.py`` — ``_decode_output`` on the ``TimeoutExpired`` legs.

    ``run_contained`` is read as a module global here exactly as the dispatch
    test reads it, so raising from a stand-in reaches the timeout branch
    without spawning anything. The SUCCESS path two lines up keeps the plain
    decode: there the child chose where its output ended.

    The branch decodes ``exc.stdout`` and ``exc.stderr`` on two separate lines,
    so it takes two cases to falsify both claims.
    """

    import subprocess

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.streaming import Model
    from aelix_coding_agent.extensions import api as api_mod

    async def _run(*, output: bytes, stderr: bytes):
        def _timed_out(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 1.0, output=output, stderr=stderr)

        monkeypatch.setattr(api_mod, "run_contained", _timed_out)

        runtime = api_mod._ExtensionRuntime()
        ext = api_mod.Extension(name="ext239")
        api = api_mod.ExtensionAPI(ext, runtime)
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="m", api="anthropic"),
                extensions=[ext],
                runtime=runtime,
                cwd=str(tmp_path),
            )
        )
        try:
            return await api.exec("anything", [])
        finally:
            await harness.dispose()

    # BOTH legs, one case each. The review mutated ``ragged_tail`` off the
    # ``exc.stderr`` line and the whole suite stayed green (401 passed in the
    # reviewer's run), because only ``exc.stdout`` was ever driven.
    on_stdout = await _run(output=_CUT_UTF8, stderr=b"")
    assert on_stdout.code == 124
    assert on_stdout.stdout == "ok: 한\ufffd"

    on_stderr = await _run(output=b"", stderr=_CUT_UTF8)
    assert on_stderr.code == 124
    assert on_stderr.stderr == "ok: 한\ufffd"

    # The success path is deliberately unclaimed, and that is a different answer.
    assert api_mod._decode_output(_CUT_UTF8) == "ok: 한湲"


def test_the_line_assembler_claims_a_cut_tail_only_when_it_flushes(korean_console) -> None:
    """``aelix_agents/stream.py`` — ``flush`` is the carve-out, ``feed`` is not.

    A line ``feed`` emits ended where the child wrote ``\n``. The line
    ``flush`` emits has NO terminator, which is the whole reason the method
    exists — "a child that dies mid-line (SIGKILL, a crash) leaves bytes with
    no terminator" — so its last character can be half a character. The class
    docstring used to assert the ``feed`` rule for both.
    """

    from aelix_agents.stream import LineAssembler

    assembler = LineAssembler(max_line_bytes=4096)
    assembler.feed(_CUT_UTF8)

    assert assembler.flush() == ["ok: 한\ufffd"]

    # ``feed``'s own line is newline-bounded and keeps the plain decode, which
    # is a DIFFERENT answer for the same bytes — so this pins both halves.
    fed = LineAssembler(max_line_bytes=4096)
    assert fed.feed(_CUT_UTF8 + b"\n") == ["ok: 한湲"]
