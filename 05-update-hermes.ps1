#requires -Version 5.1
<#
.SYNOPSIS
    Runs `hermes update` with a bounded retry around a known, confirmed
    Windows race in Hermes's own gateway-discovery pre-flight, instead of
    letting a single transient failure abort the whole update.

.DESCRIPTION
    This wraps the installed `hermes` CLI as a black box. It does not patch
    Hermes's own Python source -- traced instead (2026-09-27, against the
    real checkout at $HermesHome\hermes-agent) to confirm root cause and
    scope this workaround correctly:

      hermes_cli\update_cmd_windows.py : _discover_windows_gateways()
        calls find_profile_gateway_processes(strict=True)  [~line 838-849]

      hermes_cli\gateway.py : find_profile_gateway_processes(strict=True)
        calls get_running_pid_identity_strict(profile.path / "gateway.pid")
        and re-raises ANY exception from it as
        RuntimeError(f"Could not inspect gateway PID for profile {name}")
        [~line 776-793]

      gateway\status.py : get_running_pid_identity_strict()  [~line 2080]
        calls _is_gateway_runtime_lock_active_strict(lock_path)  [~line 1141]
        which opens gateway.lock in "r+" mode to probe it. ANY OSError on
        that open (a transient Windows sharing violation -- AV/indexer scan,
        a handle not yet released by a just-exited gateway, etc.) is raised
        as RuntimeError("gateway runtime lock probe failed: ...") instead of
        being retried.

    That RuntimeError propagates out of _discover_windows_gateways()'s
    `_abort_on_error("Could not map Windows gateway PIDs to profiles")`
    context, aborting `hermes update` before it mutates anything -- which is
    exactly the error text this script exists to work around. It is a
    narrow, almost certainly transient race (a brief exclusive-open
    collision on gateway.lock), so a short bounded retry of `hermes update`
    itself is a reasonable external mitigation without touching Hermes's
    source, which the next `hermes update` would overwrite anyway.

    Note: `hermes gateway status` does NOT hit this same strict code path
    (hermes_cli\gateway_windows.py confirms it uses the lenient
    find_gateway_pids(), which swallows errors) -- so this failure can't be
    predicted ahead of time by checking `gateway status` first, only caught
    when `hermes update` itself hits it. This script does not attempt to
    pre-emptively inspect or move any of Hermes's runtime metadata files
    (gateway.pid / gateway.lock / gateway_state.json under $HermesHome) --
    doing so blind, without reproducing Hermes's own locking semantics,
    would be more likely to cause corruption than to help. If retries alone
    don't clear it, the real fix belongs upstream in
    _is_gateway_runtime_lock_active_strict.

    This script never calls Stop-Process and never deletes or moves any
    Hermes file. It only runs `hermes update` (and `hermes gateway status`
    for a post-update health check), and interprets their output.

.PARAMETER Plan
    Read-only: runs `hermes update --plan` once, no retries, no mutation.

.PARAMETER NoGatewayRestart
    Forwarded to `hermes update` as --no-gateway-restart. Also skips the
    post-update gateway health poll, since no restart is expected.

.PARAMETER Force
    Skip the interactive confirmation before running `hermes update`.

.PARAMETER MaxAttempts
    How many times to run `hermes update` when it fails specifically with
    the known gateway-discovery race error text. Default 3. A failure with
    any other error text is never retried.

.PARAMETER RetryDelaySeconds
    Delay between retry attempts. Default 5.

.PARAMETER HealthCheckTimeoutSeconds
    How long to poll `hermes gateway status` after a successful update
    before warning (not failing) that the gateway isn't confirmed running
    yet. Default 120.

.PARAMETER HealthCheckIntervalSeconds
    Delay between post-update health poll attempts. Default 3.

.PARAMETER ExtraArgs
    Additional arguments passed through to `hermes update` verbatim.
