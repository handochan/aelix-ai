# Aelix installer for Windows -- EXPERIMENTAL.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
#
# EXPERIMENTAL (#106). What CI measures: this script runs end to end on
# windows-latest, under both pwsh 7 and Windows PowerShell 5.1 (the
# `install.ps1 e2e (pwsh)` / `install.ps1 e2e (powershell)` jobs in
# .github/workflows/ci.yml), and CI runs the full test suite on the same
# runner under 3.11, 3.12, 3.13 and 3.14. What CI does not cover: the agent itself. That
# was hand-checked on one Windows host, by one person, in one locale
# (2026-09-09) -- no second machine, no long run, and no upgrade path, since
# the previous beta had no Windows story to upgrade from. Known gaps live in
# SLICE-STATUS.md; the README's "Platform support" section is the canonical
# statement. Treat a successful install as the beginning of the test, not the
# end of it.
#
# It mirrors install.sh step for step: download the release wheels from the
# GitHub Release, verify each one against the published SHA256SUMS manifest (a
# hard security gate -- any mismatch aborts), then install the `aelix` CLI with
# uv PINNED to the exact version that manifest named. Third-party dependencies
# resolve from PyPI as usual; the four first-party wheels come from the
# checksum-verified download (uv --find-links, never --no-index). The pin is
# what makes the gate binding rather than advisory -- see Step 5.
#
# Configuration (all optional, via environment):
#   AELIX_VERSION  Pin an exact release tag (e.g. v0.1.0; Windows needs
#                  v0.1.0-beta.2 or newer). Default: resolve the newest release
#                  from the GitHub API, a pre-release included.
#   AELIX_EXTRAS   Extras to install, consumed as aelix[$AELIX_EXTRAS].
#                  Default `tui` (interactive terminal UI), which is the ONLY
#                  extra. This line used to offer `tui,images` for inline image
#                  rendering; that extra was deleted with the renderer it
#                  installed (#163, ADR-0223). Following the old advice would
#                  not have failed loudly either: uv only WARNS on an extra a
#                  package does not have ("does not have an extra named ...")
#                  and installs the rest, so the promise would simply not have
#                  been kept.
#                  DIVERGENCE from install.sh: there, a set-but-empty
#                  AELIX_EXTRAS installs the bare CLI. Windows cannot express
#                  that -- assigning '' to an environment variable DELETES it,
#                  so an empty value is indistinguishable from unset and falls
#                  back to `tui`. For the bare CLI, install `aelix` yourself:
#                  `uv tool install --force --find-links <dir> aelix`.
#   AELIX_REPO     GitHub owner/repo. Default `handochan/aelix-ai`.
#   AELIX_PYTHON   uv interpreter request for the tool environment. Default:
#                  chosen from the release being installed (Step 5, #278):
#                  `>=3.11,<3.15` for 0.1.0 and later, which pin
#                  openai>=2.7.2 and run on 3.14; `>=3.11,<3.14` for 0.1.0b2
#                  and earlier, whose pinned openai<2.0 dies mid-turn on 3.14
#                  (#262). CI runs 3.11 through 3.14, each a test leg on ubuntu
#                  and windows, and tests/test_ci_python_matrix.py holds the
#                  matrix equal to the newer range.
#                  Set it to override, e.g. AELIX_PYTHON=3.12. An EMPTY
#                  value means the default, not "no constraint"; widen it
#                  explicitly with AELIX_PYTHON='>=3.11'.
#   UV_VERSION     Optional pin for the uv bootstrap (Astral installer).
#   GITHUB_TOKEN   Optional; sent as a Bearer token on GitHub API calls to
#                  avoid the 60/hr unauthenticated rate limit.
#
# ASCII ONLY, on purpose. Do not reintroduce em dashes, box drawing or
# ellipses; the rest of this repo uses them freely, this one file cannot. It
# ships without a BOM, and Windows PowerShell 5.1 decodes a BOM-less script as
# the ANSI code page rather than as UTF-8. On the runner that is CP1252, where
# the third byte of a UTF-8 em dash, 0x94, decodes to U+201D RIGHT DOUBLE
# QUOTATION MARK, a character the PowerShell tokenizer accepts as a double
# quote. The em dash that used to sit inside Step 4's error string therefore
# CLOSED that string early and the parse collapsed into "Missing closing brace
# in statement block", pointing at the outer try below: job 100989402525, the
# first execution of this file on a Windows host. pwsh 7 was
# unaffected because Get-Content defaults to UTF-8 there, and so is
# `irm <url> | iex` because raw.githubusercontent.com sends charset=utf-8,
# which is why nothing before that run caught it.
# tests/packaging_gate/test_install_ps1_parity.py holds the invariant.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Step 0: preamble --------------------------------------------------------
$AelixVersion = if ($env:AELIX_VERSION) { $env:AELIX_VERSION } else { '' }
$AelixExtras  = if ($null -ne $env:AELIX_EXTRAS) { $env:AELIX_EXTRAS } else { 'tui' }
$AelixRepo    = if ($env:AELIX_REPO) { $env:AELIX_REPO } else { 'handochan/aelix-ai' }
# Empty means the default, as in install.sh: `if ($env:X)` is false for an
# empty string. The default depends on the release, so Step 5 fills it in.
$AelixPython  = if ($env:AELIX_PYTHON) { $env:AELIX_PYTHON } else { '' }
$UvVersion    = if ($env:UV_VERSION) { $env:UV_VERSION } else { '' }
$GithubToken  = if ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { '' }

