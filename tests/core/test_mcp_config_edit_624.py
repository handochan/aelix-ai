"""#624 (ADR-0305 D14) — the pure ``mcp.json`` ``enabled`` edit.

``core/mcp_config_edit.edit_enabled`` changes only the ``enabled`` bytes of the
named servers and refuses, with :class:`McpRefused` and nothing else, unless its
four checks pass. These tests hold it to two oracles that do not use the
module's own splice records or comparison:

- O1: the new text parses with a duplicate-refusing hook, and its value equals
  the old value with only the asked ``enabled`` members set, type by type
  (``True`` is not ``1``, ``1.0`` is not ``1``) and in key order;
- O2: deleting exactly the needed insertions of ``,<ws>"enabled"<sep>false``
  and flipping exactly the needed ``true``/``false`` tokens gives back the old
  text's tokens and every whitespace run.

The corpus, the generators (seeds 624, 6240, 6242, at the design's full counts
200 / 2,000 / 2,000) and the seven guard mutants are ported from the design's
rev1 evidence (``.omc/probes/623-624/rev1/proto/run_rev1.py``).
"""

from __future__ import annotations

import copy
import itertools
import json
import random
import re
from collections import Counter
from collections.abc import Callable, Sequence

import pytest
from aelix_coding_agent.core import mcp_config_edit as m
from aelix_coding_agent.core.mcp_config_edit import McpRefused, edit_enabled

SECRET = "SECRET-624"
Changes = list[tuple[str, bool]]

# The design's bar (ADR-0305 D14): 200 json.dumps files, 2,000 re-laid-out, 2,000 mixed.
N_DUMPS = 200
N_RELAYOUT = 2000
N_MIXED = 2000


# --- the oracles (independent of the module) ----------------------------------------

TOKEN_RE = re.compile(
    r'[ \t\r\n]+|"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?'
    r"|true|false|null|[{}\[\]:,]",
    re.S,
)
INSERT_ANY = re.compile(r',(?:\r?\n[ \t]*|[ \t]*)"enabled"[ \t]*:[ \t]*false')


class _Dup(Exception):
    pass


def _strict_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [k for k, _v in pairs]
    if len(keys) != len(set(keys)):
        raise _Dup(keys)
    return dict(pairs)


def _strict_loads(text: str) -> object:
    def constant(name: str) -> object:
        raise ValueError(name)

    return json.loads(text, object_pairs_hook=_strict_hook, parse_constant=constant)


