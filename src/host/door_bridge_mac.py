#!/usr/bin/env python3

try:
    from .door_bridge_common import parse_args, run_bridge
except ImportError:
    from door_bridge_common import parse_args, run_bridge


MAC_PORT_PATTERNS = (
    "/dev/cu.usbmodem*",
    "/dev/tty.usbmodem*",
    "/dev/cu.usbserial*",
    "/dev/tty.usbserial*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/tty.SLAB_USBtoUART*",
    "/dev/cu.wchusbserial*",
    "/dev/tty.wchusbserial*",
)


def main() -> int:
    args = parse_args(
        "Bridge STM32 door session events from serial to the fridge Unix socket bus on macOS.",
        "Serial port path. Defaults to the first macOS USB serial device found.",
    )
    return run_bridge(
        args,
        MAC_PORT_PATTERNS,
        "No serial port found. Checked common macOS USB serial devices under /dev/cu.* and /dev/tty.*.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
