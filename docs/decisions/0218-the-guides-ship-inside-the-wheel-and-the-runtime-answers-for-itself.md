# 0218. The guides ship inside the wheel, the prompt names them, and the runtime answers for itself

Status: Accepted (2026-08-14).
Date: 2026-08-14
Relates: ADR-0206 (the `_ManifestEntry` repr redaction — §5 reuses its pattern one object
further along), ADR-0197 §(a) (the band table that decided where `aelix_status` lives, and
the one-line `packages` cost it measured), ADR-0208 (what the band rule actually asks —
"is this delegation policy?"), ADR-0217 (`_safe_path` / `_escape_text`, which the new prompt
block reuses, and the trust policy the new `project-trust` guide writes down), ADR-0203
(the `.env` default-deny that guide also documents).
GitHub: #101 (parent epic #53).
Pi: `earendil-works/pi@v0.84.1` — tarball fetched 2026-08-14 and read locally. Every pi
citation below was taken from that tree, not recalled; where a path in the recon brief was
wrong, the corrected one is given.

**Provenance.** Five pieces, three provenance claims. They are separated because lumping
them is how a divergence gets read back as parity two syncs later:

| Piece                                                          | Provenance                                                              |
| -------------------------------------------------------------- | ----------------------------------------------------------------------- |
| The guides are **copied into the distribution**                  | **FORWARD-PORT** — a pi surface aelix never ported                       |
| A **system-prompt block naming them** by absolute path           | **FORWARD-PORT** — same pi surface, the half that makes bundling useful  |
| The **`aelix docs` CLI verb**                                    | **AELIX-ORIGINAL** — pi has no `docs` subcommand                         |
| **`RuntimeSnapshot` + the `aelix_status` tool**                  | **AELIX-ORIGINAL** — pi has no introspection tool                        |
| **`Extension.__repr__` redaction**                               | **SECURITY FIX**, neither parity nor divergence — pi has no manifest at all |

### The pi side, verified rather than quoted from the brief

- **Bundling.** `packages/coding-agent/package.json` at `v0.84.1`:
  `"files": ["dist","docs","examples","containerization.md","CHANGELOG.md","npm-shrinkwrap.json"]`.
  The binary build does it a second way — `copy-binary-assets` runs
  `shx cp -r docs dist/ && shx cp -r examples dist/`. pi's `docs/` holds 30 `.md` files.
- **The path helpers.** `packages/coding-agent/src/config.ts`: `getReadmePath()` `:427`,
  `getDocsPath()` `:432`, `getExamplesPath()` `:437` — each `resolve(join(getPackageDir(), …))`.
- **Their use in the system prompt.** Not at `src/system-prompt.ts`, which **404s**; the file is
  `packages/coding-agent/src/core/system-prompt.ts` (imported at `:5`, read at `:75-77`, emitted
  at `:131-138`). Its header, verbatim: *"Pi documentation (read only when the user asks about pi
  itself, its SDK, extensions, themes, skills, or TUI)"*, followed by seven bullets (`:132-138`).
- **No `docs` subcommand.** pi's help enumerates its verbs at `src/cli/args.ts:244-252`:
  `install`, `remove`, `uninstall`, `update`, `list`, `config`, `auth`. `grep -rn '"docs"' src/`
  returns three hits, none of them a command: the `config.ts` path helper and two in
  `core/tools/read.ts` where `"docs"` is a *label kind*.
- **No introspection tool, and the count in the brief was wrong.** The brief said pi's built-ins
  are "bash/edit/edit-diff/find/grep/ls/read/write/image" — nine. At `v0.84.1` they are **seven**:
  `src/core/tools/index.ts:84` reads
  `allToolNames: Set<ToolName> = new Set(["read","bash","edit","write","grep","find","ls"])`
  (and `:83` types `ToolName` as the same seven),
  and the `createTool` switch has exactly those seven arms. `edit-diff.ts` is a diff helper
  imported by `edit.ts`; there is no `image.ts` at all — images arrive as content blocks out of
  `read` (`read.ts:266`). This matters here because those seven names are exactly the seven in
  aelix's own `Available tools:` sentence, which is what makes §6 below a real defect rather
  than a cosmetic one.

