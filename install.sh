#!/bin/sh
# Aelix installer — POSIX sh, no bashisms.
#
#   curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
#
# It downloads the release wheels from the GitHub Release, verifies each one
# against the published SHA256SUMS manifest (a hard security gate — any
# mismatch aborts), then installs the `aelix` CLI with uv, PINNED to the exact
# version named by the verified manifest. The pin is what makes the gate
# meaningful: it is the reason the wheels uv installs are the wheels this
# script just verified, rather than whatever an index happens to offer under
# the same name. Third-party dependencies still resolve from PyPI as usual;
# the four first-party wheels come from the checksum-verified download
# (uv --find-links, never --no-index).
#
# Configuration (all optional, via environment):
#   AELIX_VERSION  Pin an exact release tag (e.g. v0.1.0). Default: resolve
#                  the newest release from the GitHub API, a pre-release
#                  included.
#   AELIX_EXTRAS   Extras to install, consumed as aelix[$AELIX_EXTRAS].
#                  Default `tui` (interactive terminal UI), or empty for the
#                  bare CLI.
#   AELIX_REPO     GitHub owner/repo. Default `handochan/aelix-ai`.
#   AELIX_PYTHON   uv interpreter request for the tool environment. Default:
#                  chosen from the release being installed (Step 5, #278) —
#                  `>=3.11,<3.15` for 0.1.0 and later, which pin
#                  openai>=2.7.2 and run on 3.14; `>=3.11,<3.14` for 0.1.0b2
#                  and earlier, whose pinned openai<2.0 dies mid-turn on 3.14
#                  (#262). CI runs 3.11 through 3.14, each a test leg on ubuntu
#                  and windows, and tests/test_ci_python_matrix.py holds the
#                  matrix equal to the newer range.
#                  Set it to override, e.g. AELIX_PYTHON=3.12. An EMPTY
#                  value means the default, not "no constraint" — see
#                  Step 0. Widen it explicitly: AELIX_PYTHON='>=3.11'.
#   UV_VERSION     Optional pin for the uv bootstrap (Astral installer).
#   GITHUB_TOKEN   Optional; sent as a Bearer token on GitHub API calls to
#                  avoid the 60/hr unauthenticated rate limit.

set -eu

# ── Step 0: preamble ────────────────────────────────────────────────────────
AELIX_VERSION="${AELIX_VERSION-}"
AELIX_EXTRAS="${AELIX_EXTRAS-tui}"
AELIX_REPO="${AELIX_REPO-handochan/aelix-ai}"
# EMPTY MEANS THE DEFAULT for this one knob, and this is the one place in this
# file where that matters. `uv tool install --python ""` does not fail — uv
# IGNORES an empty request and goes back to picking the newest interpreter
# (measured: exit 0, and the environment lands on 3.14.5, which for a release
# up to 0.1.0b2 is the exact state Step 5 exists to prevent). The sibling knobs
# keep a set-but-empty value because it is MEANINGFUL for them (`AELIX_EXTRAS=`
# installs the bare CLI, and the README teaches that spelling), so anyone
# copying that habit onto this variable would silently disarm the gate. Until
# #278 this line was `${AELIX_PYTHON:->=3.11,<3.14}`; the default now depends on
# the release, which is not known until Step 4, so this line only reads the
# knob and Step 5's `[ -n "$AELIX_PYTHON" ] ||` fills an unset OR empty value.
# That is also what install.ps1 does — `if ($env:AELIX_PYTHON)` is false for an
# empty string. To genuinely widen the range, say so: AELIX_PYTHON='>=3.11'.
AELIX_PYTHON="${AELIX_PYTHON-}"
# The two default requests Step 5 chooses between (#278, ADR-0265). Releases
# from 0.1.0 on pin openai>=2.7.2,<3 and run on 3.11 through 3.14, each a CI
# leg. 0.1.0b2 and earlier pin openai<2.0, which dies mid-turn on 3.14 (#262),
# and AELIX_VERSION can still install them.
py_supported='>=3.11,<3.15'
py_before_b3='>=3.11,<3.14'
UV_VERSION="${UV_VERSION-}"
GITHUB_TOKEN="${GITHUB_TOKEN-}"

