# 0214. The knowledge channel carries metadata to the model, bodies only on request — and it is gated

Status: Accepted (2026-08-12).
Date: 2026-08-12
Relates: ADR-0196 (project-scoped agent profiles — the `.aelix/agents/` trust clause this one
extends, and the mutable `skills_holder` the catalog reads through).
ADR-0197 §(g) (the child-trust exemption whose stated justification this ADR retires).
ADR-0203 (the `.env` trust hole — the same class of defect: a project-local file reaching a
privileged surface without passing the gate that exists for it).
ADR-0213 (the type gate now in CI, which is why the `BlockedEntry` widening below is safe).
GitHub: #115, #155, #156. Follow-on for #84 (`enableSkillCommands` was an inert toggle), #101.

**Provenance.** #115 and #156 are parity restoration; #155 is aelix-original. pi is the source
of truth for the two skill formatters and their destinations, measured against a real clone at
the pinned SHA `734e08e`, not recalled.

---

## The problem

### #115 — two carriers loaded, neither reached the model

`format_skill_invocation` and `load_prompt_templates` were fully ported and had **zero
production callers**. Skills loaded, `/skills` listed them, the status panel counted them, and
the assembled system prompt contained the substring `"skill"` **zero times**. A previous
session proved this with a ZORBLAX control token: the model was not ignoring skills, they were
never sent.

The issue's own title points at `format_skill_invocation`, and that framing is a trap. **pi has
two skill formatters and they are not interchangeable:**

| pi function | emits | destination |
|---|---|---|
| `formatSkillsForPrompt` (`coding-agent/core/skills.ts:336`) | name + description + location, XML-escaped | the **system prompt** (`system-prompt.ts:72`, `:164`) |
| `formatSkillInvocation` (`agent/harness/skills.ts:38`) | the **full body** | a **user turn**, for an explicit `/skill:<name>` |

Aelix had ported the second — correctly, byte-for-byte — and never ported the first
(`available_skills`: zero hits repo-wide). Wiring the body formatter into the system prompt, as
the issue's wording suggests, would inject every skill's entire markdown on every turn: the
opposite of the progressive disclosure the issue asks for in its own rationale.

### The security half, which was not in any issue

`has_trust_requiring_project_resources` gated `extensions/`, `mcp.json` and `agents/` — **not
`skills/`**. Measured, before this change:

```
.aelix/skills/ is the ONLY resource in the repo
  has_trust_requiring_project_resources : False
  resolve_project_trusted(has_ui=True)  : True   (prompt reached: False)
  .aelix/skills scanned                 : True
```

The consumption-side guard in `_resolve_skill_dirs` already anticipated this in prose — *"a
malicious project SKILL.md is a prompt-injection vector once skills reach the model"* — but the
gate is a two-part mechanism and only one part knew about skills, so `project_trusted` was
always `True` and that guard decided nothing. Inert while the channel was dead; an ungated
system-prompt injection the moment #115 landed.

### #155 — an unhandled exception at a startup boundary

`--tools Bash,Frobnicate` exited 1 with a traceback. The seven built-ins are all lowercase, so
`"Bash"` — the most natural spelling of the best-known tool — is a fatal typo, `--help` named no
valid value, and `--list-tools` did not exist. The issue says the TUI already degrades
correctly; that clean message is the **in-session** `/agents use` handler. The crash is at the
shared pre-mode-dispatch build, and `--print`, `--json`, RPC and the TUI all tracebacked
identically.

### #156 — a mixed-reason provider blamed the build

`provider_block_hint` branched on `is_recoverable_block`, true only when **every** reason is
recoverable, so a mixed provider inherited the all-dead-end wording. Measured on a provider with
8 `config-missing` rows and 1 `no-adapter` row: the UI said *"none of its 9 catalog model(s) can
run in this build"* while setting one env var took it from 0 runnable models to 8.

## The decision

### 1. Two formatters, two destinations — port the missing one, do not repurpose the other

`cli/skills_prompt.format_skills_for_prompt` is a faithful port of `formatSkillsForPrompt`:
`<available_skills>`, name/description/location only, all five XML entities escaped,
`disable_model_invocation` filtered, empty → `""`. It is appended as the **last** append chunk,
matching pi's ordering, and it **survives `--system-prompt`** because pi appends it under a
custom prompt too (`system-prompt.ts:53-77`, measured) — unlike the extension signpost, which
lives inside `build_system_prompt` and is correctly dropped.

The catalog is gated on the `read` tool being available, as in pi: the block's own instruction
is *"use the read tool to load a skill's file"*, and ordering a model to use a tool it does not
have is the defect class `tests/cli/test_agent_context.py` already polices.

`format_skill_invocation` keeps its meaning and finally gets its caller:
`cli/resource_commands.expand_resource_command` turns `/skill:<name>` into the full body as a
**user turn**, which is what `enableSkillCommands` has advertised since it shipped. Prompt
templates ride the same machinery as `/<name>`, so the third half-alive carrier closes too, and
`--prompt-template` is redefined from pi's *name* to a **path** — aelix has no template package
manager, which is the same reason `--skill` takes a path.

