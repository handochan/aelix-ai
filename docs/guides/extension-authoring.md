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
| `register_provider(name, config)` / `unregister_provider(name)` | Register / drop a model provider.  |
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
