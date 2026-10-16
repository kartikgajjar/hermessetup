#requires -Version 5.1
<#
.SYNOPSIS
    Polls an Outlook mailbox with `olk mail delta` and runs a handler script when new mail arrives
    (optionally only from matching senders / subjects). Read-only: never marks, moves or deletes mail.

.DESCRIPTION
    olk has no push/watch mode (Microsoft Graph webhooks need a public HTTPS endpoint), so this uses
    Graph's incremental sync via `olk mail delta`: each call returns what changed since an opaque
    token, plus a new token. State lives in $StateFile (outside git).

    Behaviour confirmed against olk fbb3111 (2026-10-16):
      - A first call with no token pages through the ENTIRE inbox history (deltaComplete=false until
        caught up). The first run therefore only baselines: it pages to completion, stores the token
        and emits nothing.
      - Delta items carry id/subject/createdDateTime but an EMPTY `from` and `receivedDateTime`
        (`mail list` fills them). Sender filtering therefore re-fetches the candidate IDs with
        `olk mail batch --id ...` (max 20 per call).
      - Delta also reports read-flag changes and removals. "New" here means: not removed, not seen
        before, and createdDateTime later than the newest message already handled.
      - `--account` is always passed: the default olk account is a different mailbox.
    The token is saved only after a run's pages are processed, so a crash re-delivers rather than drops.

    The handler (-OnNewMail) is any script; it is called as `-File <script> -EventFile <json>` where the
    JSON holds account, receivedAt and a `messages` array (id, from, subject, receivedDateTime).
    With no -OnNewMail the script only logs (dry run).

    Native calls are wrapped in try/catch: under $ErrorActionPreference = "Stop" a failing external
    command can throw instead of just setting $LASTEXITCODE.

.PARAMETER Account
    Mailbox to watch (passed to olk --account).

.PARAMETER Folder
    Mail folder (well-known name, ID or path). Default inbox.

.PARAMETER FromPattern
    Regex matched against the sender address; omit to match everything.

.PARAMETER SubjectPattern
    Regex matched against the subject; omit to match everything.

.PARAMETER OnNewMail
    Script to run for each batch of matching new mail. Omit for log-only.

.PARAMETER Reset
    Delete the saved token so the next run re-baselines (no events emitted for existing mail).

.PARAMETER Install
    Register the "Hermes Mail Watcher" scheduled task (every -IntervalMinutes, hidden) and run once.
    The filter/handler parameters given with -Install are baked into the task.

.PARAMETER Uninstall
    Remove the scheduled task and state.
#>
[CmdletBinding(DefaultParameterSetName = "Run")]
param(
    [string]$Account = "ekartka@hotmail.com",
    [string]$Folder = "inbox",
    [string]$FromPattern,
    [string]$SubjectPattern,
    [string]$OnNewMail,
    [Parameter(ParameterSetName = "Run")][switch]$Reset,
    [Parameter(ParameterSetName = "Install")][switch]$Install,
    [Parameter(ParameterSetName = "Install")][ValidateRange(1, 60)][int]$IntervalMinutes = 2,
    [Parameter(ParameterSetName = "Uninstall")][switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$TaskName  = "Hermes Mail Watcher"
$StateDir  = Join-Path $env:LOCALAPPDATA "hermes-mail-watcher"
$StateFile = Join-Path $StateDir "state-$($Account -replace '[^\w.@-]', '_').json"
$LogFile   = Join-Path $StateDir "watcher.log"
$MaxPages  = 500

function Write-WatchLog([string]$Message) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Message
    Write-Host $line
    try {
        if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }
        Add-Content -Path $LogFile -Value $line
        $lines = Get-Content $LogFile
        if ($lines.Count -gt 1200) { $lines | Select-Object -Last 1000 | Set-Content $LogFile }
    } catch { }
}

