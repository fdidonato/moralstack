# ============================================================================
# MoralStack - Environment Setup Script for Windows
# ============================================================================
#
# USAGE:
#   .\scripts\setup_env.ps1              # Setup with GPU support
#   .\scripts\setup_env.ps1 -CpuOnly     # Setup CPU only (no bitsandbytes)
#   .\scripts\setup_env.ps1 -Minimal     # Minimal install for testing
#
# ============================================================================

param(
    [switch]$CpuOnly,
    [switch]$Minimal,
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   MoralStack Environment Setup                               ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check if we're in the right directory
if (-not (Test-Path "moralstack")) {
    Write-Host "❌ Error: Please run this script from the moralstack project root" -ForegroundColor Red
    exit 1
}

# Virtual environment name
$VENV_NAME = ".venv"

# Create virtual environment if it doesn't exist
if (-not (Test-Path $VENV_NAME)) {
    Write-Host "📦 Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $VENV_NAME
    Write-Host "   ✓ Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "📦 Virtual environment already exists" -ForegroundColor Green
}

# Activate virtual environment
Write-Host ""
Write-Host "🔄 Activating virtual environment..." -ForegroundColor Yellow
& "$VENV_NAME\Scripts\Activate.ps1"

# Upgrade pip
Write-Host ""
Write-Host "📥 Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip --quiet

# Install PyTorch
Write-Host ""
if ($CpuOnly) {
    Write-Host "🔧 Installing PyTorch (CPU only)..." -ForegroundColor Yellow
    pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet
} else {
    Write-Host "🔧 Installing PyTorch (with CUDA support)..." -ForegroundColor Yellow
    # Default to CUDA 12.1 - adjust if needed
    pip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet
}
Write-Host "   ✓ PyTorch installed" -ForegroundColor Green

# Install requirements
Write-Host ""
if ($Minimal) {
    Write-Host "📚 Installing minimal dependencies..." -ForegroundColor Yellow
    pip install transformers accelerate peft pytest --quiet
} elseif ($CpuOnly) {
    Write-Host "📚 Installing CPU-only dependencies..." -ForegroundColor Yellow
    pip install -r requirements-cpu.txt --quiet
} else {
    Write-Host "📚 Installing full dependencies..." -ForegroundColor Yellow
    # Try to install bitsandbytes, but don't fail if it doesn't work
    pip install transformers accelerate peft datasets pytest sentencepiece protobuf --quiet

    Write-Host ""
    Write-Host "🔧 Attempting to install bitsandbytes..." -ForegroundColor Yellow
    try {
        pip install bitsandbytes --quiet 2>$null
        Write-Host "   ✓ bitsandbytes installed" -ForegroundColor Green
    } catch {
        Write-Host "   ⚠ bitsandbytes failed - trying Windows version..." -ForegroundColor Yellow
        try {
            pip install bitsandbytes-windows --quiet 2>$null
            Write-Host "   ✓ bitsandbytes-windows installed" -ForegroundColor Green
        } catch {
            Write-Host "   ⚠ bitsandbytes not available on Windows" -ForegroundColor Yellow
            Write-Host "     You can still use MoralStack, but without 4-bit quantization" -ForegroundColor Yellow
            Write-Host "     Consider using WSL2 for full GPU support" -ForegroundColor Yellow
        }
    }
}

Write-Host "   ✓ Dependencies installed" -ForegroundColor Green

# Install moralstack in development mode
#Write-Host ""
#Write-Host "📦 Installing moralstack package..." -ForegroundColor Yellow
#pip install -e . --quiet 2>$null
#if ($LASTEXITCODE -ne 0) {
#    Write-Host "   ⚠ Package install skipped (no setup.py/pyproject.toml)" -ForegroundColor Yellow
#}

# Verify installation
Write-Host ""
Write-Host "🔍 Verifying installation..." -ForegroundColor Yellow

$verification = @"
import torch
print(f'   ✓ PyTorch {torch.__version__}', end='')
print(f' (CUDA: {torch.cuda.is_available()})')
import transformers
print(f'   ✓ Transformers {transformers.__version__}')
import peft
print(f'   ✓ PEFT {peft.__version__}')
try:
    import bitsandbytes
    print(f'   ✓ bitsandbytes {bitsandbytes.__version__}')
except ImportError:
    print('   ⚠ bitsandbytes not available')
"@

python -c $verification

# Final instructions
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "✅ Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "To activate the environment in the future:" -ForegroundColor White
Write-Host "   .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "To run MoralStack:" -ForegroundColor White
if ($CpuOnly -or $Minimal) {
    Write-Host "   python scripts/mstack_run.py --mock" -ForegroundColor Yellow
} else {
    Write-Host "   python scripts/mstack_run.py --model mistralai/Mistral-7B-Instruct-v0.2" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "To run tests:" -ForegroundColor White
Write-Host "   python -m pytest tests/ -v" -ForegroundColor Yellow
Write-Host ""
