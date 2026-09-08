"""What a spawned child's bytes become — one decode policy, run by run.

Private like :mod:`aelix_ai.utils._process_tree` and :mod:`aelix_ai.utils._shell`,
and here for the same reason (#227's): consumers live in three packages and the
dependency direction is one-way (``aelix-ai`` <- ``aelix-agent-core`` <-
``aelix-coding-agent``), so the shared piece lives at the bottom. Every child
this agent DECODES ITSELF goes through here — the bash tool, a ``models.json``
``!command``, subprocess hooks, ``rg``/``fd``, subagent stderr. An MCP stdio
server is NOT one of them and is deliberately untouched: the ``mcp`` SDK's
``stdio_client`` owns that decode (``StdioServerParameters(encoding=...,
encoding_error_handler=...)``), and no line of this repo decodes those bytes.

THE BUG (#239). Every one of those sites decoded ``utf-8`` with
``errors="replace"``, hard-coded. On a Korean Windows box PowerShell writes
CP949, so ``"위치 줄:1 문자:14"`` — 17 bytes, ``c0 a7 c4 a1 20 c1 d9 3a 31 20 b9
ae c0 da 3a 31 34`` — reached the model as ``��ġ ��:1 ����:14``, character for
character what the issue reported. It is not even recognisable as corruption:
``c4 a1`` is a *valid* UTF-8 sequence (U+0121 ``ġ``). Strict UTF-8 rejects that
buffer at offset 0, and ``errors="replace"`` throws away exactly the
information needed to pick a better codec before anyone can act on it.

WHY RUN-WISE AND NOT PER BUFFER. Flipping the whole buffer to a fallback codec
on one bad byte is strictly worse than today for a MIXED buffer, and mixed
buffers are guaranteed on the reported box: the bash tool spawns with
``stderr=STDOUT``, so once the UTF-8 preamble (:func:`aelix_ai.utils._shell.
utf8_output_preamble`) has moved PowerShell to 65001, a native child that
ignored the console page still writes CP949 into the same pipe. Measured
2026-09-08 on darwin/CPython 3.12.13, with ``m = "한글".encode() + b" " +
"오류".encode("cp949")``::

    m.decode("utf-8", "replace")   ->  한글 ����
    m.decode("cp949", "replace")   ->  �븳湲� 오류     (the whole-buffer flip)
    decode_child_output(m, fallbacks=("cp949",))  ->  한글 오류

So the buffer is split into maximal runs of ``[\\x80-\\xff]``, ASCII decodes
natively, and each run is decided on its own.

A RUN IS NOT A CHARACTER BOUNDARY, WHICH COSTS TWO MORE RULES. The review of
the first cut of this module measured both, on darwin/CPython 3.12.13:

1. *The trail byte.* cp932/936/949/950 all take ``0x40-0x7E`` — ASCII — as DBCS
   TRAIL bytes, so a run boundary lands INSIDE a character. Enumerating every
   lead ``0x81-0xFF`` x trail ``0x40-0xFF`` pair that decodes to exactly one
   character, 3288 of cp932's 9604 pairs have an ASCII trail byte, cp936 7445
   of 21791, cp950 5544 of 13752, cp949 3606 of 17048 (re-measured 2026-09-09
   on darwin/CPython 3.12.13; the cp932 and cp950 figures this module first
   carried, 3153/9212 and 5549/13751, do not reproduce under that rule or any
   other we could state, and the two that DID match are what says the rule is
   right rather than the numbers). ``"エラー".encode("cp932")`` is
   ``83 47 83 89 81 5b`` — three one-byte runs — and came back
   ``\\ufffdG\\ufffd\\ufffd\\ufffd[``, no better than before. So a failed run is
   offered to a code page together with the ASCII that FOLLOWS it, up to the
   next non-ASCII byte, and those bytes are consumed only when the widened
   slice decodes strictly. Standalone ``0x20-0x7E`` is identity in all four
   pages (measured: zero exceptions), so widening can only re-pair a trail
   byte, never re-spell an ASCII one.
2. *A run can hold BOTH encodings.* Two neighbours that no ASCII byte separates
   are ONE run — ``"한글".encode() + "오류".encode("cp949")`` — and handing that
   run whole to a code page destroys the UTF-8 half. See ACCEPTANCE.

ORDER, AND WHY UTF-8 IS FIRST. Of 256 single bytes, cp949 rejects 128 and
cp936 128 and cp932 60, while cp437/cp850/cp866 reject none — so "does this
decode as cp949" discriminates, whereas a Korean UTF-8 run is quite often valid
cp949 too (``"문자"`` is ``eb ac b8 ec 9e 90``, which cp949 reads as ``臾몄옄``).
UTF-8 goes first.

AND THAT ORDER IS A LIMIT IN THE OTHER DIRECTION (#239 cross-review, ACCEPTED —
it is the pre-#239 answer, not a regression). A DBCS character whose two bytes
happen to spell a valid UTF-8 character is read as UTF-8 and the page is never
asked: ``b"\\xc4\\xa1"`` is cp949's ``치`` and also U+0121 ``ġ``, and the whole
buffer takes strict UTF-8 before any run is looked at. Measured 2026-09-09 on
darwin/CPython 3.12.13: 1027 of cp949's 17048 double-byte mappings (6.0%) are
valid UTF-8 on their own, 345 of the 11172 cp949-encodable modern Hangul
syllables (3.1%). A whole multi-character run collides far less often — every
character has to collide at once — so what stays exposed is a SINGLE CJK
character standing between ASCII bytes. There is no fix inside this module:
by construction it cannot prefer a code page over strict UTF-8 without
un-fixing every Western box. ``main`` gave the same wrong answer.

ACCEPTANCE: A PAGE THAT REJECTS NOTHING PROVES NOTHING, AND IT IS OFFERED
NOTHING. That same asymmetry decides what a successful fallback decode is
worth. A DBCS page accepting a whole run is evidence, so it takes the run —
including a run that OPENS with a UTF-8-valid pair, which is how
``"치위".encode("cp949")`` (``c4 a1 c0 a7``, whose first two bytes are the valid
``ġ``) still comes back as Korean. A single-byte page accepts every byte
sequence there is, so its "success" is a guess, and it gets no say at all:
neither over a whole run (:func:`_decode_failed_run`) nor over the bad-byte
stretch inside one (:func:`_decode_bad_bytes`). The test for "DBCS" is one
measured decode, :func:`_is_multibyte_page`.

THAT SECOND HALF IS WHAT WINDOWS CI TAUGHT US (#239 final pass). The rule was
applied to the whole run only, and the stretch a run's UTF-8 could not begin at
still went to whichever page answered first — so on the ``en-US`` runner, whose
chain is ``("cp437",)``, ``b"ok \\xff\\n"`` decoded ``"ok \\xa0\\n"``: cp437
maps ``0xFF`` to U+00A0, a NO-BREAK SPACE. Not merely a wrong character, an
INVISIBLE one, and the repo had pinned the opposite since #221
(``tests/test_extension_issue5_runtime_and_trust.py``'s
``test_exec_replaces_undecodable_bytes_*``, red on windows-latest py3.11 and
py3.12 in CI run 34238825800). Closing it costs the Western single-byte
recovery outright, and buys an invariant worth more than it: WITH NO DBCS PAGE
IN THE CHAIN THIS FUNCTION IS ``errors="replace"``, byte for byte, claimed ends
or not. Measured 2026-09-09 on darwin/CPython 3.12.13 over every window of
``"안녕 hi 한글! Größe".encode()`` and 20000 random blobs, against six
single-byte chains (cp437/cp850/cp1252/cp866/``("cp1252","cp437")``/``()``) and
all four ``ragged_head``/``ragged_tail`` shapes: 351 exhaustive windows and
20000 random blobs x 24 chain/claim combinations = 8424 + 480000 decodes, 0
differ from ``replace``, where the pre-final code differed on 2300 and 351187.
So #239 is now scoped to the DBCS consoles it was reported from, and every
Western Windows box gets exactly what it got before. What that costs is under
WHAT WE GIVE UP.

AND A GLUED RUN COSTS THE UTF-8 HALF — the price of that same acceptance rule,
and the case rule 2 above defers here (#239 cross-review, ACCEPTED). When a
UTF-8 stretch and a legacy stretch abut with NO ASCII byte between them they
are one run, and if the DBCS page takes that run whole it takes the UTF-8 half
with it: ``"문자".encode() + "오류".encode("cp949")`` comes back ``臾몄옄오류``
where ``replace`` gave ``문자����``, i.e. the half that used to be right is now
confidently wrong. The page can only take the run whole if the run has EVEN
length — a DBCS character is two bytes where a Korean UTF-8 one is three — but
"even" counts the RUN and not the text, so rule 1's ASCII trail bytes reach it
too: a legacy character ending in ``0x40-0x7E`` leaves the non-ASCII run one
byte shorter. Measured 2026-09-09 on darwin/CPython 3.12.13, 4000 glued pairs
per shape of syllables drawn uniformly from ALL 11172 cp949-encodable modern
Hangul (one ``Random(239)`` consumed across the four shapes), classified
against ``replace`` — the word "common" this paragraph first used was wrong,
not the numbers: restricting the draw to the ``b0-c8`` block gives
7.7%/27.3%/4.9%/2.2% instead, and the figures below reproduce EXACTLY over the
full set. One UTF-8 character glued to one cp949 character loses the UTF-8 half
13.3% of the time
(those runs are 5 bytes, so all 531 of them are the trail-byte case),
two-and-two 38.7%, three-and-three 9.4%, six-and-four 6.5%. The rest either
come out entirely right (83.1% at one-and-one) or keep the UTF-8 half and lose
only the legacy one. NOT FIXED, deliberately: a fix has to decide on the bytes
alone between "this run's UTF-8 opening is real" and "a run that opens
UTF-8-valid must still go whole to cp949", and this module already ruled the
second way — that is what keeps ``"치위"`` Korean, and
the buffer #239 was reported from is that shape. There is no byte-level
discriminator, and guessing wrong re-breaks the reported bug. It needs a
producer that abuts the two with no separator, which the shared
``stderr=STDOUT`` pipe makes possible (a UTF-8 progress fragment with no
trailing newline, then a native child's legacy warning) and uncommon.

WHAT WE GIVE UP (A.3): A WESTERN BOX GETS NOTHING. There is no ANSI
(``mbcs``/``GetACP``) step and, since the final pass, no single-byte step
either — so on a box whose whole chain is cp437/cp850/cp1252 this module is the
old ``errors="replace"`` call and nothing more. That is a REAL loss and not a
bookkeeping one: measured 2026-09-09 on darwin/CPython 3.12.13 over 16 German,
French, Spanish, Italian and Swedish console lines, a child writing the same
page the chain names decoded 16/16 CORRECTLY before and 16/16 as U+FFFD now.
It is given up because the same 16 lines decode 16/16 CONFIDENTLY WRONG when
the child writes the ANSI page and the chain is the OEM one — and on a Western
box those two ALWAYS differ (ACP 1252 against OEM 850 or 437), where in the CJK
locales this was reported from they are the same number (949/932/936). So the
single-byte answer is a coin flip that the bytes cannot call, and the same
probe that would have been right for a cp850-writing child is what spelled a
binary ``0xFF`` as an invisible U+00A0: measured over 2000 ``b"ok " + 1-3
random high bytes`` buffers, the pre-final code marked 0 of them on cp437 and
cp850 and rendered 7 and 11 of them with no visible character at all; every one
is now marked. A per-run ANSI step is still cheap to add later if a Western
report ever justifies one — run-wise decoding is what makes it cheap — but it
would need a producer probe this module does not have.

AND THE CHAIN IS OEM ALONE UNDER A REDIRECTED RUN. :func:`os.device_encoding`
answers :data:`None` for a non-console fd, so ``aelix -p … > out.txt``, a piped
invocation and pytest itself all get ``("oem",)``, with no console page in
front of it.

OFF WIN32 THIS IS PROVABLY THE OLD CALL. :func:`win32_output_fallbacks` returns
``()`` there, so a buffer that failed strict UTF-8 takes the ``replace`` floor
in one call — literally today's expression — and one that did not takes strict
UTF-8, which is the same text. ``tests/util/test_child_output_decode.py`` pins
that THROUGH the default rather than by passing ``fallbacks=()``.

COST, AND THE THREE SHORTCUTS THAT PAY FOR IT. The review measured the first
cut at 21x the call it replaced for binary input, so the run loop is now
entered only where it can help. A buffer that decodes strictly, one with no
fallbacks (every POSIX buffer), and one holding a NUL byte each take a single
whole-buffer decode. Measured on darwin/CPython 3.12.13, per call:

=========================  ===========  =========  ========
input                      ``replace``  this       shortcut
=========================  ===========  =========  ========
280 kB of valid UTF-8       0.104 ms     0.104 ms  strict
``os.urandom(50_000)``      0.231 ms     0.216 ms  NUL
``os.urandom(1_000_000)``   6.19 ms      6.03 ms   NUL
=========================  ===========  =========  ========

(Medians of 25. The shortcut paths are one decode where ``replace`` is one
decode, so the two columns are the same measurement twice — which is exactly
why the ``0.182``/``0.131`` this row first carried, a 28% "win" the sentence
you are reading says cannot exist, does not reproduce. Re-measured 2026-09-09
on darwin/CPython 3.12.13 over 279 972 bytes of Korean/ASCII UTF-8: 0.104 ms
either way. The two NUL rows DO reproduce — 0.229/0.217 and 6.217/6.014 in
that same session — and stand as written.)

What is left paying is win32 console text that failed UTF-8 and holds no NUL —
the case this module exists for, and one bounded by what a console writes. A
synthetic 50 kB of NUL-free random bytes with ``fallbacks=("cp437",)`` costs
15.7 ms against the floor's 0.198 ms; that is the ceiling, and it is not a
shape a console produces.
"""

