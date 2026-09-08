"""#239 — what a child's bytes become, run by run.

Every test here runs on POSIX. The win32 answer is driven through
:func:`win32_output_fallbacks`'s ``device_encoding`` seam and through the
``fallbacks`` argument, because the codecs the real thing names (``oem``, and a
console page CPython may have no codec for) do not exist off Windows — measured
2026-09-08 on darwin/CPython 3.12.13: ``codecs.lookup("oem")`` raises
``LookupError: unknown encoding: oem``.
"""

from __future__ import annotations

import os
import random
import sys

import pytest
from aelix_ai.utils._child_output import (
    _is_multibyte_page,
    decode_child_output,
    win32_output_fallbacks,
)

# The issue's own bytes: ``"위치 줄:1 문자:14"`` as a Korean Windows PowerShell
# writes it. Every byte of the mojibake in #239's body is this buffer put
# through ``.decode("utf-8", errors="replace")``.
_ISSUE_BYTES = "위치 줄:1 문자:14".encode("cp949")


def test_cp949_powershell_error_is_readable() -> None:
    out = decode_child_output(_ISSUE_BYTES, fallbacks=("cp949",))

    assert out == "위치 줄:1 문자:14"
    assert "�" not in out
    # What the model was handed before this change, character for character.
    assert _ISSUE_BYTES.decode("utf-8", errors="replace") == "��ġ ��:1 ����:14"


def test_a_utf8_run_beside_a_codepage_run_keeps_both() -> None:
    """The reason the codec is chosen per RUN and not per buffer.

    A mixed buffer is not hypothetical on the reported box: the bash tool's
    child inherits one ``stderr=STDOUT`` pipe, so once the preamble has put
    PowerShell on UTF-8 a native child (git, uv) that ignored the console page
    still writes CP949 into the same buffer. Flipping the WHOLE buffer to the
    fallback destroys the half that was already right — measured, this buffer
    comes back as ``�븳湲� 오류`` under ``.decode("cp949", "replace")``.
    """

    mixed = "한글".encode() + b" " + "오류".encode("cp949")

    assert decode_child_output(mixed, fallbacks=("cp949",)) == "한글 오류"
    assert mixed.decode("cp949", errors="replace") == "�븳湲� 오류"


def test_utf8_wins_over_a_codepage_that_would_also_accept_it() -> None:
    """UTF-8 is probed first, and the order is load-bearing, not cosmetic.

    ``"문자"`` in UTF-8 is ``eb ac b8 ec 9e 90``, and cp949 accepts all six as
    three of its own characters (``臾몄옄``). The reverse hardly ever happens —
    of 256 single bytes cp949 rejects 128 while cp437/cp850/cp866 reject none
    — so "does it decode as UTF-8" is the discriminating question and has to be
    asked first.
    """

    utf8_also_valid_cp949 = "문자".encode()

    assert utf8_also_valid_cp949.decode("cp949") == "臾몄옄"
    assert decode_child_output(utf8_also_valid_cp949, fallbacks=("cp949",)) == "문자"


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX identity claim")
def test_posix_default_is_byte_identical_to_replace() -> None:
    """A.6: off win32 the new path is the old call, and the DEFAULT proves it.

    The ``fallbacks`` argument is omitted on purpose. Passing ``fallbacks=()``
    would pin the empty chain and say nothing about which chain this platform
    actually gets; deleting the platform guard from
    :func:`win32_output_fallbacks` has to turn this red.
    """

    assert win32_output_fallbacks() == ()

    for data in (
        b"",
        b"plain ascii\n",
        "한글 ok\n".encode(),
        b"\xff\xfe binary \x00\n",
        "안녕".encode()[:-1],
        _ISSUE_BYTES,
        os.urandom(2048),
    ):
        assert decode_child_output(data) == data.decode("utf-8", errors="replace")
        assert decode_child_output(
            data, ragged_head=True, ragged_tail=True
        ) == data.decode("utf-8", errors="replace")


