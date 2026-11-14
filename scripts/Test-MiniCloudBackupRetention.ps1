$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$retentionScript = Join-Path $PSScriptRoot "Invoke-MiniCloudBackupRetention.ps1"
$backupScript = Join-Path $PSScriptRoot "New-MiniCloudBackup.ps1"
$postgresImage = "postgres:16.15-alpine"
$containerName = "minicloud-retention-test-pg-" + [guid]::NewGuid().ToString("N")
$fixture = Join-Path ([System.IO.Path]::GetTempPath()) ("minicloud-retention-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $fixture | Out-Null
$containerCreated = $false

try {
    $imageStatus = & docker image inspect $postgresImage --format "{{.Id}}" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $imageStatus) {
        throw "The required PostgreSQL image is not available locally; no image pull was attempted."
    }
    $null = & docker run -d `
        --name $containerName `
        --network none `
        --tmpfs "/var/lib/postgresql/data:rw,noexec,nosuid,size=1g" `
        --env "POSTGRES_DB=minicloud" `
        --env "POSTGRES_USER=postgres" `
        --env "POSTGRES_HOST_AUTH_METHOD=trust" `
        $postgresImage
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to start the disposable retention-test PostgreSQL container."
    }
    $containerCreated = $true

    $readySamples = 0
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker exec $containerName pg_isready -U postgres -d minicloud *> $null
        if ($LASTEXITCODE -eq 0) {
            $readySamples++
            if ($readySamples -ge 3) {
                break
            }
        } else {
            $readySamples = 0
        }
        Start-Sleep -Seconds 1
    }
    if ($readySamples -lt 3) {
        throw "The disposable retention-test PostgreSQL server did not become ready."
    }

    & docker exec $containerName psql -X -v ON_ERROR_STOP=1 -U postgres -d minicloud -c `
        "CREATE TABLE alembic_version (version_num varchar(32) NOT NULL); INSERT INTO alembic_version (version_num) VALUES ('synthetic-fixture');"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to add a synthetic schema revision to the disposable database."
    }

    $backupOutput = Join-Path $fixture "created-backups"
    New-Item -ItemType Directory -Path $backupOutput | Out-Null
    $createdBackupOutput = @(
        & $backupScript -BackupDirectory $backupOutput -ContainerName $containerName
    )
    $createdBackupLine = $createdBackupOutput |
        Where-Object { $_ -like "backup_path=*" } |
        Select-Object -First 1
    if (-not $createdBackupLine) {
        throw "Backup creation did not report its archive path."
    }
    $createdBackup = $createdBackupLine.Substring("backup_path=".Length)
    $createdMetadata = Get-Content -LiteralPath "$createdBackup.metadata.json" -Raw |
        ConvertFrom-Json
    $createdFile = Get-Item -LiteralPath $createdBackup
    $createdHash = (Get-FileHash -LiteralPath $createdBackup -Algorithm SHA256).Hash
    if (
        $createdFile.Length -le 0 -or
        $createdMetadata.file_name -ne $createdFile.Name -or
        [long]$createdMetadata.size_bytes -ne $createdFile.Length -or
        $createdMetadata.sha256 -ne $createdHash -or
        $createdMetadata.schema_revision -ne "synthetic-fixture" -or
        $createdMetadata.archive_integrity_check -ne "pg_restore --list"
    ) {
        throw "Backup creation metadata does not match the synthetic archive."
    }
    & docker exec $containerName sh -c "test ! -e /tmp/$($createdFile.Name)"
    if ($LASTEXITCODE -ne 0) {
        throw "Backup creation did not remove its named temporary database-container dump."
    }
    Write-Output "Synthetic backup creation and archive-integrity metadata passed."

    $containerArchive = "/tmp/minicloud-retention-fixture.dump"
    & docker exec $containerName pg_dump -U postgres -d minicloud --format=custom --no-owner -f $containerArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create the synthetic PostgreSQL archive fixture."
    }
    $validArchive = Join-Path $fixture "valid-template.dump"
    & docker cp "${containerName}:$containerArchive" $validArchive | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to copy the synthetic PostgreSQL archive fixture."
    }

    $baseDate = [DateTime]::UtcNow.Date
    for ($day = 0; $day -lt 42; $day++) {
        $archivePath = Join-Path $fixture ("minicloud-fixture-{0:D2}.dump" -f $day)
        Copy-Item -LiteralPath $validArchive -Destination $archivePath
        $archive = Get-Item -LiteralPath $archivePath
        $sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
        $metadata = [ordered]@{
            file_name = $archive.Name
            size_bytes = $archive.Length
            sha256 = $sha256
            format = "postgres-custom"
            archive_integrity_check = "pg_restore --list"
            schema_revision = "synthetic-fixture"
        }
        $metadata | ConvertTo-Json | Set-Content -LiteralPath "$archivePath.metadata.json" -Encoding utf8
        [System.IO.File]::SetLastWriteTimeUtc($archivePath, $baseDate.AddDays(-$day).AddHours(10))
    }
    Remove-Item -LiteralPath $validArchive -Force

    $corruptArchive = Join-Path $fixture "minicloud-corrupt.dump"
    [System.IO.File]::WriteAllText($corruptArchive, "incomplete archive fixture")
    $corruptFile = Get-Item -LiteralPath $corruptArchive
    $corruptMetadata = [ordered]@{
        file_name = $corruptFile.Name
        size_bytes = $corruptFile.Length
        sha256 = (Get-FileHash -LiteralPath $corruptArchive -Algorithm SHA256).Hash
        format = "postgres-custom"
        archive_integrity_check = "pg_restore --list"
        schema_revision = "synthetic-fixture"
    }
    $corruptMetadata | ConvertTo-Json | Set-Content -LiteralPath "$corruptArchive.metadata.json" -Encoding utf8
    $verifierScript = Join-Path $PSScriptRoot "Verify-MiniCloudBackup.ps1"
    $checksumMismatchRejected = $false
    try {
        & $verifierScript -BackupPath $corruptArchive -ExpectedSha256 $createdHash | Out-Null
    } catch {
        $checksumMismatchRejected = $_.Exception.Message -match "checksum does not match"
    }
    if (-not $checksumMismatchRejected) {
        throw "The isolated restore verifier did not reject a checksum mismatch before restore."
    }

    $incompleteRestoreRejected = $false
    try {
        & $verifierScript `
            -BackupPath $corruptArchive `
            -ExpectedSha256 $corruptMetadata.sha256 `
            -PostgresImage $postgresImage | Out-Null
    } catch {
        $incompleteRestoreRejected = $_.Exception.Message -match "Isolated backup restore failed"
    }
    if (-not $incompleteRestoreRejected) {
        throw "The isolated restore verifier accepted an incomplete archive with a matching checksum."
    }

    $rejectedCorruptArchive = $false
    try {
        & $retentionScript -BackupDirectory $fixture | Out-Null
    } catch {
        $rejectedCorruptArchive = $true
    }
    if (-not $rejectedCorruptArchive) {
        throw "Retention did not reject a checksum-matching incomplete PostgreSQL archive."
    }
    if (@(Get-ChildItem -LiteralPath $fixture -File).Count -ne 86) {
        throw "Corrupt-archive rejection modified or lost fixture files."
    }
    Remove-Item -LiteralPath $corruptArchive,"$corruptArchive.metadata.json" -Force

    $dryRun = @(& $retentionScript -BackupDirectory $fixture)
    if ($dryRun[0] -notmatch "^mode=DRY-RUN") {
        throw "Retention did not report a successful dry run."
    }
    if (@(Get-ChildItem -LiteralPath $fixture -File).Count -ne 84) {
        throw "Retention dry run changed the fixture."
    }

    $applied = @(& $retentionScript -BackupDirectory $fixture -Apply)
    if ($applied[0] -notmatch "^mode=APPLY") {
        throw "Retention did not report a successful apply."
    }
    $archives = @(Get-ChildItem -LiteralPath $fixture -Filter "*.dump" -File)
    $metadata = @(Get-ChildItem -LiteralPath $fixture -Filter "*.dump.metadata.json" -File)
    if ($archives.Count -lt 9 -or $archives.Count -gt 10 -or $metadata.Count -ne $archives.Count) {
        throw "Retention did not preserve the expected daily and weekly archive/metadata set."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $fixture "minicloud-fixture-00.dump.metadata.json"))) {
        throw "Retention removed metadata for the newest backup."
    }
    if (Test-Path -LiteralPath (Join-Path $fixture "minicloud-fixture-41.dump.metadata.json")) {
        throw "Retention did not remove old archive metadata with its archive."
    }

    $retentionPathRejected = $false
    try {
        & $retentionScript -BackupDirectory $repositoryRoot -Apply | Out-Null
    } catch {
        $retentionPathRejected = $true
    }
    if (-not $retentionPathRejected) {
        throw "The retention script accepted the repository as its target."
    }

    $backupPathRejected = $false
    try {
        & $backupScript -BackupDirectory $repositoryRoot | Out-Null
    } catch {
        $backupPathRejected = $true
    }
    if (-not $backupPathRejected) {
        throw "The backup script accepted the repository as its target."
    }

    Write-Output "Backup retention fixture tests passed: corrupt archive rejected; $($archives.Count) valid archives and metadata sidecars retained."
} finally {
    if ($containerCreated) {
        & docker rm -f $containerName *> $null
    }
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
