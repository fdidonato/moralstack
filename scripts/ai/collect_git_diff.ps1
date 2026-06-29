<#
.SYNOPSIS
  Save the current working-tree diff to a markdown file under ai/reviews/.

.DESCRIPTION
  Captures `git diff` (tracked changes), optionally staged, plus a list of
  untracked files and `git status`. Writes a single markdown artifact that the
  Codex diff-review step consumes. Read-only with respect to git: NEVER commits,
  stages, pushes, or deletes anything.

.PARAMETER Base
  Optional base ref to diff against (e.g. 'main' or a commit SHA). When set,
  captures `git diff <Base>...HEAD` plus the working tree.

.PARAMETER OutPath
  Explicit output path. Default: ai/reviews/diff-<timestamp>.md

.EXAMPLE
  pwsh scripts/ai/collect_git_diff.ps1
  pwsh scripts/ai/collect_git_diff.ps1 -Base main
#>
param(
    [string]$Base,
    [string]$OutPath
)

. "$PSScriptRoot\_common.ps1"
$root = Get-RepoRoot
Push-Location $root
try {
    $ts = New-Timestamp
    if (-not $OutPath) {
        $reviewsDir = Confirm-Dir (Join-Path $root "ai\reviews")
        $OutPath = Join-Path $reviewsDir "diff-$ts.md"
    }

    $status   = (& git status --short --branch 2>&1) -join "`n"
    $worktree = (& git diff 2>&1) -join "`n"
    $staged   = (& git diff --cached 2>&1) -join "`n"
    $untracked = (& git ls-files --others --exclude-standard 2>&1)
    $baseDiff = $null
    if ($Base) {
        $baseDiff = (& git diff "$Base...HEAD" 2>&1) -join "`n"
    }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("# Working-tree diff snapshot")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("- Generated: $ts")
    [void]$sb.AppendLine("- Repo: $root")
    if ($Base) { [void]$sb.AppendLine("- Base ref: $Base") }
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## git status")
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine($status)
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## Untracked files (not yet added)")
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine(($untracked -join "`n"))
    [void]$sb.AppendLine('```')
    if ($Base) {
        [void]$sb.AppendLine("")
        [void]$sb.AppendLine("## git diff $Base...HEAD")
        [void]$sb.AppendLine('```diff')
        [void]$sb.AppendLine($baseDiff)
        [void]$sb.AppendLine('```')
    }
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## git diff (unstaged working tree)")
    [void]$sb.AppendLine('```diff')
    [void]$sb.AppendLine($worktree)
    [void]$sb.AppendLine('```')
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## git diff --cached (staged)")
    [void]$sb.AppendLine('```diff')
    [void]$sb.AppendLine($staged)
    [void]$sb.AppendLine('```')

    Set-Content -LiteralPath $OutPath -Value $sb.ToString() -Encoding UTF8
    Write-Section "Diff saved"
    Write-Host "Wrote: $OutPath"
    Write-Output $OutPath
}
finally {
    Pop-Location
}