---

## The problem

Issue #101 asked for an offline self-help bootstrap: an installed agent should be able to
answer "how do I write an extension for *this* version" and "what am I running as" without a
web search. Three parts — bundled docs, a runtime snapshot, and a skill that sequences them.

**Four corrections to the issue's framing.** An ADR that repeats a wrong premise makes it
permanent.

### The gap was smaller than the issue assumed

The issue says a normal install offers none of this "as one official interface". The *interface*
claim is right; the *absence* claim is not. Measured by building the wheel at the base commit
(`a079188`) with `uv build --wheel --package aelix-coding-agent` and reading the zip — never the
source tree:

| Already in the base wheel                          | bytes     |
| --------------------------------------------------- | --------- |
| `skills/writing-skills/SKILL.md`                     | 3 972     |
| `skills/extending-aelix/SKILL.md`                    | 3 688     |
| `examples/INDEX.md`                                  | 7 788     |
| `examples/echo/aelix-plugin.toml` (reference manifest) | 13 029  |
| `examples/starter/README.md`                         | 1 942     |
| **total authoring prose**                            | **30 419** |

Roughly 30 KB of it, version-matched, offline, already shipping. What was missing was not the
prose.

### The real defect: shipped files linked to a directory that did not ship

`docs/guides/` was **not** in the wheel, and six shipped files pointed at it. Measured by
decompressing every text member of the base wheel and grepping — six hits, in five files:

```
aelix_coding_agent/cli/extension_install.py:2338
aelix_coding_agent/examples/INDEX.md:21, :115
aelix_coding_agent/examples/starter/README.md:39
aelix_coding_agent/examples/starter/pyproject.toml:22
aelix_coding_agent/examples/telnaut/__init__.py:8
```

`extension_install.py:2338` is the sharpest of them: it is the hint printed when an installed
user's pack goes inert, and its single reader is a user with no checkout. The same grep over the
wheel built from this branch returns **two** hits, both deliberate prose about the source tree
(`examples/starter/pyproject.toml:23`, `help/registry.py:21`).

### Of the issue's five topics, only one had a real content gap

The issue lists `extension`, `extension-api`, `tools`, `skills`, `security`/`project-trust` as
the minimum. Resolved through the shipped CLI (`python -m aelix_coding_agent docs <name>`):

| Topic          | Resolves to                            | Content gap?                                          |
| -------------- | -------------------------------------- | ----------------------------------------------------- |
| `extension`      | `extension-authoring.md` (33 620 B)      | No — the guide existed; it just never shipped         |
| `extension-api`  | `extension-authoring.md`                 | No                                                    |
| `tools`          | `extension-authoring.md`                 | No — see below                                        |
| `skills`         | **nothing**; exits 2 with a pointer      | Partly — see the residual at the end                  |
| `security`       | `project-trust.md`                       | **Yes. The only one.** Written fresh for this change  |

`tools` is aliased to the authoring guide rather than given its own document because the thing
a `tools` document would contain is already in front of the model: every adapter rebuilds the
tool list into the request params on **every** call — `openai_responses.py:303`,
`openai_completions.py:749`, `_google_shared.py:795`, `openai_codex_responses.py:254` — so each
tool's name, description and JSON schema is in context already. What is *not* in any guide is
the built-in tool list as prose; that lives in `aelix --help` under `--tools`. The alias points
at the only document in the repo about tools as a thing you can *add*.

`project-trust.md` is **written fresh, not copied** from the repo-root `SECURITY.md`. That file
carries vulnerability-reporting contact details, which have no business in every wheel on every
machine.

### The issue asked for a new skill; it got a pointer in an existing one

#101 §2 asks for a built-in `aelix-help` skill, default-on. It was not created. The
`extending-aelix` skill already carried the procedure the issue describes, and a skill's
`description` is paid on **every turn** — a second skill covering the same ground is a permanent
tax for a one-time convenience. The `aelix docs` invocations were folded into `extending-aelix`
and its frontmatter was left byte-identical, so the marginal prompt cost is zero.

