#!/usr/bin/env python3

try:
    from .door_bridge_linux import main
except ImportError:
    from door_bridge_linux import main


if __name__ == "__main__":
    raise SystemExit(main())
