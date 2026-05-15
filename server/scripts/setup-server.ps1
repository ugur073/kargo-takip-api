param(
    [switch]$SkipCloudflared
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ServerRoot ".venv"
$ToolsPath = Join-Path $ServerRoot ".tools"
$CloudflaredPath = Join-Path $ToolsPath "cloudflared.exe"

Write-Host "[INFO] Preparing Python virtual environment..."
if (-not (Test-Path $VenvPath)) {
    python -m venv $VenvPath
}

$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r (Join-Path $ServerRoot "requirements.txt")

$EnvPath = Join-Path $ServerRoot ".env"
$ExamplePath = Join-Path $ServerRoot ".env.example"
if (-not (Test-Path $EnvPath)) {
    Copy-Item $ExamplePath $EnvPath
    Write-Host "[OK] Created .env from .env.example. Update tokens before production use."
} else {
    Write-Host "[OK] .env already exists."
}

if (-not $SkipCloudflared) {
    New-Item -ItemType Directory -Force -Path $ToolsPath | Out-Null
    if (-not (Test-Path $CloudflaredPath)) {
        Write-Host "[INFO] Downloading cloudflared quick tunnel client..."
        Invoke-WebRequest `
            -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
            -OutFile $CloudflaredPath
    } else {
        Write-Host "[OK] cloudflared already exists."
    }

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($UserPath -split ";") -notcontains $ToolsPath) {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$ToolsPath", "User")
        $env:Path = "$env:Path;$ToolsPath"
        Write-Host "[OK] Added server\.tools to user PATH."
    } else {
        Write-Host "[OK] server\.tools is already in user PATH."
    }
}

Write-Host "[DONE] Backend setup completed."
