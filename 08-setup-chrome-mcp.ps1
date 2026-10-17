#requires -Version 5.1
<#
.SYNOPSIS
    Build the vendored chrome-mcp, install its Chrome extension folder, and (re)register it with Hermes.
.DESCRIPTION
    chrome-mcp/ is a vendored copy of github.com/Mehmoodqureshi/chrome-mcp (see chrome-mcp/VENDORED.md).
    It replaces webmcp: instead of a separate automated browser profile, Hermes drives YOUR real,
    logged-in Chrome through an MV3 extension, which is what gets past Cloudflare/bot checks.

    What this does (idempotent -- safe to re-run after editing -Sites or after `git pull`):
      1. npm ci --ignore-scripts + build the server and the extension (needs Node >= 18 on PATH).
      2. Install the extension to %USERPROFILE%\chrome-mcp-extension (the folder you "Load unpacked").
      3. `hermes mcp remove` + `hermes mcp add chrome-mcp ...` with one --allow-domain per site AND
         per `*.site` (the allowlist is exact-hostname: `tipranks.com` does not cover www.tipranks.com).
      4. `hermes gateway restart` so the gateway spawns the new server (skip with -NoRestart).

    Manual steps this cannot do (Chrome UI): chrome://extensions -> Developer mode -> Load unpacked
    -> the folder printed above, then click Connect in the extension popup. With --persist-token the
    pairing survives restarts.

    Deliberate choices (2026-10-13):
      * Runs OUR vendored build (`node ...\dist\src\cli.js`), never `npx @latest`, with
        --no-auto-update and --no-telemetry, so no unreviewed upstream code or phone-home runs.
      * --enable-mutations is on (needed for navigate/click/type). The domain allowlist, not that flag,
        is the guard; eval, downloads and uploads stay off.
      * Chrome is assumed to be running. If it is not, tools fail (and so do scheduled jobs that use
        them) -- there is intentionally no headless fallback.
      * `tabs_list` exposes the titles/URLs of ALL open tabs regardless of the allowlist.
      * The registration does not use `--http`/`--tunnel`; the SDK's HTTP-stack dependencies have open
        `npm audit` findings that only matter in those modes.
.PARAMETER Sites
    Registrable domains to allow (each also allows its subdomains). Default: the current set.
.PARAMETER NoRestart
    Register but do not restart the gateway.
.PARAMETER SkipBuild
    Skip npm ci/build (use when dist/ and extension-dist/ are already current).
#>
[CmdletBinding()]
param(
    [string[]]$Sites = @('tipranks.com', 'facebook.com', 'linkedin.com', 'substack.com', 'medium.com', 'orats.com', 'finance.yahoo.com'),
    [switch]$NoRestart,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'

$Root = Join-Path $PSScriptRoot 'chrome-mcp'
$Cli  = Join-Path $Root 'dist\src\cli.js'

# Native commands under $ErrorActionPreference=Stop throw on stderr output; run them with Continue
# and judge success by $LASTEXITCODE only (see CLAUDE.md "Native command calls...").
function Invoke-Native {
    param([string]$What, [scriptblock]$Block)
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $Block 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ } | ForEach-Object { Write-Host "    $_" } }
    finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) { throw 'node not found on PATH (need >= 18).' }
$hermes = (Get-Command hermes -ErrorAction SilentlyContinue)
if (-not $hermes) { throw 'hermes not found on PATH.' }

if (-not $SkipBuild) {
    Write-Host '[1/4] Building chrome-mcp...' -ForegroundColor Cyan
    $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
    Push-Location $Root
    try {
        Invoke-Native 'npm ci'          { npm ci --ignore-scripts }
        Invoke-Native 'npm run build'   { npm run build }
        Invoke-Native 'npm run build:ext' { npm run build:ext }
    } finally { Pop-Location }
} else { Write-Host '[1/4] Build skipped.' -ForegroundColor DarkGray }
if (-not (Test-Path $Cli)) { throw "Missing $Cli -- run without -SkipBuild." }

Write-Host '[2/4] Installing extension folder...' -ForegroundColor Cyan
Invoke-Native 'extension install' { node $Cli --extension-path --no-telemetry --no-auto-update }

Write-Host "[3/4] Registering with Hermes (sites: $($Sites -join ', '))..." -ForegroundColor Cyan
$flags = foreach ($s in $Sites) { '--allow-domain'; $s; '--allow-domain'; "*.$s" }
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try {
    & hermes mcp remove chrome-mcp *> $null     # exit code is nonzero when it was not registered yet
    # "Y" answers `Enable all N tools?`
    'Y' | & hermes mcp add chrome-mcp --command node --args $Cli @flags `
        --enable-mutations --persist-token --no-auto-update --no-telemetry 2>&1 |
        Where-Object { $_ -match 'Saved|error|fail' } | ForEach-Object { Write-Host "    $_" }
    $addExit = $LASTEXITCODE
} finally { $ErrorActionPreference = $prev }
if ($addExit -ne 0) { throw "hermes mcp add failed (exit $addExit)" }

if ($NoRestart) {
    Write-Host '[4/4] Gateway restart skipped -- run `hermes gateway restart` to load the server.' -ForegroundColor Yellow
} else {
    Write-Host '[4/4] Restarting gateway...' -ForegroundColor Cyan
    Invoke-Native 'hermes gateway restart' { hermes gateway restart }
}

Write-Host @"

Done. In Chrome (once per profile):
  1. chrome://extensions -> Developer mode -> Load unpacked -> $env:USERPROFILE\chrome-mcp-extension
  2. Click the "MCP Browser Extension" toolbar icon -> Connect.
Chrome must stay running: with it closed, chrome-mcp tools (and scheduled jobs using them) fail.
"@ -ForegroundColor Green
