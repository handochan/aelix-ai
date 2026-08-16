"""ADR-0196 — ``--system-prompt-file`` / ``--append-system-prompt-file``.

The two file-taking twins of ``--system-prompt`` / ``--append-system-prompt``
exist because an agent profile's BODY is the system prompt, and a body is
routinely far past what is safe to put in an argv (``ARG_MAX``, plus it leaks in
``ps``). ``agents.resolver.profile_to_flags`` therefore emits the file forms,
which makes these flags load-bearing rather than convenience sugar.

Two behaviours are pinned harder than the rest because they are placement bugs
waiting to happen, not feature checks:

* ``_apply_prompt_files`` runs at ``cli/entry.py:1842`` — AFTER the
  ``--list-models`` short-circuit and the ``--export`` exit, BEFORE mode
  resolution. Moving it up to "right after arg validation" (the obvious spot)
  would make an unreadable file hard-fail two do-a-thing-and-exit actions that
  never consult a system prompt at all. ``test_prompt_files_do_not_break_
  list_models_or_export`` is that pin.
* ``--system-prompt`` (a literal string) WINS over ``--system-prompt-file``, and
  the decision is made on ``parsed.provided`` — "did the USER type it" — not on
  ``is None``, because the profile overlay downstream also writes
  ``parsed.system_prompt``.

Everything here is offline: no registry, no auth, no session storage on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.cli.entry import (
    _apply_prompt_files,
    _async_main,
    _build_harness_options,
)


def _normalized(argv: list[str]):
    """``parse_args`` + the ``_apply_prompt_files`` normalization pass.

    Returns ``(parsed, error)`` so a test can assert on either half without
    re-deriving the call the entry point makes at ``cli/entry.py:1842``.
    """

    parsed = parse_args(argv)
    return parsed, _apply_prompt_files(parsed)


# === Normalization into the string twins =====================================


def test_system_prompt_file_replaces(tmp_path: Path) -> None:
    """``--system-prompt-file`` lands in ``parsed.system_prompt`` verbatim."""

    prompt = tmp_path / "identity.md"
    prompt.write_text("You are a recon agent.\n", encoding="utf-8")

    parsed, error = _normalized(["--system-prompt-file", str(prompt)])
    assert error is None
    assert parsed.system_prompt == "You are a recon agent.\n"
    # The raw flag value is kept (the audit trail for /agents show); the string
    # twin is what every downstream consumer reads.
    assert parsed.system_prompt_file == str(prompt)


async def test_system_prompt_file_reaches_harness_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the normalized string is the harness's BASE prompt.

    Without this arm the test above would pass on a normalization that nothing
    consumes — the exact failure mode ``--thinking`` shipped in for months
    (one writer, zero readers).
    """

    prompt = tmp_path / "identity.md"
    prompt.write_text("REPLACED_BASE", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    parsed, error = _normalized(["--system-prompt-file", str(prompt)])
    assert error is None
    options = await _build_harness_options(parsed, Session(MemorySessionStorage()))
    assert options.system_prompt == "REPLACED_BASE"


def test_append_system_prompt_file_appends_after_string_appends(
    tmp_path: Path,
) -> None:
    """File appends land AFTER string appends, preserving flag-channel order.

    ``--append-system-prompt`` is the accumulator the profile overlay later
    ``insert(0, ...)``s into, so the relative order of the two append channels is
    observable in the final prompt and has to be fixed, not incidental.
    """

    chunk = tmp_path / "extra.md"
    chunk.write_text("FROM_FILE", encoding="utf-8")

    parsed, error = _normalized(
        [
            "--append-system-prompt",
            "FROM_STRING",
            "--append-system-prompt-file",
            str(chunk),
        ]
    )
    assert error is None
    assert parsed.append_system_prompt == ["FROM_STRING", "FROM_FILE"]


def test_append_system_prompt_files_are_repeatable(tmp_path: Path) -> None:
    """``--append-system-prompt-file`` repeats in CLI order (``args.py``)."""

    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")

    parsed, error = _normalized(
        [
            "--append-system-prompt-file",
            str(first),
            "--append-system-prompt-file",
            str(second),
        ]
    )
    assert error is None
    assert parsed.append_system_prompt == ["A", "B"]


def test_explicit_string_flag_beats_file(tmp_path: Path) -> None:
    """``--system-prompt`` (literal) wins over ``--system-prompt-file``.

    Asserted in BOTH argv orders: the rule is provenance-based
    (``"system_prompt" in parsed.provided``), not last-flag-wins, so a reordering
    must not change the outcome.
    """

    prompt = tmp_path / "identity.md"
    prompt.write_text("FROM_FILE", encoding="utf-8")

    parsed, error = _normalized(
        ["--system-prompt", "LITERAL", "--system-prompt-file", str(prompt)]
    )
    assert error is None
    assert parsed.system_prompt == "LITERAL"

    reversed_parsed, reversed_error = _normalized(
        ["--system-prompt-file", str(prompt), "--system-prompt", "LITERAL"]
    )
    assert reversed_error is None
    assert reversed_parsed.system_prompt == "LITERAL"


def test_system_prompt_file_beats_profile_replace(tmp_path: Path) -> None:
    """The FILE twin carries the same provenance as the literal twin.

    ``_apply_prompt_files`` wrote ``parsed.system_prompt`` without recording
    ``"system_prompt"`` in ``parsed.provided`` (only ``"system_prompt_file"`` was
    there, set by the parser), and ``apply_profile_to_args`` gates on the FIELD
    name — so ``aelix --system-prompt-file base.md --agent scout`` silently
    discarded ``base.md`` while ``--system-prompt`` correctly won. The file flag
    is the one a LONG prompt reaches for, i.e. the likelier of the two, so the
    asymmetry broke exactly the case the flag was added for.
    """

    from aelix_coding_agent.agents.profile import parse_profile
    from aelix_coding_agent.agents.resolver import apply_profile_to_args

    base = tmp_path / "base.md"
    base.write_text("USER-SUPPLIED SYSTEM PROMPT", encoding="utf-8")
    profile = parse_profile(
        "---\nname: scout\ndescription: recon\nsystem_prompt: replace\n---\n"
        "PROFILE BODY\n",
        file_path=str(tmp_path / "scout.md"),
        scope="user",
    ).profile
    assert profile is not None

    parsed, error = _normalized(["--system-prompt-file", str(base)])
    assert error is None
    application = apply_profile_to_args(parsed, profile, provided=parsed.provided)

    assert parsed.system_prompt == "USER-SUPPLIED SYSTEM PROMPT"
    assert "system_prompt" in application.skipped


def test_prompt_file_frontmatter_is_metadata_not_prompt(tmp_path: Path) -> None:
    """A prompt file that IS a profile contributes its body, never its YAML.

    ``resolver.profile_to_flags`` emits ``--append-system-prompt-file
    <profile>.md`` and ``/agents show`` shell-quotes that exact command for the
    user to copy; reading the file whole shipped the profile's own frontmatter
    into the model's system prompt, so the two channels a profile reaches the
    runtime through disagreed on the prompt's CONTENT. Pinned alongside the
    control: an ordinary prompt that merely OPENS with ``---`` is untouched.
    """

    profile_file = tmp_path / "scout.md"
    profile_file.write_text(
        "---\nname: scout\ndescription: recon\n---\nYou are SCOUT.\n",
        encoding="utf-8",
    )
    parsed, error = _normalized(["--append-system-prompt-file", str(profile_file)])
    assert error is None
    assert parsed.append_system_prompt == ["You are SCOUT."]

    # Control 1 — no frontmatter at all: byte-for-byte.
    plain = tmp_path / "plain.md"
    plain.write_text("PLAIN PROMPT\n", encoding="utf-8")
    plain_parsed, plain_error = _normalized(["--system-prompt-file", str(plain)])
    assert plain_error is None
    assert plain_parsed.system_prompt == "PLAIN PROMPT\n"

    # Control 2 — an UNCLOSED block is not frontmatter (``parse_frontmatter``
    # returns the whole file), so nothing is silently eaten.
    unclosed = tmp_path / "unclosed.md"
    unclosed.write_text("---\nnot: closed\nstill prompt\n", encoding="utf-8")
    unclosed_parsed, unclosed_error = _normalized(
        ["--system-prompt-file", str(unclosed)]
    )
    assert unclosed_error is None
    assert unclosed_parsed.system_prompt == "---\nnot: closed\nstill prompt\n"


def test_read_failure_leaves_parsed_untouched(tmp_path: Path) -> None:
    """A failure on the SECOND file rolls nothing forward.

    ``_apply_prompt_files`` reads everything before it mutates anything, so a
    half-applied prompt (base swapped, appends missing) is unreachable.
    """

    good = tmp_path / "good.md"
    good.write_text("GOOD", encoding="utf-8")
    missing = tmp_path / "nope.md"

    parsed, error = _normalized(
        [
            "--system-prompt-file",
            str(good),
            "--append-system-prompt-file",
            str(missing),
        ]
    )
    assert error is not None
    assert parsed.system_prompt is None
    assert parsed.append_system_prompt == []


# === Refusals (each is an exit-1 at the entry point) =========================


async def test_unreadable_file_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist.md"
    code = await _async_main(["--print", "--system-prompt-file", str(missing)])
    assert code == 1
    err = capsys.readouterr().err
    assert "--system-prompt-file" in err
    assert str(missing) in err


async def test_directory_path_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory is refused by the ``is_file()`` gate.

    ``IsADirectoryError`` is an ``OSError`` subclass, so a bare ``read_text``
    would also fail — but with a platform-dependent message and, on the
    ``/dev/stdin``-shaped variant of the same mistake, by BLOCKING forever.
    The explicit not-a-regular-file refusal is what this pins.
    """

    code = await _async_main(["--print", "--system-prompt-file", str(tmp_path)])
    assert code == 1
    err = capsys.readouterr().err
    assert "not a regular file" in err


async def test_oversized_prompt_file_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """>1 MiB is refused HERE, naming the path — not as a provider-side 400."""

    huge = tmp_path / "huge.md"
    huge.write_text("x" * ((1 << 20) + 1), encoding="utf-8")

    code = await _async_main(["--print", "--system-prompt-file", str(huge)])
    assert code == 1
    err = capsys.readouterr().err
    assert str(huge) in err
    assert "limit" in err


async def test_oversized_append_file_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ceiling applies to the append channel too (same reader)."""

    huge = tmp_path / "huge-append.md"
    huge.write_text("x" * ((1 << 20) + 1), encoding="utf-8")

    code = await _async_main(
        ["--print", "--append-system-prompt-file", str(huge)]
    )
    assert code == 1
    assert "--append-system-prompt-file" in capsys.readouterr().err


# === The raison d'être: a body far past ARG_MAX ==============================


async def test_large_profile_body_survives_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """256 KiB — comfortably past ``ARG_MAX`` — round-trips into the harness.

    This is the whole reason the file flags exist: ``profile_to_flags`` emits
    ``--system-prompt-file`` precisely because the same content on
    ``--system-prompt`` would be un-spawnable (and would leak in ``ps``).
    Under the ceiling, so it must SUCCEED, not be refused.
    """

    body = "PROFILE_BODY\n" + ("A" * (256 * 1024))
    prompt = tmp_path / "big.md"
    prompt.write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    parsed, error = _normalized(["--system-prompt-file", str(prompt)])
    assert error is None
    options = await _build_harness_options(parsed, Session(MemorySessionStorage()))
    assert options.system_prompt == body
    assert len(options.system_prompt) > 256 * 1024


# === Call-site placement (entry.py:1842) =====================================


async def test_prompt_files_do_not_break_list_models_or_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The normalization must NOT precede the two do-a-thing-and-exit actions.

    Both arms use an unreadable ``--system-prompt-file``. Neither action ever
    consults a system prompt, so neither may be hard-failed by it:

    * ``--list-models`` still exits 0;
    * ``--export`` still fails for its OWN reason (a bogus source), and the
      prompt-file flag is nowhere in the diagnostic.

    Hoisting ``_apply_prompt_files`` above ``_validate_resume_flag`` — the
    nearest "arg validation" boundary, and the placement the first draft called
    for — breaks both arms at once.
    """

    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    missing = tmp_path / "does-not-exist.md"

    code = await _async_main(
        ["--list-models", "--system-prompt-file", str(missing)]
    )
    assert code == 0
    assert "--system-prompt-file" not in capsys.readouterr().err

    bogus_session = tmp_path / "no-such-session.jsonl"
    export_code = await _async_main(
        ["--export", str(bogus_session), "--system-prompt-file", str(missing)]
    )
    err = capsys.readouterr().err
    assert export_code == 1
    # The failure is the EXPORT's, not the prompt file's.
    assert "--system-prompt-file" not in err
