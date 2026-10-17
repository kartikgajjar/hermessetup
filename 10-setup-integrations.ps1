#requires -Version 5.1
<#
.SYNOPSIS
    Installs and registers Hermes's personal MCP integrations: olk (Outlook), olk-bulk, spotify, linkedin.
.DESCRIPTION
    A fresh clone + 02 gives you Hermes with `mcp_servers = {}`; 08 adds chrome-mcp. This covers the rest
    (see the notes-mcp-*.md files and CLAUDE.md for the reasoning behind each choice). Idempotent: an
    integration that is already registered is skipped (use -Force to remove + re-add), and a binary that
    is already at the pinned revision is not reinstalled. Supports -WhatIf.

    Pinned on purpose (never @latest), each reviewed before use:
      olk          github.com/rlrghb/olkcli @ fb5253c  (2026-10-17, includes the mail-delta from/receivedDateTime fix, PR #147)
      spotify-mcp  github.com/skar404/spotify-mcp @ a25a71c  (3 commits, single author, reviewed 2026-10-16)
      linkedin     mcp-server-linkedin 4.27.0 via uvx (@latest auto-updates)
    Bump a pin deliberately: read the diff first, then change the default below.

    Secrets: nothing secret is written to config.yaml. spotify uses ${SPOTIFY_*} placeholders that Hermes
    resolves from its own .env; this script reads only whether those keys exist, never their values.
    olk's OAuth tokens live in Windows Credential Manager, spotify's in ~\.config\spotify-mcp\token.json,
    linkedin's browser session in ~\.linkedin-mcp -- none of that is state this script (or 03/04) can
    recreate, so each needs its interactive login (printed at the end).

    olk write access is OPT-IN (-OlkWrite): move, delete and send then run with no Hermes approval prompt,
    so SOUL.md's confirm-first rules are the only gate. Without it olk is read-only.

    Not handled here: expertassist (its API key is entered interactively -- see notes-mcp-expertassist.md)
    and chrome-mcp (08).
.PARAMETER Account
    Outlook account for olk / olk-bulk.
.PARAMETER Only
    Subset of: olk, olk-bulk, spotify, linkedin. Default: all.
.PARAMETER OlkWrite
    Register olk with --allow-write=mail_move --allow-destructive=mail_delete --allow-send=mail_send.
.PARAMETER Force
    Remove and re-add integrations that are already registered.
.PARAMETER SkipInstall
    Register only; do not run `go install`.
.PARAMETER NoRestart
    Do not restart the gateway afterwards.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Account = "ekartka@hotmail.com",
    [ValidateSet("olk", "olk-bulk", "spotify", "linkedin")][string[]]$Only = @("olk", "olk-bulk", "spotify", "linkedin"),
    [string]$OlkRef = "fb5253cc5e86815cf163f3366d10545724b32205",
    [string]$SpotifyRef = "a25a71c615c1cddae7655e1f283843d3374c4ef5",
    [string]$LinkedinVersion = "4.27.0",
    [switch]$OlkWrite,
    [switch]$Force,
    [switch]$SkipInstall,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$Manual = New-Object System.Collections.Generic.List[string]
$Changed = $false

# Native commands under $ErrorActionPreference = "Stop" throw on stderr output; run them with Continue
# and judge success by $LASTEXITCODE only (see CLAUDE.md "Native command calls...").
function Invoke-Native([scriptblock]$Block) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $Block 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $prev }
    return [pscustomobject]@{ Exit = $LASTEXITCODE; Text = ($out -join "`n") }
}

