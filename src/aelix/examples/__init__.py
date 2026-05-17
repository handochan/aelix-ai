"""Example extensions and demos shipped with Aelix.

The :mod:`aelix.examples.echo` module exposes a minimal echo tool used by the
``python -m aelix`` / ``uv run aelix`` demo. Once the Extension API lands
(Phase 1.2 / ADR-0004) the same module will gain an extension factory that
registers the tool through ``aelix.on`` / ``aelix.register_tool``.
"""