---

## The decision

### 1. The guides are copied into the wheel (FORWARD-PORT)

`docs/guides/*.md` → `packages/aelix-coding-agent/src/aelix_coding_agent/docs/*.md`, eight files,
87 751 bytes, kept in sync by `scripts/sync_bundled_docs.py` and enforced by
`tests/test_docs_bundle_sync.py`.

Three things about the mechanism are load-bearing:

- **No pyproject change was needed for the copy itself.** `[tool.hatch.build.targets.wheel]`
  `packages` is a *file-tree* selector, so any file under `src/aelix_coding_agent` ships whatever
  its extension. (The `packages` list *was* edited, but for `aelix_status` — §4.)
- **The name matters.** `exclude` carries 12 patterns, two of them bare filenames — `CLAUDE.md`
  and `AGENTS.md`. A bundled guide under either name is dropped **silently**.
  `tests/test_docs_bundle_sync.py` guards it and derives the forbidden set *from the pyproject*,
  with a second test (`test_the_exclude_list_still_contains_the_names_this_guard_is_about`)
  guarding the guard — otherwise deleting `CLAUDE.md` from `exclude` would make the first test
  pass with nothing left to forbid.
- **Relative links out of the directory die on arrival.** The guides carried **seven**
  `](../decisions/…)` links — six in `private-catalog.md`, one in `guides/README.md` — which
  resolve in a checkout and point at nothing next to the bundled copy
  (`aelix_coding_agent/decisions/` will never exist). They are now absolute `https://github.com/…`
  URLs, correct in both places; `grep -ro "](\.\./" docs/guides/` now returns 0, and two tests walk
  every link (one over the source guides, one over the wheel's copies).

Every packaging assertion here is made against a **built wheel** (`uv build --wheel`,
`zipfile.namelist()`). A test that reads the source tree cannot see any of these three failures,
because a checkout puts everything on the path.

### 2. The system prompt names them (FORWARD-PORT)

`cli/agent_context._docs_signpost()` emits 632 chars between the environment block and the
extension signpost, taking `build_system_prompt("/some/project")` from 3 362 to **3 994** bytes
(+19%), paid every turn:

```
Aelix documentation (read one only when the user asks about Aelix itself — its setup,
extensions, providers and models, agent profiles, or trust model):
- Bundled at <abs>/aelix_coding_agent/docs/<name>.md, each small enough for `read` to return
  whole. <name> is one of: README, agent-profiles, extension-authoring, getting-started,
  models-json, private-catalog, project-trust, providers-and-models
  - extension-authoring covers the aelix-plugin.toml manifest, capabilities and publishing.
- If no guide covers the question, say so — do not answer it from another tool's behaviour.
```

Seven bullets in pi, three here, and the differences are decisions rather than trimming:

- **pi's "resolve `docs/…` under Additional docs, not the cwd" bullet is dropped** because it only
  exists to disambiguate pi's own two-line form (a directory on one line, relative topic names on
  another). Emitting one `<abs dir>/<name>.md` form costs the same bytes and removes the ambiguity
  that bullet was added to fix.
- **pi's Examples pointer is dropped** because `_extension_signpost`, immediately below, already
  hands the model the one annotated example.
- **Absolute paths, not `aelix docs <topic>`.** The CLI verb needs `bash`, and `bash` is in
  `builtin/permission._MUTATING` (measured: `'bash' in _MUTATING` → `True`, `'read' in _MUTATING`
  → `False`). PLAN mode refuses every mutating tool at `permission.py:436`, above the read-only
  short-circuit at `:442` — so in exactly the mode where a user asked the agent to look and not
  touch, an `aelix docs` pointer is unfollowable. The skill points a human at the verb; the prompt
  points the model at a path.
