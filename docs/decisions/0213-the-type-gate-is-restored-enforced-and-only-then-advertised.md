# 0213. The type gate is restored, enforced in CI, and only then advertised with `py.typed`

Status: Accepted (2026-08-12).
Date: 2026-08-12
Relates: ADR-0197 (the band rule — the kernel `aelix-agent-core` carries no delegation policy;
a kernel edit that adds no delegation surface is authorised by exception, naming its ADR).
ADR-0205 / ADR-0208 / ADR-0209 / ADR-0210 / ADR-0211 / ADR-0212 (the same exception mechanism,
for the manifest contract's documentation and for session/stats data-integrity fixes).
GitHub: #140.

**Provenance.** pi is TypeScript and has no analogue of a Python type-checker gate or of PEP
561. Everything here is aelix-original tooling policy; nothing is parity restoration or a
parity divergence.

---

## The problem

The pyright gate was configured and had never run on the product.

```
include = ["src", "packages/*/src", "scripts"]   ->  filesAnalyzed: 8
```

`packages/*/src` matched **nothing**. The eight files it did analyse were this repo's own
two-file `src/` and six-file `scripts/`, and every error it could report came from a spike
script. The 239 files of product source across the four packages had never been type-checked
at all — and because a gate that analyses nothing reports zero errors, it read exactly like a
clean tree. pyright also appeared in no workflow, so nothing would have noticed either way.

This is not tidiness. In the week before this ADR the repo shipped a defect that a green test
suite and correct live captures both missed and that pyright would have caught in one line: a
round that widened a tuple 2→3 and read `entry[2]` without updating the return annotation
(#151). The gate that exists for that class of defect was switched off by a glob.

## The decision

**Three steps, strictly ordered. Each must be complete before the next begins.**

### 1. Make the gate see the tree

`include` becomes `["src", "packages/**/src", "scripts"]` — `**` expands where `*` did not,
and it keeps covering a package added later. `extraPaths` is added alongside it, because the
config named no paths at all and import resolution therefore depended on whatever environment
happened to be active: the editable installs' `.pth` files hard-code the main checkout's
absolute paths, so the same commit reported 55 errors inside a git worktree and 44 on main.
With relative `extraPaths` every checkout — worktree, fresh clone, CI — type-checks its own
tree and they agree. Shipped separately in `1d82fe2`, because the count the rest of this ADR
quotes is meaningless without it.

**An include that matches nothing is indistinguishable from a clean tree.** Any future change
to this config must be checked by asserting `filesAnalyzed`, never by reading an error count.

### 2. Triage what that exposes, and only then put it in CI

44 errors appeared, of which 8 are the spike's (see below) and 36 were the product's. All 36
are resolved; the product tree is at **0**. They were not uniformly cosmetic, and the split is
the useful record:

- **Real defects, fixed.** `ModelRegistry` adopted whatever a duck-typed `modify_models`
  provider hook returned — including `None` — into `self._models`; the surrounding `try`
  caught a hook that raised but not one that returned garbage, so every later
  `get_available()` would break with nothing in `get_error()`. `models.json` is user-editable
  and its untyped lookups fed `Model(api=...)`/`base_url=...` directly, so a mistyped entry
  became a `Model` holding a dict. `run_tui` declares `model_registry` optional while
  `find_initial_model` takes it required and dereferences it — the enclosing `except` would
  have shown the user `'NoneType' object has no attribute …` verbatim. `rpc_types` bound a
  `str | None` to a local it declared `str`, under a comment claiming the binding existed to
  satisfy exactly that check.
- **False self-descriptions, corrected.** `_transform_assistant_content` returned
  `ThinkingContent` and had done since Sprint 6b, while both its return annotation and its
  docstring said thinking blocks were not part of the union — contradicted three lines below
  by its own branch comment. In `loader.py` a `cast` on the argument of an overloaded `on()`
  could not work (no per-`Literal` overload accepts the union), and the comment justifying it
  cited "the 8-error baseline" — a baseline made up entirely of the spike.
- **Narrowing limits, expressed rather than suppressed.** `event.type in ("end", "done")`
  became `== … or == …`: identical at runtime for a `Literal` field, but pyright narrows on
  `==` and not on `in`, which alone accounted for ten of the 44. `asyncio.create_task` on a
  sink typed `Awaitable` became `ensure_future`, which is what the sink's declared type
  actually permits.
- **Interface mismatches with third-party bases.** A `Processor.apply_transformation` override
  renamed its parameter to the base's (the old name worked only because prompt_toolkit calls
  it positionally), and a `Container.get_children` override narrowed its return to the base's.

pyright then joins CI as a required gate. It was absent entirely; a restored gate that nothing
runs is the same no-op in a different spelling.

### 3. `py.typed`, last

The marker ships to all five distributions only after step 2 is green, and CI keeps it green.
`README.md` advertises "the typed `HookBus`" while a consumer importing
`aelix_agent_core.harness.hooks` gets `reportMissingTypeStubs`, so the marker is owed. But a
PEP 561 marker on a tree that has never been fully type-checked is a *new* false
advertisement, and this project has spent the beta removing those. Marker after clean, never
before.

## The spike is an inverted gate, not a file to fix

`scripts/pyright_spike.py` is `# pyright: strict` and ends with three deliberate negative
assertions under the comment *"Inverse cases — pyright MUST emit errors for narrowing to be
considered working"*. Its **8 errors are its passing state**; `sprint-2-phase-1-3-spec.md`
records `8 errors (narrowing alive)` as the expected output, and `hooks.py` and
`extensions/api.py` both cite it as the check that their overloads still narrow.

So it can be neither fixed nor merely deleted. It leaves the gate's `include` — a file
designed to error can never let the gate be green — and gains its own CI step that runs
pyright against it and fails if the error count is **zero**. A silent narrowing regression in
the hook API now breaks the build instead of quietly improving a number.

## Why this touches the kernel, and how far

`packages/aelix-agent-core/src/aelix_agent_core/loop.py` changes: the `in`→`==` narrowing
rewrite, and `create_task`→`ensure_future` with the accumulator retyped from
`list[Task[None]]` to `list[Future[AgentMessage | None]]` (the old element type was wrong on
both counts). The band rule asks one question — *did delegation create this requirement?* It
did not: the requirement is a repo-wide tooling gate, and `loop.py` is simply where two of its
findings live. The edits add no import, no symbol, no spawn site, no cap, no consent path and
no registry; `test_kernel_has_no_subagent_surface` is unaffected. The `in`→`==` rewrite is
semantically identical for a `Literal` field, and `ensure_future` does exactly what
`create_task` did for every `async def` sink this repo has while no longer crashing on the
`Awaitable` the type permits.

`py.typed` also lands in the kernel distribution, which is packaging metadata rather than
code.

## Consequences

- The gate analyses 247 files instead of 8, and CI fails on a product-code type error.
- Checkouts agree: worktree, clone and CI produce the same count.
- The spike's 8 errors are load-bearing and asserted to remain non-zero.
- All five distributions ship `py.typed`, and the README's "typed" claim becomes true.
- Never trust an error count from this gate without `filesAnalyzed` beside it.
