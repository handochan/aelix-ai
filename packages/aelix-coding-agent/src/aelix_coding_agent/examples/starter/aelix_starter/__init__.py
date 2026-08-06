"""A minimal buildable Aelix extension (issue #91, ADR-0207).

This is the entry module named by the ``aelix.extensions`` entry point in
``pyproject.toml`` (``aelix-starter = "aelix_starter:setup"``). The host binds
the ``aelix-plugin.toml`` sitting NEXT TO this file — inside this package
directory — from your installed metadata, WITHOUT importing this module. The
import only happens later, when the extension actually loads.

``setup`` takes one argument, the ``ExtensionAPI`` the host passes in. Note it
imports nothing from ``aelix`` at module top level: everything it needs arrives
on that object, so this package builds and installs even where Aelix is not
present, and the host's import-free manifest resolution never has to touch it.
"""

from __future__ import annotations

from typing import Any


def setup(aelix: Any) -> None:
    """Register this extension's imperative surface.

    Everything DECLARED in ``aelix-plugin.toml`` — the ``hello`` command and the
    ``example`` theme — must match what actually happens here (and, for the
    theme, what the host loads from the manifest). The manifest is a declaration;
    this function is the implementation. The theme is loaded by the host from the
    manifest directly, so there is nothing to register for it here.
    """

    def _hello(*_args: Any, **_kwargs: Any) -> str:
        return "hello from the aelix starter extension"

    aelix.register_command(
        "hello",
        handler=_hello,
        description="Starter demo command — returns a greeting.",
    )


__all__ = ["setup"]
