"""Sprint 6h₆ (Phase 5a-i, ADR-0089) — ``cli/args.py`` tests.

Covers every flag in the Pi 30+-flag inventory, the three Pi-specific
parser features that motivated rejecting argparse (P-386), and
diagnostic combos.
"""

from __future__ import annotations

import io

import pytest
from aelix_coding_agent.cli.args import (
    VALID_THINKING_LEVELS,
    Args,
    parse_args,
    print_help,
)

# === Empty / trivial =========================================================


def test_empty_argv_returns_defaults() -> None:
    args = parse_args([])
    assert args.mode == "text"
    assert args.print_mode is False
    assert args.help is False
    assert args.version is False
    assert args.messages == []
    assert args.file_args == []
    assert args.unknown_flags == {}
    assert args.diagnostics == []


def test_default_dataclass_construction() -> None:
    args = Args()
    assert args.mode == "text"
    assert args.list_models is None
    assert args.messages == []


# === --help / --version ======================================================


def test_help_long() -> None:
    assert parse_args(["--help"]).help is True


def test_help_short() -> None:
    assert parse_args(["-h"]).help is True


def test_version_long() -> None:
    assert parse_args(["--version"]).version is True


def test_version_short() -> None:
    assert parse_args(["-v"]).version is True


# === --mode ==================================================================


def test_mode_text() -> None:
    assert parse_args(["--mode", "text"]).mode == "text"


def test_mode_json() -> None:
    assert parse_args(["--mode", "json"]).mode == "json"


def test_mode_rpc() -> None:
    assert parse_args(["--mode", "rpc"]).mode == "rpc"


def test_mode_invalid_records_error_diagnostic() -> None:
    args = parse_args(["--mode", "bogus"])
    assert args.mode == "text"  # unchanged
    assert any(
        d["type"] == "error" and "Invalid --mode" in d["message"]
        for d in args.diagnostics
    )


def test_mode_missing_value_records_error() -> None:
    args = parse_args(["--mode"])
    assert any(d["type"] == "error" for d in args.diagnostics)


# === --print / -p — opportunistic positional eat (Pi parity, P-386) ========


def test_print_long_no_positional() -> None:
    args = parse_args(["--print"])
    assert args.print_mode is True
    assert args.messages == []


def test_print_short() -> None:
    assert parse_args(["-p"]).print_mode is True


def test_print_with_positional_eaten() -> None:
    args = parse_args(["--print", "hello"])
    assert args.print_mode is True
    assert args.messages == ["hello"]


def test_print_does_not_eat_flag() -> None:
    args = parse_args(["--print", "--offline"])
    assert args.print_mode is True
    assert args.offline is True
    assert args.messages == []


def test_print_does_not_eat_file_arg() -> None:
    args = parse_args(["--print", "@foo.txt"])
    assert args.print_mode is True
    assert args.file_args == ["foo.txt"]
    assert args.messages == []


# === --list-models — ambiguous optional value (Pi parity, P-386) ===========


def test_list_models_no_pattern() -> None:
    args = parse_args(["--list-models"])
    assert args.list_models is True


def test_list_models_with_pattern() -> None:
    args = parse_args(["--list-models", "gpt"])
    assert args.list_models == "gpt"


def test_list_models_does_not_eat_flag() -> None:
    args = parse_args(["--list-models", "--offline"])
    assert args.list_models is True
    assert args.offline is True


# === --continue / --resume / --no-session ====================================


def test_continue_long() -> None:
    assert parse_args(["--continue"]).continue_session is True


def test_continue_short() -> None:
    assert parse_args(["-c"]).continue_session is True


def test_resume_long() -> None:
    assert parse_args(["--resume"]).resume is True


def test_resume_short() -> None:
    assert parse_args(["-r"]).resume is True


def test_no_session() -> None:
    assert parse_args(["--no-session"]).no_session is True


def test_session_path() -> None:
    args = parse_args(["--session", "/path/to/session"])
    assert args.session == "/path/to/session"


def test_fork() -> None:
    assert parse_args(["--fork", "entry-id"]).fork == "entry-id"


def test_session_dir() -> None:
    assert parse_args(["--session-dir", "/sessions"]).session_dir == "/sessions"


# === Model flags =============================================================


def test_provider() -> None:
    assert parse_args(["--provider", "anthropic"]).provider == "anthropic"


def test_model() -> None:
    assert parse_args(["--model", "claude-sonnet-4"]).model == "claude-sonnet-4"


def test_models_csv() -> None:
    args = parse_args(["--models", "a, b ,c,"])
    assert args.models == ["a", "b", "c"]


