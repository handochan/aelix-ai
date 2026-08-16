# 0225. An aborted turn releases its connection, and the interrupt handover is caused, not timed

Status: Accepted (2026-08-16).
Date: 2026-08-16
Relates: ADR-0128 (Sprint 6h₂₀, the auto-retry loop this is the abort path for),
ADR-0130 (Sprint 6h₂₂, the countdown ticker whose clock is demoted here),
ADR-0215 (the #147 fix this finally puts a gate under), ADR-0221 (the 226 ms/frame
synchronous renderer cost that makes the timer race reachable), ADR-0224 (whose
`--fix` write path is repaired here — decision 9).
GitHub: #147, left open by ADR-0215 on one specific residual.
Pi: **read, at v0.79.9, which is NOT the pin.** See "Pi parity" below — the first draft of this
ADR carried the ADR-0219/0221 "no pi copy in this workspace" stance and that stance was wrong.

## Provenance

**Two of the four changes are pi parity restoration, not aelix-original, and the first draft of
this ADR said the opposite.** That correction is the most load-bearing thing in this document,
so it is stated before the decisions rather than in a footnote.

The premise was "the local pi copy is a partial fetch with no TUI sources". It is false: a
complete pi **0.79.9** is installed as an npm global at
`/usr/local/share/nvm/.../@earendil-works/pi-coding-agent/`, including `dist/core/
agent-session.js`, `dist/modes/interactive/interactive-mode.js` and the `pi-ai` provider
adapters — compiled but not minified, with the original comments intact. ADR-0219 and ADR-0221
declined to make parity claims on the same false premise and should be revisited on the same
evidence.

**Caveat that must travel with every citation here: 0.79.9 is not the pin.** ADR-0034 pins
`v0.74.1` / `734e08e`, and ADR-0178 records that the pi repo went private, so the pin-era source
was not available to check. Everything below is labelled 0.79.9 and may postdate the pin.

## Pi parity

| this ADR's change | pi 0.79.9 | verdict |
|---|---|---|
| `abort()` also wakes the retry sleep (decision 7) | `agent-session.js:1082-1086` — `async abort() { this.abortRetry(); this.agent.abort(); await this.agent.waitForIdle(); }` | **parity restoration.** pi calls `abortRetry()` FIRST, for the same reason. |
| the handover fires on `agent_start` (decision 5) | `interactive-mode.js:2212-2216` — under `case "agent_start"`, *"Restore main escape handler if retry handler is still active (retry success event fires later, but we need main handler now)"* | **parity restoration**, and pi's comment is the same argument this ADR reaches from measurement. |
| the adapters' `finally` (decision 3) | pi threads a real `AbortSignal` into the SDK call — `pi-ai/dist/providers/anthropic.js:336`, `openai-responses.js:91-95`, `openai-completions.js:88` | **divergence, forced by the language.** In Node an `AbortSignal` on `fetch` tears the transport down from any state. The Python SDKs take no such parameter; aelix's `SimpleStreamOptions.signal` is only read to classify the stop reason. `asyncio` cancellation is the substitute, and it covers one of the two states — hence the `finally` for the other. |

**ADR-0215's parity claim is superseded by this.** It recorded that pi restores the escape
handler only on `auto_retry_end` (`interactive-mode.ts:3345-3350`) and that aelix's earlier
handover was therefore "a deliberate aelix improvement". At 0.79.9 that is no longer true —
pi restores on `agent_start`. Whether pi changed between 734e08e and 0.79.9 or the original
reading was wrong is not established here.

## Context

### The sentence the issue was left open on

ADR-0215 fixed what #147 reported (Ctrl+C and Esc inert for the whole duration of an
auto-retry request) and deliberately left the issue open on one residual, quoted verbatim:

> the abort is *cooperative* — it does not forcibly tear down a socket the provider is holding
> open, so the underlying request lingers until the transport gives up. If that residual is in
> scope for this issue, it needs a transport-level cancel (an `httpx` cancel scope /
> `asyncio` task cancellation reaching the adapter).

That sentence had never been measured. It is **wrong as stated** and **right by accident**,
and the two halves have different fixes.

### Wrong as stated: the cancellation already reaches the transport

