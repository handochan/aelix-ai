# aelix Multi-Agent Architecture — Final Synthesis (Profiles, Delegation, Teams)

**Status:** Architecture decision, ready to implement. Self-contained.
**Owner ratification (2026-07-19):** (1) runtime placement = **(A) bundled default-on `aelix-agents` extension + product-core contract** (`subagent_contract.py` + `runtime.subagents` slot); (2) packaging = **`aelix-agents` / `aelix-team` split**; (3) **goals deferred** to a follow-up extension on loop-governor #14 (never in profile frontmatter); (4) default-on timing (P4 flip vs first-release) **deferred** — decide when P3 lands.
**Supersedes:** the #16 "extension-first, zero-core-change, tool-body-owns-N-RpcClients" decision (2026-07-12) — its flag inventory is kept; its *placement* is replaced.
**Amends:** ADR-0008 (agent-loop-in-core, orchestration-in-extensions).

---

## 0. Decision summary

aelix gains agent **profiles** (named, single-file agent identities), **on-demand delegation** (call one profile as a subagent), and **teams** (compose profiles), under a three-band split that keeps the **kernel at literally zero change** while making **aelix-as-shipped genuinely multi-agent-capable** and **third-party orchestration a first-class citizen**.

The split (top band = closest to the metal):

1. **Kernel `aelix-agent-core` — UNCHANGED.** No profile concept, no spawn concept, no multi-agent event types. ADR-0008's "the runtime core holds no multi-agent concept" survives verbatim.
2. **Product-core `aelix-coding-agent` — IDENTITY + SEAM only (no spawn behavior).** It gains the *profile format*, *discovery*, the pure `profile_to_argv()` resolver, the whole-session `--agent` flag, two new `--*-system-prompt-file` flags, the `--no-builtin-tools` fix, the identity-only `/agents list|show|use` commands, **and one thin thing more: the *contract* for a subagent runtime** — a `SubagentRuntime` Protocol, a `SubagentResult` dataclass, a typed progress-event payload, a version constant, and a `runtime.subagents` binding slot on the existing `_ExtensionRuntime`. This is *interface, not behavior*: no process is ever spawned by product-core.
3. **Bundled, default-on, first-party extension `aelix-agents` — the SPAWN IMPLEMENTATION.** It implements `SubagentRuntime` (spawn/list/status/stop, registry, concurrency caps, output caps, SIGTERM→SIGKILL reaping, usage aggregation), binds it onto `api.runtime.subagents`, emits `subagent_*` progress events on the shared EventBus, and registers the `agent` delegation tool + `/agents run`. Because it *ships enabled*, "core alone" (aelix out of the box, before any marketplace install) has real single + parallel + chain delegation.
4. **First-party extension `aelix-team` (+ later goals) — TEAMS / POLICY.** Declarative team files, roles, coordinator fan-out, the live multi-pane dashboard, and (later) Codex-style goal loops. Built entirely on `api.runtime.subagents`.
5. **Third-party extensions — anything.** Alternate orchestration engines, domain role packs, an external daemon driver, a Web UI control plane — all build on the same `api.runtime.subagents` seam and the `subagent_*` event stream, never a fork.