- **"Small enough for `read` to return whole" is measured, not asserted.** Largest bundled guide
  is `extension-authoring.md` at 33 620 B against `tools/_truncate.DEFAULT_MAX_BYTES` = 51 200,
  and a test pins every guide under the cap. This is the trap `_extension_signpost` already hit
  once with `api.py` (84 KB, silently truncated to a window containing none of the `register_*`
  definitions).
- **The names are globbed at call time**, so a guide added to the bundle appears with no code
  change, and a stripped install (no `docs/`) emits `""` rather than advertising an empty
  directory.
- **The emitted directory is run through `_safe_path` + `_escape_text`** for the same reason
  ADR-0217 does it to the cwd: the path is `Path(__file__)`-derived, so a checkout or install
  under a directory named `<project_context>` would forge #121's fence from the *install* side.

### 3. `aelix docs` (AELIX-ORIGINAL)

A `docs` verb routed in `cli/entry.py` before `parse_args`, for the same reason `extension` is —
the flat flag parser would swallow `docs` and a topic name as chat positionals. `aelix docs`
lists, `aelix docs <topic>` prints to stdout, `aelix docs --search <term>` greps all of them.
Diagnostics go to stderr and product output to stdout, so `aelix docs extension | head -1` puts
markdown in the pipe and nothing else. Exit **2** for a usage error, asserted against the real
`extension_install._EXIT_DIDNT_RUN` constant rather than restated.

`aelix_coding_agent.help` is the single registry both the CLI and any future in-agent tool read,
and it is stdlib-only — no `rich`, no `prompt_toolkit`, no `cli` import. Verified twice: a child
interpreter shows neither TUI module in `sys.modules` after importing it (with a companion test
proving both *are* importable there, so the clean result is earned), and the wheel was installed
into a fresh venv **without** the `tui` extra, where `aelix docs security` printed the guide and
`aelix docs bogus` exited 2 with zero bytes on stdout.

The `parents[1]` idiom in `help/registry.py` matches `cli/config.packaged_skills_dir` and was
chosen for consistency, **not** because `importlib.resources` is unavailable — that reason was
checked and is false (it is stdlib and `.files` is present in a bare `python -m venv`, CPython
3.12.1). The wrong reason is recorded here so it does not propagate.

### 4. `RuntimeSnapshot` + the `aelix_status` tool (AELIX-ORIGINAL)

Eight fields — `version`, `cwd`, `mode`, `project_trusted`, `active_tools`, `all_tools`,
`loaded_extensions`, `manifest_api_level` — each read from a named runtime object. A live sample,
taken by driving the registered `tool_call` handler and then the real `execute()`:

```json
{ "version": "0.1.0b1", "cwd": "…", "mode": "print", "project_trusted": true,
  "active_tools": ["aelix_status"], "all_tools": ["aelix_status"],
  "loaded_extensions": [], "manifest_api_level": 1 }
```

**It ships as a bundled extension, not a `tools/` built-in.** `ToolExecutionContext` has exactly
four fields — `tool_call_id`, `signal`, `on_partial`, `model` (`aelix_ai/tools.py:77-84`) — and
none of them reaches the harness, the extension runtime, the loaded-extension list or the trust
decision. A tool under `tools/` structurally cannot see the state this tool exists to report.
`aelix_agents` already sets the precedent for the `agent` tool.

**It is its own top-level package** (`src/aelix_status`), not part of `aelix_agents`. The band
rule did not force this: ADR-0208 says the only question it asks is whether a change is
*delegation policy*, and this spawns nothing, authors no consent and declares no cap. What
decided it is narrower — ADR-0197's table defines `aelix_agents` as "the only band that spawns"
and `tests/agents/test_p2_band_boundaries.py` treats every file under it as delegation surface, so
a read-only introspection extension there would make both the label and the gate imprecise. The
cost is the one line ADR-0197 already measured: a third `packages` entry, guarded by a test that
opens a **built wheel**, because a missing entry is invisible in every source checkout.

Two field decisions worth recording:

