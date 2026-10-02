"""The cwd ``.env`` provenance record, as the provider layer reads it (#362, ADR-0250).

``aelix_coding_agent.cli.runtime_bootstrap.load_dotenv`` admits provider
credentials from a cwd ``.env`` into ``os.environ`` (ADR-0203) and writes the
NAMES it supplied to one environment variable, :data:`DOTENV_ADMITTED_ENV`
(``aelix_coding_agent.core.dotenv_provenance`` holds the writer, the parser and
the route-deciding judgements built on it). The name is defined HERE, in the
lowest package, because the provider layer has to read the record too and may
not import ``aelix-coding-agent`` (AGENTS.md: ``aelix-ai`` <- ``aelix-agent-core``
<- ``aelix-coding-agent``, never the other way); ``core.dotenv_provenance``
re-exports it, so there is one spelling.

What the provider layer needs from it is one question, asked at client
construction (:func:`aelix_ai.providers._base_url.expand_base_url`): may this
variable fill a ``{NAME}`` placeholder in a base URL? A credential authenticates
a request; it never addresses one. Codex's third cross-review of #362 (C3)
measured why the question is needed: a user's models.json
``{"providers": {"openrouter": {"baseUrl":
"https://{TENANT_KEY}.owner-gateway.invalid/v1"}}}`` plus a cloned repo's
``.env`` ``TENANT_KEY=repo-chosen.invalid/v1#`` (admitted as a credential by its
``_KEY`` suffix, which has no value-shape rule) sent ``/model openrouter/auto``
to host ``repo-chosen.invalid`` with the repo's key - the ``/`` and ``#`` erased
the fixed host suffix. So a name the record holds fills a placeholder only when
ADR-0203 admits it as provider CONFIGURATION for a base-URL template, with a
value-shape rule: :data:`DOTENV_TEMPLATE_NAMES`. An exported variable is not in
the record and fills every placeholder, as before - the user chose it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

DOTENV_ADMITTED_ENV = "AELIX_DOTENV_ADMITTED"
"""The variable that holds the record: the comma-separated names a cwd ``.env`` supplied."""

#: The ``.env``-admitted names that may fill a base-URL placeholder: the
#: catalogue's only templated tokens (``cloudflare-ai-gateway``,
#: ``cloudflare-workers-ai``), which ADR-0203's configuration arm admits with a
#: value-shape rule (``runtime_bootstrap._CF_ID``: letters, digits, ``-`` and
#: ``_`` - one plain label), and :func:`may_fill_placeholder` lets such a value
#: fill only a PATH token, so it lands below the template's fixed host whoever
#: wrote the template.
#: ``tests/providers/test_base_url.py`` pins that every name here has such a
#: rule in ``_DOTENV_CONFIG_VALUES``. The three GCP names in that arm have a
#: shape rule too but are not template tokens - the Vertex client builds its own
#: host from them (ADR-0203) - so they are not listed; a user's template that
#: names one is filled only from the shell.
DOTENV_TEMPLATE_NAMES: frozenset[str] = frozenset(
    {"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_GATEWAY_ID"}
)


#: The value a ``.env``-supplied template name may carry when it fills a token:
#: ADR-0203's ``runtime_bootstrap._CF_ID`` shape, checked AGAIN here so the
#: reader does not trust how the name was admitted. Codex's fourth cross-review
#: of #362 (F2): on Windows a hatched ``Cloudflare_Account_ID`` missed the
#: case-sensitive config dispatch (no shape check), the record spelled it
#: ``CLOUDFLARE_ACCOUNT_ID``, and ``acct?capture=repo`` moved the rest of the
#: catalogue path into a query. ``tests/providers/test_base_url.py`` pins that
#: the two patterns agree.
TEMPLATE_VALUE_SHAPE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _os_spelling(name: str) -> str:
    # Windows CPython stores environment names upper-cased; the coding agent's
    # writer records them so (``core.dotenv_provenance.env_name``).
    return name.upper() if os.name == "nt" else name


def dotenv_supplied(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """Is ``name`` in the record - did a cwd ``.env`` (here or in an ancestor aelix) supply it?"""

    source = os.environ if environ is None else environ
    raw = source.get(DOTENV_ADMITTED_ENV)
    if not raw:
        return False
    wanted = _os_spelling(name)
    return any(_os_spelling(part.strip()) == wanted for part in raw.split(","))


def may_fill_placeholder(
    name: str, environ: Mapping[str, str] | None = None, *, in_path: bool = True
) -> bool:
    """May ``name``'s current value fill a ``{name}`` base-URL placeholder?

    True for an exported variable (not in the record), anywhere in the URL; for
    a ``.env``-supplied name only when it is in :data:`DOTENV_TEMPLATE_NAMES`,
    its value has :data:`TEMPLATE_VALUE_SHAPE`, AND the token sits in the PATH
    (``in_path``: after the authority and before any ``?`` or ``#``). False
    otherwise: the token stays unexpanded (the model is not runnable, and the
    user is told to export the variable).

    The position rule is the verify round's finding on 6 (#362): the shape rule
    keeps a Cloudflare id to one plain label, which under the catalogue's
    templates lands in the path below a fixed host - but a user's own template
    may put ``{CLOUDFLARE_ACCOUNT_ID}`` in the HOST (measured: ``.env``
    ``CLOUDFLARE_ACCOUNT_ID=repochosen`` with
    ``https://{CLOUDFLARE_ACCOUNT_ID}.owner-gateway.invalid/v1`` reached
    ``repochosen.owner-gateway.invalid``). A ``.env`` value never addresses a
    request, so it never fills the scheme, the user-info, the host or the port.
    """

    if not dotenv_supplied(name, environ):
        return True
    if not in_path or _os_spelling(name) not in DOTENV_TEMPLATE_NAMES:
        return False
    source = os.environ if environ is None else environ
    return bool(TEMPLATE_VALUE_SHAPE.match(source.get(name) or ""))


__all__ = [
    "DOTENV_ADMITTED_ENV",
    "DOTENV_TEMPLATE_NAMES",
    "TEMPLATE_VALUE_SHAPE",
    "dotenv_supplied",
    "may_fill_placeholder",
]
