# RPC wire — consumer census

> Written 2026-07-29 against `main` = `da61337`. Every `path:line` below was **re-opened against
> the current tree**, not copied from an older note. Where an older note's claim turned out to be
> stale, I say so.
> **MEASURED** = I ran the command or opened the file. **INFERRED** = reasoned from what I read,
> labelled inline.

---

## 0. Headline

**Nothing has ever shipped, and the only built on-wire consumer is in this repo.**

- `git tag -l` → **0 tags**. `git ls-remote --tags origin` → **empty**. `gh release list` →
  **empty**. (MEASURED, all three.)
- `aelix-web` **does not exist**: not under `/workspaces` (only `aelix-ai` + 8 worktrees), and
  `gh repo view handochan/aelix-web` → `GraphQL: Could not resolve to a Repository`. `gh repo list
  handochan` shows 5 repos, none of them `aelix-web`. ADR-0097 itself says
  *"created at Phase 6 entry; **not yet existing**"* (`docs/decisions/0097-multi-frontend-architecture.md:100-101`).
  (MEASURED.)
- `packages/aelix-server` — the one built WS consumer — is **explicitly deleted from the publish
  set by the release workflow itself**: `.github/workflows/release.yml:82-87`
  `rm -f dist/aelix_server-*.whl dist/aelix_server-*.tar.gz`. It is also absent from the umbrella
  `aelix` package's dependencies (`pyproject.toml:29-33`). (MEASURED.)
