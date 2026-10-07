"""Python API for the DE10-Lite JTAG register file, 16 KiB buffer and system access."""

from atexit import register as _register_atexit
from typing import Optional

from usb_blaster import BUFFER_BYTES, UsbBlaster


_device: Optional[UsbBlaster] = None


def _get_device() -> UsbBlaster:
    global _device
    if _device is None:
        _device = UsbBlaster()
    return _device


def write_reg(address: int, data: int) -> None:
    """Write one unsigned 32-bit value to a JTAG-written register, address 0..7."""
    _get_device().write_reg(address, data)


def read_reg(address: int) -> int:
    """Return the unsigned 32-bit value at address 0..15 (8..15 are system-written)."""
    return _get_device().read_reg(address)


def store_buf(data) -> None:
    """Write up to BUFFER_BYTES bytes (a list of 0..255 or bytes) to the buffer from address 0."""
    _get_device().store_buf(data)


def load_buf(length: int = BUFFER_BYTES) -> list:
    """Return the first `length` buffer bytes (default: all) as a list of ints."""
    return _get_device().load_buf(length)


def sys_wr(sys_addr: int, sys_data_in: int) -> None:
    """Write one 32-bit word to the system address space at sys_addr."""
    _get_device().sys_wr(sys_addr, sys_data_in)


def sys_rd(sys_addr: int) -> int:
    """Read one 32-bit word from the system address space at sys_addr."""
    return _get_device().sys_rd(sys_addr)


def data_store(sys_start_addr: int, num_bytes: int, data_bytes_list) -> None:
    """Write the first num_bytes of data_bytes_list to system bytes from sys_start_addr."""
    data = bytes(data_bytes_list[:num_bytes])
    if len(data) != num_bytes:
        raise ValueError(f"data_bytes_list holds fewer than {num_bytes} bytes")
    _get_device().data_store(sys_start_addr, data)


def data_load(sys_start_addr: int, num_bytes: int, data_bytes_list: Optional[list] = None) -> list:
    """Read num_bytes system bytes from sys_start_addr.

    Returns them as a list of ints; when data_bytes_list is given, its
    contents are replaced with them too.
    """
    data = _get_device().data_load(sys_start_addr, num_bytes)
    if data_bytes_list is None:
        return data
    data_bytes_list[:] = data
    return data_bytes_list


def close() -> None:
    """Close the USB-Blaster connection, if it has been opened."""
    global _device
    device = _device
    _device = None
    if device is not None:
        device.close()


_register_atexit(close)

__all__ = [
    "BUFFER_BYTES",
    "close",
    "data_load",
    "data_store",
    "load_buf",
    "read_reg",
    "store_buf",
    "sys_rd",
    "sys_wr",
    "write_reg",
]
