"""A persisted CONTENT BLOCK must come back carrying everything it was written with.

#135 / ADR-0211. ``_message_to_dict`` is ``dataclasses.asdict``, so every field
reaches the JSONL. ``_message_from_dict`` rebuilt the blocks field-by-field and
read back only some of them, so the values sat on disk and died at the reload
boundary. ADR-0210 closed the message-level scalars; every loss left was inside
a content block:

- ``ImageContent.mime_type`` / ``.data`` — the pi-canonical pair, and the only
  ones the Read tool populates (``tools/read.py`` passes ``source=""``). Reading
  ``source`` alone made a resumed session re-send an image with an empty base64
  payload, which the provider rejects with HTTP 400 rather than degrading.
- ``ThinkingContent`` as a whole — no ``"thinking"`` branch existed, so the block
  returned as a raw dict and ADR-0190's signed-thinking replay was dead on resume.
- ``ToolCallContent.thought_signature`` — Gemini's multi-turn signature, which the
  adapter must echo back verbatim.
- ``TextContent.text_signature`` — the OpenAI Responses ``TextSignatureV1`` replay
  payload, and Gemini's per-text-part signature.

Nothing was ever lost on disk, so these tests are also the evidence that existing
session files become correct the moment the reader is fixed.

The round trip under test goes through the REAL storage layer wherever it can. A
test that hand-builds a wire dict and calls ``_message_from_dict`` on it can
invent a shape ``asdict`` never writes, and would prove nothing.
"""

from __future__ import annotations

import base64
import dataclasses
from pathlib import Path

from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
from aelix_agent_core.session.entries import MessageEntry
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

_TS = "2026-08-10T10:00:00.000Z"

# A genuine 1x1 PNG, base64 exactly as the Read tool stores it.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
# Gemini hands back opaque TYPE_BYTES; ``_normalize_signature`` re-encodes it to
# the canonical base64 STR that aelix stores and replays. Derive the fixture the
# same way rather than hand-typing one, so it is genuinely valid base64 (the
# adapter's gate also requires length % 4 == 0).
_THOUGHT_SIG_BYTES = b"gemini thought signature payload"
_THOUGHT_SIG = base64.b64encode(_THOUGHT_SIG_BYTES).decode("ascii")


async def _roundtrip_through_real_storage(
    tmp_path: Path, messages: list[object]
) -> list[object]:
    """Write messages to a real JSONL file, then open THAT FILE and read back."""

    fs = LocalFileSystem()
    path = str(tmp_path / "session.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, path, cwd="/repo", session_id="s-135"
    )
    previous: str | None = None
    for i, message in enumerate(messages):
        entry = MessageEntry(
            id=f"e{i}",
            parent_id=previous,
            timestamp=_TS,
            message=message,  # type: ignore[arg-type]
        )
        await storage.append_entry(entry)
        previous = entry.id

    reopened = await JsonlSessionStorage.open(fs, path)
    # get_entries is a COROUTINE; iterating it without awaiting raises.
    entries = await reopened.get_entries()
    return [e.message for e in entries]  # type: ignore[attr-defined]


