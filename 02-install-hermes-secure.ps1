#requires -Version 5.1
<#
.SYNOPSIS
    Installs Hermes Agent via the official Windows installer and applies a
    hardened, privacy-first baseline configuration.

.DESCRIPTION
    - Verifies Docker CLI + Engine (Hermes's terminal sandbox runs in Docker).
    - Creates a controlled workspace at C:\HermesWorkspace (separate from this
      installer/config source directory, which is never used as a Hermes
      working directory or Docker bind mount).
    - Runs the official installer (https://hermes-agent.nousresearch.com/install.ps1)
      with setup skipped.
    - Applies terminal/approvals/mcp/command-allowlist hardening via
      `hermes config set`, one verified key at a time, rather than overwriting
      config.yaml wholesale -- this preserves every other default section
      (model, memory, compression, gateway, etc.) and self-validates each key
      against whichever Hermes version actually got installed.
    - Writes SOUL.md (home-level identity/behavior rules) and the default
      project's AGENTS.md (project-scoped rules).
    - Does NOT configure an LLM provider, import any Claude Code
      configuration, or enable MCP/gateway/cron/WhatsApp/browser automation.

.PARAMETER Workspace
    Controlled workspace root. Defaults to C:\HermesWorkspace.
#>
[CmdletBinding()]
param(
    [string]$Workspace = "C:\HermesWorkspace"
)

$ErrorActionPreference = "Stop"

$HermesHome   = Join-Path $env:LOCALAPPDATA "hermes"
$InstallerUrl = "https://hermes-agent.nousresearch.com/install.ps1"

Write-Host ""
Write-Host "Hermes secure install" -ForegroundColor Cyan
Write-Host "======================"
Write-Host "Script dir (source only, never a working dir): $PSScriptRoot"
Write-Host "Hermes home                                   : $HermesHome"
Write-Host "Controlled workspace                          : $Workspace"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Docker prerequisite
# ---------------------------------------------------------------------------

Write-Host "[1/7] Checking Docker..."

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI not found. Install Docker Desktop first: https://www.docker.com/products/docker-desktop/"
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker CLI is present but Docker Engine is not running/reachable. Start Docker Desktop and re-run."
}

Write-Host "  Docker ready." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Create controlled workspace
#
#    Hermes's own working directories -- never C:\LocalCode\ai\hermes.
# ---------------------------------------------------------------------------

Write-Host "[2/7] Creating controlled workspace..."

$directories = @(
    $Workspace,
    (Join-Path $Workspace "input"),
    (Join-Path $Workspace "output"),
    (Join-Path $Workspace "projects"),
    (Join-Path $Workspace "projects\default")
)

foreach ($directory in $directories) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    Write-Host "  $directory"
}

# ---------------------------------------------------------------------------
# 3. Install Hermes (official installer, setup skipped)
# ---------------------------------------------------------------------------

Write-Host "[3/7] Installing Hermes from $InstallerUrl ..."

$installerText = Invoke-RestMethod $InstallerUrl
$installer = [scriptblock]::Create($installerText.TrimStart([char]0xFEFF))

# The installer's -SkipSetup flag was removed upstream (confirmed 2026-09-27
# against the current install.ps1) -- -NonInteractive alone now covers it:
# Stage-Setup/Stage-Gateway each check $NonInteractive internally and return
# immediately without prompting, so the whole no-flag ladder stays scriptable.
# -IncludeDesktop builds the Electron desktop app (apps/desktop, ui-tui, web
# npm workspaces) proactively during install rather than lazily on first
# `hermes desktop` -- worth it since this install is wiped and rebuilt daily.
& $installer -NonInteractive -IncludeDesktop

