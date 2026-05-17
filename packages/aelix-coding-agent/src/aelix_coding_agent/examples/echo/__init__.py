"""Example extensions and demos shipped with Aelix.

The :mod:`aelix_coding_agent.examples.echo` module exposes a minimal echo
tool used by the ``python -m aelix`` / ``uv run aelix`` demo. The Extension
API (Phase 1.2 / ADR-0007) has shipped; the same module can gain an extension
factory that registers the tool through ``aelix.on`` / ``aelix.register_tool``.
"""
