"""#285 — a text block that arrives whole is drawn live, once, where replay draws it.

A stream can deliver an answer block as ``text_start`` -> ``text_end(content)``
with no ``text_delta`` in between: a Responses server that puts the text only in
``response.output_item.done`` does (``process_responses_stream`` makes the slot
at that moment), and so can an extension's stream adapter. On ``529b997e`` the
TUI renderer committed such a block only if a delta had opened its stream, so
the live screen lost the text that the final message, the session file and the
``/resume`` replay all had; and the text it parked in ``_text_accum`` was then
painted in front of the NEXT block's live tail and dropped at its commit.

The rows run the wire through the REAL stream engines (``process_responses_stream``
and the openai-completions adapter, behind a fake SDK client) into the REAL
:class:`EventRenderer`, and compare what it committed with what
:meth:`EventRenderer.replay` commits for the final message.

Rows also guard the obvious fix (open a stream at every ``text_end`` that finds
none): openai-completions sends its end events in block order at end-of-stream,
so the reasoning's ``thinking_end`` closes the answer's stream (through
``_flush_thinking``) BEFORE the answer's own ``text_end`` arrives — committing
at that ``text_end`` prints the answer twice. The one mark that says "a flush
closed the answer early" is set only by that flush (a block closed by its own
end leaves nothing pending, and a mark it left would swallow the next whole
block), is spent by the next stream-less ``text_end`` whose text renders to
something, does not outlive its message (an abort between the flush and the
late end leaves it set), and does not read ``content_index``: an adapter that
rebuilds ``text_delta`` without its index (default 0) under a ``text_start``
that said 1 drew the answer twice with a mark keyed on the index (review
round 3).

Three groups pin what the whole-block commit draws. A block that renders to
nothing (for example only spaces and newlines, an HTML comment, or only
characters the #177 strip deletes) does nothing at all: it leaves the reasoning
streaming around it alone (review round 2) and leaves the mark for the late end
it belongs to (review round 4). A Markdown block commits the same characters AND
styles as a one-delta stream and ``replay`` (a raw ``Text`` of the source passed
every row that compared ``.plain`` of one line of prose), and at the renderer's
width, not 80 columns (review round 4: every other row runs at 80,
``StreamRenderer``'s default).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.types import (
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from aelix_ai.messages import AssistantMessage, TextContent, ThinkingContent, ToolCallContent
from aelix_ai.providers._openai_responses_shared import (
    ResponsesStreamState,
    process_responses_stream,
)
from aelix_ai.providers.openai_completions import stream_openai_completions
from aelix_ai.streaming import (
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
)
from aelix_coding_agent.tui.render import EventRenderer
from rich.text import Text

_MODEL = Model(api="openai-responses", id="m", provider="p", base_url="http://127.0.0.1:9/v1")
_ANSWER = "먼저 서비스 계층부터 확인하겠습니다."
_CREATED = {"type": "response.created", "response": {"id": "r"}}
_COMPLETED = {"type": "response.completed", "response": {"id": "r", "status": "completed"}}


def _message(mid: str, text: str | None) -> dict[str, Any]:
    content = [] if text is None else [{"type": "output_text", "text": text, "annotations": []}]
    return {"type": "message", "id": mid, "role": "assistant", "content": content}


def _done(index: int, item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "response.output_item.done", "output_index": index, "item": item}


def _added(index: int, item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "response.output_item.added", "output_index": index, "item": item}


def _delta(index: int, text: str) -> dict[str, Any]:
    return {"type": "response.output_text.delta", "output_index": index, "delta": text}


def _read_call(index: int) -> list[dict[str, Any]]:
    item = {"type": "function_call", "call_id": "c1", "id": "fc_1", "name": "read"}
    return [
        _added(index, {**item, "arguments": ""}),
        _done(index, {**item, "arguments": '{"path": "a.py"}'}),
    ]


def _reasoning(text: str) -> dict[str, Any]:
    return {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": text}]}


async def _responses(wire: list[dict[str, Any]]) -> tuple[list[Any], AssistantMessage]:
    async def gen() -> AsyncIterator[dict[str, Any]]:
        for event in wire:
            yield event

    state = ResponsesStreamState()
    events = [ev async for ev in process_responses_stream(gen(), state, _MODEL)]
    return events, AssistantMessage(content=list(state.content), stop_reason=state.stop_reason)


class _Screen:
    """The renderer's two sinks, read back as plain text (and, in ``rendered``,
    as the committed renderables themselves)."""

    def __init__(self, width: int = 80) -> None:
        self.commits: list[str] = []
        self.rendered: list[Any] = []
        self.tails: list[str] = []
        self.renderer = EventRenderer(commit=self._commit, set_tail=self._tail, width=width)

    def _commit(self, renderable: Any) -> None:
        self.rendered.append(renderable)
        self.commits.append(
            renderable.plain.rstrip() if isinstance(renderable, Text) else repr(renderable)
        )

    def _tail(self, ansi: str) -> None:
        self.tails.append(Text.from_ansi(ansi).plain.rstrip())

    def message(self, events: list[Any], final: AssistantMessage) -> None:
        """One assistant message, the way ``loop.py`` emits it, then its tools."""

        r = self.renderer
        r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
        for ev in events:
            r.on_agent_event(MessageUpdateEvent(message=final, assistant_message_event=ev))
        r.on_agent_event(MessageEndEvent(message=final))
        for block in final.content:
            if isinstance(block, ToolCallContent):
                r.on_agent_event(
                    ToolExecutionStartEvent(
                        tool_call_id=block.tool_call_id, tool_name=block.tool_name, args=block.input
                    )
                )


def _replayed_renderables(*finals: AssistantMessage, width: int = 80) -> list[Any]:
    commits: list[Any] = []
    EventRenderer(commit=commits.append, set_tail=lambda _a: None, width=width).replay(list(finals))
    return commits


def _replayed(*finals: AssistantMessage) -> list[str]:
    return [c.plain.rstrip() for c in _replayed_renderables(*finals)]


def _styled(renderables: list[Any]) -> list[tuple[str, list[tuple[int, int, str]]]]:
    """Each committed ``Text`` as its characters AND its style spans: two commits
    with the same ``.plain`` can still differ in what the terminal shows."""

    assert all(isinstance(r, Text) for r in renderables), renderables
    return [(r.plain, [(s.start, s.end, str(s.style)) for s in r.spans]) for r in renderables]


# === the issue's table: three wire shapes ====================================


async def test_a_message_sent_only_in_output_item_done_is_drawn_above_its_tool_card() -> None:
    events, final = await _responses(
        [_CREATED, _done(0, _message("msg_1", _ANSWER)), *_read_call(1), _COMPLETED]
    )
    assert [e.type for e in events][:2] == ["text_start", "text_end"]  # no text_delta at all

    screen = _Screen()
    screen.message(events, final)

    # 529b997e committed ['● read(a.py)'] only.
    assert screen.commits == [_ANSWER, "● read(a.py)"]
    assert screen.commits == _replayed(final)


async def test_a_message_added_then_done_with_no_delta_is_drawn() -> None:
    events, final = await _responses(
        [
            _CREATED,
            _added(0, _message("msg_1", None)),
            _done(0, _message("msg_1", _ANSWER)),
            *_read_call(1),
            _COMPLETED,
        ]
    )
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == [_ANSWER, "● read(a.py)"]
    assert screen.commits == _replayed(final)


async def test_the_streamed_control_is_unchanged() -> None:
    """The issue's control row: deltas, as the ChatGPT Codex backend sends them."""

    events, final = await _responses(
        [
            _CREATED,
            _added(0, _message("msg_1", None)),
            _delta(0, "먼저 서비스 계층부터 "),
            _delta(0, "확인하겠습니다."),
            _done(0, _message("msg_1", _ANSWER)),
            *_read_call(1),
            _COMPLETED,
        ]
    )
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == [_ANSWER, "● read(a.py)"]
    assert screen.commits == _replayed(final)


