$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "MiniCloudApiReplacement.psm1") -Force

function New-TestOperationResult {
    param(
        [bool]$Success,
        [AllowNull()][Nullable[int]]$ExitCode = $null,
        [AllowNull()][object]$Output = $null,
        [AllowNull()][string]$Error = $null
    )
    [pscustomobject]@{
        Success = $Success
        ExitCode = $ExitCode
        Output = $Output
        Error = $Error
    }
}

function New-InspectJson {
    param([string]$Id, [string]$Name, [string]$State)
    @([pscustomobject]@{
        Id = $Id
        Name = "/$Name"
        State = [pscustomobject]@{ Status = $State }
        Config = [pscustomobject]@{ Env = @("TEST=value") }
    }) | ConvertTo-Json -Depth 20 -Compress
}

function New-InspectCallback {
    param([string]$Json)
    $boundJson = $Json
    return {
        param($name)
        [pscustomobject]@{
            Success = $true
            ExitCode = 0
            Output = $boundJson
            Error = $null
        }
    }.GetNewClosure()
}

function Invoke-CaptureAttempt {
    param(
        [string]$CaseName,
        [scriptblock]$InspectCommand,
        [scriptblock]$WriteTemp,
        [scriptblock]$PublishNoClobber,
        [scriptblock]$RemoveTemp,
        [scriptblock]$GetHash,
        [switch]$PrecreateDestination,
        [string]$ContainerName = "api",
        [string]$ExpectedId = "id-api"
    )
    $caseDirectory = Join-Path $script:testRoot $CaseName
    [System.IO.Directory]::CreateDirectory($caseDirectory) | Out-Null
    $finalPath = Join-Path $caseDirectory "capture.json"
    if ($PrecreateDestination) {
        [System.IO.File]::WriteAllText($finalPath, "preserve-existing")
    }
    if ($null -eq $InspectCommand) {
        $InspectCommand = New-InspectCallback (New-InspectJson $ExpectedId $ContainerName "running")
    }
    $arguments = @{
        ContainerName = $ContainerName
        FinalPath = $finalPath
        ExpectedContainerId = $ExpectedId
        InspectCommand = $InspectCommand
    }
    if ($null -ne $WriteTemp) { $arguments.WriteTemp = $WriteTemp }
    if ($null -ne $PublishNoClobber) { $arguments.PublishNoClobber = $PublishNoClobber }
    if ($null -ne $RemoveTemp) { $arguments.RemoveTemp = $RemoveTemp }
    if ($null -ne $GetHash) { $arguments.GetHash = $GetHash }
    $capture = New-MiniCloudDockerInspectCapture @arguments
    [pscustomobject]@{ Capture = $capture; Directory = $caseDirectory; FinalPath = $finalPath }
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Message)
    $threw = $false
    try { & $Action } catch { $threw = $true }
    Assert-True $threw $Message
}

function Invoke-NamedTest {
    param([string]$Name, [scriptblock]$Action)
    try {
        & $Action
        $script:results.Add([pscustomobject]@{ Name = $Name; Passed = $true; Error = $null })
        Write-Output "PASS $Name"
    } catch {
        $script:results.Add([pscustomobject]@{ Name = $Name; Passed = $false; Error = $_.Exception.Message })
        Write-Output ("FAIL {0}: {1}" -f $Name, $_.Exception.Message)
    }
}

$script:testRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "MiniCloud-Capture-Tests-" + [guid]::NewGuid().ToString("N")
)
[System.IO.Directory]::CreateDirectory($script:testRoot) | Out-Null
$script:results = New-Object System.Collections.Generic.List[object]
$script:baseJson = New-InspectJson "id-api" "api" "running"
$script:goodCapture = $null
$script:replacementCapture = $null

