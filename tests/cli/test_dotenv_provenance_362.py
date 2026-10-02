"""#362 / ADR-0250 guard 1 — ``load_dotenv`` records which names a cwd ``.env`` supplied.

The record (``AELIX_DOTENV_ADMITTED``) is what lets every route-deciding judgement
leave a planted key out (``ModelRegistry.has_route_auth``), in this process and in
every child that inherits the environment. These pin its contract:

* it holds exactly the names the file supplied — credentials, configuration,
  hatched names — in the spelling ``os.environ`` stores them;
* it UNIONS an inherited record and leaves it alone when there is no ``.env``;
* a ``.env`` can neither set it nor open it with ``AELIX_DOTENV_ALLOW``;
* a ``.env`` key that is not a plain environment name is refused, so the
  comma-joined record cannot be poisoned (critique M2(a): ``X,ANTHROPIC_API_KEY``
  marked the user's EXPORTED key as planted);
* on Windows a mixed-case line names the upper-cased variable, and so does the
  record (critique M2(b)).

Fake values; nothing leaves the process.
"""

from __future__ import annotations

import os
import re
import types
from pathlib import Path

import pytest
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

_RECORD = "AELIX_DOTENV_ADMITTED"


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV", "GOOGLE_CLOUD", "CLOUDFLARE_")
        ):
            monkeypatch.delenv(name)
    return monkeypatch


