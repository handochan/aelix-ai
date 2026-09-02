"""``aelix-server`` console-script entry (Sprint 6h₉f §4.5).

Launches uvicorn programmatically against the module-level
``aelix_server.app:app``. ``main_sync`` is the console-script target
(matches the ``aelix`` entry naming convention).
"""

from __future__ import annotations

import uvicorn
from aelix_coding_agent.util.stdio import harden_stdio

from aelix_server.config import ServerConfig


def main() -> None:
    """Boot the daemon with config derived from ``AELIX_SERVER_*`` env vars."""

    config = ServerConfig.from_env()
    uvicorn.run(
        "aelix_server.app:app",
        host=config.bind,
        port=config.port,
        log_level="info",
    )


def main_sync() -> None:
    """Console-script target (``[project.scripts] aelix-server``).

    Hardens the std streams first, for the same reason as the ``aelix`` CLI
    (N-3, issue #110 P7). NOT measured on Windows, and deliberately stated
    without a failure claim: uvicorn's access log goes through ``logging``,
    whose ``StreamHandler.emit`` catches ``UnicodeEncodeError`` and prints
    ``--- Logging error ---`` instead, so a non-ASCII request path buries the
    log rather than killing the daemon. Writes that do NOT go through
    ``logging`` have no such catch.
    """

    harden_stdio()
    main()