from __future__ import annotations

import codecs
import os
import re
import sys

#: What a fallback probe may raise and still only mean "not this codec". The
#: #239 cross-review supplied all three: ``LookupError`` for a name with no
#: codec (``oem``/``mbcs`` off Windows, ``cp1200`` everywhere, a non-text codec
#: like ``hex``); ``UnicodeError`` and NOT its ``UnicodeDecodeError`` subclass,
#: because ``b"".decode("undefined")`` raises the base class and ``idna``
#: raises it for ``errors="replace"``; and ``OSError``, which CPython's native
#: Windows code-page decoder can raise for a conversion failure that is not
#: invalid input (``Objects/unicodeobject.c``, ``decode_code_page_strict``).
_CODEC_REFUSED = (UnicodeError, LookupError, OSError)

# Maximal runs of non-ASCII bytes. A UTF-8 multi-byte sequence is entirely
# ``>= 0x80``, so a run boundary can never fall inside a UTF-8 character. It
# CAN fall inside a DBCS one, because cp932/936/949/950 take ``0x40-0x7E`` as
# trail bytes; a DBCS LEAD byte is always ``>= 0x81``, so the cut always lands
# in the same place — right after the lead — and ``_decode_failed_run`` repairs
# it by offering the code page the ASCII that follows the run.
_NON_ASCII_RUN = re.compile(rb"[\x80-\xff]+")

