"""#624 (ADR-0305 D15, D16; opus M1) — ``/mcp`` in the TUI.

A bare ``/mcp`` opens the shared selection panel over ``McpSelection`` (real
``mcp.json`` files in ``tmp_path``); ``/mcp list`` keeps the read-only table; any
other argument is the usage line. The panel's words for a ``restart`` kind: the
status column ``off · restart``, a pending row's "the next aelix start applies
it" (never "Enter applies the saved value"), the summary's note, and "mcp.json
already said this" for an Enter that wrote nothing; both ←/→ refusals; and
``/mcp`` runs mid-turn.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    match_command,
    mid_turn_refusal,
)
from aelix_coding_agent.tui.mcp_panel import EFFECT_NOTE, mcp_panel_opener
from aelix_coding_agent.tui.selection_panel import (
    SelectionPanelBody,
    _state_line,
    cells,
    render_panel,
)

from tests.core.test_mcp_selection_624 import GLOBAL_TEXT, ONE_FILE, _Bench

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _SGR.sub("", text)


def _body(control: Any) -> SelectionPanelBody:
    body = SelectionPanelBody(
        control.selection,
        control.kind,
        title="MCP servers",
        noun=("server", "servers"),
        effect_note=EFFECT_NOTE,
        width=lambda: 100,
        count_info=True,  # as mcp_panel_opener passes it
    )
    body.plan(100, 30)
    return body


def _pick(body: SelectionPanelBody, key: str) -> None:
    body.model.cursor = next(i for i, s in enumerate(body.model.visible()) if s.item.key == key)


# --- /mcp's routing ------------------------------------------------------------------


def _ctx(**kwargs: Any) -> tuple[CommandContext, list[Any]]:
    committed: list[Any] = []
    ctx = CommandContext(
        chrome=SimpleNamespace(),
        harness=SimpleNamespace(),
        commit=committed.append,
        cwd=".",
        **kwargs,
    )
    return ctx, committed


async def _run(ctx: CommandContext, args: str) -> None:
    command = match_command("/mcp", BUILTIN_COMMANDS)
    assert command is not None and command.handler is not None
    await command.handler(ctx, args)


@pytest.mark.parametrize(
    ("args", "panel", "status"), [("", 1, 0), (" ", 1, 0), ("list", 0, 1), (" list ", 0, 1)]
)
async def test_a_bare_mcp_opens_the_panel_and_list_runs_the_table(
    args: str, panel: int, status: int
) -> None:
    calls = {"panel": 0, "status": 0}

    async def _panel() -> None:
        calls["panel"] += 1

    async def _status() -> None:
        calls["status"] += 1

    ctx, committed = _ctx(mcp_panel=_panel, mcp_status=_status)
    await _run(ctx, args)
    assert calls == {"panel": panel, "status": status}
    assert committed == []


async def test_without_a_panel_a_bare_mcp_is_the_table() -> None:
    calls: list[str] = []

    async def _status() -> None:
        calls.append("status")

    ctx, _committed = _ctx(mcp_status=_status)
    await _run(ctx, "")
    assert calls == ["status"]


@pytest.mark.parametrize("args", ["nope", "list x", "on docs"])
async def test_anything_else_is_the_usage_line(args: str) -> None:
    calls: list[str] = []

    async def _never() -> None:
        calls.append("called")

    ctx, committed = _ctx(mcp_panel=_never, mcp_status=_never)
    await _run(ctx, args)
    assert calls == []
    assert [str(c) for c in committed] == ["Usage: /mcp [list]"]


async def test_a_panel_that_fails_is_said_not_raised() -> None:
    async def _boom() -> None:
        raise RuntimeError("boom")

    ctx, committed = _ctx(mcp_panel=_boom)
    await _run(ctx, "")
    assert [str(c) for c in committed] == ["✖ /mcp failed: boom"]


def test_the_command_says_both_forms_and_runs_mid_turn() -> None:
    command = next(c for c in BUILTIN_COMMANDS if c.name == "mcp")
    assert command.description == "Turn MCP servers on or off (/mcp list: status, tool counts)"
    assert mid_turn_refusal(command, "") is None
    assert mid_turn_refusal(command, "list") is None


async def test_the_opener_opens_the_shared_panel_with_the_mcp_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_coding_agent.tui import selection_panel

    seen: list[tuple[Any, ...]] = []

    async def _panel(ctx: Any, selection: Any, kind: Any, **kwargs: Any) -> None:
        seen.append((ctx, selection, kind, kwargs))

    monkeypatch.setattr(selection_panel, "run_selection_panel", _panel)
    assert mcp_panel_opener(lambda: "ctx", None, lambda: None) is None
    control = SimpleNamespace(selection="sel", kind="kind")
    opener = mcp_panel_opener(lambda: "ctx", control, lambda: 77)  # type: ignore[arg-type]
    assert opener is not None
    await opener()
    ((ctx, selection, kind, kwargs),) = seen
    assert (ctx, selection, kind) == ("ctx", "sel", "kind")
    assert kwargs["title"] == "MCP servers"
    assert kwargs["noun"] == ("server", "servers")
    assert kwargs["effect_note"] == EFFECT_NOTE
    assert EFFECT_NOTE == "the next aelix start applies it; /reload does not reconnect MCP servers"
    assert kwargs["render_width_cap"]() == 77
    assert kwargs["count_info"] is True


# --- the panel's words ------------------------------------------------------------------


def test_the_panel_draws_its_title_noun_and_status_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.connect("docs", transport="http")
    bench.connect("fs")
    body = _body(bench.control())
    assert body.model.target == "global"
    rows = [_plain(r) for r in render_panel(body.model, body.layout)]  # type: ignore[arg-type]
    screen = "\n".join(rows)
    assert rows[0].startswith("── MCP servers ") and "Save to [Global]" in rows[0]
    assert "3 servers · 1 off · 1 locked" not in screen  # a placeholder is not an item
    assert "3 servers · 1 off" in rows[1]
    assert "SECRET-624" not in screen and "mcp.example.test" not in screen
    status = {s.item.key: body.model.status(s)[0] for s in body.model.states}
    assert status["quiet"] == "off" and status["docs"] == "" and status["odd"] == "skipped"
    _pick(body, "docs")
    body.handle("space")
    assert body.model.status(body.model.current())[0] == "* unsaved"  # type: ignore[arg-type]


async def test_a_save_says_the_next_start_and_leaves_the_row_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.connect("docs", transport="http")
    control = bench.control()
    body = _body(control)
    _pick(body, "docs")
    assert body.handle("space") == "handled"
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    text = lines[0].plain
    # The file's path wraps at the panel's width; the lines under it do not.
    assert text.startswith("✓ MCP servers saved to") and "mcp.json\n" in text
    assert text.endswith(f"\n    off  docs\n  {EFFECT_NOTE}")
    # Reopened: the row is pending until the next start, in restart's words.
    again = _body(control)
    _pick(again, "docs")
    docs = again.model.current()
    assert docs is not None and docs.pending
    assert again.model.status(docs)[0] == "off · restart"
    line = _plain(_state_line(again.model, docs, 100)).strip()
    assert line == "Saved off · now on · the next aelix start applies it"
    assert "Enter applies the saved value" not in line


async def test_an_enter_that_writes_nothing_says_mcp_json_already_said_this(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The concurrent case: another writer turned docs off after the panel opened."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    body = _body(bench.control())
    _pick(body, "docs")
    body.handle("space")
    turned_off = GLOBAL_TEXT.replace('"A": "SECRET-624"}}', '"A": "SECRET-624"}, "enabled": false}')
    bench.global_file.write_text(turned_off, encoding="utf-8")  # the other writer
    body.handle("enter")
    lines = await body.save()
    assert lines is not None
    assert lines[0].plain == "✓ MCP servers: mcp.json already said this"
    assert bench.global_file.read_text(encoding="utf-8") == turned_off


def test_the_arrows_refuse_with_the_one_file_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    body = _body(bench.control())
    assert body.handle("right") == "handled"
    assert body.model.message == (
        "warn",
        f"Cannot save to This project: {ONE_FILE.format(g=bench.global_label)}",
    )
    assert body.model.target == "global"
    body.handle("left")
    assert body.model.message is None and body.model.target == "global"


def test_a_trusted_project_file_refuses_the_global_arrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.write_project('{"mcpServers": {"local": {"command": "l"}}}')
    body = _body(bench.control())
    assert body.model.target == "project"
    body.handle("left")
    assert body.model.message == (
        "warn",
        f"Cannot save to Global: aelix reads .aelix/mcp.json in this folder, not "
        f"{bench.global_label}",
    )


@pytest.mark.parametrize("where", ["env", "untrusted"])
def test_both_arrows_refuse_where_nothing_is_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    bench = _Bench(tmp_path, monkeypatch, trusted=where != "untrusted")
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    if where == "env":
        chosen = tmp_path / "chosen.json"
        chosen.write_text(GLOBAL_TEXT, encoding="utf-8")
        monkeypatch.setenv("AELIX_MCP_CONFIG", str(chosen))
        reasons = {
            "Global": f"$AELIX_MCP_CONFIG chooses the file this run; edit {chosen} by hand",
            "This project": f"$AELIX_MCP_CONFIG chooses the file this run; edit {chosen} by hand",
        }
    else:
        bench.write_project('{"mcpServers": {"x": {"command": "SECRET-624"}}}')
        reasons = {
            "Global": f"aelix reads .aelix/mcp.json in this folder, not {bench.global_label}",
            "This project": "this folder is not trusted (/trust to decide)",
        }
    body = _body(bench.control())
    for key, word in (("left", "Global"), ("right", "This project")):
        body.handle(key)
        assert body.model.message == ("warn", f"Cannot save to {word}: {reasons[word]}")
    # Space on any row is refused too, and nothing becomes an edit.
    for index in range(len(body.model.visible())):
        body.model.cursor = index
        body.handle("space")
        assert body.model.message is not None and body.model.message[0] == "warn"
    assert body.model.edits == {}


# --- run_tui's wiring ------------------------------------------------------------------


def test_run_tui_takes_the_mcp_control() -> None:
    from aelix_coding_agent.tui.shell import run_tui

    assert inspect.signature(run_tui).parameters["mcp_control"].default is None


async def test_run_tui_wires_the_panel_and_keeps_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.tui import selection_panel
    from aelix_coding_agent.tui.shell import run_tui

    from tests.tui.test_reload_extensions_25 import _Registry
    from tests.tui.test_run_tui_smoke import (
        FakeHarness,
        FakeRuntime,
        _harness_chrome,
        _quit_within,
        _spy_commits,
        _wait,
    )

    opened: list[str] = []

    async def _panel(ctx: Any, selection: Any, kind: Any, **kwargs: Any) -> None:
        opened.append(kwargs["title"])

    monkeypatch.setattr(selection_panel, "run_selection_panel", _panel)
    control = SimpleNamespace(selection=object(), kind=object())
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(FakeHarness()),  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                mcp_control=control,
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/mcp\n")
        await _wait(lambda: opened == ["MCP servers"])
        pipe.send_text("/mcp list\n")
        await _wait(lambda: any("No MCP servers configured." in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert opened == ["MCP servers"]


# --- fix round 1: whole reasons, the header, the count, the skipped word -----------------
# (impl F1 and verify V-N5: the ←/→ refusal and the "Locked …" line were cut with "…" at 80
# and 120 columns; the live check's smaller words; V-F1: an Enter with no edit)

#: A longer path than ``tmp_path`` alone, as a CI runner's or a deep home's: the reason
#: names the file, so a one-row cut would hide its end.
_DEEP = ("a-scratch-directory-name",)
#: Every entry valid: no ⚠ banner takes the rows (an 80x24 panel has 17).
_VALID = '{"mcpServers": {"docs": {"url": "https://x.test/mcp"}, "fs": {"command": "f"}}}'


def _sized(control: Any, width: int, height: int, **kwargs: Any) -> SelectionPanelBody:
    body = SelectionPanelBody(
        control.selection,
        control.kind,
        title="MCP servers",
        noun=("server", "servers"),
        effect_note=EFFECT_NOTE,
        width=lambda: width,
        count_info=True,
        **kwargs,
    )
    # run_selection_panel's own budget: the screen's rows less the input and footer.
    body.plan(width, height - 7)
    return body


def _drawn(body: SelectionPanelBody, width: int) -> list[str]:
    assert body.layout is not None
    rows = [_plain(r) for r in body.render(width, body.layout.height)]
    assert len(rows) == body.layout.height, "the panel keeps its height"
    assert all(cells(r) <= width for r in rows), [r for r in rows if cells(r) > width]
    return rows


def _detail(rows: list[str]) -> list[str]:
    """The rows under the rule and the row naming the cursor's item, above the key hint."""

    rule = next(i for i, r in enumerate(rows) if r and set(r) == {"─"})
    return rows[rule + 2 : -1]


