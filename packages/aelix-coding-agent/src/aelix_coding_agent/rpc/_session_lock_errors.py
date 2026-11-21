"""The optional stdio session-lock error extension to control-v1."""

from __future__ import annotations

import json

from aelix_agent_core.session.session_lock import WriterKind

STARTUP_PREFIX = b"AELIX_RPC_STARTUP_ERROR/1 "


def lock_error_details(holder: WriterKind) -> dict[str, str]:
    details = {"kind": "session_locked"}
    if holder in ("studio", "cli"):
        details["holderKind"] = holder
    return details


def startup_record(nonce: str, holder: WriterKind) -> bytes:
    body = {"nonce": nonce, **lock_error_details(holder)}
    return STARTUP_PREFIX + json.dumps(body, separators=(",", ":")).encode("ascii") + b"\n"
