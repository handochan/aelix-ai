# Post-conditions for install.ps1 (#106), asserted from a SEPARATE step so the
# install step stays byte-identical to the documented one-liner.
#
# Scope discipline: only what install.ps1 itself promises. It does not promise
# that aelix runs a turn, that the TUI draws, or that PATH survives a logout --
# see README.md "Platform support" for what is still unverified on Windows.
#
# ASCII only, for install.ps1's reason and on the same host: the `powershell`
# leg is Windows PowerShell 5.1, which parses a BOM-less .ps1 as the ANSI code
# page and there turns a UTF-8 em dash into a character its tokenizer reads as
# a double quote. See install.ps1's header for the run that proved it.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This is a NEW process; install.ps1 Step 0's `SecurityProtocol` TLS 1.2 opt-in did not survive it, and
# Windows PowerShell 5.1 can still default to TLS 1.0, which GitHub refuses.
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Assert-Fail {
    param([string]$Message)
    Write-Host "::error::install.ps1 e2e: $Message"
    exit 1
}

# -- The expected version, resolved INDEPENDENTLY of the script ---------------
# install.ps1 resolves the tag with Invoke-RestMethod and parses SHA256SUMS with
# its own regex. This resolves the same two facts through `gh api --jq`, so a
# wrong tag or a wrong wheel on either side surfaces as a DISAGREEMENT rather
# than as two copies of one bug.
#
# The repo is hard-coded rather than ${{ github.repository }}: the install step
# leaves AELIX_REPO unset on purpose, so the script used its own default
# `handochan/aelix-ai`. On a fork, github.repository is the fork and would make
# this assertion compare two different releases.
$repo = 'handochan/aelix-ai'

$tag = (& gh api "repos/$repo/releases" --jq '.[0].tag_name' | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $tag) {
    Assert-Fail "could not resolve the newest release tag for '$repo' (gh exit $LASTEXITCODE)."
}

# -OutFile, not .Content. GitHub serves release assets as
# application/octet-stream, so PowerShell will not decode the body: .Content
# comes back as a byte[] on BOTH hosts, [regex]::Match stringifies that to
# "System.Byte[]", and the anchor below then matches nothing. That is how this
# script failed in run 33864409925 (jobs 100995864248 pwsh, 100995864418
# Windows PowerShell 5.1) on a run where install.ps1 itself had already
# finished green -- the same content-type trap that broke install.ps1's uv
# bootstrap one run earlier. -OutFile sidesteps the content type entirely,
# which is why install.ps1's Get-File fetches this same file the same way.
$sumsFile = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("aelix-e2e-sums-" + [Guid]::NewGuid().ToString('N'))
Invoke-WebRequest -UseBasicParsing -OutFile $sumsFile `
    -Uri "https://github.com/$repo/releases/download/$tag/SHA256SUMS"
$sums = Get-Content -Raw -LiteralPath $sumsFile
# Named aelix-e2e-sums-*, not aelix-install-*: the temp-dir assertion at the
# bottom of this file would otherwise be asserting against our own litter.
Remove-Item -LiteralPath $sumsFile -Force -ErrorAction SilentlyContinue
# Same anchor install.ps1 Step 5 uses (the `^aelix-([^-]+)-py3-none-any\.whl$`
# match): the meta-package is the only `aelix-` entry,
# its siblings escape the hyphen to an underscore (aelix_ai-, aelix_agent_core-,
# aelix_coding_agent-), and a PEP 440 version can never contain a hyphen.
$m = [regex]::Match($sums, '(?m)^\s*[0-9a-fA-F]{64}\s+\*?aelix-([^-\s]+)-py3-none-any\.whl\s*$')
if (-not $m.Success) {
    Assert-Fail "no 'aelix-<version>-py3-none-any.whl' entry in SHA256SUMS for '$tag'."
}
$expected = $m.Groups[1].Value
Write-Host "expected version (from $tag SHA256SUMS): $expected"

# -- (1) The launcher is where Step 6 says it is ------------------------------
$binDir = (& uv tool dir --bin | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $binDir) {
    Assert-Fail "'uv tool dir --bin' failed (exit $LASTEXITCODE)."
}
$launcher = Join-Path $binDir 'aelix.exe'
if (-not (Test-Path -LiteralPath $launcher)) {
    Assert-Fail "no launcher at '$launcher' (uv tool bin = '$binDir'). install.ps1 Step 6 promises it lands there."
}

# `aelix` on PATH in a LATER step is not free: it works only because the Astral
# installer appends its install dir to $env:GITHUB_PATH (uv install.ps1 0.12.9,
# Add-Ci-Path). Assert it, and assert it resolves to the launcher above rather
# than to some other aelix.
$onPath = Get-Command aelix -ErrorAction SilentlyContinue
if (-not $onPath) {
    Assert-Fail "'aelix' is not on PATH in this step; uv's bin dir ('$binDir') did not reach GITHUB_PATH."
}
$resolved = (Resolve-Path -LiteralPath $onPath.Source).Path
$expectedPath = (Resolve-Path -LiteralPath $launcher).Path
if ($resolved -ine $expectedPath) {
    Assert-Fail "'aelix' resolves to '$resolved', not the uv tool launcher '$expectedPath'."
}
Write-Host "launcher: $expectedPath"

# -- (2) --version is the version the VERIFIED manifest named -----------------
# The version pin in install.ps1 Step 5 (`==$version` in `$target`) is the
# whole point of Step 4's checksum gate: only
# the checksum-verified wheel can satisfy `==$version`. If what got installed
# reports a different version, a PyPI candidate outranked the local wheels and
# the gate verified artifacts the install discarded.
#
# `aelix --version` prints the bare PEP 440 string and nothing else -- the
# released 0.1.0b1 build does `print(VERSION)` (cli/entry.py at v0.1.0-beta.1),
# so exact equality is assertable, not a substring match.
$actual = (& aelix --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Assert-Fail "'aelix --version' exited $LASTEXITCODE. Output: $actual"
}
if ($actual -cne $expected) {
    Assert-Fail "'aelix --version' printed '$actual'; SHA256SUMS for '$tag' named '$expected'."
}
Write-Host "aelix --version == $actual"

# uv's own record of what it installed, as a second witness to the pin.
$tools = (& uv tool list | Out-String)
if ($tools -notmatch "(?m)^aelix v$([regex]::Escape($expected))\b") {
    Assert-Fail "'uv tool list' does not report 'aelix v$expected':`n$tools"
}

# -- (3) The temp dir was cleaned ---------------------------------------------
# install.ps1 creates `aelix-install-<guid>` under GetTempPath() before Step 1,
# and the `finally { Remove-Item ... }` block removes it. This is the ONLY assertion that proves the
# finally still runs when the whole file is Invoke-Expression'd -- the same call
# as the script so TEMP/TMP resolution cannot diverge.
$tempRoot = [System.IO.Path]::GetTempPath()
$leftovers = @(Get-ChildItem -LiteralPath $tempRoot -Filter 'aelix-install-*' `
    -Directory -ErrorAction SilentlyContinue)
if ($leftovers.Count -ne 0) {
    Assert-Fail "install.ps1 left $($leftovers.Count) download dir(s) under '$tempRoot': $(($leftovers | ForEach-Object { $_.FullName }) -join ', ')"
}
Write-Host "temp dir cleaned under $tempRoot"

Write-Host "install.ps1 e2e: all post-conditions hold."
