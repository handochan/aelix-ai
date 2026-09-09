# aelix-ai

Provider-agnostic AI primitives for the [Aelix](https://github.com/handochan/aelix-ai)
agent runtime: message types, streaming primitives, and tool definitions, with
pi-ai parity. No agent loop, no hook bus.

This package is part of the Aelix distribution set. For the full runtime, install
the umbrella package:

```bash
pip install aelix
```

> **Below `0.1.0b2` that command installs a placeholder, not Aelix.** The PyPI
> names were reserved by a metadata-only `0.0.0a0` pre-release, and with nothing
> but pre-releases on the index pip takes the newest — so it reported success and
> left you with no `aelix` command. From `0.1.0b2` on a pre-release tag publishes
> the real distribution (ADR-0240) and the command above resolves it. The
> checksum-verified installer described in the project README is still the
> recommended path: it is the only one that verifies the wheels it installs.

See the [project README](https://github.com/handochan/aelix-ai#readme) for
architecture, usage, and the CLI/TUI.

Licensed under Apache-2.0.