def test_api_key() -> None:
    assert parse_args(["--api-key", "sk-xxx"]).api_key == "sk-xxx"


# === --thinking ==============================================================


def test_thinking_valid_levels() -> None:
    for level in VALID_THINKING_LEVELS:
        args = parse_args(["--thinking", level])
        assert args.thinking == level
        assert all(d["type"] != "warning" for d in args.diagnostics)


def test_thinking_invalid_emits_warning() -> None:
    args = parse_args(["--thinking", "bogus"])
    assert args.thinking is None
    assert any(
        d["type"] == "warning" and "--thinking" in d["message"]
        for d in args.diagnostics
    )


# === System prompt ===========================================================


def test_system_prompt() -> None:
    assert parse_args(["--system-prompt", "be brief"]).system_prompt == "be brief"


def test_append_system_prompt_repeatable() -> None:
    args = parse_args(
        ["--append-system-prompt", "one", "--append-system-prompt", "two"]
    )
    assert args.append_system_prompt == ["one", "two"]


# === Tool / extension flags ==================================================


def test_no_tools_long() -> None:
    assert parse_args(["--no-tools"]).no_tools is True


def test_no_tools_short() -> None:
    assert parse_args(["-nt"]).no_tools is True


def test_no_builtin_tools() -> None:
    assert parse_args(["-nbt"]).no_builtin_tools is True


def test_tools_csv() -> None:
    assert parse_args(["--tools", "a,b,c"]).tools == ["a", "b", "c"]


def test_tools_short() -> None:
    assert parse_args(["-t", "x,y"]).tools == ["x", "y"]


def test_extension_repeatable_long() -> None:
    args = parse_args(["--extension", "alpha", "--extension", "beta"])
    assert args.extensions == ["alpha", "beta"]


def test_extension_short() -> None:
    args = parse_args(["-e", "alpha"])
    assert args.extensions == ["alpha"]


def test_no_extensions_long() -> None:
    assert parse_args(["--no-extensions"]).no_extensions is True


def test_no_extensions_short() -> None:
    assert parse_args(["-ne"]).no_extensions is True


def test_skill_repeatable() -> None:
    args = parse_args(["--skill", "s1", "--skill", "s2"])
    assert args.skills == ["s1", "s2"]


def test_no_skills_long() -> None:
    assert parse_args(["--no-skills"]).no_skills is True


def test_no_skills_short() -> None:
    assert parse_args(["-ns"]).no_skills is True


def test_prompt_template_repeatable() -> None:
    args = parse_args(
        ["--prompt-template", "t1", "--prompt-template", "t2"]
    )
    assert args.prompt_templates == ["t1", "t2"]


def test_theme_repeatable() -> None:
    args = parse_args(["--theme", "dark", "--theme", "light"])
    assert args.themes == ["dark", "light"]


def test_no_context_files_long() -> None:
    assert parse_args(["--no-context-files"]).no_context_files is True


def test_no_context_files_short() -> None:
    assert parse_args(["-nc"]).no_context_files is True


# === Misc ====================================================================


def test_export() -> None:
    assert parse_args(["--export", "out.html"]).export == "out.html"


def test_offline() -> None:
    assert parse_args(["--offline"]).offline is True


# === Removed inert flags =====================================================
#
# --verbose, --no-themes and --no-prompt-templates were parsed into Args
# fields that NOTHING outside args.py ever read, while `--help` advertised
# them as working features. They were removed rather than left inert, so
# `--help` only lists flags that do something.


@pytest.mark.parametrize("flag", ["--verbose", "--no-themes", "--no-prompt-templates"])
def test_removed_inert_flags_have_no_args_field(flag: str) -> None:
    field_name = flag.lstrip("-").replace("-", "_")
    assert not hasattr(parse_args([]), field_name)


@pytest.mark.parametrize("flag", ["--verbose", "--no-themes", "--no-prompt-templates"])
def test_removed_inert_flags_are_absent_from_help(flag: str) -> None:
    buf = io.StringIO()
    print_help(buf)
    assert flag not in buf.getvalue()


@pytest.mark.parametrize(
    "flag", ["--verbose", "--no-themes", "--no-prompt-templates", "-np"]
)
def test_removed_flags_error_instead_of_being_swallowed(flag: str) -> None:
    """Deleting the parse arm is not enough on its own.

    An unrecognised ``--name`` falls into the unknown-EXTENSION-flag branch,
    which swallows the NEXT token as the flag's value — so
    ``aelix --verbose "my prompt"`` would record ``{"verbose": "my prompt"}``,
    leave ``messages`` empty, and run with no prompt at all. A removed flag
    must fail loudly, and must not eat the prompt on its way out.
    """
    args = parse_args([flag, "my prompt"])
    assert any(
        d["type"] == "error" and "was removed" in d["message"]
        for d in args.diagnostics
    ), args.diagnostics
    assert args.messages == ["my prompt"]  # the prompt survived
    assert args.unknown_flags == {}  # and was not eaten as a flag value