#: Every byte, decoded once per codec to answer "is this a DBCS page?" —
#: :func:`_is_multibyte_page`.
_ALL_256 = bytes(range(256))

#: That answer, cached by codec name: a code page's shape cannot change inside
#: a process, and the probe decodes 256 bytes.
_MULTIBYTE_PAGE: dict[str, bool] = {}

#: ``GetOEMCP``'s page, and the last thing tried on win32. It is the only
#: candidate that needs no console: a redirected or ``-p`` run has none, and
#: ``os.device_encoding(1)`` measured ``None`` there.
_OEM = "oem"

#: ``device_encoding`` was not supplied, so ask the platform. A sentinel rather
#: than ``None`` because ``None`` is a MEANINGFUL answer from
#: :func:`os.device_encoding` — "that fd is not a console".
_ASK = "<ask the console>"


def win32_output_fallbacks(*, device_encoding: str | None = _ASK) -> tuple[str, ...]:
    """The codecs to try after UTF-8, best first. Empty off win32.

    On win32 that is the CONSOLE OUTPUT code page and then ``oem``. The console
    page predicts best because the children this repo spawns keep Aelix's
    console — ``containment_spawn_kwargs`` passes only
    ``CREATE_NEW_PROCESS_GROUP`` there — so they inherit whatever page the
    UTF-8 preamble just set, for free.

    TWO THINGS THAT ENTRY IS NOT (#239 cross-review). It is AELIX'S fd 1, not
    the console and not the child's pipe — the child's stdout is always a pipe
    (``stdout=subprocess.PIPE``), and :func:`os.device_encoding` answers
    :data:`None` for any non-console fd, so a redirected run, a piped run,
    ``aelix -p`` and pytest itself all fall through to ``("oem",)`` alone. And
    when the preamble DID run it contributes nothing: the console page is then
    65001, ``codecs.lookup("cp65001").name`` is ``"utf-8"`` (measured
    2026-09-09 on darwin/CPython 3.12.13), and strict UTF-8 has already failed
    by the time this list is consulted — it costs one failed decode per run.
    So the entry earns its place exactly where the preamble did NOT reach the
    child, which is #239's own reported shape (a PowerShell parse error
    discards the preamble with the script).

    :func:`os.device_encoding` is the whole resolution: on Windows it returns
    ``"cp%d" % GetConsoleOutputCP()`` for a console fd and :data:`None`
    otherwise, which is stdlib and needs no ``ctypes``. It is asked FRESH on
    every decode that has something to fall back for, never cached — a child
    that ran ``chcp`` changed the page this process shares with it.

    ``device_encoding`` is a resolution seam, spelled like
    ``_resolve_config._resolve_platform``'s: supplying it drives the win32 answer
    from a POSIX box, where the real codecs do not exist. Supplying it also
    bypasses the platform test, which is deliberate — that is the only way a
    test on darwin can reach this arm at all.
    """

    if device_encoding == _ASK:
        if sys.platform != "win32":
            return ()
        try:
            device_encoding = os.device_encoding(1)
        except OSError:  # a closed or otherwise unaskable fd 1
            device_encoding = None
    if device_encoding:
        return (device_encoding, _OEM)
    return (_OEM,)


