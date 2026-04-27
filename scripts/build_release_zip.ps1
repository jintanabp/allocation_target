param(
  [Parameter(Mandatory=$true)][string]$Version,
  [Parameter(Mandatory=$false)][string]$OutDir = "dist_release"
)

$ErrorActionPreference = "Stop"

Write-Host "== Build release zip $Version ==" -ForegroundColor Cyan

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force $OutDir | Out-Null

$zipName = "TargetAllocation-$Version.zip"
$zipPath = Join-Path $OutDir $zipName

# Exclude developer-only folders
$exclude = @(
  ".git", ".venv", "data", "__pycache__", ".pytest_cache", ".mypy_cache",
  "dist", "build", "dist_launcher", "dist_release"
)

function ShouldExclude($rel) {
  foreach ($x in $exclude) {
    if ($rel -eq $x) { return $true }
    if ($rel.StartsWith("$x\\")) { return $true }
    if ($rel.StartsWith("$x/")) { return $true }
  }
  return $false
}

$tmp = Join-Path $env:TEMP ("ta_release_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null

try {
  $root = (Resolve-Path ".").Path
  Get-ChildItem -Force -Recurse $root | ForEach-Object {
    $full = $_.FullName
    $rel = $full.Substring($root.Length).TrimStart("\\")
    if ($rel -eq "") { return }
    if (ShouldExclude($rel)) { return }
    if ($_.PSIsContainer) { return }
    $dest = Join-Path $tmp $rel
    New-Item -ItemType Directory -Force (Split-Path $dest -Parent) | Out-Null
    Copy-Item $full $dest -Force
  }

  if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
  Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $zipPath

  $hash = (Get-FileHash -Algorithm SHA256 $zipPath).Hash.ToLower()
  $manifest = @{
    version = $Version
    url     = "<IT will host this zip at an internal HTTPS URL>"
    sha256  = $hash
    notes   = "Release $Version"
  } | ConvertTo-Json -Depth 4
  $manifestPath = Join-Path $OutDir "latest.json"
  Set-Content -Path $manifestPath -Value $manifest -Encoding UTF8

  Write-Host "Zip: $zipPath" -ForegroundColor Green
  Write-Host "SHA256: $hash" -ForegroundColor Green
  Write-Host "Manifest: $manifestPath (update url field after hosting)" -ForegroundColor Yellow
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