def test_ragged_matches_replace_exhaustively() -> None:
    """A.5: the head/tail rules may never change what a no-fallback decode says.

    Exhaustive over EVERY window of a mixed UTF-8/ASCII core — each one is a
    byte-exact trim of the kind :class:`StderrRing` and ``RpcClient`` take —
    plus the three buffers the rev-1 rule got wrong: ``b"abc\\xed\\xa0"`` is
    invalid rather than incomplete and yields TWO U+FFFD, and a trimmed emoji
    leaves THREE leading continuation bytes, more than the "up to 3" cap that
    rule assumed.
    """

    core = "안녕 hi 한글!".encode()
    for i in range(len(core) + 1):
        for j in range(i, len(core) + 1):
            window = core[i:j]
            assert decode_child_output(
                window, ragged_head=True, ragged_tail=True, fallbacks=()
            ) == window.decode("utf-8", errors="replace"), window

    for window in (b"abc\xed\xa0", "😀".encode()[1:] + b"abc", b"\xa0\xa0\xa0\xa0abc"):
        assert decode_child_output(
            window, ragged_head=True, ragged_tail=True, fallbacks=()
        ) == window.decode("utf-8", errors="replace")
    assert (
        decode_child_output(b"abc\xed\xa0", ragged_head=True, ragged_tail=True, fallbacks=())
        == "abc��"
    )

    rng = random.Random(239)
    for _ in range(512):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
        assert decode_child_output(
            blob, ragged_head=True, ragged_tail=True, fallbacks=()
        ) == blob.decode("utf-8", errors="replace"), blob


def test_ragged_hands_the_untrimmed_run_to_the_fallback_only_when_both_ends_ate_it() -> None:
    """The head trim is a UTF-8 retry, not a rewrite of the run — CONDITIONALLY.

    cp949's lead-byte range is ``0x81-0xFD``, which CONTAINS the whole UTF-8
    continuation range, so a cp949 buffer whose first byte looks like a
    continuation byte is a real character. The both-ends guard hands it back
    untrimmed and cp949 reads it.

    THAT GUARD ONLY FIRES WHEN BOTH ENDS ATE BYTES, and the head strip alone
    can empty the run: ``suffix`` is set by ``if at_tail and core:``, so a run
    the head strip consumed entirely never reaches it. 3504 of the 11172
    cp949-encodable modern Hangul syllables (31.4%, measured 2026-09-09 on
    darwin/CPython 3.12.13) have BOTH bytes in ``0x80-0xBF`` and are destroyed
    that way. ``"꽃"`` is not one of them and ``"가"`` is — which is why both
    are here. This case pinned only ``"꽃"`` and stated the general rule in its
    docstring, so it went on passing while the rule stopped holding: it was
    pinning the one shape that still worked.

    The behaviour is kept, not reverted — ``_recover_ragged_run``'s docstring
    carries the trade and its measurements — but both directions are pinned so
    that a future change has to choose one on purpose.
    """

    survives = "꽃".encode("cp949")  # ``b2 c9``: the trail byte is above 0xBF
    assert 0x80 <= survives[0] <= 0xBF  # ``b2``: a UTF-8 continuation byte too
    assert (
        decode_child_output(survives, ragged_head=True, ragged_tail=True, fallbacks=("cp949",))
        == "꽃"
    )

    lost = "가".encode("cp949")  # ``b0 a1``: BOTH bytes are continuation bytes
    assert all(0x80 <= byte <= 0xBF for byte in lost)
    assert (
        decode_child_output(lost, ragged_head=True, ragged_tail=True, fallbacks=("cp949",))
        == "��"
    )
    # It is the HEAD strip that eats it; the tail claim alone costs nothing.
    assert decode_child_output(lost, ragged_tail=True, fallbacks=("cp949",)) == "가"
    assert decode_child_output(lost, fallbacks=("cp949",)) == "가"


def test_binary_never_raises() -> None:
    """The floor: a decoder in front of every child's output may not throw."""

    blob = os.urandom(4096)
    for fallbacks in ((), ("cp949",), ("cp437", "cp949"), ("oem",)):
        assert isinstance(decode_child_output(blob, fallbacks=fallbacks), str)
        assert isinstance(
            decode_child_output(
                blob, ragged_head=True, ragged_tail=True, fallbacks=fallbacks
            ),
            str,
        )