def decode_child_output(
    data: bytes,
    *,
    ragged_head: bool = False,
    ragged_tail: bool = False,
    fallbacks: tuple[str, ...] | None = None,
) -> str:
    """Decode one child's output. Never raises.

    ``fallbacks`` defaults to :func:`win32_output_fallbacks`, resolved lazily —
    the call is made only once the WHOLE buffer has failed strict UTF-8, so a
    pure-UTF-8 buffer (the overwhelming case, and every case off Windows) pays
    nothing for it. Pass an explicit tuple to pin the chain; ``()`` is exactly
    today's ``errors="replace"``, and takes that shortcut literally.

    ``ragged_head``/``ragged_tail`` mark an END that is a byte-exact CUT rather
    than a boundary the child chose. Claim only the end you really have. See
    :func:`_decode_failed_run` for what a claim changes and, just as
    importantly, for what it may not change.

    ONE FLAG FOR BOTH ENDS WAS MEASURABLY WRONG (#239 cross-review). The first
    cut of this module spelled these as a single ``ragged``, and the bash tool
    set it — but its buffer is a ``list[bytes]`` that is only ever APPENDED to,
    so byte 0 of the child's first write is always there and its head is never
    a cut. Claiming it disabled this module's own fix for that tool: the head
    arm strips leading ``0x80-0xBF`` bytes, cp949's Hangul lead bytes run
    ``0xB0-0xC8`` and so overlap the UTF-8 continuation range, so
    ``"가짜".encode("cp949")`` (``b0 a1 c2 a5``) came back ``��¥`` — #239's own
    reported shape — where the unclaimed path returns ``"가짜"``. The bash tool
    now claims ``ragged_tail`` only and gets the Korean; a site that really
    does claim ``ragged_head`` still gets ``��¥``, deliberately, and the two
    paragraphs below are that trade written down.

    THE HEAD ARM IS THE EXPENSIVE END, AND ITS PRICE ROSE WHEN THE FLAG SPLIT
    (#239 final pass). Under the single ``ragged`` both ends were always
    claimed together, so :func:`_recover_ragged_run`'s both-ends guard fired
    whenever the strip emptied the run and cp949 got the untrimmed bytes.
    Claiming the head ALONE never sets ``suffix``, that guard cannot fire, and
    a whole legacy character whose two bytes both lie in ``0x80-0xBF`` — 31.4%
    of cp949-encodable modern Hangul, ``"가"`` = ``b0 a1`` — is destroyed
    instead. Measured 2026-09-09 on darwin/CPython 3.12.13, 20000 buffers of
    1-8 random Hangul syllables encoded cp949 at offset 0, three seeds (239,
    20260908, 20260909), against the same buffers unclaimed:

    ===========================  ================  ====================
    rule                         common b0-c8      all cp949-encodable
    ===========================  ================  ====================
    HISTORICAL single ``ragged``  3.7-4.1%          6.3-6.5%
    ``ragged_head``              31.4-32.6%        51.6-51.8%
    ``ragged_head+ragged_tail``  31.8-33.0%        52.2-52.5%
    ``ragged_tail``               0.0%              0.0%
    ===========================  ================  ====================

    (The "7.8%" this module carried for the historical row is retained here
    only as the number that WAS shipped: it does not reproduce at any of the
    three seeds, and it was in any case a measurement of the rule this one
    replaced. Read the middle rows for the code you are looking at.)

    KEPT, because on the console this issue came from the head arm buys more
    than it costs. Measured over 12 realistic Korean console lines: a cp949
    line cut at an EXACT character boundary loses a whole character at 10 of
    139 head-cut offsets, while a UTF-8 line cut INSIDE a character has its
    confidently-wrong head replaced by U+FFFD at 120 of 192 — 140/192 on
    cp932, 116/192 on cp936. Only two sites claim it
    (:class:`~aelix_agents.print_channel.StderrRing` and ``RpcClient``'s
    stderr ring, both true ring buffers), and claiming only the tail is
    byte-identical to claiming nothing on the synthetic corpus: the flag costs
    nothing where the cut it describes did not happen. No alias is kept for the
    old spelling — it is one release old with no caller outside this repo, and
    an alias meaning "both ends" would keep the wrong default one keyword away.

    BOTH FLAGS ARE NO-OPS ON A CHAIN WITH NO DBCS PAGE, which is every Western
    Windows box and every POSIX one. The recovery only ever changes the answer
    by keeping a code page away from bytes it would have spelled, and since the
    final pass no single-byte page is offered any (see ACCEPTANCE): measured,
    ``ragged_head``/``ragged_tail`` in all four combinations over 351
    exhaustive windows and 20000 random blobs against six single-byte chains —
    488424 decodes — give ``errors="replace"`` every time.
    """

    if not data or data.isascii():
        return data.decode("ascii")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    resolved = win32_output_fallbacks() if fallbacks is None else fallbacks
    if not resolved and not (ragged_head or ragged_tail):
        # Nothing to fall back to and no cut end to repair: the run loop below
        # would spend one exception per run to arrive here anyway.
        return data.decode("utf-8", errors="replace")
    if b"\x00" in data:
        # A NUL byte says this is not console text — it is a binary file a
        # ``cat``/``type`` sent down the pipe, and ``git``'s own binary sniff
        # is the same test. No code page recovers that, and probing every run
        # of it is the worst case there is (see COST above).
        return data.decode("utf-8", errors="replace")

    # Only a DBCS page can want the ASCII after a run (as a trail byte), and
    # finding that slice costs a second search per failed run — so skip it
    # when no fallback in the chain could use it.
    widen = any(_is_multibyte_page(codec) for codec in resolved)
    out: list[str] = []
    end_of_data = len(data)
    pos = 0
    while pos < end_of_data:
        match = _NON_ASCII_RUN.search(data, pos)
        if match is None:
            out.append(data[pos:].decode("ascii"))
            break
        start, end = match.span()
        if start > pos:
            out.append(data[pos:start].decode("ascii"))
        run = data[start:end]
        try:
            out.append(run.decode("utf-8"))
        except UnicodeDecodeError:
            ascii_tail = b""
            if widen and end < end_of_data:
                following = _NON_ASCII_RUN.search(data, end)
                ascii_tail = data[end : following.start() if following else end_of_data]
            text, consumed = _decode_failed_run(
                run,
                resolved,
                ragged_head=ragged_head,
                ragged_tail=ragged_tail,
                at_head=start == 0,
                at_tail=end == end_of_data,
                ascii_tail=ascii_tail,
            )
            out.append(text)
            pos = end + consumed
            continue
        pos = end
    return "".join(out)


