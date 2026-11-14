function Test-MiniCloudDeploymentReady {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [psobject]$Operation,
        [AllowNull()]
        [psobject]$KubernetesDeployment
    )

    if ($null -eq $Operation -or $null -eq $KubernetesDeployment) {
        return $false
    }

    $resourceName = [string]$Operation.kubernetes_resource_name
    if ([string]::IsNullOrWhiteSpace($resourceName)) {
        return $false
    }
    if ($Operation.status -cne "ready" -or $Operation.job_state -cne "completed") {
        return $false
    }
    if ($KubernetesDeployment.metadata.name -cne $resourceName) {
        return $false
    }

    $generation = $KubernetesDeployment.metadata.generation
    $observedGeneration = $KubernetesDeployment.status.observedGeneration
    $desiredReplicas = $KubernetesDeployment.spec.replicas
    $readyReplicas = $KubernetesDeployment.status.readyReplicas
    $availableReplicas = $KubernetesDeployment.status.availableReplicas
    if (
        $null -eq $generation -or
        $null -eq $observedGeneration -or
        $null -eq $desiredReplicas -or
        $null -eq $readyReplicas -or
        $null -eq $availableReplicas
    ) {
        return $false
    }

    try {
        return (
            [long]$observedGeneration -ge [long]$generation -and
            [long]$desiredReplicas -gt 0 -and
            [long]$readyReplicas -ge [long]$desiredReplicas -and
            [long]$availableReplicas -ge [long]$desiredReplicas
        )
    } catch {
        return $false
    }
}

Export-ModuleMember -Function Test-MiniCloudDeploymentReady
