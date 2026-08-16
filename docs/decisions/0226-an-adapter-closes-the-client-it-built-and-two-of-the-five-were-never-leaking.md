# 0226. An adapter closes the client it built, and two of the five were never leaking

Status: Accepted (2026-08-16).
Date: 2026-08-16
Relates: ADR-0215 (the first #147 fix), ADR-0225 (the second, whose "found while measuring"
side-note became this issue — and whose number for it is corrected below), ADR-0213 (the
pyright gate), ADR-0224 (text-pinned citations).
GitHub: #174. Follow-up filed: shared-transport pooling (see "Rejected alternatives").
Pi: **read, at v0.79.9, which is NOT the pin.** ADR-0034 pins `v0.74.1` / `734e08e`; ADR-0178
records the pi repo going private. Every pi citation below is labelled 0.79.9 and may postdate
the pin.

## The issue's own framing did not survive measurement

Three of #174's load-bearing sentences are false, and stating that first matters more than the
fix, because two of them would have produced a worse design.

**"never closed … accumulates one client per provider request for the life of the process."**
False. The per-request `AsyncOpenAI` is a reference CYCLE, not an unreachable orphan: measured,
a forced `gc.collect()` takes 15 live clients and 15 established connections to 0 and 0. #174 is
a DETERMINISM defect — the release happens on the collector's schedule instead of the turn's —
not an FD-exhaustion defect. The distinction is not pedantry; it is the difference between "a
long session runs out of file descriptors" (it does not) and "a socket the user is done with
stays open for an unbounded and unpredictable time" (it does).

**"FDs 3 → 23 over 15 normal turns."** Not a reproducible constant. Five independent probes
measured +30, +15, +3, +7 and +14 for the same scenario. With the cyclic collector running the
count sawtooths. **ADR-0225 repeats this number** in its "found while measuring" note and it is
corrected here rather than rewritten there.

**"Reuse is the better shape for connection pooling and keep-alive."** False as a drop-in, and
this is the one that decided the design. Measured on the wire, driving the real
`create_async_client` and the real copilot header derivation against a loopback server that
recorded the bytes: with one client reused across two requests of a single agentic turn, request
2 sent `X-Initiator: user` where the correct value was `agent`, and dropped
`Copilot-Vision-Request: true` entirely. `openai_completions.py` states in-repo that without that
flag Copilot image requests are server-rejected. Reuse is a functional regression, not a
tidiness trade.

The mechanism is structural, so no amount of care in the cache would fix it: the values that
vary per request — the copilot dynamic headers derived from `context.messages`, the
session-affinity trio, anthropic's `Authorization: Bearer`, `timeout_ms`, `max_retries` — are all
CONSTRUCTOR arguments. And the issue's proposed cache key `(provider, base_url, auth)` is unsound
on its own terms: anthropic's copilot and oauth branches pass `api_key=""` and carry the
credential in a default header, so the auth is not in the `auth` component at all.

## Provenance: both options were aelix-original

