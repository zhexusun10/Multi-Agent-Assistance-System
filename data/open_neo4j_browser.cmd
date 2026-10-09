@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_knowledge_graph.ps1" -NoAuth -SkipImport
if errorlevel 1 pause
