"""Issue #287: discovery, precedence, trust, rebuilds and displayed provenance."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest
from aelix_agent_core.harness import AgentHarness
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.agents.profile import parse_profile
from aelix_coding_agent.agents.resolver import apply_profile_to_args, profile_to_flags
from aelix_coding_agent.cli import system_prompt_files
from aelix_coding_agent.cli.args import Args, parse_args
from aelix_coding_agent.cli.entry import (
    _apply_prompt_files,
    _build_harness_options,
    _resolve_append_chunks,
    _resolve_system_prompt,
    build_system_prompt,
)
from aelix_coding_agent.cli.project_trust import (
    ProjectTrustStore,
    has_trust_requiring_project_resources,
    resolve_project_trusted,
)


@pytest.fixture(autouse=True)
def isolated_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agent_dir = tmp_path / "global"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.chdir(tmp_path)
    return agent_dir


def _project_file(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / ".aelix" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("explicit", [None, "CLI"])
def test_system_prompt_precedence(
    tmp_path: Path, isolated_agent_dir: Path, trusted: bool, explicit: str | None
) -> None:
    global_file = isolated_agent_dir / "SYSTEM.md"
    global_file.write_text("GLOBAL")
    project_file = _project_file(tmp_path, "SYSTEM.md", "PROJECT")
    args = Args(system_prompt=explicit)
    result = _resolve_system_prompt(args, str(tmp_path), tools=[], project_trusted=trusted)
    assert result == (explicit or ("PROJECT" if trusted else "GLOBAL"))
    assert args.system_prompt_source_path == (
        None if explicit else str(project_file if trusted else global_file)
    )


@pytest.mark.parametrize("mode", ["replace", "append"])
@pytest.mark.parametrize("cli", [False, True])
def test_profiles_compose_with_discovery(
    tmp_path: Path, isolated_agent_dir: Path, mode: str, cli: bool
) -> None:
    (isolated_agent_dir / "SYSTEM.md").write_text("GLOBAL")
    _project_file(tmp_path, "SYSTEM.md", "PROJECT")
    _project_file(tmp_path, "APPEND_SYSTEM.md", "FILE_APPEND")
    profile_file = tmp_path / "profile.md"
    profile = parse_profile(
        f"---\nname: probe\ndescription: probe\nsystem_prompt: {mode}\n---\nPROFILE",
        file_path=str(profile_file),
        scope="user",
    ).profile
    assert profile is not None
    args = parse_args(["--system-prompt", "CLI"] if cli else [])
    apply_profile_to_args(args, profile, provided=args.provided)
    base = _resolve_system_prompt(args, str(tmp_path), tools=[], project_trusted=True)
    assert base == ("CLI" if cli else ("PROFILE" if mode == "replace" else "PROJECT"))
    append = _resolve_append_chunks(args, str(tmp_path), project_trusted=True)
    assert append == (["PROFILE", "FILE_APPEND"] if mode == "append" else ["FILE_APPEND"])


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
async def test_prompt_only_project_requires_trust(tmp_path: Path, name: str) -> None:
    _project_file(tmp_path, name, "PROJECT")
    assert has_trust_requiring_project_resources(tmp_path)
    assert not await resolve_project_trusted(
        tmp_path, override=None, has_ui=False, store=ProjectTrustStore(tmp_path / "store")
    )


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_untrusted_project_is_never_read(
    tmp_path: Path, isolated_agent_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _project_file(tmp_path, name, "PROJECT")
    (isolated_agent_dir / name).write_text("GLOBAL")
    original = os.open

    def checked_read(path, *args, **kwargs):
        assert Path(path).parent != tmp_path / ".aelix"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_read)
    result = system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=False
    )
    assert result == ("GLOBAL", str(isolated_agent_dir / name))


@pytest.mark.parametrize("content", ["", " \n\t", "\ufeff"])
def test_empty_project_file_restores_default_instead_of_global(
    tmp_path: Path, isolated_agent_dir: Path, content: str
) -> None:
    (isolated_agent_dir / "SYSTEM.md").write_text("GLOBAL")
    _project_file(tmp_path, "SYSTEM.md", content)
    args = Args()
    assert _resolve_system_prompt(args, str(tmp_path), tools=[], project_trusted=True) == (
        build_system_prompt(str(tmp_path), tools=[])
    )
    assert args.system_prompt_source_path is None


def test_bom_removed_without_stripping_frontmatter(tmp_path: Path) -> None:
    _project_file(tmp_path, "SYSTEM.md", "\ufeff---\nRaw markdown\n---\nRULE")
    assert _resolve_system_prompt(Args(), str(tmp_path), tools=[], project_trusted=True) == (
        "---\nRaw markdown\n---\nRULE"
    )


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_non_directory_project_config_has_no_candidate(tmp_path, isolated_agent_dir, capsys, name):
    (tmp_path / ".aelix").write_text("not a directory")
    (isolated_agent_dir / name).write_text("GLOBAL")
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=True
    ) == ("GLOBAL", str(isolated_agent_dir / name))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_growth_cannot_read_ahead_of_the_byte_budget(tmp_path, monkeypatch, name):
    path = _project_file(tmp_path, name, "RULE")
    limit = 16
    monkeypatch.setattr(system_prompt_files, "MAX_PROMPT_FILE_BYTES", limit)
    real_fstat = system_prompt_files.os.fstat
    real_close = system_prompt_files.os.close
    seen = {}

    def grow_after_descriptor_check(fd):
        info = real_fstat(fd)
        seen["fd"] = fd
        path.write_bytes(b"x" * (limit + 16384))
        return info

    def check_consumed_bytes(fd):
        if fd == seen.get("fd"):
            seen["consumed"] = system_prompt_files.os.lseek(fd, 0, system_prompt_files.os.SEEK_CUR)
        return real_close(fd)

    monkeypatch.setattr(system_prompt_files.os, "fstat", grow_after_descriptor_check)
    monkeypatch.setattr(system_prompt_files.os, "close", check_consumed_bytes)
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=True
    ) == (None, None)
    assert seen["consumed"] == limit + 1


@pytest.mark.parametrize("bad_kind", ["directory", "invalid_utf8", "oversized", "unreadable"])
def test_failed_read_warns_and_restores_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, bad_kind: str
) -> None:
    path = _project_file(tmp_path, "SYSTEM.md", "RULE")
    if bad_kind == "directory":
        path.unlink()
        path.mkdir()
    elif bad_kind == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif bad_kind == "oversized":
        monkeypatch.setattr(system_prompt_files, "MAX_PROMPT_FILE_BYTES", 1)
    else:

        def fail(*args, **kwargs):
            raise PermissionError("read denied")

        monkeypatch.setattr(os, "open", fail)
    args = Args()
    assert _resolve_system_prompt(args, str(tmp_path), tools=[], project_trusted=True) == (
        build_system_prompt(str(tmp_path), tools=[])
    )
    assert "Warning: cannot read system prompt file" in capsys.readouterr().err
    assert args.system_prompt_source_path is None


def test_pipe_trips_trust_without_blocking_discovery(tmp_path: Path, capsys) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("the platform has no FIFO support")
    directory = tmp_path / ".aelix"
    directory.mkdir()
    os.mkfifo(directory / "SYSTEM.md")
    assert has_trust_requiring_project_resources(tmp_path)
    _resolve_system_prompt(Args(), str(tmp_path), tools=[], project_trusted=True)
    assert "not a regular file" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [
        ["--append-system-prompt", "CLI"],
        ["--append-system-prompt", ""],
    ],
)
def test_explicit_append_suppresses_discovery(tmp_path: Path, flags: list[str]) -> None:
    _project_file(tmp_path, "APPEND_SYSTEM.md", "FILE")
    args = parse_args(flags)
    assert _resolve_append_chunks(args, str(tmp_path), project_trusted=True) == [flags[-1]]
    assert args.append_system_prompt_source_path is None


async def test_harness_order_and_live_rebuild(tmp_path: Path, isolated_agent_dir: Path) -> None:
    system = isolated_agent_dir / "SYSTEM.md"
    system.write_text("BASE_V1")
    append = _project_file(tmp_path, "APPEND_SYSTEM.md", "APPEND_V1")
    (tmp_path / "AGENTS.md").write_text("PROJECT_CONTEXT")
    args = Args(no_extensions=True)
    options = await _build_harness_options(args, Session(MemorySessionStorage()))
    harness = AgentHarness(options)
    try:
        first = harness.state.system_prompt
        assert first.startswith("BASE_V1")
        assert first.index("APPEND_V1") < first.index("<project_context>")
        assert "PROJECT_CONTEXT" in first
        system.write_text("BASE_V2")
        append.write_text("APPEND_V2")
        harness.rebuild_system_prompt()
        assert harness.state.system_prompt.startswith("BASE_V2")
        assert "APPEND_V2" in harness.state.system_prompt
        assert "APPEND_V1" not in harness.state.system_prompt
        # /new, /resume, /fork and /reload all use the same factory.
        rebuilt = await _build_harness_options(args, Session(MemorySessionStorage()))
        assert rebuilt.system_prompt == "BASE_V2"
        assert rebuilt.append_system_prompt[0] == "APPEND_V2"
        append.unlink()
        harness.rebuild_system_prompt()
        assert args.append_system_prompt_source_path is None
    finally:
        await harness.dispose()


@pytest.mark.parametrize("mode", ["append", "replace"])
def test_delegated_child_uses_existing_explicit_prompt_flags(
    tmp_path: Path, isolated_agent_dir: Path, mode: str
) -> None:
    (isolated_agent_dir / "SYSTEM.md").write_text("GLOBAL")
    (isolated_agent_dir / "APPEND_SYSTEM.md").write_text("GLOBAL_APPEND")
    path = tmp_path / "profile.md"
    text = f"---\nname: child\ndescription: child\nsystem_prompt: {mode}\n---\nCHILD"
    path.write_text(text)
    profile = parse_profile(text, file_path=str(path), scope="user").profile
    assert profile is not None
    args = parse_args(profile_to_flags(profile, prompt_path=str(path)))
    assert _apply_prompt_files(args) is None
    assert _resolve_system_prompt(args, str(tmp_path), tools=[]) == (
        "CHILD" if mode == "replace" else "GLOBAL"
    )
    assert _resolve_append_chunks(args, str(tmp_path)) == (
        ["GLOBAL_APPEND"] if mode == "replace" else ["CHILD"]
    )


def test_banner_shows_only_loaded_paths_in_order(tmp_path: Path) -> None:
    from aelix_coding_agent.tui.shell import _build_banner
    from rich.console import Console

    class Harness:
        def _action_get_system_prompt(self):
            return "BASE"

    buffer = io.StringIO()
    console = Console(file=buffer, width=240, force_terminal=False, no_color=True)
    console.print(
        _build_banner(
            Harness(),
            str(tmp_path),
            prompt_file_paths=[
                "/global/SYSTEM.md",
                "/project/.aelix/APPEND_SYSTEM.md",
            ],
        )
    )
    text = buffer.getvalue()
    assert "/global/SYSTEM.md, /project/.aelix/APPEND_SYSTEM.md" in text
    assert text.index("/global/SYSTEM.md") < text.index("/project/.aelix/APPEND_SYSTEM.md")


def test_warning_does_not_emit_control_characters(tmp_path: Path, capsys) -> None:
    system_prompt_files._warn(tmp_path / "path\x1b[31m\x07", "fail\x9b0m")
    warning = capsys.readouterr().err
    assert "\x1b" not in warning
    assert "\x07" not in warning
    assert "\x9b" not in warning


@pytest.mark.parametrize("trusted", [False, True])
def test_append_project_global_selection_and_context_switch(
    tmp_path: Path, isolated_agent_dir: Path, trusted: bool
) -> None:
    (isolated_agent_dir / "APPEND_SYSTEM.md").write_text("GLOBAL_APPEND")
    project = _project_file(tmp_path, "APPEND_SYSTEM.md", "PROJECT_APPEND")
    (tmp_path / "AGENTS.md").write_text("AGENTS")
    args = Args(no_context_files=True)
    assert _resolve_append_chunks(args, str(tmp_path), project_trusted=trusted) == [
        "PROJECT_APPEND" if trusted else "GLOBAL_APPEND"
    ]
    assert args.append_system_prompt_source_path == str(
        project if trusted else isolated_agent_dir / "APPEND_SYSTEM.md"
    )


def test_explicit_prompt_file_flags_win_over_discovered_files(tmp_path: Path) -> None:
    _project_file(tmp_path, "SYSTEM.md", "PROJECT")
    _project_file(tmp_path, "APPEND_SYSTEM.md", "PROJECT_APPEND")
    system = tmp_path / "explicit-system.md"
    system.write_text("EXPLICIT_BASE")
    append = tmp_path / "explicit-append.md"
    append.write_text("EXPLICIT_APPEND")
    args = parse_args(
        [
            "--system-prompt-file",
            str(system),
            "--append-system-prompt-file",
            str(append),
        ]
    )
    assert _apply_prompt_files(args) is None
    assert _resolve_system_prompt(args, str(tmp_path), tools=[], project_trusted=True) == (
        "EXPLICIT_BASE"
    )
    assert _resolve_append_chunks(args, str(tmp_path), project_trusted=True) == ["EXPLICIT_APPEND"]
    assert args.system_prompt_source_path is None
    assert args.append_system_prompt_source_path is None


def test_blank_project_append_masks_global(tmp_path: Path, isolated_agent_dir: Path) -> None:
    (isolated_agent_dir / "APPEND_SYSTEM.md").write_text("GLOBAL_APPEND")
    _project_file(tmp_path, "APPEND_SYSTEM.md", " \n")
    args = Args()
    assert _resolve_append_chunks(args, str(tmp_path), project_trusted=True) == []
    assert args.append_system_prompt_source_path is None


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_descriptor_race_growth_is_bounded(tmp_path: Path, monkeypatch, name: str, capsys) -> None:
    """Real growth at the selected check boundary cannot exceed the byte ceiling."""
    limit = 16
    path = _project_file(tmp_path, name, "RULE")
    monkeypatch.setattr(system_prompt_files, "MAX_PROMPT_FILE_BYTES", limit)
    original_stat, original_fstat = Path.stat, os.fstat
    original_fdopen = os.fdopen
    checks, reads = [], []

    def grow():
        path.write_bytes(b"x" * (limit + 2))

    def checked_stat(candidate, *args, **kwargs):
        info = original_stat(candidate, *args, **kwargs)
        if candidate == path:
            checks.append("path")
            grow()
        return info

    def checked_fstat(fd):
        info = original_fstat(fd)
        checks.append("descriptor")
        grow()
        return info

    @contextmanager
    def counted_fdopen(*args, **kwargs):
        with original_fdopen(*args, **kwargs) as stream:

            class Reader:
                def read(self, size):
                    reads.append(size)
                    return stream.read(size)

            yield Reader()

    # The path seam also demonstrates the old loader failing on this schedule.
    monkeypatch.setattr(Path, "stat", checked_stat)
    monkeypatch.setattr(os, "fstat", checked_fstat)
    monkeypatch.setattr(os, "fdopen", counted_fdopen)
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=True
    ) == (None, None)
    assert checks == ["descriptor"]
    assert reads == [limit + 1]
    assert "exceeds the 16-byte" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_descriptor_race_fifo_swap_does_not_block(tmp_path: Path, name: str) -> None:
    """Replace the pathname at the chosen stat/open boundary, without a sleep."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("the platform has no FIFO support; os.pipe covers descriptor validation")
    path = _project_file(tmp_path, name, "RULE")
    script = """
import os, sys
from pathlib import Path
from aelix_coding_agent.cli import system_prompt_files as loader
path = Path(sys.argv[1])
old_stat, old_open = Path.stat, os.open
swapped = False
def swap():
    global swapped
    if not swapped:
        path.unlink()
        os.mkfifo(path)
        swapped = True
def stat(candidate, *args, **kwargs):
    info = old_stat(candidate, *args, **kwargs)
    if candidate == path:
        swap()
    return info
def opened(candidate, flags, *args, **kwargs):
    if Path(candidate) == path:
        swap()
    return old_open(candidate, flags, *args, **kwargs)
Path.stat, os.open = stat, opened
assert loader.discover_system_prompt_file(str(path.parent.parent), path.name,
                                         project_trusted=True) == (None, None)
assert swapped
print('refused substituted FIFO')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert "refused substituted FIFO" in result.stdout
    assert "not a regular file" in result.stderr


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_open_regular_descriptor_survives_pathname_swap(tmp_path: Path, monkeypatch, name: str):
    if os.name == "nt":
        pytest.skip("Windows does not permit unlinking this open CRT file")
    path = _project_file(tmp_path, name, "ORIGINAL")
    original = os.open
    swapped = []

    def opened(candidate, *args, **kwargs):
        fd = original(candidate, *args, **kwargs)
        if Path(candidate) == path:
            path.unlink()
            path.write_text("REPLACEMENT")
            swapped.append(True)
        return fd

    monkeypatch.setattr(os, "open", opened)
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=True
    ) == ("ORIGINAL", str(path))
    assert swapped == [True]
    assert path.read_text() == "REPLACEMENT"


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_pipe_descriptor_refused_and_closed_on_every_platform(tmp_path: Path, monkeypatch, name):
    path = _project_file(tmp_path, name, "RULE")
    read_fd, write_fd = os.pipe()
    original_fstat = os.fstat
    opened_paths = []

    def opened(candidate, *args, **kwargs):
        opened_paths.append(Path(candidate))
        return read_fd

    monkeypatch.setattr(os, "open", opened)
    try:
        assert system_prompt_files.discover_system_prompt_file(
            str(tmp_path), name, project_trusted=True
        ) == (None, None)
        assert opened_paths == [path]
        with pytest.raises(OSError):
            original_fstat(read_fd)
    finally:
        with suppress(OSError):
            os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("failure", ["fstat", "fdopen", "read", "unicode", "size", None])
def test_descriptor_closed_after_success_and_failures(
    tmp_path: Path, isolated_agent_dir: Path, monkeypatch, failure
):
    path = _project_file(tmp_path, "SYSTEM.md", "RULE")
    (isolated_agent_dir / "SYSTEM.md").write_text("GLOBAL_MUST_STAY_MASKED")
    if failure == "unicode":
        path.write_bytes(b"\xff")
    if failure == "size":
        monkeypatch.setattr(system_prompt_files, "MAX_PROMPT_FILE_BYTES", 1)
    original_open, original_fstat, original_fdopen = os.open, os.fstat, os.fdopen
    descriptors = []

    def opened(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def fail(*args, **kwargs):
        raise OSError("controlled failure")

    @contextmanager
    def unreadable(*args, **kwargs):
        with original_fdopen(*args, **kwargs):

            class Reader:
                read = fail

            yield Reader()

    monkeypatch.setattr(os, "open", opened)
    if failure == "fstat":
        monkeypatch.setattr(os, "fstat", fail)
    elif failure == "fdopen":
        monkeypatch.setattr(os, "fdopen", fail)
    elif failure == "read":
        monkeypatch.setattr(os, "fdopen", unreadable)
    result = system_prompt_files.discover_system_prompt_file(
        str(tmp_path), "SYSTEM.md", project_trusted=True
    )
    assert result == (("RULE", str(path)) if failure is None else (None, None))
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        original_fstat(descriptors[0])


def test_open_without_platform_nonblocking_flag(tmp_path: Path, monkeypatch):
    """Platforms without O_NONBLOCK can still load regular prompt files."""
    path = _project_file(tmp_path, "SYSTEM.md", "RULE")
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), "SYSTEM.md", project_trusted=True
    ) == ("RULE", str(path))


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"A\r\nB\rC\n", "A\nB\nC\n"), (b"\xef\xbb\xbf\xc3\xa9\xc3\xa9\r", "éé\n")],
)
def test_byte_limit_bom_utf8_and_universal_newlines(tmp_path, monkeypatch, content, expected):
    path = _project_file(tmp_path, "SYSTEM.md", "RULE")
    path.write_bytes(content)
    monkeypatch.setattr(system_prompt_files, "MAX_PROMPT_FILE_BYTES", len(content))
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), "SYSTEM.md", project_trusted=True
    ) == (expected, str(path))
    # A byte limit counts the BOM and multibyte characters, before decoding.
    monkeypatch.setattr(system_prompt_files, "MAX_PROMPT_FILE_BYTES", len(content) - 1)
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), "SYSTEM.md", project_trusted=True
    ) == (None, None)


@pytest.mark.parametrize("name", ["SYSTEM.md", "APPEND_SYSTEM.md"])
def test_missing_or_dangling_project_candidate_falls_through(tmp_path, isolated_agent_dir, name):
    (isolated_agent_dir / name).write_text("GLOBAL")
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=True
    ) == ("GLOBAL", str(isolated_agent_dir / name))
    if os.name == "nt":
        pytest.skip("creating a Windows symlink can require privileges")
    path = tmp_path / ".aelix" / name
    path.parent.mkdir(exist_ok=True)
    path.symlink_to("missing-target")
    assert system_prompt_files.discover_system_prompt_file(
        str(tmp_path), name, project_trusted=True
    ) == ("GLOBAL", str(isolated_agent_dir / name))