# === the accumulator residue ==================================================


async def test_a_whole_block_leaves_nothing_in_front_of_the_next_blocks_live_tail() -> None:
    """529b997e: tail frames ['AAAABBBB', ''], commits ['BBBB']."""

    events, final = await _responses(
        [
            _CREATED,
            _done(0, _message("msg_1", "AAAA")),
            _added(1, _message("msg_2", None)),
            _delta(1, "BBBB"),
            _done(1, _message("msg_2", "BBBB")),
            _COMPLETED,
        ]
    )
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["AAAA", "BBBB"]
    assert screen.commits == _replayed(final)
    assert not [frame for frame in screen.tails if "AAAA" in frame], screen.tails
    assert "BBBB" in screen.tails


async def test_a_streamed_message_then_a_whole_one_are_both_drawn() -> None:
    """In order, the other way round: only a reasoning flush sets the "closed
    early" mark. Set at every close, the streamed message's own ``text_end``
    would leave it for the whole message after it, which then drew nothing:
    ``['AAAA']`` against ``replay``'s ``['AAAA', 'BBBB']`` (review round 3's
    verify; until round 4 only a bare-event row pinned this)."""

    events, final = await _responses(
        [
            _CREATED,
            _added(0, _message("msg_1", None)),
            _delta(0, "AAAA"),
            _done(0, _message("msg_1", "AAAA")),
            _done(1, _message("msg_2", "BBBB")),
            _COMPLETED,
        ]
    )
    assert [e.type for e in events] == [
        "text_start",
        "text_delta",
        "text_end",
        "text_start",
        "text_end",
    ]
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["AAAA", "BBBB"]
    assert screen.commits == _replayed(final)


