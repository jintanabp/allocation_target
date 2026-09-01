param(
  [Parameter(Mandatory = $false)][string]$DistDir = "dist_launcher",
  [Parameter(Mandatory = $false)][switch]$ClearMarkOfWeb
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $DistDir)) {
  Write-Host "ERROR: folder not found: $DistDir" -ForegroundColor Red
  exit 1
}

$OutAbs = (Resolve-Path -LiteralPath $DistDir).Path
$exe = Join-Path $OutAbs "TargetAllocationLauncher.exe"
$txt = Join-Path $OutAbs "update_url.txt"
$cmdPath = Join-Path $OutAbs "Start Target Allocation.cmd"
$lnk = Join-Path $OutAbs "Target Allocation.lnk"

function Test-NetZoneStream {
  param([string]$LiteralPath)
  try {
    if (-not (Test-Path -LiteralPath $LiteralPath)) { return $false }
    $full = (Resolve-Path -LiteralPath $LiteralPath).ProviderPath
    $blob = Get-Content -LiteralPath "${full}:Zone.Identifier" -Raw -ErrorAction SilentlyContinue
    if ($null -eq $blob) { return $false }
    return (($blob.Trim().Length -gt 0))
  } catch {
    return $false
  }
}

function Show-NetZoneLine {
  param([string]$Role, [string]$LiteralPath)

  if (-not (Test-Path -LiteralPath $LiteralPath)) { return }
  $flagged = Test-NetZoneStream -LiteralPath $LiteralPath
  if ($flagged) {
    $msg = "WARNING: alternate stream Zone.Identifier exists (blocked download / unzip / email)"
    $col = "Yellow"
  } else {
    $msg = "OK no Zone.Identifier alternate stream"


    $col = "Green"


  }

  Write-Host ("  [{0}] {1}" -f $Role, $msg) -ForegroundColor $col

}

Write-Host "== Launcher dist check : $OutAbs ==" -ForegroundColor Cyan

function Write-Probe {
  param([string]$Label, [string]$Path)

  $ok = Test-Path -LiteralPath $Path
  Write-Host ("{0,-24} " -f $Label) -NoNewline


  Write-Host $(if ($ok) {


      "OK"


    }


    else {


      "MISSING"


    }) -ForegroundColor $(if ($ok) { "Green" } else {


    "Red" })


  if (($ok) -and ($Path -like "*.exe")) {


    $len = (Get-Item -LiteralPath $Path).Length




    Write-Host ("  SizeBytes: {0}" -f $len)


    try {



      Write-Host ("  AuthenticodeSignature: {0}" -f (( Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction SilentlyContinue).Status))



    } catch {}


  }}

Write-Probe "TargetAllocationLauncher.exe" $exe

if ((Test-Path -LiteralPath $exe)) { Show-NetZoneLine "exe MOTW?" $exe }

Write-Probe "update_url.txt" $txt

if ((Test-Path -LiteralPath $txt)) {
  $raw = Get-Content -LiteralPath $txt -Raw -Encoding UTF8
  $first = (($raw.Trim() -split "`n")[0]).Trim()


  $take = [Math]::Min(80, $first.Length)

  Write-Host ("  preview first line ({0} chars shown): " -f $take ) -NoNewline

  Write-Host $(if ($take -eq 0) { "(empty)" } else {


      $first.Substring(0, $take)



    })
}

Write-Probe "Start Target Allocation.cmd" $cmdPath

if ((Test-Path -LiteralPath $cmdPath)) { Show-NetZoneLine "cmd MOTW?" $cmdPath }

Write-Probe "Target Allocation.lnk" $lnk

if ((Test-Path -LiteralPath $lnk)) {


  Show-NetZoneLine "lnk MOTW?" $lnk


}




if ((Test-Path -LiteralPath $lnk)) {





  $sh = New-Object -ComObject WScript.Shell




  try {





    $sc = $sh.CreateShortcut($lnk)








    Write-Host "Shortcut.TargetPath  :" $sc.TargetPath




    $argsShow = $(if ([string]::IsNullOrWhiteSpace($sc.Arguments)) { "(empty OK)" } else { $sc.Arguments })




    Write-Host "Shortcut.Arguments   :" $argsShow




    Write-Host "Shortcut.WorkingDir  :" $sc.WorkingDirectory









    Write-Host ("Target file exists?  :" + ($(if ((Test-Path -LiteralPath $sc.TargetPath)) { "YES" } else { "NO" }))) -ForegroundColor $(if ((Test-Path -LiteralPath $sc.TargetPath)) { "Green" } else {


        "Red"




      })




  } finally {






    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($sh)




  }




}







if ($ClearMarkOfWeb) {





  Write-Host ""








  Write-Host "Unblock-File (removes alternate stream marking)..." -ForegroundColor Cyan








  foreach ($p in @($exe, $cmdPath, $lnk)) {








    if (-not (Test-Path -LiteralPath $p)) {


      continue




    }






    try {








      Unblock-File -LiteralPath $p -ErrorAction Stop








      Write-Host " cleared: $p" -ForegroundColor Green








    }








    catch {


      Write-Host " failed: $p $($_.Exception.Message)" -ForegroundColor Red


    }







  }







}









Write-Host ""


Write-Host "If explorer still refuses to run exe with CANNOT ACCESS:" -ForegroundColor Yellow


Write-Host "  * run this script with -ClearMarkOfWeb   OR"


Write-Host "  * right-click exe - Properties - Unblock (if checkbox visible)"


Write-Host "  * copy dist_launcher folder to eg C:\\Tools\\Allocation\\ (outside OneDrive) and retry"


Write-Host ""

