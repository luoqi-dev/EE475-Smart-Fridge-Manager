#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cleanup() {
    echo
    echo "Stopping all services..."
    kill "${EDGE_PID:-}" "${AI_PID:-}" "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
    wait || true
    echo "All services stopped."
}

trap cleanup SIGINT SIGTERM

mkdir -p "$REPO_ROOT/data/sessions"

echo "Clearing database tables..."
python3 "$REPO_ROOT/scripts/clear_collision_tables.py"
echo "Tables cleared."

echo "Starting Edge..."
python3 "$REPO_ROOT/src/edge/door_bridge.py" &
EDGE_PID=$!

echo "Starting Detection..."
python3 "$REPO_ROOT/src/detection/model/socket_listener.py" &
AI_PID=$!

echo "Starting Backend..."
python3 "$REPO_ROOT/src/data_detection_layer/detection_runner.py" &
BACKEND_PID=$!

echo "Starting Web..."
python3 "$REPO_ROOT/src/web/server.py" &
FRONTEND_PID=$!

echo "System started."
echo "Press Ctrl+C to stop all services."

wait
