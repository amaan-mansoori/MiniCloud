param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDirectory,
    [string]$ContainerName = "minicloud-postgres",
    [string]$Database = "minicloud",
    [string]$DatabaseRole = "postgres"
)

$ErrorActionPreference = "Stop"

foreach ($identifier in @($ContainerName, $Database, $DatabaseRole)) {
    if ($identifier -notmatch "^[A-Za-z0-9_.-]+$") {
        throw "Container, database, and role identifiers may contain only letters, digits, underscore, dot, and hyphen."
    }
}

$resolvedDirectory = (Resolve-Path -LiteralPath $BackupDirectory).Path
$directory = Get-Item -LiteralPath $resolvedDirectory
if (-not $directory.PSIsContainer -or ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw "BackupDirectory must be an existing directory and must not be a reparse point."
}

$repoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd("\")
$repoPrefix = $repoRoot + "\"
$targetPath = [System.IO.Path]::GetFullPath($resolvedDirectory).TrimEnd("\")
if (
    $targetPath -eq [System.IO.Path]::GetPathRoot($targetPath).TrimEnd("\") -or
    $targetPath.Equals($repoRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $targetPath.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "BackupDirectory must be a dedicated directory outside the repository and filesystem root."
}

function Invoke-DockerChecked {
    param([string[]]$Arguments)

    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: docker $($Arguments -join ' ')`n$output"
    }
    return $output
}

$timestamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
$archiveName = "minicloud-$timestamp.dump"
$backupPath = Join-Path $targetPath $archiveName
$metadataPath = "$backupPath.metadata.json"
$containerDump = "/tmp/$archiveName"
if ((Test-Path -LiteralPath $backupPath) -or (Test-Path -LiteralPath $metadataPath)) {
    throw "The timestamped backup or metadata path already exists; refusing to overwrite it."
}

$dumpStarted = $false
try {
    Invoke-DockerChecked -Arguments @("exec", $ContainerName, "pg_isready", "-U", $DatabaseRole, "-d", $Database) | Out-Null
    $revision = Invoke-DockerChecked -Arguments @(
        "exec", $ContainerName, "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-U", $DatabaseRole, "-d", $Database, "-c",
        "SELECT version_num FROM alembic_version"
    )
    $revision = ($revision -join "`n").Trim()
    if ($revision -notmatch "^[A-Za-z0-9_.-]+$") {
        throw "Unable to read a valid Alembic schema revision; no backup was created."
    }
    $serverVersion = Invoke-DockerChecked -Arguments @(
        "exec", $ContainerName, "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-U", $DatabaseRole, "-d", $Database, "-c",
        "SELECT current_setting('server_version')"
    )
    $serverVersion = ($serverVersion -join "`n").Trim()

    $dumpStarted = $true
    Invoke-DockerChecked -Arguments @(
        "exec", $ContainerName, "pg_dump", "-U", $DatabaseRole, "-d", $Database,
        "--format=custom", "--no-owner", "-f", $containerDump
    ) | Out-Null
    Invoke-DockerChecked -Arguments @(
        "exec", $ContainerName, "pg_restore", "--list", $containerDump
    ) | Out-Null
    Invoke-DockerChecked -Arguments @("cp", "${ContainerName}:$containerDump", $backupPath) | Out-Null
    $backup = Get-Item -LiteralPath $backupPath
    if ($backup.Length -le 0) {
        throw "The created PostgreSQL archive is empty."
    }
    $sha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash
    $metadata = [ordered]@{
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        file_name = $archiveName
        size_bytes = $backup.Length
        sha256 = $sha256
        format = "postgres-custom"
        archive_integrity_check = "pg_restore --list"
        schema_revision = $revision
        postgres_server_version = $serverVersion
        database = $Database
    }
    $metadataJson = $metadata | ConvertTo-Json -Depth 3
    $temporaryMetadata = Join-Path $targetPath (".$archiveName.metadata-$([guid]::NewGuid().ToString('N')).tmp")
    try {
        [System.IO.File]::WriteAllText(
            $temporaryMetadata,
            $metadataJson,
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Move($temporaryMetadata, $metadataPath)
    } finally {
        if (Test-Path -LiteralPath $temporaryMetadata) {
            Remove-Item -LiteralPath $temporaryMetadata -Force
        }
    }

    Write-Output "backup_path=$backupPath"
    Write-Output "metadata_path=$metadataPath"
    Write-Output "backup_size_bytes=$($backup.Length)"
    Write-Output "backup_sha256=$sha256"
    Write-Output "schema_revision=$revision"
    Write-Output "format=postgres-custom"
} finally {
    if ($dumpStarted) {
        Invoke-DockerChecked -Arguments @("exec", $ContainerName, "rm", "-f", $containerDump) | Out-Null
    }
}
