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

if (-not $env:JWT_SECRET -or $env:JWT_SECRET.Length -lt 32) {
    $secretBytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($secretBytes)
    }
    finally {
        $rng.Dispose()
    }
    $env:JWT_SECRET = [Convert]::ToBase64String($secretBytes)
}

$allowedDiscordSettings = @(
    "DISCORD_CLIENT_ID",
    "DISCORD_CLIENT_SECRET",
    "DISCORD_PUBLIC_KEY",
    "DISCORD_REDIRECT_URI",
    "DISCORD_GUILD_ID",
    "DISCORD_BOT_TOKEN",
    "DISCORD_VERIFIED_ROLE_ID",
    "SCIP_MODERATOR_DISCORD_IDS"
)
$envPath = Join-Path $root ".env"
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=(.*)$') {
            $name = $Matches[1]
            if ($name -in $allowedDiscordSettings) {
                $value = $Matches[2].Trim().Trim('"').Trim("'")
                Set-Item -Path "Env:\$name" -Value $value
            }
        }
    }
}

python -m alembic -c services/api/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed with exit code $LASTEXITCODE."
}

Write-Host "SCIP dashboard/API starting at http://127.0.0.1:8080"
Write-Host "Press Ctrl+C to stop. Discord interactions still need a public HTTPS endpoint."
python -m uvicorn scip_api.main:app --host 127.0.0.1 --port 8080
if ($LASTEXITCODE -ne 0) {
    throw "The SCIP API stopped with exit code $LASTEXITCODE."
}
