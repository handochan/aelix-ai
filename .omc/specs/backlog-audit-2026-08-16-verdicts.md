# 백로그 71건 — 판정 원표 (2026-08-16)

`backlog-audit-2026-08-16.md`의 부속 자료. 🔴 = 닫기 제안이 적대적 검증에서 **반박된** 건.
verdict는 반박을 반영한 최종값이다.

| # | verdict | impact | effort | theme | 반박됨 | 한 줄 |
|---|---|---|---|---|---|---|
| #3 | LIVE | internal-only | S | `epic-rollup` | 🔴 | A 14-item wishlist epic from day one: memory, web, notebook, multi-agent, skills, guardrail, governor, shift+t |
| #6 | LIVE | internal-only | S | `loop-governor` | 🔴 | Agentic-loop robustness for weak local models: give tools timeouts, and add a loop detector/governor/recovery. |
| #7 | ALREADY_DONE | none | S | `extension-marketplace` |  | Decide what an extension marketplace even is for aelix — including self-hosted/air-gapped repositories and how |
| #14 | LIVE | papercut | L | `loop-governor` |  | The agent loop can run forever: no iteration cap, no detection of the same tool call being made over and over, |
| #16 | LIVE | none | S | `multi-agent-delegation` | 🔴 | Let one session spawn and orchestrate child aelix processes as a 'team', with a TUI view of what the children  |
| #17 | LIVE | papercut | M | `builtin-tool-extensions` |  | The agent has no way to write anything down that survives the session — give it a memory tool as a built-in ex |
| #18 | LIVE | papercut | M | `builtin-tool-extensions` |  | The agent cannot fetch a URL — add a web_fetch built-in extension with size and timeout caps, and defer web_se |
| #25 | LIVE | papercut | L | `extension-lifecycle` |  | You cannot turn an installed extension off without restarting — the /extension Installed tab can only be looke |
| #26 | LIVE | none | S | `multi-agent-delegation` | 🔴 | Show a per-subagent line in the statusline — role, elapsed, tokens — plus an "N agents" aggregate. |
| #27 | LIVE | papercut | L | `session-management` |  | Make /resume a real session browser — scope beyond this folder, rename/delete, and labels that say what the se |
| #29 | LIVE | security | XL | `permission-system` |  | Finish the permission system: rules that survive restart, gating for MCP tools, and a way to mark a tool as mu |
| #30 | NEEDS_OWNER | none | XL | `mcp-integration` |  | Let other MCP-native orchestrators drive Aelix by serving the 29 RPC commands as MCP tools. |
| #33 | LIVE | broken-feature | M | `extension-api-gaps` |  | Extensions can register a raw-keystroke handler and it is never called. |
| #34 | LIVE | papercut | M | `context-compaction` |  | The /context composition table is guessed at 4 characters per token and has no Skills row. |
| #35 | LIVE | papercut | M | `tui-polish` |  | There is no /init to write a starter AGENTS.md from what the project looks like. |
| #37 | LIVE | papercut | S | `extension-api-gaps` |  | Descriptor `rows_path`/`text_path` are documented as JSONPath but only resolve plain dotted keys. |
| #38 | LIVE | papercut | M | `tui-polish` |  | In the real TUI you can deny a tool call but you cannot tell the model why — the "No, provide reason" answer e |
| #40 | LIVE | broken-feature | M | `tui-polish` |  | /fork can only ever branch off your most recent message; there is no way to fork from an earlier one. |
| #41 | LIVE | papercut | S | `tui-polish` |  | The /statusline picker shows a "Use theme colors" checkbox that persists to disk and changes nothing on screen |
| #42 | NEEDS_OWNER | papercut | S | `tui-polish` |  | Two Ctrl+G editor follow-ups (kill the editor on shutdown; detect concurrent typing) that pi does not do eithe |
| #43 | NEEDS_OWNER | papercut | M | `model-catalog` |  | Let /scoped-models write a per-project model allow-list instead of only a global one. |
| #45 | NEEDS_OWNER | papercut | L | `tool-surface-gaps` |  | Add a cell-aware .ipynb editing tool so notebooks can be modified without hand-editing their JSON. |
| #46 | LIVE | papercut | S | `windows-support` |  | On Windows two Aelix processes refreshing OAuth tokens at the same time can silently clobber each other's cred |
| #47 | NEEDS_OWNER | none | XL | `editor-integration` |  | Speak ACP so Zed / Neovim agent plugins can drive Aelix, instead of only Aelix's own JSONL wire. |
| #48 | NEEDS_OWNER | none | S | `tui-visual-polish` |  | Paint the echoed user prompt as a full-width shaded bar instead of a plain bold-cyan line. |
| #52 | LIVE | broken-feature | L | `agent-loop-governor` | 🔴 | A self-hosted OpenAI-compatible endpoint that emits tool calls as `<tool_call>` text instead of structured `to |
| #53 | LIVE | none | S | `self-extension` | 🔴 | Let the agent write an extension and use it in the same session, pi-style, with no restart. |
| #54 | LIVE | internal-only | S | `pi-upstream-sync` | 🔴 | Track the 646-commit pi delta from v0.74.1 to v0.80.2 and file what should be adopted. |
| #58 | LIVE | none | L | `pi-upstream-sync` |  | Seed dark/light from the terminal's real background on first run (OSC 11) and follow later color-scheme change |
| #59 | LIVE | papercut | S | `pi-upstream-sync` | 🔴 | Detect Warp so Kitty inline graphics turn on, and recognise BMP files so they attach as images. |
| #60 | LIVE | papercut | M | `pi-upstream-sync` |  | Add `--exclude-tools` (subtract from the default tool set) and `--session-id` (pin a deterministic session id  |
| #61 | NEEDS_OWNER | papercut | M | `provider-live-verification` |  | Run one real-key turn through openai-responses, Gemini, Vertex and an auth-enabled Cloudflare gateway before t |
| #63 | LIVE | papercut | L | `extension-api-gaps` |  | Two activation-engine leftovers from #21: a plugin that only wants to wake on a tool call still runs its facto |
| #68 | LIVE | none | M | `extension-marketplace` |  | Half of this shipped: `aelix extension index <dir>` generates a catalog from a wheelhouse; what is left is bro |
| #69 | LIVE | ? | ? | `provider-adapter-parity` | 🔴 | Wire the three Anthropic compat flags ADR-0190 detected but left inert, and stop replaying empty text blocks. |
| #83 | LIVE | papercut | L | `provider-adapter-parity` |  | The Anthropic adapter never sends four things pi sends: tool_choice, metadata.user_id, cache_control breakpoin |
| #84 | LIVE | papercut | L | `tui-polish` |  | The /settings menu shows toggles that write to settings.json and are then read by nobody. |
| #85 | LIVE | none | M | `copilot-provider` |  | Copilot model->endpoint routing is read from the static catalog instead of the account's own /models supported |
| #86 | NEEDS_OWNER | internal-only | unknown | `release-ga` |  | A 2026-07-11 strategy memo that declares security/self-host-first positioning "confirmed" and lists the launch |
| #93 | LIVE | internal-only | M | `extension-api-gaps` |  | An extension can swap the backend for the user's `!` bash but not for the agent's `bash` tool call, because no |
| #94 | LIVE | internal-only | M | `extension-api-gaps` |  | An extension that registers a TUI autocomplete provider gets no error and no completions — the registered fact |
| #95 | LIVE | none | S | `extension-api-gaps` |  | Add pi's `baseToolsOverride` seam so an SDK embedder can swap the whole base toolset instead of pre-merging ev |
| #96 | LIVE | none | L | `extension-api-gaps` | 🔴 | A plugin can declare `capabilities.mcp_serve` and nothing ever stands up a server. |
| #103 | LIVE | papercut | M | `windows-support` |  | Stop claiming Windows support in package metadata and add a windows-latest CI leg so the failure count is a nu |
| #105 | LIVE | internal-only | S | `windows-support` |  | Killing a delegated agent still reaches a raw SIGKILL that does not exist on Windows — the two tool-side kill  |
| #106 | LIVE | papercut | M | `windows-support` |  | The Windows install one-liner we publish points at a script that has never been executed anywhere. |
| #107 | LIVE | broken-feature | S | `windows-support` |  | On Windows, piping into aelix from a producer that never writes hangs forever with no message, because the who |
| #108 | LIVE | broken-feature | M | `windows-support` |  | Four Windows path-shape defects remain after the two security ones were fixed: session-dir grants die, grep ha |
| #109 | LIVE | internal-only | XL | `windows-support` |  | Make the test suite pass on a real Windows runner — starting with the tests that sandbox themselves with HOME  |
| #110 | LIVE | internal-only | S | `windows-support` | 🔴 | Umbrella status report and P0–P7 roadmap for Windows support. |
| #123 | LIVE | none | XL | `multi-agent` |  | Let an extension declare that its delegations run over rpc, and build the intervention API that would make cho |
| #124 | LIVE | papercut | M | `install-and-update` |  | Add `aelix --update`, and at launch notice a newer version and offer to install it. |
| #127 | LIVE | internal-only | S | `extension-security` |  | Make the widget paint path re-check `ui_tui_trusted` itself instead of trusting that the load-time gate alread |
| #128 | NEEDS_OWNER | internal-only | M | `extension-security` |  | Stop the capability gate from reading pydantic defaults that in-process code can mutate process-globally. |
| #129 | LIVE | internal-only | M | `test-suite-hygiene` |  | A process-global provider registry leaks out of tests/cli and makes the /model, picker and scoped-models tests |
| #130 | LIVE | broken-feature | S | `mcp-integration` |  | Isolate each MCP server's tool collection so one server dying after connect does not take the others down. |
| #131 | LIVE | security | M | `marketplace-catalog` |  | A private catalog whose entry lists a relative `source` turns into a public-PyPI lookup of the same name when  |
| #132 | LIVE | papercut | L | `tui-polish` |  | Make the multi-agent panel actually look like a table — role glyphs, aligned columns, a real "N local agents"  |
| #133 | LIVE | papercut | M | `tui-polish` |  | Four cosmetic live-QA defects; one of the four has since shipped and one is recommended for wontfix, so what i |
| #137 | LIVE | data-loss | L | `session-durability` |  | Two terminals on one session file: everything both of them write lands on disk, but one terminal's entire turn |
| #138 | NEEDS_OWNER | security | L | `session-durability` |  | Everything the agent reads or you type — `cat .env`, a credentials file, an `env` dump — is written verbatim i |
| #139 | LIVE | papercut | S | `session-durability` |  | Session files accumulate forever — no age/count/size policy, no prune command, no way to see how much disk `~/ |
| #141 | LIVE | papercut | M | `startup-perf` |  | Every invocation pays 1.5-4 s of module import before anything happens — `aelix --version` is essentially 100% |
| #142 | LIVE | broken-feature | M | `release-ga` |  | Five things that must be true before the first un-hyphenated tag, chiefly: the version assert never checks ext |
| #146 | LIVE | broken-feature | S | `headless-modes` |  | Starting `--mode rpc` with stdin redirected from a file or /dev/null does not start an RPC server and does not |
| #147 | LIVE | none | S | `tui-polish` | 🔴 | During an auto-retry the stop keys did nothing and the TUI sat at "Working…" with no way out. |
| #148 | LIVE | papercut | S | `self-description-accuracy` |  | Two untrue statements the code makes: a /model refusal that tells a credentialled user to run /login, and a co |
| #157 | NEEDS_OWNER | papercut | S | `agent-profiles` |  | When `--model` overrides a profile's `model:`, the notice names the field that lost but never the value, so th |
| #168 | LIVE | papercut | L | `session-durability` |  | A resumed session cannot render edit diffs, bash exit codes or descriptor cards, because the persisted tool re |
| #171 | NEEDS_OWNER | internal-only | M | `self-description-accuracy` |  | Two thirds of the repo's `file.py:NNN` self-citations pointed somewhere their own sentence was not about, and  |
| #172 | LIVE | papercut | XL | `model-catalog` |  | Aelix ships a frozen model catalog with no way to pick up newly released models short of a package release or  |

