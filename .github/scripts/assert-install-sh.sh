#!/bin/sh
# Post-conditions for install.sh (#279), asserted from a SEPARATE step so the
# install step stays the documented one-liner and nothing else.
#
# The model is .github/scripts/assert-install-ps1.ps1 (#106, #263); assertions
# (1)-(4) are its four, ported. Scope discipline is the same: only what
# install.sh itself promises. It does not promise that aelix runs a turn, that
# the TUI draws, or that a NEW login shell finds `aelix` -- Step 6 hands that to
# `uv tool update-shell`, and only the next terminal can see it.
#
# POSIX sh on purpose, run as `sh <this file>`: on ubuntu-latest that is dash,
# on macos-latest it is bash 3.2 in POSIX mode -- the two `sh` dialects the
# README's `| sh` reaches.
#
# Knobs (environment), set by each step of the install-sh-e2e job:
#   E2E_ARM              which install pass this is, named in every failure.
#   E2E_EXPECT_EXTRAS    the extras the pass asked for. Default `tui` (install.sh
#                        Step 0's default); set-but-empty means the bare CLI.
#   E2E_EXPECT_PYTHON    the interpreter request the pass made. Empty or unset
#                        means install.sh's default, INSTALLER_PY_REQUEST below,
#                        and then the interpreter must be inside its range. A
#                        bare minor such as `3.11` means exactly that minor.
#   E2E_STATE_DIR        where the "an install ran since the last assert"
#                        marker lives. Default $RUNNER_TEMP, else $TMPDIR, else /tmp.
#   GH_TOKEN             for `gh api` (the expected version is resolved through
#                        it, independently of install.sh's own grep/sed).

set -eu

# The range install.sh Step 0 defaults AELIX_PYTHON to, and the range the
# INSTALLED release survives: the published 0.1.0b2 pins openai<2.0, which dies
# mid-turn on 3.14 (#262). #278 widens the range for b3+ releases only, so this
# moves when install.sh's default moves and not before.
# tests/packaging_gate/test_install_ps1_parity.py holds it equal to
# install.sh's default, as it does for assert-install-ps1.ps1.
INSTALLER_PY_REQUEST='>=3.11,<3.14'

ARM="${E2E_ARM:-unnamed pass}"
EXPECT_EXTRAS="${E2E_EXPECT_EXTRAS-tui}"
EXPECT_PYTHON="${E2E_EXPECT_PYTHON:-}"
STATE_DIR="${E2E_STATE_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}}"

# Every failure names the pass and the post-condition, so a red run says which
# arm failed and why without opening the log of the install step.
fail() {
  printf '::error::install.sh e2e [%s] post-condition %s: %s\n' "$ARM" "$1" "$2"
  exit 1
}
ok() { printf 'install.sh e2e [%s] %s\n' "$ARM" "$*"; }

# canon <path>: the directory part with every symlink resolved (macOS /tmp is
# /private/tmp; the Astral installer writes `$XDG_DATA_HOME/../bin` to
# GITHUB_PATH verbatim), the last component kept as is. The tool launcher is
# itself a symlink into the tool environment, so resolving the last component
# would make ANY link to that environment look like the launcher.
canon() {
  _d="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || return 1
  printf '%s/%s\n' "$_d" "$(basename "$1")"
}

for _tool in uv gh curl; do
  command -v "$_tool" >/dev/null 2>&1 \
    || fail 0 "'$_tool' is not on PATH in this step (PATH=$PATH)."
done

tool_dir="$(uv tool dir)" || fail 0 "'uv tool dir' failed."
bin_dir="$(uv tool dir --bin)" || fail 0 "'uv tool dir --bin' failed."
env_dir="$tool_dir/aelix"
receipt="$env_dir/uv-receipt.toml"
[ -f "$receipt" ] || fail 0 "no uv receipt at '$receipt'; uv has no record of an aelix tool install."

# -- (0) The install step before this one actually installed ----------------
# Every pass after the first would otherwise be free to do nothing and still
# find the previous pass's environment intact. `--force` rewrites the receipt,
# so it must be newer than the marker the previous assert left.
marker="$STATE_DIR/aelix-e2e-asserted"
if [ -e "$marker" ]; then
  [ -n "$(find "$receipt" -newer "$marker")" ] \
    || fail 0 "'$receipt' is not newer than the previous assert; the install step before this one did not (re)install aelix."
fi

