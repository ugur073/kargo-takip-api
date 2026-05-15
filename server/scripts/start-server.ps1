$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ServerRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment not found. Run server\scripts\setup-server.ps1 first."
}

Set-Location $ServerRoot
& $PythonExe -m uvicorn app:app --host 127.0.0.1 --port 8787 --reload
