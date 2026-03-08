#!/usr/bin/env python3

try:
    from .door_bridge_common import parse_args, run_bridge
except ImportError:
    from door_bridge_common import parse_args, run_bridge


LINUX_PORT_PATTERNS = (
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
)


def main() -> int:
    args = parse_args(
        "Bridge STM32 door session events from serial to the fridge Unix socket bus on Linux.",
        "Serial port path. Defaults to the first /dev/ttyACM* or /dev/ttyUSB* found.",
    )
    return run_bridge(
        args,
        LINUX_PORT_PATTERNS,
        "No serial port found. Checked /dev/ttyACM* and /dev/ttyUSB*.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
