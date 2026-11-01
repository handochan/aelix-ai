# 0285. A module name the user chose is never answered by the project folder

Status: Accepted (2026-11-01; D1–D4 #641, D5–D6 #638)
Date: 2026-11-01
Issue: #641 (from the Codex cross-review of the #510/#25 design, F01) and #638
(the #511 design's Codex review, C6; ADR-0279 D16 left it to a separate issue).
Batch B6 (security), owner-approved 2026-11-01; one lane, one commit per issue;
lands before #25, whose pre-trust filter (ADR-0279 D16, C5) relies on it.
Amends: ADR-0197 §(l) (the child's command line gains `-P`), ADR-0252 (dated
notes §13 and §14: the boundary it draws is now held for imports by name, and
for a `-e` name a project path answers), ADR-0149 (dated note: explicit `-e` is
gated in one case), ADR-0279 D16 (dated note: C6 is closed here).
Relates: ADR-0255 §17 (#394: pip children have used `python -P -m pip` since
2026-10-11 — the precedent), ADR-0149 (explicit `-e`, global and installed
extensions are the user's choice), ADR-0279 D16, #114 (the `-e <module>` form and
the guard this generalises; it has no ADR of its own), #25.
pi: `earendil-works/pi@0baf611f0`.
Tests: `tests/extensions/test_project_module_imports_641.py`,
`tests/agents_ext/test_child_safe_path_641.py`; the four-token launch prefix in
`tests/agents_ext/test_child_argv_contract.py` and its siblings;
`tests/extensions/test_untrusted_shadowed_entry_638.py`.

## Context

The loader imports three things **by name**: `-e <module>` (#114), a manifest's
`[plugin.entry] python = "mod:setup"` and an `aelix.extensions` entry point's
module. A name means "the module this installation can import". #114 found that
`python -m` puts the cwd at `sys.path[0]` and added `_import_path_without_cwd`,
but only around the first of the three.

Every delegated child was started as `[sys.executable, "-m",
"aelix_coding_agent", …]` with the project folder as its cwd, by both channels.
So in a child, a project file named like a global pack's module or an entry
point's module was imported in its place, whatever the project's trust: the
child's trust flags decide which `.aelix/` resources load, and this import is
not one of them. The parent, started from the `aelix` console script, has the
script's directory at `sys.path[0]` and was not affected.

Measured on `main` `0e97bd28` (macOS, the worktree's venv, a scratch HOME). A
global pack `voterpack` with `python = "review_voter:setup"`; a manifest-less
entry point `review_ep = review_ep:setup` from a dist in the (scratch) user
site, which the #91 provenance gate trusts; both genuine modules on
`PYTHONPATH`; and `review_voter.py`, `review_ep.py` in the project folder that
record themselves and write a sentinel. A real `aelix -p --agents` parent on
OpenRouter `anthropic/claude-haiku-4.5` delegated once to a user profile with
`inherit_extensions: true`; the production `RpcChannel` ran the same profile:

| Process | argv | `review_voter` | `review_ep` |
| --- | --- | --- | --- |
| parent | `.venv/bin/aelix --agents …` | genuine | genuine |
| print child | `python -m aelix_coding_agent --mode json …` | **project** (sentinel) | **project** (sentinel) |
| rpc child | `python -m aelix_coding_agent --mode rpc …` | **project** (sentinel) | **project** (sentinel) |

Both delegations completed (`PONG`); nothing told the user which file had run.
The Codex probe (`.omc/specs/510-25-design-codex-probes/pretrust.py`, a
simulated `python -m` `sys.path`, not a spawned child) had shown the stake for
a process that votes: the project's `review_voter` answered the `project_trust`
vote and turned the decision to `True`. A child does not vote — it gets
`--no-approve` wherever there is something to gate — but it runs the file all
the same.

pi has neither import. Its `-e acme` is a path relative to the cwd and an
installed package is spelled `npm:acme` (`package-manager.ts` `parseSource`,
`:1592-1616`; `extensions/loader.ts` `loadExtension` resolves the path,
`:644`). The module-name form is aelix's (#114), so the rule is aelix's to set.

#638 is the other half of the same question, one step earlier. `_is_module_ref`
clause 4 lets a path that exists win over the module reading (#114 kept it so
that `-e mypkg.ext` with `mypkg.ext/` on disk still loads from disk), and an
explicit `-e` loads outside the `no_project_local` trust gate. So in a folder
holding `acme/`, `-e acme` meant that folder, trusted or not. Measured on the
same base (the console script, a git repo whose `acme/__init__.py` records each
execution, OpenRouter `anthropic/claude-haiku-4.5`):

| Run | `acme/__init__.py` executions |
| --- | --- |
| `-e acme --no-approve` | 1, after the #114 warning |
| `-e acme --approve` | 1, after the warning |
| `-e acme` (nothing to gate, so trusted without a question) | 1, after the warning |
| `-e acme`, the repo has `.aelix/settings.json`, `-p` | **2** — the pre-trust vote load, then the load after `-p` denied the folder |
| `-e ./acme --no-approve` | 1, no warning |

pi reads every bare `-e` as that path (above), trusted or not; it has no module
reading to be confused with.

## Decision

### D1. Every `python -m aelix…` child starts with `-P`

`-P` (Python 3.11, the floor) does not prepend the cwd for `-m`. The sweep of
`packages/` for `sys.executable` with `"-m"`:

| Site | Child | `-P` |
| --- | --- | --- |
| `aelix_agents/print_channel.py` `build_child_argv` | every print-channel delegation | **added** |
| `aelix_agents/rpc_channel.py` `build_rpc_child_argv` | every rpc-channel delegation | **added** |
| `aelix_coding_agent/rpc/rpc_client.py` `_build_argv` default (`-m aelix --mode rpc`) | an embedder's RPC server, in the embedder's cwd | **added** |
| `rpc_client.py` `cli_path` form (`python <script>`) | a caller's shim | no — `-P` would drop the script's own directory, which is what a shim imports beside; that directory is not the cwd |
| `cli/extension_install.py` pip install / uninstall / download | pip | already (#394) |

No server or extension subprocess spawns Python by module; `aelix-server`
spawns nothing of the kind, and subprocess hooks and MCP stdio servers run the
command their declaration names.

`-P` is not isolation: `PYTHONPATH`, `.pth` files and user site still apply,
as ADR-0255 §17 says of pip. `build_child_env` prepends aelix's own source root
and drops empty `PYTHONPATH` segments, so it adds no cwd. It rebuilds the
variable every time: the first cut rebuilt it only when the root was missing,
so `'<root>:'` reached the child as it was, and that empty segment put the cwd
on a `-P` child's `sys.path`, where a project `sitecustomize.py` ran at startup
(the #641 verify, V4).

### D2. The three imports by name go through one function

`_import_module_outside_cwd(name, *, cwd)` replaces `importlib.import_module`
in `_factory_from_module` (used by `-e <module>` and by every
`[plugin.entry] python`, directory tier and bound entry point alike) and
`_factory_from_entry_point` (a manifest-less endpoint, and a bound one whose
manifest names no entry). Both take the loader's `cwd`; lazy activation reaches
them through `_resolve_factory` with the pending entry's `cwd`. It:

1. refuses a name already in `sys.modules` that was found through the project
   folder (D3) — `import_module` returns a cached module without reading
   `sys.path`, so the path guard alone would hand it back;
2. imports without the entries that put the project folder on `sys.path`
   when the process started (D4).

This is the guard a user who runs `python -m aelix_coding_agent` themselves
still needs, and the one a library caller gets; children no longer depend on
it (D1). The refusal is an `ImportError` reported as the pack's load error; the
pack does not load and its `setup()` does not run:

```
'review_voter' is already imported from the project directory
(<cwd>/review_voter.py), so Aelix will not use it for 'review_voter': …
start aelix with its installed command, or install the module.
```

### D3. What a "project module" is

A cached module is a project module when its file (a plain module) or any of
its package directories (a regular or a namespace package) sits **directly**
in the process cwd or the loader's `cwd`, and that folder is on `sys.path`
only through an entry D4 removes. That is exactly the set D4 stops a fresh
import from producing. Every dotted prefix of the name is checked, so a cached
project `pkg` refuses `pkg.ext`. A module found through a folder the user's
environment put there — the flat editable install of D4, imported by the vote
load or by the build before a `/reload` — is what a fresh import would find
too, and is reused.

Rejected: "its file is anywhere under the cwd". Aelix installed into a
virtualenv inside the project (`./.venv/lib/…/site-packages`) would then refuse
every installed module, and an extension author's editable install (`./src` on
`sys.path`, which D4 does not remove either) would refuse their own pack. A
module with no recorded location (built-in, frozen, injected by hand) cannot be
placed and is not refused.

### D4. The guard removes what the launch put there, compared as directories

#114 removed entries equal, as strings, to `''`, `os.getcwd()`, `str(cwd)` and
`str(cwd.resolve())`, wherever they sat. Now two kinds of entry are removed,
and only these (`_launch_entries`):

- `''`, wherever it sits — what `python -c` and the REPL insert, meaning "the
  cwd at lookup time";
- the entries the interpreter's launch inserted, when they name the process
  cwd or the loader's `cwd`: `sys.path[0]` unless `sys.flags.safe_path` is on —
  what `python -m` (the cwd) and `python <script>` (the script's folder)
  insert, and `-P` leaves out — and the cwd, once, when `PYTHONPATH` holds an
  empty segment, which Python reads as the cwd (`export
  PYTHONPATH=$PYTHONPATH:/x` writes one when the variable was unset), unless a
  `.pth` file in a site directory names the cwd as well. `site` drops a `.pth`
  line naming a folder already on `sys.path`, so there the one entry is the
  `.pth`'s too and stays: without that exception the shipped `examples/starter`
  (below) run from its root under `PYTHONPATH=':<dir>'` failed with "No module
  named 'aelix_starter'", where base and round 1 loaded it (measured in fix
  round 2).

The loader records these values when it is imported (`_LAUNCH_PATH_ENTRIES`),
before any extension runs, and at each import by name removes the first entry
equal to each value, wherever it now sits. Fix round 1 looked only at whatever
sat at `sys.path[0]` when the import ran, and the #641 round-1 verify (R1)
found three real launches where the cwd was elsewhere and the project file
answered, all of which base and the first cut had caught: a console script with
`PYTHONPATH=':site'` (the cwd at index 1, after the script's folder), `python
-m` after a user-global file extension's `sys.path.insert(0, …)`, and `python
-m` with `PYTHONPATH=':site'` (the cwd twice). Each imports the installed
module again. `site` puts `.pth` entries after the launch's own, and the
launch's `sys.path[0]` is inserted after `site` has run, so the first equal
entry is the launch's.

Everything else on `sys.path` is the user's environment — a folder
`PYTHONPATH` names, a `.pth` file in site-packages — which a repository cannot
write, and stays even when it names the cwd. The first cut removed every entry
naming the cwd, and broke the documented workflow for the repo's own
`examples/starter`: a flat-layout `pip install -e .` writes a `.pth` naming the
project root, so run from that root the pack's module was taken off the path
and failed with "No module named 'aelix_starter'" (the #641 verify, V1).

"Names the project folder" compares directories, not strings: the keys are
`normcase(abspath(entry))`, `normcase(realpath(entry))` and the directory's
`(st_dev, st_ino)` when the filesystem reports an inode. So `'.'` (relative
entries mean the process cwd at import time), a trailing separator, `sub/..`,
a symlinked spelling (`/tmp` → `/private/tmp`), another case (on macOS's
case-insensitive default volume `normcase` folds nothing and `realpath` keeps
the given case, so only the inode sees it — Codex review PC2) and, on Windows,
`/` instead of `\` are all the project folder. Only the folder itself:
`./src` stays.

### D5. A module-shaped `-e` that names a project path loads only in an explicitly trusted folder

In tier 3 of `_discover_entries`, a `str` entry that is module-shaped
(`_is_module_shaped`) but was claimed by clause 4 is refused unless the folder
is **explicitly** trusted — owner decision 2026-11-01, replacing the first
cut's "refused when untrusted". Explicit means the user said yes to this
folder, in one of three ways (the second owner decision of 2026-11-01 added
the third):

- `--approve`;
- a saved trust decision of yes for the folder **or a parent folder** —
  `trust.json` is read nearest ancestor first, as for the resolution, so the
  prompt's "Trust parent folder" counts for every folder below it, and a
  child's saved no still overrides a parent's yes. Written by the prompt's
  "Trust" or "Trust parent folder" and a `project_trust` vote with `remember`
  (and, unasked, by `/reload` — a gap, see Limits);
- the global `defaultProjectTrust: "always"`, read as the launch reads it
  (`get_default_project_trust`, global scope only, so a repository's
  `.aelix/settings.json` cannot set it; ADR-0203 keeps a repository's `.env`
  from choosing the file). It counts in a folder with nothing to gate too,
  where the resolution never reaches it: the user said yes to every folder.

`cli/entry.py` `_project_explicitly_trusted` reads these after the resolution,
through `parsed.project_trust_override`, the same `default_project_trust` value
the resolution got and `ProjectTrustStore.get`, and passes
`project_explicitly_trusted` to the load and to the manifest scan; the loader
also requires `no_project_local` false, so a saved no or a vote's no beats
"always", as it does for the resolution. Everything else refuses:

- not trusted (`no_project_local`): `--no-approve`, a saved denial, an
  undecided folder under `-p`/json/rpc, the pre-trust vote load (always);
- trusted without anyone saying yes to it: a folder with nothing to gate
  (ADR-0149 step 2 — the case the first cut let through, so a repository that
  wanted `-e acme` to run its `acme/` only had to ship no `.aelix/`), and an
  unsaved yes — "Trust (this session only)" at the prompt, a vote without
  `remember`. The owner's second decision of 2026-11-01 keeps the session-only
  answer out: it is a yes for this session, not to the folder. It also rules
  out the yes `/reload` saves without asking, which the store cannot yet tell
  from a typed one (Limits);
- a library caller that passes nothing (`project_explicitly_trusted` defaults
  to `False`).

The manifest scan shares `_discover_entries`, so such an `-e acme` surfaces no
`contributes.mcp_servers` to spawn either. `aelix status` is not a case: it
passes no configured entries (`cli/status.py`), so there is nothing to refuse
(the first cut's text listed it). The refusal happens before `_discover_in_dir`
lists the entry, so nothing in it is opened. A module-shaped entry contains no
separator, so clause 4 can only have found it at `cwd / entry`: the project
folder is the sole reason it is a path, whatever is there (a directory, a
suffix-less file, a symlink out of the project). One error per distinct entry,
the action first, because `/extension` cuts a row at the terminal width and at
80 columns the first cut's message lost both hints behind the path (the #638
verify, V6):

```
not loaded: pass -e ./acme or -e acme:setup — 'acme' would load <cwd>/acme
from this folder, which is not explicitly trusted (--approve, or a saved trust
decision); -e ./acme loads that path on purpose, -e acme:setup the installed
module.
```

The path is flattened (`safe_for_terminal`): the folder's name is what a clone
chose, and the message reaches the manifest scan's log line and `/extension`
as well as stderr (the verify's V5, Codex review PC5). The `/extension` viewer
shows it as `✖ acme — refused: not loaded: …` under "Refused (found, not
loaded)", and drops its "Run `aelix extension verify`" line when the refusals
are only these — verify reads installed endpoints, not `-e`
(`ExtensionLoadError.verify_applies`, kept out of `repr` and `==`).

The pre-trust vote load refuses the entry too but does not print it: it runs
before the decision, which may lift the refusal, and the round-1 verify (R3)
saw the refusal printed as final above a prompt whose "Trust" then loaded the
entry, and in a gated folder with a saved yes beside the warning of the load
that ran it. `cli/entry.py` skips the vote load's errors whose
`verify_applies` is false (only these); the load after the decision prints
the refusal or loads the entry.

Both hints work in the same folder: `./acme` is a path the user typed
(explicit `-e` stays ungated), and `acme:setup` is the bare form's own factory
spelled so clause 4 tests a path literally named `acme:setup` and the module
reading wins — imported without the launch's project entries (D2). An entry
that already holds a `:` gets "run from another folder" instead.

Rejected: reading the entry as the installed module instead of refusing it.
That would silently change what `-e acme` means depending on trust, and a user
who did mean the folder would get a "no module named" error about a folder that
is right there. Rejected: counting the entry as a trust-requiring resource so
the prompt asks about it — that makes `has_trust_requiring_project_resources`
depend on argv, and the two spellings already say what was meant without a
question.

### D6. An explicitly trusted folder keeps #114's behaviour

With `--approve`, a saved yes or `defaultProjectTrust: "always"`, the entry
warns ("…PROJECT-LOCAL code will be loaded instead of the installed module…",
the path flattened as in D5) and loads from disk, as it did. Settings `extensions` entries are not read yet
(ADR-0279 D8, D16); when #25 feeds them to `_discover_entries`, D5 applies to
them unless #25 decides otherwise.

## Consequences

- A delegated child imports the genuine module for a global pack and an entry
  point in a project that shadows both, on both channels, and still runs its
  task (§ Verification).
- A project `aelix_coding_agent/` directory can no longer replace a delegated
  child outright (measured by the control row of the child test).
- A trusted project's own manifest pack whose `[plugin.entry] python` module
  sat at the top of the project folder imported only under `python -m`; it no
  longer does there either, which is what the console script always did.
- `-e acme` runs nothing from `acme/` unless the folder is explicitly trusted —
  not in an untrusted project, the vote load, or a folder with nothing to gate;
  `--approve`, a saved yes and `defaultProjectTrust: "always"` behave as before
  (§ Verification). Someone who relied on the old implicit load in an ungated
  repository now gets the refusal, which names `-e ./acme`.

## Limits

- `PYTHONPATH` and `.pth` entries decide what a name means (D1, D4), the
  project folder among them when the user put it there: under `PYTHONPATH=.`
  the folder answers these imports too. That is the user's own environment, as
  ADR-0255 §17 says of pip.
- Only the named import is guarded. In a process started as `python -m
  aelix_coding_agent` in a project, or with a `PYTHONPATH` holding the cwd, the
  folder is still on `sys.path` for every other import: aelix's own, and an
  extension's own imports outside the named one — a file extension's top-level
  `import helpers`, an `import` inside a pack's `setup()` — can resolve to a
  project file (the #641 verify, V7; Codex review PC6). Delegated children are
  not affected: they start with `-P` (D1).
- An ancestor of the cwd on `sys.path` that the user put there reaches the
  folder as a package: with `PYTHONPATH=..`, `-e proj.ext` run inside `proj/`
  imports the folder's `__init__.py` (Codex review PC1). D4 removes entries
  naming the folder itself, not every entry from which it is importable.
- A module already in `sys.modules` is refused only when it can be placed: a
  cached submodule whose parent package is not cached (`pkg.ext` without
  `pkg`) and a module with no `__file__` are not (Codex review PC3, PC4). Both
  need code that already ran in this process, which could have done anything
  else as well; D2 keeps a repository from choosing a name's meaning, it is not
  a sandbox.
- The cached-module refusal (D3) asks whether the module's folder is on
  `sys.path` at all other than through the launch's entries, not whether a
  fresh import would pick this module. With `PYTHONPATH=<site>:<a symlink to
  the cwd>` under `python -m`, a project module that a global extension had
  already imported is reused and its `setup()` runs, though a fresh guarded
  import would take `<site>`'s (the #641 round-1 verify, R4). It needs an
  import that already resolved to the project file (the second bullet's case)
  and a `PYTHONPATH` naming the folder (the first's). Comparing against a
  `PathFinder.find_spec` over the remaining entries would close it.
- A saved yes nobody typed counts as explicit, against the owner's ruling
  (2026-11-01) that it should not.
  `maybe_save_implicit_project_trust_after_reload` (pi parity, #112) writes
  `True` for the launch folder when the session started with nothing to gate
  and a `/reload` then finds a trust-requiring resource there — without
  asking; the TUI says "Saved project trust for this folder". `trust.json` holds one bool per folder whoever wrote it, so from the
  next launch `-e acme` loads that folder's `acme/` (the round-1 verify, R2: 0
  executions in the session, 1 after a relaunch with `-p`; fix round 2
  measured the same). Telling it apart needs the store to record who decided,
  a schema change in `cli/project_trust.py`, which belongs to #509 (PR #643,
  not merged) and which this change does not touch; filed as a follow-up.

## Verification

Tests: the loader rows build the `python -m` ordering in-process (project first,
an installed directory after it) for a directory-tier manifest pack, a
manifest-less entry point and a bound one; the control row shows the same
`sys.path` importing the project file without the loader. Further rows: a cached
project module, regular package and namespace package refused; the flat editable
install (the cwd appended as its `.pth` does) loading through a global pack and
through `-e`, and reused when already imported; the guard keeping a
`PYTHONPATH`/`.pth` entry and, under `-P`, `sys.path[0]`; the cwd in another
case (macOS) removed and its cached module refused; the refusal's path
flattened. On the base loader 17 of the 24 rows fail (#114's guard removed the
cwd wherever it sat, so the flat install's `-e` row fails there too); on the
first #641 cut, the 8 rows fix round 1 added for V1, PC2 and V5 fail. The child
rows run CPython with the production prefix and environment in a hostile cwd:
`--version` with a project `aelix_coding_agent/` package, a keyless `-p` run
that loads a global pack and an entry point, and `--version` with
`PYTHONPATH='<root>:'` and a project `sitecustomize.py`; dropping `-P` from the
three builders fails 6 of the first 7 (the control passes), and the first cut's
`build_child_env` fails the 4 empty-segment rows. Fix round 2 (R1) added a
table of launches and `sys.path` shapes (the launch's cwd behind an insert,
after a console script's folder through an empty `PYTHONPATH` segment, twice
under `python -m`; a `.pth` or `PYTHONPATH` folder naming the cwd beside it, a
script's own folder), the recorder's rows (`-P`, set-but-empty, leading,
trailing and doubled empty segments, a `.pth` naming the cwd), which `.pth`
lines name a folder, and five real interpreter launches; on round 1's loader
the three in-process R1 rows and the three real-launch R1 rows fail, and the
controls pass.

Live (rule 10), the same scratch as § Context after the change: print and rpc
children both record `genuine` for `review_voter` and `review_ep` with
`sys.flags.safe_path` true and argv `python -P -m aelix_coding_agent`, no
sentinel, and both return `PONG` from OpenRouter `anthropic/claude-haiku-4.5`.
Fix round 1 re-ran it with a fresh delegation and an `agent(action="resume")`
of it: on base both children imported the project's modules (sentinel 4
times); now both import the genuine ones under `-P`, no sentinel, `PONG PING`.
The shipped `examples/starter`, `uv pip install -e .` and `aelix
--trust-extension-path aelix-starter-ext -p` from its root: the first cut
failed with "No module named 'aelix_starter'" and its `setup()` did not run;
now it runs, from the root and from its parent. Fix round 2 re-ran it on base,
round 1 and this: `setup()` runs in all three for the console script from the
root and from the parent, `python -m` from the root, and the console script and
`python -m` from the root under `PYTHONPATH=':<dir>'` (before the `.pth`
exception, this change failed the last two). The R1 shapes, with `-e
vfyg:setup`, a project `vfyg.py` and the genuine one on `PYTHONPATH`, each
answering `hi`:

| Launch | base | round 1 | now |
| --- | --- | --- | --- |
| console script, `PYTHONPATH=':site'` | genuine | **project** | genuine |
| `python -m`, a global file extension's `sys.path.insert(0, …)` | genuine | **project** | genuine |
| `python -m`, `PYTHONPATH=':site'` | genuine | **project** | genuine |
| controls: console script, `python -m` | genuine | genuine | genuine |

#638: the loader rows refuse a package directory, a dotted directory, a
suffix-less file and a symlink with zero executions in a folder trusted only
implicitly, and the plain entry in an untrusted one and in an implicitly trusted
one; an "explicit" flag for an untrusted folder and a library caller that says
nothing are refused too; the manifest scan returns nothing unless explicit. The
controls load: explicit (warn and load), `./acme` in an untrusted and an
implicit folder, `acme:setup` importing the installed `acme` past the local
one, the explicit scan. Further rows: the message's path flattened (ESC, BEL,
TAB, U+202E) in the error and in the `/extension` rows; the hints within the
first 80 columns of the row; the colon-entry hint; the verify line dropped for
these refusals alone and kept beside another; `verify_applies` out of `repr`
and `==`; `_project_explicitly_trusted` for `--approve`, `--no-approve` over a
saved yes, a saved yes on a parent, nothing saved, a saved no, a saved yes the
resolution did not follow, and a malformed store. The reproduction runs as a
real `python -P -m aelix_coding_agent` child: `--no-approve` 0 executions,
`--approve` 1, nothing to gate 0, a saved yes 1, a gated folder under `-p` 0,
and a gated folder with a saved yes 1; a CLI row shows the manifest scan
starting the folder's MCP server under `--approve` and not in a folder with
nothing to gate. With the first cut's condition (`no_project_local` alone) 9
rows fail. Fix round 2 added `defaultProjectTrust: "always"`: explicit rows
for "always", "always" under a saved no and under `--no-approve`, and "never";
reproduction rows for "always" with nothing to gate (1), gated (1) and gated
with a saved no (0); in every reproduction row, no vote-load line for the
refusal (R3), and a broken global extension's vote-load error still printed
beside it. On fix round 1's `entry.py` 6 of those fail; the last row fails
when every vote-load error is skipped.

Live (rule 10), the console script and OpenRouter `anthropic/claude-haiku-4.5`,
executions of the project's `acme/__init__.py`, base → first cut → this:

| Run | base | first cut | now |
| --- | --- | --- | --- |
| `-e acme --no-approve` | 1 | 0 | 0 |
| `-e acme --approve` | 1 | 1 | 1 |
| `-e acme`, nothing to gate | 1 | 1 | **0** |
| `-e acme`, saved yes | 1 | 1 | 1 |
| `-e acme`, `.aelix/settings.json`, saved yes, `-p` | 2 | 1 | 1 |
| `-e acme`, saved no, nothing to gate | 1 | 1 | **0** |
| `-e acme`, `.aelix/settings.json`, `defaultProjectTrust: "always"` | 2 | 1 | 1 (fix round 1: 0) |
| `-e acme`, `.aelix/settings.json`, undecided, `-p` | 2 | 0 | 0 |
| `-e ./acme --no-approve` / nothing to gate | 1 / 1 | 1 / 1 | 1 / 1 |
| `-e acme:setup`, installed `acme` on `PYTHONPATH` | genuine | genuine | genuine |

The saved-no row shows what the first cut missed besides the owner's case: a
folder with nothing to gate is trusted at step 2, before the store is read, so
a saved "Do not trust" did not stop `acme/` there either. In the TUI (pty,
80x24, a folder named with ESC, BEL and TAB, no flag) the refusal prints above
the banner with the path flattened, no raw control byte reaches the terminal,
and `/extension` shows `✖ acme — refused: not loaded: pass -e ./acme or -e
acme:setup — 'acme' would l…` with no verify line; the first cut ran `acme/`
there and, under `--no-approve`, showed `…it would load /private/tmp/6` with
the raw escapes on the stream.

Fix round 2, the same console script and model on base, round 1 and this (the
vote load's refusal lines in brackets, which R3 removes):

| Run | base | round 1 | now |
| --- | --- | --- | --- |
| `-e acme`, nothing to gate | 1 | 0 | 0 |
| `-e acme`, `.aelix/settings.json`, saved yes, `-p` | 2 | 1 [1] | 1 [0] |
| `-e acme`, `defaultProjectTrust: "always"`, nothing to gate | 1 | 0 | **1** |
| `-e acme`, `defaultProjectTrust: "always"`, `.aelix/settings.json` | 2 | 0 [1] | **1** [0] |
| the same, saved no | 2 | 0 [1] | 0 [0] |
| `-e acme --no-approve`, `defaultProjectTrust: "always"` | 1 | 0 | 0 |
| `-e acme`, saved yes on the parent folder, nothing to gate / gated | 1 / 2 | 1 / 1 [1] | 1 / 1 [0] |
| `-e acme`, parent yes, cwd no | 1 | 0 | 0 |

In the TUI (80x24 pty): "Trust (this session only)" at the prompt runs nothing
from `acme/` and saves nothing, and `/extension` lists the refusal; no vote-load
line shows above the prompt. A folder with nothing to gate, `.aelix/` created,
`/reload`: 0 executions and "Saved project trust for this folder…", `trust.json`
now `true`, and a relaunch with `-p` runs `acme/` once — the Limit above.