`AgentHarness.abort` has called `self._current_turn_task.cancel()` since Sprint 3c. The turn
task wraps `agent_loop`, the provider request is inside it, and no adapter catches
`CancelledError` (`except Exception` does not catch a `BaseException`). Below that,
`httpcore`'s HTTP/1.1 response iterator catches `BaseException` and closes the connection
under an `AsyncShieldCancellation`, so cancellation IS a transport-level cancel; no cancel
scope has to be added.

Measured end to end — real `AgentHarness`, real adapter, real SDK, real httpx, against a local
server that holds the response body open and reports when the client's socket goes:

| build path | abort during first request | abort during an in-flight RETRY request |
|---|---|---|
| injected `stream_fn` | socket torn down | socket torn down (2 requests served) |
| production `_make_stream_fn` | socket torn down | socket torn down (2 requests served) |

with a positive control (same rig, no abort) reporting the connection still open in every
arm. Verified live as well, through the real `python -m aelix_coding_agent` over a pty:
Ctrl+C during a held retry leaves `✖ Operation aborted` on the glass **and** the provider
observes the socket close.

### Right by accident: it does linger, in a state nobody had looked at

An adapter is an async generator, so it spends part of its life suspended at its own `yield`
while the consumer works. Cancel in THAT state and the `CancelledError` never reaches
httpcore's handler at all.

Measured, `openai-completions`, same local holding server:

| where the abort lands | result |
|---|---|
| parked in the network read | socket closed after **1.96 ms**, 0 GC passes |
| parked at the adapter's own `yield` | socket **open for the full 3 s window**, released only after an explicit `gc.collect()` |

That is "the underlying request lingers", reached by a route the issue did not name — and not
bounded the way that sentence assumes either: once the generator is abandoned no read is
pending, so the client's 600 s read timeout can never fire.

The cause is structural and visible in the source: `openai-completions` and `openai-responses`
hold the SDK stream as a bare local and iterate it with `async for`, with no `async with` and
no `finally`. `anthropic-messages` and `openai-codex-responses` hold theirs through a context
manager and release as they unwind.

**Which consumer can park there — and it is not today's TUI.** A first draft of this ADR
motivated the fix with ADR-0221's 226 ms/frame renderer. That is closer to the reason the
state is NOT reachable from the TUI: `tui/shell.py`'s `_on_agent_event` is a plain `def`, so
while it burns those 226 ms the event loop is blocked and nothing can be delivered; by the time
it returns, the adapter is back in the network read where httpcore closes the socket for us.
The state is reached by a consumer that AWAITS between events, which the harness explicitly
supports (`harness/core.py:2008` — `raw = listener(event)` then `if inspect.isawaitable(raw):
await raw`) and which async extension hooks, the `aelix_agents` RPC channel and any SDK
embedder use. So decision 3 is not closing a live TUI defect; it closes the one every awaiting
consumer already has, and stops the TUI acquiring it the day a subscriber becomes async.

### A third abort, in the gap between the two turn tasks

The retry loop runs OUTSIDE the turn task: each attempt is a fresh `_run`, and the backoff
sleep sits between two of them. So during the sleep `_current_turn_task` is `None` and
`abort()`'s cancel branch has nothing to cancel — and the next attempt's `_run` clears
`_abort_requested` on entry, discarding the flag it did set.

Measured against a local provider, counting requests:

| | requests served |
|---|---|
| no abort (positive control) | 2 |
| `abort()` during the backoff | **2 — identical, i.e. the abort was lost** |
| `abort_retry()` during the backoff | 1 |

The TUI does not reach this: during the countdown its interrupt is deliberately wired to
`abort_retry` for pi parity. The RPC `abort` command and any SDK embedder call `abort()`, and
for them "stop this turn" was silently ignored for the whole backoff.

This was surfaced by a recon agent's reading and then confirmed by the measurement above
rather than taken on its word — the same agent's claim that the google adapters release
deterministically turned out to be wrong when measured.

### And the #147 fix had no gate at all

Measured on `7815cc6`: delete the one line in `_tick_retry_countdown` that hands the interrupt
back, and `tests/tui/ tests/test_auto_retry.py tests/rpc/` still reports **1710 passed, 1
skipped**. The fix for a shipped, user-reported bug could be removed in silence.

### The handover was a race that happened to win

ADR-0215 timed the handover off the ticker's own `asyncio.sleep`. That is a SECOND clock for
the same nominal delay, started strictly after the harness started its first — the harness
emits `auto_retry_start` and only then begins its backoff, so the ticker's clock starts a
scheduling hop late. Both then race the retry request. Measured against the real harness:

