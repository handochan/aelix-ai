# 0264. The model catalog refreshes from aelix's own published copy, as pi's does from pi.dev, without transport

Status: Accepted (2026-10-20, approved by the owner as a whole); rewritten 2026-10-15 on the
owner's decision (§2), revised after its reviews (r5-r11) and on the owner's decisions of
2026-10-16, 2026-10-17 and 2026-10-19 (§7). §8 is what the implementation owes. Amended 2026-10-21
(§10: #172's B4, what the parser and the publisher measured against the shipped catalog) and
2026-10-26 (§10: #507, a shipped row's `off` from the evidence a new row's map comes from; #397,
the levels under a hidden `off` a model refuses) and 2026-10-27 (§10: #172's fold-back, run once,
and what its review had to correct by hand; #434's client as built: the store, the source, the
triggers, and what its live checks changed; #609's opt-in manual mode) and 2026-10-28 (§10:
#172's catalog repository, its first publication and §8's last checks, as measured) and 2026-10-30
(§10: #617, the owner's floor policy: a thinking level an older client lacks raises no floor;
#616, the publishing pipeline's gaps: a run queued behind a rollback stops on its hold, a failed
fetch says why, the release check lists the fields it keeps, and the publish job has tests).
Date: 2026-10-14; rewritten 2026-10-15; decisions recorded 2026-10-17 and 2026-10-19; accepted
2026-10-20
Issue: #172 (the publishing pipeline), #434 (Phase 1-2: the store, the refresh contract, the client
overlay, the triggers), #450 (OpenRouter's key-scoped narrowing), #507 (a shipped row's `off`, §10),
#397 (the floor under it, §10), #617 (the index floor and a level an older client lacks, §10),
#616 (the publishing pipeline's gaps, §10)
Relates: ADR-0065 and ADR-0140 (the registry and `models.json`), ADR-0189 and ADR-0192 (Ed25519
sidecars; the marketplace's progressive signature rule), ADR-0203 (what a cwd `.env` may carry),
ADR-0232 (transport is inherited, never invented; the generator and its ladder), ADR-0235 (pi is a
reference, not a parity target), ADR-0250 (routing; guards 1 and 2), ADR-0251 §11
(`OPENROUTER_BASE_URL`), ADR-0252 (project settings follow project trust), ADR-0262 (registrations
own routes; the registration generation), ADR-0263 (pi #10729).
Pi: `3daa20280` for §3.1 and §4; §7 cites pi `main` of 2026-10-16, `99fe148`, as `@99fe148`, and
`8efd314` (2026-10-17) moves no line cited there; `d2e7d8b7` (2026-10-19) moves some in
`generate-models.ts` and `packages/ai/src/models.ts` and maps MiniMax's direct rows (§6.2). Of the
pi files cited here (`r8/out/pi_ranges.txt`), up to `b45e0eb` (pi `main` on 2026-10-15)
`generate-models.ts` changes (OpenCode's base path `/zen` to `/inference`, Xiaomi's off/high map and
`supportsReasoningEffort: false` in `6374c2ba`, GPT-6's `supportsMidConvoEffort` in `60ac3bcc`:
transport through pi's catalog, §6.1; GPT-6.1 Sol's Codex window grows), and so do
`model-resolver.ts` (`d7f7e0d`, pi #10913) and `main.ts` (one line after `:477`); `b45e0eb..99fe148`
changes `generate-models.ts` (kimi-coding, §3.1; `dc939913`, off for Claude Haiku 5.5 and Opus 5),
`interactive-mode.ts` (`a4f67eb6`, no cited behaviour) and the publishing files (§3.1).
Aelix: read and measured at `e24ea2e8` (r4), `aa207239` (r5, r6), `bc83e0bc` (r7-r9) and `c02517ca`
(r10, r11; origin/main on 2026-10-19 and 2026-10-20), on which this is rebased; it moves cited
`entry.py` and `shell.py` lines, updated here (`r10/out/moved_lines.txt`). Evidence:
`.omc/probes/434-adr/` (round 1) and its `r2/` to `r11/`, which list their commands in
`commands.txt`. `r7/` holds models.dev and OpenRouter as fetched on 2026-10-17, the Q2 derivation
probe and the Q4 consequence probe; `r8/`, the rollback-reach and wire probes and pi's commit
ranges; `r9/`, the 34 rows' wire and pi's resume; `r10/`, the fallback's candidates (§4.4); `r11/`,
the resolution's callers and `/agents use`'s drop (§4.4). The pi research behind §7 is
`.omc/research/pi-catalog-2026-10-16/`. MEASURED: a command ran and its output is under the evidence
path. READ: source read at the SHAs above, not run. INFERRED: concluded from reading. Times are KST
(UTC+9); a cron expression is UTC by GitHub's rule, and HTTP header values are as served.

## 1. Problem

The catalog is static. `models_generated.json` holds 1,529 rows over 35 providers, and
`ModelRegistry.refresh()` (`model_registry.py:845-854`) only re-runs `_load_models()`. A model a
provider starts serving needs an aelix release or a hand-written `models.json` row; ADR-0232
measured the owner carrying 83 extra OpenRouter rows that way. MEASURED against OpenRouter's public
list on 2026-10-14 (round 1): 87 of the 356 static OpenRouter ids are no longer listed, 127
tool-capable listed ids are missing, and of the 269 shared ids the context window differs on 47, the
output cap on 99, the input price on 76 and the output price on 71.

Schedule (milestone events): #434 (Phase 1-2) joined beta.3 on 2026-10-11 and #450 (the first
source) on 2026-10-14. #172 (the refresh pipeline) joined on 2026-10-15, with the owner's decision.
This ADR is B2's to approve, and §4.8 re-cuts the batch plan's B4 and B5 into B4 to B6.

## 2. The owner's decisions, and why the earlier design was replaced

On 2026-10-15 (`.omc/specs/batch-beta3-b2-2026-10-14.md`, its section "2026-10-15 오너 결정과 좁은 마무리
라운드"), the owner decided four things, and #172 joined beta.3 with them:

- ADR-0264 is rewritten in pi's structure.
- The catalog is one aelix hosts itself, published by #172's pipeline.
- Remote rows never carry transport.
- OpenRouter narrows by key.

It left four questions open. On 2026-10-16, after a fact-checked report on how pi does each, the
owner settled Q2 (thinking maps for new reasoning ids) and Q3 (whether a key only a project `.env`
supplies narrows); on 2026-10-17, Q4 (whether a publication may move a typed string), Q1 (where the
catalog is published) and, after r7's review, the rollback's reach and the narrowing's details; on
2026-10-19, after r8's review, when a client checks and how a resumed session falls back. §7 records
each choice, the pi fact it rests on and the consequences accepted. Everything else below is this
ADR's proposal: the schedule, the format, the ordering rule, the hold, the bounds and the triggers.

The two parts that differ from pi, as this ADR applies them:

1. **The source is aelix's own catalog, not pi.dev.** The corrected catalog ADR-0232 grows is what
   gets published: `scripts/refresh_catalog.py`, additive, with transport inherited by its ladder
   and the hand corrections pinned by `tests/test_catalog_corrections_are_pinned.py`.
2. **A published row never carries transport.** `api`, `baseUrl`, `headers` and `compat` always come
   from the shipped catalog. A new id inherits them on the client by ADR-0232's ladder, and an id
   that cannot inherit them is dropped. So a wrong or compromised published file can never send a
   key where the shipped catalog does not already send it.

## 3. Evidence

### 3.1 Pi (READ at `3daa20280`, MEASURED where marked)

The runtime source, in `packages/coding-agent/src/core/`:

- `withRemoteCatalog` (`remote-catalog-provider.ts:104-185`) wraps every built-in provider except
  radius and OpenRouter (`model-runtime.ts:228-240`).
  - It fetches `https://pi.dev/api/models/providers/<id>?types=…` (`:14`, `:75-95`), 4 s per attempt
    (`:15`), and parses whole rows, transport included (`:38-51`).
  - `Last-Modified` is the catalog's stamp and `ETag` its validator (`:53-64`). The overlay applies
    only when that stamp is newer than the shipped data's (`isNewer`, `:66-73`).
  - The cache phase publishes the stored overlay before any network (`:119-130`).
  - The network phase is skipped within 4 hours of `checkedAt` unless forced (`:16`, `:131-142`),
    and revalidates with `If-None-Match` only when a stored body backs it (`:144-147`).
  - 304 moves only `checkedAt` (`:149-155`). 404 and 501 persist `lastModified: 0` with no `update`
    (`:156-166`): the overlay restored at start stays for the rest of the process, and the next
    start applies none.
  - Any other failure persists `checkedAt` and throws (`:167-172`). Success persists, then publishes
    (`:173-182`).
  - The same id replaces and new ids append (`mergeModels`, `:32-36`), with no origin mark on the
    row (`:47-50`).
  - The shipped stamp is `generatedAt` in the generated data's manifest
    (`packages/ai/src/providers/all.ts:99-102`), written and checked with per-file sha256 hashes by
    `packages/ai/scripts/model-data.ts:10-15`, `:131-142` and `:232-259`.
- `withPersonalizedCatalog` (`:187-240`) wraps OpenRouter.
  - Each network refresh merges a newer pi.dev catalog into the built-ins (`:222-229`) and passes
    the result through `personalizeOpenRouterCatalog`
    (`packages/ai/src/providers/openrouter.ts:24-60`).
  - That function sends `GET {baseUrl}/models/user` with the key and a 15 s timeout of its own
    (`:14`, `:30-33`). 401, 403 and 404 return the catalog unchanged (`:34`). Chat rows the list
    lacks are dropped, and listed ones take `contextWindow` and `maxTokens` with `||`, and `cost`
    when `pricing` is present (`:46-59`).
  - The personalized list **replaces the provider's models**: `getAllModels` and `getModels` return
    it whenever it is non-empty (`remote-catalog-provider.ts:203`, `:207`). So a narrowed-away id is
    gone from pi's provider and cannot be resolved.
  - The list is persisted per provider and replaces the catalog offline too, with no interval and no
    validator. An empty list falls back to the built-ins (`:187-194`, `:232-237`).
- The store: `ModelsStoreEntry` is `models`, `lastModified`, `checkedAt`, `etag`
  (`packages/ai/src/models-store.ts:3-15`), keyed by provider id (`:21-26`). `FileModelsStore` is
  `<agent dir>/models-store.json` (`model-runtime.ts:222-226`), locked through `proper-lockfile`
  (`models-store.ts:47-60`, `:126-146`; `auth-storage.ts:130`), whose lock is a
  `models-store.json.lock` directory made with `mkdir`, a sidecar (4.1.2 `lib/lockfile.js:11-12`,
  `:28-29`, `r4/fetch/`). Its `write` lets a lock or file error propagate
  (`models-store.ts:126-135`).
- The contract (`packages/ai/src/models.ts`): `refreshModels(context)` restores `context.stored`,
  fetches, keeps the previous list on failure and publishes through `context.publish` (`:171-193`).
  `RefreshModelsContext` carries the credential, `stored`, `publish`, `allowNetwork`, `force`, an
  always-present `signal` and `baseUrl` (`:80-101`). A run supersedes the provider's previous one,
  and `publishProviderModels` refuses once it is superseded or aborted, before persisting and again
  before updating (`:491-536`). It awaits the store write before `update()` (`:520-527`), so a write
  that throws publishes nothing in memory. A started OAuth refresh survives cancellation
  (`:630-631`). `fed6009cc` (2026-08-04) made cancellation the caller's and removed the runtime's
  own 15 s timeout.
- Triggers (§4.6 cites each): interactive and RPC start-up in the background with 15 s; the `/model`
  selector, which renders the cache, refreshes behind it, re-renders and aborts on close
  (`components/model-selector.ts:153-158`, `:184-222`, `:224-229`); the `/scoped-models` selector,
  which runs the same refresh (`interactive-mode.ts:3212-3214`, `:5396`, `:5471`@99fe148); `/model
  <term>` with no cached match; `/login`; `pi update --models`. `--list-models` reads the cache, and
  any `PI_OFFLINE` turns the network off (`model-runtime.ts:247`). Concurrent interactive refreshes
  share one run; each caller keeps its own cancellation, and the last to leave aborts it
  (`modes/interactive/model-catalog-refresh.ts:13-51`).
- Copilot's policy enablement at login reads the shipped constant, not the overlay
  (`Object.hasOwn(GITHUB_COPILOT_MODELS, …)`,
  `packages/ai/src/auth/oauth/github-copilot.ts:124-131`).

The publishing side (`.github/workflows/publish-model-catalog.yml`,
`scripts/publish-model-catalog.mjs`, `scripts/model-catalog-protocol.ts`):

- When it publishes: a run started by CI succeeding on `main` (`.yml:3-8`, `:40`) uploads whenever
  its publish job runs Monday to Friday, 10:00-15:00 Vienna time. Only scheduled runs (an hourly
  cron, `:17-21`) are held to 10:17, 12:17 and 14:17, and a manual run uploads when asked
  (`:110-135`).
- How it is split: the workflow's permissions are `contents: read` (`:35-36`). The `generate` job
  checks out with `persist-credentials: false` and generates with `generate-models.ts --strict`
  (`:45-49`, `:60-64`). The upload job checks out the same way, runs in a protected environment
  holding the R2 keys, and has its own concurrency group, `publish-model-catalog-r2` with
  `cancel-in-progress: true` (`:74-94`).
- Revisions: each is `sha256-<hex>` of `models.all.json` alone (`.mjs:159-161`), with `publishedAt`
  and `sourceCommit` in the index entry outside the hashed bytes (`:238-246`). They are uploaded
  immutable before the `no-store` index (`:30-31`, `:303-321`), nothing is uploaded when the index
  already names the revision (`:295-301`), and a bundle with fewer than 500 chat models, a missing
  required provider or a malformed shard is refused (`:32-33`, `:85-157`).
- The index lists catalogs by `minimumPiVersion`, bumped "only when generated metadata requires
  behavior unavailable in older pi clients" (`.mjs:27-28`). A client gets the newest catalog whose
  minimum does not exceed its version (`model-catalog-protocol.ts:127-158`).
- After `3daa20280`, transport through the catalog broke released pi (`r8/out/pi_ranges.txt`):
  `b06c1f2c` moved kimi-coding to `openai-completions`, and clients from 0.80.7 took its base URL
  with Anthropic Messages and got 404s (revert `6a1f799b`). `a3f265b1` re-landed it behind a 1.2.1
  minimum, and `4954d760` added `check:model-catalog-compat`, which compiles (type-checks) the latest
  release's pi-ai source against the new catalog first; no pi process runs.
- Nothing is signed: integrity is TLS and the content address.
- MEASURED (`r4/fetch/pidev-*`, 2026-10-15 01:44, no key): pi.dev's OpenRouter shard answered 200,
  265,429 bytes, 476 rows, with `etag`, `last-modified` and `cache-control: public, max-age=60,
  s-maxage=60`, and answered 304 to `If-None-Match`. Every row carries `api` and `baseUrl`, and 390
  carry `compat`: the transport this design keeps out of published rows.

### 3.2 Aelix (READ at `e24ea2e8`, `aa207239`, `bc83e0bc` and `c02517ca`, MEASURED where marked)

- Built-ins come from `aelix_ai.models`, a module-level map built from `models_generated.json` at
  import (`aelix_ai/models.py:27-36`; `get_model`, `get_providers`, `get_models` at `:58-73`).
  - The registry composes them through `load_built_in_models` (`models_json.py:562`). `models.json`
    custom rows win (`merge_custom_models`, `:438`). Then come registrations, OAuth `modify_models`
    and the owner filter (`model_registry.py:1118-1283`).
  - Readers outside the registry read the same map: the sibling backfill, guard 2's OpenRouter
    namespaces (`cli/runtime_bootstrap.py:894-928`, `:951-969`), and session statistics' fallback
    price.
  - So does Copilot's policy enablement, which POSTs `{"state": "enabled"}` to
    `{base}/models/{model_id}/policy` for every catalog id, the id unquoted in the path
    (`oauth/github_copilot.py:565`, `:634-636`).
- Every `_load_models()` advances the registration generation (`model_registry.py:1143`). An auth
  snapshot's validator refuses when the generation moved, when the model's request identity
  (provider, id, api, base URL, headers) changed, or when ownership changed (`:379-391`). MEASURED
  (`r4/out/proto_r4_routing.txt`, eight runs): a request authenticated before a `_load_models()` is
  refused ("Provider registration changed after request authentication; select the model again").
  With the generation kept, the same row re-read after a hosted publication validates.
- `get_all`, `get_available` and `find` read `_served_models` (`:263-318`).
  - ADR-0250's resolver builds its universe from them (`_route_universe`,
    `runtime_bootstrap.py:1583-1612`; `_find_in`, `:1634-1645`; `resolve_route`, `:1876`;
    `restore_session_route`, `:2222`). A raw id the named provider lacks goes to the first keyed
    provider that lists it, in catalog order (`:2168-2174`).
  - `/model <arg>` matches `get_available()` and backfills from `get_all()`
    (`core/model_argument.py:280`, `:331`, `:342`), and refuses an id several providers serve
    (`:409-413`).
  - A delegated child receives the parent's own model as `--model <id> --provider <p>`
    (`agents/resolver.py:170-192`). A profile's model string passes as typed and is resolved in the
    child (`_pin_route`, `:195-291`).
- The shipped catalog's shape, MEASURED (`r4/out/catalog_shape.txt`, `r4/out/id_rules.txt`,
  `r5/out/catalog_bounds.txt`):
  - Each provider's rows share one base-URL host, except `amazon-bedrock`'s two AWS regions.
    `azure-openai-responses`' base URL is empty and filled from configuration, and `google-vertex`'s
    is templated by location. 24 of 35 providers have exactly one transport. So a transport
    inherited from a provider's own shipped rows reaches only hosts that provider's rows already
    use.
  - Ids are ASCII, at most 50 characters, with `. - : / @ ~` as the only non-alphanumerics, and none
    has an empty, `.` or `..` path segment. No two ids of a provider differ only in case.
  - No row has a `contextWindow` below 4,095 or a `maxTokens` below 4,000. No row carries `family`
    or `releaseDate`.
  - 1,157 rows reason, and 949 of them carry no `thinkingLevelMap`, which offers levels off to high
    and nothing above (`get_supported_thinking_levels`, `aelix_ai/models.py:108-138`). For such a
    row the OpenAI-family adapters (`openai-completions`, `openai-responses`,
    `openai-codex-responses`) send the level's own name as the wire value
    (`providers/openai_completions.py:636-637`, `openai_responses.py:250-251`). Anthropic's and
    Google's map it to their own values (`_anthropic_transforms.py:517-536`,
    `_google_shared.py:570-610`).
- The catalog's history, MEASURED (`r5/out/lineage_probe.txt`): `models_generated.json` has had 11
  versions on `main`'s first-parent history since 2026-05-20, all with distinct content. Releases
  bump the version and commit on the default branch (`RELEASING.md:88-97`, `:150-151`).
- The generator, READ and MEASURED (`scripts/refresh_catalog.py`; `r4/out/new_ids_thinking.txt`):
  - It is additive, because `merge` cannot write a key that exists (`:469`).
  - A new row takes facts from models.dev and transport from the ladder (`resolve_transport`,
    `:284-339`). `build_row` writes no `thinkingLevelMap` (`:367-430`), and nothing in the file
    reads models.dev's `reasoning_options`.
  - The ladder reads the new id's models.dev `family` (`:311`), and the `family` (`:243`, `:250`,
    `:315`) and release date (`release_date`, else `last_updated`; `_released`, `:206-212`, read at
    `:279`) of the shipped rows it compares, and nothing else of models.dev
    (`r6/out/ladder_inputs.txt`). Rung 3 settles over the family-less rows of every namespace
    (`:333`). It runs over the committed catalog only (`plan`, `:432-466`).
  - Its two runs: `92eb50a4` added 422 rows, 362 of them reasoning, none with a map. `c8dc944e`
    added 102, 99 of them reasoning, and the 9 with a map had it copied by hand from a predecessor
    and pinned (its commit message).
  - The script says "Nothing here runs in CI" (`:32`).
- ADR-0232's ladder run on the client, MEASURED offline (`r4/out/ladder_replay.txt`): given each
  run's new rows without transport, `family` or dates, a client shipping the earlier catalog
  inherits the generator's wire for all 102 rows of `c8dc944e` and all 420 of `92eb50a4`'s 422 it
  answers (2 refused). The full transport matches on 99 and 377; the rest differ only in `compat` or
  `headers`, a lower bound, not ADR-0232's leave-one-out residual.
- A transport-free revision of the committed catalog, MEASURED (`r4/out/bundle_probe.txt`,
  `r5/out/lineage_probe.txt`): every row key is a fact or transport; applied back over the shipped
  catalog, the revision reproduces all 1,529 rows; in r5's format (§4.2) it is 324,681 bytes, 27,740
  gzipped.
