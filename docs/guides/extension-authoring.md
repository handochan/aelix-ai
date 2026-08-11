# Writing an Extension

Status: Accepted

An Aelix extension is a Python factory that receives an `ExtensionAPI` handle and
registers tools, slash commands, providers, flags, and hook handlers. The core
runtime (`aelix-agent-core`) stays small; everything user-facing is layered on
through this surface (see ADR-0004 / ADR-0007).

## The factory

An extension is a module exposing a `setup` factory that takes one argument, the
`ExtensionAPI`:

```python
from aelix_coding_agent.extensions.api import ExtensionAPI


def setup(aelix: ExtensionAPI) -> None:
    ...  # register tools, commands, hooks
```

The factory runs once, at load time, before the harness binds the runtime.

## Registering a tool

A tool is an `AgentTool` with a name, a description, a JSON-Schema `parameters`
object, and an async `execute(args, ctx) -> ToolResult`:

```python
from typing import Any

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.extensions.api import ExtensionAPI


async def _echo_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    text = str(args.get("text", ""))
    return ToolResult(content=[TextContent(text=f"echoed: {text}")])


echo_tool = AgentTool(
    name="echo",
    description="Echoes back the provided text.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo back."},
        },
        "required": ["text"],
    },
    execute=_echo_execute,
)


def setup(aelix: ExtensionAPI) -> None:
    aelix.register_tool(echo_tool)
```

`register_tool` refreshes the harness tool registry and auto-activates the new
tool. Last write wins within a single extension; application-supplied tools win
over extension tools at harness assembly.

This is the worked example shipped at
`packages/aelix-coding-agent/src/aelix_coding_agent/examples/echo/echo.py`.

## Registering a slash command

```python
def setup(aelix: ExtensionAPI) -> None:
    def _hello(*_args, **_kwargs) -> str:
        return "hello from my extension"

    aelix.register_command(
        "hello",
        handler=_hello,
        description="Returns a greeting.",
    )
```

Registered commands are merged into both slash-command **autocomplete** and
**dispatch**, so typing `/hello` runs `_hello`. The handler is called as
`handler(args, ctx)`:

- `args` is the raw text after the command word (`"world"` for `/hello world`);
- `ctx` is an `ExtensionCommandContext` — drive interactive UI through
  `ctx.ui.select` / `confirm` / `input` / `notify`, and session control through
  `ctx.fork` / `new_session` / `switch_session` / `reload`.

A non-empty **string** return is shown in the transcript (a convenience for
simple commands); richer output should go through `ctx.ui`. Built-in commands
(`/help`, `/model`, …) win on a name collision, and a command colliding with
another extension's gets a `name:N` invocation name.

## Other `ExtensionAPI` surface

The handle exposes more than tools and commands. The most useful members:

| Member                              | Purpose                                                  |
| ----------------------------------- | -------------------------------------------------------- |
| `register_tool(tool)`               | Register an `AgentTool`.                                 |
| `register_command(name, *, handler, description=None)` | Register a slash command.             |
| `register_provider(name, config)` / `unregister_provider(name)` | Register / drop a model provider. A `config.models` map now surfaces in `/model` (once a credential is stored). |
| `register_login_provider(provider)` / `unregister_login_provider(id)` | Add / drop a custom `/login` method with your own credential flow (see below). |
| `register_api_adapter(api, stream_fn)` / `unregister_api_adapter(api)` | Register a custom wire-protocol adapter for an endpoint config can't express (see below). |
| `register_flag(...)` / `get_flag(name)` | Declare a flag / read its value (bool, str, or `None`). |
| `on(...)`                           | Subscribe to a typed hook event (e.g. the tool-call lifecycle). |
| `get_active_tools()` / `get_system_prompt()` | Inspect the running agent.                      |

`on(...)` subscribes to the harness hook events (the same lifecycle the core
`HookBus` drives — `before_agent_start`, the tool-call hooks, context mutations,
and so on). Use it to enforce policy, audit tool calls, or mutate context. The
built-in `PolicyExtension` and `GuardrailExtension` are themselves extensions
that register `tool_call` handlers this way.

