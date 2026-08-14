---
name: extending-aelix
description: How to extend Aelix itself in plain Python — the setup(aelix) entry point, the eight register_* surfaces, the hook events, where extension files go, and how to load them. Read this when asked to add a tool, slash command, hook, keyboard shortcut, model provider or custom renderer to Aelix.
---

# Extending Aelix

An extension is **one Python file with a `setup` function**. Aelix imports it and
runs it in this process. For that file there is no manifest, no JSON, no build
step and nothing to install, so if you find yourself inventing a config format
for one, stop — that is the signal you are guessing. (Packaging an extension for
*distribution* is a different job, and it does have a manifest format:
`aelix-plugin.toml`. See the guides below before writing one.)

```python
def setup(aelix):
    aelix.register_tool(my_tool)
```

## Read the source, do not recall the API

The signatures change faster than anyone's memory of them. Two files ship inside
Aelix and are the ground truth:

- A complete worked example: `aelix_coding_agent/examples/echo/echo.py` — one
  tool plus one slash command, short enough to read whole.
- The full API: `aelix_coding_agent/extensions/api.py` — too big to read whole.
  Grep it for the surface you need (`grep -nE 'def (register_|on\()'`) and read
  at the line it reports.

The system prompt already gives you the absolute paths for both. Use `read` on
them before writing code.

## The guides, for what the source does not say

Those two files are signatures and one worked example. Everything around them —
the `aelix-plugin.toml` manifest, the capability gates, MCP servers, packaging
and publishing — is in the guides bundled inside this install. They work with no
network:

```
aelix docs                              # list every guide
aelix docs extension-authoring          # the one for this skill
aelix docs --search capabilities        # grep all of them
```

`aelix docs` needs the `bash` tool, and `bash` is blocked in plan mode. When it
is unavailable, `read` the guides directly. They are `<name>.md` in
`aelix_coding_agent/docs/` — two directories up from this skill file — and the
system prompt prints that directory as an absolute path. Each guide is small
enough for `read` to return whole.

## The eight surfaces

`setup(aelix)` receives an `ExtensionAPI` with exactly these registration
methods:

| Method | Adds |
|---|---|
| `register_tool` | A tool the model can call |
| `register_command` | A `/slash` command |
| `register_shortcut` | A key binding |
| `register_flag` | A CLI flag |
| `register_provider` | A model provider |
| `register_api_adapter` | A wire protocol for a provider |
| `register_login_provider` | A `/login` method |
| `register_message_renderer` | Custom transcript rendering |

Plus `aelix.on(event, handler)` for hooks. Events include `tool_call`,
`tool_result`, `turn_start`, `turn_end`, `agent_start`, `agent_end`,
`message_start`, `message_end`, `session_start`, `session_shutdown`,
`before_provider_request`, `after_provider_response`, `user_bash`,
`resources_discover` and `project_trust`. Grep `api.py` for `def on(` to see the
full set with its payload types — they are overloaded per event, so the type of
the handler argument depends on the event name.

A tool is an `AgentTool` with `name`, `description`, a JSON-Schema `parameters`
dict and an async `execute(args, context) -> ToolResult`. Copy the shape from
`echo.py` rather than reconstructing it.

## Where the file goes

- `~/.aelix/agent/extensions/<name>.py` — every project, no trust gate. This is
  the default choice.
- `<project>/.aelix/extensions/<name>.py` — this project only, and **only if the
  project is trusted**. Aelix executes this file with your full privileges, so a
  cloned repo's extensions sit behind a one-time consent prompt and are skipped
  silently when declined.
- `-e <path>` loads one explicitly and is never gated.

Writing to either directory may ask for approval. That is not a refusal — if the
write is declined or blocked, say so and stop. Do not retry it through `bash` and
do not write somewhere else to dodge the prompt.

## Loading it

You cannot load your own extension mid-session. Ask the user to run `/reload`,
or to restart Aelix if `/reload` does not pick it up. If you are not in an
interactive session, report the absolute path you wrote and stop there.

`/extension` lists what is loaded and what was refused, which is the fastest way
to see whether your file was picked up and why not.

## Related

For knowledge rather than code, write a skill instead — see the `writing-skills`
skill. A skill needs no reload and cannot execute anything.