---

## 클러스터 18개 (제안 순서)

### [1] Session data safety — a turn silently vanishes, secrets sit in plaintext, and nothing is ever collected

**이슈**: #137 #138 #139

**사용자 영향**: #137 destroys a whole turn of work with no error, and it fires on the most ordinary thing a user does: two terminals on one session (`aelix --continue` resolves the same file for both). #138 means every `.env`, `env` dump and credentials file the agent has ever read is sitting verbatim in ~/.aelix, ready to leave with a backup, an export or a support bundle. #139 makes that pile grow forever with no way to see or trim it.

**제안**: Keep all three separate — each needs a DIFFERENT decision made before any code — but run them as one sprint in the order 137 -> 138 -> 139. #137: pick single-writer lock vs re-read-tail-before-append (flock is NOT the fix; the bug is the process-local `_current_leaf_id` at jsonl_storage.py:448/541), and ship the Windows leg with it per #46. #138: pick write-time vs export-time redaction — that choice also decides how urgent #139 is. #139 then becomes an S (age/count/size policy + a `prune` command + a usage view).

### [2] Permission and trust enforcement — the approval gate has a hole an MCP tool walks straight through

**이슈**: #29 #127 #128  ·  **우산**: #29

**사용자 영향**: A file-writing MCP tool never reaches the approval prompt at all: `permission._MUTATING` is a fixed name set {bash, create_file, edit, execute_command, sh, shell, write, write_file} while MCP tools register server-qualified (mcp/adapter.py:95). The user believes the posture engine is protecting them; on that path it is not. #127/#128 are the opposite — real, but both require the attacker to already have in-process code execution.