def _squashed(text: str | list[str]) -> str:
    """No whitespace: a wrapped path is broken after a separator, onto the next row."""

    joined = "".join(text) if isinstance(text, list) else text
    return "".join(joined.split())


@pytest.mark.parametrize(("width", "height"), [(80, 24), (120, 40)])
def test_the_arrow_refusal_is_drawn_whole_at_80_and_120_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, height: int
) -> None:
    bench = _Bench(tmp_path.joinpath(*_DEEP), monkeypatch)
    bench.global_file.write_text(_VALID, encoding="utf-8")
    body = _sized(bench.control(), width, height)
    body.handle("right")
    want = f"Cannot save to This project: {ONE_FILE.format(g=bench.global_label)}"
    assert body.model.message == ("warn", want)
    assert cells(want) > 2 * width, "anti-vacuity: two rows would cut it"
    detail = _detail(_drawn(body, width))
    assert _squashed(want) in _squashed(detail), detail
    assert not any(r.rstrip().endswith("…") for r in detail), detail
    # In the warning's colour, as on its one row before (a refused save's is red).
    raw = body.render(width, body.layout.height)  # type: ignore[union-attr]
    shown = [r for r in raw if _plain(r) in detail and _plain(r).strip()]
    assert len(shown) >= 3 and all(r.startswith("\x1b[33m") for r in shown), shown