try {
    Invoke-NamedTest "Capture publishes verified JSON atomically and records SHA256" {
        $result = Invoke-CaptureAttempt "success" $null $null $null $null $null
        Assert-True ($result.Capture.ContainerId -ceq "id-api") "wrong captured ID"
        Assert-True ($result.Capture.Sha256 -match "^[A-F0-9]{64}$") "invalid SHA256"
        Assert-True ([System.IO.File]::Exists($result.FinalPath)) "published file missing"
        Assert-True (@(Get-ChildItem $result.Directory -Filter "*.tmp").Count -eq 0) "temp link remained"
    }

    Invoke-NamedTest "Nonzero inspect exit code is rejected" {
        $callback = { param($name) New-TestOperationResult $true 17 $script:baseJson $null }.GetNewClosure()
        Assert-Throws { Invoke-CaptureAttempt "nonzero-exit" $callback $null $null $null $null } "nonzero exit accepted"
    }
    Invoke-NamedTest "Missing inspect exit code is rejected" {
        $callback = {
            param($name)
            [pscustomobject]@{ Success = $true; Output = $script:baseJson; Error = $null }
        }.GetNewClosure()
        Assert-Throws { Invoke-CaptureAttempt "missing-exit" $callback $null $null $null $null } "missing exit accepted"
    }
    Invoke-NamedTest "Inspect callback exception is rejected" {
        $callback = { param($name) throw "injected callback exception" }
        Assert-Throws { Invoke-CaptureAttempt "callback-exception" $callback $null $null $null $null } "callback exception accepted"
    }
    Invoke-NamedTest "Empty inspect output is rejected" {
        $callback = { param($name) New-TestOperationResult $true 0 "" $null }
        Assert-Throws { Invoke-CaptureAttempt "empty-output" $callback $null $null $null $null } "empty output accepted"
    }
    Invoke-NamedTest "Malformed JSON is rejected" {
        $callback = { param($name) New-TestOperationResult $true 0 "{bad-json" $null }
        Assert-Throws { Invoke-CaptureAttempt "malformed-json" $callback $null $null $null $null } "malformed JSON accepted"
    }
    Invoke-NamedTest "Multiple inspect records are rejected" {
        $twoRecords = "[$($script:baseJson.TrimStart('[').TrimEnd(']')),$($script:baseJson.TrimStart('[').TrimEnd(']'))]"
        $callback = { param($name) New-TestOperationResult $true 0 $script:twoRecords $null }.GetNewClosure()
        $script:twoRecords = $twoRecords
        Assert-Throws { Invoke-CaptureAttempt "multiple-records" $callback $null $null $null $null } "multiple records accepted"
    }
    Invoke-NamedTest "Wrong inspected container ID is rejected" {
        $callback = New-InspectCallback (New-InspectJson "other-id" "api" "running")
        Assert-Throws { Invoke-CaptureAttempt "wrong-id" $callback $null $null $null $null } "wrong ID accepted"
    }
    Invoke-NamedTest "Wrong inspected container name is rejected" {
        $callback = New-InspectCallback (New-InspectJson "id-api" "other-name" "running")
        Assert-Throws { Invoke-CaptureAttempt "wrong-inspect-name" $callback $null $null $null $null } "wrong inspect name accepted"
    }
    Invoke-NamedTest "Existing destination is preserved and rejected" {
        $callback = { param($name) throw "inspect must not run" }
        $result = $null
        try {
            $result = Invoke-CaptureAttempt "existing-destination" $callback $null $null $null $null -PrecreateDestination
        } catch { }
        $existingPath = Join-Path (Join-Path $script:testRoot "existing-destination") "capture.json"
        Assert-True ($null -eq $result) "existing destination accepted"
        Assert-True ([System.IO.File]::ReadAllText($existingPath) -ceq "preserve-existing") "existing destination changed"
    }
    Invoke-NamedTest "Concurrent destination creation is not overwritten" {
        $script:concurrentFinal = $null
        $publisher = {
            param($temporaryPath, $finalPath)
            [System.IO.File]::WriteAllText($finalPath, "concurrent-writer")
            $module = Get-Module -Name "MiniCloudApiReplacement"
            & $module {
                param($sourcePath, $destinationPath)
                Publish-MiniCloudCaptureNoClobber -TemporaryPath $sourcePath -FinalPath $destinationPath
            } $temporaryPath $finalPath
        }
        $directory = Join-Path $script:testRoot "concurrent-destination"
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        $script:concurrentFinal = Join-Path $directory "capture.json"
        $captureArguments = @{
            ContainerName = "api"; FinalPath = $script:concurrentFinal
            ExpectedContainerId = "id-api"; InspectCommand = (New-InspectCallback $script:baseJson)
            PublishNoClobber = $publisher
        }
        Assert-Throws { New-MiniCloudDockerInspectCapture @captureArguments } "concurrent publication accepted"
        Assert-True ([System.IO.File]::ReadAllText($script:concurrentFinal) -ceq "concurrent-writer") "concurrent destination overwritten"
    }
    Invoke-NamedTest "Owned temporary file is cleaned after publication failure" {
        $publisher = { param($temporaryPath, $finalPath) New-TestOperationResult $false 81 $null "injected publication failure" }
        Assert-Throws { Invoke-CaptureAttempt "cleanup-temp" $null $null $publisher $null $null } "publication failure accepted"
        $directory = Join-Path $script:testRoot "cleanup-temp"
        Assert-True (@(Get-ChildItem $directory -Force).Count -eq 0) "owned temporary file not removed"
    }
    Invoke-NamedTest "Publication failure is surfaced" {
        $publisher = { param($temporaryPath, $finalPath) New-TestOperationResult $false 82 $null "injected publication failure" }
        Assert-Throws { Invoke-CaptureAttempt "publication-failure" $null $null $publisher $null $null } "publication failure ignored"
    }
    Invoke-NamedTest "Post-publication checksum mismatch is rejected" {
        $badHash = { param($path) New-TestOperationResult $true $null ("0" * 64) $null }
        Assert-Throws { Invoke-CaptureAttempt "checksum-mismatch" $null $null $null $null $badHash } "bad checksum accepted"
        $directory = Join-Path $script:testRoot "checksum-mismatch"
        Assert-True ([System.IO.File]::Exists((Join-Path $directory "capture.json"))) "published capture was incorrectly removed"
    }
    Invoke-NamedTest "Filesystem callback exception is rejected" {
        $writer = { param($path, $text) throw "injected writer exception" }
        Assert-Throws { Invoke-CaptureAttempt "writer-exception" $null $writer $null $null $null } "writer exception accepted"
    }
    Invoke-NamedTest "Failed write cleans its owned temporary file" {
        $writer = {
            param($path, $text)
            [System.IO.File]::WriteAllText($path, $text)
            New-TestOperationResult $false $null ([pscustomobject]@{ Owned = $true }) "injected after-write failure"
        }
        Assert-Throws { Invoke-CaptureAttempt "write-cleanup" $null $writer $null $null $null } "failed write accepted"
        $directory = Join-Path $script:testRoot "write-cleanup"
        Assert-True (@(Get-ChildItem $directory -Force).Count -eq 0) "owned failed-write temp not removed"
    }
    Invoke-NamedTest "Staged bytes are revalidated before publication" {
        $writer = {
            param($path, $text)
            [System.IO.File]::WriteAllText($path, "[]")
            New-TestOperationResult $true $null ([pscustomobject]@{ Owned = $true }) $null
        }
        Assert-Throws { Invoke-CaptureAttempt "staged-bytes" $null $writer $null $null $null } "altered staged bytes accepted"
        $directory = Join-Path $script:testRoot "staged-bytes"
        Assert-True (@(Get-ChildItem $directory -Force).Count -eq 0) "altered staged file not cleaned"
    }

    $originalCallback = New-InspectCallback (New-InspectJson "id-api" "api" "exited")
    $original = Invoke-CaptureAttempt "rollback-original" $originalCallback $null $null $null $null
    $replacementJson = New-InspectJson "id-replacement" "minicloud-api-1" "running"
    $replacementCallback = New-InspectCallback $replacementJson
    $replacement = Invoke-CaptureAttempt "rollback-replacement" $replacementCallback $null $null $null $null `
        -ContainerName "minicloud-api-1" -ExpectedId "id-replacement"
    $script:goodCapture = $original.Capture
    $script:replacementCapture = $replacement.Capture

    function Invoke-TestRollback {
        param($OriginalId, $ReplacementId, $OriginalName, $ReplacementName, $OriginalState, $ReplacementState, $OriginalCapture, $ReplacementCapture)
        New-MiniCloudRollbackPlan -OriginalCapture $OriginalCapture -ReplacementCapture $ReplacementCapture `
            -OriginalId $OriginalId -ReplacementId $ReplacementId `
            -ExpectedOriginalName $OriginalName -ExpectedReplacementName $ReplacementName `
            -ObservedOriginalState $OriginalState -ObservedReplacementState $ReplacementState -Mode DryRun
    }
    Invoke-NamedTest "Valid captured identities produce non-executable dry-run plan" {
        $plan = Invoke-TestRollback "id-api" "id-replacement" "api" "minicloud-api-1" exited running $script:goodCapture $script:replacementCapture
        Assert-True ($plan.Executable -eq $false) "plan marked executable"
        Assert-True (@($plan.Actions | Where-Object { $null -ne $_.Command }).Count -eq 0) "command was generated"
    }
    Invoke-NamedTest "Rollback rejects wrong original ID" {
        Assert-Throws { Invoke-TestRollback "arbitrary-original" "id-replacement" "api" "minicloud-api-1" exited running $script:goodCapture $script:replacementCapture } "wrong original ID accepted"
    }
    Invoke-NamedTest "Rollback rejects wrong replacement ID" {
        Assert-Throws { Invoke-TestRollback "id-api" "arbitrary-replacement" "api" "minicloud-api-1" exited running $script:goodCapture $script:replacementCapture } "wrong replacement ID accepted"
    }
    Invoke-NamedTest "Rollback rejects duplicate IDs" {
        Assert-Throws { Invoke-TestRollback "id-api" "id-api" "api" "minicloud-api-1" exited running $script:goodCapture $script:replacementCapture } "duplicate IDs accepted"
    }
    Invoke-NamedTest "Rollback rejects missing original ID" {
        Assert-Throws {
            New-MiniCloudRollbackPlan -OriginalCapture $script:goodCapture -ReplacementCapture $script:replacementCapture `
                -ReplacementId "id-replacement" -ExpectedOriginalName "api" -ExpectedReplacementName "minicloud-api-1" `
                -ObservedOriginalState exited -ObservedReplacementState running -Mode DryRun
        } "missing original ID accepted"
    }
    Invoke-NamedTest "Rollback rejects blank replacement ID" {
        Assert-Throws {
            Invoke-TestRollback "id-api" "" "api" "minicloud-api-1" exited running $script:goodCapture $script:replacementCapture
        } "blank replacement ID accepted"
    }
    Invoke-NamedTest "Rollback rejects wrong expected name" {
        Assert-Throws { Invoke-TestRollback "id-api" "id-replacement" "wrong-name" "minicloud-api-1" exited running $script:goodCapture $script:replacementCapture } "wrong name accepted"
    }
    Invoke-NamedTest "Rollback rejects wrong replacement name" {
        Assert-Throws { Invoke-TestRollback "id-api" "id-replacement" "api" "wrong-name" exited running $script:goodCapture $script:replacementCapture } "wrong replacement name accepted"
    }
    Invoke-NamedTest "Rollback rejects invalid or mismatched state" {
        Assert-Throws { Invoke-TestRollback "id-api" "id-replacement" "api" "minicloud-api-1" running running $script:goodCapture $script:replacementCapture } "unsafe original state accepted"
    }
    Invoke-NamedTest "Rollback rejects unsupported observed state" {
        Assert-Throws {
            New-MiniCloudRollbackPlan -OriginalCapture $script:goodCapture -ReplacementCapture $script:replacementCapture `
                -OriginalId "id-api" -ReplacementId "id-replacement" -ExpectedOriginalName "api" `
                -ExpectedReplacementName "minicloud-api-1" -ObservedOriginalState paused `
                -ObservedReplacementState running -Mode DryRun
        } "unsupported state accepted"
    }
    Invoke-NamedTest "Rollback rejects missing capture provenance" {
        Assert-Throws { Invoke-TestRollback "id-api" "id-replacement" "api" "minicloud-api-1" exited running ([pscustomobject]@{ ContainerId = "id-api" }) $script:replacementCapture } "missing provenance accepted"
    }
    Invoke-NamedTest "Rollback rejects missing replacement provenance" {
        Assert-Throws { Invoke-TestRollback "id-api" "id-replacement" "api" "minicloud-api-1" exited running $script:goodCapture ([pscustomobject]@{ ContainerId = "id-replacement" }) } "missing replacement provenance accepted"
    }
    Invoke-NamedTest "Rollback rejects modified capture bytes" {
        $tampered = $script:goodCapture.PSObject.Copy()
        [System.IO.File]::AppendAllText($tampered.Path, " ")
        Assert-Throws { Invoke-TestRollback "id-api" "id-replacement" "api" "minicloud-api-1" exited running $tampered $script:replacementCapture } "tampered capture accepted"
    }
}
finally {
    foreach ($file in @(Get-ChildItem -LiteralPath $script:testRoot -File -Recurse -Force -ErrorAction SilentlyContinue)) {
        [System.IO.File]::Delete($file.FullName)
    }
    foreach ($directory in @(Get-ChildItem -LiteralPath $script:testRoot -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending)) {
        [System.IO.Directory]::Delete($directory.FullName, $false)
    }
    [System.IO.Directory]::Delete($script:testRoot, $false)
}

$failed = @($script:results | Where-Object { -not $_.Passed })
Write-Output ("Named tests: {0}; passed: {1}; failed: {2}" -f `
    $script:results.Count, ($script:results.Count - $failed.Count), $failed.Count)
if ($failed.Count -gt 0) {
    foreach ($failure in $failed) { Write-Output ("FAILURE {0}: {1}" -f $failure.Name, $failure.Error) }
    exit 1
}
