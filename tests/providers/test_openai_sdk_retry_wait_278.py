"""#278 review round 2 — the openai SDK's own retries wait as they did on 1.x.

openai 2.52.0 honours a ``Retry-After`` of up to 120 s on the SDK's own retries
(1.x: up to 60 s, above that its 0.5 s-doubling backoff) and does not retry at
all above 120 s. aelix leaves the SDK its default two retries, so on 2.54 a 429
with ``Retry-After: 61`` made ``aelix -p`` take 515 s instead of 32 s (four
harness attempts x two 61 s SDK waits), and ``Retry-After: 300`` dropped the SDK
from three requests to one (``.omc/probes/278-live/cross-claude/out/p2_*``,
``p3_*``).

The client ``_openai_client.create_async_client`` builds now honours a
server-requested delay only up to the request's ``max_retry_delay_ms`` (pi's
``maxRetryDelayMs``: unset is 60000 ms, zero or less is no limit) and backs off
as the SDK always did above it, and it retries whatever the delay, as 1.x did.

Every row drives an adapter through the REAL SDK: a 429 from
``httpx.MockTransport`` and ``anyio.sleep`` in ``openai._base_client`` recording
the wait instead of sleeping. Each row's wait is what 529b997e (openai 1.109.1)
measured for the same input (``.omc/probes/278-live/r2/q1_before.out``), apart
from the rows that set ``max_retry_delay_ms``, which 1.x ignored.
"""

from __future__ import annotations

import math
import os
from typing import Any

import httpx
import openai._base_client as sdk_base_client
import pytest
from aelix_ai import Context, SimpleStreamOptions
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.providers import _openai_client
from aelix_ai.providers._openai_client import create_async_client, retry_after_cap_seconds
from aelix_ai.providers.openai_completions import stream_openai_completions
from aelix_ai.providers.openai_responses import stream_openai_responses
from aelix_ai.streaming import Model


class _Sdk:
    """What the SDK sent and how long it would have waited between tries."""

    def __init__(self) -> None:
        self.requests = 0
        self.sleeps: list[float] = []
        self.status = 429
        self.headers: dict[str, str] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(
            self.status,
            headers={"content-type": "application/json", **self.headers},
            json={"error": {"message": "slow down", "type": "rate_limit"}},
        )


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> _Sdk:
    rec = _Sdk()
    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(rec.handler)
        real_init(self, *args, **kwargs)

    real_anyio = sdk_base_client.anyio

    class _Anyio:
        def __getattr__(self, name: str) -> Any:
            return getattr(real_anyio, name)

        async def sleep(self, seconds: float) -> None:
            rec.sleeps.append(seconds)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    monkeypatch.setattr(sdk_base_client, "anyio", _Anyio())
    for name in [name for name in os.environ if name.startswith("OPENAI_")]:
        monkeypatch.delenv(name)
    return rec


def _model(api: str) -> Model:
    return Model(id="m", name="m", api=api, provider="fakeor", base_url="http://gw.invalid/v1")


async def _turn(api: str, **opts: Any) -> None:
    fn = stream_openai_completions if api == "openai-completions" else stream_openai_responses
    ctx = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    async for _ in fn(_model(api), ctx, SimpleStreamOptions(api_key="sk-provider-fake", **opts)):
        pass


def _backoff(sleeps: list[float]) -> bool:
    """The SDK's own backoff for its two default retries: 0.5 s then 1 s, each
    with up to 25 % jitter off (``INITIAL_RETRY_DELAY`` 0.5, x2 per retry)."""

    return (
        len(sleeps) == 2
        and 0.375 <= sleeps[0] <= 0.5
        and 0.75 <= sleeps[1] <= 1.0
    )


_APIS = ("openai-completions", "openai-responses")


@pytest.mark.parametrize("api", _APIS)
@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("retry-after", "61"),
        ("retry-after", "300"),
        ("retry-after-ms", "61000"),
        ("retry-after-ms", "300000"),
    ],
)
async def test_a_wait_longer_than_a_minute_gets_the_backoff_and_the_retries(
    sdk: _Sdk, api: str, header: str, value: str
) -> None:
    """529b997e: three requests about 1.5 s apart in all. 2.54 alone: two
    61 s waits, or (above 120 s) one request and no retry."""

    sdk.headers = {header: value}
    await _turn(api)
    assert sdk.requests == 3
    assert _backoff(sdk.sleeps), sdk.sleeps


