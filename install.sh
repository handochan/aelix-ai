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
#   UV_VERSION     Optional pin for the uv bootstrap (Astral installer).
#   GITHUB_TOKEN   Optional; sent as a Bearer token on GitHub API calls to
#                  avoid the 60/hr unauthenticated rate limit.

set -eu

# ── Step 0: preamble ────────────────────────────────────────────────────────
AELIX_VERSION="${AELIX_VERSION-}"
AELIX_EXTRAS="${AELIX_EXTRAS-tui}"
AELIX_REPO="${AELIX_REPO-handochan/aelix-ai}"
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
uv tool install --force --find-links "$tmp" "$target" \
  || die "uv tool install failed for '$target'."

# ── Step 6: post-install smoke + PATH hint ──────────────────────────────────
if have aelix; then
  aelix --version || log "installed, but 'aelix --version' returned non-zero."
  log "done. 'aelix' is on your PATH."
else
  log "installed. The 'aelix' launcher is in uv's tool bin (usually ~/.local/bin)."
  log "If 'aelix' is not found, add it to PATH with: uv tool update-shell"
fi
