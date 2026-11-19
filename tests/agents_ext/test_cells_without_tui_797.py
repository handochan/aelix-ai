"""#797 — the ``agent`` tool is registered on an install without the ``tui`` extra.

``aelix_agents/tool.py`` and ``panel.py`` imported ``rich.cells`` and ``wcwidth`` at
module top. Both come only with the ``tui`` extra, so without it
``import aelix_agents`` raised ``ModuleNotFoundError``, ``cli/entry.py`` printed one
stderr warning and the run had no ``agent`` tool (Aelix Studio's bundle, a plain
``pip install aelix-coding-agent`` over RPC). Owner, 2026-11-18: "import 의존 제거
(권장)". ``aelix_agents/_cells.py`` now resolves rich and wcwidth on first call.

Pinned here:

* with the extra, each ``_cells`` function IS the library's (the TUI keeps exactly
  its widths), and without it the standard-library reading counts never fewer cells
  than ``rich`` over wide, combining, emoji, ZWJ and control characters;
* a real ``--mode rpc --agents`` child whose interpreter refuses ``rich``,
  ``wcwidth`` and ``prompt_toolkit`` lists ``agent`` in ``resources/list``. Fails
  on ``1c2a4a7c``: no ``agent``, and stderr says ``aelix-agents unavailable``;
* a bundled extension that cannot be imported (``aelix_agents``, ``aelix_status``)
  is in ``extensionErrors`` across a reload, not only on stderr. Fails on
  ``1c2a4a7c``: ``extensionErrors`` is ``[]``;
* the delegation panel's width read imports the TUI only when there is a chrome,
  so an RPC host's UI (no chrome) still gets the panel without prompt_toolkit.
  Fails on ``1c2a4a7c`` with ``ImportError``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import rich.cells
import wcwidth as wcwidth_module
from aelix_agents import _cells
from aelix_agents.progress import SubagentProgressBridge
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs import cells as std_cells

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env and wire are fixtures
    _drive_real_rpc,
    _write_models,
    env,
    wire,
)
from tests.env_sandbox import child_env

CORPUS = [
    "plain ascii",
    "한글 漢字 カタカナ",  # wide
    "é à́ ́lead",  # combining marks, one before any base
    "😀 🚀 🇰🇷",  # emoji, regional indicators
    "👩‍💻 👨‍👩‍👧 ‍‍ab",  # zero width joiner, two leading
    "❤️ 1️⃣ ✌️",  # variation selector 16
    "👍🏽 👋🏿",  # skin tone modifiers
    "bell\x07 esc\x1b del\x7f c1\x85 nul\x00",  # control characters
    "​‎⁠﻿ soft­hyphen",  # zero-width format characters
]


@pytest.fixture
def no_tui_libraries(monkeypatch: pytest.MonkeyPatch) -> None:
    """``rich`` and ``wcwidth`` absent for ``_cells``: an import of either raises."""

    for name in ("rich", "rich.cells", "wcwidth"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(_cells, "_impl", None)


def test_with_the_extra_each_function_is_the_librarys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cells, "_impl", None)
    for text in CORPUS:
        assert _cells.cell_len(text) == rich.cells.cell_len(text), repr(text)
        assert _cells.set_cell_size(text, 7) == rich.cells.set_cell_size(text, 7), repr(text)
        for char in text:
            assert _cells.wcwidth(char) == wcwidth_module.wcwidth(char), hex(ord(char))
    assert _cells._impl == (  # noqa: SLF001 — the resolved functions themselves
        rich.cells.cell_len,
        rich.cells.set_cell_size,
        wcwidth_module.wcwidth,
    )


@pytest.mark.usefixtures("no_tui_libraries")
def test_without_the_extra_the_standard_library_counts_never_fewer_cells_than_rich() -> None:
    assert _cells._impl is None  # noqa: SLF001
    for text in CORPUS:
        assert _cells.cell_len(text) == std_cells.cells(text), repr(text)
        assert _cells.cell_len(text) >= rich.cells.cell_len(text), repr(text)
        for char in text:
            assert _cells.wcwidth(char) == std_cells.char_cells(char), hex(ord(char))
    assert _cells._impl is not None  # noqa: SLF001
    assert _cells._impl[0] is std_cells.cells  # noqa: SLF001
    assert _cells._impl[2] is std_cells.char_cells  # noqa: SLF001


@pytest.mark.usefixtures("no_tui_libraries")
@pytest.mark.parametrize("total", [0, 1, 2, 3, 5, 8, 13])
def test_without_the_extra_set_cell_size_fills_exactly_and_keeps_whole_pieces(total: int) -> None:
    for text in CORPUS:
        sized = _cells.set_cell_size(text, total)
        assert std_cells.cells(sized) == total, (repr(text), total, repr(sized))
        pieces = std_cells.clusters(text)
        prefixes = ["".join(pieces[:k]) for k in range(len(pieces) + 1)]
        # The longest run of whole pieces that fits, padded: never a piece less, never half of one.
        longest = max((p for p in prefixes if std_cells.cells(p) <= total), key=len) if total > 0 else ""
        assert sized == longest + " " * (total - std_cells.cells(longest)), (repr(text), total, repr(sized))


def test_a_bundled_extension_s_load_error_names_it_and_its_cause() -> None:
    """``aelix extension verify`` reads installed endpoints only, so the
    ``/extension`` viewer must not point at it for a bundled package."""

    error = entry_mod._bundled_load_error("aelix-agents", ModuleNotFoundError("No module named 'rich'"))
    assert (error.path, error.error) == ("aelix-agents (bundled)", "ModuleNotFoundError: No module named 'rich'")
    assert error.verify_applies is False


def test_the_panel_width_read_needs_no_tui_when_there_is_no_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "aelix_coding_agent.tui.width", None)
    ui = SimpleNamespace(set_widget=lambda *a, **k: None)  # an RPC UI: no chrome
    api = SimpleNamespace(runtime=SimpleNamespace(ui=ui), events=SimpleNamespace(emit=lambda *a: None))
    bridge = SubagentProgressBridge(api)
    assert bridge._panel_width() == 80  # noqa: SLF001


_REFUSE_TUI = r"""
import importlib.abc
import sys

