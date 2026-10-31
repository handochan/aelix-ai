# 0279. Skills, prompts and (later) tools and extensions are turned on and off through one settings contract

Status: Accepted (2026-10-31, #511). D1–D15 are decided and implemented for
skills and prompt templates. The design's Codex cross-review
(`.omc/specs/511-design-codex-review.md`, findings C1–C22) was triaged into
what #511 guarantees (C1–C4, C7 for skills, C8–C10, C12, C14, C15, C17–C22,
each named where it lands below) and what #25, #510 and a separate issue own
(D16). Amended 2026-10-31 (review round 1: the implementation's Codex review
X1–X9 and an independent verification V1–V7, recorded in
`.omc/specs/511-progress-2026-10-31.md`): D1 (the glob is minimatch's, pinned
by a differential corpus), D2 (a path-shaped glob and the name form), D4
(queued setters parse strictly; the lock has a deadline on both platforms and
waits off the event loop; reads take no lock; a symlinked file is written
through), D6 (an apply waits for a harness build; an unreadable file applies
nothing; the cache sentence), D11 (the save is claimed when Enter is pressed;
a one-row list; state lines are flattened). Amended again 2026-10-31 (review
round 2, M1 and R1–R6 in the same record): D1 (the matcher cannot backtrack
exponentially — its worst cases, polynomial, are in Limits; a glob past fixed
limits is refused with a warning; a trailing `/**` needs a segment), D4 (trust is checked again after the lock wait; a
cancelled edit writes nothing or keeps memory and disk agreeing; Windows retries
a replace a reader blocks; a UTF-8 BOM is accepted), D6 (the deferred apply
refuses an unreadable file; the warning names it once), D8 (the refusal is one
of its warnings), D11 (a panel too small for one row refuses typing too).
Date: 2026-10-31
Issue: #511 (absorbs #595). Design (owner-approved layout A and §7 decisions
2–11 and 13) and its triage: `.omc/specs/511-design-2026-10-31.md`; the review
it triages: `.omc/specs/511-design-codex-review.md`; the gates, grids, live
runs and review rounds: `.omc/specs/511-progress-2026-10-31.md`. The raw pty
drivers, grids and probes were kept locally, not in the repo.
Amends: ADR-0165 D5 (the runtime enable/disable it waited for is this
contract; the Installed tab is #25's), ADR-0169 #12 and ADR-0196 (the skills
holder now holds a filtered catalog; same-name skills are deduplicated), ADR-0252
(a panel can create `.aelix/settings.json`, which puts the folder under project
trust — D3). Each carries a dated note.
Relates: ADR-0235 (pi is the reference, not a parity mandate), ADR-0219 (the
120-cell ceiling the panel draws under), ADR-0159 / ADR-0194 (the modal slot and
one-frame commits), #510 (`/tool`), #25 (`/extension`), #508 (completions read
the enabled list), #512 (MCP servers), #632 (a closed modal leaves the input
anchored high — not fixed here).
pi: `earendil-works/pi` `origin/main` `318d9add6` (2026-10-30) —
`coding-agent/src/core/package-manager.ts`, `src/cli/config-selector.ts`,
`components/config-selector.ts`, `docs/settings.md`.

## Context