def _typed_equal(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_typed_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_typed_equal(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def tokens(text: str) -> list[str]:
    out: list[str] = []
    at = 0
    for match in TOKEN_RE.finditer(text):
        if match.start() != at:
            raise ValueError(f"untokenisable at {at}")
        out.append(match.group())
        at = match.end()
    if at != len(text):
        raise ValueError(f"untokenisable at {at}")
    return out


def needs(value: dict, changes: Sequence[tuple[str, bool]]) -> tuple[int, int]:
    """(insertions, flips) the asked changes need."""

    k = flips = 0
    for name, want in changes:
        entry = value["mcpServers"][name]
        if "enabled" in entry:
            flips += entry["enabled"] != want
        elif not want:
            k += 1
    return k, flips


def oracle(old: str, new: str, changes: Sequence[tuple[str, bool]]) -> str | None:
    """None when both oracles hold, else which one failed."""

    old_value = json.loads(old)
    expected = copy.deepcopy(old_value)
    for name, want in changes:
        entry = expected["mcpServers"][name]
        if "enabled" in entry or not want:
            entry["enabled"] = want
    try:
        got = _strict_loads(new)
    except (_Dup, ValueError) as exc:
        return f"O1 parse: {type(exc).__name__}"
    if not _typed_equal(got, expected):
        return "O1 value differs"
    k, flips = needs(old_value, changes)
    old_tokens = tokens(old)
    spans = [x.span() for x in INSERT_ANY.finditer(new)]
    for chosen in itertools.combinations(spans, k):
        parts: list[str] = []
        at = 0
        for a, b in chosen:
            parts.append(new[at:a])
            at = b
        parts.append(new[at:])
        try:
            back = tokens("".join(parts))
        except ValueError:
            continue
        if len(back) != len(old_tokens):
            continue
        diffs = [(a, b) for a, b in zip(old_tokens, back, strict=True) if a != b]
        if len(diffs) == flips and all({a, b} == {"true", "false"} for a, b in diffs):
            return None
    return f"O2 no inverse with k={k} flips={flips}"


# --- the hand corpus (32 shapes, opus H1's three included) ----------------------------

DEEP = '{"mcpServers": {"fs": {"command": "x", "args": ' + "[" * 100_000 + "]" * 100_000 + "}}}"
CORPUS: dict[str, str | bytes] = {
    "two-space-absent": '{\n  "mcpServers": {\n    "fs": {\n      "command": "npx",\n      "args": ["-y", "pkg", "."],\n      "env": {"TOKEN": "SECRET-624"}\n    }\n  }\n}\n',
    "minified": '{"mcpServers":{"fs":{"command":"npx","args":["-y"]}},"$schema":"x"}',
    "one-line-server-in-multiline": '{\n  "mcpServers": {\n    "fs": {"command": "npx", "args": ["-y", "pkg"]},\n    "web": {"url": "https://h/mcp?token=SECRET-624"}\n  }\n}\n',
    "crlf-tabs": '{\r\n\t"mcpServers": {\r\n\t\t"fs": {\r\n\t\t\t"command": "npx",\r\n\t\t\t"enabled": true\r\n\t\t}\r\n\t}\r\n}',
    "escaped-key": '{"mcpServers": {"fs": {"command": "npx", "enab\\u006ced": false, "n": 1.50, "u": "a\\/b", "e": "\\u00e9"}}}\n',
    "dup-enabled": '{"mcpServers": {"fs": {"command": "npx", "enabled": true, "enabled": false}}}',
    "non-bool": '{"mcpServers": {"fs": {"command": "npx", "enabled": "SECRET-624"}}}',
    "H1-two-members-one-line": '{\n  "mcpServers": {\n    "fs": {\n      "args": ["--token", "SECRET-624"], "command": "x"\n    }\n  }\n}\n',
    "H1-command-args-one-line": '{\n  "mcpServers": {\n    "fs": {\n      "command": "x", "args": []\n    }\n  }\n}\n',
    "H1-member-after-closing-brace": '{\n  "mcpServers": {\n    "fs": {\n      "env": {\n        "TOKEN": "SECRET-624"\n      }, "command": "x"\n    }\n  }\n}\n',
    "object-opens-on-key-line": '{"mcpServers": {"fs": { "command": "x",\n    "args": ["SECRET-624"]\n  }}}\n',
    "object-opens-on-key-line-one-member": '{"mcpServers": {"fs": { "command": "x"\n  }}}\n',
    "prettier-inline-objects": '{\n  "mcpServers": {\n    "fs": { "command": "npx", "args": ["-y", "pkg"] },\n    "web": { "url": "https://h/mcp", "headers": { "Authorization": "Bearer SECRET-624" } }\n  }\n}\n',
    "separator-with-newline": '{"mcpServers": {"fs": {"command"\n  :\n  "x"}}}',
    "mixed-newlines": '{\n  "mcpServers": {\r\n    "fs": {\r\n      "command": "x"\n    }\n  }\n}\n',
    "escaped-server-name": '{"mcpServers": {"f\\u0073": {"command": "x"}}}',
    "crlf-enabled-false": '{\r\n  "mcpServers": {\r\n    "fs": {\r\n      "command": "x",\r\n      "enabled": false\r\n    }\r\n  }\r\n}\r\n',
    "numbers-elsewhere": '{"mcpServers": {"fs": {"command": "x", "timeout": 1.50, "big": 1e400, "neg": -0, "e": 1E3}}, "n": 10.0}',
    "secret-everywhere": '{"mcpServers": {"fs": {"command": "SECRET-624", "args": ["SECRET-624"], "env": {"K": "SECRET-624"}}, "web": {"url": "https://SECRET-624@h/SECRET-624?t=SECRET-624", "headers": {"H": "SECRET-624"}}}}',
    # the refusals
    "nan-elsewhere": '{"mcpServers": {"fs": {"command": "x"}}, "t": NaN}',
    "invalid-utf8": b'{"mcpServers": {"fs": {"command": "x\xff"}}}',
    "bom": b'\xef\xbb\xbf{"mcpServers": {"fs": {"command": "x"}}}',
    "trailing-comma": '{"mcpServers": {"fs": {"command": "x",}}}',
    "comment": '{"mcpServers": {"fs": {"command": "x"} // c\n}}',
    "deep-nesting": DEEP,
    "dup-server": '{"mcpServers": {"fs": {"command": "x"}, "fs": {"command": "SECRET-624"}}}',
    "dup-in-env": '{"mcpServers": {"fs": {"command": "x", "env": {"K": "a", "K": "SECRET-624"}}}}',
    "dup-mcpServers": '{"mcpServers": {"fs": {"command": "x"}}, "mcpServers": {}}',
    "empty-server": '{"mcpServers": {"fs": {}}}',
    "server-not-object": '{"mcpServers": {"fs": "SECRET-624"}}',
    "no-mcpServers": '{"servers": {}}',
    "top-level-array": '[{"mcpServers": {}}]',
}
EXPECT_REFUSED = {
    "dup-enabled",
    "non-bool",
    "nan-elsewhere",
    "invalid-utf8",
    "bom",
    "trailing-comma",
    "comment",
    "deep-nesting",
    "dup-server",
    "dup-in-env",
    "dup-mcpServers",
    "empty-server",
    "server-not-object",
    "no-mcpServers",
    "top-level-array",
}
EDITABLE = sorted(set(CORPUS) - EXPECT_REFUSED)


def _bytes(raw: str | bytes) -> bytes:
    return raw if isinstance(raw, bytes) else raw.encode("utf-8")


def test_the_corpus_has_its_32_shapes_and_15_refusals() -> None:
    assert len(CORPUS) == 32
    assert len(EXPECT_REFUSED) == 15
    assert set(CORPUS) >= EXPECT_REFUSED


@pytest.mark.parametrize("want", [False, True])
@pytest.mark.parametrize("name", EDITABLE)
def test_corpus_edit_changes_only_enabled(name: str, want: bool) -> None:
    data = _bytes(CORPUS[name])
    new = edit_enabled(data, [("fs", want)])
    assert oracle(data.decode(), new.decode(), [("fs", want)]) is None
    k, flips = needs(json.loads(data), [("fs", want)])
    assert (new != data) == bool(k or flips)


@pytest.mark.parametrize("name", EDITABLE)
def test_corpus_off_then_on_flips_one_literal_back(name: str) -> None:
    data = _bytes(CORPUS[name])
    off = edit_enabled(data, [("fs", False)])
    back = edit_enabled(off, [("fs", True)])
    assert oracle(off.decode(), back.decode(), [("fs", True)]) is None
    if json.loads(data)["mcpServers"]["fs"].get("enabled") is True:
        assert back == data
    else:
        # The key is kept where pi deletes it (ADR-0305 D14): one literal, reversible.
        assert json.loads(back)["mcpServers"]["fs"]["enabled"] is True


@pytest.mark.parametrize("want", [False, True])
@pytest.mark.parametrize("name", sorted(EXPECT_REFUSED))
def test_refusal_shapes_raise_mcp_refused_and_quote_no_value(name: str, want: bool) -> None:
    data = _bytes(CORPUS[name])
    try:
        edit_enabled(data, [("fs", want)])
    except McpRefused as exc:
        text = str(exc)
    else:
        pytest.fail(f"{name}: edited, not refused")
    assert SECRET not in text
    assert text.endswith("by hand"), text


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (
            "bom",
            "the file starts with a byte-order mark, which aelix does not read; edit it by hand",
        ),
        ("invalid-utf8", "the file is not UTF-8, which aelix does not read; edit it by hand"),
        (
            "nan-elsewhere",
            "the file holds NaN or Infinity, which JSON does not allow; edit it by hand",
        ),
        ("trailing-comma", "the file is not valid JSON; edit it by hand"),
        ("dup-in-env", 'two members named "K" in one object; edit it by hand'),
        ("non-bool", 'server "fs": "enabled" is not true or false; edit the file by hand'),
        ("empty-server", 'server "fs" is not an object with members; edit the file by hand'),
        ("no-mcpServers", 'the file has no "mcpServers" object; edit it by hand'),
        ("top-level-array", 'the file is not a JSON object with "mcpServers"; edit it by hand'),
    ],
)
def test_refusal_texts_say_which_check(name: str, text: str) -> None:
    with pytest.raises(McpRefused) as info:
        edit_enabled(_bytes(CORPUS[name]), [("fs", False)])
    assert str(info.value) == text


