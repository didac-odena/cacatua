# restart.ps1 — Reinicio robusto de Whisper. Lo invoca restart.bat.
#
# Por qué existe: el restart.bat original usaba taskkill, que aquí podía PERDER la
# carrera contra el watchdog de launch.vbs (wscript) — el watchdog respawnea los
# pythonw más rápido que la verificación, así que el bat los veía "vivos" y abortaba
# sin relanzar. Esto usa Stop-Process -Force (que sí mata donde taskkill fallaba),
# mata el WATCHDOG PRIMERO (para que no respawnee mientras limpiamos), y reintenta
# en bucle hasta dejar todo muerto antes de relanzar.
#
# Nota: mata el/los wscript que ejecutan launch.vbs (si la cmdline no es legible,
# cae a "todos los wscript", como el bat original) y TODOS los pythonw (igual que el
# original — whisper es el único pythonw del setup).

$ErrorActionPreference = 'SilentlyContinue'
$dir = $PSScriptRoot

function Get-Watchdogs {
    $w = Get-CimInstance Win32_Process -Filter "Name='wscript.exe'"
    $m = $w | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'launch\.vbs') }
    if ($m) { return @($m.ProcessId) } else { return @($w.ProcessId) }  # fallback: todos los wscript
}

Write-Host 'Reiniciando Whisper (robusto)...'

$clean = $false
for ($i = 0; $i -lt 8; $i++) {
    foreach ($id in Get-Watchdogs) { Stop-Process -Id $id -Force }   # watchdog PRIMERO
    Start-Sleep -Milliseconds 250
    Get-Process pythonw | Stop-Process -Force                        # workers de whisper
    Start-Sleep -Milliseconds 500
    if ((Get-Watchdogs).Count -eq 0 -and -not (Get-Process pythonw)) { $clean = $true; break }
}

if (-not $clean) {
    Write-Host 'WARNING: wscript/pythonw siguen vivos tras 8 intentos. Matalos desde el Administrador de tareas y reintenta.'
    exit 1
}

Remove-Item (Join-Path $dir 'whisper.pid') -Force

# Relanzar via el watchdog (launch.vbs reinicia si crashea).
Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + (Join-Path $dir 'launch.vbs') + '"')
Write-Host 'Lanzado. Espera ~10s a que cargue el modelo (medium).'
