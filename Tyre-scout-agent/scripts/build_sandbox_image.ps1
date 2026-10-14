# Build the sandbox image (run once, and after any dependency change).
# Requires Docker Desktop on Windows. No administrator rights needed at RUN time.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
docker build -t tire-scout-sandbox:py312 -f "$PSScriptRoot\Dockerfile.sandbox" "$PSScriptRoot"
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
docker image inspect tire-scout-sandbox:py312 | Out-Null
Write-Host "Sandbox image tire-scout-sandbox:py312 ready."