The settings files already carried pi's `extensions`, `skills`, `prompts`,
`themes` and `packages` arrays, with getters and setters. No launch path read
them: `"skills": ["-pdf-tools"]` from pi's documentation did nothing, with no
warning. The only readers were RPC `settings/get` and `settings/set`
(`skill_paths`, `prompt_template_paths`), which exposed the raw lists without
applying them (C18 corrected the design's "nothing reads them"). The only
per-skill control was deleting the directory. #595 asked for a `/skill` panel
that turns skills on and off; the owner folded it into #511 and decided
(2026-10-26) that each resource kind gets its own slash panel — `/skill`,
`/tool`, `/extension` — over one shared contract, rather than a resource page in
`/settings`.

Measured on `e19ab55e` before this change (design §1, as corrected by C22):

- the setters wrote the whole in-memory list: a hand edit made after load, or
  another session's save, was overwritten;
- a write to a scope whose file had not loaded was skipped: `drain_errors()`
  reported the load error once (recorded at construction) and nothing about
  the skipped write, while the getter returned the new value, and
  `/scoped-models` reported "persisted" from that read-back;
- `FileSettingsStorage.with_lock` locked the settings file's inode, and only if
  it existed — `os.replace` gives the file a new inode on every write, so a
  waiter could hold the old one while another writer locked the new one, and
  for a missing file the read and `fn` ran before any lock (C1); a read error
  read as an absent file, and the temp file had a predictable name (C2);
- `/reload` did not rescan skills (an edited description and a new skill
  directory were both missing after it), while `resource_commands.py` said it
  did;
- two skills with one name both loaded with no diagnostic, and because the
  bundled directory was scanned first, `/skill:<name>` expanded the bundled copy
  over the user's own — the reverse of what `entry.py`'s docstring claimed;
- `/skills` was a read-only list with no source or state.

pi has no in-session toggle. `pi config` is a full-screen selector that writes
`+X` / `-X` entries on every keystroke, grouped by source, with a Global/Project
mode switch and a three-state project cycle.

## Decision

### D1. The grammar is pi's, plus identity

Entries in `skills` and `prompts` (and, with #25 and #510, `extensions` and the
tools key), in both `~/.aelix/agent/settings.json` and
`<cwd>/.aelix/settings.json` (`core/resource_filter.py`):

| Entry | Meaning |
|---|---|
| plain path | adds a source, resolved against that file's directory (`~` expanded) — how `~/.claude/skills` is added (#509) |
| `!glob` | exclude: pi's matching (the path relative to the file's directory, the basename, the absolute path; for a `SKILL.md` also its directory in the same three forms), with minimatch's glob — and, new, the item's identity |
| `+X` / `-X` | exact include / exclude: X is pi's exact path forms (a leading `./` dropped) or, new, the item's identity — a skill or prompt name |

