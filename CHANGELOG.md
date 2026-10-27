# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The published distribution set is released in lock-step at a single shared
version: `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent`, and the `aelix`
umbrella meta-package. (`aelix-server`, the Web-UI daemon, is deferred to a
later release and is not part of this publish set.)

<!--
No comparison / tag links are defined at the bottom of this file. The reason
recorded here used to be "the repository has no tags and no releases yet", and
that expired on 2026-08-20 when `v0.1.0-beta.1` was tagged and published as a
GitHub pre-release: `.../releases/tag/v0.1.0-beta.1` and
`.../compare/v0.1.0-beta.1...HEAD` both resolve today. They are simply still
unwritten. Add them with the next release.
-->

## [Unreleased]

### Added

- **The published model catalog can be checked only when you ask** (#609).
  `/settings` → **Check model catalogs** off (the global setting
  `checkModelCatalog: false`) stops the automatic checks at start-up, in the
  model pickers, on a `/model` miss and after `/login`; `aelix update --models`
  still checks, and the stored catalog is still used. Default on, as before. A
  project `.aelix/settings.json` cannot change it. With it off, a withdrawn model
  reaches you only when you run the command, and a session already open only when
  you restart it. The README's list of requests aelix
  makes for itself now names the model catalog check (#434 had left it out).
- **Models published between releases reach you without a release** (#434,
  ADR-0264 §10 2026-10-27). aelix now reads its own published catalog,
  `https://handochan.github.io/aelix-catalog/v1/index.json`, as pi reads
  pi.dev: a model id the catalog adds for a provider this build ships becomes
  selectable and routes as a shipped one would, and a known id takes the facts
  `main` has for it. A published row never carries transport: every row keeps,
  or inherits from its provider's shipped rows, this build's API, base URL,
  headers and compatibility flags, so no published file can send a request
  anywhere a release does not. A revision is used only when it was built from a
  `main` that holds this build's catalog.
  - It checks at an interactive or RPC start-up (in the background, at most
    15 s, never in a delegated agent), when the `/model` or `/scoped-models`
    picker opens, when `/model <term>` matched nothing ("Refreshing model
    catalogs…", then it matches again), after `/login`, and at the new
    `aelix update --models` (forced; each outcome on stderr; exit 1 on a
    failure). Unforced, at most every 4 hours. The `/model` picker shows the
    cached list at once and re-renders when the check lands.
  - The cache is `~/.aelix/agent/models-store.json` (`0600`, written
    atomically under `models-store.json.lock`); `--list-models` and `-p` read
    it and never check. `--offline`, `AELIX_OFFLINE`/`PI_OFFLINE` or
    `AELIX_MODEL_CATALOG_URL=` turn it off; a project `.env` cannot set that
    variable.
  - A rollback or withdrawal reaches you at the next check: the models it
    took out leave the lists, a resume on one falls back with
    `Could not restore model … (withdrawn from the published catalog)`, and a
    settings default or `OPENROUTER_DEFAULT_MODEL` naming one counts as unset;
    `/reload` keeps the model the session is on, and a typed id still
    resolves as typed.
  - Extensions get `model_catalog_refresh`, an observational hook event and
    aelix's first one pi lacks.
  - Copilot's policy enablement at `/login` reads only this build's shipped
    rows, and quotes the id in the request path.
  - The catalog repository does not exist yet, so for now every check answers
    404 and nothing is overlaid.
- **348 models the catalog was missing**, folded back from models.dev and
  OpenRouter with `scripts/refresh_catalog.py --apply` (#172, ADR-0264 §10
  2026-10-27). That is 1529 → 1877 rows across the same 35 providers. Nothing
  was removed or moved; two shipped rows changed (below). Among them: Claude
  Opus, Sonnet and Haiku 5.5 on `anthropic`, `github-copilot`, OpenRouter and
  the gateways; `gpt-6-luna`, `gpt-6-sol`, `gpt-6.1-sol` and `gpt-6.1-terra`
  (openai, Copilot); and `grok-4.7`. The first publication of the hosted
  catalog (#434) now starts from them instead of 288 ids behind. The review
  measured what its keys reached (Anthropic, a Copilot seat, OpenRouter) and
  corrected these by hand:
  - Copilot's `grok-4.6`, `grok-4.7` and `mai-code-1.1-flash` use the
    Responses API, the only endpoint Copilot lists for them. On chat
    completions, `grok-4.7` and `mai-code-1.1-flash` answered 400. `grok-4.6`
    and `mai-code-1.1-flash` were already shipped that way.
  - Claude Haiku 5.5 thinks adaptively: given a thinking level, it answered
    400 "thinking.type.enabled is not supported" (measured at `high`). MiniMax
    M3.1 Flash gets the same flag (pi.dev's; unmeasured).
  - Claude Opus and Sonnet 5.5 no longer offer `off`, which they refuse. A
    launch with no level now starts at `low` instead of answering 400.
  - Nine opencode rows (Grok 4.5 to 4.7, Grok Build 0.1, the Muse models) use the Responses
    API, as models.dev's per-model package and opencode's other OpenAI-package
    rows say. Four Gemini rows no longer offer `off`, where it sent a thinking
    budget of 0. Both are unmeasured.
  - Smaller fixes: Bedrock's new `eu.*` rows use the EU region; one Claude row
    on opencode-go no longer rides a qwen transport; `deepseek-flash` offers
    DeepSeek's levels; Claude 5.5 sends no temperature. The rows ADR-0266's
    `max` rules cover got them.
- **`--model fireworks/<id>` can go to OpenRouter now** (ADR-0249's
  2026-10-27 note). OpenRouter lists `fireworks/ember-1` since the fold-back,
  which makes `fireworks/` an OpenRouter namespace. A catalogued Fireworks id
  (`fireworks/accounts/fireworks/models/…`, none of them on OpenRouter) now
  goes to OpenRouter when you hold an OpenRouter key of your own and no
  Fireworks key of your own. Before, it stayed on the `fireworks` provider.
  "Your own" means exported, `/login` or `models.json`; a key only in a
  project's `.env` never chooses a route (ADR-0250). A Fireworks key of your
  own, `--api-key` or `--provider fireworks` keeps it on Fireworks.
  `fireworks/ember-1`, which only OpenRouter lists, goes to OpenRouter unless
  you pass `--provider fireworks` or hold only a Fireworks key of your own.
- **RPC hosts: native runtime controls beside pi's roster (#593, #594, ADR-0275).**
  `--mode rpc` accepts `{"type": "aelix_control", "method": ..., "params": ...}`
  requests, answered with the usual correlated `response`. They cover extension
  commands, prompt-template and skill preparation, resources and reload, typed
  settings, permission posture and evaluation, the active tools, client MCP
  servers, provider auth, extension inventory, agent profiles and delegation, and
  branching. The 29 pi commands are unchanged. `capabilities` lists only what the
  host's services can serve, so the WebSocket server advertises fewer methods
  than the CLI. Mutations serialize and need an idle runtime. A classic prompt
  or session replacement waits out a running mutation, and the reverse.
  `commands/cancel` targets one operation and never cuts a harness replacement
  half-way. That covers a reload, a branch, and an extension command's own
  `ctx.new_session` / `fork` / `switch_session` / `reload`. A cancelled command
  stops once its replacement has finished. A mutation queued when stdin closes
  does not run. Dialogs (after
  `ui/enable`) exist only around `commands/run`, `agents/run` and `auth/login`,
  end on a reply, timeout, signal or shutdown, and do not approve tool calls. A
  project agent profile needs `--approve` or the RPC confirmation. A setting is
  reported saved only after it is written. Client MCP servers never widen an
  explicit tool selection. Verified with synthetic tests and a real-provider
  native RPC run. Aelix Studio, real-provider OAuth and Windows packaged installs
  are not yet verified.

- **Maintainers: the release can be dry-run before its tag (#535, ADR-0269).**
  `release.yml` now also runs by hand: `gh workflow run release.yml --ref main
  -f tag=v0.1.0-beta.3` builds the commit and runs `candidate-smoke` on
  Ubuntu, macOS and Windows, with the same tag gate and version/pin assert the
  tag push would run, and stops there. `github-release` and `publish` run for
  a tag push only (each says so in its own `if:`), so a manual run creates no
  Release and uploads nothing to PyPI. Until now the smoke's macOS and Windows
  legs would first have run on the beta.3 tag itself. RELEASING.md's *Cutting
  a beta* runs the dry run as step 5, before the tag. The smoke script's
  failure line also reaches a Windows leg's log now: a message quoting a
  character outside cp1252 ended in a `UnicodeEncodeError` traceback instead
  of the `::error::` line, because the step runs it with `-I`, which ignores
  `PYTHONIOENCODING`.

- **Maintainers: the model catalog can now be built for publication between
  releases (#172, ADR-0264, ADR-0232).** `scripts/publish_catalog.py build`
  fetches models.dev and OpenRouter's public model list, runs the generator's
  additive plan over the committed catalog, and writes a content-addressed
  revision (`sha256-<hex>.json`) and the next `index.json` that a scheduled job
  in a separate repository, `handochan/aelix-catalog`, will publish. A
  published row carries facts only (name, reasoning, thinking map, input,
  cost, window, output cap) plus the two fields the transport rule reads:
  `api`, `baseUrl`, `headers` and `compat` are never published, and a client
  takes them from its own shipped catalog. Nothing is published when a check
  fails: the pin test, the catalog tests and the new contract test run against
  the applied catalog; every committed row must be present and unchanged; no
  row may carry transport; every row must pass the client's parser
  (`aelix_ai.hosted_catalog`), with each new row inheriting, through the
  client's copy of the rule, the transport the generator wrote; at most 100 ids
  new per publication; and the revision's lineage (the digests of `main`'s
  catalog history) must end in the committed catalog's. The transport rule
  itself moved to `aelix_ai/catalog_inherit.py`, so the generator and the
  client run one implementation; its score is unchanged (95.3% forward over
  r7's inputs). `scripts/refresh_catalog.py` now gives a new reasoning row a
  thinking map derived as pi derives one (Xiaomi's and Together's off/high
  rule, OpenRouter's supported efforts, models.dev's effort list where the
  wire sends the level's name), or none; `--fetch` downloads OpenRouter's list
  too and `--apply` refuses without it. `RELEASING.md` gains the fold-back that
  commits published additions before each release.
  `tests/test_hosted_catalog_contract.py` runs the publisher and the parser
  together on every pull request, without network, with a frozen schema-1
  sample. Users see no change yet: the client that reads the hosted catalog is
  #434. Not done here: `handochan/aelix-catalog` itself (the owner creates it)
  and its first publication; measured on 2026-10-21, that one would add 288
  ids against `main`'s catalog, over the 100 cap, so it needs the fold-back
  first or a raised `max_additions` once.

- **Contributors: CI now fails when a package imports one above it (#292,
  ADR-0015).** The packages stack as `aelix-ai` <- `aelix-agent-core` <-
  `aelix-coding-agent` <- `aelix-server` and `aelix`, and a package may import
  only itself and the workspace packages its `pyproject.toml` declares. Nothing
  checked that until now, and it did not hold: the kernel imports
  `aelix_coding_agent` in 13 places (10 inside `AgentHarness` methods, 3 under
  `TYPE_CHECKING`), so `AgentHarness` cannot be built without the coding-agent
  package, and a new import like those passed ruff, the type gate and the
  structural tests.
  `tests/test_import_direction_292.py` runs `scripts/check_import_direction.py`
  on every leg. The check reads every import in each package's source with
  `ast`, including those inside functions and under typing's `TYPE_CHECKING`
  flag (a `TYPE_CHECKING` the file binds any other way counts as code that
  runs), and calls to `import_module`, `find_spec`, `resolve_name` and
  `__import__` - also through a name the file binds to one with a
  `from ... import ... as` alias or an assignment - whose module is a string
  literal, a relative name resolved against a literal package. It fails on one
  the package does not declare and names the file, the line, the statement and
  the fix. The script's docstring lists the forms known not to be seen (a
  module name built at run time, `getattr` on `importlib`, an importer bound by
  tuple unpacking, `__import__` with a `level`, `importlib.resources.files` and
  `pkgutil.get_data`, `exec`, and the like). The existing 13 are listed by
  file, kind, module and name (not by line), with a count, and the tree must
  match the list exactly: a new import fails until it is listed, and a listed
  import that is gone fails until its entry is removed. The gate cannot stop an edit to the list itself; it makes
  one visible in review, and ADR-0015 allows an entry to be added only to
  follow a listed import that moved to another file or under `TYPE_CHECKING`.
  `pyproject.toml` declaring an upward dependency fails too. Making
  `AgentHarness` work without `aelix-coding-agent` is a follow-up. Users see no
  change.

- **Python 3.14 is supported (#278, ADR-0265).** `aelix-ai` now requires
  `openai>=2.7.2,<3` (was `>=1.66,<2.0`). Python 3.14 made `typing.Union[...]`
  slotted, and every openai before 2.7.2 assigns `__discriminator__` onto it
  whenever a streamed event fails its validation, so on 3.14 a real turn died
  with `'typing.Union' object has no attribute '__discriminator__'` (#262) while
  a one-line prompt still worked. Measured on 3.14.5 with a tool round trip
  against a local OpenAI-compatible server: the old pin exits 1 when the
  server's Responses events omit `sequence_number` and `logprobs`, as many
  compatible servers do, and exits 0 when they carry them; the new one exits 0
  either way, and on chat completions. CI's test matrix runs 3.14 on Ubuntu
  and Windows, and the packages list the 3.13 and 3.14 classifiers. The ceiling
  is `<3`: openai 3.0 replaced httpx with HTTPX2 and stopped installing
  `certifi`, which aelix's TLS handling is written against. What aelix sends
  and emits is what it was on openai 1.109.1, with two exceptions below:
  `tests/providers/test_openai_sdk_wire_278.py` holds every CI leg to 29
  scenarios recorded on 1.109.1 through the adapters' own client (text, tool
  calls, reasoning, prompt caching, HTTP 400 and 429, a refused connection,
  keyless calls, and the variables below exported). Three changes in openai
  2.x are undone. openai 2.34 refuses an empty key, so a
  keyless openai-completions call builds its client with a placeholder that
  never reaches the wire (the request's own `Authorization` replaces it, and
  without one the SDK's is omitted; an `Authorization` a payload hook sets in
  `extra_headers`, in any letter case, replaces either, so one line goes
  out). openai 2.34 also reads `OPENAI_CUSTOM_HEADERS`, whose lines replaced
  any openai-compatible provider's key with an exported `Authorization`, and
  `OPENAI_ADMIN_KEY`; aelix's client keeps neither, for every provider, as
  openai 1.x read neither, and leaves `os.environ` as it was; the shipped `examples/selfhosted` extension and the
  extension-authoring guide's snippet now build their client with it too
  (`create_async_client` takes an `http_client` for their private CA). And
  openai 2.52 waits up to 120 s for a `Retry-After` on its own
  retries and stops retrying above that (measured: a `Retry-After: 61` made
  `aelix -p` take 504 s instead of 20 s); aelix's client waits for one only up to
  the request's `max_retry_delay_ms` (pi's `maxRetryDelayMs`, 60 s when unset,
  which is 1.x's rule) and otherwise backs off and retries as 1.x did; a
  `Retry-After` of zero or less, a date in the past or `inf` is not a wait to
  honour, as on 1.x. The exceptions: a keyless openai-completions request where
  two different spellings of `Authorization` meet (`model.headers` against
  `options.headers`, or a payload hook's `extra_headers` against the
  request's own) sends one line, the later one - the `options.headers` value
  over the model's, the hook's over both - where 1.x sent both, and sends none
  when the hook's value is `openai.Omit()` under a spelling the request's own
  header does not use, where 1.x sent the request's own line; and a
  `max_retry_delay_ms` that an extension or embedder sets now moves that 60 s
  limit (1.x ignored it). Nothing in the CLI sets it until
  `retry.provider.maxRetryDelayMs` is read (#470), so there it is the default.

- **Configured authentication is checked before recording a prompt (#416).**
  TUI, RPC, print and json check after input hooks, accepting request-supported
  model/provider/registration headers, Anthropic tokens/custom headers and Vertex
  key/ADC configuration. Header-only sessions restore. Credential resolution,
  OAuth retry and ambient ADC discovery remain at request time.

- **System prompt files load automatically (#287).** `SYSTEM.md` replaces the
  generated base and `APPEND_SYSTEM.md` adds instructions, from a trusted
  project's `.aelix/` directory or the global agent directory. CLI flags retain
  precedence, session rebuilds re-read edits, and the startup banner shows
  loaded paths. See the getting-started guide for profile composition and trust.

- **A delegated agent's session is now recorded.** Every delegation — the
  `agent` tool (single, parallel or chain) and `/agents run` — now runs its
  child with a session file of its own, created beside the parent session in a
  directory named after it (`<parent>.jsonl` → `<parent>/sub-<id>.jsonl`), so
  the child's whole transcript outlives the run. Until now it existed nowhere
  once the delegation ended. The parent session keeps a receipt for each child:
  the profile, the model that actually ran, the child's file, how it ended (ok,
  error, timeout, aborted or cancelled) and what it spent. A delegation you
  cancel with Ctrl+C is recorded too, with the spend the child had reported by
  then — marked as a floor, because a request still in flight at the cancel may
  have been billed without ever reporting back. `--continue`, `/resume` and the
  session pickers never offer a child
  file. A parent without a session file (`--no-session`), or whose file is not
  inside a sessions directory's per-project folder (one you opened with
  `--session /some/where/file.jsonl`, say), still runs its children without
  one, and so does any child whose file could not be created (a disk error, a
  path too long). **Nothing deletes these files:** like sessions themselves
  they stay until you remove them — one per delegation, up to 12 per prompt
  through the `agent` tool and one per `/agents run` — and deleting a parent
  session by hand leaves its directory of children behind. (#199)
- **`aelix --export <child file>` is how you read a delegated agent's
  session**, as an HTML page, without running anything. Opening one with
  `--session` (or `--fork`) now prints a one-line warning instead of looking
  like a resume: the file does not carry what bounded the child — its
  permission clamp, its narrowed tools, `--no-agents` — so it would run as an
  ordinary session at your own posture. A viewer inside the TUI is #296. (#199)
- **`max` is a thinking level, above `xhigh` (#433, ADR-0266)**, as in pi
  since `fbdd46389`: `--thinking max`, `defaultThinkingLevel: "max"`, an agent
  profile's `thinking: max`, `/thinking` and a `models.json` `thinkingLevelMap`
  key; an `enabledModels` entry written `<id>:max` now scopes to that model
  (aelix reads the suffix only to strip it; before, the entry matched nothing
  and the scope widened to every model). A model offers it only where its
  catalog entry names it, like `xhigh`; elsewhere it runs at the model's top
  level.
  Before, `--thinking max` was dropped with a warning and a
  `defaultThinkingLevel: "max"` ran with thinking **off** (measured on
  `7faf4b9a` through a local recorder: `thinking: {"type": "disabled"}` on
  `anthropic/claude-opus-4-6`, `claude-opus-4-7` and `claude-sonnet-4-5`, and
  on `openrouter/deepseek/deepseek-v4-pro`), and `/thinking max` printed
  `thinking → max (off)`. It sends the model's own `max`: Claude's adaptive
  effort, OpenAI's on the `gpt-5.6` and `gpt-6` rows, DeepSeek V4's off
  OpenRouter, and GLM 5.2's on Fireworks and opencode-go - Z.AI's own GLM 5.2
  rows send only `enable_thinking`, so there `max` thinks as `high` does. On a
  Claude row that thinks with a token budget (only a `models.json` map can
  offer `max` there) it sends `xhigh`'s 32768 budget.

### Changed

- **A release is installed and run on Linux, macOS and Windows before anything
  is published (#382, ADR-0269).** `release.yml` used to create the GitHub
  Release and start the PyPI upload straight after the build, and no job had
  installed what they shipped; the installers' CI jobs install only releases
  already published, and `install.sh` / `install.ps1` without `AELIX_VERSION`
  take the newest Release, betas included. Now a new `candidate-smoke` job
  installs the build's own wheels on all three OSes with the installers' own
  command (`uv tool install --find-links <wheels> --python <request>
  'aelix[tui]==<version>'`, where `<request>` is what the installers ask for
  that version: `>=3.11,<3.15` from 0.1.0b3 on), on the CPython uv downloads
  for a user who has no Python in that range (the newest inside it), and runs
  them: the checksums in the form `install.sh` reads, the interpreter, every
  installed file against the wheel it came from, `.dist-info` included (so a
  same-version PyPI file cannot pass for the candidate), every package and the
  TUI importing, `aelix --version`, and one `aelix -p` turn against a local stub
  with a fake key. The Release waits for it, and the PyPI upload waits for the
  Release, so its approval comes with a window in which RELEASING.md has the
  maintainer run the installers with `AELIX_VERSION=<tag>` first. If the smoke
  fails, nothing was published and the version is still free.
- **`--mode json` exits 1 when the run's last answer failed, as `-p` does
  (#410, ADR-0268).** A JSON run whose provider kept answering `502` until the
  retries ran out, whose `/login` token refresh kept failing until the retries
  ran out, or whose last answer otherwise ended in an error or an abort exited
  0, so a script could not see the failure in `$?`; `-p` already exited 1.
  Both now use the same rule: the last message decides when this run wrote
  it, so with several prompts it is the last one's answer. The event stream
  on stdout is unchanged (measured equal, line for line, apart from ids and
  timestamps) and JSON mode still writes nothing to stderr for it: the failed
  answer's `turn_end` carries its `stop_reason` and `error_message`. Signal
  exit codes (143 for `SIGTERM`) are unchanged. pi's `--mode json` exits 0
  here; aelix diverges on purpose. One change in `-p` too: a session whose
  last answer failed in an earlier run, continued with no prompt or with a
  prompt an extension handles, now exits 0 and prints nothing, where it
  exited 1 and printed the old error (pi's `-p` still does; `--mode json`
  exited 0 for it before and still does). An answer cut off at the output
  token limit still prints and exits 0 in both modes. A delegated agent whose model failed now reports
  exit code 1 and ends as `error` in `/agents`, where it showed `done`. The
  getting-started guide has a new "Exit codes" section.
- **The 24 catalog rows that called their top tier `xhigh` now call it `max`
  - all but OpenRouter's DeepSeek V4 (#433, ADR-0266; the owner's decisions of
  2026-10-20 and 2026-10-21).** Claude Opus 4.6 on every provider (12 rows),
  DeepSeek V4 (8) and GLM 5.2 (4) mapped `xhigh` to the provider's `max`. 21
  of them now list `max` and no `xhigh`, as pi does, and send what they sent: a
  saved `xhigh` - a `defaultThinkingLevel`, a session's level, an agent
  profile's `thinking:` - moves up to `max` and sends `max`. The picker and the
  🧠 statusline say `max` where they said `xhigh (max)`; `--thinking xhigh` and
  `/thinking xhigh` there still show `xhigh (max)`. **OpenRouter's 3 DeepSeek
  V4 rows follow pi instead: a saved `xhigh` there now sends
  `reasoning_effort: "xhigh"`, where it sent `"max"`, and they offer no `max`**
  (OpenRouter answered DeepSeek's `max` with a 400, pi #4801). pi's other `max`
  rules apply to the rows aelix ships too (132 rows in all): Claude Opus 4.7
  and later, Sonnet 5 and Fable 5 offer both `xhigh` and `max` (rows that
  offered neither now send `xhigh` for `xhigh` instead of `high`), Sonnet 4.6
  offers `max` (so a saved `xhigh` there now runs at `max`, not `high`), the
  Fable mirrors no longer offer `off`, the `gpt-5.6`/`gpt-6` rows that offered
  `xhigh` offer `max`, and the 15 DeepSeek V4 rows that had no map get pi's
  (`minimal`, `low` and `medium` now run at `high` - `minimal` and `low` at
  `low` on the 4 Flash rows - and `xhigh` at `max`, or at `xhigh` on
  OpenRouter; nvidia's 4 send no effort at any level). A TUI session
  started at `defaultThinkingLevel: "xhigh"` records `xhigh` wherever that
  runs at `max`, and cycling the level (`/settings`, RPC
  `cycle_thinking_level`) from an `xhigh` the row does not list steps as the
  level it runs at - to `off`, where it went to `minimal`. The row list is in
  ADR-0266.

- **Keyless OpenAI clients that extensions and embedders build need a
  placeholder key (#278, ADR-0265) - a migration.** On openai 2.34 and later,
  which `openai>=2.7.2,<3` resolves to today, `AsyncOpenAI(api_key="")` (the
  extension-authoring guide's snippet before this change) and aelix's
  `create_async_client(api_key=None)` or `api_key=""` raise
  `openai.OpenAIError: Missing credentials ...` when the client is built; with
  `OPENAI_ADMIN_KEY` exported the client builds and every request fails before
  it is sent (`Could not resolve authentication method`). On openai 1.x both
  sent the request with no `Authorization`. Write
  `create_async_client(api_key=opts.api_key or "unused", ...)` instead: the
  endpoint then sees `Authorization: Bearer unused`. To send no
  `Authorization`, as before, add `"Authorization": openai.Omit()` to the
  request's `extra_headers` when there is no key - in an adapter that delegates
  to `stream_openai_completions` with its own client, from its `on_payload`
  hook. Both lines of that advice measured the same on openai 1.109.1, 2.7.2,
  2.33.0, 2.34.0 and 2.54.0. aelix's own adapters need nothing: a keyless openai-completions call settles
  this itself (above).

- **The installers choose the interpreter range from the release they install
  (#278, ADR-0265).** `install.sh` and `install.ps1` now default
  `AELIX_PYTHON` to `>=3.11,<3.15` for `v0.1.0-beta.3` and later and keep
  `>=3.11,<3.14` for `v0.1.0-beta.2` and earlier, which pin `openai<2.0`; a
  version they cannot parse gets the older range. A default that simply widened
  would have put an `AELIX_VERSION=v0.1.0-beta.2` install on 3.14 and the crash
  above. The log names the request it chose. Both e2e jobs gained a pass that
  pins `v0.1.0-beta.2` with 3.14 on the runner and requires the environment to
  land below 3.14, and their post-conditions derive the range they expect from
  the release they resolved. Until `v0.1.0-beta.3` is published, keep
  upgrading through the install line rather than `uv tool install aelix@latest`.

- **rpc `set_auto_retry` now persists, and `settings.json` wins over the
  in-memory toggle (#408) - a behaviour change for embedders.** As in pi,
  `set_auto_retry` (and `AgentHarness.set_auto_retry_enabled` on any harness
  with a settings manager, which is every harness the CLI builds) writes
  `retry.enabled` into the global `settings.json`: `set_auto_retry false` sent
  for one rpc session now also turns the retry off for every later TUI, `-p`
  and rpc run under that account, until something sets it back. And when the
  settings carry a boolean `retry.enabled`, it decides - an embedder that sets
  `AgentState.auto_retry_enabled` directly is overridden; `get_state`'s
  `autoRetryEnabled` reports the value in force. The `aelix-server` WebSocket
  harness has no settings manager and keeps the in-memory toggle.

- **Root commands accept a leading `--offline` without becoming agent prompts
  (#289).** `aelix --offline extension …`, `docs`, and `status` dispatch their
  commands. Other launch options before a command fail with exit 2; option
  values, immediate `-p` messages, and already started prompts retain their
  meaning. Bare `--` now explicitly ends agent options. `status`/`docs` still
  reject a trailing offline flag.

- **Windows: a `git+file:` extension source needs uv 0.11.27 or newer, and the
  private-catalog guide now says so (#393).** When aelix installs with uv (after
  `install.sh` / `install.ps1`, whose environment has no pip), uv before 0.11.27
  crashes on `git+file:///C:/…` and `name @ git+file:///C:/…` — `error: The
  channel closed unexpectedly`, exit 2 (astral-sh/uv#19887). aelix does not check
  your uv's version; `uv --version` shows it and `uv self update` upgrades the
  uv the installers bootstrap. CI and the release build now run uv 0.12.23
  (was 0.11.14), with each runner's archive checksum pinned, so the Windows test
  leg installs such a source for real.

- **Provider keys and provider extensions have guides of their own, so the two
  bundled guides nearest `read`'s cap have room for the edits coming (#490,
  ADR-0218).** The system prompt tells the model each bundled guide is small
  enough for `read` to return whole, and `read` cuts a file at 51200 bytes.
  Every guide still fit, but `extension-authoring.md` had 84 bytes left and
  `providers-and-models.md` 808, so one more paragraph in either would have
  failed the check that keeps that promise, and pending changes add 1396 bytes
  to the second. Where a key comes from (an environment variable, `--api-key`,
  `models.json`, `/login` and its token refresh), each provider's variable and
  what happens with no key are now `credentials.md`: `aelix docs credentials`,
  and `aelix docs auth`, `login`, `api-key` and `api-keys` open it. A provider
  from an extension (`register_provider`, its own `/login` method, launch and
  rebuilds, a custom wire adapter) is now `extension-providers.md`. The text
  moved as it was, and links into it point at its new place. Each trimmed guide
  keeps the first moved heading (`Providing an API key`, `Custom providers with
  their own /login method`) over a pointer to the new guide, and `aelix docs
  providers` now points there for the environment-variable table. Left:
  `providers-and-models.md` 11424 bytes, `extension-authoring.md` 13588. A new
  check fails when a link's `#section` names no heading of its guide; it found
  one in `tls-and-corporate-ca.md`, now fixed. The cap was not raised.

- **Compaction has a guide of its own, `compaction.md`, as in pi (ADR-0218).**
  The compaction fixes in this release (#469, #563, #565 and the others) put
  `providers-and-models.md` at 52018 bytes, over the 51200 at which `read` cuts
  a file, while the system prompt tells the model each bundled guide fits in one
  read. Its `Automatic compaction` section (when it runs, the `compaction`
  settings, what a failed one leaves) and `How much a request asks the model to
  write` are now `compaction.md`: `aelix docs compaction`. The text moved as it
  was; `providers-and-models.md` keeps the `Automatic compaction` heading over a
  pointer to it. Left: `providers-and-models.md` 7074 bytes. The cap was not
  raised.

- **Packaging: the `aelix` sdist no longer carries the demo recording, and the
  README's demo image now shows on PyPI (#387).** `docs/assets/demo.gif`
  (2.3 MB, about 40% of the tarball) and the three files that produce it
  (`demo.tape`, `dedup_frames.py`, `make_demo_fixture.py`) are excluded from
  the root sdist, which drops from 5,974,799 B to 3,666,895 B; nothing that
  unpacks an sdist read them, and the brand assets under `docs/assets/brand/`
  still ship. `README.md` and `README.ko.md` load the GIF from
  `raw.githubusercontent.com` as the logo already did, so it looks the same on
  GitHub and now renders on the PyPI project page, where the relative path was
  broken. The files stay in the repository. The size gate had started failing
  on Windows checkouts, whose CRLF line endings add about 24 KB to the same
  tree, which put it just over its 6 MB cap; the cap is unchanged. The per-package sdists and all five
  wheels never carried `docs/assets/`.

- **A repository's `.aelix/settings.json` now asks for trust (#369,
  ADR-0252).** It is the project scope of your settings — it can choose the
  default model and provider your prompts go to — and it is now read only in a
  trusted directory, as pi does. A repository that carries one now shows the
  trust selector once when you run `aelix` there (the selector names the file),
  and in `-p`, `--mode json` and `--mode rpc` its settings are skipped until you
  pass `--approve` or save a decision (`aelix` interactively, or `/trust`);
  the stderr notice now lists "settings". Scripts and CI that relied on a
  checked-in project settings file under `-p` need `--approve`.
  `--list-models` follows `--approve`/`--no-approve`, a saved decision or your
  global `defaultProjectTrust` without asking, and prints the same stderr
  notice when it skipped the repository's `enabledModels`. Anything named
  `.aelix/settings.json` counts, not only a regular file (as in pi). A
  delegated agent in such a repository runs untrusted.
- **`/trust` says what it does.** It saves your answer for the next launch and
  now says "Restart aelix for this to take effect." — it used to say "Run
  /reload to apply it to project-local resources.", which was not true (the
  session's trust is decided once, at launch). For the same reason it no
  longer offers "this session only" answers. (#369)
- **The trust question breaks its lines between words, and `/trust` shows what
  is saved, as pi does (#380, ADR-0252 amendment 2026-10-21).** The question
  `aelix` asks at start-up broke its text wherever the terminal edge fell: at
  120 columns it read `… on your m` / `achine` and `… apply .` /
  `aelix/settings.json`. Its lines now break after a space, at the width of the
  terminal, also when you resize it; a path wider than the terminal is still
  cut where the row is full. The two other questions asked before the session
  opens (running a project agent profile, and a session already open in another
  terminal) wrap the same way. `/trust` showed the start-up question's first
  line, cut at the terminal edge, so its skills and settings clauses never
  appeared and it did not say what you had decided before. It now shows the
  project path, `Saved decision: trusted (<path>)` or `untrusted (<path>)` -
  `(inherited from <path>)` when the decision is a parent folder's - or
  `none`, and `Current session: trusted` or `untrusted`, then the options with
  `✓` on the one you saved, the cursor on it. The title rows (the path and the
  saved decision, which repeats it) are cut between words at the width
  `/trust` opens at; an option row wider than the terminal is cut at its edge,
  as in aelix's other pickers. The risk text stays on the start-up question
  only. And choosing `Trust parent folder` in `/trust` after a saved `Do not
  trust` for the folder now trusts the folder: it also removes the folder's
  own entry from `trust.json`, as pi does. Before, the folder's `false` stayed
  and outranked the parent, so `/trust` said "Project trusted (saved)" and the
  next launch was still untrusted.
- **`aelix extension source list/add/remove` read install sources from your
  global settings only.** A project `.aelix/settings.json` listing
  `extensionSources` or `suppressedDefaultCatalogs` used to be merged in, and
  `source add` then wrote the repository's entries into your global file. If
  you kept install sources in a project file on purpose, move them to your
  global `settings.json`. (#369)
- **A `models.json` `apiKey` now comes before the provider's environment
  variable, and a model chosen at launch gets the same `compat` and
  `modelOverrides` as in `/model` (#363, ADR-0251).** The key a request carries
  is now Pi's order: `--api-key`, then a `/login` credential, then the
  provider's `apiKey` in `models.json`, then its environment variable. Until now
  the environment variable came first, so a gateway you re-pointed a built-in
  at (`providers.openai = {baseUrl: <gateway>, apiKey: …}`) received your
  exported `OPENAI_API_KEY` — or one a cloned repo's `.env` supplied — instead
  of the key you wrote for it. **This switches credentials silently:** if you
  have both an `apiKey` for a provider in `models.json` and its vendor key in
  your shell, requests now carry the `models.json` key, for every provider, not
  only a re-pointed one. An `apiKey` that names an environment variable
  (`"apiKey": "OPENAI_API_KEY"`) still reads that variable, a project `.env`
  value included; use a literal, a `!command` or a variable of your own to keep
  a `.env` key away from a gateway. A stored `/login` credential owns its
  provider, whatever it yields: an OAuth login whose refresh fails now fails the
  request, saying `OAuth refresh failed for <provider>: … Run /login to sign in
  to <provider> again.`, and sends nothing (it is not retried) — before, the
  request went out on the `models.json` `apiKey` or on the provider's
  environment variable. An `auth.json` entry that gives no key — an empty
  `api_key`, a `!command` key that prints nothing, an OAuth login whose OAuth
  provider is not available, an entry of an unknown type — fails the same way,
  naming the entry, `/login` and the `auth.json` path; before, the request went
  out on the provider's environment variable (to your gateway, if you re-pointed
  the provider). A `!command` key that fails now names them too (it already
  sent nothing, but said only that the command failed). For
  extensions, `ModelRegistry.get_provider_auth_status` reports `--api-key` ahead
  of a stored credential, as the request uses it. A key an extension registers for a provider stays after the
  environment variable for now (#365); an extension that registers the
  provider without a key (only models, `headers` or `authHeader`) no longer drops
  your `models.json` `apiKey`. A `models.json` provider entry
  may now carry only an `apiKey` (or only `authHeader`) — before, aelix refused
  the whole file (`must specify "baseUrl", "headers", "compat",
  "modelOverrides", or "models"`). **At launch**, `--model`, `--provider`, an
  agent profile and a delegated agent used to take only the host from a
  provider's `models.json` entry; they now apply the provider `compat` and the
  `modelOverrides` too, on every built-in provider whether re-pointed or not —
  so a gateway that needs `supportsDeveloperRole: false` now works from the
  command line, and **launch metadata now follows your `modelOverrides`**
  (`contextWindow`, which moves when compaction starts, `maxTokens`, `cost`,
  `name`, `reasoning`, the thinking map). The launch model is the one `/model`
  shows, including what an OAuth login changes on it. The model's protocol (`api`) is still
  the catalog's, and an exported `OPENROUTER_BASE_URL` still wins over
  `providers.openrouter.baseUrl` — at launch, and since #375 in `/model` too
  (see Security).
- **`--model` is resolved the way pi resolves it, and an OpenRouter key no
  longer turns every model string into an OpenRouter id (#362, ADR-0250).** The
  prefix names the provider and the id must match exactly inside it; a bare id
  is matched across every provider. What you will notice:
  - holding both an OpenRouter and a vendor key, `openai/gpt-4o-mini`,
    `anthropic/claude-haiku-4-5` and the other vendor-prefixed ids go to the
    **vendor** directly (they used to go to OpenRouter); with only the
    OpenRouter key they still go to OpenRouter;
  - a bare id several providers serve (`gpt-4o-mini`) goes to the one you hold a
    key for, else it is an error that lists them and says to use `--provider`
    or `<provider>/<id>` — it no longer goes to OpenRouter; a settings
    `defaultProvider` (which a project `.aelix/settings.json` can set) breaks
    that tie, or takes a bare id nothing lists, only for a provider your own
    key authenticates or one you defined, while you hold a key of your own;
  - a `<vendor>/<model>` OpenRouter's part of this build's catalog does not
    list goes to OpenRouter as written, with a one-line note, only with an
    OpenRouter key of your own — exported, stored with `/login`, or in your
    `models.json` — when its prefix is unknown (`newlab/model-x`) or a vendor
    OpenRouter shares (`openai/…`, `anthropic/…`) you hold no key for, even if
    that vendor's catalog lists the id (`openai/o1-pro`); otherwise it is "not
    found" (use `openrouter/<id>`);
  - `openrouter/<id>` strips the prefix as pi does (`openrouter/auto` sends
    `auto`), and `--provider X --model X/<id>` strips the repeated prefix when
    X does not list `X/<id>` itself (pi strips first: `--provider openrouter
    --model openrouter/auto`, and `OPENROUTER_DEFAULT_MODEL=openrouter/auto`,
    send `openrouter/auto`, which OpenRouter also accepts);
  - an id a provider does not list is sent as a custom id with a one-line
    warning, and an ambiguous or unknown `--model` stops `-p` / `--mode json`
    with pi's error before anything is sent;
  - `OPENROUTER_DEFAULT_MODEL` is read from your shell only — a project `.env`
    can no longer set it unless you list it in `AELIX_DOTENV_ALLOW` yourself,
    in which case that file's value picks the model — and
    `OPENROUTER_BASE_URL` now applies to `--provider openrouter` too;
  - `--api-key` with a bare extension id (`-e ext.py --model m1 --api-key K`) is
    no longer refused before the extension loads (and with a prefix no provider
    has it is "not found" — see the #370 entry below), and with a vendor prefix it
    keeps the string on that vendor: `--model openai/gpt-4o-mini --api-key K`
    with an OpenRouter key exported goes to OpenAI with K (it used to go to
    OpenRouter with K as the bearer, as pi still does) — so if K is an
    OpenRouter key, write `openrouter/openai/gpt-4o-mini` or it is sent to
    `api.openai.com`; a route that does not resolve gets no key;
  - a delegated agent is told `--provider` only where it could not reach your
    route alone (a provider you defined); a route only your `--api-key` would
    decide is left to the child, which never receives the key; a profile that
    brings its own `extensions:` resolves its `model:` with them.

- **`--api-key` with a model id no provider has is "not found", as in pi — your
  typed key is no longer sent to OpenRouter for it (#370, ADR-0250).** With an
  OpenRouter key of your own exported, `--model newlab/model-x --api-key K` sent
  the prompt to `openrouter.ai` with **K** as the bearer (in `-p`, `--mode json`,
  RPC and the TUI), though K was typed for whatever `newlab` is. Now `-p` and
  `--mode json` exit 1 before anything is sent, with `Error: Model
  "newlab/model-x" not found. Use --list-models to see available models.
  (--api-key does not send an id this build does not know to OpenRouter; to send
  it there with that key, use --model openrouter/newlab/model-x or --provider
  openrouter --model newlab/model-x.)`; the TUI starts with that Warning and
  sends nothing until `/model` picks a model; RPC starts on the unresolved model
  and refuses the prompt (since #371 it exits 1 with the same error, below).
  **If K is an OpenRouter key and the id is one this
  build does not list** (`--model newvendor/model --api-key sk-or-…`), write
  `openrouter/newvendor/model` or `--provider openrouter`. Unchanged: those two
  explicit routes, an id OpenRouter's part of the catalog lists
  (`x-ai/grok-4.3` still goes to OpenRouter with K, as in pi), and the same
  `--model newlab/model-x` without `--api-key` (OpenRouter, on your own key,
  with the one-line note). `/agents use` now re-resolves the launch model with
  the key in view too, so it refuses such a launch string instead of sending it
  to OpenRouter on your own key; a profile's own `model:` is resolved as before.
  (Since #412 an `/agents use` none of whose profile's `model:` / `provider:`
  applies, or `--none`, re-resolves the launch model only when it replaces a
  profile whose own `model:` or `provider:` applied; otherwise it keeps the
  model you are on — after such a launch, the placeholder — and says why no
  prompt will be sent.)
  A nested id (`newlab/org/model-x`) is refused the same way; an id with an empty
  segment (`newlab//model-x`) gets the plain not-found text, without the
  OpenRouter routes.

- **`--provider` without `--model` stops with pi's error in every mode — the
  TUI and `--mode rpc` no longer start (#368, ADR-0250).** `aelix --provider
  openai` (no `--model`) now exits 1 with `Error: --provider requires --model
  (for example: --provider openai --model <id>)` before any MCP server starts,
  the session's extensions load or any `session_start` handler runs — and,
  with no `--agent`/`--agent-file`, before the project-trust question too, so
  in a directory with `.aelix/` resources (`.aelix/settings.json` alone
  included) nothing is asked and no extension's
  `setup()` runs. `-p` and `--mode json` used to exit 1 with "model '?'
  (provider 'openai') could not be resolved …" after the extensions had loaded
  and started; **the interactive mode used to start** on that unrunnable model
  with a Warning (pick one with `/model`), and **`--mode rpc` used to start
  silently and exit 0** — both now exit 1 instead, as pi does. An agent
  profile with `provider:` and no `model:` gets the same line, as its
  delegated child now does; the profile is read under the project-trust
  answer, so in a directory with `.aelix/` resources (a `.aelix/settings.json`
  alone counts, #369) and no `--approve` /
  `--no-approve` the trust question (interactive) and its load of your user,
  global and `-e` extensions' `setup()` come before the line. A settings
  `defaultModel` does not count as the `--model`; a settings `defaultProvider`
  on its own (no flag typed) and an empty `--provider ""` keep their old
  messages. Kept: `--provider openrouter` with `OPENROUTER_DEFAULT_MODEL` in
  your shell runs that model with an OpenRouter key from any source — an
  extension that registers one in `setup()` included — and without a key
  fails as it did before (pi has no such variable). `--help` and
  `--list-models` still work with `--provider`; its help line now reads
  "Provider to search for --model (requires --model)".

- **`aelix --mode rpc` can change model, and a `--model` that does not
  resolve ends it with exit 1, as in `-p` (#371, ADR-0250).** The shipped RPC
  mode had no model registry, so `set_model`, `cycle_model` and
  `get_available_models` all answered `set_model requires a ModelRegistry —
  none configured` (`cycle_model …`, `get_available_models …`). They now work
  as in pi: `get_available_models` lists the models your credentials reach,
  those an extension registered included; `set_model {"provider", "modelId"}`
  moves the next prompt to that model (an unknown one is `Model not found:
  <provider>/<id>`); and `cycle_model` rotates to the next when two or more
  models are available (below two it returns nothing, as pi's does). A
  session held because its launch model names a provider an extension
  registered while the session was starting (#367) leaves the hold with
  `set_model` (or `cycle_model`, given two or more models to rotate through);
  its Warning now ends `No prompt will be sent for it; send set_model to select
  a model.` (it said to restart with another `--model`). **A launch model you
  give on the command line that does not resolve** — `--model`, with or
  without `--provider`: not found, ambiguous, or naming a provider nobody
  registered — used to start RPC silently, answer the first `prompt` with
  `success: false` and exit 0 at end of input; it now writes the error `-p` and
  `--mode json` write (`Error: Model "newlab/model-x" not found. …`) to stderr
  and exits 1 before reading a command, with nothing on stdout, as pi does for
  `--model` in every mode. Still started as before: a `defaultProvider` /
  `defaultModel` in `settings.json` that does not resolve (as a pair or alone;
  each prompt is refused until `set_model` or `switch_session` moves the
  session — pi falls back to an available model there instead of failing on
  it, and `-p` and `--mode json` still exit 1 on it, unchanged), an agent
  profile's `model:` that does not resolve (`-p` refuses it too, unchanged;
  with `--provider` typed beside it, RPC exits 1 as for `--model`),
  the #367 hold, a launch with no model at all, and a model that resolved to
  an api this build has no adapter for (its prompt is refused). The
  interactive mode is unchanged (a Warning, then `/model`).

- **Session totals now include what delegated agents spent, so totals from
  this release on — History's included — are higher than the same work was
  before.** The footer, `/cost`, `/session`, `/stats`, the History tab and RPC
  `get_session_stats` counted the parent's own model calls only; a child's cost
  appeared in the tool result's text and nowhere else. Each child's recorded
  spend is now added once, and the `/stats` session tab (`Tools & delegated
  agents: 12.3k in / 1.1k out · $0.0110 (2 runs)`) and `/cost` (a `tools &
  agents` row) break it out on a line of its own, already included in the
  totals above it. While a delegation is still running, when one was cut short
  (Ctrl+C, a timeout) before its child finished, or when its spend could not be
  priced, the session cost reads as a floor (`≥ $…`) rather than a figure that
  is short of the bill. Sessions recorded before this release have nothing to
  add and read as they did. (#199)
- **A parallel or chain delegation now shares one 64 KiB budget for what it
  hands back.** Each child's summary was capped at its profile's `output_cap`
  (51,200 bytes by default) and nothing capped the call, so eight children
  could return one 410 KB tool result — about 102,600 estimated tokens, 92% of
  `gpt-4o`'s auto-compaction threshold, and compaction cannot remove a tool
  result from its turn. The members now split 64 KiB evenly (8,192 bytes each
  for eight), and a member's share holds everything it says — its summary and
  any error note — so eight children at their cap now return about 67 KB. A
  single delegation keeps `output_cap`, a chain step still hands the next one
  its whole capped summary, and what a member loses to the budget is in its
  child's session file when it has one. In both modes, an error message longer
  than the cap is no longer repeated in full after the summary it already
  began. (#199)
- **Session files are now written LF-only on Windows too.** Aelix opened them
  through text-mode descriptors there, so every line it wrote ended in `\r\n`;
  every descriptor that writes session bytes is now binary, and the bytes on disk
  are the same on every platform. Session files written before this still load
  and take new entries — they just end up with mixed line endings, which the
  reader has always accepted. (#294)
- **A custom session `FileSystem` must now provide `rename_file(source,
  destination)`** — atomically replace `destination` with `source` in the same
  directory (`os.replace` in `LocalFileSystem`). Creating a session and forking
  one now publish the whole file through it, so an implementation without it
  fails at the first `create`. (#294)
- **The README's architecture section lists all five packages of the
  workspace, and the design documents no longer name an object that does not
  exist or a goal that was dropped (#266, ADR-0235 amendment 2026-10-22).**
  `README.md` and `README.ko.md` said "Three packages" and left out
  `aelix-server` and `aelix`, the workspace root and the package you install.
  The `aelix-server` line says what it is - a local daemon that serves the RPC
  protocol over HTTP (a WebSocket at `/rpc`), not published to PyPI - and points
  to its own README; the `aelix` line says it gives the `aelix` command and
  re-exports the main names of the three packages it depends on. A test now
  fails when the README's package list or count and the uv workspace's members
  disagree, and drives the server line's claims with FastAPI's test client. `docs/00-conventions.md` defined the term
  `AgentRuntime` as the runtime object in the code; nothing in the code has
  that name. It now defines `Agent` and `AgentHarness` with their import paths,
  and a test imports every path the terms name. `docs/01-product-vision.md` still set the
  goal of reimplementing pi almost exactly, which ADR-0235 dropped: it now says
  pi is a verified reference implementation and full parity is not the goal,
  and one paragraph adds the security/self-host-first positioning from #86,
  with where Aelix runs, where model traffic goes, where extension artifacts
  come from and how permissions are enforced each said on its own. It stays a
  draft.

### Fixed

- **The update notice's install command now installs the release it names
  (#386, ADR-0230 amendment 2026-10-27).** For an `install.sh` install the
  notice printed `AELIX_VERSION=<tag> curl … | sh`. That pin goes to `curl`
  and never reaches the installer. Run as shown and asked for
  `v0.1.0-beta.1`, the line resolved the newest release, installed `0.1.0b2`
  and exited 0. It now prints `curl -fsSL …/install.sh | env
  AELIX_VERSION=<tag> sh`, with `env` so csh and tcsh take it too. A Windows
  install made by `install.ps1` was told the same POSIX line, because both
  installers leave the same uv receipt. It now gets
  `powershell -ExecutionPolicy Bypass -c "Set-Item env:AELIX_VERSION '<tag>'; irm …/install.ps1 | iex"`,
  which runs from `cmd.exe` and from PowerShell and installs the pinned tag.
  The notice also stopped hard-wrapping the line. At 80 columns it printed the
  line in pieces, and the PowerShell line broke at 120 as well. A copy of what
  was on screen was then not one command, and a pasted `… | env` piece printed
  the environment. Now the terminal wraps the line, and a copy gives back the
  whole command. A feed tag that cannot sit in a command unquoted gets the
  release link and no command.
- **A launch with no thinking level, or a resumed `off` session, no longer
  fails on OpenAI's o-series (#397, ADR-0264 and ADR-0239 amendments
  2026-10-26).** On a model that cannot switch reasoning off, a level fitted
  to the model starts at the lowest level the model offers, and for the
  o-series the catalog still offered `minimal`, which OpenAI refuses:
  OpenRouter's `openai/o3` with no `--thinking` answered 400 "Unsupported
  value: 'minimal' is not supported with the 'o3-2025-04-16' model", and in
  RPC mode the first prompt did too. Now it answers at `low`, RPC `get_state`
  says `low`, and `/thinking` lists `low`, `medium` and `high`. The catalog
  hides the levels below the lowest one the model's effort list names on 83
  rows that hide `off` (95 levels): OpenAI models whose effort list starts
  above `minimal` - the o-series, codex, `gpt-5.1`-and-later and `-pro` rows,
  Copilot's `gpt-5` rows and `gpt-6-astra` - on `openai`,
  `azure-openai-responses`, `cloudflare-ai-gateway`, `opencode` and
  `github-copilot`, and OpenRouter's
  `openai/o1`, `o3`, `o3-mini`, `o3-pro` and `o4-mini`.
  `scripts/refresh_catalog.py` derives them, so the next fold-back keeps them.
  OpenRouter's other rows keep `minimal`, which every other live row #507
  changed accepted (61 of 64; the three that refused are the `o3` rows
  above). The `chat-latest` rows on `openai` and `azure-openai-responses`
  still start at `minimal`: no source the rule reads lists their efforts
  (models.dev's `302ai` lists only `medium`, so `minimal` likely fails there,
  unmeasured). An explicit `--thinking off` (and a launch profile's
  `thinking: "off"`) is unchanged: on OpenAI and OpenRouter it sends no
  reasoning field and the model reasons at its default. Not measured:
  OpenAI's own API (no credits), Azure (no adapter in this build),
  Cloudflare, opencode and Copilot.

- **Thinking off is no longer sent as `effort: "none"` to models that refuse
  it (#507, ADR-0264 amendment 2026-10-26).** On OpenRouter and the OpenAI
  Responses APIs, a model whose catalog row did not say how to turn reasoning
  off was sent `reasoning.effort: "none"` for off, and models that take no
  `none` answered 400. Measured on OpenRouter with `--thinking off`:
  `openai/gpt-5-mini`, `openai/gpt-5-nano`, `openai/gpt-oss-20b`,
  `google/gemini-2.5-pro` and `z-ai/glm-5.3-flash` answered "Reasoning is
  mandatory for this endpoint and cannot be disabled", and now reply; an off
  session on `gpt-5-nano` answers three turns and compacts where every turn
  and the compaction failed before. A session at off now sends no reasoning
  field there, so the model reasons at its default. 91 shipped rows carry
  `off: null` - 72 OpenRouter rows marked reasoning-mandatory, the o-series on
  `openai` and `cloudflare-ai-gateway`, `openai/gpt-realtime-2.1`,
  `opencode/gpt-5.3-codex-spark`, and the six `azure-openai-responses` rows
  for when that provider has an adapter - and `openai/gpt-6-astra`'s
  `"off": "none"` is now `null`; `/thinking` no longer offers off on them.
  `scripts/refresh_catalog.py` derives these from OpenRouter's
  `reasoning.mandatory` and models.dev's effort lists, as it already did for
  new rows, so the next fold-back keeps them; `--apply --off-only` writes only
  them, and the publish job reports one `main` has not committed. Not fixed
  here (#397): where off is fitted to the model - a launch with no level, a
  resumed or carried off session - it now moves to `minimal`, which OpenAI's
  o-series refuses too: `openai/o3` on OpenRouter with no `--thinking`
  answered 400 "Unsupported value: 'minimal'" (it answered "Reasoning is
  mandatory" before). Not measured: OpenAI's own API (the test key has no
  credits) and Cloudflare.

- **Automatic compaction keeps working after a session's first compaction
  (#565, ADR-0176 amendment 2026-10-22).** The guards that keep an answer from
  before a compaction from triggering another compared the answer's time in
  seconds, as aelix stamps it, with the compaction's time in milliseconds, so
  every later answer read as older than the compaction: neither the threshold
  compaction nor the compact-and-retry of a request refused as too long ran
  again in that session, and a long conversation went on to fail every turn
  with `prompt is too long`. Measured with the real `aelix -p -c` and a local
  server, every answer reporting 190000 of a 200000 window: a compaction at
  turn 2 and none at turns 3 to 5; after a first compaction, a request refused
  as too long exited 1 with nothing compacted. Now both times are read in
  milliseconds, as pi keeps them: turns 2 to 5 each compact, and the refused
  request is compacted and sent again. Sessions saved before keep their
  timestamps and compact again when reopened.

- **The corporate-CA advice no longer reads as "edit certifi's own certificate
  file": its last line now says to use a copy of it with the CA appended (#556,
  ADR-0195 amendment 2026-10-22).** The advice for a certificate from an issuer
  this machine does not trust ended with ``or append the corporate CA to the
  bundle printed by `python -m certifi`, then retry.`` That command prints the
  path of the `cacert.pem` inside the installed certifi package, so the line
  read as "add the CA to that file" - which the TLS guide warns against,
  because reinstalling or upgrading certifi replaces the file and the failure
  comes back weeks later with no clue (measured: a CA appended there was gone
  after `uv pip install --reinstall certifi`). On macOS, Windows and every
  system but Linux, where the example above it already points at `~/.aelix/ca-bundle.pem`, the line now
  says what that file is: ``where ca-bundle.pem is a copy of the bundle
  `python -m certifi` prints with the corporate CA appended (not certifi's own
  file, which an upgrade silently reverts), then retry.`` On Linux, whose
  example is the system's own bundle, the copy is offered as the alternative
  (`or at a copy of …`). The example for each platform is unchanged, and the
  advice still fits the TUI whole. The TLS guide's recipe sentence now says
  "make a copy" the same way. Following the new line in zsh on macOS - the
  guide's `cat … > ~/.aelix/ca-bundle.pem`, then the printed `export` line -
  reached a server whose certificate the probe CA signed, and certifi's own
  file was unchanged.

- **aelix no longer retries a request whose certificate this machine does not
  trust, and the TUI shows the TLS advice once, not five times (#551, ADR-0128
  amendment 2026-10-22).** The OpenAI and Anthropic SDKs report a
  failed certificate check as `Connection error.`, which is in the automatic
  retry's pattern, so one prompt behind a corporate proxy's private CA sent 4
  attempts (12 TLS handshakes with the SDKs' own retries) over 20 s, and the TUI
  printed `✖ Connection error. …` and the 7-line advice after each attempt, the
  last as `✖ Retry failed: …`. Every attempt checked the same certificate
  against the same trust store. Now aelix does not send a failure that gets the
  TLS advice again: one prompt is 3 handshakes and about 2 s - the request and
  the OpenAI or Anthropic SDK's own 2 connection retries inside it, which still
  run (`anthropic-messages`, `openai-completions` and `openai-responses` 3 each,
  Gemini 1; measured on macOS with a local self-signed server, `-p`, `--mode
  json` and the TUI, and with truststore blocked). The same holds for a compaction's or branch summary's
  summary request, a stored `/login` subscription's token refresh that fails
  the check (it is no longer "could not be reached"), the GitHub Copilot sign-in's polling (it
  stops at once instead of after 6 polls) and the OpenAI Codex adapter's
  connection retries (1 attempt instead of 4). A `502`, a refused connection
  and a timeout are retried as before, and so is the one certificate failure a
  retry can pass: the request that failed on Python 3.13's strict checking
  just before `aelix` relaxed it for the session (measured on 3.13.13: one
  retry, answered). pi retries this failure; aelix diverges knowingly. Still
  retried (follow-ups): the SDKs' own two connection retries inside the one
  request, above, and a failure whose message an extension rebuilds in
  `message_end`.

- **A request the provider refused as too long that compaction could not
  recover now fails visibly: `-p` writes the provider's error and exits 1, and
  `--mode json` exits 1 (#391, ADR-0176 amendment 2026-10-22).** The overflow
  recovery takes the failed answer out of the conversation before it compacts,
  so that the request it sends again does not carry it. When nothing was sent
  again - nothing to compact yet (the session's first turn), a summary that
  failed or was refused, an extension that cancelled the compaction, an abort
  during it - the answer stayed out, while the session file kept it. Measured
  on the base with the real CLI and a local server that refuses the request as
  `prompt is too long`: `-p` exited 0 with nothing on stdout or stderr (after a
  failed summary, only #546's `Context overflow recovery failed: …` line),
  `--mode json` exited 0, rpc `get_messages` ended on the prompt and
  `get_last_assistant_text` answered the previous turn's text. Now the failed
  answer is put back: `-p` writes the provider's error to stderr (after that
  line when the compaction failed) and exits 1, `--mode json` exits 1 with a
  clean stderr, and rpc ends on it. A turn the compaction did recover is sent
  again and answers as before. The TUI already drew the error, and still does.
  pi leaves the answer out. A refused request that the compaction could not
  recover is not compacted a second time in the same prompt: the threshold check
  after it passes over the overflow, as pi's does (with the answer back, it had
  estimated the long prompt past the threshold and run a second summary at once).
  Not changed: a retry wait that is cancelled
  (Esc, rpc `abort_retry`) drops a retriable error the same way, as in pi.

- **A turn that succeeded is no longer turned into an error when the automatic
  compaction after it fails: `-p` prints the answer and exits 0, and the failure
  is reported as pi reports it (#546, ADR-0126 amendment 2026-10-22).** When the
  conversation passed the compaction threshold after a good answer and the
  summary request then failed - a `502` past its retries, a model that refuses
  it (the 400 of #506), an auth callback that raised - or an extension's
  `session_before_compact` cancelled it, the error was raised out of the
  prompt. Measured on the base with the real CLI and a local server: `-p`
  printed `Error code: 502 - …` (or `Compaction cancelled`) instead of the
  answer and exited 1, `--mode json` exited 1 too, rpc logged `[rpc] prompt
  task failed`, and the TUI drew the error after the answer; an extension that
  cancels automatic compaction failed every turn past the threshold. Now the
  turn keeps its answer and the conversation stays as it was (the next turn
  past the threshold tries again). The `compaction_end` event says what failed
  in pi's words, `Auto-compaction failed: <reason>` (`Context overflow recovery
  failed: <reason>` when a request refused as too long could not be compacted),
  or `aborted: true` with no message for a cancel or an abort of the prompt.
  `-p` also writes that line to stderr (pi writes nothing there); `--mode json`
  and rpc carry it on the event, and json's stderr stays clean. A manual
  `/compact` fails as before. The TUI draws the line in red under the answer
  (`✖ Auto-compaction failed: …`, after the `Retry failed` line of a failure that
  was retried) and a cancel or an abort as `Auto-compaction cancelled`, as pi
  does.

- **`settings.json`'s `compaction` block now takes effect: `enabled`,
  `reserveTokens` and `keepRecentTokens` (#469, ADR-0126 amendment
  2026-10-22).** None of the three was read, as `retry.*` was not before #408:
  auto-compaction always waited for `contextWindow - 16384`, every compaction
  kept about the last 20000 tokens, and `"enabled": false` stopped nothing.
  Measured on the base with the real `aelix --mode rpc` and a local server:
  with `"keepRecentTokens": 20`, `compact` after three short turns answered
  `Nothing to compact`; with `"reserveTokens": 60000` no compaction ran at
  150000 of a 200000 window; with `"enabled": false` the threshold and the
  overflow compactions both still ran and `get_state` said
  `autoCompactionEnabled: true`. Now `enabled` turns both automatic compactions
  off (`/compact` still works), `reserveTokens` sets the threshold and
  `keepRecentTokens` how much every compaction keeps, `/compact` included;
  pi's keys and defaults, read each time they are needed (a `/reload` applies
  them). A value pi refuses with an error - a string, a negative or fractional
  number, `null` - is ignored and the default applies. rpc
  `set_auto_compaction` now writes `compaction.enabled` to the global
  `settings.json`, as pi does, so it outlasts the session (before, it held
  only for that rpc session); a project's own `compaction.enabled` still wins.
  An extension's `session_before_compact` handler sees the values in force as
  `preparation.settings` (it was `None`). Not read yet: pi's per-model
  `compaction.modelOverrides` and `branchSummary.*`; the TUI's `/context` still
  shows 16384 as its `autocompact buffer`.

- **Compaction and `/tree` summaries work on models that cannot switch reasoning
  off: they ask at the session's thinking level, set no output cap of their own,
  and a summary cut at its limit is refused (#506, ADR-0142 amendment
  2026-10-22).** The history summary, a split turn's prefix summary and the
  branch summary `/tree` writes always asked with reasoning off, which OpenAI
  Responses and OpenRouter send as `reasoning: {effort: "none"}`; `o4-mini`,
  `o3-mini` and `gpt-6-astra` refuse it (`'none' is not supported with the
  'o4-mini' model`, OpenRouter's `Reasoning is mandatory for this endpoint`),
  so on those models the conversation worked and every compaction failed, and
  the context filled up. Measured with a local server that answers as o4-mini
  and the real `aelix --mode rpc --thinking high`: the turns ran at
  `effort: high` and `compact` failed with that 400. Now each summary asks at
  the session's level fitted to the model, as pi does; `off` and a model that
  does not reason ask with reasoning off, as before. The fixed caps (13107
  tokens for the summary, 8192 for the prefix, 2048 for `/tree`) are gone, as
  in pi: the reasoning spends the same cap, and on the same server a summary at
  `high` under the 13107 cap stopped half way and that half replaced the
  conversation. Each summary now gets the cap a turn of that model gets, cut to
  the window. A summary that stops at its limit (`stop_reason` `length`) is
  refused with `Summarization failed: generation hit the token cap and the
  summary is incomplete` (`Turn prefix summarization` / `Branch summarization`
  for the other two) and is not retried; before, only an error was refused, so
  a cut summary replaced the conversation silently. Unlike pi, the `/tree`
  summary carries the level too.

- **A long conversation no longer fails on every turn before auto-compaction is
  reached: each request asks the model for no more output than the context
  window still holds, as pi does (#563, ADR-0114 amendment 2026-10-21).** An
  endpoint that refuses a request whose conversation plus its output cap is
  larger than the window (MiniMax's Anthropic-compatible API, vLLM, many
  OpenAI-compatible servers) rejected every turn once the conversation passed
  `contextWindow - maxTokens`, while auto-compaction waits for
  `contextWindow - 16384`. Measured on the base with a local server that
  enforces the sum (window 60000, cap 32000, about 8000 tokens a turn) and the
  real `aelix -p -c`: turns 4 to 6 failed with ``input length and `max_tokens`
  exceed context limit``, and after the compaction the error path finally ran,
  the kept 35101 tokens plus the 32000 cap still did not fit, so every later
  turn failed too. Now every output cap is cut to `contextWindow`, less the
  conversation (the last answer's reported usage plus an estimate of what came
  after it at pi's 3.5 characters a token; an image counts as 1372 tokens), less 4096, and at least 1: the
  Anthropic adapter's cap, with its thinking budget inside it (under 2048 tokens
  of room, budget thinking is off for that request), and a cap the request
  sets itself in openai-completions, OpenAI Responses (never under 16) and both
  Gemini adapters (none of aelix's own requests sets one since #506; an
  embedder's may). On the same server all 8 turns
  answered and the automatic compaction ran at its threshold. openai-completions
  still leaves its own cap out when it does not fit, now judged with the
  reported usage too (a divergence: pi sends the cut cap). The cost on the
  first-party Anthropic endpoint, which accepts the sum: near a full window an
  answer can stop up to about 4096 tokens sooner. The errors such endpoints
  give - Anthropic's ``input length and `max_tokens` exceed context limit``,
  Hugging Face TGI's `` `inputs` tokens + `max_new_tokens` must be <= `` - and
  pi's newer z.ai (`Prompt too long`, `Prompt exceeds max length`), DS4 and
  DashScope texts now count as a context overflow, so overflow recovery
  compacts. Not changed: the threshold itself (configurable since #469, above); the compaction
  summary's own limits went with #506 (above). Found while measuring: after a session's first
  compaction no automatic compaction ran again - fixed by #565 (above).

- **After an `/agents use` that switches the model, `/new` stays on that model:
  OpenRouter's `auto` no longer turns into `openrouter/auto` (#398, ADR-0196).**
  Three cases moved, each measured against a local server standing in for
  OpenRouter and for a `models.json` provider. Launched with
  `--model openrouter/auto`, a use that replaces a profile whose own
  `provider: openrouter` applied (the launch's `--agent`, or an earlier use)
  goes back to your launch model and sent its next prompt as `auto`, but after
  `/new` the prompts went out as OpenRouter's other model id,
  `openrouter/auto`. Launched without `--model`, a profile with
  `model: openrouter/auto` ran `auto`, then `openrouter/auto` after `/new`. And
  with a `models.json` provider `mygw` that lists both `gpt` and `mygw/gpt`, a
  profile with `model: mygw/gpt` ran `gpt`, then `mygw/gpt` after `/new`.
  `/fork`, `/resume` and `/reload` did the same wherever they do not bring back
  a session's own model. Each now stays on the model the use set. Unchanged: a
  profile with `provider: openrouter` over a typed `--model openrouter/auto`
  runs the literal `openrouter/auto` from the use on, as
  `--provider openrouter --model openrouter/auto` does, and keeps it through
  `/new`; and a typed `--model` still beats a profile's `model:`. Two corners
  stay as they were, both from a `models.json` or extension provider (one you
  defined in `models.json`, or one an extension registered with
  `register_provider`): if such a provider's name differs from a built-in one
  only in case (`OpenAI` beside `openai`), a use that lands on the built-in one
  has `/new` start on yours; and if such a provider lists both `mygw/x` and
  `mygw/mygw/x`, a profile with `model: mygw/mygw/x` runs `mygw/x`, then
  `mygw/mygw/x` after `/new`.
- **The footer shows the model and thinking level you switched to as soon as
  you switch (#534, ADR-0116 amendment 2026-10-22).** After an `/agents use`
  that changed the model to one with the same context window - another model
  of the same provider, or of another provider with the same window - the
  footer's `✱` kept the old model's name until the next answer finished, while
  your next prompt already went to the new model. Launched with
  `--model openrouter/auto --agent orprof` (a profile with
  `provider: openrouter`), `/agents use --none` sent the next prompt as `auto`
  under a footer reading `openrouter/auto`. An extension's `ctx.set_model` did
  the same. The `🧠` level stayed on the old value after `/thinking high` (which
  printed `thinking → high` above `🧠 off`), after a profile's `thinking:` and
  after an extension's `ctx.set_thinking_level`. All of these now repaint the
  footer at once. An `/agents use` that fails after switching the model (an
  extension refused its thinking level, say) leaves the footer on the model you
  stayed on. `/model`, the pickers and `/settings` already did this.
- **The footer shows the thinking level and model in force even when the TUI
  says the switch failed (#559, ADR-0116 amendment 2026-10-22).** An
  extension's `thinking_level_select` or `model_select` handler that raises
  does not undo the switch. The level or model is already in force, and the next
  request goes with it. But the raise stopped the footer's repaint, so
  `/thinking low` refused that way printed `✖ thinking switch failed` above
  `🧠 off` while the next request went at `low`. The `/thinking` picker showed
  `🧠 low`. `/model gwa/m2` and the `/model` picker left `✱ m1` over a request
  sent as `m2`, and a refused `/settings` thinking cycle left the previous
  level. The pick after `/login` had the same gap. Each of these now repaints the
  footer on its failure path too, context meter included: a refused
  `/model gwa/m2` onto a 32K model showed `✱ m2` beside `◔ 0% · 0/128K`, the
  previous model's window. The repaint after a rolled-back `/agents use` puts
  the meter back on the kept model's window too, also during a turn, where it
  stayed on the abandoned model's. `/thinking <level>` also repaints by itself,
  as the picker does, rather than only through the change event. An
  extension's own `ctx.set_model` or `ctx.set_thinking_level` refused this way
  still leaves the footer stale.
- **RPC `set_model` and `cycle_model` are recorded in the session, fire
  `model_select`, and fit the thinking level to the new model, as pi does (#415,
  ADR-0239 amendment 2026-10-22).** Both commands changed the live model and
  nothing else. A pick that no prompt followed was lost: in `aelix --mode rpc`,
  a prompt, then `set_model rec/m2`, then closing, and `--continue` came back on
  `rec/m1`. Extensions never saw a `model_select` for either command.
  `set_model` also kept the level as it was, so after `--thinking xhigh` and a
  `set_model` to a model that cannot reason, `get_state` still said `xhigh`.
  The request was already right, because the adapter fits the level when it
  builds it. `cycle_model` did fit the level and recorded the fitted level, but
  not the model, so a reopen came back on the old model at the level fitted for
  the new one. Both commands now record the pick and fire `model_select`. They
  then fit the level to the model the session ends on: `xhigh` becomes `high` on
  a model that tops out there, and `off` on one that cannot reason. They record
  the level only when it changes. A `model_select` handler that raises fails the
  command with its error, and between turns the pick is then not recorded: a
  session closed before another prompt reopens on the model it had, while a
  prompt that follows runs on the refused model, and its answer records it. A
  `cycle_model` rotation's `model_select` reports `source: "set"`, because the
  event has no `"cycle"` value yet.
- **A compaction or a branch summary whose summary request hits a provider `502` or
  a transient OAuth refresh failure is retried, as the turn is (#409, ADR-0128
  amendment 2026-10-21).** `/compact`, rpc `compact`, an extension's `ctx.compact()`
  and `navigate_tree` summary, the automatic compaction when the context fills, the
  one that recovers from a context overflow, and a split turn's prefix summary sent
  their request once: measured on the base with a local server whose summary request
  answered `502` once, the rpc `compact` failed after one request, the automatic
  compaction raised the `502` out of a turn that had succeeded, and the overflow
  recovery did not re-run the turn. Each summary request now follows the turn's
  decision - the same retryable causes (a context overflow excluded), an auth raise
  only with #379's `retry_reason` (a refused refresh still fails at once with the
  message it had), `settings.json` `retry.*` and `retry.enabled` - and shows the
  turn's `auto_retry_start` / `auto_retry_end` in the TUI, `--mode json` and rpc.
  `abort`, `abort_retry` and an extension's `ctx.abort()` end a wait at once. In the
  TUI a compaction's retry first shows the error it retries (`✖ …`), and Esc or
  Ctrl+C now cancel a retry countdown during `/compact`, which runs outside a turn
  (they did nothing there, or armed the exit window); a split turn's countdown stays
  until both of its summaries are done waiting, and once a wait is over a summary's
  re-sent request shows `Retrying (N/M) now…` without the turn's `Esc to interrupt`
  (Esc cannot stop it). A provider error event is judged as the turn judges it: an
  aborted stream is not retried. A split turn whose other summary fails no longer
  leaves the retrying one running.
- **On macOS and Windows a certificate the system does not trust gets the TLS
  advice too (#411, ADR-0195 amendment).** With `aelix status` showing
  `store: OS certificate store`, the operating system checks the server's
  certificate, and its error names none of the text aelix looked for. So a
  corporate proxy's certificate on a Mac printed only
  `Connection error. — ('“api.example.com” certificate is not trusted',)`, with
  no word on what to do, in `aelix -p`, `--mode json`, `--mode rpc` and the TUI
  alike. That error now gets the same advice OpenSSL's does: install the proxy's
  root CA system-wide, or point `SSL_CERT_FILE` at it — and the command printed
  for that is now the platform's own: Linux keeps
  `export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`, which every
  platform used to get (on macOS that directory is empty), macOS points at the
  guide's `~/.aelix/ca-bundle.pem`, and Windows gets PowerShell's
  `$env:SSL_CERT_FILE = …`. A certificate for the wrong host (macOS -67602,
  Windows `0x800B010F`) gets the hostname advice and an expired or
  not-yet-valid one (macOS -67818 and -67819, Windows `0x800B0101`) the clock
  advice, never the CA one; none of them gets the Python 3.13 strict-mode
  advice, since the operating system's check does not use it. A certificate
  macOS rejects on its own — a validity period too long, a key too small, no
  server use (-67901, -67735, -67609) — gets no advice, since no CA fixes it,
  and neither does any failure on macOS 10.13 and older, whose verifier cannot
  say which failure it is. A connection that fails for another reason
  (refused, a name that does not resolve, a timeout, a proxy's `407`, a server
  that demands a client certificate) still gets no TLS advice, and its message
  is unchanged. An HTTP error that carries the server's status — a model
  request's, or a `/login` sign-in endpoint's — no longer gets TLS advice
  because its body mentions a certificate: a `400` quoting a prompt that said
  `certificate verify failed` used to get the corporate-CA advice. An error a
  provider reports mid-stream with no numeric status (an OpenAI-style error
  whose code is a word such as `server_error`) is still read by its words, a
  known limit. On Windows the TLS guide now makes the `~/.aelix/ca-bundle.pem`
  copy in PowerShell with `Set-Content -Encoding ascii`, since Windows
  PowerShell 5.1's `>` writes UTF-16, which OpenSSL cannot read. The macOS
  codes were measured on macOS 26.6, -67819 aside (Apple's documented code; a
  not-yet-valid certificate came back as -67818); the Windows ones are read
  from Windows' error table, and the Windows CI legs run the untrusted case for
  real. The TLS guide (`aelix docs tls-and-corporate-ca`) lists the codes.

- **An extension's `select` draws a label of several lines that does not fit
  from its first line, not as a `⋮` over blank rows (#424, ADR-0253 §11.3).**
  With `Cancel` highlighted over an option of eight lines (`Deploy staging` and
  seven detail lines) and a third option, the dialog drew `▸ Cancel`, `⋮` and
  `(1/3)` and left the rest of it blank: one row at 80x10, four at 80x13, seven
  at 120x16, where aelix showed the label's first lines before #399. Those rows
  now show the label from its first line, as before #399, with a `⋮` under the
  last one for the rest; a label above the highlighted option is drawn the same
  way. In those rows a `⋮` that would hide a single one-line option shows that
  option instead, among the up to 8 options around the highlighted one (every
  option of a dialog of 8 or fewer). Elsewhere such a `⋮` can stay: past those
  8 in a dialog of 9 or more options, and where #399 already filled the dialog
  (the tall label above `▸ Cancel` and a one-line option under it, at 80x10:
  `⋮`, `▸ Cancel`, `⋮`). One row can still stay blank: when the nearest label
  above would need a `⋮` on each side of its first line, and, as before, when
  the highlighted label is drawn alone, with no `⋮` for the options around it,
  because it does not fit with one on each side. Unchanged: every
  dialog of one-line options (spawn consent and the permission gate's fallback
  among them); a dialog of longer labels that left no row blank, except that
  the `⋮` under its options, when it hid only the last option and that option
  has one line, can now show it; the highlighted option, the `(n/N)` counter,
  every hold, and the rows under a question taller than the dialog. A label
  wider than the dialog is still cut at its right edge.

- **`/agents use` of a profile that names no model or provider keeps the model
  you are on (#412).** In a session reopened on the model it last ran on, `/agents use
  <profile>` with a profile that has no `model:` or `provider:` (also
  `/agents use --none`) switched to your settings default model: the next
  prompt went there, the switch was written into the session, and the next
  `--continue` came back on the default too. In any session such a switch also
  undid a `/model` pick — back to your `--model`, or to the settings default
  without one — and wrote the model into the session again; so did a profile
  whose `model:` / `provider:` your typed `--model` / `--provider` all
  override. Such a switch now keeps the model — the one the session was
  reopened on, your `/model` pick, or the one you launched with — and writes
  no model change into the session; `/new` still starts on the launch model,
  and `/agents use --none` now says "the model is unchanged" when it keeps it.
  A profile whose `model:` your `--model` overrides but whose `provider:`
  applies still switches, to your `--model` on that provider, and replacing a
  profile whose own `model:` or `provider:` applied (with `--agent` or an
  earlier `/agents use`) still re-derives the model from your launch flags or
  settings default, as before; with neither there is nothing to re-derive and
  that profile's model stays. Replacing one your flags overrode entirely does
  not re-derive. The thinking level is not covered yet: a level `--thinking`
  or the profile's `thinking:` names is still re-applied, which undoes a
  `/thinking` pick and writes that change into the session. When prompts on
  the model you keep will be refused — an id no provider places; a Cloudflare
  AI Gateway model whose account or gateway id is unset; a model with no API
  key or login — the status line says why, with the reason the check before
  every prompt gives. In the TUI the first two are refused before that check:
  with the same reason while any model is available, and with "No provider
  configured. Run /login to add one." when none is. The first two used to
  refuse the whole switch, as did a Vertex model given `--api-key` with no
  Google Cloud environment set, whose prompts are sent; that one now switches
  with no note.

- **A model error with status `520` or `524` is retried (#414).** The automatic
  retry now covers pi's seven statuses - `429`, `500`, `502`, `503`, `504`,
  `520`, `524` - for a model request as it already did for an OAuth refresh
  (#379): a Cloudflare-style `520` or `524` from a provider or gateway is sent
  again instead of ending the turn. For the Anthropic and OpenAI SDK providers
  the SDK first sends it again twice itself, as it does a `502`; the turn is
  retried once those three requests have failed. The statuses match as pi
  matches them, anywhere in the error text, so digits that merely contain one
  make an error retryable too - `max_tokens: 25000` (its `500`), a message
  index such as `messages.520`, a request id - as they already did for the
  other five. The `settings.json` `retry` block governs the new statuses like
  the others.

- **An answer that fails before it starts now has a `message_start` (#414).**
  When a request failed before the provider's stream opened - an HTTP `502`, a
  refused key - the assistant message ended with `message_end` and no
  `message_start`, for every built-in provider and for an extension's own stream.
  It now gets `message_start` (the same message) right before its `message_end`,
  as pi's loop emits it - each such answer once, a later one in the same run too
  (the answer after a tool ran, say) - so `--mode json` and rpc clients see one
  more event for such a failure, and an extension's `message_start` hook fires
  for it. The TUI shows the failure as before, and the session records it once.

- **The bundled guides keep LF line endings on every checkout, so they stay
  under `read`'s cap on Windows too (#485).** Nothing users install changed:
  the wheel is built on Linux and carries LF. A Windows checkout turns LF into
  CRLF by default, one byte more per line, and `read`'s 51200-byte cap is
  measured on the file on disk, so with #285's edit `extension-authoring.md`
  was 51326 bytes there - over the cap on every Windows test leg of PR #484 -
  and 50418 on Ubuntu.
  `.gitattributes` now pins `docs/guides/*.md` and the bundled copies to LF,
  and a new check fails if the pin stops matching a guide or a guide is
  checked out with CRLF. The cap was not raised.

- **The TUI shows an answer that arrives in one piece (#285).** A text block sent
  without any streamed deltas — a Responses-compatible server that puts the text
  only in its `output_item.done` event, or an extension's own stream adapter —
  was saved to the session and shown by `/resume`, but never drawn while the turn
  ran: a turn that said something and then called a tool showed only the tool.
  And when such a block was followed by a streamed one, its text appeared in
  front of the new block's live window and then vanished. It is now drawn when
  it ends, below any reasoning before it and rendered as a streamed answer is,
  so a turn whose events arrive in order looks live the way `/resume` draws it.
  Not yet fixed: when the reasoning before an answer goes on streaming, or
  arrives whole, after that answer has ended, the live screen still places it
  differently from `/resume`, as it already did for a streamed answer; and
  answers that end out of order (one ends while another is still streaming) can
  be drawn in the wrong place, twice, or not at all. Some of those shapes were
  drawn as `/resume` draws them before this change and are not now, mostly an
  empty message that ends while another one is still streaming, whose answer is
  then drawn twice. No server we know of sends them.
  An openai-completions reasoning turn (the end events arrive after the answer
  streamed) still prints its answer once, and so does an extension adapter that
  wraps it and rebuilds its text deltas. The ChatGPT Codex backend sent deltas
  in every run the issue measured, so it was not affected; `-p`, `--mode json`
  and `--mode rpc` already showed the text.

- **Tests: running the suite no longer writes into your `~/.aelix/sessions`
  (#458).** This changes only the tests, not anything users run. The suite never
  set `AELIX_CODING_AGENT_SESSION_DIR`, and isolating the agent directory moves
  settings and auth but not sessions. So each test that ran the CLI without also
  sandboxing HOME left a `--<pytest temp path>--` folder in the developer's real
  session root. One machine had about 7,450 of them, and the stats dashboard
  counted each one as a project. Measured with a scratch HOME, one full run left
  six, all from `tests/cli/test_tools_flag_validation.py`. Now `tests/conftest.py`
  gives every test its own session root, unless a module-, class- or
  session-scoped fixture has set the variable to a non-empty value, which then
  reaches its tests. That value stays in effect until the fixture is torn down:
  for a session-scoped fixture that is not autouse, every test that runs after
  it until the run ends. An empty value is replaced like an unset one, since the
  CLI reads it as unset. Code in the test process that reads the variable, and
  every child that inherits the test's environment, uses it, and a full run
  leaves no folder there. A best-effort guard also blocks the common writes
  under the real root in the test process itself (a write-mode open, `mkdir`,
  rename, remove, `rmdir`, `chmod`, link, symlink, truncate, or an `rmtree` of
  the root) and fails the test. It never reads that directory. It does not see a
  rename of `~/.aelix` or the home or, on POSIX, an `rmtree` of either, `touch`
  or `utime` of an existing file, `chflags`, `mkfifo`, `sqlite3`, sockets, paths
  relative to a directory descriptor, or a symlink into the root from outside
  it. Not covered at all: a child given a hand-built environment without the
  variable, a child started through a shell, `env` or `uv run` wrapper that
  drops it, and an `aelix-server` child, whose `/rpc` ignores the variable.
  Folders that earlier runs left behind are not removed. Their names contain
  `pytest-of-`.

- **Tests: running the suite no longer opens, locks or reads your
  `~/.aelix/agent/auth.json`, or lists your extensions (#497).** This changes
  only the tests, not anything users run. Four tests ran the CLI in the test
  process with neither a sandboxed HOME nor their own `AELIX_CODING_AGENT_DIR`.
  The CLI loads `auth.json` from the agent directory before anything else, so
  each of them created `~/.aelix/agent` if it was missing, set it to mode
  `0700`, opened `auth.json` read-write (creating an empty one if there was
  none), took the same blocking lock a running `aelix` takes, and read your API
  keys and OAuth tokens. They also read `settings.json` and listed your
  `extensions` and `skills`. Measured with a stand-in home, 42 more tests that
  build the agent's options listed `~/.aelix/agent/extensions`, and with an
  extension installed there they would have imported and run it; 13 listed
  `~/.aelix/extensions`, one read `models.json` and two read `mcp.json`. One
  TUI test that undid its isolation while the TUI still ran let the update
  check write `~/.aelix/agent/update_check.json`. All of them now use a
  directory of their own. The best-effort guard from #458 now covers the whole
  `~/.aelix` (a rename or removal of `~/.aelix` itself too) and an exported
  `AELIX_CODING_AGENT_DIR`, and judges reads and listings as well as writes,
  so a test that still reaches your agent directory in the test process fails
  with a `#458/#497` message that names the fix. It still never reads that
  directory itself. It cannot see a check like `Path.exists()`, so a read the
  code makes only when a file exists is caught on a machine that has the file,
  not on CI's empty home. The four tests were why a full run needed a scratch
  HOME to stay out of your `~/.aelix`. The guard still judges nothing a child
  process does.

- **`-p`, `--mode json`, `--mode rpc` and delegated agents now think at your
  `defaultThinkingLevel` (#286).** The setting reached the interactive TUI only,
  so the other modes asked for reasoning off with it set to `high` (an
  openai-codex request went out with `reasoning: {"effort": "none"}`), and so
  did a delegated agent whose profile names its own `model` or `provider` and no
  `thinking:` — a delegation is a `--mode json` run, and it reads the default in
  your global `settings.json` only (a project's `.aelix/settings.json` does not
  reach it). Every mode now starts at, in order: `--thinking` (or the profile's
  `thinking:`), the level the session you reopen recorded, `defaultThinkingLevel`,
  then `off`; a `session_start` extension handler sees that level and can change
  it. The level is fitted to the model instead of being dropped when the model
  lacks it: `xhigh` on a model that tops out at `high` now runs at `high` (the
  TUI used to fall back to `off`), a model that cannot reason runs at `off`, and
  a model that cannot switch reasoning off runs at its lowest level, with a
  default of `off` or none. A launch whose model is decided only after
  `session_start` (an id sent to OpenRouter as written, #367) fits the level to
  the model it ends on, and a launch held on a placeholder keeps your level
  instead of `off` — also a resumed session's recorded level there, which used
  to drop to `off`. What a session records is unchanged, except where a
  `session_start` handler sets the level or the model (below): `-p`,
  `--mode json` and `--mode rpc` write no level for your default, so such a
  session takes the default you have when it is next launched; the TUI records
  your default when it applies it exactly as set, as before; `--thinking` is
  recorded as before, except `off` (a `--continue` after `--thinking off` takes
  your default). `/new` (and RPC `new_session`) keeps the level you are at and
  records it unless it is `off` — now the level your launch started at, in every
  mode; `/model` does not apply your default to the new model; `/reload` still
  goes back to `--thinking`'s level or `off` (follow-ups).

  A `session_start` handler now runs after the level is applied, as in pi; the
  TUI used to apply your default after it, and the other modes never did. With
  no `--thinking` and no recorded level, what a handler does is therefore judged
  against the level the launch started at (your default, fitted to the model)
  instead of `off`. Its `set_thinking_level("off")` over a level other than
  `off` is now recorded (it recorded nothing), and the TUI then runs at `off`,
  where it used to apply your default over it and record that whenever the
  model took the default as set. A start level other than `off`, set again, is
  no change and records nothing, where it used to be recorded — the TUI still
  records your default when that is the level as set, but not a default it
  fitted to the model. Its `set_model` keeps the level the launch fitted to its
  own model, as `/model` does: when that model could not take your default and
  the handler's model can, the TUI no longer runs at your default nor records it
  — a default of `high`, a launch on a model that cannot reason and a handler
  that switches to one that can now run at `off` (pi applies the default again
  on a model switch; a follow-up, with `/model`). And the TUI launch no longer
  fires `thinking_level_select` for your default: the level is set before
  `session_start`, as a reopened session's level is, not selected.

- **Tests only: the bash containment cases no longer fail when Windows is slow
  to start the shell and the Python tree under it (#447).** Nothing users run
  changed. `tests/tools/test_bash_tool_containment.py` failed "the root never
  announced its tree — the case measured nothing" on windows-latest 12 times
  in CI across 6 cases, never on Linux or macOS, each time without measuring
  what the case is about. Two causes. The three timeout cases (1.0 s
  and 2.0 s) counted their deadline from the call, so PowerShell's start, the
  Python root's, its `aelix_ai` import and its children all had to fit inside
  it; when the runner stalled, the kill came before the root existed. Their
  deadline now starts once the root has announced its tree (in the `on_data`
  case, once the first output has also arrived): the test holds the bash
  tool's own timed wait until then, as #388 did for the subprocess helper,
  and measures the call's time minus that hold. So the cases still bound
  everything the tool does before it starts waiting. The hold depends on the
  tool counting its timeout from that wait, and the cases fail by name if it
  starts counting from the spawn instead (#502). The other cases waited for
  the announcement "10 s", but the thread polling for it stopped after 5 s,
  so a marker written at 5.6 s was never seen, its processes were never
  registered for cleanup, and the case failed over a root that had
  announced. Reproduced on macOS with a shell that sleeps 5.5 s before
  running anything: 6 of 6 cases failed with every marker on disk, and three
  60 s helpers were left running. Every wait for the announcement in the file
  now uses #449's 30 s hang guard, stops when the call has already returned,
  and cancels the call when it gives up. Three wall-clock ceilings that also
  included the start-up now count from the announcement instead. A related
  rule was fixed too. The #446 drain-cap check failed one Windows run as "gave
  up 1.173s past its own deadline" when the runner had stopped the test
  process, so the timestamp was late, not the wait. A clock thread now ticks
  next to the wait, and a give-up late across a stretch with no ticks counts
  as a stall; any tick in between leaves the verdict as it was. New tests
  cover the stall with a GIL-holding foreign call on every platform: a rule
  table, the clock thread's two arms and the drain-cap row under the stall.
  The old and the new file ran interleaved on the same windows-latest runners
  (runs 39623623654 and 39624740002: Python 3.11 to 3.14, with no load and with
  four busy loops, 20 to 30 rounds per runner). The old file failed 67 of 1200
  timeout-leg runs (3 of 720 with no load, 64 of 480 under load), and its rule
  failed the new stalled row 400 of 400 times. The new file failed none of
  5600 runs over every case re-run. The hold before the deadline was a median
  0.36 to 0.53 s with no load and at most 4.3 s, mostly PowerShell's own
  start. Once a Python root took 4.73 s to spawn its child, which put the
  marker at 5.73 s, past the old 5 s wait.

- **Tests only: the extension `exec` case with a backgrounded helper no longer
  fails when Windows is slow to start the command (#476).** Nothing users run
  changed. `test_exec_with_a_backgrounded_holder_is_the_success_it_was` checks
  that `exec` comes back once a command that left a helper holding its output
  has exited, without waiting for the helper. Its 2.6 s limit counted from the
  call, so Python's start-up for the command and the helper's launch had to fit
  in it too. On windows-latest it failed once at 3.875 s with every result
  right, and under eight busy loops in 7 runs of 1080, each time because the
  command finished late, not because `exec` waited. The case now times from
  the flag the command writes as its last action, and allows the 2.0 s cap on
  that wait plus half a second, the same allowance the drain's cap cases use.
  The start-up waits under the 30 s hang guard instead, and the message says
  when the command announced itself, when it exited and when `exec` came back.
  On macOS a command that starts 2.5 s, 6 s or 9 s late no longer fails it,
  and a wait past the cap (5 s, 30 s, or until the helper exits) still does.
  On windows-latest the old case and the new one ran interleaved on the same
  runners (run 39635012356, Python 3.11 to 3.14). With no load neither failed
  in 600 runs. Under eight busy loops the old one failed 12 of 1080 runs, each
  time with the command's exit 1.9 to 3.2 s after the call, and the new one
  none of 1080; there `exec` came back a median 0.42 s after the command's
  exit, and at most 0.88 s.

- **Tests only: the subprocess hook cases no longer fail when Windows is slow
  to start the hook (#474).** Nothing users run changed.
  `test_a_hook_cancelled_inside_the_timeout_teardown_still_loses_its_tree`
  failed on windows-latest with "tree.pid never appeared within 15.0s". The
  hook's 1.5 s timeout counted from the spawn, so `cmd.exe`, the Python root
  and the root's own child all had to start inside it. When the runner was
  slow, the timeout's soft signal ended the root before it ran a line, and the
  case then waited 15 s for a file nothing would write. The timeout cases now
  start the hook's timeout once its root has written that file. The test
  holds the hook's own timed wait until then and checks that the wait still
  got the caller's whole timeout. The in-teardown case now cancels once the
  teardown is waiting, and checks from the recorded waits that the cancel
  landed in the 1.0 s grace, instead of cancelling 1.8 s after the call. The
  wait for the file looks once more after its limit: before, a test process
  held up past the limit failed over a file that was on disk, which
  reproduced the CI run's "hook still open when the loop closed" warnings on
  macOS. It now waits up to the 30 s hang guard, stops if the hook has
  already returned, and on a miss cancels the hook and waits for it. Three
  sibling cases with a 1.5 s or 3 s timeout get the same treatment. On
  windows-latest the old file and the new one ran interleaved on the same
  runners (run 39644523307, Python 3.11 to 3.14). With no load neither failed
  in 720 runs. Under eight busy loops the old file failed 9 of 1080 runs with
  the CI message, each time with the soft signal before the root's first
  line, and the new one none of 1080; there the root's first line came a
  median 1.2 s after the spawn. The same measurement found a cost in the
  product, which is not changed here: on Windows, killing a hook's process
  tree runs `taskkill.exe` on the event loop and waits for it. That stopped the
  loop for about 0.05 s with no load, but a median 1.3 s and up to 3 s under
  load, so a hook that times out or is cancelled can freeze the interface for
  a second or more. It is recorded as open in ADR-0238.

- **Tests only: the `run_contained` case whose command leaves a helper behind
  no longer fails when Windows is slow to start the command or the helper, and
  it now says how the wait after the command's exit ended (#503).** Nothing
  users run changed. `test_an_exited_root_with_a_pipe_holder_is_a_success_and_keeps_the_tail`
  checks that `run_contained` comes back soon after a command exits without
  waiting for a helper the command left holding its output, and keeps the line
  the helper writes just after the exit. Its 2.6 s limit counted from the call,
  so Python's start-up for the command and the helper's launch had to fit in
  it too. On windows-latest it failed once at 2.718 s with every result right,
  and its log could not say whether the command had finished late or the wait
  had run to its 2.0 s cap. The case now times from the flag the command
  writes as its last action, as #476 did for the same command through `exec`:
  the 2.0 s cap plus half a second. The start-up waits under the 30 s hang
  guard instead. On every run the case also reports, from the wait's own
  record, whether it ended quickly, at its cap or past it, with the time of
  each stage. The helper had a limit of its own: it had to start within about
  half a second, or its line came too late and both cases failed. The command
  now waits for the helper to say it has started. On macOS a command started
  2.5, 6 or 9 s late and a helper started 1 or 3 s late no longer fail the
  case, and a wait past the cap still does. On windows-latest the old case and
  the new one ran interleaved on the same runners (run 39657167068, Python 3.11
  to 3.14). With no load neither failed in 600 runs. Under eight busy loops
  on the runner's four CPUs neither failed in 1080 runs either, so that load
  does not reproduce the CI failure. Every wait after the exit ended quickly
  and none at its cap. The new case came back a median 0.30 s after the
  command's exit, and at most 0.52 s. The helper started a median 0.44 to
  0.49 s after the command and at most 1.04 s, more than the half second the
  old command left it.

- **Tests only: the bash drain cases that kill a command, or need it to finish
  inside a deadline, no longer fail when Windows is slow to start the command,
  and they say so when the start-up is what went wrong (#504).** Nothing users
  run changed. `test_a_kill_leg_whose_drain_starts_late_finds_the_readers_proof_waiting`
  failed once on windows-latest with "the root's own line: []" and 0 bytes
  delivered, which reads as lost output. Its 2.0 s timeout had to cover the
  start of PowerShell and of Python, and the kill came before the command had
  written anything. The case now starts that timeout only once the command
  has written its line and reported its process ids, so no start-up is inside
  it. A command that never starts fails as that. A second kill case in the
  same file had the same 2.0 s timeout. On macOS and Linux it failed, when the
  start was slow, as a wait that ended too early; it now waits the same way.
  Both timeouts are now 1.0 s on every platform. The case that aborts its
  command, like every case in the file, waited only 5 s for the command's
  process ids, and failed once on windows-latest for that reason. They now
  wait under the 30 s hang guard. The case that needs the command to exit
  inside its deadline failed as `assert None == 0` when the command was still
  starting at the deadline. It now recognises that from the timed wait's own
  record, says so, and runs again, up to three times, as its counterpart in
  the containment cases does. On macOS, with the command started 1.5 to 6 s
  late, each old case failed every time (4 or 5 runs each), and none of the
  new ones did. On windows-latest the old file and the new one ran
  interleaved on the same runners (run 39666366493, Python 3.11 to 3.14).
  With no load neither failed in 280 runs of each case. Under eight busy
  loops on the runner's four CPUs the command took a median 5 s to start, and
  up to 9.4 s. The old kill case then failed all 280 runs with the CI's own
  message, and the old abort case 176 of 280; the new ones failed none. Under
  that load no command could finish inside the 2.0 s deadline, so the
  deadline case failed every time in both files, the new one saying why. The
  new file also ran whole six times per runner (run 39668047399). With no
  load every run passed. Under the loops every run failed only that deadline
  case, by name.

- **Tests only: the bash drain-cap case's deadline arm no longer fails when
  Windows starts the shell too slowly to finish inside the deadline (#446).**
  Nothing users run changed. `test_a_holder_that_never_falls_idle_hits_the_drain_cap[deadline-arm]`
  needs a root that exits inside its 2.0 s deadline. On windows-latest under
  CPU load, PowerShell plus its Python child sometimes did not: 31 of 900
  loaded runs timed out and 0 of 450 unloaded ones (run 38509857375). The
  misses were mostly start-up waiting for a CPU (10 of 13 instrumented ones),
  2 were PowerShell's teardown and 1 an exit at the deadline itself; none was
  the product failing to see the exit. Such an attempt now runs again, but only
  when the site's own timed wait was handed the caller's timeout and gave up
  at its deadline - not before the caller's, and not more than half a second
  past its own - on a root that had not exited. Anything else still fails at
  once, each with its own message: a wait handed another timeout, one giving
  up early, one giving up late against its own deadline, a timeout the wait
  never raised, a root that had exited before the deadline, and a record that
  cannot place the exit. A wait that began late is not judged for it. The
  site counts the timeout from where its wait begins, after the spawn and the
  start of the pipe reader and of the waiting worker thread, and on a loaded
  Windows runner those starts wait for a CPU: up to 0.216 s after the spawn
  under four busy loops (run 38509857375), and once a wait began 1.344 s after
  the call in a CI run, which the previous revision of this change failed as
  the site's fault. The record cannot tell that from a site that starts its
  wait late, so this case does not catch a site that gives the shell longer
  than the caller's timeout. On macOS the root's state comes from `ps`, so
  with `ps` missing or hung an exited root reads as running and such an exit
  would be taken as the premise. Whether the timeout's kill reaches the tree
  is not this case's subject: the dedicated containment cases pin it. On a
  premise attempt the case only checks, for up to two seconds from when the
  call has come back and the announcement wait has settled, that the root and
  the python root it announced are dead (on Windows also the holder, which
  sits inside the job). That is a sanity check and proves nothing about the
  kill: it passes a timeout leg that kills nothing when the root then exits by
  itself, and any missed process that exits while it polls, and it never
  looks at a process that did not announce. The drain-cap checks the case
  exists for still run, on the attempt that measured. New cases drive this
  path on every platform: one with a root still running at the deadline, and
  two against a timeout leg that kills nothing and leaves the root running,
  one of them with the site's wait started 1.5 s late. A larger deadline was
  not taken: the measured exits spread wider than the window the arm needs.
  The arm's floor now compares the product's own sum,
  after a Python 3.12 Windows run failed `2.0 <= 1.9999999999999432` on a
  rounding tie, and a call back at half the deadline is pinned to fail it.
  Measured on windows-latest with an earlier revision of this change (run
  38547416538, 100 rounds per Python version and load): 0 of 900 deadline-arm
  runs failed; 10 attempts met a root still running at the deadline and ran
  again. The old test failed 16 of 900 in an earlier run (38531413887).
  A root that misses the deadline on all three attempts still fails the case,
  which says so.

- **Tests: the subprocess helper's timeout case no longer fails on a slow
  Windows child start (#388).** Test-only; nothing users run changed. The case's
  0.5 second timeout now starts once the child has announced its pid, and a
  child that never announces is reported as a premise failure, not a failed
  kill. The cancellation case's pid file is written atomically and read only
  when whole.

- **Tests only: the RPC bash cases no longer fail on a slow Windows shell start,
  and a slow call now says where its time went (#449).** Nothing changes for
  users. `tests/rpc/test_rpc_mode_bash_abort.py` awaited each real `_handle_bash`
  call under a 5 s `wait_for`, and once on windows-latest `echo hello` did not
  finish inside it. Measured there phase by phase (run 38439411854, 3000 calls),
  the product's own steps stayed under 140 ms; the time was PowerShell's — a
  bare `pwsh -NoProfile` with no Aelix code in the path once took 5.97 s from
  process creation to its first statement. An ordinary call now waits under a
  30 s hang guard. An aborted call keeps a bound, because there the time is the
  property: it waits for the shell's first output, aborts the running
  `sleep 30`, and must return within 15 s of the abort — an abort that kills
  nothing returns ~30 s after it, and `cancelled` alone cannot tell. Any call
  slower than 2 s warns with every phase it went through (spawn, executor pickup,
  the shell's first output, the child's exit on win32, the reap, the kill ladder
  after an abort as a phase of its own, the drain), naming the slowest; an
  expired bound fails with the same timeline, through `tests/event_waits.within`,
  so a `TimeoutError` the call raises itself is still reported as one.

- **Tests only: the bash abort cases get the same treatment, so a slow Windows
  shell start no longer fails them either (#471).** Nothing changes for users.
  `tests/tools/test_abort_signal.py` awaited its ordinary `echo` calls under a
  bare 5 s `wait_for`, and twice on windows-latest that ran out inside
  `proc.wait` with only a `TimeoutError` to show (py3.13, and py3.11 in the case
  after it); its abort and cancel cases had 5 s after the abort or cancel and a
  10 s wait for the shell to start. Those eight cases now go through #449's
  `BashPhases`; the file's two #234 cases keep their own 60 s bounds without
  it. An ordinary call, a cancelled one, the abort 0.2 s into `true` and every
  wait for a start run under the 30 s hang guard. An aborted call keeps a
  verdict bound and must return within 15 s of the abort, because an abort
  that kills nothing returns ~30 s after it with `exit_code=None` all the same.
  That bound checks the return, not that the command ended, which
  `test_bash_tool_containment.py` checks. An abort up to ~15 s late now only
  warns here, where the 5 s bound failed it: a working abort on windows-latest
  once took 5.17 s. A bound that runs out names the phase in flight, and a
  call or a start slower than 2 s warns with its timeline. The helper's start
  wait can now wait for a case's own event (its `started` line reaching
  `on_data`, a pid file), report up to it, and stop at once when the call has
  already returned. An expired guard is now stamped when its bound fires, so
  the kill its cancellation starts no longer takes the blame. On windows-latest
  the old file and a pre-final version of the new one both passed 240 of 240
  rounds (run 38502661220), the shell's start taking a median 0.38 s and at
  most 2.48 s (old) and 1.55 s (new). In run 38508575549, on the version before
  the review's helper fixes, an injected 5.5 s start, 5.5 s reap or 6 s
  kill-ladder stall failed the old file in every job and the new one in none.

- **`install.sh` finds the uv it just installed wherever uv put it (#279).**
  With uv absent, the installer bootstraps it with Astral's installer and then
  looked for it only in `$XDG_BIN_HOME` or `~/.local/bin`. Astral's installer
  also honours `UV_INSTALL_DIR` and puts uv in `$XDG_DATA_HOME/../bin` when
  `XDG_DATA_HOME` is set, so with either of those the install stopped with
  "uv still not found after install" and told you to add a directory uv was
  never in. It now looks where Astral's installer does, in the same order,
  takes the first uv it finds there (an old `~/.local/bin/uv` no longer wins
  over the one just installed), and if uv is still missing the error lists
  every directory it checked.

- **CI now runs `install.sh` end to end on Ubuntu and macOS (#279).** The
  `install.sh e2e (ubuntu-latest)` and `install.sh e2e (macos-latest)` jobs run
  the checked-out `install.sh` on runners with no uv. That covers both `sh`
  dialects users hit: dash, and bash in POSIX mode. Each job installs five
  times. The first two use the README's `curl … | sh` line: the uv bootstrap,
  then a re-run with uv present (the upgrade path). The last three use the
  getting-started guide's `VAR=… sh -c "$(curl …)"` form: `AELIX_EXTRAS=`
  (the bare CLI), `AELIX_PYTHON=` empty and `AELIX_PYTHON=3.11`. Python 3.14
  is installed before the third pass, so the last three run with it on the
  machine (on macOS, Homebrew's 3.14 is on `PATH` for all five). After each
  pass, `.github/scripts/assert-install-sh.sh` checks that uv's receipt was
  rewritten since the previous pass's check (from the second pass on), that
  the `aelix` on `PATH` is uv's launcher, that `aelix --version` matches the
  verified `SHA256SUMS`, that the interpreter and extras are the ones
  requested, and that the download directory is gone. Until this change only
  `install.ps1` was executed in CI, and the 3.11–3.13 interpreter promise from
  #263 was checked on Windows only. The `install.ps1 e2e` jobs now have a 15-minute timeout,
  and every CI job is required to declare one.

- **The automatic retry follows `settings.json`'s `retry` block (#408).**
  `retry.enabled`, `retry.maxRetries`, `retry.baseDelayMs` and pi's
  `retry.maxAgentDelayMs` (the longest wait, default 60 s) now apply to every
  retried turn - a provider error and a `/login` refresh that failed on a
  transient cause alike - in the TUI, `-p`, `--mode json`, `--mode rpc` and a
  delegated agent's run (which reads the global file's values only), read at
  each retry so `/reload` applies an edit. Until
  now nothing read them: `"enabled": false` still retried, and every retry ran
  three times at 2 s, 4 s and 8 s. No guide claimed these keys worked; the
  providers guide now documents them. rpc `set_auto_retry` now writes
  `retry.enabled` into the global `settings.json`, as pi does, and `get_state`
  reports the value in force. With `maxRetries: 0` no `auto_retry_end` is sent.
  An `abort`, `abort_retry` or an extension's `ctx.abort()` ends a wait of any
  configured length at once, and a `0` ms wait still honours an abort made as
  the retry starts; a float `baseDelayMs` never raises, however many retries;
  `set_auto_retry` on a global `retry` that is not an object replaces it with
  `{"enabled": ...}`. The TUI countdown rounds up, as pi's does (a 500 ms wait
  shows `in 1s`). `retry.provider.*` is still not read.

- **A new session records the model it starts on (#413).** Like pi, a session
  created with no conversation — a launch, `/new`, a `/fork` before the first
  message, a delegated child's first run — writes a `model_change` naming its
  model when it is created: before the extensions' `session_start` handlers and
  before `new_session`'s `setup`, so a turn a handler starts or a message
  `setup` writes lands after it. A session whose answers all failed before
  reaching a model (an extension hook that raised) now reopens on that model
  instead of the settings default, also when it was launched with no key and
  `/login` came later. Carve-outs: a launch whose route is decided only after
  `session_start` (an id sent to OpenRouter as written, #367), and a `/new`,
  fork, switch or reload in such a session, records once that decision is made,
  after the messages written by then (a `setup` seed, a turn `session_start`
  started — on such a launch one the turn gate refused) — and not at all when
  one of them already names its model (a seeded answer), so the session reopens
  on that answer as in pi; a held or refused model is never recorded; reopening
  a session with a conversation writes nothing; an empty session reopened on
  another model records that one; the thinking level is not recorded alongside
  it (#286 left that as it was). A session file that cannot be written runs as
  before (its prompts ran and wrote nothing) and now also prints a `Warning:` on
  stderr; with `--thinking` it still exits 1, as before. `/clone` and RPC `clone` still
  refuse a session with no conversation, in the same words, on disk and with
  `--no-session` — now also one with no conversation but other entries (only
  a name, a `/model` pick, `!cmd` output or a `custom_message` entry an
  extension wrote), which they used to clone on disk (pi refuses such an
  unsaved session).

- **On Windows, a command's output reaches the agent faster while the event loop
  is busy (#381).** The bash tool's reader thread released Python's lock twice
  for every ~5 KiB it read (once to look at the pipe, once to read it), and
  behind a busy event loop each release cost it 6-10 ms to get back: 2 MiB of
  output took 9-14 s at the median on Python 3.11/3.12 instead of 0.3-0.5 s. The
  look now keeps the lock: it is made on the reader's own handle to a pipe the
  bash tool or a contained run has just spawned and that nothing else reads, and
  such a look cannot wait. The read still releases it, because the bytes a look
  counted can leave the pipe when the command writing them is killed, and a read
  that kept the lock would then stop every thread. POSIX is unchanged, and so is
  every drain and delivery guarantee. Tests: the delivery case that sat at
  10-17 s of its 20 s budget on `windows-latest` with nothing else running now
  takes 4.6-7.3 s (py3.11), 6.3-7.7 s (py3.12) and 1.2-1.8 s (py3.13) at the
  median over three measure runs, and 10.8 s at worst, against 12.0-14.2 s at
  the median on py3.11/3.12 for the old reader; paired round by round on one
  machine, the old reader took 1.7x as long on py3.11/3.12 and 1.8x on py3.13.
  The budget did not move, so the worst run kept about 1.86x headroom.

- **Picker caller text is safe before styling (#179).** Select option/detail and
  tab name/body/error controls are displayed by name, including SOH/STX that
  prompt-toolkit treated as raw zero-width escapes. Width and filtering use that
  display text; original values, detail indices, cancel behavior and #399 title
  hold remain. Internal stats dim notes and Installed-tab built-in rows retain
  their style through private structured fragments.

- **Select-only embeddings cannot approve built-in file changes (#395).**
  Prompted `write` and `edit` calls now refuse when no file-change preview
  approval runner is wired, before a picker or extension-tier redirect can
  approve content the host has not shown. Wire the existing approval runner to
  review the full change. The TUI already wires it; automatic, session and
  headless authorization keep their prior policy. Bash and third-party tool
  select fallbacks remain available.

- **Descriptor views display Rich markup literally (#390).** Titles, table
  headers and cells, form names and values, grid items and text bodies preserve
  tag text and link targets without creating OSC 8 hyperlinks. Existing terminal
  cleanup still runs after raw JSON projection and key lookup. Shared descriptor
  builder callers also display markup literally; clean ANSI component links
  retain their existing validation and behavior.

- **Extension provider models own their route (#365).** An explicit `models`
  map replaces that provider's catalog/JSON rows; `{}` stays empty and `None`
  keeps auth-only augmentation. Launch and `/model` share the owned models and
  safe sibling backfill. Requests refuse stale routes or Model headers before
  resolving credentials, and a pure closing check cancels changes made during
  request/payload hooks before the SDK sends. Print/RPC children require the same
  explicit owner and route through startup, hooks and rebuilds.
  Fatal refusal also ends loop execution before tools or queued inputs can
  continue, independently of message replacement; pending inputs are retained.
  Extension keys now precede
  the environment at configured step 3; JSON provider headers merge with
  extension headers and explicit `auth_header=False` overrides JSON's True.

- **Extension install/remove no longer automatically run a cwd `pip/` package
  (#394).** Every pip child uses `python -P -m pip`, including verification
  downloads and installation of their verified bytes. Typed relative paths keep
  their cwd meaning; pip configuration and the user's Python environment,
  including `PYTHONPATH`, still apply. The uv backend is unchanged.

- **`/agents run` output cannot send child-authored terminal controls (#407).**
  Summary, error, profile, status, permission, model/provider and dropped tool
  names use the shared terminal sanitiser and existing BiDi format-control strip.
  Resolution and spawn errors are sanitised too. Normal multiline output and
  Aelix's own colours remain; child-authored Rich markup is displayed literally.

- **CI hangs now fail within a bounded time and report Python thread stacks
  (#429).** Each lint/test job's job limit is 40 min on Ubuntu and 60 min on
  Windows, and the pytest step limit is 35 min on Ubuntu and 55 min on Windows
  (#482 raised them from 25/50 and 20/45). A test stuck for 240 seconds exits
  with a traceback; a separate 180 second watchdog covers interpreter exit
  after the test summary. Development now requires pytest 9.0 or newer.
- **CI: a green test leg no longer fails on its pytest step's time limit
  (#482).** The suite had outgrown #429's limits. Over 129 `ci.yml` runs, the
  slowest green pytest step took 19.5 min on Ubuntu and 40.0 min on Windows,
  against step limits of 20 and 45. Run 38610624957 was killed on the
  20-minute limit with no test failed or stuck; projected from its progress
  lines, it would have taken about 25.3 min on Ubuntu. The rule is the slowest
  of these x 1.3, rounded up to 5 min: the pytest step limit is 35 min on
  Ubuntu and 55 min on Windows. The job limit is 5 min more: the job limit is
  40 min on Ubuntu and 60 min on Windows. The 240-second per-test watchdog is
  unchanged. `tests/test_ci_hang_guard_429.py` pins the measured times, the
  rule, the limits and the sentences that state them (ADR-0274). Making the
  suite faster is a separate follow-up.
- **Tests: the hang-guard gate's child pytest no longer slows down late in the
  suite (#448).** The child ran with the repository's pyproject, so pytest
  collected every directory above the child's temporary directory, including
  the parent run's basetemp with about 20,000 directories by then. On Windows
  that collection took 5-20 s and the gate's 15 s budget was exceeded.
  `--confcutdir` now stops the child at its own directory, and the gate runs in
  1.7-2.1 s in the full Windows suite. A new test checks that the child collects
  nothing above that directory. Users see no change.
- **Quitting during file completion no longer leaves aelix running (#428).**
  The `@file` menu is collected in one background job, so repeated cancellation
  during shutdown cannot leave a second worker waiting forever on a queue.
  Slash commands and ordinary text no longer start a file-completion worker;
  file-completion values and ordering stay the same.
- Clear the retry indicator as soon as the recovered assistant response ends,
  including a response that calls tools. Remaining tool work and later responses
  keep running, and a later provider failure gets a fresh retry budget. (#197)
- **A git URL's query or fragment can no longer impersonate a commit pin
  (#402, ADR-0187).** Verification reads only the URL path's final revision.
  `git+URL@main#x=@<sha>` remains unpinned and is refused by `--strict`;
  repository pin identities preserve query and fragment contents.
- **A `/login` subscription whose token refresh hits a passing failure is
  retried instead of failing the turn (#379, ADR-0251).** When an OpenAI
  Codex, Anthropic or GitHub Copilot login's access token had expired and the
  refresh got a `429`, a `500`, `502`, `503`, `504`, `520` or `524`, a dropped
  connection or a timeout from the token endpoint, the turn failed at once
  with `OAuth refresh failed for <provider>: …` — in the TUI, `-p`,
  `--mode json`, rpc and a delegated agent's run alike — although the same
  `502` from the model would have been retried. It is now retried the way that
  `502` is: up to three retries, 2 s, 4 s and 8 s apart, `⟳ Retrying (1/3) in
  2s…` in the TUI and `auto_retry_start` / `auto_retry_end` in `--mode json`
  and rpc. The statuses are exactly the ones pi retries; a `501`, a `505` and
  any other status fail at once. While your stored login exists, each retry
  refreshes again, nothing is sent until a refresh succeeds, and nothing is
  sent on an exported key or a `models.json` key instead of your login. If
  another aelix logs you out during the wait between retries, the retry sends
  nothing and ends with `The auth.json entry for <provider> is an OAuth login
  that gave no key …`, and the next turn uses the next key; if the logout lands
  while the failing refresh is still under way, the retry already uses that
  next key, as a new turn would. Once an error status (not a `2xx`) has
  arrived, that status decides, whatever then happens to the body (a broken
  `gzip` encoding, a body cut short, a read that times out): a refused `400`,
  `401` or `403` fails at once, a `429`, `500`, `502`, `503`, `504`, `520` or
  `524` is retried, and any other status fails. A `2xx` whose body is cut
  short or times out is retried as a dropped connection; a `2xx` with a
  broken encoding fails at once. For an answer whose body fails mid-read,
  pi's behaviour depends on the provider and the connection framing (its
  Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot
  refreshes do not), so aelix's rule above can differ from pi for these
  malformed answers. An extension's `message_end` handler that rewrites the
  error text changes no retry decision.
  The retried error reads `OAuth refresh failed for <provider>: <cause>`, as
  pi's does, without the `Run /login` hint. A refresh the endpoint refuses —
  `400`, `401`, `403` (`invalid_grant`, a revoked login) — still fails at once
  with the message it had, as does every other unusable `auth.json` entry (a
  refusal whose body cannot be read now gets the text the same status with an
  empty body gets, where it used to quote the decoder's or the connection's
  error); one refused after a retry (a refresh token used up by a refresh whose
  answer was lost) ends the retry with that same message, `✖ Retry failed: …` in the TUI
  and `auto_retry_end` with `success: false` in `--mode json` and rpc, and the
  next turn gets its full three retries again. One visible change: when every
  retry fails, or a refusal ends one, the error is in the last message, as it
  is when a model's error ends a retry; `-p` exits 1, and so does `--mode json`
  since #410 (under Changed).
- **A session you reopen without `--model` comes back on the model it last ran
  on, and a prompt that cannot run is no longer written into it (#376).**
  `aelix --continue` (also `--resume`, `--session`, `--fork`, and `/resume`,
  `/fork`, `/clone`, `/import`, `/reload` and RPC `switch_session`, `fork` and
  `clone` inside a session) did not restore the session's model. With no
  settings default the session had no model at all: RPC's `get_state` showed an
  empty model, every prompt failed with `No provider registered for
  api='unknown'` and was still written into the session, and `/compact` and
  branch summaries failed the same way. With a settings `defaultModel` it
  silently switched: a session that ran on `claude-haiku-4-5` resumed on your
  default instead, in every mode. The model is now restored — whichever the
  session recorded last: a `/model` pick, or the model that wrote an answer —
  unless you pass `--model`, `--provider` or `--api-key`, or an agent profile
  names a model, as in pi. A `/model` pick is now recorded when you make it, as
  pi records every pick, so `/model` then quitting comes back on that model; a
  model the session is only held on (the late-provider placeholder) is never
  recorded. The model comes back only if it can still run: a model this build,
  your `models.json` or an extension knows, with a key for its provider from
  any source. A model id nothing lists comes back as a custom id on a provider
  you defined (in `models.json` or an extension) when that provider has any
  key, as `--provider <it> --model <id>` would at launch; on any other
  provider — OpenRouter, where the launch sends such an id as written, or a
  built-in provider whose catalogue does not list it — only on a key of your
  own, never one from a project `.env` (for a built-in provider that is
  stricter than typing the same `--provider`/`--model` at launch). Otherwise
  aelix uses what it would have picked without the session and says `Could not
  restore model <provider>/<id>. Using <provider>/<id>` — the `Using` half
  naming the model the run is on once the extensions' `session_start` handlers
  have run, and only when that model has an adapter and a key — under the
  banner in the TUI and after each of the in-session commands above, on stderr
  in `-p`, `--mode json` and RPC (at startup and after `switch_session`, `fork`
  and `clone`). The thinking level the session recorded is now clamped against
  the model it came back on. A prompt on a model that certainly cannot run
  for a reason that is not a credential — no model at all (the empty model
  above: no provider and no adapter), one this build has no adapter for
  whatever its base URL (`mistral/...`, for one), or one with no base URL
  (none declared, or a `{NAME}` placeholder in it left unset, such as
  `CLOUDFLARE_ACCOUNT_ID` for `cloudflare-workers-ai`, even with a key set) —
  is refused before anything is written to the session, in every mode (RPC
  answers `success: false`, `-p` and `--mode json` exit 1). As in pi, that is
  asked after the
  extensions' `input` handlers ran: an input an extension handles itself is
  still answered, and a model an `input` handler switches to is the one
  judged; a turn an extension triggers with `send_message(...,
  trigger_turn=True)` is refused the same way (while the extensions'
  `session_start` handlers are still running after a launch that resolved no
  model, the late-provider hold answers such a turn first, as before: its
  message is written with the hold's error). No key, auth header, token or
  Google Cloud setting is asked: a prompt whose provider has no key is handled
  exactly as before — in the TUI and RPC it is sent and fails with `No API key
  for provider: <provider>`, and `-p` / `--mode json` refuse it at startup as
  they already did — so an auth header in `models.json`,
  `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_AUTH_TOKEN`, Vertex Application
  Default Credentials, or a Vertex key from `--api-key`, `auth.json` or
  `models.json` keep working, and an RPC prompt on Vertex with no Google Cloud
  setup is written and fails with the adapter's own error, as before. Known limit: a session whose only auth is
  one of those is not restored to its model — it falls back and says so. A mid-turn
  model change was recorded with the model's protocol (`anthropic-messages`)
  where its provider belongs, and now records the provider.

- **On Python 3.13 — the interpreter the installers give you — a TLS failure
  now gets the advice for what actually failed, and CI now runs 3.13 (#192).**
  3.13 turns on OpenSSL's strict RFC 5280 checks, and aelix's remedy text
  treated *every* verification failure on a strict interpreter as a strict-only
  one unless it was one of four untrusted-issuer codes: a certificate with a
  genuinely bad signature, an issuer that is not a CA, or an error that kept
  only its text ("unable to get local issuer certificate", the shape a corporate
  CA that is not installed produces) were all told "an RFC 5280 rule, not trust
  — the same host works in every other tool; reinstall on Python 3.12". Only
  the codes that always mean strict mode (78 and 80–94) get that advice now;
  everything else gets the advice for its own failure — install the CA or set
  `SSL_CERT_FILE`, check the clock, check the hostname. Code 79 (`invalid CA
  certificate`) gets both: OpenSSL raises it for an issuer marked `CA:FALSE`
  with or without strict, and only under strict for a root CA with no Basic
  Constraints extension, so on a strict interpreter the advice names both
  causes and the `openssl s_client` check that tells them apart. Nothing had
  caught it because the test matrix was Python 3.11 and 3.12 while
  `install.sh` / `install.ps1` build on the newest of 3.11–3.13, i.e. 3.13: the
  suite failed 11 tests on 3.13 at `8f7d98aa` — nine on this defect, and two
  that asserted they were running on an interpreter older than 3.13. CI now
  runs the full suite on 3.11, 3.12 and 3.13 on both Ubuntu and Windows, and a
  test holds the matrix equal to the installers' interpreter range. The TLS
  guide's table also named the wrong code for "Missing Authority Key
  Identifier": it is 85, not 95. And in the TUI every TLS remedy now shows
  whole: the TUI keeps 8 lines of an error and cuts each at 200 characters, and
  the hostname, clock, trust-store and strict remedies each had a sentence past
  that cut — the hostname and clock ones lost their only action ("check this
  provider's base URL", "check this machine's clock"). The note a session gets
  once strict checking has been relaxed for it now names the host and the
  failed check on lines of their own, so it too shows whole for any host name
  of up to 576 characters (DNS allows 253) and any verify message of up to 187
  (OpenSSL's longest is 68); with both in its first sentence it ran past 200
  for hosts such as `api.business.githubcopilot.com`.

- **A timed-out hook and a stopped delegation no longer leave processes
  running on Python 3.13.15 and later (3.14.7 and later on 3.14) - and on
  every Python version a timed-out hook now also loses a helper it
  backgrounded that still holds its output (#192).** Both give what they are
  stopping a grace after the polite signal and kill the whole process tree
  only if the grace runs out. They ended the grace when Python said the
  process had exited - and from CPython 3.13.15 and 3.14.7 (CPython's
  gh-119710; 3.14.5 and 3.14.6 do not have it) Python says so as soon as the
  FIRST process exits, not once nothing holds its output open any more. A hook
  run through a shell that died on the signal while its command ignored it
  (`cmd && …`), or a delegation whose child had left a helper behind, then had
  its grace end at once and the rest of the tree was never killed. The grace
  now ends only when nothing holds the output open, on every Python version.
  For the delegation that is what it already meant before 3.13.15. For a hook
  it is stricter than before on every version, 3.11 and 3.12 included: a hook
  that backgrounds a helper which keeps the hook's stdout or stderr open never
  reaches the end of its output, so it times out - and because its own shell
  had already exited, the grace used to end at once and the helper kept
  running. Now the grace runs out and the helper is killed with the rest of
  the hook's tree, as for any timed-out hook. A helper that sends its output
  elsewhere (`helper >/dev/null 2>&1 &`) does not hold the hook open, so the
  hook finishes and the helper keeps running, as before. CI's first run on
  3.13 found the leak; a local 3.13 suite had passed because 3.13.13 and
  3.13.14 do not have the change.

- **On Windows, a path without a drive letter now means the same thing on
  every Python version (#192).** The uv executable `aelix extension install`
  uses, `PIP_CONFIG_FILE`, `UV_CONFIG_FILE` and a typed `--index-url` were
  checked with Python's own "is this path absolute?", which on Windows answers
  yes for `\tools\uv.exe` up to Python 3.12 and no from 3.13. Such a path is
  relative to the current drive, so it is now treated as relative everywhere:
  a uv found through a drive-less `PATH` entry is not used, and a drive-less
  config file or index directory is read from the directory the installer runs
  in (or, for `--index-url`, the one you typed it in).

- **A delegated agent now thinks at the level you are thinking at, so a model
  that cannot switch reasoning off no longer kills every delegation (#354).**
  A child ran at the thinking level its profile set and nothing else — and none
  of the bundled profiles sets one — so it started with reasoning off. On a
  model whose provider refuses that (`openrouter/z-ai/glm-5.3-flash`, for
  one), every `agent` call and `/agents run` failed on its first request with
  `400 Reasoning is mandatory for this endpoint and cannot be disabled`, while
  your own turns on the same model worked. A child that runs on your model —
  its profile sets neither `model` nor `provider` — and whose profile sets no
  `thinking:` now also runs at your current level, whatever set it:
  `--thinking`, an `--agent` profile, a resumed session, `/thinking`, an RPC
  `set_thinking_level`, or `defaultThinkingLevel` (in every mode since #286).
  A profile's own `thinking:` still wins. A profile that names its own model or
  provider gets no level from you, as in pi: it runs at its own `thinking:`, or
  at the `defaultThinkingLevel` in your global `settings.json` fitted to its
  model (#286; with none set, `off`, or the model's lowest level when it cannot
  switch reasoning off) — give it a `thinking:` if your default does not give
  its model the level it needs.
- **A cloned repository's `.aelix/settings.json` no longer chooses where your
  prompt goes when you have not trusted it (#369, ADR-0252).** Its
  `defaultProvider`/`defaultModel` pair, with an `OPENAI_API_KEY` in its
  `.env`, sent a plain `aelix -p hi` to `api.openai.com` on the repository's
  key while your own `OPENROUTER_API_KEY` was exported — with `--no-approve`
  too, because the file was read whatever the trust answer. An untrusted
  directory's settings file is now neither read nor written. Nor is a
  repository's pair laundered into your own defaults any more: the model
  picked after `/login` starts from your global saved default only, where it
  used to pick the repository's pair and save it to your global settings, from
  which it chose the model in every other directory. In a directory you
  trusted the repository's settings still apply, its pair included (a `.env`
  key may then authenticate it), as pi treats them.
- **A key in a cloned repo's `.env` no longer chooses where your prompt goes
  (#362, ADR-0250).** With `ANTHROPIC_API_KEY` exported and a repo's `.env`
  carrying `OPENROUTER_API_KEY`, `--model anthropic/claude-haiku-4-5` went to
  `openrouter.ai` on the file owner's account — the prompt and whatever your
  tools read — although you held an Anthropic key of your own; a `.env`
  `OPENROUTER_API_KEY` also sent bare ids, unknown ids and a `.env`
  `OPENROUTER_DEFAULT_MODEL` there. aelix now records which names a project
  `.env` supplied, and every decision that picks a provider counts only
  credentials you exported, stored with `/login`, typed with `--api-key` or
  wrote into `models.json`: `--model`, `/model` (which also persists its
  choice; while you hold a key of your own, a provider only the `.env`
  authenticates never decides where `/model <id>` goes — even the one the
  session is on — and where only such a provider could serve it, `/model`
  refuses and says so; a prefix naming a provider you defined stays in it, on
  a `.env` key if that is where its key is, as `--model` does — a re-pointed
  `openrouter` included; the built-in `openrouter/` prefix is not exempt, so
  `/model openrouter/<id>` on a `.env`-only OpenRouter key is refused where
  `--model` runs it), the model picked after `/login` (a saved default
  that only the `.env` authenticates is skipped; since #369 only your global
  one is read there), RPC `cycle_model` for a program that runs RPC
  mode with a model registry (`set_model` names its provider and is
  unchanged; `aelix --mode rpc` wires no registry — since #371 it does,
  above), delegated agents and an `aelix` started
  from the bash tool. "A key of your own" counts one held for any provider,
  including a provider that lists no models. A project `.aelix/settings.json`
  `defaultProvider` alone no longer settles a `--model` id several providers
  serve, or homes a bare one, on a provider only the `.env` authenticates
  (`{"defaultProvider": "anthropic"}` plus a `.env` `ANTHROPIC_API_KEY` sent
  `--model claude-haiku-4-5` to `api.anthropic.com` on the repo's key past
  your exported `OPENAI_API_KEY` — and, once `--model` stopped preferring
  OpenRouter, past an exported `OPENROUTER_API_KEY` too). A project
  `defaultProvider`/`defaultModel` pair still picks the launch model when you
  pass no `--model`, and a `.env` key then authenticates it (ADR-0250 §6,
  pre-existing) — since #369 only in a directory you trusted (see above). A `.env`
  key still authenticates the route your own choices made — with no key of your
  own for that route it is the one sent, and the `Notice: loaded credentials`
  line names it. (It was also the one sent when your `models.json` re-pointed
  a built-in provider at a gateway with an `apiKey` of its own; since #363 the
  entry's `apiKey` goes first — see above.) A `.env` key whose name is not a plain environment name
  (such as `X,ANTHROPIC_API_KEY`) is now refused. Nor does a `.env` value pick a
  **host**: a `{NAME}` placeholder in a base URL (your own `models.json`
  template, say `https://{TENANT_KEY}.owner-gateway.invalid/v1`) is filled from a
  project `.env` only for Cloudflare's two shape-checked ids — a `.env`
  `TENANT_KEY=repo-chosen.invalid/v1#` used to send the prompt and the repo's key
  to `repo-chosen.invalid`; now the model is not runnable and `--model` / `/model`
  tell you to export the variable. For a program that installs an
  `AuthStorage` fallback resolver: an installed resolver counts as a credential
  of your own (so a provider only the `.env` authenticates is set aside — with
  only `.env` keys, such a program now gets `/model`'s refusals), and a resolver
  answer that carries a `.env` value (equal to it ignoring case and
  surrounding spaces, or - both 8 characters or longer - with it inside) does
  not choose a route. One more shape still
  sends on a `.env` key while you hold one of your own elsewhere: an id only one
  provider serves (`--model gpt-realtime-2.1`, only OpenAI's, with
  `OPENROUTER_API_KEY` exported and `OPENAI_API_KEY` only in the `.env`) goes to
  that provider on the `.env` key, as pi's resolver picks the only match;
  `/model gpt-realtime-2.1` refuses it (ADR-0250 §6).

- **A program driving `aelix --mode rpc` through `RpcClient` no longer loses a
  cancellation that arrives in the moment a command's answer or the turn's end
  comes back.** `wait_for_idle()`, `collect_events()`, `prompt_and_wait()` and
  every command returned normally in that moment as if nothing had been
  cancelled, which also defeated an `asyncio.timeout()` around them; a
  `prompt_and_wait()` cancelled as its `prompt` was answered then waited for
  the whole turn and returned normally. They now raise `CancelledError` there,
  as they already did a moment earlier — so a command the child has already
  answered can come back to a cancelled caller as `CancelledError`. A
  cancellation arriving as a call's own timeout expired used to come back as
  that `TimeoutError`, reporting the cancel as a timeout; it now raises
  `CancelledError` too. The rpc delegation channel, which no setting selects
  yet, inherited this: a delegation cancelled at that moment kept its child
  working until the child's turn ended and then returned the child's result as
  if it had not been cancelled; only a turn that never ended waited out the
  whole budget with the child alive; one cancelled as its budget ran out
  reported a timeout. It now ends at once and its child is killed and reaped.
  The default (print) delegation channel and `stop` were not affected. One
  side effect, kept on purpose: a turn end the child wrote just before it
  exited, but that is read a moment after the exit was noticed, can now come
  back as a completed turn instead of `RpcServerExited` — the child did finish
  its turn (not seen once in 2 400 runs of a real child that ends its turn and
  exits at once). (#351)
- **With `OPENROUTER_API_KEY` set, a model on a provider you defined no longer
  goes to OpenRouter.** `--model <provider>/<id>` for a `models.json` provider
  or an extension's `register_provider` provider was sent to OpenRouter as an
  OpenRouter model id — the prompt went to a third party instead of your own
  endpoint, and an `--api-key` you gave for that endpoint went with it as the
  OpenRouter bearer. Measured with a local listener: `--model
  retryprobe/held-model --api-key K` delivered `K` to the OpenRouter URL; with a
  local ollama in `models.json`, `--model ollama/<id>` retried twelve times
  against `openrouter.ai` and never reached `127.0.0.1:11434`. The same leak ran
  through a bare id only your provider serves, an agent profile's `model:`, a
  hand-written `defaultModel` in `settings.json`, and delegated children. Now a
  provider you defined — and a built-in provider OpenRouter has no namespace
  for, such as `openai-codex/…` or `xai/…`, which used to fail with OpenRouter's
  400 — stays with that provider, and `--provider` overrides either way.
  `--api-key` is attached to the provider the run actually uses. See ADR-0249
  (#344); how the remaining strings are routed changed again with #362 (see
  Changed).
- **An extension's provider can be chosen at launch.** `--model <name>/<id>` and
  `--provider <name> --model <id>` for a provider an extension registers were
  refused at startup (`No provider registered for api='unknown'`) because the
  model was resolved before any extension had loaded; only `/model` could reach
  it. The registry now takes the extensions' registrations first, on every
  start, `/new`, `/fork`, `/resume` and `/reload` (#344).
- **A `baseUrl` in `models.json` for a built-in provider is honoured at launch.**
  `--provider openai --model gpt-4o-mini` (and `--model openai/gpt-4o-mini`)
  with `providers.openai.baseUrl` set to a gateway went to `api.openai.com` with
  the gateway's key; only the `/model` picker used the gateway. The catalog
  model now keeps its protocol and metadata and takes your host (#344). The key
  it sends is chosen as for any provider: an
  `--api-key`, a `/login` credential or `OPENAI_API_KEY` comes before the
  `apiKey` in `models.json`.
- **A provider an extension registers in `session_start` is no longer sent to
  OpenRouter.** It arrives after the launch model is chosen, so with
  `OPENROUTER_API_KEY` set `--model <name>/<id>` had already become an
  OpenRouter id, and the first prompt went there — in print mode, and in the
  interactive and RPC modes after a warning. A launch model that only such a
  provider could serve — no provider registered at launch took it — is now
  refused, as in pi (#344, #367), whatever OpenRouter key you hold (exported,
  in a project `.env`, or none): `-p` and `--mode json` stop
  with an error before any request, naming the provider and saying to register
  it in the extension's `setup()`; the interactive mode starts with that warning
  and sends nothing until `/model` picks a model. A launch model a provider
  registered at launch serves (a built-in, `models.json`, an extension's
  `setup()`) stays on that provider, as in pi, even when the new provider
  serves the same id or your settings `defaultProvider` names it, and a turn
  a `session_start` handler triggers runs there. Whatever re-derives the model
  from the launch inputs without you naming one holds again wherever it lands
  on that provider — also after a launch that stayed on a registered provider,
  since aelix rebuilds from the launch inputs where pi keeps the session's
  model: `/new` and the other rebuilds, `/agents use` of a profile
  that names no model or provider of its own (or `--none` — also after a launch
  through `--agent`, or one that went to OpenRouter; since #412 only when it
  replaces a profile whose own `model:` or `provider:` applied — otherwise it
  keeps the model you are on), and the model picked for
  you after `/login`, which picks as it would with no saved default and never
  that provider. A `set_model` in
  the extension's own `session_start` handler does not make a refused launch
  pass or release the hold after a rebuild, and while those handlers run for a
  launch model no registered provider has claimed — or for a rebuild that holds
  — no turn starts at all, whatever model a handler has just set: a turn a
  handler triggers then ends at once as a refused turn — its `agent_end`
  arrives, so a handler waiting for it carries on instead of hanging the
  launch — and nothing is sent; its message goes out with your next prompt (it
  used to go to OpenRouter, with an `--api-key` typed for the provider as the
  bearer, or to the model the handler had set). A `model_select` handler that
  answers the held model with a `set_model` of its own does not release the
  hold either, nor send a turn while aelix puts the session on hold — at
  launch, on a rebuild or at `/agents use`, that turn is refused and nothing is
  sent; its message stays in the conversation and goes out with your next prompt
  (it used to go to the provider before the hold came back, at `/agents
  use`), and the refused turn says the session is being put on hold. `/model <name>/<id>` — or
  `/agents use` of a profile whose `model:` or `provider:` names it — then
  reaches the provider. A `/model` choice lasts until the next `/new` or other
  rebuild, which holds again, and `/model` saves it as your default, so the
  next launch without `--model` is refused the same way. The
  RPC mode starts held too, and cannot pick another model (`aelix --mode rpc`
  has no model registry for `set_model`): its warning says to restart with
  another `--model` (since #371 it has one: `set_model` leaves the hold and the
  warning says so, above). The same holds for `--provider <name> --model <id>`,
  `--provider <name>` alone, a bare id only that provider lists, a
  `defaultProvider`/`defaultModel` pair in `settings.json`, an agent profile's
  `model:` and a delegated child's launch — several of which used to fail at
  the first turn with `No provider registered for api='unknown'` — and when
  `session_start` registers two providers whose names differ only in case (the
  message names both). `--api-key` typed with such a model is refused the same
  way: no request is made, and the key is attached to no provider. A provider
  counts as registered there whatever registers it while the session is
  starting — from the end of the build through the `session_start` handlers
  and the turns they trigger, up to aelix's check after them: also the `input`
  or `before_agent_start` handler of a turn a `session_start` handler
  triggers, awaited or not, and a task a handler or `setup()` started (pi
  chooses its launch model before any handler runs) — and the message says
  "registered while a session was starting (for example in a session_start
  handler)". Register providers in `setup()` to use them at launch.
- **The launch's `Note:` that a model is sent to OpenRouter as written (or the
  custom-id `Warning:`) is no longer printed when it is not true.** An
  extension whose `session_start` handler moved the session to a model of its
  own sends the prompt there; the line still said OpenRouter. It is printed
  only while the session is still on the launch route (#367).
- **An id your `models.json` or extension provider does not list is no longer
  refused as an unknown protocol.** `--provider retryprobe --model <new-id>` and
  `--model retryprobe/<new-id>` — with or without `OPENROUTER_API_KEY` — take
  that provider's own protocol and host when its listed models agree on one (a
  model you pulled into ollama after writing `models.json` reaches your ollama);
  they used to fail with `could not be resolved to a known API protocol`
  (#344).
- **`--model RetryProbe/held-model` works.** The provider prefix is matched
  case-insensitively, as `/model` and pi already did, and a provider you defined
  wins over a built-in whose name differs from it only in case: with a
  `models.json` provider named `OpenAI`, `--model openai/m1` reaches it (with
  `OPENROUTER_API_KEY` set it would otherwise have gone to OpenRouter, an
  `--api-key` for your endpoint as the bearer). A prefix two of your providers
  share up to case, spelling neither exactly, is refused with an error naming
  both. The same goes for a provider you name: `--provider`, `defaultProvider`
  in `settings.json` and an agent profile's `provider:` are matched
  case-insensitively, and a provider you defined wins — `--model m1 --provider
  openai --api-key <key>` used to send the prompt and that key to
  `api.openai.com` instead of your `OpenAI`, and `--provider OPENAI` was refused
  as an unknown protocol (#344).
- **A second prompt sent while the first one waits to retry, or while it runs
  its closing compaction, no longer runs alongside it and mixes both messages
  into both answers.** When a provider call failed with a retryable error (a
  rate limit, an overloaded server), Aelix reported itself idle for the whole
  wait before the retry — and again while it checked whether to compact the
  conversation at the end of a turn. A second prompt sent then was accepted and
  ran at the same time as the first, and the two shared one conversation: live,
  a prompt asking only for `ALPHA` got `ALPHA` and `BRAVO` back, and so did the
  prompt asking for `BRAVO`. A prompt now holds the harness until it has
  finished completely. A second `prompt()` in that window is refused as busy,
  as it is during any other turn. Over RPC, a `prompt` command there gets an
  error response, or — with `streamingBehavior` — is queued as a steer or
  follow-up, which it used to ignore and start a turn instead; `get_state`
  reports `isStreaming` true through the wait. `wait_for_idle()` and
  `dispose()` wait for the whole prompt, where `dispose()` used to tear the
  harness down under a prompt still waiting to retry. An extension's
  `send_message(trigger_turn=True)` in that window is queued for the next turn
  instead of starting one. A model or thinking-level change or an
  `append_message()` made during the wait is written to the session by the
  time the prompt returns — an appended message used to never reach the
  session at all. If the prompt is cancelled while it is still writing them,
  the ones left over are written, in order, before any later model or
  thinking-level change, appended message, compaction, tree navigation or
  prompt writes to the session, and `dispose()` writes them — they are no
  longer overtaken by a later change (a resumed session restoring the older
  thinking level) or lost. (A session name, a label, an extension's
  `append_entry` or a user-bash record is written at once, as before.) A
  prompt cancelled while it waits for writes still being made returns once they
  are written; if a custom session storage never finishes a write, the prompt
  stays busy until it is cancelled a second time. Not changed: a
  thinking-level change made while idle with nothing waiting is written at
  once, and with a custom storage that pauses inside that write a later
  change can still land before it (#314). `navigate_tree()`
  and a manual `compact()` are refused as busy there too. An `abort()` (or
  `dispose()`) during the wait or the closing checks now also stops the
  automatic compaction and the retried turn that used to go ahead after it.
  So does an extension's `ctx.abort()` (and `ctx.shutdown()` when no shutdown
  action is installed): called from a hook of a turn that ends in a retryable
  error or over the compaction threshold, it now stops the retry and the
  compaction — it used to do nothing. It still does not stop the turn it is
  called in. Aborting a running turn whose conversation is over the compaction
  threshold — Esc or Ctrl+C in the TUI, an RPC `abort`, `dispose()` — no longer
  runs the closing compaction after the aborted turn, as in pi. Typing during
  the TUI's retry countdown is unchanged: a line typed there was, and is,
  steered into the retried turn. For
  code that embeds Aelix: an async event listener or hook handler that the
  prompt is awaiting during its retry wait or closing compaction must not
  await `wait_for_idle()`, `dispose()`, or a runtime `new_session()`,
  `fork()`, `switch_session()` or `reload()` (they wait for idle too) — each
  now waits for the prompt it is part of, for ever. A custom session storage
  must not call back into the harness from inside one of its writes with
  anything that writes the session — an idle `set_thinking_level()`,
  `prompt()`, `compact()`, `navigate_tree()` or `dispose()`: that call can
  wait for the write it is made from, for ever. (#334)
- **The last lines of a command's output no longer go missing when Aelix is
  busy as the command ends.** The bash tool stopped reading a finished
  command's output by the clock — a tenth of a second after the command ended —
  without asking the pipe whether anything was still in it. If the thread that
  reads the output was held up across that moment, the end of the output was
  dropped and the model got a shorter result with exit code 0 and no sign
  anything was missing. Because the tool keeps the *tail* of a long output,
  what went was the part the model reads: the failing assertion, the error
  summary, the last status line. Aelix's own CI lost 7 to 56 KiB off the end of
  a 2 MiB test output this way, six times. Reading now stops early only once
  every byte the command wrote before it ended is known to have been
  delivered; otherwise it keeps reading, for at most 2 seconds after the
  command ends (1 second after a timeout or an abort) — or, if Aelix itself
  was held up for more than a tenth of a second (its process stopped, or too
  busy to get back to the command) and gets back less than a tenth of a
  second before that limit or after it, until a tenth of a second after it
  gets back, once, so output that was waiting is not cut in the same
  instant. If that limit is ever reached first, the result now says so
  instead of nothing: `[Output may be incomplete: reading stopped 2s after
  the command ended, before its last output could be confirmed. Re-run it,
  or redirect its output to a file, if the end matters.]`. A command that
  backgrounds a helper still comes back right away, as before. Extensions'
  `exec`, the extension catalog's `git clone` and the `fd` file scan take the
  same fix, without the notice. On Windows a narrower gap remains: output
  that arrives just after the reading thread last checked the pipe can still
  be lost silently if that thread then gets no CPU for a tenth of a second.
  (#260, #261)

- **A turn that failed no longer says so twice when you come back to it.**
  Resume a session containing a failed turn — `/resume`, `--continue`, or
  opening the file with `--session` — and the same error sentence was drawn
  twice: once as ordinary message text carrying an internal `[error] ` prefix
  (and with any backticks in it turned into code spans), then again as the red
  `✖` line. It was drawn once while the turn was actually happening, so a
  reloaded transcript disagreed with the one it was reproducing. Now both show
  the same single `✖` line. Sessions you already have on disk are fixed by
  this too — nothing about them changes, they are simply read correctly — and
  the copy the model reads on the next turn is untouched, so a resumed
  conversation can still see what went wrong. This covers the terminal, live
  and resumed; the HTML `/export` writes still shows the `[error] ` prefix
  (and never showed the `✖` line), which is a separate fix. (#194)

- **A command whose output is one long line now reaches the model, instead of
  a note saying it was cut.** `bash` caps what it hands back at 2000 lines or
  50KB and says what it dropped. That cap counted the newline at the end of the
  output as a line of its own — and an empty line costs no bytes, so it was the
  only "line" that fit the byte budget, and the real one above it was dropped
  whole. `python3 -c "print('x'*1000000)"` ran for real and the model received
  133 bytes: none of the output, under a note reading `[Showing lines 2-2 of 2
  (50.0KB limit)]` about a command that printed one line. Every `print`, every
  `echo`, ends in that newline. The last 50KB now arrives, under a note that
  names the line it came from and how long that line really is: `[Showing last
  50.0KB of line 1 (line is 976.6KB)]`. The line counts in that note are right
  too — 3000 printed lines now read `lines 1001-3000 of 3000` where they read
  `1002-3001 of 3001` before. Two more surfaces share the cap and shared the
  defect: an RPC client's `bash`, which handed back 0 bytes of a 40,000-byte
  line under its own 32KB cap, and `read`, which called a file of exactly 2000
  lines truncated, cut its last newline and offered to continue from line 2001,
  where there is nothing — such a file is now read whole. When `read` does
  still truncate, the line count in its own notice is unchanged and still says
  one more than the file has. A `!command` whose output runs long shares the
  same cap and does not change either way: it carried a local workaround for
  this defect, which the fix makes unnecessary and removes. (#309)

- **The citation gate no longer turns its own failures green.**
  `scripts/check_citations.py --fix` relocates citations whose target moved and
  hands back the ones it cannot place — and then rebuilt the lock from the tree,
  which re-anchored those very citations onto whatever text happened to be at the
  stale line number. Measured at `fad2e28`, where it was found: six lines inserted
  into `harness/core.py` plus one edited comment drifted 71 citations; `--fix`
  relocated 59, named 12 as needing a human, and re-locked all six of the lock keys
  they share — one onto the single character `)` — after which `--check` reported
  `citations OK — 937 gated, none drifted.` (The totals follow the tree, the defect
  does not: the same probe at `96f93c0c` drifts 77 and relocates 65, against the
  same 12 and the same 6.) A citation `--fix` could not relocate now keeps the text it was written
  against, so the gate stays red until someone re-derives the number. **That makes
  `--fix` no longer the whole repair**, so the three places documenting it as one
  command — `CONTRIBUTING.md`, the pull-request template and the first-PR welcome bot
  — now say what to do with the citations it names instead. `--lock` can still
  override, for a block edited where it stands, and now prints every anchor it
  replaces — under `--lock` alone, because under `--fix` the same report could
  announce an in-place edit that had not happened. Two further citations pointed at
  **blank lines** — 15 and 54 lines away
  from the constructs their sentences named, green since the lock was written
  because an empty anchor matches any empty line — and are repaired; anchors that
  say nothing are refused outright from now on (ADR-0248, #310).

- **A delegated agent now runs on the model you are running, whenever you start
  it.** `/agents run` inherited your `--model` only if a tool call had already
  happened in that session. Start Aelix and run it as your first command, or run
  it straight after `/new`, `/resume`, `/fork` or `/reload`, and the child was
  launched with no model at all — it fell back to its own default, which for a
  reasoning-mandatory default meant the delegation died on a provider error
  instead of doing the work. Switching with `/model` and then delegating had the
  same problem the other way round: the child got the model you had just
  switched *away* from. Three other things a child inherits were wrong in the
  same window and are fixed with it: it could be handed **more tools than you
  have** (a session started with `--tools` lost that limit after `/new`), it
  stopped seeing this project's `.aelix/agents` profiles in a directory you had
  trusted, and one delegation inside a parallel or chain call failed to start
  outright, handing back an error where its work should have been — **that one
  now runs**, read-only, with no more authority than anything else in the same
  call. When you are *asked* about a delegation, and what it is then permitted
  to change, are unchanged; the tool list a child is handed is now the one you
  actually have, in that window too. (#304)
- **`!command` output now reaches the model.** Typing `!ls` or `!git status`
  ran the command and printed it on screen, and that was all it did: the
  output was recorded in a shape nothing reads back, so the model never saw
  it. `!cmd` and `!!cmd` — the form documented as "out of context" — were the
  same command, and `/help`'s "in / out of context" was a distinction that did
  not exist. Ask "what did that print?" after a `!` line and the answer was
  that it could see no command output. That output is now part of the
  conversation, in this turn and after a `--continue` or `/resume` of the same
  session, and it is drawn into the transcript on a replay. Two things come
  with it. **Your screen still gets every byte, but the copy the model carries
  is capped** at the last 2000 lines or 50KB — the same cap the agent's own
  bash tool uses — with a line saying what was left out, so one `!cat
  server.log` does not ride along in every later request of the session. A very
  long command line is shortened in the record the same way. And a
  command that **fails now says so**: the record appends "Command exited with
  code N", where before `!test -f missing` and `!test -f present` reached the
  model as the same two lines. `!!cmd` is unchanged and still records nothing
  at all: it prints on your screen and the model never sees it. Sessions
  recorded before this keep loading; their old `!` output stays invisible.
  (#299)
- **One session record that cannot be written no longer takes the rest of the
  turn's records with it.** Switching model or thinking level while the agent
  is working, and any message an extension adds to the conversation mid-turn,
  are held until the turn ends and then written to the session together. If any
  one of them was refused — by a full disk, or by an entry Aelix could not
  encode — the ones queued behind it were dropped at the same moment: not
  written, not retried, and not mentioned anywhere. Each record is now written
  on its own, so a refusal costs that record and nothing else, and it is
  reported: a warning naming what was lost and why, and a count on the
  save-point event extensions can watch, where until now that event said there
  had been nothing to save. Interrupting the agent while those records are
  being written no longer drops the ones it had not reached yet. The same
  failure also used to end the turn, surfacing the disk error as though the
  model had failed; it no longer does — the conversation carries on and only
  the record is missing. (#301)
- **A message queued for the next turn is no longer thrown away when an
  extension fails as that turn starts.** An extension can line up a message to
  be sent with your next prompt. Those queued messages were taken off the queue
  before the turn was built, so an extension that raised while Aelix was
  starting the turn — one watching the queue to redraw an indicator, or one
  rewriting the system prompt — took them with it: the prompt failed, and the
  queued text was gone from the queue, from the request and from the session
  file, with nothing in the error naming it. It now goes back on the queue,
  ahead of anything queued in the meantime, and is sent with the next prompt
  instead. A turn that fails after it has reached the model is unchanged: the
  message was already sent, so it is not queued again. (#311)
- **Cancelling a prompt while it is still starting no longer leaves the harness
  busy for good.** For code that embeds Aelix: cancel an `AgentHarness.prompt()`
  — directly, by an `asyncio.wait_for` / `asyncio.timeout` running out, or by a
  sibling task failing in a `TaskGroup` — while an extension's `input` or
  `before_agent_start` handler, a `queue_update` observer or the session read
  that opens the turn is still running, and every later `prompt()` was refused
  as busy, `wait_for_idle()` never returned and `dispose()` hung waiting for
  it. The harness now goes back to idle on the way out, and the cancellation
  still reaches you as a cancellation. A message queued for the next turn that
  the cancelled prompt had already picked up is sent with the next prompt when
  the cancel lands in a `queue_update` observer or a `before_agent_start`
  handler; cancelled in the session read, it is still lost (#320). Cancelling
  `compact()` while an async event listener is still handling its
  `compaction_start` did the same and no longer does. And a prompt that fails in
  its closing check for automatic compaction, or whose `input` handler answers
  that it has handled the prompt itself, no longer releases a turn another
  prompt is running — one that got in while it was checking, or a first
  prompt's retried turn — where a third prompt used to be let in on top of it.
  Two cases there got worse, both after a second prompt got in while a first
  one waited to retry: when the first prompt's retried turn starts while the
  second is still running (still in its extension handlers or already in its
  turn), or the second prompt's turn starts while the first one's retried turn
  is running, and the prompt whose turn started last is cancelled before that
  turn gets going, a third prompt is now let in on top of the other where it
  used to be refused. The second prompt should not have got in at all, and
  closing that closes these too (closed by #334). It was not only for embedders:
  `aelix -p` awaits its prompt inside the task `asyncio.run` cancels on the
  first Ctrl+C, so a Ctrl+C while an extension's `input` or
  `before_agent_start` handler was still running — a plugin's subprocess hook
  included — left `-p` waiting in its cleanup for a harness that never went
  idle, and it took three Ctrl+C to end. It now ends on the first.
  `aelix --mode rpc` with a prompt parked there took three Ctrl+C and now takes
  two: its prompt runs as a task of its own, which the first Ctrl+C does not
  reach and the second cancels as `asyncio.run` shuts down. (Measured by typing
  Ctrl+C into a pseudo-terminal, in both handlers, with a Python extension and
  with a subprocess hook.) In the TUI, Esc and Ctrl+C call `abort()`, which
  does not cancel a prompt at those points (read from the code, not measured).
  (#321)
- **Two terminals on one session no longer lose a whole turn.** Opening the
  same session twice was easy to do by accident — `aelix --continue` picks the
  same file for every terminal in a directory — and both terminals appeared to
  work. They did not: each one attached its turns to wherever it last thought
  the conversation ended, so the second terminal's entire turn became a dead
  branch that no reload, `--continue` or `/resume` ever showed again. The text
  was in the file and unreachable. A session file now has one live writer. When
  you open one that another terminal already has, Aelix asks: carry on in a
  fork of it (a new session file with the same history), open it read-only to
  look without writing, or stop. A terminal that chose read-only is not stuck
  that way: `/fork`, `/new` and `/resume` each move it onto a session it owns,
  and it can type again. `-p`, `--mode json` and `--mode rpc` cannot
  ask, so they stop with a message naming `--fork`, `--session` and
  `--no-session` rather than choosing for you. The same check now covers
  `/resume` and `/import` from inside a session, and opening a delegated
  agent's session file while that agent is still running. Quitting one terminal
  frees its session for the next immediately, and a terminal that is killed
  outright frees it too — the lock is held by the operating system, so there is
  nothing to clean up and nothing to wait for. On a filesystem with no file
  locking at all (some network mounts), Aelix says once that it cannot tell,
  and carries on; if the lock file beside a session is one Aelix cannot use —
  left root-owned by a `sudo aelix`, say — it says which file and stops, rather
  than blaming the filesystem and writing anyway. One empty
  `<session>.jsonl.lock` file now sits beside each session on macOS and Linux;
  it is not a session and never appears in any session list. (#137)
- **`/fork` before the first message of a session now gives you an empty
  session, not a copy of the one you were in.** Forking cuts the conversation
  just before a message you pick, and picking the very first one is how you
  start over while keeping the thread you came from on record. It did the
  opposite: the new session came back holding the entire original. A session
  started with `-p` begins with your message, so in those the first message is
  also the first thing in the file — and "the entry before it" and "the whole
  session" were the same value inside, with the whole session winning. The
  session you forked away from was never touched, so nothing was lost; the fork
  was simply not the fresh start it claimed to be. Forking before any later
  message was, and stays, unaffected. (#300)
- **`--continue` after a fork now resumes the fork, not the file you forked
  away from.** In a deep directory the fork was invisible to it. A session's
  header line carries its working directory, and a fork's carries the path of
  the session it came from as well — which contains that directory a second
  time — so from about a hundred characters of path onward the header ran past
  the 512 bytes `--continue` read when deciding whether a file was a session at
  all. The truncated line would not parse, the fork was skipped as "not a
  session", and the next `--continue` picked the original and kept writing
  there. Measured in a 143-character directory: the fork's header was 557 bytes
  and the turn landed in the original. The check now reads the whole first line
  instead of a fixed number of bytes. The same applies to any session whose
  header is long for another reason. (#297)
- **A truncated delegation summary no longer points at something nobody can
  read.** It ended "Full output preserved in tool details." — true only until
  the call returned, because a tool result's details are never saved. It now
  says "The full output is recorded in the delegated session." when the child
  has a session file and the summary came from the child itself, and says
  nothing about where the rest went otherwise. (#199)
- **`/agents run` right after `/new`, `/resume` or `/fork` no longer fails
  before asking.** Typed before the new session's first prompt, it read the
  previous session's hook context, which had already been shut down, and
  stopped with a stale-context error. It now behaves as the first `/agents run`
  of a fresh session always has. (#199)
- **A fork or an import that fails part-way no longer leaves a truncated session
  for `--continue` to resume.** A fork was written as the header followed by one
  append per entry, so a failure in the middle left, say, two of five entries on
  disk — and `--continue`, which picks the newest file whose header parses, took
  it. Forks are now published as one file that appears whole or not at all
  (staged beside its destination, then renamed into place). Import was worse: it
  emptied the destination first and then copied onto it, so re-importing over an
  existing session and failing lost that session outright; it now copies into a
  temp file, syncs it to disk, and only then replaces the destination. On
  Windows, where a virus scanner or the search indexer can hold a just-written
  file for a moment and make the rename fail, the rename is retried for about a
  second before the error is reported. (#294)
- **One failed write to a session file no longer costs every turn after it.** If
  an append failed part-way (a full disk, a second Ctrl-C), the next entry was
  glued onto the fragment, the fused line could not be read back, and on the next
  load every entry after it was dropped with it. Measured: three turns in memory,
  one after reloading. The next write now starts on a fresh line, so only the
  fragment is lost. A short write — the operating system accepting fewer bytes
  than it was given, which it reports only in a return value that was ignored —
  silently cut a line the same way; writes are now completed. (#294)
- **The installers no longer hand you an interpreter nothing has ever tested.**
  `uv tool install` consults neither `.python-version` nor `uv.lock` — it
  resolves an interpreter fresh and takes the newest one on the machine.
  Measured on a box carrying 3.11 through 3.14, `install.sh` built its
  environment on **Python 3.14.5**, which CI has never executed. There the
  pinned `openai<2.0` dies: 3.14 made `typing.Union[...]` slotted while the SDK
  still writes `union.__discriminator__ = …` onto it, so an agent turn ends in
  `AttributeError: 'typing.Union' object has no attribute '__discriminator__'`
  (#262). It is a latent failure — `construct_type` validates through pydantic
  first and only falls through to that write when validation fails, so
  `-p "say OK"` succeeds on 3.14 and real work does not. Both installers now
  pass `--python '>=3.11,<3.14'`, overridable with the new `AELIX_PYTHON` knob;
  re-running the install line moves an existing 3.14 environment onto a matching
  3.11-3.13 interpreter (measured, both directions). An empty `AELIX_PYTHON`
  means the default, not "no constraint" — `uv tool install --python ""`
  silently keeps picking the newest interpreter, so the `AELIX_EXTRAS=` habit
  must not carry over; widen it explicitly instead, `AELIX_PYTHON='>=3.11'`.

  The flag is permanent rather than a stopgap: `uv tool install` chooses the
  interpreter before it resolves, so a published wheel's own `Requires-Python`
  ceiling does not steer it (measured — a wheel declaring `<3.14,>=3.11` still
  installed onto 3.14.5 through `--find-links`, while `pip` refused the same
  wheel outright). #192's metadata bound is still worth having for the `pip`
  path, but it does not retire this.

  **It also does not cover `uv tool install` typed by hand.** `uv tool install
  aelix@latest` builds on 3.14.5, and re-running `uv tool install --force
  'aelix==0.1.0b2'` overwrites a correct 3.13 environment with a 3.14 one — both
  measured — while `uv tool upgrade aelix` suggests the first command on its own.
  Upgrade by re-running the install line. Closing that path needs a runtime guard
  in aelix, which is filed separately. (#263)

### Security

- **A delegated child can no longer put escape sequences on your status line
  (#178).** While a child runs, the status line shows its profile and the tool
  it is using, and the tool name is whatever the child reports. Both were
  printed as received. Measured in the shipped status row: a tool name carrying
  `\x1b[31m` painted the row red, and one carrying `\x01…\x02` passed an OSC 52
  clipboard write straight to the terminal. The same two names, and the child's
  state, also went raw into the progress card an `agent` call streams to
  extensions and to `--mode json`/`rpc` consumers. They are now cleaned the way
  the batch panel already cleaned them: control characters and BiDi overrides
  are removed, which defangs an escape sequence rather than removing it - its
  ESC goes and the rest stays on screen as plain text, so `\x1b[31m` shows as
  `[31m`. Every line break, tab, run of spaces and non-ASCII space (a no-break
  space, U+3000 and the like) becomes one ordinary space, leading and trailing
  spaces go (including any a removed character leaves behind), and each name is
  cut to 78 columns. The panel, the batch status row and the result footer now
  also remove BiDi overrides (U+202E and its relatives), which they used to let
  through. A name in any script with no control character or BiDi override, whose
  only spaces are single ordinary ones between words, and that fits - at most 78
  columns, and at most 312 characters, which only a run of combining accents
  reaches - is shown exactly as before.
- **`aelix extension update` no longer installs a file or directory in your
  current directory that is named like a recorded package (#405, ADR-0255
  §16).** aelix decided whether an install target was a local path by asking
  your current directory whether it existed there. `discover install` refused
  that case, but `update` re-installs records the same way and did not: run in
  a cloned repository holding a `local-ext` directory, or a `local-ext` symlink
  to a wheel, `aelix extension update` (or `update local-ext`) upgraded that
  instead of the recorded package `local-ext` — exit 0, `--upgrade
  <repository>/local-ext` (measured). The Python API did the same for
  `install_extension()` given what the catalog resolver returns. A source that
  comes from a catalog or an install record is now read by its spelling alone:
  a package name goes to your index whatever your directory holds, an absolute
  path is that path, a URL the URL it is. One spelled as a relative path
  (`./x`, a bare `x.whl`) is refused, and so is a relative path record (aelix
  has always recorded paths absolute, so only a hand-edited settings file holds
  one): `update` names it, prints the command that drops the record (`aelix
  extension source remove -- '<path>'` — `source remove` now takes `--`; it
  matches a source by its spec, its name or its path, so it also drops a
  package record of the same name and an absolute path record that the same
  string names from the directory you run it in; the message says so and
  names that path) and goes on with the others. A
  package, URL or git source from a catalog, a record or the API has its
  surrounding whitespace stripped once, and that stripped string is both what
  aelix checks and what it installs, so a git record that a typed `install
  'git+file:///repo '` wrote with its trailing space still updates. A source
  spelled as a path keeps its exact string: one with a leading space is
  relative and refused. A requirement with a version specifier, a marker or a
  URL is a package even when it ends like an archive (`x==1.0+v.whl`) or like
  a git URL (`x==1.0+vendor.git`; a catalog may now list one), while a bare
  `x.whl` or `x.tar.gz` is a relative path and refused, and a bare `x.git` is
  still a git URL. The Python API's `verify_and_pin()` and `build_pip_args()`
  refuse a kind their caller passes that the source's spelling does not give,
  and all three functions refuse a `ResolvedPath` that is not absolute as
  written (`ResolvedPath("~/pack")` raised a `ValueError` instead). A relative
  URL or direct reference in a hand-edited record or a Python `CatalogSpec`
  (`file:x`, `name @ ./x`) is not spelled as a path and is not refused: it
  reaches the installer as written, in aelix's installer directory (a known
  limit). `discover install` now installs the
  package beside a same-named entry instead of refusing. What you type is
  unchanged: `aelix extension install local-ext` beside a `local-ext` directory
  still installs that directory, and `aelix extension update ./local-ext` (a
  path you type that matches no record) upgrades that path exactly as typed,
  resolved the way `extension install` resolves a path (`~` expanded,
  `[extras]` kept), while a name you type that matches no record is the
  package.
- **`OPENROUTER_BASE_URL` now applies to OpenRouter models picked inside a
  session, not only at launch (#375, ADR-0251 §11).** If you point OpenRouter at
  a gateway with `OPENROUTER_BASE_URL`, then picking an OpenRouter model with
  `/model` (or from its picker, or through an embedder's rpc `set_model` /
  `cycle_model`) used to send the next prompt and your OpenRouter key to
  `openrouter.ai`, or to `providers.openrouter.baseUrl` from `models.json`.
  Measured in the TUI: after `/model openrouter/newlab/model-x`, `hi` went to
  `openrouter.ai:443`. Every OpenRouter model aelix hands out now uses the
  variable, whichever way it was picked; a model an extension builds itself with
  `aelix_ai.models.get_model()` is the catalog entry and keeps `openrouter.ai`
  (take it from `ctx.model_registry` instead). A session continued with `-c` was not affected: it rebuilds
  its model the way the launch does. The variable still beats a `models.json`
  `baseUrl` (provider or per model), changes nothing but the host, and still
  moves only the provider named exactly `openrouter`. A provider you defined
  under another name, or spelled `OpenRouter`, keeps its own `baseUrl` and key,
  even at `https://openrouter.ai/api/v1`. Where the value can come from is
  unchanged: your shell, or a project `.env` only when you list the name in
  `AELIX_DOTENV_ALLOW`.
- **`AELIX_OFFLINE=1` now stops the `rg`/`fd` download (#288).** The offline
  switch had two names and three readers, and the one that guards the download
  of an executable read only `PI_OFFLINE`: with `AELIX_OFFLINE=1` and no `fd`
  on `PATH`, the first `find` printed nothing and resolved `api.github.com`
  (measured through a real `aelix -p` run with a socket recorder), while the
  update check and the extension catalog were already off. Every place aelix
  reaches the network on its own — the `rg`/`fd` download, the update check, the
  extension catalog refresh and an index-less `extension install` — now asks one
  predicate, and `aelix` exports `PI_OFFLINE=1` for `AELIX_OFFLINE` too, so
  delegated agents and other child processes inherit it — including the
  installer an `aelix extension …` command starts, which the export now
  precedes (it used to run only after those subcommands had returned), and
  including that subcommand's own `--offline`. Both names now read a
  value the same way: `1`, `true`, `yes` (any case) turn offline mode on; `0`,
  `false`, `no`, `off` or an empty value leave it off; **any other value turns
  it on**, so a typo fails closed rather than open. Two readings change:
  `PI_OFFLINE=0` (or `false`/`no`/`off`) is no longer rewritten to `1` by the
  CLI — it used to switch everything offline, as if it said `1` — and under
  `aelix extension …`, which never went through that rewrite, a value such as
  `PI_OFFLINE=enabled` now skips network catalogs and refuses an index-less
  install the way `aelix` itself already did. Unchanged, and now stated in the
  offline guide: MCP servers you configured, remote `http`/`sse` ones included,
  still connect under `--offline`, and a project `.env` cannot set either name
  unless you list it in `AELIX_DOTENV_ALLOW`.
- **MCP, extension and pack tools now ask before they run, and plan mode blocks
  them (#188, ADR-0253).** The permission gate used to recognise a mutating tool
  by a fixed list of built-in names (`bash`, `write`, `edit`, …) and let every
  other name through without asking, in every posture, `--permission-mode
  plan` included. MCP tools are named `<server>__<tool>`, so an MCP server's
  `write_file` arrived as `fs__write_file`. It wrote files with no prompt in the
  default posture, and in plan mode too, interactive or headless. The gate now
  goes by what a tool is, not what it is called. Only Aelix's own `read`,
  `grep`, `find`, `ls` and `aelix_status` run unasked, and every other tool is
  handled like a file edit or a shell command:
  - **default**: a prompt before each call, showing the tool's name and every
    argument it was called with, one per row. A long value is cut, with a note
    saying how many characters were left out, but no argument is left off.
    When the arguments are taller than the prompt has room for, a line under
    them says how many lines are hidden and that PgUp/PgDn scroll to them, and
    Yes and "Yes, for this session" do nothing until every line has been on
    screen. No and Esc always answer.
    "Yes, for this session" approves that one tool, by its exact name, for
    every argument, until you exit.
  - **plan**: blocked, in `-p`, `--mode json` and `--mode rpc` as well, with a
    reason that tells the model why.
  - **auto-accept-edits** and **auto** still accept Aelix's own file edits inside
    the project. An MCP or extension tool asks, whether or not it takes a
    path. `auto` no longer runs an extension's own `shell`-style tool through the
    bash classifier.
  - **yolo**: unchanged, nothing asks.
  - **`-p`, `--mode json`, `--mode rpc`** outside plan mode: unchanged. Mutating
    tools, these included, still run without asking (see SECURITY.md).
  - **Inside a delegated agent** (the `agent` tool, `/agents run`), nobody can
    be asked. In the `default`, `auto-accept-edits` and `auto` postures an MCP
    or extension tool is now refused there. Before this change the child ran
    it. In `default` and `auto-accept-edits` that is what the child's `bash`
    already got. In `auto` the child's `bash` still runs a command the
    classifier rates safe, but an MCP or extension tool is never classified, so
    it is refused like a command the classifier would have asked about. It is
    refused under `plan` too, and `yolo` children are unchanged.
  - An MCP server's `readOnlyHint` annotation does not lower the gate. It is a
    claim made by the tool being gated. A third-party tool that reuses a
    built-in name (an extension's `grep`, say) is not treated as the built-in.
  - Unchanged: the bundled `agent` tool is still left to the spawn-consent
    dialog (ADR-0197). `GuardrailExtension`'s hard-deny patterns still go by
    bare tool name, whatever registered the tool: an extension tool named
    `shell` or `write_file` is checked like the built-in shell and file tools,
    and an MCP tool, which is always named `<server>__<tool>`, is not checked.
  - For SDK users: the gate finds the tool in the turn's tool list, so a
    `ToolCallHookEvent` built by hand without a `context`, or a built-in tool
    copied with `dataclasses.replace`, now counts as an unknown tool and asks.
    A host with a UI but no approval dialog is asked through `ctx.ui.select`,
    and for these tools the title now lists every argument too.
- **Aelix's approval prompt no longer takes Yes before you have seen the whole
  command, file or argument (#389, ADR-0253 §11 to §11.2).** This is the prompt
  for `bash`, `write`, `edit` and every MCP or extension tool. It has a
  height limit, and what did not fit was below it with nothing saying so. At
  80x24 a 400-word command showed about its first third, and 1 ran it, end
  included. The `write` and `edit` prompts stopped their preview at 40 lines
  and cut each line at the edge of the box, so the end of a file, or of one
  long line, was never on screen. Now:
  - Nothing in an approval prompt's body is cut short. Every line of a write
    or an edit is there, and a line wider than the box wraps. The prompt's
    title and its option labels are still cut at the edge of the screen; the
    body has the whole path. An argument of an MCP
    or extension tool is shown whole too: it used to be cut at 200 characters
    (60 for its name), and a prompt that then fit the screen took Yes at once.
  - The `edit` prompt shows every edit the tool will make. A call carrying an
    `edits` list and also a top-level `oldText`/`newText` showed only the list,
    and Yes applied the other one as well.
  - When the tool rewrites the path (`@` dropped, `~` expanded), the `write`
    and `edit` prompts say where the file actually lands.
  - When the command or the preview is taller than the prompt, a line under it
    says how many lines are hidden and that PgUp/PgDn scroll to them. Yes and
    "Yes, for this session" do nothing until every line has been on screen,
    as #188 made them for MCP and extension tools. No and Esc always answer.
    A long file now takes several PgDn presses before you can approve it.
  - PgUp/PgDn move a page at a time (a screenful less one line) in every
    approval prompt, MCP and extension tools included. They moved five lines.
  - A control character in a command, a path, a file or an option the model
    wrote is shown by name in reverse video (`^[`, `^M`, `<U+202E>`) instead of
    being obeyed or dropped. `ESC [8m` used to hide the rest of the command while
    the prompt counted it as shown, and a dropped ESC showed a command that was
    not the one that ran. Whitespace is the exception: a tab is drawn as spaces,
    and a file that ends in a newline looks the same as one that does not.
  - A path or tool name with `[/]` in it no longer turns the prompt into
    `<rich.console.Group object at 0x…>` that Yes answered at once.
  - A line of characters that the box and the terminal measure differently
    (flag letters, skin-tone modifiers) is no longer counted as shown while its
    end is past the edge of the screen.
  - A host with a UI but no approval dialog gets the whole command or path in
    the `ctx.ui.select` title. It used to stop at 120 characters. Aelix's own
    `select` wraps and holds that title now (#399, next entry).
  - Not covered by #389: an extension's own `ctx.ui.select` and
    `ctx.ui.confirm`. The next entry (#399) covers them.
- **An extension's `select` or `confirm` no longer takes an answer before you
  have seen the whole question (#399, ADR-0253 §11.3, ADR-0199 §(c)).** An
  extension that asks for approval through `ctx.ui.select` or `ctx.ui.confirm`
  (pi's `permission-gate.ts` example puts the whole command in the `select`
  title) had its question cut at the edge of the terminal with nothing saying
  so, and Enter or `y` answered at once. Measured at 80x24 with that example
  ported: the title stopped at `arg009 arg`, and Enter ran the 400-word command,
  end included. Now:
  - The title (and `confirm`'s message) wraps, as pi's does, by grapheme
    cluster: a letter keeps its accents, and an emoji sequence or a flag is
    not split across rows. A single cluster wider than a whole row (an
    accented letter in a one-column row, a family emoji in a six-column row)
    is still cut by character.
  - A question that fits the dialog with the first row of its highlighted
    option is drawn whole and answers at once, as before; when the options do
    not all fit under it, the options scroll (the highlighted one always on
    screen, its `(n/N)` counter while there is a row for it, and a label of
    several lines cut from the bottom, as before, when there is no room for
    all of it).
  - Only a question taller than that scrolls; a line under it says how many
    lines are hidden and that PgUp/PgDn reach them, and **no option** is taken
    — Enter, Space or Ctrl+J in `select`, `y` in `confirm` — until every line
    has been on screen. Esc and Ctrl+C always answer, `n` does in `confirm`
    (in `select` it types into the filter), and so does the
    dialog's own `Cancel` (spawn consent) or `No` (the permission gate's
    fallback). In a terminal too small to show one line of the question, its
    footer and the highlighted option's first line, the dialog says so and
    only Esc, Ctrl+C, `n` in `confirm` and that `Cancel` or `No` answer; once the terminal is large
    enough again, the question is shown and answered as above.
  - An Enter or `y` typed before the dialog is first drawn is dropped, not
    queued.
  - A control character in the question is shown by name in reverse video
    (`^M`, `^[`, `<U+202E>`). `select` used to draw a CR as a space and
    `ESC [8m` as `[8m`; `confirm` showed `^M` and `^[` (not in reverse video)
    and passed `<U+202E>` through as it was.
  - The same applies where aelix asks with the model's words through this
    dialog: the spawn-consent dialog of a delegation (`agent` tool,
    `/agents run`), which now shows the whole task (it was cut at 300
    characters) and the whole directory (it was cut to 68 characters in the
    middle), and the permission gate's `ctx.ui.select` fallback. A long task
    now needs PgDn before you can approve it.
  - aelix's own pickers (`/model`, `/settings`, `/resume`, `/trust`, the theme
    picker, `/thinking`, `/login`, `/logout`, the session-in-use prompt,
    `/extension new`'s placement question and `/agents run`'s project-agent
    confirm) are unchanged at every terminal size: same layout, same keys, a
    key typed before the first paint still taken. `/trust`'s option rows are still
    cut at the edge of the screen; since #380 its title rows break between words.
  - Not changed: option labels are drawn as before, one row per line of the
    label, cut at the edge of the screen (#179), and a member of a parallel or
    chain delegation is still shown as one row of up to 72 characters.
- **Your Anthropic key no longer goes to another provider's host (#374,
  ADR-0254).** Any provider on the Anthropic Messages API that had no key of
  its own (a `models.json` custom provider with `"api": "anthropic-messages"`,
  or a built-in such as `fireworks`, `minimax`, `opencode`, `vercel-ai-gateway`,
  `kimi-coding` or the Xiaomi providers with its own variable unset) sent your
  exported `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) to that host: the
  Anthropic SDK read the variable itself. A gateway authenticated by a header
  of its own got your Anthropic key next to that header. Measured with fake
  keys in `--mode rpc` and the TUI; `-p` and `--mode json` already stopped
  earlier with "No API key found", and so do one-shot delegated children,
  which run as `--mode json -p`, while rpc-channel children take the turn path
  of `--mode rpc`. Such a request now stops before anything is sent, with
  `No API key for provider: <provider>`, as in pi; in `--mode rpc` and the TUI
  a header-authenticated gateway gets only its header. No Anthropic client
  reads `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` or runs the SDK's
  credential chain on its own: the key it sends is the one aelix's key order
  resolved for that provider (ADR-0251), or none.

  `ANTHROPIC_CUSTOM_HEADERS`, which the Anthropic SDK merges into every
  request and which can carry `x-api-key` or `Authorization`, now reaches the
  `anthropic` provider only (the built-in one, or a `models.json` entry named
  `anthropic`). Before, it went to every provider on this API: it sat next to
  a gateway's own header, and an `X-Api-Key` in it replaced the gateway's own
  key; and it no longer authenticates a provider with no key of its own. For
  `anthropic` its headers go out as before, including replacing the key, since
  it is the SDK's way to talk to your own Anthropic proxy, and a proxy you
  authenticate with nothing but an `X-Api-Key`, `Authorization` or
  `cf-aig-authorization` header in it gets its request in `--mode rpc` and the
  TUI, also next to other headers your `models.json` entry adds. The header
  counts in any letter case and only with a non-blank value; before, the SDK
  accepted only a non-blank `X-Api-Key` or `Authorization` spelled exactly that
  way, and refused a lower-case one or `cf-aig-authorization` alone. pi counts
  none of these (ADR-0254 §6). `-p` and `--mode json` still stop such a
  header-only setup with "No API key found", as before: they do not read the
  variable, and whether they should is not decided yet.

  What stays the same: the `anthropic` provider still uses `ANTHROPIC_API_KEY`
  (and `ANTHROPIC_OAUTH_TOKEN` ahead of it), and the TUI and `--mode rpc`
  still send `ANTHROPIC_AUTH_TOKEN` as `Authorization: Bearer` when it is your
  only Anthropic credential, now to the `anthropic` provider only. A provider
  with `"api": "google-vertex"` and no key still uses Google Application
  Default Credentials and sends that token to its base URL, as pi does. What
  changes: the Anthropic SDK's own credential chain (`ANTHROPIC_PROFILE`,
  workload identity federation variables, the SDK's active profile on disk) is
  no longer consulted, for `anthropic` either. aelix never documented it; it
  ran for every provider on this API, against that provider's host. Anthropic
  OAuth (`/login`) and GitHub Copilot Claude requests no longer carry an empty
  `x-api-key` header next to the bearer.
- **An OAuth server can no longer take over your terminal through an error
  message.** When the OpenAI Codex or Anthropic sign-in or token refresh failed,
  aelix put whatever the token endpoint answered into the error, raw and whole:
  a proxy's 48 KB error page carrying `ESC[2J`, an OSC 52 clipboard write and
  `ESC[?1049h` reached `aelix -p`'s stderr intact on every turn after the Codex
  access token expired, and in the TUI it erased the transcript. The same held
  for a 200 answer that is not JSON (a captive portal), for the HTTP reason
  phrase (which may carry ESC even with an empty body; GitHub Copilot was still
  exposed there), for a Copilot device-flow `error_description`, for any
  extension provider's refresh error, and for `/login`'s custom-provider model
  fetch. The same bytes arrived two more ways: a proxy that refuses the
  connection answers with a status line of its own, and its reason phrase went
  raw into the error every sign-in and token-refresh function raises (55,000
  characters measured; aelix's own screens already bounded it, a program calling
  those functions did not), and an OpenAI Codex model request that a proxy
  answers with an error page put that page on `aelix -p`'s stderr, on
  `--mode rpc`'s stdout (its BiDi and 8-bit control characters) and on the TUI's
  screen, which it erased. A proxy that refuses a model request's connection did
  the same through every built-in provider (OpenAI Codex, Anthropic, OpenAI and
  OpenAI-compatible ones such as OpenRouter, Google): 55 KB with `ESC[2J` and an
  OSC 52 write on `aelix -p`'s stderr, and a blank TUI. A sign-in or
  token-refresh error from an HTTP error answer keeps its status; a model
  request's error shows the status only where the provider's SDK puts it in its
  message (a plain-text 403 from Anthropic or an OpenAI-compatible endpoint
  reads as its body alone, as before). Every server string these errors show is
  quoted: at most 512 characters each, with control and BiDi characters
  removed, line breaks removed (a model request's error turns each into a
  space, so its words stay apart), runs of blank space shortened to one and
  leading or trailing blank space not counted. The bound is per string, so a
  message that quotes two (a Copilot device-flow error and its description, or
  a proxy error and its cause) carries about 1.2 KB, and the Codex and
  Anthropic sign-in errors and Anthropic's refresh error, which repeat every
  link of a connection error, up to about 1.8 KB. Those three no longer repeat
  an exception your own code was handling when it called them. The automatic
  retry and the context-overflow compaction still read a model request's whole
  error: a `context_length_exceeded` code or a `502` past the 512th character
  is acted on as before. An extension's own provider builds its own error
  message and is not covered. A Codex token response missing a field is
  described by its keys only — it used to print the response, access token
  included. (#186)
- **A model, a tool or a session file can no longer steer your terminal
  through the transcript (#177).** A tool argument the model wrote reached the
  terminal as written. A `read` path carrying an OSC 52 sequence could write
  your clipboard (in a terminal that allows it), and `ESC [ 2 J` cleared the
  screen. The same held for tool names, tool output, reasoning, answers,
  provider error lines, an extension's custom messages, components and
  tool-result views, and all of it again on `/resume`. Before the transcript
  draws any of these, escape and other control characters, the one-byte CSI
  and the BiDi overrides and isolates are now removed (or shown as a space on a
  header row). Tool output loses its colour codes, as in pi. A coloured
  `git diff` is shown as a diff because of this. A path longer than 160 cells
  is shortened from the front, so the file name stays. An error message shows
  its first 8 lines, each cut to 200 characters with `…` where it was cut, and
  then one `… (N more lines omitted)` line if there were more. The same removal covers the output of your
  own `!cmd` (live and on `/resume`), a tool-result view's title, column
  headers and every value it decodes from JSON, and a BiDi control an answer
  spells as an HTML entity (`&#8238;`). Prose and extension components also
  drop the BiDi marks U+200E, U+200F and U+061C. An extension component's
  hyperlink is kept only when it is a clean `http`, `https`, `file` or
  `mailto` link. The status line, widgets, toasts and modals an extension
  draws outside the transcript are not covered by this change.
- **A catalog's `source` must be one of a few accepted forms, and a relative
  path in it is read from the catalog's own directory, never from the
  directory you run `aelix` in (#131, ADR-0255).** `aelix extension discover
  install` used to hand an entry's `source` to the installer as written, and
  the installer reads anything relative from your current directory: a
  hand-written `"source": "./acme-notes"` (or `file:acme-notes`, or — with
  uv — `acme-notes @ ./acme-notes`) installed the directory beside the
  catalog only when you stood in it, and from anywhere else whatever
  `./acme-notes` that directory held — or failed. A bare `"source":
  "acme-notes"` meant as that neighbouring directory went to the package
  index from everywhere but the catalog's directory. Now a `source` is
  accepted only as: a package name, optionally with `[extras]` and a version
  specifier (handed on unchanged); an absolute URL with its scheme in
  lowercase — `https://`, `http://`, `git+…://`, `git://`, `ssh://`,
  scp-style `user@host:path` (any user), `file:///…`, `file://localhost/…`
  (the host empty or `localhost` in lowercase, no `%`-escape in the path) — or
  `name @ <an https,
  http, git+ or file:/// URL>`, a git repository there as `name @ git+…`
  (handed on exactly as written, `#sha256=` / `#subdirectory=` fragments and
  `[extras]` included — the installer still adds the `git+` to `user@host:path`
  (as `git+ssh://user@host/path`), `git://`, `ssh://` and a bare URL ending in
  `.git`, as for one you type, and no longer puts one in front of a `name @`
  reference, which pip read as a path in your current directory); a path
  starting with `./` or `../`, or a bare `.whl` / `.tar.gz` / `.zip` (or other
  archive, `.tar.lz` included) file name — spaces allowed — which resolves
  against the directory of the local catalog file (the file a symlink points
  to) and installs from that absolute path, `[extras]` kept and never part of
  the file that is installed, hashed and pinned (a file literally named
  `x.whl[feature]` beside `x.whl` changes nothing) — and is refused when the
  catalog is served over HTTPS or git; or an absolute or `~` path,
  which is accepted from any catalog, local or served (`~` is the home of
  whoever installs) and installs as the resolved absolute path. A path must
  exist, and reaches pip or uv as a `file://` URI (`name[extras] @ file:///…`
  with extras, the name read from the wheel, sdist or `pyproject.toml`), never as
  a path string they would parse again — given one, uv installed a different
  directory for a name ending in `[]`, `[x]` or a space, or holding `#`. The
  install record keeps that URI, and `aelix extension update` re-installs a
  recorded path — one an older `aelix` recorded too — through the same URI, not
  the recorded string; a path you typed yourself that the URI cannot carry
  (`./legacy[feature]` on a directory with no `[project] name`) is updated as
  it was installed, the absolute path with its extras, and one extension that
  cannot be updated does not stop `update` for the others (it is named, and
  the exit code is non-zero). Everything else is **refused** before the consent prompt, with the
  entry, the catalog and the accepted forms named — never rewritten: `name @
  ./x`, `name @ x`, every `file:` URL other than `file:///…` /
  `file://localhost/…` (`file:x`, `file:`, `file:/abs`, `file://x`,
  `file://localhost.evil/x`, `file://LOCALHOST/…` — uv read that host as a
  directory in your current one —, `name @ file:x`), a file URL whose path holds
  a `%`-escape (uv decoded `%23` and installed a neighbouring directory — name
  the path directly; for `git+file:` it was pip that cut the path there and
  cloned another repository), a `git+file:` URL naming a host other than
  `localhost` (git ignores the host — an unsupported spelling), a URL scheme with a capital
  letter (`FILE:///…`, `Https://…` — uv took `FILE:` for a directory in your
  current one), `name @ https://…/repo.git` without `git+`, a source starting
  with `-` (the installer would read it as an option), a relative path without
  `./`, a package name ending in `.git`, a path containing `#` (uv cuts it
  there in every spelling), extras on a directory with no `pyproject.toml`
  `[project] name`, an unversioned package name that also names a file beside
  the catalog (`acme-notes[extra]` beside `./acme-notes` too; `acme-notes==1.4.0`
  is not checked), and a package spec that names a file in your current
  directory (aelix's installer takes a target that exists on disk for a path
  and would have installed that instead). The `Resolved` line shows the absolute spec next to
  what the catalog said; the `Resolved` and `Install extension from` lines,
  these refusals and every `verify` line print a path through the
  terminal-safe filter (control characters removed), and the command line
  shown under them escapes them as before. A catalog registered by a relative
  path in a hand-edited settings file is read from the directory you refresh
  in; `discover --refresh` now says so and records it, and `discover install
  --catalog <the path as registered>` still selects it; `--catalog` also
  accepts a relative path naming a registered catalog file from where you run
  it (`source add --catalog catalog.json`, then `--catalog catalog.json` beside
  it) or another spelling of its absolute path (through a symlink); a selector
  that names a registered catalog with no cached copy yet, or one whose last
  refresh failed, says that and how to fetch it — while `AELIX_OFFLINE` or
  `PI_OFFLINE` is set, how to refresh online, since an offline refresh skips a
  network catalog — and only one that names no registered catalog says so. `aelix
  extension install ./x` — what you type yourself — is unchanged, except that
  `./x.whl[feature]` counts as the path `./x.whl` when that file exists, as
  pip already read it, even beside a file literally named `x.whl[feature]`.
  `aelix extension index --relative` now writes `./name.whl` paths measured
  from the catalog file (so `--out` elsewhere gives `../…`; `--out -` measures
  from the scanned directory), and catalogs an older `--relative` wrote, with
  bare file names, still install from a local catalog file. The guarantee is
  for a catalog you trust: aelix never resolves an entry's `source` against
  the directory you run `aelix` in, and never hands the installer a string it
  would read from there. Configuration the installer discovers by itself is
  outside it: with the uv backend, a `uv.toml` or `pyproject.toml` `[tool.uv]`
  in that directory or a parent is uv's own, which aelix honours on purpose
  (ADR-0200), and in a cloned repository it could make a package-name entry
  install a wheel from there — closed by the next entry (#392). A hostile
  catalog can already name any package or URL; odd spellings in one are
  refused where cheap, and the forms left open are listed as known limits in
  the private-catalog guide.
- **A cloned repository's `uv.toml` or `[tool.uv]` can no longer choose what a
  catalog installs (#392, ADR-0255 §15, ADR-0200 §12).** With the uv backend,
  `aelix extension discover install` ran `uv pip install` in your current
  directory, and uv reads a `uv.toml` or a `pyproject.toml` `[tool.uv]` table
  from that directory and every parent: in a cloned repository whose
  `[tool.uv]` said `find-links = ["./w"]`, a trusted catalog's `"source":
  "local-ext"` installed the repository's own `local-ext` 9.9, exit 0
  (measured; `aelix extension update` of that record did the same). Now
  `discover install` and every `update` run the installer — uv or pip — in
  `~/.aelix/agent/installer-cwd` (under your agent dir; on macOS and Linux
  only you can read it). It holds a `pyproject.toml` and a `uv.toml` that
  aelix writes and that set nothing: uv looks for a project (the nearest
  `pyproject.toml`) before it looks for configuration, and these two files
  make it find no project there and stop at the empty `uv.toml`, so no
  project configuration is read at all — none from your current directory,
  its parents or the directories above the agent dir, even a `pyproject.toml`
  with a `[project]` table (measured on uv 0.11.14, 0.11.19 and 0.12.23).
  With pip, a `pip/` package in your current directory no longer runs as the
  installer for these installs.
  Your user-level `uv.toml` (`~/.config/uv/uv.toml`, `%APPDATA%\uv\uv.toml`),
  the system one, your `pip.conf` and your environment variables (`UV_*`,
  `PIP_*`) still apply, so an organisation's index pin there keeps working; a
  `uv.toml` or `pyproject.toml` in your home directory is project
  configuration to uv and no longer applies to these installs. What this
  protects against is the current directory: your own environment and your
  user- and system-level installer configuration are yours (a repository
  cannot set them — a project `.env` is admission-controlled, ADR-0203), so
  aelix passes them on exactly as set. The consequence, a known limit: a
  RELATIVE path in them (`PIP_FIND_LINKS=./wheels` or `file:wheels`,
  `UV_FIND_LINKS`, `UV_INDEX_URL=./simple`, `PIP_TARGET`, `UV_PROJECT`,
  `UV_WORKING_DIRECTORY`, `PIP_CONFIG_FILE=pip.conf`, a relative `find-links`
  in `pip.conf`) is read from the installer directory for these installs — on
  uv too, where aelix reads your `pip.conf` to pass its index on: a relative
  `PIP_CONFIG_FILE` is opened in the installer directory, as pip would open it,
  not in your current directory — use absolute paths. The one
  variable aelix checks is `UV_CONFIG_FILE`, which uv reads instead of every
  other configuration file: for these installs on uv it must be an absolute path
  exactly as uv reads it (no leading or trailing space, no `~`, no `file:`
  URL, not empty), or the install is refused with "Set UV_CONFIG_FILE to an
  absolute path." — a relative one would quietly read aelix's own empty
  `uv.toml` there (measured on uv 0.11.19 and 0.12.23). The consent block
  says where the installer runs; if aelix cannot create the directory — or
  finds a link there, or a link in place of either file, when it prepares it
  — the install is refused rather than run anywhere else (a link swapped in
  later, while the consent prompt waits, is not caught). Several aelix
  processes preparing the directory at once do not refuse one another:
  each file is written to a new file and renamed into place. A relative
  `--index-url ./simple` typed on `discover install` is read from where you
  typed it (a `file:/…` URL is a URL and passes as written). A project you
  choose with an absolute `UV_PROJECT` or
  `UV_WORKING_DIR` is read as uv reads it, and its index pin still keeps pip's
  index from being passed on to uv. From the Python API, `install_extension`
  runs what the catalog resolver returns (`resolve_entry_target`: an
  `extension_catalog.ResolvedPath`, or the new `extension_catalog.CatalogSpec`
  for a name or URL) in the installer directory, whatever the caller passes —
  there is no flag for it; `resolve_entry_source` returns a `CatalogSpec` too;
  a plain string is a source you typed and runs where you are;
  `verify_and_pin` follows the same rule for its `pip download`.
  A runner passed to `run_extension_command_async(..., runner=)` is handed the
  installer directory (`runner(argv, cwd=…)`); one that cannot take it is
  refused for these installs. `update` runs
  every record there, ones you installed by typing included: none was chosen
  in the directory `update` happens to run in. Whether aelix passes pip's
  index configuration on to uv is decided from the directory uv runs in, by
  the files uv really reads there: the project file uv finds (from the
  project's workspace root, as uv searches), your user-level file
  (`%APPDATA%\uv\uv.toml` on Windows) and the system one — before, a
  `[tool.uv]` index in any parent directory switched it off, even one uv
  never read. Unchanged, and a stated limit: a typed `aelix extension install
  <spec>` still runs in your current directory and reads its uv configuration
  there. At that point a typed pip install or `aelix extension remove` also
  imported a `pip/` package from your current directory first; #394 now blocks
  that automatic cwd lookup with Python's `-P` option.

## [0.1.0-beta.2] - 2026-09-09

### Changed

- **A tool card now shows 5 lines of output, not 12.** A `read` of a 40-line
  file used to spend 14 rows of scrollback on an 80-column terminal — one
  header, twelve body lines and the elision footer — for output the model had
  already summarised; it now spends 7. The footer still carries the whole body:
  `… (+35 more lines · /expand 1)`, and `/expand 1` prints it. The 40-line cap
  on **error and diff** cards is deliberately untouched: a Python traceback's
  diagnostic tail sits at the bottom, so head-truncation eats exactly the line
  you need (ADR-0112).
  **What you give up:** any ordinary result between 6 and 12 lines used to
  render whole and now ends in `… (+N more lines · /expand N)`. A 12-line `bash`
  result goes from 13 screen rows and no footer to 7 rows plus a `+7`. At the
  bottom of that range you pay without saving anything — a 6-line result is 7
  rows either way, and the change buys you a `(+1 more lines · /expand 1)`
  footer in place of its last line; the saving only starts at 7 lines. A
  **successful** `agent` card whose result carries no extra note loses its
  `[agent … · yolo · …]` footer to the cap once the child's summary runs past 3
  lines (it already did past 10); a failed delegation renders on the 40-line
  error path and keeps it far longer. `/expand N` still prints it either way.
  And because every result over 5 lines now takes an `/expand` id instead of
  every result over 12, the 100-slot store turns over faster: in a long session
  an older `/expand N` expires sooner than it used to (measured at width 80:
  one 100-line `read` followed by 100 six-line `bash` results keeps `/expand 1`
  at cap 12 — 1 id minted — and loses it at cap 5 — 101 minted, 100 retained).
  **This reaches fresh installs and anyone whose `settings.json` has no
  `toolCardMaxLines` key** — a value you saved through `/settings` is kept
  exactly as it was. The row still spans 3-40 and still applies to the next card
  without a restart. See
  [#247](https://github.com/handochan/aelix-ai/issues/247) and ADR-0112.
- **The statusline says how hard the model is thinking, without being asked.** The
  🧠 segment — `off` / `low` / `medium` / `high` / `xhigh` — shipped on 2026-08-07
  and was off unless you found it in `/statusline`. It is on by default now, and it
  sits between the model and the context meter:
  `● default  ·  ⏵⏵ all  ·  📂 ~/dev/aelix-ai  ·  ✱ gpt-5.6-codex  ·  🧠 high  ·  ◔ 24% · 96.4K/400K  ·  ⎇ main`.
  That is where the multi-line statusline already put it — right after the model —
  and it is the position that survives a narrow terminal: at the end of the row,
  where the segment used to sit, it was clipped off the glass at 80 and at 100
  columns and did not render at all.

  **Who gets it:** anyone installing fresh, and anyone who has never saved
  `/statusline`. The enabled set is persisted and a file on disk is read verbatim,
  so if you have saved that picker even once the segment stays off until you check
  **Thinking level** yourself. Your file is not migrated, deliberately: it cannot
  tell "never saw this" apart from "turned this off". **If your saved file already
  has the segment on**, it does move — the footer renders in registry order, so the
  🧠 leaves the end of the row and lands after the model, and on a narrow terminal
  it may now be visible where it used to be cut off.

  **What you give up:** one more segment on a row that is clipped, not wrapped.
  Measured in this repository's own checkout (`📂 ~/dev/aelix-ai` — the numbers
  move with your cwd): at 80 columns the branch was already off the row before this
  change and the meter already read `◔ 24% · 96.4K`, and now the meter is down to a
  bare `◔`; at 100 columns the row loses `⎇ main` outright, which used to fit. And
  a session started on a model with no reasoning support now reads `🧠 off` rather
  than hiding the segment — its existing, deliberate behaviour, visible out of the
  box. Note that the segment reports the level you *set*: switching to a
  non-reasoning model mid-session keeps showing the last level until you change it
  — since #251, with the tier that model actually receives next to it, so the row
  reads `🧠 high (off)` rather than a bare `🧠 high`. See
  [#248](https://github.com/handochan/aelix-ai/issues/248) and ADR-0160.

- **`/thinking`, `/settings` and the statusline now name the tier the model
  actually receives.** Until now every one of those surfaces printed the level name
  and nothing else, which was wrong in two directions at once. Pick `xhigh` on
  `claude-opus-4-6` and the request has been carrying `max` all along — 24 catalog
  rows map `xhigh` onto `max` — while the screen said `xhigh`. Pick `xhigh` and
  then `/model` your way to `openai/gpt-5.1`, which has no `xhigh`, and the level
  you set survives the switch but the adapters clamp it down to `high`; the screen
  still said `xhigh`. Both now read `✱ 6. xhigh (max)` in the picker and
  `🧠 xhigh (max)` in the statusline, or `xhigh (high)` for the clamp. **The
  parenthesis is the tier that goes on the wire, and it can be above the level as
  well as below it** — `deepseek-v4-pro` supports neither `low` nor `medium`, so
  choosing `low` there reads `low (high)`.

  Run the rule over the whole vendored catalog and it adds the parenthesis to
  **2909 of the 8562 (model, level) pairs**: 51 renames across 37 models, 1013
  clamps across 937, and 1845 across the 369 models that do not reason at all —
  leave the level at `high`, switch to `openai/gpt-4.1`, and the statusline says
  `🧠 high (off)`, because that is what the request carries (those adapters send no
  reasoning field whatsoever). Thirteen catalog rows served by the Google adapter
  (the Gemini 1.5 / 2.0, Gemma and Vertex-hosted Llama entries) are a known
  exception in the other direction: that adapter turns a disabled level back into
  a thinking request, so `(off)` understates them. That is an adapter defect,
  tracked as [#256](https://github.com/handochan/aelix-ai/issues/256), not papered
  over here.

  The suffix appears **only** when the names genuinely differ. A model that calls
  the level what you called it gets one bare word, as do a mapping that differs in
  case alone (Google's `HIGH` / `LOW` / `MINIMAL`, 16 rows), a mapping to a number
  rather than a name, and any model the display cannot interrogate. `off` is always
  bare even where the catalog renames it to `none`: the harness folds `off` into
  "no reasoning requested" before an adapter ever sees it, so there is no true
  value to put in the parenthesis. (The cost of that fold is worth knowing: on the
  100 catalog models that do not support `off`, asking for `off` sends no reasoning
  field at all and the model thinks at its own default.)

  Two smaller consequences. The `✱` in the picker now marks the row you will
  actually get — open `/thinking` on gpt-5.1 while the session still holds `xhigh`
  and the marker sits on `high`, where before it sat on nothing. And the statusline
  segment grows by up to **ten** columns on a row that is clipped rather than
  wrapped — `🧠 high (default)` on `groq/qwen/qwen3-32b` and `🧠 minimal (medium)` on
  `openai-codex/gpt-5.1-codex-mini` are the two widest, and both are rows you can
  pick straight out of `/thinking`; the headline `xhigh (max)` costs only six. With
  #248 in the same release the 🧠 sits right after the model, so those columns push
  the tail of the row (`⎇ branch` first) rather than truncating the segment itself.
  What the picker sends to the setter is unchanged: the level name, never the
  label. See [#251](https://github.com/handochan/aelix-ai/issues/251) and
  ADR-0155.
- **A `models.json` `!command` credential helper now runs once per registry
  load, not once per request.** `get_api_key_and_headers` is the harness's
  per-request auth callback, and it re-forked a shell for the provider's
  `apiKey` and for every `!command` header value on every single request —
  measured on darwin, a provider with a `!command` key plus one `!command`
  header spent 8.44 ms of the event loop per call, running the *same* two
  commands each time. On a box that lands on PowerShell that is one shell start
  per distinct `!command`: 431.8 ms each, measured with pwsh 7.6.5 **on macOS**
  — Windows PowerShell 5.1, which is what a stock box actually resolves, is
  unmeasured and typically slower. A turn is one request plus one more per tool
  call, so that cost was paid over and over, in a synchronous call inside an
  async callback, with the TUI unable to repaint. The resolved value is now kept
  on the registry that resolved it; a repeat is a dict lookup.
  **What you give up:** a credential your helper mints is read once and reused
  until the registry reloads, so one rotated underneath a running session keeps
  going out until then. Any turn that ENDS IN AN ERROR drops it — in the TUI and
  in `-p` alike — so a rejected credential costs one bad turn rather than a
  session, and in the TUI `/reload`, `/login` and a restart drop it too. A
  headless run has none of those three, so a helper minting a short-lived token
  wants a lifetime longer than the run. Nothing is kept for a command that fails
  or prints nothing, so a helper that starts working is picked up on the next
  request; an `apiKey` naming an environment variable is still read every time,
  it was never the cost. See
  [#240](https://github.com/handochan/aelix-ai/issues/240) and ADR-0140.
- **Pre-release tags now publish to PyPI.** The `publish` job used to skip any
  tag with a hyphen, so the four PyPI names carried nothing but a `0.0.0a0`
  reservation placeholder — and because pip and uv take the newest pre-release
  when *every* candidate is one, `uv tool install aelix@latest` installed that
  placeholder, found no entry points, and **removed the working aelix** it was
  asked to upgrade. From `v0.1.0-beta.2` the tag publishes its PEP 440
  pre-version (`0.1.0b2`); a plain `pip install aelix` stops seeing it the
  moment a stable version exists, so `0.1.0` is protected by the version
  itself rather than by a job gate (ADR-0240). The version assert that runs
  before the build now also reads `[project.optional-dependencies]` — the
  root's `aelix-coding-agent[tui]==X` pin was outside its loop, so a bump that
  missed it would have shipped a `pip install 'aelix[tui]'` demanding a
  version not on the index (mutating that pin to `0.1.0b9` now fails the
  assert; the unmodified workspace passes). **What you give up:** a
  pre-version is permanent on PyPI and a half-failed eight-artifact upload is
  a half-release; the remedy for either is the next beta number, never a
  retry of the same one.
- **`install.sh` / `install.ps1` run `uv tool update-shell` themselves when
  `aelix` is not on PATH after the install**, instead of printing the command
  and hoping. It appends uv's tool bin to the shell rc (idempotent) and the
  installer then says to open a new terminal; the current shell's PATH is
  still not changed, and a failure of `update-shell` falls back to the old
  by-hand hint.

- **A command that backgrounds a server now comes back when the command does.**
  `npm run dev &`, `nohup … &`, anything that exits 0 while a helper keeps the
  pipe: the bash tool used to read that pipe until the last holder closed it, so
  the tool call stayed open for the helper's whole life, with **no ceiling at
  all** — measured 4.05 s for a 4 s helper, 13.98 s for a call that had asked
  for 10 s, and 4.06 s for one that had asked for 1 s and was told it had
  succeeded within its deadline. Typing `!npm run dev &` into the TUI was the
  same freeze with nothing to bound it, since `!` commands carry no timeout. It
  now returns once the output has been quiet for 0.1 s after the command exited
  — the rule Aelix's contained runner already uses, the rule this same tool
  already used after a kill, and the rule Pi uses — under a 2-second ceiling
  for a helper that never falls quiet, and, where you asked for a deadline, no
  more than one 0.1 s grace past it: a root that exits just inside its own
  deadline still keeps its own tail (measured on darwin, a root that exits
  0.985 s into a 1-second call with a helper still holding the pipe: back at
  1.088 s, `exit_code=0`).
  **What you give up:** output the helper writes after that point is not
  captured and the model does not see it — and because a backgrounded program's
  stdout is a pipe, it is block-buffered and usually flushes only when it exits,
  so in practice you see **none** of a backgrounded server's output, not just
  its tail (redirect it — `nohup … > dev.log 2>&1 &` — and read the file, or run
  it unbuffered). While that helper is alive the finished call also keeps one
  background reader and one pipe open for it; they go away when the helper does.
  That is the trade Pi makes too. The helper itself is untouched — it keeps
  running, as it did before. See
  [#232](https://github.com/handochan/aelix-ai/issues/232) and ADR-0238.

- **Seven `sort`/`date`/`hostname` spellings stop being auto-approved, and one
  read starts.** AUTO mode decided whether a command was read-only by looking at
  the *shape* of its arguments — a `-` in front meant "flag", a `/` in front
  meant "writes". Both premises are false on POSIX, and the measurement that
  ended them was taken on a developer machine, not a Windows one:
  `sort -o out.txt in.txt`, `sort -oout.txt in.txt`,
  `sort --output=out.txt in.txt`, `sort --output out.txt in.txt`,
  `date --set=2030-01-01` and `hostname -b` all ran without asking you, and each
  writes a file or sets the system clock. So did
  `sort --compress-program=/tmp/x`, which executes `/tmp/x` on every temporary
  file. All of them prompt now. `sort --files0-from=` and `sort --random-source=`
  prompt too, for the reason this gate already refuses `findstr /f:`: one
  argument becomes an unbounded set of reads.

  Going the other way, `sort /etc/hosts` is auto-approved again under a shell
  that resolved to `bash`. It is a plain read, and it was refused only because
  `/etc/hosts` looks like a `cmd` switch to a rule that could not tell which
  shell it was reading for. Under `fish`, or when the shell cannot be resolved
  at all, it still prompts — the same "assume nothing" answer, now stated as a
  default instead of guessed at. See ADR-0237 and
  [#204](https://github.com/handochan/aelix-ai/issues/204).

- **Your own turns are a thicker band.** The echo bar that marks a human turn
  now carries one painted row above and below the text, so the turn reads as an
  object with a top and a bottom edge rather than as a single coloured line. The
  blank lines that fence it off from the renderer above and below are unchanged
  — the new rows go *inside* the bar's own ground, not outside it.

  The cost is vertical: a turn is five rows instead of three, a fifth of an
  80x24 screen. That is the trade, and it is why this is a deliberate change
  rather than a tidy-up.

- **`yolo` no longer asks before delegating — it tells you.** Choosing `yolo`
  means "run mutating tools without a prompt", and a delegation was the one
  thing it still prompted for: on every `agent` call, a dialog whose only two
  options were "Run with the inherited posture (yolo)" and "Cancel". That is a
  confirmation with no real answer, which is the shape this project already
  removed everywhere else because it teaches you to dismiss the prompts that do
  matter. It now starts the child immediately and writes one line to the status
  line instead — the profile, the posture it runs at, the file it came from, and
  how many tasks it was given — before the child does anything, for as long as
  it runs, and cleared when it finishes. The finished tool card already names
  the posture in its `[agent … · yolo · …]` footer, so that is the part that
  stays in your transcript.

  `auto-accept-edits` and `auto` still ask. The `0.1.0-beta.1` section's own
  entry is corrected in place rather than superseded — that section has not
  shipped, and correcting an unreleased line rather than recording a removal is
  the convention `92b3f35` set for exactly this case.

  What does not change: the child is still clamped to at most your own posture,
  the guardrail still hard-blocks catastrophic patterns inside it, the
  per-prompt and per-session delegation caps still apply, the status line still
  shows it running, and a *project-local* profile still cannot widen itself.
  What you lose is the chance to say "Cancel" to one specific spawn before it
  starts; Ctrl+C and shift+tab are still live. See ADR-0231 and
  [#196](https://github.com/handochan/aelix-ai/issues/196).

- **The `@` menu uses the `fd` Aelix downloaded, and stops pretending both
  enumerators agree.** `@` completion enumerates the tree with `fd` when it can
  find one and with a plain `os.walk` when it cannot, and the code said the two
  "produce the same set of matchable paths on every machine". They never did.
  `fd` hides what git ignores and the walk does not, so what the menu offered
  depended on whether you had `fd`. Worse, it looked only on `PATH` — never at
  `~/.aelix/agent/bin/fd`, the copy Aelix downloads the first time you use
  `find` — so the machine this was measured on had `fd` and used the walk
  anyway; because that checkout keeps nine agent worktree copies of the repo,
  the menu was matching 12618 paths instead of 1482, spending three of the eight
  rows for `@completionpy` on stale copies and never offering the file actually
  named. It now looks there **first**, the same order `find` and `grep` use, so
  the two run the same `fd` — which also means that on a machine
  with no `fd` the menu can narrow part-way through a session, once your first
  `find` has fetched one. On a clean clone of this repository both arms offer the
  same list: the difference is as large as whatever git ignores that the shared
  exclude list does not already name.

  Three smaller disagreements went with it. The shared exclude list now really
  applies to both arms — without `fd` the menu had been offering the `.git`
  pointer file in every worktree, and any file named `build`/`dist`/`node_modules`;
  the cost of that consistency is that a root-level `build` *script* stops being
  fuzzy-matched on the walk arm too, matching what `fd` already did — the plain
  `@` listing still offers it, on either arm. `fd` was asked for files and
  directories only, so it dropped every symlink the walk listed — fixed, and the
  two sets now match exactly on a symlink tree. And one
  disagreement is bigger than it looks and is documented rather than fixed: both
  enumerators stop after 20 000 paths, and the files git ignores count toward that
  limit on the walk — and, since #238, on the `fd` arm whenever **Gitignore in @
  menu** is off — so in a repo with a large ignored build tree either arm can run
  out of budget inside it. On the walk that means never reaching your real source
  directories at all (measured: an ignored 22 000-file `target/` next to twelve
  real source files gave the walk two menu rows where `fd` — with the toggle on,
  and so never entering `target/` — gave twelve). On the off `fd` arm it means the
  kept set stops being stable, and where the ignored files are spread over many
  directories rather than one tree it can drop real directories too. An
  undecodable filename also still reaches `fd`'s list as `�`; what the walk
  arm does with it is untested (no filesystem here can hold such a name). Nothing
  you type is ever passed to the subprocess, and `fd` is still never required.
  See ADR-0193 and [#231](https://github.com/handochan/aelix-ai/issues/231).

- **A `!command` in `models.json` or `auth.json` that tries to prompt now fails
  at once, with a reason, instead of stalling for ten seconds without one.** A
  `!command` runs in a process group of its own, which is never the terminal's
  foreground group, so the kernel STOPS it the moment it reads the terminal
  (`SIGTTIN`) or turns echo off (`SIGTTOU`) — unless it blocks or ignores that
  signal, which POSIX permits. Aelix now watches for exactly those two stops
  while it waits, ends the tree when one arrives, and says what happened:
  `The command stopped reading the terminal (SIGTTIN): …`. Measured under a real
  pty on macOS **and on Linux**, the resolver detects the stop and kills in
  0.052–0.059 s; what you wait for is that plus Aelix's own startup. Before this,
  the same helper burned the full ten-second timeout and the message said only
  `Failed to resolve API key for provider "x" from shell command: …`, with
  nothing about a terminal in it.

  Who benefits: helpers that read the terminal **themselves** — `ssh` or
  `ssh-add` with no askpass, `sudo`, a git credential helper that does
  `read </dev/tty` or `stty -echo`, and `gpg --pinentry-mode loopback`
  (measured 0.357–0.359 s).

  Two things this does not catch, said here rather than found later. A
  passphrase prompt mediated by `gpg-agent` — the normal `gpg` and `pass`
  architecture — is invisible to it, because the agent is a daemon in its own
  session and it is the agent that forks `pinentry`, where no job-control check
  applies; measured with gnupg 2.5.22, `!gpg -d` and `!pass show` still take the
  full ten seconds with no named cause. And "the helper stopped before printing
  anything" describes the shell helpers that were measured: a background process
  can still *write* to the terminal, and `sudo` and `openssl` print their prompt
  before failing. An askpass program or a GUI prompt that nobody answers still
  costs the whole timeout too — it never reads the terminal, so it never stops.

  The process group itself does not change: `setsid` would be worse, not better.
  Measured, a `setsid` helper that opens the terminal by path faces no
  job-control check at all and took the line the user had typed at Aelix's own
  prompt — silence in place of a visible stop. On Windows there is no background
  process group and no stop to detect: a helper that reads the console directly
  can still prompt there and still burn the whole timeout unanswered, and nobody
  has watched that happen. See ADR-0238 and
  [#226](https://github.com/handochan/aelix-ai/issues/226).

### Added

- **102 models the catalog was missing since 2026-08-19**, refreshed from
  models.dev with `scripts/refresh_catalog.py` (1427 → 1529 rows across the
  same 35 providers; nothing removed, nothing already shipped changed —
  measured by diffing the two JSONs row by row). Selectable now on their
  direct providers: `claude-fable-5-1` (anthropic), `gpt-6-astra` (openai),
  `gemini-3.8-flash` (google), and on github-copilot `claude-fable-5.1` +
  `gemini-3.8-flash` under the Copilot conventions the pin tests enforce
  (200K cap on Claude, zero per-token cost). 399 upstream rows were refused
  by the script's transport rule, as before. **What a refresh cannot do:**
  models.dev carries no `thinkingLevelMap`, so every added row arrived
  without one — and a reasoning model without a map has no `xhigh`.
  `claude-fable-5-1` would have clamped `xhigh` to `high` while
  `claude-fable-5` beside it kept `xhigh`. Nine flagship rows got their
  predecessor's map copied by hand (fable-5-1 ← fable-5, gpt-6-astra ←
  gpt-5.5, gemini-3.8-flash ← gemini-3.5-flash, on each provider where the
  predecessor had one) and are pinned equal to it in
  `tests/test_catalog_corrections_are_pinned.py`; the 12 rows whose
  predecessor has no map either (bedrock, the aggregators) were left as
  upstream gives them. The refresh pipeline itself (#172) is still not built —
  this is the manual path it will replace.
- **`/settings` can put the git-ignored files back in the `@` menu.** Since the `@`
  menu started using the `fd` Aelix downloads, it stops offering what git ignores —
  which is what most people want and is wrong for anyone whose build output,
  vendored tree or worktree copies are the files they keep typing `@` at. The new
  **Gitignore in @ menu** row switches it off. Off, `fd` is asked for `--no-ignore`,
  which lifts `.gitignore` (root and nested), `.git/info/exclude`, `.ignore`,
  `.fdignore`, your global ignore file, and the same files in directories *above*
  the one you are in; the shared exclude list — `.git`, `node_modules`, `.venv`,
  `__pycache__`, `dist`, `build` — still applies, and so does the 20 000-path limit,
  which the wider list now spends on ignored files too, so a large ignored population
  can push the menu past that limit — and past it, which paths you get is decided by
  `fd`'s parallel walk: not stable between runs, and in a checkout with many ignored
  directories able to drop real ones. That is why it defaults to **on**. It applies
  immediately: the next `@` you type uses the new setting, no restart. With no `fd`
  — offline mode, Termux, or simply a session where the model has never run `find`
  — the menu already offers everything, so the row changes
  nothing until an `fd` exists; when one appears mid-session the setting you chose
  is honoured at the next `@`. Measured on the checkout this was built on: on, 1 498
  candidates; off, 12 630 — exactly what the no-`fd` fallback offers there. See
  ADR-0193 and [#238](https://github.com/handochan/aelix-ai/issues/238).

- **AUTO mode can read PowerShell and `cmd`, so it stops prompting for every
  line on Windows.** Until now the gate parsed every command with a *bash*
  grammar and then, on any shell that grammar does not describe, downgraded its
  own verdict to a prompt. That is why nothing was ever mis-run there — and also
  why AUTO on Windows was indistinguishable from not shipping it. `pwsh`,
  `powershell` and `cmd.exe` now get a classifier that reads their own syntax:
  `Get-ChildItem`, `dir`, `type a.txt`, `echo hello` and `date /t` run without a
  prompt, while `Remove-Item -Recurse -Force C:\`, `del /s /q C:\Windows`,
  `date 01-01-2030`, `iex`, `Set-ExecutionPolicy Bypass` and a redirect into
  `C:\Windows` are blocked outright rather than merely asked about.

  Reading is not the same claim as harmless to read: `type`, `Get-Content`,
  `Select-String` and `findstr` still prompt when the target is a credential
  store — `.ssh`, `.aws`, `.gnupg`, `.docker`, `.kube`, `.azure`, gcloud,
  Terraform, `gh`, `.git-credentials`, `.env`/`.env.local`, a private key — or a
  UNC path, where the read itself is an outbound SMB connection to whatever host
  the command names.

  The bash classifier's DENY is kept underneath as a floor, so `rm -rf /`,
  `find . -delete` and `curl … | sh` still block under every shell — including
  the ones this adds. The ALLOW lists are deliberately incomplete: an unlisted
  name, an unread argument or an unparsable line costs a prompt, which is the
  direction that is safe to be wrong in. Windows remains EXPERIMENTAL — this is
  covered by tests, and nobody has yet run it on a real Windows box. See
  ADR-0237 and [#204](https://github.com/handochan/aelix-ai/issues/204).

- **422 more models, including the ones released this month.** The catalog had
  not been refreshed since it was ported: of the models upstream lists with a
  2026-04 release date it carried 63%, and of the 2026-08 ones, 5%. It now
  ships 1427 models across the same 35 providers — `grok-4.6`, `qwen3.8`,
  `deepseek-v4-flash-0731`, `deepseek-v4-pro-0813`, `glm-5.3`, `kimi-k3` and
  the rest, each on the providers that actually serve it.

  Nothing that was already in the catalog changed. The refresh
  (`scripts/refresh_catalog.py`) can only append — the values a maintainer
  corrected by hand are not something it is careful about, they are outside
  what it can write.

  401 upstream models were deliberately left out. 325 of them cannot call a
  tool, and this is a coding agent: a model that cannot call a tool cannot edit
  a file, and offering it would sell you a session that fails on its first
  action. The other 76 have no transport we could establish without guessing,
  and a model that appears in `/model` and then cannot reach its provider is
  worse than one that is absent. See ADR-0232.

- **A stale `uv.lock` now fails the build instead of a stranger's install.**
  A dependency added to a `pyproject.toml` without re-running `uv lock` used to
  pass every test — the suite imports from a dev environment where the package
  was already present, and nothing in the repository read the lock at all. The
  cost landed on whoever built from a clean checkout, as a missing module at
  import time. The gate compares the lock against all five manifests in both
  directions and names `uv lock` as the fix. See ADR-0233.

- **Aelix tells you when a newer release exists.** At most once a day, an
  interactive launch reads a static file on this project's own GitHub Pages
  site and, if there is something newer, prints one line naming the version and
  **the upgrade command for how you actually installed it** — re-running
  `install.sh`, `uv tool upgrade`, or `pipx upgrade`, whichever applies. A git
  checkout is detected and left alone.

  There is no universal upgrade command, and the wrong one is destructive:
  `uv tool install aelix@latest` — uv's own suggested remedy — resolves the
  PyPI name reservation, finds no entry points, and **removes the tool**, so a
  user who follows it loses their install. That is why the command is detected
  rather than printed from a constant.

  The request carries no version, no operating system, and no identifier: the
  server learns an IP address and nothing else. It creates no telemetry sink.
  Turn it off in `/settings` → *Check for updates*, or skip it with `--offline`
  / `PI_OFFLINE=1`. Every failure — offline, DNS, timeout, a malformed
  response — is silent, because a startup complaint about a failed *check* is
  worse than no check. Nothing is downloaded or executed; installing the update
  is a command you run yourself.

- **Four current flagship models are selectable** — `claude-opus-5` on both
  `anthropic` and `github-copilot`, and `gemini-3.6-flash` / `gemini-3.7-flash`
  on `google`. Added by hand from the canonical source, following the
  conventions the catalog already encodes: the Copilot row keeps the documented
  200K seat cap and zero cost (a Copilot seat is a subscription, not metered),
  while the direct Anthropic row keeps its full 1M context. A refresh pipeline
  is still open (#172).

- **A bundled `general-purpose` agent profile.** Alongside the read-only
  `explorer`, a default install now ships a full-toolset worker that can read,
  edit, and run commands to take on a delegated task whole — so basic
  multi-agent delegation works out of the box for real work, not only for
  read-only investigation. It is a `leaf` profile (it does not delegate further)
  and inherits your approval policy (its edits and commands go through the same
  consent you are under). Delegation stays off by default (`--agents` /
  `features.agents`); user and project profiles still shadow it by name.

- **An installed extension's `aelix-plugin.toml` is now read** (#91, ADR-0204).
  A package that declares an `aelix.extensions` entry point — what
  `aelix extension install <pkg>` gives you, and how the marketplace ships
  packs — previously had its manifest ignored entirely, so every
  `contributes.*` family it declared (tools, commands, themes, TUI widgets,
  MCP servers, hooks) was silently inert. The manifest is now resolved from
  the distribution's installed metadata **without importing the package**, so
  an installed pack's contributions reach the runtime and its capability
  declarations are enforced before any of its code runs.
- **Optional `aelix.manifests` entry-point group** (#91). Declare
  `<same-name-as-your-aelix.extensions-entry> = "<dotted.package>"` to point
  the host at the directory holding your `aelix-plugin.toml`. Needed only if
  the manifest does not sit in the entry module's own package, or if your pack
  is a single module and so has no package directory. Purely optional: a pack
  that omits it behaves exactly as it does today.
- **`aelix extension verify`** (#91, ADR-0207) — an import-free command that
  reports, per installed `aelix.extensions` endpoint, whether its manifest will
  bind, and exits 0 only when every reported endpoint is `BOUND` (non-zero for
  `ABSENT` / `MALFORMED` / `MISPLACED` / `FENCED` / `UNPROVEN` / `UNTRUSTED` /
  API-incompatible). Built for a catalog-submission CI. It never imports the
  pack. The `ABSENT` report names the usual cause — a **setuptools default build
  silently drops `aelix-plugin.toml` and `themes/*.toml` from the wheel** — and
  the fix. `aelix extension list` now annotates the same verdict, so
  "installed, no manifest, declarations inert" is visible instead of silent.
- **A buildable starter extension** at
  `packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/` (#91,
  ADR-0207) — a hatchling package whose wheel actually ships its manifest and
  theme — plus a new "Packaging your extension" section in the authoring guide
  naming both build backends and the setuptools `package-data` requirement.
- **Catalog policy** (#91, ADR-0207): a pack listed in the official catalog must
  yield a **bound** manifest (`aelix extension verify` exits 0) — it need not
  declare any contribution, but a manifest-less entry-point pack is not
  catalog-eligible. An arbitrary `pip install <pkg>` pack keeps the manifest
  fully optional. `BOUND` is an auditability floor, not a safety verdict.
- **`--trust-extension-path` is documented** (#91). The flag shipped with zero
  doc coverage, which matters because it is the escape hatch for the one
  workflow every extension author uses: `pip install -e` loads a pack
  **without** its manifest, so declarative contributions silently vanish while
  imperative registration keeps working. The authoring guide now explains the
  downgrade, the flag (it takes the PEP 503 *distribution* name, is repeatable,
  and persists nothing), and the fact that a newly installed pack's
  `contributes.mcp_servers` needs a process restart — the manifest scan runs
  once at startup, before the first harness build.
- **"Upgrading / uninstalling" is documented** in the README and the
  getting-started guide. Neither word had appeared anywhere across README,
  README.ko, getting-started, `install.sh` or `RELEASING.md`.
- `aelix --help` now lists `extension verify`. It shipped in
  `aelix extension --help` but not in the top-level `Subcommands:` block, so
  the one command that explains why a manifest did not bind was
  undiscoverable from the main help.
- **`settings.json` accepts a `thinkingBudgets.xhigh` key** alongside the other
  four, matching the `xhigh` budget tier added below. Nothing writes it for
  you, on a fresh install or an existing one, and a file that has only `high`
  still reads and rewrites with only `high`. Like the other four it is **schema
  only today**: nothing in Aelix reads `thinkingBudgets` yet. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).

### Changed

- **Three behaviour changes for packs installed via `aelix.extensions`** (#91).
  All three follow from the manifest being read where it previously was not,
  and each reports what to do:
  - A pack declaring `[[contributes.hooks]]` without
    `capabilities.shell_exec = true` is now **refused**, with none of its code
    executed. Add the capability if the pack genuinely needs to run
    subprocesses.
  - A pack whose only `[activation]` trigger is `on_command` is now
    **deferred**: its `setup()` runs on first use of one of those commands
    instead of at startup. Set `on_startup_finished = true` to keep the old
    behaviour. A warning naming the plugin and the escape hatch is logged when
    this happens.
  - A pack whose `[plugin.api] min_level` exceeds the host's API level is now
    **refused** instead of loading and misbehaving later. Upgrade aelix, or
    install a build of the plugin for this API level.
- A pack that is refused at load time no longer has its declared MCP servers
  started (#91). Previously the load-time refusal and the MCP gate keyed on
  different capability flags, so a denied plugin's `[[contributes.mcp_servers]]`
  were still spawned or dialled at every startup.
- An extension whose `aelix-plugin.toml` fails to parse cannot be installed
  via an entry point and silently do nothing: the pack loads **without** its
  manifest and reports an error naming the absolute path of the file. If the
  distribution ships a manifest the host could not attribute to the entry
  module, the error names that file too.
- Development installs (`pip install -e`) cannot be proved from installed
  metadata, so a pack installed that way loads without its manifest and says
  so on every start. Install the pack normally to exercise its manifest.
- **`install.sh` pins the version it installs** to the one named by the
  checksum-verified `SHA256SUMS` manifest. `--find-links` only *adds*
  candidates and the PyPI index stays enabled, so requesting the bare name
  `aelix` let an index release outrank the local wheels — the checksum gate
  could verify artifacts the next command discarded. It also names
  `GITHUB_TOKEN` and `AELIX_VERSION` when the anonymous GitHub API call fails,
  which on a shared NAT or CI address is usually a 403 rate-limit rather than a
  missing repository.
- **The release tag gate rejects dot-form pre-releases.** `v0.1.0-rc.1` is
  accepted; `v0.1.0.rc1` is refused. The whole pipeline decides "pre-release?"
  by testing the tag for a hyphen, so the dot form would have been treated as
  GA — marked a full GitHub release and carried into an irreversible PyPI
  upload. The workflow now also asserts, before building, that the tag
  normalizes to `pyproject.toml`'s version under PEP 440, and that the SBOM
  glob matches exactly one file.
- **Security and `--offline` claims now match the code.** The README, the
  Korean README and the website described extensions as *being verified* with
  Ed25519 provenance; in fact no first-party keys are provisioned and an
  unsigned pack is accepted unless you install with `--require-signature`.
  `--offline` was called "air-gap mode": it skips the `rg`/`fd` download, the
  extension-catalog fetch and index-less pypi installs, and does **not** affect
  provider or model calls. The `rg`/`fd` auto-download is now disclosed
  explicitly in the README and `SECURITY.md`.
- **Provider documentation is labelled.** Copilot Enterprise is marked
  unverified (live testing covered a paid individual seat and a Business seat
  only). The providers guide gained an adapter-coverage table: the bundled
  catalog spans nine wire protocols and this build ships six, so
  `amazon-bedrock`, `azure-openai-responses` and `mistral` cannot run — they
  are hidden from `--list-models` and `/model` rather than failing at turn one.
  `mistral` had been listed in the guide's primary environment-variable table
  and `azure-openai-responses` among "other supported providers".

### Removed

- **The `[images]` extra and `tui/images.py`.** The extra installed
  `rich-pixels` and the README offered `AELIX_EXTRAS=tui,images` for "inline
  terminal image rendering", but no production code path ever reached the
  renderer: its only importers were two test files. Installing it bought a
  dependency and zero behaviour. Inline images are not a shipped feature; see
  ADR-0223 for what wiring one would take, including the `term-image` /
  `Pillow>=11` conflict that leaves only the Unicode tier reachable.

- `--verbose`, `--no-themes` and `--no-prompt-templates` (and the `-np`
  spelling). All were parsed into `Args` fields that nothing outside
  `cli/args.py` ever read, while `--help` advertised them as working features.
  Passing one is now a hard argument error rather than a silent no-op: an
  unrecognised `--name` otherwise falls into the unknown-extension-flag branch,
  which swallows the following token as the flag's value, so
  `aelix --verbose "my prompt"` would have run with no prompt at all and no
  error. The positive forms `--theme` and `--prompt-template` are unaffected,
  as is `--no-skills`.

- **`aelix_coding_agent.tools._process_tree`.** It was a re-export shim, left
  behind when the containment primitive moved to `aelix_ai.utils._process_tree`
  so that the `aelix-ai` sites could share it (#202). Its last three importers
  — the `bash` tool, the `rg`/`fd` helper and the win32 process-tree test
  (`tests/tools/test_process_tree_win32.py`) — name the primitive directly now
  (#222); the remaining references were prose (`reaper.py`, ADR-0238,
  `SLICE-STATUS.md`) and moved with it. The path is private and undocumented,
  so no supported surface changes; this line exists because it was still
  importable, and an out-of-tree caller that reached for it deserves a written
  signal rather than an `ImportError` to bisect. `kill_process_tree` itself is
  unchanged and is still exported from `aelix_ai.utils._process_tree`.

- **`shell_basename` from `aelix_coding_agent.tools.bash`.** The Windows shell
  chain moved down to `aelix_ai.utils._shell` so a `models.json` / `auth.json`
  `!command` and the bash tool resolve through one primitive (#227), and
  `shell_basename` went with it — along with `_command_flag_for`,
  `_POWERSHELL_NAMES`, `_CMD_NAMES`, `_VERSION_SUFFIX_RE` and the three
  command-flag constants, all of which were private. Its three in-tree callers
  (`dialect_for_shell`, `is_classifiable_shell`, and the PowerShell classifier's
  name normaliser) import the new module directly. It was never in that module's
  `__all__`, so no supported surface changes; this line exists because it was
  still importable under a non-underscore name, and an out-of-tree caller that
  reached for it deserves a written signal rather than an `ImportError` to
  bisect. There is deliberately no re-export shim — the only lint-clean one
  would have to add the names to `__all__`, declaring as public a surface that
  never was; ADR-0238's #227 amendment records the reasoning. `ShellConfig` and
  `command_flag_for` still answer from the old path, but only because `bash.py`
  now imports them for its own use; import them from `aelix_ai.utils._shell`.

### Fixed

- **Thinking works on Claude Opus 5, Opus 4.8, Sonnet 5 and both Fable rows —
  and `claude-fable-5` / `claude-fable-5-1` can be used at all.** Ask any of
  them to think and the request came back `400 invalid_request_error`:
  *"`thinking.type.enabled` is not supported for this model. Use
  `thinking.type.adaptive` and `output_config.effort` to control thinking
  behavior."* Measured against `api.anthropic.com` on 2026-09-09 at `high` and
  at `xhigh` — and since what is rejected is the `thinking.type` value, which
  every budget level sends identically, thinking was unusable on those rows at
  **every** level. The two `fable` rows reject `thinking.type.disabled` too
  (`req_011CerzNQTwZdGcBeTt9o3Ea`), so they answered nothing at all, thinking
  on or off — including `claude-fable-5-1`, a row this release's own catalog
  refresh (#172) added. The defect is older than this release: it reproduces on
  `0985fcf`, where the adaptive-vs-budget split was a literal id whitelist of
  `opus-4-6` / `opus-4-7` / `sonnet-4-6`.

  The split now asks the model row. `compat.forceAdaptiveThinking` — a field
  the catalog already ships, on 12 rows, and that nothing read — decides, in
  both directions, so a catalog refresh can move a row with no code change. A
  family fallback (`claude-(opus|sonnet|fable)-(4.6|4.7|4.8|5)`) covers the
  mirrors upstream has not flagged: without it `github-copilot`,
  `vercel-ai-gateway` and `opencode` would keep sending the rejected request
  for the same models behind a proxy — 25 of the 37 adaptive rows reach the
  branch that way, `github-copilot/claude-opus-4.7` among them. **What was
  measured is six first-party `anthropic` ids** — the five that changed shape
  plus the `claude-opus-4-7` control; the other 17 rows that changed are
  mirrors, they need credentials this repo does not have, and they were equally
  unverified while they were building a request their model rejects.

  **Thinking `off` now means off here.** On any row that accepts it that is
  still `thinking: {"type": "disabled"}` — measured to work on `claude-opus-5`,
  `claude-opus-4-8` and `claude-sonnet-5`. On a row that cannot be turned off
  the honest answer is the least thinking it offers: the `fable` rows get
  `thinking: {"type": "adaptive"}` with `output_config: {"effort": "low"}` and
  no `display`, so the reasoning you asked not to have stays out of the
  transcript. A literal `SimpleStreamOptions(reasoning="off")` is answered the
  same way instead of buying `medium`'s token budget, which is what it did
  before; `--thinking off` and `/thinking off` were never affected, because the
  harness turns them into "no level" before any adapter runs.

  **What you give up:** on those rows `xhigh` is now an *effort* and not a
  token budget, so a `maxTokens` override in `models.json` no longer bounds how
  much they think — the model decides, which is what `adaptive` means. The two
  rows that genuinely cannot be turned off (`claude-fable-5`,
  `claude-fable-5-1` — both answer *"thinking.type.disabled" is not supported
  for this model*) send the lowest adaptive effort for "off" instead, which is
  the least thinking those rows offer. `claude-sonnet-5` is NOT one of them:
  upstream declared `"off": null` for it too, but the endpoint accepts
  `disabled` there (measured), so the catalog row was corrected and "off" on
  that model still means no thinking and no reasoning bill. See
  [#258](https://github.com/handochan/aelix-ai/issues/258) and ADR-0135.
- **The context meter moves during a turn, and after `/model`.** The footer's
  `◔ 42% · 84K/200K` sat on the previous turn's number for a whole
  ten-minute multi-tool turn, and `/model` changed the denominator without
  recomputing anything. The refresh already ran once per provider round-trip —
  but each one estimated over a message list the harness then extended only
  when the turn ended, so they all painted the same pre-turn figure,
  which on the first turn of a fresh session is literally `◔ 0%`. The
  mid-turn number now comes from the assistant message the provider just
  finished — its own reported usage, the same term the turn-end estimate
  anchors on, so the meter no longer jumps at the boundary — and every model
  change refreshes it through the harness's `model_select` hook, which covers
  `/model`, the picker, the post-`/login` pick and an extension's `set_model`
  alike. The meter's own per-round-trip stats read is now **skipped**
  while a live number is held, because it is measurably worse than what is
  already on screen: that removes work rather than adding it. It halves the
  per-round-trip reads rather than eliminating them — the `/stats` history row
  on the same event still takes one (the in-memory half of a read measured
  0.035 ms at 200 messages and 0.349 ms at 2000, before the session-branch disk
  read a persisted session adds on top), and the live paint costs **1.13 µs**.
  **What you give up:** the mid-turn figure counts the last round-trip only, so
  tool output produced *since* that response is not in it until the next one —
  a floor that steps, not a continuously rising bar; the optional
  input/output-token and cost segments (default-OFF) still update at turn end
  only, so mid-turn they sit one turn behind the context% beside them; and after
  a compaction the segment's deliberate blank window now ends at the first
  post-compaction assistant response instead of at the next turn end. See
  [#249](https://github.com/handochan/aelix-ai/issues/249), ADR-0116, ADR-0121
  §4.
- **A resumed session comes back at the thinking level you left it at.** Two
  things were wrong and either alone was enough. `/thinking high` outside a turn
  wrote **nothing** to the session file — the harness queued a
  `thinking_level_change` only while a turn was running, so a session whose level
  came from the picker held zero control entries and `build_context()` reported
  `off`. And when a session *did* carry the entry, neither place that builds a
  harness from a resumed session read it: a freshly built harness measured `off`
  against a session context saying `high`. So `--continue`, `--resume`,
  `--session`, `--fork` and the in-session `/resume` all started from scratch —
  and `/resume` additionally threw away the level set in that very process,
  because Aelix rebuilds the harness on a session swap (Pi does not, which is why
  Pi never needed this). The level is now written when it changes, restored at
  both seams, and clamped to the resumed model: `xhigh` on a `high`-max model
  comes back `high`, not `off`. An explicit `off` stays `off` —
  `defaultThinkingLevel` no longer overwrites it, because the TUI seed is now
  *told* a level was applied instead of guessing from the value. `--thinking`
  (and an agent profile's `thinking:`) still wins over the session at launch, and
  it is now written down too, so `aelix --thinking high` followed by `--continue`
  comes back at `high` instead of `off`; the session wins over
  `defaultThinkingLevel` (the ADR-0196 order). One place that order inverts: an
  in-session `/resume` takes the target session's own level over the launch flag,
  because you asked for that session.
  **What you give up:** the session file grows one control entry per level
  change, one the first time a session with no level of its own is opened while
  `defaultThinkingLevel` is set (after which that session out-ranks a later
  change to the global default), one the first time such a session is launched
  under `--thinking`, and one per `/new` or `/resume` into a session that has no
  level of its own. The model recorded in a session is still **not**
  restored — that is the follow-up. `/reload` is a third rebuild seam and is
  untouched here: it still drops the level. See
  [#198](https://github.com/handochan/aelix-ai/issues/198) and ADR-0239.
- **The `/settings` row for the update check could not be switched off, and the
  one for skill commands claimed a restart it never needed.** Selecting **Check
  for updates** printed `✖ Check for updates: 'check_for_updates'` and changed
  nothing: the row shipped as a toggle whose key was in neither of the two tables
  that dispatch a toggle, so the first keypress raised a `KeyError` the menu
  swallowed into a red line, and the only way to turn the check off was to
  hand-edit `settings.json` — which is not what either README said. It toggles
  now, and no key is needed in `settings.json` for the fix to reach you: a global
  pin is simply overwritten, so this is every install, not only fresh ones. A
  test now asserts that every boolean row's toggle actually **succeeds** — ten of
  the twelve were already driven, but the loop only checked the mirror payload,
  so a swallowed `KeyError` read as a pass. A boolean row also re-reads the value
  it wrote before printing it, so the one case it still cannot change — a project
  `.aelix/settings.json` carrying `checkForUpdates`, which wins over the global
  file this setting is written to — now says so instead of confirming a change
  that did not happen, and the global value it did write is flushed rather than
  left pending behind that message. **Thinking blocks** and **Compaction
  summary** under such a pin keep flipping for the session, as before, and now
  say that is all they do. Going the other way, **Skill commands** promised
  "takes effect after you restart aelix" while the gate is re-read on every line
  you type; it is now marked live and says so. **What you give up:** nothing at
  runtime — the `/skill:` gate covers the TUI surface, not the command list an
  RPC client is offered. See
  [#244](https://github.com/handochan/aelix-ai/issues/244) and ADR-0229.
- **Windows tool output is readable again, and the model is told which shell it
  is on.** Every child process this agent decodes itself — the bash tool, `!`
  commands, hooks, `rg`/`fd`, subagent stderr — was decoded as UTF-8 with
  replacement, so on a Korean Windows box, where PowerShell writes CP949,
  `위치 줄:1` arrived as `��ġ ��:1` — arithmetic, not a guess, and not even
  recognisable as corruption, because `c4 a1` is a *valid* UTF-8 sequence.
  Output is now decoded **run by run**: each stretch of non-ASCII bytes takes
  UTF-8 strict first, and only a run UTF-8 rejected is offered to the console
  output code page. So a UTF-8 run standing next to a legacy-code-page run in
  the same buffer decodes correctly on both sides — measured, `한글` beside a
  CP949 `오류` comes back as `한글 오류`, where flipping the whole buffer to
  CP949 would have returned `�븳湲� 오류` and destroyed the half that was
  already right. With **no** separator between them the two halves are one run,
  and there the promise is narrower than it sounds: the split saves the UTF-8
  half only when the code page rejects the run as a whole. `한글` glued to a
  CP949 `오류` still comes back `한글오류`, but `문자` glued to the same `오류`
  reads `臾몄옄오류` — CP949 accepts all ten bytes, so it takes them, and the
  half `U+FFFD` used to get right is now confidently wrong. Measured over 4000
  glued pairs per shape, that costs the UTF-8 half 13.3% of the time for one
  character on each side and 38.7% for two. It is left as it is on purpose:
  telling that buffer apart from `치위` — a real CP949 word whose first two
  bytes are also valid UTF-8, and the shape this issue was reported from —
  cannot be done from the bytes. Two
  details are what make the promise true rather than nearly true. CP932/936/949
  /950 use ASCII bytes as DBCS *trail* bytes (measured: 3288 of CP932's 9604
  lead/trail pairs), so a failed run is offered the ASCII that follows it as
  well — without that, Japanese `エラー` came back exactly as mojibake as
  before. And a code page that decodes all 256 single bytes (CP437/850/866 —
  every Western box) is offered nothing at all, because its accepting bytes is
  no evidence about them: not a whole run, and not the stretch inside a run
  that UTF-8 could not begin at. That second half is what the Windows CI leg
  taught us — `b"ok \xff\n"` came back as `ok \xa0`, because CP437 maps `0xFF`
  to a NO-BREAK SPACE, so the marker that says "these bytes were lost" was not
  wrong but *invisible*. The rule as it now stands means something simple and
  checkable: **on a Western Windows box this release changes nothing.** With no
  DBCS page in the chain the decoder is byte-for-byte the `errors="replace"`
  call it replaced — measured over 351 exhaustive windows and 20000 random
  buffers, against six single-byte chains and every combination of the cut-end
  claims: 488 424 decodes, none of which differs.
  And where the buffer's own END is a cut rather than something the child
  chose — a timed-out or aborted command, a stderr ring that scrolled, a child
  that died mid-line — the severed character stays a visible `U+FFFD` instead
  of being spelled by the code page: measured over 24 console lines cut at all
  297 offsets inside a character, 19 read as a confident wrong character on
  CP949 before that rule and none after. A buffer is only ever repaired at an
  end its reader really can cut, which is why a bash result claims its tail and
  not its head.
  A PowerShell that Aelix spawns itself is additionally asked for UTF-8
  (`[Console]::OutputEncoding`), but that preamble runs only *after* the shell
  has parsed your command — measured on pwsh 7.6.5, a parse error discards it
  unexecuted — so the decoder is what covers children that are not ours **and
  our own shell's parse errors, which is the failure this issue reported**.
  `cmd` is asked for nothing at all, and that is a correction made after the
  Windows CI leg ran: an earlier build of this release prepended
  `chcp 65001 >nul&` there, and on windows-latest (run 34272507388, py3.11 and
  py3.12) that prefix stopped an **unquoted space-containing executable path
  with no arguments** from running — `cmd` answered `'C:\…\a' is not
  recognized as an internal or external command`. `cmd /c` keeps the quotes
  `subprocess` puts around such a command only while the whole text between
  them is the name of an executable file, so any prefix loses them, and there
  is no spelling of a preamble that is not a prefix. The decoder covers those
  children instead. On macOS and Linux the decoded bytes are
  unchanged, provably: with no fallback codec the new path is the old call, and
  the test pins that through the platform default rather than by passing an
  empty chain. Separately, the bash tool now tells the model which shell
  actually resolved here, so it stops sending `a && b` into a Windows PowerShell
  parser; the sentence advises `;`, which is correct on 5.1, 6 and 7.
  **What you give up:** on a **CJK** Windows box a child writing genuinely
  binary bytes may decode as legacy-code-page text instead of `U+FFFD`, where
  those bytes happen to be a valid DBCS sequence (a buffer holding a NUL byte is
  exempt — that is a binary file, and it takes the old replacement path whole).
  On a **Western** box nothing decodes as legacy text, and that is a capability
  withheld rather than a regression: a German or French console app writing its
  OEM page reads as `U+FFFD`, exactly as it did under `0.1.0-beta.1`, whose
  `errors="replace"` marked the same characters — measured over 16 accented
  console lines, 16 of 16 marked now, 16 of 16 marked in beta.1, and the two
  decodes equal on all 16. What is given up is what an earlier cut of *this*
  release briefly did with those lines: offer them the OEM page, which read all
  16 correctly. It is withheld because the same 16 lines came back *confidently
  wrong* 16 times out of 16 when the child wrote the ANSI page instead
  (measured: cp1252 bytes read through cp850) — and on a Western box ANSI and
  OEM always differ (1252 against 850 or 437), where in the CJK locales this
  was reported from they are the same number. The single-byte answer is a coin flip the bytes cannot call, and it
  is the same guess that spelled a binary `0xFF` as an invisible character.
  Where a buffer's own end is a cut, the **head** claim cannot tell a severed
  UTF-8 tail from a whole legacy character whose two bytes both lie in
  `0x80-0xBF` — 31.4% of CP949-encodable Hangul, `가` among them — so the two
  readers that claim a cut head (subagent stderr, the RPC stderr window) lose
  such a character to `U+FFFD`, where this same release's *unclaimed* sites keep
  it. Not a loss against the previous release: both of those readers were
  `.decode("utf-8", errors="replace")` before this change and answered `U+FFFD
  U+FFFD` for CP949 `가` too (measured). Accepted because the
  same claim removes a confidently wrong head from 120 of 192 in-character head
  cuts on the same Korean corpus, and because only those two readers make it. A
  CJK run that happens to
  *open* with a valid UTF-8 pair is still read as CJK, which is right on the box
  this was reported from and wrong for a genuine one-character UTF-8 prefix; and
  the first command Aelix runs under PowerShell — the shell the Windows chain
  resolves first — switches that console to 65001 for the session
  (`SetConsoleOutputCP`, via `[Console]::OutputEncoding`); under `cmd` nothing
  does, so a `cmd` child's output stays on the console page and takes the
  decoder's route. **Against `0.1.0-beta.1` that is no change**, which is the
  same promise as above: beta.1 decoded those bytes `utf-8`/`errors="replace"`,
  so an OEM-page character read `U+FFFD` there too (measured: a cp850 `Grüße`
  comes back identically through both paths, as do 16 of 16 accented
  German/French console lines). The price the correction above books is
  therefore against an **intermediate build of this release** and not against
  anything published — while the `cmd` preamble existed those characters came
  back as UTF-8, and now they do not. Paid because one mojibake line is worth
  less than a command that does not run. MCP stdio servers are deliberately
  untouched: the `mcp` SDK decodes those, not this repo. `!command` credential
  resolution keeps its argv byte for byte — it gets the decoder and no preamble,
  because its stdout *is* the key. See
  [#239](https://github.com/handochan/aelix-ai/issues/239) and ADR-0238.
- **`xhigh` builds a bigger thinking budget than `high` — on the ten rows that
  can still receive one.** On models that think with a Claude-style token
  budget the two levels built the same request: measured on
  `anthropic/claude-opus-5`, both sent `thinking.budget_tokens: 16384` byte for
  byte while the optional 🧠 thinking-level statusline segment said `xhigh`.
  `xhigh` is now a budget tier of its own at **32768**, twice `high`.

  **What that changes on the wire, enumerated from the catalog this release
  ships: 11 rows.** Thirteen rows offer `xhigh` and take the budget path; on 11
  of them (output cap 128000, all `vercel-ai-gateway` rows serving OpenAI ids
  over the Anthropic Messages API) the request goes from `budget_tokens: 16384`
  to `32768`. The other two **do not change**: `openai/gpt-5.2-chat` and
  `openai/gpt-5.3-chat` cap output at 16384, where the rule that leaves 1024
  tokens for the visible answer shrinks either tier to 15360. Counted against
  the catalog AFTER the same release's refresh (#172) **and** after the same
  release's #258, which is what ships. Before #258 the count was 21 of 23,
  because ten Claude rows — five `anthropic` ids and five `github-copilot`
  mirrors — took the budget path too; they now think adaptively, where a
  budget is not what steers the model.

  **The ten Claude rows that left this tier were never receiving it.** The
  request Aelix built for them was sent verbatim to `api.anthropic.com` on
  2026-09-09, at `high` and at `xhigh`, and all eight first-party attempts
  returned `400 invalid_request_error`: *"`thinking.type.enabled` is not
  supported for this model. Use `thinking.type.adaptive` and
  `output_config.effort` to control thinking behavior."* — `claude-opus-5`
  (`req_011CerQpNc69m6CAsK93KroH`, `req_011CerQpLuQC5P5Xaw7MzyVQ`),
  `claude-fable-5` (`req_011CerQpRVUJivivpJ9SmDHp`,
  `req_011CerQpQ3uQqfCeUsKaPg9M`), `claude-opus-4-8`
  (`req_011CerQpUWYMoCu4L9t8WS7w`, `req_011CerQpSw3MHtbeDFKJYEpi`) and
  `claude-sonnet-5` (`req_011CerQpXRAezGeumCSuhkSk`,
  `req_011CerQpVws2QN9E2uMrD6yC`). What is rejected is the `thinking.type`
  value, which is the same on every budget-path level, so **thinking never
  worked on those rows at any level** — not just `xhigh`. The same 400 lands on
  0985fcf, so this tier neither introduced the failure nor is what repairs it:
  [#258](https://github.com/handochan/aelix-ai/issues/258), in this same
  release, moves them onto the adaptive request they accept — see its own entry
  below. `claude-opus-4-7`, which was already adaptive, answered normally at
  both levels in the same run (`req_011CerQpYrVRK4GK5pf5sJ8x`,
  `req_011CerQpdX2jsWYUfDcBa1x6`). The `github-copilot` mirrors are the same
  models behind a proxy and were **not** measured.

  So the tier's larger budget is what actually ships on the **eleven**
  `vercel-ai-gateway` `openai/*` rows served over the Anthropic Messages API.
  No request was made against that gateway; there, what is measured is the
  request Aelix builds. The adaptive rows were never affected. On a call that carries its own
  `max_tokens` the request's `max_tokens` rises with the budget — with a 32000-token base on `claude-opus-5`, 48384 before and
  **64768** now. What you pass there caps the *visible answer*, not the
  payload: the thinking budget is added on top and the sum clamped to the
  model's own cap, so `max_tokens=16384` at `xhigh` sends `max_tokens: 49152`
  with `budget_tokens: 32768` and still returns you at most 16384 tokens of
  answer. That is unchanged behaviour, now written down on the field itself; no
  in-tree caller supplies both a `max_tokens` and a thinking level today, so
  this is for embedders.
  Asking for `xhigh` on one of the **252 rows that do not offer it** —
  `aelix --thinking xhigh --model anthropic/claude-opus-4-1`, or an agent
  profile's `thinking:` — still sends `high`'s 16384: the Anthropic adapter now
  clamps the level against the model row the way the Google and OpenAI adapters
  already did.
  That clamp has one other **embedder-visible** effect, on a path no CLI flag
  reaches: a literal `SimpleStreamOptions(reasoning="off")` is clamped to
  `"minimal"` on the three catalog rows that declare `"off": null`
  (`anthropic/claude-fable-5`, `anthropic/claude-fable-5-1`,
  `anthropic/claude-sonnet-5`). On this release that no longer buys thinking:
  #258, below, answers the string `"off"` where it arrives, so those rows send
  their lowest adaptive effort and every other row sends
  `thinking: {"type": "disabled"}` — against `budget_tokens: 8192` on 0985fcf,
  where `resolve_anthropic_thinking` gated on `if not reasoning` and `"off"` is
  truthy. The harness collapses `off` to `None` long before any adapter sees
  it, so `--thinking off` and `/thinking off` were never affected either way;
  only a direct caller of the provider API can reach this. Both Google adapters
  still map `"off"` to `"high"`, which is worse, so the cross-adapter contract
  stays open as [#259](https://github.com/handochan/aelix-ai/issues/259).
  **What you give up:** a top tier that is slower and costs more than it did
  when it was `high` under another name. And if you override `maxTokens` or
  `contextWindow` in `models.json` so an *xhigh-offering* row's effective
  output cap lands between 16385 and 33792, `xhigh` leaves the answer exactly
  1024 tokens and no more. No shipped row that offers `xhigh` is in that range
  (their caps are 16384, 64000 and 128000). What can **no longer** happen is
  the inversion: a higher level never sends a smaller budget than a lower one
  at any cap. See ADR-0135 and
  [#250](https://github.com/handochan/aelix-ai/issues/250).

- **A very long prompt no longer eats the answer on a self-inconsistent
  catalog row.** On the 46 reasoning rows whose `maxTokens` is at least their
  `contextWindow` — the `fireworks` deepseek/glm ids among them — the output
  cap is computed from what the window has left, so it moves with your prompt.
  Land it just above a thinking tier's budget and the rule that reserves 1024
  tokens for the visible answer did not fire: in the request Aelix builds for
  `accounts/fireworks/models/deepseek-v3p1`, a 583217-character prompt at
  `--thinking high` left the answer **626** tokens. The budget is now capped by
  the room the model's output cap leaves, so the answer keeps its 1024 whatever
  the prompt. No `models.json` override needed to hit the old behaviour, which
  is why this is listed on its own. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).

- **A tiny `maxTokens` override turns thinking off instead of building a
  request Anthropic rejects.** Override a Claude-style row to
  `"maxTokens": 1024` and Aelix sent `budget_tokens: 1024` alongside
  `max_tokens: 1024`; at 512 the budget was larger than the whole request.
  The API wants a thinking budget of at least 1024 that is strictly smaller
  than `max_tokens`, so both were 400s waiting to happen — reachable only
  through `models.json`, which checks that your number is positive and nothing
  else. Below **2048** there is no budget that is both valid and leaves the
  answer its 1024 tokens, so the request now goes out with thinking disabled
  and you get an answer. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).

- **A `thinkingLevelMap` override on a Gemini 2.x model no longer crashes the
  stream.** `"thinkingLevelMap": {"xhigh": "xhigh"}` on `gemini-2.5-pro` in
  `~/.aelix/agent/models.json` raised an unhandled `KeyError: 'xhigh'` out of
  the stream factory on both the Generative AI and the Vertex adapter — and
  because it escaped synchronously it never became an error event you could
  read. An effort the budget table does not know now sends `thinkingBudget:
  -1`, the API's *dynamic* budget — thinking stays on and Gemini decides how
  much of it to do. In practice that effort is always `xhigh`: the level is
  already clamped against the model row, so the four table keys cover every
  other spelling. Answering it with the family's `high` row was the first
  revision and was reverted, because for Gemini 2.5 that row already *is* the
  API's ceiling (32768 pro, 24576 flash) — reporting a request Aelix cannot
  honour by spending the most expensive one the family can make. For the same
  ceiling reason Gemini gets **no separate `xhigh` tier**: there is no room
  above `high`. On the Gemini 3 / Gemma 4 `thinkingLevel` rows the same `xhigh`
  instead resolves to `HIGH`, the top of that scale, because `thinkingLevel`
  has no dynamic value to hand back — same rule, different alphabet. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).
- **A program sitting in the folder Aelix is running in can no longer become the
  shell.** The shell chain — what runs a `models.json` / `auth.json` `!command`,
  what the bash tool runs your commands under, and which grammar the AUTO
  permission gate reads them with — resolved its candidates with `shutil.which`,
  and on Windows that searches the current directory **before** `PATH`: read
  from CPython's own source, 3.11 unconditionally and 3.12-3.14 unless a Windows
  environment variable says otherwise, both *even when the search path is passed
  explicitly* — and then measured on CI's `windows-latest` runner under both
  interpreters, where stock `which` handed back the copy planted in the current
  directory. Cloning a repository and starting Aelix inside it was enough for a
  `pwsh.exe` there to be handed your `op read`. The same hole was open on macOS
  and Linux by another route — a `!command` was run by a bare `sh`, and an empty
  or `.` entry in `PATH` means the current directory; measured on darwin, a
  planted `sh` ran instead of `/bin/sh`.

  Aelix now walks `PATH` itself, skipping any entry that is not absolute, applies
  `PATHEXT` the way Windows does, resolves the POSIX `sh` and the bash tool's
  `bash` to absolute paths as well, and puts an existing
  `%SystemRoot%\System32\cmd.exe` ahead of the bare name `cmd.exe`, which
  Windows looks for in the application's own directory and then, unless
  `NoDefaultCurrentDirectoryInExePath` is set, in the current directory — both
  *before* the system directories and `PATH`. That step goes in
  **unconditionally**, so on a stock box, where `%COMSPEC%` already is that same
  path, the chain names one program twice: gating it on `%COMSPEC%` being unset,
  empty or relative would make the floor's presence depend on the very variable
  it exists to survive, and the duplicate costs at most one repeated spawn
  attempt, on the caller that falls through, after an attempt on an
  existence-checked path that already worked. When `%SystemRoot%` is unset,
  empty, or set to anything that is not an absolute path, the step falls back to
  the stock `C:\Windows` rather than to the bare name — a fixed system path,
  not one your working directory can influence, which is exactly what a relative
  `%SystemRoot%` resolves against, and the same default Aelix already uses to
  find `taskkill.exe`. CPython hardened its own `shell=True` spawn the same way
  (gh-101283); Aelix goes further and **drops** a
  relative `%COMSPEC%` instead of passing it on, because CPython's protection for
  that case is `executable=`, which a site that names a shell rather than
  spawning one cannot use.

  **What you give up:** a shell reached through a relative `PATH` entry (`.`,
  `bin`, `node_modules/.bin`) is no longer found — name the directory absolutely.
  On Windows that includes a rooted-but-drive-less entry such as `\tools`, which
  resolves against whatever drive you happen to be on rather than against a
  named one.
  A `$SHELL` you exported yourself is still taken verbatim: it is you naming a
  shell, not Aelix guessing one. Windows behaviour is **reasoned from CPython and
  Microsoft's documented search order and proven only by CI's `windows-latest`
  leg**; nobody has run it on a Windows desktop. See
  [#241](https://github.com/handochan/aelix-ai/issues/241) and ADR-0238.

- **Pressing Esc no longer kills a helper an extension's command left running.**
  `aelix.exec(...)` runs a command's tree contained, and after the command exits
  it keeps reading the pipes for a moment so a process the command backgrounded
  can finish writing. A cancellation that landed in that moment — Esc, ^C, or
  any cancelled turn — still ran the kill ladder, and on Linux and macOS that
  was a group kill aimed at a process this run had already reaped: it killed the
  backgrounded helper in every one of **eight** measured runs (six on macOS, two
  on Linux), while the command itself was reported as the success it was. The
  window was 0.1 s for a quiet helper and up to 2 s for one that kept writing.
  Esc there now ends **that reading** and nothing else: the helper survives,
  exactly as it does when you do not press Esc. One race is left, narrowed
  rather than closed — a cancellation that arrives between the command's own
  exit and this run noticing it can still reach the helper. That gap is under
  a millisecond ordinarily, but up to ~54 ms when the call was given a
  `timeout_ms`, because the wait polls at a 0.05 s cap: measured through
  `aelix.exec` with `timeout_ms`, an Esc 2 ms after the exit still killed the
  helper 6 times in 6, 1 in 6 at 20 ms and at 50 ms, and none at 80 ms.
  ADR-0238 records it as narrowed, not closed. The cancellation itself is
  unchanged — it is still what you get back, and the command's output is still
  discarded with it. See ADR-0238 and
  [#230](https://github.com/handochan/aelix-ai/issues/230).

- **A `models.json` / `auth.json` `!command` is no longer assumed to run under
  `sh` on Windows.** It ran `sh -c` on every platform, so on a stock Windows
  install the spawn failed and the value resolved to nothing — which you saw as
  `Failed to resolve … from shell command:`, blaming your command for the
  missing shell. Aelix now runs the shell it can find there, in the order
  `$SHELL` → `sh` → `pwsh` → `powershell` → `%COMSPEC%` → `cmd.exe`, so a box
  with an `sh` on `PATH` and no `SHELL` set keeps running `!command`s under it —
  and **write the command for the shell that will run it**: one that only
  invokes a program (`!op read op://vault/key`) is portable, one that uses shell
  syntax is not. PowerShell runs with `-NoProfile`, because a profile that
  prints a banner would otherwise be prepended to your API key (measured on
  PowerShell 7), and with `-NonInteractive`, so a prompt PowerShell itself
  raises — `Read-Host`, `Get-Credential` — is refused outright: its text can no
  longer end up inside your key, and a masked prompt can no longer sit waiting
  on a console you cannot see (the text half measured on PowerShell 7, the
  masked half read from its sources; Windows PowerShell 5.1, which is what a
  stock box actually resolves, is unmeasured). A helper that opens the console
  itself — `git`, `ssh`, `gpg` — is still not covered. The shell Aelix starts
  there is given no console window of its own. See ADR-0238 and
  [#227](https://github.com/handochan/aelix-ai/issues/227).

- **`auth.json`'s cached `!command` values are trimmed the way
  `resolve_config_value_uncached` always did** (`.strip()`), on every platform: surrounding
- **A spawn that fails before your command starts is now reported as a failed
  command, not as a broken tool.** The bash tool caught exactly two of the ways
  `Popen` fails — the shell is missing, or a path component is not a directory.
  A `$SHELL` (or a `shell_path` setting) naming a file that *exists* but is not
  a runnable program took neither branch and escaped, and so did a working
  directory you cannot enter. Instead of the `[bash] failed to spawn` line and
  exit 127 the tool already had, a `!command` typed into the **TUI** took the
  whole session down with it — that loop catches only end-of-input — the CLI
  REPL had nothing on the path either, and a tool call got `Tool 'bash' raised:
  …` with no exit code to reason about. Measured on macOS through the tool: a
  shell with the exec bit and no valid image escaped as `[Errno 8] Exec format
  error`, one with the bit cleared as `[Errno 13] Permission denied`, and an
  unreadable `cwd` as `[Errno 13]` — while a *missing* `cwd` already returned
  127, so the two now agree. On Windows this is the common case rather than the
  exotic one: a non-PE file on `%COMSPEC%` or `$SHELL` arrives as `[WinError
  193] %1 is not a valid Win32 application`, which CPython maps to an errno
  with no exception class of its own. All seven errnos now report exit 127,
  through the same allowlist a `models.json` / `auth.json` `!command` has used
  since #227.
  **What you give up:** a spawn failure that is *not* about the shell or the
  directory — the host is out of descriptors or memory, or the error carries no
  errno at all — still raises rather than claiming exit 127, because "127"
  reads to a model as *command not found* and it would keep retrying against a
  machine that cannot spawn anything. One seam: on Windows, CPython folds every
  error code its table does not name into `EINVAL`, which *is* in the
  allowlist, so a rarity like "this program needs elevation" is reported as
  exit 127 there. See ADR-0238 and
  [#243](https://github.com/handochan/aelix-ai/issues/243).

- **`auth.json`'s cached `!command` values are trimmed the way `models.json`'s
  uncached ones always were** (`.strip()`), on every platform: surrounding
  whitespace, a leading newline and a trailing `\r` or tab no longer survive
  into the key. Before this, a command that ended in CRLF left a bare carriage
  return inside an `Authorization` header.

- **`BashOperations.exec` no longer swallows a cancellation of its own task.**
  While tidying up the abort watcher after a command had finished — or after the
  tool had just timed it out and killed it — `exec` awaited the watcher inside a
  `suppress(CancelledError, Exception)`, so a cancellation of the task running
  `exec` that landed in that window was thrown away and `exec` returned a normal
  result. It waits for the watcher now instead of awaiting it, so only the
  caller's cancellation comes out, and a cancellation now wins over a timeout
  report. Only a caller that supplies an abort signal reaches that window — in
  this build the RPC `bash` command and embedders, not a model-issued bash call
  or the TUI's `!command`, both of which pass `signal=None`. Measured against a
  command that takes about 4 ms: roughly 2 % of the aborts that landed while the
  command was still running were lost (48 of 1967 across ten runs) and a cancel
  aimed at the window was lost every time; after, no aimed cancel and none of
  380 randomly-timed ones was lost. A command that exited successfully after
  backgrounding a helper still keeps that helper. Contributors only; see
  ADR-0238 and [#234](https://github.com/handochan/aelix-ai/issues/234).

- **A plain `uv sync` now produces an environment the whole test suite can run
  in.** `aelix-server` is a workspace member that nothing depends on — not the
  root project, not any other package — so `uv sync` resolved without it and
  `tests/server` and
  `tests/cli/test_stdio_encoding_win32.py::test_server_main_sync_hardens_before_boot`
  failed on the import in any fresh checkout or worktree, with nothing wrong in
  the product code. It is a dev-group dependency now, so `--all-packages` is no
  longer the difference between a green suite and a red one. Contributors only;
  dependency groups are not build input, and the published `aelix` wheel's
  `Requires-Dist` is unchanged (measured on the built wheel, and
  `tests/packaging_gate` covers it). See
  [#224](https://github.com/handochan/aelix-ai/issues/224).

- **A timed-out hook or an aborted delegation no longer leaves its command
  running.** Both teardown ladders ended the process they had spawned, and that
  process is routinely the least interesting thing in the tree: a `sh` holding a
  pipeline, a `cmd.exe` waiting on the command it launched, an RPC child whose
  grandchildren are the actual work. On POSIX it leaked whenever the shell
  forked — `sh -c "sleep 6 | cat"` left `sh`, `sleep` and `cat` behind, while
  `sh -c "sleep 5"` was fine because `sh` execs and there is nothing to leave.
  On Windows it leaked every time at all three sites, because
  `start_new_session=True` is accepted and silently ignored there (CPython names
  the parameter `unused_start_new_session`). The delegation case was measured on
  macOS, not Windows: aborting a turn ran the whole
  `terminate()` → 1s → `kill()` → 5s ladder and the child's grandchild was still
  running when it finished. The RPC delegation channel, subprocess hooks and
  `models.json`'s `!command` now put their child in a process group on POSIX and
  a Job Object on Windows, and end the tree instead of the root. A hook that
  deliberately backgrounds a helper and returns 0 still keeps it — only the
  timeout and cancellation paths kill.

  `!command` also keeps the terminal as its *controlling* terminal — which turns
  out to make a helper's tty read a **detectable stop**, not a working prompt;
  see the `!command` entry under Changed. The new sites ask for a process group
  inside the same session rather than a new session, and `!command` is the site
  where `gpg` / `pass` and pinentry run.

  On Windows the delegated child can now be asked to stop at all: it is spawned
  in its own console process group, `stop()` sends `CTRL_BREAK_EVENT`, and the
  child answers it the way it answers SIGTERM elsewhere. Letting it exit on its
  own also surfaced a crash nobody could reach before — the child's stdin
  reader held a lock the interpreter needs at shutdown — which is fixed in the
  same change. See ADR-0238 and
  [#202](https://github.com/handochan/aelix-ai/issues/202) — and the print
  channel and the reaper are converted too
  ([#220](https://github.com/handochan/aelix-ai/issues/220)): the delegation
  spawn puts its child in a job object of its own, the reaper asks that child to
  stop with `CTRL_BREAK_EVENT` when there is a grace to wait out and ends the
  job when there is not, and the print child grew the `SIGBREAK` handler that
  gives the asking somewhere to land. One
  consequence of that on Windows, deliberate and stated rather than discovered:
  a **successful** delegation now also ends whatever the child left running
  behind it, because finishing with the child is what closes its job — the same
  behaviour the RPC child has had since #202, and on POSIX still nothing at all.
  The print child's exit code on a signal is fixed in the same change: it was 1
  with two tracebacks, because the handler called `sys.exit` from inside an
  asyncio task and the loop's own teardown replaced that `SystemExit` with a
  `RuntimeError`; it now returns 143 on POSIX — measured against a real child —
  and, on Windows, the 149 that `128 + SIGBREAK` implies, which the
  `windows-latest` leg is what actually measures. That number is also the only
  thing that tells a cooperative exit apart from a hard job kill — both used to
  be 1.

- **A command Aelix runs on a timeout of its own now takes its whole tree with
  it — and a command that succeeded is reported as one.** Three places run a
  bounded command: an extension's `aelix.exec(...)`, the `git clone` behind
  `aelix extension discover --refresh`, and the `fd` scan behind `@`-mention
  completion. All three were `subprocess.run(..., timeout=)`, whose kill on a
  timeout reaches the root process and nothing else. Measured on macOS: every
  one of the three left behind a grandchild that had inherited the command's
  output pipe — and at the `exec` and `git clone` sites that survivor was
  measured re-parented to `launchd` and still sitting in Aelix's own process
  group, including through a real model at the `exec` surface. On Windows the
  same timeout did not merely leak: CPython follows its kill there with a
  `communicate()` that has no bound at all, and that call cannot return while
  any descendant still holds the pipe, so the worker was wedged for as long as
  the leftover lived. And a command that exited **0** straight away after
  backgrounding a helper was reported as a timeout once its entire deadline had
  passed — the wait was for the pipe to close and the helper was still holding
  it — so a successful `sh -c "helper & echo done"` came back to the extension
  as `code=124 killed=True` with `done` already in its output. All three sites
  now run through one contained runner: the timeout bounds the command itself,
  the kill ends the process group (POSIX) or the job object (Windows) it was
  given, and after the command exits its output is drained until the pipes fall
  idle rather than until they close. `@`-mention completion also stops crashing
  on a filename that is not valid UTF-8 — `fd`'s output is decoded with
  replacement instead of strictly under the locale codec, where a single
  undecodable byte raised out of the completer. See
  [#221](https://github.com/handochan/aelix-ai/issues/221) and ADR-0238.

- **A `bash` tool call that runs past its timeout — or that you stop with Esc —
  now takes its whole tree with it, and comes back at once instead of waiting
  out whatever the command left behind.** The tool spawned its command into a
  session of its own on macOS and Linux and a plain process group on Windows,
  and then killed by pid: `killpg` here, `taskkill /T /F` there. On Windows that
  is the leak Pi measured before we did (#9129). `taskkill /T` follows *live*
  parent links only, and MSYS `bash` runs each stage of a pipeline through a
  short-lived subshell, so by the time the kill lands the leaves have a dead
  parent — `taskkill` ends the layers it can still see, exits 0, and
  `find`/`xargs`/`head` keep running. At this site that is worse than a leak:
  the tool read the command's output until the pipe closed, and the survivors
  were holding the pipe, so **the tool call itself did not return**, past its own
  timeout. The command goes into a job object there now, which holds every
  descendant regardless of whether its parent is still alive, and the `rg`/`fd`
  helper behind the `grep` and `find` tools took the same change. As with the
  rest of this work, the Windows half is reasoned from CPython's and Pi's source
  and measured only by the suite — `windows-latest` is the one leg where a job
  object or `taskkill.exe` actually runs.

  On macOS and Linux the group kill already reached a pipeline, and what you get
  instead is **the wait**. A descendant that made a session of its own is
  outside the group by definition and survives the kill — that is what a process
  group is — and the tool was then waiting for *it* to close the command's
  output pipe. Measured: a command whose helper called `setsid` and slept 8 s
  returned at **8.02 s** under a 1.0 s timeout, and at 8.02 s and 8.03 s when it
  was stopped with Esc or by a cancelled turn. After a kill the output is now
  drained only until it falls idle for 0.1 s, and never for longer than 1 s from
  the kill — the same rule and the same constants the contained runner from #221
  uses — so stopping a stuck command costs about 0.1 s rather than the life of
  whatever it left behind. The trailing output of a descendant still writing
  after the idle window is cut: the command was killed, and its bytes are a
  courtesy. A command that exits **successfully** after backgrounding a helper
  was left alone here, because changing it changes what a model sees; it is
  decided now, under **Changed** above — see
  [#232](https://github.com/handochan/aelix-ai/issues/232).

  **The command's stdin is `/dev/null` now, not the terminal Aelix was started
  from** — the contract `aelix.exec(...)` took in #221, and what Pi does at this
  site. The child already had no controlling terminal, so it never stopped for
  input the way a background job does; it *competed* with the TUI for your
  keystrokes instead (measured: the child received what you typed and Aelix's
  own reader got nothing) and it could turn your terminal's echo off and leave
  it off after exiting. `!cat` typed into the TUI never returned, and ate the
  next key on its way. It now reads nothing and exits 0, and `git commit` with
  no `-m` fails with "Aborting commit due to empty commit message." instead of
  opening an editor you cannot see.
  **Both of those are macOS and Linux sentences.** On Windows there is no
  session to take away — the containment there is a new process group plus a job
  object — so the command keeps the console Aelix was started from: a program
  that reads its stdin gets EOF from `NUL`, but one that opens `CONIN$` directly
  (git's credential prompts) or opens the console for a masked prompt
  (`Read-Host -AsSecureString`, `Get-Credential`) still prompts on that console,
  and an unanswered prompt still costs the command's whole timeout — when there
  is one, since a configured timeout of `0` means unbounded. That half needs a
  human at a Windows console and is **unverified**, not fixed. See ADR-0238 and
  [#222](https://github.com/handochan/aelix-ai/issues/222).

- **`models.json`'s own documentation no longer breaks the file it describes.**
  Two of the guide's three examples carried a `cost` with only `input` and
  `output`, while the validator requires all four keys — and a schema error
  makes Aelix discard the **whole** `models.json`, not just that model. The
  guide ships inside the wheel, so the broken example was being distributed.
  The prose was also backwards (it said `cost` was required; in fact omitting
  it succeeds and a *partial* `cost` fails), and the page now states what you
  silently get for every field you leave out — a minimal entry for a flagship
  model yields an 8x-too-small context window, reasoning off, and no image
  support, with no warning.

- **`aelix -p "..."` no longer stalls ~30s on an inherited but idle stdin
  pipe.** Any piped stdin promotes the run to print mode, and the print path
  then waited for a first byte before continuing — even when the prompt was
  already on argv. A process spawned with `stdin=PIPE`, or run under a CI
  harness, paid the full deadline for input it would never use (measured 35.2s,
  now 6.5s). The wait is time-boxed rather than skipped: `build_initial_message`
  concatenates stdin with the argv prompt, so `cat notes.txt | aelix -p
  "summarise this"` still picks up both, and a producer that takes a couple of
  seconds to get going (`curl … | aelix -p …`, `ssh host cmd | aelix -p …`)
  still lands inside the 5s grace window. Expiry always prints a note naming
  `AELIX_STDIN_TIMEOUT`, because from inside the process an idle pipe is
  indistinguishable from a producer that was about to write — so input may be
  dropped, but never silently. With nothing on argv the behaviour is unchanged,
  since there stdin *is* the prompt.
- **`aelix extension install` and `update` no longer report success for a pack
  this build already knows cannot load** (#154). The host decides — offline, and
  without importing a line of the pack — whether an installed extension's
  `aelix-plugin.toml` will bind; `verify` and `list` read that verdict, but
  `install` never asked for it and printed an unconditional "Installed. Restart
  aelix" even for a pack whose manifest demands a newer API level than this
  build provides. `install`, `discover install` and `update` now report the
  verdict for the distribution they just wrote — and only that one, so a
  pre-existing broken endpoint elsewhere in the environment is not blamed on the
  new pack — in the same wording `verify` uses. A pack that binds prints exactly
  what it printed before — including on a reinstall, on an update, and when the
  target is a symlink to the pack directory. A bare `aelix extension update`
  closes with a one-line summary naming the packs that will not bind, so the
  outcome does not scroll away behind the per-pack reports; across many packs the
  exit code is the hardest outcome, not the first (`1` and `2` outrank `3`, since
  `3` is the one code that asserts the installer succeeded). When the command
  *cannot* tie the
  install to an installed extension distribution — a repeat `git+URL` install, a
  repeat install of a source tree whose name cannot be read without executing it
  (setup.py-only, a `[tool.poetry]` name, a `dynamic` name), or a target that is
  not an extension at all — it now says it has **no verdict** and names `verify`,
  rather than printing the success line for a distribution it never identified.
  Attribution also reads pip's own PEP 610 `direct_url.json` record, which
  answers the first two of those with the real verdict instead of a shrug.
  **New exit code 3**: pip installed the distribution, but some endpoint of it
  will not bind. `0` still means installed-and-bindable (and is also what "no
  verdict" returns — something *is* on disk, and unknown is not failure); `2`
  still means the installer never ran; **`1` now means the installer ran and
  failed**, with its own exit code printed rather than returned, because pip's
  codes are not disjoint from this verb's — pip defines `VIRTUALENV_NOT_FOUND =
  3` (raised under `PIP_REQUIRE_VIRTUALENV`) and `UNKNOWN_ERROR = 2`, so a
  passthrough left "installed but inert" and "pip refused to run" reading
  identically. Nothing is ever auto-uninstalled — the report says the package is
  on disk and hands over the exact `aelix extension remove` command. The
  packaging hint printed for a manifest missing from a wheel now names the
  starter scaffold at a path that exists — `aelix_coding_agent/examples/starter/`
  in an installed environment — instead of a bare `examples/starter/`, which is
  not a directory at the repo root and does not exist once installed. `install`
  also accepts
  `--trust-extension-path DIST` now, so a pack installed outside the
  environment's real site directories (`pip --target` plus a matching
  `PYTHONPATH`) is not called broken when it is merely unvouched; the flag means
  exactly what it means to `verify` and to `aelix` itself.
- A malformed `aelix-plugin.toml` no longer echoes the manifest's contents
  into the error printed on startup (#91). Pydantic's validation errors
  interpolate the whole parsed document, which for a manifest declaring MCP
  servers includes `contributes.mcp_servers[].env` — plugin-supplied API
  tokens. Errors now carry the failing field and message only. Affects both
  directory-installed and package-installed extensions.
- The same extension discovered both as an installed package and through a
  scanned extensions directory now loads once rather than twice (#91). Its
  `setup()` previously ran twice against the same runtime.

### Known behaviour changes

- **`aelix extension discover --refresh` no longer prompts on your terminal for
  git or ssh credentials, on macOS and Linux** (#221). The catalog clone runs
  in a session of its own there, so that a timeout can end the clone *and*
  everything it spawned: measured, `git remote-http` blocked in libcurl against
  a server that accepts and never answers outlives a kill aimed at `git` alone
  and is still running three seconds later. A process in a session of its own
  has no controlling terminal, and the terminal is where git, ssh and a
  credential helper ask you for a password, a passphrase or a host-key
  confirmation. **Configure a credential helper (https) or an ssh agent and a
  known host key (ssh) — the clone now fails at once with git's or ssh's own
  message (`fatal: could not read Username for '…'`, `Host key verification
  failed.`; the errno text git appends after that colon is your platform's
  `strerror(ENXIO)` — `Device not configured` on macOS, `No such device or
  address` on Linux) instead of waiting at a prompt you cannot see, and the
  timeout error names the cause.** Askpass programs and GUI/keychain helpers
  need no terminal and keep working — including asking you: if `GIT_ASKPASS` or
  `SSH_ASKPASS` is set (VS Code exports `GIT_ASKPASS` unconditionally in its
  integrated terminal) git still calls it, and a dialog nobody answers still
  costs the full 60 s clone timeout before the clone fails, exactly as it did
  before. **On Windows there is no session to take away**: the containment
  there is a new process group plus a job object, the clone keeps the console
  Aelix was started from, and a prompt can still appear on it — one nobody
  answers costs the same full 60 s (adversary-1; Windows is not a supported
  host, see the README). Stopping a refresh by hand now takes two `^C`: the
  clone runs outside the terminal's foreground group, and `asyncio.run`'s
  `Runner._on_sigint` cancels the main task and returns on the first —
  measured, one `^C` left the clone running to its full bound (the timeout
  ladder ended it) and two ran the interrupt ladder at 0.25 s
  (callers-census-1).
- **An extension's `aelix.exec(...)` no longer inherits your terminal; it now
  decodes output as UTF-8, and a timeout now takes its whole tree with it**
  (#221). Its stdin is `/dev/null` rather than the terminal Aelix was started
  from, which is the contract this surface has upstream. Output is decoded
  UTF-8 with replacement, so bytes that are not valid text become `U+FFFD`
  instead of raising out of the extension — and line endings still normalise to
  `\n` as they did. (This bullet used to add "on Windows, output in a legacy
  code page that used to decode correctly under your locale now shows
  replacement characters". #239 pays that back in the same unreleased block:
  this site decodes run-wise through the console code page now, so the clause
  was struck rather than shipped contradicting the entry above.) A
  successful exit kills nothing — a helper the command backgrounded before
  exiting **successfully** still survives — while the timeout, an interrupt raised
  inside the call, and a cancelled turn *that lands while the command is still
  running* each end the whole tree (docs-adr-2). A cancelled turn that lands
  once the command has been reaped — inside that reading — kills nothing: it
  ends the reading of its output, and the helper survives (#230). **A command
  that expects a terminal — a prompt,
  a pager, an editor — now fails immediately instead of stalling until its
  timeout; one that reaches for an askpass-style program instead of the
  terminal can still block until its timeout, as it always could.** On Windows
  that first half does not hold: the child gets no session (there are none),
  only a new process group and a job object, so it keeps the console Aelix was
  started from and a program that opens the console directly — rather than
  reading the stdin we set to `NUL` — can still prompt and still stall until
  its timeout (adversary-1). Output written by a *descendant* more than two
  seconds after the command itself exited is no longer waited for — the command
  still reports its own exit code, but that trailing output is cut. And a
  cancelled turn now ends a command that is **still running**: **Esc, or ^C in
  `aelix -p`, kills the command's tree** instead of leaving it to run out its
  timeout (measured: on the old code a ^C reached the command through the
  terminal in 0.02 s; with a session of its own and no such handle it would
  have waited the command's whole remaining life, 28.58 s of a 30 s sleeper).
  Once the command has been reaped, Esc kills nothing — see the `### Fixed`
  entry for #230. `code=124 killed=True` on a
  timeout is unchanged.
- **AUTO mode now prompts instead of auto-allowing when your `$SHELL` is one
  the safety classifier cannot read** (#104). The AUTO posture decides whether
  to auto-run a `bash` command by parsing it with a tree-sitter **bash**
  grammar. That verdict is only sound for the POSIX shell family (`bash`,
  `sh`, `dash`, `ksh`, `mksh`, `zsh` — version suffixes such as `bash-5.2` are
  recognised). Under any other shell — `fish`, `nushell`, or PowerShell/`cmd`
  on the experimental Windows track — the grammar reads the command line as
  unremarkable words and returns "allow", which is active mis-permissioning
  rather than a missed detection. Such commands now fall through to the
  approval prompt. **If you use fish or another non-POSIX shell on Linux or
  macOS, you will see prompts in AUTO mode where commands previously ran
  unattended.** `DENY` verdicts are still enforced for every shell, and the
  other permission postures are unchanged.

## [0.1.0-beta.1] - 2026-08-20

The first published release of Aelix, cut as the tag `v0.1.0-beta.1`
(distribution version `0.1.0b1`).

Nothing was published before it. This file previously carried a
`## [0.1.0] - 2026-06-20` entry describing an "initial public release" — no such
tag and no such GitHub Release ever existed, and its contents are the first
block of *Added* below. The *Changed* entries therefore describe changes made
during development, relative to the state of the repository rather than to any
earlier published version.

### Added

- **Agent runtime (`aelix-agent-core`)** — stateful `Agent`, hook-aware
  `AgentHarness`, typed `HookBus`, and the low-level async agent loop.
- **AI primitives (`aelix-ai`)** — provider-agnostic message, streaming, and
  tool types with pi-ai parity.
- **Providers** — Anthropic and OpenAI-compatible backends (incl. OpenRouter),
  with reasoning/thinking wiring, custom-model loading from `models.json`, and
  config-value auth indirection (env-var / command).
- **Built-in tools** — bash, read, write, edit, ls, grep, and find, with
  pi-parity schemas and behavior (including image read/resize and `rg`/`fd`
  acquisition).
- **Compaction** — context summarization with entry-level cut-points,
  split-turn handling, file-op preservation, and a token cap.
- **Extensions API (`aelix-coding-agent`)** — 4-tier extension architecture,
  extension loader, built-in policy/guardrail extensions, runtime tool
  registration, and example tools.
- **Project Trust** — running in an untrusted directory gates project-local
  extensions (`.aelix/extensions/`) and MCP servers (`.aelix/mcp.json`) behind a
  trust prompt with on-disk persistence; deny-by-default in headless mode.
- **Cooperative abort** — `Esc` cancels in-flight tools (bash, grep, find, read,
  write, edit, ls) without orphaning processes, and the RPC `abort_bash` kills
  the running shell.
- **TUI** — an interactive terminal shell (optional `[tui]` extra) with slash
  commands, streaming Markdown output, compact tool cards, a status footer and
  context meter, steer/queue, session resume/fork, and an external-editor
  binding.
- **CLI** — the real `aelix` command (session, fork, export, and model flags)
  plus a headless RPC mode and OAuth credential management.
- **Release engineering** — CI (ruff + pytest on Python 3.11 / 3.12) and a
  tag-triggered PyPI publish workflow using Trusted Publishing (OIDC).

- **Beta / pre-release track** — pre-releases (beta/rc/alpha) are cut as
  **GitHub Releases only** and installed via a checksum-verified `install.sh`
  one-liner (`uv`-based, wheels verified against a published `SHA256SUMS`
  manifest); PyPI publishing is reserved for GA. The `release.yml` workflow now
  attaches a `SHA256SUMS` manifest to each Release and skips the PyPI `publish`
  job for hyphenated (pre-release) tags. First beta version: `0.1.0b1`
  (tag `v0.1.0-beta.1`). See `RELEASING.md` → *Beta / pre-release track*.

- **One delegation can now carry several tasks** — the `agent` tool gains
  `mode: "parallel"` (run up to 8 tasks at once, at most 4 at a time) and
  `mode: "chain"` (run them in order, where a later task can write `{previous}`
  to insert the previous one's summary). Every task in one call runs under one
  agent profile in one working directory, results come back in the order you
  listed them, and a chain stops at the first failure and names the steps that
  never ran. Asking for more tasks than the limit is **refused** — the list is
  never quietly trimmed to fit. A single-task delegation is unchanged in every
  respect. See ADR-0199.

- **A multi-task delegation asks once, showing every task** — one consent
  prompt for the whole call, listing each task and the one directory they share,
  rather than one prompt per subagent or one prompt standing in for tasks you
  never saw. If that prompt would be taller than your terminal, the whole call
  is refused with an explanation of how many tasks would fit, instead of being
  drawn with its `Cancel` option pushed off the bottom of the screen. An
  ordinary read-only delegation still shows no prompt at all. A chain is never
  offered the "allow file edits for this run" option: its later steps are fed
  text written by an earlier subagent, which no one has read, so the dialog says
  so and declines to grant write authority for it. See ADR-0199.

- **A running batch is visible while it runs** — one status-line row for the
  whole batch (`agent scout ×4 · 2 running · 1 done · 1 queued · 33s`), one line
  per subagent in the tool card that stays in the transcript afterwards, and a
  small panel above the input box while two or more subagents are live. A single
  delegation keeps exactly the display it had. See ADR-0199.

- **The agent now knows how to extend itself** (#117) — the default system
  prompt gained a short "Extending yourself" block, scoped to the case where you
  ask for a tool, command or hook to be added to Aelix itself. It names the real
  contract (one Python file with `def setup(aelix)`, imported and run
  in-process — no manifest, no build step), gives both extension directories as
  absolute paths, and points at two files that ship inside the wheel — a worked
  example and the full API — so the agent reads the real API instead of guessing
  at one. Asked in plain language to add a tool, a fresh install previously
  failed *and* claimed success, inventing a `tools_definition.json` manifest
  that exists nowhere in Aelix; the word "extension" did not appear anywhere in
  the prompt it was given. The block adds about 1,200 characters of fixed text
  to the base system prompt, plus the four absolute paths it embeds — so the
  exact size depends on your working directory and where Aelix is installed.

  The global directory (`~/.aelix/agent/extensions/`, or
  `$AELIX_CODING_AGENT_DIR`) is offered first because it is not trust-gated; the
  project-local one (`<project>/.aelix/extensions/`) is offered second and
  labelled with its condition, because an untrusted project drops its
  project-local extensions with no error and no warning. The block also says a
  `.aelix/` write may ask for approval and that the prompt is not a refusal,
  and that a declined *or* policy-blocked write ends the attempt — it must not
  be retried through `bash` or written somewhere else to get around it.

  Every instruction the block emits is executed as part of the test suite
  rather than string-matched, so a command the agent is told to run cannot ship
  broken: the API grep hint is re-run through the real bash tool, ripgrep and
  the Python fallback, and the file it says to read is read back through the
  real `read` tool to prove the content actually arrives.

  The block is **omitted** whenever you supply your own prompt:
  `--system-prompt` / `--system-prompt-file`, or an agent profile with
  `system_prompt: replace`. Those remain full overrides — nothing is silently
  appended to them.

### Changed

- **The per-prompt delegation cap now counts subagents, not `agent()` calls, and
  a call that does not fit what is left is refused whole.** The limit of 12 has
  not moved, but one call can now start up to 8 subagents, so the two readings
  are no longer the same thing — counting calls would have allowed 96 subagents
  from a single prompt. A call asking for more subagents than the prompt has
  left is refused before anything is shown or started, and the refusal tells the
  model how many it may still ask for; nothing is trimmed and no subagent is
  started only for its siblings to come back as refusals. `/agents run` is still
  not rate-limited. See ADR-0199.

- **`timeout_ms` now has a maximum, and one `agent()` call is capped in total.**
  A task may ask for at most 30 minutes, and one call — however many tasks it
  carries — is bounded by the same 30 minutes, so an eight-step chain at the
  default per-task timeout can no longer occupy the session for 80 minutes.
  Previously only a *minimum* was checked, so a model could ask for a timeout of
  any size. A task that runs out of the call's remaining time returns a readable
  refusal instead of being silently dropped, and every task that already
  finished still reports its result. See ADR-0199.

- **Tightening the permission posture with shift+tab during a delegation now
  applies to the subagents that have not started yet.** In a batch of eight,
  subagents five to eight begin only after the first four finish, which can be
  much later; until now they would have launched at the posture that was in
  effect when the call began. Tightening always applies; loosening never raises
  a subagent above what was approved at the prompt. See ADR-0199.

- **`auto-accept-edits` / `auto` no longer auto-approve writes under `.aelix/`** —
  editing an agent profile (`.aelix/agents/*.md`), a project extension
  (`.aelix/extensions/*.py`), the project MCP config (`.aelix/mcp.json`) or
  project settings (`.aelix/settings.json`) now shows the usual approval prompt
  instead of being written silently. Those files **execute on a later run**, so an
  unattended agent that could write them could author what the next session runs.
  Ordinary in-project writes are unaffected. See ADR-0197 §(i).
- **`auto-accept-edits` / `auto` now resolve symlinks before deciding whether a
  write may be auto-approved.** Both of that gate's rules — "inside the project
  root" and "not a security-sensitive path" — were previously decided on the
  path as written, so a symlink checked into the repository (git stores them as
  mode 120000) could land an auto-approved write in `.aelix/`, `~/.ssh/` or the
  home directory. Such writes now show the usual approval prompt. A symlinked
  project root still works, and a symlink that stays inside the tree is still
  auto-approved.
- **`--no-agents` now beats `--agents` wherever the two appear on one command
  line**, which is what both flags have always been documented to do. The parse
  loop was really last-flag-wins, so a wrapper script or shell alias pinning
  `--no-agents` could be silently re-opened by a later `--agents`. A lone
  `--agents` is unaffected.
- **The delegation consent dialog now appears only when write authority is
  actually at stake** — when the subagent would run with a permission mode that
  can change files without asking, or when the agent profile itself declares it
  needs one (`approval_mode: auto` or `ask`) and you can therefore grant it at
  the dialog. An ordinary read-only delegation — a profile with no
  `approval_mode:` line, under an ordinary permission posture — now starts
  without a prompt: the dialog there could only offer "Run read-only (plan)" or
  "Cancel", and a confirmation with no real answer teaches you to dismiss the one
  that matters. Nothing gains authority — the subagent runs at exactly the mode
  that dialog would have granted, still cannot mutate anything, and is still
  bounded by the per-prompt and per-session delegation caps and shown in the
  status line. Running a *project-local* agent profile still takes its own
  explicit confirmation, which is unchanged. See ADR-0197 §(i) and residual R7.

  **`yolo` is the exception, and the only one** (#196, ADR-0231): there no
  dialog appears at all, because `yolo` means "run mutating tools without a
  prompt" and a delegation was the last prompt it had. The child is named in
  the status line while it runs instead — profile, posture, source file, task
  count — and the finished tool card's `[agent … · yolo · …]` footer records
  the posture afterwards.
- **A profile's `approval_mode:` now decides whether a delegation may be widened
  at the dialog.** `auto` and `ask` declare that the agent needs write authority,
  so their dialog offers "Allow file edits for this run (auto-accept-edits)";
  `inherit` (the default) and `deny` declare nothing and are never offered it —
  which is why a plain read-only delegation no longer prompts at all. Authority
  follows what the profile *declares*: the way to let an agent write is to edit
  its profile, a file you can review and sign, rather than to upgrade it from a
  modal in the middle of a turn. `deny` can never be widened, and a project-local
  profile can never be widened whatever it says. See ADR-0197 §(i).
- **A delegation dialog answer that matches none of the offered options is now a
  decline.** Previously only `Esc` and `Cancel` declined and any other string
  granted the delegation at the inherited posture. Not reachable from the shipped
  TUI, which can only return an offered option or `None`, but `ctx.ui` is a public
  extension seam. See ADR-0197 §(i).
- **Delegation is now capped per prompt and per session** — at most 12 subagents
  started by the model in one user prompt, and at most 4 live at once. Both
  return a readable refusal to the model rather than an error. `/agents run` is
  not rate-limited. See ADR-0197 §(i) residual R1.
- **A delegated child that finishes right at its timeout is no longer reported as
  a timeout.** The gap between the child closing its output and the OS reporting
  its exit status was being charged to the caller's budget, so a completed run
  with the correct answer could come back as `status=timeout, ok=False`.
- **The temporary file holding a delegated agent's system prompt is now reclaimed
  after a crash.** It was already deleted on every normal, error, timeout, kill
  and cancellation path, but a parent killed outright (SIGKILL, OOM) left it in
  the system temp directory permanently — one per delegation, mode 0600. Aelix
  now sweeps prompt files belonging to processes that are gone, at startup.

### Fixed

- **`aelix --list-models` with no credentials no longer points at a command that
  does not exist.** It advised running `aelix auth`; the `aelix` console script
  parses that as the first user message, so the one instruction a brand-new user
  received could not work. It now names the provider environment variables and
  the `/login` command inside the TUI, both of which exist.
- **The headless "No model selected." message no longer ends with a TUI-only
  instruction.** `--print` and `--mode json` print it when no provider can be
  resolved, and it closed with `Then use /model to select a model.` — `/model`
  is a TUI command, so a headless reader had no prompt to type it at. It now
  leads with `--model <id>`, which works on the invocation that just failed, and
  offers `/model` only as the interactive alternative.
