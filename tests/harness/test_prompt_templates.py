"""Pi parity: ``packages/agent/src/harness/prompt-templates.ts``.

Sprint 6h₁ §G unit coverage. Pi reference: SHA ``734e08e``.
"""

from __future__ import annotations

from pathlib import Path

from aelix_agent_core.harness.prompt_templates import (
    PromptTemplate,
    PromptTemplateDiagnostic,
    format_prompt_template_invocation,
    load_prompt_templates,
    parse_command_args,
    substitute_args,
)

# === load_prompt_templates ====================================================


def test_load_from_directory_loads_md_children(tmp_path: Path) -> None:
    """Pi parity: ``loadTemplatesFromDir`` — direct .md children, sorted."""

    (tmp_path / "b.md").write_text("body b")
    (tmp_path / "a.md").write_text("body a")
    (tmp_path / "ignore.txt").write_text("not markdown")
    result = load_prompt_templates([tmp_path])
    assert [t.name for t in result.templates] == ["a", "b"]
    assert result.diagnostics == []


def test_load_from_directory_is_non_recursive(tmp_path: Path) -> None:
    """Pi parity: only DIRECT .md children — no recursion."""

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text("nested")
    (tmp_path / "top.md").write_text("top")
    result = load_prompt_templates([tmp_path])
    names = [t.name for t in result.templates]
    assert "top" in names
    assert "nested" not in names


def test_load_explicit_md_file(tmp_path: Path) -> None:
    """Pi parity: file input — load if .md, skip otherwise."""

    md = tmp_path / "explicit.md"
    md.write_text("hello")
    txt = tmp_path / "skip.txt"
    txt.write_text("ignored")
    result = load_prompt_templates([md, txt])
    assert [t.name for t in result.templates] == ["explicit"]
    assert result.diagnostics == []


def test_missing_path_is_silent(tmp_path: Path) -> None:
    """Pi parity: ``not_found`` → silent skip (no diagnostic)."""

    result = load_prompt_templates([tmp_path / "absent"])
    assert result.templates == []
    assert result.diagnostics == []


# === Frontmatter parsing ======================================================


def test_frontmatter_description_wins_over_first_line(tmp_path: Path) -> None:
    """Pi parity: ``frontmatter.description`` short-circuits the body
    fallback."""

    file = tmp_path / "t.md"
    file.write_text("---\ndescription: From frontmatter\n---\nBody first line")
    result = load_prompt_templates([file])
    assert result.templates[0].description == "From frontmatter"
    assert result.templates[0].content == "Body first line"


def test_no_frontmatter_uses_first_body_line(tmp_path: Path) -> None:
    """Pi parity: no frontmatter → first non-empty body line, truncated to 60."""

    file = tmp_path / "t.md"
    short = "First short line"
    file.write_text(f"{short}\n\nMore content")
    result = load_prompt_templates([file])
    assert result.templates[0].description == short


def test_description_truncated_to_60_with_ellipsis(tmp_path: Path) -> None:
    """Pi parity: first body line > 60 chars → truncate, append ``...``."""

    long_line = "x" * 80
    file = tmp_path / "t.md"
    file.write_text(long_line)
    result = load_prompt_templates([file])
    desc = result.templates[0].description
    assert len(desc) == 63  # 60 + "..."
    assert desc.endswith("...")
    assert desc[:60] == "x" * 60


def test_no_frontmatter_no_body_yields_empty_description(tmp_path: Path) -> None:
    """Pi parity: no frontmatter and empty body → empty description."""

    file = tmp_path / "t.md"
    file.write_text("")
    result = load_prompt_templates([file])
    assert result.templates[0].description == ""


def test_parse_failure_emits_diagnostic(tmp_path: Path) -> None:
    """Pi parity: malformed YAML → ``parse_failed`` diagnostic, skill skipped."""

    file = tmp_path / "broken.md"
    file.write_text("---\n: this is :: not valid yaml: [oops\n---\nbody")
    result = load_prompt_templates([file])
    assert result.templates == []
    codes = [d.code for d in result.diagnostics]
    assert "parse_failed" in codes
    assert all(isinstance(d, PromptTemplateDiagnostic) for d in result.diagnostics)


def test_parse_failure_diagnostic_message_includes_yaml_error(tmp_path: Path) -> None:
    """Sprint 6h₁ W6 (P-233): the diagnostic message surfaces the YAML
    error so callers can act on it instead of guessing what went wrong.
    """

    file = tmp_path / "broken.md"
    file.write_text("---\n: this is :: not valid yaml: [oops\n---\nbody")
    result = load_prompt_templates([file])
    failures = [d for d in result.diagnostics if d.code == "parse_failed"]
    assert failures, "expected a parse_failed diagnostic"
    # Generic header is preserved + YAML's diagnostic appears after it.
    assert "failed to parse YAML frontmatter" in failures[0].message
    assert failures[0].message != "failed to parse YAML frontmatter"
    # The YAML library surfaces a recognisable token in its error.
    assert ":" in failures[0].message


