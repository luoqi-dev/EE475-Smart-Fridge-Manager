from __future__ import annotations

import argparse
import json
import socket


DEFAULT_SOCKET_PATH = "/tmp/fridge_bus.sock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send one session_id to detection_runner over the Unix socket.")
    parser.add_argument("session_id", help="Session id without the leading 'session_' directory prefix")
    parser.add_argument(
        "--socket-path",
        default=DEFAULT_SOCKET_PATH,
        help=f"Unix socket path. Default: {DEFAULT_SOCKET_PATH}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    message = {
        "type": "SESSION_CLOSED",
        "session_id": args.session_id,
    }

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(args.socket_path)
        client.sendall(json.dumps(message).encode("utf-8"))
    finally:
        client.close()

    print(f"Sent session_id: {args.session_id}")
    print(f"Socket path: {args.socket_path}")


if __name__ == "__main__":
    main()
