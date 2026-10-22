"""#416: real CLI factories and SDK wire encoding, with dummy sources only.

Interactive rows drive the factory-built harness; native TUI rendering is a
separate live gate. Print/json use their real runner, RPC its real handler.
HTTP is MockTransport below all SDK clients, with a socket/DNS tripwire.
No HOME override, credential-file discovery, or real model request is needed.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from aelix_agent_core.harness.hooks import InputHandled, InputTransform
from aelix_coding_agent.cli import entry
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import RpcCommandPrompt, RpcErrorResponse


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("ANTHROPIC_", "GOOGLE_", "GCLOUD_", "OPENROUTER_", "AELIX_", "CLOUDFLARE_")
        ):
            monkeypatch.delenv(name)
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text('{"retry":{"enabled":false}}', "utf-8")
    (agent / "models.json").write_text("{}", "utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    monkeypatch.setenv("AELIX_AUTH_PATH", str(agent / "auth.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    class Stdin:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:
            return ""

    monkeypatch.setattr(sys, "stdin", Stdin())
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    return agent


def anthropic_sse(model: str) -> bytes:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg416",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "auth416-ok"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
    ).encode()


@pytest.fixture
async def wire(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[dict[str, Any]]]:
    """Seal every SDK's real httpx client; fake ADC is discovered only at request time."""
    sent: list[dict[str, Any]] = []

    # Bootstrap the loop before denying socket calls; restore before its teardown.
    asyncio.get_running_loop()
    with monkeypatch.context() as patch:

        def blocked(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("416 test attempted socket/DNS outside MockTransport")

        patch.setattr(socket, "getaddrinfo", blocked)
        patch.setattr(socket.socket, "connect", blocked)
        patch.setattr(socket.socket, "connect_ex", blocked)

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent.append(
                {
                    "host": request.url.host,
                    "path": request.url.path,
                    "body": body,
                    "authorization": request.headers.get("authorization"),
                    "x-api-key": request.headers.get("x-api-key"),
                    "google-key": request.headers.get("x-goog-api-key"),
                }
            )
            if request.url.path.endswith("/messages"):
                content = anthropic_sse(body["model"])
            else:
                data = {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "auth416-ok"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 1,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 2,
                    },
                }
                content = f"data: {json.dumps(data)}\n\n".encode()
            return httpx.Response(
                200, content=content, headers={"content-type": "text/event-stream"}
            )

        original_async = httpx.AsyncClient.__init__
        original_sync = httpx.Client.__init__

        def async_client(self: Any, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            original_async(self, *args, **kwargs)

        def sync_client(self: Any, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            original_sync(self, *args, **kwargs)

        patch.setattr(httpx.AsyncClient, "__init__", async_client)
        patch.setattr(httpx.Client, "__init__", sync_client)
        import google.auth
        from google.oauth2.credentials import Credentials

        patch.setattr(
            google.auth,
            "default",
            lambda *a, **kw: (
                Credentials(token="fake416-adc", expiry=datetime.datetime(2099, 1, 1)),
                "fake416-project",
            ),
        )
        yield sent


def models(agent: Path, providers: dict[str, Any]) -> None:
    (agent / "models.json").write_text(json.dumps({"providers": providers}), "utf-8")


def setup_source(
    agent: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> tuple[list[str], str]:
    provider, model = "anthropic", "claude-haiku-4-5"
    tag = f"fake416-{source}"
    config: dict[str, Any] = {"baseUrl": "https://configured.invalid"}
    extra: list[str] = []
    if source in ("provider-header", "model-header"):
        if source == "provider-header":
            config["headers"] = {"aUtHoRiZaTiOn": f"Bearer {tag}"}
        else:
            config["modelOverrides"] = {model: {"headers": {"x-api-key": tag}}}
    elif source in ("registration-header", "static-header"):
        ext = agent / "auth416.py"
        config_text = (
            f'headers={{"x-api-key": "{tag}"}}'
            if source == "registration-header"
            else f'models={{"{model}": Model(id="{model}", api="anthropic-messages", '
            f'base_url="https://configured.invalid", headers={{"x-api-key": "{tag}"}})}}'
        )
        ext.write_text(
            "from aelix_ai.streaming import Model\n"
            "from aelix_coding_agent.model_registry import ProviderConfigInput\n"
            f'def setup(aelix):\n    aelix.register_provider("anthropic", ProviderConfigInput({config_text}))\n',
            "utf-8",
        )
        extra = ["-e", str(ext)]
    elif source == "custom-headers":
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", f"x-api-key: {tag}")
    elif source == "auth-token":
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", tag)
    elif source.startswith("vertex"):
        provider, model = "google-vertex", "gemini-2.5-flash"
        config = {}
        if source == "vertex-adc":
            monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fake416-project")
            monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            # No local file is required: the SDK's ambient source is deferred.
            tag = "fake416-adc"
        elif source == "vertex-runtime":
            extra = ["--api-key", tag]
        elif source == "vertex-stored":
            (agent / "auth.json").write_text(
                json.dumps({provider: {"type": "api_key", "key": tag}}), "utf-8"
            )
        elif source == "vertex-json":
            config["apiKey"] = tag
        elif source == "vertex-registration":
            ext = agent / "auth416.py"
            ext.write_text(
                "from aelix_coding_agent.model_registry import ProviderConfigInput\n"
                f'def setup(aelix):\n    aelix.register_provider("google-vertex", ProviderConfigInput(api_key="{tag}"))\n',
                "utf-8",
            )
            extra = ["-e", str(ext)]
    models(agent, {provider: config})
    return ["--provider", provider, "--model", model, *extra], tag


async def run_mode(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], mode: str, before: Any = None
) -> tuple[int, Any, list[str], Any]:
    from aelix_coding_agent import modes
    from aelix_coding_agent import tui as tui_module

    seen: dict[str, Any] = {"response": None}
    events: list[str] = []

    async def prepare(runtime: Any) -> None:
        seen["harness"] = runtime.harness
        runtime.harness.subscribe(lambda event: events.append(getattr(event, "type", "?")))
        if before is not None:
            await before(runtime)

    async def rpc(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any, **_host_services: Any
    ) -> None:
        await prepare(runtime_host)
        seen["response"] = await _handle_prompt(
            runtime_host.harness, RpcCommandPrompt(message="hello416")
        )
        await asyncio.gather(*list(runtime_host.harness._pending_tasks), return_exceptions=True)

    async def tui(runtime: Any, **kwargs: Any) -> int:
        await prepare(runtime)
        try:
            await runtime.harness.prompt("hello416", source="interactive")
        except Exception as exc:
            seen["response"] = exc
            return 1
        return 0

    original_print = modes.run_print_mode

    async def print_mode(runtime: Any, **kwargs: Any) -> int:
        await prepare(runtime)
        return await original_print(runtime, **kwargs)

    monkeypatch.setattr(modes, "run_rpc_mode", rpc)
    monkeypatch.setattr(tui_module, "run_tui", tui)
    monkeypatch.setattr(modes, "run_print_mode", print_mode)
    flags = {
        "rpc": ["--mode", "rpc"],
        "tui": [],
        "print": ["-p", "hello416"],
        "json": ["--mode", "json", "-p", "hello416"],
    }[mode]
    code = await entry._async_main(
        [*argv, "--session-dir", os.environ["AELIX_CODING_AGENT_DIR"] + "/sessions", *flags]
    )
    return code, seen.get("harness"), events, seen["response"]


SOURCES = [
    "provider-header",
    "model-header",
    "static-header",
    "registration-header",
    "custom-headers",
    "auth-token",
    "vertex-adc",
    "vertex-runtime",
    "vertex-stored",
    "vertex-json",
    "vertex-registration",
]
MODES = ["tui", "rpc", "print", "json"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("source", SOURCES)
async def test_configured_sources_use_the_actual_sdk_wire(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[dict[str, Any]],
    source: str,
    mode: str,
) -> None:
    argv, tag = setup_source(sandbox, monkeypatch, source)
    code, harness, events, response = await run_mode(monkeypatch, argv, mode)
    assert code == 0, response
    assert len(wire) == 1, (wire, harness.state.messages)
    request = wire[0]
    if source.startswith("vertex"):
        assert (
            request["host"] == "us-central1-aiplatform.googleapis.com"
            if source == "vertex-adc"
            else request["host"] == "aiplatform.googleapis.com"
        )
        assert "gemini-2.5-flash:streamGenerateContent" in request["path"]
        assert request["body"]["contents"][0]["parts"][0]["text"] == "hello416"
        assert (
            request["authorization"] == f"Bearer {tag}"
            if source == "vertex-adc"
            else request["google-key"] == tag
        )
    else:
        assert (request["host"], request["path"], request["body"]["model"]) == (
            "configured.invalid",
            "/v1/messages",
            "claude-haiku-4-5",
        )
        assert tag in (request["authorization"] or request["x-api-key"] or "")
    assert "agent_start" in events
    assert [m.role for m in harness.state.messages] == ["user", "assistant"]
    assert harness.state.messages[-1].stop_reason == (
        "stop" if source.startswith("vertex") else "end_turn"
    )
    branch = await harness.session.get_branch()
    assert len([e for e in branch if e.type == "message"]) == 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("handled", [False, True], ids=["absent-refused", "input-handled"])
async def test_missing_source_refuses_before_new_records_but_handled_input_succeeds(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[dict[str, Any]],
    mode: str,
    handled: bool,
) -> None:
    models(sandbox, {"anthropic": {"baseUrl": "https://configured.invalid"}})
    hook_calls: list[str] = []

    async def before(runtime: Any) -> None:
        def handle(event: Any, ctx: Any = None) -> Any:
            hook_calls.append(event.text)
            return InputHandled() if handled else None

        runtime.harness.hooks.on("input", handle)

    code, harness, events, response = await run_mode(
        monkeypatch, ["--provider", "anthropic", "--model", "claude-haiku-4-5"], mode, before
    )
    assert hook_calls == ["hello416"]
    assert code == (0 if handled or mode == "rpc" else 1)
    if mode == "rpc" and not handled:
        assert isinstance(response, RpcErrorResponse)
        assert "No API key" in response.error
    assert wire == []
    assert events == []
    assert harness.state.messages == []
    assert not [
        e for e in await harness.session.get_branch() if e.type in ("message", "custom_message")
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "configured", [False, True], ids=["switch-to-absent", "switch-to-configured"]
)
async def test_input_transform_judges_the_actual_switched_model(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[dict[str, Any]],
    mode: str,
    configured: bool,
) -> None:
    argv, _ = setup_source(sandbox, monkeypatch, "provider-header")
    from dataclasses import replace

    async def before(runtime: Any) -> None:
        current = runtime.harness.current_model
        selected = replace(
            current,
            provider="own416",
            headers={"x-api-key": "fake416-switched"} if configured else {},
        )

        async def transform(event: Any, ctx: Any = None) -> InputTransform:
            await runtime.harness.set_model(selected)
            return InputTransform(text="changed416")

        runtime.harness.hooks.on("input", transform)

    code, harness, events, response = await run_mode(monkeypatch, argv, mode, before)
    assert harness.current_model.provider == "own416"
    assert code == (0 if configured or mode == "rpc" else 1)
    assert len(wire) == int(configured)
    if configured:
        assert wire[0]["x-api-key"] == "fake416-switched"
        assert wire[0]["body"]["messages"][0]["content"][0]["text"] == "changed416"
    else:
        assert events == []
        assert harness.state.messages == []


@pytest.mark.parametrize("operation", ["continue", "resume", "switch", "reload", "fork", "clone"])
async def test_per_model_header_survives_session_restore_and_harness_rebuild(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, wire: list[dict[str, Any]], operation: str
) -> None:
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionRepo,
        LocalFileSystem,
    )
    from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
    from aelix_coding_agent.rpc.rpc_mode import _handle_clone, _handle_switch_session
    from aelix_coding_agent.rpc.rpc_types import RpcCommandClone, RpcCommandSwitchSession

    models(
        sandbox,
        {
            "anthropic": {
                "baseUrl": "https://configured.invalid",
                "modelOverrides": {
                    "claude-haiku-4-5": {"headers": {"x-api-key": "fake416-restored"}}
                },
            }
        },
    )
    (sandbox / "settings.json").write_text(
        json.dumps(
            {
                "defaultProvider": "anthropic",
                "defaultModel": "claude-sonnet-4-5",
                "retry": {"enabled": False},
            }
        ),
        "utf-8",
    )
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(sandbox / "sessions"))
    saved = await repo.create(JsonlSessionCreateOptions(cwd=str(Path.cwd())))
    await saved.append_model_change("anthropic", "claude-haiku-4-5")
    await saved.append_message(UserMessage(content=[TextContent(text="old416")]))
    await saved.append_message(
        AssistantMessage(
            content=[TextContent(text="old-ok416")],
            provider="anthropic",
            model="claude-haiku-4-5",
            api="anthropic-messages",
        )
    )
    leaf = await saved.get_leaf_id()
    assert leaf is not None
    session_id = json.loads(Path(str(saved.session_file)).read_text("utf-8").splitlines()[0])["id"]

    async def before(runtime: Any) -> None:
        if operation in ("switch", "reload", "fork", "clone"):
            result = await _handle_switch_session(
                runtime, RpcCommandSwitchSession(session_path=str(saved.session_file))
            )
            assert result.success
        if operation == "reload":
            await runtime.reload()
        elif operation == "fork":
            await runtime.fork(leaf, position="at")
        elif operation == "clone":
            result = await _handle_clone(runtime, RpcCommandClone())
            assert result.success
        assert runtime.harness.current_model.id == "claude-haiku-4-5"
        assert runtime.harness._prompt_check(runtime.harness.current_model) is None
        assert runtime.model_fallback_message is None

    argv = (
        ["--continue"]
        if operation == "continue"
        else ["--resume", session_id]
        if operation == "resume"
        else []
    )
    code, harness, _events, response = await run_mode(monkeypatch, argv, "rpc", before)
    assert code == 0, response
    assert len(wire) == 1
    assert (wire[0]["body"]["model"], wire[0]["x-api-key"]) == (
        "claude-haiku-4-5",
        "fake416-restored",
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("registration", ["bare-function", "provider-object"])
async def test_a_custom_stream_replacing_a_known_api_is_not_assumed_to_require_auth(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    wire: list[dict[str, Any]],
    mode: str,
    registration: str,
) -> None:
    from aelix_ai import api_registry
    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent

    calls: list[tuple[str, str]] = []

    async def before(runtime: Any) -> None:
        async def local_stream(model: Any, context: Any, options: Any) -> Any:
            calls.append((model.provider, model.id))
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="custom416-ok")], stop_reason="end_turn"
                )
            )

        if registration == "bare-function":
            api_registry.register_provider("anthropic-messages", local_stream)
        else:

            class LocalProvider:
                api = "anthropic-messages"

                def stream(self, model: Any, context: Any, options: Any) -> Any:
                    return local_stream(model, context, options)

            api_registry.register_provider_object(LocalProvider())

    code, harness, events, response = await run_mode(
        monkeypatch, ["--provider", "anthropic", "--model", "claude-haiku-4-5"], mode, before
    )
    assert code == 0, response
    assert calls == [("anthropic", "claude-haiku-4-5")]
    assert wire == []
    assert "agent_start" in events
    assert [message.role for message in harness.state.messages] == ["user", "assistant"]
