"""Carry the user's Python path through a delegated interpreter's bootstrap."""

from __future__ import annotations

import os
import sys

from aelix_coding_agent.subagent_contract import subagent_depth

_PYTHONPATH = "PYTHONPATH"
_ORIGINAL = "AELIX_CHILD_ORIGINAL_PYTHONPATH"
_ABSENT = "-"
_PRESENT = "="


def save_original_pythonpath(env: dict[str, str]) -> str | None:
    """Return the original value and save it for the child to restore."""
    if sys.platform == "win32":
        # os.environ canonicalizes names on Windows. An explicit base mapping
        # can still carry mixed-case spellings.
        for key in tuple(env):
            name = key.upper()
            if key != name and name in (_PYTHONPATH, _ORIGINAL):
                env[name] = env.pop(key)

    original = env.get(_PYTHONPATH)
    env[_ORIGINAL] = _ABSENT if original is None else _PRESENT + original
    return original


def restore_child_pythonpath() -> None:
    """Restore the user's environment while retaining the interpreter's sys.path."""
    if not subagent_depth(os.environ):
        # A standalone launch keeps its inherited environment.
        return

    original = os.environ.pop(_ORIGINAL, None)
    if original == _ABSENT:
        os.environ.pop(_PYTHONPATH, None)
    elif original is not None and original.startswith(_PRESENT):
        value = original[1:]
        os.environ[_PYTHONPATH] = value
        if sys.platform == "win32" and value == "":
            _restore_empty_windows_pythonpath()


def _restore_empty_windows_pythonpath() -> None:
    # CPython's Windows putenv uses _wputenv, which removes empty values from
    # the native environment. Keep the empty entry for implicit inheritance too.
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
    set_variable = kernel32.SetEnvironmentVariableW
    set_variable.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    if not set_variable(_PYTHONPATH, ""):
        raise ctypes.WinError(ctypes.get_last_error())  # pyright: ignore[reportAttributeAccessIssue]
