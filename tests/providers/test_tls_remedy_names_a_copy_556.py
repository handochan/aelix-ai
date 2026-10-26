"""#556 - the corporate-CA remedy's last line names a copy of certifi's bundle.

On 27fa21c5 the untrusted-issuer remedy ended, on every platform::

    or append the corporate CA to the bundle printed by `python -m certifi`, then retry.

``python -m certifi`` prints the path of the ``cacert.pem`` inside the installed
certifi package, so the line reads as "edit that file". The TLS guide warns
against exactly that: reinstalling or upgrading certifi replaces the file and
the failure comes back with no clue. Measured (``.omc/probes/b4-a-live/556/``):
a probe CA appended to certifi's ``cacert.pem`` in a throwaway venv loaded
(121 CAs), and after ``uv pip install --reinstall certifi`` it was gone (120).
The guide's recipe makes a copy and points ``SSL_CERT_FILE`` at it.

Now the line says so. Where the printed example names ``ca-bundle.pem`` (macOS,
Windows, every system but Linux) it says what that file is - the guide's copy;
on Linux, whose example is the system's own bundle, the copy is the
alternative. The remedy keeps #411's per-platform examples and its five lines.
Since #573, the source is the running Aelix process's ``aelix status`` bundle,
not a certifi installation found through the user's PATH Python.
"""

from __future__ import annotations

import re
import ssl
import sys
from pathlib import Path

import pytest
from aelix_ai.providers import _error_hints
from aelix_ai.providers._error_hints import describe_provider_error
from aelix_ai.utils.terminal_text import safe_error_for_terminal

REPO = Path(__file__).resolve().parent.parent.parent
GUIDES = (
    REPO / "docs" / "guides" / "tls-and-corporate-ca.md",
    REPO / "packages" / "aelix-coding-agent" / "src" / "aelix_coding_agent" / "docs" / "tls-and-corporate-ca.md",
)

_COPY = (
    "a copy of the certifi bundle shown by `aelix status` with the corporate CA appended "
    "(not certifi's own file, which an upgrade silently reverts), then retry."
)

#: (``sys.platform``, the example line #411 prints, the remedy's last line).
_PLATFORMS: list[tuple[str, str, str]] = [
    ("linux", "  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt", f"or at {_COPY}"),
    ("darwin", '  export SSL_CERT_FILE="$HOME/.aelix/ca-bundle.pem"', f"where ca-bundle.pem is {_COPY}"),
    ("win32", '  $env:SSL_CERT_FILE = "$HOME\\.aelix\\ca-bundle.pem"', f"where ca-bundle.pem is {_COPY}"),
    ("freebsd14", '  export SSL_CERT_FILE="$HOME/.aelix/ca-bundle.pem"', f"where ca-bundle.pem is {_COPY}"),
]


def _ca_remedy(platform: str, os_store: bool, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The lines ``aelix`` prints for an untrusted issuer on ``platform``."""

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(_error_hints, "_os_trust_store_active", lambda: os_store)
    exc = ssl.SSLCertVerificationError("unable to get local issuer certificate")
    exc.verify_code = 20  # type: ignore[attr-defined]
    full = describe_provider_error(exc)
    monkeypatch.undo()
    assert safe_error_for_terminal(full) == full, full  # whole in the TUI
    return full.split("\n")


@pytest.mark.parametrize("os_store", [True, False], ids=["os-store", "certifi"])
@pytest.mark.parametrize(("platform", "example", "last"), _PLATFORMS, ids=[p[0] for p in _PLATFORMS])
def test_the_last_line_names_a_copy_never_certifi_s_own_file(
    platform: str, example: str, last: str, os_store: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sentence is pinned, and so is what makes it right: it names a copy,
    warns off certifi's own file, and points to Aelix's own diagnostic rather
    than a PATH Python. RED on 27fa21c5 and the pre-#573 wording."""

    lines = _ca_remedy(platform, os_store, monkeypatch)

    assert lines[-1] == last, "\n".join(lines)
    assert "a copy of the certifi bundle shown by `aelix status`" in lines[-1]
    assert "not certifi's own file" in lines[-1]
    text = "\n".join(lines)
    assert "append the corporate CA to the bundle" not in text, text
    assert "python -m certifi" not in text, text


@pytest.mark.parametrize("os_store", [True, False], ids=["os-store", "certifi"])
@pytest.mark.parametrize(("platform", "example", "last"), _PLATFORMS, ids=[p[0] for p in _PLATFORMS])
def test_the_last_line_fits_the_example_above_it(
    platform: str, example: str, last: str, os_store: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the example names ``ca-bundle.pem``, a file the user makes, the last
    line says what it is; on Linux, whose example names the system's bundle, the
    copy is the alternative and ``ca-bundle.pem`` is not mentioned. #411's
    examples stay as they were, one indented line in a five-line remedy."""

    lines = _ca_remedy(platform, os_store, monkeypatch)

    assert [line for line in lines if line.startswith("  ")] == [example], "\n".join(lines)
    assert lines[-2] == example, "\n".join(lines)
    if example.endswith('ca-bundle.pem"'):
        assert lines[-1].startswith("where ca-bundle.pem is a copy of"), lines[-1]
    else:
        assert lines[-1].startswith("or at a copy of"), lines[-1]
        assert "ca-bundle.pem" not in "\n".join(lines)
    # The error, a blank line, then the remedy: five lines of at most 200.
    assert len(lines) == 7 and lines[1] == "", "\n".join(lines)
    assert max(len(line) for line in lines) <= 200, "\n".join(lines)


def _recipe_targets(guide: str) -> dict[str, str]:
    """The file each of the guide's two copy recipes writes, by shell."""

    sh = re.search(r'cat "\$certifi_bundle" corp-root\.pem > (.+)\n', guide)
    ps = re.search(
        r"Get-Content -LiteralPath \$aelixCertifiBundle, 'corp-root\.pem' \| Set-Content -Encoding ascii (.+)\n", guide
    )
    assert sh and ps, "the guide's copy recipes are gone"
    return {"sh": sh.group(1), "powershell": ps.group(1)}


@pytest.mark.parametrize("guide", GUIDES, ids=["docs-guides", "bundled"])
def test_the_guide_makes_the_copy_the_remedy_names(guide: Path) -> None:
    """The guide's recipes write the copy at the path the printed examples set
    (only reading certifi's file), and its prose says "make a copy" rather than
    "append the corporate CA to the one `python -m certifi` prints", the same
    reading #556 fixed in the remedy. RED on 27fa21c5 for the prose."""

    text = guide.read_text(encoding="utf-8")
    flat = " ".join(text.split())

    targets = _recipe_targets(text)
    assert targets["sh"] == '"$HOME/.aelix/ca-bundle.pem"', targets
    assert targets["powershell"] == '"$HOME\\.aelix\\ca-bundle.pem"', targets
    by_platform = {p: e for p, e, _ in _PLATFORMS}
    assert by_platform["darwin"].endswith('"$HOME/.aelix/ca-bundle.pem"')  # = ~/.aelix/ca-bundle.pem
    assert by_platform["win32"].endswith(targets["powershell"])

    assert "make a **copy** of Aelix\'s certifi bundle with the corporate CA appended" in flat
    assert "append the corporate CA to the one" not in flat
    assert "Do not edit `certifi`'s own file in place" in flat
