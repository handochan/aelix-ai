# aelix-ai

Provider-agnostic AI primitives for the [Aelix](https://github.com/handochan/aelix-ai)
agent runtime: message types, streaming primitives, and tool definitions, with
pi-ai parity. No agent loop, no hook bus.

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
