"""Python API for the DE10-Lite 16 x 32-bit JTAG register file."""

from atexit import register as _register_atexit
from typing import Optional

from usb_blaster import UsbBlaster


_device: Optional[UsbBlaster] = None


def _get_device() -> UsbBlaster:
    global _device
    if _device is None:
        _device = UsbBlaster()
    return _device


def write_reg(address: int, data: int) -> None:
    """Write one unsigned 32-bit value to register address 0..15."""
    _get_device().write_reg(address, data)


def read_reg(address: int) -> int:
    """Return the unsigned 32-bit value stored at register address 0..15."""
    return _get_device().read_reg(address)


def close() -> None:
    """Close the USB-Blaster connection, if it has been opened."""
    global _device
    device = _device
    _device = None
    if device is not None:
        device.close()


_register_atexit(close)

__all__ = ["close", "read_reg", "write_reg"]