$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
$CloudflaredPath = Join-Path $ServerRoot ".tools\cloudflared.exe"

if (-not (Test-Path $CloudflaredPath)) {
    throw "cloudflared.exe not found. Run server\scripts\setup-server.ps1 first."
}

& $CloudflaredPath tunnel --url http://127.0.0.1:8787
