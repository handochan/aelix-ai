"""#760 through the CLI: the real ``_async_main`` registers the ``bash`` kind and ``-p`` and json wait for it (ADR-0297).

The gate is forced on (``jobs/activation.py``'s seam) and the model is a scripted
OpenAI-compatible stream behind httpx's mock transport: the first reply calls
``bash`` with ``run_in_background``, the next ones answer. Everything between is
the product's own: ``_with_bash_kind`` in the CLI's ``is_active()`` block,
``_tool_options_for`` in ``_build_harness_options``, the tool, the job service,
the runner and the print drain. Each case runs from a cwd of its own (the
``env`` fixture chdirs to ``tmp_path/cwd``), as #733 asks of CLI cases.

Fails on ``5a0624d7`` with ``_with_bash_kind`` dropped (verify F2, M96): the
call answers "no job kind is registered under 'bash'". Fails on ``71f837eb``:
the call runs in the foreground, so the first tool result already holds the
output and no notice turn follows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs import activation

from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env is a fixture
    _write_models,
    env,
)

pytestmark = pytest.mark.skipif(
    __import__("sys").platform == "win32", reason="a POSIX shell command in the scripted call"
)


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    yield
    activation.install_service(None)


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The request bodies the model saw; the first answer is the background call."""

    import httpx

    bodies: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        bodies.append(body)
        if len(bodies) == 1:
            call = {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps(
                        {"command": "sleep 1; echo PRINTED-LATE", "run_in_background": True}
                    ),
                },
            }
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [call]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
        else:
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": f"reply {len(bodies)}"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
        for chunk in chunks:
            chunk.update(id="p", object="chat.completion.chunk")
        chunks[-1]["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data.encode())

    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(answer)
        kwargs["mounts"] = {}
        kwargs["trust_env"] = False
        kwargs.pop("proxy", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return bodies


def _texts(body: dict[str, Any]) -> str:
    out: list[str] = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            out.extend(part.get("text", "") for part in content if isinstance(part, dict))
    return "\n".join(out)


@pytest.mark.usefixtures("gate")
@pytest.mark.parametrize("mode", [["-p"], ["--mode", "json", "-p"]], ids=["print", "json"])
async def test_the_cli_starts_a_background_command_and_waits_for_its_notice(
    env: Path,  # noqa: F811
    scripted: list[dict[str, Any]],
    mode: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(env / "tmp"))
    (env / "tmp").mkdir()
    _write_models(env, "rec")
    code = await entry_mod._async_main(["--model", "rec/m1", *mode, "go"])
    assert code == 0, capsys.readouterr().err
    assert len(scripted) == 3, "the call, its answer, then the notice turn"
    started = _texts(scripted[1])
    assert "Started in the background as job" in started, "the kind was registered (verify F2)"
    assert "PRINTED-LATE" not in started, "it did not run in the foreground"
    last = _texts(scripted[2])
    assert "PRINTED-LATE" in last and "background-job-result" in last, "-p and json waited for it"