**제안**: SPLIT #29 before touching it — it is XL only because it is five issues in a coat. File the MCP/skills gating hole as its own issue TODAY (that is the security item, and it is M not XL). Leave persistence + `AgentTool.mutates` on #29. Strike three bullets that already shipped and were never crossed off: the richer approval panel (tui/approval_dialog.py, ADR-0157), tree-sitter bash arity (builtin/bash_classifier.py, ADR-0158), and tool-hiding (`set_active_tools` is already extension-facing). Keep #127/#128 separate and label them post-RCE hardening in the title so they can never out-rank the real bypass.

### [3] Hangs and cascading failures — one bad input and the process never comes back

**이슈**: #146 #130 #147

**사용자 영향**: `--mode rpc < /dev/null` hangs forever (rc=124) — exactly the shape a supervisor or CI script writes. One stdio MCP server dying during `list_tools()` takes every other server's tools with it. Both are S-sized and both produce the worst possible outcome: no result, no error, no exit.

**제안**: Close #147 now — it shipped this morning (merge 9686a0f, `fix/147-abort-during-retry`). Keep #146 and #130 separate but do them in the same pass: #146 = detect a non-pollable fd 0 (`os.fstat`/`S_ISREG`) and use a thread reader or exit clean on EOF; #130 = wrap each server's collection in `collect_agent_tools` (mcp/manager.py:67-81 has no try/except at all, verified at HEAD), mirroring the #125 connect_all fix. Highest value-per-hour in the whole backlog.