def _is_multibyte_page(codec: str) -> bool:
    """Is ``codec`` DBCS-SHAPED — i.e. is accepting a run EVIDENCE, and is
    widening the run across the ASCII after it output-neutral?

    Two probes, because those are two different properties and the whole run
    rule and the widening rule need one each.

    *Lead/trail pairs exist.* Decoding all 256 bytes with ``errors="replace"``
    yields fewer than 256 characters exactly when pairs collapse — measured
    2026-09-08 on darwin/CPython 3.12.13: cp932 229, cp950 211, cp936 199,
    cp949 195, cp1361 204, euc_jp 218, against exactly 256 for every
    single-byte page (cp437, cp850, cp866, cp1252, cp1251, iso8859-1).

    *ASCII is invariant.* Every byte ``0x00-0x7F`` decodes to itself on its
    own. Without this the first probe alone is only a fingerprint, and the
    #239 cross-review named the counter-examples: ``utf-7`` yields 255 (so it
    passes) but ``+`` is its ASCII shift introducer, and ``utf-16`` yields 127
    but no ASCII byte stands alone in it at all. Both fail here, as they must —
    widening a run into ASCII is unsound for either.

    Neither probe is a claim about arbitrary Windows code pages; it is a claim
    about this run rule, and a page that fails either simply does not get to
    take a run whole. A codec that will not answer at all is :data:`False` for
    the same reason (see :data:`_CODEC_REFUSED`).
    """

    cached = _MULTIBYTE_PAGE.get(codec)
    if cached is None:
        try:
            cached = len(_ALL_256.decode(codec, errors="replace")) < 256 and all(
                bytes([byte]).decode(codec) == chr(byte) for byte in range(0x80)
            )
        except _CODEC_REFUSED:
            cached = False
        _MULTIBYTE_PAGE[codec] = cached
    return cached


