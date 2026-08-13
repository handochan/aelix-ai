# 0217. Project context files follow pi's published policy, and forward-sync to pi's fence

Status: Accepted (2026-08-13).
Date: 2026-08-13
Relates: ADR-0203 (the cwd `.env` hole — a project-local file reaching a privileged surface,
and our one precedent for diverging from pi, over a surface pi does not have), ADR-0214
(`.aelix/skills` + prompt-templates joining the trust predicate — the divergence that turned
out to be pi *parity*), ADR-0216 (the immediately preceding trust decision, and the governing
precedent: pi's own behaviour is not overridden without a reason that survives every upstream
sync, not just this one).
GitHub: #121.

**Provenance.** Five pieces, four different provenance claims. They are listed apart because
lumping them under one label is how a divergence gets mistaken for parity two syncs later:

| Piece                                                            | Provenance                                                |
| ---------------------------------------------------------------- | --------------------------------------------------------- |
| Injection stays **trust-independent**, and is now written down    | **PARITY** — pi's published, deliberate policy             |
| Pi's `<project_context>` / `<project_instructions>` **fence**     | **FORWARD-SYNC** — pi moved in v0.75.x, aelix follows now  |
| Append-chunk **order** (user append → context → skills)           | **FORWARD-SYNC** — pi's order in both prompt branches      |
| **XML-escaping** the content and the `path` attribute             | **AELIX-ORIGINAL DIVERGENCE** — one, declared, argued below|
| The **32 KiB cap**                                                | **AELIX-ORIGINAL, PRE-EXISTING** — declared here, not introduced |

Every number below was measured — on this branch, or fetched from pi — not recalled. Pi is
quoted from `earendil-works/pi@main` at the time of writing (newest tag
`v0.84.1`); its security doc lives at **`packages/coding-agent/docs/security.md`**, not at a
top-level `docs/security.md` — the shorter path 404s, and the line numbers below are the
package-relative file's.

---

## The problem

Issue #121 asked for a policy, not a patch: *is an `AGENTS.md` written by a repository we have
not trusted allowed to become system-level context, and if so, what do we say about it?* Aelix
walks from cwd to the filesystem root, reads every `AGENTS.md`, and appends the text to the
system prompt. That path never consults project trust.

Two corrections to the issue's framing, both measured, because an ADR that repeats a wrong
premise makes the wrong premise permanent:

- **The issue says the instructions are applied "without user confirmation in
  headless/AUTO_ACCEPT runs". The headless framing is wrong: NO mode asks.** There is no
  confirmation step on this path in any mode — interactive included. `_resolve_append_chunks`
  contains no approval call, and `discover_context_files` reads no trust state at all. What
  interactive *does* have that headless does not is a one-word **disclosure** after the fact:
  the TUI startup banner's `[Context]` row reads `AGENTS.md` or `none`. That is a report, not
  a gate, and it was itself wrong until this same change — it answered from the filesystem, so
  it printed `AGENTS.md` under `--no-context-files`, when nothing had been loaded.
- **Aelix's previous format was not pi's, so this is not a regression back to parity.** Aelix
  emitted a bare per-file markdown header, `# Project context ({path})`, files joined with
  `"\n"` and no wrapper of any kind. Pi's pre-fence format was `# Project Context` once,
  followed by `## {path}` per file (e2fd651e / 7577d3b8 diffs). The two never matched. Nothing
  pi-parity was lost by replacing ours.

## The decision

### 1. Injection stays trust-independent (PARITY)

Pi's `packages/coding-agent/docs/security.md:27`, verbatim:

> Declining trust skips protected resources. Context files such as `AGENTS.override.md`,
> `AGENTS.md`, and `CLAUDE.md` are loaded regardless of project trust unless context loading
> is disabled.

and `:37`, verbatim:

> Project trust is only an input-loading guard. It prevents a repository from silently
> changing pi's settings or extensions before you approve it. It does not make untrusted
> code, untrusted prompts, or untrusted model output safe. Prompt injection from repository
> files, comments, documentation, context files, or build output is expected local-agent risk
> and cannot be reliably prevented by pi.