log() { printf '%s\n' "aelix-install: $*"; }
err() { printf '%s\n' "aelix-install: $*" >&2; }
die() { err "ERROR: $*"; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT INT TERM

# Portable download shim (plain, no auth header): curl preferred, wget fallback.
dl() {
  _url="$1"
  _out="$2"
  if have curl; then
    curl -fSL --retry 3 -o "$_out" "$_url"
  else
    wget -qO "$_out" "$_url"
  fi
}

# GitHub API GET with JSON accept header and optional Bearer token.
api_get() {
  _url="$1"
  _out="$2"
  if have curl; then
    if [ -n "$GITHUB_TOKEN" ]; then
      curl -fSL -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GITHUB_TOKEN" -o "$_out" "$_url"
    else
      curl -fSL -H "Accept: application/vnd.github+json" -o "$_out" "$_url"
    fi
  else
    if [ -n "$GITHUB_TOKEN" ]; then
      wget -q --header="Accept: application/vnd.github+json" \
        --header="Authorization: Bearer $GITHUB_TOKEN" -O "$_out" "$_url"
    else
      wget -q --header="Accept: application/vnd.github+json" -O "$_out" "$_url"
    fi
  fi
}

# Verify a checksum manifest subset (read on stdin) against files in $tmp.
sha_check_stdin() {
  if have sha256sum; then
    ( cd "$tmp" && sha256sum -c - )
  else
    ( cd "$tmp" && shasum -a 256 -c - )
  fi
}

# python_request_for <version>: the default interpreter request for the aelix
# release <version> (the PEP 440 string Step 5 reads from the verified
# SHA256SUMS, e.g. 0.1.0). The wider range only for a version this can PROVE
# is 0.1.0b3 or later (no 0.1.0b3 was published: 0.1.0 is the first release
# past 0.1.0b2) — N.N.N with an optional aN / bN / rcN, compared as
# numbers, so 0.1.0b10 is after 0.1.0b3. Anything else (a dev or post release,
# a local version, a typo) keeps the older range, which every release ever
# published runs on: failing closed costs 3.14, failing open costs #262.
python_request_for() {
  printf '%s\n' "$1" | awk -v new="$py_supported" -v old="$py_before_b3" '
    $0 !~ /^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$/ { print old; exit }
    {
      split($0, f, ".")
      match(f[3], /^[0-9]+/)
      patch = substr(f[3], 1, RLENGTH)
      pre = substr(f[3], RLENGTH + 1)
      kind = 3; num = 0
      if (pre ~ /^a/) { kind = 0; num = substr(pre, 2) }
      if (pre ~ /^b/) { kind = 1; num = substr(pre, 2) }
      if (pre ~ /^rc/) { kind = 2; num = substr(pre, 3) }
      split(f[1] " " f[2] " " patch " " kind " " num, got, " ")
      split("0 1 0 1 3", want, " ")
      for (i = 1; i <= 5; i++) {
        if (got[i] + 0 > want[i] + 0) { print new; exit }
        if (got[i] + 0 < want[i] + 0) { print old; exit }
      }
      print new
    }'
}

# ── Step 1: prerequisites ───────────────────────────────────────────────────
if ! have curl && ! have wget; then
  die "need 'curl' or 'wget'. Install one, e.g. 'apt-get install curl' or 'brew install curl'."
fi
if ! have sha256sum && ! have shasum; then
  die "need 'sha256sum' or 'shasum'. On Linux: 'apt-get install coreutils'; macOS ships 'shasum'."
fi

# ── Step 2: uv bootstrap (idempotent) ───────────────────────────────────────
if have uv; then
  log "uv already installed ($(command -v uv))."
else
  log "installing uv (Astral)..."
  _uv_installer="$tmp/uv-install.sh"
  dl "https://astral.sh/uv/install.sh" "$_uv_installer" \
    || die "failed to download the uv installer."
  # INSTALLER_NO_MODIFY_PATH is left unset so the installer wires PATH; honor
  # an optional UV_VERSION pin via the installer's version variable.
  if [ -n "$UV_VERSION" ]; then
    UV_INSTALL_VERSION="$UV_VERSION" sh "$_uv_installer" || die "uv install failed."
  else
    sh "$_uv_installer" || die "uv install failed."
  fi
  # Make uv visible to THIS process. Look where the Astral installer actually
  # put it, in ITS order (uv's install.sh, `install()`): UV_INSTALL_DIR, then
  # CARGO_DIST_FORCE_INSTALL_DIR, then UV_UNMANAGED_INSTALL (each a flat dir,
  # or <dir>/bin when it is a cargo home), then XDG_BIN_HOME, then
  # XDG_DATA_HOME/../bin, then ~/.local/bin. Until #279 this looked only at
  # XDG_BIN_HOME and ~/.local/bin, so a custom XDG_DATA_HOME or UV_INSTALL_DIR
  # left uv installed and this script dying with "uv still not found" while
  # naming a directory uv was never put in (measured: XDG_DATA_HOME=~/data puts
  # uv in ~/bin). ~/.local/bin is spelled ${HOME:+...} so this search never
  # expands an unset HOME under `set -u`: a HOME-less environment that names
  # the dir uv went to gets past it. Before #279 that held for XDG_BIN_HOME
  # only; the old ${XDG_BIN_HOME:-$HOME/.local/bin} expanded HOME whenever
  # XDG_BIN_HOME was unset, so UV_INSTALL_DIR without HOME died under set -u
  # (bash: "HOME: unbound variable"; dash: "HOME: parameter not set").
  _uv_bin=""
  _uv_tried=""
  for _d in "${UV_INSTALL_DIR-}" "${CARGO_DIST_FORCE_INSTALL_DIR-}" \
            "${UV_UNMANAGED_INSTALL-}" "${XDG_BIN_HOME-}" \
            "${XDG_DATA_HOME:+$XDG_DATA_HOME/../bin}" "${HOME:+$HOME/.local/bin}"; do
    [ -n "$_d" ] || continue
    for _c in "$_d" "$_d/bin"; do
      _uv_tried="$_uv_tried '$_c'"
      if [ -x "$_c/uv" ]; then _uv_bin="$_c"; break 2; fi
    done
  done
  [ -n "$_uv_bin" ] || die "uv still not found after install (looked in:$_uv_tried); add the directory the uv installer named above to PATH and re-run."
  PATH="$_uv_bin:$PATH"
  export PATH
  have uv || die "uv still not found after install; add '$_uv_bin' to PATH and re-run."
fi

# ── Step 3: resolve the release tag ─────────────────────────────────────────
if [ -n "$AELIX_VERSION" ]; then
  tag="$AELIX_VERSION"
  log "using pinned release tag: $tag"
else
  log "resolving the newest release from GitHub..."
  api_get "https://api.github.com/repos/$AELIX_REPO/releases" "$tmp/releases.json" \
    || die "failed to query the GitHub releases API for '$AELIX_REPO'.
  Unauthenticated GitHub API calls are limited to 60/hr PER IP, so this is
  usually an HTTP 403 rate-limit on a shared address (CI runner, corporate
  NAT, VPN) rather than a missing repo. Two escapes, either one is enough:
    - set GITHUB_TOKEN=<a token> to lift the limit (any scope; a fine-grained
      token with no permissions works, the releases list is public), or
    - set AELIX_VERSION=vX.Y.Z to pin the tag and skip this API call entirely
      (the download + checksum steps below use no API at all)."
  # The list endpoint is newest-first and INCLUDES pre-releases (unlike
  # /releases/latest), so the first tag_name is the newest release, a
  # pre-release included.
  tag="$(grep -m1 '"tag_name"' "$tmp/releases.json" | sed 's/.*: *"\(.*\)".*/\1/')"
  [ -n "$tag" ] || die "could not resolve a release tag; pin one with AELIX_VERSION=vX.Y.Z."
  log "newest release tag: $tag"
fi

# ── Step 4: download + verify (the integrity gate) ──────────────────────────
base="https://github.com/$AELIX_REPO/releases/download/$tag"

log "downloading SHA256SUMS..."
dl "$base/SHA256SUMS" "$tmp/SHA256SUMS" \
  || die "SHA256SUMS not found for '$tag' at $base — is the Release published?"

# The four first-party wheels are pure py3-none-any; sdists are not needed.
awk '$2 ~ /^aelix.*\.whl$/ {print $2}' "$tmp/SHA256SUMS" > "$tmp/.wheels"
[ -s "$tmp/.wheels" ] || die "no 'aelix*.whl' entries in SHA256SUMS for '$tag'."

while IFS= read -r name; do
  [ -n "$name" ] || continue
  log "downloading $name..."
  dl "$base/$name" "$tmp/$name" || die "failed to download $name from $base."
  _line="$(grep "  ${name}\$" "$tmp/SHA256SUMS")" \
    || die "SECURITY: $name is absent from SHA256SUMS; aborting."
  if printf '%s\n' "$_line" | sha_check_stdin >/dev/null; then
    log "verified $name"
  else
    die "SECURITY: checksum mismatch for $name; aborting."
  fi
done < "$tmp/.wheels"

# ── Step 5: install (hybrid: local verified wheels + PyPI for the rest) ──────
# The version pin below is LOAD-BEARING, not cosmetic. --find-links only ADDS
# candidates; the PyPI index stays enabled (third-party deps need it), and uv
# then resolves the best candidate across BOTH sources. Requesting the bare
# name `aelix` therefore lets a PyPI release of that name outrank the local
# wheels — and the SHA256SUMS gate in Step 4 would have verified artifacts that
# this very command discards. Pinning to the exact version named by the
# verified manifest is what closes that gap: only the checksum-verified wheel
# can satisfy `==$version`.
#
# The version is parsed from the wheel FILENAME, never from the tag: a tag is
# `v0.1.0-beta.1` while PEP 440 normalizes the same release to `0.1.0b1`, so
# the tag is not a usable version specifier. The meta-package wheel is
# `aelix-<VER>-py3-none-any.whl`; its siblings escape the hyphen in their
# distribution name to an underscore (`aelix_ai-…`, `aelix_agent_core-…`,
# `aelix_coding_agent-…`), so an `aelix-` prefix matches the meta-package
# alone. A PEP 440 version can never itself contain a hyphen, which is what
# makes the `-`-delimited field split unambiguous.
version="$(awk '$2 ~ /^aelix-[^-]+-py3-none-any\.whl$/ {
                  split($2, f, "-"); print f[2]; exit
                }' "$tmp/SHA256SUMS")"