| event-loop pressure | `turn_start` of the retry attempt … | verdict |
|---|---|---|
| idle | 2 ms AFTER the handover | handover wins |
| 150 ms/frame | 336 ms BEFORE the handover | turn visibly running, interrupt still the no-op |
| 400 ms/frame | 1237 ms BEFORE the handover | same, four times longer |

(`agent_start` and `turn_start` were measured landing in the same millisecond on this path; the
table is the same for either.)

In none of those did the interrupt fail to be restored before the request itself went out, so
this is not a re-opening of #147 — it is a window in which the retry TURN is running, the
spinner is up, and Ctrl+C is wired to a documented no-op. Ordering by luck is not ordering,
and the load that flips it is the renderer aelix already ships.

## Decision

1. **The residual as stated is retired, by measurement, not by argument.** Cancellation
   already reaches the transport. No httpx cancel scope is added, because none is needed.
2. **The property gets a gate, because it had none.** `tests/harness/
   test_abort_tears_down_the_provider_socket.py` drives a real harness against a local server
   that holds the body open and asserts SERVER-SIDE that the connection goes. Asserting on the
   harness would prove only that aelix stopped listening, which is exactly the distinction the
   issue turned on.
3. **Every adapter that lacks a context manager gets one `finally`.** `close_provider_stream`
   (`providers/_stream_close.py`) tries `close` then `aclose`, tolerates neither being present
   (the provider test fakes hand back plain async iterators) and never raises — it runs during
   `GeneratorExit`/`CancelledError` unwinding, where an exception would turn a clean abort into
   a spurious provider error in the transcript.
4. **Google is measured, unfixed, and deliberately untouched.** Both google adapters have the
   same linger and neither obvious handle closes it: `chunk_iter.aclose()` releases nothing
   (`google-genai 1.75.0` builds that generator in `_api_client.async_request_streamed`, whose
   `async for chunk in response` sits in no `finally`), and `client.aio.aclose()` on a client
   aelix created returns cleanly with the socket still up. A version that wired both was
   written, measured to change nothing, and reverted rather than shipped as fix-shaped code
   with a docstring claiming a fix. Follow-up work, recorded in the module and in #147.
5. **The interrupt handover is driven by `agent_start`, and the ticker's clock is demoted to a
   backstop.** `agent_start` is the causal signal — "the retry attempt has begun" — it already
   reaches `_on_agent_event`, and it is never earlier than the end of the backoff, so the
   pi-parity behaviour during the sleep (Esc cancels the retry, not the turn) is unchanged. It
   is pi's own choice of event, and it is the tighter of the two candidates: `turn_start`
   repeats for every tool round-trip inside an attempt where `agent_start` fires once. Measured
   on the retry path they land in the same millisecond.
6. **Both callers run one idempotent helper.** `_hand_back_retry_interrupt` is armed by
   `auto_retry_start` and disarmed by `auto_retry_end`, so an `agent_start` outside a retry
   sequence, or one from a sequence that already ended, cannot paint a "Retrying (N/M)" widget
   over a healthy turn.
7. **`abort()` wakes the retry sleep**, which is what pi's `abort()` does on its first line
   (`agent-session.js:1083`). Aborting a turn has to include "stop retrying it", and
   `abort_retry` is documented as a no-op when no retry is in flight, so this costs nothing on
   every other abort. The kernel edit is in `harness/core.py`, already in
   `_KERNEL_CHANGE_ALLOWLIST`; it adds no `aelix_agents` import and no spawn / consent /
   delegation surface. NOT ported: pi's `await this.agent.waitForIdle()` on the same line —
   `abort()` is called from a prompt-toolkit key handler here and awaiting idle inside it is a
   separate design question.
8. **That fix is gated with a REAL `stream_fn`.** Every pre-existing `prompt()`-level retry test
   monkeypatches `h._run`, so no real turn task is ever created and the abort path they
   exercise is not the one the product runs. A gate built the same way would have passed on the
   broken build.
