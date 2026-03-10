#!/usr/bin/env bash
set -e


cleanup() {
    echo
    echo "Stopping all services..."
    kill "$EDGE_PID" "$AI_PID" "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    wait
    echo "All services stopped."
}

trap cleanup SIGINT SIGTERM

echo "Clearing database tables..."

python3 scripts/clear_collision_tables.py

echo "Tables cleared."

echo "Starting Edge..."
python ./src/bridge/door_bridge.py &
EDGE_PID=$!

echo "Starting AI..."
python ./src/detection/model/socket_listener.py &
AI_PID=$!

echo "Starting Backend..."
python ./src/data_detection_layer/detection_runner.py &
BACKEND_PID=$!

echo "Starting Frontend..."
python ./web/server.py &
FRONTEND_PID=$!

echo "System started."
echo "Press Ctrl+C to stop all services."

wait
