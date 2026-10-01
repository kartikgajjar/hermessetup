#requires -Version 5.1
<#
.SYNOPSIS
    Removes orphaned Hermes Docker sandbox containers that Hermes's own
    reaper leaves running forever, and (with -Install) schedules itself to
    run every 10 minutes so they never accumulate.

.DESCRIPTION
    With terminal.backend: docker, Hermes starts one `sleep infinity`
    container per task (labelled hermes-agent=1). A Hermes process reaps
    its own containers after terminal.lifetime_seconds (300s) of idleness,
    but that reaper lives inside the process: if the gateway/CLI is killed,
    restarted or updated first, the container is orphaned. Hermes's
    startup orphan reaper (reap_orphan_containers in
    tools\environments\docker.py) deliberately only removes *exited*
    containers -- running ones might belong to a sibling Hermes process --
    and `sleep infinity` never exits, so running orphans are never cleaned.
    Six of them sat running for 3 days after the 2026-09-27 gateway churn.

    This script closes that gap:
      - A running/created hermes-agent container whose only processes are
        its init + `sleep infinity` (i.e. no command is executing in it) is
        "idle". Idle-since timestamps are tracked across runs in
        $StateFile; once a container has been idle for -IdleMinutes
        (default 30 = 6x Hermes's own 300s idle reaper, which a live owner
        would already have fired) it is removed.
      - Containers with any other process running are never touched, and
        their idle clock resets.
      - Exited/dead hermes-agent containers older than -IdleMinutes are
        removed too (normally Hermes does this itself on next start).
    A wrong guess is cheap: if a live Hermes task's container is removed,
    Hermes's label probe misses and it starts a fresh one on next use.

    Docker not installed / daemon not running -> exits 0 silently, so the
    scheduled task doesn't error while Docker Desktop is stopped.

.PARAMETER IdleMinutes
    How long a container must be continuously idle before removal.

.PARAMETER Now
    Ignore the idle clock: remove every currently idle hermes-agent
    container immediately (use after a known crash/restart).

.PARAMETER Install
    Register the "Hermes Container Reaper" scheduled task (every 10 min,
    current user, hidden window) and run one sweep.

.PARAMETER Uninstall
    Remove the scheduled task and state file.
#>
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = "Sweep")]
param(
    [Parameter(ParameterSetName = "Sweep")][int]$IdleMinutes = 30,
    [Parameter(ParameterSetName = "Sweep")][switch]$Now,
    [Parameter(ParameterSetName = "Install")][switch]$Install,
    [Parameter(ParameterSetName = "Uninstall")][switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$TaskName  = "Hermes Container Reaper"
$StateDir  = Join-Path $env:LOCALAPPDATA "hermes-container-reaper"
$StateFile = Join-Path $StateDir "idle-since.json"
$LogFile   = Join-Path $StateDir "reaper.log"

function Write-ReaperLog([string]$Message) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Message
    Write-Host $line
    try {
        if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }
        Add-Content -Path $LogFile -Value $line
        # Keep the log bounded: last 500 lines.
        $lines = Get-Content $LogFile
        if ($lines.Count -gt 600) { $lines | Select-Object -Last 500 | Set-Content $LogFile }
    } catch { }
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "Scheduled task '$TaskName' not found."
    }
    if (Test-Path $StateDir) { Remove-Item -Recurse -Force $StateDir }
    return
}

if ($Install) {
    $shell  = (Get-Process -Id $PID).Path   # same PowerShell edition that ran -Install
    $script = $PSCommandPath
    # Launched via `conhost.exe --headless`: pwsh's own -WindowStyle Hidden only hides the console
    # AFTER it has been created, so a 10-minute task flashes a window on screen every run.
    $action = New-ScheduledTaskAction -Execute "conhost.exe" `
        -Argument "--headless `"$shell`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 10)
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger, $logon) `
        -Settings $settings -Principal $principal -Force `
        -Description "Removes orphaned Hermes Docker sandbox containers (hermes-agent=1) idle > 30 min. Source: $script" | Out-Null
    Write-Host "Registered scheduled task '$TaskName' (every 10 min + at logon). Log: $LogFile"
    # Fall through to an immediate sweep.
}

# --- Sweep ------------------------------------------------------------------

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return }
& docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { return }   # daemon not running

$idleSince = @{}
if (Test-Path $StateFile) {
    try {
        (Get-Content $StateFile -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $idleSince[$_.Name] = [datetime]::Parse($_.Value, $null, "RoundtripKind") }
    } catch { $idleSince = @{} }
}

$nowUtc    = [datetime]::UtcNow
$threshold = New-TimeSpan -Minutes $IdleMinutes
$seen      = @{}

$rows = & docker ps -a --filter "label=hermes-agent=1" --format "{{.ID}}`t{{.Names}}`t{{.State}}"
if ($LASTEXITCODE -ne 0) { return }

foreach ($row in $rows) {
    if (-not $row.Trim()) { continue }
    $id, $name, $state = $row.Split("`t")
    $seen[$id] = $true

    if ($state -in @("exited", "dead")) {
        $finished = [datetime]::Parse((& docker inspect --format "{{.State.FinishedAt}}" $id), $null, "RoundtripKind")
        if ($Now -or ($nowUtc - $finished.ToUniversalTime()) -ge $threshold) {
            if ($PSCmdlet.ShouldProcess($name, "docker rm (exited)")) {
                & docker rm $id *> $null
                if ($LASTEXITCODE -eq 0) { Write-ReaperLog "Removed exited orphan $name ($id)" }
            }
        }
        continue
    }

    # running / created / paused: idle = nothing but init + `sleep infinity`.
    $idle = $false
    if ($state -eq "running") {
        # docker top needs the pid column; strip it so only the command line remains.
        $procs = & docker top $id -o pid,args 2>$null | Select-Object -Skip 1 |
            ForEach-Object { $_ -replace '^\s*\d+\s+', '' }
        if ($LASTEXITCODE -eq 0) {
            $busy = $procs | Where-Object {
                $_.Trim() -and $_ -notmatch '^\s*(/sbin/docker-init|/dev/init|docker-init|tini|catatonit|/init|s6-\S+)\b' `
                           -and $_ -notmatch '^\s*sleep infinity\s*$'
            }
            $idle = -not $busy
        }
    } elseif ($state -eq "created") {
        $idle = $true   # never started (failed docker run); nothing can be using it
    }

    if (-not $idle) { $idleSince.Remove($id); continue }
    if (-not $idleSince.ContainsKey($id)) { $idleSince[$id] = $nowUtc }

    $idleFor = $nowUtc - $idleSince[$id]
    if ($Now -or $idleFor -ge $threshold) {
        if ($PSCmdlet.ShouldProcess($name, "docker rm -f (idle $([int]$idleFor.TotalMinutes) min)")) {
            & docker rm -f $id *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-ReaperLog "Removed idle orphan $name ($id), idle >= $([int]$idleFor.TotalMinutes) min"
                $idleSince.Remove($id)
            }
        }
    }
}

# Forget containers that no longer exist; persist the rest.
foreach ($k in @($idleSince.Keys)) { if (-not $seen.ContainsKey($k)) { $idleSince.Remove($k) } }
if (-not $WhatIfPreference) {
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }
    $out = [ordered]@{}
    foreach ($k in $idleSince.Keys) { $out[$k] = $idleSince[$k].ToString("o") }
    ($out | ConvertTo-Json) | Set-Content -Path $StateFile
}
