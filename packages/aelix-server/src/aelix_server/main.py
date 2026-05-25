"""``aelix-server`` console-script entry (Sprint 6h₉f §4.5).

Launches uvicorn programmatically against the module-level
``aelix_server.app:app``. ``main_sync`` is the console-script target
(matches the ``aelix`` entry naming convention).
"""

from __future__ import annotations

import uvicorn

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
    """Console-script target (``[project.scripts] aelix-server``)."""

    main()