[ -n "$version" ] || die "could not parse the aelix version from SHA256SUMS for '$tag' (no 'aelix-<version>-py3-none-any.whl' entry)."

if [ -n "$AELIX_EXTRAS" ]; then
  target="aelix[$AELIX_EXTRAS]==$version"
else
  target="aelix==$version"
fi

# The interpreter request: AELIX_PYTHON when it is set and non-empty, else the
# default for THIS release (see WHICH RANGE below).
[ -n "$AELIX_PYTHON" ] || AELIX_PYTHON="$(python_request_for "$version")"

log "installing $target with uv (version pinned from the verified SHA256SUMS)..."
log "interpreter request: $AELIX_PYTHON"
# --find-links ADDS the four checksum-verified local wheels as candidates; the
# default PyPI index stays enabled so third-party dependencies resolve. Never
# use --no-index (it would make transitive deps unresolvable). --force makes
# re-runs idempotent.
#
# --python IS THE INTERPRETER GATE (#263). `uv tool install` consults NEITHER
# .python-version (3.12) NOR uv.lock — it resolves an interpreter fresh, and it
# takes the NEWEST one it can find. Measured on a box carrying 3.11 through
# 3.14: the unflagged `uv tool install --force aelix==0.1.0b2` built its
# environment on Python 3.14.5, an interpreter CI has never executed. There
# `openai<2.0` dies, because 3.14 made `typing.Union[...]` slotted while
# openai/_models.py:697 still writes an attribute onto it:
#
#   AttributeError: 'typing.Union' object has no attribute '__discriminator__'
#                   and no __dict__ for setting new attributes          (#262)
#
# That failure is LATENT, which is why no smoke test caught it: construct_type
# validates the union through pydantic FIRST and only falls through to that
# write when validation fails. So `-p "say OK"` SUCCEEDS on 3.14 and a real
# agent turn does not — measured both ways against the model in #262.
#
# A RANGE, not `--python 3.13`: a single version forces a download even on a
# box where 3.12 is installed and fine. With the range uv takes any local
# interpreter inside it, and downloads only when it has none (measured both
# ways, on the 3.11-3.13 range).
#
# THIS FLAG IS PERMANENT, NOT A STOPGAP. An earlier draft of this comment said
# the opposite — that #192's `requires-python` bound would retire it — and that
# was measured on the wrong path and is false. `uv tool install` chooses the
# interpreter BEFORE it resolves, so a published wheel's own Requires-Python
# ceiling never steers it. Against a wheel declaring `Requires-Python:
# <3.14,>=3.11`, on a box carrying 3.11 through 3.14:
#
#   uv tool install --find-links <dir> pkg==0.1.0  -> 3.14.5, exit 0, and the
#                                                     package IMPORTS there
#   uv tool install <the .whl file>                -> 3.14.5
#   uv tool install <a local project dir>          -> 3.13.13  <- the only one
#   pip install --find-links <dir> pkg             -> refuses, "requires a
#                                                     different Python"
#
# The first line is this script's path. Only the project-directory path reads
# the ceiling, and no user of this script takes it. A `requires-python` bound
# is still worth having — it is what makes `pip install aelix` refuse cleanly
# instead of breaking later; #192 left it to #278 — but it does not retire
# this flag.
#
# WHICH RANGE (#278, ADR-0265). 3.14 is supported from 0.1.0 on: that release
# pins openai>=2.7.2, the first openai that does not write onto typing.Union, and
# CI runs 3.14 on ubuntu and windows. But this script installs whatever
# AELIX_VERSION names, and 0.1.0b2 carries openai<2.0 forever — a default that
# simply widened would hand a pinned AELIX_VERSION=v0.1.0-beta.2 install exactly
# the crash this flag was added to stop. So the request is derived from the
# release being installed: python_request_for gives `>=3.11,<3.15` to 0.1.0b3
# and later and keeps `>=3.11,<3.14` for anything older or unparseable. The
# version is the one parsed above from the VERIFIED manifest, never the tag.
#
# THE EXTENSIONS IN THE CURRENT ENVIRONMENT (#926, ADR-0321). `uv tool install
# --force` rebuilds the tool environment from its uv-receipt.toml, and every `uv
# tool` operation leaves exactly what the receipt names: an extension `aelix
# extension install` put there with `uv pip install` is not in it, so a re-run of
# this line used to delete every extension (measured on uv 0.11.19 and 0.13.10 —
# with and without --force, and `uv tool upgrade` too; only `--with` requirements
# survive). So before the reinstall, the current environment's OWN python reads
# what it holds (carry.py below, `collect`): the receipt's other requirements and
# each installed distribution with an `aelix.extensions` entry point, as the
# requirement that reproduces it (a git commit, a directory or file that still
# exists, else name==version). They go to the reinstall as --with-requirements,
# so they land in the new receipt too. After it, the NEW environment's python
# (`check`) keeps an extension only when the bytes of its files hash as before: a
# name==version an index answered with other files is taken out again. When the
# reinstall fails with them, aelix is installed alone and they are added back one
# at a time, so one extension uv cannot install with this release leaves the
# others in; each one left out is named. An extension never fails the update.
install_aelix() {
  uv tool install --force --find-links "$tmp" --python "$AELIX_PYTHON" "$target" "$@"
}
install_failed="uv tool install failed for '$target' (interpreter request: '$AELIX_PYTHON'). If uv reported no interpreter for that range, this machine has none inside it and managed downloads are off (UV_PYTHON_DOWNLOADS=never): install one, or set AELIX_PYTHON — but read Step 5 first: a release up to 0.1.0b2 pins openai<2.0, which dies mid-turn on 3.14 (#262)."
carry="$tmp/carry"
mkdir "$carry"
cat > "$tmp/carry.py" <<'AELIX_CARRY_PY'
import base64
import hashlib
import importlib.metadata
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import url2pathname

