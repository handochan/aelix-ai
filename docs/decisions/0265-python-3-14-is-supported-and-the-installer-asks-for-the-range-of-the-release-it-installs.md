# 0265. Python 3.14 is supported, and the installer asks for the range of the release it installs

Status: Accepted (2026-10-14); review round 2 (2026-10-14: Decisions 2, 8 and 9, the
inventory of what openai 2.x changed, "Review round 2" below)
Date: 2026-10-14
Issue: #278 (this change), #262 (the crash), #263 (the installer flag), #192 (the
test matrix)
Supersedes in part: ADR-0241 — its fixed default `>=3.11,<3.14` ("Decision"),
"Widening is not a one-line edit", and the runtime guard "What this does NOT
close" proposed. Its flag, its empty-means-default rule, its post-conditions and
both of its amendments stand.
Amends: ADR-0047 — the `openai` dependency (`>=1.50,<2.0` there, `>=1.66,<2.0`
since #15) is `>=2.7.2,<3`. ADR-0254 §2.3 and §3 — what the OpenAI client does
with no key and which variables it reads (dated note there). ADR-0128 "Not
covered" — the two openai adapters now read `max_retry_delay_ms` (dated note
there).

## Context

`uv tool install` picks the newest interpreter it can find, and on Python 3.14
every release up to `0.1.0b2` dies in the middle of a turn: their pinned
`openai<2.0` assigns `union.__discriminator__` onto a `typing.Union`, which 3.14
made slotted (#262). ADR-0241 kept the installers below 3.14 and recorded what
that could not cover: `uv tool install aelix@latest`, which `uv tool upgrade`
itself suggests, still lands on 3.14 and overwrites a working 3.13 install. It
proposed a startup guard for that hole.

Owner decision (2026-10-14, #278): support 3.14 instead of guarding it.

**Why a new ADR rather than a third amendment to ADR-0241.** This is a new
decision with an alternative weighed — the guard ADR-0241 proposed against
supporting the interpreter — and it moves a dependency that ADR-0047 owns, not
the installer alone. ADR-0241's principle stands unchanged: the installer, not
uv, chooses the interpreter, and an empty `AELIX_PYTHON` means the default. So
ADR-0241 is superseded only in the parts listed above, and keeps its
measurements and amendments.

## Decision

**1. `aelix-ai` pins `openai>=2.7.2,<3`.** The floor is the first openai that
does not write onto the union. Diffing the installed `openai/_models.py` of
2.7.1 and 2.7.2 shows that as the whole change: 2.7.2 caches the discriminator
in a `weakref.WeakKeyDictionary` instead of on the object. Measured on CPython
3.14.5 by calling `construct_type` with a Responses text delta that fails
validation: 1.109.1 and 2.7.1 raise #262's `AttributeError`; 2.7.2 returns a
`ResponseTextDeltaEvent`.

The ceiling is `<3`. openai 3.0.0 (2026-08-12) replaced its transport with
HTTPX2, stopped installing `httpx` and `certifi`, and verifies TLS against the
OS store by default. aelix's TLS diagnostics (the strict-mode remedy that reads
httpx exceptions off the cause chain, the truststore report) and every
`httpx.MockTransport` test in this repo are written against httpx 0.x. Moving
to 3.x is its own migration, not part of supporting an interpreter. `uv lock`
resolves 2.54.0, the last 2.x, and changes nothing else in the lock.

**2. A keyless openai-completions request keeps the wire it had.** From openai
2.34.0, `AsyncOpenAI(api_key="")` raises `OpenAIError: Missing credentials ...
set the OPENAI_API_KEY ...` (bisected: 2.33.0 accepts it, 2.34.0 refuses it),
and the SDK refuses to send a request with no `Authorization` unless the header
is omitted explicitly. `stream_openai_completions` passed `""` whenever no key
resolved, and on openai 1.x such a request went out with the request's own auth
header if it had one and no `Authorization` otherwise. Now the adapter builds
its own client with a placeholder key that never reaches the wire: a request
that carries its own `Authorization`, in any letter case, has it collapsed to
the SDK's spelling so it replaces the SDK's bearer (the SDK merges custom
headers by exact key, so `authorization` would otherwise ride beside
`Authorization: Bearer unused`); any other request omits the SDK's
`Authorization` per request. The four keyless cases (no header,
`Authorization`, `authorization`, `cf-aig-authorization`) put the same headers
on the wire under 1.109.1, 2.7.2 and 2.54.0, and so do an `Authorization` a
payload hook sets in `extra_headers` (it wins over the omission) and a client
the caller supplies (it keeps its own key: the settling is for the adapter's
own client only). One case is not 1.x's wire: headers that carry BOTH
spellings at once (`model.headers` `Authorization` and `options.headers`
`authorization`, say) went out as two lines on 1.x and go out as one now, the
value merged last - the `options.headers` one, which overrides the model's as
every other header does. The agent loop's path, `stream_simple`, still refuses a keyless
completions call (#416). pi's adapter throws `No API key` when there is neither
a key nor an auth header; aelix keeps sending, as it did before. The Responses
adapter already resolved `"unused"` beside an auth header or raised before
building a client, so it needed no change.

**3. The installers derive the default interpreter request from the release
they install.** `install.sh` and `install.ps1` read the version out of the
verified `SHA256SUMS` (Step 5, as before) and ask for `>=3.11,<3.15` when that
version is 0.1.0b3 or later, and `>=3.11,<3.14` otherwise. A version is
compared as numbers (`N.N.N` with an optional `aN`/`bN`/`rcN`), so `0.1.0b10`
is after `0.1.0b3`; anything the comparison cannot parse keeps the older
range, which every published release runs on. Failing closed costs 3.14;
failing open costs #262. `AELIX_PYTHON` still overrides, and an empty value
still means the default.

A wider constant alone would not do. `AELIX_VERSION` installs any tag, and
`0.1.0b2` pins `openai<2.0` forever, so `AELIX_VERSION=v0.1.0-beta.2` on a
machine with 3.14 would get exactly the crash ADR-0241 was written to stop.

**4. CI runs 3.14.** The test job's matrix is 3.11, 3.12, 3.13 and 3.14 on
`ubuntu-latest` and `windows-latest`, eight legs. `tests/test_ci_python_matrix.py`
now derives the minors from `install.sh`'s `py_supported`, so the matrix still
cannot drift from what the installer hands a user. The new check names are
`lint + test (ubuntu-latest, py3.14)` and `lint + test (windows-latest, py3.14)`;
requiring them in branch protection is the owner's step.

**5. The e2e jobs check the range of the release they installed.**
`assert-install-sh.sh` and `assert-install-ps1.ps1` derive the expected default
from the version they resolve themselves, by NAMING the two releases before
#278 (`0.1.0b1`, `0.1.0b2`) rather than comparing versions, so a wrong
comparison in an installer shows up as a disagreement. Both jobs gain a pass
that pins `AELIX_VERSION=v0.1.0-beta.2` with 3.14 on the machine and must land
below 3.14. Today that pin is also the newest release; once b3 is published the
default passes move to the wide range and this pass keeps the old one honest.

**6. No runtime guard.** With the pin moved there is nothing to guard from b3
on, and a guard cannot be shipped into b2 or b1: they are published.

**7. The packages say so, and `requires-python` keeps no ceiling.** The four
published `pyproject.toml` files list the 3.13 and 3.14 classifiers beside 3.11
and 3.12. `requires-python` stays `>=3.11` in all five: ADR-0241 measured that a
wheel's ceiling does not steer `uv tool install`, and a ceiling would only make
the next interpreter a packaging error before anyone has run it.

**8. The client aelix builds keeps neither variable openai 2.34.0 started
reading** (review round 2). From 2.34.0, `AsyncOpenAI.__init__` reads
`OPENAI_CUSTOM_HEADERS` and merges its lines into the client's headers AFTER
the auth header, and reads `OPENAI_ADMIN_KEY` as the client's admin key
(bisected with the empty-key refusal: 2.33.0 reads neither). The first made an
exported `Authorization` replace the key of every openai-compatible provider -
OpenRouter, a local server, any `models.json` provider - and sent every other
line along: the ADR-0254 leak class. `_openai_client.create_async_client` now
builds an `AelixAsyncOpenAI`, a subclass that keeps exactly the caller's
headers and no admin key after the SDK's constructor runs (as
`AelixAsyncAnthropic` does for `ANTHROPIC_CUSTOM_HEADERS`, ADR-0254 §2.1;
`os.environ` is not touched, and `copy()` / `with_options()` rebuild through the
class). Unlike `ANTHROPIC_CUSTOM_HEADERS`, which ADR-0254 keeps for provider
`anthropic`, neither is kept for provider `openai` either: openai 1.x read
neither, so this is the wire every provider had. The admin key is sent only by
the SDK's admin endpoints, which aelix never calls; it is dropped so that the
client holds no credential it did not resolve. `OPENAI_ORG_ID` and
`OPENAI_PROJECT_ID` (1.x read them too, sent as `OpenAI-Organization` /
`OpenAI-Project`) stay as ADR-0254 §3 records them. Every other variable the
2.54 package reads belongs to a client aelix never builds: Azure's, the
module-level client's, and the Bedrock provider's (`AWS_*`, read only when
`provider=` is passed). The shipped `examples/selfhosted` extension built a
bare `AsyncOpenAI` for its private CA; it builds through `create_async_client`
now (which passes an `http_client` through), and the extension-authoring
guide's snippet does the same - its `api_key=opts.api_key or ""` is refused
by 2.34+ ("Missing credentials"), so it says `or "unused"` as the example
does. A client an extension or embedder builds and hands in is its own, as
on 1.x.

**9. The SDK's own retries wait as they did on openai 1.x** (review round 2).
2.52.0 honours a `Retry-After` of up to 120 s on its own retries (1.x: up to
60 s, then its 0.5 s-doubling backoff) and does not retry at all above 120 s.
aelix leaves the SDK its default two retries per harness attempt, so on 2.54 a
429 with `Retry-After: 61` made `aelix -p` take 504 s instead of 20 s (the
cross review measured 515 s against 32 s), and `Retry-After: 300` dropped from
three requests per attempt to one. The
`AelixAsyncOpenAI` client honours a server-requested delay only up to the
request's `max_retry_delay_ms` and otherwise backs off as the SDK always did,
and it retries whatever the delay, as 1.x did. `max_retry_delay_ms` is pi's
`maxRetryDelayMs`: unset is 60000 ms, which is exactly 1.x's rule, and zero or
less is no limit; a value that is not a finite number counts as unset, as
#408's settings do. Handing every retry to the harness instead (pi's way: SDK
retries 0, `retryProviderRequest`) was not taken: it would change the request
count of every rate-limited turn and show retries the SDK used to absorb.
pi fails a request at once when the server asks for more than the limit; here
the SDK backs off instead, again because 1.x did. The settings key
`retry.provider.maxRetryDelayMs` still reaches no adapter - the harness puts
no `retry.provider.*` value into the request's options (#408's "not covered",
#470) - so in the CLI the limit is the default until #470 lands, and an
extension's `before_provider_request` patch or an embedder's stream options
are what set it today. The anthropic adapter keeps its SDK's own rule, which
is the same 60 s rule.

