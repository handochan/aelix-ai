# 0285. A module name the user chose is never answered by the project folder

Status: Accepted (2026-11-01, #641)
Date: 2026-11-01
Issue: #641 (from the Codex cross-review of the #510/#25 design, F01). Batch B6
(security), owner-approved 2026-11-01; lands before #25, whose pre-trust filter
(ADR-0279 D16, C5) relies on it.
Amends: ADR-0197 §(l) (the child's command line gains `-P`), ADR-0252 (dated
note §13: the boundary it draws is now held for imports by name too).
Relates: ADR-0255 §17 (#394: pip children have used `python -P -m pip` since
2026-10-11 — the precedent), ADR-0149 (explicit `-e`, global and installed
extensions are the user's choice), ADR-0279 D16, #114 (the `-e <module>` form and
the guard this generalises; it has no ADR of its own), #25.
pi: `earendil-works/pi@0baf611f0`.
Tests: `tests/extensions/test_project_module_imports_641.py`,
`tests/agents_ext/test_child_safe_path_641.py`; the four-token launch prefix in
`tests/agents_ext/test_child_argv_contract.py` and its siblings.

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
The Codex probe had already shown the stake: the project's `review_voter` could
answer the `project_trust` vote and turn the trust decision to `True`.

pi has neither import. Its `-e acme` is a path relative to the cwd and an
installed package is spelled `npm:acme` (`package-manager.ts` `parseSource`,
`:1592-1616`; `extensions/loader.ts` `loadExtension` resolves the path,
`:644`). The module-name form is aelix's (#114), so the rule is aelix's to set.

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

## Consequences

- A delegated child imports the genuine module for a global pack and an entry
  point in a project that shadows both, on both channels, and still runs its
  task (§ Verification).
- A project `aelix_coding_agent/` directory can no longer replace a delegated
  child outright (measured by the control row of the child test).
- A trusted project's own manifest pack whose `[plugin.entry] python` module
  sat at the top of the project folder imported only under `python -m`; it no
  longer does there either, which is what the console script always did.

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
