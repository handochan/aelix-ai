# aelix-agent-core

The low-level agent runtime for [Aelix](https://github.com/handochan/aelix-ai):
the stateful `Agent`, the hook-aware `AgentHarness`, the typed `HookBus`, and the
async agent loop. Core runtime with no extension dependencies. Pi-agent-core
parity.

This package is part of the Aelix distribution set. For the full runtime, install
the umbrella package:

```bash
pip install aelix
```

> **During the beta that command installs a placeholder, not Aelix.** The PyPI
> names are reserved by a metadata-only `0.0.0a0` pre-release, so `pip` reports
> success and you get no `aelix` command. Until the first GA release, install via
> the checksum-verified installer described in the project README.

See the [project README](https://github.com/handochan/aelix-ai#readme) for
architecture, usage, and the CLI/TUI.

Licensed under Apache-2.0.
