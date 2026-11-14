$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$runId = [guid]::NewGuid().ToString("N")
$fixtureRoot = Join-Path $temporaryRoot ("minicloud-p0-07-descriptor-" + $runId)
$fixtureRootFull = [System.IO.Path]::GetFullPath($fixtureRoot)
$markerPath = Join-Path $fixtureRoot "MC-P0-07-OWNER.txt"
$nestedPath = Join-Path $fixtureRoot "nested-验证"
$fixturePath = Join-Path $nestedPath "descriptor-é.txt"
$backupPath = Join-Path $fixtureRoot "descriptor.backup"
$corruptPath = Join-Path $fixtureRoot "descriptor.corrupt"
$passCount = 0
$blockedCount = 0
$failedCount = 0
$originalCapture = $null
$originalAccess = $null
$fixtureRestored = $false

if (Test-Path -LiteralPath $fixtureRootFull) {
    throw "Unique descriptor fixture path already exists."
}
if (
    -not $fixtureRootFull.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $fixtureRootFull.StartsWith($repositoryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $fixtureRootFull.StartsWith((Join-Path $repositoryRoot ".local"), [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "Descriptor fixture path is not isolated from MiniCloud."
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public sealed class MiniCloudDescriptorCapture
{
    public uint ErrorCode;
    public IntPtr Descriptor;
    public IntPtr Owner;
    public IntPtr Group;
    public IntPtr Dacl;
    public IntPtr Sacl;
    public byte[] Bytes;
    public ushort Control;
    public uint Revision;
}

public static class MiniCloudDescriptorNative
{
    [DllImport("advapi32.dll", EntryPoint = "GetNamedSecurityInfoW", CharSet = CharSet.Unicode)]
    private static extern uint GetNamedSecurityInfo(
        string objectName,
        int objectType,
        uint securityInformation,
        out IntPtr owner,
        out IntPtr group,
        out IntPtr dacl,
        out IntPtr sacl,
        out IntPtr securityDescriptor);

    [DllImport("advapi32.dll", EntryPoint = "SetNamedSecurityInfoW", CharSet = CharSet.Unicode)]
    private static extern uint SetNamedSecurityInfo(
        string objectName,
        int objectType,
        uint securityInformation,
        IntPtr owner,
        IntPtr group,
        IntPtr dacl,
        IntPtr sacl);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetSecurityDescriptorControl(
        IntPtr securityDescriptor,
        out ushort control,
        out uint revision);

    [DllImport("advapi32.dll")]
    private static extern uint GetSecurityDescriptorLength(IntPtr securityDescriptor);

    [DllImport("kernel32.dll")]
    public static extern IntPtr LocalFree(IntPtr memory);

    public static MiniCloudDescriptorCapture Capture(string path, uint information)
    {
        var result = new MiniCloudDescriptorCapture();
        result.ErrorCode = GetNamedSecurityInfo(
            path, 1, information, out result.Owner, out result.Group,
            out result.Dacl, out result.Sacl, out result.Descriptor);
        if (result.ErrorCode != 0 || result.Descriptor == IntPtr.Zero)
            return result;

        uint length = GetSecurityDescriptorLength(result.Descriptor);
        if (length == 0 || length > Int32.MaxValue)
        {
            result.ErrorCode = 87;
            return result;
        }

        result.Bytes = new byte[(int)length];
        Marshal.Copy(result.Descriptor, result.Bytes, 0, (int)length);
        if (!GetSecurityDescriptorControl(result.Descriptor, out result.Control, out result.Revision))
            result.ErrorCode = (uint)Marshal.GetLastWin32Error();
        return result;
    }

    public static uint RestoreDacl(string path, IntPtr dacl, bool protectedDacl)
    {
        uint information = 0x00000004;
        information |= protectedDacl ? 0x80000000 : 0x20000000;
        return SetNamedSecurityInfo(
            path, 1, information, IntPtr.Zero, IntPtr.Zero, dacl, IntPtr.Zero);
    }

    public static uint RestoreSameOwnerAndGroup(string path, IntPtr owner, IntPtr group)
    {
        return SetNamedSecurityInfo(
            path, 1, 0x00000001 | 0x00000002,
            owner, group, IntPtr.Zero, IntPtr.Zero);
    }
}
"@

function Write-TestResult {
    param(
        [string]$TestId,
        [ValidateSet("PASS", "BLOCKED", "FAIL")]
        [string]$Status,
        [string]$Evidence
    )

    switch ($Status) {
        "PASS" { $script:passCount++ }
        "BLOCKED" { $script:blockedCount++ }
        "FAIL" { $script:failedCount++ }
    }
    Write-Output "$TestId|$Status|$Evidence"
}

function Get-AclBytes {
    param([System.Security.AccessControl.RawSecurityDescriptor]$Descriptor)

    if ($null -eq $Descriptor.DiscretionaryAcl) {
        return "NULL-DACL"
    }
    $bytes = New-Object byte[] $Descriptor.DiscretionaryAcl.BinaryLength
    $Descriptor.DiscretionaryAcl.GetBinaryForm($bytes, 0)
    return [System.BitConverter]::ToString($bytes)
}

function Get-Descriptor {
    param([uint32]$Information)

    $capture = [MiniCloudDescriptorNative]::Capture($fixturePath, $Information)
    if ($capture.ErrorCode -ne 0) {
        throw "GetNamedSecurityInfoW returned native error $($capture.ErrorCode)."
    }
    return $capture
}

try {
    New-Item -ItemType Directory -Path $fixtureRootFull | Out-Null
    [System.IO.File]::WriteAllText($markerPath, $runId)
    Write-Output "A-RUN_ID|$runId|FIXTURE=$fixtureRootFull"
    New-Item -ItemType Directory -Path $nestedPath | Out-Null
    [System.IO.File]::WriteAllText($fixturePath, "Synthetic MC-P0-07 descriptor fixture.")

    $originalCapture = Get-Descriptor -Information 0x00000007
    $originalAccess = Get-Acl -LiteralPath $fixturePath
    $originalDescriptor = New-Object System.Security.AccessControl.RawSecurityDescriptor(
        $originalCapture.Bytes, 0
    )
    $originalDacl = Get-AclBytes -Descriptor $originalDescriptor
    $originalOwner = $originalDescriptor.Owner.Value
    $originalGroup = $originalDescriptor.Group.Value
    $originalControl = [int]$originalDescriptor.ControlFlags
    $inheritedAceCount = @(
        $originalDescriptor.DiscretionaryAcl |
            Where-Object { ([int]$_.AceFlags -band 0x10) -ne 0 }
    ).Count

    Write-TestResult "A-UNICODE-NESTED-PATH" "PASS" "Native capture succeeded for a nested Unicode-path fixture."
    Write-TestResult "A-OWNER-GROUP-CAPTURE" "PASS" "Owner and group SIDs captured; control=0x$($originalCapture.Control.ToString('X4'))."
    Write-TestResult "A-DACL-CAPTURE" "PASS" "DACL captured; ACE count=$($originalDescriptor.DiscretionaryAcl.Count); inherited ACE count=$inheritedAceCount."
    Write-TestResult "A-DACL-ACE-ORDER" "PASS" "Original ACE byte sequence recorded for exact post-restore comparison."
    Write-TestResult "A-DACL-CONTROL" "PASS" "Control flags captured; DACL protected=$(([int]$originalControl -band 0x1000) -ne 0)."

    $fullCapture = [MiniCloudDescriptorNative]::Capture($fixturePath, 0x0000000F)
    if ($fullCapture.ErrorCode -eq 0) {
        Write-TestResult "A-SACL-CAPTURE" "PASS" "Full owner/group/DACL/SACL capture succeeded."
    } elseif ($fullCapture.ErrorCode -eq 1314) {
        Write-TestResult "A-SACL-CAPTURE" "BLOCKED" "GetNamedSecurityInfoW returned ERROR_PRIVILEGE_NOT_HELD (1314)."
    } else {
        Write-TestResult "A-SACL-CAPTURE" "FAIL" "GetNamedSecurityInfoW returned native error $($fullCapture.ErrorCode)."
    }

    $sameIdentityResult = [MiniCloudDescriptorNative]::RestoreSameOwnerAndGroup(
        $fixturePath, $originalCapture.Owner, $originalCapture.Group
    )
    if ($sameIdentityResult -eq 0) {
        $sameIdentityCheck = Get-Descriptor -Information 0x00000003
        $sameIdentityDescriptor = New-Object System.Security.AccessControl.RawSecurityDescriptor(
            $sameIdentityCheck.Bytes, 0
        )
        if (
            $sameIdentityDescriptor.Owner.Value -eq $originalOwner -and
            $sameIdentityDescriptor.Group.Value -eq $originalGroup
        ) {
            Write-TestResult "A-OWNER-GROUP-SAME-IDENTITY-SET" "PASS" "Reapplying existing owner/group SIDs succeeded and values matched."
        } else {
            Write-TestResult "A-OWNER-GROUP-SAME-IDENTITY-SET" "FAIL" "Owner/group values differed after same-identity set."
        }
        [void][MiniCloudDescriptorNative]::LocalFree($sameIdentityCheck.Descriptor)
    } else {
        Write-TestResult "A-OWNER-GROUP-SAME-IDENTITY-SET" "BLOCKED" "SetNamedSecurityInfoW returned native error $sameIdentityResult; no different identity was attempted."
    }

    $currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $changedAccess = Get-Acl -LiteralPath $fixturePath
    $testRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $currentIdentity,
        [System.Security.AccessControl.FileSystemRights]::ReadAttributes,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $changedAccess.AddAccessRule($testRule)
    Set-Acl -LiteralPath $fixturePath -AclObject $changedAccess
    $mutatedCapture = Get-Descriptor -Information 0x00000007
    $mutatedDescriptor = New-Object System.Security.AccessControl.RawSecurityDescriptor(
        $mutatedCapture.Bytes, 0
    )
    $mutatedDacl = Get-AclBytes -Descriptor $mutatedDescriptor
    if ($mutatedDacl -ne $originalDacl) {
        Write-TestResult "A-DACL-MUTATION" "PASS" "Synthetic explicit ACE changed the disposable DACL."
    } else {
        Write-TestResult "A-DACL-MUTATION" "FAIL" "Synthetic ACE did not change the captured DACL."
    }

    $wasProtected = ([int]$originalControl -band 0x1000) -ne 0
    $restoreResult = [MiniCloudDescriptorNative]::RestoreDacl(
        $fixturePath, $originalCapture.Dacl, $wasProtected
    )
    if ($restoreResult -eq 0) {
        $restoredCapture = Get-Descriptor -Information 0x00000007
        $restoredDescriptor = New-Object System.Security.AccessControl.RawSecurityDescriptor(
            $restoredCapture.Bytes, 0
        )
        if (
            (Get-AclBytes -Descriptor $restoredDescriptor) -eq $originalDacl -and
            $restoredDescriptor.Owner.Value -eq $originalOwner -and
            $restoredDescriptor.Group.Value -eq $originalGroup -and
            [int]$restoredDescriptor.ControlFlags -eq $originalControl
        ) {
            $fixtureRestored = $true
            Write-TestResult "A-DACL-RESTORE" "PASS" "DACL bytes/order, owner/group values, and returned control flags match the captured fixture."
        } else {
            Write-TestResult "A-DACL-RESTORE" "FAIL" "Restored descriptor did not match captured DACL/identity/control values."
        }
        [void][MiniCloudDescriptorNative]::LocalFree($restoredCapture.Descriptor)

        $repeatResult = [MiniCloudDescriptorNative]::RestoreDacl(
            $fixturePath, $originalCapture.Dacl, $wasProtected
        )
        if ($repeatResult -eq 0) {
            Write-TestResult "A-REPEATED-DACL-RESTORE" "PASS" "A second restore call returned success."
        } else {
            Write-TestResult "A-REPEATED-DACL-RESTORE" "FAIL" "Second restore returned native error $repeatResult."
        }
    } else {
        Write-TestResult "A-DACL-RESTORE" "FAIL" "SetNamedSecurityInfoW returned native error $restoreResult."
    }
    [void][MiniCloudDescriptorNative]::LocalFree($mutatedCapture.Descriptor)

    [System.IO.File]::WriteAllBytes($backupPath, $originalCapture.Bytes)
    $backupAcl = Get-Acl -LiteralPath $backupPath
    $backupAcl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($backupAcl.Access)) {
        [void]$backupAcl.RemoveAccessRuleSpecific($rule)
    }
    $backupAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $currentIdentity,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )))
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $administratorsSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    foreach ($sid in @($systemSid, $administratorsSid)) {
        $backupAcl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )))
    }
    Set-Acl -LiteralPath $backupPath -AclObject $backupAcl
    $verifiedBackupAcl = Get-Acl -LiteralPath $backupPath
    if (
        $verifiedBackupAcl.AreAccessRulesProtected -and
        @($verifiedBackupAcl.Access | Where-Object {
            $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $currentIdentity.Value -and
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            $_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl
        }).Count -gt 0
    ) {
        Write-TestResult "A-BACKUP-FILE-DACL" "PASS" "Synthetic descriptor backup has protected inheritance and current-operator Full Control."
    } else {
        Write-TestResult "A-BACKUP-FILE-DACL" "FAIL" "Synthetic backup DACL did not meet the checked protection condition."
    }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $expectedHash = [System.BitConverter]::ToString($sha.ComputeHash($originalCapture.Bytes))
        $storedHash = [System.BitConverter]::ToString(
            $sha.ComputeHash([System.IO.File]::ReadAllBytes($backupPath))
        )
        if ($storedHash -eq $expectedHash) {
            Write-TestResult "A-BACKUP-INTEGRITY" "PASS" "Stored synthetic descriptor hash matches its captured bytes."
        } else {
            Write-TestResult "A-BACKUP-INTEGRITY" "FAIL" "Stored synthetic descriptor hash differs."
        }

        $corruptBytes = [byte[]]$originalCapture.Bytes.Clone()
        $corruptBytes[0] = [byte]($corruptBytes[0] -bxor 1)
        [System.IO.File]::WriteAllBytes($corruptPath, $corruptBytes)
        $corruptHash = [System.BitConverter]::ToString($sha.ComputeHash($corruptBytes))
        if ($corruptHash -ne $expectedHash) {
            Write-TestResult "A-CORRUPTION-DETECTION" "PASS" "Corrupted synthetic copy is rejected by SHA-256 integrity comparison."
        } else {
            Write-TestResult "A-CORRUPTION-DETECTION" "FAIL" "Corruption did not change the hash."
        }
    }
    finally {
        $sha.Dispose()
    }

    $noClobber = $false
    try {
        $stream = [System.IO.File]::Open(
            $backupPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Dispose()
    } catch [System.IO.IOException] {
        $noClobber = $true
    }
    if ($noClobber) {
        Write-TestResult "A-NO-CLOBBER-PUBLICATION" "PASS" "CreateNew rejected an existing synthetic backup path."
    } else {
        Write-TestResult "A-NO-CLOBBER-PUBLICATION" "FAIL" "CreateNew unexpectedly opened an existing backup path."
    }

    if ($fullCapture.ErrorCode -ne 0) {
        $saclRestore = "not attempted: complete capture unavailable"
        Write-TestResult "A-SACL-RESTORE" "BLOCKED" $saclRestore
        Write-TestResult "A-PROTECTED-SACL-FLAGS" "BLOCKED" $saclRestore
        Write-TestResult "A-CROSS-IDENTITY-RESTORE" "BLOCKED" "No alternate identity or privilege elevation was used."
        Write-TestResult "A-PARTIAL-RESTORE-ROLLBACK" "BLOCKED" "A complete multi-component descriptor is unavailable; no atomicity claim is made."
        Write-TestResult "A-UNAUTHORIZED-RESTORE" "BLOCKED" "No alternate user token was available for an OS-level access-denial test."
    }
}
finally {
    if ($null -ne $originalCapture -and $originalCapture.Descriptor -ne [IntPtr]::Zero) {
        if (-not $fixtureRestored -and (Test-Path -LiteralPath $fixturePath)) {
            try {
                Set-Acl -LiteralPath $fixturePath -AclObject $originalAccess
                $recovery = [MiniCloudDescriptorNative]::Capture($fixturePath, 0x00000007)
                if ($recovery.ErrorCode -eq 0) {
                    $recovered = New-Object System.Security.AccessControl.RawSecurityDescriptor(
                        $recovery.Bytes, 0
                    )
                    $expected = New-Object System.Security.AccessControl.RawSecurityDescriptor(
                        $originalCapture.Bytes, 0
                    )
                    $fixtureRestored = (Get-AclBytes -Descriptor $recovered) -eq (Get-AclBytes -Descriptor $expected)
                    [void][MiniCloudDescriptorNative]::LocalFree($recovery.Descriptor)
                }
            } catch {
                Write-Output "A-FIXTURE-RECOVERY|FAIL|$($_.Exception.GetType().Name)"
            }
        }
        [void][MiniCloudDescriptorNative]::LocalFree($originalCapture.Descriptor)
    }

    if (Test-Path -LiteralPath $fixtureRootFull) {
        if ((Test-Path -LiteralPath $markerPath) -and [System.IO.File]::ReadAllText($markerPath) -eq $runId) {
            if ($fixtureRestored) {
                Remove-Item -LiteralPath $fixtureRootFull -Recurse -Force
                Write-Output "A-EXACT-FIXTURE-CLEANUP|$(if(Test-Path -LiteralPath $fixtureRootFull){'FAIL'}else{'PASS'})|Unique marker verified before exact-path cleanup."
            } else {
                Write-Output "A-EXACT-FIXTURE-CLEANUP|BLOCKED|Descriptor restoration was not verified; fixture retained at $fixtureRootFull."
                $script:blockedCount++
            }
        } else {
            Write-Output "A-EXACT-FIXTURE-CLEANUP|BLOCKED|Unique marker verification failed; fixture left untouched."
            $script:blockedCount++
        }
    }

    if ($null -ne $originalCapture -and $originalCapture.Descriptor -ne [IntPtr]::Zero) {
        Write-Output "A-TOTALS|PASS=$passCount|BLOCKED=$blockedCount|FAIL=$failedCount"
    }
}

if ($failedCount -gt 0) {
    exit 1
}