# The two default interpreter requests Step 5 chooses between (#278, ADR-0265),
# the same two values as install.sh's py_supported / py_before_b3. Releases from
# 0.1.0 on pin openai>=2.7.2,<3 and run on 3.11 through 3.14, each a CI leg.
# 0.1.0b2 and earlier pin openai<2.0, which dies mid-turn on 3.14 (#262), and
# AELIX_VERSION can still install them.
$PySupported = '>=3.11,<3.15'
$PyBeforeB3  = '>=3.11,<3.14'

# The default interpreter request for aelix release $Version (the PEP 440
# string Step 5 reads from the verified SHA256SUMS, e.g. 0.1.0). Mirrors
# install.sh's python_request_for: the wider range only for a version this can
# PROVE is 0.1.0b3 or later (no 0.1.0b3 was published: 0.1.0 is the first
# release past 0.1.0b2): N.N.N with an optional aN / bN / rcN, compared as
# numbers, so 0.1.0b10 is after 0.1.0b3; anything else keeps the older range,
# which every release ever published runs on.
function Get-PythonRequest {
    param([string]$Version)
    # [regex]::Match rather than -match: no $Matches automatic variable whose
    # population rules differ between hosts, and .NET's default is
    # case-sensitive, like the awk in install.sh.
    $m = [regex]::Match($Version, '^([0-9]+)\.([0-9]+)\.([0-9]+)(?:(a|b|rc)([0-9]+))?$')
    if (-not $m.Success) { return $PyBeforeB3 }
    $kind = 3
    $num = 0
    if ($m.Groups[4].Success) {
        if ($m.Groups[4].Value -ceq 'a') { $kind = 0 }
        if ($m.Groups[4].Value -ceq 'b') { $kind = 1 }
        if ($m.Groups[4].Value -ceq 'rc') { $kind = 2 }
        $num = [long]$m.Groups[5].Value
    }
    $got = @([long]$m.Groups[1].Value, [long]$m.Groups[2].Value, [long]$m.Groups[3].Value, $kind, $num)
    $want = @(0, 1, 0, 1, 3)
    for ($i = 0; $i -lt 5; $i++) {
        if ($got[$i] -gt $want[$i]) { return $PySupported }
        if ($got[$i] -lt $want[$i]) { return $PyBeforeB3 }
    }
    return $PySupported
}

function Write-Log { param([string]$Message) Write-Host "aelix-install: $Message" }
function Write-Err { param([string]$Message) Write-Host "aelix-install: $Message" -ForegroundColor Red }
function Stop-WithError {
    param([string]$Message)
    Write-Err "ERROR: $Message"
    exit 1
}