def _decode_failed_run(
    run: bytes,
    fallbacks: tuple[str, ...],
    *,
    ragged_head: bool,
    ragged_tail: bool,
    at_head: bool,
    at_tail: bool,
    ascii_tail: bytes,
) -> tuple[str, int]:
    """One non-ASCII run that UTF-8 strict rejected, and how much it ate.

    The second element is how many bytes of ``ascii_tail`` the code page took
    as DBCS trail bytes; the caller resumes after them.

    Ragged recovery first, and only at an end that is BOTH a cut the caller
    claims and an end this run actually touches — the two conditions are
    ``and``-ed per end, so a caller claiming only a cut tail never gets the head
    strip applied to its first run. Then a
    MULTI-BYTE page STRICT on the untrimmed run: such a page rejects 60-128 of
    the 256 single bytes, so its accepting the run is evidence, and that is
    what keeps ``"치위".encode("cp949")`` Korean even though the run opens on
    the UTF-8-valid ``c4 a1``. Only then the split, for the pages that prove
    nothing by accepting.

    Every probe catches ``LookupError`` beside ``UnicodeError``.
    ``codecs.lookup`` raises ``LookupError`` for ``oem``, ``mbcs`` and
    ``cp65000`` off Windows (measured), for ``cp1200`` everywhere, and for a
    non-text codec like ``hex``; :func:`os.device_encoding` can name a ``cp<N>``
    CPython has no codec for on Windows too — ``util/stdio.py`` already wraps
    ``codecs.lookup`` for exactly that. Without that arm this function raises on
    POSIX CI, out of a decoder whose whole contract is not to. The other arm is
    ``UnicodeError`` and not its ``UnicodeDecodeError`` subclass because the
    cross-review of this module found one registered codec that raises the BASE
    class: ``b"".decode("undefined")`` is ``UnicodeError: undefined encoding``,
    which the narrower clause lets through.
    """

    cut_head = ragged_head and at_head
    cut_tail = ragged_tail and at_tail
    if cut_head or cut_tail:
        recovered = _recover_ragged_run(run, at_head=cut_head, at_tail=cut_tail)
        if recovered is not None:
            return recovered, 0
    if not fallbacks:
        return run.decode("utf-8", errors="replace"), 0

    for codec in fallbacks:
        if not _is_multibyte_page(codec):
            continue
        if ascii_tail:
            try:
                return (run + ascii_tail).decode(codec), len(ascii_tail)
            except _CODEC_REFUSED:
                pass
        try:
            return run.decode(codec), 0
        except _CODEC_REFUSED:
            continue

    # Nothing that discriminates wanted the run whole, so protect the UTF-8 it
    # does contain and offer a code page only the bytes between. For a run that
    # is legacy text end to end — the reported ``c0 a7`` — the first split is
    # ``(0, len(run))`` and this is exactly the old "the whole run to the first
    # codec that takes it, else ``replace``".
    out: list[str] = []
    rest = run
    while True:
        head_end, bad_end = _split_around_the_bad_bytes(rest)
        if head_end:
            out.append(rest[:head_end].decode("utf-8"))
        bad = rest[head_end:bad_end]
        rest = rest[bad_end:]
        text, consumed = _decode_bad_bytes(bad, fallbacks, b"" if rest else ascii_tail)
        out.append(text)
        if not rest:
            return "".join(out), consumed
        try:
            out.append(rest.decode("utf-8"))
        except UnicodeDecodeError:
            continue  # more legacy bytes further in — split again
        return "".join(out), 0


def _split_around_the_bad_bytes(run: bytes) -> tuple[int, int]:
    """``(head_end, bad_end)`` for a run strict UTF-8 rejected.

    ``run[:head_end]`` is its longest valid UTF-8 prefix, and
    ``run[head_end:bad_end]`` the stretch at which no UTF-8 character can begin
    at all — the only part a code page is offered. ``bad_end`` is found by
    walking forward one byte at a time and asking a 4-byte window (UTF-8's
    maximum) whether a character starts there, which is linear; asking instead
    whether the whole remainder is valid would be quadratic on a large buffer.

    A character STARTING at ``bad_end`` does not make the remainder valid —
    ``"문자치".encode("cp949")`` resyncs at ``da c4 a1``, whose ``da c4`` is a
    character and whose ``a1`` is not — so the caller loops rather than trust
    one split.
    """

    try:
        run.decode("utf-8")
    except UnicodeDecodeError as exc:
        head_end = exc.start
    else:  # pragma: no cover - the caller only calls after a strict failure
        return len(run), len(run)
    bad_end = head_end + 1
    while bad_end < len(run) and not _starts_a_utf8_character(run, bad_end):
        bad_end += 1
    return head_end, bad_end


def _starts_a_utf8_character(run: bytes, index: int) -> bool:
    """Does a UTF-8 character begin at ``index``? A 4-byte window decides."""

    try:
        run[index : index + 4].decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start > 0
    return True


