@echo off
cd /d "%~dp0"
uv run python -m motoduel
if errorlevel 1 pause
