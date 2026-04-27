param(
  [Parameter(Mandatory=$true)][string]$UpdateUrl,
  [Parameter(Mandatory=$false)][string]$OutDir = "dist_launcher",
  [Parameter(Mandatory=$false)][string]$ShortcutName = "Target Allocation.lnk"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutDir)) {
  New-Item -ItemType Directory -Force $OutDir | Out-Null
}

$exe = Join-Path $OutDir "TargetAllocationLauncher.exe"
if (-not (Test-Path $exe)) {
  throw "TargetAllocationLauncher.exe not found. Run scripts\\build_launcher.ps1 first."
}

# Create a .cmd wrapper so the shortcut is simple and does not require setting env
$cmdPath = Join-Path $OutDir "Start Target Allocation.cmd"
$cmd = @"
@echo off
setlocal
set ""TARGET_ALLOC_UPDATE_URL=$UpdateUrl""
start """" ""%~dp0TargetAllocationLauncher.exe"" ""%TARGET_ALLOC_UPDATE_URL%""
"@
Set-Content -Path $cmdPath -Value $cmd -Encoding ASCII

# Create shortcut (.lnk) pointing to the .cmd
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $OutDir $ShortcutName))
$lnk.TargetPath = "$env:WINDIR\System32\cmd.exe"
# Run the .cmd in WorkingDirectory (portable)
$lnk.Arguments = "/c ""Start Target Allocation.cmd"""
$lnk.WorkingDirectory = $OutDir
$lnk.IconLocation = $exe
$lnk.Save()

Write-Host "Created:" -ForegroundColor Green
Write-Host " - $cmdPath"
Write-Host " - $(Join-Path $OutDir $ShortcutName)"

