# DE10-Lite USB-Blaster Runtime Data Link

This repository provides a minimal setup reference for exchanging data with logic running on a DE10-Lite FPGA through the board's own USB-Blaster: the same cable that powers and programs the board, with no extra UART or adapter. It includes module-level Python functions for a 16 x 32-bit register file and a 256 x 32-bit (1 KiB) data buffer implemented by the Virtual JTAG bridge in `jtag_bridge.sv`. Python talks directly to the classic Altera USB-Blaster using PyUSB; it does not start Quartus, `jtagd`, or another subprocess.

The verified setup is a DE10-Lite with MAX 10 `10M50DAF484C7G`, classic USB-Blaster USB ID `09FB:6001`, Quartus Prime Lite 23.1, 64-bit Anaconda Python 3.9.7, PyUSB 1.2.1, and `libusb-package` 1.0.30.0. This is the classic FT245/CPLD USB-Blaster protocol, not USB-Blaster II or FT232H MPSSE.

## Contents

- `jtag_bridge.sv`: MAX 10 Virtual JTAG bridge with 8 JTAG-written (JWSR) and 8 system-written (SWJR) registers, crossing into the system clock with a req/ack handshake, and a 1 KiB buffer streamed over JTAG.
- `buffer_ram.sv`: 256 x 32-bit true dual-port RAM (TCK port and system-clock port).
- `system_stub.sv`: placeholder for the main design; drives SWJR register `8 + n` with `~JWSR[n]`, and inverts the whole buffer on each JTAG write to JWSR 7.
- `top.sv`: top level (50 MHz board clock as the system clock, `KEY[0]` system reset).
- `top.sdc`: timing constraints; the JTAG and system clocks are asynchronous groups.
- `top.qpf` and `top.qsf`: Quartus project and pin assignments.
- `top.sof`: compiled image, included for convenience; rebuild it from the sources as described below.
- `regfile/`: installable Python package with `read_reg`, `write_reg`, `store_buf`, `load_buf`, and `close`.
- `usb_blaster.py`: direct classic USB-Blaster USB/JTAG transport.
- `verify_regfile.py` and `demo_regfile.py`: random all-register test and interactive demo.
- `verify_buffer.py`: random buffer store/load test, system-port check, and transfer timing.
- `usb_driver_setup.sh` and `usb_driver.sh`: one-time setup, then prompt-free switching between the Quartus and PyUSB drivers.
- `switch_usb_driver.ps1`: the PowerShell implementation behind both scripts.
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
cd /c/Users/<user>/Desktop/de10lite_usb_blaster_runtime_data_link
```

Keep the project files together: the QSF references `buffer_ram.sv`, `jtag_bridge.sv`, `system_stub.sv`, `top.sv`, and `top.sdc` by their project-relative names.

## Install Python Package

Create an isolated environment from Git Bash:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -v
```

The editable install provides `from regfile import read_reg, write_reg, store_buf, load_buf, close` from this environment. The same versions can be installed without editable packaging using `python -m pip install -r requirements.txt`, while running Python from this project directory.

If `python` is not the interpreter you intend to use, create the environment with the full path to that Python executable. The original tested interpreter was:

```bash
C:/Users/<user>/Anaconda3/python.exe -m venv .venv
```

`libusb-package` supplies the native libusb-1.0 backend; the driver code also detects the libusb0 backend installed by Zadig's **libusb-win32** option.

## Build And Program The FPGA

Quartus programming requires its Altera driver. If the adapter is currently using WinUSB or libusb0, first restore the Quartus driver with `./usb_driver.sh altera` (see below).

From Git Bash in the project directory, compile the sources:

```bash
C:/intelFPGA_lite/23.1std/quartus/bin64/quartus_sh.exe --flow compile top
```

The build produces `top.sof` in the project directory. Confirm the cable and FPGA are visible:

```bash
C:/intelFPGA_lite/23.1std/quartus/bin64/jtagconfig.exe
```

Then load the image into FPGA SRAM:

```bash
C:/intelFPGA_lite/23.1std/quartus/bin64/quartus_pgm.exe -m JTAG -c "USB-Blaster [USB-0]" -o "p;top.sof"
```