A hook handler registered through `on(...)` receives a read-only
`ExtensionContext` (`ctx`) that exposes per-turn fields such as `ctx.cwd` and
`ctx.model`. Those live on the context passed to your handler — not on the
`aelix` handle itself.

The full surface is defined in
`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`.

## Current support status

Every imperative `register_*` surface listed above is wired end-to-end today —
a registration made in `setup()` reaches the running agent, not just a store:

| Surface | Status | How it reaches the runtime |
| ------- | ------ | -------------------------- |
| `register_tool` | Live | Refreshes the harness tool registry and auto-activates the tool. |
| `register_command` | Live in **both** the TUI and RPC | The TUI resolves it through `CommandDispatchService` after built-ins; RPC exposes it via `get_registered_commands`. |
| `register_provider` / `unregister_provider` | Live | Queued at `setup()` time, then replayed into the real `ModelRegistry` once the harness is built. `config.models` then appear in `/model` (given a stored credential). |
| `register_login_provider` | Live (interactive TTY only) | The `/login` wizard reads the registry to build its method list. Not available under `--print` / `--json` / `--mode rpc`, which have no wizard. |
| `register_api_adapter` | Live, and re-applied across `/reload` | Fanned out to the process-global api registry, and replayed on every harness rebuild (the reload resets that registry). |
| `register_flag` / `get_flag` | Live | The declared default seeds the runtime flag values; CLI overrides win. |
| `on(...)` hooks | Live | Dispatched by the harness hook bus — the built-in Guardrail / Permission extensions use the same path. |
| `register_shortcut` | Live | Aggregated by `get_shortcuts` and read live by the TUI chrome at key-fire time, so `/reload` handler swaps take effect. First registration wins a key collision. |
| `register_message_renderer` | Live | Looked up live per custom message by `get_message_renderer`; first extension in load order wins. A renderer that raises falls back to default rendering. |

Manifest `contributes.*` families are a different story — declaring one is **not**
the same as registering it. What each family actually does at runtime:

