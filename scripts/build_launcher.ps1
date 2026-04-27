param(
  [Parameter(Mandatory=$false)][string]$OutDir = "dist_launcher"
)

$ErrorActionPreference = "Stop"

Write-Host "== Build TargetAllocationLauncher.exe ==" -ForegroundColor Cyan

function Resolve-Python {
  # Prefer: py launcher -> portable runtime python -> python (skip Microsoft Store alias)
  $root = (Resolve-Path ".").Path

  try {
    $py = (Get-Command py -ErrorAction Stop).Source
    return @{ Kind = "py"; Path = $py }
  } catch {}

  $portPy = Join-Path $root "runtime\python\python.exe"
  if (Test-Path $portPy) {
    return @{ Kind = "portable"; Path = $portPy }
  }

  try {
    $python = (Get-Command python -ErrorAction Stop).Source
    # Microsoft Store alias usually lives under WindowsApps and is a stub
    if ($python -like "*\\WindowsApps\\python.exe") { throw "python is Microsoft Store alias" }
    return @{ Kind = "python"; Path = $python }
  } catch {}

  throw "Python not found. Install Python 3.11+ (python.exe) or add runtime\\python\\python.exe before building."
}

$pyInfo = Resolve-Python
$pyKind = $pyInfo.Kind
$pyPath = $pyInfo.Path

if ($pyKind -eq "portable") {
  # Portable/embedded python may not include venv module.
  # Launcher build only needs pyinstaller (launcher.py uses stdlib only).
  Write-Host "Using portable python (no venv)..." -ForegroundColor Yellow
  & $pyPath -m pip install -U pip pyinstaller | Out-Null
  $pyForBuild = $pyPath
} else {
  if (-not (Test-Path ".venv\\Scripts\\python.exe")) {
    Write-Host "Creating .venv..." -ForegroundColor Yellow
    if ($pyKind -eq "py") {
      & $pyPath -3.11 -m venv .venv 2>$null
      if (-not (Test-Path ".venv\\Scripts\\python.exe")) { & $pyPath -3 -m venv .venv }
    } else {
      & $pyPath -m venv .venv
    }
  }
  & .venv\\Scripts\\python.exe -m pip install -U pip | Out-Null
  & .venv\\Scripts\\python.exe -m pip install -r requirements.txt | Out-Null
  & .venv\\Scripts\\python.exe -m pip install pyinstaller | Out-Null
  $pyForBuild = ".venv\\Scripts\\python.exe"
}

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force $OutDir | Out-Null

& $pyForBuild -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name "TargetAllocationLauncher" `
  launcher\\launcher.py

Copy-Item ".\\dist\\TargetAllocationLauncher.exe" -Destination (Join-Path $OutDir "TargetAllocationLauncher.exe") -Force

Write-Host "Output: $OutDir\\TargetAllocationLauncher.exe" -ForegroundColor Green