def test_removed_flag_with_inline_value_also_errors() -> None:
    """``--verbose=1`` must not slip past on the ``--key=value`` sub-case."""
    args = parse_args(["--verbose=1"])
    assert any(d["type"] == "error" for d in args.diagnostics)
    assert args.unknown_flags == {}


def test_genuine_unknown_extension_flag_still_swallows_its_value() -> None:
    """Guard the pi-parity behaviour the removed-flag branch sits in front of:
    a real unknown extension flag keeps consuming its value with NO
    diagnostic (contrast the unknown-SHORT-flag branch)."""
    args = parse_args(["--solo-ext", "my prompt"])
    assert args.unknown_flags == {"solo-ext": "my prompt"}
    assert args.messages == []
    assert [d for d in args.diagnostics if d["type"] == "error"] == []


# === @file fork ==============================================================


def test_file_arg_single() -> None:
    args = parse_args(["@foo.py"])
    assert args.file_args == ["foo.py"]


def test_file_arg_multiple() -> None:
    args = parse_args(["@a.txt", "@b.txt"])
    assert args.file_args == ["a.txt", "b.txt"]


def test_file_arg_with_path() -> None:
    args = parse_args(["@/abs/path.py", "@./rel.py"])
    assert args.file_args == ["/abs/path.py", "./rel.py"]


# === Plain positional → messages =============================================


def test_plain_positional_becomes_message() -> None:
    args = parse_args(["hello world"])
    assert args.messages == ["hello world"]


def test_multiple_positional_preserved_in_order() -> None:
    args = parse_args(["msg1", "msg2", "msg3"])
    assert args.messages == ["msg1", "msg2", "msg3"]


# === Unknown extension flag passthrough (Pi parity, P-386) ==================


def test_unknown_long_flag_with_value() -> None:
    args = parse_args(["--ext-flag", "value"])
    assert args.unknown_flags == {"ext-flag": "value"}


def test_unknown_long_flag_equals_form() -> None:
    args = parse_args(["--ext-flag=v"])
    assert args.unknown_flags == {"ext-flag": "v"}


def test_unknown_long_flag_boolean() -> None:
    args = parse_args(["--solo-ext"])
    assert args.unknown_flags == {"solo-ext": True}


def test_unknown_long_flag_does_not_eat_next_flag() -> None:
    args = parse_args(["--solo-ext", "--offline"])
    assert args.unknown_flags == {"solo-ext": True}
    assert args.offline is True


def test_unknown_short_flag_emits_diagnostic() -> None:
    args = parse_args(["-xyz"])
    assert any(
        d["type"] == "error" and "Unknown short flag" in d["message"]
        for d in args.diagnostics
    )


# === Diagnostic combos =======================================================


def test_diagnostic_collection_preserves_order() -> None:
    args = parse_args(["--mode", "bogus", "--thinking", "alsoBogus", "-xyz"])
    types = [d["type"] for d in args.diagnostics]
    # First: error from --mode, then warning from --thinking, then error
    # from unknown short flag.
    assert types == ["error", "warning", "error"]


def test_mixed_complex_invocation() -> None:
    argv = [
        "--mode",
        "json",
        "--offline",
        "--provider",
        "anthropic",
        "--model",
        "claude-x",
        "--extension",
        "ext1",
        "--extension",
        "ext2",
        "@file.txt",
        "hello",
        "@second.py",
        "world",
    ]
    args = parse_args(argv)
    assert args.mode == "json"
    assert args.offline is True
    assert args.provider == "anthropic"
    assert args.model == "claude-x"
    assert args.extensions == ["ext1", "ext2"]
    assert args.file_args == ["file.txt", "second.py"]
    assert args.messages == ["hello", "world"]


# === print_help =============================================================


def test_print_help_emits_to_stream() -> None:
    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    assert "aelix" in text.lower()
    assert "--help" in text
    assert "--version" in text
    assert "--mode" in text


# === Pi parity regressions (P-396 / P-397 / P-398) ==========================


def test_print_with_triple_dash_message() -> None:
    """P-396 — Pi ``args.ts:123-129`` ``---`` escape passes through."""
    parsed = parse_args(["--print", "---foo"])
    assert parsed.print_mode is True
    assert parsed.messages == ["---foo"]


