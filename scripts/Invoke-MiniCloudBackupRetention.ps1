[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDirectory,
    [ValidateRange(1, 3650)]
    [int]$KeepDaily = 7,
    [ValidateRange(1, 520)]
    [int]$KeepWeekly = 4,
    [string]$PostgresImage = "postgres:16.15-alpine",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$resolvedDirectory = (Resolve-Path -LiteralPath $BackupDirectory).Path
$directory = Get-Item -LiteralPath $resolvedDirectory
if (-not $directory.PSIsContainer) {
    throw "BackupDirectory must be an existing directory."
}
if ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw "BackupDirectory must not be a reparse point."
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

$backups = @(
    Get-ChildItem -LiteralPath $targetPath -File -Filter "minicloud-*.dump" |
        Sort-Object `
            @{ Expression = "LastWriteTimeUtc"; Descending = $true },
            @{ Expression = "Name"; Descending = $true }
)
$verifiedBackups = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
$legacyBackups = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
foreach ($backup in $backups) {
    if ($backup.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "A matching backup is a reparse point; refusing to process the retention set."
    }
    $metadataPath = "$($backup.FullName).metadata.json"
    if (-not (Test-Path -LiteralPath $metadataPath)) {
        $legacyBackups.Add($backup)
        continue
    }
    $metadataFile = Get-Item -LiteralPath $metadataPath
    if ($metadataFile.PSIsContainer -or ($metadataFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "A matching backup metadata sidecar is not a regular file; refusing to process the retention set."
    }
    try {
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    } catch {
        throw "Backup metadata is malformed for '$($backup.Name)'; refusing to prune any files."
    }
    if (
        $metadata.file_name -ne $backup.Name -or
        [long]$metadata.size_bytes -ne $backup.Length -or
        $metadata.format -ne "postgres-custom" -or
        $metadata.sha256 -notmatch "^[A-Fa-f0-9]{64}$"
    ) {
        throw "Backup metadata does not match '$($backup.Name)'; refusing to prune any files."
    }
    $actualSha256 = (Get-FileHash -LiteralPath $backup.FullName -Algorithm SHA256).Hash
    if ($actualSha256 -ne $metadata.sha256.ToUpperInvariant()) {
        throw "Backup checksum does not match its metadata for '$($backup.Name)'; refusing to prune any files."
    }
    $verifiedBackups.Add($backup)
}

if ($backups.Count -gt 0) {
    $imageStatus = & docker image inspect $PostgresImage --format "{{.Id}}" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $imageStatus) {
        throw "The required PostgreSQL image is not available locally; no image pull was attempted."
    }
    $validationScript = 'for archive in /backups/minicloud-*.dump; do [ -f "$archive" ] || continue; pg_restore --list "$archive" >/dev/null || exit 1; done'
    & docker run --rm --network none `
        --mount "type=bind,source=$targetPath,target=/backups,readonly" `
        --entrypoint sh $PostgresImage -c $validationScript | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "At least one MiniCloud archive is incomplete or corrupt; no retention files were removed."
    }
}

$retainedPaths = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$dailyGroups = @(
    $verifiedBackups |
        Group-Object { $_.LastWriteTimeUtc.ToString("yyyy-MM-dd") } |
        Select-Object -First $KeepDaily
)
foreach ($group in $dailyGroups) {
    [void]$retainedPaths.Add($group.Group[0].FullName)
}

$weeklyGroups = @(
    $verifiedBackups |
        Group-Object {
            $date = $_.LastWriteTimeUtc.Date
            $mondayOffset = (([int]$date.DayOfWeek + 6) % 7)
            $date.AddDays(-$mondayOffset).ToString("yyyy-MM-dd")
        } |
        Select-Object -First $KeepWeekly
)
foreach ($group in $weeklyGroups) {
    [void]$retainedPaths.Add($group.Group[0].FullName)
}
foreach ($backup in $legacyBackups) {
    [void]$retainedPaths.Add($backup.FullName)
}

$deleteCandidates = @(
    $backups | Where-Object { -not $retainedPaths.Contains($_.FullName) }
)
$mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }
Write-Output "mode=$mode target=$targetPath matching_backups=$($backups.Count) verified=$($verifiedBackups.Count) protected_legacy=$($legacyBackups.Count) retained=$($retainedPaths.Count) candidates=$($deleteCandidates.Count)"
foreach ($candidate in $deleteCandidates) {
    if (-not $Apply) {
        Write-Output "would_delete=$($candidate.Name)"
        if (Test-Path -LiteralPath "$($candidate.FullName).metadata.json") {
            Write-Output "would_delete=$($candidate.Name).metadata.json"
        }
        continue
    }

    $current = Get-Item -LiteralPath $candidate.FullName
    if (
        $current.PSIsContainer -or
        ($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        $current.Length -ne $candidate.Length -or
        $current.LastWriteTimeUtc -ne $candidate.LastWriteTimeUtc
    ) {
        throw "Backup changed after retention planning; stopping before deleting '$($candidate.Name)'."
    }
    Remove-Item -LiteralPath $candidate.FullName -Force
    Write-Output "deleted=$($candidate.Name)"
    $metadataPath = "$($candidate.FullName).metadata.json"
    if (Test-Path -LiteralPath $metadataPath) {
        Remove-Item -LiteralPath $metadataPath -Force
        Write-Output "deleted=$($candidate.Name).metadata.json"
    }
}
