"""
AI layer Socket interface: listen for SESSION_CLOSED from hardware layer, find corresponding frames directory and run inference.

Listen path: /tmp/fridge_bus.sock (Unix domain socket)
Message format: {"type": "SESSION_CLOSED", "session_id": "session_YYYYMMDD_HHMMSS"}

Hardware layer stores frames in: data/sessions/<session_id>/frames/ (001.jpg, 002.jpg, ...)
AI writes output to: data/sessions/<session_id>/vision.json

Use session_id queue (buffer), multiple SESSION_CLOSED will be processed in order without missing.

Usage (on Raspberry Pi):
  python socket_listener.py
"""
import json
import os
import sys
import threading
import queue
from pathlib import Path

# same directory as inference.py
MODEL_DIR = Path(__file__).resolve().parent
SOCKET_PATH = "/tmp/fridge_bus.sock"

# multiple session_id queues, processed by worker in order
SESSION_QUEUE = queue.Queue()


def worker():
    from inference import run_inference_for_session, SESSIONS_BASE_DIR
    while True:
        session_id = SESSION_QUEUE.get()
        if session_id is None:
            break
        try:
            frames_dir = SESSIONS_BASE_DIR / session_id / "frames"
            if not frames_dir.is_dir():
                print(f"Frames dir not found: {frames_dir}")
            else:
                print(f"Processing: {session_id} (queue size was {SESSION_QUEUE.qsize()})")
                run_inference_for_session(session_id, show=False)
                print(f"Done: {session_id}")
        except Exception as e:
            print(f"Error processing {session_id}: {e}")
        finally:
            SESSION_QUEUE.task_done()


def main():
    if sys.platform == "win32":
        print("Unix socket is for Linux (e.g. Raspberry Pi). On Windows use TCP or run inference manually.")
        sys.exit(1)

    import socket

    # start worker thread to process session_id in queue
    t = threading.Thread(target=worker, daemon=False)
    t.start()

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    print(f"AI layer listening on {SOCKET_PATH} (session_id buffer: queue)")

    try:
        while True:
            try:
                conn, _ = server.accept()
            except KeyboardInterrupt:
                break
            try:
                buf = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in buf or b"}" in buf:
                        break
                if not buf:
                    conn.close()
                    continue

                text = buf.decode("utf-8", errors="ignore").strip()
                if "\n" in text:
                    text = text.split("\n")[0]
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError as e:
                    print(f"Invalid JSON: {e}")
                    conn.close()
                    continue

                msg_type = msg.get("type")
                session_id = msg.get("session_id")

                if msg_type == "SESSION_CLOSED" and session_id:
                    if not session_id.startswith("session_"):
                        print(f"Invalid session_id format (expect session_YYYYMMDD_HHMMSS): {session_id}")
                    else:
                        SESSION_QUEUE.put(session_id)
                        print(f"SESSION_CLOSED enqueued: {session_id} (queue size: {SESSION_QUEUE.qsize()})")
                else:
                    print(f"Unknown or incomplete message: type={msg_type!r}, session_id={session_id!r}")

                conn.close()
            except Exception as e:
                print(f"Error handling request: {e}")
                try:
                    conn.close()
                except Exception:
                    pass
    finally:
        SESSION_QUEUE.put(None)
        t.join()
        server.close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        print("Listener stopped.")


if __name__ == "__main__":
    main()
