# Aelix User Guides

Status: Accepted

Task-oriented guides for using `aelix`. (Design rationale and decisions live in
the numbered docs and [`../decisions/`](../decisions/README.md).)

- [getting-started.md](getting-started.md) — install, set a key, run, modes,
  common flags, and the interactive TUI.
- [providers-and-models.md](providers-and-models.md) — provider API keys,
  environment variables, selecting and switching models.
- [models-json.md](models-json.md) — `~/.aelix/agent/models.json`: add custom
  providers/models, headers, Bearer auth, and built-in overrides.
- [agent-profiles.md](agent-profiles.md) — define a sub-agent: the frontmatter keys, where profiles live, and what each one is allowed to do
- [extension-authoring.md](extension-authoring.md) — write and load an extension
  (tools, slash commands, hooks, flags).
