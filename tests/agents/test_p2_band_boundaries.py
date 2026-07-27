"""ADR-0197 (P2) — the machine gate for the 3-band rule.

P2's whole architecture is a placement claim: ``aelix-agent-core`` (kernel) gets
**zero** delegation surface, ``aelix_coding_agent`` (product-core) gets the
CONTRACT and nothing that spawns or consents, and every process-creating and
consent-taking line lives in the bundled ``aelix_agents`` extension. Reviews
cannot hold that line across six workstreams; these four tests can.

Deliberately structural, not behavioural — they read the source tree, so they
stay meaningful in any checkout and after the branch merges (finding I6, which
retired the earlier ``main...HEAD`` gate: ``.github/workflows/ci.yml:40-41``
checks out shallow with no ``with:`` block, so that gate ERRORED on CI and went
vacuous once merged).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL_SRC = REPO_ROOT / "packages" / "aelix-agent-core" / "src"
PRODUCT_CORE_SRC = (
    REPO_ROOT / "packages" / "aelix-coding-agent" / "src" / "aelix_coding_agent"
)

# Every spelling of the P2 seam. ``subagent`` is matched case-INSENSITIVELY so
# one entry covers ``SubagentRuntime``, ``bind_subagents`` and
# ``AELIX_SUBAGENT_DEPTH``; ``aelix_agents`` is the extension package itself.
_KERNEL_FORBIDDEN_CI = ("subagent",)
_KERNEL_FORBIDDEN = ("aelix_agents",)

# The three product-core files that legitimately create processes today, all of
# them pre-P2 and none of them delegation: the RPC client's server launcher and
# the two bash/tool executors. Re-verified against the tree — these are the ONLY
# files with a hit, so any NEW entry is a band violation by construction.
_SPAWN_ALLOWLIST = (
    "rpc/rpc_client.py",
    "tools/",
)

# ``create_subprocess_exec`` / ``subprocess.Popen`` / ``os.fork``.
#
# Matched on the DOTTED path, not the final attribute: product-core already has
# six unrelated ``.fork(...)`` calls (session forking — ``tui/shell.py:781``,
# ``rpc/rpc_mode.py:1395``, ``extensions/command_context.py:116``), so a
# bare-name match would fire on them and this gate would have to be weakened
# the first time it ran. Receiverless spellings are accepted for the two names
# that can legitimately be imported directly.
_SPAWN_CALL_SUFFIXES = (
    "create_subprocess_exec",
    "subprocess.Popen",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "os.posix_spawnp",
)
_SPAWN_CALL_EXACT = frozenset({"create_subprocess_exec", "Popen", "fork"})

# KNOWN GAP, stated rather than hidden: ``asyncio.create_subprocess_shell`` is
# NOT in the set. ``extensions/subprocess_hooks.py:149`` already calls it and is
# outside the allowlist, so including it would make this gate red on arrival.
# The P2 spawner uses ``create_subprocess_exec`` (argv, never a shell string),
# so the gate covers the real drift path; widening it means first re-homing or
# allowlisting that pre-existing call.

# ADR-0197 §(i): consent is EXTENSION policy. Product-core may surface a refusal
# the extension already made; it may never author one.
_CONSENT_TOKENS = ("SpawnGrant", "request_spawn_consent")


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# === Band 1 — the kernel =====================================================


def test_kernel_has_no_subagent_surface() -> None:
    """Zero bytes of delegation vocabulary under ``aelix-agent-core``.

    This is the content form of "kernel untouched". Unlike a git-range check it
    works in a shallow checkout, needs no remote, and stays true after the merge
    — and it was measured green before a line of P2 was written.
    """

    hits: list[str] = []
    for path in _python_files(KERNEL_SRC):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        rel = path.relative_to(REPO_ROOT)
        hits.extend(
            f"{rel}: {token!r} (case-insensitive)"
            for token in _KERNEL_FORBIDDEN_CI
            if token in lowered
        )
        hits.extend(f"{rel}: {token!r}" for token in _KERNEL_FORBIDDEN if token in text)

    assert hits == [], (
        "the kernel must carry NO subagent surface (ADR-0008 as amended by "
        f"ADR-0197 — delegation is an extension concern): {hits}"
    )


def test_kernel_untouched_vs_merge_base() -> None:
    """Not one line of ``packages/aelix-agent-core`` changed on this branch.

    Skipped rather than failed when the ref is unavailable (shallow clone, no
    remote, detached tarball) — an unrunnable gate must announce itself, not
    pass silently. ``test_kernel_has_no_subagent_surface`` is the always-armed
    half; this one additionally catches a NON-subagent kernel edit.
    """

    base = _git("merge-base", "origin/main", "HEAD")
    if base.returncode != 0 or not base.stdout.strip():
        pytest.skip(
            "cannot resolve `git merge-base origin/main HEAD` "
            f"(shallow checkout or no remote): {base.stderr.strip() or 'no output'}"
        )

    kernel_path = "packages/aelix-agent-core"
    committed = _git("diff", "--name-only", base.stdout.strip(), "HEAD", "--", kernel_path)
    assert committed.returncode == 0, committed.stderr
    changed = [line for line in committed.stdout.splitlines() if line.strip()]

    # Uncommitted work counts too — during P2 the kernel edit that matters most
    # is the one still sitting in the working tree.
    worktree = _git("status", "--porcelain", "--", kernel_path)
    assert worktree.returncode == 0, worktree.stderr
    changed.extend(line for line in worktree.stdout.splitlines() if line.strip())

    assert changed == [], f"the kernel must not change in P2: {changed}"


# === Band 2 — product-core is INTERFACE ONLY =================================


def test_product_core_never_spawns() -> None:
    """No NEW process-creation site anywhere under ``aelix_coding_agent``.

    P2's central risk is exactly this drift: a "just one helper" that builds
    child argv or calls ``create_subprocess_exec`` from the product layer. The
    spawn lives in ``aelix_agents/print_channel.py`` and nowhere else.
    """

    offenders: list[str] = []
    for path in _python_files(PRODUCT_CORE_SRC):
        rel = path.relative_to(PRODUCT_CORE_SRC).as_posix()
        if any(rel.startswith(allowed) for allowed in _SPAWN_ALLOWLIST):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute | ast.Name):
                continue
            dotted = ast.unparse(node.func)
            spawns = dotted in _SPAWN_CALL_EXACT or any(
                dotted == suffix or dotted.endswith(f".{suffix}")
                for suffix in _SPAWN_CALL_SUFFIXES
            )
            if spawns:
                offenders.append(f"{rel}:{node.lineno}: {dotted}(...)")

    assert offenders == [], (
        "product-core is INTERFACE ONLY — every spawn belongs in the bundled "
        f"aelix_agents extension (ADR-0197 §1.2): {offenders}"
    )

    # The seam file itself is held to the stricter rule its own docstring
    # states: it declares types and a Protocol, so it has no business importing
    # the process or task machinery at all. Scoped to this one file because
    # seven pre-P2 product-core modules legitimately `import subprocess`.
    seam = ast.parse(
        (PRODUCT_CORE_SRC / "subagent_contract.py").read_text(encoding="utf-8")
    )
    banned = {"subprocess", "asyncio"}
    seam_imports: list[str] = []
    for node in ast.walk(seam):
        if isinstance(node, ast.Import):
            seam_imports.extend(
                a.name for a in node.names if a.name.split(".")[0] in banned
            )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in banned:
                seam_imports.append(node.module or "")

    assert seam_imports == [], (
        "subagent_contract.py is interface-only: no subprocess, no asyncio, no "
        f"task creation (ADR-0197 §4): {seam_imports}"
    )


def test_product_core_never_prompts_for_spawn_consent() -> None:
    """OC-1 — consent policy lives in ``aelix_agents/consent.py`` only.

    Product-core may SURFACE a refusal the extension already made (rendering a
    ``status="declined"`` envelope); it may never author one. If ``SpawnGrant``
    ever appears here, the posture rules have leaked across the band and
    ``tui/commands.py`` has started deciding what a child is allowed to do.
    """

    hits: list[str] = []
    for path in _python_files(PRODUCT_CORE_SRC):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(PRODUCT_CORE_SRC).as_posix()
        hits.extend(f"{rel}: {token}" for token in _CONSENT_TOKENS if token in text)

    assert hits == [], (
        "spawn consent is extension policy, not product-core policy "
        f"(ADR-0197 §(i), ADR-0008 as amended): {hits}"
    )
