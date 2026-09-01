param(

  [Parameter(Mandatory = $true)][string]$UpdateUrl,

  [Parameter(Mandatory = $false)][string]$OutDir = "dist_launcher",

  [Parameter(Mandatory = $false)][string]$ShortcutName = "Target Allocation.lnk"

)



$ErrorActionPreference = "Stop"



if (-not (Test-Path -LiteralPath $OutDir)) {

  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

}

# Absolute path: shortcuts with relative WorkingDirectory often break when Explorer runs them

$OutAbs = (Resolve-Path -LiteralPath $OutDir).Path



$exe = Join-Path $OutAbs "TargetAllocationLauncher.exe"

if (-not (Test-Path -LiteralPath $exe)) {

  throw @"

TargetAllocationLauncher.exe not found in: $OutAbs



Build the launcher first (from repo root):

  powershell -ExecutionPolicy Bypass -File .\scripts\build_launcher.ps1 -OutDir dist_launcher



Then run this script again.

"@

}



# URL lives beside the exe — launcher reads update_url.txt (avoids fragile .lnk / argv with & etc.)

$urlPath = Join-Path $OutAbs "update_url.txt"

[System.IO.File]::WriteAllText(

  $urlPath,

  $($UpdateUrl.Trim()) + "`n",

  (New-Object System.Text.UTF8Encoding $false)

)




# Optional double-click fallback (runs exe without args; reads update_url.txt)

$cmdPath = Join-Path $OutAbs "Start Target Allocation.cmd"

$cmd = @"

@echo off

cd /d "%~dp0"

"%~dp0TargetAllocationLauncher.exe"

"@

Set-Content -Path $cmdPath -Value $cmd -Encoding ASCII



# Shortcut targets ONLY the exe — Arguments empty — icon from exe(,0)

$shell = New-Object -ComObject WScript.Shell

$lnkPath = Join-Path $OutAbs $ShortcutName

$lnk = $shell.CreateShortcut($lnkPath)

$lnk.TargetPath = $exe

$lnk.Arguments = ""

$lnk.WorkingDirectory = $OutAbs

$lnk.IconLocation = "${exe},0"

$lnk.Description = "Target Allocation (launcher + updates)"



$lnk.Save()



Write-Host "Created:" -ForegroundColor Green

Write-Host " - $urlPath"

Write-Host " - $cmdPath"

Write-Host " - $lnkPath"

Write-Host "Tip: run scripts\\check_launcher_dist.ps1 to validate files and shortcut targets." -ForegroundColor DarkGray