9. **`check_citations.py --fix` splits lines the way the rest of the tool does.** Unrelated to
   the abort work, found by it, and fixed here because it CORRUPTS SOURCE FILES. The read path
   uses `split_lines` (`"\n"` only, with a docstring spelling out that `str.splitlines()` is
   wrong because Python also breaks on U+2028/U+2029/form feed/vertical tab/U+0085); the WRITE
   path used `splitlines(keepends=True)`. `cli/agent_context.py` carries exactly one U+2028 —
   the two disagree on it by one line (index 822 vs 823) — so relocating a citation there wrote
   `3123` onto the front of a line, left `shell.py:3063` untouched, and produced a file that no
   longer parsed. ADR-0224 sabotage-guarded three line-counting sites; this was a fourth, on the
   only path that writes.

## Consequences

- **The ticker's `except Exception: return` is no longer load-bearing.** A raising `set_widget`
  used to skip the handover entirely and leave the interrupt inert for the whole request — a
  smaller copy of the defect #147 reported. The causal path covers it.
- **`agent_start` now has a dispatch arm in the TUI shell**, where it previously had none. It
  is guarded by the armed state, and the guard has its own test with its own sabotage.
- **Two new tests bind a loopback socket.** Precedent: `tests/oauth/test_callback_server.py`.
  Both are hermetic (port 0, no network beyond loopback) and finish in single-digit seconds.
- **The suite's `finally` runs on the SUCCESS path too.** `AsyncStream.close()` on a
  fully-drained stream is a no-op — the SDK closes it when the body is read to completion —
  and the 661 provider tests pass unchanged.
- **`scripts/diag_retry_abort_live.py` is a new dev tool**, not a CI gate. It needs a pty and
  spawns a real agent process.

## What was verified, and what was not

- **8 sabotages, each named in the docstring of the test that answers it, each turning that
  test RED**: the causal handover, the timer backstop, the armed-state guard, the disarm on
  end, `turn_task.cancel()` in `AgentHarness.abort`, the new `abort_retry()` call in the same
  method, and the `finally` in each of the two adapters. The three socket/retry ones give the
  sharpest reading — **1 failed, N passed** in each, with the positive control staying green
  while only the target assertion flipped.
- **Three of these gates FLAKED, and the flake was mine.** A full-suite run that shared a
  2-core box with a seven-agent recon workflow reported 3 failures where the identical code had
  reported 8793 passed minutes earlier. Cause: I had bounded them with wall-clock sleeps — most
  sharply, `await asyncio.sleep(0.15)` to "land inside" a 600 ms backoff, which on a starved
  loop is already past the whole backoff, so the retry had fired and the test reported a defect
  that was not there. Rewritten to wait on the `auto_retry_start` EVENT (emitted immediately
  before the sleep begins, exact at any speed), and the socket gates' presence-assertions given
  a 20 s bound while their absence-asserting controls keep a short one — load cannot manufacture
  a socket close, so only the presence side could ever gain a false failure. Verified: 113
  passed × 3 consecutive rounds with 8 CPU burners on 2 cores, and all four sabotages still RED
  afterwards, so the wider bounds did not hollow the gates out.
- **ONE FLAKE SURVIVED AND IS NOT EXPLAINED.** After the rewrite,
  `test_aborting_a_turn_closes_the_connection_the_provider_is_holding` failed once more, inside
  a full-suite run that took 17 minutes against a normal 10. It has not reproduced since: 8/8
  in isolation under 4× CPU oversubscription, 2 clean full suites (8793 passed) before and
  after, and it passes in a 3663-test slice of everything that runs ahead of it. Rather than
  pick a plausible cause and call it fixed, the test now captures the states that could produce
  a false failure — request count, whether the stream started, whether the turn task was
  already done / cancelled / raising, connections accepted — and prints them in the assertion.
  Verified by sabotage that the diagnosis actually renders. If it recurs, it explains itself.
- **One gate hung instead of failing, and that was caught by running the sabotage.** The first
  version of the providers gate ended with `server.wait_closed()`, which waits for open
  connection handlers — and in the failing case the handler is parked in `reader.read(1)`
  precisely because the client did NOT release the socket. The one run that was supposed to
  report RED reported nothing for twenty minutes. Both socket files now force-close accepted
  connections before closing the server. A gate that hangs on failure is worse than no gate.
- **Positive controls are asserted, not assumed.** Both new socket files carry a test that runs
  the identical rig with no abort and requires the connection to STILL BE OPEN. A reading of
  "torn down" from an instrument that cannot produce "still open" is worth nothing, and this
  repo has already shipped one ADR paragraph that was wrong for exactly that reason (ADR-0221).
  The live probe needed the same correction mid-flight: its first version read `peer_gone`
  AFTER its own teardown had sent Ctrl+D and terminated the child, so every run — control
  included — reported a teardown. The observation is now snapshotted before teardown and the
  screen replayed only from bytes inside the window.
