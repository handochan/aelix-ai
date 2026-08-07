"""ADR-0197 (P2) — the machine gate for the 3-band rule.

P2's whole architecture is a placement claim: ``aelix-agent-core`` (kernel) gets
**zero** delegation surface, ``aelix_coding_agent`` (product-core) gets the
CONTRACT and nothing that spawns or consents, and every process-creating and
consent-taking line lives in the bundled ``aelix_agents`` extension. Reviews
cannot hold that line across six workstreams; these tests can.

P3 (ADR-0199) adds the two cap assertions S9 asks for. Fan-out multiplies the
number of numbers involved — concurrency, wall clock, task size, per-prompt
budget — and every one of them is a bound the EXTENSION chose, so every one of
them is a fresh chance to put policy one band too low.

Deliberately structural, not behavioural — they read the source tree, so they
stay meaningful in any checkout and after the branch merges (finding I6, which
retired the earlier ``main...HEAD`` gate: ``.github/workflows/ci.yml:40-41``
checks out shallow with no ``with:`` block, so that gate ERRORED on CI and went
vacuous once merged).
"""

from __future__ import annotations

import ast
import re
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

# === S9 — the cap allowlist ==================================================
#
# ADR-0197 verbatim: "Explicitly NOT done ... any spawn behaviour, CAP, registry
# or consent policy in product-core". Nothing gated that until P3, and
# ``MAX_SUBAGENT_DEPTH`` sits in ``subagent_contract.py`` as the precedent a
# future author would cite for putting ``MAX_CONCURRENCY`` beside it. This is
# the largest un-gated drift path in the phase, so it is an EXACT-SET assertion
# rather than a prefix rule: adding a cap to product-core has to be a deliberate
# edit to this list, made while reading the reason below.
#
# Measured against the whole package: these thirteen and nothing else.
_PRODUCT_CORE_CAP_ALLOWLIST = frozenset(
    {
        "MAX_SUBAGENT_DEPTH",  # subagent_contract.py — the P2 depth guard
        "MIN_SUPPORTED_CONTRACT_VERSION",  # subagent_contract.py — a version, not a cap
        "MAX_CATALOG_BYTES",  # cli/extension_catalog.py — pre-P2, unrelated
        "MAX_CATALOG_ENTRIES",  # cli/extension_catalog.py — pre-P2, unrelated
        "GIT_CLONE_TIMEOUT",  # cli/extension_catalog.py — pre-P2, unrelated
        "MAX_METADATA_BYTES",  # cli/extension_catalog.py — #68 index generator, a
        # decompression-bomb OOM guard on a dist's METADATA read; same category as
        # its three siblings above, NOT a delegation cap (governance-reviewed).
        "DEFAULT_MAX_BYTES",  # tools/_truncate.py — pre-P2, tool output
        "DEFAULT_MAX_LINES",  # tools/_truncate.py — pre-P2, tool output
        "GREP_MAX_LINE_LENGTH",  # tools/_truncate.py — pre-P2, tool output
        "STDERR_MAX_BYTES",  # rpc/rpc_client.py — pre-P2, rpc plumbing
        "DEFAULT_SEND_TIMEOUT_MS",  # rpc/rpc_client.py — pre-P2, rpc plumbing
        "DEFAULT_WAIT_FOR_IDLE_MS",  # rpc/rpc_client.py — pre-P2, rpc plumbing
        "SHUTDOWN_SIGTERM_TIMEOUT_MS",  # rpc/rpc_client.py — pre-P2, rpc plumbing
        "STARTUP_GRACE_MS",  # rpc/rpc_client.py — pre-P2, rpc plumbing
        # ADR-0201 added NO entry here, deliberately. The RPC sprint needed a
        # per-line framing budget and a reader limit, and every idiomatic name
        # for either trips ``_CAP_NAME_RE``. Both workarounds are wrong: a name
        # that hides what the constant is defeats the gate that exists to catch
        # exactly that drift, and a leading underscore exempts it silently
        # (see the ``startswith('_')`` skip below). So both are PARAMETERS with
        # ``None`` defaults and the numbers live in the bundled extension
        # (``stream.MAX_LINE_BYTES``, ``print_channel.STREAM_LIMIT_BYTES``),
        # which is where the band rule wants a policy number anyway.
        #
        # A FRAMING cap is not a DELEGATION cap — but this list cannot tell
        # them apart, so the right move was to not need an entry.
    }
)