def test_three_changes_in_one_save() -> None:
    text = (
        '{\n  "mcpServers": {\n    "a": {"command": "x"},\n    "b": {\n      "command": "y",\n'
        '      "enabled": false\n    },\n    "c": {"url": "https://h", "enabled": true}\n  }\n}\n'
    )
    changes = [("c", False), ("a", False), ("b", True)]
    new = edit_enabled(text.encode(), changes).decode()
    assert oracle(text, new, changes) is None
    assert new == (
        '{\n  "mcpServers": {\n    "a": {"command": "x","enabled": false},\n    "b": {\n'
        '      "command": "y",\n      "enabled": true\n    },\n'
        '    "c": {"url": "https://h", "enabled": false}\n  }\n}\n'
    )


# --- the inserted member's style (opus H1) ----------------------------------------------

STYLE_CASES = [
    # its own line: the last member's indent and the newline that ends the line before
    (
        '{\n  "mcpServers": {\n    "fs": {\n      "command": "x"\n    }\n  }\n}\n',
        '{\n  "mcpServers": {\n    "fs": {\n      "command": "x",\n      "enabled": false\n    }\n  }\n}\n',
    ),
    (
        '{\r\n\t"mcpServers": {\r\n\t\t"fs": {\r\n\t\t\t"command": "x"\r\n\t\t}\r\n\t}\r\n}',
        '{\r\n\t"mcpServers": {\r\n\t\t"fs": {\r\n\t\t\t"command": "x",\r\n\t\t\t"enabled": false\r\n\t\t}\r\n\t}\r\n}',
    ),
    # two members on one line: inline, after the gap between them (H1)
    (
        '{"mcpServers": {"fs": {\n  "command": "x", "args": []\n}}}',
        '{"mcpServers": {"fs": {\n  "command": "x", "args": [], "enabled": false\n}}}',
    ),
    (
        '{"mcpServers": {"fs": {"command": "x",\t"args": []}}}',
        '{"mcpServers": {"fs": {"command": "x",\t"args": [],\t"enabled": false}}}',
    ),
    # a member after a closing brace (H1)
    (
        '{"mcpServers": {"fs": {\n  "env": {\n    "K": "v"\n  }, "command": "x"\n}}}',
        '{"mcpServers": {"fs": {\n  "env": {\n    "K": "v"\n  }, "command": "x", "enabled": false\n}}}',
    ),
    # a single member after the brace: its spacing
    (
        '{"mcpServers": {"fs": { "command": "x" }}}',
        '{"mcpServers": {"fs": { "command": "x", "enabled": false }}}',
    ),
    (
        '{"mcpServers": {"fs": {"command": "x"}}}',
        '{"mcpServers": {"fs": {"command": "x","enabled": false}}}',
    ),
    # the separator: copied when it is [ \t]*:[ \t]*, else ": "
    (
        '{"mcpServers": {"fs": {"command" :\t"x"}}}',
        '{"mcpServers": {"fs": {"command" :\t"x","enabled" :\tfalse}}}',
    ),
    (
        '{"mcpServers": {"fs": {"command"\n:\n"x"}}}',
        '{"mcpServers": {"fs": {"command"\n:\n"x","enabled": false}}}',
    ),
    # a gap that is not ",[ \t]*" falls back to ", "
    (
        '{"mcpServers": {"fs": {"a": 1\n, "command": "x"}}}',
        '{"mcpServers": {"fs": {"a": 1\n, "command": "x", "enabled": false}}}',
    ),
]