Quartus should report `Configuration succeeded`. This operation programs volatile SRAM, not the board's configuration flash. Power loss or a board reset requires programming the SOF again.

## Bind The USB Driver For Python

Close Quartus and other JTAG applications before using Python. Zadig must bind the exact USB-Blaster device to a libusb-compatible driver; installing a driver package without replacing the active device driver is not enough.

1. Run Zadig as Administrator.
2. Choose **Options → List All Devices**.
3. Select **Altera USB-Blaster** and confirm the ID is `09FB:6001` (`USB\VID_09FB&PID_6001`). Do not select unrelated `0403:6015` FTDI adapters.
4. Select either **libusb-win32** or **WinUSB**, then click **Replace Driver** or **Install Driver**.

The verified run used Zadig's **libusb-win32** driver, which Windows reports as service `libusb0`. The PyUSB backend also supports WinUSB.

Zadig leaves its driver package in the Windows driver store, so it is needed only once per machine. After that, switching between the two drivers is done from Git Bash as described next; `./usb_driver.sh status` should now report mode `pyusb`.

## Switch Drivers Without Admin Prompts

Changing a device driver needs administrator rights. Run the one-time setup once (it shows a single UAC prompt):

```bash
./usb_driver_setup.sh
```

It copies `switch_usb_driver.ps1` to `C:\Program Files\de10lite_usb_switch\` and registers two scheduled tasks, `\de10lite\usb-driver-altera` and `\de10lite\usb-driver-pyusb`, which run that copy with highest privileges for the current user and without a window. The copy sits in an admin-only folder so a user-writable file can never run elevated; re-run the setup after editing `switch_usb_driver.ps1`. Remove everything with `./usb_driver_setup.sh --uninstall`.

Then switch from Git Bash, or from any other script, with no prompt:

```bash
./usb_driver.sh altera   # Quartus driver, for quartus_pgm / jtagconfig
./usb_driver.sh pyusb    # Zadig libusb driver, for the Python package
./usb_driver.sh status   # show the bound driver
```

`usb_driver.sh` waits until the switch has taken effect and exits 0 on success, including when the requested driver is already bound, and nonzero otherwise, so it can gate a script step, for example `./usb_driver.sh pyusb && python verify_regfile.py`. The board must be connected. On the verified PC a switch takes about 4 s.

No `oemNN.inf` name needs to be recorded. Each run asks `pnputil /enum-devices /deviceid "USB\VID_09FB&PID_6001" /drivers` which driver packages match the USB-Blaster: `usbblstr.inf` from Quartus is the `altera` driver, and the Zadig libusb-win32, libusbK, or WinUSB package is the `pyusb` driver. From an already elevated shell the same commands switch directly, without the scheduled tasks.

Do not switch drivers while a Quartus process is using the cable. Driver re-binding interrupts USB access; if the board resets or loses power, program the SOF again.

## Python API

The package opens one USB connection lazily on its first call and reuses it. `read_reg` accepts addresses 0 to 15 and `write_reg` accepts 0 to 7 (8 to 15 are written by the system); data must be an unsigned 32-bit integer.

```python
from regfile import close, read_reg, write_reg

write_reg(3, 0x12345678)
value = read_reg(3)
print(f"0x{value:08X}")
close()
```

The buffer is read and written as a whole from address 0, each call being a single JTAG scan:

```python
from regfile import BUFFER_BYTES, load_buf, store_buf

