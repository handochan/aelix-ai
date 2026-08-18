# 0229. The first string a user reads, and the two gates that could not see it

Status: Accepted (2026-08-18).
Date: 2026-08-18
Relates: ADR-0197 (the 3-band gate this batch's gates are modelled on),
ADR-0213 (a gate that analysed 8 of 247 files and reported zero errors),
ADR-0218 (the guides that ship inside the wheel), ADR-0224 (text-pinned citations).
GitHub: #189, #190, #84, #111. Refutes claims in #190 and #188.

Four beta-remaining items. Three of the four turned out to describe something other
than what was actually wrong, and the pattern is the same each time: a gate existed,
did exactly what it was built for, and was pointed at the wrong thing.

## #189 — the message, and the reason it arrived twice

`api_registry._resolve_provider` raised a message naming an internal sprint number, a
Python import path, a private wiring function, "a mock stream_fn" and "the agent loop".
It is the first sentence a credential-less user reads: press Esc at the first-run
wizard — the most likely first move — type anything, and it arrives.

Rewriting it is not the fix. **The TUI submit path was the only turn-entry point in the
product with no runnability gate.** `/model`, the model picker, `/agents use` and
print/json mode all refuse an unrunnable model with an actionable line; the one path a
user reaches by typing drove straight into the loop. So the gate moved to where the
other four already are, and the kernel's message shrank to something true for whoever
happens to see it, with the wiring note relocated to the docstring above the `raise` —
the place the reader it was written for actually looks.

### The duplication is general, and the flag that looked like the seam is not one

Measured with a `sitecustomize` probe outside the repo, and independently with an
isolation ladder (2 / 1 / 1 / 0 as each emitter is disabled):
`harness/core.py` catches anything escaping the agent loop, synthesises an
`AssistantMessage(stop_reason="error", error_message=str(exc))` whose `message_end` the
renderer prints, **and then re-raises the same exception** into `tui/shell.py`'s
catch-and-print. Its comment reasons that "the only behavioural delta is the added
events". The added events *are* a second copy, because the TUI already had a renderer
for them. Any exception that escapes the loop doubled; #189's was merely one of the few
that take the exception path at all, since every shipping adapter converts provider
failures into an `AssistantErrorEvent` that returns normally.

`EventRenderer` already had `_outcome_reported`, and it reads like the ready-made seam.
It is not: `_render_turn_abort` **read-and-clears** it on `turn_end`, which fires before
the re-raised exception reaches the shell, so by the time the shell could ask, the flag
is already back to `False`. A fix built on it would have been a no-op that looked
correct. The renderer now also records the committed error **text**, consumed by the
shell — text rather than a bool, so a mismatch means the shell is holding a *different*
error and must still print it. The failure direction is "print twice", never "print
nothing".

Not fixed here, and worth its own issue: the synthesised failure carries the same text
in `content=[TextContent("[error] …")]` *and* in `error_message`, and both are persisted,
so a `/resume` after such a turn shows the pair again. With the gate in place #189's own
case is never persisted; changing the synthesised message's shape is a session-data
change and does not belong in a beta honesty batch.

### Why the static gate is scoped to `raise` arguments

The issue asks for "a headless assertion that no user-visible error string contains
`Sprint `, `register_all`, `stream_fn`, or an `aelix_ai.` import path". Measured over
all 256 tracked product sources:

| scope | literals | matches | genuinely user-reachable |
| --- | --- | --- | --- |
| every non-docstring string literal | 16,973 | ~24 | ~4 |
| string literals inside a `raise` | 774 | **3** | **3** |

The first scoping is an 83% false-positive rate and a permanent tax on `__all__` lists
(six adapters export `register_all`), private deferred-feature dicts, pydantic
`Field(description=…)`, and an RPC error payload addressed to a machine. The second is
zero false positives, because `tui/shell.py` renders `f"✖ {exc}"` verbatim for anything
escaping `harness.prompt` — a `raise` argument in these packages is user-visible *by
construction*. That is the only seam in this codebase where "is this string
user-visible?" has a static answer. All three hits were fixed here, so the gate ships
with an empty allowlist.

**It is a drift guard, not the gate**, and the ADR says so because the file does:
indirection is invisible to it (28 raises in `extensions/headless_ui.py` are built by a
factory, one in `extensions/api.py` from a lambda — both carry sprint tokens today and
neither is visible); three other shapes reach the glass (`ctx.commit`, a `/help`
`description=`, `logger.warning`); and **count is a runtime property no static rule can
express**, which is half of what #189 was about. The behavioural lane drives the real
`run_tui`.

## #190 — the sdist, and the gate that passed a tarball with the maintainer's `.env` in it

**The issue's headline is refuted.** It reported 10,251,496 B / 1,168 members including a
scraped GOV.UK captcha page, 3.7 MB of abandoned logo drafts and two scratch HTML files.
Every one of those is **untracked**, and `release.yml` builds from `actions/checkout` —
a fresh clone. Rebuilt through the real path the artifact was 6,343,886 B / 1,158, with
all four named leaks at zero. Reproducing the dirty build with the litter copied back in
gave 10,308,093 B / 1,169, which is the issue's number to within a few commits: it was a
local working-tree build. The issue self-corrected before this batch; this records the
measurement.

The real, tracked overage was `tests/` — 551 members, 24.5% of the tarball — plus
`citations.lock.json` (470 verbatim source snippets) and an internal Windows-slice memo.
Nothing consumes the sdist's tests: the installers download wheels only and say so in a
comment, `release.yml` never unpacks an sdist, `ci.yml` runs pytest against the checkout.
Excluding them takes the artifact to 4,756,148 B / 605 and the wheel `uv build` produces
*from* that sdist is byte-identical, member for member.

### The finding the issue does not mention

`tests/packaging/test_build_hygiene.py` copies the live worktree to a temp tree and
**deletes the copy's `.gitignore`** — deliberately, and correctly: that is what stops
hatchling's VCS-ignore handling from suppressing the planted probes on its own, so the
pyproject `exclude` lists are the only thing under test. The consequence nobody had
followed through: with the belt removed, `.env` stops being suppressed, and nothing in
`_is_developer_state` matched it. **Every local run of the suite wrote a tarball
containing `ANTHROPIC_API_KEY`, `GH_TOKEN` and `PYPI_TOKEN` into a pytest temp dir, and
every assertion passed.** CI never saw it — `actions/checkout` has no `.env`, which is
exactly why it survived. Fixed on three levels: `_COPY_SKIP` no longer copies the real
file, a fake one is planted so the assertion still has something to catch, and `.env` /
`.env.local` are now in all ten `exclude` lists and in `REQUIRED_EXCLUDES`.

### Why the new assertions needed a fifth fixture

The file's own docstring argues against a fixed size — "HOW BIG IS NOT A FIXED NUMBER" —
and cites three builds of the *same commit* measuring 20 MB, then 4,112 entries, then
48 MB. That argument is correct **for its source**: a `copytree` of a live worktree
varies with whatever the maintainer's machine is holding. My own two runs of that fixture
against different checkouts of the same commit gave 6,341,061 B and 10,325,521 B.

A build seeded from `git ls-files` is a different animal: its file set is a pure function
of what is tracked, so size and inventory become stable properties. Hence a fifth
fixture rather than new assertions on the four existing ones — and it is also the only
fixture that builds what `release.yml` publishes; the other four deliberately test
something else.

The inventory assertion is an **exact allow-list of top-level entries**, not a
known-bad predicate. `_is_developer_state` is a real gate that does exactly what #111 B-7
built it for, and it passed a tarball with 551 test files in it because nothing about
`tests/` looks like developer state. A predicate enumerating known-bad will always trail
the next stray file. Measured against history, an allow-list of top-level entries needs a
one-line edit roughly every 13 days — always alongside a deliberate new top-level file,
which is the moment someone should be asked whether it belongs in a permanent public
artifact. Files added under `tests/`, `packages/`, `docs/` never touch it.

## #84 — the scope was inverted

The beta ask was "soften the help copy for the eleven inert `/settings` rows". That
shipped on 2026-07-31. Twelve days later #115 **wired** `enable_skill_commands`
(`tui/shell.py` → `expand_resource_command`, which returns `None` for a `/skill:` prefix
when the flag is off) and did not revert its copy — the exact inversion the block comment
above those rows warns about in capital letters. So the row worked while its help text
said it did not, and `tests/tui/test_settings_rows.py` **asserted the false sentence**,
pinning the lie green.

Ten of the eleven are still inert, re-measured. The fix is therefore one row, plus the
thing that would have caught it: the two hand-written sets (`INERT_ROWS`,
`WIRED_PERSIST_BLOCK_ROWS`) are now **checked against an AST scan of production call
sites**. AST, not grep — `tui/shell.py`'s docstring names `get_enable_skill_commands()`
in prose, and a substring scan counts that as a consumer.

## #111 — what was left, and what was still false

A-1, A-2, A-3 and B-1, B-2, B-3, B-5, B-6, B-7 were all already done and merely
unchecked. Three things were not.

**Both clauses of the README's headless guarantee are false, and #188 only found one.**
The sentence was: *"Two guarantees survive: `GuardrailExtension` still hard-denies its
patterns, and `--permission-mode plan` blocks every mutating tool on the headless path
too."* Both subsystems identify "mutating" by a fixed frozenset of eight **bare** tool
names, and MCP registers every tool server-qualified (`fs__write_file`), so neither is in
the way. #188's own Scope section asserts *"the floor still holds — GuardrailExtension
runs first, so catastrophic patterns are still hard-denied"*; measured, all seven
guardrail rules are scoped to those same bare-name sets and **not one** has
`applies_to_tools=None`. Both READMEs are corrected, and `tests/docs/` now pins the
narrowed claim to the code, so fixing #188 turns a test red and the prose gets updated in
the same commit.

**`aelix --export` widened the permissions of the thing it rendered.** Session `.jsonl`
files are opened `0600` inside a `0700` directory; the exporter ended in a bare
`path.write_text`, so under a stock `022` umask the rendered transcript — every prompt
and every tool result, verbatim, with no redaction pass — landed **0646** in the user's
cwd. This is the concrete residue of #138, whose headline framing ("session JSONL
exposure") is otherwise overstated: the store is fine, the export was not.

**Two headless messages had no interactive way out.** `aelix -p` with no credentials
named `/model` — a picker over the models your credentials unlock, which is empty for
exactly that reader — and `--provider X` with no key named no interactive route at all.
The shared help block's docstring argued `/login` was deliberately absent because every
caller is headless. It does not follow: `cli/list_models.py` had already solved the same
problem on the same surface by saying "run `aelix` and use `/login`". Two tests in
`test_entry_router.py` asserted `/login` was ABSENT, on the stated ground that *"Aelix
does not register one"* — it does, and has since the first-run wizard started telling
users to run it.

`docs/guides/tls-and-corporate-ca.md` closes B-9's documentation half, ships inside the
wheel, and is the user-facing form of what ADR-0226's successor work measured about
Python 3.13's `X509_V_FLAG_X509_STRICT`.

## What this batch did not do

- #188 itself (capability-based gating) — GA, per the roadmap. The READMEs now describe
  the defect instead of denying it.
- #137's fix — GA. Documented as "one session, one terminal", with the mechanism
  verified: `_current_leaf_id` is process-local and there is no `flock`/`fcntl`/`O_EXCL`
  anywhere in session storage.
- #138's redaction fork — GA. The one-line permission defect is fixed; the design
  question is not a beta item.
- The replay double-render described above.
- `docs/assets/demo.gif` (2.0 MB of the remaining 4.76 MB sdist) — a real trim
  candidate, but a separate decision from #190's.
