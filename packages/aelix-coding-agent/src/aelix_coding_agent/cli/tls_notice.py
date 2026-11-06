"""The one line the CLI shows when this process relaxes RFC 5280 strict checking (#703).

On Python 3.13 a corporate proxy's certificate can fail strict checking alone.
aelix measures that the machine trusts the chain without it, relaxes strict for
the session and sends the request again (:mod:`aelix_ai.providers._tls_strict`,
:mod:`aelix_ai.providers._strict_resend`): nothing failed that the user can act
on, so nothing is shown as an error. This says what was done, once, on one line
(:meth:`~aelix_ai.providers._tls_strict.Relaxation.notice`): dim in the TUI
(``tui/shell.py`` registers its own listener), on stderr in ``-p``, ``--mode
json`` and ``--mode rpc`` (:func:`announce_on_stderr`), whose stdout carries
only the answer or the protocol.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from aelix_ai.providers._tls_strict import Relaxation, add_relaxation_listener
from aelix_ai.utils.terminal_text import safe_for_terminal

__all__ = ["announce_on_stderr", "notice_line"]


def notice_line(relaxation: Relaxation) -> str:
    """:meth:`Relaxation.notice`, with any steering character removed.

    Not cut: the host is a request URL's (at most 253 characters by DNS) and the
    rule is OpenSSL's verify message (68 at the longest, OpenSSL 3.6).
    """

    return safe_for_terminal(relaxation.notice())


def announce_on_stderr(stream: TextIO | None = None) -> Callable[[], None]:
    """Write the notice to stderr (or ``stream``) when the relaxation is made.

    Returns the function that stops it. A write that fails is dropped: the
    listener's failure must never become the request's.
    """

    def show(relaxation: Relaxation) -> None:
        print(
            notice_line(relaxation), file=stream if stream is not None else sys.stderr, flush=True
        )

    return add_relaxation_listener(show)