# === reasoning around a whole block ==========================================


async def test_whole_reasoning_then_a_whole_answer_are_both_drawn_in_order() -> None:
    events, final = await _responses(
        [_CREATED, _done(0, _reasoning("REASON")), _done(1, _message("msg_1", _ANSWER)), _COMPLETED]
    )
    assert [e.type for e in events] == ["thinking_start", "thinking_end", "text_start", "text_end"]
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["REASON", _ANSWER]
    assert screen.commits == _replayed(final)


async def test_streaming_reasoning_is_committed_above_a_whole_answer_that_overtakes_it() -> None:
    """Out-of-order completion (the engine routes by output_index, pi #6009): the
    reasoning item is still streaming when a whole message item completes, and its
    own done arrives last. The answer may not land ABOVE the reasoning it followed,
    which is what committing it without flushing the reasoning first would do."""

    events, final = await _responses(
        [
            _CREATED,
            _added(0, {"type": "reasoning", "id": "rs_1", "summary": []}),
            {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "REASON"},
            _done(1, _message("msg_1", _ANSWER)),
            _done(0, _reasoning("REASON")),
            _COMPLETED,
        ]
    )
    assert [e.type for e in events] == [
        "thinking_start",
        "thinking_delta",
        "text_start",
        "text_end",
        "thinking_end",
    ]
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["REASON", _ANSWER]
    assert screen.commits == _replayed(final)


#: Whole blocks that the renderer draws as nothing at all: ``markdown_lines``
#: returns ``[]`` for each (an HTML comment and a link-reference definition are
#: non-blank and still render to nothing), so ``replay`` commits nothing for them.
#: The last four are one control or format character each: the RAW text renders
#: to a line, and only after the #177 strip (``_safe_prose``) to nothing, so they
#: are the rows a gate that measures the content before that strip fails.
_DRAWS_NOTHING = {
    "empty": "",
    "a space": " ",
    "two newlines": "\n\n",
    "an HTML comment": "<!-- x -->",
    "mixed": " \n\n<!-- x -->\n\n[ref]: http://example.invalid\n\t\n",
    "BEL": "\x07",
    "RLO": "‮",
    "ZWSP": "​",
    "a lone ESC": "\x1b",
}


@pytest.mark.parametrize("content", list(_DRAWS_NOTHING.values()), ids=list(_DRAWS_NOTHING))
async def test_a_whole_block_that_draws_nothing_does_not_cut_the_reasoning_around_it(
    content: str,
) -> None:
    """A whole block that renders to nothing draws nothing, and so must not commit
    the reasoning still streaming around it: the rest of that reasoning would then
    commit a second time, as the whole block, at its ``thinking_end``. a6135783
    tested only the empty string and committed ``['R1', 'R1 R2']`` for the others;
    529b997e and ``replay`` show ``['R1 R2']``. A gate on ``markdown_lines`` of
    the content before ``_safe_prose`` does the same for the control and format
    characters (review round 3)."""

    events, final = await _responses(
        [
            _CREATED,
            _added(0, {"type": "reasoning", "id": "rs_1", "summary": []}),
            {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "R1 "},
            _done(1, _message("msg_1", content)),
            {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "R2"},
            _done(0, _reasoning("R1 R2")),
            _COMPLETED,
        ]
    )
    assert [e.type for e in events] == [
        "thinking_start",
        "thinking_delta",
        "text_start",
        "text_end",
        "thinking_delta",
        "thinking_end",
    ]
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["R1 R2"]
    assert screen.commits == _replayed(final)


