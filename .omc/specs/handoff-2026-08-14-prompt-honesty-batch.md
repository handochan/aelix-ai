# Handoff — the prompt-honesty batch: #161 → #120 → #162 (+ #167)

Written at main `e087378`. Every claim below is marked **[measured]** (I ran it and saw the
output) or **[read-only]** (I read the code and did not run it). That distinction is not
decoration: the handoff *this* session inherited carried three false claims and each cost real
time. There are no unmarked assertions here. If you find one, treat it as a bug in this document.

---

## State

| item | value |
|---|---|
| `origin/main` | `e087378` [measured] |
| suite | **8603 passed / 1 skipped** [measured, full run] |
| `ruff check packages/ tests/ scripts/` | clean [measured] |
| `scripts/check_types.py` | 257 files / 0 errors, narrowing spike still inverted [measured] |

⚠️ **`/workspaces/aelix-ai` (the owner's main checkout) is at `02f9a8f` — SEVEN commits behind**
[measured]. Do not edit it; the owner runs concurrent sessions there. It needs `git pull`.
This bit me at the start of this session: `grep` for `skills_prompt.py` returned "not found" and I
briefly doubted a correct issue comment. **Check `git rev-parse origin/main` before believing a
negative grep.**

Worktrees, both merged and clean [measured]: `/workspaces/aelix-121`, `/workspaces/aelix-101`.
Start a fresh one off `origin/main`.

Shipped this session: **#121** (`a079188`, ADR-0217) and **#101** (`e087378`, ADR-0218).
#101 is deliberately **left open** — one completion criterion needs a live model (below).

---

## Why these are ONE batch

All three edit the **same two functions in one file** [measured, current line numbers]:

```
packages/aelix-coding-agent/src/aelix_coding_agent/cli/agent_context.py
  :247  _docs_signpost()          <- NEW this session (#101)
  :403  _extension_signpost()     <- #161 owns this, #162 takes 4 of its 5 paths
  :648  build_system_prompt()     <- #120 owns :689, #162 takes the 5th path at :719
```

Doing them separately means re-deriving the same measurements three times, and each round
invalidates the previous round's docstring probes — this file's own module docstring requires that
rewordings **re-run the probe**, not merely sound safer.

**#167 belongs in this batch too.** I filed it during #101 review; it touches **five call sites,
four of which are in these same two functions** [measured]. Sequencing it apart guarantees a
fourth pass over the same code.

Suggested order, and the reason: **#120 → #161 → #167 → #162.**
#120 and #161 are independent halves. #167 changes how paths are *escaped*; #162 changes *which*
paths are emitted — so #167 first means #162 redacts a surface whose encoding is already settled.

---

## #120 — the hardcoded tool list

**The sentence** [measured]: `agent_context.py:689`, `"…Available tools: read (read a file),
write …, edit …, bash …, grep …, find …, ls (list a directory)."`

**It is already wrong by TWO, before you touch anything** [measured]: `agent`
(`aelix_agents`, `AGENTS_TOOL_NAME = "agent"`) and `aelix_status` (`aelix_status`, new in #101)
both ship, both register via the extension-prepend route, and neither is in the sentence. So this
batch is not making it worse — it is the reason to fix it.

### 🔴 The crux, and it kills the obvious design [measured]

The sentence is wrong in **two opposite directions**, and only one is fixable where it sits.

1. **Overstatement** — it names tools that `--no-tools` / `--no-builtin-tools` / `--tools`
   removed. Fixable from `Args` alone.
2. **Understatement** — it omits `agent` and `aelix_status`. **Not fixable at this site**, because
   the prompt is built *before* those tools exist:

```
entry.py:1206   system_prompt = _resolve_system_prompt(parsed, cwd)
entry.py:1247   prepend_extensions.append(agents_ext)
entry.py:1262   prepend_extensions.append(StatusExtension(...))
```

**The handoff I inherited said `cli/skills_prompt.skills_catalog_visible` "is a usable foundation".
That is half true and the half that fails is the one #120 needs** [measured — I read the function
and its docstring]. It is a **name-level predicate over `Args`** (`no_tools`, `no_builtin_tools`,
`active_tools`) and its own docstring says so: *"the built-in registry is the only source of
`read`, and that filter is applied AFTER registration (`entry.py` post-build), so a name-level
check cannot see it."* It therefore solves direction (1) and is structurally blind to (2).

So decide the shape before writing code. Three options, costs stated honestly:
- **(a) Make the sentence flag-aware only.** Cheap; closes overstatement; leaves the sentence
  understating by two, which is the state that made #120 worth filing.
- **(b) Move the enumeration out of the base prompt into an append chunk computed after
  registration.** Closes both; changes prompt ordering and every test that pins the base prompt.
- **(c) Delete the enumeration.** The recon claims tool schemas are in every provider request
  already, so the prose may be redundant — **[read-only, from a recon agent; RE-MEASURE before
  relying on it]**. pi has an equivalent sentence, so this is a pi-divergence question, not just a
  cleanup.

I did not choose. It is an owner-shaped call and it decides the whole issue.

---

## #161 — who chooses global vs project-local

**Site** [measured]: `_extension_signpost()` at `agent_context.py:403`; the two write targets are
emitted at `:483` (global, `get_agent_dir()/extensions`) and just below it (project-local,
`<cwd>/.aelix/extensions`).

**The shaping constraint is in the issue and I did not re-measure it** [read-only]: the permission
layer is allow/deny only, so it cannot offer "write here instead". Whatever asks the user has to
be its own surface.

**Matters more after ADR-0216** [read-only, from ADR-0216]: implicit project trust now *persists*,
so project-local extensions become durably trusted more easily, and the write moment is the last
point a human sees the choice.

**Landmine** [measured, this session]: the signpost's ordering and wording are load-bearing and
each claim carries its probe. In particular the global target is listed **first** because the
project-local tier **fails silently** when untrusted (`no_project_local=not project_trusted`).
Any reordering must re-run that probe.

---

## #162 — the 5 absolute paths

**Measured this session**, `build_system_prompt("/home/codespace/myproject")`, 3363 chars at the
time (now larger — #101 added ~632 bytes):

```
/home/codespace/.aelix/agent/extensions                    <- $HOME-derived: OS USERNAME
/home/codespace/myproject                                  <- cwd
/home/codespace/myproject/.aelix/extensions
<install prefix>/aelix_coding_agent/examples/echo/echo.py
<install prefix>/aelix_coding_agent/extensions/api.py
username present in prompt: True
```

Emission sites [measured]: `:483` and the project-local line (both `_extension_signpost`), `:505`
and `:510` (`_package_pointer`), `:719` (`- Working directory: {cwd_abs}` in
`build_system_prompt`). #101 added a sixth: the bundled docs directory in `_docs_signpost`.

**`--no-context-files` removes NONE of them** [measured] — it drops `AGENTS.md` content, the
fence and the `path=` attributes only. That is why #121's privacy criterion was re-scoped to
documentation and this issue was split out.

**Constraint that shapes any fix** [read-only, ADR for #117]: the paths are absolute *deliberately*
— a bare `python` in the bash tool resolves to a different interpreter than the `uv tool install`
venv, which is why the signpost hands the model absolute file paths. Any redaction must re-run
that probe, not merely sound safer.

---

## #167 — one path-shaped escape (filed by me, unstarted)

**Measured**, package copied under `/tmp/amp&test_…/r&d/src`:
- `_docs_signpost` emits `…/r&amp;d/…` and `is_dir()` on the emitted string is **False**, while the
  real directory's is `True`. `_escape_text` escapes `&`, and `&` is legal in a POSIX path.
- `_extension_signpost`'s two `_package_pointer` targets are emitted **raw** — the opposite defect,
  and the ADR-0217 fence channel `a079188` closed for the cwd is still open there.

Proposed fix, **one** escape shared by five sites: strip C0/DEL/C1, escape `<`, **do not touch
`&`**. A path is not XML element text; `<` alone carries the structural guarantee ADR-0217 rests
on. Claims in `_docs_signpost`'s docstring and ADR-0218 §2 were corrected to state this residual
rather than imply it was handled.

---

## Owner decisions — do not re-litigate

- **ADR-0216 governs**: pi's own behaviour is not overridden without a reason that survives *every*
  upstream sync. The question for each option is **parity / forward-sync / divergence**, not
  "is it safer".
- **ADR-0217**: `AGENTS.md` project context stays trust-independent (pi's *published* policy —
  `packages/coding-agent/docs/security.md:27` and `:37`; the top-level path 404s).
- **#101 scope**: `aelix docs` + bundled guides + prompt block + `aelix_status`; `aelix status` CLI
  optional and not built.
- **#162 was split from #121** deliberately, on the grounds that the paths come from #117's
  signpost and are out of #121's scope.

---

## Landmines [all measured this session]

- **Citations rot with every insertion, and adding the diff delta is wrong.** #101's first commit
  added 65 `file:line` citations and moved code under others: **9 targets wrong across 13 sites**,
  two of them created by the fix round itself. Re-derive by locating the cited construct.
- **`entry.py` has 53 citations pointing at lines ≥ 1600.** Edits above that line invalidate all of
  them. One comment fix this session was deliberately kept line-neutral for exactly this reason.
- **`agent_context.py` grew ~50 lines mid-file this session.** Every `agent_context.py:NNN`
  citation with NNN ≥ 308 in *other* files shifted.
- **A globbing guard passes over whatever the glob misses, not just over an empty corpus.** Four
  independent `docs/guides/` guards shared one non-recursive `*.md` glob and were all blind to a
  nested guide at once.
- **The prompt has a byte budget with a test.** Base prompt went 3362 → 3994 (+632, +18.8%) when
  #101's docs block landed; `test_the_block_prose_stays_within_its_measured_budget` asserts the
  block's prose < 640. Anything this batch adds competes with that.
- **A test double that omits what production omits proves nothing**, and **a passing gate proves
  nothing until it has been run against the *unfixed* build.** Both bit this repo again this
  session (the wiring tripwire pinned kwarg *names*, so a `project_trusted` fail-open sabotage left
  the whole suite green).
- Worktree pytest needs `PYTHONPATH` set to that worktree's four `packages/*/src`, or it imports
  another checkout's copy. Verify with
  `python -c "import aelix_coding_agent; print(aelix_coding_agent.__file__)"` before trusting a run.
- `uv build --package <name>` builds the wheel **from the sdist**; `--wheel` / `--sdist` do not.
- `python -m build` is **not installed**; use `uv build`.

---

## Open, and not this batch

- **#101's live-model criterion.** "The skill alone drives docs → write → check → reload" is
  unverified; no real model was run against any of #101 or #121. Everything is measured on bytes,
  files, exit codes and real `tool.execute()` output. Given this repo's record on prompt work, that
  is the gap I would close first — and it would cover this batch's prompt changes too, so consider
  doing it once, after the batch, rather than per-issue.
- **`aelix status` CLI adapter** — optional in #101; seam is `StatusExtension.snapshot()` →
  `RuntimeSnapshot.to_dict()`. Do not re-implement the projection or the redaction rules fork.
- **`AgentsExtension` in the `/extension` viewer** — it is built-in but not always-on
  (default-off + depth-gated), so it does not satisfy the renamed `_BUILTIN_ALWAYS_ON_NAMES` rule.
  Pre-existing, recorded in ADR-0218.
- **14 citations in files #101 did not touch** point into regions its insertions moved. Each was
  verified as **already stale at `130acfd`**, so nothing was lost — but a repo-wide citation sweep
  is a real, separable job.

---

## Next free ADR

**0219** — 0218 is the newest [measured]. The index table in `docs/decisions/README.md` is
**descending and not sorted by number** (0093 sits above 0217); insert next to the ADR you are
following, not in numeric order. Each row is one physical line with exactly 5 pipes.
