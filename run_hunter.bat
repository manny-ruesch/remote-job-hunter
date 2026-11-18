@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo [%date% %time%] Launching Remote Job Hunter run... >> hunter.log 2>&1
if exist "%USERPROFILE%\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" (
    "%USERPROFILE%\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" -u -m rjh --home "%~dp0." scan >> hunter.log 2>&1
) else (
    python -u -m rjh --home "%~dp0." scan >> hunter.log 2>&1
)
echo [%date% %time%] Run finished. >> hunter.log 2>&1
