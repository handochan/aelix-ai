# 0216. Project trust on reload follows pi, rather than diverging toward deny

Status: Accepted (2026-08-13).
Date: 2026-08-13
Relates: ADR-0203 (the `.env` trust hole), ADR-0214 (`.aelix/skills` + `prompt-templates` joined the
trust predicate), Sprint P0 #10 (the original Project Trust landing).
GitHub: #112 — closed as **wontfix, by parity**, with the residual recorded there.

**Provenance.** Parity restoration and a parity *gain*. Nothing here is aelix-original; two of the
three pieces were simply never ported, and the third is a command pi has and aelix did not.

---

## The problem

#112 reported a chain, and it reproduces at `884e686`:

```
[reload/nofix] startup: predicate=False project_trusted=True vacuous=True
[reload/nofix] SENTINEL(exec_module ran)=True  commands={'evil'}
```

A session started in a directory with nothing to gate is trusted without being asked
(`resolve_project_trusted` step 2). If a `.aelix/extensions/*.py` appears afterwards — a
`git pull`, a teammate, or the agent running `git pull` through bash — the next harness build
`exec_module`s it, with no prompt ever shown.

Two measured corrections to the issue's framing:

- **It is not only `/reload`.** `/new`, `/fork`, `/resume` and `/switch` all go through the same
  factory, and RPC exposes `new_session` / `fork` / `switch_session`. `reload_seed is not None`
  is the wrong discriminator.
- **`cli/repl.py` is not a hole.** Its `/reload` calls `reload_resources()`, which reads no
  filesystem.

Defect 1 of the issue (`.aelix/` writes auto-approved) was already fixed by 871a6be, which removes
the most direct agent-driven path: the agent can no longer write there without approval.

## The decision

**Match pi. Do not gate.**

A gating fix was designed and measured working — re-decide trust at the head of every rebuild and
refuse newly-appeared resources, with `/trust` to recover in-session:

```
[reload/fix]       SENTINEL=False  notices=['...appeared; not loaded (restart to decide)']
[reload/fix+store] SENTINEL=True   notices=[]
```

It was **declined**. The reasoning, recorded because it is the kind of decision that gets
re-litigated at every upstream sync:

- pi keeps this surface permissive deliberately, and aelix is a pi port. A divergence here would
  have to be re-justified every time pi moves, and "we are stricter than pi at trust" is a claim
  that needs to survive that repeatedly, not once.
- The case for diverging was weaker than it first appeared. Defect 1 is already closed, so the
  remaining trigger requires a file to arrive from outside during a live session.
- The two prior security divergences are not precedent for this one. ADR-0203 covered a surface
  pi does not have; ADR-0214's skills gate was pi *parity* (`trust-manager.ts` lists skills). This
  would have been the first case where pi's own behaviour is the thing being overridden.

### What is ported

1. **The flag** — pi `main.ts:708-711`. `auto_trust_on_reload_cwd` is set at startup when the user
   passed no explicit `--approve`/`--no-approve` **and** the directory had nothing to gate. That
   pair is exactly "this session is about to be trusted without anyone being asked". Computed
   before `resolve_project_trusted`, because after a reload the predicate answers differently.
2. **The persist** — pi `interactive-mode.ts:4693-4717`, called from its `/reload` tail at
   `:5756`. Once resources appear and have been loaded, the implicit trust is written to
   `trust.json` so the next launch does not re-ask about a directory the user has been working in.
3. **`/trust`** — pi `interactive-mode.ts:2953`. aelix had no way to answer the trust question
   after startup at all: the one-shot selector runs before `run_tui` exists, so a user who
   declined, or who was never asked and then saw resources appear, could only restart.

### What this explicitly does NOT do

The persist runs **after** the reload, exactly as pi runs it after `session.reload()`. By the time
it is called the new resources have already loaded. **pi's mechanism is not a safeguard** — it
converts "never asked" into "granted, and remembered". Anyone reading this later and taking it for
a gate will be wrong, which is why the function's own docstring says so first.

The consequence, stated plainly rather than buried: a repo whose `.aelix/extensions` arrives
mid-session is trusted without a prompt, and now *durably* — where previously the implicit trust
died with the process. That is a real widening relative to aelix's prior behaviour, accepted as
the price of parity, and it is why `/trust` ships in the same commit: it is the one place a human
can still say no.

## Consequences

- Verified live, not only in tests: an empty project shows **0** trust prompts; a `.aelix/extensions`
  planted mid-session and a `/reload` produce `Saved project trust for this folder` and a
  `trust.json` entry; `/trust` opens the selector.
- The guard that matters most is pinned by its own test: an implicit write must never overwrite an
  explicit decision, least of all an explicit `False`. Without pi's `store.get(cwd) !== null`
  check, a user who answered "Do not trust" would have it silently flipped by a later reload.
- A store that cannot be read or written returns `False` and is swallowed. Failing to persist must
  never fail a reload the user asked for.
- One deliberate micro-divergence: pi also clears the flag when it finds the store already holds a
  decision, purely to skip later work. Here the flag is cleared only on a successful write, which
  costs one extra `trust.json` read per reload and keeps the clearing rule to one sentence.
- Follow-up, filed separately: the user — not the #117 signpost — should choose whether a new
  extension goes to the global (ungated) or project-local (gated) directory. That choice matters
  *more* under this ADR, because project-local trust now persists, making it the last point a
  human sees the decision.
