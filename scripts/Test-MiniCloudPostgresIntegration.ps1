$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$postgresImage = "postgres:16.15-alpine"
$runId = [Guid]::NewGuid().ToString("N")
$runnerImage = "minicloud-phase8-it-runner:$runId"
$networkName = "minicloud-phase8-it-net-$runId"
$containerName = "minicloud-phase8-it-pg-$runId"
$runnerImageCreated = $false
$networkCreated = $false
$containerCreated = $false

function Invoke-DockerChecked {
    param([string[]]$Arguments)

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Docker command failed with exit code $exitCode.`n$output"
    }
    return $output
}

function Invoke-TestRunner {
    param(
        [string[]]$Command,
        [string]$ServiceRole
    )

    $arguments = @(
        "run", "--rm",
        "--network", $networkName,
        "--workdir", "/workspace/services/api",
        "--env", "APP_ENV=test",
        "--env", "SERVICE_ROLE=$ServiceRole",
        "--env", "DATABASE_URL=postgresql+psycopg://minicloud_api_runtime@pg/minicloud?application_name=minicloud_phase8_it",
        "--env", "MINICLOUD_TEST_WORKER_DATABASE_URL=postgresql+psycopg://minicloud_worker_runtime@pg/minicloud?application_name=minicloud_phase8_it_worker",
        "--env", "MIGRATION_DATABASE_URL=postgresql+psycopg://postgres@pg/minicloud?application_name=minicloud_phase8_it",
        "--env", "JWT_SECRET_KEY=test-only-minicloud-integration-signing-key",
        "--env", "MINICLOUD_TEST_ADMIN_DATABASE_URL=postgresql://postgres@pg/minicloud?application_name=minicloud_phase8_it_admin",
        "--env", "MINICLOUD_TEST_DATABASE_NAME=minicloud",
        "--env", "MINICLOUD_TEST_RUN_ID=$runId",
        "--env", "MINICLOUD_POSTGRES_INTEGRATION=1",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint", "python",
        $runnerImage
    ) + $Command
    Invoke-DockerChecked -Arguments $arguments | Write-Output
}

try {
    Invoke-DockerChecked -Arguments @("image", "inspect", $postgresImage) | Out-Null
    Invoke-DockerChecked -Arguments @(
        "build",
        "--file", (Join-Path $repositoryRoot "infra\docker\api.Dockerfile"),
        "--tag", $runnerImage,
        $repositoryRoot
    ) | Write-Output
    $runnerImageCreated = $true

    $serverVersion = Invoke-DockerChecked -Arguments @(
        "run", "--rm", "--entrypoint", "postgres", $postgresImage, "--version"
    )
    if ($serverVersion -notmatch "PostgreSQL\) 16\.15") {
        throw "The isolated test image is not PostgreSQL 16.15."
    }

    Invoke-DockerChecked -Arguments @("network", "create", "--internal", $networkName) | Out-Null
    $networkCreated = $true
    Invoke-DockerChecked -Arguments @(
        "run", "-d",
        "--name", $containerName,
        "--network", $networkName,
        "--network-alias", "pg",
        "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=1g",
        "--env", "POSTGRES_DB=minicloud",
        "--env", "POSTGRES_USER=postgres",
        "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
        $postgresImage
    ) | Out-Null
    $containerCreated = $true

    $readySamples = 0
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            & docker exec $containerName psql -X -At -U postgres -d minicloud -c "SELECT 1" *> $null
            if ($LASTEXITCODE -eq 0) {
                $readySamples++
                if ($readySamples -ge 3) {
                    break
                }
            } else {
                $readySamples = 0
            }
        }
        catch {
            # The official image restarts PostgreSQL after its initialization server exits.
            $readySamples = 0
        }
        Start-Sleep -Seconds 1
    }
    if ($readySamples -lt 3) {
        throw "The isolated PostgreSQL test server did not become ready."
    }

    $actualVersion = Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-At", "-U", "postgres",
        "-d", "minicloud", "-c", "SELECT current_setting('server_version_num')"
    )
    if ($actualVersion -ne "160015") {
        throw "The test server reported an unexpected PostgreSQL version."
    }
    Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "minicloud", "-c",
        "ALTER DATABASE minicloud SET minicloud.phase8_test_run TO '$runId'"
    ) | Out-Null

    $network = (Invoke-DockerChecked -Arguments @("network", "inspect", $networkName) | ConvertFrom-Json)[0]
    $container = (Invoke-DockerChecked -Arguments @("inspect", $containerName) | ConvertFrom-Json)[0]
    if (-not $network.Internal) {
        throw "The test network is not internal."
    }
    if ($container.NetworkSettings.Ports.'5432/tcp' -ne $null) {
        throw "The test container unexpectedly publishes a host port."
    }
    $mounts = @($container.Mounts)
    $dataMount = @($mounts | Where-Object { $_.Destination -eq "/var/lib/postgresql/data" })
    $tmpfsPaths = @($container.HostConfig.Tmpfs.PSObject.Properties.Name)
    if ($tmpfsPaths -notcontains "/var/lib/postgresql/data" -or $dataMount.Count -gt 0) {
        throw "The isolated PostgreSQL container does not use tmpfs-only database storage."
    }
    Write-Output "Isolation verified: internal Docker network, no published ports, tmpfs-only PostgreSQL data."
    Write-Output "Test database: minicloud; PostgreSQL: 16.15; run id: $runId"

    Invoke-TestRunner -ServiceRole "migrate" -Command @(
        "-m", "alembic", "upgrade", "20261012_revocation_cleanup"
    )

    $seedSql = @"
INSERT INTO public.users (id, email, password_hash, created_at)
VALUES (
    '00000000-0000-4000-8000-000000000011',
    'migration-backfill@minicloud.test',
    'test-only-hash',
    CURRENT_TIMESTAMP
);
INSERT INTO public.projects (
    id, name, slug, status, owner_id, created_at, updated_at
)
VALUES (
    '00000000-0000-4000-8000-000000000012',
    'Migration Backfill Project',
    'migration-backfill-project',
    'provisioning',
    '00000000-0000-4000-8000-000000000011',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
);
INSERT INTO public.deployments (
    id, project_id, owner_id, name, image, port, replicas, status,
    last_error, created_at, updated_at
)
VALUES (
    '00000000-0000-4000-8000-000000000013',
    '00000000-0000-4000-8000-000000000012',
    '00000000-0000-4000-8000-000000000011',
    'migration-backfill',
    'nginx:1.27.3-alpine',
    8080,
    1,
    'accepted',
    NULL,
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
);
INSERT INTO public.deployment_jobs (
    id, deployment_id, state, attempts, max_attempts, idempotency_key,
    created_at, updated_at, lease_expires_at, last_error
)
VALUES (
    '00000000-0000-4000-8000-000000000014',
    '00000000-0000-4000-8000-000000000013',
    'timed_out',
    1,
    3,
    'legacy-backfill-key',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP,
    NULL,
    NULL
);
"@
    Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "minicloud", "-c", $seedSql
    ) | Out-Null
    Write-Output "Seeded one legacy job before the project-scope migration in the disposable database."

    Invoke-TestRunner -ServiceRole "migrate" -Command @("-m", "alembic", "upgrade", "head")

    $backfilledProjectId = Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-At", "-U", "postgres",
        "-d", "minicloud", "-c",
        "SELECT project_id FROM public.deployment_jobs WHERE id = '00000000-0000-4000-8000-000000000014'"
    )
    if ($backfilledProjectId -ne "00000000-0000-4000-8000-000000000012") {
        throw "The project-scope migration did not backfill the legacy job's project ID."
    }
    Write-Output "Verified: the project-scope migration backfilled a pre-existing job."

    $backfilledStartedAt = Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-At", "-U", "postgres",
        "-d", "minicloud", "-c",
        "SELECT job.started_at = deployment.updated_at FROM public.deployment_jobs AS job JOIN public.deployments AS deployment ON deployment.id = job.deployment_id WHERE job.id = '00000000-0000-4000-8000-000000000014'"
    )
    if ($backfilledStartedAt -ne "t") {
        throw "The rollout-start migration did not backfill the active legacy job's timestamp."
    }
    Write-Output "Verified: the rollout-start migration backfilled the active job timestamp."

    $grantPath = Join-Path $repositoryRoot "scripts\grant_minicloud_runtime.sql"
    Invoke-DockerChecked -Arguments @("cp", $grantPath, "${containerName}:/tmp/grant_minicloud_runtime.sql") | Out-Null
    $preconditionOutput = @()
    try {
        $preconditionOutput = & docker exec $containerName psql -X -v ON_ERROR_STOP=1 -U postgres -d minicloud -f /tmp/grant_minicloud_runtime.sql 2>&1
    }
    catch {
        $preconditionOutput += $_.ToString()
    }
    $preconditionExit = $LASTEXITCODE
    if ($preconditionExit -eq 0 -or ($preconditionOutput -join "`n") -notmatch "Runtime role can CREATE or TEMP in minicloud") {
        throw "The grant script did not fail closed on the initial PUBLIC TEMP ACL."
    }
    $runtimeRoleAfterRollback = Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-At", "-U", "postgres",
        "-d", "minicloud", "-c",
        "SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname IN ('minicloud_api_runtime', 'minicloud_worker_runtime')"
    )
    if ($runtimeRoleAfterRollback -ne "0") {
        throw "The grant script precondition failure did not roll back role creation."
    }
    Write-Output "Verified: initial PUBLIC TEMP causes grant transaction failure and rollback."

    Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "minicloud", "-c",
        "REVOKE TEMPORARY ON DATABASE minicloud FROM PUBLIC"
    ) | Out-Null
    Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "minicloud", "-f", "/tmp/grant_minicloud_runtime.sql"
    ) | Write-Output
    Write-Output "Verified: grant script succeeds after removing PUBLIC TEMP only in the disposable database."

    Invoke-TestRunner -ServiceRole "api" -Command @(
        "-m", "pytest", "-p", "no:cacheprovider",
        "/workspace/services/api/integration_tests/test_postgresql_runtime.py", "-q"
    )

    Invoke-TestRunner -ServiceRole "migrate" -Command @("-m", "alembic", "upgrade", "head")
    Invoke-TestRunner -ServiceRole "migrate" -Command @(
        "-m", "alembic", "downgrade", "20261109_project_idem_scope"
    )
    $membershipConstraintAfterDowngrade = Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-At", "-U", "postgres",
        "-d", "minicloud", "-c",
        "SELECT count(*) FROM pg_catalog.pg_constraint WHERE conname = 'uq_project_memberships_project_user'"
    )
    if ($membershipConstraintAfterDowngrade -ne "0") {
        throw "The membership migration downgrade did not remove only its unique constraint."
    }

    Invoke-TestRunner -ServiceRole "migrate" -Command @("-m", "alembic", "upgrade", "head")
    Invoke-TestRunner -ServiceRole "migrate" -Command @("-m", "alembic", "upgrade", "head")
    $membershipConstraintAfterReUpgrade = Invoke-DockerChecked -Arguments @(
        "exec", $containerName, "psql", "-X", "-At", "-U", "postgres",
        "-d", "minicloud", "-c",
        "SELECT count(*) FROM pg_catalog.pg_constraint WHERE conname = 'uq_project_memberships_project_user'"
    )
    if ($membershipConstraintAfterReUpgrade -ne "1") {
        throw "The membership migration re-upgrade or repeated upgrade did not converge."
    }
    Write-Output "Verified: membership migration downgrade preserved data; re-upgrade and repeated upgrade succeeded."
}
finally {
    if ($containerCreated) {
        & docker rm -f $containerName *> $null
    }
    if ($networkCreated) {
        & docker network rm $networkName *> $null
    }
    if ($runnerImageCreated) {
        & docker image rm $runnerImage *> $null
    }
}