def test_an_unknown_codec_name_is_skipped_not_raised() -> None:
    """``LookupError`` is in the except clause, and POSIX CI is why.

    ``oem`` and ``mbcs`` exist only on Windows, and ``os.device_encoding`` can
    name a ``cp<N>`` CPython has no codec for even there — ``util/stdio.py``
    already wraps ``codecs.lookup`` for exactly that. ``cp1200`` (Windows'
    number for UTF-16LE) has no CPython codec on ANY platform. Without the
    ``LookupError`` arm this call raises.

    ``undefined`` is the second arm, and it is why the clause says
    ``UnicodeError`` rather than ``UnicodeDecodeError`` (the cross-review of
    this module found it): ``codecs.lookup("undefined")`` SUCCEEDS, and its
    decode then raises the BASE ``UnicodeError: undefined encoding``, which the
    narrower subclass does not catch.
    """

    for chain in (
        ("oem", "cp-does-not-exist", "cp949"),
        ("cp1200", "hex", "cp949"),
        ("undefined", "cp949"),
    ):
        assert decode_child_output(_ISSUE_BYTES, fallbacks=chain) == "위치 줄:1 문자:14", chain


def test_console_codepage_beats_oem() -> None:
    """A.2: the console output page first, because the child shares our console.

    ``containment_spawn_kwargs`` passes only ``CREATE_NEW_PROCESS_GROUP`` on
    win32, so the tool's child keeps Aelix's console — and therefore whatever
    page the UTF-8 preamble's ``[Console]::OutputEncoding`` just set. That is
    the preamble's only spelling as of 2026-09-09: the ``cmd`` arm's
    ``chcp 65001 >nul&`` was deleted after CI run 34272507388, so
    :func:`aelix_ai.utils._shell.utf8_output_preamble` returns
    ``POWERSHELL_UTF8_PREAMBLE`` or ``""`` and nothing else.
    """

    assert win32_output_fallbacks(device_encoding="cp932") == ("cp932", "oem")


def test_no_console_falls_back_to_oem() -> None:
    """``os.device_encoding(1)`` is ``None`` when stdout is not a console.

    Measured on darwin with stdout a pipe: ``None``. That is the ``aelix -p``
    / redirected case on Windows too, and ``GetOEMCP`` is the only candidate
    left when there is no console to ask.
    """

    assert win32_output_fallbacks(device_encoding=None) == ("oem",)


# === the review's two majors ================================================
#
# The first cut split the buffer on ``[\x80-\xff]+`` and handed each failed run
# whole to the first code page that accepted it. Both halves of that were
# measured wrong: the split cuts DBCS characters whose trail byte is ASCII, and
# "accepted it" is worthless from a page that accepts everything.


def test_a_dbcs_trail_byte_in_the_ascii_range_is_not_a_run_boundary() -> None:
    """cp932/936/949/950 take ``0x40-0x7E`` as TRAIL bytes, so runs cut characters.

    Re-measured 2026-09-09 on darwin/CPython 3.12.13, enumerating every lead
    ``0x81-0xFF`` x trail ``0x40-0xFF`` pair that decodes to exactly one
    character: 3288 of cp932's 9604 such pairs have an ASCII trail byte, cp936
    7445 of 21791, cp950 5544 of 13752, cp949 3606 of 17048 — the table
    :mod:`aelix_ai.utils._child_output` carries, which is the canonical copy.
    (The ``3153/9212`` and ``5549/13751`` this docstring first held do not
    reproduce under that rule; that module's rule-1 paragraph records the
    retraction, and this line contradicted it.)
    ``"エラー".encode("cp932")`` is
    ``83 47 83 89 81 5b`` — three one-byte runs separated by ``G``, ``[`` and a
    non-ASCII neighbour — and the first cut returned ``�G���[``,
    i.e. exactly the mojibake the issue is about. The issue's own Korean
    survived only because every trail byte in it happens to be ``>= 0xA1``.
    """

    for text, page in (
        ("エラー", "cp932"),
        ("指定されたファイルが見つかりません", "cp932"),
        ("アクセスが拒否されました。", "cp932"),
        ("똠방각하", "cp949"),  # ``똠`` is ``8c 63`` — the trail byte is ``c``
        ("系统找不到指定的文件", "cp936"),
        ("找不到指定的檔案", "cp950"),
    ):
        raw = text.encode(page)
        assert decode_child_output(raw, fallbacks=(page,)) == text, (text, page)


