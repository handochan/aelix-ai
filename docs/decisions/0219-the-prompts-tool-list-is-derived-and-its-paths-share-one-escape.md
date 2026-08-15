# ADR-0219 — The prompt's tool list is derived, its paths share one escape, and the user picks the write target

- **Status**: Accepted (2026-08-14 — landed with the implementation)
- **Issues**: #120, #167, #161, #162
- **Supersedes nothing.** Closes the residual ADR-0218 §2 recorded.

---

## 1. The framing this batch inherited was wrong, and the measurement is what corrected it

The handoff written at `e087378` presented #120 as a choice between three shapes:

- **(a)** make the sentence flag-aware only,
- **(b)** move the enumeration into a chunk computed after tool registration,
- **(c)** delete the enumeration, noting "pi has an equivalent sentence, so this is a
  pi-divergence question".

**None of the three is pi's design, and the third premise is backwards.** Fetched
2026-08-14, `packages/coding-agent/src/core/system-prompt.ts` takes the tool list as a
PARAMETER:

```ts
const tools = selectedTools || ["read", "bash", "edit", "write"];
const visibleTools = tools.filter((name) => !!toolSnippets?.[name]);
const toolsList = visibleTools.length > 0
    ? visibleTools.map((name) => `- ${name}: ${toolSnippets![name]}`).join("\n")
    : "(none)";
```

and closes the open end with one sentence:

> In addition to the tools above, you may have access to other custom tools depending on
> the project.

`agent-session.ts:1023-1056` feeds it (`_rebuildSystemPrompt`), `:928-943`
(`setActiveToolsByName`) rebuilds on every active-set change, and `:2553` makes
`_refreshToolRegistry` end in that same call — so a `registerTool` at runtime rebuilds
too. `promptSnippet` / `promptGuidelines` are declared on `ToolDefinition`
(`coding-agent/src/core/extensions/types.ts:449-459`), i.e. on the extension-facing type,
so extension tools can opt in exactly like built-ins.

**So aelix's hard-coded literal was the divergence.** Porting pi's mechanism is parity,
not a new invention, and ADR-0216's rule ("pi's behaviour is not overridden without a
reason that survives every upstream sync") points at it rather than away from it. The
owner chose the full port on that basis.

The one measurement that made option (c) tempting is still true and is recorded here so
nobody re-derives it: the prose is **strictly redundant and strictly less informative**
than the wire. `read`'s prompt text was `read (read a file)`; its `description` — sent by
every adapter on every request (`openai_completions.py:749`, `openai_responses.py:303`,
`_google_shared.py:795`, `openai_codex_responses.py:254`) — is 298 characters and carries
the offset/limit truncation semantics. The seven built-ins are 5460 bytes of JSON per
request. Deleting the prose would have been defensible on redundancy grounds and wrong on
parity grounds; pi keeps a *shorter, different* text (`Read file contents`) precisely
because it is not the description.

---

## 2. What was built

### The tool list is derived from the active set

- `Tool` gains `prompt_snippet: str` and `prompt_guidelines: tuple[str, ...]`
  (`aelix_ai/tools.py`). Declared on `Tool` rather than on a separate definition type
  because aelix has none — `ExtensionAPI.register_tool` takes a `Tool` directly, so this
  is the one place a single rule reaches built-ins and extension tools alike.
- The seven built-ins carry pi's snippets **verbatim**. Two aelix tools that the old
  literal omitted now carry their own: `aelix_status` and `agent`. The issue's complaint
  that the sentence "omits `agent` and `aelix_status`" is answered by them being able to
  appear at all.
- `build_system_prompt(cwd, *, tools)` emits pi's bulleted block, `(none)` for the empty
  set, and pi's disclaimer sentence. `tools` is **required** — see §5; the first revision
  defaulted it to `None` and called that fail-honest, which it is not.
