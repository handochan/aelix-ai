"""Re-export (#202). The body moved to :mod:`aelix_ai.utils._process_tree`.

``bash.py`` and ``_subprocess.py`` are no longer the only sites that need to end
a process tree — the rpc client, the subprocess hooks and ``!command`` do too,
and one of those (``!command``, ``aelix_ai/oauth/_resolve_config.py``) lives in
``aelix-ai``, which is the bottom of the import direction and cannot import back
into this package. So the primitive moved down and this module became the import
path its two original callers already had.

Everything that was measured here is now stated there, along with the Job Object
that #105's ``taskkill`` could not be: see that module's docstring and ADR-0238.
Adopting :class:`~aelix_ai.utils._process_tree.ProcessTree` at these two sites —
a job instead of ``taskkill`` for the bash tool, which is the exact Pi #9129
shape — is #222.
"""

from __future__ import annotations

from aelix_ai.utils._process_tree import kill_process_tree

__all__ = ["kill_process_tree"]