### [4] The loop and the tools inside it are both unbounded

**이슈**: #6 #14 #52

**사용자 영향**: Two different unbounded things wear one label. A tool can run forever (`loop.py:637` awaits `tool.execute` with no `asyncio.wait_for`; MCP sessions are built with `read_timeout_seconds=None`, so an unresponsive MCP server hangs the run — escapable with Esc in the TUI, not escapable at all in --print/rpc). And the turn loop itself has no cap, no dedup and no re-steering, which is what weak local models on closed networks — the product's stated wedge — actually need.

**제안**: Do NOT collapse these into #14; both merges were tried and both were refuted. #6: re-scope to its live orphan — a tool-execution timeout policy (#11 only ever shipped the bash half; its own title says 'bash default timeout + TOOL TIMEOUT POLICY'). S fix: pass `read_timeout_seconds` in mcp/client.py:114 and wrap the execute site. #52: re-scope to clause (c) only — `thinking_format='qwen-chat-template'` is unreachable because `detect_compat` can never emit it (COMPAT_DEFERRED_ALLOWLIST, _openai_compat.py:75-81), so a self-hosted vLLM/Qwen endpoint gets no thinking parameter at all; this is request-side and a governor cannot fix it. #14 stays the governor and stays L — ship the extension-first slice (dedup veto via a reducible `tool_call` hook, recalibration via steering, soft cap via `terminate=True`) and promote only `max_iterations` to core.