#>
[CmdletBinding()]
param(
    [switch]$Plan,
    [switch]$NoGatewayRestart,
    [switch]$Force,
    [int]$MaxAttempts = 3,
    [int]$RetryDelaySeconds = 5,
    [int]$HealthCheckTimeoutSeconds = 120,
    [int]$HealthCheckIntervalSeconds = 3,
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"

$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"

# ---------------------------------------------------------------------------
# Resolve the hermes launcher. The installer places a .cmd shim at
# bin\hermes.cmd (confirmed 2026-09-27 via `Get-Command hermes`) -- there is
# no bin\hermes.exe. Prefer whatever's actually on PATH; fall back to the
# known install layout if PATH resolution fails for some reason (e.g. a
# non-interactive shell that hasn't picked up a PATH change yet).
# ---------------------------------------------------------------------------

$HermesCmd = (Get-Command hermes -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $HermesCmd) {
    foreach ($candidate in @("bin\hermes.cmd", "bin\hermes.exe")) {
        $p = Join-Path $HermesHome $candidate
        if (Test-Path -LiteralPath $p) { $HermesCmd = $p; break }
    }
}
if (-not $HermesCmd) {
    throw "Could not find the 'hermes' launcher on PATH or under $HermesHome\bin. Run 02-install-hermes-secure.ps1 first."
}

# Exact error text confirmed against gateway.py/update_cmd_windows.py/status.py
# (see header) -- only these are treated as the known, retryable race.
$RetryableErrorPatterns = @(
    'Could not map Windows gateway PIDs to profiles',
    'Could not inspect gateway PID for profile',
    'gateway runtime lock probe failed',
    'runtime metadata does not identify a live gateway'
)

Write-Host ""
Write-Host "Hermes update" -ForegroundColor Cyan
Write-Host "============="
Write-Host "Hermes launcher: $HermesCmd"
Write-Host ""

# ---------------------------------------------------------------------------
# hermes gateway status -- always exits 0 (confirmed 2026-09-27); the only
# signal is the printed text (hermes_cli\gateway_windows.py:1575):
#   "(pass:[✓]) Gateway process running (PID: ...)"  or
#   "(pass:[✗]) No gateway process detected"
# ---------------------------------------------------------------------------

function Test-HermesGatewayRunning {
    $output = & $HermesCmd gateway status 2>&1
    $text = ($output | Out-String)
    [pscustomobject]@{
        Running = ($text -match 'Gateway process running')
        Output  = $text.Trim()
    }
}

# Fallback cross-check. Confirmed 2026-09-27 on this machine: `hermes gateway
# status` (find_gateway_pids(), lenient) reported "No gateway process
# detected" for a gateway that WAS actually running -- independently verified
# via `hermes update --plan` (which lists it under "Running services to
# restart" with a real PID) and Get-Process. The two discovery paths inside
# Hermes disagree with each other here, so trust `gateway status` alone can
# produce a false "unhealthy" report after a perfectly good restart. This is
# read-only and only used as a second opinion when `gateway status` says "not
# running".
function Test-HermesGatewayRunningViaPlan {
    $output = & $HermesCmd update --plan 2>&1
    $text = ($output | Out-String)
    $match = [regex]::Match($text, 'gateway \[[^\]]*\]\s+pid\s+(\d+)')
    [pscustomobject]@{
        Running = $match.Success
        ProcessId = if ($match.Success) { [int]$match.Groups[1].Value } else { $null }
        Output  = $text.Trim()
    }
}

if ($Plan) {
    Write-Host "Running 'hermes update --plan' (read-only, no retries)..."
    & $HermesCmd update --plan @ExtraArgs
    exit $LASTEXITCODE
}

Write-Host "Ready to run 'hermes update'."
if (-not $Force) {
    $answer = Read-Host "Proceed with 'hermes update'? (y/N)"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Cancelled. Nothing was changed."
        exit 1
    }
}

$updateArgs = @()
if ($NoGatewayRestart) { $updateArgs += "--no-gateway-restart" }
$updateArgs += $ExtraArgs

# ---------------------------------------------------------------------------
# Run 'hermes update', retrying only on the confirmed transient
# gateway-discovery race. Any other failure is reported and NOT retried.
# ---------------------------------------------------------------------------