pi 0.79.9 does exactly what aelix does — `createClient` per stream call, no cache, no close.
Verified in `pi-ai/dist/providers/openai-completions.js`, `openai-responses.js` and
`anthropic.js` (four `new Anthropic(...)` branches to aelix's three).

But the cost does not transfer. Measured under node v24.14.0 against pi's own `node_modules`:
constructing 100 clients (50 OpenAI + 50 Anthropic) costs **zero** file descriptors, all of them
share the identical global `fetch`, and **neither client type has a `close` method at all**.
"Never closed" is not even expressible upstream. pi gets pooling and keep-alive from a
process-global undici dispatcher it installs itself (`pi-coding-agent/dist/core/
http-dispatcher.js`, `undici.setGlobalDispatcher(...)`), never from reusing client objects. In
Python each `AsyncOpenAI`/`AsyncAnthropic` builds its own `httpx.AsyncClient` with its own pool
(`openai/_base_client.py`, `self._client = http_client or AsyncHttpxClientWrapper(...)`).

So aelix's per-request construction is a faithful port of a pi shape whose cost is Python-only.
Neither close-per-request nor reuse is parity restoration; both are aelix-original. What pi does
supply is an operating principle worth copying: its one provider resource that genuinely owns a
socket — the codex WebSocket session cache — is the one thing it caches AND tears down, via
`registerSessionResourceCleanup`. In Python the HTTP clients own sockets too, so by pi's own
principle they belong in a cleanup path.

## Decisions

1. **Close per request, guarded by ownership.** Each adapter that builds a client closes it in a
   `finally`. `owns_client = opts.client is None` keeps a caller-supplied `options.client`
   untouched — the same line `_stream_close.py` already draws for streams. This is not a new
   pattern: `openai_codex_responses.py` has shipped exactly this shape since #147, and it is the
   only adapter that was already green under the new gate.

2. **A second helper, `close_provider_client`, NOT a reuse of `close_provider_stream`.** The
   resolution order is `aio.aclose` → `aclose` → `close`, and it is measured, not defensive:

   | client | `aclose` | `close` | what actually closes it |
   |---|---|---|---|
   | `openai.AsyncOpenAI` | absent | coroutine | `close()` |
   | `anthropic.AsyncAnthropic` | absent | coroutine | `close()` |
   | `httpx.AsyncClient` | coroutine | absent | `aclose()` |
   | `google.genai.Client` | on `.aio` only | **sync** | `aio.aclose()` |

   google is why `aio.aclose` goes first. Its `Client` exposes BOTH a synchronous `close()` and
   an async `aio.aclose()`, and they close different transports: measured, `client.close()`
   leaves `_api_client._async_httpx_client.is_closed` at `False`, while `await
   client.aio.aclose()` flips it `True` and is idempotent. Reusing `close_provider_stream`, whose
   order is `("close", "aclose")`, would have matched google's sync `close`, returned satisfied,
   and left the async pool open — a silent half-fix wearing a fix's docstring.

3. **The two google adapters are fixed even though they were measured NOT to leak, and the code
   says so.** This is the decision most likely to be misread later. Measured on the unfixed
   build, 15 turns that complete normally, cyclic collector DISABLED:

   | adapter | clients built | still alive | connections established |
   |---|---|---|---|
   | openai-completions | 15 | **15** | **15** |
   | openai-responses | 15 | **15** | **15** |
   | anthropic-messages | 15 | **15** | **15** |
   | google-generative-ai | 15 | 0 | 1 |
   | google-vertex | 15 | 0 | 1 |
   | openai-codex-responses *(already fixed)* | 15 | 0 | 1 |

   google reads identically to the adapter that was already correct. Its `Client` is reclaimed by
   plain REFCOUNTING rather than by the cyclic collector, so it already dies with the adapter
   generator's frame and takes its socket along. It is closed anyway for two reasons that are not
   "leak fixed": the release becomes a property of this code rather than of CPython's refcounting
   (which PyPy does not promise), and the invariant "an adapter closes the client it built" then
   holds for five adapters instead of three, so the day a google SDK bump introduces a cycle
   there is already a gate watching. Both adapters carry this in a comment. **Do not let a later
   changelog promote it into a leak that was found and fixed.**

4. **anthropic's ownership is one test, not three flags.** The adapter has three construction
   arms (copilot, oauth, api-key) plus the `opts.client is not None` arm. `owns_client` is
   computed once from `opts.client is None` rather than set in each construction arm, because
   three separate assignments can drift out of sync with a fourth arm and one test cannot. The
   `finally` also covers the `except _AuthError: raise` path, which leaked a client on every
   401/403.

5. **The gate asserts server-observed connections inside a `gc.disable()` window — not the FD
   delta, and not the live-object count.** Both alternatives were measured to be traps. Live
   client objects number 15 in the leak arm, in the CORRECT close-per-request arm, and in the
   reuse arm — a gate asserting on it would go RED on the fix. (Confirmed after the fix landed:
   `ALIVE` stayed 15 while `established` went 15 → 1. The fix releases the resource, not the
   object.) The FD delta sawtooths under the automatic collector. The window is load-bearing, not
   hygiene: with the collector running the arms do not separate.

   The gate carries three controls, all executable: an instrument control that deliberately leaks
   N raw `httpx.AsyncClient`s and requires the counters to move; a rig control asserting
   `opts.client is None`, which is the only thing that catches the measured false-green where a
   real client is accidentally injected; and a purity control spying on `gc.collect` for zero
   calls, because a stray collection otherwise satisfies the gate with the ownership bug
   untouched. `openai-codex-responses` is parametrized in as a discrimination control: it was
   GREEN on the unfixed build while the others were RED, from production code rather than rig
   configuration.

   Threshold is `established <= 1`, not `== 0`, so the gate accepts both candidate designs and
   does not silently prejudge a decision the owner could revisit.

6. **The shipped example that taught the leak is fixed.** `examples/telnaut/telnaut.py` built an
   `AsyncOpenAI` per request and never closed it; `docs/guides/extension-authoring.md` and its
   wheel-bundled twin reproduce that block almost verbatim, and `register_api_adapter`'s
   docstring recommends the pattern. Neither close-per-request nor reuse reaches a
   caller-supplied client by design, so fixing the guidance is the only way it stops being wrong.

## What was measured, and what was not

- **Measured:** the six-adapter table above, before and after, with and without pinning a
  reference; the wire-level header freeze under reuse; the google sync-vs-async close split;
  `gc.collect()` taking 15/15 to 0/0; pi's zero-FD client construction under node.
- **Measured, then discarded as my own artifact:** an early weakref reading of `ALIVE=0` for
  every adapter, which appeared to contradict everything above. The count was taken AFTER the
  no-collection window closed. Re-measured inside the window it is 15/15/15/0/0/0. Recorded
  because the reading looked like a finding and was a methodology error.
- **NOT measured:** the reuse arm's steady-state connection count. `established <= 1` for reuse
  is taken from the investigation, not from an independent run. If reuse is ever revisited,
  re-measure rather than trusting the threshold.
- **NOT covered end-to-end:** anthropic's copilot and oauth construction arms. All three arms
  call the same factory, so the gate's spy captures any of them, but a fix that added ownership
  to only one arm would not be caught by this file. Decision 4 removes the failure mode by
  construction rather than by test.
- **NOT in scope:** #173, the google STREAM linger. That is a different resource from the
  client's pool, and `_stream_close.py` already documents two handles measured not to work on it.

## Rejected alternatives

- **Client reuse keyed by `(provider, base_url, auth)`** — the issue's own preference. Rejected
  on the measured Copilot header regression, and on the key being unsound for anthropic's
  `api_key=""` branches. It would first require migrating every per-request value off the
  constructor onto per-request `extra_headers`/`timeout` across five adapters.
- **A shared TRANSPORT under per-request clients** — one `httpx.AsyncClient` injected via
  `http_client=` into a fresh SDK client each turn. Measured working: 15 requests, 1 server
  connection, every per-request header correct. This is the structurally pi-faithful shape and
  the real answer to the latency question reuse was reaching for. Deferred, not rejected on
  merit: it shares connections across turns while #147 has only just shipped and #173 is open,
  and its teardown belongs in `aelix_agent_core/harness/core.py` — a kernel change. Filed as a
  follow-up.
- **Calling `gc.collect()` in the adapter** — makes the symptom go away while leaving ownership
  exactly as loose, and was measured to turn an FD-only gate green with the bug untouched.
- **Asserting on live client objects** — would have failed the correct fix. See decision 5.
- **`-W error::ResourceWarning`** — needs a global config change, fires on unrelated third-party
  code, and only reports at collection time, which is the very timing the defect is about.
