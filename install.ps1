# Aelix installer for Windows -- EXPERIMENTAL.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
#
# EXPERIMENTAL (#106). What CI measures: this script runs end to end on
# windows-latest, under both pwsh 7 and Windows PowerShell 5.1 (the
# `install.ps1 e2e (pwsh)` / `install.ps1 e2e (powershell)` jobs in
# .github/workflows/ci.yml), and the full test suite is green on the same
# runner under 3.11 and 3.12. What CI does not cover: the agent itself. That
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
#   AELIX_VERSION  Pin an exact release tag (e.g. v0.1.0-beta.2, the release
#                  Windows needs). Default: resolve the newest release from the
#                  GitHub API. Pinning is the recommended path during the beta.
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
#   AELIX_PYTHON   uv interpreter request for the tool environment. Default
#                  `>=3.11,<3.14`, the range the pinned openai<2.0 survives.
#                  NOT "the range CI runs": CI runs 3.11 and 3.12, and 3.13 is
#                  in here because it works, not because anything gates it
#                  (#192 adds it to the matrix). Step 5 explains the ceiling.
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
$AelixPython  = if ($env:AELIX_PYTHON) { $env:AELIX_PYTHON } else { '>=3.11,<3.14' }
$UvVersion    = if ($env:UV_VERSION) { $env:UV_VERSION } else { '' }
$GithubToken  = if ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { '' }

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
        # /releases/latest), so the first tag_name is the newest beta during beta.
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

    Write-Log "installing $target with uv (version pinned from the verified SHA256SUMS)..."
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
    # 3.11-3.13 and downloads only when it has none (measured both ways).
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
    # reads the ceiling, and no user of this script takes it. #192's bound is
    # still worth having, because it makes `pip install aelix` refuse cleanly
    # instead of breaking later, but it does not retire this flag.
    #
    # WIDENING IS NOT A ONE-LINE EDIT. When 3.14 becomes supported (#262: the
    # floor is openai>=2.7.2), raising the default here reaches EVERY release
    # this script can install, and AELIX_VERSION pins arbitrarily old tags:
    # 0.1.0b2 will carry openai<2.0 forever. The default can widen only once
    # no installable release breaks on the wider range, or once the request is
    # derived from the release being installed.
    & uv tool install --force --find-links $tmp --python $AelixPython $target
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "uv tool install failed for '$target' (interpreter request: '$AelixPython'). If uv reported no interpreter for that range, this machine has no Python 3.11-3.13 and managed downloads are off (UV_PYTHON_DOWNLOADS=never): install one, or set AELIX_PYTHON -- but read Step 5 first, widening past 3.13 is not free."
    }

    # -- Step 6: post-install smoke + PATH hint ------------------------------
    if (Test-Have 'aelix') {
        & aelix --version
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
        & uv tool update-shell
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