- **`extension_paths` (in the issue's draft) was dropped.** Measured against the real loader:
  `Extension.source_info` came back `None` for all four load tiers (it has zero writers);
  `resolved_path` is set on exactly one branch (`loader.py:229`); and for the directory tiers the
  only path-shaped value is `Extension.name`, which *is* an absolute path — the probe returned
  `name='/tmp/…/proj/.aelix/extensions/projext.py'`. On a real machine that is `$HOME` and the OS
  username, handed to a model that may be a hosted third party. A per-extension `scope` label
  replaces it.
- **`extension_api_version` was renamed `manifest_api_level`.** `ExtensionAPI` carries no version
  at all. The only versioned extension contract is the manifest's `[plugin.api]` `level`/`min_level`
  pair checked against `AELIX_API_LEVEL` (= 1). Shipping the field under the issue's name would
  invite a model to invent version numbers for a surface that has none.

**Redaction is structural, not a filter.** Nothing is ever `repr`/`asdict`/`model_dump`-ed.
Extension identity comes from `manifest.plugin.id` (`^[a-z][a-z0-9-]{0,63}$`) and
`plugin.version` (semver) — both schema-constrained, so a token cannot travel through either;
`plugin.name`, which is free text, is deliberately unused. A path-shaped `Extension.name` is
reduced to its basename.

**`project_trusted` fails closed.** It does not call `ctx.is_project_trusted()`: that getter's
unbound default is `lambda: True` (`extensions/api.py:1094`), `AgentHarnessOptions.project_trusted`
defaults to `True` (`harness/core.py:265`), and `rpc_ws.py:90` builds options with neither — so
an extension there would be told "trusted" by a harness nobody asked. Only an
explicitly wired getter counts. The cost is under-reporting under an unwired-but-trusted harness,
which is the direction to be wrong in.

### 5. `Extension.__repr__` is redacted (SECURITY FIX)

`@dataclass(repr=False)` plus an explicit repr keeping name, plugin id, `resolved_path` and
registration **counts**. The generated repr expanded `manifest` in full, and
`contributes.mcp_servers[].env` is where a plugin puts its API tokens: measured on a pack loaded
through the real `discover_and_load_extensions`, a 1 250-char repr ending in
`env={'GITHUB_TOKEN': 'ghp_PLANTED_SECRET_abc123'}`.

ADR-0206 hardened `_ManifestEntry.__repr__` — the **carrier** — for exactly this. The `Extension`
it produces holds the same manifest object and kept the generated repr, so the redaction stopped
one object short. Nothing in production or test code reprs an `Extension` today, which is not a
defence: an object reaches a repr from any logger, debugger frame, pytest assertion rewrite or
pasted traceback, so the object has to be safe rather than every caller having to remember.

### 6. The `Available tools:` sentence is *not* fixed here

`cli/agent_context.py:639` names seven tools — `read, write, edit, bash, grep, find, ls` — which
is exactly pi's built-in set (§"the pi side" above), and it is a **hardcoded string**. It was
already wrong before this change: the `agent` tool ships and is absent from it. `aelix_status`
makes it wrong by two. Making that sentence dynamic is issue **#120** and was deliberately not
attempted here; the arithmetic is recorded so #120 is not later mistaken for damage this change
did. (The recon brief cited it at `:491-494` — its position before the docs block was inserted
above it. A sentence whose *line* moves under an unrelated edit is a mild argument for the same
issue.)

---

## Consequences

- **`aelix_status` is always on, and is therefore narrowable away.** It is appended to the prepend
  list after Guardrail and Permission (measured: `['GuardrailExtension','PermissionExtension',
  'StatusExtension']`), and its `tool_call` handler returns `None` unconditionally, so it cannot
  block, allow or rewrite a call. But `--tools <list>` and an agent profile's `tools:` drop it from
  the active set — measured, `--tools read,ls` yields `active_tools == ["read","ls"]`. That is
  correct per the flag's contract and identical to the `agent` tool's behaviour, and it means the
  tool a model would use to discover what it can do is itself removable.
- **Eight extension-count assertions moved by one.** `tests/cli/test_agent_context.py`,
  `test_agents_extension_gate.py`, `test_no_builtin_tools.py`, `test_project_trust.py`. They
  count real prepends, so the shift is the change being visible, not a test being loosened.
- **`/extension`'s viewer will list `StatusExtension` under "Plugins:"** as if it were a user
  plugin, because `tui/extension_manager.py:44` `_BUILTIN_SAFETY_NAMES` names only Guardrail and
  Permission. Pre-existing — `AgentsExtension` already has it — but #101 makes this one always-on
  and therefore always visible. Not fixed here: the constant is named for *safety* extensions and a
  status extension is not one, so the honest fix is renaming it, which belongs with that file.
- **`scope` returns `"unclassified"` for prepended built-ins and for manifest-less entry-point
  packs.** Not laziness: `loader._resolve_factory` derives both names identically
  (`getattr(factory, "__qualname__", None) or type(factory).__name__`, `:1805` and `:1813`) and
  nothing on the `Extension` distinguishes them. Telling them apart requires the loader to record
  the tier — which would also un-dead `Extension.source_info`, currently with zero writers.
- **`Extension`'s repr no longer expands the manifest.** Capabilities, activation and contributes
  are gone from tracebacks by design; registration buckets are counts. Use the object, not its repr.
- **`aelix --offline docs …` does not work**, and neither does `aelix --offline extension …`.
  Subcommands route before the flag parser, so a leading flag is never seen by them and the whole
  invocation falls through to a chat turn. For `docs` this is harmless (it touches no network); for
  `extension` it is a real sharp edge and is now documented in `providers-and-models.md`, with
  `PI_OFFLINE=1` or the subcommand's own `--offline` as the working forms. Fixing the routing was
  out of scope.
- **`aelix status` (the optional CLI adapter in #101 §3) was not built.** The seam for it is
  `StatusExtension.snapshot()` → `RuntimeSnapshot.to_dict()`. Anything that re-implements the
  projection forks the redaction rules; and any second construction site **must** pass
  `project_trusted=`, or it will print `project_trusted: false` in a trusted repo forever, silently.
- **Two false claims in the existing guides were found and fixed while auditing them**, both of the
  kind only a measurement catches. `extension-authoring.md`'s `register_flag` / `get_flag` row said
  the surface was live and that "CLI overrides win": measured, an extension registering `probe-flag`
  with `default="DEFAULT"`, launched as `aelix -e probe.py --probe-flag FROM_CLI --print …`, printed
  `get_flag('probe-flag') = 'DEFAULT'`. Confirmed structurally on this branch too: `unknown_flags`
  has no production reader outside `args.py` (the one hit in `cli/agent_context.py:423` is a comment
  saying it is not threaded), and the only caller of `set_flag_value` is the `/reload` restore path
  at `runtime/agent_session_runtime.py:816` (#92). `providers-and-models.md`'s `--offline` section
  said the flag was "currently a no-op
  reserved for forward compatibility": false on both halves — it gates the ripgrep download
  (`util/tools_manager.py:378-380`, which printed `ripgrep not found. Offline mode enabled, skipping
  download.` and returned `None`), catalog fetch (`cli/extension_install.py:3657`) and index-less
  pypi installs (`:1195`).

### How this was checked

**92 new tests** across ten files (`tests/status/` 26, `tests/help/` 14, `tests/cli/test_docs_command.py`
18, `test_docs_signpost.py` 15, `test_extending_aelix_skill.py` 5, `tests/test_docs_bundle_sync.py` 7,
`tests/packaging/test_docs_bundle.py` 5, `tests/extensions/test_extension_repr_redaction.py` 2), plus
eight existing extension-count assertions moved by one and one existing test rewritten
(`test_absent_hint_names_paths_that_exist`, which had been verifying an installed user's hint by
reading `docs/guides/` **out of the repo** — the source-tree check its own docstring says it exists
to reject). Full suite on this branch: **8 579 passed, 1 skipped, 0 failed**
(`pytest tests/ -q -p no:randomly`, 449 s).

Every new test was shown failing before the change it covers — by reverting the production file or
by mutating it, never by inspection. Three of those runs are worth recording because each caught a
test that was green for the wrong reason:

- **A globbed corpus test passes over an empty corpus.** Five of the seven tests in
  `test_docs_bundle_sync.py` were green before the bundle existed, iterating zero files. They now
  carry `assert BUNDLED_DOCS, …`. `test_every_bundled_guide_fits_in_one_read` had the same defect
  from the other direction and was restructured to assert the prompt's promise string first.
- **A `--system-prompt` guard that checks only `options.system_prompt` is vacuous.** Append chunks
  survive the override by design (`_resolve_append_chunks`, called at `entry.py:1363`). Appending
  the docs block *there* — the rejected design — leaves `system_prompt` clean and only
  `append_system_prompt` dirty, so the first draft of that test would have passed under exactly the
  design it rejects.
- **`Path(__file__).resolve().parents[2] / "docs"` fails silently in an installed layout** — that is
  site-packages, which exists and simply has no `docs/`, so the product reports "no guides are
  bundled" rather than raising. A source checkout agrees for the wrong reason (`src/` also has no
  `docs/`), so only the installed-layout test can see it.

Beyond unit tests, the wheel was installed into a fresh venv **without** the `tui` extra
(`rich`/`prompt_toolkit` both `find_spec -> None`): `aelix docs` listed 8 topics, `aelix docs security`
printed the guide, `aelix docs bogus` exited 2 with 0 bytes on stdout, and
`aelix docs extension | head -1` printed `# Writing an Extension` with no `BrokenPipeError`.

### Residuals, stated plainly

- **`skills` has no guide, and none was invented.** `aelix docs skills` exits 2 with a pointer
  rather than handing over a document about something else, and a test pins the decision so
  "we forgot" cannot later look like "we decided". The gap is narrower than that message implies,
  though: the packaged **`writing-skills` skill ships in the same wheel** (3 972 B) and covers the
  SKILL.md format, the frontmatter rules, the three load tiers and the trust gate. The near-miss
  text names `agent-profiles` and `project-trust` and does **not** name it, so a user is currently
  told less than the install actually contains. Same for `mcp` and `hooks`, which are in the same
  state. Worth its own issue.
- **No sdist → wheel round trip is tested.** `tests/packaging/test_build_hygiene.py` builds a wheel
  and an sdist from one source copy and inspects each, but never unpacks the sdist and rebuilds from
  it. That gap is recorded in `tests/packaging/test_docs_bundle.py`'s module docstring; inventing the
  pattern for one directory, when it wants to cover every target, was declined.
- **`aelix docs` prints plain text, not rendered markdown.** Deliberate: `cli/docs.py` must import
  with no `[tui]` extra, and a pipe has to receive clean markdown. A rendered viewer is a separate
  surface.
- **`--search` is substring, not regex, and has no topic scoping.** Intentional — a user searching
  `models.json` or `[capabilities]` should not have to escape anything — but if regex is wanted later
  it is a new flag, not a change to the default.

### Unmeasured, and flagged as such

**No real model was run against any behaviour this ADR claims.** Every number here came from prompt
text produced by the real code path, from a built wheel's zip index, from the real CLI's
stdout/stderr and exit codes, or from the real `tool.execute()`. One provider turn was spent, and it
was spent elsewhere: the `--probe-flag` run above needed a live model only because a flag value can
only be read from inside a running session, and what it measured was the *flag* surface, not
anything #101 built. So:

- that a model asked "how do I publish my extension?" now *reads* `extension-authoring.md` rather
  than guessing is **not** verified. #117's comparable prompt block was justified by driving a real
  model against a built wheel (it failed 2/2 before, and wrote a loadable extension after); nothing
  equivalent was done here.
- that the block's scope clause (*"read one only when the user asks about Aelix itself"*) actually
  suppresses doc-reading during ordinary work is asserted as a string, not shown as a behaviour. pi's
  identical clause is the precedent, not evidence.
- that a model *calls* `aelix_status` and uses the answer is likewise unverified.

Given this repo's record of false greens on prompt, render and hook paths specifically, that
distinction is the reason it is written down here rather than left implied.
