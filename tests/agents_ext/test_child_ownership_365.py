"""#365: real print/RPC subprocesses retain explicit-owner requirements through hooks.

The child bootstrap intercepts actual HTTPX prepared requests below the SDK and
answers a synthetic refusal. Every outbound request is intercepted, including
a mistaken builtin destination. Only fixed fake-key tags are recorded.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aelix_agents.print_channel import PrintChannel, SpawnPlan, build_child_env
from aelix_agents.rpc_channel import RpcChannel, build_rpc_child_env
from aelix_ai.models import get_model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import child_model_flags
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.model_registry import ProviderConfigInput
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.env_sandbox import child_env
from tests.model_registry.test_provider_ownership_365 import registry, row

_INTERCEPT = """
import json, os
from pathlib import Path
import httpx
async def intercepted_send(self, request, **kwargs):
    try:
        model = json.loads(request.content).get("model")
    except (ValueError, KeyError):
        model = None
    key = request.headers.get("authorization", "").removeprefix("Bearer ")
    record = {"host": request.url.host, "path": request.url.path, "model": model,
              "tag": {"B-fake": "B", "env-fake": "env"}.get(key, "other")}
    with Path(os.environ["OWNERSHIP_WIRE_PATH"]).open("a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\\n")
    return httpx.Response(401, request=request, json={"error": {"message": "FAKE-WIRE-365"}})
httpx.AsyncClient.send = intercepted_send
"""


def extension(
    path: Path,
    provider="openai",
    base="http://127.0.0.1:36501/v1",
    hook=None,
    api="openai-completions",
    fatal_queue=None,
):
    path.write_text(
        f"""
from aelix_ai.streaming import Model
from aelix_ai.models import get_model
from aelix_coding_agent.model_registry import ProviderConfigInput
def setup(api):
    api.register_provider({provider!r}, ProviderConfigInput(api_key="B-fake", models={{"m1": Model(id="m1", provider={provider!r}, api={api!r}, base_url={base!r})}}))
"""
        + (
            f"""
    async def change(event, ctx):
        {"api.register_provider('openai', ProviderConfigInput(api_key='B-fake'))" if hook == "input" else "api.unregister_provider('openai')"}
        {"await api.set_model(get_model('openai', 'gpt-4o-mini'))" if hook in ("session_start", "input") else "pass"}
    api.on({hook!r}, change)
"""
            if hook
            else ""
        )
        + (
            f"""
    from dataclasses import replace
    import asyncio
    from aelix_ai.messages import AssistantMessage
    from aelix_agent_core.harness.hooks import MessageEndEventResult
    once = False
    async def fatal(event, ctx):
        nonlocal once
        if not once:
            once = True
            api.register_provider({provider!r}, ProviderConfigInput(api_key="B-fake", models={{"m1": Model(id="m1", provider={provider!r}, api={api!r}, base_url={base!r})}}))
            api.send_user_message("queued after fatal", deliver_as={fatal_queue!r})
            await asyncio.sleep(0)
    def rewrite(event, ctx):
        if isinstance(event.message, AssistantMessage) and event.message.stop_reason == "error":
            return MessageEndEventResult(message=replace(event.message, stop_reason="stop"))
    api.on("before_provider_payload", fatal)
    api.on("message_end", rewrite)