# What counts as "cap-like": the NAME, not the value. ``= 2 * 2``,
# ``= int(os.environ[...])`` and ``= MAX_LIVE_CHILDREN`` are all caps, and
# ``MAX_CATALOG_BYTES`` in the shipped tree is already a non-literal, so a
# literal-only rule would be wrong against today's code, never mind tomorrow's.
#
# WIDENED after a review demonstrated the holes in the first spelling
# (``^(MAX|MIN)_|_(CAP|LIMIT|BUDGET|CEILING)$``), which let
# ``PARALLEL_TASKS = 8`` into ``subagent_contract.py`` with this file still
# green — a public, importable, cap-shaped name, and exactly the spelling a P4
# author reaching for "the number of tasks" picks. Two changes:
#
#   * ``(^|_)(MAX|MIN)_`` instead of ``^(MAX|MIN)_``, because the infix form is
#     not exotic — ``DEFAULT_MAX_BYTES`` and ``DEFAULT_MAX_LINES`` are already
#     in the tree, so ``DEFAULT_MAX_CONCURRENCY`` would have sailed through;
#   * the units and nouns a delegation bound is actually spelled in. Every one
#     of ``_TIMEOUT``, ``_MS``, ``_BYTES``, ``_TASKS``, ``_CHILDREN`` and
#     ``_CONCURRENCY`` is a suffix P3's own nine caps use.
#
# STILL A HEURISTIC, and a bare ``FANOUT = 8`` defeats it. That is why the seam
# file — the one place a delegation cap would plausibly land, because
# ``MAX_SUBAGENT_DEPTH`` is already there as the precedent — gets an exact-set
# rule below instead of a name pattern.
_CAP_NAME_RE = re.compile(
    r"(^|_)(MAX|MIN)_|_(CAP|LIMIT|BUDGET|CEILING|TIMEOUT|MS|BYTES|TASKS|CHILDREN|CONCURRENCY)$"
)

# Every public module-level constant ``subagent_contract.py`` declares today.
# NOT a cap rule: the seam is the CONTRACT, so the set of names it exports is
# small, deliberate and reviewable, and holding it exact catches a cap under any
# name at all — including the ones no pattern anticipates. Adding a genuine new
# contract constant is a one-line edit here, made while reading this paragraph.
_SEAM_CONSTANTS = frozenset(
    {
        "CONTRACT_VERSION",
        "MIN_SUPPORTED_CONTRACT_VERSION",
        "MAX_SUBAGENT_DEPTH",
        "DEPTH_ENV_VAR",
        "SUBAGENT_START",
        "SUBAGENT_TOOL",
        "SUBAGENT_END",
    }
)

# The nine caps P3 introduces, every one of which belongs to ``aelix_agents``.
# (a) above catches a NEW cap invented in product-core; this catches one of OURS
# being moved there — the likelier accident, because ADR-0199 tells the reader
# these numbers exist and ``subagent_contract.py`` looks like where constants go.
# All nine were verified absent from product-core before this gate was written.
_P3_CAP_NAMES = frozenset(
    {
        "MAX_CONCURRENCY",
        "MAX_PARALLEL_TASKS",
        "MAX_BATCH_WALL_MS",
        "MAX_TIMEOUT_MS",
        "MAX_TASK_BYTES",
        "MAX_DELEGATIONS_PER_PROMPT",
        "MAX_LIVE_CHILDREN",
        "PARTIAL_MIN_INTERVAL_MS",
        "BATCH_TASK_PREVIEW_CHARS",
    }
)


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


