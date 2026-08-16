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
       ``.aelix/mcp.json``, ``.aelix/agents``, ``.aelix/skills`` and
       ``.aelix/prompt-templates`` all absent —
       :func:`has_trust_requiring_project_resources`) → emit NOTHING. Step 2
       (``project_trust.py:678-680``) would have returned ``True`` for the
       parent too, so there is no authority to withhold.

       This clause carried a SECOND argument that #115 falsified, recorded here
       because it was load-bearing: it said forcing ``False`` "only strips
       ``.aelix/skills/``, which the gate never guarded", for "zero security
       gain", since the predicate checked ``extensions/``, ``mcp.json`` and
       ``agents/`` and deliberately NOT ``skills/``. #115 gave skills a
       model-facing channel — a skill's name/description/location now go into
       the system prompt, and a prompt template's body becomes a user turn — so
       the gain is no longer zero and the predicate now counts both families.
       A skills-only repo therefore makes it ``True`` and the exemption does
       not reach that case at all any more; what remains is the one thing this
       clause ever legitimately claimed, that a directory with nothing to gate
       has no authority to withhold. NO logic changed here: the predicate moved
       underneath it. That is precisely why this function calls the predicate
       instead of re-spelling the resource list — a copy would have kept
       granting the retired exemption.
    2. ANY other case — most importantly a DIFFERENT cwd, which is always
       MODEL-CHOSEN — → ``--no-approve``. This is the whole security value of
       the flag: it kills the nearest-ancestor escalation at
       ``project_trust.py:703-710`` (transitivity documented at ``:71-72``),
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