The glob is minimatch's with pi's options (none): `*` and `?` within a segment,
`**` across, no match of a leading `.` by a wildcard, `[…]` with `!`/`^`
negation and POSIX classes, `{a,b}` and the sequences `{1..3}`, `{01..10}`,
`{1..9..2}`, `{a..e}`, the extglobs `@()` `?()` `*()` `+()` `!()`, `\`
escapes, a leading `#` (a comment: matches nothing) and a second leading `!`
(minimatch's own negation). A trailing `/**` needs at least one segment
(`pdf-tools/**` does not match `pdf-tools`), as in minimatch's globstar
matching (round 2, R1). It is a re-implementation, not minimatch itself:
`tests/core/minimatch_corpus_511.json` pins 5,978 answers minimatch 10.2.6 —
the version in pi's lockfile — gives, and the matcher must agree with all of
them (X6; before round 1 `skill{1..3}` and `@(foo|bar)` silently matched
nothing).

**A glob no longer hangs a launch (round 2, M1).** Round 1 handed each segment
to Python's backtracking regex engine, and nested quantifiers backtracked
exponentially: `!*(*(?))x` against a 29-character skill folder kept `aelix -p`
from getting past skill loading (killed at 25 s). A segment is now matched by
the same translation evaluated as a table of end positions instead of by
backtracking, so every match finishes: the work is polynomial — about
nodes × characters² Python steps for a segment with extglobs, since each node
at each position walks the end positions its groups can reach — where it was
exponential. A segment with no extglob uses `fnmatch`'s atomic-group
construction, in C. Round 1's adversarial cases now answer in at most about
11 ms (`tests/core/test_resource_filter_511.py` times them in a child
process); that is not a bound for every input inside the limits below —
Limits gives the measured worst cases. Brace expansion is bounded instead of
evaluated: a `!glob` whose braces expand to more than 256 alternatives (or
more than 32 that are still globs; plain names are a set lookup), that is
longer than 512 characters, or whose groups nest more than 16 deep is REFUSED —
said once at launch as a D8 warning ("is too complex to match: …; it is
ignored") and treated as matching nothing. minimatch has no limit; a pi file
with such an entry loses that one entry here, and is told so.

One list applies `!`, then `+`, then `-`, whatever their order in the file, and
yields on, off or no opinion. `-vue*` still matches nothing. The panels write
the identity form (`"-pdf-tools"`), so an entry survives the skill moving
directory. A symlinked file matches by either spelling. A pi user's file keeps
working.

### D2. Scopes: project, then global, then on; a path reaches only its own scope

An item's saved value is the project file's opinion if it has one, else the
global file's, else on (`resource_filter.effective`). A name or a glob on a name
reaches every item — a global entry reaches project skills, a project entry
decides for the user's own skills in that project (this diverges from pi, where
each scope's patterns filter only that scope's resources). A PATH form reaches
only the items of its own file's scope: the project file's paths reach
project-discovered items, the global file's paths everything else
(`resource_filter.reaches`). Resolving against the file's directory does not
ensure that by itself — an absolute path or `../` goes anywhere — so it is
enforced (C15). A path-SHAPED glob still has its name forms: `!**/pdf-tools` in
the project file reaches the user's `pdf-tools`, because `**` can match no
segments and the rest is the skill's directory name (verifier V7). Locks (D13)
override both.

Each panel row keeps apart (C15) its row id (a shadowed duplicate shares its
winner's name), the name it is invoked by (`/skill:<name>`; none for a shadowed
row), the aliases entries match, the scope it was discovered in and the
directory it came from (`ResourceItem`).

### D3. Trust is an input to every read and write

An untrusted project's file is neither read nor written (ADR-0252 §A). Every
selection operation takes trust as an input — the launch decision AND the
manager's live flag, so a revocation after launch is honoured — and an untrusted
project scope is empty: its settings file is never opened, locked or even
stat'ed by a snapshot, a save, the post-save check or `needs_new_project_file`
(C4). `edit_list` checks trust again after it has drained the write queue, so a
revocation while it waits refuses the edit. The project's skills appear in the
panel as one locked summary row whose count comes from listing
`.aelix/skills/` — no `SKILL.md` is opened. "This project" cannot be chosen as a
save target there.

In a trusted folder with no `.aelix/settings.json`, the first project save
CREATES the file. Its existence makes project trust APPLY to the folder
(ADR-0252 §B); whether the next launch asks depends on what precedes the prompt
— an extension's `project_trust` answer, a saved decision for the folder or a
parent, `defaultProjectTrust` (`project_trust.py`, in that order) — and a
`-p` run needs `--approve` only where none of those decides (C22). The panel
says "This creates .aelix/settings.json: the folder falls under project trust.",
its key line adds "next launch may ask; -p may need --approve", and it writes
only on a second Enter.

### D4. One write primitive, one cross-process transaction

`SettingsManager.edit_list(scope, key, fn)` (`aelix_ai/settings`) runs `fn` on
the list AS IT IS ON DISK, inside `FileSettingsStorage.transact`:

- **A stable sidecar lock first (C1).** `<global settings dir>/locks/
  settings-<hash of the file's real path>.lock`, taken with `fcntl.flock` on
  POSIX and `msvcrt.locking` on Windows — on both, non-blocking attempts until a
  30 s deadline, then a `TimeoutError` naming the file (V6) — BEFORE the read,
  before `fn`, whether or not the file exists. It is never replaced, so the
  inode race cannot happen; both scopes' locks live beside the global file, so
  locking the project scope creates nothing inside the project. The old inode
  lock is still taken inside it, so a process that knows only the old protocol
  still serialises with this one. A symlinked settings file is resolved once and
  written THROUGH — the link stays a link, and the lock, keyed by the real path,
  keeps its identity across saves (X4). The wait happens off the event loop
  (`edit_list` and the setters' write task run the transaction in a worker
  thread), so a session another one keeps waiting stays responsive; a READ
  takes no lock at all, since writes replace the file atomically (V6).
  Measured: six processes making eight edits
  each to one file — 48/48 kept, for an existing and for a missing file; with
  the sidecar disabled, 29/48 and 27/48.
- **A strict read and an exclusive write (C2).** A file that exists but cannot
  be read raises; only a missing file reads as absent. The temp file is created
  with `mkstemp` (`O_EXCL`, random name) in the target's directory, so a
  planted `settings.json.tmp` symlink is never written through, and the payload
  is written in a loop until every byte is down.
- **It joins the write queue (C3).** Setter writes already queued land first
  (it flushes, then takes the scope's in-process lock their tasks take), and on
  success the in-memory scope takes exactly the list written and forgets a
  pending setter change to that key, so a later snapshot cannot overwrite it.
  The setters' own writes go through the same transaction, so a setter in one
  session and a panel save in another serialise too — and they parse the file
  STRICTLY as well: a queued setter used to read a file broken since load as
  `{}` and write its snapshot over it, and it was the write `edit_list` drains
  first (X1). Now it raises, the error is recorded, the file is untouched, and
  the edit fails on the broken file.
- **It raises instead of recording-and-skipping.** An untrusted project, a
  scope whose file did not load (`load_error(scope)`), a file that cannot be
  read or does not parse now, a value under the key that is not a list of
  strings, a legacy `skills` object whose `customDirectories` is malformed
  (which the load migration would delete without a word), and any I/O error.
- **It touches only its key (C19).** Every other key keeps its value and its
  place — it is not migrated — but the file is re-serialised: 2-space indent, no
  trailing newline, numbers and escapes in JSON's canonical spelling (`1.50` →
  `1.5`, `\u00e9` → `é`), as every settings write does (V4). An unchanged list
  writes nothing, a legacy `skills` object included (V3); an empty one removes
  the key. A well-formed legacy `skills` object is read as its directories and,
  when the list changes, replaced by the array, its `enableSkillCommands` kept
  at the top level.
- **Round 2 (R3, R5, R6).** The project-trust check runs again inside the
  transaction, after the lock is taken, so a revocation during a lock wait is
  honoured. A thread cannot be stopped, so a cancelled `edit_list` writes
  nothing if its list has not been built yet (it is told so inside the lock),
  and otherwise lets the write land and updates the in-memory scope when it
  does — memory and disk agree either way. A cancelled setter write is no
  longer recorded as a settings error. On Windows, where a lock-free reader
  holding the file open makes `os.replace` fail, the replace is retried with
  a 5–100 ms backoff for up to 2 s, then the `PermissionError` is raised. A
  file that starts with a UTF-8 BOM loads and edits, as pi's `stripBom` does
  (it is written back without one).
- `read_list(scope, key)` reads one key fresh through the same transaction;
  `scope_path(scope)` names the file. The list getters return `[]` for a value
  that is not a list (before, `"skills": "pdf"` read as `["p", "d", "f"]`).
- RPC `settings/set` for `skill_paths` / `prompt_template_paths` goes through
  `edit_list("global", …)`, and `settings/get` reads the same global list,
  validated (C18). Every other RPC `settings/set` refuses when the global file
  did not load, where the setter would have skipped silently.

The edit for one item (`resource_filter.edit`): drop every `!` / `+` / `-` entry
that names the item exactly (and could reach it, D2); evaluate what is left;
append `-name` / `+name` only if the result still differs from the wanted value
(against the inherited value: on for global, the global file's for a project
edit). A hand-written glob survives (an exact entry beats it); a plain source
entry is never removed (C12), so a skill found only through a custom path can be
turned off, survives a restart as an off row, and is turned back on.

**One save writes one file (C20).** The "Save to" control decides where Enter
writes, and every unsaved edit follows it; there is no half-saved pair of files
to report. **The double toggle (C19)** is a promise about the panel: toggling an
item twice before Enter removes the edit, and an Enter with no edits writes
nothing — no reformat, no project file created.

**Residual (C1).** Two processes with DIFFERENT agent directories
(`AELIX_CODING_AGENT_DIR` / `AELIX_SETTINGS_PATH`) editing the same project file
take different sidecar locks and fall back to the inode lock's guarantee.

### D5. Five states, kept apart

| State | Where it lives |
|---|---|
| unsaved | the panel's model only; lost on Esc |
| saved | a fresh evaluation of both files (D3), read when the panel opens and after a save — never cached |
| applied | the holder's `SkillCatalog.skills` and the harness's `skills`, committed together (D6) |
| pending | saved ≠ applied, not locked — another session or a hand edit changed the file |
| locked | fixed for this run; Space refuses and says why |

The raw inventory (every skill found, shadowed ones included) is
`SkillCatalog.discovered` / `.shadowed`; it changes only on launch, `/reload`
and `/agents use` (C9). A locked row whose saved value disagrees shows both
("Saved off · Locked: forced on by --skill or the agent profile").

### D6. When a change takes effect: one choke point, one exclusive boundary

Skills apply at the next model request, never inside a turn. Enter applies at
once when the session is idle (commands run from the input loop, so it usually
is); when a turn started by an extension or a background child is running, a
task waits for idle, takes a FRESH snapshot and applies that — so of several
saves the newest wins — and commits one line when it has. Skill text already in
the conversation stays, and the panel says so. Every applied change rewrites the
system prompt where the model can see it — the skill catalog is in the prompt
only while the `read` tool is on — so a change usually costs the next request
its cached prompt prefix; with `read` off the prompt bytes may not change at
all (X9).

**A harness build holds the apply off (X2).** The runtime keeps its old harness
pointer while the factory builds the new one, and the factory reads the
holder, installs skills on the candidate and then awaits; an apply landing in
that window updated the holder and the outgoing harness while the candidate was
installed with the old list. The factory now holds an `ApplyGate` for its whole
build (`cli/entry.py` `_harness_factory`); `busy()` is true while it is held,
and the waiter re-checks the live harness when it opens — the runtime installs
the candidate right after the factory returns, with no `await` in between.
`/agents use` is not gated (a command: it runs at idle).

**An unreadable file applies nothing (X3).** A snapshot keeps "this scope is
absent or untrusted" apart from "this file should have been read and could not
be" (`Snapshot.unreadable`). From the latter, nothing is applied — the session
keeps the selection it applied last — because evaluating it as "no entries"
would turn that file's off skills back on; the panel's banner says so, and a
project save, whose inherited value is the global file's, is refused. A
deferred apply takes its fresh snapshot inside its error handling and refuses
the same way when the file broke during the turn; the warning names the file
once ("kept what was applied: <path> is not valid JSON: …", round 2, R6).

**The boundary is exclusive because nothing yields (C8).** `wait_for_idle` only
says the harness WAS idle. The waiter re-checks `busy()` and, when it is false,
takes the snapshot and commits with no `await` in between; `AgentHarness.prompt`
claims its turn synchronously before its first `await`; on one event loop the
two cannot interleave. The kind reads the LIVE harness: a waiter parked on a
harness that `/reload`, `/new` or `/resume` replaced notices within 250 ms and
applies to the replacement.

**Rebuilds read the applied snapshot (C9).** `effective_skills(holder)`
(`core/skill_selection.py`) is the only list `set_skills` and the prompt catalog
take: the factory's first build and every rebuild, the rebuild hook's
`skills_provider`, `/agents use` and its rollback (which restores the previous
applied snapshot, not a fresh resolve — C7), `/reload` and the panel's apply.
The holder's result is a `SkillCatalog`, a `LoadSkillsResult` whose `skills` are
the APPLIED ones, so a tool registration mid-turn rebuilds the prompt from the
same list the harness holds, and a save waiting for idle cannot leak into it. An
AST gate over `packages/` fails on any `set_skills(` call that does not go
through it. RPC `get_commands`, `/skill:<name>` and #508's completions read
`harness.skills` and therefore see enabled skills only.

### D7. Prepare, commit, then the hook bus

The apply (C10):

1. **Prepare.** The candidate catalog goes into the holder and
   `AgentHarness.prepare_system_prompt()` computes the prompt the rebuild would
   install — raising where `rebuild_system_prompt()` swallows. On failure the
   holder is put back and NOTHING is applied; the result says "not applied: the
   system prompt could not be rebuilt", never "applied".
2. **Commit**, synchronously: `set_skills(effective_skills(holder))` and
   `state.system_prompt`, from the same snapshot.
3. **`resources_update`**, the dormant event already in the extension API, via
   `set_resources`: `previous_resources["skills"]` is the list of names the
   session ran with, `resources["skills"]` the new list, and every other bucket
   (`skill_paths`, `prompt_paths`, …) is kept. No new aelix-only event. A
   handler that raises leaves the change applied and is reported as the
   observer's failure.

The save itself is an ordinary settings write.

### D8. Bad entries are said once per launch

On stderr, once per launch (launch only — `/reload` runs under the TUI): an
entry that matches nothing, a plain path that is not a directory, a value that is
not a list of strings or a member that is not a non-empty string (ignored), a
`!glob` past D1's limits (ignored), and a non-empty `themes`, `packages` or
`extensions` key ("not supported yet; its
entries are ignored" — extensions until #25). Under `--no-skills` nothing is
scanned, so "matches nothing" is not checked.

### D9. Same-name skills: `--skill` > project > global > bundled

The first tier that has a name wins; the others are listed in the panel as
locked "shadowed" rows naming the winner. The same file reached twice (a
`--skill` path inside a scanned directory) loads once. This reverses the old
bundled-first scan, which let the shipped `writing-skills` beat a user's own.
`Skill` gains no fields — `tests/pi_parity` pins its shape — so the tier lives in
the catalog's `origins` (by file path); RPC's `_skill_source_info` still reads no
scope from the skill.

### D10. `/reload` rescans; prompts are filtered without a panel

`/reload` rescans the skill and prompt directories and re-reads both lists, as
pi does (the runtime reloads the settings just before the rebuild); the prompt
inventory is a holder like the skills one (C17). Prompt templates use the same
grammar under `prompts` at launch and on `/reload`. A `--prompt-template` path
is forced on, and a FILE named there is that one template — until #511 it was
widened to its directory, which loaded and forced on every sibling (C17). Two
sources reaching one file load it once. There is no `/prompt` panel (owner
decision 13). `PromptTemplate` has no path field (pinned too), so each file is
loaded on its own to keep the path the path entries need.

### D11. The panel (layout A) and its lifecycle

`tui/selection_panel.py`: `SelectionModel` (pure state), `render_panel` /
`SelectionList` and `run_selection_panel` over `show_modal`.

- A grouped checklist — PROJECT, GLOBAL (one group per source directory),
  BUNDLED, THIS RUN — one row per item: `[✓]` / `[ ]`, `(✓)` / `( )` for a
  locked row, the name (12–24 cells), the description, and a status column
  (`* unsaved`, `off · pending`, `project`, `off`, `locked`, `shadowed`). Below a
  rule, a detail strip: the name and path, the description (2 rows at 70 columns
  and up, 3 below), the state line, the key hint. Below 60 columns the
  description column is dropped.
- Keys: ↑/↓ (wrap), PgUp/PgDn, Home/End, Space toggles (a locked row is refused
  with its reason), ←/→ is the "Save to [Global] This project" control (unsaved
  edits follow it; an unwritable scope is refused with its reason), typing
  searches name and description (one token — Space toggles), Backspace edits the
  search, Enter or Ctrl+J saves and applies, Esc or Ctrl+C discards.
- Height is fixed when the panel opens — `min(rows − 7, the modal cap at that
  moment, content)`; the cap reserves the live input and footer rows, not a
  constant (C22) — and every frame draws exactly that many rows, filtered or
  not. If the terminal then shrinks below it, the height follows the cap DOWN
  (never up), the list gives up the rows and the hint and state line stay; below
  one list row the panel says the terminal is too small and refuses every key
  but Esc; with exactly one list row it shows the row the cursor is on, without
  its group header (X8). State, refusal and error lines and the transcript
  notes are flattened like names and paths before they are measured (X7). The width is read per
  frame from `terminal_columns(chrome, max_width=render_max_width)` (ADR-0219),
  not `_PICK_MAX_WIDTH`. Only the visible window is drawn; the cursor's group
  header stays pinned above it, and `(n/total)` shows when the list scrolls.
  Every name, description and path from a file is flattened first (control
  characters shown by name, #179) and cut by display width with `…`, CJK and
  clusters included.
- **Lifecycle (C21, the panel's part).** The panel owns its save, claimed in the
  Enter (or Ctrl+J) key handler itself, before the save task runs: keys buffered
  behind it — a second Enter, an Esc — are refused, so one Enter saves once and
  is never followed by "discarded" (X5). A save that fails leaves the panel open with its edits
  and a red line naming the file; nothing is applied. On success the apply
  (D6/D7), then the summary — the file written, each change, "saved, but this
  project's file keeps it on here" for an edit the other scope still overrides,
  changes applied from elsewhere — is printed and the panel unmounted and the
  input refocused inside ONE `print_above_many` suspend
  (`apply_before_redraw`); `show_modal`'s own cleanup then repeats both
  harmlessly. Esc closes the same way with one line ("N unsaved changes
  discarded." / "closed, nothing changed."), no confirmation. A save that would
  create `.aelix/settings.json` asks first (D3); Esc there goes back to the
  list.
- Banners at the top, fixed when the panel opens: `--no-skills`, the read tool
  being off (the model is then shown no skill list; choices still save), and
  `enableSkillCommands` off; the first settings problem from the fresh read.

### D12. `/skill`, `/skills` and `/skill:<off>`

`/skill` opens the panel; `/skills` is its alias, so both open the same panel.
Without a panel (headless, no settings) the command keeps the old read-only
list. `/skill:<name>` keeps working for an enabled skill; for a skill that exists
but is off it says "<name> is off — /skill to turn it on", and neither expands
it nor says "Unknown command".

### D13. Locks

`--skill` paths and an agent profile's `skills:` (they reach the run as
`--skill`, so they are forced on and locked rows — C14), a same-name duplicate
(shadowed, off) and an untrusted project's summary row (off). Under
`--no-skills` nothing but the command line's skills is scanned, which a banner
says. #510 adds the tool flags; #25 adds the always-on built-ins (Guardrail,
Permission, Status).

### D14. Delegated children resolve their own selection (C14)

A delegated child shares the parent's global settings file (same HOME and agent
directory), but resolves its skills and prompts itself, when it starts, under
its own trust, cwd and profile: it reads the project file only if it runs
trusted — the agents extension launches it with `--no-approve` wherever the
project has trust-requiring resources, a settings file included, so usually it
does not — and its profile's `skills:` are forced on. It never sees the parent's
unsaved panel edits, and a persistent RPC child keeps what it resolved at launch
until it is restarted.

### D15. `defaultTools` is not this grammar unchanged

Plain tool names in pi's `defaultTools` REPLACE the default set, which is a
set-level rule, not the per-item opinion of D1 (C13). #510 writes a set-level
evaluator and editor and adapts its result into `ResourceSelection`.

### D16. What #511 does not own

- **#25 `/extension`:** the extension filter — not in `_discover_entries` alone:
  it needs a global-only phase before the trust vote (user and global
  extensions are imported to vote before project trust is resolved) and a
  trusted-project phase after it, wired into every loader (the factory, the
  vote load, `aelix status`) and the manifest scan (C5); contribution-level
  state — manifest MCP connections dialled once at launch and their tool
  wrappers reused on every build, refusals, live server status, per-contribution
  restart requirements (C16); and `tabbed`'s interactive-tab lifecycle (C21).
  Until then a non-empty `extensions` key is warned about (D8) rather than
  half-applied, and `_discover_entries` is untouched.
- **#510 `/tool`:** the tool setters and the registry mutations that bypass them
  (`set_active_tools` callers, `_refresh_extension_tools`, `set_tools`, the
  profile rollback's active-set assignment) behind one policy boundary (C7's
  tool half); `/reload`'s restoration of the previous active set unioned with
  every extension tool (C11); `defaultTools` (D15, C13).
- **Separate issue (pre-existing, security, C6):** `-e <module>` resolves to a
  same-named directory under an untrusted cwd (`extensions/loader.py`).

## Consequences

- A pi user's `skills` / `prompts` entries work; a malformed or dead one is said.
- A panel save never overwrites a hand edit or another session's save, never
  loses a concurrent session's edit, and never reports a save that did not
  happen; RPC writes of the same lists behave the same.
- The bundled skills no longer beat a user's skill of the same name.
- `/reload` picks up an edited `SKILL.md`, a new skill directory and changed
  prompt templates.
- The first project save puts the folder under project trust (D3).
- Not fixed: #632 — after the panel closes, the input can stay where the panel's
  top was; the fixed height bounds the gap.

## Limits

- D4's residual: different agent directories do not share the sidecar lock.
- The glob is a re-implementation checked against minimatch 10.2.6 on a fixed
  corpus (D1), not minimatch itself; a construct the corpus does not cover may
  still differ. Round 2 measured the rest with the verifier's random
  differential (seed 7, refused patterns left out): 383 of 163,278 pairs
  differ (round 1, on the same pairs: 806), 17 of them with a trailing `/**`; the
  others are extglob corner cases — a repeated extglob's dot rule
  (`*(…)` taking a `.`), an empty alternative (`!(ab|)`), and minimatch's
  handling of an unclosed `[` inside an extglob — none of which a skill or
  prompt name exercises. An empty value is not matched as minimatch does
  (`@(|x)` against `""`); no item has an empty name or path.
- A `!glob` past D1's limits is refused where minimatch would evaluate it.
- Inside those limits, DELIBERATELY BUILT input is still slow — polynomial,
  not bounded small (finish-pass verify, `.omc/specs/511-verify-r2-report.md`
  R-a). Per match: a 512-character `+(*)…x` (`+(*)` 127 times, then `x`) takes
  1.0 s against a 255-character value; `*(*)x` takes 5.2 s against 4,096
  characters (a frontmatter `name:` longer than 64 characters still loads,
  with a warning, and is the identity matched). End to end with 124 skills,
  one such entry makes `aelix -p` take 6.0 s — 10.0 s with a 4,096-character
  skill name — against a 0.6 s baseline, and `/skill` opens in 3.1 s or 4.9 s;
  the snapshot runs on the event loop, so the TUI is frozen meanwhile. Only
  input built for it does this; minimatch itself takes more than 5 s on
  `*(*)x` against 29 characters.
- Many entries times many skills (R-b): 200 realistic entries × 500 skills
  take 6.6 s (round 1: 6.2 s); 200 extglob and `**` entries that match nothing
  × 500 skills take 14.5 s (round 1: 8.4 s) — on everyday extglobs the Python
  table is slower than round 1's C regex.
- A path of 500 segments against about 100 `**` takes 1.4 s (R-c; 50 segments:
  3 ms). Paths that deep do not occur in practice.
- Not done, recorded as future work: cap the extglob groups per pattern; skip
  glob-matching identities longer than 255 characters; memoize `glob_matches`
  within one catalog load.
- `SettingsManager.create` and `reload()` still take the settings file's inode
  lock with no deadline (as before #511): a process that holds it keeps a
  launch waiting. Only transactions (`edit_list`, the setters' writes) have the
  30 s deadline (round 2, R4).
- A symlinked PROJECT settings file is written through to its target, even when
  the target is outside the project. That only happens in a trusted project,
  where the project's files are already the user's to run.
- The Windows `os.replace` retry (D4) is tested by simulating the refusal; it
  has not been measured on a Windows machine with a real concurrent reader.
- A setter for the SAME key (`set_skill_paths`, `set_prompt_template_paths`)
  called while an `edit_list` of that key waits for its lock lands after the
  edit and leaves memory holding the edit's list and the disk the setter's.
  Nothing in `src/` calls those two setters; RPC goes through `edit_list`.
- At launch a settings file that did not load contributes no entries, as every
  other setting does (its error is reported); only a panel apply refuses an
  unreadable snapshot (D6).
- `/agents use` re-applies skills through the choke point but does not emit
  `resources_update`; only a panel apply does.
- A `/reload` does not reprint the D8 warnings.
- A malformed legacy `skills` object is still dropped by the load migration
  (pi parity) — only `edit_list` refuses to write over it.