# === Name derivation ==========================================================


def test_name_is_filename_stem(tmp_path: Path) -> None:
    """Pi parity: name = filename without ``.md`` extension."""

    file = tmp_path / "git-commit.md"
    file.write_text("content")
    result = load_prompt_templates([file])
    assert result.templates[0].name == "git-commit"


def test_name_strip_is_case_insensitive(tmp_path: Path) -> None:
    """Sprint 6h₁ W6 (P-234): ``.MD`` / ``.Md`` / ``.mD`` strip the same as ``.md``.

    The prior implementation only matched the literal ``.md`` and ``.MD``
    suffixes; ``.Md`` / ``.mD`` would keep the suffix in the template name.
    The fix uses ``name.lower().endswith(".md")`` so every case-variant
    of the extension strips correctly.

    Exercised via the internal ``_load_template_from_file`` helper to
    isolate the strip from the directory-iteration path (which is itself
    Pi-byte-parity-case-sensitive on the entry filter — out of scope here).
    """

    from aelix_agent_core.harness.prompt_templates import (
        _load_template_from_file,
    )

    for suffix in (".md", ".MD", ".Md", ".mD"):
        file = tmp_path / f"variant{suffix}"
        file.write_text("content")
        templates: list[PromptTemplate] = []
        diagnostics: list[PromptTemplateDiagnostic] = []
        _load_template_from_file(file, templates, diagnostics)
        assert templates, f"suffix {suffix!r}: helper failed to load template"
        assert templates[0].name == "variant", (
            f"suffix {suffix!r}: name {templates[0].name!r} did not strip"
        )
        file.unlink()


# === parse_command_args =======================================================


def test_parse_args_plain_whitespace() -> None:
    assert parse_command_args("a b c") == ["a", "b", "c"]


def test_parse_args_tab_separator() -> None:
    assert parse_command_args("a\tb\tc") == ["a", "b", "c"]


def test_parse_args_double_quotes_preserve_spaces() -> None:
    assert parse_command_args('a "b c" d') == ["a", "b c", "d"]


def test_parse_args_single_quotes_preserve_spaces() -> None:
    assert parse_command_args("a 'b c' d") == ["a", "b c", "d"]


def test_parse_args_empty_string() -> None:
    assert parse_command_args("") == []


def test_parse_args_trailing_whitespace() -> None:
    assert parse_command_args("a   ") == ["a"]


# === substitute_args ==========================================================


def test_substitute_positional() -> None:
    """Pi parity: ``$1`` is 1-indexed."""

    assert substitute_args("$1 and $2", ["x", "y"]) == "x and y"


def test_substitute_positional_missing_arg_yields_empty() -> None:
    """Pi parity: out-of-range positional → empty string."""

    assert substitute_args("$1 and $3", ["x", "y"]) == "x and "


def test_substitute_arguments_alias() -> None:
    """Pi parity: ``$ARGUMENTS`` ← all args joined with spaces."""

    assert substitute_args("got: $ARGUMENTS", ["a", "b", "c"]) == "got: a b c"


def test_substitute_at_alias() -> None:
    """Pi parity: ``$@`` ← all args joined with spaces."""

    assert substitute_args("got: $@", ["a", "b", "c"]) == "got: a b c"


def test_substitute_range_from_index() -> None:
    """Pi parity: ``${@:N}`` ← args[N-1:] joined."""

    assert substitute_args("rest=${@:2}", ["a", "b", "c", "d"]) == "rest=b c d"


def test_substitute_range_with_length() -> None:
    """Pi parity: ``${@:N:L}`` ← args[N-1 : N-1+L] joined."""

    assert substitute_args("slice=${@:2:2}", ["a", "b", "c", "d"]) == "slice=b c"


def test_substitute_range_zero_index_clamps_to_zero() -> None:
    """Pi parity: ``start < 0 → 0`` after the -1 adjustment."""

    # ${@:0} → start = 0-1 = -1 → clamp to 0 → full list.
    assert substitute_args("all=${@:0}", ["a", "b"]) == "all=a b"


# === format_prompt_template_invocation =======================================


def test_format_invocation_without_prefix() -> None:
    template = PromptTemplate(
        name="cmd", description="run", content="hi $1"
    )
    assert format_prompt_template_invocation(template, ["world"]) == "hi world"


def test_format_invocation_with_prefix() -> None:
    template = PromptTemplate(name="cmd", description="run", content="body")
    assert (
        format_prompt_template_invocation(template, [], prefix="P> ") == "P> body"
    )


def test_format_invocation_empty_args_default() -> None:
    """Pi parity: ``args = []`` default."""

    template = PromptTemplate(
        name="cmd", description="run", content="literal $1"
    )
    # No args → $1 expands to "".
    assert format_prompt_template_invocation(template) == "literal "
