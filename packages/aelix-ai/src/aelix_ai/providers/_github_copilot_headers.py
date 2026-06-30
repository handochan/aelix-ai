"""GitHub Copilot dynamic per-request headers — pi parity (#15).

Pi parity: ``packages/ai/src/api/github-copilot-headers.ts`` at SHA
``927e98068cda276bf9188f4774fb927c89823388``.

These are the **dynamic** (per-request) headers the OpenAI **Responses**
adapter stamps onto a github-copilot request. They are distinct from the
**static** :data:`aelix_ai.oauth.github_copilot.COPILOT_HEADERS`
(``User-Agent`` / ``Editor-Version`` / …) baked into the OAuth flow — that
module owns login + token exchange + ``base_url`` injection and never
touched ``X-Initiator`` / ``Openai-Intent`` / ``Copilot-Vision-Request``,
so this thin port closes that gap without duplication.

- ``X-Initiator`` — ``"agent"`` when the conversation's last message is not
  a plain user turn (a follow-up after an assistant/tool message),
  ``"user"`` otherwise. Copilot uses it to distinguish user-initiated vs.
  agent-initiated requests.
- ``Openai-Intent`` — constant ``"conversation-edits"``.
- ``Copilot-Vision-Request`` — ``"true"`` only when the request carries an
  image (gated by :func:`has_copilot_vision_input`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aelix_ai.messages import Message


def infer_copilot_initiator(messages: Sequence[Message]) -> str:
    """Pi parity: ``inferCopilotInitiator`` (github-copilot-headers.ts:5-8).

    ``"agent"`` when the last message exists and is not a ``user`` turn;
    ``"user"`` otherwise (incl. the empty-history case).
    """

    if not messages:
        return "user"
    last = messages[-1]
    return "agent" if getattr(last, "role", None) != "user" else "user"


def has_copilot_vision_input(messages: Sequence[Message]) -> bool:
    """Pi parity: ``hasCopilotVisionInput`` (github-copilot-headers.ts:11-21).

    ``True`` when any ``user`` or ``toolResult`` message carries an image
    content block.
    """

    for msg in messages:
        role = getattr(msg, "role", None)
        if role not in ("user", "toolResult"):
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if getattr(block, "type", None) == "image":
                return True
    return False


def build_copilot_dynamic_headers(
    messages: Sequence[Message], has_images: bool
) -> dict[str, str]:
    """Pi parity: ``buildCopilotDynamicHeaders`` (github-copilot-headers.ts:23-36).

    Returns ``X-Initiator`` + ``Openai-Intent`` always, plus
    ``Copilot-Vision-Request`` when ``has_images``.
    """

    headers: dict[str, str] = {
        "X-Initiator": infer_copilot_initiator(messages),
        "Openai-Intent": "conversation-edits",
    }
    if has_images:
        headers["Copilot-Vision-Request"] = "true"
    return headers


__all__ = [
    "build_copilot_dynamic_headers",
    "has_copilot_vision_input",
    "infer_copilot_initiator",
]
