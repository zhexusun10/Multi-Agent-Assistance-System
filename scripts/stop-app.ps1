$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'process_control.py')
if($LASTEXITCODE -ne 0){throw 'Application process shutdown failed'}
