@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "."
echo [%date% %time%] Launching Remote Job Hunter run... >> hunter.log 2>&1
"python" -u run_hunter.py >> hunter.log 2>&1
echo [%date% %time%] Run finished. >> hunter.log 2>&1