@pytest.mark.parametrize("content", list(_DRAWS_NOTHING.values()), ids=list(_DRAWS_NOTHING))
async def test_a_whole_block_that_draws_nothing_leaves_the_mark_to_the_late_end(
    content: str,
) -> None:
    """Review round 4 (the round-3 cross-review's E2 and E3): the reasoning item
    finishes while message 1 streams, so its flush closes message 1's stream
    early and sets the mark; message 2 arrives whole and renders to nothing;
    message 1's own end comes last. 76781f34 looked at the mark before the
    renders-to-nothing test, so message 2's end spent it and message 1's late end
    drew the answer a second time: ``['REASON', answer, answer]``. 529b997e and
    ``replay`` draw it once. An end that renders to nothing does nothing at all."""

    events, final = await _responses(
        [
            _CREATED,
            _added(0, {"type": "reasoning", "id": "rs_1", "summary": []}),
            {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "REASON"},
            _added(1, _message("msg_1", None)),
            _delta(1, _ANSWER),
            _done(0, _reasoning("REASON")),
            _done(2, _message("msg_2", content)),
            _done(1, _message("msg_1", _ANSWER)),
            _COMPLETED,
        ]
    )
    assert [(e.type, e.content_index) for e in events] == [
        ("thinking_start", 0),
        ("thinking_delta", 0),
        ("text_start", 1),
        ("text_delta", 1),
        ("thinking_end", 0),
        ("text_start", 2),
        ("text_end", 2),
        ("text_end", 1),
    ]
    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["REASON", _ANSWER]
    assert screen.commits == _replayed(final)


# === a whole block is rendered as Markdown, like a streamed one ===============

#: Blocks whose rendering is not their source: a raw ``Text`` of the content
#: shows ``**``, backticks and ``-`` bullets, and does not wrap the long line.
_MARKDOWN = {
    "a list": "Plan:\n\n- **read** `a.py`\n- fix the bug",
    "emphasis": "This is *really* **important**.",
    "a fenced code block": "Run:\n\n```python\nprint('hi')\n```",
    "a long line that wraps": " ".join(["word"] * 40),
}


@pytest.mark.parametrize("text", list(_MARKDOWN.values()), ids=list(_MARKDOWN))
async def test_a_whole_block_commits_what_a_one_delta_stream_and_replay_commit(text: str) -> None:
    """Characters AND styles: the whole block goes through the same Markdown frame
    as a stream, so its commit equals a one-delta stream's and ``replay``'s."""

    whole_events, whole_final = await _responses(
        [_CREATED, _done(0, _message("msg_1", text)), _COMPLETED]
    )
    assert "text_delta" not in [e.type for e in whole_events]
    one_delta_events, one_delta_final = await _responses(
        [
            _CREATED,
            _added(0, _message("msg_1", None)),
            _delta(0, text),
            _done(0, _message("msg_1", text)),
            _COMPLETED,
        ]
    )
    whole, one_delta = _Screen(), _Screen()
    whole.message(whole_events, whole_final)
    one_delta.message(one_delta_events, one_delta_final)

    assert len(whole.rendered) == 1
    assert whole.commits != [text]  # the rendering, not the source
    assert _styled(whole.rendered) == _styled(one_delta.rendered)
    assert _styled(whole.rendered) == _styled(_replayed_renderables(whole_final))


@pytest.mark.parametrize("width", [50, 120])
async def test_a_whole_block_is_laid_out_at_the_renderers_width(width: int) -> None:
    """Review round 4 (cross 3): every other row runs at 80 columns, which is
    also ``StreamRenderer``'s default width, so a whole block committed through a
    stream built without the renderer's width passed them all. Twenty ``word``
    are 99 cells: one row at 120, two at 80 (79 + 19 cells), two at 50 (49 + 49),
    so a layout at 80 is wrong at both widths."""

    text = " ".join(["word"] * 20)
    whole_events, whole_final = await _responses(
        [_CREATED, _done(0, _message("msg_1", text)), _COMPLETED]
    )
    one_delta_events, one_delta_final = await _responses(
        [
            _CREATED,
            _added(0, _message("msg_1", None)),
            _delta(0, text),
            _done(0, _message("msg_1", text)),
            _COMPLETED,
        ]
    )
    whole, one_delta, at_80 = _Screen(width), _Screen(width), _Screen(80)
    whole.message(whole_events, whole_final)
    one_delta.message(one_delta_events, one_delta_final)
    at_80.message(whole_events, whole_final)

    assert whole.commits != at_80.commits  # the row can tell the two widths apart
    assert [len(row.rstrip()) for row in whole.commits[0].split("\n")] == (
        [99] if width == 120 else [49, 49]
    )
    assert _styled(whole.rendered) == _styled(one_delta.rendered)
    assert _styled(whole.rendered) == _styled(_replayed_renderables(whole_final, width=width))