@pytest.mark.parametrize(("width", "height"), [(80, 24), (120, 40)])
def test_the_aelix_mcp_config_locked_line_is_drawn_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, height: int
) -> None:
    deep = tmp_path.joinpath(*_DEEP)
    bench = _Bench(deep, monkeypatch)
    bench.global_file.write_text(_VALID, encoding="utf-8")
    chosen = deep / "third" / "mcp.json"
    chosen.parent.mkdir()
    chosen.write_text('{"mcpServers": {"envsrv": {"command": "e"}}}', encoding="utf-8")
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(chosen))
    body = _sized(bench.control(), width, height)
    _pick(body, "envsrv")
    state = body.model.current()
    assert state is not None and state.lock is not None
    whole = _plain(_state_line(body.model, state, 10_000)).strip()
    assert whole.startswith("Locked off: $AELIX_MCP_CONFIG chooses the file this run; edit ")
    assert whole.endswith(f"{chosen} by hand"), whole
    assert len(whole) > width, "anti-vacuity: one row would cut it"
    detail = _detail(_drawn(body, width))
    assert _squashed(whole) in _squashed(detail), detail
    assert not any(r.rstrip().endswith("…") for r in detail), detail
    # The ←/→ refusals name the same file, whole.
    for key, word in (("left", "Global"), ("right", "This project")):
        body.handle(key)
        want = f"Cannot save to {word}: $AELIX_MCP_CONFIG chooses the file this run; edit {chosen} by hand"
        assert body.model.message == ("warn", want)
        detail = _detail(_drawn(body, width))
        assert _squashed(want) in _squashed(detail), detail
        assert not any(r.rstrip().endswith("…") for r in detail), detail