class _Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in ("rich", "wcwidth", "prompt_toolkit"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None

sys.meta_path.insert(0, _Refuse())
sys.argv = ["aelix", *sys.argv[1:]]
from aelix_coding_agent.cli.entry import main_sync

main_sync()
"""


def test_a_real_rpc_child_without_rich_wcwidth_or_prompt_toolkit_offers_agent(tmp_path: Path) -> None:
    home, agent_dir, cwd = tmp_path / "home", tmp_path / "home" / "agent", tmp_path / "cwd"
    for directory in (home / ".config", agent_dir, cwd):
        directory.mkdir(parents=True, exist_ok=True)
    provider = {
        "api": "openai-completions",
        "baseUrl": "http://127.0.0.1:9/v1",
        "apiKey": "fake-literal",
        "models": [{"id": "mockm"}],
    }
    (agent_dir / "models.json").write_text(
        json.dumps({"providers": {"mockp": provider}}), encoding="utf-8"
    )
    request = {"id": "l1", "type": "aelix_control", "method": "resources/list", "params": {}}
    done = subprocess.run(
        [sys.executable, "-P", "-c", _REFUSE_TUI, "--mode", "rpc", "--agents", "--no-session"]
        + ["--provider", "mockp", "--model", "mockm"],
        cwd=cwd,
        env=child_env(home, AELIX_CODING_AGENT_DIR=str(agent_dir), PI_OFFLINE="1"),
        input=json.dumps(request) + "\n",
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    responses = [
        json.loads(line)
        for line in done.stdout.splitlines()
        if line.startswith("{") and '"type":"response"' in line.replace(" ", "")
    ]
    listed = [r for r in responses if r.get("id") == "l1"]
    assert listed and listed[0]["success"], (done.returncode, done.stdout[-2000:], done.stderr[-2000:])
    tools = {tool["name"] for tool in listed[0]["data"]["tools"]}
    assert "agent" in tools, (sorted(tools), done.stderr[-2000:])
    assert listed[0]["data"]["extensionErrors"] == []
    assert "unavailable" not in done.stderr, done.stderr[-2000:]


async def test_a_bundled_extension_that_cannot_be_imported_is_listed_across_a_reload(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "aelix_agents", None)
    monkeypatch.setitem(sys.modules, "aelix_status", None)
    _write_models(env, "rec")

    def _control(request_id: str, method: str) -> dict[str, Any]:
        return {"id": request_id, "type": "aelix_control", "method": method, "params": {}}

    responses = _drive_real_rpc(
        monkeypatch,
        [_control("l1", "resources/list"), _control("r1", "resources/reload"), _control("l2", "resources/list")],
    )
    code = await entry_mod._async_main(["--model", "rec/m1", "--mode", "rpc", "--agents"])
    assert code == 0
    assert [r["success"] for r in responses] == [True] * 3, responses
    for listed in (responses[0], responses[2]):
        data = listed["data"]
        assert not {"agent", "aelix_status"} & {t["name"] for t in data["tools"]}
        assert sorted(e["source"] for e in data["extensionErrors"]) == [
            "aelix-agents (bundled)",
            "aelix-status (bundled)",
        ], data["extensionErrors"]
        for error in data["extensionErrors"]:
            assert error["message"].startswith(("ImportError: ", "ModuleNotFoundError: ")), error
    err = capsys.readouterr().err
    assert "Warning: aelix-agents unavailable:" in err
    assert "Warning: aelix-status unavailable:" in err


# === fix round 1 (Codex review of #797: H1, M3, L8) ==================================


def _refusing_module(message: str) -> Any:
    """``aelix_agents`` as an import that fails with ``ImportError(message)``."""

    module = ModuleType("aelix_agents")

    def __getattr__(name: str) -> Any:
        raise ImportError(message)

    module.__getattr__ = __getattr__  # type: ignore[method-assign]
    return module


def test_a_bundled_load_error_s_text_has_no_lone_surrogate() -> None:
    """H1: an ``ImportError`` can name a path the file system gave as a lone surrogate.
    Fails on ``1099de2b``: the text keeps ``\\udcff`` and UTF-8 cannot encode it."""

    error = entry_mod._bundled_load_error("aelix-agents", ImportError("at /x/\udcff/y"))
    assert error.error == "ImportError: at /x/�/y"
    error.error.encode("utf-8")


async def test_rpc_answers_both_lists_when_the_bundled_error_holds_a_lone_surrogate(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1 on the wire: both replies carry the error, U+FFFD for the surrogate.
    Fails on ``1099de2b``: neither id is answered (the reply's UTF-8 encode
    raised after the dispatch guard), and the driver times out."""

    monkeypatch.setitem(sys.modules, "aelix_agents", _refusing_module("simulated at \udcff"))
    _write_models(env, "rec")
    responses = _drive_real_rpc(
        monkeypatch,
        [
            {"id": "l1", "type": "aelix_control", "method": "resources/list", "params": {}},
            {"id": "s1", "type": "aelix_control", "method": "selections/list", "params": {"kind": "extension"}},
        ],
    )
    assert await entry_mod._async_main(["--model", "rec/m1", "--mode", "rpc", "--agents"]) == 0
    assert [(r["id"], r["success"]) for r in responses] == [("l1", True), ("s1", True)], responses
    (listed,) = [e for e in responses[0]["data"]["extensionErrors"] if e["source"] == "aelix-agents (bundled)"]
    assert listed["message"] == "ImportError: simulated at �"
    (row,) = [i for i in responses[1]["data"]["items"] if i["key"] == "bundled:aelix-agents (bundled)"]
    assert row["description"] == "failed to load: ImportError: simulated at �"


async def test_the_extension_inventory_lists_each_bundled_package_that_failed_to_load(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M3: the ``/extension`` Installed tab and RPC ``selections/list`` read the
    build's inventory, which held neither bundled failure; now each is an
    unavailable row, across a reload. Fails on ``1099de2b``: no ``bundled:`` item."""

    monkeypatch.setitem(sys.modules, "aelix_agents", None)
    monkeypatch.setitem(sys.modules, "aelix_status", None)
    _write_models(env, "rec")

    def _list(request_id: str) -> dict[str, Any]:
        return {"id": request_id, "type": "aelix_control", "method": "selections/list", "params": {"kind": "extension"}}

    responses = _drive_real_rpc(
        monkeypatch,
        [_list("s1"), {"id": "r1", "type": "aelix_control", "method": "resources/reload", "params": {}}, _list("s2")],
    )
    assert await entry_mod._async_main(["--model", "rec/m1", "--mode", "rpc", "--agents"]) == 0
    assert [r["success"] for r in responses] == [True] * 3, responses
    for listed in (responses[0], responses[2]):
        rows = {i["key"]: i for i in listed["data"]["items"] if i["key"].startswith("bundled:")}
        assert sorted(rows) == ["bundled:aelix-agents (bundled)", "bundled:aelix-status (bundled)"], rows
        for row in rows.values():
            assert row["role"] == "placeholder" and row["description"].startswith("failed to load: "), row
            assert row["lock"]["kind"] == "unavailable" and row["lock"]["tag"] == "failed", row
            assert row["lock"]["reason"] == row["description"]


def test_a_refused_row_still_says_refused() -> None:
    """The ``failed`` word is a bundled package's only (M3); a refused carrier keeps "refused:"."""

    from aelix_coding_agent.core.extension_selection import (
        ExtensionInventory,
        RefusedRow,
        with_bundled,
    )

    error = entry_mod._bundled_load_error("aelix-status", ModuleNotFoundError("No module named 'x'"))
    inventory = with_bundled(ExtensionInventory(refused=(RefusedRow("refused:0:a.toml", "a.toml", "bad manifest"),)), [error])
    assert [(r.key, r.why) for r in inventory.refused] == [
        ("refused:0:a.toml", "refused: bad manifest"),
        ("bundled:aelix-status (bundled)", "failed to load: ModuleNotFoundError: No module named 'x'"),
    ]
    assert with_bundled(inventory, []) is inventory


def test_the_text_viewer_says_failed_to_load_for_a_bundled_package_as_the_installed_tab_does() -> None:
    """One label for one failure (lane b7f verify round 2, N3): the ``/extension``
    text viewer's Installed lines said ``refused:`` for a bundled package whose
    import failed while the Installed tab said ``failed to load:``. A refused pack
    keeps ``refused:``. Fails on ``a3c8b9a6``: ``— refused: ModuleNotFoundError …``."""

    from aelix_coding_agent.extensions.loader import ExtensionLoadError
    from aelix_coding_agent.tui.extension_manager import build_installed_lines

    bundled = entry_mod._bundled_load_error("aelix-status", ModuleNotFoundError("No module named 'x'"))
    lines = build_installed_lines([], [], [ExtensionLoadError("a.toml", "bad manifest"), bundled])
    assert "  ✖ a.toml — refused: bad manifest" in lines, lines
    assert "  ✖ aelix-status (bundled) — failed to load: ModuleNotFoundError: No module named 'x'" in lines, lines
    assert repr(bundled) == repr(ExtensionLoadError(bundled.path, bundled.error)), "the CLI prints it as before"
    assert bundled == ExtensionLoadError(bundled.path, bundled.error), "bundled is left out of == (verify r3 L2)"


async def test_a_delegated_child_does_not_import_the_agents_package_the_depth_gate_forbids(
    env: Path,  # noqa: F811
    wire: list[tuple[str, str | None]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """L8: at ``MAX_SUBAGENT_DEPTH`` the extension is never registered, so a failed
    import there is no failed extension. Fails on ``1099de2b``: ``extensionErrors``
    lists ``aelix-agents (bundled)`` and stderr warns."""

    monkeypatch.setenv("AELIX_SUBAGENT_DEPTH", "1")
    monkeypatch.setitem(sys.modules, "aelix_agents", None)
    _write_models(env, "rec")
    responses = _drive_real_rpc(
        monkeypatch, [{"id": "l1", "type": "aelix_control", "method": "resources/list", "params": {}}]
    )
    assert await entry_mod._async_main(["--model", "rec/m1", "--mode", "rpc", "--agents"]) == 0
    assert responses[0]["success"], responses
    assert [e["source"] for e in responses[0]["data"]["extensionErrors"]] == []
    assert "aelix-agents unavailable" not in capsys.readouterr().err
