[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [uri]$ApiBaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$DeploymentId,
    [Parameter(Mandatory = $true)]
    [string]$TokenFile,
    [Parameter(Mandatory = $true)]
    [string]$Kubeconfig,
    [Parameter(Mandatory = $true)]
    [string]$KubernetesContext,
    [string]$Namespace = "minicloud",
    [ValidateRange(5, 3600)]
    [int]$TimeoutSeconds = 600,
    [ValidateRange(1, 60)]
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "MiniCloudDeploymentWaiter.psm1") -ErrorAction Stop

if (-not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) {
    throw "TokenFile does not exist."
}
if (-not (Test-Path -LiteralPath $Kubeconfig -PathType Leaf)) {
    throw "Kubeconfig does not exist."
}

$token = (Get-Content -LiteralPath $TokenFile -Raw).Trim()
if (-not $token) {
    throw "TokenFile is empty."
}
$headers = @{ Authorization = "Bearer $token" }
$operationUri = [uri]::new($ApiBaseUrl, "/api/v1/operations/$DeploymentId")
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
$lastSignature = $null
$lastOperation = $null
$lastKubernetesError = $null

while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $lastOperation = Invoke-RestMethod -Uri $operationUri -Headers $headers
    $signature = "$($lastOperation.status)|$($lastOperation.job_state)|$($lastOperation.kubernetes_resource_name)|$($lastOperation.last_error)"
    if ($signature -ne $lastSignature) {
        Write-Output "api_status=$($lastOperation.status) job_state=$($lastOperation.job_state) resource=$($lastOperation.kubernetes_resource_name) error=$($lastOperation.last_error)"
        $lastSignature = $signature
    }

    $resource = [string]$lastOperation.kubernetes_resource_name
    $deployment = $null
    if ([string]::IsNullOrWhiteSpace($resource)) {
        $lastKubernetesError = "API response did not include a Kubernetes resource name."
    } else {
        $kubernetesJson = & kubectl `
            --kubeconfig $Kubeconfig `
            --context $KubernetesContext `
            -n $Namespace `
            get deployment $resource `
            -o json 2>&1
        if ($LASTEXITCODE -eq 0) {
            $lastKubernetesError = $null
            $deployment = $kubernetesJson | ConvertFrom-Json
            $ready = 0
            $available = 0
            if ($null -ne $deployment.status.readyReplicas) {
                $ready = [int]$deployment.status.readyReplicas
            }
            if ($null -ne $deployment.status.availableReplicas) {
                $available = [int]$deployment.status.availableReplicas
            }
            Write-Output "kubernetes_ready=$ready/$($deployment.spec.replicas) available=$available observed=$($deployment.status.observedGeneration)/$($deployment.metadata.generation)"
        } else {
            $lastKubernetesError = ($kubernetesJson -join " ").Trim()
        }
    }

    if (Test-MiniCloudDeploymentReady -Operation $lastOperation -KubernetesDeployment $deployment) {
        Write-Output "result=ready"
        exit 0
    }
    if ($lastOperation.status -eq "failed") {
        Write-Output "result=terminal_failure"
        throw "Deployment reached terminal failure: $($lastOperation.last_error)"
    }
    Start-Sleep -Seconds $PollSeconds
}

Write-Output "result=timeout"
Write-Output "last_api_status=$($lastOperation.status)"
Write-Output "last_job_state=$($lastOperation.job_state)"
Write-Output "last_kubernetes_error=$lastKubernetesError"
throw "Deployment did not reach ready/completed before the $TimeoutSeconds second timeout."
