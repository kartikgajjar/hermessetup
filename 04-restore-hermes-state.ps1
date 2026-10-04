#requires -Version 5.1
<#
.SYNOPSIS
    Restores Hermes's persistent configuration/state (config, credentials,
    identity, memory, skills, sessions, databases, cron jobs, platform
    pairings, plugins, gateway service files, autostart shortcut) from a
    03-backup-hermes-state.ps1 export onto a machine that already has a
    fresh, hardened Hermes install.

.DESCRIPTION
    Run this AFTER 02-install-hermes-secure.ps1 on the new/reinstalled
    machine, so the code checkout and Docker/approvals hardening already
    exist. This script only restores what 03-backup-hermes-state.ps1
    exported -- it never touches hermes-agent\, bin\, node\, tools\,
    installs\, or cache\.

    After restoring config.yaml (which may be an older copy, or one you'd
    since hand-edited), the terminal/approvals/MCP hardening is re-applied
    via `hermes config set` -- the same verified keys 02-install-hermes-secure.ps1
    uses -- so the restored config can never silently carry forward weaker
    settings than this machine's installed Hermes version supports.

    Credentials (auth.json, .env) ARE restored as of 2026-09-27 -- the
    backup now includes them by explicit decision, since this pair of
    scripts supports reinstalling Hermes daily with zero reconfiguration.

    gateway.pid / gateway.lock / gateway_state.json and other runtime
    identity files are never restored (03 never backs them up) -- the fresh
    install's own gateway writes new ones on first start, which is what you
    want; a carried-over stale one is exactly the bug class
    05-update-hermes.ps1 exists to work around.

    The gateway-service\ files are restored, but if you ever switch this
    gateway to a real Windows-service-managed instance (`hermes gateway
    install`), the actual Service Control Manager registration lives in the
    Windows registry, not in files -- re-run that command rather than
    expecting a file copy to recreate it. The Startup-folder autostart
    shortcut (Hermes_Gateway.vbs), which IS how this profile currently
    autostarts, is restored directly.

.PARAMETER Source
    Where the persistent-state backup lives. Defaults to
    C:\Users\<you>\OneDrive\Documents\Hermes.
#>
[CmdletBinding()]
param(
    [string]$Source = (Join-Path $env:USERPROFILE "OneDrive\Documents\Hermes")
)

$ErrorActionPreference = "Stop"

$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$StartupVbs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Hermes_Gateway.vbs"

# The installer produces bin\hermes.cmd for a git-method install (confirmed
# 2026-09-27) -- not bin\hermes.exe. Prefer PATH resolution; fall back to
# checking both under the known install layout.
$HermesExe = (Get-Command hermes -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $HermesExe) {
    $HermesExe = @("bin\hermes.cmd", "bin\hermes.exe") |
        ForEach-Object { Join-Path $HermesHome $_ } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}

Write-Host ""
Write-Host "Hermes state restore" -ForegroundColor Cyan
Write-Host "====================="
Write-Host "Source (backup)      : $Source"
Write-Host "Destination (Hermes) : $HermesHome"
Write-Host ""

if (-not (Test-Path $Source)) {
    throw "Backup not found at $Source."
}
if (-not $HermesExe) {
    throw "Could not find the 'hermes' launcher on PATH or under $HermesHome\bin. Run 02-install-hermes-secure.ps1 first, then re-run this script."
}

$StateFiles = @(
    "config.yaml", "SOUL.md", "channel_directory.json", "install_id",
    "auth.json", ".env"
)
$StateDatabases = @("state.db", "kanban.db", "projects.db", "shared-state.db")
$StateDirs = @(
    "memories", "skills", "sessions", "sandboxes",
    "cron", "pairing", "platforms", "plugins", "desktop-plugins",
    "plugin-update-checks", "gateway-service", "kanban"
)

# ---------------------------------------------------------------------------
# 1. Stop Hermes before overwriting its state files.
# ---------------------------------------------------------------------------

Write-Host "[1/5] Stopping Hermes..."

# Belt: literally-named hermes/hermes-agent processes (other install/invocation shapes).
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -match '^hermes(-agent)?$' } |
    ForEach-Object {
        try {
            Stop-Process -Id $_.Id -Force -ErrorAction Stop
            Write-Host "  Stopped PID $($_.Id) ($($_.ProcessName))"
        }
        catch {
            Write-Warning "  Could not stop PID $($_.Id): $_"
        }
    }

