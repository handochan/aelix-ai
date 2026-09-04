"""The ``cmd.exe`` AUTO-mode classifier (#204 slice 3, ADR-0237).

The bucket table below is the contract. Unlike the PowerShell side there is no
grammar to measure a parse shape against — the lexer IS the parse — so the two
rules that a grammar would have given for free are unit-tested directly instead:
:func:`_lex` on the no-whitespace operator forms (#204 F7, the rule a whitespace
splitter gets wrong) and :func:`_statements` on which separator advances the
pipeline stage (only ``|``).

These tests RUN on Linux, macOS and the gating ``windows-latest`` leg alike.
Nothing here touches the filesystem, ``sys.platform`` or a real shell — the
classifier is a pure function of a string — so both the Windows-path and the
POSIX-path answers are asserted rather than skipped (``tests/conftest.py``
doctrine: skip only the genuinely unreachable).

Verdicts are compared by VALUE, never by identity: this module is imported
before ``test_bash_classifier.py::test_module_reimport_is_clean`` runs, and that
reload rebinds ``Verdict`` to a new class object while this module still holds
the old one. ``IntEnum`` compares equal by int across both.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.builtin.bash_classifier import _DENY_COMMANDS, Verdict
from aelix_coding_agent.builtin.shell_classifiers.cmd import (
    _CMD_ALLOW,
    _CMD_ASK,
    _CMD_DENY,
    _lex,
    _split_head,
    _statements,
    classify_cmd,
)

# === Bucket suite ===========================================================

_CASES: dict[str, Verdict] = {
    # --- ALLOW: read-only internals whose every argument was read ---
    "dir": Verdict.ALLOW,
    "dir /a /b": Verdict.ALLOW,
    "dir /a:h": Verdict.ALLOW,  # a cmd switch takes its value after ``:``
    "dir/a/b": Verdict.ALLOW,  # …and cmd needs no space before the first one
    "@dir": Verdict.ALLOW,  # ``@`` only suppresses echoing of the line
    "echo hello": Verdict.ALLOW,
    "echo a^&b": Verdict.ALLOW,  # ``^`` escapes; this is ONE statement
    "type a.txt": Verdict.ALLOW,
    "type a | findstr b": Verdict.ALLOW,
    "findstr /i foo a.txt": Verdict.ALLOW,
    "date /t": Verdict.ALLOW,  # ``/t`` DISPLAYS the date
    "date/t": Verdict.ALLOW,
    "time /t": Verdict.ALLOW,
    "sort in.txt": Verdict.ALLOW,
    "set FOO": Verdict.ALLOW,  # the one-name QUERY form
    "whoami": Verdict.ALLOW,
    "whoami /user": Verdict.ALLOW,
    "ver": Verdict.ALLOW,
    "vol": Verdict.ALLOW,
    "hostname": Verdict.ALLOW,
    "where python": Verdict.ALLOW,
    "more a.txt": Verdict.ALLOW,
    "fc a.txt b.txt": Verdict.ALLOW,
    "cd": Verdict.ALLOW,
    "cd ..": Verdict.ALLOW,
    "chdir x": Verdict.ALLOW,
    r'dir "C:\Program Files"': Verdict.ALLOW,  # reading a protected tree is fine
    # --- ASK: the ALLOW tier's gates ---
    "DIR /S": Verdict.ASK,  # a recursive listing of a drive hangs the turn
    r"dir C:\ /s": Verdict.ASK,
    "dir/s": Verdict.ASK,  # …glued, same answer
    # ``tree`` is UNCONDITIONALLY recursive, so it is not in the tier at all:
    # ``tree /f C:\`` walks the whole drive, which is exactly the hang hazard
    # ``dir``'s allowlist omits ``/s`` for. It measured ALLOW while
    # ``dir /s /b C:\`` measured ASK — the same act with two answers.
    "tree /f": Verdict.ASK,
    "tree /f C:\\": Verdict.ASK,
    "tree /s": Verdict.ASK,
    "whoami /all": Verdict.ASK,
    "more /e a.txt": Verdict.ASK,
    "type": Verdict.ASK,  # the row needs a positional
    "date": Verdict.ASK,  # PROMPTS on stdin: blocks the AUTO session forever
    "time": Verdict.ASK,
    "set": Verdict.ASK,  # dumps every environment variable
    "set FOO=bar": Verdict.ASK,  # …and this one assigns
    "set /p PASS=": Verdict.ASK,
    "set FOO BAR": Verdict.ASK,
    "hostname newname": Verdict.ASK,
    "sort /etc/hosts": Verdict.ASK,  # a ``/``-leading token is a SWITCH to cmd
    "findstr /f:list.txt x": Verdict.ASK,  # ``/f:`` reads a file LIST
    "findstr /g:pat.txt x": Verdict.ASK,
    r"cd /d C:\x": Verdict.ASK,
    # --- ASK: sensitive reads (#204 F9) ---
    r"type C:\Users\me\.ssh\id_rsa": Verdict.ASK,
    r'type "C:\Users\me\.ssh\id_rsa"': Verdict.ASK,
    r"type C:\Users\me\.aws\credentials": Verdict.ASK,
    r"more < C:\Users\me\.ssh\id_rsa": Verdict.ASK,
    # …and the ONE character that used to defeat all of it: a wildcard is not a
    # sensitive BASENAME and ``C:\Users\me\.ssh`` is not the ``~``-anchored
    # PREFIX, so every row below measured ALLOW before ``.ssh``/``.aws`` became
    # matched SEGMENTS.
    r"type C:\Users\me\.ssh\*": Verdict.ASK,
    r"more C:\Users\me\.ssh\*": Verdict.ASK,
    r"type C:\Users\me\.aws\*": Verdict.ASK,
    # ``findstr /s`` is a recursive grep of whatever it is pointed at: the
    # unbounded-read hazard ``/f:``/``/g:`` are refused for, and ``dir /s``'s
    # hang hazard. All three auto-ran a credential sweep of a whole tree.
    # The credential families the denylist did not name. Every row measured
    # ALLOW at the gate under cmd AND pwsh while ``main`` c6d424c answered ASK
    # for every Windows line — an ASK -> ALLOW move, one table entry from being
    # the wildcard case above.
    r"type C:\Users\me\.kube\config": Verdict.ASK,
    r"type C:\Users\me\.kube\*": Verdict.ASK,
    r"type C:\Users\me\.azure\accessTokens.json": Verdict.ASK,
    r"type C:\Users\me\.terraform.d\credentials.tfrc.json": Verdict.ASK,
    r"type C:\Users\me\.git-credentials": Verdict.ASK,
    r"type C:\Users\me\AppData\Roaming\gcloud\credentials.db": Verdict.ASK,
    r"type C:\Users\me\.config\gcloud\credentials.db": Verdict.ASK,
    r"type C:\app\.env.local": Verdict.ASK,
    r"type C:\Users\me\keys\id_ecdsa": Verdict.ASK,
    # The wildcard case again, one character further in. Win32 strips a trailing
    # dot from a component before it resolves, and ``::$DATA`` names the default
    # stream of the same file, so every row below opened a private key while
    # measuring ALLOW here against a basename that IS in the table; ``main``
    # c6d424c answered ASK for all four.
    r"type C:\Users\me\keys\id_rsa.": Verdict.ASK,
    r"more C:\Users\me\.netrc.": Verdict.ASK,
    r"findstr BEGIN C:\Users\me\keys\id_rsa.": Verdict.ASK,
    r"type C:\Users\me\keys\id_rsa::$DATA": Verdict.ASK,
    r"more < C:\Users\me\.kube\config": Verdict.ASK,
    r"findstr /s BEGIN C:\Users\me\.ssh\*": Verdict.ASK,
    r"findstr /s /i password C:\*": Verdict.ASK,
    r"findstr /s . C:\Users\*": Verdict.ASK,
    # --- ASK: redirects to an ordinary target (#204 F8) ---
    "dir > out.txt": Verdict.ASK,
    "type nul > out.txt": Verdict.ASK,
    "echo x > out.txt": Verdict.ASK,
    "whoami > loot.txt": Verdict.ASK,
    "set FOO > loot.txt": Verdict.ASK,
    r"tree /f C:\Users > loot.txt": Verdict.ASK,
    "dir 2>&1": Verdict.ASK,  # a handle, not a file — but still not ALLOW
    "echo x < in.txt": Verdict.ASK,
    # --- ASK: shapes the lexer refuses to interpret ---
    "d^ir": Verdict.ASK,  # an escape inside the NAME is obfuscation (F11)
    'de""l x': Verdict.ASK,
    "de^l x": Verdict.ASK,
    "%COMSPEC% /c dir": Verdict.ASK,  # a variable is never expanded
    "!cmd! /c dir": Verdict.ASK,
    "echo %PATH%": Verdict.ASK,
    'echo "unbalanced': Verdict.ASK,  # the quoting never closed
    "> out.txt dir": Verdict.ASK,  # the first token is an operator (F28)
    "(dir)": Verdict.ASK,  # a parenthesised block is not split here
    "echo ( )": Verdict.ASK,
    r"C:\Windows\System32\cmd.exe /c dir": Verdict.ASK,
    'cmd /c "del x"': Verdict.ASK,  # a nested interpreter at stage 0
    r".\evil.bat": Verdict.ASK,
    r"\\srv\share\evil.bat": Verdict.ASK,
    # --- ASK: real mutations with a legitimate agent use ---
    "del x": Verdict.ASK,  # #204 F13, and ``Remove-Item build\out.js``'s answer
    "rmdir x": Verdict.ASK,
    "dir & del x": Verdict.ASK,  # folded: ALLOW then ASK
    "start notepad": Verdict.ASK,
    'copy "a b" c': Verdict.ASK,
    "copy a ^\nb": Verdict.ASK,  # ``^`` before a newline joins the lines
    "for /f %i in ('dir') do echo %i": Verdict.ASK,
    "systeminfo": Verdict.ASK,
    "net user bob /add": Verdict.ASK,
    "xcopy a b /s": Verdict.ASK,
    "mklink a b": Verdict.ASK,
    r"reg query HKLM\x": Verdict.ASK,
    r"reg export HKLM\x f.reg": Verdict.ASK,  # writes a FILE, not the hive
    r"reg delete HKCU\x /f": Verdict.ASK,  # per-user, not machine-wide
    r"icacls .\build /grant Everyone:F": Verdict.ASK,
    r"takeown /f .\build": Verdict.ASK,
    "sort in.txt /o out.txt": Verdict.ASK,  # overwrites out.txt — see the note
    "sort in.txt /O out.txt": Verdict.ASK,  # …and cmd switches casefold
    # --- DENY: a redirect onto a protected prefix (#204 F7/F8) ---
    r"type nul>C:\Windows\System32\drivers\etc\hosts": Verdict.DENY,
    r"echo pwned>C:\Windows\System32\drivers\etc\hosts": Verdict.DENY,
    r"echo x 1>C:\Windows\y": Verdict.DENY,  # a numbered handle
    r"echo x >> C:\Windows\y": Verdict.DENY,
    r"echo x>>C:\Windows\y": Verdict.DENY,
    r"echo x 2>C:\Windows\y": Verdict.DENY,
    r'echo x>>"C:\Program Files (x86)\y"': Verdict.DENY,
    r"dir>C:\Windows\y": Verdict.DENY,
    # Win32 collapses a repeated separator and drops a component's trailing dot
    # before it opens the path; ``str.startswith`` does neither, so each of
    # these named ``C:\Windows`` while measuring ASK.
    "echo x > C:\\\\Windows\\y": Verdict.DENY,
    r"echo x > C:\Windows.\y": Verdict.DENY,
    r"echo x > C:/\Windows/y": Verdict.DENY,
    "echo x > C:\\Windows\\\\y": Verdict.DENY,
    r"ver > C:\Windows\y": Verdict.DENY,
    r"vol > C:\Windows\y": Verdict.DENY,
    r'hostname > "C:\Program Files\y"': Verdict.DENY,
    r"date /t > C:\Windows\y": Verdict.DENY,
    r"cd > C:\Windows\y": Verdict.DENY,
    r"sort in.txt > C:\Windows\y": Verdict.DENY,
    r"findstr x y > C:\Windows\z": Verdict.DENY,
    r"fc a.txt b.txt > C:\Windows\y": Verdict.DENY,
    r"dir /s > C:\Windows\y": Verdict.DENY,
    # --- DENY: ``sort``'s redirect-without-a-redirect-operator ---
    r"sort in.txt /o C:\Windows\y": Verdict.DENY,
    r"sort /O:C:\Windows\y in.txt": Verdict.DENY,
    r"sort/o C:\Windows\y in.txt": Verdict.DENY,
    r"sort /t C:\Windows": Verdict.DENY,  # ``/T`` names a TEMP directory
    # --- DENY: the recursive delete ---
    r"del /s /q C:\Windows": Verdict.DENY,
    r"DEL /S /Q C:\WINDOWS": Verdict.DENY,
    r"rd /s /q C:\Windows": Verdict.DENY,
    "rmdir /s x": Verdict.DENY,
    "erase /s x": Verdict.DENY,
    "dir & del /s x": Verdict.DENY,  # folded across a separator
    "dir && del /s x": Verdict.DENY,
    "dir\ndel /s x": Verdict.DENY,  # …and across a newline
    # The obfuscation floor is a floor, not an early return: the name still
    # resolves and the DENY table still runs.
    r"de^l /s /q C:\Windows": Verdict.DENY,
    r'"del" /s x': Verdict.DENY,
    r"del/s /q C:\Windows": Verdict.DENY,  # glued to the name
    r"del/q/s x": Verdict.DENY,  # …and clustered
    # ``;`` and ``,`` are cmd's other token delimiters, so the switch is ``/s``
    # and not ``/s;``. Both measured ASK, one character outside this DENY.
    r"del /s;/q C:\Windows": Verdict.DENY,
    r"del /s,/q C:\Windows": Verdict.DENY,
    # A parenthesised block: the lexer does not split one, so the statement is
    # floored at ASK — but the NAME still has to resolve or the DENY table is
    # never reached. Measured ASK while ``del /s x`` alone was DENY.
    "(del /s x)": Verdict.DENY,
    "(dir) & (del /s x)": Verdict.DENY,
    "( del /s x )": Verdict.DENY,
    # --- DENY: setting the clock ---
    "date 01-01-2030": Verdict.DENY,
    "time 12:00": Verdict.DENY,
    # --- DENY: unrecoverable storage / ACL / registry surfaces ---
    "format C: /q": Verdict.DENY,
    "FORMAT C:": Verdict.DENY,
    "diskpart": Verdict.DENY,
    "shutdown /s /t 0": Verdict.DENY,
    r"cipher /w:C:\\": Verdict.DENY,
    "schtasks /create /tn x /tr y": Verdict.DENY,
    r"reg delete HKLM\x /f": Verdict.DENY,
    r"reg add HKLM\x /v a /f": Verdict.DENY,
    r"reg add hklm\software\x /v a /f": Verdict.DENY,
    r"icacls C:\Windows /grant Everyone:F": Verdict.DENY,
    r"icacls C:\Windows /deny Everyone:(F)": Verdict.DENY,
    r"icacls C:\Windows /inheritance:r": Verdict.DENY,
    r"takeown /f C:\Windows": Verdict.DENY,
    "takeown /f C: /r": Verdict.DENY,
    # --- DENY: an interpreter fed a pipeline stage ---
    "dir | cmd /c del x": Verdict.DENY,
    "dir|cmd /c dir": Verdict.DENY,
    "dir | powershell -c gci": Verdict.DENY,
    r"dir | cmd.exe /c del /s x": Verdict.DENY,
}


@pytest.mark.parametrize(("command", "expected"), list(_CASES.items()))
def test_classify_bucket(command: str, expected: Verdict) -> None:
    assert classify_cmd(command) == expected, command


def test_sort_writes_are_asked_not_denied() -> None:
    """``sort /o out.txt`` is ASK, and the design document's §G says DENY.

    The rule that wins is the one the rest of the tier already follows: a write
    is DENY only when the TARGET is unrecoverable. ``sort in.txt /o out.txt``
    overwrites a file in the working directory, which is exactly what
    ``echo x > out.txt`` does — pinned ASK three rows above — and less than
    ``del x`` does, also pinned ASK. So ``/o`` and ``/t`` are run through the
    same protected-target predicate a real redirect target gets, and the DENY
    lands where it belongs.
    """

    assert classify_cmd("sort in.txt /o out.txt") == Verdict.ASK
    assert classify_cmd(r"sort in.txt /o C:\Windows\y") == Verdict.DENY
    assert classify_cmd("echo x > out.txt") == Verdict.ASK
    assert classify_cmd(r"echo x > C:\Windows\y") == Verdict.DENY


def test_obfuscating_the_name_can_never_reach_allow_nor_escape_deny() -> None:
    """#204 F11's rule is a FLOOR, and §G's ``Everyone:(F)`` row is why.

    An early ``return ASK`` for a name carrying ``^``/``"``/``%``/``!`` — or for
    a statement carrying ``(``/``)`` — reads as fail-safe and is not: it would
    hand ``de^l /s /q C:\\Windows`` and ``icacls C:\\Windows /deny Everyone:(F)``
    a prompt instead of a block, and a prompt is a thing a user clicks through.
    So the unreadable shapes raise the floor to ASK and the DENY tables still
    run on the resolved name, which is strictly stricter than either rule alone.
    """

    for command in (r"d^ir", r'de""l x', "de^l x", "echo ( )"):
        assert classify_cmd(command) == Verdict.ASK, command
    for command in (
        r"de^l /s /q C:\Windows",
        r'"del" /s x',
        r"icacls C:\Windows /deny Everyone:(F)",
        # The floor was doing its half and the name lookup was not: measured,
        # ``_split_head`` resolved the name ``(del``, which is in no table, so
        # the DENY row never ran and the block came out ASK.
        "(del /s x)",
        "(dir) & (del /s x)",
    ):
        assert classify_cmd(command) == Verdict.DENY, command


# === The F7 lexer guard =====================================================


def test_operators_need_no_whitespace() -> None:
    """The rule a whitespace splitter gets wrong, unit-tested (#204 F7).

    Measured against the draft that split on whitespace:
    ``type nul>C:\\Windows\\…\\hosts`` was TWO tokens, ``type`` and
    ``nul>C:\\Windows\\…``, so no redirect existed, the protected-target rule
    never ran, and the row's ALLOW stood. ``cmd`` needs no space around an
    operator, so neither may this lexer.
    """

    tokens = _lex(r"type nul>C:\Windows\x")
    assert tokens is not None
    assert [(token.text, token.is_operator) for token in tokens] == [
        ("type", False),
        ("nul", False),
        (">", True),
        (r"C:\Windows\x", False),
    ]

    # The numbered handle forms, which have no space either.
    assert [token.text for token in _lex("echo x 1>y") or []] == ["echo", "x", "1>", "y"]
    assert [token.text for token in _lex("dir 2>&1") or []] == ["dir", "2>&1"]
    assert [token.text for token in _lex("a>>b") or []] == ["a", ">>", "b"]

    # ``^`` escapes the operator, ``"…"`` quotes it, and neither may split.
    assert [token.text for token in _lex("echo a^&b") or []] == ["echo", "a&b"]
    assert [token.text for token in _lex('echo "a&b"') or []] == ["echo", "a&b"]
    assert [token.text for token in _lex('echo "a""b"') or []] == ["echo", 'a"b']

    # Unbalanced quoting is whole-command fatal: a lexer that lost track of
    # quoting may not claim to have found the other statements (§D.1).
    assert _lex('echo "x') is None


def test_only_a_pipe_advances_the_stage() -> None:
    """``&``/``&&``/``||``/newline are SEQUENCING, not piping.

    The same inversion guard the PowerShell side carries for ``pipeline_chain``:
    if this were backwards, "an interpreter at stage > 0 is DENY" would be dead
    code that still read as if it worked, and ``dir | cmd /c del x`` would be
    ASK while ``dir & cmd /c dir`` would be DENY — both wrong.
    """

    def stages(command: str) -> list[int]:
        tokens = _lex(command)
        assert tokens is not None
        return [stage for _statement, stage in _statements(tokens)]

    assert stages("a | b | c") == [0, 1, 2]
    assert stages("a & b") == [0, 0]
    assert stages("a && b") == [0, 0]
    assert stages("a || b") == [0, 0]
    assert stages("a\nb") == [0, 0]
    assert stages("a | b & c") == [0, 1, 0]
    # A trailing separator leaves an EMPTY statement, which is dropped rather
    # than folded in as an ASK — nothing can hide inside it.
    assert stages("dir &") == [0]


def test_a_glued_switch_is_not_a_path_component() -> None:
    """``del/s`` is ``del`` plus ``/s``, and reading it as a path lost the DENY.

    Measured before :func:`_split_head` existed: ``del/s /q C:\\Windows``
    resolved to a name of ``s``, because the basename split on ``/`` the way a
    POSIX path does. In this dialect a ``/`` in the first token is a switch
    sigil; a path separator is ``\\``.
    """

    assert _split_head("del/s") == ("del", "/s")
    assert _split_head("del/q/s") == ("del", "/q/s")
    assert _split_head(r"C:\Windows\System32\cmd.exe") == ("cmd", None)
    assert _split_head("DIR") == ("dir", None)
    assert _split_head("foo.bat") == ("foo", None)


# === Table invariants =======================================================


def test_cmd_allow_name_count() -> None:
    """Pinned because the design document counted its own ALLOW block by hand.

    It said 14 rows over a block that then named 18 commands, the same way its
    PowerShell block said 30 over 46. Counting it here is the only way the
    number stays true.
    """

    assert len(_CMD_ALLOW) == 17


def test_tiers_are_pairwise_disjoint() -> None:
    """A name in two tiers would be silently unreachable in one of them.

    ``_name_verdict`` checks the tiers in a fixed order, so a duplicate does not
    fail — the table simply stops describing the behaviour. ``del``/``rd`` and
    the ``reg``/``icacls``/``takeown`` family are deliberately in NO tier set:
    their verdict depends on a switch, so they are rule-owned, exactly as
    ``remove-item`` is on the PowerShell side.
    """

    tiers = {"allow": _CMD_ALLOW, "ask": _CMD_ASK, "deny": _CMD_DENY}
    for left in tiers:
        for right in tiers:
            if left < right:
                assert not tiers[left] & tiers[right], (left, right)
    assert not _CMD_ALLOW & _DENY_COMMANDS


def test_tables_are_canonical() -> None:
    """Every key is already in the form :func:`_split_head` produces."""

    for key in _CMD_ALLOW | _CMD_ASK | _CMD_DENY:
        assert key == key.casefold(), key
        assert " " not in key and "\t" not in key, key
        assert _split_head(key) == (key, None), key


# === Fail-safe ==============================================================


@pytest.mark.parametrize("command", ["", "   ", "\n", '"', "^"])
def test_unparsable_input_asks(command: str) -> None:
    assert classify_cmd(command) == Verdict.ASK
