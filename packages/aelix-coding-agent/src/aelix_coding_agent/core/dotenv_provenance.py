"""Which environment names a cwd ``.env`` supplied (#362, ADR-0250).

``cli.runtime_bootstrap.load_dotenv`` admits provider credentials from a cwd
``.env`` into ``os.environ`` (ADR-0203). Once there they are indistinguishable
from a key the user exported — and #362 measured what that costs: with
``ANTHROPIC_API_KEY`` exported and a cloned repo's ``.env`` carrying
``OPENROUTER_API_KEY``, ``--model anthropic/claude-haiku-4-5`` went to
``openrouter.ai`` on the file's key. ADR-0250's guard 1 keeps such a key out of
every judgement that CHOOSES a route (it still authenticates the route once
chosen). That needs the names, so the loader records them here.

The record lives in the environment, not in this process, because the decision
has to survive a process boundary: a delegated child (``build_child_env`` copies
``os.environ`` wholesale) and a nested ``aelix`` the bash tool starts
(``get_shell_env()``) both inherit the keys, and without the record they would
read a planted key as exported. ``AELIX_`` is the prefix a ``.env`` cannot set
(``_DOTENV_NEVER``); ``load_dotenv`` refuses this name in its own branch as well,
because ``AELIX_DOTENV_ALLOW`` reopens ``^AELIX_`` (measured on ``9ca53a4f``:
``AELIX_DOTENV_ALLOW=AELIX_DOTENV_ADMITTED`` admitted it).

Names only, never values. On Windows CPython stores every environment name
upper-cased (``os.py`` ``_createenviron``, ``nt`` branch), so a ``.env`` line
``OpenRouter_API_KEY=…`` sets ``OPENROUTER_API_KEY``: names are recorded and
compared as the OS stores them (:func:`env_name`), or guard 1 would miss it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

# The record's name lives in ``aelix-ai`` (the lowest package), because the
# provider layer reads it too - a ``.env`` name may not fill a base-URL
# placeholder (``aelix_ai.providers._base_url``) - and may not import this one.
from aelix_ai.dotenv_record import DOTENV_ADMITTED_ENV

#: What an environment name may look like (POSIX portable names). ``load_dotenv``
#: refuses any other ``.env`` key, so no admitted name can carry the comma this
#: record is joined with: measured on the design prototype, a ``.env`` key
#: ``X,ANTHROPIC_API_KEY`` was admitted, recorded, and then split into
#: ``ANTHROPIC_API_KEY`` — marking the user's EXPORTED key as planted and sending
#: ``anthropic/claude-haiku-4.5`` to the file's OpenRouter account
#: (``/tmp/362-work/critic/probe_critic.out`` P1).
ENV_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def env_name(name: str) -> str:
    """``name`` as ``os.environ`` stores it on this platform (upper-cased on Windows)."""

    return name.upper() if os.name == "nt" else name


def parse_record(raw: str | None) -> frozenset[str]:
    """The names in a record value; anything that is not a plain name is dropped.

    Defensive, and reachable only from outside aelix: a ``.env`` cannot write the
    record (``load_dotenv`` refuses its name in its own branch) and
    :func:`format_record` never writes a non-name, so no ``.env`` line can reach
    this filter — no launch row can turn red without it. What it guards is a
    record the USER's environment hands down malformed (``A,,B``, a stray space,
    a truncated value): those parts name no variable, and an empty string must
    not enter the set. ``tests/cli/test_dotenv_provenance_362.py`` pins it on the
    function itself.
    """

    if not raw:
        return frozenset()
    return frozenset(
        env_name(part.strip()) for part in raw.split(",") if ENV_NAME.match(part.strip())
    )


def dotenv_admitted_names(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Every name a cwd ``.env`` supplied to this process or to an ancestor aelix."""

    source = os.environ if environ is None else environ
    return parse_record(source.get(DOTENV_ADMITTED_ENV))


def format_record(names: Iterable[str]) -> str:
    """The record value for ``names`` (sorted, de-duplicated, OS spelling)."""

    return ",".join(sorted({env_name(n) for n in names if ENV_NAME.match(n)}))


def is_dotenv_admitted(name: str, admitted: frozenset[str] | None = None) -> bool:
    """Did a cwd ``.env`` supply ``name``?"""

    record = dotenv_admitted_names() if admitted is None else admitted
    return env_name(name) in record


__all__ = [
    "DOTENV_ADMITTED_ENV",
    "ENV_NAME",
    "dotenv_admitted_names",
    "env_name",
    "format_record",
    "is_dotenv_admitted",
    "parse_record",
]
