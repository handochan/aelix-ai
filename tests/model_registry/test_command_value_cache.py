"""#240 — a ``models.json`` ``!command`` runs once per registry load.

``ModelRegistry.get_api_key_and_headers`` is the harness's PER-REQUEST auth
callback (``_make_stream_fn``'s ``stream_fn`` body calls ``get_auth(model)``
once per API request, and a turn is one request plus one more per tool call).
It resolves three values through the strict resolver family — the provider
``apiKey``, the provider ``headers``, and the per-model ``headers`` — and every
one of them re-forked a shell on every request: measured on darwin, 2 spawns and
8.44 ms of blocked event loop per call for a provider with one ``!command`` key
and one ``!command`` header. On a box that lands on PowerShell it is one shell
start per distinct command (431.8 ms with pwsh 7.6.5 on macOS; Windows
PowerShell 5.1 is unmeasured).

Every counted command here appends a TAG to one shared marker file and prints
``sk-<tag>``: the strict resolver raises on empty output, so a silent counter
would make the first call fail and store nothing, and a shared file in append
order is what pins WHICH of the three sites forked.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

#: ``sys.executable`` forward-slashed, for the reason
#: ``tests/oauth/test_resolve_config.py`` gives: the windows leg resolves
#: candidate 2 of the #227 chain — Git bash ``sh.exe`` — which eats backslashes
#: inside a quoted word. On POSIX there is no backslash to replace.
_PYTHON = sys.executable.replace("\\", "/")

_COUNTER_SOURCE = """\
import sys

with open(sys.argv[1], "a", encoding="utf-8") as handle:
    handle.write(sys.argv[2] + "\\n")
print("sk-" + sys.argv[2])
"""


def _slashed(path: Path) -> str:
    return str(path).replace("\\", "/")


def _counting_command(script: Path, marker: Path, tag: str) -> str:
    """A real ``!command`` that records ``tag`` in ``marker`` and prints a value."""

    argv = (_PYTHON, _slashed(script), _slashed(marker), tag)
    return "!" + " ".join(shlex.quote(part) for part in argv)


def _tags(marker: Path) -> list[str]:
    if not marker.exists():
        return []
    return marker.read_text(encoding="utf-8").split()


async def _myco_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ModelRegistry, Path]:
    """A registry over a NOVEL provider whose three auth sites are ``!command``.

    ``myco`` and not a built-in name, over an empty ``auth.json`` with
    ``MYCO_API_KEY`` deleted, because ``get_api_key_cascade`` consults the
    environment BEFORE the models.json ``apiKey``: a runner that happens to
    export the provider's key would silently skip the ``apiKey`` site and leave
    this case measuring two of the three.
    """

    monkeypatch.delenv("MYCO_API_KEY", raising=False)
    script = tmp_path / "counter.py"
    script.write_text(_COUNTER_SOURCE, encoding="utf-8")
    marker = tmp_path / "marker.txt"

    config: dict[str, Any] = {
        "providers": {
            "myco": {
                "baseUrl": "https://api.myco.test/v1",
                "api": "openai-completions",
                "apiKey": _counting_command(script, marker, "a"),
                "headers": {"X-Org": _counting_command(script, marker, "b")},
                "models": [
                    {
                        "id": "m1",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {
                            "input": 1.0,
                            "output": 2.0,
                            "cacheRead": 0.5,
                            "cacheWrite": 0.25,
                        },
                        "contextWindow": 1000,
                        "maxTokens": 100,
                        "headers": {"X-Model": _counting_command(script, marker, "c")},
                    }
                ],
            }
        }
    }

    path = tmp_path / "models.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    storage = AuthStorage(path=tmp_path / "auth.json")
    await storage.load()
    registry = ModelRegistry(storage, models_json_path=str(path))
    assert registry.get_error() is None, registry.get_error()
    return registry, marker


async def _resolve(registry: ModelRegistry) -> None:
    model = registry.find("myco", "m1")
    assert model is not None
    resolved = await registry.get_api_key_and_headers(model)
    assert resolved.ok is True, resolved.error
    assert resolved.api_key == "sk-a"
    assert resolved.headers == {"X-Org": "sk-b", "X-Model": "sk-c"}


async def test_the_three_resolution_sites_fork_once_per_load_not_per_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7 — the issue's headline claim, and all three ``cache=`` arguments.

    The tag order ``a, b, c`` is the resolution order inside
    ``get_api_key_and_headers``: ``apiKey``, then provider headers, then
    per-model headers. Asserting the whole list and then that it is BYTE-
    unchanged is what makes dropping any ONE of the three ``cache=`` arguments
    fail — a bare "spawn count went down" assertion stays green with two of
    three wired.
    """

    registry, marker = await _myco_registry(tmp_path, monkeypatch)

    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c"]

    before = marker.read_bytes()
    await _resolve(registry)
    assert marker.read_bytes() == before


async def test_refresh_drops_the_cached_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T8 — the invalidation seam a rotated credential needs.

    ``refresh()`` runs ``_load_models``, which rebuilds the request-config maps
    the cached values were derived from; the cache is cleared in the same place
    for the same reason. Without this a rotated key needs a process restart.
    """

    registry, marker = await _myco_registry(tmp_path, monkeypatch)

    await _resolve(registry)
    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c"]

    registry.refresh()
    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c", "a", "b", "c"]


async def test_registering_a_provider_drops_the_cached_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9 — a config change is never answered from a value that predates it."""

    registry, marker = await _myco_registry(tmp_path, monkeypatch)

    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c"]

    registry.register_provider("other", ProviderConfigInput(name="Other"))
    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c", "a", "b", "c"]


async def test_clear_config_value_cache_drops_the_values_without_a_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T10 — the cheap seam, and the proof that it stays cheap.

    ``/reload`` and the failed-turn handler call this; both would be wrong to
    pay for a ``models.json`` re-read and a re-run of every OAuth
    ``modify_models`` callback. Counting ``_load_models`` is what stops the
    method from quietly becoming an alias for ``refresh()``.
    """

    registry, marker = await _myco_registry(tmp_path, monkeypatch)

    loads = 0
    original = ModelRegistry._load_models

    def counting(self: ModelRegistry) -> None:
        nonlocal loads
        loads += 1
        original(self)

    monkeypatch.setattr(ModelRegistry, "_load_models", counting)

    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c"]

    registry.clear_config_value_cache()
    assert loads == 0, "the cheap seam reloaded models.json"

    await _resolve(registry)
    assert _tags(marker) == ["a", "b", "c", "a", "b", "c"]


async def test_two_registries_do_not_share_a_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T11 — the cache is per instance, not per process.

    A module-level dict (Pi's shape) would make the second registry answer from
    the first one's credential and would leak values between tests. Both
    registries read the same ``models.json``, so only the scope distinguishes
    them.
    """

    first, marker = await _myco_registry(tmp_path, monkeypatch)
    await _resolve(first)
    assert _tags(marker) == ["a", "b", "c"]

    storage = AuthStorage(path=tmp_path / "auth.json")
    await storage.load()
    second = ModelRegistry(storage, models_json_path=str(tmp_path / "models.json"))
    await _resolve(second)
    assert _tags(marker) == ["a", "b", "c", "a", "b", "c"]
