"""``ServerConfig`` — env-driven server configuration (Sprint 6h₉f §4.1).

Env-only in v1 (ADR-0103 divergence #2 — ADR-0097 allows
``aelix-server.toml`` OR env vars; TOML parsing deferred).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_schemas_dir() -> Path:
    """Default schemas dir: ``<cwd>/docs/contracts`` resolved.

    Kept deliberately simple per spec §4.1: env override
    (``AELIX_SERVER_SCHEMAS_DIR``) OR ``Path(cwd, "docs/contracts")``
    resolved. When absent, ``/schemas`` returns 404 (documented).

    Uses ``os.getcwd()`` so a directly-constructed ``ServerConfig()``
    and ``ServerConfig.from_env()`` (which also resolves from ``cwd``)
    agree when neither env var nor explicit ``cwd`` argument is given.
    """

    return (Path(os.getcwd()) / "docs" / "contracts").resolve()


@dataclass(frozen=True)
class ServerConfig:
    """Frozen server configuration.

    Defaults match the single-user dev model: localhost bind, no auth,
    no DB. ``schemas_dir`` resolves ``docs/contracts`` relative to
    ``cwd`` unless ``AELIX_SERVER_SCHEMAS_DIR`` overrides it.
    """

    bind: str = "127.0.0.1"
    port: int = 8765
    cwd: str = "."
    model: str = ""
    provider: str = ""
    schemas_dir: Path = field(default_factory=_default_schemas_dir)

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Build from ``AELIX_SERVER_*`` env vars with the documented defaults.

        ``AELIX_SERVER_PORT`` is parsed with :func:`int`; an invalid value
        raises a clear :exc:`ValueError` at startup.
        """

        bind = os.environ.get("AELIX_SERVER_BIND", "127.0.0.1")
        port_raw = os.environ.get("AELIX_SERVER_PORT", "8765")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError(
                f"AELIX_SERVER_PORT must be an integer, got {port_raw!r}"
            ) from exc
        cwd = os.environ.get("AELIX_SERVER_CWD", ".")
        model = os.environ.get("AELIX_SERVER_MODEL", "")
        provider = os.environ.get("AELIX_SERVER_PROVIDER", "")

        schemas_override = os.environ.get("AELIX_SERVER_SCHEMAS_DIR")
        if schemas_override:
            schemas_dir = Path(schemas_override).resolve()
        else:
            schemas_dir = (Path(cwd) / "docs" / "contracts").resolve()

        return cls(
            bind=bind,
            port=port,
            cwd=cwd,
            model=model,
            provider=provider,
            schemas_dir=schemas_dir,
        )
