<#
Switch the classic USB-Blaster (USB\VID_09FB&PID_6001) between the Quartus
driver (altera) and a Zadig libusb-win32/WinUSB/libusbK driver (pyusb).

  -Mode status     Show connected/known USB-Blasters and their bound driver.
  -Mode altera     Bind the Quartus driver (usbblstr.inf).
  -Mode pyusb      Bind the Zadig-created PyUSB driver.
  -Mode install    One-time setup (UAC prompt): register prompt-free switch tasks.
  -Mode uninstall  Remove what install created (UAC prompt).

The oemNN.inf packages are found on every run from pnputil, so nothing needs to
be recorded. From an elevated shell altera/pyusb switch directly; otherwise they
run the scheduled task registered by install, which already holds admin rights.
Exit code is 0 on success and 1 on failure, so scripts can rely on it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('status', 'altera', 'pyusb', 'install', 'uninstall')]
    [string]$Mode,

    [string]$HardwareId = 'USB\VID_09FB&PID_6001',
    [int]$TimeoutSeconds = 60,

    # Internal: set by the scheduled task and by the self-elevated setup run.
    [switch]$Logged,
    [string]$TaskUser
)

$ErrorActionPreference = 'Stop'

$TaskPath = '\de10lite\'
$InstallDir = Join-Path $env:ProgramFiles 'de10lite_usb_switch'
$InstalledScript = Join-Path $InstallDir 'switch_usb_driver.ps1'
$LogDir = Join-Path $env:ProgramData 'de10lite_usb_switch'
$LogFile = if ($Logged) { Join-Path $LogDir "$Mode.log" } else { $null }

function Write-Log([string]$Message) {
    Write-Output $Message
    if ($LogFile) { Add-Content -LiteralPath $LogFile -Value $Message }
}

function Test-Admin {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DriverKind($OriginalName, $Provider, $Class) {
    if ($OriginalName -eq 'usbblstr.inf') { return 'altera' }
    if ($Class -match 'libusb' -or $Provider -in @('libusb-win32', 'libusbK', 'libwdi')) { return 'pyusb' }
    return 'other'
}

# One object per USB-Blaster Windows knows, with its bound and candidate drivers.
function Get-Blaster {
    $text = (& pnputil.exe /enum-devices /deviceid $HardwareId /drivers /format xml) | Out-String
    $xml = [xml]$text
    foreach ($dev in $xml.SelectNodes('/PnpUtil/Device')) {
        $drivers = @(foreach ($d in $dev.SelectNodes('MatchingDrivers/DriverName')) {
            $original = $d.SelectSingleNode('OriginalName').InnerText
            $provider = $d.SelectSingleNode('ProviderName').InnerText
            $class = $d.SelectSingleNode('ClassName').InnerText
            [PSCustomObject]@{
                Inf = $d.GetAttribute('DriverName')
                OriginalName = $original
                Provider = $provider
                Kind = Get-DriverKind $original $provider $class
            }
        })
        $boundNode = $dev.SelectSingleNode('DriverName')
        $bound = if ($boundNode) { $boundNode.InnerText } else { '' }
        $current = $drivers | Where-Object Inf -eq $bound | Select-Object -First 1
        [PSCustomObject]@{
            InstanceId = $dev.GetAttribute('InstanceId')
            Status = $dev.SelectSingleNode('Status').InnerText
            Driver = $bound
            OriginalName = $current.OriginalName
            Mode = if ($current) { $current.Kind } else { 'none' }
            Candidates = $drivers
        }
    }
}

function Get-ConnectedBlaster {
    $connected = @(Get-Blaster | Where-Object Status -ne 'Disconnected')
    if (-not $connected) { throw "No USB-Blaster ($HardwareId) is connected." }
    return $connected
}

function Install-Driver([string]$InfPath) {
    if (-not ('UsbBlasterDriverInstaller' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class UsbBlasterDriverInstaller
{
    [DllImport("newdev.dll", EntryPoint = "UpdateDriverForPlugAndPlayDevicesW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool UpdateDriverForPlugAndPlayDevices(
        IntPtr hwndParent,
        string hardwareId,
        string fullInfPath,
        uint installFlags,
        [MarshalAs(UnmanagedType.Bool)] out bool rebootRequired);
}
'@
    }
    $rebootRequired = $false
    # 0x5 = INSTALLFLAG_FORCE | INSTALLFLAG_NONINTERACTIVE: the two drivers rank
    # equally, so force is needed to replace the one currently bound.
    $installed = [UsbBlasterDriverInstaller]::UpdateDriverForPlugAndPlayDevices(
        [IntPtr]::Zero, $HardwareId, $InfPath, 0x5, [ref]$rebootRequired)
    if (-not $installed) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        $detail = [ComponentModel.Win32Exception]::new($errorCode).Message
        throw "Windows could not install '$InfPath' for '$HardwareId' (error $errorCode): $detail"
    }
    if ($rebootRequired) { Write-Warning 'Windows reports that a reboot is required.' }
}

function Invoke-SwitchTask([string]$Target) {
    $name = "usb-driver-$Target"
    $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Task '$TaskPath$name' not found. Run ./usb_driver_setup.sh once (or use an elevated shell)."
    }
    $before = (Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $name).LastRunTime
    Start-ScheduledTask -TaskPath $TaskPath -TaskName $name
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $name
        $state = (Get-ScheduledTask -TaskPath $TaskPath -TaskName $name).State
        # State can read Ready while LastTaskResult still holds 0x41301
        # (SCHED_S_TASK_RUNNING) or 0x41325 (SCHED_S_TASK_QUEUED).
        $done = $info.LastRunTime -ne $before -and $state -notin @('Running', 'Queued') -and
            $info.LastTaskResult -notin @(0x41301, 0x41325)
    } until ($done -or (Get-Date) -gt $deadline)
    $log = Join-Path $LogDir "$Target.log"
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log | Write-Output }
    if (-not $done) { throw "Task '$TaskPath$name' did not finish within $TimeoutSeconds s." }
    if ($info.LastTaskResult -ne 0) { throw "Task '$TaskPath$name' failed (result $($info.LastTaskResult))." }
}

