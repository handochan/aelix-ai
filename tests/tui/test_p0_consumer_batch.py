"""P0 consumer batch — context meter, real-mode footer, /export, /thinking,
colorized diffs. The harness already exposed these capabilities; the TUI now
surfaces them (ADR-0116)."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

from aelix_ai.providers.openai_completions import _usage_to_dict
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    match_command,
)
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.render import _looks_like_diff, _render_diff
from aelix_coding_agent.tui.shell import _format_context_label
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


def _plain(renderable: Any) -> str:
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, no_color=True, width=120).print(
        renderable, end=""
    )
    return buf.getvalue()


# === context meter formatting (shell._format_context_label) =================


def test_format_context_label_percent_and_tokens() -> None:
    usage = SimpleNamespace(percent=42.0, tokens=84000, context_window=200000)
    label = _format_context_label(usage)
    assert label is not None
    assert "42%" in label and "84K" in label and "200K" in label


def test_format_context_label_percent_only() -> None:
    usage = SimpleNamespace(percent=10.0, tokens=None, context_window=0)
    assert _format_context_label(usage) == "◔ 10%"


def test_format_context_label_none_usage_is_no_segment() -> None:
    assert _format_context_label(None) is None
    # usage object with nothing usable → no segment
    assert _format_context_label(SimpleNamespace(percent=None, tokens=None, context_window=0)) is None


# === colorized diff rendering (render._looks_like_diff / _render_diff) =======

_DIFF = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-old line\n+new line\n ctx\n"


def test_looks_like_diff_needs_hunk_header() -> None:
    assert _looks_like_diff(_DIFF) is True
    # plus/minus without a @@ hunk header must NOT be treated as a diff
    assert _looks_like_diff("result: +1 added, -2 removed") is False


def test_looks_like_diff_rejects_markdown_with_bare_at() -> None:
    # markdown with a `---` rule + a literal `@@` but NO real hunk header
    md = "# Title\n\n---\n\nsee `@@field` in the spec\n+ a bullet\n- another"
    assert _looks_like_diff(md) is False


def test_render_diff_colors_added_and_removed() -> None:
    grp = _render_diff(_DIFF)
    pairs = [
        (str(getattr(r, "style", "")), str(getattr(r, "plain", "")))
        for r in grp.renderables
    ]
    assert any(st == "green" and "+new line" in txt for st, txt in pairs)
    assert any(st == "red" and "-old line" in txt for st, txt in pairs)
    assert any(st == "cyan" and txt.startswith("@@") for st, txt in pairs)


# === usage capture (adapter feeds the meter + /cost) ========================


def test_usage_to_dict_maps_openai_shape() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=40,
        total_tokens=140,
        prompt_tokens_details=SimpleNamespace(cached_tokens=10),
    )
    d = _usage_to_dict(usage)
    assert d == {
        "input": 100,
        "output": 40,
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
        "cache_read": 10,
    }


def test_usage_to_dict_dict_shaped_and_total_fallback() -> None:
    # dict-shaped (mock) + no total_tokens → derived from prompt+completion
    d = _usage_to_dict({"prompt_tokens": 7, "completion_tokens": 3})
    assert d is not None and d["total_tokens"] == 10


def test_usage_to_dict_empty_is_none() -> None:
    assert _usage_to_dict(None) is None
    assert _usage_to_dict(SimpleNamespace(prompt_tokens=0, completion_tokens=0)) is None


async def test_stream_captures_usage_onto_done_message() -> None:
    """The final usage-only chunk (empty choices) must still populate
    AssistantMessage.usage so the meter/cost have real numbers (ADR-0116)."""
    from aelix_ai.providers.openai_completions import stream_openai_completions
    from aelix_ai.streaming import Context, Model, SimpleStreamOptions

    class _It:
        def __init__(self, chunks: list[Any]) -> None:
            self._chunks = chunks
            self.response = SimpleNamespace(status_code=200, headers={})

        def __aiter__(self) -> Any:
            self._i = 0
            return self

        async def __anext__(self) -> Any:
            if self._i >= len(self._chunks):
                raise StopAsyncIteration
            c = self._chunks[self._i]
            self._i += 1
            return c

    class _Raw:
        def __init__(self, it: _It) -> None:
            self._it = it
            self.http_response = it.response

        def parse(self) -> _It:
            return self._it

    class _WRR:
        def __init__(self, it: _It) -> None:
            self._it = it

        async def create(self, **kwargs: Any) -> _Raw:
            return _Raw(self._it)

    chunks = [
        SimpleNamespace(id="x", model="m", usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"), finish_reason=None)]),
        SimpleNamespace(id="x", model="m", usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")]),
        # final usage-only chunk: empty choices, carries usage
        SimpleNamespace(id="x", model="m", choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20, prompt_tokens_details=None)),
    ]
    it = _It(chunks)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=_WRR(it))))
    opts = SimpleStreamOptions(client=client, api_key="k")
    model = Model(api="openai-completions", id="m", provider="openai", base_url="https://api.openai.com/v1")

    done_usage: Any = "MISSING"
    async for ev in stream_openai_completions(model, Context(), opts):
        if type(ev).__name__ == "AssistantDoneEvent":
            done_usage = ev.message.usage  # type: ignore[attr-defined]
    assert done_usage is not None
    assert done_usage["total_tokens"] == 20
    assert done_usage["input"] == 12 and done_usage["output"] == 8


# === /export + /thinking handlers ==========================================


async def _run(name: str, args: str, harness: Any) -> list[Any]:
    committed: list[Any] = []
    ctx = CommandContext(
        chrome=SimpleNamespace(),  # type: ignore[arg-type]
        harness=harness,
        commit=committed.append,
        cwd="/work",
        commands=list(BUILTIN_COMMANDS),
    )
    cmd = match_command(f"/{name}", BUILTIN_COMMANDS)
    assert cmd is not None and cmd.handler is not None
    await cmd.handler(ctx, args)
    return committed


class _ExportHarness:
    def export_to_html(self, path: Any = None) -> str:
        return "/tmp/session.html"


async def test_export_reports_path() -> None:
    out = await _run("export", "", _ExportHarness())
    assert "session.html" in _plain(out[0])


async def test_export_unavailable_degrades() -> None:
    out = await _run("export", "", SimpleNamespace())
    assert "unavailable" in _plain(out[0]).lower()


async def test_export_failure_surfaces_not_crashes() -> None:
    class _Boom:
        def export_to_html(self, path: Any = None) -> str:
            raise RuntimeError("nothing to export")

    out = await _run("export", "", _Boom())
    assert "export failed" in _plain(out[0]).lower()


async def test_thinking_shows_current_level() -> None:
    h = SimpleNamespace(_state=SimpleNamespace(thinking_level="medium"))
    out = await _run("thinking", "", h)
    assert "medium" in _plain(out[0])


async def test_thinking_sets_level() -> None:
    captured: dict[str, str] = {}

    async def _set(level: str) -> None:
        captured["level"] = level

    h = SimpleNamespace(
        _state=SimpleNamespace(thinking_level="low"), set_thinking_level=_set
    )
    out = await _run("thinking", "high", h)
    assert captured["level"] == "high"
    assert "high" in _plain(out[0])


async def test_thinking_set_unavailable_degrades() -> None:
    h = SimpleNamespace(_state=SimpleNamespace(thinking_level=None))
    out = await _run("thinking", "high", h)
    assert "unavailable" in _plain(out[0]).lower()


async def test_thinking_unset_shows_off_not_unavailable() -> None:
    # level None + a working harness → "off (default)", NOT "unavailable"
    async def _set(level: str) -> None:
        return None

    h = SimpleNamespace(_state=SimpleNamespace(thinking_level=None), set_thinking_level=_set)
    out = await _run("thinking", "", h)
    text = _plain(out[0]).lower()
    assert "off" in text and "unavailable" not in text


# === real steering-mode footer + live context label (context) ==============


async def test_footer_shows_real_mode_and_context_label() -> None:
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(
            chrome,
            AelixFooterData(cwd="."),
            # ADR-0159: the steering ⏵⏵ segment is hidden at the default
            # "one-at-a-time"; use "all" so the provider-wins behaviour is still
            # observable in the footer.
            mode_provider=lambda: "all",
            mode="default",
        )
        # mode_provider ("all") wins over the local "default" placeholder
        assert "⏵⏵ all" in chrome._footer_line
        assert "default" not in chrome._footer_line
        # live context meter segment appears after a turn
        ctx.set_context_label("◔ 5%")
        assert "◔ 5%" in chrome._footer_line
