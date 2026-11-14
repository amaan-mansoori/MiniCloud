$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$fixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("minicloud-credential-test-" + [guid]::NewGuid().ToString("N"))
$scriptsPath = Join-Path $fixtureRoot "scripts"
$credentialsDirectory = Join-Path $fixtureRoot ".local\worker-credentials"
$adminKubeconfig = Join-Path $fixtureRoot "admin.kubeconfig"
$actualHelper = Join-Path $PSScriptRoot "New-MiniCloudWorkerKubeconfig.ps1"
$renewalController = Join-Path $PSScriptRoot "Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1"
New-Item -ItemType Directory -Path $scriptsPath,$credentialsDirectory | Out-Null
New-Item -ItemType File -Path $adminKubeconfig | Out-Null
Copy-Item -LiteralPath $actualHelper -Destination $scriptsPath
Copy-Item -LiteralPath $renewalController -Destination $scriptsPath
$workerKubeconfig = Join-Path $credentialsDirectory "kubeconfig"
$global:MiniCloudTestTokenMode = "valid"
$global:MiniCloudTestAclFailure = $false
$global:MiniCloudTestCreateTokenCalls = 0
$global:MiniCloudTestAclArguments = @()

function New-MiniCloudTestToken {
    param([long]$ExpiresAt)
    $payload = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes("{`"exp`":$ExpiresAt}")
    ).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    return "e30.$payload.synthetic-signature"
}

function Set-MiniCloudTestKubeconfig {
    param([string]$Token)
    $yaml = @"
apiVersion: v1
kind: Config
users:
- name: minicloud-worker
  user:
    token: $Token
"@
    [System.IO.File]::WriteAllText($workerKubeconfig, $yaml)
}

function global:kubectl {
    if ($args -contains "config") {
        $global:LASTEXITCODE = 0
        return '{"contexts":[{"context":{"cluster":"minicloud-cluster"}}],"clusters":[{"name":"minicloud-cluster","cluster":{"server":"https://127.0.0.1:6443","certificate-authority-data":"dGVzdA=="}}]}'
    }
    if ($args -contains "create" -and $args -contains "token") {
        $global:MiniCloudTestCreateTokenCalls++
        if ($global:MiniCloudTestTokenMode -eq "command-failure") {
            $global:LASTEXITCODE = 1
            return
        }
        if ($global:MiniCloudTestTokenMode -eq "malformed") {
            $global:LASTEXITCODE = 0
            return "not-a-jwt"
        }
        if ($global:MiniCloudTestTokenMode -eq "short-lived") {
            $expiry = [DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds()
        } else {
            $expiry = [DateTimeOffset]::UtcNow.AddHours(1).ToUnixTimeSeconds()
        }
        $global:LASTEXITCODE = 0
        return (New-MiniCloudTestToken -ExpiresAt $expiry)
    }
    throw "Unexpected fake kubectl operation."
}

function global:icacls {
    $global:MiniCloudTestAclArguments = @($args)
    if ($global:MiniCloudTestAclFailure) {
        $global:LASTEXITCODE = 1
        return "Synthetic ACL failure."
    }
    $global:LASTEXITCODE = 0
    return "Synthetic ACL success."
}

try {
    $oldToken = New-MiniCloudTestToken -ExpiresAt ([DateTimeOffset]::UtcNow.AddHours(2).ToUnixTimeSeconds())
    Set-MiniCloudTestKubeconfig -Token $oldToken
    $oldContents = [System.IO.File]::ReadAllText($workerKubeconfig)
    $output = @(
        & (Join-Path $scriptsPath "Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig
    )
    if ($output[0] -notmatch "^renewal=not_needed" -or $global:MiniCloudTestCreateTokenCalls -ne 0) {
        throw "A sufficiently fresh credential unexpectedly triggered renewal."
    }
    if ([System.IO.File]::ReadAllText($workerKubeconfig) -ne $oldContents) {
        throw "The no-op credential check modified the kubeconfig."
    }

    $nearExpiryToken = New-MiniCloudTestToken -ExpiresAt ([DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds())
    Set-MiniCloudTestKubeconfig -Token $nearExpiryToken
    & (Join-Path $scriptsPath "New-MiniCloudWorkerKubeconfig.ps1") `
        -AdminKubeconfig $adminKubeconfig `
        -Context "fixture-context" `
        -OutputPath $workerKubeconfig `
        -ApiServer "https://minicloud-phase5-control-plane:6443" | Out-Null
    if ($global:MiniCloudTestCreateTokenCalls -ne 1) {
        throw "The credential helper did not request a replacement token."
    }

    $nearExpiryToken = New-MiniCloudTestToken -ExpiresAt ([DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds())
    Set-MiniCloudTestKubeconfig -Token $nearExpiryToken
    $output = @(
        & (Join-Path $scriptsPath "Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig `
            -ApiServer "https://minicloud-phase5-control-plane:6443"
    )
    $renewedContents = [System.IO.File]::ReadAllText($workerKubeconfig)
    if (
        $output[0] -notmatch "^renewal=completed" -or
        $global:MiniCloudTestCreateTokenCalls -ne 2 -or
        $renewedContents -match [regex]::Escape($nearExpiryToken) -or
        $renewedContents -notmatch "(?m)^\s+token:\s+e30\."
    ) {
        throw "An expiring credential was not safely refreshed."
    }
    if (
        $global:MiniCloudTestAclArguments -notcontains "/inheritance:r" -or
        $global:MiniCloudTestAclArguments -notcontains "/grant:r"
    ) {
        throw "The helper did not request a restricted ACL for its temporary credential file."
    }
    if (Get-ChildItem -LiteralPath $credentialsDirectory -Filter ".kubeconfig-*.tmp") {
        throw "A successful replacement left a temporary credential file behind."
    }

    $malformedCredentials = @(
        "apiVersion: v1",
        "users:",
        "- name: minicloud-worker",
        "  user:",
        "    token: malformed"
    ) -join "`n"
    [System.IO.File]::WriteAllText($workerKubeconfig, $malformedCredentials)
    $output = @(
        & (Join-Path $scriptsPath "Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig
    )
    if ($output[0] -notmatch "^renewal=completed" -or $global:MiniCloudTestCreateTokenCalls -ne 3) {
        throw "Malformed existing credentials were not replaced."
    }

    $nearExpiryToken = New-MiniCloudTestToken -ExpiresAt ([DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds())
    Set-MiniCloudTestKubeconfig -Token $nearExpiryToken
    $originalContents = [System.IO.File]::ReadAllText($workerKubeconfig)
    $global:MiniCloudTestTokenMode = "short-lived"
    $shortTokenRejected = $false
    try {
        & (Join-Path $scriptsPath "New-MiniCloudWorkerKubeconfig.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig | Out-Null
    } catch {
        $shortTokenRejected = $_.Exception.Message -match "insufficient remaining lifetime"
    }
    if (
        -not $shortTokenRejected -or
        [System.IO.File]::ReadAllText($workerKubeconfig) -ne $originalContents
    ) {
        throw "A short-lived replacement token was not rejected before changing the existing credential."
    }

    $global:MiniCloudTestTokenMode = "valid"
    $nearExpiryToken = New-MiniCloudTestToken -ExpiresAt ([DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds())
    Set-MiniCloudTestKubeconfig -Token $nearExpiryToken
    $originalContents = [System.IO.File]::ReadAllText($workerKubeconfig)
    $global:MiniCloudTestTokenMode = "command-failure"
    $refreshFailureObserved = $false
    try {
        & (Join-Path $scriptsPath "Renew-MiniCloudWorkerKubeconfigIfNeeded.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig | Out-Null
    } catch {
        $refreshFailureObserved = $_.Exception.Message -match "refresh failed"
    }
    if (-not $refreshFailureObserved -or [System.IO.File]::ReadAllText($workerKubeconfig) -ne $originalContents) {
        throw "A failed renewal did not preserve the existing credential file."
    }

    $global:MiniCloudTestTokenMode = "valid"
    $global:MiniCloudTestAclFailure = $true
    $aclFailureObserved = $false
    try {
        & (Join-Path $scriptsPath "New-MiniCloudWorkerKubeconfig.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig | Out-Null
    } catch {
        $aclFailureObserved = $_.Exception.Message -match "restrict the temporary worker credential file"
    }
    if (-not $aclFailureObserved -or [System.IO.File]::ReadAllText($workerKubeconfig) -ne $originalContents) {
        throw "An ACL failure did not preserve the existing kubeconfig."
    }
    if (Get-ChildItem -LiteralPath $credentialsDirectory -Filter ".kubeconfig-*.tmp") {
        throw "An ACL failure left a temporary credential file behind."
    }

    $global:MiniCloudTestAclFailure = $false
    $global:MiniCloudTestCreateTokenCalls = 0
    $nearExpiryToken = New-MiniCloudTestToken -ExpiresAt ([DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds())
    Set-MiniCloudTestKubeconfig -Token $nearExpiryToken
    $originalContents = [System.IO.File]::ReadAllText($workerKubeconfig)
    $lockStream = [System.IO.File]::Open(
        $workerKubeconfig,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::None
    )
    $atomicFailureObserved = $false
    try {
        & (Join-Path $scriptsPath "New-MiniCloudWorkerKubeconfig.ps1") `
            -AdminKubeconfig $adminKubeconfig `
            -Context "fixture-context" `
            -OutputPath $workerKubeconfig | Out-Null
    } catch {
        $atomicFailureObserved = $_.Exception.Message -match "Atomic credential replacement failed"
    } finally {
        $lockStream.Dispose()
    }
    if (
        -not $atomicFailureObserved -or
        [System.IO.File]::ReadAllText($workerKubeconfig) -ne $originalContents
    ) {
        throw "A failed atomic replacement did not preserve the existing credential."
    }
    if (Get-ChildItem -LiteralPath $credentialsDirectory -Filter ".kubeconfig-*.tmp") {
        throw "A failed atomic replacement left a temporary credential file behind."
    }

    Write-Output "Credential renewal fixture tests passed: no-op, expiry, malformed/short-lived tokens, renewal and ACL failures, atomic replacement failure, recovery, and temp-file cleanup."
} finally {
    Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    Remove-Item Function:\kubectl -ErrorAction SilentlyContinue
    Remove-Item Function:\icacls -ErrorAction SilentlyContinue
    Remove-Variable MiniCloudTestTokenMode,MiniCloudTestAclFailure,MiniCloudTestCreateTokenCalls,MiniCloudTestAclArguments -Scope Global -ErrorAction SilentlyContinue
}