# #926, ADR-0321. `uv tool install --force` rebuilds the aelix tool environment from
# its uv-receipt.toml, and an extension that `aelix extension install` put there is
# not in that receipt, so the reinstall dropped it. This program runs twice. It
# writes the requirements the installer hands to `--with-requirements` (uv records
# each one in the new receipt) and, line for line, what to print when one is not
# carried.
#   collect <env dir> <state dir>   on the CURRENT environment's python: every receipt
#     requirement besides aelix that is still installed, and every installed
#     distribution with an `aelix.extensions` entry point, as a requirement that
#     reproduces it (PEP 610 direct_url.json: a git commit, a directory or file that
#     still exists, else name==version), with the extras and marker the receipt asked
#     for it with. What cannot be read or reproduced is named and left out, and no
#     one file stops the others; one whose METADATA cannot be read is named from its
#     directory. A walk of the environment that breaks part-way says so, and names
#     nothing past where it broke.
#   check <state dir> <requirements file>   on the NEW environment's python, for the
#     requirements that install took: an extension is kept only when the bytes of
#     the files its RECORD lists hash as they did before and its entry points,
#     Requires-Dist and Provides-Extra read as they did, so a carried name==version
#     that an index answered with other files or other metadata is taken out.
# Standard library only, Python 3.11+, ASCII only. install.sh and install.ps1 carry
# the same copy; tests/packaging_gate/test_install_carry.py holds them equal.

GROUP = "aelix.extensions"
FIRST_PARTY = {"aelix", "aelix-ai", "aelix-agent-core", "aelix-coding-agent", "aelix-server"}
WINDOWS = os.name == "nt"
INDEX_NOTE = " (from PyPI or an index `aelix extension source add` registered; add --index-url <index> for another)"
# When uv could not install one with the release, alone or beside the ones before it.
# The shell puts the release's version in for @VERSION@.
FAILED = " - uv could not install it with aelix @VERSION@ (its message is above)"
NO_COMMAND = ", so there is no command that reinstalls it here"