function Switch-Driver([string]$Target) {
    $connected = Get-ConnectedBlaster
    if (-not ($connected | Where-Object Mode -ne $Target)) {
        Write-Log "USB-Blaster already uses the $Target driver ($($connected[0].Driver))."
        return
    }

    $viaTask = -not (Test-Admin)
    if ($viaTask) {
        Invoke-SwitchTask $Target
    } else {
        $candidate = $connected.Candidates | Where-Object Kind -eq $Target | Select-Object -First 1
        if (-not $candidate) {
            $hint = if ($Target -eq 'pyusb') {
                'Bind it once with Zadig (libusb-win32 or WinUSB).'
            } else {
                'Install the Quartus USB-Blaster driver (quartus\drivers\usb-blaster).'
            }
            throw "No $Target driver package matches $HardwareId. $hint"
        }
        $inf = Join-Path "$env:windir\INF" $candidate.Inf
        Write-Log "Installing $($candidate.Inf) ($($candidate.OriginalName), $($candidate.Provider))..."
        Install-Driver $inf
    }

    # Re-enumeration can briefly lag the driver install.
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Blaster | Where-Object { $_.Status -ne 'Disconnected' -and $_.Mode -ne $Target }) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    $wrong = @(Get-Blaster | Where-Object { $_.Status -ne 'Disconnected' -and $_.Mode -ne $Target })
    if ($wrong) { throw "Switch did not take effect: $($wrong[0].InstanceId) uses $($wrong[0].Driver) ($($wrong[0].Mode))." }
    # The task's log already reported this line.
    if (-not $viaTask) { Write-Log "USB-Blaster now uses the $Target driver." }
}

function Install-SwitchTask {
    if (-not $TaskUser) { $TaskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name }
    $sid = ([Security.Principal.NTAccount]$TaskUser).Translate([Security.Principal.SecurityIdentifier]).Value

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # Program Files is admin-write-only, so the elevated task cannot be hijacked
    # by editing a user-writable script.
    Copy-Item -LiteralPath $PSCommandPath -Destination $InstalledScript -Force
    Write-Log "Installed $InstalledScript"

    # S4U + Highest: runs elevated without a password and without a console window.
    $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    foreach ($target in 'altera', 'pyusb') {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$InstalledScript`" -Mode $target -Logged")
        Register-ScheduledTask -TaskPath $TaskPath -TaskName "usb-driver-$target" -Action $action `
            -Principal $principal -Settings $settings -Force | Out-Null

        # Let the (non-elevated) user start and query the task.
        $task = $service.GetFolder($TaskPath.TrimEnd('\')).GetTask("usb-driver-$target")
        $sddl = $task.GetSecurityDescriptor(4)
        if ($sddl -notmatch [regex]::Escape($sid)) {
            $task.SetSecurityDescriptor("$sddl(A;;FRFX;;;$sid)", 0)
        }
        Write-Log "Registered task $($TaskPath)usb-driver-$target for $TaskUser"
    }
    Write-Log 'Setup complete. Switch with: ./usb_driver.sh altera | pyusb | status'
}

function Uninstall-SwitchTask {
    foreach ($target in 'altera', 'pyusb') {
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName "usb-driver-$target" -Confirm:$false -ErrorAction SilentlyContinue
    }
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    try { $service.GetFolder('\').DeleteFolder($TaskPath.Trim('\'), 0) } catch { }
    Remove-Item -LiteralPath $InstallDir, $LogDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Log 'Removed the USB-Blaster switch tasks and installed script.'
}

# install/uninstall elevate themselves once; output comes back through a log.
function Invoke-Elevated {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $log = Join-Path $LogDir "$Mode.log"
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Mode $Mode -Logged -TaskUser `"$user`""
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru -WindowStyle Hidden
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log | Write-Output }
    if ($process.ExitCode -ne 0) { throw "Elevated $Mode failed (exit $($process.ExitCode))." }
}

try {
    if ($LogFile) {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        Set-Content -LiteralPath $LogFile -Value $null
    }
    switch ($Mode) {
        'status' {
            $devices = @(Get-Blaster)
            if (-not $devices) { Write-Output "No USB-Blaster ($HardwareId) known to Windows."; break }
            $devices | Format-Table InstanceId, Status, Mode, Driver, OriginalName -AutoSize | Out-String -Stream |
                Where-Object { $_.Trim() } | Write-Output
        }
        { $_ -in 'altera', 'pyusb' } { Switch-Driver $Mode }
        { $_ -in 'install', 'uninstall' } {
            if (-not (Test-Admin)) { Invoke-Elevated }
            elseif ($Mode -eq 'install') { Install-SwitchTask }
            else { Uninstall-SwitchTask }
        }
    }
    exit 0
} catch {
    $message = "ERROR: $($_.Exception.Message)"
    if ($LogFile) { Add-Content -LiteralPath $LogFile -Value $message }
    [Console]::Error.WriteLine($message)
    exit 1
}