$updateExitCode = 1
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    Write-Host "Running 'hermes update' (attempt $attempt/$MaxAttempts)..."

    $output = & $HermesCmd update @updateArgs 2>&1
    $updateExitCode = $LASTEXITCODE
    $text = ($output | Out-String)
    $output | ForEach-Object { Write-Host $_ }

    if ($updateExitCode -eq 0) {
        break
    }

    $isRetryable = $false
    foreach ($pattern in $RetryableErrorPatterns) {
        if ($text -match [regex]::Escape($pattern)) { $isRetryable = $true; break }
    }

    if (-not $isRetryable) {
        Write-Host ""
        Write-Warning "hermes update exited with code $updateExitCode (not the known gateway-discovery race -- not retrying)."
        Write-Host "No further automatic action taken. Investigate before re-running." -ForegroundColor Yellow
        exit $updateExitCode
    }

    if ($attempt -eq $MaxAttempts) {
        Write-Host ""
        Write-Warning "hermes update kept hitting the known gateway-discovery race after $MaxAttempts attempt(s)."
        Write-Host "This is a transient Windows sharing-violation race probing gateway.lock" `
            "(gateway\status.py: _is_gateway_runtime_lock_active_strict), not something this" `
            "script can safely repair by touching Hermes's runtime files. No processes were" `
            "stopped and nothing was changed." -ForegroundColor Yellow
        Write-Host "Try again shortly, or file this against hermes-agent citing:"
        Write-Host "  gateway/status.py: _is_gateway_runtime_lock_active_strict (~line 1141)"
        Write-Host "  hermes_cli/gateway.py: find_profile_gateway_processes (~line 776)"
        exit $updateExitCode
    }

    Write-Host ""
    Write-Host "Hit the known gateway-discovery race (exit $updateExitCode). Retrying in ${RetryDelaySeconds}s..." -ForegroundColor Yellow
    Start-Sleep -Seconds $RetryDelaySeconds
}

Write-Host "hermes update completed (exit 0)."

# ---------------------------------------------------------------------------
# Post-update gateway health verification -- a successful update exit code
# is not the same as a healthy, running gateway. Poll with a bounded number
# of attempts rather than a single sleep-then-check.
# ---------------------------------------------------------------------------

if ($NoGatewayRestart) {
    Write-Host "--NoGatewayRestart was set; skipping post-update gateway health check."
}
else {
    Write-Host "Verifying gateway health after update..."

    $deadline = (Get-Date).AddSeconds($HealthCheckTimeoutSeconds)
    $healthy = $false
    $pollAttempt = 0

    while ((Get-Date) -lt $deadline) {
        $pollAttempt++
        $status = Test-HermesGatewayRunning
        if ($status.Running) {
            $healthy = $true
            break
        }
        Write-Host "  Attempt $pollAttempt : not yet running, retrying in ${HealthCheckIntervalSeconds}s..."
        Start-Sleep -Seconds $HealthCheckIntervalSeconds
    }

    if ($healthy) {
        Write-Host "Gateway confirmed running after $pollAttempt attempt(s)." -ForegroundColor Green
    }
    else {
        Write-Host "  'hermes gateway status' didn't report running; cross-checking via 'hermes update --plan'..."
        $planCheck = Test-HermesGatewayRunningViaPlan
        if ($planCheck.Running) {
            Write-Host "Gateway confirmed running (PID $($planCheck.ProcessId)) via 'hermes update --plan' discovery," `
                "even though 'hermes gateway status' reported not running -- known discrepancy between Hermes's" `
                "two internal discovery paths on this install (see script header)." -ForegroundColor Green
        }
        else {
            Write-Warning "Gateway did not report running within ${HealthCheckTimeoutSeconds}s after update (checked both 'gateway status' and 'update --plan')."
            Write-Host "Update itself succeeded (exit 0); this only means post-update verification timed out." -ForegroundColor Yellow
            Write-Host "Check manually: hermes gateway status"
        }
    }
}

Write-Host ""
Write-Host "Hermes update finished." -ForegroundColor Green
Write-Host ""
