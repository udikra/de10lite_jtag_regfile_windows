#!/usr/bin/env bash
# One-time setup (shows one UAC prompt): registers elevated scheduled tasks so
# usb_driver.sh can switch the USB-Blaster driver afterwards without prompts.
# Re-run after editing switch_usb_driver.ps1 to refresh the installed copy.
#
# Usage: ./usb_driver_setup.sh [--uninstall]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
    "") mode=install ;;
    --uninstall) mode=uninstall ;;
    *) echo "usage: $0 [--uninstall]" >&2; exit 2 ;;
esac

exec powershell.exe -NoProfile -ExecutionPolicy Bypass \
    -File "$(cygpath -w "$here/switch_usb_driver.ps1")" -Mode "$mode"
