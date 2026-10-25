$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$env:APP_ENV = "development"
$env:DATABASE_URL = "sqlite:///./scip-v2.db"
$env:PYTHONPATH = @(
    (Join-Path $root "services\api"),
    (Join-Path $root "scanner-engine"),
    $env:PYTHONPATH
) -ne "" -join ";"

python -m alembic -c services/api/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed with exit code $LASTEXITCODE."
}

Write-Host "SCIP API starting at http://127.0.0.1:8080"
Write-Host "Press Ctrl+C to stop the local API."
python -m uvicorn scip_api.main:app --host 127.0.0.1 --port 8080
if ($LASTEXITCODE -ne 0) {
    throw "The SCIP API stopped with exit code $LASTEXITCODE."
}
