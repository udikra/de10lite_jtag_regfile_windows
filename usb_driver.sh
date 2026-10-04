#!/usr/bin/env bash
# Switch the USB-Blaster driver without a UAC prompt (after usb_driver_setup.sh).
#   altera  Quartus driver, for quartus_pgm / jtagconfig
#   pyusb   Zadig libusb driver, for the Python regfile package
#   status  show the bound driver
# Exits 0 on success (including "already bound"), nonzero on failure.
#
# Usage: ./usb_driver.sh altera|pyusb|status
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
    altera | pyusb | status) ;;
    *) echo "usage: $0 altera|pyusb|status" >&2; exit 2 ;;
esac

exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
    -File "$(cygpath -w "$here/switch_usb_driver.ps1")" -Mode "$1"
