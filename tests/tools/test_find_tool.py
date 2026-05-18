"""Sprint 5b §A — find tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_find_tool


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


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
    result = await _exec(
        tool, {"pattern": "*.py", "path": str(tmp_path / "missing-dir")}
    )
    assert result.is_error is True


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


async def test_find_execution_mode_parallel():
    tool = create_find_tool("/tmp")
    assert tool.execution_mode == "parallel"


async def test_find_exact_limit_not_truncated(tmp_path):
    """W4 MAJOR-3 regression: when match count equals ``limit`` exactly, no
    results were dropped — ``truncated`` must be ``False``.

    Pi parity: ``relativized.length >= effectiveLimit`` collapses both
    "exact limit" and "over limit" into truncated; our impl uses a strictly
    greater check on the *collected* (pre-slice) count, which correctly
    flags only the over-limit case. We swap in a custom ``FindOperations``
    so the count is deterministic regardless of which backend (fd vs glob)
    the host machine has.
    """

    from aelix_coding_agent.tools.find import FindToolDetails

    class _ExactGlobOps:
        async def exists(self, path):  # type: ignore[override]
            return True

        async def glob(self, base, pattern):  # type: ignore[override]
            # Return exactly the limit (5) — must NOT be flagged truncated.
            return [f"{base}/f{i}.txt" for i in range(5)]

    # Force the Python fallback by giving a pattern fd won't be invoked for
    # — but more deterministically, route through ops by patching the
    # module-level fd shim to a no-op.
    import aelix_coding_agent.tools.find as find_mod

    original_try_fd = find_mod._try_fd
    find_mod._try_fd = lambda *a, **k: None  # type: ignore[assignment]
    try:
        tool = create_find_tool(str(tmp_path), {"operations": _ExactGlobOps()})
        result = await _exec(tool, {"pattern": "*.txt", "limit": 5})
    finally:
        find_mod._try_fd = original_try_fd  # type: ignore[assignment]

    assert result.is_error is False
    assert isinstance(result.details, FindToolDetails)
    assert result.details.truncated is False, (
        "exact-limit case must not be flagged truncated"
    )
    assert result.details.result_limit_reached is False


async def test_find_over_limit_is_truncated(tmp_path):
    """Companion to the exact-limit regression: 6 collected vs limit=5
    must flag ``truncated=True``.
    """

    from aelix_coding_agent.tools.find import FindToolDetails

    class _OverGlobOps:
        async def exists(self, path):  # type: ignore[override]
            return True

        async def glob(self, base, pattern):  # type: ignore[override]
            return [f"{base}/f{i}.txt" for i in range(6)]

    import aelix_coding_agent.tools.find as find_mod

    original_try_fd = find_mod._try_fd
    find_mod._try_fd = lambda *a, **k: None  # type: ignore[assignment]
    try:
        tool = create_find_tool(str(tmp_path), {"operations": _OverGlobOps()})
        result = await _exec(tool, {"pattern": "*.txt", "limit": 5})
    finally:
        find_mod._try_fd = original_try_fd  # type: ignore[assignment]

    assert result.is_error is False
    assert isinstance(result.details, FindToolDetails)
    assert result.details.truncated is True
    assert result.details.result_limit_reached is True
