$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

if (Test-Path -LiteralPath (Join-Path $root ".env")) {
    foreach ($line in Get-Content -LiteralPath (Join-Path $root ".env")) {
        if ($line -match '^\s*(SC_TRUCKERS_API_BASE_URL|SC_TRUCKERS_API_TOKEN|TESSERACT_CMD)\s*=\s*(.*)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

$env:APP_ENV = "development"
$env:DATABASE_URL = "sqlite:///./scip-v2.db"
$env:PYTHONPATH = @(
    (Join-Path $root "services\ocr"),
    (Join-Path $root "services\api"),
    (Join-Path $root "scanner-engine"),
    $env:PYTHONPATH
) -ne "" -join ";"
$env:SCIP_OCR_SERVICE_URL = "http://127.0.0.1:8001"

python -m alembic -c services/api/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed with exit code $LASTEXITCODE."
}

$python = (Get-Command python).Source
$ocrStdout = Join-Path $env:TEMP "scip-ocr-service.log"
$ocrStderr = Join-Path $env:TEMP "scip-ocr-service-error.log"
$ocrProcess = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "scip_ocr.main:app", "--host", "127.0.0.1", "--port", "8001") `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $ocrStdout `
    -RedirectStandardError $ocrStderr
Start-Sleep -Seconds 2
$ocrProcess.Refresh()
if ($ocrProcess.HasExited) {
    if (Test-Path -LiteralPath $ocrStderr) {
        Get-Content -LiteralPath $ocrStderr -Tail 20
    }
    throw "The SCIP OCR service failed to start. Install services\ocr\requirements.txt and retry."
}
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    if (-not $env:TESSERACT_CMD -or -not (Test-Path -LiteralPath $env:TESSERACT_CMD)) {
        Write-Warning "Tesseract was not found on PATH. Set TESSERACT_CMD to its executable; OCR requests otherwise cannot run."
    }
}

Write-Host "SCIP API starting at http://127.0.0.1:8080"
Write-Host "SCIP OCR service starting at http://127.0.0.1:8001"
Write-Host "Press Ctrl+C to stop the local API."
try {
    python -m uvicorn scip_api.main:app --host 127.0.0.1 --port 8080
    if ($LASTEXITCODE -ne 0) {
        throw "The SCIP API stopped with exit code $LASTEXITCODE."
    }
}
finally {
    $ocrProcess.Refresh()
    if (-not $ocrProcess.HasExited) {
        Stop-Process -Id $ocrProcess.Id
    }
}
