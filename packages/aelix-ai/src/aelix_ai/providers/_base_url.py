"""Base-URL placeholder expansion — pi parity (``cloudflare-auth.ts``).

pi expands ``{CLOUDFLARE_ACCOUNT_ID}`` / ``{CLOUDFLARE_GATEWAY_ID}`` tokens in a
model ``baseUrl`` from the resolved auth env (``resolveCloudflareBaseUrl``,
``packages/ai/src/providers/cloudflare-auth.ts``) before constructing the
client. The placeholder name *is* the env-var name, so the faithful, general
form is: substitute any ``{ENV_VAR}`` token with ``os.environ[ENV_VAR]`` when
that variable is set, leaving the token verbatim otherwise.

A base_url that still carries a ``{…}`` token after expansion means a required
env var is missing — the SDK would receive a malformed URL and fail at the
first turn — so callers treat such a model as not runnable / keep it hidden
(see :mod:`aelix_coding_agent.core.runnable_models`).

A credential authenticates; it never addresses (#362, ADR-0250 §2.2, ADR-0203).
pi expands only Cloudflare's two tokens; this general form would also let a
cloned repo's ``.env`` choose the HOST of a user's own template (Codex's third
cross-review, C3: ``https://{TENANT_KEY}.owner-gateway.invalid/v1`` plus a
``.env`` ``TENANT_KEY=repo-chosen.invalid/v1#`` sent the prompt and the repo's key
to ``repo-chosen.invalid``). So a variable a cwd ``.env`` supplied fills a
placeholder only when ADR-0203 admits it as template configuration with a
value-shape rule (:data:`aelix_ai.dotenv_record.DOTENV_TEMPLATE_NAMES`, the two
Cloudflare ids); any other ``.env`` name leaves its token verbatim - the model is
not runnable, and :func:`dotenv_withheld_placeholder_names` lets the caller say
why. An exported variable fills every placeholder, as before.
"""

from __future__ import annotations

import os
import re

from aelix_ai.dotenv_record import may_fill_placeholder

# Placeholder token: ``{ENV_VAR_NAME}`` (matches pi's ``{CLOUDFLARE_ACCOUNT_ID}``
# / ``{CLOUDFLARE_GATEWAY_ID}``). Env-var names are letters/digits/underscore.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# ``scheme://`` at the very start of a template (RFC 3986: a letter, then
# letters, digits, ``+``, ``-``, ``.``).
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def expand_base_url(base_url: str | None) -> str | None:
    """Substitute ``{ENV_VAR}`` tokens in ``base_url`` from the environment.

    Each ``{NAME}`` is replaced by ``os.environ[NAME]`` when that variable is
    set and non-empty and :func:`~aelix_ai.dotenv_record.may_fill_placeholder`
    (exported, or a ``.env``-admitted Cloudflare id); otherwise the token is
    left verbatim so :func:`has_unexpanded_placeholders` can flag the model as
    not runnable. Returns the input unchanged when it is falsy or carries no
    placeholder.
    """

    if not base_url or "{" not in base_url:
        return base_url
    span = _path_span(base_url)

    def _sub(match: re.Match[str]) -> str:
        value = _fill_value(match.group(1), _in_path(match.start(), span))
        return value if value else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, base_url)


def _path_span(base_url: str) -> tuple[int, int] | None:
    """Where the template's PATH begins and ends - ``(index of its '/', index of '?'/'#' or len)``.

    The authority ends at the first ``/``, ``?`` or ``#`` after ``scheme://``;
    the path is there only when that delimiter is a ``/``, and it ends at the
    first ``?`` or ``#`` after it. Read off the TEMPLATE, so a value never moves
    the boundary. No ``scheme://``, or no path (``https://host``,
    ``https://host?next=/{X}`` - Codex's fourth cross-review of #362, F3:
    the ``/`` inside a query was read as the path), means no ``.env`` value fills
    anything (fail-closed). The scheme must OPEN the template (RFC 3986 scheme
    characters): a ``://`` inside a query (``h.invalid/v1?next=https://x/{X}``)
    is not one - the independent check of the fifth pass's fixes.
    """

    scheme = _SCHEME_RE.match(base_url)
    if scheme is None:
        return None
    start = scheme.end()
    found = [i for i in (base_url.find(d, start) for d in "/?#") if i >= 0]
    authority_end = min(found) if found else len(base_url)
    if authority_end >= len(base_url) or base_url[authority_end] != "/":
        return None
    ends = [i for i in (base_url.find(d, authority_end) for d in "?#") if i >= 0]
    return authority_end, (min(ends) if ends else len(base_url))


def _in_path(position: int, span: tuple[int, int] | None) -> bool:
    return span is not None and span[0] < position < span[1]


def _placeholders(base_url: str) -> list[tuple[str, bool]]:
    """Each ``{NAME}`` token in order, with whether it sits in the path."""

    span = _path_span(base_url)
    return [(m.group(1), _in_path(m.start(), span)) for m in _PLACEHOLDER_RE.finditer(base_url)]


def _fill_value(name: str, in_path: bool) -> str | None:
    """The value ``{name}`` expands to, or None when the token must stay."""

    value = os.environ.get(name)
    if not value or not may_fill_placeholder(name, in_path=in_path):
        return None
    return value


def unexpanded_placeholder_names(base_url: str | None) -> list[str]:
    """Env-var names whose ``{NAME}`` token is still unfilled after expansion.

    Empty when ``base_url`` is falsy, carries no placeholder, or every token's
    env var is set — i.e. the URL is ready for client construction.
    """

    if not base_url or "{" not in base_url:
        return []
    return [name for name, in_path in _placeholders(base_url) if not _fill_value(name, in_path)]


def dotenv_withheld_placeholder_names(base_url: str | None) -> list[str]:
    """The unfilled names that ARE set - by a cwd ``.env``, which may not fill them.

    A subset of :func:`unexpanded_placeholder_names`: the variable has a value,
    but a project ``.env`` supplied it and it is not template configuration, so
    the caller's "set X" would mislead - the user must export it instead.
    """

    if not base_url or "{" not in base_url:
        return []
    return [
        name
        for name, in_path in _placeholders(base_url)
        if os.environ.get(name) and not may_fill_placeholder(name, in_path=in_path)
    ]


def has_unexpanded_placeholders(base_url: str | None) -> bool:
    """True when ``base_url`` still carries a ``{ENV_VAR}`` after expansion.

    Signals a required env var is unset → the model would hit a malformed URL
    at the first turn, so callers treat it as not runnable / keep it hidden.
    """

    return bool(unexpanded_placeholder_names(base_url))


__all__ = [
    "dotenv_withheld_placeholder_names",
    "expand_base_url",
    "has_unexpanded_placeholders",
    "unexpanded_placeholder_names",
]
