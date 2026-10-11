"""Private structured picker text for internal producers (#179).

The string value is plain text, so existing tab formatters keep their list[str]
contract. Only these explicit fragments carry owned styling; their text is
still named by the picker before rendering. Plain ANSI strings are never
upgraded to this representation, including in ``own=True`` pickers.
"""

from __future__ import annotations

from typing import Literal


class _PickerText(str):
    fragments: tuple[tuple[Literal["dim"], str], ...]

    def __new__(cls, fragments: tuple[tuple[Literal["dim"], str], ...]) -> _PickerText:
        value = super().__new__(cls, "".join(text for _style, text in fragments))
        value.fragments = fragments
        return value
