<#
.SYNOPSIS
  Run an independent Codex CLI review of the current DIFF against an approved plan.

.DESCRIPTION
  Collects the working-tree diff (via collect_git_diff.ps1 unless -DiffPath is
  given), then asks Codex to review that diff against the approved plan and the
  Cursor handoff. Runs non-interactively in a READ-ONLY sandbox:

      codex exec -s read-only [-m <model>] -o <review.md> -   (prompt on stdin)

  Codex inspects the repo read-only; it never modifies, commits, or pushes.

.PARAMETER PlanPath
  Path to the approved plan markdown. Required.

.PARAMETER DiffPath
  Optional pre-collected diff markdown. If omitted, collect_git_diff.ps1 is run.

.PARAMETER HandoffPath
  Optional path to the Cursor handoff used for the implementation (for context).

.PARAMETER Base
  Optional base ref forwarded to collect_git_diff.ps1 when collecting the diff.

.PARAMETER OutDir
  Where to write the review. Default: ai/reviews/

.PARAMETER Model
  Codex model override. Default: $env:CODEX_MODEL (see run_codex_plan_review.ps1).

.PARAMETER DryRun
  Build/save the prompt but do not invoke Codex.

.EXAMPLE
  pwsh scripts/ai/run_codex_diff_review.ps1 -PlanPath ai/plans/my-feature.md
#>
param(
    [Parameter(Mandatory)][string]$PlanPath,
    [string]$DiffPath,
    [string]$HandoffPath,
    [string]$Base,
    [string]$OutDir,
    [string]$Model = $env:CODEX_MODEL,
    [switch]$DryRun
)

. "$PSScriptRoot\_common.ps1"
$root = Get-RepoRoot
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath $PlanPath)) { Write-Error "Plan not found: $PlanPath"; exit 1 }
    $ts = New-Timestamp
    $planName = [System.IO.Path]::GetFileNameWithoutExtension($PlanPath)
    if (-not $OutDir) { $OutDir = Join-Path $root "ai\reviews" }
    $OutDir = Confirm-Dir $OutDir
    $promptsDir = Confirm-Dir (Join-Path $root "ai\prompts")

    if (-not $DiffPath) {
        Write-Host "Collecting current diff..." -ForegroundColor Cyan
        $collectArgs = @{}
        if ($Base) { $collectArgs.Base = $Base }
        $DiffPath = & "$PSScriptRoot\collect_git_diff.ps1" @collectArgs | Select-Object -Last 1
    }
    if (-not (Test-Path -LiteralPath $DiffPath)) { Write-Error "Diff not found: $DiffPath"; exit 1 }

    $templatePath = Join-Path $root "ai\prompts\codex-diff-review-template.md"
    if (-not (Test-Path -LiteralPath $templatePath)) { Write-Error "Missing template: $templatePath"; exit 1 }

    $template = Get-Content -LiteralPath $templatePath -Raw
    $planBody = Get-Content -LiteralPath $PlanPath -Raw
    $diffBody = Get-Content -LiteralPath $DiffPath -Raw
    $handoffBody = if ($HandoffPath -and (Test-Path -LiteralPath $HandoffPath)) { Get-Content -LiteralPath $HandoffPath -Raw } else { "(no handoff provided)" }

    $prompt = @"
$template

---

## Repository context
- Repo root: $root
- MoralStack governance engine. Verify the diff does NOT break any invariant in
  PROJECT_SPEC.md section 5 / .claude/rules/ (decision/generation separation,
  hard-signal supremacy, prompt transparency, governed delivery, observability).
- You may read any file in the repo (read-only) to verify the diff in context.

---

## APPROVED PLAN (file: $PlanPath)

$planBody

---

## CURSOR HANDOFF (file: $HandoffPath)

$handoffBody

---

## DIFF UNDER REVIEW (file: $DiffPath)

$diffBody
"@

    $generatedPrompt = Join-Path $promptsDir "generated-codex-diff-review-$planName-$ts.md"
    Set-Content -LiteralPath $generatedPrompt -Value $prompt -Encoding UTF8
    $reviewOut = Join-Path $OutDir "codex-diff-review-$planName-$ts.md"

    $codex = Resolve-CodexCmd
    $modelArgs = @()
    if ($Model) { $modelArgs += @("-m", $Model) }
    if ($env:CODEX_REASONING_EFFORT) { $modelArgs += @("-c", "model_reasoning_effort=`"$($env:CODEX_REASONING_EFFORT)`"") }

    if (-not $codex -or $DryRun) {
        Write-Section "Codex CLI not invoked (manual mode)"
        if (-not $codex) { Write-Host "Codex CLI not found. Set `$env:CODEX_CMD or install Codex." -ForegroundColor Yellow }
        Write-Host "Prompt saved to: $generatedPrompt"
        Write-Host ""
        Write-Host "Run manually:" -ForegroundColor Cyan
        Write-Host "  Get-Content -Raw '$generatedPrompt' | codex exec -s read-only $($modelArgs -join ' ') -o '$reviewOut' -"
        exit 2
    }

    Write-Section "Running Codex diff review (read-only sandbox)"
    Write-Host "Plan     : $PlanPath"
    Write-Host "Diff     : $DiffPath"
    Write-Host "Review ->: $reviewOut"
    Write-Host ""

    $prompt | & $codex exec -s read-only @modelArgs -o $reviewOut -
    $code = $LASTEXITCODE

    if (Test-Path -LiteralPath $reviewOut) {
        Write-Host ""
        Write-Host "Review written: $reviewOut" -ForegroundColor Green
        Write-Output $reviewOut
    } else {
        Write-Error "Codex exited ($code) but no review file was produced. Prompt at $generatedPrompt."
        exit 3
    }
}
finally {
    Pop-Location
}
