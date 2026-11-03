# 0281. Skills are also read from `~/.agents/skills` and, in a trusted project, from every `.agents/skills` up to the git root

Status: Accepted (2026-10-31, #509). D1–D10 are decided and implemented; the
choices the issue recommended (D1, D2, D8) were settled by the main loop under
the owner's beta.3 scope decision (2026-10-26). Review round 1 (2026-11-01, a
Codex cross-review and an independent verify of the first commit) amended D3–D7
and D9 and added D10; each change names its finding. Its verify (round 2,
2026-11-01) corrected D10 and Limits — what the write rule does not see through
a symlink, and how long a cwd fills the question — and made each load warning
print once (D4). **Amended 2026-11-03 (#646, owner decision): D10 now also
refuses a write into the folder BEHIND a link — the resolved form of every
folder or file the loader reads, not only the names — which closes the Limits
item it left open as far as D10's dated note says; its review round 1 judged
the file the tools open (`@`, `..` after a link), held a child in a subfolder
to the parent's folders, followed the links inside a root, added the agent
profiles' paths and `$AELIX_MCP_CONFIG`, and bounded what one decision costs;
its round 2 bounded the project profiles' YAML, made nesting past a parser
ask, judged every link on Windows before following it and followed `bin`;
see the dated notes in D10 and Limits.**
Date: 2026-10-31
Issue: #509. Live runs, pty grids and drivers were kept locally under
`/tmp/509-impl/`, not in the repo.
Amends: ADR-0252 §5 (pi's `.agents/skills` ancestor walk is now in aelix's
gated set), ADR-0279 D9 (the order inside a tier) and D11 (the panel's groups:
one per source directory, PROJECT included, each drawn once). Each carries a
dated note.
Relates: ADR-0235 (pi is the reference, not a parity mandate), ADR-0149 (the
trust model), ADR-0203 (`HOME` is on `_DOTENV_LOCKED`), #115 (the hole this
must not reopen), #179 (flattening), #511 (the settings `skills` paths), #510
(`/tool`, which builds on `_resolve_skill_tiers`), #277 (docs).
pi: `earendil-works/pi` `origin/main` `44d9fb5f6` (2026-10-31) —
`packages/coding-agent/src/core/package-manager.ts` (`collectSkillEntries`
`:394-471`, `findGitRepoRoot` `:477-489`, `collectAncestorAgentsSkillDirs`
`:491-510`, `addAutoDiscoveredResources` `:2548-2712`, `resourcePrecedenceRank`
`:215-221`, `toResolvedPaths` `:2772-2798`), `src/core/trust-manager.ts`
(`hasTrustRequiringProjectResources` `:186-215`), `src/core/skills.ts`
(`loadSkillFromFile`'s `isDeclaredSkill` `:282-306`), `docs/skills.md:61`,
`CHANGELOG.md` `:3620` (the feature), `:3422` (#1915), `:3058` (#2603), `:884`,
`:41` (#10488).

## Context

On `0e97bd28` `cli/entry.py` `_resolve_skill_tiers` read four places: `--skill`
paths, `<cwd>/.aelix/skills` (trusted only), `~/.aelix/agent/skills` and the
bundled directory. `~/.agents/skills` and `.agents/skills` — the Agent Skills
locations pi and other agents use — were not read, so a user who wrote skills
for another agent had none in aelix. ADR-0252 §5 recorded the gap ("pi's
`.agents/skills` ancestor walk … still not in aelix's set (no loaders)").

pi (`addAutoDiscoveredResources`) reads, in this order: `.pi/skills` and then
`<dir>/.agents/skills` from the cwd up to the git root (or the filesystem root
outside a repository), nearest first — both only when the project is trusted —
then `~/.pi/agent/skills` and `~/.agents/skills`, ungated. A stable sort by
`resourcePrecedenceRank` keeps that order inside each scope, and name
collisions keep the first. Its trust check (`hasTrustRequiringProjectResources`)
counts `.agents/skills` in the cwd **and every ancestor**, not stopping at the git
root, and skips the user's own `~/.agents/skills` (`pathsEqual`). pi read
`~/.claude/skills` once (`enableClaudeUser`, `70440f759`) and dropped it with its
resource loader (`b846a4bfc`).

Measured on `0e97bd28` with the real CLI, `-p` against OpenRouter
`anthropic/claude-haiku-4.5`, a scratch `HOME` and cwd, and a `-e` extension
that records the `before_provider_payload` body (no headers): a git repository
with `.agents/skills` at its root and at `pkg/`, cwd `pkg/app`, a same-name skill
in `pkg/app/.aelix/skills`, and `~/.agents/skills` in the scratch home.
`<available_skills>` held `shared-name` (the `.aelix` copy) and the two bundled
skills with `--approve`; the bundled two without. In a folder under the home
directory outside a repository: the bundled two. A repository whose only
skills were in its root's `.agents/skills`: the bundled two, trusted or not, and
no question asked.

## Decision

### D1. `~/.agents/skills` is a GLOBAL source, ungated

It is read right after `~/.aelix/agent/skills`, at the same rank, never gated
(it is the user's own directory). It is the home directory's, not the agent
directory's: `AELIX_CODING_AGENT_DIR` moves aelix's files, not another tool's. A
repository `.env` cannot move it (`HOME` is on `_DOTENV_LOCKED`, ADR-0203).
`core/skill_dirs.py` `user_agents_skills_dir`.

### D2. `.agents/skills` from the cwd up to the git root is a PROJECT source, trusted only

`project_agents_skill_dirs(cwd)` (pi `collectAncestorAgentsSkillDirs`): for the
cwd and each parent, `<dir>/.agents/skills` when it is a directory, nearest
first, stopping after the nearest directory that holds anything named `.git`
(a worktree's or submodule's `.git` file counts, as pi's `existsSync`), or at
the filesystem root outside a repository. The walk is over the cwd's own
spelling; the launch passes `Path.cwd()`, which is the physical path, so its
parents are real directories. A symlinked `.agents` or `.agents/skills` is
followed (it is in the project, so it is gated). Nothing raises: a directory
that cannot be checked is not one.

### D3. Trust covers exactly what the scan reads

`has_trust_requiring_project_resources` gains one clause: a directory from
`trust_requiring_agents_skill_dirs(cwd)` — the same walk as D2, restricted to
directories whose listing shows something the loader could read: a `SKILL.md`,
or a subdirectory that is not hidden and not `node_modules` (`skipped_entry`,
the loader's own rule). The first commit took any entry, as every other
directory family does, so a folder holding only a `README.md` asked and loaded
nothing when trusted (verify V5); its other files are never skills there (D5).
No file is opened and nothing below the first level is listed, so an empty
subfolder still asks — the safe side: what this misses would load unasked.
One function serves the scan and the gate, so
neither can grow without the other: everything the scan can load from a
`.agents/skills` makes the folder ask, and the gate asks about nothing the scan
does not read. Without the clause (sabotage M1, Tests), a repository whose only
skills sit in an ancestor's `.agents/skills` would be trusted at step 2 and its
`SKILL.md` text would reach the system prompt unasked — the hole #115 closed for
`.aelix/skills`.

**Divergence from pi, knowingly (ADR-0235).** pi's trust check walks every
ancestor while its scan stops at the git root, so a repository under a folder
that has a `.agents/skills` asks about skills it will never load, and a "Trust"
answer there consents to nothing. Here the gate is the scan's own list.

**The user's own directory is never a project directory.** A folder under the
home directory outside a repository walks up to the home directory and meets
`~/.agents/skills`. It is left out of the walk wherever it is met — compared by
file identity (`skill_dirs.path_key`: `os.stat`'s device and inode, the
`normcase(realpath)` string where there is none), so a project's symlink to it
is the user's directory too — and so it is read once, as GLOBAL, and never
makes a folder ask. The first commit compared `normcase(realpath(…))`, which on
macOS is no key: `normcase` changes nothing on darwin and `realpath` keeps the
case it is given, so a `HOME` spelled `…/Home` for `…/home` on the default
case-insensitive volume, or a project's symlink so spelled, made it a project
directory that asked, was read twice, and won as PROJECT (review F3, verify V3). pi does the same with `pathsEqual` (#1915: before that fix pi
classified it as project-scoped in non-git folders under `$HOME`; #10488: a
case-only difference on Windows read it twice). A directory ABOVE the home
directory (`/Users/.agents/skills`) is not the user's and is walked like any
other.

**An unreadable `.agents/skills`** (no read permission) has no entry the gate can
see, so it does not ask; the scan cannot list it either and loads nothing from
it (a trusted launch warns `Permission denied` for it, `list_failed` and three
`file_info_failed` for its ignore files, as for an unreadable `.aelix/skills`).

### D4. Order inside a tier is pi's; D9's tiers are unchanged

| Tier | Directories, first wins |
| --- | --- |
| this run | `--skill`, an agent profile's `skills:` |
| project (trusted) | `<cwd>/.aelix/skills`, `<cwd>/.agents/skills`, each parent's `.agents/skills` nearest first up to the git root, then the project file's `skills` paths |
| global | `~/.aelix/agent/skills`, `~/.agents/skills`, then the global file's `skills` paths |
| bundled | `writing-skills`, `extending-aelix` |

pi's order inside a scope: `.pi/skills` before `.agents` (inserted first,
`addAutoDiscoveredResources`, kept by the stable `resourcePrecedenceRank` sort)
and a nearer `.agents/skills` before a farther one (`collectAncestorAgentsSkillDirs`
pushes the cwd first). It does not contradict D9: a project directory still
beats every global one. Unchanged from #511 and recorded here: aelix puts a
settings file's `skills` paths after the auto-discovered directories of their
tier, where pi ranks them first (`resourcePrecedenceRank` 0 vs 1).

The losers stay `SkillCatalog.shadowed` (ADR-0279 D9), and the origin of each
skill is still `SkillCatalog.origins` (by file path); `SkillSource` gains
`agents: bool`. `Skill` gains no field (`tests/pi_parity` pins it).

A directory is scanned once per rule, not once (`load_skill_catalog` keys a scan
by `(path_key(directory), agents)`). A settings `skills` path or `--skill`
naming an Agent Skills directory reads it by aelix's rule (root `.md` files are
skills), and the walk reads it by pi's (grouped `.md` files are); keyed by the
directory alone, whichever came first erased what only the other finds — a
settings `"~/.agents/skills"` lost its root `custom.md`, `--skill .agents/skills`
lost the grouped files (review F2). A file both find is taken once, by the
first (`path_key` of the file). So is each warning: both rules read a
`SKILL.md` alike, so the second scan repeated the first one's diagnostics, and
every `Warning: skill load:` line was printed twice at launch, in `-p` and on
the TUI's stderr (round 2, R1); the catalog keeps one per `(code, path,
message)`.

### D5. An Agent Skills directory is scanned by pi's rule for it

`load_skills(dirs, agents=True)` (pi `collectSkillEntries` mode `"agents"`,
`5e11f6586`): the directory's own root `.md` files are not skills — the
directory is shared with other agents, and a `README.md` there would otherwise
be a warning at every launch (pi #2603) — and a `.md` file in a grouping folder
below it (one with no `SKILL.md`) is a skill when its frontmatter has a
description; otherwise it is skipped without a diagnostic and its name is not
checked against the grouping folder (pi `loadSkillFromFile`, `isDeclaredSkill`).
`SKILL.md` discovery, ignore files and hidden entries are as before. aelix's own
directories keep their rule (root `.md` files are skills).

A file that is not UTF-8 no longer stops the load (review F4). The read was
strict and caught only `OSError`, so one such grouped `.md` — documentation,
declaring nothing — raised `UnicodeDecodeError` out of `load_skills` and lost
every skill of the launch; in `~/.agents/skills`, which other tools write, at
every launch. pi reads with Node's lenient decoder and never fails there. Now
a grouped `.md` that declares no skill even read leniently is skipped without a
word (pi skips it); a skill — a `SKILL.md`, a root `.md`, a grouped `.md` with a
description — is skipped with one `read_failed` diagnostic ("not UTF-8 text …;
skipped") rather than loaded with its bytes replaced; an ignore file is read as
Node reads it (U+FFFD). The skill half holds in aelix's own directories too,
where a non-UTF-8 `SKILL.md` aborted the load before #509.

### D6. The trust prompt and `/trust` name the directories

The start-up question keeps its #380 paragraph (still its fourth line) and adds
one sentence, always: "Trusting also loads the skills in .agents/skills here and
in the folders above, up to the repository root." ("… and in every folder
above." outside a repository) — a later `/reload` reads one that arrives with a
`git pull` (ADR-0216), so the consent names the family even where none exists
yet. When directories make the folder ask, the sentence ends "; found in
../.agents/skills and ../../.agents/skills." `/trust` adds a fifth row, "Skills
found in: ../.agents/skills and ../../.agents/skills".

The directories are named **relative to the cwd** that both surfaces print just
above (`agents_skill_dirs_phrase`), nearest first, at most three and then "and N
more (/skill lists them)". The first version listed each by full path, one row
each; measured in a pty at 80x24, the start-up question — 17 rows without them,
and not scrolled — then lost its options below the screen's bottom edge under a
long cwd and two directories (`/tmp/509-impl/tui/grids-evil-80x24-v1-full-paths`;
with the relative phrase, `grids-evil-80x24`, they stay on screen). Since every
directory is the cwd or a parent, the phrase holds `..` and `.agents/skills`
only — no folder name the repository chose reaches the question.

A relative path is bounded too: more than three levels up the `..` are counted
(`skill_dirs.parent_relative`: `../../../.agents/skills`, then
`../×4/.agents/skills`). The first commit wrote them out and claimed they stayed
short however deep the cwd; 65 short levels made the sentence five rows and
moved the last option of the 80x24 question to row 25, off the screen (review
F5). With the same row cutter (`_one_shot_rows`) after the change, the sentence
no longer grows with depth (two rows for one directory at 80 columns), and the
question is 22 rows at 65 levels; what still grows is the cwd line and the
parent option (Limits).

It is flattened anyway (`project_trust._one_row`: every character a terminal
would act on drawn by its name, `^[`, `^J`, `<U+202E>` — the panel's `flat`,
#179, without its TUI import; a test pins the two equal), and the rows are cut
by display width by the existing cutters (`_title_rows` at start-up,
`_own_title_rows` in `/trust`). `/skill` names every directory, the same way
(D7). pi's prompt names no directory.

### D7. The `/skill` panel

- One group per source directory in every tier (ADR-0279 D11 said "GLOBAL (one
  group per source directory)"; PROJECT now has several too):
  `PROJECT  .aelix/skills/`, `PROJECT  .agents/skills/`,
  `PROJECT  ../.agents/skills/` per parent (relative to the cwd and bounded as
  in D6), `GLOBAL  ~/.aelix/agent/skills/`, `GLOBAL  ~/.agents/skills/` (the
  user's keeps its `~` under the home directory). The first commit drew a
  parent by full path; cut from the right at 80 columns, every parent shares
  the long prefix that was kept, and all four headers of a 150-cell cwd read
  the same (verify V1).
- A group's rows are drawn together, under one header. Rows sorted by
  (tier, index) put a shadowed row after every winner of its tier, so with
  three project directories a shadowed row of the second drew a second header
  for it below the third (and `plan_layout`, which counts distinct groups,
  planned one row fewer than was drawn). They are now sorted by (tier, the
  group's first row, index); sabotage M4 turns the contiguity row red.
- A same-name skill in `.agents/skills` that `.aelix/skills` beats is a locked
  "shadowed" row in its own directory's group, naming the winner.
- Untrusted: one locked summary row per directory the scan would read — `.aelix/
  skills` and each `.agents/skills` — counted from a listing by the loader's
  rules (`count_untrusted_skills`, no file opened): a `SKILL.md` in the
  directory itself is its one skill, hidden entries and `node_modules` are
  skipped (the first commit counted `node_modules`, review F6), a subdirectory
  with a `SKILL.md` counts, and a root `.md` file counts except in an Agent
  Skills directory. "N skills not loaded".
- A scrolled list pins the top row's group header only when a row of that group
  is under it; otherwise the window starts at the next header
  (`selection_panel._window`). The pin replaced the top row, so a one-row
  group — every untrusted `.agents/skills` is one — showed a header with
  nothing under it (verify V6).

### D8. `~/.claude/skills` is not read automatically

As pi, which dropped it. A user who wants it adds it as a path in a settings
file's `skills` list (ADR-0279 D1), the example that ADR and
`skills-and-prompts.md` already give.

### D9. Every entry point agrees

| Entry point | Reads `.agents/skills`? | Counts it for trust? |
| --- | --- | --- |
| launch (TUI) | yes — `_load_skill_catalog` → `_resolve_skill_tiers`, trusted only for the project walk | yes — `_resolve_project_trust` → `resolve_project_trusted` step 2 |
| `-p` / `--mode json` | same loader | same resolver; untrusted → the headless notice |
| `--mode rpc` | same loader (the same `_async_main`) | same resolver |
| `/reload` | same loader (`reload_seed` branch of the factory), the launch's trust | `maybe_save_implicit_project_trust_after_reload` uses the predicate; its message says the folder "gained .aelix resources or .agents/skills" (it named `.aelix` only, verify V2) |
| `/agents use` | same loader (`agents/service.py`), `inherit_skills: false` → `--no-skills` → none | the launch's trust |
| delegated child | its own launch, its own trust | `aelix_agents.trust.child_trust_argv` calls the predicate: a same-cwd child under an ancestor `.agents/skills` gets `--no-approve` |
| `--list-models` | no skills | the predicate decides whether the notice is printed |
| the trust vote-load, `auto_trust_on_reload_cwd` (pi `autoTrustOnReloadCwd`) | — | the predicate |
| `aelix status` | lists no skills | `project_trusted` from `resolve_project_trusted`; untrusted reads "NOT trusted — .aelix/ resources and .agents/skills here are skipped" (it named `.aelix/` only, verify V4) |
| `/trust`, the start-up question | — | name `trust_requiring_agents_skill_dirs` |
| `/skill` panel | `SkillKind` over the holder; untrusted rows from `project_agents_skill_dirs` | — |

The headless notice names the family: "Notice: project-local .aelix resources
(extensions, skills, agent profiles, settings) and .agents/skills skipped in an
untrusted directory; pass --approve to trust." A repository whose only skills sit
there is in that group, and the old text named only `.aelix`.

### D10. A write under `.agents/` is never auto-accepted

`.agents` joins `.aelix` in `builtin/permission.py` `_SENSITIVE_DIR_COMPONENTS`.
D1 and D2 made `.agents/skills` text that reaches the system prompt, and the
first commit left it writable: measured through the real gate, write tool,
loader and harness (review F1), an `auto-accept-edits` child without a UI
(`headless_default="block"`, the posture a widened delegated child gets) wrote
`.agents/skills/planted/SKILL.md`, the parent — trusted at launch, nothing to
gate — put its description in the system prompt on `/reload` and expanded
`/skill:planted`; the same write from a session started in `$HOME` landed in
`~/.agents/skills` and loaded in an unrelated `--no-approve` project. Now
both are blocked headless and ask interactively. As for `.aelix`, `yolo` and a
plain headless run (no approver) still write. No other path list in the
tree names `.aelix` (the bash and PowerShell protected prefixes name neither).

As for `.aelix` (ADR-0197 §(i)), the rule matches folder NAMES on the write's
own path, in its lexical and its symlink-resolved spelling. A link with another
name that points into `.agents` (`docs -> .agents`) is therefore refused, but a
link NAMED `.agents` or `.agents/skills` that points at a folder with another
name is not seen from the other side: a write to that real folder is judged by
its own names and auto-accepted, and the loader then reads it through the link
(round 2, R2; Limits). The first version of this paragraph said only "checked
on the lexical and the symlink-resolved path", which read as if symlinks were
covered both ways.

**Amended 2026-11-03 (#646, owner decision): the folders behind the names are
refused too.** The name rule stays — it is what holds while a link does not
exist yet (the TOCTOU in `_is_auto_allowable_write`'s docstring). Beside it, a
write whose resolved path is, or is under, the resolved path of a folder or
file the loader reads is not auto-accepted: it asks interactively under
`auto-accept-edits` and `auto`, and a delegated child without a UI is refused
it (`builtin/permission.py` `_loader_resource_roots`, `_under_loader_resource`).
The owner chose the whole class, not the skills folders alone:

- `<cwd>/.aelix` and the agent directory (`~/.aelix/agent`, or wherever
  `AELIX_CODING_AGENT_DIR` puts it), whole — skills, `extensions/` (run as code
  on the next launch), `settings.json`, `agents/`, `prompt-templates/`,
  `mcp.json`, `trust.json`. The name rule already refuses every `.aelix`
  component, so the folder behind a link of that name gets the same answer.
- `~/.agents/skills`, and `.agents/skills` in the cwd and each folder above it
  up to the git root, whether it exists or not (`core/skill_dirs.py`
  `agents_skill_walk`), so `.agents -> tooling` guards `tooling/skills` before
  the write creates it.
- The plain `skills` and `prompts` paths of both settings files, and the global
  settings file itself (`AELIX_SETTINGS_PATH` can move it). The project file is
  read by the gate itself on every decision, whatever the trust — a delegated
  child runs `--no-approve` wherever that file exists (`aelix_agents/trust.py`),
  so its own manager never opens it, while the trusted parent loads the paths
  on its next `/reload`. The read applies and reports nothing and never blocks:
  anything but a regular file names nothing, and the file is opened
  non-blocking. The global file is read through the process's manager
  (`PermissionExtension.settings`, wired in `cli/entry.py`; RPC
  `permissions/evaluate` passes the same one), the copy loaded at launch and
  the file on disk both counting.
- The `skills:` and `extensions:` paths of the agent profiles in the agent
  directory's `agents/` and the project's `.aelix/agents/` — a run of that
  profile loads them (`agents/profile.py` `_parse_path_list`). Read by the
  gate itself, like the project settings file, so the project's count in an
  untrusted project too; an unchanged profile is parsed once (cached by path and
  `stat`).
- The file `$AELIX_MCP_CONFIG` names: its servers run as commands at the next
  launch.
- The target of every link inside the folders above that a load follows
  (Codex PC1): the skill loader lists a skills folder at every depth and
  follows a linked folder or `SKILL.md` (`harness/skills.py`
  `_load_skills_from_dir_internal`), the extension loader a linked `.py` or
  package. Each existing root is listed for links — below a config dir only its
  own entries and `skills/`, `extensions/`, `agents/`, `prompt-templates/` and
  `bin/` (the bash tool runs `~/.aelix/agent/bin` from the front of `PATH`;
  round 2, R4), so `~/.aelix/sessions` is never listed from a cwd of `~`; a
  hidden folder, `node_modules` and `__pycache__` are not entered — and each
  target is a root in turn, listed the same way.
- For a delegated child, the same project roots of every cwd above it, and the
  parent's `$AELIX_MCP_CONFIG` (the child's environment drops that variable). A
  child the `agent` tool starts in a subfolder computed its roots from that
  subfolder alone, so the parent's `.aelix` and the paths its settings name were
  not roots for it (verify V3). Both channels' spawns — a first run and a resume
  alike — add the parent's cwd and MCP file to `AELIX_PARENT_LOADER_ROOTS`
  (`pass_loader_roots_to_child`), keeping what the parent inherited, so a
  grandchild knows every folder above it. The child's gate only adds what the
  record lists; a record that does not parse, lists more than 16 entries, or
  names a path on another machine than the child's cwd asks for every write. A
  project `.env` cannot set it (`^AELIX_`, measured through `load_dotenv`).

Not taken: `--skill`, `--prompt-template` and `--agent-file` paths (the user
named them for the run), the skills shipped inside the package (the program
itself), and the session folder (`AELIX_CODING_AGENT_SESSION_DIR`): a session
is read only when the user resumes one, as the transcript they pick, never at a
launch they did not ask for, and its default lies outside the agent directory
(`~/.aelix/sessions`). A user whose `~/.agents/skills` links into a dotfiles
repository is asked when editing skills there, not elsewhere in that
repository — the cost `.aelix` already pays (ADR-0197 §(i)).

THE PATH THE TOOL OPENS (review round 1, verify V1 and V2; older than #646).
The gate expanded the path with `os.path.expanduser` and joined it to the cwd
itself; the write and edit tools resolve it with `tools/_path_utils.py`
`resolve_to_cwd`, which strips a leading `@`, normalizes to NFC and turns
unicode spaces into ASCII ones. `@.aelix/extensions/at.py` was judged as a
folder named `@.aelix` and written to `.aelix/extensions/at.py`;
`@~/Library/LaunchAgents/x.plist` and `@/anywhere/x` were judged inside the
project and written outside it — every one auto-accepted, interactive and
headless, on `74f9214c` and on the first #646 commit. And `realpath(normpath(p))`
applied a `..` before the link in front of it, while the OS applies it to the
link's target: `lnk -> .agents/skills/existing` made `lnk/../v/SKILL.md` a
project skill. Now the gate judges `resolve_to_cwd`'s own result
(`_write_spellings`), and resolves it one name at a time without normalizing it
first (`_realpath`); a test writes a corpus of spellings (`@`, `~`, `~user`,
NFD, unicode spaces, a trailing `/`, `..`, `.`, absolute, relative) through the
real write and edit tools and holds the gate's target to the file they opened.

WHAT ONE DECISION MAY COST (verify V4). A repository writes the project
settings file and the project profiles, and can ship any number of links and
folders under a root; the call runs on the event loop. On the first commit,
75,684 entries in a 1 MiB settings file cost 1.13 s per write decision and
1,000 entries through a 300-link chain 0.67 s. Now a project's list holds at
most 64 entries, its `.aelix/agents` at most 64 profiles of at most 64 KiB, a
path at most 64 links (no kernel opens a path through more: Linux stops at 40,
macOS at 32, Windows at 63), and one decision lists and follows at most 8,192
folder entries and links (`_Budget`); past any of them the write asks.

Round 2 (verify R2): within those caps the project profiles still cost the
first decision of every process — and so of every delegated child — 5.1 s to
26.5 s on `d937442a` (64 profiles of 63 KiB; `yaml.safe_load` is pure Python),
and nesting costs the square of its depth: one profile of 4,000 `[` took
0.43 s whatever its size. Now the project profiles one decision reads hold at
most 12 KiB of frontmatter in all, counted before any of it is parsed, cached
or not, so the answer does not depend on what the process parsed before, and
each profile at most 16 `[` and `{` (`_PROJECT_PROFILE_TEXT_LIMIT`,
`_PROJECT_PROFILE_FLOW_LIMIT`); past either, the write asks. A body is not
YAML and costs nothing. Measured after: the verify's five shapes ask in
0.3–9 ms (the 9 ms is the first `yaml` import), and the worst found inside
the caps — the text limit filled by 1 KiB profiles that open 16 each, then
list — takes 52 ms once, then under 1 ms. `CSafeLoader` is not used: libyaml
crashed the process on the deep profile (exit 139).

A PATH ON ANOTHER MACHINE (verify V5, Windows; measured with `ntpath` rules on
POSIX, no Windows run). `stat` of `\\host\share\x` (or `//host/share/x`) opens
an SMB session that sends the user's NTLM hash, and the gate now reads paths a
project wrote. A project settings or profile path and a record entry on a UNC
or device drive (`\\?\`, `\\.\`) other than the one the cwd is on, or in the
NT object namespace (`\??\`, which `ntpath` before 3.13 takes for a rooted
path, not a drive), is refused before anything resolves it: the write asks.
Round 2 (verify R3): `ntpath.realpath` follows a whole chain inside one
`CreateFileW`, and `stat`, `scandir` and a listed entry's `is_dir()` follow
one too, so a link whose SECOND hop is a share, a root that is such a link, a
profile file linked to one and a link at the top of a config dir reached the
share before anything looked at it. Now on Windows the gate resolves every
path it opens one link at a time — the write itself, each root, the
`.aelix/agents` folder and each profile in it, the project's
`.aelix/settings.json`, a link inside a root — and refuses a link whose target
is on another machine before following it (`_realpath_nt`, `_link_target`).
`os.readlink` gives a junction and an absolute link as `\\?\C:\…`; that prefix
comes off first, as `ntpath.realpath` takes it off, so a link to a local
drive is followed, not refused as a device path. The link-free result goes
through `os.path.realpath` only for its spelling (case, 8.3 names).

How a write is placed: each root is resolved and anchored at its deepest
existing folder, and the write's folders are walked up to the filesystem root —
a root matches when one of them is that folder by file identity (device and
inode, as `skill_dirs.path_key`) and the names below it start with the root's
missing names, folded as the name rule folds them (case, trailing dots and
spaces, a stream suffix) and NFC-normalised. So `Tooling/skills/…` on a
case-insensitive volume is caught, and a cwd inside a root (the dotfiles repo's
`skills/`) asks for every write. All of it is recomputed on every decision: a
link can be re-pointed mid-session. An `OSError` while resolving the write or a
root, listing a root or reading a settings file or profile asks, as does a
project settings file over 1 MiB; a settings file or profile that does not
parse names nothing, as the loader reads nothing from it. JSON or YAML nested
past the parser asks instead (round 2, verify R1): whether the parser gives up
depends on how deep the stack already is, so it is no proof that the loader,
with a shallower stack, reads nothing. On `d937442a` a project settings file
of `"[" * 100000`, or that value in `AELIX_PARENT_LOADER_ROOTS`, raised
`RecursionError` out of the hook — which the harness turns into an error
that ends the turn, by a reading of `harness/core.py`, not a run — and the
spawner raised too. Now the write asks, and the spawner starts the
child with the record as it got it.

Measured before (`74f9214c`) and after (the first #646 commit) through the real
gate (`GuardrailExtension` + `PermissionExtension`, interactive with an approval
runner and headless with `headless_default="block"`), the write and edit tools,
RPC `permissions/evaluate` and the trusted catalog
(`/tmp/646-impl/live/{before,after}/summary.json`, kept locally). Every row
was `allow` in both postures and both channels before, and was written and
loaded as shown:

| Link or setting | Write | Before | After |
| --- | --- | --- | --- |
| `.agents/skills -> ../.claude/skills` | `.claude/skills/planted-a/SKILL.md` | written, project skill | asks; child refused |
| `.agents -> tooling` | `tooling/skills/planted-d/SKILL.md` | written, project skill | asks; child refused |
| `.aelix -> config` | `config/skills/…`, `config/extensions/x.py`, `config/settings.json`, `config/agents/…`, `config/prompt-templates/…` | written (a skill, a template, and next launch's code and settings) | asks; child refused |
| `~/.agents/skills -> ~/dotfiles/skills`, cwd `~/dotfiles` | `skills/planted-f/SKILL.md` | written, global skill (also in an unrelated untrusted repo) | asks; child refused |
| `~/.aelix -> ~/dotfiles/aelix`, cwd `~/dotfiles` | `aelix/agent/extensions/x.py`, `…/skills/…`, `…/settings.json` | written, global skill | asks; child refused |
| project `"skills": ["../docs/skills"]`, `"prompts": ["../docs/prompts"]` | `docs/skills/…`, `docs/prompts/planted-p.md` | written, project skill and template | asks; child refused |
| global `"skills": ["~/work/team-skills"]`, cwd `~/work` | `team-skills/planted-g/SKILL.md` | written, global skill | asks; child refused |
| none (control) | `src/x.py` | written | written |
| `--skill myskills` (control) | `myskills/planted-cli/SKILL.md` | written, `cli` skill | written, `cli` skill |

Review round 1 measured what the first commit still let through, on that
commit (`98b9ea46`) and after this round — in process through the same gate
(`/tmp/646-fix-r1/probes/`, kept locally), and live with real children (the
table after this one):

| Case | Write | On `98b9ea46` | After round 1 |
| --- | --- | --- | --- |
| leading `@` | `@.aelix/extensions/at.py`, `@.agents/skills/v/SKILL.md` | written, the skill loaded | asks; child refused |
| leading `@`, outside the project | `@/tmp/…/outside/at.txt`, `@~/Library/LaunchAgents/x.plist` | written outside the project | asks; child refused |
| `lnk -> .agents/skills/existing`; `lnk2 -> .aelix/extensions/sub` | `lnk/../v/SKILL.md`; `lnk2/../dd.py` | written, project skill; `.aelix/extensions/dd.py` | asks; child refused |
| `.aelix -> pkg/conf`, child started with `cwd: pkg` | `conf/extensions/sub2.py` | written | refused |
| project `"skills": ["../docs/skills"]`, child with `cwd: docs` | `skills/v/SKILL.md` | written, project skill | refused |
| `.agents/skills/x -> ../../docs/x`, two folders down, a linked `SKILL.md`, `.aelix/extensions/e.py -> ../../src/e.py` | `docs/x/SKILL.md`, `far/z/SKILL.md`, `notes/solo.md`, `src/e.py` | written, each loaded through the link | asks; child refused |
| `AELIX_MCP_CONFIG=mcp.servers.json` | `mcp.servers.json` | written | asks |
| profile `skills: [../../docs/pskills]` | `docs/pskills/x/SKILL.md` | written | asks; child refused |
| none (control) | `lib/z.py`, `@lib/z.py`, `lnk3/../ok.py` | written | written |

Live (rule 10): the TUI under a pty with `--permission-mode auto-accept-edits`,
OpenRouter `anthropic/claude-haiku-4.5`, a project with `.aelix/settings.json`
(`"skills": ["../share/skills"]`), so each child ran `--no-approve`; first
`/agents run general-purpose`, then the parent model's own `agent` call with
`cwd: share`, whose consent dialog read `Directory: …/repo/share`,
`Permission: auto-accept-edits` (`/tmp/646-fix-r1/live/{before98,after}/`, kept
locally; no `sk-or-` in any output, pty bytes or session):

| Child's write | On `98b9ea46` | After round 1 |
| --- | --- | --- |
| `lib/ok.txt` (control) | written | written |
| `kit/agents/skills/live-a/SKILL.md` (`.agents -> kit/agents`) | refused | refused |
| `share/skills/live-b/SKILL.md` (settings path) | refused | refused |
| `@.agents/skills/live-c/SKILL.md` | allowed; `mkdir` failed on the dangling link | refused |
| `lnk/../live-d/SKILL.md` (`lnk -> share/skills/seed`) | written to `share/skills/live-d/` | refused |
| `@.aelix/extensions/live-at.py` | written to `.aelix/extensions/` | refused |
| `@/tmp/…/outside/live-out.txt` | written outside the project | refused |
| `docs/linked/SKILL.md` (`.aelix/skills/linked -> ../../docs/linked`) | written | refused |
| `cwd: share` child, `skills/live-e/SKILL.md` | written to `share/skills/` | refused |
| `cwd: share` child, `sub-ok.txt` (control) | written | written |

The cost is a resolve, a few `stat` calls and a listing per existing root on
each write decision: 0.14 ms in a repository (0.11 ms on the first commit),
0.48 ms with 20 project profiles, 4.2 ms with a 1,200-entry `.agents/skills`
tree (0.11 ms before: it was not listed) and 4.8 ms at a cwd 65 folders deep
outside a repository; the hostile settings files above now ask in 1.9 ms and
0.2 ms.

The rule asks about every write with a `.agents` segment anywhere in its path,
as `.aelix`'s does, not only `.agents/skills` (round 2, R4): `.agents/plans/x.md`,
a file named `.agents`, `node_modules/pkg/.agents/README.md`, and every write in
a project that sits below a `.agents` folder (`/work/.agents/proj/src/main.py`).
It errs toward asking, and a cwd of `~/.agents/skills/my-skill` should ask;
narrowing it to `.agents` followed by `skills` was not taken.

## Consequences

- A user's `~/.agents/skills` loads in every folder, ungated, below their own
  `~/.aelix/agent/skills` of the same name.
- A repository's `.agents/skills` (and its parents' up to the git root) load
  only in a trusted project; such a repository asks once interactively and is
  denied under `-p` until `--approve` or a saved decision — as a `.aelix/skills`
  one already was.
- A folder under the home directory outside a repository does not ask because
  of `~/.agents/skills`.
- The start-up question is two or three rows taller (a blank row and the
  sentence, which wraps once at 80 columns).
- An interactive `auto-accept-edits` user writing their own `.agents/` sees the
  approval prompt instead of a silent write (D10), as for `.aelix/`. Since #646
  the same holds for the folder a link of that name points at, the folders the
  settings `skills` and `prompts` paths and the agent profiles' `skills:` and
  `extensions:` paths name, the `$AELIX_MCP_CONFIG` file, the target of a link
  inside any of them, and the agent directory when the cwd holds it
  (2026-11-03).

## Limits

- The gate is decided once per launch (ADR-0252 §E); a `.agents/skills` that
  appears later in an already-trusted folder loads on `/reload`, as any
  `.aelix` resource does (ADR-0216, #112). A folder that was trusted without
  asking (nothing to gate at launch) is then also SAVED as trusted
  (`maybe_save_implicit_project_trust_after_reload`) — because of a
  `.agents/skills` in a folder above it, which pi's `autoTrustOnReloadCwd` does
  too.
- The untrusted panel row counts one level of a directory (a skill folder or,
  for `.aelix/skills`, a root `.md` file); a skill deeper down, or a grouped
  `.md`, is not counted, and a directory holding only those shows no row while
  the trust question still names it. Ignore files are not read (no file of an
  untrusted folder is opened), so an entry a `.gitignore` excludes is still
  counted: the one case where the count is more than trusting loads.
- The trust check lists one level: a `.agents/skills` holding only an empty
  subfolder, or one an ignore file excludes, still asks.
- Only the `.agents/skills` sentence is bounded (D6). The start-up question's
  cwd line and its `Trust parent folder (…)` option still grow with the cwd, as
  before #509 (#380 cuts them into rows, it does not shorten them), and the
  screen fills sooner than the first version of this note said (it put the
  edge at a 202-character cwd; round 2, R3). Counted with `_one_shot_rows` at
  80 columns, an ASCII cwd of 161 characters with three or more folders, or 164
  with one or two, already takes all 24 rows (the hint on the last), and the
  last option drops below row 24 at 257 characters with three or more folders
  (321 with fewer); the round-1 verify's pty run had a 154-cell cwd under
  five folders take exactly 24 rows. A hostile cwd gets there sooner: in the
  same verify, 122 characters holding a newline, an ESC and 30 wide CJK
  characters, under three folders, put "Do not trust (this session only)" and
  the hint below row 24 of the pty, and Down ×4 selected a row that was not
  drawn (counted in-process, that option sat at
  row 20 on `0e97bd28`, before the sentence, and at row 24 since). Shortening
  rows will not close this; the fix is to keep the options pinned and scroll
  the body above them, left to a follow-up issue.
- **Closed 2026-11-03 by #646 for what D10's dated note lists, and no
  further.** The write rule (D10) judged the names on the write's path, not the
  folders the loader reads: a write to the real folder behind a symlink named
  `.agents`, `.agents/skills` or `.aelix`, or to a #511 settings `skills` path
  (`docs/skills`), was auto-accepted and loaded (measured through the real gate
  and the trusted catalog on `0e97bd28`). What D10's note still does not cover:
  - A hard link: a pre-existing hard link to a skill file (`docs/s.md` and
    `.agents/skills/x/SKILL.md` one inode) is written through its other name
    and changes the skill (round 1, V8). Git cannot check out a hard link, and
    `ln` asks under `auto`.
  - Decide, then link, then write: the write tool resolves the path again when
    it runs and does not ask again, so a link made between the decision and the
    write lands where it points (round 1, V9). Every command that makes a link
    asks under `auto` (`ln -s`, `git apply`, `git checkout`, `tar`, `unzip`,
    `patch` among them), and the name rule holds for a name that is already
    sensitive.
  - A repository can make every write ask, the safe side (round 1, V10): a
    self-link `.agents -> .agents`, `"skills": ["/"]` or `"prompts": [".."]`,
    a list of more than 64 entries, more than 64 profiles, profiles holding
    more than 12 KiB of frontmatter in all or more than 16 `[` and `{` in one
    (round 2), or a tree under a root past 8,192 entries and links. So can a
    user's own large tree under a root (a `~/.agents/skills` clone that big —
    linking `~/dev/aelix-studio`, 54k entries, made every write ask in the
    round-1 verify), and an `AELIX_CODING_AGENT_DIR` inside the cwd now asks
    for the writes under it (round 1, V12).
  - `--skill`, `--prompt-template` and `--agent-file` paths, the packaged
    skills and the session folder (D10's note says why).
  - Windows (inferred from `ntpath` rules and a mocked `_NT` on POSIX; no
    Windows run): the gate judges every link before following it (D10's note),
    but the trust check at launch already looks at `.aelix` and
    `.agents/skills` themselves, and a link re-pointed between the gate's walk
    and its final `realpath` is followed (the decide-then-link race above). A
    root the user's own files link to another machine (a `~/.agents/skills` on
    a share) makes every write ask. On Python 3.11 a junction inside a root is
    listed as a folder and its target never becomes a root
    (`DirEntry.is_junction` is 3.12+); git cannot check out a junction, so it
    takes one made by hand (round 2, R5).
  - Out of scope, older than #646, for a follow-up issue (verify V11, round 2
    R7): a trusted `"skills": ["/"]` scans the filesystem for about 23 s and
    then crashes the skill catalog with a `PermissionError`
    (`harness/skills.py:280`, `entry.is_dir()` in
    `_load_skills_from_dir_internal`) — the gate asks for every write there;
    and a valid project settings file nested 5,000 deep makes
    `SettingsManager.create` for a trusted project raise `RecursionError`
    (the gate's own read parses it).
- `/skill` orders a tier's groups by their first row, so a directory whose rows
  are all shadowed is drawn after the next directory's winners (a cwd's
  `.agents/skills/` holding only a copy that `.aelix/skills` beats sits below
  `../.agents/skills/`). The rows of each group stay together.
- The walk follows the cwd's spelling. A process started with a cwd string that
  is not its physical path (an embedder passing a symlinked path) walks that
  string's parents.
- The start-up question and `/trust` name three directories; the rest are
  counted, not named (`/skill` lists them all).
- Seen while measuring D6 and not changed here: the start-up question prints
  the cwd with a newline in a folder name as a line break (`_title_rows` splits
  the body at newlines before naming), and `/trust`'s own frame blanks an ESC in
  the cwd and prints the `Trust parent folder (…)` option label and the footer's
  cwd unflattened, so a newline in a folder name breaks those rows. They predate
  #509 (the cwd is the user's, but a repository chooses its subfolders' names).
- Under `--no-skills` neither `.agents` directory is read (D13 of ADR-0279).

## Measured after

The same `-p` runs on this commit (`/tmp/509-impl/live/after*/summary.json`):

| Case | `<available_skills>` | stderr notice |
| --- | --- | --- |
| repo, `--approve` | `shared-name` (the `.aelix` copy), `pkg-skill`, `repo-root-skill`, `home-agents-skill`, the bundled two | — |
| repo, no flag (headless deny) | `home-agents-skill`, `shared-name` (the `~/.agents` copy), the bundled two | the new notice |
| folder under the home directory, no repository, no flag | `home-agents-skill`, `shared-name`, the bundled two | — (not asked) |
| agents-only repository, no flag | the bundled two | the new notice |
| agents-only repository, `--approve` | `planted`, the bundled two | — |

The model's answer listed exactly those names in every case. `above-repo-skill`,
in a `.agents/skills` above the git root, never loaded. A `README.md` at the top
of `~/.agents/skills` produced no warning.

In a pty (pyte, a second screen 80 columns wider recording any cell drawn past
the terminal's width), at 80x24 and 120x40 (`/tmp/509-impl/tui/grids-*`): the
start-up question with the sentence and the two relative paths; "Do not trust
(this session only)", then `/skill` with one locked "N skills not loaded" row per
directory (`.aelix/skills/` 1, `pkg/.agents/skills/` 1, the root's 2) and
`GLOBAL  ~/.agents/skills/` loaded; `/trust` with its fifth row; with `--approve`,
`/skill` with one header per directory, the shadowed `shared-name` rows in their
own directories' groups naming the `.aelix` winner, and Space on one refused
with that reason; a folder under the home directory: no question, `GLOBAL
~/.agents/skills/`. No frame drew past the width; the panel's height did not
change while open (17 rows at 80x24 in both runs, 20 untrusted and 21 trusted at
120x40, across cursor moves, End, a search and a refused Space). A folder named
`evil<ESC>[31mred<LF>Trust is safe` above the cwd: the raw output never held
`ESC [31m`; the new rows draw `^[` and `^J` (`/skill`'s group labels), the
relative phrase holds no folder name; see Limits for the pre-existing rows.

### After review round 1 (2026-11-01, `/tmp/509-fix-r1/`)

The review's probes re-run on the fixed tree (`probes/post-*.json`): the
`.agents/skills/planted/SKILL.md` write and the `$HOME` one are blocked at the
gate, so nothing reaches the reloaded prompt or the unrelated project; a
`HOME` in another case and a symlink so spelled give no project directory, no
question and the `~/.aelix/agent/skills` winner; a settings path and `--skill`
on one `.agents/skills` both keep `root-extra` and `group-extra`; the non-UTF-8
note loads the sibling skill; the `node_modules` row is gone. Real `-p` runs
(OpenRouter `anthropic/claude-haiku-4.5`, `live/out/summary.json`), the model's
answer matching `<available_skills>` every time:

| Case | `<available_skills>` | stderr notice |
| --- | --- | --- |
| repo whose root `.agents/skills` holds a skill, a grouped skill, a non-UTF-8 note and `node_modules/SKILL.md`, cwd two below, no flag | `sk-home`, the bundled two | the notice |
| the same, `--approve` | `grouped-skill`, `sk-root`, `sk-home`, the bundled two | — |
| folder under a `HOME` spelled in another case (its `~/.agents/skills` holds a non-UTF-8 note too) | `sk-home`, the bundled two | — (not asked) |
| repository whose `.agents/skills` holds only a `README.md` | `sk-home`, the bundled two | — (not asked) |

In a pty at 80x24 and 120x40 (`tui/grids-*`): the review's 65-level cwd draws
the question in 24 rows with "Do not trust (this session only)" on screen and
selected (row 25 before); a 151-cell cwd under five `.agents/skills` shows all
five options, `/trust`'s row ("… and 2 more (/skill lists them)") and `/skill`'s
headers `.agents/skills/` through `../×4/.agents/skills/`, all different,
untrusted and trusted, with no header bare at the top or above another while
opened, moved, at End and back up; the panel's height did not change (17 at
80x24, 23 at 120x40) and no frame drew past the width. The verify lane's
drivers (plain and hostile folder name, both sizes), re-run on this tree,
passed all 23 and 19 checks, and the planted `ESC[31m` never reached the raw
output.

### After review round 2 (2026-11-01, `/tmp/509-fix-r2/`)

A scripted local provider (no model call), a scratch `HOME` whose
`~/.agents/skills` holds a non-UTF-8 `SKILL.md` and one named unlike its folder,
and a global settings `skills` path `"~/.agents/skills"` (`live/drive.py`,
`live/summary.json`): `-p` printed each of the two `Warning: skill load:` lines
once (twice on the round-1 commit, as its verify measured); a trusted
project's `.agents/skills` named again by `--skill` printed its own two once
each beside the home ones; the TUI at 80x24 wrote each once to the raw stream
before its first frame.

## Tests

`tests/cli/test_agents_skills_509.py` (28): the walk and its stops, the user's
directory (also through a symlink), an unreadable and an empty directory, trust
covering the scan, the headless denial of an agents-only repository, the
delegated child's `--no-approve`, `--no-skills`, precedence and shadowing, the
prompt and `/trust` rows, flattening equal to the panel's, the panel's groups and
untrusted rows, and the loader rule for both `.agents` tiers. Fourteen
sabotages, each applied alone to the tree and reverted (`/tmp/509-impl/mutation/
mutate.py`), each turn at least one row red: the predicate clause, the home
exclusion, the git-root stop, the group sort, the catalog's `agents` flag (green
until the catalog-level row was added), the prompt's directory phrase, the
untrusted rows, an untrusted scan of the walk, the `~/.agents/skills` source, the
grouped-`.md` rule, `.agents` before `.aelix`, farther before nearer, an empty
directory asking, and `_one_row` passing a newline.

Review round 1 added 22 rows to that file (50 in all) — a `HOME` in another case
and a symlink so spelled (both skip on a case-sensitive volume), `path_key`'s
fallback, a settings path and `--skill` naming an Agent Skills directory, three
non-UTF-8 files (an undeclared note, a declared skill, an ignore file), the
counted levels and the deep question's rows at 80 columns, the parents' headers
at 80 columns under a cwd over 160 cells (untrusted and trusted), the user's `~`
header, the count's listing rule, seven listings for the trust check, and
`aelix status` — plus `.agents` rows in `tests/builtin/test_sensitive_aelix_dir.py`
(the predicate, a `docs -> .agents` symlink, and the gate end to end in a project
and in `$HOME`), the one-row-group window in `tests/tui/test_selection_panel_511.py`,
and the `/reload` message through the real input loop
(`tests/tui/test_reload_trust_message_509.py`). Fifteen sabotages, each one fix
reverted alone with `PYTHONDONTWRITEBYTECODE=1`, every `__pycache__` cleared and
the file's bytes restored and hashed (`/tmp/509-fix-r1/mutation/mutate.py`),
each turn at least one of those rows red; the `node_modules` count survived
once, because the fixture put its `SKILL.md` two levels down where the
one-level count never looks, and was red after the fixture was corrected.

Review round 2 added 3 rows (53 in all): a settings path to the user's
directory and `--skill` on a trusted `.agents/skills` each warning once, and
`path_key` on a filesystem reporting `st_ino == 0` (`os.stat` patched) still
keeping two spellings equal, the user's directory reached through a project's
symlink out of the walk and an unrelated repository's in it. Three sabotages
(`/tmp/509-fix-r2/mutate.py`, the same discipline): the warning dedupe removed,
the inode-less fallback never taken, and the fallback keyed by `abspath`
instead of `realpath`, each turned its rows red.