def test_list_models_does_not_eat_file_arg() -> None:
    """P-397 — Pi ``args.ts:154-160`` excludes ``@`` from optional value."""
    parsed = parse_args(["--list-models", "@foo.py"])
    assert parsed.list_models is True
    assert parsed.file_args == ["foo.py"]


def test_unknown_flag_does_not_eat_file_arg() -> None:
    """P-398 — Pi ``args.ts:167-180`` excludes ``@`` from passthrough."""
    parsed = parse_args(["--my-ext", "@input.txt", "msg"])
    assert parsed.unknown_flags == {"my-ext": True}
    assert parsed.file_args == ["input.txt"]
    assert parsed.messages == ["msg"]


# === Agent profiles (aelix-original, ADR-0196) ==============================


def test_agent_flags_parse() -> None:
    """``--agent`` / ``--agent-file`` are first-class, not unknown-flag
    passthrough (which is where every unrecognized ``--foo`` lands)."""
    parsed = parse_args(["--agent", "scout"])
    assert parsed.agent == "scout"
    assert parsed.agent_file is None
    assert parsed.unknown_flags == {}

    parsed = parse_args(["--agent-file", "./profiles/scout.md"])
    assert parsed.agent_file == "./profiles/scout.md"
    assert parsed.agent is None
    assert parsed.unknown_flags == {}

    # Mutual exclusion is enforced in ``cli/entry.py`` (it needs stderr and an
    # exit code); the parser records BOTH so entry can diagnose it.
    parsed = parse_args(["--agent", "scout", "--agent-file", "x.md"])
    assert (parsed.agent, parsed.agent_file) == ("scout", "x.md")


def test_agent_flag_missing_value_is_inert() -> None:
    """Trailing ``--agent`` with no value follows the house lookahead
    contract: no crash, no consumption, field left at its default."""
    parsed = parse_args(["--agent"])
    assert parsed.agent is None
    assert parsed.unknown_flags == {}


def test_prompt_file_flags_parse() -> None:
    parsed = parse_args(["--system-prompt-file", "/tmp/base.md"])
    assert parsed.system_prompt_file == "/tmp/base.md"
    # The parser does NOT read the file — normalization into the string twin
    # happens in ``entry._apply_prompt_files``.
    assert parsed.system_prompt is None

    parsed = parse_args(
        [
            "--append-system-prompt-file",
            "/tmp/a.md",
            "--append-system-prompt-file",
            "/tmp/b.md",
        ]
    )
    assert parsed.append_system_prompt_files == ["/tmp/a.md", "/tmp/b.md"]
    assert parsed.append_system_prompt == []


def test_prompt_file_flags_do_not_shadow_their_string_twins() -> None:
    """The linear loop matches on equality, so ``--system-prompt-file`` can
    never be swallowed by the ``--system-prompt`` branch (or vice versa)."""
    parsed = parse_args(
        ["--system-prompt", "literal", "--system-prompt-file", "/tmp/x.md"]
    )
    assert parsed.system_prompt == "literal"
    assert parsed.system_prompt_file == "/tmp/x.md"
    assert parsed.messages == []


# === Args.provided — explicit-CLI provenance (ADR-0196) =====================
#
# Every row is a field an agent profile may overlay (plus the profile flags
# themselves). A MISSING row means the profile would silently beat an
# explicit CLI flag, so the table is exhaustive by construction.

_PROVENANCE_CASES: list[tuple[list[str], str]] = [
    (["--provider", "anthropic"], "provider"),
    (["--model", "claude-x"], "model"),
    (["--thinking", "high"], "thinking"),
    (["--system-prompt", "be brief"], "system_prompt"),
    (["--append-system-prompt", "extra"], "append_system_prompt"),
    (["--system-prompt-file", "/tmp/x.md"], "system_prompt_file"),
    (["--append-system-prompt-file", "/tmp/x.md"], "append_system_prompt_files"),
    (["--agent", "scout"], "agent"),
    (["--agent-file", "/tmp/scout.md"], "agent_file"),
    (["--no-tools"], "no_tools"),
    (["-nt"], "no_tools"),
    (["--no-builtin-tools"], "no_builtin_tools"),
    (["-nbt"], "no_builtin_tools"),
    (["--tools", "read,grep"], "tools"),
    (["-t", "read"], "tools"),
    (["--extension", "/tmp/ext.py"], "extensions"),
    (["-e", "/tmp/ext.py"], "extensions"),
    (["--no-extensions"], "no_extensions"),
    (["-ne"], "no_extensions"),
    (["--skill", "/tmp/skills/recon"], "skills"),
    (["--no-skills"], "no_skills"),
    (["-ns"], "no_skills"),
    (["--no-context-files"], "no_context_files"),
    (["-nc"], "no_context_files"),
]