# The kernel is frozen EXCEPT where an accepted ADR authorises a change, and
# each exception is listed here with the ADR that bought it. This is not a
# weakening: the gate below still fires on every kernel file that is not on
# this list, which is the unreviewed edit it exists to catch.
#
# ``harness/core.py`` — RPC sprint, ADR-0201 §D3. The turn terminator: an
# aborted turn emitted
# NO terminal event, so every ``agent_end``-waiting parent (``RpcClient.
# prompt_and_wait``) hung for its full 60 s, and any exception that was not an
# ``AgentHarnessError`` escaped the closure block the file already contained.
# Both are Aelix regressions against pi, whose closure block is already ported
# here and whose own comment reads "Pi parity: synthesize failure assistant
# message + emit closure events" — so this is parity RESTORATION, and it carries
# no delegation surface (``test_kernel_has_no_subagent_surface`` still guards
# that, and still passes).
#
# ``harness/core.py``, SECOND hunk — ``prompt(images=...)`` never reached the
# model. Pi builds every user message through one helper,
# ``createUserMessage(text, images)`` (``agent-harness.ts:43-45``), and uses it
# for ``prompt``, ``steer``, ``followUp`` and ``nextTurn`` alike; aelix ported
# its behaviour into ``steer`` / ``follow_up`` only, so a prompt's image was
# accepted at the signature, threaded through the input hook, and dropped one
# line later. Parity RESTORATION again, and again no delegation surface.
#
# ``pyproject.toml`` — #111 B-7, the pre-beta packaging-hygiene batch. NOT a code
# change and not an ADR-bearing one: it adds ``exclude`` lists (``.omc``,
# ``__pycache__``, ``*.pyc``, ``CLAUDE.md``, ``AGENTS.md``,
# ``aelix-session-*.html``) to this package's wheel and sdist build targets, and
# touches nothing else in the file. The hole being closed was repo-wide — a
# root-anchored ``.gitignore`` rule let a nested ``.omc/state/`` escape, and
# hatchling swept maintainer ``agent-replay-*.jsonl`` session transcripts into
# published artifacts. Every published package needs the same two stanzas or the
# fix has a hole exactly where a package was skipped, so the kernel's own
# distribution metadata is in scope for the same reason its own maintenance is:
# the band rule isolates delegation POLICY, and build-file selection is not
# policy. It adds no import, no runtime behaviour and no delegation surface —
# ``test_kernel_has_no_subagent_surface`` is unaffected and still passes.
# tests/packaging/test_build_hygiene.py is the standing guard.
#
# LISTED SEPARATELY ON PURPOSE. This entry is path-based, so a second hunk in an
# already-allowlisted file passes the gate silently. The list is the record of
# WHY the kernel was opened, and a reason that is not written down is a reason
# that stops existing — which is the failure mode the RPC sprint spent itself on.
#
# ``contracts/manifest.py`` — ADR-0205, the issue #91 capability-gate family.
# DOCSTRING AND ``Field(description=...)`` ONLY: the nine ``Capabilities`` flags
# keep their names, types and ``False`` defaults, so the parsed model and every
# validator are byte-for-byte unchanged (``tests/contracts`` and the schema
# generator both confirm it — regenerating ``docs/contracts/*.schema.json``
# rewrites nothing but the new ``description`` keys).
#
# The kernel is the only place this text CAN live. Three of the nine flags are
# now enforced (``shell_exec``, ``net``, ``ui_tui_trusted``) and six remain
# documentation of intent, but all nine are identical ``= false`` lines in a
# plugin's TOML — so ``fs_write = false`` reads as a sandbox guarantee it has
# never made. ``description`` is the ONLY per-flag prose that reaches
# ``docs/contracts/manifest.schema.json``, i.e. the only channel a non-Python
# consumer (an editor's TOML schema, the web UI, a third-party packer) has for
# telling a refusal apart from a promise. Putting it in the product layer would
# leave the published contract still silent.
#
# In scope for the same reason the kernel's own maintenance is: the band rule
# isolates delegation POLICY from the kernel, and the manifest contract
# documenting its own enforcement status is not delegation policy. It adds no
# import, no runtime behaviour and no delegation surface —
# ``test_kernel_has_no_subagent_surface`` is unaffected and still passes.
# ``session/*`` — ADR-0208, the session-durability track. The JSONL session
# store lives in the kernel band and had three reproduced data-integrity
# defects: a crash-truncated line bricked ``--continue``, a valid-but-unterminated
# tail silently fused with the next append (losing the last committed turn), and
# session files were world-readable 0644 while ``auth.json`` is 0600. The
# defective code IS the kernel session layer, so the fix can live nowhere else.
# It adds no ``aelix_agents`` import, no spawn behaviour, no cap and no delegation
# surface — ``test_kernel_has_no_subagent_surface`` is unaffected and still passes
# (an independent governance review confirmed the diffs carry no delegation
# vocabulary). The single-writer lock (#46) is a follow-up and is NOT authorised here.
_KERNEL_CHANGE_ALLOWLIST = frozenset(
    {
        "packages/aelix-agent-core/src/aelix_agent_core/harness/core.py",
        "packages/aelix-agent-core/pyproject.toml",
        "packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py",
        "packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_storage.py",
        "packages/aelix-agent-core/src/aelix_agent_core/session/fs.py",
        "packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_repo.py",
        "packages/aelix-agent-core/src/aelix_agent_core/session/__init__.py",
        # ADR-0209, #122. A resumed session's persisted history must seed
        # ``_state.messages`` so ``get_session_stats``/``_get_context_usage_safe``
        # do not read zero after ``/resume``. The fix moves an existing
        # message-hydration rebuild out of ``if setup is not None:`` so it runs on
        # every replacement. Replace-assignment (idempotent, counted once), no
        # ``aelix_agents`` import, no spawn/cap/consent/delegation surface —
        # ``test_kernel_has_no_subagent_surface`` still passes (independently
        # reviewed non-delegation). Same category as the ADR-0208 session entries.
        "packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py",
    }
)