async def test_image_data_survives_real_storage(tmp_path: Path) -> None:
    """The headline loss: a real PNG must come back byte-identical."""

    original = UserMessage(
        content=[
            TextContent(text="what is this?"),
            ImageContent(mime_type="image/png", data=_PNG_B64),
        ]
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    block = restored.content[1]
    assert isinstance(block, ImageContent)
    assert block.mime_type == "image/png"
    assert block.data == _PNG_B64
    # Not merely equal-length or truthy: identical to what was written.
    assert block.data == original.content[1].data


async def test_thinking_block_returns_typed_not_a_dict(tmp_path: Path) -> None:
    """The ADR-0190 linkage: the restored block must satisfy isinstance.

    ``_transform_messages.py:177`` tests ``isinstance(block, ThinkingContent)``.
    A raw dict — what this used to restore as — never matches, so the signed
    block was silently downgraded to plain text on every resumed session.
    """

    original = AssistantMessage(
        content=[
            TextContent(text="answer", text_signature='{"v":1,"id":"msg_1"}'),
            ThinkingContent(
                thinking="step by step",
                thinking_signature="ErEECpMBCA8YAipAh0Fg",
                redacted=False,
            ),
        ],
        provider="anthropic",
        model="claude-haiku-4-5",
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    text, thinking = restored.content
    assert isinstance(thinking, ThinkingContent), (
        f"restored as {type(thinking).__name__}, not ThinkingContent"
    )
    assert not isinstance(thinking, dict)
    assert thinking.thinking == "step by step"
    assert thinking.thinking_signature == "ErEECpMBCA8YAipAh0Fg"
    assert thinking.redacted is False

    assert isinstance(text, TextContent)
    assert text.text_signature == '{"v":1,"id":"msg_1"}'


async def test_thought_signature_survives_and_stays_str(tmp_path: Path) -> None:
    """Gemini landmine: thoughtSignature is bytes-vs-str sensitive.

    ``_google_shared._normalize_signature`` re-encodes the SDK's bytes to a
    canonical base64 ``str`` at ingest, so storage is always ``str``. A round
    trip must not change that type — a ``None`` or ``bytes`` here is dropped by
    ``is_valid_thought_signature`` without raising, so the type IS the contract.
    """

    original = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="call_1",
                tool_name="read",
                input={"path": "a.png"},
                thought_signature=_THOUGHT_SIG,
            )
        ]
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    call = restored.content[0]
    assert isinstance(call, ToolCallContent)
    assert call.thought_signature == _THOUGHT_SIG
    assert type(call.thought_signature) is str
    assert call.input == {"path": "a.png"}

    # The end it serves: the adapter's own validity gate must accept it.
    # Before the fix this was '' — falsy — so resolve_thought_signature dropped
    # every Gemini signature on resume without anything raising.
    from aelix_ai.providers._google_shared import (
        _normalize_signature,
        is_valid_thought_signature,
        resolve_thought_signature,
    )

    assert is_valid_thought_signature(call.thought_signature) is True
    assert resolve_thought_signature(True, call.thought_signature) == _THOUGHT_SIG

    # The ingest seam this fixture imitates: SDK bytes normalise to exactly the
    # str that was persisted, so the round trip closes the loop without a
    # bytes/str change anywhere along it.
    assert _normalize_signature(_THOUGHT_SIG_BYTES) == call.thought_signature


