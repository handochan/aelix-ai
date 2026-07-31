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
| `telnaut/telnaut.py` | Providers & login | A corporate custom-provider extension (#77): a custom wire adapter via `register_api_adapter`, the models that route to it via `register_provider`, and an employee-number `/login` method via `register_login_provider`. Shows delegating to a built-in provider with `replace(opts, client=...)` so you reuse Aelix's SSE parsing. |

## How to load an extension

Four tiers, in the loader's own priority order (highest first):

| Tier | Spelling | What it means |
| --- | --- | --- |
| 1 | `./.aelix/extensions/` | Project-local — auto-discovered, no flag. Subject to the Project Trust gate. |
| 2 | `~/.aelix/agent/extensions/` | Your global extensions — auto-discovered, no flag. |
| 3 | `-e <path>` | An explicit `.py` file, or a directory to scan. Relative paths resolve against the cwd. |
| 3 | `-e <module>` | An explicit dotted module: `pkg.mod` (calls its top-level `setup`) or `pkg.mod:factory` (calls a named callable). |
| 4 | `entry_points(group="aelix.extensions")` | Extensions shipped by an installed package — this is what `aelix extension install <pkg>` gets you. Loaded **last**, so an installed package can never shadow a project-local file. |

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

No. A single `.py` file with a top-level `setup(aelix)` is a complete
extension — `echo.py` would work with its `aelix-plugin.toml` deleted.

Add a manifest when you need the host to know things *before* your code runs:
lazy activation (`on_command`), declared commands/tools, declared MCP servers,
themes, TUI widgets, or subprocess hooks. The schema is
`aelix_agent_core.contracts.manifest.PluginManifest` and it is **strict**
(`extra="forbid"`) — an unknown key is a hard failure, so check the schema
instead of inventing keys.

Note the pickup rule, which surprises people: a manifest is read when your
plugin directory is a **subdirectory of a scanned extensions directory**. Three
spellings scan a directory — `./.aelix/extensions/<you>/`,
`~/.aelix/agent/extensions/<you>/`, and `-e <parent>` where `<parent>` contains
`<you>/`. `-e` at a *directory* is a scan: its own `*.py` children load
directly **and** each subdirectory is resolved through `aelix-plugin.toml` /
`pyproject.toml [tool.aelix]` / `__init__.py`. Only `-e <file.py>` and
`-e <module>` never consult a manifest — which is why the `aelix -e
aelix_coding_agent.examples.echo.echo` invocation above runs `echo.py` without
reading a line of `echo/aelix-plugin.toml`, while `-e path/to/examples/` would
load `echo` *through* it.

One trap if you do write a manifest: `[plugin.entry] python` is resolved by a
bare `importlib.import_module`, and a plugin directory under
`./.aelix/extensions/<you>/` is **never placed on `sys.path`**. A manifest
naming its own sibling module there fails at startup with `No module named
'<you>'`. Either pip-install your plugin as a real package, or skip the
manifest and drop a bare `.py` with a top-level `setup(aelix)` — that is
already a complete extension.

## Further reading

- `docs/guides/extension-authoring.md` — the full authoring guide.
- `extensions/api.py` — `ExtensionAPI`, the object your `setup` receives.
- `docs/decisions/0096-*.md` — the manifest v1 decision record.