def test_a_page_that_accepts_every_byte_may_not_claim_a_run_holding_utf8() -> None:
    """cp437/cp850/cp866 reject 0 of 256 bytes, so "it decoded" is not evidence.

    That is every Western Windows box, CI's ``en-US`` runner included. The
    first cut let one stray byte re-spell the whole surrounding run — strictly
    worse than the ``errors="replace"`` it replaced, since modern Windows
    tools (git, uv, node) all emit UTF-8.

    The rule reached only the whole RUN until #239's final pass; the stretch
    inside a run that UTF-8 could not begin at still went to whatever page
    answered first, so the stray byte kept a spelling of its own. Both levels
    apply it now, so the UTF-8 survives AND the stray byte stays a marker.
    """

    corrupted = bytearray("결과: 한국어".encode())
    corrupted[9] = 0xC0  # inside ``한``, which is where a bad byte lands

    assert decode_child_output("한글".encode() + b"\xff", fallbacks=("cp437",)) == "한글�"
    assert decode_child_output("café".encode() + b"\xa0", fallbacks=("cp437",)) == "café�"
    assert decode_child_output(bytes(corrupted), fallbacks=("cp437",)).endswith("국어")
    # An rg-shaped line: an escape byte, one bad byte, then more real UTF-8.
    rg = "매치: 값→".encode() + b"\x1b\xfe" + "끝".encode()
    assert decode_child_output(rg, fallbacks=("cp850",)) == "매치: 값→\x1b�끝"


def test_a_binary_byte_stays_a_replacement_character_on_every_western_chain() -> None:
    """windows-latest, CI run 34238825800, py3.11 and py3.12, both red.

    ``tests/test_extension_issue5_runtime_and_trust.py``'s
    ``test_exec_replaces_undecodable_bytes_on_the_success_path`` and
    ``…_on_the_timeout_path`` write ``b"ok \xff\n"`` and have pinned
    ``"ok \ufffd\n"`` since #221. They passed on ubuntu and macOS, where the
    fallback chain is empty, and failed on the runner because its chain is the
    OEM page: cp437 maps ``0xFF`` to U+00A0, a NO-BREAK SPACE, so the marker
    saying "these bytes were lost" became invisible rather than merely wrong.

    Pinned here as well as there because those two cases spawn a real child and
    reach the win32 arm only on the win32 leg — nothing on a POSIX box caught
    this before CI did.
    """

    for chain in (("cp437",), ("cp850",), ("cp1252",), ("cp866",), ("cp1252", "cp437")):
        assert decode_child_output(b"ok \xff\n", fallbacks=chain) == "ok �\n", chain
        assert decode_child_output(b"\xff", fallbacks=chain) == "�", chain

    # A DBCS page reaches the same answer by REFUSING the byte, which is what
    # says the fix is the multi-byte test and not a special case for 0xFF.
    assert decode_child_output(b"ok \xff\n", fallbacks=("cp949",)) == "ok �\n"


def test_a_chain_with_no_dbcs_page_is_exactly_errors_replace() -> None:
    """The invariant the final pass buys, and what pays for the Western loss.

    A single-byte page is now offered nothing — not a whole run
    (``_decode_failed_run``) and not the bad stretch inside one
    (``_decode_bad_bytes``) — so on every Western Windows box this module is
    the call it replaced, claimed ends or not. That is checkable rather than
    arguable, and this case is the check. The sweep below is 351 exhaustive
    windows and 2000 random blobs against 6 chains x 4 claim shapes = 56424
    decodes; re-measured 2026-09-09 on darwin/CPython 3.12.13 with
    ``_decode_bad_bytes`` restored to its pre-final rule, 37232 of those 56424
    differ from ``replace`` (2300 of the 8424 window decodes, 34932 of the
    48000 random ones), so the case has something to catch at every size.
    """

    chains = [("cp437",), ("cp850",), ("cp1252",), ("cp866",), ("cp1252", "cp437"), ()]
    claims = [
        {},
        {"ragged_head": True},
        {"ragged_tail": True},
        {"ragged_head": True, "ragged_tail": True},
    ]

    core = "안녕 hi 한글! Größe".encode()
    for i in range(len(core) + 1):
        for j in range(i, len(core) + 1):
            window = core[i:j]
            floor = window.decode("utf-8", errors="replace")
            for chain in chains:
                for claim in claims:
                    got = decode_child_output(window, fallbacks=chain, **claim)
                    assert got == floor, (window, chain, claim)

    rng = random.Random(2390908)
    for _ in range(2000):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
        floor = blob.decode("utf-8", errors="replace")
        for chain in chains:
            for claim in claims:
                got = decode_child_output(blob, fallbacks=chain, **claim)
                assert got == floor, (blob, chain, claim)


