# aelix-coding-agent

The [Aelix](https://github.com/handochan/aelix-ai) coding agent: the
`ExtensionAPI` surface, the extension loader, built-in policy/guardrail
extensions, built-in tools, the interactive TUI, and the real `aelix` CLI.
Pi-coding-agent parity.

This package ships the `aelix` command. For the full runtime, install the
umbrella package:

```bash
pip install aelix
```

Optional extras:

```bash
pip install 'aelix-coding-agent[tui]'     # interactive terminal shell
pip install 'aelix-coding-agent[images]'  # inline image rendering
```

See the [project README](https://github.com/handochan/aelix-ai#readme) for
architecture, usage, and the CLI/TUI.

Licensed under Apache-2.0.