function Test-Tool([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Test-Registered([string]$Name) {
    $r = Invoke-Native { hermes mcp list }
    return [bool]($r.Text -split "`n" | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s" })
}

# Adds the MCP server unless it exists (-Force replaces). $AddArgs go after `hermes mcp add <name>`.
function Register-Mcp([string]$Name, [string[]]$AddArgs) {
    if (Test-Registered $Name) {
        if (-not $Force) { Write-Host "  $Name already registered -- skipped (-Force to re-add)"; return }
        if ($PSCmdlet.ShouldProcess($Name, "hermes mcp remove")) { Invoke-Native { hermes mcp remove $Name } | Out-Null }
    }
    if ($PSCmdlet.ShouldProcess($Name, "hermes mcp add")) {
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { $out = 'Y' | & hermes mcp add $Name @AddArgs 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $prev }
        if ($LASTEXITCODE -ne 0) { throw "hermes mcp add $Name failed (exit $LASTEXITCODE): $($out -join ' ')" }
        $out | Where-Object { $_ -match "Saved" } | ForEach-Object { Write-Host "  $_" }
        $script:Changed = $true
    }
}

# go install <module>@<ref>, unless the installed binary already carries that revision.
function Install-GoTool([string]$Module, [string]$Ref, [string]$Binary, [scriptblock]$InstalledRevision) {
    if (-not (Test-Tool "go")) { Write-Warning "  go not found on PATH -- cannot install $Binary (install Go, or use -SkipInstall)"; return $false }
    $have = ""
    # `go install` builds carry no ldflags version (`olk version` says "dev"), so read the module stamp.
    if (Test-Tool $Binary) { try { $have = [string](& $InstalledRevision) } catch { $have = "" } }
    if ($have -and $have.Contains($Ref.Substring(0, 12))) { Write-Host "  $Binary already at $($Ref.Substring(0, 12))"; return $true }
    if ($PSCmdlet.ShouldProcess("$Module@$Ref", "go install")) {
        $r = Invoke-Native { go install "$Module@$Ref" }
        if ($r.Exit -ne 0) { throw "go install $Module@$Ref failed: $($r.Text)" }
        Write-Host "  installed $Binary @ $($Ref.Substring(0, 7))"
    }
    return $true
}

# --- olk + olk-bulk -----------------------------------------------------------------------------------
if ($Only -contains "olk" -or $Only -contains "olk-bulk") {
    Write-Host "[olk] Outlook CLI / MCP" -ForegroundColor Cyan
    $ok = $true
    if (-not $SkipInstall) {
        $ok = Install-GoTool "github.com/rlrghb/olkcli/cmd/olk" $OlkRef "olk" { (& go version -m (Get-Command olk).Source 2>&1 | Out-String) }
    }
    if ($ok -and (Test-Tool "olk")) {
        if ($Only -contains "olk") {
            $a = @("--command", "olk", "--args", "mcp", "--account", $Account)
            if ($OlkWrite) { $a += @("--allow-write=mail_move", "--allow-destructive=mail_delete", "--allow-send=mail_send") }
            Register-Mcp "olk" $a
            if (-not $OlkWrite) { Write-Host "  olk is READ-ONLY (add -OlkWrite for move/delete/send)" }
        }
        if ($Only -contains "olk-bulk") {
            $py = Get-ChildItem (Join-Path $HermesHome "tools") -Directory -Filter "python-*" -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName "python.exe" } | Where-Object { Test-Path $_ } | Select-Object -First 1
            if (-not $py) { Write-Warning "  Hermes's bundled python not found under $HermesHome\tools -- skipping olk-bulk" }
            else {
                # The interpreter path is stored verbatim; 05-update-hermes.ps1 repoints it after a Python bump.
                Register-Mcp "olk-bulk" @("--command", ($py -replace "\\", "/"), "--args", (Join-Path $PSScriptRoot "olk-bulk-mcp.py"), "--account", $Account)
            }
        }
        $Manual.Add("olk auth login --account $Account   (device-code login; complete it as THAT account, then verify with: olk auth status --account $Account)")
    } elseif (-not $ok) { Write-Warning "  olk skipped" }
}

# --- spotify ------------------------------------------------------------------------------------------
if ($Only -contains "spotify") {
    Write-Host "[spotify] Spotify MCP" -ForegroundColor Cyan
    $ok = $true
    if (-not $SkipInstall) {
        $ok = Install-GoTool "github.com/skar404/spotify-mcp/cmd/spotify-mcp" $SpotifyRef "spotify-mcp" {
            $exe = (Get-Command spotify-mcp).Source
            (& go version -m $exe 2>&1 | Out-String)
        }
    }
    if ($ok -and (Test-Tool "spotify-mcp")) {
        $envFile = Join-Path $HermesHome ".env"
        $have = @{}
        if (Test-Path $envFile) { Get-Content $envFile | ForEach-Object { if ($_ -match "^\s*(SPOTIFY_[A-Z_]+)\s*=\s*\S") { $have[$Matches[1]] = $true } } }
        $missing = @("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI") | Where-Object { -not $have[$_] }
        if ($missing) {
            Write-Warning "  Hermes's .env lacks: $($missing -join ', ') -- not registering spotify yet"
            $Manual.Add("spotify: create an app at developer.spotify.com/dashboard (redirect URI http://127.0.0.1:8080/callback -- the IP literal, not localhost), then add SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET and SPOTIFY_REDIRECT_URI to $envFile and re-run: .\10-setup-integrations.ps1 -Only spotify")
        } else {
            if (-not $have["SPOTIFY_TOKEN_PATH"] -and $PSCmdlet.ShouldProcess($envFile, "append SPOTIFY_TOKEN_PATH")) {
                # stdio servers get a filtered environment (no USERPROFILE), so pass the token path explicitly.
                Add-Content -Path $envFile -Value "SPOTIFY_TOKEN_PATH=$env:USERPROFILE\.config\spotify-mcp\token.json"
            }
            $exe = (Get-Command spotify-mcp).Source -replace "\\", "/"
            Register-Mcp "spotify" @("--command", $exe, "--args", "serve", "--env",
                'SPOTIFY_CLIENT_ID=${SPOTIFY_CLIENT_ID}', 'SPOTIFY_CLIENT_SECRET=${SPOTIFY_CLIENT_SECRET}',
                'SPOTIFY_REDIRECT_URI=${SPOTIFY_REDIRECT_URI}', 'SPOTIFY_TOKEN_PATH=${SPOTIFY_TOKEN_PATH}')
            $Manual.Add('spotify login (the binary reads env vars only, not a .env file): load them, then authorize in the browser --' + "`n" +
                "      Get-Content `"$envFile`" | ForEach-Object { if (`$_ -match '^\s*(SPOTIFY_[A-Z_]+)\s*=\s*(.*)`$') { Set-Item `"env:`$(`$Matches[1])`" `$Matches[2].Trim() } }; spotify-mcp auth" + "`n" +
                "      (Spotify's refresh token expires after ~180 days: re-run the same.)")
        }
    } elseif (-not $ok) { Write-Warning "  spotify skipped" }
}

# --- linkedin -----------------------------------------------------------------------------------------
if ($Only -contains "linkedin") {
    Write-Host "[linkedin] LinkedIn MCP" -ForegroundColor Cyan
    if (-not (Test-Tool "uvx")) { Write-Warning "  uvx not found on PATH -- skipping linkedin" }
    else {
        Register-Mcp "linkedin" @("--command", "uvx", "--env", "UV_HTTP_TIMEOUT=300", "--connect-timeout", "120", "--args", "mcp-server-linkedin@$LinkedinVersion")
        $Manual.Add("linkedin login (saves a browser session to ~\.linkedin-mcp):  `$env:UV_HTTP_TIMEOUT=300; uvx mcp-server-linkedin@$LinkedinVersion --login")
    }
}

# --- gateway restart -----------------------------------------------------------------------------------
if ($Changed -and -not $NoRestart) {
    if ($PSCmdlet.ShouldProcess("gateway", "hermes gateway restart")) {
        Write-Host "Restarting gateway..." -ForegroundColor Cyan
        $r = Invoke-Native { hermes gateway restart }
        $r.Text -split "`n" | Where-Object { $_ } | ForEach-Object { Write-Host "  $_" }
    }
} elseif ($Changed) { Write-Host "Gateway NOT restarted (-NoRestart): run 'hermes gateway restart' to load the new servers." }

Write-Host ""
Write-Host "Done. Not automatable / still to do by hand:" -ForegroundColor Green
foreach ($m in $Manual) { Write-Host "  - $m" }
Write-Host "  - expertassist: add it yourself (API key is entered interactively) -- see notes-mcp-expertassist.md"
Write-Host "  - chrome-mcp: .\08-setup-chrome-mcp.ps1, then load the extension in Chrome and click Connect"
