# 0261. Root commands reject launch options before dispatch

Status: Accepted
Date: 2026-10-11
GitHub: #289
Relates: ADR-0089 (flat parser), ADR-0185 (extension commands), ADR-0218 (docs),
ADR-0222 (status), ADR-0139 (#288 offline predicate and child export).

## Problem

The entry point checked only the first argv token. `aelix --offline status`
therefore launched an agent with `status` as its message, and
`aelix -e policy.py extension list` loaded the extension and prompted an agent
instead of running the requested command. Offline intentionally permits the
configured provider, so this could send an unintended request and run tools.

## Decision

The root-command grammar is
`aelix [--offline ...] <extension|docs|status> [command arguments]`.
Repeated leading `--offline` is allowed and exports offline before dispatch.
Any other launch option before the first unconsumed command word MUST fail with
exit 2, before the flag parser, trust resolution, extension discovery, model
selection, harness construction, or session creation. Diagnostics identify
registered option names only; values, paths, prompts, and arbitrary unknown
tokens MUST NOT be echoed.

A pure classifier shares lexical value consumption with the flat parser.
Required option values remain literal even when they equal a command word or
look like a flag. Resume and list-models keep their existing optional-value
rules. An immediate `-p`/`--print` message, a file input, an ordinary prompt
already started, or bare `--` fixes the route as agent input; later command
words MUST NOT be searched again. Thus `-p status` intentionally prompts the
agent, while `-p --offline status` is a rejected command prefix.

An unknown long option directly followed by a root-command word is ambiguous
and MUST fail closed; `--custom=status` retains its explicit extension value.
Short clusters and equals forms for registered long options gain no support.
Bare `--` now ends the flat parser's options, preserving subsequent messages
and `@file` inputs, including flag-shaped messages.

`status` and `docs` MUST accept offline only as a root prefix, not through a new
postfix grammar. Existing command parsers and docs search text remain intact.
The command-first extension route MUST retain #288's raw `"--offline" in argv`
detection over the entire command tail, including after its own separator.
This compatibility rule can treat a literal extension-tail token as offline;
the new root-prefix classifier does not broaden that rule to agent input.

## Existing boundaries

Status still resolves trust and executes discovered extension code under
ADR-0222; `status --no-extensions` skips discovery. Rejecting `-e policy.py status`
does not add explicit extension loading to status. The synchronous entry point
still hardens stdio, bootstraps truststore/dotenv, and registers providers before
async routing. This decision does not claim to eliminate that bootstrap.
Offline provider/MCP access and the shared environment predicate are unchanged.

## Reference and verification

Pi main was inspected at `ea448f454af474f3e2fe9733b331e17b0662f5d6`
(2026-10-10T19:29:35Z). Its main exports raw offline before package dispatch;
its package parser still checks the first token. Its args parser already has
the `--` messages/files separator. [Pi #9183](https://github.com/earendil-works/pi/issues/9183)
reports the same leading-extension fall-through; its closure is an automated
new-contributor action, not evidence of a fix. Aelix uses Pi as a reference
under ADR-0235 and chooses explicit rejection of launch prefixes.

The regression suite covers lexical value/prompt boundaries, real awaited
dispatch before parsing, forbidden provider/harness/session/loader paths, and
isolated subprocess commands with network and optional-TUI import tripwires.
Execution results and unrun scope belong in the #289 handoff; this ADR states
the contract, not a claim that a full suite or live model has been measured.
