"""Pi parity: ``cli/initial-message.ts`` (43 LOC).

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-388).

Two parity hazards mirrored exactly:

1. **``.shift()`` SIDE EFFECT** — Pi pops the first element off
   ``parsed.messages`` so the caller's residual ``messages`` loop does
   NOT re-emit the initial message. Aelix mirrors via
   :meth:`list.pop(0)` which mutates the caller's
   :class:`Args.messages` list in-place.
2. **No-separator concat** — Pi composes ``stdin + fileText +
   firstMessage`` with ``parts.join("")`` (empty separator). Aelix
   mirrors via ``"".join(parts)``.

Pi citation: ``cli/initial-message.ts:1-43`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .args import Args


@dataclass
class InitialMessage:
    """Pi parity: ``{initialMessage, initialImages}`` return shape.

    Both fields are :data:`None` when no input is present (Pi
    ``undefined`` parity).
    """

    initial_message: str | None = None
    initial_images: list[Any] | None = None


def build_initial_message(
    parsed: Args,
    *,
    file_text: str | None = None,
    file_images: list[Any] | None = None,
    stdin_content: str | None = None,
) -> InitialMessage:
    """Pi parity: ``buildInitialMessage`` (``cli/initial-message.ts``).

    Composition order: ``stdin + file_text + first_message`` joined
    with no separator (Pi ``parts.join("")``).

    SIDE EFFECT: pops index 0 from :attr:`Args.messages` (Pi ``.shift()``)
    when ``parsed.messages`` is non-empty. The mutation is required so
    the caller's residual-messages loop does NOT re-emit the initial
    message.

    Returns :class:`InitialMessage` with both fields :data:`None` when
    no inputs were supplied (Pi parity — caller skips the
    ``session.prompt(initial)`` call).
    """

    parts: list[str] = []
    if stdin_content is not None:
        parts.append(stdin_content)
    if file_text:
        parts.append(file_text)
    if parsed.messages:
        parts.append(parsed.messages[0])
        # SIDE EFFECT — Pi ``parsed.messages.shift()`` parity (P-388).
        parsed.messages.pop(0)
    return InitialMessage(
        initial_message="".join(parts) if parts else None,
        initial_images=file_images if file_images else None,
    )


__all__ = ["InitialMessage", "build_initial_message"]
