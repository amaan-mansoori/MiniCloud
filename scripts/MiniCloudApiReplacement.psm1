Set-StrictMode -Version Latest

function New-MiniCloudOperationResult {
    param(
        [Parameter(Mandatory)][bool]$Success,
        [AllowNull()][Nullable[int]]$ExitCode,
        [AllowNull()][object]$Output,
        [AllowNull()][string]$Error
    )
    [pscustomobject]@{
        Success = $Success
        ExitCode = $ExitCode
        Output = $Output
        Error = $Error
    }
}

function Invoke-MiniCloudOperation {
    param(
        [Parameter(Mandatory)][scriptblock]$Operation,
        [Parameter(Mandatory)][object[]]$Arguments,
        [Parameter(Mandatory)][string]$Name,
        [switch]$RequireExitCode,
        [switch]$AllowFailure
    )
    try {
        $result = & $Operation @Arguments
    } catch {
        $result = New-MiniCloudOperationResult -Success $false -ExitCode $null `
            -Output $null -Error $_.Exception.Message
    }
    if ($null -eq $result) {
        throw "$Name returned no operation result."
    }
    foreach ($field in @("Success", "ExitCode", "Output", "Error")) {
        if ($null -eq $result.psobject.Properties[$field]) {
            throw "$Name returned a malformed operation result."
        }
    }
    if ($result.Success -isnot [bool]) {
        throw "$Name returned a malformed success value."
    }
    if ($null -ne $result.ExitCode -and $result.ExitCode -isnot [int]) {
        throw "$Name returned a malformed exit code."
    }
    if ($RequireExitCode -and $null -eq $result.ExitCode) {
        throw "$Name did not return an exit code."
    }
    if (-not $result.Success -and -not $AllowFailure) {
        $code = if ($null -eq $result.ExitCode) { "unavailable" } else { [string]$result.ExitCode }
        throw "$Name failed (exit code: $code)."
    }
    if ($result.Success -and $null -ne $result.ExitCode -and $result.ExitCode -ne 0) {
        throw "$Name reported success with a nonzero exit code."
    }
    return $result
}

function Write-MiniCloudCaptureTemp {
    param([string]$Path, [string]$Text)
    $stream = $null
    $owned = $false
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $owned = $true
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        return New-MiniCloudOperationResult -Success $true -ExitCode $null `
            -Output ([pscustomobject]@{ Owned = $true }) -Error $null
    } catch {
        $operationError = $_.Exception.Message
        $cleanupError = $null
        if ($null -ne $stream) { $stream.Dispose() }
        if ($owned -and [System.IO.File]::Exists($Path)) {
            try {
                [System.IO.File]::Delete($Path)
                $owned = $false
            } catch {
                $cleanupError = $_.Exception.Message
            }
        }
        if ($cleanupError) { $operationError = "$operationError; temp cleanup: $cleanupError" }
        return New-MiniCloudOperationResult -Success $false -ExitCode $null `
            -Output ([pscustomobject]@{ Owned = $owned }) -Error $operationError
    }
}

function Publish-MiniCloudCaptureNoClobber {
    param([string]$TemporaryPath, [string]$FinalPath)
    try {
        if (-not ("MiniCloud.AtomicHardLink" -as [type])) {
            Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace MiniCloud {
    public static class AtomicHardLink {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CreateHardLinkW")]
        public static extern bool Create(string newFileName, string existingFileName, IntPtr securityAttributes);
    }
}
"@
        }
        if (-not [MiniCloud.AtomicHardLink]::Create(
            $FinalPath, $TemporaryPath, [IntPtr]::Zero
        )) {
            $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            return New-MiniCloudOperationResult -Success $false -ExitCode $errorCode `
                -Output $null -Error "Atomic no-clobber hard-link publication failed."
        }
        return New-MiniCloudOperationResult -Success $true -ExitCode 0 `
            -Output ([pscustomobject]@{
                PublishedPath = $FinalPath
                Atomic = $true
                NoClobber = $true
            }) -Error $null
    } catch {
        return New-MiniCloudOperationResult -Success $false -ExitCode $null `
            -Output $null -Error $_.Exception.Message
    }
}

function Remove-MiniCloudCaptureTemp {
    param([string]$Path)
    try {
        if ([System.IO.File]::Exists($Path)) { [System.IO.File]::Delete($Path) }
        return New-MiniCloudOperationResult -Success $true -ExitCode $null `
            -Output $Path -Error $null
    } catch {
        return New-MiniCloudOperationResult -Success $false -ExitCode $null `
            -Output $null -Error $_.Exception.Message
    }
}

