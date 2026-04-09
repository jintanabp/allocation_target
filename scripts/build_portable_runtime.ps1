# Portable Python embeddable + pip + requirements -> runtime\python\
# Run: scripts\build_portable_runtime.bat
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PyDir = Join-Path $Root "runtime\python"
$PyVer = "3.11.9"
$ZipUrl = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip"

Write-Host "============================================"
Write-Host " Build portable Python (Windows amd64)"
Write-Host " Target: $PyDir"
Write-Host "============================================"

$runtimeDir = Join-Path $Root "runtime"
if (-not (Test-Path $runtimeDir)) {
    New-Item -ItemType Directory -Path $runtimeDir | Out-Null
}

if (-not (Test-Path (Join-Path $PyDir "python.exe"))) {
    Write-Host "Downloading embeddable Python $PyVer ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $zipPath = Join-Path $runtimeDir "pyembed.zip"
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zipPath -UseBasicParsing
    if (Test-Path $PyDir) { Remove-Item -LiteralPath $PyDir -Recurse -Force }
    New-Item -ItemType Directory -Path $PyDir | Out-Null
    Expand-Archive -LiteralPath $zipPath -DestinationPath $PyDir -Force
    Remove-Item -LiteralPath $zipPath -Force

    $pth = Get-ChildItem -LiteralPath $PyDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $pth) { throw "Missing python*._pth after unzip" }
    $text = [System.IO.File]::ReadAllText($pth.FullName)
    $text = $text.Replace("#import site", "import site")
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($pth.FullName, $text, $utf8NoBom)

    Write-Host "Downloading get-pip.py ..."
    $getPip = Join-Path $runtimeDir "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing

    Write-Host "Installing pip ..."
    $pyExe = Join-Path $PyDir "python.exe"
    & $pyExe $getPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }
}
else {
    Write-Host "Found runtime\python\python.exe -- skipping ZIP download"
}

Write-Host ""
Write-Host "pip install -r requirements.txt (may take several minutes) ..."
$py = Join-Path $PyDir "python.exe"
& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $py -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host ""
Write-Host "============================================"
Write-Host " Done. Zip the whole project including runtime\ and share."
Write-Host " Users double-click Run_Local.bat only."
Write-Host "============================================"
