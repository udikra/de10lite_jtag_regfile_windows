# DE10-Lite JTAG Register File

This repository provides a minimal setup reference for runtime logic register access via USB-Blaster JTAG on DE10-Lite FPGA. It includes a module-level Python functions for a 16 x 32-bit register file implemented in the supplied `reg_file.sv`. Python talks directly to the classic Altera USB-Blaster using PyUSB; it does not start Quartus, `jtagd`, or another subprocess.

The verified setup is a DE10-Lite with MAX 10 `10M50DAF484C7G`, classic USB-Blaster USB ID `09FB:6001`, Quartus Prime Lite 23.1, 64-bit Anaconda Python 3.9.7, PyUSB 1.2.1, and `libusb-package` 1.0.30.0. This is the classic FT245/CPLD USB-Blaster protocol, not USB-Blaster II or FT232H MPSSE.

## Contents

- `reg_file.sv`: supplied synchronous 16 x 32-bit register-file module.
- `jtag_regfile.sv`: MAX 10 Virtual JTAG wrapper around that module.
- `de10lite_jtag_regfile.qpf` and `.qsf`: Quartus project and pin assignments.
- `de10lite_jtag_regfile.sof`: compiled image, included for convenience; rebuild it from the sources as described below.
- `regfile/`: installable Python package with `read_reg`, `write_reg`, and `close`.
- `usb_blaster.py`: direct classic USB-Blaster USB/JTAG transport.
- `verify_regfile.py` and `demo_regfile.py`: random all-register test and interactive demo.
- `switch_usb_driver.ps1`: read device status and switch between Quartus and the captured PyUSB driver.
- `test_*.py`: board-independent tests.

## Hardware And Software

Required hardware:

- DE10-Lite with MAX 10 `10M50DAF484C7G`.
- Its classic USB-Blaster interface, enumerating as `USB\VID_09FB&PID_6001`.
- A USB cable connected to the board's USB-Blaster port.

Required software:

