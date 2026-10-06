"""The one offline predicate — every place aelix would reach the network on its own.

#288. Offline was decided in three places that disagreed: ``update_check`` and
``cli/extension_install`` read ``PI_OFFLINE`` and ``AELIX_OFFLINE``, while
``util/tools_manager`` — the ``rg``/``fd`` download, which fetches an
executable — read ``PI_OFFLINE`` only, and ``cli/entry.py`` exported
``PI_OFFLINE=1`` only when ``PI_OFFLINE`` was already set. So ``AELIX_OFFLINE=1``
turned off the update check and the catalog fetch, and the first ``grep`` or
``find`` still went to ``api.github.com``. Three copies of one rule meant the
next alias would again land in two of them; this module is the rule, and the
others call it.

The names. ``PI_OFFLINE`` is pi's (``utils/tools-manager.ts``
``isOfflineModeEnabled``, ``main.ts`` ``isTruthyEnvFlag``); ``AELIX_OFFLINE`` is
the Aelix alias ADR-0185 introduced, and it is now a full alias. No third name:
ADR-0230 records why — every ``os.environ`` read is a consumer a hostile cwd
``.env`` may try to drive. Neither name is one a cwd ``.env`` can set on its
own: both match ``^PI_`` / ``^AELIX_`` in ``runtime_bootstrap._DOTENV_NEVER``,
so they are refused unless the user's real environment names them in
``AELIX_DOTENV_ALLOW`` (ADR-0203: neither is on the locked floor), and even then
a ``.env`` value never replaces one already exported. The ``--offline`` flag
reaches this function as ``explicit``, or through the ``PI_OFFLINE=1`` that
:func:`export_if_offline` writes.

The values. ``1``, ``true`` and ``yes`` turn it on (pi's documented set, case
ignored); ``0``, ``false``, ``no``, ``off`` and an unset or blank variable turn
it off (ADR-0185: ``PI_OFFLINE=0`` reads as off). Any other value also turns it
on. That last line is the one divergence from pi's ``isOfflineModeEnabled``,
and it goes in the safe direction: a switch that controls network access fails
closed, so ``PI_OFFLINE=on`` or ``AELIX_OFFLINE=enabled`` is offline rather than
silently online. Pi itself is not consistent here — its ``version-check.ts`` and
``model-runtime.ts`` treat any set ``PI_OFFLINE`` as offline — and this is the
reading ``update_check.is_offline`` already had. Surrounding whitespace is
ignored.
"""

from __future__ import annotations

import os

#: The environment names that engage offline mode, in the order they are read.
OFFLINE_ENV_NAMES: tuple[str, ...] = ("PI_OFFLINE", "AELIX_OFFLINE")

#: Values that read as an explicit OFF. Anything else non-blank is ON.
_OFF_VALUES = frozenset({"0", "false", "no", "off"})


def env_value_is_on(value: str | None) -> bool:
    """Whether one offline variable's value engages offline mode."""

    text = (value or "").strip().lower()
    return bool(text) and text not in _OFF_VALUES


def is_offline(explicit: bool = False) -> bool:
    """True when aelix must not reach the network on its own.

    ``explicit`` is a parsed ``--offline`` flag; otherwise either name in
    :data:`OFFLINE_ENV_NAMES` engages it. A value read as OFF in one name does
    not cancel the other: ``PI_OFFLINE=0 AELIX_OFFLINE=1`` is offline.
    """

    if explicit:
        return True
    return any(env_value_is_on(os.environ.get(name)) for name in OFFLINE_ENV_NAMES)


def export_if_offline(explicit: bool = False) -> bool:
    """Export ``PI_OFFLINE=1`` when :func:`is_offline`, so every child inherits it.

    The one place aelix writes either name. ``cli/entry.py`` calls it first,
    before any verb is dispatched — as pi's ``main.ts:576-580`` @ ``b223082bb``
    exports before ``handlePackageCommand`` (``:597``) — so the installer
    ``aelix extension install`` starts inherits ``PI_OFFLINE=1`` the way a
    delegated agent does (#288 review round 1: the verbs return before
    ``parse_args``, and the export used to run after it). There the extension
    verb's own ``--offline`` is ``explicit``, read the way pi reads its flag
    (``args.includes("--offline")``); ``cli/entry.py`` calls it again once
    ``parse_args`` has read the main ``--offline``. Online, nothing is written:
    an explicit ``PI_OFFLINE=0`` stays ``0`` rather than becoming ``1``.
    Returns the answer.
    """

    on = is_offline(explicit)
    if on:
        os.environ["PI_OFFLINE"] = "1"
    return on


__all__ = ["OFFLINE_ENV_NAMES", "env_value_is_on", "export_if_offline", "is_offline"]
