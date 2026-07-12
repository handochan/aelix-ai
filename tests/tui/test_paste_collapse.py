"""Issue #81 — large bracketed-paste collapse (Claude-Code-inspired).

A large paste is replaced in the editor by a ``[Pasted text #N +M lines]``
placeholder held in a per-session registry; on submit the placeholder is
re-expanded so the model always receives the full text (only the input box is
compressed). An immediately-repeated identical paste reveals the raw text.

The bindings are driven directly (a fake ``event`` carrying ``.data`` +
``.current_buffer`` for the paste; the accept/follow-up handlers read
``chrome.buffer``) — no Application.run / TTY.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

from aelix_coding_agent.tui.chrome import (
    AelixChrome,
    _paste_line_count,
    _paste_placeholder,
    _should_collapse_paste,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


def _make_chrome() -> AelixChrome:
    console = Console(file=io.StringIO(), force_terminal=True, width=80)
    return AelixChrome(console=console)


def _handler(chrome: AelixChrome, keys: tuple[str, ...]) -> Any:
    kb = chrome.app.key_bindings
    assert kb is not None
    for binding in kb.bindings:
        got = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if got == keys:
            return binding.handler
    raise AssertionError(f"no binding for {keys}")


def _paste(chrome: AelixChrome, data: str) -> None:
    handler = _handler(chrome, ("<bracketed-paste>",))
    handler(SimpleNamespace(data=data, current_buffer=chrome.buffer))


def _submit(chrome: AelixChrome) -> None:
    chrome.buffer.cursor_position = len(chrome.buffer.text)
    _handler(chrome, ("c-m",))(SimpleNamespace())


def _big(n: int = 36) -> str:
    return "\n".join(f"line{i}" for i in range(n))


# === module-level helpers ===================================================


def test_should_collapse_thresholds() -> None:
    assert _should_collapse_paste(_big(6)) is True  # line-count trigger
    assert _should_collapse_paste("\n".join(["x"] * 5)) is False  # 5 lines
    assert _should_collapse_paste("a" * 1000) is True  # char-count trigger
    assert _should_collapse_paste("short") is False
    assert _should_collapse_paste("") is False


def test_paste_line_count() -> None:
    assert _paste_line_count("a\nb\nc") == 3
    assert _paste_line_count("a\nb\nc\n") == 3  # trailing newline dropped
    assert _paste_line_count("solo") == 1
    assert _paste_line_count("") == 0


def test_paste_placeholder_format() -> None:
    assert _paste_placeholder(4, _big(36)) == "[Pasted text #4 +36 lines]"


# === collapse behaviour =====================================================


def test_large_paste_collapses_to_placeholder() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        _paste(chrome, _big(36))
        assert chrome.buffer.text == "[Pasted text #1 +36 lines]"


def test_small_paste_inserts_raw() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        _paste(chrome, "just a short line")
        assert chrome.buffer.text == "just a short line"


def test_submit_expands_placeholder_to_full_text() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == big
        assert chrome.buffer.text == ""  # editor cleared on submit


def test_mixed_text_and_placeholder_expands_only_placeholder() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(10)
        _paste(chrome, big)
        chrome.buffer.cursor_position = len(chrome.buffer.text)
        chrome.buffer.insert_text(" please review")
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == big + " please review"


def test_crlf_normalized_before_collapse_and_expand() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        _paste(chrome, "a\r\nb\r\nc\r\nd\r\ne\r\nf\r\ng")  # 7 CRLF lines
        assert chrome.buffer.text == "[Pasted text #1 +7 lines]"
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == "a\nb\nc\nd\ne\nf\ng"


def test_repeat_identical_paste_reveals_raw() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)
        assert chrome.buffer.text.startswith("[Pasted text #1")
        _paste(chrome, big)  # identical, consecutive → reveal REPLACES placeholder
        assert chrome.buffer.text == big  # placeholder gone, only raw remains
        assert chrome._paste_registry == {}  # its registry entry popped


def test_reveal_then_submit_sends_single_copy() -> None:
    # Issue #81 review (HIGH): reveal must REPLACE the placeholder, so submit
    # after a reveal sends the content exactly ONCE (not doubled).
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)  # collapse
        _paste(chrome, big)  # reveal
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == big  # exactly one copy


def test_three_consecutive_pastes_real_flow() -> None:
    # Issue #81 review (NIT): the true accumulate-in-buffer flow, no manual reset.
    # collapse → reveal (replace) → collapse again ⇒ raw + one placeholder; submit
    # round-trips to exactly what is displayed (two copies).
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)  # buffer = [#1]
        _paste(chrome, big)  # reveal → buffer = big
        _paste(chrome, big)  # big != last_raw(None) → collapse → buffer = big+[#2]
        assert chrome.buffer.text == big + "[Pasted text #2 +36 lines]"
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == big + big


def test_get_editor_text_expands_placeholder() -> None:
    # Issue #81 review (MEDIUM): the external-editor / dequeue snapshot must see
    # the REAL content, not the opaque placeholder token.
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)
        assert chrome.buffer.text.startswith("[Pasted")  # editor shows placeholder
        assert chrome.get_editor_text() == big  # snapshot sees the full content


def test_set_editor_text_clears_stale_registry() -> None:
    # After an external-editor round-trip, the whole buffer is replaced with
    # (already-expanded) text, so the paste registry must be dropped — no stale
    # entry can re-expand at submit.
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        _paste(chrome, _big(36))
        assert chrome._paste_registry
        chrome.set_editor_text("edited full content")
        assert chrome._paste_registry == {}
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == "edited full content"


def test_multi_placeholder_both_expand() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        a, b = _big(10), _big(20)
        _paste(chrome, a)
        chrome.buffer.cursor_position = len(chrome.buffer.text)
        chrome.buffer.insert_text(" and ")
        _paste(chrome, b)
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == a + " and " + b


def test_nested_placeholder_is_not_double_expanded() -> None:
    # Issue #81 review: _expand_pastes is a single pass, so a pasted blob that
    # literally contains ANOTHER live placeholder token is not re-expanded.
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        ph_b = chrome._register_paste("BBB")
        ph_a = chrome._register_paste(f"AAA {ph_b} AAA")
        out = chrome._expand_pastes(ph_a)
        assert out == f"AAA {ph_b} AAA"  # ph_b kept literal, not turned into BBB


def test_eviction_preserves_live_placeholder() -> None:
    from aelix_coding_agent.tui import chrome as chrome_mod

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        live = chrome._register_paste(_big(36) + "#live")
        chrome.buffer.text = live  # the token is in the live buffer
        for i in range(chrome_mod._PASTE_REGISTRY_MAX + 30):
            chrome._register_paste(_big(36) + f"#{i}")  # not in buffer → evictable
        # The live token survives eviction (never stranded → never leaks/loses).
        assert live in chrome._paste_registry
        assert chrome._expand_pastes(live).endswith("#live")


def test_history_stores_expanded_text() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)
        _submit(chrome)
        # Up-arrow recall must yield the full content, not the placeholder.
        assert big in list(chrome.buffer.history.get_strings())[-1]


def test_counter_is_monotonic_across_submits() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        _paste(chrome, _big(36))
        _submit(chrome)
        chrome._input_queue.get_nowait()
        _paste(chrome, _big(20))
        # #N keeps climbing across a submit (registry cleared, counter is not).
        assert chrome.buffer.text == "[Pasted text #2 +20 lines]"


def test_ctrl_c_clear_resets_paste_registry() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        _paste(chrome, _big(36))
        assert chrome._paste_registry  # armed
        _handler(chrome, ("c-c",))(SimpleNamespace())  # idle, non-empty buffer → clear
        assert chrome.buffer.text == ""
        assert chrome._paste_registry == {}
        assert chrome._last_pasted_raw is None


def test_steer_path_expands_placeholder() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        steered: list[str] = []
        chrome.on_steer = steered.append
        chrome.set_running(True)  # mid-turn → Enter steers
        big = _big(36)
        _paste(chrome, big)
        _submit(chrome)
        assert steered == [big]


def test_follow_up_path_expands_placeholder() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        followed: list[str] = []
        chrome.on_follow_up = followed.append
        chrome.set_running(True)
        big = _big(36)
        _paste(chrome, big)
        chrome.buffer.cursor_position = len(chrome.buffer.text)
        _handler(chrome, ("escape", "c-m"))(SimpleNamespace())  # Alt+Enter
        assert followed == [big]


def test_long_single_line_collapse_and_expand_end_to_end() -> None:
    # The char-count trigger driven end to end: a single 1000+ char line (1 line)
    # collapses and round-trips to the full text on submit.
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        blob = "x" * 1500  # one line, over _PASTE_COLLAPSE_MIN_CHARS
        _paste(chrome, blob)
        assert chrome.buffer.text == "[Pasted text #1 +1 lines]"
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == blob


def test_hand_typed_live_placeholder_expands_documented() -> None:
    # Documented edge (Issue #81 review): a string the user types that is IDENTICAL
    # to a currently-registered placeholder is expanded at submit. This is
    # near-impossible in practice (#N is monotonic and the registry is cleared on
    # submit), so it is accepted; this test locks the behavior.
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        big = _big(36)
        _paste(chrome, big)  # registers [Pasted text #1 +36 lines]
        # User clears and hand-types the very same token, then submits.
        chrome.buffer.text = "[Pasted text #1 +36 lines]"
        chrome.buffer.cursor_position = len(chrome.buffer.text)
        _submit(chrome)
        assert chrome._input_queue.get_nowait() == big


def test_registry_is_bounded() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        from aelix_coding_agent.tui import chrome as chrome_mod

        for i in range(chrome_mod._PASTE_REGISTRY_MAX + 25):
            chrome._register_paste(_big(36) + f"#{i}")
        assert len(chrome._paste_registry) <= chrome_mod._PASTE_REGISTRY_MAX
