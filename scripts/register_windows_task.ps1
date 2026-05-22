param(
  [string]$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$TaskName = "DETSIS Mevzuat Paneli Yerel Guncelleme"
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $RepoDir "scripts\local_update.ps1"
if (-not (Test-Path $scriptPath)) {
  throw "local_update.ps1 bulunamadı: $scriptPath"
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -RepoDir `"$RepoDir`""

schtasks.exe /Create /TN $TaskName /TR $action /SC HOURLY /MO 6 /F | Out-Host

Write-Host "Görev oluşturuldu: $TaskName"
Write-Host "Sıklık: 6 saatte bir"
Write-Host "Elle test için:"
Write-Host "schtasks /Run /TN `"$TaskName`""