def test_a_glued_run_is_split_only_when_the_page_rejects_it_whole() -> None:
    """No separator: the split saves the UTF-8 half only if cp949 says no.

    ``test_a_utf8_run_beside_a_codepage_run_keeps_both`` has a space in it, so
    the two halves are two runs and the split never has to happen. Without the
    space they are ONE run — and this case passes because those particular ten
    bytes are not valid cp949 end to end. That is luck, not a rule, which is
    why the KNOWN LIMIT below sits next to it (#239 cross-review).
    """

    glued = "한글".encode() + "오류".encode("cp949")

    assert decode_child_output(glued, fallbacks=("cp949",)) == "한글오류"
    # And this is why the assertion above is not a general guarantee.
    assert "한글".encode().decode("cp949", "replace") != "한글"


def test_a_glued_run_the_page_accepts_whole_costs_the_utf8_half() -> None:
    """KNOWN LIMIT (#239 cross-review) — pinned so nobody reads it as fixed.

    The acceptance rule says a DBCS page taking a run WHOLE is evidence, and it
    is taken before the UTF-8-preserving split is considered. When a UTF-8
    stretch and a legacy stretch abut with no ASCII between them and cp949
    accepts the lot, the half that ``replace`` got right is now confidently
    wrong. Measured 2026-09-09 on darwin/CPython 3.12.13, 4000 glued pairs per
    shape drawn from ALL 11172 cp949-encodable modern Hangul: one-and-one loses
    the UTF-8 half 13.3% of the time (all of those through rule 1's ASCII trail
    byte, which shortens the non-ASCII run to an even length), two-and-two
    38.7%, three-and-three 9.4%, six-and-four 6.5%. (This docstring said
    "common" and carried these numbers; re-measured, they reproduce over the
    full set and NOT over the ``b0-c8`` block, which gives
    7.7%/27.3%/4.9%/2.2%. See :mod:`aelix_ai.utils._child_output`.)

    NOT FIXED, and the reason is in the case below it: any repair has to
    decide, on the bytes alone, between "this run's UTF-8 opening is real" and
    ``test_a_dbcs_page_still_claims_a_run_that_opens_utf8_valid``, which is the
    shape the issue was reported from. There is no byte-level discriminator.
    """

    lost = "문자".encode() + "오류".encode("cp949")

    assert decode_child_output(lost, fallbacks=("cp949",)) == "臾몄옄오류"
    assert lost.decode("utf-8", errors="replace") == "문자����"
    # The even-length rule, stated as an assertion rather than as prose.
    assert len(lost) % 2 == 0
    assert lost.decode("cp949") == "臾몄옄오류"


def test_a_lone_cjk_character_whose_bytes_are_valid_utf8_reads_as_utf8() -> None:
    """KNOWN LIMIT (#239 cross-review), and it is ``main``'s answer too.

    UTF-8 strict goes first — it has to, or every Western box regresses — so a
    DBCS character that happens to spell a valid UTF-8 one is never offered to
    the page. Measured 2026-09-09 on darwin/CPython 3.12.13: 1027 of cp949's
    17048 double-byte mappings (6.0%) are valid UTF-8 on their own, and 345 of
    the 11172 cp949-encodable modern Hangul syllables (3.1%). A multi-character
    run has to collide in every character at once, so what stays exposed is a
    SINGLE CJK character standing between ASCII bytes.
    """

    assert "치".encode("cp949") == b"\xc4\xa1"
    assert decode_child_output("치".encode("cp949"), fallbacks=("cp949",)) == "ġ"
    # Two characters already escape it: the run is no longer valid UTF-8.
    assert decode_child_output("치위".encode("cp949"), fallbacks=("cp949",)) == "치위"