## What was measured

Probes and outputs are in `.omc/probes/278-live/impl/`.

- **A real turn on 3.14 with the old pin**, 529b997e (openai 1.109.1), CPython
  3.14.5: `aelix -p` with a tool round trip (`read`) against a local fake
  OpenAI-compatible server, isolated agent, settings and session directories.
  Responses events in the compat shape many servers send (no `sequence_number`,
  no `logprobs`, a `response` without `created_at`): exit 1,
  `'typing.Union' object has no attribute '__discriminator__' and no __dict__
  for setting new attributes`. The same server sending events that validate:
  exit 0. Chat completions: exit 0. 3.13.13: exit 0. The defect is latent
  exactly as ADR-0241 described.
- **The same turns on the new pin**, openai 2.54.0, 3.14.5: exit 0 for compat
  and strict Responses and for chat completions, each with the tool result
  quoted back. The same from an isolated `uv tool install --python 3.14
  --find-links <local wheels> aelix` (it took the local `0.1.0b3.dev278` build
  over PyPI's 0.1.0b2), and after `uv tool install --force --find-links <local
  wheels> aelix` typed with no `--python` over a 3.13 install, which moved the
  environment from 3.13.13 to 3.14.5 exactly as #278 describes, and now works
  there. The published 0.1.0b2 forced the same way also lands on 3.14.5, and its
  first compat Responses turn exits 1 with #262's message.
- **The installers, end to end, through the real uv.** `install.sh` and
  `assert-install-sh.sh` were run against two fake GitHub releases served by a
  `curl` shim (`v0.1.0-beta.3` with 0.1.0b3 wheels as the newest, and
  `v0.1.0-beta.2`), on a box with 3.11-3.14: the default install logged
  `interpreter request: >=3.11,<3.15` and landed on 3.14.5;
  `AELIX_VERSION=v0.1.0-beta.2` logged `>=3.11,<3.14` and landed on 3.13.13;
  `AELIX_PYTHON=` empty took the b3 default; `AELIX_PYTHON=3.12` landed on
  3.12. Every post-condition held on all four, and the pinned pass made no API
  call.
- **CI, run 38503137529** (the branch's first commit): all eight test legs
  green, including `ubuntu-latest, py3.14` on CPython 3.14.8 (16708 passed, 33
  skipped) and `windows-latest, py3.14` on 3.14.7 (16530 passed, 211 skipped).
  Both `install.sh e2e` jobs green against the published v0.1.0-beta.2: every
  default pass logged `interpreter request: >=3.11,<3.14`, and pass 6 (pinned,
  with 3.14 on the box and uv's unconstrained pick being 3.14) landed on 3.13
  on macOS and 3.11 on Ubuntu. On both Windows hosts the first two passes
  derived `>=3.11,<3.14` for 0.1.0b2 too, so the PowerShell derivation runs on
  Windows PowerShell 5.1 and pwsh. The Windows pinned pass stopped at its
  premise: `uv python find`, run in the checkout, answered the repository's
  `.python-version` (3.12.10) instead of the 3.14.8 it had just installed. The
  step now runs in `runner.temp`, as the POSIX job's already did.
- **The wire did not change.** Thirteen scenarios drive both adapters through
  the real SDK and `httpx.MockTransport` (text, tool calls with history,
  reasoning, `prompt_cache_retention`/session headers on api.openai.com, HTTP
  400 and 429, a refused connection) and dump the request and every event or
  error text. The dumps are identical under openai 1.109.1 on 3.11 and 3.13,
  2.7.2 on 3.13 and 3.14, and 2.54.0 on 3.11, 3.13 and 3.14. Under 1.109.1 on
  3.14 the three compat Responses scenarios end in #262's error instead.
  `tests/providers/test_openai_sdk_wire_278.py` holds every leg to the
  1.109.1 recording. That first recording handed each scenario a client, so
  the client the adapter builds - its headers, and what it reads from the
  environment - never reached it, and it compared only nine named headers.
  Review round 2 re-recorded it on 529b997e (1.109.1, 3.13.13) through the
  adapters' own client, with every header but the SDK's version stamps, and
  added twelve scenarios (25 in all): the 2.34 variables exported (Decision
  8), the keyless cases of Decision 2 (no header, each spelling of one, a
  hook's `extra_headers`, a caller-supplied client) and the Responses
  adapter's keyless cases. The retry wait is not a wire
  shape; `tests/providers/test_openai_sdk_retry_wait_278.py` holds it to what
  1.109.1 measured (Decision 9).
- **What openai 2.x changed that aelix can see**, each with its resolution:
  - 2.34.0 refuses an empty key and a request with no `Authorization` - the
    keyless completions call settles its own auth (Decision 2).
  - 2.34.0 reads `OPENAI_CUSTOM_HEADERS` (merged over the auth header) and
    `OPENAI_ADMIN_KEY` in the constructor - the client aelix builds keeps
    neither (Decision 8). The first round's inventory missed both: it read the
    empty-key refusal out of the same 2.34.0 constructor and not the lines
    around it.
  - 2.52.0 (not 2.51.0, as the first round wrote: 2.51.0 has no
    `MAX_RETRY_AFTER_DELAY`; the upstream changelog lists "honor Retry-After
    delays up to two minutes" under 2.52.0) honours a `Retry-After` of up to
    120 s on its own retries and stops retrying above that - the client aelix
    builds waits as 1.x did (Decision 9). The first round read this from the
    source and did not measure it; review round 2 did (below).
  - `prompt_cache_retention` became a native `responses.create` keyword in
    2.8.0; aelix keeps relocating it into `extra_body` because the 2.7.2 floor
    does not have it, and the body is the same either way.

## What this does NOT close

- **Releases up to `0.1.0b2` still crash on 3.14** when installed by hand
  (`uv tool install aelix==0.1.0b2`, or `aelix@latest` while b2 is the newest
  release). Users get 3.14 support from `0.1.0b3`, so the README keeps telling
  them to upgrade through the install line until then.
- **`anthropic` has the same defect below 0.72.1.** Measured the same way on
  3.14.5: `construct_type` over a `RawMessageStreamEvent` that fails validation
  raises #262's `AttributeError` on 0.72.0 and returns the event on 0.72.1. The
  lock has 0.102.0 and any fresh resolve takes a newer one, but `aelix-ai`
  declares `anthropic>=0.40`. The floor is not raised here because 0.72.1 is
  not a usable floor either: 25 rows of `test_anthropic_no_env_credential_374.py`
  fail on it (`ANTHROPIC_CUSTOM_HEADERS` handling arrived later), so the real
  floor needs its own bisection. `google-genai` 1.52, the declared floor, has no
  such write.
- **The Responses adapter sends two `Authorization` lines for a keyless request
  whose own header is spelled `authorization`, and `Authorization: Bearer
  unused` beside a `cf-aig-authorization`.** It builds with `"unused"` and the
  SDK keeps both keys; measured the same on 1.109.1 and 2.54.0 (the
  `resp-keyless-*` scenarios record both), so it is older than this change and
  reachable from a `models.json` header (#416). Not fixed here.
- **`retry.provider.*` in `settings.json` still reaches no adapter** (#470). The
  openai adapters read `max_retry_delay_ms` from the request's options
  (Decision 9), but the harness never fills it from the settings, so in the CLI
  it is the 60 s default. The anthropic adapter does not read it at all; its
  SDK's own rule is the same 60 s.
- **After b3 is published, the empty-`AELIX_PYTHON` pass loses one witness.**
  The interpreter cannot tell an ignored `--python ""` from the default once
  3.14 is inside the default range; the request uv records in its receipt still
  can.

## Review round 2 (2026-10-14)

Verify (FAIL, 4 blocking) and an independent cross review (5 findings, one
security) on `e3744bf1`; kits in `.omc/probes/278-live/{verify,cross-claude}/`,
this round's in `.omc/probes/278-live/r2/`. Each item was reproduced on
`e3744bf1` first.

1. **`OPENAI_CUSTOM_HEADERS` replaced every openai-compatible provider's key**
   (verify blocking 1, cross F1, security). Real `aelix -p`, a `models.json`
   provider with its own fake key, the variable (an `Authorization` and a second
   line), `OPENAI_ADMIN_KEY` and `OPENAI_API_KEY` exported
   (`live_env_headers.out`): 529b997e (1.109.1) sends the provider's key and
   nothing else; `e3744bf1` sends the variable's `Authorization` and its second
   line, on completions and Responses; this change, on 3.12.13 and 3.14.5,
   sends the provider's key and nothing else. Fixed by Decision 8. Rows: the
   golden's three `*-env-exported` scenarios and seven rows in
   `test_anthropic_no_env_credential_374.py` (given key on both adapters,
   three keyless shapes, the factory and its copies, the shipped
   `examples/selfhosted` client), red on `e3744bf1` (3 and 7). The example
   was not in either review's findings; the sweep for clients built with the
   SDK directly found it.
2. **The SDK's retry wait** (cross F2). Real `aelix -p`, default settings, a
   server answering 429 with `Retry-After` (`live_429_*.out`):

   | `Retry-After` | 529b997e | `e3744bf1` | this change, 3.12 | this change, 3.14 |
   | --- | --- | --- | --- | --- |
   | 61 | 20 s, 12 requests | 504 s, 12 | 20 s, 12 | 21 s, 12 |
   | 300 | 20 s, 12 | 15 s, 4 | 21 s, 12 | 21 s, 12 |

   In-process (`q1_before.out`, `q1_after.out`; the SDK's sleeps recorded):
   61 and 300 give two backoff waits under 1 s and three requests on 1.109.1
   and here, `[61, 61]` / one request on `e3744bf1`. Fixed by Decision 9; rows
   in `test_openai_sdk_retry_wait_278.py`, whose 12 default rows pass on
   529b997e and fail 8 on `e3744bf1` (the 4 that pass are the 30 s / 60 s
   controls).
3. **Text** (verify blocking 3 and 4, cross F3): CONTRIBUTING.md's CI line; the
   120 s release is 2.52.0; the 374 test's docstring named a function that does
   not exist; ADR-0254 §2.3, its sweep table and §3 (dated notes); the
   CHANGELOG's "unchanged" now says what is and the one difference; the
   inventory above; Decision 2 and `_keyless_client_auth` state the
   both-spellings case; the golden's docstring no longer claims headers it did
   not record (cross F3d: every scenario was handed a client).
4. **Unpinned contracts** (verify blocking 2, cross F5). Rows for a
   caller-supplied client (S07), a hook's `extra_headers` (S08), both spellings
   (last wins), and four installer versions (`0.1.1a1`, `0.1.1b1`, `0.2.0b1`,
   `1.0.0b1`). The golden now also records the keyless and Responses keyless
   cases from 1.109.1 (25 scenarios).

**Sabotage** (`sabotage_r2.out`, a throwaway worktree, `tests/providers`,
`tests/oauth` and the installer parity file, each mutant alone, the file
restored and its md5 checked after each): SABOTAGE_SUMMARY.