"""
            if fatal_queue is not None
            else ""
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("channel_kind", ["print", "rpc"])
@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "same",
        "wrong-host",
        "wrong-api",
        "session_start",
        "input",
        "builtin",
        "child-only",
        "before_provider_request",
        "before_provider_payload",
        "fatal-follow_up",
        "fatal-steer",
    ],
)
async def test_child_owner_wire_and_controls(tmp_path, channel_kind, case):
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(
        json.dumps({"retry": {"enabled": False}}), encoding="utf-8"
    )
    r = await registry(agent)
    r.register_provider("openai", ProviderConfigInput(api_key="B-fake", models={"m1": row()}))
    selected = r.find("openai", "m1")
    if case == "builtin":
        r.unregister_provider("openai")
        selected = get_model("openai", "gpt-4o-mini")
    explicit = ()
    model = None
    if case not in ("missing", "builtin"):
        ext = tmp_path / "owner.py"
        extension(
            ext,
            provider="childonly" if case == "child-only" else "openai",
            base="http://127.0.0.1:36502/v1" if case == "wrong-host" else row().base_url,
            hook=case
            if case
            in ("session_start", "input", "before_provider_request", "before_provider_payload")
            else None,
            api="openai-responses" if case == "wrong-api" else "openai-completions",
            fatal_queue=case.removeprefix("fatal-") if case.startswith("fatal-") else None,
        )
        explicit = (str(ext),)
        if case == "child-only":
            model = "childonly/m1"
    prompt = tmp_path / "profile.md"
    prompt.write_text("Answer briefly.", encoding="utf-8")
    profile = AgentProfile(
        name="p",
        description="d",
        body="Answer briefly.",
        file_path=str(prompt),
        scope="user",
        tools=(),
        extensions=explicit,
        model=model,
    )
    flags = child_model_flags(profile, selected, r)
    if case in ("builtin", "child-only"):
        assert "--require-registration-route" not in flags
    else:
        at = flags.index("--require-registration-route")
        assert len(flags[at + 1]) == 64
        assert row().base_url not in " ".join(flags)
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(_INTERCEPT, encoding="utf-8")
    record = tmp_path / "wire.jsonl"
    scratch = tmp_path / "cwd"
    scratch.mkdir()
    env = child_env(
        tmp_path / "home",
        AELIX_CODING_AGENT_DIR=str(agent),
        AELIX_SETTINGS_PATH=str(agent / "settings.json"),
        PYTHONPATH=str(bootstrap),
        OWNERSHIP_WIRE_PATH=str(record),
        OPENAI_API_KEY="env-fake",
        PI_OFFLINE="1",
        AELIX_DEFAULT_CATALOG="",
    )
    cls = PrintChannel if channel_kind == "print" else RpcChannel
    env_builder = build_child_env if channel_kind == "print" else build_rpc_child_env
    channel = cls(
        parent_model=lambda: selected,
        model_registry=lambda: r,
        env_builder=lambda p: env_builder(p, base=env),
    )
    plan = SpawnPlan(
        id="sub-365",
        resolved=ResolvedProfile(name="p", profile=profile, source_path=str(prompt), scope="user"),
        task="Reply: hi",
        cwd=str(scratch),
        parent_cwd=str(scratch),
        permission_mode=PermissionMode.PLAN,
    )
    result = await asyncio.wait_for(channel.run(plan), 45)
    records = (
        [json.loads(line) for line in record.read_text("utf-8").splitlines()]
        if record.exists()
        else []
    )
    assert not result.ok  # fake SDK refusal, or the ownership refusal before SDK
    if case in ("same", "builtin", "child-only"):
        assert len(records) == 1
        assert records[0] == {
            "host": "api.openai.com" if case == "builtin" else "127.0.0.1",
            "path": "/v1/responses" if case == "builtin" else "/v1/chat/completions",
            "model": "gpt-4o-mini" if case == "builtin" else "m1",
            "tag": "env" if case == "builtin" else "B",
        }
    else:
        assert records == []
        assert "registration" in f"{result.summary} {result.details}"


async def test_receipt_hash_does_not_disclose_url_credentials(tmp_path):
    r = await registry(tmp_path)
    model = row(base="https://fake-user:fake-password@example.invalid/v1?token=fake-secret")
    r.register_provider("openai", ProviderConfigInput(models={"m1": model}))
    profile = AgentProfile(name="p", description="d", body="b", file_path="p.md", scope="user")
    flags = child_model_flags(profile, model, r)
    rendered = " ".join(flags)
    assert "--require-registration-route" in flags
    assert not any(
        value in rendered
        for value in ("fake-user", "fake-password", "fake-secret", "example.invalid")
    )