This is not pi's oversight; pi tried the other way and reverted it inside four days:

| Commit    | Date       | Subject                                          | trust-manager.ts                                                        | First tag |
| --------- | ---------- | ------------------------------------------------ | ----------------------------------------------------------------------- | --------- |
| `89a9220` | 2026-06-05 | `feat(coding-agent): add project trust gating`   | **adds** `const CONTEXT_FILE_NAMES = ["AGENTS.md","AGENTS.MD","CLAUDE.md","CLAUDE.MD"]` (file created here) | `v0.79.0` |
| `5cb4f59` | 2026-06-09 | `feat(ui): Improved project approval settings`   | **deletes** the same line and its loop                                   | `v0.79.1` |

(Tag attribution measured with the compare API, not assumed: `89a9220` is *behind* `v0.79.0`
and *ahead of* `v0.78.1`; `5cb4f59` is *behind* `v0.79.1` and *ahead of* `v0.79.0`.)

ADR-0216 set the bar for overriding pi's own behaviour and declined to clear it; this does not
clear it either. The two prior security divergences are not precedent — ADR-0203 covered a
surface pi does not have, and ADR-0214's skills gate turned out to be pi *parity* — so gating
here would still be the first case where pi's own published behaviour is the thing being
overridden, and it would have to be re-justified at every upstream sync, not once.

`--no-context-files` / `-nc` remains the switch. `--no-approve` is not, and
`args.Args.project_trust_override`'s docstring — which used to describe #121 as "the open
policy question" — now says so.

### 2. Pi's fence, forward-synced (FORWARD-SYNC)

Pi wraps context files in an XML fence, identically in both prompt branches:

```ts
prompt += "\n\n<project_context>\n\n";
prompt += "Project-specific instructions and guidelines:\n\n";
for (const { path: filePath, content } of contextFiles) {
    prompt += `<project_instructions path="${filePath}">\n${content}\n</project_instructions>\n\n`;
}
prompt += "</project_context>\n";
```

**Both commits must be cited; citing `e2fd651e` alone for the default path is wrong.**

| Commit     | Date       | Branch it changed                    | First tag |
| ---------- | ---------- | ------------------------------------ | --------- |
| `e2fd651e` | 2026-05-17 | `customPrompt` branch (`system-prompt.ts:~59`) | `v0.75.0` |
| `7577d3b8` | 2026-05-18 | default branch (`system-prompt.ts:~153`)       | `v0.75.4` |

Both replaced `# Project Context` / `## ${filePath}` with the fence, and pi's own stated motive
is in the two commit subjects: "so that agents are less likely to ingest a prompt with
inconsistent boundaries" (`e2fd651e`) and "…with unclear boundaries" (`7577d3b8`). That motive
is worth quoting, because §3 is about honouring exactly it. The fence is present in `v0.75.4`
and in `v0.84.1` (2 occurrences each), i.e. in every release since.

Aelix drops pi's leading `"\n\n"`: this function returns an append *chunk* and the harness
already joins chunks with `"\n\n"` (`harness/core.py:572-573`). Emitting it here would double
the gap.

### 3. XML escaping — the one declared divergence

Aelix escapes both the file content and the `path` attribute; pi interpolates both raw. The
argument is **not** "safer than pi", and it is not a claim that pi erred. It is narrower:

> An unescaped fence lets a hostile `AGENTS.md` close the boundary early, so its payload reads
> to the model as being **outside** project context. Emitting that fence would make aelix
> *assert* a provenance boundary it is not keeping. `agent_context.py`'s own docstring already
> forbids exactly that — "an overclaim HERE is acted on by the model". We are declining to
> ship a label we cannot honour.

Measured three ways against **one** hostile `AGENTS.md` whose body carries all three forgeries
(a closing `</project_instructions>`, a closing `</project_context>`, and a complete
`<available_skills>` pair), counted in the string `discover_context_files` returns:

