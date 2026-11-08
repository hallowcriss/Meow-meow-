@echo off
rem Double-click to start the Controller Replay recorder.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -c "import pygame" 2>nul || %PY% -m pip install pygame
%PY% recorder.py
pause
