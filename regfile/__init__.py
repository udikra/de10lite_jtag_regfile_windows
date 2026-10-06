"""Python API for the DE10-Lite 16 x 32-bit JTAG register file and 1 KiB buffer."""

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


def close() -> None:
    """Close the USB-Blaster connection, if it has been opened."""
    global _device
    device = _device
    _device = None
    if device is not None:
        device.close()


_register_atexit(close)

__all__ = ["BUFFER_BYTES", "close", "load_buf", "read_reg", "store_buf", "write_reg"]