# Braces: this build's actual gateway runs as a bare `python.exe` (confirmed 2026-09-27) --
# see 03-backup-hermes-state.ps1's step 1 comment for why gateway.lock is read directly here.
$lockPath = Join-Path $HermesHome "gateway.lock"
if (Test-Path -LiteralPath $lockPath) {
    try {
        $lockRecord = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
        $gatewayPid = [int]$lockRecord.pid
        $proc = Get-Process -Id $gatewayPid -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $gatewayPid -Force -ErrorAction Stop
            Write-Host "  Stopped PID $gatewayPid (gateway, via gateway.lock)"
        }
    }
    catch {
        Write-Warning "  Could not stop gateway PID from gateway.lock: $_"
    }
}
Start-Sleep -Seconds 2

# ---------------------------------------------------------------------------
# 2. Restore the allow-listed state, overwriting the fresh install's
#    generated config.yaml/SOUL.md/DBs with the backed-up ones.
# ---------------------------------------------------------------------------

Write-Host "[2/5] Restoring persistent state..."

$restored = New-Object System.Collections.Generic.List[string]

foreach ($name in $StateFiles) {
    $src = Join-Path $Source $name
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $HermesHome $name) -Force
        $restored.Add($name)
    }
}

foreach ($db in $StateDatabases) {
    foreach ($suffix in @("", "-wal", "-shm")) {
        $fileName = "$db$suffix"
        $src = Join-Path $Source $fileName
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $HermesHome $fileName) -Force
            $restored.Add($fileName)
        }
    }
}

foreach ($dir in $StateDirs) {
    $src = Join-Path $Source $dir
    if (Test-Path -LiteralPath $src) {
        $dst = Join-Path $HermesHome $dir
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
        Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
        $restored.Add("$dir\")
    }
}

$srcVbs = Join-Path $Source "Hermes_Gateway.vbs"
if (Test-Path -LiteralPath $srcVbs) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $StartupVbs) -Force | Out-Null
    Copy-Item -LiteralPath $srcVbs -Destination $StartupVbs -Force
    $restored.Add("Hermes_Gateway.vbs (Startup autostart shortcut)")
}

foreach ($item in $restored) { Write-Host "  $item" }

# ---------------------------------------------------------------------------
# 3. Re-apply the hardened baseline on top of whatever config.yaml was
#    restored -- same keys/values as 02-install-hermes-secure.ps1, verified
#    against THIS machine's installed Hermes version.
# ---------------------------------------------------------------------------

Write-Host "[3/5] Re-applying hardened config over the restored state..."

function Set-HermesConfig {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Value
    )
    # --force: newer Hermes versions (confirmed 2026-09-27) refuse to set a
    # section key (e.g. mcp_servers) to a scalar/object without it -- "config
    # set" alone now errors "is a configuration section" for those. Harmless
    # for plain leaf keys.
    & $HermesExe config set --force $Key $Value | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "hermes config set '$Key' '$Value' failed (exit $LASTEXITCODE) -- config is only partially hardened."
    }
}

$HardenedSettings = [ordered]@{
    "terminal.backend"                       = "docker"
    "terminal.cwd"                            = "/workspace"
    "terminal.docker_image"                   = "nikolaik/python-nodejs:python3.11-nodejs20"
    "terminal.docker_mount_cwd_to_workspace"  = "false"
    "terminal.docker_volumes"                 = "[]"
    "terminal.docker_forward_env"             = "[]"
    "terminal.docker_env"                     = "{}"
    "terminal.docker_extra_args"              = "[]"
    "terminal.docker_network"                 = "false"
    "terminal.container_persistent"           = "true"
    "terminal.container_cpu"                  = "2"
    "terminal.container_memory"               = "4096"
    "terminal.timeout"                        = "180"
    "terminal.lifetime_seconds"               = "300"
    "approvals.mode"                          = "manual"
    "approvals.timeout"                       = "300"
    "approvals.cron_mode"                     = "deny"
    "approvals.single_query_mode"             = "deny"
    "approvals.unattended_mode"               = "deny"
    "approvals.mcp_reload_confirm"            = "true"
    "approvals.destructive_slash_confirm"     = "true"
    "command_allowlist"                       = "[]"
    "mcp_servers"                             = "{}"
}

foreach ($key in $HardenedSettings.Keys) {
    Set-HermesConfig -Key $key -Value $HardenedSettings[$key]
}
Write-Host "  Hardened terminal/approvals/MCP settings re-applied."

