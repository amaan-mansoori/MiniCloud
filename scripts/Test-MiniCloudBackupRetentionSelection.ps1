$ErrorActionPreference = "Stop"
Import-Module -Name (Join-Path $PSScriptRoot "MiniCloudBackupRetention.psm1") -Force

$fixture = Join-Path ([System.IO.Path]::GetTempPath()) (
    "minicloud-retention-selection-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $fixture | Out-Null

function ConvertTo-FixtureUtcDateTime {
    param([string]$Value)
    return [DateTimeOffset]::Parse(
        $Value,
        [System.Globalization.CultureInfo]::InvariantCulture
    ).UtcDateTime
}

function New-FixtureBackup {
    param(
        [string]$Name,
        [DateTime]$TimestampUtc
    )

    $path = Join-Path $fixture $Name
    [System.IO.File]::WriteAllText($path, $Name)
    [System.IO.File]::SetLastWriteTimeUtc($path, $TimestampUtc)
    $backup = Get-Item -LiteralPath $path
    if ($backup.LastWriteTimeUtc -ne $TimestampUtc) {
        throw "The fixture timestamp was not preserved for '$Name'."
    }
    return $backup
}

function Assert-RetentionSelection {
    param(
        [string]$CaseName,
        [System.IO.FileInfo[]]$Backups,
        [int]$KeepDaily,
        [int]$KeepWeekly,
        [string[]]$ExpectedNames
    )

    $selectedNames = @(
        Get-MiniCloudBackupRetentionSelection `
            -Backups $Backups `
            -KeepDaily $KeepDaily `
            -KeepWeekly $KeepWeekly |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
    $expectedSorted = @($ExpectedNames | Sort-Object)
    $differences = @(
        Compare-Object -ReferenceObject $expectedSorted -DifferenceObject $selectedNames
    )
    if ($differences.Count -gt 0) {
        throw "$CaseName selected '$($selectedNames -join ',')'; expected '$($expectedSorted -join ',')'."
    }
    Write-Output "PASS $CaseName"
}

function Get-FixtureSnapshot {
    param([string]$Path)

    @(
        Get-ChildItem -LiteralPath $Path -File -Recurse |
            Sort-Object -Property FullName |
            ForEach-Object {
                [pscustomobject]@{
                    FullName = $_.FullName
                    Length = $_.Length
                    Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                    LastWriteTimeUtc = $_.LastWriteTimeUtc
                }
            }
    )
}

try {
    $monthBoundary = @(
        (New-FixtureBackup "month-jan.dump" (ConvertTo-FixtureUtcDateTime "2025-01-31T23:59:00Z")),
        (New-FixtureBackup "month-feb.dump" (ConvertTo-FixtureUtcDateTime "2025-02-01T00:01:00Z"))
    )
    Assert-RetentionSelection "Month boundary" $monthBoundary 1 1 @("month-feb.dump")

    $yearBoundary = @(
        (New-FixtureBackup "year-dec.dump" (ConvertTo-FixtureUtcDateTime "2024-12-31T23:59:00Z")),
        (New-FixtureBackup "year-jan.dump" (ConvertTo-FixtureUtcDateTime "2025-01-01T00:01:00Z"))
    )
    Assert-RetentionSelection "December/January daily boundary" $yearBoundary 1 1 @("year-jan.dump")

    $leapBoundary = @(
        (New-FixtureBackup "leap-feb28.dump" (ConvertTo-FixtureUtcDateTime "2024-02-28T12:00:00Z")),
        (New-FixtureBackup "leap-feb29.dump" (ConvertTo-FixtureUtcDateTime "2024-02-29T12:00:00Z")),
        (New-FixtureBackup "leap-mar01.dump" (ConvertTo-FixtureUtcDateTime "2024-03-01T12:00:00Z"))
    )
    Assert-RetentionSelection "February 29 leap-day boundary" $leapBoundary 2 1 @(
        "leap-feb29.dump",
        "leap-mar01.dump"
    )

    $mondayBoundary = @(
        (New-FixtureBackup "week-sunday.dump" (ConvertTo-FixtureUtcDateTime "2025-01-05T23:00:00Z")),
        (New-FixtureBackup "week-monday.dump" (ConvertTo-FixtureUtcDateTime "2025-01-06T00:01:00Z"))
    )
    Assert-RetentionSelection "Monday-start week boundary" $mondayBoundary 1 1 @("week-monday.dump")

    $yearWeekBoundary = @(
        (New-FixtureBackup "week-year-sunday.dump" (ConvertTo-FixtureUtcDateTime "2024-12-29T23:00:00Z")),
        (New-FixtureBackup "week-year-monday.dump" (ConvertTo-FixtureUtcDateTime "2024-12-30T00:01:00Z"))
    )
    Assert-RetentionSelection "Monday-start week across year boundary" $yearWeekBoundary 1 1 @(
        "week-year-monday.dump"
    )

    $tieTimestamp = ConvertTo-FixtureUtcDateTime "2024-06-15T12:00:00Z"
    $tiedBackups = @(
        (New-FixtureBackup "tie-alpha.dump" $tieTimestamp),
        (New-FixtureBackup "tie-zeta.dump" $tieTimestamp)
    )
    Assert-RetentionSelection "Equal timestamps use descending filename" $tiedBackups 1 1 @("tie-zeta.dump")

    $offsetTimestamp = [DateTimeOffset]::Parse(
        "2024-03-01T01:30:00+02:00",
        [System.Globalization.CultureInfo]::InvariantCulture
    ).UtcDateTime
    if ((Get-MiniCloudBackupUtcDateKey $offsetTimestamp) -ne "2024-02-29") {
        throw "UTC date grouping did not normalize an offset timestamp."
    }
    if ((Get-MiniCloudBackupUtcWeekKey $offsetTimestamp) -ne "2024-02-26") {
        throw "UTC week grouping did not normalize the leap-day timestamp."
    }
    Write-Output "PASS UTC date/week grouping"

    $malformedFixture = Join-Path $fixture "malformed-metadata"
    New-Item -ItemType Directory -Path $malformedFixture | Out-Null
    $malformedArchive = Join-Path $malformedFixture "minicloud-malformed.dump"
    [System.IO.File]::WriteAllText($malformedArchive, "disposable malformed-metadata fixture")
    $malformedMetadata = "$malformedArchive.metadata.json"
    [System.IO.File]::WriteAllText($malformedMetadata, "{ malformed")
    $beforeMalformed = @(Get-FixtureSnapshot -Path $malformedFixture)
    $malformedRejected = $false
    try {
        & (Join-Path $PSScriptRoot "Invoke-MiniCloudBackupRetention.ps1") `
            -BackupDirectory $malformedFixture `
            -Apply | Out-Null
    } catch {
        $malformedRejected = $_.Exception.Message -match "Backup metadata is malformed"
    }
    $afterMalformed = @(Get-FixtureSnapshot -Path $malformedFixture)
    $malformedChanges = @(
        Compare-Object `
            -ReferenceObject $beforeMalformed `
            -DifferenceObject $afterMalformed `
            -Property FullName,Length,Sha256,LastWriteTimeUtc
    )
    if (-not $malformedRejected -or $malformedChanges.Count -ne 0) {
        throw "Malformed metadata did not fail closed without modifying or deleting fixture files."
    }
    Write-Output "PASS malformed metadata fails closed without file changes"

    Write-Output "Retention selection fixture tests passed."
} finally {
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
