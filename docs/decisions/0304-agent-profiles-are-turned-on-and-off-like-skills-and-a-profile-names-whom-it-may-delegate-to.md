# 0304. Agent profiles are turned on and off like skills, and a profile names the profiles it may delegate to

Status: Accepted (2026-11-17, #795, #796). Implemented on `69e43857` (after #775 `b6100968` and
#776 `69e43857` merged). What holds each decision is listed in "How it is held" at the end: the
tests, the mutants and the live runs, each with the condition it was measured under. Where the
implementation differs from the design draft (`.omc/specs/795-796-adr-0304-draft.md`, revision
1), the decision below says what was built and "Where the build differs" lists each change with
its reason. Amended 2026-11-18 by ADR-0305 (#624): D14 is done (note at the end).
Amended 2026-11-25 (#383): D15 is measured on an installed 0.1.0b2, except the `/agents list`
diagnostic (note at the end).
Date: 2026-11-16 (design revision 1: 2026-11-17; implemented 2026-11-17)
Issue: #795 (the agents panel), #796 (the `agents:` frontmatter key). One lane, one PR, one
commit (the contract's §7 says why).
Design brief: `.omc/specs/795-796-design-brief.md`. Scout: `.omc/specs/agents-panel-scout-report.md`
(drafts in `.omc/specs/agents-panel-drafts/`). Implementation contract:
`.omc/specs/795-796-impl-contract-2026-11-16.md`.
Amends: ADR-0279 (a fourth kind under the contract; D1's plain path entry is not supported for
`agents`; D6's busy-apply pending check also compares a kind's applied lists; D8's warnings
for `agents`), ADR-0196 (a new frontmatter key; discovery keeps the profiles that lose a name
collision; a bare `/agents` opens a panel; `/agents list` and `/agents show` gain rows),
ADR-0197 (f) and (l) (the roster and the model door are filtered; the human doors are not; the
seam gains an optional `ModelDelegation` member and the `DelegationNotAllowed` refusal),
ADR-0277 (the resume refusal for a profile that is off or not listed; D7's listing leaves out
agents the session may not resume now), ADR-0199 (g) (the rule a nested delegation will
follow). Notes on ADR-0283 D10 (the shared panel gains a per-row "not in use now" wording that
takes precedence over the `project` provenance word) and ADR-0298 (Ctrl+B is not gated). Each
carries a dated note.
Relates: ADR-0235 (pi is the reference, not a parity mandate), ADR-0281 (one group per directory),
ADR-0282 (MCP state words), ADR-0284 (`extensions` plain path deferred, the precedent for D1
here), ADR-0289 (a failed rebuild keeps what was applied), ADR-0292 (what runs mid-turn),
ADR-0293 / ADR-0295 / ADR-0298 (jobs, background agents, Ctrl+B), ADR-0299 (#776's `/jobs`
panel), #624 (control-v1 exposure of saved selections), #383 (beta.2 → beta.3 upgrade and
downgrade check), #253 (`contributes.agents`), #16 and #123 (nesting).
pi: `earendil-works/pi` `origin/main` `b7bb7ca90` (re-read 2026-11-17; the first draft read
`71723c6cb`, and none of the 21 commits between them touches agent-profile code). pi has no
agents panel and no delegation allowlist: its core resource types are `extensions`, `skills`,
`prompts` and `themes` (`packages/coding-agent/src/core/package-manager.ts:247`), its
`settings-manager.ts` has no `agents` key, the coding-agent subagent example's `AgentConfig`
has no on/off or allowlist field, and the p2 app's subagent plugin
(`apps/p2/plugins/subagents/`) has neither. Both features are aelix's own; nothing here diverges
from pi because pi has nothing here.

Owner decisions this implements, all 2026-11-16. Only the quoted words are the owner's; the text
after each quote is the lead's reading of it (`.omc/specs/795-796-impl-contract-2026-11-16.md`
§1), not the owner's words:
- "beta.3에 넣기": #795 and #796 go in beta.3, at P1 in B6, in one lane and one PR, after #775
  and #776.
- Storage, "skills와 똑같이 (권장)": two files, global and project; the project file wins;
  untrusted project files are neither read nor written (ADR-0279 D2/D3).
- Off profiles, "위임에서만 막기 (권장)": an off profile is hidden from the model's roster and
  refused at the model's door; a human-typed `/agents use`, `--agent` or `/agents run` is still
  allowed.
- Allowlist, "`agents:` 키, 없으면 전부 (권장)": frontmatter key `agents:`; absent means every
  enabled profile; `[]` means no delegation; a list means only those names; names only, no globs.
- O1, "`agents` (권장)": the settings key is `agents`.
- O2, "적용 안 함 (권장)": RPC `agents/run` is not filtered.
- O3, "추가 (권장)": agents are added to #624's control-v1 targets. #624's body records it
  ("2026-11-16 추가 — agents 종류 (오너 결정)", issue edited 2026-11-16 15:12Z).
- O4, "문서에만 밝힘 (권장)": that older versions ignore both keys is documented only, and
  measured in #383.
- Extension API, "문서화 + 필터 API 제공 (권장)". The owner's words are only those; what follows
  is the lead's reading of them: the public `SubagentRuntime.spawn` and
  `resolve_profile` stay unfiltered, as human paths; a filtered `resolve_for_model` is exposed
  for extension authors who build model-facing tools; the guide says model-facing extension
  tools must use it; the guide and this ADR state the limit plainly: the feature is a policy on
  aelix's own doors, not a security boundary. An installed extension, or a model with `bash`,
  can still run `aelix --agent <profile>`.

The lead's calls (2026-11-16, not the owner's):
- A bare `/agents` opens the panel. `/agents list` keeps the table and gains an ON column.
  Headless mode keeps the plain list. (As built: a command context with no panel opener keeps
  the list; `-p`, `--mode json` and RPC dispatch no slash command at all, D9 and D11.)
- The allowlist does not apply to a human-typed `/agents run`, by the same principle as the
  owner's "위임에서만 막기".
- For future nesting (#16, #123), a child's list is its own list intersected with its parent's,
  and `role: leaf` wins. This round only writes the rule down (D16).
- The panel uses the existing `tui/selection_panel.py`, as `/skill` and `/tool` do, not #776's
  jobs widget.

The lead's call (L1 in the implementation brief; not the owner's):
- A settings file that did not load at launch is reported once and its `agents` list is not
  applied, which leaves on what it turned off (D3, D13). Fail-closed is the alternative (A15).

Every other choice below is this ADR's and is labelled so where it matters.

## Context

Read at `2f3cdf28` and `5d0858ae`; "M / B" gives the line at each where they differ.

- **No profile can be turned off.** The only switches are `features.agents` (global-only, default
  off; ADR-0197 (m)) and `--agents` / `--no-agents`, which turn delegation as a whole on or off.
  A user profile is removed by deleting its file; a bundled one (`explorer`, `general-purpose`)
  cannot be removed at all, only shadowed by a same-named user file
  (`agents/discovery.py` `builtin_agents_dir`). `general-purpose` declares no `tools:`, so it
  inherits every tool. "Delegate only to the read-only `explorer`" cannot be set.
- **No profile says whom it may delegate to.** A session that runs as a profile (`--agent`,
  `--agent-file`, `/agents use`, RPC `agents/use`) offers the model every discovered profile.
  `agents/profile.py` `_KNOWN_KEYS` (line 98) has no such key; an unknown key is warned about
  and ignored (`unknown frontmatter key … (ignored)`, line 337), so an `agents:` written today
  loads with no effect.
- **The roster** is rebuilt from discovery on every `before_agent_start`, notice turns included
  (`aelix_agents/extension.py` `AgentsExtension._roster` 975 / 992, called from
  `_on_before_agent_start` 1002 / 1019), and lists every discovered profile with its scope.
- **aelix's own model doors.** The model's `agent` call (`_on_tool_call` 1065 / 1082, resolve at
  1125 / 1142), its resume (`_approve_resume` 1216 / 1233, resolve at 1267 / 1284) and the
  pre-spawn re-check (`_execute_pending` 1686 / 1703, resolve at 1721 / 1738) call
  `_SubagentRuntimeImpl.resolve_profile(name, allow_project=False)` (`aelix_agents/runtime.py`
  790 / 795), which runs discovery through `discovery.resolve_profile` and builds the only
  `ResolvedProfile` in `aelix_agents` (832 / 837). The human doors `/agents run`
  (`tui/commands.py` `_agents_handler` 1258, resolve 1407/1418, `runtime.spawn` 1441) and RPC
  `agents/run` (`rpc/control.py` `RpcControlPlane.run_agent` 848, resolve 883/887, `spawn` 893)
  call the same pair; they may confirm a project profile.
- **The children are started** by `spawn_granted` (`_execute_pending` 1864 / 1888 and the batch
  member `batch.py::_member` 464), `resume_granted` (1842 / 1863), `run_batch` (1880 / 1906),
  the background `self._spawn` (1733 / 1750, which builds `jobs_client._Launch` at 1992 / 2199)
  and the job runner's `runtime._run` (`jobs_client.py` `DelegationRunner.run`, 499 / 502).
  Every one of them is reached from the call the hook approved and runs the object the grant was
  taken against (`pending.resolved`).
- **Extensions hold the same unfiltered seam.** `api.runtime.subagents` (`extensions/api.py`
  612-620, `ExtensionAPI.runtime` 2466) hands any extension the bound `SubagentRuntime`, whose
  `resolve_profile` and `spawn` are the human doors. `spawn` asks its own consent, which
  succeeds without asking when there is no UI (`aelix_agents/consent.py` 1439-1446). No bundled
  extension wraps it in a model-facing tool (opus review, grep).
- **What the model reads** for a name it cannot use: `Delegation refused: unknown agent profile
  'x'; available: …`, where "available" is every discovered profile
  (`discovery._unknown_name_message` 438). In a folder that is not trusted, that function first
  PARSES the project's `.aelix/agents/*.md` (`_scan_dir`, 378-436) to say "pass --approve",
  advice that cannot make a model retry succeed: the model door refuses every project profile
  (B5, `runtime.py` 823 / 828). A resume whose profile cannot be re-resolved, for any reason,
  reads `RESUME_PROFILE_CHANGED` (`aelix_agents/child_session.py` 528; ADR-0277), and a missing
  or unknown resume id carries a records-only listing of resumable agents with their profile
  names (`child_session.py` 1525, 1528, `_listing` 1991).
- **The selection contract** (ADR-0279, ADR-0283, ADR-0284) already turns skills, tools and
  extensions on and off: two settings files, a per-kind `ResourceKind`, one
  `ResourceSelection` service, one panel (`tui/selection_panel.py`), one write primitive
  (`SettingsManager.edit_list`), and an applied state that changes only at idle. Its busy-apply
  path compares only row values: with no row moving it returns success and defers nothing
  (`core/resource_selection.py` 615-623). `resource_filter._realpath` caches resolved paths
  until `forget_resolved_paths()` (169-184), which the skill catalog load and every panel
  snapshot call.
- **The commands' context.** The TUI builds the only `CommandContext` (`tui/shell.py` 2707 / 2711);
  it carries `agent_profiles` (`agent_service.list`) and `use_agent`, nothing that knows the
  applied lists. RPC `commands/run` (`rpc/control.py` 528-570) dispatches extension commands,
  prompt templates and skills only; a built-in such as `/agents` is `not_a_command` there.
- **Nesting does not exist.** `MAX_SUBAGENT_DEPTH = 1` is hard-coded
  (`subagent_contract.py`; ADR-0197 (c), ADR-0199 (g)): a delegated child has no `agent` tool,
  so a profile's delegation list can only matter for the main session's identity today.
- **#775** (ADR-0298) adds Ctrl+B, which moves a running foreground call into a background job,
  and `SubagentHost.claimed` (`runtime.py` 503 at B). It moves only a single `run` or a `resume`:
  `_detach_call` returns `None` for any other mode (`extension.py` 1990 at B). It resolves no
  profile and starts no child. #611's background `spawn` (ADR-0295) goes through the same
  `agent` hook as a `run`. Both are inert until #611 S3e (`jobs/activation.py`).

## Decision

### D1. The settings key is `agents`, with ADR-0279 D1's per-item grammar

Owner decision O1, 2026-11-16, "`agents` (권장)". A top-level `agents` array of strings in
`~/.aelix/agent/settings.json` and `<cwd>/.aelix/settings.json`, beside `skills`, `prompts` and
`extensions`. It is a different key from `features.agents`, the global-only boolean that decides
whether delegation exists at all (ADR-0197 (m)), and from `backgroundJobs.limits.agents`, a job
count; all three stay, and the guides set them apart.

The grammar is ADR-0279 D1's per-item grammar, as skills use it:

| Entry | Meaning |
|---|---|
| `-name` / `+name` | turn the profile whose `name` field is `name` off / on |
| `!glob` | turn off every profile the glob matches, by its name or by ADR-0279 D1's path forms of its file (minimatch, with D1's limits) |
| `-<path>` / `+<path>` | the exact file, by ADR-0279 D1's path forms; a path in the project file reaches project profiles only, a path in the global file every other tier (ADR-0279 D2) |
| plain path (no prefix) | **not supported for `agents`**: warned once at launch and ignored (D13) |