@pytest.mark.parametrize("where", ["global", "project", "env", "untrusted"])
def test_the_save_to_control_marks_a_scope_only_when_one_is_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    bench = _Bench(tmp_path, monkeypatch, trusted=where != "untrusted")
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    if where == "env":
        chosen = tmp_path / "chosen.json"
        chosen.write_text(GLOBAL_TEXT, encoding="utf-8")
        monkeypatch.setenv("AELIX_MCP_CONFIG", str(chosen))
    elif where in ("project", "untrusted"):
        bench.write_project('{"mcpServers": {"local": {"command": "l"}}}')
    body = _body(bench.control())
    title = _plain(render_panel(body.model, body.layout)[0])  # type: ignore[arg-type]
    marked = {
        "global": "Save to [Global]  This project",
        "project": "Save to Global  [This project]",
        "env": "Save to Global  This project",
        "untrusted": "Save to Global  This project",
    }
    assert marked[where] in title, title


def test_an_untrusted_folder_counts_the_extension_servers_it_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch, trusted=False)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.write_project('{"mcpServers": {"x": {"command": "SECRET-624"}}}')
    bench.owners["packsrv"] = SimpleNamespace(plugin="weather-pack", started=True, carrier=None)
    bench.connect("packsrv")
    body = _body(bench.control())
    roles = sorted(s.item.role for s in body.model.states)
    assert roles == ["info", "placeholder"], roles
    search = _plain(render_panel(body.model, body.layout)[1])  # type: ignore[arg-type]
    assert "1 server" in search and "0 servers" not in search, search


def test_the_server_count_takes_the_extension_rows_as_mcp_list_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs, fs and quiet are items, odd is a skipped entry, packsrv an extension's server:
    four servers, as ``/mcp list`` counts the three started or off and the extension's."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    bench.owners["packsrv"] = SimpleNamespace(plugin="weather-pack", started=True, carrier=None)
    bench.connect("packsrv")
    body = _body(bench.control())
    search = _plain(render_panel(body.model, body.layout)[1])  # type: ignore[arg-type]
    assert "4 servers · 1 off" in search, search


def test_an_entry_that_never_started_says_skipped_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    body = _body(bench.control())
    _pick(body, "odd")
    odd = body.model.current()
    assert odd is not None and odd.item.role == "placeholder" and odd.problem is not None
    assert body.model.status(odd)[0] == "skipped"
    line = _plain(_state_line(body.model, odd, 10_000)).strip()
    assert "failed" not in line, line
    assert line == (
        f"Unavailable: {bench.global_label} skips this entry; fix it by hand · "
        "server 'odd': \"enabled\" must be true or false, not a string; not started"
    )


async def test_an_enter_with_no_edit_says_nothing_to_save_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-F1: not "mcp.json already said this", which only an edit the file already holds says."""

    bench = _Bench(tmp_path, monkeypatch)
    bench.global_file.write_text(GLOBAL_TEXT, encoding="utf-8")
    before = bench.global_file.read_bytes()
    body = _body(bench.control())
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    assert lines[0].plain == "✓ MCP servers: nothing to save"
    assert bench.global_file.read_bytes() == before