| Emitter                                          | `<project_context>` | `</project_context>` | `<project_instructions` | `</project_instructions>` | `<available_skills>` |
| ------------------------------------------------ | ------------------- | -------------------- | ----------------------- | ------------------------- | -------------------- |
| aelix at `21319a3` (markdown header, no fence)    | 0                   | 1                    | 0                       | 1                         | 1                    |
| **pi's emission, transliterated verbatim**        | **1**               | **2**                | **1**                   | **2**                     | 1                    |
| aelix on this branch (fence + escape)             | **1**               | **1**                | **1**                   | **1**                     | **0**                |

The pi row is the whole argument: **one open tag, two close tags.** Everything after the forged
close reads as outside the block that announced it.

Pi is vulnerable to all three body variants, and to a fourth channel the body cannot reach —
the `path` attribute, which is a *directory name* and therefore also arrives with the clone. A
`"` in a directory name is accepted by the OS: `mkdir 'sub">x'` succeeded here and discovery
found the `AGENTS.md` beneath it. Pi interpolates that into `path="…"` raw; aelix emits
`path="…/sub&quot;&gt;x/AGENTS.md"`. Feeding pi's emitter a path that *also* contains newlines
yields 2 open / 2 close tags with a payload between them — but that half is measured on **pi's
emitter, not on a real directory**: `mkdir` with a newline in the component failed with `ENOENT`
on this machine's filesystem, so treat the newline variant as a property of the emitter that
this environment could not reproduce end-to-end.

**Two things the first implementation of this section got wrong**, both found by review after it
was committed, both fixed before merge. They are recorded rather than quietly corrected because
each is a way the section could have shipped while *believing* it was complete:

- **The body is element text, not an attribute.** The first pass reused
  `skills_prompt._escape_xml` for both. That helper is pi's, applied by pi to three short
  attribute-ish fields, so it escapes `>`, `"` and `'` too — which element text does not need,
  and a project's rules are full of all three. On a realistic 249-byte rules file it emitted 17
  entities of which **2 were required**, at +28.5% bytes. Two costs followed. Because escaping
  runs before budgeting, the inflation is spent out of the 32 KiB cap: a **31640-byte
  `AGENTS.md`, comfortably under the cap and delivered whole by `21319a3`, was truncated**. And
  the model was shown `don&apos;t use print()` in prose it may copy into a file or a command.
  `_escape_text` now does `&` and `<` only (+0.8% on the same file). The structural guarantee is
  untouched, because it never rested on the other three: a tag needs `<`.
- **The cwd is attacker-controlled too, and it is not inside the fence.** Escaping the body
  closed the loud half and left a quiet one: `build_system_prompt` interpolated the working
  directory raw, twice (the `Working directory:` line and the signpost's project-local write
  target), into the *same assembled prompt*. POSIX and git both permit `<` and `>` in a path
  component and `git clone` recreates them, so a repo shipping a subdirectory named
  `<project_context>` produced **3 opens against 1 close with a completely benign `AGENTS.md`** —
  aelix emitting the unbalanced fence itself, with the user's own `--append-system-prompt` chunk
  swallowed inside it. Two-component names go further: `a<` + `project_context>` spells a closing
  tag, and `<project_instructions path="` + `etc` + `policy.md">` forges a complete opening
  provenance tag with an attacker-chosen path. The cwd now gets the same `<`/`&` substitution.
  A path with neither character is unchanged byte-for-byte, which is pinned by its own test —
  a guard that rewrote every path would have passed the forgery assertions while breaking the
  signpost for everyone.

  This is the sharpest illustration of why §3 is argued the way it is. Escaping the body alone
  would have satisfied every test written for it and still left the sentence at the top of this
  section false.

Implementation notes that belong here because they are load-bearing:

- `cli/skills_prompt._escape_xml` is **reused, not re-derived, for the `path` attribute**. Its
  replacement order (`&` first) is what keeps it correct, and it covers `"`, which an attribute
  value genuinely needs. The body takes `_escape_text` instead, for the reasons above.
