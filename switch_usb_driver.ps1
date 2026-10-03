[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('status', 'capture-winusb', 'capture-pyusb', 'winusb', 'pyusb', 'altera')]
    [string]$Mode,

    [string]$WinUsbInf,
    [string]$PyUsbInf,
    [string]$InstanceId = 'USB\VID_09FB&PID_6001\91D28408',
    [string]$HardwareId = 'USB\VID_09FB&PID_6001',
    [string]$QuartusInf = 'C:\intelFPGA_lite\23.1std\quartus\drivers\usb-blaster\usbblstr.inf'
)

$ErrorActionPreference = 'Stop'

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

if ($Mode -eq 'status') {
    $device = Get-PnpDevice -InstanceId $InstanceId -ErrorAction Stop
    $service = (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName DEVPKEY_Device_Service).Data
    $infName = (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName DEVPKEY_Device_DriverInfPath).Data
    [PSCustomObject]@{
        Status = $device.Status
        Name = $device.FriendlyName
        InstanceId = $device.InstanceId
        Service = $service
        DriverInf = $infName
        DriverInfPath = Join-Path "$env:windir\INF" $infName
    }
    return
}

if ($Mode -in @('capture-winusb', 'capture-pyusb')) {
    $service = (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName DEVPKEY_Device_Service).Data
    $infName = (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName DEVPKEY_Device_DriverInfPath).Data
    if ($Mode -eq 'capture-winusb' -and $service -ne 'WinUSB') {
        throw "The selected device is using '$service', not WinUSB."
    }
    if ($Mode -eq 'capture-pyusb' -and $service -notin @('WinUSB', 'libusb0', 'libusbK')) {
        throw "The selected device is using '$service'. Expected WinUSB, libusb0, or libusbK."
    }
    $capturedInf = Join-Path "$env:windir\INF" $infName
    if (-not (Test-Path -LiteralPath $capturedInf)) {
        throw "Windows reports driver INF '$infName', but '$capturedInf' was not found."
    }
    Write-Output "PyUSB driver INF path: $capturedInf"
    Write-Output 'Pass this path to -PyUsbInf when switching back to the captured driver.'
    return
}

if ($Mode -ne 'altera') {
    $infArgument = if ($Mode -eq 'winusb') { $WinUsbInf } else { $PyUsbInf }
    if (-not $infArgument) {
        throw 'Supply -PyUsbInf with the signed INF path reported by -Mode capture-pyusb.'
    }
    $infPath = (Resolve-Path -LiteralPath $infArgument).Path
    if (-not ((Get-Content -LiteralPath $infPath -Raw) -like '*VID_09FB&PID_6001*')) {
        throw "The INF '$infPath' does not declare hardware ID VID_09FB&PID_6001."
    }
} else {
    $infPath = (Resolve-Path -LiteralPath $QuartusInf).Path
    if (-not ((Get-Content -LiteralPath $infPath -Raw) -like '*VID_09FB&PID_6001*')) {
        throw "The Quartus INF '$infPath' does not declare hardware ID VID_09FB&PID_6001."
    }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell window to change drivers.'
}

$rebootRequired = $false
$installed = [UsbBlasterDriverInstaller]::UpdateDriverForPlugAndPlayDevices(
    [IntPtr]::Zero,
    $HardwareId,
    $infPath,
    0x5,
    [ref]$rebootRequired
)

if (-not $installed) {
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    $detail = [ComponentModel.Win32Exception]::new($errorCode).Message
    throw "Windows could not install '$infPath' for '$HardwareId' (error $errorCode): $detail"
}

Write-Output "Installed driver from: $infPath"
if ($rebootRequired) {
    Write-Warning 'Windows reports that a reboot is required.'
}