- **No public document promises pi compatibility.** All five doc sites promise "a JSONL
  command/response protocol" and nothing more. The only `pi` mention in `README.md` is a
  **license/attribution** sentence ("Substantial portions of Aelix are a TypeScript-to-Python port
  of pi", `README.md:207-209`). (MEASURED.)
- The **only** thing that pins the wire to pi is an **internal test suite**
  (`tests/pi_parity/test_phase_4_4_strict_superset.py` and siblings) against JSON fixtures snapshotted
  from pi@734e08e. That is a self-imposed gate, editable by this repo, not a promise to any user.

**Therefore: the migration cost of a protocol change is dominated by in-repo test churn, not by any
external client.** There is no released version, no external consumer, and the one internal WS
bridge is byte-transparent (it translates nothing, so a wire change costs it literally zero lines).

---

## 1. The deliverable table

| Consumer | On the wire? | Built or planned | Breakage cost | Evidence |
|---|---|---|---|---|
| **`packages/aelix-server` (`WS /rpc`)** | **Yes — byte-transparent** | **Built**, unpublished/dormant | **~0 lines of server code.** It performs no translation: WS text → `StreamReader`, `stdout_write` bytes → WS text. A wire change flows through untouched. Cost is only in its 17 tests (5 of which assert wire fields). | `packages/aelix-server/src/aelix_server/rpc_ws.py:112-138`; `app.py:46-48`; excluded from publish at `.github/workflows/release.yml:82-87`; `packages/aelix-server/README.md:7-9` (Status block starts at :7) |
| **`aelix-web` (Phase 6 repo)** | Would be (over WS) | **Does not exist.** Not on disk, not on GitHub, no stack chosen | **Zero.** An unbuilt client is free to migrate. | `gh repo view handochan/aelix-web` → not found (MEASURED); `docs/decisions/0097-multi-frontend-architecture.md:100-101` "not yet existing"; `/workspaces` listing has no `aelix-web` |
| **`RpcClient` (in-repo client)** | Yes — parses events + responses | **Built**, but its default argv targets a **mock demo** | Moderate: `rpc_client.py` + `rpc_types.py` are the client-side decoders. But no production caller exists (see next two rows). | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:454-471` (`-m aelix`); the demo it launches: `src/aelix/__main__.py:131-147` (mock `stream_fn`) |
| **`tests/rpc/*`** | Yes (in-process + stub children) | **Built** — 190 collected tests | **Highest single bucket.** Every rpc-mode handler test asserts response envelopes / data shapes. | `python -m pytest tests/rpc -q --collect-only` → **190 tests** (MEASURED) |
| **`tests/pi_parity/*` (rpc-touching)** | Yes — pins wire **against pi fixtures** | **Built** — 155 collected across 6 files | **The real constraint.** These are the ONLY artefacts asserting pi compatibility of the wire. A protocol change requires deliberately editing or retiring them + their fixtures. | 155 tests MEASURED across `test_phase_4_3/4_4/4_9/4_11/4_12/4_13_strict_superset.py`; wire assertions at `tests/pi_parity/test_phase_4_4_strict_superset.py:52-62` (29 command names), `:246-256` (13 `RpcSessionState` fields), `:258-263` (camelCase-on-wire), `:268-272` (9 extension-UI methods); fixture `tests/pi_parity/fixtures/pi_rpc_mode_734e08e.json` |
| **`tests/server/test_server.py`** | Yes — 5 WS tests assert `type`/`command`/`id`/`success`/`data` | **Built** — 17 collected tests | Low: 5 tests, each a handful of asserts. | `python -m pytest tests/server -q --collect-only` → **17 tests** (MEASURED); wire asserts at `tests/server/test_server.py:132-136`, `:166-174`, `:244-247` |
| **The TUI** | **NO** | Built | **Zero.** The TUI drives `AgentHarness` in-process; it never spawns or speaks `--mode rpc`. Only three *comments* in `tui/` mention rpc. | `grep -rn "rpc" .../tui/` → only `shell.py:328`, `scoped_models.py:33`, `render.py:4`, all prose comments (MEASURED). Dispatch is one branch at `cli/entry.py:2157-2160` and the TUI is a different branch |
| **`aelix_agents` (subagent delegation)** | **NO — uses `--mode json`, not rpc** | Built (P2/P3) | **Zero today.** `PrintChannel` spawns `--mode json`; `resolver.py:200-206` only *emits* `["--mode","rpc"]` for the not-yet-built `oneshot=False` path. | `packages/aelix-coding-agent/src/aelix_agents/` has no rpc module (17 files listed, no `rpc_channel.py`); `agents/resolver.py:201`; only 3 rpc mentions across `aelix_agents/*.py`, all comments |
| **Extensions / `ExtensionAPI`** | **NO** | Built | **Zero.** No extension surface reads or writes the rpc wire. The nine `extension_ui_request` types are **types only** and the server drops every `extension_ui_response`. | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/` has no rpc consumer (10 files, MEASURED); `rpc/rpc_mode.py` drop confirmed in the transport recon |
| **`docs/contracts/*.schema.json`** | **NO — none of them is the RPC wire** | Built (4 schemas) | **Zero.** They cover manifest / descriptor-envelope / primitives / slot-taxonomy. **There is no JSON Schema for the RPC protocol anywhere in the repo.** | `ls docs/contracts/` (MEASURED); `docs/contracts/README.md:16-21` table; served by `packages/aelix-server/src/aelix_server/schemas.py:27-43` |
| **`python -m aelix --mode rpc` (umbrella demo)** | Yes — mock harness | Built, **and it IS in the wheel** | Low, but see §7: an older note said `src/aelix` is not packaged. That is **stale**. | `pyproject.toml:59` `packages = ["src/aelix"]`; demo at `src/aelix/__main__.py:131-147`, `:344` |
| **End users / installed versions** | — | **None exist** | **Zero.** No tag, no release, nothing published. | `git tag -l` → 0; `gh release list` → empty; `git ls-remote --tags origin` → empty (all MEASURED) |

---

## 2. `packages/aelix-server` — read fully

**Size (MEASURED):** 381 lines of Python total — `rpc_ws.py` 159, `main.py` 30, `schemas.py` 43,
`app.py` 54, `config.py` 78, `__init__.py` 17.

### 2.1 Is the wire byte-identical over WS? — **Yes, on the payload. Framing differs by design.**

MEASURED, both directions:

- **Server → client.** `run_rpc_mode` writes through exactly one sink:
  `rpc_mode.py:1879-1880` — `def _output(obj): write_sink(serialize_json_line(obj).encode("utf-8"))`.
  `serialize_json_line` is `json.dumps(value, ensure_ascii=False) + "\n"` (`rpc/_jsonl.py:19-31`).
  So each `stdout_write` call carries **exactly one complete JSONL record including its trailing
  `\n`**. `aelix-server` queues that byte string (`rpc_ws.py:106-110`) and the single drain task
  sends it as one WS text frame verbatim: `await websocket.send_text(item.decode("utf-8"))`
  (`rpc_ws.py:136`). **No re-encode, no re-frame, no field mapping.**
- **Client → server.** `rpc_ws.py:115-120` — `text = await websocket.receive_text()`,
  `line = text.rstrip("\n")`, `reader.feed_data(line.encode("utf-8") + b"\n")`. The only mutation is
  trailing-newline normalisation. The bytes then hit the same `JsonlLineReader` the stdio path uses.

**Consequence for the decision: a protocol change costs `aelix-server` ZERO source lines.** It is a
transport adapter with no schema knowledge. The `WS /rpc` handler contains no command name, no field
name, and no envelope key — grep it: the only JSON-ish string in the whole file is `"utf-8"`.

Two real (but small) divergences worth recording:

1. **Framing is per-record, not per-byte.** stdio delivers a byte stream that a consumer must split
   on `\n`; WS delivers one message per record. A client written against WS that assumes
   "one frame == one record" is relying on an emergent property of `_output`, not on a documented
   guarantee. (INFERRED that this is undocumented — I found no doc stating it.)
2. **`redirect_stdout(sys.stderr)` is process-global and applies even when `stdout_write` is
   injected** (`rpc_mode.py:1847-1849` builds the CM unconditionally; `:2096` enters it). This is
   exactly why `aelix-server` is single-flight — `rpc_ws.py:11-14` documents it, and the guard is at
   `rpc_ws.py:67-71` (`close(code=1013)` *before* `accept()`).

### 2.2 Shipped or dormant? — **Dormant, and deliberately so.**

MEASURED evidence, four independent sources:

- `packages/aelix-server/README.md:7-9` (Status block starts at :7): *"**Status:** the Web-UI daemon is deferred to a later
  release and is **not** part of the current Aelix PyPI publish set. It builds as part of the
  workspace but is not published yet."*
- `CHANGELOG.md:9-11`: *"(`aelix-server`, the Web-UI daemon, is deferred to a later release and is
  not part of this publish set.)"*
- `.github/workflows/release.yml:82-87` — a build step named *"Drop aelix-server artifacts (excluded
  from v1 publish set)"* that `rm -f`s the wheels, and `:94` notes the `SHA256SUMS` manifest lists
  only the remaining packages.
- `RELEASING.md:16` — *"`aelix-server` (the Web-UI daemon) is **excluded** from this publish set"*;
  `:84` and `:187` say to bump its version only "for workspace coherence".

It *is* importable in the dev venv (`import aelix_server` succeeds, fastapi 0.136.3 present —
MEASURED), and it has 17 passing endpoint tests, so it is live code, not a stub. But it has never
been distributed and cannot be, by its own release pipeline.

**Cost of a protocol change here: the 5 wire-asserting tests in `tests/server/test_server.py`
(lines 126-136, 142-174, 232-247) and nothing else.** All five assert only the *response envelope*
keys (`type`/`command`/`id`/`success`/`data`) — none asserts an event shape.

---

## 3. `aelix-web` — it does not exist. Plainly.

MEASURED:

- `ls /workspaces` → `aelix-ai, aelix-beta-release, aelix-bisect, aelix-docs-fix, aelix-marketplace,
  aelix-p3, aelix-phaseB, aelix-rpc, aelix-w1a`. All are `aelix-ai` worktrees or the marketplace
  repo. **No `aelix-web`.**
- `gh repo view handochan/aelix-web` → `GraphQL: Could not resolve to a Repository with the name
  'handochan/aelix-web'. (repository)`
- `gh repo list handochan --limit 50` → `agora-kb`, `aelix-ai`, `aelix-marketplace`, `opencode`
  (fork), `claude-code` (fork). **No `aelix-web`.**
- `git remote -v` on this repo → only `origin https://github.com/handochan/aelix-ai`.

Documentary status (MEASURED):

- `docs/decisions/0097-multi-frontend-architecture.md:96-104` — *"Phase 6 entry creates a new
  repository… **Repo URL**: `github.com/handochan/aelix-web` (created at Phase 6 entry; **not yet
  existing**). **Stack decisions (deferred to Phase 6 entry)**: React + Vite vs SvelteKit vs
  Next.js…"* — even the framework is undecided.
- `docs/decisions/0133-tui-packaging-no-aelix-tui-split.md:17` — *"(`aelix-tui`, `aelix-rpc`,
  `aelix-web-ui`) were **never created**."*
- `docs/decisions/0107-sprint-6h10d-snapshots-autocomplete.md:85` — *"Next: Phase 6 (Web UI,
  separate `aelix-web` repo) **or backlog**."* Phase 6 was never entered; the tree went to backlog
  (P1/P2/P3 agent work).

**This is the single cheapest fact in the whole census. The one client that would have been most
expensive to migrate has not been started.** (INFERRED conclusion, from measured absence.)

---

## 4. Public documentation promises — exact quotes

All five sites re-opened and quoted verbatim (MEASURED).

**`README.md:50-52`:**
> `- ⚙️ **Scriptable & headless.** `--print`, line-delimited `--mode json`, and a `--mode rpc``
> `  JSONL protocol make aelix embeddable in pipelines, CI, and evaluation loops — deterministic`
> `  and machine-readable.`

**`README.ko.md:52-54`:**
> `- ⚙️ **스크립트·헤드리스 구동.** `--print`, 라인 단위 `--mode json`, `--mode rpc` JSONL`
> `  프로토콜로 파이프라인·CI·평가 루프에 그대로 임베드할 수 있습니다 — 결정적이고 기계가`
> `  읽을 수 있는 출력.`

**`docs/guides/getting-started.md:31-32`** (the `tui` extra description):
> `  default interactive mode; the bare `aelix` install still supports `--print`,`
> `  `--mode json`, and `--mode rpc`.`

**`docs/guides/getting-started.md:74`** (the Modes table row):
> `| `aelix --mode rpc`    | headless    | JSONL command/response protocol on stdio |`

**`docs/guides/extension-authoring.md:175-176`:**
> `- The `/login` picker is interactive-only (a TTY). In `--print` / `--json` /`
> `  `--mode rpc` there is no wizard, so a custom login flow does not run there.`

### The decisive question: does any doc promise PI COMPATIBILITY?

**No. Not one of them.** (MEASURED — `grep -rniE "\bpi\b|parity|compatib" README.md README.ko.md
docs/guides/` returns exactly two `pi` hits and both are the attribution paragraph.)

The strongest formulation anywhere is `getting-started.md:74`: **"JSONL command/response protocol on
stdio"**. That is a *shape* promise — line-delimited JSON, commands in, responses out, over stdio.
It names no command, no field, no version, and no upstream project.

The one `pi` mention in a public doc is `README.md:207-209`:
> `Substantial portions of Aelix are a TypeScript-to-Python port of`
> `[pi](https://github.com/earendil-works/pi) (reference commit `734e08e`),`
> `Copyright © 2025 [Mario Zechner](https://github.com/badlogic), MIT licensed.`

That is a **license and provenance statement**, in a section titled *"License & attribution"*. It
tells the reader where the code came from. It does not tell any client that pi's `rpc-client.ts`
will interoperate with `aelix --mode rpc`. (INFERRED reading, but the section heading makes it
about as unambiguous as prose gets.)

**Also measured: there is no RPC protocol reference document at all.** `docs/` contains
`00-conventions.md`, `01-product-vision.md`, `02-initial-requirements.md`,
`03-architecture-principles.md`, `04-reference-projects.md`, `README.md`, `assets/`, `contracts/`,
`decisions/`, `guides/`. `docs/guides/` contains 5 files, none about rpc. **Nothing public
enumerates the 29 commands, the response envelope, or the session-state fields.** A user reading
only the shipped docs cannot write a client at all — they would have to read the source.

---

## 5. `docs/contracts/` — none of it is the RPC wire

Four schemas exist (MEASURED, `ls docs/contracts/`):

| File | Source model | Stated consumer |
|---|---|---|
| `manifest.schema.json` | `PluginManifest` | aelix-plugin.toml validators (host + IDE + marketplace) |
| `descriptor-envelope.schema.json` | `DescriptorEnvelope` | TUI/Web descriptor host renderers |
| `primitives.schema.json` | 8 primitives + `ActionDescriptor` | Tier-2 descriptor renderers, Phase 6 Web slots |
| `slot-taxonomy.schema.json` | `SLOT_MULTIPLICITY` + `SLOT_PAYLOAD_TIER` | Slot registry validators (host + Phase 6 aelix-web) |

(`docs/contracts/README.md:16-21`.)

**None of these describes the RPC protocol.** They are the *extension/descriptor* contract layer
(ADR-0094/0095/0096). Generated from Pydantic models by
`scripts/generate_contracts_schemas.py`, drift-checked in CI with `--check`
(`docs/contracts/README.md:23-47`).

**Who serves them:** `packages/aelix-server/src/aelix_server/schemas.py:27-43` —
`GET /schemas/{name}` → `docs/contracts/{name}.schema.json`, behind an
`^[A-Za-z0-9_-]+$` allowlist plus a resolve-prefix guard, 400 on illegal name, 404 on missing file.
Registered at `app.py:42-44`. Since `aelix-server` is unpublished, **this endpoint has never served
anyone.**

The tension worth flagging: `docs/decisions/0097-multi-frontend-architecture.md:118-119` declares
*"Aelix core is the single source of truth for descriptor envelope, slot taxonomy, manifest schema,
**RPC API**"* — the RPC API is named as part of the binding cross-repo contract, but **no schema for
it was ever generated**. The contract layer covers three of the four named surfaces. (MEASURED
gap.)

---

## 6. In-repo consumers, itemised

### 6.1 `tests/rpc/` — 190 tests (MEASURED)

29 files. Shape (re-verified against the current tree, consistent with the transport recon):
`test_rpc_mode_*.py` drive `run_rpc_mode` **in-process** against a fake harness and assert response
envelopes/data; `test_rpc_client_*.py` subclass `RpcClient` to replace `_build_argv` with an inline
`python -c` echo stub; `test_rpc_types.py` and `test_jsonl.py` pin serialization.

**This is the largest breakage bucket.** Most of the 190 assert something about the wire.

### 6.2 `tests/pi_parity/` — 155 tests across 6 rpc-touching files (MEASURED)

`test_phase_4_3/4_4/4_9/4_11/4_12/4_13_strict_superset.py`. **These are the only artefacts in the
entire repository that assert pi compatibility of the wire**, and they do it against JSON fixtures
snapshotted from pi@`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

The load-bearing assertions (all re-opened, MEASURED):

- `test_phase_4_4_strict_superset.py:52-62` — `set(fixture["rpc_command_types"]) == RPC_COMMAND_TYPES`.
  The docstring calls the fixture array *"the authoritative wire surface"*.
- `:64-92` — `len(RPC_COMMAND_TYPES) == 29`, `len(SUPPORTED_COMMANDS) == 29`,
  `len(DEFERRED_COMMANDS) == 0`.
- `:246-256` — `RpcSessionState.__dataclass_fields__` keys == the fixture's 13 `rpc_session_state_shape` keys.
- `:258-263` — every key of `RpcSessionState().to_json()` is camelCase (`assert "_" not in key`).
- `:268-272` and `:274-295` — the 9 `extension_ui_request` method names match the fixture.
- `:297-306` — the 3 `extension_ui_response` shapes carry the `extension_ui_response` discriminator.
- `test_phase_4_9_strict_superset.py:345-347` — `assert fixture["pi_sha"] == "734e08e…"`.
- `test_phase_4_13_strict_superset.py:235-310` — `_handle_fork` / `_handle_clone` /
  `_handle_switch_session` **wire shapes** asserted directly.

The fixture itself (`tests/pi_parity/fixtures/pi_rpc_mode_734e08e.json`) records the envelope
contract in prose:
> `"success_shape": "{id?, type: 'response', command: <command>, success: true, data?: <command-specific>}"`
> `"error_shape": "{id?, type: 'response', command: <command>, success: false, error: <string>}"`

**Honest framing of the cost:** these are *this repo's own tests*, against *this repo's own
fixtures*, enforcing a goal the repo set itself ("pi agent를 완전 동일하게" —
`docs/decisions/0103-sprint-6h9f-aelix-server.md:7-8`). They are a **policy commitment**, not a user
promise. Changing the wire means deliberately amending them + the fixtures + writing an ADR that
records the divergence. That is real work and a real philosophical decision — but it is entirely
inside the owner's control, and it breaks no one outside the repo.

### 6.3 `tests/server/` — 17 tests (MEASURED)

5 touch the wire (`:126-136`, `:142-154`, `:156-174`, `:232-247`); the other 12 are `/healthz`,
`/schemas`, and `ServerConfig`.

### 6.4 The TUI — **not on the wire**

MEASURED: `grep -rn "rpc" packages/aelix-coding-agent/src/aelix_coding_agent/tui/` returns three
hits, all **prose comments** (`shell.py:328`, `scoped_models.py:33`, `render.py:4`). No `RpcClient`,
no `run_rpc_mode`, no `--mode rpc` argv. The TUI drives `AgentHarness` in-process; `--mode rpc` is a
sibling branch in the same dispatcher (`cli/entry.py:2157-2160`), reached only when
`app_mode == "rpc"` (`entry.py:112`, `:125-126`).

**INFERRED but well-supported:** the recon note's line-19 claim that the WS wire is "identical to
the TUI stdio transport" (`rpc_ws.py:4-5` says the same) is a statement about *`--mode rpc`'s* stdio
transport, not about the interactive TUI. The interactive TUI has no transport at all.

### 6.5 Extensions — **not on the wire**

`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/` = `api.py`, `command_context.py`,
`command_dispatch.py`, `ext_ui.py`, `headless_ui.py`, `loader.py`, `subprocess_hooks.py`,
`widget_protocols.py`, `__init__.py` (MEASURED). None imports the rpc package. The nine
`extension_ui_request` types exist as **dataclasses only** and the server drops every
`extension_ui_response` (ADR-0197 §(i) blocker (b); re-confirmed by the transport recon).

### 6.6 `aelix_agents` (P2/P3 delegation) — **on `--mode json`, not rpc**

MEASURED: `packages/aelix-coding-agent/src/aelix_agents/` has 17 modules, none named for rpc, and
only 3 rpc mentions, all comments (`print_channel.py:367`, `progress.py:54`, `stream.py:30`).
`agents/resolver.py:200-206` *can* emit `["--mode","rpc"]` when `oneshot=False`, but no caller does
that today — that is precisely what this sprint proposes to build.

**This is the important asymmetry for the decision: the RPC wire's ONLY prospective in-repo
production consumer is the `RpcChannel` this sprint has not written yet.** Changing the protocol
before writing it is strictly cheaper than after. (INFERRED, but from measured absence of the
consumer.)

### 6.7 Anything reading `--mode rpc` output

Complete list of argv/dispatch sites that select rpc (MEASURED, `grep -rn '"rpc"'` over
`packages/ src/ scripts/`):

- `cli/args.py:31,44` — the `--mode` literal + validator.
- `cli/entry.py:112,119,125-126,1313-1316,2157-2160` — mode routing, the `@file` incompatibility
  guard, and the single `run_rpc_mode` dispatch.
- `agents/resolver.py:201` — `["--mode","rpc"]` for `oneshot=False` (no live caller).
- `rpc/rpc_client.py:454-471` — the client's own argv, defaulting to `-m aelix`.
- `src/aelix/__main__.py:272,344` — the umbrella demo's `--mode {interactive,rpc}` choice.
- `harness/hooks.py:976` and `harness/core.py:1183` — `source: Literal["interactive","rpc","extension"]`,
  a *provenance label* on prompts, not a transport.

**Nothing else in the tree consumes rpc output.**

---

## 7. Corrections to the older notes

Re-verified against `da61337`; these matter because the kickoff handoff quotes them.

1. **STALE — now false.** `rpc-sprint-recon-envelope.md` says *"`src/aelix` is not in the wheel's
   packages list (pyproject.toml:110)"*. **The current root `pyproject.toml:59` reads
   `packages = ["src/aelix"]`.** The `-m aelix` mock-stream demo **is** shipped in the umbrella
   wheel. (MEASURED.) This slightly *raises* the stakes on `rpc_client.py:466`: the broken default
   argv points at something users would actually have installed — if anything were ever released.
2. **CONFIRMED, still true.** `rpc_client.py:466` builds `[sys.executable, "-m", "aelix", "--mode",
   "rpc"]` (`rpc_client.py:465-466`), and `src/aelix/__main__.py:131-147` `_run_rpc()` builds an
   `AgentHarness` with `_make_mock_stream_fn()`.
3. **CONFIRMED.** `_jsonl.py:19-31` `serialize_json_line` uses `ensure_ascii=False`; the byte-parity
   impossibility versus print mode's default `ensure_ascii=True` stands.
4. **CONFIRMED.** `rpc_mode.py:1847-1849` + `:2096` — `redirect_stdout(sys.stderr)` is entered
   unconditionally, including when `stdout_write` is injected (as `aelix-server` does).
5. **Note.** `pip show aelix-server` reports `Version: 0.1.0` while
   `packages/aelix-server/pyproject.toml:7` says `0.1.0b1` — stale editable-install metadata in the
   dev venv, not a repo inconsistency. (MEASURED, cosmetic.)

---

## 8. What this means for the fork the owner must settle

Stated as cost, not as a recommendation.

- **External breakage: zero.** No tag, no release, no published wheel, no consuming repo, no
  documented protocol reference. There is no user who could have written a client. (MEASURED on
  every clause.)
- **`aelix-server` breakage: zero source lines, 5 test asserts.** It is a byte-transparent pipe. Any
  change to commands, fields, or envelopes flows through it unmodified.
- **`aelix-web` breakage: zero.** It does not exist and its framework has not been chosen.
- **The entire real cost is the ~345 in-repo tests** (190 `tests/rpc` + 155 pi-parity) **and the
  philosophical commitment those pi fixtures encode.** That commitment is self-imposed
  (`ADR-0103:7-8`), self-enforced, and revisable by ADR.
- **The prospective production consumer — `RpcChannel` — has not been written.** Every day it stays
  unwritten, the wire is cheaper to change.

**The distinction the task asked for, answered directly:** the public docs promise *"a JSONL
command/response protocol"* and nothing more. **Pi compatibility is promised only to ourselves, in
`tests/pi_parity/`.** No user-facing artefact would be violated by changing the protocol.

---

## 9. Uncertainty / what I did not verify

- I did not execute the test suites, only `--collect-only` counts. The 190/155/17 numbers are
  collected counts, not pass counts.
- I did not audit which of the 190 `tests/rpc` tests would *actually* fail under a specific protocol
  change — that depends on the change. I characterise the bucket, not the blast radius of a
  hypothetical edit.
- `gh` was authenticated as the repo owner and returned results, so the "repo does not exist" result
  is a genuine 404 rather than a permissions artefact — but I did not check for a *private* repo
  under a different account/org.
- I found no `.git` submodule, npm workspace, or vendored TS client anywhere; I checked
  `/workspaces`, `git remote -v`, and grepped `*.md/*.py/*.toml/*.json/*.yml`. A client living
  entirely outside these would not have been found.
