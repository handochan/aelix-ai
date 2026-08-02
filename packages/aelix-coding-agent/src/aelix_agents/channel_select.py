"""``AELIX_SUBAGENT_CHANNEL`` — the DEV-ONLY delegation-transport selector.

Which transport a delegation spawns with is a DECISION, and ADR-0201 §D1's
closing sentence rules on where decisions live: "product-core got the
**mechanism** and the extension kept the **decision**". So the read is here, in
band 3, and ``cli/entry.py`` is untouched by it — it still names ``aelix_agents``
at exactly one site and still passes no channel.

That placement is not what the import-direction gate forces. Reading the var in
``cli/entry.py`` and passing ``channel=RpcChannel(...)`` was tried and
``tests/cli/test_p2_import_direction.py`` stayed GREEN (``_imported_names``
counts one entry per ``ImportFrom`` *node*, so a two-name import is still one
site). Two things decide it instead:

* :class:`~aelix_agents.print_channel.SubagentChannel` is declared in band 3, not
  in ``subagent_contract``. Under the other shape product-core would construct a
  value of a type it has no name for and pass it through a parameter typed by a
  band-3 Protocol.
* The only registry callable ``cli/entry.py`` can build is a ``lambda`` over one
  of its locals. The stale-proof one —
  ``AgentsExtension._host_model_registry``, which reads through ``_api.runtime``
  and therefore follows ``bind_model_registry`` across every ``/reload`` — is
  reachable only from inside this package.

Not beside ``DEPTH_ENV_VAR`` in ``subagent_contract`` either, which is the third
option a reader will think of: ``MAX_SUBAGENT_DEPTH`` / ``DEPTH_ENV_VAR`` are
there because product-core ITSELF gates on depth (``entry.py`` suppresses the
extension at the cap). Product-core gates on nothing about the channel.

THE CONTRACT
------------

===============================  ==========================================
Value                            Result
===============================  ==========================================
unset                            ``PrintChannel`` — today's behaviour
``""`` / whitespace-only         ``PrintChannel``, treated as unset
``print`` (any case, padded)     ``PrintChannel``
``rpc`` (any case, padded)       ``RpcChannel``
anything else                    :exc:`ChannelSelectionError`
===============================  ==========================================

``VAR=`` and ``env -u VAR`` behaving identically is deliberate and has a
precedent — ``AELIX_DEFAULT_CATALOG`` maps ``""`` → ``None``
(``cli/extension_catalog.py``) — because a shell script that clears a variable
by assigning empty must not get a different agent than one that unsets it. The
``.strip().lower()`` follows ``_reload_rebuild_enabled`` (``tui/shell.py``).

WHY AN UNKNOWN VALUE REFUSES INSTEAD OF FALLING BACK
----------------------------------------------------

This is the one place the repo's dominant env-var convention — lenient parse,
module default, say nothing (all four ``_env_float`` vars, ``_env_truthy``,
``_reload_rebuild_enabled``, ``subagent_depth``) — is WRONG, so the reason is
recorded rather than assumed.

A rejected ``--permission-mode`` warns and continues because its default is
still CORRECT. A rejected channel's default is not. Both channels feed the
identical ``_StreamState`` through the identical ``reduce_event`` into an
identically shaped ``on_stream``, so a silently-downgraded run is
indistinguishable from a successful rpc run at **every surface the parent
observes**: same envelope shape, same ``SubagentProgress`` fields. The only
external discriminator is the child's argv. A smoke report reading "rpc handled
the timeout correctly" while ``PrintChannel`` did the work is worse than no
smoke at all — and both of the regressions Step 1 shipped were PrintChannel
invariants that ``RpcChannel`` does not hold, so a mis-selected run reports
exactly the wrong answer.

The hard tier has its own precedents: ``--mode``, which is the transport
selector and the closest analogue there is (``cli/args.py`` → ``entry.py``,
``Error: …`` + ``return 1``), and ``AELIX_SERVER_PORT``
(``aelix_server/config.py``), whose docstring says an invalid value "raises a
clear ``ValueError`` at startup". The raise here surfaces the same way that one
does: :meth:`AgentsExtension.__post_init__` runs inside ``_async_main`` long
before the TUI starts, so the exception is a traceback ending in this message
and exit 1, with no alt-screen to corrupt.

WHY IT IS A MODULE AND NOT A HELPER ON ``AgentsExtension``
-----------------------------------------------------------

**The rpc import must stay lazy.** Measured on this box, three runs:
``import aelix_agents.rpc_channel`` costs **45-50 ms** on top of
``import aelix_agents``. Today's only production path is print, and every
delegation-enabled startup would pay that for a transport it will not use — so
the ``RpcChannel`` import lives INSIDE the ``rpc`` branch, which is only
possible if the branch is a function. It also gives the tests a pure function
with no dataclass around it.

DEV-ONLY, AND THAT IS WHY IT IS AN ENV VAR
-------------------------------------------

ADR-0201 defers the user-facing selector because a SETTING needs a
global-scope-only read — the property ``get_features_agents`` has and states as
a security property, so a cloned repo cannot switch delegation on from its own
``.aelix/settings.json``. **A repo cannot set an environment variable**, so that
self-elevation defeat is structurally impossible here and the owner decision it
needs is not owed yet. The setting stays deferred.

Modelled on ``AELIX_CODEX_ORIGINATOR`` (``openai_codex_responses.py``), which
exists for the same reason: an override that lets a live smoke run without a
rebuild, documented where it is read rather than in the README.

FIRST ENV-VAR READ IN THIS PACKAGE. Verified before writing it: ``aelix_agents``
touched ``os.environ`` in exactly one place, ``dict(os.environ)`` in
``build_child_env``. Nothing else here reads configuration, and that is worth
keeping true — a second one belongs beside this one.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from aelix_agents.print_channel import PrintChannel

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from aelix_agents.print_channel import SubagentChannel

CHANNEL_ENV_VAR = "AELIX_SUBAGENT_CHANNEL"
"""Two-token ``AELIX_<AREA>_<THING>``, joining the ``AELIX_SUBAGENT_DEPTH``
family. That one is declared in band 2 and this one in band 3 for the reason
this module's docstring gives; the shared prefix is the naming convention, not a
claim about placement."""

_PRINT = "print"
_RPC = "rpc"
_ACCEPTED = (_PRINT, _RPC)


class ChannelSelectionError(ValueError):
    """:data:`CHANNEL_ENV_VAR` named a transport that does not exist.

    A ``ValueError`` subclass so a caller that only knows the shape of a bad
    value still catches it, and a named type so a caller that wants to
    distinguish "the channel selector refused" from any other constructor
    failure can.
    """


def select_channel(
    *,
    model_registry: Callable[[], Any | None] | None = None,
    env: Mapping[str, str] | None = None,
) -> SubagentChannel:
    """Resolve :data:`CHANNEL_ENV_VAR` into a channel. See the module docstring.

    ``model_registry`` is passed straight through to whichever channel is built,
    and it must be a CALLABLE read live: the registry is rebound on ``/reload``,
    so a captured one goes stale. It is what wakes ``apply_cost_fallback``, which
    had never executed in production for either channel because the only
    production construction passed no registry at all.

    ``env=None`` means :data:`os.environ`. The parameter exists so the tests need
    no ``monkeypatch.setenv``, mirroring ``build_child_env(base=…)``'s own style.
    """

    source = os.environ if env is None else env
    value = source.get(CHANNEL_ENV_VAR, "").strip().lower()
    if value in ("", _PRINT):
        return PrintChannel(model_registry=model_registry)
    if value == _RPC:
        # LAZY ON PURPOSE — 45-50 ms measured, paid only by a run that asked for
        # it. See the module docstring.
        from aelix_agents.rpc_channel import RpcChannel

        return RpcChannel(model_registry=model_registry)
    raw = source.get(CHANNEL_ENV_VAR, "")
    raise ChannelSelectionError(
        f"{CHANNEL_ENV_VAR}={raw!r} is not a delegation channel; expected one "
        f"of {', '.join(_ACCEPTED)}. Refusing to start rather than falling back "
        "to 'print': the two channels are indistinguishable at every surface "
        "the parent observes, so a silent downgrade would produce a run that "
        "looks like a successful rpc run and is not one."
    )


__all__ = [
    "CHANNEL_ENV_VAR",
    "ChannelSelectionError",
    "select_channel",
]