- `cli.entry._resolve_system_prompt` takes `tools` as a required keyword argument too.
  Both production callers go through it. This is not defensive: the last time one of the
  two silently omitted an argument the other passed (`skills=`, #115), `/agents use`
  shipped a prompt that told the model about none of the profile's skills while
  `/skills` still listed them, and nothing visible broke.
- **The two signposts are gated on the tool each one names** — the documentation block on
  `read`, the self-extension block on `write`, its two source pointers on `read` again.
  Pi does the same for its skills catalog (`if (hasRead && …)`). §5 has the defect that
  forced this.

### Guidelines follow their tool

Three of aelix's static guideline bullets named `read` or `bash`, so a `--no-tools` run was
still instructed to "read files" and to "verify your work … via bash". They moved onto
those tools' `prompt_guidelines`. Three of the four "Converging to an answer" bullets are
about tool-call loops and are now emitted only when there are tools to call.

Pi's four `edit` guideline bullets were **deliberately not ported**: aelix's `edit`
description already carries the same three rules and goes on the wire every request, so
re-emitting them would bill ~440 chars a turn for facts the model already has. Pi's `bash`
guideline was not ported either — it names `PI_*` environment variables, which do not
exist in aelix, and handing the model a namespace that is not there is the exact class of
overclaim this issue is about.

### The rebuild seam

Pi keeps the tool registry and the prompt builder in one class. Aelix split them: the
registry landed in `aelix_agent_core`, the prompt text stayed in `aelix_coding_agent`. The
join is a callback, `AgentHarnessOptions.system_prompt_rebuilder`, invoked from **three**
writers: `_action_set_active_tools` (pi's `setActiveToolsByName`),
`_refresh_extension_tools` (pi's `_refreshToolRegistry` — which reaches its rebuild for
free by ending in the setter; aelix's assigns `active_tool_names` directly to dodge the
validator, so the call is explicit), and `set_tools` (the atomic registry replace, which
§5 records as the one the first revision missed while asserting there was nothing to
miss). `cli.entry` supplies the callback; the kernel never learns what a prompt says.

Three properties of the callback are load-bearing:

- It returns the **complete** prompt, not a base for the kernel to re-join.
  `/agents use` recomputes the skills catalog and the `AGENTS.md` chunk from live state,
  so a kernel-side re-join would silently revert them.
- It reads the skills **through a provider**, never a captured value. Pi's
  `_rebuildSystemPrompt` re-reads `_resourceLoader.getSkills()` on every call
  (`agent-session.ts:1043`) for the same reason: a snapshot would re-emit the previous
  profile's catalog on the next tool change.
- It closes over the shared mutable `Args`, so `--system-prompt` and a profile's
  `system_prompt: replace` are honoured for free — pi's `customPrompt` branch ignores
  `selectedTools` too, and a user who supplied their own prompt is not asking us to edit
  it.

Failures are swallowed with a debug log. The callback touches the filesystem (the docs
signpost globs, the extension signpost stats two files) and its callers are a tool
registration and an extension action; letting an `OSError` abort `register_tool` would
turn cosmetic staleness into a broken session.

`AgentProfileService.use` now composes the prompt **after** `set_active_tools` rather than
before it. It used to build an identity from a tool set that was about to change.

### One path-shaped escape (#167)

Five prompt sites emitted paths under two rules and neither was right for a path:

| site | was | measured failure |
|---|---|---|
| docs directory, `cwd_abs`, both write targets | `_escape_text` (`&`, `<`) | install under `/tmp/amp&test_…/r&d/src` → prompt emits `…/r&amp;d/…`, `is_dir()` **False** while the real one is **True** |
| the two `_package_pointer` targets | **raw** | a `<` in an install path was not neutralised at all — the ADR-0217 fence channel `a079188` closed for the cwd was open from the install side |

`_safe_prompt_path` is now the single rule: strip C0/DEL/C1, escape `<`, **leave `&`
alone**. A tag needs `<`; with every `<` escaped no interpolated path can open
`</project_instructions>` or `<available_skills>`, which is the whole structural guarantee
ADR-0217 rests on. `>`, `"`, `'` and `&` cannot start a tag, so escaping them buys nothing
and each one mangles a real path — and `&` is legal in a POSIX path component that
`git clone` recreates faithfully. The guard lives **on** `_package_pointer` rather than at
its two call sites, so a third pointer cannot be added unescaped.

The `<name>.py` / `<name>.md` placeholders are assembled outside the escape: their `<` is
deliberate prose, and escaping it would hand the model `&lt;name&gt;.py` as a filename.

### The user picks the write target (#161)

The self-extension block listed the global target first — for the measured reason that the
project-local tier is trust-gated and fails **silently** — and said nothing about who
decides. With no instruction, "listed first" is what the model acted on. It now asks, and
the ask carries a fallback because this block is emitted for `-p` / `--mode json` /
`--mode rpc` and for delegated subagents, where the honest reading of a bare "ask the
user" is "refuse". The fallback names the global target: the tier that cannot fail
silently when there is nobody to answer a trust prompt either.

This is issue #161's **shape 1** (prompt-only). Shape 2 — a real `/extension new <name>`
command — is the escalation if a live model ignores the ask, and that is a decision to be
taken on the measurement rather than in advance.

---

## 3. #162 — the paths stay absolute, and the documentation stops being partial

The base prompt emits **six** absolute paths on every turn and one of them is
`$HOME`-derived, so the OS account name reaches the provider on every request. Measured:
`- Working directory: …`, the two extension write targets, the two package pointers, and
the bundled docs directory; `username present: True`.

`~` was measured to work through **all six file tools** — `read`, `write`, `edit`, `ls`,
`find`, `grep` — because `expand_path` is pi-parity and is `os.path.expanduser`, which is
platform-independent. It was a real, cheap option.

**It was not taken.** The owner's call, on two grounds:

1. **Parity.** pi emits `getReadmePath()` / `getDocsPath()` / `getExamplesPath()` and
   `Current working directory: …` as absolute paths with no `~` anywhere. Abbreviating
   would be the divergence, and ADR-0216 governs.
2. **Windows.** The owner raised it and the measurement bore it out. `~` works in the file
   tools on Windows, but `_resolve_shell_win32` resolves the `bash` tool to
   `pwsh` → `powershell` → `cmd.exe` (`tools/bash.py:176-198`), and none of those expands
   `~` in an argument to an external program. One prompt path *is* handed to bash — the
   `grep -nE 'def (register_|on\()' <api.py>` hint — so a blanket `~` would have created
   a new Windows failure in exchange for a privacy gain that Windows would not get anyway
   (`C:\Users\<name>` is if anything the more identifying form).

What changed instead is `SECURITY.md`, which previously named only the working-directory
line and did not mention the username at all. It now enumerates all six, states the
username disclosure plainly, records that no flag suppresses them and why they are
absolute, and points at the one real mitigation (`before_provider_payload`, verified to
exist at `harness/hooks.py:644`). ADR-0217's rule — we do not assert a boundary we are not
keeping — applies to documentation too: a partial list reads as a complete one.

---

## 4. Cost, honestly

**Every number below is for `cwd = /some/project`**, re-measured on the final tree. The
first revision of this section quoted numbers taken with a longer cwd and an intermediate
build, so they were not reproducible as written — review caught it, and the fix is to state
the input rather than to trust a remembered figure.

| build | chars |
|---|---|
| pre-change (`015834c`) | 4027 |
| default, 7 built-ins | 4625 |
| `--tools read,write` | 4113 |
| `--tools read` | 2175 |
| `--no-tools` | 963 |

So the default turn costs **+598 chars (+14.8%)** and every narrower session costs *less*
than before — sharply less, because the two signposts are now gated on the tool each one
names. That gating is not an optimisation; it is §5's correctness fix, and the byte saving
is a side effect.

The signpost prose budget rose **1320 → 1600** (measured 1520), bought by #161's ask
clause and its fallback, following that test's standing rule that every raise cites the
finding that paid for it.

There is a **wall-clock** cost too, and it is worth naming because pi does not pay it.
One rebuild is **5.0 ms** measured on this repo as the cwd — it re-walks for `AGENTS.md`
and re-formats the skills catalog, because `discover_context_files` reads the filesystem
on every call while pi's `_rebuildSystemPrompt` reads a pre-loaded `ResourceLoader`.
`register_tool` triggers one rebuild **per tool**, so an extension registering ten tools
spends ~50 ms at load. That is inside the noise of extension import itself and is not
worth a cache today; if a pack ever registers hundreds of tools it becomes one, and the
fix is to give the rebuild a loader rather than to skip it.

---

## 5. The review round, and what the first commit got wrong

`652c4ce` was reviewed in five isolated contexts, each finding verified by an independent
agent instructed to default to REFUTED. It reported **37 findings**; the ones that changed
the code are below. They are recorded here rather than quietly fixed because three of them
are the same class of error this repo keeps catching, and one of them was a false statement
in the commit message itself.

- 🔴 **The commit message's sabotage claim was false.** It said "dropping either rebuild
  call … turns the corresponding test RED". True for the two kernel calls; **not** for the
  post-registration one in `_harness_factory` — deleting that left all 105 prompt tests
  green. The cause: the test helper *re-implemented* `_harness_factory`'s three-way branch
  instead of calling it, so the production line was unreachable from any test. That is
  precisely "a double that omits what production does proves nothing", committed inside the
  file whose own docstring cites it. Fixed by extracting
  `entry.apply_post_registration_tool_policy` — a module-level function both the factory and
  the tests call.
- 🔴 **The 0-tool prompt contradicted itself.** The opener, the guidelines and the
  convergence bullets varied with the active set; the docs signpost and the self-extension
  signpost did not. So `--no-tools` said "you cannot read, write or run anything" and then,
  thirty lines later, named a directory to `read`, a path to `write` to, and a `grep -nE`
  command. A worse contradiction than the one #120 was filed about, invented by the fix for
  it. Both blocks now follow the tool they name — which is pi's own move: it appends its
  skills catalog only `if (hasRead && skills.length > 0)`.
- 🔴 **`set_tools` did not rebuild**, while `_rebuild_system_prompt`'s docstring asserted it
  was "called from every place the active set changes". A comment that names an invariant is
  worth what the check behind it is worth, so there is now an AST tripwire over
  `harness/core.py` that fails when a writer of `_state.tools` / `_state.active_tool_names`
  is not followed by a rebuild.
- **`prompt_snippet` was an unbounded multi-line channel.** Pi normalizes it
  (`_normalizePromptSnippet`: collapse `[\r\n]+` then `\s+`, trim, empty ⇒ absent); the port
  dropped that. Restored, plus an aelix length cap — the same divergence `skills_prompt`
  already carries, for the same reason: the value arrives with an installed pack. Order is
  load-bearing and the first attempt got it wrong: `_CONTROL_KILL` *deletes* `\n`, so
  stripping before collapsing welded `"ok\n\nGuidelines:"` into `"okGuidelines:"`.
- **The skills catalog's read-tool gate re-derived from the flags** while the tool block came
  from the live set, so one prompt could say "(none)" and "use the read tool" at once.
  `skills_catalog_visible` now takes an authoritative `live_tool_names`.
- **`build_system_prompt(cwd)` had a `tools=None` default** documented as "fail-honest". It
  was not: `(none)` is a positive claim that the session has no tools, not the absence of a
  claim, and pi's own `selectedTools` default is a list. The parameter is now required.
- **The equality test between `/agents use` and the rebuilder was tautological** — it
  re-evaluated the rebuilder's own expression. It now drives the real
  `AgentProfileService.use`.
- Plus documentation corrections: `agent-session.ts:1043` → `:1043`, the byte table in §4,
  and `SECURITY.md`'s "no flag suppresses them" (`--system-prompt` does, and a narrower
  toolset drops the blocks that depend on it).

Every fix above was then sabotaged and the corresponding gate confirmed RED — including the
post-registration rebuild, which is the one the first round could not have caught.

## 6. #161: shape 1 was measured and failed, so shape 2 was built

Issue #161 offered three shapes and said, in its own words, to escalate "on the measurement
rather than in advance". The measurement, run against a real model in a real interactive TUI
(`--provider anthropic --model claude-sonnet-4-5`), two runs per wording:

| prompt | the user's phrasing | asked? |
|---|---|---|
| pre-change `015834c` | "Please add it" | ❌ wrote global, silently |
| shipped clause | "Do the work." | ❌ wrote global, silently |
| shipped clause | "Please add it" | ❌ wrote global — but *said* "since you didn't specify …" |
| strengthened wording | "Please add it" | ✅ asked, and stopped its turn |
| strengthened wording | "Do the work." | ❌ wrote global |

One compliance in four, and **the variable was how the user phrased the request** — not a
property anyone can ship. The prose clause bought a narration, not an ask.

So the choice moved to `/extension new <name>` (shape 2): it validates the name, asks
through `ctx.ui.select` with both options stating their *consequence* ("no trust gate" /
"ships to everyone who clones it, trust-gated"), scaffolds a **working** extension the real
loader accepts, refuses to overwrite, and says out loud that an untrusted project skips a
project-local extension with no error. With no dialog surface it refuses and prints both
paths rather than choosing — a version that silently chose would be the pre-#161 behaviour
with extra steps.

**The compliance problem moves, it does not vanish**, and the issue predicted exactly this:
shape 2 "brings shape 1's compliance problem back unless the signpost points at it". The
signpost does point at it. **It was then probed, and pointing does not work either:**

| signpost clause | user said | handed off to `/extension new`? |
|---|---|---|
| "tell them to run `/extension new <name>`" | "Please add it" | ❌ read the example, wrote directly |
| "DO NOT WRITE THE FILE YOURSELF … reply with that command and STOP" | "Please add it" | ❌ read the example, wrote directly |

0 of 2, with the strongest wording available.

So **the prompt stopped trying to steer the model.** The signpost is a third draft that
says what to DO — write, and say where — and names where the choice actually lives. A
prompt cannot make a model stop; it can tell the truth about who decides.

## 7. #161 shape 3: the approval prompt gets a third answer

What the probes also showed, and what nobody had credited: **the user is not blind today.**
Every one of those writes stopped at the permission dialog — `Create/overwrite
/home/…/extensions/reverse_string.py? [y/s/n]`. #161's premise that "the user is never
asked" is true about the *choice* and false about the *path*. What the user could not do
was answer anything except yes or no.

Now they can:

```
  1. [y] Yes — user global, every project (~/.aelix/agent/extensions/x.py)
  2. [s] Yes, for this session
  3. [p] Only this project (<cwd>/.aelix/extensions/x.py)
  4. [n] No
  ↑/↓ to move · 1-4 / y·s·p·n · Enter to confirm · Esc to deny
```

### The issue's blocking premise is half wrong, and measuring it is what made this small

#161 says the permission layer "is allow/deny only … it cannot offer 'write here instead',
so the choice cannot ride on the existing approval prompt", and rates shape 3 *multi-day,
not recommended*. That is **true of the return value** — `ToolCallResult` carries `block`
and `reason` — and **false of the mechanism**: `ToolCallHookEvent.args` is *the same dict*
the loop hands to `tool.execute` (`harness/core.py` `_before_tool_call_bridge`: "we pass
`ctx.args` by reference — no defensive copy"). Driven end to end through that bridge, a
handler that rewrites `args["path"]` and returns `None` lands the file at the new path and
leaves the original absent.

**No kernel change.** Three files and one test module.
`test_the_redirect_reaches_the_real_write_tool` drives the bridge rather than the tool, so
it goes red if that reference is ever replaced with a copy — which would kill the design
silently.

### What is deliberately narrow

- **Exactly two targets, never a free-form third.** The same two the signpost emits and
  `extension_targets` returns. A write landing anywhere else — including *below* an
  extensions directory, which the loader does not scan — keeps the three static rows
  byte-for-byte.
- **The redirect ALLOWS.** A `block` would turn the user's second *yes* into a *no*.
- **No session rule is synthesized.** "Put this one somewhere else" is a statement about
  this file, not a standing allow for the tier.
- **The row sits above "No"** because it is a second way to say yes, not a way to decline.
- **The labels state the consequence**, not only the path: the project tier is trust-gated
  and fails SILENTLY when untrusted, the global tier is not gated at all, and no path name
  says so.
- **The hint line is derived from the rows.** The first revision left it reading
  "1-3 / y·s·n" under a four-row dialog — the stale-prose defect this whole ADR is about.

### Live-verified

Real model, real TUI. The model chose the global tier, the user pressed `[p]`, and:

```
global tier : /tmp/probe-s4-agentdir/extensions/   → not created
project tier: /tmp/probe-s4/.aelix/extensions/reverse_string.py   1221 bytes
```

The model then reported it correctly — *"saved it to …/.aelix/extensions/reverse_string.py
(project-only, trusted location)"* — naming the path the USER chose, not the one it asked
for. Sabotaged seven ways (block instead of allow, allow without mutating, offer on every
write, row below "No", hardcoded hint, and a defensive copy in the kernel bridge); every
gate went red.

## 8. What is NOT closed

- **The live probe covered the prompt, not the command.** A real model was run against the
  #120 wording (`--no-tools` → "I have NO tools available right now. I cannot read a file";
  `--tools read` → "exactly one tool: read … No, I cannot run shell commands"), against the
  #117 regression (in all seven runs where it wrote, it read `examples/echo/echo.py` first
  and invented no manifest), and against #101's documentation criterion (asked what an `aelix-plugin.toml` contains, it
  answered from the bundled guide — nine capability flags, the three enforced gates,
  "descriptors reserved and inert"). **That closes #101's open criterion.** §6 records what
  the same probe found about #161 — the model does not hand the choice over, with any
  wording tried — and §7 is what was built instead. **The model still does not ask.** It
  writes, and the user redirects in one keystroke; that is the shipped behaviour, not a
  workaround for one that failed.
- **Verification coverage of the review was partial.** 37 findings were reported; the
  adversarial verify pass had returned 8 verdicts (7 CONFIRMED, 1 REFUTED) before it was stopped when the fixes
  above were made. The rest were acted on where they were independently reproduced here and
  left otherwise — a handful of LOW citation and wording items remain unaddressed.
- **Citation rot.** The review found that this batch displaced constructs that ~19 live
  `file:line` citations elsewhere in the tree pointed at. They were correct at `015834c`.
  Re-deriving them is a real, separable job and is not done.
- **The `AGENTS.md` walk is still re-run per rebuild** (5.0 ms), where pi reads a cached
  loader. Named in §4; not worth a cache until a pack registers hundreds of tools.
