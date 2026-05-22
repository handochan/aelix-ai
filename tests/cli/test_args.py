"""Sprint 6h₆ (Phase 5a-i, ADR-0089) — ``cli/args.py`` tests.

Covers every flag in the Pi 30+-flag inventory, the three Pi-specific
parser features that motivated rejecting argparse (P-386), and
diagnostic combos.
"""

from __future__ import annotations

import io

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
    args = parse_args(["--print", "--verbose"])
    assert args.print_mode is True
    assert args.verbose is True
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
    args = parse_args(["--list-models", "--verbose"])
    assert args.list_models is True
    assert args.verbose is True


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


def test_no_prompt_templates_short() -> None:
    assert parse_args(["-np"]).no_prompt_templates is True


def test_theme_repeatable() -> None:
    args = parse_args(["--theme", "dark", "--theme", "light"])
    assert args.themes == ["dark", "light"]


def test_no_themes() -> None:
    assert parse_args(["--no-themes"]).no_themes is True


def test_no_context_files_long() -> None:
    assert parse_args(["--no-context-files"]).no_context_files is True


def test_no_context_files_short() -> None:
    assert parse_args(["-nc"]).no_context_files is True


# === Misc ====================================================================


def test_export() -> None:
    assert parse_args(["--export", "out.html"]).export == "out.html"


def test_verbose() -> None:
    assert parse_args(["--verbose"]).verbose is True


def test_offline() -> None:
    assert parse_args(["--offline"]).offline is True


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
    args = parse_args(["--solo-ext", "--verbose"])
    assert args.unknown_flags == {"solo-ext": True}
    assert args.verbose is True


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
        "--verbose",
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
    assert args.verbose is True
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