@pytest.mark.parametrize(("argv", "field_name"), _PROVENANCE_CASES)
def test_provided_records_explicit_flags(argv: list[str], field_name: str) -> None:
    parsed = parse_args(argv)
    assert field_name in parsed.provided
    # The recorded name must be a real field — a typo would be a silent
    # no-op in the overlay's ``name not in provided`` check.
    assert hasattr(parsed, field_name)


def test_provided_is_empty_when_nothing_supplied() -> None:
    assert parse_args([]).provided == set()
    # Flags no profile can overlay stay out of the provenance set.
    assert parse_args(["--offline", "--print", "hi"]).provided == set()


def test_provided_accumulates_across_flags() -> None:
    parsed = parse_args(["--model", "m", "--provider", "p", "--no-tools"])
    assert parsed.provided == {"model", "provider", "no_tools"}


def test_provided_records_empty_tools_csv() -> None:
    """The whole reason ``provided`` exists: ``--tools ''`` parses to ``[]``,
    which is byte-identical to the "never supplied" default."""
    parsed = parse_args(["--tools", ""])
    assert parsed.tools == []
    assert parse_args([]).tools == []
    assert "tools" in parsed.provided
    assert "tools" not in parse_args([]).provided


def test_invalid_thinking_not_recorded_as_provided() -> None:
    """``--thinking bogus`` warns and drops (the level never lands on
    ``Args``), so recording it would let a typo veto a profile's valid
    ``thinking:`` and leave the session at ``off``."""
    parsed = parse_args(["--thinking", "bogus"])
    assert parsed.thinking is None
    assert "thinking" not in parsed.provided
    assert parse_args(["--thinking", "high"]).provided == {"thinking"}


def test_provided_survives_unknown_flag_passthrough() -> None:
    """An extension flag between two known flags must not break provenance."""
    parsed = parse_args(["--model", "m", "--ext-flag", "value", "--no-skills"])
    assert parsed.unknown_flags == {"ext-flag": "value"}
    assert parsed.provided == {"model", "no_skills"}


# === print_help — new groups + the two corrected lines ======================


def test_print_help_lists_agent_and_prompt_file_flags() -> None:
    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    assert "Agent profiles:" in text
    assert "--agent <name>" in text
    assert "--agent-file <path>" in text
    assert "--system-prompt-file <path>" in text
    assert "--append-system-prompt-file <path>" in text


def test_print_help_lists_every_extension_subcommand() -> None:
    """`extension verify` shipped in `aelix extension --help` but was missing
    from the top-level `Subcommands:` block, so the one command that explains
    why a manifest did not bind was undiscoverable from `aelix --help`."""
    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    for sub in ("install", "list", "verify", "discover", "update", "remove",
                "keygen", "sign", "trust"):
        assert f"extension {sub}" in text, sub


def test_print_help_offline_line_does_not_overclaim() -> None:
    """`--offline` reaches exactly three sites (the rg/fd download, the
    catalog fetch, index-less pypi installs) and does NOT make provider/LLM
    calls offline. The help line used to say "startup network operations",
    which reads as air-gap mode."""
    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    assert "--offline" in text
    assert "startup network operations" not in text


def test_print_help_says_path_for_skill_and_extension() -> None:
    """ADR-0196 — both lines said ``<name>`` while the implementation
    resolved a PATH (``entry.py:939-980`` for ``--skill``,
    ``extensions/loader.py:797-859`` for ``-e``). Aelix has no name resolver
    for either, so the old wording sent users to a silently-ignored flag."""
    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    assert "--skill <path>" in text
    assert "--extension, -e <path>" in text
    assert "--skill <name>" not in text
    assert "--extension, -e <name>" not in text


def test_print_help_lists_extension_discover_subcommands() -> None:
    """Issue #116 narrative 3 — ``extension discover`` and its ``install``
    subcommand were listed by ``aelix extension --help``
    (``cli/extension_install.py`` ``_USAGE``) but MISSING from the top-level
    ``--help`` Subcommands block, so the catalog-browsing entry point was
    invisible to anyone reading the main help."""
    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    subcommands = text.split("Subcommands:")[1].split("File arguments:")[0]
    assert "extension discover [<query>]" in subcommands
    assert "extension discover install" in subcommands
    # The flags the discover subcommand actually accepts are advertised too.
    assert "--no-default-catalog" in subcommands