def _load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]
) -> str:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    for line in text.splitlines():
        name = line.partition("=")[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name not in os.environ:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
    if _RECORD not in os.environ:
        monkeypatch.setenv(_RECORD, "")
        monkeypatch.delenv(_RECORD)
    load_dotenv(str(path))
    return capsys.readouterr().err


def test_the_record_holds_every_name_the_file_supplied(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean.setenv("AELIX_DOTENV_ALLOW", "PI_OFFLINE")
    _load(
        clean,
        tmp_path,
        "OPENROUTER_API_KEY=a\nGOOGLE_CLOUD_PROJECT=my-gcp-project\nPI_OFFLINE=1\n"
        "NOT_A_CREDENTIAL=x\n",
        capsys,
    )
    clean.delenv("PI_OFFLINE", raising=False)
    assert os.environ[_RECORD] == "GOOGLE_CLOUD_PROJECT,OPENROUTER_API_KEY,PI_OFFLINE"


def test_an_exported_name_never_enters_the_record(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``setdefault``: the user's own value wins, and it is not the file's."""

    clean.setenv("ANTHROPIC_API_KEY", "ant-shell-fake")
    _load(clean, tmp_path, "ANTHROPIC_API_KEY=planted\nOPENROUTER_API_KEY=planted\n", capsys)
    assert os.environ["ANTHROPIC_API_KEY"] == "ant-shell-fake"
    assert os.environ[_RECORD] == "OPENROUTER_API_KEY"


def test_the_record_unions_an_inherited_one(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A delegated child, started in another project, keeps its parent's marks too."""

    clean.setenv(_RECORD, "OPENAI_API_KEY")
    clean.setenv("OPENAI_API_KEY", "from-the-parents-dotenv")
    _load(clean, tmp_path, "OPENROUTER_API_KEY=planted\nOPENAI_API_KEY=again\n", capsys)
    assert os.environ[_RECORD] == "OPENAI_API_KEY,OPENROUTER_API_KEY"


def test_a_child_rereading_the_same_dotenv_keeps_the_record(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """It admits nothing (the keys are inherited), so an overwrite would erase the mark."""

    _load(clean, tmp_path, "OPENROUTER_API_KEY=planted\n", capsys)
    _load(clean, tmp_path, "OPENROUTER_API_KEY=planted\n", capsys)
    assert os.environ[_RECORD] == "OPENROUTER_API_KEY"


def test_no_dotenv_leaves_an_inherited_record_alone(
    clean: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean.setenv(_RECORD, "OPENROUTER_API_KEY")
    load_dotenv(str(tmp_path / "absent.env"))
    assert os.environ[_RECORD] == "OPENROUTER_API_KEY"


@pytest.mark.parametrize("hatch", [None, _RECORD], ids=["plain", "hatch-names-it"])
def test_a_dotenv_cannot_set_the_record(
    clean: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    hatch: str | None,
) -> None:
    """``^AELIX_`` alone did not hold: on ``9ca53a4f`` the hatch admitted it.

    A ``.env`` that could write the record could mark the user's exported key as
    planted — or, with an empty value, erase a parent's mark.
    """

    if hatch:
        clean.setenv("AELIX_DOTENV_ALLOW", hatch)
    clean.setenv("ANTHROPIC_API_KEY", "ant-shell-fake")
    err = _load(clean, tmp_path, f"{_RECORD}=ANTHROPIC_API_KEY\n", capsys)
    assert os.environ.get(_RECORD) is None
    assert (
        f"Notice: ignored {_RECORD} from {tmp_path / '.env'} — aelix writes it to record "
        "which credentials came from a project .env; a .env cannot set it."
    ) in err


@pytest.mark.parametrize(
    "key", ["X,ANTHROPIC_API_KEY", "MY KEY_API_KEY", "9LIVES_API_KEY", "A-B_TOKEN", "A.B_KEY"]
)
def test_a_key_that_is_not_an_environment_name_is_refused(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str
) -> None:
    """Critique M2(a): the comma row admitted ``X,ANTHROPIC_API_KEY`` and poisoned the record."""

    clean.setenv("ANTHROPIC_API_KEY", "ant-shell-fake")
    clean.setenv("AELIX_DOTENV_ALLOW", key)  # the hatch cannot name it either
    err = _load(clean, tmp_path, f"{key}=junk\nOPENROUTER_API_KEY=planted\n", capsys)
    assert key not in os.environ
    assert os.environ[_RECORD] == "OPENROUTER_API_KEY"
    assert "are not environment variable names" in err


def test_the_poisoned_record_cannot_reopen_the_planted_capture(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Critique P1 at the resolver: the user's exported Anthropic key still counts."""

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_model

    clean.setenv("ANTHROPIC_API_KEY", "ant-shell-fake")
    _load(clean, tmp_path, "OPENROUTER_API_KEY=planted\nX,ANTHROPIC_API_KEY=junk\n", capsys)
    assert resolve_model("anthropic/claude-haiku-4.5", None, None).provider == "anthropic"


class _NtEnviron(os._Environ):  # type: ignore[name-defined]
    """CPython's ``nt`` ``os.environ`` (``os.py`` ``_createenviron``): names upper-cased.

    Without ``putenv`` — the emulation must not reach the real process environment.
    """

    def __setitem__(self, key: str, value: str) -> None:
        self._data[self.encodekey(key)] = self.encodevalue(value)

    def __delitem__(self, key: str) -> None:
        del self._data[self.encodekey(key)]


def test_on_windows_a_mixed_case_line_is_recorded_as_the_variable_it_set(
    clean: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Critique M2(b), emulated: ``OpenRouter_API_KEY=…`` sets ``OPENROUTER_API_KEY`` on nt.

    Recording the file's spelling would miss it, and the planted capture (A10)
    reopened under Windows semantics on the design's spec. Not run on a real
    Windows here; the windows CI leg runs it natively too (where ``os.name`` is
    already ``nt``).
    """

    import aelix_coding_agent.core.dotenv_provenance as provenance
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_model

    data = {k.upper(): v for k, v in os.environ.items()}
    nt_environ = _NtEnviron(data, str.upper, str, str, str)
    clean.setattr(os, "environ", nt_environ)
    clean.setattr(provenance, "os", types.SimpleNamespace(name="nt", environ=nt_environ))
    nt_environ["ANTHROPIC_API_KEY"] = "ant-shell-fake"
    path = tmp_path / ".env"
    path.write_text("OpenRouter_API_KEY=or-dotenv-planted\n", encoding="utf-8")
    load_dotenv(str(path))
    assert nt_environ.get("OPENROUTER_API_KEY") == "or-dotenv-planted"
    assert nt_environ[_RECORD] == "OPENROUTER_API_KEY"
    model = resolve_model("anthropic/claude-haiku-4.5", None, None)
    assert model.provider == "anthropic"


def test_openrouter_default_model_is_no_longer_in_the_config_arm() -> None:
    from aelix_coding_agent.cli.runtime_bootstrap import _DOTENV_CONFIG_VALUES

    assert "OPENROUTER_DEFAULT_MODEL" not in _DOTENV_CONFIG_VALUES
    assert sorted(_DOTENV_CONFIG_VALUES) == [
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_GATEWAY_ID",
        "GCLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_PROJECT",
    ]


def test_a_malformed_inherited_record_yields_only_names() -> None:
    """``parse_record``'s name filter, pinned on the function (the first verification's V2).

    No ``.env`` line reaches it — the loader refuses the record's name and
    ``format_record`` writes names only — so this is the one row that can: a
    record the user's own environment hands down malformed. An empty part, a
    stray space or a ``KEY=value`` fragment names no variable.
    """

    from aelix_coding_agent.core.dotenv_provenance import parse_record

    assert parse_record("OPENAI_API_KEY,, ,X Y,K=v,ANTHROPIC_API_KEY ") == frozenset(
        {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    )
    assert parse_record("") == frozenset() and parse_record(None) == frozenset()
