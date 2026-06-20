# aelix-server

The [Aelix](https://github.com/handochan/aelix-ai) server: a FastAPI + uvicorn
daemon exposing the agent runtime over WebSocket (`/rpc`), with `/healthz` and
`/schemas` endpoints. Aelix-additive (ADR-0097 / ADR-0103).

> **Status:** the Web-UI daemon is deferred to a later release and is **not**
> part of the current Aelix PyPI publish set. It builds as part of the workspace
> but is not published yet.

See the [project README](https://github.com/handochan/aelix-ai#readme) for
architecture and usage.

Licensed under Apache-2.0.
