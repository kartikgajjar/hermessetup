#requires -Version 5.1
<#
.SYNOPSIS
    Exports Hermes's persistent configuration/state (config, credentials,
    identity, memory, skills, sessions, databases, cron jobs, platform
    pairings, plugins, gateway service files, autostart shortcut) to a
    OneDrive-synced folder, for a daily cleanup + fresh-install cycle where
    NOTHING the user configured is expected to be lost.

.DESCRIPTION
    %LOCALAPPDATA%\hermes mixes small user state with large code/runtime
    (tools\ ~1.6GB downloaded tool binaries, installs\ ~650MB staged venvs,
    node\ ~95MB portable Node.js, hermes-agent\ ~3GB git checkout, cache\
    ~500MB). Pointing HERMES_HOME itself at OneDrive would sync all of that
    too. This script instead copies an explicit allow-list of small state
    paths out to $Destination and leaves everything else alone.

    Credentials (auth.json, .env -- provider API keys / OAuth tokens) ARE
    included as of 2026-09-27, by explicit decision: this backup exists to
    support reinstalling Hermes daily with zero reconfiguration, and
    re-authenticating providers every day defeats that. This means secrets
    land in $Destination in plaintext, which is a OneDrive-synced folder --
    accept that tradeoff deliberately, don't carry it forward by accident.

    Never backed up, on purpose -- these are runtime/ephemeral identity
    files, not configuration, and restoring stale copies of them onto a
    fresh install actively recreates the exact "stale gateway metadata"
    class of bug 05-update-hermes.ps1 exists to work around:
        gateway.pid, gateway.lock, gateway_state.json, gateway-starts.log,
        spawn-ledger.json, .update_exit_code, *.lock, *-shm, *-wal (except
        immediately after the databases below, which DO need their -shm/-wal
        siblings copied alongside the main file)

    Also never backed up -- large, regenerable code/runtime/cache, not
    state: tools\, installs\, node\, hermes-agent\, cache\, bin\, desktop\,
    logs\, backups\ (Hermes's own internal backup dir), source-checks\.

    Currently excluded and NOT yet handled (flagged here so the gap is
    visible instead of silent): attachments\, images\, image_cache\,
    audio_cache\ -- conversation media, empty on this machine as of
    2026-09-27. Add them to $StateDirs below if/when they start holding
    content you care about.

    Hermes's local databases (state.db, kanban.db, projects.db,
    shared-state.db) run in SQLite WAL mode -- a cloud sync client grabbing a
    mid-write snapshot of the main file plus its -wal/-shm siblings can
    corrupt them. This script stops Hermes first so SQLite checkpoints
    cleanly on the last connection closing, then copies.

.PARAMETER Destination
    Where the persistent-state export goes. Defaults to
    C:\Users\<you>\OneDrive\Documents\Hermes.
#>
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE "OneDrive\Documents\Hermes")
)

$ErrorActionPreference = "Stop"

$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$StartupVbs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Hermes_Gateway.vbs"

Write-Host ""
Write-Host "Hermes state backup" -ForegroundColor Cyan
Write-Host "===================="
Write-Host "Source (Hermes home): $HermesHome"
Write-Host "Destination          : $Destination"
Write-Host ""

if (-not (Test-Path $HermesHome)) {
    throw "Hermes home not found at $HermesHome -- nothing to back up."
}

# ---------------------------------------------------------------------------
# Explicit allow-list: only these paths, relative to $HermesHome, are ever
# copied. See the exclusion lists in the header comment for everything else
# and why it's left out.
# ---------------------------------------------------------------------------

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
# 1. Stop Hermes so SQLite checkpoints cleanly (last connection close
#    triggers a WAL checkpoint) before we copy the .db/-wal/-shm files.
# ---------------------------------------------------------------------------

Write-Host "[1/3] Stopping Hermes so state is quiescent before copying..."

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
# the name match above never catches it, and `hermes gateway stop`/`gateway status` are
# currently blind to it too (a known cmdline-matching gap for this bootstrap-launcher shape).
# gateway.lock's own JSON is the one place that reliably names the real PID; read it fresh
# and stop that process directly rather than trusting a name-based heuristic that's a no-op
# here. This bypasses Hermes's own graceful shutdown, which is why we read-then-immediately-act
# instead of trusting an old file.
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
# 2. Copy the allow-listed state into $Destination, mirroring relative paths
#    so restore is a straight reverse copy.
# ---------------------------------------------------------------------------

Write-Host "[2/3] Copying persistent state..."

New-Item -ItemType Directory -Path $Destination -Force | Out-Null

$copied = New-Object System.Collections.Generic.List[string]

foreach ($name in $StateFiles) {
    $src = Join-Path $HermesHome $name
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $Destination $name) -Force
        $copied.Add($name)
    }
}

foreach ($db in $StateDatabases) {
    foreach ($suffix in @("", "-wal", "-shm")) {
        $fileName = "$db$suffix"
        $src = Join-Path $HermesHome $fileName
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $Destination $fileName) -Force
            $copied.Add($fileName)
        }
    }
}

foreach ($dir in $StateDirs) {
    $src = Join-Path $HermesHome $dir
    if (Test-Path -LiteralPath $src) {
        $dst = Join-Path $Destination $dir
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
        Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
        $copied.Add("$dir\")
    }
}

if (Test-Path -LiteralPath $StartupVbs) {
    Copy-Item -LiteralPath $StartupVbs -Destination (Join-Path $Destination "Hermes_Gateway.vbs") -Force
    $copied.Add("Hermes_Gateway.vbs (Startup autostart shortcut)")
}

foreach ($item in $copied) { Write-Host "  $item" }

# ---------------------------------------------------------------------------
# 3. Report
# ---------------------------------------------------------------------------

Write-Host "[3/3] Done."
$totalSize = (Get-ChildItem -Path $Destination -Recurse -File -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum
$totalMB = [Math]::Round(($totalSize / 1MB), 2)

Write-Host ""
Write-Host "Backed up $($copied.Count) item(s), $totalMB MB, to:" -ForegroundColor Green
Write-Host "  $Destination"
Write-Host ""
Write-Host "Included: config, credentials (auth.json/.env), identity, memories," `
    "skills, sessions, databases, cron, pairing, platforms, plugins," `
    "gateway-service files, kanban, and the Startup autostart shortcut." -ForegroundColor Green
Write-Host ""
Write-Host "NOT included (by design -- large code/runtime/cache, not state):" -ForegroundColor Yellow
Write-Host "  tools\, installs\, node\, hermes-agent\, cache\, bin\, logs\, backups\, source-checks\"
Write-Host "NOT included (runtime/ephemeral identity files -- restoring stale copies would be harmful):" -ForegroundColor Yellow
Write-Host "  gateway.pid, gateway.lock, gateway_state.json, gateway-starts.log, spawn-ledger.json"
Write-Host "NOT included (currently empty on this machine -- add to `$StateDirs if this changes):" -ForegroundColor Yellow
Write-Host "  attachments\, images\, image_cache\, audio_cache\"
Write-Host ""
Write-Host "Hermes was stopped for this backup. Start it again normally (e.g. 'hermes chat')."
Write-Host ""
