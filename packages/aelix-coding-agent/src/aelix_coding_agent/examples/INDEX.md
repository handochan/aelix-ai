# Shipped extension examples

Every example here is real, imported by the test suite, and **runnable** — not a
snippet. Run one with `-e` and its dotted module path:

```console
$ aelix -e aelix_coding_agent.examples.echo.echo --print "say hi"
```

That works because these ship *inside* the installed `aelix_coding_agent`
package, so they are importable wherever Aelix is (issue #114 made `-e` accept a
dotted module; before that only a filesystem path worked).

## Index

| File | Category | Purpose |
| --- | --- | --- |
| `echo/echo.py` | Tools & commands | The minimal complete extension. A `setup(aelix)` that registers one `AgentTool` (`echo`) and one slash command (`/hello`). Start here. |
| `echo/aelix-plugin.toml` | Manifest | The repository's reference `aelix-plugin.toml` (manifest v1, ADR-0096) — annotated, schema-valid, with every contribution family either used or shown commented. **Copy this file** rather than guessing a manifest format. |
| `telnaut/telnaut.py` | Providers & login | A corporate custom-provider extension (#77): a custom wire adapter via `register_api_adapter`, the models that route to it via `register_provider`, and an employee-number `/login` method via `register_login_provider`. Shows delegating to a built-in provider with `replace(opts, client=...)` so you reuse Aelix's SSE parsing — and closing the client it built in a `finally`, because the adapter you delegate to deliberately never closes a client you inject (#174). |
| `starter/` | Packaging | A real, **buildable** hatchling package (`pyproject.toml` + `aelix_starter/` with `setup()`, `aelix-plugin.toml`, `themes/example.toml`, and the `aelix.extensions` entry point). Copy this to publish an installed pack whose manifest actually ships in the wheel. See its `README.md` and the guide's [Packaging your extension](../docs/extension-authoring.md#packaging-your-extension). |

## How to load an extension

Four tiers, in the loader's own priority order (highest first):

| Tier | Spelling | What it means |
| --- | --- | --- |
| 1 | `./.aelix/extensions/` | Project-local — auto-discovered, no flag. Subject to the Project Trust gate. |
| 2 | `~/.aelix/agent/extensions/` | Your global extensions — auto-discovered, no flag. |
| 3 | `-e <path>` | An explicit `.py` file, or a directory to scan. Relative paths resolve against the cwd. |
| 3 | `-e <module>` | An explicit dotted module: `pkg.mod` (calls its top-level `setup`) or `pkg.mod:factory` (calls a named callable). |
| 4 | `entry_points(group="aelix.extensions")` | Extensions shipped by an installed package — this is what `aelix extension install <pkg>` gets you. Loaded **last**, so an installed package can never shadow a project-local file. Its `aelix-plugin.toml` **is** read, from installed metadata and without importing the package, so an installed pack is gated and can be deferred exactly like a directory pack. |

The two `-e` rows are one tier, not two priority levels: they are the same
`configured_paths` list, classified per entry.

A path that **exists on disk always wins** over the module reading, so nothing
about the older path spellings changed — but because that lets the directory
you happen to be standing in decide what a bare name means, the loader logs a
warning naming both readings whenever it happens. Conversely, `-e <module>`
resolves against the **installed environment only**: the project directory is
deliberately taken off `sys.path` for that import, so a repository cannot
substitute its own `foo.py` for your installed `foo`. See `_is_module_ref` and
`_import_path_without_cwd` in `extensions/loader.py`.

## Do I need a manifest?

No — **unless you list in the official catalog**. A single `.py` file with a
top-level `setup(aelix)` is a complete extension — `echo.py` would work with its
`aelix-plugin.toml` deleted. A **catalog-listed** pack is the one exception
(ADR-0207): its installed distribution must yield a *bound* manifest (parses +
schema-valid — it need not declare any contribution), so a marketplace entry is
an auditable declaration rather than a black box. Prove yours binds with
`aelix extension verify <name>` (exit 0). An arbitrary `pip install <pkg>` pack
is under no such obligation.

Add a manifest when you need the host to know things *before* your code runs:
lazy activation (`on_command`), declared commands/tools, declared MCP servers,
themes, TUI widgets, or subprocess hooks. The schema is
`aelix_agent_core.contracts.manifest.PluginManifest` and it is **strict**
(`extra="forbid"`) — an unknown key is a hard failure, so check the schema
instead of inventing keys.

Note the pickup rule, which surprises people. **Four** channels read a
manifest. Three of them scan a directory and pick it up when your plugin
directory is a **subdirectory of a scanned extensions directory** —
`./.aelix/extensions/<you>/`, `~/.aelix/agent/extensions/<you>/`, and
`-e <parent>` where `<parent>` contains `<you>/`. `-e` at a *directory* is a
scan: its own `*.py` children load directly **and** each subdirectory is
resolved through `aelix-plugin.toml` / `pyproject.toml [tool.aelix]` /
`__init__.py`. Only `-e <file.py>` and `-e <module>` never consult a manifest —
which is why the `aelix -e aelix_coding_agent.examples.echo.echo` invocation
above runs `echo.py` without reading a line of `echo/aelix-plugin.toml`, while
`-e path/to/examples/` would load `echo` *through* it.

The fourth channel is **tier 4, an installed package** (issue #91), and it is
the one the marketplace uses. It does not scan a directory at all: the host
reads your distribution's installed metadata (`RECORD`) and binds the
`aelix-plugin.toml` that sits inside the package directory of the entry
point's own module — `mypack.ext:setup` reads `mypack/aelix-plugin.toml`. No
code of yours is imported to decide this. Consequences worth knowing before
you publish:

* Put the manifest **in the entry module's package**. One shipped elsewhere is
  reported MISPLACED and the pack loads *without* it, so every declarative
  contribution is ignored. The error names the file. The fix is to move it, or
  to declare the optional `aelix.manifests` entry point (`<same-ep-name> =
  "<dotted.package>"`) pointing at the directory that holds it — which is also
  the answer for a **single-module** pack, since `soloext.py` has no package
  directory.
* `pip install -e` cannot be proved from metadata (an editable `RECORD` lists
  only the `.pth` and the `dist-info`), so while you develop, your pack loads
  manifest-less and says so on every start. That is expected; install it
  normally to exercise the manifest.
* Because the manifest is now read, an installed pack is **gated** like any
  other: `[[contributes.hooks]]` requires `capabilities.shell_exec = true` and
  `[[contributes.tui_widgets]]` requires `capabilities.ui_tui_trusted = true`,
  or the pack is refused before a single line of it is imported.
* Also because the manifest is now read, an installed pack whose only
  `[activation]` trigger is `on_command` is **deferred** — its `setup()` runs
  on first use of one of those commands, not at startup. Set
  `on_startup_finished = true` if you need the old eager behaviour.

One trap if you do write a manifest: `[plugin.entry] python` is resolved by a
bare `importlib.import_module`, and a plugin directory under
`./.aelix/extensions/<you>/` is **never placed on `sys.path`**. A manifest
naming its own sibling module there fails at startup with `No module named
'<you>'`. Either pip-install your plugin as a real package, or skip the
manifest and drop a bare `.py` with a top-level `setup(aelix)` — that is
already a complete extension.

## Further reading

- [`../docs/extension-authoring.md`](../docs/extension-authoring.md) — the full
  authoring guide, also readable as `aelix docs extension`.
- `extensions/api.py` — `ExtensionAPI`, the object your `setup` receives.
- `docs/decisions/0096-*.md` — the manifest v1 decision record.
