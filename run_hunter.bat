@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\Manny\remote_job_hunter"
echo [%date% %time%] Launching Remote Job Hunter run... >> hunter.log 2>&1
"C:\Users\Manny\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" -u -m rjh --home "C:\Users\Manny\remote_job_hunter" scan >> hunter.log 2>&1
echo [%date% %time%] Run finished. >> hunter.log 2>&1
