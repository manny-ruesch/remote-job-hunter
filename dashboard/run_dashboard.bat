@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\Manny\remote_job_hunter\dashboard"
set PYTHON_EXE=C:\Users\Manny\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
"%PYTHON_EXE%" dashboard_server.py
