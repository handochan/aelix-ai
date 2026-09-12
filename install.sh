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
#   AELIX_VERSION  Pin an exact release tag (e.g. v0.1.0-beta.1). Default:
#                  resolve the newest release from the GitHub API. Pinning is
#                  the recommended path during the beta.
#   AELIX_EXTRAS   Extras to install, consumed as aelix[$AELIX_EXTRAS].
#                  Default `tui` (interactive terminal UI), or empty for the
#                  bare CLI.
#   AELIX_REPO     GitHub owner/repo. Default `handochan/aelix-ai`.
#   AELIX_PYTHON   uv interpreter request for the tool environment. Default
#                  `>=3.11,<3.14` — the range the pinned openai<2.0 survives.
#                  NOT "the range CI runs": CI runs 3.11 and 3.12, and 3.13 is
#                  in here because it works, not because anything gates it
#                  (#192 adds it to the matrix). See Step 5 for the ceiling.
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
# `:-`, NOT `-`, and this is the one place in this file where that matters.
# `uv tool install --python ""` does not fail — uv IGNORES an empty request
# and goes back to picking the newest interpreter (measured: exit 0, and the
# environment lands on 3.14.5, the exact state Step 5 exists to prevent). The
# sibling knobs use `-` because a set-but-empty value is MEANINGFUL for them
# (`AELIX_EXTRAS=` installs the bare CLI, and the README teaches that spelling),
# so anyone copying that habit onto this variable would silently disarm the
# gate. `:-` makes empty mean the default, which is also what install.ps1
# already does — `if ($env:AELIX_PYTHON)` is false for an empty string. To
# genuinely widen the range, say so: AELIX_PYTHON='>=3.11'.
AELIX_PYTHON="${AELIX_PYTHON:->=3.11,<3.14}"
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
  # Make uv visible to THIS process (installer targets XDG_BIN_HOME or ~/.local/bin).
  _uv_bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
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
  # /releases/latest), so the first tag_name is the newest beta during beta.
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

log "installing $target with uv (version pinned from the verified SHA256SUMS)..."
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
# 3.11-3.13, and downloads only when it has none (measured both ways).
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
# the ceiling, and no user of this script takes it. #192's bound is still worth
# having — it is what makes `pip install aelix` refuse cleanly instead of
# breaking later — but it does not retire this flag.
#
# WIDENING IS NOT A ONE-LINE EDIT. When 3.14 becomes supported (#262: the floor
# is openai>=2.7.2), raising the default here reaches EVERY release this script
# can install, and AELIX_VERSION pins arbitrarily old tags — 0.1.0b2 will still
# carry openai<2.0 forever. So the default can only widen once no installable
# release breaks on the wider range, or once the request is derived from the
# release being installed. Widening on the day the fix ships would hand a
# pinning user exactly the crash this ceiling was added to stop.
uv tool install --force --find-links "$tmp" --python "$AELIX_PYTHON" "$target" \
  || die "uv tool install failed for '$target' (interpreter request: '$AELIX_PYTHON'). If uv reported no interpreter for that range, this machine has no Python 3.11-3.13 and managed downloads are off (UV_PYTHON_DOWNLOADS=never): install one, or set AELIX_PYTHON — but read Step 5 first, widening past 3.13 is not free."

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