**Aelix divergence, deliberate:** a 32KB catalog cap with per-description truncation. pi bounds
neither. The description is attacker-controlled and the loader does not bound it — a
200,000-char description loads with only an advisory diagnostic. Truncating in place rather than
dropping the skill, because a dropped skill would still appear in `/skills`, re-opening the
human/model split #115 exists to close.

**Aelix divergence, deliberate:** `/skill:<typo>` returns "Unknown command" rather than passing
the literal text to the model as pi does. The shell already owns that rule for every other `/x`.

### 2. The trust clause lands in the same commit as the channel

`skills/` and `prompt-templates/` join the predicate. pi's own `trust-manager.ts` lists skills,
so this is a return to parity, not a widening past it. Two consequences, both accepted:

- **A behaviour change for existing users.** A repo with `.aelix/skills/` now prompts, and a
  headless run needs `--approve` or a persisted decision. Measured at the wire: with `--approve`
  the catalog is delivered; without it, headless denies and nothing is injected.
- **ADR-0197 §(g) clause 1's second argument is retired at its source.** It exempted a same-cwd
  child from `--no-approve` because withholding trust *"only strips `.aelix/skills/`, which the
  gate never guarded"*, for *"zero security gain"*. The gain is no longer zero. No logic changed
  in `aelix_agents/trust.py` — it calls the predicate rather than re-spelling the resource list,
  so the exemption closed itself. A copy would have kept granting it.

The trust **prompt wording** is extended in the same commit, in two clauses rather than one: a
"Trust" answer is consent to exactly what the prompt names, and "can execute arbitrary code"
next to "skills" would tell the user a skill runs code, which is false.

### 3. `--tools` is validated where the registry is readable

The validation itself was never wrong — `AgentHarness.__init__` checks **after** merging
extension and MCP tools, so `--tools echo` with the echo extension is legitimately valid. Only
its *location* was: raised from inside the constructor, there is no harness yet, so a caller
wanting to say "here is what you could have typed" would have to rebuild the list and drift.

The explicit allowlist is therefore **deferred past construction** and applied one line later,
where `harness.state.tools` is the live registry. `--no-tools` is not deferred: it is a kill
switch, cannot name an unknown tool, and leaving it seeded preserves "no tools at any point in
the build". A single catch at the shared startup build fixes all four modes, and the same catch
fixes the profile path, because a profile's `tools:` lands in the same `parsed.tools`.

**Where the deferral lives is load-bearing, and the first attempt got it wrong twice.** Putting
it in `_build_harness_options` broke that function's contract — `--tools` must arrive in
`options.active_tool_names` — and silently dropped the filter for every caller other than the
factory; six tests caught it. Mutating `opts` in the factory instead was still wrong: the caller
observes that object and the harness retains it as `self._options`, so clearing the field in
place made both of them claim no allowlist had been requested. The deferral is therefore a
`dataclasses.replace` copy handed to the constructor, leaving the original truthful. The
resulting harness has `_options.active_tool_names is None` while `state.active_tool_names`
carries the allowlist — the same shape the reload path already produces (ADR-0179), not a new
one.

`/agents list` annotates a profile whose `tools:` this build cannot satisfy, against the live
registry for the same reason, and never hides the profile — it may name an extension tool that
another build does register.

### 4. Three states, from a subset rather than a third boolean

`dead_end_reasons(reasons)` returns the non-recoverable subset. All three states fall out of
`(bool(dead), bool(ids - dead))`, so no enum has to be kept in sync with `_RECOVERABLE_REASONS`.

`is_recoverable_block` is **not** widened. It is compared with `is` at one call site, where a
non-bool would silently stop matching, and its docstring already records a previous over-reach
on this exact predicate.

The **verdict is unchanged**: one model no configuration can rescue still earns "unusable", an
explicit #151 decision. Only the sentence changes, to name the dead-end subset. The wizard's
`blocked` entry becomes a `NamedTuple` — index access still works, so `entry[2]` and its
assertions are untouched, but the shape is now visible to the type gate. That is the whole
point: #151 shipped three pyright errors through a green suite by widening this very tuple.

## Consequences

- The channel is live and measured **at the wire**, not in a Python object: a real
  `--print` run against a stub provider carries `<available_skills>` with the skill's name,
  description and absolute path, and does **not** carry its body.
- Two skills ship in the wheel (`writing-skills`, `extending-aelix`), verified against a real
  `uv build`. pi ships none; aelix does because the channel is otherwise empty on a fresh
  install, and #101 assumes a package tier exists.
- Two tests were **inverted** and one **moved between clauses**, because they asserted the old
  behaviour as correct. Each is documented in place with what it used to claim and why that is
  now false — they are the record, not collateral.
- The packaged tier makes the skills catalog present in **every** run, so assertions that read
  `harness.skills` or `append_system_prompt` as exact lists were rewritten as membership checks.
  Where the catalog's own position was the thing at stake (`test_profile_body_precedes_user_
  appends_and_follows_context_files`) it is now asserted explicitly as the LAST chunk, which
  turns an incidental break into a pin on pi's ordering.
- Not done, deliberately: `--list-tools` (done properly it needs a full startup to enumerate
  extension tools — a bigger deliverable than #155); reviving `--no-prompt-templates` from
  `REMOVED_FLAGS`; `aelix-server`'s `rpc_ws` harness, which builds with no prompt or skills and
  is unaffected either way.
