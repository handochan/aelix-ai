# 0252. Project settings follow project trust

Status: Accepted (2026-10-06; **#380 amendment 2026-10-21**, §10: the trust question wraps by word, `/trust` shows pi's trust selector)
Date: 2026-10-06
Amends: **ADR-0149** (the gated resource set gains `.aelix/settings.json`), **ADR-0178**
(the "project settings are loaded ungated" premise of its global-only read), **ADR-0250**
§2.10 (the post-`/login` pick's saved default is the global one) and §2.7's settings routes
for a typed `--api-key` (an untrusted project's no longer route it, §9), and closes ADR-0250
§6's "launch's settings default pair" bullet and §7 item 11. Dated notes in each.
Relates: ADR-0203 (`.env` admission; the global settings file is still the user's only
because a `.env` cannot choose it), ADR-0216 (implicit trust after `/reload`, unchanged),
ADR-0235 (pi is the reference, not a parity mandate), #367 (the late-registered path).
Issue: #369 (from #362 item 11). Owner direction: 2026-10-02 (the pi direction for the
batch); the choices the research left open were made by the main loop under it on
2026-10-06 (§6). Review rounds: §7, §8.
pi: `89a92207f` ("feat(coding-agent): add project trust gating", 2026-06-05). Line
citations below are pi @ `b223082bb`; the trust code is unchanged since `88ff80b98`
(`git diff 88ff80b98 b223082bb` touches a doc comment in `settings-manager.ts` and one
`main.ts` line, neither about trust).
Tests: `tests/settings_manager/test_settings_manager_project_trust_369.py`,
`tests/cli/test_project_settings_trust_369.py`, `tests/tui/test_project_settings_trust_369.py`,
`tests/tui/test_post_login_pick_362.py` (premise moved, §4).

## 1. What was measured

On `5dee21d1`, the real CLI in print mode, fake keys, an isolated agent dir, a scratch cwd
and a local CONNECT recorder as `HTTPS_PROXY` that answers 403
(`.omc/probes/369-live/impl/base_sweep.out`). The project's `.aelix/settings.json` is
`{"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}`, the user's own
`OPENROUTER_API_KEY` is exported, and the project's `.env` holds `OPENAI_API_KEY`:

| Row | Flags | `5dee21d1` |
| --- | --- | --- |
| S1 | none | `CONNECT api.openai.com:443` (the repo's key) |
| S3 | `--no-approve` | `CONNECT api.openai.com:443` |
| S1A | `--approve` | `CONNECT api.openai.com:443` |
| S2 | none, no `.env` | no request, "No API key found for OpenAI." |
| S7 | the pair in the user's GLOBAL settings | `CONNECT api.openai.com:443` |
| S0 / S0B | no project file / `{}` | no request, "No model selected." |

S3 is the defect: the trust answer did not matter. Two reasons, both read and then
measured:

1. `SettingsManager` had no trust state. `from_storage` and `reload` always read the project
   file, and the `set_project_*` setters always wrote it (`SettingsManager.create(...,
   project_trusted=False)` raised `TypeError`).
2. `has_trust_requiring_project_resources` did not list `settings.json`, so a repo whose
   only `.aelix` resource is its settings file was trusted at step 2 with no prompt. Gating
   the manager alone would have closed nothing.

Two laundering paths carried a repo's settings into the user's GLOBAL file:

- **The post-`/login` pick** read the merged pair and persisted its choice globally. In a
  pty (`base_tui.out`, `f1-login-openai`: `--no-approve`, the project pair, `.env`
  `OPENAI_API_KEY`, an unrunnable launch, `/login` storing the user's own OpenAI key) the
  global settings ended with `"defaultModel": "gpt-4o-mini"` — the repo's. Without the
  project file: `"gpt-5.4"`.
- **`aelix extension source add`** (`base_ext.out`): a project file listing
  `{"extensionSources": [{"spec": "https://repo-index.invalid/simple", "kind": "index"}]}`
  made `source list` show that index and `source add <dir>` write it **and** the new path
  into the global `settings.json`. `install` folds registered indexes into pip's
  `--index-url` (read, not run).

And `/trust` said "Run /reload to apply it to project-local resources." Measured in a pty
(`base_trustreload.out`): a project prompt template, "Do not trust (this session only)" at
launch, `/trust` → Trust, `/reload`, then `/hello` → "Unknown command: /hello". Control
(`base_trustlaunch`): "Trust (this session only)" at launch, `/hello` sent a turn. The
process's trust is decided once in `cli/entry.py` and passed by value into every rebuild.

## 2. Decision

### A. `SettingsManager` takes the trust (pi `settings-manager.ts`)

- `create(cwd, agent_dir, *, project_trusted=True)`, `from_storage(storage, *,
  project_trusted=True)`, `in_memory(settings, *, project_trusted=True)` — pi's
  `SettingsManagerCreateOptions` (`:269-271`), default TRUSTED as pi's `projectTrusted ??
  true` (`:442`), so embedders and unit tests keep the merged view.
- An untrusted project scope is empty **before any storage access** (`:473-476`): the file
  is never opened, flock'd or parsed, so a malformed untrusted file reports nothing.
- `is_project_trusted()` / `set_project_trusted(trusted)` (`:578-603`): a no-op when
  unchanged; clears the project modification sets; untrusted → the scope is emptied and its
  load error forgotten; trusted → the file is read now and a load error recorded for
  `drain_errors()`. `reload()` keeps the flag (`:623`).
- Every project write refuses with pi's text, "Project is not trusted; refusing to write
  project settings" (`:662-666`): first in `_save_project_settings` and in each of the five
  `set_project_*` setters (before the copy, as pi's `updateProjectSettings`), and again
  inside the queued write (pi `enqueueWrite`, `:683-694`), so a write queued while trusted
  and run after `set_project_trusted(False)` is refused and recorded, not performed.
- `get_extension_sources` / `get_suppressed_default_catalogs` read the GLOBAL scope only.
  Their comments always said "GLOBAL-scope only"; the code read the merged view. They are
  aelix-original (no pi oracle), and global-only holds whatever the trust — a TRUSTED repo
  must not register a user-level install source either (the `get_features_agents`
  precedent). This closes the `source add` laundering.

### B. `.aelix/settings.json` is a trust-requiring resource (pi `trust-manager.ts:30-39`)

`has_trust_requiring_project_resources` returns `True` when anything exists at
`cwd/.aelix/settings.json`, as pi (`existsSync`, `trust-manager.ts:192`). (Round 1 used a
file test; a FIFO there then skipped a saved denial — §7.) The prompt names it, in a third
clause because it carries a third risk:

> This allows Aelix to load .aelix extensions, MCP servers, and agent profiles, which can
> execute arbitrary code on your machine — to load .aelix skills and prompt templates, whose
> text is placed into the agent's instructions — and to apply .aelix/settings.json, which
> can choose the model and provider your prompts are sent to.

The headless notice names it too: "Notice: project-local .aelix resources (extensions,
skills, agent profiles, settings) skipped in an untrusted directory; pass --approve to
trust." `aelix_agents.trust.child_trust_argv` calls the predicate, so a delegated child in a
settings-only repo now runs with `--no-approve`.

**Consequence (owner-visible):** every repository carrying `.aelix/settings.json` now asks
once interactively, and is denied under `-p`/`--mode json`/`--mode rpc` until `--approve`
or a saved decision — as in pi. Such a directory also pays the user/global extension
vote-load at launch (ADR-0178), as pi does.

### C. The CLI builds untrusted, then decides (pi `main.ts:736-748`, `resource-loader.ts:520`)

`cli/entry.py` creates the run's manager with `project_trusted=False` and calls
`set_project_trusted(project_trusted)` immediately after `_resolve_project_trust`, then
reports any project load error. Between the two, the only read is the global-only
`get_default_project_trust()`; everything after — the default-pair seed and
`profile_baseline` back-fill, the `default_provider` every rebuild gets, the harness options,
`harness.reload`, `run_tui` and RPC — sees the decided view with no further edit. A reader
added between the two would see an empty project scope, which fails safe.

`--list-models` builds untrusted and resolves trust without a prompt, in
`resolve_project_trusted`'s order (pi `project-trust.ts` `resolveProjectTrusted`):
`--approve` / `--no-approve`; then a directory with nothing gated
(`has_trust_requiring_project_resources` false) is trusted — step 2, before any saved
decision or default is consulted; then a saved `trust.json` decision, the global
`defaultProjectTrust`, otherwise untrusted — pi's print-mode trust. The default is read from the untrusted manager, so a
project-scoped `defaultProjectTrust` cannot count. No `project_trust` extension vote: nothing
is loaded on that path (a stated narrowing). When the decision is untrusted and the
directory has a gated resource, it prints the run's headless notice to stderr (aelix-original;
pi prints none), so an unscoped list is distinguishable from a repo that scopes nothing;
stdout carries only the table.

The extension CLI (`extension_install._load_settings`) builds with `project_trusted=False`
explicitly and never resolves trust: it reads and writes only global-scope fields. pi's
package CLI also builds untrusted (`package-manager-cli.ts:750`), but then resolves trust
for **every** package/config command — vote extensions, `resolveProjectTrusted`,
`setProjectTrusted`; `update` uses the saved decision only (`createCommandSettingsManager`,
`:743-789`) — because those commands read the project's `packages`; and it refuses a `-l`
install/remove when the project is untrusted (`:931`, `:940-944`). aelix's extension CLI
reads no project-scoped field, so there is nothing for a trust decision to unlock. (Round 2
corrected the round-1 text here, which said pi resolves trust "only for its project-scoped
(`-l`) writes" — false at `b223082bb`.)

Every production `SettingsManager.create/from_storage/in_memory` call passes
`project_trusted`; a test walks the source tree's AST and pins it.

**Not copied:** pi's `startupSettingsManager` (`main.ts:669`) is created with the default
(trusted) and read for first-time setup, theme and `sessionDir` before trust is decided.
aelix has no pre-trust reader, so there is nothing to "restore parity" with there.

### D. The post-`/login` pick reads the GLOBAL saved default (`tui/shell.py`)

The pick is persisted to global settings, so it starts from the user's own saved default
only (`get_global_settings()`), never the merged pair. An untrusted repo's pair was already
excluded by A/C; this also keeps a TRUSTED repo's pair from being laundered into the
default every other directory uses. ADR-0250 §2.10's divergence (b) — honouring a saved
default pi's login path ignores — stays, for the user's own default. pi never reads settings
there (`completeProviderAuthentication`), so for a project pair this is pi's outcome.

### E. `/trust` saves for the next launch, and says so (pi `interactive-mode.ts:5299`)

No in-session re-read (the pi direction). The message is now "Project {verdict} ({scope}):
{target}\nRestart aelix for this to take effect.", and `/trust` offers no "this session
only" answers (pi's selector, `trust-selector.ts:44`, calls `getProjectTrustOptions(cwd)`
without them) — one would change nothing. The startup selector keeps them. ADR-0216's
implicit trust after `/reload` is unchanged: a session that started trusted (nothing to
gate) re-reads a `settings.json` that arrives by `git pull`, as pi's reload keeps the trust
it started with.

### F. A trusted project's pair is applied (the reinforcement is declined)

The issue offered an optional reinforcement: apply guard 1 to a TRUSTED project's pair when
it points at a provider only a `.env` authenticates while the user holds a credential of
their own. **Declined** (owner direction, 2026-10-06), and S1A (`--approve`) still reaches
`api.openai.com` on the `.env` key:

- pi treats a trusted project's settings as the user's, and has no `.env` to tell apart;
- guard 1 forbids a credential choosing a route — here the trusted file chose it and the
  key only authenticates it;
- ADR-0250 §6 already records the same shape for a trusted project's agent profile
  `provider:` (S10a) as inside what trusting the project grants;
- trust already grants code execution through extensions and MCP.

It is a residual, recorded in `docs/guides/project-trust.md` ("Project settings follow the
answer").

## 3. Before and after

`.omc/probes/369-live/impl/` (`after_*.out`, same driver, the tree at this commit):

| Row | `5dee21d1` | after |
| --- | --- | --- |
| S1 (no flag) | `api.openai.com` | no request; the notice, then "No model selected." |
| S3 (`--no-approve`) | `api.openai.com` | no request |
| S2 (no `.env`) | "No API key found for OpenAI." | no request, "No model selected." |
| S1A (`--approve`) | `api.openai.com` | `api.openai.com` (F) |
| S7 (global pair) | `api.openai.com` | `api.openai.com` |
| `extension source list` / `add` | lists the repo index; global file gets index + path | no repo index; global file gets the path only |
| pty `f1-login-openai` | global `defaultModel "gpt-4o-mini"` | `"gpt-5.4"` |
| pty settings-only repo, interactive | no prompt; footer `gpt-4o-mini` | the selector with the settings clause |
| pty `/trust` → Trust | "Run /reload to apply it …" | "Restart aelix for this to take effect."; three options |

## 4. Tests and sabotage

New rows are RED on `5dee21d1` and GREEN here (`red_on_base.out`). `tests/tui/
test_post_login_pick_362.py::test_the_login_command_picks_through_the_route_auth_view`
moved its `openai/gpt-4o-mini` pair from the project file to the GLOBAL settings: after D a
project pair never reaches the pick, which would have left the saved-default arm empty and
the raw-registry sabotage (ADR-0250 §4 round 5) green.

Sabotage, each piece reverted alone on a throwaway worktree, against the five files above
plus `tests/cli/test_project_trust.py` (102 rows; `.omc/probes/369-live/impl/sabotage.out`).
Every piece turns at least one row red:

| Piece reverted | Red rows |
| --- | --- |
| A: untrusted load returns the file | 14 (manager, S1/S3, `--list-models`, global-only `defaultProjectTrust`) |
| A: `set_project_trusted(True)` does not read | 9 (`--approve`, saved decision, global `always`, list) |
| A: `set_project_trusted(False)` does not clear | 1 |
| A: `reload` ignores the flag | 1 |
| A: setter + save write refusals | 5 (each setter) |
| A: queued-write refusal | 1 |
| A: `get_extension_sources` / `get_suppressed_default_catalogs` merged | 1 each |
| B: predicate clause | 7 (predicate, prompt/deny, child argv, S1/S3, list) |
| B: prompt clause | 1 |
| C: run manager built trusted | 2 (malformed-file report, construction-site pin) |
| C: no `set_project_trusted` after the decision | 4 |
| C: `--list-models` without resolution / built trusted | 2 / 1 |
| C: headless notice text | 2 |
| C: extension CLI built trusted | 1 (construction-site pin; its getters are global-only anyway) |
| D: post-`/login` pick reads the merged pair | 1 |
| ADR-0250 V1c (the view replaced by the raw registry) | 2 (`test_post_login_pick_362`, both halves) |
| E: the `/trust` message / its session-only options | 1 / 1 |

## 5. What this does not close

- A trusted project's pair plus a `.env` key (F).
- A typed `--api-key` on a settings route that names OpenRouter (ADR-0250 §2.7): from the
  global settings or a TRUSTED project's file it still goes to `openrouter.ai` — the user's
  own choice (§9).
- `/trust`'s in-TUI selector shows the first line of the prompt only (pre-existing; the
  startup selector shows all of it). Closed by §10 (#380): `/trust` shows pi's trust
  selector rows instead.
- pi's `.agents/skills` ancestor walk and its `themes`/`SYSTEM.md`/`APPEND_SYSTEM.md`
  resources are still not in aelix's set (no loaders).

## 6. Owner decisions recorded here

Made by the main loop on 2026-10-06 under the owner's pi direction (2026-10-02), each the
research's recommendation: A (untrusted → neither read nor written), B (settings.json gated,
prompt names it), C (build untrusted, decide, then set; `--list-models` non-interactive),
D (global-only post-`/login` default), the global-only install-source getters, E (no
in-session re-read; the message made true), F declined, explicit trust at every production
construction site. Chosen in this lane: `/trust` drops its session-only answers (E); the
extension CLI passes `project_trusted=False` rather than resolving trust (C).

## 7. Review round 1 (2026-10-06)

An independent verify pass and a Codex cross-review of the first commit (`67281070`) found
the following; each is fixed in the amended commit, with a row that is RED on `67281070`
or under a named sabotage (`.omc/probes/369-live/fix2/`).

- **`--list-models`' trust order was not pinned.** Dropping the `default_project_trust`
  argument, or collapsing `"never"` into `"always"`, passed every test
  (`b1_repro_on_67281070.out`: "46 passed" under both). New rows: global `always` → scoped
  list; global `never` → unscoped and the project file not opened; a project-scoped `always`
  → ignored; a saved denial and `--no-approve` each beat a global `always`.
- **A FIFO (or a directory) named `settings.json` skipped the trust requirement**, so a
  saved denial never applied and the loader opened it (`c1_fifo_on_67281070.out`:
  `predicate=False`, `resolved_trust=True`, one open, "Warning: settings (project): [Errno
  45] Operation not supported"). The predicate now tests existence (§B); the untrusted
  manager never touches the path (§A, unchanged), pinned for a FIFO with a saved denial
  under `-p` and `--list-models`.
- **§C's description of pi's package CLI was false** ("resolves trust only for its `-l`
  writes"); corrected above, and in `extension_install._load_settings`.
- **`--list-models` said nothing when it skipped a repo's `enabledModels`**; it now prints
  the headless notice to stderr (§C).
- Docs: `project-trust.md` named the global-only exceptions to "every other setting"
  (`defaultProjectTrust`, `features.agents`, `extensionSources`,
  `suppressedDefaultCatalogs`, `respectGitignore`), counted four directory entries (was
  "three", pre-existing), and re-derived two stale `trust.json` citations onto gated full
  citations.

Owner choices recorded in §6 stand.

## 8. Review round 2 (2026-10-06)

The verify pass of the amended commit (`890c982a`) found one unpinned conjunct. The
headless notice is printed when the decision is untrusted **and**
`has_trust_requiring_project_resources` holds; removing the second conjunct, at
`--list-models` or at the run's own notice, passed every test
(`.omc/probes/369-live/fix3/mut_on_890c982a.out`: "3958 passed" under each), because the
only nothing-gated row ran with no flag and an ungated directory is trusted at step 2.
New rows, on both surfaces, in a directory holding only an empty `.aelix/`: `--no-approve`
(untrusted, step 1) → no notice, RED under each mutant; a global `"never"` and a saved
denial → no notice, each with its decision asserted — they are trusted there, because
step 2 precedes the saved decision and the default (§C, the `--list-models` order;
`cli/project_trust.py` `resolve_project_trusted` steps 2, 4 and 5), so they guard the order
rather than the conjunct. Nothing else changed. (§C named that order without step 2 until
the round-3 verification pointed it out; the step was added there on the rebase onto
`62e2238b`.)

## 9. Rebased onto #370 (2026-10-06)

#370 (`a7435b9f`) landed first and wrote ADR-0250 §2.7 and §6 for an aelix that still read
an untrusted project's `.aelix/settings.json`: a project file's `defaultProvider:
openrouter` or its pair `openrouter` + `<id>` took a typed `--api-key` to `openrouter.ai`
"with or without `--approve`", until #369. Both notes now state the merged behaviour,
re-measured on the rebased commit with #370's own probe
(`.omc/probes/369-live/rebase2/settings-routes-head.txt`, in process, recorders): the four
untrusted project rows attach nothing and send nothing (the `defaultProvider` row is pi's
not-found; the pair row has no model: `--api-key requires a model`), while the global rows
and the `--approve` project rows — 8 of 12 — still send the typed key there, the stated
residual of §5.

## 10. Amendment 2026-10-21 (#380): the trust question wraps by word, `/trust` is pi's selector

Owner decision (issue comment, 2026-10-21): as pi. Measured on main `27fa21c5` in a pty
(`.omc/probes/b4-b-live/380/before/`, fake keys, a CONNECT recorder that answers 403):

- the start-up selector (`cli/entry.py` `_prompt_one_shot_select`) handed prompt-toolkit
  one string in a `Window(wrap_lines=True)`, which wraps by cell: at 120 columns the
  body read `… on your m` / `achine` and `… apply .` / `aelix/settings.json`
  (`fresh.120.0_prompt.screen`), at 80 `wh` / `ich`, `promp` / `t`, `se` / `nt`;
- `/trust` (`tui/shell.py` `_open_trust`) passed the same body to aelix's own picker
  frame, which does not wrap (ADR-0253 §11.3), so it showed the heading, the path and the
  paragraph's first row cut at the screen's edge (`fresh.120.2_trust_open.screen`) and
  never what was saved;
- `/trust` → `Trust parent folder` after a saved `Do not trust` on the folder wrote the
  parent only; the folder's `false` stayed nearer and outranked it, so the message said
  "Project trusted (saved)" and the folder stayed untrusted
  (`saved-untrusted-parent`: `trust.json` held the parent `true` and the folder `false`).

Decision:

- **The start-up selector cuts its rows after a space**, at the width of each paint (a
  resize re-wraps), as pi's `ExtensionSelectorComponent` (`Text`, `wrapTextWithAnsi`)
  does: `_one_shot_rows` runs every line through `tui/context.py`'s `_title_rows` (the
  approval prompt's `_cut`, by grapheme cluster), which also draws a character
  `safe_for_terminal` would remove by its name in reverse video. A word wider than the
  row (a path) is cut where the row is full; its indent ends a row of its own, as pi's
  `wrapSingleLine` pushes the whitespace before breaking a long word. A line that fits is
  drawn as before. The project-agent confirmation (ADR-0196) and the session-in-use
  question (#137) use the same widget and wrap the same way. `wrap_lines=True` stays as the
  net for a row prompt-toolkit measures wider than `_cut` did.
- **`/trust` shows pi's `trust-selector.ts` rows** (pi `fdce48eb4`): `Project trust`, the
  project path, `Saved decision: <trusted|untrusted> (<path>)` — `(inherited from
  <path>)` when an ancestor's entry decided, `none` when nothing did (pi `formatDecision`)
  — and `Current session: trusted|untrusted` (pi reads `isProjectTrusted()`; aelix
  `SettingsManager.is_project_trusted()`, set from the launch decision by §C). The options
  carry pi's mark column: `✓ ` on the option whose first write is the entry's directory and
  decision (pi `isSavedOption`), two spaces on the others, and the cursor starts on it.
  The risk text stays on the start-up question, which is the consent; `/trust` changes a
  decision already made. `ProjectTrustStore.get_entry` returns the deciding directory with
  the decision (pi `getEntry`).
- **Divergences, knowingly (ADR-0235).** pi draws a blank row under the path; aelix's own
  frame draws its rule under the title, and four title rows is the height `/trust` had,
  so at 80x16 `Do not trust` and its counter stay on the modal (ADR-0253 §11.3's reason
  for not wrapping own pickers). pi's `Text` wraps every row; aelix cuts the title rows
  (`_own_title_rows`, the same `_cut`) to the width `/trust` opens at, because the saved
  decision repeats the path: at 80 columns a 62-character path ran `Saved decision:
  untrusted (/private/…` off the edge. A row wider than the screen therefore adds rows; at
  80x16 with such a path the key hint is the row the modal cuts (`Do not trust` and the
  counter stay), and a terminal that narrows while `/trust` is open clips as every own
  picker does. The option rows themselves are not wrapped (the own frame's rule).
- **`Trust parent folder` writes pi's `updates`**: the parent `true` and the folder's own
  entry deleted, in one atomic write (`ProjectTrustStore.set_many`, pi `setMany`;
  `trust_option_updates`, pi `ProjectTrustOption.updates`). The start-up path is
  unchanged: it asks only when no entry from the folder up is decided, so there is no
  folder entry to clear.
- **The one exception to ADR-0253 §11.3's "aelix's own pickers are main's, byte for
  byte".** `/trust` still passes `own=True` and is drawn by `_picker_frame` with main's
  keys; only the title and option text it passes moved. Every other own picker is
  unchanged (`tests/tui/test_select_title_held.py`, its `/trust` case now pi's rows).

Tests: `tests/cli/test_one_shot_select_wraps_words_380.py` (the cut at 120, 80 and 40
columns, the measured breaks gone, a long path, rows that fit unchanged, an ESC named,
the real widget painted through pyte at 120 and 80, the real widget painted at 120 and then,
after the output narrowed, at 50, and a TUI whose row cutter cannot be imported answering
`None`, the deny, before the app runs) and
`tests/tui/test_trust_selector_380.py` (the saved-decision words and mark for none, the
folder, its parent and a grandparent, `get_entry`, the parent update, `/trust` in a running
TUI at 80 and 120 columns with an untrusted and a trusted session, and the rows in the own
frame). Each fix piece reverted, and four wrong forms (no cut, a cut by cell, `textwrap`, a
fixed 78-column cut in `_one_shot_rows`), turns a row red (`.omc/probes/b4-b-live/380/sabotage.out`, 17 of 17).
Review round 2: the width `_render` reads was pinned by no row, so a fixed 78 or the
process terminal's width (`shutil.get_terminal_size`) in its place passed them all; the
120-then-50 row is red on both (`.omc/probes/b4-b-live/r2/`).
After the change in a pty at 120, 80 and 80x16: `.omc/probes/b4-b-live/380/after/`.
