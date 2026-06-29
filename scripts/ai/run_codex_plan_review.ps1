<#
.SYNOPSIS
  Run an independent Codex CLI review of a technical PLAN (before implementation).

.DESCRIPTION
  Builds a rigorous review prompt by combining the plan-review template
  (ai/prompts/codex-plan-review-template.md) with the plan under review, then
  invokes Codex non-interactively in a READ-ONLY sandbox:

      codex exec -s read-only [-m <model>] -o <review.md> -   (prompt on stdin)

  The generated prompt is always saved (ai/prompts/generated-*) so the step is
  reproducible and can be run manually if the CLI is unavailable. Codex inspects
  the repo read-only; it cannot modify files, commit, or push.

.PARAMETER PlanPath
  Path to the plan markdown (typically under ai/plans/). Required.

.PARAMETER OutDir
  Where to write the review. Default: ai/reviews/

.PARAMETER Model
  Codex model override. Default: $env:CODEX_MODEL (empty => Codex uses its own
  configured default, which on this machine is gpt-5.5 at xhigh reasoning effort,
  i.e. the high-effort review setting). Reasoning effort can be overridden with
  $env:CODEX_REASONING_EFFORT (passed as -c model_reasoning_effort=<value>).

.PARAMETER DryRun
  Build and save the prompt but do NOT invoke Codex; print the manual command.

.EXAMPLE
  pwsh scripts/ai/run_codex_plan_review.ps1 -PlanPath ai/plans/my-feature.md
#>
param(
    [Parameter(Mandatory)][string]$PlanPath,
    [string]$OutDir,
    [string]$Model = $env:CODEX_MODEL,
    [switch]$DryRun
)

. "$PSScriptRoot\_common.ps1"
$root = Get-RepoRoot
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath $PlanPath)) {
        Write-Error "Plan not found: $PlanPath"; exit 1
    }
    $ts = New-Timestamp
    $planName = [System.IO.Path]::GetFileNameWithoutExtension($PlanPath)
    if (-not $OutDir) { $OutDir = Join-Path $root "ai\reviews" }
    $OutDir = Confirm-Dir $OutDir
    $promptsDir = Confirm-Dir (Join-Path $root "ai\prompts")

    $templatePath = Join-Path $root "ai\prompts\codex-plan-review-template.md"
    if (-not (Test-Path -LiteralPath $templatePath)) {
        Write-Error "Missing template: $templatePath"; exit 1
    }
    $template = Get-Content -LiteralPath $templatePath -Raw
    $planBody = Get-Content -LiteralPath $PlanPath -Raw

    $prompt = @"
$template

---

## Repository context
- Repo root: $root
- This is the MoralStack governance engine. Safety invariants are in
  PROJECT_SPEC.md (section 5) and .claude/rules/. Flag any plan step that risks
  breaking them (decision/generation separation, hard-signal supremacy,
  prompt transparency, governed delivery, observability best-effort).
- You may read any file in the repo (read-only) to verify the plan's claims.

---

## PLAN UNDER REVIEW (file: $PlanPath)

$planBody
"@

    $generatedPrompt = Join-Path $promptsDir "generated-codex-plan-review-$planName-$ts.md"
    Set-Content -LiteralPath $generatedPrompt -Value $prompt -Encoding UTF8
    $reviewOut = Join-Path $OutDir "codex-plan-review-$planName-$ts.md"

    $codex = Resolve-CodexCmd
    $modelArgs = @()
    if ($Model) { $modelArgs += @("-m", $Model) }
    if ($env:CODEX_REASONING_EFFORT) { $modelArgs += @("-c", "model_reasoning_effort=`"$($env:CODEX_REASONING_EFFORT)`"") }

    if (-not $codex -or $DryRun) {
        Write-Section "Codex CLI not invoked (manual mode)"
        if (-not $codex) {
            Write-Host "Codex CLI not found. Set `$env:CODEX_CMD or install Codex." -ForegroundColor Yellow
        }
        Write-Host "Prompt saved to: $generatedPrompt"
        Write-Host ""
        Write-Host "Run manually, e.g.:" -ForegroundColor Cyan
        Write-Host "  Get-Content -Raw '$generatedPrompt' | codex exec -s read-only $($modelArgs -join ' ') -o '$reviewOut' -"
        exit 2
    }

    Write-Section "Running Codex plan review (read-only sandbox)"
    Write-Host "Model    : $(if ($Model) { $Model } else { '(Codex default: gpt-5.5/xhigh)' })"
    Write-Host "Prompt   : $generatedPrompt"
    Write-Host "Review -> : $reviewOut"
    Write-Host ""

    $prompt | & $codex exec -s read-only @modelArgs -o $reviewOut -
    $code = $LASTEXITCODE

    if (Test-Path -LiteralPath $reviewOut) {
        Write-Host ""
        Write-Host "Review written: $reviewOut" -ForegroundColor Green
        Write-Output $reviewOut
    } else {
        Write-Error "Codex exited ($code) but no review file was produced. Inspect the prompt at $generatedPrompt and run manually."
        exit 3
    }
}
finally {
    Pop-Location
}