- **Live, through the real TUI** (`python -m aelix_coding_agent` in a 100×24 pty against a
  local provider reached through a temporary `models.json`): attempt 1 fails retryably,
  attempt 2 is held open, Ctrl+C during it leaves `✖ Operation aborted` and `✖ Retry failed:
  cancelled` on the glass, and the provider observes the socket close. The `--no-interrupt`
  control leaves the connection up and no abort notice.
- **The live probe does NOT discriminate decision 5, and passes identically on `7815cc6`.**
  Stated in the script's own docstring so a green run is never mistaken for covering it. The
  window the causal handover closes only opens under event-loop pressure, which cannot be
  injected into a child process from outside; the sabotage-verified headless tests are what
  pin it.
- **NOT measured:** `anthropic-messages` and `openai-codex-responses` in the yield-parked
  state. Their context managers are READ, not exercised — the claim here is a source reading.
- **NOT measured:** whether a lingering google connection has any consequence beyond the socket
  itself (billing, provider-side generation continuing). Only the socket was instrumented.
- **NOT reproduced here:** a recon agent measured the pre-fix linger at **>700 s ESTABLISHED
  with no FIN**, against my own >3 s window. The mechanism it gives (no pending read, so the
  read timeout cannot fire) is sound and matches the code, but the number is theirs, not mine.
- **Found while measuring, NOT fixed, and bigger than this residual:** the same agent measured
  15 NORMALLY COMPLETED turns taking file descriptors from 3 to 23 on both the fixed and the
  unfixed build — aelix constructs a fresh `AsyncOpenAI` (and therefore a fresh
  `httpx.AsyncClient`) per request and never closes it. That is an always-on leak, unrelated to
  abort, and it dominates the abort-only one this ADR fixes. Owner's call whether it gets its
  own issue.

  > **Correction (2026-08-16, ADR-0226).** That became #174 and is now fixed, but two claims in
  > the paragraph above are wrong. They are annotated rather than rewritten, per this directory's
  > convention that an ADR records what was believed when it was decided. **"3 to 23" is not
  > reproducible** — five independent probes measured +30, +15, +3, +7 and +14 for the same
  > scenario, because the count sawtooths while the cyclic collector runs; any gate written
  > against a fixed FD delta is a coin flip. **"Never closes it" overstates the exposure** — the
  > per-request client is a reference CYCLE, and a forced `gc.collect()` takes 15 live clients
  > and 15 established connections to 0 and 0, so the defect is the release's TIMING, not
  > unbounded accumulation. ADR-0226 carries the measured tables, including the finding that the
  > two google adapters were never leaking at all.

## Rejected alternatives

- **Adding an httpx cancel scope / passing an `AbortSignal` into the adapters**, as the issue
  suggested — measured unnecessary for the read-parked case (httpcore already does it) and
  insufficient for the yield-parked one (nothing is awaiting the transport to receive it).
- **Calling `gc.collect()` in the abort path** — makes the symptom go away while leaving every
  adapter's ownership as loose as before, and puts a full collection on the interactive path.
- **Letting the test call `gc.collect()` before asserting** — would turn the assertion into
  "the object is eventually reclaimable", which was already true on the broken build.
- **Wiring google anyway, since the helper never raises** — a call that changes nothing, next
  to a docstring saying it does, is worse than a documented gap. See decision 4.
- **Deleting the ticker's handover now that `turn_start` drives it** — the ticker still covers a
  backoff that expires with no turn behind it, and it costs one call to an idempotent helper.
- **Restoring the interrupt on `message_start`** — the abort close-out during an in-flight
  retry emits no `message_start` at all (recorded in #147 and pinned by the existing tests), so
  it is the one event in that sequence that cannot be relied on.
- **Restoring on `turn_start`** — works, and was the first implementation, but `agent_start` is
  what pi uses and fires once per attempt rather than once per tool round-trip.

## Out of scope

- The google adapters' linger (decision 4) and whatever handle the SDK would have to expose.
- `openai_codex_responses`, `anthropic`: no change, and no measurement in the yield-parked
  state.
- Whether an aborted request keeps costing tokens provider-side after the socket closes.