def _decode_bad_bytes(
    bad: bytes, fallbacks: tuple[str, ...], ascii_tail: bytes
) -> tuple[str, int]:
    """A DBCS page for the bytes UTF-8 could not begin at, else ``replace``.

    THE MULTI-BYTE TEST IS THE ONE :func:`_decode_failed_run` ALREADY APPLIES
    ONE LEVEL UP, and it is here for the same reason: a page that decodes all
    256 single bytes accepts anything, so its accepting these bytes is not
    evidence about them. The first cut of this module made the test only at the
    whole-run level and let any page take the stretch inside a run — an
    asymmetry it documented as deliberate, and windows-latest refuted it. CI
    run 34238825800 (py3.11 and py3.12, branch ``fix/239-beta2``) failed
    ``tests/test_extension_issue5_runtime_and_trust.py``'s
    ``test_exec_replaces_undecodable_bytes_on_the_success_path`` and
    ``…_on_the_timeout_path``, which write ``b"ok \\xff\\n"`` and have pinned
    ``"ok \\ufffd\\n"`` since #221. On the runner's ``("cp437",)`` chain the
    stretch was one byte, and cp437 maps ``0xFF`` to U+00A0 — a NO-BREAK SPACE,
    so the "these bytes were lost" marker did not become a wrong character, it
    became NO character. Measured 2026-09-09 on darwin/CPython 3.12.13 with the
    committed chains: ``("cp437",)`` and ``("cp850",)`` both gave
    ``"ok \\xa0\\n"``, ``("cp1252",)`` gave ``"ok ÿ\\n"``, and ``("cp949",)`` —
    a page that REFUSES the byte — gave ``"ok \\ufffd\\n"``, which is the answer
    all four give now.

    ``replace`` is the floor that makes "byte-identical to today off win32"
    true by construction rather than by hand-picked buffers, and with this test
    in place it makes the stronger statement too: a chain with no DBCS page in
    it is ``errors="replace"`` for the whole module (see ACCEPTANCE).
    """

    for codec in fallbacks:
        if not _is_multibyte_page(codec):
            continue
        if ascii_tail:
            try:
                return (bad + ascii_tail).decode(codec), len(ascii_tail)
            except _CODEC_REFUSED:
                pass
        try:
            return bad.decode(codec), 0
        except _CODEC_REFUSED:
            continue
    return bad.decode("utf-8", errors="replace"), 0


def _recover_ragged_run(run: bytes, *, at_head: bool, at_tail: bool) -> str | None:
    """A UTF-8 core inside a byte-exactly trimmed window, or :data:`None`.

    HEAD: strip leading continuation bytes (``0x80-0xBF``) with NO cap and
    retry strict. There is no cap because there is no bound — measured,
    ``"😀".encode()[1:]`` leaves three, and a window can start anywhere.

    TAIL: withhold a trailing slice only when the run's first strict error
    reaches the end of the run (``e.end == len(...)``), which is what "a
    genuine truncated prefix" looks like. Anything else is invalid rather than
    incomplete and must not be withheld — measured, ``b"abc\\xed\\xa0"`` errors
    at ``[0, 1)`` and ``replace`` yields TWO U+FFFD for it, not one.

    Each stripped or withheld byte-group costs exactly one U+FFFD, which is
    what ``replace`` produces for it, so recovery can never disagree with
    today's answer where today's answer was already right.

    Returns :data:`None` — and the FALLBACKS then get the UNTRIMMED run — when
    the core does not decode strictly, and when BOTH ends ate bytes and nothing
    is left between them. Trimming before the fallback would eat a real
    character: cp949's lead-byte range is ``0x81-0xFD``, which CONTAINS the
    whole UTF-8 continuation range, and ``"꽃".encode("cp949")`` is ``b2 c9``,
    whose two bytes the head strip and the tail withhold consume between them —
    a real character that would come back as two U+FFFD before cp949 was ever
    asked.

    AN EMPTY CORE FROM ONE END TAKES THE U+FFFD (#239 cross-review), AND AT THE
    HEAD THAT IS A GUESS THAT LOSES 31.4% OF THE TIME (#239 final pass). The
    case the rule was written for is a character the cut severed and nothing
    else, which the fallbacks must NOT see: a code page confidently spells the
    fragment where ``replace`` said U+FFFD, and U+FFFD is the CORRECT rendering
    of a severed character. That is right-to-wrong, where the rest of this
    module's fallback risk is wrong-to-wrong. It needs an ASCII byte — a space,
    a ``:``, a ``/`` — right before the severed character, so that the fragment
    is alone in its run, which is what a word list, a path component or a
    one-word status line produces. Measured 2026-09-09 on darwin/CPython
    3.12.13 over 24 realistic console lines cut at all 297 offsets that land
    strictly inside a multi-byte character: 19 (6.4%) became a confident wrong
    character on cp949 — ``"위치 줄:1 문자:14"`` cut to
    ``ec 9c 84 ec b9 98 20 ec a4`` read ``위치 以`` where ``replace`` gave
    ``위치 �`` — 20 on cp932, 47 on cp936 and 19 on cp950. All 7 chains score 0
    with the rule below, but three of them now score 0 WITHOUT it as well. The
    ``100 (33.7%) on cp850/cp437`` this docstring reported alongside — a halved
    Latin-1 2-byte character leaves a LONE lead byte that is always alone in
    its run, and ``b"caf\\xc3"`` read ``caf├`` — was measured against the code
    as it stood before the final pass. A single-byte page is offered nothing
    now (see ACCEPTANCE), so ``b"caf\\xc3"`` is ``caf\ufffd`` at every setting:
    re-measured 2026-09-09 on darwin/CPython 3.12.13 over 16 accented Latin
    console lines cut at all 33 offsets that land strictly inside a character,
    0 of 33 on cp850 and 0 of 33 on cp437, against 33 of 33 on cp932. What is
    below is a DBCS rule now.

    BUT THE HEAD STRIP CANNOT TELL THAT FRAGMENT FROM A WHOLE LEGACY CHARACTER,
    and this docstring used to claim it could. The strip takes bytes in
    ``0x80-0xBF``, and 3504 of the 11172 cp949-encodable modern Hangul
    syllables — 31.4%, measured 2026-09-09 on darwin/CPython 3.12.13 — have
    BOTH of their bytes in that range: ``"가"`` is ``b0 a1``, ``"각"`` ``b0
    a2``, ``"간"`` ``b0 a3``. For those the head strip eats the whole character
    and this function returns two U+FFFD without cp949 ever being asked, where
    the pre-cross-review rule returned ``"가"``. A real site: a
    :class:`~aelix_agents.print_channel.StderrRing` holding ``"가 오류: 파일
    없음"`` trimmed at an exact character boundary now reads ``"�� 오류: 파일
    없음"``. Synthetically, driving 1e8a6d6's own module beside this one over
    20000 short cp949 buffers of 1-8 syllables, NEW-wrong-where-old-was-right
    is 5476-5667 on the common ``b0-c8`` block and 9045-9115 across all
    cp949-encodable Hangul (three seeds: 239, 20260908, 20260909; the ALL half
    re-measured 2026-09-09 on darwin/CPython 3.12.13 as 9045/9115/9065).

    THE OTHER DIRECTION IS 0 ONLY FOR THE PAIR OF CLAIMS, NOT FOR THIS ARM
    ALONE, and this docstring stated the stronger thing. Old ``ragged=True``
    against today's ``ragged_head=True, ragged_tail=True`` is 0/0/0 at all
    three seeds; against ``ragged_head=True`` ALONE — the arm this paragraph is
    about — the old flag is wrong where the head-only claim is right 107, 139
    and 117 times across all cp949-encodable Hangul (re-measured 2026-09-09 on
    darwin/CPython 3.12.13). ``b7 a1 be df b7 e1`` (``"래야료"``) is one:
    measured, this function returns ``None`` at ``at_head=True,
    at_tail=False`` — the head strip eats ``b7 a1 be`` and the surviving ``df
    b7 e1`` still does not decode — so the run falls through to cp949, which
    reads it right, whereas ``at_head=True, at_tail=True`` trims the ``e1``
    that made it fail, SUCCEEDS with ``���߷�``, and cp949 is never asked. So
    the trade this paragraph books is a trade, not a strict loss.

    KEPT ANYWAY, because the head arm's gain is larger where it applies. Over
    12 realistic multi-word Korean console lines, measured 2026-09-09 on
    darwin/CPython 3.12.13 with ``fallbacks=("cp949",)``: a cp949 line trimmed
    at an EXACT character boundary loses a whole character at 10 of 136 head
    offsets, while a UTF-8 line trimmed INSIDE a character has its confidently
    wrong head turned back into U+FFFD at 132 of 206 — 143 of 206 on cp932 and
    129 of 206 on cp936. (Those are this pass's corpus; the paragraph in
    :func:`decode_child_output` gives the same trade on the cross-review's
    12 lines, 10 of 139 against 120 of 192, which is the same answer twice.)
    And because ``ragged_head`` is now claimed by two sites instead of every
    one.
    ``tests/util/test_child_output_decode.py`` pins BOTH directions —
    ``"꽃"`` (``b2 c9``, trail byte above ``0xBF``) survives and ``"가"``
    (``b0 a1``) does not — so the trade is deliberate rather than accidental.

    So one end returns ``prefix + suffix``, which is byte-for-byte what
    ``errors="replace"`` produced for the whole run; only both ends decline.
    """

    prefix = ""
    suffix = ""
    core = run
    if at_head:
        stripped = 0
        while stripped < len(core) and 0x80 <= core[stripped] <= 0xBF:
            stripped += 1
        if stripped:
            prefix = "�" * stripped
            core = core[stripped:]
    if at_tail and core:
        try:
            core.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.end == len(core) and _could_be_a_longer_character(core[exc.start :]):
                suffix = "�"
                core = core[: exc.start]
    if not core:
        # Both ends ate bytes: the run is equally a legacy character the head
        # strip cut in half, so hand it back untrimmed. ONE end ate them all
        # and we take the U+FFFD — right for a severed character, and wrong
        # for the 31.4% of cp949 Hangul whose two bytes both lie in 0x80-0xBF
        # (docstring above; the cost is measured and accepted, not overlooked).
        return None if (prefix and suffix) else ((prefix + suffix) or None)
    try:
        return prefix + core.decode("utf-8") + suffix
    except UnicodeDecodeError:
        return None


