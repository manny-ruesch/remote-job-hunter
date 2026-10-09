@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d ".\dashboard"
set PYTHON_EXE=python
"%PYTHON_EXE%" dashboard_server.py