async def test_tool_result_image_survives_real_storage(tmp_path: Path) -> None:
    """The Read tool's own path: an image arrives as a toolResult."""

    original = ToolResultMessage(
        tool_call_id="call_1",
        content=[ImageContent(mime_type="image/png", data=_PNG_B64)],
        tool_name="read",
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    block = restored.content[0]
    assert isinstance(block, ImageContent)
    assert block.data == _PNG_B64


async def test_legacy_source_only_image_still_loads(tmp_path: Path) -> None:
    """Back-compat: a Sprint 6a block has no mime_type/data at all."""

    original = UserMessage(
        content=[ImageContent(source="data:image/png;base64," + _PNG_B64)]
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    block = restored.content[0]
    assert isinstance(block, ImageContent)
    assert block.source == "data:image/png;base64," + _PNG_B64
    # Absent fields default to "", not None — adapters branch on truthiness.
    assert block.mime_type == ""
    assert block.data == ""
    assert type(block.mime_type) is str


async def test_plain_assistant_message_unchanged(tmp_path: Path) -> None:
    """The common case carries none of these fields and must not regress."""

    original = AssistantMessage(
        content=[TextContent(text="hello")],
        stop_reason="end_turn",
        usage={"input": 10, "output": 5},
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    assert restored.content == [TextContent(text="hello")]
    assert restored.stop_reason == "end_turn"
    assert restored.usage == {"input": 10, "output": 5}
    assert restored.content[0].text_signature == ""


async def test_restored_image_reaches_the_provider_with_its_payload(
    tmp_path: Path,
) -> None:
    """End to end: the adapter must emit real base64, not ''.

    This is the assertion that guards the live failure. Before the fix the same
    call emitted ``data: ''`` and the real Anthropic API answered
    ``invalid_request_error: messages.0.content.1.image.source.base64: image
    cannot be empty`` — a hard 400 on the next turn of any resumed session that
    contained an image, not a quality degradation.
    """

    from aelix_ai.providers._anthropic_transforms import (
        _content_blocks_to_anthropic,
    )

    original = ToolResultMessage(
        tool_call_id="call_1",
        content=[ImageContent(mime_type="image/png", data=_PNG_B64)],
        tool_name="read",
    )
    (restored,) = await _roundtrip_through_real_storage(tmp_path, [original])

    wire = _content_blocks_to_anthropic(list(restored.content))
    image = [b for b in wire if b.get("type") == "image"][0]
    assert image["source"]["data"] == _PNG_B64
    assert image["source"]["data"] != ""
    assert image["source"]["media_type"] == "image/png"


# === Anti-drift =========================================================
#
# pi cannot lose a content field because it never decodes one
# (``jsonl-storage.ts`` casts the parsed JSON straight to the entry type).
# aelix must decode, to get real dataclasses for the isinstance-based
# downstream contract. This test buys back pi's structural property: it walks
# ``dataclasses.fields()``, so it fails the day someone adds a field to a
# content dataclass and forgets to read it back.


def _fill(cls: type) -> object:
    """Build an instance with a unique sentinel in EVERY field."""

    kwargs: dict[str, object] = {}
    for f in dataclasses.fields(cls):
        if f.name == "type":
            continue
        annotation = str(f.type)
        if "bool" in annotation:
            kwargs[f.name] = True
        elif "dict" in annotation:
            kwargs[f.name] = {"sentinel_key": "sentinel_value"}
        else:
            kwargs[f.name] = f"{cls.__name__}:{f.name}"
    return cls(**kwargs)


async def test_every_content_field_survives_a_roundtrip(tmp_path: Path) -> None:
    """No field of any content dataclass may be dropped by the reader."""

    user = UserMessage(content=[_fill(TextContent), _fill(ImageContent)])  # type: ignore[list-item]
    assistant = AssistantMessage(
        content=[  # type: ignore[list-item]
            _fill(TextContent),
            _fill(ThinkingContent),
            _fill(ToolCallContent),
        ]
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_1",
        content=[_fill(TextContent), _fill(ImageContent)],  # type: ignore[list-item]
    )

    restored = await _roundtrip_through_real_storage(
        tmp_path, [user, assistant, tool_result]
    )

    losses: list[str] = []
    for original_msg, restored_msg in zip(
        [user, assistant, tool_result], restored, strict=True
    ):
        for i, block in enumerate(original_msg.content):
            back = restored_msg.content[i]
            if not dataclasses.is_dataclass(back):
                losses.append(
                    f"{type(original_msg).__name__}.content[{i}]: "
                    f"{type(block).__name__} restored as {type(back).__name__}"
                )
                continue
            for f in dataclasses.fields(block):
                written = getattr(block, f.name)
                read = getattr(back, f.name, "<MISSING>")
                if written != read:
                    losses.append(
                        f"{type(original_msg).__name__}.content[{i}].{f.name}: "
                        f"wrote {written!r}, read {read!r}"
                    )

    assert not losses, "content fields lost on reload:\n  " + "\n  ".join(losses)


async def test_unknown_future_block_passes_through(tmp_path: Path) -> None:
    """Forward compat: a block only a NEWER aelix writes must not crash us."""

    from aelix_agent_core.session.entries import _message_from_dict

    restored = _message_from_dict(
        {
            "role": "assistant",
            "content": [{"type": "futureBlock", "x": 1}],
        }
    )
    assert restored.content == [{"type": "futureBlock", "x": 1}]


def test_unknown_role_still_raises() -> None:
    """Do not soften the role check into a silent passthrough."""

    import pytest
    from aelix_agent_core.session.entries import _message_from_dict

    with pytest.raises(ValueError, match="unsupported message role on wire"):
        _message_from_dict({"role": "bogus", "content": []})


def test_explicit_nulls_never_produce_none() -> None:
    """Null-hostile: a JSON null must degrade to '' / {}, never None.

    A hand-edited transcript or a non-aelix writer can emit ``null`` where a
    string is declared. ``block.get(key, "")`` returns ``None`` there — the
    default only fires when the key is ABSENT. Nothing would crash:
    ``is_valid_thought_signature(None)`` is simply False, so the signature is
    dropped exactly as before and the bug looks fixed while replay stays broken.
    Assert the TYPE, because ``== ""`` would not catch it either.
    """

    from aelix_agent_core.session.entries import _message_from_dict

    assistant = _message_from_dict(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": None, "text_signature": None},
                {
                    "type": "thinking",
                    "thinking": None,
                    "thinking_signature": None,
                    "redacted": None,
                },
                {
                    "type": "toolCall",
                    "tool_call_id": None,
                    "tool_name": None,
                    "input": None,
                    "thought_signature": None,
                },
            ],
        }
    )
    text, thinking, call = assistant.content
    assert type(text.text) is str and text.text == ""
    assert type(text.text_signature) is str
    assert type(thinking.thinking) is str
    assert type(thinking.thinking_signature) is str
    assert thinking.redacted is False
    assert type(call.thought_signature) is str
    assert type(call.input) is dict and call.input == {}

    user = _message_from_dict(
        {
            "role": "user",
            "content": [
                {"type": "image", "source": None, "mime_type": None, "data": None}
            ],
        }
    )
    image = user.content[0]
    assert type(image.source) is str
    assert type(image.mime_type) is str
    assert type(image.data) is str