def _porcelain_path(line: str) -> str:
    """Normalise one ``git status --porcelain`` / ``diff --name-only`` line.

    Porcelain prefixes a two-column status code (`` M path``); renames arrive as
    ``R  old -> new`` and we take the destination, since that is the file whose
    content is now in the tree.
    """

    stripped = line[3:] if len(line) > 3 and line[2] == " " else line
    stripped = stripped.strip()
    if " -> " in stripped:
        stripped = stripped.split(" -> ", 1)[1]
    return stripped.strip().strip('"')


def test_kernel_untouched_vs_merge_base() -> None:
    """No kernel file changed on this branch except those an ADR authorises.

    Skipped rather than failed when the ref is unavailable (shallow clone, no
    remote, detached tarball) — an unrunnable gate must announce itself, not
    pass silently. ``test_kernel_has_no_subagent_surface`` is the always-armed
    half; this one additionally catches a NON-subagent kernel edit.

    Originally this asserted the kernel did not change AT ALL, which was right
    for P2 and P3 — both were pure delegation-policy sprints whose kernel delta
    was legitimately byte-empty. It is wrong as a permanent rule: it also forbids
    the kernel's own maintenance, including repairing a pi-parity regression the
    kernel itself claims in a comment to implement. The band rule isolates
    delegation POLICY from the kernel; it does not make the kernel unmaintainable.
    So the freeze is now by exception, and each exception names its ADR above.
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

    # Uncommitted work counts too — the kernel edit that matters most is the one
    # still sitting in the working tree.
    worktree = _git("status", "--porcelain", "--", kernel_path)
    assert worktree.returncode == 0, worktree.stderr
    changed.extend(line for line in worktree.stdout.splitlines() if line.strip())

    unauthorised = sorted(
        {
            path
            for path in (_porcelain_path(line) for line in changed)
            if path and path not in _KERNEL_CHANGE_ALLOWLIST
        }
    )
    assert unauthorised == [], (
        "the kernel changed in a file no ADR authorises — add it to "
        "`_KERNEL_CHANGE_ALLOWLIST` with the ADR that bought it, or revert: "
        f"{unauthorised}"
    )


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


def _importable_assignments(tree: ast.Module) -> list[ast.Assign | ast.AnnAssign]:
    """Every assignment reachable from the MODULE namespace.

    Descends through ``if`` / ``try`` / ``with`` and into class bodies — a cap
    can be conditional (``if sys.platform == "linux": MAX_X = 4``) or a class
    attribute and still be importable, and a body-only scan of ``tree.body``
    would miss both. Stops at function boundaries, for the same principled
    reason the rule skips ``_``-prefixed names: a function-local UPPER_SNAKE is
    unreachable from outside the module, so it cannot be a policy surface the
    extension band reads.
    """

    found: list[ast.Assign | ast.AnnAssign] = []
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Assign | ast.AnnAssign):
            found.append(node)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def test_product_core_declares_only_the_allowlisted_caps() -> None:
    """S9 — the exact set of cap-like constants product-core may declare.

    ADR-0197 verbatim: "Explicitly **NOT** done ... any spawn behaviour, **cap**,
    registry or consent policy in product-core." Until this test, nothing
    enforced it — and ``MAX_SUBAGENT_DEPTH`` living in ``subagent_contract.py``
    is precisely the precedent a P4 author would cite for dropping
    ``MAX_CONCURRENCY`` beside it. P3's caps (concurrency, wall clock, task
    bytes, per-prompt budget) are the extension's policy: they exist because
    ``aelix_agents`` chose them and a different runtime may choose others.

    Matched on the NAME, not the value: ``= 2 * 2`` and
    ``= int(os.environ[...])`` are caps too, and both ``ast.Assign`` and
    ``ast.AnnAssign`` count — ``MAX_CONCURRENCY: Final[int] = 4`` is the
    idiomatic modern spelling and an ``Assign``-only rule would pass vacuously
    against it.
    """

    declared: dict[str, list[str]] = {}
    for path in _python_files(PRODUCT_CORE_SRC):
        rel = path.relative_to(PRODUCT_CORE_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _importable_assignments(tree):
            targets = (
                [t for t in node.targets if isinstance(t, ast.Name)]
                if isinstance(node, ast.Assign)
                else ([node.target] if isinstance(node.target, ast.Name) else [])
            )
            for target in targets:
                name = target.id
                # PUBLIC UPPER_SNAKE only, and the reason is principled rather
                # than convenient: a constant that governs the extension band
                # has to be importable to BE a policy surface, and a
                # module-private ``_STDOUT_CAP`` cannot be read by
                # ``aelix_agents`` at all.
                if name.startswith("_") or name != name.upper():
                    continue
                if _CAP_NAME_RE.search(name):
                    declared.setdefault(name, []).append(f"{rel}:{node.lineno}")

    unexpected = {n: locs for n, locs in declared.items() if n not in _PRODUCT_CORE_CAP_ALLOWLIST}
    missing = sorted(_PRODUCT_CORE_CAP_ALLOWLIST - declared.keys())

    assert not unexpected, (
        "product-core declared a cap-like constant that is not on the "
        "allowlist. ADR-0197: 'Explicitly NOT done ... any spawn behaviour, "
        "CAP, registry or consent policy in product-core' — the caps that "
        "bound delegation (concurrency, wall clock, task size, per-prompt "
        "budget) are aelix_agents' policy, and a different subagent runtime "
        "gets to choose different ones. Put it in the extension, or amend "
        f"_PRODUCT_CORE_CAP_ALLOWLIST deliberately: {unexpected}"
    )
    # The other direction, so the allowlist cannot quietly rot into a list of
    # names nothing declares any more and stop discriminating.
    assert missing == [], (
        f"_PRODUCT_CORE_CAP_ALLOWLIST names constants that no longer exist: {missing}"
    )


def _public_module_constants(path: Path) -> dict[str, str]:
    """Every public UPPER_SNAKE name a module binds at import time."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in _importable_assignments(tree):
        targets = (
            [t for t in node.targets if isinstance(t, ast.Name)]
            if isinstance(node, ast.Assign)
            else ([node.target] if isinstance(node.target, ast.Name) else [])
        )
        for target in targets:
            name = target.id
            if name.startswith("_") or name != name.upper():
                continue
            found[name] = f"{path.name}:{node.lineno}"
    return found


