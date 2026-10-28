# 0232. Upstream has no transport, so the catalog can only inherit it

Status: Accepted (2026-08-19); amended 2026-10-21 (#172, ADR-0264 §4.8 B4: the ladder moved to
`aelix_ai/catalog_inherit.py`, new rows get a derived thinking map, the generator runs in CI);
noted 2026-10-28 (#172: the catalog repository is live, ADR-0264 §10).
Date: 2026-08-19
Relates: ADR-0067 (the catalog and its shape contract), ADR-0230 §catalog (the coverage
measurement that opened this), ADR-0034 (the pi pin a regeneration would follow).
GitHub: #172.

The catalog had not been refreshed since it was ported. MEASURED against models.dev on
2026-08-19: of the models upstream lists with a 2026-04 release date we shipped 63%; of
the 2026-08 ones, **5%**. The owner named five they wanted — `grok-4.6`, `qwen3.8`,
`deepseek-v4-flash-0731`, `deepseek-v4-pro-0813`, `glm-5.3` — and none of them were
selectable on any provider.

422 models are added here, taking the catalog from 1005 to 1427. What follows is why
that is not simply "we ran the refresh".

## The finding that reframes the issue: models.dev has no transport

`models_generated.json` rows carry four fields that decide whether a request arrives:
`api`, `baseUrl`, `headers`, `compat`. **None of them exists anywhere in models.dev's
`api.json`.** Upstream serves metadata — cost, limits, modalities, `tool_call`,
`release_date` — and nothing else. The transport comes from pi's per-provider model
files, which is why #172 was never a download.

This changes what a wrong answer costs. A row with a wrong `api` is not a row with a
wrong number: it appears in `/model`, the user picks it, and the request fails at the
provider with an error that names nothing they can act on. **A model that is absent is
better than a model that cannot work.**

So `scripts/refresh_catalog.py` never invents a transport. It inherits one from a model
already in the catalog, or it refuses and reports. 76 of the 823 candidates were
refused on exactly that ground.

## Additive-only is a shape, not a discipline

`test_catalog_corrections_are_pinned.py` records that this file is a **corrected fork**:
41 hand edits over six commits, and a faithful regeneration from the pi pin reverts 19
of them while dropping 376 of 847 models. The script cannot revert one because it cannot
write a key that already exists — `merge()` asserts, and the assertion is tested.

Verified on the result rather than claimed: of the 1005 rows that were here before,
**0 changed value, 0 were dropped, and none moved position** (order is load-bearing —
`cycle_model` walks insertion order).

That check had to be done on parsed rows, because `git diff` reported **11209 deleted
lines** for a change that deleted nothing. The file is mostly `},` and `"cost": {`, so
git found a cheaper alignment. A line count cannot see this file.

## The one guess, and its score

Transport is inherited by a three-rung ladder: same family and same namespace → the
provider's sole wire → the provider's other family-less models. `audit_transport_rule`
scores it by hiding a row we ship and asking the rule to predict it.

Scored on the catalog **as it stood before this change**, which is the state the
decision was taken against — scoring it afterwards would be asking the rule about
rows it wrote itself:

| | answered | correct | of the misses, wrong about the wire |
|---|---|---|---|
| forward (hide the row *and its successors*) | 951 | 902 = **94.8%** | 11 = 98.8% wire-correct |
| leave-one-out | 981 | 873 = 89.0% | 5 = 99.5% wire-correct |

Two things had to be measured before those numbers meant anything:

**The scorer's first report was misleading in our favour's opposite direction.** It
printed only `api`, so eleven `compat` differences read as "wrong transport". Ten of
fourteen misses were `compat` alone — the rule was right about where to send every one
of those requests. Naming the disagreeing field is what made the next decision possible.

**That decision: the wire must be unanimous, the quirks need not be.** Requiring a
family to agree on all four fields refused models over `compat` alone, and that is
precisely what kept `glm-5.3` and `qwen3.8` out on the first pass — every sibling agreed
about the endpoint. Quirks now come from the newest sibling, which is why the audit has
a `forward` mode at all: plain leave-one-out asks the rule to predict that
`claude-3-haiku` does *not* need the `forceAdaptiveThinking` every recent Claude
carries, and scores it down for answering with the newest thing. That is the reverse of
the question being asked.

**One miss was a real defect, not an artifact.** Every `eu.`-prefixed Bedrock row was
being handed the `us-east-1` endpoint, because the region lives in the model id and the
family rule did not look at it. `_namespace` now separates a route (`eu`, `anthropic`,
`global`) from a version (`glm-5` of `glm-5.2`) on whether the leading dotted segment
contains a digit, checked against all 1427 ids.

## Residual risk, stated rather than resolved

The 11 forward misses are all one thing: **the first model of a new wire on a provider**.
Copilot's first `anthropic-messages` row, opencode's first `google-generative-ai` row.
When a provider adds a transport it has never used, this script will get that model
wrong — there is nothing to inherit from. It is why the run prints its additions and its
skips, and why a human reads them.

## Aliases are hand-written because overlap measures the wrong thing

Four providers we ship are filed upstream under another id. The temptation was a
similarity threshold, and MEASURED it would have been wrong: `openai-codex` overlaps
`opencode` **93%** and `azure-openai-responses` overlaps `openai` 79% — high because
resellers carry the same model *names*, not because they are the same service. Pointing
our ChatGPT-subscription transport at a reseller's list would have passed any threshold
worth setting.

So `UPSTREAM_IDS` is a table with a reason per row: `together`→`togetherai` (17 of 17
ids, a rename), `vercel-ai-gateway`→`vercel` (87%, next best 45%), and the two `zai`
providers to their coding-plan counterparts — our base URLs are literally
`api.z.ai/api/coding/paas/v4` and `open.bigmodel.cn/api/coding/paas/v4`, and three of our
six `zai` rows exist upstream only under plain `zai`, so pi already treats the pair as
one catalog. `glm-5.3` reaches the `zai` provider only through that entry.

`ant-ling`, `fireworks`, `kimi-coding`, `openai-codex` and `azure-openai-responses` stay
frozen. They have no counterpart worth the name.

## What is deliberately not added

401 candidates were skipped: **325 cannot call a tool** and 76 have no defensible
transport. The first is a product rule — this is a coding agent, and a model that cannot
call a tool cannot edit a file, so offering it sells a session that fails on its first
action.

## The Copilot conventions, and a defect they caused

A Copilot seat is a subscription and the cap is the seat's, not the model's. Upstream
disagrees with both: it prices every Copilot row and serves 1000000 for
`gemini-3.6-flash`. The added rows are therefore zeroed and normalised to the window
their wire already carries — 200000 on `anthropic-messages`, 128000 on
`openai-completions` — now gated over all 36 rows rather than the 27 that were
hand-written.

Lowering a context window without touching the output cap produced
`github-copilot/kimi-k3` with a **131072-token cap inside a 128000-token window**. Rows
are clamped, and the gate pins the two pre-existing offenders (both upstream's own bad
data) so a third fails.

## What this is NOT, and why #172 stays open

The issue asks for two mechanisms and this is one of them. Build-time generation is
here; the **runtime remote catalog overlay** is not — no `models-store.json`, no
ETag/304 revalidation, no four-hour freshness window, nothing that lets a model
become selectable without shipping a release. Its acceptance criterion is
untouched: *"A current model added to the remote catalog can become selectable
without an Aelix package release."*

That gap is worth more than it looks. MEASURED on this machine: `aelix
--list-models` reports 418 OpenRouter models against the 335 in the catalog. The
extra 83 come from a hand-maintained `~/.aelix/agent/models.json` — the very
workaround the issue lists as the reason it was filed. Someone is already paying
this cost by hand.

Two narrower limits, both deliberate:

- **One upstream source, not four.** The issue records pi's generator combining
  models.dev with OpenRouter's, Vercel's and NVIDIA's own `/models` endpoints.
  This reads models.dev alone. Those three providers are among the largest
  additions here, but by way of models.dev's mirror of them, which can lag.
- **It cannot add a provider.** Only models, and only to providers we already
  ship. A new provider needs auth, an env var, an adapter and a resolver default
  — none of which upstream metadata contains.

## Consequences

- `#172` stays OPEN for the runtime overlay. What closes here is the coverage:
  refreshing is `python scripts/refresh_catalog.py --fetch --apply` with a scored
  rule and a printed skip list, not a hand-edit.
- Nothing here runs in CI or at import. The catalog is committed data; the gate that
  guards it needs no network.
- `test_catalog_corrections_are_pinned.py` keeps its role unchanged — it pins values
  that differ from upstream, and this script is now the thing it is defending against.

## Amendment 2026-10-21: the ladder moved, new rows get a map, and this runs in CI

ADR-0264 (accepted 2026-10-20) publishes this generator's output from `handochan/aelix-catalog`
and inherits a published row's transport on the client with this ADR's ladder. Its §4.8 B4 row is
#172's second half, and it changes three things here; ADR-0264 §6 asked for an amendment, not a
supersession. Evidence: `.omc/probes/b4-d-live/172/` (commands in `commands.txt`).

- **The ladder lives in `aelix_ai/catalog_inherit.py`.** `transport_of`, `family_keys`,
  `namespace`, `released`, `wire`, `orphans`, `settle` and `resolve_transport` moved verbatim (one
  `list(...).index` hoisted out of a sort key), so the generator and the client run one
  implementation. The script keeps its old names (`_family_keys`, `_namespace`, ...) as the same
  function objects, and `tests/test_catalog_overlay.py` checks the identity. The ladder reads three
  upstream fields and nothing else: a new id's `family`, and each compared row's `family` and
  release date (`release_date`, else `last_updated`); `ladder_inputs()` puts a published row's
  `family` and `releaseDate` where it looks for them. MEASURED: `--audit` prints byte-identical
  output before and after the move over r7's models.dev (2026-10-17): forward 1407/1477 = 95.3% (12
  of 70 misses on the wire), leave-one-out 1428/1509 = 94.6%. Both scores are pinned over
  `tests/fixtures/hosted_catalog/r7-inputs.json.gz`, r7's inputs cut to what the generator reads
  (the cut changes neither the plan nor either audit, checked when it was written).
