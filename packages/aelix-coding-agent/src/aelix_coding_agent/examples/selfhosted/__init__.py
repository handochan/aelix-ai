"""Self-hosted endpoint — a worked custom-provider extension example (Issue #77).

:mod:`aelix_coding_agent.examples.selfhosted.selfhosted` shows how a team adds
the OpenAI-compatible inference endpoint it runs itself as a provider that (a)
runs turns via ``register_provider`` and (b) contributes its own ``/login``
method — an access token the endpoint's console issues — via
``register_login_provider``. Load it like any extension — point ``--extension``
at ``selfhosted.py``, drop it in a project-local ``.aelix/extensions/``, or
install it as a package (see ``aelix docs extension`` § Loading an extension).
"""
