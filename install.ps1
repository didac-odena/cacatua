# Whisper dictation - installer
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"

Write-Host "== Whisper dictation installer =="

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python not found. Install Python 3.11+ from https://www.python.org/ and re-run."
    exit 1
}

if (-not (Test-Path $venv)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venv
}

$venvPython = Join-Path $venv "Scripts\python.exe"

Write-Host "Installing base requirements..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")

$hasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($hasNvidia) {
    Write-Host "NVIDIA GPU detected - installing CUDA libraries..."
    & $venvPython -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

    $ct2 = Join-Path $venv "Lib\site-packages\ctranslate2"
    $nvidia = Join-Path $venv "Lib\site-packages\nvidia"
    if ((Test-Path $nvidia) -and (Test-Path $ct2)) {
        Get-ChildItem -Path $nvidia -Recurse -Filter "*.dll" | ForEach-Object {
            Copy-Item $_.FullName -Destination $ct2 -Force
        }
        Write-Host "CUDA DLLs copied into ctranslate2."
    }
} else {
    Write-Host "No NVIDIA GPU detected - will run on CPU (slower transcription)."
}

# Remove old startup shortcut if present
$startup = [Environment]::GetFolderPath("Startup")
$oldLnk = Join-Path $startup "Whisper.lnk"
if (Test-Path $oldLnk) {
    Remove-Item $oldLnk -Force
    Write-Host "Removed old startup shortcut."
}

# Register scheduled task with elevated privileges (no UAC prompt at login)
$taskName = "WhisperDictation"
$vbs = Join-Path $root "launch.vbs"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 0
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Write-Host "Scheduled task '$taskName' registered (runs elevated at login, no UAC prompt)."

Write-Host ""
Write-Host "Install complete."
Write-Host "First run will download the Whisper model (~500 MB for small, ~1.5 GB for medium)."
Write-Host "Start now with: wscript.exe `"$vbs`""
Write-Host "Or reboot and it will start at login."