def _could_be_a_longer_character(tail: bytes) -> bool:
    """Is ``tail`` the START of a UTF-8 character, waiting for the rest?

    ``exc.end == len(run)`` alone does NOT say so, which the #239 cross-review
    found: a terminal ``0xFF`` — or ``c0``, ``c1``, ``f5``-``ff`` — errors
    there too with ``invalid start byte``, and it can never begin a character
    at all. Withholding it spends a U+FFFD where the code page had an answer.
    The cp1252 case this docstring used to measure — ``b"\\xc2\\xa2\\xff"``
    returning ``¢\ufffd`` against the unclaimed ``¢ÿ`` — can no longer show
    that: since #239's final pass a single-byte page is offered nothing, so
    both paths answer ``¢\ufffd`` and cp1252 cannot tell this rule from its
    absence. A DBCS page still can. Measured 2026-09-09 on darwin/CPython
    3.12.13, ``decode_child_output(b"\\xea\\xb0\\x92\\xc0",
    ragged_tail=True, fallbacks=("cp949",))`` returns ``媛뮹`` — four bytes
    cp949 reads as two characters — where withholding the terminal ``0xC0``
    returns ``값\ufffd``. ``0xFF`` cannot show it on any chain, because cp949
    REFUSES it and every answer is ``값\ufffd``.

    An incremental decoder settles it without reading the reason string:
    ``final=False`` means "more may follow", so it buffers a genuine truncated
    prefix and raises for anything invalid.
    """

    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(tail, final=False)
    except UnicodeDecodeError:
        return False
    return bool(decoder.getstate()[0])


__all__ = ["decode_child_output", "win32_output_fallbacks"]
