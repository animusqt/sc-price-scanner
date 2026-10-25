param(
    [Parameter(Mandatory = $true)]
    [string]$EncryptionKey,
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
if ($EncryptionKey.Length -lt 32) {
    throw "EncryptionKey must contain at least 32 characters."
}

$compose = Get-Command docker -ErrorAction SilentlyContinue
$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $compose -or -not $openssl) {
    throw "Docker and OpenSSL are required for encrypted PostgreSQL backups."
}

function Get-ComposeSetting([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value -and (Test-Path -LiteralPath ".env")) {
        $line = Get-Content -LiteralPath ".env" |
            Where-Object { $_ -match "^\s*$Name\s*=" } |
            Select-Object -First 1
        if ($line) {
            $value = ($line -replace "^\s*$Name\s*=", "").Trim().Trim('"').Trim("'")
        }
    }
    if (-not $value -or $value -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "$Name must be set to a valid PostgreSQL identifier in .env or the process environment."
    }
    return $value
}

$postgresUser = Get-ComposeSetting "POSTGRES_USER"
$postgresDatabase = Get-ComposeSetting "POSTGRES_DB"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outputPath = Join-Path $OutputDirectory "scip-$timestamp.sql.enc"
$plainPath = Join-Path $env:TEMP "scip-$timestamp.sql"
$env:SCIP_BACKUP_KEY = $EncryptionKey
try {
    $command = 'docker compose exec -T db pg_dump -U {0} {1} > "{2}"' -f `
        $postgresUser, $postgresDatabase, $plainPath
    & $env:ComSpec /d /s /c $command
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $plainPath)) {
        throw "PostgreSQL dump failed."
    }
    & $openssl.Source enc -aes-256-cbc -pbkdf2 -salt `
        -pass env:SCIP_BACKUP_KEY -in $plainPath -out $outputPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) {
        throw "Encrypted database backup failed."
    }
    Get-FileHash -LiteralPath $outputPath -Algorithm SHA256
}
finally {
    Remove-Item -LiteralPath $plainPath -ErrorAction SilentlyContinue
    Remove-Item Env:\SCIP_BACKUP_KEY -ErrorAction SilentlyContinue
}
