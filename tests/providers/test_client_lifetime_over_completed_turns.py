"""A turn that COMPLETES NORMALLY must not leave its provider client alive (#174).

WHAT THE DEFECT ACTUALLY IS — the issue's own headline did not survive
measurement, so read this before trusting the title. #174 says every adapter
"builds its own SDK client per request and never closes it", accumulating "for
the life of the process". The second half is false: the per-request client is a
reference CYCLE, and a forced ``gc.collect()`` reclaims every leaked client and
every socket (15 live / 15 open -> 0 / 0). So this is a DETERMINISM defect, not
an FD-exhaustion defect — the release happens on the collector's schedule
instead of the turn's. The issue's "FDs 3 -> 23 over 15 turns" is likewise not a
constant: five independent probes on the unfixed build got +30, +15, +3, +7 and
+14, because with the cyclic collector running the count sawtooths. NOTHING IN
THIS FILE MAY BE REWRITTEN AGAINST A FIXED FD NUMBER; that gate is a coin flip.

Stated the way it reproduces: on the unfixed build each completed turn adds +1
never-closed client and +1 still-established server connection, inside a window
where nothing is collected.

=== THE FOUR THINGS THAT ARE LOAD-BEARING ===

1. ``gc.collect(); gc.disable()`` AROUND THE TURN LOOP. Not hygiene. With the
   collector running, the leak arm and the correct close-per-request arm do not
   separate — the collector reclaims the cycle mid-loop and the counters
   converge. ``gc.enable()`` lives in a ``finally`` so a failing assertion
   cannot leave the collector off for the rest of the suite.

2. THE SIGNAL IS SERVER-OBSERVED ESTABLISHED CONNECTIONS. Two obvious
   alternatives were measured and are traps:
     - LIVE CLIENT OBJECT COUNT is 15 in the leak arm, 15 in the correct
       close-per-request arm AND 15 in the client-reuse arm. A gate on it goes
       RED on the correct fix.
     - FD DELTA sawtooths (see above). It survives here only as a LOOSE
       corroborator, ``fd_delta < N``, never an equality and never a number
       lifted from the issue.

3. ``_Server.writers`` AND THE TEARDOWN THAT FORCE-CLOSES THEM. Inherited from
   ``tests/providers/test_stream_close_on_cancel.py``, where the same omission
   cost a 20-minute silent run: ``server.wait_closed()`` waits on exactly those
   handler tasks, so a FAILING case (adapter still holding its socket) HANGS in
   ``reader.readuntil`` instead of reporting RED. Do not "simplify" the list
   away.

4. THE ASSERTION IS ``established <= 1``, NOT ``== 0``. Measured arms: leak 15,
   close-per-request 0, client-reuse 1. Client reuse was separately measured to
   be a FUNCTIONAL REGRESSION here (stale ``X-Initiator``, dropped
   ``Copilot-Vision-Request``) and the owner chose close-per-request, but the
   gate must not silently prejudge a design the owner could still revisit.

=== WHY THERE ARE TWO SHAPES OF THE SAME TEST ===

``test_a_completed_turn_leaves_no_established_provider_connection`` drives the
real ``stream_*`` function against a loopback fake and patches NOTHING. It is
the purest form and it separates four arms cleanly. It CANNOT cover google, and
that is a measurement, not an omission:

    MEASURED, unfixed build, 15 completed turns, ``gc.disable()`` held:
      openai-completions      accepted 15  established 15  fd_delta +30
      openai-responses        accepted 15  established 15  fd_delta +30
      anthropic-messages      accepted 15  established 15  fd_delta +30
      openai-codex-responses  accepted 15  established  0  fd_delta  +1
      google-generative-ai    accepted 15  established  0  fd_delta  +2
      google-vertex           accepted 15  established  0  fd_delta  +2

    The google rows are NOT evidence that google is fixed. ``google.genai
    .Client`` is reclaimed by REFCOUNTING, not by the cyclic collector, so
    ``gc.disable()`` does not pin it: the client dies the moment the adapter
    generator's frame goes, and the socket goes with it. Proven by holding one
    extra reference to each client the adapter built — the same run then reports
    established 15 / fd_delta +30 with all 15 connection pools reporting
    ``is_closed == False``. The ownership bug is there; this instrument is blind
    to it.

``test_the_client_the_adapter_built_is_released_before_the_turn_returns`` is
that experiment made into a gate: a spy on the adapter's own client FACTORY
(delegating to the real one, returning the real object — no double, no fake)
keeps one strong reference per client, so reclamation cannot stand in for
release. It covers all six adapters, google included, and it is the arm that
would still be RED the day an SDK stops being cyclic and quietly turns the pure
test above green. ``assert len(built) == N`` is the rig control for it: a patch
aimed at the wrong symbol shows up as zero captures, not as a pass.

=== THE THREE CONTROLS, ALL EXECUTABLE ===

(a) INSTRUMENT — ``test_the_instrument_sees_a_deliberate_leak`` leaks N raw
    ``httpx.AsyncClient``s against the same server and REQUIRES established >= N
    and fd_delta >= N. Without it, "0 established" is indistinguishable from a
    counter that never moves. A gate whose detector has no positive control
    reports absence of evidence as evidence of absence.

(b) RIG — ``assert opts.client is None`` runs on the real options object handed
    to the adapter. It catches the measured false-green where a client is
    accidentally injected: the obvious ``accepted >= 1`` setup check passes in
    that case, because the injected client connects too.

(c) PURITY — ``gc.collect`` is spied and required to be called ZERO times inside
    the window. A stray ``gc.collect()`` dropped anywhere in an adapter
    otherwise satisfies every assertion here with the ownership bug untouched.

=== WHAT THIS FILE DELIBERATELY DOES NOT DO ===

No abort, anywhere. #174 is about turns that end normally, so every turn is
asserted to have ended in ``AssistantDoneEvent`` — a turn that errored proves
nothing about the premise. Abort/stream lifetime is #147's gate,
``tests/providers/test_stream_close_on_cancel.py``; do not merge the two.

No construct-only arm. Building clients without issuing requests costs ZERO
FDs — the cost is per CONNECTION — so a test that only constructs is a
guaranteed false green.

``openai-codex-responses`` is GREEN here on purpose and must stay green: it
already does close-per-request behind an ``owns_client`` flag
(``openai_codex_responses.py``). It is the discrimination control — on the
unfixed build this file reports five RED arms and that one GREEN, from
production code, in a single run. If codex ever goes RED, the discipline
regressed there.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gc
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.providers import anthropic as anthropic_adapter
from aelix_ai.providers import google_generative_ai as google_adapter
from aelix_ai.providers import google_vertex as vertex_adapter
from aelix_ai.providers import openai_codex_responses as codex_adapter
from aelix_ai.providers import openai_completions as completions_adapter
from aelix_ai.providers import openai_responses as responses_adapter
from aelix_ai.providers._anthropic_client import (
    create_async_client as create_anthropic_client,
)
from aelix_ai.providers._google_client import create_client as create_google_client
from aelix_ai.providers._google_client import create_vertex_client
from aelix_ai.providers._openai_client import (
    create_async_client as create_openai_client,
)
from aelix_ai.streaming import Context, Model, SimpleStreamOptions

# Turns per arm. 15 is the issue's own number; nothing below is scaled to it
# except the loose ``fd_delta < N`` corroborator and the instrument control.
N = 15

# How long the server is given to notice a peer that went away. The property is
# an ABSENCE claim on the fixed build, where the close is synchronous and the
# handler's ``readuntil`` raises within microseconds; a longer window would only
# slow the suite. On the unfixed build the poll runs to the end, so keep it
# short enough that six RED arms stay tolerable.
_SETTLE_S = 2.0


# === wire bodies: one COMPLETE keep-alive turn each ===================

def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def _event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\n" + _sse(payload)


# Cribbed from the #147 gate's ``_COMPLETIONS_BODY`` and extended with the
# terminal frames — that file parks mid-stream on purpose, this one must reach
# ``AssistantDoneEvent``.
_COMPLETIONS_BODY = [
    _sse(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [
                {"index": 0, "delta": {"content": "tok"}, "finish_reason": None}
            ],
        }
    ),
    _sse(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    ),
    "data: [DONE]\n\n",
]

_RESPONSES_BODY = [
    _event(
        "response.created",
        {
            "type": "response.created",
            "response": {"id": "resp_1", "status": "in_progress", "output": []},
        },
    ),
    _event(
        "response.output_text.delta",
        {
            "type": "response.output_text.delta",
            "item_id": "m1",
            "output_index": 0,
            "content_index": 0,
            "delta": "tok",
        },
    ),
    _event(
        "response.completed",
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "m1",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "tok"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    ),
]

_ANTHROPIC_BODY = [
    _event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "m",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
    ),
    _event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    ),
    _event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "tok"},
        },
    ),
    _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
    _event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
    ),
    _event("message_stop", {"type": "message_stop"}),
]

# google-genai speaks ``?alt=sse`` with CRLF frame boundaries.
_GOOGLE_BODY = [
    "data: "
    + json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "tok"}], "role": "model"}}]}
    )
    + "\r\n\r\n",
    "data: "
    + json.dumps(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": ""}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }
    )
    + "\r\n\r\n",
]


def _codex_access_token() -> str:
    """A JWT carrying only the one claim the codex adapter reads.

    ``openai_codex_responses`` raises before any socket is opened when
    ``chatgpt_account_id`` is missing, which would make the codex arm pass by
    never connecting — the exact false green ``accepted >= 1`` exists to catch.
    """

    def _b64(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return ".".join(
        [
            _b64({"alg": "none", "typ": "JWT"}),
            _b64({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_1"}}),
            "not-a-signature",
        ]
    )


# === the loopback provider ============================================

class _Server:
    """Serves one COMPLETE keep-alive turn per request and counts connections.

    The handler LOOPS over requests on the same socket rather than serving one
    and returning. That is what makes the client-reuse arm measurable: a reused
    client sends all N turns down one connection, so ``accepted`` stays 1 while
    ``requests`` reaches N. A one-shot handler would close after the first turn
    and report the reuse arm as a clean 0, i.e. it would fabricate a pass.
    """

    def __init__(self, body: list[str]) -> None:
        self._body = body
        self.accepted = 0
        self.gone = 0
        self.requests = 0
        # See point 3 of the module docstring: without this list a FAILING case
        # hangs in ``server.wait_closed()`` instead of reporting RED.
        self.writers: list[asyncio.StreamWriter] = []

    @property
    def established(self) -> int:
        """Connections accepted whose peer has not yet gone away."""

        return self.accepted - self.gone

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.accepted += 1
        self.writers.append(writer)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":")[1])
                if length:
                    await reader.readexactly(length)
                self.requests += 1
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                )
                for piece in self._body:
                    raw = piece.encode()
                    writer.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
                # Terminating chunk — the turn ENDS. Omitting it would leave
                # every arm mid-stream and this file would be measuring #147.
                writer.write(b"0\r\n\r\n")
                await writer.drain()
        except (
            asyncio.IncompleteReadError,
            ConnectionResetError,
            BrokenPipeError,
            asyncio.CancelledError,
        ):
            pass
        finally:
            self.gone += 1
            with contextlib.suppress(Exception):
                writer.close()


# === arm table ========================================================

@dataclass(frozen=True)
class _Arm:
    api: str
    provider: str
    body: list[str]
    stream_fn: Callable[..., Any]
    base_path: str
    api_key: str
    # Module + attribute of the client factory the adapter calls. The pinning
    # test wraps it; ``None`` would mean "cannot pin", and there is no such arm.
    factory_module: Any
    factory_attr: str
    # Builds a client a CALLER owns, the same way the adapter would build its
    # own — so "the adapter must not close it" is a like-for-like claim.
    make_caller_client: Callable[[str], Any]


def _arms() -> dict[str, _Arm]:
    codex_key = _codex_access_token()
    return {
        "openai-completions": _Arm(
            api="openai-completions",
            provider="openai",
            body=_COMPLETIONS_BODY,
            stream_fn=completions_adapter.stream_openai_completions,
            base_path="/v1",
            api_key="sk-not-a-real-key",
            factory_module=completions_adapter,
            factory_attr="create_async_client",
            make_caller_client=lambda url: create_openai_client(
                api_key="sk-not-a-real-key", base_url=url
            ),
        ),
        "openai-responses": _Arm(
            api="openai-responses",
            provider="openai",
            body=_RESPONSES_BODY,
            stream_fn=responses_adapter.stream_openai_responses,
            base_path="/v1",
            api_key="sk-not-a-real-key",
            factory_module=responses_adapter,
            factory_attr="create_async_client",
            make_caller_client=lambda url: create_openai_client(
                api_key="sk-not-a-real-key", base_url=url
            ),
        ),
        "anthropic-messages": _Arm(
            api="anthropic-messages",
            provider="anthropic",
            body=_ANTHROPIC_BODY,
            stream_fn=anthropic_adapter.stream_anthropic,
            base_path="",
            api_key="sk-ant-not-a-real-key",
            factory_module=anthropic_adapter,
            factory_attr="create_async_client",
            make_caller_client=lambda url: create_anthropic_client(
                api_key="sk-ant-not-a-real-key", base_url=url
            ),
        ),
        "google-generative-ai": _Arm(
            api="google-generative-ai",
            provider="google",
            body=_GOOGLE_BODY,
            stream_fn=google_adapter.stream_google,
            base_path="/v1beta",
            api_key="not-a-real-key",
            factory_module=google_adapter,
            factory_attr="create_client",
            make_caller_client=lambda url: create_google_client(
                api_key="not-a-real-key", base_url=url
            ),
        ),
        "google-vertex": _Arm(
            api="google-vertex",
            provider="google-vertex",
            body=_GOOGLE_BODY,
            stream_fn=vertex_adapter.stream_google_vertex,
            base_path="/v1",
            api_key="not-a-real-key",
            factory_module=vertex_adapter,
            factory_attr="create_vertex_client",
            make_caller_client=lambda url: create_vertex_client(
                api_key="not-a-real-key", base_url=url
            ),
        ),
        "openai-codex-responses": _Arm(
            api="openai-codex-responses",
            provider="openai-codex",
            body=_RESPONSES_BODY,
            stream_fn=codex_adapter.stream_openai_codex_responses,
            base_path="",
            api_key=codex_key,
            factory_module=codex_adapter,
            factory_attr="_create_codex_client",
            make_caller_client=lambda _url: httpx.AsyncClient(),
        ),
    }


ARMS = _arms()

# Arms the PURE (nothing patched) connection instrument can separate. The two
# google arms are excluded on measurement, not on taste — see the table in the
# module docstring. They are covered by the pinning test below, which is RED for
# them today.
CONNECTION_ARMS = [
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
    "openai-codex-responses",
]

ALL_ARMS = list(ARMS)


# === instrumentation ==================================================

def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


@contextlib.contextmanager
def _no_collection_window() -> Iterator[Callable[[], int]]:
    """Freeze the collector and count any call to ``gc.collect`` inside.

    Load-bearing twice over. The ``gc.disable()`` is what makes the arms
    separate at all (docstring point 1). The spy is control (c): the spy
    DELEGATES to the real ``gc.collect`` so behaviour is unchanged, and the
    caller asserts the count is zero — a stray collect anywhere in an adapter
    would otherwise satisfy every assertion here with the ownership bug intact.
    """

    calls = 0
    real_collect = gc.collect

    def spy(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        return real_collect(*args, **kwargs)

    # Clear the decks BEFORE the spy goes in, so this call is not counted.
    gc.collect()
    gc.disable()
    gc.collect = spy  # type: ignore[assignment]
    try:
        yield lambda: calls
    finally:
        gc.collect = real_collect  # type: ignore[assignment]
        gc.enable()


@contextlib.contextmanager
def _pinned_factory(arm: _Arm, built: list[Any]) -> Iterator[None]:
    """Hold one strong reference to every client the adapter builds.

    NOT A DOUBLE: the wrapper forwards ``*args, **kwargs`` to the real factory
    and returns its exact return value, so the adapter gets the same object it
    would have got. The only effect is that the client cannot be reclaimed while
    the measurement runs — which is what turns "was it released?" into a
    question about the ADAPTER instead of a question about CPython's
    reclamation policy (google's client is refcount-freed, so without this the
    google arms report a clean 0 while leaking; see the module docstring).
    """

    real = getattr(arm.factory_module, arm.factory_attr)

    def spy(*args: Any, **kwargs: Any) -> Any:
        client = real(*args, **kwargs)
        built.append(client)
        return client

    setattr(arm.factory_module, arm.factory_attr, spy)
    try:
        yield
    finally:
        setattr(arm.factory_module, arm.factory_attr, real)


def _context() -> Context:
    """A one-message context.

    Not decoration: the google adapters reject an empty ``Context`` with
    "contents are required." BEFORE opening a socket, which would make both
    google arms pass by never connecting.
    """

    return Context(messages=[UserMessage(content=[TextContent(text="hi")])])


@dataclass
class _Reading:
    completed: int
    accepted: int
    requests: int
    established: int
    fd_delta: int
    collect_calls: int
    client_was_none: bool
    built: int


async def _settle(server_obj: _Server, target: int) -> None:
    """Give the server a bounded chance to notice peers that went away."""

    deadline = asyncio.get_running_loop().time() + _SETTLE_S
    while server_obj.established > target:
        if asyncio.get_running_loop().time() >= deadline:
            return
        await asyncio.sleep(0.02)


async def _drive(
    arm: _Arm,
    *,
    pin_clients: bool,
    caller_client: Any = None,
) -> tuple[_Reading, _Server]:
    """Run N complete turns against a loopback fake and read the counters."""

    server_obj = _Server(arm.body)
    server = await asyncio.start_server(server_obj.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}{arm.base_path}"
    model = Model(api=arm.api, id="m", provider=arm.provider, base_url=base_url)
    opts = SimpleStreamOptions(api_key=arm.api_key, client=caller_client)
    ctx = _context()
    built: list[Any] = []

    completed = 0
    try:
        pin = (
            _pinned_factory(arm, built) if pin_clients else contextlib.nullcontext()
        )
        with pin, _no_collection_window() as collect_calls:
            fd_before = _fd_count()
            for _ in range(N):
                terminal: Any = None
                async for event in arm.stream_fn(model, ctx, opts):
                    terminal = event
                if type(terminal).__name__ == "AssistantDoneEvent":
                    completed += 1
            fd_after = _fd_count()
            await _settle(server_obj, target=1)
            reading = _Reading(
                completed=completed,
                accepted=server_obj.accepted,
                requests=server_obj.requests,
                established=server_obj.established,
                fd_delta=fd_after - fd_before,
                collect_calls=collect_calls(),
                client_was_none=opts.client is None,
                built=len(built),
            )
        return reading, server_obj
    finally:
        built.clear()
        for open_writer in server_obj.writers:
            with contextlib.suppress(Exception):
                open_writer.close()
        server.close()
        await server.wait_closed()
        # Outside the window on purpose: leaving 15 un-reclaimed clients per arm
        # behind would push the leak into whatever test runs next.
        gc.collect()


def _assert_setup(arm: _Arm, r: _Reading, *, expect_client_none: bool) -> None:
    """Everything that must hold before the property means anything."""

    assert r.completed == N, (
        f"{arm.api}: only {r.completed}/{N} turns reached AssistantDoneEvent — "
        "#174 is a claim about turns that COMPLETE NORMALLY, so a run with "
        "errored turns proves nothing either way"
    )
    assert r.accepted >= 1, (
        f"{arm.api}: the server accepted {r.accepted} connections — the adapter "
        "never reached the wire, so every count below is vacuous"
    )
    if expect_client_none:
        # CONTROL (b). ``accepted >= 1`` passes when a client is accidentally
        # injected, because the injected client connects too; this is the only
        # check that catches it.
        assert r.client_was_none, (
            f"{arm.api}: options.client was not None — this run measured a "
            "CALLER-supplied client, which the adapter is required NOT to "
            "close, so a clean result here would be meaningless"
        )
    # CONTROL (c).
    assert r.collect_calls == 0, (
        f"{arm.api}: gc.collect was called {r.collect_calls} times inside the "
        "measurement window — a collect makes the leak arm and the fixed arm "
        "indistinguishable, so this result cannot be trusted"
    )


@pytest.mark.parametrize("api", CONNECTION_ARMS)
async def test_a_completed_turn_leaves_no_established_provider_connection(
    api: str,
) -> None:
    """RED on the unfixed build for the three adapters that leak; GREEN for codex.

    SABOTAGE: delete the ``finally: if owns_client: await
    close_provider_client(client)`` from the adapter under test. The client then
    outlives the turn, its pooled connection stays established, and — with the
    collector frozen — nothing takes it back.

    ``openai-codex-responses`` is the discrimination control and is expected to
    pass here BEFORE the fix: it already closes per request behind
    ``owns_client``. If it fails, that discipline regressed.
    """

    arm = ARMS[api]
    reading, _server = await _drive(arm, pin_clients=False)
    _assert_setup(arm, reading, expect_client_none=True)

    # THE PROPERTY. ``<= 1`` not ``== 0``: measured arms are leak 15,
    # close-per-request 0, client-reuse 1. See docstring point 4.
    assert reading.established <= 1, (
        f"{api}: {reading.established} provider connections were still "
        f"established after {N} turns that all completed normally "
        f"(accepted={reading.accepted}, requests={reading.requests}, "
        f"fd_delta={reading.fd_delta:+d}). Each completed turn left behind a "
        "client the adapter built and never closed."
    )

    # CORROBORATOR ONLY — deliberately loose. The issue's "3 -> 23" did not
    # reproduce (+30/+15/+3/+7/+14 across five probes), so this may never become
    # an equality or a fixed number.
    assert reading.fd_delta < N, (
        f"{api}: file descriptors grew by {reading.fd_delta:+d} over {N} turns, "
        "which is the per-turn accumulation this gate exists to prevent"
    )


@pytest.mark.parametrize("api", ALL_ARMS)
async def test_the_client_the_adapter_built_is_released_before_the_turn_returns(
    api: str,
) -> None:
    """Same property, with reclamation removed as a confound. Covers google.

    The pure test above cannot see the google arms: ``google.genai.Client`` is
    freed by refcounting rather than by the cyclic collector, so it dies with
    the adapter's frame and takes its socket along even under ``gc.disable()``.
    Pinning one reference per built client asks the question the issue actually
    asks — did the ADAPTER release it, or did the runtime happen to.

    This is also the arm that stays honest if an SDK stops being cyclic: the
    pure test would go quietly green, this one would not.

    SABOTAGE: same as above. Additionally, replacing the adapter's
    ``close_provider_client(client)`` with a bare ``gc.collect()`` passes the
    pure test and fails this one — plus control (c).
    """

    arm = ARMS[api]
    reading, _server = await _drive(arm, pin_clients=True)
    _assert_setup(arm, reading, expect_client_none=True)

    # RIG CONTROL for the spy: a patch aimed at a symbol the adapter does not
    # call captures nothing, and "0 established" would then be an artifact of a
    # dead instrument rather than a result.
    assert reading.built == N, (
        f"{api}: the factory spy captured {reading.built} clients over {N} "
        f"turns — {arm.factory_module.__name__}.{arm.factory_attr} is not the "
        "symbol this adapter builds its client with, so this run measured "
        "nothing"
    )

    assert reading.established <= 1, (
        f"{api}: {reading.established} of the {reading.built} clients this "
        f"adapter built over {N} completed turns still hold an established "
        f"connection (fd_delta={reading.fd_delta:+d}). They are only released "
        "when the runtime gets round to reclaiming them."
    )
    assert reading.fd_delta < N, (
        f"{api}: file descriptors grew by {reading.fd_delta:+d} over {N} turns"
    )


async def test_the_instrument_sees_a_deliberate_leak() -> None:
    """CONTROL (a) — POSITIVE CONTROL FOR THE DETECTOR. Do not delete as trivial.

    Every other assertion in this file is an ABSENCE claim, and an absence claim
    against a broken counter is free. This leaks N raw ``httpx.AsyncClient``s
    against the same server, exactly the way the adapters do, and REQUIRES the
    counters to move. If this ever goes red, every green above is vacuous.
    """

    server_obj = _Server(_COMPLETIONS_BODY)
    server = await asyncio.start_server(server_obj.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    leaked: list[httpx.AsyncClient] = []
    try:
        with _no_collection_window() as collect_calls:
            fd_before = _fd_count()
            for _ in range(N):
                client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
                leaked.append(client)  # never closed — that is the point
                # A real request: construct-only costs ZERO fds, because the
                # cost is per CONNECTION, so a construct-only control would
                # itself be a false green.
                response = await client.post("/v1/chat/completions", json={})
                assert response.status_code == 200
            fd_after = _fd_count()
            calls = collect_calls()
        assert calls == 0
        assert server_obj.established >= N, (
            f"the instrument reports {server_obj.established} established "
            f"connections while {N} deliberately-leaked clients are held open — "
            "the detector is broken and every absence assertion in this file is "
            "vacuous"
        )
        assert fd_after - fd_before >= N, (
            f"the fd corroborator moved {fd_after - fd_before:+d} while {N} "
            "leaked clients were held open"
        )
    finally:
        for open_client in leaked:
            with contextlib.suppress(Exception):
                await open_client.aclose()
        for open_writer in server_obj.writers:
            with contextlib.suppress(Exception):
                open_writer.close()
        server.close()
        await server.wait_closed()


def _client_is_closed(client: Any) -> bool:
    """Read a client's closed flag, or REFUSE.

    Raises on a shape it does not know rather than returning ``False``. A
    permissive default here would report "still open" for a client it cannot
    inspect, i.e. it would turn the regression test below into a guaranteed
    pass.

    MEASURED that this is not vacuous — it flips False -> True on a real closed
    client for all five shapes in the arm table (openai, anthropic, google
    generative-ai, google vertex, raw httpx), and refuses ``object()``.
    """

    api_client = getattr(client, "_api_client", None)
    if api_client is not None:  # google.genai.Client
        pool = getattr(api_client, "_async_httpx_client", None)
        if pool is None:
            raise AssertionError(
                f"{type(client)!r} has no _async_httpx_client to inspect"
            )
        return bool(pool.is_closed)
    probe = getattr(client, "is_closed", None)
    if probe is None:
        raise AssertionError(f"no closed-probe known for {type(client)!r}")
    return bool(probe() if callable(probe) else probe)


@pytest.mark.parametrize("api", ALL_ARMS)
async def test_a_caller_supplied_client_survives_the_turn(api: str) -> None:
    """GREEN today and must STAY green — ``options.client`` is never ours to close.

    This is the only assertion that separates the ``owns_client`` discipline
    from a naive "close the client in a finally". An SDK embedder that hands us
    a client keeps using it after the turn; closing it breaks their next call,
    silently and one turn later.

    Asserted three ways because the internals probe alone is weak: N turns all
    complete, the client's own closed-flag is still False, and an (N+1)-th turn
    on the SAME client still completes — the last one is the property a user
    would actually notice.
    """

    arm = ARMS[api]
    server_obj = _Server(arm.body)
    server = await asyncio.start_server(server_obj.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}{arm.base_path}"
    caller_client = arm.make_caller_client(base_url)
    model = Model(api=arm.api, id="m", provider=arm.provider, base_url=base_url)
    opts = SimpleStreamOptions(api_key=arm.api_key, client=caller_client)
    ctx = _context()

    try:
        assert opts.client is caller_client, "the rig failed to inject the client"
        completed = 0
        for _ in range(N):
            terminal: Any = None
            async for event in arm.stream_fn(model, ctx, opts):
                terminal = event
            if type(terminal).__name__ == "AssistantDoneEvent":
                completed += 1
        assert completed == N, (
            f"{api}: only {completed}/{N} caller-client turns completed; the "
            "run says nothing about whether the client survived"
        )

        assert not _client_is_closed(caller_client), (
            f"{api}: the adapter closed a client it did not build — "
            "options.client belongs to the caller"
        )

        # The user-visible half: a closed client cannot serve another turn.
        terminal = None
        async for event in arm.stream_fn(model, ctx, opts):
            terminal = event
        assert type(terminal).__name__ == "AssistantDoneEvent", (
            f"{api}: the turn AFTER {N} turns on a caller-supplied client did "
            f"not complete (ended in {type(terminal).__name__}) — the adapter "
            "left the caller's client unusable"
        )
    finally:
        with contextlib.suppress(Exception):
            closer = getattr(getattr(caller_client, "aio", None), "aclose", None)
            if closer is not None:
                await closer()
            elif hasattr(caller_client, "aclose"):
                await caller_client.aclose()
            elif hasattr(caller_client, "close"):
                result = caller_client.close()
                if hasattr(result, "__await__"):
                    await result
        for open_writer in server_obj.writers:
            with contextlib.suppress(Exception):
                open_writer.close()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