# Runs olk and returns the parsed JSON; throws with olk's output on a non-zero exit.
function Invoke-Olk([string[]]$OlkArgs) {
    $text = $null
    try {
        $text = & olk --account $Account --json @OlkArgs 2>&1 | Out-String
    } catch {
        throw "olk $($OlkArgs -join ' ') failed: $($_.Exception.Message)"
    }
    if ($LASTEXITCODE -ne 0) { throw "olk $($OlkArgs -join ' ') exited $LASTEXITCODE`: $text" }
    return ($text | ConvertFrom-Json)
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
    $shell = (Get-Process -Id $PID).Path
    $argLine = "--headless `"$shell`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Account `"$Account`" -Folder `"$Folder`""
    foreach ($p in @("FromPattern", "SubjectPattern", "OnNewMail")) {
        $v = Get-Variable $p -ValueOnly
        if ($v) { $argLine += " -$p `"$($v -replace '"', '\"')`"" }
    }
    $action   = New-ScheduledTaskAction -Execute "conhost.exe" -Argument $argLine
    $trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
    $logon    = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger, $logon) `
        -Settings $settings -Principal $principal -Force `
        -Description "Polls $Account via olk mail delta and runs a handler on new mail. Source: $PSCommandPath" | Out-Null
    Write-Host "Registered scheduled task '$TaskName' (every $IntervalMinutes min + at logon). Log: $LogFile"
    # Fall through to one run (this is the baseline run on first install).
}

if (-not (Get-Command olk -ErrorAction SilentlyContinue)) { Write-WatchLog "olk not found on PATH"; return }

if ($Reset -and (Test-Path $StateFile)) {
    Remove-Item $StateFile
    Write-WatchLog "State reset for $Account; next run re-baselines."
    return
}

$state = $null
if (Test-Path $StateFile) {
    try { $state = Get-Content $StateFile -Raw | ConvertFrom-Json } catch { $state = $null }
}
$baselining = (-not $state) -or (-not $state.token)

# --- Page the delta feed until caught up --------------------------------------
$token = if ($baselining) { $null } else { $state.token }
$changed = New-Object System.Collections.Generic.List[object]
try {
    for ($page = 0; $page -lt $MaxPages; $page++) {
        $olkArgs = @("mail", "delta", "-f", $Folder, "-n", "50")
        if ($token) { $olkArgs += @("--token", $token) }
        $r = Invoke-Olk $olkArgs
        foreach ($m in @($r.results)) { if ($m) { $changed.Add($m) } }
        if ($r.deltaToken) { $token = $r.deltaToken }
        if ($r.deltaComplete) { break }
        if ($page -eq $MaxPages - 1) { throw "delta not caught up after $MaxPages pages; keeping old token" }
    }
} catch {
    Write-WatchLog "Delta failed: $($_.Exception.Message)"
    exit 1
}

if (-not $token) { Write-WatchLog "No delta token returned; nothing saved."; exit 1 }
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }

# Newest creation time among non-removed items, as UTC.
function Get-Created($m) { if ($m.createdDateTime) { [datetime]::Parse($m.createdDateTime, $null, "RoundtripKind").ToUniversalTime() } else { [datetime]::MinValue } }
$live = @($changed | Where-Object { -not $_.removed })

if ($baselining) {
    # Baseline marker is "now", not the newest createdDateTime: the inbox holds future-dated messages
    # (sender clock skew; seen 2026-10-17 in a 2026-10-16 inbox) which would hide every real new mail.
    $newest = [datetime]::UtcNow
    [ordered]@{ token = $token; lastCreated = $newest.ToString("o"); seen = @() } | ConvertTo-Json | Set-Content $StateFile
    Write-WatchLog "Baselined $Account ($($live.Count) existing messages). No events emitted."
    return
}

# --- Select genuinely new mail --------------------------------------------------
$lastCreated = [datetime]::Parse($state.lastCreated, $null, "RoundtripKind").ToUniversalTime()
$seen = @($state.seen)
$new = @($live | Where-Object { ($_.id -notin $seen) -and ((Get-Created $_) -gt $lastCreated) })

$matched = @()
if ($new.Count -gt 0) {
    # Delta omits sender/receivedDateTime; fetch them for the candidates (<= 20 per batch call).
    $full = @()
    try {
        for ($i = 0; $i -lt $new.Count; $i += 20) {
            $ids = @($new[$i..([Math]::Min($i + 19, $new.Count - 1))] | ForEach-Object { $_.id })
            $olkArgs = @("mail", "batch", "--concise")
            foreach ($id in $ids) { $olkArgs += @("--id", $id) }
            $b = Invoke-Olk $olkArgs
            $full += @($b.results)
        }
    } catch {
        Write-WatchLog "Batch fetch failed (token NOT advanced, will retry): $($_.Exception.Message)"
        exit 1
    }
    $matched = @($full | Where-Object {
        $_ -and
        ((-not $FromPattern)    -or ($_.from    -match $FromPattern)) -and
        ((-not $SubjectPattern) -or ($_.subject -match $SubjectPattern))
    })
    foreach ($m in $full) { if ($m) { Write-WatchLog ("new: {0} | {1}{2}" -f $m.from, $m.subject, $(if ($m -in $matched) { "  [match]" } else { "" })) } }
}

if ($matched.Count -gt 0 -and $OnNewMail) {
    $eventFile = Join-Path $StateDir ("event-{0:yyyyMMdd-HHmmss}.json" -f (Get-Date))
    [ordered]@{
        account = $Account
        receivedAt = (Get-Date).ToUniversalTime().ToString("o")
        messages = @($matched | ForEach-Object { [ordered]@{ id = $_.id; from = $_.from; subject = $_.subject; receivedDateTime = $_.receivedDateTime } })
    } | ConvertTo-Json -Depth 5 | Set-Content $eventFile
    try {
        & $OnNewMail -EventFile $eventFile
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { Write-WatchLog "Handler exited $LASTEXITCODE" }
    } catch {
        Write-WatchLog "Handler failed: $($_.Exception.Message)"
    }
} elseif ($matched.Count -gt 0) {
    Write-WatchLog "$($matched.Count) matching message(s); no -OnNewMail set (dry run)."
}

# --- Persist: token advances only after processing ------------------------------
$newest = if ($new.Count) { ($new | ForEach-Object { Get-Created $_ } | Measure-Object -Maximum).Maximum } else { $lastCreated }
if ($newest -lt $lastCreated) { $newest = $lastCreated }
if ($newest -gt [datetime]::UtcNow) { $newest = [datetime]::UtcNow }   # never let a future-dated message become the marker
$seenOut = @($seen + @($new | ForEach-Object { $_.id })) | Select-Object -Last 500
[ordered]@{ token = $token; lastCreated = $newest.ToString("o"); seen = @($seenOut) } | ConvertTo-Json | Set-Content $StateFile
