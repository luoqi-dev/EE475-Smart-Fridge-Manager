#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <SESSION_ID>"
  echo "Example: $0 test_codex"
  exit 2
fi

SESSION_ID="$1"
REPO_ROOT="$(pwd)"
SOCKET_PATH="/tmp/fridge_bus.sock"
DB_PATH="$REPO_ROOT/data/db/fridge.db"
VISION_PATH="$REPO_ROOT/data/sessions/session_${SESSION_ID}/vision.json"
LOG_DIR="$REPO_ROOT/scripts/logs"
LOG_PATH="$LOG_DIR/detection_runner_${SESSION_ID}.log"

mkdir -p "$LOG_DIR"

LISTENER_PID=""

cleanup() {
  set +e
  if [[ -n "${LISTENER_PID}" ]] && kill -0 "${LISTENER_PID}" 2>/dev/null; then
    kill "${LISTENER_PID}" 2>/dev/null || true
    wait "${LISTENER_PID}" 2>/dev/null || true
  fi
  if [[ -S "$SOCKET_PATH" ]]; then
    rm -f "$SOCKET_PATH"
  fi
}
trap cleanup EXIT

clear_events_table() {
  echo "[TEST] Clearing events table ..."
  python3 - <<'PY'
import sqlite3
from pathlib import Path
import sys

repo_root = Path.cwd()
sys.path.append(str(repo_root))
from src.data_detection_layer.vision_to_events import ensure_schema_v2

conn = sqlite3.connect(str(repo_root / 'data' / 'db' / 'fridge.db'))
try:
    conn.execute('PRAGMA journal_mode=WAL;')
    ensure_schema_v2(conn)
    conn.execute('DELETE FROM events;')
    conn.commit()
finally:
    conn.close()
PY
}

wait_for_listener_ready() {
  local timeout_sec=15
  local start_ts now elapsed
  start_ts="$(date +%s)"

  echo "[TEST] Waiting for listener readiness ..."
  while true; do
    if [[ -S "$SOCKET_PATH" ]] || grep -q "Listening on" "$LOG_PATH" 2>/dev/null; then
      echo "[TEST] Listener is ready."
      return 0
    fi

    if [[ -n "${LISTENER_PID}" ]] && ! kill -0 "${LISTENER_PID}" 2>/dev/null; then
      echo "[TEST][ERROR] DetectionRunner listener exited unexpectedly."
      tail -n 120 "$LOG_PATH" || true
      return 1
    fi

    now="$(date +%s)"
    elapsed=$((now - start_ts))
    if (( elapsed >= timeout_sec )); then
      echo "[TEST][ERROR] Timeout waiting for listener readiness (${timeout_sec}s)."
      tail -n 120 "$LOG_PATH" || true
      return 1
    fi
    sleep 0.2
  done
}

wait_for_session_completion() {
  local timeout_sec=15
  local start_ts now elapsed
  start_ts="$(date +%s)"

  echo "[TEST] Waiting for session processing completion ..."
  while true; do
    if grep -q "\[DetectionRunner\] Ready for next session_id\.\.\." "$LOG_PATH" 2>/dev/null; then
      echo "[TEST] Session processing complete."
      return 0
    fi

    if [[ -n "${LISTENER_PID}" ]] && ! kill -0 "${LISTENER_PID}" 2>/dev/null; then
      echo "[TEST][ERROR] DetectionRunner listener exited before completion marker."
      tail -n 200 "$LOG_PATH" || true
      return 1
    fi

    now="$(date +%s)"
    elapsed=$((now - start_ts))
    if (( elapsed >= timeout_sec )); then
      echo "[TEST][ERROR] Timeout waiting for completion marker (${timeout_sec}s)."
      tail -n 200 "$LOG_PATH" || true
      return 1
    fi

    sleep 0.2
  done
}

dump_events_table() {
  echo "[TEST] Dumping events table ..."
  python3 - <<'PY'
import sqlite3
from pathlib import Path

repo_root = Path.cwd()
db_path = repo_root / 'data' / 'db' / 'fridge.db'
conn = sqlite3.connect(str(db_path))
query = """
SELECT id, session_id, event_time_utc, item_name, status, confidence, track_id
FROM events
ORDER BY event_time_utc ASC, id ASC;
"""
try:
    try:
        import pandas as pd
        df = pd.read_sql_query(query, conn)
        print(df)
    except Exception:
        cur = conn.execute(query)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        print(cols)
        for row in rows:
            print(row)
finally:
    conn.close()
PY
}

echo "[TEST] =================================================="
echo "[TEST] SESSION_ID=$SESSION_ID"
echo "[TEST] Repo root: $REPO_ROOT"
echo "[TEST] Vision path: $VISION_PATH"
echo "[TEST] DB path: $DB_PATH"
echo "[TEST] Listener socket: $SOCKET_PATH"
echo "[TEST] Listener log: $LOG_PATH"
echo "[TEST] =================================================="

if [[ ! -f "$VISION_PATH" ]]; then
  echo "[TEST][ERROR] Missing vision file: $VISION_PATH"
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "[TEST][ERROR] Missing DB file: $DB_PATH"
  exit 1
fi

: > "$LOG_PATH"

clear_events_table

echo "[TEST] Starting DetectionRunner listener ..."
PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}" \
python3 -u - <<'PY' > "$LOG_PATH" 2>&1 &
from src.data_detection_layer.detection_runner import get_project_root, run_socket_listener
PROJECT_ROOT = get_project_root()
run_socket_listener(project_root=PROJECT_ROOT)
PY
LISTENER_PID="$!"

wait_for_listener_ready

echo "[TEST] Sending session_id=$SESSION_ID over socket ..."
python3 - <<PY
import json
import socket

socket_path = '/tmp/fridge_bus.sock'
payload = {'type': 'SESSION_CLOSED', 'session_id': '$SESSION_ID'}

client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.connect(socket_path)
client.sendall(json.dumps(payload).encode('utf-8'))
client.close()
PY

wait_for_session_completion

dump_events_table

if [[ -n "${LISTENER_PID}" ]] && kill -0 "${LISTENER_PID}" 2>/dev/null; then
  kill "${LISTENER_PID}" 2>/dev/null || true
  wait "${LISTENER_PID}" 2>/dev/null || true
fi
LISTENER_PID=""

clear_events_table

echo "[TEST] Done."