# -- The expected version, resolved INDEPENDENTLY of the script ---------------
# install.sh resolves the tag with grep/sed over the releases JSON and parses
# SHA256SUMS with awk. This takes the tag through `gh api --jq` and the version
# through sed, so a wrong tag or a wrong wheel on either side surfaces as a
# DISAGREEMENT rather than as two copies of one bug.
#
# The repo is hard-coded, not $GITHUB_REPOSITORY: the install step leaves
# AELIX_REPO unset, so install.sh used its own default. On a fork the two differ.
repo='handochan/aelix-ai'
tag="$(gh api "repos/$repo/releases" --jq '.[0].tag_name')" \
  || fail 2 "could not resolve the newest release tag for '$repo' through gh api."
[ -n "$tag" ] || fail 2 "gh api returned no release tag for '$repo'."
sums="$(curl -fsSL "https://github.com/$repo/releases/download/$tag/SHA256SUMS")" \
  || fail 2 "could not download SHA256SUMS for '$tag'."
# Same anchor as install.sh Step 5: the meta-package is the only `aelix-` entry
# (its siblings escape the hyphen: aelix_ai-...), and a PEP 440 version never
# contains a hyphen.
expected="$(printf '%s\n' "$sums" \
  | sed -n 's/^[0-9a-fA-F]\{64\}[[:space:]][[:space:]]*\*\{0,1\}aelix-\([^-[:space:]]*\)-py3-none-any\.whl[[:space:]]*$/\1/p' \
  | head -n 1)"
[ -n "$expected" ] || fail 2 "no 'aelix-<version>-py3-none-any.whl' entry in SHA256SUMS for '$tag'."
ok "expected version (from $tag SHA256SUMS): $expected"

# -- (1) The launcher is where Step 6 says it is ------------------------------
launcher="$bin_dir/aelix"
[ -x "$launcher" ] \
  || fail 1 "no executable launcher at '$launcher' (uv tool bin = '$bin_dir'). install.sh Step 6 promises it lands in uv's tool bin."
# `aelix` on PATH in a LATER step is not free: it holds only because the Astral
# installer appended its install dir to GITHUB_PATH (add_install_dir_to_ci_path)
# -- or the runner already had that dir on PATH. Assert it, and assert it is
# this launcher rather than some other aelix.
on_path="$(command -v aelix)" \
  || fail 1 "'aelix' is not on PATH in this step; uv's tool bin ('$bin_dir') did not reach GITHUB_PATH. PATH=$PATH"
want="$(canon "$launcher")" || fail 1 "cannot resolve '$launcher'."
got="$(canon "$on_path")" || fail 1 "cannot resolve '$on_path'."
[ "$got" = "$want" ] || fail 1 "'aelix' on PATH is '$got', not the uv tool launcher '$want'."
ok "launcher: $want"

# -- (2) --version is the version the VERIFIED manifest named -----------------
# The `==$version` pin in install.sh Step 5 is what makes Step 4's checksum
# gate binding. A different version here means a PyPI candidate outranked the
# local wheels and the gate verified artifacts the install discarded.
# `aelix --version` prints the bare PEP 440 string and nothing else.
actual="$(aelix --version 2>&1)" || fail 2 "'aelix --version' exited non-zero. Output: $actual"
[ "$actual" = "$expected" ] \
  || fail 2 "'aelix --version' printed '$actual'; SHA256SUMS for '$tag' named '$expected'."
ok "aelix --version == $actual"
# uv's own record of what it installed, as a second witness to the pin.
uv tool list 2>/dev/null | grep -q "^aelix v$expected\$" \
  || fail 2 "'uv tool list' does not report 'aelix v$expected': $(uv tool list 2>&1 | head -n 5)"

# -- (3) The interpreter is the one the pass asked for ------------------------
# install.sh Step 5 passes `--python "$AELIX_PYTHON"`, because `uv tool
# install` otherwise takes the NEWEST interpreter on the box, and on 3.14 the
# pinned openai<2.0 dies mid-turn (#262, #263). pyvenv.cfg rather than running
# the environment's python: the value uv wrote IS the fact being asserted.
cfg="$env_dir/pyvenv.cfg"
[ -f "$cfg" ] || fail 3 "no pyvenv.cfg at '$cfg'; cannot tell which interpreter the tool environment was built on."
py="$(sed -n 's/^[[:space:]]*version_info[[:space:]]*=[[:space:]]*\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' "$cfg" | head -n 1)"
[ -n "$py" ] || fail 3 "no parseable 'version_info' in '$cfg': $(cat "$cfg")"
py_major="${py%%.*}"
py_minor="${py#*.}"
if [ -n "$EXPECT_PYTHON" ]; then
  [ "$py" = "$EXPECT_PYTHON" ] \
    || fail 3 "the tool environment is on Python $py; this pass requested AELIX_PYTHON=$EXPECT_PYTHON."
  request="$EXPECT_PYTHON"