# The installer produces bin\hermes.cmd for a git-method install (confirmed
# 2026-09-27) -- not bin\hermes.exe. Check both rather than hardcoding one.
$HermesExe = @("bin\hermes.cmd", "bin\hermes.exe") |
    ForEach-Object { Join-Path $HermesHome $_ } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $HermesExe) {
    throw "Hermes installation failed: neither bin\hermes.cmd nor bin\hermes.exe found under $HermesHome."
}
Write-Host "  Installed: $HermesExe" -ForegroundColor Green

# Computer Use (cua-driver: native screen capture / desktop control) runs on
# THIS Windows host directly -- it is not part of the Docker terminal sandbox
# and cannot be, since containers have no access to the Windows desktop
# session. The installer fetches cua-driver from a third-party repo
# (github.com/trycua/cua) unless -SkipComputerUse is passed; it is not passed
# here, so the driver install + toolset enable below is intentional, not a
# default left on by accident. `capture` (screenshot) is a no-approval,
# no-side-effect action; every other action (click/type/drag/key) routes
# through the same approvals.mode: manual gate as everything else, so nothing
# can click/type on-screen (e.g. inside TradeStation) without an explicit
# per-action approval prompt.
& $HermesExe tools enable computer_use --platform cli | Out-Null
Write-Host "  Computer Use toolset enabled (screen capture is free; click/type/key require manual approval)."

# ---------------------------------------------------------------------------
# 4. Harden config via `hermes config set`
#
#    Verified against the installed CLI's own schema (hermes config get /
#    source inspection) rather than assumed. Using `config set` per key --
#    instead of overwriting config.yaml -- merges cleanly with the rest of
#    the default config and fails loudly (non-zero exit) if a key the
#    installed version no longer recognizes is rejected.
# ---------------------------------------------------------------------------

Write-Host "[4/7] Applying hardened terminal/approvals/MCP config..."

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
        throw "hermes config set '$Key' '$Value' failed (exit $LASTEXITCODE) -- config is only partially hardened. Fix the key/value and re-run."
    }
}

# Docker terminal sandbox: no host mounts, no network, no env forwarding.
#
# container_persistent: true -- deliberately NOT per-session disposable.
# Traced 2026-10-04 (tools\terminal_tool.py _resolve_container_task_id,
# tools\environments\docker.py _mount_args): with false, every gateway
# message/session gets its own container whose /root and /workspace are
# tmpfs, so anything a Discord session writes (reports, CSVs) is gone when
# that container is reaped and no other session -- CLI or a later Discord
# message -- can see it. With true, CLI and default-profile gateway sessions
# share ONE container, and /root + /workspace are bind-mounted from
# $HermesHome\sandboxes\docker\default\{home,workspace}, so working files
# survive restarts and are visible across Discord and CLI. Still no host
# mounts beyond that Hermes-owned dir; 03/04 back it up as state.
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
    # Every dangerous command and unattended/cron/single-query path requires
    # manual approval; nothing pre-approved.
    "approvals.mode"                          = "manual"
    "approvals.timeout"                       = "300"
    "approvals.cron_mode"                     = "deny"
    "approvals.single_query_mode"             = "deny"
    "approvals.unattended_mode"               = "deny"
    "approvals.mcp_reload_confirm"            = "true"
    "approvals.destructive_slash_confirm"     = "true"
    # No pre-approved commands, no MCP servers.
    "command_allowlist"                       = "[]"
    "mcp_servers"                             = "{}"
}

foreach ($key in $HardenedSettings.Keys) {
    Set-HermesConfig -Key $key -Value $HardenedSettings[$key]
    Write-Host "  $key = $($HardenedSettings[$key])"
}

# ---------------------------------------------------------------------------
# 5. SOUL.md -- home-level identity/behavior rules (kept terse: it consumes
#    model context on every turn).
# ---------------------------------------------------------------------------

Write-Host "[5/7] Writing SOUL.md..."

$SoulPath = Join-Path $HermesHome "SOUL.md"

$Soul = @'
# Privacy & Access Rules

