# PostgreSQL Backup and Restore Runbook

This is an operator procedure for isolated development deployments, not a
production backup guarantee. Frequency, retention, recovery point objective,
and recovery time objective must be set and verified by each operator.

## Safety boundaries

- Keep archives outside the repository in a restricted directory.
- Never restore over a persistent database as part of a verification drill.
- Use a separately named disposable database instance with no published port.
- Do not use `docker compose down -v`, prune volumes, or remove unidentified
  Docker resources.
- Do not put credentials, database URLs, or token values in commands, logs,
  tickets, or source control.
- Preserve the source archive and investigate if any validation fails.

## Create and validate a backup

Review `scripts\New-MiniCloudBackup.ps1` and provide an existing,
operator-controlled backup directory:

```powershell
.\scripts\New-MiniCloudBackup.ps1 `
  -BackupDirectory "<protected-backup-directory>"
```

The script creates a custom-format PostgreSQL dump and a metadata sidecar with
its size, SHA-256, server version, database name, and schema revision. It
validates the archive before completing. Store an independent copy of the
digest and metadata; a digest recomputed from an untrusted archive does not
establish provenance.

To verify a specific archive, obtain its expected digest from an independent
trusted record and run:

```powershell
.\scripts\Verify-MiniCloudBackup.ps1 `
  -BackupPath "<protected-backup-directory>\<archive>.dump" `
  -ExpectedSha256 "<trusted-sha256>"
```

The verifier checks the digest before and after restoring into isolated,
temporary storage. It disables container networking, mounts the archive
read-only, publishes no ports, and checks the schema revision and database
structure. A successful isolated restore is not an end-to-end service
recovery test.

## Retention

Retention is operator-invoked and is not a scheduled guarantee. Review the
script and dry-run output before applying it:

```powershell
.\scripts\Invoke-MiniCloudBackupRetention.ps1 `
  -BackupDirectory "<protected-backup-directory>"

.\scripts\Invoke-MiniCloudBackupRetention.ps1 `
  -BackupDirectory "<protected-backup-directory>" `
  -Apply
```

Only the script's documented backup archive and metadata patterns are
eligible. Confirm required recovery points and independent checksum records
before applying cleanup. By default, retention keeps the newest archive for
each of the seven most recent UTC dates and four most recent Monday-start UTC
weeks; date and week groups are ordered newest first, and equal timestamps are
resolved by descending archive name. Malformed or mismatched metadata stops
pruning. Apply removes each candidate archive and sidecar in separate file
operations; interruption can leave an orphan sidecar. Do not remove orphan
sidecars automatically—inspect the backup directory and recover metadata from
an independent copy where available.
The disposable fixture suite is
`scripts\Test-MiniCloudBackupRetention.ps1`; it does not validate any
operator's actual archives.

## Recovery drill

1. Identify the selected archive and independently stored expected digest.
2. Verify it in isolation. If validation fails, stop and retain the archive.
3. Restore only into a separately named disposable database with no published
   host port; never target a persistent deployment for a drill.
4. Validate the expected schema, required tables, constraints, and data.
5. Measure full service recovery separately, including application and
   Worker readiness and any required Kubernetes workloads.
6. Remove only disposable resources positively identified as created by that
   drill, after recording its outcome.

The repository includes automation for isolated backup validation, but no
production schedule, off-machine archive, achieved RPO/RTO, or production
recovery claim is made here.
