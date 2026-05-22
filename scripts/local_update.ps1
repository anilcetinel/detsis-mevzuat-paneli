param(
  [string]$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$PythonExe = "",
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location $RepoDir

$logDir = Join-Path $RepoDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runLog = Join-Path $logDir "local_scheduler.log"

function Write-RunLog {
  param([string]$Message)
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -Path $runLog -Value $line -Encoding UTF8
  Write-Host $line
}

function Find-Python {
  if ($PythonExe -and (Test-Path $PythonExe)) {
    return (Resolve-Path $PythonExe).Path
  }

  $candidates = @(
    (Join-Path $RepoDir ".venv\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
  )

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return (Resolve-Path $candidate).Path
    }
  }

  $command = Get-Command python -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    return "py -3"
  }

  throw "Python bulunamadı. Python 3 kurun veya -PythonExe parametresiyle python.exe yolunu verin."
}

function Invoke-Python {
  param([string[]]$Arguments)
  if ($script:PythonCommand -eq "py -3") {
    & py -3 @Arguments
  } else {
    & $script:PythonCommand @Arguments
  }
}

function Invoke-PythonLogged {
  param([string[]]$Arguments)
  if ($script:PythonCommand -eq "py -3") {
    & py -3 @Arguments 2>&1 | Tee-Object -FilePath $runLog -Append
  } else {
    & $script:PythonCommand @Arguments 2>&1 | Tee-Object -FilePath $runLog -Append
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Python komutu başarısız oldu: $($Arguments -join ' ')"
  }
}

function Test-PythonModule {
  param([string]$ModuleName)
  try {
    Invoke-Python @("-c", "import $ModuleName")
    return $true
  } catch {
    return $false
  }
}

try {
  Write-RunLog "Yerel DETSİS güncelleme başladı."
  $script:PythonCommand = Find-Python
  Write-RunLog "Python: $script:PythonCommand"

  if (-not $SkipInstall -and ((-not (Test-PythonModule "playwright")) -or (-not (Test-PythonModule "pypdf")))) {
    Write-RunLog "Python bağımlılıkları eksik, requirements kuruluyor."
    Invoke-Python @("-m", "pip", "install", "-r", "requirements.txt")
    Invoke-Python @("-m", "playwright", "install", "chromium")
  } else {
    Write-RunLog "Python bağımlılıkları hazır."
  }

  git pull --rebase --autostash origin main

  $env:DETSIS_FAIL_ON_LIVE_FAILURE = "1"
  Invoke-PythonLogged @("scraper.py")
  Remove-Item Env:\DETSIS_FAIL_ON_LIVE_FAILURE -ErrorAction SilentlyContinue

  git add -- data/mevzuatlar.json data/mevzuatlar.csv
  if (Test-Path "logs/hata_log.csv") { git add -- logs/hata_log.csv }
  if (Test-Path "logs/new_records.log") { git add -- logs/new_records.log }
  if (Test-Path "logs/son_degisim_raporu.json") { git add -- logs/son_degisim_raporu.json }
  $archives = Get-ChildItem -Path "data/archive" -Filter "*.json" -ErrorAction SilentlyContinue
  if ($archives) { git add -- data/archive/*.json }

  $changes = git diff --cached --name-only
  if (-not $changes) {
    Write-RunLog "Veri değişikliği yok, commit atılmadı."
    exit 0
  }

  git commit -m "data: DETSIS mevzuat verisini yerel güncelle"
  git push origin HEAD:main
  Write-RunLog "Yerel DETSİS güncelleme başarıyla tamamlandı."
} catch {
  Write-RunLog "HATA: $($_.Exception.Message)"
  exit 1
}