# ---------------------------------------------------------------------------
# 4. Pre-install optional per-platform packages (discord.py, etc.).
#
#    These live in the venv (installs\<hash>\...), which 03/04 deliberately
#    never backs up -- so a fresh install has the restored platform config/
#    tokens but not the Python package the gateway needs to use them, and
#    `hermes gateway run` stops to ask "Install it now? [Y/n]" interactively.
#    Confirmed 2026-09-27: restoring a config with DISCORD_BOT_TOKEN set
#    reproduced exactly this prompt on a fresh install. Detect which
#    platforms are actually configured from .env's token variables (the one
#    signal that survives the restore) and pre-install each via
#    `hermes pm install --extra <name>`, so this never blocks daily reinstalls.
# ---------------------------------------------------------------------------

Write-Host "[4/5] Pre-installing optional platform packages..."

# Only discord/telegram are real `hermes pm install --extra` names (confirmed
# 2026-09-27 against hermes-agent/pyproject.toml's [project.optional-dependencies]
# -- WhatsApp is a built-in Baileys bridge with no separate Python extra, and
# there's no Weixin extra either). Match only an actual UNCOMMENTED, non-empty
# assignment -- Hermes's own generated .env ships every platform's variables
# as commented-out template scaffolding (e.g. "# TELEGRAM_BOT_TOKEN="), which
# a plain substring match would false-positive on.
$envPath = Join-Path $HermesHome ".env"
$PlatformEnvMarkers = [ordered]@{
    "discord"   = "DISCORD_BOT_TOKEN"
    "telegram"  = "TELEGRAM_BOT_TOKEN"
}

if (Test-Path -LiteralPath $envPath) {
    $envContent = Get-Content -LiteralPath $envPath -Raw
    foreach ($platform in $PlatformEnvMarkers.Keys) {
        $pattern = "(?m)^\s*" + [regex]::Escape($PlatformEnvMarkers[$platform]) + "\s*=\s*\S+"
        if ($envContent -match $pattern) {
            Write-Host "  Detected $platform credentials -- installing 'hermes pm install --extra $platform'..."
            & $HermesExe pm install --extra $platform
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "  'hermes pm install --extra $platform' failed (exit $LASTEXITCODE) -- 'hermes gateway run' may prompt for it."
            }
        }
    }
}
else {
    Write-Host "  No .env restored; skipping."
}

# ---------------------------------------------------------------------------
# 5. Actually start the gateway now.
#
#    The restored Startup-folder shortcut only fires on the NEXT Windows
#    logon -- a restore that leaves the gateway stopped until reboot is not
#    "back to how it was" for a daily reinstall workflow. Launch it the same
#    way the shortcut does: run gateway-service\Hermes_Gateway.vbs detached
#    via wscript.exe (confirmed 2026-09-27 -- this is the actual, working
#    launch path for this profile's manual/non-service gateway). Then poll
#    `hermes gateway status` rather than assume success from a bare launch.
# ---------------------------------------------------------------------------

Write-Host "[5/5] Starting the gateway..."

$gatewayVbs = Join-Path $HermesHome "gateway-service\Hermes_Gateway.vbs"
if (-not (Test-Path -LiteralPath $gatewayVbs)) {
    Write-Warning "  $gatewayVbs not found -- nothing to launch. Start it manually (e.g. 'hermes gateway run')."
}
else {
    Start-Process wscript.exe -ArgumentList $gatewayVbs

    $deadline = (Get-Date).AddSeconds(30)
    $running = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        $statusText = (& $HermesExe gateway status 2>&1 | Out-String)
        if ($statusText -match 'Gateway process running') {
            $running = $true
            break
        }
    }
    if ($running) {
        Write-Host "  Gateway is running." -ForegroundColor Green
    }
    else {
        Write-Warning "  Gateway did not report running within 30s. Check manually: hermes gateway status"
    }
}

Write-Host ""
Write-Host "Restore complete." -ForegroundColor Green
Write-Host ""
Write-Host "Credentials were restored (auth.json/.env) -- no re-auth should be needed." -ForegroundColor Green
Write-Host "If you use a Windows-service-managed gateway (not this profile's current" `
    "Startup-shortcut autostart), re-run 'hermes gateway install' -- the actual" `
    "service registration lives in the Windows registry, not in copied files." -ForegroundColor Yellow
Write-Host ""