@pytest.mark.parametrize(("old", "expected"), STYLE_CASES)
def test_inserted_member_copies_the_object_style(old: str, expected: str) -> None:
    assert edit_enabled(old.encode(), [("fs", False)]).decode() == expected


def test_escaped_enabled_key_and_server_name_are_found() -> None:
    old = '{"mcpServers": {"f\\u0073": {"command": "x", "enab\\u006ced": true}}}'
    new = edit_enabled(old.encode(), [("fs", False)]).decode()
    assert new == old.replace("true", "false")


def test_nothing_to_change_returns_the_same_bytes() -> None:
    data = b'{"mcpServers": {"a": {"command": "x"}, "b": {"command": "y", "enabled": false}}}'
    assert edit_enabled(data, [("a", True), ("b", False)]) == data
    assert edit_enabled(data, []) == data


def test_an_unreadable_file_is_refused_even_with_no_change() -> None:
    with pytest.raises(McpRefused):
        edit_enabled(b'{"mcpServers": {}', [])


@pytest.mark.parametrize(
    ("changes", "word"),
    [
        ([("a", False), ("a", True)], "twice"),
        ([("a", 1)], "true or false"),
        ([(1, False)], "true or false"),
        ([(f"s{i}", False) for i in range(1025)], "too many"),
    ],
)
def test_the_changes_themselves_are_checked(changes: Changes, word: str) -> None:
    data = b'{"mcpServers": {"a": {"command": "x"}}}'
    with pytest.raises(McpRefused, match=word):
        edit_enabled(data, changes)


def test_a_missing_server_is_refused_by_name() -> None:
    with pytest.raises(McpRefused, match='server "gone" is not in "mcpServers"'):
        edit_enabled(b'{"mcpServers": {"a": {"command": "x"}}}', [("gone", False)])


def test_refusal_names_are_printable_and_bounded() -> None:
    long = "\x07" + "s" * 100
    data = json.dumps({"mcpServers": {long: {"command": "x", "enabled": "SECRET-624"}}}).encode()
    with pytest.raises(McpRefused) as info:
        edit_enabled(data, [(long, False)])
    text = str(info.value)
    assert "\x07" not in text
    assert 'server "?' + "s" * 62 + '…"' in text
    assert SECRET not in text


