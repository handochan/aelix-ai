"""Demo entry point for the Aelix runtime.

Run with ``uv run aelix`` (or ``python -m aelix``). Surfaces:

- ``aelix`` (no subcommand, default mode interactive) — wires the ``echo``
  example tool into an :class:`Agent` with a mock ``stream_fn`` that
  plays a two-turn script.
- ``aelix --mode rpc`` — Sprint 6d (Phase 4.4) headless JSONL protocol —
  spawns an :class:`AgentHarness` and routes stdin/stdout through
  :func:`aelix_coding_agent.rpc.run_rpc_mode` for non-Python client
  embedding.
- ``aelix auth <subcommand>`` — Sprint 6e (Phase 4.5) — credential
  management via :class:`AuthStorage`. Subcommands:

  - ``login <provider>`` — start OAuth flow with stdin-based callbacks.
  - ``logout <provider>`` — drop stored credentials.
  - ``status [provider]`` — report :class:`AuthStatus` per provider.
  - ``list`` — list all stored providers.

No LLM provider or API key is required for the interactive demo. Phase 2
swapped the mock for real providers under :mod:`aelix_ai.providers`; the
RPC mode reuses the same mock so the wire surface is end-to-end testable
without external credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core import Agent, AgentEvent, AgentOptions, AgentState
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.oauth import (
    AuthStorage,
    OAuthAuthInfo,
    OAuthLoginCallbacks,
    OAuthPrompt,
    get_oauth_providers,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.examples.echo.echo import echo_tool
from aelix_coding_agent.rpc import run_rpc_mode


def _make_mock_stream_fn() -> Any:
    """Return a stateful mock ``stream_fn`` that plays the two-turn demo."""

    turn_index = {"value": 0}

    async def mock_stream_fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        idx = turn_index["value"]
        turn_index["value"] += 1
        partial = AssistantMessage(content=[])
        yield AssistantStartEvent(partial=partial)
        if idx == 0:
            final = AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="call_1",
                        tool_name="echo",
                        input={"text": "Aelix runtime is online."},
                    )
                ],
                stop_reason="tool_use",
            )
        else:
            final = AssistantMessage(
                content=[
                    TextContent(text="Echoed: Aelix runtime is online.")
                ],
                stop_reason="end_turn",
            )
        yield AssistantEndEvent(message=final)

    return mock_stream_fn


def _print_listener(event: AgentEvent) -> None:
    if event.type == "message_end":
        msg = event.message
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextContent):
                    print(f"[assistant] {block.text}")
                elif isinstance(block, ToolCallContent):
                    print(f"[tool call ] {block.tool_name}({block.input})")
    elif event.type == "tool_execution_end":
        for block in event.result.content:
            if isinstance(block, TextContent):
                print(f"[tool ret  ] {block.text}")


# Backwards-compatible alias for ``_run`` — the legacy demo entry point.
# Sprint 6d (Phase 4.4) split the interactive demo into ``_run_interactive``
# alongside the new ``_run_rpc`` entry; existing regressions reference the
# original name so we preserve it as an alias.


async def _run_interactive() -> None:
    state = AgentState(
        system_prompt="You are an echo bot.",
        model=Model(id="mock-echo", provider="mock"),
        tools=[echo_tool],
    )
    agent = Agent(
        AgentOptions(
            initial_state=state,
            stream_fn=_make_mock_stream_fn(),
        )
    )
    agent.subscribe(_print_listener)

    await agent.prompt("Echo this: Aelix runtime is online.")


async def _run_rpc() -> None:
    """Sprint 6d (Phase 4.4) — headless JSONL protocol entry.

    Builds a minimal :class:`AgentHarness` and hands stdin/stdout to
    :func:`run_rpc_mode`. The harness has the same mock ``stream_fn`` as
    the interactive demo so the RPC wire surface is testable end-to-end
    without external credentials. RpcClient drives everything through the
    9 supported commands (prompt, abort, new_session, get_state,
    get_messages, compact, bash, set_thinking_level, set_session_name).
    """

    options = AgentHarnessOptions(
        model=Model(id="mock-echo", provider="mock"),
        stream_fn=_make_mock_stream_fn(),
    )
    harness = AgentHarness(options)
    await run_rpc_mode(harness)


# === Sprint 6e (Phase 4.5) — `aelix auth` subcommand ===


def _cli_callbacks() -> OAuthLoginCallbacks:
    """Build an :class:`OAuthLoginCallbacks` bundle for CLI use.

    ``on_auth`` prints the auth URL + instructions to stdout. ``on_prompt``
    reads from stdin. ``on_progress`` prints progress to stdout. The
    Anthropic + Codex flows additionally race ``on_manual_code_input``
    against the local callback server; we wire that to the same stdin
    reader (the user may paste either the redirect URL or the bare code).
    """

    def on_auth(info: OAuthAuthInfo) -> None:
        print(f"\nOpen this URL in your browser:\n  {info.url}", flush=True)
        if info.instructions:
            print(f"\n{info.instructions}", flush=True)

    def on_prompt(prompt: OAuthPrompt) -> str:
        placeholder = f" [{prompt.placeholder}]" if prompt.placeholder else ""
        return input(f"\n{prompt.message}{placeholder}: ")

    def on_progress(msg: str) -> None:
        print(f"... {msg}", flush=True)

    return OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_progress=on_progress,
    )


async def _cmd_auth_login(provider_id: str) -> int:
    """Login to ``provider_id`` and persist credentials to auth.json.

    Sprint 6e W6 (n1): ``storage.login`` may raise :class:`RuntimeError`
    for unknown providers, OAuth-server failures, or token-exchange
    failures. The CLI catches these and prints a diagnostic to stderr
    + exits with code 1 (rather than dumping a Python traceback).
    """

    storage = AuthStorage()
    await storage.load()
    try:
        await storage.login(provider_id, _cli_callbacks())
    except RuntimeError as exc:
        print(f"Login failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Logged in: {provider_id}")
    return 0


async def _cmd_auth_logout(provider_id: str) -> int:
    """Drop stored credentials for ``provider_id``."""

    storage = AuthStorage()
    await storage.load()
    await storage.logout(provider_id)
    print(f"Logged out: {provider_id}")
    return 0


async def _cmd_auth_status(provider_id: str | None) -> int:
    """Report :class:`AuthStatus` for ``provider_id`` (or all providers).

    Sprint 6e W6 (P-152): when an explicit ``provider_id`` is given and
    does NOT match any known OAuth provider OR any stored entry, exit
    with code 2 and a clear ``Unknown provider`` message on stderr —
    rather than silently reporting ``not configured``.
    """

    storage = AuthStorage()
    await storage.load()

    if provider_id is not None:
        # Pi parity: validate against known OAuth providers UNION the
        # stored entries (api_key entries for not-yet-registered
        # providers must remain queryable).
        known = {p.id for p in get_oauth_providers()} | set(storage.list())
        if provider_id not in known:
            print(
                f"Unknown provider: {provider_id}",
                file=sys.stderr,
                flush=True,
            )
            return 2
        providers = [provider_id]
    else:
        # All built-in OAuth provider ids + any stored entries
        # (e.g., raw api_key providers not in the OAuth registry).
        ids = {p.id for p in get_oauth_providers()}
        ids.update(storage.list())
        providers = sorted(ids)

    for pid in providers:
        status = await storage.get_auth_status(pid)
        configured = "configured" if status.configured else "not configured"
        source = status.source or "—"
        label = f" ({status.label})" if status.label else ""
        print(f"{pid}: {configured} [source={source}{label}]")
    return 0


async def _cmd_auth_list() -> int:
    """List all stored provider entries (api_key or oauth)."""

    storage = AuthStorage()
    await storage.load()
    entries = storage.list()
    if not entries:
        print("(no credentials stored)")
        return 0
    for provider_id in sorted(entries):
        status = await storage.get_auth_status(provider_id)
        print(f"{provider_id}: source={status.source or '—'}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aelix")
    parser.add_argument(
        "--mode",
        choices=["interactive", "rpc"],
        default="interactive",
        help=(
            "Run as a local demo (default) or in RPC mode (Phase 4.4 — "
            "JSONL stdin/stdout protocol for non-Python embedding)."
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Optional provider identifier (forwarded by RpcClient).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model id (forwarded by RpcClient).",
    )

    # Sprint 6e (Phase 4.5): ``aelix auth ...`` subcommands. Subparsers
    # are declared optional (``required=False``) so the top-level
    # ``aelix`` and ``aelix --mode rpc`` paths keep working.
    subparsers = parser.add_subparsers(dest="command", required=False)

    auth = subparsers.add_parser("auth", help="Credential management.")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    login_p = auth_sub.add_parser("login", help="Login to an OAuth provider.")
    login_p.add_argument("provider", help="Provider id (e.g., anthropic).")

    logout_p = auth_sub.add_parser(
        "logout", help="Drop stored credentials for a provider."
    )
    logout_p.add_argument("provider", help="Provider id.")

    status_p = auth_sub.add_parser(
        "status", help="Report auth status per provider."
    )
    status_p.add_argument(
        "provider", nargs="?", default=None, help="Optional provider id."
    )

    auth_sub.add_parser("list", help="List all stored provider entries.")

    return parser


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _build_parser()
    # ``parse_known_args`` so RpcClient's extra ``args`` pass-through
    # doesn't break the CLI when future flags are added downstream.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    # Sprint 6e (Phase 4.5): dispatch the ``auth`` subcommand first.
    if getattr(args, "command", None) == "auth":
        auth_cmd = getattr(args, "auth_command", None)
        if auth_cmd == "login":
            sys.exit(asyncio.run(_cmd_auth_login(args.provider)))
        if auth_cmd == "logout":
            sys.exit(asyncio.run(_cmd_auth_logout(args.provider)))
        if auth_cmd == "status":
            sys.exit(asyncio.run(_cmd_auth_status(args.provider)))
        if auth_cmd == "list":
            sys.exit(asyncio.run(_cmd_auth_list()))
        # argparse guarantees one of the above; defensive default.
        sys.exit(2)

    # Sprint 6d (Phase 4.4) — RPC mode dispatch (preserved).
    if args.mode == "rpc":
        asyncio.run(_run_rpc())
        return
    asyncio.run(_run_interactive())


# Legacy alias preserved for tests that pre-date the Phase 4.4 split
# (``tests/test_agent_regression.py::test_existing_demo_runs_clean``).
_run = _run_interactive


if __name__ == "__main__":
    main()