# === the end event of a block that is already in scrollback ==================


@dataclass
class _Delta:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: Any = None


@dataclass
class _Choice:
    delta: _Delta
    finish_reason: str | None = None


@dataclass
class _Chunk:
    id: str = "c"
    model: str = "m"
    choices: list[_Choice] = field(default_factory=list)
    usage: Any = None


class _Chunks:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = list(chunks)
        self.response = SimpleNamespace(status_code=200, headers={})

    def __aiter__(self) -> _Chunks:
        return self

    async def __anext__(self) -> _Chunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeOpenAI:
    """Just enough of ``AsyncOpenAI`` for ``chat.completions.with_raw_response``."""

    def __init__(self, chunks: list[_Chunk]) -> None:
        stream = _Chunks(chunks)

        async def create(**_kwargs: Any) -> Any:
            return SimpleNamespace(parse=lambda: stream, http_response=stream.response)

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )


async def test_an_openai_completions_answer_closed_by_its_reasoning_end_is_drawn_once() -> None:
    """The adapter sends its end events at end-of-stream in block order, so the
    reasoning's ``thinking_end`` (block 0) arrives while the answer (block 1) is
    still open: it runs ``_flush_thinking``, which closes the answer's stream
    first, and the answer's own ``text_end`` then finds no stream open. Green on
    529b997e too — this row is here so that committing at every stream-less
    ``text_end`` goes red."""

    model = Model(api="openai-completions", id="m", provider="p", base_url="http://127.0.0.1:9/v1")
    chunks = [
        _Chunk(choices=[_Choice(_Delta(reasoning_content="think "))]),
        _Chunk(choices=[_Choice(_Delta(reasoning_content="hard"))]),
        _Chunk(choices=[_Choice(_Delta(content="the "))]),
        _Chunk(choices=[_Choice(_Delta(content="answer"))]),
        _Chunk(choices=[_Choice(_Delta(), finish_reason="stop")]),
    ]
    out = [
        ev
        async for ev in stream_openai_completions(
            model, Context(), SimpleStreamOptions(api_key="fake", client=_FakeOpenAI(chunks))
        )
    ]
    events = [ev for ev in out if ev.type not in ("start", "done", "error")]
    final = next(ev.message for ev in out if ev.type == "done")
    assert [(e.type, getattr(e, "content_index", None)) for e in events][-2:] == [
        ("thinking_end", 0),
        ("text_end", 1),
    ]

    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["think hard", "the answer"]
    assert screen.commits == _replayed(final)


#: How an extension adapter that wraps the built-in openai-completions adapter
#: (as the shipped ``examples/selfhosted`` one wraps its ``stream_simple``) may
#: rewrite each ``text_delta``: rebuilt from the delta text alone, so its
#: ``content_index`` is the class default 0 while ``text_start`` and
#: ``text_end`` pass through with the real index 1.
_REBUILT_DELTA = {
    "passed on as is": lambda delta: delta,
    "redacted": lambda delta: delta.replace("secret", "******"),
}


