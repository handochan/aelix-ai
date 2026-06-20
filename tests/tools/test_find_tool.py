"""Sprint 5b §A — find tool tests (P0 #3 behavior parity)."""

from __future__ import annotations

import pytest
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_find_tool
from aelix_coding_agent.tools.find import FindToolDetails


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


class _StaticGlobOps:
    """Deterministic ``FindOperations`` returning a fixed path list."""

    def __init__(self, paths):
        self._paths = paths

    async def exists(self, path):  # type: ignore[override]
        return True

    async def glob(self, base, pattern):  # type: ignore[override]
        return list(self._paths)


@pytest.fixture
def _no_fd(monkeypatch):
    """Disable fd so tests route deterministically through ``operations``."""

    import aelix_coding_agent.tools.find as find_mod

    async def _disabled(*a, **k):
        return None

    monkeypatch.setattr(find_mod, "_try_fd", _disabled)


async def test_find_returns_matches(tmp_path):
    (tmp_path / "alpha.py").write_text("")
    (tmp_path / "beta.py").write_text("")
    (tmp_path / "ignore.txt").write_text("")
    tool = create_find_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "*.py"})
    assert result.is_error is False
    assert "alpha.py" in result.content[0].text or "beta.py" in result.content[0].text


async def test_find_missing_path_uses_cwd(tmp_path):
    (tmp_path / "f.txt").write_text("")
    tool = create_find_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "*.txt"})
    assert result.is_error is False


async def test_find_nonexistent_base(tmp_path):
    tool = create_find_tool(str(tmp_path))
    missing = str(tmp_path / "missing-dir")
    result = await _exec(tool, {"pattern": "*.py", "path": missing})
    assert result.is_error is True
    # Pi parity error string: ``Path not found: {base}``.
    assert result.content[0].text == f"Path not found: {missing}"


async def test_find_missing_pattern():
    tool = create_find_tool("/tmp")
    result = await _exec(tool, {})
    assert result.is_error is True


async def test_find_limit(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("")
    tool = create_find_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "*.txt", "limit": 5})
    assert result.is_error is False


async def test_find_no_matches(tmp_path):
    tool = create_find_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "*.zzz"})
    assert result.is_error is False
    # Pi parity empty-result string.
    assert result.content[0].text == "No files found matching pattern"


async def test_find_execution_mode_parallel():
    tool = create_find_tool("/tmp")
    assert tool.execution_mode == "parallel"


async def test_find_relativizes_paths_under_base(tmp_path, _no_fd):
    """Pi parity: output paths are POSIX-relative to the search dir, not
    absolute, when the result starts with the search root.
    """

    base = str(tmp_path)
    ops = _StaticGlobOps(
        [f"{base}/src/a.py", f"{base}/src/sub/b.py"]
    )
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "**/*.py"})
    assert result.is_error is False
    lines = result.content[0].text.split("\n")
    assert lines == ["src/a.py", "src/sub/b.py"]


async def test_find_relativizes_path_outside_base(tmp_path, _no_fd):
    """Paths not under the search root fall back to a POSIX-relative path
    (Pi ``path.relative``) — here a basename via the shared helper.
    """

    base = str(tmp_path / "search")
    (tmp_path / "search").mkdir()
    ops = _StaticGlobOps([str(tmp_path / "outside.py")])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "*.py"})
    assert result.is_error is False
    # relativize_to_posix returns ../outside.py -> basename fallback.
    assert result.content[0].text == "outside.py"


async def test_find_preserves_trailing_slash(tmp_path, _no_fd):
    """Pi parity: a result line ending with a separator keeps its trailing
    slash after relativization.
    """

    base = str(tmp_path)
    ops = _StaticGlobOps([f"{base}/somedir/"])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "*"})
    assert result.is_error is False
    assert result.content[0].text == "somedir/"


async def test_find_skips_blank_lines(tmp_path, _no_fd):
    """Blank / whitespace-only result lines are dropped (Pi ``if (!line)``)."""

    base = str(tmp_path)
    ops = _StaticGlobOps([f"{base}/a.py", "   ", ""])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "*.py"})
    assert result.is_error is False
    assert result.content[0].text == "a.py"


async def test_find_exact_limit_not_truncated(tmp_path, _no_fd):
    """W4 MAJOR-3 regression: when match count equals ``limit`` exactly, no
    results were dropped — ``truncated`` must be ``False`` and no notice.
    """

    base = str(tmp_path)
    ops = _StaticGlobOps([f"{base}/f{i}.txt" for i in range(5)])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "*.txt", "limit": 5})

    assert result.is_error is False
    assert isinstance(result.details, FindToolDetails)
    assert result.details.truncated is False, (
        "exact-limit case must not be flagged truncated"
    )
    assert result.details.result_limit_reached is False
    assert "limit reached" not in result.content[0].text