- The store discipline to reuse: `AuthStorage` writes `<path>.tmp` at `0o600`, fsyncs,
  `os.replace`s, keeps the directory `0o700` (`oauth/auth_storage.py:237-297`), and `flock`s the
  data file itself (POSIX). MEASURED (`r4/out/flock_replace_probe_rerun.txt`,
  `r4/out/sidecar_lock_probe_rerun.txt`): once the holder replaces the data file, a second process
  takes the "exclusive" lock while the first still holds it; a never-replaced sidecar keeps it out.
- Offline is `util/offline.py:55-65` (`--offline`, `AELIX_OFFLINE`, `PI_OFFLINE`), exported before
  any verb (`cli/entry.py:2446`). A cwd `.env` reaches either name only through the hatch, and only
  towards offline. The hatch's floor (`runtime_bootstrap.py:336-383`): a repository may redirect,
  never execute, never choose the global store and never widen the gate.
- A key that only a cwd `.env` supplies, MEASURED (`r5/out/envprobe_print.txt`,
  `r5/out/envprobe_tui.txt`, a fake key and a dead proxy): a launch picks no model with it (`aelix
  -p` prints "No model selected."), and the key leaves the process once OpenRouter is named or
  picked. So guard 1 keeps a `.env` key out of route choice but not out of use, as
  `README.md:361-372` warns; those lines and `.env.example:121-122` tell users to put the key there.
  Guard 1 knows which names came only from a `.env` (`_dotenv_only_names`,
  `runtime_bootstrap.py:1559`).
- The hook bus: event names are a closed `Literal` (`aelix_agent_core/harness/hooks.py:157-201`,
  `HOOK_RESULT_TYPES` `:1306`), pinned to pi's 36 by
  `tests/pi_parity/test_hook_event_name_literal_pi_parity.py:60-64`. Coding-agent emits on the
  harness's bus without `core.py` (`cli/repl.py:329-335`).
- Delegation depth is `AELIX_SUBAGENT_DEPTH` (`subagent_contract.py:56`). The `/model` picker takes
  an injected `select` (`tui/model_picker.py:108-116`), which `shell.py` wires to the TUI's select
  widget (`tui/context.py`). `/scoped-models` is `tui/scoped_models.py`, opened from
  `tui/shell.py:1530`.
- Root commands are `extension`, `docs` and `status` (`cli/args.py:109-110`). Any other first
  positional is a prompt (`:189-192`), `update` included.
- CI and versions: `ci.yml` runs on a push to any branch and on pull requests, and cancels a
  superseded run of the same ref (`.github/workflows/ci.yml:16-19`, `:26-28`). MEASURED
  (`r5/fetch/ci_query.txt`, `r5/fetch/any_workflow_query.txt`, 2026-10-15 02:48): `GET
  /repos/handochan/aelix-ai/actions/workflows/ci.yml/runs?branch=main&event=push&status=success`
  lists CI runs only, the newest being `e24ea2e8`; the repository-wide
  `/actions/runs?branch=main&status=success` interleaves dependency-graph runs (`event=dynamic`).
  Every package reports `0.1.0b2` (`pyproject.toml`; MEASURED through `importlib.metadata`).
- Pages:
  - aelix-ai's own site deploys `site/` only on pushes to `main` that touch `site/**` or
    `pages.yml`, and by hand (`.github/workflows/pages.yml:19-25`). Each deploy replaces the whole
    site. A release's `site/latest-version.json` is one such push (`RELEASING.md:107-110`).
  - The marketplace repository deploys its whole root on every push to `main`, with no paths filter,
    and by hand (`handochan/aelix-marketplace` `pages.yml:17-21` at `95b3ee91`,
    `r6/fetch/marketplace-pages.yml`).
  - The marketplace catalog's signature rule is progressive: unsigned is admitted over TLS while
    `FIRST_PARTY_KEYS` is empty, and a present but invalid signature refuses
    (`cli/extension_catalog.py:122-133`, `cli/extension_signing.py:101-111`, ADR-0192).

### 3.3 OpenRouter (MEASURED in rounds 1-2 and r7, unauthenticated)

- `GET /api/v1/models`, at 10:42 and again at 11:51 on 2026-10-14:
  - 200, about 774 KB, 462 models, `links.next` null, `total_count` 462, no `ETag` and no
    `Last-Modified`. `json.loads` of the body takes about 3.4 ms.
  - The fields #450 reads are `context_length`, `top_provider.context_length`,
    `top_provider.max_completion_tokens` (missing or 0 in 8), and `pricing`.
  - `pricing` has `prompt`, `completion`, `input_cache_read` and `input_cache_write` as $/token
    strings. 7 are negative, all of them routers. 81 rows carry `overrides`, and 77 of those are
    prompt-length tiers that aelix's `ModelCost` cannot hold.
  - On 2026-10-17 (`r7/out/q2_derive_probe.txt`): 468 models, 339 taking `reasoning`, 203 of them
    with `reasoning.supported_efforts`, the list §7 Q2 reads.
- `GET /api/v1/models/user` without a key: 401, `cache-control: private, no-store`. Its docs: the
  list is filtered by the key's provider preferences, privacy settings and guardrails; `offset` and
  `limit` page it (default 500, at most 1000), and "when both offset and limit are omitted, the full
  list is returned"; `links` and `total_count` are required; errors are 401, 403 ("Only management
  keys can perform this operation", a text the public `/models` page shares), 404 and 500. **No
  authenticated response was measured.**

## 4. Decision

### 4.1 The shape

```
 published (Pages)                    shipped (wheel)              agent dir
 v1/index.json ───┐                   models_generated.json        models-store.json (+ .lock)
 v1/revisions/    │ its lineage holds  (its digest, computed)       ├─ aelix-hosted: revisions
   sha256-<hex> ──┤ the shipped digest?      │                      └─ openrouter: narrowed list
                  ▼                          ▼
        facts only ──► effective built-in catalog (aelix_ai.models) ◄── transport: shipped only
                                │
                                ▼ composition: models.json, custom rows, registrations, OAuth
                          route rows ──► every lookup (ADR-0250), published rows as shipped ones
                                │
                                ▼ OpenRouter key-scoped narrowing (served list only)
                          served list ──► picker, --list-models, cycling, RPC model lists
```

- The shipped catalog stays the baseline and the offline fallback.
- A published revision of aelix's own catalog overlays every built-in provider when its lineage
  holds this build's catalog digest, that is, when it was built from a `main` commit whose history
  contains this build's catalog (§4.2). This is pi's `withRemoteCatalog` with a different ordering
  rule, and one revision instead of one request per provider.
- A known id takes the revision's facts. A new id routes as it would if a release shipped it, as
  pi's overlay rows do (§4.4, §7 Q4).
- OpenRouter's key-scoped list narrows what is offered for OpenRouter and sets its limits and
  prices, computed as pi's `personalizeOpenRouterCatalog` computes them. pi replaces the provider's
  model list with the result. aelix applies it to the served list only, so it never decides where a
  typed string goes (§4.4, §4.5).
- Nothing published or fetched supplies transport.

### 4.2 The publishing pipeline (#172)

**Where.** A new public repository, `handochan/aelix-catalog`, with Pages built by GitHub Actions.
aelix compiles in `https://handochan.github.io/aelix-catalog/v1/index.json` (§7 Q1).

- The owner creates the repository and turns Pages on once, as for the marketplace.
- The workflow lives there, holds no stored secret and checks the public aelix-ai out read-only.
  Only its publish job's `GITHUB_TOKEN` can write, and only to the catalog repository; aelix-ai's
  own workflows gain no permission.

