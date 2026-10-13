#requires -Version 5.1
<#
.SYNOPSIS
    Read-only check: has upstream chrome-mcp moved past the vendored copy?
.DESCRIPTION
    Reads UPSTREAM_COMMIT / UPSTREAM_VERSION from chrome-mcp\VENDORED.md, asks GitHub for
    upstream main's HEAD, and if it differs lists the commits since the vendored one.
    Changes nothing. Re-syncing is manual (see VENDORED.md) because local edits to the
    vendored files must be re-applied deliberately.
#>
$ErrorActionPreference = 'Stop'
$meta = Get-Content (Join-Path $PSScriptRoot 'chrome-mcp\VENDORED.md') -Raw
$commit  = [regex]::Match($meta, '(?m)^UPSTREAM_COMMIT=(\w+)').Groups[1].Value
$version = [regex]::Match($meta, '(?m)^UPSTREAM_VERSION=(\S+)').Groups[1].Value
if (-not $commit) { throw 'UPSTREAM_COMMIT not found in chrome-mcp\VENDORED.md' }

$repo = 'Mehmoodqureshi/chrome-mcp'
$head = (Invoke-RestMethod "https://api.github.com/repos/$repo/commits/main" -Headers @{ 'User-Agent' = 'hermes-setup' }).sha
Write-Host "Vendored: $version @ $($commit.Substring(0,7))"
Write-Host "Upstream: main @ $($head.Substring(0,7))"
if ($head -eq $commit) { Write-Host 'Up to date.'; return }

$cmp = Invoke-RestMethod "https://api.github.com/repos/$repo/compare/$commit...$head" -Headers @{ 'User-Agent' = 'hermes-setup' }
Write-Host "`n$($cmp.ahead_by) new commit(s):"
$cmp.commits | ForEach-Object { '  {0}  {1}' -f $_.sha.Substring(0,7), ($_.commit.message -split "`n")[0] }
Write-Host "`nFull diff: $($cmp.html_url)"