async def test_find_over_limit_is_truncated(tmp_path, _no_fd):
    """Companion to the exact-limit regression: 6 collected vs limit=5 must
    flag ``truncated=True`` AND append the Pi-faithful result-limit notice.
    """

    base = str(tmp_path)
    ops = _StaticGlobOps([f"{base}/f{i}.txt" for i in range(6)])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "*.txt", "limit": 5})

    assert result.is_error is False
    assert isinstance(result.details, FindToolDetails)
    assert result.details.truncated is True
    assert result.details.result_limit_reached is True
    # Pi parity notice string, wrapped in ``[...]``.
    assert result.content[0].text.endswith(
        "\n\n[5 results limit reached. Use limit=10 for more, or refine pattern]"
    )


async def test_find_limit_zero_preserved(tmp_path, _no_fd):
    """Pi parity: ``limit=0`` is preserved (``is None`` check), NOT clamped to
    the 1000 default. Any match overflows a zero limit.
    """

    base = str(tmp_path)
    ops = _StaticGlobOps([f"{base}/a.py"])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "*.py", "limit": 0})

    assert result.is_error is False
    # Zero limit => no kept results => empty-result message.
    assert result.content[0].text == "No files found matching pattern"


async def test_find_byte_cap_notice(tmp_path, _no_fd):
    """Pi parity: output over 50KB is byte-truncated and gets the
    ``50.0KB limit reached`` notice.
    """

    base = str(tmp_path)
    # Each entry contributes a long relative path; many of them blow the cap.
    long_name = "x" * 200
    ops = _StaticGlobOps([f"{base}/{long_name}_{i}.py" for i in range(500)])
    tool = create_find_tool(base, {"operations": ops})
    result = await _exec(tool, {"pattern": "**/*.py", "limit": 10000})

    assert result.is_error is False
    assert isinstance(result.details, FindToolDetails)
    assert result.details.truncated is True
    assert "50.0KB limit reached" in result.content[0].text


# --- fd-backed path: deterministic via a stubbed subprocess (no real fd) -----


async def test_find_fd_path_relativizes(tmp_path, monkeypatch):
    """P0 #3 HEAVY (ADR-0139): exercise the fd-backed branch of find. The
    gitignore-respect flag ``--no-require-git`` is passed and absolute fd output
    is relativized to the search dir (mirrors grep's rg lock-in test)."""

    from aelix_coding_agent.tools import find as _find_mod

    (tmp_path / "src").mkdir()
    captured: dict = {}

    async def _fake_run_cancellable(cmd, **_kw):
        captured["cmd"] = cmd
        out = f"{tmp_path}/src/app.py\n{tmp_path}/src/util.py\n"
        return (out, 0)

    async def _fd(_tool, silent=True):
        return "/fake/fd"

    monkeypatch.setattr(_find_mod, "run_cancellable", _fake_run_cancellable)
    monkeypatch.setattr(_find_mod, "ensure_tool", _fd)
    tool = create_find_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "*.py"})
    assert "--no-require-git" in captured["cmd"]  # gitignore-respect flag
    text = result.content[0].text
    assert "src/app.py" in text and "src/util.py" in text
    assert str(tmp_path) not in text  # paths are relativized, not absolute


async def test_find_fd_exact_limit_not_truncated(tmp_path, monkeypatch):
    """fd path: a result set exactly equal to ``limit`` is NOT flagged truncated
    (W4 MAJOR-3 overflow-vs-exact boundary), only strictly-over is."""

    from aelix_coding_agent.tools import find as _find_mod

    # fd is invoked with --max-results limit+1; emit exactly `limit` (2) lines.
    async def _fake_run_cancellable(cmd, **_kw):
        out = f"{tmp_path}/a.py\n{tmp_path}/b.py\n"
        return (out, 0)

    async def _fd(_tool, silent=True):
        return "/fake/fd"

    monkeypatch.setattr(_find_mod, "run_cancellable", _fake_run_cancellable)
    monkeypatch.setattr(_find_mod, "ensure_tool", _fd)
    tool = create_find_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "*.py", "limit": 2})
    assert "limit reached" not in result.content[0].text
    assert result.details.result_limit_reached is False