else
  floor="$(printf '%s' "$INSTALLER_PY_REQUEST" | sed -n 's/^>=3\.\([0-9][0-9]*\),<3\.[0-9][0-9]*$/\1/p')"
  ceiling="$(printf '%s' "$INSTALLER_PY_REQUEST" | sed -n 's/^>=3\.[0-9][0-9]*,<3\.\([0-9][0-9]*\)$/\1/p')"
  { [ -n "$floor" ] && [ -n "$ceiling" ]; } \
    || fail 3 "INSTALLER_PY_REQUEST '$INSTALLER_PY_REQUEST' is not the '>=3.N,<3.M' shape this script checks."
  if [ "$py_major" -ne 3 ] || [ "$py_minor" -lt "$floor" ] || [ "$py_minor" -ge "$ceiling" ]; then
    fail 3 "the tool environment is on Python $py; install.sh requests '$INSTALLER_PY_REQUEST' by default. Outside that range nothing in CI has executed the interpreter, and on 3.14 the pinned openai<2.0 dies mid-turn (#262, #263)."
  fi
  request="$INSTALLER_PY_REQUEST"
fi
# The request uv recorded. For the empty-AELIX_PYTHON pass this is the sharper
# witness: `--python ""` is accepted by uv and IGNORED, so an empty value that
# reached uv would record no request at all (#263).
recorded="$(sed -n 's/^python[[:space:]]*=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$receipt" | tr -d ' ')"
[ "$recorded" = "$request" ] \
  || fail 3 "uv recorded the interpreter request '$recorded' in '$receipt'; this pass expected '$request'."
ok "tool interpreter: Python $py (request '$recorded')"

# -- (3b) The extras are the ones the pass asked for --------------------------
# `AELIX_EXTRAS=` installs the bare CLI: POSIX-only behaviour (an empty
# variable does not exist on Windows) that nothing else executes.
req_line="$(grep '^requirements[[:space:]]*=' "$receipt")" \
  || fail 3 "no 'requirements' line in '$receipt'."
if [ -n "$EXPECT_EXTRAS" ]; then
  printf '%s\n' "$req_line" | grep -q "extras = \[\"$EXPECT_EXTRAS\"\]" \
    || fail 3 "uv recorded '$req_line'; this pass asked for aelix[$EXPECT_EXTRAS]."
  "$env_dir/bin/python" -c 'import prompt_toolkit' 2>/dev/null \
    || fail 3 "aelix[$EXPECT_EXTRAS] was requested but prompt_toolkit (the tui extra) is not importable in the tool environment."
else
  case "$req_line" in
    *extras*) fail 3 "uv recorded '$req_line'; this pass set AELIX_EXTRAS= and asked for the bare CLI." ;;
  esac
  if "$env_dir/bin/python" -c 'import prompt_toolkit' 2>/dev/null; then
    fail 3 "AELIX_EXTRAS= asked for the bare CLI, but prompt_toolkit (the tui extra) is installed in the tool environment."
  fi
fi
ok "extras: '${EXPECT_EXTRAS}' (receipt: $req_line)"

# -- (4) The temp dir was cleaned ---------------------------------------------
# install.sh makes its download dir with a bare `mktemp -d` and removes it in
# `trap cleanup EXIT INT TERM`. Ask mktemp itself where that lands -- the same
# call as the script, so TMPDIR resolution cannot diverge (macOS's mktemp does
# not even read TMPDIR for a bare -d). The name is generic, so a leftover is
# recognised by what install.sh puts in it.
probe="$(mktemp -d)" || fail 4 "mktemp -d failed."
temp_root="$(dirname "$probe")"
rmdir "$probe"
leftovers=""
for d in "$temp_root"/*/ "$temp_root"/.[!.]*/; do
  [ -d "$d" ] || continue
  if [ -e "$d/.wheels" ] || [ -e "$d/SHA256SUMS" ] || [ -e "$d/uv-install.sh" ] || [ -e "$d/releases.json" ]; then
    leftovers="$leftovers ${d%/}"
  fi
done
[ -z "$leftovers" ] || fail 4 "install.sh left its download dir(s) under '$temp_root':$leftovers"
ok "temp dir cleaned under $temp_root"

mkdir -p "$STATE_DIR"
touch "$marker"
ok "all post-conditions hold."
