# 0197. Subagent-runtime seam & the bundled `aelix-agents` extension (P2)

Status: Accepted (2026-07-26) — owner-ratified authority posture (Option C).
**Amended 2026-07-27 (owner): §(i)'s consent dialog fires only when write
authority is at stake, and the widening option is offered only to a profile that
DECLARED it needs write authority — see `#### Amendment (2026-07-27, owner)` in
§(i) and residual R7.**
Design record that lands with the P2 implementation (same pattern as
ADR-0186/0187/0188/0189/0196).
Date: 2026-07-26
Amends: ADR-0008 (see its `Amendment (2026-07-26)` section — this ADR is the
narrow amendment that clause points at)
Relates: ADR-0002 (small kernel — **untouched here**), ADR-0004 (policy as a
built-in extension — the prepend order this ADR must not disturb), ADR-0021 /
ADR-0027 (`execution_mode="sequential"` batch downgrade, which §(i) uses as a
security control rather than a performance one), ADR-0028 (extension discovery
tiers the depth guard has to survive), ADR-0157 (permission postures + the
approval dialog — the posture ladder this ADR gives a **second** consumer),
ADR-0178 (project-trust bootstrap — the resolution ladder §(g) corrects),
ADR-0196 (P1 agent-profile format; its four deferrals are disposed of here),
ADR-0198 (the print-mode JSON envelope this spawner consumes).
Source spec: `.omc/specs/multiagent-profiles-teams-architecture-spec.md` §2.5,
§6, §8 **Phase 2**, §9, §10.
Pi pin: `earendil-works/pi@734e08e`.

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적
목표입니다."** — delegation as shipped here is **AELIX-ORIGINAL**. pi's
`pi-subagents` is the *shape* reference (one parent process, one child process,
a JSON event stream, a returned result), but pi has no posture clamp, no
consent gate, no depth guard, no cap in single mode, no roster injection and no
timeout. Parity is of SHAPE, not of source; every divergence below is named.

**Anchor convention.** Every `file:line` in this record was re-read against the
P2 baseline `ab1932a` (branch `feat/p2-subagent-runtime`, before the P2 hunks
landed). The P2 edits themselves shift some of those numbers by a few lines;
the anchors are given as the *evidence* for a decision, not as a maintenance
contract.

## Scope of P2 — what this ADR does NOT decide

**One parent spawns ONE child, and only one level deep.** No parallel mode, no
chain mode, no teams, no long-lived `RpcChannel`, no daemon, no dashboard. The
kernel `packages/aelix-agent-core` is **byte-unchanged** — every kernel anchor
below is a READ. Product-core gains an INTERFACE (types, a Protocol, a binding
slot, two flags, one settings key) and nothing else: it never builds a child
argv, never calls `create_subprocess_exec`, never parses a child stream, and
never authors a consent decision. All of that lives in the bundled
`aelix_agents` extension.

Delegation is **default-off** in P2 (`[features] agents`, spec §8: default-off
through Phase 3, flipped at Phase 4).

## Context

Spec §8 Phase 2 asks for: a subagent-runtime contract in product-core; a
bundled `aelix-agents` extension that owns the spawn; the `agent` tool; the
`/agents run` door; the 0600 temp prompt file ADR-0196 deferred; the parent-grant
intersection ADR-0196 deferred; and the `[features] agents` flag ADR-0196
deferred.

A four-lens adversarial review executed probes against the real tree before any
code was written, and two of its findings invalidated the ratified design as
drafted. The measured evidence (`/workspaces/aelix-ai/.venv/bin/python`,
baseline `ab1932a`):

```
'agent' in _MUTATING                                     -> False
_rule_key("agent", {"profile": "a"})                     -> 'tool:agent'
_rule_key("agent", {"profile": "b"})                     -> 'tool:agent'     # args-blind
_is_auto_allowable_write(".aelix/agents/evil.md",  cwd)  -> True
_is_auto_allowable_write(".aelix/extensions/x.py", cwd)  -> True
_is_auto_allowable_write(".aelix/mcp.json",        cwd)  -> True
_is_auto_allowable_write(".aelix/settings.json",   cwd)  -> True
asyncio StreamReader default limit                       -> 65536
  200 000-byte line -> ValueError: Separator is not found, and chunk exceed the limit
  next readline()   -> ValueError: Separator is found, but chunk is longer than limit
child writes 1 MiB to stderr between two stdout lines, parent drains stdout first
                                                         -> hung for the full 2-minute budget
```

Read line by line: **delegation asks nobody.** `"agent"` is not in `_MUTATING`
(`builtin/permission.py:64`), so an `agent` tool call falls into the
non-mutating branch at `permission.py:324-325` and is *silently allowed* — the
one action a model can take that starts a whole second agent with real authority
has an empty gate. **A writable child can author the parent's next identity**:
all four `.aelix/**` control files were auto-approvable under AUTO_ACCEPT, and
those are exactly the resources the Project Trust gate exists to guard
(`cli/project_trust.py:112-177`). **A routine child event is unreadable**: a
`message_end` carrying one `read` of `harness/core.py` (199 246 bytes)
serializes to ~207 KB, and `readline()` raises past 64 KiB *unrecoverably* — the
terminating `agent_end` is then lost, so every such run would report a failure.
**Two pipes drained in sequence deadlock**, and a real aelix child writes to
stderr routinely (`modes/print_mode.py:225`, `:239`, provider SDK/httpx logging,
any extension `print(..., file=sys.stderr)`).

## Decision

### (a) Three bands, and the seam between them

| band | package | may it spawn? |
|---|---|---|
| kernel | `packages/aelix-agent-core` | **zero bytes changed**; no profile concept, no spawn concept, no multi-agent event type |
| product-core | `packages/aelix-coding-agent/src/aelix_coding_agent` | **never.** Declares the CONTRACT and *calls* a bound Protocol |
| bundled extension | `packages/aelix-coding-agent/src/aelix_agents` | **the only band that spawns** |

`subagent_contract.py` is a top-level product-core module (sibling of
`login_registry.py` / `model_registry.py`) carrying types, a `Protocol`, three
event-channel names, a version range and the depth constant. It imports nothing
from `aelix_coding_agent` at runtime — only stdlib plus a `TYPE_CHECKING` import
of `AgentProfile` — so the contract can never grow behaviour by accident. The
reverse direction is deliberately open: `aelix_agents.*` may import
`aelix_coding_agent.*` (that is what lets §(g) reuse the shipped
`has_trust_requiring_project_resources`, exported at `project_trust.py:103`).
Product-core may name `aelix_agents` at **exactly one** site — a function-level
import inside `cli/entry.py::_async_main`. Both directions are test-enforced.

`_ExtensionRuntime` gains a `subagents` property and `bind_subagents` /
`unbind_subagents` (`extensions/api.py`, after `bind_ui` at `:527-535`). `None`
is the NORMAL state — delegation is default-off and a child never binds — so
every caller degrades rather than assumes.

**The bundled extension ships inside the existing `aelix-coding-agent` wheel**
(`packages/aelix-coding-agent/pyproject.toml:100-101` gains
`"src/aelix_agents"`), loaded as a conditional `prepend` **appended after**
Guardrail and Permission. A separate distribution was rejected on measured cost:
it trips `tests/test_license_sync.py:19`,
`tests/test_release_version_consistency.py:26-31`, `scripts/generate_sbom.py:37`,
`RELEASING.md`, `install.sh:165`, and needs a new PyPI pending publisher — six
release-plumbing edits to gain nothing a package directory does not already give.

### (b) `bind_subagents` refuses four ways — and the version is a RANGE

```
MIN_SUPPORTED_CONTRACT_VERSION <= runtime.contract_version <= CONTRACT_VERSION
```

1. **Version range, not exact equality.** An exact-equality gate is either dead
   code (a runtime importing the live constant can never mismatch) or a hard
   break for every third-party runtime the day P3 bumps the number. The rule
   that makes the window real is stated in the constant's own docstring:
   **adding a DEFAULTED dataclass field is additive and does NOT bump the
   version**; only changing an existing field's shape or adding a required
   parameter does. `details`, `dropped_lines` and `permission_mode` all landed
   under that rule inside P2 itself.
2. **Double bind.** A second runtime does not silently displace the first —
   extension load order is not a contract, and a silent swap would leave the
   `agent` tool and `/agents run` driving two different child registries. A
   second orchestration engine must opt in with `replace=True`. The `bind_ui`
   precedent (`api.py:527-535`, a one-line `self._ui = ui` with no validation) is
   singular because there is only ever one UI; it is the wrong precedent here.
3. **Depth.** Product-core will not HOLD a runtime inside a delegated child,
   regardless of which extension tier produced it (see §(c)).
