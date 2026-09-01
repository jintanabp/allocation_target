param(
  [Parameter(Mandatory=$false)][string]$OutDir = "dist_launcher",
  # No console window on double-click (errors go to %LOCALAPPDATA%\TargetAllocation\launcher.log)
  [Parameter(Mandatory=$false)][switch]$Console = $false
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

# Do not Remove-Item OutDir recursively: preserves Start Target Allocation.cmd / .lnk from make_launcher_shortcut.ps1
if (-not (Test-Path -LiteralPath $OutDir)) {
  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

$pyiArgs = @(
  "--noconfirm"
  "--clean"
  "--onefile"
  "--name", "TargetAllocationLauncher"
  "launcher\\launcher.py"
)
if (-not $Console) {
  $pyiArgs = @("--windowed") + $pyiArgs
  Write-Host "PyInstaller: --windowed (use -Console to show a console for debugging)" -ForegroundColor DarkGray
}

& $pyForBuild -m PyInstaller @pyiArgs

$outExe = Join-Path $OutDir "TargetAllocationLauncher.exe"
Copy-Item ".\\dist\\TargetAllocationLauncher.exe" -Destination $outExe -Force

Write-Host "Output: $outExe" -ForegroundColor Green
Write-Host "Tip: run make_launcher_shortcut.ps1 if you need a fresh .cmd/.lnk with a new update URL." -ForegroundColor DarkGray

