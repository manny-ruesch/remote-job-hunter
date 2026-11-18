@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo ===================================================
echo [Remote Job Hunter] Lancement du Command Center...
echo ===================================================

where rjh >nul 2>nul
if %ERRORLEVEL% equ 0 (
    rjh serve
    goto :end
)

where uvx >nul 2>nul
if %ERRORLEVEL% equ 0 (
    uvx --from . rjh serve
    goto :end
)

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python -m rjh.cli serve
    goto :end
)

where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -m rjh.cli serve
    goto :end
)

echo [ERREUR] Ni rjh, ni uvx, ni python ne sont disponibles dans le PATH.
echo Veuillez installer Python 3.11+ ou lancer la commande: pipx install .
pause

:end
endlocal
