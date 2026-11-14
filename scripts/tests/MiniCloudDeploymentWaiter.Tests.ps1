Import-Module (Join-Path $PSScriptRoot "..\MiniCloudDeploymentWaiter.psm1") -Force

function New-TestReadyOperation {
    [pscustomobject]@{
        status = "ready"
        job_state = "completed"
        kubernetes_resource_name = "mc-12345678"
    }
}

function New-TestReadyDeployment {
    [pscustomobject]@{
        metadata = [pscustomobject]@{
            name = "mc-12345678"
            generation = 3
        }
        spec = [pscustomobject]@{
            replicas = 2
        }
        status = [pscustomobject]@{
            observedGeneration = 3
            readyReplicas = 2
            availableReplicas = 2
        }
    }
}

Describe "Test-MiniCloudDeploymentReady" {
    It "accepts a matching deployment with observed ready replicas" {
        Test-MiniCloudDeploymentReady `
            -Operation (New-TestReadyOperation) `
            -KubernetesDeployment (New-TestReadyDeployment) |
            Should Be $true
    }

    It "rejects API success when the Kubernetes lookup returned no resource" {
        Test-MiniCloudDeploymentReady `
            -Operation (New-TestReadyOperation) `
            -KubernetesDeployment $null |
            Should Be $false
    }

    It "rejects an API success with no canonical resource name" {
        $operation = New-TestReadyOperation
        $operation.kubernetes_resource_name = ""

        Test-MiniCloudDeploymentReady `
            -Operation $operation `
            -KubernetesDeployment (New-TestReadyDeployment) |
            Should Be $false
    }

    It "rejects stale Kubernetes generations" {
        $deployment = New-TestReadyDeployment
        $deployment.status.observedGeneration = 2

        Test-MiniCloudDeploymentReady `
            -Operation (New-TestReadyOperation) `
            -KubernetesDeployment $deployment |
            Should Be $false
    }

    It "rejects a deployment with fewer ready replicas than requested" {
        $deployment = New-TestReadyDeployment
        $deployment.status.readyReplicas = 1

        Test-MiniCloudDeploymentReady `
            -Operation (New-TestReadyOperation) `
            -KubernetesDeployment $deployment |
            Should Be $false
    }

    It "rejects a deployment with fewer available replicas than requested" {
        $deployment = New-TestReadyDeployment
        $deployment.status.availableReplicas = 1

        Test-MiniCloudDeploymentReady `
            -Operation (New-TestReadyOperation) `
            -KubernetesDeployment $deployment |
            Should Be $false
    }

    It "rejects a Kubernetes resource with a different name" {
        $deployment = New-TestReadyDeployment
        $deployment.metadata.name = "mc-87654321"

        Test-MiniCloudDeploymentReady `
            -Operation (New-TestReadyOperation) `
            -KubernetesDeployment $deployment |
            Should Be $false
    }

    It "does not accept API readiness before the job completes" {
        $operation = New-TestReadyOperation
        $operation.job_state = "progressing"

        Test-MiniCloudDeploymentReady `
            -Operation $operation `
            -KubernetesDeployment (New-TestReadyDeployment) |
            Should Be $false
    }
}