def test_a_dbcs_page_still_claims_a_run_that_opens_utf8_valid() -> None:
    """The other direction, and the counter-case the split is guarded against.

    ``"치위".encode("cp949")`` is ``c4 a1 c0 a7``, whose first two bytes are the
    valid UTF-8 ``ġ`` — so splitting at the first strict error would hand back
    ``ġ위``. cp949 rejects 128 of the 256 single bytes, so its accepting the
    run WHOLE is evidence, and it is taken before any split is considered. The
    mirror case ``문자치`` resyncs on its last two bytes and would lose ``치``.
    """

    for text in ("치위", "문자치"):
        assert decode_child_output(text.encode("cp949"), fallbacks=("cp949",)) == text
    # Both really do open/close on a UTF-8-valid pair, which is the whole point.
    assert "치".encode("cp949").decode("utf-8") == "ġ"


def test_is_multibyte_page_separates_the_two_families_in_one_decode() -> None:
    """The measurement the acceptance rule rests on.

    Decoding all 256 single bytes with ``errors="replace"`` yields fewer than
    256 characters exactly when the page has lead/trail pairs. Measured
    2026-09-08 on darwin/CPython 3.12.13: cp932 229, cp950 211, cp936 199,
    cp949 195; every single-byte page 256.
    """

    for page in ("cp932", "cp936", "cp949", "cp950"):
        assert _is_multibyte_page(page), page
    for page in ("cp437", "cp850", "cp866", "cp1252", "cp1251", "iso8859-1"):
        assert not _is_multibyte_page(page), page
    # A codec nobody can look up is not evidence for anything — and off Windows
    # that includes ``oem``, which the real chain always ends with.
    assert not _is_multibyte_page("cp-does-not-exist")


def test_a_nul_byte_takes_the_replace_floor() -> None:
    """A buffer with a NUL is a binary file, not console text.

    No code page recovers it, and probing every run of it is the worst case
    there is: measured on darwin/CPython 3.12.13, 50 kB of NUL-free random
    bytes costs 15.7 ms through the run loop against 0.198 ms for the floor,
    and the gap scales. So the same sniff ``git`` uses short-circuits it, and
    the answer is the old call's.
    """

    blob = "한글".encode() + b"\x00" + "오류".encode("cp949")

    assert decode_child_output(blob, fallbacks=("cp949",)) == blob.decode(
        "utf-8", errors="replace"
    )


def test_ragged_recovery_is_for_the_CUT_ends_and_not_the_interior() -> None:
    """``at_head``/``at_tail`` are positions, and replacing them with ``True`` bites.

    A run in the MIDDLE of a ragged window has both its ends chosen by the
    child, so trimming it is not repair, it is damage: ``9c d7 8d 9a`` is
    ``쑿뜗`` in cp949, but its ``d7 8d`` is also a valid UTF-8 character, so a
    head/tail trim applied there recovers ``�׍�`` and cp949 is
    never asked.
    """

    interior = bytes.fromhex("9cd78d9a")
    assert interior.decode("cp949") == "쑿뜗"

    assert (
        decode_child_output(
            b"x " + interior + b" y",
            ragged_head=True,
            ragged_tail=True,
            fallbacks=("cp949",),
        )
        == "x 쑿뜗 y"
    )


# === the Codex cross-review (CLAUDE.md rule 8) ==============================


def test_a_terminal_invalid_byte_is_not_a_truncated_character() -> None:
    """`exc.end == len(run)` is true for an INVALID last byte as well.

    The ragged tail rule withholds a byte-group that the cut may have severed.
    A terminal `0xFF` — or `c0`, `c1`, `f5`-`ff` — errors at the end of the run
    too and can never begin a character at all, so withholding it spends a
    U+FFFD where the code page had an answer. The ragged and the plain answer
    have to agree here, and before the cross-review they did not.

    This case ran on `cp1252` until #239's final pass, where a single-byte page
    stopped being offered anything: it now answers `값�` either way, so it
    can no longer tell the guard from its absence. The page that still can is a
    DBCS one — `ea b0 92 c0` is four bytes cp949 reads as two characters, and
    withholding the last would throw that reading away.
    """

    for terminal in (b"\xc0", b"\xc1", b"\xf5"):
        run = "값".encode() + terminal
        ragged = decode_child_output(
            run, ragged_head=True, ragged_tail=True, fallbacks=("cp949",)
        )
        assert ragged == decode_child_output(run, fallbacks=("cp949",)), terminal
        assert "�" not in ragged, (terminal, ragged)

    # And where the page has NO answer the two still agree — on the floor.
    for chain in (("cp949",), ("cp1252",)):
        run = "값".encode() + b"\xff"
        assert decode_child_output(
            run, ragged_head=True, ragged_tail=True, fallbacks=chain
        ) == decode_child_output(run, fallbacks=chain) == "값�", chain

    # A genuinely severed character still is withheld, which is the whole point
    # of the flag: this tail is `ea b8`, the first two bytes of `글`.
    assert (
        decode_child_output(
            "ok: 한글".encode()[:-1], ragged_head=True, ragged_tail=True, fallbacks=("cp949",)
        )
        == "ok: 한�"
    )
    assert decode_child_output("ok: 한글".encode()[:-1], fallbacks=("cp949",)) == "ok: 한湲"


