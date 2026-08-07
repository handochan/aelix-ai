# Aelix installer for Windows — EXPERIMENTAL.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
#
# EXPERIMENTAL (#106). Windows support is a parallel, unreleased track: this
# script has never been executed on a Windows host by CI or by the maintainers,
# and the agent itself still has known Windows gaps (see SLICE-STATUS.md).
# Treat a successful install as the beginning of the test, not the end of it.
# The supported platforms today are Linux and macOS, via install.sh.
#
# It mirrors install.sh step for step: download the release wheels from the
# GitHub Release, verify each one against the published SHA256SUMS manifest (a
# hard security gate — any mismatch aborts), then install the `aelix` CLI with
# uv. Third-party dependencies resolve from PyPI as usual; the four first-party
# wheels come from the checksum-verified download (uv --find-links, never
# --no-index).
#
# Configuration (all optional, via environment):
#   AELIX_VERSION  Pin an exact release tag (e.g. v0.1.0-beta.1). Default:
#                  resolve the newest release from the GitHub API. Pinning is
#                  the recommended path during the beta.
#   AELIX_EXTRAS   Extras to install, consumed as aelix[$AELIX_EXTRAS].
#                  Default `tui` (interactive terminal UI). Use `tui,images`
#                  for inline image rendering.
#                  DIVERGENCE from install.sh: there, a set-but-empty
#                  AELIX_EXTRAS installs the bare CLI. Windows cannot express
#                  that — assigning '' to an environment variable DELETES it,
#                  so an empty value is indistinguishable from unset and falls
#                  back to `tui`. For the bare CLI, install `aelix` yourself:
#                  `uv tool install --force --find-links <dir> aelix`.
#   AELIX_REPO     GitHub owner/repo. Default `handochan/aelix-ai`.
#   UV_VERSION     Optional pin for the uv bootstrap (Astral installer).
#   GITHUB_TOKEN   Optional; sent as a Bearer token on GitHub API calls to
#                  avoid the 60/hr unauthenticated rate limit.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Step 0: preamble ────────────────────────────────────────────────────────
$AelixVersion = if ($env:AELIX_VERSION) { $env:AELIX_VERSION } else { '' }
$AelixExtras  = if ($null -ne $env:AELIX_EXTRAS) { $env:AELIX_EXTRAS } else { 'tui' }
$AelixRepo    = if ($env:AELIX_REPO) { $env:AELIX_REPO } else { 'handochan/aelix-ai' }
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

    # ── Step 1: prerequisites ───────────────────────────────────────────────
    # Nothing to check that install.sh checks: Invoke-WebRequest replaces
    # curl/wget and Get-FileHash replaces sha256sum, both built in since
    # PowerShell 4. The one hard requirement is the PowerShell version itself.
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        Stop-WithError "need PowerShell 5.1 or newer (found $($PSVersionTable.PSVersion))."
    }

    # ── Step 2: uv bootstrap (idempotent) ───────────────────────────────────
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
            $uvScript = (Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -UseBasicParsing).Content
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

    # ── Step 3: resolve the release tag ─────────────────────────────────────
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

    # ── Step 4: download + verify (the integrity gate) ──────────────────────
    $base = "https://github.com/$AelixRepo/releases/download/$tag"

    Write-Log 'downloading SHA256SUMS...'
    $sumsPath = Join-Path $tmp 'SHA256SUMS'
    try {
        Get-File -Url "$base/SHA256SUMS" -OutFile $sumsPath
    } catch {
        Stop-WithError "SHA256SUMS not found for '$tag' at $base — is the Release published? $($_.Exception.Message)"
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

    # ── Step 5: install (hybrid: local verified wheels + PyPI for the rest) ──
    $target = if ($AelixExtras) { "aelix[$AelixExtras]" } else { 'aelix' }

    Write-Log "installing $target with uv..."
    # --find-links ADDS the four checksum-verified local wheels as candidates;
    # the default PyPI index stays enabled so third-party dependencies resolve.
    # Never use --no-index (it would make transitive deps unresolvable).
    # --force makes re-runs idempotent.
    #
    # Parity note, carried deliberately from install.sh: the target is NOT
    # version-pinned. The pin is on the release TAG (which wheels get
    # downloaded and checksum-verified), not on what uv resolves. Should
    # `aelix` ever publish to PyPI, uv could prefer a newer index version over
    # the verified local wheel and quietly bypass the integrity gate. Fixing
    # that belongs in BOTH installers at once so they cannot drift.
    & uv tool install --force --find-links $tmp $target
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "uv tool install failed for '$target'."
    }

    # ── Step 6: post-install smoke + PATH hint ──────────────────────────────
    if (Test-Have 'aelix') {
        & aelix --version
        if ($LASTEXITCODE -ne 0) {
            Write-Log "installed, but 'aelix --version' returned non-zero."
        }
        Write-Log "done. 'aelix' is on your PATH."
    } else {
        Write-Log "installed. The 'aelix' launcher is in uv's tool bin (usually ~\.local\bin)."
        Write-Log "If 'aelix' is not found, add it to PATH with: uv tool update-shell"
    }
}
finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