function Get-MiniCloudCaptureSha256 {
    param([string]$Path)
    try {
        $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash
        return New-MiniCloudOperationResult -Success $true -ExitCode $null `
            -Output $hash -Error $null
    } catch {
        return New-MiniCloudOperationResult -Success $false -ExitCode $null `
            -Output $null -Error $_.Exception.Message
    }
}

function New-MiniCloudDockerInspectCapture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ContainerName,
        [Parameter(Mandatory)][string]$FinalPath,
        [Parameter(Mandatory)][string]$ExpectedContainerId,
        [Parameter(Mandatory)][scriptblock]$InspectCommand,
        [scriptblock]$WriteTemp = ${function:Write-MiniCloudCaptureTemp},
        [scriptblock]$PublishNoClobber = ${function:Publish-MiniCloudCaptureNoClobber},
        [scriptblock]$RemoveTemp = ${function:Remove-MiniCloudCaptureTemp},
        [scriptblock]$GetHash = ${function:Get-MiniCloudCaptureSha256}
    )

    if ([string]::IsNullOrWhiteSpace($ContainerName) -or
        [string]::IsNullOrWhiteSpace($ExpectedContainerId)) {
        throw "Container name and expected ID are required."
    }
    $absoluteFinalPath = [System.IO.Path]::GetFullPath($FinalPath)
    $directory = [System.IO.Path]::GetDirectoryName($absoluteFinalPath)
    if (-not [System.IO.Directory]::Exists($directory)) {
        throw "Capture destination directory does not exist."
    }
    if ([System.IO.File]::Exists($absoluteFinalPath) -or
        [System.IO.Directory]::Exists($absoluteFinalPath)) {
        throw "Refusing to overwrite an existing capture."
    }

    $temporaryPath = Join-Path $directory (
        "." + [System.IO.Path]::GetFileName($absoluteFinalPath) + "." +
        [guid]::NewGuid().ToString("N") + ".tmp"
    )
    $temporaryOwned = $false
    try {
        $inspect = Invoke-MiniCloudOperation -Operation $InspectCommand `
            -Arguments @($ContainerName) -Name "Inspect operation" -RequireExitCode
        if ($inspect.Output -isnot [string] -or
            [string]::IsNullOrWhiteSpace($inspect.Output)) {
            throw "Inspect operation returned empty or malformed output."
        }
        try {
            $parsed = ConvertFrom-Json -InputObject $inspect.Output -ErrorAction Stop
        } catch {
            throw "Inspect operation returned malformed JSON."
        }
        $records = @($parsed)
        if ($records.Count -ne 1 -or $null -eq $records[0]) {
            throw "Inspect output must contain exactly one container record."
        }
        $record = $records[0]
        $actualId = [string]$record.Id
        $recordName = ([string]$record.Name).TrimStart("/")
        if ($actualId -cne $ExpectedContainerId) {
            throw "Inspect output container ID did not match the expected ID."
        }
        if ($recordName -cne $ContainerName) {
            throw "Inspect output container name did not match the expected name."
        }
        $state = [string]$record.State.Status
        if ([string]::IsNullOrWhiteSpace($state)) {
            throw "Inspect output did not contain a container state."
        }

        $write = Invoke-MiniCloudOperation -Operation $WriteTemp `
            -Arguments @($temporaryPath, $inspect.Output) -Name "Temporary-file write" -AllowFailure
        $temporaryOwned = ($null -ne $write.Output -and
            $null -ne $write.Output.PSObject.Properties["Owned"] -and
            [bool]$write.Output.Owned)
        if (-not $write.Success) {
            throw "Temporary-file write failed."
        }
        $stagedText = [System.IO.File]::ReadAllText(
            $temporaryPath, (New-Object System.Text.UTF8Encoding($false))
        )
        if ($stagedText -cne $inspect.Output) {
            throw "Temporary capture contents changed before publication."
        }

        $publication = Invoke-MiniCloudOperation -Operation $PublishNoClobber `
            -Arguments @($temporaryPath, $absoluteFinalPath) -Name "Atomic publication"
        if ($null -eq $publication.Output -or
            $publication.Output.PublishedPath -cne $absoluteFinalPath -or
            $publication.Output.Atomic -isnot [bool] -or
            -not $publication.Output.Atomic -or
            $publication.Output.NoClobber -isnot [bool] -or
            -not $publication.Output.NoClobber) {
            throw "Publisher did not attest atomic no-clobber publication."
        }
        $hashResult = Invoke-MiniCloudOperation -Operation $GetHash `
            -Arguments @($absoluteFinalPath) -Name "Post-publication SHA256"
        $hash = [string]$hashResult.Output
        $actualHash = (Get-FileHash -LiteralPath $absoluteFinalPath `
            -Algorithm SHA256 -ErrorAction Stop).Hash
        if ($hash -notmatch "^[A-Fa-f0-9]{64}$" -or
            $hash.ToUpperInvariant() -cne $actualHash.ToUpperInvariant()) {
            throw "Post-publication SHA256 result was missing, malformed, or mismatched."
        }
        $cleanup = Invoke-MiniCloudOperation -Operation $RemoveTemp `
            -Arguments @($temporaryPath) -Name "Temporary-file cleanup"
        $temporaryOwned = $false

        return [pscustomobject]@{
            Source = "MiniCloudDockerInspectCapture"
            CaptureVersion = 1
            CaptureId = [guid]::NewGuid().ToString("D")
            CapturedAtUtc = [DateTime]::UtcNow.ToString("o")
            ContainerName = $ContainerName
            ContainerId = $actualId
            State = $state
            Path = $absoluteFinalPath
            Sha256 = $hash.ToUpperInvariant()
        }
    } catch {
        $failure = $_
        if ($temporaryOwned -and [System.IO.File]::Exists($temporaryPath)) {
            $cleanup = Invoke-MiniCloudOperation -Operation $RemoveTemp `
                -Arguments @($temporaryPath) -Name "Temporary-file cleanup"
            if (-not $cleanup.Success) {
                throw "Capture failed and its owned temporary file could not be removed."
            }
        }
        throw $failure
    }
}

function Test-MiniCloudAclEntries {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Acl,
        [Parameter(Mandatory)][string]$OperatorSid,
        [Parameter(Mandatory)][ValidateSet("Directory", "File")][string]$Kind
    )
    if (-not $Acl.OwnerSid -or $Acl.OwnerSid -ne $OperatorSid) { return $false }
    if ($null -eq $Acl.Access) { return $false }
    $entries = @($Acl.Access)
    if (@($entries | Where-Object { $_.IsInherited }).Count -ne 0) { return $false }
    if (@($entries | Where-Object { $_.AccessControlType -eq "Deny" }).Count -ne 0) { return $false }
    $unexpected = @($entries | Where-Object { $_.IdentitySid -ne $OperatorSid })
    if ($unexpected.Count -ne 0) { return $false }
    $allows = @($entries | Where-Object {
        $_.IdentitySid -eq $OperatorSid -and $_.AccessControlType -eq "Allow"
    })
    if ($allows.Count -ne 1 -or $allows[0].Rights -ne "FullControl") { return $false }
    if ($Kind -eq "Directory" -and $allows[0].InheritanceFlags -notmatch "ContainerInherit") {
        return $false
    }
    return $true
}

function Test-MiniCloudEffectiveAccess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Acl,
        [Parameter(Mandatory)][string]$OperatorSid,
        [Parameter(Mandatory)][ValidateSet("Directory", "File")][string]$Kind
    )
    # ACL entry inspection is deliberately not treated as Windows AccessCheck.
    return [pscustomobject]@{
        Verified = $false
        Blocked = $true
        Reason = "Windows AccessCheck implementation and evidence are required."
        Kind = $Kind
        OperatorSid = $OperatorSid
    }
}

$script:SupportedConfigFields = @(
    "Args", "Cmd", "Entrypoint", "Env", "ExposedPorts", "Hostname", "Image",
    "Labels", "OnBuild", "OpenStdin", "StdinOnce", "Tty", "User", "Volumes",
    "WorkingDir", "StopSignal", "Healthcheck"
)
$script:SupportedHostConfigFields = @(
    "AutoRemove", "Binds", "CgroupnsMode", "CapAdd", "CapDrop", "CgroupParent",
    "ConsoleSize", "CpuCount", "CpuPercent", "CpuPeriod", "CpuQuota",
    "CpuRealtimePeriod", "CpuRealtimeRuntime", "CpusetCpus", "CpusetMems",
    "DeviceCgroupRules", "Devices", "DeviceRequests", "Dns", "DnsOptions",
    "DnsSearch", "ExtraHosts", "GroupAdd", "IpcMode", "Init", "Isolation",
    "KernelMemory", "Links", "LogConfig", "MaskedPaths", "Memory",
    "MemoryReservation", "MemorySwap", "MemorySwappiness", "NanoCpus",
    "NetworkMode", "OomKillDisable", "OomScoreAdj", "PidMode", "PidsLimit",
    "PortBindings", "Privileged", "PublishAllPorts", "ReadonlyRootfs",
    "ReadonlyPaths", "RestartPolicy", "SecurityOpt", "ShmSize", "StorageOpt",
    "Sysctls", "Tmpfs", "UTSMode", "Ulimits", "UsernsMode", "VolumesFrom",
    "Mounts", "StopTimeout", "Runtime", "NoNewPrivileges"
)
$script:SupportedNetworkFields = @(
    "Networks", "Ports", "SandboxID", "SandboxKey", "HairpinMode",
    "LinkLocalIPv6Address", "LinkLocalIPv6PrefixLen", "GlobalIPv6Address",
    "GlobalIPv6PrefixLen", "IPAddress", "IPPrefixLen", "Gateway", "MacAddress"
)

function Test-MiniCloudFieldAllowlist {
    param([Parameter(Mandatory)][object]$Configuration)
    foreach ($section in @("Config", "HostConfig", "NetworkSettings")) {
        $object = $Configuration.$section
        if ($null -eq $object) { throw "Missing configuration section: $section" }
        $allowed = switch ($section) {
            "Config" { $script:SupportedConfigFields }
            "HostConfig" { $script:SupportedHostConfigFields }
            "NetworkSettings" { $script:SupportedNetworkFields }
        }
        foreach ($property in $object.psobject.Properties) {
            if ($property.Name -notin $allowed -and $null -ne $property.Value -and
                [string]$property.Value -ne "" -and
                ($property.Value -isnot [array] -or $property.Value.Count -ne 0) -and
                ($property.Value -isnot [hashtable] -or $property.Value.Count -ne 0) -and
                ($property.Value -isnot [pscustomobject] -or @($property.Value.psobject.Properties).Count -ne 0)) {
                throw "Unsupported non-default field: $section.$($property.Name)"
            }
        }
    }
    return $true
}

function ConvertTo-MiniCloudCreateModel {
    [CmdletBinding()]
    param([Parameter(Mandatory)][object]$Configuration)
    Test-MiniCloudFieldAllowlist $Configuration | Out-Null
    $c = $Configuration.Config
    $h = $Configuration.HostConfig
    $n = $Configuration.NetworkSettings
    if ([string]::IsNullOrWhiteSpace([string]$Configuration.Image) -or
        [string]::IsNullOrWhiteSpace([string]$c.Image)) {
        throw "Image and image ID are required."
    }
    [pscustomobject]@{
        Image = [string]$c.Image
        ImageId = [string]$Configuration.Image
        Config = $c
        HostConfig = $h
        NetworkSettings = $n
    }
}

function ConvertTo-MiniCloudComparable {
    param([Parameter(Mandatory)][object]$Model)
    $json = $Model | ConvertTo-Json -Depth 100 -Compress
    return ($json | ConvertFrom-Json)
}

function Compare-MiniCloudDockerConfigurations {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Original,
        [Parameter(Mandatory)][object]$Intended,
        [Parameter(Mandatory)][object]$Actual
    )
    $originalJson = (ConvertTo-MiniCloudComparable $Original | ConvertTo-Json -Depth 100 -Compress)
    $intendedJson = (ConvertTo-MiniCloudComparable $Intended | ConvertTo-Json -Depth 100 -Compress)
    $actualJson = (ConvertTo-MiniCloudComparable $Actual | ConvertTo-Json -Depth 100 -Compress)
    $differences = @()
    if ($originalJson -ne $intendedJson) {
        $differences += [pscustomobject]@{ Comparison = "OriginalToIntended"; Difference = "Changed" }
    }
    if ($intendedJson -ne $actualJson) {
        $differences += [pscustomobject]@{ Comparison = "IntendedToActual"; Difference = "Changed" }
    }
    [pscustomobject]@{
        Equal = ($differences.Count -eq 0)
        OriginalToIntendedEqual = ($originalJson -eq $intendedJson)
        IntendedToActualEqual = ($intendedJson -eq $actualJson)
        Differences = $differences
    }
}

function Get-MiniCloudCapturedIdentity {
    param([Parameter(Mandatory)][object]$Capture)
    foreach ($field in @(
        "Source", "CaptureVersion", "CaptureId", "CapturedAtUtc", "ContainerName",
        "ContainerId", "State", "Path", "Sha256"
    )) {
        if ($null -eq $Capture.PSObject.Properties[$field]) {
            throw "Capture provenance is incomplete."
        }
    }
    if ($Capture.Source -cne "MiniCloudDockerInspectCapture" -or
        $Capture.CaptureVersion -ne 1 -or
        [string]::IsNullOrWhiteSpace([string]$Capture.CaptureId) -or
        [string]::IsNullOrWhiteSpace([string]$Capture.CapturedAtUtc) -or
        [string]::IsNullOrWhiteSpace([string]$Capture.ContainerName) -or
        [string]::IsNullOrWhiteSpace([string]$Capture.ContainerId) -or
        [string]$Capture.Sha256 -notmatch "^[A-Fa-f0-9]{64}$" -or
        -not [System.IO.File]::Exists([string]$Capture.Path)) {
        throw "Capture provenance is invalid."
    }
    try {
        $actualHash = (Get-FileHash -LiteralPath $Capture.Path -Algorithm SHA256 -ErrorAction Stop).Hash
        $parsed = ConvertFrom-Json -InputObject (
            Get-Content -LiteralPath $Capture.Path -Raw -ErrorAction Stop
        ) -ErrorAction Stop
    } catch {
        throw "Capture provenance file could not be verified."
    }
    $records = @($parsed)
    if ($actualHash -cne [string]$Capture.Sha256 -or $records.Count -ne 1) {
        throw "Capture checksum or record count did not match provenance."
    }
    $record = $records[0]
    if ([string]$record.Id -cne [string]$Capture.ContainerId -or
        ([string]$record.Name).TrimStart("/") -cne [string]$Capture.ContainerName -or
        [string]$record.State.Status -cne [string]$Capture.State) {
        throw "Captured identity did not match its provenance record."
    }
    return [pscustomobject]@{
        ContainerId = [string]$record.Id
        ContainerName = [string]$Capture.ContainerName
        State = [string]$record.State.Status
        CaptureId = [string]$Capture.CaptureId
        Sha256 = [string]$Capture.Sha256
    }
}

function New-MiniCloudRollbackPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$OriginalCapture,
        [Parameter(Mandatory)][object]$ReplacementCapture,
        [Parameter(Mandatory)][AllowEmptyString()][string]$OriginalId,
        [Parameter(Mandatory)][AllowEmptyString()][string]$ReplacementId,
        [Parameter(Mandatory)][string]$ExpectedOriginalName,
        [Parameter(Mandatory)][string]$ExpectedReplacementName,
        [Parameter(Mandatory)][ValidateSet("created", "running", "exited")][string]$ObservedOriginalState,
        [Parameter(Mandatory)][ValidateSet("created", "running", "exited")][string]$ObservedReplacementState,
        [Parameter(Mandatory)][ValidateSet("DryRun")][string]$Mode
    )
    if ([string]::IsNullOrWhiteSpace($OriginalId) -or
        [string]::IsNullOrWhiteSpace($ReplacementId) -or
        $OriginalId -ceq $ReplacementId) {
        throw "Rollback IDs are missing, blank, or duplicated."
    }
    $original = Get-MiniCloudCapturedIdentity -Capture $OriginalCapture
    $replacement = Get-MiniCloudCapturedIdentity -Capture $ReplacementCapture
    if ($OriginalId -cne $original.ContainerId -or
        $ReplacementId -cne $replacement.ContainerId) {
        throw "Supplied rollback ID did not match its verified capture."
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedOriginalName) -or
        [string]::IsNullOrWhiteSpace($ExpectedReplacementName) -or
        $ExpectedOriginalName -cne $original.ContainerName -or
        $ExpectedReplacementName -cne $replacement.ContainerName) {
        throw "Expected rollback name did not match its verified capture."
    }
    if ($ObservedOriginalState -cne $original.State -or
        $ObservedReplacementState -cne $replacement.State -or
        $ObservedOriginalState -cne "exited") {
        throw "Observed rollback state did not match the captured safe state."
    }
    [pscustomobject]@{
        Mode = $Mode
        Executable = $false
        Original = $original
        Replacement = $replacement
        Actions = @(
            [pscustomobject]@{
                Step = 1
                Description = "Re-verify both captured identities and states before any separately approved recovery."
                Command = $null
            },
            [pscustomobject]@{
                Step = 2
                Description = "No stop, remove, rename, or start command is generated by this dry-run planner."
                Command = $null
            }
        )
    }
}

Export-ModuleMember -Function @(
    "New-MiniCloudDockerInspectCapture", "Test-MiniCloudAclEntries",
    "Test-MiniCloudEffectiveAccess", "Test-MiniCloudFieldAllowlist",
    "ConvertTo-MiniCloudCreateModel", "Compare-MiniCloudDockerConfigurations",
    "New-MiniCloudRollbackPlan"
)