@pytest.mark.parametrize("rewrite", list(_REBUILT_DELTA.values()), ids=list(_REBUILT_DELTA))
async def test_an_adapter_that_rebuilds_its_text_deltas_without_their_index_draws_once(
    rewrite: Any,
) -> None:
    """Review round 3 (cross F2): fb4abfd8 keyed the "closed early" mark on
    ``content_index``. The deltas opened the stream as block 0, the reasoning's
    ``thinking_end`` closed it and marked 0, and the answer's own ``text_end``
    came as block 1, found no mark and drew the answer a second time from its
    copy (``['think hard', 'the ****** answer', 'the secret answer']``).
    529b997e drew it once. The mark reads no index now."""

    model = Model(api="openai-completions", id="m", provider="p", base_url="http://127.0.0.1:9/v1")
    chunks = [
        _Chunk(choices=[_Choice(_Delta(reasoning_content="think "))]),
        _Chunk(choices=[_Choice(_Delta(reasoning_content="hard"))]),
        _Chunk(choices=[_Choice(_Delta(content="the secret "))]),
        _Chunk(choices=[_Choice(_Delta(content="answer"))]),
        _Chunk(choices=[_Choice(_Delta(), finish_reason="stop")]),
    ]

    async def adapter() -> AsyncIterator[Any]:
        async for ev in stream_openai_completions(
            model, Context(), SimpleStreamOptions(api_key="fake", client=_FakeOpenAI(chunks))
        ):
            if ev.type == "text_delta":
                yield TextDeltaEvent(delta=rewrite(ev.delta), partial=ev.partial)
            else:
                yield ev

    out = [ev async for ev in adapter()]
    events = [ev for ev in out if ev.type not in ("start", "done", "error")]
    final = next(ev.message for ev in out if ev.type == "done")
    assert [(e.type, e.content_index) for e in events if e.type.startswith("text")] == [
        ("text_start", 1),
        ("text_delta", 0),
        ("text_delta", 0),
        ("text_end", 1),
    ]

    screen = _Screen()
    screen.message(events, final)

    assert screen.commits == ["think hard", rewrite("the secret answer")]


def test_deltas_at_index_0_under_a_text_start_at_1_draw_the_answer_once() -> None:
    """The same shape without the adapter: ``text_start`` and ``text_end`` say
    block 1, the deltas between them say 0, and the reasoning's ``thinking_end``
    comes between the last delta and the answer's end."""

    screen = _Screen()
    r = screen.renderer
    r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
    for ev in (
        ThinkingDeltaEvent(delta="think", content_index=0),
        TextStartEvent(content_index=1),
        TextDeltaEvent(delta="the answer"),
        ThinkingEndEvent(content="think", content_index=0),
        TextEndEvent(content="the answer", content_index=1),
    ):
        r.on_agent_event(MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=ev))

    assert screen.commits == ["think", "the answer"]


def test_a_late_end_for_a_block_already_drawn_leaves_nothing_for_the_next_block() -> None:
    """The same end-of-stream ordering from an adapter with two text blocks: the
    late ``text_end`` of the block a flush already committed must not park its
    copy where the next block's stream would start from (the #285 residue)."""

    screen = _Screen()
    r = screen.renderer
    r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
    for ev in (
        ThinkingDeltaEvent(delta="R", content_index=0),
        TextStartEvent(content_index=1),
        TextDeltaEvent(delta="first-block", content_index=1),
        ThinkingEndEvent(content="R", content_index=0),
        TextEndEvent(content="first-block", content_index=1),
        TextStartEvent(content_index=2),
        TextDeltaEvent(delta="second-block", content_index=2),
        TextEndEvent(content="second-block", content_index=2),
    ):
        r.on_agent_event(MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=ev))

    assert screen.commits == ["R", "first-block", "second-block"]
    assert not [f for f in screen.tails if "first-block" in f and "second-block" in f], screen.tails


async def test_an_abort_after_the_flush_leaves_no_mark_on_the_next_message() -> None:
    """The reasoning's ``thinking_end`` closed the answer early (openai-completions'
    order) and the turn was aborted before the answer's own ``text_end``: the
    mark is still set when the message ends. It may not outlive that message, or
    the next message's first whole block is taken for the late end and dropped."""

    screen = _Screen()
    r = screen.renderer
    r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
    for ev in (
        ThinkingDeltaEvent(delta="R", content_index=0),
        TextDeltaEvent(delta="partial answer", content_index=1),
        ThinkingEndEvent(content="R", content_index=0),
    ):
        r.on_agent_event(MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=ev))
    # Esc: the loop emits only turn_end for an abort (no message_end, no text_end).
    r.on_agent_event(TurnEndEvent(message=AssistantMessage(stop_reason="aborted"), tool_results=[]))
    events, final = await _responses(
        [_CREATED, _done(0, _message("msg_2", "whole second")), _COMPLETED]
    )
    screen.message(events, final)

    assert screen.commits == ["R", "partial answer", "✖ Operation aborted", "whole second"]


