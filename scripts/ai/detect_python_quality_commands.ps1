<#
.SYNOPSIS
  Detect available Python test / lint / format / typecheck commands for this repo.

.DESCRIPTION
  Read-only inspection. Inspects pyproject.toml, setup.cfg, tox.ini, noxfile.py,
  Makefile, requirements*.txt and lock files, then prints the verification
  commands the implementer (a Claude Sonnet sub-agent) or reviewer (Codex CLI)
  should run.

  Does NOT install anything and does NOT run the tools. It only reports what is
  configured/available so handoffs can cite real commands instead of guessed ones.

.PARAMETER Json
  Emit a JSON object instead of human-readable text (useful for handoff scripts).

.EXAMPLE
  pwsh scripts/ai/detect_python_quality_commands.ps1
  pwsh scripts/ai/detect_python_quality_commands.ps1 -Json
#>
param([switch]$Json)

. "$PSScriptRoot\_common.ps1"
$root = Get-RepoRoot

function Read-FileSafe([string]$rel) {
    $p = Join-Path $root $rel
    if (Test-Path -LiteralPath $p) { return (Get-Content -LiteralPath $p -Raw) }
    return $null
}

$pyproject = Read-FileSafe "pyproject.toml"
$setupcfg  = Read-FileSafe "setup.cfg"
$toxini    = Read-FileSafe "tox.ini"
$hasNox    = Test-Path -LiteralPath (Join-Path $root "noxfile.py")
$makefile  = Read-FileSafe "Makefile"
$reqFiles  = Get-ChildItem -LiteralPath $root -Filter "requirements*.txt" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }
$locks     = @("poetry.lock","pdm.lock","uv.lock") | Where-Object { Test-Path -LiteralPath (Join-Path $root $_) }

# Pick the interpreter prefix the rest of the repo already uses.
$venvPy = $null
foreach ($cand in @("venv\Scripts\python.exe","venv/bin/python",".venv\Scripts\python.exe",".venv/bin/python")) {
    if (Test-Path -LiteralPath (Join-Path $root $cand)) { $venvPy = $cand; break }
}
$pyPrefix = if ($venvPy) { ".\$venvPy" } else { "python" }

$result = [ordered]@{
    repo_root      = $root
    python_prefix  = $pyPrefix
    test           = @()
    lint           = @()
    format         = @()
    typecheck      = @()
    sources_seen   = @()
    notes          = @()
}

function Has([string]$hay, [string]$needle) {
    return ($null -ne $hay) -and ($hay -match [regex]::Escape($needle))
}

# --- pytest ---
if ((Has $pyproject "[tool.pytest") -or (Has $setupcfg "[tool:pytest") -or (Has $toxini "pytest")) {
    $result.test += "$pyPrefix -m pytest"
    $result.test += "$pyPrefix -m pytest tests/test_<area>.py   # scoped while iterating"
}
# --- ruff (lint + format) ---
if (Has $pyproject "[tool.ruff") {
    $result.lint   += "$pyPrefix -m ruff check ."
    $result.format += "$pyPrefix -m ruff format ."
}
# --- black ---
if (Has $pyproject "[tool.black") {
    $result.format += "$pyPrefix -m black ."
}
# --- mypy ---
if (Has $pyproject "[tool.mypy") {
    $result.typecheck += "$pyPrefix -m mypy moralstack --ignore-missing-imports"
}
# --- pyright ---
if ((Has $pyproject "pyright") -or (Test-Path -LiteralPath (Join-Path $root "pyrightconfig.json"))) {
    $result.typecheck += "pyright"
}
# --- pre-commit (the project's canonical gate) ---
if (Test-Path -LiteralPath (Join-Path $root ".pre-commit-config.yaml")) {
    $result.lint += "$pyPrefix -m pre_commit run -a   # canonical pre-commit gate (ruff/black/mypy/whitespace)"
    $result.notes += "This repo gates on pre-commit; prefer 'pre_commit run -a' as the umbrella lint/format/type check."
}

# Record which source files were found.
if ($pyproject) { $result.sources_seen += "pyproject.toml" }
if ($setupcfg)  { $result.sources_seen += "setup.cfg" }
if ($toxini)    { $result.sources_seen += "tox.ini" }
if ($hasNox)    { $result.sources_seen += "noxfile.py" }
if ($makefile)  { $result.sources_seen += "Makefile" }
$result.sources_seen += $reqFiles
$result.sources_seen += $locks

if ($makefile) {
    $targets = Select-String -InputObject $makefile -Pattern '^(test|lint|format|typecheck|check)\s*:' -AllMatches
    if ($targets) { $result.notes += "Makefile defines targets you may prefer: try 'make test', 'make lint', etc." }
}
if ($result.test.Count -eq 0)      { $result.notes += "No pytest config detected; suggested fallback: '$pyPrefix -m pytest' (do NOT install deps without approval)." }
if ($result.typecheck.Count -eq 0) { $result.notes += "No typechecker config detected; suggested fallback: '$pyPrefix -m mypy <pkg>' if mypy is installed." }

if ($Json) {
    $result | ConvertTo-Json -Depth 5
    return
}

Write-Section "Python quality commands detected ($root)"
Write-Host "Interpreter prefix : $pyPrefix"
Write-Host "Sources inspected  : $((($result.sources_seen | Where-Object { $_ }) -join ', '))"
Write-Host ""
Write-Host "TEST:"      -ForegroundColor Green; $result.test      | ForEach-Object { Write-Host "  $_" }
Write-Host "LINT:"      -ForegroundColor Green; $result.lint      | ForEach-Object { Write-Host "  $_" }
Write-Host "FORMAT:"    -ForegroundColor Green; $result.format    | ForEach-Object { Write-Host "  $_" }
Write-Host "TYPECHECK:" -ForegroundColor Green; $result.typecheck | ForEach-Object { Write-Host "  $_" }
if ($result.notes.Count) {
    Write-Host ""
    Write-Host "NOTES:" -ForegroundColor Yellow
    $result.notes | ForEach-Object { Write-Host "  - $_" }
}