def test_the_seam_declares_exactly_its_contract_constants_and_nothing_more() -> None:
    """S9, closed at the one file a delegation cap would actually land in.

    :data:`_CAP_NAME_RE` is a heuristic and a review demonstrated its holes:
    appending ``PARALLEL_TASKS = 8`` to ``subagent_contract.py`` left this file
    green, and ``FANOUT = 8`` still evades the widened pattern. A pattern cannot
    be finished, because the drift is a NAME someone has not thought of yet.

    So the seam is held to an exact set instead. It can afford one: it is the
    CONTRACT — types, a Protocol, a version and three event names — and its own
    docstring already forbids it machinery (``test_product_core_never_spawns``
    asserts it imports neither ``subprocess`` nor ``asyncio``). Any new public
    constant here is a deliberate edit to :data:`_SEAM_CONSTANTS`, and a cap
    lands as a red test whatever it is called.
    """

    declared = _public_module_constants(PRODUCT_CORE_SRC / "subagent_contract.py")

    unexpected = {n: loc for n, loc in declared.items() if n not in _SEAM_CONSTANTS}
    assert not unexpected, (
        "subagent_contract.py declared a new public constant. The seam carries "
        "the CONTRACT and no policy of its own (ADR-0197 §1.2): the caps that "
        "bound delegation are aelix_agents' (ADR-0199 §2), and MAX_SUBAGENT_DEPTH "
        "sitting here is the precedent a future author would cite for putting "
        "one beside it. Add it to _SEAM_CONSTANTS deliberately, or put it in the "
        f"extension: {unexpected}"
    )
    missing = sorted(_SEAM_CONSTANTS - declared.keys())
    assert missing == [], (
        f"_SEAM_CONSTANTS names constants subagent_contract.py no longer has: {missing}"
    )