def test_ascii_invariance_is_half_of_the_multibyte_test() -> None:
    """`< 256 characters` on its own is a fingerprint, not the property needed.

    Both `utf-7` (255 characters) and `utf-16` (127) pass the collapse probe
    and neither may take a run whole or be widened across the ASCII after it:
    `+` is UTF-7's shift introducer and no ASCII byte stands alone in UTF-16.
    So the codec must also decode every byte `0x00-0x7F` to itself.
    """

    for page in ("cp932", "cp936", "cp949", "cp950", "cp1361", "euc_jp"):
        assert _is_multibyte_page(page), page
    for page in ("utf-7", "utf-16", "hz", "iso2022_jp", "cp437", "cp1252"):
        assert not _is_multibyte_page(page), page


def test_no_codec_name_can_make_this_raise() -> None:
    """"Never raises" has to survive the fallback chain, not just the bytes.

    `codecs.lookup("undefined")` SUCCEEDS and its decode then raises the base
    `UnicodeError`, which `except UnicodeDecodeError` lets through; `idna`
    raises the same for `errors="replace"`; `hex` is a `LookupError` at lookup
    because it is not a text codec.
    """

    for chain in (
        ("undefined",),
        ("idna", "punycode"),
        ("hex", "rot13"),
        ("undefined", "idna", "cp949"),
    ):
        for blob in (_ISSUE_BYTES, b"\x80", os.urandom(512)):
            assert isinstance(decode_child_output(blob, fallbacks=chain), str), chain
            assert isinstance(
                decode_child_output(
                    blob, ragged_head=True, ragged_tail=True, fallbacks=chain
                ),
                str,
            ), chain


def test_a_severed_character_alone_in_its_run_stays_a_replacement_mark() -> None:
    """#239 cross-review finding 3 — the empty-core guard was doing two jobs.

    When the cut severs a character that has an ASCII byte right before it — a
    space, a ``:``, a ``/``, i.e. a word list, a path component or a one-word
    status line — the fragment is ALONE in its run. The tail withhold then
    empties the core, and the run used to be handed to the fallbacks
    UNTRIMMED, where the console page spelled it: ``ok: 援`` for the first two
    bytes of ``국``. That is right-to-wrong, because U+FFFD is the CORRECT
    rendering of a character the cut destroyed, and it is the exact failure
    #239 exists to end.

    Measured 2026-09-09 on darwin/CPython 3.12.13 over 24 realistic console
    lines cut at all 297 offsets that land strictly inside a multi-byte
    character: 19 (6.4%) came back as a confident wrong character on cp949, 20
    on cp932, 47 on cp936 and 19 on cp950. All seven chains score 0 now.

    THE SINGLE-BYTE HALF OF THAT MEASUREMENT IS HISTORY (#239 final pass). It
    read ``100 (33.7%) on cp850 and on cp437`` — a halved Latin-1 character
    leaves a lone lead byte, which is alone in its run almost always — and it
    was taken against the code as it stood then. Those chains score 0 WITHOUT
    the guard now, because a page that decodes all 256 single bytes is offered
    nothing at all: re-measured 2026-09-09 over 16 accented Latin console lines
    cut at all 33 in-character offsets, 0 of 33 on cp850 and 0 of 33 on cp437,
    against 33 of 33 on cp932. The ``cp850`` case below therefore no longer
    witnesses this guard — it witnesses the ACCEPTANCE rule that
    ``test_a_chain_with_no_dbcs_page_is_exactly_errors_replace`` pins — and the
    two cp949 cases are what falsify a revert of the guard itself.
    """

    assert decode_child_output(b"ok: \xea\xb5", ragged_tail=True, fallbacks=("cp949",)) == "ok: \ufffd"
    assert decode_child_output(b"caf\xc3", ragged_tail=True, fallbacks=("cp850",)) == "caf\ufffd"
    # The head direction of the same guard: a window that opens on the tail of
    # a character the ring trimmed.
    assert (
        decode_child_output(b"\xb5\xad ok", ragged_head=True, fallbacks=("cp949",))
        == "\ufffd\ufffd ok"
    )

    # Each of those is byte-for-byte what ``errors="replace"`` produced, which
    # is the property the fix is built on.
    for cut, page in ((b"ok: \xea\xb5", "cp949"), (b"caf\xc3", "cp850"), (b"\xb5\xad ok", "cp949")):
        assert decode_child_output(
            cut, ragged_head=True, ragged_tail=True, fallbacks=(page,)
        ) == cut.decode("utf-8", errors="replace"), (cut, page)

    # BOTH ends is still the exception, and it is load-bearing: ``"꽃"`` is
    # ``b2 c9``, whose two bytes the head strip and the tail withhold consume
    # between them, so the untrimmed run has to reach cp949.
    assert (
        decode_child_output(
            "꽃".encode("cp949"), ragged_head=True, ragged_tail=True, fallbacks=("cp949",)
        )
        == "꽃"
    )


