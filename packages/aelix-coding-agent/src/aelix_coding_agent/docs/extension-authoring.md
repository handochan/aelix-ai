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

### Your tool goes through the approval prompt

Aelix's permission gate treats every tool it did not build itself as one that
can change things, the same way it treats `bash`, `write` and `edit`
([ADR-0253](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0253-the-permission-gate-treats-any-tool-it-does-not-know-as-mutating.md)).
That covers your extension's tools and every MCP tool. The gate decides from
the tool *object* that will run, not from its name, so naming your tool `read`
or `grep` does not make it read-only. Posture by posture:

| posture | your tool |
| --- | --- |
| `default` | asks first. The dialog shows the tool name and every argument, one per row, each value whole (a long one wraps). If the arguments do not fit, a line under them says how many lines are hidden and that PgUp/PgDn scroll to them, and Yes is held until every line has been on screen (Aelix's own `bash`, `write` and `edit` prompts hold Yes the same way). "Yes, for this session" approves this one tool, by its exact name, for any arguments, until exit. |
| `plan` | blocked, headless included. The model is told why. |
| `auto-accept-edits`, `auto` | asks. These postures auto-accept only Aelix's own file edits, and only inside the project. |
| `yolo` | runs, no prompt. |
| `-p` / `--mode json` / `--mode rpc` | runs, no prompt, as `bash` does (blocked in `plan`). |
| inside a delegated agent | refused in every posture except `yolo`. |


Embedding hosts that bind a UI with only the public `select` protocol cannot approve
prompted built-in `write` or `edit` calls. These calls are denied before any picker,
with a reason asking the host to configure a file-change preview approval runner
(#395). Wire `PermissionExtension(approval_runner=...)` to present the full
`ApprovalRequest` and return its `ApprovalDecision`. Aelix's TUI already does this.
Bash and third-party tools keep their whole-command or whole-argument select fallback;
existing session grants, automatic postures and headless policy are unchanged.

Over `--mode rpc`, a host that enables dialogs (`aelix_control` `ui/enable`,
[ADR-0275](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0275-acp-runtime-controls-use-a-separate-rpc-envelope.md))
gets one only while its `commands/run` or `agents/run` request runs. There
`ctx.has_ui` is true: `select` / `confirm` / `input` / `editor` go to the host
and come back as a timeout or cancel when nobody answers, `notify`, `set_status`,
`set_title` and line-list `set_widget` reach it as notices, and terminal-only
methods (`custom`, editor control, working indicators, themes) do nothing. That
dialog channel does not approve tool calls: the permission gate keeps its
`--mode rpc` verdict in the table above, and the host approves through its own
`tool_call` hook.

There is no flag your tool can set to skip this. For an MCP tool, a
`readOnlyHint` annotation does not skip it either, because a hint is the tool
describing itself. If a read-only tool of yours prompts too often, "Yes, for
this session" is the answer today. Persistent per-tool rules are tracked in
[#29](https://github.com/handochan/aelix-ai/issues/29).

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

`select` wraps its title, as pi's does, and `confirm` its title and message
(#399), by grapheme cluster: a letter keeps its accents and an emoji sequence
or a flag is not split across rows. Known limit: a single cluster wider than a
whole row of the dialog (an accented letter in a one-column row, a ZWJ family
emoji in a six-column row) is cut by character. A control character in them is drawn by name in
reverse video (`^M`, `^[`, `<U+202E>`), never obeyed and never dropped. A question that fits the
dialog with the first row of its highlighted option is drawn whole and answers at once; the
options scroll under it when they do not all fit (the highlighted one is
always on screen, and a label of several lines is drawn from its first line and
cut from the bottom when there is no room for all of it, a `⋮` row marking the
rest while there is a row for it: #424; one row can stay blank instead, that
label left behind a `⋮`, when it is the nearest label above and would need a
`⋮` on each side of its first line). A question taller than that scrolls (PgUp/PgDn), and **no
option is taken** (Enter, Space or Ctrl+J in `select`, `y` in `confirm`) until
every row of it has been drawn; Esc and Ctrl+C always answer, and so does `n`
in `confirm` (in `select`, `n` types into the filter). An Enter or
`y` typed before the dialog is first drawn is dropped, not queued. So pi's
`permission-gate.ts` example, ported, shows the whole command and runs it only
once its end has been on screen. Your own `No` or `Cancel` option is held like
any other while the question is held (aelix cannot know what a label does):
Esc is the answer that is always taken. Option/detail and tab name/body/error
controls are displayed by name in reverse video before picker styling (#179).
Newlines remain rows; CJK, accents and ZWJ sequences stay intact. Filtering and
width use this safe display text. `select` still returns the original option
string, and detail receives its original index; caller ANSI is never trusted.

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
| `register_setting(name, *, label, get_value, set_value, description="")` | Contribute an extension-owned global boolean toggle to `/settings`. |
| `register_provider(name, config)` / `unregister_provider(name)` | Register / drop a model provider. A `config.models` map surfaces in `/model` (once a credential is stored) and is selectable at launch — `--model <name>/<id>` or `--provider <name> --model <id>`. |
| `register_login_provider(provider)` / `unregister_login_provider(id)` | Add / drop a custom `/login` method with your own credential flow (see [extension-providers.md](extension-providers.md#custom-providers-with-their-own-login-method)). |
| `register_api_adapter(api, stream_fn)` / `unregister_api_adapter(api)` | Register a custom wire-protocol adapter for an endpoint config can't express (see [extension-providers.md](extension-providers.md#when-config-isnt-enough--a-custom-wire-adapter)). |
| `register_flag(...)` / `get_flag(name)` | Declare a flag / read its value (bool, str, or `None`). A user sets one on the command line — see [Flags](#flags). |
| `on(...)`                           | Subscribe to a typed hook event (e.g. the tool-call lifecycle). |
| `get_active_tools()` / `get_system_prompt()` | Inspect the running agent.                      |
| `set_active_tools(names)` / `get_all_tools()` | Choose which registered tools are on. It can only NARROW what the run allows: a tool `--exclude-tools` removed, one `--no-tools` / `--tools` / `--no-builtin-tools` or an agent profile locks off, or one the user turned off in `defaultTools` (`/tool`) is left off without an error; an unknown name raises. See [tools.md](tools.md). |
| `append_entry(custom_type, data=None)` | Record a `CustomEntry` in the session file. The model never sees it. Name it with your own `custom_type` (the `aelix.` namespace is for first-party records) and keep `data` to JSON values: lists rather than tuples, string keys, finite floats. See ADR-0242. |

`on(...)` subscribes to the harness hook events (the same lifecycle the core
`HookBus` drives — `before_agent_start`, the tool-call hooks, context mutations,
and so on). Use it to enforce policy, audit tool calls, or mutate context. The
built-in `PolicyExtension` and `GuardrailExtension` are themselves extensions
that register `tool_call` handlers this way.

A hook handler registered through `on(...)` receives a read-only
`ExtensionContext` (`ctx`) that exposes per-turn fields such as `ctx.cwd` and
`ctx.model`. Those live on the context passed to your handler — not on the
`aelix` handle itself.

An `input_withdrawn` handler hears that a user's Esc or an RPC `abort` took input the
model never answered out of context. `event.messages` is what the caller may give back
to the user, with the live prompt first when `event.prompt_withdrawn` is true and the
steer and follow-up lines the abort cleared last; a `before_agent_start` message of yours
is not in it, because the next prompt asks you again. It is observational. On a turn's
abort it comes before that turn's `turn_end` and `agent_end`; on an abort between runs, such
as in the retry countdown, it comes after them; with no prompt running it comes from the
abort itself, for those lines alone. An `abort` handler does not tell you this: it fires
for every abort, and clears the queues.

A `model_select` or `thinking_level_select` handler runs after the change: the
model or level is already in force. Raising there does not undo it. The command
that made the switch (`/model`, `/thinking`, `/settings`, the pick after
`/login`, RPC's `set_model` or `cycle_model`) says it failed, the footer shows the
model and level the next request uses, and that request goes with them. A
model refused between turns is not recorded when it is refused. If the session
is closed before another prompt, reopening it brings back the model it was on
before. If a prompt follows, it runs on the refused model and its answer
records that model, so reopening the session comes back on the refused model.
`/agents use` is the one command that
puts its model and level back when it fails. Your handler also runs before the
TUI's own footer repaint, so a raise there skips it. The commands repaint
anyway (#559), but a `ctx.set_model` or `ctx.set_thinking_level` from an
extension that your handler refuses leaves the footer on the old value until
the next repaint.

The full surface is defined in
`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`.

## Current support status

Every imperative `register_*` surface listed above is wired end-to-end today —
a registration made in `setup()` reaches the running agent, not just a store:

| Surface | Status | How it reaches the runtime |
| ------- | ------ | -------------------------- |
| `register_tool` | Live | Refreshes the harness tool registry and auto-activates the tool. |
| `register_command` | Live in **both** the TUI and RPC | The TUI resolves it through `CommandDispatchService` after built-ins; RPC exposes it via `get_registered_commands`. |
| `register_setting` | Live in the TUI | `/settings` reads current runner contributions each time it opens/reopens. The getter reads the owner's persisted value; the setter can be synchronous or async and completes before the menu reports success. |
| `register_provider` / `unregister_provider` | Live | `setup()` registrations bind before launch lookup and replay on rebuilds (ADR-0249). Late registrations reach the registry but cannot claim a pending launch. See [Launch and rebuilds](extension-providers.md#launch-and-rebuilds) and [ownership notes](extension-providers.md#custom-providers-with-their-own-login-method). |
| `register_login_provider` | Live (interactive TTY only) | The `/login` wizard reads the registry to build its method list. Not available under `--print` / `--json` / `--mode rpc`, which have no wizard. |
| `register_api_adapter` | Live, and re-applied across `/reload` | Fanned out to the process-global api registry, and replayed on every harness rebuild (the reload resets that registry). |
| `register_flag` / `get_flag` | Registration live, **no way to set one** | The declared default seeds the runtime flag values and `get_flag` reads them back — but nothing threads a command line into that dict, so `get_flag` only ever returns your default. See the warning below. |
| `on(...)` hooks | Live | Dispatched by the harness hook bus — the built-in Guardrail / Permission extensions use the same path. |
| `register_shortcut` | Live | Aggregated by `get_shortcuts` and read live by the TUI chrome at key-fire time, so `/reload` handler swaps take effect. First registration wins a key collision. |
| `register_message_renderer` | Live | Looked up live per custom message by `get_message_renderer`; first extension in load order wins. A renderer that raises falls back to default rendering. |

### Global settings toggles

An extension can add a live boolean row to `/settings` without adding a field to
the host's settings schema. Persist the global choice in your extension's
user-owned storage and expose that same value through any slash/CLI controls:

```python
def setup(aelix):
    store = MyUserSettings()  # reads do not initialize missing storage
    aelix.register_setting(
        "enabled",
        label="Memory",
        get_value=store.is_enabled,       # returns exactly bool; read-only
        set_value=store.set_enabled,      # accepts bool; sync or async
        description="Use memory globally across projects and sessions.",
    )
```

Registration calls neither callback. Opening the menu reads the current value;
selection toggles through the owner, awaits persistence and rereads it to confirm
the change. The desired value is the opposite of the value shown in the selected
row; another process changing it while the menu is open cannot reverse the user's
ON/OFF intent. Callback failure makes the row unavailable with a generic diagnostic;
exception text is not displayed. Labels are qualified when they collide with
built-ins or other contributions, with unique final labels. Built-in settings
continue to use `SettingsManager` unchanged.

Reload replaces the runner's contributions. Stale callbacks are rejected before
owner invocation, including invalidation between coroutine creation and awaiting.
An operation already running belongs to the extension: its setter must implement
any cancellation or transactional revocation its own persistence requires.
`/settings` is interactive; this API does not introduce a headless/RPC settings UI.
This surface is an Aelix addition (ADR-0256), separate from `register_flag`.

### What reaches the terminal

The transcript strips control characters from text your extension, a tool or
the model supplied before it draws that text (#177). Your tool's result is shown
with escape sequences removed (colour included) and other control characters
deleted. Newlines and tabs are kept. The tool name and its arguments are shown
on one header row. A custom message's default rendering is stripped the same
way, and also loses the BiDi marks U+200E, U+200F and U+061C. A
`register_message_renderer` component keeps its SGR colours. Any other control
character in its lines (a C1 CSI, a BiDi override, one of those three marks) is
drawn as a space. An OSC 8 hyperlink in a component is kept only when its target
is an `http`, `https`, `file` or `mailto` URL with no control character or BiDi
control in it. Any other link is dropped and its text is kept. A `tool-renderer-desc`
view draws its `title` and column headers on one row each, with control
characters shown as spaces, and strips every value it decodes from your tool's
JSON (a cell, a form field's name and value, a grid item, the text body) the
way a tool result is stripped. It strips them after it decodes your JSON, so
a column's `key`, `rows_path` and `text_path` are matched against your JSON
keys exactly as you wrote them, escaped (`\u200b`) or not. The status line, widgets, toasts and modals you
register are drawn outside the transcript and are not stripped this way. Every
displayed descriptor string is literal text (#390), including titles, headers,
table cells, form names and values, grid items and text bodies. Rich tags such as
`[bold]` and `[link=…]` appear as written, including their targets; they do not
apply styles or create hyperlinks. This also applies to the shared descriptor
builder used by `render_tool_result` and management modals.

### Flags

`register_flag` declares a flag and `get_flag` reads it, and a value given on the
command line reaches the extension on its first build. Measured against the
shipped CLI — an extension registering `probe-flag` with `default="DEFAULT"`,
launched as `aelix -e probe.py --probe-flag FROM_CLI --print …` — printed
`get_flag('probe-flag') = 'FROM_CLI'`; the same extension launched with no flag
printed `'DEFAULT'`. A valueless `--probe-bool` arrives as `True`.

The parser puts an unrecognised `--` token into `Args.unknown_flags` with no
diagnostic, and the first harness build seeds that dict into the runtime's flag
values ([#92](https://github.com/handochan/aelix-ai/issues/92)). A declared
default is never clobbered: seeding only supplies names the user actually
passed. `/reload` restores the values the previous runtime held, so a flag
survives a reload as well.

Manifest `contributes.*` families are a different story — declaring one is **not**
the same as registering it. What each family actually does at runtime:

| Family | Runtime effect |
| ------ | -------------- |
| `contributes.hooks` | **Activated, capability-gated** — each entry registers a subprocess hook, and the whole pack's load is **refused** unless `capabilities.shell_exec = true` (the host spawns your command). Also forces eager loading. See [Capabilities](#capabilities--three-are-gates-six-are-documentation). |
| `contributes.themes` | **Activated** — registered at TUI start and offered in `/settings → Theme` (see [Themes](#themes)). Forces eager loading. Not gated. |
| `contributes.tui_widgets` | **Activated, capability-gated** — painted into the chrome at TUI start, but **refused before your module is imported** unless `capabilities.ui_tui_trusted = true`. Forces eager loading. See [Capabilities](#capabilities--three-are-gates-six-are-documentation). |
| `contributes.mcp_servers` | **Activated** — consumed by the MCP client at startup, and **capability-gated**: `transport = "stdio"` requires `capabilities.shell_exec = true` (the host spawns your `command`/`args` as a subprocess — the same flag `contributes.hooks` needs), while `transport = "http"`/`"sse"` requires `capabilities.net = true` (the host opens the connection to your `url`). A server whose flag is false is **not started**, and the host prints why. A server the user's `mcp.json` also names is **not started** either — that entry wins the name whether it is enabled or not, and the host prints a notice (see [mcp-servers](mcp-servers.md#servers-an-extension-declares)). There is no `enabled` key: a manifest that sets one does not validate. |
| `contributes.commands` | **Metadata only** — supplies the *description* for a lazily-activated plugin's command stub in autocomplete. The command itself must still come from `register_command`. |
| `contributes.tools` | **Declaration only** — it forces the plugin to load eagerly so its tools are visible to the model from startup, but it does not itself create a tool. Register the tool with `register_tool`. |
| `contributes.descriptors` | **Reserved and inert** — declaring it logs a warning. Emit descriptors at runtime instead (see [Descriptors](#descriptors-runtime-emitted-not-manifest)). |

## Custom providers with their own `/login` method

A model provider of your own, its `/login` method and a custom wire adapter
(`register_provider`, `register_login_provider`, `register_api_adapter`) are
in [extension-providers.md](extension-providers.md).

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

A module name — here, in a manifest's `[plugin.entry] python`, or in an
`aelix.extensions` entry point — is imported from the environment aelix is
installed in, never from the directory you run aelix in: when the way aelix was
started put that directory on the import path (`python -m aelix_coding_agent`,
`python -c`, an empty `PYTHONPATH` segment), it is taken off for the import,
and a same-named module already imported through it is refused (#114, #641). A
directory your own environment put there stays — a folder `PYTHONPATH` names,
or the `.pth` that `pip install -e .` writes for
a flat-layout pack, so you can run aelix from the root of the pack you are
developing. Delegated agents start with `python -P`, which keeps their working
folder off the import path entirely. To load a file that sits in your project,
give its path (`-e ./my_extension.py`) or put it under `.aelix/extensions/`,
where [Project trust](#project-trust) decides.

If the directory you run in holds a file or folder with the module's name
(`my_package.my_extension`, or `acme` for `-e acme`), that path wins and a
warning says so — but only in a folder you trusted yourself (`--approve`, a
saved "Trust", or `defaultProjectTrust: "always"`). Anywhere else, a folder
with nothing to gate included, the entry is refused instead (#638): write `-e
./acme` to load the folder, or `-e acme:setup` — the same factory, spelled so
it can only be the installed module.

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
`entry_points` are always loaded — except a module-shaped `-e` that only a path
in the project answers, which loads only in a folder you trusted explicitly
(above). See ADR-0149 for the trust model.

## Disabling extensions

```bash
aelix --no-extensions      # disable auto-discovery (project-local + global + entry_points); -ne
```

`--no-extensions` (`-ne`) turns off the three auto-discovery channels;
extensions you pass explicitly with `-e` still load.

### Turning one extension off

`/extension` opens on its **Installed** tab, a list of every extension aelix
found — on or off — grouped by where it came from: BUILT-IN, PROJECT
(`.aelix/extensions`), GLOBAL (`~/.aelix/agent/extensions`), INSTALLED
(entry points), THIS RUN (`-e`), and the MCP servers. Space turns the row under
the cursor on or off, ← → picks the file Enter saves to (**Global** or **This
project**), typing searches, Enter saves, Esc discards; Tab moves to the
Discover and Sources tabs and back, and unsaved changes wait for you there.

Nothing changes until **`/reload`** (or a restart): the row says `off · /reload`
until then. After `/reload` an extension that is off is not imported at all —
its tools, commands, hooks and skills are gone — and `/reload` leaves one line
saying what changed and what failed:

```
Extensions after /reload: weather.py off · broken-ext.py failed
    ✖ broken-ext.py failed to load: RuntimeError: boom (see /extension)
```

A pack whose manifest no longer parses is said there too (`✖ broken was not
loaded: Invalid manifest …`). `/new`, `/fork` and `/resume` say what failed to
load below the new session's banner instead of printing it over the input.

An extension that raises in its import or `setup()` — `sys.exit()` included —
is skipped with that line, and the rest load. A `KeyboardInterrupt` there (or
Ctrl+C while a slow `setup()` runs) ends aelix with one line naming the
extension.

One extension failing is never a failed `/reload`. When the reload itself fails
— re-reading `settings.json` or building the session raised — aelix builds the
session again on the settings it was running with, with Guardrail, Permission
and every extension's hooks, and says so:

```
✖ /reload failed during the rebuild: PermissionError: … — kept the previous settings; fix the cause, then /reload again
```

If that fails too, aelix stops rather than run anything without its checks:
prompts, `!` lines, tool calls, steering and follow-up messages are refused
with "aelix is stopped until /reload succeeds" until a `/reload` (or `/new`)
works; `/`-commands still run. An extension's `ctx.reload()`, RPC
`resources/reload` and a failed `/new`, `/resume`, `/fork` or `/import` raise
`RebuildFailedError` with the same words (ADR-0289).

One reload or session change runs at a time. A `ctx.reload()`,
`ctx.new_session()`, `ctx.fork()` or `ctx.switch_session()` asked for while
another runs waits for it — but one asked for from inside it (a
`session_shutdown`, `session_start` or `resources_discover` handler, or
`setup()`) raises `RuntimeError` instead, since it would wait on itself. A
`with_session` callback runs after the change is done, so it may start another.

The panel writes the `extensions` list of `settings.json`, the same grammar as
`skills` (see [skills-and-prompts](skills-and-prompts.md)): `-name` turns an
extension off, `+name` back on, `!glob` turns off what matches, and the
project's file decides over the global one. The name is the manifest's
`plugin.id`, an installed pack's entry-point or distribution name, a `.py`
file's name without `.py`, or a package directory's name — for a package
whose `pyproject.toml` lists its files under `[tool.aelix] extensions`, the
directory's name turns them all off, and each file's own name (or its row's
label, `bundle/worker.py`) just that one:

```json
{ "extensions": ["-weather", "-jira-tools"] }
```

A path works too (`-extensions/weather.py`, relative to the file's folder; on
Windows in any case; a symlinked `~/.aelix` is fine). A plain path with no
`-`/`+`/`!` — which in pi adds a place to look for extensions — is not
supported yet; aelix warns and ignores it, as it does `-builtin:…` (the
built-ins below cannot be turned off). An entry that names no extension aelix
found is warned about at launch.

When two extensions share a name — a global and a project `weather.py` — the
panel writes the one you toggled by its path, so the other keeps its value;
the rows read `weather.py (global)` and `weather.py (project)`, here and in
`/tool`. A file can name by path only its own extensions, so turning the global
one off is saved to **Global**; the panel says so if you pick the other file.
Turning both off in one save writes `-weather`, to either file. A hand-written
`-weather` turns both off; turning one back on would turn the other on too,
and the panel cannot keep that one off for you, so it says what to add by
hand — `add "-extensions/weather.py" to .aelix/settings.json, then save
again`.

What cannot be turned off here, and says why on its row: Guardrail, Permission
and Status (always on), the Agents extension (it follows `[features] agents` in
`/settings`), an extension you named with `-e` (for this run), and everything
under `--no-extensions` (run without it to turn them on and off). An untrusted
project's extensions are one row with how many there are; nothing in that
folder is read.

Some things wait for a restart even after `/reload`, and the save and the
`/reload` line say so per extension: an MCP server an extension declared keeps
its connection (its tools are hidden) until restart, and one whose extension was
off at launch connects at the next launch; a provider an extension registered
stays in `/model`; a shortcut key an extension adds binds at the next launch.
An extension that is on but fails to load leaves the MCP servers its manifest
declared connected, with their tools in use — they were started at launch from
the manifest, and the next launch starts them again — and its row and the
`/reload` line say exactly that.

A delegated agent with `inherit_extensions: true` reads its own settings, but
never loads an extension the session that delegated to it turned off, nor
starts the MCP servers that extension declared: the parent passes what it keeps
off along (`$AELIX_EXTENSIONS_PARENT_OFF`, which can only turn extensions off).
Under `--no-extensions` its agents load only their own `-e` extensions.

`aelix status` lists the extensions the settings turn off, without importing
them.

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
# themes/solarized.toml — only these eight roles are styled; unknown keys are ignored
name = "solarized"

[roles]
assistant  = "cyan"
tool       = "yellow"
error      = "red"
dim        = "bright_black"
accent     = "blue"
thinking   = "magenta"
shell      = "magenta"
statusline = "grey70"
```

The theme is registered on TUI start (and re-reconciled on `/resume` · `/fork` ·
`/reload`) and appears in `/settings → Theme`. It is only made *available* — the
user's selected theme is never changed for them. A color Rich cannot parse is
dropped (that role renders unstyled); the file must live inside the plugin
directory. Colors are Rich color names or hex (`#89b4fa`). See ADR-0184.

`dim` also colours the lines above and below the input, and `accent` an `@file`
reference typed in it. `shell` colours the input's shell mode — a line starting
with `!`: the `!` marker, those lines and the hint under the input. A theme
without `shell` gets magenta there, so the mode still shows. `statusline` is the
footer's text, which the built-in themes draw a little dimmer than the input
(`grey70`; `grey30` in `light`). A theme without `statusline` leaves the footer in
the terminal's own colour. See ADR-0287.

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
#     no verdict: /path/to/plainpack
#   To re-check: aelix extension verify
echo $?   # 3
```

Every non-empty bucket gets its own detail line. A recorded source whose
distribution name was never detected is labelled with its path rather than a
name, and the closing hint adapts to that: `To re-check one: aelix extension
verify <name>` is printed only when every listed row carries a name `verify`
would accept, and otherwise the argument-less form above — which settles every
endpoint at once — is printed instead. A hint naming `<name>` for a
path-labelled row would be the one actionable line that cannot work, for exactly
the rows that need it.

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

The same holds for a pack turned off or on in `/extension` (#25): a pack that is
off at launch has its servers neither scanned nor started; turned on, they
connect at the next launch. A pack turned off by `/reload` keeps its servers
connected until restart, but their tools are taken away from the model at once.

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