- Windows 10 or 11, plus Git Bash.
- Quartus Prime Lite 23.1 with MAX 10 device support and USB-Blaster drivers installed. The tested installation path is `C:\intelFPGA_lite\23.1std`.
- 64-bit Python. The measured/tested environment used Anaconda Python 3.9.7 at `C:\Users\<user>\Anaconda3\python.exe`.
- Zadig, downloaded from [zadig.akeo.ie](https://zadig.akeo.ie/), for the one-time PyUSB driver binding.

The provided bitstream and JTAG wrapper target the device and board pinout above. Other boards or USB-Blaster models are not validated by this guide.

## Get The Sources

Extract the ZIP or clone the repository, then open Git Bash in the extracted project folder. For example:

```bash
cd /c/Users/<user>/Desktop/de10lite_jtag_regfile
```

Keep the project files together: the QSF references `reg_file.sv` and `jtag_regfile.sv` by their project-relative names.

## Install Python Package

Create an isolated environment from Git Bash:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -v
```

The editable install provides `from regfile import read_reg, write_reg, close` from this environment. The same versions can be installed without editable packaging using `python -m pip install -r requirements.txt`, while running Python from this project directory.

If `python` is not the interpreter you intend to use, create the environment with the full path to that Python executable. The original tested interpreter was:

```bash
C:/Users/<user>/Anaconda3/python.exe -m venv .venv
```

`libusb-package` supplies the native libusb-1.0 backend; the driver code also detects the libusb0 backend installed by Zadig's **libusb-win32** option.

## Build And Program The FPGA

Quartus programming requires its Altera driver. If the adapter is currently using WinUSB or libusb0, first restore the Quartus driver using the elevated PowerShell instructions below.

From Git Bash in the project directory, compile the sources:

```bash
C:/intelFPGA_lite/23.1std/quartus/bin64/quartus_sh.exe --flow compile de10lite_jtag_regfile
```

The build produces `de10lite_jtag_regfile.sof` in the project directory. Confirm the cable and FPGA are visible:

```bash
C:/intelFPGA_lite/23.1std/quartus/bin64/jtagconfig.exe
```

Then load the image into FPGA SRAM:

```bash
C:/intelFPGA_lite/23.1std/quartus/bin64/quartus_pgm.exe -m JTAG -c "USB-Blaster [USB-0]" -o "p;de10lite_jtag_regfile.sof"
```

Quartus should report `Configuration succeeded`. This operation programs volatile SRAM, not the board's configuration flash. Power loss or a board reset requires programming the SOF again.

## Bind The USB Driver For Python

Close Quartus and other JTAG applications before using Python. Zadig must bind the exact USB-Blaster device to a libusb-compatible driver; installing a driver package without replacing the active device driver is not enough.

1. Run Zadig as Administrator.
2. Choose **Options → List All Devices**.
3. Select **Altera USB-Blaster** and confirm the ID is `09FB:6001` (`USB\VID_09FB&PID_6001`). Do not select unrelated `0403:6015` FTDI adapters.
4. Select either **libusb-win32** or **WinUSB**, then click **Replace Driver** or **Install Driver**.

The verified run used Zadig's **libusb-win32** driver, which Windows reports as service `libusb0`. The PyUSB backend also supports WinUSB. Do not change this device's driver while a Quartus process is using it.

From Git Bash, check the active binding:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$PWD")\switch_usb_driver.ps1" -Mode status
```

Expected PyUSB services are `libusb0`, `WinUSB`, or `libusbK`. Capture the active PyUSB INF path (read-only; no elevation needed):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$PWD")\switch_usb_driver.ps1" -Mode capture-pyusb
```

Save the printed `C:\Windows\INF\oemNN.inf` path. Driver changes require an elevated PowerShell window. From that window, opened in the project directory:

```powershell
.\switch_usb_driver.ps1 -Mode altera
```

The `altera` mode restores the signed driver shipped with Quartus at `C:\intelFPGA_lite\23.1std\quartus\drivers\usb-blaster\usbblstr.inf`. To return to the captured PyUSB driver later:

```powershell
.\switch_usb_driver.ps1 -Mode pyusb -PyUsbInf 'C:\Windows\INF\oemNN.inf'
```

Replace `oemNN.inf` with the path captured from this machine. After each change, use `-Mode status` to check the bound service. Driver re-binding interrupts USB access; if the board resets or loses power, program the SOF again.

## Python API

The package opens one USB connection lazily on its first call and reuses it. Addresses must be integers from 0 to 15; data must be an unsigned 32-bit integer.

```python
from regfile import close, read_reg, write_reg

write_reg(3, 0x12345678)
value = read_reg(3)
print(f"0x{value:08X}")
close()
```

`close()` is optional at process exit, but is useful for explicit cleanup and tests. Opening the driver resets the JTAG TAP, which also asserts the register file's active-low reset; register contents are volatile.

## Random Test And Demo

Run the test from Git Bash while the SOF is loaded and a PyUSB-compatible driver is active:

```bash
python verify_regfile.py
```

It writes independent random 32-bit values to all 16 addresses, reads all addresses back, and exits with `PASS` only if every value matches.

Run the printed demonstration with:

```bash
python demo_regfile.py
```

It imports the same package functions, prints each random write and read, and exits nonzero on a mismatch. Run either script with Quartus and other JTAG clients closed.

## Protocol And Measured Timing

The wrapper instantiates MAX 10 Virtual JTAG with a one-bit virtual IR and routes a 40-bit data frame to the supplied synchronous register-file module. The frame is shifted LSB-first: address `[3:0]`, write-enable bit `4`, data `[36:5]`, and reserved `[39:37]`. A read selects the address on one scan, then captures the synchronous `data_out` on the following scan.

The throughput-focused transport caches the USER0/Virtual JTAG selection, uses byte-shift mode for the first 32 DR bits, packet-aligns each byte-shift command, and batches the two scans required for a synchronous read. On the verified board, `speed_test.py` completed 1,000 random write/read/verify pairs in 2.105 s (2.105 ms per pair, about 475 pairs/s). This measurement includes initial lazy connection setup and is specific to the tested PC, board, driver, and USB topology; it is not a hard timing guarantee.

The offline suite (`python -m unittest discover -v`) checks frame encoding and the package API without hardware. The random scripts perform the hardware-level test.