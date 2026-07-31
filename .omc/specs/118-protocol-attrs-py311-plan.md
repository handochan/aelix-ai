# #118 — main CI red on py3.11: `_protocol_members` has no 3.11 tier

Branch `fix/118-protocol-attrs-py311`, worktree `/workspaces/aelix-118`, rebased onto `main` @ `732fc71`.

Closes **#118**. main has been red on the py3.11 CI job since `bb24295` (P3, 2026-07-28) — i.e. through
the P3, W1-A, #117, RPC and #114 merges. All four shipped packages declare
`requires-python = ">=3.11"` and the CI matrix is `["3.11", "3.12"]`
(`.github/workflows/ci.yml:37`), so the suite is broken on the **minimum version the project
advertises**, immediately before the first beta tag.

## 1. Scope is exactly one test — measured, not assumed

Full suite on real CPython 3.11.15, this worktree, rebased onto current main:

```
py3.11 : 1 failed, 7380 passed, 1 skipped   in 246s
py3.12 : 0 failed, 7381 passed, 1 skipped   in 278s
```

`7380 + 1 = 7381` — same collection, exactly one test fails on 3.11. **This is not a broader
portability pass.** A repo-wide grep for `__protocol_attrs__` / `get_protocol_members` /
`typing._*` returns hits in exactly one file.

## 2. The defect

`tests/agents/test_subagent_contract.py:556-568`:

```python
def _protocol_members(protocol: type) -> frozenset[str]:
    """...``typing.get_protocol_members`` only exists from 3.13; ``__protocol_attrs__``
    is what it reads and is present on every ``Protocol`` subclass back to 3.8...."""
    public = getattr(typing, "get_protocol_members", None)
    if public is not None:          # 3.13+
        return frozenset(public(protocol))
    return frozenset(protocol.__protocol_attrs__)   # assumes 3.12+
```

The docstring's premise is **false**. `__protocol_attrs__` was added in **Python 3.12** by the PEP 544
runtime refactor, not 3.8. On 3.11 the members are not a class attribute at all — they are computed by
the module-private `typing._get_protocol_attrs(cls)`.

So the chain has a hole: 3.13+ → public function, 3.12 → class attribute, **3.11 → AttributeError**.

Measured on real 3.11.15:

```
typing.get_protocol_members (3.13+) : absent
Protocol.__protocol_attrs__ (3.12+) : absent      <- AttributeError raised here
typing._get_protocol_attrs  (3.11)  : present, returns the correct member set
```

## 3. The fix

Insert the missing tier:

```python
public = getattr(typing, "get_protocol_members", None)
if public is not None:                      # 3.13+
    return frozenset(public(protocol))
attrs = getattr(protocol, "__protocol_attrs__", None)
if attrs is not None:                       # 3.12
    return frozenset(attrs)
return frozenset(typing._get_protocol_attrs(protocol))   # 3.11
```

and correct the docstring, which currently states a false fact that would mislead the next reader.

### The design question the implementer must settle explicitly

`typing._get_protocol_attrs` is a **private** API. Normally that is a smell. Here there is a specific
argument that it is acceptable, and the implementer should either adopt it (and record it in a comment)
or reject it with a better alternative:

> The private name is reached **only on 3.11**. 3.11 is feature-frozen — it receives security fixes
> only, so `typing._get_protocol_attrs` cannot be removed or changed there. Every version that could
> still evolve (3.12, 3.13+) is served by a non-private tier that is tried first. The private access is
> therefore pinned to a version that cannot move under it.

Alternatives to weigh and reject-with-reason if not chosen: re-deriving members from `__annotations__`
plus the class dict (re-implements typing's rules, risks silent drift from the very thing the test
exists to pin), or `dir()`-diffing against a bare `Protocol` base.

Whichever is chosen, the failure mode must be **loud**: if no tier can produce members, raise with a
message naming the interpreter version, never return an empty frozenset — an empty set would make
`test_the_protocol_member_set_is_frozen` pass vacuously, which is worse than the current crash.

## 4. Also worth settling

- **The 3.13+ tier has never executed.** CI runs 3.11 and 3.12 only, so
  `typing.get_protocol_members` is dead code that has never been exercised. Validating the helper (not
  the whole suite) against a real 3.13 is cheap — `uv` can fetch `cpython-3.13.13` — and turns three
  claimed tiers into three verified tiers. Do it if it costs one command; do not turn it into a CI
  matrix change (that is an owner decision, out of scope here).
- **Why this survived three days.** The CI job did its job — it went red and stayed red. Nothing in the
  repo makes a red matrix leg block a merge. That is a process/branch-protection question for the
  owner, NOT something to fix in this lane. Note it, do not act on it.

## 5. Lane isolation

Owns: `tests/agents/test_subagent_contract.py` only.
Does NOT touch: any `packages/**` source, `.github/workflows/**`, or any other test file. If the fix
appears to require production source, STOP and report — that would mean the diagnosis is wrong.

## 6. Verification gate — DUAL INTERPRETER, this is the distinctive requirement

A fix verified on only one interpreter is worthless here; the bug exists precisely because 3.11 was
never run locally.

```
# 3.11 — the venv built for this worktree, already bound to it
cd /workspaces/aelix-118 && ./.venv311/bin/python -m pytest -q -p no:cacheprovider

# 3.12 — the shared editable venv; PYTHONPATH is MANDATORY or it imports /workspaces/aelix-ai
cd /workspaces/aelix-118 && source /workspaces/aelix-ai/.venv/bin/activate && \
PYTHONPATH="/workspaces/aelix-118/packages/aelix-coding-agent/src:/workspaces/aelix-118/packages/aelix-agent-core/src:/workspaces/aelix-118/packages/aelix-ai/src:/workspaces/aelix-118/packages/aelix-server/src:/workspaces/aelix-118/src" \
python -m pytest -q -p no:cacheprovider
```

Print `aelix_coding_agent.__file__` on BOTH and assert it starts with `/workspaces/aelix-118`.
(The 3.11 venv resolves correctly on its own — `uv sync` bound it to this worktree — but assert anyway.)

Acceptance:

- [ ] py3.11 full suite: **0 failed** (baseline was 1 failed / 7380 passed)
- [ ] py3.12 full suite: still 0 failed, count unchanged
- [ ] `_protocol_members` returns the SAME frozenset on 3.11 and 3.12 for `SubagentRuntime` — print both
- [ ] The no-tier-available path raises loudly rather than returning an empty set — prove by test
- [ ] ruff clean
- [ ] (if cheap) the 3.13 tier executes correctly on real 3.13

## 7. Known pre-existing flakes — never report as a regression, never fix here

`tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored`,
`tests/agents_ext/test_print_channel_spawn.py::test_a_child_that_finishes_at_the_deadline_is_not_a_timeout`,
and assorted tui timing tests under host load. Re-run a named test alone before concluding anything.
