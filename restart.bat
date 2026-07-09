@echo off
REM La logica robusta de reinicio vive en restart.ps1 (Stop-Process -Force +
REM mata el watchdog primero + bucle de reintento). Antes esto usaba taskkill,
REM que perdia la carrera contra el watchdog de launch.vbs y abortaba sin relanzar.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart.ps1"