A profile's identity is its `name` field, not its file name (ADR-0196 D2). The panel writes
`-name` and `+name`, so an entry survives the file moving. One list applies `!`, then `+`, then
`-`, whatever their order (ADR-0279 D1). An empty list, `"agents": []`, turns nothing off; that
is the opposite of a profile's `agents: []` (D4), and both are tested.

Not ADR-0279 D15's set-level grammar: there is no default set of profiles to replace, and each
profile is an item on its own, as each skill is (this ADR's choice). A plain path entry would
add a profile directory, which is a new discovery tier with its own trust question; ADR-0284 D9
deferred the same row for `extensions` for the same reason (this ADR's choice).

"Names only, no globs", the lead's reading of the owner's allowlist decision, is about the
frontmatter `agents:` list (D4). The settings list keeps `!glob`, because it is skills' grammar
and the owner chose "skills와 똑같이".

No profile name is reserved. Profiles named `agents`, `features`, `skills`, `tools`, `inherit`
or `none` pass `_validate_name` today and keep meaning themselves, in the settings list and in
`agents:` alike (`agents: [none]` is the profile named `none`).

### D2. Scope and trust are ADR-0279 D2 and D3, unchanged

Owner decision, 2026-11-16, "skills와 똑같이 (권장)". A profile's saved value is the project
file's opinion if it has one, else the global file's, else on. An untrusted project's file is
never opened, locked or stat'ed; the panel cannot choose "This project" there. A first project
save creates `.aelix/settings.json` and asks first, because the file puts the folder under
project trust (ADR-0279 D3). Every write goes through `SettingsManager.edit_list(scope, "agents",
…)` (ADR-0279 D4).

What follows from the owner's choice, stated so it is not read as an oversight: a trusted
project's file can turn ON a profile the user turned off in the global file. It cannot turn
delegation itself on: `features.agents` stays global-only (ADR-0197 (m)), and a `"+reviewer"`
with `features.agents` off changes nothing the model sees (tested). An untrusted project's
profiles are not discovered at all (ADR-0196 D2), so its file has nothing to decide.

### D3. Saved and applied: one holder, read at launch and `/reload`, applied at idle

ADR-0279 D5's states hold. The **applied** state is the two validated `agents` lists as they
were last applied, kept in one holder in `cli/entry.py` beside `skills_holder`:

- read at launch, and again by a `/reload` or RPC `resources/reload` build, rolled back with
  the skills and prompt catalogs when that build fails (ADR-0289);
- replaced by the panel's apply (D10);
- not re-read by `/new`, `/fork`, `/resume` or `/agents use`, as the skills holder is not.

The holder keeps the LISTS, not a set of names: discovery runs on every prompt (ADR-0197 (l)),
so a profile file written mid-session is decided by the applied lists the first time it is
found (this ADR's choice). A hand edit or another session's save is pending until Enter or
`/reload` (ADR-0279 D5).

The apply is ADR-0279 D6's: at once when the session is idle; while a turn runs, or while a
harness build holds the `ApplyGate`, deferred until idle, then applied from a fresh read, newest
save winning; an unreadable file applies nothing. It rebuilds no prompt: the roster is re-stamped
at the next `before_agent_start`, which runs before the next prompt's request. It emits
`resources_update` with `resources["agents"]`, the names in use before and after, when they
differ (ADR-0279 D7's third step; ADR-0283 D6 added `resources["tools"]` the same way).

**Pending is a change of lists, not only of rows** (amends ADR-0279 D6, for kinds that keep
lists; Codex 4). Because the holder keeps lists, a saved change that moves no row today — a
`!rev*` replacing `-reviewer` while only `reviewer` exists — still changes which profiles a
later discovery turns off. So a busy apply compares the lists the snapshot read with the lists
the kind has applied, as well as the rows, and defers when either differs. Skills, tools and
extensions declare no applied lists and keep today's row-only check.

**`AgentKind.apply` applies the files' lists** (opus 15). `ResourceSelection.apply` passes the
names its snapshot evaluated; `AgentKind.apply` re-reads the two lists synchronously, before its
first `await`, and commits those, so a name a later discovery finds is decided by them. The
names it was passed are what those lists evaluate to: both reads happen with no `await` between
them (ADR-0279 D6's "nothing yields"), and the panel passes no snapshot of its own
(`selection_panel.py` 1247). A test pins `after == enabled`.

Because the apply waits for idle, the roster and the doors of one prompt read the same applied
state. They can still disagree with the FILES on disk; that is pending, as above.

**A settings file that did not load** (opus 6; recommended to the lead by revision 1). At launch
`SettingsManager._try_load` gives that scope an empty `Settings()` (`settings_manager.py`
572-587), so its `agents` list is absent and every profile it turned off is on for the run. A
`/reload` that fails to load a file keeps that scope's previous settings
(`SettingsManager.reload`, 793-826), so nothing changes there. Revision 1 recommends keeping
ADR-0279's launch rule ("a settings file that did not load contributes no entries", its Limits)
and saying it once on stderr (D13), not failing closed: the owner chose "skills와 똑같이", the
lead's reading of the owner's extension-API decision calls this a policy, not a security
boundary, and a fail-closed launch would
need its own verdict and refusal texts and would refuse every model delegation for a typo the
D13 line already names. A failed GLOBAL file also loses `features.agents`, so delegation is off
for that run unless `--agents` was passed. The panel shows the file as unreadable and refuses to
apply from it (ADR-0279 D6, D11).

A delegated child does not read the `agents` lists: it has no `agent` tool (ADR-0197 (c)).

### D4. The frontmatter key `agents:` (#796)

Owner decision, 2026-11-16, "`agents:` 키, 없으면 전부 (권장)". `AgentProfile.agents:
tuple[str, ...] | None`, parsed like `tools:` (ADR-0196 D1) and three-valued like it:

| Written | `AgentProfile.agents` | Means |
|---|---|---|
| key absent, or `agents:` with no value (YAML null) | `None` | every profile that is on (D5) |
| `agents: []` or `agents: ""` | `()` | delegates to no profile |
| `agents: [explorer, reviewer]` or `agents: explorer, reviewer` | `("explorer", "reviewer")` | only those names |

Parse rules (this ADR's, following `tools:` and the lead's reading of the allowlist decision,
names only):

- A YAML list or a comma-separated string, as `tools:` accepts (`profile._coerce_str_list`);
  entries are stripped and blank ones dropped. `agents: ""` therefore reads as `[]`, as
  `tools: ""` reads as no tools.
- Each entry must pass the four name rules of `profile._validate_name` (lowercase `a-z`, `0-9`,
  `-`; at most 64 characters; no leading or trailing hyphen; no `--`). An entry that fails is an
  ERROR, and the profile does not load, as every other invalid value does (ADR-0196 D1: a bad
  identity is fatal; a non-string `tools:` entry fails the profile the same way). An entry
  holding a glob character (`*`, `?`, `[`, `{`, `!`) gets its own message:
  `agents: entries are profile names; 'expl*' is not one (globs are not supported)`.
- A non-string entry, a number, or a mapping is an ERROR.
- A name listed twice is kept once, in first-seen order.
- A profile may list its own name.
- **A name no discovered profile has is a WARNING, never an error.** Discovery adds it
  (`invalid_metadata`, type `warning`, on the declaring file): `agents: names 'reviewr', which
  no agent profile found here has`. `/agents list` shows it. `discovery.resolve_profile`'s fatal
  path escalates only `missing_path`, so `--agent orchestrator` still launches. The name is
  matched again at every check, so a profile created later is allowed from then on.
- Allowed at every scope, a project profile's included: the list only narrows.

**What an ERROR costs** (opus 12, stated so it is not read as an oversight). A profile whose
`agents:` does not parse does not load at all: `--agent` refuses it at launch, `/agents use`
refuses it, and it is gone as a delegation TARGET too, although `agents:` means nothing while
a profile is a target. That is discovery's rule for every broken profile (a broken profile is
not found by name; `tests/agents_ext/test_agent_unsendable_text_627.py` pins it for `model:`).
`/agents list` shows the error, and the panel shows the file as a failed row (D10). Dropping the
bad entry with a warning was considered (A17) and not chosen: an identity whose allowlist was
not read as written is not the identity the user wrote, and the launch error is the loud place
to learn it.

The list is read when the profile becomes the session's identity: `--agent` / `--agent-file` at
launch, `/agents use`, RPC `agents/use`. Like every other field of the identity, an edit of that
file takes effect at the next `/agents use` or launch (ADR-0196 D8; this ADR's choice).

It applies only while the profile is the main session's identity. Today a profile's `agents:`
means nothing when that profile is the TARGET of a delegation, because its child has no `agent`
tool (ADR-0197 (c)). D16 writes down what it will mean when nesting arrives.

### D5. One function decides; the roster and every door aelix opens to the model ask it

`aelix_coding_agent.core.agent_selection.delegation_view(discovered, lists, identity)` is the one
place the three inputs meet:

- `discovered`: one discovery result (ADR-0196 D2), with the losers kept beside the winners
  (D10); the view keeps the whole result;
- `lists`: the applied `agents` lists (D3); `None` means every profile is on (a host that wired
  no lists, which keeps every existing embedder and test unchanged);
- `identity`: the session's profile; `None` means no allowlist.

It clears `resource_filter`'s resolved-path cache (`forget_resolved_paths()`) before it
evaluates, so a symlink re-pointed since the last evaluation is judged by its new target
(Codex 3; the skill catalog load and the panel snapshot already do this, ADR-0279 MF1).

Its `available` is the discovered winners that the lists leave on and the identity's `agents:`
allows (all of them when `identity` or `identity.agents` is `None`), sorted by name. Its
`verdict(name)` is the first of these that applies:

1. `none_listed`: the identity's `agents:` is `[]`;
2. `unknown`: no discovered winner has the name;
3. `not_listed`: the identity has a list and the name is not on it;
4. `project`: the profile is project-scoped (ADR-0197 (f), finding B5);
5. `off`: the lists leave it off;
6. `ok`.

The order is this ADR's: the refusals no settings change can cure come first, so the model is
never told "only the user can turn it on" about a profile that turning on would not unlock.
A name is in `available` exactly when its verdict is `ok`, or `project` and the lists leave it
on: a project profile that is off reads `project` (the order above) and is hidden (D7). A
property test pins it over every combination of up to two of six global entries, three project
lists and five identities (`tests/core/test_agent_selection_795.py`
`test_property_available_is_exactly_ok_or_an_on_project_profile`, more than 1 000 checks): the
roster and the door cannot disagree about one view.

**Neither list widens the other.** A profile on the identity's list that is off stays off; a
profile that is on but not on the list is refused; a project profile that is on and listed still
meets the B5 refusal at the model door.

In `aelix_agents`, `SubagentHost` gains two live getters, `agent_lists` and `active_profile`
(both default to `None`). `aelix_agents/runtime.py` gains `delegation_for(host)`: one
`discover_profiles` with the host's cwd, trust and agent directory, then `delegation_view`. The
roster calls it with the extension's host, so it works before a runtime is bound, as the roster
does today. `_SubagentRuntimeImpl` gains:

- `delegation()`: `delegation_for(self.host)`;
- `resolve_for_model(name)`: the model's door, **public** (D17). The path-shape refusal; then
  ONE `self.delegation()`; its verdict; then the profile is taken from THAT view's discovery
  result — never from a second discovery — checked by `discovery.complete_profile` (the
  `missing_path` escalation `discovery.resolve_profile` applies, factored out so both share it)
  and turned into a `ResolvedProfile` by the one constructor helper, which applies B5 (Codex 3:
  the profile checked is the profile returned). A refusal raises: `DelegationRefused` for
  `none_listed`, `not_listed` and `off` (a `ProfileError` and a `DelegationNotAllowed`, carrying
  the verdict, the identity's name and list, and the available names); `ProjectScopeProfileError`
  for `project` (today's type and text); `ProfileError` for `unknown`, a path-shaped name or a
  missing path.

`resolve_profile`, the `SubagentRuntime` Protocol method, keeps its behaviour and its texts. It
is what the human doors call (D6), and filtering it would block them, against the owner's
decision. It builds its result through the same constructor helper, so `ResolvedProfile(` is
built in one place.

Product-core reads the same function through `AgentProfileService.delegation()`: `/agents list`
and `/agents show` through `CommandContext.agent_delegation` (D11), RPC `agents/list`, and
`AgentKind` (the panel), which is handed that method rather than calling `delegation_view`
itself. The function lives in product-core because product-core needs it and `aelix_agents` may
import product-core but not the reverse (ADR-0197 (a)). It decides a resource selection, which is
product-core's since ADR-0279. It holds no spawn behaviour, cap, registry or consent policy, so
the band rule (`tests/agents/test_p2_band_boundaries.py`) is kept: the 24-name cap in D7 lives in
`aelix_agents`, beside `MAX_ROSTER`.

**A gate test holds the shape** (the contract's §5.3). It is structural regression protection,
not a proof that every model-originated start is checked: it reads the source of
`aelix_agents` and product-core and fails when (a) a profile source, an identity object or a
start sink is loaded — called, read as an attribute or passed as a value; the three constructors
`ResolvedProfile(`, `PendingSpawn(` and `_Launch(` only where they are called — outside its exact
set. The identity objects (`ResolvedProfile(`, its one constructor helper `_resolved`,
`PendingSpawn(`, `_Launch(` and `ResolvedProfile`'s copy forms) and the start sinks
(`spawn_granted`, `resume_granted`, `run_batch`, `_run`, `_execute_pending`, and `/agents
run`'s consented launch `_spawn_consented`) are inventoried across BOTH packages; product-core
loads none of them. `._spawn` is inventoried in `aelix_agents` only, because product-core has
unrelated methods of that name; from product-core, building the `PendingSpawn` it would need is
what the gate holds, and a `PendingSpawn` taken from the extension's `_pending` is data through a
container, which the gate does not follow (Limits). The profile sources (`discover_profiles`, discovery's `resolve_profile`,
`complete_profile` and the profile loaders) are inventoried in `aelix_agents` only, because
product-core's own commands read profiles. (b) The `resolved` a `PendingSpawn` carries can come
from anything but `resolve_for_model` (a dataflow rule over the hook's local bindings, not line
order). (c) A sink in `_execute_pending` runs anything but the approved `pending` object, or is
not preceded in the function's own statement list by the re-check —
`<name> = <…>.resolve_for_model(…)` with the call as the whole right-hand side, so
`pending.resolved or <call>` does not count — and then by an `if` whose test reads a name the
re-check's statement binds and whose body ends in `return`. That is the refusal's SHAPE: the
gate does not check that the refusal can fire (Limits names three forms that pass it), and the
door tests that move a profile under an off path between the hook and execute are what hold
that it does. Import aliases are resolved (`from dataclasses import replace as swap`), and a
gated name reached by `getattr`, `operator.attrgetter`, `importlib.import_module` or
`__import__` with a constant string is a violation (a computed string is not matched). Its positive controls are synthetic sources
committed with it, one or more for every rule, so they run in CI. It does not follow calls
through helpers outside its rules, data through containers, a function reached through a plain
assignment (`g = getattr`), `exec`, or code outside the two packages: an extension's tool, an
RPC host and a shell command are D17's and Limits' business.

### D6. Every entry point, and what gates it

Lines are `2f3cdf28` / `5d0858ae` where they differ.

| Entry point | Code | Gate | What the model or user gets |
|---|---|---|---|
| Roster: the `agent` tool's description, every prompt and every notice turn | `AgentsExtension._roster` 975 / 992 via `_on_before_agent_start` 1002 / 1019 | `delegation_for(host).available` | off and not-listed profiles are absent; an empty roster is one of D7's sentences |
| Model `agent` run, one task | `_on_tool_call` 1065 / 1082 → `resolve_for_model` | verdict | refused at the hook, before any dialog, budget or slot; D7 texts |
| Model `agent` run, parallel batch | same call; one profile per call (ADR-0199 (b)) | verdict, once | same |
| Model `agent` run, chain | same | verdict, once | same |
| Model `agent` background `spawn` (#611; behind the job gate) | same | verdict, once | same |
| Model `agent` resume (#610) | `_approve_resume` 1216 / 1233 → `resolve_for_model` | verdict | refused at the hook, before the dialog; D7 resume texts |
| Model `agent` resume with a missing or unknown id | `find_resume_target` → `child_session._listing` 1991 | the view (D7), only while a policy is in force | the listing leaves out agents whose profile's verdict is `none_listed`, `not_listed` or `off`, and counts them; an agent of an unknown (deleted) or project profile is listed as before |
| Pre-spawn re-check | `_execute_pending` 1686 / 1703 → `resolve_for_model`, above `_spawn` 1733 / 1750 and `_detach_call` — / 1854 | verdict again, then the source path compared as today | refused after the dialog and the slot, before the budget, any record or process (D7); a different file now winning the name is `_IDENTITY_MISMATCH` as today |
| Batch members, chain links, the background job's runner | `batch.py::_member` 464 (`spawn_granted`), `jobs_client.py::DelegationRunner.run` 499 / 502 (`runtime._run` on `launch.resolved`) | not re-checked | they run the approved call, queued members included (D8) |
| Ctrl+B (#775) | `_detach_call` / `_run_call`, `detach.py`; single `run` and `resume` only (1990 at B) | not gated | it starts nothing; the moved run continues (D8) |
| `agent` wait / status / stop / result, `job(...)`, `/jobs` | `_control` 2060 / 2267, the jobs facade | not gated | they start nothing |
| An extension's model-facing tool that calls `api.runtime.subagents.resolve_for_model` | the extension's code | verdict (D17) | the extension's tool returns the refusal; the guide says such tools must use this method |
| An extension that calls `resolve_profile` + `spawn` | the extension's code; `extensions/api.py` 612-620 | not filtered (D17) | an off or not-listed profile runs; Limits |
| `/agents run <name> <task>` | `_agents_handler` 1258 → `runtime.resolve_profile` 1407 + `runtime.spawn` 1441 (moved to the `_agents_run` helper) | not filtered | an off profile runs (the owner's "위임에서만 막기"); one outside the identity's list runs (lead); one dim line says the model could not (D11) |
| RPC `agents/run` | `RpcControlPlane.run_agent` 848 → `resolve_profile` 883 + `spawn` 893 | not filtered (owner, O2) | as `/agents run`; a host that hands it to a model is outside this policy (Limits) |
| `/agents use`, RPC `agents/use`, `--agent`, `--agent-file` | `AgentProfileService.use`, `cli/entry.py` 3459 / 3462 | not filtered | an off profile can be the identity (the owner's "위임에서만 막기") |
| A model with `bash` running `aelix --agent <profile> -p …` | a new process | not filtered | outside aelix's doors (Limits) |
| `/agents list`, `/agents show`, RPC `agents/list`, `agents/show` | `AgentProfileService.list` / `.delegation()` | shows | ON column, rows and fields (D11, D12) |
| bare `/agents` (TUI with settings) | the panel (D10) | edits | — |
| A delegated child (depth 1) | depth guard (ADR-0197 (c)) | n/a | it has no `agent` tool |

`/agents run` and RPC `agents/run` reach `runtime.spawn`, the user-typed door with its own
consent; aelix's model doors reach `spawn_granted` and `resume_granted` only. The line between
filtered and not is drawn there, and D17 hands extension authors the filtered half.

### D7. What is hidden, what is refused, and the exact texts the model reads

**Hidden** from the roster: a profile that is off, one outside the identity's list, and every
profile when the list is `[]`. **Listed and refused** at the door, as today: a project profile
that is on and listed (ADR-0197 (l) keeps it listed so the model can tell the user to run it).
**Refused by name**: all of these, and an unknown name.

The `agent` hook prefixes `Delegation refused: ` as it does today, so the runtime's messages do
not repeat it. Names are the profiles' `name` fields, which `_validate_name` restricts; a name the
model sent is shown with `repr`, as today. `<available>` is `available: a, b`, every name in D5's
`available`, sorted, uncut, as today's text lists every discovered name (opus 11: a run with
nothing filtered reads byte-identical to today at any profile count). With nothing available it
is `no agent profile is available to delegate to`. The identity's own `agents:` list appears only
in the empty-roster sentence, in written order, cut at 24 names (`MAX_ROSTER`), then
`, … (+N more)`.

Run, batch, chain and background spawn (the hook and the pre-spawn re-check):

| Verdict | Text |
|---|---|
| `none_listed` | `Delegation refused: this session runs as agent profile 'orchestrator', whose agents: list is empty, so it delegates to no profile` |
| `unknown` | `Delegation refused: unknown agent profile 'x'; <available>` (today's text; `available` is now D5's set) |
| `unknown`, nothing discovered at all | today's `no profiles found in <user agents dir>` text, unchanged |
| `not_listed` | `Delegation refused: agent profile 'reviewer' is not one this session's profile 'orchestrator' may delegate to; <available>` |
| `project` | today's B5 text, unchanged: `… is project-local and needs a per-identity confirmation; a model-chosen delegation cannot give one. Run it yourself with /agents run <name>, or start aelix with --approve.` |
| `off` | `Delegation refused: agent profile 'reviewer' is turned off in the settings, and only the user can turn it on (with /agents); <available>` |

The `not_listed` text does not print the identity's list (opus 15): that list can hold profiles
that are off or not found, and naming them would invite calls the door refuses. `<available>`
already is the intersection.

**No peek at the model door** (Codex 6; this ADR's choice). The model door's `unknown` text never
reads an untrusted folder's `.aelix/agents/`: today's "exists only in … pass --approve" variant
parsed those files and gave advice that cannot make the model's retry succeed (B5 refuses every
project profile at the model door). The human doors keep it: `--agent`, `/agents use` and
`/agents run` go through `discovery.resolve_profile`, whose `_unknown_name_message` still peeks,
parse-only, as today (`tests/cli/test_agent_flag_end_to_end.py`
`test_project_profile_inert_when_untrusted` pins that). No test pins the peek at the model door.

Resume (ADR-0277's row "the profile cannot be re-found" is amended; every other resume refusal
is unchanged):

| Verdict | Text |
|---|---|
| `none_listed` | `Resume refused: this session runs as agent profile 'orchestrator', whose agents: list is empty, so it resumes no agent.` |
| `not_listed` | `Resume refused: the agent profile 'reviewer' that agent ran as is not one this session's profile 'orchestrator' may delegate to. Available: explorer.` |
| `off` | `Resume refused: the agent profile 'reviewer' that agent ran as is turned off in the settings, and only the user can turn it on (with /agents). Available: explorer, general-purpose.` |
| `unknown`, `project`, a different file now winning the name, any other failure | `RESUME_PROFILE_CHANGED`, unchanged |

(`Available: …` there is `<available>` capitalised and ending with a period; with nothing
available it reads `No agent profile is available to delegate to.`)

**The resume listing** (amends ADR-0277 D7; Codex 9, opus 7). The records-only listing a missing
or unknown resume id carries leaves out every agent whose recorded profile the session's
settings or identity rule out now — verdict `none_listed`, `not_listed` or `off` — and ends with
one line counting them when there are any:
`- N more ran as an agent profile this session may not delegate to now; they cannot be resumed.`
No name is printed for them. An agent whose profile is unknown now (deleted) or project-scoped
is listed as before, and its resume is refused as before (`RESUME_PROFILE_CHANGED`). With no
policy in force — no entry in either applied list and no `agents:` on the identity — the
listing is today's, byte for byte, and takes no discovery at all. The listing is still built
from the records only and opens no child file; with a policy in force the view costs one
discovery, taken only when a listing is built. Without this, ten such agents
(`LISTED_AGENTS = 10`) could fill the listing and push out the resumable ones.

What replaces `Available profiles:` in the tool description when D5's set is empty:

| Case | Text |
|---|---|
| nothing discovered | `_no_profiles_message()`, unchanged |
| the identity's list is `[]` | `No agent profile is available: this session runs as agent profile 'orchestrator', whose agents: list is empty, so it delegates to no profile.` |
| the identity has a list, some of it is found, and every found one is off | `No agent profile is available: this session runs as agent profile 'orchestrator', which may delegate only to explorer, reviewer, and each of those that is found is turned off. Only the user can turn one on (with /agents).` |
| the identity has a list and none of it is found | `No agent profile is available: this session runs as agent profile 'orchestrator', which may delegate only to explorer, reviewer, and none of those is found.` |
| no identity list, and every profile found is off | `No agent profile is available: every agent profile found is turned off in the settings. Only the user can turn one on (with /agents).` |

"Only the user can turn … on" appears only where turning a profile on would make it available
(opus 15; D5's principle). The previous "write a profile file" message (`_no_profiles_message`)
was the wrong advice for the last four cases (#795).

**When each refusal is taken** (Codex 10). At the hook (`_on_tool_call`, `_approve_resume`):
before the dialog, before the per-prompt budget is charged, before a slot is reserved, before any
record or process (ADR-0199 §3.5.2.1, ADR-0277 D11). At the pre-spawn re-check
(`_execute_pending`): AFTER the dialog was answered and the slot reserved — `_execute`'s
`finally` gives the slot back — and before the budget is charged, before any record or process,
as today's `_IDENTITY_MISMATCH` is. Within one prompt the re-check can differ from the hook only
through the filesystem (a symlink re-pointed under a `-<path>` entry, a profile file moved): the
lists and the identity cannot change mid-turn (D3, ADR-0292).

### D8. Running work is not stopped; an approved call continues as approved (this ADR's choice)

Turning a profile off, or switching to an identity whose list leaves it out, applies from the
next model-door start. **An already-approved call continues as approved** (Codex 8): every
member of a parallel batch and every link of a chain that its hook approved runs, the ones still
queued for a permit included, on the profile object the grant was taken against
(`pending.resolved`), as ADR-0199's one-check-per-call rule has it. Nothing running is stopped:

- A foreground delegation cannot see the change: the apply waits for idle (D3), a foreground
  batch or chain holds the turn until its last member ends, and `/agents use` and RPC
  `agents/use` are refused while a turn runs (ADR-0292, `rpc/control.py` `_require_quiescent`).
- Ctrl+B moves only a single `run` or a `resume` (`_detach_call`, `extension.py` 1990 at B), so
  no batch or chain leaves the foreground with members queued. The rule above is written so a
  later change that detaches one inherits it.
- A background job, or a run Ctrl+B moved, runs to its end; its notice arrives; `wait`,
  `status`, `stop` and `result` work; `/jobs` works.
- A later `resume` of that agent is a start, and is refused (D7); a fresh `run` is refused.

A toggle is not a stop request: Esc, `agent(action="stop")` and `/jobs` stop work (ADR-0276
D5: the bound on work is a human who ends it).

### D9. Modes

- **TUI**: the panel (D10), the roster and the doors (D5, D6), `/agents` (D11).
- **`-p` and `--mode json`**: the roster and the doors are filtered from the lists read at launch
  and the launch identity; `AgentsExtension` gets both getters in every mode (`cli/entry.py`
  3378, before the mode branch; a test checks `-p`, json and RPC). There is no panel and no
  slash command. A run with `--agent orchestrator` offers only its list.
- **RPC and ACP over it**: the roster and the doors are filtered as in `-p`; RPC
  `resources/reload` re-reads the lists; `agents/use` changes the identity, so the next prompt's
  roster follows; RPC's own fields are D12's.
- **Delegation off for the run** (`features.agents` off, `--no-agents`, or no `agent` tool
  because `/tool`, `--tools` or `-xt` removed it): no roster, no model door. `/agents run` is
  refused as today when the runtime is not bound. The panel still opens and saves, with a banner
  (D10).
- **A delegated child**: it has no `agent` tool and does not read the lists (D3). D16 is the rule
  for when it will.

### D10. The panel

The ADR-0279 D11 panel (`tui/selection_panel.py`, `run_selection_panel`) over a new
`AgentKind` (`kind="agent"`, `settings_key="agents"`, `effect="next_request"`, D1's grammar).
Title "Agents", noun "profile" / "profiles". The lead chose this panel over #776's jobs widget.
`AgentKind` and its `ResourceSelection` are built by one mode-independent factory (D14).

**Rows and groups**, in ADR-0279's layout-A order, each group drawn once with its shadowed rows
under it (ADR-0281 D7):

| Group | Rows |
|---|---|
| `PROJECT  .aelix/agents/` | the trusted project's profiles; in an untrusted folder, one locked summary row instead: `N profiles not loaded`, "This folder is not trusted, so its agent profiles are not loaded." The count is `discovery.count_untrusted_profiles`: the entries `_scan_dir` would consider (not a dotfile, `.md`, a regular file), counted with a directory listing; no file is opened (ADR-0279 D3). It is not `count_untrusted_skills`, whose `agents=True` means Agent Skills directories and counts zero profiles (opus 10) |
| `GLOBAL  ~/.aelix/agent/agents/` | the user tier (`$AELIX_CODING_AGENT_DIR/agents`, shown with `~`) |
| `BUNDLED` | the profiles shipped in the wheel |

- A profile row: the name, the description, the status column, and in the detail strip the
  path. Its row key is the name; its identity, the name; its path, the file.
- **Shadowed rows.** Discovery keeps the profile that loses a name collision, across tiers and
  within one tier (ADR-0196 D2 keeps both warnings). The loser is a locked-off row tagged
  `shadowed`: "shadowed by the <tier> profile of the same name (<path>)". A settings entry
  names the winner, which shares the name.
- **A file that did not load** is ONE placeholder row per file in its tier's group (opus 9),
  keyed by its path, labelled with its file name: "did not load: the model is not offered it",
  locked (lock kind `status`, so the state line is the reason itself), tag `failed`, state line
  `see /agents list · did not load: <first error>` (ADR-0283 D6's pointer-first rule). A file
  with two errors is still one row.
- **No run locks.** The identity's `agents:` list is not a lock: a row outside it can be toggled,
  because the saved value matters to other identities and sessions (the issue's design; this
  ADR keeps it).
- **"Not in use now" rows.** A row whose saved value is on but whose model-door start would not
  pass now gets the live state ADR-0283 D10 gave tools, with its own words:

  | Why | Status column | State line clause |
  |---|---|---|
  | outside the identity's list | `not listed` | `not listed by 'orchestrator'` |
  | a project profile | `/agents run` | `only /agents run starts a project profile` |

  The clauses are short so "Saved on (this project's file) · now off (not listed by
  'orchestrator')" keeps its reason at 80 columns; the identity banner names the list. A tool
  row keeps `off now` / `turned off this run`. The words come from the kind
  (`ItemState.live_note`), and show while the row is saved on. A saved-off row reads `off`, or
  `project` when this project's file decided it: that is the shared panel's word for every
  kind, measured for a skill row in the same state (a project file `"skills": ["-tidy"]`,
  applied) and pinned for both by `tests/tui/test_agents_panel_795.py`
  `test_a_row_this_projects_file_saved_off_reads_project_as_a_skill_row_does` (owner: "skills와
  똑같이"); its state line says "Saved off (this project's file) · now off".
  **When a row has a live note, its status word takes precedence over
  the `project` provenance word** (Codex 7, opus 8; amends ADR-0283 D10's order for rows that
  carry one): a profile the project file turned on that is outside the identity's list shows
  `not listed`, and the state line keeps "Saved on (this project's file)". An unsaved edit, a
  failed load and a pending value still come first, as today.
- **Banners**, fixed when the panel opens, at most four rows each (`BANNER_ROWS`):
  1. no `agent` tool this session: `The model has no agent tool in this session; choices still save.`
  2. an identity with a list: `This session runs as 'orchestrator', which may delegate only to: explorer, reviewer.`
     or `This session runs as 'orchestrator', which delegates to no profile (agents: []).`
  3. a saved change of lists the session does not use yet: `The saved list is not the one this
     session uses yet; Enter applies it.` (below; never beside an unreadable file).
  4. the first settings problem, or the unreadable file, as ADR-0279 D11 has them.

  The first draft's "The model cannot start a PROJECT profile" banner is dropped (opus 14): every
  project row already says `/agents run` in its status column and its state line, and fewer
  banners leave more list rows at 80x24. The 80x24 captures record how many list rows remain:
  five with the three banners the implementation round captured, and three (one group header,
  two profiles) with all four, the settings problem wrapping to two rows (fix round 1,
  `.omc/probes/795-796/fix-r1/live/banners-80x24/`).
- **Keys and lifecycle**: ADR-0279 D11, unchanged. ↑/↓, PgUp/PgDn, Home/End, Space toggles (a
  locked row says why), ←/→ chooses "Save to [Global] This project", typing searches, Enter or
  Ctrl+J saves to one file and applies (D3), Esc or Ctrl+C discards. A save that would create
  `.aelix/settings.json` asks first.
- **Grammar**: per item (D1), not D15's whole set. A save edits one row at a time with
  `resource_filter.edit`.
- **After Enter**, the summary's effect line is `The model gets the new agent list from your next
  message; delegations already running keep running.` or, with no `agent` tool this session,
  `The model has no agent tool in this session; the choice applies where it has one.` A saved
  change the session still does not use says why, with the clause above (ADR-0279 D11's #704
  note).
- **Mid-turn**: the panel opens while the model answers (a bare `/agents` already passes
  `mid_turn_refusal`), and a save then applies when the turn ends (D3), a change of lists that
  moves no row included.
- **A saved change of lists that moves no row** (`!rev*` replacing `-reviewer` while only
  `reviewer` exists; a hand edit or another session's save) is shown on a line of its own,
  apart from the rows' pending values and from unsaved edits: `The saved list is not the one
  this session uses yet; Enter applies it.` (`Snapshot.policy_pending`: the lists the snapshot
  read differ from the kind's `applied_policy()`; never with an unreadable file, whose own
  banner shows instead).

### D11. The `/agents` command

- **Bare `/agents`** opens the panel in the TUI (the lead's call; ADR-0279 D12's `/skill` is the
  precedent, and #776 plans the same for a bare `/jobs`). A command context with no panel
  opener (`CommandContext.agent_panel` unset: a host that wires none) lists as it does today.
  `-p`, `--mode json` and RPC dispatch no slash command (D9): in `-p` the text `/agents` is a
  prompt to the model, measured by the round-1 verifier on `c9154b23` (the request reached the
  provider; `.omc/probes/795-796/verify-r1/live/slash-p.err`), and RPC `commands/run` answers
  `not_a_command` (`tests/rpc/test_agents_selection_rpc_795.py`
  `test_commands_run_does_not_dispatch_the_agents_builtin`).
- **The commands read the applied view** through a new `CommandContext.agent_delegation`
  getter (Codex 5, opus 3), wired in `run_tui` from `AgentProfileService.delegation`. They never
  read the settings files: that would show saved-but-unapplied state. With no getter (headless
  tests, a host without the service), every row and note below is left out and the output is
  today's. RPC builds no `CommandContext` — its `commands/run` does not dispatch built-ins — so
  RPC reads the view through `agents/list` (D12).
- **`/agents list`** keeps its table and gains an `ON` column, `on` or `off` from the applied
  lists (the lead's call). Under the table, one dim line when the identity has a list:
  `This session runs as 'orchestrator', which may delegate only to: explorer, reviewer.`
  The diagnostics panel shows D4's unknown-name warnings with the others.
- **`/agents show <name>`** gains two rows: `delegation` (`on`, or `off in /agents: the model
  cannot start it`) and `agents` (`every profile that is on`, `none`, or the names), with a dim
  note on the second: `used while this profile is the session's identity (--agent, /agents use);
  a delegated agent cannot delegate`. ADR-0196's note that `/agents show` must not imply that
  `role` has an effect applies to this row too.
- **`/agents run`** of an off profile or one outside the identity's list runs, with one dim line
  before it: `reviewer is off in /agents, so the model cannot delegate to it; you can still run
  it.` or `orchestrator's agents: does not list reviewer, so the model cannot delegate to it; you
  can still run it.` (this ADR's choice).
- `/agents use` and `--agent` take an off profile as before (the owner's "위임에서만 막기").
- The command's description becomes "Turn agent profiles on or off (Space toggles, Enter saves)",
  the words `/skill` and `/tool` use (opus 15); the subcommands stay in its usage line. `use`
  and `run` stay refused mid-turn (ADR-0292).

### D12. RPC

- `agents/list`: each profile gains `enabled` (the applied lists' answer; `true` where the host
  wired no lists) and `decidedBy` (`"project"`, `"global"` or `null`). Existing keys are
  unchanged. `diagnostics` carries D4's warnings.
- `agents/show`: `profile.agents` appears through `asdict` (`null`, `[]` or names).
- `settings/get` and `settings/set` gain `agent_profiles`, the GLOBAL file's `agents` list,
  through `RESOURCE_LIST_KEYS` like `skill_paths` (ADR-0279 D4): `edit_list("global", "agents",
  …)`, `requiresReload: true`. The session uses it after `resources/reload` (D3) or at the next
  launch.
- `agents/run` and `agents/use` are not filtered (owner decision O2, "적용 안 함 (권장)", for
  `agents/run`; the owner's "위임에서만 막기" for `agents/use`).
- No `delegable` field is added (Limits).

### D13. Launch warnings (ADR-0279 D8, for `agents`)

Once per launch, on stderr, in the main session only (depth 0), after the lists are read and
profiles discovered:

- an entry that is not a non-empty string (`it is ignored`);
- a `!glob` past ADR-0279 D1's limits;
- a plain path entry: `"agents" entry "<x>" would add a profile directory, which is not
  supported; it is ignored`, and, when `<x>` is the name of a found profile, the same line ends
  `; to turn the profile '<x>' on or off, write "+<x>" or "-<x>"` (opus, on O1: the plain form
  means opposite things in the frontmatter and the settings);
- an entry that matches no profile, shadowed ones included (`"agents" entry "<x>" matches no
  agent profile`);
- when the launch identity's `agents:` names a profile that is not found, `agent profile
  'orchestrator': agents: names 'reviewr', which no agent profile found here has`, printed
  `Warning: agent profile 'orchestrator': …` — the profile's own prefix, as `--agent`'s
  `Notice: agent profile:` lines — where every other line here is `Warning: settings: …`
  (`tests/cli/test_agent_lists_795.py`); it is checked with no settings entry too;
- when a settings file that is read (the global file; the project file when trusted) did not
  load (D3): `<path> did not load, so its "agents" list is not applied: every agent profile it
  turns off is on this run (fix the file, then /reload)`.

Printed whether delegation is on or off: the lists also feed `/agents list` and the panel. Not
reprinted on `/reload` (ADR-0279 Limits).

### D14. #624

Owner decision O3, 2026-11-16, "추가 (권장)", recorded in #624's body: agents are among #624's
control-v1 targets. `AgentKind` is a plain `ResourceKind` with D1's per-item grammar, so #624
serves it with the other kinds. Two things this lane does so #624 needs nothing agents-specific
(opus 1):

- **One mode-independent factory.** `core/agent_selection.build_agent_control(...)` builds the
  `AgentKind` and its `ResourceSelection`. `cli/entry.py` calls it once, right after the session
  runtime exists and before the mode branch (`session_host["runtime"] = runtime`, 4684 / 4687),
  not inside the interactive branch where `SkillControl`, `ToolControl` and `ExtensionControl`
  are built today (5001 / 5004 onwards). The TUI receives it; #624 passes the same object to RPC.
- **The live words serialize.** `ItemState.live` and `ItemState.live_note` (a pair of strings,
  or null) are among the fields #624's list response carries, beside the lock and pending state
  its done-conditions require. Every field of an `AgentKind` row's `ItemState` is a string,
  bool, tuple or `None`, and a test runs `json.dumps(dataclasses.asdict(state))` over every row.

This ADR adds no control-v1 method; RPC `settings/set agent_profiles` (D12) is the existing
global-only path until #624 lands.

### D15. Older aelix

Owner decision O4, 2026-11-16, "문서에만 밝힘 (권장)": documented only; #383 measures it.

- An aelix from before this change (beta.2 and earlier) reads a profile's `agents:` as an
  unknown key: a discovery diagnostic, `unknown frontmatter key 'agents' (ignored)`, that
  `/agents list` shows (code reading: `agents/profile.py` 337 at `2f3cdf28`, rendered by
  `tui/commands.py` `_render_agents_diagnostics`), and the profile then delegates to every
  profile. A launch does not print it: `--agent orchestrator -p` on `69e43857`, the commit this
  change starts from, printed only `Agent profile: orchestrator (…)` on stderr (the round-1
  verifier, `.omc/probes/795-796/verify-r1/live/BASE-R2.stderr`).
- The same aelix drops a top-level `agents` settings key on load, because the key is not in its
  settings tables, so nothing is turned off and nothing is said. A later write of another key by
  that aelix keeps the `agents` value on disk, re-serialised (the merge-based persist path and
  `edit_list` write back keys they do not know; ADR-0279 D4, C19). What is kept is the value, not
  the bytes (Codex 11: persistence re-serialises the file, `settings_manager.py` 1345).

Apart from that launch, both are code readings of `2f3cdf28`'s behaviour, and neither is
measured on an installed 0.1.0b2 wheel: that measurement is owed to #383 (the beta.2 → beta.3
upgrade and downgrade check), as the owner's O4 "문서에만 밝힘 (권장)" has it; the contract hands
it the two checks. The guides say both. No mitigation in the old versions is offered.

### D16. Nesting, when it arrives (the lead's rule; written, not implemented)

When #16 / #123 raise `MAX_SUBAGENT_DEPTH` and relax ADR-0199 (g)'s six mechanisms:

- A child's effective set is its own `delegation_view` (its own lists, read as a child reads its
  settings under ADR-0279 D14: the global file, and the project file only when it runs trusted;
  its own `agents:`) intersected with its parent's effective set at the spawn. A child never
  widens what its parent could delegate to, as `narrow_tools` never widens its tools.
- `role: leaf` wins: a leaf child gets no `agent` tool whatever its `agents:` says.
- No warning is added today for `agents:` on a leaf profile: `role` defaults to `leaf`
  (`agents/profile.py`), so it would fire on every profile that sets `agents:` for its use as
  the main session's identity (this ADR's correction of the issue's sketch, which proposed one).

`MAX_SUBAGENT_DEPTH` is not changed here.

### D17. Extensions: the filtered door is public; the Protocol stays unfiltered

Owner decision, 2026-11-16, "문서화 + 필터 API 제공 (권장)"; the owner's words are only those.
What follows is this ADR's design for it, after the lead's reading in the header. The seam gains
an optional member rather than changing `SubagentRuntime`:

- `subagent_contract.ModelDelegation`, a `runtime_checkable` Protocol with one method,
  `resolve_for_model(name: str) -> ResolvedProfile`, and `subagent_contract.DelegationNotAllowed`,
  the refusal it raises for a profile the session's settings or identity rule out. The bundled
  runtime implements it (D5); its `DelegationRefused` is a `ProfileError` and a
  `DelegationNotAllowed`, as `ProjectScopeProfileError` is a `ProfileError` and a
  `ProjectScopeRefused`.
- `SubagentRuntime` keeps its seven members. `bind_subagents`' shape check is unchanged, so a
  third-party runtime that implements only the Protocol still binds, and `CONTRACT_VERSION`
  does not move (an additive, optional member; the seam's compat window is described in
  `_ExtensionRuntime.bind_subagents`' docstring, `extensions/api.py` 622-748, finding B9).
- `resolve_profile` and `spawn` stay unfiltered: they are the human doors (`/agents run`, RPC
  `agents/run`).
- The guide (`docs/guides/extension-authoring.md`) says: a tool the MODEL can call that starts a
  delegated agent must get its profile from `resolve_for_model`, and return its refusal's text
  to the model; a runtime that is not a `ModelDelegation` cannot answer, so such a tool refuses.
  It also says what the method does not do: it is not consent (`spawn` still asks, and does not
  ask without a UI), and it is a policy on aelix's own doors, not a security boundary (Limits).
- A test builds a model-visible extension tool on the public API that calls
  `resolve_for_model` and then the public `spawn`, which reaches the session's channel (a
  recording one in the test): with the profile off, the tool returns the `off` text and `spawn`
  is called zero times and the channel records nothing; with it on, `spawn` is called exactly
  once and the channel records that one child, whose answer the tool returns; and
  `resolve_profile` for the same off profile still returns (the human doors stay open, as the
  lead's reading of the owner's decision has it) (`tests/agents_ext/test_delegation_filter_795_796.py`
  `test_a_model_facing_extension_tool_on_the_filtered_door`).

## Consequences

- The bundled `general-purpose` can be turned off, and "delegate only to `explorer`" can be set
  for good (`"agents": ["-general-purpose"]`) or for one identity (`agents: [explorer]`).
- The model sees only the profiles it may start, plus listed project profiles as before, and a
  refusal says which cure exists and who holds it.
- The roster and every door aelix opens to the model read one function; the gate test keeps a new
  aelix door from skipping it. An extension's model-facing tool reads it when it uses
  `resolve_for_model`, which the guide requires and nothing enforces. The human doors behave as
  they do today.
- A trusted project can turn on a profile the user turned off globally (the owner's storage
  decision, D2).
- In the TUI, a bare `/agents` opens a panel where it printed a list.
- One more settings key reads like `features.agents`; the guides set the two apart.
- When nothing is turned off and no identity has a list, the roster, the texts and the doors
  behave as they do today (pinned by the existing tests that must keep passing), with one
  exception: in a folder that is not trusted, the model door's unknown-name text no longer peeks
  at `.aelix/agents/` and no longer says `--approve` (D7).
- The resume listing a model reads can be shorter than the records (D7).
- With `agents: []` the `agent` tool stays registered with a description that says it delegates
  to nothing, so its contract text (at most `DESCRIPTION_HEAD_MAX_BYTES`, 2 KiB) is still sent on
  every request (Alternatives, A9).

## Limits and what is not covered

- **A policy on aelix's own doors, not a security boundary** (the lead's reading of the owner's
  extension-API decision "문서화 + 필터 API 제공 (권장)", 2026-11-16). The
  `agents` settings list and a profile's `agents:` decide what aelix's `agent` tool offers and
  starts. They do not stop an installed extension that calls `resolve_profile` and `spawn`
  (D17), a host that hands RPC `agents/run` to a model (O2), or a model with `bash` that runs
  `aelix --agent <profile> -p …`, which starts a new process with that identity. Extension
  authors are told to use `resolve_for_model`; nothing enforces it.
- The identity's `agents:` is read when the identity is taken; an edit of that file applies at
  the next `/agents use` or launch.
- A hand edit of the settings, or another session's save, is pending until Enter in the panel or
  `/reload` (D3), as for skills.
- At launch, a settings file that did not load turns nothing off and says so once (D3, D13; the
  lead's call).
- A resume of an agent whose background job is still running, when its profile has been turned
  off, reads the `off` refusal rather than `RESUME_RUNNING`; the job's result still arrives.
- The pre-spawn re-check compares the profile's path, not its bytes: an edit of the approved
  file at the same path is not detected, and the approved object runs (today's rule, unchanged;
  Codex 10).
- **The gate checks the re-check's refusal by its shape only** (D5 (c); the round-2 verifier's
  X1-X3). Each of these passes it: `if not still_the_same and False:`; a constant reassigned
  before the `if` (`still_the_same = True`); a `DelegationRefused` handler that sets
  `still_the_same` to `True`. The door tests catch them: inserted into `extension.py` as product
  mutants, each fails `tests/agents_ext/test_delegation_filter_795_796.py`
  `test_a_profile_file_moved_under_an_off_path_between_hook_and_execute_is_refused`, which runs
  on every platform (3 of 3, that test alone), and the POSIX-only symlink case
  `test_a_path_turned_off_between_hook_and_execute_is_refused_in_the_hooks_words` (3 of 3, the
  round-2 verifier).
- **Forms outside the gate's string matching** (M2; the round-2 verifier's X7-X10):
  `operator.methodcaller("spawn_granted", …)`, `inspect.getattr_static(runtime,
  "resume_granted")`, `vars(discovery)["discover_profiles"]`, and
  `object.__setattr__(pending, "resolved", …)` after the re-check, which swaps the approved
  object without naming a gated symbol. Each passes the real-tree gate
  (`.omc/probes/795-796/fix-r2/gate/real-bypasses-r2.log`); the gate is regression protection,
  not a proof (D5).
- **Data through a container, and computed names** (the round-3 verifier's N1, N2, N5, N6 and N8):
  product-core calling `ext._spawn(ext._pending[id], ctx)` with an approved `PendingSpawn` taken
  from the extension, a constructor bound to another name (`make = PendingSpawn; make(...)`) or
  rebuilt through `type(old)(...)`, `getattr(runtime, '_spawn_' + 'consented')`, and a
  constructor returned as a value. Each passes the real-tree gate
  (`.omc/probes/795-796/verify-r3/gate/bypass-r3.log`).
- Older aelix ignores both keys (D15).
- No control-v1 selection method until #624 (D14), no `delegable` field on RPC `agents/list`, no
  `/agent` alias, no `/settings` row.
- Profiles an extension contributes (#253) are not covered; they would be a new panel group.
- A plain path entry in `agents` does not add a profile directory (D1).
- Discovery per model-door call: one at the hook and one at the re-check, as today (D5 resolves
  from the view's own discovery, so `resolve_for_model` adds none); one per prompt for the roster,
  as today; one more for a resume whose id is missing or unknown (the listing), only while a
  policy is in force. Each now also clears the resolved-path cache. One discovery plus one
  `delegation_view` over 30 user and 2 bundled profiles took 4.35 ms median, 13.8 ms max over
  50 runs (macOS, Python 3.11, while a test run loaded the machine;
  `.omc/probes/795-796/impl/timing.txt`).
- The B5 `project` text still ends "or start aelix with --approve", which does not let the model
  start a project profile either; it is today's text, outside #795/#796, and a follow-up
  candidate.
- D16 is written, not implemented.
- The guide's "older aelix" paragraph (D15) is a code reading of `2f3cdf28`, not measured on a
  0.1.0b2 wheel; #383 owns that measurement. (Measured since, except the `/agents list`
  diagnostic: Note (2026-11-25, #383).)

## How it is held

Measured 2026-11-17 on the lane's branch over `69e43857`, on macOS (Darwin 25.6), Python 3.12
unless a row says otherwise. "Fails on `69e43857`" means the test file was copied into a
worktree of that commit with its own venv and run there.

| Decision | Held by | On `69e43857` |
|---|---|---|
| D4 parse rules, three values, globs, reserved-looking names | `tests/agents/test_profile_agents_key_796.py` (36) | 30 fail (`agents` is an unknown key there); the 6 reserved-name rows pass (they pin today's behaviour) |
| D4 unknown-name warning, D10 shadowed losers, D5 `complete_profile`, D10 untrusted count | `tests/agents/test_profile_discovery_795.py` (9) | collection fails: `complete_profile` does not exist |
| D1, D2, D3, D5, D13 lists, the verdict order, the property, the symlink cache, the settings key, a lists-only busy apply, `AgentKind.apply` | `tests/core/test_agent_selection_795.py` (38) | collection fails: `ImportError` for `ShadowedProfile` from `agents/discovery.py` (its first import that base lacks) |
| D5-D8, D17 every model door, the roster and its empty sentences, no peek, one discovery, the re-check, resume and its listing; continuation of a batch and a chain whose later members are queued (one permit) while a REAL panel save turns the profile off, deferred until the turn ends; a background job and a Ctrl+B-moved run whose profile is turned off; a background spawn refused for all four verdicts; the extension door through the public `spawn` (zero calls refused, one accepted); the texts with nothing available or nothing discovered; the path refusal; the listing's one view; the re-check refusing a profile file moved under an off path, on every platform | `tests/agents_ext/test_delegation_filter_795_796.py` (57) | collection fails: `DelegationRefused` does not exist |
| D5's gate: 24 exact sets (B1-B4, D1-D3, D5, D7, D8 and E4 across both packages), M1-M4 with import aliases, F1-F5, 61 synthetic controls with at least one for every rule and 3 that tell F3's clauses apart by their words | `tests/agents/test_delegation_filter_gate_795.py` with `tests/agents/delegation_gate.py` (68) | the real-tree test fails with 13 violations (A1, A3, B1, B4, C1-C3, E1, E2, E4, F1 ×2, F3); the 67 checker self-tests pass there by construction (the checker is the test's own file) |
| D3, D9, D13, D14 entry wiring in `-p`, json, RPC and the TUI; `--agent` and `/agents use` taking an off profile; the identity's unknown-name warning with no settings entry and its prefix; a settings problem's `settings:` prefix; the identity getter before the service exists | `tests/cli/test_agent_lists_795.py` (17) | collection fails: `_identity_of` does not exist |
| D12 RPC | `tests/rpc/test_agents_selection_rpc_795.py` (6) | 4 fail; `agents/run` not filtered and `commands/run` `not_a_command` pass (premise pins) |
| D10, D11 the panel and `/agents`; a lists-only pending change and its Enter; a project-decided saved-off row read against a skill row; a save during a turn; the service's own view; `agents: []` on every surface; an Enter that both moves a row and applies the lists; the four banners' order and the third's yellow, read from the terminal's cells | `tests/tui/test_agents_panel_795.py` (26) | collection fails: `ModuleNotFoundError` for `aelix_coding_agent.core.agent_selection` |

The eight files hold 257 tests (234 at fix round 1's head `f08cf72a`, 185 at the implementation
round's head `c9154b23`). Fix round 1
ran each file above in a `69e43857` worktree with its own venv and saw the failures in the last
column (`.omc/probes/795-796/fix-r1/base-run.txt`), and ran its new and changed tests in a
`c9154b23` worktree. As written, `tests/tui/test_agents_panel_795.py` fails at collection there:
`POLICY_PENDING` does not exist at `c9154b23`. With that one import stubbed (the constant inlined
into the copy, so the behaviour is measured rather than the import), 7 tests fail: the 80-column
clauses, the lists-only pending line and the warning prefix (`head0-run.txt`). The round's 11 new
gate controls for C-1, C-2, C-5 and
`attrgetter` by alias fail against the `c9154b23` checker
(`gate-new-controls-on-old-checker.txt`).

At `c9154b23`, every test file the change touched (34: the eight above and 26 whose citations
moved) passed whole on Python 3.11.15, 3.12 and 3.14.5 on macOS (1 677 passed, 8 skipped
Linux-only) and in Linux docker (`python:3.12-slim`, 1 685 passed). Fix round 1 re-ran the 23
test files it touched or whose imports it changed (the eight above and every file importing
`selection_panel`, `resource_selection` or `agent_selection`): 706 passed on each of Python
3.11.15, 3.12.13 and 3.14.5 on macOS with `-W error::pytest.PytestUnraisableExceptionWarning`,
and 704 passed, 2 skipped (case-insensitive-volume tests) in Linux docker (`python:3.12-slim`,
a non-root user); `scripts/preflight.py --base 69e43857` passed every gate it picks, the
touched-tests gate over 350 files in 598 s included (`.omc/probes/795-796/fix-r1/runs/`). `scripts/preflight.py --base 69e43857` ran every
gate it picks plus the 11 405 tests of the files that import what changed; its four failures
(a `run_tui` double that did not take `agent_control`, the `/agents` description wrapping
`/help` at 80 columns, and a test runtime whose resolve returned a bare object) were fixed and
those files re-run green. The full suite is not run here.

**Mutants** (`.agents/skills/aelix-tests-that-bite/scripts/mutants.py`, a worktree copy with its
own venv, `PYTHONDONTWRITEBYTECODE=1`, restored by sha256): 24 product mutants on the changed
lines, every one killed by the eight files above after two tests were added for the two first
survivors (the resume listing hid an unknown profile's agent; `_agent_lists` read lists at
depth 1). A first-draft mutant of the verdict order was equivalent (it changed nothing) and was
rewritten. Five gate bypasses (G1, G3, G5, G6,
G10) inserted into the real `extension.py` copy: 5/5 fail the real-tree gate test. Logs:
`.omc/probes/795-796/impl/mp*-log.json`, `mg-log.json`.

Fix round 1 re-ran the round-1 verifier's 109 mutants of the changed product lines (two
re-targeted where the line moved) and seven of its own, against the eight files above, in a
worktree copy with its own venv: 114 of 116 killed. The two that survive are V81 — reading the
project file's load error when the project is untrusted, which the settings manager never
loads, so there is no error to read (equivalent) — and V104 — `/agents list` discovering again
instead of reusing the view's result, the same disk state read twice (cost only). Logs:
`.omc/probes/795-796/fix-r1/mut/`. Twelve bypasses inserted into the real tree — the verifier's
six and Codex's six (product-core `spawn_granted`, `resume_granted` and `_run`; a re-check
skipped by `or` and by a conditional expression; `replace` under an import alias) — each fail
the real tree's check; the `c9154b23` checker missed Codex's six
(`gate-real-bypasses.log`, `gate-real-bypasses-old-checker.log`).

Fix round 2 (the round-2 verifier's review of `f08cf72a`; the branch was then folded and rebased
onto `bf507d59`, which carries #777 and #772; evidence in `.omc/probes/795-796/fix-r2/`):

- Contract §9's gate probe gives the same sets on `69e43857` and `bf507d59`, so neither merge
  moved a sink or a door (`probe/`).
- The verifier's ten real-tree bypasses and its four product-core cases were inserted into the
  tree in memory and checked as the real-tree test checks it. X5 (`_spawn_consented` called from
  `_control`), X6 and the four product-core variants now fail the gate (D8, B2, B3, B4). X1-X3
  and X7-X10 still pass it, as Limits says. X4 (the roster built from every profile rather than
  the available ones) also passes it; the door tests catch it (the round-2 verifier)
  (`gate/real-bypasses-r2.log`). Fix round 1's twelve,
  re-run on the same tree, still fail it, 12/12 (`gate/fix-r1-real-bypasses-on-new-gate.log`).
- The verifier's 52 checker mutants, each run against every self-test: 52 killed, where 45 were
  before this round's controls. This round's own 15 mutants are B4 and D8 dropped, and B2-B4,
  D1-D3, D5, D7 and D8 narrowed to `aelix_agents`, with and without their sets rewritten: 15
  killed (`gate/selftest-mut-r2.log`).
- Run against the `08122b78` checker, the 7 new X5 and X6 controls fail. The other new controls
  and tests pass at `08122b78`, whose code already behaved so. Each kills its named mutant in a
  `git archive` copy with its own venv (`mut/`; the mutant names are the round-2 verifier's, not
  the gate's rules):
  - X1-X3, a refusal that cannot fire:
    `test_a_profile_file_moved_under_an_off_path_between_hook_and_execute_is_refused` alone, 3/3;
  - mutants D8 (the pending line drawn dim) and D9 (that line drawn first):
    `test_the_four_banners_keep_their_order_and_the_pending_line_is_yellow`;
  - mutant D10 ("no row changed" after an Enter that moved one):
    `test_an_enter_that_moves_a_row_and_applies_the_lists_does_not_say_no_row_changed`;
  - mutant D17 (`_warning_kind` returning `""`):
    `test_the_launch_warnings_print_once_at_depth_zero`.
- The eight files above (257 tests, the four this round touched among them) pass with
  `-W error::pytest.PytestUnraisableExceptionWarning` on Python 3.11.15, 3.12.13 and 3.14.5 on
  macOS, and in Linux docker (`python:3.12-slim`, aarch64, a non-root user): 257 passed on each
  (`py31*.log`, `docker/`). This round changed no product code.

**Live runs** (`openai/gpt-4o-mini` on OpenRouter pinned to the `openai` upstream with
`allow_fallbacks: false` in a scratch `models.json`; scratch HOME, agent dir and cwd;
`--thinking off`; evidence in `.omc/probes/795-796/impl/live/`):

| Run | Setup | What the model's tool call returned |
|---|---|---|
| R0 | nothing off | the child ran: `PONG` |
| R1 | global `"agents": ["-general-purpose"]` | `Delegation refused: agent profile 'general-purpose' is turned off in the settings, and only the user can turn it on (with /agents); available: explorer, orchestrator, solo` |
| R2 | `--agent orchestrator` (`agents: [explorer]`), asked for `general-purpose` | the `not_listed` text, `available: explorer`; the control asking for `explorer` ran |
| R3 | `--agent solo` (`agents: []`) | the `none_listed` text; the tool description's last paragraph, read in the live process, was the `none_listed` empty sentence (the model declined to quote it) |
| R4 | delegate to `explorer`, then `"-explorer"`, same `--session` | resume by id: the resume `off` text; resume with no id: `This conversation has no delegated agent it can resume.` and the count line for 1; with `explorer` back on, the resume ran |
| R5 | R1's setup, `--mode rpc` | `agents/list`: `general-purpose` `enabled: false`, `decidedBy: "global"`; `settings/get` `agent_profiles` `["-general-purpose"]`, `string_array`, `requiresReload: true` |
| R6 | R1's setup, an `-e` extension tool on `resolve_for_model` | `general-purpose`: the `off` text and no child record in the session; `explorer`: the child ran |

**TUI** (`.agents/skills/aelix-live-check/scripts/tui_capture.py`, 120x40 and 80x24, captures in
`.omc/probes/795-796/impl/captures/`): the panel's three groups, `not listed` / `/agents run` /
`project` / `shadowed` / `failed` words, a project-decided `not listed` row whose state line keeps
"Saved on (this project's file)", the failed row's `see /agents list · …` state line, the
identity banner, banner 1 under `--no-agents`, the untrusted summary row with "This project"
refused, Space + Enter writing `-general-purpose` with the effect line, Esc's "1 unsaved change
discarded.", `/agents list` with `ON` and the identity line, `/agents show` with the two rows,
`/agents run` of an off profile running after its dim note (SGR `2m` measured in the raw bytes),
and a refused model call in the transcript. At 80x24 with all three banners, five list rows
remain (two group headers, three profiles); with one banner, seven.

Fix round 1 (captures in `.omc/probes/795-796/fix-r1/live/`, 120x40 and 80x24, scratch HOME,
agent dir and cwd, no model): a project file saving `reviewer` off reads `project` with "Saved
off (this project's file) · now off" in `/agents`, and a skill the same file saves off reads the
same in `/skill`; "Saved on (this project's file) · now off (not listed by 'orchestrator')" is
whole at 80 columns; a hand edit of the global file from `-reviewer` to `!rev*` while aelix runs
shows "The saved list is not the one this session uses yet; Enter applies it." (yellow), Enter
leaves "✓ Agents: nothing to save" and "The saved list is in use now; no row changed.", and the
panel reopened shows no such line.

## Where the build differs from the design draft

- **The property (D5).** The draft said a name is in `available` exactly when its verdict is
  `ok` or `project`; with its own verdict order an off project profile reads `project` and is
  hidden, so the property is `ok`, or `project` and on.
- **The resume listing (D7).** The draft left out every agent whose verdict is not `ok`. That
  also hid agents of a deleted or project profile and changed the listing with no policy in
  force, which broke `tests/agents_ext/test_agent_resume_610.py`
  `test_a_missing_or_unknown_id_is_refused_with_the_listing` and made a host that wired no agent
  directory list the user's agents folder. Only the three policy verdicts are left out, and with
  no policy in force no discovery is taken.
- **The failed row's lock kind** is `status` (the contract named `unavailable`), so its state
  line reads as D10 words it.
- **A live note shows while the row is saved on**, as D10's "a row whose saved value is on" says.
- **Text placement.** The run refusals, `<available>` and the empty sentences are in
  `aelix_agents/tool.py`, the resume texts in `child_session.py`; `runtime.resolve_for_model`
  imports the first two inside the method, because `tool.py` imports `runtime.py`.
- **`build_agent_control`** takes the two settings bases from `cli/entry.py`; `AgentKind` reads
  whether `agent` is an active tool from the harness itself.
- **The gate's C3 set** names `runtime.py::_SubagentRuntimeImpl._listed_predicate.listed`, the
  nested predicate the listing calls, where the contract wrote `::find_resume_target`.
- **Where the extension guide lives.** The section D17 describes is
  `docs/guides/agent-profiles.md` "Extension tools the model calls", with a one-line pointer from
  `extension-authoring.md`: that guide was 131 bytes under the 51 200-byte read cap
  `tests/cli/test_docs_signpost.py` `test_every_bundled_guide_fits_in_one_read` holds, and the
  section would have pushed it over.
- **The `/agents` description** drops the draft's "; list, show, switch or delegate": at 90
  characters it wrapped in `/help` at 80 columns, which
  `tests/tui/test_commands.py` `test_help_renderable_lists_every_command` and
  `tests/tui/test_resource_completion_508.py` `test_help_names_each_alias_beside_its_command`
  refuse; it is now `/skill`'s and `/tool`'s shape.
- **The settings field count** is 48, not 47: there were already 47 fields at `69e43857`
  (the comment said 46).

From fix round 1 (the verifier's and Codex's reviews of `c9154b23`):

- **The gate's start sinks cover both packages.** The contract scoped D1-D7 to `aelix_agents`;
  `spawn_granted`, `resume_granted`, `run_batch`, `_run` and `_execute_pending` are now exact
  sets across both packages (product-core loads none). F3 takes the re-check only as a whole
  right-hand side; B1's copy forms are matched through import aliases and include
  `copy.deepcopy`.
- **A saved-off row this project's file decided reads `project`**, as a skill row in that state
  does (measured; D10), where the draft said it reads `off`.
- **The "not in use now" clauses are shorter** (`not listed by 'orchestrator'`, `only /agents run
  starts a project profile`), so the state line keeps its reason at 80 columns.
- **A change of lists that moves no row has a line of its own** in the panel, and Enter's
  summary says the list is in use (Codex 6 r1); the contract had the busy apply defer it but
  showed nothing.
- **The identity's unknown-name launch warning keeps the profile's prefix**
  (`Warning: agent profile …`), where the implementation round printed it among the
  `Warning: settings:` lines.

From fix round 2 (the round-2 verifier's review of `f08cf72a`):

- **The gate's identity objects and `/agents run`'s inner launch cover both packages.**
  `PendingSpawn(` and `_Launch(` (B2, B3) were inventoried in `aelix_agents` only, and the
  constructor helper `_resolved` and `_spawn_consented` not at all, so the verifier's X5 and X6
  passed the gate. B2 and B3 now hold across both packages, and `_resolved` (B4) and
  `_spawn_consented` (D8) are exact sets there. D5 (a) was made exact for each of these four
  forms rather than narrowed: the real tree passes the new sets with no false positive.
- **D5 (c) is narrowed to the refusal's shape**, which is what the gate checks. Limits names the
  three forms that pass it, and an every-platform door test (a profile file moved under an off
  path) holds the behaviour where the symlink case is skipped.

## Alternatives considered

- **A1. Hide off profiles from the roster only.** Rejected by the owner ("위임에서만 막기", which
  the lead reads as hidden AND refused by name).
- **A2. An off profile is gone for humans too** (`/agents use`, `--agent`, `/agents run` refuse
  it). Rejected by the owner (the same decision).
- **A3. Global-only storage, or a project file that may only turn profiles off.** Rejected by the
  owner ("skills와 똑같이").
- **A4. Filter inside `resolve_profile`.** Rejected: it is the `SubagentRuntime` Protocol method
  the human doors and any P4 runtime call; filtering there blocks `/agents run`, against the
  owner's decision, and puts product policy in the Protocol.
- **A5. Read the settings files fresh at every door.** Rejected: the roster is stamped once per
  prompt, so a door reading a newer file than its roster would refuse a listed profile mid-prompt;
  ADR-0279 D5/D6 keep saved and applied apart for this reason.
- **A6. Key `agentProfiles`.** Rejected by the owner (O1, "`agents` (권장)"). The opus review
  recommended it for the two other `agents` keys in the same file; D13's plain-entry hint and the
  guides carry that concern instead.
- **A7. ADR-0279 D15's set-level grammar.** Rejected: there is no default set to replace (D1).
- **A8. Globs in frontmatter `agents:`.** Not done: the lead reads the owner's allowlist decision
  ("`agents:` 키, 없으면 전부 (권장)") as names only, no globs (D4).
- **A9. Remove the `agent` tool when `agents: []`.** Not done: it needs a `profile` lock in
  ADR-0283's `ToolPolicy` and the `/agents use` rollback to restore it; `wait` / `status` /
  `stop` / `result` on jobs started under an earlier identity need the tool; and leaving `agent`
  out of `tools:` already does it for a profile that wants no tool at all.
- **A10. An unknown name in `agents:` is an error.** Rejected: a profile can be created later, and
  an error would make the identity fail to launch.
- **A11. Stop running delegations and jobs of a profile turned off, or re-check queued batch
  members.** Rejected (D8).
- **A12. #776's jobs widget for the panel.** Rejected by the lead.
- **A13. Show the model-door verdict instead of `on`/`off` in `/agents list`.** Not done: the
  column answers what the panel edits; the identity line under the table carries the rest.
- **A14. Filter the public `resolve_profile` / `spawn` for extensions by default** (the Codex
  review's fix for its finding 1). Rejected by the owner ("문서화 + 필터 API 제공 (권장)"); in the
  lead's reading they stay the human doors, and D17 adds the filtered one.
- **A15. Fail closed when a settings file did not load at launch.** Not recommended (D3): it
  needs a verdict and texts of its own, departs from ADR-0279's launch rule the owner's storage
  decision follows, and refuses every model delegation for a typo D13 already names. The lead
  may choose it instead.
- **A16. Annotate, rather than leave out, the agents the resume listing cannot resume.** Not
  done: the listing hides what the model may not start (D7, this ADR's choice, after the lead's
  reading of "위임에서만 막기"), and an annotated row still takes one of the ten places.
- **A17. Drop a malformed `agents:` entry with a warning instead of failing the profile.** Not
  done (D4).
- **A18. Keep the untrusted peek at the model door, or move it to a stat of the folder.** Not done
  (D7): the advice it produced cannot help the model, and the human doors keep it.

## Note (2026-11-18, ADR-0305, #624)

D14 is done. The `agent_control` built for every mode is served to RPC as kind `agent`, the same
object the TUI's `/agents` panel gets: `cli/entry.py`'s `_selection_controls` takes it and
`SelectionControls.by_kind()` lists it when it is not `None` (ADR-0305 D1, D10). Its rows carry
`live` and `liveNote` on the wire (ADR-0305 D3; the serializer's `liveNote` is pinned since
ADR-0305's fix round 1 by `test_a_started_server_that_is_not_connected_carries_its_live_note`,
`tests/rpc/test_selections_rows_624.py`, on `mcp` rows). `tests/rpc/test_selections_same_bytes_624.py`
holds that the panel and `selections/set` kind `agent` write the same bytes with the same
effects, and `test_capabilities_lists_no_agent_kind_without_an_agent_service` that a host with
no agent profile service lists no `agent` kind.

## Note (2026-11-25, #383)

D15 is measured on an installed 0.1.0b2, except the `/agents list` diagnostic. #383's official run installed 0.1.0b2, the Release's wheels, with the 0.1.0
install line and `AELIX_VERSION=v0.1.0-beta.2`. It ran after the 0.1.0 tree (`d9aa80f3`, the tree
of `v0.1.0`) had written the files, on macOS and in Linux docker, twice each (runs 3 and 4),
against a local stub model. The step names are the run's
([RELEASING.md](../../RELEASING.md#upgrading-and-downgrading-between-releases)).

- **The `agents:` frontmatter (D6).** It was tested with two profiles, `agents: [helper383]` and
  `agents: []`.
  - `-p --agent lead-some` and `-p --agent lead-none` exit 0.
  - The `agent` tool offers the full roster, and the `general-purpose` child runs.
  - stderr holds only `Agent profile: lead-some (user: …)`. As D15 says of a launch, no
    `unknown frontmatter key` line is printed on `-p`.
  - The profile files are byte-identical after the runs.
  - The `/agents list` diagnostic was not run; it stays a code reading.
- **A global `agents: ["-explorer"]` (D4).** 0.1.0b2 runs a turn, still offers `explorer`, and
  leaves `settings.json` as written.
- **0.1.0b2's own settings writer (D5).** When it writes another key, it keeps `agents` and
  `completedThinkingDisplay` with their values, and `features.agents` stays its own key. The check
  compares values, not bytes, as D15 says.

Apart from the `/agents list` diagnostic, what D15 read from `2f3cdf28`'s code holds on the
installed wheel. No mitigation in the old
versions is offered, as before.