- **Escaping happens before budgeting.** Escaping grows text (up to ~5× for `&`), so budgeting
  raw bytes would leave the cap unenforced by that factor.
- **Truncation can only ever reach escaped content.** The loop charges tag bytes and body
  bytes separately, so both closing tags are always emitted whole — measured against a
  40000-`&` `AGENTS.md`, which comes back at 32766 bytes with the fence still balanced
  (1 / 1 and 1 / 1) and a `Warning: AGENTS.md truncated…` on stderr. Truncating an assembled
  block string instead would make *aelix itself* emit the unbalanced fence — manufacturing the
  exact defect this section exists to prevent, with no hostile file involved.

### 4. Chunk order (FORWARD-SYNC)

Pi's order is base prompt → `appendSystemPrompt` → project context → skills, and both
branches agree (`system-prompt.ts` — append `:49-51` / `:140-142`, context `:54-61` /
`:145-152`, skills `:65-67` / `:155-157`). Aelix built `[context, *user_appends, catalog]`.

That is not cosmetic. An `AGENTS.md` arrives with a `git clone` and is injected
trust-independently by §1, so putting it first made an untrusted repository's text outrank the
chunk the user typed on their own command line. Now measured as `[0] 'USER-CHUNK'`,
`[1] '<project_context>'`. Pi already had it right.

### 5. The 32 KiB cap — a pre-existing divergence, declared

`_MAX_CONTEXT_BYTES = 32_768` is **aelix-original**. **Pi has no cap at all** — it
concatenates every context file it loaded. This ADR does not introduce the cap; it records it,
because nothing until now said where it came from and "aelix caps context files" has exactly
the shape of a thing that gets described as parity by the next reader.

What changed about it: the cap now bounds the **entire returned string** — fence (85 bytes,
measured), per-file tags, and escaped content — where before it bounded raw content only.

## Consequences

- **What a session actually sends.** The chunk contains the full text of every `AGENTS.md`
  from cwd to the filesystem root plus each file's **absolute path** in a `path=` attribute,
  and it lives in the system prompt, so it goes to the configured model provider. The walk
  does not stop at a repository boundary: with `~/AGENTS.md`, `~/src/clone/AGENTS.md` (a
  directory containing `.git`) and `~/src/clone/pkg/AGENTS.md` present, running in `pkg`
  emitted all three, root-most first. An `AGENTS.md` in `$HOME` therefore reaches every
  project beneath it. This is now written down in `SECURITY.md` rather than only in code.
- **`-nc` is a content opt-out, not a privacy opt-out.** Measured on one small project:
  without the flag the assembled prompt is 3832 bytes and carries the file text, the fence and
  the absolute `path=` attributes; with `--no-context-files` it is 3397 bytes and carries none
  of them — but `- Working directory: /abs/path` and the extension-signpost path survive in
  **both**, because they come from the base prompt. `SECURITY.md` says this in as many words.
- **Aelix reads only `AGENTS.md`.** pi's security doc names `AGENTS.override.md`, `AGENTS.md`
  and `CLAUDE.md`; `_CONTEXT_FILENAME` is a single name and `CLAUDE.md` / `AGENTS.override.md`
  are not read (measured). That gap is untouched by this ADR and is not claimed as parity.
- **Two TUI surfaces were lying and now are not.** `/context` asked the *filesystem* a question
  only the prompt can answer, and was wrong in both directions at once. Re-measured for this
  ADR by feeding the old gather's inputs (`system_prompt` whole, `memory_text` re-discovered
  from the cwd) and the new gather's inputs (one prompt, split) into the same real
  `estimate_categories`, over one assembled prompt with a 6500-char `AGENTS.md`:

  | run | old gather | new gather |
  | --- | ---------- | ---------- |
  | no flag | `System prompt 2512` + `Memory files 1670` — 1670 counted **twice** | `System prompt 843` + `Memory files 1670` — **disjoint** |
  | `-nc`   | `System prompt 842` + `Memory files 1670` — a **phantom** row | `System prompt 842`, no memory row |

  (843 vs 842 without the flag is the `"\n\n"` the harness joined with, which correctly stays
  charged to the system prompt.) The banner's `[Context]` row had the same phantom. Both now
  answer from the assembled prompt by containment (`tui/project_context.py`), so `-nc` needs no
  plumbing into either. The safe degrade is deliberate: if the file changes mid-session the row
  disappears and its tokens stay charged to `System prompt` — an understatement of one row, not
  a phantom and not a double count.