def test_claiming_a_cut_tail_does_not_trim_the_head() -> None:
    """#239 cross-review finding 2 — the ends are two claims, not one.

    The head arm strips leading ``0x80-0xBF`` bytes with no cap, and cp949's
    Hangul lead bytes run ``0xB0-0xC8``, so half the Hangul block looks like a
    UTF-8 continuation byte. ``"가짜".encode("cp949")`` is ``b0 a1 c2 a5``: the
    strip eats ``b0 a1``, the remaining ``c2 a5`` is a valid UTF-8 ``¥``, and
    :func:`_recover_ragged_run` returns before cp949 is ever asked. A caller
    whose buffer is append-only has no cut head to claim, and now cannot claim
    one by accident.

    Measured 2026-09-09 on darwin/CPython 3.12.13, 20000 buffers of 1-8 random
    common Hangul syllables encoded cp949 at offset 0: claiming only the tail
    is byte-identical to claiming nothing (0 of 20000 differ, at each of the
    seeds 239 / 20260908 / 20260909), which is the property this case exists
    for.

    THE ``7.8%`` THIS DOCSTRING GAVE FOR THE HEAD CLAIM DESCRIBED THE FLAG THAT
    WAS SPLIT, not either arm of the split (#239 final pass, review finding).
    Under the single ``ragged`` both ends were always claimed together, so
    ``_recover_ragged_run``'s both-ends guard rescued the emptied run; claiming
    the head ALONE cannot reach that guard, and the same sweep gives 31.4-32.6%
    over the common ``b0-c8`` block and 51.6-51.8% over all cp949-encodable
    Hangul (three seeds). Read those; the old figure is kept only as what was
    shipped.
    """

    fake = "가짜".encode("cp949")
    assert fake == b"\xb0\xa1\xc2\xa5"
    assert fake[2:].decode("utf-8") == "¥"  # why the head strip returns at all

    assert decode_child_output(fake, ragged_tail=True, fallbacks=("cp949",)) == "가짜"
    # The head claim is what breaks it, and it has to keep breaking it — the
    # two ring sites really do cut their head and need the strip.
    assert (
        decode_child_output(fake, ragged_head=True, ragged_tail=True, fallbacks=("cp949",))
        == "\ufffd\ufffd¥"
    )
    assert fake.decode("utf-8", errors="replace") == "\ufffd\ufffd¥"  # i.e. main's mojibake

    # A tail claim still repairs the tail — the flag did not become a no-op.
    assert (
        decode_child_output("ok: 한국어".encode()[:-1], ragged_tail=True, fallbacks=("cp437",))
        == "ok: 한국\ufffd"
    )
