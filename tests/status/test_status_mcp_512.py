"""#512 (ADR-0282) — ``aelix status`` lists the MCP servers a launch would read.

Driven through the shipped ``run_status_command`` / ``_collect`` against real
files and the real trust resolver, like ``test_status_cli.py``. What is pinned:
the states are the shared vocabulary's (``enabled`` / ``disabled``), the
disabled hint names the file and the key, an untrusted project file is skipped
whole (as the launch skips it), endpoints never reach the output (a url can
carry a token), and reading ``mcp.json`` is reported under ``--no-extensions``
too, because it executes nothing and starts nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_coding_agent.cli.status import _collect, run_status_command

_TOKEN = "tok_PLANTED_512"


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, where: str = "global") -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.chdir(project)
    target = agent_dir / "mcp.json" if where == "global" else project / ".aelix" / "mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {"url": f"https://mcp.example.test/mcp?key={_TOKEN}"},
                    "quiet": {"command": "quiet-srv", "enabled": False},
                    "odd": {"command": "odd-srv", "enabled": "no"},
                }
            }
        ),
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize("discover", [True, False])
async def test_the_json_lists_states_in_the_shared_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, discover: bool
) -> None:
    target = _setup(tmp_path, monkeypatch)
    mcp = (await _collect(discover=discover))["mcp"]
    assert mcp["config"] == str(target)
    assert mcp["source"] == "global"
    assert mcp["skipped_untrusted"] is False
    assert mcp["servers"] == [
        {"name": "docs", "transport": "http", "state": "enabled"},
        {"name": "quiet", "transport": "stdio", "state": "disabled"},
    ]
    (warning,) = mcp["warnings"]
    assert "'odd'" in warning and '"enabled" must be true or false' in warning


async def test_the_text_says_disabled_and_how_to_turn_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(tmp_path, monkeypatch)
    assert await run_status_command(["--no-extensions"]) == 0
    out = capsys.readouterr().out
    block = out[out.index("MCP servers (") :]
    lines = block.splitlines()
    assert any(ln.split() == ["docs", "http", "enabled"] for ln in lines), block
    assert any(ln.split() == ["quiet", "stdio", "disabled"] for ln in lines), block
    assert 'Turned off by "enabled": false in ' in block
    assert "delete the key" in block
    assert "! " in block and "'odd'" in block


async def test_no_endpoint_or_token_reaches_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(tmp_path, monkeypatch)
    assert await run_status_command([]) == 0
    assert await run_status_command(["--json"]) == 0
    out = capsys.readouterr().out
    assert _TOKEN not in out
    assert "mcp.example.test" not in out
    assert "quiet-srv" not in out


async def test_an_untrusted_project_file_is_skipped_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(tmp_path, monkeypatch, where="project")
    report = await _collect(discover=False)
    mcp = report["mcp"]
    assert report["project_trusted"] is False
    assert mcp["source"] == "project" and mcp["skipped_untrusted"] is True
    assert mcp["servers"] == [] and mcp["warnings"] == []
    assert await run_status_command(["--no-extensions"]) == 0
    out = capsys.readouterr().out
    assert "MCP servers: .aelix/mcp.json skipped — this directory is not trusted" in out
    assert "quiet" not in out


async def test_a_trusted_project_file_is_listed_by_its_relative_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aelix_coding_agent.cli.project_trust import ProjectTrustStore

    _setup(tmp_path, monkeypatch, where="project")
    ProjectTrustStore(str(tmp_path / "agent")).set(tmp_path / "proj", True)
    assert await run_status_command(["--no-extensions"]) == 0
    out = capsys.readouterr().out
    assert "MCP servers (.aelix/mcp.json):" in out
    assert 'Turned off by "enabled": false in .aelix/mcp.json.' in out


async def test_no_file_says_none_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    report = await _collect(discover=False)
    assert report["mcp"]["config"] is None and report["mcp"]["servers"] == []
    assert await run_status_command(["--no-extensions"]) == 0
    assert "MCP servers: none configured." in capsys.readouterr().out


# --- review round 1 (2026-11-01) --------------------------------------------------


def _write(target: Path, servers: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


async def test_an_invalid_urls_token_never_reaches_text_or_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#512 review 512-02: the loader's pydantic message carried ``input_value``,
    which this command printed after only stripping control characters."""

    target = _setup(tmp_path, monkeypatch)
    _write(target, {"docs": {"url": [f"https://example.test/mcp?token={_TOKEN}"]}})
    assert await run_status_command(["--no-extensions"]) == 0
    assert await run_status_command(["--no-extensions", "--json"]) == 0
    out = capsys.readouterr().out
    assert _TOKEN not in out and "example.test" not in out
    assert "'docs': url: Input should be a valid string" in out


async def test_the_global_default_project_trust_is_the_launchs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#512 review 512-04: with ``"defaultProjectTrust": "always"`` and no saved
    decision every launch trusts the project and starts its servers, while
    status said they were skipped. And it says what it cannot predict."""

    _setup(tmp_path, monkeypatch, where="project")
    (tmp_path / "agent" / "settings.json").write_text(
        json.dumps({"defaultProjectTrust": "always"}), encoding="utf-8"
    )
    report = await _collect(discover=False)
    assert report["project_trusted"] is True
    mcp = report["mcp"]
    assert mcp["skipped_untrusted"] is False
    assert [s["name"] for s in mcp["servers"]] == ["docs", "quiet"]
    assert "project_trust handlers" in mcp["trust_note"]
    assert await run_status_command(["--no-extensions"]) == 0
    out = capsys.readouterr().out
    assert "status cannot predict either" in " ".join(out.split())
    block = out[out.index("MCP servers (") : out.index("Only a running session")]
    for line in block.splitlines():
        assert len(line) <= 80, line


async def test_the_trust_note_is_only_for_the_project_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch)
    assert (await _collect(discover=False))["mcp"]["trust_note"] is None


async def test_an_untrusted_project_file_says_the_global_file_is_not_read_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#512 verify V1: the project file is the one picked, so the global file's
    servers do not start in that directory either — only its names count."""

    _setup(tmp_path, monkeypatch, where="project")
    assert await run_status_command(["--no-extensions"]) == 0
    out = capsys.readouterr().out
    assert "none of your global" in out and "which it replaces" in out
    assert "names still keep an" in out


@pytest.mark.parametrize("name", ["서버" * 50, "x" * 200, "é" * 90])
async def test_rows_fit_80_cells_whatever_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """#512 review 512-05 / verify V6: names were cut and padded by code points,
    so a Korean name took 180 cells and pushed transport and state off the row."""

    import unicodedata

    def cells(text: str) -> int:
        return sum(
            0
            if unicodedata.combining(ch)
            else 2
            if unicodedata.east_asian_width(ch) in ("W", "F")
            else 1
            for ch in text
        )

    target = _setup(tmp_path, monkeypatch)
    _write(target, {name: {"command": "srv", "enabled": False}, "short": {"command": "srv"}})
    assert await run_status_command(["--no-extensions"]) == 0
    out = capsys.readouterr().out
    block = out[out.index("MCP servers (") :].splitlines()[1:3]
    assert [ln.split()[-2:] for ln in block] == [["stdio", "enabled"], ["stdio", "disabled"]]
    widths = {cells(ln) for ln in block}
    assert len(widths) <= 2 and max(widths) <= 80, block
    # The transport column starts at the same cell on every row.
    starts = {cells(ln[: ln.rindex("stdio")]) for ln in block}
    assert len(starts) == 1, block
