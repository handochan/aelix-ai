# 0196. Agent-profile format & single-agent invoke (P1)

Status: Accepted (2026-07-19) — owner-ratified architecture. Design record that
lands with the P1 implementation (same pattern as ADR-0186/0187/0188/0189).
Amended 2026-10-28 (ADR-0276, #604): `timeout_ms` is parsed only to warn that it is
ignored — a delegation has no time limit — and a bad value no longer drops the
profile; the field stays (deprecated) for RPC `agents/show`.
Amended 2026-10-14 (#412): `/agents use` where no `model:`/`provider:` of the profile
applies (or `--none`) keeps the live model (§D8; `## Amendment (2026-10-14, #412)` at
the end).
Amended 2026-10-20 (#398): a use that sets a model writes BOTH halves of it back onto
`parsed`, so `/new`, `/fork`, `/resume` and `/reload` land on the same provider and id,
but for two corners from a `models.json` or extension provider that its Known corners
list (`## Amendment (2026-10-20, #398)` at the end).
Date: 2026-07-19
Amends: ADR-0008 (see its `Amendment (2026-07-19)` section — this ADR is the
narrow amendment that clause points at)
Relates: ADR-0002 (small kernel — **untouched here**), ADR-0028 (extension
discovery tiers the profile tiers mirror), ADR-0069 (skills loader — the idiom
this format is isomorphic to), ADR-0160 (the persisted default-model seed this
relocates), ADR-0177 (`AELIX_RELOAD_REBUILD` — moot under this ADR's `/agents
use`), ADR-0179 (#24-FU reload active-tool round-trip the tool filter must
survive), ADR-0195 (#98 model-resolution discipline the split model/provider
pair obeys). Supersedes-in-part the #16 "extension-first" placement decision.
Source spec: `.omc/specs/multiagent-profiles-teams-architecture-spec.md` §2/§5/§7,
§8 **Phase 1**, §9.
Pi pin: `earendil-works/pi@734e08e`.
Followed by: **ADR-0197** (subagent-runtime seam & the bundled `aelix-agents`
extension, P2) — see `## Note (2026-07-26)` at the end of this record for the
disposition of every item under "Deferred deliberately".

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적
목표입니다."** — this feature is **AELIX-ORIGINAL**, not a port. pi has an
`agents/<name>.md` role file, but neither pi nor the kernel has a profile
*concept*: no dataclass, no discovery, no resolver, no `--agent` flag. Parity is
therefore of SHAPE, not of source — the format deliberately mirrors the kernel
skill loader (`aelix_agent_core/harness/skills.py`) so profiles and skills stay
one idiom, and the pi role file loads unchanged (one-way compat, §D8).

## Scope of P1 — what this ADR does NOT decide

**Nothing spawns.** No `subagent_contract.py`, no `bind_subagents`, no `agent`
tool, no `SubagentRuntime`. Those are P2 and get their own ADR (spec §9 bullet 3).
**The kernel `packages/aelix-agent-core` is byte-unchanged** — every kernel
anchor below is a READ, never an edit. ADR-0008's "runtime core에는 multi-agent
개념을 두지 않는다" survives verbatim; see the ADR-0008 amendment for why
single-agent *identity* is not the multi-agent *orchestration* that clause forbids.

## Context

Spec §8 Phase 1 asks for: the profile format + parser + validator; discovery
(user + trust-gated project); the pure `profile_to_argv()` resolver; `--agent` /
`--agent-file`; `--system-prompt-file` / `--append-system-prompt-file`; honoring
`--no-builtin-tools`; and `/agents list|show|use`.

A 4-lens adversarial review executed probes against the real tree before any code
was written and found that **the ratified format could not be implemented
faithfully on the code as it stood** — four of its fields were inert, and its
central security control was a no-op. The measured evidence
(`/workspaces/aelix-ai/.venv/bin/python`, baseline = `934f2de`):

```
agents-only .aelix/  → has_trust_requiring_project_resources : False
                       resolve_project_trusted(has_ui=False)  : True   ← gate never fires
_resolve_active_tools(parse_args(["--tools", ""]))  → None   (ALL TOOLS ACTIVE)
_resolve_active_tools(parse_args(["--no-tools"]))   → []
resolve_model("inherit", None) → Model(id="inherit", provider="", api="unknown")
parse_frontmatter("---\nname: x\ndescription: y\n")
                               → ({}, "---\nname: x\ndescription: y\n", None)
yaml.safe_load("skills: none") → {"skills": "none"}   # the STRING, not a sentinel
yaml.safe_load("skills:")      → {"skills": None}
```

Read line by line: a project `.aelix/agents/` directory raised no trust
requirement at all, so the `project_trusted` flag threaded into profile discovery
was decorative; a profile declaring `tools: []` ("this agent gets no tools")
would have compiled to `--tools ''` → `parsed.tools == []` → `None` → **every
tool active**, the exact inversion of the author's intent; `model: inherit` — the
format's own documented default — would have become a literal model id and hit
the #98 unrunnable gate; an unclosed frontmatter block returns the whole file as
the *body*, i.e. the profile's own YAML would have been shipped into the model's
system prompt; and `skills: none` cannot be a sentinel because YAML hands back
the string `'none'`, indistinguishable from a path named `none`.

Three of the four are pre-existing product defects that P1 must fix as
prerequisites, not scope expansion (§D6).

## Decision

### D1. Format — one `<name>.md`, YAML frontmatter + markdown body

Adopted from spec §2.1 unchanged (single signed artifact, no credential blast
radius, marketplace-droppable). The parser is a new product-core package
`aelix_coding_agent/agents/`, built on the kernel's shared frontmatter parser
(`aelix_agent_core/harness/_frontmatter.py:23`) and deliberately shaped like the
kernel skill loader — `Literal` diagnostic-code union + frozen diagnostic
dataclass + `{value, diagnostics}` result (`harness/skills.py:50-56`, `:80-87`,
`:90-95`). **Two deliberate divergences from `skills.py`:**

1. `ProfileDiagnostic.type` admits `"error"`. `SkillDiagnostic.type` is
   `Literal["warning"]` only (`skills.py:84-87`) because a bad skill is skipped;
   a bad *identity* must be fatal — running under an identity other than the one
   requested is a safety problem, not a warning.
2. Unknown frontmatter keys emit an `unknown_field` **warning** (`skills.py`
   ignores them silently). The spec defers `deny_tools`/`mcp_servers`/`memory`/
   `hooks`/`extends`/`output_schema` behind the same parser (§2.3); a warning
   keeps them forward-compatible *and* visible instead of silently inert.

Validation ports **all four** name rules from `skills.py:471-482` — regex, ≤64,
no leading/trailing hyphen, no `--` — not just the regex, because
`^[a-z0-9-]+$` alone accepts `--tools` and `-p` as profile names. `description`
follows `skills.py:487-495` (required, ≤1024).

Field semantics that close a measured defect:

- **`model: inherit` / `provider: inherit` normalize to `None`**, identical to an
  absent key (spec §2.5's compiler is written `if model != "inherit"`). Without
  this the format's documented default is unrunnable (probe above).
- **`tools:` distinguishes absent from empty.** Absent → `None` (inherit);
  `tools: []` → `()` → `--no-tools`, never `--tools ''`. This is the
  all-tools-active inversion, fixed at the only place that can distinguish the
  two.
- **An unclosed frontmatter block is a hard `parse_failed` error.**
  `parse_frontmatter` returns `({}, <whole file>, None)` when the closing `---`
  is missing (`_frontmatter.py:46-47`), so the check must be explicit or the
  YAML reaches the model as prompt text.
- **`thinking:` is validated against `VALID_THINKING_LEVELS`** (`args.py:32-40`)
  and an invalid level is an ERROR. Deliberate asymmetry with the CLI flag, which
  warns-and-drops (`args.py:313-325`): a typo on the command line is recoverable
  in the next keystroke, a typo in a committed identity file is not.
- **P2/P3-consumed fields (`role`, `output_cap`, `timeout_ms`, `approval_mode`)
  are parsed and validated in P1 but consumed by nobody**, so the format never
  changes shape between phases. This is explicitly NOT the same as shipping a
  field that is *plumbed but inert at runtime* — see §D6.3.
  *Amended 2026-10-28 by [ADR-0276](0276-delegation-has-no-time-limit.md) ([#604](https://github.com/handochan/aelix-ai/issues/604)):
  `timeout_ms` is no longer validated as a positive integer: any value is one
  `unknown_field` warning and the profile loads; the parsed integer is kept on the
  field and read by nothing but the spawn-time warning (ADR-0276 D2).*

### D2. Discovery, scope, and effective-scope-by-containment

Dirs per spec §2.2: `~/.aelix/agent/agents/*.md` (user, via `get_agent_dir()`,
`cli/config.py:82`) and `<cwd>/.aelix/agents/*.md` (project, trust-gated).
Non-recursive `*.md` glob, dotfiles skipped; **no `agents/<name>/agent.md`
directory form in P1** (spec §2.1's escape hatch stays deferred). Identity is the
`name` field, not the filename; a mismatch warns.

**Scope is EFFECTIVE, decided by resolved-path containment — never by how the
user named the profile.** `classify_scope` checks the user dir first, then
`cwd.resolve()` containment, then falls through to `explicit`. Without this,
`--agent-file .aelix/agents/x.md` launders a project-scoped profile past both the
confirmation prompt and the `extensions:` restriction below, which is the whole
point of having them.

**Project wins a `name` collision** (spec §2.2, ratified) — but the collision
emits a warning naming *both* absolute paths, and the confirmation prompt fires.
Both halves of the ratified mitigation ship in the same commit: project-wins
without the prompt is an escalation primitive, not a precedence rule.

**`extensions:` is a hard parse ERROR for a project-scoped profile.** A profile's
`extensions:` entries become `parsed.extensions`, which is the tier-3 explicit
path list — and tier 3 is ungated by *both* discovery guards
(`extensions/loader.py:443-464`; the `if not no_discovery:` block ends at `:441`),
so it reaches `exec_module` on a path a `git clone` chose. Refusing it at the
project scope cuts that chain at the format layer; user/explicit scopes keep the
field. The spec's parent-grant *intersection* rule (§2.5 / §10) is a **spawn**
rule and is correctly deferred to P2 — P1 spawns nothing to intersect against.

`resolve_profile` is **fatal on error** and escalates a `missing_path` warning to
fatal: running with a silently-absent skill directory is the same class of
problem as running the wrong identity. `/agents list` keeps it a warning so a
broken profile can never break the listing.

### D3. `profile_to_flags` / `profile_to_argv` — one driftless contract

The resolver is pure and public: `profile_to_flags()` emits *only existing flags*
plus the two new file flags, and `profile_to_argv()` prefixes the channel
(`["--mode","json","-p","--no-session"]` one-shot / `["--mode","rpc"]`
long-lived). **In P1 `profile_to_argv` has exactly one consumer: `/agents show`'s
dry-run rendering.** It is built now, with the channel prefixes, so P2's spawner
consumes a tested contract instead of re-deriving argv.

Its twin `apply_profile_to_args()` overlays the same profile onto a live `Args`
in-process. **The two are pinned to each other by an anti-drift test that
asserts on three planes** — `Args` fields over `PROFILE_OVERLAY_FIELDS`, the
`_resolve_active_tools` result, and the built `AgentHarnessOptions`
(`thinking_level` + `system_prompt`). An `Args`-vs-`Args` assertion provably
cannot catch either of the two defects that actually existed: both sides collapse
`tools: ()` to "all tools", and both sides write the same dead `thinking` field.

### D4. CLI surface — precedence needs real provenance

New `Args` fields: `agent`, `agent_file`, `system_prompt_file`,
`append_system_prompt_files`, and **`provided: set[str]`** — the names of fields
the user set EXPLICITLY on the command line.

`provided` is load-bearing, not bookkeeping. "An explicit CLI flag always beats
the profile" is otherwise unenforceable: every `Args` field is a plain default,
so `--tools ''` yields `[]` — indistinguishable from "not supplied". `provided` is
NOT part of the parsed-value surface and is excluded from `Args`-equality
assertions. `--thinking` records itself only inside the valid-level branch
(`args.py:316-317`), so a rejected level cannot suppress a profile's valid one.

**One field is exempt: `append_system_prompt`.** The precedence rule is about
fields that are *slots* — two writers, one value, the typed flag wins. Appends
are an **accumulator**: the profile body and the user's `--append-system-prompt`
chunks coexist. Gating it would not mean "the user's value wins", it would mean
"the profile's identity is silently dropped" while stderr still reports
`Agent profile: <name>` — exactly the wrong-identity failure `--agent` is
fatal-on-error to prevent. It is also what keeps the two channels aligned:
`profile_to_flags` emits `--append-system-prompt-file`, a *separate* accumulator
that `_apply_prompt_files` extends unconditionally (it consults `provided` only
for the scalar `system_prompt`), so a gate on the overlay side would split the
channels apart for every non-empty `provided`. Effective append order is context
files → profile body → the user's own chunks, pinned by
`tests/cli/test_agent_flag_end_to_end.py::test_profile_body_precedes_user_appends_and_follows_context_files`.

The two file-taking prompt flags are the only genuinely-forced new flags:
`--system-prompt` / `--append-system-prompt` take a **literal string**
(`args.py:281-288`), which overflows `ARG_MAX` and leaks a whole system prompt in
`ps` for a real profile body. They are normalized into their string twins ONCE,
at `entry.py:984` — immediately after the `--export` early exit (`:975-984`) and
before mode resolution — so an unreadable prompt file cannot hard-fail
`--list-models` or `--export`, which do a thing and exit. The literal-string flag
wins over its file twin; file-appends land after string-appends. A 1 MiB cap
fails loudly here rather than at the provider.

**Ordering is the whole design.** The profile overlay sits immediately after the
trust gate (`entry.py:1262-1268`) and before `scan_extension_manifests`
(`:1302-1308`), `_resolve_skill_dirs` (`:1355`) and the harness factory (`:1363`),
so all three see it. Two existing blocks **move below it**, unmodified except for
one guard each:

- **The ADR-0160 persisted-default-model seed** (`entry.py:1086-1134`). It ran
  *before* the overlay point and its guard is `if parsed.model is None and
  parsed.provider is None:` — so for anyone who has ever used `/settings →
  Default model`, `parsed.model` was already occupied and a profile's `model:`
  would have been inert. Relocated, the seed correctly skips when a profile
  supplied the model. Its new guard also consults `provided`, and the split-pair
  case (profile model, no provider) falls to the `elif` that hands the persisted
  provider to `resolve_model`'s lowest-precedence `default_provider` slot instead
  of impersonating an explicit `--provider` — **exactly the ADR-0195 §Decision-4
  discipline**, preserved rather than re-litigated.
- **The `--api-key` block** (`entry.py:1159-1186`). It resolved a model and
  pinned the runtime key at `:1184` *before* the overlay. Two provable failures:
  `aelix --agent scout --api-key K` with no persisted default resolved
  `Model(id="", provider="")` → `not model.provider` → a hard `return 1` telling
  the user to pass `--model` while the profile names one; and a persisted
  `openai/gpt-5` under a profile with `provider: anthropic` called
  `set_runtime_api_key("openai", …)` — a live process-wide override on the wrong
  provider, since that `auth_storage` is the same object threaded to `run_tui` at
  `entry.py:1488`. Relocation is verified safe: between `:1134` and `:1262` the
  only readers of `parsed.model` / `parsed.provider` / `default_provider` are
  inside that same `--api-key` block (`:1164-1165`); the remaining readers are a
  factory closure kwarg read at call time (`:1378`, invoked at `:1430`) and
  `:1529`.

A project-scoped profile additionally prompts once (pi `confirmProjectAgents`,
spec §2.2 line 80) via the one-shot selector extracted out of
`_prompt_project_trust_interactive` (`entry.py:770-846`). Non-interactive modes
**deny**, mirroring `project_trust.py`'s own step 6 (`if not has_ui or prompt is
None: return False`). `--approve` is the pre-existing escape hatch; **no new
flag.** Rationale, written into the helper's docstring: trust is a
*directory-level* yes-once decision inherited by ancestors, not consent to a
specific identity file that appeared in the tree afterwards.

### D5. TUI — `/agents list | show | use`

A new `BuiltinCommand("agents", …)` in `BUILTIN_COMMANDS`
(`tui/commands.py:1207-1246`); verified no collision — the list contains
`skills`, `tools` and `extension`, but nothing named `agents`. `CommandContext`
gains two optional callbacks (`None` in headless tests), matching the existing
`resume_session` / `settings_action` idiom.

`show` renders the parsed fields **and the resolved flags** from
`profile_to_flags`. This is the auditable-wedge gem from spec §7: you see exactly
what identity, model and tool set a profile compiles to *before* anything runs.
Unset fields render `—`, never a literal `None`.

### D6. Three prerequisite defect fixes

These are not scope expansion. The ratified format depends on all three; without
them P1 would ship three fields that look validated and provably do nothing.

**D6.1 — the trust predicate was blind to `.aelix/agents/`.**
`has_trust_requiring_project_resources` (`cli/project_trust.py`) checked only
`.aelix/extensions/` and `.aelix/mcp.json`, so an agents-only `.aelix/`
short-circuited `resolve_project_trusted` to `True` at step 2 **in every mode**
(measured: predicate `False` → `resolve_project_trusted(has_ui=False)` `True`).
A third clause lands in the same commit as `agents/discovery.py`, and
`format_project_trust_prompt` now names agent profiles — a user cannot consent to
a surface the prompt never disclosed, so the predicate and the prompt string are
kept in lockstep by their own docstrings. `.aelix/teams/` is **reserved for P4 in
a comment only**: an inert check cannot be tested, so that clause lands with the
loader that reads team descriptors, never before it.

**D6.2 — `--no-builtin-tools` was parsed but ignored.** `_resolve_active_tools`
(`entry.py:446-465`) documented the omission itself (`:454-458`:
*"`--no-builtin-tools` … is NOT wired here: `active_tool_names` is seeded before
extensions register their tools"*). That ordering fact is correct and unchanged,
so the only faithful expression is a **post-registration filter**, applied inside
the factory after `harness = AgentHarness(opts)` (`entry.py:1394`) and before
`bind_model_registry` (`:1414`), where `state.tools` already holds app ∪ extension
tools and MCP tools arrived via `opts.tools`. Four properties, each verified:

- Guarded on `not parsed.no_tools`. `--no-tools` produces `[]` first
  (`entry.py:461-462`, applied at `core.py:659`), and
  `_action_set_active_tools` is explicitly **non-destructive**
  (`core.py:3525-3534` — *"record the active filter, don't drop tools"*), so
  `state.tools` still holds the full registry; an unguarded filter would
  re-enable every extension and `<server>__<tool>` MCP tool the user just killed.
- Uses the already-exported `ALL_TOOL_NAMES` (`tools/__init__.py:63`, `__all__`
  at `:118`). No second `create_all_tools()` call, and **no third
  `BUILTIN_TOOL_NAMES`** — the kernel already owns that name
  (`aelix_agent_core/harness/hooks.py:1080`).
- Names come from live `state.tools`, so `set_active_tools`' raising validator
  (`core.py:3527-3532`) can never fire.
- Reload-safe: on reload the factory runs with `active_tool_names=None`, the
  filter applies, then `AgentSessionRuntime.reload()` step 6
  (`aelix_agent_core/runtime/agent_session_runtime.py:762-798`) overwrites with
  `(active_before ∩ current) ∪ (ext ∩ current)`. `active_before` was snapshotted
  before teardown (`:726-727`) and already excludes built-ins; `ext_names` comes
  from `ext.tools` (`:779`) and built-ins are *app* tools passed via
  `options.tools`, never extension tools — so a built-in cannot reappear
  (ADR-0179's round-trip is preserved, not weakened).

**D6.3 — `--thinking` was parsed but unconsumed.** `args.py:317` is the ONLY
writer of `parsed.thinking` and product-core had **zero** readers (`git grep
'\.thinking\b'` returns that write plus two unrelated `_export_html` hits — a
`block.thinking` field and a CSS class). Fixed with one kwarg on the
`AgentHarnessOptions(…)` literal (`entry.py:727-755`):
`thinking_level=parsed.thinking`. The receiving seam already exists —
`AgentHarnessOptions.thinking_level` (`core.py:238`) → `AgentState.thinking_level`
(`core.py:603-604`), with `None` leaving the `"off"` default (`types.py:84`). The
TUI's settings seed (`tui/shell.py:551-560`) runs once *after* the harness is
built, so its guard now also requires `harness.state.thinking_level in (None, "",
"off")`: CLI/profile wins, the settings seed still fills the untouched case, and
`parsed` is deliberately **not** threaded into `run_tui`.

*Amended by ADR-0239 (#198)* — the resumed session is a third source, ranked
between CLI/profile and the settings default. The value-sniffing guard above
cannot see it (a restored explicit `off` is indistinguishable from unset by
value), so `run_tui` takes a derived `thinking_level_restored: bool` and the seed
stands down when it is set. Still not `parsed`.

*Amended by ADR-0239 (#286, 2026-10-14)* — the settings seed is no longer in the
TUI. It ran there only, so `-p`, `--mode json`, `--mode rpc` and every delegated
child ignored `defaultThinkingLevel`. `cli/entry.py` now applies the whole
ladder (CLI/profile > the resumed session > the settings default > `off`)
before the mode dispatch, for every mode, every rung but the CLI's clamped to
the model rather than skipped; `run_tui` no longer takes
`thinking_level_restored`. The order above is unchanged, and so is what a launch
records: the TUI still records the default its seed recorded, now from
`cli/entry.py` right before `run_tui` — except where a `session_start` handler
sets the level or the model, since it now meets the applied level instead of
`off` (ADR-0239's #286 decision 4).

Shipping `thinking:` as a validated-looking field that provably does nothing —
and rendering `--thinking high` in `/agents show` — is not acceptable. This is
the same class as spec §5's own **FIX (honor)** row for `--no-builtin-tools`.

### D7. Skills and extensions are PATHS, not names

aelix has no skill or extension package manager: `--skill` takes a **path**
(`entry.py:552-555` states it outright — *"a path to a skill directory (or a
`SKILL.md` whose parent is scanned) rather than an installable name"*), and
`-e/--extension` likewise resolves paths (`loader.py:443-464`). Profiles inherit
that divergence: **`skills:` and `extensions:` entries are paths**, `~`-expanded
and resolved against the **profile's own directory** — never cwd, so a profile is
portable across working directories and `profile_to_flags` emits an absolute path
that round-trips unchanged through `_resolve_skill_dirs`' cwd-relative logic. A
missing path is diagnosed (`load_skills` never diagnoses a missing directory —
it silently skips, `skills.py`), warning at parse and fatal at resolve.

Two consequences: **spec §2.4's example is wrong** and is corrected here
(`skills: [python-recon]` → an absolute path), as are the §2.3 `skills` /
`extensions` "list of names/paths" cells; and `args.py`'s own help text lied in
the same direction (`--skill <name>`, `--extension, -e <name>`) — corrected in the
same change, since that help text is the root cause of the spec's mistake.

**§2.3 gains one row: `inherit_skills: bool = true`**, replacing the ambiguous
`skills: none` sentinel the format implied — `yaml.safe_load("skills: none")`
yields the *string* `'none'`, indistinguishable from a path named `none` (probe
above). The asymmetry with `inherit_extensions: false` is deliberate and
recorded: **extensions are code** (an inherited extension executes in the
profile's session, so the secure default is "less ambient code"), while **skills
are inert prompt text** — and defaulting `inherit_skills` to false would silently
disable a user's global skills for every profile they write.

### D8. `/agents use` — Option D (durable `parsed` overlay + live public-state apply)

`use` overlays the profile onto the **same `Args` object the harness factory
closes over** (`entry.py:1367`) after resetting it to a pristine deepcopy
baseline taken at parse time, then applies the result to the LIVE harness through
its public surface. No rebuild, no reload, no kernel change.

Probe evidence that this option exists at all (the review's finding U1):
`harness.state` returns the live `_state` (`core.py:918-920`), and the public
setters are `set_skills` (`:1117`, sync), `set_model` (`:2200`),
`set_thinking_level` (`:2230`), `set_active_tools` (`:2249`) — while
**`set_system_prompt` does not exist** (`grep -rn "set_system_prompt" packages/`
→ zero hits), which is why the system prompt is written through
`state.system_prompt` and pinned by a test that also asserts
`harness._action_get_system_prompt()` agrees (`core.py:3536-3537` →
`_current_system_prompt`, `:3510-3514`).

Why the baseline reset is mandatory rather than tidy: `apply_profile_to_args` is
accretive (`append_system_prompt.insert`, `skills`/`extensions` `+=`) and
latching (`no_extensions`/`no_skills` set `True`), and `_build_harness_options`
re-reads `parsed.append_system_prompt` fresh on every build (`entry.py:765`) — so
without the reset, `use a` then `use b` would stack a's identity under b's. The
update is in-place (`self.parsed.__dict__.update(...)`) because a nested command
handler cannot rebind `_async_main`'s local, and the factory holds that exact
object. For the same reason the loaded skills move into a mutable holder: they
are computed **once, outside** the factory (`entry.py:1355-1356`) and re-applied
inside it (`:1427`), so a `use`-changed `parsed.skills` would otherwise never
reach a later `/new`, `/fork` or `/resume` rebuild.

**Rejections.** Option A — rebuild through `_harness_factory` — silently reverts
a mid-session `/model` and thinking-level change, because the factory re-derives
every option from `parsed`. Options B and C fail the observability requirement
above: a swap that `state` reports but `_action_get_system_prompt()` does not is
a swap extensions cannot see.

*Amended 2026-10-14 (#412):* the `/model` half of that promise did not hold for a
use where no `model:`/`provider:` of the profile applied. `use` re-resolved the
baseline and `set_model`-ed it for every such use — the settings pair when no model
flag was typed (the baseline back-fill), the typed flags otherwise — so a `/model`
pick — and, after #376, a restored session's model — was reverted and the move
recorded. Such a use now keeps the live model unless it drops the route of the
profile it replaces; see the amendment at the end of this record. The thinking half
still does not hold when `--thinking` was typed: a profile silent about the level
re-applies it over a `/thinking` pick (a known corner there).

**Naming the two unrelated `reload` methods, once, unambiguously** (they have
collided in review before):

| Symbol | Anchor | Relation to P1 |
|---|---|---|
| `AgentSessionRuntime.reload` | kernel `runtime/agent_session_runtime.py:658` (docstring `:658-705`, body `:706-830`) | The `/reload` path. P1 never calls it; its step-6 active-tool round-trip is what D6.2's filter must survive. |
| `AgentHarness.reload` | kernel `harness/core.py:2964` | Calls `reset_api_providers()` at step 5 (`core.py:3048`). Never invoked by any production caller (`entry.py:742-753` records this). Calling it to swap identity would tear down provider registration mid-session. |

Consequently **`AELIX_RELOAD_REBUILD` (`tui/shell.py:107-123`, ADR-0177) is moot
for `/agents use`** — the kill switch selects between two `/reload` strategies,
neither of which this path takes.

Two honest limitations, surfaced to the user rather than hidden:

- A profile with a **non-empty `extensions:`** is refused by `use` — loading new
  code genuinely needs the factory — and the message names `--agent <name>` as
  the way to get it.
- `inherit_extensions: false` with an **empty** `extensions:` applies everything
  else and prints a one-line notice that the ambient extension set is unchanged
  in-session. Rejecting it would reject *every* profile, since
  `inherit_extensions` defaults `false` (spec §2.3).

## Deferred deliberately (recorded so they are not read as gaps)

- **The 0600 temp-prompt writer** (spec §8 lists it under Phase 1). Its purpose is
  to hand a body to a *child process* without `ARG_MAX`/`ps` exposure — and
  **nothing spawns in P1**. `/agents show` renders the profile's own
  `file_path`, which is more auditable than a temp path that vanishes. It lands
  in P2 with the spawner that needs it. The flags it depends on
  (`--system-prompt-file` / `--append-system-prompt-file`) ship here, so P2 adds
  a writer, not a protocol.
- **The `confirm_project_agents` SETTINGS KEY.** The prompt itself ships (§D4).
  The review's citation for a settings key — spec line 178 — is a parameter of
  the **P2 `agent` tool**, inside §3.1's JSON block, not a settings row. The real
  requirement is spec line 80 ("Running a project-scoped profile also prompts
  once"), which the prompt satisfies. A persisted opt-out belongs with the tool
  parameter it mirrors, in P2.
- **The `[features] agents` default-off flag** (spec §8 line 367). A default-off
  flag would make spec §8's own Phase-1 acceptance test — `aelix --agent scout -p
  "recon X"` (line 372) — fail out of the box. The flag's stated purpose is to
  stabilize **delegation** before it is default-on; identity has no token
  multiplication and no child processes to stabilize. It lands in P2, where it
  gates the thing it was designed to gate.
- **The parent-grant tool/extension intersection** (spec §2.5 / §10). A spawn
  rule; P1 has no parent/child pair. P2.

## Consequences

- The ratified format is implementable as written for the first time: all four
  measured inversions (trust no-op, `tools: []` → all tools, `model: inherit`
  unrunnable, dead `thinking:`) are closed, and `skills: none` is replaced by a
  boolean that YAML can actually represent.
- Three pre-existing product defects are fixed for **every** user, not only
  profile users: project `.aelix/agents/` is now gated, `--no-builtin-tools` now
  does what its help text says, and `--thinking` now reaches the harness.
- The kernel is byte-unchanged. Product-core gains one new package
  (`agents/`), four `Args` fields, two flags, one built-in command, and two
  relocated (not rewritten) blocks in `entry.py`.
- **One-way pi compat, unchanged from spec §2.1:** a pi `agents/<name>.md` role
  loads on aelix unmodified; an aelix profile using `extensions:` or `role:` does
  not run on pi. Documented and accepted — the divergence is additive.

## Known limitations / follow-ups

- **Vote-load ordering caveat (correct, and left as-is).** The throwaway
  project-trust *vote* extension load (`entry.py:1245-1260`) runs BEFORE the
  overlay — it must, since it exists to inform the trust decision the overlay
  waits on — so a profile's `--no-extensions` cannot suppress it. Bounded by
  construction: it runs only when trust is unresolved
  (`parsed.project_trust_override is None`), it never loads the project-local
  tier (`no_project_local=True`), and its runtime is bound to nothing. Revisiting
  it means reordering the trust gate itself, which is out of scope here.
- **`use` cannot load extensions.** By design (§D8); `--agent` is the documented
  route.
- **`profile_to_argv` has no runtime consumer in P1.** It is exercised only by
  `/agents show` and its tests until P2's spawner arrives. This is deliberate —
  a tested contract before a caller beats a caller that re-derives argv — but it
  does mean the channel prefixes are unproven against a real child until P2's
  cross-channel parity test (spec §9 bullet 4).
- **`role`, `output_cap`, `timeout_ms`, `approval_mode` are validated but
  unconsumed** until P2/P3, and `/agents show` must not imply otherwise.
  (*`timeout_ms` is unconsumed again since ADR-0276, #604 — on purpose.*)
- **Spec corrections applied in this change** (so the wrong citation is not
  propagated into P2): §1 line 35 claimed the skill parser/validator "already
  lives in product-core (`harness/skills.py:59-77`, wired from `entry.py:1426`)"
  — it lives in the **kernel** `packages/aelix-agent-core`, and the wiring is
  `entry.py:1355-1356` (load, once, outside the factory) + `:1427` (`set_skills`,
  inside it; `:1426` is that call's comment). The isomorphism argument is
  unaffected and is what §D1 builds on — but it is an argument about *shape*,
  not about *package*, and the P1 package placement rests on ADR-0008's amended
  clause instead.

## Note (2026-07-26) — P2 disposition of the deferrals

**Added by:** ADR-0197 (subagent-runtime seam & the bundled `aelix-agents`
extension, P2). Nothing above is changed or withdrawn; this note records what
happened to each item under "Deferred deliberately (recorded so they are not read
as gaps)" now that P2 has landed.

| Deferral | Disposition |
|---|---|
| **The 0600 temp-prompt writer** | **Fulfilled in P2 (ADR-0197 §(h)).** `tempfile.mkdtemp(prefix="aelix-subagent-")` at 0700 + `os.open(…, O_CREAT\|O_WRONLY\|O_EXCL, 0o600)`, content = `profile.body`, unlinked in one `try/finally` inside the reaper's shielded scope and also from the child-registry record so `stop` / `stop_all` / `add_cleanup` cannot leak it. As predicted here, P2 added a *writer*, not a protocol — `--system-prompt-file` / `--append-system-prompt-file` shipped in P1 unchanged. |
| **The `confirm_project_agents` SETTINGS KEY** | **Behavioural half fulfilled; the settings key itself STILL DEFERRED.** ADR-0197 ships two consent gates: §(f) for *identity* (a model-driven `agent` call always passes `allow_project=False` and is fail-closed with no prompt; `/agents run` reuses this ADR's own `AgentProfileService.confirm_project` callback) and §(i) for *authority* (a spawn-time dialog in the parent). What remains deferred is only the **persisted opt-out** — and P2 deliberately ships **no** persisted consent at all: a grant is per-spawn, never written to disk and not even memoized for the session (ADR-0197 §(i)). A memo, if it ever lands, must key on `(profile, source_path, granted mode)`. |
| **The `[features] agents` default-off flag** | **Fulfilled in P2 (ADR-0197 §(m)).** Default `False`, read through a **global-scope-only** getter shaped like `get_default_project_trust` so a cloned repo's `.aelix/settings.json` cannot switch delegation on, with run-scoped `--agents` / `--no-agents` on top. It gates exactly what it was designed to gate: this record's argument — that identity has no token multiplication and no child processes to stabilize — is unchanged, and P1's own Phase-1 acceptance test (`aelix --agent scout -p "recon X"`) still passes with the flag off. |
| **The parent-grant tool/extension intersection** (spec §2.5 / §10) | **Fulfilled in P2 (ADR-0197 §(h)).** `requested ∩ parent_grant ∩ ALL_TOOL_NAMES − {"agent"}`, computed from the live `api.runtime.actions.get_active_tools()`. §D1's `tools: []` → `--no-tools` fix is load-bearing for it: an empty intersection must render `--no-tools`, never `--tools ''`, or the child gets **every** tool. |

Two further P1 statements are settled by P2 and are worth reading together with
the sections above:

- **"`profile_to_argv` has no runtime consumer in P1"** (Known limitations) — it
  has one now. ADR-0197 §(h)'s spawner builds its argv as
  `[sys.executable, "-m", "aelix_coding_agent", *profile_to_argv(...),
  "--permission-mode", <mode>, *child_trust_argv(...), "--no-agents"]`, and a test
  pins that `/agents show`'s dry-run string is built from the *same* calls, so the
  auditable dry run cannot drift from what actually runs. The `"Task: "` prefix at
  `agents/resolver.py:204-205` turned out to be load-bearing rather than cosmetic:
  a bare task beginning with `--` is swallowed into `parsed.unknown_flags` with no
  diagnostic at all (`cli/args.py:504`/`:510`/`:513`).
- **"`role`, `output_cap`, `timeout_ms`, `approval_mode` are validated but
  unconsumed"** — `output_cap`, `timeout_ms` and `approval_mode` are consumed in
  P2 (§(k), §(k), §(e) respectively; `timeout_ms` is no longer consumed since
  ADR-0276, #604 — a delegation has no time limit). **`role` is not.** It is wired into the
  child's `AELIX_SUBAGENT_DEPTH` but is **arithmetically inert** at the shipped
  `MAX_SUBAGENT_DEPTH = 1`, where both `leaf` and `orchestrator` yield `1` — and
  `profile.py:208` defaults every profile to `leaf` regardless. `/agents show`
  must continue not to imply otherwise until P3 raises the cap.

One P1 *decision* is also worth re-reading in the P2 light, unchanged: **§D2's
project-scope `extensions:` hard error (THE RCE CUT, `agents/profile.py:342-351`)
is what bounds ADR-0197 §(g)'s accepted cost.** A profile's explicit
`extensions:` list loads via loader tier 3, outside both discovery guards, so it
still works under the child's `--no-approve` — and that bypass is user-scope-only
precisely because this ADR forbade the field at project scope.

## Amendment (2026-10-14, #412) — a route-less `/agents use` keeps the live model

"Route-less" here: no `model:`/`provider:` of the profile applies — it names neither,
or a typed CLI flag beat each one it names — or the use is `--none`.

**Issue:** #412. **Owner decision 2026-10-14: option A** (keep the live model; decided from
`.omc/specs/b2-decision-412-evidence.md`, which traced the code at `529b997e`).

**Measured on `main` `529b997e`** — the real TUI in a pty (forkpty + pyte), fake keys,
`models.json` pointing `anthropic` at a local mock, agent dir, settings, session dir and
cwd isolated; driver and screens in `.omc/probes/412-live/impl/`:

- A session that ran on `claude-haiku-4-5`, reopened with `--continue` under settings
  `defaultProvider`/`defaultModel` = `anthropic`/`claude-sonnet-4-5`: the footer showed
  haiku (ADR-0239's #376 restore). `/agents use plain` (a profile with no `model:` /
  `provider:`) moved the footer to `claude-sonnet-4-5`, the `/model` picker marked sonnet
  as current, the next prompt reached the mock as sonnet, the session file gained
  `model_change` sonnet, and the next `--continue` came back on sonnet.
- A fresh session on the settings pair: `/model claude-haiku-4-5`, then `/agents use plain`
  — back on sonnet (`model_change` records sonnet, haiku, sonnet). This section's own
  promise, that a mid-session `/model` survives a profile switch, did not hold.
- A fresh session with no `/model`: every route-less use recorded the same model again
  (sonnet three times after `/agents use plain` and `/agents use --none`).

The cause was the review fix that back-fills the settings pair into the baseline when neither
`--model` nor `--provider` was typed (`cli/entry.py`, the "BACK-FILL THE `/agents use` BASELINE"
block). It was written for the DURABLE half — without it the next `/new` resolved no model at
all, the #98 state — and the test that pins it reads only the next rebuild
(`tests/cli/test_agent_flag_end_to_end.py::test_agents_use_keeps_the_persisted_default_model`).
With the baseline filled, `use`'s model block ran for every route-less use: it
re-resolved the launch inputs and called `set_model`, which moved the LIVE model and,
since #376, recorded the move. (With `--model` typed, the baseline is that flag and
no restore happens: the re-resolve reverted a `/model` pick to the flag and recorded the
same model again — never the settings model.) #367 (the late-provider hold), #370 (`typed_key`) and #152
(the runnable gate) were later built around that re-resolve.

### Decision

1. **A route-less use keeps the live model.** No `model:`/`provider:` of the profile
   applies — none is named, or a typed CLI flag beat each one named ("CLI flags override
   model, provider") — or it is `--none`. Whatever put the live model there stays: the
   session's restored record, a `/model` pick, the launch model. No model is resolved
   and `set_model` is not called, so no `model_change` is recorded and a reopen comes
   back where it was; a fresh session keeps the one start record #413 wrote. (The
   thinking block still runs on this path and records a `thinking_level_change` when it
   moves the level — the thinking corner below.) A profile whose `model:` a typed
   `--model` beat but whose `provider:` applied is NOT route-less: `use` resolves the
   typed model on the profile's provider and switches there, as before — "applies" is
   the overlay's own `applied`, the same set decision 2 asks of the profile being
   replaced. `parsed` still returns to the baseline: `/new`
   follows the launch inputs, and `/resume`, `/fork`, `/import` and `/reload` restore the
   session's record — the factory's `_user_named_a_model` already counts an active
   profile that names no `model:`/`provider:` as naming nothing, and one whose route a
   typed flag beat comes with that flag, which already turns the restore off (ADR-0239,
   #376 decision 1). `parsed.provider` is no
   longer completed on this path, so `/new` resolves the baseline exactly as the launch
   did.
2. **A use that drops a route keeps the behaviour above it.** When the profile being
   replaced — the launch's `--agent`, or an earlier use — applied its own `model:` or
   `provider:`, the live model is that profile's and nothing else will take it away: the
   use re-resolves the baseline and sets it, holds where that lands on a late provider
   (ADR-0250 §2.11) and is told about `--api-key` (§2.7), as before. "Applied" is the
   overlay's own answer: `AgentProfileService._route_applies` runs `apply_profile_to_args`
   on a copy of the baseline with the baseline's `provided`, the same call the launch and
   every use make, so a profile whose route a typed flag beat drops nothing — and only
   `model` / `provider` in that answer count: a profile that applies other fields (the
   `no_extensions` of one without `inherit_extensions: true`, a `thinking:`) drops no
   route. The re-resolve is not quite `/new`'s: it passes no settings `defaultProvider`
   (a rebuild does), so a bare `--model` id several providers serve can land elsewhere
   (#398's drop-path corner below). With an EMPTY baseline — no `--model`/`--provider`
   and no settings default — there is nothing to re-resolve and the dropped profile's model
   stays, as before; since review round 3 that path asks for decision 3's note as a
   kept model does (it used to keep a model whose prompts were refused in silence).
   `--none`'s status line says which happened: "Agent profile cleared (baseline CLI
   identity restored)." where the drop put a model on the session (its re-resolve, or a
   hold's placeholder), and "… restored; the model is unchanged)." wherever the model was
   left in place (review round 3: after a `/model` pick over a typed `--model`, the old
   line claimed a baseline it no longer restored).
3. **When prompts on the kept model will be refused, the status line says why.** A
   late-provider hold's placeholder gets the hold's notice again, as the re-resolve used
   to say it (ADR-0250 §2.11). Any other model with a provider gets the CLI prompt
   check's own answer — `cli/entry.py`'s `_make_prompt_check`, the composition the
   harness factory installs with `set_prompt_check`: `turn_refusal` (no turn can run on
   it — #370's not-found placeholder is the one a launch leaves; a cloudflare base URL
   whose `{ENV_VAR}` placeholders are unfilled), then `ModelRegistry.has_configured_auth`
   (no credential source: pi's "No API key found" text) — followed by `No prompt will be
   sent for it; run /model to select a working model.` The note is that check's reason,
   which is not every surface's wording: the TUI's prompt line refuses a model
   `turn_refusal` refuses BEFORE the check, with its own line (`tui/shell.py`, #189) —
   the hold's notice; the same reason (`unsupported_message`) and "Run /model to select
   a working model." while `get_available()` lists any model; and "No provider
   configured. Run /login to add one." when it lists none, where `/model` has nothing
   to offer (measured, review round 3: `.omc/probes/412-live/r3/live/`). A model the
   check refuses only for its credential reaches the check, and the words match. A
   service built without a registry (an embedder's) asks `turn_refusal` alone, the half
   that needs none. The
   profile applies. Before this amendment the not-found and cloudflare cases re-resolved
   the launch inputs and refused the whole profile (the launch's not-found text, #152's
   `unsupported_message`), as did a Vertex model on a typed `--api-key`, whose prompts
   are sent (`is_runnable`'s environment-only Vertex arm); a model with no credential got
   no word. The empty placeholder (nothing was ever chosen) gets no note, as before.
4. **pi.** pi has no `/agents` command in core (`packages/coding-agent/src/core/
   slash-commands.ts` at `pi@3daa20280`). Its nearest analogue, the subagent example
   extension, inherits the dispatching session's LIVE model AND its LIVE thinking level
   when a profile omits `model` (`examples/extensions/subagent/index.ts`:
   `inheritsDispatchConfig = !agent.model`, then `--model` from `dispatchDefaults.model`
   and `--thinking` from `dispatchDefaults.thinkingLevel`, both read from `ctx`; its
   README: "When `model` is omitted, the subagent inherits the dispatching session's
   active model and thinking level"; PR #7897). pi's subagent profiles have no
   thinking field. aelix's own delegation already inherits both (`agents/resolver.py`
   `inherits_parent_model`; `aelix_agents.print_channel.inherit_thinking`, #354).
   `/agents use` now agrees with both on the MODEL. On the thinking level it does not
   yet: a level `--thinking` names is re-applied over the live one (and a profile's own
   `thinking:`, which pi's profiles cannot carry, is applied). That divergence is the
   thinking corner below, left to a follow-up.

### Known corners (left to follow-up issues)

- **A `/model` pick made after a route profile is still reverted by a route-less use**:
  the drop rule counts the live model as the profile's. A precise rule would re-resolve
  only while the live model is still the one the profile set.
- **In a restored session, `--none` after a profile whose `model:` or `provider:`
  applied lands on the settings model, not the restored one.** Getting back to the record needs the restore
  state, which lives in `cli/entry.py`.
- **`/thinking` has the same shape**: a route-less use re-applies `--thinking` (or the
  profile's `thinking:`) and reverts a `/thinking` pick, and `set_thinking_level`
  records that move as a `thinking_level_change` — so "no record" in decision 1 is about
  the model only. Pinned as it is today by
  `test_a_route_less_use_keeps_the_model_but_still_applies_a_named_thinking_level`, whose
  first row also guards the part that must stay: a profile's own `thinking:` still
  applies on the keep path. The other direction is older: `--none` after a profile
  whose `thinking:` applied leaves that level when the baseline names none (nothing
  re-applies a level), and `--none`'s status line, which speaks for the model since
  review round 3, says nothing about it.
- **#398 remains on the drop path.** Its reported shape — `--model openrouter/auto`, a
  route-less use, `/new` — now keeps id `auto`; with a provider-naming profile dropped
  (`--agent` with `provider: openrouter`, or such a use, then a route-less use) the
  re-resolve still completes `parsed.provider` and `/new` lands on the literal
  `openrouter/auto` id (measured, `.omc/probes/412-live/impl/probe398.out`).
  *(2026-10-20: resolved — the amendment below.)*

### Consequences

- The six tests that pinned the old behaviour now pin this one, with their guarantees
  kept (#367, #370: nothing reaches the late provider, the typed key is attached
  nowhere): `test_api_key_unknown_prefix_370.py`'s three `/agents use` rows (the profile
  applies on the placeholder and says why), `test_late_provider_landing_367.py`'s two
  "asks again" rows (the live `other` model stays; the second ask is pinned on the drop
  path instead) and the `agents-use-after-model` hold row (its pick is now made under a
  launch `--agent`; the old premise is a test of its own). New rows:
  `tests/cli/test_agents_use_keeps_live_model_412.py` — among them (review round 2) the
  applied-not-named rule (a beaten `model:` with an applying `provider:` switches; both
  beaten, or a beaten `model:` alone, keeps), the note as the prompt check's own text
  (no credential, an unfilled cloudflare base URL, no note for Vertex on a typed key,
  `turn_refusal` alone without a registry) and the thinking level on the keep path;
  (review round 3) the replaced profile's other applied fields and `thinking:` are no
  route, the empty-baseline drop keeps the profile's model with the note and is not
  `/new`, the `--none` status on both paths, and `--none` re-applying a typed
  `--thinking`.
- ADR-0239's #376 consequence ("`/agents use` of a profile that names no model still
  re-resolves the launch inputs … a follow-up") is resolved by this amendment; ADR-0250's
  §2.7 and §2.11 statements that every such use re-resolves the launch inputs are narrowed
  to a use that drops a route (dated notes there).

## Amendment (2026-10-20, #398) — a use that sets a model writes back both halves of it

**Issue:** #398 (a #375 follow-up: ADR-0251 §11, "Seen alongside, not this seam"). The
issue named two directions; both were measured (below), and the one that keeps the
existing pin was taken. No owner decision was asked.

**Measured on `main` `7faf4b9a`** — the real TUI in a pty (forkpty + pyte), a fake
OpenRouter key, `OPENROUTER_BASE_URL` and a `models.json` provider `mygw` (listing `gpt`
and `mygw/gpt`) at a local mock that logs each request's `model`, HTTPS through a CONNECT
recorder that refuses everything; agent dir, settings, session dir, home and cwd
isolated; driver, screens and outputs in `.omc/probes/398-live/impl/`:

- `aelix --model openrouter/auto`, `hi`, `/agents use orprof` (`provider: openrouter`),
  `/agents use plain` (drops that route: the launch inputs are re-resolved), `hi`,
  `/new`, `hi` — the mock received `model=auto`, `auto`, then `openrouter/auto`.
- On the settings pair `mygw`/`gpt`: `/agents use autoprof` (`model: openrouter/auto`),
  `hi`, `/new`, `hi` — `auto`, then `openrouter/auto`.
- On the settings pair `openrouter`/`auto`: `/agents use gwprof` (`model: mygw/gpt`),
  `hi`, `/new`, `hi` — `gpt`, then `mygw/gpt`.

The cause was `use`'s completion of the pair for #98: `parsed.provider = model.provider`,
with `parsed.model` left the string as typed. Every rebuild resolves `parsed` as
`--model` beside `--provider` — `resolve_route`'s step E: the string as an id of that
provider, else one `<provider>/` stripped (case-insensitively, pi's strip), else a custom
id — and beside a provider the typed string can name another id. Without one,
`openrouter/auto` is the prefix `openrouter` and OpenRouter's `auto` (ADR-0250 §2, pi's
alias); beside `openrouter` it is the catalogue's literal `openrouter/auto`. In the
catalogue that is the only id whose prefixed spelling is also an id of its own provider;
a `models.json` provider can list more (`mygw/gpt` beside `gpt`). The #412 keep path
writes nothing, so the reported shape (a route-less use) already held since #412; what
remained was every use whose own route applied and every use that drops one.

### Decision

1. **A use that sets a model writes both halves of it back onto `parsed`**:
   `parsed.provider` = its provider and `parsed.model` = the string step E reads back as
   exactly its id (`agents/service.py` `_pinned_model_spelling`) — the id, or
   `<provider>/<id>` for an id that begins with its own provider's prefix, which step E
   strips once: a custom `mygw/custom-x` under `mygw` written as the bare id came back as
   `custom-x`, and the catalogued `openrouter/auto` is written `openrouter/openrouter/auto`
   and found again. So `/new` lands on the provider and id the use set, and so do
   `/fork`, `/resume` and `/reload` wherever they resolve `parsed` rather than restore
   the session's record — after a use whose own route applied, or under a typed
   `--model`/`--provider` (ADR-0239, #376 decision 1) — except in the first two Known
   corners below (a user-defined provider named like a catalogue one up to case; a
   provider that also lists an id's prefixed spelling). pi names a session's model by the
   same pair (`restoreModelFromSession`: `getModel(provider, id)`). The held branch and
   the keep path still write nothing; the use's own resolve is unchanged.
2. **Not taken: leaving `parsed.provider` unwritten** (the issue's other direction). The
   pin is load-bearing. The use resolves with other inputs than a rebuild — a profile's
   own route without `--api-key`'s rule (ADR-0250 §2.7, #370 round 2), and never with
   the settings `defaultProvider` — so unpinned, `/new` re-resolves the string under the
   rebuild's inputs. Measured with that variant: every shape above lands where it
   should (`.omc/probes/398-live/impl/runs/probe398-variant-b.out`), and over the tests
   `tests/cli`, `tests/agents`, `tests/agents_ext` and `tests/tui` held before this
   amendment's own file (9460 collected) one row goes red (9426 pass, 33 skipped),
   `tests/cli/test_api_key_unknown_prefix_370.py::test_agents_use_of_a_profiles_own_model_resolves_as_before_api_key`
   — after `/agents use nlown` (`model: newlab/model-x`) under a typed
   `--api-key`, `/new` lands on #370's not-found placeholder (`newlab`/`model-x`,
   `api='unknown'`) instead of staying on the OpenRouter route the use took.
3. **The ids are unchanged.** A provider-less `--model openrouter/auto` still names
   `auto` and `--provider openrouter --model openrouter/auto` the literal id (ADR-0250
   §2); both catalogue rows stay. This is the answer ADR-0264 §4.5 deferred to #398
   ("`auto`; #398 decides that id"): `auto` stays an id aelix resolves to.

### Known corners (left to follow-up issues)

- **The provider half, beside a user-defined provider named like a catalogue one up to
  case** (cross review, review round 2). Step E reads `parsed.provider` through
  `_named_provider`, which takes a user-defined provider equal to the name up to case
  before the catalogue (the F1 shadowing rule, ADR-0250). A use that lands on the
  catalogue's `openai` while a `models.json` or extension provider is named `OpenAI`
  (`models.json` defines it, or an extension's `register_provider` registers it: both
  count as user-defined, `ModelRegistry.get_user_defined_providers`) is rebuilt on the
  user's `OpenAI` — the profile path (`/agents use gptprof`, `model: gpt-4o-mini`) and
  the drop path alike — while an `--agent gptprof` launch, which pins no provider, stays
  on the catalogue's `openai` through `/new`. Measured (with the `models.json` provider)
  the same on `7faf4b9a` and with this amendment's code: in-process
  `.omc/probes/398-live/r2/runs/inprocess-cases.out` (cases c2, c2b, c2c), and live in
  a pty (`r2/runs/live-all.out`, scenario `case`: the use's prompt went to
  `api.openai.com`, refused at the CONNECT recorder, the one after `/new` to the user's
  `OpenAI` endpoint). No `--provider` spelling names the catalogue's `openai` once a
  user-defined `OpenAI` exists, so `service.py` cannot write one that round-trips; a fix
  needs step E's provider rule or a pin of another shape.
- **An id whose prefixed spelling its provider also lists** (verify, review round 2).
  `_pinned_model_spelling` takes no registry: an id that begins with its provider's prefix
  is written `<provider>/<id>`. When that string is itself an id of the provider —
  `mygw/x` and `mygw/mygw/x` both listed by a `models.json` or extension provider `mygw`
  (step E's `_find_in` reads an extension registration's own models first) — step E
  finds it before any strip, so a use on `mygw/x` (`model: mygw/mygw/x`) has `/new` move
  to `mygw/mygw/x`, as on `7faf4b9a` (measured with a `models.json` `mygw`: in-process
  c7, live scenario `thrice`, same kit). The shipped catalogue has no such pair: none of
  its 22 ids that begin with their own provider's prefix is listed doubled
  (`.omc/probes/398-live/cross-claude/out/catalog_twins.out`). Once ADR-0264's
  catalogue refresh lands, a published revision could add one: its new ids route like
  shipped ones (ADR-0264 §4.1, §4.4); only §4.5's OpenRouter narrowing adds no ids. A
  spelling that asks the registry — the bare id when step E finds it as itself, else the
  prefixed form — would close it.
- **The same pair-to-flags spelling outside `parsed`.** A delegated child inherits the
  parent's model as `--model <id> --provider <provider>` (`agents/resolver.py`
  `parent_model_flags`; `_pin_route` for a user-defined route), and the settings pair
  `/model` saves is seeded as `--model`/`--provider` (`cli/entry.py`). Both pass through
  step E, so a custom id that begins with its own provider's prefix loses it: a parent on
  `mygw`/`mygw/custom-x` gives a child `custom-x`, and settings `defaultProvider: mygw`,
  `defaultModel: mygw/custom-x` launch on `custom-x` (measured,
  `.omc/probes/398-live/impl/runs/corner.out`, `seed.out`). Catalogued ids are unaffected
  (`openrouter/auto` beside `openrouter` is found as itself), so #398's ids never reach
  this. `_pinned_model_spelling` is the spelling both need.
- **`use` resolves without the settings `defaultProvider`** (decision 2 of the #412
  amendment). A bare id no catalogue lists, which `--agent` homes under the settings
  `defaultProvider`, is refused by `/agents use` ("Model … not found") — measured
  `.omc/probes/398-live/impl/runs/probe398-inputs.out` row Y2, unchanged by this
  amendment.
- **A pin outlives a credential change, a launch does not.** After `/agents use` of a
  profile whose `model: anthropic/claude-haiku-4-5` went to OpenRouter (guard 2, only an
  OpenRouter key), an Anthropic key that appears later does not move `/new`; the same
  profile at launch (`--agent`) re-resolves on `/new` and reaches Anthropic (row Y1). The
  pin predates #398 and decision 2 keeps it.

### Consequences

- New rows: `tests/cli/test_agents_use_pins_the_resolved_pair_398.py` — the three defect
  shapes (a dropped launch `--agent`, a dropped earlier use, a profile's own
  `model: openrouter/auto`), the `models.json` twin on both paths, `/resume` + `/fork` +
  `/reload` after the use, the rows that held on `7faf4b9a` and must keep holding (the
  literal `openrouter/auto` a profile's `provider:` names, OpenRouter's vendor id, a
  custom id with its own provider's prefix), a slash id whose prefixed twin the provider
  also lists (`org/m1` beside `mygw/org/m1`: moved on `7faf4b9a`, and red under a
  spelling that qualifies every slash id — review round 2), and the spelling read back
  through `resolve_route` for fourteen pairs.
- The bullet "#398 remains on the drop path" above is resolved.

**Review round 2 (2026-10-21).** No behaviour change (one docstring); kit
`.omc/probes/398-live/r2/`. The
CHANGELOG entry's first sentence listed "a profile whose own `model:` or `provider:`
applies" among the uses that moved under a typed `--model openrouter/auto`; a typed
`--model` beats a profile's `model:`, and a profile's `provider: openrouter` there runs
the literal `openrouter/auto` from the use on, on `7faf4b9a` and on this amendment alike
(live, scenario `literal`: `auto`, `openrouter/auto`, `openrouter/auto`). The entry now
names each case that moved — the drop path under `--model openrouter/auto`, a profile's
own `model: openrouter/auto` with no `--model` typed, the `models.json` twin — and the
entry, the guide, decision 1 and the first two Known corners above (new) state where
`/new` still moves. The slash row now has its prefixed twin listed, and one spelling
row was added (above).


## Note (2026-10-31, #511) — the skills holder holds a filtered catalog

*Amended 2026-10-31 by [ADR-0279](0279-resources-are-turned-on-and-off-through-one-settings-contract.md).*
`skills_holder["result"]` is now a `SkillCatalog`: a `LoadSkillsResult` whose
`skills` are the ENABLED ones under the `skills` settings lists, with every
skill found beside them. `/agents use` rescans through the same loader as launch
and `/reload` (`_load_skill_catalog`) and hands the harness
`effective_skills(holder)`, as does its rollback; a profile's `skills:` paths
are forced on (a lock in `/skill`).