### [5] Inert controls and statements the code makes about itself that are false

**이슈**: #84 #41 #58 #148  ·  **우산**: #84

**사용자 영향**: A user opens /settings, flips eleven different switches, and nothing happens — ever. They tick 'Use theme colors' and it persists to disk and changes no pixel. They pick a light theme and no built-in surface reads it. They are told to run /login for a provider whose key is already set. Each individual fix is S; the aggregate is the fastest way this product loses a user's trust.

**제안**: One merge only: fold #41 into #58 as a single honestly-titled issue — 'the theme is stored but never applied' — because detection (OSC 11) is worthless until a built-in surface consumes a Theme, and today `context.theme` reaches four extension-supplied consumers and nothing else. Keep #84 separate and just do it (11 getters, live reads where cheap, mirroring how `tool_card_max_lines` already works) — and if any row will not be wired this quarter, delete the row rather than keep shipping 'applies next launch'. #148 is two independent two-line fixes, both still true at HEAD (model_argument.py:390 derives `offered_providers` after the pool swap; _transform_messages.py:209 still claims a field that has existed since #15).

### [6] Provider adapters and the model catalog

**이슈**: #61 #83 #69 #85 #172 #43  ·  **우산**: #83

**사용자 영향**: Four adapters are advertised in README and the provider guide and have never touched a real endpoint (#61) — a user who picks one is the integration test. Anthropic callers cannot force or forbid a tool at all (no `tool_choice` anywhere, verified) and get no prompt caching. Copilot works only because the static catalog happens to match today's hosts. And the model catalog is a frozen snapshot, so every new model release makes the product look abandoned (#172).

**제안**: Merge #69 into #83 — but FIRST move #69's item 3 onto #83's gap list, because #83 explicitly excludes it ('out of scope for this issue unless bundled') and it is the one piece that would be lost: `transform_messages` emits `{'type':'text','text':''}` and empty-content messages that the Anthropic API rejects, and the openai_completions sibling already filters them, so the 'fix both adapters together' premise is dead. Keep #61 (owner-only, needs real keys — narrow it to the three genuinely unverified surfaces and strike the anthropic item its own comment already confirmed), #85, #172 and #43 separate. #43: strike the `blocked_by: protected-core` line, ADR-0201 D1 killed it; what remains is a deliberate divergence from pi, i.e. a yes/no.

### [7] Windows — the classifier claims support that has never existed

**이슈**: #110 #103 #105 #106 #107 #108 #109 #46  ·  **우산**: #110

**사용자 영향**: All four pyprojects still declare `Operating System :: OS Independent` (verified at HEAD) while zero tests and zero CI jobs have ever run on win32. A Windows user installs, the TUI comes up, file tools work — and then every shell command fails, search timeouts crash instead of degrading, skills register nameless, and two OAuth processes can silently clobber each other's tokens (#46).

**제안**: Keep the umbrella (#110 is already the correct roadmap) and keep every child — this is scattering done RIGHT, do not merge it. Do #103 first and only #103 first: it is hours of work, it removes a false claim from PyPI metadata, and without a windows-latest job the rest is a snapshot rather than support. Two hard sequencing facts from the children: fix #109's 24 `setenv('HOME')` tests BEFORE enabling the job (ntpath.expanduser ignores HOME and those tests write to the runner's real profile), and #46 is already P5 on #110's own roadmap so fold it in rather than tracking it apart.

### [8] Resume is a headline feature that is half-built

**이슈**: #27 #168

**사용자 영향**: A resumed session shows no diffs, no exit codes and no descriptor cards, because the persisted toolResult carries less than the live event did. And the picker can only see this folder, labels sessions `{created} · {short-id}`, and cannot rename or delete — so as #139's pile grows it becomes unusable.

**제안**: Keep separate — different layers, different decisions. Rewrite #27's body first: the 'only 9, no search' premise is DEAD (the ADR-0163 picker is already scrolling + type-to-filter; the `# select() shows the first 9` comment at shell.py:733 is stale), so scope it to metadata (title + message count), all-folders scope, sort, rename/delete — pi already has all of these. #168 is a band decision, not a patch: `ToolResultMessage` has no `details` field, so recovering diffs means putting more on the kernel wire; the `tool_call_id -> tool_name` map is recoverable in render.py alone but must land WITH the rest or you get a half-rendered card.

### [9] TUI command and interaction gaps — seven small independent holes

**이슈**: #40 #38 #35 #34 #133 #42 #48

**사용자 영향**: /fork can only ever branch off your most recent message. Denying a tool call sends the model no reason at all, so it retries the identical call. There is no /init. The /context table has no Skills row and guesses at 4 chars per token. The completion menu paints over the status bar on essentially every slash command.

**제안**: Keep all seven separate — do NOT create another 'TUI polish' bucket; that is how #66 and #133 became un-actionable. Three need their bodies corrected before anyone starts: #40 drop the `/fork <entry-id>` half (measured against pi at main AND at the ADR-0034 pin — pi matches `/fork` exactly and shows a picker; the argument never existed), #34 narrow to the Skills row (now unblocked by #115/ADR-0214) and drop the 'exact measured' half, #42 re-scope onto the real divergence its body does not mention (aelix passes `check=False` and applies the edit even when the editor exits non-zero, so `:cq` in vim still overwrites your input box). #48 and #42 need an owner yes/no first; #133's item 2 already shipped, so strike it.

### [10] Extension API surfaces that accept a registration and silently never consume it

**이슈**: #33 #94 #96 #37

**사용자 영향**: An extension author gets a working registration, a working unsubscribe, and silence. `on_terminal_input` handlers are stored and never iterated; `add_autocomplete_provider` appends to a list with zero readers; `capabilities.mcp_serve` validates, loads, and stands up nothing with no warning. This is the single most repeated defect shape in this repo (#20, #21, #80, #115 all closed on it) and it is still shipping.

**제안**: Do them in one pass but keep them as separate issues, because they are not the same size: #33 is a straight wiring job into the chrome key processor; #94 has an UNFINISHED CONTRACT first (`get_suggestions -> list[str]` has no display text and no replace-span, so decide the shape before external packs depend on it); #96 needs only Part 1 now — a loader warning mirroring the descriptor reserved/inert precedent — with Part 2 moving to the MCP-server product question in the containers cluster. #37 should shrink to a one-line honesty fix (change descriptor.py's 'JSONPath' comment to 'dotted key') and the JSONPath ask closed: it is a runtime-only, extension-only surface with zero known consumers.

### [11] Tool and file-input surface — memory, web, notebook, and a crash on an image type

**이슈**: #59 #17 #18 #45

**사용자 영향**: `aelix @photo.bmp` raises an uncaught UnicodeDecodeError — a plain traceback on a plain action (#59). The agent cannot write anything down that survives a session (#17) and cannot fetch a URL without a user-configured MCP server (#18).

**제안**: #59: strike the Warp/Kitty half — ADR-0223 deleted tui/images.py outright — and re-scope to the BMP magic byte alone; it lives on the still-live model-bound image path (util/image_detect.py), pi already returns image/bmp, and it is an S. #17 and #18 are the same shape of work (a bundled `register_tool` extension, following the guardrail/permission precedent) and should be ONE sprint; split or drop #18's `web_search` half, which has been blocked on an unmade provider/key call for 14 months and is dragging the whole issue. Note on #18: it must declare the `net` capability and honour `--offline`, or it becomes the one always-on hole in an otherwise gated network story. #45 is an owner call and its own body says so.

### [12] Multi-agent — the shipped half is real, the named half is not

**이슈**: #132 #26 #16 #123 #157  ·  **우산**: #16

**사용자 영향**: Delegation works and is default-OFF. What a user cannot do: intervene in or stop a running child (Enter is routed to `steer()` as a message, so the REPL is read-only for the whole call), run heterogeneous roles in one batch, or see more than a ragged one-line aggregate that overflows an 80-column terminal at four children.

**제안**: MERGE #26 into #132 — same surface, and #26's true residual (the multi-line footer-composer row, which never got the D1 deliverable, plus the non-batch 'N agents' aggregate) is exactly what #132's redesign has to build anyway. Keep #16 open but re-title it to what is actually left: P4 `aelix-team` — heterogeneous per-task profiles, declarative team files, the multi-pane dashboard that subsumes the widget panel, and a mid-turn stop. Keep #123 (transport) separate and follow its own order W3 -> W4 -> W1/W2/W5; note `RpcChannel` currently has ZERO production callers. #157 is not a sprint item at all — it is a two-minute owner decision (name the values in the notice, or leave it); answer it and close.

### [13] Marketplace and supply chain

**이슈**: #7 #131 #68

**사용자 영향**: #131 is a dependency-confusion shape: a hand-written catalog with a relative `source` classifies as a local path from the wheel directory and as a PUBLIC PyPI lookup from anywhere else. It is mitigated (the generator defaults to absolute paths) but not fixed. #68 is browse-only convenience; install-by-bare-name against a private index already works.

**제안**: CLOSE #7 — this is the only issue in the batch that is genuinely dead, and it died the good way: the definition question was answered and then implemented by #19/#32/#32-A/#64/#67/#65/#88/#89/#91/#145. If the two residual ops tasks matter (seed the official catalog, provision the first-party signing key) open them as their own one-liners rather than keeping a definition epic alive. Do #131 (resolve an entry's source against the CATALOG's location, or refuse a relative source that would fall through to a public index). #68 stays low — the owner already narrowed it correctly to Part 1.

### [14] Release, distribution and positioning

**이슈**: #142 #124 #86  ·  **우산**: #142

**사용자 영향**: #142 is a latent trap that only detonates on GA day: the tag/version assert never reads `[project.optional-dependencies]`, so `pip install 'aelix[tui]'` will demand a version that was never published, and there is no `pypi` GitHub Environment, so the first irreversible upload runs unattended. #124 is the ordinary expectation that a CLI can update itself — nothing exists.

**제안**: Keep separate. #142 is a five-item pre-GA checklist and every item is XS/S — do it as a block before any un-hyphenated tag, including the TestPyPI rehearsal (the publish step uploads all eight artifacts in one call with `skip-existing: false`, so a single 403 leaves PyPI with a partial, unfixable release). #124 is a real feature request with zero implementation. #86 is largely discharged — the README rewrite shipped and #74 closed — so strip it to the two things still owed and not code: written ToS verification for Copilot seat consumption, and the first-party reference packs. It should not sit in an engineering backlog in its current form.

### [15] Extension lifecycle and activation engine

**이슈**: #25 #63

**사용자 영향**: You cannot turn an installed extension off without restarting, and a pack whose manifest declares an MCP server still needs a full process restart — `/reload` will not reconnect it (the mcp_tools closure is frozen into every harness rebuild before the factory even exists). This quietly falsifies the 'write an extension, /reload, use it' loop the self-extension epic advertises.

**제안**: Split both issues along the cheap/expensive line rather than merging them. The cheap halves ride the EXISTING reload path and should be one piece of work: a persisted disable list honoured at load time with a Space toggle that says 'takes effect on /reload' (#25), plus a reload-time MCP re-scan (#63 half 2, already named as open residue in ADR-0204). The expensive halves — true hot enable/disable, which needs the activation/teardown subsystem ADR-0165 D5 named, and lazy `on_tool_call` blocked on schema-carrying ToolContribs — go to a separate issue that nobody has asked for.

### [16] Seams for controlling which tools exist

**이슈**: #60 #95 #93

**사용자 영향**: No user-facing loss today; these are parity and ergonomics for embedders. #60 is the only one a normal user touches (`--exclude-tools` to subtract from the default set, `--session-id` for deterministic scripting).

**제안**: Keep all three separate — they operate at three different layers and merging them would hide that. #60 is CLI-layer active-set filtering and is the only S with real parity backing (port pi's `validateSessionIdFlags` id-shape validation, not just the flag). #95 is an SDK-layer base swap (a named `base_tools_override`, phase-stub carry-forward, good-first-issue). #93 is a per-tool backend hook (`bash_spawn`, symmetric to the existing `user_bash`) and is the SAFE alternative to inverting built-ins-win precedence — say that in the title so nobody proposes the dangerous version again.

### [17] Engineering hygiene and measurement

**이슈**: #129 #141 #171

**사용자 영향**: No direct user impact; all three are about whether the team can trust its own signals. CI is green only because it never runs tests/cli and tests/tui in one process (#129). Every invocation pays 1.4-2.2s of import before doing anything, and no test would notice if the next merge doubled it (#141).

**제안**: Keep separate. #171 is mostly SHIPPED — 798 citations re-derived, `scripts/check_citations.py` and `tests/test_citation_drift.py` exist at HEAD — so close the repair and keep only the open convention question ('is `file.py:NNN` the right convention at all, vs `file.py::construct`'), which is an owner call. #129 is a registry-global leak and blocks ever relying on a single full-suite run. #141 is a lazy-import pass over exactly two modules (guardrail 493ms, mcp.adapter ~281ms) plus one asserting budget test; sequence it after the #23 onboarding work since both touch cli/entry.py.

### [18] Containers and product-definition questions

**이슈**: #3 #53 #54 #30 #47

**사용자 영향**: Internal tidiness only — but closing these carelessly is how asks disappear. Three of them were proposed for closure and all three were refuted with named orphan clauses.

**제안**: MERGE #30 and #47 — they are the same unanswered product question in two vocabularies ('should other agents/editors be able to drive aelix?'). Answer it ONCE, for both surfaces, and sequence any yes after aelix-server ships; note ADR-0201 D2 already declined ACP for the one wire that exists, and #96 Part 2 is the third face of the same question. Do NOT close #3, #53 or #54 as they stand — each is the sole remaining record of specific asks (#3: `/goal`, `/btw`, the peer-agent interop adapter; #53: #63's `/reload` MCP gap and #92's unread `unknown_flags`; #54: `model_update`, `tools_update`, `ctx.mode`, `getSystemPromptOptions`, pi #5673 — I grepped all open issues and each of those strings appears in exactly one place). File the orphans as children FIRST, then close the container the same day. #54 additionally needs re-pointing at the real range (pi is at v0.84.2; the epic still tracks v0.80.2).


---

## 클러스터 에이전트 요약 (원문)

> Of the 71, exactly two can be closed today: #7 (the marketplace definition question was answered and then implemented by #19/#32/#64/#65/#88/#89/#91/#145) and #147 (fixed and merged to main this morning as 9686a0f) — nine further closures were proposed and all nine were refuted (#3, #6, #16, #26, #52, #53, #54, #59, #69), every one by the same error of reading a partial fix as a whole one, and twice by quoting an owner comment the owner had already retracted or superseded. Eight more are not work at all but unanswered questions nobody can start (#30, #42, #43, #45, #47, #48, #61, #157), plus #86 which is business rather than backlog; answer them in one sitting or close them, because they are currently indistinguishable from work in any list. About a dozen are live but their bodies are WRONG and must be rewritten before anyone begins (#6, #16, #26, #27, #29, #37, #40, #43, #46, #52, #54, #59) — three of those name a blocker that no longer exists, and #29 alone carries three bullets that shipped and were never crossed off. That leaves roughly 47 genuinely unbuilt items, and they concentrate in five places: session durability, Windows, the extension API's dead sinks, provider adapters, and multi-agent. Do the session-data cluster first, in the order #137 -> #138 -> #139: #137 is the backlog's only reproducible data loss and it fires on the most ordinary thing a user does (two terminals on one session via `aelix --continue`), while #138 leaves every secret the agent has ever read sitting verbatim in ~/.aelix ready to ride out in a backup or an export — and each of the three needs one decision made before any code, which is exactly the kind of work that rots when postponed. Caveat you should know when reading this: the verdict list I was handed was truncated mid-#69, so settled verdicts for 36 of the 71 (#83 onward) were never delivered — I re-derived those from the issue bodies plus direct checks against HEAD (9686a0f), and every claim about them above is something I measured rather than inherited.
