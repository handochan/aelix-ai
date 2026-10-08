"""Trust-aware SYSTEM.md / APPEND_SYSTEM.md discovery (ADR-0257)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from .config import CONFIG_DIR_NAME, get_agent_dir

MAX_PROMPT_FILE_BYTES = 1 << 20


def discover_system_prompt_file(
    cwd: str, name: str, *, project_trusted: bool
) -> tuple[str | None, str | None]:
    """Read the first existing candidate, returning (content, loaded path).

    A selected empty/broken project file masks the global file: an empty
    SYSTEM.md deliberately restores the built-in prompt. Never inspect an
    untrusted project candidate, and never read a pipe/device/directory.
    """
    candidates = [Path(get_agent_dir()) / name]
    if project_trusted:
        candidates.insert(0, Path(cwd) / CONFIG_DIR_NAME / name)
    for path in candidates:
        try:
            # Nonblocking open avoids waiting on a substituted FIFO for
            # a writer. O_BINARY preserves byte counts on Windows.
            fd = os.open(
                path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            )
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            _warn(path, str(exc))
            return None, None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("not a regular file")
            if info.st_size > MAX_PROMPT_FILE_BYTES:
                raise OSError(f"exceeds the {MAX_PROMPT_FILE_BYTES}-byte system-prompt limit")
            # The loader owns fd even if fdopen fails. Validate and read the
            # same descriptor; a pathname replacement cannot substitute it.
            with os.fdopen(fd, "rb", buffering=0, closefd=False) as stream:
                raw = bytearray()
                remaining = MAX_PROMPT_FILE_BYTES + 1
                while remaining:
                    chunk = stream.read(remaining)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    remaining -= len(chunk)
            if len(raw) > MAX_PROMPT_FILE_BYTES:
                raise OSError(f"exceeds the {MAX_PROMPT_FILE_BYTES}-byte system-prompt limit")
            # Preserve Path.read_text's universal-newline behavior.
            content = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except (OSError, UnicodeError) as exc:
            _warn(path, str(exc))
            return None, None
        finally:
            os.close(fd)
        if not content.strip():
            return None, None
        return content, str(path.absolute())
    return None, None


def _warn(path: Path, reason: str) -> None:
    print(
        "".join(
            c
            for c in f"Warning: cannot read system prompt file {path}: {reason}"
            if c.isprintable()
        ),
        file=sys.stderr,
    )
