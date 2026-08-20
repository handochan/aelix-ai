# #114 — revive `-e <dotted.module>`, and make the shipped examples runnable

Branch `feat/114-dotted-module-extension`, worktree `/workspaces/aelix-114`, from `main` @ `54fff15`.

Closes **#114**. Newly urgent: the #117 signpost (`ec1403b`) now points the model at
`examples/echo/echo.py` by absolute path and calls it "worked example, read this one"
(`cli/agent_context.py:59,180-184`). Reading works; the only documented way to *run* it does not.

## 1. The defect

`discover_and_load_extensions` coerces every configured `str` to a `Path` **before** it reaches
`_resolve_factory`:

```python
# extensions/loader.py:470  (inside "3. Explicit configured paths")
p = Path(entry) if isinstance(entry, str) else entry
```

`_resolve_factory` already handles `str` correctly (`loader.py:893-896`): `.py` suffix →
`_factory_from_file`, otherwise → `_factory_from_module`, which supports BOTH the legacy bare
`module.path` (resolves top-level `setup`) and the colon form `module.path:callable`. That branch is
simply **unreachable from the CLI**, because the coercion above turns every string into a `Path` first.

Live, on `main` @ `54fff15`:

```console
$ aelix -e aelix_coding_agent.examples.echo.echo --print "hi"
Warning: extension load: ExtensionLoadError(
  path='/tmp/aelix_coding_agent.examples.echo.echo',
  error='Extension file not found: /tmp/aelix_coding_agent.examples.echo.echo')

>>> await load_extensions(['aelix_coding_agent.examples.echo.echo'])
extensions: ['aelix_coding_agent.examples.echo.echo']  errors: []   # library API works
```

Same string, opposite outcomes — a CLI/library divergence, not merely a missing feature.

Neither shipped example (`examples/echo`, `examples/selfhosted`) is registered as an entry point
either, so today they can be read but **never run**.
[The second example's directory was renamed to `examples/selfhosted` after this plan was written.]

## 2. The fix, and its one real ambiguity

In the `configured_paths` loop, only coerce to `Path` when the entry is actually path-shaped;
otherwise pass the `str` through unchanged so `_resolve_factory` reaches `_factory_from_module`.

The ambiguity is real: `mypkg.ext` could be a dotted module OR a file on disk. Resolution order —
**an existing path always wins**, matching the precedent already set by `classify_target`
("a local path WINS if it exists on disk"). Only when nothing exists on disk do we ask whether the
string looks like a module.

Proposed predicate (the implementer must verify each clause against the real tree, not assume):

1. `.py` suffix → path. Always. (`_resolve_factory` already encodes this.)
2. Resolves to an existing file or directory (absolute, or relative to `cwd`) → path.
3. Contains a path separator (`/`, or `os.sep` / `os.altsep`) → path. A dotted module never does.
4. Contains `:` → module:callable form → module. (Windows drive letters `C:\...` are excluded by
   clause 3 firing first — **verify this ordering holds**.)
5. Otherwise, if it matches a dotted-identifier shape → module.
6. Otherwise → path (preserves today's behaviour and today's error message).

Clause 6 matters: anything we cannot confidently classify must keep the current behaviour, so this
change can only *add* working inputs, never move a previously-working input to a new failure mode.

## 3. Also in scope

- **`examples/INDEX.md`** — a categorized filename → purpose table (pi's pattern; pi ships
  `examples/extensions/` with exactly such an index). Nothing currently points at the two examples.
- **The first complete `aelix-plugin.toml` in the repository.** `find . -name aelix-plugin.toml`
  returns NOTHING today — only inline TOML strings inside `tests/extensions/*`. The 2026-07-27 live
  session measured the unassisted agent **inventing a fake `tools_definition.json` manifest**, so a
  real one on disk to copy is worth more than any prose assertion that manifests exist.
  It must be a manifest the loader actually parses (`aelix_agent_core.contracts.manifest.PluginManifest`
  is the authority and is strict, `extra="forbid"`).
- **Docstring accuracy** on the changed function.

## 4. Out of scope

- Registering the examples as entry points (changes install-time surface; `-e` is the documented path
  and is what #114 is about).
- `#115` skills carrier, `#118` CI red, `#91`, anything in `cli/extension_*` (W1-A territory, already
  merged).
- Do NOT touch `cli/agent_context.py` — the #117 signpost just landed there and its pointers are
  already correct for *reading*.

## 5. Lane isolation

Owns: `extensions/loader.py`, `tests/extensions/*`, `src/aelix_coding_agent/examples/**`.
Does NOT touch: `cli/entry.py`, `cli/args.py`, `cli/agent_context.py`, `cli/extension_*`, `tui/`,
`agents/`, `rpc/`. An rpc sprint and a self-awareness lane both merged recently; staying out of their
files keeps this trivially rebaseable.

## 6. Verification gate

**MANDATORY test command** (the venv is an editable install pointing at `/workspaces/aelix-ai`; without
this PYTHONPATH a worktree run imports the MAIN repo and green means nothing — this exact bug was in
W1-A's plan and the review caught it):

```
cd /workspaces/aelix-114 && source /workspaces/aelix-ai/.venv/bin/activate && \
PYTHONPATH="/workspaces/aelix-114/packages/aelix-coding-agent/src:/workspaces/aelix-114/packages/aelix-agent-core/src:/workspaces/aelix-114/packages/aelix-ai/src:/workspaces/aelix-114/packages/aelix-server/src:/workspaces/aelix-114/src" \
python -m pytest -q -p no:cacheprovider
```

Print `aelix_coding_agent.__file__` and assert it starts with `/workspaces/aelix-114` before trusting
any run.

Acceptance:

- [ ] `aelix -e aelix_coding_agent.examples.echo.echo --print …` loads the extension, `errors == []`
- [ ] Same for `examples.selfhosted.selfhosted` [this module was renamed after the plan was written]
- [ ] CLI and `load_extensions([same_string])` agree — pin the equivalence, since divergence is the bug
- [ ] `-e ./some/ext.py`, `-e /abs/ext.py`, `-e some_dir/`, `-e mypkg.ext` **that exists on disk** all
      behave exactly as before (path wins)
- [ ] `module:callable` form works from the CLI
- [ ] A file that genuinely does not exist still produces today's error message
- [ ] The new `aelix-plugin.toml` round-trips through `PluginManifest`
- [ ] Full suite green vs baseline; ruff clean

## 7. Known pre-existing flakes — never report as a regression, never fix here

`tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored`
(measured ~2/5 at idle, 0/2 under load), `tests/agents_ext/test_print_channel_spawn.py::
test_a_child_that_finishes_at_the_deadline_is_not_a_timeout`, and assorted tui timing tests.
Re-run a named test alone before concluding anything. **#118** (main CI red on py3.11 since `bb24295`)
is open and out of scope; local runs are py3.12.
