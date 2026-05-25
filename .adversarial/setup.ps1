$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[1/3] Validating .adversarial kit structure..."
python scripts/validate_artifacts.py --root $Root

Write-Host "[2/3] Checking optional external tools..."
foreach ($bin in @("git", "rg", "claude", "codex")) {
    $cmd = Get-Command $bin -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host "OK: $bin"
    } else {
        Write-Host "WARN: $bin not found in PATH"
    }
}

Write-Host "[3/3] Done."
Write-Host "Next: run from repo root: python .adversarial/scripts/adversarial_plan.py --task .adversarial/tasks/example_task.md --dry-run"