@pytest.mark.parametrize("where", ["_value", "_same", "deepcopy"])
def test_recursion_anywhere_is_a_refusal(monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise RecursionError

    if where == "deepcopy":
        monkeypatch.setattr(m.copy, "deepcopy", boom)
    else:
        monkeypatch.setattr(m, where, boom)
    with pytest.raises(McpRefused, match="by hand"):
        edit_enabled(b'{"mcpServers": {"a": {"command": "x"}}}', [("a", False)])


def test_random_byte_damage_is_refused_or_edited_never_raised() -> None:
    rng = random.Random(6243)
    base = _bytes(CORPUS["two-space-absent"])
    outcomes: Counter[str] = Counter()
    for _ in range(600):
        data = bytearray(base)
        for _ in range(rng.randint(1, 4)):
            at = rng.randrange(len(data))
            data[at] = rng.choice(b'{}[]:,"\\ \n\r\ttfn0123456789.-eE\xff\xc3')
        want = rng.random() < 0.5
        try:
            new = edit_enabled(bytes(data), [("fs", want)])
        except McpRefused as exc:
            assert SECRET not in str(exc)
            outcomes["refused"] += 1
            continue
        assert oracle(bytes(data).decode(), new.decode(), [("fs", want)]) is None
        outcomes["edited"] += 1
    assert outcomes["refused"] > 0
    assert outcomes["edited"] > 0


# --- each guard on its own: either one alone refuses what it is for ---------------------------

GUARD_OLD = '{"mcpServers": {"a": {"command": "x", "n": 1.0, "t": true}, "b": {"command": "y"}}}\n'
GUARD_NEW = GUARD_OLD.replace('"y"}', '"y","enabled": false}')
GUARD_SPLICE = m._Splice(GUARD_OLD.index('"y"}') + 3, "", ',"enabled": false')


@pytest.mark.parametrize(
    "text", ["{} x", '{"a": "unterminated}', '{"a": tru}', '{"a" 1}', "[1, 2", "-"]
)
def test_the_span_scanner_refuses_on_its_own(text: str) -> None:
    """Any scanner error is a refusal, never another exception, even on text the strict
    parse would have refused first."""

    with pytest.raises(McpRefused, match="could not be read for editing"):
        m._scan(text)


def test_the_byte_and_value_guards_pass_the_planned_edit() -> None:
    assert edit_enabled(GUARD_OLD.encode(), [("b", False)]).decode() == GUARD_NEW
    m._check_bytes(GUARD_OLD, GUARD_NEW, [GUARD_SPLICE])
    m._check_value(json.loads(GUARD_OLD), GUARD_NEW, [("b", False)])


def _splices_case(old: str, *splices: m._Splice) -> tuple[str, str, list[m._Splice]]:
    """A case whose new text is exactly *old* with *splices* applied: only the splice
    check under test can refuse it."""

    return old, m._apply(old, list(splices)), list(splices)


_AT_Y = GUARD_OLD.index('"y"}') + 3
_SPACED = GUARD_OLD.replace('"y"}', '"y" }')
_BYTE_CASES = {
    # bytes outside the splices: before one, inside the inserted text, after the last
    "respelt-before": (GUARD_OLD, GUARD_NEW.replace("1.0", "1.00"), [GUARD_SPLICE]),
    "same-length-change-before": (GUARD_OLD, GUARD_NEW.replace('"x"', '"z"'), [GUARD_SPLICE]),
    "inserted-differs": (GUARD_OLD, GUARD_NEW.replace(',"enabled"', ', "enabled"'), [GUARD_SPLICE]),
    "inserted-same-length-differs": (
        GUARD_OLD,
        GUARD_NEW.replace('"enabled": false', '"enabled": fa1se'),
        [GUARD_SPLICE],
    ),
    "newline-dropped": (GUARD_OLD, GUARD_NEW[:-1], [GUARD_SPLICE]),
    # the splices themselves, applied faithfully
    "not-a-flip": _splices_case(GUARD_OLD, m._Splice(GUARD_OLD.index("true"), "true", "1")),
    "not-the-insert-shape": _splices_case(GUARD_OLD, m._Splice(_AT_Y, "", ',\n\n"enabled": false')),
    "inserted-twice-at-one-place": _splices_case(GUARD_OLD, GUARD_SPLICE, GUARD_SPLICE),
    "out-of-order": (
        GUARD_OLD,
        GUARD_NEW.replace("true", "false"),
        [GUARD_SPLICE, m._Splice(GUARD_OLD.index("true"), "true", "false")],
    ),
    "after-whitespace": _splices_case(
        _SPACED, m._Splice(_SPACED.index('"y" }') + 4, "", ',"enabled": false')
    ),
    "not-before-a-brace": _splices_case(
        GUARD_OLD, m._Splice(GUARD_OLD.index('"x"') + 3, "", ',"enabled": false')
    ),
    "flip-off-its-literal": _splices_case(
        GUARD_OLD, m._Splice(GUARD_OLD.index("true") + 1, "true", "false")
    ),
}


@pytest.mark.parametrize("case", sorted(_BYTE_CASES))
def test_the_byte_guard_alone_refuses(case: str) -> None:
    old, new, splices = _BYTE_CASES[case]
    with pytest.raises(McpRefused, match="nothing written"):
        m._check_bytes(old, new, splices)


@pytest.mark.parametrize(
    "new",
    [
        GUARD_NEW.replace("true", "1"),  # True == 1 in Python
        GUARD_NEW.replace("1.0", "1"),  # 1.0 == 1 in Python
        GUARD_NEW.replace('"command": "x", "n": 1.0', '"n": 1.0, "command": "x"'),  # key order
        GUARD_NEW.replace('"enabled": false', '"enabled": true'),
        GUARD_NEW.replace('"enabled": false', '"enabled": false, "enabled": false'),
        GUARD_OLD,  # the change not made
    ],
)
def test_the_value_guard_alone_refuses(new: str) -> None:
    with pytest.raises(McpRefused):
        m._check_value(json.loads(GUARD_OLD), new, [("b", False)])


def test_the_value_guard_keeps_an_absent_key_absent_when_turned_on() -> None:
    old = '{"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}}'
    new = old.replace('"y"}', '"y", "enabled": false}')
    m._check_value(json.loads(old), new, [("a", True), ("b", False)])
    with pytest.raises(McpRefused):
        m._check_value(
            json.loads(old),
            new.replace('"x"}', '"x", "enabled": true}'),
            [("a", True), ("b", False)],
        )


# --- the generators -------------------------------------------------------------------------


def random_doc(rng: random.Random) -> dict:
    servers: dict[str, dict] = {}
    for k in range(rng.randint(1, 5)):
        entry: dict = {}
        keys = ["command", "args", "env", "url", "headers", "timeout", "nested", "enabled"]
        rng.shuffle(keys)
        stdio = rng.random() < 0.6
        for key in keys:
            if key == "command" and stdio:
                entry["command"] = rng.choice(["npx", "uvx", "/bin/x", "C:\\bin\\x.exe", SECRET])
            elif key == "url" and not stdio:
                entry["url"] = f"https://h{k}/mcp?token={SECRET}"
            elif key == "args" and rng.random() < 0.7:
                entry["args"] = ["-y", f"p{k}", "--token", SECRET, "é"]
            elif key == "env" and rng.random() < 0.5:
                entry["env"] = {"TOKEN": SECRET, "N": "1"}
            elif key == "headers" and rng.random() < 0.3:
                entry["headers"] = {"Authorization": f"Bearer {SECRET}"}
            elif key == "timeout" and rng.random() < 0.3:
                entry["timeout"] = rng.choice([1.5, 10, 0.25, -0.0, 1e21])
            elif key == "nested" and rng.random() < 0.2:
                entry["nested"] = {"a": [{"b": None}, [], {}], "t": True}
            elif key == "enabled" and rng.random() < 0.5:
                entry["enabled"] = rng.random() < 0.5
        if not entry or ("command" not in entry and "url" not in entry):
            entry["command"] = "x"
        servers[f"s{k}" if rng.random() < 0.8 else f's{k}-é/\\"q'] = entry
    doc: dict = {}
    if rng.random() < 0.3:
        doc["$schema"] = "https://example/x"
    doc["mcpServers"] = servers
    if rng.random() < 0.3:
        doc["inputs"] = [{"id": "t", "password": True}]
    return doc


def relayout(text: str, rng: random.Random) -> str:
    """Every whitespace gap between two tokens re-chosen: removed, a space, a
    newline with a random indent, inlined: shapes a json.dumps corpus never reaches."""

    toks = [t for t in tokens(text) if t[0] not in " \t\r\n"]
    out = [toks[0]]
    for tok in toks[1:]:
        r = rng.random()
        if r < 0.35:
            gap = ""
        elif r < 0.65:
            gap = " "
        elif r < 0.85:
            gap = "\n" + rng.choice(["", "  ", "    ", "\t", "      ", " \t"])
        elif r < 0.95:
            gap = rng.choice(["  ", "\t", " \t "])
        else:
            gap = "\n\n" + rng.choice(["", "  "])
        out.append(gap)
        out.append(tok)
    return "".join(out)


def generated_case(rng: random.Random, mode: str) -> tuple[str, dict, Changes]:
    doc = random_doc(rng)
    indent = rng.choice([None, 1, 2, 4, "\t"])
    seps = rng.choice([(",", ":"), (", ", ": "), (",", ": ")])
    text = json.dumps(
        doc,
        indent=indent,
        separators=seps if indent is None else (seps[0].strip(), seps[1]),
        ensure_ascii=rng.random() < 0.5,
    )
    if mode == "relayout" or (mode == "mixed" and rng.random() < 0.7):
        text = relayout(text, rng)
    if rng.random() < 0.3:
        text = text.replace("\n", "\r\n")
    if rng.random() < 0.5:
        text += rng.choice(["\n", "\r\n", "  \n"])
    names = list(doc["mcpServers"])
    changes = [(n, rng.random() < 0.5) for n in rng.sample(names, rng.randint(1, len(names)))]
    return text, doc, changes


def generated(
    seed: int,
    n: int,
    mode: str,
    edit: Callable[[bytes, Sequence[tuple[str, bool]]], bytes] = edit_enabled,
) -> tuple[Counter[str], list[str]]:
    """Counts of edited / unchanged / refused / raised / oracle-failed over *n* files."""

    rng = random.Random(seed)
    counts: Counter[str] = Counter()
    failures: list[str] = []
    for i in range(n):
        text, _doc, changes = generated_case(rng, mode)
        try:
            new = edit(text.encode("utf-8"), changes).decode("utf-8")
        except McpRefused as exc:
            counts["refused"] += 1
            failures.append(f"#{i} refused: {exc}")
            continue
        except Exception as exc:  # the whole point: nothing escapes as another exception
            counts["raised"] += 1
            failures.append(f"#{i} raised {type(exc).__name__}")
            continue
        verdict = oracle(text, new, changes)
        if verdict is not None:
            counts["oracle-failed"] += 1
            failures.append(f"#{i} {verdict}")
            continue
        k, flips = needs(json.loads(text), changes)
        counts["edited" if (k or flips) else "unchanged"] += 1
        counts["insertions"] += k
        counts["flips"] += flips
    return counts, failures


@pytest.mark.parametrize(
    ("seed", "n", "mode"),
    [(624, N_DUMPS, "dumps"), (6240, N_RELAYOUT, "relayout"), (6242, N_MIXED, "mixed")],
)
def test_generated_files_edit_cleanly(seed: int, n: int, mode: str) -> None:
    counts, failures = generated(seed, n, mode)
    assert failures == []
    assert counts["refused"] == counts["raised"] == counts["oracle-failed"] == 0
    assert counts["edited"] + counts["unchanged"] == n
    assert counts["insertions"] > 0
    assert counts["flips"] > 0


# --- the guard mutants: a splice bug must refuse, never return a wrong file ---------------------


def _insertion_round0(text: str, obj: m._Node) -> m._Splice:
    """Round 0's bug: the indent is everything before the key on its line (opus H1)."""

    last = obj.members[-1]
    sep = text[last.key_end : last.value.start]
    line_start = text.rfind("\n", 0, last.key_start) + 1
    if "\n" in text[obj.start : last.key_start]:
        lead = ",\n" + text[line_start : last.key_start]
    else:
        lead = ", "
    return m._Splice(last.value.end, "", lead + '"enabled"' + sep + "false")


def _plan_mutant(kind: str) -> Callable[..., list[m._Splice]]:
    real_plan = m._plan

    def plan(text: str, root: m._Node, changes: Sequence[tuple[str, bool]]) -> list[m._Splice]:
        out = []
        for sp in real_plan(text, root, changes):
            if kind == "offset+1" and sp.removed == "":
                sp = m._Splice(sp.offset + 1, "", sp.inserted)
            elif kind == "write-true-for-false":
                sp = m._Splice(sp.offset, sp.removed, sp.inserted.replace("false", "true"))
            elif kind == "dup-member" and sp.removed == "":
                sp = m._Splice(sp.offset, "", sp.inserted + sp.inserted)
            out.append(sp)
        return out

    return plan


_TRUE_TOKEN = re.compile(r'(?<![\w"])true(?![\w"])')


def _apply_mutant(kind: str) -> Callable[[str, Sequence[m._Splice]], str]:
    real_apply = m._apply

    def apply(text: str, splices: Sequence[m._Splice]) -> str:
        new = real_apply(text, splices)
        if kind == "other-true-to-1":
            taken, delta = [], 0
            for sp in splices:
                taken.append((sp.offset + delta, sp.offset + delta + len(sp.inserted)))
                delta += len(sp.inserted) - len(sp.removed)
            for hit in _TRUE_TOKEN.finditer(new):
                if all(not (a <= hit.start() < b) for a, b in taken):
                    return new[: hit.start()] + "1" + new[hit.end() :]
        elif kind == "number-respell":
            return new.replace("1.50", "1.5", 1)
        elif kind == "drop-trailing-newline" and new.endswith("\n"):
            return new[:-1]
        return new

    return apply


MUTANTS = [
    "indent-whole-line",
    "other-true-to-1",
    "number-respell",
    "offset+1",
    "write-true-for-false",
    "dup-member",
    "drop-trailing-newline",
]


def mutant_inputs() -> list[tuple[str, Changes]]:
    base: list[tuple[str, Changes]] = [
        (str(CORPUS["H1-two-members-one-line"]), [("fs", False)]),
        (str(CORPUS["H1-command-args-one-line"]), [("fs", False)]),
        (str(CORPUS["H1-member-after-closing-brace"]), [("fs", False)]),
        (
            '{\n  "mcpServers": {\n    "a": {"command": "x", "enabled": true, "n": 1.50},\n'
            '    "fs": {\n      "command": "y"\n    }\n  }\n}\n',
            [("fs", False)],
        ),
        (
            '{"mcpServers": {"a": {"command": "x", "enabled": true}, '
            '"fs": {"command": "y", "enabled": true, "t": 1.50}}}\n',
            [("fs", False)],
        ),
        (
            '{\n  "mcpServers": {\n    "fs": {\n      "command": "y",\n      "enabled": false\n'
            '    },\n    "b": {"url": "u", "enabled": true}\n  }\n}\n',
            [("fs", True)],
        ),
    ]
    rng = random.Random(6241)
    for _ in range(300):
        doc = random_doc(rng)
        doc["mcpServers"]["zz"] = {"command": "x", "enabled": True, "n": 1.5}
        text = json.dumps(doc, indent=2)
        text = relayout(text, rng).replace("1.5,", "1.50,").replace("1.5}", "1.50}") + "\n"
        name = rng.choice(list(doc["mcpServers"]))
        base.append((text, [(name, rng.random() < 0.5)]))
    return base


def run_mutant(
    monkeypatch: pytest.MonkeyPatch, mutant: str, inputs: list[tuple[str, Changes]]
) -> tuple[int, int, int]:
    """(reached, refused, written-wrong) for one guard mutant over *inputs*."""

    correct = [edit_enabled(text.encode(), changes) for text, changes in inputs]
    seen: list[str] = []
    with monkeypatch.context() as patch:
        if mutant == "indent-whole-line":
            patch.setattr(m, "_insertion", _insertion_round0)
        elif mutant in ("offset+1", "write-true-for-false", "dup-member"):
            patch.setattr(m, "_plan", _plan_mutant(mutant))
        mutated_apply = _apply_mutant(mutant)

        def recording_apply(text: str, splices: Sequence[m._Splice]) -> str:
            seen.append(mutated_apply(text, splices))
            return seen[-1]

        patch.setattr(m, "_apply", recording_apply)
        reached = refused = wrong = 0
        for (text, changes), good in zip(inputs, correct, strict=True):
            seen.clear()
            try:
                got = edit_enabled(text.encode(), changes)
            except McpRefused:
                got = None
            reached += bool(seen) and seen[-1] != good.decode()
            if got is None:
                refused += 1
            elif got != good:
                wrong += 1
    return reached, refused, wrong


@pytest.fixture(scope="module")
def guard_inputs() -> list[tuple[str, Changes]]:
    return mutant_inputs()


@pytest.mark.parametrize("mutant", MUTANTS)
def test_guard_mutant_is_refused_never_written(
    monkeypatch: pytest.MonkeyPatch, mutant: str, guard_inputs: list[tuple[str, Changes]]
) -> None:
    reached, refused, wrong = run_mutant(monkeypatch, mutant, guard_inputs)
    assert len(guard_inputs) == 306
    assert wrong == 0
    assert reached > 0, "the mutant changed no input: the check would be vacuous"
    assert refused >= reached


@pytest.mark.parametrize("want", [True, False])
def test_an_enabled_null_is_refused_by_the_shape_check(want: bool) -> None:
    """``null`` is not ``true``/``false``: the shape check says so (verifier mutant M26),
    not a later guard in other words."""

    data = b'{"mcpServers": {"fs": {"command": "x", "enabled": null}}}'
    with pytest.raises(McpRefused) as info:
        edit_enabled(data, [("fs", want)])
    assert str(info.value) == 'server "fs": "enabled" is not true or false; edit the file by hand'
