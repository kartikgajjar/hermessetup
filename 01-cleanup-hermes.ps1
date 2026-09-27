#requires -Version 5.1
<#
.SYNOPSIS
    Fully removes Hermes Agent state (config, auth, sessions, memory, skills,
    logs, credentials) and its Docker/process footprint from this machine.

.DESCRIPTION
    Destructive. Deletes %LOCALAPPDATA%\hermes and the legacy %USERPROFILE%\.hermes
    directory, stops Hermes processes, removes Hermes-owned Docker containers, and
    clears Hermes-related user-level environment variables.

    Never touches this script's own directory (the installer/config source under
    C:\LocalCode\ai\hermes) -- every recursive delete target is checked against an
    explicit allow-list before anything is removed.

.PARAMETER Force
    Skip the interactive confirmation prompt.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$LegacyHome = Join-Path $env:USERPROFILE ".hermes"

$EnvVarsToClear = @(
    "HERMES_HOME",
    "HERMES_GIT_BASH_PATH",
    "TERMINAL_ENV",
    "TERMINAL_CWD",
    "TERMINAL_DOCKER_IMAGE",
    "TERMINAL_DOCKER_FORWARD_ENV",
    "TERMINAL_DOCKER_VOLUMES",
    "TERMINAL_DOCKER_ENV",
    "TERMINAL_DOCKER_EXTRA_ARGS",
    "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
    "TERMINAL_CONTAINER_PERSISTENT"
)

Write-Host ""
Write-Host "Hermes cleanup" -ForegroundColor Cyan
Write-Host "=============="
Write-Host "Primary Hermes home : $HermesHome"
Write-Host "Legacy Hermes home  : $LegacyHome"
Write-Host "Script directory (never touched): $PSScriptRoot"
Write-Host ""

if (-not $Force) {
    Write-Warning "This permanently deletes Hermes auth, config, sessions, memory, skills, logs, credentials, and all other local state. This cannot be undone."
    $answer = Read-Host "Type YES to continue"
    if ($answer -cne "YES") {
        Write-Host "Cancelled. Nothing was changed."
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Defensive recursive-delete helper
#
# Every deletion target must pass ALL of:
#   1. non-empty, resolvable path
#   2. not a drive root
#   3. not suspiciously short
#   4. not equal to, and does not contain, the script's own directory
#   5. present on an explicit per-run allow-list (belt-and-braces on top of 1-4)
# ---------------------------------------------------------------------------

$ScriptRootFull = [System.IO.Path]::GetFullPath($PSScriptRoot)

function Assert-SafeDeleteTarget {
    param(
        [Parameter(Mandatory)][string]$FullPath,
        [Parameter(Mandatory)][string[]]$AllowList
    )

    $root = [System.IO.Path]::GetPathRoot($FullPath)
    if ($FullPath.TrimEnd('\') -ieq $root.TrimEnd('\')) {
        throw "Refusing to delete a drive root: $FullPath"
    }
    if ($FullPath.Length -lt 10) {
        throw "Refusing to delete a suspiciously short path: $FullPath"
    }
    if ($FullPath -ieq $ScriptRootFull -or
        $ScriptRootFull.StartsWith("$FullPath\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete '$FullPath' -- it is, or is an ancestor of, the script directory '$ScriptRootFull'."
    }
    if (-not ($AllowList | Where-Object { $_ -ieq $FullPath })) {
        throw "Refusing to delete '$FullPath' -- not on this run's explicit allow-list."
    }
}

function Get-HermesLockerInfo {
    # Best-effort: name the process actually holding $Path open, via
    # Sysinternals `handle` if it's available. Restart Manager (rstrtmgr.dll)
    # was tried for this first and rejected -- it returned ERROR_ACCESS_DENIED
    # even from an elevated session on this machine (2026-09-20), so it is
    # not a reliable fallback here.
    param([Parameter(Mandatory)][string]$Path)

    $handleExe = Get-Command handle64.exe, handle.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if (-not $handleExe) {
        $wingetHandle = Join-Path $env:LOCALAPPDATA `
            "Microsoft\WinGet\Packages\Microsoft.Sysinternals.Handle_Microsoft.Winget.Source_8wekyb3d8bbwe\handle64.exe"
        if (Test-Path $wingetHandle) { $handleExe = $wingetHandle }
    }

    if (-not $handleExe) {
        return "Tip: install Sysinternals Handle ('winget install --id Microsoft.Sysinternals.Handle -e')" `
            + " then run:`n  handle64.exe -accepteula '$Path'`nto name the exact process/PID holding it open."
    }

    try {
        $output = & $handleExe -accepteula -nobanner $Path 2>$null
        if ($output) {
            return "Detected via Sysinternals handle:`n$($output -join "`n")"
        }
        return "Sysinternals handle found no open handles on '$Path' right now -- the lock may have been" `
            + " transient (antivirus/indexer scan); re-running this script often resolves it."
    }
    catch {
        return "Attempted to run Sysinternals handle but it failed: $($_.Exception.Message)"
    }
}

function Remove-HermesPathSafely {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string[]]$AllowList
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        Write-Warning "$Label : empty path, skipping."
        return
    }

    $full = [System.IO.Path]::GetFullPath($Path)
    Assert-SafeDeleteTarget -FullPath $full -AllowList $AllowList

    if (-not (Test-Path -LiteralPath $full)) {
        Write-Host "$Label not present, nothing to remove: $full"
        return
    }

    # A directory can stay locked even after every file inside it is gone --
    # e.g. a process has its CURRENT WORKING DIRECTORY set inside it. That
    # alone is enough for Windows to deny delete/rename, with no Hermes-named
    # process anywhere in sight (the process-stop step above only matches
    # names 'hermes'/'hermes-agent'). Confirmed 2026-09-20: a VS Code-spawned
    # pwsh.exe integrated terminal held exactly this kind of lock on
    # hermes-agent\ after its contents had already been cleared by a prior
    # partial install/cleanup attempt -- closing that terminal tab released it.
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            Remove-Item -LiteralPath $full -Recurse -Force
            Write-Host "Removed $Label : $full" -ForegroundColor Green
            return
        }
        catch {
            if ($attempt -eq $maxAttempts) {
                $lockerInfo = Get-HermesLockerInfo -Path $full
                throw "Could not remove $Label ('$full') after $maxAttempts attempt(s): $($_.Exception.Message)`n" `
                    + "This is usually a process with its working directory set inside this folder (an open " `
                    + "VS Code integrated terminal, cmd/PowerShell window, or a leftover installer child " `
                    + "process) -- not a Hermes process itself.`n$lockerInfo`n" `
                    + "Close any shells/editors with a window open inside '$full', then re-run this script."
            }
            Write-Warning "  Remove attempt $attempt/$maxAttempts for $Label failed (retrying): $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }
}

$AllowedDeleteTargets = @(
    [System.IO.Path]::GetFullPath($HermesHome),
    [System.IO.Path]::GetFullPath($LegacyHome)
)

# ---------------------------------------------------------------------------
# 1. Stop Hermes-related processes
# ---------------------------------------------------------------------------

Write-Host "`n[1/5] Stopping Hermes processes..."

Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -match '^hermes(-agent)?$' } |
    ForEach-Object {
        try {
            Stop-Process -Id $_.Id -Force -ErrorAction Stop
            Write-Host "  Stopped PID $($_.Id) ($($_.ProcessName))"
        }
        catch {
            Write-Warning "  Could not stop PID $($_.Id) ($($_.ProcessName)): $_"
        }
    }

# ---------------------------------------------------------------------------
# 2. Remove Hermes-owned Docker containers only
#
#    Every container Hermes creates is labeled hermes-agent=1 (see
#    tools/environments/docker.py) and named hermes-<8 hex chars>. Both are
#    used so a container is still caught if it predates the label.
# ---------------------------------------------------------------------------

Write-Host "[2/5] Removing Hermes-owned Docker containers..."

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        docker info *> $null
        $dockerRunning = ($LASTEXITCODE -eq 0)
    }
    catch {
        $dockerRunning = $false
    }

    if (-not $dockerRunning) {
        Write-Warning "  Docker Engine is not running; skipping container cleanup."
    }
    else {
        $targets = New-Object System.Collections.Generic.List[string]

        $byLabel = & docker ps -a --filter "label=hermes-agent=1" --format "{{.ID}}" 2>$null
        if ($byLabel) { $targets.AddRange([string[]]$byLabel) }

        $byName = & docker ps -a --format "{{.ID}} {{.Names}}" 2>$null |
            Where-Object { $_ -match '\bhermes-[0-9a-f]{8}\b' } |
            ForEach-Object { ($_ -split '\s+')[0] }
        if ($byName) { $targets.AddRange([string[]]$byName) }

        $targets = $targets | Where-Object { $_ } | Sort-Object -Unique

        if (-not $targets) {
            Write-Host "  No Hermes-owned containers found."
        }
        foreach ($id in $targets) {
            try {
                docker rm -f $id | Out-Null
                Write-Host "  Removed container $id"
            }
            catch {
                Write-Warning "  Could not remove container $id : $_"
            }
        }
    }
}
else {
    Write-Host "  Docker CLI not found; skipping."
}

