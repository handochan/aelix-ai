"""Telnaut — a worked custom-provider extension example (Issue #77).

:mod:`aelix_coding_agent.examples.telnaut.telnaut` shows how an in-house team
adds a private provider that (a) runs turns via ``register_provider`` and (b)
contributes its own ``/login`` method — an employee-number sign-in — via
``register_login_provider``. Load it like any extension — point ``--extension``
at ``telnaut.py``, drop it in a project-local ``.aelix/extensions/``, or install
it as a package (see docs/guides/extension-authoring.md § Loading an extension).
"""
