"""Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).

Stored configuration values in Pi's ``auth.json`` can use two
indirection forms:

- ``!<command>``: the rest of the string is executed as a shell
  command via ``sh -c <command>``; the trimmed stdout becomes the
  resolved value. Per-command results are cached so repeated reads do
  not re-fork the shell.
- ``<env-name>``: when the literal string matches an environment
  variable name, its value is substituted in. If the env var is unset,
  the literal value is returned verbatim (Pi behavior).

This module ports the helper into Aelix so stored ``api_key`` entries
honor the Pi convention (Sprint 6e W6, P-141). Without it, a Pi-style
``auth.json`` entry like ``"key": "OPENAI_API_KEY"`` would have leaked
the env-var NAME as the API key.
"""

from __future__ import annotations

import os
import subprocess


def resolve_config_value(
    value: str, cache: dict[str, str] | None = None
) -> str:
    """Pi parity: ``coding-agent/core/resolve-config-value.ts``.

    - When ``value`` starts with ``!``, treat the rest as a shell
      command and return the trimmed stdout. Results are cached per
      ``cache`` mapping (if provided) so repeated reads do not re-fork.
    - When ``value`` matches an environment variable name, return the
      env value.
    - Otherwise return ``value`` verbatim.

    The ``cache`` parameter is intentionally exposed so callers (e.g.
    :class:`AuthStorage`) can scope the cache to a single instance and
    invalidate it on demand. Pi caches per-process; Aelix scopes it
    tighter for testability.
    """

    if value.startswith("!"):
        cmd = value[1:]
        if cache is not None and cmd in cache:
            return cache[cmd]
        result = subprocess.run(  # noqa: S602 — intentional Pi-parity shell exec.
            ["sh", "-c", cmd],
            capture_output=True,
            text=True,
            check=True,
        )
        out = result.stdout.rstrip("\n")
        if cache is not None:
            cache[cmd] = out
        return out
    return os.environ.get(value, value)


__all__ = ["resolve_config_value"]