- **Residual, stated plainly.** Escaping closes **structural** forgery only. A hostile
  `AGENTS.md` remains an arbitrary-instruction channel *by design* — that is what a context
  file is — and it can still simply ask the model, in prose, to do whatever it wants. Nothing
  in this ADR changes that, and pi's `:37` above is the honest description of it. What changed
  is only that the file can no longer claim to be something other than a context file.
- **Unmeasured, and flagged as such.** Whether a real model *acts* on a forged catalog or a
  forged boundary is **not** measured. Every number here was taken from prompt text produced by
  the real code path (`parse_args` → `_resolve_append_chunks` → the harness join), never from a
  provider turn. The claim being made is "the bytes handed to the harness are correct", not "a
  model behaves differently". Given this repo's false-green history on render and hook paths
  specifically, that distinction is the point of writing it down.
- **`--no-context-files` now reaches delegated subagents.** It did not before: `no_context_files`
  occurred nowhere in the `aelix_agents` package, and all 12 measured spawn combinations emitted
  `-nc=False`, so a user who typed the flag was unprotected the moment the agent delegated. The
  chain is `cli/entry.py`'s `AgentsExtension(no_context_files=…)` → `SpawnPlan.parent_context_files`
  → `narrow_context_files` → `resolver.profile_to_flags` → the child's argv, on both the print and
  RPC channels. Two things about the wiring line are load-bearing and are pinned by tests:
  it is a **lambda, not a captured bool** — `/agents use` rewrites that same `Args` *object* in
  place, so a bool read at construction is stale in both directions; and there is exactly **one**
  polarity flip in the chain (`AgentsExtension.no_context_files` is negative, matching `Args`;
  `SubagentHost.context_files` and `SpawnPlan.parent_context_files` are positive), in
  `AgentsExtension._host_context_files`. An AST tripwire pins the `entry.py` call site —
  AST rather than a substring search, because `no_context_files` already appears in `entry.py`
  for the discovery gate itself and a text match would report the hole closed while it was open.
- **A hostile directory name could steer the terminal, in every mode.** Two side findings, one
  root cause: POSIX permits every byte but `/` and NUL in a path component, so a directory name
  arrives with the clone. (i) `discover_context_files`' two budget warnings interpolate an
  absolute path and are printed from the `cli/entry.py` discovery call — *before* any TUI exists,
  so they fire under `-p` / `--mode json` / `--mode rpc` too. Measured on the real function with a
  directory named `proj\x1b]0;pwned\x07\x1b[31mZ`: `control chars reaching stderr: ['0x7', '0x1b']`
  — an unterminated OSC title-set sequence, which then eats every byte the terminal prints after
  it. (ii) The TUI banner rendered the session `cwd` raw, and Rich is not a defence: printing that
  same path through a `no_color` Rich console emitted it with the BEL dropped but the **ESC passed
  through**. Both now delete C0 / DEL / C1 before printing. The `cli` copy of the table is
  duplicated rather than imported from `tui/commands.sanitize_for_terminal`, because `cli` must not
  import `tui` and this path is reached by headless runs that never build a TUI; the repo already
  carried three copies of that table for the same band reason. The path is also de-fanged before it
  enters the `path=` attribute — XML escaping neutralises `"` and `>` but leaves control bytes
  untouched, and XML 1.0's `Char` production forbids C0 outright, so a raw `\x1b` would make the
  fence this ADR just started asserting malformed in the one attribute an attacker picks.
  Only `cwd` is treated on the banner — it is the one value sourced from a filename; `model_id`,
  `base_url` and extension names were **not** measured and nothing here should be read as clearing
  them.