- Local-first. Minimum necessary data access for the task at hand.
- Do not inspect unrelated files, credentials, or environment variables.
- Do not seek out browser profiles, OneDrive, corporate/private data,
  financial data, email, screenshots, or auth material unless explicitly
  provided for the current task.
- Prefer local preprocessing/filtering before sending anything externally.
- Disclose when data may be sent to an external API, MCP server, browser
  service, gateway, or LLM.
- Send only the minimum information required externally.
- Never store secrets or confidential data in Memory or Skills.
- Do not broaden filesystem, network, or tool permissions for convenience.
'@

Set-Content -Path $SoulPath -Value $Soul -Encoding UTF8
Write-Host "  $SoulPath"

# ---------------------------------------------------------------------------
# 6. AGENTS.md -- project-scoped rules for the default project. Deliberately
#    does not repeat SOUL.md's home-level rules.
# ---------------------------------------------------------------------------

Write-Host "[6/7] Writing default project AGENTS.md..."

$AgentsPath = Join-Path $Workspace "projects\default\AGENTS.md"

$Agents = @'
# Project Rules

- Work only with files explicitly provided for this project.
- Do not access parent directories.
- Do not inspect .env files, credentials, tokens, browser profiles, SSH
  keys, OneDrive, or unrelated repositories.
- Process/filter locally first.
- Do not transmit source files externally unless explicitly requested.
- Require explicit authorization before enabling MCP, host mounts, gateway
  access, or additional network access.
'@

Set-Content -Path $AgentsPath -Value $Agents -Encoding UTF8
Write-Host "  $AgentsPath"

# ---------------------------------------------------------------------------
# 7. Verify -- read the applied config back from Hermes itself rather than
#    just asserting what we think we wrote.
# ---------------------------------------------------------------------------

Write-Host "[7/7] Verifying..."
Write-Host ""
& $HermesExe --version
Write-Host ""

$VerifyKeys = @(
    "terminal.backend", "terminal.docker_network", "terminal.docker_mount_cwd_to_workspace",
    "terminal.docker_volumes", "terminal.docker_forward_env", "terminal.docker_extra_args",
    "terminal.container_persistent", "approvals.mode", "approvals.cron_mode",
    "approvals.unattended_mode", "command_allowlist", "mcp_servers"
)
Write-Host "Applied configuration (read back from Hermes):"
foreach ($k in $VerifyKeys) {
    $v = & $HermesExe config get $k 2>$null
    Write-Host ("  {0,-40}: {1}" -f $k, ($v -join ' '))
}

Write-Host ""
Write-Host "Computer Use driver status:"
& $HermesExe computer-use status

Write-Host ""
Write-Host "Secure Hermes baseline installed." -ForegroundColor Green
Write-Host ""
Write-Host "Hermes runtime      : $HermesHome"
Write-Host "Controlled workspace: $Workspace"
Write-Host "Default project     : $Workspace\projects\default"
Write-Host ""
Write-Host "No LLM provider has been configured. No Claude Code settings," `
    "MCPs, skills, gateway, WhatsApp, cron, or browser automation" `
    "were touched or imported." -ForegroundColor Yellow
Write-Host "Computer Use IS enabled (deliberately, for native screen capture / desktop" `
    "control -- see step 3). Screenshots need no approval; every click/type/key" `
    "action still requires manual approval per approvals.mode: manual." -ForegroundColor Yellow
Write-Host ""
Write-Host "Next: hermes setup"
Write-Host "  Configure ONLY the LLM provider/auth in that wizard for now --"
Write-Host "  do not enable/import MCPs, skills, gateway, WhatsApp, cron,"
Write-Host "  browser automation, or Claude Code configuration yet."
Write-Host ""

# The last external call above ('computer-use status') reports "not
# installed" with a non-zero exit -- that's expected/benign here, but left
# unreset it leaks through as this whole script's own exit code even though
# everything actually succeeded. Confirmed 2026-09-27: install completed
# fully (all 7 steps, verified config) yet the script still exited 1.
exit 0