**Why this shape.** The three judged designs each won one lens: kernel-minimalist won pi-parity/kernel-philosophy (score 9), orchestration-first won extension-author DX (8.5), profile-first won end-user/GTM (8). The synthesis takes **kernel-minimalist as the base** (highest aggregate, and it wins the keystone philosophical lens that *is* aelix's thesis), then fixes its two real weaknesses by grafting: (a) profile-first's "profiles are a first-class resource, exactly like skills" *justification* for the product-core identity band (without profile-first's spawn-service-in-core overreach), and (b) orchestration-first's *named, idiomatic runtime seam + live progress events* (without its product-core `SubagentSupervisor` or its kernel event types). The result: kernel untouched, product-core minimal and auditable, yet a stable named surface for a *second* orchestration extension and a decoupled dashboard — the exact DX gap that scored kernel-minimalist a 5.

The load-bearing move that dissolves the sharpest conflict (Judge-1 "spawn must not be in core" vs Judge-3 "delegation must work core-alone"): **"core alone usable" is a user-facing claim satisfied by a bundled default-on extension**, while **"kernel/product-core minimal" is an architectural claim satisfied by keeping the spawn *implementation* out of the product-core module.** A profile-authoring user cannot tell whether `agent` lives in `entry.py` or in a bundled extension; an auditor and a pi-purist very much can. Both are served.

---

## 1. Core / extension boundary

Package roots: `packages/aelix-agent-core` (kernel), `packages/aelix-coding-agent` (product-core: CLI, tools, extensions, TUI, RPC). All file:line refs are verified against the tree.

| Primitive | Band | Justification |
|---|---|---|
| L1 agent loop, tool dispatch, `AgentStart/EndEvent` (payloads stay empty) | **Kernel** — unchanged | ADR-0002/0008. `types.py:166-230`. Not touched. |
| `AgentProfile` dataclass + YAML-frontmatter parser + validator | **Product-core** (new `agents/` module) | A profile is a declarative *resource that shapes one run* — isomorphic to a skill. **CORRECTED (ADR-0196):** the skill parser/validator does **not** live in product-core — it is in the **KERNEL**, `packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py:59-77`, and it is wired from `entry.py:1355-1356` (`load_skills`, once, OUTSIDE the harness factory) + `entry.py:1427` (`harness.set_skills`, INSIDE it; `:1426` is that call's comment). The isomorphism argument survives intact, but it is about **shape**, not about package: the profile parser's product-core placement rests on the **ADR-0008 amendment** (single-agent identity = product-core resource concern), not on where `skills.py` happens to sit. The shared frontmatter parser it builds on is also kernel-side (`harness/_frontmatter.py:23`). |
| Profile **discovery** (`~/.aelix/agent/agents/*.md` user, `<cwd>/.aelix/agents/*.md` project, trust-gated) | **Product-core** | Mirrors skill dirs (`entry.py:547-577`), extension tiers (`loader.py:301-364`), MCP (`config.py:127-208`), trust gate (`entry.py:849-886`). |
| **`profile_to_argv(profile, *, oneshot) -> list[str]`** — pure, public, tested resolver (writes 0600 temp prompt file) | **Product-core** | The single driftless format→flags contract. Third parties and the runtime both consume it; nobody re-derives argv. |
| **`--agent <name>` / `--agent-file <path>`** whole-session identity | **Product-core** (`args.py`) | The "invoke one agent on demand" session entrypoint. |
| **`--system-prompt-file` / `--append-system-prompt-file`** (NEW) | **Product-core** (`args.py` + `entry.py:661-665,760-766`) | Verified: `--system-prompt`/`--append-system-prompt` take a *literal string* (`args.py:281-288`), so a long body overflows `ARG_MAX` and leaks in `ps`. File-path flags are the only genuinely-forced new spawn flag. |
| **Honor `--no-builtin-tools`** (currently parsed-but-ignored) | **Product-core fix** (`entry.py:446-465`) | Parsed to `parsed.no_builtin_tools` but `_resolve_active_tools` ignores it. Needed for tool-minimal profiles. Post-registration filter (see §6.1). |
| **`/agents list \| show <name> \| use <name>`** (identity only) | **Product-core** built-in (`commands.py:1207-1246`) | `show` renders the resolved spawn flags (dry-run auditability); `use` swaps identity mid-session via existing harness setters. No spawn. |
| **Subagent runtime CONTRACT**: `SubagentRuntime` Protocol, `SubagentResult`/`SubagentStatus`/`SubagentUsage`, `SubagentProgress` event payload, `CONTRACT_VERSION`, channel-name constants | **Product-core** (new `subagent_contract.py`) | *Interface + data only, zero behavior.* Owning the seam is what fixes kernel-minimalist's "unowned observability contract" flaw and Judge-2's "under-specified binding." Distinct from the rejected *spawn service implementation in core*. |
| **`runtime.subagents` binding slot** on `_ExtensionRuntime` (`bind_subagents()` + `@property subagents`) | **Product-core** | Verified-idiomatic: `_ExtensionRuntime` (`api.py:422`) already exposes `bind_ui/bind_core/bind_model_registry/bind_login_registries/bind_api_adapters` via friendly `@property` accessors, and `ExtensionAPI.runtime` is public (`api.py:1938`). Adding one nullable slot is ~15 lines of plumbing, not a multi-agent concept. |
| **`SubagentRuntime` IMPLEMENTATION** (spawn one-shot + long-lived children, registry, list/status/stop/stop_all, semaphore, caps, reaper, usage aggregation) | **Bundled 1st-party ext `aelix-agents`** (ships default-on) | Requires subprocess spawning + CLI/flag knowledge + `RpcClient` — the concerns ADR-0002 keeps out of the kernel and ADR-0008 keeps out of orchestration-in-core. Binds onto `api.runtime.subagents` at load. |
| **`agent` delegation tool** (single/parallel/chain) + **`/agents run`** | **Bundled 1st-party ext `aelix-agents`** | Parent-LLM-drivable. `register_tool` (`api.py:1488`), `register_command` (`api.py:1576`). A *client* of the runtime, so `/agents list` and the dashboard see the same children. |
| **`subagent_*` progress events** + per-child statusline row | **Bundled 1st-party ext `aelix-agents`** | Emitted on the shared open `EventBus` (`api.events`, `api.py:504`) with the product-core-typed `SubagentProgress` payload. Fixes profile-first's opaque-mid-flight-parallel flaw. |
| **Teams** (declarative team files, roles, coordinator fan-out, shared task-list/kanban) | **1st-party ext `aelix-team`** | Orchestration STRATEGY = L2 = extension. |
| **Live team dashboard** (multi-pane, per-agent) | **1st-party ext `aelix-team`** | `ext_ui.custom()` (`ext_ui.py:315`) / keyed `set_widget(key, …)` (`ext_ui.py:275`) — verified keyed, so multiple dashboards coexist. Data source = `subagent_*` events. |
| **Goal loop** (persisted objective, evidence-gated continuation) | **1st-party ext (later)**, wired to loop-governor #14 | Objective ≠ identity (Goose/Codex axis). Never in the profile format. |
| **External daemon driver / Web UI control plane** | **3rd-party / aelix-server (future)** | A transport wrapper over `api.runtime.subagents`. Explicit non-goal for product-core (§10). No cloud presence. |

**"Core alone must be usable" — proof.** With *no marketplace extensions installed* (aelix as shipped, `aelix-agents` default-on): author `~/.aelix/agent/agents/reviewer.md`; run `aelix --agent reviewer -p "audit auth.py"` (whole-session identity); `/agents list|show|use`; and the parent LLM can `agent(profile="reviewer", task=…)` in single, parallel (≤8, 4 concurrent), or chain mode, with capped/structured/timed/killable results and **live per-child progress**. Teams (`aelix-team`) are the only thing that may be a separate install.

**"Extensions freely possible" — proof.** `api.runtime.subagents` (spawn/list/status/stop) + `subagent_*` events let *any* extension build teams, goals, dashboards, or a daemon **without forking `aelix-agents`**. A second orchestration engine registers its own tool + `/its-own-command`, subscribes to the same events, and drives the same registry. The collisions that made the pure-kernel-minimalist design hostile to a second orchestrator are gone: tool names are per-extension, `/agents` subcommands are split cleanly (core owns `list|show|use`, `aelix-agents` owns `run`, a third party owns its own verb), and dashboard widgets are keyed.

---

## 2. Agent profile format

### 2.1 Decision: one `<name>.md` = YAML frontmatter + markdown body

All three designs and all three judges converge here; adopt it. **Reject** the owner's literal "`system.md` per agent in a directory" and **reject** the Hermes home-directory bundle.

Rationale:
- **Signs as one artifact.** aelix's Ed25519 provenance is sha256-bound per file (#67). One `.md` = one signature. A dir-bundle needs a signed hash-tree + canonicalization rules — more attack surface.
- **No credential blast radius.** Hermes' per-profile `.env` (API keys, bot tokens) inside the profile dir is *exactly* the credential-exposure gap reviewers flag against Hermes for regulated use. A single `.md` carries **no secrets**; auth stays in the existing credential store. This is aelix's GTM wedge made concrete.
- **pi-parity superset.** It is a strict superset of pi's `agents/scout.md`, so a pi role loads unchanged (one-way compat; an aelix profile with `extensions:`/`role:` will not run on pi — documented, accepted).
- **Reviewable / diffable / marketplace-droppable.**

Optional escape hatch (same resolver, later phase): `agents/<name>/agent.md` with an adjacent `skills/` dir for offline bundling. Identity is *always* the single `.md`; state/sessions/credentials are **never** inside a profile.

### 2.2 Directory layout, scope, precedence

```
~/.aelix/agent/agents/<name>.md      # USER scope    (get_agent_dir(), config.py:82-92)
<cwd>/.aelix/agents/<name>.md        # PROJECT scope (TRUST-GATED, entry.py:849-886)
```

- Precedence low→high: **user < project < `--agent-file <path>` < managed/policy pin.** Project overrides user by `name` (pi `AgentScope="both"`).
- **Trust gate:** project-local profiles are inert until the project is trusted, identical to project-local skills/extensions/MCP — a project `.md` can widen tools and inject a system prompt, so it is a prompt-injection + privilege-escalation vector. Running a project-scoped profile also prompts once (pi `confirmProjectAgents`, default on).
- Identity comes from the `name` **field**, not the filename (Claude Code parity).

### 2.3 Frontmatter fields (minimal v1 — ~14 fields; grow later behind the same parser)

| Field | Req | Type / values | Compiles to (existing flag unless noted) |
|---|---|---|---|
| `name` | ✅ | str, lowercase-hyphen | identity; drives `/agents` + auto-delegation match |
| `description` | ✅ | str | routing/delegation hint; `/agents list` |
| `model` | — | str \| `inherit` (default `inherit`) | `--model` (`args.py:267`) |
| `provider` | — | str | `--provider` (`args.py:263`) |
| `tools` | — | list \| CSV | `--tools` (`args.py:307`) — allowlist |
| `builtin_tools` | — | bool (default `true`) | `--no-builtin-tools` when `false` (**fix**, `entry.py:446`) |
| `skills` | — | list of **PATHS** (YAML list or CSV) | `--skill …` (`args.py:338`). **CORRECTED (ADR-0196):** aelix has no skill package manager — `--skill` takes a **path** to a skill directory (or a `SKILL.md` whose parent is scanned), never an installable *name* (`entry.py:552-555` says so outright). A **relative** entry resolves against the **profile's own directory**, never cwd, so a profile stays portable across working directories. A non-existent path warns at parse (so `/agents list` can never break) and is **fatal** at resolve. |
| `inherit_skills` | — | bool (default `true`) | `--no-skills` when `false` (`args.py:342`). **ADDED (ADR-0196):** replaces the ambiguous `skills: none` sentinel the format implied — `yaml.safe_load("skills: none")` yields the **string** `'none'`, indistinguishable from a path named `none` (measured). The default asymmetry with `inherit_extensions` (`false`) is deliberate: **extensions are code** (inherit less ambient code by default), **skills are inert prompt text** — defaulting this to `false` would silently disable a user's global skills for every profile they write. |
| `extensions` | — | list of **PATHS** (YAML list or CSV) | `-e …` (`args.py:326`) — **aelix-original**. **CORRECTED (ADR-0196):** `-e/--extension` takes a **path**, not a name (`extensions/loader.py:443-464`); resolved against the profile's own directory, same rule as `skills`. **A non-empty `extensions:` is a hard parse ERROR in a PROJECT-scoped profile** — the tier-3 explicit-path list is ungated by BOTH discovery guards (the `if not no_discovery:` block ends at `loader.py:441`), so a path a `git clone` chose would reach `exec_module`. User/explicit scopes keep the field. |
| `inherit_extensions` | — | bool (default `false`) | `false` → child gets ONLY listed exts (isolated); `true` → keep discovery |
| `system_prompt` | — | `append` \| `replace` (default `append`) | `--append-system-prompt-file` (append) or `--system-prompt-file` (replace) — **NEW file flags** |
| `context_files` | — | bool (default `true`) | `--no-context-files` when `false` (`args.py:356`) |
| `thinking` | — | `off`/`low`/`medium`/`high`… | `--thinking` (`args.py:313`) |
| `role` | — | `leaf` (default) \| `orchestrator` | leaf omits `aelix-agents` from child (§4.2 depth mechanism) |
| `output_cap` | — | int bytes (default `51200`) | 50 KiB summary cap (pi `PER_TASK_OUTPUT_CAP`) |
| `timeout_ms` | — | int | per-task timeout for the spawn |
| `approval_mode` | — | `inherit`\|`ask`\|`auto`\|`deny` (default `inherit`) | *format only* — enforced by the permission extension; a child may never exceed the parent's mode |
| body | ✅ | markdown | the system prompt (written to a 0600 temp file at spawn) |

**Single prompt-mode field** (`system_prompt: append|replace`) and **one** `inherit_extensions` boolean — deliberately fixing profile-first's two-conflicting-`inherit_*`-booleans friction.

**Deferred to a later phase** (grow behind the same parser): `deny_tools` (denylist), `mcp_servers`, `memory`, `hooks`, `color`, `isolation: worktree`, `output_schema` (structured return), `extends` (single-level shallow inheritance). v1 uses `tools` allowlists only; no profile-to-profile inheritance.

### 2.4 Example profile file

`~/.aelix/agent/agents/scout.md`:

```markdown
---
name: scout
description: Fast read-only codebase reconnaissance. Use for locating code and summarizing structure before planning.
model: claude-haiku-4-5
tools: [read, grep, find, ls, bash]
skills: [/home/you/.aelix/agent/skills/python-recon]
inherit_skills: true
extensions: []
inherit_extensions: false
system_prompt: replace
context_files: true
role: leaf
output_cap: 51200
timeout_ms: 300000
approval_mode: deny
---
You are Scout, a fast read-only reconnaissance agent.
Report findings as a terse bullet list with `file:line` anchors.
Never modify files. Never run networked or state-changing commands.
```

**CORRECTED (ADR-0196):** the `skills:` entry was originally written
`skills: [python-recon]`, a *name*. aelix has no skill package manager, so a
name resolves to nothing and `load_skills` never diagnoses a missing directory —
the profile would have run silently without its skill. Entries are **paths**;
`~` is expanded, and a **relative** entry resolves against the profile's own
directory (here `~/.aelix/agent/agents/`), never cwd. `inherit_skills` is shown
explicitly at its default (`true`) for contrast with `inherit_extensions`
(`false`) — see §2.3.

### 2.5 How "a profile declares its own extensions + tools + skills" is realized at spawn

The authoritative compiler is the pure function **`profile_to_argv(profile, *, oneshot: bool) -> list[str]`** in product-core. It emits *only existing flags* plus the two new file flags:

```
oneshot   → ["--mode","json","-p","--no-session"]        # print_mode.py:98-256
long-lived→ ["--mode","rpc"]                              # RpcClient, rpc_client.py
  + (["--model", model]        if model != "inherit")     # args.py:267  ✅
  + (["--provider", provider]  if provider)               # args.py:263  ✅
  + (["--tools", ",".join(tools)] if tools)               # args.py:307  ✅
  + (["--no-builtin-tools"]    if not builtin_tools)      # args.py:305  ⚠ FIX to honor
  + (["--no-skills"] | ["--skill", d, …])                 # args.py:338/342 ✅
  + (["--no-extensions"] + ["-e", x, …])                  # args.py:326/330 ✅
  + (["--no-context-files"]    if not context_files)      # args.py:356 ✅
  + (["--thinking", level]     if thinking)               # args.py:313 ✅
  + ([ "--system-prompt-file" | "--append-system-prompt-file", tmp0600 ])  # NEW
  + (["Task: " + task]         if oneshot)                # positional
```

The body is written to a **0600 temp file** and passed by path (never inline) — avoids `ARG_MAX` and `ps` leakage; unlinked in `finally`. Because the profile is exploded into concrete flags, **the child needs no access to the profile directory, no network, and no shared state** — fully deterministic, cross-cwd, cross-machine, air-gap-safe.

**Security floor is re-asserted at spawn, always** (mirrors `entry.py:690-702`): `GuardrailExtension()` + the permission extension are prepended even when `extensions: []` and `inherit_extensions: false`. A profile can never opt a child out of safety extensions. Additionally the child's effective tool set is **intersected with (never a union of) the parent's granted set**, and permission approval is never delegatable (no agent message is consent). This is what makes "spawn-in-an-extension" exactly as safe as "spawn-in-the-harness."

---

## 3. Invocation model

### 3.1 Parent LLM → one agent on demand (the `agent` tool, in `aelix-agents`)

Registered via `register_tool` (`api.py:1488`); delegates entirely to `api.runtime.subagents`.

```jsonc
{
  "name": "agent",                 // label "Agent"
  "params": {
    "profile":  "string   // single mode: one profile name (resolved by core)",
    "task":     "string   // the delegated instruction",
    "mode":     "single | parallel | chain   // default single",
    "tasks":    "[{profile, task, cwd?}]      // parallel; ≤ MAX_PARALLEL=8, ≤ MAX_CONCURRENCY=4",
    "chain":    "[{profile, task, cwd?}]      // sequential; {previous} substitution",
    "cwd":      "string?  // per-task working dir; default parent cwd",
    "background":"bool?   // default false (sync)",
    "timeout_ms":"int?    // default from profile or 300000",
    "output_cap":"int?    // default from profile or 51200",
    "confirm_project_agents": "bool  // default true (pi parity)"
  }
}
```

Auto-delegation: the parent matches `task` against each profile's `description` (Claude Code / Codex pattern); the discovered profile roster is injected into the tool description.

Returns a structured **`SubagentResult`** (or a list):

```jsonc
{
  "id": "sub_01H…", "profile": "scout", "ok": true, "status": "ok | error | timeout | aborted",
  "summary": "…final assistant text, ≤ output_cap, truncation-marked…",
  "truncated": false,
  "usage": { "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0, "tokens": 0, "turns": 0 },
  "error": null
}
```

Only the `summary` re-enters the parent context (Hermes/Claude Code discipline) — the child transcript never pollutes the parent. Usage is aggregated from the child's `message_end`/`tool_result_end` events exactly as pi's `subagent/index.ts` sums it.

### 3.2 User → one agent directly (one profile, three reuse paths — Claude Code lineage)

The *same* `<name>.md` is used three ways with zero redefinition:
1. **`aelix --agent <name>`** (product-core, always-on) — the profile *is* the whole top-level session. `--system-prompt-file` replace semantics via `entry.py:661-665`.
2. **`/agents use <name>`** (product-core built-in) — swap identity mid-session: re-apply system prompt, re-scope tools (`_resolve_active_tools`, `entry.py:446-465`), re-load skills (`harness.set_skills`, `entry.py:1427`), switch model. Reuses existing harness setters; no spawn.
3. **`/agents run <name> <task>`** and **`@<name>`** (`aelix-agents` ext) — the `agent` delegation path.

Staging parity with Claude Code (where `SendMessage`/resume works *without* the experimental-teams flag): **single-agent identity (`--agent`, `/agents use`, `/agents show`) is product-core and always available**; **delegation and teams are gated behind the feature flag / extension.**

### 3.3 Sync vs background, caps, timeout / kill

- **Sync (default).** Foreground; blocks the delegating tool call only; collects and returns. `background: true` → the runtime returns a handle and streams `subagent_*` events; results collected later.
- **Output cap** 50 KiB/task (overridable per profile), truncation-marked; **only the summary returns**.
- **Concurrency** enforced by a runtime semaphore: `MAX_CONCURRENCY=4`, `MAX_PARALLEL_TASKS=8` (pi parity). Over-limit batches **error, not truncate** (Hermes rule).
- **Timeout / kill — two documented conventions, one per spawn path** (resolving kernel-minimalist's unresolved 1s-vs-5s ambiguity):
  - *One-shot json child (`PrintChannel`):* SIGTERM → **5 s** grace → SIGKILL (pi parity; the grace lets the child flush its final json event and the reaper unlink the 0600 temp prompt). Settings: `agents.oneshot_kill_grace_ms=5000`.
  - *Long-lived rpc child (`RpcChannel`):* `RpcClient.stop()` SIGTERM → **1 s** → SIGKILL (the established `rpc_client.py:158-204` contract, ADR-0071). Settings: `agents.rpc_kill_grace_ms=1000`.
  - Both: task-cancellation → `finally: runtime.stop(id)`; parent shutdown → `runtime.stop_all()`; the 100 ms startup-death check (`rpc_client.py:141-156`) catches launch failures.

---

## 4. Team model

Teams are a **first-party extension `aelix-team`** built entirely on `api.runtime.subagents`. Team choreography is *strategy* (L2 = extension); the runtime supplies spawn/status/events.

### 4.1 What the runtime exposes to the team extension

```python
# api.runtime.subagents : SubagentRuntime | None   (bound by aelix-agents; None if not loaded)
resolve_profile(name_or_path) -> ResolvedProfile
spawn(resolved, task, *, cwd, mode, timeout_ms, background, on_event) -> SubagentResult
list() -> list[SubagentStatus]
status(id) -> SubagentStatus
stop(id) -> None
stop_all() -> None
# concurrency semaphore + hard caps + output-cap + result envelope live inside the runtime
```

The team extension calls these N times; it never re-implements spawning, capping, or killing.

### 4.2 Composition — declarative + ad-hoc

**Declarative** `<cwd>/.aelix/teams/<name>.md` (trust-gated, same discovery family):

```yaml
---
name: ship-feature
coordinator: orchestrator          # a profile with role: orchestrator
members:
  - { profile: scout,    role: leaf }
  - { profile: worker,   role: leaf, model: claude-opus-4-8 }
  - { profile: reviewer, role: leaf }
mode: fan-out                      # fan-out | pipeline | chain | kanban
concurrency: 4                     # ≤ runtime hard cap
max_depth: 1                       # 1 = flat
token_budget: 2000000
---
Team brief / mission (becomes the coordinator's context).
```

Invoked via `/team run ship-feature <task>`. **Ad-hoc:** the `agent` tool's `parallel`/`chain` modes, or a `team` tool the extension registers, for one-off teams with no file.

### 4.3 Roles, coordination, streaming, limits

- **Roles:** `orchestrator` (may delegate to `max_depth`) vs `leaf` (default; cannot re-delegate — Hermes/Codex depth-1). Leaves are also blocked from memory-write / user-clarify toolsets (Hermes guardrail).
- **Depth cap as MECHANISM, not just policy** (kernel-minimalist's elegant idea): a `leaf` child is spawned **without `aelix-agents` in its extension set**, so it physically lacks the `agent` tool and *cannot nest*. `max_depth=1` is the default and its own enforcement. Belt-and-suspenders: the runtime threads `AELIX_SUBAGENT_DEPTH` via child env (`_build_env`, `rpc_client.py:474-477`) and refuses spawns past `max_depth` even for orchestrators.
- **Phase-1 coordination = coordinator-owned fan-out** (no broker): the coordinator decomposes, spawns via the runtime, aggregates typed `SubagentResult` objects. **Result exchange is typed envelopes, not shared memory.** A file-locked shared task-list / kanban (self-claiming cards keyed by `assignee=profile`, Claude Code style) is a later `aelix-team` phase — the choreography (claim/complete/dependency-unblock/file-locking) stays entirely in the extension.
- **Live dashboard:** subscribes to `subagent_*` events, renders one pane per agent (`{name, status, current tool, elapsed, tokens, cost}`) via `ext_ui.custom()` (focusable overlay) or keyed `set_widget(key, …)` (persistent chrome row). **Data source is the child's own event stream surfaced by the runtime — never kernel events** (kernel `AgentStart/End` payloads stay empty; ADR-0165's deferred subagent-status work is fulfilled at the extension layer, not by enriching kernel types).

---

## 5. New / changed CLI flags for child spawn

| Flag | Status | Where | Notes |
|---|---|---|---|
| `--agent <name>` / `--agent-file <path>` | **NEW** | product-core `args.py` + `entry.py` | Whole-session identity; child self-resolves the profile (single profile→config code path, no parent/child drift). |
| `--system-prompt-file <path>` | **NEW** | product-core `args.py` + `entry.py:661-665` | Reads a 0600 temp file; replace semantics. Fixes the verified literal-string limitation of `--system-prompt`. |
| `--append-system-prompt-file <path>` | **NEW** | product-core `args.py` + `entry.py:760-766` | Append semantics. The existing text-taking `--system-prompt`/`--append-system-prompt` stay working (no caller breaks). |
| `--no-builtin-tools` | **FIX (honor)** | product-core `entry.py:446-465` | Parsed today (`parsed.no_builtin_tools`) but ignored; wire a post-registration tool filter (§6.1). |
| `AELIX_SUBAGENT_DEPTH` (env, not a flag) | **NEW** | runtime `_build_env` (`rpc_client.py:474-477`) | Numeric depth guard threaded into each child. |
| `--mode json`, `-p`, `--no-session`, `--model`, `--provider`, `--tools`, `--no-extensions`, `-e`, `--skill`, `--no-skills`, `--no-context-files`, `--thinking`, `--mode rpc` | **REUSED as-is** | verified present (`args.py:219-357`) | Honored identically across rpc/json/print modes — harness built once at `entry.py:1366`. |

No other new child flag is required. `--agent` (wiring where the child reads the profile itself) is the whole-session path; the delegation path explodes profiles into the concrete flags above so children need no profile-dir access.

---

## 6. ExtensionAPI additions

All additions are in **product-core** as *interface/plumbing*; behavior is bound by the bundled `aelix-agents` extension.

### 6.1 Product-core `subagent_contract.py` (new module — types + version, no behavior)

```python
CONTRACT_VERSION = 1

class SubagentStatus(TypedDict):     # for list()/status()
    id: str; profile: str; state: Literal["starting","running","done","error","stopped"]
    current_tool: str | None; elapsed_ms: int; tokens: int; cost: float

@dataclass(frozen=True)
class SubagentUsage:
    input: int; output: int; cache_read: int; cache_write: int
    cost: float; tokens: int; turns: int

@dataclass(frozen=True)
class SubagentResult:
    id: str; profile: str; ok: bool
    status: Literal["ok","error","timeout","aborted"]
    summary: str; truncated: bool; usage: SubagentUsage; error: str | None

@dataclass(frozen=True)
class SubagentProgress:               # payload of subagent_* events
    id: str; profile: str; state: str
    current_tool: str | None; elapsed_ms: int; tokens: int; cost: float

# event channel-name constants on the shared EventBus
SUBAGENT_START = "subagent_start"; SUBAGENT_TOOL = "subagent_tool"; SUBAGENT_END = "subagent_end"

class SubagentRuntime(Protocol):      # implemented by aelix-agents, bound onto _ExtensionRuntime
    def resolve_profile(self, name_or_path: str) -> "ResolvedProfile": ...
    async def spawn(self, resolved, task, *, cwd=None, mode="single",
                    timeout_ms=None, background=False, on_event=None) -> SubagentResult: ...
    def list(self) -> list[SubagentStatus]: ...
    def status(self, id: str) -> SubagentStatus: ...
    async def stop(self, id: str) -> None: ...
    async def stop_all(self) -> None: ...
```

The `--no-builtin-tools` fix (§5) also lands here in spirit: a small post-registration tool filter applied after extension/MCP `register_tool` runs, so tool-minimal profiles are honored despite the seed-before-register ordering noted at `entry.py:454`.

### 6.2 Product-core `_ExtensionRuntime` binding slot (idiomatic, ~15 lines)

```python
# in _ExtensionRuntime (api.py:422), beside bind_ui / bind_core / bind_model_registry / …
def bind_subagents(self, runtime: "SubagentRuntime | None") -> None:
    self._subagents = runtime
@property
def subagents(self) -> "SubagentRuntime | None":   # → api.runtime.subagents (api.runtime is public, api.py:1938)
    return self._subagents
```

### 6.3 Bundled `aelix-agents` extension (the implementation + wiring)

- Implements `SubagentRuntime` with two channels behind one protocol: `PrintChannel` (one-shot `--mode json -p --no-session`, reusing `print_mode.py:98-256`) and `RpcChannel` (long-lived, wrapping `RpcClient`, `rpc_client.py:111-204,425-450`).
- At load: `api.runtime.bind_subagents(self._runtime)`.
- `register_tool(agent_tool)` (`api.py:1488`); `register_command("run", …)` under `/agents` (`api.py:1576`).
- Emits `SUBAGENT_*` events with `SubagentProgress` payloads on `api.events` (`api.py:504`); sets a per-child statusline row via `set_status(key, …)` (`ext_ui.py:245`).

### 6.4 Decision on the event bus (reject the invasive HookBus edit)

`subagent_*` events go on the **open, arbitrary-channel EventBus** (verified — `api.events`), carrying the product-core-typed `SubagentProgress` dataclass. This gives third parties a **static typed payload** (fixing Judge-2's "untyped `Any` events are second-class" concern) **without** editing the closed `HOOK_RESULT_TYPES` set — deliberately rejecting orchestration-first's kernel event-type addition *and* Judge-2's "must promote to the typed HookBus" as over-invasive for v1. Promotion to the typed HookBus is recorded as optional future hardening, not required.

---

## 7. TUI story

- **Product-core (identity, always-on):**
  - `/agents list` — discovered profiles with scope + trust badge.
  - `/agents show <name>` — renders frontmatter **and the resolved spawn flags** (dry-run auditability — you see exactly what identity/tools/model a profile compiles to before anything runs). This is the auditable-wedge gem grafted from profile-first.
  - `/agents use <name>` — swap identity mid-session.
- **`aelix-agents` (delegation):** `/agents run <name> <task>`; a **per-child statusline row** (name / status / current tool / elapsed / tokens / cost) so a parallel or background batch is never opaque mid-flight — directly closing the "no progress feedback" (진행 피드백 부재) gap the owner has hit before.
- **`aelix-team` (teams):** a live **multi-pane dashboard** (one focusable overlay via `custom()` or keyed chrome rows via `set_widget(key, …)`), fed by `subagent_*` events; `/team run <name> <task>`.
- **Later:** `@<name>` mention routing → `agent` tool call (extends the existing `@file` fuzzy completion).

---

## 8. Phased roadmap

Each phase is independently landable + testable. The existing `RpcClient` test harness (`tests/rpc/*`, `tests/pi_parity/test_phase_4_4_strict_superset.py`) already drives real `--mode rpc` children and is the ready-made bed. **Ship Phases 1–3 behind a default-off `[features] agents` flag; flip default-on at Phase 4** (once the event/result contract is proven under the dashboard) — reconciling "stabilize behind a flag" (Codex) with "core-alone-usable at ship" (GTM).

**Phase 1 — Product-core identity (no multi-agent; ADR-0008 fully intact).**
Deliver: `AgentProfile` + parser + validator; discovery (`~/.aelix/agent/agents` + trust-gated `cwd/.aelix/agents`); `profile_to_argv()` pure resolver + 0600 temp-file writer; `--agent`/`--agent-file`; `--system-prompt-file`/`--append-system-prompt-file`; honor `--no-builtin-tools`; `/agents list|show|use`.
Reuses: `config.py:82-92`, `entry.py:547-577` (skill dirs), `loader.py:301-364`, `entry.py:446-465`, `entry.py:661-665,760-766`, `entry.py:849-886` (trust), `commands.py:1207-1246`.
Test: `aelix --agent scout -p "recon X"` builds the right harness; `/agents show` renders exact flags; `/agents use` swaps identity; a project-local profile prompts for trust.

**Phase 2 — Product-core seam + bundled `aelix-agents` runtime + single delegation.**
Deliver: `subagent_contract.py` (Protocol/types/events/`CONTRACT_VERSION`) + `bind_subagents`/`@property subagents`; `aelix-agents` extension with `PrintChannel` one-shot spawner, `agent` tool (single mode), `SubagentResult` envelope + 50 KiB cap + SIGTERM→5s→SIGKILL reaper, `subagent_*` events + statusline row, `/agents run`. Binds `api.runtime.subagents`.
Reuses: `print_mode.py:98-256`, `rpc_client.py:454-477` (argv/env), `register_tool` (`api.py:1488`), `register_command` (`api.py:1576`), `api.events` (`api.py:504`).
Test: parent `agent(profile=scout,…)` returns structured/capped/timed/killable result; statusline shows live progress; `stop` kills the child and unlinks the temp prompt.

**Phase 3 — parallel + chain + governance + long-lived channel.**
Deliver: `agent` parallel/chain modes; semaphore (`MAX_CONCURRENCY=4`), `MAX_PARALLEL_TASKS=8`, `max_depth` via `AELIX_SUBAGENT_DEPTH` + leaf-omits-`aelix-agents`; `{previous}` substitution; over-limit errors-not-truncates; `RpcChannel` (long-lived interactive members) with `prompt_and_wait`/`stop`/`set_model`; **cross-channel parity test** (PrintChannel ≡ RpcChannel `SubagentResult` for the same task).
Reuses: `rpc_client.py:425-450,158-204,267`.
Test: parallel cap=4; chain `{previous}`; parity test green.

**Phase 4 — `aelix-team` + live dashboard (flip flag default-on).**
Deliver: declarative `.team.yaml`/`.md` + ad-hoc `team` tool; coordinator fan-out; roles; live multi-pane dashboard; `/team run`. Flip `[features] agents` default-on.
Reuses: `ext_ui.py:275,315`, `widget_protocols.py`, `command_dispatch.py:219-295`.
Test: `/team run` renders a live dashboard from child events with the kernel untouched.

**Phase 5 — GTM hardening (the exact gaps Hermes reviewers flag).**
Deliver: Ed25519-signed profiles + team files (reuse #67 provenance, sha256-bound); spawn **audit trail**; marketplace distribution via `extension_sources` (#32-A) + air-gap-native signed catalog (#65); signature-required policy option (fail-closed).

**Phase 6 — Later / separate first-party (and 3rd-party territory).**
Goal loop (Codex-style, evidence-gated continuation) wired to loop-governor #14; shared-task-list/kanban broker; structured `output_schema` return; `@<name>` mention routing; profile `extends:` inheritance; `deny_tools`/`mcp_servers`/`worktree` fields. **Explicitly out of product-core forever:** external daemon driver / Web UI control plane (thin transport wrappers over `api.runtime.subagents`), a JS workflow runtime, and any cloud presence.

---

## 9. ADR plan

- **ADR-0008 — AMEND (not overturn).** Add a clause distinguishing (a) **single-agent profile identity** — format/discovery/resolution — as a *product-core resource concern, isomorphic to skill loading* (L1-adjacent, not the multi-agent orchestration the kernel forbids), from (b) **multi-agent spawn/teams/routing** — still the extension layer. Scope the sentence "runtime core에는 multi-agent 개념을 두지 않는다" explicitly to the **kernel** (still true — untouched) and to **orchestration** (still true — extension). Record the **mechanism/policy rejection criterion** verbatim as a review gate: *"Product-core owns the profile format/resolver and a subagent-runtime CONTRACT (types + binding slot) only. The spawn IMPLEMENTATION, caps, registry, and all topology/task-list/goal/dashboard decisions are extension policy. A PR adding spawn behavior, a supervisor, or a subagent lifecycle event TYPE to product-core or the kernel is rejected on sight."*
- **NEW ADR — "agent-profile format & single-agent invoke"** (Phase 1). Covers format, discovery dirs, `profile_to_argv`, `--agent`, `/agents list|show|use`, the `--*-system-prompt-file` flags, the `--no-builtin-tools` fix. Grounds the boundary in profile-first's "first-class resource like skills" justification. Records the one-way pi-compat (a pi role loads on aelix; an aelix profile does not run on pi).
- **NEW ADR — "subagent-runtime seam & `aelix-agents` extension"** (Phase 2–3). The product-core CONTRACT (Protocol/types/events/`CONTRACT_VERSION`) + `runtime.subagents` binding slot; the bundled first-party extension owning the implementation; the mechanism/policy line; and the explicit rejections — **NOT** kernel event types (refute orchestration-first) and **NOT** a product-core `SubagentSupervisor` (refute orchestration-first + profile-first's task-tool-in-core).
- **NEW ADR — "print-mode & rpc event-envelope stability contract."** Versions the `--mode json` / rpc event schema + `SubagentResult`; mandates the cross-channel parity test; ties ADR-0056/0057/0071. Nothing parses child output until this lands.
- **ADR-0165 — note.** The deferred subagent-status subsystem is fulfilled at the extension layer from the child's own event stream; kernel `AgentStart/EndEvent` payloads stay empty. No kernel/`tui/types.py` enrichment is required.
- **Supersede the #16 decision** (documented in `project_16_pi_orchestrator_multiagent`). Keep its flag inventory (correct). Replace its placement: the runtime is a **bound service in a bundled default-on extension**, not ad-hoc `RpcClient`s trapped in a tool-body stack frame — record why (observability, a shared registry, and non-hostility to a *second* orchestration extension).

---

## 10. Risks, mitigations, and non-goals

**Risks & mitigations**
- **Token multiplication** (each child runs its own model+tool loop; depth×concurrency compounds). *Mitigate:* default-off flag through Phase 3; runtime concurrency caps (4) + `MAX_PARALLEL=8` + `max_depth=1` (leaf-omits-`aelix-agents`); 50 KiB summary cap; summary-only return; per-team token budget; **cost surfaced in every `SubagentResult`, the statusline, and `/agents list`** so multiplication is visible, not hidden; "read-heavy work first" guidance in the tool description.
- **Child lifecycle / orphans.** *Mitigate:* per-path documented kill grace (5 s one-shot / 1 s rpc) → SIGKILL; `finally: stop()` on cancel; `stop_all()` on parent shutdown; spawn children in their own process group; the 100 ms startup-death check (`rpc_client.py:141-156`); temp 0600 prompt unlinked in `finally`.
- **Extension-set divergence (parent↔child).** A profile's `extensions:` becomes the child's *entire* set via `--no-extensions -e …`. *Mitigate (mandatory):* the runtime **always prepends `GuardrailExtension()` + permission** (like `entry.py:690-702`) after honoring `--no-extensions`; the secure default is `inherit_extensions: false` (child gets *less* ambient code, never more); a child never gains a permission mode looser than the parent's.
- **Privilege escalation / prompt injection via a `.md`.** *Mitigate:* project-local profiles trust-gated (`entry.py:849-886`) + `confirm_project_agents` prompt; a spawned child's effective tool/extension set is **intersected with the parent's grant** (never a union) unless a human approves; profiles are Ed25519-signed; permission approval is never delegated.
- **Signing / marketplace supply chain.** *Mitigate:* provenance on install (existing Ed25519, sha256-bound); a profile's `extensions:` refs resolve through the *same* marketplace trust + signature verification as any extension; signature-required policy (fail-closed).
- **RPC / print-mode protocol drift** (result parser couples to `message_end`/`tool_result_end`/`usage.*`). *Mitigate:* centralize all event parsing in the runtime; version the envelope (`CONTRACT_VERSION`); the cross-channel parity test asserts PrintChannel ≡ RpcChannel; both flow through the single `_build_harness_options` build (`entry.py:1366`) so they emit identical shapes.
- **Seam-in-a-replaceable-layer** (kernel-minimalist's own flaw). *Mitigate:* the *contract* (Protocol/types/events/version) lives in **product-core** and is ADR'd + versioned; only the *implementation* is in the (replaceable) extension. A second orchestration extension or a future Web UI/daemon builds on the product-core contract, never a fork.
- **Scope creep into the runtime** (a supervised runtime is a policy magnet). *Mitigate:* the mechanism/policy line is written into the ADR as an enforceable rejection criterion (§9); the runtime is a bound service in an extension, not a core module, so growth is visible and reviewable.

**Explicit non-goals**
- **No external-driver daemon in product-core.** A Unix-socket/NDJSON control plane or Web UI is a *thin transport wrapper* over `api.runtime.subagents`, shipped as 3rd-party/aelix-server — never in product-core, never the *first* shape.
- **No cloud presence / Radius.** Rejected entirely. All spawn is local subprocess; discovery is filesystem-only; telemetry stays off; marketplace is offline-capable.
- **No Hermes home-directory bundle** (per-profile `.env`/`state.db`/cron). Single signed `.md`; credentials stay in the shared auth store.
- **No JS Workflow runtime.** aelix is Python; a deterministic Python "recipe" runner is a later extension, if ever.
- **No multi-agent concept in the kernel.** Kernel stays at zero. No `subagent_*` event *types* in the kernel; no supervisor in product-core.
- **No goals in the profile format.** Identity (profile/WHO) and objective (goal/WHAT) stay orthogonal; goals route to loop-governor #14 as a separate future extension.

---

## 11. Decision log (conflicts resolved, with reasons)

1. **Base design = kernel-minimalist.** Highest aggregate score (21.5) and it wins the keystone pi-parity/kernel-philosophy lens (9) that *is* aelix's thesis (ADR-0002/0008). Reconciled with profile-first's resource *framing* and orchestration-first's runtime *observability*.
2. **Spawn implementation placement = bundled default-on first-party extension `aelix-agents` + a product-core CONTRACT (seam).** Rejects profile-first's spawn-service+task-tool *in the product-core module* (Judge-1: that is verbatim ADR-0008 L2) and orchestration-first's product-core `SubagentSupervisor` (Judge-1: pi quarantined its orchestrator as a separate package). Reconciles Judge-1 vs Judge-3 by defining **"core alone usable" = aelix-as-shipped, including bundled default-on extensions** (user-facing) while keeping the *implementation* out of the product-core module (architectural).
3. **Observability seam ownership = product-core owns the CONTRACT (Protocol + types + typed events + version + binding slot); the bundled extension owns the IMPLEMENTATION.** Fixes kernel-minimalist's confirmed "unowned observability seam" flaw *and* Judge-2's "under-specified binding" *and* its "hostile to a second orchestration extension" critique — without a supervisor in core. `api.runtime.subagents` is verified-idiomatic against the existing `bind_*` + `@property` family.
4. **Events = open EventBus with a product-core-typed `SubagentProgress` payload.** Rejects orchestration-first's *kernel* event types (doubly unforced) and rejects Judge-2's "must edit the closed `HOOK_RESULT_TYPES`" as over-invasive for v1; typed-payload-on-open-bus gives the static contract at lower cost. HookBus promotion is optional future hardening.
5. **Kill grace = 5 s one-shot / 1 s rpc, documented + settings-exposed.** Resolves kernel-minimalist's left-as-TODO ambiguity by deciding one convention per spawn path with rationale.
6. **Profile format = single signed `.md`** (unanimous). Rejects the owner's literal per-agent `system.md`-in-a-dir and the Hermes bundle (credential blast radius).
7. **Schema = minimal ~14 fields** (kernel-minimalist), with **one** prompt-mode field and **one** `inherit_extensions` boolean — fixing profile-first's two-conflicting-`inherit_*`-booleans friction. Deny-list/mcp/worktree/`extends`/`output_schema` deferred behind the same parser.
8. **Depth cap = leaf-omits-`aelix-agents` mechanism** + `AELIX_SUBAGENT_DEPTH` guard. `max_depth=1` is default and enforcement at once.
9. **Feature flag = default-off through Phase 3, flip default-on at Phase 4.** Reconciles "stabilize behind a flag" (Codex) with "core-alone-usable at ship" (GTM), given the pre-beta stage.
10. **Goals (Codex-style) deferred** to a separate later extension wired to loop-governor #14; kept out of the profile format (identity vs objective orthogonal).
11. **The `--*-system-prompt-file` flags are genuinely forced** (verified: `--system-prompt`/`--append-system-prompt` take literal strings, `args.py:281-288`), correcting kernel-minimalist's one feasibility slip and adding the *only* truly-forced new spawn flag to its "smallest core."