function Test-Have {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# TLS 1.2 for Windows PowerShell 5.1, whose default can still be TLS 1.0 and
# which GitHub refuses. Harmless on PowerShell 7.
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("aelix-install-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    # Plain download (no auth header), the Invoke-WebRequest analogue of `dl`.
    function Get-File {
        param([string]$Url, [string]$OutFile)
        # -UseBasicParsing: PowerShell 5.1 otherwise wants Internet Explorer's
        # engine, which is absent on Server Core and on stripped images.
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
    }

    # GitHub API GET with JSON accept header and optional Bearer token.
    function Get-GitHubApi {
        param([string]$Url)
        $headers = @{ 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'aelix-install' }
        if ($GithubToken) { $headers['Authorization'] = "Bearer $GithubToken" }
        Invoke-RestMethod -Uri $Url -Headers $headers -UseBasicParsing
    }

    # -- Step 1: prerequisites -----------------------------------------------
    # Nothing to check that install.sh checks: Invoke-WebRequest replaces
    # curl/wget and Get-FileHash replaces sha256sum, both built in since
    # PowerShell 4. The one hard requirement is the PowerShell version itself.
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        Stop-WithError "need PowerShell 5.1 or newer (found $($PSVersionTable.PSVersion))."
    }

    # -- Step 2: uv bootstrap (idempotent) -----------------------------------
    if (Test-Have 'uv') {
        Write-Log "uv already installed ($((Get-Command uv).Source))."
    } else {
        Write-Log 'installing uv (Astral)...'
        try {
            # Piped into Invoke-Expression rather than saved and executed:
            # Invoke-WebRequest -OutFile stamps a downloaded .ps1 with the
            # mark-of-the-web, which the execution policy then blocks even
            # under -ExecutionPolicy Bypass on the OUTER script. This is also
            # the form Astral documents for Windows. Same trust model as
            # install.sh, which pipes the same vendor's script into `sh`.
            #
            # Invoke-RestMethod, NOT (Invoke-WebRequest ...).Content. The 200
            # that ends the astral.sh redirect chain carries NO Content-Type
            # header at all (`curl -sSIL https://astral.sh/uv/install.ps1`), so
            # PowerShell cannot tell the body is text and IWR hands .Content
            # back as a byte[]; Invoke-Expression then refuses it with "Cannot
            # convert 'System.Byte[]' to the type 'System.String' required by
            # parameter 'Command'". That is how this script died on its first
            # real Windows execution: job 100989402205, pwsh 7.6.5. IRM
            # decodes the same headerless body to a String, measured
            # on pwsh 7.6.5, and `irm | iex` is the form Astral documents.
            $uvScript = Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -UseBasicParsing
        } catch {
            Stop-WithError "failed to download the uv installer: $($_.Exception.Message)"
        }
        # Mirrors install.sh, which pins via UV_INSTALL_VERSION. UNVERIFIED on
        # Windows: the Astral PowerShell installer's pin variable has not been
        # confirmed on a real host, so an unset UV_VERSION (the default) is the
        # only path exercised by design.
        if ($UvVersion) { $env:UV_INSTALL_VERSION = $UvVersion }
        try {
            Invoke-Expression $uvScript
        } catch {
            Stop-WithError "uv install failed: $($_.Exception.Message)"
        }
        # Make uv visible to THIS process (the installer targets ~\.local\bin).
        $uvBin = Join-Path $env:USERPROFILE '.local\bin'
        $env:PATH = "$uvBin;$env:PATH"
        if (-not (Test-Have 'uv')) {
            Stop-WithError "uv still not found after install; add '$uvBin' to PATH and re-run."
        }
    }

    # -- Step 3: resolve the release tag -------------------------------------
    if ($AelixVersion) {
        $tag = $AelixVersion
        Write-Log "using pinned release tag: $tag"
    } else {
        Write-Log 'resolving the newest release from GitHub...'
        try {
            $releases = Get-GitHubApi -Url "https://api.github.com/repos/$AelixRepo/releases"
        } catch {
            Stop-WithError "failed to query the GitHub releases API for '$AelixRepo': $($_.Exception.Message)"
        }
        # The list endpoint is newest-first and INCLUDES pre-releases (unlike
        # /releases/latest), so the first tag_name is the newest release, a
        # pre-release included.
        $tag = @($releases)[0].tag_name
        if (-not $tag) {
            Stop-WithError 'could not resolve a release tag; pin one with AELIX_VERSION=vX.Y.Z.'
        }
        Write-Log "newest release tag: $tag"
    }

    # -- Step 4: download + verify (the integrity gate) ----------------------
    $base = "https://github.com/$AelixRepo/releases/download/$tag"

    Write-Log 'downloading SHA256SUMS...'
    $sumsPath = Join-Path $tmp 'SHA256SUMS'
    try {
        Get-File -Url "$base/SHA256SUMS" -OutFile $sumsPath
    } catch {
        Stop-WithError "SHA256SUMS not found for '$tag' at $base -- is the Release published? $($_.Exception.Message)"
    }

    # Parse `<hex>  <name>` into a name -> hash map (sha256sum's two-space form).
    $sums = @{}
    foreach ($line in Get-Content -LiteralPath $sumsPath) {
        if ($line -match '^\s*([0-9a-fA-F]{64})\s+\*?(\S+)\s*$') {
            $sums[$Matches[2]] = $Matches[1]
        }
    }

    # The four first-party wheels are pure py3-none-any; sdists are not needed.
    $wheels = @($sums.Keys | Where-Object { $_ -like 'aelix*.whl' } | Sort-Object)
    if ($wheels.Count -eq 0) {
        Stop-WithError "no 'aelix*.whl' entries in SHA256SUMS for '$tag'."
    }

    foreach ($name in $wheels) {
        Write-Log "downloading $name..."
        $dest = Join-Path $tmp $name
        try {
            Get-File -Url "$base/$name" -OutFile $dest
        } catch {
            Stop-WithError "failed to download $name from $base : $($_.Exception.Message)"
        }
        $expected = $sums[$name]
        if (-not $expected) {
            Stop-WithError "SECURITY: $name is absent from SHA256SUMS; aborting."
        }
        $actual = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
        # Get-FileHash returns uppercase; the manifest is lowercase.
        if ($actual -ine $expected) {
            Stop-WithError "SECURITY: checksum mismatch for $name; aborting."
        }
        Write-Log "verified $name"
    }

    # -- Step 5: install (hybrid: local verified wheels + PyPI for the rest) --
    # The version pin below is LOAD-BEARING, not cosmetic. --find-links only
    # ADDS candidates; the PyPI index stays enabled (third-party deps need it),
    # and uv then resolves the best candidate across BOTH sources. Requesting
    # the bare name `aelix` therefore lets a PyPI release of that name outrank
    # the local wheels -- and the SHA256SUMS gate in Step 4 would have verified
    # artifacts that this very command discards. Pinning to the exact version
    # named by the verified manifest is what closes that gap: only the
    # checksum-verified wheel can satisfy `==$version`.
    #
    # The version is parsed from the wheel FILENAME, never from the tag: a tag
    # is `v0.1.0-beta.1` while PEP 440 normalizes the same release to
    # `0.1.0b1`, so the tag is not a usable version specifier. The
    # meta-package wheel is `aelix-<VER>-py3-none-any.whl`; its siblings escape
    # the hyphen in their distribution name to an underscore (`aelix_ai-...`,
    # `aelix_agent_core-...`, `aelix_coding_agent-...`), so an `aelix-` prefix
    # matches the meta-package alone. A PEP 440 version can never itself
    # contain a hyphen, which is what makes the `-`-delimited split
    # unambiguous.
    $version = $null
    foreach ($name in $wheels) {
        # -match is case-insensitive, matching the shell's filename handling.
        if ($name -match '^aelix-([^-]+)-py3-none-any\.whl$') {
            $version = $Matches[1]
            break
        }
    }
    if (-not $version) {
        Stop-WithError "could not parse the aelix version from SHA256SUMS for '$tag' (no 'aelix-<version>-py3-none-any.whl' entry)."
    }

    $target = if ($AelixExtras) { "aelix[$AelixExtras]==$version" } else { "aelix==$version" }

    # The interpreter request: AELIX_PYTHON when it is set and non-empty, else
    # the default for THIS release (see WHICH RANGE below).
    if (-not $AelixPython) { $AelixPython = Get-PythonRequest $version }

    Write-Log "installing $target with uv (version pinned from the verified SHA256SUMS)..."
    Write-Log "interpreter request: $AelixPython"
    # --find-links ADDS the four checksum-verified local wheels as candidates;
    # the default PyPI index stays enabled so third-party dependencies resolve.
    # Never use --no-index (it would make transitive deps unresolvable).
    # --force makes re-runs idempotent.
    #
    # --python IS THE INTERPRETER GATE (#263). `uv tool install` consults
    # NEITHER .python-version (3.12) NOR uv.lock; it resolves an interpreter
    # fresh and takes the NEWEST one it can find. Measured on a box carrying
    # 3.11 through 3.14, the unflagged `uv tool install --force
    # aelix==0.1.0b2` built its environment on Python 3.14.5, an interpreter
    # CI has never executed. There `openai<2.0` dies, because 3.14 made
    # `typing.Union[...]` slotted while openai/_models.py:697 still writes an
    # attribute onto it:
    #
    #   AttributeError: 'typing.Union' object has no attribute
    #                   '__discriminator__' and no __dict__ for setting new
    #                   attributes                                      (#262)
    #
    # That failure is LATENT, which is why no smoke test caught it:
    # construct_type validates the union through pydantic FIRST and only falls
    # through to that write when validation fails. So `-p "say OK"` SUCCEEDS
    # on 3.14 and a real agent turn does not, measured both ways against the
    # model named in #262.
    #
    # A RANGE, not `--python 3.13`: a single version forces a download even on
    # a box where 3.12 is installed and fine. With the range uv takes any local
    # interpreter inside it and downloads only when it has none (measured both
    # ways, on the 3.11-3.13 range).
    #
    # THIS FLAG IS PERMANENT, NOT A STOPGAP. An earlier draft of this comment
    # said the opposite, that #192's `requires-python` bound would retire it.
    # That was measured on the wrong path and is false. `uv tool install`
    # chooses the interpreter BEFORE it resolves, so a published wheel's own
    # Requires-Python ceiling never steers it. Against a wheel declaring
    # `Requires-Python: <3.14,>=3.11`, on a box carrying 3.11 through 3.14:
    #
    #   uv tool install --find-links <dir> pkg==0.1.0 -> 3.14.5, exit 0, and
    #                                                    the package IMPORTS
    #   uv tool install <the .whl file>               -> 3.14.5
    #   uv tool install <a local project dir>         -> 3.13.13  <- only one
    #   pip install --find-links <dir> pkg            -> refuses, "requires a
    #                                                    different Python"
    #
    # The first line is this script's path. Only the project-directory path
    # reads the ceiling, and no user of this script takes it. A
    # `requires-python` bound is still worth having, because it makes `pip
    # install aelix` refuse cleanly instead of breaking later (#192 left it to
    # #278), but it does not retire this flag.
    #
    # WHICH RANGE (#278, ADR-0265). 3.14 is supported from 0.1.0 on: that
    # release pins openai>=2.7.2, the first openai that does not write onto
    # typing.Union, and CI runs 3.14 on ubuntu and windows. But this script
    # installs whatever AELIX_VERSION names, and 0.1.0b2 carries openai<2.0
    # forever: a default that simply widened would hand a pinned
    # AELIX_VERSION=v0.1.0-beta.2 install exactly the crash this flag was added
    # to stop. So the request is derived from the release being installed:
    # Get-PythonRequest gives '>=3.11,<3.15' to 0.1.0b3 and later and keeps
    # '>=3.11,<3.14' for anything older or unparseable. The version is the one
    # parsed above from the VERIFIED manifest, never the tag.
    #
    # THE EXTENSIONS IN THE CURRENT ENVIRONMENT (#926, ADR-0321), as install.sh.
    # `uv tool install --force` rebuilds the tool environment from its
    # uv-receipt.toml, and an extension `aelix extension install` put there with
    # `uv pip install` is not in it, so a re-run of this script used to delete
    # every extension. Before the reinstall the current environment's OWN python
    # reads what it holds (carry.py below, `collect`); the reinstall takes it as
    # --with-requirements, so it lands in the new receipt; the NEW environment's
    # python (`check`) keeps an extension only when the bytes of its files hash as
    # before. When the reinstall fails with them, aelix is installed alone and they
    # are added back one at a time, so one extension uv cannot install with this
    # release leaves the others in; each one left out is named. An extension never
    # fails the update.
    function Install-Aelix {
        param([string[]]$Extra = @())
        Invoke-Native {
            & uv tool install --force --find-links $tmp --python $AelixPython $target @Extra
        }
    }
    # Every native program here is read by its $LASTEXITCODE. With pwsh 7.3+'s
    # $PSNativeCommandUseErrorActionPreference = $true, a non-zero exit under
    # ErrorActionPreference Stop raises first (Codex review round 2 S2: one extension uv
    # could not install stopped the update with aelix not updated). This function's
    # own copy is off, for the call it makes; the session's is left as it was.
    function Invoke-Native {
        param([scriptblock]$Command)
        $PSNativeCommandUseErrorActionPreference = $false
        & $Command
    }
    function Test-NonEmpty {
        param([string]$Path)
        (Test-Path -LiteralPath $Path) -and ((Get-Item -LiteralPath $Path).Length -gt 0)
    }
    function Read-Lines {
        param([string]$Path)
        if (-not (Test-Path -LiteralPath $Path)) { return @() }
        return @(Get-Content -LiteralPath $Path -Encoding UTF8)
    }
    function Write-Lines {
        param([string]$Path, [string[]]$Lines = @())
        [System.IO.File]::WriteAllText($Path, ((@($Lines) | ForEach-Object { "$_`n" }) -join ''))
    }
    $installFailed = "uv tool install failed for '$target' (interpreter request: '$AelixPython'). If uv reported no interpreter for that range, this machine has none inside it and managed downloads are off (UV_PYTHON_DOWNLOADS=never): install one, or set AELIX_PYTHON -- but read Step 5 first: a release up to 0.1.0b2 pins openai<2.0, which dies mid-turn on 3.14 (#262)."
    $carryDir = Join-Path $tmp 'carry'
    New-Item -ItemType Directory -Path $carryDir -Force | Out-Null
    $carryPy = Join-Path $tmp 'carry.py'
    $carrySource = @'
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
'@
    [System.IO.File]::WriteAllText($carryPy, $carrySource)
    $withFile = Join-Path $carryDir 'with.txt'
    $restoreFile = Join-Path $carryDir 'restore.txt'
    $failedFile = Join-Path $carryDir 'failed.txt'
    $inFile = Join-Path $carryDir 'in.txt'
    # The extensions an environment's own files name, for when its python cannot
    # read them (aelix's own distributions left out): the project name and version
    # its METADATA gives, else the dist-info directory's, said to be that.
    function Get-ExtensionNamesFromFiles {
        param([string]$EnvDir)
        $site = Join-Path $EnvDir 'Lib\site-packages'
        $names = @()
        if (-not (Test-Path -LiteralPath $site)) { return $names }
        foreach ($info in @(Get-ChildItem -LiteralPath $site -Directory -Filter '*.dist-info' -ErrorAction SilentlyContinue)) {
            $ep = Join-Path $info.FullName 'entry_points.txt'
            if (-not (Test-Path -LiteralPath $ep)) { continue }
            if (-not (Select-String -LiteralPath $ep -Pattern '^\[aelix\.extensions\]' -Quiet)) { continue }
            $stem = $info.Name.Substring(0, $info.Name.Length - '.dist-info'.Length)
            if ($stem -match '^(aelix|aelix_ai|aelix_agent_core|aelix_coding_agent|aelix_server)-') { continue }
            $project = ''
            $release = ''
            try {
                foreach ($line in @(Get-Content -LiteralPath (Join-Path $info.FullName 'METADATA') -Encoding UTF8 -ErrorAction Stop)) {
                    if ($line -eq '') { break }
                    $m = [regex]::Match($line, '^Name:\s*(\S.*)$')
                    if (-not $project -and $m.Success) { $project = $m.Groups[1].Value.Trim() }
                    $m = [regex]::Match($line, '^Version:\s*(\S.*)$')
                    if (-not $release -and $m.Success) { $release = $m.Groups[1].Value.Trim() }
                }
            } catch {
                $project = ''
            }
            if ($project) { $names += ($project + ' ' + $release); continue }
            $cut = $stem.IndexOf('-')
            if ($cut -gt 0) { $shown = $stem.Substring(0, $cut) + ' ' + $stem.Substring($cut + 1) } else { $shown = $stem }
            $names += ($shown + ' (named from its directory: its METADATA cannot be read)')
        }
        return $names
    }
    # The line naming each requirement in a file as not carried.
    function Write-NotCarried {
        param([string]$Requirements)
        $all = @(Read-Lines $withFile)
        $msgs = @(Read-Lines $restoreFile)
        foreach ($line in @(Read-Lines $Requirements)) {
            $k = [Array]::IndexOf([string[]]$all, [string]$line)
            if ($k -ge 0) { Write-Host $msgs[$k] }
        }
    }
    # aelix is installed alone; add the carried requirements one at a time on top
    # of the ones that went in, and name each one uv cannot install with this
    # release. A failed build removes the environment (uv 0.11.19 and 0.13.10), so
    # when the last try failed, what went in is installed again.
    function Add-CarriedBack {
        $all = @(Read-Lines $withFile)
        # Why each is left out after uv could not install it with this release; its
        # command, where there is one, is one that can work, not the same source again.
        $fails = @(Read-Lines $failedFile | ForEach-Object { ([string]$_).Replace('@VERSION@', [string]$version) })
        $in = @()
        Write-Lines $inFile $in
        if ($all.Count -eq 1) {
            foreach ($m in $fails) { Write-Host $m }
            return
        }
        $tryFile = Join-Path $carryDir 'try.txt'
        $last = $true
        for ($i = 0; $i -lt $all.Count; $i++) {
            $try = @($in) + @($all[$i])
            Write-Lines $tryFile $try
            Install-Aelix -Extra @('--with-requirements', $tryFile)
            if ($LASTEXITCODE -eq 0) {
                $in = $try
                $last = $true
            } else {
                # On a line of its own: uv's last message may not end its line.
                [Console]::Error.WriteLine()
                Write-Host $fails[$i]
                $last = $false
            }
        }
        Write-Lines $inFile $in
        if ($last) { return }
        if ($in.Count -gt 0) {
            Install-Aelix -Extra @('--with-requirements', $inFile)
            if ($LASTEXITCODE -eq 0) { return }
        }
        Install-Aelix
        if ($LASTEXITCODE -ne 0) { Stop-WithError $installFailed }
        Write-NotCarried $inFile
        Write-Lines $inFile @()
    }

    $envDir = $null
    $toolDir = (Invoke-Native { & uv tool dir } | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $toolDir -and (Test-Path -LiteralPath (Join-Path $toolDir 'aelix\uv-receipt.toml'))) {
        $envDir = Join-Path $toolDir 'aelix'
    }
    $carried = $false
    $unread = @()
    if ($envDir) {
        $envPython = Join-Path $envDir 'Scripts\python.exe'
        Write-Log "reading what the current aelix environment holds ($envDir)..."
        $runs = $false
        try {
            # Not -c '': Windows PowerShell 5.1 (and pwsh's Legacy argument passing)
            # drops an empty argument, and Python then exits 2 on a healthy environment
            # (Codex review round 2 M2).
            Invoke-Native { & $envPython -I -c 'pass' 2>$null } | Out-Null
            $runs = ($LASTEXITCODE -eq 0)
        } catch {
            $runs = $false
        }
        if (-not $runs) {
            Write-Log "the current aelix environment's python does not run, so nothing in it can be carried over."
            $unread = @(Get-ExtensionNamesFromFiles $envDir)
        } else {
            $collected = 1
            try {
                Invoke-Native { & $envPython -I $carryPy collect $envDir $carryDir }
                $collected = $LASTEXITCODE
            } catch {
                $collected = 1
            }
            if ($collected -eq 0) {
                $carried = Test-NonEmpty $withFile
            } else {
                Write-Log 'could not read the current aelix environment; nothing in it is carried over.'
                $unread = @(Get-ExtensionNamesFromFiles $envDir)
            }
        }
    }
    if (-not $carried) {
        Install-Aelix
        if ($LASTEXITCODE -ne 0) { Stop-WithError $installFailed }
    } else {
        Copy-Item -LiteralPath $withFile -Destination $inFile -Force
        Install-Aelix -Extra @('--with-requirements', $inFile)
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine()  # uv's last message may not end its line
            Write-Log 'the reinstall with the carried requirements failed; installing aelix alone, then adding them back one at a time.'
            Install-Aelix
            if ($LASTEXITCODE -ne 0) { Stop-WithError $installFailed }
            Add-CarriedBack
        }
        if (Test-NonEmpty $inFile) {
            $check = 1
            try {
                Invoke-Native { & $envPython -I $carryPy check $carryDir $inFile }
                $check = $LASTEXITCODE
            } catch {
                $check = 1
            }
            $keptFile = Join-Path $carryDir 'kept.txt'
            if ($check -eq 3) {
                Write-Log 'reinstalling without what was taken out...'
                if (-not (Test-NonEmpty $keptFile)) {
                    Install-Aelix
                    if ($LASTEXITCODE -ne 0) { Stop-WithError $installFailed }
                    $carried = $false
                } else {
                    Install-Aelix -Extra @('--with-requirements', $keptFile)
                    if ($LASTEXITCODE -ne 0) {
                        [Console]::Error.WriteLine()
                        Write-Log 'that reinstall failed; installing aelix with nothing carried over.'
                        Install-Aelix
                        if ($LASTEXITCODE -ne 0) { Stop-WithError $installFailed }
                        Write-NotCarried $keptFile
                        $carried = $false
                    }
                }
            } elseif ($check -ne 0) {
                Write-Log "could not compare the carried extensions (exit $check); installing aelix with nothing carried over."
                Install-Aelix
                if ($LASTEXITCODE -ne 0) { Stop-WithError $installFailed }
                Write-NotCarried $inFile
                $carried = $false
            }
        } else {
            $carried = $false
        }
    }
    if ($unread.Count -gt 0) {
        foreach ($name in $unread) {
            Write-Log "not carried: $name - the current environment could not be read"
        }
        $hasRestore = $false
        try {
            Invoke-Native { & (Join-Path $envDir 'Scripts\aelix.exe') extension restore --help 2>$null } | Out-Null
            $hasRestore = ($LASTEXITCODE -eq 0)
        } catch {
            $hasRestore = $false
        }
        if ($hasRestore) {
            Write-Log "'aelix extension restore --all' puts back each one 'aelix extension install' recorded at a pin; the next interactive aelix start does it too."
        } else {
            Write-Log "aelix $version has no restore command: reinstall each with 'aelix extension install <the git URL, path or name you installed it from>'."
        }
    }

    # The carried extensions are what was installed before, but no aelix pin check
    # ran on them here (ADR-0321): the new aelix's own import-free verdict says
    # whether each binds on this release. It is printed; the update is kept.
    $extFile = Join-Path $carryDir 'extensions.txt'
    if ($carried -and (Test-NonEmpty $extFile)) {
        $names = @(Read-Lines $extFile) -join ', '
        Write-Log "carried over: $names. Checking them with the new aelix:"
        $verify = 2
        try {
            Invoke-Native { & (Join-Path $envDir 'Scripts\aelix.exe') extension verify }
            $verify = $LASTEXITCODE
        } catch {
            $verify = 2
        }
        if ($verify -eq 0) {
            Write-Log "every carried extension binds on aelix $version."
        } elseif ($verify -eq 1) {
            Write-Log "an extension above does not bind on aelix $version. The update is kept, and aelix leaves out what does not bind; 'aelix extension remove <name>' removes one."
        } else {
            Write-Log "'aelix extension verify' exited $verify; run it again after the install to see each extension's verdict."
        }
    }

    # -- Step 6: post-install smoke + PATH hint ------------------------------
    if (Test-Have 'aelix') {
        Invoke-Native { & aelix --version }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "installed, but 'aelix --version' returned non-zero."
        }
        Write-Log "done. 'aelix' is on your PATH."
    } else {
        # Same as install.sh step 6: the launcher is in uv's tool bin but this
        # session cannot see it. Run `uv tool update-shell` now (it appends the
        # tool bin to the user PATH, idempotently) instead of asking the user
        # to; it cannot fix THIS session, hence the "new terminal" line.
        Write-Log "installed. The 'aelix' launcher is in uv's tool bin (usually ~\.local\bin), which is not on this session's PATH."
        Invoke-Native { & uv tool update-shell }
        if ($LASTEXITCODE -eq 0) {
            Write-Log "PATH updated for future sessions. Open a new terminal and run: aelix --version"
        } else {
            Write-Log "'uv tool update-shell' failed; add uv's tool bin to PATH by hand (uv tool dir --bin) and re-run: aelix --version"
        }
    }
}
finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
