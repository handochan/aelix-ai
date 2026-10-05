"""#369 — project settings follow project trust (pi ``89a92207f``, ADR-0252).

pi @ ``b223082bb`` ``settings-manager.ts``: ``create(cwd, agentDir,
{projectTrusted})`` (:417-430, default trusted :442), ``loadFromStorage`` returns
``{}`` for an untrusted project BEFORE any storage access (:473-476),
``setProjectTrusted`` (:582-603), ``reload`` keeps the flag (:623), and every
project write asserts trust (:662-666, in ``saveProjectSettings`` :752,
``updateProjectSettings`` :769 and inside the queued task :683-694).

On ``5dee21d1`` ``SettingsManager`` had no trust state: ``create`` refused the
keyword (``TypeError``) and the project file was read and written whatever the
directory's trust.

The two aelix-original getters ``get_extension_sources`` /
``get_suppressed_default_catalogs`` read the GLOBAL scope only (their comments
always said so; the merged read let ``aelix extension source add`` copy a
repo's index into the user's file — ``tests/cli/test_project_settings_trust_369.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_ai.settings import (
    ExtensionSourceObject,
    InMemorySettingsStorage,
    Settings,
    SettingsManager,
)

_PAIR = {"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}
_REFUSAL = "Project is not trusted; refusing to write project settings"


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _project(settings_dirs: dict[str, Path], payload: object) -> Path:
    path = settings_dirs["project_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _create(settings_dirs: dict[str, Path], **kwargs: bool) -> SettingsManager:
    return SettingsManager.create(
        settings_dirs["project_dir"], settings_dirs["agent_dir"], **kwargs
    )


class _SpyStorage(InMemorySettingsStorage):
    def __init__(self) -> None:
        super().__init__()
        self.scopes: list[str] = []

    def with_lock(self, scope, fn):  # type: ignore[no-untyped-def, override]
        self.scopes.append(scope)
        super().with_lock(scope, fn)


def test_the_default_stays_trusted_as_in_pi(settings_dirs: dict[str, Path]) -> None:
    _project(settings_dirs, _PAIR)
    manager = _create(settings_dirs)
    assert manager.is_project_trusted() is True
    assert (manager.get_default_provider(), manager.get_default_model()) == (
        "openai",
        "gpt-4o-mini",
    )


def test_an_untrusted_manager_reads_no_project_settings(settings_dirs: dict[str, Path]) -> None:
    _project(settings_dirs, _PAIR)
    manager = _create(settings_dirs, project_trusted=False)
    assert manager.is_project_trusted() is False
    assert manager.get_default_provider() is None
    assert manager.get_default_model() is None
    assert manager.get_project_settings() == Settings()


def test_an_untrusted_project_file_is_never_opened() -> None:
    storage = _SpyStorage()
    storage.with_lock("project", lambda _current: json.dumps(_PAIR))
    storage.scopes.clear()
    manager = SettingsManager.from_storage(storage, project_trusted=False)
    assert storage.scopes == ["global"]
    assert manager.get_default_model() is None
    manager.set_project_trusted(True)
    assert storage.scopes == ["global", "project"]
    assert manager.get_default_model() == "gpt-4o-mini"


def test_in_memory_takes_the_flag_too() -> None:
    assert SettingsManager.in_memory({}, project_trusted=False).is_project_trusted() is False


def test_set_project_trusted_adds_and_removes_the_project_scope(
    settings_dirs: dict[str, Path],
) -> None:
    _project(settings_dirs, _PAIR)
    manager = _create(settings_dirs, project_trusted=False)
    manager.set_project_trusted(True)
    assert (manager.get_default_provider(), manager.get_default_model()) == (
        "openai",
        "gpt-4o-mini",
    )
    manager.set_project_trusted(False)
    assert manager.get_default_provider() is None
    assert manager.get_project_settings() == Settings()


def test_a_malformed_untrusted_file_reports_nothing_until_trusted(
    settings_dirs: dict[str, Path],
) -> None:
    _project(settings_dirs, "{not json")
    manager = _create(settings_dirs, project_trusted=False)
    assert manager.drain_errors() == []
    manager.set_project_trusted(True)
    errors = manager.drain_errors()
    assert [e.scope for e in errors] == ["project"]
    # Unchanged value: no second read, no second error.
    manager.set_project_trusted(True)
    assert manager.drain_errors() == []


@pytest.mark.parametrize(
    ("setter", "value"),
    [
        ("set_project_packages", ["npm:planted"]),
        ("set_project_extension_paths", ["planted.py"]),
        ("set_project_skill_paths", ["planted"]),
        ("set_project_prompt_template_paths", ["planted"]),
        ("set_project_theme_paths", ["planted"]),
    ],
)
async def test_an_untrusted_project_is_never_written(
    settings_dirs: dict[str, Path], setter: str, value: object
) -> None:
    path = _project(settings_dirs, _PAIR)
    before = path.read_text(encoding="utf-8")
    manager = _create(settings_dirs, project_trusted=False)
    with pytest.raises(RuntimeError, match=_REFUSAL):
        getattr(manager, setter)(value)
    await manager.flush()
    assert path.read_text(encoding="utf-8") == before
    assert manager.get_project_settings() == Settings()


async def test_a_write_queued_while_trusted_is_refused_after_trust_is_withdrawn(
    settings_dirs: dict[str, Path],
) -> None:
    path = _project(settings_dirs, _PAIR)
    before = path.read_text(encoding="utf-8")
    manager = _create(settings_dirs)
    manager.set_project_extension_paths(["queued.py"])
    manager.set_project_trusted(False)
    await manager.flush()
    assert path.read_text(encoding="utf-8") == before
    errors = manager.drain_errors()
    assert [(e.scope, str(e.error)) for e in errors] == [("project", _REFUSAL)]


async def test_a_trusted_project_is_still_written(settings_dirs: dict[str, Path]) -> None:
    path = _project(settings_dirs, _PAIR)
    manager = _create(settings_dirs, project_trusted=False)
    manager.set_project_trusted(True)
    manager.set_project_extension_paths(["mine.py"])
    await manager.flush()
    assert json.loads(path.read_text(encoding="utf-8"))["extensions"] == ["mine.py"]
    assert manager.drain_errors() == []


async def test_reload_keeps_an_untrusted_project_empty(settings_dirs: dict[str, Path]) -> None:
    path = _project(settings_dirs, {})
    manager = _create(settings_dirs, project_trusted=False)
    path.write_text(json.dumps(_PAIR), encoding="utf-8")
    await manager.reload()
    assert manager.get_default_model() is None
    manager.set_project_trusted(True)
    await manager.reload()
    assert manager.get_default_model() == "gpt-4o-mini"


@pytest.mark.parametrize("trusted", [True, False], ids=["trusted", "untrusted"])
def test_install_sources_are_read_from_global_settings_only(
    settings_dirs: dict[str, Path], trusted: bool
) -> None:
    _project(
        settings_dirs,
        {
            "extensionSources": [{"spec": "https://repo-index.invalid/simple", "kind": "index"}],
            "suppressedDefaultCatalogs": ["handochan.github.io/aelix-marketplace/catalog.json"],
        },
    )
    settings_dirs["global_path"].write_text(
        json.dumps({"extensionSources": [{"spec": "/mine", "kind": "path"}]}), encoding="utf-8"
    )
    manager = _create(settings_dirs, project_trusted=trusted)
    assert manager.get_extension_sources() == [ExtensionSourceObject(spec="/mine", kind="path")]
    assert manager.get_suppressed_default_catalogs() == []
