"""``GET /schemas/{name}`` handler (Sprint 6h₉f §4.4).

Serves ``docs/contracts/{name}.schema.json`` for cross-repo aelix-web
consumption. Path-traversal safe: a strict allowlist regex
(``^[A-Za-z0-9_-]+$`` — the hyphen is REQUIRED for the on-disk names
``descriptor-envelope`` / ``slot-taxonomy``) plus a resolve-prefix guard.

On-disk filenames (``docs/contracts/``):
``descriptor-envelope.schema.json``, ``manifest.schema.json``,
``primitives.schema.json``, ``slot-taxonomy.schema.json`` — so the
``{name}`` path param maps to ``{name}.schema.json``.
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

# Hyphen is intentional — ``descriptor-envelope`` / ``slot-taxonomy`` carry
# it on disk. No ``.`` or ``/`` permitted, so ``..`` traversal cannot match.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


async def get_schema(name: str, request: Request) -> FileResponse:
    """Return the requested schema file or raise a 4xx :exc:`HTTPException`.

    400 — name fails the allowlist regex or escapes ``schemas_dir``.
    404 — name is valid but the file does not exist.
    """

    if not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="invalid schema name")
    base = request.app.state.config.schemas_dir.resolve()
    path = (base / f"{name}.schema.json").resolve()
    # Resolve-prefix guard: the resolved path must live under ``base``.
    if not str(path).startswith(str(base) + os.sep):
        raise HTTPException(status_code=400, detail="invalid schema name")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="schema not found")
    return FileResponse(path, media_type="application/json")