**When.** `cron: "17 1,3,5 * * 1-5"`, which is weekdays at 10:17, 12:17 and 14:17 KST: pi's three
scheduled slots, in the owner's zone, so a bad revision meets a person. It also runs by hand
(`workflow_dispatch`). pi also publishes after any green `main` CI inside its window (§3.1). Here a
`main` commit waits for the next slot: aelix-ai's CI could start the catalog workflow only by a
dispatch (`repository_dispatch` or `workflow_dispatch` through the REST API) with a token stored for
the catalog repository (INFERRED from GitHub's documentation, `r6/fetch/gh-docs-events.md`).

- The workflow has `concurrency: {group: aelix-catalog, cancel-in-progress: false, queue: max}` and
  every job has `timeout-minutes: 30`, so runs never interleave index commits or deploys.
- From GitHub's documentation (`r6/fetch/gh-docs-concurrency.md`; not measured): by default a newer
  pending run cancels the pending one, so a slot could replace a rollback waiting behind a long run
  and publish from `main`. `queue: max` keeps up to 100 pending runs, in an order GitHub does not
  guarantee; either order ends in the rollback's state (a slot after it meets the hold, one before
  it is overridden). With `queue: max`, a rollback and a publish pending at once were both kept
  (measured on 2026-10-31 with two, §10 2026-10-30, #616).
- A rollback or withdrawal counts as done only when its run succeeded and its summary shows the new
  index and the hold. A cancelled one is dispatched again.

**Which aelix-ai commit.** The `head_sha` of `GET
/repos/handochan/aelix-ai/actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=1`.

- This is pi's "after CI succeeds on `main`" without a cross-repository trigger, so a red `main` is
  never published.
- Naming the workflow file, the event and the branch matters: a repository-wide query also returns
  dependency-graph runs (§3.2).
- `ci.yml` cancels a superseded run of the same ref, so a commit whose run a later push cancelled
  has no green run of its own. It is published through the later commit's run, which tested a tree
  that contains it.
- The job checks out that sha with full history and checks that it is an ancestor of `origin/main`.
- A dispatch may name `source_commit`. That commit must pass the same query for its own sha.

**Two jobs.** All aelix-ai code runs in the job that cannot write.

1. `build`, with `permissions: contents: read`:
   - checks out aelix-ai at the chosen sha (`fetch-depth: 0`, for the lineage) and the catalog
     repository at its default branch's head, not the run's own commit (#616, §10 2026-10-30),
     both with `persist-credentials: false`;
   - runs steps 1-4 below;
   - uploads the revision, the next index and the summary as an artifact.
2. `publish`, with `needs: build`, `permissions: contents: write, pages: write, id-token: write` and
   `environment: github-pages`. It runs no aelix-ai code and no `uv`. It:
   - downloads the artifact, and takes only the revision file and the next index from it;
   - re-reads the hold (below) at the catalog repository's current head;
   - checks, with its own code (stdlib, in the catalog repository), that the revision's sha256
     matches its name and that the index has the schema's shape and names it;
   - writes what its own code decides: the new `v1/revisions/<name>.json`, `v1/index.json`,
     `control/hold.json` and the pruning (Failure handling). The artifact can supply only the first
     two, so a compromised build cannot delete or rewrite another path or the hold.
   - `rollback`, `withdraw` and `release-hold` skip `build`: the job writes their index and hold
     from the dispatch's inputs and the repository's history, and runs under `if: ${{ !cancelled()
     && !failure() }}` so a skipped `build` does not skip it.
   - commits, and pushes without force. A push refused because the branch moved publishes nothing.
   - deploys Pages from that tree in the same run, because a push made with `GITHUB_TOKEN` starts no
     other workflow.

Every action is pinned to a commit SHA with its tag in a comment, as `pages.yml` and `ci.yml` pin
theirs. pi's generate job runs read-only with `persist-credentials: false` the same way (§3.1).

**What it runs.** All logic lives in aelix-ai, reviewed and tested there. The catalog workflow only
calls it.

1. `uv sync --frozen` in the aelix-ai checkout at that commit.
2. `scripts/publish_catalog.py build --fetch` fetches models.dev's `api.json` and OpenRouter's
   public `/api/v1/models` (recording each one's sha256 and the fetch time for the index entry),
   runs `refresh_catalog.py`'s additive plan over the committed catalog with each new reasoning
   row's thinking map derived (§7 Q2), applies it to the checkout, computes the lineage and writes
   the revision. The `off: null` the plan finds for shipped rows (§10, 2026-10-26) goes in the
   summary and is not applied.
3. Validate, and publish nothing on any failure:
   - The pin test, `tests/test_catalog_corrections_are_pinned.py`, runs against the applied
     checkout, so no revision carries a value the pin test rejects at that commit. So do
     `tests/test_catalog_overlay.py` (the ladder's audit) and
     `tests/test_hosted_catalog_contract.py`.
   - Additivity is re-checked: every committed row is present with identical facts, and nothing is
     dropped.
   - No row carries `api`, `baseUrl`, `headers`, `compat` or `provider`.
   - Every row parses with the client's parser (§4.3), and every new row inherits, through the
     client's ladder over the committed catalog and the revision's `family` and `releaseDate`, the
     transport the generator wrote. The publisher lowercases `family`, as the ladder does
     (`refresh_catalog.py:169`); any other value the parser rejects fails the run, loudly.
   - Size and row caps: at most 16 MiB, 20,000 rows and 5,000 rows for one provider.
   - Every committed provider is present, and no provider the committed catalog lacks appears. A
     revision cannot add a provider (#172, ADR-0232).
   - At most 100 ids are new since the previous published revision, or since `main`'s catalog for
     the first one. A dispatch's `max_additions` raises that cap for one run. This bounds a
     models.dev flood, which is otherwise bounded only by the 5,000-row cap per provider.
   - The lineage's last digest is the committed catalog's at the source commit.
4. If the revision's name equals the one the index names for this minimum version, stop: it is
   already current (pi `.mjs:295-301`). Nothing time-dependent is hashed (Format), so a slot with
   nothing new commits and deploys nothing.
5. The publish job commits and deploys (above). The catalog repository's history is the publication
   log.
6. Write a job summary. It lists:
   - rows added per provider, and each new reasoning row's map source (§7 Q2);
   - the new rows that can move a typed string (§4.4, §7 Q4): an id whose first `/`-segment names
     another catalogued provider, and an id another provider also lists;
   - against the latest release's catalog (read at its tag):
     - the new rows it could not inherit transport for;
     - the rows it would drop, and the fields it would not take, keeping its own value or none (a
       map that names a level it lacks among them), each list at most 200 lines (#616, §10
       2026-10-30);
     - the rows it lacks that `main` has committed but whose inherited transport differs from
       `main`'s. These are hand-fixed rows reaching older clients with the ladder's quirks.
     - whether the client's whole-revision bounds (§4.3) would refuse this revision for that
       release.

**The hold.** `control/hold.json` in the catalog repository, outside the deployed `v1/` tree.

- A dispatch with `action=rollback revision=<revision>` or `action=withdraw` writes the hold (its
  reason, run, time and the revision it pins) in the same commit as the index it publishes, which
  carries `hold` (Format) so that clients know its removals are a hold's (§4.4). The owner can also
  add the file by hand in the GitHub UI.
- While the hold exists, every scheduled run and every `action=publish` dispatch stops before
  building, puts the hold in its summary, and publishes nothing. The build job reads the hold at the
  default branch's head, so a run that waited behind the rollback that wrote it stops too (#616).
- Only `action=release-hold` removes it, so a rollback or a withdrawal stays in force until the
  owner lifts it, whatever `main` and upstream do meanwhile. The next slot then publishes from
  `main`.
- The publish job re-reads the hold at the head it pushes onto, so a hand-added hold also wins over
  a build that started before it.

**Format.**

- `v1/index.json`: `{"schema": "aelix-model-catalog-index/1", "catalogs": [{"minimumAelixVersion",
  "revision", "publishedAt", "sourceCommit", "upstream": {"modelsDevSha256", "openRouterSha256",
  "fetchedAt"}, "rows", "newRows"}], "hold"}`, with one entry per minimum version, as pi's, and
  `hold` (`{"action", "at"}`) only in an index a rollback or withdrawal publishes. Build metadata
  lives here only, as pi keeps `publishedAt` and `sourceCommit` in its index entry (`.mjs:238-246`).
- `v1/revisions/sha256-<hex>.json`: `{"schema": "aelix-model-catalog/1", "lineage": […],
  "providers": {"<provider>": [row, …]}}`.
  - It is canonical JSON: keys sorted, providers and rows sorted by id, no insignificant whitespace,
    UTF-8. One serializer, shared by the job and the contract test, writes it.
  - `<hex>` is the sha256 of the file's bytes.
  - No time, commit or run id is inside, so the name changes only when the rows or the lineage
    change. MEASURED (`r5/out/lineage_probe.txt`): built twice with different build metadata, one
    name. r4's format, with `generatedAt` inside, gave two.
- A row holds facts only: `id`, `name`, `reasoning`, `thinkingLevelMap` (optional), `input`, `cost`
  (`input`, `output`, `cacheRead`, `cacheWrite`), `contextWindow` and `maxTokens`.
  - Every row, known ones included, also carries the ladder's inputs where models.dev lists the id
    for that provider: `family` and `releaseDate` (the generator's `release_date`, else
    `last_updated`). The ladder reads a new id's `family`, and both fields of each shipped row it
    compares (§3.2). They are not facts (§4.3).
  - MEASURED: today's catalog with its 11-digest lineage is 324,681 bytes, 27,740 gzipped, without
    those fields; their caps add at most 104 bytes a row (about 159 KB, not measured with
    models.dev's values), and the lineage at its cap about 17.5 KB, before gzip.
- One file per revision rather than one per provider. That means one request and one hash, and the
  two halves of a model added to two providers in one run can never come from two deploys.

**The ordering rule: lineage, not time.**

- A catalog's digest is the sha256 of its canonical JSON (as above), cut to 32 hex characters. Being
  canonical, it does not change with a CRLF checkout or key order.
- `lineage` lists, oldest first, the digest of every version `models_generated.json` has had on
  `main`'s first-parent history up to the source commit, at most the newest 500 (`main` has 11 since
  2026-05-20). Its last entry is the source commit's catalog.
- A client installs a revision only when the lineage holds its own shipped digest (§4.3), that is,
  when the revision was built from a `main` commit whose history contains this build's catalog.
- There is no stamp file and no hand step. The client computes its own digest from the shipped file,
  once per process when it first compares. MEASURED: re-reading and parsing the file takes about 2
  ms, and the digest about 3 ms.
- MEASURED over the 11 versions (`r5/out/lineage_probe.txt`): of 121 (build, revision) pairs, none
  installs older facts as newer, and none withholds a revision built at or after the build's own
  version. A checkout with one unmerged edit is in no lineage. That holds for distinct digests. A
  catalog revert (A, B, A) repeats one, and until the next slot a build cut at the second A installs
  the B-era revision, so a revert that cannot wait is paired with a rollback.
- So the cases r4's build-time stamp got wrong cannot occur: a slot from a `main` older than a
  release never reverts its corrections or maps, a dev checkout's unmerged edit is never masked, and
  a revision built after a release, carrying its catalog, is installed.

**`MINIMUM_AELIX_VERSION` starts at `0.1.0b2`.**

- Every checkout reports `0.1.0b2` until R's release commit bumps the version
  (`RELEASING.md:88-97`), so a `0.1.0b3` floor would select nothing before R and §8's live checks
  could not pass in B5.
- It admits nothing it should not: the released `0.1.0b2` wheel has no client, and every build with
  the client has #433's levels (B3 merges before B5). Bumping the version early would make dev
  builds claim a version PyPI lacks, which the update check (#386) compares against.
- The floor rises, as pi's does, only when published values need client behaviour that older clients
  lack. A floor-raising entry stays within the oldest admitted client's caps (§4.3).
- A thinking level an older client does not know raises no floor and adds no entry (§10 2026-10-30,
  #617). That client's parser drops a new id whose map names the level. For a known id it keeps
  the shipped map, so `main`'s map for that row reaches the client only after an upgrade (§4.3).

**Integrity.**

- HTTPS only.
- Revision paths are immutable and content-addressed, so Pages' per-file CDN caching can never pair
  one deploy's index with another deploy's revision.
- The client verifies a revision's sha256 against its name before parsing, and caps every read
  (§4.3).

**Signing is not added in beta.3.**

- (a) A published row cannot carry transport, so a forged revision cannot send a key to a new host
  or add a provider.
- (b) A forged revision's new ids route like shipped rows (§7 Q4), so it can move a typed string
  between the user's own configured providers, as a release could. A hold stops that for resolutions
  after each client's next check (§6.1), and the rest is bounded: wrong numbers within the floors
  and the regression bound, bogus rows within the caps, curated maps removed within the bound
  (§4.3).
- (c) A signing key held by the same scheduled job would not separate a forged revision from a real
  one. A key held outside it would put a person in every publication.
- The catalog repository and the actions it pins are a surface of their own; a compromise there
  cannot forge an aelix release, and its worst case is (b). pi signs nothing either.

Signing is added when a row may ever carry transport, when aelix provisions a first-party key
(`FIRST_PARTY_KEYS`, ADR-0189), or when a custom domain is compiled in (§6.2). `index.json` then
gains an `.aelixsig` sidecar, verified fail-closed by the marketplace's code, which is the
marketplace's progressive rule (ADR-0192).

**Failure handling.**

- Any failed step publishes nothing. The index keeps the last good revision, clients keep theirs,
  and the failed run is the owner's notice. A models.dev or OpenRouter list that is down, malformed
  or shrunken fails the fetch, the additive plan or a cap.
- A published revision that turns out wrong is one dispatch away from repair, and both repairs set
  the hold. `action=rollback` points the index at a deployed revision; clients follow it if its
  lineage admits them, and otherwise drop their overlay (§4.3). `action=withdraw`, the kill switch,
  publishes an index with no catalog, which removes each client's overlay at that client's next
  index check (§6.1 says when), plus Pages' own cache lifetime (not measured).
- A revision stays deployed while an index entry names it, and the last 30 always stay. Pruning the
  rest is the publish job's own code.

**Schedules in a public repository.** GitHub disables a public repository's scheduled workflows
after 60 days without activity (its documentation), so 60 days with no catalog change and no
upstream addition stop the slots; clients keep the last revision until the owner re-enables them.

**Keeping CI, `main` and the hosted file consistent.**

- aelix-ai's CI runs `tests/test_hosted_catalog_contract.py` on every pull request, without network:
  `publish_catalog.py build` over the committed catalog with no additions must produce a revision
  the client parser accepts, whose lineage ends in the shipped digest and which reproduces every
  shipped row (§3.2). A frozen sample, `tests/fixtures/hosted_catalog/v1-sample.json`, must parse
  forever under schema 1. So the publisher and the parser cannot drift apart in any commit.
- The publish job runs the pin test on what it publishes (step 3), so the hosted file cannot
  contradict a pin of its commit, and a new pin on `main` reaches users at the next slot.
- **Fold-back.** Published additions reach `main`, and so a release, only through a commit. R's
  prep, and the owner at any time, runs `scripts/refresh_catalog.py --fetch --apply` and the pin
  test and commits the result, so the ladder's transport and the derived maps for those rows are
  reviewed once, in a diff. `RELEASING.md` gains the step. It commits upstream as of fold time, not
  exactly what was published, with no 100-id cap, and it proposes again a row a hold rolled back:
  the diff review is the gate (§6.2). Without the fold-back, ids new to the latest release grow by
  up to 100 a slot until the client's 500-per-provider bound refuses every revision (§4.3).

### 4.3 The client overlay (#434)

**The contract** - pi's, in aelix's names:

- `ModelsStore` (a Protocol with `read`, `write` and `delete` per entry key; synchronous, so the
  registry's constructor can read it) and `InMemoryModelsStore` in aelix-ai. `FileModelsStore` lives
  in coding-agent, where pi keeps it, at `<agent dir>/models-store.json`, never resolved against the
  cwd.
- `RefreshModelsContext` (pi's fields), `ModelsPublication(persist, update)`, `publish(...) ->
  bool`, and `ModelsRefreshResult(aborted, errors, published, skipped)`.
- `ModelRegistry.refresh_models(*, allow_network, force=False, sources=None, signal, trigger)`,
  async. The synchronous `refresh()` and `reset()` keep their signature and meaning.

**Rules.**

1. Cancellation belongs to the trigger (`fed6009cc`). Every trigger passes its own signal and bounds
   its wait at 15 s, and the registry has no timer. `allow_network` is false whenever `is_offline()`
   is true, so an extension that calls `refresh_models` honours `--offline`.
2. Publication is generation-checked and serialized per source.
   - A run supersedes the source's previous run. `publish` refuses once the run is superseded or
     aborted, checking before persisting and again before updating memory (pi `models.ts:491-536`).
   - It persists first and then updates memory, as pi does. A write that fails publishes nothing in
     memory, and the run reports `failed`; pi's `publishProviderModels` awaits the write before
     `update()` (§3.1). Failing writes include a read-only agent dir, a Windows sharing violation
     and a lock not taken in time.
   - Under the lock, the persist first compares the stored entry with the one the run started from
     (`checkedAt`, `etag`). When another process persisted meanwhile, the run's result is dropped
     and the stored entry is published instead, so a slow run never writes an older index over a
     newer one, a withdrawal included.
   - So a child spawned after a publication always reads the revision its parent runs on.
   - The narrowing source is the exception: its in-memory-only case (§4.5) is allowed, because
     narrowing never changes routing, so a child that lacks it resolves the same.
3. A publication never advances ADR-0262's generation.
   - A hosted publication installs the overlay (below) and recomposes the registry's rows from the
     last load's configuration: no `models.json` read, no command-cache clear, no credential command
     and no token refresh. A narrowing publication replaces only the served layer.
   - Every `_load_models()` (`refresh()`, `reset()`, registrations, the login wizard's two calls)
     composes over the installed overlay and re-applies the narrowing.
   - MEASURED (§3.2): the generation bump refuses an in-flight request, and without it the request
     validates. A hosted publication cannot change an existing row's request identity, because that
     row keeps its shipped transport.
4. The previous list survives failure: a failed run publishes nothing.

**The store.** `{"version": 1, "salt": "<64 hex>", "entries": {"aelix-hosted": …, "openrouter":
…}}`.

- `aelix-hosted` caches what was published, not one build's view of it: every build using the agent
  dir reads it (a release beside a dev checkout of `main`, or two versions). It holds `url` (the
  index URL it came from; an entry from another URL is ignored), the index's `catalogs`,
  `checkedAt`, optionally `failedAt`, `etag` and `lastModified`, each revision that index names,
  whole and keyed by name, and `removed`, the ids a hold took out (§4.4). Each build decides at read
  time, from a revision's lineage, whether it is admitted. So no build empties another's overlay,
  and the 4-hour skip and a 304 always stand for stored revisions.
- Writes:
  - `<path>.tmp` at `0o600`, fsync, `os.replace`, with the directory at `0o700`.
  - The read-modify-write runs under an exclusive `flock` on `models-store.json.lock`, a sidecar
    that is never replaced. pi's lock is a sidecar too (§3.1), and a lock on the data file was
    measured not to exclude (§3.2).
  - The lock is taken with `LOCK_NB` and a bounded retry of at most 2 s, so a lock another process
    holds cannot stall event-loop shutdown. Not taking it in time is a failed write (rule 2).
  - Windows gets an in-process lock only, as `auth.json` does; a lost race costs a refetch.
- Reads:
  - A missing, empty or unparseable file is an empty store, with a warning.
  - A `version` this build does not know is neither read nor rewritten, and hosted publications then
    apply nothing (rule 2). An older aelix after a downgrade runs on its shipped catalog and cannot
    clobber a newer store (#383).
  - Every entry is re-validated by the parser a network response goes through, and each revision
    against its name's sha256, so a hand-edited store adds nothing the network could not.
  - Transport is re-inherited from the shipped catalog at every start, and the lineage check is
    re-run against this build's digest, so an upgrade re-derives both.

**The hosted source** (coding-agent, where pi keeps `withRemoteCatalog`):

- Cache phase, inside `ModelRegistry.create()` before the first composition, offline included:
  select this build's entry from the stored `catalogs` (step 2) and install its revision when the
  revision's lineage holds this build's shipped digest.
- Network phase, when allowed and not aborted.
  - Unless forced, it is skipped while `checkedAt` is within 4 hours (pi's interval), or, with no
    index stored, while `failedAt` is within 15 minutes. A timestamp in the future counts as absent.
  1. `GET {base}/v1/index.json`. It sends `If-None-Match` or `If-Modified-Since` only when every
     revision the stored index names is stored and verifies, as pi revalidates only when a stored
     body backs it (`:144-147`). Pages sends an `ETag` and a `Last-Modified` and answers
     `If-None-Match` with 304 (measured 2026-10-27, §10 2026-10-28); nothing depends on them.
     - 304 keeps the stored index and its revisions.
     - 404 means no hosted catalog: store an empty index. The overlay already installed stays for
       the rest of the process, and the next start has none. That is pi's 404 (§3.1).
     - Any other non-2xx, a transport error, or an index over 64 KiB or malformed is a failure.
  2. Select the entry with the highest `minimumAelixVersion` not above this version (pi's
     `selectModelCatalog`; PEP 440 through `packaging`, a coding-agent dependency). No such entry,
     or an empty `catalogs` (withdrawn), removes the overlay now. pi's index has no withdrawal;
     aelix's acts at once because it exists to stop a bad revision. A model already selected from
     the overlay keeps its row (§4.6).
  3. `GET {base}/v1/revisions/<revision>.json` for each revision the index names that is not stored,
     the selected one first, counting decoded bytes while streaming (a 16 MiB cap), and verify its
     sha256. For the selected revision, any non-2xx (404 included: a deploy still propagating) or a
     mismatch is a failure, and the stored index and revisions stay. A failure on another entry's
     revision leaves that revision unstored and is reported; the next check, 304 included, fetches
     the revisions still missing. Otherwise the new index and its revisions are persisted together,
     and revisions it no longer names are dropped, after `removed` is updated from them (§4.4).
  4. If the selected revision's lineage holds this build's digest, parse and publish it. If not,
     remove any overlay; the store is the same either way.
     - "Not" covers a revision that predates this build's catalog, and a build whose catalog never
       reached `main`, such as a dev checkout with an unmerged edit.
     - The client takes what the index names or nothing, never the newest revision it has seen. A
       rollback to a revision older than this build leaves the build on its shipped catalog.
- Success, 304, 404 and a withdrawn index set `checkedAt` and clear `failedAt`. A failure sets
  `failedAt`, and also `checkedAt` when an index is stored (pi
  `remote-catalog-provider.ts:167-172`). So an unreachable host costs one bounded attempt per 4
  hours with a cache, and one per 15 minutes without one.
- Transport rules: HTTPS only, with at most 3 redirects, each to HTTPS; `User-Agent:
  aelix/<version>`; no credential is ever sent.
  - The base URL is the built-in one unless `AELIX_MODEL_CATALOG_URL` is set in the real
    environment. An empty value turns the hosted source off, as `AELIX_DEFAULT_CATALOG=` does for
    the marketplace. A literal loopback address over HTTP (`http://127.0.0.1[:port]`,
    `http://[::1][:port]`, redirects only to itself) serves the tests and §8's dry run; any other
    `http:` URL turns the source off with a warning.
  - The name goes on the `.env` floor: it decides what the global store holds for every directory.

**What a hosted row may set.** The parser lives in aelix-ai, and the publisher uses it too.

- Only catalogued providers; any other provider is ignored. Unknown fields are ignored, and
  transport fields are never read.
- `id`: 1 to 256 ASCII characters from letters, digits and `. - : / @ ~`, starting with a letter, a
  digit, `@` or `~`, with no empty, `.` or `..` path segment. Every shipped id fits (§3.2). Ids
  reach URLs (Copilot's policy path) and a child's argv.
- The other fields:
  - `name` passes through `safe_for_terminal`, at most 200 characters.
  - `reasoning` is a JSON boolean.
  - `thinkingLevelMap` keys are among this build's levels, and its values are `null` or 1 to 32
    characters of `[a-z0-9_-]`.
  - `input` is a subset of `text` and `image` that contains `text`.
  - `cost` is four finite, non-negative numbers of at most $10,000 per million, never a JSON `true`
    or `false`.
  - `contextWindow` is an integer from 1,024 to 100,000,000. `maxTokens` is an integer of 256 or
    more, clamped to `contextWindow`. Both floors are below every shipped row (§3.2).
  - `family` is at most 64 characters of `[a-z0-9._-]`, and `releaseDate` at most 10 of `[0-9-]`,
    compared as text, as the generator compares it.
- **A known id** (the shipped catalog has it) takes the hosted row as `main`'s complete facts for
  it, and keeps its shipped transport, so corrections and curated maps committed to `main` reach
  every client between releases. One exception is a map that names a level the client lacks
  (§10 2026-10-30).
  - Every required field (`name`, `reasoning`, `input`, `cost`, `contextWindow`, `maxTokens`)
    replaces the shipped value. A field that is present but fails validation, or a required field
    that is missing, keeps the shipped value for that field, and is counted.
  - A present `thinkingLevelMap` replaces the shipped map, and an absent one removes it (`main` has
    none): that is how the 24 rows #433 reclassifies reach clients.
  - `family` and `releaseDate` are not facts: the ladder reads them when the row is a sibling.
- **A new id** gets its transport from ADR-0232's ladder, moved out of `scripts/refresh_catalog.py`
  into aelix-ai so the generator and the client run one implementation.
  - The ladder runs over this client's shipped rows of that provider only, never hosted or user
    rows. Its upstream inputs are the revision's `family` for the new row, and `family` and
    `releaseDate` for each shipped row it compares.
  - No answer drops the row, and the drop is counted. So does a field that fails validation, and an
    id equal, ignoring case, to a shipped id of the provider or to an earlier hosted id.
- **The whole revision is refused** - this build installs none of it, keeps for the process the
  overlay it had, and starts without one while the index names it - when any of these holds:
  - it is over 20,000 rows or 5,000 for one provider, or over 2,000 ids new to this build or 500 for
    one provider;
  - it is not a JSON object of a known `schema`;
  - more than 25 known rows each lose their thinking map, or drop `contextWindow` or `maxTokens`
    below half the shipped value (the regression bound).

  A sha256 mismatch or a body over 16 MiB is a failed fetch (step 3). The regression bound stops a
  forged or broken revision that sets every window to 1,024 or strips every map. A legitimate
  correction that large meets it too, and every later revision carries it, so that release refuses
  later revisions until its users upgrade; the publish job's summary says so beforehand (§4.2 step
  6).
- No thinking map is derived on the client. A hosted row carries exactly the map aelix's generator
  wrote: for a known id, the curated map of `main`; for a new id, the derived map or none (§7 Q2).

**The effective built-in catalog.**

- `aelix_ai.models` gains three things: a function that installs a validated overlay, a function
  that returns the shipped rows, and the shipped digest.
  - Installing swaps in a new map, so a reader sees the old map or the new one, never a mix.
- `get_model` and `get_models` return the effective rows, as pi's readers read the wrapped provider.
  So these read the hosted revision: the registry, `compose_built_in`, the sibling backfill, guard
  2's OpenRouter namespaces (a revision widens them as a release would) and session statistics'
  fallback price.
- `get_providers` is unchanged.
- Copilot's policy enablement is a write to the user's GitHub account. It reads the shipped rows, as
  pi reads its shipped constant there (§3.1), and quotes the id in the path.
- The overlay is process-global.
  - `ModelRegistry.in_memory()` installs nothing.
  - `tests/conftest.py` gains an autouse fixture that installs no overlay after every test and
    resets the refresher's shared runs. So a test that installs an overlay cannot leak it into the
    next one.
  - A test that needs an overlay installs it itself.

### 4.4 Precedence, and what may move a typed string

Per provider, from lowest to highest precedence:

1. the shipped row;
2. the hosted revision, when its lineage admits this build;
3. `models.json` provider `baseUrl`/`compat` and `modelOverrides`, composed onto both by the
   function that composes shipped rows today, so the user's tuning beats the hosted file;
4. `models.json` custom rows, which win on `(provider, id)` and are never narrowed away;
5. extension registrations (an explicit map owns the provider, ADR-0262);
6. OAuth `modify_models`;
7. the owner filter.

That composition is the **route rows**. The **served list** is the route rows with OpenRouter's
narrowing (§4.5) applied to the catalogue rows of `openrouter`.

| Reader | Reads |
| --- | --- |
| `get_all`, `get_available`: the picker, `/scoped-models`, `--list-models`, TUI and RPC cycling, RPC `get_available_models`, the login wizard's provider rows, the first model after `/login` | the served list |
| ADR-0250's `resolve_route` (every step), `restore_session_route`, a child's pin, guard 2's namespaces | route rows |
| `/model <arg>` | route rows, each replaced by its served copy |
| `find(provider, id)` | the served row, else the route row |

**Published rows route like shipped rows** (§7 Q4). A known id's hosted facts change numbers, never
destinations. A new id answers every lookup a release shipping it would answer, so a publication can
move a typed string, and each revision's summary names the rows that can (§4.2 step 6).

MEASURED on `aa207239` (`r5/scripts/proto_r5_q4.py`, `r5/out/proto_r5_q4.txt`; r4's four key sets,
each with and without a paired Anthropic row, 56 results per run over `--model`, `/model`, a child's
pin, `--provider openrouter` and session restore, four OpenRouter rows published as new ids):
published new ids route as r4 measured a release with the same rows to, in all 56 results of every
run. With Anthropic and OpenRouter keys, `--model` and `/model anthropic/claude-sonnet-5.5` move
from Anthropic (a runnable custom id) to OpenRouter's new row, unless the revision also adds
Anthropic's own row under that spelling. `--model fireworks/ember-1` moves from a Fireworks custom
id with `api=unknown` (no adapter) to OpenRouter's row in all eight runs.

MEASURED on `bc83e0bc` (`r7/scripts/proto_r7_q4a.py`, `r7/out/proto_r7_q4a.txt`), pi's two further
consequences (§7 Q4): Anthropic's own row under its own spelling (`claude-sonnet-5-5`) does not win
`anthropic/claude-sonnet-5.5` back. A second keyed gateway publishing the same id turns `/model`
into "is served by several providers — name one" (`core/model_argument.py:409-413`), and `--model`
takes the first keyed provider in catalog order (§3.2): it stays on OpenRouter beside Vercel's row
and moves to OpenCode's beside OpenCode's, where pi gives an ambiguity error (§6.2). With only an
Anthropic key nothing moves.

**A resumed session whose model a hold removed falls back to the default model as pi's resume picks
it** (`core/sdk.ts:223-251`@99fe148; the owner's choice of 2026-10-19), never to a removed id, and
`/reload` keeps the open session's model. At `bc83e0bc` a resume restores it as a custom id
(MEASURED, `r8/out/restore_after_rollback.txt`).

- The client knows from the index. A check that persists an index carrying `hold` (§4.2) adds to the
  hosted entry's `removed`, per provider, each id a revision of the stored index named and none of
  the new one's names. An id a later index names leaves; at most 20,000 stay, oldest dropped. A
  **removed id** is one `removed` holds and the route rows lack.
- `restore_session_route` (`runtime_bootstrap.py:2222`) looks in the route rows first, so a shipped,
  user-defined or registered id restores as today; a removed id is not restored. Any other missing
  id, a session's or a default's, keeps today's custom id (a custom id the user typed, a model a
  release dropped), where pi falls back too (its `getModel` knows no custom id): #376's divergence.
- The fallback (`cli/entry.py:2019-2032`) resolves the settings pair `/model` saves
  (`cli/model_switch.py:115-120`), seeded into `parsed` (`:3176-3180`), as an explicit provider, so
  a missing id becomes a custom id (step E, `runtime_bootstrap.py:1990-2011`). MEASURED
  (`r10/out/fallback_candidates.txt`): `Could not restore model openrouter/fireworks/ember-1. Using
  openrouter/fireworks/ember-1`, and the same through `OPENROUTER_DEFAULT_MODEL` (step 0). pi takes
  a saved default only when `getModel` finds it with auth, else a provider default, else the first
  available model (`model-resolver.ts:576-606`, `main.ts:502-504`@99fe148). So a settings default
  or `OPENROUTER_DEFAULT_MODEL` naming a removed id counts as unset wherever it is resolved, and the
  skip sits where they are read into a resolution: `resolve_route` (`runtime_bootstrap.py:1876`)
  reads the variable itself (step 0, `:1973-1986`) and is told which of its strings the settings
  seed supplied (`cli/entry.py:3146-3182`, the pair's one read on these paths, puts it in `parsed`
  and `/agents use`'s baseline, not in `parsed.provided`). It asks whether an id is removed at each
  call, so a hold stored mid-process reaches the next one. The post-`/login` pick reads the global
  pair into `find_initial_model`'s step 3 (`core/model_resolver.py:861-863`), whose `find` through
  `_RouteAuthView` already misses a removed id. Known callers, examples and not the definition
  (`r11/out/callers.txt`): every build through the harness factory (`cli/entry.py:2027`: a launch
  with no `--model`, a resume's fallback, `/new`), the late-route hold
  (`runtime_bootstrap.py:2709`), and `/agents use` dropping a route, which resets `parsed` to the
  baseline (`agents/service.py:404`; back-filled at `cli/entry.py:3170-3175`), then resolves and
  sets it (`agents/service.py:605-642`); MEASURED (`r11/out/agents_use_drop.txt`), today that sets
  the removed id. The next candidate is aelix's port of pi's, `find_initial_model`'s steps 4-5 as
  the post-`/login` pick runs them: the first runnable `get_available()` model in
  `DEFAULT_MODEL_PER_PROVIDER` order, else the first runnable one, through `_RouteAuthView`
  (`core/model_resolver.py:247-290`, `tui/shell.py:4141`); else no model. (A launch or a resume
  with no default at all still resolves none, unlike pi's.)
- It says so in #376's place (the TUI transcript; stderr in print and RPC mode): `Could not restore
  model <provider>/<id> (withdrawn from the published catalog). Using <provider>/<id>`; with no
  session model, `Default model <provider>/<id> was withdrawn from the published catalog. Using …`.
  With no candidate, each line ends before `. Using`, as #376's does
  (`runtime_bootstrap.py:2281-2310`), and the session has no model, as a launch with none has;
  pi's says `No models available` and how to log in instead (`sdk.ts:246-247`,
  `auth-guidance.ts:14-16`@99fe148).
- `/reload` rebuilds through the same factory (`cli/entry.py:3472-3490`,
  `agent_session_runtime.py:1139-1164`), where pi's keeps its `AgentSession` and model
  (`agent-session.ts:3690-3729`@99fe148), so `ReloadSeed` (`runtime/_types.py:45-62`) carries the
  current model and the factory keeps it when its id is a removed id. `/resume`, `/fork`, `/import`
  and RPC `switch_session` fall back, as pi's do through `createAgentSession`
  (`agent-session-runtime.ts:196-214`, `:262-341`, `:359-393`@99fe148).

**OpenRouter's narrowing never moves a typed string: it hides rows from what is offered, and routing
still sees them.** This is aelix's divergence from pi, whose personalized list replaces the
provider's models (§3.1), so pi cannot resolve a narrowed-away id. Narrowing written into the
routing rows flips typed strings: round 1 measured that (#136's shape), and r4 reproduced it.

MEASURED in r4's runs (`r4/out/proto_r4_routing.txt`): with two OpenRouter ids narrowed away and a
third's numbers changed in the served list only, 0 results and 0 child pins changed provider in any
run; the narrowed ids left the offered list (360 to 358) and, typed, still resolved, and `--model`
and `/model` showed the narrowed numbers. Written into the registry's rows instead, the same
narrowing moved 3 results with Anthropic and OpenRouter keys and 2 in each other key set.

RPC `set_model` searches the offered list, as pi's does, so over RPC a narrowed id is refused. #450
moves it to `find` with the configured-auth test (§4.8).

### 4.5 OpenRouter's key-scoped narrowing (#450)

What is listed, and how its numbers are taken, follows pi's `personalizeOpenRouterCatalog`. Where
the result goes does not: the served list only (§4.4). The earlier revisions' safety rules apply.

- Input: the route rows' OpenRouter catalogue rows, shipped and hosted. Never `models.json` custom
  rows or registration rows.
- Output: a served copy of each listed id.
  - `contextWindow` = `top_provider.context_length || context_length || catalog`.
  - `maxTokens` = `top_provider.max_completion_tokens || catalog`, clamped to the window.
  - `cost` comes from `pricing` when present. A missing, unparseable or negative base price becomes
    0, pi's semantics. Prompt-length tiers and time-of-day overrides are dropped, so `/cost`
    under-reports long prompts on those models exactly as it does today.
- Catalogue rows the list lacks are hidden. Ids the list holds that the catalog lacks are ignored,
  so narrowing adds nothing.
- An empty result falls back to the catalog. A bad number keeps the catalog's value for that field.
  A source may keep a static row it never lists (`auto`; #398 decides that id — 2026-10-20: it
  stays, the id a provider-less `--model openrouter/auto` names; ADR-0196's #398 amendment).
- The request: `GET {base}/models/user` with the key, no `offset` or `limit`, a 15 s total timeout,
  and no redirect followed with the key.
  - `{base}` comes from `with_openrouter_base_url` over the composed row. So `OPENROUTER_BASE_URL`
    beats a `models.json` `baseUrl`, which beats the catalog (ADR-0251 §11, #375): the key goes only
    where requests go.
  - 401, 403 and 404 are `Unavailable`: the catalog stays unnarrowed, recorded as checked.
- The response is refused whole, as a failure, when it decodes to over 16 MiB (counted while
  streaming), is not an object with a `data` list, holds over 5,000 rows, or is a partial page
  (`links.next` set, or `total_count` other than the rows received).
- Server text in errors is quoted and bounded (`quote_server_text`, `quoting_transport_errors`,
  `StatusBeforeBody`, `oauth/_helpers.py`). The key appears in no error, event, log line or store
  entry.
- The credential is the request's own key, resolved in ADR-0251's order.
  - A stored OAuth credential is refreshed first, shielded from the catalog's cancellation (pi
    `models.ts:630-631`).
  - A background run never runs a `!command` (pi #10729, ADR-0263). It skips the provider until a
    request has resolved that key.
  - A key that only a cwd `.env` supplies never narrows (§7 Q3), decided by the provenance guard 1
    reads (§3.2). The picker then shows the unnarrowed catalog with one line saying why, and the key
    keeps authenticating requests as today.
- The `openrouter` store entry, one per provider as pi's, holds `source`
  (`openrouter-models-user/1`), `principal`, `state` (`listed` or `unavailable`), `checkedAt`, an
  optional `failedAt`, and the listed ids with their numbers.
  - `principal` is an HMAC-SHA256 under the store's salt over a domain tag, the provider, the
    effective base URL and the resolved key. The resolved key is a literal's value, a named
    variable's value or a command's output. For OAuth it is a stable account identity, never the
    token; with no such identity, narrowing is memory only.
  - The entry applies only when its principal matches, checked at publication and whenever the
    served list is computed. So `--api-key` or a changed exported key changes the list at the next
    read. pi keeps the previous key's list until the next refresh
    (`packages/coding-agent/docs/providers.md:122`@99fe148).
  - `/logout` deletes the entry (the owner's choice of 2026-10-17): it is that account's view of
    OpenRouter and does not outlive the login, and a key still exported refetches it at the next
    trigger. pi's logout deletes only the credential and keeps the list
    (`packages/ai/src/models.ts:825-834`@99fe148).
- Freshness: none, as pi's list has none (`remote-catalog-provider.ts:187-194`@99fe148). Every
  trigger that refreshes all sources refetches it (§4.6), and a failure keeps the stored list.
  Delegated children make no start-up refresh, so N children do not make N requests.
- The lane's first step, before code: call the authenticated `/models/user` with the owner's
  ordinary key (CLAUDE.md rule 10) and record the status, fields, validators, size, and whether
  `auto` and `:batch` ids appear. If an ordinary key gets 403, the source reads `GET {base}/models`
  with the key instead, with the same narrowing; an organization can filter that list for API keys
  (pi #10353).
- #450's issue body (rows from OpenRouter metadata, a new model in `-p`) no longer matches, so on
  acceptance its scope and acceptance are rewritten to the narrowing alone (§8).

### 4.6 When a refresh runs: pi's triggers

| Trigger | Module | Sources | The user |
| --- | --- | --- | --- |
| interactive start-up | `tui/shell.py`, once the TUI is up | all | does not wait; 15 s in the background; the footer updates when it lands (pi `interactive-mode.ts:1149-1156`) |
| RPC start-up | `rpc/rpc_mode.py`, once the harness exists | all | does not wait; 15 s in the background (pi `main.ts:938-945`) |
| `/model` picker | `tui/model_picker.py`, `tui/context.py`, `tui/shell.py` | all | sees the cached list at once; the list re-renders when the run lands, with one status line (refreshed, could not refresh and showing cached models, or timed out); the last waiter to leave aborts the run (pi's selector and coordinator) |
| `/scoped-models` picker | `tui/scoped_models.py`, `tui/shell.py` | all | as the `/model` picker (pi `interactive-mode.ts:3212-3214`, `:5471`@99fe148) |
| `/model <term>` with no match | `cli/model_switch.py` | all, not forced (the hosted TTL holds) | "Refreshing model catalogs…", waits up to 15 s, matches again (pi `:5233-5265`) |
| `/login` success | `tui/shell.py`, `tui/login_wizard.py` | the hosted catalog when stale; that provider's key-scoped source, forced | does not wait; a model chosen meanwhile is kept (pi `:6152-6170`) |
| `/logout` | `tui/login_wizard.py` (`run_logout`) | none: that provider's narrowing entry is deleted (§4.5) | - |
| `aelix update --models` | `cli/args.py`, `cli/entry.py` | all, forced | waits up to 15 s, prints each source's outcome, exits 1 on a failure (pi `package-manager-cli.ts:605-631`); `aelix update` without `--models` exits 2 and names it |
| `aelix --list-models` | `cli/list_models.py` | none: the cache | - (pi `list-models.ts:29-39`) |
| print mode (`-p`) | - | none: the cache | - (pi has no print start-up refresh) |

`aelix update` is new CLI surface. It becomes the fourth root command (`cli/args.py:109-110`), so
`aelix update` stops being a one-word prompt. MEASURED (`r5/out/root_verb_probe.txt`, `update` added
to `ROOT_COMMANDS` in a probe): `aelix -- update`, `aelix -p update` and a prompt with `update`
later in it still send the words.

Refreshes never run offline (`--offline`, `AELIX_OFFLINE`, `PI_OFFLINE`), and never at start-up in a
delegated child (`AELIX_SUBAGENT_DEPTH` of 1 or more), which reads the cache its parent wrote: the
one divergence in the triggers. pi refreshes at every RPC start-up, so N RPC children would make N
refreshes, and the narrowing source has no interval to stop them (§4.5).

- Concurrent triggers share one run per source (pi's coordinator). The refresher holds its tasks
  with strong references, and event-loop teardown at exit cancels a run before it publishes.
- A publication never changes the harness's current model, between turns included, so #450's "a
  refresh does not change the turn's model" holds without harness code; the current model keeps its
  numbers until it is selected again.

### 4.7 Layering and observability

- **aelix-ai** holds `ModelsStore`, `InMemoryModelsStore` and the entry and contract types; the
  hosted format and parser, the canonical digest and ADR-0232's ladder, shared with `scripts/`; the
  overlay install, the shipped accessor and the shipped digest in `models.py`; and, with #450, the
  OpenRouter narrowing. It imports nothing upward: the agent dir, the offline answer, the
  credential, the base URL and the version predicate arrive as arguments.
- **coding-agent** holds `FileModelsStore`, the hosted source, the refresher, route rows and the
  served list, publication and the principal in `model_registry.py`, the triggers and `aelix update
  --models`. With #450 it gains the route reads in `cli/runtime_bootstrap.py` and
  `core/model_argument.py`, since narrowing is all that separates route rows from the served list.
- **agent-core** holds one observational event, `model_catalog_refresh`.
  - It carries the source (`aelix-hosted` or `openrouter`), the trigger, the outcome (`published`,
    `unchanged`, `unavailable`, `withdrawn`, `not-applicable`, `failed`, `aborted`, `skipped`), the
    revision, counts of rows added, changed, removed and dropped for want of transport, a quoted
    error and `checked_at`. It carries no credential and no URL.
  - It is emitted on the live harness's bus once per source per run, after publication. The shell
    and RPC mode bind it, and rebind it on a hot-swap.
  - A handler's failure never fails a refresh or a turn, and a handler cannot veto or edit a list.
  - It is aelix's first event pi lacks (#434 and AGENTS.md ask for a hook-bus event). The pi-parity
    fixture gains an aelix-only bucket, and the typed `on()` overloads in `extensions/api.py` move
    with it.
- `aelix update --models` and `--list-models` report on stderr.
- #292 (B3) puts the import-direction gate in front of all of this.

### 4.8 Batch placement: a re-cut of B4 and B5, and one more batch

The triggers need `tui/shell.py`, `cli/entry.py`, `cli/args.py` and `rpc/rpc_mode.py`. In B4, #380
holds `entry.py` and `shell.py`, and #415 holds `rpc_mode.py`. In B3, #433 holds `args.py` and the
catalog. And #434's client parses what #172 publishes. So:

| Batch | Lane | Files | Why there |
| --- | --- | --- | --- |
| B4 | **#172**, in place of "#434 Phase 1-2" | `scripts/refresh_catalog.py` (the derived maps, §7 Q2); new `scripts/publish_catalog.py`; new `aelix_ai/catalog_inherit.py` (the ladder) and `aelix_ai/hosted_catalog.py` (the format, the parser, the canonical digest and the lineage); `tests/test_catalog_overlay.py`; new `tests/test_hosted_catalog_contract.py` and its fixture; ADR-0232's amendment; `RELEASING.md` (the fold-back, after B3's #382); outside the repo, `handochan/aelix-catalog` (its workflow, `control/`) | no B4 lane holds any of them; after #433 (B3), whose reclassified maps the first revision publishes |
| B5 | **#434** Phase 1-2 | `aelix_ai/models.py`; new store and contract modules; coding-agent's new `FileModelsStore`, hosted source and refresher; `model_registry.py`; `cli/runtime_bootstrap.py` (the `.env` floor, the resume and default rule); agent-core's `runtime/_types.py` and `runtime/agent_session_runtime.py` (`ReloadSeed`, §4.4); `oauth/github_copilot.py` (shipped rows, quoted id); `harness/hooks.py`, `extensions/api.py` and the pi-parity fixture; `tests/conftest.py` (the reset fixture); `tui/model_picker.py`; `tui/scoped_models.py`; `tui/context.py`; `tui/shell.py`; `cli/model_switch.py`; `cli/list_models.py`; `cli/args.py`; `cli/entry.py`; `rpc/rpc_mode.py`; `tui/login_wizard.py`; the guides; `SECURITY.md` | each is free once B4's #380 and #415 and B3's #433 have merged; B5's other lane, #386, holds `update_check.py` only |
| B6 | **#450**, then **#293** | the OpenRouter narrowing module in aelix-ai; `model_registry.py` (the source's wiring, the principal); `cli/runtime_bootstrap.py` and `core/model_argument.py` (route reads); `rpc/rpc_mode.py` (`set_model` through `find`) | `model_registry.py`, `runtime_bootstrap.py` and `rpc_mode.py` are #434's in B5; #293 stays the last code change, as planned |

**This adds a sixth batch, B6.** The plan of 2026-10-14
(`.omc/specs/beta3-batch-plan-2026-10-14.md`) had five (B4: #434 Phase 1-2; B5: #450, #386 and
#293). #450 cannot run beside #434 (both edit `model_registry.py` and `rpc_mode.py`), so the plan
gains one serial step whatever it is called; to keep five, call #450 then #293 the tail of B5,
started when #434 merges. R starts one serial lane later either way.

- The plan's #434 file column (`model_registry`, the store, `_helpers`, `hooks`) is replaced by this
  table when B4 starts. `runtime_bootstrap.py`, `hooks.py` and `model_registry.py` are heavily
  cited: run `scripts/check_citations.py --fix` after every rebase.
- In R, #383 covers an upgrade whose shipped digest is in no stored revision's lineage (no overlay
  until the next revision) and a downgrade facing an unknown store `version` (left untouched). #384
  covers the start-up refresh, offline, logout and a key change, live. R's prep runs the fold-back
  (§4.2).
- **#481**, #414's split-off part 3 (another aelix's `/logout` reaching a running session; filed
  with no milestone), edits `oauth/auth_storage.py`, `model_registry.py`, the provider guides and
  `run_logout` (`.omc/probes/414-live/part3/`), which #434 and #450 edit in B5 and B6. So it goes in
  B4, before #434, or after R if it is not in beta.3 (not decided). Landing first, it gives #434 a
  sidecar lock to reuse; it names this ADR's lock rule as its model.

## 5. Alternatives considered

- **pi.dev itself as the source** (#172's original text). Rejected by the owner's decision and by
  ADR-0232: pi.dev serves pi's rows, and a pi-faithful regeneration reverts 19 of aelix's 41 hand
  corrections; its rows carry transport (§3.1, MEASURED); it is a third party's server.
- **Published rows with transport**, as pi's. Rejected by the owner's decision. Transport from the
  network is the one thing that could send a key elsewhere, and inheriting it costs one ladder run
  per new id.
- **Another host** (§7 Q1). The marketplace's Pages would put the scheduled bot's commits beside the
  marketplace's own commits and validation workflows, two review domains in one history. aelix-ai's
  own Pages replaces the whole site from `site/` at each deploy (`pages.yml:19-25`), so the job
  would need `contents: write` on aelix-ai, or `pages.yml` would have to fetch the live catalog back
  at every site deploy. pi's single domain over R2 needs a stored upload secret (§7 Q1).
- **Prefer-newer by build time** (r4's `generatedAt` stamp), or **a catalog counter only `main`
  advances**. Replaced by the lineage. A time is not facts: a slot from a `main` older than a
  release, or a stamp written on a pull-request branch, could publish older facts as newer, and
  hashing the stamp made every slot a new revision. A counter needs a stamp file every catalog
  commit bumps, a pull-request branch bumps it too, and two branches at one count still need the
  content to tell them apart.
- **A publisher-written pointer to the sibling to inherit from**, instead of the client-side ladder.
  Not adopted: it lets the file name any of the provider's transports. The published `family` and a
  new id's namespace steer the ladder too, and so may a revision's `family` on known rows; rung 3
  settles over every namespace's family-less rows, so a Bedrock `eu.` id with no `eu.` relative
  inherits `us-east-1` (`r7/out/r6review_eu_probe.txt`, `r7/out/bedrock_orphans_bc83e0bc.txt`). The
  bound is the provider's shipped hosts, as a pointer's would be, and the ladder MEASURED reproduces
  the generator's wire on all 522 past additions it answers (§3.2).
- **One file per provider**, as pi.dev serves. Not adopted: a whole revision is 28 KB gzipped, and
  per-provider files cost 35 requests or a hash list and can pair halves of one addition from two
  deploys.
- **Sign the index in beta.3**: deferred (§4.2).
- **Publish only reviewed commits** (a bot opens a pull request on aelix-ai, the owner merges, and
  the publish runs on `main`). Not adopted: it gives the scheduled job write access to aelix-ai and
  puts a person in front of every model, which is a release. The unattended part is limited to
  ADR-0232's rule-bound additions and the derived maps; every change to an existing row comes from a
  reviewed `main`, and the fold-back reviews the additions (§4.2).
- **Published new ids that answer only lookups naming their provider** (r6's Q4 option B: a mark on
  the row, a filter on the provider-less reads and on `/model`'s view, guard 2 kept on the shipped
  set). Rejected by the owner (§7 Q4): pi has no such mark, and B left a brand-new id typed without
  its provider unfound until a release. r5 measured it moving nothing (`r5/out/proto_r5_q4.txt`).
- **Copy a predecessor's thinking map to new ids** (r6's Q2 option B), or **hold new reasoning ids
  back** until `main` curates them (option C). Rejected by the owner (§7 Q2): pi does neither. The
  ladder's chosen sibling carried the hand-copied map for 1 of the 9 rows, and its provider-wide
  rungs would hand Gemini maps to Vertex Claude rows (`r4/out/ladder_replay.txt`).
- **OpenRouter's list as a source of new ids** (the earlier revisions). Superseded by the hosted
  catalog: it admitted 3 of 39 candidates (`r3/out/added_reasoning.txt`) and needed a
  canonical-spelling rule in `/model`.
- **Narrowing that replaces the provider's rows**, as pi's does. Rejected: it moves typed strings
  (§4.4, MEASURED).
- **Narrowing with a key only a project `.env` supplies** (r6's Q3 options B to D), rejected by the
  owner (§7 Q3), and **a 4-hour interval on OpenRouter's list** (r6), replaced by pi's refetch at
  every trigger: the delegated children it was for make no start-up refresh (§4.6).
- **Publish in memory when the store write fails** (r4). Rejected: a child spawned afterwards reads
  the old cache. pi does not do it either (§3.1).
- **`--list-models` refreshing on a terminal** (the earlier revisions). Replaced by pi's cache-only
  `--list-models` and `aelix update --models`.
- **An async `refresh()`** (#434's wording). Rejected: its callers are `shell.py` and `core.py`, and
  a synchronous reload without network is still the right meaning for `/login` and harness reload.
- **Lock the data file**, **cache per credential**, **a registry-owned timeout**, **a gate event**:
  rejected or deferred as before, on the lock probe, pi's per-provider entry, `fed6009cc` and
  "observation first" respectively.

## 6. Consequences

- A model aelix's generator admits for any built-in provider becomes selectable within a business
  day of models.dev listing it, without a release, with transport from the shipped catalog and a
  thinking map derived as pi derives one, or none (§7 Q2).
  - Corrections and curated maps committed to `main` reach every client the same way.
  - A published row routes as a release shipping it would, so a typed string can move to it at a
    weekday slot (§7 Q4). Each revision's summary names the rows that can do that, and a hold stops
    them for resolutions after each client's next check (§6.1).
- aelix makes one HTTPS request to `handochan.github.io` at a trigger (§4.6), at most every 4 hours
  unforced, which reveals the IP address and the aelix version, as pi's does to pi.dev. `--offline`,
  `AELIX_OFFLINE`, `PI_OFFLINE` or `AELIX_MODEL_CATALOG_URL=` turn it off. `SECURITY.md` says so,
  along with the store's file mode and the rule that transport never comes from the network.
- With a stored or exported OpenRouter key, the picker offers what the key may use, with its limits
  and prices, refetched at every trigger. A typed id the key cannot use still resolves, and fails at
  OpenRouter as it does today: what is offered and what a typed string reaches differ on purpose,
  for narrowed ids only. A key only a project `.env` supplies narrows nothing, and the picker says
  so.
- `aelix update` becomes a root command; until now it was a prompt (§4.6).
- `models-store.json` and `models-store.json.lock` appear in the agent dir. Deleting them loses only
  the cache.
- The owner runs the catalog repository: a failed scheduled run is an email, and a bad revision is
  one dispatch from a rollback or withdrawal that holds until released (§4.2).
- These docs change with the implementation: `docs/guides/providers-and-models.md` ("pi refreshes
  its catalog from pi.dev; aelix does not", line 247; the restore fallback, `:102-106`) and
  `models-json.md`; `RELEASING.md` (the fold-back); ADR-0232's amendment when #172 lands, not a
  supersession (the generator runs in CI and derives maps); ADR-0250's (a published revision's rows
  enter routing and guard 2's namespaces as a release's would).
- The first aelix-only hook event (§4.7).
- `auth.json`'s cross-process lock is taken on the file it then replaces (§3.2), so concurrent OAuth
  refreshes in three processes are not serialized. That is #481's item 6, placed in §4.8.

### 6.1 Known limitations in beta.3

- A new reasoning id that no upstream effort list and no provider rule covers arrives without a map:
  167 of the 230 in today's plan (§7 Q2). It offers levels off to high, and the OpenAI-family
  adapters send each level's own name (§3.2).
- The hosted catalog cannot add a provider, an adapter or an auth method; those still need a release
  (#172).
- A transport fix cannot reach clients without a release. That covers a new base URL, `compat` flag
  or header for an existing provider or id. pi moved OpenCode's base path from `/zen` to
  `/inference` through its catalog, and set Xiaomi's `supportsReasoningEffort: false` the same way
  (the header's Pi line); aelix's provider rules carry the map only (§7 Q2). The same channel broke
  pi's released kimi-coding clients (§3.1), which a row with no transport cannot do.
- A hold reaches a client at its next index check, plus Pages' cache. As pi's
  (`modes/interactive/model-catalog-refresh.ts:22`@99fe148), a client checks only at a trigger
  (§4.6: interactive or RPC start-up at depth 0, a `/model` or `/scoped-models` picker opening, a
  `/model` miss, `/login` when stale, `aelix update --models`, which forces it), and unforced only
  once 4 hours have passed. An open TUI or RPC process may get a hold late, or never if no trigger
  fires; print mode and delegated children never check and use what the last check stored. Then new
  resolutions follow the hold, a settings default it removed included, and a resume on an id it
  removed falls back to the default model (§4.4; a client that made no check while the hold stood
  records nothing), while an open session's current model, `/reload` included (§4.4, §4.6), and a
  child's pin (`agents/resolver.py:170-192`) keep their route. The owner's choices of 2026-10-17 and
  2026-10-19 (§7 Q4).
- Unattended additions can carry the ladder's quirks: without family or dates the client matched the
  generator's full transport on 99 of 102 and 377 of 422 rows (§3.2), a lower bound. With both
  published, a client on the lineage's last catalog runs the generator's ladder on its inputs
  (INFERRED), step 3 checks it, and each summary counts older releases' quirks (§4.2 step 6).
- An older release refuses every revision after a correction on `main` larger than the regression
  bound (§4.3).
- A published Copilot id is never policy-enabled at `/login`, which reads shipped rows as pi's does
  (§3.1), so a request on it may fail until a release ships it.
- Some builds get no overlay and run on their shipped catalog: a dev checkout with an unmerged
  catalog edit, a release cut from a commit whose catalog never reached `main`, and a build after a
  rollback to a revision older than it.
- A delegated child reads the cache as it was when it started.
- A credential command's narrowing applies only after a request has run the command in the process.
  An OAuth credential with no stable account identity is narrowed in memory only.
- Windows has no cross-process lock on the store.
- Not measured: Pages' cache lifetime behind the `max-age=600` it sends (its headers and validators
  were measured on 2026-10-27, §10 2026-10-28); whether a workflow's own pushes reset GitHub's
  60-day schedule timer; `queue: max` with two runs pending at once (§10 2026-10-28 never had more
  than one); a ladder replay with models.dev's `family` and dates; an authenticated `/models/user`;
  Q2's derivation inside the generator (r7 probed pi's rules over the generator's plan only);
  Windows; Python 3.11.

### 6.2 Open follow-ups

- `main` cannot say "never publish this models.dev id" except by changing the generator's rules. A
  pinned exclusion list would let a hold be released after a bad upstream row, and would stop the
  fold-back from proposing it again.
- The publish job's 100-new-ids cap and the client's 2,000 and 500 bounds are not checked against
  each other, and step 6 reports the client's bounds for the latest release only.
- **A custom domain** (§7 Q1): the homepage at its root and `models.<domain>` at the catalog
  repository, one name as pi.dev is, with no stored secret
  (`.omc/research/pi-catalog-2026-10-16/home-factcheck.md`, MEASURED on other repositories). The old
  `github.io` URL then answers a path-preserving 301 that can point at `http://` for up to 24 hours,
  until "Enforce HTTPS" can be selected, and beta.2's update check and marketplace fetch refuse it
  (fail closed); verifying DNS first shortens that. The registrar, DNS host and renewal become
  parties to trust, so signing comes with it (§4.2). `aelix.dev` and `aelix.ai` are taken.
- ADR-0250's step 5 takes the first keyed raw match in catalog order
  (`runtime_bootstrap.py:2168-2174`), where pi #10913 made the case an ambiguity error
  (`model-resolver.ts:250-259`, `:490-505`@99fe148). A second gateway's publication can now exercise
  it (§4.4).
- pi's other map rules are not adopted (§7 Q2): its per-provider derivations (Gemini, Mistral, Z.AI,
  Baseten, Kimi K3, Qwen Token Plan) and id-pattern rules (Copilot's `gpt-5` minimal as `low`,
  `generate-models.ts:1104-1106`@99fe148), and, since `d2e7d8b7` (READ), MiniMax's direct rows' maps
  from models.dev's effort levels, paired with `forceAdaptiveThinking`, a `compat` flag a published
  row cannot carry. aelix's Google adapter maps levels unlike pi's (`q2-factcheck.md`, error 4).
- 34 shipped reasoning rows of Together and the Xiaomi providers lack the map a new row of theirs
  gets (`r7/out/q2_derive_probe.txt`). Giving it them is a correction on `main` that fixes no
  measured failure: 31 of the 34 send no level name; 3 Together rows (`openai/gpt-oss-20b`,
  `deepseek-ai/DeepSeek-V4-Flash-0731` and `-Pro-0813`) send the level's own name as
  `reasoning_effort` (`openai_completions.py:809-819`; `r9/out/together_xiaomi_nomap.txt`),
  unmeasured against Together. pi's own treatment: gpt-oss hides off and minimal; V4-Pro offers off
  and high, as Together's default does, but sends `reasoning_effort: "high"`; V4-Flash takes the
  default and sends no effort (`generate-models.ts:179-219`, `:570-587`;
  `packages/ai/src/models.ts:1230-1238`@99fe148).

## 7. Decisions taken

The owner decided Q2 and Q3 on 2026-10-16, after reading a fact-checked report on how pi does each
(`.omc/research/pi-catalog-2026-10-16/ANSWERS-checked.md`, with a note and a fact-check per question
beside it), Q4, Q1, the rollback's reach and the narrowing's details on 2026-10-17, and the check
timing and a resume's fallback on 2026-10-19 (`.omc/specs/batch-beta3-b2-2026-10-14.md`). pi
citations here are at `99fe148`. The owner approved the ADR as a whole on 2026-10-20.

Owner-visible items approval accepts without a question of their own: `aelix update --models`, a new
root verb that makes `aelix update` stop being a prompt (§4.6); the catalog repository's three
weekday slots and its four manual actions (publish, rollback, withdraw, release-hold), and the
fold-back in R's prep (§4.2); the index floor `0.1.0b2` (§4.2); one HTTPS request at most every 4
hours (§6); the first aelix-only hook event (§4.7); a sixth batch (§4.8).

1. **Where the catalog is published: a dedicated public repository, `handochan/aelix-catalog`, with
   its own Pages and the workflow in it, and no stored secret** (2026-10-17; r6's option A; §4.2).
   - The pi fact, given when the owner asked whether one repository would be better: pi.dev serves
     pi's homepage and catalog under one domain from two places, a private site repository
     (`earendil-works/pi.dev`) and a Cloudflare R2 bucket pi's workflow uploads to from a protected
     environment (`.github/workflows/publish-model-catalog.yml:87`, `:93-94`, `:154`).
   - Accepted: the owner creates the repository and turns Pages on (source: GitHub Actions) once;
     aelix-ai gains no write permission; the client compiles in a `github.io` URL. A pi-like single
     domain is a follow-up, not this ADR's work (§6.2).
2. **Thinking maps for new reasoning ids: derived as pi derives them, and published without one
   where nothing applies** (2026-10-16; §4.2 step 2). No family-predecessor copy, no hold-back. #172
   adds this to `scripts/refresh_catalog.py`, for new rows only (the generator stays additive), in
   pi's order (#507 reads the same evidence for a shipped row's missing `off`, §10, 2026-10-26):
   1. A provider-wide rule hides every level but off and high: pi's Xiaomi rule
      (`packages/ai/scripts/generate-models.ts:2487-2503`, `6374c2ba`) and Together's default
      (`:215-219`, `:578-587`), the map only (pi's Xiaomi `compat` is transport here). pi #10818
      fixed MiMo's Chat Completions rejecting `reasoning_effort: "minimal"`, which aelix never
      sends. MEASURED (`r8/out/xiaomi_wire.txt`, `together_xiaomi_wire.txt`): its 21 Xiaomi rows are
      `anthropic-messages` and send a `budget_tokens` per level (`_anthropic_transforms.py:645`),
      and 22 of its 27 reasoning Together rows send only `reasoning.enabled` (the `together`
      thinking format, `openai_completions.py:809-813`), the other 5 a level's name (§6.2). So the
      rule only hides levels.
   2. An OpenRouter row takes its map from OpenRouter's public list, `reasoning.supported_efforts`
      (`openrouter-reasoning-options.ts:12-23`, `openrouter-catalog.ts:47`; pi's since `650e7a61`,
      2026-08-25).
   3. Any other row whose inherited transport sends the level's own name (`openai-responses`,
      `azure-openai-responses`, `openai-codex-responses`, and `openai-completions` with
      `thinkingFormat: openai` and reasoning effort supported; pi's `supportsDirectReasoningEffort`,
      `generate-models.ts:545-561`) takes models.dev's `reasoning_options`
      (`models-dev-reasoning-options.ts:18-30`; pi's main automatic path since `1ae06409`,
      2026-07-22): a listed level maps to itself, an unlisted one is hidden, off maps to `none` when
      listed. pi's `anthropic-messages` branch fires only where a loader set `forceAdaptiveThinking`
      first (Fireworks, `:1765-1770`; Claude's own rows get it later, `:1160`), and aelix's
      generator adds no Fireworks rows, so no `anthropic-messages` row is derived.
   4. Otherwise no map: levels off to high, and the OpenAI-family adapters send each level's name
      (§3.2), as pi publishes such rows (`packages/ai/src/models.ts:1228-1239`); pi.dev served 515
      of its 1,340 reasoning rows so on 2026-10-16 (`q2-factcheck.md`). A map names only the build's
      levels. This ADR assumes the post-B3 build: #433 (B3) adds `max` before #172 (B4) runs the
      generator (§4.8), so a derived `max` is kept and sent as `max`.

   MEASURED (`r7/scripts/q2_derive_probe.py`, `r7/out/q2_derive_probe.txt`; models.dev and
   OpenRouter fetched 2026-10-17 11:43, sha256 in `r7/fetch/sha256.txt`): the generator's plan,
   before step 3's 100-id cap, adds 265 rows, 230 reasoning: 38 take OpenRouter's map, 15
   models.dev's and 10 a provider rule, and 167 none; 39 derived maps named `max` (dropped at
   `bc83e0bc`, kept after #433). On the 66 shipped rows with a curated map that rules 2-3 also
   cover, the derived map offers the same levels on 8 (6 of 48 from models.dev, 2 of 18 from
   OpenRouter) and fewer on 42, mostly `minimal` (hidden on 57), which upstream does not list.

   Accepted: a new id's map is upstream's claim, unreviewed until `main` curates one, which then
   wins as a known id's, and it often offers fewer levels than `main` would. It can send values no
   unmapped row sends: `xhigh`, `max`, and `reasoning_effort: "none"` for off on an OpenAI-format
   Chat Completions row (`openai_completions.py:821-830`). A model that rejects a level its map
   offers fails at that level until a map or a rule is committed, which reaches every client at the
   next slot, as pi's Xiaomi fix reached users through pi.dev about 90 minutes after its commit,
   before a release (`q2-factcheck.md`).
3. **An OpenRouter key that only a project `.env` supplies never narrows the list** (2026-10-16;
   r6's option A; §4.5).
   - The pi fact: pi reads no project `.env` at all. Its standalone binary stopped loading one in
     1.1.0 (2026-10-07; `scripts/build-binaries.sh:130-131`, pi #10473), before per-key narrowing
     shipped in 1.2.0 (2026-10-12; pi #10353, PR #10672), so no released pi narrows with such a key
     (`q3-factcheck.md`).
   - The picker shows the unnarrowed catalog with one line saying why. The key keeps working for
     model calls as today (ADR-0203, ADR-0250 guard 1).
   - The details, the owner's choice of 2026-10-17: stored and exported keys narrow at pi's triggers
     (§4.6, §3.1) with no 4-hour interval, with one persisted entry per provider
     (`remote-catalog-provider.ts:187-194`; §4.5). `/logout` deletes that provider's stored list, a
     divergence from pi, which keeps it (`packages/ai/src/models.ts:825-834`). That a changed key
     never sees the previous key's list is this ADR's own rule from rounds 1-3 (§4.5).
   - Accepted: the documented `.env` setup (`README.md:361-372`, `.env.example:121-122`) gets no
     narrowing; every trigger costs one `/models/user` request when a key is stored or exported.
4. **A publication may change where a typed model string goes: release-equivalent routing, pi's
   overlay** (2026-10-17, after a detailed explanation: a measured table, and the destination,
   billing and takeover cases; r6's option A; §4.4). Option B's mark and filter are removed.
   - The pi fact: an overlay row carries no origin mark (`remote-catalog-provider.ts:32-36`,
     `:47-50`), and every resolver path reads the merged pool. PR #10913 made `provider/id` a hint
     that a keyed gateway answers when the named provider has no match
     (`packages/coding-agent/src/core/model-resolver.ts:223-260`). pi has no rule that prefers
     shipped rows or holds new ones back (`q4-factcheck.md`).
   - Each revision's summary lists the new rows that can move a typed string (§4.2 step 6).
   - Accepted:
     - the rollback's reach as §6.1 states it, pi's, chosen on 2026-10-17 and, after r8's review, on
       2026-10-19: pi's check timing, stated exactly, and pi's resume, which moves a session whose
       model a hold removed to the default model (§4.4, which applies pi's rule for a default too and
       keeps the open session's model across `/reload`); any other missing id keeps #376's restore;
     - the moves §4.4 measured, two gateways' `--model` following catalog order included (§6.2);
     - a forged revision can move a typed string between the user's own configured providers, as a
       release could (§4.2).

## 8. What the implementation owes

- **#172**:
  - the contract test in aelix-ai's CI;
  - in the publish job, the pin, additivity, no-transport, client-parse, inheritance, lineage-head
    and additions-cap checks, each made to fail once on purpose;
  - the derived maps (§7 Q2) as tests: each rule and their order, a derived `max` kept, no
    `anthropic-messages` row derived, and r7's counts reproduced from the probe's inputs;
  - the canonical digest and lineage over `main`'s history, with `r5/scripts/lineage_probe.py`'s
    pairs as a test (no older facts installed as newer, nothing withheld at or after the build's
    version, a dev edit in no lineage);
  - the ladder's audit unchanged after the move;
  - a dry run of both jobs against a local static server and a scratch catalog repository: a held
    slot publishes nothing, `release-hold` resumes, a rollback's index carries `hold` and survives
    the next slot, `publish` runs after a skipped `build`, an unchanged slot commits nothing, a push
    onto a moved branch is refused, and an artifact naming another path is refused;
  - after the owner creates the repository: the first real publication, then GETs of `v1/index.json`
    and its revision recording Pages' headers, the sizes and the sha256 check, and a rollback
    dispatched while a slot runs, with the next slot queued behind it (`queue: max`);
  - the fold-back step in `RELEASING.md`, run once.
- **#434**, tests:
  - of the store: atomic write, `0o600`, the sidecar lock across processes with `LOCK_NB` and its
    bounded retry, a failed write publishing nothing in memory, a persist losing to a newer stored
    entry, an unknown version left untouched and applying nothing, a corrupted file read as empty;
  - of the hosted source: TTL, `failedAt`, timestamps in the future, 304, the validator sent only
    when the stored revisions verify, 404 (the overlay kept until the next start), a withdrawn index
    (removed at once), a rollback older than the build (no overlay), version selection at `0.1.0b2`,
    the lineage both ways, a sha256 mismatch and a non-2xx revision GET (the stored index kept), a
    failure on another entry's revision (this build's entry still installed), the streaming cap, the
    row, additions and regression bounds, the floors, transport fields ignored, case-insensitive
    duplicates, unknown providers, a new id with no inheritance dropped, the loopback `http` rule,
    and `removed` (an index with `hold` adds the ids no revision names, one without adds none, an id
    named again leaves, the cap);
  - of a shared store: a release and a dev checkout of a newer `main`, and two versions, alternating
    on one agent dir, where neither empties the other's overlay;
  - of the known-id merge for every optional field (an absent map removes the shipped one, an
    invalid field keeps it, `family` and `releaseDate` are no facts), and of the ladder's inputs (a
    new id's `family`, its siblings' `family` and `releaseDate`);
  - of the effective catalog: Copilot's enablement on shipped rows, the id quoted, guard 2 on the
    effective rows, the reset fixture; and round 1's probe, where a request authenticated before a
    hosted publication still sends;
  - `r4/scripts/proto_r4_routing.py`'s narrowing comparisons, r5's and r7's release-equivalent cases
    (`proto_r5_q4.py` state A, `proto_r7_q4a.py`), r8's rollback reach (`restore_after_rollback.py`)
    and r10's `fallback_candidates.py`: a resume on a removed id falls back with §4.4's line, and a
    typed custom id and an id a release dropped still restore; a settings default, then
    `OPENROUTER_DEFAULT_MODEL`, naming a removed id is skipped on a resume, a launch and `/new` for
    `DEFAULT_MODEL_PER_PROVIDER`'s pick (never `Using <the removed id>`), and a shipped default is
    kept after a withdrawal; `/reload` on a removed id keeps it, and `/resume`, `/fork`, `/import`
    and RPC `switch_session` fall back;
  - one sweep for the skip (§4.4): every call of `resolve_route` and `find_initial_model`,
    enumerated by the test from the code (`r11/scripts/callers.py`), never from a list, so a new
    one fails it until it is driven, lands on no removed id when the settings pair, then
    `OPENROUTER_DEFAULT_MODEL`, names one; and r10's review probe (`agents_use_drop.py`):
    `/agents use` dropping a route back to that baseline sets the next candidate, never the
    removed id;
  - of the coordinator (a shared run, the last waiter aborting, supersession at every await, Python
    3.11), and of the event.
- **#434**, live, per CLAUDE.md rules 9 and 10, on a `0.1.0b2` checkout (the index's first entry
  admits it) whose catalog is `main`'s:
  - `uv run aelix` with a start-up refresh against the real URL, or against a loopback server
    serving a revision built from the lane's tree;
  - the `/model` and `/scoped-models` pickers re-rendering, and `/model openrouter/<new id>` waiting
    and matching;
  - a request on a hosted new id succeeding;
  - `aelix update --models`, and `--list-models` from the cache;
  - `AELIX_OFFLINE=1` and an unreachable URL;
  - a rollback served from the loopback server, then `/reload` in a session on the id it removed,
    and a resume of it with that id as the settings default;
  - RPC start-up at depth 0, and none at depth 1.
- **#450**: its issue's scope and acceptance rewritten first, then the authenticated `/models/user`
  (§4.5); live narrowing in the picker, refetched at each trigger; `/logout` deleting the entry and
  a key change clearing the list; a key only a cwd `.env` supplies narrowing nothing, with the
  picker's line; RPC `set_model` of a narrowed id.
- Windows: `os.replace` of the store while another process reads it, and the in-process lock.
- Independent review and a cross-review (CLAUDE.md rule 8; an independent Claude while Codex is out,
  the owner's 2026-09-24 rule). This ADR decides what a published file can put into a model row and
  what may move a typed string.

## 9. History

- 2026-10-14, rounds 1-3 (`.omc/specs/adr-0264-review-r1.json` to `-r3.json`): an OpenRouter-only
  design whose store, lock, backoff, caps, principal and generation rules stay in §4.
- 2026-10-15, r4-r6 (`-r4.json`, `-r5.json`): rewritten on the owner's decision (§2) on `e24ea2e8`;
  the lineage, the hold, the `0.1.0b2` floor, Q4 measured. Rebased on `aa207239`.
- 2026-10-17, r7-r8: the owner's decisions recorded (§7), their dates corrected after r7's review;
  option B removed; pi's `/scoped-models` trigger; the rollback's reach (§6.1) and the narrowing's
  details (§7 Q3); the provider rule only hides levels, a derived `max` kept after #433, pi's
  `b45e0eb..99fe148` with its kimi-coding incident (§3.1). Rebased on `bc83e0bc`.
- 2026-10-19, r9-r10: the owner's choices of 2026-10-19 recorded, pi's check timing (§6.1) and a
  resume's fallback (§4.4: `hold`, `removed`); 3 of the 34 rows send a level name (§6.2); the
  changed-key rule is this ADR's (§7 Q3). After r9's review: the fallback skips a removed default
  and `/reload` keeps the open session's model (§4.4); pi's compat check compiles, runs nothing
  (§3.1); V4-Pro offers off and high (§6.2). Rebased on `c02517ca`.
- 2026-10-20, r11: after r10's review, the removed-default skip sits where the pair is read into a
  resolution, `resolve_route`, `/agents use` dropping a route included, with one §8 sweep; the line
  with no candidate; a resume with no default resolves none, as a launch does (§4.4).
- 2026-10-20: accepted by the owner as a whole (draft PR #505); §8 is now what the implementation
  owes rather than a condition for acceptance.

## 10. Amendments

### 2026-10-21: #172's B4 - the format, the parser and the publisher, as measured

B4 (§4.8) landed `aelix_ai/catalog_inherit.py`, `aelix_ai/hosted_catalog.py`,
`scripts/publish_catalog.py`, the derived maps in `scripts/refresh_catalog.py`,
`tests/test_hosted_catalog_contract.py` with `tests/fixtures/hosted_catalog/` (`v1-sample.json`,
`v1-index-sample.json`, `r7-inputs.json.gz`), ADR-0232's amendment and the fold-back in
`RELEASING.md`. The catalog repository's workflow and `control/` are prepared outside this
repository for the owner, who creates `handochan/aelix-catalog` (§7 Q1). Evidence:
`.omc/probes/b4-d-live/172/` (commands in `commands.txt`). Run against the shipped catalog, the
rules of §4.3 and §4.2 as written refused shipped data in four places, so this records what the
code does instead:

1. **A `thinkingLevelMap` value is `null` or 1-32 characters of `[A-Za-z0-9_-]`**, not `[a-z0-9_-]`.
   MEASURED: 8 shipped Google rows (`google`, `google-vertex`, `opencode` Gemini 3.x and Gemma 4)
   send Gemini's own `thinkingLevel` values `HIGH`, `LOW` and `MINIMAL`, 16 values the lowercase
   rule refused; every revision would have failed step 3. Wire values are case-sensitive, so the
   rule takes both cases and nothing more.
2. **A known row's field that fails validation passes the publisher when the client keeps the same
   value.** `openrouter/openrouter/auto` ships `cost.input` and `cost.output` of -1,000,000,
   OpenRouter's "price varies" sentinel, outside 0 to 10,000. §4.3 already keeps the shipped value
   for such a field, so every client shows exactly what ships; step 3's client-parse check now
   compares each row's effective facts with what the generator wrote and fails only where they
   differ, and the summary lists the fields clients keep. A NEW row with such a value is still
   dropped by the client and so still fails the run, loudly, and so does a `family` or
   `releaseDate` the parser rejects on any row, known ones included.
3. **The clamp makes two shipped rows differ.** `maxTokens` is clamped to `contextWindow` (§4.3), and
   two shipped OpenRouter rows carry upstream's own incoherent caps (`nex-agi/deepseek-v3.1-nex-n1`
   163,840 in 131,072; `openai/gpt-3.5-turbo-0613` 4,096 in 4,095; pinned since ADR-0232). A
   revision of the shipped catalog therefore reproduces every shipped row except those two caps,
   which the contract test pins by name; neither nears the regression bound.
4. **A shipped id is matched exactly before any case rule**, so a case variant never shadows it.
   Rows are sorted by id and `ACME-1` sorts before `acme-1`: a parser that marks ids seen ignoring
   case, as the first draft did, dropped the shipped `acme-1` as "listed twice" behind a variant,
   and a forged row could freeze a known id's facts that way. The frozen sample holds that case.

Also decided while implementing, within the ADR's rules:

- `lineage` collapses consecutive versions with the same digest (a commit that touches the file
  without changing what it says), and a revision whose `lineage` is not 1 to 500 digests of 32 hex
  characters is not schema 1's shape and is refused whole. A shallow clone is refused: its history
  would cut the lineage silently.
- A publication is "unchanged" (step 4) only when the index names this revision for this floor and
  carries no `hold`; the first publication after a `release-hold` writes the index again without
  it.
- `publish_catalog.py build` stops as `held` when the catalog checkout has `control/hold.json`, as
  well as the workflow's own first step, so a build started by hand cannot walk past a hold.
- The plan runs over the catalog as committed at the source commit, and a checkout whose file
  differs from it is refused; the applied file is restored after the tests unless
  `--leave-applied`.
- The index parser requires the protocol fields (`minimumAelixVersion`, `revision`, `hold`) and
  checks the build metadata's types where present, as pi's parser returns only its protocol fields.

**The first publication meets the additions cap.** MEASURED live on 2026-10-21 23:52 KST: against
`main`'s catalog (`27fa21c5`, 1,529 rows) the plan adds 288 ids, over step 3's 100, so the first
run fails `additions-cap`. It needs either the fold-back committed on `main` first (then the first
revision adds what models.dev listed since) or one dispatch with `max_additions` of at least 288.
With the cap raised, the build passes every check, the pin, overlay and contract tests included on
the applied checkout: 1,817 rows, 460,765 bytes.

**Where §8's #172 list stands.** Done in B4: the contract test in CI; each publisher check made to
fail once (pin, additivity, no-transport, client-parse, inheritance, lineage-head, additions-cap,
caps, providers, hold); the derived maps' rules, order, a kept `max`, no `anthropic-messages` map
and r7's counts reproduced; the digest and lineage with r5's pairs over a synthetic `main` (a merge
and a no-op commit included); the ladder's audit unchanged. The dry run of both jobs ran locally
(`probes/dry_run.py`): every workflow step replayed with the workflow's own commands against a
scratch bare repository and a loopback static server standing in for Pages, each served revision
fetched back, checked against its name and parsed over the shipped catalog. A held slot publishes
nothing, and an artifact built before the hold is not published; `release-hold` resumes; a
rollback's index carries `hold` and survives the next slot; an unchanged slot commits nothing; a
push onto a moved branch is refused and nothing is deployed; an artifact naming another path, a
second revision, a hash mismatch, an index naming another revision or an index carrying `hold` is
refused; `control/` is never served. NOT run: GitHub Actions itself, so `publish` running after a
skipped `build` is the workflow's `if:` as written, not measured; actionlint 1.7.12 (with
shellcheck) passes the workflow except `concurrency.queue`, which it does not know and GitHub's
documentation (r6) does. Waiting for the owner: the repository, its first real publication and
§8's GETs of Pages' headers. The fold-back runs once in R's prep.

### 2026-10-26: #507 - a shipped row's `off` comes from the evidence a new row's map comes from

§7 Q2 derives maps for new rows only, and a shipped row's map is a key that exists. A shipped
reasoning row whose map has no `off` sends off as `effort: "none"` through both Responses adapters
(`openai-responses`, `openai-codex-responses`; Copilot excepted) and Chat Completions' OpenRouter
thinking format (`model.thinkingLevelMap?.off ?? "none"`), and a model that takes no `none`
answers 400. MEASURED on `0dee85d0` (`.omc/probes/507/live-before/`):
`--thinking off` on OpenRouter's `openai/gpt-5-mini`, `openai/gpt-5-nano`, `openai/gpt-oss-20b`,
`google/gemini-2.5-pro`, `z-ai/glm-5.3-flash` and `openai/gpt-6-astra` answered "Reasoning is
mandatory for this endpoint and cannot be disabled." `off: null` omits the field, as pi's maps do
for such models (pi.dev's published rows, 2026-10-26).

1. **The fold-back writes `off: null` into a shipped map that has no `off`, where the evidence says
   so.** For each shipped reasoning row on one of those wires, `plan_off_fills` asks §7 Q2's rules,
   in their order, for the map a new row would get - OpenRouter's `reasoning.mandatory` for an
   OpenRouter row, models.dev's effort list without `none` for a row that sends the level's name -
   and takes only its `off`. `--apply` writes it; `--apply --off-only` writes it without the
   additions. A key that does not exist, so the generator stays additive, and a shipped map's other
   levels stay `main`'s curation. A shipped `off` string the evidence contradicts is reported for a
   hand correction (RELEASING.md) and never overwritten.
2. **Two evidence-only lookups** for providers models.dev does not name as aelix does:
   `azure-openai-responses` reads models.dev's `azure`, then `openai`; `cloudflare-ai-gateway`
   reads the gateway's `openai/<id>` (the prefix pi strips), then `openai`. They add no row
   (`UPSTREAM_IDS` is unchanged). This build has no `azure-openai-responses` adapter (ADR-0254), so
   its fills change no request today; they are what its wire would need. pi's Azure rows carry no
   map and send `none`: a divergence (ADR-0235).
3. **The publish job reports and does not apply.** Its summary lists the fills and conflicts; a
   shipped row publishes as `main` has it (§4.3), so a fill reaches clients through a commit, as
   the fold-back's additions do. Additivity (§4.2 step 3) is unchanged.

MEASURED, models.dev and OpenRouter fetched 2026-10-26 12:50 UTC (sha256 in
`tests/fixtures/hosted_catalog/off-507-inputs.json.gz`): 91 fills - 72 OpenRouter rows marked
mandatory and 19 direct rows (`openai`'s o1, o1-pro, o3, o3-mini, o3-pro, o4-mini and
gpt-realtime-2.1; the same six o-series on `azure-openai-responses`; o1, o3, o3-mini, o3-pro and
o4-mini on `cloudflare-ai-gateway`; `opencode/gpt-5.3-codex-spark`) - and one conflict:
`openai/gpt-6-astra`'s `"off": "none"`, copied by hand from `gpt-5.5` on 2026-09-08, corrected to
`null` (pi: "GPT-6 Astra and GPT-6.1 Sol reject reasoning.effort "none"", `generate-models.ts:1080`
at `b84d5d1bc`). Committed by `--apply --off-only`; `tests/test_reasoning_off_507.py` takes the fills
back out, gets them proposed again and the file back byte for byte, and a plan over the shipped
catalog proposes nothing.

Left alone: 72 rows that still send `none` for off - the two deep-research rows on `openai` and on
`azure-openai-responses` (no evidence), 54 OpenRouter rows the public list no longer carries or
lists without a `reasoning` object, and the 14 `openai-codex` rows. models.dev has no Codex
provider; its `azure` and `opencode` list `gpt-5.1-codex-max`, `gpt-5.1-codex-mini` and
`gpt-5.2-codex` without `none`, but the ChatGPT backend is another service and is unmeasured, so no
lookup is added for it.

Found by the independent review (2026-10-26, `.omc/probes/507/review-claude/`), MEASURED and kept:

- **A fitted off lands on `minimal`.** Only `off` is filled, and `clamp_thinking_level` moves an
  unsupported off up to the next level the map offers, `minimal` on every changed row. A launch with
  no level and a resumed or carried off session therefore send `minimal`, which a model may refuse
  too. The evidence says so for 53 of the 92 changed rows (19 direct, every direct o-series row
  among them), and says nothing past `mandatory` for 26 OpenRouter fills, OpenRouter's o-series
  (`openai/o1`, `o3`, `o3-mini`, `o3-pro`, `o4-mini`) among them. MEASURED: OpenRouter's
  `openai/o3` with no `--thinking` answered 400 "Unsupported value: 'minimal' is not supported
  with the 'o3-2025-04-16' model" (it answered "Reasoning is mandatory" on `0dee85d0`), while
  `--thinking off` answered. pi's map for that row hides only `off` too. Fitting off to the lowest
  level a model takes, which the owner decided for #397 on 2026-10-21, is #397's.
- **OpenRouter's `~…-latest` aliases are filled as pi fills them.** Their public rows say
  `mandatory: true`, and pi.dev's own maps hide their `off`, yet all eight shipped aliases answered
  `effort: "none"` with a 200 (`.omc/probes/507/alias-probe/`; five with no reasoning tokens, the
  Grok and two GLM aliases with 1 to 49), as `~google/gemini-flash-latest` did at off through aelix
  on `0dee85d0` (`live-after/README.md`), while `google/gemini-3.8-flash`, where that alias routed,
  refuses it, as does `x-ai/grok-4.5` (the Grok alias routed to `grok-4.7`, not probed). Off is
  no longer offered on them and costs the model's default reasoning. Kept, as the owner's
  2026-10-21 decision on #397 follows pi; a follow-up if aliases should diverge.
- The rows the picker offers no `off` on went from 118 to 210 (`thinking_picker.py`).

### 2026-10-26: #397 - under a hidden `off`, the levels a model refuses are hidden too

A row that hides `off` sends a fitted `off` (a launch with no level, a resumed or carried `off`,
`/agents use`, RPC `set_model`; ADR-0239 decision 2) as the lowest level its map offers, and a map
that names nothing below `high` offers `minimal`. MEASURED on `2cbc126b` (`.omc/probes/397/live/`):
OpenRouter's `openai/o3` with no `--thinking` answered 400 "Unsupported value: 'minimal' is not
supported with the 'o3-2025-04-16' model", and so did RPC mode's first prompt (`get_state`:
`minimal`).

1. **`plan_floor_fills` writes `null` for each level below the lowest one the judging API's
   effort list names, on shipped rows that hide `off`, where the map has no key for it.** Only
   rows that hide `off`: elsewhere `off` is the floor. Only the levels below the floor: pi's
   derived maps hide every level a list leaves out, which here stays `main`'s curation (§7 Q2).
   Reported for a hand correction and never written: a level under the floor that the map names
   with a string, and a floor at `xhigh` or `max` the map does not name (those levels exist only
   where a map names them, so hiding the rest would leave none). The plan runs over the shipped
   rows after their off fills, so a row whose `off` the same run hides gets its floor in that
   run; a row the run adds gets it at the next fold-back. `--apply` and `--apply --off-only`
   write it; the publish job reports it and applies nothing (§4.3), in the off fills' section.
2. **Whose list counts.** A Responses row's models.dev row (§10's #507 lookups), else the same
   id under models.dev's `openai`, `azure`, `opencode` (`FLOOR_FALLBACK_IDS`): a Responses row is
   an OpenAI model, and models.dev's `openai` lists nothing for `gpt-5-codex`, `gpt-5.1-codex*`
   and `gpt-5.2-codex` while its `azure` and `opencode` list them from `low` (pi #1334: OpenAI
   refused `minimal` on `gpt-5.3-codex`, its body naming `gpt-5.2`). GitHub Copilot is in: its
   adapters skip only the off branch, so a fitted `off` reaches them as a level's name (pi maps
   Copilot's `gpt-5` `minimal` to `low` instead, `generate-models.ts:1090` at `b84d5d1bc`, the
   rule §6.2 left out; the wire is the same `low`). For OpenRouter, only an `openai/` row whose public list names no
   efforts, read from models.dev's `openai` (`OPENROUTER_PASSTHROUGH`). OpenRouter's own lists
   are not read, because MEASURED with `effort: "minimal"`
   (`.omc/probes/397/minimal-2026-10-26.jsonl`) 61 of the 64 live rows #507 filled answered 200,
   among them the 32 whose list leaves `minimal` out; only `openai/o3`, `o3-mini` and `o3-pro`
   refused it, with OpenAI's own error body. A 200 does not show which effort ran there, and pi
   reads those lists (pi.dev's OpenRouter `openai/o3`, with no list, hides only `off`, so the
   five OpenRouter rows here go past pi).

MEASURED: 95 levels on 83 rows over `2cbc126b`'s catalog (`minimal` 83, `low` 10, `medium` 2;
`tests/fixtures/hosted_catalog/floor-397-fills.json`): `azure-openai-responses` 23, `openai` 17,
`opencode` 16, `cloudflare-ai-gateway` 12, `github-copilot` 10 and OpenRouter's `openai/o1`,
`o3`, `o3-mini`, `o3-pro`, `o4-mini`. Committed by `--apply --off-only`;
`tests/test_off_floor_397.py` takes the floor back out, gets it proposed again and the file back
byte for byte, and a plan over the shipped catalog proposes nothing. After it, OpenRouter's
`openai/o3` with no `--thinking` answered, RPC `get_state` said `low` and the first prompt
answered, and the `/thinking` picker lists `low`, `medium`, `high`. Unmeasured: OpenAI's own API
(no credits), Azure (no adapter), Cloudflare, opencode and Copilot.

Not covered: the `gpt-5.1-chat-latest` and `gpt-5.2-chat-latest` rows on `openai` and
`azure-openai-responses` hide `off` and no source the rule reads lists their efforts, so a
fitted `off` still lands on `minimal` there, as before; models.dev's `302ai` (and `orcarouter`
for `gpt-5.2-chat-latest`) list only `medium`, so `minimal` likely fails there (unmeasured). An explicit `off` is not fitted at launch
(ADR-0239's #397 amendment).

### 2026-10-27: #172 - the fold-back, run once, and what its review corrected by hand

§8 owed "the fold-back step in `RELEASING.md`, run once", before the first publication: measured
on 2026-10-21, that publication would have added 288 ids against `main`, over the 100 cap. This is
that run.

Inputs: models.dev's `api.json` and OpenRouter's `/api/v1/models`, fetched once at
2026-10-27T00:08:01Z (sha256 `14d7540b…` and `80d8f47c…`) and fed to `--source` /
`--openrouter-source`, so the plan is reproducible. The plan over `0447024d`: **348 ids to add,
409 skipped, no `off` or floor fill on a shipped row** (#507 and #397 had already run). After
`--apply`, a second dry run proposes nothing. The added rows' maps leave no `~` or `!` line
either. Compared row by row against `0447024d` (not with `git diff`, which re-anchors this
file):
- 0 rows removed and 0 moved.
- 2 shipped rows changed by hand (Copilot, below).
- 1529 → 1877 rows over the same 35 providers.

The review measured the rows it could reach with the keys at hand: Anthropic's API, a Copilot
seat (zero cost) and OpenRouter. It compared every added row with three sources:
- pi.dev's published row for the same provider and id (288 of 348 are there);
- the request this build's adapters build from it;
- Copilot's own `GET /models`, which gives each model's policy and `supported_endpoints`.

Two independent reviews (Codex, and a fresh-context Claude) found more. Corrected by hand in the
same commit, each pinned in `tests/test_catalog_corrections_are_pinned.py`:

- **Copilot rows that Copilot serves on `/responses` only now ride `openai-responses`:**
  `grok-4.6`, `grok-4.7` and `mai-code-1.1-flash`. In Copilot's listing they were the only rows
  whose `api` disagreed with their endpoints. On `/chat/completions`, `grok-4.7` and
  `mai-code-1.1-flash` answered 400 *"not accessible via the /chat/completions endpoint"*.
  On `/responses`, all three answered (`grok-4.7` also at `minimal`, `low` and `high`). Two of
  the three were shipped rows: `grok-4.6` and `mai-code-1.1-flash`.
- **Claude Haiku 5.5 is adaptive** on its five `anthropic-messages` rows (compat
  `forceAdaptiveThinking: true`). `supports_adaptive_thinking`'s family fallback names opus,
  sonnet and fable, so `anthropic/claude-haiku-5-5` at `high` was sent a budget and answered 400
  *"thinking.type.enabled is not supported for this model"*. With the flag it answered with no
  level, at `minimal`, `low`, `high`, `xhigh` and `max`, and Copilot's at `high` and `max`. Its
  map names `xhigh` and `max`, which an absent key would not offer.
- **Claude Opus and Sonnet 5.5 hide `off`** on their nine `anthropic-messages` rows
  (`{off: null, minimal: null, low … max}`). They refuse thinking off. With no level,
  `anthropic/claude-opus-5-5` answered 400 *'"thinking.type.disabled" is not supported'*.
  `anthropic/claude-sonnet-5-5` and `github-copilot/claude-sonnet-5.5` answered 400 *'To turn
  thinking off on this model, send "thinking": {"type": "between_tools"}'*: a shape this build
  does not send. With `off` hidden: no level, `--thinking off`, `low`, `high` and `max` all
  answered, and `/thinking` lists `low` to `max`. pi.dev hides `off` on three of the nine rows:
  the `anthropic` Opus and Sonnet rows and Copilot's Opus. It offers `off` on the other six
  (Copilot's Sonnet, opencode's two, Vercel's three), presumably because pi now sends
  `between_tools`. The measurement decides here.
- **Claude 5.5's 14 `anthropic-messages` rows take no temperature** (`supportsTemperature:
  false`), as pi.dev's rows and the shipped `claude-opus-5` rows do. Unmeasured: the adapter
  sends a temperature only when a caller supplies one and thinking is off.
- **MiniMax M3.1 Flash (`minimax`, `minimax-cn`) is adaptive.** pi.dev's rows carry
  `forceAdaptiveThinking` and hide `off` and `minimal`, and models.dev lists `low` to `max` with
  no toggle. The shipped M3 and M2.x rows are budget rows in both sources. Unmeasured: no MiniMax
  key.
- **Nine opencode rows ride `openai-responses`:** `grok-4.5` to `4.7`, `grok-build-0.1`, the
  `muse` rows and their `-contributor-free` twins. models.dev's per-model `provider.npm` is
  `@ai-sdk/openai` for all nine, and on opencode 74 of the 75 shipped rows follow `npm`. Every
  shipped `@ai-sdk/openai` row there is `openai-responses`, and pi.dev agrees for the seven it
  lists. The nine had inherited `openai-completions` from the provider's family-less rows. Their
  derived maps already equal pi.dev's; `grok-build-0.1`, with no effort list, takes pi.dev's map.
  Unmeasured: no opencode key.
- **`opencode-go/claude-haiku-5-5` rides `anthropic-messages` at `https://opencode.ai/zen/go`.**
  It had inherited a qwen sibling's `openai-completions` transport with `thinkingFormat: "qwen"`.
  The fix follows pi.dev and opencode-go's one shipped `anthropic-messages` row. It is kept
  rather than dropped, because the generator, and the publish job, would propose a dropped id
  again with the same transport. Unmeasured.
- **Four Gemini rows hide `off` and `minimal`:** `google/gemini-pro-latest`,
  `google/gemini-4-argon`, `google-vertex/gemini-4-argon` and `opencode/gemini-4-argon`.
  - With `off` offered, the Google adapters send `thinkingBudget: 0` for a launch with no level.
  - models.dev lists effort `low`/`medium`/`high` with no toggle, and pi.dev hides both levels on
    all four.
  - `gemini-pro-latest`'s canonical row, `gemini-3.1-pro-preview`, carries the shipped hand map
    that hides them.

  Unmeasured: no Google key.
- **`deepseek/deepseek-flash` (V4.1 Flash 1017 by its canonical id) takes the Flash map**
  `{minimal: null, low, medium: null, high, max}`. It had none, so it offered `minimal` and
  `medium`, which DeepSeek does not list. Unmeasured.
- **The nine `eu.*` Bedrock rows the fold-back added are served from `eu-central-1`**, as the 10
  shipped `eu.*` rows and pi.dev say. Five had inherited `us-east-1`. They are inert: this build
  has no Bedrock adapter (ADR-0266 decision 6).
- **ADR-0266 decision 5's rules on the 24 added rows they cover.**
  - Claude 4.7+ and 5.x `{xhigh, max}` on 20 Bedrock rows.
  - The DeepSeek V4 map on four V4.1 Flash rows that came with none: the Flash map on `opencode`
    (2) and `opencode-go`, where models.dev lists `low`, and the V4 map on `nvidia`, as on its
    shipped V4 rows.
  - The rule is for a row "that has no map". The two OpenRouter V4.1 Flash rows and the
    Cloudflare one keep the maps derived from OpenRouter's `supported_efforts`
    (`[max, high, low, none]`) and models.dev's effort list.

  ADR-0266's #172 amendment records the test.

Measured working as committed (one short prompt each; `.omc/probes/172/live/`, not in the tree):
- **Copilot:** `claude-haiku-5.5`, `claude-sonnet-5.5`, `gpt-6-luna` (default, `off`, `high`),
  `grok-4.6`, `grok-4.7` and `mai-code-1.1-flash`.
- **Anthropic:** `claude-haiku-5-5`, `-sonnet-5-5` and `-opus-5-5`. On the committed maps all
  three answered at `xhigh` and `max`, which reach the request as adaptive
  `output_config.effort: "xhigh"` / `"max"`. Copilot's `claude-sonnet-5.5` answered at `max`.
- **OpenRouter:** 17 runs over 9 added rows covering every map source.
  `deepseek/deepseek-v4.1-flash-1017` answered at `max`, which its kept map offers.
- **OpenRouter `inception/mercury-2.5`:** with `--thinking off` it ran a bash tool call and
  returned its unguessable output. pi's Mercury 2 rule hides `off` because instant mode
  disables tool calling; that does not hold for 2.5 today, so the derived map stays.

Copilot `gemini-4-argon` answered 400 `model_not_supported`. Copilot's `/models` lists it on
`/chat/completions`, the transport it has, with its policy `disabled` for this seat, and `/login`
enables policies, so that is not a catalog fault.

**Routing.** OpenRouter now lists `fireworks/ember-1`, so `fireworks` became an OpenRouter
namespace. ADR-0249: 10 of the 35 catalogued providers overlap, was 9.
- **A catalogued Fireworks id** (`fireworks/accounts/fireworks/models/…`, 20 of them, none listed
  on OpenRouter) now goes to OpenRouter for a user who holds an OpenRouter key of their own and no
  Fireworks key of their own. On `0447024d` it stayed on `fireworks`.
  - "Their own" means exported, `/login` or `models.json`. A cwd `.env` key authenticates a route
    but never chooses one (ADR-0250 guard 1), so an exported OpenRouter key plus a `.env`
    Fireworks key now lands on OpenRouter.
  - A Fireworks key of their own, `--api-key`, or `--provider fireworks` keeps the id on Fireworks.
- **An id OpenRouter lists and Fireworks' catalog does not** (`fireworks/ember-1`) goes to
  OpenRouter even with a Fireworks key of the user's own, and with no key at all. Only
  `--provider fireworks`, or a Fireworks key of the user's own with no OpenRouter key of their
  own, keeps it on Fireworks. On `0447024d` it went to `fireworks`, which could not resolve it.
- MEASURED, `--model fireworks/ember-1` with only an OpenRouter key: on `0447024d` it went to the
  `fireworks` provider; now it goes to OpenRouter, which answered 402 for that key's credit. The
  other cases were resolved offline by the round-2 verifier with `resolve_route` and fake keys.

ADR-0249's note records it.

What this run shows the generator does not do. Each fold-back's review does these by hand until
it does, and the first publication and #434 should take them into account:
- It does not apply ADR-0266's family rules to a new row (decision 9: it writes the derived map
  only). The test catches that on `main`, but the publish job runs the same generator.
- It derives no map for an `anthropic-messages` row: it reads models.dev's effort list only
  where the wire sends the level's name. A new Claude or MiniMax generation's `off`, `xhigh`,
  `max` and adaptive flag are therefore hand work. pi's generator carries them as family rules
  (`generate-models.ts` at `fdce48eb4`: `:1142-1181` for Claude's `xhigh`/`max`, Fable's `off`,
  the adaptive flag at `:1160` and temperature; per-model overrides for MiniMax M3.1 Flash at
  `:2414-2434` and for Claude 5.5's `off` at `:2827-2848`).
- It inherits the first transport a provider used for a family (ADR-0232's residual risk) and
  does not read the evidence that now exists:
  - models.dev's per-model `provider.npm` or `provider.api`, present on 34 providers on
    2026-10-27 (`scripts/refresh_catalog.py` and `catalog_inherit.py` said `api.json` has no
    transport at all, and now say what it has);
  - Copilot's `/models` for Copilot rows.

Left as inherited and unmeasured (no key). Each follows the same provider's shipped rows:
- 18 rows whose `api` differs from pi.dev's:
  - opencode `qwen3.8-max`, as the shipped `qwen3-coder`;
  - opencode-go `gemini-4-argon`, `gpt-6-luna` and `grok-4.7`. opencode-go's shipped rows
    disagree with `npm` on 8 of 34, among them `gpt-5.6-luna` and the `muse` rows;
  - `xai/grok-4.7`;
  - xiaomi's `mimo-v2.6` on its Anthropic endpoints;
  - OpenRouter's Claude 5.5 rows and `google/gemini-nano-banana-2.1` (pi.dev:
    `openrouter-images`) on OpenRouter's OpenAI endpoint.
- `openai/gpt-6-luna`, where pi.dev names a wire, `openai-decisions`, that this build lacks.
- The `zen` → `inference` base URLs pi.dev now uses for opencode, which every shipped opencode
  row shares.
- The three `google-vertex` Claude 5.5 `@default` rows on the Gemini transport. All 14 shipped
  Vertex Claude rows are on it too, and pi.dev has none. They offer `off` and `minimal` and would
  send `thinkingBudget: 0` at `off`. The whole provider needs Vertex configuration to run at
  all, and ADR-0266 left these ids out (Not applied).
- `cloudflare-workers-ai/@cf/deepseek-ai/deepseek-v4.1-flash-1017` keeps the map models.dev's
  `[none, low, high, max]` derives (`off: "none"`, `low` offered). Its two shipped V4 siblings
  carry decision 5's map although models.dev lists the same efforts for them, and pi.dev gives
  this row that map plus the DeepSeek format. So at `off` it sends `reasoning_effort: "none"`
  where they send none. Unmeasured: no Cloudflare credentials.

Follow-ups found on shipped rows and left to their own issues:
- opencode-go: the transports `npm` disagrees with, and `thinkingFormat: "qwen"` on its non-Qwen
  rows.
- Moonshot `kimi-k3`, whose `supportsReasoningEffort: false` the new K3.1 rows inherited (pi.dev:
  `true`).
- `nvidia`'s DeepSeek V4 rows, which send no effort (ADR-0266 decision 6; pi.dev: the DeepSeek
  format).
- Six Vercel realtime or live-audio rows on `anthropic-messages`.
- Large `maxTokens` caps that OpenRouter's credit check refuses for a small balance.
- Four shipped OpenRouter DeepSeek V4 rows (`-v4-pro-0813`, `-v4-flash-0731`,
  `~deepseek-v4-flash-latest`, `-v4-flash-vision-exp`). OpenRouter lists `[max, high, low, none]`
  for them, and pi.dev now carries those derived maps. They keep decision 5's
  `{xhigh: xhigh, max: null}` and send an `xhigh` OpenRouter does not list for them.

### 2026-10-27: #434 - the client: the store, the source, the triggers, as built and measured

§4.3-§4.7 as one change on `2fea1de8` (the fold-back's merge). Evidence:
`.omc/probes/434/` (the loopback catalog `tools/serve_catalog.py`, which builds revisions from the
checkout the way the publisher does; the pty driver `tools/tui_drive.py`; `live/` for every
screen and log below; `integration-map-e0fd2b96.md`).

**What landed, where §4.7 put it.**
- aelix-ai: `models_store.py` (`ModelsStore`, `InMemoryModelsStore`, `RefreshModelsContext`,
  `ModelsPublication`, `ModelsRefreshResult`, `SourceOutcome`, `AbortSignal`); in `models.py`,
  `install_overlay`, `get_shipped_model(s)`, `shipped_catalog` and `shipped_digest` (at the end of
  the module); `models_generated.SHIPPED_CATALOG` and `model_from_row`.
- coding-agent: `models_store.FileModelsStore`; `hosted_catalog_source.py` (the cache phase,
  `load_cached`, and the network phase, `refresh_hosted`); `model_refresh.py` (the refresher and
  the event binding); in `model_registry.py`, `refresh_models`, the publication, `is_removed` and
  the recomposition; the triggers; `cli/update_command.py`.
- agent-core: `model_catalog_refresh` (`ModelCatalogRefreshHookEvent`), in the pi-parity fixture's
  new `aelix_only_event_names` bucket; `ReloadSeed.model`.

**Decided while implementing, within the rules above.**
1. **The store entry keeps the index document as published** (`index`, re-validated by
   `parse_index` at every read) rather than its `catalogs` alone, so `hold` is there when
   `removed` is updated; each revision is stored as its exact text, so the sha256 re-check at
   every start holds; `removed` is `{provider: [[id, addedAt], ...]}`. A 404 stores an empty index
   marked `unavailable`.
2. **`ModelsStore.write` compares, then writes** (`base`: the stamp of the entry the run started
   from) and returns the newer stored entry when it lost, `{}` when that entry was deleted, so
   rule 2's "the stored entry is published instead" is one call under the lock. The outcome a run
   reports is then the newer entry's, not its own.
3. **A check skipped within the 4 hours adopts the stored entry into memory** (nothing is
   written), except after a 404. Another aelix on the same agent dir may have stored a newer
   index (`aelix update --models`, a second session); without this an open TUI took a hold only
   when its own 4 hours ran out. MEASURED (`live/L5a-*`): a hold stored by another process
   reached the open TUI at its next `/model`, whose list lost the withdrawn id.
4. **The removed-default skip sits in `resolve_route` and reads a recorded settings seed.** The
   launch records the pair it seeds into `parsed` (`record_settings_seed`); `resolve_route` skips
   a removed id those strings name, and step 0's variable, before any other step, while the seed
   is active (`parsed` holds the settings pair, not a profile's or a typed model: review rounds 1
   and 2 below). The callers that resolve `parsed`'s pair (11 of the 21 resolution calls: the
   launch, the factory, the late-route hold, `/agents use`) need not pass anything, and
   `tests/cli/test_withdrawn_default_sweep_434.py` classifies all 21 from the code (a new one
   fails it). `ResolvedRoute.withdrawn` carries the removed pair, and with no session to restore
   the line is `Default model <p>/<id> was withdrawn from the published catalog. Using …`.
5. **`/scoped-models` does not re-render its rows when the run lands**, where §4.6 says "as the
   `/model` picker". Its save reads the rows it opened with (#153's carry-forward and collapse
   rules): a row arriving mid-selection is unticked in the picker's state, so with no scoping a
   plain confirm would save an explicit list without it. It shows one status line with how many
   models the new list adds, and a reopen shows them (MEASURED, `live/L3-*`). The `/model` picker
   re-renders in place (MEASURED, `live/L2-*`: 4 rows, then 5, filter and cursor kept, "Model
   catalogs refreshed").
6. **`/model <term>` refreshes on two refusals**: nothing matched, and #136's backfill (an id the
   registry does not hold under a provider named with a key). The live check found the second:
   `/model openrouter/<new id>` switched to a custom id without asking the catalog (`live/L1-*`
   first run). Now it says "Refreshing model catalogs…", and the run published the row it then
   matched.
7. **`/login` runs the hosted source unforced in the background; `/logout` runs nothing.** The
   forced key-scoped source and the narrowing entry's deletion are #450's.
8. **Cancellation is a task cancel plus `AbortSignal`.** The refresher shares one run per
   registry; a forced trigger supersedes an unforced run; a waiter that leaves (cancelled or at
   15 s) leaves the run to the others, and the last one aborts it. The event is emitted by a
   registry listener that reads the live harness at each emit, so a hot-swap needs no rebind.
9. **#172's note of 2026-10-22**: `RevisionRefused`'s per-provider message quotes the published
   key (`_quoted`), and `ignored_providers` is counted, never printed.
10. **`aelix update` is the fourth root command**; `aelix update -h` prints its usage and exits 0.
    `--list-models` names the cached revision on stderr.
11. **`ModelRegistry.create()` puts the store beside `models.json`**
    (`<agent dir>/models-store.json` for the default path), so a test or an embedder that passes
    its own `models.json` gets its own store.

**Measured live** (`uv run aelix` from the lane, a loopback catalog built from its tree, real
OpenRouter):
- a start-up refresh whose index answered after 20 s ended at 15 s and published nothing; the
  real URL's start-up stored the 404 as `unavailable` (the repository does not exist yet);
- `/model openrouter/openai/gpt-6.1-terra-pro` (an id this build does not ship): "Refreshing model
  catalogs…", the index and the revision fetched, `model → openai/gpt-6.1-terra-pro (openrouter)`,
  and a request on it answered `pong`;
- `aelix update --models`: `published`, then `unchanged` on a 304, `unavailable` on the real URL,
  `failed: index: ConnectError` (exit 1) on an unreachable one, exit 1 offline with no request,
  exit 2 bare; `--list-models` from the cache with no request;
- a rollback (hold) stored by another process, then `/reload` in the session on the id it
  removed: the next request was answered by that id (the session record); a resume of it, with
  that id as the settings default, said `Could not restore model openrouter/openai/gpt-6.1-terra-pro
  (withdrawn from the published catalog). Using anthropic/claude-opus-4-7` (both keys held:
  Anthropic's default comes first in `DEFAULT_MODEL_PER_PROVIDER`);
- RPC at depth 0 fetched the index and the revision; at depth 1 nothing; `AELIX_OFFLINE=1` made
  no request.

**Not in this change.** #450's narrowing (§4.5), so r4's narrowing comparisons are #450's; the
catalog repository and its first publication (§8's #172 list), which wait for the owner, and so
Pages' headers. No test runs on Windows a reader during `os.replace` or the in-process lock
alone (the cross-process lock test is POSIX-only); CI's Windows legs run the rest of these tests,
and no Windows host was used.

**Review round 1** (Codex cross-review and an independent Claude review of the first version;
`.omc/probes/434/review-codex/`, `review-claude/`). Fixed, each with a test that fails on the
first version:
- **The settings seed carries its provenance.** Equality with the recorded pair was not enough:
  a launch through `--agent` left the removed default in the `/agents use` baseline unrecorded
  (`--none` and the `/new` after went back to the removed id), and a profile that named the same
  pair was skipped as if it were the default. The launch records the pair active only when
  `parsed` takes it, and `/agents use` sets it to whether the profile named its own route
  (restored on rollback). The provider is canonicalized (case, a `<provider>/` prefix) before
  the removed check. Item 4 above now reads with this.
- **A running process adopts a stored entry as a running process** (`adopt_entry`): a newer
  entry from another URL changes nothing, a 404's keeps the overlay, and a missing or refused
  revision keeps it; the start-up cache phase still installs nothing in those cases. Losing the
  persist to another aelix's 404 had removed the live overlay.
- **Each registry tracks the revision it composed over**, so a second registry recomposes over
  an overlay another one installed.
- **A URL or redirect with userinfo is refused**: httpx turns `user:pass@` into a Basic
  `Authorization` header whatever `auth=` says.
- **A 304 goes through the revision fetch too**, so a revision the stored index names and the
  store lacks is fetched on a 304 (§4.3 step 3), not only on a 200.
- **A hold whose other revision failed keeps its candidates pending** (`pendingRemoved`) until a
  check has every revision the index names; the first version dropped the old revisions and
  with them the removal.
- **Both locks share one 2-second deadline** (the in-process wait counted on top of the file
  lock's).
- **The event's `removed` counts** the published ids a publication took out of the effective
  catalog.
- **The `/model` picker keeps its own rows** and keeps the cursor on the same model across a
  renumbering refresh (a test drives the real `select` and its key bindings); an exception in
  the landing never reaches the picker.
- **With `--api-key`, a withdrawn default falls back only on its own provider**, else there is no
  model: the key is attached to the route's provider after resolution (ADR-0250 §2.7), and the
  first version handed an OpenRouter-typed key to Anthropic's default (independent review).
- **Every failure after the fetch goes through the failed check** (with its `failedAt`): a
  revision whose bytes hash to their name but are not UTF-8 is a failed fetch of that revision,
  and a deeply nested revision that the parser cannot read is a failure, not an exception. The
  first version stored nothing and refetched at every trigger.
- **No fetch against a store this build cannot write**: a store of an unknown version is not
  fetched for (`skipped`, `failed` when forced), and after a failed write the source backs off
  for 15 minutes in the process, as `failedAt` would. The first version re-downloaded the
  revision at every trigger.
- **The cache phase never stops a launch** on a stored entry it cannot read; a `{}` file is an
  empty store, rewritten on the next write.
- **Nothing says "Refreshing model catalogs…" with the source off** (`AELIX_MODEL_CATALOG_URL=`),
  and `aelix update --models --offline` takes `--offline` after the command, as `aelix extension`
  does.
- Tests: a run stopped (aborted or superseded) during its persist publishes nothing in memory;
  an aborted context publishes nothing (the first version's test never reached publication); the
  registry's first check refuses an aborted run; a trigger waits at most its bound and the last
  waiter aborts the run; a forced trigger supersedes an unforced run; teardown cancels a running
  refresh before it publishes; `/agents use` with an `--agent` launch and with a profile naming
  the removed pair (both through `_async_main`).
- Left as they are: the lock wait (at most 2 s) runs on the event loop's thread; the TUI and RPC
  triggers are bounded, so it is bounded too.

**Review round 2** (a fresh verifier of round 1, `.omc/probes/434/verify-r1.md`: 17 of 19
findings FIXED, 2 PARTIAL, 3 new should-fix). Fixed, each with a test that fails when reverted:
- **A provider-only profile keeps the seed active**: the seed is active while `parsed`'s model
  string is the default's, not only while no profile named a route; round 1 had marked it
  inactive and landed `/agents use <provider-only profile>` (and `/new` after) on the removed id.
  A rolled-back use restores the flag (now tested), and the canonical provider and the
  `<provider>/` strip are tested.
- **With `--api-key`, the withdrawn default's provider's own rows** are the fallback pool, with
  no credential test: the key is attached after resolution, so round 1's pool was empty and the
  launch had no model.
- **The write backoff stops the network only**: the entry another aelix stored is still adopted
  (round 1 skipped item 3's adoption for 15 minutes after a failed write).
- A 404 keeps `pendingRemoved`; `aelix update --offline` alone prints the usage; a refused
  `AELIX_MODEL_CATALOG_URL` warns once; SECURITY.md says the 15-minute retry; the guide says the
  `--api-key` rule.
- Not tested, as the verifier found: `/fork`, `/import` and `/new` on a removed id beyond
  `switch_session` and the reviewers' probes (the behaviour measured fine). Per the two-round
  cap, nothing further was reviewed.

### 2026-10-27: #609 - an opt-in switch: check only when the user asks

The owner's choice of 2026-10-27, after #434 merged: keep pi's triggers (§4.6, the owner's choice
of 2026-10-19) as the default, and add an opt-in mode where only `aelix update --models` checks.

- **The switch** is a global setting, `checkModelCatalog` (default on), shown in `/settings` as
  "Check model catalogs". Its getter reads the GLOBAL cell only, as `respectGitignore`'s does, so
  a project `.aelix/settings.json` can neither turn it off nor on and the row shows what it
  changes. A setting rather than an environment name, for the reason `checkForUpdates` gives: no
  new variable a cwd `.env` could try to drive.
- **What it gates**: every automatic trigger of §4.6 (TUI and RPC start-up, the `/model` and
  `/scoped-models` pickers, a `/model` miss, `/login`) asks `model_refresh.auto_refresh_allowed`
  off the session's settings manager when it fires, so a flip applies from the next trigger.
  `aelix update --models` asks `refresh_allowed` alone, and the cache phase installs the stored
  revision either way. With no settings manager (an embedder) it reads as on.
- **The price, written in the guide and SECURITY.md**: nothing checks between commands, so a hold
  (a rollback or a withdrawal) reaches a machine with the switch off only when the user runs
  `aelix update --models`, and new models appear only then. A session already open sees what the
  command stored only after a restart: with the switch on, the next picker check adopts another
  process's store even inside the 4 hours, but with it off nothing in the session reads the store
  (review round 1 measured it; reading the store on picker open without a request is left out).
- The README's list of the requests aelix makes for itself now names the model catalog check,
  which #434 had left out.

### 2026-10-28: #172 - the catalog repository, its first publication and §8's last checks

The owner's instructions of 2026-10-27: create the repository and publish once, then run the
rollback check. These are §8's last #172 items. Evidence: `.omc/probes/172/first-publication/` and
`.omc/probes/172/rollback-check/`.

- **The repository.** `handochan/aelix-catalog` (public) holds B4's prepared workflow,
  `tools/publish_job.py`, `control/` and a README (first commit `e12cde8`). Pages builds from
  GitHub Actions (`build_type=workflow`, the `github-pages` environment). GitHub accepted the
  workflow, `concurrency.queue: max` included, and its weekday schedule is on.
- **The first publication** (dispatched 2026-10-27T15:31Z, run 40531811294): build and publish
  succeeded, and commit `b31d048` published `sha256-1e01c1c0…7faa`, built from aelix-ai `2fea1de8`
  (the fold-back, then the newest `main` whose `ci.yml` push run had passed). Its entry: floor
  `0.1.0b2`, 1878 rows, one more than the fold-back's 1877 (`openrouter/openai/gpt-6.1-terra-pro`).
- **Pages, as served** (§8's GETs): `v1/index.json` (510 bytes) and the revision (483,615 bytes)
  answer 200 with `content-type: application/json; charset=utf-8`, `cache-control: max-age=600`,
  an `ETag` and a `Last-Modified`. The revision's sha256 is its name, and `If-None-Match` on the
  index answers 304, so §4.3's validators exist on Pages and its 304 path runs there.
- **The client** (`main` `3cec8c0c`, a scratch agent dir, the real URL): `aelix update --models`
  published the revision, with one model this release does not ship, and a second run was
  `unchanged`. Over the shipped catalog the revision changed 2 rows and dropped none, and
  its lineage admits the build. A `-p` request on the new id answered `pong`.
- **A rollback dispatched while a slot runs, with the next slot queued behind it** (runs
  40539401207 to 40540123469, 2026-10-27 16:17-16:22Z). The target was the only deployed revision,
  so the rollback could remove no id.

  | run | action | dispatched | result |
  | --- | --- | --- | --- |
  | A 40539401207 | publish | first | `unchanged` |
  | R 40539435075 | rollback | while A ran | waited for A; commit `5e826a1`, the index with `hold` |
  | B 40539471268 | publish | while R ran | waited for R; `unchanged` |
  | C 40539913547 | publish | after R | stopped at the build's hold check |
  | D 40540027747 | release-hold | after C | commit `a497281`, `control/hold.json` gone, nothing deployed |
  | E 40540123469 | publish | after D | commit `02e4821`, the index again without `hold` |

  - Each run waited for the one before it, and none was cancelled. At most one run was pending at
    any moment: R's jobs started when A ended (16:17:34Z), and B was dispatched at 16:17:37Z.
    GitHub's default concurrency with `cancel-in-progress: false` does the same, so `queue: max`,
    which §4.2 has from GitHub's documentation only, was not exercised.
  - `publish` ran after a skipped `build` (R and D), which B4's dry run could only read from the
    workflow's `if:` (§10, 2026-10-21).
  - After R, the served index carried `hold: {action: rollback, at: …}`, and `control/hold.json`
    carried the revision, the reason and the run's URL.
  - After E, the client said `unchanged`.
- **What it found.**
  - **A queued slot reads the hold at its dispatch commit.** B's `build` job checks out the run's
    own commit (`b31d048`, before R's hold commit). Its "Stop while a hold exists" therefore saw no
    hold, and it built.
    - The guard that matters is the `publish` job's re-read of `control/hold.json` at the branch
      head. B ended `unchanged`, so that job never ran with a revision to publish.
    - A slot queued behind a rollback therefore reaches the publish job instead of stopping first.
      Checking out the default branch in `build` too would stop it at the first step.
    - Filed as #616, together with the publish job's missing tests, `_commit`'s `control`
      pathspec, the `result.json` that `build --fetch` leaves unwritten when a fetch fails, and
      three mutants no test pins.
  - **The floor policy is still open.** The summary check parses with the latest release's parser,
    v0.1.0-beta.2's. It drops 128 rows, every one for `thinkingLevelMap: level 'max'`. The build
    log shows only its last 40 lines, 31 of them drops; the 128 is `release_report` run again over
    the published revision with v0.1.0-beta.2's catalog and levels.
    - The published revision keeps those rows. Beta.2 has no catalog client, so nothing is lost.
    - §4.2 says only when the floor rises. What the publisher does once a client in the wild lacks
      a level is undecided: #617 (the owner decided it on 2026-10-30, below).
- **Not measured.**
  - The weekday schedule had not fired by the time of writing; its first slot is 2026-10-28 10:17
    KST.
  - Pages' cache lifetime behind `max-age=600`.
  - Two runs pending at once, the case `queue: max` exists for. It needs a rollback and a slot both
    dispatched while one build runs, which takes about 18 s.

With this, §8's #172 list is done, except that `queue: max` itself was not exercised (above; it
was on 2026-10-31, #616 below).

### 2026-10-30: #617 - a thinking level an older client lacks raises no floor

The owner's choice of 2026-10-30 was #617's option (c). When a published map names a thinking level
that an older client's parser does not know, the publisher does nothing different:

- It does not raise `MINIMUM_AELIX_VERSION` (option a).
- It does not publish an entry per floor (option b).

The client's own parser decides row by row, as §4.3 says it does. Evidence: `.omc/probes/617/`.

- **What such a client does** (READ: `hosted_catalog.py`, `_apply_known` and `_add_new`).
  - **A known id**, one the client ships, takes the published row's other facts. It keeps its
    shipped `thinkingLevelMap`, or none if it ships none, and this is noted in `kept`. A shipped
    map is not lost, so the row is no regression, and the bound of 25 can never refuse a revision
    for this.
    - What the client misses is `main`'s map for that row. Until it upgrades, no curated correction
      to that map reaches it, and neither does a #507 or #397 fill.
    - So §4.3's "corrections and curated maps committed to `main` reach every client" does not hold
      for such a map.
  - **A new id** whose map names the level is dropped and noted. That client offers the model only
    after an upgrade.
  - A map that names the level only as `null`, to hide it, is rejected the same way.
  - `_add_new` is the only place that drops a row for its map. So the 128 rows that the summary
    check dropped for v0.1.0-beta.2 on `sha256-1e01c1c0…` (§10 2026-10-28) were all ids new to
    beta.2.
- **MEASURED** (`without_max_r2.py`, `without_max_r2.out`).
  - This checkout's parser at `f3e76fbb` read the published revision `sha256-ee8eaf4c…` three times,
    each time with `max` taken out of its levels.
  - The revision has 1,885 rows. 261 of them name `max`, 36 of those only as `null`, and 8 ids are
    new to this checkout.

  | Catalog it read against | Known rows naming `max`: map kept | New rows naming `max`: dropped | New ids added | Regressions |
  | --- | --- | --- | --- | --- |
  | this checkout's | 259 of 259 | 2 of 2 | 6 | 0 |
  | this checkout's, with `max` taken out of its maps | 259 of 259 | 2 of 2 | 6 | 0 |
  | v0.1.0-beta.2's | 132 of 132 (71 of them ship no map, so they keep none) | 129 of 129 | 227 | 0 |

  - **`name`, one of the other facts, applies.** In the first reading, every known row naming `max`
    got a marked `name`, and every one of them took it. A marked value is needed because, at this
    source commit, the published facts equal the shipped ones. That the other facts apply too rests
    on reading `_apply_known`'s loop.
  - **The revision would install.** The lineage admits this checkout's catalog and beta.2's
    (`lineage_admits`), so the revision would install over either. The second catalog is made up,
    and no lineage holds its digest.
  - **None of these is a real older build with a client.** No such build exists.
- **Why (c).**
  - No client in use lacks a level the maps name. v0.1.0-beta.2 has no catalog client, and every
    build with one has #433's levels.
  - Option (a) would freeze an older client at the last revision published under its floor. The next
    index keeps the other floors' entries (`publish_catalog.py`, `next_index`), so every correction
    and every new id after that revision stops reaching the client, not only the rows that name the
    level.
  - Option (b) would have the publisher build and check one revision per floor in every slot, with
    tests of its own, for no client that needs it today.
- **What stays.** The floor still rises when published values need client behaviour that older
  clients lack (§4.2).
- **What the summary did not show** (until #616 the same day, below).
  - The publish summary's release check (`release_report`, `publish_catalog.py`) printed the rows it
    would drop. It never printed the fields it would keep.
  - For beta.2 on this revision it printed "rows it would drop: 129", and nothing for the 132 known
    rows that kept their shipped map, or none.
  - The summary's list of the fields every client keeps parses with `main`'s levels, so it does not
    show them either.
  - A level that only known rows name therefore never reached the release check. New ids that name
    it reached the check only as drops. So the summary was no reliable signal for when to look again.
  - Printing the kept maps there was a code change, not made in #617.
- **When to look again.** Look again in either of two cases:
  - when `EXTENDED_THINKING_LEVELS` (`aelix_ai/models.py`) gains a level after a release that ships
    the client. The commit that adds the level is the moment: a new row's map is derived at
    publication, so the next slot can already publish a map that names it.
  - when the new ids such a release drops matter to its users.
- No behaviour changes. `MINIMUM_AELIX_VERSION`'s docstring in `scripts/publish_catalog.py` now says
  this beside the constant.

### 2026-10-30: #616 - the publishing pipeline: a queued run stops at its hold check, a failed fetch says why, the release check lists the fields it does not take

#616 listed five gaps §10 2026-10-28's checks found. On 2026-10-30 the owner added a sixth, the
release check's missing kept fields that #617's review found (above), and asked for all six. None
had published anything wrong. The work spans two repositories: the catalog repository's `20b0464`
(handochan/aelix-catalog#1, fast-forwarded onto its `main` on 2026-10-31), and this commit.
Evidence: `.omc/probes/616/`. The review was two independent passes, Codex and a fresh-context
Claude (`codex/`, `claude-r1/`); their findings are fixed or recorded below.

- **A run queued behind a rollback stops at its hold check** (item 1, the catalog repository).
  - `build` now checks the catalog repository out at `${{ github.event.repository.default_branch }}`,
    as `publish` already did. So its "Stop while a hold exists", the step after that checkout, reads
    the hold that the rollback it waited behind wrote. Before, it read the hold at the run's own
    commit, as run B did on 2026-10-27 (§10 2026-10-28).
  - `build` also reads the current index and `tools/publish_job.py` there now, as `publish` does.
  - MEASURED on 2026-10-31 (`item1/`), on the catalog repository's `main` at `20b0464`, in the
    same sequence as §10 2026-10-28's, on a weekend with no slot:

    | run | action | dispatched | result |
    | --- | --- | --- | --- |
    | A 41149657245 | publish | first | commit `3e6e225`, `sha256-993702e2…` (1,887 rows, from `e19ab55e`) |
    | R 41149693586 | rollback to `sha256-97bc9107…` | while A ran | waited for A; commit `e78dfab`, the index with `hold` |
    | B1 41149729986 | publish | while R waited | queued at `3e6e225`; ran after R, stopped at the build's hold check |
    | B2 41149771903 | publish | while B1 ran | queued at `e78dfab`; stopped at the build's hold check |
    | D 41149876080 | release-hold | after B2 | commit `81db75b`, `control/hold.json` gone, nothing deployed |
    | E 41149928208 | publish | after D | commit `0c77095`, `sha256-993702e2…` again, the index without `hold` |

    - B1 is the case #616 names. Its own commit, `3e6e225`, has no `control/hold.json`; R's
      `e78dfab` added it before B1's `build` began. That `build` checked out the branch head, and
      its hold check stopped it: every later `build` step and `publish` were skipped, and it
      committed nothing. At its own commit the step would have seen no hold and built, as B did on
      2026-10-27 (INFERRED: the file is absent at `3e6e225`).
    - **`queue: max` was exercised.** R and B1 were both pending from 00:21:33Z, when B1 was
      created, to 00:21:40Z, when A ended and R's jobs began. Neither was cancelled, where GitHub's
      documented default cancels R (§4.2). That is two pending runs, not the hundred `queue: max`
      allows, and the window is read from the runs' own timestamps (`item1/runs.json`), not from
      one read that showed both pending.
    - A published a new id, so R was a real rollback: from about 00:22Z to 00:24Z the served index
      named `sha256-97bc9107…`, one row fewer, with the hold. E published `sha256-993702e2…` again.
    - The results are read from the runs' step conclusions and the catalog repository's commits.
      The step summaries are not in the API: each build job's check-run output came back empty.
- **The publish job's own code has tests** (items 2 and 3, the catalog repository).
  - `tests/test_publish_job.py`: 17 tests in stdlib `unittest`, run with the runner's `python3`,
    which is what the job runs `tools/publish_job.py` with. They cover:
    - nine kinds of artifact the job refuses, and a symbolic link. Each changes nothing;
    - a rollback, which takes its entry from the index history, leaves another floor's entry where
      it is and holds. A good artifact is then not published, and `release-hold` lifts the hold;
    - a rollback to a revision two indexes named, which takes the newer entry;
    - a rollback to a revision no index named, or one that is not deployed, refused;
    - a withdrawal;
    - the pruning, which keeps every named revision and the newest 30;
    - a hold file that is not JSON, not an object, or `{}`, which still holds, and whose summary
      says so;
    - a hold added by hand while a build ran, which wins over its artifact;
    - `stage-site`, which copies `v1/` only;
    - both jobs checking this repository out at the default branch, read from the settings rather
      than the comments, and every later `build` step running only without a hold.
  - `.github/workflows/test.yml` runs them on a push to `main` or a pull request that changes
    `tools/`, `tests/` or a workflow. A publication's own push starts no workflow.
  - `_commit` named `control` to `git add` whether or not it existed. A checkout without `control/`
    therefore failed every commit with `pathspec 'control' did not match any files`. It now names
    only the paths that exist on disk or in the index, so a tracked directory gone from disk is
    committed as a removal.
  - `check-hold` printed "No hold." for a hold file of `{}` while it stopped the build, because an
    empty object is falsy. It now prints the hold.
  - MEASURED (`catalog-mutants/out-r1.txt`). With no mutant, all 17 pass on Python 3.12, and the
    round-2 review ran all 17 on 3.11 to 3.14. Each of 16 mutants of `publish_job.py` and `publish.yml`
    fails at least one of them. Among the 16 are the old `_commit`, `build` at the run's own commit,
    and that `ref:` commented out.
- **A failed fetch is a failed check** (item 4, `publish_catalog.py`'s `_fetch`).
  - Either fetch can fail in several ways. Any of the following now raises `BuildFailed`:
    - an `OSError`: no answer, a timeout, an HTTP error status;
    - an `http.client.HTTPException`: a body cut short;
    - a `ValueError`: not UTF-8, not JSON;
    - a `RecursionError`: JSON nested too deep for `json.loads`.
  - The failure reads `fetch: <url>: <type>: <text>`, with the text cut to 300 characters. So
    `result.json` says `failed` with that reason, and the summary lists it.
  - Any other exception no check expected still ends in a traceback with no `result.json`. So do the
    paths outside `--fetch`: an upstream file on disk, or an index or revision in `--catalog-repo`,
    that is not JSON.
  - MEASURED (`fetch-fail/`, `fetch-fail-before/`). `build --fetch` ran through a proxy that refuses
    connections.
    - At this tree it exited 1. `result.json` was `{"state": "failed", "failures": ["fetch:
      https://models.dev/api.json: URLError: <urlopen error [Errno 61] Connection refused>"]}`, and
      `summary.md` listed the same line.
    - At `c2390412` it exited 1 with a traceback and left `out/` empty.
    - The Claude review reproduced it through a proxy that answers `CONNECT` with 503, and no
      request reached models.dev (`claude-r1/fetch-503/`).
- **The release check lists the fields the release does not take** (item 6, `release_report`).
  - After the drops it prints "fields it would not take, keeping its own value or none: N", then
    one `kept:` line per field. A known row that ships no map keeps none, and a ladder input that
    fails validation is ignored, so "its own value" is not always a value.
  - Each list in the release check now shows at most 200 lines (`RELEASE_LISTED`), then "and N more".
    The count above each list stays whole. GitHub's documentation caps a step summary at 1 MiB, and
    Codex built an accepted revision whose kept lines alone came to 1.3 MB (not measured on GitHub).
    The summary's other sections are not capped.
  - MEASURED (`release-kept/`). A dry `build --fetch` at the first draft of this change, against the
    catalog repository at `7e9986f`, ended `unchanged` (`sha256-97bc9107…`, 1,886 rows). For
    v0.1.0-beta.2 it printed:
    - "rows it would drop: 129";
    - a kept count of 133. 132 of these are `thinkingLevelMap` for `max`, #617's 132 known rows (the
      Claude review checked set equality). The other is `openrouter/auto`'s cost, the one field
      every client keeps (§10 2026-10-21), which the list of fields every client keeps also names.
  - MEASURED again at this tree on 2026-10-31 (`release-kept-r1/`). Upstream had one more id, so the
    build ended `publish` (`sha256-7bf9ae44…`, 1,887 rows, written to the probe directory only). The
    release check printed the new label, "rows it would drop: 129" and a kept count of 133, and no
    list reached the cap. The summary was 29,619 bytes.
  - So a level that only known rows name now reaches the release check. #617's "When to look again"
    stands. The kept lines are one more place to see the case it names.
- **Cross round 1's three mutants are pinned** (item 5). No code under `packages/` changes: the
  three behaviours were right, and no test held them.
  - The three mutants are in `.omc/probes/b4-d-live/cross/scripts/`:
    - mD: the ladder reads a published `family` and `releaseDate` unvalidated. Tests now pin that a
      shipped row's `family` that fails the rule steers no new id. They also pin that a
      `releaseDate` that fails it does not make that row the newest sibling. The review found the
      second half unpinned.
    - mH: a hold's `at` is unchecked. A test now pins that an index hold whose `at` is not a string,
      or is missing, is invalid.
    - mO: the published `releaseDate` lacks the generator's `last_updated` fallback. A test now pins
      that `ladder_inputs_for` takes `last_updated` when `release_date` is missing, empty or null,
      as `catalog_inherit.released` does.
  - MEASURED (`mutants/out-r2.txt`). With no mutant, the three catalog test files pass on a copy:
    239 passed, 1 skipped (the skip is the history test, which needs a clone). Each of the following
    fails at least one test written for it:
    - the three mutants;
    - undoing item 4 or item 6;
    - the twelve the reviews added:
      - mD's `releaseDate` half alone, and `get`'s default for mO;
      - a kept count that is the drop count, or that counts rows;
      - kept lines for maps only;
      - no 300-character cut, `OSError` alone, no `RecursionError`;
      - no cap, an uncapped kept list, an uncapped transport list, and "and 0 more" at the cap;
    - a cap that miscounts its "more" line, the author's own.
  - The round-2 review also ran mD's `family` half alone, and it fails.
- **The schedule fired.** §10 2026-10-28 said the weekday schedule had not fired by the time of
  writing. On 2026-10-28, 10-29 and 10-30 all nine slots ran and succeeded, each created between 4
  and 8 minutes after its `:17`. Five of them published.
- **Not changed.** `queue: max` stays. Node 20's deprecation notices on the pinned Pages and
  artifact actions are notices only.
