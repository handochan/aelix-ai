"""The child's project-trust flags — ADR-0197 §(g).

NEAR-PURE: no ``asyncio``, no ``subprocess``, no spawn. It touches the
filesystem only through ``Path.resolve()`` and one already-shipped product-core
predicate, which is what makes it unit-testable against a ``tmp_path``.

WHAT THIS IS NOT. An earlier draft of this design asserted that "the child
treats its cwd as UNTRUSTED". That claim is false and was deleted.
``cli/project_trust.py::resolve_project_trusted`` (``:470-580``) is a
seven-step ladder — 1 explicit override (``:521-523``) · 2 no trust-requiring
resources → ``True`` (``:525-527``) · 3 extension vote (``:531-548``) ·
4 persisted ``trust.json``, NEAREST-ANCESTOR walk (``:550-557``) · 5 global
``defaultProjectTrust`` (``:559-563``) · 6 headless → ``False`` (``:565-567``) ·
7 prompt (``:569-580``) — and the headless denial a subagent would hit is step
SIX, reached only after steps 1-5 have all declined to answer. A child in a
trusted tree is trusted, and ``:60-61`` documents that trusting a parent
directory transitively trusts its children.

WHY THE FLAG IS CONDITIONAL RATHER THAN ALWAYS-ON (finding OC-4). Passing
``--no-approve`` unconditionally buys exactly ONE new escalation, and it costs a
silent regression of a shipped default. Both are measured; see
:func:`child_trust_argv`.
"""

from __future__ import annotations

from pathlib import Path

from aelix_coding_agent.cli.project_trust import has_trust_requiring_project_resources


def child_trust_argv(child_cwd: Path, parent_cwd: Path) -> list[str]:
    """The project-trust flags the child argv carries — ADR-0197 §(g).

    Two clauses, and each one is load-bearing:

    1. SAME cwd AND the gate has nothing to gate (``.aelix/extensions``,
       ``.aelix/mcp.json``, ``.aelix/agents`` all absent —
       ``project_trust.py:112-177``) → emit NOTHING. Step 2
       (``project_trust.py:525-527``) would have returned ``True`` for the
       parent too, so there is no authority to withhold; forcing ``False`` here
       only strips ``.aelix/skills/``, which the gate never guarded, and
       silently regresses the ``inherit_skills: true`` DEFAULT
       (``agents/profile.py:175``). The predicate checks ``extensions/``,
       ``mcp.json`` and ``agents/`` and deliberately NOT ``skills/``, so a
       skills-only repo loads its skills today — and would stop under an
       always-on flag, for zero security gain, with the child's Notice going to
       stderr where a successful run never surfaces it.
    2. ANY other case — most importantly a DIFFERENT cwd, which is always
       MODEL-CHOSEN — → ``--no-approve``. This is the whole security value of
       the flag: it kills the nearest-ancestor escalation at
       ``project_trust.py:550-557`` (transitivity documented at ``:60-61``),
       where a child started in ``vendor/sdk`` inherits the monorepo root's
       persisted ``True`` and executes a vendored ``.aelix/extensions/*.py``
       the parent itself never loaded (``extensions/loader.py`` scans
       ``cwd/.aelix/extensions`` only, so the parent at the root has never
       touched that file). Measured: parent at root → not executed; child at
       ``vendor/sdk`` without the flag → EXECUTED; with the flag → not
       executed.

    Wherever the trust gate is actually live, this is SECURITY-IDENTICAL to
    passing ``--no-approve`` unconditionally.

    FAIL-CLOSED ON ANYTHING UNEXPECTED. A corrupt or unreadable path, a
    ``resolve()`` that raises, a predicate that blows up — every one of them
    returns ``["--no-approve"]``. Clause 1 is an EXEMPTION from a security
    default, so it may only be granted on positive evidence; an exception is
    the absence of evidence.

    Composes with, and does not replace, the cwd containment rule in §6.4: that
    rule bounds WHERE a child may run (inside the parent's tree),
    :func:`child_trust_argv` bounds WHAT may execute once it gets there.
    """

    try:
        same_cwd = child_cwd.resolve() == parent_cwd.resolve()
        if same_cwd and not has_trust_requiring_project_resources(child_cwd):
            return []
    except Exception:  # noqa: BLE001 — any failure means "no evidence", so deny
        return ["--no-approve"]
    return ["--no-approve"]


__all__ = [
    "child_trust_argv",
]