def test_an_adapter_that_sends_every_block_as_index_0_gets_each_one_drawn() -> None:
    """``content_index`` defaults to 0 on every event class, so an extension adapter
    that never sets it sends a streamed block and then a whole one under the same
    index. The streamed block's own ``text_end`` closed it, which leaves no mark
    (only a reasoning flush sets one), so the whole block after it is drawn."""

    screen = _Screen()
    r = screen.renderer
    r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
    for ev in (
        TextStartEvent(),
        TextDeltaEvent(delta="streamed block"),
        TextEndEvent(content="streamed block"),
        TextStartEvent(),
        TextEndEvent(content="whole block"),
    ):
        r.on_agent_event(MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=ev))

    assert screen.commits == ["streamed block", "whole block"]


def test_a_late_end_spends_its_mark_so_a_later_whole_block_is_drawn() -> None:
    """The same index-0 adapter with openai-completions' end order: the late
    ``text_end`` of the block the reasoning flush closed draws nothing and uses
    up the mark, so a whole block after it is drawn."""

    screen = _Screen()
    r = screen.renderer
    r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
    for ev in (
        ThinkingDeltaEvent(delta="R"),
        TextDeltaEvent(delta="first"),
        ThinkingEndEvent(content="R"),
        TextEndEvent(content="first"),
        TextStartEvent(),
        TextEndEvent(content="second"),
    ):
        r.on_agent_event(MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=ev))

    assert screen.commits == ["R", "first", "second"]


@pytest.mark.parametrize(
    "blank",
    ["", " ", "\x07", "<!-- x -->", "[r]: http://x.invalid"],
    ids=["empty", "a space", "BEL", "an HTML comment", "a link reference definition"],
)
def test_a_stream_that_drew_nothing_leaves_no_mark_for_the_next_whole_block(
    blank: str,
) -> None:
    """Review round 4: openai-completions' end order from an adapter with two
    text blocks, the first of which streamed only a text that renders to
    nothing. The reasoning's ``thinking_end`` closes that stream, which drew
    nothing; the block's own end renders to nothing and does nothing; so a
    mark set by that flush would wait for the next stream-less end, the second
    block arriving whole, and drop it (``['R']``, as on 529b997e, which drew no
    whole block). 76781f34 drew it, and so does ``replay``: a stream that drew
    nothing leaves no mark. The last two are not blank and still render to
    nothing, so "drew something" must be the renderer's test, not
    ``.strip()``: that form passed every other row."""

    final = AssistantMessage(
        content=[
            ThinkingContent(thinking="R"),
            TextContent(text=blank),
            TextContent(text="second"),
        ]
    )
    screen = _Screen()
    r = screen.renderer
    r.on_agent_event(MessageStartEvent(message=AssistantMessage(content=[])))
    for ev in (
        ThinkingDeltaEvent(delta="R", content_index=0),
        TextStartEvent(content_index=1),
        TextDeltaEvent(delta=blank, content_index=1),
        TextStartEvent(content_index=2),
        ThinkingEndEvent(content="R", content_index=0),
        TextEndEvent(content=blank, content_index=1),
        TextEndEvent(content="second", content_index=2),
    ):
        r.on_agent_event(MessageUpdateEvent(message=final, assistant_message_event=ev))

    assert screen.commits == ["R", "second"]
    assert screen.commits == _replayed(final)


# === through the real run_tui, onto the glass ================================


async def test_a_whole_answer_reaches_the_real_chrome_scrollback() -> None:
    """The renderer wired by ``run_tui`` to the real chrome and its print-above
    pump, read back through a terminal emulator: the block that never streamed
    is on the grid. On 529b997e this waits out the harness bound and fails."""

    from _pyte import render_shell_to_screen  # sibling helper
    from test_run_tui_smoke import FakeHarness, FakeRuntime  # sibling module

    anchor = "whole-answer-285"
    runtime = FakeRuntime(FakeHarness())

    async def drive(_chrome: Any) -> None:
        listener = runtime.harness.subscribers[0]
        final = AssistantMessage(content=[])
        listener(MessageStartEvent(message=final))  # type: ignore[operator]
        for ev in (
            TextStartEvent(content_index=0),
            TextEndEvent(content=f"The {anchor} arrived in one piece.", content_index=0),
        ):
            listener(MessageUpdateEvent(message=final, assistant_message_event=ev))  # type: ignore[operator]

    grid = await render_shell_to_screen(
        runtime=runtime,
        rows=24,
        cols=80,
        drive=drive,
        settled=lambda display: any(anchor in row for row in display),
        settled_what=f"the whole answer ({anchor!r}) to reach the pyte grid",
        include_history=True,
    )
    assert sum(anchor in row for row in grid) == 1
