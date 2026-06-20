"""Shell environment helper — Pi parity ``utils/shell.ts`` ``getShellEnv``.

P0 #3 HEAVY (ADR-0139). Builds the environment for spawned bash commands so
that auto-downloaded ``rg`` / ``fd`` binaries (installed by
:mod:`aelix_coding_agent.util.tools_manager` into
:func:`aelix_coding_agent.cli.config.get_bin_dir`) are resolvable on ``PATH``.

Pi citation: ``utils/shell.ts:108-120`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import os


def get_shell_env() -> dict[str, str]:
    """Pi parity ``getShellEnv`` (``utils/shell.ts:108-120``).

    Returns a copy of the process environment with :func:`get_bin_dir`
    prepended to the ``PATH`` entry (resolved case-insensitively, matching
    Pi's ``key.toLowerCase() === "path"`` lookup). The bin dir is only
    prepended when not already present, so repeated calls are idempotent.
    """

    # Lazy import: ``cli.config`` lives behind a heavy ``cli/__init__`` that
    # imports ``cli.repl`` → ``tools.bash``; importing it at module load would
    # create an import cycle (bash → shell_env → cli.config → repl → bash).
    from aelix_coding_agent.cli.config import get_bin_dir

    bin_dir = get_bin_dir()
    env = dict(os.environ)

    # Pi parity: resolve the PATH key case-insensitively (Windows uses "Path").
    path_key = next(
        (k for k in env if k.lower() == "path"),
        "PATH",
    )
    current_path = env.get(path_key, "")
    entries = [e for e in current_path.split(os.pathsep) if e]
    if bin_dir not in entries:
        updated = os.pathsep.join([p for p in (bin_dir, current_path) if p])
        env[path_key] = updated
    return env


__all__ = ["get_shell_env"]
