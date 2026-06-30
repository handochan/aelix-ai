"""Thin ``google-genai`` SDK wrappers — pi parity (#15).

Pi parity: ``createClient`` in ``packages/ai/src/api/google-generative-ai.ts``
(322-341) and the ``createClient`` / ``createClientWithApiKey`` /
``buildHttpOptions`` trio in ``packages/ai/src/api/google-vertex.ts``
(337-407). Both pi adapters construct the official ``@google/genai``
``GoogleGenAI`` client; the Aelix port targets the official ``google-genai``
Python SDK wrapped behind this module so the two adapters (``google`` Gemini
Developer API + ``google-vertex`` Vertex AI) share one construction seam.

The SDK is **lazy-imported inside each factory** so importing the adapter
modules (which stay dormant until Workflow B registers them) never hard-fails
when ``google-genai`` is absent — matching the dormant-build contract for #15.

The ``api_version=""`` quirk (Gemini Developer API): a Gemini ``baseUrl`` such
as ``https://generativelanguage.googleapis.com/v1beta`` already carries the
version path, so the SDK must NOT append its own — pi sets ``apiVersion = ""``
whenever ``model.baseUrl`` is present. In the Python SDK ``api_version`` lives
inside ``http_options`` (there is no top-level ``apiVersion`` constructor arg).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.genai import Client


# Pi parity: ``google-vertex.ts`` ``API_VERSION``. Vertex pins ``v1`` (vs the
# Gemini Developer API's ``v1beta``); pi passes it as the top-level
# ``apiVersion`` GoogleGenAI option, which the Python SDK exposes as
# ``http_options.api_version``.
_VERTEX_API_VERSION = "v1"

# Pi parity: ``baseUrlIncludesApiVersion`` (google-vertex.ts:395-402) — a path
# segment like ``v1`` / ``v1beta`` / ``v1beta2``.
_API_VERSION_SEGMENT = re.compile(r"^v\d+(?:beta\d*)?$")


def create_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout_ms: int | None = None,
) -> Client:
    """Build a Gemini Developer API ``genai.Client``.

    Pi parity: ``createClient`` (google-generative-ai.ts:322-341). The
    ``base_url`` is expanded for any ``{ENV_VAR}`` placeholder (cloudflare-style
    safety) before the SDK sees it; when present, the ``api_version=""`` quirk
    is applied so the SDK does not append a version onto a URL that already has
    one. ``headers`` (model + option headers, pre-merged by the adapter) ride
    in ``http_options`` when non-empty.
    """

    from google.genai import Client

    from aelix_ai.providers._base_url import expand_base_url

    http_options: dict[str, Any] = {}
    expanded = expand_base_url(base_url)
    if expanded:
        http_options["base_url"] = expanded
        # baseUrl already includes the version path — don't append one.
        http_options["api_version"] = ""
    if headers:
        http_options["headers"] = dict(headers)
    if timeout_ms is not None:
        # The SDK's HttpOptions.timeout is in milliseconds.
        http_options["timeout"] = timeout_ms

    kwargs: dict[str, Any] = {"api_key": api_key}
    if http_options:
        kwargs["http_options"] = http_options
    return Client(**kwargs)


def _resolve_vertex_custom_base_url(base_url: str | None) -> str | None:
    """Pi parity: ``resolveCustomBaseUrl`` (google-vertex.ts:387-393).

    A Vertex ``baseUrl`` that is empty or still carries the ``{location}``
    placeholder is ignored (the SDK builds the regional host from
    project/location); any other explicit URL is honored.
    """

    if not base_url:
        return None
    trimmed = base_url.strip()
    if not trimmed or "{location}" in trimmed:
        return None
    return trimmed


def _base_url_includes_api_version(base_url: str) -> bool:
    """Pi parity: ``baseUrlIncludesApiVersion`` (google-vertex.ts:395-402)."""

    path = base_url
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+(/.*)?$", base_url)
    if match and match.group(1):
        path = match.group(1)
    return any(_API_VERSION_SEGMENT.match(seg) for seg in path.split("/") if seg)


def _build_vertex_http_options(
    base_url: str | None, headers: dict[str, str] | None
) -> dict[str, Any]:
    """Pi parity: ``buildHttpOptions`` (google-vertex.ts:368-385).

    ``api_version`` defaults to ``v1`` (the top-level pi ``apiVersion``); a
    custom base URL is added with ``base_url_resource_scope = COLLECTION`` and,
    when that URL already includes a version path, ``api_version`` is cleared
    so the SDK does not double-append one.
    """

    http_options: dict[str, Any] = {"api_version": _VERTEX_API_VERSION}
    custom = _resolve_vertex_custom_base_url(base_url)
    if custom:
        http_options["base_url"] = custom
        http_options["base_url_resource_scope"] = "COLLECTION"
        if _base_url_includes_api_version(custom):
            http_options["api_version"] = ""
    if headers:
        http_options["headers"] = dict(headers)
    return http_options


def create_vertex_client(
    *,
    api_key: str | None = None,
    project: str | None = None,
    location: str | None = None,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
) -> Client:
    """Build a Vertex AI ``genai.Client`` (``vertexai=True``).

    Pi parity: ``createClientWithApiKey`` (google-vertex.ts:355-366) when an
    explicit Vertex ``api_key`` is supplied, else ``createClient``
    (337-353) using ADC with ``project`` + ``location``. The Python SDK
    auto-discovers Application Default Credentials (incl.
    ``GOOGLE_APPLICATION_CREDENTIALS``) when no ``api_key`` is given, so pi's
    ``googleAuthOptions.keyFilename`` plumbing is unnecessary (documented
    divergence — the SDK reads the env var itself).
    """

    from google.genai import Client

    http_options = _build_vertex_http_options(base_url, headers)
    kwargs: dict[str, Any] = {"vertexai": True, "http_options": http_options}
    if api_key:
        kwargs["api_key"] = api_key
    else:
        if project:
            kwargs["project"] = project
        if location:
            kwargs["location"] = location
    return Client(**kwargs)


async def open_generate_content_stream(
    client: Any, params: dict[str, Any]
) -> Any:
    """Open the async Gemini stream — the ``double-await`` seam.

    Pi parity: ``await client.models.generateContentStream(params)``. The
    Python SDK's ``client.aio.models.generate_content_stream`` is itself a
    coroutine that resolves to the async iterator, so the caller does
    ``async for chunk in await open_generate_content_stream(...)`` — the outer
    await happens here, returning the iterator.
    """

    return await client.aio.models.generate_content_stream(
        model=params["model"],
        contents=params["contents"],
        config=params.get("config"),
    )


__all__ = [
    "create_client",
    "create_vertex_client",
    "open_generate_content_stream",
]
