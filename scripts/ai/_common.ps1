<#
.SYNOPSIS
  Shared helpers for the MoralStack AI agentic workflow scripts (PowerShell).

.DESCRIPTION
  DRY helpers used by run_cursor_implementation.ps1, collect_git_diff.ps1.
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

function Resolve-CursorAgentCmd {
    <#
      Resolve the Cursor *agent* CLI (the headless implementer, distinct from the
      'cursor' GUI launcher). Override with $env:CURSOR_CMD.
      Lookup order:
        1. $env:CURSOR_CMD (if set and resolvable)
        2. 'cursor-agent' on PATH
        3. %LOCALAPPDATA%\cursor-agent\cursor-agent.cmd (Windows install location)
      Returns the resolved command/path string, or $null if not found.
    #>
    if ($env:CURSOR_CMD) {
        $explicit = Get-Command $env:CURSOR_CMD -ErrorAction SilentlyContinue
        if ($explicit) { return $env:CURSOR_CMD }
        if (Test-Path -LiteralPath $env:CURSOR_CMD) { return $env:CURSOR_CMD }
    }
    $onPath = Get-Command "cursor-agent" -ErrorAction SilentlyContinue
    if ($onPath) { return "cursor-agent" }
    $local = Join-Path $env:LOCALAPPDATA "cursor-agent\cursor-agent.cmd"
    if (Test-Path -LiteralPath $local) { return $local }
    return $null
}

function Write-Section {
    param([Parameter(Mandatory)][string]$Text)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor DarkGray
}
