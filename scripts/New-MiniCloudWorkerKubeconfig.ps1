param(
    [Parameter(Mandatory = $true)]
    [string]$AdminKubeconfig,
    [Parameter(Mandatory = $true)]
    [string]$Context,
    [string]$Namespace = "minicloud",
    [string]$OutputPath = ".local\worker-credentials\kubeconfig",
    [string]$ApiServer,
    [ValidateRange(5, 55)]
    [int]$RenewBeforeMinutes = 15
)

$ErrorActionPreference = "Stop"
$resolvedAdminKubeconfig = (Resolve-Path -LiteralPath $AdminKubeconfig).Path
$selectedJson = & kubectl --kubeconfig $resolvedAdminKubeconfig --context $Context config view --raw --minify -o json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the explicitly selected Kubernetes context."
}

$selectedConfig = ($selectedJson -join "`n") | ConvertFrom-Json
$selectedContext = $selectedConfig.contexts[0].context
$selectedCluster = $selectedConfig.clusters | Where-Object { $_.name -eq $selectedContext.cluster } | Select-Object -First 1
if (-not $selectedCluster -or -not $selectedCluster.cluster.server -or -not $selectedCluster.cluster.'certificate-authority-data') {
    throw "The selected context must provide a server and embedded certificate authority data."
}

$namespacePattern = "^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$"
if ($Namespace.Length -gt 63 -or $Namespace -notmatch $namespacePattern) {
    throw "Namespace must be a valid lowercase DNS label of at most 63 characters."
}

$apiServerUrl = $selectedCluster.cluster.server
if ($ApiServer) {
    $parsedApiServer = $null
    if (-not [Uri]::TryCreate($ApiServer, [UriKind]::Absolute, [ref]$parsedApiServer) -or
        $parsedApiServer.Scheme -ne "https" -or
        $parsedApiServer.UserInfo -or
        $parsedApiServer.Query -or
        $parsedApiServer.Fragment) {
        throw "ApiServer must be an absolute HTTPS URL without credentials, query, or fragment."
    }
    $apiServerUrl = $parsedApiServer.AbsoluteUri.TrimEnd("/")
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$localRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot ".local")).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)
} else {
    $resolvedOutputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputPath))
}
if (-not $resolvedOutputPath.StartsWith($localRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Worker credentials must be stored inside the repository's ignored .local directory."
}

$token = & kubectl --kubeconfig $resolvedAdminKubeconfig --context $Context create token minicloud-worker --namespace $Namespace --duration=1h
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "Unable to obtain a short-lived token for the MiniCloud worker ServiceAccount."
}
$tokenValue = ($token -join "").Trim()
$tokenParts = $tokenValue.Split(".")
if ($tokenParts.Length -ne 3) {
    throw "The Kubernetes API returned a malformed ServiceAccount token."
}
$payloadPart = $tokenParts[1].Replace("-", "+").Replace("_", "/")
$payloadPart += "=" * ((4 - $payloadPart.Length % 4) % 4)
try {
    $tokenClaims = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payloadPart)) | ConvertFrom-Json
    if (-not $tokenClaims.exp) {
        throw "The Kubernetes API returned a ServiceAccount token without an expiration."
    }
    $tokenExpiry = [DateTimeOffset]::FromUnixTimeSeconds([long]$tokenClaims.exp).ToUniversalTime()
} catch {
    throw "The Kubernetes API returned an invalid or non-expiring ServiceAccount token."
}
if ($tokenExpiry -le [DateTimeOffset]::UtcNow.AddMinutes($RenewBeforeMinutes)) {
    throw "The Kubernetes API returned a ServiceAccount token with insufficient remaining lifetime."
}

$yaml = @"
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: $($selectedCluster.cluster.'certificate-authority-data')
    server: $apiServerUrl
  name: minicloud-cluster
contexts:
- context:
    cluster: minicloud-cluster
    user: minicloud-worker
    namespace: $Namespace
  name: minicloud-worker
current-context: minicloud-worker
users:
- name: minicloud-worker
  user:
    token: $tokenValue
"@

$outputDirectory = [System.IO.Path]::GetDirectoryName($resolvedOutputPath)
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
$temporaryPath = Join-Path $outputDirectory (".kubeconfig-" + [guid]::NewGuid().ToString("N") + ".tmp")
New-Item -ItemType File -Path $temporaryPath | Out-Null
& icacls $temporaryPath /inheritance:r /grant:r "$($env:USERNAME):(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $temporaryPath -Force
    throw "Unable to restrict the temporary worker credential file to the current Windows user."
}
try {
    $yaml | Set-Content -LiteralPath $temporaryPath -Encoding utf8
    if (-not ("MiniCloudAtomicFile" -as [type])) {
        Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
public static class MiniCloudAtomicFile {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool MoveFileEx(string existingFileName, string newFileName, int flags);
}
"@
    }
    if (-not [MiniCloudAtomicFile]::MoveFileEx($temporaryPath, $resolvedOutputPath, 9)) {
        $win32Error = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "Atomic credential replacement failed with Windows error $win32Error; the prior credential was preserved."
    }
} finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}
$token = $null
$tokenValue = $null
$tokenParts = $null
$tokenClaims = $null
$payloadPart = $null
$selectedJson = $null
$selectedConfig = $null
$yaml = $null

Write-Output "Restricted worker kubeconfig atomically updated at $resolvedOutputPath for context minicloud-worker. Token expires at $($tokenExpiry.ToString('u'))."
