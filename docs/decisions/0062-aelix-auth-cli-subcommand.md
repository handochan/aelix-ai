# 0062. `aelix auth` CLI Subcommand

Status: Accepted (Sprint 6e / Phase 4.5 / W6 shipped)

## Context

Sprint 6c shipped the OAuth client framework (ADR-0051) +
AuthStorage JSON layer (ADR-0053) but had **no user-facing CLI** to
exercise them. The only way to log in to Anthropic was to write a
test that wired `OAuthLoginCallbacks` and called
`AuthStorage.set_oauth` directly. Pi exposes `aelix auth login/
logout/status/list` (Pi `cli/auth.ts` equivalent surface); Sprint 6e
ports the CLI subcommand verbatim.

The CLI also needs to **preserve** the Sprint 6d `--mode rpc` flag
(ADR-0058) so existing harness integrations don't break. The
top-level invocation surface is:

```
aelix                  # interactive demo (Sprint 1)
aelix --mode rpc       # RPC mode (Sprint 6d)
aelix auth login    <provider>   # NEW — Sprint 6e
aelix auth logout   <provider>   # NEW — Sprint 6e
aelix auth status   [provider]   # NEW — Sprint 6e
aelix auth list                  # NEW — Sprint 6e
```

## Decision

Aelix `src/aelix/__main__.py` gains an `auth` subparser layered
**alongside** the existing `--mode` flag. `argparse.subparsers(required=False)`
means the bare `aelix` and `aelix --mode rpc` paths continue to
dispatch as before.

### Subcommand surface (Pi parity)

| Subcommand | Behavior | Exit codes |
|---|---|---|
| `aelix auth login <provider>` | Resolve provider from registry → run `OAuthProvider.login(callbacks)` → persist via `AuthStorage.set_oauth` | 0 success / 1 RuntimeError (with stderr diagnostic) |
| `aelix auth logout <provider>` | `AuthStorage.remove(provider)` | 0 success |
| `aelix auth status [provider]` | `AuthStorage.get_auth_status(provider)` for one (or all known providers) | 0 success / 2 unknown provider |
| `aelix auth list` | Stored provider ids (Pi `list()`) | 0 success |

### CLI callbacks (stdin/stdout wiring)

```python
def _cli_callbacks() -> OAuthLoginCallbacks:
    def on_auth(info):
        print(f"\nOpen this URL in your browser:\n  {info.url}", flush=True)
        if info.instructions:
            print(f"\n{info.instructions}", flush=True)

    def on_prompt(prompt):
        placeholder = f" [{prompt.placeholder}]" if prompt.placeholder else ""
        return input(f"\n{prompt.message}{placeholder}: ")

    def on_progress(msg):
        print(f"... {msg}", flush=True)

    return OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt,
                                on_progress=on_progress)
```

`on_manual_code_input` is intentionally **not wired** in Sprint 6e —
the CLI relies entirely on the local callback server. Sprint 6f's
TUI will wire it (carry-forward P-161).

### Sprint 6e W6 must-fix integration

| Finding | Subject | Resolution |
|---|---|---|
| **P-152** | `aelix auth status totally-fictional-provider` silently reported `not configured` | Validate against `get_oauth_providers() ∪ storage.list()` → exit 2 with `Unknown provider: <id>` stderr diagnostic |
| **n1** | `aelix auth login bogus` dumped Python traceback (RuntimeError leaked) | Try/except RuntimeError → stderr diagnostic + exit 1 |
| **P-157** | `_maybe_await` was duplicated across Anthropic / Copilot / Codex | Extracted to `_helpers.py::maybe_await` (single owner) |

### `known = registry ∪ stored` partition (P-152)

```python
known = {p.id for p in get_oauth_providers()} | set(storage.list())
if provider_id not in known:
    print(f"Unknown provider: {provider_id}", file=sys.stderr, flush=True)
    return 2
```

This is **Pi parity** — Pi validates against the registry AND the
stored set, so a user with an `api_key` entry for a custom provider
id (e.g., `my-internal-llm`) can still query `aelix auth status
my-internal-llm` without registering an OAuth provider.

### Back-compat: top-level paths preserved

- `aelix` (no subcommand) → interactive demo (Sprint 1 echo bot).
- `aelix --mode rpc` → RPC mode (Sprint 6d JSONL dispatcher).
- `parse_known_args` swallows extra positional args so the
  RpcClient subprocess forwarding doesn't break.

### Test harness — subprocess-based

`tests/cli/test_auth_subcommand.py` uses `subprocess.run([sys.executable,
"-m", "aelix", "auth", …])` with `AELIX_AUTH_PATH=<tmp>` so the
developer's real `~/.config/aelix/agent/auth.json` is never touched.
Coverage:

- `auth list` empty + populated.
- `auth status` no-provider lists all 3 OAuth providers.
- `auth status <unknown>` exits 2 (P-152).
- `auth status <stored-non-oauth>` is valid (Pi `registry ∪ stored`).
- `auth status <known-unconfigured>` reports `not configured`.
- `auth logout` removes the entry, leaving others intact.
- `auth login bogus` exits 1 (n1 RuntimeError handling).
- Back-compat: no-subcommand runs interactive demo.

## Consequences

- Users have a first-class entry point for Anthropic / Copilot /
  Codex OAuth — no test-only `set_oauth` calls required.
- The exit-code partition (0 / 1 / 2) follows POSIX convention and
  is amenable to shell scripting (`if aelix auth status anthropic;
  then …`).
- The `--mode rpc` flag preservation means existing harness
  integrations (Sprint 6d RpcClient consumers) keep working without
  modification.
- The `parse_known_args` choice trades strict validation for
  forward-compat: future flags forwarded by RpcClient won't break
  the auth path.

### Deferred to Sprint 6f

- **`--api-key <provider>:<key>` CLI flag** — surfaces
  `AuthStorage.set_runtime_api_key` and `set_fallback_resolver` to
  the command line (ADR-0061 carry-forward).
- **TUI `on_manual_code_input` wiring** (P-161) — the Sprint 6f TUI
  will support pasting the redirect URL or bare code without going
  through the local callback server.
- **Interactive `aelix auth login` provider picker** — Pi has an
  inline select prompt when no provider id is given. Sprint 6e
  requires the explicit id; the picker lands when the TUI does.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6e — CLI
  surface complete).
- ADR-0051 — OAuth client framework (the registry the CLI consults).
- ADR-0053 — AuthStorage + secrets (the storage layer the CLI
  routes through).
- ADR-0058 — Phase 4.4 closure (the `--mode rpc` flag this ADR
  preserves).
- ADR-0061 — AuthStorage layered cascade (the cascade `status`/
  `list` consume).
- ADR-0063 — Phase 4.5 strict superset closure.

## Phase

Sprint 6e / Phase 4.5 (shipped — 4-subcommand surface live; TUI
manual-input + provider picker deferred to Sprint 6f).
