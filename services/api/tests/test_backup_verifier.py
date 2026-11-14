from pathlib import Path

ROOT = Path(__file__).parents[3]
POWERSHELL_SCRIPT = (ROOT / "scripts" / "Verify-MiniCloudBackup.ps1").read_text(
    encoding="utf-8"
)
CONTAINER_SCRIPT = (ROOT / "scripts" / "verify-minicloud-backup.sh").read_text(
    encoding="utf-8"
)


def test_backup_verifier_checks_checksum_and_uses_only_disposable_isolated_storage():
    assert "Get-FileHash" in POWERSHELL_SCRIPT
    assert '"--network", "none"' in POWERSHELL_SCRIPT
    assert '"/var/lib/postgresql/data:rw,noexec,nosuid,size=1g"' in POWERSHELL_SCRIPT
    assert "target=/backup.dump,readonly" in POWERSHELL_SCRIPT
    assert "tr -d '\\015'" in POWERSHELL_SCRIPT
    assert "restore_duration_seconds=" in POWERSHELL_SCRIPT
    assert '"--rm"' in POWERSHELL_SCRIPT
    assert '"--volume"' not in POWERSHELL_SCRIPT.lower()
    assert "pg_restore --exit-on-error --no-owner --no-privileges" in CONTAINER_SCRIPT
    assert "minicloud_pgdata" not in POWERSHELL_SCRIPT + CONTAINER_SCRIPT