def canon(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def say(line):
    print("aelix-install: " + line, flush=True)


def quote(text):
    if WINDOWS:
        return "'" + text.replace("'", "''") + "'"
    return "'" + text.replace("'", "'\"'\"'") + "'"


def file_uri(path):
    # uv reads a %23 in a file URI as '#' and then as a fragment (uv 0.11.19).
    if "#" in str(path):
        return None
    return Path(path).as_uri()


def url_path(url):
    parts = urlsplit(url)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
        return None
    return url2pathname(parts.path)


def env_python(env_dir):
    if WINDOWS:
        return os.path.join(env_dir, "Scripts", "python.exe")
    return os.path.join(env_dir, "bin", "python")


def uv_pip(env_dir, line):
    head = "uv pip install --python " + quote(env_python(env_dir)) + " "
    if line.startswith("-e "):
        return head + "-e " + quote(line[3:])
    return head + quote(line)


def stem_of(dist):
    """The dist-info directory's own name (name-version), when its METADATA is unreadable."""
    path = getattr(dist, "_path", None)  # PathDistribution's directory; it has no public name
    base = os.path.basename(str(path)) if path is not None else ""
    for suffix in (".dist-info", ".egg-info"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def unreadable(exc):
    return ("this installer could not read it (" + type(exc).__name__ + "), so there is no command"
            " to reinstall it from here")


def is_extension(dist):
    try:
        return any(ep.group == GROUP for ep in dist.entry_points)
    except Exception:  # an entry_points.txt importlib cannot parse: its text decides
        try:
            return "[" + GROUP + "]" in (dist.read_text("entry_points.txt") or "")
        except Exception:
            return False


def path_pins():
    agent = os.environ.get("AELIX_CODING_AGENT_DIR") or os.path.join(
        os.path.expanduser("~"), ".aelix", "agent"
    )
    out = {}
    try:
        with open(os.path.join(agent, "extension_pins.json"), encoding="utf-8") as fh:
            pins = json.load(fh).get("pins")
        for identity, pin in pins.items():
            if isinstance(pin, dict) and pin.get("kind") == "path" and isinstance(pin.get("sha256"), str):
                out.setdefault(os.path.basename(identity), []).append((identity, pin["sha256"]))
    except Exception:  # no pins, or a file aelix wrote in another shape: no original to find
        pass
    return out


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def from_receipt(entry):
    """A uv receipt requirement -> a requirements-file line, or None."""
    name = str(entry["name"])
    extras = entry.get("extras") or []
    extra = "[" + ",".join(extras) + "]" if extras else ""
    marker = entry.get("marker")
    tail = " ; " + marker if isinstance(marker, str) and marker else ""
    if "editable" in entry:
        uri = file_uri(entry["editable"])
        return None if uri is None or extra or tail else "-e " + uri
    if "git" in entry:
        parts = urlsplit(str(entry["git"]))
        query = dict(parse_qsl(parts.query))
        if set(query) - {"rev", "tag", "branch", "subdirectory"}:
            return None
        ref = query.get("rev") or query.get("tag") or query.get("branch")
        line = name + extra + " @ git+" + urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        line += "@" + ref if ref else ""
        line += "#subdirectory=" + query["subdirectory"] if query.get("subdirectory") else ""
    elif "directory" in entry or "path" in entry:
        uri = file_uri(entry.get("directory") or entry.get("path"))
        if uri is None:
            return None
        line = name + extra + " @ " + uri
    elif "url" in entry:
        line = name + extra + " @ " + str(entry["url"])
    elif set(entry) <= {"name", "extras", "marker", "specifier"}:
        line = name + extra + str(entry.get("specifier") or "")
    else:
        return None
    return line + tail


def from_dist(dist, pins, env_dir, extra=""):
    """An installed extension -> (line, reinstall command) or (None, why none can be given).
    ``extra``: the ``[extras]`` the receipt asked for it with, which the command keeps."""
    name, version = dist.metadata["Name"], dist.version
    raw = dist.read_text("direct_url.json")
    if not raw:
        line = name + "==" + version
        return line, "aelix extension install " + quote(name + extra + "==" + version) + INDEX_NOTE
    info = json.loads(raw)
    url = str(info["url"])
    sub = info.get("subdirectory")
    sub_part = "#subdirectory=" + sub if sub else ""
    if "vcs_info" in info:
        vcs = info["vcs_info"]
        commit = str(vcs.get("commit_id") or "")
        if vcs.get("vcs") != "git" or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", commit):
            return None, ("it came from a " + str(vcs.get("vcs")) + " checkout this installer cannot pin,"
                          " so there is no command to reinstall it from here")
        source = "git+" + url + "@" + commit + sub_part
        return name + " @ " + source, "aelix extension install " + quote(
            name + extra + " @ " + source if extra else source)
    path = url_path(url)
    if path is None:
        line = name + " @ " + url
        return line, uv_pip(env_dir, name + extra + " @ " + url)
    uri = file_uri(path)
    if "dir_info" in info:
        if not os.path.isdir(path) or uri is None:
            return None, ("its source directory " + path + " is gone or has a '#' in it, so there is no"
                          " command to reinstall it from here")
        if info["dir_info"].get("editable"):
            line = "-e " + uri
            return line, uv_pip(env_dir, line)
        return name + " @ " + uri + sub_part, "aelix extension install " + quote(path + extra)
    if os.path.isfile(path) and uri is not None:
        return name + " @ " + uri, "aelix extension install " + quote(path + extra)
    # `aelix extension install <file>` installs a staged copy whose directory is gone
    # by now; the pin it recorded names the original file and its hash (ADR-0187).
    for identity, digest in pins.get(os.path.basename(path), []):
        try:
            original = file_uri(identity)
            if original is not None and os.path.isfile(identity) and sha256_of(identity) == digest:
                return name + " @ " + original, "aelix extension install " + quote(identity + extra)
        except OSError:
            continue
    return None, ("the file it was installed from is gone (" + os.path.basename(path) + "), so there is"
                  " no command to reinstall it from here")


def files_of(dist):
    """What each file the RECORD lists holds now, by sha256 of its bytes (not RECORD's claim)."""
    out = {}
    try:
        listed = list(dist.files or ())
    except Exception:  # a RECORD importlib cannot parse lists nothing to compare
        return out
    for item in listed:
        try:
            rel = str(item).replace("\\", "/")
            if rel.startswith("../") or ".dist-info/" in rel or rel.endswith(".pyc") or "__pycache__/" in rel:
                continue
            digest = hashlib.sha256(item.locate().read_bytes()).digest()
            out[rel] = "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        except Exception:  # not on disk (3.11 lists these, 3.12+ drops them) or unreadable
            continue
    return out


def requirement_key(line):
    """A Requires-Dist line in one spelling: build tools differ in spaces, quotes,
    `name (spec)` against `namespec`, and the order of a specifier's parts."""
    text = re.sub(r"\s+", "", str(line)).replace('"', "'")
    head, _, marker = text.partition(";")
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\(?([^\x29]*)\)?$", head)  # \x29: a closing parenthesis
    if not m:
        return text.lower()
    extras = ",".join(sorted(canon(x) for x in (m.group(2) or "[]")[1:-1].split(",") if x))
    spec = ",".join(sorted(x for x in m.group(3).split(",") if x))
    return canon(m.group(1)) + "[" + extras + "]" + spec + ";" + marker.lower()


def meta_of(dist):
    """What the dist-info says beside its files: its entry points, and what it pulls in
    (Requires-Dist) and offers (Provides-Extra). A wheel that keeps the files and drops
    its entry points, or adds a dependency, is not the extension that was installed."""
    try:
        points = sorted(ep.group + " " + ep.name + "=" + re.sub(r"\s+", "", ep.value)
                        for ep in dist.entry_points)
    except Exception:  # an entry_points.txt importlib cannot parse: its text, as it is
        try:
            points = ["text " + str(dist.read_text("entry_points.txt"))]
        except Exception:
            points = ["unreadable"]
    try:
        meta = dist.metadata
        requires = sorted(requirement_key(r) for r in (meta.get_all("Requires-Dist") or []))
        extras = sorted(canon(e) for e in (meta.get_all("Provides-Extra") or []))
    except Exception:
        requires, extras = ["unreadable"], ["unreadable"]
    return {"entry_points": points, "requires": requires, "extras": extras}


def installed_dists(unread=None):
    """Each distribution by canonical name. One whose METADATA names nothing readable goes
    to ``unread`` when it is given: it can still be named from its directory."""
    out = {}
    try:
        for dist in importlib.metadata.distributions():
            try:  # get_all: a missing METADATA reads as empty, and ["Name"] warns (3.12+)
                names = dist.metadata.get_all("Name") or []
                name = names[0] if names else None
            except Exception:
                name = None
            if name:
                out.setdefault(canon(name), dist)
            elif unread is not None:
                unread.append(dist)
    except Exception as exc:  # a directory importlib cannot walk: what it found so far
        say("could not read every distribution of the environment (" + type(exc).__name__ + "), so"
            " one it did not reach is not carried and not named here")
    return out


def asked_extra(entry):
    """The ``[extras]`` the receipt asked for an extension with, or "" (none, or unreadable)."""
    extras = entry.get("extras") if isinstance(entry, dict) else None
    if isinstance(extras, list) and extras and all(isinstance(x, str) for x in extras):
        return "[" + ",".join(extras) + "]"
    return ""


def asked_for(name, line, entry):
    """``line`` with the extras and marker the receipt asked for this extension with, and
    a note when they cannot go with it."""
    if not isinstance(entry, dict):
        return line, ""
    extras = entry.get("extras") or []
    marker = entry.get("marker") or ""
    if (not isinstance(extras, list) or not all(isinstance(x, str) for x in extras)
            or not isinstance(marker, str)):
        return line, " (its uv receipt asks for extras or a marker this installer cannot read back;" \
            " carried without them)"
    if not extras and not marker:
        return line, ""
    if line.startswith("-e ") or not line.startswith(name):
        return line, " (an editable line cannot carry the receipt's extras " + ",".join(extras) \
            + "; carried without them)"
    extra = "[" + ",".join(extras) + "]" if extras else ""
    return name + extra + line[len(name):] + (" ; " + marker if marker else ""), ""


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(line + "\n" for line in lines))


def read_receipt(env_dir):
    try:
        import tomllib

        with open(os.path.join(env_dir, "uv-receipt.toml"), "rb") as fh:
            requirements = tomllib.load(fh)["tool"]["requirements"]
        if not isinstance(requirements, list):
            raise TypeError("requirements")
        return requirements
    except Exception as exc:
        say("could not read the uv receipt of the current aelix (" + type(exc).__name__ + "), so what it"
            " adds besides the extensions is not carried")
        return []


def bad(line):
    return any(ch in line for ch in "\r\n\x00")


def collect(env_dir, state):
    unread = []
    installed = installed_dists(unread)
    unread_names = {canon(stem_of(dist).split("-", 1)[0]) for dist in unread}
    items, derived, asked = [], {}, {}
    for entry in read_receipt(env_dir):
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name or bad(name):
            say("not carried: a uv receipt entry this installer cannot read, so there is no command to"
                " reinstall it from here")
            continue
        if canon(name) == "aelix":
            continue
        dist = installed.get(canon(name))
        if dist is None and canon(name) in unread_names:
            continue  # installed, with a METADATA that cannot be read: named below
        if dist is None:
            say("not carried: " + name + " - the uv receipt names it, but it is not installed now, so there"
                " is nothing to reinstall")
            continue
        if is_extension(dist):
            asked[canon(name)] = entry  # carried below, from what is installed now
            continue
        try:
            line = from_receipt(entry)
        except Exception:
            line = None
        if line is None or bad(line):
            say("not carried: " + name + " - uv recorded it in a form this installer cannot read back, so"
                " there is no command to reinstall it from here")
            continue
        items.append((line, "not carried: " + line + " - reinstall it with: " + uv_pip(env_dir, line),
                      "not carried: " + line + FAILED + NO_COMMAND))
        say("carrying " + line + " (already in the uv receipt)")
    pins = path_pins()
    for key, dist in sorted(installed.items()):
        if key in FIRST_PARTY or not is_extension(dist):
            continue
        label = key
        try:
            name = str(dist.metadata["Name"])
            label = name + " " + str(dist.version)
            line, how = from_dist(dist, pins, env_dir, asked_extra(asked.get(key)))
            note = ""
            if line is not None:
                line, note = asked_for(name, line, asked.get(key))
        except Exception as exc:
            say("not carried: " + label + " - " + unreadable(exc))
            continue
        if line is None or bad(line):
            say("not carried: " + label + " - " + (how if line is None else "its source has a control"
                " character, so there is no command to reinstall it from here"))
            continue
        files = files_of(dist)
        if not files:
            say("not carried: " + label + " - it records no installed files to compare; reinstall it with: "
                + how)
            continue
        derived[line] = {"key": key, "label": label, "files": files, "meta": meta_of(dist),
                         "restore": how}
        # A name==version may come from an index the installer's uv does not use; the
        # same source again (a git commit, a file) fails the same way.
        index = "==" in line and " @ " not in line and not line.startswith("-e ")
        failed = FAILED + ("; from an index this installer does not use, reinstall it with: " + how
                           if index else NO_COMMAND)
        items.append((line, "not carried: " + label + " - reinstall it with: " + how,
                      "not carried: " + label + failed))
        say("carrying " + label + " as " + line + note)
    for dist in unread:
        stem = stem_of(dist)
        if not is_extension(dist) or canon(stem.split("-", 1)[0]) in FIRST_PARTY:
            continue
        try:
            points = ", ".join(sorted(ep.name for ep in dist.entry_points if ep.group == GROUP))
        except Exception:
            points = "unreadable"
        shown = stem.replace("-", " ", 1) if stem else "an extension"
        say("not carried: " + shown + " - its METADATA cannot be read, so this installer knows it only by"
            " its directory (" + stem + ".dist-info) and its entry points (" + points + "), and there is"
            " no command to reinstall it from here")
    write_lines(os.path.join(state, "with.txt"), [item[0] for item in items])
    write_lines(os.path.join(state, "restore.txt"), ["aelix-install: " + item[1] for item in items])
    write_lines(os.path.join(state, "failed.txt"), ["aelix-install: " + item[2] for item in items])
    with open(os.path.join(state, "expect.json"), "w", encoding="utf-8") as fh:
        json.dump(derived, fh)
    return 0


def check(state, taken):
    with open(os.path.join(state, "expect.json"), encoding="utf-8") as fh:
        expected = json.load(fh)
    with open(taken, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    installed = installed_dists()
    kept, labels, out = [], [], 0
    for line in lines:
        item = expected.get(line)
        if item is None:
            kept.append(line)  # a receipt requirement: carried as uv recorded it
            continue
        dist = installed.get(item["key"])
        if dist is None:
            why = "it is missing from"
        elif files_of(dist) != item["files"]:
            why = "its files differ in"
        elif meta_of(dist) != item["meta"]:
            why = "its entry points or what it depends on (Requires-Dist, Provides-Extra) differ in"
        else:
            kept.append(line)
            labels.append(item["label"])
            continue
        say("not carried: " + item["label"] + " - " + why + " the new environment, so it is taken out"
            + "; reinstall it with: " + item["restore"])
        out = 3
    write_lines(os.path.join(state, "kept.txt"), kept)
    write_lines(os.path.join(state, "extensions.txt"), labels)
    return out


def main(argv):
    try:
        # A name the console cannot encode prints escaped (a cp1252 console on Windows).
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
        if len(argv) == 4 and argv[1] == "collect":
            return collect(argv[2], argv[3])
        if len(argv) == 4 and argv[1] == "check":
            return check(argv[2], argv[3])
    except Exception as exc:
        say("carry.py " + argv[1] + " stopped: " + type(exc).__name__ + ": " + str(exc)[:200])
        return 1
    print("usage: collect <tool env dir> <state dir> | check <state dir> <requirements file>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
AELIX_CARRY_PY
# names_in_files <env dir>: the extensions an environment's own files name, for when
# its python cannot read them (aelix's own distributions left out): the project name
# and version its METADATA gives, else the dist-info directory's, said to be that.
names_in_files() {
  for _ep in "$1"/lib/python*/site-packages/*.dist-info/entry_points.txt; do
    if [ ! -f "$_ep" ] || ! grep -q '^\[aelix\.extensions\]' "$_ep"; then continue; fi
    _i="${_ep%/entry_points.txt}"; _d="${_i##*/}"; _d="${_d%.dist-info}"
    case "$_d" in aelix-*|aelix_ai-*|aelix_agent_core-*|aelix_coding_agent-*|aelix_server-*) continue ;; esac
    _nm="$(sed -n 's/^Name:[[:space:]]*//p' "$_i/METADATA" 2>/dev/null | head -n 1 | tr -d '\r')"
    _vn="$(sed -n 's/^Version:[[:space:]]*//p' "$_i/METADATA" 2>/dev/null | head -n 1 | tr -d '\r')"
    if [ -n "$_nm" ]; then
      printf '%s %s\n' "$_nm" "$_vn"
    else
      printf '%s %s (named from its directory: its METADATA cannot be read)\n' "${_d%%-*}" "${_d#*-}"
    fi
  done
}
# not_carried <requirements file>: the line naming each requirement in it as not carried.
not_carried() {
  while IFS= read -r _l; do
    _k="$(grep -n -F -x -e "$_l" "$carry/with.txt" | head -n 1 | cut -d: -f1)"
    if [ -n "$_k" ]; then sed -n "${_k}p" "$carry/restore.txt"; fi
  done < "$1"
}
# failed_line <n>: why requirement n is left out after uv could not install it with
# this release (failed.txt, line n, with the release's version put in), on a line of
# its own after uv's. Its command, where there is one, is one that can work: not the
# same source again.
failed_line() {
  after_uv
  sed -n "${1}p" "$carry/failed.txt" | sed "s|@VERSION@|$version|"
}
# after_uv: what is printed after a failed uv call starts on a line of its own; uv's
# last message may not end its line (its build-failure hint does not, uv 0.11.19).
after_uv() { printf '\n' >&2; }
# add_back: aelix is installed alone; add the carried requirements one at a time on
# top of the ones that went in, and name each one uv cannot install with this release.
# A failed build removes the environment (uv 0.11.19 and 0.13.10), so when the last
# try failed, what went in is installed again.
add_back() {
  : > "$carry/in.txt"
  _n="$(wc -l < "$carry/with.txt" | tr -d ' ')"
  if [ "$_n" -eq 1 ]; then failed_line 1; return 0; fi
  _i=0
  _last=ok
  while [ "$_i" -lt "$_n" ]; do
    _i=$((_i + 1))
    { cat "$carry/in.txt"; sed -n "${_i}p" "$carry/with.txt"; } > "$carry/try.txt"
    if install_aelix --with-requirements "$carry/try.txt"; then
      mv "$carry/try.txt" "$carry/in.txt"
      _last=ok
    else
      failed_line "$_i"
      _last=failed
    fi
  done
  if [ "$_last" = ok ]; then return 0; fi
  if [ -s "$carry/in.txt" ] && install_aelix --with-requirements "$carry/in.txt"; then return 0; fi
  install_aelix || die "$install_failed"
  not_carried "$carry/in.txt"
  : > "$carry/in.txt"
}
env_dir=""
if _tool_dir="$(uv tool dir 2>/dev/null </dev/null)" && [ -f "$_tool_dir/aelix/uv-receipt.toml" ]; then
  env_dir="$_tool_dir/aelix"
fi
carried=""
: > "$carry/unread.txt"
if [ -n "$env_dir" ]; then
  log "reading what the current aelix environment holds ($env_dir)..."
  if ! "$env_dir/bin/python" -I -c pass >/dev/null 2>&1 </dev/null; then
    log "the current aelix environment's python does not run, so nothing in it can be carried over."
    names_in_files "$env_dir" > "$carry/unread.txt"
  elif "$env_dir/bin/python" -I "$tmp/carry.py" collect "$env_dir" "$carry" </dev/null; then
    if [ -s "$carry/with.txt" ]; then carried=yes; fi
  else
    log "could not read the current aelix environment; nothing in it is carried over."
    names_in_files "$env_dir" > "$carry/unread.txt"
  fi
fi
if [ -z "$carried" ]; then
  install_aelix || die "$install_failed"
else
  cp "$carry/with.txt" "$carry/in.txt"
  if ! install_aelix --with-requirements "$carry/in.txt"; then
    after_uv
    log "the reinstall with the carried requirements failed; installing aelix alone, then adding them back one at a time."
    install_aelix || die "$install_failed"
    add_back
  fi
  if [ -s "$carry/in.txt" ]; then
    _check=0
    "$env_dir/bin/python" -I "$tmp/carry.py" check "$carry" "$carry/in.txt" </dev/null || _check=$?
    if [ "$_check" -eq 3 ]; then
      log "reinstalling without what was taken out..."
      if [ ! -s "$carry/kept.txt" ]; then
        install_aelix || die "$install_failed"
        carried=""
      elif ! install_aelix --with-requirements "$carry/kept.txt"; then
        after_uv
        log "that reinstall failed; installing aelix with nothing carried over."
        install_aelix || die "$install_failed"
        not_carried "$carry/kept.txt"
        carried=""
      fi
    elif [ "$_check" -ne 0 ]; then
      log "could not compare the carried extensions (exit $_check); installing aelix with nothing carried over."
      install_aelix || die "$install_failed"
      not_carried "$carry/in.txt"
      carried=""
    fi
  else
    carried=""
  fi
fi
if [ -s "$carry/unread.txt" ]; then
  while IFS= read -r _x; do
    log "not carried: $_x - the current environment could not be read"
  done < "$carry/unread.txt"
  if "$env_dir/bin/aelix" extension restore --help >/dev/null 2>&1 </dev/null; then
    log "'aelix extension restore --all' puts back each one 'aelix extension install' recorded at a pin; the next interactive aelix start does it too."
  else
    log "aelix $version has no restore command: reinstall each with 'aelix extension install <the git URL, path or name you installed it from>'."
  fi
fi

# The carried extensions are what was installed before, but no aelix pin check ran
# on them here (ADR-0321): the new aelix's own import-free verdict says whether each
# binds on this release. Its verdict is printed and the update is kept either way.
if [ -n "$carried" ] && [ -s "$carry/extensions.txt" ]; then
  log "carried over: $(tr '\n' ',' < "$carry/extensions.txt" | sed 's/,$//; s/,/, /g'). Checking them with the new aelix:"
  _verify=0
  "$env_dir/bin/aelix" extension verify </dev/null || _verify=$?
  case "$_verify" in
    0) log "every carried extension binds on aelix $version." ;;
    1) log "an extension above does not bind on aelix $version. The update is kept, and aelix leaves out what does not bind; 'aelix extension remove <name>' removes one." ;;
    *) log "'aelix extension verify' exited $_verify; run it again after the install to see each extension's verdict." ;;
  esac
fi

# ── Step 6: post-install smoke + PATH hint ──────────────────────────────────
if have aelix; then
  aelix --version || log "installed, but 'aelix --version' returned non-zero."
  log "done. 'aelix' is on your PATH."
else
  # The launcher landed in uv's tool bin but this shell cannot see it. Until
  # beta.2 this branch only ADVISED running `uv tool update-shell`, which left
  # the one step that decides whether `aelix` works in the next terminal to
  # the user. Run it here instead — it appends the tool bin to the shell's rc
  # file, idempotently, and prints what it changed — and say which shell to
  # reopen. It cannot fix THIS shell's PATH, hence the log line.
  log "installed. The 'aelix' launcher is in uv's tool bin (usually ~/.local/bin), which is not on this shell's PATH."
  if uv tool update-shell; then
    log "PATH updated for future shells. Open a new terminal (or 'source' your shell rc) and run: aelix --version"
  else
    log "'uv tool update-shell' failed; add uv's tool bin to PATH by hand (\`uv tool dir --bin\`) and re-run: aelix --version"
  fi
fi
