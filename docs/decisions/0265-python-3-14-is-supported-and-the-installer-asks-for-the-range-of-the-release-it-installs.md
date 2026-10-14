# 0265. Python 3.14 is supported, and the installer asks for the range of the release it installs

Status: Accepted (2026-10-14)
Date: 2026-10-14
Issue: #278 (this change), #262 (the crash), #263 (the installer flag), #192 (the
test matrix)
Supersedes in part: ADR-0241 — its fixed default `>=3.11,<3.14` ("Decision"),
"Widening is not a one-line edit", and the runtime guard "What this does NOT
close" proposed. Its flag, its empty-means-default rule, its post-conditions and
both of its amendments stand.
Amends: ADR-0047 — the `openai` dependency (`>=1.50,<2.0` there, `>=1.66,<2.0`
since #15) is `>=2.7.2,<3`.

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
on the wire under 1.109.1, 2.7.2 and 2.54.0. A client the caller supplies is
untouched. The agent loop's path, `stream_simple`, still refuses a keyless
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
  400 and 429, a refused connection) and dump the request (method, path, JSON
  body, the headers aelix sets) and every event or error text. The dumps are
  identical under openai 1.109.1 on 3.11 and 3.13, 2.7.2 on 3.13 and 3.14, and
  2.54.0 on 3.11, 3.13 and 3.14. Under 1.109.1 on 3.14 the three compat
  Responses scenarios end in #262's error instead.
  `tests/providers/test_openai_sdk_wire_278.py` holds every leg to the
  1.109.1 recording.
- **What openai 2.x changed that aelix can see.** The empty key (Decision 2;
  its wire measured as above).
  `prompt_cache_retention` became a native `responses.create` keyword in 2.8.0;
  aelix keeps relocating it into `extra_body` because the 2.7.2 floor does not
  have it, and the body is the same either way. From 2.51.0 the SDK's own
  retries honour a `Retry-After` of up to 120 s (it was 60 s) and stop retrying
  above that; aelix passes no `max_retries` by default, so the SDK's two retries
  apply before aelix's own retry sees the error. That is a behaviour change on
  rate-limited providers, read from the SDK source and not measured live; pi
  sets the SDK's retries to 0 and enforces `maxRetryDelayMs` itself, and porting
  that is a follow-up.

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
  whose own header is spelled `authorization`.** It builds with `"unused"` and
  the SDK keeps both keys; measured the same on 1.109.1 and 2.54.0, so it is
  older than this change and reachable from a `models.json` header (#416). Not
  fixed here.
- **After b3 is published, the empty-`AELIX_PYTHON` pass loses one witness.**
  The interpreter cannot tell an ignored `--python ""` from the default once
  3.14 is inside the default range; the request uv records in its receipt still
  can.