@pytest.mark.parametrize("api", _APIS)
@pytest.mark.parametrize("seconds", ["30", "60"])
async def test_a_wait_of_up_to_a_minute_is_honoured(sdk: _Sdk, api: str, seconds: str) -> None:
    """The control: what the server asks for, as on 1.x."""

    sdk.headers = {"retry-after": seconds}
    await _turn(api)
    assert sdk.requests == 3
    assert sdk.sleeps == [float(seconds), float(seconds)]


@pytest.mark.parametrize("api", _APIS)
@pytest.mark.parametrize(
    ("max_retry_delay_ms", "retry_after", "honoured"),
    [
        (10_000, "30", False),
        (120_000, "61", True),
        (300_000, "300", True),
        (0, "300", True),
        (-1, "300", True),
    ],
)
async def test_max_retry_delay_ms_is_the_limit(
    sdk: _Sdk, api: str, max_retry_delay_ms: int, retry_after: str, honoured: bool
) -> None:
    """pi ``maxRetryDelayMs``: the longest server-requested wait; 0 or less is
    no limit. Above it the SDK backs off and still retries."""

    sdk.headers = {"retry-after": retry_after}
    await _turn(api, max_retry_delay_ms=max_retry_delay_ms)
    assert sdk.requests == 3
    if honoured:
        assert sdk.sleeps == [float(retry_after), float(retry_after)]
    else:
        assert _backoff(sdk.sleeps), sdk.sleeps


@pytest.mark.parametrize("api", _APIS)
@pytest.mark.parametrize("bad", ["60000", True, math.nan, math.inf])
async def test_a_max_retry_delay_ms_that_is_not_a_number_is_the_default(
    sdk: _Sdk, api: str, bad: Any
) -> None:
    sdk.headers = {"retry-after": "61"}
    await _turn(api, max_retry_delay_ms=bad)
    assert _backoff(sdk.sleeps), sdk.sleeps
    sdk.sleeps.clear()
    sdk.headers = {"retry-after": "59"}
    await _turn(api, max_retry_delay_ms=bad)
    assert sdk.sleeps == [59.0, 59.0]


@pytest.mark.parametrize("api", _APIS)
async def test_the_rest_of_the_sdk_retry_rule_is_kept(sdk: _Sdk, api: str) -> None:
    """``x-should-retry: false`` still wins, a 400 is not retried, and the
    caller's ``max_retries`` still counts."""

    sdk.headers = {"retry-after": "300", "x-should-retry": "false"}
    await _turn(api)
    assert (sdk.requests, sdk.sleeps) == (1, [])

    sdk.requests, sdk.status, sdk.headers = 0, 400, {"retry-after": "1"}
    await _turn(api)
    assert (sdk.requests, sdk.sleeps) == (1, [])

    sdk.requests, sdk.status, sdk.headers = 0, 429, {"retry-after": "300"}
    await _turn(api, max_retries=1)
    assert sdk.requests == 2 and len(sdk.sleeps) == 1 and sdk.sleeps[0] <= 0.5


def test_retry_after_cap_seconds() -> None:
    assert retry_after_cap_seconds(None) == 60.0
    assert retry_after_cap_seconds(_openai_client.DEFAULT_MAX_RETRY_DELAY_MS) == 60.0
    assert retry_after_cap_seconds(1500) == 1.5
    assert retry_after_cap_seconds(0) is None
    assert retry_after_cap_seconds(-5) is None
    for bad in ("1000", True, False, math.nan, math.inf, -math.inf, [1]):
        assert retry_after_cap_seconds(bad) == 60.0, bad


def test_a_copy_keeps_the_limit(sdk: _Sdk) -> None:
    client: Any = create_async_client(api_key="sk-provider-fake", max_retry_delay_ms=5000)
    assert client._aelix_retry_after_cap == 5.0
    assert client.copy()._aelix_retry_after_cap == 5.0
    assert client.with_options(timeout=3)._aelix_retry_after_cap == 5.0
    assert create_async_client(api_key="sk-provider-fake")._aelix_retry_after_cap == 60.0