| Family | Runtime effect |
| ------ | -------------- |
| `contributes.hooks` | **Activated, capability-gated** — each entry registers a subprocess hook, and the whole pack's load is **refused** unless `capabilities.shell_exec = true` (the host spawns your command). Also forces eager loading. See [Capabilities](#capabilities--three-are-gates-six-are-documentation). |
| `contributes.themes` | **Activated** — registered at TUI start and offered in `/settings → Theme` (see [Themes](#themes)). Forces eager loading. Not gated. |
| `contributes.tui_widgets` | **Activated, capability-gated** — painted into the chrome at TUI start, but **refused before your module is imported** unless `capabilities.ui_tui_trusted = true`. Forces eager loading. See [Capabilities](#capabilities--three-are-gates-six-are-documentation). |
| `contributes.mcp_servers` | **Activated** — consumed by the MCP client at startup, and **capability-gated**: `transport = "stdio"` requires `capabilities.shell_exec = true` (the host spawns your `command`/`args` as a subprocess — the same flag `contributes.hooks` needs), while `transport = "http"`/`"sse"` requires `capabilities.net = true` (the host opens the connection to your `url`). A server whose flag is false is **not started**, and the host prints why. |
| `contributes.commands` | **Metadata only** — supplies the *description* for a lazily-activated plugin's command stub in autocomplete. The command itself must still come from `register_command`. |
| `contributes.tools` | **Declaration only** — it forces the plugin to load eagerly so its tools are visible to the model from startup, but it does not itself create a tool. Register the tool with `register_tool`. |
| `contributes.descriptors` | **Reserved and inert** — declaring it logs a warning. Emit descriptors at runtime instead (see [Descriptors](#descriptors-runtime-emitted-not-manifest)). |

## Custom providers with their own `/login` method

An extension can add a private provider — say a corporate `telnaut` — and give it
its **own entry in the `/login` method list** with a custom credential flow (e.g.
"enter your employee number"). Two calls, sharing one id:

- `register_provider(id, ProviderConfigInput(...))` wires the provider for turns:
  which wire protocol it speaks (`api`), its `base_url`, and its `models` (which
  appear in `/model` once a credential is stored).
- `register_login_provider(LoginProvider(id, name, authenticate))` adds `name`
  to the `/login` method list. When the user picks it, your async `authenticate`
  handler runs a custom flow through a `LoginContext` (the same masked
  `select` / `prompt` / `confirm` / `notify` dialogs the built-in methods use) and
  returns the credential string. The wizard persists it under `id` — you never
  touch the auth store yourself.

```python
from aelix_coding_agent.login_registry import LoginContext, LoginProvider
from aelix_coding_agent.model_registry import ProviderConfigInput
from aelix_ai.streaming import Model

async def _authenticate(ctx: LoginContext) -> str | None:
    employee_no = await ctx.prompt("사번을 입력하세요")     # employee number
    if not employee_no:
        return None                                          # None = cancel
    passcode = await ctx.prompt("passcode", password=True)   # masked
    if not passcode:
        return None
    return exchange_for_token(employee_no, passcode)         # your corporate auth

def setup(aelix):
    aelix.register_provider("telnaut", ProviderConfigInput(
        name="Telnaut",
        models={"telnaut-large": Model(
            id="telnaut-large", provider="telnaut",
            api="openai-completions", base_url="https://llm.telnaut.internal/v1")},
    ))
    aelix.register_login_provider(LoginProvider(
        id="telnaut", name="Telnaut (사내)", authenticate=_authenticate))
```

Notes and limits:

- The credential your handler returns is stored under the provider `id`, so the
  same id must be used for both calls for turns to authenticate.
- `api` is normally a built-in adapter id (`openai-completions`,
  `anthropic-messages`, `google-generative-ai`, …). For an endpoint those can't
  express, register your own with `register_api_adapter` (below). A model on an
  unregistered `api` is hidden from `/model`.
- The `/login` picker is interactive-only (a TTY). In `--print` / `--json` /
  `--mode rpc` there is no wizard, so a custom login flow does not run there.
- The login registry is process-global: an extension removed on `/reload` should
  call `unregister_login_provider(id)` in its teardown.

### When config isn't enough — a custom wire adapter

If the endpoint deviates from what `ProviderConfigInput` can express — the model
in the URL path, non-OpenAI request fields, or a custom `httpx` client (e.g.
`verify=False` for a self-signed internal CA) — register a custom **StreamFn**
`(Model, Context, SimpleStreamOptions) -> AsyncIterator[event]` under your own
`api` id with `register_api_adapter(api, stream_fn)`. A `Model` whose `api` equals
that id then routes to your function.

The easiest StreamFn builds its own `openai.AsyncOpenAI` (with whatever
`http_client` / `base_url` it needs) and **delegates** to the built-in adapter via
`replace(opts, client=...)`, reusing all of aelix's SSE parsing and event mapping:

```python
from dataclasses import replace
import httpx
from openai import AsyncOpenAI
from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_PROVIDER

async def telnaut_stream(model, context, opts):
    client = AsyncOpenAI(
        http_client=httpx.AsyncClient(verify=False),      # custom TLS
        base_url=getattr(model, "base_url", "") or None,  # model baked into the URL
        api_key=opts.api_key or "",
    )
    def payload(params, _m):
        params["user"] = opts.api_key      # e.g. an employee number in a standard field
        return params
    async for ev in OPENAI_COMPLETIONS_PROVIDER.stream_simple(
        model, context, replace(opts, client=client, on_payload=payload)
    ):
        yield ev

def setup(aelix):
    aelix.register_api_adapter("telnaut-openai", telnaut_stream)
    aelix.register_provider("telnaut", ProviderConfigInput(models={"gpt5mini": Model(
        id="gpt5mini", api="telnaut-openai", base_url="https://host/v1/gpt5mini")}))
```

`register_api_adapter` re-applies your adapter across `/reload` (the api registry
is reset on reload; the harness rebuild replays your registration). Unlike the
built-in adapters, custom body keys must be OpenAI-valid or go inside
`extra_body` — the OpenAI SDK rejects unknown top-level kwargs. This is a real,
supported extension surface (no fork), but it does run your networking code:
`verify=False` disables certificate checks, so scope it to trusted internal hosts.

A complete worked example ships at
`aelix_coding_agent/examples/telnaut/telnaut.py`. Load it like any extension —
point `--extension` at the file, drop it in a project-local `.aelix/extensions/`,
or install it as a package (see [Loading an extension](#loading-an-extension)).

## Loading an extension

Pass `--extension` / `-e` (repeatable). The value can be:

- **A file path** ending in `.py` — loaded via `importlib.util.spec_from_file_location`:

  ```bash
  aelix -e ./my_extension.py
  ```

- **A dotted module path** — imported via `importlib.import_module`:

  ```bash
  aelix -e my_package.my_extension
  ```

A module is expected to expose a `setup` callable (the factory convention).

### Auto-discovery

Aelix also discovers extensions without `-e`, from three channels:

- `~/.aelix/agent/extensions/` — your global extensions. This is
  `get_agent_dir()/extensions`; override the base with the agent-dir env var
  (`AELIX_CODING_AGENT_DIR`).
- `cwd/.aelix/extensions/` — project-local extensions.
- `entry_points(group="aelix.extensions")` — extensions shipped by installed
  packages.

The directory scan is the primary channel. Errors are collected per-extension,
so one bad extension does not abort the rest.

### Project trust

Project-local extensions (`cwd/.aelix/extensions/`) run arbitrary code from the
directory you are in, so they are **gated by Project Trust**. The first time you
run `aelix` in a directory that has them, you are asked to approve; in
non-interactive mode they are denied by default. Use `--approve` / `-a` to trust
project-local resources for a run, or `--no-approve` / `-na` to ignore them.
Global extensions (`~/.aelix/agent/extensions/`), explicit `-e`, and
`entry_points` are always loaded. See ADR-0149 for the trust model.

## Disabling extensions

```bash
aelix --no-extensions      # disable auto-discovery (project-local + global + entry_points); -ne
```

`--no-extensions` (`-ne`) turns off the three auto-discovery channels;
extensions you pass explicitly with `-e` still load.

## Manifest contributions

A manifest plugin (`aelix-plugin.toml`) can declare capabilities the host
activates without imperative registration. Two of the declarative families:

### Capabilities — three are gates, six are documentation

The nine `[capabilities]` flags all default to `false` and all look alike, but
they do not behave alike. Three are **enforced**: declare the contribution
without the flag and the host refuses it, with a printed reason.

| flag | status | what it gates |
|---|---|---|
| `shell_exec` | **ENFORCED** | `[[contributes.hooks]]` (load refused) and a `transport = "stdio"` `[[contributes.mcp_servers]]` (server not started) |
| `net` | **ENFORCED** | a `transport = "http"`/`"sse"` `[[contributes.mcp_servers]]` (server not started) |
| `ui_tui_trusted` | **ENFORCED** | `[[contributes.tui_widgets]]` — refused *before* your module is imported |
| `fs_write`, `fs_read_user`, `mcp_invoke`, `ui_descriptor`, `ui_web_trusted`, `mcp_serve` | declared only | nothing yet — see below |

The other six are a statement of intent, **not a sandbox**. A plugin is
in-process Python: `fs_write = false` does not stop `open(path, "w")`, it only
says you do not mean to. Even for the enforced three the check is on the
*declaration*, at load time — a plugin that is already running can reach its
own `Capabilities` and change them.

Three traps worth naming:

- **`mcp_serve` is not the flag for `[[contributes.mcp_servers]]`.**
  `mcp_serve` means *your plugin is itself an MCP server* and forces
  `[plugin.entry] python`. `[[contributes.mcp_servers]]` is the opposite —
  config asking the host to connect **out** to a server, needing no plugin
  code at all. It is gated on `shell_exec` / `net` per the table above.
- **`shell_exec` does not unlock an http/sse server**, and `net` does not
  unlock a stdio one. Different primitives, different flags.
- **`env` on a stdio server ADDS variables — it does not pass the host's
  environment through.** Your server starts with the MCP SDK's small default
  set (`HOME`, `PATH`, `SHELL`, `TERM`, `USER`) plus exactly what you declare,
  and never the user's `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` or anything else
  they happen to have exported. A server that expects to read a credential out
  of the ambient environment will not find one, and that is deliberate: take
  it as an explicit argument, or read it from a path the user configures, so
  the manifest shows what your server consumes. (Declaring `env` used to hand
  the child the parent environment whole — which made the most harmless-looking
  line in a manifest the one that leaked keys.)

See ADR-0205 for the reasoning and `docs/contracts/manifest.schema.json` for
the same information machine-readably.

### Themes

Bundle a color theme as a plugin-relative TOML file and declare it:

```toml
# aelix-plugin.toml
[capabilities]
# (no ui_tui_trusted needed — a theme is data, not code)

[contributes]
themes = [{ path = "themes/solarized.toml" }]
```

```toml
# themes/solarized.toml — only these six roles are styled; unknown keys are ignored
name = "solarized"

[roles]
assistant = "cyan"
tool      = "yellow"
error      = "red"
dim        = "bright_black"
accent     = "blue"
thinking   = "magenta"
```

The theme is registered on TUI start (and re-reconciled on `/resume` · `/fork` ·
`/reload`) and appears in `/settings → Theme`. It is only made *available* — the
user's selected theme is never changed for them. A color Rich cannot parse is
dropped (that role renders unstyled); the file must live inside the plugin
directory. Colors are Rich color names or hex (`#89b4fa`). See ADR-0184.

### Descriptors (runtime-emitted, not manifest)

`[[contributes.descriptors]]` is **reserved and inert** — a descriptor's content
is runtime data a static declaration cannot carry. Emit descriptors at runtime by
appending to the `ui:list-modules` probe:

```python
def setup(aelix: ExtensionAPI) -> None:
    def _on_list_modules(probe) -> None:
        probe.modules.append(
            {"kind": "status-item", "namespace": "myplug", "id": "stat",
             "payload": {"kind": "status-item", "text": "ready"}}
        )
    aelix.events.on("ui:list-modules", _on_list_modules)
```

See ADR-0095 for the descriptor protocol and the full slot taxonomy.

## Packaging your extension

This section is only for the **installed-package** channel — the one the
marketplace uses and `aelix extension install <pkg>` gives you. A single `.py`
file, a directory, or a git URL need none of it. But once you build a wheel, one
detail decides whether your manifest survives the build, and the default of the
most common backend gets it wrong.

**The footgun.** When your package declares an `aelix.extensions` entry point,
the host reads your `aelix-plugin.toml` from the *installed distribution's
metadata* — it must be **inside the wheel**, at
`<your_package>/aelix-plugin.toml`. A **setuptools build with default
configuration ships `*.py` and silently DROPS non-code files** —
`aelix-plugin.toml` and `themes/*.toml` among them. The result is the worst kind
of failure: the wheel installs, your `setup()` runs, the pack looks healthy —
and every declarative contribution (declared tools/commands, themes, TUI
widgets, MCP servers, subprocess hooks) is inert, because the manifest is simply
not there to read. Nothing errors; the contribution just never appears.

**Two backends, and what each needs.**

- **hatchling (recommended).** Ships every file inside the selected package
  directory by default, data files included, so the manifest and themes ride
  along with no extra configuration:

  ```toml
  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [project.entry-points."aelix.extensions"]
  my-ext = "my_package:setup"

  [tool.hatch.build.targets.wheel]
  packages = ["my_package"]
  ```

- **setuptools.** You must opt the data files in **explicitly** — either
  `include_package_data = true` plus a `MANIFEST.in` that names them, or an
  explicit `[tool.setuptools.package-data]`:

  ```toml
  [tool.setuptools.package-data]
  my_package = ["aelix-plugin.toml", "themes/*.toml"]
  ```

**A copyable, buildable starter** lives at
`packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/` — a
hatchling project with `pyproject.toml`, a package holding `setup()` +
`aelix-plugin.toml` + `themes/example.toml`, and the entry point wired. Copy it
and change the names.

**Prove the manifest shipped — do not assume it.** After building, look inside
the wheel and then ask the host:

```bash
pip wheel . -w dist --no-deps
python -m zipfile -l dist/*.whl        # must list <pkg>/aelix-plugin.toml
pip install dist/*.whl
aelix extension verify <name>          # exit 0 == the manifest bound
```

`aelix extension verify` runs the host's own import-free resolver over your
installed endpoint and exits non-zero if the manifest is `ABSENT` (dropped from
the wheel — it prints the setuptools/package-data cause), `MALFORMED`,
`MISPLACED` (shipped, but not inside the entry module's package), or otherwise
unbindable. `aelix extension list` annotates the same verdict. Wire
`aelix extension verify <name>` into your release CI so a build that drops the
manifest fails there, not silently on a user's machine. See ADR-0207.

**`install` tells you the same thing, without being asked** (#154). Since the
host can reach that verdict offline and without importing your pack, `aelix
extension install`, `aelix extension discover install` and `aelix extension
update` all end by reporting it for the distribution they just wrote — so a user
never sees "Installed. Restart aelix" for a pack this build will refuse to load:

```bash
aelix extension install ./my-ext --yes
#   Installed, but this build already knows 1 of 1 endpoint(s) will NOT bind:
#     INCOMPATIBLE my-ext [my-ext 1.0.0]
#         Plugin 'my-ext' … requires API_LEVEL >= 7, host has 1; not loaded.
#     → not loaded at all on this build.
#   The distribution is installed; nothing was undone. To remove it:
#     aelix extension remove my-ext
echo $?   # 3
```

Four exit codes, pairwise distinct, so a script can tell them apart:

| code | meaning |
| ---- | ------- |
| `0`  | installed, and every endpoint of it binds — *or* the command could not tie the install to an installed extension distribution (see below) |
| `1`  | the installer ran and **failed**. Its own exit code is printed, not returned |
| `2`  | the installer never ran (usage error, guard refusal, your `N` at the consent prompt) |
| `3`  | installed, but some endpoint of it will **not** bind |

`1` is normalised rather than passed through on purpose: pip's own codes are not
disjoint from these. pip defines `VIRTUALENV_NOT_FOUND = 3` (raised under
`PIP_REQUIRE_VIRTUALENV`, which plenty of organisations set globally) and
`UNKNOWN_ERROR = 2`, so passing pip's code through would have let `3` mean either
"the distribution is on disk but inert" or "pip refused to run and nothing
landed" — opposite conclusions about the same machine.

**When the command has no verdict, it says so.** A `git+URL` names no
distribution, a reinstall of an unchanged pack moves nothing in the entry-point
ledger, and a source tree can hide its name where nothing but executing it can
find (setup.py-only, `[tool.poetry]`, `dynamic`). Most of those are answered by
reading pip's own PEP 610 `direct_url.json` record — a lookup, not a guess. What
that still cannot name gets an explicit *"this build has no binding verdict for
it … this is NOT a claim that the pack will load"* and a pointer to `verify`,
never the success line. The exit stays `0` there: the installer did put something
on disk, and `3` has to keep meaning "this build **knows** it will not bind".

The package is never auto-uninstalled: undoing what you asked for would be its
own surprise, so you get the exact `remove` command instead. Only the
distribution you just installed is reported, so an unrelated broken pack already
in the environment is not blamed on this install.

`install` accepts `--trust-extension-path DIST` for the same reason `verify` does
— see the next section. Without it, a pack installed outside the environment's
real site directories (`pip --target` plus a matching `PYTHONPATH`) would be
reported `UNTRUSTED_PATH`, which says "unvouched", not "broken".

**`update` reports the same verdict, with the same codes.** An upgrade is an
install: the host knows exactly as much about the bytes it just wrote, so
`aelix extension update <name>` ends with the report above and exits `3` when the
pack it just upgraded has gone inert. A bare `aelix extension update` walks every
recorded source and closes with a one-line summary, so the outcome does not
scroll away behind the per-pack reports:

```bash
aelix extension update --yes
#   … one report per pack …
#   update summary: 5 pack(s) — 1 bound, 3 NOT bound, 1 no verdict, 0 failed.
#     NOT bound: brokenpack, gitpack, legacypack
#   To re-check one: aelix extension verify <name>
echo $?   # 3
```

The summary is bad-news-only — a run where every pack bound prints exactly what
it always did, one success line per pack and nothing else. Across many packs the
run's exit code is the **hardest** outcome, not the first: `1` and `2` outrank
`3`, because `3` is the one code that asserts the installer succeeded and it may
only be reported when that is true of every pack.

### Developing with `pip install -e` — the manifest downgrade

**An editable install loads your pack WITHOUT its manifest.** This is the one
case where the development workflow does not match what your users get, and it
bites exactly when you are iterating on declarative contributions.

The entry-point tier only binds a manifest whose distribution lives inside the
environment's real site directories. An editable install deliberately does not:
the code stays in your working tree, so the provenance fence classifies it as
unproven and refuses the manifest. Your `setup()` still runs and every
*imperative* registration (`register_tool`, `register_command`, …) works
normally — but every **declarative** contribution silently vanishes: declared
tools, themes, TUI widgets, MCP servers, subprocess hooks.

The escape hatch is an explicit, per-distribution vouch:

```bash
pip install -e .
aelix --trust-extension-path my-dist-name          # PEP 503 DIST name, not the module
aelix extension verify --trust-extension-path my-dist-name
```

`--trust-extension-path` takes the **distribution** name (what `pip list` shows,
normalized per PEP 503) and is repeatable. It vouches for that one distribution
for that one run; nothing is persisted, so a plain `aelix` goes back to
refusing. Run `aelix extension verify` without the flag before you ship — that
is the verdict your users will get.

If you would rather not pass the flag every time, build and install a real wheel
(`pip wheel . -w dist --no-deps && pip install --force-reinstall dist/*.whl`),
which is also the only way to test the exact artifact you publish.

### Newly installed MCP servers need a restart

`contributes.mcp_servers` is read by a manifest scan that runs **once at
startup**, before the first harness is built. Installing a pack into a running
session — via `aelix extension install` or `/reload` — therefore does not start
its MCP servers, and no error is printed because nothing failed. Restart the
process and they come up. Tools, commands and themes from the same pack do not
need this; MCP servers specifically do.

## Publishing

An extension does not need a catalog: `-e ./my_ext.py`, a git URL, or a pip
package all install without one. A catalog only makes an extension
*discoverable* — it is an advisory index, and listing never means "reviewed" or
"safe" (ADR-0188).

A catalog-listed pack has one extra requirement (ADR-0207): the installed
distribution must yield a **bound** manifest — `aelix extension verify` must exit
0. It need not declare any contribution, but it must carry a parseable,
schema-valid `aelix-plugin.toml`, so a marketplace entry is an auditable,
gated set of declarations rather than a black-box entry-point pack. An arbitrary
`pip install <pkg>` pack is under no such obligation.

The official index is the **[Aelix Marketplace](https://handochan.github.io/aelix-marketplace/)**,
which is `DEFAULT_CATALOG_URL` — registered out of the box, though nothing is
fetched until you ask. It ships empty and is open for submissions:

```bash
aelix extension discover --refresh       # fetch the default catalog (network)
aelix extension discover                 # browse the cached snapshot
aelix extension discover install <name>  # resolve from that snapshot + install
```

`discover` and `discover install` read the local cache; only `--refresh` goes to
the network. Refresh once before looking for a freshly merged listing.

To list yours, open a pull request against
[handochan/aelix-marketplace](https://github.com/handochan/aelix-marketplace)
with an entry naming your extension and its `source` spec — see that repo's
[CONTRIBUTING.md](https://github.com/handochan/aelix-marketplace/blob/main/CONTRIBUTING.md).
Sign the artifact first if you want users to be able to gate on provenance:

```bash
aelix extension keygen                          # publisher Ed25519 key; prints a keyId
aelix extension sign <artifact> --key <keyId>   # detached .aelixsig sidecar
```

Publishing somewhere other than the public marketplace — an internal catalog for
your organisation, or a directory of wheels on an air-gapped network — is
[private-catalog.md](private-catalog.md). `aelix extension index <dir>` generates
the catalog document from your built wheels, so you never hand-maintain it.

```bash
aelix extension index dist/ --name "Acme internal"
```
