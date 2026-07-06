<#
.SYNOPSIS
  Shared helpers for the MoralStack AI agentic workflow scripts (PowerShell).

.DESCRIPTION
  DRY helpers used by collect_git_diff.ps1 and the other AI-workflow scripts.
  Dot-source this file:

      . "$PSScriptRoot\_common.ps1"

  No side effects on import. No network, no commits, no destructive git.
#>

Set-StrictMode -Version Latest

function Get-RepoRoot {
    # Resolve the git repo root; fall back to two levels up from this script.
    try {
        $root = (& git rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and $root) { return (Resolve-Path $root).Path }
    } catch { }
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function New-Timestamp {
    return (Get-Date -Format "yyyyMMdd-HHmmss")
}

function Confirm-Dir {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Write-Section {
    param([Parameter(Mandatory)][string]$Text)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor DarkGray
}