store_buf(list(range(256)) * 4)   # up to BUFFER_BYTES (1024) bytes, list of 0..255 or bytes
data = load_buf()                 # list of BUFFER_BYTES ints; load_buf(n) reads the first n
```

Words are little-endian: byte `4k` is bits 7..0 of word `k`. `store_buf` zero-pads a trailing partial word. The system side must not use the buffer while JTAG transfers it; the handshake for that is left to the register file.

`close()` is optional at process exit, but is useful for explicit cleanup and tests. Opening the driver resets the JTAG TAP, which does not clear the registers; pressing `KEY[0]` resets the system clock domain and clears them. Register contents are volatile.

## Random Test And Demo

Run the test from Git Bash while the SOF is loaded and a PyUSB-compatible driver is active:

```bash
./usb_driver.sh pyusb
python verify_regfile.py
```

It writes independent random 32-bit values to all 16 addresses, reads all addresses back, and exits with `PASS` only if every value matches.

Check the buffer with:

```bash
python verify_buffer.py
```

It stores and loads random data of several lengths interleaved with register accesses, writes JWSR 7 so `system_stub.sv` inverts the buffer through its system port and checks the inverse, then times 100 full-buffer stores and loads.

Run the printed demonstration with:

```bash
python demo_regfile.py
```

It imports the same package functions, prints each random write and read, and exits nonzero on a mismatch. Run either script with Quartus and other JTAG clients closed.

## Protocol And Measured Timing

The bridge instantiates MAX 10 Virtual JTAG with a one-bit virtual IR and a 40-bit data frame, shifted LSB-first. Requests carry address `[3:0]`, write-enable bit `4`, data `[36:5]`, reserved `[38:37]`, and a NOP bit `39` that suppresses the command. Responses carry read data `[36:5]`, a done flag `37`, and an overrun flag `38`.

Addresses 0..7 (JWSR) are written over JTAG and read by the system; addresses 8..15 (SWJR) are written by the system and read over JTAG. JTAG can read all 16, and JTAG writes to 8..15 are ignored. The registers live in the system clock domain. Because TCK only runs during scans, Update-DR latches each command and toggles a request bit; the system side executes it after a 2-FF synchronizer and toggles an acknowledge bit back. A read issues the command on one scan and captures the result with NOP scans until `done` is set. A command issued while the previous one is still in flight is dropped and sets `overrun`, which `read_reg` reports as an error. While the system domain is held in reset, commands wait and reads time out.

The throughput-focused transport caches the USER0/Virtual JTAG selection, uses byte-shift mode for the first 32 DR bits, packet-aligns each byte-shift command, and batches the two scans required for a synchronous read. On the verified board, `speed_test.py` completed 1,000 random write/read/verify pairs in 2.105 s (2.105 ms per pair, about 475 pairs/s). This measurement includes initial lazy connection setup and is specific to the tested PC, board, driver, and USB topology; it is not a hard timing guarantee.

The offline suite (`python -m unittest discover -v`) checks frame encoding and the package API without hardware. The random scripts perform the hardware-level test.

## Buffer Protocol And Timing

The virtual IR is 2 bits: 0 selects the 40-bit register frame, 1 `BUF_STORE`, 2 `BUF_LOAD`. The hub keeps a 4-bit node IR field, so the USER1 virtual IR scan is still 5 bits (`0b1_0000 | ir`). The driver caches the selected virtual IR and reselects it only when switching between registers and buffer.

Each buffer access is one DR scan of `32 * n` bits that transfers words `0..n-1`, LSB of word 0 first. The hardware keeps only a 32-bit shift register: on a store every 32nd TCK writes the completed word to RAM port A and advances the address; on a load Capture-DR loads word 0 and every 32nd TCK reloads the register with the next word, which the RAM has prefetched. RAM port B is the system interface (`buf_en`, `buf_rd`, `buf_addr`, `buf_wdata`, `buf_rdata`, clocked by `sys_clk`; reads return data the next cycle). The ports are not arbitrated. The depth is set by `BUF_ADDR_BITS` in `jtag_bridge.sv` (with `BUFFER_WORDS` in `usb_blaster.py`); the buffer uses one M9K pair per 256 words.

The driver sends all but the last byte in the USB-Blaster's byte-shift mode, as packet-aligned 62-byte commands, and bit-bangs the last byte to leave Shift-DR. A load reads TDO concurrently from a second thread, because the adapter's FIFO holds only a few hundred TDO bytes, and pads the response to whole USB packets so it is not held by the FTDI latency timer. On the verified board, `verify_buffer.py` measured 2.5 ms per 1 KiB store (about 400 KiB/s, 3.3 Mbit/s) and 3.9 ms per 1 KiB load (about 255 KiB/s), versus about 2 ms for a single register access. The JTAG clock alone accounts for about 1.4 ms per KiB at the adapter's 6 MHz byte-shift rate; the rest is USB transfer time.
