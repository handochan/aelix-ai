# Aelix User Guides

Status: Accepted

Task-oriented guides for using `aelix`. (Design rationale and decisions live in
the numbered docs and [`docs/decisions/`](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/README.md).)

These guides also ship **inside the wheel**, so an installed user can read them
with no network and no checkout: `aelix docs` lists them, `aelix docs <topic>`
prints one. That is why every cross-directory link here is an absolute URL —
`../decisions/` does not exist next to the bundled copy.

- [getting-started.md](getting-started.md) — install, set a key, run, modes,
  common flags, and the interactive TUI.
- [providers-and-models.md](providers-and-models.md) — provider API keys,
  environment variables, selecting and switching models.
- [models-json.md](models-json.md) — `~/.aelix/agent/models.json`: add custom
  providers/models, headers, Bearer auth, and built-in overrides.
- [agent-profiles.md](agent-profiles.md) — define a sub-agent: the frontmatter keys, where profiles live, and what each one is allowed to do
- [extension-authoring.md](extension-authoring.md) — write and load an extension
  (tools, slash commands, hooks, flags).
- [project-trust.md](project-trust.md) — the gate in front of a cloned repo's
  `.aelix/` resources: what it covers, what it deliberately does not
  (`AGENTS.md`), `--approve` / `--no-approve`, `/trust`, and where the answer is
  stored.
- [private-catalog.md](private-catalog.md) — run your own extension catalog:
  generate one from a directory of wheels, register it, and install from it on a
  closed or air-gapped network.
