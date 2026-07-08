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

- `~/.aelix/extensions/` — your global extensions.
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
Global extensions (`~/.aelix/extensions/`), explicit `-e`, and `entry_points`
are always loaded. See ADR-0149 for the trust model.

## Disabling extensions

```bash
aelix --no-extensions      # disable auto-discovery (project-local + global + entry_points); -ne
```

`--no-extensions` (`-ne`) turns off the three auto-discovery channels;
extensions you pass explicitly with `-e` still load.

## Manifest contributions

A manifest plugin (`aelix-plugin.toml`) can declare capabilities the host
activates without imperative registration. Two of the declarative families:

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
