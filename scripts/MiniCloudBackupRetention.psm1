$ErrorActionPreference = "Stop"

function Get-MiniCloudBackupUtcDateKey {
    param(
        [Parameter(Mandatory = $true)]
        [DateTime]$Timestamp
    )

    $utcTimestamp = $Timestamp.ToUniversalTime()
    return $utcTimestamp.ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-MiniCloudBackupUtcWeekKey {
    param(
        [Parameter(Mandatory = $true)]
        [DateTime]$Timestamp
    )

    $date = $Timestamp.ToUniversalTime().Date
    $mondayOffset = (([int]$date.DayOfWeek + 6) % 7)
    return $date.AddDays(-$mondayOffset).ToString(
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
}

function Get-MiniCloudBackupRetentionSelection {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.IO.FileInfo[]]$Backups,
        [ValidateRange(1, 3650)]
        [int]$KeepDaily = 7,
        [ValidateRange(1, 520)]
        [int]$KeepWeekly = 4
    )

    $selectedBackups = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
    $selectedPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $dateSort = @{
        Expression = {
            [DateTime]::ParseExact(
                $_.Name,
                "yyyy-MM-dd",
                [System.Globalization.CultureInfo]::InvariantCulture
            )
        }
        Descending = $true
    }
    $backupSort = @(
        @{ Expression = "LastWriteTimeUtc"; Descending = $true },
        @{ Expression = "Name"; Descending = $true }
    )

    $dailyGroups = @(
        $Backups |
            Group-Object { Get-MiniCloudBackupUtcDateKey -Timestamp $_.LastWriteTimeUtc } |
            Sort-Object -Property $dateSort |
            Select-Object -First $KeepDaily
    )
    foreach ($group in $dailyGroups) {
        $newestBackup = $group.Group |
            Sort-Object -Property $backupSort |
            Select-Object -First 1
        if ($newestBackup -and $selectedPaths.Add($newestBackup.FullName)) {
            [void]$selectedBackups.Add($newestBackup)
        }
    }

    $weeklyGroups = @(
        $Backups |
            Group-Object { Get-MiniCloudBackupUtcWeekKey -Timestamp $_.LastWriteTimeUtc } |
            Sort-Object -Property $dateSort |
            Select-Object -First $KeepWeekly
    )
    foreach ($group in $weeklyGroups) {
        $newestBackup = $group.Group |
            Sort-Object -Property $backupSort |
            Select-Object -First 1
        if ($newestBackup -and $selectedPaths.Add($newestBackup.FullName)) {
            [void]$selectedBackups.Add($newestBackup)
        }
    }

    return $selectedBackups.ToArray()
}

Export-ModuleMember -Function `
    Get-MiniCloudBackupUtcDateKey, `
    Get-MiniCloudBackupUtcWeekKey, `
    Get-MiniCloudBackupRetentionSelection