- **A new reasoning row gets a thinking map derived as pi derives one** (ADR-0264 §7 Q2, the owner's
  decision of 2026-10-16), in pi's order: Xiaomi's and Together's provider rule (hide minimal, low
  and medium), OpenRouter's public `reasoning.supported_efforts` for OpenRouter rows, models.dev's
  `reasoning_options` for a row whose inherited wire sends the level's own name (the three
  Responses APIs, OpenAI-format Chat Completions with effort support; never `anthropic-messages`),
  else none. `max` is kept (#433). An existing row's map is a key that exists, so additive-only
  still holds as a shape; #507 writes only an absent `off` into one (ADR-0264 §10,
  2026-10-26). `--fetch` downloads OpenRouter's list as well, and `--apply` refuses to
  run without one, because every new OpenRouter reasoning row would land without its map.
  MEASURED over r7's inputs, r7's counts come back: 265 rows, 230 reasoning, 38 OpenRouter maps,
  15 models.dev, 10 provider rule, 167 none, 39 naming `max`.
- **It runs in CI.** The Consequences above say "nothing here runs in CI"; aelix-ai's own CI now
  runs `tests/test_hosted_catalog_contract.py`, which runs the generator's plan, without network.
  It also runs on a schedule in the catalog repository, `handochan/aelix-catalog`, live since
  2026-10-27 (ADR-0264 §7 Q1, and §10 2026-10-28 for its first publication), through
  `scripts/publish_catalog.py build`, which plans over the committed catalog, applies it
  to a throwaway checkout, runs the pin test, this ADR's overlay test and ADR-0264's contract test
  against it, and publishes the rows without transport (ADR-0264 §4.2). A row reaches `main`, and
  a release, only through the fold-back (`RELEASING.md`), reviewed in a diff as every run before
  it was.

MEASURED live on 2026-10-21 23:52 KST (models.dev and OpenRouter fetched without a key; sha256 in
`inputs/live/sha256.txt`): the plan over `27fa21c5`'s 1,529 rows adds 288 and skips 465; of the new
rows, 41 take OpenRouter's map, 17 models.dev's, 13 a provider rule, 176 none, and 41 do not reason.
The forward audit over today's models.dev scores 1403/1476 = 95.1% (12 misses on the wire). This
commit does not apply them; the fold-back does, as its own reviewed commit.
