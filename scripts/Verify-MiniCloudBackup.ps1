param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Fa-f0-9]{64}$")]
    [string]$ExpectedSha256,
    [string]$PostgresImage = "postgres:16.15-alpine"
)

$ErrorActionPreference = "Stop"
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$scriptPath = Join-Path $PSScriptRoot "verify-minicloud-backup.sh"
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "The isolated backup verification script is missing."
}

$actualSha256 = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash
if ($actualSha256 -ne $ExpectedSha256.ToUpperInvariant()) {
    throw "Backup checksum does not match the expected SHA-256; no restore was attempted."
}

$imageStatus = & docker image inspect $PostgresImage --format "{{.Id}}" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $imageStatus) {
    throw "The required PostgreSQL image is not available locally; no image pull was attempted."
}

$arguments = @(
    "run", "--rm",
    "--network", "none",
    "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=1g",
    "--mount", "type=bind,source=$resolvedBackup,target=/backup.dump,readonly",
    "--mount", "type=bind,source=$scriptPath,target=/verify-backup.sh,readonly",
    "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
    "--entrypoint", "sh",
    $PostgresImage,
    "-c",
    "tr -d '\015' < /verify-backup.sh | sh"
)

$restoreTimer = [System.Diagnostics.Stopwatch]::StartNew()
& docker @arguments
$restoreExitCode = $LASTEXITCODE
$restoreTimer.Stop()
if ($restoreExitCode -ne 0) {
    throw "Isolated backup restore failed with exit code $restoreExitCode."
}

$finalSha256 = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash
if ($finalSha256 -ne $actualSha256) {
    throw "Backup checksum changed while it was mounted read-only; investigate concurrent modification."
}

Write-Output "restore_duration_seconds=$([Math]::Round($restoreTimer.Elapsed.TotalSeconds, 2))"
Write-Output "backup_sha256=$finalSha256"
Write-Output "isolation=network-none,tmpfs-only,backup-read-only,no-host-ports"