def test_the_p3_cap_names_never_appear_in_product_core() -> None:
    """S9's other half — none of OUR caps migrates across the band.

    An AST scan for the name in a BINDING position (an assignment target, a
    function parameter, an imported alias, or a load), deliberately NOT a raw
    substring scan the way ``SpawnGrant`` is scanned above. Naming the grant
    type *is* how consent policy leaks; naming a cap is not.
    ``subagent_contract.py`` narrates what the extension does — its
    ``MAX_SUBAGENT_DEPTH`` docstring is exactly that — so a future sentence like
    "the extension bounds fan-out with ``MAX_CONCURRENCY``" would go red for
    documentation that is correct and desirable, and the cheapest fix would be
    to delete the signpost ADR-0199 wanted.
    """

    hits: list[str] = []
    for path in _python_files(PRODUCT_CORE_SRC):
        rel = path.relative_to(PRODUCT_CORE_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # ``ast.Name`` covers Store (an ``Assign``/``AnnAssign`` target) and
            # Load (a use) in one; ``ast.arg`` covers a parameter; ``ast.alias``
            # covers ``from aelix_agents.batch import MAX_CONCURRENCY``, which
            # binds the name here even though no ``Name`` node mentions it.
            if isinstance(node, ast.Name):
                bound = node.id
            elif isinstance(node, ast.arg):
                bound = node.arg
            elif isinstance(node, ast.alias):
                bound = (node.asname or node.name).rsplit(".", 1)[-1]
            else:
                continue
            if bound in _P3_CAP_NAMES:
                hits.append(f"{rel}:{node.lineno}: {bound}")

    assert hits == [], (
        "a P3 delegation cap has moved into product-core. These nine numbers "
        "are aelix_agents' policy (ADR-0199); product-core carries the "
        f"CONTRACT and no bounds of its own (ADR-0197 §1.2): {hits}"
    )