# ---------------------------------------------------------------------------
# 3. Run hermes uninstall (best-effort)
#
#    --full removes config/data along with the CLI; --yes skips its own
#    interactive confirmation (this script already got one above).
# ---------------------------------------------------------------------------

Write-Host "[3/5] Running 'hermes uninstall --full --yes'..."

if (Get-Command hermes -ErrorAction SilentlyContinue) {
    try {
        & hermes uninstall --full --yes
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "  hermes uninstall exited with code $LASTEXITCODE; continuing with manual cleanup."
        }
    }
    catch {
        Write-Warning "  hermes uninstall failed ($_); continuing with manual cleanup."
    }
}
else {
    Write-Host "  'hermes' not found on PATH; skipping, manual cleanup will cover it."
}

# ---------------------------------------------------------------------------
# 4. Remove Hermes state directories (guarded)
# ---------------------------------------------------------------------------

Write-Host "[4/5] Removing Hermes state directories..."

Remove-HermesPathSafely -Path $HermesHome -Label "Primary Hermes home" -AllowList $AllowedDeleteTargets
Remove-HermesPathSafely -Path $LegacyHome -Label "Legacy Hermes home"  -AllowList $AllowedDeleteTargets

# ---------------------------------------------------------------------------
# 5. Remove Hermes user-level environment overrides
# ---------------------------------------------------------------------------

Write-Host "[5/5] Removing Hermes user environment overrides..."

foreach ($variable in $EnvVarsToClear) {
    [Environment]::SetEnvironmentVariable($variable, $null, "User")
    Remove-Item "Env:$variable" -ErrorAction SilentlyContinue
}
Write-Host "  Cleared: $($EnvVarsToClear -join ', ')"

Write-Host ""
Write-Host "Hermes cleanup complete." -ForegroundColor Green
Write-Host "Script/installer directory was NOT touched: $PSScriptRoot"
Write-Host ""