4. **Shape** *(added in P2 review, MEDIUM #7).* `contract_version` is
   **self-declared** — an `int` the runtime sets on itself — so on its own it was
   the seam's only conformance check and proved nothing about the object having
   the members the Protocol names. `SubagentRuntime` was already
   `runtime_checkable` and was never used. Measured before the gate:
   `bind_subagents` accepted `class _Foreign: contract_version = 1` (one
   attribute, no methods) — the repo's own test bound exactly that — and a
   consumer calling `runtime.list()` got `AttributeError`, which `/agents run`'s
   `except Exception` renders as a generic red line with no hint that the runtime
   is malformed. The `isinstance` runs **after** the version check, so a runtime
   built against a future contract is told about the version rather than about
   whichever member that version renamed, and the message names the missing
   members so a third-party implementer can act on it.

**A refusal a caller can cure is TYPED** *(P2 review, MEDIUM #8).*
`subagent_contract.ProjectScopeRefused` is the contract's declaration of the one
`resolve_profile` failure `/agents run` may answer (§(f)). Before it,
`tui/commands.py` decided with `_PROJECT_SCOPE_MARKER = "per-identity
confirmation" in str(exc)` — a phrase produced by exactly one implementation — so
a second runtime implementing `resolve_profile` exactly as the Protocol docstring
instructs got no confirmation path at all and its project-scoped profiles were
simply unrunnable. The substring survives as a documented back-compat fallback
for runtimes predating the class; the bundled implementation raises
`ProjectScopeProfileError(ProfileError, ProjectScopeRefused)` so every existing
`except ProfileError` keeps working.

**`AgentsExtension` binds with `replace=False`, explicitly** *(P2 review,
MEDIUM #13).* It read `replace=runtime.subagents is self._runtime`, which is
provably constant `False` — `self._runtime` is assigned a fresh
`_SubagentRuntimeImpl` three lines above — and as a live expression invited the
reading that the bundled extension can take a seam over. It cannot and must not.
The consequence is on the other side: a second engine that DOES pass
`replace=True` gets a silent takeover, and `bind_subagents` returns the displaced
runtime to nobody, calls no `stop_all` on it and offers no `on_replaced`. Without
a check the displaced extension keeps its `agent` tool registered and its
`tool_call` hook armed, so the MODEL's delegations keep spawning through the
displaced registry while `/agents run` drives the new one — the split-brain
refusal 2 exists to prevent, arriving through the spelling refusal 2 permits.
`AgentsExtension` therefore re-reads the seam on every `tool_call` and stands
down when it no longer holds it. A handoff protocol on the contract itself
(returning the displaced runtime, an `on_replaced` callback) is **P4**, with the
keyed registry below.

`ExtensionError`'s `code` Literal (`api.py:127`) is widened with
`"contract_mismatch"`; raising a code outside the declared union was itself a
violation. `unbind_subagents(runtime)` is **identity-scoped**: an extension's
`api.add_cleanup` (`api.py:1554`) must call it rather than
`bind_subagents(None)`, or whichever extension tears down first nulls the slot
for a runtime it does not own.

**REJECTED → P3/P4: a keyed multi-runtime registry.** It is only exercisable
once a second runtime exists (P4 `aelix-team`), and shipping it now freezes an
unvalidated key namespace into `CONTRACT_VERSION 1`.

### (c) The depth guard — a pull-forward, shipped as a GUARD not a feature

Spec §8 lists `max_depth` via `AELIX_SUBAGENT_DEPTH` under **Phase 3**. P2 ships
it anyway, because P2 without it is a fork bomb. It lands deliberately
un-featured: **hardcoded `MAX_SUBAGENT_DEPTH = 1`, no `max_depth` profile field,
no configurability, no setting.** Three layers, because each covers a hole the
others do not:

1. **Build-time suppression.** `cli/entry.py::_build_harness_options` does not
   prepend `AgentsExtension` when `subagent_depth() >= MAX_SUBAGENT_DEPTH`, so a
   child physically has no `agent` tool.
2. **Seam-level refusal.** `bind_subagents` itself refuses at depth. This is the
   layer that matters: `extensions/loader.py:475`'s
   `if not no_discovery and include_entry_points:` kills tier 4 under
   `--no-extensions`, and `agents/profile.py:342-351` (THE RCE CUT) blocks
   `extensions:` at project scope — but a **user-scope tier-1** extension still
   loads inside a child with `inherit_extensions: true`. Putting the invariant in
   the seam turns "we did not load our extension" into "product-core will not
   hold a runtime at depth".
3. **Tool-time re-check.** The `agent` tool returns `ToolResult(is_error=True)`
   at depth. It **returns**, never raises.

**`role` ships INERT.** `role: leaf` maps the child's depth env var to
`MAX_SUBAGENT_DEPTH`; `orchestrator` maps it to `subagent_depth() + 1`. At
`MAX = 1` **both branches yield 1**, and `agents/profile.py:208` defaults `role`
to `"leaf"` anyway, so **every profile is a leaf and the field is arithmetically
inert in P2**. It is wired now so P3 only has to raise the cap, and its test is
made discriminating by monkeypatching `MAX_SUBAGENT_DEPTH = 2` (at the shipped
value the test could not fail).

**Accepted residual risk:** a child holding `bash` in its intersected grant can
run `AELIX_SUBAGENT_DEPTH=0 python -m aelix_coding_agent …`. A bash environment
scrubber is **out of P2 scope** (P3) — it is a general containment problem, not a
delegation one, and a partial scrubber would advertise a guarantee it cannot keep.

### (d) `/agents run` is a product-core built-in — spec §6.3 is not implementable

Spec §6.3 asks for `register_command("run", …)` under `/agents` from the
extension. Measured: `tui/shell.py:2452-2459` runs `match_command` (built-ins)
and `command.handler` first, falling through to `dispatch.try_execute` only at
`:2465-2470`; and `extensions/command_dispatch.py:76-85` splits a command on the
FIRST space, so an extension command name **can never contain one**. A built-in
`/agents` therefore shadows the whole first word and an extension can never serve
`/agents run`. The `run` branch lands in the existing `_agents_handler`
(`tui/commands.py:640`).

It stays INTERFACE ONLY: it reads `ctx.harness.runtime.subagents`, calls
`resolve_profile` and `spawn`, renders the returned `SubagentResult`, and
degrades with a message when the runtime is `None`. It never renders a consent
dialog, never encodes a posture rule, and never sees a grant.

### (e) The child permission floor — the CLAMP

**How a headless child behaves without it.** `builtin/permission.py::_on_tool_call`
(`:304-392`) is one ordered ladder:

| order | site | behaviour |
|---|---|---|
| PLAN + mutating | `:318-321` | `block=True` — **above** the headless branch, so it holds in a child |
| non-mutating | `:324-325` | `return None` — silently allowed; **this is where an `agent` call lands today** |
| session rule | `:330-331` | allow |
| YOLO | `:339-340` | allow |
| AUTO_ACCEPT write | `:348-353` | allow when `_is_auto_allowable_write` passes — **also above** the headless branch |
| AUTO | `:361-377` | tree-sitter bash classification |
| **headless** | **`:382-383`** | **`if not ctx.has_ui: return None`** ← terminal. Allow. |
| prompt | `:385-392` | the 4-option modal, serialized by `async with self._lock` at `:387` |

A `--mode json -p` child has `ctx.has_ui == False`, so **without the clamp it
auto-allows every mutating tool**, with no channel back to the parent.

**`ctx.has_ui` — the mechanism, corrected.** It is *not* `app_mode ==
"interactive"`. `cli/entry.py:1095` defines a same-named **local** that only
feeds `resolve_project_trusted(has_ui=…)` at `:1096-1100` and never reaches the
extension runtime. The real property is `extensions/api.py:957`, returning at
`:977-978`:

```python
runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
return runtime.ui is not HEADLESS_UI_CONTEXT
```

The conclusion (a child is `False`) is unchanged, but the **character** is
different: this is a **time-varying value, not a mode**. It is `False` during
`harness.bootstrap()`, flips `True` at `tui/shell.py:1892`, is re-pointed on every
harness rebuild (`shell.py:1565`), and reverts to `False` at `shell.py:1950`.
**Consequence, binding for §(i): read it LIVE, immediately before prompting, and
never cache it.**

Two product-core additions, both interface:

1. **`--permission-mode <default|auto-accept-edits|plan|yolo|auto>`** in
   `cli/args.py`, applied when seeding `PermissionPosture`. The spawner **always**
   passes it explicitly. A bogus value appends a `{"type": "warning"}` entry to
   `parsed.diagnostics` and drops, mirroring `--thinking` (`args.py:413-430`) —
   it does *not* print to stderr and does *not* record into `parsed.provided`.
2. **A child-only headless floor** — `headless_default: Literal["allow","block"]
   = "allow"` on the `PermissionExtension` dataclass, flipped to `"block"` by
   `entry.py` only when `subagent_depth() > 0`. Every existing `-p` / `--mode
   json` / `--mode rpc` user is byte-unchanged.

**The floor is a belt, not the guarantee.** `permission.py:348-353` returns
~30 lines ABOVE `:382-383`, so a child running under AUTO_ACCEPT never reaches
the floor at all. The actual guarantee is the posture the child runs under, and
the spawner computes it:

**The mapping is a CLAMP over a total order, applied to every row — not a lookup
table.** `_RANK = {PLAN:0, DEFAULT:1, AUTO_ACCEPT:2, AUTO:3, YOLO:4}`.

* `deny` → `PLAN`, always.
* `auto` → requests `AUTO_ACCEPT`.
* `ask` **without** a live parent UI → `PLAN` (nobody to ask, so it collapses
  exactly like `deny`).
* `inherit`, and `ask` **with** a live UI → `{DEFAULT→PLAN, PLAN→PLAN,
  AUTO_ACCEPT→AUTO_ACCEPT, AUTO→AUTO_ACCEPT, YOLO→YOLO}`.
* Then the clamp: `requested if _RANK[requested] <= _RANK[parent] else parent`.
* Then `DEFAULT → PLAN`, because DEFAULT in a child means "prompt" and the child
  has no prompt; PLAN gives the same denial with a model-actionable reason and
  does not depend on `headless_default` being set correctly. Strictly tighter,
  never looser.

The realized table (executed, not reasoned):

| parent posture | `inherit` / `ask`+UI | `auto` | `ask` no-UI | `deny` |
|---|---|---|---|---|
| `plan` | `plan` | `plan` | `plan` | `plan` |
| `default` | `plan` | **`plan`** | `plan` | `plan` |
| `auto-accept-edits` | `auto-accept-edits` | `auto-accept-edits` | `plan` | `plan` |
| `auto` | `auto-accept-edits` | `auto-accept-edits` | `plan` | `plan` |
| `yolo` | `yolo` | `auto-accept-edits` | `plan` | `plan` |

The bolded cell is the vulnerability this closes: before the clamp, a
project-authored profile declaring `approval_mode: auto` lifted a **DEFAULT**
parent to auto-accepted repo-wide writes.

**The project-scope widening ban is a rank-MIN, not an assignment — and it is
inert under the current clamp.** The first form of the rule
(`requested = <the inherit result>` whenever `scope == "project"` and
`approval_mode == "auto"`) was executed and produced, for a **YOLO** parent,
`user scope = auto-accept-edits` but `project scope = yolo`: the "ban" made the
checked-in repo file **wider** than the user's own profile. The shipped form
substitutes only when the fallback is strictly tighter. Verified honestly: with
the rank-min in place, the clause changes **no** row — the clamp alone already
produces the same answer everywhere, because `auto` requests `AUTO_ACCEPT` and
`AUTO_ACCEPT` is already ≤ every parent that could widen. It is retained as a
**defensive invariant with a test**, not as a load-bearing computation: it
becomes load-bearing the moment the `auto` mapping or the clamp ceiling changes,
and its test pins "project is never looser than user" for every parent posture so
that such a change has to state its intent. The project-scope rule that **does**
bite today is §(i)'s absolute refusal to offer widening to a project-scoped
profile.

**Restating spec §10 honestly.** The old sentence — *"a child never gains a
permission mode looser than the parent's"* — is true **of the clamp**, and the
shipped guarantee is one clause longer:

> A child never gains a permission mode looser than the parent's **unless a
> human, at the parent's own TUI, explicitly granted it for that one spawn — and
> then never above `auto-accept-edits`, and never for a project-scoped profile.**

The shorter version must not survive anywhere: it is written in this longer form
in `aelix_agents/posture.py`'s module docstring and in `consent.py` as well.

### (f) Project-scoped identity consent — the model door is fail-closed

`SubagentRuntime.resolve_profile(name_or_path, *, allow_project: bool = False)`
refuses a `scope == "project"` profile unless `allow_project is True`, with the
message shape `agents/service.py:241-247` already uses.

* **The `agent` tool (model-driven) always passes `allow_project=False`.
  Fail-closed, no prompt, ever.** The *model* picks the profile string, so repo
  content (a README, a comment, a fixture) must not be able to select a
  project-authored identity whose body replaces the system prompt. This is
  deliberately **stricter** than §(i)'s consent dialog, and the asymmetry is the
  point: identity selection is not something a modal should rescue when the model
  wrote the name.
* **`/agents run` (user-typed)** may set `allow_project=True`, but only through
  the gate `--agent` already uses at startup (`cli/entry.py:1489-1504`) or an
  explicit `--approve` (`parsed.project_trust_override is True`). The existing
  `AgentProfileService.confirm_project` callback is reused, not re-implemented.

The rationale is ADR-0196's, unchanged and re-stated in the Protocol docstring:
directory trust is a yes-once decision **ancestors inherit**
(`project_trust.py:60-61`); it is not consent to a project-local *identity*,
which additionally **wins a `name` collision** against the user's own.

### (g) Child project trust — the TWO-CLAUSE `--no-approve` rule

**The claim that started this section was false and is deleted.** The design
draft asserted "the child treats its cwd as UNTRUSTED". It does not.
`cli/project_trust.py::resolve_project_trusted` (`:470-580`) is seven steps:
**1** explicit override, no persist (`:521-523`) · **2** no trust-requiring
resources → `True` (`:525-527`) · **3** `project_trust` extension vote
(`:531-548`) · **4** persisted `trust.json`, **nearest-ancestor walk**
(`:550-557`) · **5** global `defaultProjectTrust` (`:559-563`) · **6** headless →
`False` (`:565-567`) · **7** prompt (`:569-580`). The headless deny is step
**six**, reached only after all of those.

**What an always-on `--no-approve` would actually buy — exactly one escalation,
measured.** The commonly-told story ("Alice trusted the repo, the child
re-executes `.aelix/extensions/evil.py`") is **not** an escalation: the parent
already `exec_module`'d that same file at startup, same user, same machine, same
keys. The real one is:

> Alice trusts a monorepo **root** once (persisted). The root has no `.aelix/`
> at all, so nothing has ever executed. A vendored dependency merged three
> months ago carries `monorepo/vendor/sdk/.aelix/extensions/telemetry.py`; the
> parent runs at the root and has **never** loaded it — `extensions/loader.py:432-435`
> scans `cwd/.aelix/extensions` only. The model now calls
> `agent(profile=…, cwd="vendor/sdk")`. §(h)'s containment rule allows it (it is
> inside the parent cwd). The child starts in `vendor/sdk`, step 4's ancestor
> walk finds the root's `True`, and **`telemetry.py` executes.**

Measured: parent at root → nested extension NOT executed; child at `vendor/sdk`
without the flag → **executed**; child at `vendor/sdk` with `--no-approve` → not
executed. The trigger is a **model-chosen `cwd`**, and nothing prompts the human
at delegation time. Monorepos, vendoring, submodules and checked-in
`node_modules`-shaped trees all have this shape.

**What an always-on `--no-approve` would cost — a silent regression of a
default.** `has_trust_requiring_project_resources` (`project_trust.py:112-177`)
checks `.aelix/extensions/` (`:135-138`), `.aelix/mcp.json` (`:143-146`) and
`.aelix/agents/` (`:170-173`) — and **not `.aelix/skills/`**. A skills-only repo
therefore returns `False`, step 2 returns `True` with no prompt, and project
skills load today. Forcing step 1 to `False` makes `_resolve_skill_dirs`
(`entry.py:691-692`, `:719`) drop the directory — while `inherit_skills` defaults
to **`True`** (`agents/profile.py:175-181`, whose own comment reads *"skills are
inert prompt text, extensions are code"*). The child's Notice goes to stderr, and
§(k) surfaces stderr only on failure, so on a successful run the user never
learns.

**Decision: the flag is CONDITIONAL.** `aelix_agents/trust.py::child_trust_argv`
returns `[]` or `["--no-approve"]`:

1. **Same cwd AND the gate has nothing to gate** (`.aelix/extensions`,
   `.aelix/mcp.json`, `.aelix/agents` all absent) → emit **nothing**. Step 2
   would have returned `True` for the parent too, so there is no authority to
   withhold; forcing `False` here only strips `.aelix/skills/`, which the gate
   never guarded.
2. **Any other case — most importantly a DIFFERENT cwd, which is always
   MODEL-CHOSEN** → `--no-approve`. This is the whole security value of the flag.

Wherever the trust gate is actually live, this is **security-identical** to
passing `--no-approve` unconditionally.

**What it does not prevent, stated plainly.** `--no-approve` stops the child from
**loading** a project-local extension, MCP config or agent profile. It does
nothing about the child **writing** one — that hole is closed separately, by
§(i)'s `.aelix` mitigation. It also does not bound *where* the child runs; that
is §(h)'s containment rule. The two compose: `is_relative_to` bounds **where**,
`child_trust_argv` bounds **what executes there**.

Accepted costs (documented, not papered over):

* `inherit_extensions: true` + auto-discovered project extensions are lost
  whenever clause 2 fires. Narrow: `inherit_extensions` defaults to `False`
  (`agents/profile.py:187-191`), and a profile's *explicit* `extensions:` list is
  rendered as `-e <abs path>`, which loads via loader **tier 3**
  (`loader.py:443-464`) outside both discovery guards — so an explicit list still
  works under `--no-approve`. And `extensions:` is forbidden outright at project
  scope (`agents/profile.py:342-351`), so this bypass is user-scope-only, which is
  exactly right.
* Project-local MCP servers are lost under clause 2 — arguably a feature; §(h)
  already pops `AELIX_MCP_CONFIG` for the same reason.
* A global `defaultProjectTrust: "always"` is ignored under clause 2. One line
  could honour it; **not in P2**.
* **Not a cost — project-scoped agent profiles.** The spawner never passes
  `--agent <name>`; it passes the profile *body* through
  `--system-prompt-file` / `--append-system-prompt-file`
  (`agents/resolver.py:176`, `:178`). The child never re-runs `discover_profiles`.
* **Not a cost — session-only trust.** It already never reaches a child:
  `interpret_trust_option` returns `remember=False`, nothing lands on disk, and
  the child denies at step 6.

**REJECTED → P3:** the wider variant that also honours a persisted `True` plus a
user-scope clause (recovering `inherit_extensions: true` and project MCP). It is
~6 more lines but carries a named **TOCTOU** residual — an attacker creating
`.aelix/extensions` between the parent's predicate check and the child's own
step-2 re-evaluation.

### (h) The child: argv, env, cwd, stdio, and the 0600 prompt file

**Narrowing happens before argv is rendered.** The child's tool set is the
**intersection** `requested ∩ parent_grant ∩ ALL_TOOL_NAMES` minus `{"agent"}`,
where `parent_grant` is the live `api.runtime.actions.get_active_tools()`
(`api.py:945`). The intersection is structural — a child can never exceed the
parent's grant. `∩ ALL_TOOL_NAMES` prevents naming an extension/MCP tool the
child cannot build. `− {"agent"}` is a second, independent anti-nesting layer.
What the profile asked for and did not get rides back on
`SubagentResult.dropped_tools`.

An **empty** intersection must render `--no-tools`, never `--tools ''`:
`agents/resolver.py:154-156` documents why — `--tools ''` parses to `[]`, which
`_resolve_active_tools` reads as falsy → `None` → **every** tool active, the exact
inversion. ADR-0196 fixed this at the format layer; the spawner must not
re-introduce it.

argv is `[sys.executable, "-m", "aelix_coding_agent", *profile_to_argv(...),
"--permission-mode", <mode>, *child_trust_argv(...), "--no-agents"]`. Never
`-m aelix` (`rpc/rpc_client.py:466` does exactly that and it is a **live bug** —
`python -m aelix` is the umbrella meta-package demo) and never the `aelix`
console script. `profile_to_argv` (`agents/resolver.py:183-206`) already prepends
`["--mode","json","-p","--no-session"]` (`:200-202`) and appends `f"Task: {task}"`
(`:204-205`); **the `"Task: "` prefix is load-bearing** — a bare task starting
with `--` is swallowed into `parsed.unknown_flags` with **no diagnostic**
(`args.py:504`/`:510`/`:513`), while `-` produces `Unknown short flag`
(`:515-517`). Because every security assertion here is argv-shaped and `args.py`
is silent about unknown long flags, the spawner's exact argv is fed through
`parse_args` in a test that asserts `unknown_flags == {}` — a renamed or typo'd
`--permission-mode` would otherwise ship an auto-approving child with a green
suite.

env is `dict(os.environ)` plus: `AELIX_SUBAGENT_DEPTH` (§(c));
`AELIX_STDIN_TIMEOUT="1"` (an inherited `"0"` means *wait forever*,
`entry.py:172-174`); `AELIX_MCP_CONFIG` **popped** (otherwise every child fans out
its own MCP subprocesses); `PYTHONPATH` prepended with the product-core parent
directory when absent. Deliberately inherited and documented as such:
`AELIX_CODING_AGENT_DIR`, `AELIX_SETTINGS_PATH`, `AELIX_AUTH_PATH`,
`XDG_CONFIG_HOME`, `PI_OFFLINE`, and every provider API key.

cwd is the tool's `cwd` argument when supplied **and**
`Path.resolve().is_relative_to(parent_cwd)`; an out-of-tree `cwd` is a tool
error, not a silent fallback.

stdio: **`stdin=DEVNULL` is mandatory** — an inherited stdin costs **+30 s per
delegation** (`_read_piped_stdin`, `entry.py:144-208`, whose default timeout is
`30.0` at `:172-174`) and any bytes that arrive
are *prepended to the task message*. `stdout=PIPE, stderr=PIPE,
limit=8 MiB`. `start_new_session=True`, because the default would put the child
in the parent's process group and a single Ctrl+C would SIGINT every subagent at
once with no envelope (neither `tui/shell.py:1323-1339` nor
`modes/print_mode.py:114-131` installs a SIGINT handler).

**`PR_SET_PDEATHSIG(SIGTERM)` in `preexec_fn`** (Linux, `except Exception: pass`),
because a parent hard-death otherwise leaves a **fully-running orphan**. Verified:
`print_mode.py`'s `_emit` (`:157-165`) only records `stdout_dead["v"]`; the acting
`break` is at `:199-204` inside the **residual**-messages loop and the
`raise BrokenPipeError` at `:209-212` — both strictly **after**
`await runtime_host.harness.prompt(initial_message)` at `:191-195`. Since
`agents/resolver.py:204-205` makes the whole task the *initial* prompt, the EPIPE
guard never covers it: a child whose parent is SIGKILLed runs every turn, every
LLM call and every tool to completion, reparented to init, in its own session,
with its 0700 tmpdir leaked. PDEATHSIG composes with `start_new_session=True`
(setsid does not clear it; neither does the exec — only a UID change does).

**The 0600 prompt file** (ADR-0196's Phase-1 deferral, delivered here):
`tempfile.mkdtemp(prefix=f"aelix-subagent-{os.getpid()}-")` at 0700, then
`os.open(path, O_CREAT|O_WRONLY|O_EXCL, 0o600)`, name
`prompt-<sanitized profile name>[:64].md`, content `profile.body` (already
frontmatter-free). It exists so a profile body reaches a child process without
`ARG_MAX` overflow and without leaking the whole system prompt in `ps`. It is
unlinked in **one** `try/finally` inside the shielded scope of §(j), and the path
is also recorded on the child-registry record so `stop`, `stop_all` and the
`api.add_cleanup` teardown unlink it even if the owning task was cancelled.

**A FOURTH OWNER, because those three all live inside the parent PROCESS**
*(P2 review, MEDIUM #5; closes the plan's §6.4 `parent-killed` case, MEDIUM #14).*
An earlier draft of this section claimed the file was "unlinked on EVERY exit
path". That is true for cancellation and false for a hard parent death — SIGKILL,
OOM, a crash — which this very section already treats as a real scenario (it is
the whole rationale for PDEATHSIG). Measured 4/4 on parent-SIGKILL probes driving
the real `PrintChannel`:

```
leaked_tmpdirs=['/tmp/aelix-subagent-c75oxoxz']
modes=['0o600'] contents=['SECRET-SYSTEM-PROMPT-BODY']
```

— the mode and the body exactly as designed, and the file simply never going
away: one per delegation, for the life of the box, since `/tmp` cleaners are
configuration-dependent. So the directory NAME now carries the creating pid and
`aelix_agents.prompt_file.sweep_stale_prompt_dirs()` reclaims every
`aelix-subagent-<pid>-*` whose pid is gone. `AgentsExtension.__call__` runs it
once per harness build, after the bind succeeds — the one place we know this
process is a delegation PARENT, so a child can never sweep the directory its own
parent is using.

Three refusals, all erring toward leaving a directory alone, because a wrong
deletion yanks a live delegation's system prompt out from under a child that has
not finished reading it: the name must parse as `aelix-subagent-<digits>-…` (an
un-stamped directory from an older build never does); the pid must be dead (a
recycled pid reads as live and the directory waits for a later run); and the
directory must be ours by `lstat` uid and not our own pid.

**Per-spawn directories are KEPT.** The review's "reuse ONE per-process
directory" would make concurrent delegations share a removable resource, so one
finishing run's cleanup would delete a live run's prompt file. Stamping the pid
into a still-unique name is all the sweep needs.

### (i) Spawn-time consent with bounded widening

**The problem is not UX — it is an empty gate.** Measured: `_MUTATING`
(`permission.py:64`) is
`['bash','create_file','edit','execute_command','sh','shell','write','write_file']`;
`"agent"` is not in it, so an `agent` call is silently allowed at
`permission.py:324-325`.

**Adding `"agent"` to `_MUTATING` is NOT the fix, and must not be attempted.**
`_rule_key` (`:94-116`) falls through to an args-blind `f"tool:{tool_name}"` at
`:116` — measured, `_rule_key("agent", {"profile":"a"})` and
`_rule_key("agent", {"profile":"b"})` are both `'tool:agent'`. One "Yes, for this
session" would then approve **every profile against every task** for the rest of
the run. The gate must live in our own extension, keyed on what actually varies.
A code comment at `permission.py:64` and a test pin this so it is not
rediscovered as a "fix".

**The location is the `tool_call` hook, not `execute()`.** Three reasons, each
measured:

1. **No serialization in `execute()`.** The kernel runs Phase 1 — sequential prep,
   where `before_tool_call` fires (`loop.py:510-531`, driven from the prep loop at
   `loop.py:809-823`) — **sequentially**, and Phase 2 under `asyncio.gather`
   (`loop.py:877`) **in parallel**, with `harness/core.py:247` defaulting
   `tool_execution = "parallel"`. Two modals from two `execute()`s in one batch
   collide on `tui/chrome.py:518`'s single `_modal` slot: `mount_modal` (`:1511`)
   overwrites unconditionally, the first Future is orphaned, and the turn hangs
   until Ctrl+C.
2. **No `ctx.has_ui`.** `ToolExecutionContext` (`packages/aelix-ai/src/aelix_ai/tools.py:52-84`)
   has exactly four fields — `tool_call_id`, `signal`, `on_partial`, `model` — and
   no UI. `has_ui` is on `ExtensionContext` (`api.py:957`), which hooks get and
   `execute()` does not.
3. **No first-class refusal.** A hook returning `ToolCallResult(block=True,
   reason=…)` is handled by `harness/hooks.py:1419-1439` `_reducer_tool_call` —
   sequential, first `block=True` short-circuits — and the kernel renders it as a
   model-readable `_Immediate` error result (`loop.py:518-531`). An `execute()`
   refusal is just another error string.

In the hook, **the kernel serializes for us**: every permission prompt in a batch
completes before any `execute()` starts. Belt-and-braces for two `agent` calls in
one assistant message: the tool declares `execution_mode="sequential"` (kernel
`types.py:47-56`; `loop.py:683-693` downgrades the **whole batch** when any tool in
it says so — the `tools/bash.py:574` pattern), **and** `consent.py` holds a single
`asyncio.Lock` around the prompt (the precedent is `permission.py:387`).
`execution_mode="sequential"` is therefore a **security** setting here, not a
performance one, and a test pins that `dataclasses.replace` (used to re-inject the
roster) preserves it.

**The grant hand-off.** `ToolCallHookEvent.tool_call_id` and
`ToolExecutionContext.tool_call_id` are the same id. The hook returns `None`
immediately unless `event.tool_name == "agent"`; otherwise it resolves the
profile, computes the clamp, runs the consent gate, and stores a frozen
`SpawnGrant` in `self._grants[event.tool_call_id]`. On refusal it returns
`ToolCallResult(block=True, reason=…)` and stores **nothing**. `execute()` does
`grant = self._grants.pop(ctx.tool_call_id, None)`; **`None` ⇒
`ToolResult(is_error=True)` and no process is created.** That is the anti-bypass
invariant: a caller that skipped the hook cannot spawn. Grants are bounded (32
entries, oldest dropped) and cleared on `turn_end` and `session_shutdown` — a
grant never survives the turn that created it.

**Two doors, one gate.** `spawn(...)` — the Protocol method used by `/agents run`
— takes consent itself before any process exists. `spawn_granted(grant, ...)` is
implementation-private, not on the Protocol, and is what the tool calls with the
popped grant. **The grant type is deliberately NOT on the Protocol**, so no
consent parameter leaks into product-core; a test pins that
`inspect.signature(SubagentRuntime.spawn)` carries none.

**The dialog.** `ExtensionUIDialogOptions` (`extensions/ext_ui.py:56-67`) carries
only `signal` + `timeout`, and the extension-facing `select`
(`ext_ui.py:186-193`) takes `(title, options, opts)` — **there is no body/detail
field on the extension-facing protocol**, so all context rides the `title` string
as a multi-line block: profile name · **`resolved.source_path`** · scope · the
child's cwd · the task truncated to 300 characters. Options, in order:
`"Run read-only (<clamped>)"`; `"Allow file edits for this run
(auto-accept-edits)"` **only when widening is permitted**; `"Cancel"`. `select`
returns `None` on Esc (`tui/context.py:255-262`) and `"Cancel"` is explicit — both
give `consented=False`. **Fail-closed by construction.**

**AND BY ALLOW-LIST, not by deny-list** *(P2 review, MEDIUM #1).* Only an answer
that IS one of the strings `build_options` just rendered may consent. The
shipped form tested `answer is None or answer == CANCEL_OPTION` and let every
other `str` fall through to the grant at the bottom of the function — so an
answer matching **no offered option was CONSENT at the clamp**. Measured through
the real hook→execute wiring with `"<<< user pressed something weird >>>"`:

```
parent=yolo               -> is_error=False  SPAWNED=1  child_mode=yolo
parent=auto               -> SPAWNED=1  child_mode=auto-accept-edits
parent=auto-accept-edits  -> SPAWNED=1  child_mode=auto-accept-edits
```

i.e. an unattended, bash-capable child authorised by a string nobody was shown.
Not reachable through the shipped TUI (`tui/context.py::select` returns
`items[idx][1]` or `None`), but `ctx.ui` is a public Protocol seam that `bind_ui`
lets any host or extension supply and the planned Web UI is exactly such a host —
and this module's stated contract is that ANY unexpected input is a decline,
which is already how `_ask` treats exceptions and non-`str` answers. Two tests
had enshrined the opposite (`test_a_declined_dialog_blocks_and_starts_nothing`
asserted that a nonsense answer SPAWNS, under a docstring saying it must not);
both now assert the decline.

**The approved identity is re-checked at `execute()`** *(P2 review, MEDIUM #10).*
The shipped guard compared `pending.grant.source_path` against
`pending.resolved.source_path`, which are equal by construction on every path —
`PendingSpawn` is built at one site from the same `resolved` the grant was taken
against — so the branch was dead while reading like an anti-substitution defence.
It now re-resolves the approved NAME (always `allow_project=False`) and compares
the source path, with any failure a refusal. The window is real: the hook runs in
the kernel's sequential prep phase and `execute()` under `asyncio.gather`, and the
profile search path is a directory the model's own tools can write — a
project-scoped profile appearing in between WINS the name collision
(`agents/service.py:99-100`), which is §(f)'s threat arriving one step later.

With **no live UI** the gate returns the clamp with `widened=False,
consented=True` and never prompts, so `-p` / `--mode json` / RPC delegation keeps
working exactly as §(e) describes and **widening never happens headlessly**.

#### Amendment (2026-07-27, owner) — the dialog fires only when write authority is at stake

**The rule as shipped.** With a live UI, `request_spawn_consent` opens the dialog
**iff**

```
consent_is_required(resolved, clamped, has_ui=True)
  ==  grants_write_authority(clamped)  OR  _may_widen(resolved, clamped, has_ui=True)
```

A delegation that is **read-only and cannot be widened proceeds with no prompt**,
returning the same grant the baseline option would have produced
(`mode == clamped`, `widened=False`, `consented=True`). The superseded behaviour
prompted on *every* delegation with a live UI, including one where
`build_options` could only render `["Run read-only (plan)", "Cancel"]`.

##### Second amendment, same day — widening is offered only to a profile that DECLARED it

The first form of this amendment left `_may_widen` returning True for *any*
user- or `explicit`-scoped profile with a live UI whose clamp was below
`auto-accept-edits`. Measured consequence: only 9 of the 40 live-UI cells stopped
prompting — all of them project-scoped — and an ordinary **read-only user-scope
delegation still rendered a modal**, purely because the human could theoretically
have widened it there. That is the click-through trainer the amendment exists to
remove, wearing a different hat.

**The rule.** The widening option is offered **only when the profile itself
declares it needs write authority.** `_may_widen` gains constraint **0**:

```python
declares_write_authority(resolved.profile.approval_mode)   # "auto" | "ask"
```

It is numbered 0 because it is of a different kind from the five below: those
**bound** how far a widening may go, this one decides whether the option exists
at all. The predicate lives in `aelix_agents/posture.py` beside
`grants_write_authority`, next to the table that justifies **every** value:

| `approval_mode` | declares? | why |
|---|---|---|
| `auto` | **yes** | The profile literally asks for its writes to be auto-approved. There is no other reading. Under a tight parent finding B4's clamp refuses to grant that silently — which is exactly the situation a dialog is for. |
| `ask` | **yes** | The author asked for a human decision *at spawn time*, and the only decision this dialog offers is how much authority to grant. It must stay here or the value becomes **inert**: its clamp under a `default` parent is `plan`, so without the declaration the early-out above would suppress the very dialog `ask` exists to open, making it indistinguishable from `inherit` and re-opening the "validated, not read" deferral (`agents/profile.py:218-220`) that OC-8 closed. |
| `inherit` | no | Asks for no **more** authority than the parent already has. When the parent *is* loose the clamp itself is write-capable and the dialog fires through `grants_write_authority` — never through the widening option — so refusing to volunteer one loses nothing. This is the owner's headline case. |
| `deny` | **never** | The explicit opposite. Its clamp is `plan` under every parent and in every scope, and a dialog offering to widen it would contradict the file it was read from. |

It is an **allow-list**, so an unrecognised value (a future one, a caller that
skipped `parse_profile`'s validation, a case-mangled string) declares nothing and
cannot widen.

**Consequences the owner accepted, stated plainly:**

- a profile that does not declare — a scout with no `approval_mode` line, or an
  explicit `inherit` — whose clamp grants no write authority produces **no dialog
  at all**, and the spawn proceeds read-only;
- a profile that **does** declare produces the dialog, offering the bounded
  widening exactly as before;
- **a human can no longer opportunistically upgrade a non-declaring profile at
  spawn time.** That is the intended trade: authority follows *declared* intent,
  and the way to grant a profile writes is to edit the profile — a reviewable,
  and under ADR-0189 signable, artifact — rather than a decision taken under a
  modal in the middle of somebody else's turn.

**What it does not touch.** The `grants_write_authority(clamped)` half is
unchanged: a clamp that already grants write authority — finding OC-1's shift+tab
case — never reached `_may_widen` in the first place and still prompts, at every
scope, on every delegation. So do the ceiling (`auto-accept-edits`, never
`auto`/`yolo`), the project-scope ban, the headless silent downgrade, and
fail-closed declining.

**Measured, over all 60 live-UI `(parent × scope × approval_mode)` cells:**

| | cells that prompt |
|---|---|
| before either amendment | 60 |
| after the first (write authority at stake) | 49 |
| **after this one** | **35** |

The 14 that changed here are every `inherit` / `deny` profile at `user` or
`explicit` scope whose clamp grants no write authority: `inherit` under a `plan`
or `default` parent, and `deny` under all five. Nothing that declares stopped
prompting, and nothing at project scope moved (it had already stopped).

**The owner's reasoning, which the implementation honours.** A modal that appears
when there is no real choice trains click-through, and the habit it trains then
endangers the prompts that *do* matter — the write-capable spawn and the
widenable one, which are rendered by this same widget. A dialog whose only
substantive answer is "yes" is not a gate; it is practice at dismissing gates.

**`grants_write_authority` is derived, not asserted, and it is not `is PLAN`.**
It lives in `aelix_agents/posture.py` and is a threshold on the same `_RANK`
ladder the clamp uses (`>= AUTO_ACCEPT`), walked off `builtin/permission.py`'s
own branch order for a **delegated child** — `ctx.has_ui = False`,
`headless_default = "block"`, the two settings `cli/entry.py` gives a process at
`subagent_depth() > 0`:

| clamp | branch reached | mutating tool, nobody asked | authority |
|---|---|---|---|
| `plan` | (b), above the read-only short-circuit and above the headless branch | BLOCKED | no |
| `default` | (d), the headless floor — DEFAULT means *ask*, and asking is what is blocked | BLOCKED | no |
| `auto-accept-edits` | (f) `return None`, ~30 lines **above** (d) — finding B4 | ALLOWED | **yes** |
| `auto` | (g) writes as (f), plus a classifier-ALLOW bash | ALLOWED | **yes** |
| `yolo` | (e) `return None` for every mutating tool | ALLOWED | **yes** |

`default` therefore belongs on the no-authority side on its own merits, not
because the clamp happens to tighten a clamped `DEFAULT` to `PLAN` today — those
are two independent decisions, and only the ladder answer survives either being
relaxed. The table is executable:
`tests/agents_ext/test_posture_clamp.py::test_write_authority_matches_the_real_permission_ladder`
drives the **real** `PermissionExtension` for all five postures and asserts the
predicate and the ladder agree in both directions.

**Order is load-bearing: the early-out consults `_may_widen`, not the clamp
alone.** A **declaring** profile under a `default` parent also clamps to `plan`,
but it *can* be lifted to `auto-accept-edits` by one human answer — so it still
prompts. Skipping it would leave shift+tab as the only way to let a child write,
which raises the **whole session** and hands that posture to every subsequent
delegation; that is the trade §(i) already rejected under "Why read-only-only was
rejected", arriving through the back door. Since the second amendment this is
what `approval_mode: auto` under a tight parent *means*, and it is the one case
where a human answer is the only thing that can satisfy a profile's stated need.

**The project-scope gate is NOT holed, and that had to be proved before the
dialog could go.** The consent dialog was a second place a human saw *which*
identity was about to run. Both doors that can reach a project-scoped profile
still meet a human first, and neither of them is this dialog:

- **the model-driven `agent` tool** — unreachable. `allow_project=False` is
  hardcoded at *both* of its sites (`extension.py`'s `tool_call` hook, and the
  `execute()` re-resolve), so a project identity is refused outright (§(f)).
- **`/agents run`** — `tui/commands.py` resolves with the `allow_project=False`
  default, catches the typed `ProjectScopeRefused`, runs
  `_confirm_project_agent_for_run` (ADR-0196's per-identity copy, naming the
  file path), and only on the affirmative — matched by identity against
  `PROJECT_AGENT_CONFIRM_OPTIONS[0]` — re-resolves with `allow_project=True`.
  That call site is the **only** `allow_project=True` in the source tree.

Pinned by `test_a_project_identity_is_unreachable_without_the_affirmative`
(every non-affirmative answer leaves the identity unresolved and starts nothing)
and by `test_agent_tool_refuses_project_scoped_profile_without_confirmation`.

**The MODEL door pre-filters ahead of this, and now with LITERALLY the same
predicate.** `AgentsExtension._grant_for` calls `consent_is_required` — the
exported function `request_spawn_consent`'s own early-out calls — and skips
`request_spawn_consent` when it is False. It has been respelled three times:
`want is PermissionMode.PLAN` → `not grants_write_authority(want) and
approval_mode != "ask"` → the shared predicate. The middle form was the problem:
it disagreed with the dialog's own rule on two cells, so a read-only *user*-scoped
delegation prompted through `/agents run` and not through the model door, and a
declaring `auto` profile under a tight parent was swallowed by the model door
entirely — clamped to `plan` by finding B4's rule and never mentioned to the
human. Two hand-maintained spellings of a consent rule is how one door drifts
open; there is now one function and nothing to keep in agreement.
`MAX_DELEGATIONS_PER_PROMPT` is what bounds the branch that stays silent.

**Honest residual — R7 below.** A read-only delegation now happens with no
per-spawn human confirmation at all. **The "no session memo" decision above is
unchanged**: there is still no persistence and no memo, and every spawn that
*does* prompt prompts again.

**Widening is ALLOWED but BOUNDED — constraint 0 plus five, all required:**

0. `declares_write_authority(resolved.profile.approval_mode)` — the profile said
   it needs write authority (`auto` or `ask`). Added by the second amendment
   above, which carries the per-value justification. Constraints 1–5 bound how
   far a widening may go; this one decides whether the option exists.
1. `resolved.scope != "project"` — a project-scoped profile can **never** be
   widened, dialog or no dialog. This preserves the §(e) vulnerability's core
   verbatim: its subject was *a repo file widening silently*, and a human
   answering a modal is not that.
2. `ctx.has_ui` is `True` **at the moment of the prompt** (live read — the
   value is time-varying, see §(e)).
3. The ceiling is **exactly `auto-accept-edits`**. `auto` and `yolo` are never
   offered by any dialog, for any parent posture — so **bash in the child is
   still gated** by the child's own ladder.
4. `_RANK[AUTO_ACCEPT] > _RANK[clamped]`, or the option is a no-op and is not
   rendered.
5. The `.aelix` mitigation below has landed. A structural prerequisite, not a
   runtime check.

**No persistence, and no session memo.** A grant is per-spawn: P2 asks every
time. Nothing is written to disk, and nothing is memoized for the session — a
second delegation to the same profile prompts again, pinned by a test. A
per-session memo is **P3**, and when it lands it must key on
`(profile name, source_path, granted mode)` — **never** on a `tool:agent` rule
key, for the `_rule_key` reason above.

**Why read-only-only was rejected.** It is the strictly *worse* option: with no
way to widen one spawn, the user presses shift+tab and raises the **whole
session** to `auto-accept-edits`, after which §(e) hands that posture to *every*
subsequent child with no prompt at all. A per-spawn, per-child grant from a
DEFAULT parent is narrower than a session-wide posture bump.

**`approval_mode: ask` no longer refuses the spawn.** The old rule existed only
because there was nowhere to ask. There is now: with a live UI `ask` opens the
dialog; without one it clamps to `plan`.

**Prerequisite: `.aelix` joins `_SENSITIVE_DIR_COMPONENTS`
(`permission.py:219`).** Measured before the change, against
`_is_auto_allowable_write` (`permission.py:222-252`):

```
.aelix/agents/evil.md      -> True        .env                   -> False
.aelix/extensions/evil.py  -> True        ../outside.txt         -> False
.aelix/mcp.json            -> True        ~/.ssh/authorized_keys -> False
.aelix/settings.json       -> True        src/app.py             -> True
_SENSITIVE_DIR_COMPONENTS = {'.gnupg', '.ssh', 'cron.d', 'cron.daily'}
```

Guardrail has no `.aelix/` rule either (it covers `.env*`, `.git/`,
`node_modules/`, `__pycache__/`). Those four paths are **precisely** the resources
`has_trust_requiring_project_resources` gates, so an auto-accepting child could
**write the file that a later parent run executes** under an ancestor
`trust.json: true` (`project_trust.py:550-557`, transitivity at `:60-61`) — a
write-to-exec escalation that `--no-approve` cannot touch, because `--no-approve`
only stops *loading* such a file, never *writing* one. Delegation makes it
reachable by a process nobody is watching, which is what turns it from a
nice-to-have into a P2 blocker.

**This is a user-visible behaviour change, and it is wider than the four paths
above.** `_SENSITIVE_DIR_COMPONENTS` is a *path-component* rule, so **every**
write under any `.aelix/` directory — including `.aelix/skills/**` and anything a
user keeps there — now falls through to the 4-option prompt under AUTO_ACCEPT and
AUTO instead of being silently accepted. Interactive users who edit their own
agent profiles under AUTO_ACCEPT will see a prompt they did not see before. That
is the intended trade, and it carries a CHANGELOG line. The blast radius is
bounded by a test asserting that ordinary in-cwd writes (`src/app.py`) are still
auto-allowable.

### (j) The reaper, and why `killpg` is the wrong instrument

`reap(proc, *, grace=5.0, eager_kill=False)` is SIGTERM → grace → SIGKILL, and it
must be **safe to cancel repeatedly**.

`tui/chrome.py:855`'s `_interrupt` calls `on_interrupt` on **every** Ctrl+C while
a turn is running — the running branch is `:863-866` and it has **no debounce**
(`_CTRL_C_EXIT_WINDOW`, declared at `:159`, is read only at `:875`, inside the
**idle empty-buffer** branch `:872-882`). `chrome.py:884-890` binds Esc to the
same callback under `Condition(lambda: self._running)`. **N presses = N
`cancel()` calls on the same turn task.** A plain coroutine reaper takes
`CancelledError` at its `wait_for` and never reaches the SIGKILL leg, leaving a
**session leader** (`start_new_session=True`) that no terminal signal and no
parent-side timeout can ever reach again.

Two defenses, both required: `except BaseException` around the grace wait, so
`CancelledError` escalates exactly like a timeout; and the caller runs the reaper
as a **detached task** awaited under `asyncio.shield`, so cancelling the awaiter
never cancels the kill. A second cancellation sets `eager_kill` and SIGKILLs
immediately rather than waiting out the grace. The final `await` on the process is
mandatory or a zombie leaks.

**The `killpg` rationale in the draft was factually wrong and is corrected.**
"Use `os.killpg`, not `proc.kill()`, so the child's own `bash` grandchildren die
too" does not hold: `tools/bash.py:157-164` uses
`subprocess.Popen(..., start_new_session=True)` and `tools/_subprocess.py:74-79`
uses `create_subprocess_exec(..., start_new_session=True)`, so grandchildren are
leaders of their **own** groups and `killpg` on the child's group cannot reach
them. The kill leg walks `/proc/*/stat` PPid instead, deepest-first, from a
snapshot taken **before** `terminate()` — a pid-recycling hazard, since the
`ThreadedChildWatcher` may already have waitpid'd the child and a re-resolved
`getpgid(proc.pid)` could name a recycled process. On the cooperative SIGTERM leg
the walk is redundant (the child's own `_signal_cleanup_and_exit` →
`dispose()` → `abort()` → `bash.py:210-215` `_kill_group` reaps them); the
escalation leg exists precisely for the child that does not cooperate.

**Do not port pi's escalation literally.** Node's `subprocess.killed` means "a
signal was sent", so pi's `if (!proc.killed) proc.kill("SIGKILL")` is false 5 s
later in every case and SIGKILL is never actually sent. The Python predicate must
be **liveness**.

On the normal path, stdout is drained to EOF **before** reaping — closing the read
end early gives the child a `BrokenPipeError` → exit 141, a parent-side bug
masquerading as a child failure. On the kill paths, any exit code is accepted.

**NEVER `proc.terminate()` / `proc.kill()`** *(P2 review, MEDIUM #4).*
`asyncio.subprocess.Process.terminate` reaches `subprocess.Popen.send_signal`,
whose first statement is `self.poll()` — a `waitpid(pid, WNOHANG)`. If the child
has already exited but the loop's watcher has not yet delivered the status, that
`poll()` reaps the zombie out from under the loop, the watcher's own `waitpid`
raises `ChildProcessError`, and asyncio substitutes **255**. Measured over 60
reaps of a child that really exited 0, with the loop briefly blocked so the
watcher could not win the race: `{255: 60}`, one *"child process pid … exit
status already read: will report returncode 255"* warning apiece. Since
`build_result` computes `failed = … or exit_code != 0` (§(k)), on any leg whose
outcome is not already a failure — `stop()`/`stop_all()` racing a child that just
finished, which `abort_child` always drives with `eager_kill=True` — that flipped
a **successful** delegation to `ok=False, status="error", exit_code=255`.
`reaper._signal_child` re-reads `proc.returncode` and then uses
`os.kill(proc.pid, sig)`, which signals a zombie harmlessly and leaves the status
for the watcher. Pid recycling is not reintroduced: an unreaped child's pid
cannot be recycled, and there is no `await` between the liveness read and the
signal.

**PDEATHSIG STAYS `SIGTERM`, and that is a measured choice, not an omission**
*(P2 review, MEDIUM #12 / MEDIUM #6).* Two residuals are real and are recorded
here rather than papered over:

* **A SIGTERM-ignoring or SIGTERM-blocking child outlives the parent's hard
  death forever.** `PR_SET_PDEATHSIG` delivers exactly one signal and the
  SIGTERM→grace→SIGKILL ladder lives in `reap`, which dies with the parent.
  Measured: a child blocking SIGTERM via `pthread_sigmask` was still `S
  (sleeping)` three seconds after the parent was SIGKILLed. The real aelix child
  does not block SIGTERM — it installs a `loop.add_signal_handler` callback — but
  a wedged loop delays it and nothing escalates.
* **A session-leader grandchild survives even a POLITE child.** `tools/bash.py`
  and `tools/_subprocess.py` both pass `start_new_session=True`, so a grandchild
  leads its own group and nothing outside the child can find it once the child is
  gone.

The review proposed `PR_SET_PDEATHSIG(SIGKILL)` for the first. **Rejected, with
the measurement**: SIGKILL denies the child the cleanup that is the only thing
reaching its `bash` grandchildren. Parent SIGKILLed, child forks a session-leader
grandchild and kills it on SIGTERM:

```
pdeathsig=SIGTERM   child_alive=False  grandchild_alive=False
pdeathsig=SIGKILL   child_alive=False  grandchild_alive=True
```

i.e. SIGKILL trades a rare severe residual for a universal one — every parent
death orphaning every bash grandchild. SIGTERM stays.
`test_pdeathsig_is_sigterm_and_that_is_a_measured_choice` pins the choice so it
cannot be flipped by accident.

**REJECTED → P3:** cgroup / `pidfd_open` subtree containment, and a per-session
stray-pid file with a cross-run sweeper. That is where both residuals above are
actually fixed. The `/proc` walk is racy in theory and sufficient in practice for
a single-user local product; real containment belongs with P3's `RpcChannel`
lifecycle work.

**Pipe EOF is not the exit status** *(P2 review, MEDIUM #3).* Once both pumps
reach EOF every writer has closed fd 1 and fd 2, so the child is exiting — but
`proc.returncode` only appears when the loop's child watcher delivers it,
milliseconds to tens of milliseconds later. Charging that gap to the caller's
deadline reported COMPLETE runs as failures: measured on a child answering at
~679 ms, `timeout_ms=686` gave `status=timeout, ok=False` with `summary='the
complete answer'`, in 4/30 runs of a sweep across the band. The post-EOF exit
wait therefore takes `max(remaining budget, POST_EOF_EXIT_GRACE_SECONDS = 2.0)`.
It is a **floor, not an amnesty**: a child that closes its stdio and then wedges
still times out, two seconds later.

### (k) The envelope — always returned, never raised

`SubagentResult` is returned on every path, including spawn failure, timeout,
abort and decline. Failure detection never trusts the return code alone:

```
failed = exit_code != 0 or stop_reason in ("error", "aborted") or outcome != "ok"
```

`print_mode.py`'s `stop_reason in ("error","aborted") → exit_code = 1` is guarded
by `if mode == "text"`, so a bogus-model JSON run **exits 0 with empty stderr**
while the stream carries `stop_reason: "error"`.

The summary fallback chain is `error_message` → sanitized stderr tail →
extracted summary → `"(no output)"`. The **stderr rung is mandatory**: a child
with no API key exits 1 with **zero stdout bytes**, not even the session header.
It is sanitized before the model sees it — the SIGTERM path used to print
`Task exception was never retrieved` / `future: <Task finished … exception=SystemExit(143)>`,
which was **normal** rather than an error. The raw text is kept
on `SubagentResult.details`.

*Amended 2026-09-05 ([#220](https://github.com/handochan/aelix-ai/issues/220)):
`_signal_cleanup_and_exit` now records `128 + sig` for `run_print_mode` to
return rather than calling `sys.exit` inside a coroutine, so today's child emits
none of that noise (measured). The sanitizer stays as a defensive filter — any
other unretrieved task exception in the child still produces the same shape.*

`details` exists because `summary` is capped at `profile.output_cap` (default
51 200) on **UTF-8 bytes** and its truncation marker promises "full output
preserved in tool details" — a promise that was false on the `/agents run` door,
which never builds a `ToolResult`, and invisible to any future dashboard or Web
UI, both of which consume `SubagentResult` rather than `ToolResult`. Capping in
**single** mode is a deliberate divergence: pi's `truncateParallelOutput` has one
call site and single mode returns uncapped.

`timeout_ms` precedence is tool argument → `profile.timeout_ms` → **600 000**. pi
has no timeout of any kind, so this is aelix-original. On expiry the reaper runs
and the envelope is `status="timeout", ok=False` **carrying the partial summary
and partial usage** — never an exception. **The consent dialog is outside the
timeout budget**: the clock starts at `create_subprocess_exec`, not at the tool
call, so a human thinking for two minutes does not consume the child's ten.

`permission_mode` records the posture the child actually ran under, so
`/agents run`, a later dashboard and any audit story can all show what authority
was granted — including when a human widened it. It is an additive, defaulted
field and does **not** bump `CONTRACT_VERSION` (§(b)).

Abort returns rather than throws: pi throws and discards every streamed partial;
aelix returns `SubagentResult(status="aborted", ok=False, summary=<partial>,
usage=<partial>, details=<raw>)`.

### (l) Progress, the statusline, and the `agent` tool's roster

Progress rides `api.events` at `subagent_start` / `subagent_tool` /
`subagent_end`. **EventBus caveats, re-verified at `extensions/api.py:272-278`:**
handlers run **synchronously**, the return value is discarded (so an `async def`
subscriber's body never runs), and every handler exception is swallowed by
`contextlib.suppress(Exception)` with **no logging** — a broken subscriber
produces zero rows and zero diagnostics. A test documents this rather than
pretending it is a bug.

The statusline uses `runtime.ui.set_status(f"subagent:{id}", text)`, read **live
per call** (rebound at `shell.py:1565`, reverted at `:1950`) and guarded with
`runtime.ui is not HEADLESS_UI_CONTEXT`, because in print/json/rpc `bind_ui` is
never called and every `ctx.ui.*` raises `NotImplementedError`. `set_status`
(`chrome.py:1288-1293`) feeds `_render_status` (`chrome.py:1036-1047`), which is
**one height-1 row** with `\n` stripped — one segment per child; multi-row panels
are `set_widget` and are P4. The row is cleared with `set_status(key, None)` in a
`finally`. `ctx.on_partial` is driven from the same reduce step so the parent's
own tool card streams.

The `agent` tool's **description injects the profile roster** (name + description
+ scope, each description truncated to 160 chars, total ≤ 4 KiB, at most 24
entries). pi does not do this — `agents.ts` still exports `formatAgentList` but
`index.ts` never imports it — so pi's parent model discovers profile names by
guessing. Because `Tool` is `@dataclass(frozen=True)` and the description is
fixed at `register_tool` time, the roster is refreshed from a
**`before_agent_start`** handler (`harness/core.py:1242`) via
`api.register_tool(dataclasses.replace(agent_tool, description=roster))`
(`api.py:1488`); a `turn_start` handler would be too late for the current turn.
Landmine, asserted in a test rather than rediscovered: `register_tool` →
`refresh_tools` → `_refresh_extension_tools` (`harness/core.py:847-906`)
**materializes `active_tool_names` from the `None` sentinel into a concrete
list.**

**The registry is LIVE-ONLY, and four Protocol members ship with no P2 consumer**
*(P2 review, MEDIUM #16).* `_run` deregisters in a `finally` before `spawn`
returns, so `list()` returns only in-flight rows and `status(id)` raises
`KeyError` for anything finished — which makes the terminal `SubagentState`
values `"done"` / `"error"` / `"stopped"` unreachable through either. Terminal
state is observable on the `SUBAGENT_END` channel **and nowhere else**: a P4
dashboard must subscribe BEFORE a spawn starts, never poll after it. Both
methods now say so in the contract.

`list()`, `status()` and `stop()` have **no product-core caller** in P2, and that
is deliberate rather than an oversight: `/agents run` awaits `spawn` inside the
command handler and the `agent` tool is `execution_mode="sequential"`, so the
REPL is blocked for exactly as long as a child is live and there is no moment at
which a user could type `/agents list` or `/agents stop`. Adding those
subcommands in P2 would ship two commands that cannot be reached. They exist so
the vocabulary is stable for the P3/P4 surfaces that CAN reach them — a
background/parallel mode is what first makes them callable. `stop_all()` is the
one member with a live consumer: the extension teardown.

### (m) The settings gate

`[features] agents`, **default `False`** in P2. Read through a new
**global-scope-only** getter copying the shape of `get_default_project_trust`
(`settings/settings_manager.py:1008`, whose docstring states the reason verbatim):
project settings are loaded **ungated**, so a merged read would let any cloned
repo switch delegation on by shipping `.aelix/settings.json`. Run-scoped
`--agents` / `--no-agents`; precedence `--no-agents` > `--agents` >
`get_features_agents()`.

All five settings registration tables must be edited together, because an unknown
key is **silently dropped** (`settings_manager.py:105-109`). The `/settings` row is
`live=False` — the change takes effect on the next harness build, and adding a
live-mirror branch would create exactly the inert half-wired row class that #84
catalogued.

## Deferred deliberately (recorded so they are not read as gaps)

* **The child→parent approval back-channel (P3).** The child still has **no**
  approval channel and never prompts; every consent decision is taken in the
  parent before a process exists. This is blocked by four *measured* facts, not by
  plumbing: (a) the child's stdin cannot carry it — `cli/entry.py:1266-1267` →
  `_read_piped_stdin` → `sys.stdin.read()` (`entry.py:208`) reads to **EOF**, so an
  open pipe hangs the child forever and a closed one prepends the reply to the
  task prompt; (b) the RPC UI multiplex is **types only**
  (`rpc/rpc_types.py:639-642`), the server drops responses
  (`rpc/rpc_mode.py:2045-2049`), and RPC children never call `bind_ui`; (c) the
  parent TUI has **no modal arbiter** — `chrome.py:518`'s `_modal` is a single
  slot, `mount_modal` (`:1500`/`:1511`) overwrites unconditionally,
  `unmount_modal` (`:1514`/`:1517`) nulls unconditionally, and `is_modal_open`
  (`:1520`) is used only as a visibility filter (`:630-632`), never as a
  re-entrancy guard; (d) a back-channel would invalidate this ADR's entire §(e)
  derivation. Estimated +500 LOC and two new product-core subsystems — its own
  sprint.
* **A persisted or per-session consent memo (P3)**, keyed on
  `(profile, source_path, mode)` and never on a rule key.
* **A bash environment scrubber (P3)** for the `AELIX_SUBAGENT_DEPTH` reset
  described in §(c).
* **Parallel and chain modes, teams, and a long-lived `RpcChannel` (P3/P4).**
  `spawn(mode=...)` accepts `"single"`; anything else raises
  `ValueError("mode %r is P3")`. `SubagentMode` declares all three so the type
  does not change shape between phases, exactly as ADR-0196 did with `role` /
  `output_cap` / `timeout_ms` / `approval_mode`.
* **A keyed multi-runtime `bind_subagents` registry (P3/P4)** — §(b).
* **cgroup / `pidfd_open` subtree containment, and a stray-pid sweeper (P3)** —
  §(j).
* **The wider child-trust rule** that also recovers `inherit_extensions: true`
  and project MCP, and **honouring `defaultProjectTrust: "always"` in a child**
  (P3) — §(g).
* **Flipping `permission.py:382` allow→deny for ALL headless runs.** That is a
  behaviour change for every existing `-p` user and needs its own ADR; P2 flips it
  only for `subagent_depth() > 0`.
* **The cross-channel parity test** (spec §9) and the `rpc` half of the envelope
  contract — see ADR-0198.

Explicitly **NOT** done, and rejected on sight per ADR-0008's review gate: kernel
event types for subagents; a product-core `SubagentSupervisor`; any spawn
behaviour, cap, registry or consent policy in product-core.

## Consequences

- ADR-0196's four deferrals are all disposed of: the 0600 temp-prompt writer
  ships (§(h)), the parent-grant intersection ships (§(h)), `[features] agents`
  ships (§(m)), and `confirm_project_agents`' *behavioural* half ships as two
  consent gates (§(f) for identity, §(i) for authority) — only the settings key
  itself remains deferred.
- **Out of the box a delegated child is read-only.** A DEFAULT parent produces a
  `plan` child, and that is what a spawn gets when nobody answers a dialog.
- The kernel is byte-unchanged: `grep -rn
  "subagent\|SubagentRuntime\|bind_subagents\|AELIX_SUBAGENT\|aelix_agents"
  packages/aelix-agent-core/src/ | wc -l` → **0**. This is enforced as a content
  gate rather than a git-range diff, so it works in a shallow CI checkout and
  stays meaningful after the branch merges; the git-range check is kept as a
  skip-if-unavailable adjunct, and `.github/workflows/ci.yml` gains
  `fetch-depth: 0` so it can actually run.
- Product-core gains one new module (`subagent_contract.py`), four `api.py` hunks,
  three flags (`--permission-mode`, `--agents`, `--no-agents`), one
  `PermissionExtension` field, one `_SENSITIVE_DIR_COMPONENTS` entry, one settings
  key and one `/agents` subcommand. It gains **zero** lines that spawn a process
  or author a consent decision, both test-enforced.
- Four pre-existing product defects are fixed for every user, not only delegation
  users: `.aelix/**` is no longer auto-approvable under AUTO_ACCEPT/AUTO; the
  JSON event stream's shape is pinned for the first time (ADR-0198); the
  `--mode json` transport's 64 KiB ceiling is documented and worked around; and
  `permission.py:64` now carries the comment that stops the next reader from
  "fixing" delegation consent through `_MUTATING`.

## Known limitations / follow-ups

- **R1 — consent is per-spawn, not per-tool.** Once the human says yes, that
  child writes unattended for its whole run; individual writes are never shown.
  This is exactly what the deferred back-channel would fix, and it is the honest
  price of deferring it.

  **R1's counterpart — a delegation BUDGET, because the common case asks nobody**
  *(P2 review, MEDIUM #2).* `_grant_for` returns `consented=True` with no dialog
  whenever `consent_is_required` is False — the clamp grants no write authority
  **and** the profile declared no need for it (spelled that way since the
  2026-07-27 second amendment; it read `want is PermissionMode.PLAN`, then
  `not grants_write_authority(want) and approval_mode != "ask"`). That is the
  out-of-the-box case for a `default` parent with a default profile. See **R7**
  for the same property stated as a residual now that `request_spawn_consent`
  applies it too. Nothing
  bounded how many of those one turn could start; measured against the shipped
  runtime, `dialogs shown to the human: 0` / `child processes started: 200`, each
  a full `-m aelix_coding_agent` process holding the parent's API keys with up to
  `DEFAULT_TIMEOUT_MS` (10 min) of wall clock, and `"agent"` is deliberately
  absent from `_MUTATING` so the parent's own permission ladder never saw them
  either. A prompt-injected README saying *"call agent() 200 times"* therefore
  cost real money and 200 real processes with no gate and no ceiling. Two
  numbers, both in `aelix_agents/runtime.py`, both returning an error **envelope**
  and never raising:

  | constant | value | scope | reset |
  |---|---|---|---|
  | `MAX_DELEGATIONS_PER_PROMPT` | **12** | the MODEL door (`spawn_granted`) only | `before_agent_start`, i.e. once per USER prompt |
  | `MAX_LIVE_CHILDREN` | **4** | both doors, checked in `_run` | n/a — a live-registry bound |

  Twelve is a ceiling, not a quota: well above any honest fan-out for
  single-mode, foreground, one-at-a-time delegation, and far below the cost of an
  unbounded loop. It is scoped to the model door because `/agents run` is a human
  typing a command, and rate-limiting the human who is already the gate would be
  theatre. Attempts are **not refunded** on failure — the cost being bounded is
  "how many times may one turn ask", and refunding would let a reliably-failing
  task loop forever. Four live children is a resource bound rather than a
  behavioural one: P2's own shape (a `sequential` tool, an awaited command
  handler) means a real session holds one, so the cap only ever fires for a host
  driving concurrent turns.
- **R2 — the task text in the consent dialog is model-authored.** Prompt
  injection can produce a benign-looking task. Mitigations, all mandatory and all
  shipped: always display `resolved.source_path` (a human-owned file path),
  truncate the task to 300 characters, and never widen a project-scoped profile.
- **R3 — modal rendering is not unit-testable.** Our dialog is *taller* than the
  shipped approval dialog (path + 300-char task), so it can hit
  `_CappedContainer` / `_modal_cap` clipping (`tui/overlay.py:136`, `:198`,
  `:222`, `:271`) differently. Mitigated by copying the fixed-height options-window
  pattern from `tui/approval_dialog.py:373-377` (`Dimension.exact(n_option_rows)`
  + `dont_extend_height=True`) and budgeting one manual live-TUI smoke.
- **R4 — the turn cannot be interrupted while the modal is up.** prompt-toolkit's
  `_CombinedRegistry` gives control-level bindings priority, so the modal absorbs
  Ctrl+C / Esc. Identical to today's approval dialog; Esc = decline = the turn
  continues.
- **R5 — the `.aelix` sensitive-component change alters existing behaviour** for
  interactive AUTO_ACCEPT/AUTO users, wider than the four control files that
  motivated it (§(i)).
- **R6 — the bash environment bypass of the depth guard is unchanged** (§(c)).
- **R7 — a read-only delegation happens with no per-spawn human confirmation**
  *(owner decision, 2026-07-27; §(i) amendment and its second amendment).* The
  dialog fires only when write authority is at stake, so a spawn whose clamp
  grants none **and** whose profile declared no need for any starts without
  anyone being asked. After the second amendment that is the ordinary case, not
  an edge one: an `inherit` profile — a scout with no `approval_mode` line at
  all — under a `default` parent is what most delegations look like, and it now
  runs with no modal. That is a confirmation removed, not an audit trail; state
  it as the price, not as a gap. The owner's reasoning for paying it: a modal
  with no real choice trains click-through, and the habit then endangers the
  prompts that matter — the write-capable spawn and the declaring one, which are
  rendered by this same widget. What bounds such a spawn instead:

  | bound | what it actually stops |
  |---|---|
  | the clamp | a `plan` child is refused **every** mutating tool at branch (b) of its own ladder — above the read-only short-circuit and above the headless branch, so it holds on the non-interactive path too. It cannot mutate anything, in any directory, with or without `--no-approve`. |
  | `MAX_DELEGATIONS_PER_PROMPT` (12) | how many such spawns one **user prompt** may start. Reset in `before_agent_start`, so an injected instruction cannot refresh its own budget by taking another turn. |
  | `MAX_LIVE_CHILDREN` (4) | how many may be alive at once, on **both** doors. |
  | the statusline row + `subagent_*` events (§(l)) | the run is **visible while it happens** and afterwards — profile, state, elapsed, tokens, cost — and the result panel always names `permission_mode`. |

  What it does **not** bound: reading. A read-only child can read anything the
  parent could, and R1's per-tool consent — the thing that would show the human
  each action — remains deferred. The identity that runs is still gated: at
  project scope by `_confirm_project_agent_for_run` / the model door's
  `allow_project=False`, and at user scope by the profile living under
  `~/.aelix/agent/agents/`, which is the user's own directory.

  The **no session memo** rule is untouched by this. Nothing is persisted and
  nothing is memoised; a spawn that prompts, prompts every time.
- **The project-scope rank-min in §(e) is currently inert.** It is retained as a
  tested defensive invariant; a future change to the `auto` mapping or the clamp
  ceiling makes it load-bearing, and its test is written so such a change must
  state its intent.
- **`role`, and `SubagentMode`'s `"parallel"` / `"chain"`, are declared but
  unreachable** until P3 raises `MAX_SUBAGENT_DEPTH` and adds the modes.
- **The `/proc` descendant walk is racy in theory.** Accepted for a single-user
  local product; real containment is P3.
- **`trust.json` lost-update** is unchanged from ADR-0178 — the child never writes
  it (clause 1 emits nothing, clause 2 forces step 1, and neither persists), so
  P2 adds no new writer, but it also does not fix the existing one.
