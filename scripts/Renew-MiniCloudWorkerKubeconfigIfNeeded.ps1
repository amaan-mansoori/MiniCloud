[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AdminKubeconfig,
    [Parameter(Mandatory = $true)]
    [string]$Context,
    [string]$Namespace = "minicloud",
    [string]$OutputPath = ".local\worker-credentials\kubeconfig",
    [string]$ApiServer,
    [ValidateRange(5, 55)]
    [int]$RenewBeforeMinutes = 15,
    [string]$RenewalHelper = (Join-Path $PSScriptRoot "New-MiniCloudWorkerKubeconfig.ps1")
)

$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$localRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot ".local")).TrimEnd("\") + "\"
if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)
} else {
    $resolvedOutputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputPath))
}
if (-not $resolvedOutputPath.StartsWith($localRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Worker credentials must remain inside the repository's ignored .local directory."
}
if (-not (Test-Path -LiteralPath $RenewalHelper -PathType Leaf)) {
    throw "The worker credential renewal helper is missing."
}

function Get-WorkerCredentialExpiry {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $contents = [System.IO.File]::ReadAllText($Path)
    $tokenMatch = [regex]::Match($contents, "(?m)^\s+token:\s*(\S+)\s*$")
    if (-not $tokenMatch.Success) {
        return $null
    }
    $tokenParts = $tokenMatch.Groups[1].Value.Split(".")
    if ($tokenParts.Length -ne 3) {
        return $null
    }
    try {
        $payloadPart = $tokenParts[1].Replace("-", "+").Replace("_", "/")
        $payloadPart += "=" * ((4 - $payloadPart.Length % 4) % 4)
        $claims = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($payloadPart)
        ) | ConvertFrom-Json
        if ($null -eq $claims.exp) {
            return $null
        }
        return [DateTimeOffset]::FromUnixTimeSeconds([long]$claims.exp).ToUniversalTime()
    } catch {
        return $null
    }
}

$mutex = [System.Threading.Mutex]::new($false, "Local\MiniCloudWorkerCredentialRenewal")
$ownsMutex = $false
try {
    try {
        $ownsMutex = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }
    if (-not $ownsMutex) {
        Write-Output "renewal=skipped reason=another_refresh_is_running"
        return
    }

    $renewalDeadline = [DateTimeOffset]::UtcNow.AddMinutes($RenewBeforeMinutes)
    $currentExpiry = Get-WorkerCredentialExpiry -Path $resolvedOutputPath
    if ($currentExpiry -and $currentExpiry -gt $renewalDeadline) {
        Write-Output "renewal=not_needed expires_at_utc=$($currentExpiry.ToString('u'))"
        return
    }

    $helperArguments = @{
        AdminKubeconfig = $AdminKubeconfig
        Context = $Context
        Namespace = $Namespace
        OutputPath = $resolvedOutputPath
        RenewBeforeMinutes = $RenewBeforeMinutes
    }
    if ($ApiServer) {
        $helperArguments.ApiServer = $ApiServer
    }
    try {
        & $RenewalHelper @helperArguments *> $null
    } catch {
        throw "Worker credential refresh failed; the existing kubeconfig was not intentionally modified."
    }

    $refreshedExpiry = Get-WorkerCredentialExpiry -Path $resolvedOutputPath
    if (-not $refreshedExpiry -or $refreshedExpiry -le [DateTimeOffset]::UtcNow.AddMinutes($RenewBeforeMinutes)) {
        throw "Worker credential refresh did not produce a valid credential with sufficient remaining lifetime."
    }
    Write-Output "renewal=completed expires_at_utc=$($refreshedExpiry.ToString('u'))"
} finally {
    if ($ownsMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
